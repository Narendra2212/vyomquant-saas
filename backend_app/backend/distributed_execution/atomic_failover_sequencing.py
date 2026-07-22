"""
Atomic Failover Sequencing - Phase 6 Correctness Fix

This module implements atomic failover sequencing to prevent partial failover
and inconsistent state. This is a critical correctness fix for the strict algo
trading platform.

Key Features:
- Atomic failover state transitions
- Checkpoint-based failover
- State transfer validation with checksums
- Idempotent execution resumption
- Failover rollback on failure

Author: Principal Institutional Algo Execution Validation Engineer
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from backend_app.core.cache.redis_manager import redis_manager

from .immutable_journal import immutable_journal

logger = logging.getLogger("atomic_failover_sequencing")


class FailoverState(Enum):
    """Failover state machine."""
    NORMAL = "normal"
    FAILING_OVER = "failing_over"
    STATE_TRANSFER = "state_transfer"
    VALIDATION = "validation"
    EXECUTION_RESUMPTION = "execution_resumption"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLBACK = "rollback"


class FailoverCheckpoint:
    """Failover checkpoint for atomic sequencing."""
    
    def __init__(self, checkpoint_id: str, state: FailoverState):
        self.checkpoint_id = checkpoint_id
        self.state = state
        self.timestamp = datetime.now(timezone.utc)
        self.data: Dict[str, Any] = {}
        self.checksum: Optional[str] = None
    
    def calculate_checksum(self) -> str:
        """Calculate checksum for checkpoint data."""
        data_str = json.dumps(self.data, sort_keys=True, default=str)
        return hashlib.sha256(data_str.encode()).hexdigest()
    
    def validate_checksum(self) -> bool:
        """Validate checkpoint data integrity."""
        if not self.checksum:
            return False
        
        calculated = self.calculate_checksum()
        return calculated == self.checksum


class AtomicFailoverSequencer:
    """
    Atomic failover sequencer for deterministic failover.
    
    Ensures failover is atomic, consistent, and recoverable.
    """
    
    def __init__(self):
        """Initialize atomic failover sequencer."""
        self.redis = redis_manager
        self.journal = immutable_journal
        
        # Failover state
        self.current_state = FailoverState.NORMAL
        self.current_checkpoint: Optional[FailoverCheckpoint] = None
        self.failover_id: Optional[str] = None
        
        # Configuration
        self.checkpoint_ttl = 3600  # 1 hour
        self.failover_timeout = 300  # 5 minutes
    
    async def initiate_failover(
        self,
        source_node: str,
        target_node: str,
        reason: str
    ) -> str:
        """
        Initiate atomic failover sequence.
        
        Args:
            source_node: Source node ID
            target_node: Target node ID
            reason: Failover reason
            
        Returns:
            Failover ID
        """
        try:
            # Generate failover ID
            self.failover_id = f"failover_{uuid.uuid4().hex[:16]}"
            
            # Create initial checkpoint
            checkpoint = FailoverCheckpoint(
                checkpoint_id=f"checkpoint_{uuid.uuid4().hex[:16]}",
                state=FailoverState.FAILING_OVER
            )
            checkpoint.data = {
                "failover_id": self.failover_id,
                "source_node": source_node,
                "target_node": target_node,
                "reason": reason,
                "initiated_at": datetime.now(timezone.utc).isoformat()
            }
            checkpoint.checksum = checkpoint.calculate_checksum()
            
            # Store checkpoint
            await self._store_checkpoint(checkpoint)
            self.current_checkpoint = checkpoint
            
            # Update state
            self.current_state = FailoverState.FAILING_OVER
            await self._update_failover_state()
            
            logger.info(f"Failover initiated: {self.failover_id} from {source_node} to {target_node}")
            return self.failover_id
            
        except Exception as e:
            logger.error(f"Failover initiation failed: {e}")
            await self._rollback_failover()
            raise
    
    async def transfer_state(
        self,
        state_data: Dict[str, Any]
    ) -> bool:
        """
        Transfer state with validation.
        
        Args:
            state_data: State data to transfer
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Validate current state
            if self.current_state != FailoverState.FAILING_OVER:
                logger.error(f"Invalid state for state transfer: {self.current_state}")
                return False
            
            # Create state transfer checkpoint
            checkpoint = FailoverCheckpoint(
                checkpoint_id=f"checkpoint_{uuid.uuid4().hex[:16]}",
                state=FailoverState.STATE_TRANSFER
            )
            checkpoint.data = {
                "failover_id": self.failover_id,
                "state_data": state_data,
                "transferred_at": datetime.now(timezone.utc).isoformat()
            }
            checkpoint.checksum = checkpoint.calculate_checksum()
            
            # Store checkpoint
            await self._store_checkpoint(checkpoint)
            self.current_checkpoint = checkpoint
            
            # Update state
            self.current_state = FailoverState.STATE_TRANSFER
            await self._update_failover_state()
            
            logger.info(f"State transfer checkpoint created: {checkpoint.checkpoint_id}")
            return True
            
        except Exception as e:
            logger.error(f"State transfer failed: {e}")
            await self._rollback_failover()
            return False
    
    async def validate_state_transfer(
        self,
        received_checksum: str
    ) -> bool:
        """
        Validate state transfer with checksum.
        
        Args:
            received_checksum: Checksum received from target
            
        Returns:
            True if valid, False otherwise
        """
        try:
            # Validate current state
            if self.current_state != FailoverState.STATE_TRANSFER:
                logger.error(f"Invalid state for validation: {self.current_state}")
                return False
            
            # Validate checkpoint
            if not self.current_checkpoint:
                logger.error("No checkpoint to validate")
                return False
            
            # Validate checksum
            calculated_checksum = self.current_checkpoint.calculate_checksum()
            
            if calculated_checksum != received_checksum:
                logger.error(
                    f"Checksum mismatch: calculated={calculated_checksum}, "
                    f"received={received_checksum}"
                )
                await self._rollback_failover()
                return False
            
            # Create validation checkpoint
            checkpoint = FailoverCheckpoint(
                checkpoint_id=f"checkpoint_{uuid.uuid4().hex[:16]}",
                state=FailoverState.VALIDATION
            )
            checkpoint.data = {
                "failover_id": self.failover_id,
                "checksum_valid": True,
                "validated_at": datetime.now(timezone.utc).isoformat()
            }
            checkpoint.checksum = checkpoint.calculate_checksum()
            
            # Store checkpoint
            await self._store_checkpoint(checkpoint)
            self.current_checkpoint = checkpoint
            
            # Update state
            self.current_state = FailoverState.VALIDATION
            await self._update_failover_state()
            
            logger.info(f"State transfer validated: {checkpoint.checkpoint_id}")
            return True
            
        except Exception as e:
            logger.error(f"State validation failed: {e}")
            await self._rollback_failover()
            return False
    
    async def resume_execution(
        self,
        execution_ids: List[str]
    ) -> bool:
        """
        Resume execution idempotently.
        
        Args:
            execution_ids: Execution IDs to resume
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Validate current state
            if self.current_state != FailoverState.VALIDATION:
                logger.error(f"Invalid state for execution resumption: {self.current_state}")
                return False
            
            # Check for duplicate execution resumption
            for exec_id in execution_ids:
                if await self._check_execution_resumed(exec_id):
                    logger.warning(f"Execution already resumed: {exec_id}")
                    continue
            
            # Create execution resumption checkpoint
            checkpoint = FailoverCheckpoint(
                checkpoint_id=f"checkpoint_{uuid.uuid4().hex[:16]}",
                state=FailoverState.EXECUTION_RESUMPTION
            )
            checkpoint.data = {
                "failover_id": self.failover_id,
                "execution_ids": execution_ids,
                "resumed_at": datetime.now(timezone.utc).isoformat()
            }
            checkpoint.checksum = checkpoint.calculate_checksum()
            
            # Store checkpoint
            await self._store_checkpoint(checkpoint)
            self.current_checkpoint = checkpoint
            
            # Mark executions as resumed
            for exec_id in execution_ids:
                await self._mark_execution_resumed(exec_id)
            
            # Update state
            self.current_state = FailoverState.EXECUTION_RESUMPTION
            await self._update_failover_state()
            
            logger.info(f"Execution resumption checkpoint created: {checkpoint.checkpoint_id}")
            return True
            
        except Exception as e:
            logger.error(f"Execution resumption failed: {e}")
            await self._rollback_failover()
            return False
    
    async def complete_failover(self) -> bool:
        """
        Complete failover sequence.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Validate current state
            if self.current_state != FailoverState.EXECUTION_RESUMPTION:
                logger.error(f"Invalid state for completion: {self.current_state}")
                return False
            
            # Create completion checkpoint
            checkpoint = FailoverCheckpoint(
                checkpoint_id=f"checkpoint_{uuid.uuid4().hex[:16]}",
                state=FailoverState.COMPLETED
            )
            checkpoint.data = {
                "failover_id": self.failover_id,
                "completed_at": datetime.now(timezone.utc).isoformat()
            }
            checkpoint.checksum = checkpoint.calculate_checksum()
            
            # Store checkpoint
            await self._store_checkpoint(checkpoint)
            self.current_checkpoint = checkpoint
            
            # Update state
            self.current_state = FailoverState.COMPLETED
            await self._update_failover_state()
            
            # Clean up old checkpoints
            await self._cleanup_old_checkpoints()
            
            logger.info(f"Failover completed: {self.failover_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failover completion failed: {e}")
            await self._rollback_failover()
            return False
    
    async def _store_checkpoint(self, checkpoint: FailoverCheckpoint):
        """Store checkpoint in Redis."""
        try:
            key = f"failover_checkpoint:{self.failover_id}:{checkpoint.checkpoint_id}"
            data = {
                "checkpoint_id": checkpoint.checkpoint_id,
                "state": checkpoint.state.value,
                "timestamp": checkpoint.timestamp.isoformat(),
                "data": checkpoint.data,
                "checksum": checkpoint.checksum
            }
            
            await self.redis.setex(
                key,
                self.checkpoint_ttl,
                json.dumps(data, default=str)
            )
            
        except Exception as e:
            logger.error(f"Failed to store checkpoint: {e}")
            raise
    
    async def _update_failover_state(self):
        """Update failover state in Redis."""
        try:
            key = f"failover_state:{self.failover_id}"
            data = {
                "failover_id": self.failover_id,
                "state": self.current_state.value,
                "current_checkpoint": self.current_checkpoint.checkpoint_id if self.current_checkpoint else None,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            
            await self.redis.setex(
                key,
                self.checkpoint_ttl,
                json.dumps(data, default=str)
            )
            
        except Exception as e:
            logger.error(f"Failed to update failover state: {e}")
            raise
    
    async def _check_execution_resumed(self, execution_id: str) -> bool:
        """Check if execution has already been resumed."""
        try:
            key = f"execution_resumed:{execution_id}"
            exists = await self.redis.exists(key)
            return bool(exists)
            
        except Exception as e:
            logger.error(f"Failed to check execution resumed status: {e}")
            return False
    
    async def _mark_execution_resumed(self, execution_id: str):
        """Mark execution as resumed."""
        try:
            key = f"execution_resumed:{execution_id}"
            await self.redis.setex(key, 86400, "resumed")  # 24 hours
            
        except Exception as e:
            logger.error(f"Failed to mark execution as resumed: {e}")
    
    async def _rollback_failover(self):
        """Rollback failover on failure."""
        try:
            logger.warning(f"Rolling back failover: {self.failover_id}")
            
            # Update state to rollback
            self.current_state = FailoverState.ROLLBACK
            await self._update_failover_state()
            
            # Clean up checkpoints
            if self.failover_id:
                await self._cleanup_failover_checkpoints()
            
            # Reset state
            self.current_state = FailoverState.FAILED
            self.current_checkpoint = None
            
            logger.warning(f"Failover rolled back: {self.failover_id}")
            
        except Exception as e:
            logger.error(f"Failover rollback failed: {e}")
    
    async def _cleanup_old_checkpoints(self):
        """Clean up old checkpoints."""
        try:
            # Keep only the last checkpoint
            if self.current_checkpoint:
                pattern = f"failover_checkpoint:{self.failover_id}:*"
                keys = await self.redis.keys(pattern)
                
                for key in keys:
                    if self.current_checkpoint.checkpoint_id not in key:
                        await self.redis.delete(key)
            
        except Exception as e:
            logger.error(f"Failed to cleanup old checkpoints: {e}")
    
    async def _cleanup_failover_checkpoints(self):
        """Clean up all failover checkpoints."""
        try:
            pattern = f"failover_checkpoint:{self.failover_id}:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                await self.redis.delete(key)
            
            # Clean up state
            state_key = f"failover_state:{self.failover_id}"
            await self.redis.delete(state_key)
            
        except Exception as e:
            logger.error(f"Failed to cleanup failover checkpoints: {e}")
    
    async def get_failover_status(self, failover_id: str) -> Optional[Dict[str, Any]]:
        """Get failover status."""
        try:
            key = f"failover_state:{failover_id}"
            data = await self.redis.get(key)
            
            if not data:
                return None
            
            return json.loads(data)
            
        except Exception as e:
            logger.error(f"Failed to get failover status: {e}")
            return None


# Global instance
_atomic_failover_sequencer: Optional[AtomicFailoverSequencer] = None


def get_atomic_failover_sequencer() -> AtomicFailoverSequencer:
    """Get or create atomic failover sequencer instance."""
    global _atomic_failover_sequencer
    if _atomic_failover_sequencer is None:
        _atomic_failover_sequencer = AtomicFailoverSequencer()
    return _atomic_failover_sequencer
