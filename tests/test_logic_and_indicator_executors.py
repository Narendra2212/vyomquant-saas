"""
tests/test_logic_and_indicator_executors.py

The gate an author drew is the gate that runs, and the indicator port an author wired is
the series that arrives.

Spec: strategy-builder task 5.4 (``design.md`` -> Logic blocks, and -> Runtime references).
Requirements 5.10, 20.2, 20.7.

THE TWO DEFECT CLASSES THESE TESTS PIN
--------------------------------------
1. **The warmup region must be silent.** ``cross_above`` / ``cross_below`` are defined on
   closed bars only and report **False** - not NaN, not an exception - when either series is
   undefined at the current *or the previous* bar (Requirement 20.7). Getting this wrong
   produces a phantom entry on the first bar an indicator becomes defined, which is a trade
   the strategy could never have made and the single most common way a backtest invents its
   first fill. The same rule runs through every comparator: an undefined bar compares False,
   so ``RSI > 30`` is false while RSI is warming rather than truthy-by-accident.

2. **A multi-output indicator must publish every declared output port.** Requirement 5.10
   says the registry publishes one output port per distinct output, and Requirement 20.2 says
   each one is validated against its declared type before a downstream node can read it.
   Before task 5.4 ``IndicatorExecutor`` returned a single series per node, so an edge from
   ``bollinger_bands.upper`` received %B and an edge from ``macd.signal`` received the
   histogram - a plausible series computed from the wrong quantity, with nothing raised.
   Worse, 27 of the 33 published indicators had no branch at all in the executor and silently
   computed RSI(14).

NOTHING IS FAKED
----------------
The registry is the real assembled registry, the graphs are real canonical graphs of
published descriptors, the plans come from the real compiler through the real
``plan_to_engine_graph``, and execution is the real ``DAGEngine`` over real pandas. Every
expected indicator value is produced by calling ``indicators_backend``'s own published
function - the module the descriptor's ``runtime_ref`` names - so no formula is restated
here. The boolean expectations are written out bar by bar as literals, because the point of
those tests is the *rule*, and deriving the expectation from the kernel under test would
assert nothing about it.
"""

import logging
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import indicators_backend as ib
from backend_app.backend import strategy_compiler as SC
from backend_app.backend.dag_engine import (DAGEngine, DAGExecutionError,
                                            IndicatorComputationError,
                                            IndicatorExecutor,
                                            LegacyLogicExecutor, LogicExecutor,
                                            PortInputs)
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.schema import (EdgeSpec, NodeSpec,
                                                     StrategyGraph)

BARS = 90


# ---------------------------------------------------------------------------
# Fixtures and builders
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg():
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


@pytest.fixture(scope="module")
def candles():
    """A deterministic OHLCV frame: a rising leg, a falling leg, a rising leg.

    Written as an expression rather than drawn at random, so every indicator below moves
    without any seed reaching an asserted value.
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
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": pd.Series(np.linspace(1_000.0, 5_000.0, BARS), index=stamps),
        },
        index=stamps,
    )


@pytest.fixture(scope="module")
def long_candles():
    """Long enough for the widest declared warmup among the 33 published indicators.

    ``ichimoku_cloud`` alone declares a 78-bar warmup, and ``guard_indicators`` refuses an
    indicator output that is entirely NaN - correctly, since a window longer than the data
    has no value to report - so the all-indicator sweep needs a fixture that lets every one
    of them produce something.
    """
    stamps = pd.date_range("2024-01-01", periods=400, freq="5min", name="timestamp")
    trend = np.linspace(0.0, 40.0, 400)
    wave = 12.0 * np.sin(np.linspace(0.0, 9.0 * np.pi, 400))
    close = pd.Series(100.0 + trend + wave, index=stamps)
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": pd.Series(np.linspace(1_000.0, 5_000.0, 400), index=stamps),
        },
        index=stamps,
    )


def bars(values):
    """A short bar index for the direct-executor tests."""
    return pd.date_range("2024-01-01", periods=len(values), freq="5min", name="timestamp")


def frame(closes):
    stamps = bars(closes)
    close = pd.Series(np.asarray(closes, dtype=float), index=stamps)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": pd.Series(1.0, index=stamps),
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


def _data(reg):
    return _node(
        reg,
        "ohlcv_feed",
        symbol="ETH/USDT",
        timeframe="5m",
        market_type="spot",
        mode="streaming",
    )


def _action(reg):
    return _node(reg, "action_buy_market", **_required(reg["action_buy_market"]))


def run_graph(reg, graph, market_data):
    """Validate, compile, adapt and execute: every stage is the real one."""
    report = V.validate(graph, reg)
    assert report.valid, f"the graph must validate: {report.codes()}"
    plan = SC.compile_graph(graph, reg)
    nodes, edges = SC.plan_to_engine_graph(plan, reg)
    engine = DAGEngine(enable_event_buffer=False)
    result = engine.execute_dag(nodes, edges, market_data)
    return engine, result


def logic_node(reg, block_id, **runtime_params):
    """The engine payload ``plan_to_engine_graph`` emits for a LOGIC node.

    Built from the descriptor's own ``runtime_ref``, so a renamed kernel breaks this rather
    than being quietly worked around.
    """
    return {
        "id": f"direct_{block_id}",
        "type": "logic",
        "block_id": block_id,
        "runtime_ref": reg[block_id].runtime_ref,
        "runtime_params": dict(runtime_params),
        "operator": block_id.upper(),
    }


def direct(reg, block_id, ports, market_data, **runtime_params):
    """Run one LOGIC block through ``LogicExecutor`` with port-addressed inputs.

    ``ports`` is ``{input port name: [series, ...]}``, so a variadic port can hold more than
    one connection - which is what ``and`` and ``or`` legitimately do.
    """
    values = {}
    bindings = {}
    for port_name, series_list in ports.items():
        source_ids = []
        for position, series in enumerate(series_list):
            source_id = f"{port_name}_{position}"
            values[source_id] = series
            source_ids.append(source_id)
        bindings[port_name] = source_ids
    inputs = PortInputs(values, bindings)
    return LogicExecutor().execute(
        logic_node(reg, block_id, **runtime_params), inputs, market_data
    )


def as_bool_list(series):
    return [bool(value) for value in np.asarray(series)]


# ---------------------------------------------------------------------------
# Requirement 20.7 - the crossover warmup rule
# ---------------------------------------------------------------------------


class TestCrossoverIsSilentThroughWarmup:
    """The rule that stops a phantom entry on bar one.

    Every expectation here is a literal, computed by hand from the definition in
    ``design.md`` -> Logic blocks. Deriving it from the kernel would make the test agree with
    whatever the kernel does, which is the opposite of pinning a rule.
    """

    #: fast/slow chosen so the four interesting cases all appear in six bars: no previous
    #: bar at all, a NaN previous bar, a genuine up-cross, and a genuine down-cross.
    FAST = [np.nan, np.nan, 1.0, 3.0, 2.0, 5.0]
    SLOW = [np.nan, 2.0, 2.0, 2.0, 3.0, 3.0]

    def test_cross_above_fires_only_on_a_closed_bar_with_both_sides_defined(self, reg):
        market_data = frame(self.FAST)
        fast = pd.Series(self.FAST, index=market_data.index)
        slow = pd.Series(self.SLOW, index=market_data.index)

        produced = direct(reg, "cross_above", {"fast": [fast], "slow": [slow]}, market_data)

        assert as_bool_list(produced) == [
            False,  # bar 0: no previous bar to compare, so nothing can have crossed
            False,  # bar 1: fast[0] is NaN - the relation before the cross is unknown
            False,  # bar 2: fast[1] is NaN, and 1.0 is not above 2.0 anyway
            True,   # bar 3: 1.0 <= 2.0 then 3.0 > 2.0, both bars defined
            False,  # bar 4: fast fell back below; no upward flip
            True,   # bar 5: 2.0 <= 3.0 then 5.0 > 3.0
        ]

    def test_cross_below_is_the_mirror(self, reg):
        market_data = frame(self.FAST)
        fast = pd.Series(self.FAST, index=market_data.index)
        slow = pd.Series(self.SLOW, index=market_data.index)

        produced = direct(reg, "cross_below", {"fast": [fast], "slow": [slow]}, market_data)

        assert as_bool_list(produced) == [False, False, False, False, True, False]

    def test_the_first_defined_bar_after_a_nan_warmup_never_signals(self, reg):
        """The exact bar a phantom entry appears on.

        ``fast`` is above ``slow`` from the moment ``slow`` becomes defined. Treating the NaN
        warmup as "below" - which is what any comparison that does not mask NaN effectively
        does - reports a cross on bar 3. There was no cross: the relation on bar 2 is
        unknown, not "fast was below".
        """
        market_data = frame([10.0] * 6)
        fast = pd.Series([10.0] * 6, index=market_data.index)
        slow = pd.Series([np.nan, np.nan, np.nan, 5.0, 5.0, 5.0], index=market_data.index)

        produced = direct(reg, "cross_above", {"fast": [fast], "slow": [slow]}, market_data)

        assert as_bool_list(produced) == [False] * 6, (
            "a crossover fired inside or at the edge of the NaN warmup region"
        )

    def test_the_result_is_boolean_and_holds_no_nan(self, reg):
        """False, not NaN. A NaN here would propagate into a truthy value downstream."""
        market_data = frame([1.0, 2.0, 3.0, 4.0])
        fast = pd.Series([np.nan, 1.0, 3.0, np.nan], index=market_data.index)
        slow = pd.Series([2.0, 2.0, 2.0, 2.0], index=market_data.index)

        produced = direct(reg, "cross_above", {"fast": [fast], "slow": [slow]}, market_data)

        assert produced.dtype == bool
        assert not produced.isna().any()
        assert as_bool_list(produced) == [False, False, True, False]

    def test_a_real_indicator_warmup_produces_no_signal_before_it_is_satisfied(
        self, reg, candles
    ):
        """End to end: ``close`` crosses an EMA whose first 19 bars are NaN.

        The graph is compiled and executed for real, so this is the runtime's answer rather
        than the kernel's.
        """
        data = _data(reg)
        ema = _node(reg, "ema", window=20, source="close")
        cross = _node(reg, "cross_above", **_required(reg["cross_above"]))
        action = _action(reg)
        graph = StrategyGraph(
            nodes=[data, ema, cross, action],
            edges=[
                EdgeSpec.create(data.id, "close", ema.id, "series"),
                EdgeSpec.create(data.id, "close", cross.id, "fast"),
                EdgeSpec.create(ema.id, "value", cross.id, "slow"),
                EdgeSpec.create(cross.id, "out", action.id, "signal"),
            ],
        )
        engine, _result = run_graph(reg, graph, candles)

        warmup = int(np.isnan(ib.ema(candles["close"], 20)).sum())
        assert warmup == 19, "the fixture must actually have a warmup region to be silent in"
        produced = np.asarray(engine.node_outputs[(cross.id, "out")], dtype=bool)
        assert not produced[: warmup + 1].any(), (
            "a cross fired while the EMA was still undefined, or on the first bar it became "
            f"defined: {np.flatnonzero(produced[: warmup + 1])}"
        )


# ---------------------------------------------------------------------------
# Comparators
# ---------------------------------------------------------------------------


class TestComparators:
    """``left`` is left and ``right`` is right, and an undefined bar is False."""

    @pytest.mark.parametrize(
        "block_id,expected",
        [
            ("gt", [False, False, True, False, False]),
            ("lt", [False, True, False, False, False]),
            ("gte", [False, False, True, True, False]),
            ("lte", [False, True, False, True, False]),
            ("eq", [False, False, False, True, False]),
            ("neq", [False, True, True, False, False]),
        ],
    )
    def test_each_comparator_applies_its_own_relation(self, reg, block_id, expected):
        """Bar 0 is NaN on the left, bar 4 NaN on the right; both must report False."""
        market_data = frame([1.0, 2.0, 3.0, 4.0, 5.0])
        left = pd.Series([np.nan, 1.0, 5.0, 4.0, 4.0], index=market_data.index)
        right = pd.Series([1.0, 2.0, 2.0, 4.0, np.nan], index=market_data.index)

        produced = direct(reg, block_id, {"left": [left], "right": [right]}, market_data)

        assert as_bool_list(produced) == expected

    def test_the_sides_are_bound_by_port_name_not_by_edge_order(self, reg):
        """``gt(a, b)`` and ``gt(b, a)`` must differ, or the comparator is a coin toss."""
        market_data = frame([1.0, 2.0, 3.0])
        low = pd.Series([1.0, 1.0, 1.0], index=market_data.index)
        high = pd.Series([2.0, 2.0, 2.0], index=market_data.index)

        forward = direct(reg, "gt", {"left": [high], "right": [low]}, market_data)
        reverse = direct(reg, "gt", {"left": [low], "right": [high]}, market_data)

        assert as_bool_list(forward) == [True, True, True]
        assert as_bool_list(reverse) == [False, False, False]

    def test_an_unfed_required_port_names_itself(self, reg):
        market_data = frame([1.0, 2.0, 3.0])
        left = pd.Series([1.0, 2.0, 3.0], index=market_data.index)

        with pytest.raises(DAGExecutionError) as excinfo:
            direct(reg, "gt", {"left": [left]}, market_data)
        assert "'right'" in str(excinfo.value)

    def test_a_comparator_runs_through_the_compiled_graph(self, reg, candles):
        """The same relation, reached through the compiler and the engine."""
        data = _data(reg)
        floor = _node(reg, "constant", value=120.0)
        gate = _node(reg, "gt", **_required(reg["gt"]))
        action = _action(reg)
        graph = StrategyGraph(
            nodes=[data, floor, gate, action],
            edges=[
                EdgeSpec.create(data.id, "close", gate.id, "left"),
                EdgeSpec.create(floor.id, "value", gate.id, "right"),
                EdgeSpec.create(gate.id, "out", action.id, "signal"),
            ],
        )
        engine, _result = run_graph(reg, graph, candles)

        produced = np.asarray(engine.node_outputs[(gate.id, "out")], dtype=bool)
        np.testing.assert_array_equal(produced, np.asarray(candles["close"] > 120.0))
        assert produced.any() and not produced.all(), (
            "the gate is constant over this fixture, so the comparison proves nothing"
        )


# ---------------------------------------------------------------------------
# Boolean gates
# ---------------------------------------------------------------------------


class TestBooleanGates:
    def test_and_is_true_only_where_every_input_is(self, reg):
        market_data = frame([1.0, 2.0, 3.0, 4.0])
        first = pd.Series([True, True, False, False], index=market_data.index)
        second = pd.Series([True, False, True, False], index=market_data.index)

        produced = direct(reg, "and", {"a": [first], "b": [second]}, market_data)

        assert as_bool_list(produced) == [True, False, False, False]

    def test_or_is_true_where_at_least_one_input_is(self, reg):
        market_data = frame([1.0, 2.0, 3.0, 4.0])
        first = pd.Series([True, True, False, False], index=market_data.index)
        second = pd.Series([True, False, True, False], index=market_data.index)

        produced = direct(reg, "or", {"a": [first], "b": [second]}, market_data)

        assert as_bool_list(produced) == [True, True, True, False]

    def test_not_inverts_and_an_undefined_bar_becomes_true(self, reg):
        """An undefined bar is false, so NOT of it is true. Stated so it is deliberate."""
        market_data = frame([1.0, 2.0, 3.0])
        condition = pd.Series([1.0, 0.0, np.nan], index=market_data.index)

        produced = direct(reg, "not", {"a": [condition]}, market_data)

        assert as_bool_list(produced) == [False, True, True]

    def test_a_variadic_port_receives_every_condition_it_holds(self, reg):
        """Three conditions on ``and``: two on the variadic ``a`` port, one on ``b``.

        Reducing a variadic port to one connection would compute a plausible answer from a
        subset of the author's conditions - here, dropping the always-false one and
        reporting True.
        """
        market_data = frame([1.0, 2.0, 3.0])
        always = pd.Series([True, True, True], index=market_data.index)
        never = pd.Series([False, False, False], index=market_data.index)

        produced = direct(
            reg, "and", {"a": [always, never], "b": [always]}, market_data
        )

        assert as_bool_list(produced) == [False, False, False]

    def test_a_gate_over_two_real_comparators_runs_through_the_compiled_graph(
        self, reg, candles
    ):
        data = _data(reg)
        low = _node(reg, "constant", value=110.0)
        high = _node(reg, "constant", value=130.0)
        above = _node(reg, "gt", **_required(reg["gt"]))
        below = _node(reg, "lt", **_required(reg["lt"]))
        gate = _node(reg, "and", **_required(reg["and"]))
        action = _action(reg)
        graph = StrategyGraph(
            nodes=[data, low, high, above, below, gate, action],
            edges=[
                EdgeSpec.create(data.id, "close", above.id, "left"),
                EdgeSpec.create(low.id, "value", above.id, "right"),
                EdgeSpec.create(data.id, "close", below.id, "left"),
                EdgeSpec.create(high.id, "value", below.id, "right"),
                EdgeSpec.create(above.id, "out", gate.id, "a"),
                EdgeSpec.create(below.id, "out", gate.id, "b"),
                EdgeSpec.create(gate.id, "out", action.id, "signal"),
            ],
        )
        engine, _result = run_graph(reg, graph, candles)

        close = candles["close"]
        expected = np.asarray((close > 110.0) & (close < 130.0))
        np.testing.assert_array_equal(
            np.asarray(engine.node_outputs[(gate.id, "out")], dtype=bool), expected
        )
        assert expected.any() and not expected.all()


# ---------------------------------------------------------------------------
# between
# ---------------------------------------------------------------------------


class TestBetween:
    def test_inclusive_bounds_count_a_value_exactly_on_a_bound_as_inside(self, reg):
        market_data = frame([1.0, 2.0, 3.0, 4.0, 5.0])
        value = pd.Series([0.5, 1.0, 2.0, 3.0, np.nan], index=market_data.index)
        lower = pd.Series(1.0, index=market_data.index)
        upper = pd.Series(3.0, index=market_data.index)

        produced = direct(
            reg,
            "between",
            {"value": [value], "lower": [lower], "upper": [upper]},
            market_data,
            inclusive=True,
        )

        assert as_bool_list(produced) == [False, True, True, True, False]

    def test_exclusive_bounds_exclude_it(self, reg):
        market_data = frame([1.0, 2.0, 3.0, 4.0, 5.0])
        value = pd.Series([0.5, 1.0, 2.0, 3.0, np.nan], index=market_data.index)
        lower = pd.Series(1.0, index=market_data.index)
        upper = pd.Series(3.0, index=market_data.index)

        produced = direct(
            reg,
            "between",
            {"value": [value], "lower": [lower], "upper": [upper]},
            market_data,
            inclusive=False,
        )

        assert as_bool_list(produced) == [False, False, True, False, False]

    def test_the_three_ports_are_not_interchangeable(self, reg):
        """``value``/``lower``/``upper`` swapped is a band that can never be inside."""
        market_data = frame([1.0, 2.0, 3.0])
        value = pd.Series(2.0, index=market_data.index)
        lower = pd.Series(1.0, index=market_data.index)
        upper = pd.Series(3.0, index=market_data.index)

        correct = direct(
            reg,
            "between",
            {"value": [value], "lower": [lower], "upper": [upper]},
            market_data,
        )
        swapped = direct(
            reg,
            "between",
            {"value": [upper], "lower": [value], "upper": [lower]},
            market_data,
        )

        assert as_bool_list(correct) == [True, True, True]
        assert as_bool_list(swapped) == [False, False, False]


# ---------------------------------------------------------------------------
# if_then_else
# ---------------------------------------------------------------------------


class TestIfThenElse:
    def test_each_bar_takes_the_branch_its_condition_selects(self, reg):
        market_data = frame([1.0, 2.0, 3.0, 4.0])
        condition = pd.Series([True, False, True, False], index=market_data.index)
        then_value = pd.Series([10.0, 10.0, 10.0, 10.0], index=market_data.index)
        else_value = pd.Series([-1.0, -2.0, -3.0, -4.0], index=market_data.index)

        produced = direct(
            reg,
            "if_then_else",
            {
                "condition": [condition],
                "then_value": [then_value],
                "else_value": [else_value],
            },
            market_data,
        )

        assert list(np.asarray(produced, dtype=float)) == [10.0, -2.0, 10.0, -4.0]

    def test_an_undefined_condition_takes_the_else_branch(self, reg):
        """An undefined bar is false, so it selects ``else_value`` rather than inventing."""
        market_data = frame([1.0, 2.0])
        condition = pd.Series([np.nan, 1.0], index=market_data.index)
        then_value = pd.Series([10.0, 10.0], index=market_data.index)
        else_value = pd.Series([-5.0, -5.0], index=market_data.index)

        produced = direct(
            reg,
            "if_then_else",
            {
                "condition": [condition],
                "then_value": [then_value],
                "else_value": [else_value],
            },
            market_data,
        )

        assert list(np.asarray(produced, dtype=float)) == [-5.0, 10.0]

    def test_it_runs_through_the_compiled_graph(self, reg, candles):
        """``if_then_else``'s declared successors exclude ACTION, so the ACTION path is the
        comparator's; the selector node is a legal leaf downstream of DATA."""
        data = _data(reg)
        floor = _node(reg, "constant", value=120.0)
        gate = _node(reg, "gt", **_required(reg["gt"]))
        select = _node(reg, "if_then_else", **_required(reg["if_then_else"]))
        action = _action(reg)
        graph = StrategyGraph(
            nodes=[data, floor, gate, select, action],
            edges=[
                EdgeSpec.create(data.id, "close", gate.id, "left"),
                EdgeSpec.create(floor.id, "value", gate.id, "right"),
                EdgeSpec.create(gate.id, "out", action.id, "signal"),
                EdgeSpec.create(gate.id, "out", select.id, "condition"),
                EdgeSpec.create(data.id, "high", select.id, "then_value"),
                EdgeSpec.create(data.id, "low", select.id, "else_value"),
            ],
        )
        engine, _result = run_graph(reg, graph, candles)

        expected = np.where(
            np.asarray(candles["close"] > 120.0),
            np.asarray(candles["high"], dtype=float),
            np.asarray(candles["low"], dtype=float),
        )
        np.testing.assert_allclose(
            np.asarray(engine.node_outputs[(select.id, "out")], dtype=float), expected
        )


# ---------------------------------------------------------------------------
# to_signal
# ---------------------------------------------------------------------------


class TestToSignal:
    def test_a_long_signal_is_positive_and_a_short_one_is_negative(self, reg):
        market_data = frame([1.0, 2.0, 3.0])
        condition = pd.Series([True, False, True], index=market_data.index)

        long_signal = direct(
            reg,
            "to_signal",
            {"condition": [condition]},
            market_data,
            direction="long",
            strength=1.0,
        )
        short_signal = direct(
            reg,
            "to_signal",
            {"condition": [condition]},
            market_data,
            direction="short",
            strength=1.0,
        )

        assert list(np.asarray(long_signal, dtype=float)) == [1.0, 0.0, 1.0]
        assert list(np.asarray(short_signal, dtype=float)) == [-1.0, 0.0, -1.0]

    def test_strength_scales_the_emitted_signal(self, reg):
        market_data = frame([1.0, 2.0])
        condition = pd.Series([True, False], index=market_data.index)

        produced = direct(
            reg,
            "to_signal",
            {"condition": [condition]},
            market_data,
            direction="long",
            strength=0.25,
        )

        assert list(np.asarray(produced, dtype=float)) == [0.25, 0.0]

    @pytest.mark.parametrize(
        "runtime_params",
        [
            {"direction": "up", "strength": 1.0},
            {"direction": "long", "strength": 1.5},
            {"direction": "long", "strength": -0.5},
        ],
    )
    def test_an_out_of_contract_direction_or_strength_is_refused(
        self, reg, runtime_params
    ):
        """No default direction and no clamped strength: a silent 'long' would trade the
        exact opposite of a short strategy, and a strength above 1 is not a conviction."""
        market_data = frame([1.0, 2.0])
        condition = pd.Series([True, False], index=market_data.index)

        with pytest.raises(DAGExecutionError):
            direct(
                reg, "to_signal", {"condition": [condition]}, market_data, **runtime_params
            )

    def test_it_runs_through_the_compiled_graph_into_an_action(self, reg, candles):
        data = _data(reg)
        floor = _node(reg, "constant", value=120.0)
        gate = _node(reg, "gt", **_required(reg["gt"]))
        signal = _node(reg, "to_signal", direction="short", strength=0.5)
        action = _action(reg)
        graph = StrategyGraph(
            nodes=[data, floor, gate, signal, action],
            edges=[
                EdgeSpec.create(data.id, "close", gate.id, "left"),
                EdgeSpec.create(floor.id, "value", gate.id, "right"),
                EdgeSpec.create(gate.id, "out", signal.id, "condition"),
                EdgeSpec.create(signal.id, "out", action.id, "signal"),
            ],
        )
        engine, _result = run_graph(reg, graph, candles)

        expected = np.where(np.asarray(candles["close"] > 120.0), -0.5, 0.0)
        np.testing.assert_allclose(
            np.asarray(engine.node_outputs[(signal.id, "out")], dtype=float), expected
        )


# ---------------------------------------------------------------------------
# The legacy loose-dict logic path is untouched
# ---------------------------------------------------------------------------


class TestTheLegacyLogicPathIsUnchanged:
    def test_a_dict_with_no_runtime_ref_still_reads_its_uppercase_operator(self, reg):
        """``LogicExecutor``'s fallback is ``LegacyLogicExecutor``, so a hand-built dict -
        of which there are still callers - behaves exactly as it did."""
        market_data = frame([1.0, 2.0, 3.0])
        left = pd.Series([1.0, 5.0, 1.0], index=market_data.index)
        right = pd.Series(2.0, index=market_data.index)

        through_logic = LogicExecutor().execute(
            {"id": "legacy", "operator": "GT"}, {"a": left, "b": right}, market_data
        )
        through_legacy = LegacyLogicExecutor().execute(
            {"id": "legacy", "operator": "GT"}, {"a": left, "b": right}, market_data
        )

        assert as_bool_list(through_logic) == [False, True, False]
        assert as_bool_list(through_logic) == as_bool_list(through_legacy)


# ---------------------------------------------------------------------------
# Requirements 5.10 / 20.2 - every declared indicator output port is published
# ---------------------------------------------------------------------------


def indicator_graph(reg, indicator, wiring, read_port, **params):
    """``ohlcv_feed -> <indicator> -> gt -> action``, with the indicator wired by ``wiring``.

    The ``gt -> action`` tail is scaffolding that makes the graph legal (a DATA node and an
    ACTION node are both required); no assertion below is about it. ``read_port`` is the
    indicator output port the tail consumes, chosen per test so the *named* port is the one
    an edge actually asks for.
    """
    data = _data(reg)
    node = _node(reg, indicator, **params)
    threshold = _node(reg, "constant", value=0.0)
    gate = _node(reg, "gt", **_required(reg["gt"]))
    action = _action(reg)
    graph = StrategyGraph(
        nodes=[data, node, threshold, gate, action],
        edges=[
            *wiring(data, node),
            EdgeSpec.create(node.id, read_port, gate.id, "left"),
            EdgeSpec.create(threshold.id, "value", gate.id, "right"),
            EdgeSpec.create(gate.id, "out", action.id, "signal"),
        ],
    )
    return graph, node


def series_wiring(data, node):
    return [EdgeSpec.create(data.id, "close", node.id, "series")]


def hlc_wiring(data, node):
    return [
        EdgeSpec.create(data.id, "high", node.id, "high"),
        EdgeSpec.create(data.id, "low", node.id, "low"),
        EdgeSpec.create(data.id, "close", node.id, "close"),
    ]


class TestMultiOutputIndicatorsPublishEveryPort:
    """Requirement 5.10 at runtime, not only in the published registry payload."""

    def test_macd_publishes_its_three_declared_ports_as_three_distinct_series(
        self, reg, candles
    ):
        graph, node = indicator_graph(
            reg, "macd", series_wiring, "histogram", fast=12, slow=26, signal=9
        )
        engine, _result = run_graph(reg, graph, candles)

        macd_line, signal_line, histogram = ib.macd(
            candles["close"], fast=12, slow=26, signal=9
        )
        for port_name, expected in (
            ("macd", macd_line),
            ("signal", signal_line),
            ("histogram", histogram),
        ):
            key = (node.id, port_name)
            assert key in engine.node_outputs, f"port '{port_name}' was not published"
            np.testing.assert_allclose(
                np.asarray(engine.node_outputs[key], dtype=float),
                np.asarray(expected, dtype=float),
                equal_nan=True,
            )

        published = [
            np.asarray(engine.node_outputs[(node.id, name)], dtype=float)
            for name in ("macd", "signal", "histogram")
        ]
        for first in range(len(published)):
            for second in range(first + 1, len(published)):
                assert not np.array_equal(
                    published[first], published[second], equal_nan=True
                ), (
                    "two MACD ports carry the identical series, which is the collapse this "
                    "task closed"
                )

    def test_an_edge_naming_a_specific_macd_port_receives_that_port(self, reg, candles):
        """``macd.signal -> gt.left`` must deliver the signal line, not the histogram."""
        graph, node = indicator_graph(
            reg, "macd", series_wiring, "signal", fast=12, slow=26, signal=9
        )
        engine, _result = run_graph(reg, graph, candles)

        gate_id = next(
            node_id
            for (node_id, port) in engine.node_outputs
            if port == "out" and node_id != node.id
        )
        _macd_line, signal_line, histogram = ib.macd(
            candles["close"], fast=12, slow=26, signal=9
        )
        produced = np.asarray(engine.node_outputs[(gate_id, "out")], dtype=bool)
        against_signal = np.asarray(signal_line, dtype=float) > 0.0
        against_signal[np.isnan(np.asarray(signal_line, dtype=float))] = False
        against_histogram = np.asarray(histogram, dtype=float) > 0.0
        against_histogram[np.isnan(np.asarray(histogram, dtype=float))] = False

        np.testing.assert_array_equal(produced, against_signal)
        assert not np.array_equal(against_signal, against_histogram), (
            "the signal line and the histogram compare identically on this fixture, so "
            "this assertion proves nothing"
        )

    def test_bollinger_bands_publishes_all_five_bands(self, reg, candles):
        graph, node = indicator_graph(
            reg, "bollinger_bands", series_wiring, "percent_b", window=20, num_std=2.0
        )
        engine, _result = run_graph(reg, graph, candles)

        middle, lower, upper, bandwidth, percent_b = ib.bollinger_bands(
            candles["close"], window=20, num_std=2.0
        )
        for port_name, expected in (
            ("middle", middle),
            ("lower", lower),
            ("upper", upper),
            ("bandwidth", bandwidth),
            ("percent_b", percent_b),
        ):
            key = (node.id, port_name)
            assert key in engine.node_outputs, f"port '{port_name}' was not published"
            np.testing.assert_allclose(
                np.asarray(engine.node_outputs[key], dtype=float),
                np.asarray(expected, dtype=float),
                equal_nan=True,
            )

        assert not np.array_equal(
            np.asarray(engine.node_outputs[(node.id, "upper")], dtype=float),
            np.asarray(engine.node_outputs[(node.id, "percent_b")], dtype=float),
            equal_nan=True,
        ), "an edge from 'upper' used to receive %B; the two must not be the same series"

    def test_a_multi_input_indicator_binds_high_low_and_close_to_their_own_ports(
        self, reg, candles
    ):
        """``stochastic`` reads three different columns of one upstream node.

        Read positionally through the id-keyed view those three edges collapse to one entry
        and the indicator computes over close three times - a plausible oscillator from the
        wrong data. This asserts the published k and d, which can only come out right if
        high, low and close each arrived on their own port.
        """
        graph, node = indicator_graph(
            reg, "stochastic", hlc_wiring, "k", k_window=14, d_window=3
        )
        engine, _result = run_graph(reg, graph, candles)

        k_line, d_line = ib.stochastic(
            candles["high"], candles["low"], candles["close"], k_window=14, d_window=3
        )
        for port_name, expected in (("k", k_line), ("d", d_line)):
            np.testing.assert_allclose(
                np.asarray(engine.node_outputs[(node.id, port_name)], dtype=float),
                np.asarray(expected, dtype=float),
                equal_nan=True,
            )

        collapsed, _d = ib.stochastic(
            candles["close"], candles["close"], candles["close"], k_window=14, d_window=3
        )
        assert not np.array_equal(
            np.asarray(k_line, dtype=float),
            np.asarray(collapsed, dtype=float),
            equal_nan=True,
        ), "high/low/close and close/close/close agree on this fixture, so this proves nothing"

    def test_every_published_indicator_publishes_every_port_it_declares(
        self, reg, long_candles
    ):
        """All 33, not a sampled few - the ``else`` branch that computed RSI(14) for 27 of
        them is what this closes.

        Each indicator is executed through ``IndicatorExecutor`` directly with its ports
        unfed, so the market-data fallback binds them; that is the same path a legacy loose
        dict takes, and it exercises every declared input shape without building 33 graphs.
        The fixture is long enough for the widest declared warmup, because
        ``guard_indicators`` legitimately refuses an output that is entirely NaN.
        """
        candles = long_candles
        executor = IndicatorExecutor()
        for spec in ib.INDICATOR_SPECS:
            node = {
                "id": f"probe_{spec.block_id}",
                "type": "indicator",
                "block_id": spec.block_id,
                "indicator": spec.block_id,
                "params": _required(reg[spec.block_id]),
            }
            produced = executor.execute(node, PortInputs({"upstream": candles["close"]}), candles)
            by_port = produced.by_port
            assert set(by_port) == {port.name for port in spec.outputs}, (
                f"{spec.block_id} declares {[p.name for p in spec.outputs]} but published "
                f"{sorted(by_port)}"
            )
            for series in by_port.values():
                assert len(series) == len(candles)

    def test_the_legacy_primary_value_is_the_port_the_old_executor_returned(
        self, reg, candles
    ):
        """Publishing new ports must not move ``node_results[node_id]``.

        The deleted ``_calculate_macd`` returned the histogram and ``_calculate_bollinger``
        returned %B. An existing edge with no ``source_port``, ``dag_event_loop``'s signal
        history and the golden-plan digest all read that value.
        """
        for indicator, expected_port, params in (
            ("macd", "histogram", {"fast": 12, "slow": 26, "signal": 9}),
            ("bollinger_bands", "percent_b", {"window": 20, "num_std": 2.0}),
        ):
            graph, node = indicator_graph(
                reg, indicator, series_wiring, expected_port, **params
            )
            engine, result = run_graph(reg, graph, candles)
            np.testing.assert_allclose(
                np.asarray(result["node_results"][node.id], dtype=float),
                np.asarray(engine.node_outputs[(node.id, expected_port)], dtype=float),
                equal_nan=True,
            )

    def test_no_fallback_warning_is_logged_for_an_indicator_output_port(
        self, reg, candles, caplog
    ):
        """``resolve_node_inputs`` warns when an edge names a port its source did not
        produce. For indicators that warning must now be unreachable."""
        graph, _node = indicator_graph(
            reg, "macd", series_wiring, "signal", fast=12, slow=26, signal=9
        )
        with caplog.at_level(logging.WARNING, logger="DAGEngine"):
            run_graph(reg, graph, candles)

        offending = [
            record.getMessage()
            for record in caplog.records
            if "which its executor did not produce" in record.getMessage()
        ]
        assert offending == [], offending


# ---------------------------------------------------------------------------
# The re-point itself
# ---------------------------------------------------------------------------


class TestIndicatorExecutorDelegatesToIndicatorsBackend:
    @pytest.mark.parametrize(
        "attribute",
        ["_calculate_rsi", "_calculate_macd", "_calculate_bollinger", "_calculate_atr"],
    )
    def test_the_private_duplicates_are_gone(self, attribute):
        """They disagreed with the module they duplicated - a simple rolling mean where
        ``indicators_backend.rsi`` uses Wilder smoothing, a sample standard deviation where
        ``bollinger_bands`` uses a population one - so the engine and the registry's
        published warmup described different numbers."""
        assert not hasattr(IndicatorExecutor, attribute)

    def test_the_engine_computes_what_the_published_runtime_computes(self, reg, candles):
        graph, node = indicator_graph(reg, "rsi", series_wiring, "value", window=14)
        engine, _result = run_graph(reg, graph, candles)

        np.testing.assert_allclose(
            np.asarray(engine.node_outputs[(node.id, "value")], dtype=float),
            np.asarray(ib.rsi(candles["close"], 14), dtype=float),
            equal_nan=True,
        )

    def test_the_declared_warmup_region_is_nan_rather_than_a_fabricated_number(
        self, reg, candles
    ):
        """Requirement 20.1 needs an absent value to be absent. The deleted EMA branch
        published a 20-bar EMA at bar 0, computed from one bar."""
        graph, node = indicator_graph(
            reg, "ema", series_wiring, "value", window=20, source="close"
        )
        engine, _result = run_graph(reg, graph, candles)

        produced = np.asarray(engine.node_outputs[(node.id, "value")], dtype=float)
        assert np.isnan(produced[:19]).all(), (
            "a 20-bar EMA reported a value before it had 20 bars"
        )
        assert not np.isnan(produced[19:]).any()
        assert reg["ema"].warmup({"window": 20}) >= 20, (
            "the registry's declared warmup must not be shorter than the bars the runtime "
            "leaves undefined, or a readiness gate calls the node ready while it is NaN"
        )

    def test_an_indicator_the_registry_does_not_publish_is_refused(self, candles):
        """It used to log a warning and compute RSI(14). Running a different indicator than
        the author selected is a wrong-strategy defect, not a degraded mode."""
        node = {"id": "unknown", "type": "indicator", "indicator": "not_an_indicator"}

        with pytest.raises(IndicatorComputationError) as excinfo:
            IndicatorExecutor().execute(
                node, PortInputs({"upstream": candles["close"]}), candles
            )
        assert "not_an_indicator" in str(excinfo.value)

    def test_a_legacy_bollinger_alias_still_resolves(self, candles):
        """``strategy_compiler`` writes ``indicator="bb"`` for ``bollinger_bands``."""
        node = {
            "id": "legacy_bb",
            "type": "indicator",
            "indicator": "bb",
            "params": {"period": 20, "std_dev": 2.0},
        }
        produced = IndicatorExecutor().execute(
            node, PortInputs({"upstream": candles["close"]}), candles
        )

        _mid, _low, _up, _bw, percent_b = ib.bollinger_bands(
            candles["close"], window=20, num_std=2.0
        )
        np.testing.assert_allclose(
            np.asarray(produced, dtype=float),
            np.asarray(percent_b, dtype=float),
            equal_nan=True,
        )

    def test_a_legacy_period_alias_still_sets_the_window(self, candles):
        """A hand-built dict carries ``period``; the descriptor publishes ``window``."""
        node = {
            "id": "legacy_rsi",
            "type": "indicator",
            "indicator": "rsi",
            "params": {"period": 7},
        }
        produced = IndicatorExecutor().execute(
            node, PortInputs({"upstream": candles["close"]}), candles
        )

        np.testing.assert_allclose(
            np.asarray(produced, dtype=float),
            np.asarray(ib.rsi(candles["close"], 7), dtype=float),
            equal_nan=True,
        )
        assert not np.array_equal(
            np.asarray(produced, dtype=float),
            np.asarray(ib.rsi(candles["close"], 14), dtype=float),
            equal_nan=True,
        )
