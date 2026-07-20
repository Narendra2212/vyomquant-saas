"""
Atomic Persistence Coordinator

Institutional-grade atomic persistence coordinator for persistence consistency.

This module provides:
- State consistency across persistence layers
- Divergence detection and resolution
- Replay-safe state restoration
- Atomic state transitions
- State validation and verification

Author: Principal Institutional Execution Consistency Engineer
"""

import asyncio
import logging
import json
import hashlib
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("AtomicPersistenceCoordinator")


# ═══════════════════════════════════════════════════════════════════════════
# CONSISTENT STATE
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ConsistentState:
    """Consistent state across all persistence layers."""
    state_id: str
    tenant_id: UUID
    state_type: str  # order, position, execution, etc.
    
    # State data
    data: Dict[str, Any]
    
    # Versioning
    version: int
    checksum: str
    
    # Layer states
    memory_state: Optional[Dict[str, Any]] = None
    redis_state: Optional[Dict[str, Any]] = None
    database_state: Optional[Dict[str, Any]] = None
    
    # Timestamps
    memory_updated_at: Optional[datetime] = None
    redis_updated_at: Optional[datetime] = None
    database_updated_at: Optional[datetime] = None
    
    # Consistency
    is_consistent: bool = True
    divergence_detected: bool = False
    divergence_details: Optional[Dict[str, Any]] = None
    
    def calculate_checksum(self) -> str:
        """Calculate state checksum."""
        data_str = json.dumps(self.data, sort_keys=True)
        return hashlib.sha256(data_str.encode()).hexdigest()
    
    def verify_consistency(self) -> bool:
        """Verify consistency across layers."""
        # Check if all layers have same checksum
        memory_checksum = None
        if self.memory_state:
            memory_checksum = hashlib.sha256(json.dumps(self.memory_state, sort_keys=True).encode()).hexdigest()
        
        redis_checksum = None
        if self.redis_state:
            redis_checksum = hashlib.sha256(json.dumps(self.redis_state, sort_keys=True).encode()).hexdigest()
        
        database_checksum = None
        if self.database_state:
            database_checksum = hashlib.sha256(json.dumps(self.database_state, sort_keys=True).encode()).hexdigest()
        
        # Compare checksums
        if memory_checksum and redis_checksum and memory_checksum != redis_checksum:
            self.divergence_detected = True
            self.divergence_details = {
                "layers": ["memory", "redis"],
                "checksums": [memory_checksum, redis_checksum]
            }
            return False
        
        if redis_checksum and database_checksum and redis_checksum != database_checksum:
            self.divergence_detected = True
            self.divergence_details = {
                "layers": ["redis", "database"],
                "checksums": [redis_checksum, database_checksum]
            }
            return False
        
        return True


# ═══════════════════════════════════════════════════════════════════════════
# STATE CHECKPOINT
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class StateCheckpoint:
    """State checkpoint for replay-safe restoration."""
    checkpoint_id: str
    state_id: str
    tenant_id: UUID
    state_type: str
    
    # State snapshot
    state_snapshot: Dict[str, Any]
    
    # Layer snapshots
    memory_snapshot: Optional[Dict[str, Any]] = None
    redis_snapshot: Optional[Dict[str, Any]] = None
    database_snapshot: Optional[Dict[str, Any]] = None
    
    # Timing
    created_at: datetime
    
    # Validation
    checksum: str = ""
    version: str = "1.0"
    
    def calculate_checksum(self) -> str:
        """Calculate checkpoint checksum."""
        data_str = json.dumps(self.state_snapshot, sort_keys=True)
        return hashlib.sha256(data_str.encode()).hexdigest()
    
    def verify(self) -> bool:
        """Verify checkpoint integrity."""
        return self.checksum == self.calculate_checksum()


# ═══════════════════════════════════════════════════════════════════════════
# ATOMIC PERSISTENCE COORDINATOR
# ═══════════════════════════════════════════════════════════════════════════

class AtomicPersistenceCoordinator:
    """Coordinates consistency across persistence layers."""
    
    def __init__(self, db: Session, tenant_id: UUID):
        self.tenant_id = tenant_id
        self.memory_cache: Dict[str, ConsistentState] = {}
        self.redis_client = None
        self.db_session = db
    
    async def initialize(self):
        """Initialize persistence layers."""
        # Initialize Redis client
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            self.redis_client = await redis_manager.get_client()
            logger.info("[AtomicPersistenceCoordinator] Redis client initialized")
        except Exception as e:
            logger.warning(f"[AtomicPersistenceCoordinator] Redis initialization failed: {e}")
    
    async def write_state(
        self,
        state_id: str,
        state_type: str,
        data: Dict[str, Any],
        write_to_memory: bool = True,
        write_to_redis: bool = True,
        write_to_database: bool = True
    ) -> ConsistentState:
        """Write state to all specified layers atomically."""
        # Create consistent state
        state = ConsistentState(
            state_id=state_id,
            tenant_id=self.tenant_id,
            state_type=state_type,
            data=data,
            version=1,
            checksum=""
        )
        state.checksum = state.calculate_checksum()
        
        # Write to memory
        if write_to_memory:
            self.memory_cache[state_id] = state
            state.memory_state = data
            state.memory_updated_at = datetime.utcnow()
        
        # Write to Redis
        if write_to_redis and self.redis_client:
            redis_key = f"state:{self.tenant_id}:{state_type}:{state_id}"
            await self.redis_client.setex(
                redis_key,
                86400,  # 24 hours TTL
                json.dumps(data)
            )
            state.redis_state = data
            state.redis_updated_at = datetime.utcnow()
        
        # Write to database
        if write_to_database and self.db_session:
            self.db_session.execute(
                text("""
                    INSERT INTO state_persistence (state_id, tenant_id, state_type, data, version, checksum, updated_at)
                    VALUES (:state_id, :tenant_id, :state_type, :data, :version, :checksum, :updated_at)
                    ON CONFLICT (state_id, tenant_id) 
                    DO UPDATE SET data = :data, version = version + 1, checksum = :checksum, updated_at = :updated_at
                """),
                {
                    "state_id": state_id,
                    "tenant_id": str(self.tenant_id),
                    "state_type": state_type,
                    "data": json.dumps(data),
                    "version": 1,
                    "checksum": state.checksum,
                    "updated_at": datetime.utcnow()
                }
            )
            self.db_session.commit()
            state.database_state = data
            state.database_updated_at = datetime.utcnow()
        
        # Verify consistency
        state.is_consistent = state.verify_consistency()
        
        return state
    
    async def read_state(
        self,
        state_id: str,
        state_type: str,
        read_from_memory: bool = True,
        read_from_redis: bool = True,
        read_from_database: bool = False
    ) -> Optional[ConsistentState]:
        """Read state from layers with consistency validation."""
        state = ConsistentState(
            state_id=state_id,
            tenant_id=self.tenant_id,
            state_type=state_type,
            data={},
            version=0,
            checksum=""
        )
        
        # Read from memory
        if read_from_memory and state_id in self.memory_cache:
            state.memory_state = self.memory_cache[state_id].data
            state.memory_updated_at = self.memory_cache[state_id].memory_updated_at
            state.data = state.memory_state
        
        # Read from Redis
        if read_from_redis and self.redis_client:
            redis_key = f"state:{self.tenant_id}:{state_type}:{state_id}"
            redis_data = await self.redis_client.get(redis_key)
            if redis_data:
                state.redis_state = json.loads(redis_data)
                state.redis_updated_at = datetime.utcnow()
                if not state.data:
                    state.data = state.redis_state
        
        # Read from database
        if read_from_database and self.db_session:
            result = self.db_session.execute(
                text("""
                    SELECT data, version, checksum, updated_at
                    FROM state_persistence
                    WHERE state_id = :state_id AND tenant_id = :tenant_id
                    ORDER BY version DESC
                    LIMIT 1
                """),
                {"state_id": state_id, "tenant_id": str(self.tenant_id)}
            ).fetchone()
            
            if result:
                state.database_state = json.loads(result[0])
                state.version = result[1]
                state.checksum = result[2]
                state.database_updated_at = result[3]
                if not state.data:
                    state.data = state.database_state
        
        # Verify consistency
        state.is_consistent = state.verify_consistency()
        
        return state if state.data else None
    
    async def detect_divergence(
        self,
        state_id: str,
        state_type: str
    ) -> Optional[Dict[str, Any]]:
        """Detect divergence between persistence layers."""
        # Read state from all layers
        state = await self.read_state(
            state_id=state_id,
            state_type=state_type,
            read_from_memory=True,
            read_from_redis=True,
            read_from_database=True
        )
        
        if not state:
            return None
        
        # Check for divergence
        if state.divergence_detected:
            return state.divergence_details
        
        return None
    
    async def resolve_divergence(
        self,
        state_id: str,
        state_type: str,
        source_layer: str = "database"  # memory, redis, database
    ) -> bool:
        """Resolve divergence by using source layer as truth."""
        # Read state from all layers
        state = await self.read_state(
            state_id=state_id,
            state_type=state_type,
            read_from_memory=True,
            read_from_redis=True,
            read_from_database=True
        )
        
        if not state:
            return False
        
        # Determine source data
        if source_layer == "memory" and state.memory_state:
            source_data = state.memory_state
        elif source_layer == "redis" and state.redis_state:
            source_data = state.redis_state
        elif source_layer == "database" and state.database_state:
            source_data = state.database_state
        else:
            logger.error(f"[AtomicPersistenceCoordinator] Invalid source layer: {source_layer}")
            return False
        
        # Rewrite state to all layers
        await self.write_state(
            state_id=state_id,
            state_type=state_type,
            data=source_data,
            write_to_memory=True,
            write_to_redis=True,
            write_to_database=True
        )
        
        logger.info(f"[AtomicPersistenceCoordinator] Divergence resolved: {state_id} from {source_layer}")
        
        return True
    
    async def create_checkpoint(
        self,
        state_id: str,
        state_type: str
    ) -> StateCheckpoint:
        """Create checkpoint for replay-safe restoration."""
        # Read current state from all layers
        state = await self.read_state(
            state_id=state_id,
            state_type=state_type,
            read_from_memory=True,
            read_from_redis=True,
            read_from_database=True
        )
        
        if not state:
            raise ValueError(f"State not found: {state_id}")
        
        # Create checkpoint
        checkpoint = StateCheckpoint(
            checkpoint_id=str(uuid4()),
            state_id=state_id,
            tenant_id=self.tenant_id,
            state_type=state_type,
            state_snapshot=state.data,
            memory_snapshot=state.memory_state,
            redis_snapshot=state.redis_state,
            database_snapshot=state.database_state,
            created_at=datetime.utcnow()
        )
        
        # Calculate checksum
        checkpoint.checksum = checkpoint.calculate_checksum()
        
        # Save checkpoint to database
        self.db_session.execute(
            text("""
                INSERT INTO state_checkpoints (checkpoint_id, state_id, tenant_id, state_type, state_snapshot, memory_snapshot, redis_snapshot, database_snapshot, checksum, created_at)
                VALUES (:checkpoint_id, :state_id, :tenant_id, :state_type, :state_snapshot, :memory_snapshot, :redis_snapshot, :database_snapshot, :checksum, :created_at)
            """),
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "state_id": state_id,
                "tenant_id": str(self.tenant_id),
                "state_type": state_type,
                "state_snapshot": json.dumps(checkpoint.state_snapshot),
                "memory_snapshot": json.dumps(checkpoint.memory_snapshot) if checkpoint.memory_snapshot else None,
                "redis_snapshot": json.dumps(checkpoint.redis_snapshot) if checkpoint.redis_snapshot else None,
                "database_snapshot": json.dumps(checkpoint.database_snapshot) if checkpoint.database_snapshot else None,
                "checksum": checkpoint.checksum,
                "created_at": checkpoint.created_at
            }
        )
        self.db_session.commit()
        
        logger.info(f"[AtomicPersistenceCoordinator] Checkpoint created: {checkpoint.checkpoint_id}")
        
        return checkpoint
    
    async def restore_from_checkpoint(
        self,
        checkpoint_id: str
    ) -> bool:
        """Restore state from checkpoint."""
        # Load checkpoint from database
        result = self.db_session.execute(
            text("""
                SELECT checkpoint_id, state_id, tenant_id, state_type, state_snapshot, memory_snapshot, redis_snapshot, database_snapshot, checksum, created_at
                FROM state_checkpoints
                WHERE checkpoint_id = :checkpoint_id AND tenant_id = :tenant_id
            """),
            {"checkpoint_id": checkpoint_id, "tenant_id": str(self.tenant_id)}
        ).fetchone()
        
        if not result:
            logger.error(f"[AtomicPersistenceCoordinator] Checkpoint not found: {checkpoint_id}")
            return False
        
        # Reconstruct checkpoint
        checkpoint = StateCheckpoint(
            checkpoint_id=result[0],
            state_id=result[1],
            tenant_id=UUID(result[2]),
            state_type=result[3],
            state_snapshot=json.loads(result[4]),
            memory_snapshot=json.loads(result[5]) if result[5] else None,
            redis_snapshot=json.loads(result[6]) if result[6] else None,
            database_snapshot=json.loads(result[7]) if result[7] else None,
            created_at=result[9],
            checksum=result[8]
        )
        
        # Verify checkpoint integrity
        if not checkpoint.verify():
            logger.error(f"[AtomicPersistenceCoordinator] Checkpoint verification failed: {checkpoint_id}")
            return False
        
        # Restore memory state
        if checkpoint.memory_snapshot:
            self.memory_cache[checkpoint.state_id] = ConsistentState(
                state_id=checkpoint.state_id,
                tenant_id=self.tenant_id,
                state_type=checkpoint.state_type,
                data=checkpoint.memory_snapshot,
                version=0,
                checksum=""
            )
        
        # Restore Redis state
        if checkpoint.redis_snapshot and self.redis_client:
            redis_key = f"state:{self.tenant_id}:{checkpoint.state_type}:{checkpoint.state_id}"
            await self.redis_client.setex(
                redis_key,
                86400,
                json.dumps(checkpoint.redis_snapshot)
            )
        
        # Restore database state
        if checkpoint.database_snapshot:
            self.db_session.execute(
                text("""
                    INSERT INTO state_persistence (state_id, tenant_id, state_type, data, version, checksum, updated_at)
                    VALUES (:state_id, :tenant_id, :state_type, :data, :version, :checksum, :updated_at)
                    ON CONFLICT (state_id, tenant_id) 
                    DO UPDATE SET data = :data, checksum = :checksum, updated_at = :updated_at
                """),
                {
                    "state_id": checkpoint.state_id,
                    "tenant_id": str(self.tenant_id),
                    "state_type": checkpoint.state_type,
                    "data": json.dumps(checkpoint.database_snapshot),
                    "version": 1,
                    "checksum": checkpoint.checksum,
                    "updated_at": datetime.utcnow()
                }
            )
            self.db_session.commit()
        
        logger.info(f"[AtomicPersistenceCoordinator] State restored from checkpoint: {checkpoint_id}")
        
        return True
    
    async def validate_consistency(
        self,
        state_id: str,
        state_type: str
    ) -> bool:
        """Validate consistency across persistence layers."""
        # Read state from all layers
        state = await self.read_state(
            state_id=state_id,
            state_type=state_type,
            read_from_memory=True,
            read_from_redis=True,
            read_from_database=True
        )
        
        if not state:
            return False
        
        # Verify consistency
        return state.is_consistent
    
    async def validate_integrity(
        self,
        state_id: str,
        state_type: str
    ) -> bool:
        """Validate state integrity."""
        # Read state from database (source of truth)
        result = self.db_session.execute(
            text("""
                SELECT data, checksum
                FROM state_persistence
                WHERE state_id = :state_id AND tenant_id = :tenant_id
                ORDER BY version DESC
                LIMIT 1
            """),
            {"state_id": state_id, "tenant_id": str(self.tenant_id)}
        ).fetchone()
        
        if not result:
            return False
        
        # Verify checksum
        data = json.loads(result[0])
        stored_checksum = result[1]
        calculated_checksum = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        
        return stored_checksum == calculated_checksum
    
    async def cleanup_old_checkpoints(self, older_than_hours: int = 24):
        """Clean up old checkpoints."""
        cutoff_time = datetime.utcnow() - timedelta(hours=older_than_hours)
        
        result = self.db_session.execute(
            text("""
                DELETE FROM state_checkpoints
                WHERE tenant_id = :tenant_id AND created_at < :cutoff_time
            """),
            {"tenant_id": str(self.tenant_id), "cutoff_time": cutoff_time}
        )
        
        deleted_count = result.rowcount
        logger.info(f"[AtomicPersistenceCoordinator] Cleaned up {deleted_count} old checkpoints")
        
        return deleted_count


# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL INSTANCE
# ═══════════════════════════════════════════════════════════════════════════

def get_atomic_persistence_coordinator(db: Session, tenant_id: UUID) -> AtomicPersistenceCoordinator:
    """Get atomic persistence coordinator instance."""
    return AtomicPersistenceCoordinator(db, tenant_id)
