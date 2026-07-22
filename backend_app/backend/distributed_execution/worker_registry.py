"""
Worker Registry

This module implements the worker registry for Phase 3 distributed orchestration.
The registry manages worker registration, discovery, capabilities, health monitoring,
and lifecycle management while preserving deterministic guarantees.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from backend_app.core.cache.redis_manager import redis_manager

from .deterministic_reassignment_model import DeterministicCapabilityAssigner
from .heartbeat_architecture import HeartbeatCollector
from .orchestration_safety_guarantees import (DeterministicAssignmentGuarantee,
                                              OperationIsolationGuarantee)

logger = logging.getLogger(__name__)


class WorkerState(Enum):
    """Worker operational states."""
    REGISTERING = "registering"
    ACTIVE = "active"
    BUSY = "busy"
    MAINTENANCE = "maintenance"
    DRAINING = "draining"
    FAILED = "failed"
    SHUTDOWN = "shutdown"


class WorkerType(Enum):
    """Worker types."""
    EXECUTION = "execution_worker"
    DAG = "dag_worker"
    SPECIALIZED = "specialized_worker"
    COORDINATOR = "coordinator_worker"


@dataclass
class WorkerCapabilities:
    """Worker capabilities definition."""
    exchanges: List[str] = field(default_factory=list)
    order_types: List[str] = field(default_factory=list)
    max_concurrent_jobs: int = 5
    memory_mb: int = 1024
    cpu_cores: int = 2
    specializations: List[str] = field(default_factory=list)
    supported_tasks: List[str] = field(default_factory=list)
    performance_tier: str = "standard"  # standard, high, ultra


@dataclass
class WorkerMetrics:
    """Worker performance metrics."""
    cpu_utilization: float = 0.0
    memory_utilization: float = 0.0
    active_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    average_job_duration_ms: float = 0.0
    last_job_completion: Optional[datetime] = None
    uptime_seconds: int = 0
    response_time_p95_ms: float = 0.0


@dataclass
class WorkerInfo:
    """Complete worker information."""
    worker_id: str
    worker_type: WorkerType
    state: WorkerState
    capabilities: WorkerCapabilities
    metrics: WorkerMetrics
    hostname: str
    pid: int
    started_at: datetime
    last_heartbeat: datetime
    registration_time: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: Set[str] = field(default_factory=set)
    load_ratio: float = 0.0
    health_score: float = 1.0


class WorkerRegistry:
    """
    Worker Registry
    
    Manages worker registration, discovery, capabilities, and health monitoring
    with deterministic guarantees and replay safety.
    """
    
    def __init__(self):
        self.registry_key = "worker_registry"
        self.worker_index_key = "worker_index"
        self.capability_index_key = "capability_index"
        
        # Components
        self.heartbeat_collector = HeartbeatCollector()
        self.capability_assigner = DeterministicCapabilityAssigner()
        
        # Safety guarantees
        self.deterministic_guarantee = DeterministicAssignmentGuarantee()
        self.isolation_guarantee = OperationIsolationGuarantee()
        
        # Registry state
        self.workers: Dict[str, WorkerInfo] = {}
        self.worker_capabilities: Dict[str, WorkerCapabilities] = {}
        self.worker_metrics: Dict[str, WorkerMetrics] = {}
        
        # Configuration
        self.worker_timeout = 300  # 5 minutes
        self.health_check_interval = 60  # 1 minute
        self.cleanup_interval = 300  # 5 minutes
        self.metrics_retention_period = timedelta(days=7)
        
        # Background tasks
        self.health_check_task: Optional[asyncio.Task] = None
        self.cleanup_task: Optional[asyncio.Task] = None
        self.running = False
    
    async def initialize(self) -> bool:
        """Initialize the worker registry."""
        try:
            logger.info("Initializing worker registry")
            
            # Start heartbeat collection
            await self.heartbeat_collector.start()
            
            # Load existing workers from storage
            await self._load_workers_from_storage()
            
            # Start background tasks
            self.running = True
            self.health_check_task = asyncio.create_task(self._health_check_loop())
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())
            
            logger.info("Worker registry initialized")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize worker registry: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop the worker registry."""
        try:
            logger.info("Stopping worker registry")
            
            self.running = False
            
            # Cancel background tasks
            if self.health_check_task and not self.health_check_task.done():
                self.health_check_task.cancel()
                try:
                    await self.health_check_task
                except asyncio.CancelledError:
                    pass
            
            if self.cleanup_task and not self.cleanup_task.done():
                self.cleanup_task.cancel()
                try:
                    await self.cleanup_task
                except asyncio.CancelledError:
                    pass
            
            # Stop heartbeat collection
            await self.heartbeat_collector.stop()
            
            # Persist registry state
            await self._persist_registry_state()
            
            logger.info("Worker registry stopped")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop worker registry: {e}")
            return False
    
    async def register_worker(
        self,
        worker_id: str,
        worker_type: WorkerType,
        hostname: str,
        pid: int,
        capabilities: WorkerCapabilities,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[Set[str]] = None
    ) -> bool:
        """Register a new worker."""
        try:
            # Create worker info
            worker_info = WorkerInfo(
                worker_id=worker_id,
                worker_type=worker_type,
                state=WorkerState.REGISTERING,
                capabilities=capabilities,
                metrics=WorkerMetrics(),
                hostname=hostname,
                pid=pid,
                started_at=datetime.now(timezone.utc),
                last_heartbeat=datetime.now(timezone.utc),
                registration_time=datetime.now(timezone.utc),
                metadata=metadata or {},
                tags=tags or set()
            )
            
            # Check for existing registration
            if worker_id in self.workers:
                logger.warning(f"Worker already registered: {worker_id}")
                return await self._update_worker_registration(worker_info)
            
            # Store worker in registry
            await self._store_worker(worker_info)
            
            # Update indexes
            await self._update_capability_index(worker_id, capabilities)
            await self._update_worker_index(worker_id, worker_info)
            
            # Change state to active
            worker_info.state = WorkerState.ACTIVE
            await self._update_worker_state(worker_id, WorkerState.ACTIVE)
            
            logger.info(f"Worker registered: {worker_id} ({worker_type.value})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to register worker {worker_id}: {e}")
            return False
    
    async def unregister_worker(self, worker_id: str, reason: str = "shutdown") -> bool:
        """Unregister a worker."""
        try:
            if worker_id not in self.workers:
                logger.warning(f"Worker not found for unregistration: {worker_id}")
                return False
            
            worker_info = self.workers[worker_id]
            
            # Change state to shutdown
            worker_info.state = WorkerState.SHUTDOWN
            await self._update_worker_state(worker_id, WorkerState.SHUTDOWN)
            
            # Remove from indexes
            await self._remove_from_capability_index(worker_id)
            await self._remove_from_worker_index(worker_id)
            
            # Remove from registry
            await self._remove_worker(worker_id)
            
            logger.info(f"Worker unregistered: {worker_id} ({reason})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to unregister worker {worker_id}: {e}")
            return False
    
    async def update_worker_heartbeat(self, worker_id: str, metrics: WorkerMetrics) -> bool:
        """Update worker heartbeat and metrics."""
        try:
            if worker_id not in self.workers:
                logger.warning(f"Heartbeat from unregistered worker: {worker_id}")
                return False
            
            worker_info = self.workers[worker_id]
            
            # Update heartbeat time
            worker_info.last_heartbeat = datetime.now(timezone.utc)
            
            # Update metrics
            worker_info.metrics = metrics
            
            # Update load ratio
            worker_info.load_ratio = metrics.active_jobs / worker_info.capabilities.max_concurrent_jobs
            
            # Calculate health score
            worker_info.health_score = self._calculate_health_score(worker_info)
            
            # Update state based on load
            if worker_info.state == WorkerState.ACTIVE and metrics.active_jobs > 0:
                worker_info.state = WorkerState.BUSY
                await self._update_worker_state(worker_id, WorkerState.BUSY)
            elif worker_info.state == WorkerState.BUSY and metrics.active_jobs == 0:
                worker_info.state = WorkerState.ACTIVE
                await self._update_worker_state(worker_id, WorkerState.ACTIVE)
            
            # Store updated worker
            await self._store_worker(worker_info)
            
            logger.debug(f"Heartbeat updated: {worker_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update worker heartbeat {worker_id}: {e}")
            return False
    
    async def get_worker_info(self, worker_id: str) -> Optional[WorkerInfo]:
        """Get worker information."""
        try:
            # Check local cache
            if worker_id in self.workers:
                return self.workers[worker_id]
            
            # Load from storage
            worker_data = await redis_manager.hgetall(f"worker:{worker_id}")
            if worker_data:
                worker_info = self._deserialize_worker_info(worker_data)
                self.workers[worker_id] = worker_info
                return worker_info
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get worker info {worker_id}: {e}")
            return None
    
    async def get_all_workers(self) -> Dict[str, WorkerInfo]:
        """Get all registered workers."""
        try:
            # Refresh from storage if needed
            await self._refresh_workers_from_storage()
            return self.workers.copy()
            
        except Exception as e:
            logger.error(f"Failed to get all workers: {e}")
            return {}
    
    async def find_suitable_workers(self, requirements: Dict[str, Any]) -> List[str]:
        """Find workers suitable for given requirements."""
        try:
            suitable_workers = []
            
            # Get all active workers
            all_workers = await self.get_all_workers()
            
            for worker_id, worker_info in all_workers.items():
                # Check worker state
                if worker_info.state not in [WorkerState.ACTIVE, WorkerState.BUSY]:
                    continue
                
                # Check capabilities match
                if self._check_capabilities_match(requirements, worker_info.capabilities):
                    suitable_workers.append(worker_id)
            
            # Sort deterministically
            suitable_workers.sort()
            
            return suitable_workers
            
        except Exception as e:
            logger.error(f"Failed to find suitable workers: {e}")
            return []
    
    async def find_workers_by_capability(self, capability: str, value: Any) -> List[str]:
        """Find workers with specific capability."""
        try:
            # Use capability index
            index_key = f"{self.capability_index_key}:{capability}:{value}"
            worker_ids = await redis_manager.smembers(index_key)
            
            # Filter active workers
            active_workers = []
            for worker_id in worker_ids:
                worker_info = await self.get_worker_info(worker_id)
                if worker_info and worker_info.state in [WorkerState.ACTIVE, WorkerState.BUSY]:
                    active_workers.append(worker_id)
            
            return sorted(active_workers)
            
        except Exception as e:
            logger.error(f"Failed to find workers by capability {capability}: {e}")
            return []
    
    async def get_worker_metrics(self, worker_id: str) -> Optional[WorkerMetrics]:
        """Get worker metrics."""
        try:
            worker_info = await self.get_worker_info(worker_id)
            if worker_info:
                return worker_info.metrics
            return None
            
        except Exception as e:
            logger.error(f"Failed to get worker metrics {worker_id}: {e}")
            return None
    
    async def update_worker_state(self, worker_id: str, new_state: WorkerState) -> bool:
        """Update worker state."""
        try:
            worker_info = await self.get_worker_info(worker_id)
            if not worker_info:
                logger.warning(f"Worker not found for state update: {worker_id}")
                return False
            
            # Validate state transition
            if not self._is_valid_state_transition(worker_info.state, new_state):
                logger.warning(f"Invalid state transition for {worker_id}: {worker_info.state} → {new_state}")
                return False
            
            # Update state
            worker_info.state = new_state
            await self._update_worker_state(worker_id, new_state)
            await self._store_worker(worker_info)
            
            logger.info(f"Worker state updated: {worker_id} → {new_state.value}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update worker state {worker_id}: {e}")
            return False
    
    async def get_registry_statistics(self) -> Dict[str, Any]:
        """Get registry statistics."""
        try:
            all_workers = await self.get_all_workers()
            
            stats = {
                "total_workers": len(all_workers),
                "workers_by_state": {},
                "workers_by_type": {},
                "total_capacity": 0,
                "active_capacity": 0,
                "average_load": 0.0,
                "average_health_score": 0.0,
                "last_updated": datetime.now(timezone.utc).isoformat()
            }
            
            total_load = 0.0
            total_health = 0.0
            worker_count = 0
            
            for worker_info in all_workers.values():
                # Count by state
                state_name = worker_info.state.value
                stats["workers_by_state"][state_name] = stats["workers_by_state"].get(state_name, 0) + 1
                
                # Count by type
                type_name = worker_info.worker_type.value
                stats["workers_by_type"][type_name] = stats["workers_by_type"].get(type_name, 0) + 1
                
                # Capacity calculations
                self.total_capacity += worker_info.capabilities.max_concurrent_jobs
                if worker_info.state in [WorkerState.ACTIVE, WorkerState.BUSY]:
                    self.total_capacity += worker_info.capabilities.max_concurrent_jobs
                
                # Load and health averages
                if worker_info.state in [WorkerState.ACTIVE, WorkerState.BUSY]:
                    total_load += worker_info.load_ratio
                    total_health += worker_info.health_score
                    worker_count += 1
            
            if worker_count > 0:
                stats["average_load"] = total_load / worker_count
                stats["average_health_score"] = total_health / worker_count
            
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get registry statistics: {e}")
            return {"error": str(e)}
    
    async def _store_worker(self, worker_info: WorkerInfo):
        """Store worker information in Redis."""
        try:
            worker_key = f"worker:{worker_info.worker_id}"
            worker_data = self._serialize_worker_info(worker_info)
            
            await redis_manager.hset(worker_key, mapping=worker_data)
            await redis_manager.expire(worker_key, self.worker_timeout)
            
            # Update local cache
            self.workers[worker_info.worker_id] = worker_info
            
        except Exception as e:
            logger.error(f"Failed to store worker {worker_info.worker_id}: {e}")
            raise
    
    async def _remove_worker(self, worker_id: str):
        """Remove worker from registry."""
        try:
            worker_key = f"worker:{worker_id}"
            await redis_manager.delete(worker_key)
            
            # Remove from local cache
            self.workers.pop(worker_id, None)
            
        except Exception as e:
            logger.error(f"Failed to remove worker {worker_id}: {e}")
            raise
    
    async def _update_worker_state(self, worker_id: str, state: WorkerState):
        """Update worker state in Redis."""
        try:
            worker_key = f"worker:{worker_id}"
            await redis_manager.hset(worker_key, "state", state.value)
            
        except Exception as e:
            logger.error(f"Failed to update worker state {worker_id}: {e}")
            raise
    
    async def _update_capability_index(self, worker_id: str, capabilities: WorkerCapabilities):
        """Update capability index for worker."""
        try:
            # Index exchanges
            for exchange in capabilities.exchanges:
                index_key = f"{self.capability_index_key}:exchanges:{exchange}"
                await redis_manager.sadd(index_key, worker_id)
            
            # Index order types
            for order_type in capabilities.order_types:
                index_key = f"{self.capability_index_key}:order_types:{order_type}"
                await redis_manager.sadd(index_key, worker_id)
            
            # Index specializations
            for specialization in capabilities.specializations:
                index_key = f"{self.capability_index_key}:specializations:{specialization}"
                await redis_manager.sadd(index_key, worker_id)
            
            # Index supported tasks
            for task in capabilities.supported_tasks:
                index_key = f"{self.capability_index_key}:supported_tasks:{task}"
                await redis_manager.sadd(index_key, worker_id)
            
            # Index performance tier
            index_key = f"{self.capability_index_key}:performance_tier:{capabilities.performance_tier}"
            await redis_manager.sadd(index_key, worker_id)
            
        except Exception as e:
            logger.error(f"Failed to update capability index for {worker_id}: {e}")
            raise
    
    async def _remove_from_capability_index(self, worker_id: str):
        """Remove worker from capability index."""
        try:
            worker_info = await self.get_worker_info(worker_id)
            if not worker_info:
                return
            
            capabilities = worker_info.capabilities
            
            # Remove from all capability indexes
            for exchange in capabilities.exchanges:
                index_key = f"{self.capability_index_key}:exchanges:{exchange}"
                await redis_manager.srem(index_key, worker_id)
            
            for order_type in capabilities.order_types:
                index_key = f"{self.capability_index_key}:order_types:{order_type}"
                await redis_manager.srem(index_key, worker_id)
            
            for specialization in capabilities.specializations:
                index_key = f"{self.capability_index_key}:specializations:{specialization}"
                await redis_manager.srem(index_key, worker_id)
            
            for task in capabilities.supported_tasks:
                index_key = f"{self.capability_index_key}:supported_tasks:{task}"
                await redis_manager.srem(index_key, worker_id)
            
            index_key = f"{self.capability_index_key}:performance_tier:{capabilities.performance_tier}"
            await redis_manager.srem(index_key, worker_id)
            
        except Exception as e:
            logger.error(f"Failed to remove {worker_id} from capability index: {e}")
            raise
    
    async def _update_worker_index(self, worker_id: str, worker_info: WorkerInfo):
        """Update worker index."""
        try:
            # Add to type index
            type_key = f"{self.worker_index_key}:type:{worker_info.worker_type.value}"
            await redis_manager.sadd(type_key, worker_id)
            
            # Add to state index
            state_key = f"{self.worker_index_key}:state:{worker_info.state.value}"
            await redis_manager.sadd(state_key, worker_id)
            
        except Exception as e:
            logger.error(f"Failed to update worker index for {worker_id}: {e}")
            raise
    
    async def _remove_from_worker_index(self, worker_id: str):
        """Remove worker from index."""
        try:
            worker_info = await self.get_worker_info(worker_id)
            if not worker_info:
                return
            
            # Remove from type index
            type_key = f"{self.worker_index_key}:type:{worker_info.worker_type.value}"
            await redis_manager.srem(type_key, worker_id)
            
            # Remove from state index
            state_key = f"{self.worker_index_key}:state:{worker_info.state.value}"
            await redis_manager.srem(state_key, worker_id)
            
        except Exception as e:
            logger.error(f"Failed to remove {worker_id} from worker index: {e}")
            raise
    
    def _serialize_worker_info(self, worker_info: WorkerInfo) -> Dict[str, str]:
        """Serialize worker info to dictionary."""
        return {
            "worker_id": worker_info.worker_id,
            "worker_type": worker_info.worker_type.value,
            "state": worker_info.state.value,
            "hostname": worker_info.hostname,
            "pid": str(worker_info.pid),
            "started_at": worker_info.started_at.isoformat(),
            "last_heartbeat": worker_info.last_heartbeat.isoformat(),
            "registration_time": worker_info.registration_time.isoformat(),
            "capabilities": json.dumps(worker_info.capabilities.__dict__),
            "metrics": json.dumps(worker_info.metrics.__dict__),
            "metadata": json.dumps(worker_info.metadata),
            "tags": json.dumps(list(worker_info.tags)),
            "load_ratio": str(worker_info.load_ratio),
            "health_score": str(worker_info.health_score)
        }
    
    def _deserialize_worker_info(self, data: Dict[str, str]) -> WorkerInfo:
        """Deserialize worker info from dictionary."""
        capabilities_dict = json.loads(data["capabilities"])
        metrics_dict = json.loads(data["metrics"])
        
        return WorkerInfo(
            worker_id=data["worker_id"],
            worker_type=WorkerType(data["worker_type"]),
            state=WorkerState(data["state"]),
            capabilities=WorkerCapabilities(**capabilities_dict),
            metrics=WorkerMetrics(**metrics_dict),
            hostname=data["hostname"],
            pid=int(data["pid"]),
            started_at=datetime.fromisoformat(data["started_at"]),
            last_heartbeat=datetime.fromisoformat(data["last_heartbeat"]),
            registration_time=datetime.fromisoformat(data["registration_time"]),
            metadata=json.loads(data["metadata"]),
            tags=set(json.loads(data["tags"])),
            load_ratio=float(data["load_ratio"]),
            health_score=float(data["health_score"])
        )
    
    def _calculate_health_score(self, worker_info: WorkerInfo) -> float:
        """Calculate worker health score."""
        try:
            health_score = 1.0
            
            # CPU utilization impact
            cpu_impact = 1.0 - (worker_info.metrics.cpu_utilization * 0.3)
            health_score *= cpu_impact
            
            # Memory utilization impact
            memory_impact = 1.0 - (worker_info.metrics.memory_utilization * 0.3)
            health_score *= memory_impact
            
            # Load ratio impact
            load_impact = 1.0 - (worker_info.load_ratio * 0.2)
            health_score *= load_impact
            
            # Failure rate impact
            total_jobs = worker_info.metrics.completed_jobs + worker_info.metrics.failed_jobs
            if total_jobs > 0:
                failure_rate = worker_info.metrics.failed_jobs / total_jobs
                failure_impact = 1.0 - (failure_rate * 0.2)
                health_score *= failure_impact
            
            return max(0.0, min(1.0, health_score))
            
        except Exception as e:
            logger.error(f"Failed to calculate health score: {e}")
            return 0.0
    
    def _check_capabilities_match(self, requirements: Dict[str, Any], capabilities: WorkerCapabilities) -> bool:
        """Check if worker capabilities match requirements."""
        try:
            # Check exchanges
            if "exchanges" in requirements:
                required_exchanges = set(requirements["exchanges"])
                worker_exchanges = set(capabilities.exchanges)
                if not required_exchanges.issubset(worker_exchanges):
                    return False
            
            # Check order types
            if "order_types" in requirements:
                required_order_types = set(requirements["order_types"])
                worker_order_types = set(capabilities.order_types)
                if not required_order_types.issubset(worker_order_types):
                    return False
            
            # Check specializations
            if "specializations" in requirements:
                required_specializations = set(requirements["specializations"])
                worker_specializations = set(capabilities.specializations)
                if not required_specializations.issubset(worker_specializations):
                    return False
            
            # Check supported tasks
            if "supported_tasks" in requirements:
                required_tasks = set(requirements["supported_tasks"])
                worker_tasks = set(capabilities.supported_tasks)
                if not required_tasks.issubset(worker_tasks):
                    return False
            
            # Check performance requirements
            if "max_concurrent_jobs" in requirements:
                if capabilities.max_concurrent_jobs < requirements["max_concurrent_jobs"]:
                    return False
            
            if "memory_mb" in requirements:
                if capabilities.memory_mb < requirements["memory_mb"]:
                    return False
            
            if "cpu_cores" in requirements:
                if capabilities.cpu_cores < requirements["cpu_cores"]:
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to check capabilities match: {e}")
            return False
    
    def _is_valid_state_transition(self, current_state: WorkerState, new_state: WorkerState) -> bool:
        """Check if state transition is valid."""
        valid_transitions = {
            WorkerState.REGISTERING: [WorkerState.ACTIVE, WorkerState.FAILED],
            WorkerState.ACTIVE: [WorkerState.BUSY, WorkerState.MAINTENANCE, WorkerState.DRAINING, WorkerState.SHUTDOWN],
            WorkerState.BUSY: [WorkerState.ACTIVE, WorkerState.FAILED, WorkerState.SHUTDOWN],
            WorkerState.MAINTENANCE: [WorkerState.ACTIVE, WorkerState.SHUTDOWN],
            WorkerState.DRAINING: [WorkerState.ACTIVE, WorkerState.SHUTDOWN],
            WorkerState.FAILED: [WorkerState.SHUTDOWN],
            WorkerState.SHUTDOWN: []  # Terminal state
        }
        
        return new_state in valid_transitions.get(current_state, [])
    
    async def _load_workers_from_storage(self):
        """Load existing workers from Redis storage."""
        try:
            # Get all worker keys
            worker_keys = await redis_manager.keys("worker:*")
            
            for worker_key in worker_keys:
                worker_data = await redis_manager.hgetall(worker_key)
                if worker_data:
                    worker_info = self._deserialize_worker_info(worker_data)
                    self.workers[worker_info.worker_id] = worker_info
            
            logger.info(f"Loaded {len(self.workers)} workers from storage")
            
        except Exception as e:
            logger.error(f"Failed to load workers from storage: {e}")
    
    async def _refresh_workers_from_storage(self):
        """Refresh workers from storage if needed."""
        try:
            # This could be optimized to only load updated workers
            await self._load_workers_from_storage()
            
        except Exception as e:
            logger.error(f"Failed to refresh workers from storage: {e}")
    
    async def _persist_registry_state(self):
        """Persist registry state to storage."""
        try:
            # Workers are already persisted individually
            # This could be extended to persist registry metadata
            registry_metadata = {
                "last_persisted": datetime.now(timezone.utc).isoformat(),
                "total_workers": len(self.workers)
            }
            
            await redis_manager.set("registry_metadata", json.dumps(registry_metadata))
            
        except Exception as e:
            logger.error(f"Failed to persist registry state: {e}")
    
    async def _update_worker_registration(self, worker_info: WorkerInfo) -> bool:
        """Update existing worker registration."""
        try:
            # Update registration time
            worker_info.registration_time = datetime.now(timezone.utc)
            worker_info.last_heartbeat = datetime.now(timezone.utc)
            
            # Store updated info
            await self._store_worker(worker_info)
            
            logger.info(f"Worker registration updated: {worker_info.worker_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update worker registration: {e}")
            return False
    
    async def _health_check_loop(self):
        """Background health check loop."""
        while self.running:
            try:
                await self._perform_health_checks()
                await asyncio.sleep(self.health_check_interval)
                
            except Exception as e:
                logger.error(f"Health check loop error: {e}")
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
    
    async def _perform_health_checks(self):
        """Perform health checks on all workers."""
        try:
            current_time = datetime.now(timezone.utc)
            workers_to_check = list(self.workers.values())
            
            for worker_info in workers_to_check:
                # Check heartbeat timeout
                time_since_heartbeat = (current_time - worker_info.last_heartbeat).total_seconds()
                
                if time_since_heartbeat > self.worker_timeout:
                    logger.warning(f"Worker heartbeat timeout: {worker_info.worker_id}")
                    
                    # Mark as failed
                    await self.update_worker_state(worker_info.worker_id, WorkerState.FAILED)
            
        except Exception as e:
            logger.error(f"Failed to perform health checks: {e}")
    
    async def _perform_cleanup(self):
        """Perform cleanup operations."""
        try:
            # Remove expired workers from local cache
            current_time = datetime.now(timezone.utc())
            expired_workers = []
            
            for worker_id, worker_info in self.workers.items():
                if worker_info.state == WorkerState.SHUTDOWN:
                    # Remove shutdown workers after timeout
                    time_since_shutdown = (current_time - worker_info.last_heartbeat).total_seconds()
                    if time_since_shutdown > self.worker_timeout:
                        expired_workers.append(worker_id)
            
            for worker_id in expired_workers:
                await self.unregister_worker(worker_id, "cleanup")
            
            if expired_workers:
                logger.info(f"Cleaned up {len(expired_workers)} expired workers")
            
        except Exception as e:
            logger.error(f"Failed to perform cleanup: {e}")


# Global worker registry instance
worker_registry = WorkerRegistry()


class DeterministicReassignmentCoordinator:
    """Coordinates deterministic reassignment of orphaned workflows."""
    pass
