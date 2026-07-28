import asyncio
import logging
from typing import Set, Any, Coroutine, Optional

logger = logging.getLogger("BackgroundTasks")

# Global set to hold strong references to running background tasks to prevent
# them from being prematurely garbage collected by the event loop.
_tracked_tasks: Set[asyncio.Task] = set()

def _on_task_done(task: asyncio.Task) -> None:
    """Callback triggered when a tracked task finishes."""
    # Remove the strong reference so the task can be garbage collected
    _tracked_tasks.discard(task)

    try:
        # Check if an exception occurred in the task
        exc = task.exception()
        if exc:
            task_name = task.get_name()
            logger.error(f"Background task '{task_name}' failed with exception: {exc}", exc_info=exc)
    except asyncio.CancelledError:
        task_name = task.get_name()
        logger.warning(f"Background task '{task_name}' was cancelled.")
    except Exception as e:
        # Failsafe for unexpected errors while retrieving the exception
        logger.error(f"Unexpected error retrieving background task exception: {e}", exc_info=e)

def fire_and_forget_task(coro: Coroutine[Any, Any, Any], name: Optional[str] = None) -> asyncio.Task:
    """
    Creates an asyncio task, holds a strong reference to it until it completes,
    and logs any exceptions it raises. This is a safe replacement for
    fire-and-forget asyncio.create_task() calls.

    Args:
        coro: The coroutine to execute in the background.
        name: An optional name for the task (helps with logging/debugging).

    Returns:
        The created asyncio.Task.
    """
    task = asyncio.create_task(coro, name=name)
    
    # Keep a strong reference to prevent garbage collection
    _tracked_tasks.add(task)
    
    # Attach a callback to log exceptions and clean up the reference
    task.add_done_callback(_on_task_done)
    
    return task

def get_tracked_tasks_count() -> int:
    """Returns the number of currently tracked background tasks."""
    return len(_tracked_tasks)
