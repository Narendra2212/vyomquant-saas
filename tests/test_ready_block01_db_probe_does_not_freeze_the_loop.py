"""READY-BLOCK-01: a slow database cannot freeze the API through the readiness probe.

Production 2026-11-20: three ECS rollouts (deploys 584, 587 x2) were rolled back with
"unhealthy ... Request timed out" ~70 s after the new task registered. The database
readiness probe ran a SYNCHRONOUS `SELECT 1` directly on the event loop inside an `async`
function; `asyncio.wait_for` cannot interrupt that, so a slow connect or a pool checkout
froze the whole process -- /health/live included -- and both the ALB and the container
health check timed out. The probe now runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import time
import types

from backend_app.core import readiness


class _SlowSession:
    def execute(self, *_a, **_k):
        time.sleep(5)  # a pool checkout / connect that hangs

    def close(self):
        pass


def test_a_hung_database_times_out_and_the_loop_stays_responsive(monkeypatch):
    import backend_app.core.database as database

    monkeypatch.setattr(database, "SessionLocal", lambda: _SlowSession())

    async def scenario():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.1)
                ticks += 1

        hb = asyncio.create_task(heartbeat())
        started = time.monotonic()
        report = await readiness.probe_database()
        elapsed = time.monotonic() - started
        hb.cancel()
        return report, elapsed, ticks

    report, elapsed, ticks = asyncio.run(scenario())
    assert elapsed < readiness.PROBE_TIMEOUT_SECONDS + 1.0, f"probe blocked for {elapsed:.1f}s"
    assert report.state != readiness.UP
    # The event loop kept running other work while the database hung (a health check,
    # a request): ~3 s of 0.1 s heartbeats. Before the fix this was 0.
    assert ticks >= 20, f"event loop was frozen ({ticks} heartbeats)"
