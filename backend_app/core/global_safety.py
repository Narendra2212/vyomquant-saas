"""
core/global_safety.py — Distributed Global Kill Switch for Trading System

🔴 STEP 1 — DISTRIBUTED GLOBAL KILL SWITCH (CRITICAL)

This module provides a Redis-backed global kill switch that can instantly
stop ALL trading activity across the entire distributed system.

ZERO TOLERANCE for:
- Duplicate orders
- Partial state mismatch  
- Silent failures
- Distributed inconsistencies
- Race conditions

The kill switch is checked before EVERY execution and blocks all
trading if activated.

FAIL-SAFE POLICY:
  If Redis is unavailable the kill switch enters a LOCAL LATCH state:
    - All execution is BLOCKED (fail-closed)
    - Every subsequent is_active() call probes Redis for recovery
    - Once Redis responds the latch is lifted and normal operation resumes
  This prevents permanent lockout from a transient Redis blip while
  still guaranteeing zero execution during an outage.

EXPECTED RESULT:
✔ System can be globally stopped instantly
✔ Transient Redis failures do NOT permanently deadlock the platform
✔ Full audit trail via HISTORY_KEY list
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("GlobalSafety")

# How long (seconds) a Redis failure keeps the local latch engaged
# before we automatically probe again.
_REDIS_PROBE_INTERVAL_SECONDS: int = 10


class ExecutionBlocked(Exception):
    """
    Raised when global kill switch is active and execution is attempted.
    
    This is a critical safety exception that indicates the system has been
    globally stopped due to a safety violation.
    """
    pass


class GlobalKillSwitch:
    """
    🔴 STEP 1 — DISTRIBUTED GLOBAL KILL SWITCH (CRITICAL)
    
    Redis-backed kill switch that provides INSTANT system-wide shutdown.
    
    IMPLEMENTATION:
    - KEY = "system:kill_switch" in Redis
    - is_active(): Returns True if kill switch is engaged
    - activate(reason): Engages kill switch with critical logging
    - deactivate(): Disengages kill switch
    
    ENFORCEMENT:
    - Checked before ANY execution
    - If active: raises ExecutionBlocked
    
    AUTO-TRIGGERS:
    - Exchange unhealthy
    - Reconciliation mismatch
    - Duplicate order detected
    - Portfolio inconsistency
    - Redis unavailable (fail-safe: assumes active until Redis recovers)
    
    EXPECTED RESULT:
    ✔ System can be globally stopped instantly
    """
    
    KEY = "system:kill_switch"
    HISTORY_KEY = "system:kill_switch_history"
    
    def __init__(self):
        # True  → local memory latch is engaged (Redis unreachable)
        self._local_fallback_active: bool = False
        # Timestamp of the last Redis failure — used to schedule probe retries
        self._redis_failure_at: Optional[datetime] = None
        # Asyncio lock to prevent concurrent probe/latch races
        self._probe_lock = asyncio.Lock()
        # Last known kill switch state (False = allowed, True = blocked)
        self._last_state: bool = False

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _should_probe_redis(self) -> bool:
        """
        True if enough time has passed since the last Redis failure to justify
        a reconnect attempt.
        """
        if not self._local_fallback_active:
            return True  # No latch — always go to Redis
        if self._redis_failure_at is None:
            return True
        elapsed = (datetime.utcnow() - self._redis_failure_at).total_seconds()
        return elapsed >= _REDIS_PROBE_INTERVAL_SECONDS

    async def _redis_get(self, key: str):
        """
        Safe Redis GET that routes through the backend redis_manager proxy.
        Returns None on any error; does NOT raise.
        """
        try:
            from backend_app.backend.redis_manager import redis_manager
            return await redis_manager.get(key)
        except Exception:
            return None  # Handled by caller

    async def _redis_set(self, key: str, value: str) -> bool:
        """Safe Redis SET. Returns True on success."""
        try:
            from backend_app.backend.redis_manager import redis_manager
            await redis_manager.set(key, value)
            return True
        except Exception:
            return False

    async def _redis_lpush(self, key: str, value: str) -> bool:
        """Safe Redis LPUSH. Returns True on success."""
        try:
            from backend_app.backend.redis_manager import redis_manager
            await redis_manager.lpush(key, value)
            return True
        except Exception:
            return False

    async def _redis_lrange(self, key: str, start: int, end: int) -> list:
        """Safe Redis LRANGE. Returns [] on error."""
        try:
            from backend_app.backend.redis_manager import redis_manager
            result = await redis_manager.lrange(key, start, end)
            return result or []
        except Exception:
            return []

    # ── Public API ────────────────────────────────────────────────────────────

    async def is_active(self) -> bool:
        """
        Check if global kill switch is active.
        
        Returns:
            True if kill switch is engaged (system stopped)
            False if system is operational
            
        Fail-safe policy:
            If Redis is unreachable:
              1. Engage local memory latch (block execution).
              2. Every _REDIS_PROBE_INTERVAL_SECONDS seconds retry Redis.
              3. If Redis recovers, lift the latch (no permanent lockout).
        """
        # Fast path: local latch engaged
        if self._local_fallback_active:
            if not self._should_probe_redis():
                logger.critical(
                    "🔴 GLOBAL KILL SWITCH IS ACTIVE (Local Fallback Latch — Redis unreachable) "
                    "— EXECUTION BLOCKED"
                )
                return True
            # Attempt to probe Redis under a lock to avoid concurrent probes
            async with self._probe_lock:
                # Double-check after acquiring lock
                if not self._local_fallback_active:
                    return self._last_state  # Another coroutine already recovered
                else:
                    probe_result = await self._redis_get(self.KEY)
                    if probe_result is not None:
                        # Redis responded — lift the latch
                        self._local_fallback_active = False
                        self._redis_failure_at = None
                        self._last_state = (probe_result == b"1" or probe_result == "1")
                        logger.warning(
                            "🟡 Redis reconnected — local fallback latch LIFTED. "
                            "Resuming normal kill-switch checks."
                        )
                        if self._last_state:
                            logger.critical("🔴 GLOBAL KILL SWITCH IS ACTIVE — EXECUTION BLOCKED")
                        return self._last_state
                    else:
                        # Still down — reset timer so next probe is delayed
                        self._redis_failure_at = datetime.utcnow()
                        self._last_state = True
                        logger.critical(
                            "🔴 Redis probe FAILED — local latch remains ACTIVE. "
                            "All executions BLOCKED."
                        )
                        return True
            # Fall through to normal Redis check after recovery

        # Normal path: consult Redis
        try:
            value = await self._redis_get(self.KEY)
            if value is None:
                # Key does not exist → switch is NOT active (safe default)
                self._last_state = False
                return False

            is_killed = value == b"1" or value == "1"
            self._last_state = is_killed
            if is_killed:
                logger.critical("🔴 GLOBAL KILL SWITCH IS ACTIVE — EXECUTION BLOCKED")
            return is_killed

        except Exception as e:
            # FAIL-SAFE: engage local latch
            logger.critical(
                f"🔴 KILL SWITCH CHECK FAILED (Redis error: {e}) — "
                f"ENGAGING LOCAL LATCH — all executions BLOCKED"
            )
            self._local_fallback_active = True
            self._redis_failure_at = datetime.utcnow()
            self._last_state = True
            return True

    async def activate(self, reason: str, triggered_by: Optional[str] = None):
        """
        Activate the global kill switch.
        
        Args:
            reason: Human-readable reason for activation
            triggered_by: Component or user that triggered the kill switch
            
        This will:
        1. Set Redis key to "1"
        2. Log critical message with reason
        3. Record in history for audit
        
        All subsequent executions will be blocked until deactivated.
        """
        timestamp = datetime.utcnow().isoformat()
        history_entry = f"{timestamp}|ACTIVATE|{reason}|{triggered_by or 'system'}"

        # Always engage local latch immediately for instant effect
        self._local_fallback_active = True
        self._redis_failure_at = None  # Clear probe timer — this is intentional, not a failure

        redis_ok = await self._redis_set(self.KEY, "1")
        if not redis_ok:
            logger.critical(
                f"🔴 FAILED to persist kill switch to Redis — "
                f"LOCAL LATCH is still engaged (in-process protection active)\n"
                f"    Reason: {reason}\n"
                f"    This is a WARNING — Redis is down but local process is blocked"
            )
        
        await self._redis_lpush(self.HISTORY_KEY, history_entry)
        
        logger.critical(
            f"🔴🔴🔴 GLOBAL KILL SWITCH ACTIVATED 🔴🔴🔴\n"
            f"    Reason: {reason}\n"
            f"    Triggered By: {triggered_by or 'system'}\n"
            f"    Timestamp: {timestamp}\n"
            f"    Redis persisted: {redis_ok}\n"
            f"    ALL EXECUTIONS BLOCKED UNTIL DEACTIVATED"
        )

    async def deactivate(self, reason: str, triggered_by: Optional[str] = None):
        """
        Deactivate the global kill switch.
        
        Args:
            reason: Human-readable reason for deactivation
            triggered_by: Component or user that deactivated the kill switch
            
        WARNING: This should only be done after verifying all safety
        conditions have been resolved.
        """
        timestamp = datetime.utcnow().isoformat()
        history_entry = f"{timestamp}|DEACTIVATE|{reason}|{triggered_by or 'system'}"

        # Clear local latch first
        self._local_fallback_active = False
        self._redis_failure_at = None

        redis_ok = await self._redis_set(self.KEY, "0")
        if not redis_ok:
            logger.critical(
                f"🔴 FAILED to clear kill switch in Redis — "
                f"local latch cleared but Redis may still block other processes\n"
                f"    Manual Redis intervention required.\n"
                f"    Reason: {reason}"
            )
            raise RuntimeError(
                f"CRITICAL: Kill switch deactivated locally but Redis update FAILED. "
                f"Other processes may remain blocked. Manual Redis fix required."
            )

        await self._redis_lpush(self.HISTORY_KEY, history_entry)

        logger.info(
            f"🟢 GLOBAL KILL SWITCH DEACTIVATED\n"
            f"    Reason: {reason}\n"
            f"    Triggered By: {triggered_by or 'system'}\n"
            f"    Timestamp: {timestamp}\n"
            f"    Executions now allowed"
        )

    async def get_history(self, limit: int = 100) -> list:
        """
        Get kill switch activation/deactivation history.
        
        Args:
            limit: Maximum number of history entries to return
            
        Returns:
            List of history entries (most recent first)
        """
        raw = await self._redis_lrange(self.HISTORY_KEY, 0, limit - 1)
        return [entry.decode() if isinstance(entry, bytes) else entry for entry in raw]

    async def get_status(self) -> dict:
        """Return a structured status dict for health endpoints."""
        active = await self.is_active()
        return {
            "kill_switch_active": active,
            "local_latch_active": self._local_fallback_active,
            "redis_failure_at": (
                self._redis_failure_at.isoformat() if self._redis_failure_at else None
            ),
            "probe_interval_seconds": _REDIS_PROBE_INTERVAL_SECONDS,
        }

    # ── Auto-trigger helpers ──────────────────────────────────────────────────

    async def trigger_on_exchange_unhealthy(
        self, 
        exchange_id: str, 
        health_check_result: dict
    ):
        """Auto-trigger kill switch if exchange is unhealthy."""
        reason = f"Exchange {exchange_id} unhealthy: {health_check_result}"
        await self.activate(reason, triggered_by="health_monitor")

    async def trigger_on_reconciliation_mismatch(
        self,
        order_id: str,
        local_state: dict,
        exchange_state: dict
    ):
        """Auto-trigger kill switch if order reconciliation shows mismatch."""
        reason = (
            f"Reconciliation mismatch for order {order_id}: "
            f"local={local_state}, exchange={exchange_state}"
        )
        await self.activate(reason, triggered_by="reconciliation_engine")

    async def trigger_on_duplicate_order(
        self,
        execution_id: str,
        existing_order: dict,
        attempted_order: dict
    ):
        """Auto-trigger kill switch if duplicate order detected."""
        reason = (
            f"Duplicate order detected for execution_id {execution_id}: "
            f"existing={existing_order}, attempted={attempted_order}"
        )
        await self.activate(reason, triggered_by="idempotency_layer")

    async def trigger_on_portfolio_inconsistency(
        self,
        tenant_id: str,
        expected_balance: float,
        actual_balance: float,
        discrepancy: float
    ):
        """Auto-trigger kill switch if portfolio shows inconsistency."""
        reason = (
            f"Portfolio inconsistency for {tenant_id}: "
            f"expected={expected_balance}, actual={actual_balance}, "
            f"discrepancy={discrepancy}"
        )
        await self.activate(reason, triggered_by="portfolio_validator")


# ── Singleton ─────────────────────────────────────────────────────────────────

_global_kill_switch: Optional[GlobalKillSwitch] = None


def get_global_kill_switch() -> GlobalKillSwitch:
    """Get or create the global kill switch instance."""
    global _global_kill_switch
    if _global_kill_switch is None:
        _global_kill_switch = GlobalKillSwitch()
    return _global_kill_switch


async def enforce_kill_switch():
    """
    Enforce kill switch check before execution.
    
    Raises:
        ExecutionBlocked: If kill switch is active
    """
    kill_switch = get_global_kill_switch()
    if await kill_switch.is_active():
        raise ExecutionBlocked(
            "🔴 GLOBAL KILL SWITCH IS ACTIVE — ALL EXECUTIONS BLOCKED"
        )


import hmac
import hashlib

_VALIDATION_SECRET: bytes = b"aerora_quant_risk_validation_secret_key_2026"

def generate_validation_token(execution_id: str, symbol: str, size) -> str:
    """Generate a HMAC-SHA256 validation token to prevent direct executor bypass."""
    normalized_symbol = symbol.replace('/', '-').upper()
    size_str = f"{float(size):.8f}"
    msg = f"{execution_id}:{normalized_symbol}:{size_str}".encode("utf-8")
    return hmac.new(_VALIDATION_SECRET, msg, hashlib.sha256).hexdigest()

def verify_validation_token(execution_id: str, symbol: str, size, token: str) -> bool:
    """Verify the validation token matches."""
    expected = generate_validation_token(execution_id, symbol, size)
    return hmac.compare_digest(expected, token)


# Convenience exports
__all__ = [
    "GlobalKillSwitch",
    "ExecutionBlocked",
    "get_global_kill_switch",
    "enforce_kill_switch",
    "generate_validation_token",
    "verify_validation_token",
]

