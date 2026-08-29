"""tests/test_task_9_6_performance_budgets.py - the harness the budgets are measured with.

Spec: strategy-builder task 9.6. Requirements 25.1, 25.2, 25.3; ``design.md`` -> Testing
strategy -> Performance ("Compile latency for graphs of 10 / 50 / 100 / 200 nodes",
"Validation latency under the debounce path", "Registry response size and cache-hit ratio")
and ``design.md`` -> Performance for the two stated numbers.

WHY THIS FILE HOLDS NO WALL-CLOCK ASSERTION
-------------------------------------------
The p95 measurements live in ``tests/perf/test_strategy_builder_budgets.py``, which
``pytest.ini`` keeps out of the default lane. That is a deliberate split, and the reason is
not that the measurement is slow - it takes seconds, unlike task 7.6's 24 h feed experiment -
but that a wall-clock comparison on a shared runner fails for reasons that have nothing to do
with the code under test. The backend suite carries a **pinned** failure ceiling that every
task from 8.6 onward reconciles against; a timing assertion that flakes would make that
ceiling meaningless, and "the build is red because the runner was busy" is how a real budget
regression gets ignored.

So the default lane gets everything about the budgets that is *deterministic*:

* the measurement points, taken from ``metrics.NODE_COUNT_BUCKETS`` rather than restated;
* the graphs themselves - that they are the sizes they claim, that they **validate**, and
  that the 200-node one sits exactly on Requirement 25.4's legal ceiling, so the latency file
  is timing compilation rather than timing a refusal;
* the percentile machinery - that the two metrics the budgets are read from are ``Summary``
  and that their p95 is a nearest-rank percentile over samples rather than a bucket edge;
* the registry cache-hit ratio (Requirement 25.3), which is a counter ratio and needs no
  clock;
* that the latency lane is excluded on purpose and still reachable.

The graph builders live here, and the latency file imports them, so there is one definition
of "a 200-node graph" across both lanes.

WHAT IS NOT MEASURED AGAIN HERE
-------------------------------
The **registry response size** half of Requirement 25.3 was measured by task 9.5 from the
bytes actually served: ``/registry/blocks`` is 15 863 gzipped bytes, 6.2% of the 250 KB
ceiling, with the four projections between 304 B and 4 651 B. That measurement, its
``compresslevel`` justification and its byte-for-byte identity check are in
``tests/test_task_9_5_registry_budget.py``; the "fetched once per session" client half is in
``algo22-terminal/tests/unit/registryBudget.test.js`` (twelve reads, two requests). None of
it is re-derived here - a second size measurement with a second methodology would give the
budget two answers. What this file adds is the **ratio**, which 9.5 asserted only as a pair
of counter values for a single 304 and which ``design.md``'s observability table names as the
cache-effectiveness signal.

The ceiling constant is imported from 9.5's module rather than restated, so the two files
cannot come to disagree about what 250 KB means.
"""

import ast
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from backend_app.backend import metrics as M
from backend_app.backend.market_data_latency import LatencySummary
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.schema import EdgeSpec, NodeSpec, StrategyGraph
from backend_app.core.dependencies import get_current_user, get_request_supabase
from tests.test_task_9_5_registry_budget import BUDGET_BYTES

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The four graph sizes ``design.md`` -> Testing strategy -> Performance names. Not restated:
#: read from the shipped label helper, because task 9.1 bucketed
#: ``builder.compile.duration_ms`` by exactly these edges *so that* this task could read
#: Requirement 25.1's p95 at 200 nodes off one series. A test below asserts they are equal,
#: so an edit to the buckets moves the measurement points with them.
MEASUREMENT_POINTS: Tuple[int, ...] = M.NODE_COUNT_BUCKETS

#: Requirement 25.1 / ``design.md`` -> Performance: "Compile budget | p95 < 50 ms at 200
#: nodes". Read as "50 ms or less", which is the requirement's own wording.
COMPILE_BUDGET_MS = 50.0

#: Requirement 25.2 / ``design.md`` -> Performance: "Validation budget | p95 < 120 ms
#: server-side".
VALIDATION_BUDGET_MS = 120.0

#: Requirement 25.4's edge ceiling, from the shipped table rather than restated.
EDGE_CEILING = V.LIMITS.max_edges

#: ``algo22-terminal/src/lib/graphValidation.js``. The client debounce, quoted here only so
#: the "under the debounce path" measurement names the interval it is under; the constant and
#: the coalescing behaviour are asserted where they live (``graphValidation.test.js`` and
#: ``strategyBuilder.validation.test.jsx``, task 3.10).
CLIENT_DEBOUNCE_MS = 400

LATENCY_LANE = REPO_ROOT / "tests" / "perf" / "test_strategy_builder_budgets.py"
PYTEST_INI = REPO_ROOT / "pytest.ini"

REGISTRY_BASE = "/api/strategy-operations/registry"

REGISTRY_RESOURCES = ("blocks", "indicators", "features", "models", "timeframes")


# ---------------------------------------------------------------------------
# The graphs. One definition, used by both lanes.
# ---------------------------------------------------------------------------


def assembled_registry():
    """The real assembled registry.

    ``build_registry`` rather than ``get_registry``: the memoised accessor is what the
    request path uses, but a test that wants a registry of its own should not depend on
    whether some earlier test warmed or reset the module cache.
    """
    return registry_module.build_registry()


@pytest.fixture(scope="module")
def reg():
    return assembled_registry()


def _node(reg: Any, node_id: str, block_id: str, **params: Any) -> NodeSpec:
    """One node, with its category taken from the registry rather than asserted.

    The category a client sends is advisory (Requirement 6.13, and the validator warns with
    ``PORT_CONTRACT_RECOMPUTED`` when it disagrees). Reading it from the descriptor keeps
    these graphs free of the warnings a mismatch would add, so the latency file is timing a
    clean validation rather than a validation plus an override report.
    """
    return NodeSpec(
        id=node_id,
        block_id=block_id,
        category=reg[block_id].category,
        params=dict(params),
    )


def fan_graph(reg: Any, total_nodes: int) -> StrategyGraph:
    """A valid graph of exactly ``total_nodes`` nodes and ``total_nodes`` edges.

    ``ohlcv_feed -> {ema, rsi x N} -> between -> action_buy_market``, with the RSI bank
    fanning out of the feed's ``close`` port. Fan-*out* is unbounded (Requirement 25.4's 16 is
    a fan-*in* limit on one variadic port), so this shape scales to any size without needing
    a second data source, and every added node is a real indicator the compiler has to place
    in a level and bind to a runtime reference.

    Only ``n_rsi_0`` feeds the gate. The rest publish nothing downstream and are still not
    orphans: validator stage 7 clears any node reachable *from* a DATA block, which is the
    whole bank. That is the honest shape of a large work-in-progress strategy, and it is
    also what keeps the node count independent of the edge count.
    """
    if total_nodes < 6:
        raise ValueError("the fixed spine is five nodes plus at least one indicator")
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
        _node(reg, "n_floor", "constant", value=30.0),
        _node(reg, "n_ema", "ema", window=20, source="close"),
        _node(reg, "n_gate", "between", inclusive=True),
        _node(
            reg,
            "n_buy",
            "action_buy_market",
            quantity_type="percent_of_equity",
            quantity=0.25,
        ),
    ]
    edges = [
        EdgeSpec(id="e_ema", source="n_data", source_port="close", target="n_ema", target_port="series"),
        EdgeSpec(id="e_lo", source="n_floor", source_port="value", target="n_gate", target_port="lower"),
        EdgeSpec(id="e_hi", source="n_ema", source_port="value", target="n_gate", target_port="upper"),
        EdgeSpec(id="e_out", source="n_gate", source_port="out", target="n_buy", target_port="signal"),
    ]
    for index in range(total_nodes - len(nodes)):
        nodes.append(_node(reg, f"n_rsi_{index}", "rsi", window=14))
        edges.append(
            EdgeSpec(
                id=f"e_rsi_{index}",
                source="n_data",
                source_port="close",
                target=f"n_rsi_{index}",
                target_port="series",
            )
        )
    edges.append(
        EdgeSpec(id="e_val", source="n_rsi_0", source_port="value", target="n_gate", target_port="value")
    )
    return StrategyGraph(
        schema_version=2,
        strategy_id=f"s-perf-{total_nodes}",
        version="1.0.0",
        name=f"task 9.6 fan {total_nodes}",
        nodes=nodes,
        edges=edges,
    )


def ceiling_graph(
    reg: Any,
    total_nodes: int = None,
    total_edges: int = None,
) -> StrategyGraph:
    """The largest graph Requirement 25.4 permits: 200 nodes **and** 400 edges.

    ``fan_graph(200)`` carries 200 edges, half the legal maximum, and compile cost is a
    function of both. So the worst legal case gets its own shape: a bank of ``rsi`` nodes plus
    a bank of ``between`` gates, each gate consuming three edges (``value``, ``upper``,
    ``lower``). Sources fan out freely, so 96 indicators feed 101 gates without anything being
    connected twice - what the edge ceiling costs is edges, not distinct producers.
    """
    total_nodes = V.LIMITS.max_nodes if total_nodes is None else total_nodes
    total_edges = V.LIMITS.max_edges if total_edges is None else total_edges
    # Three fixed nodes (feed, constant, action), one fixed edge (gate -> action). Every
    # indicator costs one edge, every gate costs three.
    gates = (total_edges - 1 - (total_nodes - 3)) // 2
    indicators = total_nodes - 3 - gates
    if gates < 1 or indicators < 1:
        raise ValueError(f"no legal shape for {total_nodes} nodes and {total_edges} edges")
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
        _node(reg, "n_floor", "constant", value=30.0),
        _node(
            reg,
            "n_buy",
            "action_buy_market",
            quantity_type="percent_of_equity",
            quantity=0.25,
        ),
    ]
    edges: List[EdgeSpec] = []
    for index in range(indicators):
        nodes.append(_node(reg, f"n_rsi_{index}", "rsi", window=14))
        edges.append(
            EdgeSpec(
                id=f"e_rsi_{index}",
                source="n_data",
                source_port="close",
                target=f"n_rsi_{index}",
                target_port="series",
            )
        )
    for index in range(gates):
        nodes.append(_node(reg, f"n_gate_{index}", "between", inclusive=True))
        edges.append(
            EdgeSpec(
                id=f"e_gate_value_{index}",
                source=f"n_rsi_{index % indicators}",
                source_port="value",
                target=f"n_gate_{index}",
                target_port="value",
            )
        )
        edges.append(
            EdgeSpec(
                id=f"e_gate_upper_{index}",
                source=f"n_rsi_{(index + 1) % indicators}",
                source_port="value",
                target=f"n_gate_{index}",
                target_port="upper",
            )
        )
        edges.append(
            EdgeSpec(
                id=f"e_gate_lower_{index}",
                source="n_floor",
                source_port="value",
                target=f"n_gate_{index}",
                target_port="lower",
            )
        )
    edges.append(
        EdgeSpec(id="e_out", source="n_gate_0", source_port="out", target="n_buy", target_port="signal")
    )
    # The arithmetic above only closes for an (N, E) pair of the right parity. A silently
    # smaller graph would make "the budget holds at the ceiling" a claim about something
    # else, so it raises rather than returning an approximation.
    if (len(nodes), len(edges)) != (total_nodes, total_edges):
        raise ValueError(
            f"asked for {total_nodes} nodes and {total_edges} edges, this shape yields "
            f"{len(nodes)} and {len(edges)}"
        )
    return StrategyGraph(
        schema_version=2,
        strategy_id="s-perf-ceiling",
        version="1.0.0",
        name=f"task 9.6 ceiling {total_nodes}x{total_edges}",
        nodes=nodes,
        edges=edges,
    )


def debounce_body(graph: StrategyGraph) -> Dict[str, Any]:
    """The request body the client's debounced validation POST carries.

    ``StrategyBuilder.jsx`` calls ``strategiesApi.validate(graph)`` with the canonical
    envelope ``toCanonical`` produced, and ``strategies.js`` posts it as the **whole body** -
    not wrapped in ``{"dag": ...}``, which is the shape the older callers and most of the
    existing endpoint tests use. So the debounce path exercises the handler's top-level
    branch, and this is the body it sees.
    """
    return graph.to_dict()


# ---------------------------------------------------------------------------
# 1. The measurement points are the shipped ones
# ---------------------------------------------------------------------------


class TestTheMeasurementPoints:
    """25.1/25.2 name 200 nodes; ``design.md`` names 10 / 50 / 100 / 200."""

    def test_the_points_are_the_metrics_node_count_buckets(self):
        assert MEASUREMENT_POINTS == (10, 50, 100, 200)
        assert MEASUREMENT_POINTS == M.NODE_COUNT_BUCKETS

    @pytest.mark.parametrize("count", MEASUREMENT_POINTS)
    def test_a_graph_of_that_size_files_under_a_label_of_that_size(self, count):
        """A p95 read at ``node_count="200"`` must be a p95 of 200-node compiles.

        The label is a bucket - "the smallest edge at or above the count" - so this only
        holds because the measurement points *are* the edges. It is the property the latency
        file depends on when it reads one series per size, so it is asserted rather than
        assumed.
        """
        assert M.node_count_bucket(count) == str(count)

    def test_the_largest_point_is_the_legal_maximum(self):
        """Requirement 25.4 caps a graph at 200 nodes, and 25.1 states its budget there."""
        assert MEASUREMENT_POINTS[-1] == V.LIMITS.max_nodes

    def test_a_graph_above_the_ceiling_is_visible_rather_than_filed_under_200(self):
        """So an over-limit compile cannot dilute the series the budget is read from."""
        assert M.node_count_bucket(V.LIMITS.max_nodes + 1) == M.NODE_COUNT_OVERFLOW


# ---------------------------------------------------------------------------
# 2. The graphs are the sizes they claim, and they compile
# ---------------------------------------------------------------------------


class TestTheGraphsAreRealAndLegal:
    """A latency figure over a refused graph would measure the refusal, not the compile."""

    @pytest.mark.parametrize("count", MEASUREMENT_POINTS)
    def test_the_fan_graph_has_exactly_the_requested_size(self, reg, count):
        graph = fan_graph(reg, count)
        assert len(graph.nodes) == count
        assert len(graph.edges) == count
        assert len({node.id for node in graph.nodes}) == count

    @pytest.mark.parametrize("count", MEASUREMENT_POINTS)
    def test_the_fan_graph_validates_with_no_errors_and_no_warnings(self, reg, count):
        report = V.validate(graph=fan_graph(reg, count), registry=reg)
        assert report.valid, f"{count} nodes: {report.codes()}"
        assert report.errors == []
        assert [issue["code"] for issue in report.warnings] == []
        assert report.dag_hash is not None

    @pytest.mark.parametrize("count", MEASUREMENT_POINTS)
    def test_the_fan_graph_is_within_every_capacity_bound(self, reg, count):
        """Task 9.4's standalone rule, so no bound is being brushed against unnoticed."""
        assert V.capacity_issues(fan_graph(reg, count)) == ()

    def test_the_compiled_plan_holds_every_node_so_the_label_is_the_size(self, reg):
        """``record_builder_compile`` takes its count from ``plan.execution_order``."""
        from backend_app.backend import strategy_builder as SB

        compiled = SB.compile_version(fan_graph(reg, 200), reg)
        assert len(compiled.plan.execution_order) == 200
        assert M.node_count_bucket(len(compiled.plan.execution_order)) == "200"

    def test_the_ceiling_graph_sits_on_both_bounds_at_once(self, reg):
        graph = ceiling_graph(reg)
        assert len(graph.nodes) == V.LIMITS.max_nodes == 200
        assert len(graph.edges) == EDGE_CEILING == 400
        assert V.capacity_issues(graph) == ()
        report = V.validate(graph, reg)
        assert report.valid, report.codes()

    def test_the_ceiling_is_a_ceiling_and_not_a_coincidence(self, reg):
        """One more node, or one more edge, and the validator refuses.

        This is what makes "the budget holds at the maximum legal graph" a claim about the
        maximum rather than about an arbitrary large graph. Task 9.4 pinned 200 as *legal*;
        this pins 201 as not.
        """
        one_more_node = ceiling_graph(reg)
        one_more_node.nodes.append(_node(reg, "n_rsi_extra", "rsi", window=9))
        one_more_node.edges.append(
            EdgeSpec(
                id="e_rsi_extra",
                source="n_data",
                source_port="close",
                target="n_rsi_extra",
                target_port="series",
            )
        )
        codes = {issue["code"] for issue in V.capacity_issues(one_more_node)}
        assert V.CODE_NODE_LIMIT_EXCEEDED in codes
        assert V.CODE_EDGE_LIMIT_EXCEEDED in codes

        # 198 rather than 199 because the gate shape costs two edges per node swapped, so
        # 400 edges is reachable at an even node count. The point is a graph under the node
        # limit and over the edge limit, which is what isolates the second bound.
        one_more_edge = ceiling_graph(reg, total_nodes=198, total_edges=EDGE_CEILING)
        assert len(one_more_edge.nodes) == 198
        assert len(one_more_edge.edges) == EDGE_CEILING
        one_more_edge.edges.append(
            EdgeSpec(
                id="e_edge_extra",
                source="n_rsi_0",
                source_port="value",
                target="n_gate_1",
                target_port="value",
            )
        )
        edge_codes = {issue["code"] for issue in V.capacity_issues(one_more_edge)}
        assert V.CODE_NODE_LIMIT_EXCEEDED not in edge_codes
        assert V.CODE_EDGE_LIMIT_EXCEEDED in edge_codes

    def test_the_debounce_body_is_the_envelope_the_client_posts(self, reg):
        """Top-level canonical graph, and the handler reads it without a ``dag`` wrapper."""
        body = debounce_body(fan_graph(reg, 10))
        assert body["schema_version"] == 2
        assert "dag" not in body
        assert len(body["nodes"]) == 10 and len(body["edges"]) == 10

        from backend_app.backend.strategy_dag.schema import load_graph

        assert len(load_graph(body).nodes) == 10


# ---------------------------------------------------------------------------
# 3. The p95 the budgets are compared against is a percentile
# ---------------------------------------------------------------------------


class TestThePercentileIsAPercentile:
    """Task 9.1 made these two ``Summary`` for exactly this reason. Requirements 25.1, 25.2."""

    def test_both_budgeted_metrics_are_summaries_not_histograms(self):
        collector = M.metrics_collector
        assert isinstance(collector.builder_compile_duration_ms, M.Summary)
        assert isinstance(collector.builder_validation_duration_ms, M.Summary)
        assert not isinstance(collector.builder_compile_duration_ms, M.Histogram)
        assert not isinstance(collector.builder_validation_duration_ms, M.Histogram)

    def test_the_p95_is_the_nearest_rank_value_of_the_observed_population(self):
        """Not a bucket edge: the 95th of 100 known observations, exactly.

        A fresh collector, so this cannot be perturbed by whatever else the suite recorded.
        """
        fresh = M.MetricsCollector()
        population = [float(value) for value in range(1, 101)]
        for value in population:
            fresh.record_builder_validation(value)

        summary = fresh.builder_validation_duration_ms.summary()
        assert isinstance(summary, LatencySummary)
        assert summary.count == 100
        assert summary.p95_ms == LatencySummary.from_samples(population).p95_ms
        assert summary.p95_ms == 95.0
        assert summary.max_ms == 100.0

    def test_an_unmeasured_population_answers_none_and_never_zero(self):
        """Task 7.6's rule: a zero would read as "instant" and pass any budget."""
        fresh = M.MetricsCollector()
        assert fresh.builder_validation_duration_ms.summary() is None
        assert fresh.builder_compile_duration_ms.summary(node_count="200") is None

    def test_the_compile_series_is_readable_per_node_count(self):
        """One series per measurement point, so a 200-node p95 is not a blend of sizes."""
        fresh = M.MetricsCollector()
        for count, duration in ((10, 1.0), (200, 40.0)):
            for _ in range(20):
                fresh.record_builder_compile(duration, count)

        small = fresh.builder_compile_duration_ms.summary(node_count="10")
        large = fresh.builder_compile_duration_ms.summary(node_count="200")
        assert small.p95_ms == 1.0
        assert large.p95_ms == 40.0

    def test_the_retained_ring_is_larger_than_the_latency_files_sample_count(self):
        """A ring smaller than the run would silently make the p95 a p95 of the tail."""
        source = LATENCY_LANE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        samples = {
            target.id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        assert samples["SAMPLES"] + samples["WARMUP"] <= M.Summary.DEFAULT_CAPACITY


# ---------------------------------------------------------------------------
# 4. Requirement 25.3: the registry cache-hit ratio
# ---------------------------------------------------------------------------


@pytest.fixture
def user():
    return {
        "id": "usr_task96_reader",
        "email": "budgets@example.com",
        "role": "authenticated",
        "access_token": "token_task96",
    }


@pytest.fixture
def client(user):
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def fresh_metrics(monkeypatch):
    """A collector of this test's own, so a ratio is a ratio of this session."""
    fresh = M.MetricsCollector()
    monkeypatch.setattr(M, "metrics_collector", fresh)
    return fresh


def _ratio(collector: Any, resource: str) -> float:
    requests = collector.builder_registry_requests.get(resource=resource)
    hits = collector.builder_registry_cache_hits.get(resource=resource)
    assert requests > 0, f"no {resource} request was counted"
    return hits / requests


class TestTheRegistryCacheHitRatio:
    """The cache-effectiveness signal ``design.md``'s observability table names.

    The size half of Requirement 25.3 is task 9.5's measurement (15 863 gzipped bytes for
    ``/blocks``, 6.2% of the ceiling) and is not re-derived here. What is measured here is
    what a session of reads costs the server once the client holds an ``ETag``.
    """

    def test_a_session_of_reads_pays_for_the_payload_once(self, client, fresh_metrics):
        """Twelve reads of ``/blocks``: one miss, eleven 304s. Ratio 11/12."""
        first = client.get(f"{REGISTRY_BASE}/blocks")
        assert first.status_code == 200
        tag = first.headers["etag"]

        statuses = [first.status_code]
        for _ in range(11):
            statuses.append(
                client.get(
                    f"{REGISTRY_BASE}/blocks", headers={"If-None-Match": tag}
                ).status_code
            )

        assert statuses == [200] + [304] * 11
        assert fresh_metrics.builder_registry_requests.get(resource="blocks") == 12
        assert fresh_metrics.builder_registry_cache_hits.get(resource="blocks") == 11
        assert _ratio(fresh_metrics, "blocks") == pytest.approx(11 / 12)

    @pytest.mark.parametrize("resource", REGISTRY_RESOURCES)
    def test_every_resource_caches_and_is_counted_under_its_own_name(
        self, client, fresh_metrics, resource
    ):
        """Five resources, five independent ratios - no projection is uncached."""
        first = client.get(f"{REGISTRY_BASE}/{resource}")
        assert first.status_code == 200
        second = client.get(
            f"{REGISTRY_BASE}/{resource}",
            headers={"If-None-Match": first.headers["etag"]},
        )

        assert second.status_code == 304
        assert _ratio(fresh_metrics, resource) == pytest.approx(0.5)
        for other in REGISTRY_RESOURCES:
            if other != resource:
                assert (
                    fresh_metrics.builder_registry_requests.get(resource=other) == 0
                ), f"reading {resource} counted a request against {other}"

    def test_the_hit_ratio_rises_with_the_session_and_has_no_ceiling_below_one(
        self, client, fresh_metrics
    ):
        """The ratio is ``(n-1)/n``: the cache never expires mid-session on its own.

        ``Cache-Control: private, max-age=0, must-revalidate`` asks for revalidation every
        time and lets the ``ETag`` make it free, so a longer session has a *higher* hit
        ratio, not a lower one. A max-age would put a floor under the miss rate.
        """
        first = client.get(f"{REGISTRY_BASE}/blocks")
        tag = first.headers["etag"]
        assert "must-revalidate" in first.headers["cache-control"]
        assert "max-age=0" in first.headers["cache-control"]

        observed = []
        for reads in range(2, 7):
            client.get(f"{REGISTRY_BASE}/blocks", headers={"If-None-Match": tag})
            observed.append((reads, _ratio(fresh_metrics, "blocks")))

        assert observed == [
            (reads, pytest.approx((reads - 1) / reads)) for reads, _ in observed
        ]
        assert observed[-1][1] > observed[0][1]

    def test_a_miss_is_still_a_miss_when_the_client_holds_a_stale_tag(
        self, client, fresh_metrics
    ):
        """A hit ratio that counted a stale revalidation as a hit would be a lie."""
        client.get(f"{REGISTRY_BASE}/blocks")
        stale = client.get(
            f"{REGISTRY_BASE}/blocks", headers={"If-None-Match": '"not-the-tag"'}
        )

        assert stale.status_code == 200
        assert _ratio(fresh_metrics, "blocks") == 0.0

    def test_the_two_files_agree_on_what_250_kb_means(self):
        """One ceiling constant, imported rather than restated."""
        assert BUDGET_BYTES == 250 * 1024


# ---------------------------------------------------------------------------
# 5. The latency lane is excluded on purpose, and still reachable
# ---------------------------------------------------------------------------


class TestTheLatencyLaneIsOutsideTheDefaultRun:
    """A decision, recorded as a test so it cannot be undone by accident either way."""

    def test_the_latency_file_exists_and_parses(self):
        assert LATENCY_LANE.is_file(), f"{LATENCY_LANE} is missing"
        compile(LATENCY_LANE.read_text(encoding="utf-8"), str(LATENCY_LANE), "exec")

    def test_pytest_ini_keeps_tests_perf_out_of_the_default_lane(self):
        line = next(
            row
            for row in PYTEST_INI.read_text(encoding="utf-8").splitlines()
            if row.startswith("norecursedirs")
        )
        assert "tests/perf" in line

    def test_the_shipped_norecursedirs_defaults_are_still_restated(self):
        """Setting the key replaces pytest's defaults; dropping them descends into .venv."""
        line = next(
            row
            for row in PYTEST_INI.read_text(encoding="utf-8").splitlines()
            if row.startswith("norecursedirs")
        )
        for shipped in ("*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv", "{arch}"):
            assert shipped in line, f"pytest's default ignore {shipped} was dropped"

    def test_the_latency_file_takes_its_graphs_from_this_module(self):
        """One definition of "a 200-node graph" across both lanes, asserted from the AST."""
        tree = ast.parse(LATENCY_LANE.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "tests.test_task_9_6_performance_budgets"
            for alias in node.names
        }
        assert {"fan_graph", "ceiling_graph", "MEASUREMENT_POINTS"} <= imported

    def test_the_latency_file_asserts_the_two_stated_budgets(self):
        """So the budgets cannot be edited in the lane that is not run by default."""
        source = LATENCY_LANE.read_text(encoding="utf-8")
        assert "COMPILE_BUDGET_MS" in source
        assert "VALIDATION_BUDGET_MS" in source
        assert COMPILE_BUDGET_MS == 50.0
        assert VALIDATION_BUDGET_MS == 120.0

    def test_the_latency_file_needs_no_environment_variable_to_run(self):
        """Unlike task 7.6's 24 h feed experiment, which is gated a second time.

        This measurement finishes in seconds and takes no feed, no Redis and no exchange, so
        ``pytest tests/perf`` should run it rather than skip it - that is how task 9.7
        confirms the budgets at 200 nodes.
        """
        source = LATENCY_LANE.read_text(encoding="utf-8")
        assert "AERORA_MARKET_DATA_LATENCY_EXPERIMENT" not in source
        assert "skipif" not in source
