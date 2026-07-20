"""
backend/startup_recovery.py — FastAPI Startup Recovery.

Runs on application startup to recover tasks from previous session.
Ensures system resumes cleanly after crash/restart.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Set
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend_app.core.database import SessionLocal
from backend_app.core.models.dag_task import DAGTaskModel, TaskStatus as DBTaskStatus
from backend_app.core.dag_task_queue import dag_task_queue, TaskQueueKeyBuilder, TaskStatus
from backend_app.core.cache import redis_manager
from backend_app.backend.task_recovery import task_recovery_service


logger = logging.getLogger("StartupRecovery")


def _parse_datetime(val) -> datetime:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        if val.endswith('Z'):
            val = val[:-1] + '+00:00'
        try:
            return datetime.fromisoformat(val)
        except Exception:
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S%z"):
                try:
                    return datetime.strptime(val, fmt)
                except Exception:
                    pass
    return datetime.now(timezone.utc).replace(tzinfo=None)


class StartupRecovery:
    """
    Startup recovery for FastAPI application.
    
    Runs on application startup to:
    1. Recover stuck running/assigned tasks (stale heartbeat)
    2. Re-enqueue pending tasks missing from Redis queue
    3. Ensure system consistency after crash/restart
    """
    
    def __init__(
        self,
        heartbeat_threshold_seconds: float = 30.0,
        enable_recovery: bool = True
    ):
        self.heartbeat_threshold_seconds = heartbeat_threshold_seconds
        self.enable_recovery = enable_recovery
    
    async def recover_all_tasks(self) -> Dict[str, Any]:
        """
        Main recovery routine - runs on FastAPI startup.
        
        Steps:
        1. Load all tasks with status IN ('RUNNING', 'ASSIGNED')
        2. For each with stale heartbeat → mark for retry
        3. Load tasks with status = 'PENDING' not in Redis queue
        4. Re-enqueue missing tasks
        
        Returns:
            Recovery statistics
        """
        logger.info(
            "RECOVERY_STARTED: Starting system recovery after startup",
            extra={
                "event": "RECOVERY_STARTED",
                "heartbeat_threshold_seconds": self.heartbeat_threshold_seconds,
            }
        )
        
        stats = {
            "stuck_tasks_found": 0,
            "stuck_tasks_recovered": 0,
            "pending_tasks_found": 0,
            "pending_tasks_requeued": 0,
            "errors": [],
        }
        
        try:
            # ═══════════════════════════════════════════════════════════════
            # STEP 1 & 2: Recover stuck running/assigned tasks
            # ═══════════════════════════════════════════════════════════════
            stuck_stats = await self._recover_stuck_tasks()
            stats["stuck_tasks_found"] = stuck_stats["found"]
            stats["stuck_tasks_recovered"] = stuck_stats["recovered"]
            
            # ═══════════════════════════════════════════════════════════════
            # STEP 3 & 4: Re-enqueue pending tasks missing from Redis
            # ═══════════════════════════════════════════════════════════════
            pending_stats = await self._recover_pending_tasks()
            stats["pending_tasks_found"] = pending_stats["found"]
            stats["pending_tasks_requeued"] = pending_stats["requeued"]
            
        except Exception as e:
            logger.error(f"RECOVERY_ERROR: {str(e)}")
            stats["errors"].append(str(e))
        
        # Log final summary
        total_recovered = stats["stuck_tasks_recovered"] + stats["pending_tasks_requeued"]
        
        logger.info(
            f"RECOVERY_COMPLETED: "
            f"stuck_found={stats['stuck_tasks_found']} "
            f"stuck_recovered={stats['stuck_tasks_recovered']} "
            f"pending_found={stats['pending_tasks_found']} "
            f"pending_requeued={stats['pending_tasks_requeued']} "
            f"total_recovered={total_recovered}",
            extra={
                "event": "RECOVERY_COMPLETED",
                **stats,
                "total_recovered": total_recovered,
            }
        )
        
        return stats
    
    async def _recover_stuck_tasks(self) -> Dict[str, int]:
        """
        Step 1 & 2: Recover stuck running/assigned tasks.
        
        Finds tasks with stale heartbeat and marks them for retry.
        
        Returns:
            {'found': N, 'recovered': N}
        """
        logger.info("RECOVERY_STEP_1: Recovering stuck running/assigned tasks")
        
        # Use existing service to detect and recover
        recovered = await task_recovery_service.detect_and_recover_stuck_tasks(
            threshold_seconds=self.heartbeat_threshold_seconds
        )
        
        # Get count of stuck tasks found
        stuck_tasks = await task_recovery_service.detect_stuck_tasks(
            threshold_seconds=self.heartbeat_threshold_seconds
        )
        
        # Actually recover them
        for task in stuck_tasks:
            success = await task_recovery_service.recover_task(task, action="auto")
            if success:
                logger.info(
                    f"TASK_RECOVERED: task_id={task.task_id} "
                    f"retry_count={task.retry_count + 1}/{task.max_retries}",
                    extra={
                        "event": "TASK_RECOVERED",
                        "task_id": str(task.task_id),
                        "tenant_id": str(task.tenant_id),
                        "old_status": task.status.value,
                        "old_retry_count": task.retry_count,
                        "new_retry_count": task.retry_count + 1,
                        "last_heartbeat": task._last_heartbeat.isoformat() if task._last_heartbeat else None,
                    }
                )
        
        found = len(stuck_tasks)
        
        logger.info(
            f"RECOVERY_STEP_1_COMPLETE: found={found} recovered={recovered}",
            extra={
                "event": "RECOVERY_STEP_1_COMPLETE",
                "found": found,
                "recovered": recovered,
            }
        )
        
        return {"found": found, "recovered": recovered}
    
    async def _recover_pending_tasks(self) -> Dict[str, int]:
        """
        Step 3 & 4: Re-enqueue pending tasks missing from Redis.
        
        Finds tasks with status='pending' that are not in Redis queue
        and re-enqueues them.
        
        Returns:
            {'found': N, 'requeued': N}
        """
        logger.info("RECOVERY_STEP_2: Recovering pending tasks missing from Redis queue")
        
        db_session = SessionLocal()
        found = 0
        requeued = 0
        
        try:
            # Get all pending tasks from DB
            result = db_session.execute(
                text("""
                    SELECT 
                        task_id,
                        tenant_id,
                        priority,
                        dag_config,
                        retry_count,
                        max_retries,
                        created_at
                    FROM dag_tasks
                    WHERE status = 'PENDING'
                    ORDER BY created_at ASC
                """)
            )
            
            pending_tasks = []
            for row in result:
                task = {
                    "task_id": str(row.task_id),
                    "tenant_id": str(row.tenant_id),
                    "priority": row.priority,
                    "dag_config": row.dag_config,
                    "retry_count": row.retry_count or 0,
                    "max_retries": row.max_retries or 3,
                    "created_at": _parse_datetime(row.created_at),
                }
                pending_tasks.append(task)
            
            found = len(pending_tasks)
            
            if found == 0:
                logger.info("RECOVERY_STEP_2: No pending tasks found in database")
                return {"found": 0, "requeued": 0}
            
            logger.info(f"RECOVERY_STEP_2: Found {found} pending tasks in database")
            
            # Check each task against Redis queue
            for task in pending_tasks:
                task_id = task["task_id"]
                tenant_id = task["tenant_id"]
                
                # Check if task is in Redis queue
                queue_key = TaskQueueKeyBuilder.task_queue(tenant_id)
                score = await redis_manager.zscore(queue_key, task_id)
                
                if score is None:
                    # Task is pending in DB but not in Redis queue
                    # Need to re-enqueue
                    try:
                        # Calculate score based on priority and creation time
                        new_score = task["priority"] * 1_000_000_000_000 + int(
                            task["created_at"].timestamp() * 1000
                        )
                        
                        # Add to Redis queue
                        await redis_manager.zadd(queue_key, {task_id: new_score})
                        
                        # Ensure status is correct in Redis
                        status_key = TaskQueueKeyBuilder.task_status(task_id)
                        await redis_manager.set(status_key, TaskStatus.PENDING.value)
                        
                        requeued += 1
                        
                        logger.info(
                            f"TASK_REQUEUED: task_id={task_id} "
                            f"tenant={tenant_id} "
                            f"priority={task['priority']} "
                            f"reason=missing_from_redis",
                            extra={
                                "event": "TASK_REQUEUED",
                                "task_id": task_id,
                                "tenant_id": tenant_id,
                                "priority": task["priority"],
                                "retry_count": task["retry_count"],
                                "reason": "missing_from_redis",
                                "created_at": task["created_at"].isoformat(),
                            }
                        )
                        
                    except Exception as e:
                        logger.error(
                            f"TASK_REQUEUE_FAILED: task_id={task_id} error={str(e)}",
                            extra={
                                "event": "TASK_REQUEUE_FAILED",
                                "task_id": task_id,
                                "error": str(e),
                            }
                        )
                else:
                    # Task is already in Redis queue
                    logger.debug(
                        f"TASK_ALREADY_QUEUED: task_id={task_id} in Redis (score={score})"
                    )
            
        except Exception as e:
            logger.error(f"RECOVERY_STEP_2_ERROR: {str(e)}")
        finally:
            db_session.close()
        
        logger.info(
            f"RECOVERY_STEP_2_COMPLETE: found={found} requeued={requeued}",
            extra={
                "event": "RECOVERY_STEP_2_COMPLETE",
                "found": found,
                "requeued": requeued,
            }
        )
        
        return {"found": found, "requeued": requeued}


# Global instance
startup_recovery = StartupRecovery(
    heartbeat_threshold_seconds=30.0,
    enable_recovery=True,
)


async def run_startup_recovery() -> Dict[str, Any]:
    """
    Convenience function to run startup recovery.
    
    Usage in FastAPI:
        @app.on_event("startup")
        async def on_startup():
            await run_startup_recovery()
    
    Or with lifespan:
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            await run_startup_recovery()
            yield
    """
    return await startup_recovery.recover_all_tasks()
