"""
Fill Deduplication Manager

Principal Institutional Execution Consistency Engineer

This module implements fill deduplication to prevent duplicate fills
from affecting positions multiple times. It provides:

- fill_id mandatory with hash validation
- Persistent fill registry (Redis)
- Duplicate fill rejection
- Replay-safe fill processing

CRITICAL: Same fill processed twice must affect position exactly once.
"""

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class FillRecord:
    """Immutable fill record with hash-based deduplication."""
    fill_id: str
    order_id: str
    tenant_id: str
    symbol: str
    side: str
    filled_quantity: Decimal
    fill_price: Decimal
    timestamp: datetime
    exchange_trade_id: str
    fill_hash: str
    fee: Decimal = Decimal("0")
    fee_currency: str = ""
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class FillDeduplicationManager:
    """
    Manages fill deduplication with persistent registry and hash validation.
    
    Ensures that the same fill (identified by hash) cannot be processed
    twice, preventing position duplication and PnL double-counting.
    """
    
    def __init__(
        self,
        redis_client: Any,
        fill_registry_ttl: int = 86400 * 30,  # 30 days
        enable_hash_validation: bool = True
    ):
        """
        Initialize fill deduplication manager.
        
        Args:
            redis_client: Redis client for persistent fill registry
            fill_registry_ttl: TTL for fill registry entries (seconds)
            enable_hash_validation: Enable cryptographic hash validation
        """
        self.redis = redis_client
        self.fill_registry_ttl = fill_registry_ttl
        self.enable_hash_validation = enable_hash_validation
        self._fill_registry_prefix = "fill_registry"
        
    def generate_fill_hash(
        self,
        order_id: str,
        symbol: str,
        side: str,
        filled_quantity: Decimal,
        fill_price: Decimal,
        timestamp: datetime,
        exchange_trade_id: str
    ) -> str:
        """
        Generate cryptographic hash for fill deduplication.
        
        Hash includes stable fill attributes (excluding timestamp for deduplication).
        Same fill data will always produce same hash regardless of processing time.
        
        Args:
            order_id: Order identifier
            symbol: Trading symbol
            side: Buy/Sell
            filled_quantity: Quantity filled
            fill_price: Fill price
            timestamp: Fill timestamp (excluded from hash for stability)
            exchange_trade_id: Exchange trade ID (primary deduplication key)
            
        Returns:
            SHA-256 hash hex string
        """
        # Normalize fill data for hashing
        # When exchange_trade_id is present, it is the primary deterministic key
        # When absent (e.g. simulated/paper or batched fills), timestamp differentiates distinct partial fills
        fill_data = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "filled_quantity": str(filled_quantity),
            "fill_price": str(fill_price),
            "exchange_trade_id": exchange_trade_id if exchange_trade_id else f"time_{timestamp.isoformat()}"
        }
        
        # Sort keys for deterministic hashing
        fill_string = json.dumps(fill_data, sort_keys=True)
        
        # Generate SHA-256 hash
        fill_hash = hashlib.sha256(fill_string.encode()).hexdigest()
        
        return fill_hash
    
    def generate_fill_id(
        self,
        order_id: str,
        fill_hash: str,
        timestamp: datetime
    ) -> str:
        """
        Generate deterministic fill_id from order_id and fill_hash.
        
        Args:
            order_id: Order identifier
            fill_hash: Fill content hash
            timestamp: Fill timestamp
            
        Returns:
            Deterministic fill_id
        """
        fill_id_str = f"{order_id}:{fill_hash}:{timestamp.isoformat()}"
        fill_id_hash = hashlib.sha256(fill_id_str.encode()).hexdigest()[:16]
        return f"fill_{fill_id_hash}"
    
    async def is_duplicate_fill(
        self,
        tenant_id: str,
        fill_hash: str
    ) -> bool:
        """
        Check if fill has already been processed.
        
        Args:
            tenant_id: Tenant identifier
            fill_hash: Fill content hash
            
        Returns:
            True if fill is duplicate, False otherwise
        """
        registry_key = f"{self._fill_registry_prefix}:{tenant_id}:{fill_hash}"
        
        try:
            exists = await self.redis.exists(registry_key)
            return bool(exists)
        except Exception as e:
            logger.error(f"[FillDeduplicationManager] Error checking duplicate fill: {e}")
            # Fail safe: assume not duplicate to prevent blocking
            return False
    
    async def register_fill(
        self,
        tenant_id: str,
        fill_record: FillRecord
    ) -> bool:
        """
        Register fill in persistent registry to prevent duplicates.
        
        Args:
            tenant_id: Tenant identifier
            fill_record: Fill record to register
            
        Returns:
            True if registration successful, False if duplicate or error
        """
        registry_key = f"{self._fill_registry_prefix}:{tenant_id}:{fill_record.fill_hash}"
        
        try:
            # Register fill with TTL atomically (only if not already registered)
            fill_data = {
                "fill_id": fill_record.fill_id,
                "order_id": fill_record.order_id,
                "symbol": fill_record.symbol,
                "side": fill_record.side,
                "filled_quantity": str(fill_record.filled_quantity),
                "fill_price": str(fill_record.fill_price),
                "timestamp": fill_record.timestamp.isoformat(),
                "exchange_trade_id": fill_record.exchange_trade_id,
                "fill_hash": fill_record.fill_hash,
                "registered_at": datetime.now(timezone.utc).isoformat()
            }
            
            res = await self.redis.set(
                registry_key,
                json.dumps(fill_data),
                ex=self.fill_registry_ttl,
                nx=True
            )
            
            if not res:
                logger.warning(
                    f"[FillDeduplicationManager] Duplicate fill detected (atomic NX): "
                    f"fill_id={fill_record.fill_id}, fill_hash={fill_record.fill_hash}"
                )
                return False
            
            logger.info(
                f"[FillDeduplicationManager] Fill registered: "
                f"fill_id={fill_record.fill_id}, fill_hash={fill_record.fill_hash}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[FillDeduplicationManager] Error registering fill: {e}")
            return False
    
    async def process_fill(
        self,
        tenant_id: str,
        order_id: str,
        symbol: str,
        side: str,
        filled_quantity: Decimal,
        fill_price: Decimal,
        timestamp: datetime,
        exchange_trade_id: str,
        fee: Decimal = Decimal("0"),
        fee_currency: str = "",
        metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Process fill with deduplication check.
        
        This is the main entry point for fill processing. It:
        1. Generates fill hash
        2. Checks for duplicate
        3. Registers fill if not duplicate
        4. Returns result indicating if fill was processed or rejected
        
        Args:
            tenant_id: Tenant identifier
            order_id: Order identifier
            symbol: Trading symbol
            side: Buy/Sell
            filled_quantity: Quantity filled
            fill_price: Fill price
            timestamp: Fill timestamp
            exchange_trade_id: Exchange trade ID
            fee: Trading fee
            fee_currency: Fee currency
            metadata: Additional metadata
            
        Returns:
            Dict with status and fill record if processed
        """
        # Generate fill hash
        if self.enable_hash_validation:
            fill_hash = self.generate_fill_hash(
                order_id=order_id,
                symbol=symbol,
                side=side,
                filled_quantity=filled_quantity,
                fill_price=fill_price,
                timestamp=timestamp,
                exchange_trade_id=exchange_trade_id
            )
        else:
            # Fallback: use exchange_trade_id as hash
            fill_hash = hashlib.sha256(exchange_trade_id.encode()).hexdigest()
        
        # Check for duplicate
        is_duplicate = await self.is_duplicate_fill(tenant_id, fill_hash)
        
        if is_duplicate:
            logger.warning(
                f"[FillDeduplicationManager] Duplicate fill rejected: "
                f"order_id={order_id}, exchange_trade_id={exchange_trade_id}, "
                f"fill_hash={fill_hash}"
            )
            return {
                "status": "duplicate_rejected",
                "fill_id": None,
                "fill_hash": fill_hash,
                "reason": "Fill already processed",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        
        # Generate fill_id
        fill_id = self.generate_fill_id(order_id, fill_hash, timestamp)
        
        # Create fill record
        fill_record = FillRecord(
            fill_id=fill_id,
            order_id=order_id,
            tenant_id=tenant_id,
            symbol=symbol,
            side=side,
            filled_quantity=filled_quantity,
            fill_price=fill_price,
            timestamp=timestamp,
            exchange_trade_id=exchange_trade_id,
            fill_hash=fill_hash,
            fee=fee,
            fee_currency=fee_currency,
            metadata=metadata or {}
        )
        
        # Register fill
        registered = await self.register_fill(tenant_id, fill_record)
        
        if not registered:
            logger.warning(
                f"[FillDeduplicationManager] Concurrent duplicate fill rejected on register: "
                f"fill_id={fill_id}, fill_hash={fill_hash}"
            )
            return {
                "status": "duplicate_rejected",
                "fill_id": fill_id,
                "fill_hash": fill_hash,
                "reason": "Fill already processed (atomic registry deduplication)",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        
        # Fill processed successfully
        logger.info(
            f"[FillDeduplicationManager] Fill processed: "
            f"fill_id={fill_id}, order_id={order_id}, "
            f"symbol={symbol}, side={side}, quantity={filled_quantity}, "
            f"price={fill_price}"
        )
        
        return {
            "status": "processed",
            "fill_id": fill_id,
            "fill_hash": fill_hash,
            "fill_record": asdict(fill_record),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def get_fill_registry_info(
        self,
        tenant_id: str,
        fill_hash: str
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve fill registry information.
        
        Args:
            tenant_id: Tenant identifier
            fill_hash: Fill content hash
            
        Returns:
            Fill registry data if exists, None otherwise
        """
        registry_key = f"{self._fill_registry_prefix}:{tenant_id}:{fill_hash}"
        
        try:
            data = await self.redis.get(registry_key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"[FillDeduplicationManager] Error getting fill registry: {e}")
            return None
    
    async def cleanup_expired_fills(self, tenant_id: str) -> int:
        """
        Cleanup expired fill registry entries (handled by Redis TTL).
        
        This is a no-op as Redis handles TTL automatically.
        Included for API completeness.
        
        Args:
            tenant_id: Tenant identifier
            
        Returns:
            Number of entries cleaned (always 0 for Redis TTL)
        """
        # Redis handles TTL automatically, no manual cleanup needed
        return 0
    
    async def get_fill_count(self, tenant_id: str) -> int:
        """
        Get count of fills in registry for tenant.
        
        Args:
            tenant_id: Tenant identifier
            
        Returns:
            Count of fills in registry
        """
        pattern = f"{self._fill_registry_prefix}:{tenant_id}:*"
        
        try:
            keys = []
            async for key in self.redis.scan_iter(match=pattern):
                keys.append(key)
            return len(keys)
        except Exception as e:
            logger.error(f"[FillDeduplicationManager] Error counting fills: {e}")
            return 0


# Factory function for dependency injection
def create_fill_deduplication_manager(
    redis_client: Any,
    fill_registry_ttl: int = 86400 * 30,
    enable_hash_validation: bool = True
) -> FillDeduplicationManager:
    """
    Factory function to create FillDeduplicationManager.
    
    Args:
        redis_client: Redis client
        fill_registry_ttl: TTL for fill registry entries
        enable_hash_validation: Enable cryptographic hash validation
        
    Returns:
        Configured FillDeduplicationManager instance
    """
    return FillDeduplicationManager(
        redis_client=redis_client,
        fill_registry_ttl=fill_registry_ttl,
        enable_hash_validation=enable_hash_validation
    )