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

    def strategy_builder_metrics(self) -> List[Any]:
        """The metric objects for :data:`STRATEGY_BUILDER_METRIC_ATTRIBUTES`."""
        return [
            getattr(self, attribute)
            for attribute in self.STRATEGY_BUILDER_METRIC_ATTRIBUTES
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
