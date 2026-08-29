"""
tests/test_math_executor_firewall.py

An arithmetic edge case makes a bar undefined. It never makes an order.

Spec: strategy-builder task 5.3 (``design.md`` -> Math blocks).
Requirements 20.3, 20.4, 20.5, 20.6.

THE TWO BOUNDARIES THESE TESTS PIN
----------------------------------
The firewall is two rules that point in opposite directions, and the distinction is the
whole design:

1. **Within a series, NaN is the answer and it propagates.** A bar with no defined value
   is NaN - a warming indicator, a division by a zero denominator, ``sqrt`` of a negative
   bar - so the readiness gate can tell "no value yet" apart from "the number zero".
   Writing zero instead would be inventing a price.
2. **At the action boundary, NaN stops.** ``assert_execution_safe`` is the last gate an
   intent passes and a non-finite or non-positive order field does not pass it.

``±Inf`` exists at neither boundary. Unlike NaN, an infinity survives comparison silently:
``inf > threshold`` is True, so an overflowed number *trades*, at a size no exchange should
ever be asked for. So an undefined result is NaN, always - and every condition that
produced one is recorded against the node that produced it, because a silent NaN is a bar
an author cannot explain.

NOTHING IS FAKED
----------------
The registry is the real assembled registry, the graphs are real canonical graphs of
published descriptors, the plans come from the real compiler through the real
``plan_to_engine_graph``, execution is the real ``DAGEngine`` over real pandas, and every
expected value is produced by calling the block's own published kernel - never by
restating a formula here. ``assert_execution_safe`` is called directly, because it is
called directly on the intent path.

WHAT THIS FILE DOES NOT CLAIM
-----------------------------
``assert_execution_safe`` *adds* a check. ``execution_guard.py``, ``risk_engine.py`` and
``dag_risk_integration.py`` stay authoritative on the intent path and still apply every
limit they applied before; the last test class asserts only that this gate refuses what
they cannot see - a value that is not a number at all, which compares False against every
limit and so passes a "refuse if quantity > max" guard untouched.
"""

import inspect
import math
import os
import sys
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_compiler as SC
from backend_app.backend.dag_engine import (DAGEngine, DAGExecutionError,
                                            ExecutionBlocked, MathExecutor,
                                            NodeIssueLog, PortInputs,
                                            assert_execution_safe,
                                            safe_math_apply)
from backend_app.backend.strategy_dag import block_specs as BS
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.schema import (EdgeSpec, NodeSpec,
                                                     StrategyGraph)
from backend_app.core.global_safety import \
    ExecutionBlocked as PlatformExecutionBlocked

BARS = 24

#: ``block_specs.EPSILON`` is the magnitude below which the firewall treats a denominator
#: as zero. Read from the module rather than restated, so the tests move with it.
EPSILON = BS.EPSILON


# ---------------------------------------------------------------------------
# Fixtures and builders
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg():
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


def frame(close_values, high_values=None):
    """An OHLCV frame whose ``close`` is exactly ``close_values``.

    The close column is what every math test below feeds into its block, so it carries
    the adversarial bars (a NaN, a zero, a negative, an extreme). ``high`` is separately
    settable because two *different* columns from one DATA node is how the port-addressing
    tests distinguish ``high - low`` from ``low - low``.
    """
    stamps = pd.date_range("2024-01-01", periods=len(close_values), freq="5min", name="timestamp")
    close = pd.Series(np.asarray(close_values, dtype=float), index=stamps)
    high = close + 1.5 if high_values is None else pd.Series(
        np.asarray(high_values, dtype=float), index=stamps
    )
    return pd.DataFrame(
        {
            "open": close - 0.25,
            "high": high,
            "low": close - 0.5,
            "close": close,
            "volume": pd.Series(np.linspace(1_000.0, 9_000.0, len(close)), index=stamps),
        },
        index=stamps,
    )


def _node(reg, block_id, **params):
    return NodeSpec.create(block_id, reg[block_id].category, params=params)


def _required(descriptor):
    """Required params filled from what the descriptor itself publishes."""
    values = {}
    for spec in descriptor.params:
        value = spec.default if spec.default is not None else spec.example
        if spec.required and value is not None:
            values[spec.key] = value
    return values


def math_pipeline(reg, math_nodes, wiring, sink_id):
    """``ohlcv_feed -> <math nodes> -> gt -> action``, validated and compiled.

    MATH's declared successors do not include ACTION, so the ``gt -> action`` tail is
    scaffolding that makes the graph legal (Requirement 7.6 needs a DATA node and an
    ACTION node); no assertion in this file is about it. ``wiring`` receives the DATA node
    and returns every edge among the math nodes, so each test owns - and states - which
    DATA port it wired.
    """
    data = _node(
        reg,
        "ohlcv_feed",
        symbol="ETH/USDT",
        timeframe="5m",
        market_type="spot",
        mode="streaming",
    )
    gate = _node(reg, "gt", **_required(reg["gt"]))
    threshold = _node(reg, "constant", value=0.5)
    action = _node(reg, "action_buy_market", **_required(reg["action_buy_market"]))

    graph = StrategyGraph(
        nodes=[data, *math_nodes, gate, threshold, action],
        edges=[
            *wiring(data),
            EdgeSpec.create(sink_id, "out", gate.id, "left"),
            EdgeSpec.create(threshold.id, "value", gate.id, "right"),
            EdgeSpec.create(gate.id, "out", action.id, "signal"),
        ],
    )
    report = V.validate(graph, reg)
    assert report.valid, f"the graph must validate: {report.codes()}"
    return graph, data


def run(reg, market_data, math_nodes, wiring, sink_id):
    """Compile, adapt and execute: the real validator, compiler, adapter and engine."""
    graph, _data = math_pipeline(reg, math_nodes, wiring, sink_id)
    plan = SC.compile_graph(graph, reg)
    nodes, edges = SC.plan_to_engine_graph(plan, reg)
    engine = DAGEngine(enable_event_buffer=False)
    engine.execute_dag(nodes, edges, market_data)
    return engine, plan


def produced(engine, node_id):
    """One math node's output port as a float array."""
    return np.asarray(engine.node_outputs[(node_id, "out")], dtype=float)


def codes(engine, node_id):
    return [issue["code"] for issue in engine.get_node_issues(node_id)[node_id]]


def assert_no_infinity(values, label=""):
    """Requirement 20.3: finite values or NaN, never an infinity."""
    array = np.asarray(values, dtype=float)
    assert not np.isinf(array).any(), (
        f"{label or 'result'} holds an infinity, which compares True against a "
        f"threshold and would trade: {array[np.isinf(array)][:5]}"
    )


# ---------------------------------------------------------------------------
# Requirement 20.3 / 20.4 - NaN propagates within a series
# ---------------------------------------------------------------------------


class TestNanPropagatesWithinTheSeries:
    def test_an_undefined_bar_propagates_and_leaves_the_other_bars_untouched(
        self, reg
    ):
        """One NaN close is one NaN result - not a poisoned series, not a filled zero.

        Filling it would hand the readiness gate a number for a bar that has none, and a
        gate cannot refuse a value it was told is real.
        """
        closes = np.linspace(100.0, 140.0, BARS)
        closes[5] = np.nan
        market_data = frame(closes)

        adder = _node(reg, "add")
        offset = _node(reg, "constant", value=10.0)
        engine, _plan = run(
            reg,
            market_data,
            [adder, offset],
            lambda data: [
                EdgeSpec.create(data.id, "close", adder.id, "a"),
                EdgeSpec.create(offset.id, "value", adder.id, "b"),
            ],
            adder.id,
        )

        out = produced(engine, adder.id)
        assert math.isnan(out[5]), "the undefined bar did not stay undefined"
        assert np.isnan(out).sum() == 1, "NaN spread beyond the bar that was undefined"
        np.testing.assert_allclose(
            out, BS.math_add(market_data["close"], 10.0), equal_nan=True
        )
        assert_no_infinity(out, "add")

    def test_a_series_with_no_undefined_bar_produces_none(self, reg):
        """The accepting case: nothing is NaN'd defensively."""
        market_data = frame(np.linspace(100.0, 140.0, BARS))

        adder = _node(reg, "add")
        offset = _node(reg, "constant", value=10.0)
        engine, _plan = run(
            reg,
            market_data,
            [adder, offset],
            lambda data: [
                EdgeSpec.create(data.id, "close", adder.id, "a"),
                EdgeSpec.create(offset.id, "value", adder.id, "b"),
            ],
            adder.id,
        )

        out = produced(engine, adder.id)
        assert np.isfinite(out).all()
        assert engine.get_node_issues(adder.id)[adder.id] == []


# ---------------------------------------------------------------------------
# Requirement 20.4 - division by a zero-valued denominator
# ---------------------------------------------------------------------------


class TestDivisionByZero:
    def _divide_by(self, reg, market_data, denominator_value):
        divider = _node(reg, "divide")
        denominator = _node(reg, "constant", value=denominator_value)
        engine, _plan = run(
            reg,
            market_data,
            [divider, denominator],
            lambda data: [
                EdgeSpec.create(data.id, "close", divider.id, "numerator"),
                EdgeSpec.create(denominator.id, "value", divider.id, "denominator"),
            ],
            divider.id,
        )
        return engine, divider

    def test_a_zero_denominator_is_nan_on_every_bar_and_the_condition_is_recorded(
        self, reg
    ):
        market_data = frame(np.linspace(100.0, 140.0, BARS))
        engine, divider = self._divide_by(reg, market_data, 0.0)

        out = produced(engine, divider.id)
        assert np.isnan(out).all(), "a zero denominator produced a number"
        assert_no_infinity(out, "divide")

        recorded = engine.get_node_issues(divider.id)[divider.id]
        assert [issue["code"] for issue in recorded] == ["DIVISION_BY_ZERO"]
        assert recorded[0]["node_id"] == divider.id
        assert recorded[0]["bars_affected"] == BARS
        assert recorded[0]["bar"] == 0

    def test_a_denominator_below_epsilon_is_treated_as_zero(self, reg):
        """"Near zero" is the rule, not "exactly zero".

        ``close / 1e-15`` is finite in IEEE arithmetic, so nothing would raise - it would
        just produce a number seventeen orders of magnitude larger than the author's
        prices and size an order from it.
        """
        market_data = frame(np.linspace(100.0, 140.0, BARS))
        engine, divider = self._divide_by(reg, market_data, EPSILON / 1_000.0)

        out = produced(engine, divider.id)
        assert np.isnan(out).all()
        assert codes(engine, divider.id) == ["DIVISION_BY_ZERO"]

    def test_a_denominator_above_epsilon_divides_normally(self, reg):
        """The accepting case: a small but meaningful divisor is still arithmetic."""
        market_data = frame(np.linspace(100.0, 140.0, BARS))
        engine, divider = self._divide_by(reg, market_data, 1e-6)

        out = produced(engine, divider.id)
        assert np.isfinite(out).all()
        np.testing.assert_allclose(out, BS.math_divide(market_data["close"], 1e-6))
        assert engine.get_node_issues(divider.id)[divider.id] == []

    def test_a_zero_divisor_on_modulo_is_nan_and_recorded(self, reg):
        market_data = frame(np.linspace(100.0, 140.0, BARS))
        mod = _node(reg, "modulo")
        divisor = _node(reg, "constant", value=0.0)
        engine, _plan = run(
            reg,
            market_data,
            [mod, divisor],
            lambda data: [
                EdgeSpec.create(data.id, "close", mod.id, "a"),
                EdgeSpec.create(divisor.id, "value", mod.id, "b"),
            ],
            mod.id,
        )

        assert np.isnan(produced(engine, mod.id)).all()
        assert codes(engine, mod.id) == ["MODULO_BY_ZERO"]


# ---------------------------------------------------------------------------
# Requirement 20.4 - sqrt(<0), log(<=0), overflow
# ---------------------------------------------------------------------------


class TestUndefinedAndOverflowingOperations:
    def test_a_negative_bar_under_sqrt_is_nan_and_the_positive_bars_are_computed(
        self, reg
    ):
        """Rejecting and accepting bars in one series: the firewall is per bar.

        A blanket refusal would take the whole series out over one bad bar; an exception
        would take the run out. Neither is what an author wants from a block that is
        perfectly well defined on the other twenty-three bars.
        """
        closes = np.linspace(-4.0, 15.0, BARS)
        market_data = frame(closes)
        root = _node(reg, "sqrt")
        engine, _plan = run(
            reg,
            market_data,
            [root],
            lambda data: [EdgeSpec.create(data.id, "close", root.id, "a")],
            root.id,
        )

        out = produced(engine, root.id)
        negative = closes < 0
        assert np.isnan(out[negative]).all(), "a negative bar produced a root"
        assert np.isfinite(out[~negative]).all(), "a valid bar was NaN'd too"
        np.testing.assert_allclose(
            out, BS.math_sqrt(market_data["close"]), equal_nan=True
        )
        assert_no_infinity(out, "sqrt")

        recorded = engine.get_node_issues(root.id)[root.id]
        assert [issue["code"] for issue in recorded] == ["NEGATIVE_ROOT"]
        assert recorded[0]["bars_affected"] == int(negative.sum())

    def test_a_non_positive_bar_under_log_is_nan_and_recorded(self, reg):
        """Zero counts: ``log(0)`` is ``-Inf`` in IEEE arithmetic, and ``-Inf`` trades."""
        closes = np.linspace(-2.0, 20.0, BARS)
        closes[7] = 0.0
        market_data = frame(closes)
        logarithm = _node(reg, "log", base="e")
        engine, _plan = run(
            reg,
            market_data,
            [logarithm],
            lambda data: [EdgeSpec.create(data.id, "close", logarithm.id, "a")],
            logarithm.id,
        )

        out = produced(engine, logarithm.id)
        non_positive = closes <= 0
        assert np.isnan(out[non_positive]).all()
        assert np.isfinite(out[~non_positive]).all()
        assert_no_infinity(out, "log")
        np.testing.assert_allclose(
            out, BS.math_log(market_data["close"], base="e"), equal_nan=True
        )

        recorded = engine.get_node_issues(logarithm.id)[logarithm.id]
        assert [issue["code"] for issue in recorded] == ["NON_POSITIVE_LOG"]
        assert recorded[0]["bars_affected"] == int(non_positive.sum())

    def test_an_overflowing_exp_is_nan_rather_than_infinity_and_is_recorded(self, reg):
        """``exp(800)`` overflows to ``+Inf``; the bar is undefined, so it is NaN.

        Recording it matters as much as NaN'ing it: Requirement 20.4 names "an arithmetic
        overflow" alongside the three undefined operations, and an author staring at an
        empty bar needs to be told the number left the representable range rather than
        left to guess.
        """
        closes = np.linspace(1.0, 5.0, BARS)
        highs = closes.copy()
        highs[3] = 800.0
        market_data = frame(closes, high_values=highs)

        exponential = _node(reg, "exp")
        engine, _plan = run(
            reg,
            market_data,
            [exponential],
            lambda data: [EdgeSpec.create(data.id, "high", exponential.id, "a")],
            exponential.id,
        )

        out = produced(engine, exponential.id)
        assert math.isnan(out[3]), "the overflowed bar was not NaN'd"
        assert np.isfinite(np.delete(out, 3)).all()
        assert_no_infinity(out, "exp")
        assert codes(engine, exponential.id) == ["ARITHMETIC_OVERFLOW"]

    def test_a_non_overflowing_exp_is_computed(self, reg):
        """The accepting case."""
        market_data = frame(np.linspace(1.0, 5.0, BARS))
        exponential = _node(reg, "exp")
        engine, _plan = run(
            reg,
            market_data,
            [exponential],
            lambda data: [EdgeSpec.create(data.id, "close", exponential.id, "a")],
            exponential.id,
        )

        out = produced(engine, exponential.id)
        assert np.isfinite(out).all()
        np.testing.assert_allclose(out, BS.math_exp(market_data["close"]))
        assert engine.get_node_issues(exponential.id)[exponential.id] == []

    def test_an_overflowing_product_is_nan_rather_than_infinity_and_is_recorded(
        self, reg
    ):
        """Overflow is not an ``exp`` speciality - any op can leave the float range."""
        closes = np.full(BARS, 1e300)
        market_data = frame(closes, high_values=closes)

        product = _node(reg, "multiply")
        engine, _plan = run(
            reg,
            market_data,
            [product],
            lambda data: [
                EdgeSpec.create(data.id, "close", product.id, "a"),
                EdgeSpec.create(data.id, "high", product.id, "b"),
            ],
            product.id,
        )

        out = produced(engine, product.id)
        assert np.isnan(out).all()
        assert_no_infinity(out, "multiply")
        assert codes(engine, product.id) == ["ARITHMETIC_OVERFLOW"]


# ---------------------------------------------------------------------------
# Requirement 20.4 - the condition is recorded against the node
# ---------------------------------------------------------------------------


class TestConditionsAreRecordedAgainstTheProducingNode:
    def _two_dividers(self, reg, market_data):
        broken = _node(reg, "divide")
        healthy = _node(reg, "divide")
        zero = _node(reg, "constant", value=0.0)
        two = _node(reg, "constant", value=2.0)
        engine, _plan = run(
            reg,
            market_data,
            [broken, healthy, zero, two],
            lambda data: [
                EdgeSpec.create(data.id, "close", broken.id, "numerator"),
                EdgeSpec.create(zero.id, "value", broken.id, "denominator"),
                EdgeSpec.create(broken.id, "out", healthy.id, "numerator"),
                EdgeSpec.create(two.id, "value", healthy.id, "denominator"),
            ],
            healthy.id,
        )
        return engine, broken, healthy

    def test_only_the_node_that_observed_the_condition_carries_it(self, reg):
        """Two identical blocks, one bad denominator: the log names which one.

        A run-level counter would say "something divided by zero somewhere", which is
        exactly the diagnosis an author cannot act on.
        """
        market_data = frame(np.linspace(100.0, 140.0, BARS))
        engine, broken, healthy = self._two_dividers(reg, market_data)

        recorded = engine.get_node_issues()
        assert broken.id in recorded
        assert healthy.id not in recorded, (
            "the downstream divider recorded a condition it never observed"
        )
        assert [issue["code"] for issue in recorded[broken.id]] == ["DIVISION_BY_ZERO"]

    def test_a_downstream_node_inherits_the_nan_but_not_the_condition(self, reg):
        """NaN flows downstream; the *explanation* stays where it was true."""
        market_data = frame(np.linspace(100.0, 140.0, BARS))
        engine, _broken, healthy = self._two_dividers(reg, market_data)

        assert np.isnan(produced(engine, healthy.id)).all()
        assert codes(engine, healthy.id) == []

    def test_the_log_is_cleared_between_runs(self, reg):
        """A second run must not show the first run's conditions."""
        market_data = frame(np.linspace(100.0, 140.0, BARS))
        divider = _node(reg, "divide")
        zero = _node(reg, "constant", value=0.0)
        graph, data = math_pipeline(
            reg,
            [divider, zero],
            lambda data: [
                EdgeSpec.create(data.id, "close", divider.id, "numerator"),
                EdgeSpec.create(zero.id, "value", divider.id, "denominator"),
            ],
            divider.id,
        )
        plan = SC.compile_graph(graph, reg)
        nodes, edges = SC.plan_to_engine_graph(plan, reg)

        engine = DAGEngine(enable_event_buffer=False)
        engine.execute_dag(nodes, edges, market_data)
        first = len(engine.node_issues)
        engine.execute_dag(nodes, edges, market_data)

        assert first == 1
        assert len(engine.node_issues) == 1, "conditions accumulated across runs"

    def test_the_log_object_is_shared_with_the_executors_not_replaced(self, reg):
        """The executors hold a reference, so a fresh log per run would be written into
        by nobody. ``clear()`` is the contract, and this is what depends on it."""
        engine = DAGEngine(enable_event_buffer=False)
        log = engine.node_issues
        engine.execute_dag(
            [{"id": "d", "type": "input"}],
            [],
            frame(np.linspace(1.0, 2.0, BARS)),
        )
        assert engine.node_issues is log
        assert engine.executors["math"].issues is log


# ---------------------------------------------------------------------------
# Operands are bound by input port name, not by position (task 5.2 / 5.3)
# ---------------------------------------------------------------------------


class TestOperandsAreBoundByPortName:
    def test_two_ports_fed_by_one_upstream_node_are_two_operands(self, reg):
        """``high -> subtract.a`` and ``low -> subtract.b`` is a range, not zero.

        The id-keyed view collapses both edges onto one entry for the DATA node, so
        positional resolution hands the kernel a single operand and ``subtract`` computes
        ``low - low``: a plausible near-zero series, from the wrong data, with nothing
        raised. This is the defect class port addressing closes.
        """
        market_data = frame(np.linspace(100.0, 140.0, BARS))
        difference = _node(reg, "subtract")
        engine, _plan = run(
            reg,
            market_data,
            [difference],
            lambda data: [
                EdgeSpec.create(data.id, "high", difference.id, "a"),
                EdgeSpec.create(data.id, "low", difference.id, "b"),
            ],
            difference.id,
        )

        out = produced(engine, difference.id)
        np.testing.assert_allclose(
            out, BS.math_subtract(market_data["high"], market_data["low"])
        )
        assert not np.allclose(out, 0.0), (
            "the block computed low - low, so both edges collapsed onto one operand"
        )

    def test_a_required_port_with_no_connection_is_named(self, reg):
        """A missing operand reports the *port*, not a TypeError about an argument."""
        market_data = frame(np.linspace(100.0, 140.0, BARS))
        executor = MathExecutor(fallback=None)
        node = {
            "id": "n_div",
            "type": "math",
            "block_id": "divide",
            "runtime_ref": reg["divide"].runtime_ref,
            "runtime_params": {},
        }
        inputs = PortInputs({"n_up": market_data["close"]}, {"numerator": ["n_up"]})

        with pytest.raises(DAGExecutionError) as excinfo:
            executor.execute(node, inputs, market_data)
        message = str(excinfo.value)
        assert "n_div" in message and "denominator" in message

    def test_a_non_variadic_port_fed_twice_is_refused(self, reg):
        """``divide`` has one numerator. Silently using one of two would be a guess."""
        market_data = frame(np.linspace(100.0, 140.0, BARS))
        executor = MathExecutor(fallback=None)
        node = {
            "id": "n_div",
            "type": "math",
            "block_id": "divide",
            "runtime_ref": reg["divide"].runtime_ref,
            "runtime_params": {},
        }
        inputs = PortInputs(
            {"n_a": market_data["close"], "n_b": market_data["high"]},
            {"numerator": ["n_a", "n_b"], "denominator": ["n_a"]},
        )

        with pytest.raises(DAGExecutionError) as excinfo:
            executor.execute(node, inputs, market_data)
        assert "numerator" in str(excinfo.value)

    def test_a_payload_with_no_port_addressed_edges_keeps_the_positional_path(
        self, reg
    ):
        """A hand-built payload carrying ``input_order`` and no ports still runs.

        Port addressing is additive: a caller feeding this engine a loose dict must not
        acquire a new failure mode just because the canonical path grew a better one.
        """
        market_data = frame(np.linspace(100.0, 140.0, BARS))
        executor = MathExecutor(fallback=None)
        node = {
            "id": "n_sub",
            "type": "math",
            "block_id": "subtract",
            "runtime_ref": reg["subtract"].runtime_ref,
            "runtime_params": {},
            "input_order": ["n_a", "n_b"],
        }
        inputs = {"n_a": market_data["high"], "n_b": market_data["low"]}

        out = executor.execute(node, inputs, market_data)
        np.testing.assert_allclose(
            np.asarray(out, dtype=float),
            BS.math_subtract(market_data["high"], market_data["low"]),
        )

    def test_declared_input_port_order_is_the_kernels_parameter_order(self, reg):
        """The claim ``BlockKernelExecutor`` relies on, asserted rather than assumed.

        The executor binds operands by walking ``descriptor.inputs`` in declaration order
        and passes them positionally. If a descriptor ever declared ``[denominator,
        numerator]`` while the kernel took ``(numerator, denominator)``, every division in
        the platform would silently invert. Only the checkable half of the contract is
        checkable here - names, in order - which is exactly the half that catches that.
        """
        for descriptor in reg.blocks():
            if descriptor.category.value not in ("MATH", "LOGIC"):
                continue
            kernel = descriptor.resolve_runtime()
            parameters = list(inspect.signature(kernel).parameters.values())
            variadic_kernel = any(
                parameter.kind is parameter.VAR_POSITIONAL for parameter in parameters
            )
            param_keys = {spec.key for spec in descriptor.params}
            positional = [
                parameter.name
                for parameter in parameters
                if parameter.kind is parameter.POSITIONAL_OR_KEYWORD
                and parameter.name not in ("node_id", "issues")
                and parameter.name not in param_keys
            ]
            declared = [port.name for port in descriptor.inputs]

            if variadic_kernel:
                assert any(port.variadic for port in descriptor.inputs), (
                    f"{descriptor.block_id}'s kernel takes *operands but the block "
                    f"declares no variadic port, so the extra operands are unreachable"
                )
                continue
            assert positional == declared, (
                f"{descriptor.block_id} declares ports {declared} but its kernel takes "
                f"{positional}; operands would be bound to the wrong parameters"
            )


# ---------------------------------------------------------------------------
# Task 5.0's variadic fan-in, through the firewall
# ---------------------------------------------------------------------------


class TestVariadicOperandsReachTheFirewall:
    """A variadic port fed N times is N operands, and the firewall sees all N.

    ``CompiledPlan.inbound`` used to keep one edge per port, so ``add`` returned one
    addend. The plan carries every edge now (task 5.0); what these tests add is that the
    *numeric* rules hold across the whole operand list - a NaN on the third addend is
    still an undefined bar, not a dropped one.
    """

    def _three_term_sum(self, reg, market_data):
        adder = _node(reg, "add")
        offset = _node(reg, "constant", value=3.0)
        engine, plan = run(
            reg,
            market_data,
            [adder, offset],
            lambda data: [
                # Both of these land on the SAME variadic port.
                EdgeSpec.create(data.id, "close", adder.id, "a"),
                EdgeSpec.create(data.id, "low", adder.id, "a"),
                EdgeSpec.create(offset.id, "value", adder.id, "b"),
            ],
            adder.id,
        )
        return engine, plan, adder

    def test_every_addend_on_the_variadic_port_reaches_the_sum(self, reg):
        market_data = frame(np.linspace(100.0, 140.0, BARS))
        engine, plan, adder = self._three_term_sum(reg, market_data)

        assert len(plan.inbound_edges(adder.id, "a")) == 2, (
            "the plan dropped an operand on the variadic port"
        )
        out = produced(engine, adder.id)
        np.testing.assert_allclose(
            out, BS.math_add(market_data["close"], market_data["low"], 3.0)
        )
        assert not np.allclose(
            out, BS.math_add(market_data["close"], 3.0)
        ), "the sum is missing an addend"

    def test_a_nan_on_one_of_three_addends_makes_that_bar_undefined(self, reg):
        """NaN propagation is over the whole operand list, not the first two."""
        closes = np.linspace(100.0, 140.0, BARS)
        closes[9] = np.nan
        market_data = frame(closes)
        engine, _plan, adder = self._three_term_sum(reg, market_data)

        out = produced(engine, adder.id)
        assert math.isnan(out[9])
        assert np.isnan(out).sum() == 1
        assert_no_infinity(out, "add")

    def test_a_three_operand_minimum_compares_every_operand(self, reg):
        """``min`` reduces across the variadic port, so a lower third operand wins."""
        closes = np.linspace(100.0, 140.0, BARS)
        market_data = frame(closes)
        minimum = _node(reg, "min")
        floor_value = _node(reg, "constant", value=120.0)
        engine, plan = run(
            reg,
            market_data,
            [minimum, floor_value],
            lambda data: [
                EdgeSpec.create(data.id, "close", minimum.id, "a"),
                EdgeSpec.create(data.id, "high", minimum.id, "a"),
                EdgeSpec.create(floor_value.id, "value", minimum.id, "b"),
            ],
            minimum.id,
        )

        assert len(plan.inbound_edges(minimum.id, "a")) == 2
        np.testing.assert_allclose(
            produced(engine, minimum.id),
            BS.math_min(market_data["close"], market_data["high"], 120.0),
        )


# ---------------------------------------------------------------------------
# The engine-side seam: safe_math_apply
# ---------------------------------------------------------------------------


class TestSafeMathApplySeam:
    """``dag_engine.safe_math_apply`` adds bar alignment and node-level recording.

    The arithmetic is the kernel's; these tests assert only what the seam owns.
    """

    def test_it_returns_a_series_on_the_operand_index(self):
        stamps = pd.date_range("2024-03-01", periods=6, freq="1min")
        left = pd.Series(np.arange(6, dtype=float), index=stamps)

        out = safe_math_apply("ADD", [left, 2.0], node_id="n_1")

        assert isinstance(out, pd.Series)
        assert out.index.equals(stamps)
        np.testing.assert_allclose(out.to_numpy(), np.arange(6) + 2.0)

    def test_it_returns_a_series_on_the_index_it_is_given(self):
        stamps = pd.date_range("2024-03-01", periods=4, freq="1min")

        out = safe_math_apply("NEGATE", [np.array([1.0, 2.0, 3.0, 4.0])], index=stamps)

        assert out.index.equals(stamps)

    def test_operands_from_different_bars_are_refused(self):
        """Equal length is not alignment. Two 6-bar series from different windows
        combine into values that never coexisted - the same defect as stacking feature
        matrices by row position, and the kernels cannot see it because a kernel receives
        bare arrays with no timestamps left to compare."""
        left = pd.Series(
            np.ones(6), index=pd.date_range("2024-03-01", periods=6, freq="1min")
        )
        right = pd.Series(
            np.ones(6), index=pd.date_range("2024-04-01", periods=6, freq="1min")
        )

        with pytest.raises(DAGExecutionError) as excinfo:
            safe_math_apply("ADD", [left, right], node_id="n_1")
        assert "aligned" in str(excinfo.value)

    def test_an_unknown_operation_is_refused_rather_than_returning_nan(self):
        """A graph that cannot be evaluated must say so. A NaN says "this bar has no
        value", which a warming node also says, so returning one here would make an
        unimplementable node look like warmup."""
        left = pd.Series([1.0, 2.0])

        with pytest.raises(DAGExecutionError) as excinfo:
            safe_math_apply("TELEPORT", [left], node_id="n_1")
        assert "n_1" in str(excinfo.value)

    def test_it_records_the_condition_into_the_node_issue_log(self):
        log = NodeIssueLog()
        numerator = pd.Series([1.0, 2.0, 3.0])
        denominator = pd.Series([1.0, 0.0, 3.0])

        out = safe_math_apply(
            "DIVIDE", [numerator, denominator], node_id="n_div", issues=log
        )

        assert math.isnan(out.iloc[1])
        assert [issue.code for issue in log.for_node("n_div")] == ["DIVISION_BY_ZERO"]
        assert log.for_node("n_div")[0].bar == 1

    @pytest.mark.parametrize(
        "op,arity,options",
        [
            ("ADD", 2, None),
            ("SUBTRACT", 2, None),
            ("MULTIPLY", 2, None),
            ("DIVIDE", 2, None),
            ("MODULO", 2, None),
            ("MIN", 2, None),
            ("MAX", 2, None),
            ("ABS", 1, None),
            ("NEGATE", 1, None),
            ("ROUND", 1, {"decimals": 3}),
            ("FLOOR", 1, None),
            ("CEIL", 1, None),
            ("SQRT", 1, None),
            ("LOG", 1, {"base": "e"}),
            ("EXP", 1, None),
            ("CLAMP", 1, {"lower": -10.0, "upper": 10.0}),
            ("SHIFT", 1, {"bars": 2}),
        ],
    )
    def test_no_operation_produces_an_infinity_on_an_adversarial_series(
        self, op, arity, options
    ):
        """Requirement 20.3 across every operation the MATH category publishes.

        The operand series carries the whole adversarial set at once: NaN, ±Inf, zero, a
        negative, a denormal and both ends of the float range. Each op is called at its
        own arity, so a unary op is handed one series rather than being asked to ignore a
        second - the kernels are strict about arity, which is itself the behaviour that
        turns a mis-bound operand into a refusal instead of a wrong number.
        """
        hostile = pd.Series(
            [np.nan, np.inf, -np.inf, 0.0, -7.5, 1e-320, 1e308, -1e308, 800.0, 3.0]
        )
        other = pd.Series([1.0, 0.0, -1e308, 1e-320, np.inf, np.nan, 1e308, 2.0, 0.5, 0.0])
        operands = [hostile, other][:arity]

        out = safe_math_apply(op, operands, node_id="n_1", options=options)

        assert_no_infinity(out, op)
        assert not np.isnan(out).all(), (
            f"{op} NaN'd every bar including the well-defined one, which would be a "
            f"firewall that never lets arithmetic through"
        )


# ---------------------------------------------------------------------------
# Requirements 20.5 / 20.6 - the action boundary
# ---------------------------------------------------------------------------


class _Intent:
    """An intent as an object rather than a mapping.

    Task 8.4's emit path is not written yet, so the firewall reads both shapes and this
    asserts it. A payload type is not allowed to decide whether a safety check runs.
    """

    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


class TestAssertExecutionSafeBlocksNonFiniteFields:
    def test_a_nan_quantity_blocks_the_intent(self):
        """The hole this closes: NaN compares False against every limit, so a guard
        written as "refuse if quantity > max_quantity" waves it straight through."""
        with pytest.raises(ExecutionBlocked) as excinfo:
            assert_execution_safe({"quantity": float("nan"), "price": 100.0}, "n_act")

        assert excinfo.value.code == "NON_FINITE_ORDER_FIELD"
        assert excinfo.value.node_id == "n_act"

    @pytest.mark.parametrize("field", ["quantity", "price", "trigger_price", "notional"])
    @pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
    def test_every_named_order_field_is_checked(self, field, value):
        intent = {"quantity": 1.0, "price": 100.0, "trigger_price": 99.0, "notional": 100.0}
        intent[field] = value

        with pytest.raises(ExecutionBlocked) as excinfo:
            assert_execution_safe(intent, "n_act")
        assert excinfo.value.code == "NON_FINITE_ORDER_FIELD"
        assert field in excinfo.value.detail

    def test_a_non_finite_field_beyond_the_named_four_also_blocks(self):
        """Requirement 20.5 is about "a Trade_Intent numeric field", not four of them.
        A stop price is as capable of sending an order to the wrong place."""
        with pytest.raises(ExecutionBlocked) as excinfo:
            assert_execution_safe(
                {"quantity": 1.0, "stop_loss": float("inf")}, "n_act"
            )
        assert "stop_loss" in excinfo.value.detail

    def test_a_decimal_nan_blocks_the_intent(self):
        """Exchange payloads carry ``Decimal``, and ``Decimal("NaN")`` is not a float."""
        with pytest.raises(ExecutionBlocked):
            assert_execution_safe({"quantity": Decimal("NaN")}, "n_act")

    def test_a_named_field_that_is_not_a_number_at_all_is_refused(self):
        """"Unable to check" is not "safe to send"."""
        with pytest.raises(ExecutionBlocked) as excinfo:
            assert_execution_safe({"quantity": "1.0e", "price": 100.0}, "n_act")
        assert excinfo.value.code == "NON_FINITE_ORDER_FIELD"

    def test_a_non_numeric_field_that_is_not_an_order_field_is_left_alone(self):
        """A symbol and a side are not numbers, and refusing them would refuse every
        real intent."""
        assert_execution_safe(
            {
                "symbol": "ETH/USDT",
                "side": "buy",
                "reduce_only": True,
                "quantity": 1.5,
                "price": 100.0,
            },
            "n_act",
        )

    def test_a_missing_optional_field_is_not_a_violation(self):
        """A market order legitimately carries no trigger price."""
        assert_execution_safe(
            {"quantity": 1.5, "price": None, "trigger_price": None}, "n_act"
        )

    def test_an_object_intent_is_checked_like_a_mapping(self):
        with pytest.raises(ExecutionBlocked):
            assert_execution_safe(_Intent(quantity=1.0, price=float("inf")), "n_act")

        assert_execution_safe(_Intent(quantity=1.0, price=100.0), "n_act") is None


class TestAssertExecutionSafeBlocksNonPositiveQuantity:
    @pytest.mark.parametrize("quantity", [0.0, -1.0, -1e-9, Decimal("0")])
    def test_a_quantity_at_or_below_zero_blocks_the_intent(self, quantity):
        with pytest.raises(ExecutionBlocked) as excinfo:
            assert_execution_safe({"quantity": quantity, "price": 100.0}, "n_act")

        assert excinfo.value.code == "NON_POSITIVE_QUANTITY"

    def test_a_positive_quantity_passes(self):
        assert (
            assert_execution_safe({"quantity": 1e-8, "price": 100.0}, "n_act") is None
        )

    def test_a_non_positive_price_is_left_to_the_execution_layer(self):
        """Deliberately not checked here. Order-type semantics own price - a market
        order's price field is a legitimate placeholder - and a firewall that blocks
        valid orders gets switched off. ``execution_guard`` / ``risk_engine`` remain
        authoritative on price."""
        assert_execution_safe({"quantity": 1.0, "price": 0.0}, "n_act")


class TestAssertExecutionSafeRecordsAndIsRecognisable:
    def test_the_incident_is_recorded_against_the_node(self):
        log = NodeIssueLog()

        with pytest.raises(ExecutionBlocked):
            assert_execution_safe({"quantity": float("nan")}, "n_act", issues=log)

        recorded = log.for_node("n_act")
        assert [issue.code for issue in recorded] == ["NON_FINITE_ORDER_FIELD"]

    def test_it_raises_the_platforms_own_execution_blocked(self):
        """One name in the codebase means "no order was sent". Anything already written
        to catch ``core.global_safety.ExecutionBlocked`` treats a blocked intent as a
        non-execution, which is the safe direction."""
        assert issubclass(ExecutionBlocked, PlatformExecutionBlocked)

        with pytest.raises(PlatformExecutionBlocked):
            assert_execution_safe({"quantity": -1.0}, "n_act")

    def test_the_node_and_the_code_are_attributes_not_message_text(self):
        """The caller that records the incident should not have to parse a string."""
        error = ExecutionBlocked("n_act", "NON_FINITE_ORDER_FIELD", "quantity=nan")

        assert (error.node_id, error.code) == ("n_act", "NON_FINITE_ORDER_FIELD")
        assert "n_act" in str(error) and "NON_FINITE_ORDER_FIELD" in str(error)
