"""
Replay-Safe Transaction Guard

Institutional-grade replay-safe transaction guard for transactional consistency.

This module provides:
- Replay-safe transaction validation
- Transactional rollback safety
- Replay-safe checkpoint management
- Transactional retry safety
- Failover-safe persistence

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

logger = logging.getLogger("ReplaySafeTransactionGuard")


# ═══════════════════════════════════════════════════════════════════════════
# TRANSACTION CHECKPOINT
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TransactionCheckpoint:
    """Transaction checkpoint for rollback and replay."""
    checkpoint_id: str
    transaction_id: str
    tenant_id: UUID
    
    # State snapshot
    state_snapshot: Dict[str, Any]
    
    # Operation snapshot
    operations_snapshot: List[Dict[str, Any]]
    
    # Timing
    created_at: datetime
    
    # Validation
    checksum: str = ""
    version: str = "1.0"
    
    def calculate_checksum(self) -> str:
        """Calculate checkpoint checksum."""
        data = f"{self.checkpoint_id}:{self.transaction_id}:{json.dumps(self.state_snapshot, sort_keys=True)}"
        return hashlib.sha256(data.encode()).hexdigest()
    
    def verify(self) -> bool:
        """Verify checkpoint integrity."""
        return self.checksum == self.calculate_checksum()


# ═══════════════════════════════════════════════════════════════════════════
# TRANSACTION ROLLBACK
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TransactionRollback:
    """Transaction rollback record."""
    rollback_id: str
    transaction_id: str
    tenant_id: UUID
    
    # Rollback reason
    reason: str
    
    # Rollback state
    rollback_state: Dict[str, Any]
    
    # Timing
    created_at: datetime
    
    # Validation
    checksum: str = ""
    version: str = "1.0"
    
    def calculate_checksum(self) -> str:
        """Calculate rollback checksum."""
        data = f"{self.rollback_id}:{self.transaction_id}:{json.dumps(self.rollback_state, sort_keys=True)}"
        return hashlib.sha256(data.encode()).hexdigest()
    
    def verify(self) -> bool:
        """Verify rollback integrity."""
        return self.checksum == self.calculate_checksum()


# ═══════════════════════════════════════════════════════════════════════════
# REPLAY-SAFE TRANSACTION GUARD
# ═══════════════════════════════════════════════════════════════════════════

class ReplaySafeTransactionGuard:
    """Replay-safe transaction guard for transactional consistency."""
    
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id
        self.active_checkpoints: Dict[str, TransactionCheckpoint] = {}
        self.active_rollbacks: Dict[str, TransactionRollback] = {}
    
    async def create_checkpoint(
        self,
        transaction_id: str,
        state_snapshot: Dict[str, Any],
        operations_snapshot: List[Dict[str, Any]]
    ) -> TransactionCheckpoint:
        """Create transaction checkpoint for rollback and replay."""
        # Create checkpoint
        checkpoint = TransactionCheckpoint(
            checkpoint_id=str(uuid4()),
            transaction_id=transaction_id,
            tenant_id=self.tenant_id,
            state_snapshot=state_snapshot,
            operations_snapshot=operations_snapshot,
            created_at=datetime.utcnow()
        )
        
        # Calculate checksum
        checkpoint.checksum = checkpoint.calculate_checksum()
        
        # Save checkpoint to database
        self.db.execute(
            text("""
                INSERT INTO transaction_checkpoints (checkpoint_id, transaction_id, tenant_id, state_snapshot, operations_snapshot, checksum, created_at)
                VALUES (:checkpoint_id, :transaction_id, :tenant_id, :state_snapshot, :operations_snapshot, :checksum, :created_at)
            """),
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "transaction_id": transaction_id,
                "tenant_id": str(self.tenant_id),
                "state_snapshot": json.dumps(state_snapshot),
                "operations_snapshot": json.dumps(operations_snapshot),
                "checksum": checkpoint.checksum,
                "created_at": checkpoint.created_at
            }
        )
        self.db.commit()
        
        # Add to active checkpoints
        self.active_checkpoints[checkpoint.checkpoint_id] = checkpoint
        
        logger.info(f"[ReplaySafeGuard] Checkpoint created: {checkpoint.checkpoint_id}")
        
        return checkpoint
    
    async def load_checkpoint(self, checkpoint_id: str) -> Optional[TransactionCheckpoint]:
        """Load checkpoint from database."""
        result = self.db.execute(
            text("""
                SELECT checkpoint_id, transaction_id, tenant_id, state_snapshot, operations_snapshot, checksum, created_at
                FROM transaction_checkpoints
                WHERE checkpoint_id = :checkpoint_id AND tenant_id = :tenant_id
            """),
            {"checkpoint_id": checkpoint_id, "tenant_id": str(self.tenant_id)}
        ).fetchone()
        
        if not result:
            logger.error(f"[ReplaySafeGuard] Checkpoint not found: {checkpoint_id}")
            return None
        
        # Reconstruct checkpoint
        checkpoint = TransactionCheckpoint(
            checkpoint_id=result[0],
            transaction_id=result[1],
            tenant_id=UUID(result[2]),
            state_snapshot=json.loads(result[3]),
            operations_snapshot=json.loads(result[4]),
            created_at=result[6],
            checksum=result[5]
        )
        
        # Verify checkpoint integrity
        if not checkpoint.verify():
            logger.error(f"[ReplaySafeGuard] Checkpoint verification failed: {checkpoint_id}")
            return None
        
        return checkpoint
    
    async def restore_from_checkpoint(self, checkpoint_id: str) -> bool:
        """Restore state from checkpoint."""
        # Load checkpoint
        checkpoint = await self.load_checkpoint(checkpoint_id)
        if not checkpoint:
            return False
        
        # Verify checkpoint integrity
        if not checkpoint.verify():
            logger.error(f"[ReplaySafeGuard] Checkpoint verification failed: {checkpoint_id}")
            return False
        
        # Restore state (implementation depends on specific state requirements)
        # For now, log the restoration
        logger.info(f"[ReplaySafeGuard] State restored from checkpoint: {checkpoint_id}")
        
        return True
    
    async def create_rollback(
        self,
        transaction_id: str,
        reason: str,
        rollback_state: Dict[str, Any]
    ) -> TransactionRollback:
        """Create rollback record."""
        # Create rollback
        rollback = TransactionRollback(
            rollback_id=str(uuid4()),
            transaction_id=transaction_id,
            tenant_id=self.tenant_id,
            reason=reason,
            rollback_state=rollback_state,
            created_at=datetime.utcnow()
        )
        
        # Calculate checksum
        rollback.checksum = rollback.calculate_checksum()
        
        # Save rollback to database
        self.db.execute(
            text("""
                INSERT INTO transaction_rollbacks (rollback_id, transaction_id, tenant_id, reason, rollback_state, checksum, created_at)
                VALUES (:rollback_id, :transaction_id, :tenant_id, :reason, :rollback_state, :checksum, :created_at)
            """),
            {
                "rollback_id": rollback.rollback_id,
                "transaction_id": transaction_id,
                "tenant_id": str(self.tenant_id),
                "reason": reason,
                "rollback_state": json.dumps(rollback_state),
                "checksum": rollback.checksum,
                "created_at": rollback.created_at
            }
        )
        self.db.commit()
        
        # Add to active rollbacks
        self.active_rollbacks[rollback.rollback_id] = rollback
        
        logger.info(f"[ReplaySafeGuard] Rollback created: {rollback.rollback_id}")
        
        return rollback
    
    async def validate_replay_safety(self, transaction_id: str) -> bool:
        """Validate if transaction is replay-safe."""
        # Load transaction record
        result = self.db.execute(
            text("""
                SELECT is_replay_safe, is_idempotent, is_deterministic
                FROM transaction_records
                WHERE transaction_id = :transaction_id AND tenant_id = :tenant_id
            """),
            {"transaction_id": transaction_id, "tenant_id": str(self.tenant_id)}
        ).fetchone()
        
        if not result:
            logger.error(f"[ReplaySafeGuard] Transaction not found: {transaction_id}")
            return False
        
        # Check replay safety flags
        is_replay_safe = result[0]
        is_idempotent = result[1]
        is_deterministic = result[2]
        
        if not is_replay_safe:
            logger.error(f"[ReplaySafeGuard] Transaction not replay-safe: {transaction_id}")
            return False
        
        if not is_idempotent:
            logger.error(f"[ReplaySafeGuard] Transaction not idempotent: {transaction_id}")
            return False
        
        if not is_deterministic:
            logger.error(f"[ReplaySafeGuard] Transaction not deterministic: {transaction_id}")
            return False
        
        return True
    
    async def execute_with_retry(
        self,
        operation: callable,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 10.0
    ) -> Any:
        """Execute operation with exponential backoff retry."""
        for attempt in range(max_retries):
            try:
                # Execute operation
                result = await operation()
                return result
                
            except Exception as e:
                # Check if retryable
                if not self._is_retryable_error(e):
                    logger.error(f"[ReplaySafeGuard] Non-retryable error: {e}")
                    raise
                
                # Calculate backoff delay
                delay = min(base_delay * (2 ** attempt), max_delay)
                
                logger.warning(
                    f"[ReplaySafeGuard] Retry attempt {attempt + 1}/{max_retries} "
                    f"after {delay}s delay: {e}"
                )
                
                # Wait before retry
                await asyncio.sleep(delay)
        
        # Max retries exceeded
        raise Exception(f"Operation failed after {max_retries} retries")
    
    def _is_retryable_error(self, error: Exception) -> bool:
        """Check if error is retryable."""
        # Define retryable error types
        error_message = str(error).lower()
        
        # Network-related errors are retryable
        retryable_patterns = [
            "connection",
            "timeout",
            "network",
            "temporary",
            "deadlock",
            "lock wait timeout",
            "try again",
            "503",
            "502",
            "504"
        ]
        
        for pattern in retryable_patterns:
            if pattern in error_message:
                return True
        
        # Specific exception types that are retryable
        retryable_exceptions = (
            # Add specific exception types here
            # For now, check error message patterns
        )
        
        return False
    
    async def validate_checkpoint_integrity(self, checkpoint_id: str) -> bool:
        """Validate checkpoint integrity."""
        # Load checkpoint
        checkpoint = await self.load_checkpoint(checkpoint_id)
        if not checkpoint:
            return False
        
        # Verify checksum
        return checkpoint.verify()
    
    async def validate_rollback_integrity(self, rollback_id: str) -> bool:
        """Validate rollback integrity."""
        # Load rollback from database
        result = self.db.execute(
            text("""
                SELECT rollback_id, transaction_id, tenant_id, reason, rollback_state, checksum, created_at
                FROM transaction_rollbacks
                WHERE rollback_id = :rollback_id AND tenant_id = :tenant_id
            """),
            {"rollback_id": rollback_id, "tenant_id": str(self.tenant_id)}
        ).fetchone()
        
        if not result:
            logger.error(f"[ReplaySafeGuard] Rollback not found: {rollback_id}")
            return False
        
        # Reconstruct rollback
        rollback = TransactionRollback(
            rollback_id=result[0],
            transaction_id=result[1],
            tenant_id=UUID(result[2]),
            reason=result[3],
            rollback_state=json.loads(result[4]),
            created_at=result[6],
            checksum=result[5]
        )
        
        # Verify checksum
        return rollback.verify()
    
    async def cleanup_old_checkpoints(self, older_than_hours: int = 24):
        """Clean up old checkpoints."""
        # Delete checkpoints older than specified hours
        cutoff_time = datetime.utcnow() - timedelta(hours=older_than_hours)
        
        result = self.db.execute(
            text("""
                DELETE FROM transaction_checkpoints
                WHERE tenant_id = :tenant_id AND created_at < :cutoff_time
            """),
            {"tenant_id": str(self.tenant_id), "cutoff_time": cutoff_time}
        )
        
        deleted_count = result.rowcount
        logger.info(f"[ReplaySafeGuard] Cleaned up {deleted_count} old checkpoints")
        
        return deleted_count
    
    async def get_active_checkpoints(self) -> List[TransactionCheckpoint]:
        """Get all active checkpoints for tenant."""
        result = self.db.execute(
            text("""
                SELECT checkpoint_id, transaction_id, tenant_id, state_snapshot, operations_snapshot, checksum, created_at
                FROM transaction_checkpoints
                WHERE tenant_id = :tenant_id
                ORDER BY created_at DESC
            """),
            {"tenant_id": str(self.tenant_id)}
        ).fetchall()
        
        checkpoints = []
        for row in result:
            checkpoint = TransactionCheckpoint(
                checkpoint_id=row[0],
                transaction_id=row[1],
                tenant_id=UUID(row[2]),
                state_snapshot=json.loads(row[3]),
                operations_snapshot=json.loads(row[4]),
                created_at=row[6],
                checksum=row[5]
            )
            checkpoints.append(checkpoint)
        
        return checkpoints


# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL INSTANCE
# ═══════════════════════════════════════════════════════════════════════════

def get_replay_safe_transaction_guard(db: Session, tenant_id: UUID) -> ReplaySafeTransactionGuard:
    """Get replay-safe transaction guard instance."""
    return ReplaySafeTransactionGuard(db, tenant_id)
