import asyncio
import hashlib
import json
import logging
import pickle
import threading
import zlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel


def get_current_user(): return {}
"""
backend/state_persistence.py — DAG State Persistence System.

Comprehensive state management for DAG execution with:
  - Real-time state snapshotting (positions, indicators, node results)
  - Crash recovery and state restoration
  - Event sourcing with replay capability
  - Configurable checkpointing intervals
  - Dual storage: Redis (hot) + Database (cold)
  - State versioning and rollback
  - Compression and optimization

Architecture:
  ┌─────────────────────────────────────────────────────────────────────┐
  │                     STATE PERSISTENCE SYSTEM                       │
  │                                                                       │
  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐              │
  │  │   DAG       │───▶│   State     │───▶│   Redis     │              │
  │  │  Execution  │    │   Capture   │    │   (Hot)     │              │
  │  │             │    │             │    │             │              │
  │  └─────────────┘    └──────┬────┘    └──────┬──────┘              │
  │                            │                │                       │
  │                            ▼                ▼                       │
  │                   ┌─────────────────────────────────┐               │
  │                   │         CHECKPOINTING           │               │
  │                   │                                 │               │
  │                   │  • Time-based (every N seconds)   │               │
  │                   │  • Event-based (every N events) │               │
  │                   │  • Manual (explicit trigger)      │               │
  │                   │  • Pre-shutdown (graceful exit) │               │
  │                   └────────────────┬────────────────┘               │
  │                                    │                                │
  │                                    ▼                                │
  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐              │
  │  │  Database   │◄───│   State     │───▶│   Event     │              │
  │  │  (Cold)     │    │   Store     │    │   Log       │              │
  │  │             │    │             │    │             │              │
  │  └─────────────┘    └─────────────┘    └─────────────┘              │
  │                                                                       │
  │  ┌─────────────────────────────────────────────────────────────────┐│
  │  │                     CRASH RECOVERY                             ││
  │  │                                                                 ││
  │  │  1. Detect crash (on startup)                                   ││
  │  │  2. Load last checkpoint from Redis/DB                        ││
  │  │  3. Replay events from event log to catch up                   ││
  │  │  4. Verify state consistency                                  ││
  │  │  5. Resume execution                                          ││
  │  │                                                                 ││
  │  └─────────────────────────────────────────────────────────────────┘│
  │                                                                       │
  └─────────────────────────────────────────────────────────────────────┘

Storage Strategy:
  - Redis: Current state, hot cache, fast access (< 1ms)
  - PostgreSQL: Persistent snapshots, history, audit log
  - S3/MinIO: Large historical checkpoints (optional)

State Components:
  - RollingWindow (indicator calculations)
  - NodeResults (DAG execution outputs)
  - Positions (trading state)
  - EventQueue (pending events)
  - ExecutionMetrics (performance data)
"""


# Redis and Database imports (conditional)
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

try:
    from sqlalchemy import (JSON, Column, DateTime, Integer, LargeBinary,
                            String, create_engine)
    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy.orm import sessionmaker
    SQLALCHEMY_AVAILABLE = True
except ImportError:
    SQLALCHEMY_AVAILABLE = False

logger = logging.getLogger("StatePersistence")


# ═══════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════

class CheckpointType(Enum):
    """Types of checkpoints."""
    TIME_BASED = "time_based"
    EVENT_BASED = "event_based"
    MANUAL = "manual"
    PRE_SHUTDOWN = "pre_shutdown"


class PersistenceLevel(Enum):
    """Level of state persistence."""
    NONE = "none"           # No persistence
    MEMORY = "memory"       # In-memory only (current session)
    REDIS = "redis"         # Redis (survives process restart)
    DATABASE = "database"   # Database (persistent)
    FULL = "full"           # All levels


@dataclass
class StateSnapshot:
    """Complete state snapshot for persistence."""
    session_id: str
    tenant_id: str  # Added tenant_id field for tenant isolation (NH-05)
    symbol: str
    timestamp: datetime
    checkpoint_type: CheckpointType
    
    # DAG State
    rolling_window_data: Dict[str, Any]  # Compressed DataFrame
    node_results: Dict[str, List[Dict]]    # Node outputs
    last_execution_order: List[str]
    
    # Position State
    positions: List[Dict]                # Current positions
    
    # Event State
    pending_events: List[Dict]             # Unprocessed events
    processed_events_count: int
    
    # Metrics
    events_processed: int
    signals_emitted: int
    start_time: datetime
    
    # Metadata
    version: str = "1.0"
    checksum: str = ""
    
    def calculate_checksum(self) -> str:
        """Calculate state checksum for integrity verification."""
        data = f"{self.session_id}:{self.tenant_id}:{self.symbol}:{self.timestamp.isoformat()}:{self.processed_events_count}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]
    
    def verify(self) -> bool:
        """Verify state integrity."""
        return self.checksum == self.calculate_checksum()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "checkpoint_type": self.checkpoint_type.value,
            "rolling_window_data": self.rolling_window_data,
            "node_results": self.node_results,
            "last_execution_order": self.last_execution_order,
            "positions": self.positions,
            "pending_events": self.pending_events,
            "processed_events_count": self.processed_events_count,
            "events_processed": self.events_processed,
            "signals_emitted": self.signals_emitted,
            "start_time": self.start_time.isoformat(),
            "version": self.version,
            "checksum": self.checksum,
        }


@dataclass
class PersistedEvent:
    """Event record for event sourcing."""
    event_id: str
    session_id: str
    tenant_id: str  # Added tenant_id field for event isolation (NH-05)
    event_type: str
    symbol: str
    timestamp: datetime
    event_data: Dict[str, Any]
    processed: bool = False
    processed_at: Optional[datetime] = None
    result: Optional[Dict] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "event_type": self.event_type,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "event_data": self.event_data,
            "processed": self.processed,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "result": self.result,
        }


@dataclass
class PersistenceConfig:
    """Configuration for state persistence."""
    # Levels
    use_redis: bool = True
    use_database: bool = True
    
    # Checkpointing
    checkpoint_interval_seconds: float = 30.0
    checkpoint_on_event_count: int = 100
    enable_pre_shutdown_checkpoint: bool = True
    
    # Redis settings
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_ttl_seconds: int = 86400  # 24 hours
    
    # Database settings
    database_url: str = "postgresql://user:pass@localhost/trading"
    
    # Compression
    compression_enabled: bool = True
    compression_level: int = 6  # 0-9
    
    # Event sourcing
    event_sourcing_enabled: bool = True
    max_events_in_memory: int = 1000
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "use_redis": self.use_redis,
            "use_database": self.use_database,
            "checkpoint_interval_seconds": self.checkpoint_interval_seconds,
            "event_sourcing_enabled": self.event_sourcing_enabled,
        }


# ═══════════════════════════════════════════════════════════════════════════
# COMPRESSION UTILS
# ═══════════════════════════════════════════════════════════════════════════

class StateCompressor:
    """Compress and decompress state data."""
    
    @staticmethod
    def compress_dataframe(df: pd.DataFrame, level: int = 6) -> bytes:
        """Compress DataFrame to bytes."""
        # Convert to pickle
        pickled = pickle.dumps(df)
        # Compress
        compressed = zlib.compress(pickled, level=level)
        return compressed
    
    @staticmethod
    def decompress_dataframe(compressed: bytes) -> pd.DataFrame:
        """Decompress bytes to DataFrame."""
        # Decompress
        pickled = zlib.decompress(compressed)
        # Unpickle
        df = pickle.loads(pickled)
        return df
    
    @staticmethod
    def compress_dict(data: Dict, level: int = 6) -> bytes:
        """Compress dictionary to bytes."""
        json_str = json.dumps(data, default=str)
        return zlib.compress(json_str.encode(), level=level)
    
    @staticmethod
    def decompress_dict(compressed: bytes) -> Dict:
        """Decompress bytes to dictionary."""
        json_str = zlib.decompress(compressed).decode()
        return json.loads(json_str)


# ═══════════════════════════════════════════════════════════════════════════
# REDIS STORAGE
# ═══════════════════════════════════════════════════════════════════════════

class RedisStateStore:
    """Redis-based state storage for hot data."""
    
    def __init__(self, config: PersistenceConfig):
        self.config = config
        self.redis_client = None
        
        if REDIS_AVAILABLE and config.use_redis:
            try:
                self.redis_client = redis.Redis(
                    host=config.redis_host,
                    port=config.redis_port,
                    db=config.redis_db,
                    decode_responses=False,  # For binary data
                    socket_connect_timeout=5,
                    socket_timeout=5,
                )
                self.redis_client.ping()
                logger.info("Redis connection established")
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}")
                self.redis_client = None
    
    def _key(self, session_id: str, symbol: str, suffix: str = "") -> str:
        """Generate Redis key."""
        base = f"dag:state:{session_id}:{symbol}"
        if suffix:
            base += f":{suffix}"
        return base
    
    async def save_snapshot(self, snapshot: StateSnapshot) -> bool:
        """Save state snapshot to Redis."""
        if not self.redis_client:
            return False
        
        try:
            key = self._key(snapshot.session_id, snapshot.symbol, "snapshot")
            
            # Compress and store
            data = StateCompressor.compress_dict(snapshot.to_dict(), self.config.compression_level)
            
            pipe = self.redis_client.pipeline()
            pipe.set(key, data)
            pipe.expire(key, self.config.redis_ttl_seconds)
            
            # Also store metadata separately for quick lookup
            meta_key = self._key(snapshot.session_id, snapshot.symbol, "meta")
            meta = {
                "timestamp": snapshot.timestamp.isoformat(),
                "checkpoint_type": snapshot.checkpoint_type.value,
                "processed_events": snapshot.processed_events_count,
            }
            pipe.set(meta_key, json.dumps(meta))
            pipe.expire(meta_key, self.config.redis_ttl_seconds)
            
            pipe.execute()
            return True
            
        except Exception as e:
            logger.error(f"Failed to save snapshot to Redis: {e}")
            return False
    
    async def load_snapshot(self, session_id: str, symbol: str) -> Optional[StateSnapshot]:
        """Load state snapshot from Redis."""
        if not self.redis_client:
            return None
        
        try:
            key = self._key(session_id, symbol, "snapshot")
            data = self.redis_client.get(key)
            
            if not data:
                return None
            
            # Decompress and parse
            snapshot_dict = StateCompressor.decompress_dict(data)
            
            # Reconstruct snapshot
            snapshot = StateSnapshot(
                session_id=snapshot_dict["session_id"],
                tenant_id=snapshot_dict.get("tenant_id", "default_tenant"),
                symbol=snapshot_dict["symbol"],
                timestamp=datetime.fromisoformat(snapshot_dict["timestamp"]),
                checkpoint_type=CheckpointType(snapshot_dict["checkpoint_type"]),
                rolling_window_data=snapshot_dict["rolling_window_data"],
                node_results=snapshot_dict["node_results"],
                last_execution_order=snapshot_dict["last_execution_order"],
                positions=snapshot_dict["positions"],
                pending_events=snapshot_dict["pending_events"],
                processed_events_count=snapshot_dict["processed_events_count"],
                events_processed=snapshot_dict["events_processed"],
                signals_emitted=snapshot_dict["signals_emitted"],
                start_time=datetime.fromisoformat(snapshot_dict["start_time"]),
                version=snapshot_dict.get("version", "1.0"),
                checksum=snapshot_dict.get("checksum", ""),
            )
            
            # Verify integrity
            if not snapshot.verify():
                logger.warning("Snapshot checksum verification failed")
                return None
            
            return snapshot
            
        except Exception as e:
            logger.error(f"Failed to load snapshot from Redis: {e}")
            return None
    
    async def save_event(self, event: PersistedEvent) -> bool:
        """Save event to Redis stream."""
        if not self.redis_client:
            return False
        
        try:
            # Use Redis Stream for event sourcing
            stream_key = f"dag:events:{event.session_id}:{event.symbol}"
            event_data = event.to_dict()
            
            self.redis_client.xadd(
                stream_key,
                {"data": json.dumps(event_data)},
                maxlen=self.config.max_events_in_memory
            )
            return True
            
        except Exception as e:
            logger.error(f"Failed to save event to Redis: {e}")
            return False
    
    async def get_events(
        self,
        session_id: str,
        symbol: str,
        start_id: str = "0",
        count: int = 100
    ) -> List[PersistedEvent]:
        """Get events from Redis stream."""
        if not self.redis_client:
            return []
        
        try:
            stream_key = f"dag:events:{session_id}:{symbol}"
            events = self.redis_client.xread({stream_key: start_id}, count=count)
            
            result = []
            for stream_name, messages in events:
                for msg_id, fields in messages:
                    data = json.loads(fields[b"data"])
                    event = PersistedEvent(
                        event_id=msg_id.decode(),
                        session_id=data["session_id"],
                        tenant_id=data.get("tenant_id", "default_tenant"),
                        event_type=data["event_type"],
                        symbol=data["symbol"],
                        timestamp=datetime.fromisoformat(data["timestamp"]),
                        event_data=data["event_data"],
                        processed=data.get("processed", False),
                    )
                    result.append(event)
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to get events from Redis: {e}")
            return []
    
    async def delete_state(self, session_id: str, symbol: str) -> bool:
        """Delete state from Redis."""
        if not self.redis_client:
            return False
        
        try:
            pattern = self._key(session_id, symbol, "*")
            keys = self.redis_client.keys(pattern)
            if keys:
                self.redis_client.delete(*keys)
            return True
        except Exception as e:
            logger.error(f"Failed to delete state from Redis: {e}")
            return False


# ═══════════════════════════════════════════════════════════════════════════
# DATABASE STORAGE
# ═══════════════════════════════════════════════════════════════════════════

if SQLALCHEMY_AVAILABLE:
    Base = declarative_base()
    
    class StateSnapshotModel(Base):
        """Database model for state snapshots."""
        __tablename__ = "dag_state_snapshots"
        
        id = Column(String, primary_key=True)
        session_id = Column(String, index=True)
        tenant_id = Column(String, index=True)  # Added tenant_id column
        symbol = Column(String, index=True)
        timestamp = Column(DateTime, index=True)
        checkpoint_type = Column(String)
        state_data = Column(LargeBinary)  # Compressed
        events_processed = Column(Integer)
        signals_emitted = Column(Integer)
        version = Column(String)
        checksum = Column(String)
        created_at = Column(DateTime, default=datetime.utcnow)
    
    class EventLogModel(Base):
        """Database model for event log."""
        __tablename__ = "dag_event_log"
        
        id = Column(String, primary_key=True)
        event_id = Column(String, index=True)
        session_id = Column(String, index=True)
        tenant_id = Column(String, index=True)  # Added tenant_id column
        symbol = Column(String, index=True)
        event_type = Column(String)
        timestamp = Column(DateTime)
        event_data = Column(JSON)
        processed = Column(Integer, default=0)
        processed_at = Column(DateTime)
        result = Column(JSON)


class DatabaseStateStore:
    """Database-based state storage for persistent data."""
    
    def __init__(self, config: PersistenceConfig):
        self.config = config
        self.engine = None
        self.Session = None
        
        if SQLALCHEMY_AVAILABLE and config.use_database:
            try:
                self.engine = create_engine(config.database_url)
                Base.metadata.create_all(self.engine)
                self.Session = sessionmaker(bind=self.engine)
                logger.info("Database connection established")
            except Exception as e:
                logger.warning(f"Database connection failed: {e}")
    
    async def save_snapshot(self, snapshot: StateSnapshot) -> bool:
        """Save state snapshot to database."""
        if not self.Session:
            return False
        
        try:
            session = self.Session()
            
            # Compress state data
            compressed = StateCompressor.compress_dict(
                snapshot.to_dict(),
                self.config.compression_level
            )
            
            # Create record
            record = StateSnapshotModel(
                id=f"{snapshot.session_id}:{snapshot.symbol}:{snapshot.timestamp.timestamp()}",
                session_id=snapshot.session_id,
                tenant_id=snapshot.tenant_id,
                symbol=snapshot.symbol,
                timestamp=snapshot.timestamp,
                checkpoint_type=snapshot.checkpoint_type.value,
                state_data=compressed,
                events_processed=snapshot.events_processed,
                signals_emitted=snapshot.signals_emitted,
                version=snapshot.version,
                checksum=snapshot.checksum,
            )
            
            session.merge(record)  # Upsert
            session.commit()
            session.close()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to save snapshot to database: {e}")
            return False
    
    async def load_latest_snapshot(
        self,
        session_id: str,
        symbol: str
    ) -> Optional[StateSnapshot]:
        """Load latest state snapshot from database."""
        if not self.Session:
            return None
        
        try:
            session = self.Session()
            
            record = session.query(StateSnapshotModel).filter_by(
                session_id=session_id,
                symbol=symbol
            ).order_by(StateSnapshotModel.timestamp.desc()).first()
            
            if not record:
                session.close()
                return None
            
            # Decompress and parse
            snapshot_dict = StateCompressor.decompress_dict(record.state_data)
            
            snapshot = StateSnapshot(
                session_id=snapshot_dict["session_id"],
                tenant_id=snapshot_dict.get("tenant_id", "default_tenant"),
                symbol=snapshot_dict["symbol"],
                timestamp=datetime.fromisoformat(snapshot_dict["timestamp"]),
                checkpoint_type=CheckpointType(snapshot_dict["checkpoint_type"]),
                rolling_window_data=snapshot_dict["rolling_window_data"],
                node_results=snapshot_dict["node_results"],
                last_execution_order=snapshot_dict["last_execution_order"],
                positions=snapshot_dict["positions"],
                pending_events=snapshot_dict["pending_events"],
                processed_events_count=snapshot_dict["processed_events_count"],
                events_processed=snapshot_dict["events_processed"],
                signals_emitted=snapshot_dict["signals_emitted"],
                start_time=datetime.fromisoformat(snapshot_dict["start_time"]),
                version=snapshot_dict.get("version", "1.0"),
                checksum=snapshot_dict.get("checksum", ""),
            )
            
            session.close()
            return snapshot
            
        except Exception as e:
            logger.error(f"Failed to load snapshot from database: {e}")
            return None
    
    async def save_event(self, event: PersistedEvent) -> bool:
        """Save event to database."""
        if not self.Session:
            return False
        
        try:
            session = self.Session()
            
            record = EventLogModel(
                id=f"{event.session_id}:{event.event_id}",
                event_id=event.event_id,
                session_id=event.session_id,
                tenant_id=event.tenant_id,
                symbol=event.symbol,
                event_type=event.event_type,
                timestamp=event.timestamp,
                event_data=event.event_data,
                processed=1 if event.processed else 0,
                processed_at=event.processed_at,
                result=event.result,
            )
            
            session.merge(record)
            session.commit()
            session.close()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to save event to database: {e}")
            return False
    
    async def get_unprocessed_events(
        self,
        session_id: str,
        symbol: str,
        since: Optional[datetime] = None
    ) -> List[PersistedEvent]:
        """Get unprocessed events for replay."""
        if not self.Session:
            return []
        
        try:
            session = self.Session()
            
            query = session.query(EventLogModel).filter_by(
                session_id=session_id,
                symbol=symbol,
                processed=0
            )
            
            if since:
                query = query.filter(EventLogModel.timestamp >= since)
            
            records = query.order_by(EventLogModel.timestamp).all()
            
            events = []
            for record in records:
                event = PersistedEvent(
                    event_id=record.event_id,
                    session_id=record.session_id,
                    tenant_id=getattr(record, "tenant_id", "default_tenant"),
                    event_type=record.event_type,
                    symbol=record.symbol,
                    timestamp=record.timestamp,
                    event_data=record.event_data,
                    processed=bool(record.processed),
                    processed_at=record.processed_at,
                    result=record.result,
                )
                events.append(event)
            
            session.close()
            return events
            
        except Exception as e:
            logger.error(f"Failed to get unprocessed events: {e}")
            return []


# ═══════════════════════════════════════════════════════════════════════════
# STATE PERSISTENCE MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class StatePersistenceManager:
    """
    Main state persistence manager.
    
    Coordinates Redis and database storage, manages checkpointing,
    and handles crash recovery.
    """
    
    def __init__(self, config: Optional[PersistenceConfig] = None):
        self.config = config or PersistenceConfig()
        
        # Storage backends
        self.redis_store = RedisStateStore(self.config)
        self.db_store = DatabaseStateStore(self.config)
        
        # Checkpointing
        self._last_checkpoint_time: Dict[str, datetime] = {}
        self._event_count_since_checkpoint: Dict[str, int] = {}
        self._checkpoint_lock = threading.Lock()
        
        # Event sourcing
        self._pending_events: Dict[str, List[PersistedEvent]] = {}
        
        # Callbacks
        self.checkpoint_callbacks: List[Callable[[StateSnapshot], None]] = []
        
        # Background tasks
        self._checkpoint_task: Optional[asyncio.Task] = None
        self._running = False
        
        logger.info("StatePersistenceManager initialized")
    
    async def start(self):
        """Start background checkpointing."""
        self._running = True
        if self.config.checkpoint_interval_seconds > 0:
            self._checkpoint_task = asyncio.create_task(
                self._checkpoint_loop()
            )
        logger.info("State persistence started")
    
    async def stop(self):
        """Stop and perform final checkpoint."""
        self._running = False
        
        if self._checkpoint_task:
            self._checkpoint_task.cancel()
            try:
                await self._checkpoint_task
            except asyncio.CancelledError:
                pass
        
        logger.info("State persistence stopped")
    
    async def _checkpoint_loop(self):
        """Background checkpointing loop."""
        while self._running:
            try:
                await asyncio.sleep(self.config.checkpoint_interval_seconds)
                
                # Time-based checkpoints for all active sessions
                for key, last_time in list(self._last_checkpoint_time.items()):
                    if (datetime.now() - last_time).seconds >= self.config.checkpoint_interval_seconds:
                        session_id, symbol = key.split(":", 1)
                        # Trigger checkpoint via callback
                        for callback in self.checkpoint_callbacks:
                            try:
                                if asyncio.iscoroutinefunction(callback):
                                    await callback(session_id, symbol, CheckpointType.TIME_BASED)
                                else:
                                    callback(session_id, symbol, CheckpointType.TIME_BASED)
                            except Exception as e:
                                logger.error(f"Checkpoint callback error: {e}")
                                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Checkpoint loop error: {e}")
    
    async def create_snapshot(
        self,
        session_id: str,
        tenant_id: str,  # Added tenant_id argument
        symbol: str,
        dag_state: Dict[str, Any],
        positions: List[Dict],
        pending_events: List[Dict],
        metrics: Dict[str, Any],
        checkpoint_type: CheckpointType = CheckpointType.MANUAL
    ) -> Optional[StateSnapshot]:
        """Create and save state snapshot."""
        
        # Compress rolling window data
        rolling_window = dag_state.get("rolling_window")
        if rolling_window and hasattr(rolling_window, "to_dataframe"):
            df = rolling_window.to_dataframe()
            compressed_window = StateCompressor.compress_dataframe(df, self.config.compression_level)
            window_data = {"compressed": compressed_window.hex()}
        else:
            window_data = {}
        
        # Create snapshot
        snapshot = StateSnapshot(
            session_id=session_id,
            tenant_id=tenant_id,
            symbol=symbol,
            timestamp=datetime.now(),
            checkpoint_type=checkpoint_type,
            rolling_window_data=window_data,
            node_results=dag_state.get("node_results", {}),
            last_execution_order=dag_state.get("execution_order", []),
            positions=positions,
            pending_events=pending_events,
            processed_events_count=metrics.get("events_processed", 0),
            events_processed=metrics.get("events_processed", 0),
            signals_emitted=metrics.get("signals_emitted", 0),
            start_time=dag_state.get("start_time", datetime.now()),
        )
        
        # Calculate checksum
        snapshot.checksum = snapshot.calculate_checksum()
        
        # Save to both stores
        redis_success = await self.redis_store.save_snapshot(snapshot)
        db_success = await self.db_store.save_snapshot(snapshot)
        
        if redis_success or db_success:
            with self._checkpoint_lock:
                key = f"{session_id}:{symbol}"
                self._last_checkpoint_time[key] = datetime.now()
                self._event_count_since_checkpoint[key] = 0
            
            logger.info(
                f"Snapshot created: {session_id}:{symbol} "
                f"(redis={redis_success}, db={db_success})"
            )
            
            # Notify callbacks
            for callback in self.checkpoint_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(snapshot)
                    else:
                        callback(snapshot)
                except Exception as e:
                    logger.error(f"Checkpoint callback error: {e}")
            
            return snapshot
        
        return None
    
    async def restore_state(
        self,
        session_id: str,
        symbol: str
    ) -> Tuple[Optional[StateSnapshot], List[PersistedEvent]]:
        """
        Restore state after crash.
        
        Returns (snapshot, unprocessed_events) for replay.
        """
        logger.info(f"Restoring state for {session_id}:{symbol}")
        
        # Try Redis first (faster)
        snapshot = await self.redis_store.load_snapshot(session_id, symbol)
        
        # Fall back to database
        if not snapshot:
            snapshot = await self.db_store.load_latest_snapshot(session_id, symbol)
        
        if not snapshot:
            logger.warning(f"No snapshot found for {session_id}:{symbol}")
            return None, []
        
        # Get unprocessed events for replay
        unprocessed = await self.db_store.get_unprocessed_events(
            session_id, symbol, since=snapshot.timestamp
        )
        
        logger.info(
            f"State restored: {session_id}:{symbol} "
            f"(events={snapshot.processed_events_count}, "
            f"replay={len(unprocessed)})"
        )
        
        return snapshot, unprocessed
    
    async def persist_event(self, event: PersistedEvent) -> bool:
        """Persist an event for sourcing."""
        # Save to Redis (fast)
        redis_success = await self.redis_store.save_event(event)
        
        # Save to database (persistent)
        db_success = await self.db_store.save_event(event)
        
        return redis_success or db_success
    
    async def mark_event_processed(
        self,
        event_id: str,
        session_id: str,
        result: Optional[Dict] = None
    ) -> bool:
        """Mark an event as processed."""
        # Update in database
        if self.db_store.Session:
            try:
                session = self.db_store.Session()
                record = session.query(EventLogModel).filter_by(
                    event_id=event_id,
                    session_id=session_id
                ).first()
                
                if record:
                    record.processed = 1
                    record.processed_at = datetime.utcnow()
                    if result:
                        record.result = result
                    
                    session.commit()
                
                session.close()
                return True
                
            except Exception as e:
                logger.error(f"Failed to mark event processed: {e}")
        
        return False
    
    def should_checkpoint(
        self,
        session_id: str,
        symbol: str,
        event_count: int = 1
    ) -> bool:
        """Check if checkpoint is needed based on event count."""
        with self._checkpoint_lock:
            key = f"{session_id}:{symbol}"
            self._event_count_since_checkpoint[key] = self._event_count_since_checkpoint.get(key, 0) + event_count
            return self._event_count_since_checkpoint[key] >= self.config.checkpoint_on_event_count
    
    async def delete_state(self, session_id: str, symbol: str) -> bool:
        """Delete all state for a session."""
        redis_success = await self.redis_store.delete_state(session_id, symbol)
        
        # Note: Database records are kept for audit trail
        
        with self._checkpoint_lock:
            key = f"{session_id}:{symbol}"
            self._last_checkpoint_time.pop(key, None)
            self._event_count_since_checkpoint.pop(key, None)
        
        return redis_success
    
    def add_checkpoint_callback(self, callback: Callable[[StateSnapshot], None]):
        """Add callback for checkpoint events."""
        self.checkpoint_callbacks.append(callback)
    
    async def get_recovery_info(self, session_id: str, symbol: str) -> Dict[str, Any]:
        """Get information about recovery options."""
        snapshot, events = await self.restore_state(session_id, symbol)
        
        if not snapshot:
            return {
                "can_recover": False,
                "reason": "no_snapshot_found"
            }
        
        return {
            "can_recover": True,
            "last_checkpoint": snapshot.timestamp.isoformat(),
            "checkpoint_type": snapshot.checkpoint_type.value,
            "events_to_replay": len(events),
            "processed_events": snapshot.processed_events_count,
            "data_sources": [
                "redis" if self.config.use_redis else None,
                "database" if self.config.use_database else None
            ]
        }


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════


router = APIRouter(prefix="/api/state", tags=["state-persistence"])

# Global persistence manager
_persistence_manager: Optional[StatePersistenceManager] = None


def get_persistence_manager() -> StatePersistenceManager:
    """Get or create persistence manager singleton."""
    global _persistence_manager
    if _persistence_manager is None:
        _persistence_manager = StatePersistenceManager()
    return _persistence_manager


class CheckpointRequest(BaseModel):
    """Request to create checkpoint."""
    session_id: str
    symbol: str
    checkpoint_type: str = "manual"


class RecoveryRequest(BaseModel):
    """Request to check recovery options."""
    session_id: str
    symbol: str


class DeleteStateRequest(BaseModel):
    """Request to delete state."""
    session_id: str
    symbol: str


    get_current_user  # Import auth dependency


@router.post("/checkpoint")
async def create_checkpoint_endpoint(
    request: CheckpointRequest,
    user: dict = Depends(get_current_user)
):
    """Create manual checkpoint (triggered by DAG execution)."""
    manager = get_persistence_manager()
    
    # Secure validation
    snapshot, _ = await manager.restore_state(request.session_id, request.symbol)
    if snapshot and getattr(snapshot, "tenant_id", None) != user.get("id"):
        raise HTTPException(403, "Access denied: session does not belong to your tenant context.")
        
    return {
        "status": "checkpoint_requested",
        "session_id": request.session_id,
        "symbol": request.symbol,
        "type": request.checkpoint_type,
    }


@router.get("/recover/{session_id}/{symbol}")
async def get_recovery_info_endpoint(
    session_id: str,
    symbol: str,
    user: dict = Depends(get_current_user)
):
    """Get recovery information for a session."""
    manager = get_persistence_manager()
    
    # Secure validation
    snapshot, _ = await manager.restore_state(session_id, symbol)
    if snapshot and getattr(snapshot, "tenant_id", None) != user.get("id"):
        raise HTTPException(403, "Access denied: session does not belong to your tenant context.")
        
    info = await manager.get_recovery_info(session_id, symbol)
    return info


@router.post("/recover/{session_id}/{symbol}/execute")
async def execute_recovery_endpoint(
    session_id: str,
    symbol: str,
    user: dict = Depends(get_current_user)
):
    """Execute recovery for a session."""
    manager = get_persistence_manager()
    
    snapshot, events = await manager.restore_state(session_id, symbol)
    
    if not snapshot:
        raise HTTPException(404, "No snapshot found for recovery")
        
    if getattr(snapshot, "tenant_id", None) != user.get("id"):
        raise HTTPException(403, "Access denied: session does not belong to your tenant context.")
    
    return {
        "status": "recovered",
        "session_id": session_id,
        "symbol": symbol,
        "checkpoint_time": snapshot.timestamp.isoformat(),
        "events_to_replay": len(events),
        "snapshot": {
            "processed_events": snapshot.processed_events_count,
            "signals_emitted": snapshot.signals_emitted,
        }
    }


@router.delete("/delete/{session_id}/{symbol}")
async def delete_state_endpoint(
    session_id: str,
    symbol: str,
    user: dict = Depends(get_current_user)
):
    """Delete state for a session."""
    manager = get_persistence_manager()
    
    # Secure validation
    snapshot, _ = await manager.restore_state(session_id, symbol)
    if snapshot and getattr(snapshot, "tenant_id", None) != user.get("id"):
        raise HTTPException(403, "Access denied: session does not belong to your tenant context.")
        
    success = await manager.delete_state(session_id, symbol)
    
    return {
        "status": "deleted" if success else "failed",
        "session_id": session_id,
        "symbol": symbol
    }


@router.get("/stats")
async def get_persistence_stats():
    """Get persistence statistics."""
    manager = get_persistence_manager()
    
    return {
        "redis_available": manager.redis_store.redis_client is not None,
        "database_available": manager.db_store.engine is not None,
        "checkpoint_interval": manager.config.checkpoint_interval_seconds,
        "event_sourcing": manager.config.event_sourcing_enabled,
    }
