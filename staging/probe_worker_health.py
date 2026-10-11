"""Does WorkerBase.report_health() actually report anything?

`worker_base.report_health` reaches for `redis_manager.get_pool()` inside a
`try/except Exception` that only logs a warning. If that attribute does not exist, every
heartbeat raises AttributeError, is swallowed, and no `worker:health:*` key is ever
written -- so a liveness probe watching those keys sees a worker that never checks in,
and the "truthful health reporting" documented on the class never runs.

This probe decides it by execution rather than by reading.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.cache import redis_manager  # noqa: E402
from backend_app.core.worker_base import WorkerBase  # noqa: E402


class _Probe(WorkerBase):
    def __init__(self) -> None:
        super().__init__(worker_name="probe_worker", poll_interval=0.0)

    async def process_iteration(self) -> None:
        return None


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record.getMessage())


async def main() -> int:
    print("ATTR get_pool present on redis_manager :", hasattr(redis_manager, "get_pool"))
    print("ATTR pool     present on redis_manager :", hasattr(redis_manager, "pool"))
    print("redis_manager class                    :", type(redis_manager).__name__)

    # Does the attribute access actually raise?
    try:
        redis_manager.get_pool()
        print("CALL get_pool()                        : returned without error")
    except Exception as exc:  # noqa: BLE001
        print(f"CALL get_pool()                        : raised {type(exc).__name__}: {exc}")

    cap = _Capture()
    wb_logger = logging.getLogger("WorkerBase")
    wb_logger.addHandler(cap)
    wb_logger.setLevel(logging.DEBUG)

    probe = _Probe()
    probe._consecutive_failures = 5  # should report "unhealthy"
    await probe.report_health()

    wb_logger.removeHandler(cap)

    print("report_health() log records            :", len(cap.records))
    for rec in cap.records:
        print("   >", rec[:160])

    swallowed = any("Failed to update health key" in r for r in cap.records)
    print()
    print("VERDICT: heartbeat silently swallowed  :", swallowed)
    print("VERDICT: worker:health:* key written   :", not swallowed)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
