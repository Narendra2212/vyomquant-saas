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

    Health reporting is truthful: the heartbeat status reflects actual
    process_iteration() success/failure history, not just whether the loop
    is running. A worker that fails every iteration will report "unhealthy"
    after 3 consecutive failures, not "healthy".
    """
    
    # Consecutive failure threshold before reporting "unhealthy"
    UNHEALTHY_THRESHOLD = 3

    def __init__(self, worker_name: str, poll_interval: float = 1.0, heartbeat_interval: float = 10.0, backpressure_priority: Priority = Priority.MEDIUM):
        self.worker_name = worker_name
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.backpressure_priority = backpressure_priority
        self.running = False
        self._main_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        # Tracks consecutive process_iteration() failures; reset on success.
        # Used by _heartbeat_loop to publish truthful health status.
        self._consecutive_failures: int = 0

    async def start(self) -> None:
        """Starts the worker's main loop and heartbeat loop."""
        if self.running:
            logger.warning(f"Worker {self.worker_name} is already running.")
            return
            
        self.running = True
        self._consecutive_failures = 0
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

                # 3. Successful iteration — reset failure counter
                if self._consecutive_failures > 0:
                    logger.info(
                        f"[{self.worker_name}] Recovered after {self._consecutive_failures} consecutive failure(s)."
                    )
                self._consecutive_failures = 0
                
                # 4. Rest interval
                if self.poll_interval > 0:
                    await asyncio.sleep(self.poll_interval)
                    
            except asyncio.CancelledError:
                logger.info(f"[{self.worker_name}] Main loop cancelled.")
                break
            except Exception as e:
                self._consecutive_failures += 1
                logger.error(
                    f"[{self.worker_name}] Unhandled error in main loop "
                    f"(consecutive_failures={self._consecutive_failures}): {e}",
                    exc_info=True,
                )
                if self._consecutive_failures >= self.UNHEALTHY_THRESHOLD:
                    logger.critical(
                        f"[{self.worker_name}] UNHEALTHY: {self._consecutive_failures} consecutive failures. "
                        "Worker is persistently broken — check process_iteration() implementation."
                    )
                await asyncio.sleep(self.poll_interval)

    async def _heartbeat_loop(self) -> None:
        """Periodically reports worker health derived from actual job outcomes."""
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
        Reports truthful worker health derived from _consecutive_failures counter.

        Status logic:
          - "healthy"   — zero consecutive failures
          - "degraded"  — 1 or 2 consecutive failures (recovering / transient)
          - "unhealthy" — 3+ consecutive failures (persistently broken)

        External liveness probes watching these Redis keys will see the real
        operational state of the worker, not a permanently-green fabrication.
        """
        failures = self._consecutive_failures
        if failures >= self.UNHEALTHY_THRESHOLD:
            status = "unhealthy"
        elif failures > 0:
            status = "degraded"
        else:
            status = "healthy"

        health_key = f"worker:health:{self.worker_name}"
        try:
            pool = redis_manager.get_pool()
            if pool:
                now_iso = datetime.now(timezone.utc).isoformat()
                await pool.hset(health_key, mapping={
                    "status": status,
                    "consecutive_failures": str(failures),
                    "last_heartbeat": now_iso,
                    "running": str(self.running),
                })
                # Expire key after 3 missed heartbeats
                await pool.expire(health_key, int(self.heartbeat_interval * 3))
        except Exception as e:
            # Don't fail the heartbeat loop if Redis is temporarily down
            logger.warning(f"[{self.worker_name}] Failed to update health key in Redis: {e}")


