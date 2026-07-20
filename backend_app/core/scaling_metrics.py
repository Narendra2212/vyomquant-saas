"""
core/scaling_metrics.py — SCALING METRICS FOR SCALE MONITORING

STEP 8: METRICS FOR SCALE MONITORING

GOAL: Track load for 500 users (≈150 active)

METRICS:
  - active_users: Currently logged-in users with sessions
  - ws_connections: WebSocket server connection count
  - tasks_queue_size: Pending tasks in queue (dag, execution, portfolio)
  - execution_latency: Average execution latency per worker type

FEATURES:
  - Prometheus-compatible format
  - Real-time updates via background collector
  - Alert thresholds for scaling decisions
  - Historical data for trend analysis

USAGE:
    from backend_app.core.scaling_metrics import scaling_metrics
    
    # Update metrics
    scaling_metrics.set_active_users(147)
    scaling_metrics.set_ws_connections(142)
    scaling_metrics.set_queue_size("dag", 23)
    scaling_metrics.set_execution_latency("order", 0.45)
    
    # Get Prometheus format
    print(scaling_metrics.get_prometheus_metrics())
"""

import time
import logging
import threading
from typing import Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime
from collections import deque

logger = logging.getLogger("ScalingMetrics")


@dataclass
class MetricPoint:
    """Single metric data point with timestamp."""
    value: float
    timestamp: float = field(default_factory=time.time)


class ScalingMetricsCollector:
    """
    STEP 8: Metrics collector for scaling monitoring.
    
    Tracks real-time system load for scaling decisions:
    - Active users (gauge)
    - WebSocket connections (gauge)
    - Task queue sizes (gauge per type)
    - Execution latency (gauge per executor)
    
    Also maintains history for trend analysis.
    """
    
    def __init__(self, history_minutes: int = 60):
        """
        Initialize the scaling metrics collector.
        
        Args:
            history_minutes: Minutes of history to keep per metric
        """
        self._lock = threading.Lock()
        
        # Current values (gauges)
        self._active_users: int = 0
        self._ws_connections: int = 0
        self._queue_sizes: Dict[str, int] = {
            "dag": 0,
            "execution": 0,
            "portfolio": 0,
        }
        self._execution_latencies: Dict[str, float] = {
            "dag": 0.0,
            "order": 0.0,
            "position": 0.0,
        }
        
        # History for trend analysis (circular buffers)
        max_points = history_minutes * 60  # 1 point per second
        self._active_users_history: deque = deque(maxlen=max_points)
        self._ws_connections_history: deque = deque(maxlen=max_points)
        self._queue_sizes_history: Dict[str, deque] = {
            k: deque(maxlen=max_points) for k in self._queue_sizes.keys()
        }
        self._latency_history: Dict[str, deque] = {
            k: deque(maxlen=max_points) for k in self._execution_latencies.keys()
        }
        
        # Alert thresholds
        self.thresholds = {
            "active_users_warning": 140,
            "active_users_critical": 180,
            "ws_connections_warning": 140,
            "ws_connections_critical": 180,
            "queue_size_warning": 50,
            "queue_size_critical": 100,
            "latency_warning_seconds": 1.0,
            "latency_critical_seconds": 5.0,
        }
        
        self._last_update = time.time()
        logger.info("[ScalingMetrics] Initialized")
    
    # ═══════════════════════════════════════════════════════════════════════
    # METRIC SETTERS
    # ═══════════════════════════════════════════════════════════════════════
    
    def set_active_users(self, count: int):
        """Set current active users count."""
        with self._lock:
            self._active_users = count
            self._active_users_history.append(MetricPoint(count))
            self._last_update = time.time()
        
        logger.debug(f"[ScalingMetrics] Active users: {count}")
        self._check_threshold("active_users", count)
    
    def set_ws_connections(self, count: int):
        """Set WebSocket server connections count."""
        with self._lock:
            self._ws_connections = count
            self._ws_connections_history.append(MetricPoint(count))
            self._last_update = time.time()
        
        logger.debug(f"[ScalingMetrics] WS connections: {count}")
        self._check_threshold("ws_connections", count)
    
    def set_queue_size(self, queue_type: str, size: int):
        """Set task queue size."""
        if queue_type not in self._queue_sizes:
            logger.warning(f"[ScalingMetrics] Unknown queue type: {queue_type}")
            return
        
        with self._lock:
            self._queue_sizes[queue_type] = size
            self._queue_sizes_history[queue_type].append(MetricPoint(size))
            self._last_update = time.time()
        
        logger.debug(f"[ScalingMetrics] Queue {queue_type}: {size}")
        self._check_threshold(f"queue_{queue_type}", size)
    
    def set_execution_latency(self, executor: str, latency_seconds: float):
        """Set execution latency (avg over last minute)."""
        if executor not in self._execution_latencies:
            logger.warning(f"[ScalingMetrics] Unknown executor: {executor}")
            return
        
        with self._lock:
            self._execution_latencies[executor] = latency_seconds
            self._latency_history[executor].append(MetricPoint(latency_seconds))
            self._last_update = time.time()
        
        logger.debug(f"[ScalingMetrics] Latency {executor}: {latency_seconds:.3f}s")
        self._check_threshold(f"latency_{executor}", latency_seconds)
    
    # ═══════════════════════════════════════════════════════════════════════
    # THRESHOLD CHECKING
    # ═══════════════════════════════════════════════════════════════════════
    
    def _check_threshold(self, metric: str, value: float):
        """Check if metric exceeds alert thresholds."""
        if "active_users" in metric:
            if value >= self.thresholds["active_users_critical"]:
                logger.critical(f"[ScalingAlert] Active users critical: {value} >= {self.thresholds['active_users_critical']}")
            elif value >= self.thresholds["active_users_warning"]:
                logger.warning(f"[ScalingAlert] Active users warning: {value} >= {self.thresholds['active_users_warning']}")
        
        elif "ws_connections" in metric:
            if value >= self.thresholds["ws_connections_critical"]:
                logger.critical(f"[ScalingAlert] WS connections critical: {value} >= {self.thresholds['ws_connections_critical']}")
            elif value >= self.thresholds["ws_connections_warning"]:
                logger.warning(f"[ScalingAlert] WS connections warning: {value} >= {self.thresholds['ws_connections_warning']}")
        
        elif "queue_" in metric:
            if value >= self.thresholds["queue_size_critical"]:
                logger.critical(f"[ScalingAlert] Queue size critical: {value} >= {self.thresholds['queue_size_critical']}")
            elif value >= self.thresholds["queue_size_warning"]:
                logger.warning(f"[ScalingAlert] Queue size warning: {value} >= {self.thresholds['queue_size_warning']}")
        
        elif "latency_" in metric:
            if value >= self.thresholds["latency_critical_seconds"]:
                logger.critical(f"[ScalingAlert] Latency critical: {value:.2f}s >= {self.thresholds['latency_critical_seconds']}s")
            elif value >= self.thresholds["latency_warning_seconds"]:
                logger.warning(f"[ScalingAlert] Latency warning: {value:.2f}s >= {self.thresholds['latency_warning_seconds']}s")
    
    # ═══════════════════════════════════════════════════════════════════════
    # GETTERS
    # ═══════════════════════════════════════════════════════════════════════
    
    def get_active_users(self) -> int:
        """Get current active users."""
        with self._lock:
            return self._active_users
    
    def get_ws_connections(self) -> int:
        """Get current WebSocket connections."""
        with self._lock:
            return self._ws_connections
    
    def get_queue_size(self, queue_type: str) -> int:
        """Get current queue size."""
        with self._lock:
            return self._queue_sizes.get(queue_type, 0)
    
    def get_execution_latency(self, executor: str) -> float:
        """Get current execution latency."""
        with self._lock:
            return self._execution_latencies.get(executor, 0.0)
    
    # ═══════════════════════════════════════════════════════════════════════
    # HISTORY & TRENDS
    # ═══════════════════════════════════════════════════════════════════════
    
    def get_average_over_period(self, metric: str, seconds: int = 60) -> Optional[float]:
        """Get average value over last N seconds."""
        with self._lock:
            cutoff = time.time() - seconds
            
            if metric == "active_users":
                points = [p.value for p in self._active_users_history if p.timestamp > cutoff]
            elif metric == "ws_connections":
                points = [p.value for p in self._ws_connections_history if p.timestamp > cutoff]
            elif metric.startswith("queue_"):
                queue_type = metric.replace("queue_", "")
                points = [p.value for p in self._queue_sizes_history.get(queue_type, []) if p.timestamp > cutoff]
            elif metric.startswith("latency_"):
                executor = metric.replace("latency_", "")
                points = [p.value for p in self._latency_history.get(executor, []) if p.timestamp > cutoff]
            else:
                return None
            
            if not points:
                return None
            
            return sum(points) / len(points)
    
    def get_trend(self, metric: str, seconds: int = 300) -> str:
        """Get trend direction (increasing, decreasing, stable)."""
        avg_current = self.get_average_over_period(metric, seconds=60)
        avg_previous = self.get_average_over_period(metric, seconds=seconds)
        
        if avg_current is None or avg_previous is None:
            return "unknown"
        
        diff = avg_current - avg_previous
        threshold = avg_previous * 0.1  # 10% change threshold
        
        if diff > threshold:
            return "increasing"
        elif diff < -threshold:
            return "decreasing"
        else:
            return "stable"
    
    # ═══════════════════════════════════════════════════════════════════════
    # PROMETHEUS EXPORT
    # ═══════════════════════════════════════════════════════════════════════
    
    def get_prometheus_metrics(self) -> str:
        """Export metrics in Prometheus format."""
        with self._lock:
            lines = []
            
            # Active users
            lines.append("# HELP active_users_total Current number of active users")
            lines.append("# TYPE active_users_total gauge")
            lines.append(f"active_users_total {self._active_users}")
            
            # WebSocket connections
            lines.append("# HELP ws_server_connections_total Current WebSocket server connections")
            lines.append("# TYPE ws_server_connections_total gauge")
            lines.append(f"ws_server_connections_total {self._ws_connections}")
            
            # Task queue sizes
            lines.append("# HELP tasks_queue_size Current number of tasks in queue")
            lines.append("# TYPE tasks_queue_size gauge")
            for queue_type, size in self._queue_sizes.items():
                lines.append(f'tasks_queue_size{{queue_type="{queue_type}"}} {size}')
            
            # Execution latencies
            lines.append("# HELP execution_latency_seconds Average execution latency")
            lines.append("# TYPE execution_latency_seconds gauge")
            for executor, latency in self._execution_latencies.items():
                lines.append(f'execution_latency_seconds{{executor="{executor}"}} {latency:.6f}')
            
            return "\n".join(lines)
    
    # ═══════════════════════════════════════════════════════════════════════
    # SNAPSHOT
    # ═══════════════════════════════════════════════════════════════════════
    
    def get_snapshot(self) -> Dict:
        """Get current metrics snapshot."""
        with self._lock:
            return {
                "timestamp": datetime.utcnow().isoformat(),
                "active_users": self._active_users,
                "ws_connections": self._ws_connections,
                "queue_sizes": self._queue_sizes.copy(),
                "execution_latencies": {
                    k: round(v, 3) for k, v in self._execution_latencies.items()
                },
                "thresholds": self.thresholds,
            }
    
    def get_scaling_recommendation(self) -> Dict:
        """Get scaling recommendation based on current load."""
        snapshot = self.get_snapshot()
        recommendations = []
        
        # Check active users
        if snapshot["active_users"] >= self.thresholds["active_users_critical"]:
            recommendations.append({
                "metric": "active_users",
                "severity": "critical",
                "message": "Scale up: Add more backend instances",
                "current": snapshot["active_users"],
                "threshold": self.thresholds["active_users_critical"]
            })
        
        # Check WebSocket connections
        if snapshot["ws_connections"] >= self.thresholds["ws_connections_critical"]:
            recommendations.append({
                "metric": "ws_connections",
                "severity": "critical",
                "message": "Scale up: Add WebSocket server instances",
                "current": snapshot["ws_connections"],
                "threshold": self.thresholds["ws_connections_critical"]
            })
        
        # Check queue sizes
        for queue_type, size in snapshot["queue_sizes"].items():
            if size >= self.thresholds["queue_size_critical"]:
                recommendations.append({
                    "metric": f"queue_{queue_type}",
                    "severity": "critical",
                    "message": f"Scale up: Add {queue_type} workers",
                    "current": size,
                    "threshold": self.thresholds["queue_size_critical"]
                })
        
        # Check latencies
        for executor, latency in snapshot["execution_latencies"].items():
            if latency >= self.thresholds["latency_critical_seconds"]:
                recommendations.append({
                    "metric": f"latency_{executor}",
                    "severity": "critical",
                    "message": f"Scale up: {executor} executor overloaded",
                    "current": latency,
                    "threshold": self.thresholds["latency_critical_seconds"]
                })
        
        return {
            "timestamp": snapshot["timestamp"],
            "needs_scaling": len(recommendations) > 0,
            "recommendations": recommendations,
            "snapshot": snapshot,
        }


# Global singleton
scaling_metrics = ScalingMetricsCollector()


def get_scaling_metrics() -> ScalingMetricsCollector:
    """Get the global scaling metrics instance."""
    return scaling_metrics
