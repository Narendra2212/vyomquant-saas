"""
Distributed Execution Coordinator

This module implements the distributed execution coordinator for Phase 3
distributed orchestration. The coordinator manages distributed task execution,
worker coordination, and resource allocation while preserving deterministic
guarantees and replay safety.
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
from backend_app.core.models.dag_task import DAGTaskModel as DAGTask, TaskStatus
from .queue_manager import DistributedQueueManager, QueueType, ExecutionJob
from .replay_engine import immutable_journal, sequence_manager
from .replay_validator import event_signer
from .lease_management_architecture import LeaseManager, LeaseRequest, LeaseType
from .worker_topology_architecture import WorkerRegistry, WorkerInfo
from .heartbeat_architecture import HeartbeatCollector, HeartbeatGenerator
from .orphan_recovery_architecture import OrphanDetector, OrphanRecoverer
from .deterministic_reassignment_model import DeterministicReassignmentCoordinator
from .orchestration_safety_guarantees import (
    DeterministicAssignmentGuarantee,
    AtomicTransactionGuarantee,
    SplitBrainPreventionGuarantee
)

logger = logging.getLogger(__name__)


class CoordinatorState(Enum):
    """Coordinator operational states."""
    STARTING = "starting"
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    SHUTTING_DOWN = "shutting_down"
    SHUTDOWN = "shutdown"


class TaskPriority(Enum):
    """Task execution priorities."""
    LOW = 1
    NORMAL = 5
    HIGH = 10
    CRITICAL = 20


@dataclass
class TaskAssignment:
    """Task assignment information."""
    task_id: str
    worker_id: str
    assigned_at: datetime
    lease_id: Optional[str] = None
    expires_at: Optional[datetime] = None
    status: str = "assigned"


@dataclass
class CoordinatorMetrics:
    """Coordinator performance metrics."""
    total_tasks_assigned: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    active_workers: int = 0
    average_assignment_time_ms: float = 0.0
    queue_depth: int = 0
    last_assignment_time: Optional[datetime] = None


class DistributedExecutionCoordinator:
    """
    Distributed Execution Coordinator
    
    Manages distributed task execution with deterministic guarantees,
    worker coordination, and replay safety.
    """
    
    def __init__(self, coordinator_id: Optional[str] = None):
        self.coordinator_id = coordinator_id or f"coordinator_{uuid.uuid4().hex[:8]}"
        self.state = CoordinatorState.STARTING
        
        # Core components
        self.queue_manager = DistributedQueueManager()
        self.worker_registry = WorkerRegistry()
        self.lease_manager = LeaseManager()
        self.heartbeat_collector = HeartbeatCollector()
        self.heartbeat_generator = HeartbeatGenerator(
            self.coordinator_id,
            "execution_coordinator",
            interval_seconds=30
        )
        self.orphan_detector = OrphanDetector(
            self.heartbeat_collector,
            self.lease_manager,
            self.queue_manager
        )
        self.orphan_recoverer = OrphanRecoverer(
            self.orphan_detector,
            self.worker_registry,
            self.lease_manager,
            self.queue_manager
        )
        self.reassignment_coordinator = DeterministicReassignmentCoordinator()
        
        # Safety guarantees
        self.deterministic_guarantee = DeterministicAssignmentGuarantee()
        self.transaction_guarantee = AtomicTransactionGuarantee()
        self.split_brain_guarantee = SplitBrainPreventionGuarantee()
        
        # Runtime state
        self.running = False
        self.task_assignments: Dict[str, TaskAssignment] = {}
        self.active_tasks: Set[str] = set()
        self.metrics = CoordinatorMetrics()
        self.assignment_lock = asyncio.Lock()
        
        # Configuration
        self.max_concurrent_assignments = 100
        self.assignment_timeout = 300  # 5 minutes
        self.heartbeat_interval = 30
        self.orphan_check_interval = 60
        self.metrics_interval = 30
        
        # Background tasks
        self.coordination_task: Optional[asyncio.Task] = None
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.orphan_detection_task: Optional[asyncio.Task] = None
        self.metrics_task: Optional[asyncio.Task] = None
    
    async def start(self) -> bool:
        """Start the execution coordinator."""
        try:
            logger.info(f"Starting execution coordinator: {self.coordinator_id}")
            
            # Initialize components
            await self._initialize_components()
            
            # Acquire coordinator leadership
            leadership_acquired = await self._acquire_leadership()
            if not leadership_acquired:
                logger.warning("Failed to acquire coordinator leadership")
                return False
            
            # Start background tasks
            self.running = True
            self.state = CoordinatorState.ACTIVE
            
            self.coordination_task = asyncio.create_task(self._coordination_loop())
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self.orphan_detection_task = asyncio.create_task(self._orphan_detection_loop())
            self.metrics_task = asyncio.create_task(self._metrics_loop())
            
            # Start heartbeat generation
            await self.heartbeat_generator.start()
            
            logger.info(f"Execution coordinator started: {self.coordinator_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start execution coordinator: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop the execution coordinator."""
        try:
            logger.info(f"Stopping execution coordinator: {self.coordinator_id}")
            
            self.running = False
            self.state = CoordinatorState.SHUTTING_DOWN
            
            # Stop heartbeat generation
            await self.heartbeat_generator.stop()
            
            # Cancel background tasks
            tasks = [
                self.coordination_task,
                self.heartbeat_task,
                self.orphan_detection_task,
                self.metrics_task
            ]
            
            for task in tasks:
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            # Release leadership
            await self._release_leadership()
            
            # Complete active assignments
            await self._complete_active_assignments()
            
            self.state = CoordinatorState.SHUTDOWN
            logger.info(f"Execution coordinator stopped: {self.coordinator_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop execution coordinator: {e}")
            return False
    
    async def _initialize_components(self):
        """Initialize coordinator components."""
        try:
            # Initialize worker registry
            await self.worker_registry.initialize()
            
            # Initialize lease manager
            await self.lease_manager.initialize()
            
            # Start heartbeat collection
            await self.heartbeat_collector.start()
            
            # Initialize safety guarantees
            await self.deterministic_guarantee.initialize()
            await self.transaction_guarantee.initialize()
            await self.split_brain_guarantee.initialize()
            
            logger.info("Coordinator components initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize coordinator components: {e}")
            raise
    
    async def _acquire_leadership(self) -> bool:
        """Acquire coordinator leadership lease."""
        try:
            lease_request = LeaseRequest(
                resource_id="execution_coordinator",
                requester_id=self.coordinator_id,
                lease_type=LeaseType.EXCLUSIVE,
                ttl_seconds=3600,  # 1 hour
                auto_renew=True,
                max_renewals=100,
                priority=20,  # High priority for coordinator
                metadata={
                    "coordinator_type": "execution_coordinator",
                    "started_at": datetime.now(timezone.utc).isoformat()
                }
            )
            
            lease_result = await self.lease_manager.acquire_lease(lease_request)
            
            if lease_result.success:
                logger.info(f"Coordinator leadership acquired: {lease_result.lease.lease_id}")
                return True
            else:
                logger.warning(f"Failed to acquire leadership: {lease_result.reason}")
                return False
                
        except Exception as e:
            logger.error(f"Leadership acquisition failed: {e}")
            return False
    
    async def _release_leadership(self):
        """Release coordinator leadership lease."""
        try:
            # Find and release coordinator lease
            coordinator_leases = await self.lease_manager.get_leases_by_owner(self.coordinator_id)
            
            for lease in coordinator_leases:
                if lease.resource_id == "execution_coordinator":
                    await self.lease_manager.release_lease(lease.lease_id)
                    logger.info(f"Released coordinator lease: {lease.lease_id}")
                    break
            
        except Exception as e:
            logger.error(f"Leadership release failed: {e}")
    
    async def _coordination_loop(self):
        """Main coordination loop for task assignment."""
        while self.running:
            try:
                # Check coordinator capacity
                if len(self.active_tasks) >= self.max_concurrent_assignments:
                    await asyncio.sleep(1)
                    continue
                
                # Get next task from queue
                task = await self._get_next_task()
                if not task:
                    await asyncio.sleep(0.5)
                    continue
                
                # Assign task to worker
                assignment_success = await self._assign_task(task)
                if assignment_success:
                    self.metrics.total_tasks_assigned += 1
                    self.metrics.last_assignment_time = datetime.now(timezone.utc)
                
            except Exception as e:
                logger.error(f"Coordination loop error: {e}")
                await asyncio.sleep(5)
    
    async def _get_next_task(self) -> Optional[ExecutionJob]:
        """Get next task from execution queue."""
        try:
            # Get task from queue manager
            task = await self.queue_manager.consume_execution_job(
                consumer_group="coordinator_group",
                consumer_id=self.coordinator_id
            )
            
            if task:
                logger.debug(f"Retrieved task: {task.job_id}")
                return task
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get next task: {e}")
            return None
    
    async def _assign_task(self, task: ExecutionJob) -> bool:
        """Assign task to suitable worker."""
        try:
            async with self.assignment_lock:
                # Get task requirements
                task_requirements = self._extract_task_requirements(task)
                
                # Find suitable workers
                suitable_workers = await self._find_suitable_workers(task_requirements)
                if not suitable_workers:
                    logger.warning(f"No suitable workers for task: {task.job_id}")
                    # Requeue task
                    await self.queue_manager.publish(QueueType.EXECUTION, task)
                    return False
                
                # Select worker deterministically
                selected_worker = await self._select_worker_deterministically(task, suitable_workers)
                if not selected_worker:
                    logger.warning(f"Worker selection failed for task: {task.job_id}")
                    await self.queue_manager.publish(QueueType.EXECUTION, task)
                    return False
                
                # Acquire task lease
                lease_acquired = await self._acquire_task_lease(task, selected_worker)
                if not lease_acquired:
                    logger.warning(f"Failed to acquire task lease: {task.job_id}")
                    await self.queue_manager.publish(QueueType.EXECUTION, task)
                    return False
                
                # Create assignment
                assignment = TaskAssignment(
                    task_id=task.job_id,
                    worker_id=selected_worker,
                    assigned_at=datetime.now(timezone.utc),
                    lease_id=lease_acquired.lease_id,
                    expires_at=lease_acquired.expires_at
                )
                
                # Store assignment
                self.task_assignments[task.job_id] = assignment
                self.active_tasks.add(task.job_id)
                
                # Send task to worker
                delivery_success = await self._deliver_task_to_worker(task, selected_worker)
                if not delivery_success:
                    # Clean up assignment
                    await self._cleanup_failed_assignment(task.job_id)
                    await self.queue_manager.publish(QueueType.EXECUTION, task)
                    return False
                
                logger.info(f"Task assigned: {task.job_id} → {selected_worker}")
                return True
                
        except Exception as e:
            logger.error(f"Task assignment failed: {e}")
            return False
    
    def _extract_task_requirements(self, task: ExecutionJob) -> Dict[str, Any]:
        """Extract requirements from task."""
        try:
            payload = task.payload or {}
            
            requirements = {
                "task_type": payload.get("task_type", "general"),
                "priority": TaskPriority(payload.get("priority", 5)),
                "estimated_duration": payload.get("estimated_duration", 60),  # seconds
                "required_capabilities": payload.get("required_capabilities", []),
                "memory_requirement": payload.get("memory_requirement", 512),  # MB
                "cpu_requirement": payload.get("cpu_requirement", 1),  # cores
                "tenant_id": task.tenant_id
            }
            
            return requirements
            
        except Exception as e:
            logger.error(f"Failed to extract task requirements: {e}")
            return {}
    
    async def _find_suitable_workers(self, requirements: Dict[str, Any]) -> List[str]:
        """Find workers suitable for task requirements."""
        try:
            # Get all healthy workers
            all_workers = await self.worker_registry.get_all_workers()
            healthy_workers = [
                worker_id for worker_id, worker_info in all_workers.items()
                if worker_info.get("health") == "healthy" and
                worker_info.get("status") == "active"
            ]
            
            if not healthy_workers:
                return []
            
            # Filter by capabilities
            required_capabilities = requirements.get("required_capabilities", [])
            if required_capabilities:
                suitable_workers = []
                
                for worker_id in healthy_workers:
                    worker_info = all_workers[worker_id]
                    worker_capabilities = worker_info.get("capabilities", {})
                    
                    # Check if worker has all required capabilities
                    if all(cap in worker_capabilities for cap in required_capabilities):
                        suitable_workers.append(worker_id)
                
                return suitable_workers
            
            return healthy_workers
            
        except Exception as e:
            logger.error(f"Failed to find suitable workers: {e}")
            return []
    
    async def _select_worker_deterministically(self, task: ExecutionJob, workers: List[str]) -> Optional[str]:
        """Select worker using deterministic assignment."""
        try:
            # Use deterministic guarantee for selection
            selected_worker = self.deterministic_guarantee.guarantee_deterministic_selection(
                task.job_id,
                workers
            )
            
            return selected_worker
            
        except Exception as e:
            logger.error(f"Deterministic worker selection failed: {e}")
            return None
    
    async def _acquire_task_lease(self, task: ExecutionJob, worker_id: str) -> Optional[Any]:
        """Acquire lease for task execution."""
        try:
            lease_request = LeaseRequest(
                resource_id=f"task:{task.job_id}",
                requester_id=worker_id,
                lease_type=LeaseType.EXCLUSIVE,
                ttl_seconds=self.assignment_timeout,
                auto_renew=False,  # Task lease doesn't auto-renew
                max_renewals=0,
                priority=task.priority.value if hasattr(task, 'priority') else 5,
                metadata={
                    "task_id": task.job_id,
                    "tenant_id": task.tenant_id,
                    "coordinator_id": self.coordinator_id,
                    "worker_id": worker_id
                }
            )
            
            lease_result = await self.lease_manager.acquire_lease(lease_request)
            
            if lease_result.success:
                return lease_result.lease
            else:
                logger.warning(f"Task lease acquisition failed: {lease_result.reason}")
                return None
                
        except Exception as e:
            logger.error(f"Task lease acquisition failed: {e}")
            return None
    
    async def _deliver_task_to_worker(self, task: ExecutionJob, worker_id: str) -> bool:
        """Deliver task to assigned worker."""
        try:
            # Get worker connection info
            worker_info = await self.worker_registry.get_worker_info(worker_id)
            if not worker_info:
                logger.error(f"Worker not found: {worker_id}")
                return False
            
            # Send task to worker via worker's queue
            worker_queue = f"worker:{worker_id}:tasks"
            
            delivery_task = ExecutionJob()
            delivery_task.job_id = task.job_id
            delivery_task.tenant_id = task.tenant_id
            delivery_task.payload = {
                **(task.payload or {}),
                "assigned_by": self.coordinator_id,
                "assigned_at": datetime.now(timezone.utc).isoformat(),
                "lease_id": self.task_assignments[task.job_id].lease_id
            }
            delivery_task.priority = task.priority
            delivery_task.created_at = task.created_at
            
            await self.queue_manager.backend.publish(worker_queue, delivery_task)
            
            logger.debug(f"Task delivered to worker: {task.job_id} → {worker_id}")
            return True
            
        except Exception as e:
            logger.error(f"Task delivery failed: {e}")
            return False
    
    async def _cleanup_failed_assignment(self, task_id: str):
        """Clean up failed task assignment."""
        try:
            # Remove from active tasks
            self.active_tasks.discard(task_id)
            
            # Remove assignment record
            if task_id in self.task_assignments:
                assignment = self.task_assignments[task_id]
                
                # Release task lease if exists
                if assignment.lease_id:
                    await self.lease_manager.release_lease(assignment.lease_id)
                
                del self.task_assignments[task_id]
            
        except Exception as e:
            logger.error(f"Failed to cleanup assignment: {task_id}: {e}")
    
    async def _heartbeat_loop(self):
        """Coordinator heartbeat loop."""
        while self.running:
            try:
                # Update coordinator status
                await self._update_coordinator_status()
                
                await asyncio.sleep(self.heartbeat_interval)
                
            except Exception as e:
                logger.error(f"Heartbeat loop error: {e}")
                await asyncio.sleep(5)
    
    async def _update_coordinator_status(self):
        """Update coordinator status in registry."""
        try:
            coordinator_info = {
                "coordinator_id": self.coordinator_id,
                "state": self.state.value,
                "active_tasks": len(self.active_tasks),
                "total_assignments": self.metrics.total_tasks_assigned,
                "completed_tasks": self.metrics.tasks_completed,
                "failed_tasks": self.metrics.tasks_failed,
                "active_workers": self.metrics.active_workers,
                "last_heartbeat": datetime.now(timezone.utc).isoformat()
            }
            
            # Store in Redis
            await redis_manager.hset(
                f"coordinator:{self.coordinator_id}",
                mapping=coordinator_info
            )
            
            # Set expiration
            await redis_manager.expire(f"coordinator:{self.coordinator_id}", 300)  # 5 minutes
            
        except Exception as e:
            logger.error(f"Failed to update coordinator status: {e}")
    
    async def _orphan_detection_loop(self):
        """Orphan detection and recovery loop."""
        while self.running:
            try:
                # Trigger orphan detection
                await self.orphan_detector._detect_worker_orphans()
                await self.orphan_detector._detect_task_orphans()
                await self.orphan_detector._detect_queue_orphans()
                await self.orphan_detector._detect_lease_orphans()
                
                await asyncio.sleep(self.orphan_check_interval)
                
            except Exception as e:
                logger.error(f"Orphan detection loop error: {e}")
                await asyncio.sleep(10)
    
    async def _metrics_loop(self):
        """Metrics collection and reporting loop."""
        while self.running:
            try:
                # Update metrics
                await self._update_metrics()
                
                # Report metrics
                await self._report_metrics()
                
                await asyncio.sleep(self.metrics_interval)
                
            except Exception as e:
                logger.error(f"Metrics loop error: {e}")
                await asyncio.sleep(10)
    
    async def _update_metrics(self):
        """Update coordinator metrics."""
        try:
            # Get active worker count
            all_workers = await self.worker_registry.get_all_workers()
            self.metrics.active_workers = len([
                worker_id for worker_id, worker_info in all_workers.items()
                if worker_info.get("health") == "healthy"
            ])
            
            # Get queue depth
            queue_stats = await self.queue_manager.get_queue_stats(QueueType.EXECUTION)
            self.metrics.queue_depth = queue_stats.get("depth", 0)
            
            # Calculate assignment time
            if self.metrics.total_tasks_assigned > 0:
                # This would be calculated from actual assignment times
                self.metrics.average_assignment_time_ms = 50.0  # Placeholder
            
        except Exception as e:
            logger.error(f"Failed to update metrics: {e}")
    
    async def _report_metrics(self):
        """Report coordinator metrics."""
        try:
            metrics_data = {
                "coordinator_id": self.coordinator_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metrics": {
                    "total_tasks_assigned": self.metrics.total_tasks_assigned,
                    "tasks_completed": self.metrics.tasks_completed,
                    "tasks_failed": self.metrics.tasks_failed,
                    "active_tasks": len(self.active_tasks),
                    "active_workers": self.metrics.active_workers,
                    "queue_depth": self.metrics.queue_depth,
                    "average_assignment_time_ms": self.metrics.average_assignment_time_ms
                }
            }
            
            # Store metrics
            await redis_manager.set(
                f"metrics:coordinator:{self.coordinator_id}",
                json.dumps(metrics_data),
                ex=3600  # 1 hour
            )
            
        except Exception as e:
            logger.error(f"Failed to report metrics: {e}")
    
    async def _complete_active_assignments(self):
        """Complete active assignments during shutdown."""
        try:
            # Wait for active tasks to complete or timeout
            if self.active_tasks:
                logger.info(f"Waiting for {len(self.active_tasks)} active tasks to complete")
                
                # Wait with timeout
                timeout = 60  # 1 minute
                start_time = datetime.now(timezone.utc)
                
                while self.active_tasks and (datetime.now(timezone.utc) - start_time).total_seconds() < timeout:
                    await asyncio.sleep(1)
                
                # Force complete remaining tasks
                for task_id in list(self.active_tasks):
                    await self._cleanup_failed_assignment(task_id)
            
        except Exception as e:
            logger.error(f"Failed to complete active assignments: {e}")
    
    async def handle_task_completion(self, task_id: str, worker_id: str, result: Dict[str, Any]) -> bool:
        """Handle task completion from worker."""
        try:
            async with self.assignment_lock:
                if task_id not in self.task_assignments:
                    logger.warning(f"Task assignment not found: {task_id}")
                    return False
                
                assignment = self.task_assignments[task_id]
                
                # Verify worker assignment
                if assignment.worker_id != worker_id:
                    logger.warning(f"Worker mismatch for task {task_id}: expected {assignment.worker_id}, got {worker_id}")
                    return False
                
                # Update metrics
                if result.get("success", False):
                    self.metrics.tasks_completed += 1
                else:
                    self.metrics.tasks_failed += 1
                
                # Clean up assignment
                await self._cleanup_failed_assignment(task_id)
                
                # Acknowledge task in queue
                await self.queue_manager.acknowledge_job(task_id)
                
                logger.info(f"Task completed: {task_id} by {worker_id}")
                return True
                
        except Exception as e:
            logger.error(f"Task completion handling failed: {e}")
            return False
    
    async def get_coordinator_status(self) -> Dict[str, Any]:
        """Get current coordinator status."""
        try:
            return {
                "coordinator_id": self.coordinator_id,
                "state": self.state.value,
                "running": self.running,
                "active_tasks": len(self.active_tasks),
                "total_assignments": self.metrics.total_tasks_assigned,
                "metrics": {
                    "tasks_completed": self.metrics.tasks_completed,
                    "tasks_failed": self.metrics.tasks_failed,
                    "active_workers": self.metrics.active_workers,
                    "queue_depth": self.metrics.queue_depth,
                    "average_assignment_time_ms": self.metrics.average_assignment_time_ms
                },
                "last_assignment_time": self.metrics.last_assignment_time.isoformat() if self.metrics.last_assignment_time else None
            }
            
        except Exception as e:
            logger.error(f"Failed to get coordinator status: {e}")
            return {"error": str(e)}


# Global coordinator instance
execution_coordinator = DistributedExecutionCoordinator()
