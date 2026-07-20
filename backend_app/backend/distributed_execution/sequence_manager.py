"""
Sequence Manager for Deterministic Event Ordering

Provides monotonic sequence number management with persistence,
gap detection, and recovery capabilities for immutable journal.

Author: Principal Distributed Execution Engineer
"""

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import logging

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("sequence_manager")


@dataclass
class SequenceMetadata:
    """Metadata for sequence number management."""
    tenant_id: str
    strategy_id: Optional[str]
    current_sequence: int
    last_updated: datetime
    gap_detected: bool = False
    gap_start: Optional[int] = None
    gap_end: Optional[int] = None


class SequenceManager:
    """Deterministic sequence number manager with persistence."""
    
    def __init__(self):
        self.redis = redis_manager
        
        # Sequence configuration
        self.sequence_prefix = "sequence_counter"
        self.gap_prefix = "sequence_gap"
        self.metadata_prefix = "sequence_metadata"
        
        # TTL configuration
        self.sequence_ttl = 86400 * 365  # 1 year
        self.gap_ttl = 86400 * 30    # 30 days
        self.metadata_ttl = 86400 * 365  # 1 year
        
        # In-memory cache for performance
        self.sequence_cache: Dict[str, int] = {}
        self.metadata_cache: Dict[str, SequenceMetadata] = {}
        
        logger.info("Sequence manager initialized")
    
    async def initialize(self) -> bool:
        """Initialize sequence manager with existing sequences."""
        try:
            # Test Redis connection
            await self.redis.ping()
            
            # Initialize global sequence
            await self._get_next_sequence_id("global")
            
            logger.info("Sequence manager initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize sequence manager: {e}")
            return False
    
    async def get_next_sequence_id(self, tenant_id: str, 
                                strategy_id: Optional[str] = None) -> int:
        """Get next sequence ID for tenant/strategy combination."""
        try:
            # Build cache key
            if strategy_id:
                cache_key = f"{tenant_id}:{strategy_id}"
                sequence_key = f"{self.sequence_prefix}:{tenant_id}:{strategy_id}"
                metadata_key = f"{self.metadata_prefix}:{tenant_id}:{strategy_id}"
            else:
                cache_key = tenant_id
                sequence_key = f"{self.sequence_prefix}:{tenant_id}"
                metadata_key = f"{self.metadata_prefix}:{tenant_id}"
            
            # Check cache first
            if cache_key in self.sequence_cache:
                current_sequence = self.sequence_cache[cache_key]
                next_sequence = current_sequence + 1
                
                # Update cache
                self.sequence_cache[cache_key] = next_sequence
                
                # Update Redis asynchronously
                asyncio.create_task(self._update_sequence_async(sequence_key, next_sequence, metadata_key, tenant_id, strategy_id))
                
                return next_sequence
            
            # Get from Redis
            current_sequence = await self.redis.get(sequence_key)
            if current_sequence is None:
                current_sequence = 0
            else:
                current_sequence = int(current_sequence)
            
            next_sequence = current_sequence + 1
            
            # Update cache
            self.sequence_cache[cache_key] = next_sequence
            
            # Update Redis
            pipe = self.redis.pipeline()
            pipe.set(sequence_key, next_sequence)
            pipe.expire(sequence_key, self.sequence_ttl)
            
            # Update metadata
            metadata = SequenceMetadata(
                tenant_id=tenant_id,
                strategy_id=strategy_id,
                current_sequence=next_sequence,
                last_updated=datetime.now(timezone.utc)
            )
            pipe.set(metadata_key, metadata.__dict__)
            pipe.expire(metadata_key, self.metadata_ttl)
            
            await pipe.execute()
            
            logger.debug(f"Generated sequence {next_sequence} for {cache_key}")
            return next_sequence
            
        except Exception as e:
            logger.error(f"Failed to get next sequence ID for {tenant_id}: {e}")
            raise
    
    async def get_current_sequence_id(self, tenant_id: str, 
                                   strategy_id: Optional[str] = None) -> int:
        """Get current sequence ID for tenant/strategy combination."""
        try:
            # Build cache key
            if strategy_id:
                cache_key = f"{tenant_id}:{strategy_id}"
                sequence_key = f"{self.sequence_prefix}:{tenant_id}:{strategy_id}"
            else:
                cache_key = tenant_id
                sequence_key = f"{self.sequence_prefix}:{tenant_id}"
            
            # Check cache first
            if cache_key in self.sequence_cache:
                return self.sequence_cache[cache_key]
            
            # Get from Redis
            current_sequence = await self.redis.get(sequence_key)
            if current_sequence is None:
                return 0
            else:
                return int(current_sequence)
                
        except Exception as e:
            logger.error(f"Failed to get current sequence ID for {tenant_id}: {e}")
            return 0
    
    async def validate_sequence_continuity(self, tenant_id: str, 
                                       expected_sequence: int,
                                       strategy_id: Optional[str] = None) -> bool:
        """Validate sequence continuity and detect gaps."""
        try:
            current_sequence = await self.get_current_sequence_id(tenant_id, strategy_id)
            
            if expected_sequence != current_sequence + 1:
                logger.warning(f"Sequence gap detected for {tenant_id}: expected {expected_sequence}, current {current_sequence}")
                
                # Record gap
                await self._record_sequence_gap(tenant_id, strategy_id, expected_sequence, current_sequence)
                
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to validate sequence continuity: {e}")
            return False
    
    async def get_sequence_gaps(self, tenant_id: str, 
                               strategy_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get sequence gaps for tenant/strategy combination."""
        try:
            if strategy_id:
                gap_key = f"{self.gap_prefix}:{tenant_id}:{strategy_id}"
            else:
                gap_key = f"{self.gap_prefix}:{tenant_id}"
            
            # Get gaps from Redis
            gaps = []
            gap_entries = await self.redis.lrange(gap_key, 0, -1)
            
            for entry in gap_entries:
                try:
                    gap_data = json.loads(entry)
                    gaps.append(gap_data)
                except Exception as e:
                    logger.error(f"Failed to parse gap entry: {e}")
                    continue
            
            return gaps
            
        except Exception as e:
            logger.error(f"Failed to get sequence gaps: {e}")
            return []
    
    async def resolve_sequence_gap(self, tenant_id: str, gap_start: int,
                               gap_end: int, strategy_id: Optional[str] = None) -> bool:
        """Resolve sequence gap by filling missing sequences."""
        try:
            # Validate gap
            if gap_start >= gap_end:
                logger.error(f"Invalid gap range: {gap_start} to {gap_end}")
                return False
            
            # Update current sequence to gap_end
            if strategy_id:
                sequence_key = f"{self.sequence_prefix}:{tenant_id}:{strategy_id}"
                metadata_key = f"{self.metadata_prefix}:{tenant_id}:{strategy_id}"
                cache_key = f"{tenant_id}:{strategy_id}"
            else:
                sequence_key = f"{self.sequence_prefix}:{tenant_id}"
                metadata_key = f"{self.metadata_prefix}:{tenant_id}"
                cache_key = tenant_id
            
            # Update sequence
            pipe = self.redis.pipeline()
            pipe.set(sequence_key, gap_end)
            pipe.expire(sequence_key, self.sequence_ttl)
            
            # Update metadata
            metadata = SequenceMetadata(
                tenant_id=tenant_id,
                strategy_id=strategy_id,
                current_sequence=gap_end,
                last_updated=datetime.now(timezone.utc),
                gap_detected=False
            )
            pipe.set(metadata_key, metadata.__dict__)
            pipe.expire(metadata_key, self.metadata_ttl)
            
            # Update cache
            self.sequence_cache[cache_key] = gap_end
            
            # Remove gap record
            if strategy_id:
                gap_key = f"{self.gap_prefix}:{tenant_id}:{strategy_id}"
            else:
                gap_key = f"{self.gap_prefix}:{tenant_id}"
            
            pipe.lrem(gap_key, 1, json.dumps({
                "gap_start": gap_start,
                "gap_end": gap_end,
                "resolved_at": datetime.now(timezone.utc).isoformat()
            }))
            
            await pipe.execute()
            
            logger.info(f"Resolved sequence gap {gap_start}-{gap_end} for {tenant_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to resolve sequence gap: {e}")
            return False
    
    async def get_sequence_metadata(self, tenant_id: str, 
                                 strategy_id: Optional[str] = None) -> Optional[SequenceMetadata]:
        """Get sequence metadata for tenant/strategy combination."""
        try:
            if strategy_id:
                metadata_key = f"{self.metadata_prefix}:{tenant_id}:{strategy_id}"
                cache_key = f"{tenant_id}:{strategy_id}"
            else:
                metadata_key = f"{self.metadata_prefix}:{tenant_id}"
                cache_key = tenant_id
            
            # Check cache first
            if cache_key in self.metadata_cache:
                return self.metadata_cache[cache_key]
            
            # Get from Redis
            metadata_data = await self.redis.get(metadata_key)
            if metadata_data is None:
                return None
            
            metadata_dict = json.loads(metadata_data)
            metadata = SequenceMetadata(**metadata_dict)
            
            # Update cache
            self.metadata_cache[cache_key] = metadata
            
            return metadata
            
        except Exception as e:
            logger.error(f"Failed to get sequence metadata: {e}")
            return None
    
    async def _update_sequence_async(self, sequence_key: str, next_sequence: int, 
                                 metadata_key: str, tenant_id: str, 
                                 strategy_id: Optional[str]):
        """Update sequence asynchronously."""
        try:
            pipe = self.redis.pipeline()
            
            # Update sequence
            pipe.set(sequence_key, next_sequence)
            pipe.expire(sequence_key, self.sequence_ttl)
            
            # Update metadata
            metadata = SequenceMetadata(
                tenant_id=tenant_id,
                strategy_id=strategy_id,
                current_sequence=next_sequence,
                last_updated=datetime.now(timezone.utc)
            )
            pipe.set(metadata_key, metadata.__dict__)
            pipe.expire(metadata_key, self.metadata_ttl)
            
            await pipe.execute()
            
        except Exception as e:
            logger.error(f"Failed to update sequence asynchronously: {e}")
    
    async def _record_sequence_gap(self, tenant_id: str, strategy_id: Optional[str],
                               expected_sequence: int, current_sequence: int):
        """Record sequence gap for tracking."""
        try:
            if strategy_id:
                gap_key = f"{self.gap_prefix}:{tenant_id}:{strategy_id}"
            else:
                gap_key = f"{self.gap_prefix}:{tenant_id}"
            
            gap_data = {
                "gap_start": current_sequence + 1,
                "gap_end": expected_sequence - 1,
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "expected_sequence": expected_sequence,
                "current_sequence": current_sequence
            }
            
            # Store gap
            await self.redis.lpush(gap_key, json.dumps(gap_data))
            await self.redis.expire(gap_key, self.gap_ttl)
            
            logger.warning(f"Recorded sequence gap {gap_data['gap_start']}-{gap_data['gap_end']} for {tenant_id}")
            
        except Exception as e:
            logger.error(f"Failed to record sequence gap: {e}")
    
    async def get_sequence_statistics(self, tenant_id: str, 
                                   strategy_id: Optional[str] = None) -> Dict[str, Any]:
        """Get sequence statistics for tenant/strategy combination."""
        try:
            current_sequence = await self.get_current_sequence_id(tenant_id, strategy_id)
            gaps = await self.get_sequence_gaps(tenant_id, strategy_id)
            metadata = await self.get_sequence_metadata(tenant_id, strategy_id)
            
            return {
                "tenant_id": tenant_id,
                "strategy_id": strategy_id,
                "current_sequence": current_sequence,
                "gap_count": len(gaps),
                "gaps": gaps,
                "metadata": metadata.__dict__ if metadata else None,
                "last_updated": metadata.last_updated if metadata else None
            }
            
        except Exception as e:
            logger.error(f"Failed to get sequence statistics: {e}")
            return {}
    
    async def reset_sequence(self, tenant_id: str, strategy_id: Optional[str] = None) -> bool:
        """Reset sequence for tenant/strategy combination."""
        try:
            if strategy_id:
                sequence_key = f"{self.sequence_prefix}:{tenant_id}:{strategy_id}"
                metadata_key = f"{self.metadata_prefix}:{tenant_id}:{strategy_id}"
                cache_key = f"{tenant_id}:{strategy_id}"
            else:
                sequence_key = f"{self.sequence_prefix}:{tenant_id}"
                metadata_key = f"{self.metadata_prefix}:{tenant_id}"
                cache_key = tenant_id
            
            # Reset sequence to 0
            pipe = self.redis.pipeline()
            pipe.set(sequence_key, 0)
            pipe.expire(sequence_key, self.sequence_ttl)
            
            # Update metadata
            metadata = SequenceMetadata(
                tenant_id=tenant_id,
                strategy_id=strategy_id,
                current_sequence=0,
                last_updated=datetime.now(timezone.utc)
            )
            pipe.set(metadata_key, metadata.__dict__)
            pipe.expire(metadata_key, self.metadata_ttl)
            
            # Update cache
            self.sequence_cache[cache_key] = 0
            self.metadata_cache[cache_key] = metadata
            
            await pipe.execute()
            
            logger.info(f"Reset sequence for {tenant_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to reset sequence: {e}")
            return False


# Global sequence manager instance
sequence_manager = SequenceManager()
