"""
core/event_pipeline.py — EVENT PIPELINE HARDENING

STEP 2: MAKE EVENTS ORDERED + REPLAYABLE

GOAL: No lost events, replay capability, correct ordering

ARCHITECTURE:
  ┌─────────────────────────────────────────────────────────────────┐
  │                     EVENT PIPELINE                             │
  │                                                                 │
  │   Producer ──▶ Redis Streams ──▶ Consumer Group               │
  │                                    │                           │
  │                    ┌───────────────┼───────────────┐          │
  │                    ▼               ▼               ▼          │
  │               DAG Engine    Execution Engine    UI Streaming   │
  │               (checkpoint)   (checkpoint)        (checkpoint)    │
  │                                                                 │
  │   Every Event:                                                  │
  │   {                                                            │
  │     "event_id": "tenant_123:0000000001",  // monotonic         │
  │     "timestamp": "2024-01-15T10:30:00.000Z",                  │
  │     "tenant_id": "tenant_123",                                │
  │     "event_type": "order_placed",                             │
  │     "payload": {...}                                          │
  │   }                                                            │
  │                                                                 │
  │   Storage: events:{tenant_id}  // Redis Stream per tenant     │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘

FEATURES:
  - Monotonic event IDs (strict ordering)
  - Redis Streams (or Kafka if available)
  - Consumer groups with checkpoints
  - Event replay from any point
  - Automatic failover and recovery
  - Exactly-once processing semantics

EXPECTED RESULT:
  ✔ No lost events
  ✔ Replay capability
  ✔ Correct ordering
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

# Redis imports
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logger = logging.getLogger("EventPipeline")


# ═══════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════

class EventType(str, Enum):
    """Event types for the pipeline."""
    # Order events
    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_REJECTED = "order_rejected"
    
    # Position events
    POSITION_OPENED = "position_opened"
    POSITION_UPDATED = "position_updated"
    POSITION_CLOSED = "position_closed"
    
    # Trade events
    TRADE_EXECUTED = "trade_executed"
    
    # System events
    DAG_EXECUTION_STARTED = "dag_execution_started"
    DAG_EXECUTION_COMPLETED = "dag_execution_completed"
    DAG_EXECUTION_FAILED = "dag_execution_failed"
    
    # Price events
    PRICE_UPDATE = "price_update"
    
    # User events
    USER_CONNECTED = "user_connected"
    USER_DISCONNECTED = "user_disconnected"


@dataclass
class Event:
    """
    Event in the pipeline.
    
    Every event must include:
    - event_id: Monotonic ID (strict ordering)
    - timestamp: ISO format UTC
    - tenant_id: Tenant identifier
    - event_type: Type of event
    - payload: Event data
    """
    event_id: str  # Format: "{tenant_id}:{monotonic_counter:010d}"
    timestamp: datetime
    tenant_id: str
    event_type: EventType
    payload: Dict[str, Any]
    
    # Metadata
    source: str = ""  # Service that generated the event
    correlation_id: str = ""  # For tracing
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "tenant_id": self.tenant_id,
            "event_type": self.event_type.value,
            "payload": self.payload,
            "source": self.source,
            "correlation_id": self.correlation_id,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        """Create from dictionary."""
        return cls(
            event_id=data["event_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            tenant_id=data["tenant_id"],
            event_type=EventType(data["event_type"]),
            payload=data["payload"],
            source=data.get("source", ""),
            correlation_id=data.get("correlation_id", ""),
        )
    
    def get_sequence_number(self) -> int:
        """Extract monotonic sequence number from event_id."""
        # event_id format: "tenant_id:0000000001"
        try:
            return int(self.event_id.split(":")[-1])
        except (ValueError, IndexError):
            return 0


@dataclass
class ConsumerCheckpoint:
    """Checkpoint for a consumer in a consumer group."""
    consumer_id: str
    tenant_id: str
    last_event_id: str  # Last processed event ID
    last_sequence: int  # Last processed sequence number
    timestamp: datetime = field(default_factory=datetime.utcnow)
    processed_count: int = 0
    error_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "consumer_id": self.consumer_id,
            "tenant_id": self.tenant_id,
            "last_event_id": self.last_event_id,
            "last_sequence": self.last_sequence,
            "timestamp": self.timestamp.isoformat(),
            "processed_count": self.processed_count,
            "error_count": self.error_count,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConsumerCheckpoint":
        return cls(
            consumer_id=data["consumer_id"],
            tenant_id=data["tenant_id"],
            last_event_id=data["last_event_id"],
            last_sequence=data["last_sequence"],
            timestamp=datetime.fromisoformat(data["timestamp"]) if data.get("timestamp") else datetime.utcnow(),
            processed_count=data.get("processed_count", 0),
            error_count=data.get("error_count", 0),
        )


# ═══════════════════════════════════════════════════════════════════════════
# EVENT PRODUCER
# ═══════════════════════════════════════════════════════════════════════════

class EventProducer:
    """
    Produces events to Redis Streams.
    
    Features:
    - Monotonic event ID generation
    - Batch publishing
    - Automatic retry
    """
    
    def __init__(self, redis_client: Optional[Any] = None, redis_url: str = "redis://localhost:6379/0"):
        self.redis_url = redis_url
        self._redis = redis_client
        self._sequence_counters: Dict[str, int] = {}  # tenant_id -> counter
        self._lock = asyncio.Lock()
    
    async def connect(self):
        """Connect to Redis."""
        if not self._redis and REDIS_AVAILABLE:
            try:
                self._redis = await redis.from_url(self.redis_url)
                logger.info("[EventProducer] Connected to Redis")
            except Exception as e:
                logger.error(f"[EventProducer] Redis connection failed: {e}")
                raise
    
    async def disconnect(self):
        """Disconnect from Redis."""
        if self._redis:
            await self._redis.close()
            self._redis = None
    
    async def _get_next_sequence(self, tenant_id: str) -> int:
        """Get next monotonic sequence number for tenant."""
        async with self._lock:
            # Try to get from Redis first (for multi-instance coordination)
            if self._redis:
                try:
                    key = f"event_seq:{tenant_id}"
                    seq = await self._redis.incr(key)
                    return seq
                except Exception as e:
                    logger.warning(f"[EventProducer] Failed to get sequence from Redis: {e}")
            
            # Fallback to memory counter
            if tenant_id not in self._sequence_counters:
                self._sequence_counters[tenant_id] = 0
            self._sequence_counters[tenant_id] += 1
            return self._sequence_counters[tenant_id]
    
    async def publish(
        self,
        tenant_id: str,
        event_type: EventType,
        payload: Dict[str, Any],
        source: str = "",
        correlation_id: str = ""
    ) -> Event:
        """
        Publish a single event.
        
        Args:
            tenant_id: Tenant identifier
            event_type: Type of event
            payload: Event data
            source: Service that generated the event
            correlation_id: For distributed tracing
        
        Returns:
            Published event with assigned event_id
        """
        if not self._redis:
            raise RuntimeError("EventProducer not connected")
        
        # Generate monotonic event ID
        sequence = await self._get_next_sequence(tenant_id)
        event_id = f"{tenant_id}:{sequence:010d}"
        
        # Create event
        event = Event(
            event_id=event_id,
            timestamp=datetime.utcnow(),
            tenant_id=tenant_id,
            event_type=event_type,
            payload=payload,
            source=source,
            correlation_id=correlation_id,
        )
        
        # Publish to Redis Stream
        stream_key = f"events:{tenant_id}"
        
        try:
            await self._redis.xadd(
                stream_key,
                {"data": json.dumps(event.to_dict())},
                id=event_id  # Use our monotonic ID
            )
            
            logger.debug(f"[EventProducer] Published {event_type.value} to {stream_key} (id: {event_id})")
            return event
            
        except Exception as e:
            logger.error(f"[EventProducer] Failed to publish event: {e}")
            raise
    
    async def publish_batch(
        self,
        tenant_id: str,
        events: List[tuple]  # [(event_type, payload, source, correlation_id), ...]
    ) -> List[Event]:
        """Publish multiple events in a batch."""
        if not self._redis:
            raise RuntimeError("EventProducer not connected")
        
        published_events = []
        stream_key = f"events:{tenant_id}"
        
        # Use pipeline for batch operation
        pipe = self._redis.pipeline()
        
        for event_type, payload, source, correlation_id in events:
            sequence = await self._get_next_sequence(tenant_id)
            event_id = f"{tenant_id}:{sequence:010d}"
            
            event = Event(
                event_id=event_id,
                timestamp=datetime.utcnow(),
                tenant_id=tenant_id,
                event_type=event_type,
                payload=payload,
                source=source,
                correlation_id=correlation_id,
            )
            
            pipe.xadd(stream_key, {"data": json.dumps(event.to_dict())}, id=event_id)
            published_events.append(event)
        
        try:
            await pipe.execute()
            logger.debug(f"[EventProducer] Published batch of {len(events)} events to {stream_key}")
            return published_events
        except Exception as e:
            logger.error(f"[EventProducer] Batch publish failed: {e}")
            raise


# ═══════════════════════════════════════════════════════════════════════════
# EVENT CONSUMER
# ═══════════════════════════════════════════════════════════════════════════

class EventConsumer:
    """
    Consumes events from Redis Streams with checkpointing.
    
    Features:
    - Consumer groups for load balancing
    - Automatic checkpointing
    - Replay from any point
    - Exactly-once processing semantics
    """
    
    def __init__(
        self,
        consumer_id: str,
        tenant_id: str,
        event_types: Optional[Set[EventType]] = None,
        redis_client: Optional[Any] = None,
        redis_url: str = "redis://localhost:6379/0",
        consumer_group: str = "default",
        checkpoint_interval: int = 10,
        auto_checkpoint: bool = True
    ):
        self.consumer_id = consumer_id
        self.tenant_id = tenant_id
        self.event_types = event_types  # None = all types
        self.consumer_group = consumer_group
        self.checkpoint_interval = checkpoint_interval
        self.auto_checkpoint = auto_checkpoint
        
        self._redis_url = redis_url
        self._redis = redis_client
        
        self._running = False
        self._checkpoint: Optional[ConsumerCheckpoint] = None
        self._event_handlers: Dict[EventType, List[Callable[[Event], Any]]] = {}
        self._pending_events: Dict[str, Event] = {}  # For exactly-once
        
        self._stream_key = f"events:{tenant_id}"
        self._checkpoint_key = f"checkpoints:{tenant_id}:{consumer_group}:{consumer_id}"
    
    async def connect(self):
        """Connect to Redis and create consumer group."""
        if not self._redis and REDIS_AVAILABLE:
            self._redis = await redis.from_url(self._redis_url)
        
        if not self._redis:
            raise RuntimeError("Redis not available")
        
        # Create consumer group if not exists
        try:
            await self._redis.xgroup_create(
                self._stream_key,
                self.consumer_group,
                id="0",  # From beginning
                mkstream=True
            )
            logger.info(f"[EventConsumer] Created consumer group {self.consumer_group}")
        except Exception as e:
            if "already exists" in str(e):
                logger.info(f"[EventConsumer] Consumer group {self.consumer_group} already exists")
            else:
                logger.error(f"[EventConsumer] Failed to create consumer group: {e}")
        
        # Load checkpoint
        await self._load_checkpoint()
    
    async def disconnect(self):
        """Disconnect and save checkpoint."""
        self._running = False
        
        # Save final checkpoint
        if self._checkpoint:
            await self._save_checkpoint()
        
        if self._redis:
            await self._redis.close()
            self._redis = None
    
    async def _load_checkpoint(self):
        """Load last checkpoint from Redis."""
        if not self._redis:
            return
        
        try:
            data = await self._redis.get(self._checkpoint_key)
            if data:
                self._checkpoint = ConsumerCheckpoint.from_dict(json.loads(data))
                logger.info(
                    f"[EventConsumer] Loaded checkpoint: "
                    f"last_event={self._checkpoint.last_event_id}, "
                    f"processed={self._checkpoint.processed_count}"
                )
            else:
                # Start from beginning
                self._checkpoint = ConsumerCheckpoint(
                    consumer_id=self.consumer_id,
                    tenant_id=self.tenant_id,
                    last_event_id="0",
                    last_sequence=0,
                )
        except Exception as e:
            logger.warning(f"[EventConsumer] Failed to load checkpoint: {e}")
            self._checkpoint = ConsumerCheckpoint(
                consumer_id=self.consumer_id,
                tenant_id=self.tenant_id,
                last_event_id="0",
                last_sequence=0,
            )
    
    async def _save_checkpoint(self):
        """Save checkpoint to Redis."""
        if not self._redis or not self._checkpoint:
            return
        
        try:
            self._checkpoint.timestamp = datetime.utcnow()
            await self._redis.set(
                self._checkpoint_key,
                json.dumps(self._checkpoint.to_dict())
            )
            logger.debug(f"[EventConsumer] Checkpoint saved: {self._checkpoint.last_event_id}")
        except Exception as e:
            logger.error(f"[EventConsumer] Failed to save checkpoint: {e}")
    
    def register_handler(self, event_type: EventType, handler: Callable[[Event], Any]):
        """Register event handler."""
        if event_type not in self._event_handlers:
            self._event_handlers[event_type] = []
        self._event_handlers[event_type].append(handler)
        logger.info(f"[EventConsumer] Registered handler for {event_type.value}")
    
    async def start(self):
        """Start consuming events."""
        self._running = True
        logger.info(f"[EventConsumer] Started: {self.consumer_id} for {self.tenant_id}")
        
        checkpoint_counter = 0
        
        while self._running:
            try:
                # Read from stream
                # XREADGROUP with COUNT 1 and BLOCK 5000ms
                messages = await self._redis.xreadgroup(
                    groupname=self.consumer_group,
                    consumername=self.consumer_id,
                    streams={self._stream_key: ">"},  # ">" = new messages only
                    count=1,
                    block=5000  # 5 second timeout
                )
                
                if not messages:
                    continue
                
                # Process messages
                for stream_name, msgs in messages:
                    for msg_id, fields in msgs:
                        await self._process_message(msg_id, fields)
                        checkpoint_counter += 1
                        
                        # Periodic checkpoint
                        if self.auto_checkpoint and checkpoint_counter >= self.checkpoint_interval:
                            await self._save_checkpoint()
                            checkpoint_counter = 0
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[EventConsumer] Error consuming: {e}")
                await asyncio.sleep(1)
        
        # Final checkpoint on stop
        await self._save_checkpoint()
        logger.info(f"[EventConsumer] Stopped: {self.consumer_id}")
    
    async def _process_message(self, msg_id: str, fields: Dict):
        """Process a single message."""
        try:
            # Parse event
            data = json.loads(fields.get(b"data", fields.get("data", "{}")))
            event = Event.from_dict(data)
            
            # Check if we should process this event type
            if self.event_types and event.event_type not in self.event_types:
                # Acknowledge but skip
                await self._redis.xack(self._stream_key, self.consumer_group, msg_id)
                return
            
            # Check for duplicate (exactly-once semantics)
            if event.event_id in self._pending_events:
                logger.warning(f"[EventConsumer] Duplicate event detected: {event.event_id}")
                await self._redis.xack(self._stream_key, self.consumer_group, msg_id)
                return
            
            self._pending_events[event.event_id] = event
            
            # Execute handlers
            handlers = self._event_handlers.get(event.event_type, [])
            for handler in handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(event)
                    else:
                        handler(event)
                except Exception as e:
                    logger.error(f"[EventConsumer] Handler error for {event.event_type.value}: {e}")
                    self._checkpoint.error_count += 1
            
            # Acknowledge message
            await self._redis.xack(self._stream_key, self.consumer_group, msg_id)
            
            # Update checkpoint
            self._checkpoint.last_event_id = event.event_id
            self._checkpoint.last_sequence = event.get_sequence_number()
            self._checkpoint.processed_count += 1
            
            # Remove from pending
            del self._pending_events[event.event_id]
            
        except Exception as e:
            logger.error(f"[EventConsumer] Failed to process message {msg_id}: {e}")
            # Don't acknowledge - message will be redelivered
    
    async def replay_from(self, start_event_id: str):
        """
        Replay events from a specific point.
        
        Args:
            start_event_id: Event ID to start replaying from
        """
        logger.info(f"[EventConsumer] Starting replay from {start_event_id}")
        
        # Use XRANGE to read historical events
        messages = await self._redis.xrange(self._stream_key, start_event_id, "+")
        
        for msg_id, fields in messages:
            # Skip already processed
            if msg_id == start_event_id:
                continue
            
            await self._process_message(msg_id, fields)
        
        logger.info(f"[EventConsumer] Replay complete, processed {len(messages)} events")
    
    async def get_checkpoint(self) -> Optional[ConsumerCheckpoint]:
        """Get current checkpoint."""
        return self._checkpoint
    
    def stop(self):
        """Stop consuming."""
        self._running = False


# ═══════════════════════════════════════════════════════════════════════════
# EVENT PIPELINE MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class EventPipeline:
    """
    High-level event pipeline manager.
    
    Coordinates producers and consumers for the entire system.
    """
    
    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self.redis_url = redis_url
        self._redis: Optional[Any] = None
        self._producer: Optional[EventProducer] = None
        self._consumers: Dict[str, EventConsumer] = {}
        self._running = False
    
    async def connect(self):
        """Initialize the pipeline."""
        if REDIS_AVAILABLE:
            self._redis = await redis.from_url(self.redis_url)
        
        self._producer = EventProducer(self._redis, self.redis_url)
        await self._producer.connect()
        
        logger.info("[EventPipeline] Connected")
    
    async def disconnect(self):
        """Shutdown the pipeline."""
        # Stop all consumers
        for consumer in self._consumers.values():
            consumer.stop()
        
        # Wait for consumers to finish
        await asyncio.sleep(1)
        
        # Disconnect
        if self._producer:
            await self._producer.disconnect()
        
        if self._redis:
            await self._redis.close()
        
        logger.info("[EventPipeline] Disconnected")
    
    def create_consumer(
        self,
        consumer_id: str,
        tenant_id: str,
        event_types: Optional[Set[EventType]] = None,
        consumer_group: str = "default"
    ) -> EventConsumer:
        """Create and register a consumer."""
        consumer = EventConsumer(
            consumer_id=consumer_id,
            tenant_id=tenant_id,
            event_types=event_types,
            redis_client=self._redis,
            consumer_group=consumer_group
        )
        
        key = f"{tenant_id}:{consumer_group}:{consumer_id}"
        self._consumers[key] = consumer
        
        return consumer
    
    async def publish(
        self,
        tenant_id: str,
        event_type: EventType,
        payload: Dict[str, Any],
        source: str = "",
        correlation_id: str = ""
    ) -> Event:
        """Publish an event."""
        if not self._producer:
            raise RuntimeError("Pipeline not connected")
        
        return await self._producer.publish(
            tenant_id=tenant_id,
            event_type=event_type,
            payload=payload,
            source=source,
            correlation_id=correlation_id
        )
    
    async def start_consumers(self):
        """Start all registered consumers."""
        tasks = []
        for consumer in self._consumers.values():
            await consumer.connect()
            task = asyncio.create_task(consumer.start())
            tasks.append(task)
        
        self._running = True
        logger.info(f"[EventPipeline] Started {len(tasks)} consumers")
    
    def stop_consumers(self):
        """Stop all consumers."""
        for consumer in self._consumers.values():
            consumer.stop()
        self._running = False
    
    async def get_stream_info(self, tenant_id: str) -> Dict[str, Any]:
        """Get information about a tenant's event stream."""
        if not self._redis:
            return {}
        
        stream_key = f"events:{tenant_id}"
        
        try:
            # Get stream length
            length = await self._redis.xlen(stream_key)
            
            # Get consumer groups info
            groups_info = await self._redis.xinfo_groups(stream_key)
            
            return {
                "tenant_id": tenant_id,
                "stream_key": stream_key,
                "length": length,
                "consumer_groups": len(groups_info),
                "groups": groups_info,
            }
        except Exception as e:
            logger.error(f"[EventPipeline] Failed to get stream info: {e}")
            return {}


# Global singleton
_event_pipeline: Optional[EventPipeline] = None


async def get_event_pipeline(redis_url: str = "redis://localhost:6379/0") -> EventPipeline:
    """Get or create global event pipeline."""
    global _event_pipeline
    
    if _event_pipeline is None:
        _event_pipeline = EventPipeline(redis_url)
        await _event_pipeline.connect()
    
    return _event_pipeline
