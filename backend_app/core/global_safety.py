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

─────────────────────────────────────────────────────────────────────────────
KILL-01 / TRADE-02 (Phase 8). TWO DEFECTS THAT MADE THE POLICY ABOVE UNTRUE.
─────────────────────────────────────────────────────────────────────────────
The policy stated above was the INTENT. Measured against the code, neither half
of it held, and both failures came from the same root cause: `_redis_get`
returned ``None`` for two completely different situations — "Redis answered and
the key is not set" and "Redis could not be reached at all".

  1. FAIL-OPEN ON AN OUTAGE (the serious one). `_redis_get` caught every
     exception and returned ``None``, so the fail-closed ``except`` handler at
     the bottom of ``is_active()`` was UNREACHABLE for a connection error.
     Control landed on ``if value is None: return False`` instead, and a Redis
     outage reported the kill switch as NOT ACTIVE. A triggered switch stopped
     blocking the moment Redis went away — the opposite of the documented
     fail-closed guarantee, on the one control whose entire purpose is to stop
     trading.

  2. PERMANENT LOCKOUT, the mirror image. Once the latch WAS engaged, the
     recovery probe lifted it only ``if probe_result is not None``. The normal
     resting state of a healthy system is "key absent", which is ``None``. So a
     fully recovered Redis with no kill-switch key set could never lift the
     latch, and the module's promise that transient failures "do NOT permanently
     deadlock the platform" was also false.

  Both are fixed by making the three outcomes distinguishable: a value, a
  genuine absence (``None``), or ``RedisUnavailable`` raised. Nothing else about
  the latch design changed.

  3. ASYMMETRIC TTL. ``activate()`` wrote the key with ``ex=86400`` while
     ``deactivate()`` wrote it with no expiry. "Stopped" therefore decayed back
     to "trading permitted" after 24 hours on its own, while "permitted" was
     durable forever — a safety control that drifts toward the unsafe state
     unattended. The active key and its metadata now carry no TTL, matching this
     repository's own convention for state keys
     (``core/redis_cluster.py``: ``RedisDatabase.STATE: 0,  # No TTL``).
     A kill switch is cleared deliberately, by ``deactivate()``, or not at all.

REGRESSION: tests/test_trade02_kill_switch_fails_closed.py
"""
import asyncio
import hashlib
import hmac
import logging
from datetime import datetime
from typing import Iterable, Optional

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


class RedisUnavailable(Exception):
    """The authoritative kill-switch state could not be read.

    Distinct from "the key is not set", which is a successful read of an
    unengaged switch. Conflating the two is KILL-01: see the module docstring.
    Raised by :meth:`GlobalKillSwitch._redis_get` so that a caller which must
    fail closed can tell "the switch is off" from "I do not know".
    """
    pass


class KillSwitchResetRefused(Exception):
    """A reset of the kill switch was attempted and denied.

    KILL-02. ``deactivate()`` used to accept a free-text ``triggered_by`` that nothing
    verified and had no credential, role or operator parameter at all, so there was no
    such thing as a denied reset -- every caller succeeded. This exists so that refusing
    is expressible, and so a refusal is a distinct, catchable outcome rather than a
    boolean the caller can ignore.

    Carries a machine-readable ``code``:

    ``RESET_UNAUTHORIZED``
        the caller supplied no verifiable operator identity or authorization source.
    ``RESET_REASON_MISSING``
        no reason was given for clearing an emergency control.
    ``RESET_HOLDS_UNACKNOWLEDGED``
        one or more independent halts are still active and the caller did not
        acknowledge them, so clearing would silently re-enable trading that something
        else is still blocking.
    ``RESET_STATE_UNVERIFIABLE``
        the authoritative state could not be read, so the reset cannot be shown to be
        safe. Fails closed: the switch stays engaged.
    ``RESET_CONTENDED``
        another transition is in progress. Refused rather than interleaved.
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


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

    #: Redis SET of the independent halts currently holding the switch engaged, each
    #: member being the `triggered_by` that engaged it.
    #:
    #: KILL-02, THE "SILENT RE-ENABLE" HALF. Five things can activate this switch: an
    #: operator through `/api/admin/kill-all`, and four automatic triggers
    #: (`health_monitor`, `reconciliation_engine`, `idempotency_layer`,
    #: `portfolio_validator`). Before this set existed, the state was a single string
    #: `"1"`, so a reset could not distinguish "the operator's halt is over" from "the
    #: operator's halt is over AND the reconciliation engine is still reporting a
    #: mismatch". Clearing on the first of those while the second was true re-enabled
    #: trading into an unresolved fault, and nothing anywhere said so.
    #:
    #: A reset therefore has to name which holds it has reviewed, and `deactivate()`
    #: refuses if the live set contains anything it was not told about. That is what makes
    #: "cannot silently re-enable trading while a separate halt remains active" a checked
    #: property rather than an operator convention.
    HOLDS_KEY = "system:kill_switch_holds"

    #: Short-lived Redis lock serialising activate/deactivate against each other.
    #:
    #: Without it, an `activate()` and a `deactivate()` racing across two processes can
    #: interleave as: deactivate reads holds, activate adds its hold and sets "1",
    #: deactivate sees no unacknowledged holds and writes "0". The platform ends up
    #: trading with a hold recorded and the switch cleared. The in-process
    #: `_probe_lock` cannot prevent this because the two calls are in different processes.
    TRANSITION_LOCK_KEY = "system:kill_switch:transition"

    #: How long the transition lock may be held before it self-releases. Deliberately
    #: short: it covers a handful of Redis round trips, and a crashed holder must not be
    #: able to make the control untransitionable. Separate from the audit write, which
    #: happens outside the lock precisely so a slow database cannot extend it.
    TRANSITION_LOCK_TTL_SECONDS = 15
    
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
        """Redis GET that distinguishes "not set" from "could not be read".

        Returns:
            The stored value when the key exists.
            ``None`` when Redis answered and the key does not exist.

        Raises:
            RedisUnavailable: the backend could not be reached, or the read
                failed for any other reason. The caller decides what that means;
                for a kill switch it means fail closed.

        This used to swallow every exception and return ``None``, which made the
        two outcomes indistinguishable and is the root of KILL-01. Every
        exception is translated rather than propagated as-is so that callers
        catch one documented type instead of whatever the Redis client raised.
        """
        try:
            from backend_app.backend.redis_manager import redis_manager
            return await redis_manager.get(key)
        except Exception as exc:
            raise RedisUnavailable(
                f"could not read kill-switch key {key!r}: {exc.__class__.__name__}: {exc}"
            ) from exc

    async def _redis_set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """Safe Redis SET with optional TTL. Returns True on success."""
        try:
            from backend_app.backend.redis_manager import redis_manager
            if ex:
                await redis_manager.setex(key, ex, value)
            else:
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
    
    async def _redis_expire(self, key: str, seconds: int) -> bool:
        """Safe Redis EXPIRE. Returns True on success."""
        try:
            from backend_app.backend.redis_manager import redis_manager
            await redis_manager.expire(key, seconds)
            return True
        except Exception:
            return False

    # ── Hold-set and transition-lock helpers ─────────────────────────────────
    #
    # NOTE THE DELIBERATE INCONSISTENCY WITH THE HELPERS ABOVE. `_redis_set`/`_redis_lpush`
    # /`_redis_expire`/`_redis_lrange` all swallow every exception and report a bool,
    # which is why `activate()` can engage the switch and silently record nothing. That
    # behaviour is retained for them because `activate()` deliberately proceeds on a
    # Redis failure (the local latch still blocks this process).
    #
    # The READ below must NOT behave that way. `_redis_smembers` raises
    # `RedisUnavailable`, for exactly the reason `_redis_get` does: a hold set that could
    # not be read is not evidence that there are no holds, and `deactivate()` has to be
    # able to tell those two apart to fail closed. Conflating them is KILL-01's shape.

    async def _redis_smembers(self, key: str) -> set:
        """Redis SMEMBERS that distinguishes "empty" from "could not be read".

        Returns:
            The decoded members. An empty set means Redis answered and the key holds
            nothing.

        Raises:
            RedisUnavailable: the backend could not be reached, or the read failed for
                any other reason.
        """
        try:
            from backend_app.backend.redis_manager import redis_manager
            raw = await redis_manager.smembers(key)
        except Exception as exc:
            raise RedisUnavailable(
                f"could not read kill-switch hold set {key!r}: "
                f"{exc.__class__.__name__}: {exc}"
            ) from exc
        return {
            member.decode() if isinstance(member, bytes) else str(member)
            for member in (raw or set())
        }

    async def _redis_sadd(self, key: str, value: str) -> bool:
        """Safe Redis SADD. Returns True on success.

        Best-effort, like `_redis_set`: a hold that could not be registered must not stop
        the switch engaging, because the switch engaging is the safe direction.
        """
        try:
            from backend_app.backend.redis_manager import redis_manager
            await redis_manager.sadd(key, value)
            return True
        except Exception:
            return False

    async def _redis_srem(self, key: str, *values: str) -> bool:
        """Safe Redis SREM. Returns True on success.

        Reported rather than swallowed at the call site: a hold that could not be released
        leaves the switch engaged, which is safe but needs saying -- an operator whose
        reset "succeeded" while the hold survived would be told the platform is trading
        when it is not.
        """
        if not values:
            return True
        try:
            from backend_app.backend.redis_manager import redis_manager
            await redis_manager.srem(key, *values)
            return True
        except Exception:
            return False

    async def _acquire_transition_lock(self) -> bool:
        """Take the activate/deactivate lock, or return False.

        `SET key value NX EX ttl` is one atomic Redis operation, so two processes cannot
        both believe they hold it.

        Returns True when Redis is UNREACHABLE. That is not an oversight: if the lock
        cannot be taken because the backend is down, `activate()` must still be able to
        engage the local latch, and `deactivate()` is about to fail closed anyway when
        its own state read raises. Refusing to activate because a *lock* was unavailable
        would make a Redis outage prevent an emergency halt.
        """
        try:
            from backend_app.backend.redis_manager import redis_manager
            acquired = await redis_manager.set(
                self.TRANSITION_LOCK_KEY,
                "1",
                nx=True,
                ex=self.TRANSITION_LOCK_TTL_SECONDS,
            )
            return bool(acquired)
        except Exception:
            return True

    async def _release_transition_lock(self) -> None:
        """Release the transition lock. Failure is harmless -- it carries a TTL."""
        try:
            from backend_app.backend.redis_manager import redis_manager
            await redis_manager.delete(self.TRANSITION_LOCK_KEY)
        except Exception:
            pass

    async def get_active_holds(self) -> set:
        """The independent halts currently holding the switch engaged.

        Raises:
            RedisUnavailable: the set could not be read. The caller must treat that as
                "unknown", never as "none".
        """
        return await self._redis_smembers(self.HOLDS_KEY)

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
                    # RECOVERY IS DECIDED BY WHETHER REDIS ANSWERED, NOT BY WHETHER
                    # THE KEY HAS A VALUE. The previous version lifted the latch only
                    # when `probe_result is not None`, but the resting state of a
                    # healthy system is "key absent" — which is None. A recovered
                    # Redis with no kill-switch key could therefore never lift the
                    # latch, deadlocking the platform permanently. That is the mirror
                    # image of KILL-01 and has the same cause.
                    try:
                        probe_result = await self._redis_get(self.KEY)
                    except RedisUnavailable as exc:
                        # Still down — reset timer so next probe is delayed
                        self._redis_failure_at = datetime.utcnow()
                        self._last_state = True
                        logger.critical(
                            f"🔴 Redis probe FAILED ({exc}) — local latch remains "
                            f"ACTIVE. All executions BLOCKED."
                        )
                        return True

                    # Redis responded — lift the latch. `None` here means the key is
                    # genuinely not set, i.e. the switch is off.
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

        # Normal path: consult Redis
        try:
            value = await self._redis_get(self.KEY)
        except RedisUnavailable as exc:
            # FAIL-CLOSED. This handler was unreachable before KILL-01 was fixed:
            # `_redis_get` swallowed the connection error and returned None, so
            # control fell through to the `value is None` branch below and a Redis
            # outage reported the kill switch as INACTIVE.
            logger.critical(
                f"🔴 KILL SWITCH CHECK FAILED ({exc}) — "
                f"ENGAGING LOCAL LATCH — all executions BLOCKED"
            )
            self._local_fallback_active = True
            self._redis_failure_at = datetime.utcnow()
            self._last_state = True
            return True
        except Exception as e:
            # Anything `_redis_get` did not translate. Same policy: a kill-switch
            # read that did not succeed is not evidence that trading is permitted.
            logger.critical(
                f"🔴 KILL SWITCH CHECK FAILED (unexpected {e.__class__.__name__}: {e}) "
                f"— ENGAGING LOCAL LATCH — all executions BLOCKED"
            )
            self._local_fallback_active = True
            self._redis_failure_at = datetime.utcnow()
            self._last_state = True
            return True

        if value is None:
            # Redis ANSWERED and the key does not exist → switch is genuinely not
            # engaged. Reaching this branch now requires a successful read.
            self._last_state = False
            return False

        is_killed = value == b"1" or value == "1"
        self._last_state = is_killed
        if is_killed:
            logger.critical("🔴 GLOBAL KILL SWITCH IS ACTIVE — EXECUTION BLOCKED")
        return is_killed

    async def activate(
        self,
        reason: str,
        triggered_by: Optional[str] = None,
        *,
        operator_id: Optional[str] = None,
        operator_email: Optional[str] = None,
        authorization_source: Optional[str] = None,
    ):
        """
        Activate the global kill switch with latching mechanism.
        
        Args:
            reason: Human-readable reason for activation
            triggered_by: Component or user that triggered the kill switch
            operator_id: the authenticated operator, when a human engaged this. ``None``
                for the four automatic triggers, which have no operator.
            operator_email: the operator's email, for a human-readable trail.
            authorization_source: how the actor's authority was established -- e.g. the
                dependency that resolved the operator role.
            
        This will:
        1. Set Redis key to "1" (no TTL) so every process observes the halt
        2. Engage local latch for instant effect
        3. Register an independent HOLD for ``triggered_by``, so a later reset cannot
           clear this halt without acknowledging it
        4. Log critical message with reason
        5. Record in Redis history, and durably in ``public.safety_control_audit``
        
        All subsequent executions will be blocked until deactivated.

        THE THREE KEYWORD ARGUMENTS ARE OPTIONAL, AND deactivate()'s ARE NOT.
        Asymmetric on purpose. Engaging a safety control is the safe direction and must
        stay available to an automatic trigger that has no operator identity at all;
        CLEARING one is the dangerous direction and is where the evidence is required.
        """
        timestamp = datetime.utcnow().isoformat()
        hold = (triggered_by or "system").strip() or "system"
        history_entry = f"{timestamp}|ACTIVATE|{reason}|{hold}"

        # Serialise against a concurrent reset. Taken BEFORE the latch so an interleaved
        # deactivate cannot read an empty hold set, have this call add its hold, and then
        # write "0" over the top of this activation. Returns True when Redis is
        # unreachable, so an outage cannot prevent an emergency halt -- see
        # `_acquire_transition_lock`.
        have_lock = await self._acquire_transition_lock()
        if not have_lock:
            logger.warning(
                "🟡 Kill-switch activation proceeding WITHOUT the transition lock "
                "(another transition is in progress). Activation is the safe direction, "
                "so it is not refused; the hold is still registered."
            )

        # Always engage local latch immediately for instant effect
        self._local_fallback_active = True
        self._redis_failure_at = None  # Clear probe timer — this is intentional, not a failure
        self._last_state = True  # Remember kill switch is active

        # Set Redis key with NO TTL. It used to carry ex=86400 while deactivate()
        # wrote the "off" value with no expiry at all, so "trading stopped" decayed
        # back to "trading permitted" after 24 hours unattended while "permitted"
        # was durable forever. A safety control must not drift toward the unsafe
        # state. Matches core/redis_cluster.py's own `RedisDatabase.STATE: 0` rule
        # for state keys. Cleared only by deactivate().
        redis_ok = await self._redis_set(self.KEY, "1")
        if not redis_ok:
            logger.critical(
                f"🔴 FAILED to persist kill switch to Redis — "
                f"LOCAL LATCH is still engaged (in-process protection active)\n"
                f"    Reason: {reason}\n"
                f"    This is a WARNING — Redis is down but local process is blocked"
            )
        
        # REGISTER THIS HALT AS AN INDEPENDENT HOLD.
        #
        # Best-effort by the same reasoning as the state write above: a hold that could
        # not be registered must not stop the switch engaging. The consequence of losing
        # it is that a later reset is less constrained, not that trading continues now --
        # and `deactivate()` fails closed if it cannot read the set at all, so a Redis
        # outage spanning both calls refuses the reset rather than permitting it.
        hold_ok = await self._redis_sadd(self.HOLDS_KEY, hold)
        if not hold_ok:
            logger.critical(
                "🔴 FAILED to register kill-switch hold %r — the switch IS engaged, but a "
                "later reset will not be able to see that this halt is outstanding.",
                hold,
            )

        # Record activation in history for audit trail.
        #
        # THIS IS NO LONGER THE RECORD. The LPUSH and the EXPIRE below both go through
        # helpers that swallow every exception, and the 604800 is applied here in
        # `activate()` only -- so a `deactivate()` entry inherits a partially-elapsed
        # clock and can vanish seconds after being written. Both are retained as a fast
        # operational view, unchanged; the durable record is the
        # `public.safety_control_audit` row written below, which is append-only and has
        # no expiry. See backend_app/migrations/028_safety_control_audit.sql for why no
        # retention duration is declared.
        await self._redis_lpush(self.HISTORY_KEY, history_entry)
        await self._redis_expire(self.HISTORY_KEY, 604800)  # 7 day history retention
        
        # Set activation metadata for tracking
        activation_metadata = {
            "activated_at": timestamp,
            "reason": reason,
            "triggered_by": triggered_by or "system",
            "state": "ACTIVE"
        }
        metadata_key = f"{self.KEY}:metadata"
        # No TTL, for the same reason as the state key above: metadata that expires
        # before the state it describes leaves an active kill switch with no
        # recorded reason or trigger, which is exactly what an operator needs at
        # the moment they are deciding whether it is safe to clear it.
        await self._redis_set(metadata_key, str(activation_metadata))
        
        # Released before the durable write, so a slow or unreachable database cannot
        # extend the window in which no other transition can proceed.
        if have_lock:
            await self._release_transition_lock()

        # THE DURABLE RECORD. Never raises -- see core/safety_audit's module docstring for
        # why a failed audit write must not prevent a safety control from engaging.
        from backend_app.core.safety_audit import (ACTION_ACTIVATE,
                                                   CONTROL_GLOBAL_KILL_SWITCH,
                                                   OUTCOME_APPLIED,
                                                   record_safety_transition)

        await record_safety_transition(
            control=CONTROL_GLOBAL_KILL_SWITCH,
            action=ACTION_ACTIVATE,
            outcome=OUTCOME_APPLIED,
            reason=reason,
            triggered_by=hold,
            operator_id=operator_id,
            operator_email=operator_email,
            authorization_source=authorization_source,
            detail={
                "redis_persisted": redis_ok,
                "hold_registered": hold_ok,
                "local_latch": "ENGAGED",
                "activated_at": timestamp,
                "transition_lock_held": have_lock,
            },
        )

        logger.critical(
            f"🔴🔴🔴 GLOBAL KILL SWITCH ACTIVATED 🔴🔴🔴\n"
            f"    Reason: {reason}\n"
            f"    Triggered By: {triggered_by or 'system'}\n"
            f"    Timestamp: {timestamp}\n"
            f"    Redis persisted: {redis_ok}\n"
            f"    Hold registered: {hold_ok} ({hold})\n"
            f"    Local latch: ENGAGED\n"
            f"    ALL EXECUTIONS BLOCKED UNTIL DEACTIVATED"
        )

    async def deactivate(
        self,
        reason: str,
        *,
        operator_id: str,
        authorization_source: str,
        acknowledge_holds: Iterable[str],
        operator_email: Optional[str] = None,
        triggered_by: Optional[str] = None,
    ):
        """
        Deactivate the global kill switch. Requires authorization evidence.

        KILL-02. WHAT THIS USED TO BE
        -----------------------------
        ``deactivate(reason, triggered_by=None)``. ``triggered_by`` was free text that
        nothing verified, there was no credential/role/operator parameter of any kind, and
        the audit trail was a Redis LIST whose ``EXPIRE`` was applied only in
        ``activate()``. So an emergency control could be cleared by any caller, attributed
        to any string they chose, with the record able to expire out from under it. There
        was also no such thing as a *denied* reset: every call succeeded.

        It also cleared the local latch BEFORE attempting the Redis write, and raised only
        afterwards -- so a Redis failure left this process's latch cleared while other
        processes stayed blocked. This version reads and validates everything first and
        mutates nothing until every check has passed.

        THE EVIDENCE IS REQUIRED BY THE SIGNATURE, NOT VALIDATED AT RUNTIME
        ------------------------------------------------------------------
        ``operator_id``, ``authorization_source`` and ``acknowledge_holds`` are
        keyword-only with no defaults, so a caller that has not established who is acting
        and on what authority **cannot call this method at all**. That is the same
        structural reasoning TRADE-05 applied to ``risk_approved``: an invariant the type
        system enforces cannot be forgotten by a future branch, whereas one checked inside
        the body can be bypassed by passing a plausible-looking value.

        Contrast ``activate()``, whose equivalent arguments are optional. Engaging a
        safety control is the safe direction and must remain available to the four
        automatic triggers, which have no operator identity. Clearing one is the dangerous
        direction.

        Args:
            reason: why the halt is being cleared. Must not be blank.
            operator_id: the authenticated operator performing the reset.
            authorization_source: how that operator's authority was established -- e.g.
                the dependency that resolved the operator role.
            acknowledge_holds: the halts the operator has reviewed. Must cover every
                hold currently registered; see below.
            operator_email: the operator's email, for a human-readable trail.
            triggered_by: optional label; defaults to ``operator_email`` or
                ``operator_id``.

        Raises:
            KillSwitchResetRefused: with ``code`` one of ``RESET_REASON_MISSING``,
                ``RESET_UNAUTHORIZED``, ``RESET_CONTENDED``,
                ``RESET_STATE_UNVERIFIABLE`` or ``RESET_HOLDS_UNACKNOWLEDGED``. Every
                refusal is itself recorded durably, which is the point of the
                ``outcome`` column.
            RuntimeError: the authoritative state write failed after all checks passed,
                so other processes may remain blocked. Preserved as ``RuntimeError``
                because it is not a refusal -- the reset was authorized and then the
                backend failed, which is a different event and demands manual repair.
        """
        timestamp = datetime.utcnow().isoformat()
        actor = (triggered_by or operator_email or operator_id or "").strip() or "unknown"

        from backend_app.core.safety_audit import (ACTION_DEACTIVATE,
                                                   CONTROL_GLOBAL_KILL_SWITCH,
                                                   OUTCOME_APPLIED,
                                                   OUTCOME_REFUSED,
                                                   record_safety_transition)

        async def _refuse(code: str, detail: str, **extra):
            """Record the denial durably, then raise.

            A refused reset is recorded BEFORE the exception propagates, so an attacker
            or a confused operator cannot produce an unrecorded attempt by discarding the
            exception.
            """
            await record_safety_transition(
                control=CONTROL_GLOBAL_KILL_SWITCH,
                action=ACTION_DEACTIVATE,
                outcome=OUTCOME_REFUSED,
                reason=reason or "(no reason supplied)",
                triggered_by=actor,
                operator_id=operator_id,
                operator_email=operator_email,
                authorization_source=authorization_source,
                detail={"refusal_code": code, "refusal_detail": detail, **extra},
            )
            logger.critical(
                "🔴 KILL-SWITCH RESET REFUSED (%s) by %s: %s", code, actor, detail
            )
            raise KillSwitchResetRefused(code, detail)

        # ── 1. A reason is mandatory ─────────────────────────────────────────
        if not (reason or "").strip():
            await _refuse(
                "RESET_REASON_MISSING",
                "clearing an emergency control requires a stated reason; none was given.",
            )

        # ── 2. Authorization evidence must actually be present ───────────────
        # The signature makes these mandatory to SUPPLY; this makes them mandatory to be
        # MEANINGFUL. `operator_id=""` would otherwise satisfy the signature and record an
        # anonymous reset.
        if not str(operator_id or "").strip():
            await _refuse(
                "RESET_UNAUTHORIZED",
                "no operator identity was supplied, so this reset cannot be attributed. "
                "An emergency control is not cleared anonymously.",
            )
        if not str(authorization_source or "").strip():
            await _refuse(
                "RESET_UNAUTHORIZED",
                "no authorization source was supplied, so there is no evidence the "
                "actor was permitted to clear this control. KILL-02 was exactly this: a "
                "free-text label nothing verified.",
            )

        # ── 3. Serialise against a concurrent activation ─────────────────────
        have_lock = await self._acquire_transition_lock()
        if not have_lock:
            await _refuse(
                "RESET_CONTENDED",
                "another kill-switch transition is in progress. Refused rather than "
                "interleaved: a reset that reads the hold set before a concurrent "
                "activation registers its hold would clear a halt it never saw.",
            )

        try:
            # ── 4. Read the live hold set. FAILS CLOSED. ─────────────────────
            # `_redis_smembers` raises rather than returning an empty set, for the same
            # reason `_redis_get` does: "I could not read the holds" is not evidence that
            # there are none. Conflating those two is KILL-01's shape, and here it would
            # mean a Redis outage silently permits a reset that clears halts nobody
            # reviewed.
            try:
                live_holds = await self._redis_smembers(self.HOLDS_KEY)
            except RedisUnavailable as exc:
                await _refuse(
                    "RESET_STATE_UNVERIFIABLE",
                    f"the authoritative hold set could not be read ({exc}), so this "
                    f"reset cannot be shown to be safe. The switch stays engaged.",
                )
                return  # unreachable; keeps the control flow explicit for a reader

            # ── 5. Every outstanding halt must have been acknowledged ────────
            acknowledged = {
                str(h).strip() for h in (acknowledge_holds or ()) if str(h).strip()
            }
            unacknowledged = live_holds - acknowledged
            if unacknowledged:
                await _refuse(
                    "RESET_HOLDS_UNACKNOWLEDGED",
                    "these independent halts are still active and were not acknowledged: "
                    + ", ".join(sorted(unacknowledged))
                    + ". Clearing the switch now would re-enable trading into a fault "
                      "that something else is still reporting. Resolve them, or "
                      "acknowledge them explicitly.",
                    live_holds=sorted(live_holds),
                    acknowledged=sorted(acknowledged),
                    unacknowledged=sorted(unacknowledged),
                )

            # ── 6. Every check has passed. NOW mutate. ───────────────────────
            # Order matters and is the reverse of the previous implementation's: the
            # authoritative state is written FIRST and the local latch is cleared only
            # after that write succeeded. The old order cleared the latch and then raised
            # on a failed write, leaving this process unlatched while others stayed
            # blocked -- the split brain its own docstring warned about.
            redis_ok = await self._redis_set(self.KEY, "0")
            if not redis_ok:
                logger.critical(
                    f"🔴 FAILED to clear kill switch in Redis — "
                    f"the local latch has NOT been cleared, so this process remains "
                    f"blocked and is consistent with the others.\n"
                    f"    Manual Redis intervention required.\n"
                    f"    Reason: {reason}"
                )
                await record_safety_transition(
                    control=CONTROL_GLOBAL_KILL_SWITCH,
                    action=ACTION_DEACTIVATE,
                    outcome=OUTCOME_REFUSED,
                    reason=reason,
                    triggered_by=actor,
                    operator_id=operator_id,
                    operator_email=operator_email,
                    authorization_source=authorization_source,
                    detail={
                        "refusal_code": "RESET_STATE_WRITE_FAILED",
                        "live_holds": sorted(live_holds),
                    },
                )
                raise RuntimeError(
                    "CRITICAL: Kill switch reset was authorized but the Redis update "
                    "FAILED. The local latch was left ENGAGED so this process stays "
                    "consistent with the others, which may still be blocked. Manual "
                    "Redis fix required."
                )

            holds_released = await self._redis_srem(self.HOLDS_KEY, *sorted(live_holds))
            if live_holds and not holds_released:
                logger.critical(
                    "🔴 Kill switch cleared but the hold set could not be emptied (%s). "
                    "A later reset will refuse until it is, which is the safe direction.",
                    sorted(live_holds),
                )

            # The latch is cleared LAST, once the authoritative state agrees.
            self._local_fallback_active = False
            self._redis_failure_at = None
            self._last_state = False
        finally:
            if have_lock:
                await self._release_transition_lock()

        history_entry = f"{timestamp}|DEACTIVATE|{reason}|{actor}"
        await self._redis_lpush(self.HISTORY_KEY, history_entry)
        # The 7-day EXPIRE is applied in `activate()` only and is deliberately NOT
        # re-applied here. Refreshing it would extend the operational view's window on
        # every reset and make its retention depend on transition order. The durable
        # record is the audit row below; the list is a fast view with a fixed window.

        await record_safety_transition(
            control=CONTROL_GLOBAL_KILL_SWITCH,
            action=ACTION_DEACTIVATE,
            outcome=OUTCOME_APPLIED,
            reason=reason,
            triggered_by=actor,
            operator_id=operator_id,
            operator_email=operator_email,
            authorization_source=authorization_source,
            detail={
                "released_holds": sorted(live_holds),
                "holds_released_ok": holds_released,
                "deactivated_at": timestamp,
            },
        )

        logger.info(
            f"🟢 GLOBAL KILL SWITCH DEACTIVATED\n"
            f"    Reason: {reason}\n"
            f"    Operator: {operator_email or operator_id}\n"
            f"    Authorization: {authorization_source}\n"
            f"    Holds released: {sorted(live_holds) or '(none)'}\n"
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
        """Return a structured status dict for health endpoints.

        ``holds`` and ``holds_readable`` exist so an operator can tell WHY the platform is
        halted and what a reset would have to acknowledge, rather than only that it is.
        ``holds_readable`` is False when the set could not be read -- reported rather than
        rendered as an empty list, because "no holds" and "I do not know" are the two
        facts this module exists to keep apart.
        """
        active = await self.is_active()
        try:
            holds = sorted(await self._redis_smembers(self.HOLDS_KEY))
            holds_readable = True
        except RedisUnavailable:
            holds = []
            holds_readable = False
        return {
            "kill_switch_active": active,
            "local_latch_active": self._local_fallback_active,
            "redis_failure_at": (
                self._redis_failure_at.isoformat() if self._redis_failure_at else None
            ),
            "probe_interval_seconds": _REDIS_PROBE_INTERVAL_SECONDS,
            "holds": holds,
            "holds_readable": holds_readable,
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
    "RedisUnavailable",
    "KillSwitchResetRefused",
    "get_global_kill_switch",
    "enforce_kill_switch",
    "generate_validation_token",
    "verify_validation_token",
]

