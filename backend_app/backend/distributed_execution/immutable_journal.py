"""
Immutable Execution Journal

Institutional-grade append-only execution journal with deterministic
event ordering, cryptographic integrity verification, and replay-safe
persistence for HFT trading infrastructure.

Author: Principal Distributed Execution Engineer
"""

import asyncio
import json
import time
import uuid
import hashlib
import hmac
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field, asdict
import logging

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("immutable_journal")


class EventType(Enum):
    """Immutable event types for execution journal."""
    # Order Events
    ORDER_SUBMITTED = "order_submitted"
    ORDER_ACCEPTED = "order_accepted"
    ORDER_REJECTED = "order_rejected"
    ORDER_FILLED = "order_filled"
    ORDER_PARTIALLY_FILLED = "order_partially_filled"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_EXPIRED = "order_expired"
    
    # Position Events
    POSITION_OPENED = "position_opened"
    POSITION_UPDATED = "position_updated"
    POSITION_CLOSED = "position_closed"
    
    # Risk Events
    RISK_VALIDATED = "risk_validated"
    RISK_BLOCKED = "risk_blocked"
    RISK_LIMIT_EXCEEDED = "risk_limit_exceeded"
    
    # Execution Events
    EXECUTION_STARTED = "execution_started"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"
    
    # System Events
    SYSTEM_CHECKPOINT = "system_checkpoint"
    SYSTEM_SNAPSHOT = "system_snapshot"
    SYSTEM_RECOVERY = "system_recovery"


@dataclass
class EventHeader:
    """Immutable event header with metadata."""
    event_id: str
    event_type: EventType
    timestamp: datetime
    sequence_id: int
    version: int
    tenant_id: str
    strategy_id: str
    bot_id: str
    signal_id: Optional[str] = None
    causation_id: Optional[str] = None
    correlation_id: Optional[str] = None


@dataclass
class EventSignature:
    """Immutable event signature for integrity verification."""
    event_hash: str
    previous_event_hash: str
    signature: str
    signature_timestamp: datetime
    signature_version: int = 1


@dataclass
class EventMetadata:
    """Immutable event metadata for audit and tracing."""
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None


@dataclass
class ExecutionEvent:
    """Immutable execution event with complete integrity."""
    header: EventHeader
    payload: Dict[str, Any]
    signature: EventSignature
    metadata: EventMetadata
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "header": asdict(self.header),
            "payload": self.payload,
            "signature": asdict(self.signature),
            "metadata": asdict(self.metadata)
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ExecutionEvent':
        """Create from dictionary for deserialization."""
        header_data = data["header"]
        header = EventHeader(
            event_id=header_data["event_id"],
            event_type=EventType(header_data["event_type"]),
            timestamp=datetime.fromisoformat(header_data["timestamp"]),
            sequence_id=header_data["sequence_id"],
            version=header_data["version"],
            tenant_id=header_data["tenant_id"],
            strategy_id=header_data["strategy_id"],
            bot_id=header_data["bot_id"],
            signal_id=header_data.get("signal_id"),
            causation_id=header_data.get("causation_id"),
            correlation_id=header_data.get("correlation_id")
        )
        
        signature_data = data["signature"]
        signature = EventSignature(
            event_hash=signature_data["event_hash"],
            previous_event_hash=signature_data["previous_event_hash"],
            signature=signature_data["signature"],
            signature_timestamp=datetime.fromisoformat(signature_data["signature_timestamp"]),
            signature_version=signature_data.get("signature_version", 1)
        )
        
        metadata_data = data.get("metadata", {})
        metadata = EventMetadata(
            user_id=metadata_data.get("user_id"),
            session_id=metadata_data.get("session_id"),
            ip_address=metadata_data.get("ip_address"),
            user_agent=metadata_data.get("user_agent"),
            request_id=metadata_data.get("request_id"),
            trace_id=metadata_data.get("trace_id"),
            span_id=metadata_data.get("span_id")
        )
        
        return cls(
            header=header,
            payload=data["payload"],
            signature=signature,
            metadata=metadata
        )


class ImmutableJournal:
    """Immutable append-only execution journal with integrity verification."""
    
    def __init__(self):
        self.redis = redis_manager
        
        # Journal configuration
        self.journal_prefix = "execution_journal"
        self.sequence_prefix = "journal_sequence"
        self.index_prefix = "journal_index"
        self.checkpoint_prefix = "journal_checkpoint"
        
        # Event signing configuration
        self.signing_key = b"immutable_journal_signing_key_v1"
        self.signature_version = 1
        
        # TTL configuration
        self.event_ttl = 86400 * 365  # 1 year
        self.index_ttl = 86400 * 90    # 90 days
        self.checkpoint_ttl = 86400 * 730  # 2 years
        
        logger.info("Immutable journal initialized")
    
    async def initialize(self) -> bool:
        """Initialize journal with sequence counters and indexes."""
        try:
            # Create sequence counters for each tenant
            test_tenant = "test_tenant"
            await self._get_next_sequence_id(test_tenant)
            
            logger.info("Immutable journal initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize immutable journal: {e}")
            return False
    
    async def append_event(self, event: ExecutionEvent) -> bool:
        """Append event to immutable journal with integrity verification."""
        try:
            # Generate event hash
            event_hash = await self._generate_event_hash(event)
            
            # Get previous event hash
            previous_hash = await self._get_previous_event_hash(
                event.header.tenant_id, 
                event.header.sequence_id - 1
            )
            
            # Generate event signature
            signature = await self._generate_event_signature(
                event, event_hash, previous_hash
            )
            
            # Update event with hash and signature
            event.signature.event_hash = event_hash
            event.signature.previous_event_hash = previous_hash
            event.signature.signature = signature
            
            # Store event in Redis Stream
            stream_key = f"{self.journal_prefix}:{event.header.tenant_id}"
            event_data = event.to_dict()
            
            # Use Redis Stream for append-only storage
            await self.redis.xadd(
                stream_key,
                event_data,
                maxlen=1000000,  # Stream max length
                approximate=True
            )
            
            # Update sequence counter
            await self._update_sequence_counter(event.header.tenant_id, event.header.sequence_id)
            
            # Update indexes
            await self._update_indexes(event)
            
            # Set TTL for stream
            await self.redis.expire(stream_key, self.event_ttl)
            
            logger.debug(f"Appended event {event.header.event_id} to immutable journal")
            return True
            
        except Exception as e:
            logger.error(f"Failed to append event {event.header.event_id}: {e}")
            return False
    
    async def get_events(self, tenant_id: str, start_sequence: Optional[int] = None, 
                      end_sequence: Optional[int] = None, event_type: Optional[EventType] = None,
                      limit: int = 1000) -> List[ExecutionEvent]:
        """Get events from immutable journal with filtering options."""
        try:
            stream_key = f"{self.journal_prefix}:{tenant_id}"
            
            # Build Redis Stream read options
            if start_sequence is not None:
                start_id = f"{start_sequence}-0"
            else:
                start_id = "0"
            
            # Read events from stream
            events = []
            stream_entries = await self.redis.xrevrange(
                stream_key,
                max=end_sequence,
                min=start_id,
                count=limit
            )
            
            for entry_id, entry_data in stream_entries:
                try:
                    event = ExecutionEvent.from_dict(entry_data)
                    
                    # Apply filters
                    if event_type and event.header.event_type != event_type:
                        continue
                    
                    events.append(event)
                    
                except Exception as e:
                    logger.error(f"Failed to deserialize event {entry_id}: {e}")
                    continue
            
            # Reverse to chronological order
            events.reverse()
            
            logger.debug(f"Retrieved {len(events)} events from immutable journal")
            return events
            
        except Exception as e:
            logger.error(f"Failed to get events from immutable journal: {e}")
            return []
    
    async def verify_event_integrity(self, event: ExecutionEvent) -> bool:
        """Verify event integrity using cryptographic signatures."""
        try:
            # Generate event hash
            expected_hash = await self._generate_event_hash(event)
            
            # Compare with stored hash
            if event.signature.event_hash != expected_hash:
                logger.error(f"Event hash mismatch for {event.header.event_id}")
                return False
            
            # Verify previous event hash
            if event.header.sequence_id > 1:
                expected_previous_hash = await self._get_previous_event_hash(
                    event.header.tenant_id, 
                    event.header.sequence_id - 1
                )
                if event.signature.previous_event_hash != expected_previous_hash:
                    logger.error(f"Previous event hash mismatch for {event.header.event_id}")
                    return False
            
            # Verify signature
            expected_signature = await self._generate_event_signature(
                event, expected_hash, event.signature.previous_event_hash
            )
            if event.signature.signature != expected_signature:
                logger.error(f"Event signature mismatch for {event.header.event_id}")
                return False
            
            logger.debug(f"Event integrity verified for {event.header.event_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to verify event integrity: {e}")
            return False
    
    async def _get_next_sequence_id(self, tenant_id: str) -> int:
        """Get next sequence ID for tenant."""
        try:
            sequence_key = f"{self.sequence_prefix}:{tenant_id}"
            sequence_id = await self.redis.incr(sequence_key)
            
            # Set TTL for sequence counter
            await self.redis.expire(sequence_key, self.event_ttl)
            
            return sequence_id
            
        except Exception as e:
            logger.error(f"Failed to get next sequence ID for {tenant_id}: {e}")
            raise
    
    async def _update_sequence_counter(self, tenant_id: str, sequence_id: int):
        """Update sequence counter for tenant."""
        try:
            sequence_key = f"{self.sequence_prefix}:{tenant_id}"
            current_sequence = await self.redis.get(sequence_key)
            
            if current_sequence is None or int(current_sequence) < sequence_id:
                await self.redis.set(sequence_key, sequence_id)
                await self.redis.expire(sequence_key, self.event_ttl)
            
        except Exception as e:
            logger.error(f"Failed to update sequence counter for {tenant_id}: {e}")
    
    async def _generate_event_hash(self, event: ExecutionEvent) -> str:
        """Generate SHA-256 hash of event content."""
        try:
            # Create canonical representation
            canonical_data = {
                "header": asdict(event.header),
                "payload": event.payload,
                "metadata": asdict(event.metadata)
            }
            
            # Generate hash
            hash_input = json.dumps(canonical_data, sort_keys=True, separators=(',', ':'))
            event_hash = hashlib.sha256(hash_input.encode('utf-8')).hexdigest()
            
            return event_hash
            
        except Exception as e:
            logger.error(f"Failed to generate event hash: {e}")
            raise
    
    async def _get_previous_event_hash(self, tenant_id: str, sequence_id: int) -> str:
        """Get previous event hash for sequence chaining."""
        try:
            if sequence_id <= 0:
                return "0" * 64  # Genesis hash
            
            # Get previous event
            previous_events = await self.get_events(
                tenant_id=tenant_id,
                start_sequence=sequence_id,
                end_sequence=sequence_id,
                limit=1
            )
            
            if previous_events:
                return previous_events[0].signature.event_hash
            else:
                return "0" * 64  # Genesis hash
                
        except Exception as e:
            logger.error(f"Failed to get previous event hash: {e}")
            return "0" * 64
    
    async def _generate_event_signature(self, event: ExecutionEvent, 
                                   event_hash: str, previous_hash: str) -> str:
        """Generate HMAC signature for event."""
        try:
            # Create signature input
            signature_data = f"{event_hash}:{previous_hash}:{event.signature.signature_timestamp.isoformat()}"
            
            # Generate HMAC signature
            signature = hmac.new(
                self.signing_key,
                signature_data.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            return signature
            
        except Exception as e:
            logger.error(f"Failed to generate event signature: {e}")
            raise
    
    async def _update_indexes(self, event: ExecutionEvent):
        """Update indexes for fast event lookup."""
        try:
            pipe = self.redis.pipeline()
            
            # Event type index
            type_key = f"{self.index_prefix}:type:{event.header.event_type.value}"
            pipe.sadd(type_key, event.header.event_id)
            pipe.expire(type_key, self.index_ttl)
            
            # Timestamp index (by hour)
            timestamp = event.header.timestamp
            hour_key = f"{self.index_prefix}:ts:{timestamp.strftime('%Y%m%d%H')}"
            pipe.zadd(hour_key, {event.header.event_id: timestamp.timestamp()})
            pipe.expire(hour_key, self.index_ttl)
            
            # Tenant index
            tenant_key = f"{self.index_prefix}:tenant:{event.header.tenant_id}"
            pipe.zadd(tenant_key, {event.header.event_id: timestamp.timestamp()})
            pipe.expire(tenant_key, self.index_ttl)
            
            # Strategy index
            strategy_key = f"{self.index_prefix}:strategy:{event.header.strategy_id}"
            pipe.zadd(strategy_key, {event.header.event_id: timestamp.timestamp()})
            pipe.expire(strategy_key, self.index_ttl)
            
            # Sequence index
            sequence_key = f"{self.index_prefix}:seq:{event.header.tenant_id}"
            pipe.zadd(sequence_key, {event.header.event_id: event.header.sequence_id})
            pipe.expire(sequence_key, self.index_ttl)
            
            await pipe.execute()
            
        except Exception as e:
            logger.error(f"Failed to update indexes for event {event.header.event_id}: {e}")
    
    async def create_checkpoint(self, tenant_id: str, checkpoint_data: Dict[str, Any]) -> bool:
        """Create checkpoint for replay recovery."""
        try:
            checkpoint_id = str(uuid.uuid4())
            checkpoint_key = f"{self.checkpoint_prefix}:{tenant_id}"
            
            checkpoint = {
                "checkpoint_id": checkpoint_id,
                "tenant_id": tenant_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sequence_id": await self._get_next_sequence_id(tenant_id),
                "data": checkpoint_data
            }
            
            # Store checkpoint
            await self.redis.xadd(
                checkpoint_key,
                checkpoint,
                maxlen=1000,  # Keep last 1000 checkpoints
                approximate=True
            )
            
            # Set TTL for checkpoint
            await self.redis.expire(checkpoint_key, self.checkpoint_ttl)
            
            logger.info(f"Created checkpoint {checkpoint_id} for tenant {tenant_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create checkpoint for {tenant_id}: {e}")
            return False
    
    async def get_checkpoints(self, tenant_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get checkpoints for tenant."""
        try:
            checkpoint_key = f"{self.checkpoint_prefix}:{tenant_id}"
            
            # Get checkpoints from stream
            checkpoint_entries = await self.redis.xrevrange(
                checkpoint_key,
                count=limit
            )
            
            checkpoints = []
            for entry_id, entry_data in checkpoint_entries:
                checkpoints.append(entry_data)
            
            return checkpoints
            
        except Exception as e:
            logger.error(f"Failed to get checkpoints for {tenant_id}: {e}")
            return []


# Global journal instance
immutable_journal = ImmutableJournal()
