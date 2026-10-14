"""RT-01 / RT-02 / RT-05 -- a strategy saved by the current builder runs on a deployment.

THE DEFECTS (audit)
    RT-02  ``deploy_version`` handed ``strategy_versions.blueprint`` (a serialised StrategyGraph:
           nodes and edges, by design no venue) to ``fleet.start_bot``. ``BotRunner`` refused it
           ("exchange_id must be provided"), and even with a venue the LEGACY evaluator needs
           ``buy_logic``/``sell_logic``/``risk`` which a graph does not have.
    RT-05  the canonical evaluator (``DAGEngine.execute_plan``) had no caller on the deployment
           path.
    RT-01  a paper bot's exchange object was ``None`` (the mock "injection" is a no-op before
           ``connect()``), so its loop died on the first read; the fleet logged "exited
           cleanly" and the row still said running. Even with a client the DEV_MODE mock streams
           a constant 50,000.

WHAT THIS FILE PROVES (offline: recording doubles, no network, no credential, no real order)
    * the runtime blueprint carries the binding and starts a ``BotRunner`` that the raw graph
      cannot; an unstartable version is refused with a 422 BEFORE any row is written;
    * the evaluator runs the real compiled plan over closed bars and produces a real order
      payload only where the plan fires and execution is permitted (negative control: safe mode);
    * sizing rules resolve against measured state or yield NO order; stop/limit orders are
      refused rather than executed as market orders; a spot sell with no position is refused;
    * a paper ``BotRunner`` boots on injected public market data, evaluates a closed bar and
      submits an order through ``execute_with_idempotency`` -- and does not touch the DEV_MODE
      mock or the credential vault.

WHAT IT DOES NOT SHOW: behaviour against a real exchange's public feed (24h staging soak, the
audit's acceptance criterion) -- that is EXTERNAL and stays unverified.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend_app.backend import deployment_runtime as dr
from backend_app.backend import strategy_compiler as SC
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.core.safety_config import ExecutionFlags
from tests.fakes_market_data import (FakePublicExchange, make_closed_ohlcv,
                                     public_factory)
from tests.test_plan_engine_adaptation_fidelity import gate_graph

USER_ID = "11111111-1111-4111-8111-111111111111"


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def reg():
    return registry_module.build_registry()


@pytest.fixture(scope="module")
def version_row(reg):
    """A ``strategy_versions`` row exactly as the builder persists it."""
    graph, _ids = gate_graph(reg)
    plan = SC.compile_graph(graph, reg)
    return {
        "id": "ver-1", "version": "1.0",
        "blueprint": graph.to_dict(),
        "compiled_plan": plan.to_json(),
    }


@pytest.fixture
def execution_permitted():
    """Permit strategy-signal execution for one test, then restore every flag.

    ``SafetyMonitor`` is not patched or weakened: this flips the platform's own paper switch and
    restores the environment and every flag afterwards (same approach as
    tests/test_plan_engine_adaptation_fidelity.py).
    """
    names = ("LIVE_TRADING_ENABLED", "PAPER_TRADING_ENABLED", "MANUAL_ORDER_EXECUTION",
             "STRATEGY_SIGNAL_EXECUTION", "BACKTEST_CAN_SUBMIT_ORDERS", "ALLOW_ML_INFERENCE")
    previous_mode = os.environ.get("VYOMQUANT_MODE")
    previous = {n: getattr(ExecutionFlags, n) for n in names}
    os.environ["VYOMQUANT_MODE"] = "paper"
    try:
        ExecutionFlags.enable_paper_trading()
        yield
    finally:
        for n, v in previous.items():
            setattr(ExecutionFlags, n, v)
        if previous_mode is None:
            os.environ.pop("VYOMQUANT_MODE", None)
        else:
            os.environ["VYOMQUANT_MODE"] = previous_mode


def _blueprint(row, **over):
    kw = dict(deployment_id="dep-1", strategy_id="strat-1", symbol="ETH/USDT",
              timeframe="5m", exchange_id="binance", mode="paper")
    kw.update(over)
    return dr.build_runtime_blueprint(row, **kw)


def _bars(closes, start=1_700_000_000, step=300):
    return [
        dr.ClosedBar.from_candle({"timestamp": start + i * step, "open": c, "high": c + .5,
                                  "low": c - .5, "close": c, "volume": 10})
        for i, c in enumerate(closes)
    ]


def _rising_then_falling():
    closes, price = [], 100.0
    for i in range(90):
        price += 1.5 if i < 30 else (-1.25 if i < 60 else 0.75)
        closes.append(round(price, 4))
    return closes


# ── 1. the blueprint ─────────────────────────────────────────────────────────


class TestRuntimeBlueprint:
    def test_the_raw_builder_graph_cannot_start_a_bot_but_the_runtime_blueprint_can(self, version_row):
        from backend_app.backend.master_executor import BotRunner

        state = SimpleNamespace(vault=None, telemetry=None, risk=None, alert=None)
        # Negative control: this is the defect.
        with pytest.raises(ValueError, match="exchange_id must be provided"):
            BotRunner(USER_ID, "ETH/USDT", dict(version_row["blueprint"]),
                      state, paper_trading=True)
        bot = BotRunner(USER_ID, "ETH/USDT", _blueprint(version_row), state, paper_trading=True)
        assert bot.graph_eval is not None, "a graph blueprint must run on the DAG evaluator"
        assert bot.exchange_id == "binance"

    def test_binding_fields_are_injected_and_nothing_is_dropped(self, version_row):
        bp = _blueprint(version_row)
        assert bp["runtime"] == dr.RUNTIME_DAG
        assert (bp["exchange_id"], bp["symbol"], bp["timeframe"]) == ("binance", "ETH/USDT", "5m")
        assert bp["deployment_id"] == "dep-1" and bp["version_id"] == "ver-1"
        assert bp["nodes"] == version_row["blueprint"]["nodes"]
        assert bp["compiled_plan"] == version_row["compiled_plan"], "the stored plan travels (R22.3)"
        # the stored version row is not mutated
        assert "runtime" not in version_row["blueprint"]

    @pytest.mark.parametrize("bad", [
        {"symbol": None}, {"timeframe": ""}, {"symbol": "  "},
    ])
    def test_no_market_means_no_blueprint(self, version_row, bad):
        with pytest.raises(dr.RuntimeBlueprintError, match="symbol/timeframe"):
            _blueprint(version_row, **bad)

    @pytest.mark.parametrize("row", [
        None, {}, {"blueprint": {"nodes": [], "edges": []}}, {"blueprint": {"nodes": [{}]}},
    ])
    def test_a_version_with_no_graph_is_refused(self, row):
        with pytest.raises(dr.RuntimeBlueprintError):
            _blueprint(row)

    def test_paper_falls_back_to_the_configured_feed_but_never_invents_one(self, version_row, monkeypatch):
        monkeypatch.delenv("DEFAULT_EXCHANGE", raising=False)
        with pytest.raises(dr.RuntimeBlueprintError, match="DEFAULT_EXCHANGE"):
            _blueprint(version_row, exchange_id=None)
        monkeypatch.setenv("DEFAULT_EXCHANGE", "kraken")
        assert _blueprint(version_row, exchange_id=None)["exchange_id"] == "kraken"

    def test_live_never_falls_back_to_the_default_feed(self, version_row, monkeypatch):
        monkeypatch.setenv("DEFAULT_EXCHANGE", "kraken")
        with pytest.raises(dr.RuntimeBlueprintError):
            _blueprint(version_row, exchange_id=None, mode="live")

    def test_an_uncompilable_graph_fails_the_start_not_the_first_tick(self, version_row):
        bp = _blueprint(version_row)
        bp.pop("compiled_plan")
        bp["nodes"] = [{"id": "x", "block_id": "does.not.exist", "category": "data", "params": {}}]
        bp["edges"] = []
        with pytest.raises(Exception):
            dr.GraphSignalEvaluator(bp)


# ── 2. sizing ────────────────────────────────────────────────────────────────


class TestResolveOrderQuantity:
    ctx = dict(price="100", equity="10000", free_quote="4000", position_base="2")

    @pytest.mark.parametrize("sizing,expected", [
        ({"quantity_type": "base_amount", "quantity": "0.5"}, "0.5"),
        ({"quantity_type": "percent_of_equity", "quantity": 10}, "10"),          # 1000/100
        ({"quantity_type": "percent_of_free_balance", "quantity": 25}, "10"),    # 1000/100
        ({"quantity_type": "quote_notional", "quantity": 250}, "2.5"),
        ({"quantity_type": "fixed_notional", "quantity": 50}, "0.5"),
        ({"quantity_type": "percent_of_position", "quantity": 50}, "1"),
    ])
    def test_each_rule_resolves_against_measured_state(self, sizing, expected):
        assert dr.resolve_order_quantity(sizing, **self.ctx) == Decimal(expected)

    @pytest.mark.parametrize("sizing,ctx", [
        ({"quantity_type": "percent_of_equity", "quantity": 10}, dict(equity=None)),
        ({"quantity_type": "percent_of_equity", "quantity": 10}, dict(equity="0")),
        ({"quantity_type": "percent_of_free_balance", "quantity": 10}, dict(free_quote="-1")),
        ({"quantity_type": "percent_of_position", "quantity": 50}, dict(position_base="0")),
        ({"quantity_type": "quote_notional", "quantity": 50}, dict(price="0")),
        ({"quantity_type": "quote_notional", "quantity": 50}, dict(price="nan")),
        ({"quantity_type": "mystery_rule", "quantity": 1}, {}),
        ({"quantity_type": "base_amount", "quantity": 0}, {}),
        ({"quantity_type": "base_amount"}, {}),
        ({}, {}),
    ])
    def test_unresolvable_state_yields_no_order_not_a_default(self, sizing, ctx):
        assert dr.resolve_order_quantity(sizing, **{**self.ctx, **ctx}) is None


# ── 3. the evaluator over the real compiled plan ─────────────────────────────


class TestGraphSignalEvaluator:
    def _eval(self, version_row, **over):
        return dr.GraphSignalEvaluator(_blueprint(version_row, **over))

    def _run(self, ev, closes, position=0):
        fired = []
        for bar in _bars(closes):
            out = ev.on_closed_bars([bar], price=bar.close, equity=10_000,
                                    free_quote=10_000, position_base=position)
            if out:
                fired.append((bar.event_timestamp, out))
        return fired

    def test_the_plan_fires_real_buy_orders_where_its_gate_is_true(self, version_row, execution_permitted):
        fired = self._run(self._eval(version_row), _rising_then_falling())
        assert fired, "positive control: the strategy must trade on this series"
        order = fired[0][1]
        assert order["side"] == "buy" and order["source"] == "dag"
        assert order["symbol"] == "ETH/USDT" and order["order_type"] == "market"
        assert order["qty"] > 0 and order["reduce_only"] is False
        assert order["signal_id"], "the signal is traceable to the bar that produced it"

    def test_safe_mode_blocks_the_order_path_and_the_evaluator_does_not_override_it(self, version_row):
        """Negative control. ``tests/conftest.py`` runs in safe mode; nothing here relaxes it."""
        assert self._run(self._eval(version_row), _rising_then_falling()) == []

    def test_history_is_warmed_without_replaying_old_signals(self, version_row, execution_permitted):
        ev = self._eval(version_row)
        admitted = ev.warm_up(_bars(_rising_then_falling()))
        assert admitted == 90
        # the next live bar continues the window (indicators already warm) ...
        nxt = dr.ClosedBar.from_candle({"timestamp": 1_700_000_000 + 90 * 300, "open": 160,
                                        "high": 161, "low": 159, "close": 160, "volume": 10})
        out = ev.on_closed_bars([nxt], price=160, equity=10_000, free_quote=10_000, position_base=0)
        assert out and out["side"] == "buy"
        # ... and a repeat of it is refused by the window, so it cannot double-fire
        assert ev.on_closed_bars([nxt], price=160, equity=10_000, free_quote=10_000, position_base=0) is None

    def test_only_the_newest_of_several_closed_bars_is_acted_on(self, version_row, execution_permitted):
        ev = self._eval(version_row)
        bars = _bars(_rising_then_falling())
        ev.warm_up(bars[:60])
        out = ev.on_closed_bars(bars[60:], price=bars[-1].close, equity=10_000,
                                free_quote=10_000, position_base=0)
        assert out is not None
        assert ev._runtime.bars_admitted == 90, "every bar reached the window"

    # -- signal -> order (the guard rails), driven through the real converter ----------

    def _order(self, version_row, signal, *, position=0, **ctx):
        ev = self._eval(version_row)
        return ev, ev._to_order(signal, price=ctx.get("price", 100), equity=ctx.get("equity", 10_000),
                                free_quote=ctx.get("free_quote", 10_000), position_base=position)

    def test_stop_and_limit_orders_are_refused_not_executed_as_market(self, version_row):
        for order_type in ("limit", "stop_market", "stop_limit", "take_profit_market"):
            ev, out = self._order(version_row, {"decision": "BUY", "quantity": "1",
                                                "order_type": order_type})
            assert out is None
            assert ev.refusals == {"ORDER_TYPE_NOT_EXECUTABLE": 1}

    def test_a_spot_sell_with_no_position_is_refused_there_is_no_short(self, version_row):
        ev, out = self._order(version_row, {"decision": "SELL", "quantity": "1"}, position=0)
        assert out is None and ev.refusals == {"SELL_WITHOUT_POSITION": 1}

    def test_a_sell_is_capped_at_the_position_and_marked_reduce_only(self, version_row):
        _, out = self._order(version_row, {"decision": "SELL", "quantity": "5"}, position=2)
        assert out["side"] == "sell" and out["qty"] == 2.0 and out["reduce_only"] is True

    def test_a_close_sells_the_whole_position_reduce_only(self, version_row):
        _, out = self._order(version_row, {"decision": "CLOSE"}, position=1.5)
        assert (out["side"], out["qty"], out["reduce_only"]) == ("sell", 1.5, True)

    def test_a_close_with_nothing_open_is_not_an_order(self, version_row):
        ev, out = self._order(version_row, {"decision": "CLOSE"}, position=0)
        assert out is None and ev.refusals == {}

    def test_an_unsizeable_signal_makes_no_order(self, version_row):
        ev, out = self._order(version_row, {"decision": "BUY",
                                            "sizing_intention": {"quantity_type": "percent_of_equity",
                                                                 "quantity": 10}}, equity=None)
        assert out is None and ev.refusals == {"SIZE_UNRESOLVABLE": 1}

    def test_a_percent_of_equity_signal_is_sized_from_measured_equity(self, version_row):
        _, out = self._order(version_row, {"decision": "BUY",
                                           "sizing_intention": {"quantity_type": "percent_of_equity",
                                                                "quantity": 10}},
                             equity=10_000, price=100)
        assert out["qty"] == 10.0

    def test_hold_and_unknown_decisions_state_no_order(self, version_row):
        for decision in ("HOLD", "", "FLATTEN?"):
            _, out = self._order(version_row, {"decision": decision, "quantity": "1"})
            assert out is None


# ── 4. a paper BotRunner end to end, on injected public market data ───────────


class _Redis:
    async def set(self, *a, **k):
        return True

    async def delete(self, *a, **k):
        return 1


class _Risk:
    async def validate_trade_request(self, req):
        from backend_app.core.risk_manager import RiskVerdict
        self.last = req
        return RiskVerdict.APPROVED, "ok"


class _Telemetry:
    async def get_account_health(self, user_id):
        return {"current_drawdown_pct": 0.0, "daily_pnl_pct": 0.0}

    async def log_execution(self, **k):
        return None


class _ExplodingVault:
    def __init__(self):
        self.calls = []

    def load_decrypted_keys(self, *a):
        self.calls.append(a)
        raise AssertionError("a paper bot must never read credentials")


class _RecordingEngine:
    cash = Decimal("10000")
    current_equity = Decimal("10000")

    def __init__(self):
        self.orders = []

    async def execute_with_idempotency(self, **kw):
        self.orders.append(kw)
        return {"status": "completed",
                "result": {"filled_qty": float(kw["size"]), "price": float(kw["price"])}}


def _boot_paper_bot(version_row, trades, *, timeframe_ms=300_000):
    from backend_app.backend.master_executor import BotRunner

    ohlcv = make_closed_ohlcv(90, "5m")
    exchange = FakePublicExchange(ohlcv, trades=trades)
    vault = _ExplodingVault()
    state = SimpleNamespace(vault=vault, telemetry=_Telemetry(), risk=_Risk(), alert=MagicMock())
    bot = BotRunner(USER_ID, "ETH/USDT", _blueprint(version_row), state, paper_trading=True,
                    public_market_data_factory=public_factory(exchange))
    return bot, exchange, vault, ohlcv


def test_a_paper_bot_boots_on_public_data_trades_a_closed_bar_and_reads_no_credentials(
    version_row, execution_permitted, monkeypatch
):
    bar_ms = 300_000
    # Closed history ends one bar ago; two ticks: one inside the forming bar, one that closes it.
    probe = make_closed_ohlcv(90, "5m")
    forming_open = probe[-1][0] + bar_ms
    ticks = [
        {"timestamp": forming_open + 1_000, "price": 165.0, "amount": 1.0},
        {"timestamp": forming_open + bar_ms + 1_000, "price": 166.0, "amount": 1.0},
    ]
    bot, exchange, vault, _ = _boot_paper_bot(version_row, ticks)

    from backend_app.core.cache.redis_manager import redis_manager
    monkeypatch.setattr(redis_manager, "get_client", AsyncMock(return_value=_Redis()))
    monkeypatch.setattr("backend_app.backend.signal_service.get_signal_service",
                        AsyncMock(side_effect=RuntimeError("no db in this test")))

    async def scenario():
        await bot._recover_live_state()
        assert bot.graph_eval is not None
        assert bot.ccxt_bridge.api_key is None, "paper market data is keyless"
        engine = _RecordingEngine()
        bot.execution_engine = engine
        try:
            await asyncio.wait_for(bot._inner_loop(), timeout=5)
        except asyncio.TimeoutError:
            pass    # the fake feed is out of trades: a quiet market, not an error
        return engine

    engine = asyncio.run(scenario())
    # (the scenario returns the engine; the bot's exposure is read below via the closure)
    assert bot.recovered_exposure_coins > 0, (
        "a completed buy must leave the bot holding the position; it used to be rolled back by "
        "a call to a method that does not exist"
    )

    assert engine.orders, "the closed bar must have produced an order through the engine"
    order = engine.orders[0]
    assert order["side"] == "buy" and order["symbol"] == "ETH/USDT"
    assert order["source"] == "bot_runner"
    assert order["execution_environment"].name == "PAPER"
    assert order["risk_approved"] is True
    assert vault.calls == [], "credentials were consulted"
    assert exchange.balance_calls == 0, "a keyless paper bot must not try to read a wallet"


def test_the_forming_candle_is_not_loaded_as_history(version_row):
    """Warm-up drops a bar whose period has not ended (its close is not known yet)."""
    import time
    from backend_app.backend.master_executor import BotRunner

    now_ms = int(time.time() * 1000)
    closed = make_closed_ohlcv(40, "5m")
    forming = [(now_ms // 300_000) * 300_000, 1, 2, 0.5, 1.5, 3.0]
    exchange = FakePublicExchange(closed + [forming])
    state = SimpleNamespace(vault=_ExplodingVault(), telemetry=_Telemetry(), risk=_Risk(), alert=MagicMock())
    bot = BotRunner(USER_ID, "ETH/USDT", _blueprint(version_row), state, paper_trading=True,
                    public_market_data_factory=public_factory(exchange))

    async def boot():
        await bot._recover_live_state()

    asyncio.run(boot())
    assert bot.graph_eval._runtime.bars_admitted == 40


# ── 5. RT-01: no DEV_MODE mock behind a deployed bot ─────────────────────────


class _FailingExchange:
    id = "binance"
    symbols = None

    def __init__(self, config=None):
        pass

    async def load_markets(self):
        raise RuntimeError("venue unreachable")

    async def close(self):
        return None


def test_connect_without_mock_raises_in_dev_mode_and_default_keeps_the_dev_mock(monkeypatch):
    import backend_app.backend.connection_engine as ce
    import backend_app.core.dependencies as deps

    monkeypatch.setattr(deps, "DEV_MODE", True)
    monkeypatch.setattr(ce.ccxt, "binance", _FailingExchange, raising=False)
    monkeypatch.setattr(ce.asyncio, "sleep", AsyncMock())

    async def go(allow_mock):
        engine = ce.ConnectionEngine("binance", api_key=None, secret_key=None)
        return await engine.connect(allow_mock=allow_mock)

    with pytest.raises(ConnectionError):
        asyncio.run(go(False))                       # a deployed bot gets the real error
    exchange = asyncio.run(go(True))                 # unchanged default for dev tooling
    assert exchange is not None


def test_a_paper_boot_that_cannot_reach_the_venue_fails_the_start(version_row, monkeypatch):
    import backend_app.backend.connection_engine as ce
    import backend_app.core.dependencies as deps
    from backend_app.backend.master_executor import BotRunner

    monkeypatch.setattr(deps, "DEV_MODE", True)
    monkeypatch.setattr(ce.ccxt, "binance", _FailingExchange, raising=False)
    monkeypatch.setattr(ce.asyncio, "sleep", AsyncMock())
    state = SimpleNamespace(vault=_ExplodingVault(), telemetry=_Telemetry(), risk=_Risk(), alert=MagicMock())
    bot = BotRunner(USER_ID, "ETH/USDT", _blueprint(version_row), state, paper_trading=True)

    with pytest.raises(ConnectionError):
        asyncio.run(bot._recover_live_state())
    assert bot.data_engine is None, "no mock exchange was handed to the loop"


def test_a_data_stream_that_ends_is_an_error_not_a_clean_exit(version_row):
    """The venue offers no watchTrades -> `stream_live_ticks` returns. That must not read as a
    clean exit (the deployment row would keep saying running)."""
    bot, *_ = _boot_paper_bot(version_row, [])
    bot_exchange = FakePublicExchange(make_closed_ohlcv(5, "5m"), watch_trades=False)
    bot._public_market_data_factory = public_factory(bot_exchange)

    async def go():
        await bot._recover_live_state()
        with pytest.raises(RuntimeError, match="ended unexpectedly"):
            await bot._inner_loop()

    asyncio.run(go())


def test_a_shutdown_cancel_stays_a_cancel(version_row):
    """`stream_live_ticks` used to swallow CancelledError (break), so cancelling a bot looked like
    the feed ending. With 'feed ended' now an error, that would turn every shutdown into a
    crash-and-reconnect; the cancellation must propagate as itself."""
    bot, *_ = _boot_paper_bot(version_row, [])

    async def go():
        await bot._recover_live_state()
        task = asyncio.create_task(bot._inner_loop())
        await asyncio.sleep(0.2)          # parked in watch_trades on the quiet fake feed
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())


def test_giving_up_records_why(version_row, monkeypatch):
    import backend_app.backend.master_executor as me

    monkeypatch.setattr(me, "MAX_RECONNECT_ATTEMPTS", 2)
    monkeypatch.setattr(me.asyncio, "sleep", AsyncMock())
    bot, *_ = _boot_paper_bot(version_row, [])
    bot.alert = SimpleNamespace(send_critical=AsyncMock())
    bot._inner_loop = AsyncMock(side_effect=RuntimeError("feed down"))
    bot._recover_live_state = AsyncMock()

    asyncio.run(bot._run_trading_loop())

    assert bot.exit_reason and "gave up after 2" in bot.exit_reason and "feed down" in bot.exit_reason


# ── 6. deploy_version: refuse before writing; start with the runtime blueprint ─


from tests.test_task_8_2_deployment_binding import (  # noqa: E402
    _Supabase, _default_rows, _deploy, _version)
from tests.test_task_8_2_deployment_binding import \
    _clean_module_state as _clean_module_state  # noqa: E402,F401  (autouse fixture)
from tests.test_task_8_2_deployment_binding import \
    seeded_universe  # noqa: E402,F401


@pytest.mark.asyncio
async def test_a_graph_version_is_started_with_the_runtime_blueprint(version_row, seeded_universe):
    rows = _default_rows()
    rows["strategy_versions"] = [_version(blueprint=dict(version_row["blueprint"]))]
    sb = _Supabase(rows)

    result, fleet = await _deploy(sb)

    assert result["success"] is True
    (call,) = fleet.start_bot.await_args_list
    blueprint = call.kwargs["blueprint"]
    assert blueprint["runtime"] == dr.RUNTIME_DAG
    assert blueprint["deployment_id"] == result["deployment"]["id"]
    assert blueprint["symbol"] == call.kwargs["symbol"]
    assert blueprint["exchange_id"], "the runtime was given a venue"
    assert call.kwargs["paper_trading"] is True


@pytest.mark.asyncio
async def test_an_unstartable_version_is_refused_with_422_before_any_row_exists(seeded_universe):
    from backend_app.backend.deployment_binding import DeployRejected

    rows = _default_rows()
    rows["strategy_versions"] = [_version(blueprint={"nodes": [], "edges": []})]
    sb = _Supabase(rows)

    with pytest.raises(DeployRejected) as caught:
        await _deploy(sb)

    assert caught.value.code == "RUNTIME_BLUEPRINT_NOT_STARTABLE"
    assert caught.value.http_status == 422
    assert sb.inserted("strategy_deployments") == [], "no deployment row may exist"
