"""
tests/test_dag_engine_port_addressing.py

The block that ran is the block the author wired, on the port the author wired it to.

Spec: strategy-builder task 5.2 (``design.md`` -> DAG runtime contract, Feature
engineering, Runtime executor interface). Requirements 4.3, 18.12, 20.2.

THE DEFECT CLASS THESE TESTS EXIST FOR
--------------------------------------
Before this task the engine resolved a node's inputs by walking the edge list and keying
them on the **upstream node id**, and it stored one output per node. Three consequences,
none of which raised:

1. ``ohlcv_feed.volume -> feat_volume.volume`` delivered **close prices as volume**. The
   DATA pass-through returned ``market_data["close"]`` for every output port, so a volume
   feature was computed from prices. Plausible numbers, wrong column, silent.
2. A multi-output block collapsed onto one value. ``macd`` publishes three ports and
   ``xgboost`` two; an edge from the port that was not returned received whichever one
   was.
3. FEATURE_ENGINEERING nodes did not execute at all - ``{"type": "feature"}`` was mapped
   to the same pass-through, so a feature node republished ``close`` instead of producing
   features. That is the runtime half of SB-03.

Positional / primary-value resolution is the same defect class as stacking two feature
matrices by row position: a plausible answer computed from the wrong data, with nothing
raised. So these tests assert the *addressing*, not just the arithmetic.

NOTHING IS FAKED
----------------
The registry is the real assembled registry, the graphs are real canonical graphs built
from published descriptors, the plans come from the real compiler through the real
``plan_to_engine_graph``, execution is the real ``DAGEngine`` over real pandas, and every
expected feature value is produced by calling the block's own published runtime - never
by restating a formula here.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend import strategy_compiler as SC
from backend_app.backend.dag_engine import (DAGEngine, DAGExecutionError,
                                            FeatureExecutor, NodeOutputs,
                                            PortInputs, descriptor_for,
                                            resolve_node_inputs,
                                            validate_node_output,
                                            validate_port_output)
from backend_app.backend.feature_engineering import FEATURE_SPECS, FeatureEngine
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.feature_matrix import FeatureMatrix
from backend_app.backend.strategy_dag.schema import (EdgeSpec, NodeSpec,
                                                     StrategyGraph)

BARS = 80


# ---------------------------------------------------------------------------
# Fixtures and builders
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg():
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


@pytest.fixture(scope="module")
def candles():
    """A deterministic OHLCV frame whose five columns are all *different*.

    That matters here more than the shape of the price path: the whole point is
    detecting a port that delivered the wrong column, and a frame whose volume happens
    to equal its close cannot detect anything.
    """
    stamps = pd.date_range("2024-01-01", periods=BARS, freq="5min", name="timestamp")
    close = pd.Series(np.linspace(100.0, 140.0, BARS), index=stamps)
    return pd.DataFrame(
        {
            # Deliberately asymmetric around close: with high = close + h and
            # low = close - h, hlc3 collapses to close exactly, and a test asserting
            # "the frame port delivered more than one series" could not fail.
            "open": close - 0.25,
            "high": close + 1.5,
            "low": close - 0.5,
            "close": close,
            "volume": pd.Series(np.linspace(1_000.0, 9_000.0, BARS), index=stamps),
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


def feature_graph(reg, feature_nodes, feature_edges_from):
    """``ohlcv_feed -> <feature nodes> -> xgboost -> gt -> action``.

    The smallest graph that both validates (Requirement 7.6 needs a DATA node and an
    ACTION node) and routes real FEATURE_ENGINEERING nodes. ``feature_edges_from`` maps
    each feature node to ``(data output port, its own input port)``, so a test says
    which DATA port it is wiring and the test - not the engine - owns that claim.
    """
    data = _node(
        reg,
        "ohlcv_feed",
        symbol="ETH/USDT",
        timeframe="5m",
        market_type="spot",
        mode="streaming",
    )
    model = _node(reg, "xgboost", **_required(reg["xgboost"]))
    gate = _node(reg, "gt", **_required(reg["gt"]))
    const = _node(reg, "constant", value=0.5)
    action = _node(reg, "action_buy_market", **_required(reg["action_buy_market"]))

    edges = []
    for node in feature_nodes:
        source_port, target_port = feature_edges_from[node.id]
        edges.append(EdgeSpec.create(data.id, source_port, node.id, target_port))

    # The last feature node feeds the model, so every feature node is on a path from
    # DATA and the graph has no orphan (validation stage 7).
    edges.append(EdgeSpec.create(feature_nodes[-1].id, "matrix", model.id, "features"))
    edges.append(EdgeSpec.create(model.id, "prediction", gate.id, "left"))
    edges.append(EdgeSpec.create(const.id, "value", gate.id, "right"))
    edges.append(EdgeSpec.create(gate.id, "out", action.id, "signal"))

    graph = StrategyGraph(
        nodes=[data, *feature_nodes, model, gate, const, action], edges=edges
    )
    return graph, {"data": data.id, "model": model.id, "action": action.id}


def concat_graph(reg, feature_nodes):
    """``ohlcv_feed -> <feature nodes> -> feat_concat -> xgboost -> gt -> action``.

    ``feature_graph`` chains its feature nodes into the model one at a time; this builder
    fans **all** of them onto ``feat_concat``'s single variadic ``matrix`` port, which is the
    wiring a variadic fan-in test needs and the one the plan used to collapse to one edge.
    Every feature node reads ``close``, so the graph differs from ``feature_graph`` only in
    where the matrices converge.
    """
    data = _node(
        reg,
        "ohlcv_feed",
        symbol="ETH/USDT",
        timeframe="5m",
        market_type="spot",
        mode="streaming",
    )
    concat = _node(reg, "feat_concat")
    model = _node(reg, "xgboost", **_required(reg["xgboost"]))
    gate = _node(reg, "gt", **_required(reg["gt"]))
    const = _node(reg, "constant", value=0.5)
    action = _node(reg, "action_buy_market", **_required(reg["action_buy_market"]))

    edges = []
    for node in feature_nodes:
        edges.append(EdgeSpec.create(data.id, "close", node.id, "series"))
        # Every one of these lands on the SAME variadic port of the SAME node.
        edges.append(EdgeSpec.create(node.id, "matrix", concat.id, "matrix"))
    edges.append(EdgeSpec.create(concat.id, "matrix", model.id, "features"))
    edges.append(EdgeSpec.create(model.id, "prediction", gate.id, "left"))
    edges.append(EdgeSpec.create(const.id, "value", gate.id, "right"))
    edges.append(EdgeSpec.create(gate.id, "out", action.id, "signal"))

    graph = StrategyGraph(
        nodes=[data, *feature_nodes, concat, model, gate, const, action], edges=edges
    )
    return graph, {"data": data.id, "concat": concat.id, "action": action.id}


def run(reg, graph, candles):
    """Compile, adapt and execute: the real validator, compiler, adapter and engine."""
    report = V.validate(graph, reg)
    assert report.valid, f"the graph must validate: {report.codes()}"
    plan = SC.compile_graph(graph, reg)
    nodes, edges = SC.plan_to_engine_graph(plan, reg)
    engine = DAGEngine(enable_event_buffer=False)
    result = engine.execute_dag(nodes, edges, candles)
    return engine, result


# ---------------------------------------------------------------------------
# Requirement 20.2 - inputs addressed by input port name
# ---------------------------------------------------------------------------


class TestInputsAreAddressedByPort:
    """An edge's ``target_port`` decides which input a value lands on."""

    def test_the_volume_port_receives_volume_and_not_close(self, reg, candles):
        """The headline regression: ``ohlcv_feed.volume -> feat_volume.volume``.

        The pass-through returned ``close`` for every DATA output port, so this feature
        was computed from prices. The assertion compares against ``compute_volume_features``
        run on the volume column directly - the block's own published runtime - so it
        cannot pass by accident on a frame where the two columns are similar.
        """
        volume = _node(reg, "feat_volume", window=6)
        graph, ids = feature_graph(
            reg, [volume], {volume.id: ("volume", "volume")}
        )
        engine, _result = run(reg, graph, candles)

        matrix = engine.node_outputs[(volume.id, "matrix")]
        expected_mean, expected_std, expected_ratio = (
            FeatureEngine.compute_volume_features(
                np.asarray(candles["volume"], dtype=float), 6
            )
        )

        np.testing.assert_allclose(
            matrix.column("volume_mean_6"), expected_mean, equal_nan=True
        )
        np.testing.assert_allclose(
            matrix.column("volume_std_6"), expected_std, equal_nan=True
        )
        np.testing.assert_allclose(
            matrix.column("volume_ratio_6"), expected_ratio, equal_nan=True
        )

        # And it is emphatically NOT the close-derived answer the old path produced.
        close_derived, _, _ = FeatureEngine.compute_volume_features(
            np.asarray(candles["close"], dtype=float), 6
        )
        assert not np.allclose(
            matrix.column("volume_mean_6"), close_derived, equal_nan=True
        ), "the volume port delivered close prices - the pre-task defect"

    def test_the_frame_port_receives_the_candle_frame(self, reg, candles):
        """``feat_price_transform`` needs open/high/low/close, not one series.

        ``hlc3`` is (high + low + close) / 3. Fed only ``close`` it would be ``close``
        itself, which is a value that looks entirely reasonable on a chart.
        """
        transform = _node(reg, "feat_price_transform", transform="hlc3")
        graph, _ids = feature_graph(
            reg, [transform], {transform.id: ("frame", "frame")}
        )
        engine, _result = run(reg, graph, candles)

        produced = engine.node_outputs[(transform.id, "matrix")]
        expected = FeatureEngine.compute_price_transform(
            np.asarray(candles["open"], dtype=float),
            np.asarray(candles["high"], dtype=float),
            np.asarray(candles["low"], dtype=float),
            np.asarray(candles["close"], dtype=float),
            transform="hlc3",
        )
        np.testing.assert_allclose(
            produced.column("price_transform_hlc3"), expected, equal_nan=True
        )
        assert not np.allclose(
            produced.column("price_transform_hlc3"),
            np.asarray(candles["close"], dtype=float),
        ), "hlc3 collapsed onto close, so the frame port delivered a single series"

    def test_one_upstream_node_can_feed_two_ports_of_one_target(self, reg, candles):
        """``close -> gt.left`` and ``close -> gt.right`` are two inputs, not one.

        The id-keyed view collapses them - it always did - so this is exactly the case
        port addressing has to keep. Both ports resolve to the same value here, and the
        point is that both ports *exist*.
        """
        edges = [
            {"id": "e1", "source": "d", "source_port": "close", "target": "g", "target_port": "left"},
            {"id": "e2", "source": "d", "source_port": "close", "target": "g", "target_port": "right"},
        ]
        inputs = resolve_node_inputs("g", edges, {"d": candles["close"]}, {})

        assert sorted(inputs.ports()) == ["left", "right"]
        assert len(inputs) == 1, "the legacy id-keyed view still collapses by source"
        assert inputs.port("left") is inputs.port("right") is candles["close"]

    def test_an_edge_naming_no_target_port_is_not_assigned_a_guessed_one(self, candles):
        """A legacy payload keeps its legacy behaviour and gains no invented port.

        Guessing a port here would be guessing an operand, and a guessed operand is a
        different strategy running.
        """
        edges = [{"id": "e", "source": "d", "target": "g"}]
        inputs = resolve_node_inputs("g", edges, {"d": candles["close"]}, {})

        assert inputs.ports() == ()
        assert inputs["d"] is candles["close"]


class TestPortInputs:
    """The container an executor receives."""

    def test_it_is_still_a_plain_dict_keyed_by_upstream_node_id(self):
        """Every pre-existing executor reads it this way, including in-place mutation."""
        series = pd.Series([1.0, 2.0])
        inputs = PortInputs({"a": series}, {"left": ["a"]})

        assert isinstance(inputs, dict)
        assert list(inputs.items()) == [("a", series)]
        assert inputs["a"] is series

    def test_a_mutation_through_the_id_view_is_visible_through_the_port_view(self):
        """One value, two views - never two copies that can drift.

        ``validate_node_inputs`` back-fills a NaN warmup in place
        (``inputs[input_id] = value.bfill()``), so a copied port map would hand the port
        view the stale array.
        """
        inputs = PortInputs({"a": pd.Series([np.nan, 2.0])}, {"series": ["a"]})
        inputs["a"] = inputs["a"].bfill()

        assert inputs.port("series").iloc[0] == 2.0

    def test_a_variadic_port_keeps_every_connection(self):
        first, second = FeatureMatrix(index=[1], columns=["a"], values=[[1.0]]), FeatureMatrix(
            index=[1], columns=["b"], values=[[2.0]]
        )
        inputs = PortInputs({"x": first, "y": second}, {"matrix": ["x", "y"]})

        assert inputs.port_values("matrix") == (first, second)

    def test_reading_a_multiply_fed_port_as_a_single_value_is_refused(self):
        """Silently taking the first would drop the author's other inputs."""
        inputs = PortInputs({"x": 1, "y": 2}, {"matrix": ["x", "y"]})

        with pytest.raises(DAGExecutionError) as excinfo:
            inputs.port("matrix")
        assert "2 connections" in str(excinfo.value)

    def test_an_unfed_port_returns_the_default(self):
        assert PortInputs({}, {}).port("series", default="unfed") == "unfed"


# ---------------------------------------------------------------------------
# Requirement 20.2 - outputs keyed by output port name
# ---------------------------------------------------------------------------


class TestOutputsAreKeyedByPort:
    def test_a_data_node_publishes_every_declared_output_port(self, reg, candles):
        lag = _node(reg, "feat_lag", lags=[1])
        graph, ids = feature_graph(reg, [lag], {lag.id: ("close", "series")})
        engine, _result = run(reg, graph, candles)

        declared = [port.name for port in reg["ohlcv_feed"].outputs]
        for port_name in declared:
            key = (ids["data"], port_name)
            assert key in engine.node_outputs, f"port '{port_name}' was not published"

        for column in ("open", "high", "low", "close", "volume"):
            np.testing.assert_allclose(
                np.asarray(engine.node_outputs[(ids["data"], column)], dtype=float),
                np.asarray(candles[column], dtype=float),
            )
        assert engine.node_outputs[(ids["data"], "frame")] is candles

    def test_the_legacy_primary_view_is_unchanged(self, reg, candles):
        """``node_results[node_id]`` still holds what it always held.

        ``ohlcv_feed`` declares ``frame`` first, but the engine has always returned
        ``close`` for a DATA node and ``tests/test_dag_runtime_golden_plan.py`` pins that
        series. Publishing the other five ports must not move it - which is why
        :class:`NodeOutputs` carries an explicit ``primary`` rather than assuming the
        first declared port.
        """
        lag = _node(reg, "feat_lag", lags=[1])
        graph, ids = feature_graph(reg, [lag], {lag.id: ("close", "series")})
        engine, _result = run(reg, graph, candles)

        np.testing.assert_allclose(
            np.asarray(engine.node_results[ids["data"]], dtype=float),
            np.asarray(candles["close"], dtype=float),
        )

    def test_node_outputs_is_reset_between_runs(self, reg, candles):
        """A second run must not read the first run's values."""
        lag = _node(reg, "feat_lag", lags=[1])
        graph, _ids = feature_graph(reg, [lag], {lag.id: ("close", "series")})
        report = V.validate(graph, reg)
        assert report.valid
        plan = SC.compile_graph(graph, reg)
        nodes, edges = SC.plan_to_engine_graph(plan, reg)

        engine = DAGEngine(enable_event_buffer=False)
        engine.execute_dag(nodes, edges, candles)
        first = dict(engine.node_outputs)
        engine.execute_dag(nodes, edges, candles.iloc[:60])

        assert len(engine.node_outputs) == len(first)
        assert engine.node_outputs[(lag.id, "matrix")].n_rows == 60

    def test_a_primary_port_absent_from_the_produced_ports_is_refused(self):
        with pytest.raises(DAGExecutionError):
            NodeOutputs(by_port={"matrix": 1}, primary="value")


class TestOutputsAreValidatedBeforeEnteringTheValueMap:
    """Requirement 20.2: validated *before* the value is available downstream."""

    def test_a_wrong_length_output_is_refused_and_nothing_is_published(
        self, reg, candles
    ):
        lag = _node(reg, "feat_lag", lags=[1])
        graph, ids = feature_graph(reg, [lag], {lag.id: ("close", "series")})
        report = V.validate(graph, reg)
        assert report.valid
        plan = SC.compile_graph(graph, reg)
        nodes, edges = SC.plan_to_engine_graph(plan, reg)

        class TruncatingExecutor:
            def execute(self, node, inputs, market_data):
                return market_data["close"].iloc[:-1]

        engine = DAGEngine(enable_event_buffer=False)
        engine.executors["market_data"] = TruncatingExecutor()

        with pytest.raises(DAGExecutionError) as excinfo:
            engine.execute_dag(nodes, edges, candles)

        assert "length mismatch" in str(excinfo.value)
        assert not any(
            node_id == ids["data"] for node_id, _port in engine.node_outputs
        ), "a value that failed its port contract must not be published"

    def test_a_feature_matrix_port_refuses_a_bare_series(self, reg, candles):
        """A ``FEATURE_MATRIX`` payload must carry its own timestamp index.

        A bare series has no index to align on, so a downstream ``feat_concat`` could
        only fall back to row position - the failure mode the contract exists to prevent.
        """
        port = reg["feat_lag"].output_port("matrix")

        with pytest.raises(DAGExecutionError) as excinfo:
            validate_port_output(
                candles["close"], "n_1", port, len(candles), candles.index
            )
        assert "FEATURE_MATRIX" in str(excinfo.value)

    def test_an_infinite_value_is_refused_on_a_series_port(self, reg, candles):
        """Requirement 20.3: results hold finite values or NaN, never an infinity."""
        port = reg["rsi"].output_port("value")
        poisoned = pd.Series(np.full(len(candles), 1.0), index=candles.index)
        poisoned.iloc[3] = np.inf

        with pytest.raises(DAGExecutionError) as excinfo:
            validate_port_output(poisoned, "n_1", port, len(candles), candles.index)
        assert "infinite" in str(excinfo.value)

    def test_nan_is_permitted_on_a_series_port(self, reg, candles):
        """An undefined bar is NaN by contract: a warmup region, or division by zero.

        Rejecting it here would force the runtime to invent a number for a bar where it
        has none. The readiness gate (task 8.4) is what stops an unwarmed value reaching
        an order, not a blanket refusal of NaN.
        """
        port = reg["rsi"].output_port("value")
        warming = pd.Series(np.full(len(candles), np.nan), index=candles.index)
        warming.iloc[20:] = 55.0

        validate_port_output(warming, "n_1", port, len(candles), candles.index)

    def test_the_existing_output_validator_still_rejects_nan_by_default(self, candles):
        """``allow_nan`` is additive: the four pre-existing call sites are unchanged."""
        with pytest.raises(DAGExecutionError):
            validate_node_output(
                output=pd.Series(np.nan, index=candles.index),
                node_id="n_1",
                expected_length=len(candles),
                expected_index=candles.index,
            )


# ---------------------------------------------------------------------------
# Requirement 4.3 - FEATURE_ENGINEERING nodes execute through FEATURE_SPECS
# ---------------------------------------------------------------------------


class TestFeatureExecutorDelegates:
    def test_every_feature_spec_runtime_ref_resolves_through_the_descriptor(self, reg):
        """The executor's only way of computing anything.

        If a ``runtime_ref`` stopped resolving, the executor would have nothing to call -
        and the registry would already have failed startup (task 1.7). Asserted here as
        the precondition this executor depends on.
        """
        for spec in FEATURE_SPECS:
            descriptor = reg.get(spec.block_id)
            assert descriptor is not None, f"{spec.block_id} is not published"
            assert callable(descriptor.resolve_runtime()), spec.runtime_ref

    @pytest.mark.parametrize(
        "block_id,params,port,expected_columns",
        [
            ("feat_lag", {"lags": [1, 3]}, ("close", "series"), ["lag_1", "lag_3"]),
            ("feat_returns", {"periods": [1, 5]}, ("close", "series"), ["return_1", "return_5"]),
            ("feat_log_returns", {}, ("close", "series"), ["log_returns"]),
            ("feat_rolling_mean", {"window": 7}, ("close", "series"), ["rolling_mean_7"]),
            ("feat_rolling_std", {"window": 7, "ddof": 0}, ("close", "series"), ["rolling_std_7"]),
            ("feat_volatility", {"window": 9}, ("close", "series"), ["volatility_9"]),
            ("feat_momentum", {"windows": [4, 8]}, ("close", "series"), ["momentum_4", "momentum_8"]),
            ("feat_zscore", {"window": 10}, ("close", "series"), ["zscore_10"]),
            ("feat_normalize", {"window": 10, "method": "minmax"}, ("close", "series"), ["normalize_minmax_10"]),
            ("feat_time", {"parts": ["hour", "dow"]}, ("frame", "frame"), ["time_hour", "time_dow"]),
            ("feat_volume", {"window": 6}, ("volume", "volume"), ["volume_mean_6", "volume_std_6", "volume_ratio_6"]),
            ("feat_price_transform", {"transform": "ohlc4"}, ("frame", "frame"), ["price_transform_ohlc4"]),
        ],
    )
    def test_a_feature_node_produces_a_matrix_on_the_market_data_index(
        self, reg, candles, block_id, params, port, expected_columns
    ):
        """Every single-input feature block executes end to end.

        Column names are asserted because they are the model's input contract: a
        recorded ``feature_schema`` has to keep matching the graph that produced it, and
        an inspector preview (Requirement 24.8) shows them to the author.
        """
        node = _node(reg, block_id, **params)
        graph, _ids = feature_graph(reg, [node], {node.id: port})
        engine, _result = run(reg, graph, candles)

        matrix = engine.node_outputs[(node.id, "matrix")]
        assert isinstance(matrix, FeatureMatrix)
        assert matrix.columns == expected_columns
        assert matrix.n_rows == len(candles)
        np.testing.assert_array_equal(matrix.index, np.asarray(candles.index))

    def test_the_produced_values_are_the_published_runtimes_own_values(
        self, reg, candles
    ):
        """No feature formula is reimplemented in the engine.

        The expected array comes from calling ``FeatureEngine.compute_lag_features``
        directly. If the executor computed lags itself, this would be the test that
        caught the drift.
        """
        node = _node(reg, "feat_lag", lags=[1, 2, 5])
        graph, _ids = feature_graph(reg, [node], {node.id: ("close", "series")})
        engine, _result = run(reg, graph, candles)

        matrix = engine.node_outputs[(node.id, "matrix")]
        expected = FeatureEngine.compute_lag_features(
            np.asarray(candles["close"], dtype=float), [1, 2, 5]
        )
        for position, name in enumerate(["lag_1", "lag_2", "lag_5"]):
            np.testing.assert_allclose(
                matrix.column(name), expected[:, position], equal_nan=True
            )

    def test_column_provenance_names_the_producing_node(self, reg, candles):
        """Requirement 18.12: a feature can be traced back to the block that made it."""
        node = _node(reg, "feat_momentum", windows=[4, 8])
        graph, _ids = feature_graph(reg, [node], {node.id: ("close", "series")})
        engine, _result = run(reg, graph, candles)

        matrix = engine.node_outputs[(node.id, "matrix")]
        assert set(matrix.provenance.values()) == {node.id}
        assert set(matrix.provenance) == set(matrix.columns)

    def test_warmup_is_floored_at_the_descriptors_declared_lookback(self, reg, candles):
        """A row that came out non-NaN is not automatically a trustworthy row.

        ``build_feature_matrix`` derives warmup from leading NaNs, which catches a block
        whose declared lookback understates reality. This is the other direction: a
        rolling z-score over 10 bars is not trustworthy at row 0 merely because that row
        happened to be finite. The larger of the two is the only safe answer.
        """
        node = _node(reg, "feat_zscore", window=10, mode="expanding")
        graph, _ids = feature_graph(reg, [node], {node.id: ("close", "series")})
        engine, _result = run(reg, graph, candles)

        matrix = engine.node_outputs[(node.id, "matrix")]
        assert matrix.warmup_offset >= reg["feat_zscore"].warmup(
            {"window": 10, "mode": "expanding"}
        )

    def test_a_feature_matrix_input_port_is_addressed_by_port(self, reg, candles):
        """``feat_select`` reads its matrix off the ``matrix`` port and projects columns."""
        lag = _node(reg, "feat_lag", lags=[1, 2, 3])
        select = _node(reg, "feat_select", columns=["lag_1", "lag_3"])
        data = _node(
            reg,
            "ohlcv_feed",
            symbol="ETH/USDT",
            timeframe="5m",
            market_type="spot",
            mode="streaming",
        )
        model = _node(reg, "xgboost", **_required(reg["xgboost"]))
        gate = _node(reg, "gt", **_required(reg["gt"]))
        const = _node(reg, "constant", value=0.5)
        action = _node(reg, "action_buy_market", **_required(reg["action_buy_market"]))
        graph = StrategyGraph(
            nodes=[data, lag, select, model, gate, const, action],
            edges=[
                EdgeSpec.create(data.id, "close", lag.id, "series"),
                EdgeSpec.create(lag.id, "matrix", select.id, "matrix"),
                EdgeSpec.create(select.id, "matrix", model.id, "features"),
                EdgeSpec.create(model.id, "prediction", gate.id, "left"),
                EdgeSpec.create(const.id, "value", gate.id, "right"),
                EdgeSpec.create(gate.id, "out", action.id, "signal"),
            ],
        )
        engine, _result = run(reg, graph, candles)

        projected = engine.node_outputs[(select.id, "matrix")]
        assert projected.columns == ["lag_1", "lag_3"]

    def test_a_feature_matrix_port_fed_a_series_is_refused_by_name(self, reg, candles):
        """Named refusal, not a positional coercion."""
        descriptor = reg["feat_select"]
        executor = FeatureExecutor(fallback=None)
        node = {
            "id": "n_sel",
            "type": "feature",
            "block_id": "feat_select",
            "params": {"columns": ["lag_1"]},
        }
        inputs = PortInputs({"n_up": candles["close"]}, {"matrix": ["n_up"]})

        with pytest.raises(DAGExecutionError) as excinfo:
            executor.execute(node, inputs, candles)
        message = str(excinfo.value)
        assert "n_sel" in message and "matrix" in message
        assert descriptor is reg["feat_select"]

    def test_a_legacy_feature_dict_keeps_its_pre_task_behaviour(self, reg, candles):
        """A ``{"type": "feature"}`` dict carries no ``block_id``, so there is no
        descriptor to run and no port contract to satisfy. It goes to the fallback it
        reached before this task rather than failing on a lookup it was never written
        to satisfy.
        """
        nodes = [
            {"id": "d", "type": "input"},
            {"id": "f", "type": "feature", "label": "legacy feature"},
        ]
        edges = [{"id": "e", "source": "d", "target": "f"}]
        engine = DAGEngine(enable_event_buffer=False)
        engine.execute_dag(nodes, edges, candles)

        pd.testing.assert_series_equal(
            engine.node_results["f"], candles["close"], check_names=False
        )
        assert descriptor_for(nodes[1]) is None

    def test_feat_standardize_refuses_to_fit_a_scaler_without_a_train_split(
        self, reg, candles
    ):
        """``REVIEW_REQUIRED`` means refused, not quietly fitted on everything.

        ``compute_standardize`` demands an explicit train-split ``fit_range``, which a
        bar-by-bar runtime has no honest way to supply. The refusal surfaces as a named
        node failure - which is the correct outcome, because fitting on all rows would
        leak validation and test statistics into training.
        """
        executor = FeatureExecutor(fallback=None)
        node = {
            "id": "n_std",
            "type": "feature",
            "block_id": "feat_standardize",
            "params": {"fit_on": "train_split"},
        }
        upstream = FeatureMatrix(
            index=np.asarray(candles.index),
            columns=["lag_1"],
            values=np.asarray(candles["close"], dtype=float).reshape(-1, 1),
        )
        inputs = PortInputs({"n_up": upstream}, {"matrix": ["n_up"]})

        with pytest.raises(DAGExecutionError) as excinfo:
            executor.execute(node, inputs, candles)
        assert "n_std" in str(excinfo.value)
        assert "fit_range" in str(excinfo.value)


class TestVariadicFeatureConcat:
    """``feat_concat`` combines 2..N matrices, joined on the timestamp index."""

    @staticmethod
    def _matrix(candles, name, offset):
        """A one-column matrix warming for ``offset`` rows.

        Built through ``build_feature_matrix``, which derives the warmup from the
        leading NaNs actually present - the same path :class:`FeatureExecutor` uses, so
        the fixture cannot claim a warmup its data does not have.
        """
        from backend_app.backend.strategy_dag.feature_matrix import \
            build_feature_matrix

        values = np.asarray(candles["close"], dtype=float).copy()
        values[:offset] = np.nan
        return build_feature_matrix(
            index=np.asarray(candles.index), columns=[name], series=[values]
        )

    def _concat_node(self):
        return {
            "id": "n_cat",
            "type": "feature",
            "block_id": "feat_concat",
            "params": {},
        }

    def test_both_connections_on_the_variadic_port_reach_the_runtime(self, candles):
        """Two edges into one variadic port are two operands, not one.

        Delegation is to ``FeatureEngine.concat_feature_matrices``, which routes to
        ``concat_matrices`` - the one join, timestamp-addressed, with no positional mode
        to opt into (Requirement 18.13).
        """
        first = self._matrix(candles, "a", 3)
        second = self._matrix(candles, "b", 7)
        inputs = PortInputs(
            {"n_a": first, "n_b": second}, {"matrix": ["n_a", "n_b"]}
        )

        produced = FeatureExecutor(fallback=None).execute(
            self._concat_node(), inputs, candles
        )
        matrix = produced.by_port["matrix"]

        assert matrix.columns == ["a", "b"]
        assert matrix.warmup_offset == 7, "the matrix-level warmup is the worst column's"
        np.testing.assert_array_equal(matrix.index, np.asarray(candles.index))

    def test_a_single_connection_is_refused_rather_than_concatenated_alone(
        self, candles
    ):
        """A one-input "concat" is not a concat, and that is now the *only* reason it raises.

        This assertion used to stand in for a plan-side defect: ``CompiledPlan.inbound`` was
        keyed ``target_port -> one edge``, so a variadic port fed twice reached the engine as
        one edge and every ``feat_concat`` was effectively one-input. That is fixed - the plan
        carries every edge on a port (task 5.0), and
        ``test_two_connections_survive_the_plan_and_reach_the_engine`` runs a genuine
        two-input concat end to end.

        What remains is the block's own arity contract: ``feat_concat`` declares 2..N inputs,
        so a graph that wires exactly one is refused by name rather than passed through as a
        one-column matrix a model would then be trained on. The refusal is deliberate and
        stays.
        """
        inputs = PortInputs(
            {"n_a": self._matrix(candles, "a", 3)}, {"matrix": ["n_a"]}
        )

        with pytest.raises(DAGExecutionError) as excinfo:
            FeatureExecutor(fallback=None).execute(
                self._concat_node(), inputs, candles
            )
        assert "n_cat" in str(excinfo.value)

    def test_two_connections_survive_the_plan_and_reach_the_engine(self, reg, candles):
        """The end-to-end case the plan used to make impossible.

        Two feature nodes on ``feat_concat``'s one variadic port, through the real
        validator, the real compiler, ``plan_to_engine_graph`` and the real ``DAGEngine``.
        Before task 5.0 the plan kept one of the two edges (last-write-wins on
        ``inbound[node][port]``), so the engine saw a one-input concat and this graph failed
        at the arity check above - a two-input concat could not execute at all.

        Both halves are asserted: the plan and the engine payload each carry **two** edges
        into the port, and the produced matrix carries both upstream nodes' columns rather
        than one node's.
        """
        lag = _node(reg, "feat_lag", lags=[1, 2])
        rolling = _node(reg, "feat_rolling_mean", window=5)
        graph, ids = concat_graph(reg, [lag, rolling])

        report = V.validate(graph, reg)
        assert report.valid, f"the graph must validate: {report.codes()}"
        plan = SC.compile_graph(graph, reg)

        plan_edges = plan.inbound_edges(ids["concat"], "matrix")
        assert len(plan_edges) == 2, (
            "the plan dropped an operand on the variadic port: "
            f"{[edge.id for edge in plan_edges]}"
        )
        assert {edge.source for edge in plan_edges} == {lag.id, rolling.id}

        nodes, edges = SC.plan_to_engine_graph(plan, reg)
        engine_edges = [
            edge
            for edge in edges
            if edge["target"] == ids["concat"] and edge["target_port"] == "matrix"
        ]
        assert len(engine_edges) == 2, (
            "the adaptation emitted one edge for a port the plan says has two: "
            f"{engine_edges}"
        )

        engine = DAGEngine(enable_event_buffer=False)
        engine.execute_dag(nodes, edges, candles)

        produced = engine.node_outputs[(ids["concat"], "matrix")]
        upstream_columns = (
            engine.node_outputs[(lag.id, "matrix")].columns
            + engine.node_outputs[(rolling.id, "matrix")].columns
        )
        assert produced.columns == upstream_columns, (
            "the concat published one operand's columns rather than both: "
            f"{produced.columns} vs {upstream_columns}"
        )
        np.testing.assert_array_equal(produced.index, np.asarray(candles.index))


# ---------------------------------------------------------------------------
# Task 5.0 - a variadic port fed N times reaches the engine with N operands
# ---------------------------------------------------------------------------


class TestVariadicFanInReachesTheEngineIntact:
    """One variadic port, N connections, N operands - across the three categories.

    ``CompiledPlan.inbound`` used to be ``node_id -> target_port -> ONE edge``, built with
    last-write-wins. The validator's rule R7 only refuses a second edge on a *non*-variadic
    port, so a variadic port fed N times was a legal graph whose extra operands the plan
    then discarded with nothing raised: ``add`` returned one addend, ``and`` returned one
    condition, ``feat_concat`` published one matrix. Same silent-wrong-answer class as
    SB-05's dropped nodes.

    Each test below wires N > 1 edges onto one variadic port and asserts three things in
    sequence, because the operand can be lost at any of them: the plan keeps N edges, the
    engine payload carries N edges and N entries of ``input_order``, and the executed value
    is the kernel's answer over **all** N operands rather than over the subset the old shape
    would have kept. The expected value always comes from the block's own published runtime
    (``block_specs`` / ``FeatureEngine``); no arithmetic is restated here.
    """

    def _data(self, reg):
        return _node(
            reg,
            "ohlcv_feed",
            symbol="ETH/USDT",
            timeframe="5m",
            market_type="spot",
            mode="streaming",
        )

    def _comparator(self, reg, block_id, left, left_port, threshold_value):
        """``<left> <block_id> constant(threshold)``: one boolean condition and its nodes.

        A LOGIC node's own inputs are single-arity, so this is only scaffolding - it exists
        so the variadic ``and`` below is fed by real boolean producers rather than by
        anything invented here.
        """
        threshold = _node(reg, "constant", value=threshold_value)
        comparator = _node(reg, block_id, **_required(reg[block_id]))
        return (
            comparator,
            [threshold, comparator],
            [
                EdgeSpec.create(left.id, left_port, comparator.id, "left"),
                EdgeSpec.create(threshold.id, "value", comparator.id, "right"),
            ],
        )

    def _compile_and_adapt(self, reg, graph):
        report = V.validate(graph, reg)
        assert report.valid, f"the graph must validate: {report.codes()}"
        plan = SC.compile_graph(graph, reg)
        nodes, edges = SC.plan_to_engine_graph(plan, reg)
        return plan, nodes, edges

    @staticmethod
    def _assert_fan_in(
        plan, nodes, edges, node_id, port, expected_sources, total_operands=None
    ):
        """The places an operand can be lost between the graph and the kernel.

        ``total_operands`` is asserted against ``input_order`` only for the kernel-backed
        categories that carry one (MATH and LOGIC); a FEATURE node addresses its inputs by
        port rather than positionally, so for it the engine-edge count is the whole claim.
        Returns the adapted node so a caller can read its ``input_order``.
        """
        plan_edges = plan.inbound_edges(node_id, port)
        assert {edge.source for edge in plan_edges} == set(expected_sources), (
            f"the plan lost an operand on {node_id}.{port}: "
            f"{[edge.source for edge in plan_edges]}"
        )
        assert len(plan_edges) == len(expected_sources)

        on_port = [
            edge
            for edge in edges
            if edge["target"] == node_id and edge["target_port"] == port
        ]
        assert len(on_port) == len(expected_sources), (
            f"the adaptation emitted {len(on_port)} engine edges for a port the plan says "
            f"has {len(expected_sources)}"
        )

        engine_node = next(node for node in nodes if node["id"] == node_id)
        if total_operands is not None:
            order = engine_node["input_order"]
            assert len(order) == total_operands, (
                f"{node_id} reaches the engine with {len(order)} operands instead of "
                f"{total_operands}: {order}"
            )
        return engine_node

    def test_a_math_add_fed_twice_on_one_port_sums_every_addend(self, reg, candles):
        """MATH: ``add.a`` twice plus ``add.b`` is a three-term sum, not a two-term one.

        ``ohlcv_feed -> {rsi, ema} -> add.a`` (twice) with ``constant -> add.b``, then
        ``add -> gt -> action`` because MATH's declared successors do not include ACTION -
        the sink is scaffolding so the graph validates, not part of the claim.
        """
        from backend_app.backend.strategy_dag import block_specs as BS

        data = self._data(reg)
        rsi = _node(reg, "rsi", window=14)
        ema = _node(reg, "ema", window=20, source="close")
        offset = _node(reg, "constant", value=7.5)
        adder = _node(reg, "add", **_required(reg["add"]))
        action = _node(reg, "action_buy_market", **_required(reg["action_buy_market"]))
        sink, sink_nodes, sink_edges = self._comparator(reg, "gt", adder, "out", 50.0)

        edges = [
            EdgeSpec.create(data.id, "close", rsi.id, "series"),
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            # Both of these land on the SAME variadic port.
            EdgeSpec.create(rsi.id, "value", adder.id, "a"),
            EdgeSpec.create(ema.id, "value", adder.id, "a"),
            EdgeSpec.create(offset.id, "value", adder.id, "b"),
            *sink_edges,
            EdgeSpec.create(sink.id, "out", action.id, "signal"),
        ]
        graph = StrategyGraph(
            nodes=[data, rsi, ema, offset, adder, *sink_nodes, action], edges=edges
        )
        plan, nodes, engine_edges = self._compile_and_adapt(reg, graph)
        engine_node = self._assert_fan_in(
            plan, nodes, engine_edges, adder.id, "a", [rsi.id, ema.id], 3
        )

        results = DAGEngine(enable_event_buffer=False).execute_dag(
            nodes, engine_edges, candles
        )["node_results"]
        produced = np.asarray(results[adder.id], dtype=float)
        operands = [
            np.asarray(results[node_id], dtype=float)
            for node_id in engine_node["input_order"]
        ]
        assert len(operands) == 3

        np.testing.assert_allclose(
            produced, BS.math_add(*operands), equal_nan=True
        )
        # And not the two-term answer the last-write-wins plan produced: whichever of the
        # two edges on ``a`` survived, one addend was missing.
        for dropped in (0, 1):
            kept = [array for index, array in enumerate(operands) if index != dropped]
            assert not np.allclose(
                produced, BS.math_add(*kept), equal_nan=True
            ), "the sum is missing an addend, which is the defect task 5.0 removes"

    def test_a_logic_and_fed_twice_on_one_port_requires_every_condition(
        self, reg, candles
    ):
        """LOGIC: ``and.a`` twice plus ``and.b`` is a three-condition gate.

        The three conditions are chosen so that **none of them is implied by the others** on
        this fixture - ``close > 110`` and ``close < 130`` bound a band the rising close
        leaves on both sides, and ``rsi > 30`` is false through the RSI warmup. Nested
        thresholds (``rsi > 20`` and ``rsi > 60``) would make the AND of two equal the AND of
        three, and the assertion below would prove nothing.
        """
        from backend_app.backend.strategy_dag import block_specs as BS

        data = self._data(reg)
        rsi = _node(reg, "rsi", window=14)
        gate = _node(reg, "and", **_required(reg["and"]))
        action = _node(reg, "action_buy_market", **_required(reg["action_buy_market"]))

        above, above_nodes, above_edges = self._comparator(reg, "gt", data, "close", 110.0)
        below, below_nodes, below_edges = self._comparator(reg, "lt", data, "close", 130.0)
        warm, warm_nodes, warm_edges = self._comparator(reg, "gt", rsi, "value", 30.0)

        edges = [
            EdgeSpec.create(data.id, "close", rsi.id, "series"),
            *above_edges,
            *below_edges,
            *warm_edges,
            # Two of the three conditions land on the SAME variadic port.
            EdgeSpec.create(above.id, "out", gate.id, "a"),
            EdgeSpec.create(below.id, "out", gate.id, "a"),
            EdgeSpec.create(warm.id, "out", gate.id, "b"),
            EdgeSpec.create(gate.id, "out", action.id, "signal"),
        ]
        graph = StrategyGraph(
            nodes=[
                data,
                rsi,
                *above_nodes,
                *below_nodes,
                *warm_nodes,
                gate,
                action,
            ],
            edges=edges,
        )
        plan, nodes, engine_edges = self._compile_and_adapt(reg, graph)
        engine_node = self._assert_fan_in(
            plan, nodes, engine_edges, gate.id, "a", [above.id, below.id], 3
        )

        results = DAGEngine(enable_event_buffer=False).execute_dag(
            nodes, engine_edges, candles
        )["node_results"]
        operands = [
            np.asarray(results[node_id]) for node_id in engine_node["input_order"]
        ]
        assert len(operands) == 3

        produced = np.asarray(results[gate.id]).astype(bool)
        np.testing.assert_array_equal(
            produced, np.asarray(BS.logic_and(*operands)).astype(bool)
        )
        # And not the two-condition answer the last-write-wins plan produced: whichever of
        # the two edges on ``a`` survived, one condition was not being required.
        for dropped in (0, 1):
            kept = [array for index, array in enumerate(operands) if index != dropped]
            assert not np.array_equal(
                produced, np.asarray(BS.logic_and(*kept)).astype(bool)
            ), "the gate is true on bars a dropped condition forbids"

    def test_a_feat_concat_fed_three_times_publishes_every_matrix(self, reg, candles):
        """FEATURE: three matrices on one variadic port are three sets of columns.

        N > 2 on purpose: the plan's shape has to hold for the whole 2..N range the port
        declares, not just for the smallest case.
        """
        features = [
            _node(reg, "feat_lag", lags=[1, 2]),
            _node(reg, "feat_rolling_mean", window=5),
            _node(reg, "feat_rolling_std", window=4),
        ]
        graph, ids = concat_graph(reg, features)
        plan, nodes, engine_edges = self._compile_and_adapt(reg, graph)
        self._assert_fan_in(
            plan,
            nodes,
            engine_edges,
            ids["concat"],
            "matrix",
            [node.id for node in features],
        )

        engine = DAGEngine(enable_event_buffer=False)
        engine.execute_dag(nodes, engine_edges, candles)
        produced = engine.node_outputs[(ids["concat"], "matrix")]

        expected_columns = []
        for node in features:
            expected_columns.extend(engine.node_outputs[(node.id, "matrix")].columns)
        assert produced.columns == expected_columns, (
            "the concat published a subset of its operands' columns: "
            f"{produced.columns} vs {expected_columns}"
        )
        assert len(produced.columns) > len(
            engine.node_outputs[(features[0].id, "matrix")].columns
        ), "the assertion above would pass on a single-operand concat"


# ---------------------------------------------------------------------------
# Existing behaviour that must survive
# ---------------------------------------------------------------------------


class TestTracerAndEventBufferSurvive:
    def test_every_node_is_traced_with_its_type_and_duration(self, reg, candles):
        node = _node(reg, "feat_rolling_mean", window=5)
        graph, _ids = feature_graph(reg, [node], {node.id: ("close", "series")})
        engine, result = run(reg, graph, candles)

        trace = engine.get_execution_trace()
        assert {entry["node_id"] for entry in trace} == set(result["execution_order"])
        assert {entry["status"] for entry in trace} == {"success"}
        assert all(entry["execution_time_ms"] is not None for entry in trace)

        feature_entry = next(e for e in trace if e["node_id"] == node.id)
        assert feature_entry["node_type"] == "feature"

    def test_a_feature_matrix_output_is_traced_by_type_rather_than_dropped(
        self, reg, candles
    ):
        node = _node(reg, "feat_lag", lags=[1])
        graph, _ids = feature_graph(reg, [node], {node.id: ("close", "series")})
        engine, _result = run(reg, graph, candles)

        entry = next(
            e for e in engine.get_execution_trace() if e["node_id"] == node.id
        )
        assert entry["output_type"] == "FeatureMatrix"

    def test_a_node_failure_is_traced_and_re_raised(self, reg, candles):
        lag = _node(reg, "feat_lag", lags=[1])
        graph, ids = feature_graph(reg, [lag], {lag.id: ("close", "series")})
        report = V.validate(graph, reg)
        assert report.valid
        plan = SC.compile_graph(graph, reg)
        nodes, edges = SC.plan_to_engine_graph(plan, reg)

        class ExplodingExecutor:
            def execute(self, node, inputs, market_data):
                raise RuntimeError("boom")

        engine = DAGEngine(enable_event_buffer=False)
        engine.executors["market_data"] = ExplodingExecutor()

        with pytest.raises(RuntimeError):
            engine.execute_dag(nodes, edges, candles)

        failure = engine.get_last_failure()
        assert failure is not None
        assert failure["node_id"] == ids["data"]
        assert "boom" in failure["error_message"]

    def test_the_event_buffer_configuration_is_untouched(self):
        assert DAGEngine().enable_event_buffer is True
        assert DAGEngine(enable_event_buffer=False).enable_event_buffer is False
        assert DAGEngine()._event_buffer_key == "events:{tenant_id}"
        assert DAGEngine()._max_stream_length == 1000


# ---------------------------------------------------------------------------
# Property: the engine adds addressing, never arithmetic
# ---------------------------------------------------------------------------


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    bars=st.integers(min_value=12, max_value=90),
    lags=st.lists(
        st.integers(min_value=1, max_value=10), min_size=1, max_size=4, unique=True
    ),
)
def test_feat_lag_output_is_exactly_its_published_runtimes_output(bars, lags):
    """For any bar count and any lag set, the engine reproduces the runtime exactly.

    The property that keeps this executor honest: it may address, shape and annotate,
    but it must not compute. Any arithmetic drift between ``FeatureExecutor`` and
    ``FeatureEngine.compute_lag_features`` shows up as a value mismatch.

    Warmup is asserted as a *floor*, not an equality: the matrix contract may raise it
    above ``max(lags)`` when the produced column carries more leading NaNs than the
    declared lookback, and understating it is the only unsafe direction.
    """
    stamps = pd.date_range("2024-01-01", periods=bars, freq="1min")
    close = pd.Series(np.linspace(50.0, 50.0 + bars, bars), index=stamps)
    frame = pd.DataFrame({"close": close}, index=stamps)

    executor = FeatureExecutor(fallback=None)
    node = {
        "id": "n_lag",
        "type": "feature",
        "block_id": "feat_lag",
        "params": {"lags": sorted(lags)},
    }
    inputs = PortInputs({"n_d": close}, {"series": ["n_d"]})

    matrix = executor.execute(node, inputs, frame).by_port["matrix"]
    expected = FeatureEngine.compute_lag_features(
        np.asarray(close, dtype=float), sorted(lags)
    )

    assert matrix.n_rows == bars
    assert matrix.columns == [f"lag_{lag}" for lag in sorted(lags)]
    assert matrix.warmup_offset >= max(lags)
    for position, name in enumerate(matrix.columns):
        np.testing.assert_allclose(
            matrix.column(name), expected[:, position], equal_nan=True
        )
