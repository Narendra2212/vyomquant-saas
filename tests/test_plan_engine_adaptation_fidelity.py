"""
tests/test_plan_engine_adaptation_fidelity.py

The authored strategy is the strategy that executes.

Spec: strategy-builder task 2.4 (``design.md`` -> The single compiler -> Migration path for
callers) and the fidelity contract task 2.10's golden file pins. Requirements 22.3, 22.4.

THE REGRESSION THESE TESTS EXIST FOR
------------------------------------
``strategy_compiler.plan_to_engine_graph`` adapts a ``CompiledPlan`` into the loose
``(nodes, edges)`` payload ``dag_engine.DAGEngine`` consumes. It used to lift only keys
found in ``node.params``, and four kinds of authored meaning were consequently dropped
between "what the author saved" and "what ran":

1. **ACTION side.** ACTION descriptors carry ``metadata["side"]`` /
   ``metadata["order_type"]`` (``block_specs._action_spec``), never a ``params["action"]``,
   while ``dag_engine.ActionExecutor`` reads ``node.get("action", "hold")``. Every compiled
   canonical plan therefore reached the engine as ``hold`` and could never place an order,
   whichever ACTION block the author picked.
2. **Indicator window.** ``IndicatorExecutor`` reads ``params["period"]``;
   ``indicators_backend.INDICATOR_SPECS`` publishes ``window``. An authored ``window=7`` was
   ignored and the engine substituted its own default.
3. **MATH constant.** A MATH node was executed by the pass-through executor, which
   republishes ``close``, so a ``constant`` block's value was not what a downstream
   comparison saw.
4. **LOGIC operator.** Every LOGIC node arrived without an ``operator`` and was evaluated as
   ``AND``, so a comparator never compared.

Before task 2.4 the consumers were fed legacy loose dicts carrying ``action: "buy"``,
``period: 14`` and ``operator: "GT"`` as top-level keys, so this is a regression of the
canonical path and not a pre-existing engine limitation.

WHY IT WAS INVISIBLE
--------------------
``tests/conftest.py`` sets ``VYOMQUANT_MODE=safe``, so ``ActionExecutor`` returns an
all-zero series regardless of side, and *both* consumers (backtester and live loop) go
through the same adaptation - so Requirement 22.4's "both agree" held while both were
wrong. These tests therefore assert the adapted node payload directly, and exercise the
firing path in one focused test that flips the platform's own paper-trading switch through
a fixture that restores it. ``SafetyMonitor`` is never weakened: this file asserts that
safe mode blocks order-placing execution before that test and again after it.

NOTHING IS FAKED
----------------
The registry is the real assembled registry, the graphs are real canonical graphs built
from published descriptors, the plans come from the real compiler and the execution is the
real ``DAGEngine`` over real pandas.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import indicators_backend
from backend_app.backend import strategy_compiler as SC
from backend_app.backend.dag_engine import (
    DAGEngine,
    LegacyLogicExecutor,
    MarketDataExecutor,
)
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.plan import CompiledPlan
from backend_app.backend.strategy_dag.schema import (
    BlockCategory,
    EdgeSpec,
    NodeSpec,
    StrategyGraph,
)
from backend_app.core.safety_config import ExecutionFlags, SafetyMonitor

BARS = 90


# ---------------------------------------------------------------------------
# Fixtures and graph builders
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg():
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


@pytest.fixture(scope="module")
def candles():
    """A deterministic OHLCV frame: a rising leg, a falling leg, a rising leg.

    Written as an expression rather than drawn at random, so RSI moves across the band
    edges the assertions use without any seed reaching an asserted value.
    """
    closes = []
    price = 100.0
    for index in range(BARS):
        if index < 30:
            price += 1.5
        elif index < 60:
            price -= 1.25
        else:
            price += 0.75
        closes.append(round(price, 4))
    stamps = pd.date_range("2024-03-01", periods=BARS, freq="5min", name="timestamp")
    close = pd.Series(closes, index=stamps)
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": pd.Series(10.0, index=stamps),
        },
        index=stamps,
    )


def _node(reg, block_id, **params):
    return NodeSpec.create(block_id, reg[block_id].category, params=params)


def _param_value(spec):
    """A value the descriptor itself publishes for one param.

    ``default`` where it declares one, otherwise ``example`` - which every param whose
    default is deliberately absent (``quantity``, ``price``, ``trigger_price``) carries.
    Nothing is invented here, which is what lets the ACTION test iterate over *every*
    published ACTION block rather than a hand-picked three.
    """
    return spec.default if spec.default is not None else spec.example


def _required_params(descriptor):
    return {
        spec.key: _param_value(spec)
        for spec in descriptor.params
        if spec.required and _param_value(spec) is not None
    }


def gate_graph(
    reg,
    *,
    gate_block="gt",
    action_block="action_buy_market",
    window=14,
    floor=42.5,
):
    """``ohlcv_feed -> rsi -> <gate>(rsi, constant...) -> <action>``.

    The smallest graph that exercises all four dropped semantics at once: an indicator with
    an authored window, MATH constants feeding a comparison, a LOGIC gate, and an ACTION
    block whose side lives only in its descriptor's metadata.

    The gate's inputs are read from its descriptor, so the same builder serves every
    two- and three-input gate the registry publishes without restating any port name here.
    The first gate input is the indicator; each remaining one is a constant, spaced 10 apart
    so a band gate (``between``) has a band rather than a point.
    """
    gate_ports = [port.name for port in reg[gate_block].inputs]
    data = _node(
        reg,
        "ohlcv_feed",
        symbol="ETH/USDT",
        timeframe="5m",
        market_type="spot",
        mode="streaming",
    )
    rsi = _node(reg, "rsi", window=window)
    gate = _node(reg, gate_block, **_required_params(reg[gate_block]))
    action = _node(reg, action_block, **_required_params(reg[action_block]))

    constants = [
        _node(reg, "constant", value=floor + 10.0 * offset)
        for offset in range(len(gate_ports) - 1)
    ]
    edges = [
        EdgeSpec.create(data.id, "close", rsi.id, "series"),
        EdgeSpec.create(rsi.id, "value", gate.id, gate_ports[0]),
        EdgeSpec.create(gate.id, "out", action.id, "signal"),
    ]
    for port_name, constant in zip(gate_ports[1:], constants):
        edges.append(EdgeSpec.create(constant.id, "value", gate.id, port_name))

    graph = StrategyGraph(
        nodes=[data, rsi, gate, action, *constants],
        edges=edges,
    )
    ids = {
        "data": data.id,
        "rsi": rsi.id,
        "gate": gate.id,
        "action": action.id,
        "constants": [constant.id for constant in constants],
        "const": constants[0].id if constants else None,
    }
    return graph, ids


def adapt(reg, graph):
    """Compile and adapt: the real validator, the real compiler, the real adapter."""
    report = V.validate(graph, reg)
    assert report.valid, f"the graph must validate: {report.codes()}"
    plan = SC.compile_graph(graph, reg)
    nodes, edges = SC.plan_to_engine_graph(plan, reg)
    return plan, nodes, edges


def by_id(nodes):
    return {node["id"]: node for node in nodes}


def run(nodes, edges, market_data):
    return DAGEngine(enable_tracing=False, enable_event_buffer=False).execute(
        nodes=nodes, edges=edges, market_data=market_data
    )


def action_block_ids(reg):
    return [
        descriptor.block_id
        for descriptor in reg.in_category(BlockCategory.ACTION)
    ]


# ---------------------------------------------------------------------------
# 1. ACTION side
# ---------------------------------------------------------------------------


class TestActionSideSurvivesTheAdaptation:
    """Defect 1: every ACTION block reached the engine as ``hold``."""

    def test_every_published_action_block_carries_its_descriptor_side(self, reg):
        """For **every** ACTION block the registry publishes, not a sampled few.

        An entry block's ``side`` is ``buy`` or ``sell`` and must arrive as the engine's
        ``action``. An exit block declares ``side = None`` on purpose - its side is the
        inverse of the open position, resolved at execution time - and the legacy engine
        holds no position state, so it is adapted as ``hold`` while the descriptor's
        unreduced ``side`` / ``order_type`` / ``intent`` still travel on the node for a
        consumer that can resolve them.
        """
        blocks = action_block_ids(reg)
        assert blocks, "the registry publishes no ACTION blocks"

        for block_id in blocks:
            descriptor = reg[block_id]
            graph, ids = gate_graph(reg, action_block=block_id)
            _plan, nodes, _edges = adapt(reg, graph)
            node = by_id(nodes)[ids["action"]]

            expected_side = descriptor.metadata.get("side")
            assert node["side"] == expected_side, (
                f"{block_id}: adapted side {node['side']!r} is not the descriptor's "
                f"{expected_side!r}"
            )
            assert node["order_type"] == descriptor.metadata.get("order_type")
            assert node["intent"] == descriptor.metadata.get("intent")
            assert node["reduce_only_forced"] == bool(
                descriptor.metadata.get("reduce_only_forced")
            )
            if expected_side in ("buy", "sell"):
                assert node["action"] == expected_side, (
                    f"{block_id} declares side={expected_side!r} but reaches "
                    f"ActionExecutor as {node['action']!r}; the engine reads 'action'"
                )
            else:
                assert node["action"] == "hold", (
                    f"{block_id} has a position-resolved side, so the legacy engine must be "
                    f"given 'hold' rather than a guessed direction; got {node['action']!r}"
                )

    def test_the_sell_block_is_not_adapted_as_a_buy(self, reg):
        """The cheapest possible proof that the side is read rather than defaulted."""
        buy_graph, buy_ids = gate_graph(reg, action_block="action_buy_market")
        sell_graph, sell_ids = gate_graph(reg, action_block="action_sell_market")
        _p, buy_nodes, _e = adapt(reg, buy_graph)
        _p, sell_nodes, _e = adapt(reg, sell_graph)
        assert by_id(buy_nodes)[buy_ids["action"]]["action"] == "buy"
        assert by_id(sell_nodes)[sell_ids["action"]]["action"] == "sell"


# ---------------------------------------------------------------------------
# 2. Indicator window
# ---------------------------------------------------------------------------


class TestAuthoredWindowReachesTheExecutor:
    """Defect 2: the registry publishes ``window``, the executor reads ``period``."""

    def test_the_authored_window_arrives_as_period(self, reg):
        graph, ids = gate_graph(reg, window=7)
        _plan, nodes, _edges = adapt(reg, graph)
        rsi_node = by_id(nodes)[ids["rsi"]]
        assert rsi_node["params"]["window"] == 7, "the canonical key must survive"
        assert rsi_node["params"]["period"] == 7, (
            "the legacy alias must survive alongside it: dag_event_loop's "
            "StatefulIndicatorExecutor still reads params['period'], and an authored window "
            "of 7 that does not arrive there is silently replaced by that executor's own "
            "default. (dag_engine.IndicatorExecutor reads the canonical 'window' since the "
            "task 5.4 re-point, and accepts either.)"
        )

    def test_the_executor_computes_the_authored_window_not_its_default(self, reg, candles):
        """Asserted on the *output series*, not only on the payload key.

        The expectation comes from ``indicators_backend.rsi`` - the module the descriptor's
        ``runtime_ref`` names and, since task 5.4, the only RSI implementation there is.
        ``IndicatorExecutor`` used to carry a private ``_calculate_rsi`` that this test
        called; it disagreed with the published one (a simple rolling mean where
        ``indicators_backend`` uses Wilder smoothing), so asserting against it proved the
        window arrived without proving the engine and the registry agreed on the number.
        """
        graph, ids = gate_graph(reg, window=7)
        _plan, nodes, edges = adapt(reg, graph)
        produced = run(nodes, edges, candles)["node_results"][ids["rsi"]]

        expected = pd.Series(
            indicators_backend.rsi(candles["close"], 7), index=candles.index
        )
        engine_default = pd.Series(
            indicators_backend.rsi(candles["close"], 14), index=candles.index
        )

        pd.testing.assert_series_equal(
            produced, expected, check_names=False, check_dtype=False
        )
        assert not produced.equals(engine_default), (
            "RSI(7) and RSI(14) came out identical, so this assertion proves nothing"
        )


# ---------------------------------------------------------------------------
# 3. MATH constant
# ---------------------------------------------------------------------------


class TestMathConstantIsWhatDownstreamReads:
    """Defect 3: a MATH node republished ``close``."""

    def test_the_constant_node_publishes_its_value_on_every_bar(self, reg, candles):
        graph, ids = gate_graph(reg, floor=42.5)
        _plan, nodes, edges = adapt(reg, graph)
        produced = run(nodes, edges, candles)["node_results"][ids["const"]]

        assert len(produced) == len(candles)
        assert produced.eq(42.5).all(), (
            "the constant block published "
            f"{sorted(set(produced.round(4)))[:5]} rather than 42.5; the pass-through "
            "executor republishes close, which is a different strategy"
        )
        assert not produced.equals(candles["close"])

    def test_the_downstream_comparison_sees_the_value_not_close(self, reg, candles):
        graph, ids = gate_graph(reg, floor=42.5)
        _plan, nodes, edges = adapt(reg, graph)
        results = run(nodes, edges, candles)["node_results"]

        rsi = results[ids["rsi"]]
        gate = results[ids["gate"]].astype(bool)

        expected = (rsi > 42.5).fillna(False)
        # A comparator's contract: an undefined bar reports False, so the warmup region
        # never signals.
        expected[rsi.isna()] = False

        pd.testing.assert_series_equal(
            gate, expected.astype(bool), check_names=False
        )
        against_close = (rsi > candles["close"]).fillna(False).astype(bool)
        assert not gate.equals(against_close), (
            "comparing RSI against 42.5 and against close gave the same series, so this "
            "assertion proves nothing on this fixture"
        )


# ---------------------------------------------------------------------------
# 4. LOGIC gate
# ---------------------------------------------------------------------------


class TestLogicGateIsEvaluatedAsTheAuthoredGate:
    """Defect 4: every LOGIC node was evaluated as AND."""

    @pytest.mark.parametrize(
        "gate_block",
        ["gt", "lt", "gte", "lte", "eq", "neq", "cross_above", "cross_below", "between"],
    )
    def test_the_authored_gate_arrives_as_its_own_operator(self, reg, gate_block):
        graph, ids = gate_graph(reg, gate_block=gate_block)
        _plan, nodes, _edges = adapt(reg, graph)
        node = by_id(nodes)[ids["gate"]]
        assert node["operator"] == gate_block.upper(), (
            f"{gate_block} reached the engine as operator {node['operator']!r}; a LOGIC "
            "node with no operator is evaluated as AND"
        )
        assert node["runtime_ref"] == reg[gate_block].runtime_ref

    def test_each_comparator_produces_its_own_series(self, reg, candles):
        """``gt``, ``lt`` and ``between`` over the same inputs must disagree.

        Under the defect all three produced the identical AND series, which is exactly what
        "a comparator never actually compares" means.
        """
        produced = {}
        for gate_block in ("gt", "lt", "between"):
            graph, ids = gate_graph(reg, gate_block=gate_block)
            _plan, nodes, edges = adapt(reg, graph)
            series = run(nodes, edges, candles)["node_results"][ids["gate"]].astype(bool)
            assert series.any() and not series.all(), (
                f"{gate_block} is constant over this fixture "
                f"({series.value_counts().to_dict()}), so it cannot be distinguished"
            )
            produced[gate_block] = series

        assert not produced["gt"].equals(produced["lt"])
        assert not produced["gt"].equals(produced["between"])
        assert not produced["lt"].equals(produced["between"])

    def test_a_comparator_is_not_evaluated_as_and(self, reg, candles):
        graph, ids = gate_graph(reg, gate_block="gt", floor=42.5)
        _plan, nodes, edges = adapt(reg, graph)
        results = run(nodes, edges, candles)["node_results"]
        gate = results[ids["gate"]].astype(bool)

        as_and = (
            LegacyLogicExecutor()
            .execute(
                {"id": "and_probe", "operator": "AND"},
                {"a": results[ids["rsi"]], "b": results[ids["const"]]},
                candles,
            )
            .astype(bool)
        )
        assert not gate.equals(as_and), (
            "the comparator produced exactly what AND over the same inputs produces, "
            "which is the defect this test exists for"
        )

    def test_the_inputs_reach_the_gate_in_declared_port_order(self, reg):
        """``left``/``right`` is decided by port order, not by edge-list order.

        ``DAGEngine.get_node_inputs`` walks the edge list, so a target's edge order is its
        operand order. ``plan.inbound`` is keyed by port and iterates in edge-sort (or, after
        a JSON round trip, alphabetical) order, so the adapter has to impose the declared
        order or ``a > b`` can silently become ``b > a``.
        """
        graph, ids = gate_graph(reg, gate_block="between")
        _plan, nodes, edges = adapt(reg, graph)
        gate_edges = [edge for edge in edges if edge["target"] == ids["gate"]]
        assert [edge["target_port"] for edge in gate_edges] == ["value", "lower", "upper"]
        assert by_id(nodes)[ids["gate"]]["input_order"] == [
            ids["rsi"],
            ids["constants"][0],
            ids["constants"][1],
        ]

    def test_port_order_survives_the_persisted_json_round_trip(self, reg, candles):
        """A version is executed from stored bytes, so the round trip must not reorder."""
        graph, ids = gate_graph(reg, gate_block="between")
        plan = SC.compile_graph(graph, reg)
        reloaded = CompiledPlan.from_json(plan.to_json())

        direct = SC.plan_to_engine_graph(plan, reg)
        from_bytes = SC.plan_to_engine_graph(reloaded, reg)
        assert direct == from_bytes

        first = run(*direct, candles)["node_results"][ids["gate"]]
        second = run(*from_bytes, candles)["node_results"][ids["gate"]]
        pd.testing.assert_series_equal(first, second)


# ---------------------------------------------------------------------------
# 5. The gate actually fires
# ---------------------------------------------------------------------------


@pytest.fixture
def execution_permitted():
    """Permit strategy-signal execution for one test, then restore every flag.

    ``SafetyMonitor.check_execution_allowed`` is NOT patched, weakened or bypassed. This
    flips the platform's own paper-trading switch (``ExecutionFlags.enable_paper_trading``,
    which itself refuses to run unless ``VYOMQUANT_MODE=paper``) and restores the previous
    environment variable and the previous value of every flag afterwards, so no other test
    can observe a permissive state.
    """
    flag_names = (
        "LIVE_TRADING_ENABLED",
        "PAPER_TRADING_ENABLED",
        "MANUAL_ORDER_EXECUTION",
        "STRATEGY_SIGNAL_EXECUTION",
        "BACKTEST_CAN_SUBMIT_ORDERS",
        "ALLOW_ML_INFERENCE",
    )
    previous_mode = os.environ.get("VYOMQUANT_MODE")
    previous_flags = {name: getattr(ExecutionFlags, name) for name in flag_names}
    os.environ["VYOMQUANT_MODE"] = "paper"
    try:
        ExecutionFlags.enable_paper_trading()
        yield
    finally:
        for name, value in previous_flags.items():
            setattr(ExecutionFlags, name, value)
        if previous_mode is None:
            os.environ.pop("VYOMQUANT_MODE", None)
        else:
            os.environ["VYOMQUANT_MODE"] = previous_mode


class TestTheGateFires:
    """The end the fix exists for: a true comparison becomes a real buy intent."""

    def test_safe_mode_blocks_the_order_placing_path_but_the_side_still_arrives(
        self, reg, candles
    ):
        assert SafetyMonitor.check_execution_allowed("strategy_signal"), (
            "the suite must run with order-placing execution blocked "
            "(tests/conftest.py sets VYOMQUANT_MODE=safe)"
        )
        graph, ids = gate_graph(reg, floor=42.5)
        _plan, nodes, edges = adapt(reg, graph)
        result = run(nodes, edges, candles)
        assert by_id(nodes)[ids["action"]]["action"] == "buy", (
            "the side must arrive even when execution is blocked; blocking is the "
            "executor's decision, not the adapter's"
        )
        assert (result["signals"] == 0).all(), (
            "safe mode must still produce no intent; if this fails a financial-safety "
            "control has been weakened"
        )

    def test_a_true_comparison_produces_a_real_buy_intent(
        self, reg, candles, execution_permitted
    ):
        """With execution permitted, the authored buy fires exactly where the gate is true."""
        assert SafetyMonitor.check_execution_allowed("strategy_signal") is None, (
            "the fixture failed to permit strategy-signal execution"
        )
        graph, ids = gate_graph(reg, floor=42.5)
        _plan, nodes, edges = adapt(reg, graph)
        result = run(nodes, edges, candles)

        gate = result["node_results"][ids["gate"]].astype(bool)
        action = result["node_results"][ids["action"]]
        signals = result["signals"]

        assert gate.any(), "the fixture must make the comparison true somewhere"
        assert (action[gate] == 1).all(), "a true bar must buy"
        assert (action[~gate] == 0).all(), "a false bar must not trade"
        assert (signals[gate] == 1).all()
        assert int((signals == 1).sum()) == int(gate.sum())

        from backend_app.backend.dag_event_loop import DAGEventLoop

        loop = DAGEventLoop(dag_nodes=nodes, dag_edges=edges, symbols=["ETH/USDT"])
        labelled = {loop._signal_to_action(float(value)) for value in signals[gate]}
        assert labelled == {"buy"}, (
            f"the live loop labels the firing bars {labelled} rather than buy"
        )

    def test_a_sell_block_fires_the_other_way(self, reg, candles, execution_permitted):
        graph, ids = gate_graph(reg, action_block="action_sell_market", floor=42.5)
        _plan, nodes, edges = adapt(reg, graph)
        result = run(nodes, edges, candles)
        gate = result["node_results"][ids["gate"]].astype(bool)
        assert (result["signals"][gate] == -1).all(), (
            "an authored sell must reach the engine as a sell, not as a buy or a hold"
        )

    def test_safe_mode_is_restored_after_the_permitted_tests(self):
        """The fixture's restore path, asserted rather than assumed."""
        assert SafetyMonitor.check_execution_allowed("strategy_signal"), (
            "order-placing execution is still permitted after the focused tests; the "
            "restoring fixture failed"
        )
        assert ExecutionFlags.STRATEGY_SIGNAL_EXECUTION is False
        assert ExecutionFlags.PAPER_TRADING_ENABLED is False
        assert os.environ.get("VYOMQUANT_MODE") == "safe"


# ---------------------------------------------------------------------------
# 6. Backward compatibility with the legacy loose-dict callers
# ---------------------------------------------------------------------------


class TestLegacyLooseDictsAreUnchanged:
    """``dag_engine`` is still fed hand-built dicts; none of them may change behaviour."""

    def test_a_legacy_math_node_still_passes_through(self, candles):
        nodes = [
            {"id": "in", "type": "market_data"},
            {"id": "m", "type": "math"},
        ]
        edges = [{"id": "e", "source": "in", "target": "m"}]
        produced = run(nodes, edges, candles)["node_results"]["m"]
        pd.testing.assert_series_equal(
            produced,
            MarketDataExecutor().execute(nodes[1], {}, candles),
            check_names=False,
        )

    def test_a_legacy_logic_node_still_uses_its_uppercase_operator(self, candles):
        """A loose dict carries ``operator`` and no ``runtime_ref``: the legacy path."""
        left = pd.Series(np.linspace(0.0, 10.0, len(candles)), index=candles.index)
        right = pd.Series(5.0, index=candles.index)
        for operator, expected in (
            ("GT", left > right),
            ("LT", left < right),
            ("AND", pd.concat([left, right], axis=1).all(axis=1)),
        ):
            produced = LegacyLogicExecutor().execute(
                {"id": "legacy", "operator": operator}, {"a": left, "b": right}, candles
            )
            pd.testing.assert_series_equal(
                produced.astype(bool), expected.astype(bool), check_names=False
            )

    def test_the_engine_dispatches_a_legacy_logic_node_to_the_legacy_executor(self, candles):
        """No ``runtime_ref`` on the node means the kernel path is never taken."""
        nodes = [
            {"id": "d", "type": "market_data"},
            {"id": "g", "type": "logic", "operator": "AND"},
        ]
        edges = [{"id": "e", "source": "d", "target": "g"}]
        produced = run(nodes, edges, candles)["node_results"]["g"]
        expected = pd.concat([candles["close"]], axis=1).all(axis=1)
        pd.testing.assert_series_equal(
            produced.astype(bool), expected.astype(bool), check_names=False
        )

    def test_a_legacy_action_node_still_reads_its_own_action_key(self, candles):
        nodes = [
            {"id": "d", "type": "market_data"},
            {"id": "a", "type": "action", "action": "buy"},
        ]
        edges = [{"id": "e", "source": "d", "target": "a"}]
        produced = run(nodes, edges, candles)["node_results"]["a"]
        assert set(produced.unique()) <= {0, 1}


# ---------------------------------------------------------------------------
# 7. The adaptation refuses to guess, and carries no order-placing reference
# ---------------------------------------------------------------------------


class TestTheAdapterRefusesToGuess:
    def test_a_plan_naming_an_unpublished_block_is_refused(self, reg):
        """A persisted plan is read back without re-validation, so this is reachable."""
        graph, ids = gate_graph(reg)
        payload = SC.compile_graph(graph, reg).to_dict()
        payload["node_index"][ids["rsi"]]["block_id"] = "no_such_indicator"
        mutated = CompiledPlan.from_dict(payload)

        with pytest.raises(SC.CompilerError) as excinfo:
            SC.plan_to_engine_graph(mutated, reg)
        assert "no_such_indicator" in str(excinfo.value)

    def test_the_adapter_resolves_the_registry_itself_when_none_is_passed(self, reg):
        """Existing callers pass no registry; they must keep working."""
        graph, _ids = gate_graph(reg)
        plan = SC.compile_graph(graph, reg)
        assert SC.plan_to_engine_graph(plan, reg) == SC.plan_to_engine_graph(plan)

    def test_no_action_node_carries_an_order_placing_runtime_reference(self, reg):
        """A node dict must never be a loaded gun: orders go through the guarded path."""
        for block_id in action_block_ids(reg):
            graph, ids = gate_graph(reg, action_block=block_id)
            _plan, nodes, _edges = adapt(reg, graph)
            node = by_id(nodes)[ids["action"]]
            assert "runtime_ref" not in node, (
                f"{block_id} carries a runtime_ref into the engine payload; "
                "CCXTExchangeExecutor.place_order must not be reachable from a node dict"
            )

    def test_only_kernel_backed_categories_carry_a_runtime_reference(self, reg):
        graph, _ids = gate_graph(reg)
        _plan, nodes, _edges = adapt(reg, graph)
        for node in nodes:
            ref = node.get("runtime_ref")
            if ref is None:
                continue
            assert node["category"] in ("MATH", "LOGIC")
            assert ref.startswith("block_specs."), (
                f"node {node['id']} carries runtime_ref {ref!r}; only the pure kernels in "
                "block_specs may be called from the engine"
            )
            assert isinstance(node["input_order"], list)
