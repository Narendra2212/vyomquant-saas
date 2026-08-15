"""
backend/task_recovery.py — Task Recovery Service.

Detects and recovers tasks from crashed workers based on stale heartbeats.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend_app.core.cache import redis_manager
from backend_app.core.dag_task_queue import TaskQueueKeyBuilder, TaskStatus
from backend_app.core.database import SessionLocal
from backend_app.core.models.dag_task import DAGTaskModel

logger = logging.getLogger("TaskRecovery")


class TaskRecoveryService:
    """
    Service for detecting and recovering stuck tasks.
    
    Detects tasks where:
    - status = 'RUNNING' (or 'assigned')
    - last_heartbeat is older than threshold (default: 30 seconds)
    
    Recovery actions:
    - Mark as failed with error 'Worker crashed'
    - Or retry if retry_count < max_retries
    """
    
    def __init__(
        self,
        stale_threshold_seconds: float = 30.0,
        enable_auto_retry: bool = True,
        max_recovery_attempts: int = 3,
        redis_client: Optional[Any] = None
    ):
        self.stale_threshold_seconds = stale_threshold_seconds
        self.enable_auto_retry = enable_auto_retry
        self.max_recovery_attempts = max_recovery_attempts
        self.redis = redis_client
        self._running = False
        self._recovery_task: Optional[asyncio.Task] = None

    def _get_redis(self):
        if self.redis is not None:
            return self.redis
        import backend_app.core.cache as cache
        return cache.redis_manager
    
    async def start(self, interval_seconds: float = 30.0):
        """Start the recovery service loop."""
        if self._running:
            logger.warning("TaskRecoveryService already running")
            return
        
        self._running = True
        self._recovery_task = asyncio.create_task(
            self._recovery_loop(interval_seconds)
        )
        
        logger.info(
            f"RECOVERY_SERVICE_STARTED: "
            f"threshold={self.stale_threshold_seconds}s "
            f"interval={interval_seconds}s "
            f"auto_retry={self.enable_auto_retry}",
            extra={
                "event": "RECOVERY_SERVICE_STARTED",
                "threshold_seconds": self.stale_threshold_seconds,
                "interval_seconds": interval_seconds,
                "auto_retry": self.enable_auto_retry,
            }
        )
    
    async def stop(self):
        """Stop the recovery service."""
        self._running = False
        
        if self._recovery_task:
            self._recovery_task.cancel()
            try:
                await self._recovery_task
            except asyncio.CancelledError:
                pass
        
        logger.info("RECOVERY_SERVICE_STOPPED")
    
    async def _recovery_loop(self, interval_seconds: float):
        """Main recovery loop - runs periodically to detect stuck tasks."""
        while self._running:
            try:
                # Run recovery detection
                recovered = await self.detect_and_recover_stuck_tasks()
                
                if recovered > 0:
                    logger.info(f"RECOVERY_BATCH_COMPLETED: recovered={recovered}")
                
                # Wait for next interval
                await asyncio.sleep(interval_seconds)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"RECOVERY_LOOP_ERROR: {str(e)}")
                await asyncio.sleep(interval_seconds)
    
    async def detect_stuck_tasks(
        self, 
        threshold_seconds: Optional[float] = None
    ) -> List[DAGTaskModel]:
        """
        Detect tasks with stale heartbeat.
        
        Finds tasks where:
        - status = 'RUNNING' OR 'assigned'
        - last_heartbeat < now() - threshold_seconds
        
        Args:
            threshold_seconds: Staleness threshold (default: self.stale_threshold_seconds)
        
        Returns:
            List of stuck tasks
        """
        threshold = threshold_seconds or self.stale_threshold_seconds
        stale_time = datetime.utcnow() - timedelta(seconds=threshold)
        
        db_session = SessionLocal()
        try:
            # Query for stuck tasks using raw SQL for efficiency
            result = db_session.execute(
                text("""
                    SELECT 
                        task_id,
                        tenant_id,
                        status,
                        priority,
                        dag_config,
                        progress,
                        retry_count,
                        max_retries,
                        created_at,
                        started_at,
                        last_heartbeat,
                        worker_id
                    FROM dag_tasks
                    WHERE status IN ('RUNNING', 'ASSIGNED')
                        AND (
                            last_heartbeat IS NULL 
                            OR last_heartbeat < :stale_time
                        )
                    ORDER BY started_at ASC
                """),
                {"stale_time": stale_time}
            )
            
            # Convert to DAGTaskModel objects
            stuck_tasks = []
            for row in result:
                task = DAGTaskModel(
                    task_id=row.task_id,
                    tenant_id=row.tenant_id,
                    status=row.status,
                    priority=row.priority,
                    dag_config=row.dag_config,
                    progress=row.progress or 0.0,
                    retry_count=row.retry_count or 0,
                    max_retries=row.max_retries or 3,
                    created_at=row.created_at,
                    started_at=row.started_at,
                )
                # Attach extra fields not in model
                task._worker_id = row.worker_id
                task._last_heartbeat = row.last_heartbeat
                stuck_tasks.append(task)
            
            if stuck_tasks:
                logger.info(
                    f"STUCK_TASKS_DETECTED: count={len(stuck_tasks)} "
                    f"threshold={threshold}s",
                    extra={
                        "event": "STUCK_TASKS_DETECTED",
                        "count": len(stuck_tasks),
                        "threshold_seconds": threshold,
                        "task_ids": [str(t.task_id) for t in stuck_tasks],
                    }
                )
            
            return stuck_tasks
            
        except Exception as e:
            logger.error(f"DETECT_STUCK_TASKS_ERROR: {str(e)}")
            return []
        finally:
            db_session.close()
    
    async def recover_task(
        self, 
        task: DAGTaskModel,
        action: str = "auto"  # "auto", "fail", "retry"
    ) -> bool:
        """
        Recover a single stuck task.
        
        RECOVERY LOGIC:
        1. Increment retry_count
        2. IF new retry_count <= max_retries:
           - status = 'PENDING'
           - push back to Redis queue
           - LOG: TASK_RETRY
        3. ELSE (retries exhausted):
           - move to DEAD LETTER
           - status = 'FAILED'
           - LOG: TASK_DEAD_LETTER
        
        Args:
            task: The stuck task to recover
            action: Recovery action - "auto" (decide based on retry count), 
                   "fail" (force fail), "retry" (force retry)
        
        Returns:
            True if recovery successful, False otherwise
        """
        task_id = str(task.task_id)
        str(task.tenant_id)
        
        db_session = SessionLocal()
        try:
            # STEP 1: Increment retry_count
            new_retry_count = task.retry_count + 1
            
            # Determine action based on new retry count
            if action == "auto":
                if self.enable_auto_retry and new_retry_count <= task.max_retries:
                    action = "retry"
                else:
                    action = "fail"
            
            if action == "retry":
                # STEP 2: RETRY - status = pending, push to Redis
                return await self._retry_task(task, db_session, new_retry_count)
            else:
                # STEP 3: DEAD LETTER - status = failed
                return await self._fail_task(task, db_session, new_retry_count)
                
        except Exception as e:
            logger.error(
                f"TASK_RECOVERY_FAILED: task_id={task_id} error={str(e)}",
                extra={
                    "event": "TASK_RECOVERY_FAILED",
                    "task_id": task_id,
                    "error": str(e),
                }
            )
            db_session.rollback()
            return False
        finally:
            db_session.close()
    
    async def _retry_task(
        self, 
        task: DAGTaskModel, 
        db_session: Session,
        new_retry_count: int
    ) -> bool:
        """
        Retry a stuck task by requeuing it.
        
        Sets:
        - status = 'PENDING'
        - retry_count = new_retry_count
        - Pushes to Redis queue
        
        Logs: TASK_RETRY
        """
        task_id = str(task.task_id)
        tenant_id = str(task.tenant_id)
        
        # Update database: status = pending, increment retry
        db_session.execute(
            text("""
                UPDATE dag_tasks
                SET status = 'PENDING',
                    retry_count = :retry_count,
                    error = NULL,
                    started_at = NULL,
                    last_heartbeat = NULL,
                    worker_id = NULL,
                    progress = 0.0
                WHERE task_id = :task_id
                AND tenant_id = :tenant_id
            """),
            {
                "task_id": task.task_id,
                "tenant_id": task.tenant_id,
                "retry_count": new_retry_count,
            }
        )
        db_session.commit()
        
        # Re-add to Redis queue
        r = self._get_redis()
        queue_key = TaskQueueKeyBuilder.task_queue(tenant_id)
        score = task.priority * 1_000_000_000_000 + int(datetime.utcnow().timestamp() * 1000)
        await r.zadd(queue_key, {task_id: score})
        
        # Update Redis status
        status_key = TaskQueueKeyBuilder.task_status(task_id)
        await r.set(status_key, TaskStatus.PENDING.value)
        
        # Remove from active set so execution slots are not leaked on retry
        await r.srem(
            TaskQueueKeyBuilder.active_tasks(tenant_id),
            task_id
        )
        
        # LOG: TASK_RETRY
        logger.info(
            f"TASK_RETRY: task_id={task_id} "
            f"retry_count={new_retry_count}/{task.max_retries} "
            f"reason=worker_crashed",
            extra={
                "event": "TASK_RETRY",
                "task_id": task_id,
                "tenant_id": tenant_id,
                "retry_count": new_retry_count,
                "max_retries": task.max_retries,
                "reason": "worker_crashed",
            }
        )
        
        return True
    
    async def _fail_task(
        self, 
        task: DAGTaskModel, 
        db_session: Session,
        new_retry_count: int
    ) -> bool:
        """
        Mark a stuck task as failed and move to dead letter.
        
        Sets:
        - status = 'FAILED'
        - retry_count = new_retry_count (already incremented)
        - Moves to DEAD LETTER queue
        
        Logs: TASK_DEAD_LETTER
        """
        task_id = str(task.task_id)
        tenant_id = str(task.tenant_id)
        
        # Update database: status = failed
        db_session.execute(
            text("""
                UPDATE dag_tasks
                SET status = 'FAILED',
                    retry_count = :retry_count,
                    error = :error,
                    completed_at = CURRENT_TIMESTAMP,
                    progress = 0.0
                WHERE task_id = :task_id
                AND tenant_id = :tenant_id
            """),
            {
                "task_id": task.task_id,
                "tenant_id": task.tenant_id,
                "retry_count": new_retry_count,
                "error": "Worker crashed - task lost due to stale heartbeat",
            }
        )
        db_session.commit()
        
        # Update Redis status
        r = self._get_redis()
        status_key = TaskQueueKeyBuilder.task_status(task_id)
        await r.set(status_key, TaskStatus.FAILED.value)
        
        # Move to DEAD LETTER queue
        await r.lpush(
            TaskQueueKeyBuilder.dead_letter_queue(),
            task_id
        )
        
        # Remove from active set
        await r.srem(
            TaskQueueKeyBuilder.active_tasks(tenant_id),
            task_id
        )
        
        # LOG: TASK_DEAD_LETTER
        logger.warning(
            f"TASK_DEAD_LETTER: task_id={task_id} "
            f"retry_count={new_retry_count}/{task.max_retries} "
            f"reason=worker_crashed_retries_exhausted",
            extra={
                "event": "TASK_DEAD_LETTER",
                "task_id": task_id,
                "tenant_id": tenant_id,
                "retry_count": new_retry_count,
                "max_retries": task.max_retries,
                "reason": "worker_crashed_retries_exhausted",
                "last_heartbeat": task._last_heartbeat.isoformat() if task._last_heartbeat else None,
            }
        )
        
        return True
    
    async def detect_and_recover_stuck_tasks(
        self,
        threshold_seconds: Optional[float] = None
    ) -> int:
        """
        Detect and recover all stuck tasks.
        
        Args:
            threshold_seconds: Staleness threshold
        
        Returns:
            Number of tasks recovered
        """
        stuck_tasks = await self.detect_stuck_tasks(threshold_seconds)
        
        if not stuck_tasks:
            return 0
        
        recovered_count = 0
        for task in stuck_tasks:
            success = await self.recover_task(task, action="auto")
            if success:
                recovered_count += 1
            
            # Small delay to avoid overwhelming the system
            await asyncio.sleep(0.1)
        
        logger.info(
            f"RECOVERY_SUMMARY: detected={len(stuck_tasks)} recovered={recovered_count}",
            extra={
                "event": "RECOVERY_SUMMARY",
                "detected": len(stuck_tasks),
                "recovered": recovered_count,
                "failed": len(stuck_tasks) - recovered_count,
            }
        )
        
        return recovered_count
    
    async def get_recovery_stats(self) -> Dict[str, Any]:
        """Get statistics about task recovery."""
        db_session = SessionLocal()
        try:
            # Count tasks by status
            result = db_session.execute(
                text("""
                    SELECT 
                        COUNT(*) FILTER (WHERE status = 'RUNNING') as running,
                        COUNT(*) FILTER (WHERE status = 'ASSIGNED') as assigned,
                        COUNT(*) FILTER (WHERE status = 'PENDING') as pending,
                        COUNT(*) FILTER (WHERE status = 'FAILED' 
                            AND error LIKE '%Worker crashed%') as crashed,
                        COUNT(*) FILTER (WHERE status = 'FAILED' 
                            AND error LIKE '%Worker crashed%' 
                            AND retry_count >= max_retries) as crashed_exhausted
                    FROM dag_tasks
                """)
            ).fetchone()
            
            # Count stale tasks
            stale_time = datetime.utcnow() - timedelta(seconds=self.stale_threshold_seconds)
            stale_result = db_session.execute(
                text("""
                    SELECT COUNT(*)
                    FROM dag_tasks
                    WHERE status IN ('RUNNING', 'ASSIGNED')
                        AND last_heartbeat < :stale_time
                """),
                {"stale_time": stale_time}
            ).fetchone()
            
            return {
                "running_tasks": result.running or 0,
                "assigned_tasks": result.assigned or 0,
                "pending_tasks": result.pending or 0,
                "crashed_tasks": result.crashed or 0,
                "crashed_exhausted": result.crashed_exhausted or 0,
                "stale_tasks": stale_result[0] if stale_result else 0,
                "stale_threshold_seconds": self.stale_threshold_seconds,
                "auto_retry_enabled": self.enable_auto_retry,
            }
            
        except Exception as e:
            logger.error(f"GET_STATS_ERROR: {str(e)}")
            return {}
        finally:
            db_session.close()


# Global instance
task_recovery_service = TaskRecoveryService(
    stale_threshold_seconds=30.0,
    enable_auto_retry=True,
)
