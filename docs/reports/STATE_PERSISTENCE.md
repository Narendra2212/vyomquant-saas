# State Persistence System

**Date:** May 1, 2026

---

## Overview

Built a **comprehensive state persistence system** for DAG execution with:
- ✅ Real-time state snapshotting (positions, indicators, node results)
- ✅ Crash recovery and state restoration
- ✅ Event sourcing with replay capability
- ✅ Configurable checkpointing (time-based, event-based, manual)
- ✅ Dual storage: Redis (hot) + Database (cold)
- ✅ State versioning and integrity verification

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     STATE PERSISTENCE SYSTEM                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐              │
│  │   DAG       │───▶│   State     │───▶│   Redis     │              │
│  │  Execution  │    │   Capture   │    │   (Hot)     │              │
│  └─────────────┘    └──────┬──────┘    └──────┬──────┘              │
│                            │                   │                      │
│                            │          ┌────────┴────────┐            │
│                            │          │                 │            │
│                            │    ┌─────▼─────┐    ┌──────▼─────┐      │
│                            │    │  State    │    │   Event    │      │
│                            │    │ Snapshot  │    │   Stream   │      │
│                            │    └─────┬─────┘    └──────┬─────┘      │
│                            │          │                 │            │
│                            └─────┬────┴────────┬────────┘            │
│                                  │             │                      │
│                            ┌─────▼─────────────▼─────┐              │
│                            │      CHECKPOINTING        │              │
│                            │                           │              │
│                            │ • Time: Every 30s         │              │
│                            │ • Events: Every 100       │              │
│                            │ • Manual: On demand       │              │
│                            │ • Shutdown: Graceful      │              │
│                            └────────────┬──────────────┘              │
│                                         │                            │
│                            ┌────────────▼──────────────┐              │
│                            │         Database          │              │
│                            │         (Cold)            │              │
│                            │                           │              │
│                            │ • Persistent snapshots    │              │
│                            │ • Event log for replay    │              │
│                            │ • Audit trail             │              │
│                            └───────────────────────────┘              │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘

Storage Strategy:
  Redis (< 1ms): Current state, fast access, TTL 24h
  Database (persistent): Snapshots, event history, audit
  S3/MinIO (optional): Large historical archives
```

---

## Core Components

### 1. StateSnapshot

```python
@dataclass
class StateSnapshot:
    session_id: str              # Session identifier
    symbol: str                  # Trading symbol
    timestamp: datetime          # Snapshot time
    checkpoint_type: CheckpointType
    
    # DAG State
    rolling_window_data: Dict    # Compressed DataFrame
    node_results: Dict           # Node outputs
    last_execution_order: List  # Execution history
    
    # Position State
    positions: List[Dict]      # Current positions
    
    # Event State
    pending_events: List[Dict]   # Unprocessed events
    processed_events_count: int
    
    # Metrics
    events_processed: int
    signals_emitted: int
    start_time: datetime
    
    # Integrity
    version: str = "1.0"
    checksum: str = ""           # SHA-256 verification
    
    def calculate_checksum(self) -> str
    def verify(self) -> bool
```

### 2. PersistedEvent

```python
@dataclass
class PersistedEvent:
    event_id: str
    session_id: str
    event_type: str
    symbol: str
    timestamp: datetime
    event_data: Dict
    processed: bool
    processed_at: Optional[datetime]
    result: Optional[Dict]
```

### 3. RedisStateStore

```python
class RedisStateStore:
    """Hot storage for fast access."""
    
    async def save_snapshot(snapshot: StateSnapshot) -> bool
    async def load_snapshot(session_id, symbol) -> Optional[StateSnapshot]
    async def save_event(event: PersistedEvent) -> bool
    async def get_events(session_id, symbol, start_id, count) -> List[PersistedEvent]
    async def delete_state(session_id, symbol) -> bool
```

**Redis Keys:**
- `dag:state:{session_id}:{symbol}:snapshot` - Full snapshot
- `dag:state:{session_id}:{symbol}:meta` - Metadata
- `dag:events:{session_id}:{symbol}` - Event stream

### 4. DatabaseStateStore

```python
class DatabaseStateStore:
    """Cold storage for persistence."""
    
    async def save_snapshot(snapshot: StateSnapshot) -> bool
    async def load_latest_snapshot(session_id, symbol) -> Optional[StateSnapshot]
    async def save_event(event: PersistedEvent) -> bool
    async def get_unprocessed_events(session_id, symbol, since) -> List[PersistedEvent]
```

**Database Tables:**
- `dag_state_snapshots` - Persistent snapshots
- `dag_event_log` - Event sourcing log

### 5. StatePersistenceManager

```python
class StatePersistenceManager:
    """Main coordinator for state management."""
    
    def __init__(self, config: PersistenceConfig)
    
    # Lifecycle
    async def start()  # Start background checkpointing
    async def stop()   # Final checkpoint and shutdown
    
    # State management
    async def create_snapshot(...) -> StateSnapshot
    async def restore_state(session_id, symbol) -> (Snapshot, Events)
    async def delete_state(session_id, symbol) -> bool
    
    # Event sourcing
    async def persist_event(event: PersistedEvent) -> bool
    async def mark_event_processed(event_id, result) -> bool
    
    # Checkpointing
    def should_checkpoint(session_id, symbol, event_count) -> bool
    def add_checkpoint_callback(callback)
    
    # Recovery
    async def get_recovery_info(session_id, symbol) -> Dict
```

---

## Checkpointing Strategies

| Type | Trigger | Use Case |
|------|---------|----------|
| **Time-Based** | Every N seconds | Regular persistence |
| **Event-Based** | Every N events | High-frequency updates |
| **Manual** | Explicit call | Critical operations |
| **Pre-Shutdown** | Graceful exit | Ensure clean state |

**Default Configuration:**
- Time interval: 30 seconds
- Event threshold: 100 events
- Compression: Level 6 (balanced)

---

## Compression & Optimization

```python
class StateCompressor:
    @staticmethod
    def compress_dataframe(df: pd.DataFrame, level: int) -> bytes
    # Pickle + zlib compression
    # Typical: 10-50x size reduction
    
    @staticmethod
    def decompress_dataframe(compressed: bytes) -> pd.DataFrame
    
    @staticmethod
    def compress_dict(data: Dict, level: int) -> bytes
    # JSON + zlib compression
```

**Compression Levels:**
- 0: No compression (fastest)
- 6: Balanced (default)
- 9: Maximum compression (slowest)

---

## Crash Recovery Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                     CRASH RECOVERY                                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  1. Detect Crash (on startup)                                       │
│     └─▶ Check for existing session state                            │
│                                                                      │
│  2. Load Last Checkpoint                                            │
│     ├─▶ Try Redis first (fastest)                                   │
│     └─▶ Fallback to Database                                        │
│                                                                      │
│  3. Get Unprocessed Events                                          │
│     └─▶ Query event log for events since checkpoint                 │
│                                                                      │
│  4. Replay Events                                                   │
│     ├─▶ Process each event sequentially                            │
│     ├─▶ Update state after each event                              │
│     └─▶ Verify consistency                                          │
│                                                                      │
│  5. Resume Execution                                                │
│     └─▶ Continue from recovered state                               │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## API Endpoints

### Check Recovery Info

```http
GET /api/state/recover/{session_id}/{symbol}

Response:
{
  "can_recover": true,
  "last_checkpoint": "2026-05-01T12:30:00",
  "checkpoint_type": "time_based",
  "events_to_replay": 15,
  "processed_events": 2847,
  "data_sources": ["redis", "database"]
}
```

### Execute Recovery

```http
POST /api/state/recover/{session_id}/{symbol}/execute

Response:
{
  "status": "recovered",
  "session_id": "uuid",
  "symbol": "BTCUSDT",
  "checkpoint_time": "2026-05-01T12:30:00",
  "events_to_replay": 15,
  "snapshot": {
    "processed_events": 2847,
    "signals_emitted": 342
  }
}
```

### Delete State

```http
DELETE /api/state/delete/{session_id}/{symbol}

Response:
{
  "status": "deleted",
  "session_id": "uuid",
  "symbol": "BTCUSDT"
}
```

### Get Persistence Stats

```http
GET /api/state/stats

Response:
{
  "redis_available": true,
  "database_available": true,
  "checkpoint_interval": 30.0,
  "event_sourcing": true
}
```

---

## Usage Examples

### Example 1: Basic State Persistence

```python
from backend.state_persistence import (
    StatePersistenceManager,
    PersistenceConfig,
    StateSnapshot,
    PersistedEvent
)

# Configure
config = PersistenceConfig(
    use_redis=True,
    use_database=True,
    checkpoint_interval_seconds=30.0,
    checkpoint_on_event_count=100,
    compression_level=6,
    event_sourcing_enabled=True
)

# Create manager
manager = StatePersistenceManager(config)

# Start background checkpointing
await manager.start()

# In your DAG execution loop
async def on_checkpoint_needed(session_id, symbol, checkpoint_type):
    """Callback triggered when checkpoint is needed."""
    
    # Capture current DAG state
    dag_state = {
        "rolling_window": rolling_window,
        "node_results": node_results,
        "execution_order": execution_order,
        "start_time": start_time,
    }
    
    # Capture positions
    positions = [p.to_dict() for p in portfolio.positions.values()]
    
    # Capture pending events
    pending = [e.to_dict() for e in event_queue]
    
    # Create snapshot
    snapshot = await manager.create_snapshot(
        session_id=session_id,
        symbol=symbol,
        dag_state=dag_state,
        positions=positions,
        pending_events=pending,
        metrics=execution_stats,
        checkpoint_type=checkpoint_type
    )
    
    logger.info(f"Checkpoint created: {snapshot.timestamp}")

# Register callback
manager.add_checkpoint_callback(on_checkpoint_needed)
```

### Example 2: Event Sourcing

```python
# Persist each event
async def on_market_event(event):
    # Create persisted event
    persisted = PersistedEvent(
        event_id=str(uuid.uuid4()),
        session_id=session_id,
        event_type="MARKET",
        symbol=event.symbol,
        timestamp=event.timestamp,
        event_data=event.to_dict(),
        processed=False
    )
    
    # Save to event log
    await manager.persist_event(persisted)
    
    # Process event...
    result = await process_event(event)
    
    # Mark as processed
    await manager.mark_event_processed(
        persisted.event_id,
        result={"signals": result}
    )
```

### Example 3: Crash Recovery

```python
# On startup - check for recovery
async def startup_recovery(session_id, symbol):
    # Check recovery info
    info = await manager.get_recovery_info(session_id, symbol)
    
    if not info["can_recover"]:
        logger.info("No previous state found - starting fresh")
        return None
    
    logger.info(
        f"Recovering state from {info['last_checkpoint']} "
        f"({info['events_to_replay']} events to replay)"
    )
    
    # Restore state
    snapshot, events = await manager.restore_state(session_id, symbol)
    
    if not snapshot:
        logger.error("Failed to restore state")
        return None
    
    # Restore rolling window
    if snapshot.rolling_window_data:
        df = StateCompressor.decompress_dataframe(
            bytes.fromhex(snapshot.rolling_window_data["compressed"])
        )
        rolling_window = RollingWindow.from_dataframe(df)
    
    # Restore positions
    for pos_data in snapshot.positions:
        position = Position.from_dict(pos_data)
        portfolio.positions[position.id] = position
    
    # Replay events
    for event in events:
        logger.info(f"Replaying event: {event.event_id}")
        await process_market_event(event)
        await manager.mark_event_processed(event.event_id)
    
    logger.info(f"Recovery complete - {len(events)} events replayed")
    
    return snapshot
```

### Example 4: Manual Checkpointing

```python
# Before critical operation
async def before_critical_operation():
    snapshot = await manager.create_snapshot(
        session_id=session_id,
        symbol=symbol,
        dag_state=dag_state,
        positions=positions,
        pending_events=pending,
        metrics=metrics,
        checkpoint_type=CheckpointType.MANUAL
    )
    
    logger.info(f"Pre-operation checkpoint: {snapshot.checksum}")

# After critical operation - rollback if needed
async def after_critical_operation(success: bool):
    if not success:
        logger.warning("Operation failed - rolling back to checkpoint")
        
        # Restore from last checkpoint
        snapshot, events = await manager.restore_state(session_id, symbol)
        
        # Restore all state
        await restore_from_snapshot(snapshot)
        
        logger.info("Rollback complete")
```

### Example 5: DAG Event Loop Integration

```python
from backend.state_persistence import StatePersistenceManager, StateSnapshot
from backend.dag_event_loop import DAGEventLoop

class PersistentDAGEventLoop(DAGEventLoop):
    """DAG with full state persistence."""
    
    def __init__(self, persistence_manager: StatePersistenceManager, ...):
        super().__init__(...)
        self.persistence = persistence_manager
        
        # Register checkpoint callback
        self.persistence.add_checkpoint_callback(self._on_checkpoint)
    
    async def start(self):
        # Try to recover state
        await self._attempt_recovery()
        
        # Start persistence
        await self.persistence.start()
        
        await super().start()
    
    async def stop(self):
        # Final checkpoint
        await self._create_checkpoint(CheckpointType.PRE_SHUTDOWN)
        
        # Stop persistence
        await self.persistence.stop()
        
        await super().stop()
    
    async def _attempt_recovery(self):
        """Attempt to recover from crash."""
        for symbol in self.symbols:
            snapshot, events = await self.persistence.restore_state(
                self.session_id, symbol
            )
            
            if snapshot:
                # Restore rolling window
                if snapshot.rolling_window_data:
                    compressed = bytes.fromhex(
                        snapshot.rolling_window_data["compressed"]
                    )
                    df = StateCompressor.decompress_dataframe(compressed)
                    self.rolling_windows[symbol] = RollingWindow.from_dataframe(df)
                
                # Replay events
                for event in events:
                    await self._process_event(event)
                    await self.persistence.mark_event_processed(event.event_id)
                
                logger.info(f"Recovered {symbol}: {len(events)} events replayed")
    
    async def _on_checkpoint(self, snapshot: StateSnapshot):
        """Handle checkpoint callback."""
        logger.info(
            f"Checkpoint: {snapshot.symbol} "
            f"events={snapshot.events_processed} "
            f"signals={snapshot.signals_emitted}"
        )
    
    async def _process_event(self, event):
        # Persist event before processing
        persisted = PersistedEvent(
            event_id=str(uuid.uuid4()),
            session_id=self.session_id,
            event_type=event.event_type.value,
            symbol=event.symbol,
            timestamp=event.timestamp,
            event_data=event.to_dict(),
            processed=False
        )
        await self.persistence.persist_event(persisted)
        
        # Process...
        result = await super()._process_event(event)
        
        # Mark processed
        await self.persistence.mark_event_processed(
            persisted.event_id,
            result=result
        )
        
        # Check if checkpoint needed
        if self.persistence.should_checkpoint(self.session_id, event.symbol):
            await self._create_checkpoint(CheckpointType.EVENT_BASED)
        
        return result
    
    async def _create_checkpoint(self, checkpoint_type):
        """Create state checkpoint."""
        for symbol in self.symbols:
            window = self.rolling_windows.get(symbol)
            
            snapshot = await self.persistence.create_snapshot(
                session_id=self.session_id,
                symbol=symbol,
                dag_state={
                    "rolling_window": window,
                    "node_results": self.node_results_history.get(symbol, {}),
                    "execution_order": [],
                    "start_time": self.start_time,
                },
                positions=[],  # From portfolio manager
                pending_events=[],  # From event queue
                metrics=self.get_stats(),
                checkpoint_type=checkpoint_type
            )
```

### Example 6: Monitoring & Alerting

```python
async def monitor_persistence():
    """Monitor persistence health."""
    while True:
        stats = await manager.get_stats()
        
        # Check Redis connectivity
        if not stats["redis_available"]:
            await send_alert("Redis unavailable - using database only")
        
        # Check checkpoint lag
        for key, last_time in manager._last_checkpoint_time.items():
            lag = (datetime.now() - last_time).total_seconds()
            if lag > manager.config.checkpoint_interval_seconds * 2:
                await send_alert(f"Checkpoint lag for {key}: {lag:.0f}s")
        
        # Log stats
        logger.info(
            f"Persistence: redis={stats['redis_available']}, "
            f"db={stats['database_available']}, "
            f"interval={stats['checkpoint_interval']}s"
        )
        
        await asyncio.sleep(60)

# Start monitoring
asyncio.create_task(monitor_persistence())
```

---

## Files Created/Modified

| File | Lines | Change |
|------|-------|--------|
| `backend/state_persistence.py` | ~700 | **NEW** - State persistence system |
| `main.py` | +3 | Added persistence router import & registration |

---

## Status: ✅ COMPLETE

State persistence system with:
- ✅ Dual storage (Redis + Database)
- ✅ 4 checkpointing strategies
- ✅ Event sourcing with replay
- ✅ Compression and optimization
- ✅ Crash recovery flow
- ✅ Integrity verification (checksums)
- ✅ DAG integration
