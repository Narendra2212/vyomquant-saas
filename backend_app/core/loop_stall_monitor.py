"""LOOP-STALL-01: name the code that freezes the event loop.

WHY THIS EXISTS
    Deploys 584, 587 and 591 (attempt 1) were rolled back because a new ECS task stopped
    answering the ALB health check (5 s timeout) about a minute after it started. Every
    request -- the health check included -- is served by ONE asyncio event loop, so a
    single synchronous call that takes seconds (a blocking HTTP/database client, CPU-heavy
    work) freezes the whole process. READY-BLOCK-01 removed one such call from the DB
    probe; at least one more remains, and the task's logs never said which, because a
    frozen loop cannot log that it is frozen.

WHAT IT DOES
    An asyncio task stamps ``time.monotonic()`` every ``TICK_SECONDS``. A daemon THREAD
    (which keeps running while the loop is frozen) checks the stamp. When it is older than
    ``STALL_SECONDS`` it logs, once per stall, a WARNING with the main thread's current
    stack -- the exact line the loop is stuck on -- and, when the loop recovers, how long
    the stall lasted.

    It observes only. It changes no behaviour, holds no lock the loop needs, and costs one
    float read every half second. Disable with ``LOOP_STALL_MONITOR=0``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback
from typing import Optional

logger = logging.getLogger("vyomquant.loop_stall")

TICK_SECONDS = 0.5
STALL_SECONDS = float(os.getenv("LOOP_STALL_SECONDS", "2.0"))
_MAX_STACK_FRAMES = 25


class LoopStallMonitor:
    def __init__(self, stall_seconds: float = STALL_SECONDS, tick_seconds: float = TICK_SECONDS):
        self.stall_seconds = stall_seconds
        self.tick_seconds = tick_seconds
        self._last_tick = time.monotonic()
        self._loop_thread_id: Optional[int] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._task: Optional[asyncio.Task] = None
        self.stalls_reported = 0

    async def _heartbeat(self) -> None:
        while not self._stop.is_set():
            self._last_tick = time.monotonic()
            await asyncio.sleep(self.tick_seconds)

    def _loop_stack(self) -> str:
        frame = sys._current_frames().get(self._loop_thread_id)  # noqa: SLF001 - diagnostics
        if frame is None:
            return "<event loop thread has no frame>"
        return "".join(traceback.format_stack(frame, limit=_MAX_STACK_FRAMES))

    def _watch(self) -> None:
        in_stall = False
        stall_started = 0.0
        while not self._stop.wait(self.tick_seconds):
            age = time.monotonic() - self._last_tick
            if age >= self.stall_seconds and not in_stall:
                in_stall = True
                stall_started = self._last_tick
                self.stalls_reported += 1
                logger.warning(
                    "LOOP-STALL-01: event loop unresponsive for %.1fs; it is executing:\n%s",
                    age, self._loop_stack(),
                )
            elif in_stall and age < self.stall_seconds:
                in_stall = False
                logger.warning(
                    "LOOP-STALL-01: event loop recovered after %.1fs",
                    self._last_tick - stall_started,
                )

    def start(self) -> None:
        """Start from inside the running loop (e.g. the app lifespan)."""
        if self._thread is not None:
            return
        self._loop_thread_id = threading.get_ident()
        self._last_tick = time.monotonic()
        self._task = asyncio.get_running_loop().create_task(self._heartbeat())
        self._thread = threading.Thread(target=self._watch, name="loop-stall-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()


def start_loop_stall_monitor() -> Optional[LoopStallMonitor]:
    """Start the monitor unless ``LOOP_STALL_MONITOR=0``. Never raises into startup."""
    if os.getenv("LOOP_STALL_MONITOR", "1").strip() in ("0", "false", "False", "off"):
        return None
    try:
        monitor = LoopStallMonitor()
        monitor.start()
        return monitor
    except Exception as exc:  # noqa: BLE001 - diagnostics must never block startup
        logger.error("LOOP-STALL-01: monitor could not start: %s", exc)
        return None
