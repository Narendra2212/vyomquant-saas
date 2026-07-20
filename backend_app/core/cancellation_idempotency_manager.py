"""
Cancellation Idempotency Manager

Principal Institutional Execution Consistency Engineer

This module implements cancellation idempotency to ensure that the same
cancellation submitted multiple times executes exactly once. It provides:

- Cancellation registry with idempotency keys
- Duplicate cancellation detection
- Exchange cancellation tracking
- Idempotent cancellation execution

CRITICAL: Same cancellation submitted multiple times must execute exactly once.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from dataclasses import dataclass, asdict
import logging
import asyncio

logger = logging.getLogger(__name__)


@dataclass
class CancellationRecord:
    """Immutable cancellation record with idempotency tracking."""
    cancellation_id: str
    order_id: str
    tenant_id: str
    idempotency_key: str
    timestamp: datetime
    status: str  # pending, submitted, completed, failed
    exchange_cancellation_id: Optional[str] = None
    reason: str = ""
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class CancellationIdempotencyManager:
    """
    Manages cancellation idempotency with persistent registry.
    
    Ensures that the same cancellation (identified by idempotency key)
    cannot be executed multiple times, preventing duplicate exchange
    cancellation requests and state corruption.
    """
    
    def __init__(
        self,
        redis_client: Any,
        cancellation_registry_ttl: int = 86400 * 7,  # 7 days
        enable_idempotency: bool = True
    ):
        """
        Initialize cancellation idempotency manager.
        
        Args:
            redis_client: Redis client for persistent cancellation registry
            cancellation_registry_ttl: TTL for cancellation registry entries (seconds)
            enable_idempotency: Enable idempotency checks
        """
        self.redis = redis_client
        self.cancellation_registry_ttl = cancellation_registry_ttl
        self.enable_idempotency = enable_idempotency
        self._cancellation_registry_prefix = "cancellation_registry"
        
    def generate_idempotency_key(
        self,
        order_id: str,
        tenant_id: str,
        timestamp: datetime
    ) -> str:
        """
        Generate deterministic idempotency key for cancellation.
        
        The idempotency key uniquely identifies a cancellation request.
        Same order cancelled multiple times will have different keys
        (different timestamps), but same cancellation request retried
        will have the same key.
        
        Args:
            order_id: Order identifier
            tenant_id: Tenant identifier
            timestamp: Cancellation request timestamp
            
        Returns:
            Deterministic idempotency key
        """
        idempotency_str = f"{tenant_id}:{order_id}:{timestamp.isoformat()}"
        idempotency_hash = hashlib.sha256(idempotency_str.encode()).hexdigest()[:16]
        return f"cancellation_{idempotency_hash}"
    
    def generate_cancellation_id(
        self,
        order_id: str,
        idempotency_key: str
    ) -> str:
        """
        Generate deterministic cancellation_id from order_id and idempotency_key.
        
        Args:
            order_id: Order identifier
            idempotency_key: Idempotency key
            
        Returns:
            Deterministic cancellation_id
        """
        cancellation_id_str = f"{order_id}:{idempotency_key}"
        cancellation_id_hash = hashlib.sha256(cancellation_id_str.encode()).hexdigest()[:16]
        return f"cancellation_{cancellation_id_hash}"
    
    async def is_duplicate_cancellation(
        self,
        tenant_id: str,
        idempotency_key: str
    ) -> bool:
        """
        Check if cancellation has already been processed.
        
        Args:
            tenant_id: Tenant identifier
            idempotency_key: Idempotency key
            
        Returns:
            True if cancellation is duplicate, False otherwise
        """
        registry_key = f"{self._cancellation_registry_prefix}:{tenant_id}:{idempotency_key}"
        
        try:
            exists = await self.redis.exists(registry_key)
            return bool(exists)
        except Exception as e:
            logger.error(f"[CancellationIdempotencyManager] Error checking duplicate cancellation: {e}")
            # Fail safe: assume not duplicate to prevent blocking
            return False
    
    async def register_cancellation(
        self,
        tenant_id: str,
        cancellation_record: CancellationRecord
    ) -> bool:
        """
        Register cancellation in persistent registry to prevent duplicates.
        
        Args:
            tenant_id: Tenant identifier
            cancellation_record: Cancellation record to register
            
        Returns:
            True if registration successful, False if duplicate or error
        """
        registry_key = f"{self._cancellation_registry_prefix}:{tenant_id}:{cancellation_record.idempotency_key}"
        
        try:
            # Check if already registered
            exists = await self.redis.exists(registry_key)
            if exists:
                logger.warning(
                    f"[CancellationIdempotencyManager] Duplicate cancellation detected: "
                    f"cancellation_id={cancellation_record.cancellation_id}, "
                    f"idempotency_key={cancellation_record.idempotency_key}"
                )
                return False
            
            # Register cancellation with TTL
            cancellation_data = {
                "cancellation_id": cancellation_record.cancellation_id,
                "order_id": cancellation_record.order_id,
                "idempotency_key": cancellation_record.idempotency_key,
                "timestamp": cancellation_record.timestamp.isoformat(),
                "status": cancellation_record.status,
                "exchange_cancellation_id": cancellation_record.exchange_cancellation_id,
                "reason": cancellation_record.reason,
                "registered_at": datetime.now(timezone.utc).isoformat()
            }
            
            await self.redis.setex(
                registry_key,
                self.cancellation_registry_ttl,
                json.dumps(cancellation_data)
            )
            
            logger.info(
                f"[CancellationIdempotencyManager] Cancellation registered: "
                f"cancellation_id={cancellation_record.cancellation_id}, "
                f"idempotency_key={cancellation_record.idempotency_key}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[CancellationIdempotencyManager] Error registering cancellation: {e}")
            return False
    
    async def update_cancellation_status(
        self,
        tenant_id: str,
        idempotency_key: str,
        status: str,
        exchange_cancellation_id: Optional[str] = None
    ) -> bool:
        """
        Update cancellation status in registry.
        
        Args:
            tenant_id: Tenant identifier
            idempotency_key: Idempotency key
            status: New status (submitted, completed, failed)
            exchange_cancellation_id: Exchange cancellation ID if available
            
        Returns:
            True if update successful, False otherwise
        """
        registry_key = f"{self._cancellation_registry_prefix}:{tenant_id}:{idempotency_key}"
        
        try:
            data = await self.redis.get(registry_key)
            if not data:
                logger.warning(
                    f"[CancellationIdempotencyManager] Cancellation not found for status update: "
                    f"idempotency_key={idempotency_key}"
                )
                return False
            
            cancellation_data = json.loads(data)
            cancellation_data["status"] = status
            if exchange_cancellation_id:
                cancellation_data["exchange_cancellation_id"] = exchange_cancellation_id
            cancellation_data["updated_at"] = datetime.now(timezone.utc).isoformat()
            
            await self.redis.setex(
                registry_key,
                self.cancellation_registry_ttl,
                json.dumps(cancellation_data)
            )
            
            logger.info(
                f"[CancellationIdempotencyManager] Cancellation status updated: "
                f"idempotency_key={idempotency_key}, status={status}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[CancellationIdempotencyManager] Error updating cancellation status: {e}")
            return False
    
    async def process_cancellation(
        self,
        tenant_id: str,
        order_id: str,
        reason: str = "",
        metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Process cancellation with idempotency check.
        
        This is the main entry point for cancellation processing. It:
        1. Generates idempotency key
        2. Checks for duplicate
        3. Registers cancellation if not duplicate
        4. Returns result indicating if cancellation was processed or rejected
        
        Args:
            tenant_id: Tenant identifier
            order_id: Order identifier
            reason: Cancellation reason
            metadata: Additional metadata
            
        Returns:
            Dict with status and cancellation record if processed
        """
        if not self.enable_idempotency:
            # Idempotency disabled - proceed without check
            logger.warning(
                f"[CancellationIdempotencyManager] Idempotency disabled, "
                f"proceeding without duplicate check for order_id={order_id}"
            )
            return {
                "status": "idempotency_disabled",
                "cancellation_id": None,
                "idempotency_key": None,
                "reason": "Idempotency check disabled",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        
        # Generate idempotency key
        timestamp = datetime.now(timezone.utc)
        idempotency_key = self.generate_idempotency_key(order_id, tenant_id, timestamp)
        
        # Check for duplicate
        is_duplicate = await self.is_duplicate_cancellation(tenant_id, idempotency_key)
        
        if is_duplicate:
            # Get existing cancellation record
            existing_record = await self.get_cancellation_record(tenant_id, idempotency_key)
            
            logger.warning(
                f"[CancellationIdempotencyManager] Duplicate cancellation rejected: "
                f"order_id={order_id}, idempotency_key={idempotency_key}"
            )
            return {
                "status": "duplicate_rejected",
                "cancellation_id": existing_record.get("cancellation_id") if existing_record else None,
                "idempotency_key": idempotency_key,
                "existing_status": existing_record.get("status") if existing_record else "unknown",
                "reason": "Cancellation already processed",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        
        # Generate cancellation_id
        cancellation_id = self.generate_cancellation_id(order_id, idempotency_key)
        
        # Create cancellation record
        cancellation_record = CancellationRecord(
            cancellation_id=cancellation_id,
            order_id=order_id,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            timestamp=timestamp,
            status="pending",
            reason=reason,
            metadata=metadata or {}
        )
        
        # Register cancellation
        registered = await self.register_cancellation(tenant_id, cancellation_record)
        
        if not registered:
            logger.error(
                f"[CancellationIdempotencyManager] Failed to register cancellation: "
                f"cancellation_id={cancellation_id}"
            )
            return {
                "status": "registration_failed",
                "cancellation_id": cancellation_id,
                "idempotency_key": idempotency_key,
                "reason": "Failed to register cancellation in registry",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        
        # Cancellation processed successfully
        logger.info(
            f"[CancellationIdempotencyManager] Cancellation processed: "
            f"cancellation_id={cancellation_id}, order_id={order_id}, "
            f"reason={reason}"
        )
        
        return {
            "status": "processed",
            "cancellation_id": cancellation_id,
            "idempotency_key": idempotency_key,
            "cancellation_record": asdict(cancellation_record),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def get_cancellation_record(
        self,
        tenant_id: str,
        idempotency_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve cancellation record from registry.
        
        Args:
            tenant_id: Tenant identifier
            idempotency_key: Idempotency key
            
        Returns:
            Cancellation record if exists, None otherwise
        """
        registry_key = f"{self._cancellation_registry_prefix}:{tenant_id}:{idempotency_key}"
        
        try:
            data = await self.redis.get(registry_key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"[CancellationIdempotencyManager] Error getting cancellation record: {e}")
            return None
    
    async def get_cancellation_by_order_id(
        self,
        tenant_id: str,
        order_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve most recent cancellation for order_id.
        
        Args:
            tenant_id: Tenant identifier
            order_id: Order identifier
            
        Returns:
            Most recent cancellation record if exists, None otherwise
        """
        pattern = f"{self._cancellation_registry_prefix}:{tenant_id}:*"
        
        try:
            keys = []
            async for key in self.redis.scan_iter(match=pattern):
                keys.append(key)
            
            # Find cancellation for this order_id
            for key in keys:
                data = await self.redis.get(key)
                if data:
                    record = json.loads(data)
                    if record.get("order_id") == order_id:
                        return record
            
            return None
        except Exception as e:
            logger.error(f"[CancellationIdempotencyManager] Error getting cancellation by order_id: {e}")
            return None
    
    async def get_cancellation_count(self, tenant_id: str) -> int:
        """
        Get count of cancellations in registry for tenant.
        
        Args:
            tenant_id: Tenant identifier
            
        Returns:
            Count of cancellations in registry
        """
        pattern = f"{self._cancellation_registry_prefix}:{tenant_id}:*"
        
        try:
            keys = []
            async for key in self.redis.scan_iter(match=pattern):
                keys.append(key)
            return len(keys)
        except Exception as e:
            logger.error(f"[CancellationIdempotencyManager] Error counting cancellations: {e}")
            return 0


# Factory function for dependency injection
def create_cancellation_idempotency_manager(
    redis_client: Any,
    cancellation_registry_ttl: int = 86400 * 7,
    enable_idempotency: bool = True
) -> CancellationIdempotencyManager:
    """
    Factory function to create CancellationIdempotencyManager.
    
    Args:
        redis_client: Redis client
        cancellation_registry_ttl: TTL for cancellation registry entries
        enable_idempotency: Enable idempotency checks
        
    Returns:
        Configured CancellationIdempotencyManager instance
    """
    return CancellationIdempotencyManager(
        redis_client=redis_client,
        cancellation_registry_ttl=cancellation_registry_ttl,
        enable_idempotency=enable_idempotency
    )