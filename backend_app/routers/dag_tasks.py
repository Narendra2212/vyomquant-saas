import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import (APIRouter, BackgroundTasks, Depends, HTTPException,
                     WebSocket)
from pydantic import BaseModel, Field

from backend_app.backend.dag_worker import WorkerPool, worker_pool
from backend_app.backend.redis_manager import redis_manager
from backend_app.backend.task_recovery import task_recovery_service
from backend_app.core.dag_task_queue import TaskQueueKeyBuilder, dag_task_queue
from backend_app.core.dependencies import get_admin_user, get_current_user

logger = logging.getLogger(__name__)
"""
routers/dag_tasks.py — DAG Task Queue API Endpoints.

API for submitting, monitoring, and managing DAG execution tasks.
"""




router = APIRouter(prefix="/api/dag/tasks", tags=["dag-tasks"])


# ═══════════════════════════════════════════════════════════════════════════
# REQUEST/RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════

class SubmitTaskRequest(BaseModel):
    """Request to submit DAG execution task."""
    dag_config: Dict[str, Any] = Field(..., description="DAG configuration")
    priority: int = Field(default=5, ge=1, le=10, description="Task priority (1=highest)")
    scheduled_for: Optional[datetime] = Field(None, description="Scheduled execution time")


class SubmitTaskResponse(BaseModel):
    """Response from task submission."""
    task_id: str
    tenant_id: str
    status: str
    queue_position: Optional[int]
    estimated_start: Optional[str]


class TaskStatusResponse(BaseModel):
    """Task status response."""
    task_id: str
    status: str
    progress: Optional[float]
    created_at: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
    retry_count: int
    result: Optional[Dict]
    error: Optional[str]


class TaskListResponse(BaseModel):
    """List of tasks response."""
    tasks: List[TaskStatusResponse]
    total: int
    pending: int
    running: int
    completed: int


class QueueStatsResponse(BaseModel):
    """Queue statistics response."""
    tenant_id: str
    pending_count: int
    running_count: int
    completed_count: int
    failed_count: int
    dead_letter_count: int
    max_concurrent: int


class CancelTaskResponse(BaseModel):
    """Task cancellation response."""
    task_id: str
    cancelled: bool
    message: str


class WorkerStatsResponse(BaseModel):
    """Worker pool statistics."""
    total_workers: int
    active_workers: int
    idle_workers: int
    busy_workers: int


# ═══════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/submit", response_model=SubmitTaskResponse)
async def submit_task(
    request: SubmitTaskRequest,
    background_tasks: BackgroundTasks,
    tenant: dict = Depends(get_current_user)
):
    """
    🔴 PHASE 3 API LOCK: DAG task submission DISABLED.
    
    ALGO-ONLY EXECUTION: Tasks must be submitted by BotRunner internally.
    UI/API direct task submission is STRICTLY PROHIBITED.
    """
    from fastapi import HTTPException
    
    logger.critical(
        f"🚫 BLOCKED API TASK SUBMISSION | "
        f"Tenant: {tenant['id']} | "
        f"Reason: Manual execution disabled. Use strategy deployment."
    )
    
    raise HTTPException(
        status_code=403,
        detail={
            "error": "MANUAL_EXECUTION_DISABLED",
            "message": "Manual execution disabled. Use strategy deployment.",
            "allowed_path": "Strategy → DAG → BotRunner → UnifiedExecutionEngine",
            "blocked_path": "UI → API → Task Submission",
            "solution": "Deploy a strategy via the internal BotRunner system."
        }
    )


@router.get("/status/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    tenant: dict = Depends(get_current_user)
):
    """Get status of a specific task."""
    status = await dag_task_queue.get_task_status(task_id)
    
    if not status:
        raise HTTPException(404, f"Task {task_id} not found")
    
    # Verify tenant ownership
    task = await dag_task_queue._load_task(task_id)
    if task and task.tenant_id != tenant["id"]:
        raise HTTPException(403, "Access denied")
    
    return TaskStatusResponse(
        task_id=task_id,
        status=status.get("status", "unknown"),
        progress=float(status.get("progress")) if status.get("progress") else None,
        created_at=status.get("created_at"),
        started_at=status.get("started_at"),
        completed_at=status.get("completed_at"),
        retry_count=status.get("retry_count", 0),
        result=task.result if task else None,
        error=task.error if task else None,
    )


@router.get("/list", response_model=TaskListResponse)
async def list_tasks(
    status: Optional[str] = None,
    limit: int = 50,
    tenant: dict = Depends(get_current_user)
):
    """
    List tasks for the tenant.
    
    Args:
        status: Filter by status (pending, running, completed, failed)
        limit: Maximum number of tasks to return
    """
    # Get all task IDs for tenant
    pending_key = TaskQueueKeyBuilder.task_queue(tenant["id"])
    active_key = TaskQueueKeyBuilder.active_tasks(tenant["id"])
    history_key = TaskQueueKeyBuilder.task_history(tenant["id"])
    
    pending_ids = await dag_task_queue._redis_zrange(pending_key, 0, limit)
    active_ids = await dag_task_queue._redis_smembers(active_key)
    history_ids = await dag_task_queue._redis_lrange(history_key, 0, limit)
    
    all_ids = pending_ids + list(active_ids) + history_ids
    
    # Load tasks
    tasks = []
    for task_id in all_ids[:limit]:
        task_status = await dag_task_queue.get_task_status(task_id)
        if task_status:
            tasks.append(TaskStatusResponse(
                task_id=task_id,
                status=task_status.get("status", "unknown"),
                progress=float(task_status.get("progress")) if task_status.get("progress") else None,
                created_at=task_status.get("created_at"),
                started_at=task_status.get("started_at"),
                completed_at=task_status.get("completed_at"),
                retry_count=task_status.get("retry_count", 0),
            ))
    
    # Filter by status if specified
    if status:
        tasks = [t for t in tasks if t.status == status]
    
    return TaskListResponse(
        tasks=tasks,
        total=len(tasks),
        pending=len([t for t in tasks if t.status == "pending"]),
        running=len([t for t in tasks if t.status == "running"]),
        completed=len([t for t in tasks if t.status == "completed"]),
    )


@router.post("/cancel/{task_id}", response_model=CancelTaskResponse)
async def cancel_task(
    task_id: str,
    tenant: dict = Depends(get_current_user)
):
    """Cancel a pending or running task."""
    cancelled = await dag_task_queue.cancel_task(task_id, tenant["id"])
    
    if cancelled:
        return CancelTaskResponse(
            task_id=task_id,
            cancelled=True,
            message="Task cancelled successfully"
        )
    else:
        return CancelTaskResponse(
            task_id=task_id,
            cancelled=False,
            message="Task not found or already completed"
        )


@router.get("/stats", response_model=QueueStatsResponse)
async def get_queue_stats(
    tenant: dict = Depends(get_current_user)
):
    """Get queue statistics for the tenant."""
    stats = await dag_task_queue.get_queue_stats(tenant["id"])
    
    return QueueStatsResponse(
        tenant_id=stats.tenant_id,
        pending_count=stats.pending_count,
        running_count=stats.running_count,
        completed_count=stats.completed_count,
        failed_count=stats.failed_count,
        dead_letter_count=stats.dead_letter_count,
        max_concurrent=stats.max_concurrent,
    )


@router.get("/workers", response_model=WorkerStatsResponse)
async def get_worker_stats(
    admin: dict = Depends(get_admin_user)
):
    """Get worker pool statistics."""
    stats = worker_pool.get_stats()
    return WorkerStatsResponse(**stats)


@router.post("/workers/start")
async def start_workers(
    count: int = 4,
    admin: dict = Depends(get_admin_user)
):
    """Start worker pool (admin only)."""
    global worker_pool
    if worker_pool._started:
        return {"message": "Worker pool already running", "stats": worker_pool.get_stats()}
    
    worker_pool = WorkerPool(num_workers=count)
    await worker_pool.start()
    
    return {
        "message": f"Started {count} workers",
        "stats": worker_pool.get_stats()
    }


@router.post("/workers/stop")
async def stop_workers(
    admin: dict = Depends(get_admin_user)
):
    """Stop worker pool (admin only)."""
    await worker_pool.stop()
    
    return {"message": "Worker pool stopped"}


# WebSocket endpoint for real-time task updates
@router.websocket("/ws/{task_id}")
async def task_websocket(websocket: WebSocket, task_id: str):
    """WebSocket for real-time task progress updates."""
    await websocket.accept()
    
    try:
        # Verify task exists and get tenant
        task = await dag_task_queue._load_task(task_id)
        if not task:
            await websocket.close(code=4004, reason="Task not found")
            return
        
        # Verify WebSocket auth — FAIL CLOSED
        # No token → deny. Verification error → deny. Tenant mismatch → deny.
        token = websocket.query_params.get("token") or websocket.headers.get("x-auth-token")
        if not token:
            await websocket.close(code=4001, reason="Authentication required: provide ?token= or x-auth-token header")
            return

        try:
            from backend_app.core.websocket_auth import verify_websocket_token
            auth_user = await verify_websocket_token(token)
            if not auth_user:
                await websocket.close(code=4003, reason="Invalid or expired authentication token")
                return
            if str(auth_user.get("id")) != str(getattr(task, "tenant_id", auth_user.get("id"))):
                await websocket.close(code=4003, reason="Unauthorized tenant task access")
                return
        except Exception as e:
            logger.warning(f"WebSocket auth failed for task {task_id}: {e}")
            await websocket.close(code=4003, reason="Authentication verification failed")
            return

        
        # Send initial status
        status = await dag_task_queue.get_task_status(task_id)
        await websocket.send_json({
            "type": "status",
            "data": status
        })
        
        # Poll for updates
        last_progress = None
        while True:
            await asyncio.sleep(1)
            
            status = await dag_task_queue.get_task_status(task_id)
            progress = status.get("progress")
            
            # Send update if progress changed
            if progress != last_progress:
                await websocket.send_json({
                    "type": "progress",
                    "data": status
                })
                last_progress = progress
            
            # Close if task completed/failed
            if status.get("status") in ["completed", "failed", "cancelled"]:
                await websocket.send_json({
                    "type": "final",
                    "data": status
                })
                await websocket.close()
                break
                
    except Exception as e:
        logger.error(f"WebSocket error for task {task_id}: {e}")
        await websocket.close(code=1011, reason="Internal error")


# ═══════════════════════════════════════════════════════════════════════════
# RECOVERY ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

class RecoveryStatsResponse(BaseModel):
    """Recovery statistics response."""
    running_tasks: int
    assigned_tasks: int
    pending_tasks: int
    crashed_tasks: int
    crashed_exhausted: int
    stale_tasks: int
    retry_queue_size: int
    dead_letter_count: int
    stale_threshold_seconds: float
    auto_retry_enabled: bool


class RecoveryTriggerResponse(BaseModel):
    """Manual recovery trigger response."""
    recovered: int
    message: str


class DeadLetterTask(BaseModel):
    """Dead letter task info."""
    task_id: str
    tenant_id: str
    status: str
    retry_count: int
    max_retries: int
    error: Optional[str]
    completed_at: Optional[str]


class DeadLetterListResponse(BaseModel):
    """Dead letter queue contents."""
    count: int
    tasks: List[DeadLetterTask]


@router.get("/recovery/stats", response_model=RecoveryStatsResponse)
async def get_recovery_stats(
    tenant: dict = Depends(get_current_user)
):
    """
    Get recovery statistics.
    
    Returns:
    - stuck tasks (stale heartbeat)
    - retry queue size
    - dead letter count
    """
    try:
        # Get recovery stats from service
        stats = await task_recovery_service.get_recovery_stats()
        
        # Get retry queue size (pending tasks across all tenants)
        from backend_app.core.cache import redis_manager
        0
        # This would need to iterate all tenant queues - simplified here
        
        # Get dead letter count
        dead_letter_key = TaskQueueKeyBuilder.dead_letter_queue()
        dead_letter_count = await redis_manager.llen(dead_letter_key) or 0
        
        return RecoveryStatsResponse(
            running_tasks=stats.get("running_tasks", 0),
            assigned_tasks=stats.get("assigned_tasks", 0),
            pending_tasks=stats.get("pending_tasks", 0),
            crashed_tasks=stats.get("crashed_tasks", 0),
            crashed_exhausted=stats.get("crashed_exhausted", 0),
            stale_tasks=stats.get("stale_tasks", 0),
            retry_queue_size=stats.get("pending_tasks", 0),  # Approximation
            dead_letter_count=dead_letter_count,
            stale_threshold_seconds=stats.get("stale_threshold_seconds", 30.0),
            auto_retry_enabled=stats.get("auto_retry_enabled", True),
        )
    except Exception as e:
        logger.error(f"RECOVERY_STATS_ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get recovery stats: {str(e)}")


@router.post("/recovery/trigger", response_model=RecoveryTriggerResponse)
async def trigger_recovery(
    tenant: dict = Depends(get_current_user)
):
    """
    Manually trigger task recovery.
    
    Detects and recovers stuck tasks from crashed workers.
    """
    try:
        logger.info(
            f"MANUAL_RECOVERY_TRIGGERED: by={tenant['id']}",
            extra={
                "event": "MANUAL_RECOVERY_TRIGGERED",
                "triggered_by": tenant["id"],
            }
        )
        
        # Run recovery
        recovered = await task_recovery_service.detect_and_recover_stuck_tasks()
        
        logger.info(
            f"MANUAL_RECOVERY_COMPLETE: recovered={recovered}",
            extra={
                "event": "MANUAL_RECOVERY_COMPLETE",
                "recovered": recovered,
            }
        )
        
        return RecoveryTriggerResponse(
            recovered=recovered,
            message=f"Recovery complete. Recovered {recovered} stuck tasks."
        )
    except Exception as e:
        logger.error(f"MANUAL_RECOVERY_ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Recovery failed: {str(e)}")


@router.get("/recovery/dead-letter", response_model=DeadLetterListResponse)
async def get_dead_letter_tasks(
    limit: int = 100,
    tenant: dict = Depends(get_current_user)
):
    """
    Inspect dead letter queue.
    
    Returns failed tasks that exceeded max retries.
    """
    try:
        from sqlalchemy import text

        from backend_app.core.cache import redis_manager
        from backend_app.core.database import SessionLocal

        # Get task IDs from dead letter queue
        dead_letter_key = TaskQueueKeyBuilder.dead_letter_queue()
        task_ids = await redis_manager.lrange(dead_letter_key, 0, limit - 1)
        
        if not task_ids:
            return DeadLetterListResponse(count=0, tasks=[])
        
        # Fetch task details from database
        db_session = SessionLocal()
        dead_letter_tasks = []
        
        try:
            for task_id_str in task_ids:
                try:
                    task_id = UUID(task_id_str)
                except Exception:
                    continue
                
                result = db_session.execute(
                    text("""
                        SELECT 
                            task_id,
                            tenant_id,
                            status,
                            retry_count,
                            max_retries,
                            error,
                            completed_at
                        FROM dag_tasks
                        WHERE task_id = :task_id
                    """),
                    {"task_id": task_id}
                ).fetchone()
                
                if result:
                    dead_letter_tasks.append(DeadLetterTask(
                        task_id=str(result.task_id),
                        tenant_id=str(result.tenant_id),
                        status=result.status.value if hasattr(result.status, 'value') else str(result.status),
                        retry_count=result.retry_count or 0,
                        max_retries=result.max_retries or 3,
                        error=result.error,
                        completed_at=result.completed_at.isoformat() if result.completed_at else None
                    ))
        finally:
            db_session.close()
        
        logger.info(
            f"DEAD_LETTER_INSPECTED: count={len(dead_letter_tasks)} limit={limit}",
            extra={
                "event": "DEAD_LETTER_INSPECTED",
                "count": len(dead_letter_tasks),
                "limit": limit,
                "requested_by": tenant["id"],
            }
        )
        
        return DeadLetterListResponse(
            count=len(dead_letter_tasks),
            tasks=dead_letter_tasks
        )
    except Exception as e:
        logger.error(f"DEAD_LETTER_ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get dead letter tasks: {str(e)}")


# Helper methods for Redis operations (add to DAGTaskQueueManager)
async def _redis_zrange(self, key, start, end):
    """Helper to get sorted set range."""
    return await redis_manager.zrange(key, start, end) or []

async def _redis_smembers(self, key):
    """Helper to get set members."""
    return await redis_manager.smembers(key) or set()

async def _redis_lrange(self, key, start, end):
    """Helper to get list range."""
    return await redis_manager.lrange(key, start, end) or []

async def _get_queue_position(self, queue_key, task_id):
    """Get position of task in queue."""
    rank = await redis_manager.zrank(queue_key, task_id)
    return rank + 1 if rank is not None else None

# Attach helpers to dag_task_queue
dag_task_queue._redis_zrange = _redis_zrange.__get__(dag_task_queue, type(dag_task_queue))
dag_task_queue._redis_smembers = _redis_smembers.__get__(dag_task_queue, type(dag_task_queue))
dag_task_queue._redis_lrange = _redis_lrange.__get__(dag_task_queue, type(dag_task_queue))
dag_task_queue._get_queue_position = _get_queue_position.__get__(dag_task_queue, type(dag_task_queue))
