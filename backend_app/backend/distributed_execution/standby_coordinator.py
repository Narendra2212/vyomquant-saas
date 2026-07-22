"""
Standby Coordinator for Institutional Recovery

Phase 4 — Standby Coordinator

Maintains synchronized state with the active coordinator and enables rapid,
deterministic failover while preserving replay guarantees.

Author: Principal Institutional Recovery and Failover Engineer
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from backend_app.core.cache.redis_manager import redis_manager

from .immutable_journal import immutable_journal
from .leader_election_manager import FencingToken, LeaderElectionManager
from .lease_manager import LeaseManager, LeaseRequest, LeaseType

logger = logging.getLogger("standby_coordinator")


class StandbyState(Enum):
    """Standby coordinator states."""
    STARTING = "starting"
    STANDBY = "standby"
    SYNCHRONIZING = "synchronizing"
    FAILOVER = "failover"
    ACTIVE = "active"
    STOPPING = "stopping"


@dataclass
class StateSnapshot:
    """State snapshot from active coordinator."""
    coordinator_id: str
    active_tasks: List[str]
    task_assignments: Dict[str, Dict[str, Any]]
    metrics: Dict[str, Any]
    last_heartbeat: Optional[str]
    timestamp: datetime
    sequence: int


@dataclass
class JournalCheckpoint:
    """Journal checkpoint from active coordinator."""
    checkpoint_id: str
    sequence: int
    timestamp: datetime
    data: Dict[str, Any]


class StandbyCoordinator:
    """Standby coordinator for hot standby topology."""
    
    def __init__(self, coordinator_id: str, is_active: bool = False):
        self.coordinator_id = coordinator_id
        self.is_active = is_active
        self.redis = redis_manager
        self.lease_manager = LeaseManager()
        self.leader_election_manager = LeaderElectionManager(coordinator_id)
        self.immutable_journal = immutable_journal
        
        # Standby state
        self.state = StandbyState.STARTING
        self.local_state: Dict[str, Any] = {}
        self.last_sync_time: Optional[datetime] = None
        self.last_checkpoint_sequence: int = 0
        
        # Synchronization configuration
        self.state_sync_interval = 1.0  # 1 second
        self.checkpoint_sync_interval = 10.0  # 10 seconds
        self.heartbeat_interval = 0.5  # 500ms
        
        # Synchronization channels
        self.state_channel = f"state_sync:{coordinator_id}"
        self.ack_channel = f"state_ack:{coordinator_id}"
        self.checkpoint_channel = f"journal_checkpoint:{coordinator_id}"
        self.heartbeat_channel = f"leadership_heartbeat:{coordinator_id}"
        self.transfer_channel = f"leadership_transfer:{coordinator_id}"
        
        # Background tasks
        self._state_sync_task: Optional[asyncio.Task] = None
        self._checkpoint_sync_task: Optional[asyncio.Task] = None
        self._heartbeat_monitor_task: Optional[asyncio.Task] = None
        self._transfer_listener_task: Optional[asyncio.Task] = None
        self._running = False
        
        logger.info(f"Standby coordinator initialized: {coordinator_id}, active={is_active}")
    
    async def initialize(self) -> bool:
        """Initialize standby coordinator."""
        try:
            # Initialize dependencies
            await self.lease_manager.initialize()
            await self.leader_election_manager.initialize()
            await self.immutable_journal.initialize()
            
            logger.info("Standby coordinator initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize standby coordinator: {e}")
            return False
    
    async def start(self) -> bool:
        """Start standby coordinator."""
        try:
            self._running = True
            self.state = StandbyState.STANDBY if not self.is_active else StandbyState.ACTIVE
            
            if self.is_active:
                # Start broadcasting state
                self._state_sync_task = asyncio.create_task(self._broadcast_state_loop())
                self._checkpoint_sync_task = asyncio.create_task(self._broadcast_checkpoint_loop())
                self._heartbeat_monitor_task = asyncio.create_task(self._broadcast_heartbeat_loop())
            else:
                # Start receiving state
                self._state_sync_task = asyncio.create_task(self._receive_state_loop())
                self._checkpoint_sync_task = asyncio.create_task(self._receive_checkpoint_loop())
                self._heartbeat_monitor_task = asyncio.create_task(self._monitor_leader_heartbeat())
                self._transfer_listener_task = asyncio.create_task(self._listen_for_transfer())
            
            logger.info(f"Standby coordinator started: {self.coordinator_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start standby coordinator: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop standby coordinator."""
        try:
            self._running = False
            self.state = StandbyState.STOPPING
            
            # Cancel all tasks
            tasks = [
                self._state_sync_task,
                self._checkpoint_sync_task,
                self._heartbeat_monitor_task,
                self._transfer_listener_task
            ]
            
            for task in tasks:
                if task:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            logger.info(f"Standby coordinator stopped: {self.coordinator_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop standby coordinator: {e}")
            return False
    
    async def _broadcast_state_loop(self):
        """Broadcast state to standby (active coordinator)."""
        while self._running:
            try:
                # Capture state
                state = await self._capture_state()
                
                # Validate state
                if not await self._validate_state(state):
                    logger.warning("State validation failed, skipping sync")
                    await asyncio.sleep(self.state_sync_interval)
                    continue
                
                # Publish state
                sequence = await self._get_next_sequence()
                await self.redis.publish(
                    self.state_channel,
                    json.dumps({
                        "state": state,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "sequence": sequence
                    })
                )
                
                # Wait for acknowledgment
                await self._wait_for_acknowledgment(sequence)
                
                self.last_sync_time = datetime.now(timezone.utc)
                await asyncio.sleep(self.state_sync_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"State broadcast error: {e}")
                await asyncio.sleep(self.state_sync_interval)
    
    async def _receive_state_loop(self):
        """Receive state from active (standby coordinator)."""
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.state_channel)
        
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    state = data["state"]
                    timestamp = datetime.fromisoformat(data["timestamp"])
                    sequence = data["sequence"]
                    
                    # Validate state
                    if not await self._validate_state(state):
                        logger.warning("Received invalid state, rejecting")
                        continue
                    
                    # Apply state
                    await self._apply_state(state)
                    
                    self.last_sync_time = timestamp
                    
                    # Send acknowledgment
                    await self.redis.publish(
                        self.ack_channel,
                        json.dumps({
                            "sequence": sequence,
                            "acknowledged_at": datetime.now(timezone.utc).isoformat()
                        })
                    )
                    
                except Exception as e:
                    logger.error(f"State receive error: {e}")
    
    async def _broadcast_checkpoint_loop(self):
        """Broadcast journal checkpoints to standby (active coordinator)."""
        while self._running:
            try:
                # Get current journal sequence
                current_sequence = await self.immutable_journal._get_current_sequence(self.coordinator_id)
                
                # Create checkpoint if sequence advanced
                if current_sequence > self.last_checkpoint_sequence:
                    checkpoint = await self.immutable_journal.create_checkpoint(
                        self.coordinator_id,
                        {
                            "sequence": current_sequence,
                            "active_tasks": list(self.local_state.get("active_tasks", [])),
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }
                    )
                    
                    # Publish checkpoint
                    await self.redis.publish(
                        self.checkpoint_channel,
                        json.dumps({
                            "checkpoint_id": checkpoint["checkpoint_id"],
                            "sequence": current_sequence,
                            "timestamp": checkpoint["timestamp"],
                            "data": checkpoint["data"]
                        })
                    )
                    
                    self.last_checkpoint_sequence = current_sequence
                
                await asyncio.sleep(self.checkpoint_sync_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Checkpoint broadcast error: {e}")
                await asyncio.sleep(self.checkpoint_sync_interval)
    
    async def _receive_checkpoint_loop(self):
        """Receive journal checkpoints from active (standby coordinator)."""
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.checkpoint_channel)
        
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    checkpoint_id = data["checkpoint_id"]
                    sequence = data["sequence"]
                    
                    # Validate checkpoint
                    if not await self._validate_checkpoint(data):
                        logger.warning("Received invalid checkpoint, rejecting")
                        continue
                    
                    # Apply checkpoint
                    await self._apply_checkpoint(data)
                    
                    self.last_checkpoint_sequence = sequence
                    
                    logger.info(f"Applied journal checkpoint {checkpoint_id} at sequence {sequence}")
                    
                except Exception as e:
                    logger.error(f"Checkpoint receive error: {e}")
    
    async def _broadcast_heartbeat_loop(self):
        """Broadcast leadership heartbeat (active coordinator)."""
        while self._running:
            try:
                heartbeat = {
                    "coordinator_id": self.coordinator_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "state": self.state.value
                }
                
                await self.redis.publish(
                    self.heartbeat_channel,
                    json.dumps(heartbeat)
                )
                
                await asyncio.sleep(self.heartbeat_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat broadcast error: {e}")
                await asyncio.sleep(self.heartbeat_interval)
    
    async def _monitor_leader_heartbeat(self):
        """Monitor leader heartbeat (standby coordinator)."""
        while self._running:
            try:
                # Check leader heartbeat
                leader_heartbeat = await self._get_leader_heartbeat()
                
                if not leader_heartbeat:
                    # Leader heartbeat missing, trigger failover
                    logger.warning("Leader heartbeat missing, triggering failover")
                    await self._trigger_failover("leader_heartbeat_missing")
                    break
                
                # Check heartbeat age
                heartbeat_age = (datetime.now(timezone.utc) - 
                                 datetime.fromisoformat(leader_heartbeat["timestamp"])).total_seconds()
                
                if heartbeat_age > 5.0:  # 5 seconds timeout
                    logger.warning("Leader heartbeat timeout, triggering failover")
                    await self._trigger_failover("leader_heartbeat_timeout")
                    break
                
                await asyncio.sleep(1.0)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Leader heartbeat monitoring error: {e}")
                await asyncio.sleep(1.0)
    
    async def _listen_for_transfer(self):
        """Listen for leadership transfer requests (standby coordinator)."""
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.transfer_channel)
        
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    transfer_context = json.loads(message["data"])
                    
                    # Accept leadership transfer
                    success = await self._accept_leadership_transfer(transfer_context)
                    
                    if success:
                        logger.info(f"Leadership transfer accepted: {transfer_context['transfer_id']}")
                        break
                    else:
                        logger.warning(f"Leadership transfer rejected: {transfer_context['transfer_id']}")
                        
                except Exception as e:
                    logger.error(f"Leadership transfer error: {e}")
    
    async def _capture_state(self) -> Dict[str, Any]:
        """Capture current coordinator state."""
        return {
            "coordinator_id": self.coordinator_id,
            "active_tasks": list(self.local_state.get("active_tasks", [])),
            "task_assignments": self.local_state.get("task_assignments", {}),
            "metrics": self.local_state.get("metrics", {}),
            "last_heartbeat": self.local_state.get("last_heartbeat"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def _validate_state(self, state: Dict[str, Any]) -> bool:
        """Validate state integrity."""
        # Check required fields
        required_fields = ["coordinator_id", "active_tasks", "task_assignments", "metrics"]
        if not all(field in state for field in required_fields):
            return False
        
        # Check coordinator ID matches
        if state["coordinator_id"] != self.coordinator_id:
            return False
        
        return True
    
    async def _apply_state(self, state: Dict[str, Any]):
        """Apply received state locally."""
        self.local_state["active_tasks"] = set(state["active_tasks"])
        self.local_state["task_assignments"] = state["task_assignments"]
        self.local_state["metrics"] = state["metrics"]
        
        if state.get("last_heartbeat"):
            self.local_state["last_heartbeat"] = datetime.fromisoformat(state["last_heartbeat"])
    
    async def _validate_checkpoint(self, checkpoint: Dict[str, Any]) -> bool:
        """Validate checkpoint integrity."""
        # Check required fields
        required_fields = ["checkpoint_id", "sequence", "timestamp", "data"]
        if not all(field in checkpoint for field in required_fields):
            return False
        
        # Check sequence is increasing
        if checkpoint["sequence"] <= self.last_checkpoint_sequence:
            return False
        
        # Verify checkpoint in journal
        journal_checkpoints = await self.immutable_journal.get_checkpoints(self.coordinator_id)
        checkpoint_ids = [cp["checkpoint_id"] for cp in journal_checkpoints]
        
        return checkpoint["checkpoint_id"] in checkpoint_ids
    
    async def _apply_checkpoint(self, checkpoint: Dict[str, Any]):
        """Apply received checkpoint locally."""
        checkpoint_data = checkpoint["data"]
        
        if "active_tasks" in checkpoint_data:
            self.local_state["active_tasks"] = set(checkpoint_data["active_tasks"])
        
        self.last_checkpoint_sequence = checkpoint["sequence"]
    
    async def _wait_for_acknowledgment(self, sequence: int, timeout: float = 5.0):
        """Wait for state acknowledgment."""
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.ack_channel)
        
        start_time = datetime.now(timezone.utc)
        
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    if data["sequence"] == sequence:
                        return
                except Exception:
                    pass
                
                # Check timeout
                if (datetime.now(timezone.utc) - start_time).total_seconds() > timeout:
                    logger.warning(f"Acknowledgment timeout for sequence {sequence}")
                    return
    
    async def _get_leader_heartbeat(self) -> Optional[Dict[str, Any]]:
        """Get leader heartbeat."""
        try:
            heartbeat_key = "heartbeat:coordinator:leader"
            heartbeat_data = await self.redis.get(heartbeat_key)
            
            if heartbeat_data:
                return json.loads(heartbeat_data)
            else:
                return None
                
        except Exception as e:
            logger.error(f"Failed to get leader heartbeat: {e}")
            return None
    
    async def _trigger_failover(self, reason: str):
        """Trigger failover process."""
        try:
            logger.info(f"Triggering failover: {reason}")
            
            # Generate new fencing token
            new_term = await self._get_next_term()
            fencing_token = await self.leader_election_manager._generate_fencing_token(
                self.coordinator_id,
                new_term
            )
            
            # Acquire leadership lease
            lease_request = LeaseRequest(
                resource_id="execution_coordinator",
                requester_id=self.coordinator_id,
                lease_type=LeaseType.EXCLUSIVE,
                ttl_seconds=3600,
                auto_renew=True,
                max_renewals=100,
                priority=20,
                metadata={
                    "fencing_token": fencing_token.token_id,
                    "term": new_term,
                    "failover_reason": reason
                }
            )
            
            lease_result = await self.lease_manager.acquire_lease(lease_request)
            
            if not lease_result.success:
                logger.error("Failed to acquire leadership lease during failover")
                return False
            
            # Validate journal state
            if not await self._validate_journal_state():
                logger.error("Journal state validation failed during failover")
                await self.lease_manager.release_lease(lease_result.lease.lease_id)
                return False
            
            # Broadcast leadership
            await self._broadcast_leadership(fencing_token, new_term)
            
            # Become active
            self.state = StandbyState.ACTIVE
            self.is_active = True
            
            # Restart tasks as active
            await self.stop()
            await self.start()
            
            logger.info(f"Failover completed: {reason}")
            return True
            
        except Exception as e:
            logger.error(f"Failover failed: {e}")
            return False
    
    async def _accept_leadership_transfer(self, transfer_context: Dict[str, Any]) -> bool:
        """Accept leadership transfer."""
        try:
            transfer_id = transfer_context["transfer_id"]
            
            # Validate transfer request
            if not await self._validate_transfer_request(transfer_context):
                logger.warning("Invalid transfer request")
                return False
            
            # Apply state snapshot
            await self._apply_state_snapshot(transfer_context["state_snapshot"])
            
            # Validate journal sequence
            if not await self._validate_journal_sequence(transfer_context["journal_sequence"]):
                logger.warning("Journal sequence validation failed")
                return False
            
            # Generate new fencing token
            new_term = await self._get_next_term()
            fencing_token = await self.leader_election_manager._generate_fencing_token(
                self.coordinator_id,
                new_term
            )
            
            # Acquire leadership lease
            lease_request = LeaseRequest(
                resource_id="execution_coordinator",
                requester_id=self.coordinator_id,
                lease_type=LeaseType.EXCLUSIVE,
                ttl_seconds=3600,
                auto_renew=True,
                max_renewals=100,
                priority=20,
                metadata={
                    "fencing_token": fencing_token.token_id,
                    "term": new_term,
                    "transfer_id": transfer_id
                }
            )
            
            lease_result = await self.lease_manager.acquire_lease(lease_request)
            
            if not lease_result.success:
                logger.error("Failed to acquire leadership lease")
                return False
            
            # Broadcast leadership
            await self._broadcast_leadership(fencing_token, new_term)
            
            # Send acknowledgment
            ack_channel = f"leadership_ack:{self.coordinator_id}"
            await self.redis.publish(
                ack_channel,
                json.dumps({
                    "transfer_id": transfer_id,
                    "acknowledged_at": datetime.now(timezone.utc).isoformat(),
                    "fencing_token": fencing_token.token_id,
                    "term": new_term
                })
            )
            
            # Become active
            self.state = StandbyState.ACTIVE
            self.is_active = True
            
            return True
            
        except Exception as e:
            logger.error(f"Leadership transfer acceptance failed: {e}")
            return False
    
    async def _validate_transfer_request(self, transfer_context: Dict[str, Any]) -> bool:
        """Validate transfer request."""
        # Check required fields
        required_fields = ["transfer_id", "from_coordinator", "reason", "initiated_at", "state_snapshot", "journal_sequence"]
        if not all(field in transfer_context for field in required_fields):
            return False
        
        # Check transfer is recent (within 30 seconds)
        initiated_at = datetime.fromisoformat(transfer_context["initiated_at"])
        if (datetime.now(timezone.utc) - initiated_at).total_seconds() > 30:
            return False
        
        # Validate state snapshot
        if not await self._validate_state_snapshot(transfer_context["state_snapshot"]):
            return False
        
        return True
    
    async def _apply_state_snapshot(self, state_snapshot: Dict[str, Any]):
        """Apply state snapshot."""
        self.local_state = state_snapshot.copy()
    
    async def _validate_state_snapshot(self, state_snapshot: Dict[str, Any]) -> bool:
        """Validate state snapshot."""
        return await self._validate_state(state_snapshot)
    
    async def _validate_journal_sequence(self, sequence: int) -> bool:
        """Validate journal sequence."""
        current_sequence = await self.immutable_journal._get_current_sequence(self.coordinator_id)
        return sequence <= current_sequence
    
    async def _validate_journal_state(self) -> bool:
        """Validate journal state."""
        try:
            # Get last checkpoint
            checkpoints = await self.immutable_journal.get_checkpoints(self.coordinator_id)
            
            if not checkpoints:
                logger.warning("No journal checkpoints found")
                return True
            
            last_checkpoint = checkpoints[0]
            
            # Verify checkpoint integrity
            if not await self.immutable_journal.verify_checkpoint(last_checkpoint):
                logger.error("Journal checkpoint verification failed")
                return False
            
            # Verify checkpoint is recent
            checkpoint_age = (datetime.now(timezone.utc) - 
                             datetime.fromisoformat(last_checkpoint["timestamp"])).total_seconds()
            
            if checkpoint_age > 300:  # 5 minutes
                logger.error("Journal checkpoint too old")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Journal state validation failed: {e}")
            return False
    
    async def _broadcast_leadership(self, fencing_token: FencingToken, term: int):
        """Broadcast leadership to all components."""
        leadership_message = {
            "coordinator_id": self.coordinator_id,
            "term": term,
            "fencing_token": fencing_token.token_id,
            "fencing_token_hash": fencing_token.token_hash,
            "became_leader_at": fencing_token.generated_at.isoformat(),
            "expires_at": fencing_token.expires_at.isoformat()
        }
        
        await self.redis.publish(
            "leadership_announcement",
            json.dumps(leadership_message)
        )
    
    async def _get_next_sequence(self) -> int:
        """Get next sequence number."""
        try:
            sequence_key = f"standby:sequence:{self.coordinator_id}"
            sequence = await self.redis.incr(sequence_key)
            await self.redis.expire(sequence_key, 3600)
            return sequence
            
        except Exception as e:
            logger.error(f"Failed to get next sequence: {e}")
            return 0
    
    async def _get_next_term(self) -> int:
        """Get next term number."""
        try:
            term_key = "leader:term:current"
            current_term = await self.redis.get(term_key)
            
            if current_term:
                term = int(current_term) + 1
            else:
                term = 1
            
            await self.redis.set(term_key, term)
            return term
            
        except Exception as e:
            logger.error(f"Failed to get next term: {e}")
            return 1
    
    async def get_standby_status(self) -> Dict[str, Any]:
        """Get current standby status."""
        return {
            "coordinator_id": self.coordinator_id,
            "is_active": self.is_active,
            "state": self.state.value,
            "last_sync_time": self.last_sync_time.isoformat() if self.last_sync_time else None,
            "last_checkpoint_sequence": self.last_checkpoint_sequence
        }


# Global instance
_standby_coordinator: Optional[StandbyCoordinator] = None


def get_standby_coordinator(coordinator_id: str = "default", is_active: bool = False) -> StandbyCoordinator:
    """Get or create standby coordinator instance."""
    global _standby_coordinator
    if _standby_coordinator is None:
        _standby_coordinator = StandbyCoordinator(coordinator_id, is_active)
    return _standby_coordinator
