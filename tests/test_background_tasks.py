"""
tests/test_background_tasks.py
Unit tests for the fire-and-forget background task tracking utility.
"""
import asyncio
import logging
from unittest.mock import patch
import pytest

from backend_app.core.background_tasks import fire_and_forget_task, _tracked_tasks, get_tracked_tasks_count

async def wait_for_tracked_tasks_drained(deadline_seconds: float = 2.0) -> None:
    """
    Poll until every tracked task has been removed by its done-callback.

    A fixed `await asyncio.sleep(...)` budget is flaky: on a loaded machine the
    done-callback may not have run yet when the assertion executes. Polling with
    a generous deadline is both faster in the common case and deterministic under
    load. Raises AssertionError if the tasks never drain, so a genuine leak of a
    tracked task still fails the test rather than hanging.
    """
    loop = asyncio.get_running_loop()
    expiry = loop.time() + deadline_seconds
    while get_tracked_tasks_count() != 0:
        assert loop.time() < expiry, (
            f"tracked tasks did not drain within {deadline_seconds}s; "
            f"{get_tracked_tasks_count()} still tracked"
        )
        await asyncio.sleep(0.01)

@pytest.fixture(autouse=True)
def cleanup_tracked_tasks():
    # Clear the set before each test to ensure test isolation
    _tracked_tasks.clear()
    yield
    # Wait for any lingering tasks to complete
    for task in list(_tracked_tasks):
        if not task.done():
            task.cancel()
    _tracked_tasks.clear()

def test_fire_and_forget_task_tracks_and_cleans_up():
    """Test that a task is tracked while running and removed when done."""
    async def fast_task():
        await asyncio.sleep(0.01)
        return "done"

    async def run_test():
        # Start the task
        task = fire_and_forget_task(fast_task(), name="test_success_task")
        
        # Task should be tracked immediately
        assert get_tracked_tasks_count() == 1
        assert task in _tracked_tasks
        
        # Wait for the task to complete and for the done-callback to run
        await wait_for_tracked_tasks_drained()
        
        # Task should be removed from tracking after completion
        assert get_tracked_tasks_count() == 0
        assert task not in _tracked_tasks
        
    asyncio.run(run_test())

def test_fire_and_forget_task_logs_exception():
    """Test that an exception in a fire-and-forget task is caught and logged."""
    async def failing_task():
        await asyncio.sleep(0.01)
        raise ValueError("Simulated background error")

    async def run_test():
        with patch("backend_app.core.background_tasks.logger") as mock_logger:
            task = fire_and_forget_task(failing_task(), name="test_failing_task")
            
            # Wait for the task to complete and trigger the callback
            await wait_for_tracked_tasks_drained()
            
            # Verify it was removed
            assert get_tracked_tasks_count() == 0
            
            # Verify the error was logged
            mock_logger.error.assert_called_once()
            log_msg = mock_logger.error.call_args[0][0]
            assert "Background task 'test_failing_task' failed with exception: Simulated background error" in log_msg
            assert mock_logger.error.call_args[1]["exc_info"] is not None

    asyncio.run(run_test())

def test_fire_and_forget_task_logs_cancellation():
    """Test that task cancellation is caught and logged."""
    async def long_task():
        await asyncio.sleep(1.0)
        return "done"

    async def run_test():
        with patch("backend_app.core.background_tasks.logger") as mock_logger:
            task = fire_and_forget_task(long_task(), name="test_cancelled_task")
            
            # Cancel the task manually
            task.cancel()
            
            # Yield to event loop until the cancellation callback has run
            await wait_for_tracked_tasks_drained()
            
            # Verify it was removed
            assert get_tracked_tasks_count() == 0
            
            # Verify the warning was logged
            mock_logger.warning.assert_called_once()
            log_msg = mock_logger.warning.call_args[0][0]
            assert "Background task 'test_cancelled_task' was cancelled." in log_msg

    asyncio.run(run_test())
