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

import time
import threading
from typing import Dict, List, Optional, Any
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger("Metrics")


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
        self.description = description
        self.label_names = labels or []
        self.values: Dict[tuple, float] = {}
        self._lock = threading.Lock()
    
    def inc(self, value: float = 1, **label_values):
        """Increment counter."""
        key = tuple(label_values.get(l, "") for l in self.label_names)
        with self._lock:
            self.values[key] = self.values.get(key, 0) + value
    
    def get(self, **label_values) -> float:
        """Get current value."""
        key = tuple(label_values.get(l, "") for l in self.label_names)
        with self._lock:
            return self.values.get(key, 0)
    
    def to_prometheus(self) -> str:
        """Export to Prometheus format."""
        lines = [f"# HELP {self.name} {self.description}"]
        lines.append(f"# TYPE {self.name} counter")
        
        with self._lock:
            for key, value in self.values.items():
                label_str = ",".join(
                    f'{name}="{val}"' 
                    for name, val in zip(self.label_names, key) if val
                )
                if label_str:
                    lines.append(f'{self.name}{{{label_str}}} {value}')
                else:
                    lines.append(f'{self.name} {value}')
        
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
        self.description = description
        self.buckets = buckets
        self.label_names = labels or []
        self.bucket_counts: Dict[tuple, Dict[float, float]] = {}
        self.sum_values: Dict[tuple, float] = {}
        self.count_values: Dict[tuple, float] = {}
        self._lock = threading.Lock()
    
    def observe(self, value: float, **label_values):
        """Observe a value."""
        key = tuple(label_values.get(l, "") for l in self.label_names)
        
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
        """Export to Prometheus format."""
        lines = [f"# HELP {self.name} {self.description}"]
        lines.append(f"# TYPE {self.name} histogram")
        
        with self._lock:
            for key in self.bucket_counts:
                label_str = ",".join(
                    f'{name}="{val}"' 
                    for name, val in zip(self.label_names, key) if val
                )
                label_prefix = f"{{{label_str}}}" if label_str else ""
                
                # Bucket values
                for bucket in self.buckets:
                    bucket_label = f'le="{bucket}"'
                    if label_str:
                        full_label = f"{label_str},{bucket_label}"
                    else:
                        full_label = bucket_label
                    count = self.bucket_counts[key].get(bucket, 0)
                    lines.append(f'{self.name}_bucket{{{full_label}}} {count}')
                
                # Sum and count
                lines.append(f'{self.name}_sum{label_prefix} {self.sum_values.get(key, 0)}')
                lines.append(f'{self.name}_count{label_prefix} {self.count_values.get(key, 0)}')
        
        return "\n".join(lines)


class Gauge:
    """Prometheus-style gauge metric."""
    
    def __init__(self, name: str, description: str, labels: Optional[List[str]] = None):
        self.name = name
        self.description = description
        self.label_names = labels or []
        self.values: Dict[tuple, float] = {}
        self._lock = threading.Lock()
    
    def set(self, value: float, **label_values):
        """Set gauge value."""
        key = tuple(label_values.get(l, "") for l in self.label_names)
        with self._lock:
            self.values[key] = value
    
    def inc(self, value: float = 1, **label_values):
        """Increment gauge."""
        key = tuple(label_values.get(l, "") for l in self.label_names)
        with self._lock:
            self.values[key] = self.values.get(key, 0) + value
    
    def dec(self, value: float = 1, **label_values):
        """Decrement gauge."""
        key = tuple(label_values.get(l, "") for l in self.label_names)
        with self._lock:
            self.values[key] = self.values.get(key, 0) - value
    
    def to_prometheus(self) -> str:
        """Export to Prometheus format."""
        lines = [f"# HELP {self.name} {self.description}"]
        lines.append(f"# TYPE {self.name} gauge")
        
        with self._lock:
            for key, value in self.values.items():
                label_str = ",".join(
                    f'{name}="{val}"' 
                    for name, val in zip(self.label_names, key) if val
                )
                if label_str:
                    lines.append(f'{self.name}{{{label_str}}} {value}')
                else:
                    lines.append(f'{self.name} {value}')
        
        return "\n".join(lines)


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
        
        # Track start time for uptime
        self._start_time = time.time()
        
        # Recent events buffer for alerting
        self._recent_blocks: deque = deque(maxlen=100)
        self._recent_failures: deque = deque(maxlen=100)
        self._lock = threading.Lock()
    
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
