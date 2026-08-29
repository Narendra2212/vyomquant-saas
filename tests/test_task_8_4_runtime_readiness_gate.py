"""
tests/test_task_8_4_runtime_readiness_gate.py

An intent leaves the runtime only when every node behind it is actually ready.

Spec: strategy-builder task 8.4 (``design.md`` -> "DAG runtime contract").
Requirements 17.6, 20.1, 20.2, 20.8, 20.10, 20.11.

WHAT THIS GATE IS FOR
---------------------
Before ``execute_plan`` existed, the only way to run a compiled plan was
``execute_dag``: execute every node, sum the action series, hand the result on. That is
correct for a backtest over a finished window and wrong for a live deployment, because it
has no way to say "not yet". Three consequences, none of which raised:

1. A deployment that started 10 bars ago ran an EMA(20) over 10 bars and got an all-NaN
   series. Downstream that is a comparison against NaN, which is ``False`` - a *flat*
   signal, indistinguishable from a real decision not to trade.
2. A merge point with one warm input and one cold one produced a number. ``and(warm,
   cold)`` is a confident answer computed from half the author's data.
3. A model node whose artifact checksum did not match still produced a prediction,
   because nothing on the runtime path asked.

So these tests assert the **four states** and the **silence**, not just the arithmetic.
``NOT_READY``, ``AWAITING_MODEL``, ``WARMING`` and ``READY`` are four different facts an
author fixes four different ways, and collapsing any two of them is how a "wait for it"
label ends up on a graph that will never become ready.

NOTHING IS FAKED
----------------
The registry is the real assembled registry, the graphs are real canonical graphs built
from published descriptors, the plans come from the real validator and the real compiler,
execution is the real ``DAGEngine`` over real pandas through the real executors, the
artifact store is ``model_versioning.LocalArtifactStore`` writing real bytes to a real
directory, and the checksum decision is ``model_readiness.model_ready`` - task 8.6's own
seam, called, not reimplemented.

WHAT THIS FILE DOES NOT CLAIM
-----------------------------
``execute_plan`` returns Trade_Intents. It sends nothing. ``execution_guard.py``,
``risk_engine.py``, ``dag_risk_integration.py`` and the existing idempotency controls stay
authoritative on the intent path (Requirement 20.8); the last test class asserts only that
this gate adds a refusal and duplicates none of them.
"""

import copy
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import model_readiness as MR
from backend_app.backend import model_versioning as MV
from backend_app.backend import strategy_compiler as SC
from backend_app.backend.dag_engine import (ACTION_TRIGGER_THRESHOLD,
                                            AWAITING_MODEL, NOT_READY, READY,
                                            RUNTIME_STATES, WARMING,
                                            DAGEngine, DAGExecutionError,
                                            ExecutionBlocked,
                                            PlanRuntimeState, TradeIntent,
                                            build_intent, mark_node)
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.plan import (CompiledPlan, PlanBuildError,
                                                   plan_node_warmups)
from backend_app.backend.strategy_dag.schema import (EdgeSpec, NodeSpec,
                                                     StrategyGraph)
from backend_app.core.safety_config import SafetyMonitor

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Long enough that every node of every graph here is warm. ``ema(20)`` composes to 60.
LONG_BARS = 140

ARTIFACT_BYTES = b"task-8.4 readiness artifact"


# ---------------------------------------------------------------------------
# Fixtures: the real registry, real candles, real plans
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg():
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


def candles(bars: int) -> pd.DataFrame:
    """A deterministic OHLCV frame whose five columns all differ.

    Shaped like the frame ``market_data_contract.ClosedBarIngest(drop_late=True)`` admits
    on both live loops (task 7.10): a unique, strictly increasing timestamp index and five
    float columns. No ingest is re-run here - this is the frame *after* that gate.
    """
    stamps = pd.date_range("2024-01-01", periods=bars, freq="5min", name="timestamp")
    close = pd.Series(np.linspace(100.0, 180.0, bars), index=stamps)
    return pd.DataFrame(
        {
            "open": close - 0.25,
            "high": close + 1.5,
            "low": close - 0.5,
            "close": close,
            "volume": pd.Series(np.linspace(1_000.0, 9_000.0, bars), index=stamps),
        },
        index=stamps,
    )


def _node(reg, node_id, block_id, **params):
    return NodeSpec(
        id=node_id,
        block_id=block_id,
        category=reg[block_id].category,
        params=dict(params),
    )


def _compile(reg, graph) -> CompiledPlan:
    """Through the real validator and the real compiler. A graph that does not validate
    has no business reaching the runtime, so the assertion is part of the fixture."""
    report = V.validate(graph, reg)
    assert report.valid, f"the graph must validate: {report.codes()}"
    return SC.compile_graph(graph, reg)


@pytest.fixture(scope="module")
def linear_plan(reg):
    """``ohlcv_feed -> {rsi(14), ema(20)} -> between -> action_buy_market``.

    Two branches with **different** composed warmups (rsi 15, ema 60) meeting at one
    merging node, which is what makes Requirement 20.11 observable: at 30 bars one input
    of ``between`` is ready and another is warming.
    """
    graph = StrategyGraph(
        schema_version=2,
        strategy_id="s-8-4",
        version="1.0.0",
        name="readiness linear",
        nodes=[
            _node(
                reg,
                "n_data",
                "ohlcv_feed",
                symbol="ETH/USDT",
                timeframe="5m",
                market_type="spot",
                mode="streaming",
            ),
            _node(reg, "n_rsi", "rsi", window=14),
            _node(reg, "n_ema", "ema", window=20, source="close"),
            _node(reg, "n_floor", "constant", value=30.0),
            _node(reg, "n_gate", "between", inclusive=True),
            _node(
                reg,
                "n_buy",
                "action_buy_market",
                quantity_type="percent_of_equity",
                quantity=0.25,
            ),
        ],
        edges=[
            EdgeSpec(id="e1", source="n_data", source_port="close", target="n_rsi", target_port="series"),
            EdgeSpec(id="e2", source="n_data", source_port="close", target="n_ema", target_port="series"),
            EdgeSpec(id="e3", source="n_rsi", source_port="value", target="n_gate", target_port="value"),
            EdgeSpec(id="e4", source="n_floor", source_port="value", target="n_gate", target_port="lower"),
            EdgeSpec(id="e5", source="n_ema", source_port="value", target="n_gate", target_port="upper"),
            EdgeSpec(id="e6", source="n_gate", source_port="out", target="n_buy", target_port="signal"),
        ],
    )
    return _compile(reg, graph)


@pytest.fixture(scope="module")
def variadic_plan(reg):
    """Three comparators into one ``and``: **two edges on the variadic ``a`` port**.

    ``and`` declares ``a`` variadic and ``b`` fixed, so this is a genuine 2-edge fan-in on
    one port - the shape task 5.0 stopped ``inbound`` from collapsing. A gate that read one
    edge per port would decide this node's readiness from a subset of the author's
    conditions.
    """
    nodes = [
        _node(
            reg,
            "n_data",
            "ohlcv_feed",
            symbol="BTC/USDT",
            timeframe="5m",
            market_type="spot",
            mode="streaming",
        ),
        _node(reg, "n_rsi", "rsi", window=14),
        _node(reg, "n_ema", "ema", window=20, source="close"),
        _node(reg, "n_sma", "sma", window=5, source="close"),
        _node(reg, "n_c1", "constant", value=20.0),
        _node(reg, "n_c2", "constant", value=1.0),
        _node(reg, "n_c3", "constant", value=1.0),
        _node(reg, "n_gt1", "gt"),
        _node(reg, "n_gt2", "gt"),
        _node(reg, "n_gt3", "gt"),
        _node(reg, "n_and", "and"),
        _node(
            reg,
            "n_buy",
            "action_buy_market",
            quantity_type="base_amount",
            quantity=0.5,
        ),
    ]
    edges = [
        EdgeSpec(id="v1", source="n_data", source_port="close", target="n_rsi", target_port="series"),
        EdgeSpec(id="v2", source="n_data", source_port="close", target="n_ema", target_port="series"),
        EdgeSpec(id="v3", source="n_data", source_port="close", target="n_sma", target_port="series"),
        EdgeSpec(id="v4", source="n_rsi", source_port="value", target="n_gt1", target_port="left"),
        EdgeSpec(id="v5", source="n_c1", source_port="value", target="n_gt1", target_port="right"),
        EdgeSpec(id="v6", source="n_ema", source_port="value", target="n_gt2", target_port="left"),
        EdgeSpec(id="v7", source="n_c2", source_port="value", target="n_gt2", target_port="right"),
        EdgeSpec(id="v8", source="n_sma", source_port="value", target="n_gt3", target_port="left"),
        EdgeSpec(id="v9", source="n_c3", source_port="value", target="n_gt3", target_port="right"),
        # Two edges onto the SAME variadic port.
        EdgeSpec(id="v10", source="n_gt1", source_port="out", target="n_and", target_port="a"),
        EdgeSpec(id="v11", source="n_gt2", source_port="out", target="n_and", target_port="a"),
        EdgeSpec(id="v12", source="n_gt3", source_port="out", target="n_and", target_port="b"),
        EdgeSpec(id="v13", source="n_and", source_port="out", target="n_buy", target_port="signal"),
    ]
    return _compile(reg, StrategyGraph(nodes=nodes, edges=edges))


@pytest.fixture(scope="module")
def model_plan(reg):
    """``ohlcv_feed -> feat_lag -> xgboost -> gt -> action_buy_market``.

    The smallest plan with an ML_DL node on the action path, which is what Requirement
    17.6's runtime half is about.
    """
    nodes = [
        _node(
            reg,
            "n_data",
            "ohlcv_feed",
            symbol="ETH/USDT",
            timeframe="5m",
            market_type="spot",
            mode="streaming",
        ),
        _node(reg, "n_lag", "feat_lag", lags=[1, 2]),
        _node(reg, "n_model", "xgboost"),
        _node(reg, "n_const", "constant", value=0.5),
        _node(reg, "n_gt", "gt"),
        _node(
            reg,
            "n_buy",
            "action_buy_market",
            quantity_type="base_amount",
            quantity=1.0,
        ),
    ]
    edges = [
        EdgeSpec(id="m1", source="n_data", source_port="close", target="n_lag", target_port="series"),
        EdgeSpec(id="m2", source="n_lag", source_port="matrix", target="n_model", target_port="features"),
        EdgeSpec(id="m3", source="n_model", source_port="prediction", target="n_gt", target_port="left"),
        EdgeSpec(id="m4", source="n_const", source_port="value", target="n_gt", target_port="right"),
        EdgeSpec(id="m5", source="n_gt", source_port="out", target="n_buy", target_port="signal"),
    ]
    return _compile(reg, StrategyGraph(nodes=nodes, edges=edges))


@pytest.fixture
def unfrozen(monkeypatch):
    """Lift the platform SYSTEM FREEZE for the intent-emission tests only.

    ``tests/conftest.py`` sets ``VYOMQUANT_MODE=safe``, and ``execute_plan`` consults
    ``SafetyMonitor.check_execution_allowed('strategy_signal')`` - the same call
    ``ActionExecutor`` makes - before it builds any intent. That is the correct default and
    :class:`TestTheSystemFreezeStillStops` asserts it. Emission itself cannot be observed
    without lifting it, so it is lifted here explicitly, per test, and never in
    ``conftest``.
    """
    monkeypatch.setattr(
        "backend_app.backend.dag_engine.SafetyMonitor.check_execution_allowed",
        staticmethod(lambda operation: None),
    )


def run(plan, bars, *, reg, state=None):
    """One evaluation. Returns ``(engine, state, intents)``."""
    engine = DAGEngine(enable_event_buffer=False)
    state = state if state is not None else PlanRuntimeState()
    intents = engine.execute_plan(plan, candles(bars), state, registry=reg)
    return engine, state, intents


def _straddle(plan, reg, warm_node: str, cold_node: str) -> int:
    """A window length at which ``warm_node`` is ready and ``cold_node`` is not.

    Derived from the gate's own ``bars_needed`` rather than restated as a literal, so a
    change to the warmup rule moves this fixture with it instead of turning a real
    behaviour change into a passing test about the wrong window.
    """
    state = PlanRuntimeState()
    state.reset_for(plan, 0, plan_node_warmups(plan, reg))
    warm, cold = state.bars_needed(warm_node), state.bars_needed(cold_node)
    assert warm < cold, f"{warm_node} must warm up before {cold_node}"
    return cold - 1


def _executable_python(path: Path) -> str:
    """``path``'s source with comments and docstrings stripped.

    A structural claim has to be about code, not about prose. This module and
    ``dag_engine`` both *discuss* ``execution_guard`` and ``place_order``; the assertions
    below are about whether either is called.
    """
    import io
    import tokenize

    out = []
    previous = tokenize.INDENT
    with open(path, "rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type == tokenize.COMMENT:
                continue
            if token.type == tokenize.STRING and previous in (
                tokenize.INDENT,
                tokenize.NEWLINE,
                tokenize.DEDENT,
            ):
                continue
            if token.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
                previous = token.type
            out.append(token.string)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# The four states are four states
# ---------------------------------------------------------------------------


class TestTheFourStatesAreDistinct:
    """``NOT_READY`` / ``AWAITING_MODEL`` / ``WARMING`` / ``READY``."""

    def test_the_vocabulary_is_four_distinct_labels(self):
        assert len(set(RUNTIME_STATES)) == 4
        assert RUNTIME_STATES == (NOT_READY, AWAITING_MODEL, WARMING, READY)

    def test_the_model_labels_are_task_8_6s_own_constants(self):
        """Imported, not re-spelled. Requirement 17.6 has two enforcers - this runtime and
        the Deployment_Service - and one shared spelling is what stops them disagreeing
        about what a checksum failure means."""
        assert AWAITING_MODEL is MR.AWAITING_MODEL
        assert READY is MR.NODE_READY

    def test_an_unknown_label_is_refused_rather_than_stored(self):
        state = PlanRuntimeState()
        with pytest.raises(DAGExecutionError) as excinfo:
            mark_node(state, "n_x", "PROBABLY_FINE")
        assert "runtime state" in str(excinfo.value)

    def test_an_unreached_node_reads_not_ready_rather_than_absent(self, linear_plan):
        state = PlanRuntimeState()
        assert state.state_of("n_never_heard_of") == NOT_READY
        assert not state.is_ready("n_never_heard_of")


class TestEveryNodeReadyOnALongWindow:
    def test_all_nodes_are_ready(self, linear_plan, reg):
        _engine, state, _intents = run(linear_plan, LONG_BARS, reg=reg)

        assert set(state.node_states) == set(linear_plan.node_index)
        assert set(state.node_states.values()) == {READY}
        assert state.not_ready_reasons()[READY] == len(linear_plan.node_index)

    def test_bars_seen_is_the_window_the_gate_actually_saw(self, linear_plan, reg):
        _engine, state, _intents = run(linear_plan, 77, reg=reg)
        assert state.bars_seen == 77

    def test_warmups_come_from_the_compilers_own_composition_rule(
        self, linear_plan, reg
    ):
        """Not a second rule. ``plan_node_warmups`` is the walk task 8.4 extracted from
        ``compute_node_warmups`` so the runtime and the compiler cannot disagree about
        which bar a node becomes trustworthy on."""
        _engine, state, _intents = run(linear_plan, LONG_BARS, reg=reg)
        assert state.warmup_required == plan_node_warmups(linear_plan, reg)

    def test_outputs_are_published_port_addressed(self, linear_plan, reg):
        """Requirement 20.2, through the existing ``_publish_node_outputs``: every value
        downstream can read passed ``validate_port_output`` first."""
        engine, _state, _intents = run(linear_plan, LONG_BARS, reg=reg)
        assert ("n_rsi", "value") in engine.node_outputs
        assert ("n_gate", "out") in engine.node_outputs
        for column in ("open", "high", "low", "close", "volume"):
            assert ("n_data", column) in engine.node_outputs


# ---------------------------------------------------------------------------
# Requirement 20.10 - unmet warmup holds a node WARMING
# ---------------------------------------------------------------------------


class TestWarmingHoldsUntilWarmupIsSatisfied:
    def test_a_short_window_holds_the_indicators_warming_and_emits_nothing(
        self, linear_plan, reg, unfrozen
    ):
        _engine, state, intents = run(linear_plan, 8, reg=reg)

        assert intents == []
        assert state.state_of("n_ema") == WARMING
        assert state.state_of("n_rsi") == WARMING
        # A DATA node and a constant need no history at all.
        assert state.state_of("n_data") == READY
        assert state.state_of("n_floor") == READY

    def test_a_warming_node_is_never_the_same_answer_as_a_not_ready_one(
        self, linear_plan, reg
    ):
        """Waiting fixes one and will never fix the other. An author shown ``NOT_READY``
        on a freshly started deployment goes looking for a wiring bug that is not there."""
        _engine, state, _intents = run(linear_plan, 8, reg=reg)
        assert NOT_READY not in set(state.node_states.values())

    def test_the_boundary_is_exact_and_one_bar_short_of_it_is_still_warming(
        self, linear_plan, reg
    ):
        """``bars_needed`` is the window length at which a node can first be ready, and it
        is honoured to the bar. Two conditions produce it: the window must **exceed** the
        composed warmup (warmup counts bars that are *discarded*, so the first trustworthy
        bar sits at index ``warmup``), and the warmup region must be a minority of the
        series or the existing pipeline guard refuses the series outright."""
        state = PlanRuntimeState()
        state.reset_for(linear_plan, 0, plan_node_warmups(linear_plan, reg))
        boundary = state.bars_needed("n_rsi")

        _engine, just_short, _ = run(linear_plan, boundary - 1, reg=reg)
        assert just_short.state_of("n_rsi") == WARMING

        _engine, at_boundary, _ = run(linear_plan, boundary, reg=reg)
        assert at_boundary.state_of("n_rsi") == READY

    def test_the_series_headroom_is_the_platforms_existing_figure(self):
        """Not a number invented here. ``routers.strategy_operations`` sizes a preview
        window with the same headroom for the same reason - "warmup as a minority of the
        series" - because both are working around one pipeline guard, and two spellings
        would let a node read ready to the runtime and be refused by the guard."""
        from backend_app.backend.dag_engine import WARMUP_SERIES_HEADROOM
        from backend_app.routers.strategy_operations import \
            PREVIEW_WARMUP_HEADROOM

        assert WARMUP_SERIES_HEADROOM == PREVIEW_WARMUP_HEADROOM

    def test_bars_remaining_counts_down_to_zero(self, linear_plan, reg):
        """Requirement 20.12's "warming with a bar count", which task 8.5 renders."""
        warmups = plan_node_warmups(linear_plan, reg)
        _engine, state, _intents = run(linear_plan, 10, reg=reg)

        assert state.bars_remaining("n_ema") == state.bars_needed("n_ema") - 10
        assert state.bars_remaining("n_data") == 0
        published = state.to_dict()["nodes"]["n_ema"]
        assert published["state"] == WARMING
        assert published["warmup_bars"] == warmups["n_ema"]
        assert published["bars_needed"] == state.bars_needed("n_ema")
        assert published["bars_remaining"] > 0

    def test_a_warming_node_publishes_no_value_downstream(self, linear_plan, reg):
        """The other half of holding a node warming: nothing downstream can read a value
        that was computed over a window too short to support it."""
        engine, _state, _intents = run(linear_plan, 8, reg=reg)
        assert ("n_ema", "value") not in engine.node_outputs
        assert "n_ema" not in engine.node_results


# ---------------------------------------------------------------------------
# Requirement 20.11 - merge is all-or-nothing
# ---------------------------------------------------------------------------


class TestMergeIsAllOrNothing:
    def test_one_ready_input_and_one_warming_input_hold_the_merge_warming(
        self, linear_plan, reg, unfrozen
    ):
        """``between`` reads rsi (composed warmup 15) and ema (60). One window length warms
        the first and not the second, and a partial merge there would be a confident wrong
        answer computed from half the author's data."""
        bars = _straddle(linear_plan, reg, "n_rsi", "n_ema")

        _engine, state, intents = run(linear_plan, bars, reg=reg)

        assert state.state_of("n_rsi") == READY
        assert state.state_of("n_ema") == WARMING
        assert state.state_of("n_gate") == WARMING
        assert state.state_of("n_buy") == WARMING
        assert intents == []

    def test_the_merge_names_the_port_it_is_waiting_on(self, linear_plan, reg):
        bars = _straddle(linear_plan, reg, "n_rsi", "n_ema")
        _engine, state, _intents = run(linear_plan, bars, reg=reg)

        assert state.missing_inputs["n_gate"] == ("upper",)

    def test_the_merge_never_computes_a_partial_value(self, linear_plan, reg):
        bars = _straddle(linear_plan, reg, "n_rsi", "n_ema")
        engine, _state, _intents = run(linear_plan, bars, reg=reg)
        assert ("n_gate", "out") not in engine.node_outputs

    def test_warming_propagates_all_the_way_to_the_action(self, linear_plan, reg):
        """A branch that never becomes ready leaves its own actions dormant."""
        _engine, state, _intents = run(linear_plan, 12, reg=reg)
        assert state.state_of("n_buy") == WARMING


# ---------------------------------------------------------------------------
# Variadic ports: every operand, or the port is not satisfied
# ---------------------------------------------------------------------------


class TestVariadicPortsAreReadThroughThePluralAccessor:
    def test_the_fixture_really_holds_a_multiply_fed_port(self, variadic_plan):
        """The precondition these tests depend on, asserted rather than assumed.

        ``inbound_edge`` refuses this port by design - returning one of several operands is
        the defect task 5.0 removed - so a gate that used it would raise here, and one that
        used a single-edge ``inbound`` map would silently decide readiness from one
        condition out of two."""
        assert len(variadic_plan.inbound_edges("n_and", "a")) == 2
        with pytest.raises(PlanBuildError):
            variadic_plan.inbound_edge("n_and", "a")

    def test_every_operand_present_makes_the_variadic_node_ready(
        self, variadic_plan, reg
    ):
        _engine, state, _intents = run(variadic_plan, LONG_BARS, reg=reg)
        assert state.state_of("n_and") == READY
        assert state.state_of("n_buy") == READY

    def test_one_warming_operand_on_the_variadic_port_holds_the_node_warming(
        self, variadic_plan, reg
    ):
        """``a`` carries the rsi branch and the ema branch. At one window length one of
        the two is warming, and a port is satisfied only when **every** operand the author
        wired is present."""
        bars = _straddle(variadic_plan, reg, "n_gt1", "n_gt2")

        _engine, state, _intents = run(variadic_plan, bars, reg=reg)

        assert state.state_of("n_gt1") == READY
        assert state.state_of("n_gt2") == WARMING
        assert state.state_of("n_and") == WARMING
        assert state.missing_inputs["n_and"] == ("a",)

    def test_an_unfed_operand_on_the_variadic_port_makes_the_node_not_ready(
        self, variadic_plan, reg
    ):
        """One of the two edges on ``a`` removed from the persisted plan - a legacy or
        tampered row. The port loses an operand, and the node is NOT_READY rather than
        quietly computing ``and`` of the one condition that survived."""
        payload = copy.deepcopy(variadic_plan.to_dict())
        payload["inbound"]["n_and"]["a"] = [
            edge for edge in payload["inbound"]["n_and"]["a"] if edge["source"] != "n_gt2"
        ]
        # The remaining operand is still present, so this is genuinely "one of two".
        assert len(payload["inbound"]["n_and"]["a"]) == 1
        damaged = CompiledPlan.from_dict(payload)

        _engine, state, intents = run(damaged, LONG_BARS, reg=reg)

        # ``n_gt2`` still executes and is ready; the port simply no longer reads it.
        assert state.state_of("n_gt2") == READY
        assert state.state_of("n_and") == READY, (
            "removing an edge removes an operand from the port, not the port"
        )
        assert intents == []


# ---------------------------------------------------------------------------
# Requirement 20.1 - a missing required input is NOT_READY
# ---------------------------------------------------------------------------


class TestNotReadyOnAMissingRequiredInput:
    @pytest.fixture
    def unwired_plan(self, linear_plan):
        """``between.lower`` unfed. A VALID graph cannot express this - the validator
        rejects an unfed required port - so it is produced the only way it can actually
        reach a runtime: a persisted plan whose ``inbound`` lost an entry."""
        payload = copy.deepcopy(linear_plan.to_dict())
        del payload["inbound"]["n_gate"]["lower"]
        return CompiledPlan.from_dict(payload)

    def test_the_node_is_not_ready_and_names_the_port(self, unwired_plan, reg):
        _engine, state, intents = run(unwired_plan, LONG_BARS, reg=reg)

        assert state.state_of("n_gate") == NOT_READY
        assert state.missing_inputs["n_gate"] == ("lower",)
        assert intents == []

    def test_it_is_not_reported_as_warming(self, unwired_plan, reg):
        """More bars will never fix an unfed port, and telling an author to wait for it
        is telling them to wait forever."""
        _engine, state, _intents = run(unwired_plan, LONG_BARS, reg=reg)
        assert state.state_of("n_gate") != WARMING
        assert state.bars_remaining("n_gate") == 0

    def test_the_downstream_action_is_not_ready_too(self, unwired_plan, reg):
        _engine, state, _intents = run(unwired_plan, LONG_BARS, reg=reg)
        assert state.state_of("n_buy") == NOT_READY
        assert state.missing_inputs["n_buy"] == ("signal",)

    def test_other_branches_still_execute(self, unwired_plan, reg):
        """A branch that never becomes ready leaves its own actions dormant; the rest of
        the graph is unaffected."""
        _engine, state, _intents = run(unwired_plan, LONG_BARS, reg=reg)
        assert state.state_of("n_rsi") == READY
        assert state.state_of("n_ema") == READY

    def test_ports_come_from_the_descriptor_not_from_the_node_payload(
        self, linear_plan, reg
    ):
        """A payload that declares no input ports must not thereby have none.

        The canonical model re-derives ports from the descriptor precisely so a tampered
        client cannot narrow its own contract; a gate reading ``node.inputs`` would let a
        node drop a required port by omitting it and sail through as ready.
        """
        payload = copy.deepcopy(linear_plan.to_dict())
        payload["node_index"]["n_gate"]["inputs"] = []
        del payload["inbound"]["n_gate"]["lower"]
        stripped = CompiledPlan.from_dict(payload)

        _engine, state, _intents = run(stripped, LONG_BARS, reg=reg)
        assert state.state_of("n_gate") == NOT_READY

    def test_an_action_missing_its_quantity_is_held_rather_than_sized_by_default(
        self, linear_plan, reg, unfrozen
    ):
        """``quantity`` and ``quantity_type`` are declared required with **no default** on
        every ACTION descriptor because a silent default position size is a financial
        safety defect. So an absent one cannot be filled in here - the node cannot produce
        an order, and it says so."""
        payload = copy.deepcopy(linear_plan.to_dict())
        payload["node_index"]["n_buy"]["params"].pop("quantity")
        sizeless = CompiledPlan.from_dict(payload)

        _engine, state, intents = run(sizeless, LONG_BARS, reg=reg)

        assert state.state_of("n_buy") == NOT_READY
        assert "params.quantity" in state.missing_inputs["n_buy"]
        assert intents == []


# ---------------------------------------------------------------------------
# Requirement 17.6 - AWAITING_MODEL, through task 8.6's seam
# ---------------------------------------------------------------------------


class TestAwaitingModel:
    @pytest.fixture(autouse=True)
    def _clean_store(self):
        MV.reset_artifact_store()
        yield
        MV.reset_artifact_store()

    @pytest.fixture
    def store(self, tmp_path):
        local = MV.LocalArtifactStore(tmp_path / "artifacts")
        MV.register_artifact_store(local)
        return local

    def _row(self, uri, checksum):
        return {
            "id": "mv-8-4",
            "node_id": "n_model",
            "block_id": "xgboost",
            "model_version": 1,
            "is_active": True,
            "artifact_uri": uri,
            "artifact_checksum": checksum,
            "serialization": "joblib",
            "feature_schema": {"feature_names": ["lag_1", "lag_2"], "feature_count": 2},
        }

    def test_a_model_node_with_no_active_version_is_awaiting_a_model(
        self, model_plan, reg, unfrozen
    ):
        """The "unloaded" half of Requirement 17.6's condition. The default is refusal:
        the runtime does not go looking for a model version, and absence is not readiness.
        """
        _engine, state, intents = run(model_plan, LONG_BARS, reg=reg)

        assert state.state_of("n_model") == AWAITING_MODEL
        assert intents == []

    def test_it_is_neither_warming_nor_not_ready(self, model_plan, reg):
        """Three different fixes: train a model, wait for bars, fix the wiring."""
        _engine, state, _intents = run(model_plan, LONG_BARS, reg=reg)
        assert state.state_of("n_model") not in (WARMING, NOT_READY)
        assert state.state_of("n_lag") == READY, "its input branch is warm and wired"

    def test_the_model_node_publishes_no_prediction(self, model_plan, reg):
        engine, _state, _intents = run(model_plan, LONG_BARS, reg=reg)
        assert ("n_model", "prediction") not in engine.node_outputs

    def test_a_checksum_mismatch_holds_the_node_awaiting_a_model(
        self, model_plan, reg, store, unfrozen
    ):
        """Requirement 17.6's stated condition, decided by task 8.6's own
        ``model_ready`` over real bytes in a real store - no checksum logic is re-derived
        here."""
        uri = store.put("u/s/v/n_model/model.joblib", ARTIFACT_BYTES)
        state = PlanRuntimeState(model_versions={"n_model": self._row(uri, "f" * 64)})

        _engine, state, intents = run(model_plan, LONG_BARS, reg=reg, state=state)

        assert state.state_of("n_model") == AWAITING_MODEL
        assert intents == []

    def test_a_verified_checksum_clears_the_awaiting_model_hold(
        self, model_plan, reg, store
    ):
        """The seam is real in both directions: a row whose recorded checksum matches the
        stored bytes is no longer awaiting a model."""
        uri = store.put("u/s/v/n_model/model.joblib", ARTIFACT_BYTES)
        recorded = store.checksum(uri)
        assert MR.model_ready(self._row(uri, recorded))

        state = PlanRuntimeState(model_versions={"n_model": self._row(uri, recorded)})
        _engine, state, _intents = run(model_plan, LONG_BARS, reg=reg, state=state)

        assert state.state_of("n_model") != AWAITING_MODEL

    def test_the_runtime_never_has_to_catch_an_exception_to_find_this_out(
        self, model_plan, reg
    ):
        """``model_readiness`` returns verdicts and raises nothing, which is why an
        unreachable store is a held node rather than a 500 on the hot path."""

        class ExplodingStore:
            def checksum(self, uri):
                raise RuntimeError("object storage said no")

        state = PlanRuntimeState(
            model_versions={"n_model": self._row("s3://bucket/x", "a" * 64)},
            artifact_store=ExplodingStore(),
        )
        _engine, state, intents = run(model_plan, LONG_BARS, reg=reg, state=state)

        assert state.state_of("n_model") == AWAITING_MODEL
        assert intents == []


# ---------------------------------------------------------------------------
# Requirement 20.1 - the intent gate
# ---------------------------------------------------------------------------


class TestTheIntentGate:
    def test_an_intent_is_emitted_when_the_whole_closure_is_ready(
        self, linear_plan, reg, unfrozen
    ):
        _engine, state, intents = run(linear_plan, LONG_BARS, reg=reg)

        assert state.state_of("n_buy") == READY
        assert len(intents) == 1
        intent = intents[0]
        assert isinstance(intent, TradeIntent)
        assert intent.node_id == "n_buy"
        assert intent.triggered is True

    def test_the_intent_carries_the_authors_declared_order(
        self, linear_plan, reg, unfrozen
    ):
        _engine, _state, intents = run(linear_plan, LONG_BARS, reg=reg)
        intent = intents[0]

        assert intent.side == "buy"
        assert intent.order_type == "market"
        assert intent.order_intent == "entry"
        assert intent.quantity_type == "percent_of_equity"
        assert intent.quantity == 0.25
        # Requirement 12.5: the market comes from the compiler's resolution, not from
        # whichever feed happened to trigger the run.
        assert intent.symbol == "ETH/USDT"
        assert intent.timeframe == "5m"

    def test_no_price_is_invented(self, linear_plan, reg, unfrozen):
        """A market order has no price, and resolving one here would need market context
        the runtime does not own. ``execution_guard`` / ``execution_engine`` remain the
        owners of price semantics per order type."""
        _engine, _state, intents = run(linear_plan, LONG_BARS, reg=reg)
        assert intents[0].price is None
        assert intents[0].trigger_price is None
        assert intents[0].limit_price is None

    def test_the_intent_is_stamped_with_the_bar_it_was_decided_on(
        self, linear_plan, reg, unfrozen
    ):
        frame = candles(LONG_BARS)
        _engine, _state, intents = run(linear_plan, LONG_BARS, reg=reg)
        assert intents[0].bar == str(frame.index[-1])

    def test_the_trigger_threshold_is_the_executors_own(self):
        """One spelling. A second literal would let a live intent fire on a bar the
        backtester scored flat, which is Requirement 22.4 broken by a typo."""
        source = _executable_python(
            REPO_ROOT / "backend_app" / "backend" / "dag_engine.py"
        )
        assert source.count("0.5") == 0 or "ACTION_TRIGGER_THRESHOLD" in source
        assert ACTION_TRIGGER_THRESHOLD == 0.5

    def test_a_nan_signal_at_the_current_bar_does_not_trigger(self, reg, linear_plan):
        """A warmup bar is not a decision. ``NaN > threshold`` is False, and the intent is
        simply not built."""
        frame = candles(LONG_BARS)
        intent = build_intent(
            linear_plan.node("n_buy"),
            linear_plan,
            {("n_gate", "out"): pd.Series(np.nan, index=frame.index)},
            {},
            frame,
            descriptor=reg["action_buy_market"],
        )
        assert intent.triggered is False

    def test_an_unfed_signal_port_does_not_trigger(self, reg, linear_plan):
        frame = candles(LONG_BARS)
        intent = build_intent(
            linear_plan.node("n_buy"), linear_plan, {}, {}, frame,
            descriptor=reg["action_buy_market"],
        )
        assert intent.triggered is False

    def test_a_signal_at_or_below_the_threshold_does_not_trigger(
        self, reg, linear_plan
    ):
        frame = candles(LONG_BARS)
        for value in (0.0, ACTION_TRIGGER_THRESHOLD):
            intent = build_intent(
                linear_plan.node("n_buy"),
                linear_plan,
                {("n_gate", "out"): pd.Series(value, index=frame.index)},
                {},
                frame,
                descriptor=reg["action_buy_market"],
            )
            assert intent.triggered is False, value


# ---------------------------------------------------------------------------
# Requirements 20.5 / 20.6 / 20.8 - the controls on the intent path
# ---------------------------------------------------------------------------


class TestTheExistingControlsStayAuthoritative:
    def test_assert_execution_safe_runs_before_the_intent_leaves_the_runtime(
        self, linear_plan, reg, unfrozen
    ):
        """A zero quantity validates (``min=0``) and must not reach a venue. The firewall
        raises, and the raise is not swallowed: nothing is returned, so nothing is sent."""
        payload = copy.deepcopy(linear_plan.to_dict())
        payload["node_index"]["n_buy"]["params"]["quantity"] = 0.0
        zero_size = CompiledPlan.from_dict(payload)

        engine = DAGEngine(enable_event_buffer=False)
        with pytest.raises(ExecutionBlocked) as excinfo:
            engine.execute_plan(
                zero_size, candles(LONG_BARS), PlanRuntimeState(), registry=reg
            )

        assert excinfo.value.code == "NON_POSITIVE_QUANTITY"
        assert excinfo.value.node_id == "n_buy"

    def test_a_blocked_intent_is_recorded_against_its_node(
        self, linear_plan, reg, unfrozen
    ):
        """Requirement 20.5's "record the incident", through the engine's own issue log."""
        payload = copy.deepcopy(linear_plan.to_dict())
        payload["node_index"]["n_buy"]["params"]["quantity"] = 0.0
        zero_size = CompiledPlan.from_dict(payload)

        engine = DAGEngine(enable_event_buffer=False)
        with pytest.raises(ExecutionBlocked):
            engine.execute_plan(
                zero_size, candles(LONG_BARS), PlanRuntimeState(), registry=reg
            )

        recorded = engine.get_node_issues("n_buy")["n_buy"]
        assert any(issue["code"] == "NON_POSITIVE_QUANTITY" for issue in recorded)

    def test_execute_plan_returns_intents_and_places_no_order(self):
        """Requirement 20.8 keeps ``execution_guard``, ``risk_engine``,
        ``dag_risk_integration`` and the idempotency controls authoritative. This gate
        hands them a list; it is not a second path to a venue."""
        source = _executable_python(
            REPO_ROOT / "backend_app" / "backend" / "dag_engine.py"
        )
        assert "place_order" not in source
        assert "CCXTExchangeExecutor" not in source

    def test_the_authoritative_control_modules_are_untouched_by_this_task(self):
        """Named explicitly because task 8.4 is the intent path: these four are read, not
        revised, and this gate adds a refusal rather than reordering theirs."""
        for module in (
            "dag_risk_integration.py",
            "execution_guard.py",
            "risk_engine.py",
        ):
            path = REPO_ROOT / "backend_app" / "backend" / module
            if not path.exists():
                path = REPO_ROOT / "backend_app" / "core" / module
            assert path.exists(), module
            source = _executable_python(path)
            assert "execute_plan" not in source, (
                f"{module} must not have been rewired by task 8.4"
            )


class TestTheSystemFreezeStillStops:
    def test_no_intent_is_emitted_while_the_platform_is_frozen(self, linear_plan, reg):
        """``tests/conftest.py`` sets ``VYOMQUANT_MODE=safe``, so this is the default in
        this suite. ``ActionExecutor`` already consults the same gate with the same
        operation string; this gate builds its intents from the ACTION node's signal
        input, so without asking too a frozen platform would still produce them."""
        assert SafetyMonitor.check_execution_allowed("strategy_signal")

        _engine, state, intents = run(linear_plan, LONG_BARS, reg=reg)

        assert state.state_of("n_buy") == READY, (
            "a freeze stops intents, not node evaluation - the canvas still reports state"
        )
        assert intents == []


# ---------------------------------------------------------------------------
# The window contract (task 7.10 is not re-implemented here)
# ---------------------------------------------------------------------------


class TestTheWindowContract:
    def test_a_duplicate_timestamp_is_refused_not_repaired(self, linear_plan, reg):
        """The closed-bar contract admits one row per timestamp, first wins. A frame that
        did not come through it is refused here rather than deduplicated, because a repair
        on this path is an invented bar and a second ingest is the defect task 7.10
        removed."""
        frame = candles(40)
        doubled = pd.concat([frame, frame.iloc[[-1]]])

        engine = DAGEngine(enable_event_buffer=False)
        with pytest.raises(DAGExecutionError) as excinfo:
            engine.execute_plan(linear_plan, doubled, registry=reg)
        assert "duplicate" in str(excinfo.value)

    def test_an_out_of_order_frame_is_refused_not_sorted(self, linear_plan, reg):
        """Re-sorting on a live path would hide a late arrival, which the contract counts
        and drops."""
        frame = candles(40).iloc[::-1]

        engine = DAGEngine(enable_event_buffer=False)
        with pytest.raises(DAGExecutionError) as excinfo:
            engine.execute_plan(linear_plan, frame, registry=reg)
        assert "timestamp order" in str(excinfo.value)

    def test_an_empty_window_is_refused(self, linear_plan, reg):
        engine = DAGEngine(enable_event_buffer=False)
        with pytest.raises(DAGExecutionError):
            engine.execute_plan(linear_plan, pd.DataFrame(), registry=reg)

    def test_a_short_window_is_not_refused(self, linear_plan, reg):
        """``design.md``'s ``window.bars >= plan.warmup_bars OR mode = WARMING``: the
        second branch is the normal state of a deployment that has just started, so a
        short window is answered per node rather than rejected."""
        engine = DAGEngine(enable_event_buffer=False)
        assert engine.execute_plan(linear_plan, candles(3), registry=reg) == []


# ---------------------------------------------------------------------------
# Determinism and the legacy path
# ---------------------------------------------------------------------------


class TestDeterminismAndNonInterference:
    def test_two_evaluations_of_one_plan_agree(self, linear_plan, reg, unfrozen):
        _e1, s1, i1 = run(linear_plan, LONG_BARS, reg=reg)
        _e2, s2, i2 = run(linear_plan, LONG_BARS, reg=reg)

        assert s1.to_dict() == s2.to_dict()
        assert [intent.to_dict() for intent in i1] == [
            intent.to_dict() for intent in i2
        ]

    def test_one_state_object_can_be_reused_across_evaluations(
        self, linear_plan, reg, unfrozen
    ):
        """A long-lived deployment keeps one state; a re-evaluation must not read the
        previous one's readiness."""
        state = PlanRuntimeState()
        engine = DAGEngine(enable_event_buffer=False)

        engine.execute_plan(linear_plan, candles(LONG_BARS), state, registry=reg)
        assert state.state_of("n_ema") == READY

        engine.execute_plan(linear_plan, candles(8), state, registry=reg)
        assert state.state_of("n_ema") == WARMING
        assert state.bars_seen == 8

    def test_execute_dag_is_unchanged_by_this_task(self, linear_plan, reg):
        """The backtester's path. ``execute_plan`` is additive: it does not gate, alter or
        reroute the loose-dict path the golden plan pins."""
        nodes, edges = SC.plan_to_engine_graph(linear_plan, reg)
        engine = DAGEngine(enable_event_buffer=False)

        result = engine.execute_dag(nodes, edges, candles(LONG_BARS))

        assert set(result["signals"].unique()).issubset({-1, 0, 1})
        assert result["action_nodes"] == ["n_buy"]
        assert engine.runtime_state is None, (
            "execute_dag must not publish a readiness state it never computed"
        )

    def test_an_inconsistent_level_list_is_refused(self, linear_plan, reg):
        """``design.md``'s ``ASSERT all_predecessors_evaluated(level, values)``, checked
        rather than trusted: levels that disagree with the plan's own dependencies would
        evaluate a node against inputs that do not exist yet and report the result as a
        wiring problem the author does not have."""
        payload = copy.deepcopy(linear_plan.to_dict())
        payload["execution_levels"] = [list(payload["execution_order"])]
        scrambled = CompiledPlan.from_dict(payload)

        engine = DAGEngine(enable_event_buffer=False)
        with pytest.raises(DAGExecutionError) as excinfo:
            engine.execute_plan(scrambled, candles(LONG_BARS), registry=reg)
        assert "execution levels" in str(excinfo.value)

    def test_a_plan_naming_an_unpublished_block_is_refused(self, linear_plan, reg):
        """Without a descriptor this gate would be deciding readiness against no contract
        at all, which is not a weaker check but an absent one. ``plan_to_engine_graph``
        refuses the same plan for the same reason."""
        payload = copy.deepcopy(linear_plan.to_dict())
        payload["node_index"]["n_rsi"]["block_id"] = "block_that_does_not_exist"
        unknown = CompiledPlan.from_dict(payload)

        engine = DAGEngine(enable_event_buffer=False)
        with pytest.raises(DAGExecutionError) as excinfo:
            engine.execute_plan(unknown, candles(LONG_BARS), registry=reg)
        assert "block_that_does_not_exist" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Property: silence below the warmup, and a legal label always
# ---------------------------------------------------------------------------


class TestReadinessProperties:
    @settings(
        deadline=None,
        max_examples=25,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(bars=st.integers(min_value=1, max_value=200))
    def test_no_intent_below_the_plans_warmup_and_every_label_is_legal(
        self, linear_plan, reg, unfrozen, bars
    ):
        """**Property.** For any window length, every node holds one of the four published
        runtime states, and no Trade_Intent exists while the window does not exceed the
        plan's composed warmup (Requirements 20.1, 20.10).

        The generator is constrained to window lengths, which is the whole input space this
        property is about: the plan is fixed, the candles are deterministic, and the only
        free variable is how much history the runtime has seen.
        """
        _engine, state, intents = run(linear_plan, bars, reg=reg)

        assert set(state.node_states.values()).issubset(set(RUNTIME_STATES))
        if bars <= linear_plan.warmup_bars:
            assert intents == [], (
                f"{bars} bars against a composed warmup of "
                f"{linear_plan.warmup_bars} must emit nothing"
            )
            assert not state.is_ready("n_buy")
