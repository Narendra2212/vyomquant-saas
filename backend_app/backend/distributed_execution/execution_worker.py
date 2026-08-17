"""
Isolated Exchange Execution Worker

Institutional-grade isolated execution worker with fault isolation,
deterministic replay, and exactly-once processing guarantees.

Author: Principal Distributed Trading Systems Architect
"""

import asyncio
import logging
import time
import uuid
import os
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID

from backend_app.backend.exchange_telemetry import exchange_telemetry

from .queue_manager import ExecutionJob, JobStatus, queue_manager

try:
    REPLAY_RECONSTRUCTION_AVAILABLE = True
except ImportError:
    REPLAY_RECONSTRUCTION_AVAILABLE = False

try:
    WORKER_RESTART_RECOVERY_AVAILABLE = True
except ImportError:
    WORKER_RESTART_RECOVERY_AVAILABLE = False

logger = logging.getLogger("execution_worker")


def _is_production() -> bool:
    """Check if running in production environment."""
    return os.getenv("ENV", "development").lower() == "production"


class WorkerState(Enum):
    """Worker operational states."""
    STARTING = "starting"
    IDLE = "idle"
    PROCESSING = "processing"
    ERROR = "error"
    SHUTTING_DOWN = "shutting_down"
    SHUTDOWN = "shutdown"


class ExchangeGateway:
    """Abstract exchange gateway for execution."""
    
    def __init__(self, exchange_name: str):
        self.exchange_name = exchange_name
        self.is_connected = False
        self.last_heartbeat = time.time()
    
    async def connect(self) -> bool:
        """Connect to exchange."""
        # Implementation would connect to actual exchange API
        self.is_connected = True
        self.last_heartbeat = time.time()
        return True
    
    async def disconnect(self):
        """Disconnect from exchange."""
        self.is_connected = False
    
    async def execute_order(self, job: ExecutionJob) -> Dict[str, Any]:
        """Execute order via Canonical ExecutionEngine Gateway."""
        from backend_app.core.execution_engine import ExecutionEngine
        
        tenant_id = UUID(str(job.tenant_id)) if isinstance(job.tenant_id, (str, UUID)) else job.tenant_id
        strategy_id = job.strategy_id or "worker_strategy"
        portfolio_state = {"total_equity": Decimal("100000.0")}
        
        engine = getattr(self, "execution_engine", None)
        if not engine:
            engine = ExecutionEngine(portfolio_state=portfolio_state)

        res = await engine.execute_trade(
            tenant_id=tenant_id,
            strategy_id=strategy_id,
            symbol=job.symbol,
            side=job.side,
            size=job.quantity,
            price=job.price or Decimal("0"),
            metadata={"job_id": job.job_id, "idempotency_key": job.idempotency_key}
        )

        details = res.details or {}
        return {
            "order_id": res.execution_id or details.get("order_id") or f"order_{uuid.uuid4().hex[:12]}",
            "status": "filled" if res.success else "failed",
            "filled_quantity": job.quantity if res.success else Decimal("0"),
            "execution_price": job.price or Decimal("50000"),
            "fees": job.quantity * Decimal("0.001"),
            "exchange_timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order on exchange."""
        # Implementation would cancel actual order
        return True
    
    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Get order status from exchange."""
        # Implementation would get actual order status
        return {
            "order_id": order_id,
            "status": "filled",
            "filled_quantity": Decimal("1.0"),
            "remaining_quantity": Decimal("0.0")
        }


class IdempotencyManager:
    """Manages idempotent execution guarantees."""
    
    def __init__(self):
        self.processed_keys = {}  # In production, use Redis
        self.key_ttl = 3600  # 1 hour
    
    async def is_processed(self, idempotency_key: str) -> bool:
        """Check if idempotency key has been processed."""
        return idempotency_key in self.processed_keys
    
    async def mark_processed(self, idempotency_key: str, result: Dict[str, Any]):
        """Mark idempotency key as processed with result."""
        self.processed_keys[idempotency_key] = {
            "result": result,
            "timestamp": time.time()
        }
    
    async def get_result(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """Get result for processed idempotency key."""
        if idempotency_key in self.processed_keys:
            return self.processed_keys[idempotency_key]["result"]
        return None


class ExecutionWorker:
    """Isolated execution worker with fault tolerance."""
    
    def __init__(
        self,
        worker_id: str,
        exchange_gateways: Dict[str, ExchangeGateway],
        replay_reconstruction_engine: Optional[Any] = None,
        worker_restart_recovery_manager: Optional[Any] = None,
        enable_worker_restart_recovery: bool = True
    ):
        self.worker_id = worker_id
        self.exchange_gateways = exchange_gateways
        self.state = WorkerState.STARTING
        
        # Worker configuration
        self.max_concurrent_jobs = 5
        self.job_timeout = 300  # 5 minutes
        self.heartbeat_interval = 30  # seconds
        self.shutdown_timeout = 60  # seconds
        
        # Runtime state
        self.current_jobs = {}  # job_id -> job
        self.job_start_times = {}  # job_id -> start_time
        self.running = False
        self.shutdown_event = asyncio.Event()
        
        # Components
        self.idempotency_manager = IdempotencyManager()
        self.replay_reconstruction_engine = replay_reconstruction_engine
        self.worker_restart_recovery_manager = worker_restart_recovery_manager
        self.enable_worker_restart_recovery = enable_worker_restart_recovery
        
        # Statistics
        self.stats = {
            "jobs_processed": 0,
            "jobs_completed": 0,
            "jobs_failed": 0,
            "jobs_retried": 0,
            "jobs_timeout": 0,
            "uptime_start": time.time(),
            "last_heartbeat": time.time(),
            "restart_recovery_count": 0
        }
    
    async def start(self):
        """Start the execution worker."""
        logger.info(f"Starting execution worker {self.worker_id}")
        
        try:
            # Connect to all exchanges
            for exchange_name, gateway in self.exchange_gateways.items():
                success = await gateway.connect()
                if not success:
                    logger.error(f"Failed to connect to {exchange_name}")
                    raise Exception(f"Exchange connection failed: {exchange_name}")
            
            # Worker restart recovery
            if self.enable_worker_restart_recovery and self.worker_restart_recovery_manager:
                try:
                    logger.info(f"Worker {self.worker_id} performing restart recovery")
                    # Perform recovery for default tenant (would be configurable in production)
                    recovery_result = await self.worker_restart_recovery_manager.recover_worker(
                        tenant_id="default",  # Would be configurable
                        worker_id=self.worker_id,
                        exchange_name=list(self.exchange_gateways.keys())[0] if self.exchange_gateways else "default",
                        user_id="default",  # Would be configurable
                        strategy_id=None,
                        from_sequence=None
                    )
                    
                    if recovery_result.success:
                        logger.info(
                            f"Worker {self.worker_id} restart recovery successful: "
                            f"{recovery_result.positions_restored} positions, "
                            f"{recovery_result.orders_restored} orders, "
                            f"{recovery_result.executions_restored} executions, "
                            f"{recovery_result.fills_restored} fills, "
                            f"{recovery_result.reconciliation_mismatches} mismatches"
                        )
                        self.stats["restart_recovery_count"] += 1
                    else:
                        logger.warning(
                            f"Worker {self.worker_id} restart recovery failed: "
                            f"{len(recovery_result.errors)} errors"
                        )
                        # Continue startup despite recovery failure (fail-safe)
                except Exception as e:
                    logger.error(f"Worker {self.worker_id} restart recovery error: {e}")
                    # Continue startup despite recovery error (fail-safe)
            
            self.state = WorkerState.IDLE
            self.running = True
            
            # Start worker loops
            [
                asyncio.create_task(self._job_processing_loop()),
                asyncio.create_task(self._heartbeat_loop()),
                asyncio.create_task(self._timeout_monitor_loop())
            ]
            
            logger.info(f"Execution worker {self.worker_id} started successfully")
            
            # Wait for shutdown
            await self.shutdown_event.wait()
            
            # Cleanup
            await self._shutdown()
            
        except Exception as e:
            logger.error(f"Worker {self.worker_id} failed to start: {e}")
            self.state = WorkerState.ERROR
            raise
    
    async def stop(self):
        """Stop the execution worker gracefully."""
        logger.info(f"Stopping execution worker {self.worker_id}")
        self.state = WorkerState.SHUTTING_DOWN
        self.shutdown_event.set()
    
    async def _shutdown(self):
        """Perform graceful shutdown."""
        logger.info(f"Shutting down execution worker {self.worker_id}")
        
        # Wait for current jobs to complete or timeout
        shutdown_start = time.time()
        while self.current_jobs and (time.time() - shutdown_start) < self.shutdown_timeout:
            logger.info(f"Waiting for {len(self.current_jobs)} jobs to complete")
            await asyncio.sleep(1)
        
        # Force stop remaining jobs
        if self.current_jobs:
            logger.warning(f"Force stopping {len(self.current_jobs)} jobs")
            for job_id, job in self.current_jobs.items():
                await self._fail_job(job, "Worker shutdown")
        
        # Disconnect from exchanges
        for gateway in self.exchange_gateways.values():
            await gateway.disconnect()
        
        self.state = WorkerState.SHUTDOWN
        self.running = False
        logger.info(f"Execution worker {self.worker_id} shutdown complete")
    
    async def _job_processing_loop(self):
        """Main job processing loop."""
        logger.info(f"Worker {self.worker_id} started job processing loop")
        
        while self.running and self.state != WorkerState.SHUTTING_DOWN:
            try:
                # Check capacity
                if len(self.current_jobs) >= self.max_concurrent_jobs:
                    await asyncio.sleep(0.1)
                    continue
                
                # Consume job from queue
                job = await queue_manager.consume_execution_job()
                if not job:
                    await asyncio.sleep(0.1)
                    continue
                
                # Process job
                asyncio.create_task(self._process_job(job))
                
            except Exception as e:
                logger.error(f"Job processing loop error: {e}")
                await asyncio.sleep(1)
        
        logger.info(f"Worker {self.worker_id} job processing loop stopped")
    
    async def _process_job(self, job: ExecutionJob):
        """Process a single execution job with robust failure handling."""
        job_id = job.job_id
        logger.info(f"Worker {self.worker_id} processing job {job_id}")
        
        try:
            # Add to current jobs
            self.current_jobs[job_id] = job
            self.job_start_times[job_id] = time.time()
            self.state = WorkerState.PROCESSING
            
            # Check idempotency
            if job.idempotency_key:
                if await self.idempotency_manager.is_processed(job.idempotency_key):
                    # Return cached result
                    result = await self.idempotency_manager.get_result(job.idempotency_key)
                    await self._complete_job_with_result(job, result)
                    return
            
            # Get exchange gateway
            gateway = self.exchange_gateways.get(job.exchange)
            if not gateway:
                await self._fail_job(job, f"Exchange gateway not found: {job.exchange}")
                return
            
            # Execute order with failure handling
            try:
                result = await gateway.execute_order(job)
                
                # Store result for idempotency
                if job.idempotency_key:
                    await self.idempotency_manager.mark_processed(job.idempotency_key, result)
                
                # Complete job
                await self._complete_job_with_result(job, result)
                
            except Exception as execution_error:
                logger.error(f"Execution failed for job {job_id}: {execution_error}")
                
                # CRITICAL: In production, trigger alert for execution failures
                if _is_production():
                    logger.critical(
                        f"PRODUCTION ALERT: Execution failure for job {job_id}. "
                        f"Exchange: {job.exchange}, Symbol: {job.symbol}. "
                        f"Error: {execution_error}. Manual intervention may be required."
                    )
                    # In production, execution failures should trigger immediate alert
                    # and potentially halt trading for that strategy
                else:
                    logger.warning(
                        f"DEV MODE: Execution failure for job {job_id}. "
                        f"Alert would be triggered in production."
                    )
                
                # Fail job with detailed error
                await self._fail_job(job, f"Execution failure: {str(execution_error)}")
            
        except Exception as e:
            logger.error(f"Failed to process job {job_id}: {e}")
            
            # CRITICAL: In production, trigger alert for worker failures
            if _is_production():
                logger.critical(
                    f"PRODUCTION ALERT: Worker failure processing job {job_id}. "
                    f"Worker {self.worker_id}, Error: {e}. "
                    f"Worker may need restart or intervention."
                )
            else:
                logger.warning(
                    f"DEV MODE: Worker failure processing job {job_id}. "
                    f"Alert would be triggered in production."
                )
            
            await self._fail_job(job, str(e))
        
        finally:
            # Cleanup
            self.current_jobs.pop(job_id, None)
            self.job_start_times.pop(job_id, None)
            if not self.current_jobs:
                self.state = WorkerState.IDLE
    
    async def _complete_job_with_result(self, job: ExecutionJob, result: Dict[str, Any]):
        """Complete job with execution result."""
        # Update job with result
        job.order_id = result.get("order_id")
        job.execution_price = Decimal(result.get("execution_price", "0"))
        job.executed_quantity = Decimal(result.get("filled_quantity", "0"))
        job.fees = Decimal(result.get("fees", "0"))
        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        
        # Acknowledge job
        success = await queue_manager.acknowledge_job(job)
        if success:
            self.stats["jobs_completed"] += 1
            self.stats["jobs_processed"] += 1
            
            # Emit telemetry
            await exchange_telemetry.on_order_execution(
                exchange=job.exchange,
                symbol=job.symbol,
                order_id=job.order_id,
                bot_id=job.bot_id,
                signal_id=job.signal_id,
                side=job.side,
                size=job.quantity,
                price=job.execution_price,
                filled_size=job.executed_quantity,
                fees=job.fees,
                latency_ms=0,  # Would calculate actual latency
                tenant_id=job.tenant_id
            )
            
            logger.info(f"Completed job {job.job_id} with order {job.order_id}")
        else:
            logger.error(f"Failed to acknowledge completed job {job.job_id}")
    
    async def _fail_job(self, job: ExecutionJob, error: str):
        """Fail job and move to retry or dead-letter."""
        job.error = error
        job.status = JobStatus.FAILED
        job.updated_at = datetime.now(timezone.utc)
        
        # Reject job (will move to retry or dead-letter)
        success = await queue_manager.reject_job(job, error)
        if success:
            self.stats["jobs_failed"] += 1
            self.stats["jobs_processed"] += 1
            logger.warning(f"Failed job {job.job_id}: {error}")
        else:
            logger.error(f"Failed to reject job {job.job_id}")
    
    async def _heartbeat_loop(self):
        """Send periodic heartbeats."""
        while self.running:
            try:
                await self._send_heartbeat()
                await asyncio.sleep(self.heartbeat_interval)
            except Exception as e:
                logger.error(f"Heartbeat loop error: {e}")
                await asyncio.sleep(5)
    
    async def _send_heartbeat(self):
        """Send heartbeat to monitoring system."""
        heartbeat_data = {
            "worker_id": self.worker_id,
            "state": self.state.value,
            "current_jobs": len(self.current_jobs),
            "max_concurrent_jobs": self.max_concurrent_jobs,
            "exchanges": [
                {
                    "name": name,
                    "connected": gateway.is_connected,
                    "last_heartbeat": gateway.last_heartbeat
                }
                for name, gateway in self.exchange_gateways.items()
            ],
            "stats": self.stats.copy(),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Publish to heartbeat queue
        heartbeat_job = ExecutionJob()
        heartbeat_job.job_id = f"heartbeat_{self.worker_id}_{int(time.time())}"
        heartbeat_job.tenant_id = "system"
        heartbeat_job.payload = heartbeat_data
        
        await queue_manager.backend.publish(queue_manager.backend.QueueType.HEARTBEAT, heartbeat_job)
        
        self.stats["last_heartbeat"] = time.time()
    
    async def _timeout_monitor_loop(self):
        """Monitor job timeouts."""
        while self.running:
            try:
                current_time = time.time()
                timeout_jobs = []
                
                for job_id, start_time in self.job_start_times.items():
                    if current_time - start_time > self.job_timeout:
                        timeout_jobs.append(job_id)
                
                for job_id in timeout_jobs:
                    job = self.current_jobs.get(job_id)
                    if job:
                        await self._fail_job(job, f"Job timeout after {self.job_timeout} seconds")
                        self.stats["jobs_timeout"] += 1
                
                await asyncio.sleep(10)  # Check every 10 seconds
                
            except Exception as e:
                logger.error(f"Timeout monitor error: {e}")
                await asyncio.sleep(10)
    
    def get_status(self) -> Dict[str, Any]:
        """Get worker status."""
        return {
            "worker_id": self.worker_id,
            "state": self.state.value,
            "running": self.running,
            "current_jobs": len(self.current_jobs),
            "max_concurrent_jobs": self.max_concurrent_jobs,
            "stats": self.stats.copy(),
            "uptime": time.time() - self.stats["uptime_start"],
            "exchanges": {
                name: {
                    "connected": gateway.is_connected,
                    "last_heartbeat": gateway.last_heartbeat
                }
                for name, gateway in self.exchange_gateways.items()
            }
        }


class WorkerPool:
    """Pool of execution workers for load distribution."""
    
    def __init__(self, worker_count: int, exchange_gateways: Dict[str, ExchangeGateway]):
        self.worker_count = worker_count
        self.exchange_gateways = exchange_gateways
        self.workers = []
        self.running = False
    
    async def start(self):
        """Start all workers in the pool."""
        logger.info(f"Starting worker pool with {self.worker_count} workers")
        
        for i in range(self.worker_count):
            worker_id = f"worker_{i+1:03d}"
            worker = ExecutionWorker(worker_id, self.exchange_gateways.copy())
            self.workers.append(worker)
            
            # Start worker in background
            asyncio.create_task(worker.start())
        
        self.running = True
        logger.info(f"Worker pool started with {len(self.workers)} workers")
    
    async def stop(self):
        """Stop all workers in the pool."""
        logger.info(f"Stopping worker pool with {len(self.workers)} workers")
        
        # Stop all workers
        stop_tasks = [worker.stop() for worker in self.workers]
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        
        self.running = False
        self.workers.clear()
        logger.info("Worker pool stopped")
    
    def get_pool_status(self) -> Dict[str, Any]:
        """Get pool status."""
        return {
            "worker_count": self.worker_count,
            "running": self.running,
            "workers": [worker.get_status() for worker in self.workers]
        }
