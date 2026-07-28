import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional
from datetime import datetime, timezone

from backend_app.core.cache import redis_manager
from backend_app.core.backpressure_v2 import get_backpressure_v2, Priority

logger = logging.getLogger("WorkerBase")

class WorkerBase(ABC):
    """
    Base class for all background workers.
    Provides standardized lifecycle management (startup/shutdown),
    health reporting, and a backpressure hook.
    """
    
    def __init__(self, worker_name: str, poll_interval: float = 1.0, heartbeat_interval: float = 10.0, backpressure_priority: Priority = Priority.MEDIUM):
        self.worker_name = worker_name
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.backpressure_priority = backpressure_priority
        self.running = False
        self._main_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Starts the worker's main loop and heartbeat loop."""
        if self.running:
            logger.warning(f"Worker {self.worker_name} is already running.")
            return
            
        self.running = True
        logger.info(f"Starting worker: {self.worker_name}")
        
        # Start background tasks
        self._main_task = asyncio.create_task(self._run_loop(), name=f"{self.worker_name}_main")
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name=f"{self.worker_name}_heartbeat")

    async def stop(self) -> None:
        """Gracefully stops the worker."""
        if not self.running:
            return
            
        logger.info(f"Stopping worker: {self.worker_name}")
        self.running = False
        
        # Cancel tasks
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            
        # Wait for cancellation
        tasks = [t for t in (self._main_task, self._heartbeat_task) if t]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
        # Optional subclass cleanup
        try:
            await self.cleanup()
        except Exception as e:
            logger.error(f"Error during cleanup for worker {self.worker_name}: {e}", exc_info=True)
            
        logger.info(f"Worker {self.worker_name} stopped.")

    async def _run_loop(self) -> None:
        """The main execution loop for the worker."""
        while self.running:
            try:
                # 1. Backpressure check
                if await self.check_backpressure():
                    logger.warning(f"[{self.worker_name}] Backpressure applied. Throttling iteration.")
                    await asyncio.sleep(self.poll_interval * 2)
                    continue

                # 2. Main Processing
                await self.process_iteration()
                
                # 3. Rest interval
                if self.poll_interval > 0:
                    await asyncio.sleep(self.poll_interval)
                    
            except asyncio.CancelledError:
                logger.info(f"[{self.worker_name}] Main loop cancelled.")
                break
            except Exception as e:
                logger.error(f"[{self.worker_name}] Unhandled error in main loop: {e}", exc_info=True)
                await asyncio.sleep(self.poll_interval)

    async def _heartbeat_loop(self) -> None:
        """Periodically reports worker health."""
        while self.running:
            try:
                await self.report_health()
                await asyncio.sleep(self.heartbeat_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.worker_name}] Error reporting health: {e}")
                await asyncio.sleep(self.heartbeat_interval)

    @abstractmethod
    async def process_iteration(self) -> None:
        """
        The core business logic of the worker (e.g., polling a queue, reading a stream).
        Must be implemented by subclasses.
        """
        pass
        
    async def cleanup(self) -> None:
        """
        Optional hook for cleaning up resources (e.g., closing connections).
        Override if needed.
        """
        pass

    async def check_backpressure(self) -> bool:
        """
        Determines if the worker should throttle based on backpressure.
        Default implementation uses core/backpressure_v2.py.
        Override for custom logic.
        """
        # If backpressure controller cannot accept tasks of this priority, we throttle.
        # backpressure_v2.py's controller tracks overall state.
        bp = get_backpressure_v2()
        if hasattr(bp, 'can_accept'):
            return not bp.can_accept(self.backpressure_priority)
        return False

    async def report_health(self) -> None:
        """
        Reports the worker's health by updating a heartbeat key in Redis.
        External systems can monitor these keys for liveness probes.
        """
        health_key = f"worker:health:{self.worker_name}"
        try:
            pool = redis_manager.get_pool()
            if pool:
                now_iso = datetime.now(timezone.utc).isoformat()
                await pool.hset(health_key, mapping={
                    "status": "healthy",
                    "last_heartbeat": now_iso,
                    "running": str(self.running)
                })
                # Expire key after 3 missed heartbeats
                await pool.expire(health_key, int(self.heartbeat_interval * 3))
        except Exception as e:
            # Don't fail the heartbeat loop if Redis is temporarily down
            logger.warning(f"[{self.worker_name}] Failed to update health key in Redis: {e}")
