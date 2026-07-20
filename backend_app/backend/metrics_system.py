"""
Metrics and Monitoring System

STEP 7.7 — PRODUCTION HARDENING

Tracks:
- Order success rate
- Latency
- PnL drift
- Error rate

Metrics:
┌─────────────────────────────────────────────────────────────────┐
│  Order Metrics                                                   │
│  ├─ success_rate: Successful orders / Total orders               │
│  ├─ avg_latency: Average order execution time                    │
│  ├─ error_rate: Failed orders / Total orders                     │
│  └─ pending_stuck: Orders pending > threshold                  │
├─────────────────────────────────────────────────────────────────┤
│  PnL Metrics                                                     │
│  ├─ total_pnl: Current total PnL                                  │
│  ├─ unrealized_pnl: Open position PnL                           │
│  ├─ realized_pnl: Closed trade PnL                              │
│  └─ drift: Difference from expected PnL                         │
├─────────────────────────────────────────────────────────────────┤
│  System Metrics                                                  │
│  ├─ error_rate: Errors per minute                               │
│  ├─ circuit_breaker_states: Open/Closed circuits               │
│  ├─ latency: API response times                                  │
│  └─ uptime: System availability                                  │
└─────────────────────────────────────────────────────────────────┘
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any
from collections import deque

from sqlalchemy.orm import Session
from sqlalchemy import func

from backend_app.core.models.execution_record import ExecutionRecordModel, ExecutionStatus
from backend_app.core.position_model import PositionModel

logger = logging.getLogger(__name__)


@dataclass
class OrderMetrics:
    """Order execution metrics."""
    total_orders: int = 0
    successful_orders: int = 0
    failed_orders: int = 0
    pending_orders: int = 0
    stuck_orders: int = 0
    
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    
    @property
    def success_rate(self) -> float:
        if self.total_orders == 0:
            return 0.0
        return (self.successful_orders / self.total_orders) * 100
    
    @property
    def error_rate(self) -> float:
        if self.total_orders == 0:
            return 0.0
        return (self.failed_orders / self.total_orders) * 100


@dataclass
class PnLMetrics:
    """PnL tracking metrics."""
    total_pnl: Decimal = Decimal('0')
    unrealized_pnl: Decimal = Decimal('0')
    realized_pnl: Decimal = Decimal('0')
    
    expected_pnl: Decimal = Decimal('0')  # From position calculations
    drift: Decimal = Decimal('0')
    
    @property
    def drift_pct(self) -> float:
        if self.expected_pnl == 0:
            return 0.0
        return float((self.drift / self.expected_pnl) * 100)


@dataclass
class SystemMetrics:
    """System health metrics."""
    errors_last_minute: int = 0
    errors_last_hour: int = 0
    
    circuit_breaker_states: Dict[str, str] = field(default_factory=dict)
    
    avg_api_latency_ms: float = 0.0
    max_api_latency_ms: float = 0.0
    
    uptime_seconds: float = 0.0
    start_time: datetime = field(default_factory=datetime.utcnow)


class MetricsSystem:
    """
    STEP 7.7: Production metrics and monitoring.
    
    Collects metrics for observability and alerting.
    
    Usage:
        metrics = MetricsSystem(db_session)
        
        # Collect metrics
        order_metrics = await metrics.collect_order_metrics(tenant_id)
        pnl_metrics = await metrics.collect_pnl_metrics(tenant_id)
        
        # Check for issues
        if order_metrics.error_rate > 10:
            await alert_system.send_warning("High order error rate")
    """
    
    def __init__(self, db_session: Session):
        self.db = db_session
        
        # Latency tracking
        self._order_latencies: deque = deque(maxlen=1000)
        self._api_latencies: deque = deque(maxlen=1000)
        
        # Error tracking
        self._errors: deque = deque(maxlen=10000)
        
        # Start time
        self.start_time = datetime.utcnow()
    
    async def collect_order_metrics(
        self,
        tenant_id: str,
        window_minutes: int = 60
    ) -> OrderMetrics:
        """
        Collect order execution metrics.
        
        Args:
            tenant_id: Tenant UUID
            window_minutes: Time window for metrics
            
        Returns:
            OrderMetrics with current statistics
        """
        since = datetime.utcnow() - timedelta(minutes=window_minutes)
        
        # Query counts
        total = self.db.query(ExecutionRecordModel).filter(
            ExecutionRecordModel.tenant_id == tenant_id,
            ExecutionRecordModel.created_at >= since
        ).count()
        
        successful = self.db.query(ExecutionRecordModel).filter(
            ExecutionRecordModel.tenant_id == tenant_id,
            ExecutionRecordModel.status == ExecutionStatus.COMPLETED,
            ExecutionRecordModel.created_at >= since
        ).count()
        
        failed = self.db.query(ExecutionRecordModel).filter(
            ExecutionRecordModel.tenant_id == tenant_id,
            ExecutionRecordModel.status == ExecutionStatus.FAILED,
            ExecutionRecordModel.created_at >= since
        ).count()
        
        pending = self.db.query(ExecutionRecordModel).filter(
            ExecutionRecordModel.tenant_id == tenant_id,
            ExecutionRecordModel.status.in_([
                ExecutionStatus.PENDING,
                ExecutionStatus.EXECUTING
            ])
        ).count()
        
        # Count stuck orders (> 5 minutes pending)
        stuck_threshold = datetime.utcnow() - timedelta(minutes=5)
        stuck = self.db.query(ExecutionRecordModel).filter(
            ExecutionRecordModel.tenant_id == tenant_id,
            ExecutionRecordModel.status.in_([
                ExecutionStatus.PENDING,
                ExecutionStatus.EXECUTING
            ]),
            ExecutionRecordModel.created_at < stuck_threshold
        ).count()
        
        # Calculate latency statistics
        latencies = list(self._order_latencies)
        
        metrics = OrderMetrics(
            total_orders=total,
            successful_orders=successful,
            failed_orders=failed,
            pending_orders=pending,
            stuck_orders=stuck,
            avg_latency_ms=self._calculate_avg(latencies),
            p95_latency_ms=self._calculate_percentile(latencies, 95),
            p99_latency_ms=self._calculate_percentile(latencies, 99)
        )
        
        return metrics
    
    async def collect_pnl_metrics(self, tenant_id: str) -> PnLMetrics:
        """
        Collect PnL metrics and detect drift.
        
        Returns:
            PnLMetrics with drift calculation
        """
        # Get current PnL from positions
        positions = self.db.query(PositionModel).filter(
            PositionModel.tenant_id == tenant_id
        ).all()
        
        total_unrealized = sum(
            Decimal(p.unrealized_pnl) for p in positions
        )
        total_realized = sum(
            Decimal(p.realized_pnl) for p in positions
        )
        total_pnl = total_unrealized + total_realized
        
        # Calculate expected PnL from fills
        # In production, this would cross-reference with exchange data
        expected_pnl = total_pnl  # Simplified - real impl would calculate independently
        
        # Detect drift
        drift = total_pnl - expected_pnl
        
        metrics = PnLMetrics(
            total_pnl=total_pnl,
            unrealized_pnl=total_unrealized,
            realized_pnl=total_realized,
            expected_pnl=expected_pnl,
            drift=drift
        )
        
        # Alert on significant drift
        if abs(metrics.drift_pct) > 1.0:  # > 1% drift
            logger.error(
                f"SIGNIFICANT PnL DRIFT DETECTED: {metrics.drift_pct:.2f}% | "
                f"expected={expected_pnl}, actual={total_pnl}"
            )
        
        return metrics
    
    async def collect_system_metrics(self) -> SystemMetrics:
        """Collect system health metrics."""
        now = datetime.utcnow()
        
        # Error counts
        one_minute_ago = now - timedelta(minutes=1)
        one_hour_ago = now - timedelta(hours=1)
        
        errors_last_minute = sum(
            1 for t in self._errors if t > one_minute_ago
        )
        errors_last_hour = sum(
            1 for t in self._errors if t > one_hour_ago
        )
        
        # API latency
        api_latencies = list(self._api_latencies)
        
        # Uptime
        uptime = (now - self.start_time).total_seconds()
        
        # Circuit breaker states
        try:
            from backend_app.backend.circuit_breaker import get_circuit_breaker_manager
            cb_manager = get_circuit_breaker_manager()
            cb_states = {
                name: metrics["state"]
                for name, metrics in cb_manager.get_all_metrics().items()
            }
        except:
            cb_states = {}
        
        metrics = SystemMetrics(
            errors_last_minute=errors_last_minute,
            errors_last_hour=errors_last_hour,
            circuit_breaker_states=cb_states,
            avg_api_latency_ms=self._calculate_avg(api_latencies),
            max_api_latency_ms=max(api_latencies) if api_latencies else 0,
            uptime_seconds=uptime,
            start_time=self.start_time
        )
        
        return metrics
    
    def record_order_latency(self, latency_ms: float):
        """Record order execution latency."""
        self._order_latencies.append(latency_ms)
    
    def record_api_latency(self, latency_ms: float):
        """Record API response latency."""
        self._api_latencies.append(latency_ms)
    
    def record_error(self):
        """Record an error occurrence."""
        self._errors.append(datetime.utcnow())
    
    def _calculate_avg(self, values: List[float]) -> float:
        """Calculate average of values."""
        if not values:
            return 0.0
        return sum(values) / len(values)
    
    def _calculate_percentile(self, values: List[float], percentile: int) -> float:
        """Calculate percentile value."""
        if not values:
            return 0.0
        
        sorted_values = sorted(values)
        index = int(len(sorted_values) * (percentile / 100))
        return sorted_values[min(index, len(sorted_values) - 1)]
    
    async def get_full_metrics_report(
        self,
        tenant_id: str
    ) -> Dict[str, Any]:
        """Get complete metrics report for tenant."""
        order_metrics = await self.collect_order_metrics(tenant_id)
        pnl_metrics = await self.collect_pnl_metrics(tenant_id)
        system_metrics = await self.collect_system_metrics()
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "tenant_id": tenant_id,
            "orders": {
                "total": order_metrics.total_orders,
                "successful": order_metrics.successful_orders,
                "failed": order_metrics.failed_orders,
                "pending": order_metrics.pending_orders,
                "stuck": order_metrics.stuck_orders,
                "success_rate_pct": order_metrics.success_rate,
                "error_rate_pct": order_metrics.error_rate,
                "latency_ms": {
                    "avg": order_metrics.avg_latency_ms,
                    "p95": order_metrics.p95_latency_ms,
                    "p99": order_metrics.p99_latency_ms
                }
            },
            "pnl": {
                "total": str(pnl_metrics.total_pnl),
                "unrealized": str(pnl_metrics.unrealized_pnl),
                "realized": str(pnl_metrics.realized_pnl),
                "expected": str(pnl_metrics.expected_pnl),
                "drift": str(pnl_metrics.drift),
                "drift_pct": pnl_metrics.drift_pct
            },
            "system": {
                "errors_last_minute": system_metrics.errors_last_minute,
                "errors_last_hour": system_metrics.errors_last_hour,
                "circuit_breakers": system_metrics.circuit_breaker_states,
                "api_latency_ms": {
                    "avg": system_metrics.avg_api_latency_ms,
                    "max": system_metrics.max_api_latency_ms
                },
                "uptime_seconds": system_metrics.uptime_seconds,
                "uptime_hours": system_metrics.uptime_seconds / 3600
            }
        }
    
    async def check_health(self, tenant_id: str) -> Dict[str, Any]:
        """
        Check overall system health.
        
        Returns:
            Health status with any issues found
        """
        issues = []
        
        # Order metrics
        order_metrics = await self.collect_order_metrics(tenant_id)
        
        if order_metrics.error_rate > 10:
            issues.append({
                "severity": "critical",
                "component": "orders",
                "message": f"High error rate: {order_metrics.error_rate:.1f}%"
            })
        
        if order_metrics.stuck_orders > 0:
            issues.append({
                "severity": "warning",
                "component": "orders",
                "message": f"{order_metrics.stuck_orders} stuck orders"
            })
        
        # PnL metrics
        pnl_metrics = await self.collect_pnl_metrics(tenant_id)
        
        if abs(pnl_metrics.drift_pct) > 1.0:
            issues.append({
                "severity": "critical",
                "component": "pnl",
                "message": f"PnL drift: {pnl_metrics.drift_pct:.2f}%"
            })
        
        # System metrics
        system_metrics = await self.collect_system_metrics()
        
        if system_metrics.errors_last_minute > 10:
            issues.append({
                "severity": "warning",
                "component": "system",
                "message": f"{system_metrics.errors_last_minute} errors in last minute"
            })
        
        # Circuit breakers
        for name, state in system_metrics.circuit_breaker_states.items():
            if state == "open":
                issues.append({
                    "severity": "critical",
                    "component": "circuit_breaker",
                    "message": f"Circuit breaker OPEN: {name}"
                })
        
        # Overall health
        if any(i["severity"] == "critical" for i in issues):
            status = "critical"
        elif issues:
            status = "warning"
        else:
            status = "healthy"
        
        return {
            "status": status,
            "issues": issues,
            "checked_at": datetime.utcnow().isoformat()
        }


# Global instance
def get_metrics_system(db_session: Session) -> MetricsSystem:
    """Get metrics system instance."""
    return MetricsSystem(db_session)
