"""
core/slo_monitor.py — SLO MONITORING AND COMPLIANCE TRACKING

STEP 11: PRODUCTION SAFETY WITH SLOs

This module provides:
- SLO definition and tracking
- Compliance calculation
- Alert generation on violation
- Rollback recommendations

SLOs DEFINED:
1. Latency SLOs:
   - API P99 latency < 200ms (99% compliance)
   - WebSocket P95 latency < 100ms
   - Order execution P99 < 500ms

2. Success Rate SLOs:
   - API success rate > 99.9%
   - Order placement success > 99.5%
   - WebSocket connection success > 99%

3. Error Rate SLOs:
   - API error rate < 0.1%
   - Database error rate < 0.01%
   - Exchange API error rate < 1%

USAGE:
    from backend_app.core.slo_monitor import slo_monitor, SLOType
    
    # Record request latency
    slo_monitor.record_latency("api", duration_seconds)
    
    # Record success/failure
    slo_monitor.record_request("api", success=True)
    
    # Check compliance
    compliance = slo_monitor.get_compliance()
    if not compliance['api_latency'].is_compliant:
        print("SLO violation detected!")
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from collections import deque

# Prometheus client (optional)
try:
    from prometheus_client import Histogram, Counter, Gauge
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

logger = logging.getLogger("SLOMonitor")


# ═══════════════════════════════════════════════════════════════════════════
# SLO TYPES AND THRESHOLDS
# ═══════════════════════════════════════════════════════════════════════════

class SLOType(Enum):
    """Types of Service Level Objectives."""
    LATENCY_P99 = "latency_p99"
    LATENCY_P95 = "latency_p95"
    SUCCESS_RATE = "success_rate"
    ERROR_RATE = "error_rate"
    AVAILABILITY = "availability"


@dataclass
class SLOThreshold:
    """Threshold configuration for an SLO."""
    slo_type: SLOType
    service: str  # api, websocket, database, exchange, etc.
    
    # Threshold values
    target_value: float  # e.g., 0.99 for 99%
    warning_threshold: float  # e.g., 0.995 (slightly better than target)
    critical_threshold: float  # e.g., 0.98 (worse than target)
    
    # Time windows
    evaluation_window_seconds: float = 300.0  # 5 minutes
    
    # Units
    unit: str = "percent"  # percent, seconds, ratio
    
    def __post_init__(self):
        if self.unit == "percent":
            assert 0 <= self.target_value <= 1
            assert 0 <= self.warning_threshold <= 1
            assert 0 <= self.critical_threshold <= 1


# Pre-defined SLO thresholds
DEFAULT_SLO_THRESHOLDS = [
    # Latency SLOs
    SLOThreshold(
        slo_type=SLOType.LATENCY_P99,
        service="api",
        target_value=0.2,  # 200ms
        warning_threshold=0.15,  # 150ms
        critical_threshold=0.5,  # 500ms
        unit="seconds"
    ),
    SLOThreshold(
        slo_type=SLOType.LATENCY_P95,
        service="websocket",
        target_value=0.1,  # 100ms
        warning_threshold=0.05,  # 50ms
        critical_threshold=0.2,  # 200ms
        unit="seconds"
    ),
    SLOThreshold(
        slo_type=SLOType.LATENCY_P99,
        service="order_execution",
        target_value=0.5,  # 500ms
        warning_threshold=0.3,  # 300ms
        critical_threshold=1.0,  # 1s
        unit="seconds"
    ),
    
    # Success Rate SLOs
    SLOThreshold(
        slo_type=SLOType.SUCCESS_RATE,
        service="api",
        target_value=0.999,  # 99.9%
        warning_threshold=0.9995,  # 99.95%
        critical_threshold=0.99,  # 99%
        unit="percent"
    ),
    SLOThreshold(
        slo_type=SLOType.SUCCESS_RATE,
        service="order_placement",
        target_value=0.995,  # 99.5%
        warning_threshold=0.998,  # 99.8%
        critical_threshold=0.98,  # 98%
        unit="percent"
    ),
    SLOThreshold(
        slo_type=SLOType.SUCCESS_RATE,
        service="websocket",
        target_value=0.99,  # 99%
        warning_threshold=0.995,  # 99.5%
        critical_threshold=0.95,  # 95%
        unit="percent"
    ),
    
    # Error Rate SLOs
    SLOThreshold(
        slo_type=SLOType.ERROR_RATE,
        service="api",
        target_value=0.001,  # 0.1%
        warning_threshold=0.0005,  # 0.05%
        critical_threshold=0.01,  # 1%
        unit="percent"
    ),
    SLOThreshold(
        slo_type=SLOType.ERROR_RATE,
        service="database",
        target_value=0.0001,  # 0.01%
        warning_threshold=0.00005,  # 0.005%
        critical_threshold=0.001,  # 0.1%
        unit="percent"
    ),
    SLOThreshold(
        slo_type=SLOType.ERROR_RATE,
        service="exchange",
        target_value=0.01,  # 1%
        warning_threshold=0.005,  # 0.5%
        critical_threshold=0.05,  # 5%
        unit="percent"
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# SLO COMPLIANCE STATUS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class SLOCompliance:
    """SLO compliance status."""
    slo_type: SLOType
    service: str
    current_value: float
    target_value: float
    is_compliant: bool
    compliance_rate: float  # 1.0 = 100% compliant
    status: str  # "healthy", "warning", "critical", "violated"
    last_violation: Optional[datetime] = None
    violation_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "slo_type": self.slo_type.value,
            "service": self.service,
            "current_value": self.current_value,
            "target_value": self.target_value,
            "is_compliant": self.is_compliant,
            "compliance_rate": self.compliance_rate,
            "status": self.status,
            "last_violation": self.last_violation.isoformat() if self.last_violation else None,
            "violation_count": self.violation_count,
        }


# ═══════════════════════════════════════════════════════════════════════════
# SLO MONITOR
# ═══════════════════════════════════════════════════════════════════════════

class SLOMonitor:
    """
    Monitors Service Level Objectives and tracks compliance.
    
    Features:
    - Real-time SLO tracking
    - Violation detection
    - Alert generation
    - Rollback recommendations
    - Compliance reporting
    """
    
    def __init__(self, thresholds: Optional[List[SLOThreshold]] = None):
        self.thresholds = thresholds or DEFAULT_SLO_THRESHOLDS
        
        # Data storage for calculations
        self._latency_data: Dict[str, deque] = {}  # service -> deque of (timestamp, duration)
        self._request_data: Dict[str, deque] = {}  # service -> deque of (timestamp, success)
        self._error_data: Dict[str, deque] = {}  # service -> deque of (timestamp, error)
        
        # Current compliance status
        self._compliance_status: Dict[str, SLOCompliance] = {}
        
        # Callbacks
        self._violation_callbacks: List[Callable[[SLOCompliance], Any]] = []
        self._rollback_callbacks: List[Callable[[List[SLOCompliance]], Any]] = []
        
        # State
        self._running = False
        self._evaluation_task: Optional[asyncio.Task] = None
        
        # Initialize Prometheus metrics if available
        self._init_prometheus_metrics()
        
        logger.info(f"[SLOMonitor] Initialized with {len(self.thresholds)} SLO thresholds")
    
    def _init_prometheus_metrics(self):
        """Initialize Prometheus metrics."""
        if not PROMETHEUS_AVAILABLE:
            return
        
        # Latency histograms
        self._latency_histograms: Dict[str, Histogram] = {}
        for threshold in self.thresholds:
            if threshold.slo_type in [SLOType.LATENCY_P99, SLOType.LATENCY_P95]:
                name = f"{threshold.service}_latency_seconds"
                if name not in self._latency_histograms:
                    self._latency_histograms[name] = Histogram(
                        name,
                        f'{threshold.service} request latency',
                        buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
                    )
        
        # Success/error counters
        self._request_counters: Dict[str, Counter] = {}
        for threshold in self.thresholds:
            if threshold.slo_type == SLOType.SUCCESS_RATE:
                name = f"{threshold.service}_requests_total"
                if name not in self._request_counters:
                    self._request_counters[name] = Counter(
                        name,
                        f'{threshold.service} requests',
                        ['status']
                    )
        
        # Compliance gauges
        self._compliance_gauges: Dict[str, Gauge] = {}
        for threshold in self.thresholds:
            name = f"slo_compliance_{threshold.service}_{threshold.slo_type.value}"
            self._compliance_gauges[name] = Gauge(
                name,
                f'SLO compliance for {threshold.service}'
            )
    
    async def start(self, evaluation_interval: float = 30.0):
        """Start SLO monitoring."""
        self._running = True
        self._evaluation_task = asyncio.create_task(
            self._evaluation_loop(evaluation_interval)
        )
        logger.info("[SLOMonitor] Started")
    
    async def stop(self):
        """Stop SLO monitoring."""
        self._running = False
        if self._evaluation_task:
            self._evaluation_task.cancel()
            try:
                await self._evaluation_task
            except asyncio.CancelledError:
                pass
        logger.info("[SLOMonitor] Stopped")
    
    def record_latency(self, service: str, duration_seconds: float):
        """Record a latency measurement."""
        if service not in self._latency_data:
            self._latency_data[service] = deque(maxlen=10000)
        
        self._latency_data[service].append((time.time(), duration_seconds))
        
        # Update Prometheus
        if PROMETHEUS_AVAILABLE and service in self._latency_histograms:
            self._latency_histograms[service].observe(duration_seconds)
    
    def record_request(self, service: str, success: bool):
        """Record a request success/failure."""
        if service not in self._request_data:
            self._request_data[service] = deque(maxlen=10000)
        
        self._request_data[service].append((time.time(), success))
        
        # Update Prometheus
        if PROMETHEUS_AVAILABLE and service in self._request_counters:
            status = "success" if success else "failure"
            self._request_counters[service].labels(status=status).inc()
    
    def record_error(self, service: str, error_type: str = "generic"):
        """Record an error occurrence."""
        if service not in self._error_data:
            self._error_data[service] = deque(maxlen=10000)
        
        self._error_data[service].append((time.time(), error_type))
    
    async def _evaluation_loop(self, interval: float):
        """Periodic SLO evaluation loop."""
        while self._running:
            try:
                await self._evaluate_all_slos()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[SLOMonitor] Evaluation error: {e}")
                await asyncio.sleep(interval)
    
    async def _evaluate_all_slos(self):
        """Evaluate all SLOs and update compliance status."""
        now = time.time()
        
        for threshold in self.thresholds:
            compliance = self._evaluate_slo(threshold, now)
            key = f"{threshold.service}:{threshold.slo_type.value}"
            
            # Check for status change
            prev_compliance = self._compliance_status.get(key)
            
            if prev_compliance:
                if prev_compliance.is_compliant and not compliance.is_compliant:
                    # SLO violation started
                    compliance.last_violation = datetime.utcnow()
                    compliance.violation_count = prev_compliance.violation_count + 1
                    await self._notify_violation(compliance)
                elif not prev_compliance.is_compliant and compliance.is_compliant:
                    # SLO violation ended
                    logger.info(f"[SLOMonitor] SLO restored: {key}")
            
            self._compliance_status[key] = compliance
            
            # Update Prometheus gauge
            if PROMETHEUS_AVAILABLE:
                gauge_name = f"slo_compliance_{threshold.service}_{threshold.slo_type.value}"
                if gauge_name in self._compliance_gauges:
                    self._compliance_gauges[gauge_name].set(
                        1.0 if compliance.is_compliant else 0.0
                    )
        
        # Check for rollback condition
        await self._check_rollback_condition()
    
    def _evaluate_slo(self, threshold: SLOThreshold, now: float) -> SLOCompliance:
        """Evaluate a single SLO."""
        window_start = now - threshold.evaluation_window_seconds
        
        if threshold.slo_type in [SLOType.LATENCY_P99, SLOType.LATENCY_P95]:
            return self._evaluate_latency_slo(threshold, window_start, now)
        elif threshold.slo_type == SLOType.SUCCESS_RATE:
            return self._evaluate_success_rate_slo(threshold, window_start, now)
        elif threshold.slo_type == SLOType.ERROR_RATE:
            return self._evaluate_error_rate_slo(threshold, window_start, now)
        else:
            return SLOCompliance(
                slo_type=threshold.slo_type,
                service=threshold.service,
                current_value=0.0,
                target_value=threshold.target_value,
                is_compliant=False,
                compliance_rate=0.0,
                status="unknown"
            )
    
    def _evaluate_latency_slo(self, threshold: SLOThreshold, window_start: float, now: float) -> SLOCompliance:
        """Evaluate latency SLO."""
        service = threshold.service
        data = self._latency_data.get(service, deque())
        
        # Filter to window
        latencies = [
            duration for ts, duration in data
            if window_start <= ts <= now
        ]
        
        if not latencies:
            return SLOCompliance(
                slo_type=threshold.slo_type,
                service=service,
                current_value=0.0,
                target_value=threshold.target_value,
                is_compliant=True,
                compliance_rate=1.0,
                status="healthy"
            )
        
        # Calculate percentile
        latencies_sorted = sorted(latencies)
        if threshold.slo_type == SLOType.LATENCY_P99:
            percentile_index = int(len(latencies_sorted) * 0.99)
        else:  # P95
            percentile_index = int(len(latencies_sorted) * 0.95)
        
        percentile_index = min(percentile_index, len(latencies_sorted) - 1)
        current_p = latencies_sorted[percentile_index]
        
        # Determine status
        if current_p <= threshold.warning_threshold:
            status = "healthy"
        elif current_p <= threshold.target_value:
            status = "warning"
        elif current_p <= threshold.critical_threshold:
            status = "critical"
        else:
            status = "violated"
        
        is_compliant = current_p <= threshold.target_value
        
        return SLOCompliance(
            slo_type=threshold.slo_type,
            service=service,
            current_value=current_p,
            target_value=threshold.target_value,
            is_compliant=is_compliant,
            compliance_rate=1.0 if is_compliant else (threshold.target_value / current_p if current_p > 0 else 0),
            status=status
        )
    
    def _evaluate_success_rate_slo(self, threshold: SLOThreshold, window_start: float, now: float) -> SLOCompliance:
        """Evaluate success rate SLO."""
        service = threshold.service
        data = self._request_data.get(service, deque())
        
        # Filter to window
        requests = [
            success for ts, success in data
            if window_start <= ts <= now
        ]
        
        if not requests:
            return SLOCompliance(
                slo_type=threshold.slo_type,
                service=service,
                current_value=1.0,
                target_value=threshold.target_value,
                is_compliant=True,
                compliance_rate=1.0,
                status="healthy"
            )
        
        success_count = sum(1 for r in requests if r)
        success_rate = success_count / len(requests)
        
        # Determine status
        if success_rate >= threshold.warning_threshold:
            status = "healthy"
        elif success_rate >= threshold.target_value:
            status = "warning"
        elif success_rate >= threshold.critical_threshold:
            status = "critical"
        else:
            status = "violated"
        
        is_compliant = success_rate >= threshold.target_value
        
        return SLOCompliance(
            slo_type=threshold.slo_type,
            service=service,
            current_value=success_rate,
            target_value=threshold.target_value,
            is_compliant=is_compliant,
            compliance_rate=success_rate / threshold.target_value if threshold.target_value > 0 else 0,
            status=status
        )
    
    def _evaluate_error_rate_slo(self, threshold: SLOThreshold, window_start: float, now: float) -> SLOCompliance:
        """Evaluate error rate SLO."""
        service = threshold.service
        errors = self._error_data.get(service, deque())
        requests = self._request_data.get(service, deque())
        
        # Filter to window
        error_count = sum(
            1 for ts, _ in errors
            if window_start <= ts <= now
        )
        
        request_count = sum(
            1 for ts, _ in requests
            if window_start <= ts <= now
        )
        
        if request_count == 0:
            error_rate = 0.0
        else:
            error_rate = error_count / request_count
        
        # For error rate, lower is better
        is_compliant = error_rate <= threshold.target_value
        
        if error_rate <= threshold.warning_threshold:
            status = "healthy"
        elif error_rate <= threshold.target_value:
            status = "warning"
        elif error_rate <= threshold.critical_threshold:
            status = "critical"
        else:
            status = "violated"
        
        return SLOCompliance(
            slo_type=threshold.slo_type,
            service=service,
            current_value=error_rate,
            target_value=threshold.target_value,
            is_compliant=is_compliant,
            compliance_rate=threshold.target_value / error_rate if error_rate > 0 else 1.0,
            status=status
        )
    
    async def _notify_violation(self, compliance: SLOCompliance):
        """Notify callbacks of SLO violation."""
        logger.critical(
            f"[SLOMonitor] SLO VIOLATION: {compliance.service} {compliance.slo_type.value} "
            f"is {compliance.status} (current: {compliance.current_value}, target: {compliance.target_value})"
        )
        
        for callback in self._violation_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(callback(compliance))
                else:
                    callback(compliance)
            except Exception as e:
                logger.error(f"[SLOMonitor] Violation callback error: {e}")
    
    async def _check_rollback_condition(self):
        """Check if rollback should be triggered."""
        # Rollback conditions:
        # 1. Multiple critical SLO violations
        # 2. Order placement SLO violated
        
        critical_violations = [
            c for c in self._compliance_status.values()
            if c.status == "violated" and c.service in ["api", "order_placement"]
        ]
        
        order_violation = any(
            c.service == "order_placement" and c.status == "violated"
            for c in self._compliance_status.values()
        )
        
        should_rollback = (
            len(critical_violations) >= 2 or
            order_violation
        )
        
        if should_rollback:
            logger.critical(
                f"[SLOMonitor] ROLLBACK TRIGGERED: {len(critical_violations)} critical SLO violations"
            )
            
            for callback in self._rollback_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        asyncio.create_task(callback(critical_violations))
                    else:
                        callback(critical_violations)
                except Exception as e:
                    logger.error(f"[SLOMonitor] Rollback callback error: {e}")
    
    def register_violation_callback(self, callback: Callable[[SLOCompliance], Any]):
        """Register callback for SLO violations."""
        self._violation_callbacks.append(callback)
    
    def register_rollback_callback(self, callback: Callable[[List[SLOCompliance]], Any]):
        """Register callback for rollback triggers."""
        self._rollback_callbacks.append(callback)
    
    def get_compliance(self) -> Dict[str, SLOCompliance]:
        """Get current compliance status for all SLOs."""
        return self._compliance_status.copy()
    
    def get_summary(self) -> Dict[str, Any]:
        """Get SLO compliance summary."""
        total = len(self._compliance_status)
        compliant = sum(1 for c in self._compliance_status.values() if c.is_compliant)
        violated = total - compliant
        
        return {
            "total_slos": total,
            "compliant": compliant,
            "violated": violated,
            "compliance_rate": compliant / total if total > 0 else 0,
            "slos": {
                key: compliance.to_dict()
                for key, compliance in self._compliance_status.items()
            }
        }


# Global singleton
slo_monitor = SLOMonitor()


def get_slo_monitor() -> SLOMonitor:
    """Get global SLO monitor instance."""
    return slo_monitor
