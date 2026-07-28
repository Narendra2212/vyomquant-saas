"""
core/backpressure.py — BACKPRESSURE SYSTEM (DEPRECATED)

.. deprecated::
    This module is superseded by ``core/backpressure_v2.py``, which provides
    multi-queue monitoring, priority-tier load-shedding, and DAG/Signal/
    Analytics/Execution specific integrations.  All new code—including
    WorkerBase and every background worker—uses backpressure_v2.  This file
    is retained only for historical reference; it has no active callers.


STEP 9: PREVENT SYSTEM OVERLOAD

GOAL: Protect system from crashing under heavy load

STRATEGY:
  IF queue_size > threshold:
    THEN slow down new tasks
    THEN reject low-priority signals
    THEN shed load gracefully

FEATURES:
  - Queue size monitoring per type (dag, execution, portfolio)
  - Dynamic rate limiting based on load
  - Priority-based rejection (low-priority tasks dropped first)
  - Circuit breaker pattern for overloaded subsystems
  - Gradual recovery when load decreases
  - Metrics and alerting for backpressure events

THRESHOLDS:
  - Warning: 50 tasks (slow down)
  - Critical: 100 tasks (reject low priority)
  - Emergency: 200 tasks (reject all non-critical)

USAGE:
    from backend_app.core.backpressure import backpressure, Priority
    
    # Check if task can be accepted
    if backpressure.can_accept(Priority.HIGH):
        await queue.put(task)
    else:
        logger.warning("Task rejected due to backpressure")
    
    # Use context manager for automatic backpressure
    async with backpressure.admission_control(Priority.NORMAL):
        await process_task(task)
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("Backpressure")


class Priority(Enum):
    """Task priority levels for backpressure decisions."""
    CRITICAL = 0   # Always process (e.g., emergency liquidation)
    HIGH = 1       # Process unless emergency
    NORMAL = 2     # Reject if critical
    LOW = 3        # Reject if warning or higher
    BACKGROUND = 4 # Reject if any backpressure


class LoadLevel(Enum):
    """System load levels for graduated response."""
    NORMAL = "normal"         # 0-50 tasks: Normal operation
    WARNING = "warning"       # 50-100 tasks: Slow down
    CRITICAL = "critical"     # 100-200 tasks: Reject low priority
    EMERGENCY = "emergency"     # 200+ tasks: Reject all but critical


@dataclass
class BackpressureConfig:
    """Configuration for backpressure thresholds."""
    warning_threshold: int = 50
    critical_threshold: int = 100
    emergency_threshold: int = 200
    
    # Rate limiting per priority at each level
    rates: Dict[LoadLevel, Dict[Priority, float]] = field(default_factory=lambda: {
        LoadLevel.NORMAL: {
            Priority.CRITICAL: 1.0,   # 100% accepted
            Priority.HIGH: 1.0,
            Priority.NORMAL: 1.0,
            Priority.LOW: 1.0,
            Priority.BACKGROUND: 1.0,
        },
        LoadLevel.WARNING: {
            Priority.CRITICAL: 1.0,
            Priority.HIGH: 1.0,
            Priority.NORMAL: 0.8,     # 80% accepted
            Priority.LOW: 0.5,        # 50% accepted
            Priority.BACKGROUND: 0.2, # 20% accepted
        },
        LoadLevel.CRITICAL: {
            Priority.CRITICAL: 1.0,
            Priority.HIGH: 0.9,
            Priority.NORMAL: 0.5,
            Priority.LOW: 0.0,        # Rejected
            Priority.BACKGROUND: 0.0,
        },
        LoadLevel.EMERGENCY: {
            Priority.CRITICAL: 1.0,
            Priority.HIGH: 0.5,
            Priority.NORMAL: 0.0,     # Rejected
            Priority.LOW: 0.0,
            Priority.BACKGROUND: 0.0,
        },
    })


@dataclass
class QueueMetrics:
    """Metrics for a single queue."""
    queue_type: str
    current_size: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    last_update: float = field(default_factory=time.time)


class BackpressureController:
    """
    STEP 9: Backpressure controller to prevent system overload.
    
    Monitors queue sizes and applies graduated response:
    - NORMAL (< 50): Accept all tasks
    - WARNING (50-100): Slow down, reduce low-priority
    - CRITICAL (100-200): Reject low-priority
    - EMERGENCY (200+): Reject all non-critical
    """
    
    def __init__(self, config: Optional[BackpressureConfig] = None):
        self.config = config or BackpressureConfig()
        self._queue_metrics: Dict[str, QueueMetrics] = {
            "dag": QueueMetrics("dag"),
            "execution": QueueMetrics("execution"),
            "portfolio": QueueMetrics("portfolio"),
        }
        self._lock = asyncio.Lock()
        self._current_level = LoadLevel.NORMAL
        self._rejection_callbacks: List[Callable[[str, Priority, str], Any]] = []
        
        # Stats
        self._stats = {
            "total_accepted": 0,
            "total_rejected": 0,
            "rejection_by_priority": {p: 0 for p in Priority},
            "current_load_level": LoadLevel.NORMAL.value,
        }
        
        logger.info(
            f"[Backpressure] Initialized: "
            f"warning={self.config.warning_threshold}, "
            f"critical={self.config.critical_threshold}, "
            f"emergency={self.config.emergency_threshold}"
        )
    
    def update_queue_size(self, queue_type: str, size: int):
        """Update current queue size."""
        if queue_type not in self._queue_metrics:
            return
        
        self._queue_metrics[queue_type].current_size = size
        self._queue_metrics[queue_type].last_update = time.time()
        
        # Update overall load level based on max queue
        max_size = max(m.current_size for m in self._queue_metrics.values())
        new_level = self._get_load_level(max_size)
        
        if new_level != self._current_level:
            old_level = self._current_level
            self._current_level = new_level
            self._stats["current_load_level"] = new_level.value
            
            if new_level.value > old_level.value:
                logger.warning(
                    f"[Backpressure] Load increased: {old_level.value} → {new_level.value} "
                    f"(max_queue={max_size})"
                )
            else:
                logger.info(
                    f"[Backpressure] Load decreased: {old_level.value} → {new_level.value} "
                    f"(max_queue={max_size})"
                )
    
    def _get_load_level(self, queue_size: int) -> LoadLevel:
        """Determine load level from queue size."""
        if queue_size >= self.config.emergency_threshold:
            return LoadLevel.EMERGENCY
        elif queue_size >= self.config.critical_threshold:
            return LoadLevel.CRITICAL
        elif queue_size >= self.config.warning_threshold:
            return LoadLevel.WARNING
        else:
            return LoadLevel.NORMAL
    
    def can_accept(self, priority: Priority = Priority.NORMAL) -> bool:
        """
        Check if task with given priority can be accepted.
        
        Uses probabilistic rejection based on priority and load level.
        """
        rate = self.config.rates[self._current_level][priority]
        
        if rate >= 1.0:
            return True
        elif rate <= 0.0:
            return False
        else:
            # Probabilistic acceptance
            import random
            return random.random() < rate
    
    async def try_accept(self, priority: Priority = Priority.NORMAL, timeout: float = 5.0) -> bool:
        """
        Try to accept task, waiting for load to decrease if needed.
        
        Returns True when task can be accepted, False if timeout.
        """
        start = time.time()
        
        while time.time() - start < timeout:
            if self.can_accept(priority):
                return True
            
            # Wait a bit and retry
            await asyncio.sleep(0.1)
        
        return False
    
    @asynccontextmanager
    async def admission_control(self, priority: Priority = Priority.NORMAL):
        """
        Context manager for admission control.
        
        Usage:
            async with backpressure.admission_control(Priority.HIGH):
                await process_task(task)
        """
        if not self.can_accept(priority):
            raise BackpressureRejected(
                f"Task with priority {priority.name} rejected due to {self._current_level.value} load"
            )
        
        self._record_acceptance()
        try:
            yield
        except Exception:
            # Re-raise exceptions
            raise
    
    def _record_acceptance(self):
        """Record task acceptance."""
        self._stats["total_accepted"] += 1
    
    def record_rejection(self, priority: Priority, reason: str = "backpressure"):
        """Record task rejection."""
        self._stats["total_rejected"] += 1
        self._stats["rejection_by_priority"][priority] += 1
        
        # Notify callbacks
        for callback in self._rejection_callbacks:
            try:
                callback(priority.name, priority, reason)
            except Exception:
                pass
    
    def register_rejection_callback(self, callback: Callable[[str, Priority, str], Any]):
        """Register callback for rejection events."""
        self._rejection_callbacks.append(callback)
    
    def get_current_level(self) -> LoadLevel:
        """Get current load level."""
        return self._current_level
    
    def get_queue_sizes(self) -> Dict[str, int]:
        """Get current queue sizes."""
        return {k: v.current_size for k, v in self._queue_metrics.items()}
    
    def get_stats(self) -> Dict[str, Any]:
        """Get backpressure statistics."""
        return {
            **self._stats,
            "queue_sizes": self.get_queue_sizes(),
            "current_level": self._current_level.value,
            "thresholds": {
                "warning": self.config.warning_threshold,
                "critical": self.config.critical_threshold,
                "emergency": self.config.emergency_threshold,
            },
        }
    
    def should_shed_load(self) -> bool:
        """Check if aggressive load shedding should occur."""
        return self._current_level in [LoadLevel.CRITICAL, LoadLevel.EMERGENCY]
    
    def get_priority_for_task(self, task_type: str) -> Priority:
        """Determine priority based on task type."""
        priorities = {
            # Critical - never drop
            "emergency_liquidate": Priority.CRITICAL,
            "risk_limit_hit": Priority.CRITICAL,
            
            # High - drop only in emergency
            "order_execution": Priority.HIGH,
            "position_update": Priority.HIGH,
            "margin_call": Priority.HIGH,
            
            # Normal - drop in critical
            "dag_task": Priority.NORMAL,
            "strategy_execution": Priority.NORMAL,
            
            # Low - drop in warning
            "portfolio_snapshot": Priority.LOW,
            "pnl_report": Priority.LOW,
            
            # Background - drop first
            "log_cleanup": Priority.BACKGROUND,
            "metrics_aggregation": Priority.BACKGROUND,
        }
        return priorities.get(task_type, Priority.NORMAL)


class BackpressureRejected(Exception):
    """Exception raised when task is rejected due to backpressure."""
    
    def __init__(self, message: str, priority: Optional[Priority] = None):
        super().__init__(message)
        self.priority = priority


class CircuitBreaker:
    """
    Circuit breaker pattern for overloaded subsystems.
    
    States:
    - CLOSED: Normal operation
    - OPEN: Rejecting requests (overload)
    - HALF_OPEN: Testing if recovered
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        
        self._state = "CLOSED"
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0
        self._lock = asyncio.Lock()
    
    async def can_execute(self) -> bool:
        """Check if execution is allowed."""
        async with self._lock:
            if self._state == "CLOSED":
                return True
            elif self._state == "OPEN":
                # Check if recovery timeout passed
                if self._last_failure_time and \
                   time.time() - self._last_failure_time > self.recovery_timeout:
                    self._state = "HALF_OPEN"
                    self._half_open_calls = 0
                    logger.info("[CircuitBreaker] Entering HALF_OPEN state")
                    return True
                return False
            elif self._state == "HALF_OPEN":
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False
            return True
    
    async def record_success(self):
        """Record successful execution."""
        async with self._lock:
            if self._state == "HALF_OPEN":
                self._state = "CLOSED"
                self._failure_count = 0
                logger.info("[CircuitBreaker] Closed (recovered)")
            elif self._state == "CLOSED":
                self._failure_count = 0
    
    async def record_failure(self):
        """Record failed execution."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            if self._state == "CLOSED" and self._failure_count >= self.failure_threshold:
                self._state = "OPEN"
                logger.critical(
                    f"[CircuitBreaker] OPENED after {self.failure_threshold} failures"
                )
            elif self._state == "HALF_OPEN":
                self._state = "OPEN"
                logger.warning("[CircuitBreaker] Back to OPEN (recovery failed)")
    
    def get_state(self) -> str:
        """Get current circuit breaker state."""
        return self._state


# Global singleton
backpressure = BackpressureController()


def get_backpressure() -> BackpressureController:
    """Get global backpressure controller."""
    return backpressure
