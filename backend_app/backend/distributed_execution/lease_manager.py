"""
Lease Manager

This module implements the lease management system for Phase 3 distributed orchestration.
The lease manager provides distributed lease coordination, automatic renewal, conflict resolution,
and split-brain prevention while preserving deterministic guarantees.
"""

import asyncio
import json
import logging
import uuid
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum

from backend_app.core.cache.redis_manager import redis_manager
from backend_app.core.database_pool import get_db_session
from .orchestration_safety_guarantees import (
    SplitBrainPreventionGuarantee,
    AtomicTransactionGuarantee,
    DeterministicAssignmentGuarantee
)

logger = logging.getLogger(__name__)


class LeaseType(Enum):
    """Lease types for different resources."""
    EXCLUSIVE = "exclusive"
    SHARED = "shared"
    READ_ONLY = "read_only"
    COORDINATION = "coordination"


class LeaseStatus(Enum):
    """Lease operational status."""
    ACTIVE = "active"
    EXPIRED = "expired"
    RELEASED = "released"
    RENEWING = "renewing"
    TRANSFERRING = "transferring"
    CONFLICTED = "conflicted"


@dataclass
class LeaseRequest:
    """Lease acquisition request."""
    lease_id: Optional[str] = None
    resource_id: str
    requester_id: str
    lease_type: LeaseType = LeaseType.EXCLUSIVE
    ttl_seconds: int = 3600
    auto_renew: bool = True
    max_renewals: int = 10
    priority: int = 5
    metadata: Dict[str, Any] = field(default_factory=dict)
    requirements: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Lease:
    """Active lease information."""
    lease_id: str
    resource_id: str
    owner_id: str
    lease_type: LeaseType
    ttl_seconds: int
    auto_renew: bool
    max_renewals: int
    priority: int
    created_at: datetime
    acquired_at: datetime
    expires_at: datetime
    renewed_at: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    renewal_count: int = 0
    transfer_count: int = 0
    fencing_token: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: LeaseStatus = LeaseStatus.ACTIVE


@dataclass
class LeaseResult:
    """Lease operation result."""
    success: bool
    lease: Optional[Lease] = None
    fencing_token: Optional[str] = None
    reason: Optional[str] = None
    error: Optional[str] = None
    alternatives: Optional[List[str]] = None


@dataclass
class RenewalResult:
    """Lease renewal result."""
    success: bool
    new_expires_at: Optional[datetime] = None
    renewal_count: Optional[int] = None
    reason: Optional[str] = None
    error: Optional[str] = None


@dataclass
class TransferResult:
    """Lease transfer result."""
    success: bool
    new_lease_id: Optional[str] = None
    new_fencing_token: Optional[str] = None
    transferred_at: Optional[datetime] = None
    reason: Optional[str] = None
    error: Optional[str] = None


class LeaseManager:
    """
    Lease Manager
    
    Manages distributed lease coordination with automatic renewal,
    conflict resolution, and split-brain prevention.
    """
    
    def __init__(self):
        self.lease_key_prefix = "lease"
        self.resource_lease_key_prefix = "resource_lease"
        self.owner_lease_key_prefix = "owner_lease"
        self.lease_history_key_prefix = "lease_history"
        
        # Safety guarantees
        self.split_brain_guarantee = SplitBrainPreventionGuarantee()
        self.transaction_guarantee = AtomicTransactionGuarantee()
        self.deterministic_guarantee = DeterministicAssignmentGuarantee()
        
        # Lease state
        self.active_leases: Dict[str, Lease] = {}
        self.resource_leases: Dict[str, Set[str]] = {}
        self.owner_leases: Dict[str, Set[str]] = {}
        
        # Configuration
        self.default_ttl = 3600  # 1 hour
        self.max_ttl = 86400  # 24 hours
        self.renewal_threshold = 0.8  # Renew at 80% of TTL
        self.cleanup_interval = 300  # 5 minutes
        self.conflict_resolution_timeout = 30  # 30 seconds
        
        # Background tasks
        self.renewal_task: Optional[asyncio.Task] = None
        self.cleanup_task: Optional[asyncio.Task] = None
        self.running = False
    
    async def initialize(self) -> bool:
        """Initialize the lease manager."""
        try:
            logger.info("Initializing lease manager")
            
            # Load existing leases from storage
            await self._load_leases_from_storage()
            
            # Start background tasks
            self.running = True
            self.renewal_task = asyncio.create_task(self._renewal_loop())
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())
            
            logger.info("Lease manager initialized")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize lease manager: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop the lease manager."""
        try:
            logger.info("Stopping lease manager")
            
            self.running = False
            
            # Cancel background tasks
            if self.renewal_task and not self.renewal_task.done():
                self.renewal_task.cancel()
                try:
                    await self.renewal_task
                except asyncio.CancelledError:
                    pass
            
            if self.cleanup_task and not self.cleanup_task.done():
                self.cleanup_task.cancel()
                try:
                    await self.cleanup_task
                except asyncio.CancelledError:
                    pass
            
            # Release all active leases
            await self._release_all_leases()
            
            # Persist lease state
            await self._persist_lease_state()
            
            logger.info("Lease manager stopped")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop lease manager: {e}")
            return False
    
    async def acquire_lease(self, request: LeaseRequest) -> LeaseResult:
        """Acquire lease for resource."""
        try:
            # Validate request
            validation_result = await self._validate_lease_request(request)
            if not validation_result:
                return LeaseResult(
                    success=False,
                    reason="Invalid lease request"
                )
            
            # Generate lease ID if not provided
            lease_id = request.lease_id or self._generate_lease_id()
            
            # Check for existing lease
            existing_lease = await self._get_resource_lease(request.resource_id)
            
            if existing_lease:
                # Check if lease can be preempted
                if not await self._can_preempt_lease(existing_lease, request):
                    return LeaseResult(
                        success=False,
                        reason="Resource already leased",
                        alternatives=await self._get_alternative_resources(request)
                    )
                
                # Force release existing lease
                await self._force_release_lease(existing_lease.lease_id)
            
            # Create new lease
            lease = Lease(
                lease_id=lease_id,
                resource_id=request.resource_id,
                owner_id=request.requester_id,
                lease_type=request.lease_type,
                ttl_seconds=min(request.ttl_seconds, self.max_ttl),
                auto_renew=request.auto_renew,
                max_renewals=request.max_renewals,
                priority=request.priority,
                created_at=datetime.now(timezone.utc),
                acquired_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=min(request.ttl_seconds, self.max_ttl)),
                fencing_token=self._generate_fencing_token(request.requester_id, lease_id),
                metadata=request.metadata
            )
            
            # Atomic lease acquisition
            acquisition_success = await self._atomic_lease_acquisition(lease)
            
            if acquisition_success:
                # Update lease registry
                await self._register_lease(lease)
                
                # Start renewal if auto-renew enabled
                if lease.auto_renew:
                    asyncio.create_task(self._start_lease_renewal(lease))
                
                logger.info(f"Lease acquired: {lease_id} for resource {request.resource_id}")
                
                return LeaseResult(
                    success=True,
                    lease=lease,
                    fencing_token=lease.fencing_token
                )
            else:
                return LeaseResult(
                    success=False,
                    reason="Lease acquisition failed"
                )
                
        except Exception as e:
            logger.error(f"Lease acquisition failed: {e}")
            return LeaseResult(
                success=False,
                error=str(e)
            )
    
    async def release_lease(self, lease_id: str) -> bool:
        """Release lease."""
        try:
            lease = await self._get_lease(lease_id)
            if not lease:
                logger.warning(f"Lease not found for release: {lease_id}")
                return False
            
            # Update lease status
            lease.status = LeaseStatus.RELEASED
            lease.expires_at = datetime.now(timezone.utc)
            
            # Remove from registry
            await self._unregister_lease(lease)
            
            # Store in history
            await self._store_lease_history(lease)
            
            logger.info(f"Lease released: {lease_id}")
            return True
            
        except Exception as e:
            logger.error(f"Lease release failed: {e}")
            return False
    
    async def renew_lease(self, lease_id: str, extension_seconds: Optional[int] = None) -> RenewalResult:
        """Renew lease."""
        try:
            lease = await self._get_lease(lease_id)
            if not lease:
                return RenewalResult(
                    success=False,
                    reason="Lease not found"
                )
            
            # Check renewal limits
            if lease.renewal_count >= lease.max_renewals:
                return RenewalResult(
                    success=False,
                    reason="Maximum renewals exceeded"
                )
            
            # Calculate new expiration
            extension = extension_seconds or lease.ttl_seconds
            new_expires_at = datetime.now(timezone.utc) + timedelta(seconds=extension)
            
            # Validate renewal timing
            if not await self._validate_renewal_timing(lease):
                return RenewalResult(
                    success=False,
                    reason="Too early to renew"
                )
            
            # Atomic renewal
            renewal_success = await self._atomic_lease_renewal(lease_id, new_expires_at)
            
            if renewal_success:
                # Update lease
                lease.expires_at = new_expires_at
                lease.renewed_at = datetime.now(timezone.utc)
                lease.renewal_count += 1
                
                # Update registry
                await self._update_lease_in_registry(lease)
                
                logger.info(f"Lease renewed: {lease_id} until {new_expires_at}")
                
                return RenewalResult(
                    success=True,
                    new_expires_at=new_expires_at,
                    renewal_count=lease.renewal_count
                )
            else:
                return RenewalResult(
                    success=False,
                    reason="Renewal atomic operation failed"
                )
                
        except Exception as e:
            logger.error(f"Lease renewal failed: {e}")
            return RenewalResult(
                success=False,
                error=str(e)
            )
    
    async def transfer_lease(
        self,
        lease_id: str,
        from_owner_id: str,
        to_owner_id: str,
        transfer_reason: str = "manual"
    ) -> TransferResult:
        """Transfer lease ownership."""
        try:
            lease = await self._get_lease(lease_id)
            if not lease:
                return TransferResult(
                    success=False,
                    reason="Lease not found"
                )
            
            # Validate current owner
            if lease.owner_id != from_owner_id:
                return TransferResult(
                    success=False,
                    reason="Not lease owner"
                )
            
            # Create transfer context
            transfer_id = f"transfer_{lease_id}_{int(time.time())}"
            
            # Execute transfer
            transfer_success = await self._execute_lease_transfer(
                lease,
                to_owner_id,
                transfer_id,
                transfer_reason
            )
            
            if transfer_success:
                logger.info(f"Lease transferred: {lease_id} from {from_owner_id} to {to_owner_id}")
                
                return TransferResult(
                    success=True,
                    new_lease_id=lease.lease_id,
                    new_fencing_token=lease.fencing_token,
                    transferred_at=datetime.now(timezone.utc)
                )
            else:
                return TransferResult(
                    success=False,
                    reason="Transfer failed"
                )
                
        except Exception as e:
            logger.error(f"Lease transfer failed: {e}")
            return TransferResult(
                success=False,
                error=str(e)
            )
    
    async def get_lease(self, lease_id: str) -> Optional[Lease]:
        """Get lease information."""
        try:
            # Check local cache
            if lease_id in self.active_leases:
                return self.active_leases[lease_id]
            
            # Load from storage
            lease_data = await redis_manager.hgetall(f"{self.lease_key_prefix}:{lease_id}")
            if lease_data:
                lease = self._deserialize_lease(lease_data)
                if lease.status == LeaseStatus.ACTIVE:
                    self.active_leases[lease_id] = lease
                    return lease
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get lease {lease_id}: {e}")
            return None
    
    async def get_resource_lease(self, resource_id: str) -> Optional[Lease]:
        """Get lease for resource."""
        try:
            resource_key = f"{self.resource_lease_key_prefix}:{resource_id}"
            lease_id = await redis_manager.get(resource_key)
            
            if lease_id:
                return await self.get_lease(lease_id)
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get resource lease {resource_id}: {e}")
            return None
    
    async def get_leases_by_owner(self, owner_id: str) -> List[Lease]:
        """Get all leases owned by requester."""
        try:
            owner_key = f"{self.owner_lease_key_prefix}:{owner_id}"
            lease_ids = await redis_manager.smembers(owner_key)
            
            leases = []
            for lease_id in lease_ids:
                lease = await self.get_lease(lease_id)
                if lease:
                    leases.append(lease)
            
            return leases
            
        except Exception as e:
            logger.error(f"Failed to get leases by owner {owner_id}: {e}")
            return []
    
    async def get_all_active_leases(self) -> List[Lease]:
        """Get all active leases."""
        try:
            return list(self.active_leases.values())
            
        except Exception as e:
            logger.error(f"Failed to get all active leases: {e}")
            return []
    
    async def get_lease_statistics(self) -> Dict[str, Any]:
        """Get lease statistics."""
        try:
            all_leases = await self.get_all_active_leases()
            
            stats = {
                "total_active_leases": len(all_leases),
                "leases_by_type": {},
                "leases_by_status": {},
                "average_ttl": 0.0,
                "renewal_rate": 0.0,
                "expiring_soon": 0,
                "last_updated": datetime.now(timezone.utc).isoformat()
            }
            
            total_ttl = 0
            renewed_count = 0
            expiring_threshold = datetime.now(timezone.utc) + timedelta(minutes=30)
            
            for lease in all_leases:
                # Count by type
                lease_type = lease.lease_type.value
                stats["leases_by_type"][lease_type] = stats["leases_by_type"].get(lease_type, 0) + 1
                
                # Count by status
                status = lease.status.value
                stats["leases_by_status"][status] = stats["leases_by_status"].get(status, 0) + 1
                
                # TTL calculations
                total_ttl += lease.ttl_seconds
                if lease.renewal_count > 0:
                    renewed_count += 1
                
                # Check expiring soon
                if lease.expires_at < expiring_threshold:
                    stats["expiring_soon"] += 1
            
            if all_leases:
                stats["average_ttl"] = total_ttl / len(all_leases)
                stats["renewal_rate"] = renewed_count / len(all_leases)
            
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get lease statistics: {e}")
            return {"error": str(e)}
    
    # Private methods
    
    def _generate_lease_id(self) -> str:
        """Generate unique lease ID."""
        return f"lease_{uuid.uuid4().hex[:8]}_{int(time.time())}"
    
    def _generate_fencing_token(self, owner_id: str, lease_id: str) -> str:
        """Generate fencing token for lease."""
        token_data = f"{owner_id}:{lease_id}:{int(time.time() * 1000)}"
        token_hash = hashlib.sha256(token_data.encode()).hexdigest()[:16]
        return f"fence_{token_hash}"
    
    async def _validate_lease_request(self, request: LeaseRequest) -> bool:
        """Validate lease request."""
        try:
            # Check required fields
            if not request.resource_id or not request.requester_id:
                return False
            
            # Check TTL limits
            if request.ttl_seconds <= 0 or request.ttl_seconds > self.max_ttl:
                return False
            
            # Check priority range
            if not (1 <= request.priority <= 20):
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Lease request validation failed: {e}")
            return False
    
    async def _get_resource_lease(self, resource_id: str) -> Optional[Lease]:
        """Get current lease for resource."""
        try:
            resource_key = f"{self.resource_lease_key_prefix}:{resource_id}"
            lease_id = await redis_manager.get(resource_key)
            
            if lease_id:
                return await self.get_lease(lease_id)
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get resource lease: {e}")
            return None
    
    async def _can_preempt_lease(self, existing_lease: Lease, new_request: LeaseRequest) -> bool:
        """Check if lease can be preempted."""
        try:
            # Higher priority can preempt
            if new_request.priority > existing_lease.priority:
                return True
            
            # Same priority but newer can preempt (based on policy)
            if new_request.priority == existing_lease.priority:
                # This could be based on various policies
                return False
            
            return False
            
        except Exception as e:
            logger.error(f"Preemption check failed: {e}")
            return False
    
    async def _force_release_lease(self, lease_id: str):
        """Force release of existing lease."""
        try:
            lease = await self._get_lease(lease_id)
            if lease:
                lease.status = LeaseStatus.RELEASED
                await self._unregister_lease(lease)
                await self._store_lease_history(lease)
                
        except Exception as e:
            logger.error(f"Failed to force release lease {lease_id}: {e}")
    
    async def _atomic_lease_acquisition(self, lease: Lease) -> bool:
        """Atomically acquire lease using Redis."""
        try:
            # Use Redis pipeline for atomic operations
            pipe = redis_manager.pipeline()
            
            # Store lease
            lease_key = f"{self.lease_key_prefix}:{lease.lease_id}"
            lease_data = self._serialize_lease(lease)
            
            pipe.hset(lease_key, mapping=lease_data)
            pipe.expire(lease_key, lease.ttl_seconds + 60)  # Extra buffer
            
            # Set resource ownership
            resource_key = f"{self.resource_lease_key_prefix}:{lease.resource_id}"
            pipe.set(resource_key, lease.lease_id, ex=lease.ttl_seconds + 60)
            
            # Add to owner leases
            owner_key = f"{self.owner_lease_key_prefix}:{lease.owner_id}"
            pipe.sadd(owner_key, lease.lease_id)
            pipe.expire(owner_key, lease.ttl_seconds + 60)
            
            # Execute pipeline
            await pipe.execute()
            
            return True
            
        except Exception as e:
            logger.error(f"Atomic lease acquisition failed: {e}")
            return False
    
    async def _register_lease(self, lease: Lease):
        """Register lease in local state."""
        try:
            self.active_leases[lease.lease_id] = lease
            
            if lease.resource_id not in self.resource_leases:
                self.resource_leases[lease.resource_id] = set()
            self.resource_leases[lease.resource_id].add(lease.lease_id)
            
            if lease.owner_id not in self.owner_leases:
                self.owner_leases[lease.owner_id] = set()
            self.owner_leases[lease.owner_id].add(lease.lease_id)
            
        except Exception as e:
            logger.error(f"Failed to register lease: {e}")
    
    async def _unregister_lease(self, lease: Lease):
        """Unregister lease from local state."""
        try:
            self.active_leases.pop(lease.lease_id, None)
            
            if lease.resource_id in self.resource_leases:
                self.resource_leases[lease.resource_id].discard(lease.lease_id)
                if not self.resource_leases[lease.resource_id]:
                    del self.resource_leases[lease.resource_id]
            
            if lease.owner_id in self.owner_leases:
                self.owner_leases[lease.owner_id].discard(lease.lease_id)
                if not self.owner_leases[lease.owner_id]:
                    del self.owner_leases[lease.owner_id]
            
            # Remove from Redis
            lease_key = f"{self.lease_key_prefix}:{lease.lease_id}"
            await redis_manager.delete(lease_key)
            
            resource_key = f"{self.resource_lease_key_prefix}:{lease.resource_id}"
            await redis_manager.delete(resource_key)
            
            owner_key = f"{self.owner_lease_key_prefix}:{lease.owner_id}"
            await redis_manager.srem(owner_key, lease.lease_id)
            
        except Exception as e:
            logger.error(f"Failed to unregister lease: {e}")
    
    async def _validate_renewal_timing(self, lease: Lease) -> bool:
        """Validate if it's time to renew lease."""
        try:
            current_time = datetime.now(timezone.utc)
            time_to_expiry = (lease.expires_at - current_time).total_seconds()
            renewal_threshold = lease.ttl_seconds * self.renewal_threshold
            
            return time_to_expiry <= renewal_threshold
            
        except Exception as e:
            logger.error(f"Renewal timing validation failed: {e}")
            return False
    
    async def _atomic_lease_renewal(self, lease_id: str, new_expires_at: datetime) -> bool:
        """Atomically renew lease."""
        try:
            lease_key = f"{self.lease_key_prefix}:{lease_id}"
            
            # Update expiration in Redis
            await redis_manager.hset(lease_key, "expires_at", new_expires_at.isoformat())
            await redis_manager.expire(lease_key, int((new_expires_at - datetime.now(timezone.utc)).total_seconds()) + 60)
            
            return True
            
        except Exception as e:
            logger.error(f"Atomic lease renewal failed: {e}")
            return False
    
    async def _update_lease_in_registry(self, lease: Lease):
        """Update lease in registry."""
        try:
            lease_key = f"{self.lease_key_prefix}:{lease.lease_id}"
            lease_data = self._serialize_lease(lease)
            
            await redis_manager.hset(lease_key, mapping=lease_data)
            
            # Update local cache
            self.active_leases[lease.lease_id] = lease
            
        except Exception as e:
            logger.error(f"Failed to update lease in registry: {e}")
    
    async def _execute_lease_transfer(
        self,
        lease: Lease,
        to_owner_id: str,
        transfer_id: str,
        transfer_reason: str
    ) -> bool:
        """Execute lease transfer."""
        try:
            # Phase 1: Prepare transfer
            await self._prepare_transfer(transfer_id, lease, to_owner_id)
            
            # Phase 2: Update lease ownership
            old_owner_id = lease.owner_id
            lease.owner_id = to_owner_id
            lease.transfer_count += 1
            lease.last_activity = datetime.now(timezone.utc)
            
            # Update metadata
            lease.metadata.update({
                "transferred_from": old_owner_id,
                "transfer_reason": transfer_reason,
                "transfer_id": transfer_id,
                "transferred_at": datetime.now(timezone.utc).isoformat()
            })
            
            # Phase 3: Update registry
            await self._unregister_lease(lease)  # Remove old associations
            await self._register_lease(lease)  # Add new associations
            
            return True
            
        except Exception as e:
            logger.error(f"Lease transfer execution failed: {e}")
            return False
    
    async def _prepare_transfer(self, transfer_id: str, lease: Lease, to_owner_id: str):
        """Prepare lease transfer."""
        try:
            # Store transfer preparation
            transfer_data = {
                "transfer_id": transfer_id,
                "lease_id": lease.lease_id,
                "from_owner": lease.owner_id,
                "to_owner": to_owner_id,
                "prepared_at": datetime.now(timezone.utc).isoformat()
            }
            
            await redis_manager.setex(
                f"transfer_prep:{transfer_id}",
                300,  # 5 minutes
                json.dumps(transfer_data)
            )
            
        except Exception as e:
            logger.error(f"Transfer preparation failed: {e}")
    
    async def _start_lease_renewal(self, lease: Lease):
        """Start automatic lease renewal."""
        try:
            while lease.auto_renew and lease.renewal_count < lease.max_renewals:
                # Wait until renewal time
                current_time = datetime.now(timezone.utc)
                time_to_renewal = (lease.expires_at - current_time).total_seconds()
                renewal_time = time_to_renewal * self.renewal_threshold
                
                if renewal_time > 0:
                    await asyncio.sleep(renewal_time)
                
                # Check if lease is still active
                current_lease = await self.get_lease(lease.lease_id)
                if not current_lease or current_lease.status != LeaseStatus.ACTIVE:
                    break
                
                # Renew lease
                renewal_result = await self.renew_lease(lease.lease_id)
                if not renewal_result.success:
                    logger.warning(f"Auto-renewal failed for lease {lease.lease_id}")
                    break
                
                # Update lease reference
                lease = await self.get_lease(lease.lease_id)
                if not lease:
                    break
            
        except Exception as e:
            logger.error(f"Lease renewal loop failed: {e}")
    
    async def _get_alternative_resources(self, request: LeaseRequest) -> List[str]:
        """Get alternative resources for failed lease acquisition."""
        try:
            # This would implement logic to find similar resources
            # For now, return empty list
            return []
            
        except Exception as e:
            logger.error(f"Failed to get alternative resources: {e}")
            return []
    
    def _serialize_lease(self, lease: Lease) -> Dict[str, str]:
        """Serialize lease to dictionary."""
        return {
            "lease_id": lease.lease_id,
            "resource_id": lease.resource_id,
            "owner_id": lease.owner_id,
            "lease_type": lease.lease_type.value,
            "ttl_seconds": str(lease.ttl_seconds),
            "auto_renew": str(lease.auto_renew),
            "max_renewals": str(lease.max_renewals),
            "priority": str(lease.priority),
            "created_at": lease.created_at.isoformat(),
            "acquired_at": lease.acquired_at.isoformat(),
            "expires_at": lease.expires_at.isoformat(),
            "renewed_at": lease.renewed_at.isoformat() if lease.renewed_at else "",
            "last_activity": lease.last_activity.isoformat() if lease.last_activity else "",
            "renewal_count": str(lease.renewal_count),
            "transfer_count": str(lease.transfer_count),
            "fencing_token": lease.fencing_token,
            "status": lease.status.value,
            "metadata": json.dumps(lease.metadata)
        }
    
    def _deserialize_lease(self, data: Dict[str, str]) -> Lease:
        """Deserialize lease from dictionary."""
        return Lease(
            lease_id=data["lease_id"],
            resource_id=data["resource_id"],
            owner_id=data["owner_id"],
            lease_type=LeaseType(data["lease_type"]),
            ttl_seconds=int(data["ttl_seconds"]),
            auto_renew=data["auto_renew"] == "True",
            max_renewals=int(data["max_renewals"]),
            priority=int(data["priority"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            acquired_at=datetime.fromisoformat(data["acquired_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            renewed_at=datetime.fromisoformat(data["renewed_at"]) if data["renewed_at"] else None,
            last_activity=datetime.fromisoformat(data["last_activity"]) if data["last_activity"] else None,
            renewal_count=int(data["renewal_count"]),
            transfer_count=int(data["transfer_count"]),
            fencing_token=data["fencing_token"],
            status=LeaseStatus(data["status"]),
            metadata=json.loads(data["metadata"])
        )
    
    async def _load_leases_from_storage(self):
        """Load existing leases from Redis."""
        try:
            # Get all lease keys
            lease_keys = await redis_manager.keys(f"{self.lease_key_prefix}:*")
            
            for lease_key in lease_keys:
                lease_data = await redis_manager.hgetall(lease_key)
                if lease_data:
                    lease = self._deserialize_lease(lease_data)
                    if lease.status == LeaseStatus.ACTIVE:
                        self.active_leases[lease.lease_id] = lease
                        
                        # Update indexes
                        if lease.resource_id not in self.resource_leases:
                            self.resource_leases[lease.resource_id] = set()
                        self.resource_leases[lease.resource_id].add(lease.lease_id)
                        
                        if lease.owner_id not in self.owner_leases:
                            self.owner_leases[lease.owner_id] = set()
                        self.owner_leases[lease.owner_id].add(lease.lease_id)
            
            logger.info(f"Loaded {len(self.active_leases)} active leases from storage")
            
        except Exception as e:
            logger.error(f"Failed to load leases from storage: {e}")
    
    async def _store_lease_history(self, lease: Lease):
        """Store lease in history."""
        try:
            history_key = f"{self.lease_history_key_prefix}:{lease.lease_id}"
            history_data = self._serialize_lease(lease)
            
            await redis_manager.setex(
                history_key,
                86400 * 7,  # 7 days
                json.dumps(history_data)
            )
            
        except Exception as e:
            logger.error(f"Failed to store lease history: {e}")
    
    async def _release_all_leases(self):
        """Release all active leases."""
        try:
            lease_ids = list(self.active_leases.keys())
            
            for lease_id in lease_ids:
                await self.release_lease(lease_id)
            
            logger.info(f"Released {len(lease_ids)} leases")
            
        except Exception as e:
            logger.error(f"Failed to release all leases: {e}")
    
    async def _persist_lease_state(self):
        """Persist lease state."""
        try:
            # Leases are already persisted individually
            # This could be extended to persist registry metadata
            registry_metadata = {
                "last_persisted": datetime.now(timezone.utc).isoformat(),
                "active_leases": len(self.active_leases)
            }
            
            await redis_manager.set("lease_manager_metadata", json.dumps(registry_metadata))
            
        except Exception as e:
            logger.error(f"Failed to persist lease state: {e}")
    
    async def _renewal_loop(self):
        """Background renewal loop."""
        while self.running:
            try:
                await self._check_lease_renewals()
                await asyncio.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.error(f"Renewal loop error: {e}")
                await asyncio.sleep(10)
    
    async def _cleanup_loop(self):
        """Background cleanup loop."""
        while self.running:
            try:
                await self._perform_cleanup()
                await asyncio.sleep(self.cleanup_interval)
                
            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")
                await asyncio.sleep(10)
    
    async def _check_lease_renewals(self):
        """Check and renew leases that need renewal."""
        try:
            current_time = datetime.now(timezone.utc)
            leases_to_check = list(self.active_leases.values())
            
            for lease in leases_to_check:
                if not lease.auto_renew:
                    continue
                
                if lease.renewal_count >= lease.max_renewals:
                    continue
                
                # Check if renewal is needed
                time_to_expiry = (lease.expires_at - current_time).total_seconds()
                renewal_threshold = lease.ttl_seconds * self.renewal_threshold
                
                if time_to_expiry <= renewal_threshold:
                    await self.renew_lease(lease.lease_id)
            
        except Exception as e:
            logger.error(f"Failed to check lease renewals: {e}")
    
    async def _perform_cleanup(self):
        """Perform cleanup operations."""
        try:
            current_time = datetime.now(timezone.utc)
            expired_leases = []
            
            for lease_id, lease in self.active_leases.items():
                if lease.expires_at < current_time:
                    expired_leases.append(lease_id)
            
            for lease_id in expired_leases:
                lease = self.active_leases[lease_id]
                lease.status = LeaseStatus.EXPIRED
                await self._unregister_lease(lease)
                await self._store_lease_history(lease)
            
            if expired_leases:
                logger.info(f"Cleaned up {len(expired_leases)} expired leases")
            
        except Exception as e:
            logger.error(f"Failed to perform cleanup: {e}")


# Global lease manager instance
lease_manager = LeaseManager()
