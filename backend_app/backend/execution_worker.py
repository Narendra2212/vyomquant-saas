"""
backend/execution_worker.py — ORDER EXECUTION WORKER

STEP 4: DAG TASK QUEUE SCALING

Dedicated worker for order execution tasks.
- Processes order placement, cancellation, modification
- Concurrency: 5 concurrent executions
- Priority: High (orders are time-sensitive)

USAGE:
    from backend_app.backend.execution_worker import ExecutionWorker
    
    worker = ExecutionWorker(
        worker_id="execution-1",
        concurrency=5,
        poll_interval=0.5  # Fast polling for orders
    )
    await worker.start()
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

import redis.asyncio as aioredis

from backend_app.backend.event_publisher import get_event_publisher
from backend_app.core.execution_engine import ExecutionEngine

logger = logging.getLogger("ExecutionWorker")

# =============================================================================
# CONFIGURATION
# =============================================================================

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/1")  # DB 1 for queue
QUEUE_NAME = "execution:orders"  # Dedicated queue for execution tasks
PRIORITY_QUEUE = "execution:orders:priority"  # High-priority orders


class ExecutionTaskType(str, Enum):
    """Types of execution tasks."""
    PLACE_ORDER = "PLACE_ORDER"
    CANCEL_ORDER = "CANCEL_ORDER"
    MODIFY_ORDER = "MODIFY_ORDER"
    CLOSE_POSITION = "CLOSE_POSITION"
    EMERGENCY_LIQUIDATE = "EMERGENCY_LIQUIDATE"


from backend_app.core.worker_base import WorkerBase

class ExecutionWorker(WorkerBase):
    """
    Dedicated worker for order execution.
    
    Features:
    - High concurrency (5 concurrent executions)
    - Fast polling (500ms)
    - Priority queue support
    - Idempotency protection
    - Circuit breaker integration
    """
    
    def __init__(
        self,
        worker_id: Optional[str] = None,
        concurrency: int = 5,
        poll_interval_seconds: float = 0.5,
        max_idle_seconds: float = 30.0,
    ):
        name = worker_id or f"execution-{os.getpid()}"
        super().__init__(worker_name=name, poll_interval=60.0) # Stats interval
        self.worker_id = name
        self.concurrency = concurrency
        self.consumer_poll_interval = poll_interval_seconds
        self.max_idle_seconds = max_idle_seconds
        
        self._redis: Optional[aioredis.Redis] = None
        self._execution_engine: Optional[ExecutionEngine] = None
        self._event_publisher = None
        
        self._shutdown_event = asyncio.Event()
        self._semaphore = asyncio.Semaphore(concurrency)
        self.consumer_tasks = []
        
        self._stats = {
            "tasks_processed": 0,
            "tasks_failed": 0,
            "tasks_succeeded": 0,
            "avg_execution_time_ms": 0.0,
        }
        
        self._active_tasks: set = set()
        self._last_activity = datetime.utcnow()
    
    async def initialize(self):
        """Initialize worker connections."""
        logger.info(f"[ExecutionWorker {self.worker_id}] Initializing...")
        
        # Connect to Redis (DB 1 - queue database)
        self._redis = await aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True
        )
        
        # Initialize execution engine
        self._execution_engine = ExecutionEngine()
        
        # Event publisher for notifications
        self._event_publisher = await get_event_publisher()
        
        logger.info(
            f"[ExecutionWorker {self.worker_id}] Ready "
            f"(concurrency={self.concurrency}, poll={self.poll_interval}s)"
        )
    
    async def cleanup(self):
        """Clean shutdown."""
        logger.info(f"[ExecutionWorker {self.worker_id}] Shutting down consumers...")
        self._shutdown_event.set()
        
        # Cancel consumers
        for task in self.consumer_tasks:
            task.cancel()
            
        try:
            if self.consumer_tasks:
                await asyncio.gather(*self.consumer_tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass
            
        # Wait for active tasks to complete
        if self._active_tasks:
            logger.info(f"Waiting for {len(self._active_tasks)} active tasks...")
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
        
        if self._redis:
            await self._redis.close()
        
        logger.info(f"[ExecutionWorker {self.worker_id}] Shutdown complete")
    
    async def start(self):
        """Start the worker loop."""
        if not self._redis:
            await self.initialize()
            
        await super().start()
        
        # Start multiple consumer tasks for concurrency
        self.consumer_tasks = [
            asyncio.create_task(self._consumer_loop(), name=f"consumer-{i}")
            for i in range(self.concurrency)
        ]
    
    async def process_iteration(self):
        """Report worker stats periodically."""
        logger.info(
            f"[ExecutionWorker {self.worker_id}] Stats: "
            f"processed={self._stats['tasks_processed']}, "
            f"success={self._stats['tasks_succeeded']}, "
            f"failed={self._stats['tasks_failed']}, "
            f"avg_time={self._stats['avg_execution_time_ms']:.1f}ms"
        )
    
    async def _consumer_loop(self):
        """Main consumer loop - claims and executes tasks."""
        while self.running and not self._shutdown_event.is_set():
            try:
                # Try priority queue first, then normal queue
                task_data = await self._claim_task()
                
                if task_data:
                    self._last_activity = datetime.utcnow()
                    
                    # Execute with semaphore for concurrency control
                    async with self._semaphore:
                        task_future = asyncio.create_task(
                            self._execute_task(task_data)
                        )
                        self._active_tasks.add(task_future)
                        
                        try:
                            await task_future
                        finally:
                            self._active_tasks.discard(task_future)
                else:
                    # No task available, wait before retry
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self.consumer_poll_interval
                    )
                    
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"[ExecutionWorker {self.worker_id}] Consumer error: {e}")
                await asyncio.sleep(1)
    
    async def _claim_task(self) -> Optional[Dict]:
        """Claim a task from the queue (priority first, then normal)."""
        try:
            # Try priority queue first (blocking pop with 1 second timeout)
            result = await self._redis.brpop(PRIORITY_QUEUE, timeout=1)
            if result:
                _, data = result
                return json.loads(data)
            
            # Try normal queue
            result = await self._redis.brpop(QUEUE_NAME, timeout=1)
            if result:
                _, data = result
                return json.loads(data)
            
            return None
            
        except Exception as e:
            logger.error(f"[ExecutionWorker {self.worker_id}] Claim error: {e}")
            return None
    
    async def _execute_task(self, task: Dict[str, Any]):
        """Execute an order task."""
        task_id = task.get("task_id", "unknown")
        task_type = task.get("task_type")
        tenant_id = task.get("tenant_id")
        
        start_time = datetime.utcnow()
        
        try:
            logger.info(
                f"[ExecutionWorker {self.worker_id}] Executing {task_type} "
                f"for tenant {tenant_id}"
            )
            
            # Check idempotency
            if await self._is_duplicate(task_id):
                logger.info(f"Task {task_id} already processed (duplicate)")
                return
            
            # Execute based on task type
            if task_type == ExecutionTaskType.PLACE_ORDER:
                await self._place_order(task)
            elif task_type == ExecutionTaskType.CANCEL_ORDER:
                await self._cancel_order(task)
            elif task_type == ExecutionTaskType.MODIFY_ORDER:
                await self._modify_order(task)
            elif task_type == ExecutionTaskType.CLOSE_POSITION:
                await self._close_position(task)
            elif task_type == ExecutionTaskType.EMERGENCY_LIQUIDATE:
                await self._emergency_liquidate(task)
            else:
                raise ValueError(f"Unknown task type: {task_type}")
            
            # Mark as executed
            await self._mark_executed(task_id)
            
            # Update stats
            execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            self._update_stats(success=True, execution_time_ms=execution_time)
            
            # Publish success event
            await self._event_publisher.publish_alert(
                tenant_id=tenant_id,
                level="info",
                title=f"Order {task_type} Completed",
                message=f"Task {task_id} executed successfully in {execution_time:.0f}ms"
            )
            
        except Exception as e:
            logger.error(f"[ExecutionWorker {self.worker_id}] Task {task_id} failed: {e}")
            
            self._update_stats(success=False)
            
            # Publish failure event
            await self._event_publisher.publish_alert(
                tenant_id=tenant_id,
                level="error",
                title=f"Order {task_type} Failed",
                message=f"Task {task_id} failed: {str(e)}"
            )
            
            # Re-queue for retry if needed
            retry_count = task.get("retry_count", 0)
            if retry_count < 3:
                task["retry_count"] = retry_count + 1
                await self._redis.lpush(QUEUE_NAME, json.dumps(task))
    
    async def _place_order(self, task: Dict):
        """Execute place order task."""
        params = task.get("params", {})
        
        result = await self._execution_engine.place_order(
            tenant_id=task["tenant_id"],
            exchange_id=params["exchange_id"],
            symbol=params["symbol"],
            side=params["side"],
            size=params["size"],
            price=params.get("price"),
            signal_id=params.get("signal_id", ""),
            order_type=params["order_type"],
        )
        
        if not result.success:
            raise Exception(f"Order placement failed: {result.message}")
        
        return result
    
    async def _cancel_order(self, task: Dict):
        """Execute cancel order task."""
        params = task.get("params", {})
        
        result = await self._execution_engine.cancel_order(
            tenant_id=task["tenant_id"],
            exchange_id=params["exchange_id"],
            order_id=params["order_id"],
        )
        
        if not result.success:
            raise Exception(f"Order cancellation failed: {result.message}")
        
        return result
    
    async def _modify_order(self, task: Dict):
        """Execute modify order task."""
        params = task.get("params", {})
        
        result = await self._execution_engine.modify_order(
            tenant_id=task["tenant_id"],
            exchange_id=params["exchange_id"],
            order_id=params["order_id"],
            new_price=params.get("new_price"),
            new_size=params.get("new_size"),
        )
        
        if not result.success:
            raise Exception(f"Order modification failed: {result.message}")
        
        return result
    
    async def _close_position(self, task: Dict):
        """Execute close position task."""
        params = task.get("params", {})
        
        result = await self._execution_engine.close_position(
            tenant_id=task["tenant_id"],
            exchange_id=params["exchange_id"],
            position_id=params["position_id"],
            size=params.get("size"),  # None = close all
        )
        
        if not result.success:
            raise Exception(f"Position close failed: {result.message}")
        
        return result
    
    async def _emergency_liquidate(self, task: Dict):
        """Execute emergency liquidation."""
        params = task.get("params", {})
        
        result = await self._execution_engine.emergency_liquidate(
            tenant_id=task["tenant_id"],
            exchange_id=params.get("exchange_id"),
            reason=params.get("reason", "circuit_breaker"),
        )
        
        if not result.success:
            raise Exception(f"Emergency liquidation failed: {result.message}")
        
        return result
    
    async def _is_duplicate(self, task_id: str) -> bool:
        """Check if task was already executed (idempotency)."""
        key = f"execution:completed:{task_id}"
        exists = await self._redis.exists(key)
        return bool(exists)
    
    async def _mark_executed(self, task_id: str):
        """Mark task as executed (with 24h TTL)."""
        key = f"execution:completed:{task_id}"
        await self._redis.setex(key, 86400, "1")  # 24 hour TTL
    
    def _update_stats(self, success: bool, execution_time_ms: float = 0):
        """Update worker statistics."""
        self._stats["tasks_processed"] += 1
        
        if success:
            self._stats["tasks_succeeded"] += 1
        else:
            self._stats["tasks_failed"] += 1
        
        # Update rolling average
        n = self._stats["tasks_processed"]
        old_avg = self._stats["avg_execution_time_ms"]
        self._stats["avg_execution_time_ms"] = (old_avg * (n - 1) + execution_time_ms) / n
    
    # process_iteration now handles stats reporting.
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current worker statistics."""
        return {
            "worker_id": self.worker_id,
            "worker_type": "execution",
            "concurrency": self.concurrency,
            **self._stats,
            "active_tasks": len(self._active_tasks),
            "is_running": self.running,
        }


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

async def main():
    """Run execution worker."""
    worker = ExecutionWorker()
    
    # Handle signals
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(worker.close()))
    
    try:
        await worker.start()
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Execution worker error: {e}")
        raise
    finally:
        await worker.stop()


if __name__ == "__main__":
    import signal
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
