"""
tests/test_backtest_risk_configuration_provenance.py

The backtester simulates different per-trade risk than the live runtime, and the response
calls its figures "the configuration that was actually applied".

P1-07. Two halves, both measured against the real ``BacktestRuntime.run_backtest`` body.

HALF ONE -- THE PER-TRADE RISK IS HARDCODED
``backtest_runtime.run_backtest`` calls the simulator with literals::

    params={
        "timeframe": timeframe,
        "trade_size_pct": 0.10,
        "ml_threshold": 0.80,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.15,
    }

Those three risk numbers govern the simulation: ``trade_size_pct`` becomes
``size=..., size_type="percent"`` on ``vbt.Portfolio.from_signals``, and the other two
become ``sl_stop`` and ``tp_stop``. They are the same three quantities the LIVE path
reads off the strategy, in ``strategy_builder.StrategyEngine.run_logic``::

    risk_cfg = strategy_blueprint.get("risk") or {}
    if "position_size_pct" not in risk_cfg:
        logger.error("Blueprint missing risk.position_size_pct. Skipping tick.")
        return None
    ...
    "params": {
        "stop_loss_pct": risk_cfg.get("stop_loss_pct"),
        "take_profit_pct": risk_cfg.get("take_profit_pct"),
    },

So a strategy configured to risk 25% per trade with a 3% stop is backtested at 10% with a
5% stop, and the backtest's track record describes a strategy that will not be the one
trading. For a backtester whose stated purpose is to run "the same pipeline the live path
uses", that is the defect, not a tuning detail.

WHERE THE RISK CONFIG IS, AND IS NOT. It is **not** on the version row. ``strategy_versions``
has no risk column, and its ``blueprint`` holds a serialized ``StrategyGraph`` --
``CompiledVersion.canonical_columns`` asserts its key set equals exactly the ten
``CANONICAL_VERSION_COLUMNS``, none of which is risk-bearing. It **is** on the strategy
row, as the ``strategies.risk`` JSONB column (migration 020,
``DEFAULT '{}'::jsonb``), and the backtest route already holds it:
``StrategyService.get_strategy`` selects ``*``, so ``owned["strategy"]["risk"]`` is in
hand in the handler. The handler forwards only ``version_row`` and never looks at it.

WHY A MISSING CONFIG MUST NOT REFUSE. The live path refuses the tick when
``position_size_pct`` is absent. A backtest cannot copy that: the column defaults to
``'{}'`` and is unvalidated free-form JSON written straight from the client dict at three
sites, so refusing would turn backtesting off for every strategy that has never set a
risk block. Documented defaults are applied instead -- the same 0.10/0.05/0.15, so no
existing result moves -- and the response states which values came from the strategy and
which were defaulted. A default that is reported is a different thing from a literal that
is hidden.

HALF TWO -- THE RESPONSE OVERSTATES WHAT WAS APPLIED
``execute_backtest``'s docstring describes its ``configuration`` key as "the configuration
that was actually applied, so a client can show what produced the numbers rather than what
it hoped it sent". Four of the seven configuration fields it reports reach no simulation:

``spread``
    Reaches ``BacktestEngine.spread`` and stops there. The engine says so itself, in a
    comment next to the log line that used to claim otherwise: "self.spread reaches no
    simulation at all, so no spread is modelled".
``risk_per_trade``
    Reaches ``RiskEngine.risk_per_trade``, which is read only by
    ``RiskEngine.calculate_position_size``. ``run_backtest`` never calls it.
``max_drawdown``, ``daily_loss_limit``
    Reach ``RiskEngine``, which ``run_backtest`` consults exactly once --
    ``if not self.risk_engine.can_trade()`` -- before any trade has been simulated, on an
    engine whose ``current_equity`` still equals its ``peak_equity`` and whose
    ``daily_loss`` is still zero. Nothing updates either during the run. They are not
    during-run limits; in the normal range they cannot change the outcome, which the test
    below measures by running the same backtest at 0.9 and at 0.05.

The three that do work are ``initial_capital`` (``init_cash``), ``commission``
(``fees=``) and ``slippage`` (``slippage=``), and ``tests/test_task_6_1_backtest_execute.py``
already pins those reaching the engines.

``configuration`` itself is not changed and not pruned -- it is an accurate record of what
was *submitted*, and ``test_task_6_1_backtest_execute.py`` asserts exact dict equality on
it. The claim that needs correcting is the one that says submitted means applied.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import backtest_runtime as rt
from backend_app.backend.backtest_runtime import BacktestRuntime

from tests.test_backtest_evidence_columns_regression import (  # noqa: E402
    RECONCILED_FOR_BAR_COUNT,
    TABLE,
    USER,
    _bars,
    _running_row,
    _service,
    _StubDagEngine,
    _StubDataEngine,
    _StubExecutionGraph,
    _Supabase,
)

# The literals that are in the source today, and that must remain the DOCUMENTED DEFAULT
# rather than the only possible value. Named here so a change to either is deliberate.
EXPECTED_DEFAULTS = {
    "trade_size_pct": 0.10,
    "stop_loss_pct": 0.05,
    "take_profit_pct": 0.15,
}

# A risk block using the LIVE path's key names, deliberately nothing like the defaults.
STRATEGY_RISK = {
    "position_size_pct": 0.25,
    "stop_loss_pct": 0.03,
    "take_profit_pct": 0.09,
}


class _SpyEngine:
    """Records the ``params`` the runtime hands the simulator.

    The assertion target for half one: what matters is not that a number exists somewhere
    but that it reaches ``run_backtest_async``, which is where ``sl_stop``, ``tp_stop``
    and ``size`` are decided.
    """

    initial_capital = 10_000.0
    fees = 0.001
    slippage = 0.0005
    spread = 0.0002

    def __init__(self):
        self.params = None
        self.calls = 0

    async def run_backtest_async(self, **kwargs):
        self.calls += 1
        self.params = dict(kwargs.get("params") or {})
        return (
            {
                "final_equity": 10_500.0,
                "total_trades": 7,
                "total_return_pct": 5.0,
                "equity_curve": [{"timestamp": "2024-01-01T00:00:00Z", "equity": 10_500.0}],
            },
            [],
        )


class _StubPackage:
    """A strategy package carrying metadata, the way ``run_version_backtest`` builds one."""

    def __init__(self, metadata):
        self.execution_graph = _StubExecutionGraph()
        self.metadata = dict(metadata)


def _runtime(sb, engine, **kwargs) -> BacktestRuntime:
    """The real runtime with only the feed, the DAG engine and the simulator stubbed."""
    runtime = BacktestRuntime(initial_capital=10_000.0, **kwargs)
    runtime.set_data_engine = lambda *_a, **_k: None
    runtime.data_engine = _StubDataEngine(_bars())
    runtime.dag_engine = _StubDagEngine()
    runtime.vectorbt_engine = engine
    runtime._calculate_performance_metrics = lambda *a, **k: {}
    runtime._generate_charts = lambda *a, **k: {}
    runtime.backtest_service = _service(sb)
    return runtime


async def _run(metadata, **runtime_kwargs):
    """One ``run_backtest`` against a spy simulator. Returns ``(envelope, spy)``."""
    # `RECONCILED_FOR_BAR_COUNT` is the harness's "migration 006 fully applied" column
    # set, so nothing here fails on an absent evidence column -- this file is about the
    # risk params, not about schema drift.
    sb = _Supabase(RECONCILED_FOR_BAR_COUNT, rows={TABLE: [_running_row()]})
    engine = _SpyEngine()
    runtime = _runtime(sb, engine, **runtime_kwargs)
    envelope = await runtime.run_backtest(
        strategy_package=_StubPackage(metadata),
        user=USER,
        strategy_id="11111111-1111-4111-8111-111111111111",
        version_id="22222222-2222-4222-8222-222222222222",
        version="v1.0",
        start_date="2024-01-01",
        end_date="2024-03-01",
        exchange_instance=object(),
    )
    return envelope, engine


BASE_METADATA = {"symbols": ["BTC/USDT"], "timeframe": "15m"}


# ---------------------------------------------------------------------------
# 1. Half one -- the strategy's configured risk reaches the simulator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTheStrategysRiskConfigReachesTheSimulator:
    async def test_the_configured_trade_size_is_what_is_simulated(self):
        _envelope, engine = await _run({**BASE_METADATA, "risk": STRATEGY_RISK})

        assert engine.params["trade_size_pct"] == 0.25, (
            f"the strategy declares position_size_pct 0.25 but the simulator was given "
            f"trade_size_pct {engine.params.get('trade_size_pct')}"
        )

    async def test_the_configured_stops_are_what_is_simulated(self):
        _envelope, engine = await _run({**BASE_METADATA, "risk": STRATEGY_RISK})

        assert (
            engine.params["stop_loss_pct"],
            engine.params["take_profit_pct"],
        ) == (0.03, 0.09), (
            f"the strategy declares a 3% stop and a 9% target; the simulator was given "
            f"{engine.params.get('stop_loss_pct')} and "
            f"{engine.params.get('take_profit_pct')}"
        )

    async def test_the_key_names_are_the_live_paths_own(self):
        """``position_size_pct``, not a second spelling invented for the backtester.

        ``strategy_builder.run_logic`` subscripts ``risk_cfg["position_size_pct"]`` and
        ``.get("stop_loss_pct")`` / ``.get("take_profit_pct")``. If the backtester read
        different keys off the same column, the two would silently disagree again for any
        strategy configured through the live path.
        """
        assert rt.RISK_KEY_TRADE_SIZE == "position_size_pct"
        assert rt.RISK_KEY_STOP_LOSS == "stop_loss_pct"
        assert rt.RISK_KEY_TAKE_PROFIT == "take_profit_pct"

    async def test_two_strategies_with_different_risk_are_simulated_differently(self):
        _a, engine_a = await _run(
            {**BASE_METADATA, "risk": {"position_size_pct": 0.02, "stop_loss_pct": 0.01}}
        )
        _b, engine_b = await _run(
            {**BASE_METADATA, "risk": {"position_size_pct": 0.50, "stop_loss_pct": 0.20}}
        )

        assert engine_a.params["trade_size_pct"] != engine_b.params["trade_size_pct"]
        assert engine_a.params["stop_loss_pct"] != engine_b.params["stop_loss_pct"]


@pytest.mark.asyncio
class TestAnAbsentRiskConfigFallsBackAndSaysSo:
    async def test_an_empty_risk_block_uses_the_documented_defaults(self):
        """``strategies.risk`` defaults to ``'{}'``, so this is the common case."""
        _envelope, engine = await _run({**BASE_METADATA, "risk": {}})

        for key, expected in EXPECTED_DEFAULTS.items():
            assert engine.params[key] == expected, (
                f"with no configured risk, {key} should fall back to the documented "
                f"default {expected}, got {engine.params.get(key)}"
            )

    async def test_a_missing_risk_key_entirely_also_falls_back(self):
        _envelope, engine = await _run(dict(BASE_METADATA))

        for key, expected in EXPECTED_DEFAULTS.items():
            assert engine.params[key] == expected

    async def test_a_partially_configured_block_defaults_only_the_absent_fields(self):
        _envelope, engine = await _run(
            {**BASE_METADATA, "risk": {"position_size_pct": 0.33}}
        )

        assert engine.params["trade_size_pct"] == 0.33
        assert engine.params["stop_loss_pct"] == EXPECTED_DEFAULTS["stop_loss_pct"]
        assert engine.params["take_profit_pct"] == EXPECTED_DEFAULTS["take_profit_pct"]

    async def test_the_defaults_are_named_constants_not_literals(self):
        """So that "what does a backtest assume?" has one answer that can be cited."""
        assert rt.DEFAULT_TRADE_SIZE_PCT == EXPECTED_DEFAULTS["trade_size_pct"]
        assert rt.DEFAULT_STOP_LOSS_PCT == EXPECTED_DEFAULTS["stop_loss_pct"]
        assert rt.DEFAULT_TAKE_PROFIT_PCT == EXPECTED_DEFAULTS["take_profit_pct"]


# ---------------------------------------------------------------------------
# 2. Half two -- the result states what was applied, and its provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTheResultStatesTheRiskItApplied:
    async def test_the_envelope_reports_the_applied_risk(self):
        envelope, engine = await _run({**BASE_METADATA, "risk": STRATEGY_RISK})

        applied = envelope.get("applied_risk")
        assert applied is not None, (
            f"the result does not say what per-trade risk it simulated; keys: "
            f"{sorted(envelope)}"
        )
        assert applied["trade_size_pct"] == 0.25
        assert applied["stop_loss_pct"] == 0.03
        assert applied["take_profit_pct"] == 0.09

    async def test_the_applied_risk_agrees_with_what_the_simulator_was_given(self):
        """A report that could drift from the run would be worse than no report."""
        envelope, engine = await _run({**BASE_METADATA, "risk": STRATEGY_RISK})

        applied = envelope["applied_risk"]
        for key in EXPECTED_DEFAULTS:
            assert applied[key] == engine.params[key], (
                f"applied_risk says {key}={applied[key]} but the simulator was given "
                f"{engine.params[key]}"
            )

    async def test_a_configured_value_is_attributed_to_the_strategy(self):
        envelope, _engine = await _run({**BASE_METADATA, "risk": STRATEGY_RISK})

        source = envelope["applied_risk"]["source"]
        assert source["trade_size_pct"] == rt.RISK_SOURCE_STRATEGY
        assert source["stop_loss_pct"] == rt.RISK_SOURCE_STRATEGY
        assert source["take_profit_pct"] == rt.RISK_SOURCE_STRATEGY

    async def test_a_defaulted_value_is_attributed_to_the_default(self):
        envelope, _engine = await _run({**BASE_METADATA, "risk": {}})

        source = envelope["applied_risk"]["source"]
        assert source["trade_size_pct"] == rt.RISK_SOURCE_DEFAULT
        assert source["stop_loss_pct"] == rt.RISK_SOURCE_DEFAULT
        assert source["take_profit_pct"] == rt.RISK_SOURCE_DEFAULT

    async def test_a_partial_block_is_attributed_field_by_field(self):
        envelope, _engine = await _run(
            {**BASE_METADATA, "risk": {"position_size_pct": 0.33}}
        )

        source = envelope["applied_risk"]["source"]
        assert source["trade_size_pct"] == rt.RISK_SOURCE_STRATEGY
        assert source["stop_loss_pct"] == rt.RISK_SOURCE_DEFAULT
        assert source["take_profit_pct"] == rt.RISK_SOURCE_DEFAULT


# ---------------------------------------------------------------------------
# 3. Half two -- the four configuration fields that reach no simulation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTheInertConfigurationFieldsAreDeclared:
    async def test_the_runtime_names_the_fields_that_reach_no_simulation(self):
        """A caller must be able to tell a control from a decoration.

        Named on the runtime rather than assembled in the router, so the list lives next
        to the code that would have to change for a field to stop being inert.
        """
        assert set(rt.CONFIGURATION_FIELDS_NOT_APPLIED) == {
            "spread",
            "risk_per_trade",
            "max_drawdown",
            "daily_loss_limit",
        }, (
            "the set of accepted-but-unapplied backtest configuration fields is not "
            f"declared as expected: {getattr(rt, 'CONFIGURATION_FIELDS_NOT_APPLIED', None)}"
        )

    async def test_every_declared_inert_field_has_a_stated_reason(self):
        for field in rt.CONFIGURATION_FIELDS_NOT_APPLIED:
            reason = rt.CONFIGURATION_FIELDS_NOT_APPLIED[field]
            assert isinstance(reason, str) and len(reason) > 20, (
                f"{field} is declared inert with no usable explanation: {reason!r}"
            )

    async def test_max_drawdown_does_not_change_the_result_in_the_normal_range(self):
        """The measurement behind the claim, rather than the claim alone.

        ``can_trade()`` is consulted once, before any trade, on an engine whose equity
        still equals its peak. If that is right, a 5% ceiling and a 90% ceiling must
        produce the same run.
        """
        _a, engine_a = await _run(dict(BASE_METADATA), max_drawdown=0.05)
        _b, engine_b = await _run(dict(BASE_METADATA), max_drawdown=0.90)

        assert engine_a.params == engine_b.params
        assert engine_a.calls == engine_b.calls == 1

    async def test_daily_loss_limit_does_not_change_the_result_in_the_normal_range(self):
        _a, engine_a = await _run(dict(BASE_METADATA), daily_loss_limit=0.01)
        _b, engine_b = await _run(dict(BASE_METADATA), daily_loss_limit=0.99)

        assert engine_a.params == engine_b.params

    async def test_risk_per_trade_does_not_change_the_result(self):
        """It feeds only ``calculate_position_size``, which the backtest never calls."""
        _a, engine_a = await _run(dict(BASE_METADATA), risk_per_trade=0.001)
        _b, engine_b = await _run(dict(BASE_METADATA), risk_per_trade=0.999)

        assert engine_a.params == engine_b.params


class TestTheRouteDoesNotClaimSubmittedMeansApplied:
    def test_the_docstring_no_longer_says_the_configuration_was_actually_applied(self):
        """The false claim lives in prose, so prose is where it has to be fixed.

        ``configuration`` is an accurate record of what was SUBMITTED and is not changed
        by this task -- ``test_task_6_1_backtest_execute.py`` asserts exact dict equality
        on it. What is corrected is the sentence promising a client it can "show what
        produced the numbers rather than what it hoped it sent", which was not true of
        four of its seven fields.
        """
        import inspect

        from backend_app.routers import strategy_operations as so

        doc = inspect.getdoc(so.execute_backtest) or ""
        assert "the configuration that was actually applied" not in doc, (
            "execute_backtest still promises that `configuration` is what was applied, "
            "while spread, risk_per_trade, max_drawdown and daily_loss_limit reach no "
            "simulation"
        )

    def test_the_route_forwards_the_strategys_risk_block(self):
        """Source-level, because the risk dict is in the handler and was never passed on.

        ``owned["strategy"]["risk"]`` is already loaded by ``get_strategy``'s
        ``select("*")``; the handler used it for the archive check only.
        """
        import inspect

        from backend_app.routers import strategy_operations as so

        source = inspect.getsource(so.execute_backtest)
        assert "strategy_risk" in source, (
            "execute_backtest does not forward a strategy_risk argument, so the "
            "simulator cannot know the strategy's configured per-trade risk"
        )
