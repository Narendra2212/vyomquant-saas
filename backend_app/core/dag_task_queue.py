import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID

from backend_app.core.cache import redis_manager
from backend_app.core.database import SessionLocal
from backend_app.core.hard_quota_enforcer import HardQuotaEnforcer
from backend_app.core.models.dag_task import DAGTaskCreate, DAGTaskRepository
from backend_app.core.tenant import TenantContext

"""
core/dag_task_queue.py — DAG Task Queue System.

Isolated task execution for DAG sessions with:
  - Per-user task queues with priority support
  - Maximum concurrent execution per tenant
  - Task isolation and sandboxing
  - Progress tracking and status updates
  - Automatic retries with exponential backoff
  - Task cancellation and cleanup
  - Dead letter queue for failed tasks

Architecture:
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │                         DAG TASK QUEUE SYSTEM                              │
  ├─────────────────────────────────────────────────────────────────────────────┤
  │                                                                              │
│  INCOMING TASKS                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │  POST /api/dag/execute                                               │    │
│  │  ├── Validate DAG config                                            │    │
│  │  ├── Check user quota (hard enforcement)                           │    │
│  │  └── Create Task                                                    │    │
│  │      ├── task_id: uuid                                             │    │
│  │      ├── tenant_id: user_123                                       │    │
│  │      ├── priority: 1-10 (default: 5)                                 │    │
│  │      ├── dag_config: {...}                                         │    │
│  │      └── status: PENDING                                           │    │
│  └──────────────────────────────────┬───────────────────────────────────┘    │
│                                     │                                        │
│                                     ▼                                        │
│  QUEUE ASSIGNMENT                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │  Redis: task_queue:{tenant_id} (Sorted Set)                          │    │
│  │                                                                      │    │
│  │  Score = priority * 1000000000 + timestamp                           │    │
│  │  (Higher priority = lower score = processed first)                   │    │
│  │                                                                      │    │
│  │  Alternative: Global queue with tenant round-robin                     │    │
│  │  (Ensures fair scheduling across tenants)                            │    │
│  └──────────────────────────────────┬───────────────────────────────────┘    │
│                                     │                                        │
│                                     ▼                                        │
│  WORKER POOL                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │  DAGWorker (Celery/Asyncio)                                        │    │
│  │                                                                      │    │
│  │  Per-Tenant Concurrency Control:                                     │    │
│  │  ┌────────────────────────────────────────────────────────────────┐ │    │
│  │  │  Check: user:{tenant_id}:dag:tasks:active < max_concurrent    │ │    │
│  │  │                                                                  │ │    │
│  │  │  If OK:                                                           │ │    │
│  │  │  1. Add to active set (SADD)                                     │ │    │
│  │  │  2. Acquire execution slot (SEMAPHORE)                          │ │    │
│  │  │  3. Execute DAG in isolated task                                │ │    │
│  │  │  4. Remove from active set (SREM)                              │ │    │
│  │  │                                                                  │ │    │
│  │  │  If FULL: Task stays in queue, wait for slot                    │ │    │
│  │  └────────────────────────────────────────────────────────────────┘ │    │
│  │                                                                      │    │
│  │  Task Lifecycle:                                                     │    │
│  │  PENDING → ASSIGNED → RUNNING → COMPLETED/FAILED/CANCELLED          │    │
│  └──────────────────────────────────┬───────────────────────────────────┘    │
│                                     │                                        │
│                                     ▼                                        │
│  STATUS & MONITORING                                                         │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │  Redis: task:{task_id}:status                                        │    │
│  │                                                                      │    │
│  │  task:{task_id}:progress  → Progress updates (0-100%)                │    │
│  │  task:{task_id}:results   → Execution results                        │    │
│  │  task:{task_id}:logs      → Execution logs                             │    │
│  │  task:{task_id}:metrics   → Performance metrics                        │    │
│  └───────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  SPECIAL QUEUES                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │  Dead Letter Queue: task_queue:dead_letter                           │    │
│  │  - Failed tasks after max retries                                     │    │
│  │  - Manual inspection and replay possible                            │    │
│  │                                                                      │    │
│  │  Scheduled Queue: task_queue:scheduled                             │    │
│  │  - Tasks with delayed execution                                       │    │
│  │  - Cron-based recurring tasks                                         │    │
│  └───────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
"""



logger = logging.getLogger("DAGTaskQueue")


# ═══════════════════════════════════════════════════════════════════════════
# TASK DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

class TaskStatus(Enum):
    """Task lifecycle statuses."""
    PENDING = "PENDING"           # Waiting in queue
    ASSIGNED = "ASSIGNED"         # Assigned to worker
    RUNNING = "RUNNING"           # Currently executing
    COMPLETED = "COMPLETED"       # Successfully finished
    FAILED = "FAILED"             # Execution failed
    CANCELLED = "CANCELLED"       # Manually cancelled
    TIMEOUT = "timeout"           # Execution timed out
    DEAD_LETTER = "dead_letter"   # Max retries exceeded


class TaskPriority(Enum):
    """Task priority levels."""
    CRITICAL = 1    # Immediate execution
    HIGH = 3        # Above normal
    NORMAL = 5      # Default
    LOW = 7         # Below normal
    BACKGROUND = 10 # When resources available


@dataclass
class DAGTask:
    """DAG execution task definition."""
    task_id: str
    tenant_id: str
    dag_config: Dict[str, Any]
    
    # Scheduling
    priority: int = 5
    created_at: datetime = field(default_factory=datetime.utcnow)
    scheduled_for: Optional[datetime] = None
    
    # Execution
    status: TaskStatus = TaskStatus.PENDING
    worker_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Retry logic
    retry_count: int = 0
    max_retries: int = 3
    
    # Context
    request_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    
    # Results
    result: Optional[Dict] = None
    error: Optional[str] = None
    execution_time_ms: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "dag_config": self.dag_config,
            "priority": self.priority,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "status": self.status.value,
            "worker_id": self.worker_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "request_id": self.request_id,
            "result": self.result,
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DAGTask":
        return cls(
            task_id=data["task_id"],
            tenant_id=data["tenant_id"],
            dag_config=data["dag_config"],
            priority=data.get("priority", 5),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.utcnow(),
            scheduled_for=datetime.fromisoformat(data["scheduled_for"]) if data.get("scheduled_for") else None,
            status=TaskStatus(data.get("status", "pending")),
            worker_id=data.get("worker_id"),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 3),
            request_id=data.get("request_id"),
            ip_address=data.get("ip_address"),
            user_agent=data.get("user_agent"),
            result=data.get("result"),
            error=data.get("error"),
            execution_time_ms=data.get("execution_time_ms"),
        )


@dataclass
class TaskQueueStats:
    """Statistics for a task queue."""
    tenant_id: str
    pending_count: int
    running_count: int
    completed_count: int
    failed_count: int
    dead_letter_count: int
    avg_execution_time_ms: float
    max_concurrent: int
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════
# REDIS KEY BUILDERS
# ═══════════════════════════════════════════════════════════════════════════

class TaskQueueKeyBuilder:
    """Generate Redis keys for task queue system."""
    
    @staticmethod
    def task_queue(tenant_id: str) -> str:
        """Sorted set of pending tasks per tenant."""
        return f"user:{tenant_id}:dag:task_queue"
    
    @staticmethod
    def global_task_queue() -> str:
        """Global queue for fair scheduling."""
        return "dag:global_task_queue"
    
    @staticmethod
    def task_data(task_id: str) -> str:
        """Hash with full task data."""
        return f"task:{task_id}:data"
    
    @staticmethod
    def task_status(task_id: str) -> str:
        """String with current status."""
        return f"task:{task_id}:status"
    
    @staticmethod
    def task_progress(task_id: str) -> str:
        """String with progress (0-100)."""
        return f"task:{task_id}:progress"
    
    @staticmethod
    def task_result(task_id: str) -> str:
        """Hash with execution results."""
        return f"task:{task_id}:result"
    
    @staticmethod
    def task_logs(task_id: str) -> str:
        """List with execution logs."""
        return f"task:{task_id}:logs"
    
    @staticmethod
    def active_tasks(tenant_id: str) -> str:
        """Set of currently running task IDs."""
        return f"user:{tenant_id}:dag:tasks:active"
    
    @staticmethod
    def task_history(tenant_id: str) -> str:
        """List of completed task IDs."""
        return f"user:{tenant_id}:dag:tasks:history"
    
    @staticmethod
    def dead_letter_queue() -> str:
        """List of failed tasks after max retries."""
        return "dag:task_queue:dead_letter"
    
    @staticmethod
    def scheduled_queue() -> str:
        """Sorted set of scheduled tasks."""
        return "dag:task_queue:scheduled"
    
    @staticmethod
    def concurrency_semaphore(tenant_id: str) -> str:
        """Semaphore for limiting concurrent execution."""
        return f"user:{tenant_id}:dag:semaphore"
    
    @staticmethod
    def worker_registry() -> str:
        """Set of active workers."""
        return "dag:workers:registry"
    
    @staticmethod
    def task_cancel_signal(task_id: str) -> str:
        """Cancellation signal for running tasks."""
        return f"task:{task_id}:cancel"
    
    @staticmethod
    def worker_tasks(worker_id: str) -> str:
        """Set of tasks assigned to worker."""
        return f"dag:worker:{worker_id}:tasks"


# ═══════════════════════════════════════════════════════════════════════════
# TASK QUEUE MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class DAGTaskQueueManager:
    """
    Manages DAG task queues with per-user isolation.
    
    Features:
    - Per-tenant task queues
    - Priority-based scheduling
    - Concurrent execution limits
    - Task lifecycle management
    """
    
    def __init__(
        self,
        max_concurrent_per_tenant: int = 3,
        global_max_concurrent: int = 100,
        task_timeout_seconds: float = 300.0
    ):
        self.max_concurrent_per_tenant = max_concurrent_per_tenant
        self.global_max_concurrent = global_max_concurrent
        self.task_timeout_seconds = task_timeout_seconds
        self.enforcer = HardQuotaEnforcer()
        
        # Track local state
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._shutdown_event = asyncio.Event()
    
    async def submit_task(
        self,
        tenant: TenantContext,
        dag_config: Dict[str, Any],
        priority: int = 5,
        scheduled_for: Optional[datetime] = None,
        request_id: Optional[str] = None,
        db_session = None
    ) -> DAGTask:
        """
        Submit a DAG execution task to the queue using DUAL-WRITE pattern.
        
        DUAL-WRITE PROCESS:
        1. Insert task into PostgreSQL (source of truth)
        2. Push task_id into Redis queue (for workers)
        3. If Redis fails → rollback DB transaction (no task lost)
        4. If DB fails → no Redis write (consistency maintained)
        
        Args:
            tenant: Tenant context for isolation
            dag_config: DAG configuration
            priority: Task priority (1-10, lower = higher priority)
            scheduled_for: Optional scheduled execution time
            request_id: Request tracking ID
            db_session: Optional database session (will create if not provided)
        
        Returns:
            DAGTask with task_id from PostgreSQL
            
        Raises:
            RuntimeError: If dual-write fails (task not lost, in DB but not queued)
        """
        from uuid import UUID

        from backend_app.core.database import SessionLocal

        # Create local session if not provided
        local_session = False
        if db_session is None:
            db_session = SessionLocal()
            local_session = True
        
        task_id = None
        task = None
        db_task = None
        
        try:
            # ═══════════════════════════════════════════════════════════════
            # STEP 1: PERSIST TO POSTGRESQL (SOURCE OF TRUTH)
            # ═══════════════════════════════════════════════════════════════
            
            repo = DAGTaskRepository(db_session)
            
            # Create task in database
            task_create = DAGTaskCreate(
                dag_config=dag_config,
                priority=priority,
                max_retries=3,
                scheduled_for=scheduled_for
            )
            
            db_task = repo.create(
                task=task_create,
                tenant_id=UUID(tenant.user_id)
            )
            
            # Get task_id from database (UUID)
            task_id = str(db_task.task_id)
            
            # Log successful persistence
            logger.info(
                f"TASK_PERSISTED: task_id={task_id} "
                f"tenant={tenant.user_id} "
                f"priority={priority} "
                f"status={db_task.status.value}",
                extra={
                    "event": "TASK_PERSISTED",
                    "task_id": task_id,
                    "tenant_id": tenant.user_id,
                    "priority": priority,
                    "request_id": request_id,
                }
            )
            
            # ═══════════════════════════════════════════════════════════════
            # STEP 2: ENQUEUE TO REDIS (FOR WORKER PROCESSING)
            # ═══════════════════════════════════════════════════════════════
            
            # Create in-memory task object for Redis
            task = DAGTask(
                task_id=task_id,
                tenant_id=tenant.user_id,
                dag_config=dag_config,
                priority=priority,
                scheduled_for=scheduled_for,
                request_id=request_id,
                ip_address=tenant.ip_address,
                user_agent=tenant.user_agent,
                status=TaskStatus(db_task.status.value),
                retry_count=db_task.retry_count,
                max_retries=db_task.max_retries,
                created_at=db_task.created_at,
            )
            
            # Store task metadata in Redis
            await self._store_task(task)
            
            # Add to appropriate queue
            if scheduled_for and scheduled_for > datetime.utcnow():
                # Future scheduled task
                await self._add_to_scheduled_queue(task)
                queue_type = "scheduled"
            else:
                # Immediate queue
                await self._add_to_tenant_queue(task)
                queue_type = "immediate"
            
            # Commit database transaction (now that Redis succeeded)
            if local_session:
                db_session.commit()
            
            # Log successful enqueue
            logger.info(
                f"TASK_ENQUEUED: task_id={task_id} "
                f"queue={queue_type} "
                f"tenant={tenant.user_id} "
                f"priority={priority}",
                extra={
                    "event": "TASK_ENQUEUED",
                    "task_id": task_id,
                    "tenant_id": tenant.user_id,
                    "queue_type": queue_type,
                    "priority": priority,
                    "request_id": request_id,
                }
            )
            
            return task
            
        except Exception as e:
            # ═══════════════════════════════════════════════════════════════
            # ERROR HANDLING: ROLLBACK ON FAILURE
            # ═══════════════════════════════════════════════════════════════
            
            # Rollback database transaction
            if local_session and db_session:
                db_session.rollback()
            
            # Determine failure type
            if task_id is None:
                # Failed during DB insert
                logger.error(
                    f"TASK_DB_INSERT_FAILED: tenant={tenant.user_id} "
                    f"error={str(e)}",
                    extra={
                        "event": "TASK_DB_INSERT_FAILED",
                        "tenant_id": tenant.user_id,
                        "error": str(e),
                        "request_id": request_id,
                    }
                )
                raise RuntimeError(f"Failed to persist task to database: {e}")
            
            else:
                # Failed during Redis enqueue (after DB insert succeeded)
                logger.error(
                    f"TASK_REDIS_ENQUEUE_FAILED: task_id={task_id} "
                    f"tenant={tenant.user_id} "
                    f"error={str(e)}",
                    extra={
                        "event": "TASK_REDIS_ENQUEUE_FAILED",
                        "task_id": task_id,
                        "tenant_id": tenant.user_id,
                        "error": str(e),
                        "request_id": request_id,
                    }
                )
                
                # Task is in DB but not in queue - will be picked up by recovery job
                # Mark task status as 'failed' in DB for visibility
                if db_task:
                    try:
                        db_task.status = "failed"
                        db_task.error = f"Redis enqueue failed: {str(e)}"
                        if local_session:
                            db_session.commit()
                    except Exception as cleanup_error:
                        logger.warning(
                            f"Failed to mark task as failed in DB: {cleanup_error}"
                        )
                
                raise RuntimeError(
                    f"Task persisted to DB but failed to enqueue: {e}. "
                    f"Task ID: {task_id}. Will be recovered by cleanup job."
                )
        
        finally:
            # Cleanup local session
            if local_session and db_session:
                db_session.close()
    
    async def _store_task(self, task: DAGTask):
        """Store task data in Redis."""
        key = TaskQueueKeyBuilder.task_data(task.task_id)
        await redis_manager.hset(key, mapping={
            "data": json.dumps(task.to_dict()),
            "tenant_id": task.tenant_id,
        })
        await redis_manager.expire(key, 86400 * 7)  # 7 days
        
        # Set initial status
        status_key = TaskQueueKeyBuilder.task_status(task.task_id)
        await redis_manager.set(status_key, task.status.value)
        await redis_manager.expire(status_key, 86400 * 7)
    
    async def _add_to_tenant_queue(self, task: DAGTask):
        """Add task to tenant-specific queue."""
        key = TaskQueueKeyBuilder.task_queue(task.tenant_id)
        
        # Score = priority * 1000000000 + timestamp
        # This ensures higher priority (lower number) is processed first
        # Within same priority, earlier submission wins
        score = task.priority * 1_000_000_000_000 + int(task.created_at.timestamp() * 1000)
        
        await redis_manager.zadd(key, {task.task_id: score})
        await redis_manager.expire(key, 86400)  # 1 day
    
    async def _add_to_scheduled_queue(self, task: DAGTask):
        """Add task to scheduled queue for future execution."""
        key = TaskQueueKeyBuilder.scheduled_queue()
        score = int(task.scheduled_for.timestamp())
        
        await redis_manager.zadd(key, {task.task_id: score})
        
        # Update status
        status_key = TaskQueueKeyBuilder.task_status(task.task_id)
        await redis_manager.set(status_key, "scheduled")
    
    async def claim_task(
        self,
        worker_id: str,
        tenant_id: Optional[str] = None
    ) -> Optional[DAGTask]:
        """
        Claim next available task for execution.
        
        Args:
            worker_id: Worker claiming the task
            tenant_id: Optional specific tenant to claim from
        
        Returns:
            DAGTask if available, None otherwise
        """
        # Check if we should process scheduled tasks first
        scheduled_task = await self._get_scheduled_task()
        if scheduled_task:
            task = scheduled_task
        elif tenant_id:
            # Claim from specific tenant queue
            task = await self._claim_from_tenant_queue(tenant_id, worker_id)
        else:
            # Fair scheduling: round-robin through tenants
            task = await self._claim_fair(worker_id)
        
        if not task:
            return None
        
        # Check concurrency limit for tenant
        if not await self._acquire_execution_slot(task.tenant_id):
            # Tenant at max concurrency, put back in queue
            await self._add_to_tenant_queue(task)
            return None
        
        # Mark as assigned
        task.status = TaskStatus.ASSIGNED
        task.worker_id = worker_id
        task.started_at = datetime.utcnow()
        
        # Update storage
        await self._update_task_status(task)
        await redis_manager.sadd(
            TaskQueueKeyBuilder.active_tasks(task.tenant_id),
            task.task_id
        )
        await redis_manager.sadd(
            TaskQueueKeyBuilder.worker_tasks(worker_id),
            task.task_id
        )
        
        logger.info(f"Task claimed: {task.task_id} by worker {worker_id}")
        
        return task
    
    async def _get_scheduled_task(self) -> Optional[DAGTask]:
        """Get any scheduled task that should run now."""
        key = TaskQueueKeyBuilder.scheduled_queue()
        now = int(datetime.utcnow().timestamp())
        
        # Get tasks scheduled for now or earlier
        task_ids = await redis_manager.zrangebyscore(key, 0, now, count=1)
        
        if not task_ids:
            return None
        
        task_id = task_ids[0]
        
        # Remove from scheduled queue
        await redis_manager.zrem(key, task_id)
        
        # Load task
        return await self._load_task(task_id)
    
    async def _claim_from_tenant_queue(
        self,
        tenant_id: str,
        worker_id: str
    ) -> Optional[DAGTask]:
        """Claim next task from specific tenant queue."""
        key = TaskQueueKeyBuilder.task_queue(tenant_id)
        
        # Get highest priority task (lowest score)
        task_ids = await redis_manager.zrange(key, 0, 0)
        
        if not task_ids:
            return None
        
        task_id = task_ids[0]
        
        # Remove from queue (atomic)
        removed = await redis_manager.zrem(key, task_id)
        if not removed:
            # Race condition: another worker claimed it
            return None
        
        # Load task
        return await self._load_task(task_id)
    
    async def _claim_fair(self, worker_id: str) -> Optional[DAGTask]:
        """Claim next task using fair scheduling across tenants."""
        # Get all tenant queues
        pattern = "user:*:dag:task_queue"
        queue_keys = await redis_manager.keys(pattern)
        
        if not queue_keys:
            return None
        
        # Round-robin: find tenant with tasks and lowest active count
        best_tenant = None
        best_task = None
        best_load = float('inf')
        
        for queue_key in queue_keys:
            # Extract tenant_id from key
            parts = queue_key.split(":")
            if len(parts) >= 3:
                tenant_id = parts[1]
                
                # Check if queue has tasks
                task_ids = await redis_manager.zrange(queue_key, 0, 0)
                if not task_ids:
                    continue
                
                # Check active load
                active_count = await redis_manager.scard(
                    TaskQueueKeyBuilder.active_tasks(tenant_id)
                )
                
                # Prefer tenants with lower active load
                if active_count < best_load:
                    best_load = active_count
                    best_tenant = tenant_id
                    best_task = task_ids[0]
        
        if not best_tenant or not best_task:
            return None
        
        # Remove from queue
        key = TaskQueueKeyBuilder.task_queue(best_tenant)
        removed = await redis_manager.zrem(key, best_task)
        if not removed:
            return None
        
        return await self._load_task(best_task)
    
    async def _load_task(self, task_id: str) -> Optional[DAGTask]:
        """Load task from Redis."""
        key = TaskQueueKeyBuilder.task_data(task_id)
        data = await redis_manager.hget(key, "data")
        
        if not data:
            return None
        
        try:
            task_dict = json.loads(data)
            return DAGTask.from_dict(task_dict)
        except Exception as e:
            logger.error(f"Failed to load task {task_id}: {e}")
            return None
    
    async def _acquire_execution_slot(self, tenant_id: str) -> bool:
        """Try to acquire execution slot for tenant."""
        active_key = TaskQueueKeyBuilder.active_tasks(tenant_id)
        active_count = await redis_manager.scard(active_key)
        
        if active_count >= self.max_concurrent_per_tenant:
            return False
        
        return True
    
    async def _update_task_status(self, task: DAGTask):
        """Update task status in Redis."""
        key = TaskQueueKeyBuilder.task_data(task.task_id)
        await redis_manager.hset(key, "data", json.dumps(task.to_dict()))
        
        status_key = TaskQueueKeyBuilder.task_status(task.task_id)
        await redis_manager.set(status_key, task.status.value)
    
    async def complete_task(
        self,
        task_id: str,
        result: Dict[str, Any],
        execution_time_ms: float
    ):
        """Mark task as completed."""
        task = await self._load_task(task_id)
        if not task:
            logger.warning(f"Complete called for unknown task: {task_id}")
            return
        
        task.status = TaskStatus.COMPLETED
        task.completed_at = datetime.utcnow()
        task.result = result
        task.execution_time_ms = execution_time_ms
        
        # Update storage
        await self._update_task_status(task)
        
        # Store result
        result_key = TaskQueueKeyBuilder.task_result(task_id)
        await redis_manager.hset(result_key, mapping={
            "result": json.dumps(result),
            "execution_time_ms": execution_time_ms,
        })
        
        # Cleanup active tracking
        await self._cleanup_active_task(task)
        
        # Add to history
        await redis_manager.lpush(
            TaskQueueKeyBuilder.task_history(task.tenant_id),
            task_id
        )
        await redis_manager.ltrim(
            TaskQueueKeyBuilder.task_history(task.tenant_id),
            0,
            999  # Keep last 1000
        )
        
        logger.info(
            f"Task completed: {task_id} "
            f"({execution_time_ms:.0f}ms)"
        )
    
    async def fail_task(
        self,
        task_id: str,
        error: str,
        execution_time_ms: Optional[float] = None
    ):
        """Mark task as failed with retry logic."""
        task = await self._load_task(task_id)
        if not task:
            return
        
        task.error = error
        task.execution_time_ms = execution_time_ms
        task.retry_count += 1
        
        if task.retry_count < task.max_retries:
            # Retry with exponential backoff
            backoff_seconds = 2 ** task.retry_count
            task.scheduled_for = datetime.utcnow() + timedelta(seconds=backoff_seconds)
            task.status = TaskStatus.PENDING
            
            await self._update_task_status(task)
            await self._add_to_scheduled_queue(task)
            
            logger.warning(
                f"Task failed, will retry: {task_id} "
                f"(attempt {task.retry_count}/{task.max_retries}, "
                f"backoff: {backoff_seconds}s)"
            )
        else:
            # Max retries exceeded - dead letter
            task.status = TaskStatus.DEAD_LETTER
            task.completed_at = datetime.utcnow()
            
            await self._update_task_status(task)
            await self._cleanup_active_task(task)
            
            # Add to dead letter queue
            await redis_manager.lpush(
                TaskQueueKeyBuilder.dead_letter_queue(),
                json.dumps(task.to_dict())
            )
            
            logger.error(
                f"Task failed permanently: {task_id} "
                f"(max retries exceeded, moved to dead letter)"
            )
    
    async def cancel_task(
        self, 
        task_id: str, 
        tenant_id: str,
        reason: str = "user_requested"
    ) -> bool:
        """
        Cancel a pending or running task.
        
        Flow:
        1. Load task from Redis
        2. Update PostgreSQL: status = 'cancelled'
        3. If PENDING: remove from Redis queue
        4. If RUNNING: signal worker cancellation
        5. Clean up Redis tracking
        
        Returns:
            True if cancelled, False if not found or already completed
        """
        # Load task from Redis
        task = await self._load_task(task_id)
        if not task or task.tenant_id != tenant_id:
            logger.warning(f"CANCEL_FAILED: task_id={task_id} not found or wrong tenant")
            return False
        
        # Check if already in terminal state
        if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
            logger.info(f"CANCEL_SKIPPED: task_id={task_id} already {task.status.value}")
            return False
        
        # ═══════════════════════════════════════════════════════════════
        # STEP 1: Update PostgreSQL (source of truth) FIRST
        # ═══════════════════════════════════════════════════════════════
        db_session = SessionLocal()
        try:
            from backend_app.core.models.dag_task import DAGTaskRepository
            from backend_app.core.models.dag_task import \
                TaskStatus as DBTaskStatus
            
            repo = DAGTaskRepository(db_session)
            now = datetime.utcnow()
            
            # Update status to cancelled in DB
            repo.update_status(
                UUID(task_id),
                UUID(tenant_id),
                DBTaskStatus.CANCELLED,
                error=f"Cancelled: {reason}",
                completed_at=now,
                progress=0.0
            )
            
            logger.info(
                f"TASK_CANCELLED_DB: task_id={task_id} status=cancelled source=postgresql",
                extra={
                    "event": "TASK_CANCELLED_DB",
                    "task_id": task_id,
                    "tenant_id": tenant_id,
                    "old_status": task.status.value,
                    "new_status": "cancelled",
                    "reason": reason,
                }
            )
        except Exception as e:
            logger.error(f"CANCEL_DB_ERROR: task_id={task_id} error={str(e)}")
            db_session.rollback()
            return False
        finally:
            db_session.close()
        
        # ═══════════════════════════════════════════════════════════════
        # STEP 2: Handle Redis based on current status
        # ═══════════════════════════════════════════════════════════════
        if task.status == TaskStatus.PENDING:
            # Remove from pending queue
            queue_key = TaskQueueKeyBuilder.task_queue(tenant_id)
            removed = await redis_manager.zrem(queue_key, task_id)
            
            logger.info(
                f"TASK_REMOVED_FROM_QUEUE: task_id={task_id} removed={removed}",
                extra={
                    "event": "TASK_REMOVED_FROM_QUEUE",
                    "task_id": task_id,
                    "queue": queue_key,
                    "removed": removed,
                }
            )
        
        elif task.status == TaskStatus.RUNNING:
            # Signal cancellation to worker
            cancel_key = TaskQueueKeyBuilder.task_cancel_signal(task_id)
            await redis_manager.setex(
                cancel_key,
                60,  # TTL: 60 seconds
                json.dumps({
                    "cancelled_at": datetime.utcnow().isoformat(),
                    "reason": reason,
                    "by": "user"
                })
            )
            
            logger.info(
                f"TASK_CANCEL_SIGNALLED: task_id={task_id} worker_id={task.worker_id}",
                extra={
                    "event": "TASK_CANCEL_SIGNALLED",
                    "task_id": task_id,
                    "worker_id": task.worker_id,
                    "reason": reason,
                }
            )
        
        # ═══════════════════════════════════════════════════════════════
        # STEP 3: Update Redis status and clean up
        # ═══════════════════════════════════════════════════════════════
        # Update task status in Redis
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.utcnow()
        await self._update_task_status(task)
        
        # Clean up active tracking
        await self._cleanup_active_task(task)
        
        # Add to history
        history_key = TaskQueueKeyBuilder.task_history(tenant_id)
        await redis_manager.lpush(history_key, task_id)
        await redis_manager.ltrim(history_key, 0, 999)  # Keep last 1000
        
        logger.info(
            f"TASK_CANCELLED: task_id={task_id} tenant_id={tenant_id} reason={reason}",
            extra={
                "event": "TASK_CANCELLED",
                "task_id": task_id,
                "tenant_id": tenant_id,
                "old_status": task.status.value if hasattr(task, 'status') else "unknown",
                "reason": reason,
            }
        )
        
        return True
    
    async def _cleanup_active_task(self, task: DAGTask):
        """Cleanup active task tracking."""
        if task.worker_id:
            await redis_manager.srem(
                TaskQueueKeyBuilder.worker_tasks(task.worker_id),
                task.task_id
            )
        
        await redis_manager.srem(
            TaskQueueKeyBuilder.active_tasks(task.tenant_id),
            task.task_id
        )
    
    async def get_task_status(self, task_id: str) -> Optional[Dict]:
        """Get current task status."""
        status_key = TaskQueueKeyBuilder.task_status(task_id)
        status = await redis_manager.get(status_key)
        
        if not status:
            return None
        
        # Load full task data
        task = await self._load_task(task_id)
        if not task:
            return {"status": status}
        
        return {
            "task_id": task_id,
            "status": status,
            "progress": await redis_manager.get(
                TaskQueueKeyBuilder.task_progress(task_id)
            ),
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "retry_count": task.retry_count,
        }
    
    async def update_task_progress(self, task_id: str, progress: float, message: str = ""):
        """Update task progress (0-100)."""
        progress_key = TaskQueueKeyBuilder.task_progress(task_id)
        await redis_manager.set(progress_key, str(progress))
        
        # Log progress
        if message:
            logs_key = TaskQueueKeyBuilder.task_logs(task_id)
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "progress": progress,
                "message": message,
            }
            await redis_manager.lpush(logs_key, json.dumps(log_entry))
    
    async def get_queue_stats(self, tenant_id: str) -> TaskQueueStats:
        """Get queue statistics for tenant."""
        pending_key = TaskQueueKeyBuilder.task_queue(tenant_id)
        active_key = TaskQueueKeyBuilder.active_tasks(tenant_id)
        history_key = TaskQueueKeyBuilder.task_history(tenant_id)
        
        pending = await redis_manager.zcard(pending_key)
        running = await redis_manager.scard(active_key)
        history = await redis_manager.lrange(history_key, 0, -1)
        
        # Count completed/failed from history
        completed = 0
        failed = 0
        for task_id in history[:100]:  # Sample last 100
            status = await redis_manager.get(
                TaskQueueKeyBuilder.task_status(task_id)
            )
            if status == "completed":
                completed += 1
            elif status == "failed":
                failed += 1
        
        # Get dead letter count
        dlq_key = TaskQueueKeyBuilder.dead_letter_queue()
        dead_letter = await redis_manager.llen(dlq_key)
        
        return TaskQueueStats(
            tenant_id=tenant_id,
            pending_count=pending,
            running_count=running,
            completed_count=completed,
            failed_count=failed,
            dead_letter_count=dead_letter,
            avg_execution_time_ms=0.0,  # Calculate from history
            max_concurrent=self.max_concurrent_per_tenant,
        )
    
    async def shutdown(self):
        """Graceful shutdown - cancel pending tasks."""
        self._shutdown_event.set()
        
        # Cancel all running tasks
        for task_id, asyncio_task in self._running_tasks.items():
            asyncio_task.cancel()
        
        logger.info("Task queue manager shutdown")


# Global instance
dag_task_queue = DAGTaskQueueManager()

