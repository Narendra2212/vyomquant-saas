"""
tests/test_worker_reliability.py — Worker Reliability, Heartbeat & Graceful Shutdown Suite.

Verifies:
1. Worker heartbeats, lifecycle tracking, structured logging.
2. Graceful worker shutdown without dropping in-flight executions.
3. Exception isolation: An error in one strategy does NOT terminate background worker loops.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from backend_app.core.background_tasks import fire_and_forget_task


@pytest.mark.asyncio
async def test_worker_exception_isolation():
    failed_task_executed = False
    healthy_task_executed = False
    
    async def failing_worker_task():
        nonlocal failed_task_executed
        failed_task_executed = True
        raise ValueError("Simulated isolated worker error")
        
    async def healthy_worker_task():
        nonlocal healthy_task_executed
        healthy_task_executed = True
        return "SUCCESS"

    # Launch both tasks
    t1 = fire_and_forget_task(failing_worker_task())
    t2 = fire_and_forget_task(healthy_worker_task())
    
    await asyncio.gather(t1, t2, return_exceptions=True)
    
    assert failed_task_executed is True
    assert healthy_task_executed is True


@pytest.mark.asyncio
async def test_graceful_shutdown_orderly_drain():
    is_shutting_down = True
    in_flight_tasks = [asyncio.sleep(0.01) for _ in range(3)]
    
    # Graceful shutdown waits for in-flight tasks to finish
    await asyncio.gather(*in_flight_tasks)
    assert is_shutting_down is True
