"""
core/backpressure_v2.py — BACKPRESSURE + LOAD SHEDDING (1000+ Users)

STEP 3: PREVENT SYSTEM OVERLOAD FOR PRODUCTION SCALE

GOAL: Graceful degradation under extreme load

STRATEGY:
  IF queue_size > threshold:
    THEN slow down new DAG tasks
    THEN drop low-priority signals
    THEN block new strategies

PRIORITY LEVELS:
  HIGH  → Execution (orders, positions) - Never drop
  MED   → Signals (price updates, indicators) - Drop if critical
  LOW   → Analytics (reports, logging) - Drop first

ARCHITECTURE:
  ┌─────────────────────────────────────────────────────────────────┐
  │                   BACKPRESSURE CONTROLLER V2                   │
  │                                                                 │
  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐        │
  │  │  DAG Queue  │    │ Signal Queue│    │ Analytics   │        │
  │  │  Monitor    │    │  Monitor    │    │  Monitor    │        │
  │  │  (50-200)   │    │  (100-500)  │    │  (10-50)    │        │
  │  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘        │
  │         │                   │                   │              │
  │         └───────────────────┼───────────────────┘              │
  │                             │                                   │
  │                      ┌──────▼──────┐                            │
  │                      │ Load Level  │                            │
  │                      │  Assessment │                            │
  │                      └──────┬──────┘                            │
  │                             │                                   │
  │         ┌───────────────────┼───────────────────┐               │
  │         ▼                   ▼                   ▼               │
  │    ┌─────────┐        ┌─────────┐        ┌─────────┐          │
  │    │  Slow   │        │  Drop   │        │  Block  │          │
  │    │  DAG    │        │  Low    │        │  New    │          │
  │    │  Tasks  │        │Priority │        │Strategies│          │
  │    │(throttle)│       │(signals)│        │(reject) │          │
  │    └─────────┘        └─────────┘        └─────────┘          │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘

THRESHOLDS:
  DAG Queue:
    - NORMAL: < 50 tasks
    - WARNING: 50-100 tasks  → Throttle new DAG tasks
    - CRITICAL: 100-200 tasks → Drop MED priority signals
    - EMERGENCY: > 200 tasks  → Block new strategies, drop LOW priority

  Signal Queue:
    - NORMAL: < 100 signals
    - WARNING: 100-250 signals → Sample/drop MED priority
    - CRITICAL: > 250 signals → Drop all MED, throttle LOW

EXPECTED RESULT:
  ✔ System degrades gracefully
  ✔ No crashes under spikes
  ✔ Critical execution preserved
  ✔ Analytics shed first
"""

import asyncio
import logging
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("BackpressureV2")


class Priority(Enum):
    """Priority levels for load shedding decisions."""
    CRITICAL = 0   # Execution - never drop (orders, positions)
    HIGH = 1       # Core signals - drop only in emergency
    MEDIUM = 2     # Standard signals - drop in critical
    LOW = 3        # Analytics - drop first


class LoadLevel(Enum):
    """System load levels."""
    NORMAL = "normal"         # All systems normal
    WARNING = "warning"       # Throttle non-critical
    CRITICAL = "critical"       # Shed load aggressively
    EMERGENCY = "emergency"     # Survival mode


class QueueType(Enum):
    """Types of queues to monitor."""
    DAG = "dag"                 # DAG execution queue
    SIGNAL = "signal"           # Signal processing queue
    ANALYTICS = "analytics"     # Analytics/reporting queue
    EXECUTION = "execution"     # Order execution queue


@dataclass
class QueueThresholds:
    """Thresholds for a specific queue."""
    warning: int
    critical: int
    emergency: int


@dataclass
class BackpressureStats:
    """Statistics for backpressure decisions."""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    dag_queue_size: int = 0
    signal_queue_size: int = 0
    analytics_queue_size: int = 0
    execution_queue_size: int = 0
    current_level: LoadLevel = LoadLevel.NORMAL
    
    # Action counts
    tasks_throttled: int = 0
    signals_dropped: int = 0
    strategies_blocked: int = 0
    
    # Priority breakdown
    dropped_by_priority: Dict[Priority, int] = field(default_factory=lambda: {
        Priority.CRITICAL: 0,
        Priority.HIGH: 0,
        Priority.MEDIUM: 0,
        Priority.LOW: 0,
    })


class BackpressureControllerV2:
    """
    STEP 3: Advanced backpressure for 1000+ users.
    
    Features:
    - Multi-queue monitoring (DAG, Signal, Analytics, Execution)
    - Priority-based load shedding
    - Strategy admission control
    - Graduated response (throttle → drop → block)
    - Automatic recovery when load decreases
    """
    
    def __init__(self):
        # Queue thresholds
        self.thresholds: Dict[QueueType, QueueThresholds] = {
            QueueType.DAG: QueueThresholds(warning=50, critical=100, emergency=200),
            QueueType.SIGNAL: QueueThresholds(warning=100, critical=250, emergency=500),
            QueueType.ANALYTICS: QueueThresholds(warning=10, critical=25, emergency=50),
            QueueType.EXECUTION: QueueThresholds(warning=20, critical=50, emergency=100),
        }
        
        # Current queue sizes
        self._queue_sizes: Dict[QueueType, int] = {
            QueueType.DAG: 0,
            QueueType.SIGNAL: 0,
            QueueType.ANALYTICS: 0,
            QueueType.EXECUTION: 0,
        }
        
        # Current load level
        self._current_level = LoadLevel.NORMAL
        
        # Strategy blocking
        self._blocked_strategies: Set[str] = set()
        self._strategy_blocking_enabled = False
        
        # Throttling
        self._throttle_rate = 1.0  # 1.0 = full speed, 0.0 = stopped
        
        # Statistics
        self._stats = BackpressureStats()
        
        # Callbacks
        self._level_change_callbacks: List[Callable[[LoadLevel, LoadLevel], Any]] = []
        self._shed_callbacks: List[Callable[[Priority, str], Any]] = []
        
        logger.info("[BackpressureV2] Initialized")
    
    def update_queue_size(self, queue_type: QueueType, size: int):
        """Update queue size and reassess load level."""
        self._queue_sizes[queue_type] = size
        self._reassess_load_level()
    
    def _reassess_load_level(self):
        """Reassess overall load level based on all queues."""
        # Find the worst load level across all queues
        worst_level = LoadLevel.NORMAL
        
        for queue_type, size in self._queue_sizes.items():
            thresholds = self.thresholds[queue_type]
            
            if size >= thresholds.emergency:
                level = LoadLevel.EMERGENCY
            elif size >= thresholds.critical:
                level = LoadLevel.CRITICAL
            elif size >= thresholds.warning:
                level = LoadLevel.WARNING
            else:
                level = LoadLevel.NORMAL
            
            # Keep the worst level
            if self._level_priority(level) > self._level_priority(worst_level):
                worst_level = level
        
        # Check for level change
        if worst_level != self._current_level:
            old_level = self._current_level
            self._current_level = worst_level
            self._stats.current_level = worst_level
            
            logger.warning(
                f"[BackpressureV2] Load level changed: {old_level.value} → {worst_level.value}"
            )
            
            # Notify callbacks
            for callback in self._level_change_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        asyncio.create_task(callback(old_level, worst_level))
                    else:
                        callback(old_level, worst_level)
                except Exception as e:
                    logger.error(f"[BackpressureV2] Level change callback error: {e}")
            
            # Apply load shedding actions
            self._apply_load_shedding(worst_level)
    
    def _level_priority(self, level: LoadLevel) -> int:
        """Get numeric priority for load level (higher = worse)."""
        priorities = {
            LoadLevel.NORMAL: 0,
            LoadLevel.WARNING: 1,
            LoadLevel.CRITICAL: 2,
            LoadLevel.EMERGENCY: 3,
        }
        return priorities.get(level, 0)
    
    def _apply_load_shedding(self, level: LoadLevel):
        """Apply load shedding based on level."""
        if level == LoadLevel.NORMAL:
            self._throttle_rate = 1.0
            self._strategy_blocking_enabled = False
            
        elif level == LoadLevel.WARNING:
            # Throttle DAG tasks to 80%
            self._throttle_rate = 0.8
            self._strategy_blocking_enabled = False
            
        elif level == LoadLevel.CRITICAL:
            # Throttle to 50%, block new strategies
            self._throttle_rate = 0.5
            self._strategy_blocking_enabled = True
            
        elif level == LoadLevel.EMERGENCY:
            # Survival mode - minimal processing
            self._throttle_rate = 0.2
            self._strategy_blocking_enabled = True
    
    def can_accept(self, priority: Priority, item_type: str = "task") -> bool:
        """
        Check if item with given priority can be accepted.
        
        Args:
            priority: Priority level of the item
            item_type: Type of item (task, signal, strategy, etc.)
        
        Returns:
            True if item should be accepted, False to drop/reject
        """
        # Critical items always accepted
        if priority == Priority.CRITICAL:
            return True
        
        # Strategy admission control
        if item_type == "strategy":
            if self._strategy_blocking_enabled:
                self._stats.strategies_blocked += 1
                return False
            return True
        
        # Load-based acceptance
        if self._current_level == LoadLevel.EMERGENCY:
            # Only CRITICAL in emergency
            return priority == Priority.CRITICAL
        
        elif self._current_level == LoadLevel.CRITICAL:
            # Drop LOW and MEDIUM
            if priority in [Priority.LOW, Priority.MEDIUM]:
                self._record_drop(priority)
                return False
            return True
        
        elif self._current_level == LoadLevel.WARNING:
            # Probabilistic drop for LOW priority
            if priority == Priority.LOW:
                # 50% drop rate in warning
                if random.random() < 0.5:
                    self._record_drop(priority)
                    return False
            return True
        
        # Normal - accept all
        return True
    
    def should_throttle(self) -> bool:
        """Check if processing should be throttled."""
        return self._throttle_rate < 1.0
    
    def get_throttle_delay(self) -> float:
        """Get delay to apply for throttling (in seconds)."""
        if self._throttle_rate >= 1.0:
            return 0.0
        
        # Calculate delay: 20% throttle = 0.25s delay between tasks
        # Formula: (1 / throttle_rate - 1) * base_delay
        base_delay = 0.2
        delay = (1.0 / max(self._throttle_rate, 0.1) - 1.0) * base_delay
        return delay
    
    @asynccontextmanager
    async def throttled_execution(self):
        """Context manager for throttled execution."""
        if self.should_throttle():
            delay = self.get_throttle_delay()
            if delay > 0:
                await asyncio.sleep(delay)
                self._stats.tasks_throttled += 1
        yield
    
    def _record_drop(self, priority: Priority):
        """Record a dropped item."""
        self._stats.signals_dropped += 1
        self._stats.dropped_by_priority[priority] += 1
        
        # Notify callbacks
        for callback in self._shed_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(callback(priority, "backpressure"))
                else:
                    callback(priority, "backpressure")
            except Exception as e:
                logger.error(f"[BackpressureV2] Shed callback error: {e}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # STRATEGY MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════════
    
    def can_start_strategy(self, strategy_id: str) -> bool:
        """Check if new strategy can be started."""
        if strategy_id in self._blocked_strategies:
            return False
        
        if self._strategy_blocking_enabled:
            self._blocked_strategies.add(strategy_id)
            self._stats.strategies_blocked += 1
            logger.warning(f"[BackpressureV2] Blocked strategy start: {strategy_id}")
            return False
        
        return True
    
    def block_strategy(self, strategy_id: str):
        """Manually block a strategy."""
        self._blocked_strategies.add(strategy_id)
        logger.info(f"[BackpressureV2] Manually blocked strategy: {strategy_id}")
    
    def unblock_strategy(self, strategy_id: str):
        """Unblock a strategy."""
        self._blocked_strategies.discard(strategy_id)
        logger.info(f"[BackpressureV2] Unblocked strategy: {strategy_id}")
    
    def is_strategy_blocked(self, strategy_id: str) -> bool:
        """Check if strategy is blocked."""
        return strategy_id in self._blocked_strategies or self._strategy_blocking_enabled
    
    # ═══════════════════════════════════════════════════════════════════════
    # CALLBACKS
    # ═══════════════════════════════════════════════════════════════════════
    
    def register_level_change_callback(self, callback: Callable[[LoadLevel, LoadLevel], Any]):
        """Register callback for load level changes."""
        self._level_change_callbacks.append(callback)
    
    def register_shed_callback(self, callback: Callable[[Priority, str], Any]):
        """Register callback for load shedding events."""
        self._shed_callbacks.append(callback)
    
    # ═══════════════════════════════════════════════════════════════════════
    # STATS & MONITORING
    # ═══════════════════════════════════════════════════════════════════════
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current backpressure statistics."""
        return {
            "timestamp": self._stats.timestamp.isoformat(),
            "load_level": self._current_level.value,
            "queue_sizes": {
                qt.value: size for qt, size in self._queue_sizes.items()
            },
            "throttle_rate": self._throttle_rate,
            "strategy_blocking_enabled": self._strategy_blocking_enabled,
            "blocked_strategies_count": len(self._blocked_strategies),
            "actions": {
                "tasks_throttled": self._stats.tasks_throttled,
                "signals_dropped": self._stats.signals_dropped,
                "strategies_blocked": self._stats.strategies_blocked,
            },
            "dropped_by_priority": {
                p.name: count for p, count in self._stats.dropped_by_priority.items()
            },
        }
    
    def get_current_level(self) -> LoadLevel:
        """Get current load level."""
        return self._current_level
    
    def reset_stats(self):
        """Reset statistics counters."""
        self._stats = BackpressureStats(current_level=self._current_level)


# Global singleton
backpressure_v2 = BackpressureControllerV2()


def get_backpressure_v2() -> BackpressureControllerV2:
    """Get global backpressure controller v2."""
    return backpressure_v2


# ═══════════════════════════════════════════════════════════════════════════
# DECORATORS
# ═══════════════════════════════════════════════════════════════════════════

def with_backpressure(priority: Priority, item_type: str = "task"):
    """
    Decorator to apply backpressure to a function.
    
    Usage:
        @with_backpressure(Priority.MEDIUM, "signal")
        async def process_signal(signal_data):
            # Process signal
            pass
        
        # If load is high, function may be skipped
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            if not backpressure_v2.can_accept(priority, item_type):
                logger.debug(f"[BackpressureV2] Dropping {item_type} with {priority.name} priority")
                return None
            
            # Apply throttling
            async with backpressure_v2.throttled_execution():
                return await func(*args, **kwargs)
        
        return wrapper
    return decorator


def strategy_admission_control():
    """
    Decorator for strategy start functions.
    
    Usage:
        @strategy_admission_control()
        async def start_strategy(strategy_id, config):
            # Start strategy
            pass
    """
    def decorator(func):
        async def wrapper(strategy_id: str, *args, **kwargs):
            if not backpressure_v2.can_start_strategy(strategy_id):
                logger.warning(f"[BackpressureV2] Strategy {strategy_id} rejected due to load")
                raise StrategyBlockedError(f"Strategy {strategy_id} blocked due to system load")
            
            return await func(strategy_id, *args, **kwargs)
        
        return wrapper
    return decorator


class StrategyBlockedError(Exception):
    """Raised when strategy is blocked due to backpressure."""
    pass


# ═══════════════════════════════════════════════════════════════════════════
# INTEGRATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════

class DAGTaskThrottler:
    """Helper to throttle DAG task execution."""
    
    def __init__(self, backpressure: Optional[BackpressureControllerV2] = None):
        self.backpressure = backpressure or backpressure_v2
    
    async def submit_task(self, task: Any, priority: Priority = Priority.MEDIUM) -> bool:
        """Submit DAG task with backpressure."""
        if not self.backpressure.can_accept(priority, "dag_task"):
            logger.debug(f"[DAGThrottler] Task dropped: {priority.name}")
            return False
        
        # Apply throttling delay
        async with self.backpressure.throttled_execution():
            # Task would be submitted to queue here
            return True


class SignalLoadShedder:
    """Helper to shed signal processing load."""
    
    def __init__(self, backpressure: Optional[BackpressureControllerV2] = None):
        self.backpressure = backpressure or backpressure_v2
    
    def should_process(self, signal_type: str, priority: Priority) -> bool:
        """Check if signal should be processed."""
        can_accept = self.backpressure.can_accept(priority, "signal")
        
        if not can_accept:
            logger.debug(f"[SignalShedder] Dropping {signal_type} signal: {priority.name}")
        
        return can_accept


class StrategyAdmissionController:
    """Helper to control strategy admission."""
    
    def __init__(self, backpressure: Optional[BackpressureControllerV2] = None):
        self.backpressure = backpressure or backpressure_v2
    
    def can_start(self, strategy_id: str) -> bool:
        """Check if strategy can be started."""
        return self.backpressure.can_start_strategy(strategy_id)
    
    def validate_strategy_config(self, config: Dict[str, Any]) -> tuple[bool, str]:
        """
        Validate strategy configuration for current load.
        
        Returns:
            (is_valid, reason)
        """
        level = self.backpressure.get_current_level()
        
        if level == LoadLevel.EMERGENCY:
            return False, "System in emergency mode - no new strategies allowed"
        
        if level == LoadLevel.CRITICAL:
            # Only allow simple strategies
            complexity = config.get("complexity", "medium")
            if complexity == "high":
                return False, "High complexity strategies blocked during critical load"
        
        return True, "OK"
