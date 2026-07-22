"""
Leader Election Manager for Institutional Recovery

Phase 4 — Leader Election Manager

Provides deterministic, safe leader selection while preserving replay guarantees
and preventing split-brain scenarios.

Author: Principal Institutional Recovery and Failover Engineer
"""

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from backend_app.core.cache.redis_manager import redis_manager

from .heartbeat_manager import HeartbeatManager
from .immutable_journal import immutable_journal
from .lease_manager import LeaseManager, LeaseRequest, LeaseType

logger = logging.getLogger("leader_election_manager")


class ElectionState(Enum):
    """Leader election states."""
    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"


@dataclass
class VoteRequest:
    """Vote request from candidate."""
    candidate_id: str
    term: int
    last_log_index: int
    last_log_term: int
    priority: int
    fencing_token: str
    journal_checkpoint: str


@dataclass
class VoteResponse:
    """Vote response from node."""
    voter_id: str
    term: int
    vote_granted: bool
    reason: Optional[str] = None


@dataclass
class FencingToken:
    """Fencing token for split-brain prevention."""
    token_id: str
    coordinator_id: str
    term: int
    generated_at: datetime
    expires_at: datetime
    signature: str
    token_hash: str


@dataclass
class Term:
    """Election term."""
    term_number: int
    leader_id: Optional[str]
    started_at: datetime
    votes_cast: int
    votes_required: int
    election_status: str


class LeaderElectionManager:
    """Leader election manager for institutional recovery."""
    
    def __init__(self, coordinator_id: str):
        self.coordinator_id = coordinator_id
        self.redis = redis_manager
        self.lease_manager = LeaseManager()
        self.heartbeat_manager = HeartbeatManager()
        self.immutable_journal = immutable_journal
        
        # Election state
        self.state = ElectionState.FOLLOWER
        self.current_term: int = 0
        self.voted_for: Optional[str] = None
        self.leader_id: Optional[str] = None
        
        # Configuration
        self.election_timeout_min = 0.15  # 150ms
        self.election_timeout_max = 0.30  # 300ms
        self.heartbeat_interval = 0.5  # 500ms
        self.votes_required = 2  # Majority of 3 nodes
        
        # Background tasks
        self._election_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Fencing token
        self.signing_key = b"leader_election_signing_key_v1"
        
        logger.info(f"Leader election manager initialized: {coordinator_id}")
    
    async def initialize(self) -> bool:
        """Initialize leader election manager."""
        try:
            # Initialize dependencies
            await self.lease_manager.initialize()
            await self.heartbeat_manager.initialize()
            await self.immutable_journal.initialize()
            
            # Load current term
            self.current_term = await self._get_current_term()
            
            logger.info("Leader election manager initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize leader election manager: {e}")
            return False
    
    async def start(self) -> bool:
        """Start leader election manager."""
        try:
            self._running = True
            
            # Start election task
            self._election_task = asyncio.create_task(self._election_loop())
            
            # Start heartbeat task if leader
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            
            logger.info(f"Leader election manager started: {self.coordinator_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start leader election manager: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop leader election manager."""
        try:
            self._running = False
            
            # Cancel tasks
            if self._election_task:
                self._election_task.cancel()
                try:
                    await self._election_task
                except asyncio.CancelledError:
                    pass
            
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass
            
            # Step down if leader
            if self.state == ElectionState.LEADER:
                await self._step_down()
            
            logger.info(f"Leader election manager stopped: {self.coordinator_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop leader election manager: {e}")
            return False
    
    async def _election_loop(self):
        """Main election loop."""
        while self._running:
            try:
                if self.state == ElectionState.FOLLOWER:
                    # Check for election timeout
                    last_heartbeat = await self._get_last_leader_heartbeat()
                    
                    if not last_heartbeat:
                        # No heartbeat, start election
                        await self._start_election()
                    else:
                        # Check heartbeat age
                        heartbeat_age = (datetime.now(timezone.utc) - 
                                         datetime.fromisoformat(last_heartbeat["timestamp"])).total_seconds()
                        
                        if heartbeat_age > self.election_timeout_max:
                            # Heartbeat timeout, start election
                            await self._start_election()
                
                elif self.state == ElectionState.CANDIDATE:
                    # Election already in progress, wait for result
                    await asyncio.sleep(self.election_timeout_min)
                
                elif self.state == ElectionState.LEADER:
                    # Send heartbeats
                    await self._send_leader_heartbeat()
                
                await asyncio.sleep(self.election_timeout_min)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Election loop error: {e}")
                await asyncio.sleep(self.election_timeout_min)
    
    async def _heartbeat_loop(self):
        """Heartbeat loop for leader."""
        while self._running:
            try:
                if self.state == ElectionState.LEADER:
                    await self._send_leader_heartbeat()
                
                await asyncio.sleep(self.heartbeat_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat loop error: {e}")
                await asyncio.sleep(self.heartbeat_interval)
    
    async def _start_election(self):
        """Start leader election."""
        try:
            # Increment term
            self.current_term += 1
            await self._set_current_term(self.current_term)
            
            # Become candidate
            self.state = ElectionState.CANDIDATE
            self.voted_for = self.coordinator_id
            
            # Vote for self
            await self._store_vote(self.current_term, self.coordinator_id, self.coordinator_id)
            
            # Generate fencing token
            fencing_token = await self._generate_fencing_token(self.coordinator_id, self.current_term)
            
            # Get journal checkpoint
            journal_checkpoint = await self._get_journal_checkpoint()
            
            # Create vote request
            vote_request = VoteRequest(
                candidate_id=self.coordinator_id,
                term=self.current_term,
                last_log_index=await self._get_last_log_index(),
                last_log_term=self.current_term,
                priority=await self._get_priority(),
                fencing_token=fencing_token.token_id,
                journal_checkpoint=journal_checkpoint
            )
            
            # Request votes from all nodes
            votes = await self._request_votes(vote_request)
            
            # Count votes
            votes_granted = sum(1 for vote in votes if vote.vote_granted)
            
            # Check if won election
            if votes_granted >= self.votes_required:
                # Become leader
                await self._become_leader(fencing_token)
            else:
                # Lost election, become follower
                self.state = ElectionState.FOLLOWER
                self.voted_for = None
            
            logger.info(
                f"Election completed: term={self.current_term}, "
                f"votes_granted={votes_granted}, "
                f"state={self.state.value}"
            )
            
        except Exception as e:
            logger.error(f"Election failed: {e}")
            self.state = ElectionState.FOLLOWER
    
    async def _request_votes(self, vote_request: VoteRequest) -> List[VoteResponse]:
        """Request votes from all nodes."""
        try:
            # Get all coordinator nodes
            nodes = await self._get_coordinator_nodes()
            
            votes = []
            
            for node_id in nodes:
                if node_id == self.coordinator_id:
                    # Vote for self
                    votes.append(VoteResponse(
                        voter_id=self.coordinator_id,
                        term=vote_request.term,
                        vote_granted=True
                    ))
                    continue
                
                # Request vote from node
                try:
                    vote = await self._request_vote_from_node(node_id, vote_request)
                    votes.append(vote)
                except Exception as e:
                    logger.error(f"Failed to request vote from {node_id}: {e}")
                    votes.append(VoteResponse(
                        voter_id=node_id,
                        term=vote_request.term,
                        vote_granted=False,
                        reason=str(e)
                    ))
            
            return votes
            
        except Exception as e:
            logger.error(f"Vote request failed: {e}")
            return []
    
    async def _request_vote_from_node(self, node_id: str, 
                                     vote_request: VoteRequest) -> VoteResponse:
        """Request vote from specific node."""
        try:
            # Check if node has already voted in this term
            existing_vote = await self._get_vote(self.current_term, node_id)
            
            if existing_vote:
                # Node already voted
                return VoteResponse(
                    voter_id=node_id,
                    term=self.current_term,
                    vote_granted=False,
                    reason="already_voted"
                )
            
            # Validate vote request
            if not await self._validate_vote_request(vote_request):
                return VoteResponse(
                    voter_id=node_id,
                    term=self.current_term,
                    vote_granted=False,
                    reason="validation_failed"
                )
            
            # Grant vote
            await self._store_vote(self.current_term, node_id, vote_request.candidate_id)
            
            return VoteResponse(
                voter_id=node_id,
                term=self.current_term,
                vote_granted=True
            )
            
        except Exception as e:
            logger.error(f"Vote request from node failed: {e}")
            return VoteResponse(
                voter_id=node_id,
                term=self.current_term,
                vote_granted=False,
                reason=str(e)
            )
    
    async def _validate_vote_request(self, vote_request: VoteRequest) -> bool:
        """Validate vote request."""
        try:
            # Check term
            if vote_request.term < self.current_term:
                return False
            
            # Check if already voted
            if self.voted_for and self.voted_for != vote_request.candidate_id:
                return False
            
            # Check journal checkpoint
            if not await self._validate_journal_checkpoint(vote_request.journal_checkpoint):
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Vote request validation failed: {e}")
            return False
    
    async def _become_leader(self, fencing_token: FencingToken):
        """Become leader."""
        try:
            # Update state
            self.state = ElectionState.LEADER
            self.leader_id = self.coordinator_id
            
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
                    "term": self.current_term
                }
            )
            
            lease_result = await self.lease_manager.acquire_lease(lease_request)
            
            if not lease_result.success:
                logger.error("Failed to acquire leadership lease")
                self.state = ElectionState.FOLLOWER
                return
            
            # Store leadership claim
            await self._store_leadership_claim(self.coordinator_id, self.current_term, fencing_token.token_id)
            
            # Broadcast leadership
            await self._broadcast_leadership(fencing_token)
            
            logger.info(
                f"Became leader: coordinator_id={self.coordinator_id}, "
                f"term={self.current_term}"
            )
            
        except Exception as e:
            logger.error(f"Failed to become leader: {e}")
            self.state = ElectionState.FOLLOWER
    
    async def _step_down(self):
        """Step down from leadership."""
        try:
            # Release leadership lease
            await self.lease_manager.release_lease("execution_coordinator")
            
            # Remove leadership claim
            await self._remove_leadership_claim(self.coordinator_id)
            
            # Update state
            self.state = ElectionState.FOLLOWER
            self.leader_id = None
            
            logger.info(f"Stepped down from leadership: {self.coordinator_id}")
            
        except Exception as e:
            logger.error(f"Failed to step down: {e}")
    
    async def _generate_fencing_token(self, coordinator_id: str, term: int) -> FencingToken:
        """Generate fencing token."""
        try:
            token_id = str(uuid.uuid4())
            generated_at = datetime.now(timezone.utc)
            expires_at = generated_at + timedelta(hours=1)
            
            # Create token data
            token_data = f"{token_id}:{coordinator_id}:{term}:{generated_at.isoformat()}"
            
            # Generate signature
            signature = hmac.new(
                self.signing_key,
                token_data.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            # Generate hash
            token_hash = hashlib.sha256(token_data.encode('utf-8')).hexdigest()
            
            return FencingToken(
                token_id=token_id,
                coordinator_id=coordinator_id,
                term=term,
                generated_at=generated_at,
                expires_at=expires_at,
                signature=signature,
                token_hash=token_hash
            )
            
        except Exception as e:
            logger.error(f"Fencing token generation failed: {e}")
            raise
    
    async def validate_fencing_token(self, token: FencingToken) -> bool:
        """Validate fencing token."""
        try:
            # Check expiration
            if datetime.now(timezone.utc) > token.expires_at:
                return False
            
            # Verify signature
            token_data = f"{token.token_id}:{token.coordinator_id}:{token.term}:{token.generated_at.isoformat()}"
            expected_signature = hmac.new(
                self.signing_key,
                token_data.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            if token.signature != expected_signature:
                return False
            
            # Verify hash
            expected_hash = hashlib.sha256(token_data.encode('utf-8')).hexdigest()
            if token.token_hash != expected_hash:
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Fencing token validation failed: {e}")
            return False
    
    async def _send_leader_heartbeat(self):
        """Send leader heartbeat."""
        try:
            heartbeat = {
                "coordinator_id": self.coordinator_id,
                "term": self.current_term,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            heartbeat_key = "heartbeat:coordinator:leader"
            await self.redis.setex(
                heartbeat_key,
                10,  # 10 seconds TTL
                json.dumps(heartbeat)
            )
            
        except Exception as e:
            logger.error(f"Leader heartbeat failed: {e}")
    
    async def _get_last_leader_heartbeat(self) -> Optional[Dict[str, Any]]:
        """Get last leader heartbeat."""
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
    
    async def _store_vote(self, term: int, voter_id: str, candidate_id: str):
        """Store vote."""
        try:
            vote_key = f"leader:vote:{term}:{voter_id}"
            vote_data = {
                "voted_for": candidate_id,
                "voted_at": datetime.now(timezone.utc).isoformat()
            }
            await self.redis.setex(vote_key, 3600, json.dumps(vote_data))
            
        except Exception as e:
            logger.error(f"Failed to store vote: {e}")
    
    async def _get_vote(self, term: int, voter_id: str) -> Optional[str]:
        """Get vote."""
        try:
            vote_key = f"leader:vote:{term}:{voter_id}"
            vote_data = await self.redis.get(vote_key)
            
            if vote_data:
                vote = json.loads(vote_data)
                return vote["voted_for"]
            else:
                return None
                
        except Exception as e:
            logger.error(f"Failed to get vote: {e}")
            return None
    
    async def _store_leadership_claim(self, coordinator_id: str, term: int, fencing_token: str):
        """Store leadership claim."""
        try:
            claim_key = f"leader:claim:{coordinator_id}"
            claim_data = {
                "coordinator_id": coordinator_id,
                "term": term,
                "fencing_token": fencing_token,
                "claimed_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            }
            await self.redis.setex(claim_key, 3600, json.dumps(claim_data))
            
        except Exception as e:
            logger.error(f"Failed to store leadership claim: {e}")
    
    async def _remove_leadership_claim(self, coordinator_id: str):
        """Remove leadership claim."""
        try:
            claim_key = f"leader:claim:{coordinator_id}"
            await self.redis.delete(claim_key)
            
        except Exception as e:
            logger.error(f"Failed to remove leadership claim: {e}")
    
    async def _broadcast_leadership(self, fencing_token: FencingToken):
        """Broadcast leadership to all components."""
        try:
            leadership_message = {
                "coordinator_id": self.coordinator_id,
                "term": self.current_term,
                "fencing_token": fencing_token.token_id,
                "fencing_token_hash": fencing_token.token_hash,
                "became_leader_at": fencing_token.generated_at.isoformat(),
                "expires_at": fencing_token.expires_at.isoformat()
            }
            
            await self.redis.publish(
                "leadership_announcement",
                json.dumps(leadership_message)
            )
            
        except Exception as e:
            logger.error(f"Leadership broadcast failed: {e}")
    
    async def _get_current_term(self) -> int:
        """Get current term."""
        try:
            term_key = "leader:term:current"
            term = await self.redis.get(term_key)
            
            if term:
                return int(term)
            else:
                return 0
                
        except Exception as e:
            logger.error(f"Failed to get current term: {e}")
            return 0
    
    async def _set_current_term(self, term: int):
        """Set current term."""
        try:
            term_key = "leader:term:current"
            await self.redis.set(term_key, term)
            
        except Exception as e:
            logger.error(f"Failed to set current term: {e}")
    
    async def _get_coordinator_nodes(self) -> List[str]:
        """Get all coordinator nodes."""
        try:
            # For now, return a fixed list
            # In production, this would be dynamic
            return [
                "coordinator_1",
                "coordinator_2",
                "coordinator_3"
            ]
            
        except Exception as e:
            logger.error(f"Failed to get coordinator nodes: {e}")
            return []
    
    async def _get_priority(self) -> int:
        """Get coordinator priority."""
        try:
            # For now, return a fixed priority
            # In production, this would be configurable
            return 10
            
        except Exception as e:
            logger.error(f"Failed to get priority: {e}")
            return 10
    
    async def _get_last_log_index(self) -> int:
        """Get last log index."""
        try:
            # Get last sequence from journal
            events = await self.immutable_journal.get_events_by_tenant("system")
            
            if events:
                return events[-1].header.sequence_id
            else:
                return 0
                
        except Exception as e:
            logger.error(f"Failed to get last log index: {e}")
            return 0
    
    async def _get_journal_checkpoint(self) -> str:
        """Get journal checkpoint."""
        try:
            checkpoints = await self.immutable_journal.get_checkpoints("system")
            
            if checkpoints:
                return checkpoints[0]["checkpoint_id"]
            else:
                return ""
                
        except Exception as e:
            logger.error(f"Failed to get journal checkpoint: {e}")
            return ""
    
    async def _validate_journal_checkpoint(self, checkpoint_id: str) -> bool:
        """Validate journal checkpoint."""
        try:
            if not checkpoint_id:
                return False
            
            checkpoints = await self.immutable_journal.get_checkpoints("system")
            checkpoint_ids = [cp["checkpoint_id"] for cp in checkpoints]
            
            return checkpoint_id in checkpoint_ids
            
        except Exception as e:
            logger.error(f"Journal checkpoint validation failed: {e}")
            return False
    
    async def get_election_status(self) -> Dict[str, Any]:
        """Get current election status."""
        return {
            "coordinator_id": self.coordinator_id,
            "state": self.state.value,
            "current_term": self.current_term,
            "voted_for": self.voted_for,
            "leader_id": self.leader_id
        }


# Global instance
_leader_election_manager: Optional[LeaderElectionManager] = None


def get_leader_election_manager(coordinator_id: str = "default") -> LeaderElectionManager:
    """Get or create leader election manager instance."""
    global _leader_election_manager
    if _leader_election_manager is None:
        _leader_election_manager = LeaderElectionManager(coordinator_id)
    return _leader_election_manager
