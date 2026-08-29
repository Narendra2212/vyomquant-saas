"""
tests/test_task_9_4_limits_and_runtime_optimisations.py

The capacity limits and the two runtime optimisations.

Spec: strategy-builder task 9.4 (``design.md`` -> Performance, and DAG runtime contract).
Requirements 25.4, 25.6, 25.7.

Three subjects, and the last two are optimisations - which sets what has to be proved about
them. An optimisation that changes an answer is not an optimisation, so most of this file is
equivalence: the same plan over the same candles must produce the same node series, the same
node states, the same trace and the same intents whether a level ran on one thread or four,
and whether a node's value was computed or remembered.

1. **Capacity (25.4).** The six bounds are :data:`validator.LIMITS` - the table task 1.9
   built - and every refusal names the limit and its permitted value. What 9.4 adds is
   :func:`validator.capacity_issues`, the same rule callable on its own, and the measured
   feature-column count being *read out of the dataset statistics a caller already passes*
   so the 200-column bound is reachable on a real path. The single-source claim is asserted
   against both of task 9.1's readers.
2. **Level concurrency (25.6).** Genuine overlap is proved with a barrier: two nodes of one
   level must be inside their executors at the same time or the barrier times out. ML/DL and
   ACTION nodes are proved to stay on the calling thread.
3. **Memoisation (25.7).** Hits are proved by counters *and* by equality with a freshly
   computed run. The staleness traps are driven deliberately: a window that ends on the same
   bar but disagrees about an earlier one, an edit to an upstream node's params, an
   unfingerprintable frame, and a cache too small to hold the graph.

Nothing here weakens a control. The SYSTEM FREEZE ``tests/conftest.py`` installs
(``VYOMQUANT_MODE=safe``) is lifted only by the ``unfrozen`` fixture, per test, and the last
class asserts it still stops intents with both optimisations switched on.
"""

import hashlib
import os
import sys
import threading

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import metrics as M
from backend_app.backend import ml_training_policy as MTP
from backend_app.backend import strategy_compiler as SC
from backend_app.backend.dag_engine import (LEVEL_PARALLELISM_DEFAULT,
                                            SERIAL_EXECUTOR_TYPES, DAGEngine,
                                            NodeMemoKey, NodeResultCache,
                                            PlanRuntimeState, _level_pool,
                                            window_identity)
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.plan import CompiledPlan
from backend_app.backend.strategy_dag.schema import (EdgeSpec, NodeSpec,
                                                     StrategyGraph)

#: Long enough that every node of the fixtures below is warm (``ema(20)`` composes to 60).
LONG_BARS = 140


# ---------------------------------------------------------------------------
# Fixtures: the real registry, the real compiler, real candles
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg():
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


def candles(bars: int, *, first_close_bump: float = 0.0) -> pd.DataFrame:
    """A deterministic OHLCV frame, shaped like the closed-bar contract's output.

    ``first_close_bump`` edits the **first** bar only. That is the frame this file needs to
    catch a cache keyed on a bare window end: the last timestamp, the bar count and the
    columns are all identical, and every windowed indicator still moves.
    """
    stamps = pd.date_range("2024-01-01", periods=bars, freq="5min", name="timestamp")
    close = pd.Series(np.linspace(100.0, 180.0, bars), index=stamps)
    if first_close_bump:
        close.iloc[0] = close.iloc[0] + first_close_bump
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


def _diamond_graph(reg, *, rsi_window: int = 14, ema_window: int = 20) -> StrategyGraph:
    """``ohlcv_feed -> {rsi, ema} -> between -> action_buy_market``.

    A diamond on purpose: ``rsi`` and ``ema`` share a level and neither can see the other,
    which is the shape Requirement 25.6 is about. ``constant`` sits with the DATA node in
    level 0, so two levels of this plan hold more than one node.
    """
    return StrategyGraph(
        schema_version=2,
        strategy_id="s-9-4",
        version="1.0.0",
        name="task 9.4 diamond",
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
            _node(reg, "n_rsi", "rsi", window=rsi_window),
            _node(reg, "n_ema", "ema", window=ema_window, source="close"),
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


def _compile(reg, graph) -> CompiledPlan:
    report = V.validate(graph, reg)
    assert report.valid, f"the graph must validate: {report.codes()}"
    return SC.compile_graph(graph, reg)


@pytest.fixture(scope="module")
def diamond(reg) -> CompiledPlan:
    return _compile(reg, _diamond_graph(reg))


@pytest.fixture
def unfrozen(monkeypatch):
    """Lift the platform SYSTEM FREEZE for the intent-comparison tests only.

    ``tests/conftest.py`` sets ``VYOMQUANT_MODE=safe`` and ``execute_plan`` consults
    ``SafetyMonitor.check_execution_allowed('strategy_signal')`` before it builds any
    intent. That is the correct default; :class:`TestNoControlIsWeakened` asserts it holds
    with both optimisations on. It is lifted here explicitly, per test, never in conftest.
    """
    monkeypatch.setattr(
        "backend_app.backend.dag_engine.SafetyMonitor.check_execution_allowed",
        staticmethod(lambda operation: None),
    )


# ---------------------------------------------------------------------------
# Helpers: what "the same answer" means
# ---------------------------------------------------------------------------


def _value_digest(value) -> str:
    """A digest of one node's published value, whatever type the port carries."""
    try:
        return _hashable_digest(value)
    except TypeError:
        # A value pandas cannot hash (the object column one test deliberately adds) is
        # still comparable by its own repr, which is enough for an equivalence claim.
        return hashlib.sha256(repr(value).encode()).hexdigest()


def _hashable_digest(value) -> str:
    if isinstance(value, pd.Series):
        payload = pd.util.hash_pandas_object(value, index=True).to_numpy(dtype="uint64")
        return hashlib.sha256(
            str(value.dtype).encode() + np.ascontiguousarray(payload).tobytes()
        ).hexdigest()
    if isinstance(value, pd.DataFrame):
        payload = pd.util.hash_pandas_object(value, index=True).to_numpy(dtype="uint64")
        return hashlib.sha256(np.ascontiguousarray(payload).tobytes()).hexdigest()
    if isinstance(value, np.ndarray):
        return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()
    return hashlib.sha256(repr(value).encode()).hexdigest()


def evaluate(plan, window, *, reg, cache=None, parallel=None, engine=None):
    """One evaluation, plus everything an equivalence claim needs from it."""
    engine = engine if engine is not None else DAGEngine(enable_event_buffer=False)
    state = PlanRuntimeState()
    intents = engine.execute_plan(
        plan, window, state, registry=reg, cache=cache, parallel=parallel
    )
    return {
        "engine": engine,
        "state": state,
        "intents": intents,
        "node_states": dict(state.node_states),
        "results": {
            node_id: _value_digest(value)
            for node_id, value in engine.node_results.items()
        },
        "ports": {
            f"{node_id}.{port}": _value_digest(value)
            for (node_id, port), value in engine.node_outputs.items()
        },
        "trace_order": [
            entry["node_id"] for entry in engine.tracer.get_trace_as_dict()
        ],
        "trace_status": [
            entry["status"] for entry in engine.tracer.get_trace_as_dict()
        ],
    }


def assert_same_answer(left, right, subject: str) -> None:
    """The full equivalence claim, so a partial one cannot pass for it."""
    assert left["results"] == right["results"], f"{subject}: node values moved"
    assert left["ports"] == right["ports"], f"{subject}: published port values moved"
    assert left["node_states"] == right["node_states"], f"{subject}: node states moved"
    assert left["intents"] == right["intents"], f"{subject}: intents moved"
    assert left["trace_order"] == right["trace_order"], f"{subject}: trace order moved"
    assert left["trace_status"] == right["trace_status"], f"{subject}: trace status moved"


# ---------------------------------------------------------------------------
# Requirement 25.4: the capacity limits, and only one table of them
# ---------------------------------------------------------------------------


class TestTheCapacityLimits:
    """Six bounds, each refusal naming the limit and its permitted value."""

    def test_the_table_is_the_designs_table(self):
        assert V.LIMITS.max_nodes == 200
        assert V.LIMITS.max_edges == 400
        assert V.LIMITS.max_ml_nodes == 4
        assert V.LIMITS.max_feature_nodes == 40
        assert V.LIMITS.max_feature_columns == 200
        assert V.LIMITS.max_fan_in_per_variadic_port == 16

    def test_two_hundred_and_one_nodes_are_refused_by_name(self, reg):
        """The real 200 bound, not an injected small one."""
        nodes = [
            _node(reg, f"n_{index}", "constant", value=float(index))
            for index in range(201)
        ]
        issues = V.capacity_issues(StrategyGraph(nodes=nodes, edges=[]))
        by_code = {issue["code"]: issue for issue in issues}

        assert V.CODE_NODE_LIMIT_EXCEEDED in by_code
        issue = by_code[V.CODE_NODE_LIMIT_EXCEEDED]
        assert issue["expected"] == 200
        assert issue["actual"] == 201
        assert issue["field"] == "max_nodes"
        assert "Max nodes per strategy" in issue["message"]
        assert "200" in issue["message"] and "201" in issue["message"]

    def test_two_hundred_nodes_are_allowed(self, reg):
        nodes = [
            _node(reg, f"n_{index}", "constant", value=float(index))
            for index in range(200)
        ]
        assert V.capacity_issues(StrategyGraph(nodes=nodes, edges=[])) == ()

    def test_every_bound_names_its_permitted_value(self, reg):
        """One assertion shape over all six, so none can be the odd one out."""
        limits = V.GraphLimits(
            max_nodes=1,
            max_edges=1,
            max_ml_nodes=1,
            max_feature_nodes=1,
            max_feature_columns=1,
            max_fan_in_per_variadic_port=1,
        )
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
            _node(reg, "n_lag_a", "feat_lag", lags=[1]),
            _node(reg, "n_lag_b", "feat_lag", lags=[2]),
            _node(reg, "n_model_a", "xgboost"),
            _node(reg, "n_model_b", "lstm"),
            _node(reg, "n_c1", "constant", value=1.0),
            _node(reg, "n_c2", "constant", value=2.0),
            _node(reg, "n_and", "and"),
        ]
        edges = [
            EdgeSpec(id="c1", source="n_data", source_port="close", target="n_lag_a", target_port="series"),
            EdgeSpec(id="c2", source="n_data", source_port="close", target="n_lag_b", target_port="series"),
            EdgeSpec(id="c3", source="n_c1", source_port="value", target="n_and", target_port="a"),
            EdgeSpec(id="c4", source="n_c2", source_port="value", target="n_and", target_port="a"),
        ]
        issues = V.capacity_issues(
            StrategyGraph(nodes=nodes, edges=edges),
            limits=limits,
            descriptors={node.id: reg[node.block_id] for node in nodes},
            feature_columns=7,
        )
        by_code = {issue["code"]: issue for issue in issues}

        expected_codes = {
            V.CODE_NODE_LIMIT_EXCEEDED: ("max_nodes", 8),
            V.CODE_EDGE_LIMIT_EXCEEDED: ("max_edges", 4),
            V.CODE_ML_NODE_LIMIT_EXCEEDED: ("max_ml_nodes", 2),
            V.CODE_FEATURE_NODE_LIMIT_EXCEEDED: ("max_feature_nodes", 2),
            V.CODE_FEATURE_COLUMN_LIMIT_EXCEEDED: ("max_feature_columns", 7),
            V.CODE_PORT_FAN_IN_LIMIT_EXCEEDED: ("max_fan_in_per_variadic_port", 2),
        }
        assert set(expected_codes).issubset(by_code), sorted(by_code)
        for code, (field_name, actual) in expected_codes.items():
            issue = by_code[code]
            assert issue["severity"] == V.SEVERITY_ERROR, code
            assert issue["field"] == field_name, code
            assert issue["expected"] == 1, code
            assert issue["actual"] == actual, code
            assert V.LIMIT_LABELS[field_name] in issue["message"], code

    def test_seventeen_connections_on_one_variadic_port_are_refused(self, reg):
        """The real 16 bound on the real variadic port of ``and``."""
        sources = [
            _node(reg, f"n_c{index}", "constant", value=float(index))
            for index in range(17)
        ]
        gate = _node(reg, "n_and", "and")
        graph = StrategyGraph(
            nodes=sources + [gate],
            edges=[
                EdgeSpec(
                    id=f"f{index}",
                    source=source.id,
                    source_port="value",
                    target="n_and",
                    target_port="a",
                )
                for index, source in enumerate(sources)
            ],
        )
        issues = [
            issue
            for issue in V.capacity_issues(graph)
            if issue["code"] == V.CODE_PORT_FAN_IN_LIMIT_EXCEEDED
        ]
        assert len(issues) == 1
        assert issues[0]["expected"] == 16
        assert issues[0]["actual"] == 17
        assert "n_and.a" in issues[0]["message"]

    def test_the_standalone_check_and_the_pipeline_agree(self, reg):
        """One rule. ``capacity_issues`` is what stage 1 runs, not a second copy."""
        graph = _diamond_graph(reg)
        limits = V.GraphLimits(max_nodes=3, max_edges=2)

        standalone = V.capacity_issues(
            graph,
            limits=limits,
            descriptors={node.id: reg[node.block_id] for node in graph.nodes},
        )
        report = V.validate(graph, reg, limits=limits)
        pipeline = [
            issue
            for issue in report.errors
            if issue["code"]
            in {V.CODE_NODE_LIMIT_EXCEEDED, V.CODE_EDGE_LIMIT_EXCEEDED}
        ]

        assert [issue["message"] for issue in standalone] == [
            issue["message"] for issue in pipeline
        ]
        assert [issue["expected"] for issue in standalone] == [
            issue["expected"] for issue in pipeline
        ]

    def test_a_legal_graph_reports_nothing(self, reg):
        assert V.capacity_issues(_diamond_graph(reg)) == ()


class TestTheFeatureColumnCeilingIsReachable:
    """25.4's 200-column bound, from the statistics a caller already supplies."""

    def test_measured_statistics_supply_the_count(self, reg):
        graph = _diamond_graph(reg)
        stats = {"usable_rows": 5_000, "usable_feature_columns": 201, "label_horizon": 1}

        report = V.validate(graph, reg, ml_dataset_stats=stats)
        issues = [
            issue
            for issue in report.errors
            if issue["code"] == V.CODE_FEATURE_COLUMN_LIMIT_EXCEEDED
        ]

        assert len(issues) == 1
        assert issues[0]["expected"] == 200
        assert issues[0]["actual"] == 201
        assert "Max feature columns" in issues[0]["message"]

    def test_two_hundred_columns_are_allowed(self, reg):
        stats = {"usable_rows": 5_000, "usable_feature_columns": 200, "label_horizon": 1}
        report = V.validate(_diamond_graph(reg), reg, ml_dataset_stats=stats)
        assert not [
            issue
            for issue in report.errors
            if issue["code"] == V.CODE_FEATURE_COLUMN_LIMIT_EXCEEDED
        ]

    def test_an_explicit_count_still_wins(self, reg):
        """A caller that measured the matrix itself is not overridden by the stats."""
        report = V.validate(
            _diamond_graph(reg),
            reg,
            feature_columns=500,
            ml_dataset_stats={
                "usable_rows": 10,
                "usable_feature_columns": 3,
                "label_horizon": 1,
            },
        )
        issue = [
            issue
            for issue in report.errors
            if issue["code"] == V.CODE_FEATURE_COLUMN_LIMIT_EXCEEDED
        ]
        assert len(issue) == 1 and issue[0]["actual"] == 500

    @pytest.mark.parametrize(
        "stats", [None, object(), {"rows": 10}, {"usable_feature_columns": "many"}, []]
    )
    def test_an_unreadable_measurement_is_no_measurement(self, stats):
        """No count is invented, and nothing raises. A fabricated width would either
        refuse a legal graph or admit an illegal one."""
        assert V.measured_feature_columns(stats) is None

    def test_the_widest_matrix_in_a_multi_model_graph_binds(self):
        assert (
            V.measured_feature_columns(
                {"n_a": {"columns": 11}, "n_b": {"columns": 240}}
            )
            == 240
        )


class TestOneLimitTable:
    """Task 9.1's readers read :data:`LIMITS`; nobody restates the numbers."""

    def test_the_training_policy_reads_the_graph_ceiling(self):
        assert (
            MTP._platform_max_feature_columns() == V.LIMITS.max_feature_columns == 200
        )

    def test_the_metrics_node_buckets_top_out_at_the_node_limit(self):
        assert M.NODE_COUNT_BUCKETS[-1] == V.LIMITS.max_nodes

    def test_the_limit_labels_cover_every_field(self):
        fields = {
            "max_nodes",
            "max_edges",
            "max_ml_nodes",
            "max_feature_nodes",
            "max_feature_columns",
            "max_fan_in_per_variadic_port",
        }
        assert set(V.LIMIT_LABELS) == fields
        assert all(V.LIMITS.label(name) != name for name in fields)


# ---------------------------------------------------------------------------
# Requirement 25.7: memoisation that cannot change an answer
# ---------------------------------------------------------------------------


class TestMemoisation:
    """A remembered value is the value that would have been computed, or no value."""

    def test_the_key_is_the_designs_key(self):
        assert NodeMemoKey.__dataclass_fields__.keys() == {
            "node_id",
            "params_hash",
            "window_end",
        }

    def test_a_repeated_evaluation_is_remembered_and_identical(self, reg, diamond, unfrozen):
        window = candles(LONG_BARS)
        cache = NodeResultCache()

        first = evaluate(diamond, window, reg=reg, cache=cache, parallel=False)
        assert cache.stats()["hits"] == 0
        assert cache.stats()["stores"] == len(first["results"])

        second = evaluate(diamond, window, reg=reg, cache=cache, parallel=False)

        assert cache.stats()["hits"] == len(first["results"]) > 0
        assert_same_answer(first, second, "a remembered evaluation")

    @pytest.mark.parametrize(
        ("rsi_window", "ema_window", "bars"),
        [(14, 20, LONG_BARS), (2, 3, 90), (30, 10, 200), (9, 26, 121)],
    )
    def test_a_cached_run_equals_an_uncached_run(
        self, reg, unfrozen, rsi_window, ema_window, bars
    ):
        """**Validates: Requirements 25.7.** Over four shapes, twice each."""
        plan = _compile(reg, _diamond_graph(reg, rsi_window=rsi_window, ema_window=ema_window))
        window = candles(bars)
        cache = NodeResultCache()

        uncached = evaluate(plan, window, reg=reg, parallel=False)
        evaluate(plan, window, reg=reg, cache=cache, parallel=False)
        from_cache = evaluate(plan, window, reg=reg, cache=cache, parallel=False)

        assert cache.hits > 0, "nothing was remembered, so nothing was proved"
        assert_same_answer(uncached, from_cache, "a cache hit")

    def test_the_same_window_end_over_different_history_is_not_a_hit(self, reg, diamond, unfrozen):
        """The trap the design's key would fall into on its own.

        Both frames hold 140 bars, end on the same timestamp and carry the same columns.
        Only the *first* bar differs - and every windowed indicator in this plan moves with
        it, because ``ema(20)`` is seeded from the first 20 closes.
        """
        original = candles(LONG_BARS)
        edited = candles(LONG_BARS, first_close_bump=25.0)
        assert original.index[-1] == edited.index[-1]
        assert len(original) == len(edited)
        assert not original["close"].equals(edited["close"])

        cache = NodeResultCache()
        evaluate(diamond, original, reg=reg, cache=cache, parallel=False)
        hits_after_first = cache.hits

        served = evaluate(diamond, edited, reg=reg, cache=cache, parallel=False)
        fresh = evaluate(diamond, edited, reg=reg, parallel=False)

        assert cache.hits == hits_after_first, "a stale window was served"
        assert cache.window_changes == 1
        assert_same_answer(fresh, served, "a window that only shares its last bar")
        assert served["results"]["n_ema"] != _value_digest(original["close"].ewm(span=20).mean())

    def test_an_upstream_edit_misses_downstream_and_hits_elsewhere(self, reg, unfrozen):
        """``n_gate``'s own params did not change; the value feeding it did."""
        window = candles(LONG_BARS)
        cache = NodeResultCache()
        base = _compile(reg, _diamond_graph(reg, rsi_window=14))
        evaluate(base, window, reg=reg, cache=cache, parallel=False)

        edited = _compile(reg, _diamond_graph(reg, rsi_window=9))
        hits_before = cache.hits
        served = evaluate(edited, window, reg=reg, cache=cache, parallel=False)
        fresh = evaluate(edited, window, reg=reg, parallel=False)

        assert_same_answer(fresh, served, "an edited upstream node")
        # ``n_data``, ``n_floor`` and ``n_ema`` are untouched and are hits; ``n_rsi``,
        # ``n_gate`` and ``n_buy`` are the edit and its downstream closure.
        assert cache.hits - hits_before == 3

    def test_an_unfingerprintable_window_is_simply_not_cached(self, reg, diamond, unfrozen):
        window = candles(LONG_BARS)
        window["annotation"] = [{"unhashable": index} for index in range(len(window))]
        assert window_identity(window) is None

        cache = NodeResultCache()
        first = evaluate(diamond, window, reg=reg, cache=cache, parallel=False)
        second = evaluate(diamond, window, reg=reg, cache=cache, parallel=False)

        assert cache.stats()["entries"] == 0
        assert cache.hits == 0
        assert_same_answer(first, second, "an unfingerprintable window")

    def test_a_cache_too_small_for_the_graph_still_answers_correctly(self, reg, diamond, unfrozen):
        cache = NodeResultCache(max_entries=2)
        window = candles(LONG_BARS)

        uncached = evaluate(diamond, window, reg=reg, parallel=False)
        evaluate(diamond, window, reg=reg, cache=cache, parallel=False)
        served = evaluate(diamond, window, reg=reg, cache=cache, parallel=False)

        assert cache.evictions > 0
        assert cache.stats()["entries"] <= 2
        assert_same_answer(uncached, served, "an evicting cache")

    def test_a_hit_hands_out_a_copy(self, reg, diamond):
        """A consumer that edits a remembered series cannot corrupt the next hit."""
        window = candles(LONG_BARS)
        cache = NodeResultCache()
        evaluate(diamond, window, reg=reg, cache=cache, parallel=False)

        first = evaluate(diamond, window, reg=reg, cache=cache, parallel=False)
        series = first["engine"].node_results["n_ema"]
        assert isinstance(series, pd.Series)
        series.iloc[:] = -999.0

        second = evaluate(diamond, window, reg=reg, cache=cache, parallel=False)
        assert second["results"]["n_ema"] == first["results"]["n_ema"] != _value_digest(series)

    def test_no_cache_means_no_behaviour_change(self, reg, diamond, unfrozen):
        window = candles(LONG_BARS)
        left = evaluate(diamond, window, reg=reg, parallel=False)
        right = evaluate(diamond, window, reg=reg, cache=None, parallel=False)
        assert_same_answer(left, right, "no cache at all")

    def test_a_new_window_empties_the_cache_rather_than_growing_it(self, reg, diamond):
        cache = NodeResultCache()
        evaluate(diamond, candles(LONG_BARS), reg=reg, cache=cache, parallel=False)
        entries_after_one_window = cache.stats()["entries"]

        evaluate(diamond, candles(LONG_BARS + 1), reg=reg, cache=cache, parallel=False)

        assert cache.stats()["entries"] == entries_after_one_window
        assert cache.window_changes == 1
        assert cache.stats()["window_end"] == str(candles(LONG_BARS + 1).index[-1])


class TestWindowIdentity:
    """The fingerprint the memo scope rests on."""

    def test_identical_frames_share_an_identity(self):
        assert window_identity(candles(60)) == window_identity(candles(60))

    @pytest.mark.parametrize(
        "other",
        [
            candles(61),
            candles(60, first_close_bump=0.5),
            candles(60).rename(columns={"volume": "vol"}),
            candles(60).assign(extra=1.0),
        ],
    )
    def test_any_difference_is_a_different_identity(self, other):
        assert window_identity(candles(60)) != window_identity(other)

    @pytest.mark.parametrize("frame", [None, "not a frame", pd.DataFrame()])
    def test_nothing_identifiable_is_no_identity(self, frame):
        assert window_identity(frame) is None


# ---------------------------------------------------------------------------
# Requirement 25.6: the nodes of one level, concurrently
# ---------------------------------------------------------------------------


class _BarrierExecutor:
    """Wraps an executor so two nodes must be inside it at the same time.

    The proof of concurrency, rather than a proxy for it: a serial evaluation cannot get
    two callers to the barrier, so ``wait`` times out and the test fails loudly instead of
    passing on a timing coincidence.
    """

    def __init__(self, inner, barrier):
        self.inner = inner
        self.barrier = barrier
        self.threads = []

    def execute(self, node, inputs, market_data):
        self.threads.append(threading.get_ident())
        self.barrier.wait(timeout=15)
        return self.inner.execute(node, inputs, market_data)


class _ThreadRecordingExecutor:
    """Wraps an executor and records the thread each node ran on."""

    def __init__(self, inner):
        self.inner = inner
        self.threads = {}

    def execute(self, node, inputs, market_data):
        self.threads[node["id"]] = threading.get_ident()
        return self.inner.execute(node, inputs, market_data)


class TestLevelConcurrency:
    """``execution_levels`` drives the pool ``dag_engine_parallel`` owns."""

    def test_the_pool_is_the_parallel_engines_own(self):
        """Not a pool of this module's making: the object is ``dag_engine_parallel``'s.

        ``get_parallel_engine()`` is deliberately **not** what is called, and this test
        records why: ``ParallelDAGEngine()`` with no arguments raises, because its default
        worker count reads ``threading.cpu_count`` - a name that does not exist. The engine
        is therefore constructed with an explicit count instead of the module being edited.
        """
        from concurrent.futures import ThreadPoolExecutor

        from backend_app.backend import dag_engine as DE
        from backend_app.backend.dag_engine_parallel import (ParallelDAGEngine,
                                                             get_parallel_engine)

        pool = _level_pool()
        assert isinstance(pool, ThreadPoolExecutor)
        assert isinstance(DE._LEVEL_ENGINE, ParallelDAGEngine)
        assert DE._LEVEL_ENGINE.max_workers == DE.LEVEL_POOL_WORKERS >= 2
        assert _level_pool() is pool, "one pool per process, not one per call"

        with pytest.raises(AttributeError, match="cpu_count"):
            get_parallel_engine()

    def test_the_plans_levels_are_what_is_driven(self, diamond):
        """Two levels of this plan hold more than one node, so there is work to overlap."""
        assert [sorted(level) for level in diamond.execution_levels[:2]] == [
            ["n_data", "n_floor"],
            ["n_ema", "n_rsi"],
        ]

    def test_two_nodes_of_one_level_are_inside_their_executors_together(self, reg, diamond):
        engine = DAGEngine(enable_event_buffer=False)
        barrier = threading.Barrier(2)
        wrapper = _BarrierExecutor(engine.executors["indicator"], barrier)
        engine.executors["indicator"] = wrapper

        result = evaluate(diamond, candles(LONG_BARS), reg=reg, parallel=True, engine=engine)

        assert not barrier.broken, "the two indicator nodes never overlapped"
        assert len(set(wrapper.threads)) == 2
        assert threading.get_ident() not in wrapper.threads
        assert engine.level_parallelism["levels_concurrent"] >= 1
        assert engine.level_parallelism["nodes_concurrent"] >= 2
        assert result["state"].node_states["n_gate"] == "READY"

    def test_a_concurrent_level_produces_the_sequential_answer(self, reg, diamond, unfrozen):
        window = candles(LONG_BARS)
        sequential = evaluate(diamond, window, reg=reg, parallel=False)
        concurrent = evaluate(diamond, window, reg=reg, parallel=True)
        assert_same_answer(concurrent, sequential, "a concurrently evaluated level")

    def test_sequential_evaluation_uses_no_pool(self, reg, diamond):
        engine = DAGEngine(enable_event_buffer=False)
        evaluate(diamond, candles(LONG_BARS), reg=reg, parallel=False, engine=engine)
        assert engine.level_parallelism["levels_concurrent"] == 0
        assert engine.level_parallelism["nodes_concurrent"] == 0
        assert engine.level_parallelism["levels"] > 0

    def test_action_nodes_stay_on_the_calling_thread(self, reg, diamond, unfrozen):
        """:data:`SERIAL_EXECUTOR_TYPES`: the kill-switch path keeps one caller."""
        engine = DAGEngine(enable_event_buffer=False)
        action = _ThreadRecordingExecutor(engine.executors["action"])
        indicators = _ThreadRecordingExecutor(engine.executors["indicator"])
        engine.executors["action"] = action
        engine.executors["indicator"] = indicators

        evaluate(diamond, candles(LONG_BARS), reg=reg, parallel=True, engine=engine)

        assert action.threads["n_buy"] == threading.get_ident()
        assert set(indicators.threads) == {"n_rsi", "n_ema"}
        assert "ml" in SERIAL_EXECUTOR_TYPES and "action" in SERIAL_EXECUTOR_TYPES

    def test_a_failure_inside_a_concurrent_level_is_still_that_nodes_failure(
        self, reg, diamond
    ):
        engine = DAGEngine(enable_event_buffer=False)
        real = engine.executors["indicator"]

        class _Exploding:
            def execute(self, node, inputs, market_data):
                if node["id"] == "n_ema":
                    raise RuntimeError("ema exploded")
                return real.execute(node, inputs, market_data)

        engine.executors["indicator"] = _Exploding()

        with pytest.raises(RuntimeError, match="ema exploded"):
            engine.execute_plan(
                diamond, candles(LONG_BARS), registry=reg, parallel=True
            )

        failures = engine.tracer.failures_as_dict()
        assert [entry["node_id"] for entry in failures] == ["n_ema"]
        assert failures[0]["error_message"] == "ema exploded"
        assert ("n_ema", "value") not in engine.node_outputs

    def test_the_default_is_declared_once_and_overridable_per_call(self, reg, diamond):
        assert isinstance(LEVEL_PARALLELISM_DEFAULT, bool)
        engine = DAGEngine(enable_event_buffer=False)
        evaluate(diamond, candles(LONG_BARS), reg=reg, parallel=None, engine=engine)
        expected = 2 if LEVEL_PARALLELISM_DEFAULT else 0
        assert engine.level_parallelism["levels_concurrent"] == expected


class TestBothOptimisationsTogether:
    """Neither optimisation may change the other's answer."""

    def test_memoised_and_concurrent_equals_plain(self, reg, diamond, unfrozen):
        window = candles(LONG_BARS)
        plain = evaluate(diamond, window, reg=reg, parallel=False)

        cache = NodeResultCache()
        evaluate(diamond, window, reg=reg, cache=cache, parallel=True)
        optimised = evaluate(diamond, window, reg=reg, cache=cache, parallel=True)

        assert cache.hits > 0
        assert_same_answer(plain, optimised, "memoised and concurrent")


# ---------------------------------------------------------------------------
# Nothing was weakened
# ---------------------------------------------------------------------------


class TestNoControlIsWeakened:
    """The controls this task must not touch, asserted with both optimisations on."""

    def test_the_system_freeze_still_stops_every_intent(self, reg, diamond):
        """No ``unfrozen`` fixture here: ``VYOMQUANT_MODE=safe`` is in force."""
        cache = NodeResultCache()
        window = candles(LONG_BARS)
        evaluate(diamond, window, reg=reg, cache=cache, parallel=True)
        result = evaluate(diamond, window, reg=reg, cache=cache, parallel=True)

        assert result["intents"] == []
        assert result["state"].node_states["n_buy"] == "READY"

    def test_a_remembered_node_is_still_a_gated_node(self, reg, diamond):
        """A warming node is not executed, so it is not remembered either - the readiness
        gate runs before the cache is consulted, on every evaluation."""
        cache = NodeResultCache()
        short = evaluate(diamond, candles(30), reg=reg, cache=cache, parallel=True)

        assert short["state"].node_states["n_ema"] == "WARMING"
        assert "n_ema" not in short["results"]
        assert short["intents"] == []

    def test_the_intent_path_still_names_no_venue(self, reg, diamond, unfrozen):
        cache = NodeResultCache()
        window = candles(LONG_BARS)
        evaluate(diamond, window, reg=reg, cache=cache, parallel=True)
        result = evaluate(diamond, window, reg=reg, cache=cache, parallel=True)

        text = repr(result["intents"]) + repr(cache.stats())
        for forbidden in ("binance", "bybit", "api_key", "secret", "passphrase"):
            assert forbidden not in text.lower()
