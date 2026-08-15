# -*- coding: utf-8 -*-
"""
core/metrics.py - Application metrics and monitoring utilities.

Provides:
- Prometheus Request/response metrics
- Performance tracking
- Health check utilities
- Redis and Postgres connection tracking
"""

import logging
from typing import Any, Dict

from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger("Metrics")


# ═══════════════════════════════════════════════════════════════════════════════
# PROMETHEUS METRICS DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

# HTTP Metrics
HTTP_REQUESTS_TOTAL = Counter(
    "aerora_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"]
)

HTTP_REQUEST_DURATION = Histogram(
    "aerora_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"]
)

# Execution Metrics
EXECUTION_ATTEMPTS_TOTAL = Counter(
    "aerora_execution_attempts_total",
    "Total execution attempts",
    ["tenant_id", "status"]
)

EXECUTION_SUCCESS_TOTAL = Counter(
    "aerora_execution_success_total",
    "Total successful executions",
    ["tenant_id"]
)

DUPLICATE_EXECUTION_SKIPPED_TOTAL = Counter(
    "aerora_duplicate_execution_skipped_total",
    "Total prevented duplicate executions",
    ["tenant_id"]
)

# Infrastructure Health Gauges
REDIS_CONNECTED = Gauge(
    "aerora_redis_connected",
    "1 if Redis is connected, 0 otherwise"
)

POSTGRES_CONNECTED = Gauge(
    "aerora_postgres_connected",
    "1 if Postgres is connected, 0 otherwise"
)

SYSTEM_HEALTH = Gauge(
    "aerora_system_health",
    "1 if system is healthy, 0 otherwise"
)

# ═══════════════════════════════════════════════════════════════════════════════
# METRICS COLLECTOR (Backward Compatibility)
# ═══════════════════════════════════════════════════════════════════════════════

class MetricsCollector:
    """Wrapper to maintain backward compatibility with in-memory stats."""
    
    def record_request(self, endpoint: str, method: str, duration_ms: float, status_code: int):
        """Record a request metric"""
        HTTP_REQUESTS_TOTAL.labels(method=method, endpoint=endpoint, status_code=status_code).inc()
        HTTP_REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration_ms / 1000.0)
    
    def increment(self, counter_name: str, value: int = 1):
        """Increment a generic counter (not exposed to prometheus unless mapped)"""
        pass
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current metrics stats (mocked)"""
        return {
            "total_requests": 0,
            "avg_duration_ms": 0,
            "error_rate": 0,
            "counters": {}
        }


metrics = MetricsCollector()


class ExecutionMetrics:
    """Prometheus-style metrics for execution tracking."""
    
    def record_attempt(self, tenant_id: str, status: str = "pending"):
        EXECUTION_ATTEMPTS_TOTAL.labels(tenant_id=tenant_id, status=status).inc()
    
    def record_success(self, tenant_id: str):
        EXECUTION_SUCCESS_TOTAL.labels(tenant_id=tenant_id).inc()
    
    def record_duplicate_skipped(self, tenant_id: str, execution_id: str):
        DUPLICATE_EXECUTION_SKIPPED_TOTAL.labels(tenant_id=tenant_id).inc()
        logger.warning(
            f"DUPLICATE_EXECUTION_PREVENTED: execution_id={execution_id} tenant={tenant_id}",
            extra={
                "event": "DUPLICATE_EXECUTION_PREVENTED",
                "execution_id": execution_id,
                "tenant_id": tenant_id,
            }
        )
    
    def get_metrics(self) -> Dict[str, Any]:
        """Mock output for backward compatibility."""
        return {}


execution_metrics = ExecutionMetrics()


def get_system_snapshot() -> Dict[str, Any]:
    """Returns a snapshot of system health and metrics."""
    return {
        "status": "ok",
        "uptime": "dev",
        "dev_mode": True,
    }
