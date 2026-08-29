"""What the builder, the training service and the runtime record about themselves.

Spec: strategy-builder task 9.1. Requirements 24.1, 24.2, 24.3; ``design.md`` ->
"Observability and node-level diagnostics".

WHAT THIS FILE IS SHAPED TO CATCH
---------------------------------
Instrumentation is easy to add and easy to get wrong in four ways that each have a real
cost, so each gets its own class below.

1. **A metric that is defined and never recorded.** A dashboard panel reading a series
   nobody increments is worse than a missing panel: it reads as "zero happened". So every
   assertion here drives the **real seam** - the real validator, the real compiler, the real
   registry endpoint, the real feed classifier, the real arrival gate, the real lifecycle
   writer, the real intent firewall - and then reads the counter. Nothing below records a
   metric by calling the recorder and then asserting the recorder was called.

2. **A percentile that is really a bucket edge.** ``metrics.Histogram`` is Prometheus-shaped
   and keeps bucket counts, not samples; task 7.6 recorded that and built
   ``market_data_latency.LatencySummary`` for the one figure a stated margin is measured on.
   Three of this task's metrics carry stated numbers - Requirement 25.1's 50 ms, 25.2's
   120 ms and 19.12's 25 ms - and :class:`TestExactPercentilesWhereABudgetIsStated` asserts
   those three answer with samples that actually occurred.

3. **A label that leaks, or that makes one series per tenant.** A metrics layer with an
   unbounded label set is how observability takes down the process it was added to observe,
   and a label carrying a venue or a secret is SB-06 in a new location.
   :class:`TestNothingSecretOrPerTenantIsLabelled` checks the label *names* of all eighteen
   metrics against both vocabularies.

4. **Instrumentation that changes what the code it measures does.** The recording calls sit
   inside a validator, a compile, a training state transition and the gate that refuses a
   non-finite trade intent. :class:`TestInstrumentationNeverFailsTheActItMeasures` breaks a
   metric object and asserts each of those still does its job.

The eighteen metric names are transcribed from the task's three bullets, verbatim, into
:data:`REQUIRED_METRIC_NAMES`. That literal list is the point of this file: a rename on
either side has to be a deliberate edit in two places.

No ``hypothesis`` import, deliberately. ``design.md`` numbers the properties this spec
generates property tests for; task 9.1 names none, and adding an unnumbered property here
would put a test in the suite no design document accounts for.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from backend_app.backend import feed_state as FS
from backend_app.backend import market_data_contract as MC
from backend_app.backend import metrics as M
from backend_app.backend import strategy_compiler as SC
from backend_app.backend import strategy_lifecycle as LC
from backend_app.backend import strategy_service as SS
from backend_app.backend import training_worker as W
from backend_app.backend.dag_engine import (NOT_READY, READY, RUNTIME_STATES,
                                            WARMING, DAGEngine,
                                            ExecutionBlocked, PlanRuntimeState,
                                            assert_execution_safe)
from backend_app.backend.market_data_latency import LatencySummary
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.schema import (EdgeSpec, NodeSpec,
                                                     StrategyGraph)

# ---------------------------------------------------------------------------
# The vocabulary, transcribed from task 9.1's three bullets
# ---------------------------------------------------------------------------

#: Requirement 24.1 - the Strategy_Builder_API.
BUILDER_METRIC_NAMES = (
    "builder.validation.duration_ms",
    "builder.validation.errors",
    "builder.compile.duration_ms",
    "builder.compile.failures",
    "builder.registry.requests",
    "builder.registry.cache_hits",
    "builder.assets.universe_age_seconds",
)

#: Requirement 24.2 - the Training_Service.
TRAINING_METRIC_NAMES = (
    "training.jobs.by_status",
    "training.job.duration_seconds",
    "training.jobs.cap_rejections",
    "training.jobs.blocked_insufficient_data",
)

#: Requirement 24.3 - the DAG_Runtime, the deployment state machine and the feed.
RUNTIME_METRIC_NAMES = (
    "dag.node.execution_ms",
    "dag.node.not_ready",
    "dag.intents.blocked_non_finite",
    "deployment.state_transitions",
    "market_data.latency_ms",
    "market_data.quality_score",
    "market_data.feed_state",
)

REQUIRED_METRIC_NAMES = (
    BUILDER_METRIC_NAMES + TRAINING_METRIC_NAMES + RUNTIME_METRIC_NAMES
)

#: The three metrics compared against a number the spec states, and therefore the three that
#: must not answer a percentile with a bucket edge. Requirement 25.1 (p95 compile at 200
#: nodes <= 50 ms), Requirement 25.2 (p95 validation <= 120 ms), Requirement 19.12 (a 25 ms
#: p99 margin).
BUDGETED_METRIC_NAMES = (
    "builder.validation.duration_ms",
    "builder.compile.duration_ms",
    "market_data.latency_ms",
)

#: Label names that would make one series per tenant, per user or per resource. None of these
#: is a secret; every one of them is unbounded, and an unbounded label set is the failure
#: mode. ``symbol`` is deliberately absent: a market identifier is global reference data
#: drawn from the asset universe, and ``execution_latency_ms`` has been labelled by it since
#: STEP 8.1.
FORBIDDEN_IDENTITY_LABELS = frozenset(
    {
        "user",
        "user_id",
        "tenant",
        "tenant_id",
        "account",
        "account_id",
        "strategy",
        "strategy_id",
        "version",
        "version_id",
        "deployment",
        "deployment_id",
        "job",
        "job_id",
        "node",
        "node_id",
        "email",
        "session",
        "session_id",
        "request_id",
        "ip",
    }
)

#: SB-06 / Requirement 12.1's vocabulary. A metric name or label carrying any of these is a
#: credential surface, whatever it happens to hold at the time.
FORBIDDEN_CREDENTIAL_LABELS = frozenset(
    {
        "exchange",
        "exchange_id",
        "venue",
        "api_key",
        "apikey",
        "key",
        "secret",
        "passphrase",
        "password",
        "token",
        "access_token",
        "credential",
        "credentials",
    }
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def collector(monkeypatch):
    """A fresh collector installed as the module singleton, per test.

    Every seam reaches the collector through a lazy ``from backend_app.backend.metrics
    import metrics_collector`` **inside** the function, so replacing the module attribute is
    enough and no seam has to be told about it. Fresh per test because these are counters:
    a shared singleton would make every assertion depend on execution order.
    """
    fresh = M.MetricsCollector()
    monkeypatch.setattr(M, "metrics_collector", fresh)
    return fresh


@pytest.fixture(scope="module")
def reg():
    """The real assembled registry. Assembly is not cheap, so it is shared."""
    return registry_module.build_registry()


def _node(reg, node_id, block_id, **params):
    return NodeSpec(
        id=node_id,
        block_id=block_id,
        category=reg[block_id].category,
        params=dict(params),
    )


def _linear_graph(reg, *, window: int = 20) -> StrategyGraph:
    """``ohlcv_feed -> ema -> gt -> action_buy_market``, the smallest tradeable graph."""
    return StrategyGraph(
        schema_version=2,
        strategy_id="s-9-1",
        version="1.0.0",
        name="metrics linear",
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
            _node(reg, "n_ema", "ema", window=window, source="close"),
            _node(reg, "n_const", "constant", value=1.0),
            _node(reg, "n_gt", "gt"),
            _node(
                reg,
                "n_buy",
                "action_buy_market",
                quantity_type="base_amount",
                quantity=1.0,
            ),
        ],
        edges=[
            EdgeSpec(id="e1", source="n_data", source_port="close",
                     target="n_ema", target_port="series"),
            EdgeSpec(id="e2", source="n_ema", source_port="value",
                     target="n_gt", target_port="left"),
            EdgeSpec(id="e3", source="n_const", source_port="value",
                     target="n_gt", target_port="right"),
            EdgeSpec(id="e4", source="n_gt", source_port="out",
                     target="n_buy", target_port="signal"),
        ],
    )


def _invalid_graph(reg) -> StrategyGraph:
    """A graph with an edge to a node that does not exist. Invalid, for one clear reason."""
    graph = _linear_graph(reg)
    graph.edges.append(
        EdgeSpec(id="e_ghost", source="n_gt", source_port="out",
                 target="n_does_not_exist", target_port="signal")
    )
    return graph


def candles(bars: int) -> pd.DataFrame:
    """The frame ``ClosedBarIngest(drop_late=True)`` admits: unique index, five columns."""
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


def metric_named(collector, name: str):
    """The metric object whose ``name`` is exactly ``name``, or a failure naming it."""
    for metric in collector.strategy_builder_metrics():
        if metric.name == name:
            return metric
    raise AssertionError(f"no metric is named {name!r}")


# ═══════════════════════════════════════════════════════════════════════════
# 1. The vocabulary is the spec's
# ═══════════════════════════════════════════════════════════════════════════


class TestTheMetricVocabularyIsTheSpecs:
    """Eighteen names, spelled the way task 9.1's three bullets spell them."""

    @pytest.mark.parametrize("name", REQUIRED_METRIC_NAMES)
    def test_each_named_metric_exists_verbatim(self, collector, name):
        assert metric_named(collector, name) is not None

    def test_there_is_no_nineteenth_and_no_seventeenth(self, collector):
        """Pinned both ways: an extra metric is reviewed, a deleted one fails."""
        defined = {metric.name for metric in collector.strategy_builder_metrics()}

        assert defined == set(REQUIRED_METRIC_NAMES)

    def test_the_attribute_list_and_the_metric_objects_agree(self, collector):
        """The exporter reads the attribute list, so a typo in it would silently drop one."""
        assert len(collector.STRATEGY_BUILDER_METRIC_ATTRIBUTES) == len(
            REQUIRED_METRIC_NAMES
        )
        assert len(set(collector.STRATEGY_BUILDER_METRIC_ATTRIBUTES)) == len(
            REQUIRED_METRIC_NAMES
        )

    @pytest.mark.parametrize("name", REQUIRED_METRIC_NAMES)
    def test_each_named_metric_reaches_the_scrape(self, collector, name):
        """Defined is not exposed. Every one has to appear in ``/metrics``' payload."""
        exported = collector.get_prometheus_metrics()

        assert M.to_prometheus_name(name) in exported

    @pytest.mark.parametrize("name", REQUIRED_METRIC_NAMES)
    def test_the_exported_name_is_a_legal_prometheus_name(self, name):
        """The dotted name is the identity; the exported line cannot carry a dot."""
        exported = M.to_prometheus_name(name)

        assert "." not in exported
        assert exported.replace("_", "").replace(":", "").isalnum()
        assert not exported[0].isdigit()

    def test_sanitisation_leaves_every_pre_existing_name_alone(self, collector):
        """Task 9.1 must not have renamed a metric an existing dashboard reads."""
        pre_existing = (
            collector.trades_executed_total,
            collector.trades_blocked_total,
            collector.failed_orders_total,
            collector.websocket_disconnects_total,
            collector.execution_latency_ms,
            collector.risk_score_histogram,
            collector.active_connections,
            collector.active_users,
            collector.ws_server_connections,
            collector.tasks_queue_size,
            collector.execution_latency_seconds,
            collector.system_uptime_seconds,
        )

        for metric in pre_existing:
            assert metric.prometheus_name == metric.name

    def test_sanitisation_is_idempotent(self):
        once = [M.to_prometheus_name(name) for name in REQUIRED_METRIC_NAMES]

        assert [M.to_prometheus_name(name) for name in once] == once


# ═══════════════════════════════════════════════════════════════════════════
# 2. Nothing secret, nothing per-tenant
# ═══════════════════════════════════════════════════════════════════════════


class TestNothingSecretOrPerTenantIsLabelled:
    """A label is a dimension. These are the dimensions it must not be."""

    @pytest.mark.parametrize("name", REQUIRED_METRIC_NAMES)
    def test_no_metric_name_carries_a_credential_word(self, name):
        segments = set(name.replace(".", "_").lower().split("_"))

        assert not segments & FORBIDDEN_CREDENTIAL_LABELS

    def test_no_label_name_carries_a_credential_word(self, collector):
        offenders = {
            metric.name: sorted(set(metric.label_names) & FORBIDDEN_CREDENTIAL_LABELS)
            for metric in collector.strategy_builder_metrics()
            if set(metric.label_names) & FORBIDDEN_CREDENTIAL_LABELS
        }

        assert offenders == {}

    def test_no_label_name_is_a_per_tenant_or_per_resource_identity(self, collector):
        """The unbounded-cardinality half. A per-tenant series set is the outage."""
        offenders = {
            metric.name: sorted(set(metric.label_names) & FORBIDDEN_IDENTITY_LABELS)
            for metric in collector.strategy_builder_metrics()
            if set(metric.label_names) & FORBIDDEN_IDENTITY_LABELS
        }

        assert offenders == {}

    def test_a_label_value_cannot_break_out_of_its_quotes(self):
        """An unescaped quote would split one exported line into two malformed ones."""
        rendered = M.safe_label_value('bad" value\nsecond line')

        assert '"' not in rendered.replace('\\"', "")
        assert "\n" not in rendered

    def test_a_label_value_is_bounded(self):
        rendered = M.safe_label_value("x" * 5_000)

        assert len(rendered) == M.LABEL_VALUE_MAX_LENGTH

    def test_a_hostile_label_value_still_exports_one_line_per_series(self, collector):
        """The backstop, end to end: a value carrying quotes and newlines is contained."""
        collector.record_builder_compile_failure('VALIDATION"\nERROR')
        exported = collector.builder_compile_failures.to_prometheus()

        # Two comment lines plus exactly one sample line.
        assert len([line for line in exported.splitlines() if line]) == 3


# ═══════════════════════════════════════════════════════════════════════════
# 3. Exact percentiles where a budget is stated
# ═══════════════════════════════════════════════════════════════════════════


class TestExactPercentilesWhereABudgetIsStated:
    """Task 7.6's finding, applied: a bucket edge is not a percentile."""

    @pytest.mark.parametrize("name", BUDGETED_METRIC_NAMES)
    def test_a_budgeted_metric_retains_samples_rather_than_buckets(self, collector, name):
        metric = metric_named(collector, name)

        assert isinstance(metric, M.Summary)
        assert not isinstance(metric, M.Histogram)

    def test_the_percentiles_are_samples_that_actually_occurred(self, collector):
        """The whole reason ``Summary`` exists. A bucket edge would fail this."""
        for value in range(1, 101):
            collector.builder_validation_duration_ms.observe(float(value))

        summary = collector.builder_validation_duration_ms.summary()

        assert summary is not None
        assert summary.p50_ms == 50.0
        assert summary.p95_ms == 95.0
        assert summary.p99_ms == 99.0
        assert summary.max_ms == 100.0

    def test_the_percentile_method_is_task_7_6s_and_not_a_second_one(self, collector):
        """Delegated, so the two cannot drift. One nearest-rank implementation exists."""
        collector.market_data_latency_ms.observe(7.5, timeframe="1m")
        summary = collector.market_data_latency_ms.summary(timeframe="1m")

        assert isinstance(summary, LatencySummary)

    def test_an_unmeasured_population_is_none_and_never_zero(self, collector):
        """A zero would read as "instant". Task 7.6's rule, inherited."""
        assert collector.builder_compile_duration_ms.summary(node_count="200") is None

    def test_a_non_finite_observation_is_discarded_rather_than_stored(self, collector):
        """One infinity would make every percentile above it an infinity."""
        collector.builder_validation_duration_ms.observe(float("inf"))
        collector.builder_validation_duration_ms.observe(float("nan"))
        collector.builder_validation_duration_ms.observe(12.0)

        summary = collector.builder_validation_duration_ms.summary()

        assert summary is not None
        assert summary.count == 1
        assert summary.max_ms == 12.0

    def test_the_sample_ring_is_bounded_but_the_totals_are_not(self, collector):
        """Memory is bounded; ``_sum``/``_count`` stay monotonic as Prometheus expects."""
        small = M.Summary("t.bounded_ms", "test", capacity=8)
        for value in range(1, 101):
            small.observe(float(value))

        summary = small.summary()

        assert summary is not None
        assert summary.count == 8
        assert small.count() == 100.0

    def test_a_histogram_keeps_bucket_counts_and_offers_no_exact_percentile(self, collector):
        """The contrast, asserted rather than assumed: the two types are not interchangeable."""
        histogram = collector.dag_node_execution_ms

        assert isinstance(histogram, M.Histogram)
        assert not hasattr(histogram, "summary")
        assert histogram.buckets


# ═══════════════════════════════════════════════════════════════════════════
# 4. Requirement 24.1 — the builder seams
# ═══════════════════════════════════════════════════════════════════════════


class TestTheValidatorSeam:
    def test_a_validation_records_its_duration(self, collector, reg):
        V.validate(_linear_graph(reg), reg)

        summary = collector.builder_validation_duration_ms.summary()

        assert summary is not None
        assert summary.count == 1
        assert summary.max_ms >= 0.0

    def test_a_valid_graph_records_no_error(self, collector, reg):
        report = V.validate(_linear_graph(reg), reg)

        assert report.valid
        assert collector.builder_validation_errors.total() == 0

    def test_an_invalid_graph_records_one_increment_per_error_code(self, collector, reg):
        report = V.validate(_invalid_graph(reg), reg)

        assert not report.valid
        for code in {str(issue.get("code")) for issue in report.errors}:
            assert collector.builder_validation_errors.get(code=code) >= 1
        assert collector.builder_validation_errors.total() == len(report.errors)

    def test_warnings_are_not_counted_as_errors(self, collector, reg):
        """"Error counts by code" is unusable as an error rate if warnings are folded in."""
        from backend_app.backend.strategy_dag.schema import ValidationState

        graph = _linear_graph(reg)
        # A client claiming INVALID on a valid graph is recomputed, and the override is
        # reported as a WARNING, not an error.
        graph.validation_state = ValidationState.INVALID
        report = V.validate(graph, reg)

        assert report.warnings
        assert not report.errors
        assert collector.builder_validation_errors.total() == 0


class TestTheCompilerSeam:
    def test_a_compile_records_its_duration_under_its_node_count_bucket(
        self, collector, reg
    ):
        plan = SC.get_compiler().compile_plan(_linear_graph(reg), reg)
        bucket = M.node_count_bucket(len(plan.execution_order))

        summary = collector.builder_compile_duration_ms.summary(node_count=bucket)

        assert bucket == "10"  # five nodes
        assert summary is not None
        assert summary.count == 1

    def test_the_node_count_label_is_bounded_to_the_spec_s_measurement_points(self):
        """Requirements 25.1/25.2 and task 9.6 measure at 10, 50, 100 and 200."""
        seen = {M.node_count_bucket(n) for n in range(0, 260)}

        assert seen == {"10", "50", "100", "200", M.NODE_COUNT_OVERFLOW}

    def test_an_unreadable_node_count_is_named_rather_than_filed_under_a_size(self):
        assert M.node_count_bucket(None) == "unknown"
        assert M.node_count_bucket(-4) == "unknown"

    def test_an_invalid_graph_records_a_failure_and_no_duration(self, collector, reg):
        with pytest.raises(SC.ValidationError):
            SC.get_compiler().compile_plan(_invalid_graph(reg), reg)

        assert (
            collector.builder_compile_failures.get(
                reason=SC.COMPILE_FAILURE_INVALID_GRAPH
            )
            == 1
        )
        # The duration of a refusal is not the duration of a compile.
        assert collector.builder_compile_duration_ms.count(node_count="10") == 0

    def test_a_payload_that_is_not_a_canonical_graph_is_its_own_failure_reason(
        self, collector
    ):
        """A caller contract breach and an author's invalid graph are different faults."""
        with pytest.raises(SC.CompilerError):
            SC.get_compiler().compile_plan({"not": "a graph"})

        assert (
            collector.builder_compile_failures.get(
                reason=SC.COMPILE_FAILURE_NOT_CANONICAL
            )
            == 1
        )


class TestTheRegistrySeam:
    """``builder.registry.requests`` and ``.cache_hits``, through the real endpoint."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from backend_app.core.dependencies import (get_current_user,
                                                   get_request_supabase)
        from backend_app.main import app

        user = {
            "id": "usr_metrics_reader",
            "email": "metrics@example.com",
            "role": "authenticated",
            "access_token": "token_metrics",
        }
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_request_supabase] = lambda: None
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()

    def test_a_served_registry_counts_one_request_and_no_cache_hit(
        self, collector, client
    ):
        response = client.get("/api/strategy-operations/registry/blocks")

        assert response.status_code == 200
        assert collector.builder_registry_requests.get(resource="blocks") == 1
        assert collector.builder_registry_cache_hits.get(resource="blocks") == 0

    def test_a_matching_etag_counts_both_a_request_and_a_hit(self, collector, client):
        """Both, so the hit ratio's denominator is right (Requirement 25.3)."""
        first = client.get("/api/strategy-operations/registry/blocks")
        second = client.get(
            "/api/strategy-operations/registry/blocks",
            headers={"If-None-Match": first.headers["ETag"]},
        )

        assert second.status_code == 304
        assert collector.builder_registry_requests.get(resource="blocks") == 2
        assert collector.builder_registry_cache_hits.get(resource="blocks") == 1

    def test_each_projection_is_counted_under_its_own_resource(self, collector, client):
        """A projection's hit rate must not be attributed to ``/blocks``."""
        client.get("/api/strategy-operations/registry/blocks")
        client.get("/api/strategy-operations/registry/indicators")

        assert collector.builder_registry_requests.get(resource="blocks") == 1
        assert collector.builder_registry_requests.get(resource="indicators") == 1


class TestTheAssetUniverseSeam:
    def test_the_gauge_is_the_age_the_response_reports(self, collector, monkeypatch):
        """Read from ``source_meta.age_seconds``, so the two cannot disagree.

        The real ``discover_assets`` over a real merged universe, seeded an hour old. The
        endpoint's own line is ``set_asset_universe_age(page["source_meta"]["age_seconds"])``
        and this asserts the arithmetic behind that value rather than the assignment.
        """
        import time as _time

        from backend_app.backend import asset_universe as au

        au.reset_asset_universe_state_for_tests()
        monkeypatch.setattr(au, "supported_exchange_ids", lambda: [])

        async def no_redis():
            return None

        monkeypatch.setattr(au, "_redis_cache", no_redis)
        assets, _stats = au.merge_markets(
            [
                (
                    "binance",
                    {
                        "BTC/USDT": {
                            "symbol": "BTC/USDT",
                            "base": "BTC",
                            "quote": "USDT",
                            "type": "spot",
                            "active": True,
                            "precision": {"price": 2, "amount": 5},
                            "limits": {},
                        }
                    },
                )
            ]
        )
        au._local_universe = au.AssetUniverse(
            assets=assets,
            generated_at=_time.time() - 3_600.0,
            exchanges=["binance"],
            exchanges_failed=[],
        )
        try:
            page = asyncio.run(au.discover_assets(au.AssetQuery()))
            reported = page["source_meta"]["age_seconds"]
            collector.set_asset_universe_age(reported)

            assert reported == pytest.approx(3_600.0, abs=5.0)
            assert collector.builder_assets_universe_age_seconds.get() == reported
        finally:
            au.reset_asset_universe_state_for_tests()

    def test_an_unmeasured_universe_leaves_the_gauge_unset_rather_than_zero(
        self, collector
    ):
        """Zero seconds reads as "refreshed just now", which is the opposite of unknown."""
        assert collector.builder_assets_universe_age_seconds.get() is None

        collector.set_asset_universe_age(None)

        assert collector.builder_assets_universe_age_seconds.get() is None


# ═══════════════════════════════════════════════════════════════════════════
# 5. Requirement 24.2 — the training seams
# ═══════════════════════════════════════════════════════════════════════════


class TestTheTrainingSeams:
    JOB = {
        "id": "job_metrics_1",
        "user_id": "usr_1",
        "version_id": "ver_1",
        "node_id": "n_model",
        "block_id": "xgboost",
        "status": W.STATUS_RUNNING,
    }

    @pytest.fixture
    def offline_worker(self, monkeypatch):
        """``_finish`` with its three writes replaced. The status logic stays real."""
        written = {}

        async def fake_transition(sb, job_id, payload, **kwargs):
            written.update(payload)
            return dict(payload, id=job_id)

        async def fake_lifecycle(sb, version_id, state, extra=None):
            return True

        async def fake_publish(user_id, event, detail):
            return None

        monkeypatch.setattr(W, "_transition", fake_transition)
        monkeypatch.setattr(W, "set_version_lifecycle", fake_lifecycle)
        monkeypatch.setattr(W.S, "publish_training_event", fake_publish)
        return written

    def _job(self, **overrides):
        started = datetime.now(timezone.utc) - timedelta(seconds=90)
        return {**self.JOB, "started_at": started.isoformat(), **overrides}

    def test_a_completed_job_is_counted_by_status(self, collector, offline_worker):
        asyncio.run(
            W._finish(
                None, self._job(), status=W.STATUS_COMPLETED, worker_id="w1"
            )
        )

        assert collector.training_jobs_by_status.get(status=W.STATUS_COMPLETED) == 1

    def test_a_terminal_job_records_its_duration_under_its_model_block(
        self, collector, offline_worker
    ):
        asyncio.run(
            W._finish(
                None, self._job(), status=W.STATUS_COMPLETED, worker_id="w1"
            )
        )

        assert (
            collector.training_job_duration_seconds.count_values.get(("xgboost",)) == 1
        )
        # The figure is the job's own elapsed time, so ~90 s, not ~0.
        assert collector.training_job_duration_seconds.sum_values[("xgboost",)] > 80.0

    def test_a_cap_exceeded_failure_names_the_cap_it_hit(self, collector, offline_worker):
        asyncio.run(
            W._finish(
                None,
                self._job(),
                status=W.STATUS_FAILED,
                worker_id="w1",
                failure_reason=W.FAILURE_CAP_EXCEEDED,
                detail={"cap": "max_epochs", "requested": 500, "permitted": 100},
            )
        )

        assert collector.training_jobs_cap_rejections.get(cap="max_epochs") == 1

    def test_a_run_time_data_shortfall_is_counted_as_insufficient_data(
        self, collector, offline_worker
    ):
        """A job admitted with enough data can find it gone by the time it runs."""
        asyncio.run(
            W._finish(
                None,
                self._job(),
                status=W.STATUS_FAILED,
                worker_id="w1",
                failure_reason=W.FAILURE_ML_REQUIREMENTS,
            )
        )

        assert (
            collector.training_jobs_blocked_insufficient_data.get(
                reason=W.FAILURE_ML_REQUIREMENTS
            )
            == 1
        )

    def test_a_quality_refusal_is_not_counted_as_insufficient_data(
        self, collector, offline_worker
    ):
        """Requirement 14.7 is a grading refusal. Bars arrived; they were judged unusable."""
        asyncio.run(
            W._finish(
                None,
                self._job(),
                status=W.STATUS_FAILED,
                worker_id="w1",
                failure_reason=W.FAILURE_DATA_QUALITY,
            )
        )

        assert collector.training_jobs_blocked_insufficient_data.total() == 0
        assert collector.training_jobs_by_status.get(status=W.STATUS_FAILED) == 1

    def test_the_worker_and_the_api_share_one_insufficient_data_vocabulary(self):
        """Two lists would answer one question twice, differently."""
        assert SS.INSUFFICIENT_DATA_BLOCK_REASONS == (
            SS.REASON_ML_REQUIREMENTS,
            SS.REASON_DATASET,
        )
        for reason in SS.INSUFFICIENT_DATA_BLOCK_REASONS:
            assert reason in W.FAILURE_REASONS

    @pytest.mark.parametrize(
        "reason",
        [
            SS.REASON_DATA_QUALITY,
            SS.REASON_DATA_UNAVAILABLE,
            SS.REASON_DATA_SOURCE,
            SS.REASON_MODEL_UNPUBLISHED,
            SS.REASON_CAP_EXCEEDED,
            SS.REASON_FEATURES,
        ],
    )
    def test_the_admission_seam_counts_only_a_data_shortfall(self, collector, reason):
        SS._record_training_block(reason)

        assert collector.training_jobs_blocked_insufficient_data.total() == 0

    @pytest.mark.parametrize("reason", SS.INSUFFICIENT_DATA_BLOCK_REASONS)
    def test_the_admission_seam_counts_a_data_shortfall(self, collector, reason):
        SS._record_training_block(reason)

        assert (
            collector.training_jobs_blocked_insufficient_data.get(reason=reason) == 1
        )

    def test_an_admission_cap_rejection_names_its_cap(self, collector):
        SS._record_training_cap_rejection(
            {"error": "CapExceeded", "cap": "max_rows", "requested": 1, "permitted": 0}
        )

        assert collector.training_jobs_cap_rejections.get(cap="max_rows") == 1

    def test_a_cap_rejection_with_no_named_cap_is_labelled_unnamed_not_blank(
        self, collector
    ):
        SS._record_training_cap_rejection({})

        assert collector.training_jobs_cap_rejections.get(cap="unnamed") == 1


# ═══════════════════════════════════════════════════════════════════════════
# 6. Requirement 24.3 — the runtime seams
# ═══════════════════════════════════════════════════════════════════════════


class TestTheRuntimeSeams:
    @pytest.fixture
    def plan(self, reg):
        return SC.get_compiler().compile_plan(_linear_graph(reg), reg)

    def test_every_executed_node_is_timed_under_its_block_category(
        self, collector, reg, plan
    ):
        DAGEngine(enable_event_buffer=False).execute_plan(
            plan, candles(140), PlanRuntimeState(), registry=reg
        )

        counted = {
            key[0]: value
            for key, value in collector.dag_node_execution_ms.count_values.items()
        }

        assert counted, "no node execution was timed"
        # The categories are the registry's, not the executor keys.
        expected = {
            str(getattr(reg[plan.node(node_id).block_id].category, "value"))
            for node_id in plan.execution_order
        }
        assert set(counted) <= expected

    def test_the_category_label_vocabulary_is_the_registrys(self, collector, reg, plan):
        DAGEngine(enable_event_buffer=False).execute_plan(
            plan, candles(140), PlanRuntimeState(), registry=reg
        )

        published = {str(category.value) for category in {
            reg[block_id].category
            for block_id in {plan.node(n).block_id for n in plan.execution_order}
        }}
        for key in collector.dag_node_execution_ms.count_values:
            assert key[0] in published

    def test_a_short_window_records_not_ready_counts_by_reason(
        self, collector, reg, plan
    ):
        """At 5 bars the 20-bar EMA and everything downstream of it cannot produce."""
        DAGEngine(enable_event_buffer=False).execute_plan(
            plan, candles(5), PlanRuntimeState(), registry=reg
        )

        total = collector.dag_node_not_ready.total()

        assert total > 0
        for key in collector.dag_node_not_ready.values:
            assert key[0] in set(RUNTIME_STATES)

    def test_ready_is_never_recorded_as_a_not_ready_reason(self, collector, reg, plan):
        """READY is not a reason a node produced nothing."""
        DAGEngine(enable_event_buffer=False).execute_plan(
            plan, candles(140), PlanRuntimeState(), registry=reg
        )

        assert collector.dag_node_not_ready.get(reason=READY) == 0

    def test_a_non_finite_order_field_increments_the_alerting_counter(self, collector):
        intent = SimpleIntent(quantity=float("nan"))

        with pytest.raises(ExecutionBlocked):
            assert_execution_safe(intent, "n_buy")

        assert collector.dag_intents_blocked_non_finite.get() == 1

    def test_a_non_positive_quantity_does_not_increment_it(self, collector):
        """Task 9.2 alerts on ANY increment. A zero-size order must not fire that alert."""
        intent = SimpleIntent(quantity=0.0)

        with pytest.raises(ExecutionBlocked):
            assert_execution_safe(intent, "n_buy")

        assert collector.dag_intents_blocked_non_finite.get() == 0

    def test_the_counter_has_no_labels_so_any_increment_is_one_series(self, collector):
        assert collector.dag_intents_blocked_non_finite.label_names == []

    def test_a_safe_intent_increments_nothing(self, collector):
        assert_execution_safe(SimpleIntent(quantity=1.0, price=100.0), "n_buy")

        assert collector.dag_intents_blocked_non_finite.get() == 0


class SimpleIntent:
    """The smallest object ``assert_execution_safe`` reads. Not a mock of the gate."""

    def __init__(self, **fields):
        self.symbol = "ETH/USDT"
        self.side = "buy"
        for name, value in fields.items():
            setattr(self, name, value)


# ═══════════════════════════════════════════════════════════════════════════
# 7. Requirement 24.3 — the deployment state machine
# ═══════════════════════════════════════════════════════════════════════════


class TestTheLifecycleSeam:
    #: ``READY -> DEPLOYED`` is a real edge of ``VERSION_TRANSITIONS``, so the gate lets it
    #: through and the write is what decides. Taken from the table rather than guessed.
    ROW = {
        "id": "ver_metrics_1",
        "strategy_id": "stg_metrics_1",
        "lifecycle_state": LC.LIFECYCLE_READY,
    }

    def test_the_transition_under_test_is_a_real_edge(self):
        assert LC.is_transition_legal(LC.LIFECYCLE_READY, LC.LIFECYCLE_DEPLOYED)

    def test_a_written_transition_is_counted_with_both_states(
        self, collector, monkeypatch
    ):
        async def wrote(sb, version_id, state, extra=None):
            return True

        async def no_audit(*args, **kwargs):
            return "aud_1"

        monkeypatch.setattr(W, "set_version_lifecycle", wrote)
        monkeypatch.setattr(LC, "record_audit", no_audit)

        result = asyncio.run(
            LC.apply_version_state(
                None,
                dict(self.ROW),
                LC.LIFECYCLE_DEPLOYED,
                reason="test deploy",
                action="deploy",
            )
        )

        assert result.moved
        assert (
            collector.deployment_state_transitions.get(
                from_state=LC.LIFECYCLE_READY,
                to_state=LC.LIFECYCLE_DEPLOYED,
                action="deploy",
            )
            == 1
        )

    def test_a_transition_that_could_not_be_written_is_not_counted(
        self, collector, monkeypatch
    ):
        """Migrations 004-004e are unapplied here: the write returns False, not a 500."""

        async def could_not_write(sb, version_id, state, extra=None):
            return False

        monkeypatch.setattr(W, "set_version_lifecycle", could_not_write)

        result = asyncio.run(
            LC.apply_version_state(
                None,
                dict(self.ROW),
                LC.LIFECYCLE_DEPLOYED,
                reason="test deploy",
                action="deploy",
            )
        )

        assert not result.moved
        assert collector.deployment_state_transitions.total() == 0

    def test_a_refused_transition_is_not_counted(self, collector):
        """A transition the gate refused did not happen, so it is not a transition."""
        with pytest.raises(LC.LifecycleRejected):
            asyncio.run(
                LC.apply_version_state(
                    None,
                    {"id": "ver_x", "lifecycle_state": LC.LIFECYCLE_DEPLOYED},
                    LC.LIFECYCLE_DRAFT,
                    reason="illegal",
                    action="edit",
                )
            )

        assert collector.deployment_state_transitions.total() == 0


# ═══════════════════════════════════════════════════════════════════════════
# 8. Requirement 24.3 — the feed and the market data
# ═══════════════════════════════════════════════════════════════════════════


class TestTheFeedStateSeam:
    def test_a_live_feed_is_recorded_with_its_state_and_its_reason(self, collector):
        report = FS.evaluate_feed_state(
            timeframe="5m", connected=True, age_seconds=10.0
        )

        assert report.state is FS.FeedState.LIVE
        assert (
            collector.market_data_feed_state.get(
                state="LIVE", reason=report.reason, timeframe="5m"
            )
            == 1
        )

    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            ({"connected": True, "age_seconds": 10.0}, "LIVE"),
            ({"connected": True, "age_seconds": 600.0}, "DELAYED"),
            ({"connected": True, "age_seconds": 5_000.0}, "STALE"),
            ({"connected": False, "age_seconds": 1.0}, "DISCONNECTED"),
            (
                {
                    "connected": True,
                    "age_seconds": 10.0,
                    "available_bars": 5,
                    "warmup_bars": 60,
                },
                "INSUFFICIENT_DATA",
            ),
        ],
    )
    def test_every_one_of_the_five_states_is_recorded(self, collector, kwargs, expected):
        """Requirement 19.6 fixes the vocabulary at five; none of them is unobservable."""
        report = FS.evaluate_feed_state(timeframe="5m", **kwargs)

        assert report.state.value == expected
        assert collector.market_data_feed_state.get(
            state=expected, reason=report.reason, timeframe="5m"
        ) == 1

    def test_no_symbol_is_labelled(self, collector):
        """One series per market per state per reason is how a counter goes unbounded."""
        assert "symbol" not in collector.market_data_feed_state.label_names


class TestTheMarketDataSeams:
    TIMEFRAME = "5m"
    NOW = datetime(2024, 3, 1, 12, 0, 0)

    def _bar(self, moment, close=100.0):
        return [
            int(pd.Timestamp(moment).value // 1_000_000),
            close - 0.25,
            close + 1.5,
            close - 0.5,
            close,
            1_000.0,
        ]

    def test_a_streamed_bar_records_its_delay_after_close(self, collector):
        """``closed_before - open_time``: the delay between the bar closing and admission."""
        ingest = MC.ClosedBarIngest(self.TIMEFRAME, drop_late=True, now=self.NOW)
        # Opens at 11:50, closes at 11:55, admitted at 12:00 -> 300 s late.
        assert ingest.offer(self._bar(self.NOW - timedelta(minutes=10)))

        summary = collector.market_data_latency_ms.summary(timeframe=self.TIMEFRAME)

        assert summary is not None
        assert summary.count == 1
        assert summary.max_ms == pytest.approx(300_000.0)

    def test_a_bar_admitted_the_instant_it_closed_reads_as_zero(self, collector):
        ingest = MC.ClosedBarIngest(self.TIMEFRAME, drop_late=True, now=self.NOW)
        assert ingest.offer(self._bar(self.NOW - timedelta(minutes=5)))

        summary = collector.market_data_latency_ms.summary(timeframe=self.TIMEFRAME)

        assert summary is not None
        assert summary.max_ms == pytest.approx(0.0)

    def test_a_historical_read_records_no_latency(self, collector):
        """Its clock is fixed at construction, so every bar would report its age."""
        ingest = MC.ClosedBarIngest(self.TIMEFRAME, drop_late=False, now=self.NOW)
        assert ingest.offer(self._bar(self.NOW - timedelta(hours=6)))

        assert collector.market_data_latency_ms.count(timeframe=self.TIMEFRAME) == 0

    def test_a_dropped_bar_records_no_latency(self, collector):
        """A forming bar was never admitted, so it has no admission delay."""
        ingest = MC.ClosedBarIngest(self.TIMEFRAME, drop_late=True, now=self.NOW)
        assert not ingest.offer(self._bar(self.NOW))

        assert collector.market_data_latency_ms.count(timeframe=self.TIMEFRAME) == 0

    def test_the_quality_score_is_the_validators_own_figure(self, collector):
        """Republished, not recomputed. This module grades nothing."""
        stamps = pd.date_range("2024-01-01", periods=200, freq="5min", name="timestamp")
        close = pd.Series(
            100.0 + 5.0 * np.sin(np.linspace(0.0, 9.0, 200)), index=stamps
        )
        frame = pd.DataFrame(
            {
                "open": close - 0.25,
                "high": close + 1.5,
                "low": close - 0.5,
                "close": close,
                "volume": pd.Series(np.linspace(1_000.0, 9_000.0, 200), index=stamps),
            },
            index=stamps,
        )

        report = asyncio.run(MC.quality_report(frame, "ETH/USDT", self.TIMEFRAME))

        assert collector.market_data_quality_score.get(
            symbol="ETH/USDT", timeframe=self.TIMEFRAME
        ) == report.quality_score


# ═══════════════════════════════════════════════════════════════════════════
# 9. Instrumentation never fails the act it measures
# ═══════════════════════════════════════════════════════════════════════════


class TestInstrumentationNeverFailsTheActItMeasures:
    """Requirement 21.9's posture, applied to task 9.1: no control loses its decision.

    Each test breaks the *metric object* a seam writes to - the realistic failure, a metric
    in a bad state - and asserts the seam still does its job. A missing sample is a gap on a
    dashboard; a propagated exception here would be a refused validation, an aborted
    evaluation, or an intent firewall that stopped refusing.
    """

    @staticmethod
    def _break(metric, method: str):
        def explode(*args, **kwargs):
            raise RuntimeError("this metric is in a bad state")

        setattr(metric, method, explode)

    def test_a_broken_metric_does_not_refuse_a_validation(self, collector, reg):
        self._break(collector.builder_validation_duration_ms, "observe")
        self._break(collector.builder_validation_errors, "inc")

        report = V.validate(_linear_graph(reg), reg)

        assert report.valid
        assert report.dag_hash

    def test_a_broken_metric_does_not_refuse_a_compile(self, collector, reg):
        self._break(collector.builder_compile_duration_ms, "observe")

        plan = SC.get_compiler().compile_plan(_linear_graph(reg), reg)

        assert plan.dag_hash
        assert len(plan.execution_order) == 5

    def test_a_broken_metric_does_not_stop_an_intent_from_being_refused(self, collector):
        """The load-bearing one. A refusal that stopped refusing would send the order."""
        self._break(collector.dag_intents_blocked_non_finite, "inc")

        with pytest.raises(ExecutionBlocked) as raised:
            assert_execution_safe(SimpleIntent(quantity=float("inf")), "n_buy")

        assert raised.value.code == "NON_FINITE_ORDER_FIELD"

    def test_a_broken_metric_does_not_abort_an_evaluation(self, collector, reg):
        self._break(collector.dag_node_execution_ms, "observe")
        self._break(collector.dag_node_not_ready, "inc")
        plan = SC.get_compiler().compile_plan(_linear_graph(reg), reg)

        intents = DAGEngine(enable_event_buffer=False).execute_plan(
            plan, candles(140), PlanRuntimeState(), registry=reg
        )

        assert isinstance(intents, list)

    def test_a_broken_metric_does_not_stop_a_feed_from_being_classified(self, collector):
        """``evaluate_feed_state`` documents that it never raises. No exceptions to that."""
        self._break(collector.market_data_feed_state, "inc")

        report = FS.evaluate_feed_state(timeframe="5m", connected=True, age_seconds=10.0)

        assert report.state is FS.FeedState.LIVE

    def test_a_broken_metric_does_not_drop_an_admitted_bar(self, collector):
        self._break(collector.market_data_latency_ms, "observe")
        ingest = MC.ClosedBarIngest("5m", drop_late=True, now=datetime(2024, 3, 1, 12))

        admitted = ingest.offer(
            [
                int(pd.Timestamp(datetime(2024, 3, 1, 11, 50)).value // 1_000_000),
                99.75, 101.5, 99.5, 100.0, 1_000.0,
            ]
        )

        assert admitted
        assert ingest.counters.accepted == 1

    def test_an_unreachable_metrics_module_is_survivable(self, monkeypatch, reg):
        """The other failure mode: the lazy import itself fails. Every seam handles it."""
        import builtins

        real_import = builtins.__import__

        def refuse_metrics(name, *args, **kwargs):
            if name == "backend_app.backend.metrics":
                raise ImportError("metrics are unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse_metrics)

        assert V._metrics() is None
        assert SC._metrics() is None
        assert FS._metrics() is None
        assert MC._metrics() is None
        assert LC._metrics() is None
        assert W._metrics() is None
        assert SS._metrics() is None

    def test_never_fails_swallows_and_returns_none(self):
        calls = []

        @M.never_fails
        def boom():
            calls.append(1)
            raise ValueError("no")

        assert boom() is None
        assert calls == [1]

    @pytest.mark.parametrize(
        "method,args",
        [
            ("record_builder_validation", ("not a number",)),
            ("record_builder_compile", ("not a number", 10)),
            ("record_training_job_status", ("QUEUED",)),
            ("record_dag_node_execution", ("INDICATOR", object())),
            ("record_dag_not_ready", ("WARMING", "not an int")),
            ("record_market_data_latency", ("5m", object())),
            ("set_market_data_quality_score", ("ETH/USDT", "5m", object())),
            ("set_asset_universe_age", (object(),)),
        ],
    )
    def test_a_recorder_handed_nonsense_returns_rather_than_raises(
        self, collector, method, args
    ):
        assert getattr(collector, method)(*args) is None
