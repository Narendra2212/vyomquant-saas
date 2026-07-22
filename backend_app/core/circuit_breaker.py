"""
core/circuit_breaker.py — CIRCUIT BREAKER PATTERN FOR EXCHANGE FAILURES

STEP 3: PREVENT CASCADING FAILURES

GOAL: Block execution to unhealthy exchanges to prevent system overload
and cascading failures.

STATES:
- CLOSED: Normal operation, requests pass through
- OPEN: Exchange unhealthy, all requests blocked
- HALF_OPEN: Testing if exchange recovered

USAGE:
    from backend_app.core.circuit_breaker import ExchangeCircuitBreaker
    
    breaker = ExchangeCircuitBreaker.get_breaker("binance")
    
    if breaker.can_execute():
        try:
            result = await execute_order()
            breaker.record_success()
        except ExchangeError:
            breaker.record_failure()
    else:
        raise Exception("Exchange circuit breaker is OPEN")
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger("CircuitBreaker")


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is OPEN and execution is blocked."""
    pass


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5           # Failures to open circuit
    recovery_timeout: float = 30.0      # Seconds before half-open
    half_open_max_calls: int = 3        # Test calls in half-open
    success_threshold: int = 2          # Successes to close circuit


class ExchangeCircuitBreaker:
    """
    Circuit breaker for exchange connections.
    
    Prevents cascading failures by blocking requests to unhealthy exchanges.
    """
    
    _instances: Dict[str, "ExchangeCircuitBreaker"] = {}
    _lock = asyncio.Lock()
    
    def __init__(self, exchange_id: str, config: Optional[CircuitBreakerConfig] = None):
        self.exchange_id = exchange_id
        self.config = config or CircuitBreakerConfig()
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.half_open_calls = 0
        
        # Instance-level async lock for atomic operations
        self._instance_lock = asyncio.Lock()
        
        logger.info(f"Circuit breaker initialized for {exchange_id}")

    @property
    def failure_threshold(self) -> int:
        return self.config.failure_threshold

    @failure_threshold.setter
    def failure_threshold(self, value: int):
        self.config.failure_threshold = value

    @property
    def recovery_timeout(self) -> float:
        return self.config.recovery_timeout

    @recovery_timeout.setter
    def recovery_timeout(self, value: float):
        self.config.recovery_timeout = value

    @property
    def half_open_max_calls(self) -> int:
        return self.config.half_open_max_calls

    @half_open_max_calls.setter
    def half_open_max_calls(self, value: int):
        self.config.half_open_max_calls = value

    @property
    def success_threshold(self) -> int:
        return self.config.success_threshold

    @success_threshold.setter
    def success_threshold(self, value: int):
        self.config.success_threshold = value
    
    @classmethod
    async def get_breaker_async(cls, exchange_id: str) -> "ExchangeCircuitBreaker":
        """Get or create circuit breaker for exchange (async-safe)."""
        async with cls._lock:
            if exchange_id not in cls._instances:
                cls._instances[exchange_id] = ExchangeCircuitBreaker(exchange_id)
            return cls._instances[exchange_id]
    
    @classmethod
    def get_breaker(cls, exchange_id: str) -> "ExchangeCircuitBreaker":
        """Get or create circuit breaker for exchange (sync fallback)."""
        # For backwards compatibility - use async version when possible
        if exchange_id not in cls._instances:
            # Create without lock (risk accepted for sync compatibility)
            cls._instances[exchange_id] = ExchangeCircuitBreaker(exchange_id)
        return cls._instances[exchange_id]
    
    async def can_execute_async(self) -> bool:
        """Check if execution is allowed (async-safe with lock)."""
        async with self._instance_lock:
            return await self._can_execute_unlocked()
    
    def can_execute(self) -> bool:
        """Check if execution is allowed (sync version)."""
        # Note: This is NOT thread-safe without external locking
        # Use can_execute_async() for proper async safety
        return self._can_execute_unlocked_sync()
    
    async def _can_execute_unlocked(self) -> bool:
        """Internal check - must be called with lock held."""
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.OPEN:
            # Check if recovery timeout passed
            if self.last_failure_time:
                elapsed = time.time() - self.last_failure_time
                if elapsed >= self.config.recovery_timeout:
                    logger.info(
                        f"Circuit breaker for {self.exchange_id} entering HALF_OPEN "
                        f"after {elapsed:.1f}s"
                    )
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
                    self.success_count = 0
                    return True
            return False
        
        if self.state == CircuitState.HALF_OPEN:
            # Allow limited test calls
            if self.half_open_calls < self.config.half_open_max_calls:
                self.half_open_calls += 1
                return True
            return False
        
        return True
    
    def _can_execute_unlocked_sync(self) -> bool:
        """Sync version of check - must be called with lock held."""
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.OPEN:
            if self.last_failure_time:
                elapsed = time.time() - self.last_failure_time
                if elapsed >= self.config.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
                    self.success_count = 0
                    return True
            return False
        
        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_calls < self.config.half_open_max_calls:
                self.half_open_calls += 1
                return True
            return False
        
        return True
    
    async def execute_with_breaker(self, coro, *args, **kwargs):
        """
        STEP 1: ATOMIC EXECUTION PATTERN
        
        Prevents race condition between can_execute() and record_*():
        
        PATTERN:
            async with breaker.lock():
                if not breaker.can_execute():
                    block
                result = await execute()
                breaker.record_success() / record_failure()
        
        This ensures state doesn't change between check and execution.
        """
        async with self._instance_lock:
            if not await self._can_execute_unlocked():
                raise CircuitBreakerOpenError(
                    f"Circuit breaker OPEN for {self.exchange_id}"
                )
            
            try:
                result = await coro(*args, **kwargs)
                await self._record_success_unlocked()
                return result
            except Exception:
                await self._record_failure_unlocked()
                raise
    
    async def record_success_async(self):
        """Record successful execution (async-safe)."""
        async with self._instance_lock:
            await self._record_success_unlocked()
    
    async def _record_success_unlocked(self):
        """Internal success record - must be called with lock held."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                logger.info(
                    f"Circuit breaker for {self.exchange_id} CLOSED "
                    f"({self.success_count} consecutive successes)"
                )
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.half_open_calls = 0
        elif self.state == CircuitState.CLOSED:
            self.failure_count = max(0, self.failure_count - 1)
    
    def record_success(self):
        """Record successful execution (sync - use async version when possible)."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.half_open_calls = 0
        elif self.state == CircuitState.CLOSED:
            self.failure_count = max(0, self.failure_count - 1)
    
    async def record_failure_async(self):
        """Record failed execution (async-safe)."""
        async with self._instance_lock:
            await self._record_failure_unlocked()
    
    async def _record_failure_unlocked(self):
        """Internal failure record - must be called with lock held."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.HALF_OPEN:
            # Immediately reopen on failure in half-open
            logger.warning(
                f"Circuit breaker for {self.exchange_id} re-OPENED "
                f"(failure in HALF_OPEN state)"
            )
            self.state = CircuitState.OPEN
            self.half_open_calls = 0
        
        elif self.state == CircuitState.CLOSED:
            if self.failure_count >= self.config.failure_threshold:
                logger.error(
                    f"Circuit breaker for {self.exchange_id} OPENED "
                    f"({self.failure_count} failures in a row)"
                )
                self.state = CircuitState.OPEN
    
    def record_failure(self):
        """Record failed execution (sync - use async version when possible)."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self.half_open_calls = 0
        elif self.state == CircuitState.CLOSED:
            if self.failure_count >= self.config.failure_threshold:
                self.state = CircuitState.OPEN
    
    def get_state(self) -> CircuitState:
        """Get current circuit state."""
        return self.state
    
    def reset(self):
        """Manually reset circuit breaker (for testing/emergencies)."""
        logger.warning(f"Circuit breaker for {self.exchange_id} manually reset")
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.half_open_calls = 0
        self.last_failure_time = None


# Global circuit breaker registry
def get_exchange_breaker(exchange_id: str) -> ExchangeCircuitBreaker:
    """Get circuit breaker for exchange (convenience function)."""
    return ExchangeCircuitBreaker.get_breaker(exchange_id)


def get_all_breaker_states() -> Dict[str, str]:
    """Get states of all circuit breakers for monitoring."""
    return {
        exchange_id: breaker.state.value
        for exchange_id, breaker in ExchangeCircuitBreaker._instances.items()
    }
