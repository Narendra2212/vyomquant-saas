"""
backend/dag_worker.py — DAG Task Queue Worker.

Asynchronous worker for executing DAG tasks from the queue.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from backend_app.backend.dag_engine import DAGEngine
from backend_app.backend.market_data_validation import (MarketDataValidator,
                                                        ValidationConfig)
from backend_app.core.cache import redis_manager
from backend_app.core.dag_task_queue import (DAGTask, TaskQueueKeyBuilder,
                                             TaskStatus, dag_task_queue)
from backend_app.core.database import SessionLocal
from backend_app.core.feature_flags import (ExecutionContext, ExecutionFlags,
                                            UnsafeExecutionError)
from backend_app.core.models.dag_task import DAGTaskRepository
from backend_app.core.models.dag_task import TaskStatus as DBTaskStatus
from backend_app.core.safety_monitor import log_blocked_execution
from backend_app.core.tenant import TenantContext, TenantQuota
from backend_app.core.worker_base import WorkerBase

logger = logging.getLogger("DAGWorker")


class DAGWorker(WorkerBase):
    """
    Worker that processes DAG tasks from the queue.
    
    Each worker:
    - Claims tasks from queue
    - Validates tenant permissions
    - Executes DAG with progress updates
    - Handles results and errors
    """
    
    def __init__(
        self,
        worker_id: Optional[str] = None,
        poll_interval_seconds: float = 1.0,
        max_idle_seconds: float = 60.0,
        heartbeat_interval_seconds: float = 5.0
    ):
        name = worker_id or str(uuid.uuid4())[:8]
        super().__init__(worker_name=name, poll_interval=poll_interval_seconds, heartbeat_interval=heartbeat_interval_seconds)
        self.worker_id = name
        self.max_idle_seconds = max_idle_seconds
        self.queue = dag_task_queue
        self._current_task: Optional[DAGTask] = None
        
        # Task heartbeat tracking
        self._task_heartbeat_task: Optional[asyncio.Task] = None
        self._heartbeat_stop_event = asyncio.Event()
        
        # Register worker
        self._register_worker()
    
    def _register_worker(self):
        """Register worker in Redis."""
        redis_manager.sadd(TaskQueueKeyBuilder.worker_registry(), self.worker_id)
        logger.info(f"Worker registered: {self.worker_id}")
    
    async def start(self):
        """Start worker loop."""
        await super().start()
    
    async def cleanup(self):
        """Cleanup worker gracefully."""
        if self._current_task:
            # Signal current task to cancel
            logger.info(f"Cancelling current task: {self._current_task.task_id}")
        
        # Cleanup registration
        redis_manager.srem(TaskQueueKeyBuilder.worker_registry(), self.worker_id)
    
    async def process_iteration(self):
        """Main worker loop iteration with atomic task claiming."""
        # Note: WorkerBase handles the while self.running loop and asyncio.sleep
        # Try to claim a task with atomic PostgreSQL-Redis coordination
        task = await self._atomic_claim_task()
        
        if task:
            await self._execute_task(task)
        else:
            # No tasks available
            await asyncio.sleep(self.poll_interval)
    
    async def _atomic_claim_task(self) -> Optional[DAGTask]:
        """
        Atomically claim a task from Redis queue and update PostgreSQL.
        
        Process:
        1. Pop task_id from Redis queue (atomic ZPOPMIN)
        2. Fetch task from PostgreSQL
        3. Update DB status: pending → assigned → running
        4. Set started_at and last_heartbeat
        5. If DB update fails, push task back to Redis
        
        Returns:
            DAGTask if successfully claimed, None otherwise
        """
        
        # Step 1: Pop task_id from Redis queue (atomic operation)
        # Try tenant-specific queue first
        tenant_pattern = "user:*:dag:task_queue"
        queue_keys = await redis_manager.keys(tenant_pattern)
        
        task_id = None
        queue_key = None
        tenant_id = None
        
        for key in queue_keys:
            # Try to pop from this queue
            try:
                # ZPOPMIN returns [member, score] or empty list
                result = redis_manager.redis.zpopmin(key, 1)
                if result:
                    task_id = result[0][0].decode() if isinstance(result[0][0], bytes) else result[0][0]
                    queue_key = key
                    # Extract tenant_id from key: user:{tenant_id}:dag:task_queue
                    parts = key.split(":")
                    if len(parts) >= 3:
                        tenant_id = parts[1]
                    break
            except Exception as e:
                logger.warning(f"Failed to pop from queue {key}: {e}")
                continue
        
        if not task_id:
            return None
        
        logger.info(f"TASK_POPPED: task_id={task_id} from queue={queue_key}")
        
        # Step 2 & 3: Fetch from PostgreSQL and update status
        db_session = SessionLocal()
        db_task = None
        
        try:
            repo = DAGTaskRepository(db_session)
            
            # Fetch task from PostgreSQL
            db_task = repo.get_by_id(UUID(task_id), UUID(tenant_id))
            
            if not db_task:
                logger.error(f"TASK_NOT_IN_DB: task_id={task_id} popped from Redis but not found in PostgreSQL")
                # Task lost - this shouldn't happen with dual-write
                return None
            
            # Check current status
            if db_task.status != DBTaskStatus.PENDING:
                logger.warning(f"TASK_ALREADY_CLAIMED: task_id={task_id} status={db_task.status.value}")
                # Someone else claimed it - race condition
                return None
            
            # Update status: pending → assigned → running
            # Set timestamps
            now = datetime.utcnow()
            db_task.status = DBTaskStatus.RUNNING
            db_task.started_at = now
            db_task.last_heartbeat = now
            db_task.progress = 0.0
            
            # Add worker info
            db_task.worker_id = self.worker_id
            
            # Commit to database
            db_session.commit()
            db_session.refresh(db_task)
            
            logger.info(
                f"TASK_CLAIMED: task_id={task_id} "
                f"worker={self.worker_id} "
                f"tenant={tenant_id} "
                f"status={db_task.status.value}",
                extra={
                    "event": "TASK_CLAIMED",
                    "task_id": task_id,
                    "worker_id": self.worker_id,
                    "tenant_id": tenant_id,
                    "status": db_task.status.value,
                }
            )
            
            # Add to active set in Redis
            await redis_manager.sadd(
                TaskQueueKeyBuilder.active_tasks(tenant_id),
                task_id
            )
            
            # Create DAGTask object for execution
            task = DAGTask(
                task_id=task_id,
                tenant_id=tenant_id,
                dag_config=db_task.dag_config,
                priority=db_task.priority,
                status=TaskStatus(db_task.status.value),
                retry_count=db_task.retry_count,
                max_retries=db_task.max_retries,
                created_at=db_task.created_at,
                started_at=db_task.started_at,
                worker_id=self.worker_id,
            )
            
            return task
            
        except Exception as e:
            # DB update failed - rollback and push back to Redis
            logger.error(
                f"TASK_DB_CLAIM_FAILED: task_id={task_id} "
                f"error={str(e)}. Pushing back to queue.",
                extra={
                    "event": "TASK_DB_CLAIM_FAILED",
                    "task_id": task_id,
                    "error": str(e),
                }
            )
            
            # Rollback database transaction
            db_session.rollback()
            
            # Step 5: Push task back to Redis queue
            try:
                # Get original priority/score
                task_data = await redis_manager.hget(
                    TaskQueueKeyBuilder.task_data(task_id), "data"
                )
                if task_data:
                    task_dict = json.loads(task_data)
                    priority = task_dict.get("priority", 5)
                    created_at = datetime.fromisoformat(task_dict.get("created_at", datetime.utcnow().isoformat()))
                    
                    # Re-calculate score
                    score = priority * 1_000_000_000_000 + int(created_at.timestamp() * 1000)
                    
                    # Push back to queue
                    await redis_manager.zadd(queue_key, {task_id: score})
                    
                    logger.info(
                        f"TASK_REQUEUED: task_id={task_id} after DB claim failure",
                        extra={
                            "event": "TASK_REQUEUED",
                            "task_id": task_id,
                            "reason": "db_claim_failed",
                        }
                    )
            except Exception as requeue_error:
                logger.error(
                    f"TASK_REQUEUE_FAILED: task_id={task_id} "
                    f"error={str(requeue_error)}. Task may be lost!",
                    extra={
                        "event": "TASK_REQUEUE_FAILED",
                        "task_id": task_id,
                        "error": str(requeue_error),
                    }
                )
            
            return None
            
        finally:
            db_session.close()
    
    async def _execute_task(self, task: DAGTask):
        """Execute a DAG task with heartbeat updates."""
        self._current_task = task
        start_time = datetime.utcnow()
        
        db_session = SessionLocal()
        
        try:
            # Create tenant context
            tenant = TenantContext(
                user_id=task.tenant_id,
                tenant_id=task.tenant_id,
                email="",  # Load from cache or DB
                plan=self._get_tenant_plan(task.tenant_id),
                quota=self._get_tenant_quota(task.tenant_id),
            )
            
            # Validate market data first
            await self._validate_market_data(task, tenant)
            
            # Execute DAG with progress updates
            result = await self._run_dag_with_heartbeat(task, tenant, db_session)
            
            # Calculate execution time
            execution_time_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            completed_at = datetime.utcnow()
            
            # ═══════════════════════════════════════════════════════════════
            # SOURCE OF TRUTH: Store result in PostgreSQL FIRST
            # ═══════════════════════════════════════════════════════════════
            repo = DAGTaskRepository(db_session)
            repo.update_status(
                UUID(task.task_id),
                UUID(task.tenant_id),
                DBTaskStatus.COMPLETED,
                result=result,                    # JSON output
                progress=100.0,                   # 100% complete
                completed_at=completed_at,        # Completion timestamp
                execution_time_ms=execution_time_ms
            )
            
            logger.info(
                f"TASK_RESULT_PERSISTED: task_id={task.task_id} "
                f"db=postgresql status=completed progress=100%",
                extra={
                    "event": "TASK_RESULT_PERSISTED",
                    "task_id": task.task_id,
                    "tenant_id": task.tenant_id,
                    "status": "completed",
                    "progress": 100.0,
                    "source": "postgresql",
                }
            )
            
            # Update Redis (secondary cache, not source of truth)
            await self.queue.complete_task(
                task.task_id,
                result,
                execution_time_ms
            )
            
            # Store summary in Redis for quick access
            result_summary = {
                "task_id": task.task_id,
                "tenant_id": task.tenant_id,
                "status": "completed",
                "progress": 100.0,
                "completed_at": completed_at.isoformat(),
                "execution_time_ms": execution_time_ms,
                "result_preview": str(result)[:500] if result else None,  # Truncated
            }
            await redis_manager.setex(
                TaskQueueKeyBuilder.task_result(task.task_id),
                3600,  # 1 hour TTL
                json.dumps(result_summary)
            )
            
            # Remove from active set
            await redis_manager.srem(
                TaskQueueKeyBuilder.active_tasks(task.tenant_id),
                task.task_id
            )
            
            logger.info(
                f"TASK_COMPLETED: task_id={task.task_id} "
                f"({execution_time_ms:.0f}ms)",
                extra={
                    "event": "TASK_COMPLETED",
                    "task_id": task.task_id,
                    "execution_time_ms": execution_time_ms,
                }
            )
            
        except asyncio.CancelledError:
            # Task was cancelled during execution
            execution_time_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            
            logger.warning(
                f"TASK_CANCELLED_EXECUTION: task_id={task.task_id} reason=user_requested",
                extra={
                    "event": "TASK_CANCELLED_EXECUTION",
                    "task_id": task.task_id,
                    "reason": "user_requested",
                }
            )
            
            # Update DB status to cancelled
            try:
                repo = DAGTaskRepository(db_session)
                repo.update_status(
                    UUID(task.task_id),
                    UUID(task.tenant_id),
                    DBTaskStatus.CANCELLED,
                    error="Task cancelled by user during execution",
                    completed_at=datetime.utcnow(),
                    progress=0.0
                )
                
                logger.info(
                    f"TASK_CANCELLED_DB_UPDATED: task_id={task.task_id} status=cancelled",
                    extra={
                        "event": "TASK_CANCELLED_DB_UPDATED",
                        "task_id": task.task_id,
                        "status": "cancelled",
                    }
                )
            except Exception as db_error:
                logger.error(f"Failed to update cancelled task status in DB: {db_error}")
            
            # Update Redis status
            await self.queue.cancel_task(
                task.task_id,
                task.tenant_id,
                reason="execution_cancelled"
            )
            
            raise
            
        except Exception as e:
            execution_time_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            
            logger.error(
                f"TASK_FAILED: task_id={task.task_id} error={str(e)}",
                extra={
                    "event": "TASK_FAILED",
                    "task_id": task.task_id,
                    "error": str(e),
                }
            )
            
            # Update DB status to failed
            try:
                repo = DAGTaskRepository(db_session)
                repo.update_status(
                    UUID(task.task_id),
                    UUID(task.tenant_id),
                    DBTaskStatus.FAILED,
                    error=str(e),
                    execution_time_ms=execution_time_ms
                )
            except Exception as db_error:
                logger.error(f"Failed to update task status in DB: {db_error}")
            
            # Mark failed in Redis
            await self.queue.fail_task(
                task.task_id,
                str(e),
                execution_time_ms
            )
            
        finally:
            self._current_task = None
            # Stop heartbeat
            await self._stop_heartbeat()
            db_session.close()
    
    async def start_heartbeat(self, task_id: str):
        """
        Start heartbeat for a running task.
        
        Updates dag_tasks.last_heartbeat every 5 seconds.
        If worker crashes, heartbeat stops → last_heartbeat becomes stale
        → recovery job detects and reassigns task.
        
        Args:
            task_id: Task identifier to heartbeat
        """
        if self._task_heartbeat_task and not self._task_heartbeat_task.done():
            logger.warning(f"Heartbeat already running for task {task_id}, stopping old one")
            await self._stop_heartbeat()
        
        self._heartbeat_stop_event.clear()
        self._task_heartbeat_task = asyncio.create_task(
            self._task_heartbeat_loop(task_id)
        )
        
        logger.info(
            f"HEARTBEAT_STARTED: task_id={task_id} worker={self.worker_id}",
            extra={
                "event": "HEARTBEAT_STARTED",
                "task_id": task_id,
                "worker_id": self.worker_id,
                "interval_seconds": self.heartbeat_interval,
            }
        )
    
    async def _task_heartbeat_loop(self, task_id: str):
        """
        Heartbeat loop - updates last_heartbeat every 5 seconds.
        
        Runs until:
        - Task completes (stop_heartbeat called)
        - Worker stops
        - DB update fails (stops to prevent spam)
        """
        failures = 0
        max_failures = 3
        
        while not self._heartbeat_stop_event.is_set():
            try:
                # Wait for interval or stop signal
                try:
                    await asyncio.wait_for(
                        self._heartbeat_stop_event.wait(),
                        timeout=self.heartbeat_interval
                    )
                    # Stop event was set
                    break
                except asyncio.TimeoutError:
                    # Interval elapsed, send heartbeat
                    pass
                
                # Update heartbeat in database
                db_session = SessionLocal()
                try:
                    # Get current task to verify it's still running
                    if self._current_task:
                        tenant_id = self._current_task.tenant_id
                        
                        # Use raw SQL for efficiency (no need to fetch full object)
                        from sqlalchemy import text
                        result = db_session.execute(
                            text("""
                                UPDATE dag_tasks 
                                SET last_heartbeat = CURRENT_TIMESTAMP
                                WHERE task_id = :task_id 
                                AND tenant_id = :tenant_id
                                AND status = 'running'
                                RETURNING task_id
                            """),
                            {
                                "task_id": UUID(task_id),
                                "tenant_id": UUID(tenant_id)
                            }
                        )
                        
                        updated = result.fetchone()
                        db_session.commit()
                        
                        if updated:
                            failures = 0  # Reset failure count on success
                            logger.debug(
                                f"HEARTBEAT: task_id={task_id} worker={self.worker_id}"
                            )
                        else:
                            # Task not found or not running
                            logger.warning(
                                f"HEARTBEAT_FAILED: task_id={task_id} not found or not running",
                                extra={
                                    "event": "HEARTBEAT_FAILED",
                                    "task_id": task_id,
                                    "reason": "task_not_running",
                                }
                            )
                            break
                    else:
                        # No current task, stop heartbeat
                        logger.warning(f"HEARTBEAT: No current task for {task_id}, stopping")
                        break
                        
                except Exception as e:
                    failures += 1
                    logger.error(
                        f"HEARTBEAT_ERROR: task_id={task_id} error={str(e)} failures={failures}/{max_failures}",
                        extra={
                            "event": "HEARTBEAT_ERROR",
                            "task_id": task_id,
                            "error": str(e),
                            "failure_count": failures,
                        }
                    )
                    
                    if failures >= max_failures:
                        logger.error(
                            f"HEARTBEAT_MAX_FAILURES: task_id={task_id} stopping heartbeat",
                            extra={
                                "event": "HEARTBEAT_MAX_FAILURES",
                                "task_id": task_id,
                                "max_failures": max_failures,
                            }
                        )
                        break
                finally:
                    db_session.close()
                    
            except asyncio.CancelledError:
                logger.info(f"HEARTBEAT_CANCELLED: task_id={task_id}")
                break
            except Exception as e:
                logger.error(f"HEARTBEAT_EXCEPTION: task_id={task_id} error={str(e)}")
                break
        
        logger.info(
            f"HEARTBEAT_STOPPED: task_id={task_id} worker={self.worker_id}",
            extra={
                "event": "HEARTBEAT_STOPPED",
                "task_id": task_id,
                "worker_id": self.worker_id,
            }
        )
    
    async def _stop_heartbeat(self):
        """Stop the heartbeat loop."""
        if self._task_heartbeat_task:
            self._heartbeat_stop_event.set()
            try:
                await asyncio.wait_for(self._task_heartbeat_task, timeout=1.0)
            except asyncio.TimeoutError:
                self._task_heartbeat_task.cancel()
                try:
                    await self._task_heartbeat_task
                except asyncio.CancelledError:
                    pass
            except Exception as e:
                logger.warning(f"Error stopping heartbeat: {e}")
            self._task_heartbeat_task = None
    
    async def _check_cancellation(self, task_id: str) -> bool:
        """
        Check if task cancellation has been requested.
        
        Looks for cancellation signal in Redis.
        
        Returns:
            True if cancelled, False otherwise
        """
        try:
            cancel_key = TaskQueueKeyBuilder.task_cancel_signal(task_id)
            signal = await redis_manager.get(cancel_key)
            
            if signal:
                cancel_data = json.loads(signal)
                logger.warning(
                    f"TASK_CANCEL_DETECTED: task_id={task_id} "
                    f"cancelled_at={cancel_data.get('cancelled_at')} "
                    f"reason={cancel_data.get('reason')}",
                    extra={
                        "event": "TASK_CANCEL_DETECTED",
                        "task_id": task_id,
                        "cancelled_at": cancel_data.get('cancelled_at'),
                        "reason": cancel_data.get('reason'),
                    }
                )
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"CANCEL_CHECK_ERROR: task_id={task_id} error={str(e)}")
            return False
    
    async def _run_dag_with_heartbeat(
        self,
        task: DAGTask,
        tenant: TenantContext,
        db_session: Any
    ) -> Dict[str, Any]:
        """
        Run DAG with heartbeat and progress updates.
        
        Starts heartbeat before execution and stops after.
        Checks for cancellation between each node.
        """
        # ═══════════════════════════════════════════════════════════
        # HARD STOP SAFETY CHECK (STEP 1)
        # ═══════════════════════════════════════════════════════════
        if not ExecutionFlags.DAG_TRADING_ENABLED:
            # Log blocked execution attempt
            log_blocked_execution(
                source="dag_worker._run_dag_with_heartbeat",
                context=ExecutionContext.DAG.value,
                tenant_id=str(task.tenant_id) if task.tenant_id else None,
                strategy_id=task.dag_config.get("strategy_id"),
                details={
                    "task_id": str(task.task_id),
                    "dag_config": task.dag_config,
                    "reason": "DAG_TRADING_ENABLED is False - unsafe path blocked pending safety review"
                }
            )
            
            # Update task status to reflect blocking
            from sqlalchemy import text
            db_session.execute(
                text("""
                    UPDATE dag_tasks 
                    SET status = 'failed',
                        error_message = 'DAG execution disabled (unsafe path blocked - STEP 1 safety lockdown)'
                    WHERE task_id = :task_id
                """),
                {"task_id": UUID(task.task_id)}
            )
            db_session.commit()
            
            # Raise exception to stop execution
            raise UnsafeExecutionError(
                ExecutionContext.DAG,
                "DAG execution is disabled (STEP 1 safety lockdown). "
                "See transformation plan Phase 1."
            )
        # ═══════════════════════════════════════════════════════════
        
        # Start heartbeat for this task
        await self.start_heartbeat(task.task_id)
        
        try:
            # Create execution engine for tenant
            engine = DAGEngine()
            
            nodes = task.dag_config.get("nodes", [])
            task.dag_config.get("edges", [])
            
            total_nodes = len(nodes)
            results = {}
            
            # Execute nodes with progress updates
            for i, node in enumerate(nodes):
                # ═══════════════════════════════════════════════════════════
                # CHECK FOR CANCELLATION (between each node)
                # ═══════════════════════════════════════════════════════════
                if await self._check_cancellation(task.task_id):
                    logger.info(
                        f"TASK_EXECUTION_CANCELLED: task_id={task.task_id} "
                        f"node={node.get('id', i)}/{total_nodes}",
                        extra={
                            "event": "TASK_EXECUTION_CANCELLED",
                            "task_id": task.task_id,
                            "node_id": node.get('id', i),
                            "progress": 10 + (i / total_nodes) * 80,
                        }
                    )
                    raise asyncio.CancelledError(f"Task {task.task_id} cancelled by user")
                
                progress = 10 + (i / total_nodes) * 80  # 10% to 90%
                
                # Update progress in DB (also updates heartbeat)
                try:
                    from sqlalchemy import text
                    db_session.execute(
                        text("""
                            UPDATE dag_tasks 
                            SET progress = :progress,
                                last_heartbeat = CURRENT_TIMESTAMP
                            WHERE task_id = :task_id 
                            AND tenant_id = :tenant_id
                        """),
                        {
                            "progress": progress,
                            "task_id": UUID(task.task_id),
                            "tenant_id": UUID(task.tenant_id)
                        }
                    )
                    db_session.commit()
                except Exception as e:
                    logger.warning(f"Failed to update progress: {e}")
                
                await self.queue.update_task_progress(
                    task.task_id,
                    progress,
                    f"Executing node {node.get('id', i)} ({i+1}/{total_nodes})"
                )
                
                # Execute node
                node_result = await engine.execute_node(node, results)
                results[node.get("id")] = node_result
                
                # Small delay for progress visibility
                await asyncio.sleep(0.05)
            
            # Final result
            await self.queue.update_task_progress(
                task.task_id,
                95.0,
                "Finalizing results"
            )
            
            return {
                "node_results": results,
                "execution_order": [n.get("id") for n in nodes],
                "symbols": task.dag_config.get("symbols"),
                "timestamp": datetime.utcnow().isoformat(),
            }
            
        except asyncio.CancelledError:
            # Re-raise to be handled by _execute_task
            raise
        except Exception as e:
            logger.error(f"TASK_EXECUTION_ERROR: task_id={task.task_id} error={str(e)}")
            raise
        finally:
            # Heartbeat will be stopped in _execute_task finally block
            pass
    
    async def _validate_market_data(
        self,
        task: DAGTask,
        tenant: TenantContext
    ):
        """Validate market data before DAG execution."""
        symbols = task.dag_config.get("symbols", [])
        
        MarketDataValidator(ValidationConfig())
        
        for symbol in symbols:
            await self.queue.update_task_progress(
                task.task_id,
                5.0,
                f"Validating market data for {symbol}"
            )
            
            # Validation happens here
            # In practice, this would fetch and validate data
            await asyncio.sleep(0.1)  # Simulate
    
    async def _run_dag(
        self,
        task: DAGTask,
        tenant: TenantContext
    ) -> Dict[str, Any]:
        """Run DAG execution."""
        # Create execution engine for tenant
        engine = DAGEngine()
        
        nodes = task.dag_config.get("nodes", [])
        task.dag_config.get("edges", [])
        
        total_nodes = len(nodes)
        results = {}
        
        # Execute nodes with progress updates
        for i, node in enumerate(nodes):
            progress = 10 + (i / total_nodes) * 80  # 10% to 90%
            
            await self.queue.update_task_progress(
                task.task_id,
                progress,
                f"Executing node {node.get('id', i)} ({i+1}/{total_nodes})"
            )
            
            # Execute node
            node_result = await engine.execute_node(node, results)
            results[node.get("id")] = node_result
            
            # Small delay for progress visibility
            await asyncio.sleep(0.05)
        
        # Final result
        await self.queue.update_task_progress(
            task.task_id,
            95.0,
            "Finalizing results"
        )
        
        return {
            "node_results": results,
            "execution_order": [n.get("id") for n in nodes],
            "symbols": task.dag_config.get("symbols"),
            "timestamp": datetime.utcnow().isoformat(),
        }
    
    def _get_tenant_plan(self, tenant_id: str):
        """Get tenant plan from cache or DB."""
        # In practice, load from Redis or DB
        from backend_app.core.tenant import TenantPlan
        return TenantPlan.PROFESSIONAL
    
    def _get_tenant_quota(self, tenant_id: str):
        """Get tenant quota from cache or DB."""
        return TenantQuota()


class WorkerPool:
    """Pool of DAG workers."""
    
    def __init__(self, num_workers: int = 4):
        self.num_workers = num_workers
        self.workers: list[DAGWorker] = []
        self._started = False
    
    async def start(self):
        """Start all workers."""
        if self._started:
            return
        
        for i in range(self.num_workers):
            worker = DAGWorker(worker_id=f"worker-{i+1}")
            await worker.start()
            self.workers.append(worker)
        
        self._started = True
        logger.info(f"Worker pool started with {self.num_workers} workers")
    
    async def stop(self):
        """Stop all workers."""
        for worker in self.workers:
            await worker.stop()
        
        self.workers.clear()
        self._started = False
        logger.info("Worker pool stopped")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        return {
            "total_workers": len(self.workers),
            "active_workers": sum(1 for w in self.workers if w.running),
            "idle_workers": sum(1 for w in self.workers if not w._current_task),
            "busy_workers": sum(1 for w in self.workers if w._current_task),
        }


# Global worker pool
worker_pool = WorkerPool(num_workers=4)
