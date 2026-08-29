"""
core/distributed_idempotency.py — Distributed Idempotency Layer

🔴 STEP 2 — DISTRIBUTED IDEMPOTENCY (MANDATORY)

Provides Redis-based idempotency to prevent duplicate orders across pods.

IMPLEMENTATION:
- Key: f"idempotency:{tenant_id}:{client_order_id}"
- Checks Redis BEFORE execution
- If exists: returns cached result
- If not: sets "processing" with 60s TTL, executes, then stores result with
  RESULT_TTL (3600s by default, or the caller's result_ttl override)

SIGNAL -> ORDER PATH (trading-lifecycle-integration, Requirement 19.1):
- idempotency_key_for(signal) derives the key deterministically from the
  Signal's own id, so every retry of the same decision computes the same key.
- SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS is that path's result TTL (6h default,
  configurable). Passed per call; the 1-hour class default is unchanged for
  every other caller.
- Neither addition touches the locking algorithm.

RULE:
ALL orders must have client_order_id
MUST be unique per request

EXPECTED RESULT:
✔ No duplicate orders across pods
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from backend_app.core.cache import redis_manager
from backend_app.core.global_safety import get_global_kill_switch

logger = logging.getLogger("DistributedIdempotency")


# ---------------------------------------------------------------------------
# SIGNAL-SCOPED IDEMPOTENCY (trading-lifecycle-integration, Requirement 19.1)
#
# Two additions, both purely additive to the layer below: a deterministic key
# derivation for a Signal, and a result TTL long enough for a Signal's real
# lifecycle. NOTHING in the locking algorithm changes - the atomic Lua
# check-and-set and the owner-token compare-and-delete release are reused
# verbatim by the signal path.
# ---------------------------------------------------------------------------

#: Prefix that scopes a derived key to the signal namespace, so a signal-derived
#: key can never be confused with an HTTP caller's own client_order_id.
SIGNAL_KEY_PREFIX = "signal:"

#: Default result TTL for the signal -> order path: 6 hours.
#:
#: WHY 6 HOURS AND NOT THE CLASS DEFAULT OF 1 HOUR.
#:   DistributedIdempotencyLayer.RESULT_TTL = 3600 is correct for the layer's
#:   original purpose - a synchronous HTTP request being retried - and is NOT
#:   widened here. Every existing caller keeps the 1-hour default. Requirement
#:   19.1 asks for something stronger on the trading path: the key and its
#:   recorded outcome must stay enforced from a Signal's GENERATED state until
#:   it reaches a terminal Order_Lifecycle_State, and must not be evicted
#:   before then. The ceiling that bounds a real submission is the platform's
#:   own order timeout - OrderStateEngine.default_timeout_seconds (30s) times
#:   DistributedIdempotencyLayer.MAX_RETRY_ATTEMPTS - so 6 hours is that
#:   ceiling plus very generous headroom, not a guess at a signal's lifetime.
#:
#: WHY A REDIS TTL IS NOT THE ONLY GUARANTEE, BY DESIGN.
#:   Migration 005b's partial unique index uq_signals_idempotency_key is the
#:   DURABLE BACKSTOP behind this Redis lock. If Redis is flushed or a key
#:   expires mid-flight, a second INSERT carrying the same idempotency_key
#:   fails at the database with 23505, which the caller (task 10.1/10.2's
#:   signal_service) translates into DuplicateOrderError. So this TTL is sized
#:   for the fast path; it is not load-bearing for correctness on its own.
DEFAULT_SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS: int = 6 * 60 * 60


def _read_signal_result_ttl() -> int:
    """Read the signal-path result TTL from the environment, configured not hardcoded.

    A missing, unparseable or non-positive value falls back to the 6-hour
    default rather than raising at import time: a bad operator override must
    never take the whole trading path down, and a zero or negative TTL would
    mean "no idempotency window at all", which is the one value this layer
    must never adopt silently.
    """
    raw = os.getenv("SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS")
    if raw is None or str(raw).strip() == "":
        return DEFAULT_SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS=%r is not an integer - "
            "falling back to the %ds default.",
            raw,
            DEFAULT_SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS,
        )
        return DEFAULT_SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS
    if parsed <= 0:
        logger.warning(
            "SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS=%d is not positive - "
            "falling back to the %ds default.",
            parsed,
            DEFAULT_SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS,
        )
        return DEFAULT_SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS
    return parsed


#: The configured result TTL, in seconds, for the signal -> order path.
SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS: int = _read_signal_result_ttl()


def _signal_id_of(signal: Any) -> Any:
    """Pull the signal's own id out of a Signal, a mapping, or a bare id string.

    Deliberately duck-typed: task 10.1 owns the Signal record's final shape, and
    this helper must already be usable by every writer on the path (the service,
    a recovery sweep reading rows back out of the database as dicts, a test).
    Only the id is ever read - see idempotency_key_for on why that matters.
    """
    if isinstance(signal, str):
        return signal
    if isinstance(signal, dict):
        # A key present but NULL is treated exactly like a key absent. A database
        # row read back with a NULL id must not derive "signal:None", which every
        # such row would share.
        if signal.get("id") is None:
            raise ValueError(
                "idempotency_key_for requires a signal with a non-null 'id'; "
                "got a mapping without one."
            )
        return signal["id"]
    signal_id = getattr(signal, "id", None)
    if signal_id is None:
        raise ValueError(
            "idempotency_key_for requires a signal with an 'id'; "
            f"got {type(signal).__name__} without one."
        )
    return signal_id


def idempotency_key_for(signal: Any) -> str:
    """Derive the Idempotency_Key for a Signal. Deterministic and pure.

    Requirement 19.1. The key is a pure function of the Signal's own identity:

        idempotency_key_for(signal) == "signal:" + signal.id

    WHY signal.id AND NOTHING ELSE.
        signal.id is minted once, at generation (Requirement 15.1), and is never
        reused. Deriving the key from it - rather than from an attempt counter, a
        timestamp, a retry number or anything else that moves - is precisely what
        makes a network retry, an exchange-side retry, a WebSocket reconnect and
        a worker restart submitting for the SAME signal all compute the SAME key,
        which is what gives "at most one order at the exchange".

        It is also what keeps the key clean under Requirement 20.3: no exchange
        credential, API key, secret, passphrase or exchange identifier is read
        here, so none can end up in a key that is logged, indexed in
        uq_signals_idempotency_key, or sent as a client order id. The signal's
        exchange_account_id is NOT part of the key either - the key names one
        decision, not one venue.

    NO NORMALISATION IS APPLIED to the id beyond str(). Stripping or lower-casing
    it would let two distinct signals collapse onto one key, which would silently
    suppress a real second order.

    Args:
        signal: A Signal (anything with an ``id``), a mapping with an ``"id"``,
            or the signal id itself.

    Returns:
        The derived key, always prefixed ``signal:``.

    Raises:
        ValueError: If no id is present, or the id is empty/blank. An unnamed
            signal has no identity to be idempotent on, so this fails loudly
            rather than deriving a key that every unnamed signal would share.
    """
    signal_id = _signal_id_of(signal)
    text = str(signal_id)
    if text.strip() == "":
        raise ValueError(
            "idempotency_key_for requires a non-empty signal id; "
            f"got {signal_id!r}."
        )
    return f"{SIGNAL_KEY_PREFIX}{text}"


def _is_production() -> bool:
    """Check if running in production environment."""
    return os.getenv("ENV", "development").lower() == "production"


class DuplicateOrderError(Exception):
    """
    Raised when a duplicate order is detected via idempotency check.
    
    This indicates the same client_order_id has already been processed.
    """
    pass


class MissingClientOrderIdError(Exception):
    """
    Raised when an order is submitted without a client_order_id.
    
    RULE: ALL orders must have client_order_id
    """
    pass


@dataclass
class IdempotencyResult:
    """Result of an idempotency check."""
    is_duplicate: bool
    is_processing: bool
    cached_result: Optional[Any] = None
    key: str = ""


class DistributedIdempotencyLayer:
    """
    🔴 STEP 2 — DISTRIBUTED IDEMPOTENCY (MANDATORY)
    
    Redis-backed idempotency layer that prevents duplicate orders
    across all pods in the distributed system.
    
    KEY FORMAT:
        idempotency:{tenant_id}:{client_order_id}
    
    FLOW:
        1. Check if key exists in Redis
        2. If exists:
           - If value is "processing": wait and retry
           - Otherwise: return cached result (duplicate detected)
        3. If not exists:
           - Set key to "processing" with 60s TTL
           - Execute the operation
           - Store result with RESULT_TTL (3600s), or with the caller's
             result_ttl override where one is given
    
    ENFORCEMENT:
        - ALL orders must have client_order_id
        - client_order_id MUST be unique per request
        - Raises MissingClientOrderIdError if missing
        - Raises DuplicateOrderError if duplicate detected with critical issues
    
    EXPECTED RESULT:
        ✔ No duplicate orders across pods
    """
    
    KEY_PREFIX = "idempotency"
    PROCESSING_TTL = 60      # 60 seconds for processing state
    RESULT_TTL = 3600        # 1 hour for completed results
    MAX_RETRY_ATTEMPTS = 10  # Max attempts to check processing state
    RETRY_DELAY = 0.5        # Seconds between retries
    
    def _generate_key(
        self, 
        tenant_id: str, 
        client_order_id: str, 
        exchange_id: Optional[str] = None
    ) -> str:
        """Generate the Redis key for idempotency."""
        if exchange_id:
            return f"{self.KEY_PREFIX}:{tenant_id}:{exchange_id}:{client_order_id}"
        return f"{self.KEY_PREFIX}:{tenant_id}:{client_order_id}"
    
    def _resolve_result_ttl(self, result_ttl: Optional[int] = None) -> int:
        """Resolve the result TTL for one call.
        
        ``None`` means "the existing default", which is what keeps every caller
        that predates the signal path on exactly the 1-hour RESULT_TTL it had
        before. A non-positive or unparseable override is refused rather than
        applied, because passing it to Redis as an expiry would either error or
        - worse - store the result with no idempotency window at all.
        """
        if result_ttl is None:
            return self.RESULT_TTL
        try:
            ttl = int(result_ttl)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"result_ttl must be a positive integer number of seconds; "
                f"got {result_ttl!r}."
            ) from exc
        if ttl <= 0:
            raise ValueError(
                f"result_ttl must be a positive integer number of seconds; "
                f"got {ttl}."
            )
        return ttl
    
    async def check_idempotency(
        self, 
        tenant_id: str, 
        client_order_id: str,
        exchange_id: Optional[str] = None
    ) -> IdempotencyResult:
        """
        Check if this request has already been processed.
        
        Args:
            tenant_id: Tenant identifier
            client_order_id: Client-provided unique order ID
            exchange_id: Optional exchange identifier
            
        Returns:
            IdempotencyResult indicating duplicate status
            
        Raises:
            MissingClientOrderIdError: If client_order_id is empty/None
        """
        # RULE: ALL orders must have client_order_id
        if not client_order_id:
            logger.error(
                f"🔴 STEP 2: Order rejected - missing client_order_id for tenant {tenant_id}"
            )
            raise MissingClientOrderIdError(
                "RULE VIOLATION: ALL orders must have client_order_id"
            )
        
        key = self._generate_key(tenant_id, client_order_id, exchange_id)
        
        try:
            # Check if key exists in Redis
            existing_value = await redis_manager.get(key)
            
            if existing_value is None:
                # Key doesn't exist - this is a new request
                logger.debug(f"STEP 2: New request {key} - no idempotency conflict")
                return IdempotencyResult(
                    is_duplicate=False,
                    is_processing=False,
                    cached_result=None,
                    key=key
                )
            
            # Key exists - decode value
            value_str = existing_value.decode() if isinstance(existing_value, bytes) else str(existing_value)
            
            if value_str == "processing" or value_str.startswith("processing:"):
                # Another pod/worker is currently processing this request
                logger.info(f"STEP 2: Request {key} is being processed by another pod")
                return IdempotencyResult(
                    is_duplicate=False,
                    is_processing=True,
                    cached_result=None,
                    key=key
                )
            
            # Request already completed - return cached result with integrity validation
            try:
                cached_result = json.loads(value_str)
                if isinstance(cached_result, dict):
                    # Verify tenant isolation and order matching within cached payload
                    cached_tenant = cached_result.get("tenant_id")
                    cached_order = cached_result.get("client_order_id")
                    if cached_tenant and cached_tenant != tenant_id:
                        logger.critical(f"STEP 2: Tenant mismatch in cached result for {key}")
                        return IdempotencyResult(is_duplicate=False, is_processing=False, cached_result=None, key=key)
                    if cached_order and cached_order != client_order_id:
                        logger.critical(f"STEP 2: client_order_id mismatch in cached result for {key}")
                        return IdempotencyResult(is_duplicate=False, is_processing=False, cached_result=None, key=key)
                
                logger.info(f"STEP 2: Duplicate request detected {key} - returning cached result")
                return IdempotencyResult(
                    is_duplicate=True,
                    is_processing=False,
                    cached_result=cached_result,
                    key=key
                )
            except json.JSONDecodeError:
                # Invalid cached value - treat as new request
                logger.warning(f"STEP 2: Invalid cached value for {key} - treating as new")
                return IdempotencyResult(
                    is_duplicate=False,
                    is_processing=False,
                    cached_result=None,
                    key=key
                )
                
        except Exception as e:
            logger.error(f"STEP 2: Idempotency check failed for {key}: {e}")
            # FAIL-CLOSED: In production, Redis failure should prevent duplicate execution risk
            if _is_production():
                raise RuntimeError(
                    f"CRITICAL: Redis unavailable during idempotency check. "
                    f"Operation blocked to prevent duplicate execution in production."
                )
            # In development, allow operation but log the failure
            return IdempotencyResult(
                is_duplicate=False,
                is_processing=False,
                cached_result=None,
                key=key
            )
            return IdempotencyResult(
                is_duplicate=False,
                is_processing=False,
                cached_result=None,
                key=key
            )
    
    async def _atomic_check_and_set(
        self,
        tenant_id: str,
        client_order_id: str,
        owner_token: str,
        exchange_id: Optional[str] = None
    ) -> tuple[Optional[dict], bool]:
        """
        FIN-CRITICAL-004 FIX: Atomic check-and-set using Redis Lua script.
        
        This method combines idempotency check and lock acquisition into a single
        atomic operation to prevent race conditions where duplicate requests could
        both proceed when the first worker's store_result fails after lock release.
        
        Args:
            tenant_id: Tenant identifier
            client_order_id: Client-provided unique order ID
            owner_token: Unique lock owner token
            exchange_id: Optional exchange identifier
            
        Returns:
            Tuple of (cached_result: Optional[dict], lock_acquired: bool)
        """
        key = self._generate_key(tenant_id, client_order_id, exchange_id)
        lock_payload = f"processing:{owner_token}"
        
        # Redis Lua script for atomic check-and-set
        lua_script = """
        local key = KEYS[1]
        local lock_payload = ARGV[1]
        local processing_ttl = ARGV[2]
        
        -- Check if key exists
        local current_value = redis.call('GET', key)
        
        -- If key exists and is a completed result, return it
        if current_value and string.sub(current_value, 1, 7) ~= 'processing' then
            return {1, current_value}  -- Return cached result
        end
        
        -- If key doesn't exist or is expired, set processing lock
        if not current_value then
            redis.call('SET', key, lock_payload, 'EX', processing_ttl, 'NX')
            return {0, lock_payload}  -- Lock acquired
        end
        
        -- Key exists and is processing - lock not acquired
        return {0, false}
        """
        
        try:
            result = await redis_manager.eval_lua(
                lua_script,
                keys=[key],
                args=[lock_payload, str(self.PROCESSING_TTL)]
            )
            
            # Parse Lua script result
            # Format: [status, value] where status 1 = cached result, 0 = lock status
            status, value = result
            
            if status == 1:
                # Cached result exists
                try:
                    cached_data = json.loads(value)
                    return cached_data, False
                except json.JSONDecodeError:
                    logger.warning(f"Invalid cached value in atomic check-and-set for {key}")
                    return None, False
            else:
                # No cached result, lock status in value
                if value == lock_payload:
                    return None, True  # Lock acquired
                else:
                    return None, False  # Lock not acquired
                    
        except Exception as e:
            logger.error(f"Atomic check-and-set failed for {key}: {e}")
            # Fallback to non-atomic behavior if Lua script fails
            return None, False

    async def start_processing(
        self, 
        tenant_id: str, 
        client_order_id: str,
        owner_token: Optional[str] = None,
        exchange_id: Optional[str] = None
    ) -> tuple[bool, str]:
        """
        Atomically mark this request as being processed using Redis SET NX with owner token.
        
        Args:
            tenant_id: Tenant identifier
            client_order_id: Client-provided unique order ID
            owner_token: Optional unique lock owner token
            exchange_id: Optional exchange identifier
            
        Returns:
            Tuple of (acquired: bool, owner_token: str)
        """
        from uuid import uuid4
        token = owner_token or str(uuid4())
        key = self._generate_key(tenant_id, client_order_id, exchange_id)
        lock_payload = f"processing:{token}"
        
        try:
            res = await redis_manager.set(key, lock_payload, ex=self.PROCESSING_TTL, nx=True)
            acquired = bool(res)
            if acquired:
                logger.debug(f"STEP 2: Atomically acquired processing lock for {key} with owner {token} (TTL={self.PROCESSING_TTL}s)")
            else:
                logger.info(f"STEP 2: Failed to acquire processing lock for {key} - key already exists")
            return acquired, token
        except Exception as e:
            logger.error(f"STEP 2: Failed to mark processing for {key}: {e}")
            # FAIL-CLOSED: In production, Redis failure should prevent duplicate execution risk
            if _is_production():
                raise RuntimeError(
                    f"CRITICAL: Redis unavailable during processing lock acquisition. "
                    f"Operation blocked to prevent duplicate execution in production."
                )
            return False, token

    async def release_processing_lock(
        self,
        tenant_id: str,
        client_order_id: str,
        owner_token: str,
        exchange_id: Optional[str] = None
    ) -> bool:
        """
        Owner-safe lock release using compare-and-delete.
        Only deletes the key if current value matches owner_token to prevent
        unintentionally deleting another worker's new lock if TTL expired.
        """
        key = self._generate_key(tenant_id, client_order_id, exchange_id)
        expected_val = f"processing:{owner_token}"
        try:
            curr_val = await redis_manager.get(key)
            if curr_val is None:
                return True
            curr_str = curr_val.decode() if isinstance(curr_val, bytes) else str(curr_val)
            if curr_str == expected_val or curr_str == "processing":
                await redis_manager.delete(key)
                logger.debug(f"STEP 2: Owner-safe released lock for {key}")
                return True
            logger.warning(f"STEP 2: Lock release skipped for {key} - owner mismatch (held by: {curr_str})")
            return False
        except Exception as e:
            logger.error(f"STEP 2: Error releasing lock for {key}: {e}")
            return False
    
    async def store_result(
        self, 
        tenant_id: str, 
        client_order_id: str, 
        result: Any,
        exchange_id: Optional[str] = None,
        result_ttl: Optional[int] = None
    ) -> str:
        """
        Store the execution result for future duplicate detection.
        
        Sets key to JSON result with RESULT_TTL (3600s) unless overridden.
        
        Args:
            tenant_id: Tenant identifier
            client_order_id: Client-provided unique order ID
            result: Execution result to cache (must be JSON serializable)
            exchange_id: Optional exchange identifier
            result_ttl: Optional TTL override, in seconds. Defaults to
                RESULT_TTL, so every existing caller's behaviour is unchanged.
                The signal -> order path passes
                SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS here (Requirement 19.1).
            
        Returns:
            The Redis key that was set
        """
        key = self._generate_key(tenant_id, client_order_id, exchange_id)
        ttl = self._resolve_result_ttl(result_ttl)
        
        try:
            result_json = json.dumps({
                "result": result,
                "timestamp": datetime.utcnow().isoformat(),
                "tenant_id": tenant_id,
                "client_order_id": client_order_id,
                "exchange_id": exchange_id
            })
            await redis_manager.set(key, result_json, ex=ttl)
            logger.debug(f"STEP 2: Stored result for {key} (TTL={ttl}s)")
            return key
        except Exception as e:
            logger.error(f"STEP 2: Failed to store result for {key}: {e}")
            # FAIL-CLOSED: In production, Redis failure should prevent duplicate execution risk
            if _is_production():
                raise RuntimeError(
                    f"CRITICAL: Redis unavailable during result storage. "
                    f"Operation blocked to prevent duplicate execution in production."
                )
            return key
    
    async def execute_with_idempotency(
        self,
        tenant_id: str,
        client_order_id: str,
        operation: Callable,
        *args,
        exchange_id: Optional[str] = None,
        result_ttl: Optional[int] = None,
        **kwargs
    ) -> Any:
        """
        Execute an operation with distributed idempotency guarantees.
        
        This is the MAIN ENTRY POINT for idempotent operations.
        
        Args:
            tenant_id: Tenant identifier
            client_order_id: The idempotency key. On the signal -> order path
                this is ``idempotency_key_for(signal)``.
            operation: The awaitable to run at most once for this key.
            *args / **kwargs: Forwarded to ``operation``.
            exchange_id: Optional exchange identifier, scopes the Redis key.
            result_ttl: Optional result-TTL override, in seconds. Defaults to
                None, meaning RESULT_TTL - so no existing caller's behaviour
                changes. The signal -> order path passes
                SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS (Requirement 19.1).
                Consumed by this layer, NOT forwarded to ``operation``, the same
                way ``exchange_id`` already is.
        
        The locking algorithm below is unchanged: the same atomic Lua
        check-and-set acquires, and the same owner-token compare-and-delete
        releases. ``result_ttl`` affects only how long a COMPLETED result is
        remembered; PROCESSING_TTL, the in-flight lock's lifetime, is untouched.
        """
        # Validate the override before doing any Redis work, so a bad TTL is a
        # loud rejection up front rather than a surprise after the operation has
        # already run and its result cannot be cached.
        self._resolve_result_ttl(result_ttl)
        
        # RULE: ALL orders must have client_order_id
        if not client_order_id:
            logger.error(
                f"🔴 STEP 2: Order rejected - missing client_order_id for tenant {tenant_id}"
            )
            raise MissingClientOrderIdError(
                "RULE VIOLATION: ALL orders must have client_order_id"
            )
        
        # FIN-CRITICAL-004 FIX: Use atomic check-and-set with Redis Lua script
        # This prevents race condition between idempotency check and lock acquisition
        from uuid import uuid4
        owner_token = str(uuid4())
        
        # Atomic check-and-set operation
        cached_result, lock_acquired = await self._atomic_check_and_set(
            tenant_id, client_order_id, owner_token, exchange_id
        )
        
        if cached_result:
            logger.info(
                f"STEP 2: Returning cached result for duplicate request "
                f"(atomic check-and-set)"
            )
            return cached_result.get("result")
        
        if not lock_acquired:
            logger.info(
                f"STEP 2: Failed to acquire lock atomically - request already processing"
            )
        
        if not lock_acquired:
            logger.info(
                f"STEP 2: Waiting for concurrent request {client_order_id}"
            )
            
            # Wait and retry to fetch completed duplicate result
            for attempt in range(self.MAX_RETRY_ATTEMPTS):
                await asyncio.sleep(self.RETRY_DELAY)
                
                recheck = await self.check_idempotency(tenant_id, client_order_id, exchange_id)
                
                if recheck.is_duplicate and recheck.cached_result:
                    # Processing completed by peer pod - return cached result safely
                    return recheck.cached_result.get("result")
                
                if not recheck.is_processing:
                    # No longer processing - attempt to acquire lock again
                    lock_acquired, lock_token = await self.start_processing(
                        tenant_id, client_order_id, owner_token=owner_token, exchange_id=exchange_id
                    )
                    if lock_acquired:
                        break
            
            if not lock_acquired:
                # Max retries exceeded or duplicate rejected
                logger.critical(
                    f"🔴 STEP 2: Duplicate concurrent execution blocked for {client_order_id}"
                )
                raise DuplicateOrderError(
                    f"Duplicate order execution blocked for request {client_order_id}"
                )
        
        try:
            # Execute the operation
            result = await operation(*args, **kwargs)
            
            # Store result
            await self.store_result(
                tenant_id,
                client_order_id,
                result,
                exchange_id=exchange_id,
                result_ttl=result_ttl,
            )
            
            return result
            
        except Exception:
            # Owner-safe release of processing state on failure.
            #
            # owner_token, NOT lock_token. lock_token was a real bug: it is bound
            # only inside the `if not lock_acquired:` retry loop above, so on the
            # COMMON path - the lock acquired atomically on the first try - the
            # name does not exist in this frame and this block raised
            # UnboundLocalError instead of releasing the lock and re-raising the
            # real error. Two consequences, both bad and both silent: the lock
            # stayed held for the full PROCESSING_TTL (60s) even though the
            # operation had already failed, and the actual failure - a risk
            # rejection, an exchange error, a database write failure - was
            # replaced by an UnboundLocalError that named none of it.
            #
            # owner_token is the correct value in BOTH cases: it is the token
            # this call generated, it is the token the atomic Lua check-and-set
            # wrote into the lock payload, and it is the token the retry loop
            # passes to start_processing (which returns it back unchanged), so
            # lock_token was only ever an alias for it on the one path where it
            # existed at all.
            await self.release_processing_lock(
                tenant_id, client_order_id, owner_token=owner_token, exchange_id=exchange_id
            )
            
            # Re-raise the original exception
            raise


# Global singleton instance
_idempotency_layer: Optional[DistributedIdempotencyLayer] = None


def get_idempotency_layer() -> DistributedIdempotencyLayer:
    """Get or create the distributed idempotency layer instance."""
    global _idempotency_layer
    if _idempotency_layer is None:
        _idempotency_layer = DistributedIdempotencyLayer()
    return _idempotency_layer


# Alias for validation suites
DistributedIdempotency = DistributedIdempotencyLayer

# Convenience exports
__all__ = [
    "DistributedIdempotencyLayer",
    "DistributedIdempotency",
    "IdempotencyResult",
    "DuplicateOrderError",
    "MissingClientOrderIdError",
    "get_idempotency_layer",
    # Signal-scoped additions (Requirement 19.1)
    "idempotency_key_for",
    "SIGNAL_KEY_PREFIX",
    "SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS",
    "DEFAULT_SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS",
]
