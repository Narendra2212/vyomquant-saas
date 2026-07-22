"""
Distributed Execution Orchestrator

Institutional-grade execution orchestration that coordinates
strategy signals, risk validation, queue management, and worker pools.

Author: Principal Distributed Trading Systems Architect
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from backend_app.backend.exchange_telemetry import exchange_telemetry

from .execution_worker import ExchangeGateway, WorkerPool
from .job_persistence import job_persistence
from .queue_manager import (ExecutionJob, JobPriority, JobStatus, QueueType,
                            queue_manager)

logger = logging.getLogger("execution_orchestrator")


class ExecutionOrchestrator:
    """Main orchestrator for distributed execution system."""
    
    def __init__(self):
        self.worker_pool: Optional[WorkerPool] = None
        self.exchange_gateways: Dict[str, ExchangeGateway] = {}
        self.running = False
        
        # Configuration
        self.worker_count = 5
        self.max_queue_size = 10000
        self.heartbeat_interval = 30
        self.retry_interval = 60
        self.cleanup_interval = 300  # 5 minutes
        
        # Background tasks
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.retry_task: Optional[asyncio.Task] = None
        self.cleanup_task: Optional[asyncio.Task] = None
        self.dead_letter_task: Optional[asyncio.Task] = None
        
        # Statistics
        self.stats = {
            "jobs_submitted": 0,
            "jobs_completed": 0,
            "jobs_failed": 0,
            "jobs_retried": 0,
            "jobs_dead_lettered": 0,
            "workers_active": 0,
            "queue_depth": 0,
            "start_time": time.time()
        }
    
    async def initialize(self, exchange_configs: Dict[str, Dict[str, Any]]):
        """Initialize the orchestrator with exchange configurations."""
        logger.info("Initializing distributed execution orchestrator")
        
        # Create exchange gateways
        for exchange_name, config in exchange_configs.items():
            gateway = ExchangeGateway(exchange_name)
            self.exchange_gateways[exchange_name] = gateway
            logger.info(f"Created gateway for exchange: {exchange_name}")
        
        # Create worker pool
        self.worker_pool = WorkerPool(self.worker_count, self.exchange_gateways)
        
        logger.info(f"Initialized orchestrator with {len(self.exchange_gateways)} exchanges and {self.worker_count} workers")
    
    async def start(self):
        """Start the distributed execution system."""
        logger.info("Starting distributed execution orchestrator")
        
        try:
            # Start worker pool
            await self.worker_pool.start()
            self.stats["workers_active"] = self.worker_count
            
            # Start background tasks
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self.retry_task = asyncio.create_task(self._retry_loop())
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())
            self.dead_letter_task = asyncio.create_task(self._dead_letter_loop())
            
            self.running = True
            logger.info("Distributed execution orchestrator started successfully")
            
        except Exception as e:
            logger.error(f"Failed to start orchestrator: {e}")
            await self.stop()
            raise
    
    async def stop(self):
        """Stop the distributed execution system."""
        logger.info("Stopping distributed execution orchestrator")
        
        self.running = False
        
        # Cancel background tasks
        tasks = [self.heartbeat_task, self.retry_task, self.cleanup_task, self.dead_letter_task]
        for task in tasks:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # Stop worker pool
        if self.worker_pool:
            await self.worker_pool.stop()
        
        self.stats["workers_active"] = 0
        logger.info("Distributed execution orchestrator stopped")
    
    async def submit_execution_job(self, tenant_id: str, strategy_id: str, bot_id: str,
                                  signal_id: str, exchange: str, symbol: str, side: str,
                                  order_type: str, quantity: Decimal, price: Optional[Decimal] = None,
                                  priority: JobPriority = JobPriority.NORMAL,
                                  idempotency_key: Optional[str] = None) -> str:
        """Submit execution job to the distributed queue."""
        
        # Validate inputs
        if exchange not in self.exchange_gateways:
            raise ValueError(f"Exchange not supported: {exchange}")
        
        # Create job
        job = ExecutionJob()
        job.tenant_id = tenant_id
        job.strategy_id = strategy_id
        job.bot_id = bot_id
        job.signal_id = signal_id
        job.exchange = exchange
        job.symbol = symbol
        job.side = side
        job.order_type = order_type
        job.quantity = quantity
        job.price = price
        job.priority = priority
        job.idempotency_key = idempotency_key
        
        # Save to persistence
        await job_persistence.save_job(job)
        
        # Submit to queue
        success = await queue_manager.publish_execution_job(job)
        if not success:
            raise Exception("Failed to submit job to execution queue")
        
        # Update statistics
        self.stats["jobs_submitted"] += 1
        
        # Emit telemetry
        await exchange_telemetry.on_order_execution(
            exchange=exchange,
            symbol=symbol,
            order_id=job.job_id,  # Use job_id as temporary order ID
            bot_id=bot_id,
            signal_id=signal_id,
            side=side,
            size=quantity,
            price=price or Decimal("0"),
            filled_size=Decimal("0"),
            fees=Decimal("0"),
            slippage=Decimal("0"),
            latency_ms=0,
            tenant_id=tenant_id
        )
        
        logger.info(f"Submitted execution job {job.job_id} for {tenant_id}")
        return job.job_id
    
    async def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job status from persistence."""
        job = await job_persistence.load_job(job_id)
        if not job:
            return None
        
        return {
            "job_id": job.job_id,
            "status": job.status.value,
            "worker_id": job.worker_id,
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "order_id": job.order_id,
            "execution_price": str(job.execution_price) if job.execution_price else None,
            "executed_quantity": str(job.executed_quantity) if job.executed_quantity else None,
            "fees": str(job.fees) if job.fees else None,
            "error": job.error,
            "retry_count": job.retry_count
        }
    
    async def get_tenant_jobs(self, tenant_id: str, status: Optional[JobStatus] = None,
                             limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Get jobs for a tenant."""
        jobs = await job_persistence.get_jobs_by_tenant(tenant_id, status, limit, offset)
        
        return [
            {
                "job_id": job.job_id,
                "strategy_id": job.strategy_id,
                "bot_id": job.bot_id,
                "signal_id": job.signal_id,
                "exchange": job.exchange,
                "symbol": job.symbol,
                "side": job.side,
                "order_type": job.order_type,
                "quantity": str(job.quantity),
                "price": str(job.price) if job.price else None,
                "status": job.status.value,
                "created_at": job.created_at.isoformat(),
                "updated_at": job.updated_at.isoformat(),
                "order_id": job.order_id,
                "error": job.error,
                "retry_count": job.retry_count
            }
            for job in jobs
        ]
    
    async def get_system_status(self) -> Dict[str, Any]:
        """Get overall system status."""
        # Get queue depth
        queue_depth = 0
        try:
            # This would need to be implemented in the queue backend
            queue_depth = 0  # Placeholder
        except Exception:
            pass
        
        # Get worker pool status
        worker_status = {}
        if self.worker_pool:
            worker_status = self.worker_pool.get_pool_status()
        
        # Get job statistics
        job_stats = await job_persistence.get_job_statistics()
        
        return {
            "running": self.running,
            "workers": worker_status,
            "queue_depth": queue_depth,
            "stats": self.stats.copy(),
            "job_statistics": job_stats,
            "uptime": time.time() - self.stats["start_time"],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def replay_job(self, job_id: str) -> Optional[str]:
        """Replay a failed job."""
        replay_job = await job_persistence.replay_job(job_id)
        if not replay_job:
            return None
        
        # Submit replay job
        success = await queue_manager.publish_execution_job(replay_job)
        if not success:
            raise Exception("Failed to submit replay job")
        
        logger.info(f"Replayed job {job_id} as {replay_job.job_id}")
        return replay_job.job_id
    
    async def _heartbeat_loop(self):
        """Periodic heartbeat monitoring."""
        while self.running:
            try:
                # Update queue depth
                # This would need to be implemented in the queue backend
                
                # Emit system heartbeat
                heartbeat_data = {
                    "orchestrator_id": "main",
                    "running": self.running,
                    "workers_active": self.stats["workers_active"],
                    "stats": self.stats.copy(),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                
                # Publish heartbeat
                heartbeat_job = ExecutionJob()
                heartbeat_job.job_id = f"orchestrator_heartbeat_{int(time.time())}"
                heartbeat_job.tenant_id = "system"
                heartbeat_job.payload = heartbeat_data
                
                await queue_manager.backend.publish(queue_manager.backend.QueueType.HEARTBEAT, heartbeat_job)
                
                await asyncio.sleep(self.heartbeat_interval)
                
            except Exception as e:
                logger.error(f"Heartbeat loop error: {e}")
                await asyncio.sleep(10)
    
    async def _retry_loop(self):
        """Process retry queue."""
        while self.running:
            try:
                # Consume from retry queue
                retry_consumer_id = "retry_processor"
                retry_job = await queue_manager.backend.consume(
                    QueueType.RETRY, 
                    "retry_processors", 
                    retry_consumer_id
                )
                
                if retry_job:
                    # Calculate retry delay
                    delay = retry_job.retry_delay * (2 ** (retry_job.retry_count - 1))
                    delay = min(delay, 300)  # Max 5 minutes
                    
                    logger.info(f"Retrying job {retry_job.job_id} after {delay}s delay")
                    
                    # Wait for retry delay
                    await asyncio.sleep(delay)
                    
                    # Resubmit to main queue
                    retry_job.status = JobStatus.PENDING
                    retry_job.updated_at = datetime.now(timezone.utc)
                    
                    success = await queue_manager.publish_execution_job(retry_job)
                    if success:
                        await queue_manager.backend.acknowledge(
                            QueueType.RETRY, 
                            "retry_processors", 
                            retry_job._message_id
                        )
                        self.stats["jobs_retried"] += 1
                    else:
                        logger.error(f"Failed to resubmit retry job {retry_job.job_id}")
                else:
                    await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Retry loop error: {e}")
                await asyncio.sleep(5)
    
    async def _cleanup_loop(self):
        """Periodic cleanup of expired data."""
        while self.running:
            try:
                # Clean up expired jobs
                cleaned_count = await job_persistence.cleanup_expired_jobs()
                if cleaned_count > 0:
                    logger.info(f"Cleaned up {cleaned_count} expired jobs")
                
                await asyncio.sleep(self.cleanup_interval)
                
            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")
                await asyncio.sleep(60)
    
    async def _dead_letter_loop(self):
        """Process dead-letter queue and alert on critical failures."""
        while self.running:
            try:
                # Consume from dead-letter queue
                dlq_consumer_id = "dlq_processor"
                dlq_job = await queue_manager.backend.consume(
                    QueueType.DEAD_LETTER, 
                    "dlq_processors", 
                    dlq_consumer_id
                )
                
                if dlq_job:
                    logger.error(f"Job {dlq_job.job_id} moved to dead-letter: {dlq_job.error}")
                    
                    # Emit critical alert
                    await exchange_telemetry.on_order_rejected(
                        exchange=dlq_job.exchange,
                        symbol=dlq_job.symbol,
                        order_id=dlq_job.job_id,
                        bot_id=dlq_job.bot_id,
                        signal_id=dlq_job.signal_id,
                        reason=f"Dead-letter: {dlq_job.error}",
                        error_code="DEAD_LETTER",
                        tenant_id=dlq_job.tenant_id
                    )
                    
                    # Acknowledge dead-letter job
                    await queue_manager.backend.acknowledge(
                        QueueType.DEAD_LETTER, 
                        "dlq_processors", 
                        dlq_job._message_id
                    )
                    
                    self.stats["jobs_dead_lettered"] += 1
                else:
                    await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Dead-letter loop error: {e}")
                await asyncio.sleep(5)


# Global orchestrator instance
execution_orchestrator = ExecutionOrchestrator()
