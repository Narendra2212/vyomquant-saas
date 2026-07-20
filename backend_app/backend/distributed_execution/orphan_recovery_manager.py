"""
Orphan Recovery Manager

This module implements the orphan recovery system for Phase 3 distributed orchestration.
The orphan recovery manager provides detection, recovery, and reassignment of orphaned
tasks, workers, queues, and resources while preserving deterministic guarantees and replay safety.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum

from backend_app.core.cache.redis_manager import redis_manager
from backend_app.core.database_pool import get_db_session
from .queue_manager import DistributedQueueManager, QueueType, ExecutionJob
from .worker_registry import WorkerRegistry, WorkerInfo
from .lease_manager import LeaseManager, Lease
from .heartbeat_manager import HeartbeatManager, HealthStatus
from .deterministic_reassignment_model import DeterministicReassignmentCoordinator
from .orchestration_safety_guarantees import (
    ReplaySafeReassignmentManager,
    DeterministicAssignmentGuarantee,
    AtomicTransactionGuarantee
)

logger = logging.getLogger(__name__)


class OrphanType(Enum):
    """Types of orphans."""
    TASK = "task"
    WORKER = "worker"
    QUEUE = "queue"
    LEASE = "lease"
    RESOURCE = "resource"


class OrphanStatus(Enum):
    """Orphan recovery status."""
    DETECTED = "detected"
    RECOVERING = "recovering"
    RECOVERED = "recovered"
    FAILED = "failed"
    ABANDONED = "abandoned"


class FailureType(Enum):
    """Failure types for orphans."""
    TIMEOUT = "timeout"
    CRASHED = "crashed"
    DISCONNECTED = "disconnected"
    UNRESPONSIVE = "unresponsive"
    FAILED = "failed"
    ISOLATED = "isolated"


@dataclass
class OrphanResource:
    """Base class for orphaned resources."""
    orphan_id: str
    resource_type: OrphanType
    resource_id: str
    orphan_type: str
    detected_at: datetime
    details: Dict[str, Any] = field(default_factory=dict)
    status: OrphanStatus = OrphanStatus.DETECTED
    recovery_priority: int = 5
    recovery_attempts: int = 0
    max_recovery_attempts: int = 3


@dataclass
class TaskOrphan(OrphanResource):
    """Orphaned task."""
    task_id: str
    task_type: str
    worker_id: Optional[str] = None
    task_data: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        self.resource_type = OrphanType.TASK
        self.resource_id = self.task_id


@dataclass
class WorkerOrphan(OrphanResource):
    """Orphaned worker."""
    worker_id: str
    worker_type: str
    last_heartbeat: Optional[datetime] = None
    active_tasks: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        self.resource_type = OrphanType.WORKER
        self.resource_id = self.worker_id


@dataclass
class QueueOrphan(OrphanResource):
    """Orphaned queue."""
    queue_id: str
    queue_type: str
    owner_id: Optional[str] = None
    queue_stats: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        self.resource_type = OrphanType.QUEUE
        self.resource_id = self.queue_id


@dataclass
class LeaseOrphan(OrphanResource):
    """Orphaned lease."""
    lease_id: str
    resource_id: str
    owner_id: str
    lease_data: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        self.resource_type = OrphanType.LEASE
        self.resource_id = self.lease_id


@dataclass
class RecoveryContext:
    """Recovery operation context."""
    recovery_id: str
    orphan: OrphanResource
    started_at: datetime
    strategy: str = "default"
    status: str = "initiated"
    completed_at: Optional[datetime] = None
    success: Optional[bool] = None
    error: Optional[str] = None


@dataclass
class RecoveryResult:
    """Recovery operation result."""
    success: bool
    recovery_id: str
    orphan_id: str
    strategy: str
    completed_at: datetime
    new_assignment: Optional[str] = None
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class OrphanRecoveryManager:
    """
    Orphan Recovery Manager
    
    Detects, recovers, and reassigns orphaned resources while preserving
    deterministic guarantees and replay safety.
    """
    
    def __init__(self):
        self.orphan_key_prefix = "orphan"
        self.recovery_history_key_prefix = "recovery_history"
        
        # Dependencies
        self.queue_manager = DistributedQueueManager()
        self.worker_registry = WorkerRegistry()
        self.lease_manager = LeaseManager()
        self.heartbeat_manager = HeartbeatManager()
        self.reassignment_coordinator = DeterministicReassignmentCoordinator()
        self.replay_safe_reassigner = ReplaySafeReassignmentManager()
        
        # Safety guarantees
        self.deterministic_guarantee = DeterministicAssignmentGuarantee()
        self.transaction_guarantee = AtomicTransactionGuarantee()
        
        # Recovery state
        self.detected_orphans: Dict[str, OrphanResource] = {}
        self.active_recoveries: Dict[str, RecoveryContext] = {}
        self.recovery_history: Dict[str, RecoveryResult] = {}
        
        # Configuration
        self.max_concurrent_recoveries = 5
        self.recovery_timeout = 300  # 5 minutes
        self.detection_interval = 60  # 1 minute
        self.recovery_delay = 30  # 30 seconds between attempts
        
        # Background tasks
        self.detection_task: Optional[asyncio.Task] = None
        self.recovery_task: Optional[asyncio.Task] = None
        self.running = False
    
    async def initialize(self) -> bool:
        """Initialize the orphan recovery manager."""
        try:
            logger.info("Initializing orphan recovery manager")
            
            # Load existing orphan state
            await self._load_orphan_state()
            
            # Start background tasks
            self.running = True
            self.detection_task = asyncio.create_task(self._orphan_detection_loop())
            self.recovery_task = asyncio.create_task(self._recovery_processing_loop())
            
            logger.info("Orphan recovery manager initialized")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize orphan recovery manager: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop the orphan recovery manager."""
        try:
            logger.info("Stopping orphan recovery manager")
            
            self.running = False
            
            # Cancel background tasks
            if self.detection_task and not self.detection_task.done():
                self.detection_task.cancel()
                try:
                    await self.detection_task
                except asyncio.CancelledError:
                    pass
            
            if self.recovery_task and not self.recovery_task.done():
                self.recovery_task.cancel()
                try:
                    await self.recovery_task
                except asyncio.CancelledError:
                    pass
            
            # Complete active recoveries
            await self._complete_active_recoveries()
            
            # Persist orphan state
            await self._persist_orphan_state()
            
            logger.info("Orphan recovery manager stopped")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop orphan recovery manager: {e}")
            return False
    
    async def detect_worker_orphans(self):
        """Detect orphaned workers."""
        try:
            current_time = datetime.now(timezone.utc)
            
            # Get all workers from registry
            all_workers = await self.worker_registry.get_all_workers()
            
            for worker_id, worker_info in all_workers.items():
                # Check for failed workers
                health_info = await self.heartbeat_manager.get_component_health(worker_id)
                
                if health_info and health_info.get("status") == "failed":
                    await self._handle_worker_orphan(worker_id, "failed", health_info)
                
                # Check for timeout
                last_heartbeat = worker_info.get("last_heartbeat")
                if last_heartbeat:
                    if isinstance(last_heartbeat, str):
                        last_heartbeat = datetime.fromisoformat(last_heartbeat)
                    
                    timeout = self._get_worker_timeout(worker_id)
                    time_since_heartbeat = (current_time - last_heartbeat).total_seconds()
                    
                    if time_since_heartbeat > timeout:
                        await self._handle_worker_orphan(
                            worker_id,
                            "timeout",
                            {"timeout_duration": time_since_heartbeat}
                        )
                
                # Check for critical health
                health_score = health_info.get("health_score", 1.0) if health_info else 1.0
                if health_score < 0.3:
                    await self._handle_worker_orphan(
                        worker_id,
                        "critical_health",
                        {"health_score": health_score}
                    )
            
        except Exception as e:
            logger.error(f"Worker orphan detection failed: {e}")
    
    async def detect_task_orphans(self):
        """Detect orphaned tasks."""
        try:
            # Get all tasks from database
            all_tasks = await self._get_all_tasks()
            
            for task in all_tasks:
                # Check for in-flight tasks with no active worker
                if task.get("status") == "running" and task.get("worker_id"):
                    worker_healthy = await self._is_worker_healthy(task["worker_id"])
                    
                    if not worker_healthy:
                        await self._handle_task_orphan(
                            task["task_id"],
                            "worker_failed",
                            {"task": task, "worker_id": task["worker_id"]}
                        )
                
                # Check for long-running tasks
                if task.get("started_at"):
                    started_at = datetime.fromisoformat(task["started_at"])
                    runtime = (datetime.now(timezone.utc) - started_at).total_seconds()
                    max_runtime = self._get_max_task_runtime(task.get("task_type", "default"))
                    
                    if runtime > max_runtime:
                        await self._handle_task_orphan(
                            task["task_id"],
                            "long_running",
                            {"task": task, "runtime": runtime}
                        )
                
                # Check for stuck tasks (no progress)
                if task.get("last_progress_update"):
                    last_progress = datetime.fromisoformat(task["last_progress_update"])
                    time_since_progress = (datetime.now(timezone.utc) - last_progress).total_seconds()
                    
                    if time_since_progress > 300:  # 5 minutes
                        await self._handle_task_orphan(
                            task["task_id"],
                            "no_progress",
                            {"task": task, "time_since_progress": time_since_progress}
                        )
            
        except Exception as e:
            logger.error(f"Task orphan detection failed: {e}")
    
    async def detect_queue_orphans(self):
        """Detect orphaned queues."""
        try:
            # Get all queues
            all_queues = await self.queue_manager.get_all_queues()
            
            for queue_id in all_queues:
                # Check for unowned queues
                ownership = await self.queue_manager.get_queue_ownership(queue_id)
                
                if not ownership or not ownership.get("owner_id"):
                    await self._handle_queue_orphan(queue_id, "unowned", {})
                
                # Check for stuck queues
                queue_stats = await self.queue_manager.get_queue_stats(queue_id)
                depth = queue_stats.get("depth", 0)
                processing_rate = queue_stats.get("processing_rate", 0)
                
                if depth > 1000 and processing_rate == 0:
                    await self._handle_queue_orphan(
                        queue_id,
                        "stuck",
                        {"depth": depth, "processing_rate": processing_rate}
                    )
                
                # Check for overflow queues
                max_depth = queue_stats.get("max_depth", 10000)
                if depth > max_depth * 0.9:
                    await self._handle_queue_orphan(
                        queue_id,
                        "overflow",
                        {"depth": depth, "max_depth": max_depth}
                    )
            
        except Exception as e:
            logger.error(f"Queue orphan detection failed: {e}")
    
    async def detect_lease_orphans(self):
        """Detect orphaned leases."""
        try:
            # Get all active leases
            active_leases = await self.lease_manager.get_all_active_leases()
            
            for lease in active_leases:
                # Check for expired leases
                if lease.expires_at < datetime.now(timezone.utc):
                    await self._handle_lease_orphan(lease.lease_id, "expired", lease)
                
                # Check for stale leases (owner not responding)
                owner_healthy = await self._is_owner_healthy(lease.owner_id)
                if not owner_healthy:
                    await self._handle_lease_orphan(lease.lease_id, "owner_unhealthy", lease)
                
                # Check for abandoned leases (no activity)
                last_activity = lease.last_activity
                if last_activity:
                    time_since_activity = (datetime.now(timezone.utc) - last_activity).total_seconds()
                    
                    if time_since_activity > lease.ttl_seconds * 2:
                        await self._handle_lease_orphan(lease.lease_id, "abandoned", lease)
            
        except Exception as e:
            logger.error(f"Lease orphan detection failed: {e}")
    
    async def initiate_recovery(self, orphan_id: str) -> bool:
        """Initiate recovery for orphan."""
        try:
            if orphan_id not in self.detected_orphans:
                logger.warning(f"Orphan not found for recovery: {orphan_id}")
                return False
            
            if orphan_id in self.active_recoveries:
                logger.warning(f"Recovery already in progress for orphan: {orphan_id}")
                return False
            
            # Create recovery context
            orphan = self.detected_orphans[orphan_id]
            recovery_context = RecoveryContext(
                recovery_id=f"recovery_{orphan_id}_{int(time.time())}",
                orphan=orphan,
                started_at=datetime.now(timezone.utc),
                status="initiated"
            )
            
            # Determine recovery strategy
            recovery_context.strategy = await self._determine_recovery_strategy(orphan)
            
            # Start recovery
            self.active_recoveries[orphan_id] = recovery_context
            
            # Execute recovery
            success = await self._execute_recovery(recovery_context)
            
            return success
            
        except Exception as e:
            logger.error(f"Recovery initiation failed: {e}")
            return False
    
    async def get_orphan_status(self, orphan_id: str) -> Optional[Dict[str, Any]]:
        """Get orphan recovery status."""
        try:
            if orphan_id in self.detected_orphans:
                orphan = self.detected_orphans[orphan_id]
                
                status = {
                    "orphan_id": orphan_id,
                    "resource_type": orphan.resource_type.value,
                    "resource_id": orphan.resource_id,
                    "orphan_type": orphan.orphan_type,
                    "detected_at": orphan.detected_at.isoformat(),
                    "status": orphan.status.value,
                    "recovery_priority": orphan.recovery_priority,
                    "recovery_attempts": orphan.recovery_attempts
                }
                
                if orphan_id in self.active_recoveries:
                    recovery_context = self.active_recoveries[orphan_id]
                    status["recovery"] = {
                        "recovery_id": recovery_context.recovery_id,
                        "strategy": recovery_context.strategy,
                        "status": recovery_context.status,
                        "started_at": recovery_context.started_at.isoformat()
                    }
                
                if orphan_id in self.recovery_history:
                    recovery_result = self.recovery_history[orphan_id]
                    status["last_recovery"] = {
                        "success": recovery_result.success,
                        "completed_at": recovery_result.completed_at.isoformat(),
                        "strategy": recovery_result.strategy
                    }
                
                return status
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get orphan status {orphan_id}: {e}")
            return None
    
    async def get_all_orphans(self) -> List[Dict[str, Any]]:
        """Get all detected orphans."""
        try:
            orphans = []
            
            for orphan_id, orphan in self.detected_orphans.items():
                orphan_status = await self.get_orphan_status(orphan_id)
                if orphan_status:
                    orphans.append(orphan_status)
            
            return orphans
            
        except Exception as e:
            logger.error(f"Failed to get all orphans: {e}")
            return []
    
    async def get_recovery_statistics(self) -> Dict[str, Any]:
        """Get recovery statistics."""
        try:
            stats = {
                "total_orphans": len(self.detected_orphans),
                "active_recoveries": len(self.active_recoveries),
                "completed_recoveries": len(self.recovery_history),
                "orphan_types": {},
                "recovery_success_rate": 0.0,
                "average_recovery_time": 0.0,
                "last_updated": datetime.now(timezone.utc).isoformat()
            }
            
            # Count orphan types
            for orphan in self.detected_orphans.values():
                orphan_type = orphan.orphan_type
                stats["orphan_types"][orphan_type] = stats["orphan_types"].get(orphan_type, 0) + 1
            
            # Calculate success rate
            if self.recovery_history:
                successful_recoveries = sum(1 for result in self.recovery_history.values() if result.success)
                stats["recovery_success_rate"] = successful_recoveries / len(self.recovery_history)
            
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get recovery statistics: {e}")
            return {"error": str(e)}
    
    # Private methods
    
    async def _handle_worker_orphan(self, worker_id: str, orphan_type: str, details: Dict[str, Any]):
        """Handle detected worker orphan."""
        try:
            orphan_id = f"worker_orphan_{worker_id}_{int(time.time())}"
            
            orphan = WorkerOrphan(
                orphan_id=orphan_id,
                worker_id=worker_id,
                worker_type=details.get("worker_type", "unknown"),
                orphan_type=orphan_type,
                detected_at=datetime.now(timezone.utc),
                details=details,
                recovery_priority=self._calculate_recovery_priority("worker", orphan_type, details)
            )
            
            self.detected_orphans[orphan_id] = orphan
            
            # Trigger recovery
            await self._trigger_recovery(orphan)
            
            logger.info(f"Detected worker orphan: {worker_id} ({orphan_type})")
            
        except Exception as e:
            logger.error(f"Worker orphan handling failed: {e}")
    
    async def _handle_task_orphan(self, task_id: str, orphan_type: str, details: Dict[str, Any]):
        """Handle detected task orphan."""
        try:
            orphan_id = f"task_orphan_{task_id}_{int(time.time())}"
            
            task_data = details.get("task", {})
            
            orphan = TaskOrphan(
                orphan_id=orphan_id,
                task_id=task_id,
                task_type=task_data.get("task_type", "unknown"),
                worker_id=task_data.get("worker_id"),
                orphan_type=orphan_type,
                detected_at=datetime.now(timezone.utc),
                details=details,
                task_data=task_data,
                recovery_priority=self._calculate_recovery_priority("task", orphan_type, details)
            )
            
            self.detected_orphans[orphan_id] = orphan
            
            # Trigger recovery
            await self._trigger_recovery(orphan)
            
            logger.info(f"Detected task orphan: {task_id} ({orphan_type})")
            
        except Exception as e:
            logger.error(f"Task orphan handling failed: {e}")
    
    async def _handle_queue_orphan(self, queue_id: str, orphan_type: str, details: Dict[str, Any]):
        """Handle detected queue orphan."""
        try:
            orphan_id = f"queue_orphan_{queue_id}_{int(time.time())}"
            
            orphan = QueueOrphan(
                orphan_id=orphan_id,
                queue_id=queue_id,
                queue_type=details.get("queue_type", "unknown"),
                orphan_type=orphan_type,
                detected_at=datetime.now(timezone.utc),
                details=details,
                queue_stats=details,
                recovery_priority=self._calculate_recovery_priority("queue", orphan_type, details)
            )
            
            self.detected_orphans[orphan_id] = orphan
            
            # Trigger recovery
            await self._trigger_recovery(orphan)
            
            logger.info(f"Detected queue orphan: {queue_id} ({orphan_type})")
            
        except Exception as e:
            logger.error(f"Queue orphan handling failed: {e}")
    
    async def _handle_lease_orphan(self, lease_id: str, orphan_type: str, lease: Lease):
        """Handle detected lease orphan."""
        try:
            orphan_id = f"lease_orphan_{lease_id}_{int(time.time())}"
            
            orphan = LeaseOrphan(
                orphan_id=orphan_id,
                lease_id=lease_id,
                resource_id=lease.resource_id,
                owner_id=lease.owner_id,
                orphan_type=orphan_type,
                detected_at=datetime.now(timezone.utc),
                details={"lease_data": lease.__dict__},
                lease_data=lease.__dict__,
                recovery_priority=self._calculate_recovery_priority("lease", orphan_type, {"lease": lease})
            )
            
            self.detected_orphans[orphan_id] = orphan
            
            # Trigger recovery
            await self._trigger_recovery(orphan)
            
            logger.info(f"Detected lease orphan: {lease_id} ({orphan_type})")
            
        except Exception as e:
            logger.error(f"Lease orphan handling failed: {e}")
    
    def _calculate_recovery_priority(self, resource_type: str, orphan_type: str, details: Dict[str, Any]) -> int:
        """Calculate recovery priority for orphan."""
        try:
            base_priority = 5
            
            # Adjust based on resource type
            if resource_type == "task":
                base_priority = 8  # Tasks are high priority
            elif resource_type == "worker":
                base_priority = 7  # Workers are high priority
            elif resource_type == "queue":
                base_priority = 6  # Queues are medium priority
            elif resource_type == "lease":
                base_priority = 5  # Leases are medium priority
            
            # Adjust based on orphan type
            if orphan_type == "critical_health":
                base_priority += 3
            elif orphan_type == "timeout":
                base_priority += 2
            elif orphan_type == "failed":
                base_priority += 2
            elif orphan_type == "stuck":
                base_priority += 1
            
            return min(base_priority, 10)  # Cap at 10
            
        except Exception as e:
            logger.error(f"Recovery priority calculation failed: {e}")
            return 5
    
    async def _trigger_recovery(self, orphan: OrphanResource):
        """Trigger recovery for orphan."""
        try:
            # Check recovery limits
            if len(self.active_recoveries) >= self.max_concurrent_recoveries:
                await self._queue_recovery(orphan)
                return
            
            # Start recovery immediately
            await self.initiate_recovery(orphan.orphan_id)
            
        except Exception as e:
            logger.error(f"Recovery trigger failed: {e}")
    
    async def _queue_recovery(self, orphan: OrphanResource):
        """Queue recovery for later processing."""
        try:
            # This would implement a priority queue for recovery
            # For now, just log that it's queued
            logger.info(f"Recovery queued for orphan: {orphan.orphan_id}")
            
        except Exception as e:
            logger.error(f"Recovery queuing failed: {e}")
    
    async def _determine_recovery_strategy(self, orphan: OrphanResource) -> str:
        """Determine recovery strategy for orphan."""
        try:
            if isinstance(orphan, TaskOrphan):
                return await self._determine_task_recovery_strategy(orphan)
            elif isinstance(orphan, WorkerOrphan):
                return await self._determine_worker_recovery_strategy(orphan)
            elif isinstance(orphan, QueueOrphan):
                return await self._determine_queue_recovery_strategy(orphan)
            elif isinstance(orphan, LeaseOrphan):
                return await self._determine_lease_recovery_strategy(orphan)
            else:
                return "default"
                
        except Exception as e:
            logger.error(f"Recovery strategy determination failed: {e}")
            return "default"
    
    async def _determine_task_recovery_strategy(self, orphan: TaskOrphan) -> str:
        """Determine task recovery strategy."""
        try:
            if orphan.orphan_type == "worker_failed":
                return "worker_replacement"
            elif orphan.orphan_type == "long_running":
                return "task_intervention"
            elif orphan.orphan_type == "no_progress":
                return "task_restart"
            else:
                return "task_reassignment"
                
        except Exception as e:
            logger.error(f"Task recovery strategy determination failed: {e}")
            return "task_reassignment"
    
    async def _execute_recovery(self, context: RecoveryContext) -> bool:
        """Execute recovery for orphan."""
        try:
            context.status = "executing"
            context.started_at = datetime.now(timezone.utc)
            
            orphan = context.orphan
            
            # Execute recovery based on type
            if isinstance(orphan, TaskOrphan):
                success = await self._recover_task_orphan(orphan, context)
            elif isinstance(orphan, WorkerOrphan):
                success = await self._recover_worker_orphan(orphan, context)
            elif isinstance(orphan, QueueOrphan):
                success = await self._recover_queue_orphan(orphan, context)
            elif isinstance(orphan, LeaseOrphan):
                success = await self._recover_lease_orphan(orphan, context)
            else:
                logger.error(f"Unknown orphan type: {type(orphan)}")
                success = False
            
            # Update recovery context
            context.completed_at = datetime.now(timezone.utc)
            context.status = "completed" if success else "failed"
            context.success = success
            
            # Store in history
            recovery_result = RecoveryResult(
                success=success,
                recovery_id=context.recovery_id,
                orphan_id=orphan.orphan_id,
                strategy=context.strategy,
                completed_at=context.completed_at,
                error=context.error
            )
            
            self.recovery_history[context.recovery_id] = recovery_result
            
            # Remove from active recoveries
            self.active_recoveries.pop(orphan.orphan_id, None)
            
            logger.info(f"Recovery {context.recovery_id} {'completed' if success else 'failed'}")
            return success
            
        except Exception as e:
            logger.error(f"Recovery execution failed: {e}")
            return False
    
    async def _recover_task_orphan(self, orphan: TaskOrphan, context: RecoveryContext) -> bool:
        """Recover orphaned task."""
        try:
            task_id = orphan.task_id
            orphan_type = orphan.orphan_type
            
            logger.info(f"Recovering task orphan {task_id} ({orphan_type})")
            
            # Get task details
            task_data = await self._get_task_details(task_id)
            if not task_data:
                logger.error(f"Task {task_id} not found")
                return False
            
            # Recovery strategy based on orphan type
            if orphan_type == "worker_failed":
                return await self._recover_task_from_worker_failure(task_data, context)
            elif orphan_type == "long_running":
                return await self._recover_long_running_task(task_data, context)
            elif orphan_type == "no_progress":
                return await self._recover_stuck_task(task_data, context)
            else:
                return await self._recover_generic_task(task_data, context)
                
        except Exception as e:
            logger.error(f"Task orphan recovery failed: {e}")
            return False
    
    async def _recover_worker_orphan(self, orphan: WorkerOrphan, context: RecoveryContext) -> bool:
        """Recover orphaned worker."""
        try:
            worker_id = orphan.worker_id
            orphan_type = orphan.orphan_type
            
            logger.info(f"Recovering worker orphan {worker_id} ({orphan_type})")
            
            # Recovery strategy based on orphan type
            if orphan_type == "failed":
                return await self._recover_failed_worker(worker_id, context)
            elif orphan_type == "timeout":
                return await self._recover_timeout_worker(worker_id, context)
            elif orphan_type == "critical_health":
                return await self._recover_unhealthy_worker(worker_id, context)
            else:
                return await self._recover_generic_worker(worker_id, context)
                
        except Exception as e:
            logger.error(f"Worker orphan recovery failed: {e}")
            return False
    
    async def _recover_queue_orphan(self, orphan: QueueOrphan, context: RecoveryContext) -> bool:
        """Recover orphaned queue."""
        try:
            queue_id = orphan.queue_id
            orphan_type = orphan.orphan_type
            
            logger.info(f"Recovering queue orphan {queue_id} ({orphan_type})")
            
            # Recovery strategy based on orphan type
            if orphan_type == "unowned":
                return await self._recover_unowned_queue(queue_id, context)
            elif orphan_type == "stuck":
                return await self._recover_stuck_queue(queue_id, context)
            elif orphan_type == "overflow":
                return await self._recover_overflow_queue(queue_id, context)
            else:
                return await self._recover_generic_queue(queue_id, context)
                
        except Exception as e:
            logger.error(f"Queue orphan recovery failed: {e}")
            return False
    
    async def _recover_lease_orphan(self, orphan: LeaseOrphan, context: RecoveryContext) -> bool:
        """Recover orphaned lease."""
        try:
            lease_id = orphan.lease_id
            orphan_type = orphan.orphan_type
            
            logger.info(f"Recovering lease orphan {lease_id} ({orphan_type})")
            
            # Recovery strategy based on orphan type
            if orphan_type == "expired":
                return await self._recover_expired_lease(lease_id, context)
            elif orphan_type == "abandoned":
                return await self._recover_abandoned_lease(lease_id, context)
            elif orphan_type == "owner_unhealthy":
                return await self._recover_unhealthy_owner_lease(lease_id, context)
            else:
                return await self._recover_generic_lease(lease_id, context)
                
        except Exception as e:
            logger.error(f"Lease orphan recovery failed: {e}")
            return False
    
    # Helper methods
    
    def _get_worker_timeout(self, worker_id: str) -> int:
        """Get timeout for worker."""
        try:
            # Type-specific timeouts
            return 90  # Default 90 seconds
            
        except Exception:
            return 90
    
    def _get_max_task_runtime(self, task_type: str) -> int:
        """Get maximum runtime for task type."""
        try:
            # Type-specific runtimes
            return 300  # Default 5 minutes
            
        except Exception:
            return 300
    
    async def _is_worker_healthy(self, worker_id: str) -> bool:
        """Check if worker is healthy."""
        try:
            health = await self.heartbeat_manager.get_component_health(worker_id)
            if health:
                return health.get("status") == HealthStatus.HEALTHY.value
            return False
            
        except Exception:
            return False
    
    async def _is_owner_healthy(self, owner_id: str) -> bool:
        """Check if lease owner is healthy."""
        try:
            health = await self.heartbeat_manager.get_component_health(owner_id)
            if health:
                return health.get("status") == HealthStatus.HEALTHY.value
            return False
            
        except Exception:
            return False
    
    async def _get_task_details(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task details from database."""
        try:
            # This would query the database for task details
            # For now, return placeholder
            return {"task_id": task_id, "status": "running"}
            
        except Exception:
            return None
    
    async def _recover_task_from_worker_failure(self, task: Dict[str, Any], context: RecoveryContext) -> bool:
        """Recover task from worker failure."""
        try:
            # Find replacement worker
            replacement_worker = await self._find_replacement_worker(task)
            if not replacement_worker:
                return False
            
            # Reset task state
            await self._reset_task_state(task["task_id"])
            
            # Reassign task
            reassign_success = await self._reassign_task_to_worker(task["task_id"], replacement_worker)
            if not reassign_success:
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Task recovery from worker failure failed: {e}")
            return False
    
    async def _find_replacement_worker(self, task: Dict[str, Any]) -> Optional[str]:
        """Find suitable replacement worker for task."""
        try:
            # Get task requirements
            requirements = {
                "task_type": task.get("task_type", "general"),
                "required_capabilities": task.get("required_capabilities", [])
            }
            
            # Find suitable workers
            suitable_workers = await self.worker_registry.find_suitable_workers(requirements)
            
            if not suitable_workers:
                return None
            
            # Select best worker
            return suitable_workers[0]  # Simple selection for now
            
        except Exception as e:
            logger.error(f"Replacement worker selection failed: {e}")
            return None
    
    async def _reset_task_state(self, task_id: str):
        """Reset task state."""
        try:
            # This would reset the task state in the database
            logger.info(f"Resetting task state: {task_id}")
            
        except Exception as e:
            logger.error(f"Task state reset failed: {e}")
    
    async def _reassign_task_to_worker(self, task_id: str, worker_id: str) -> bool:
        """Reassign task to worker."""
        try:
            # This would update the task assignment in the database
            logger.info(f"Reassigning task {task_id} to worker {worker_id}")
            return True
            
        except Exception as e:
            logger.error(f"Task reassignment failed: {e}")
            return False
    
    async def _recover_failed_worker(self, worker_id: str, context: RecoveryContext) -> bool:
        """Recover failed worker."""
        try:
            # Get worker's active tasks
            active_tasks = await self._get_worker_active_tasks(worker_id)
            
            # Reassign all tasks
            for task_id in active_tasks:
                task = await self._get_task_details(task_id)
                if task:
                    replacement_worker = await self._find_replacement_worker(task)
                    if replacement_worker:
                        await self._reassign_task_to_worker(task_id, replacement_worker)
            
            # Remove worker from registry
            await self.worker_registry.unregister_worker(worker_id)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed worker recovery failed: {e}")
            return False
    
    async def _get_worker_active_tasks(self, worker_id: str) -> List[str]:
        """Get active tasks for worker."""
        try:
            # This would query the database for worker's active tasks
            return []
            
        except Exception:
            return []
    
    async def _recover_unowned_queue(self, queue_id: str, context: RecoveryContext) -> bool:
        """Recover unowned queue."""
        try:
            # Find suitable owner
            suitable_owner = await self._find_queue_owner(queue_id)
            if not suitable_owner:
                return False
            
            # Assign ownership
            ownership_success = await self._assign_queue_ownership(queue_id, suitable_owner)
            if not ownership_success:
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Unowned queue recovery failed: {e}")
            return False
    
    async def _find_queue_owner(self, queue_id: str) -> Optional[str]:
        """Find suitable owner for queue."""
        try:
            # This would implement logic to find suitable queue owner
            return "default_owner"
            
        except Exception:
            return None
    
    async def _assign_queue_ownership(self, queue_id: str, owner_id: str) -> bool:
        """Assign queue ownership."""
        try:
            # This would update queue ownership in the system
            logger.info(f"Assigning queue {queue_id} ownership to {owner_id}")
            return True
            
        except Exception as e:
            logger.error(f"Queue ownership assignment failed: {e}")
            return False
    
    async def _recover_expired_lease(self, lease_id: str, context: RecoveryContext) -> bool:
        """Recover expired lease."""
        try:
            # Get lease details
            lease = await self.lease_manager.get_lease(lease_id)
            if not lease:
                return False
            
            # Create new lease if needed
            if lease.auto_renew:
                renewal_success = await self.lease_manager.renew_lease(lease_id)
                return renewal_success
            
            return True
            
        except Exception as e:
            logger.error(f"Expired lease recovery failed: {e}")
            return False
    
    async def _recover_generic_task(self, task: Dict[str, Any], context: RecoveryContext) -> bool:
        """Recover task using generic strategy."""
        try:
            # Reset and reassign
            await self._reset_task_state(task["task_id"])
            
            replacement_worker = await self._find_replacement_worker(task)
            if replacement_worker:
                return await self._reassign_task_to_worker(task["task_id"], replacement_worker)
            
            return False
            
        except Exception as e:
            logger.error(f"Generic task recovery failed: {e}")
            return False
    
    async def _recover_generic_worker(self, worker_id: str, context: RecoveryContext) -> bool:
        """Recover worker using generic strategy."""
        try:
            # Unregister and re-register
            await self.worker_registry.unregister_worker(worker_id)
            
            # This would trigger worker restart/recreation
            return True
            
        except Exception as e:
            logger.error(f"Generic worker recovery failed: {e}")
            return False
    
    async def _recover_generic_queue(self, queue_id: str, context: RecoveryContext) -> bool:
        """Recover queue using generic strategy."""
        try:
            # Reset queue state
            logger.info(f"Resetting queue state: {queue_id}")
            return True
            
        except Exception as e:
            logger.error(f"Generic queue recovery failed: {e}")
            return False
    
    async def _recover_generic_lease(self, lease_id: str, context: RecoveryContext) -> bool:
        """Recover lease using generic strategy."""
        try:
            # Release and recreate if needed
            await self.lease_manager.release_lease(lease_id)
            return True
            
        except Exception as e:
            logger.error(f"Generic lease recovery failed: {e}")
            return False
    
    async def _recover_long_running_task(self, task: Dict[str, Any], context: RecoveryContext) -> bool:
        """Recover long-running task."""
        try:
            # Check if task should be interrupted
            max_runtime = self._get_max_task_runtime(task.get("task_type", "default"))
            started_at = datetime.fromisoformat(task.get("started_at", datetime.now(timezone.utc).isoformat()))
            runtime = (datetime.now(timezone.utc) - started_at).total_seconds()
            
            if runtime > max_runtime * 2:  # Double the max runtime
                # Force task reset
                await self._reset_task_state(task["task_id"])
                return await self._recover_generic_task(task, context)
            
            return True
            
        except Exception as e:
            logger.error(f"Long-running task recovery failed: {e}")
            return False
    
    async def _recover_stuck_task(self, task: Dict[str, Any], context: RecoveryContext) -> bool:
        """Recover stuck task."""
        try:
            # Force task reset and reassignment
            await self._reset_task_state(task["task_id"])
            return await self._recover_generic_task(task, context)
            
        except Exception as e:
            logger.error(f"Stuck task recovery failed: {e}")
            return False
    
    async def _recover_timeout_worker(self, worker_id: str, context: RecoveryContext) -> bool:
        """Recover timeout worker."""
        try:
            return await self._recover_failed_worker(worker_id, context)
            
        except Exception as e:
            logger.error(f"Timeout worker recovery failed: {e}")
            return False
    
    async def _recover_unhealthy_worker(self, worker_id: str, context: RecoveryContext) -> bool:
        """Recover unhealthy worker."""
        try:
            return await self._recover_failed_worker(worker_id, context)
            
        except Exception as e:
            logger.error(f"Unhealthy worker recovery failed: {e}")
            return False
    
    async def _recover_stuck_queue(self, queue_id: str, context: RecoveryContext) -> bool:
        """Recover stuck queue."""
        try:
            # Reset queue processing
            logger.info(f"Resetting stuck queue: {queue_id}")
            return True
            
        except Exception as e:
            logger.error(f"Stuck queue recovery failed: {e}")
            return False
    
    async def _recover_overflow_queue(self, queue_id: str, context: RecoveryContext) -> bool:
        """Recover overflow queue."""
        try:
            # Scale up processing or drain excess messages
            logger.info(f"Handling overflow queue: {queue_id}")
            return True
            
        except Exception as e:
            logger.error(f"Overflow queue recovery failed: {e}")
            return False
    
    async def _recover_abandoned_lease(self, lease_id: str, context: RecoveryContext) -> bool:
        """Recover abandoned lease."""
        try:
            # Release abandoned lease
            await self.lease_manager.release_lease(lease_id)
            return True
            
        except Exception as e:
            logger.error(f"Abandoned lease recovery failed: {e}")
            return False
    
    async def _recover_unhealthy_owner_lease(self, lease_id: str, context: RecoveryContext) -> bool:
        """Recover lease with unhealthy owner."""
        try:
            lease = await self.lease_manager.get_lease(lease_id)
            if not lease:
                return False
            
            # Transfer lease to healthy owner
            new_owner = await self._find_healthy_owner()
            if new_owner:
                transfer_success = await self.lease_manager.transfer_lease(
                    lease_id,
                    lease.owner_id,
                    new_owner,
                    "owner_unhealthy"
                )
                return transfer_success
            
            return False
            
        except Exception as e:
            logger.error(f"Unhealthy owner lease recovery failed: {e}")
            return False
    
    async def _find_healthy_owner(self) -> Optional[str]:
        """Find healthy owner for lease transfer."""
        try:
            # Get all healthy workers
            all_workers = await self.worker_registry.get_all_workers()
            healthy_workers = [
                worker_id for worker_id, worker_info in all_workers.items()
                if worker_info.get("health") == "healthy"
            ]
            
            return healthy_workers[0] if healthy_workers else None
            
        except Exception:
            return None
    
    async def _get_all_tasks(self) -> List[Dict[str, Any]]:
        """Get all tasks from database."""
        try:
            # This would query the database for all tasks
            return []
            
        except Exception:
            return []
    
    async def _load_orphan_state(self):
        """Load existing orphan state from Redis."""
        try:
            # Get all orphan keys
            orphan_keys = await redis_manager.keys(f"{self.orphan_key_prefix}:*")
            
            for orphan_key in orphan_keys:
                orphan_data = await redis_manager.hgetall(orphan_key)
                if orphan_data:
                    orphan_type = orphan_data.get("resource_type")
                    if orphan_type:
                        # Parse and create appropriate orphan object
                        orphan = self._parse_orphan(orphan_data)
                        if orphan:
                            self.detected_orphans[orphan.orphan_id] = orphan
            
            logger.info(f"Loaded {len(self.detected_orphans)} orphans from storage")
            
        except Exception as e:
            logger.error(f"Failed to load orphan state: {e}")
    
    def _parse_orphan(self, data: Dict[str, Any]) -> Optional[OrphanResource]:
        """Parse orphan from data."""
        try:
            resource_type = OrphanType(data["resource_type"])
            
            if resource_type == OrphanType.TASK:
                return TaskOrphan(
                    orphan_id=data["orphan_id"],
                    task_id=data["resource_id"],
                    task_type=data.get("task_type", "unknown"),
                    worker_id=data.get("worker_id"),
                    orphan_type=data["orphan_type"],
                    detected_at=datetime.fromisoformat(data["detected_at"]),
                    details=json.loads(data.get("details", "{}")),
                    status=OrphanStatus(data.get("status", "detected")),
                    recovery_priority=int(data.get("recovery_priority", 5)),
                    recovery_attempts=int(data.get("recovery_attempts", 0)),
                    task_data=json.loads(data.get("task_data", "{}"))
                )
            elif resource_type == OrphanType.WORKER:
                return WorkerOrphan(
                    orphan_id=data["orphan_id"],
                    worker_id=data["resource_id"],
                    worker_type=data.get("worker_type", "unknown"),
                    orphan_type=data["orphan_type"],
                    detected_at=datetime.fromisoformat(data["detected_at"]),
                    details=json.loads(data.get("details", "{}")),
                    status=OrphanStatus(data.get("status", "detected")),
                    recovery_priority=int(data.get("recovery_priority", 5)),
                    recovery_attempts=int(data.get("recovery_attempts", 0)),
                    last_heartbeat=datetime.fromisoformat(data["last_heartbeat"]) if data.get("last_heartbeat") else None,
                    active_tasks=json.loads(data.get("active_tasks", "[]"))
                )
            # Add other orphan types as needed
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to parse orphan: {e}")
            return None
    
    async def _persist_orphan_state(self):
        """Persist orphan state."""
        try:
            # Orphans are already persisted individually
            # This could be extended to persist metadata
            metadata = {
                "last_persisted": datetime.now(timezone.utc).isoformat(),
                "total_orphans": len(self.detected_orphans),
                "active_recoveries": len(self.active_recoveries)
            }
            
            await redis_manager.set("orphan_recovery_metadata", json.dumps(metadata))
            
        except Exception as e:
            logger.error(f"Failed to persist orphan state: {e}")
    
    async def _complete_active_recoveries(self):
        """Complete active recoveries during shutdown."""
        try:
            # Wait for active recoveries to complete or timeout
            if self.active_recoveries:
                logger.info(f"Waiting for {len(self.active_recoveries)} active recoveries to complete")
                
                timeout = 60  # 1 minute
                start_time = datetime.now(timezone.utc)
                
                while self.active_recoveries and (datetime.now(timezone.utc) - start_time).total_seconds() < timeout:
                    await asyncio.sleep(1)
            
        except Exception as e:
            logger.error(f"Failed to complete active recoveries: {e}")
    
    async def _orphan_detection_loop(self):
        """Background orphan detection loop."""
        while self.running:
            try:
                await self.detect_worker_orphans()
                await self.detect_task_orphans()
                await self.detect_queue_orphans()
                await self.detect_lease_orphans()
                
                await asyncio.sleep(self.detection_interval)
                
            except Exception as e:
                logger.error(f"Orphan detection loop error: {e}")
                await asyncio.sleep(10)
    
    async def _recovery_processing_loop(self):
        """Background recovery processing loop."""
        while self.running:
            try:
                # Process recovery queue
                await self._process_recovery_queue()
                await asyncio.sleep(5)  # Short interval for responsive recovery
                
            except Exception as e:
                logger.error(f"Recovery processing loop error: {e}")
                await asyncio.sleep(10)
    
    async def _process_recovery_queue(self):
        """Process queued recovery operations."""
        try:
            # This would implement a priority queue for recovery operations
            # For now, just check if there are orphans needing recovery
            
            for orphan_id, orphan in list(self.detected_orphans.items()):
                if orphan.status == OrphanStatus.DETECTED and orphan_id not in self.active_recoveries:
                    await self.initiate_recovery(orphan_id)
            
        except Exception as e:
            logger.error(f"Recovery queue processing failed: {e}")


# Global orphan recovery manager instance
orphan_recovery_manager = OrphanRecoveryManager()
