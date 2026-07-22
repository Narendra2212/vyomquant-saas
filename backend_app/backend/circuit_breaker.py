
"""
Circuit Breaker

STEP 7.6 — PRODUCTION HARDENING

Stops trading if:
- Too many errors
- Exchange down
- Latency too high

Circuit Breaker Pattern:
┌─────────────────────────────────────────────────────────────────┐
│  CLOSED  →  OPEN  →  HALF-OPEN  →  CLOSED                       │
│                                                                  │
│  CLOSED:    Normal operation, requests pass through             │
│                                                                  │
│  OPEN:      Failure threshold reached, reject all requests     │
│             (cooldown period)                                    │
│                                                                  │
│  HALF-OPEN: Testing if system recovered                        │
│             Allow limited requests                             │
│             If success → CLOSED                                 │
│             If fail → OPEN                                      │
└─────────────────────────────────────────────────────────────────┘

"""
import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"          # Normal - allow requests
    OPEN = "open"             # Failure - reject requests
    HALF_OPEN = "half_open"   # Testing - allow limited requests


class CircuitBreakerError(Exception):
    """Raised when circuit is open."""
    def __init__(self, message: str, circuit_name: str):
        super().__init__(message)
        self.circuit_name = circuit_name


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5              # Open after N failures
    success_threshold: int = 3              # Close after N successes in half-open
    timeout_seconds: int = 60               # Cooldown before half-open
    half_open_max_calls: int = 3            # Max calls in half-open state
    latency_threshold_ms: float = 5000.0    # Open if latency > 5s


class CircuitBreaker:
    """
    STEP 7.6: Circuit breaker for production safety.
    
    Prevents cascade failures by stopping requests when errors are high.
    
    Usage:
        circuit = CircuitBreaker(
            name="binance_api",
            config=CircuitBreakerConfig(
                failure_threshold=5,
                timeout_seconds=60
            )
        )
        
        # Use as decorator
        @circuit.protect
        async def place_order():
            return await exchange.place_order(...)
        
        # Or manual check
        if circuit.allow_request():
            try:
                result = await exchange.call()
                circuit.record_success()
            except Exception as e:
                circuit.record_failure()
        else:
            raise CircuitBreakerError("Circuit is OPEN")
    """
    
    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None
    ):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.last_state_change = time.time()
        
        # Half-open tracking
        self.half_open_calls = 0
        
        # Metrics
        self.total_calls = 0
        self.total_successes = 0
        self.total_failures = 0
        self.total_rejected = 0
        
        # Latency tracking
        self.recent_latencies: list = []
        self.max_latency_samples = 100
    
    def allow_request(self) -> bool:
        """
        Check if request should be allowed.
        
        Returns:
            True if request can proceed, False if rejected
        """
        now = time.time()
        
        if self.state == CircuitState.CLOSED:
            # Normal operation
            return True
        
        elif self.state == CircuitState.OPEN:
            # Check if timeout expired
            if self.last_failure_time:
                elapsed = now - self.last_failure_time
                if elapsed >= self.config.timeout_seconds:
                    logger.info(
                        f"Circuit {self.name}: Timeout expired, "
                        f"transitioning to HALF-OPEN"
                    )
                    self._transition_to(CircuitState.HALF_OPEN)
                    return True
            
            # Still in timeout
            return False
        
        elif self.state == CircuitState.HALF_OPEN:
            # Allow limited requests for testing
            if self.half_open_calls < self.config.half_open_max_calls:
                self.half_open_calls += 1
                return True
            else:
                return False
        
        return False
    
    def record_success(self, latency_ms: Optional[float] = None):
        """Record a successful call."""
        self.total_calls += 1
        self.total_successes += 1
        
        # Track latency
        if latency_ms:
            self.recent_latencies.append(latency_ms)
            if len(self.recent_latencies) > self.max_latency_samples:
                self.recent_latencies.pop(0)
        
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            
            if self.success_count >= self.config.success_threshold:
                logger.info(
                    f"Circuit {self.name}: Success threshold reached, "
                    f"transitioning to CLOSED"
                )
                self._transition_to(CircuitState.CLOSED)
    
    def record_failure(self, latency_ms: Optional[float] = None):
        """Record a failed call."""
        self.total_calls += 1
        self.total_failures += 1
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        # Track latency (even for failures)
        if latency_ms:
            self.recent_latencies.append(latency_ms)
            if len(self.recent_latencies) > self.max_latency_samples:
                self.recent_latencies.pop(0)
        
        # Check failure threshold
        if self.failure_count >= self.config.failure_threshold:
            if self.state != CircuitState.OPEN:
                logger.critical(
                    f"Circuit {self.name}: Failure threshold reached ({self.failure_count}), "
                    f"transitioning to OPEN - TRADING STOPPED"
                )
                self._transition_to(CircuitState.OPEN)
    
    def record_rejection(self):
        """Record a rejected call (circuit open)."""
        self.total_rejected += 1
    
    def protect(self, func: Callable) -> Callable:
        """
        Decorator to protect a function with circuit breaker.
        
        Usage:
            @circuit.protect
            async def place_order():
                return await exchange.place_order(...)
        """
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if not self.allow_request():
                self.record_rejection()
                raise CircuitBreakerError(
                    f"Circuit {self.name} is OPEN - request rejected",
                    self.name
                )
            
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                latency_ms = (time.time() - start_time) * 1000
                
                # Check latency threshold
                if latency_ms > self.config.latency_threshold_ms:
                    logger.warning(
                        f"Circuit {self.name}: High latency detected ({latency_ms:.0f}ms)"
                    )
                    self.record_failure(latency_ms)
                else:
                    self.record_success(latency_ms)
                
                return result
                
            except Exception:
                latency_ms = (time.time() - start_time) * 1000
                self.record_failure(latency_ms)
                raise
        
        return wrapper
    
    def _transition_to(self, new_state: CircuitState):
        """Transition to new state."""
        old_state = self.state
        self.state = new_state
        self.last_state_change = time.time()
        
        # Reset counters on state change
        if new_state == CircuitState.CLOSED:
            self.failure_count = 0
            self.success_count = 0
            self.half_open_calls = 0
        elif new_state == CircuitState.HALF_OPEN:
            self.half_open_calls = 0
            self.success_count = 0
        
        # Log state change
        logger.info(
            f"Circuit {self.name}: {old_state.value} → {new_state.value}"
        )
        
        # STEP 7.8: Send alert on OPEN
        if new_state == CircuitState.OPEN:
            self._send_circuit_open_alert()
    
    def _send_circuit_open_alert(self):
        """Send alert when circuit opens."""
        try:
            from backend_app.backend.alert_system import get_alert_system
            alert_system = get_alert_system()
            
            asyncio.create_task(alert_system.send_critical_alert(
                title=f"Circuit Breaker OPEN: {self.name}",
                message=f"Trading stopped due to excessive errors. "
                        f"Failures: {self.failure_count}, "
                        f"Timeout: {self.config.timeout_seconds}s",
                metadata={
                    "circuit_name": self.name,
                    "failure_count": self.failure_count,
                    "failure_threshold": self.config.failure_threshold,
                    "timeout_seconds": self.config.timeout_seconds,
                    "last_failure": datetime.fromtimestamp(
                        self.last_failure_time
                    ).isoformat() if self.last_failure_time else None
                }
            ))
        except Exception as e:
            logger.error(f"Failed to send circuit alert: {e}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get circuit breaker metrics."""
        avg_latency = (
            sum(self.recent_latencies) / len(self.recent_latencies)
            if self.recent_latencies else 0
        )
        
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "failure_threshold": self.config.failure_threshold,
            "success_threshold": self.config.success_threshold,
            "total_calls": self.total_calls,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
            "total_rejected": self.total_rejected,
            "success_rate": (
                self.total_successes / max(self.total_calls, 1) * 100
            ),
            "avg_latency_ms": avg_latency,
            "last_failure": datetime.fromtimestamp(
                self.last_failure_time
            ).isoformat() if self.last_failure_time else None,
            "time_in_state_seconds": time.time() - self.last_state_change
        }


class CircuitBreakerManager:
    """Manages multiple circuit breakers."""
    
    def __init__(self):
        self._circuits: Dict[str, CircuitBreaker] = {}
    
    def get_circuit(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None
    ) -> CircuitBreaker:
        """Get or create circuit breaker."""
        if name not in self._circuits:
            self._circuits[name] = CircuitBreaker(name, config)
        return self._circuits[name]
    
    def get_all_metrics(self) -> Dict[str, Any]:
        """Get metrics for all circuits."""
        return {
            name: circuit.get_metrics()
            for name, circuit in self._circuits.items()
        }
    
    def check_all_circuits(self) -> bool:
        """
        Check if any circuit is open.
        
        Returns:
            True if all circuits closed, False if any open
        """
        for name, circuit in self._circuits.items():
            if circuit.state == CircuitState.OPEN:
                return False
        return True


# Global instance
_circuit_manager: Optional[CircuitBreakerManager] = None


def get_circuit_breaker_manager() -> CircuitBreakerManager:
    """Get global circuit breaker manager."""
    global _circuit_manager
    if _circuit_manager is None:
        _circuit_manager = CircuitBreakerManager()
    return _circuit_manager


def get_circuit_breaker(
    name: str,
    config: Optional[CircuitBreakerConfig] = None
) -> CircuitBreaker:
    """Get a specific circuit breaker."""
    return get_circuit_breaker_manager().get_circuit(name, config)


# =============================================================================
# STEP 8.4 + 8.5: GLOBAL CIRCUIT BREAKER + EMERGENCY LIQUIDATION
# =============================================================================

@dataclass
class GlobalCircuitBreakerConfig:
    """Configuration for global capital protection circuit breaker."""
    daily_loss_threshold_pct: float = 5.0  # Stop if daily loss > 5%
    max_drawdown_threshold_pct: float = 10.0  # Stop if drawdown > 10%
    consecutive_failures_threshold: int = 5  # Stop after 5 consecutive failures
    
    # Cooldown periods (in seconds)
    daily_loss_cooldown: int = 86400  # 24 hours
    drawdown_cooldown: int = 3600  # 1 hour
    failures_cooldown: int = 300  # 5 minutes


class GlobalCircuitBreakerState(Enum):
    """States for the global circuit breaker."""
    NORMAL = "normal"
    DAILY_LIMIT_HIT = "daily_limit_hit"
    DRAWDOWN_LIMIT_HIT = "drawdown_limit_hit"
    FAILURE_LIMIT_HIT = "failure_limit_hit"
    MANUAL_STOP = "manual_stop"


class GlobalCircuitBreaker:
    """
    STEP 8.4 + 8.5: Global Circuit Breaker + Emergency Liquidation
    
    STEP 8.4: Capital Protection
    Protects capital by stopping ALL trading when:
    1. Daily loss > 5% → STOP ALL TRADING for 24 hours
    2. Drawdown > 10% → STOP SYSTEM for 1 hour
    3. Consecutive failures > 5 → STOP for 5 minutes
    
    STEP 8.5: Auto Position Liquidation (Emergency Exit)
    When circuit breaker triggers:
    1. Close all open positions
    2. Cancel all orders
    3. Freeze execution
    
    This is the FINAL SAFETY NET - no trades execute when this is open.
    
    Usage:
        from backend_app.backend.circuit_breaker import global_circuit_breaker
        
        # Check before any trade
        if not global_circuit_breaker.can_trade():
            logger.critical("🚫 GLOBAL CIRCUIT BREAKER OPEN - NO TRADING ALLOWED")
            return
        
        # Record trade result
        global_circuit_breaker.record_trade(pnl=100.0, starting_equity=10000.0)
        
        # Record failure
        global_circuit_breaker.record_failure()
        
        # Check status
        status = global_circuit_breaker.get_status()
        # Returns: {"state": "normal", "can_trade": True, ...}
    """
    
    def __init__(self, config: Optional[GlobalCircuitBreakerConfig] = None):
        self.config = config or GlobalCircuitBreakerConfig()
        
        # State management
        self.state = GlobalCircuitBreakerState.NORMAL
        self._lock = threading.Lock()
        
        # Daily tracking
        self.daily_starting_equity: Optional[float] = None
        self.current_equity: Optional[float] = None
        self.daily_pnl: float = 0.0
        
        # Drawdown tracking
        self.peak_equity: Optional[float] = None
        self.max_drawdown_pct: float = 0.0
        
        # Failure tracking
        self.consecutive_failures: int = 0
        self.last_failure_time: Optional[float] = None
        
        # Circuit open tracking
        self.circuit_opened_at: Optional[float] = None
        self.circuit_reason: Optional[str] = None
        self.cooldown_until: Optional[float] = None
        
        # Trade history for today
        self.trades_today: int = 0
        self.trading_start_date: Optional[str] = None
        
        # STEP 8.5: Emergency liquidation callbacks
        # Callbacks registered to execute when circuit breaker opens
        self._liquidation_callbacks: Dict[str, Callable[[str, str], Any]] = {}
        self._last_liquidation_results: Optional[Dict[str, Any]] = None
        
        logger.info("STEP 8.4 + 8.5: Global Circuit Breaker initialized with emergency liquidation")
    
    def _reset_daily_tracking(self):
        """Reset daily tracking for new trading day."""
        today = datetime.now().strftime("%Y-%m-%d")
        
        if self.trading_start_date != today:
            logger.info(f"STEP 8.4: New trading day - resetting daily tracking ({today})")
            self.daily_pnl = 0.0
            self.trades_today = 0
            self.trading_start_date = today
            
            # If we were stopped for daily loss, check if we can resume
            if self.state == GlobalCircuitBreakerState.DAILY_LIMIT_HIT:
                self._check_cooldown_expired()
    
    def _check_cooldown_expired(self) -> bool:
        """Check if cooldown period has expired and reset if so."""
        if self.cooldown_until is None:
            return True
        
        if time.time() >= self.cooldown_until:
            logger.warning(
                f"STEP 8.4: Cooldown expired - resetting circuit breaker | "
                f"Previous state: {self.state.value} | Reason: {self.circuit_reason}"
            )
            self._reset_circuit()
            return True
        
        return False
    
    def _reset_circuit(self):
        """Reset circuit breaker to normal state."""
        with self._lock:
            previous_state = self.state
            self.state = GlobalCircuitBreakerState.NORMAL
            self.circuit_opened_at = None
            self.circuit_reason = None
            self.cooldown_until = None
            
            # Reset failure count on recovery
            if previous_state == GlobalCircuitBreakerState.FAILURE_LIMIT_HIT:
                self.consecutive_failures = 0
            
            logger.critical(
                f"🟢 STEP 8.4: GLOBAL CIRCUIT BREAKER RESET | "
                f"Previous: {previous_state.value} | Now: NORMAL"
            )
    
    def _open_circuit(self, state: GlobalCircuitBreakerState, reason: str, cooldown_seconds: int):
        """Open the circuit breaker with specified state and cooldown."""
        with self._lock:
            if self.state != GlobalCircuitBreakerState.NORMAL:
                # Already open - don't change
                return
            
            self.state = state
            self.circuit_opened_at = time.time()
            self.circuit_reason = reason
            self.cooldown_until = time.time() + cooldown_seconds
            
            logger.critical(
                f"🔴 STEP 8.4: GLOBAL CIRCUIT BREAKER OPEN | "
                f"State: {state.value} | Reason: {reason} | "
                f"Cooldown: {cooldown_seconds}s until {datetime.fromtimestamp(self.cooldown_until)}"
            )
            
            # STEP 8.5: TRIGGER EMERGENCY LIQUIDATION
            # Close all positions, cancel all orders, freeze execution
            self._trigger_emergency_liquidation(state, reason)
    
    def _trigger_emergency_liquidation(
        self,
        state: GlobalCircuitBreakerState,
        reason: str
    ):
        """
        STEP 8.5: Auto Position Liquidation - EMERGENCY EXIT
        
        When circuit breaker triggered:
        1. Close all open positions
        2. Cancel all orders
        3. Freeze execution
        
        This is called automatically when the circuit opens.
        Uses callbacks registered via register_liquidation_callback().
        """
        logger.critical(
            f"🚨 STEP 8.5: EMERGENCY LIQUIDATION TRIGGERED | "
            f"State: {state.value} | Reason: {reason}"
        )
        
        # Execute all registered liquidation callbacks
        liquidation_results = []
        for callback_name, callback in self._liquidation_callbacks.items():
            try:
                logger.critical(
                    f"🚨 STEP 8.5: Executing liquidation callback '{callback_name}'"
                )
                result = callback(state=state.value, reason=reason)
                liquidation_results.append({
                    "callback": callback_name,
                    "success": True,
                    "result": result,
                })
                logger.critical(
                    f"✅ STEP 8.5: Liquidation callback '{callback_name}' completed"
                )
            except Exception as e:
                logger.error(
                    f"❌ STEP 8.5: Liquidation callback '{callback_name}' failed: {e}"
                )
                liquidation_results.append({
                    "callback": callback_name,
                    "success": False,
                    "error": str(e),
                })
        
        # Log summary
        success_count = sum(1 for r in liquidation_results if r["success"])
        total_count = len(liquidation_results)
        
        logger.critical(
            f"🚨 STEP 8.5: EMERGENCY LIQUIDATION COMPLETE | "
            f"Success: {success_count}/{total_count} | "
            f"Execution FROZEN until cooldown expires"
        )
        
        # Store liquidation results for audit
        self._last_liquidation_results = {
            "timestamp": datetime.utcnow().isoformat(),
            "trigger_state": state.value,
            "trigger_reason": reason,
            "results": liquidation_results,
            "total_callbacks": total_count,
            "successful_callbacks": success_count,
        }
    
    def register_liquidation_callback(
        self,
        name: str,
        callback: Callable[[str, str], Any]
    ):
        """
        Register a callback to execute during emergency liquidation.
        
        Args:
            name: Unique name for this callback
            callback: Function(state: str, reason: str) -> Any
                Called when circuit breaker opens
        
        Usage:
            def my_liquidation_handler(state: str, reason: str):
                # Close all positions
                portfolio.close_all_positions()
                # Cancel all orders
                order_manager.cancel_all_orders()
                return {"positions_closed": 5, "orders_cancelled": 3}
            
            global_circuit_breaker().register_liquidation_callback(
                "portfolio_liquidation",
                my_liquidation_handler
            )
        """
        self._liquidation_callbacks[name] = callback
        logger.info(f"STEP 8.5: Registered liquidation callback '{name}'")
    
    def unregister_liquidation_callback(self, name: str):
        """Unregister a liquidation callback."""
        if name in self._liquidation_callbacks:
            del self._liquidation_callbacks[name]
            logger.info(f"STEP 8.5: Unregistered liquidation callback '{name}'")
    
    def get_last_liquidation_results(self) -> Optional[Dict[str, Any]]:
        """Get results from the last emergency liquidation."""
        return getattr(self, '_last_liquidation_results', None)
    
    def can_trade(self) -> bool:
        """
        Check if trading is allowed.
        
        Returns:
            True if circuit is closed (NORMAL state)
            False if circuit is open (any other state)
        """
        # Check if cooldown expired
        if not self._check_cooldown_expired():
            return False
        
        # Reset daily tracking if needed
        self._reset_daily_tracking()
        
        with self._lock:
            can_trade = self.state == GlobalCircuitBreakerState.NORMAL
            
            if not can_trade:
                remaining = self.cooldown_until - time.time() if self.cooldown_until else 0
                logger.critical(
                    f"🚫 STEP 8.4: TRADING BLOCKED | State: {self.state.value} | "
                    f"Reason: {self.circuit_reason} | Cooldown: {remaining:.0f}s remaining"
                )
            
            return can_trade
    
    def record_trade(self, pnl: float, current_equity: float, starting_equity: Optional[float] = None):
        """
        Record a completed trade and update equity tracking.
        
        Args:
            pnl: Profit/loss from the trade
            current_equity: Current portfolio equity
            starting_equity: Starting equity for the day (optional, auto-tracked)
        """
        with self._lock:
            # Reset daily tracking if needed
            self._reset_daily_tracking()
            
            # Initialize starting equity if not set
            if self.daily_starting_equity is None:
                if starting_equity:
                    self.daily_starting_equity = starting_equity
                    self.peak_equity = starting_equity
                else:
                    self.daily_starting_equity = current_equity - pnl
                    self.peak_equity = self.daily_starting_equity
            
            # Update tracking
            self.current_equity = current_equity
            self.daily_pnl += pnl
            self.trades_today += 1
            
            # Update peak equity and drawdown
            if current_equity > self.peak_equity:
                self.peak_equity = current_equity
            
            current_drawdown = (self.peak_equity - current_equity) / self.peak_equity * 100
            self.max_drawdown_pct = max(self.max_drawdown_pct, current_drawdown)
            
            # Reset consecutive failures on successful trade
            if pnl != 0:  # Any completed trade (win or loss) resets failure counter
                self.consecutive_failures = 0
            
            # Check daily loss limit
            daily_loss_pct = abs(self.daily_pnl) / self.daily_starting_equity * 100 if self.daily_pnl < 0 else 0
            
            if daily_loss_pct > self.config.daily_loss_threshold_pct:
                self._open_circuit(
                    GlobalCircuitBreakerState.DAILY_LIMIT_HIT,
                    f"Daily loss {daily_loss_pct:.2f}% exceeds threshold {self.config.daily_loss_threshold_pct}%",
                    self.config.daily_loss_cooldown
                )
                return
            
            # Check drawdown limit
            if self.max_drawdown_pct > self.config.max_drawdown_threshold_pct:
                self._open_circuit(
                    GlobalCircuitBreakerState.DRAWDOWN_LIMIT_HIT,
                    f"Max drawdown {self.max_drawdown_pct:.2f}% exceeds threshold {self.config.max_drawdown_threshold_pct}%",
                    self.config.drawdown_cooldown
                )
                return
            
            logger.debug(
                f"STEP 8.4: Trade recorded | PnL: {pnl:.2f} | Equity: {current_equity:.2f} | "
                f"Daily PnL: {self.daily_pnl:.2f} | Drawdown: {self.max_drawdown_pct:.2f}%"
            )
    
    def record_failure(self, failure_reason: str = ""):
        """
        Record a trade failure.
        
        Args:
            failure_reason: Optional reason for the failure
        """
        with self._lock:
            self.consecutive_failures += 1
            self.last_failure_time = time.time()
            
            logger.warning(
                f"STEP 8.4: Trade failure recorded | Count: {self.consecutive_failures}/"
                f"{self.config.consecutive_failures_threshold} | Reason: {failure_reason}"
            )
            
            # Check failure threshold
            if self.consecutive_failures >= self.config.consecutive_failures_threshold:
                self._open_circuit(
                    GlobalCircuitBreakerState.FAILURE_LIMIT_HIT,
                    f"{self.consecutive_failures} consecutive failures (threshold: {self.config.consecutive_failures_threshold})",
                    self.config.failures_cooldown
                )
    
    def manual_stop(self, reason: str = "Manual stop"):
        """Manually stop all trading (admin override)."""
        with self._lock:
            if self.state != GlobalCircuitBreakerState.NORMAL:
                logger.warning(f"STEP 8.4: Manual stop called but circuit already open ({self.state.value})")
                return
            
            self.state = GlobalCircuitBreakerState.MANUAL_STOP
            self.circuit_opened_at = time.time()
            self.circuit_reason = reason
            # No automatic cooldown for manual stop - requires manual reset
            self.cooldown_until = None
            
            logger.critical(
                f"🛑 STEP 8.4: MANUAL STOP ACTIVATED | Reason: {reason} | "
                f"Trading HALTED until manually reset"
            )
    
    def manual_reset(self, admin_key: str) -> bool:
        """
        Manually reset the circuit breaker (requires admin key).
        
        Args:
            admin_key: Simple admin verification (in production, use proper auth)
            
        Returns:
            True if reset successful, False otherwise
        """
        # Simple check - in production, verify proper admin credentials
        if admin_key != "reset_circuit_8.4":
            logger.error("STEP 8.4: Manual reset failed - invalid admin key")
            return False
        
        with self._lock:
            previous_state = self.state
            self._reset_circuit()
            
            logger.critical(
                f"🟢 STEP 8.4: MANUAL RESET SUCCESSFUL | Previous: {previous_state.value}"
            )
            return True
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current circuit breaker status.
        
        Returns:
            Dict with full status information
        """
        with self._lock:
            remaining_cooldown = 0.0
            if self.cooldown_until:
                remaining_cooldown = max(0, self.cooldown_until - time.time())
            
            daily_loss_pct = 0.0
            if self.daily_pnl < 0 and self.daily_starting_equity:
                daily_loss_pct = abs(self.daily_pnl) / self.daily_starting_equity * 100
            
            return {
                "state": self.state.value,
                "can_trade": self.state == GlobalCircuitBreakerState.NORMAL,
                "circuit_reason": self.circuit_reason,
                "circuit_opened_at": datetime.fromtimestamp(self.circuit_opened_at).isoformat() if self.circuit_opened_at else None,
                "cooldown_seconds_remaining": int(remaining_cooldown),
                "daily_metrics": {
                    "starting_equity": self.daily_starting_equity,
                    "current_equity": self.current_equity,
                    "daily_pnl": self.daily_pnl,
                    "daily_loss_pct": daily_loss_pct,
                    "trades_today": self.trades_today,
                },
                "drawdown_metrics": {
                    "peak_equity": self.peak_equity,
                    "max_drawdown_pct": self.max_drawdown_pct,
                    "threshold_pct": self.config.max_drawdown_threshold_pct,
                },
                "failure_metrics": {
                    "consecutive_failures": self.consecutive_failures,
                    "threshold": self.config.consecutive_failures_threshold,
                    "last_failure": datetime.fromtimestamp(self.last_failure_time).isoformat() if self.last_failure_time else None,
                },
                "thresholds": {
                    "daily_loss_pct": self.config.daily_loss_threshold_pct,
                    "max_drawdown_pct": self.config.max_drawdown_threshold_pct,
                    "consecutive_failures": self.config.consecutive_failures_threshold,
                },
                "liquidation": {
                    "registered_callbacks": list(self._liquidation_callbacks.keys()),
                    "last_liquidation": self._last_liquidation_results,
                },
            }


# Global instance - SINGLETON
_global_circuit_breaker: Optional[GlobalCircuitBreaker] = None
_global_cb_lock = threading.Lock()


def get_global_circuit_breaker(
    config: Optional[GlobalCircuitBreakerConfig] = None
) -> GlobalCircuitBreaker:
    """Get the global circuit breaker singleton."""
    global _global_circuit_breaker
    
    with _global_cb_lock:
        if _global_circuit_breaker is None:
            _global_circuit_breaker = GlobalCircuitBreaker(config)
        return _global_circuit_breaker


# Convenience singleton
_global_circuit_breaker_instance: Optional[GlobalCircuitBreaker] = None


def global_circuit_breaker() -> GlobalCircuitBreaker:
    """
    Get the global circuit breaker instance (lazy initialization).
    
    Usage:
        from backend_app.backend.circuit_breaker import global_circuit_breaker
        
        if global_circuit_breaker().can_trade():
            execute_trade()
        else:
            logger.critical("Trading halted by global circuit breaker")
    """
    global _global_circuit_breaker_instance
    
    if _global_circuit_breaker_instance is None:
        _global_circuit_breaker_instance = GlobalCircuitBreaker()
    
    return _global_circuit_breaker_instance

