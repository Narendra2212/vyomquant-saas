"""
backend/portfolio_worker.py — PORTFOLIO UPDATE WORKER

STEP 4: DAG TASK QUEUE SCALING

Dedicated worker for portfolio calculations and PnL updates.
- Processes position updates, portfolio snapshots, margin calculations
- Concurrency: 5 concurrent portfolio operations
- Priority: Medium (portfolio updates are important but not time-critical)

USAGE:
    from backend_app.backend.portfolio_worker import PortfolioWorker
    
    worker = PortfolioWorker(
        worker_id="portfolio-1",
        concurrency=5,
        poll_interval=1.0
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
from backend_app.backend.position_engine import PositionEngine
from backend_app.core.cache import redis_manager as cache_manager

logger = logging.getLogger("PortfolioWorker")

# =============================================================================
# CONFIGURATION
# =============================================================================

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/1")  # DB 1 for queue
QUEUE_NAME = "portfolio:updates"  # Dedicated queue for portfolio tasks
PRIORITY_QUEUE = "portfolio:updates:priority"  # High-priority portfolio tasks


class PortfolioTaskType(str, Enum):
    """Types of portfolio tasks."""
    UPDATE_POSITION_PNL = "UPDATE_POSITION_PNL"
    CALCULATE_PORTFOLIO_SNAPSHOT = "CALCULATE_PORTFOLIO_SNAPSHOT"
    UPDATE_MARGIN_REQUIREMENTS = "UPDATE_MARGIN_REQUIREMENTS"
    RECONCILE_POSITIONS = "RECONCILE_POSITIONS"
    GENERATE_PNL_REPORT = "GENERATE_PNL_REPORT"


class PortfolioWorker:
    """
    Dedicated worker for portfolio calculations and PnL updates.
    
    Features:
    - Concurrency: 5 concurrent portfolio operations
    - Medium polling interval (1s)
    - Position PnL calculations
    - Portfolio snapshot generation
    - Margin requirement updates
    """
    
    def __init__(
        self,
        worker_id: Optional[str] = None,
        concurrency: int = 5,
        poll_interval_seconds: float = 1.0,
        max_idle_seconds: float = 60.0,
    ):
        self.worker_id = worker_id or f"portfolio-{os.getpid()}"
        self.concurrency = concurrency
        self.poll_interval = poll_interval_seconds
        self.max_idle_seconds = max_idle_seconds
        
        self._redis: Optional[aioredis.Redis] = None
        self._position_engine: Optional[PositionEngine] = None
        self._event_publisher = None
        
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._semaphore = asyncio.Semaphore(concurrency)
        
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
        logger.info(f"[PortfolioWorker {self.worker_id}] Initializing...")
        
        # Connect to Redis (DB 1 - queue database)
        self._redis = await aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True
        )
        
        # Initialize position engine
        self._position_engine = PositionEngine()
        
        # Event publisher for notifications
        self._event_publisher = await get_event_publisher()
        
        logger.info(
            f"[PortfolioWorker {self.worker_id}] Ready "
            f"(concurrency={self.concurrency}, poll={self.poll_interval}s)"
        )
    
    async def close(self):
        """Clean shutdown."""
        logger.info(f"[PortfolioWorker {self.worker_id}] Shutting down...")
        self._running = False
        self._shutdown_event.set()
        
        # Wait for active tasks to complete
        if self._active_tasks:
            logger.info(f"Waiting for {len(self._active_tasks)} active tasks...")
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
        
        if self._redis:
            await self._redis.close()
        
        logger.info(f"[PortfolioWorker {self.worker_id}] Shutdown complete")
    
    async def start(self):
        """Start the worker loop."""
        if not self._redis:
            await self.initialize()
        
        self._running = True
        logger.info(f"[PortfolioWorker {self.worker_id}] Started")
        
        # Start multiple consumer tasks for concurrency
        consumer_tasks = [
            asyncio.create_task(self._consumer_loop(), name=f"consumer-{i}")
            for i in range(self.concurrency)
        ]
        
        # Start stats reporter
        stats_task = asyncio.create_task(self._stats_reporter())
        
        # Wait for shutdown
        await self._shutdown_event.wait()
        
        # Cancel all consumers
        for task in consumer_tasks:
            task.cancel()
        stats_task.cancel()
        
        try:
            await asyncio.gather(*consumer_tasks, stats_task, return_exceptions=True)
        except asyncio.CancelledError:
            pass
    
    async def _consumer_loop(self):
        """Main consumer loop - claims and executes tasks."""
        while self._running and not self._shutdown_event.is_set():
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
                        task_future.add_done_callback(
                            lambda f: self._active_tasks.discard(f)
                        )
                else:
                    # No tasks, wait before polling again
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self.poll_interval
                    )
                    
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                logger.error(f"[PortfolioWorker {self.worker_id}] Consumer error: {e}")
                await asyncio.sleep(self.poll_interval)
    
    async def _claim_task(self) -> Optional[Dict[str, Any]]:
        """Claim a task from the queue (priority first, then normal)."""
        try:
            # Try priority queue first
            result = await self._redis.brpop(PRIORITY_QUEUE, timeout=1)
            if result:
                _, task_json = result
                return json.loads(task_json)
            
            # Try normal queue
            result = await self._redis.brpop(QUEUE_NAME, timeout=1)
            if result:
                _, task_json = result
                return json.loads(task_json)
                
        except Exception as e:
            logger.error(f"Error claiming task: {e}")
        
        return None
    
    async def _execute_task(self, task_data: Dict[str, Any]):
        """Execute a portfolio task."""
        task_id = task_data.get("task_id", "unknown")
        task_type = task_data.get("task_type")
        tenant_id = task_data.get("tenant_id")
        
        start_time = datetime.utcnow()
        
        try:
            logger.debug(f"[PortfolioWorker] Executing {task_type} for tenant {tenant_id}")
            
            # Execute based on task type
            if task_type == PortfolioTaskType.UPDATE_POSITION_PNL:
                await self._update_position_pnl(task_data)
            elif task_type == PortfolioTaskType.CALCULATE_PORTFOLIO_SNAPSHOT:
                await self._calculate_portfolio_snapshot(task_data)
            elif task_type == PortfolioTaskType.UPDATE_MARGIN_REQUIREMENTS:
                await self._update_margin_requirements(task_data)
            elif task_type == PortfolioTaskType.RECONCILE_POSITIONS:
                await self._reconcile_positions(task_data)
            elif task_type == PortfolioTaskType.GENERATE_PNL_REPORT:
                await self._generate_pnl_report(task_data)
            else:
                logger.warning(f"Unknown task type: {task_type}")
            
            # Update stats
            execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            self._stats["tasks_processed"] += 1
            self._stats["tasks_succeeded"] += 1
            self._update_avg_execution_time(execution_time)
            
            logger.debug(f"[PortfolioWorker] Task {task_id} completed in {execution_time:.2f}ms")
            
        except Exception as e:
            self._stats["tasks_processed"] += 1
            self._stats["tasks_failed"] += 1
            logger.error(f"[PortfolioWorker] Task {task_id} failed: {e}")
    
    async def _update_position_pnl(self, task_data: Dict[str, Any]):
        """Update position PnL based on current market price."""
        tenant_id = task_data.get("tenant_id")
        position_id = task_data.get("position_id")
        current_price = task_data.get("current_price")
        
        # Update position unrealized PnL
        if self._position_engine:
            await self._position_engine.update_position_pnl(
                tenant_id=tenant_id,
                position_id=position_id,
                current_price=current_price
            )
        
        # Publish PnL update event
        if self._event_publisher:
            await self._event_publisher.publish_pnl_update(
                tenant_id=tenant_id,
                position_id=position_id,
                unrealized_pnl=task_data.get("unrealized_pnl", 0)
            )
    
    async def _calculate_portfolio_snapshot(self, task_data: Dict[str, Any]):
        """Calculate portfolio snapshot for a tenant."""
        tenant_id = task_data.get("tenant_id")
        
        # Calculate total equity, margin used, available balance
        if self._position_engine:
            snapshot = await self._position_engine.calculate_portfolio_snapshot(
                tenant_id=tenant_id
            )
            
            # Cache snapshot for quick access
            cache_key = f"portfolio:snapshot:{tenant_id}"
            await cache_manager.setex(
                cache_key,
                300,  # 5 minute TTL
                json.dumps(snapshot)
            )
    
    async def _update_margin_requirements(self, task_data: Dict[str, Any]):
        """Update margin requirements for open positions."""
        tenant_id = task_data.get("tenant_id")
        
        if self._position_engine:
            await self._position_engine.update_margin_requirements(tenant_id=tenant_id)
    
    async def _reconcile_positions(self, task_data: Dict[str, Any]):
        """Reconcile positions with exchange data."""
        tenant_id = task_data.get("tenant_id")
        exchange_id = task_data.get("exchange_id")
        
        if self._position_engine:
            await self._position_engine.reconcile_positions(
                tenant_id=tenant_id,
                exchange_id=exchange_id
            )
    
    async def _generate_pnl_report(self, task_data: Dict[str, Any]):
        """Generate PnL report for a time period."""
        tenant_id = task_data.get("tenant_id")
        start_time = task_data.get("start_time")
        end_time = task_data.get("end_time")
        
        if self._position_engine:
            report = await self._position_engine.generate_pnl_report(
                tenant_id=tenant_id,
                start_time=start_time,
                end_time=end_time
            )
            
            # Store report in cache
            cache_key = f"portfolio:pnl_report:{tenant_id}:{start_time}:{end_time}"
            await cache_manager.setex(
                cache_key,
                3600,  # 1 hour TTL
                json.dumps(report)
            )
    
    def _update_avg_execution_time(self, execution_time_ms: float):
        """Update rolling average execution time."""
        n = self._stats["tasks_processed"]
        if n > 0:
            current_avg = self._stats["avg_execution_time_ms"]
            self._stats["avg_execution_time_ms"] = (
                (current_avg * (n - 1) + execution_time_ms) / n
            )
    
    async def _stats_reporter(self):
        """Periodically report worker stats."""
        while self._running and not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=60.0  # Report every minute
                )
            except asyncio.TimeoutError:
                logger.info(
                    f"[PortfolioWorker {self.worker_id}] Stats: "
                    f"processed={self._stats['tasks_processed']}, "
                    f"success={self._stats['tasks_succeeded']}, "
                    f"failed={self._stats['tasks_failed']}, "
                    f"avg_time={self._stats['avg_execution_time_ms']:.2f}ms"
                )


# =============================================================================
# WORKER RUNNER (for running standalone)
# =============================================================================

async def run_portfolio_worker():
    """Run a portfolio worker instance."""
    worker = PortfolioWorker(
        worker_id=os.getenv("WORKER_ID", f"portfolio-{os.getpid()}"),
        concurrency=int(os.getenv("WORKER_CONCURRENCY", "5")),
        poll_interval_seconds=float(os.getenv("WORKER_POLL_INTERVAL", "1.0"))
    )
    
    # Handle shutdown signals
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(worker.close()))
    
    await worker.start()


if __name__ == "__main__":
    import signal
    asyncio.run(run_portfolio_worker())
