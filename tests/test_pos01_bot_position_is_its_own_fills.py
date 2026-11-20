"""
tests/test_pos01_bot_position_is_its_own_fills.py

POS-01 and TIER-01, found during the live testnet run.

POS-01: a live bot's position was the account's WHOLE holding of the base coin. A Binance
testnet account comes funded with BTC, so a BTC/USDC bot started "holding" ~1 BTC: every buy,
even 0.0001 BTC, was refused as "over max exposure", and an exit signal would have closed
"the whole position" - selling coins the user owned and the bot never bought. The position
is now this deployment's own net filled quantity, capped at what the wallet holds; a
deployment with no id or unreadable fills starts flat.

TIER-01: ``FleetManager.start_bot`` resolved the user's plan for the bot-count limit only;
the bot's risk checks read ``blueprint["user_tier"]``, which nothing set, so every bot was
held to FREE-tier limits ("would exceed free tier max exposure of $5,000") whatever the
user paid for.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend_app.backend.master_executor import BotRunner


class _Q:
    def __init__(self, rows, fail=False):
        self.rows, self.fail, self.filters = rows, fail, []

    def select(self, *_a):
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def gt(self, col, val):
        self.filters.append(lambda r: float(r.get(col) or 0) > val)
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("db down")
        return SimpleNamespace(data=[r for r in self.rows if all(f(r) for f in self.filters)])


def _bot(deployment_id="dep-1"):
    bot = BotRunner.__new__(BotRunner)
    bot.user_id, bot.symbol = "u1", "BTC/USDC"
    bot.blueprint = {"deployment_id": deployment_id} if deployment_id else {}
    return bot


def _with_rows(monkeypatch, rows, fail=False):
    monkeypatch.setattr(
        "backend_app.core.dependencies.get_service_supabase",
        lambda: SimpleNamespace(table=lambda name: _Q(rows, fail)),
    )


def test_the_position_is_this_deployments_net_fills(monkeypatch):
    _with_rows(monkeypatch, [
        {"deployment_id": "dep-1", "decision": "BUY", "filled": 0.0003},
        {"deployment_id": "dep-1", "decision": "SELL", "filled": 0.0001},
        {"deployment_id": "dep-1", "decision": "BUY", "filled": 0},          # unfilled
        {"deployment_id": "other", "decision": "BUY", "filled": 5},          # another bot
    ])
    pos, source = asyncio.run(_bot()._own_position_from_fills())
    assert pos == Decimal("0.0002")
    assert source == "own_fills:2"


def test_a_new_deployment_starts_flat_whatever_the_wallet_holds(monkeypatch):
    _with_rows(monkeypatch, [])
    pos, _ = asyncio.run(_bot()._own_position_from_fills())
    assert pos == 0


@pytest.mark.parametrize("deployment_id, fail, source", [
    (None, False, "no_deployment_id_flat"),
    ("dep-1", True, "fills_unreadable_flat"),
])
def test_unknown_or_unreadable_starts_flat(monkeypatch, deployment_id, fail, source):
    _with_rows(monkeypatch, [{"deployment_id": "dep-1", "decision": "BUY", "filled": 1}], fail)
    pos, got = asyncio.run(_bot(deployment_id)._own_position_from_fills())
    assert pos == 0 and got == source


def test_the_live_boot_never_adopts_the_wallets_holdings(monkeypatch):
    """Through the real boot: a wallet holding 1 BTC and a deployment that bought 0.0002."""
    import backend_app.backend.connection_engine as ce
    import backend_app.backend.data_seeking_engine as de
    import backend_app.backend.exchange_executor as ex
    import backend_app.core.execution_engine as ee
    from tests.test_rt07_live_bot_requires_measured_balance import _Conn, _Vault

    class Data:
        def __init__(self, exchange):
            pass

        async def fetch_wallet_balance_snapshot(self):
            return {"USDC": {"total": 500.0}, "BTC": {"total": 1.0}}

        async def fetch_historical_ohlcv(self, *a, **k):
            return []

    monkeypatch.setattr(ce, "ConnectionEngine", _Conn)
    monkeypatch.setattr(de, "DataEngine", Data)
    monkeypatch.setattr(ex, "get_exchange_executor", lambda **k: object())
    monkeypatch.setattr(ee, "ExecutionEngine", lambda **kw: SimpleNamespace(**kw))
    _with_rows(monkeypatch, [{"deployment_id": "dep-9", "decision": "BUY", "filled": 0.0002}])

    gs = SimpleNamespace(vault=_Vault("k"), telemetry=SimpleNamespace(),
                         risk=SimpleNamespace(), alert=SimpleNamespace())
    bot = BotRunner("u1", "BTC/USDC",
                    {"exchange_id": "binance", "timeframe": "1m", "deployment_id": "dep-9"},
                    gs, paper_trading=False)
    try:
        asyncio.run(bot._recover_live_state())
    except Exception:
        pass  # later warm-up steps are not under test
    assert bot.recovered_exposure_coins == Decimal("0.0002")


def test_the_fleet_passes_the_users_plan_to_the_bot(monkeypatch):
    """TIER-01: the plan resolved in start_bot reaches the bot's blueprint."""
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "backend_app" / "backend" / "fleet_manager.py").read_text()
    tree = ast.parse(src)
    start_bot = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.AsyncFunctionDef) and n.name == "start_bot")
    body = ast.unparse(start_bot)
    assert "'user_tier': user_tier" in body, "start_bot must hand the resolved plan to the bot"
    # ... and BEFORE the BotRunner is constructed with that blueprint.
    assert body.index("'user_tier': user_tier") < body.index("BotRunner(")
