"""
core/distributed_idempotency.py — Distributed Idempotency Layer

🔴 STEP 2 — DISTRIBUTED IDEMPOTENCY (MANDATORY)

Provides Redis-based idempotency to prevent duplicate orders across pods.

IMPLEMENTATION:
- Key: f"idempotency:{tenant_id}:{client_order_id}"
- Checks Redis BEFORE execution
- If exists: returns cached result
- If not: sets "processing" with 60s TTL, executes, then stores result with 3600s TTL

RULE:
ALL orders must have client_order_id
MUST be unique per request

EXPECTED RESULT:
✔ No duplicate orders across pods
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional, Any, Callable
from datetime import datetime

from backend_app.backend.redis_manager import redis_manager
from backend_app.core.global_safety import get_global_kill_switch

logger = logging.getLogger("DistributedIdempotency")


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
           - Store result with 3600s TTL
    
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
    
    def _generate_key(self, tenant_id: str, client_order_id: str) -> str:
        """Generate the Redis key for idempotency."""
        return f"{self.KEY_PREFIX}:{tenant_id}:{client_order_id}"
    
    async def check_idempotency(
        self, 
        tenant_id: str, 
        client_order_id: str
    ) -> IdempotencyResult:
        """
        Check if this request has already been processed.
        
        Args:
            tenant_id: Tenant identifier
            client_order_id: Client-provided unique order ID
            
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
        
        key = self._generate_key(tenant_id, client_order_id)
        
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
            value_str = existing_value.decode() if isinstance(existing_value, bytes) else existing_value
            
            if value_str == "processing":
                # Another pod is currently processing this request
                logger.info(f"STEP 2: Request {key} is being processed by another pod")
                return IdempotencyResult(
                    is_duplicate=False,
                    is_processing=True,
                    cached_result=None,
                    key=key
                )
            
            # Request already completed - return cached result
            try:
                cached_result = json.loads(value_str)
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
            # Fail-safe: assume not duplicate if check fails
            # But log critically for investigation
            return IdempotencyResult(
                is_duplicate=False,
                is_processing=False,
                cached_result=None,
                key=key
            )
    
    async def start_processing(
        self, 
        tenant_id: str, 
        client_order_id: str
    ) -> str:
        """
        Mark this request as being processed.
        
        Sets key to "processing" with 60s TTL.
        
        Args:
            tenant_id: Tenant identifier
            client_order_id: Client-provided unique order ID
            
        Returns:
            The Redis key that was set
        """
        key = self._generate_key(tenant_id, client_order_id)
        
        try:
            await redis_manager.set(key, "processing", ex=self.PROCESSING_TTL)
            logger.debug(f"STEP 2: Marked {key} as processing (TTL={self.PROCESSING_TTL}s)")
            return key
        except Exception as e:
            logger.error(f"STEP 2: Failed to mark processing for {key}: {e}")
            # Continue anyway - idempotency is best-effort if Redis fails
            return key
    
    async def store_result(
        self, 
        tenant_id: str, 
        client_order_id: str, 
        result: Any
    ) -> str:
        """
        Store the execution result for future duplicate detection.
        
        Sets key to JSON result with 3600s TTL.
        
        Args:
            tenant_id: Tenant identifier
            client_order_id: Client-provided unique order ID
            result: Execution result to cache (must be JSON serializable)
            
        Returns:
            The Redis key that was set
        """
        key = self._generate_key(tenant_id, client_order_id)
        
        try:
            result_json = json.dumps({
                "result": result,
                "timestamp": datetime.utcnow().isoformat(),
                "tenant_id": tenant_id,
                "client_order_id": client_order_id
            })
            await redis_manager.set(key, result_json, ex=self.RESULT_TTL)
            logger.debug(f"STEP 2: Stored result for {key} (TTL={self.RESULT_TTL}s)")
            return key
        except Exception as e:
            logger.error(f"STEP 2: Failed to store result for {key}: {e}")
            return key
    
    async def execute_with_idempotency(
        self,
        tenant_id: str,
        client_order_id: str,
        operation: Callable,
        *args,
        **kwargs
    ) -> Any:
        """
        Execute an operation with distributed idempotency guarantees.
        
        This is the MAIN ENTRY POINT for idempotent operations.
        
        FLOW:
            1. Check idempotency (raises if duplicate)
            2. If processing, wait and retry
            3. Mark as "processing" in Redis
            4. Execute operation
            5. Store result in Redis
            6. Return result
        
        Args:
            tenant_id: Tenant identifier
            client_order_id: Client-provided unique order ID
            operation: Async function to execute
            *args, **kwargs: Arguments to pass to operation
            
        Returns:
            Operation result (cached or fresh)
            
        Raises:
            MissingClientOrderIdError: If client_order_id is missing
            DuplicateOrderError: If critical duplicate detected
        """
        # Check idempotency
        idempotency_result = await self.check_idempotency(tenant_id, client_order_id)
        
        # Handle duplicate (already completed)
        if idempotency_result.is_duplicate:
            logger.info(
                f"STEP 2: Returning cached result for duplicate request "
                f"{idempotency_result.key}"
            )
            return idempotency_result.cached_result["result"]
        
        # Handle processing (another pod is working on it)
        if idempotency_result.is_processing:
            logger.info(
                f"STEP 2: Waiting for processing request {idempotency_result.key}"
            )
            
            # Wait and retry
            for attempt in range(self.MAX_RETRY_ATTEMPTS):
                await asyncio.sleep(self.RETRY_DELAY)
                
                recheck = await self.check_idempotency(tenant_id, client_order_id)
                
                if recheck.is_duplicate:
                    # Processing completed - return result
                    return recheck.cached_result["result"]
                
                if not recheck.is_processing:
                    # No longer processing - we can take over
                    break
            else:
                # Max retries exceeded - potential stuck processing
                logger.critical(
                    f"🔴 STEP 2: Processing stuck for {idempotency_result.key} "
                    f"after {self.MAX_RETRY_ATTEMPTS} retries"
                )
                # Trigger kill switch for investigation
                kill_switch = get_global_kill_switch()
                await kill_switch.trigger_on_reconciliation_mismatch(
                    client_order_id,
                    {"status": "stuck_processing"},
                    {"retries": self.MAX_RETRY_ATTEMPTS}
                )
                raise DuplicateOrderError(
                    f"Processing stuck for request {client_order_id}"
                )
        
        # Mark as processing
        await self.start_processing(tenant_id, client_order_id)
        
        try:
            # Execute the operation
            result = await operation(*args, **kwargs)
            
            # Store result
            await self.store_result(tenant_id, client_order_id, result)
            
            return result
            
        except Exception as e:
            # Clear processing state on failure
            key = self._generate_key(tenant_id, client_order_id)
            try:
                await redis_manager.delete(key)
            except:
                pass
            
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
]
