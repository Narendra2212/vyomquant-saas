"""RT-21 -- flattening a position, the part cancel/cancel-all (commit 2644850) did not cover.

THE DEFECT (audit, AMENDMENT)
    ``POST /api/portfolio/close-all`` ended in an unconditional 403
    (``DIRECT_EXECUTION_BLOCKED``): "Deploy a strategy with close_positions logic via the
    strategy DAG system" -- advice a user in an incident cannot act on. Unlike a resting
    order (visible and cancellable at the venue from any process), an open position's size
    is tracked ONLY in the memory of the bot that opened it
    (``BotRunner.recovered_exposure_coins``, settled solely from real fills -- see
    ``settle_exposure``). There is no second, externally readable copy to act on from an API
    handler, so a close cannot be placed from outside the owning process without inventing a
    second order-placing path next to ``execute_with_idempotency`` -- exactly what RT-15
    hardened this codebase against.

THE FIX
    The same durable, cross-process request RT-08 already uses for ``stop``:
    ``strategy_deployments.close_requested_at`` (migration 038), written by the API under
    the user's own JWT (allow-listed for that by the same migration), read and cleared by
    ``FleetManager``'s heartbeat (``_close_requested`` / ``_honour_close_request``), acted on
    by ``BotRunner.close_position()`` -- which calls the SAME
    ``execution_engine.execute_with_idempotency(source="bot_runner", ...)`` every regular
    order already goes through. No new source value, no new token, no second gateway: the
    only new thing is that an existing bot can now be TOLD to sell its own recorded
    exposure, not open a new one.

WHAT THIS FILE PROVES (fake Supabase double, fake engine/bot; no network, no real exchange)
    * ``DeploymentStateStore.request_close`` / ``close_requested`` / ``clear_close_request``;
    * ``BotRunner.close_position()``: no-ops honestly when there is nothing to close or no
      measured price yet; otherwise sells EXACTLY the recorded exposure, through the real
      gateway call shape, and updates exposure only from what the gateway reports filled;
    * ``FleetManager._honour_close_request`` clears the request before acting (so a slow
      close is not re-dispatched by the next heartbeat) and calls the bot;
    * the request never reaches ``execute_with_idempotency`` with ``source`` other than
      ``"bot_runner"`` -- this fix adds no new value the gateway's STEP 6 check has to trust.

NOT SHOWN: a real exchange fill (needs sandbox credentials); the in-memory engine's own
paper-fill arithmetic (covered by ``tests/test_execution_engine_*``); the API route's HTTP
layer end-to-end (covered by a FastAPI TestClient elsewhere in this suite's convention --
this file is the mechanism beneath it).
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend_app.backend.deployment_state import DeploymentStateStore
from backend_app.backend.fleet_manager import FleetManager


# ── the same in-memory PostgREST-shaped double RT-08's own suite uses ───────


class _Query:
    def __init__(self, rows, op, payload=None):
        self._rows, self._op, self._payload = rows, op, payload
        self._filters = []

    def select(self, *_a, **_k):
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def eq(self, col, val):
        self._filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        self._filters.append(lambda r: r.get(col) in vals)
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        hit = [r for r in self._rows if all(f(r) for f in self._filters)]
        if self._op == "update":
            for r in hit:
                r.update(self._payload)
        return SimpleNamespace(data=[dict(r) for r in hit])


class FakeSB:
    def __init__(self, deployments):
        self.deployments = deployments

    def table(self, name):
        assert name == "strategy_deployments", name
        return _Query(self.deployments, "select")


def _row(id_="d1", status="running", **kw):
    return {"id": id_, "user_id": "u1", "strategy_id": "s1", "version_id": "v1",
            "exchange_symbol": "ETH/USDT", "exchange_id": "binance", "mode": "paper",
            "environment": "paper", "status": status, **kw}


# ── the durable request/read/clear cycle ─────────────────────────────────────


@pytest.mark.asyncio
class TestStoreCloseRequest:
    async def test_request_requires_a_live_row(self):
        sb = FakeSB([_row("a", "running"), _row("b", "stopped")])
        store = DeploymentStateStore(lambda: sb)
        assert await store.request_close("a") is True
        assert await store.request_close("b") is False
        assert await store.close_requested("a") is True
        assert await store.close_requested("b") is False

    async def test_request_is_honoured_while_stopping_too(self):
        # A user may ask to flatten a deployment that is already mid-stop (its last tick
        # has not yet confirmed `stopped`): the bot is still owned and still reachable.
        sb = FakeSB([_row("a", "stopping")])
        store = DeploymentStateStore(lambda: sb)
        assert await store.request_close("a") is True

    async def test_clear_removes_the_request_regardless_of_status(self):
        sb = FakeSB([_row("a", "running", close_requested_at="2026-01-01T00:00:00Z")])
        store = DeploymentStateStore(lambda: sb)
        await store.clear_close_request("a")
        assert sb.deployments[0]["close_requested_at"] is None

    async def test_an_unreadable_row_is_not_requested(self):
        def boom():
            raise RuntimeError("db down")
        assert await DeploymentStateStore(boom).close_requested("a") is False


# ── BotRunner.close_position() ───────────────────────────────────────────────


def _bot_with_engine(exposure: Decimal, *, last_price=None, paper=True):
    """A BotRunner stand-in carrying only what ``close_position`` reads."""
    from backend_app.backend.master_executor import BotRunner

    bot = BotRunner.__new__(BotRunner)  # skip __init__: no live market connection needed
    bot.user_id = "11111111-1111-4111-8111-111111111111"
    bot.symbol = "ETH/USDT"
    bot.blueprint = {"strategy_id": "s1"}
    bot.paper_trading = paper
    bot.paused = False
    bot.recovered_exposure_coins = exposure
    bot._last_price = last_price
    bot.execution_engine = MagicMock()
    bot.execution_engine.execute_with_idempotency = AsyncMock(
        return_value={"status": "completed", "result": {"trade_result": {"size": str(exposure)}}}
    )
    return bot


@pytest.mark.asyncio
class TestClosePosition:
    async def test_no_position_is_a_true_no_op(self):
        bot = _bot_with_engine(Decimal("0"), last_price=2000.0)
        ok, detail = await bot.close_position()
        assert ok is True and "no open position" in detail
        bot.execution_engine.execute_with_idempotency.assert_not_awaited()

    async def test_refuses_without_a_measured_price(self):
        bot = _bot_with_engine(Decimal("1.5"), last_price=None)
        ok, detail = await bot.close_position()
        assert ok is False and "price" in detail
        bot.execution_engine.execute_with_idempotency.assert_not_awaited()

    async def test_sells_exactly_the_recorded_exposure_through_the_real_gateway(self):
        bot = _bot_with_engine(Decimal("1.5"), last_price=2000.0)
        ok, detail = await bot.close_position()
        assert ok is True
        call = bot.execution_engine.execute_with_idempotency.await_args.kwargs
        assert call["side"] == "sell"
        assert call["size"] == Decimal("1.5"), "must sell exactly what is held, never more"
        assert call["source"] == "bot_runner", (
            "RT-15: no new source value is introduced; this must still look, to the "
            "gateway, exactly like every other order BotRunner places"
        )
        assert call["risk_approved"] is True
        assert bot.recovered_exposure_coins == Decimal("0")

    async def test_live_mode_states_live_not_paper(self):
        from backend_app.backend.execution_environment import ExecutionEnvironment
        bot = _bot_with_engine(Decimal("2"), last_price=100.0, paper=False)
        await bot.close_position()
        call = bot.execution_engine.execute_with_idempotency.await_args.kwargs
        assert call["execution_environment"] == ExecutionEnvironment.LIVE

    async def test_a_result_that_does_not_fill_leaves_exposure_unchanged(self):
        bot = _bot_with_engine(Decimal("1.5"), last_price=2000.0)
        bot.execution_engine.execute_with_idempotency = AsyncMock(
            return_value={"status": "blocked", "message": "kill switch active"}
        )
        ok, detail = await bot.close_position()
        assert ok is False
        assert bot.recovered_exposure_coins == Decimal("1.5"), (
            "a blocked/failed close must not be recorded as a fill"
        )

    async def test_restores_the_prior_pause_state_even_on_failure(self):
        bot = _bot_with_engine(Decimal("1"), last_price=10.0)
        bot.paused = False
        bot.execution_engine.execute_with_idempotency = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            await bot.close_position()
        assert bot.paused is False, "pausing for the close must not leak into a permanent pause"


# ── fleet: the request reaches the owning bot and is cleared first ──────────


class _Store:
    def __init__(self):
        self.cleared = []
        self._close_due = {}

    async def close_requested(self, dep):
        return self._close_due.get(dep, False)

    async def clear_close_request(self, dep):
        self.cleared.append(dep)
        self._close_due[dep] = False


def _bot(dep="d1"):
    return SimpleNamespace(
        blueprint={"deployment_id": dep}, user_id="u1", symbol="ETH/USDT",
        close_position=AsyncMock(return_value=(True, "closed 1.5 ETH/USDT")),
    )


@pytest.mark.asyncio
class TestFleetHonoursCloseRequest:
    async def test_close_requested_reads_the_store_for_this_bots_deployment(self):
        store = _Store()
        store._close_due["d1"] = True
        fleet = FleetManager(state_store=store)
        fleet._active_fleet["k"] = {"bot": _bot("d1"), "symbol": "ETH/USDT"}
        assert await fleet._close_requested("k") is True

    async def test_honour_clears_before_calling_close_position(self):
        store = _Store()
        fleet = FleetManager(state_store=store)
        bot = _bot("d1")
        fleet._active_fleet["k"] = {"bot": bot, "symbol": "ETH/USDT"}

        order = []
        store.clear_close_request = AsyncMock(side_effect=lambda d: order.append("cleared"))
        bot.close_position = AsyncMock(side_effect=lambda: order.append("closed") or (True, "ok"))

        await fleet._honour_close_request("k")
        assert order == ["cleared", "closed"], (
            "the request must be cleared BEFORE close_position runs, so a slow close is "
            "not re-dispatched by the next heartbeat"
        )
        bot.close_position.assert_awaited_once()

    async def test_honour_is_a_no_op_for_an_unknown_bot(self):
        store = _Store()
        fleet = FleetManager(state_store=store)
        await fleet._honour_close_request("missing")  # must not raise
