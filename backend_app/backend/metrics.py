"""
backend/metrics.py — Metrics System for Trading Platform (STEP 8.1)

PHASE 8: MONITORING + ALERTING + FAILSAFE INFRASTRUCTURE (FINAL PRODUCTION LAYER)
STEP 8.1: Metrics System (MANDATORY)

Purpose:
  - Track system performance and health metrics
  - Provide Prometheus-compatible metrics endpoint
  - Enable observability and alerting

Metrics Tracked:
  - trades_executed_total: Counter of successful trades
  - trades_blocked_total: Counter of blocked trades (by ExecutionGuard)
  - avg_execution_latency: Histogram of order execution latency
  - failed_orders_total: Counter of failed orders
  - websocket_disconnects: Counter of WebSocket disconnections
  - risk_score_distribution: Histogram of risk scores from ExecutionGuard

Exposed Endpoint:
  - GET /metrics: Prometheus format text

Usage:
    from backend_app.backend.metrics import MetricsCollector, metrics_collector
    
    # Record metrics
    metrics_collector.record_trade_executed(latency_ms=150)
    metrics_collector.record_trade_blocked(reason="insufficient_balance")
    metrics_collector.record_websocket_disconnect()
    metrics_collector.record_risk_score(risk_score=45.5)
    
    # Get Prometheus format
    metrics_text = metrics_collector.get_prometheus_metrics()
"""

import functools
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("Metrics")


# ══════════════════════════════════════════════════════════════════════════
#  NAME AND LABEL SAFETY (strategy-builder task 9.1)
# ══════════════════════════════════════════════════════════════════════════
# The strategy-builder metric names are dotted - `builder.validation.duration_ms`,
# `dag.node.execution_ms` - because that is the vocabulary the spec fixes and the
# vocabulary alerts, dashboards and task 9.2 refer to. Prometheus' exposition format does
# not admit a dot in a metric name, so the dotted name stays the metric's *identity*
# (`.name`, which is what a caller and a test address it by) and the exported line carries
# the sanitised form (`.prometheus_name`). One name, two renderings, rather than two names.
#
# Every pre-existing metric in this module is already a legal Prometheus name, so
# sanitisation is a no-op for all of them and no exported line changes.

_NAME_UNSAFE = re.compile(r"[^a-zA-Z0-9_:]")

#: Cap on an exported label value. A label is a dimension, not a payload: an unbounded
#: string would make one series per distinct value and could carry text that was never
#: meant to leave the process.
LABEL_VALUE_MAX_LENGTH = 64


def to_prometheus_name(name: str) -> str:
    """``"dag.node.execution_ms"`` -> ``"dag_node_execution_ms"``.

    Anything outside ``[a-zA-Z0-9_:]`` becomes ``_``, and a leading digit is prefixed, so
    the result is always a legal metric name. Idempotent, and the identity function on
    every name this module defined before task 9.1.
    """
    safe = _NAME_UNSAFE.sub("_", str(name))
    if safe and safe[0].isdigit():
        safe = f"_{safe}"
    return safe


def safe_label_value(value: Any) -> str:
    """One label value, rendered so it cannot corrupt the exposition or carry a payload.

    Backslashes, quotes and newlines are escaped as the exposition format requires - an
    unescaped quote would split one line into two malformed ones - and the result is
    truncated to :data:`LABEL_VALUE_MAX_LENGTH`. Callers pass vocabulary values (a block
    id, a failure code, a lifecycle state); this is the backstop, not the policy.
    """
    text = "" if value is None else str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", "\\n").replace("\r", "\\r").replace("\t", " ")
    if len(text) > LABEL_VALUE_MAX_LENGTH:
        text = text[:LABEL_VALUE_MAX_LENGTH]
    return text


def _label_string(names: Sequence[str], key: Tuple) -> str:
    """``'a="1",b="2"'`` for one label key. An empty value is omitted, as it always was."""
    return ",".join(
        f'{name}="{safe_label_value(val)}"'
        for name, val in zip(names, key)
        if val != "" and val is not None
    )


def never_fails(fn):
    """Instrumentation must never fail the act it measures.

    A recording call sits inside a validator, a compile, a training transition, an intent
    gate. If recording raised, an observability concern would decide whether a control ran
    - so it cannot raise. The failure is logged at debug level and the metric is simply not
    recorded, which is the only safe direction: a missing sample is a gap on a dashboard, a
    propagated exception is an outage.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - deliberately total; see the docstring
            try:
                logger.debug("Metric %s was not recorded.", fn.__name__, exc_info=True)
            except Exception:  # noqa: BLE001 - a logger that raises must not either
                pass
            return None

    return wrapper


@dataclass
class MetricValue:
    """Single metric data point."""
    timestamp: float
    value: float
    labels: Dict[str, str] = field(default_factory=dict)


class Counter:
    """Prometheus-style counter metric."""
    
    def __init__(self, name: str, description: str, labels: Optional[List[str]] = None):
        self.name = name
        self.prometheus_name = to_prometheus_name(name)
        self.description = description
        self.label_names = labels or []
        self.values: Dict[tuple, float] = {}
        self._lock = threading.Lock()
    
    def inc(self, value: float = 1, **label_values):
        """Increment counter."""
        key = tuple(label_values.get(low, "") for low in self.label_names)
        with self._lock:
            self.values[key] = self.values.get(key, 0) + value
    
    def get(self, **label_values) -> float:
        """Get current value."""
        key = tuple(label_values.get(low, "") for low in self.label_names)
        with self._lock:
            return self.values.get(key, 0)
    
    def total(self) -> float:
        """Every label combination summed. ``0`` when nothing was ever recorded."""
        with self._lock:
            return float(sum(self.values.values()))
    
    def to_prometheus(self) -> str:
        """Export to Prometheus format."""
        name = self.prometheus_name
        lines = [f"# HELP {name} {self.description}"]
        lines.append(f"# TYPE {name} counter")
        
        with self._lock:
            for key, value in self.values.items():
                label_str = _label_string(self.label_names, key)
                if label_str:
                    lines.append(f'{name}{{{label_str}}} {value}')
                else:
                    lines.append(f'{name} {value}')
        
        return "\n".join(lines)


class Histogram:
    """Prometheus-style histogram metric."""
    
    def __init__(
        self, 
        name: str, 
        description: str, 
        buckets: List[float],
        labels: Optional[List[str]] = None
    ):
        self.name = name
        self.prometheus_name = to_prometheus_name(name)
        self.description = description
        self.buckets = buckets
        self.label_names = labels or []
        self.bucket_counts: Dict[tuple, Dict[float, float]] = {}
        self.sum_values: Dict[tuple, float] = {}
        self.count_values: Dict[tuple, float] = {}
        self._lock = threading.Lock()
    
    def observe(self, value: float, **label_values):
        """Observe a value."""
        key = tuple(label_values.get(low, "") for low in self.label_names)
        
        with self._lock:
            if key not in self.bucket_counts:
                self.bucket_counts[key] = {b: 0 for b in self.buckets}
                self.sum_values[key] = 0
                self.count_values[key] = 0
            
            # Increment appropriate buckets
            for bucket in self.buckets:
                if value <= bucket:
                    self.bucket_counts[key][bucket] += 1
            
            self.sum_values[key] += value
            self.count_values[key] += 1
    
    def to_prometheus(self) -> str:
        """Export to Prometheus format.

        Bucket counts, a sum and a count - **not** the observed samples. A percentile read
        back out of this is therefore a bucket edge, not a percentile, which is why
        :class:`Summary` exists for the metrics that carry a stated numeric budget. See
        ``market_data_latency.LatencySummary`` and strategy-builder task 7.6.
        """
        name = self.prometheus_name
        lines = [f"# HELP {name} {self.description}"]
        lines.append(f"# TYPE {name} histogram")
        
        with self._lock:
            for key in self.bucket_counts:
                label_str = _label_string(self.label_names, key)
                label_prefix = f"{{{label_str}}}" if label_str else ""
                
                # Bucket values
                for bucket in self.buckets:
                    bucket_label = f'le="{bucket}"'
                    if label_str:
                        full_label = f"{label_str},{bucket_label}"
                    else:
                        full_label = bucket_label
                    count = self.bucket_counts[key].get(bucket, 0)
                    lines.append(f'{name}_bucket{{{full_label}}} {count}')
                
                # Sum and count
                lines.append(f'{name}_sum{label_prefix} {self.sum_values.get(key, 0)}')
                lines.append(f'{name}_count{label_prefix} {self.count_values.get(key, 0)}')
        
        return "\n".join(lines)


class Gauge:
    """Prometheus-style gauge metric."""
    
    def __init__(self, name: str, description: str, labels: Optional[List[str]] = None):
        self.name = name
        self.prometheus_name = to_prometheus_name(name)
        self.description = description
        self.label_names = labels or []
        self.values: Dict[tuple, float] = {}
        self._lock = threading.Lock()
    
    def set(self, value: float, **label_values):
        """Set gauge value."""
        key = tuple(label_values.get(low, "") for low in self.label_names)
        with self._lock:
            self.values[key] = value
    
    def inc(self, value: float = 1, **label_values):
        """Increment gauge."""
        key = tuple(label_values.get(low, "") for low in self.label_names)
        with self._lock:
            self.values[key] = self.values.get(key, 0) + value
    
    def dec(self, value: float = 1, **label_values):
        """Decrement gauge."""
        key = tuple(label_values.get(low, "") for low in self.label_names)
        with self._lock:
            self.values[key] = self.values.get(key, 0) - value
    
    def get(self, **label_values) -> Optional[float]:
        """The current value, or ``None`` when this label combination was never set.

        ``None`` rather than ``0``: a gauge nobody set and a gauge set to zero are
        different facts, and an age of zero reads as "refreshed just now".
        """
        key = tuple(label_values.get(low, "") for low in self.label_names)
        with self._lock:
            return self.values.get(key)
    
    def to_prometheus(self) -> str:
        """Export to Prometheus format."""
        name = self.prometheus_name
        lines = [f"# HELP {name} {self.description}"]
        lines.append(f"# TYPE {name} gauge")
        
        with self._lock:
            for key, value in self.values.items():
                label_str = _label_string(self.label_names, key)
                if label_str:
                    lines.append(f'{name}{{{label_str}}} {value}')
                else:
                    lines.append(f'{name} {value}')
        
        return "\n".join(lines)


class Summary:
    """Exact percentiles over retained samples. Added by strategy-builder task 9.1.

    WHY THIS EXISTS ALONGSIDE :class:`Histogram`
    --------------------------------------------
    :class:`Histogram` is Prometheus-shaped: it keeps bucket counts, not samples, so a
    percentile read back out of it is a **bucket edge**, decided by the bucket layout
    rather than by the population. Task 7.6 recorded that for the 25 ms margin in
    Requirement 19.12 and built ``market_data_latency.LatencySummary`` for it.

    Three strategy-builder metrics are compared against a stated number - Requirement 25.1
    (p95 compile at 200 nodes <= 50 ms), Requirement 25.2 (p95 validation <= 120 ms) and
    Requirement 19.12's 25 ms margin - so for those three the bucket layout must not be
    what decides the comparison. They use this class. Everything with no stated numeric
    budget stays a :class:`Histogram`, which is cheaper and is what the existing dashboards
    read.

    The percentile method is **not reimplemented here**: :meth:`summary` delegates to
    ``LatencySummary.from_samples``, so there is one nearest-rank implementation in the
    codebase and this class cannot drift from task 7.6's.

    Bounded by construction
        Samples are held in a ring of ``capacity`` per label combination, so memory is
        bounded however long the process runs, and the quantiles describe the recent
        population. ``_sum`` and ``_count`` are **lifetime** totals and are not affected by
        the ring, so the exported ``_sum`` / ``_count`` stay monotonic the way a Prometheus
        client expects.
    """

    #: Samples retained per label combination. 4096 covers task 9.6's 200-node runs with
    #: room to spare, at a few tens of kilobytes per series.
    DEFAULT_CAPACITY = 4096

    #: The quantiles exported, and the ones the spec's budgets are stated at.
    QUANTILES: Tuple[float, ...] = (0.5, 0.95, 0.99)

    def __init__(
        self,
        name: str,
        description: str,
        labels: Optional[List[str]] = None,
        capacity: int = DEFAULT_CAPACITY,
    ):
        self.name = name
        self.prometheus_name = to_prometheus_name(name)
        self.description = description
        self.label_names = labels or []
        self.capacity = int(capacity)
        self.samples: Dict[tuple, deque] = {}
        self.sum_values: Dict[tuple, float] = {}
        self.count_values: Dict[tuple, float] = {}
        self._lock = threading.Lock()

    def _key(self, label_values: Dict[str, Any]) -> tuple:
        return tuple(label_values.get(low, "") for low in self.label_names)

    def observe(self, value: float, **label_values):
        """Retain one sample.

        A non-finite observation is **discarded rather than stored**: an infinity would
        make every percentile above it an infinity, and a NaN would poison the sort. That
        is the same rule ``LatencySummary.from_samples`` applies to its input, applied one
        step earlier so the ring never holds a value it would have thrown away.
        """
        numeric = float(value)
        if numeric != numeric or numeric in (float("inf"), float("-inf")):
            return
        key = self._key(label_values)
        with self._lock:
            ring = self.samples.get(key)
            if ring is None:
                ring = deque(maxlen=self.capacity)
                self.samples[key] = ring
                self.sum_values[key] = 0.0
                self.count_values[key] = 0.0
            ring.append(numeric)
            self.sum_values[key] += numeric
            self.count_values[key] += 1

    def summary(self, **label_values):
        """``LatencySummary`` over the retained samples, or ``None`` for an empty one.

        ``None`` is deliberate and comes from task 7.6: an unmeasured population must not
        answer ``0``, which would read as "instant".
        """
        key = self._key(label_values)
        with self._lock:
            ring = self.samples.get(key)
            retained = list(ring) if ring else []
        if not retained:
            return None
        factory = _latency_summary()
        if factory is None:  # pragma: no cover - stdlib-only import cannot fail
            return None
        return factory.from_samples(retained)

    def count(self, **label_values) -> float:
        """Lifetime observation count for one label combination."""
        return self.count_values.get(self._key(label_values), 0.0)

    def to_prometheus(self) -> str:
        """Export as a Prometheus summary: quantiles, then ``_sum`` and ``_count``."""
        name = self.prometheus_name
        lines = [f"# HELP {name} {self.description}"]
        lines.append(f"# TYPE {name} summary")

        with self._lock:
            keys = list(self.samples)
        for key in keys:
            labels = {
                low: key[index] for index, low in enumerate(self.label_names)
            }
            summary = self.summary(**labels)
            label_str = _label_string(self.label_names, key)
            label_prefix = f"{{{label_str}}}" if label_str else ""
            if summary is not None:
                for quantile in self.QUANTILES:
                    attribute = f"p{int(round(quantile * 100))}_ms"
                    observed = getattr(summary, attribute, None)
                    if observed is None:
                        continue
                    quantile_label = f'quantile="{quantile}"'
                    full_label = (
                        f"{label_str},{quantile_label}" if label_str else quantile_label
                    )
                    lines.append(f"{name}{{{full_label}}} {observed}")
            lines.append(f"{name}_sum{label_prefix} {self.sum_values.get(key, 0)}")
            lines.append(f"{name}_count{label_prefix} {self.count_values.get(key, 0)}")

        return "\n".join(lines)


_LATENCY_SUMMARY_CACHE: List[Any] = []


def _latency_summary():
    """``market_data_latency.LatencySummary``, imported lazily and cached.

    Lazy so importing this module - which ``websocket_monitor`` does at import time - does
    not pull in the market-data decision module, and so a metrics import can never be what
    breaks a cold start.
    """
    if _LATENCY_SUMMARY_CACHE:
        return _LATENCY_SUMMARY_CACHE[0]
    try:
        from backend_app.backend.market_data_latency import LatencySummary
    except Exception:  # noqa: BLE001 - metrics never break their caller
        return None
    _LATENCY_SUMMARY_CACHE.append(LatencySummary)
    return LatencySummary


# ══════════════════════════════════════════════════════════════════════════
#  STRATEGY BUILDER VOCABULARY (task 9.1, Requirements 24.1, 24.2, 24.3)
# ══════════════════════════════════════════════════════════════════════════

#: ``builder.compile.duration_ms`` is recorded "by node count", and a raw count would make
#: one series per graph size. These are the measurement points Requirements 25.1/25.2 and
#: task 9.6 name, so a compile is filed under the smallest bucket at or above its node
#: count and the series count is fixed at five.
NODE_COUNT_BUCKETS: Tuple[int, ...] = (10, 50, 100, 200)

#: Label value for a graph above the largest bucket. Requirement 25.4 caps a graph at 200
#: nodes, so this should only ever be reached by a caller that compiled without the
#: capacity stage - and then it is visible rather than filed under "200".
NODE_COUNT_OVERFLOW = "200+"


def node_count_bucket(node_count: Any) -> str:
    """The ``node_count`` label value for a graph of ``node_count`` nodes.

    Named without the ``compile`` verb on purpose: ``tests/test_compiler_architecture.py``
    treats any ``compile``-named definition as a candidate graph-compile authority, and a
    label helper in the metrics module is not one. The test is right to be broad; this is
    the naming that keeps it honest instead of exempting a file from it.
    """
    try:
        count = int(node_count)
    except (TypeError, ValueError):
        return "unknown"
    if count < 0:
        return "unknown"
    for edge in NODE_COUNT_BUCKETS:
        if count <= edge:
            return str(edge)
    return NODE_COUNT_OVERFLOW


class MetricsCollector:
    """
    STEP 8.1: Central metrics collector for trading platform.
    
    Tracks key system metrics:
    - trades_executed_total: Successful trade executions
    - trades_blocked_total: Trades blocked by ExecutionGuard
    - execution_latency_ms: Order execution latency histogram
    - failed_orders_total: Failed order attempts
    - websocket_disconnects_total: WebSocket connection drops
    - risk_score_histogram: Risk score distribution
    - active_connections: Current active WebSocket connections
    - system_uptime_seconds: System uptime
    
    Usage:
        metrics = MetricsCollector()
        metrics.record_trade_executed(latency_ms=150, symbol="BTC")
        metrics.record_trade_blocked(reason="insufficient_balance")
        
        # Get Prometheus format for /metrics endpoint
        prometheus_text = metrics.get_prometheus_metrics()
    """
    
    def __init__(self):
        # Counters
        self.trades_executed_total = Counter(
            "trades_executed_total",
            "Total number of executed trades",
            labels=["symbol", "side"]
        )
        
        self.trades_blocked_total = Counter(
            "trades_blocked_total",
            "Total number of trades blocked by ExecutionGuard",
            labels=["reason", "symbol"]
        )
        
        self.failed_orders_total = Counter(
            "failed_orders_total",
            "Total number of failed order attempts",
            labels=["reason", "symbol"]
        )
        
        self.websocket_disconnects_total = Counter(
            "websocket_disconnects_total",
            "Total number of WebSocket disconnections",
            labels=["exchange"]
        )
        
        # Histograms
        self.execution_latency_ms = Histogram(
            "execution_latency_ms",
            "Order execution latency in milliseconds",
            buckets=[10, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
            labels=["symbol"]
        )
        
        self.risk_score_histogram = Histogram(
            "risk_score_distribution",
            "Distribution of risk scores from ExecutionGuard",
            buckets=[0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
            labels=["tenant_id"]
        )
        
        # Gauges
        self.active_connections = Gauge(
            "active_websocket_connections",
            "Current number of active WebSocket connections",
            labels=["exchange"]
        )
        
        # STEP 8: SCALING METRICS
        self.active_users = Gauge(
            "active_users_total",
            "Current number of active users (logged in with sessions)"
        )
        
        self.ws_server_connections = Gauge(
            "ws_server_connections_total",
            "Current number of WebSocket server connections"
        )
        
        self.tasks_queue_size = Gauge(
            "tasks_queue_size",
            "Current number of tasks in queue",
            labels=["queue_type"]  # dag, execution, portfolio
        )
        
        self.execution_latency_seconds = Gauge(
            "execution_latency_seconds",
            "Current execution latency (avg over last minute)",
            labels=["executor"]  # dag, order, position
        )
        
        self.system_uptime_seconds = Gauge(
            "system_uptime_seconds",
            "System uptime in seconds"
        )
        
        self._init_strategy_builder_metrics()
        self._init_marketplace_paper_metrics()
        
        # Track start time for uptime
        self._start_time = time.time()
        
        # Recent events buffer for alerting
        self._recent_blocks: deque = deque(maxlen=100)
        self._recent_failures: deque = deque(maxlen=100)
        self._lock = threading.Lock()
    
    # ══════════════════════════════════════════════════════════════════════
    #  STRATEGY BUILDER (task 9.1 — Requirements 24.1, 24.2, 24.3)
    # ══════════════════════════════════════════════════════════════════════
    #  The metric NAMES are the spec's, verbatim and dotted. Every one is defined here and
    #  exported by `get_prometheus_metrics`, so there is one registry rather than a second
    #  builder-shaped one alongside it.
    #
    #  WHAT NEVER APPEARS IN A NAME, A LABEL OR A VALUE
    #  ------------------------------------------------
    #  No exchange identifier, api key, secret or passphrase (SB-06, Requirement 12.1). No
    #  user id, tenant id, strategy id, version id, deployment id or node id either - not
    #  because those are secret, but because a per-tenant or per-resource label makes an
    #  unbounded series set, and an unbounded series set is how a metrics layer takes down
    #  the process it was added to observe. Every label below is drawn from a **closed
    #  vocabulary**: a failure code, a lifecycle state, a registry block id, a runtime
    #  state, a cap name, a bar interval, a registry resource name, a market symbol.
    #
    #  WHY EVERY RECORDING METHOD IS DECORATED
    #  ---------------------------------------
    #  `@never_fails`. These calls sit inside a validator, a compile, a training state
    #  transition and the intent gate. Instrumentation must not change a control's decision
    #  and must never fail the act it measures, so a recording failure is logged at debug
    #  and dropped. See :func:`never_fails`.

    def _init_strategy_builder_metrics(self):
        # -- Requirement 24.1: the builder API ----------------------------------
        #  Unlabelled on purpose: Requirement 25.2's p95 budget is stated over validation
        #  as a whole, and a label would split the population so that no single series
        #  answered it. A `Summary`, not a `Histogram`, because 120 ms is a stated number.
        self.builder_validation_duration_ms = Summary(
            "builder.validation.duration_ms",
            "Graph validation wall-clock duration in milliseconds (exact percentiles)",
        )
        self.builder_validation_errors = Counter(
            "builder.validation.errors",
            "Validation errors collected, by validation issue code",
            labels=["code"],
        )
        #  "by node count" from the task, bucketed by `node_count_bucket` so the
        #  series count is fixed at five and the buckets are the sizes 25.1 and 9.6 name.
        self.builder_compile_duration_ms = Summary(
            "builder.compile.duration_ms",
            "Compilation wall-clock duration in milliseconds, by graph node count",
            labels=["node_count"],
        )
        self.builder_compile_failures = Counter(
            "builder.compile.failures",
            "Compilations that produced no plan, by refusal reason",
            labels=["reason"],
        )
        self.builder_registry_requests = Counter(
            "builder.registry.requests",
            "Block registry responses served, by registry resource",
            labels=["resource"],
        )
        self.builder_registry_cache_hits = Counter(
            "builder.registry.cache_hits",
            "Registry requests answered 304 from the client's ETag, by resource",
            labels=["resource"],
        )
        self.builder_assets_universe_age_seconds = Gauge(
            "builder.assets.universe_age_seconds",
            "Age of the served asset universe snapshot in seconds",
        )

        # -- Requirement 24.2: the training service ----------------------------
        self.training_jobs_by_status = Counter(
            "training.jobs.by_status",
            "Training job status transitions recorded, by status",
            labels=["status"],
        )
        self.training_job_duration_seconds = Histogram(
            "training.job.duration_seconds",
            "Training job wall-clock duration in seconds, by model block",
            buckets=[1, 5, 15, 30, 60, 300, 900, 1800, 3600, 7200, 21600],
            labels=["block_id"],
        )
        self.training_jobs_cap_rejections = Counter(
            "training.jobs.cap_rejections",
            "Training requests refused by a resource cap, by cap name",
            labels=["cap"],
        )
        self.training_jobs_blocked_insufficient_data = Counter(
            "training.jobs.blocked_insufficient_data",
            "Training requests blocked for want of data, by block reason",
            labels=["reason"],
        )

        # -- Requirement 24.3: the DAG runtime and the feed --------------------
        self.dag_node_execution_ms = Histogram(
            "dag.node.execution_ms",
            "Per-node execution duration in milliseconds, by block category",
            buckets=[0.5, 1, 5, 10, 25, 50, 100, 250, 500, 1000, 5000],
            labels=["category"],
        )
        self.dag_node_not_ready = Counter(
            "dag.node.not_ready",
            "Node evaluations that produced no value, by runtime state",
            labels=["reason"],
        )
        #  Unlabelled: task 9.2 alerts on ANY increment, so "any increment" has to be one
        #  series rather than a sum a rule has to remember to take.
        self.dag_intents_blocked_non_finite = Counter(
            "dag.intents.blocked_non_finite",
            "Trade intents refused at the action boundary for a non-finite order field",
        )
        self.deployment_state_transitions = Counter(
            "deployment.state_transitions",
            "Lifecycle transitions written, by from-state, to-state and action",
            labels=["from_state", "to_state", "action"],
        )
        #  A `Summary`, for Requirement 19.12's 25 ms margin. See :class:`Summary`.
        self.market_data_latency_ms = Summary(
            "market_data.latency_ms",
            "Delay in milliseconds between a bar closing and its admission, by timeframe",
            labels=["timeframe"],
        )
        self.market_data_quality_score = Gauge(
            "market_data.quality_score",
            "Last measured DataQualityReport score (0-100), by market",
            labels=["symbol", "timeframe"],
        )
        self.market_data_feed_state = Counter(
            "market_data.feed_state",
            "Feed state classifications, by state, reason and timeframe",
            labels=["state", "reason", "timeframe"],
        )

        # -- marketplace-subscriptions-paper-trading Requirements 14.10, 26.6, 27.6 ----
        #  The Paper_Session's market-data feed. Requirement 26.6 requires a latency and an
        #  error-rate metric for "market-data delivery" introduced by that spec, and
        #  Requirement 14.10 requires the session to measure and expose delivery latency and
        #  feed health. Declared here, on the one collector, rather than in a second registry
        #  under `backend/paper/`: /metrics stays the one place the platform is scraped.
        #
        #  A `Summary` for the latency, for the same reason `market_data_latency_ms` is one -
        #  these figures are read as percentiles and a bucket edge would decide them by the
        #  bucket layout. Counters for the three drop reasons, because Requirement 14.7's
        #  guarantees are counted events and an operator alerts on any increment.
        self.paper_feed_latency_ms = Summary(
            "paper.feed.latency_ms",
            "Delay in milliseconds between a paper market event's own timestamp and the "
            "session processing it, by symbol and timeframe",
            labels=["symbol", "timeframe"],
        )
        self.paper_feed_events = Counter(
            "paper.feed.events",
            "Paper market-data events accepted and recorded, by symbol and transport",
            labels=["symbol", "transport"],
        )
        self.paper_feed_invalid = Counter(
            "paper.feed.invalid",
            "Paper market-data events dropped by normalisation or validation, never "
            "repaired, by symbol and refusal reason",
            labels=["symbol", "reason"],
        )
        self.paper_feed_duplicates = Counter(
            "paper.feed.duplicates",
            "Paper market-data events discarded as an already-processed event identity, by "
            "symbol and which arbiter caught it (the in-process LRU or the unique index)",
            labels=["symbol", "arbiter"],
        )
        self.paper_feed_out_of_order = Counter(
            "paper.feed.out_of_order",
            "Paper market-data events dropped for a timestamp below the last one processed "
            "for that symbol, by symbol",
            labels=["symbol"],
        )
        self.paper_feed_state = Counter(
            "paper.feed.state",
            "Paper session feed-state transitions, by state and the reason measured",
            labels=["state", "reason"],
        )
        self.paper_feed_reconnects = Counter(
            "paper.feed.reconnects",
            "Paper market-data reconnection attempts, by outcome",
            labels=["outcome"],
        )

    # ══════════════════════════════════════════════════════════════════════
    #  marketplace-subscriptions-paper-trading task 33.5
    #  (Requirements 26.6, 27.6 — design.md § "Metrics (Requirement 26.6)")
    # ══════════════════════════════════════════════════════════════════════
    #  The rest of that table: the HTTP surfaces, the WebSocket event types, signal
    #  generation, paper order execution, the database operations and the expiry sweep. The
    #  seven `paper.feed.*` names above were declared by task 24.x and are NOT redeclared or
    #  renamed here; this method is purely additive.
    #
    #  WHERE THE DESIGN WRITES `{route}`, `{event_type}`, `{operation}` OR `{reason}`
    #  ----------------------------------------------------------------------------
    #  Those braces are a DIMENSION, not a name fragment. They become labels, exactly as
    #  `paper.feed.state{HEALTHY,DEGRADED,FALLBACK_REST}` became a `state` label above and
    #  `paper.order.rejected{reason}` becomes a `reason` label below. One metric with a label
    #  rather than one metric per value: a name assembled from a path or a table name would
    #  create a metric family per value, which is how a metrics layer takes down the process it
    #  was added to observe.
    #
    #  Every dimension used here is drawn from a CLOSED vocabulary: a FastAPI route TEMPLATE
    #  (never a resolved path, so an id can never become a series), an HTTP status class, a
    #  `PaperEvent` member, a `rejection_reason` from Requirement 16.5's eight, a
    #  `paper_repository._execute` `what` literal, a sweep stage name.
    #
    #  WHY THE ERROR RATE NEEDS NO SEPARATE DENOMINATOR
    #  ------------------------------------------------
    #  Requirement 26.6 asks for "latency and error-rate metrics". `Summary` exports `_count`
    #  alongside its quantiles, and the latency summary is observed on EVERY request, so the
    #  rate is `errors / latency_ms_count` and there is no third metric to keep in step with
    #  the other two.

    def _init_marketplace_paper_metrics(self):
        # -- each API endpoint introduced or modified ---------------------------
        #  `Summary` rather than `Histogram`: an endpoint latency is read as a percentile, and
        #  a bucket edge would be decided by the bucket layout rather than by the population.
        self.marketplace_http_latency_ms = Summary(
            "marketplace.http.latency_ms",
            "Marketplace HTTP request duration in milliseconds, by route template",
            labels=["route"],
        )
        self.marketplace_http_errors = Counter(
            "marketplace.http.errors",
            "Marketplace HTTP responses with a 4xx or 5xx status, by route template and "
            "status class",
            labels=["route", "status"],
        )
        self.paper_http_latency_ms = Summary(
            "paper.http.latency_ms",
            "Paper trading HTTP request duration in milliseconds, by route template",
            labels=["route"],
        )
        self.paper_http_errors = Counter(
            "paper.http.errors",
            "Paper trading HTTP responses with a 4xx or 5xx status, by route template and "
            "status class",
            labels=["route", "status"],
        )

        # -- each WebSocket event type -----------------------------------------
        self.paper_ws_emitted = Counter(
            "paper.ws.emitted",
            "Paper_Channel frames written to a subscriber, by event type",
            labels=["event_type"],
        )
        self.paper_ws_latency_ms = Summary(
            "paper.ws.latency_ms",
            "Wall-clock duration in milliseconds of one Paper_Channel fan-out, by event type",
            labels=["event_type"],
        )
        self.paper_ws_errors = Counter(
            "paper.ws.errors",
            "Paper_Channel deliveries that did not reach a subscriber, by event type and "
            "reason (a failed write, a revoked ownership, a raising handler)",
            labels=["event_type", "reason"],
        )
        #  A Gauge, and unlabelled: Requirement 19.11's bound is per connection, so what an
        #  operator alerts on is the DEEPEST queue in the process at the last fan-out. A
        #  per-connection label would make one series per socket.
        self.paper_ws_queue_depth = Gauge(
            "paper.ws.queue_depth",
            "Deepest per-connection pending-event queue observed at the last Paper_Channel "
            "fan-out (Requirement 19.11's counter)",
        )
        self.paper_ws_slow_consumer_disconnects = Counter(
            "paper.ws.slow_consumer_disconnects",
            "Paper_Channel subscriptions closed for exceeding the pending-event bound",
        )

        # -- signal generation --------------------------------------------------
        self.paper_signal_latency_ms = Summary(
            "paper.signal.latency_ms",
            "Delay in milliseconds between the bar a signal was produced from being received "
            "and the signal being read, by symbol",
            labels=["symbol"],
        )
        self.paper_signal_generated = Counter(
            "paper.signal.generated",
            "Signals a Paper_Session read off its strategy runtime, by symbol",
            labels=["symbol"],
        )
        self.paper_signal_errors = Counter(
            "paper.signal.errors",
            "Strategy outputs a Paper_Session could not act on, by refusal reason",
            labels=["reason"],
        )

        # -- paper order execution ---------------------------------------------
        #  Unlabelled: these are the two figures an operator compares against a budget for the
        #  session as a whole, and a label would split the population so that no single series
        #  answered it - the same reason `builder_validation_duration_ms` is unlabelled.
        self.paper_order_submit_latency_ms = Summary(
            "paper.order.submit_latency_ms",
            "Wall-clock duration in milliseconds of one paper order submission, from intent "
            "to persisted order",
        )
        self.paper_fill_apply_latency_ms = Summary(
            "paper.fill.apply_latency_ms",
            "Wall-clock duration in milliseconds of applying one paper fill to the account",
        )
        self.paper_order_rejected = Counter(
            "paper.order.rejected",
            "Paper order intents refused, by refusal reason - Requirement 16.5's eight names, "
            "whether the refusal was persisted as a REJECTED order or the intent's own values "
            "made such a row unrepresentable",
            labels=["reason"],
        )
        self.paper_order_concurrency_conflicts = Counter(
            "paper.order.concurrency_conflicts",
            "Paper order write paths that gave up after the bounded retries, by operation",
            labels=["operation"],
        )
        self.paper_order_retries = Counter(
            "paper.order.retries",
            "Retryable conflicts on a paper order write path, by operation",
            labels=["operation"],
        )

        # -- database operations ------------------------------------------------
        #  `round_trips` is the one P-57 is about (Requirements 27.1, 27.2): a page whose cost
        #  grows with its row count shows up here as a count that grows with it. Counted at the
        #  statement boundary, so it is one increment per statement issued and nothing has to
        #  remember to count.
        self.marketplace_db_latency_ms = Summary(
            "marketplace.db.latency_ms",
            "Marketplace Persistence_Layer statement duration in milliseconds, by operation",
            labels=["operation"],
        )
        self.marketplace_db_errors = Counter(
            "marketplace.db.errors",
            "Marketplace Persistence_Layer statements that did not complete, by operation",
            labels=["operation"],
        )
        self.marketplace_db_round_trips = Counter(
            "marketplace.db.round_trips",
            "Marketplace Persistence_Layer statements issued, by operation - the figure "
            "Requirements 27.1 and 27.2 bound",
            labels=["operation"],
        )
        self.paper_db_latency_ms = Summary(
            "paper.db.latency_ms",
            "Paper Persistence_Layer statement duration in milliseconds, by operation",
            labels=["operation"],
        )
        self.paper_db_errors = Counter(
            "paper.db.errors",
            "Paper Persistence_Layer statements that did not complete, by operation",
            labels=["operation"],
        )
        self.paper_db_round_trips = Counter(
            "paper.db.round_trips",
            "Paper Persistence_Layer statements issued, by operation",
            labels=["operation"],
        )

        # -- the expiry sweep ---------------------------------------------------
        self.marketplace_expiry_sweep_duration_ms = Summary(
            "marketplace.expiry_sweep.duration_ms",
            "Wall-clock duration in milliseconds of one Subscription expiry sweep pass",
        )
        self.marketplace_expiry_sweep_transitions = Counter(
            "marketplace.expiry_sweep.transitions",
            "Subscription_State transitions written by the expiry sweep",
        )
        self.marketplace_expiry_sweep_errors = Counter(
            "marketplace.expiry_sweep.errors",
            "Expiry sweep steps that failed, by stage",
            labels=["stage"],
        )
        #  A Gauge holding a UNIX timestamp, and the health-check input Requirement 26.6 names.
        #  It is never set on a pass that failed - see `expiry_sweep.last_run_at` for why a
        #  permanently broken sweep that stamped this on every attempt would look permanently
        #  healthy. Unset (`Gauge.get() is None`) therefore means "no pass has ever fully
        #  succeeded in this process", which is a different fact from a stale timestamp.
        self.marketplace_expiry_sweep_last_run_at = Gauge(
            "marketplace.expiry_sweep.last_run_at",
            "UNIX timestamp of the last FULLY SUCCESSFUL Subscription expiry sweep",
        )

    #: Every strategy-builder metric, in the order Requirements 24.1, 24.2 and 24.3 list
    #: them. Named once so the exporter and any test read the same list.
    STRATEGY_BUILDER_METRIC_ATTRIBUTES: Tuple[str, ...] = (
        "builder_validation_duration_ms",
        "builder_validation_errors",
        "builder_compile_duration_ms",
        "builder_compile_failures",
        "builder_registry_requests",
        "builder_registry_cache_hits",
        "builder_assets_universe_age_seconds",
        "training_jobs_by_status",
        "training_job_duration_seconds",
        "training_jobs_cap_rejections",
        "training_jobs_blocked_insufficient_data",
        "dag_node_execution_ms",
        "dag_node_not_ready",
        "dag_intents_blocked_non_finite",
        "deployment_state_transitions",
        "market_data_latency_ms",
        "market_data_quality_score",
        "market_data_feed_state",
    )

    #: The Paper_Session market-data feed metrics (marketplace-subscriptions-paper-trading
    #: Requirements 14.10, 26.6). A SEPARATE tuple from
    #: :data:`STRATEGY_BUILDER_METRIC_ATTRIBUTES` on purpose: that list is asserted
    #: element-for-element against the strategy-builder requirement text in
    #: ``tests/test_task_9_1_builder_metrics.py``, and appending a paper metric to it would make
    #: that assertion fail for a metric the requirement it checks does not mention. Both lists
    #: reach the same ``/metrics`` exposition through :meth:`get_prometheus_metrics`.
    PAPER_FEED_METRIC_ATTRIBUTES: Tuple[str, ...] = (
        "paper_feed_latency_ms",
        "paper_feed_events",
        "paper_feed_invalid",
        "paper_feed_duplicates",
        "paper_feed_out_of_order",
        "paper_feed_state",
        "paper_feed_reconnects",
    )

    def strategy_builder_metrics(self) -> List[Any]:
        """The metric objects for :data:`STRATEGY_BUILDER_METRIC_ATTRIBUTES`."""
        return [
            getattr(self, attribute)
            for attribute in self.STRATEGY_BUILDER_METRIC_ATTRIBUTES
        ]

    #: The rest of ``design.md``'s Requirement 26.6 table (task 33.5), in the table's own row
    #: order: the HTTP surfaces, the WebSocket event types, signal generation, paper order
    #: execution, the database operations and the expiry sweep. A THIRD tuple for the same
    #: reason :data:`PAPER_FEED_METRIC_ATTRIBUTES` is a second one - each list is asserted
    #: against the requirement text that names it, and folding these into either of the other
    #: two would make that assertion fail for a metric its requirement does not mention. All
    #: three reach the same ``/metrics`` exposition through :meth:`get_prometheus_metrics`.
    MARKETPLACE_PAPER_METRIC_ATTRIBUTES: Tuple[str, ...] = (
        "marketplace_http_latency_ms",
        "marketplace_http_errors",
        "paper_http_latency_ms",
        "paper_http_errors",
        "paper_ws_emitted",
        "paper_ws_latency_ms",
        "paper_ws_errors",
        "paper_ws_queue_depth",
        "paper_ws_slow_consumer_disconnects",
        "paper_signal_latency_ms",
        "paper_signal_generated",
        "paper_signal_errors",
        "paper_order_submit_latency_ms",
        "paper_fill_apply_latency_ms",
        "paper_order_rejected",
        "paper_order_concurrency_conflicts",
        "paper_order_retries",
        "marketplace_db_latency_ms",
        "marketplace_db_errors",
        "marketplace_db_round_trips",
        "paper_db_latency_ms",
        "paper_db_errors",
        "paper_db_round_trips",
        "marketplace_expiry_sweep_duration_ms",
        "marketplace_expiry_sweep_transitions",
        "marketplace_expiry_sweep_errors",
        "marketplace_expiry_sweep_last_run_at",
    )

    def paper_feed_metrics(self) -> List[Any]:
        """The metric objects for :data:`PAPER_FEED_METRIC_ATTRIBUTES`."""
        return [
            getattr(self, attribute)
            for attribute in self.PAPER_FEED_METRIC_ATTRIBUTES
        ]

    def marketplace_paper_metrics(self) -> List[Any]:
        """The metric objects for :data:`MARKETPLACE_PAPER_METRIC_ATTRIBUTES`."""
        return [
            getattr(self, attribute)
            for attribute in self.MARKETPLACE_PAPER_METRIC_ATTRIBUTES
        ]

    # -- Requirement 24.1 ---------------------------------------------------

    @never_fails
    def record_builder_validation(
        self, duration_ms: float, codes: Optional[Iterable[str]] = None
    ):
        """One validation pass: its duration, and one increment per error code collected.

        ``codes`` is the report's **error** codes. Warnings are not errors and are not
        counted here, because Requirement 24.1 asks for "validation error counts by code"
        and folding warnings in would make the figure unusable as an error rate.
        """
        self.builder_validation_duration_ms.observe(float(duration_ms))
        for code in codes or ():
            self.builder_validation_errors.inc(code=safe_label_value(code))

    @never_fails
    def record_builder_compile(self, duration_ms: float, node_count: Any):
        """One successful compile: its duration, filed under its node-count bucket."""
        self.builder_compile_duration_ms.observe(
            float(duration_ms), node_count=node_count_bucket(node_count)
        )

    @never_fails
    def record_builder_compile_failure(self, reason: str):
        """One compile that produced no plan."""
        self.builder_compile_failures.inc(reason=safe_label_value(reason))

    @never_fails
    def record_builder_registry_request(self, resource: str, cache_hit: bool = False):
        """One registry response. A 304 counts as both a request and a cache hit.

        Both, not either: the hit ratio Requirement 25.3 cares about is hits over requests,
        so a hit that was not also counted as a request would make the denominator wrong.
        """
        label = safe_label_value(resource)
        self.builder_registry_requests.inc(resource=label)
        if cache_hit:
            self.builder_registry_cache_hits.inc(resource=label)

    @never_fails
    def set_asset_universe_age(self, age_seconds: float):
        """The age of the universe snapshot that was just served."""
        age = float(age_seconds)
        if age != age or age in (float("inf"), float("-inf")) or age < 0:
            return
        self.builder_assets_universe_age_seconds.set(age)

    # -- Requirement 24.2 ---------------------------------------------------

    @never_fails
    def record_training_job_status(
        self,
        status: str,
        *,
        block_id: Optional[str] = None,
        duration_seconds: Optional[float] = None,
    ):
        """One training job status transition, and its duration when it has ended.

        ``duration_seconds`` is supplied by the caller from the job row's own elapsed time
        rather than measured here, so the figure is the one the duration cap in Requirement
        15.8 is enforced against and not a second reading of it.
        """
        self.training_jobs_by_status.inc(status=safe_label_value(status))
        if duration_seconds is None:
            return
        duration = float(duration_seconds)
        if duration != duration or duration < 0:
            return
        self.training_job_duration_seconds.observe(
            duration, block_id=safe_label_value(block_id or "unknown")
        )

    @never_fails
    def record_training_cap_rejection(self, cap: str):
        """One training request refused by one cap (Requirement 16.3's named cap)."""
        self.training_jobs_cap_rejections.inc(cap=safe_label_value(cap))

    @never_fails
    def record_training_blocked_insufficient_data(self, reason: str):
        """One training request blocked because there was not enough usable data."""
        self.training_jobs_blocked_insufficient_data.inc(reason=safe_label_value(reason))

    # -- Requirement 24.3 ---------------------------------------------------

    @never_fails
    def record_dag_node_execution(self, category: str, duration_ms: float):
        """One node executed, by Block_Category."""
        self.dag_node_execution_ms.observe(
            float(duration_ms), category=safe_label_value(category)
        )

    @never_fails
    def record_dag_not_ready(self, reason: str, count: int = 1):
        """``count`` node evaluations that produced no value, for one runtime state."""
        increment = int(count)
        if increment <= 0:
            return
        self.dag_node_not_ready.inc(increment, reason=safe_label_value(reason))

    @never_fails
    def record_dag_intent_blocked_non_finite(self):
        """One Trade_Intent refused for a non-finite order field (Requirement 20.5)."""
        self.dag_intents_blocked_non_finite.inc()

    @never_fails
    def record_deployment_state_transition(
        self, from_state: Any, to_state: Any, action: Optional[str] = None
    ):
        """One lifecycle transition that was actually written."""
        self.deployment_state_transitions.inc(
            from_state=safe_label_value(from_state or "unknown"),
            to_state=safe_label_value(to_state or "unknown"),
            action=safe_label_value(action or "transition"),
        )

    @never_fails
    def record_market_data_latency(self, timeframe: str, latency_ms: float):
        """How long after its close one bar was admitted."""
        self.market_data_latency_ms.observe(
            float(latency_ms), timeframe=safe_label_value(timeframe)
        )

    @never_fails
    def set_market_data_quality_score(self, symbol: str, timeframe: str, score: float):
        """The graded quality of the window that was just validated.

        ``None`` records nothing. An ungraded window and a window graded zero are different
        facts, and a zero would read as "unusable data" on a dashboard.
        """
        if score is None:
            return
        value = float(score)
        if value != value or value in (float("inf"), float("-inf")):
            return
        self.market_data_quality_score.set(
            value,
            symbol=safe_label_value(symbol),
            timeframe=safe_label_value(timeframe),
        )

    @never_fails
    def record_feed_state(
        self, state: Any, reason: Optional[str] = None, timeframe: Optional[str] = None
    ):
        """One feed-state classification, with the reason it was measured to be that."""
        label = getattr(state, "value", state)
        self.market_data_feed_state.inc(
            state=safe_label_value(label),
            reason=safe_label_value(reason or ""),
            timeframe=safe_label_value(timeframe or ""),
        )

    # -- marketplace-subscriptions-paper-trading Requirements 14.10, 26.6 ---------------

    @never_fails
    def record_paper_feed_latency(
        self, latency_ms: Any, symbol: str = "", timeframe: str = ""
    ):
        """One accepted paper market event's delivery delay (Requirement 14.10).

        ``latency_ms`` may arrive as a ``Decimal`` - the feed measures it in exact decimal
        arithmetic, because the same instants feed a persisted ``NUMERIC(10,3)`` column. It is
        converted to ``float`` **here and only here**: a percentile is a presentation figure, not
        a money or price computation, so Requirement 18.1 is not in play, and ``Summary`` retains
        floats. ``None`` records nothing - an unmeasurable delay and a zero delay are different
        facts.
        """
        if latency_ms is None:
            return
        self.paper_feed_latency_ms.observe(
            float(latency_ms),
            symbol=safe_label_value(symbol),
            timeframe=safe_label_value(timeframe),
        )

    @never_fails
    def record_paper_feed_event(self, symbol: str = "", transport: str = ""):
        """One market event accepted, validated and recorded to ``paper_market_events``."""
        self.paper_feed_events.inc(
            symbol=safe_label_value(symbol), transport=safe_label_value(transport)
        )

    @never_fails
    def record_paper_feed_invalid(self, symbol: str = "", reason: str = ""):
        """``paper.feed.invalid``: one event dropped rather than repaired (Requirement 14.9)."""
        self.paper_feed_invalid.inc(
            symbol=safe_label_value(symbol), reason=safe_label_value(reason)
        )

    @never_fails
    def record_paper_feed_duplicate(self, symbol: str = "", arbiter: str = ""):
        """One already-processed event identity discarded (Requirement 14.7).

        ``arbiter`` distinguishes the in-process LRU from the ``uq_paper_market_event`` unique
        index, because the two say different things about the deployment: LRU hits are a chatty
        feed, unique-index hits are a cache miss - a restarted worker, an evicted entry, or two
        workers on one session - and only the second is worth an operator's attention.
        """
        self.paper_feed_duplicates.inc(
            symbol=safe_label_value(symbol), arbiter=safe_label_value(arbiter)
        )

    @never_fails
    def record_paper_feed_out_of_order(self, symbol: str = ""):
        """``paper.feed.out_of_order``: one event below the symbol's last timestamp."""
        self.paper_feed_out_of_order.inc(symbol=safe_label_value(symbol))

    @never_fails
    def record_paper_feed_state(self, state: Any, reason: str = ""):
        """One paper feed-state transition, with the reason it was measured to be that."""
        label = getattr(state, "value", state)
        self.paper_feed_state.inc(
            state=safe_label_value(label), reason=safe_label_value(reason)
        )

    @never_fails
    def record_paper_feed_reconnect(self, outcome: str = ""):
        """One reconnection attempt after a dropped subscription (Requirement 14.5)."""
        self.paper_feed_reconnects.inc(outcome=safe_label_value(outcome))

    # -- task 33.5: the rest of Requirement 26.6's table ------------------------------
    #
    #  Every method below is `@never_fails` for the reason stated on :func:`never_fails`, and
    #  that reason is load-bearing HERE in particular: these calls sit inside the delivery loop
    #  of a WebSocket fan-out, inside the statement boundary of the paper repository and inside
    #  the order submission path. A recording failure must never be what drops a market event, a
    #  fill or a response, so it is logged at debug and the measured act carries on.

    @staticmethod
    def status_class(status_code: Any) -> str:
        """``503`` -> ``'5xx'``. The bounded form of an HTTP status, for a label.

        A label per distinct status would be bounded anyway, but an error RATE is read per
        class, and this is the vocabulary the alerting rules use. Anything unreadable as an
        integer becomes ``'unknown'`` rather than raising inside instrumentation.
        """
        try:
            code = int(status_code)
        except (TypeError, ValueError):
            return "unknown"
        if code < 100 or code > 599:
            return "unknown"
        return f"{code // 100}xx"

    @never_fails
    def record_http_request(
        self, domain: str, route: str, status_code: Any, duration_ms: Any
    ):
        """One request to a route this specification introduced or modified.

        ``domain`` selects the metric family - ``'marketplace'`` or ``'paper'``. An unknown
        domain records NOTHING rather than being filed under one of the two: a figure attributed
        to the wrong surface is worse than a missing one, and the caller
        (``core/http_metrics.py``) only ever passes a route it classified.

        ``route`` is a route TEMPLATE (``/api/paper/sessions/{session_id}``), never a resolved
        path, so a session identifier cannot become a series - and cannot appear in the
        exposition at all, which also keeps another user's identifiers out of it
        (Requirement 26.4).
        """
        if domain == "marketplace":
            latency, errors = self.marketplace_http_latency_ms, self.marketplace_http_errors
        elif domain == "paper":
            latency, errors = self.paper_http_latency_ms, self.paper_http_errors
        else:
            return
        label = safe_label_value(route)
        if duration_ms is not None:
            latency.observe(float(duration_ms), route=label)
        status = self.status_class(status_code)
        if status in ("4xx", "5xx", "unknown"):
            errors.inc(route=label, status=status)

    @never_fails
    def record_paper_ws_fanout(
        self,
        event_type: str,
        *,
        delivered: int = 0,
        duration_ms: Any = None,
        queue_depth: Any = None,
    ):
        """One Paper_Channel fan-out of one frame: how many got it, and how long it took.

        ``delivered`` is the number of connections the frame was actually WRITTEN to, not the
        number subscribed: a frame counted for a connection that was closed for falling behind
        would make the emitted count disagree with what any client received.

        ``queue_depth`` is the deepest pending count seen during the fan-out. ``None`` leaves
        the gauge alone, which is not the same as setting it to zero - see :meth:`Gauge.get`.
        """
        label = safe_label_value(event_type)
        count = int(delivered)
        if count > 0:
            self.paper_ws_emitted.inc(count, event_type=label)
        if duration_ms is not None:
            self.paper_ws_latency_ms.observe(float(duration_ms), event_type=label)
        if queue_depth is not None:
            depth = float(queue_depth)
            if depth == depth and depth not in (float("inf"), float("-inf")):
                self.paper_ws_queue_depth.set(depth)

    @never_fails
    def record_paper_ws_error(self, event_type: str, reason: str, count: int = 1):
        """``count`` deliveries of one event type that did not reach their subscriber."""
        increment = int(count)
        if increment <= 0:
            return
        self.paper_ws_errors.inc(
            increment,
            event_type=safe_label_value(event_type),
            reason=safe_label_value(reason),
        )

    @never_fails
    def record_paper_ws_slow_consumer_disconnect(self, count: int = 1):
        """``count`` subscriptions closed for exceeding the pending-event bound (19.11)."""
        increment = int(count)
        if increment > 0:
            self.paper_ws_slow_consumer_disconnects.inc(increment)

    @never_fails
    def record_paper_signal_latency(self, symbol: str, duration_ms: Any):
        """How long one signal-generation step took, by market.

        Separate from :meth:`record_paper_signal_generated` because the two are counted at
        different rates: one strategy evaluation over one bar is ONE generation step and may
        produce zero, one or several signals. Multiplying the duration by the signal count would
        make a two-signal bar look twice as slow as it was.
        """
        if duration_ms is None:
            return
        self.paper_signal_latency_ms.observe(
            float(duration_ms), symbol=safe_label_value(symbol)
        )

    @never_fails
    def record_paper_signal_generated(self, symbol: str = "", count: int = 1):
        """``count`` signals a Paper_Session read off its strategy runtime, by market."""
        increment = int(count)
        if increment > 0:
            self.paper_signal_generated.inc(increment, symbol=safe_label_value(symbol))

    @never_fails
    def record_paper_signal_error(self, reason: str):
        """One strategy output the session could not act on, by refusal reason."""
        self.paper_signal_errors.inc(reason=safe_label_value(reason))

    @never_fails
    def record_paper_order_submitted(self, duration_ms: Any):
        """How long one order submission took, end to end."""
        if duration_ms is None:
            return
        self.paper_order_submit_latency_ms.observe(float(duration_ms))

    @never_fails
    def record_paper_fill_applied(self, duration_ms: Any):
        """How long applying one fill to the account took, end to end."""
        if duration_ms is None:
            return
        self.paper_fill_apply_latency_ms.observe(float(duration_ms))

    @never_fails
    def record_paper_order_rejected(self, reason: str):
        """One refused order intent, by the ``rejection_reason`` recorded on it."""
        self.paper_order_rejected.inc(reason=safe_label_value(reason))

    @never_fails
    def record_paper_order_retry(self, operation: str):
        """One retryable conflict on a paper order write path (Requirement 16.10)."""
        self.paper_order_retries.inc(operation=safe_label_value(operation))

    @never_fails
    def record_paper_order_concurrency_conflict(self, operation: str):
        """One write path that used up its bounded retries and gave up with a 409."""
        self.paper_order_concurrency_conflicts.inc(operation=safe_label_value(operation))

    @never_fails
    def record_db_statement(
        self, domain: str, operation: str, *, duration_ms: Any = None, failed: bool = False
    ):
        """ONE Persistence_Layer round trip, whether or not it completed.

        Counted for a failed statement too. A statement that was issued and did not complete is
        still a round trip - it cost the same trip - and excluding it would make
        ``round_trips`` disagree with what the database actually saw, which is the figure
        Requirements 27.1 and 27.2 are about.

        ``domain`` is ``'marketplace'`` or ``'paper'``; an unknown domain records nothing, for
        the reason :meth:`record_http_request` gives.
        """
        if domain == "marketplace":
            trips = self.marketplace_db_round_trips
            latency = self.marketplace_db_latency_ms
            errors = self.marketplace_db_errors
        elif domain == "paper":
            trips = self.paper_db_round_trips
            latency = self.paper_db_latency_ms
            errors = self.paper_db_errors
        else:
            return
        label = safe_label_value(operation)
        trips.inc(operation=label)
        if duration_ms is not None:
            latency.observe(float(duration_ms), operation=label)
        if failed:
            errors.inc(operation=label)

    @never_fails
    def record_expiry_sweep(
        self,
        *,
        duration_ms: Any = None,
        transitions: int = 0,
        failures: Any = (),
        succeeded_at: Any = None,
    ):
        """One expiry sweep pass: its duration, what it transitioned, and what failed.

        ``failures`` is an iterable of stage names, so one broken row is counted under the stage
        that broke rather than under a single undifferentiated total.

        ``succeeded_at`` is a UNIX timestamp and is written ONLY for a pass that completed with
        no failure - ``expiry_sweep.last_run_at``'s rule, restated here so the exported gauge
        and the process-local health input cannot disagree about what "last run" means.
        """
        if duration_ms is not None:
            self.marketplace_expiry_sweep_duration_ms.observe(float(duration_ms))
        written = int(transitions)
        if written > 0:
            self.marketplace_expiry_sweep_transitions.inc(written)
        for stage in failures or ():
            self.marketplace_expiry_sweep_errors.inc(stage=safe_label_value(stage))
        if succeeded_at is None:
            return
        instant = float(succeeded_at)
        if instant == instant and instant not in (float("inf"), float("-inf")):
            self.marketplace_expiry_sweep_last_run_at.set(instant)

    def record_trade_executed(self, latency_ms: float, symbol: str = "", side: str = ""):
        """Record a successful trade execution."""
        self.trades_executed_total.inc(symbol=symbol, side=side)
        self.execution_latency_ms.observe(latency_ms, symbol=symbol)
        logger.debug(f"Metrics: Trade executed | {symbol} {side} | {latency_ms}ms")
    
    def record_trade_blocked(self, reason: str, symbol: str = "", risk_score: float = 0):
        """Record a trade blocked by ExecutionGuard."""
        self.trades_blocked_total.inc(reason=reason, symbol=symbol)
        
        # Store for alerting
        with self._lock:
            self._recent_blocks.append({
                "timestamp": datetime.utcnow().isoformat(),
                "reason": reason,
                "symbol": symbol,
                "risk_score": risk_score
            })
        
        logger.warning(f"Metrics: Trade blocked | {symbol} | {reason}")
    
    def record_failed_order(self, reason: str, symbol: str = ""):
        """Record a failed order attempt."""
        self.failed_orders_total.inc(reason=reason, symbol=symbol)
        
        # Store for alerting
        with self._lock:
            self._recent_failures.append({
                "timestamp": datetime.utcnow().isoformat(),
                "reason": reason,
                "symbol": symbol
            })
        
        logger.warning(f"Metrics: Order failed | {symbol} | {reason}")
    
    def record_websocket_disconnect(self, exchange: str = ""):
        """Record a WebSocket disconnection."""
        self.websocket_disconnects_total.inc(exchange=exchange)
        logger.warning(f"Metrics: WebSocket disconnect | {exchange}")
    
    def record_websocket_connect(self, exchange: str = ""):
        """Record a WebSocket connection."""
        self.active_connections.inc(exchange=exchange)
    
    def record_websocket_close(self, exchange: str = ""):
        """Record a WebSocket connection close."""
        self.active_connections.dec(exchange=exchange)
    
    def record_risk_score(self, risk_score: float, tenant_id: str = ""):
        """Record a risk score from ExecutionGuard."""
        self.risk_score_histogram.observe(risk_score, tenant_id=tenant_id)
    
    # STEP 8: SCALING METRIC METHODS
    def set_active_users(self, count: int):
        """Set current active users count."""
        self.active_users.set(count)
        logger.debug(f"[Metrics] Active users: {count}")
    
    def set_ws_server_connections(self, count: int):
        """Set WebSocket server connections count."""
        self.ws_server_connections.set(count)
    
    def set_tasks_queue_size(self, queue_type: str, size: int):
        """Set tasks queue size."""
        self.tasks_queue_size.set(size, queue_type=queue_type)
    
    def set_execution_latency(self, executor: str, latency_seconds: float):
        """Set execution latency (avg over last minute)."""
        self.execution_latency_seconds.set(latency_seconds, executor=executor)
    
    def update_uptime(self):
        """Update system uptime gauge."""
        uptime = time.time() - self._start_time
        self.system_uptime_seconds.set(uptime)
    
    def get_recent_blocks(self, count: int = 10) -> List[Dict]:
        """Get recent blocked trades for alerting."""
        with self._lock:
            return list(self._recent_blocks)[-count:]
    
    def get_recent_failures(self, count: int = 10) -> List[Dict]:
        """Get recent failed orders for alerting."""
        with self._lock:
            return list(self._recent_failures)[-count:]
    
    def get_prometheus_metrics(self) -> str:
        """
        Export all metrics in Prometheus text format.
        
        Returns:
            Prometheus-formatted metrics string for /metrics endpoint
        """
        self.update_uptime()
        
        metrics = [
            self.trades_executed_total.to_prometheus(),
            self.trades_blocked_total.to_prometheus(),
            self.failed_orders_total.to_prometheus(),
            self.websocket_disconnects_total.to_prometheus(),
            self.execution_latency_ms.to_prometheus(),
            self.risk_score_histogram.to_prometheus(),
            self.active_connections.to_prometheus(),
            # STEP 8: Scaling metrics
            self.active_users.to_prometheus(),
            self.ws_server_connections.to_prometheus(),
            self.tasks_queue_size.to_prometheus(),
            self.execution_latency_seconds.to_prometheus(),
            self.system_uptime_seconds.to_prometheus(),
        ]
        # Strategy builder (task 9.1). Exported from the same list rather than from a
        # second endpoint, so /metrics stays the one place the platform is scraped.
        metrics.extend(
            metric.to_prometheus() for metric in self.strategy_builder_metrics()
        )
        # The Paper_Session market-data feed (marketplace-subscriptions-paper-trading task
        # 24.x). Same exposition, same endpoint; a second list only because the strategy-builder
        # one is asserted against its own requirement text.
        metrics.extend(metric.to_prometheus() for metric in self.paper_feed_metrics())
        # The rest of design.md's Requirement 26.6 table (task 33.5): the HTTP surfaces, the
        # WebSocket event types, signal generation, paper order execution, the database
        # operations and the expiry sweep. Same exposition, same endpoint, same collector.
        metrics.extend(
            metric.to_prometheus() for metric in self.marketplace_paper_metrics()
        )
        
        return "\n\n".join(metrics)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics snapshot."""
        return {
            "trades_executed": self.trades_executed_total.get(),
            "trades_blocked": self.trades_blocked_total.get(),
            "failed_orders": self.failed_orders_total.get(),
            "websocket_disconnects": self.websocket_disconnects_total.get(),
            "uptime_seconds": time.time() - self._start_time,
            "recent_blocks": len(self._recent_blocks),
            "recent_failures": len(self._recent_failures),
        }


# Global singleton instance
metrics_collector = MetricsCollector()


def get_metrics_collector() -> MetricsCollector:
    """Get the global metrics collector instance."""
    return metrics_collector


def guarded_collector() -> Optional[MetricsCollector]:
    """The process collector, or ``None`` when it is unavailable. **Never raises.**

    The one place the guarded-accessor idiom lives (marketplace-subscriptions-paper-trading
    Requirement 30.2). Before this existed, every module that wanted an instrumentation call it
    could not be broken by wrote its own two-statement ``_metrics()`` - a lazy
    ``from backend_app.backend.metrics import metrics_collector`` inside a
    ``try``/``except Exception: return None``. Five of those copies were byte-identical, which is
    the duplication 30.2 is about: the collector is one object, so reaching it is one
    responsibility and belongs in one function.

    Two properties, both of them the reason callers use this instead of touching
    :data:`metrics_collector` directly:

    * **Late-bound.** The name is read out of this module's globals on every call, so a test that
      replaces ``metrics.metrics_collector`` with a fresh :class:`MetricsCollector` is seen by
      every caller immediately - which is what ``tests/test_paper_market_feed_events.py`` and
      ``tests/test_paper_market_feed_selection.py`` rely on. A caller that bound the collector
      object at import time would hold whichever one existed then.
    * **Guarded.** Instrumentation must never be what breaks the path it measures: not a market
      event dropped, not a bar unevaluated, not a fill lost, not a statement unissued, not a
      subscriber unwritten-to. So the defined outcome of *any* failure to produce the collector is
      ``None``, and every caller is written to carry on without one.

    The ``except`` is broad on purpose, and its outcome is defined rather than silent: ``None``, in
    every case, which is the only extra value a caller has to handle. It is broad because the ways
    the name can fail to resolve are not worth enumerating - a test that deleted the module
    attribute raises ``NameError`` here, an import-time failure that left the module half-built
    would raise something else - and the one disposition that would be wrong is letting any of them
    reach the caller.

    What this does NOT promise, stated so no caller assumes it: the *returned* collector's own
    methods are not wrapped. ``guarded_collector()`` never raises; ``guarded_collector().record_x()``
    raises whatever that collector raises, exactly as it did when each caller inlined this. The
    recording calls in this repository are counter increments on a lock, and a caller that needs
    more than that guarantee has to say so at its own call site.
    """
    try:
        return metrics_collector
    except Exception:  # noqa: BLE001 - defined outcome: None. See the docstring.
        return None


# Convenience functions for easy import
record_trade_executed = metrics_collector.record_trade_executed
record_trade_blocked = metrics_collector.record_trade_blocked
record_failed_order = metrics_collector.record_failed_order
record_websocket_disconnect = metrics_collector.record_websocket_disconnect
record_websocket_connect = metrics_collector.record_websocket_connect
record_websocket_close = metrics_collector.record_websocket_close
record_risk_score = metrics_collector.record_risk_score
get_prometheus_metrics = metrics_collector.get_prometheus_metrics

# Strategy builder (task 9.1). Bound to the same singleton, so a call site imports one
# name instead of reaching for a collector - and there is still only one registry.
record_builder_validation = metrics_collector.record_builder_validation
record_builder_compile = metrics_collector.record_builder_compile
record_builder_compile_failure = metrics_collector.record_builder_compile_failure
record_builder_registry_request = metrics_collector.record_builder_registry_request
set_asset_universe_age = metrics_collector.set_asset_universe_age
record_training_job_status = metrics_collector.record_training_job_status
record_training_cap_rejection = metrics_collector.record_training_cap_rejection
record_training_blocked_insufficient_data = (
    metrics_collector.record_training_blocked_insufficient_data
)
record_dag_node_execution = metrics_collector.record_dag_node_execution
record_dag_not_ready = metrics_collector.record_dag_not_ready
record_dag_intent_blocked_non_finite = (
    metrics_collector.record_dag_intent_blocked_non_finite
)
record_deployment_state_transition = metrics_collector.record_deployment_state_transition
record_market_data_latency = metrics_collector.record_market_data_latency
set_market_data_quality_score = metrics_collector.set_market_data_quality_score
record_feed_state = metrics_collector.record_feed_state

# The Paper_Session market-data feed (marketplace-subscriptions-paper-trading task 24.x).
# NOTE for call sites: the paper package reaches the collector through :func:`guarded_collector`,
# not through these bound names, precisely so a test can replace the module attribute with a fresh
# collector the way ``tests/test_task_9_1_builder_metrics.py`` does. A name bound here at import
# time is bound to whichever collector existed then and cannot be swapped.
record_paper_feed_latency = metrics_collector.record_paper_feed_latency
record_paper_feed_event = metrics_collector.record_paper_feed_event
record_paper_feed_invalid = metrics_collector.record_paper_feed_invalid
record_paper_feed_duplicate = metrics_collector.record_paper_feed_duplicate
record_paper_feed_out_of_order = metrics_collector.record_paper_feed_out_of_order
record_paper_feed_state = metrics_collector.record_paper_feed_state
record_paper_feed_reconnect = metrics_collector.record_paper_feed_reconnect
