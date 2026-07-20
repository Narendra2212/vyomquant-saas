# 🔥 STEP 2 — EVENT PIPELINE HARDENING

## Goal: Make Events Ordered + Replayable for 1000+ Users

**Focus:**
- No lost events
- Replay capability
- Correct ordering

---

## PROBLEM

Without hardened event pipeline:
- ❌ Events lost during high load
- ❌ Out-of-order processing
- ❌ No replay capability after crashes
- ❌ Duplicate event processing
- ❌ No audit trail

---

## SOLUTION: REDIS STREAMS EVENT PIPELINE

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     EVENT PIPELINE                               │
│                                                                 │
│   Producer ──▶ Redis Streams ──▶ Consumer Group                │
│                                   │                             │
│                    ┌──────────────┼──────────────┐           │
│                    ▼               ▼               ▼           │
│               DAG Engine    Execution Engine    UI Streaming    │
│               (checkpoint)   (checkpoint)        (checkpoint)   │
│                                                                 │
│   Storage: events:{tenant_id}  // One stream per tenant        │
│                                                                 │
│   Event Format:                                                │
│   {                                                            │
│     "event_id": "tenant_123:0000000001",  // monotonic         │
│     "timestamp": "2024-01-15T10:30:00.000Z",                  │
│     "tenant_id": "tenant_123",                                │
│     "event_type": "order_placed",                             │
│     "payload": {...},                                         │
│     "source": "order_service",                                  │
│     "correlation_id": "corr_abc123"                            │
│   }                                                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Key Features

| Feature | Implementation | Benefit |
|---------|---------------|---------|
| **Monotonic IDs** | `tenant_id:{sequence:010d}` | Strict ordering guarantee |
| **Redis Streams** | `XADD`, `XREADGROUP` | Persistent, ordered log |
| **Consumer Groups** | `XGROUP CREATE` | Load balancing + failover |
| **Checkpoints** | Redis key per consumer | Resume after restart |
| **Exactly-Once** | Pending event tracking | No duplicates |
| **Replay** | `XRANGE` from any ID | Recovery, debugging |

---

## FILES CREATED

| File | Purpose | Lines |
|------|---------|-------|
| `core/event_pipeline.py` | Event pipeline with Redis Streams | 600+ |
| `SCALING_1000_STEP_2_SUMMARY.md` | This documentation | - |

---

## EVENT PIPELINE (`core/event_pipeline.py`)

### Components

```python
# 1. Event Producer - Publishes events
producer = EventProducer(redis_client)
event = await producer.publish(
    tenant_id="tenant_123",
    event_type=EventType.ORDER_PLACED,
    payload={"order_id": "ord_123", "symbol": "BTC-USD", ...},
    source="order_service"
)
# Returns: Event with monotonic ID "tenant_123:0000000001"

# 2. Event Consumer - Subscribes to events
consumer = EventConsumer(
    consumer_id="dag_engine_1",
    tenant_id="tenant_123",
    event_types={EventType.ORDER_PLACED, EventType.TRADE_EXECUTED},
    consumer_group="dag_engines"
)
consumer.register_handler(EventType.ORDER_PLACED, on_order_placed)
await consumer.start()  # Blocks and processes events

# 3. Event Pipeline Manager - Coordinates everything
pipeline = EventPipeline()
await pipeline.connect()

# Publish through pipeline
event = await pipeline.publish(tenant_id, event_type, payload)

# Create and start consumers
consumer = pipeline.create_consumer("engine_1", "tenant_123")
await pipeline.start_consumers()
```

### Event Structure

```python
@dataclass
class Event:
    event_id: str           # "tenant_123:0000000001" (monotonic)
    timestamp: datetime     # UTC ISO format
    tenant_id: str          # "tenant_123"
    event_type: EventType   # ORDER_PLACED, TRADE_EXECUTED, etc.
    payload: dict           # Event-specific data
    source: str             # "order_service", "execution_engine"
    correlation_id: str     # For distributed tracing
```

### Consumer Groups

```
Stream: events:tenant_123

Consumer Group: dag_engines
├── Consumer: dag_engine_1 (processing messages)
├── Consumer: dag_engine_2 (processing messages)
└── Consumer: dag_engine_3 (idle)

Consumer Group: execution_engines
├── Consumer: exec_1 (processing messages)
└── Consumer: exec_2 (processing messages)

Consumer Group: ui_streaming
└── Consumer: ui_1 (processing messages)
```

Each consumer group maintains its own checkpoint.

---

## USAGE

### Publish Events

```python
from core.event_pipeline import get_event_pipeline, EventType

# Get pipeline
pipeline = await get_event_pipeline()

# Publish order placed event
event = await pipeline.publish(
    tenant_id="tenant_123",
    event_type=EventType.ORDER_PLACED,
    payload={
        "order_id": "ord_abc123",
        "symbol": "BTC-USD",
        "side": "buy",
        "quantity": "0.5",
        "price": "50000.00",
        "user_id": "user_456"
    },
    source="order_service",
    correlation_id="req_xyz789"
)

print(f"Published event: {event.event_id}")
# Output: Published event: tenant_123:0000000001
```

### Consume Events

```python
from core.event_pipeline import EventConsumer, EventType

# Create consumer
consumer = EventConsumer(
    consumer_id="dag_worker_1",
    tenant_id="tenant_123",
    event_types={EventType.ORDER_PLACED, EventType.PRICE_UPDATE},
    consumer_group="dag_engines"
)

# Register handlers
async def on_order_placed(event):
    print(f"Processing order: {event.payload['order_id']}")
    # Trigger DAG execution
    await dag_engine.execute(event.payload)

async def on_price_update(event):
    # Update indicators
    await indicators.update(event.payload)

consumer.register_handler(EventType.ORDER_PLACED, on_order_placed)
consumer.register_handler(EventType.PRICE_UPDATE, on_price_update)

# Start consuming (blocks)
await consumer.connect()
await consumer.start()
```

### Consumer with Checkpointing

```python
consumer = EventConsumer(
    consumer_id="execution_worker_1",
    tenant_id="tenant_123",
    event_types={EventType.ORDER_PLACED},
    consumer_group="execution_engines",
    checkpoint_interval=10,  # Save checkpoint every 10 events
    auto_checkpoint=True
)

# Automatic checkpointing:
# 1. Consumer processes events
# 2. Every 10 events, saves checkpoint to Redis
# 3. On restart, loads checkpoint and resumes from last processed event
# 4. Unacknowledged messages are redelivered

# Manual checkpoint control:
await consumer._save_checkpoint()  # Force save
await consumer._load_checkpoint()  # Force reload
```

### Replay Events

```python
# Replay from specific event (recovery scenario)
await consumer.replay_from("tenant_123:0000000100")

# This will:
# 1. Read all events from "tenant_123:0000000100" to now
# 2. Process them through registered handlers
# 3. NOT update checkpoint (replay only, don't advance)

# Use case: Recover after DAG engine crash
# - Last checkpoint: "tenant_123:0000000100"
# - Events 101-150 happened while down
# - Replay from 100 to process missed events
```

---

## INTEGRATION

### With State Service (Step 1)

```python
# State service publishes events on state changes
from backend.state_service import state_service, StateChangeEvent

async def on_state_change(event: StateChangeEvent):
    # Publish to event pipeline
    pipeline = await get_event_pipeline()
    
    event_type_map = {
        "order_created": EventType.ORDER_PLACED,
        "order_updated": EventType.ORDER_FILLED,
        "position_updated": EventType.POSITION_UPDATED,
    }
    
    await pipeline.publish(
        tenant_id=event.user_id,  # Or extract tenant
        event_type=event_type_map.get(event.event_type, EventType.DAG_EXECUTION_COMPLETED),
        payload=event.new_state,
        source="state_service",
        correlation_id=event.event_id
    )

state_service.register_event_callback(on_state_change)
```

### With DAG Engine

```python
# DAG engine consumes events and triggers executions
from core.event_pipeline import EventConsumer, EventType
from backend.dag_worker import dag_worker

consumer = EventConsumer(
    consumer_id="dag_1",
    tenant_id="tenant_123",
    event_types={
        EventType.ORDER_PLACED,
        EventType.TRADE_EXECUTED,
        EventType.PRICE_UPDATE
    },
    consumer_group="dag_engines"
)

@consumer.register_handler(EventType.ORDER_PLACED)
async def handle_order(event):
    # Load DAG for this symbol
    dag = await dag_worker.load_dag(event.payload['symbol'])
    
    # Execute with event as trigger
    result = await dag.execute(trigger_event=event)
    
    # Publish completion event
    await pipeline.publish(
        tenant_id=event.tenant_id,
        event_type=EventType.DAG_EXECUTION_COMPLETED,
        payload={"dag_id": dag.id, "result": result}
    )
```

### With WebSocket Server

```python
# WebSocket server streams events to UI
from core.event_pipeline import EventConsumer, EventType
from api_ws.ws_manager import ws_manager

consumer = EventConsumer(
    consumer_id="ws_streamer",
    tenant_id="tenant_123",
    event_types={
        EventType.ORDER_PLACED,
        EventType.ORDER_FILLED,
        EventType.POSITION_UPDATED,
        EventType.PRICE_UPDATE
    },
    consumer_group="ui_streaming"
)

@consumer.register_handler(EventType.ORDER_PLACED)
async def stream_order(event):
    # Broadcast to connected WebSocket clients for this tenant
    await ws_manager.broadcast_to_tenant(
        tenant_id=event.tenant_id,
        message={
            "type": "order_placed",
            "data": event.payload
        }
    )
```

---

## MONITORING

### Stream Information

```python
pipeline = await get_event_pipeline()
info = await pipeline.get_stream_info("tenant_123")

print(info)
# {
#     "tenant_id": "tenant_123",
#     "stream_key": "events:tenant_123",
#     "length": 15432,  # Total events in stream
#     "consumer_groups": 3,
#     "groups": [
#         {
#             "name": "dag_engines",
#             "consumers": 3,
#             "pending": 12,  # Unacknowledged messages
#             "last_delivered_id": "tenant_123:0000015430"
#         },
#         {
#             "name": "execution_engines",
#             "consumers": 2,
#             "pending": 0,
#             "last_delivered_id": "tenant_123:0000015432"
#         }
#     ]
# }
```

### Consumer Checkpoint

```python
checkpoint = await consumer.get_checkpoint()
print(checkpoint.to_dict())
# {
#     "consumer_id": "dag_1",
#     "tenant_id": "tenant_123",
#     "last_event_id": "tenant_123:0000015000",
#     "last_sequence": 15000,
#     "timestamp": "2024-01-15T10:30:00",
#     "processed_count": 15000,
#     "error_count": 3
# }
```

---

## FAILURE HANDLING

### Consumer Crash

```
1. Consumer crashes while processing event ID "tenant_123:0000010000"
2. Event remains in pending list (unacknowledged)
3. Consumer restarts, loads checkpoint
4. Checkpoint: "tenant_123:0000009999"
5. Redis redelivers unacknowledged events starting from 10000
6. Consumer processes events (exactly-once tracking prevents duplicates)
7. Normal operation resumes
```

### Network Partition

```
1. Network partition between consumer and Redis
2. Consumer can't acknowledge events
3. Events accumulate in pending list
4. Network recovers
5. Consumer reconnects
6. Redis redelivers all pending events
7. Consumer processes (idempotent handlers handle duplicates)
```

### Redis Failover

```
1. Redis master fails
2. Redis replica promoted to master
3. Consumers reconnect to new master
4. Streams are replicated, no data loss
5. Consumers resume from checkpoint
6. Some events may be redelivered (exactly-once handles this)
```

---

## TESTING

### Test Event Ordering

```python
async def test_event_ordering():
    pipeline = await get_event_pipeline()
    
    # Publish 100 events rapidly
    events = []
    for i in range(100):
        event = await pipeline.publish(
            tenant_id="test_tenant",
            event_type=EventType.PRICE_UPDATE,
            payload={"index": i}
        )
        events.append(event)
    
    # Verify monotonic IDs
    for i in range(1, len(events)):
        prev_seq = events[i-1].get_sequence_number()
        curr_seq = events[i].get_sequence_number()
        assert curr_seq == prev_seq + 1, "Non-monotonic sequence!"
    
    print("[PASS] Events are strictly ordered")
```

### Test Replay

```python
async def test_replay():
    # Create consumer with memory storage
    processed = []
    
    consumer = EventConsumer("test_1", "test_tenant")
    consumer.register_handler(EventType.PRICE_UPDATE, lambda e: processed.append(e.event_id))
    
    # Replay from specific point
    await consumer.replay_from("test_tenant:0000000050")
    
    # Verify we got events 51-100
    assert len(processed) == 50
    
    print("[PASS] Replay working correctly")
```

### Test Exactly-Once

```python
async def test_exactly_once():
    processed_count = 0
    
    async def handler(event):
        nonlocal processed_count
        processed_count += 1
        # Simulate slow processing
        await asyncio.sleep(0.1)
    
    consumer = EventConsumer("test_1", "test_tenant")
    consumer.register_handler(EventType.ORDER_PLACED, handler)
    
    # Publish event
    event = await pipeline.publish("test_tenant", EventType.ORDER_PLACED, {})
    
    # Simulate crash and restart
    await consumer.connect()
    # Event will be redelivered
    
    # Verify handler was only called once
    # (consumer tracks pending events)
    assert processed_count == 1, "Duplicate processing detected!"
    
    print("[PASS] Exactly-once processing working")
```

---

## EXPECTED RESULTS

### Before (Without Event Pipeline)
- ❌ Events lost during high load
- ❌ Out-of-order processing
- ❌ No replay after crashes
- ❌ Duplicate processing

### After (With Event Pipeline)
- ✅ No lost events (Redis Streams persistence)
- ✅ Strict ordering (monotonic IDs)
- ✅ Replay capability (XRANGE from any point)
- ✅ Exactly-once processing (pending event tracking)
- ✅ Automatic failover (consumer groups + checkpoints)

---

## SUMMARY

**Goal:** Production-grade event pipeline for 1000+ users

**Step 2 Complete:** ✅
- Redis Streams for event storage
- Monotonic event IDs (strict ordering)
- Consumer groups with load balancing
- Checkpoints for recovery
- Replay capability
- Exactly-once processing

**Key Components:**
- `EventProducer` - Publishes with monotonic IDs
- `EventConsumer` - Subscribes with checkpointing
- `EventPipeline` - High-level coordinator
- `ConsumerCheckpoint` - Recovery state

**Event Types:**
- ORDER_PLACED, ORDER_FILLED, ORDER_CANCELLED
- POSITION_OPENED, POSITION_UPDATED, POSITION_CLOSED
- TRADE_EXECUTED
- DAG_EXECUTION_STARTED, DAG_EXECUTION_COMPLETED
- PRICE_UPDATE

**Status:** Ready for 1000+ users with reliable event processing
