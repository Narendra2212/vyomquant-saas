"""OBS-04 regression: every worker's heartbeat actually reaches Redis.

THE DEFECT
----------
``WorkerBase.report_health`` computes a truthful status from the worker's real job
outcomes -- ``healthy`` / ``degraded`` / ``unhealthy`` off ``_consecutive_failures`` --
and its own docstring promises that *"External liveness probes watching these Redis keys
will see the real operational state of the worker, not a permanently-green
fabrication."*

It then wrote that status through::

    pool = redis_manager.get_pool()
    if pool:
        await pool.hset(health_key, mapping={...})

``redis_manager`` is a ``SharedRedisManager`` singleton. It has no ``get_pool``, no
``__getattr__`` fallback, and its ``pool`` property returns the facade itself. So the
first line raised ``AttributeError`` on every heartbeat, the surrounding
``except Exception`` logged it as though Redis were "temporarily down", and **no worker
ever wrote a ``worker:health:*`` key** -- not CommandWorker, not DAGWorker, not
BacktestWorker, not MarketplaceExpiryWorker. The status computation above it was dead
code, and any probe watching those keys saw a fleet that had never checked in.

Proven by execution before the fix (staging/probe_worker_health.py)::

    CALL get_pool() : raised AttributeError: 'SharedRedisManager' object has no
                      attribute 'get_pool'
    report_health() log records : 1
       > [probe_worker] Failed to update health key in Redis: 'SharedRedisManager'
         object has no attribute 'get_pool'

THE FIX
-------
Call the facade directly. It already exposes ``hset`` and ``expire``, and is already
fail-soft, so there was never a pool object to fetch.

The decisive test here is :func:`test_a_facade_without_get_pool_still_gets_a_heartbeat`:
the fake deliberately does **not** define ``get_pool``, reproducing the real singleton's
surface. Pre-fix that test fails; a fake that happened to provide ``get_pool`` would have
hidden the entire defect, which is how it survived into a launch audit.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import backend_app.core.worker_base as wb  # noqa: E402
from backend_app.core.worker_base import WorkerBase  # noqa: E402


class FacadeWithoutGetPool:
    """The real singleton's surface: `hset`/`expire`, a `pool` that returns self, no `get_pool`.

    `pool` is present and truthy precisely because the pre-fix code's `if pool:` guard
    would have passed had it reached for the right attribute -- the bug was the accessor
    name, not the availability of Redis. Keeping the decoy here means the test is
    measuring the fix rather than the absence of a fake.
    """

    def __init__(self) -> None:
        self.hashes: Dict[str, Dict[str, Any]] = {}
        self.ttls: Dict[str, int] = {}
        self.fail_on_hset = False

    @property
    def pool(self):
        return self

    async def hset(self, key: str, name: str = None, value: str = None,
                   mapping: dict = None) -> int:
        if self.fail_on_hset:
            raise RuntimeError("redis unavailable")
        bucket = self.hashes.setdefault(key, {})
        if mapping:
            bucket.update({str(k): str(v) for k, v in mapping.items()})
        elif name is not None:
            bucket[str(name)] = str(value)
        return len(bucket)

    async def expire(self, key: str, time: int, *args, **kwargs) -> bool:
        self.ttls[key] = time
        return True


class _Probe(WorkerBase):
    def __init__(self, name: str = "probe_worker", heartbeat_interval: float = 10.0) -> None:
        super().__init__(worker_name=name, poll_interval=0.0,
                         heartbeat_interval=heartbeat_interval)

    async def process_iteration(self) -> None:
        return None


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def facade(monkeypatch):
    fake = FacadeWithoutGetPool()
    monkeypatch.setattr(wb, "redis_manager", fake)
    return fake


# ── the decisive assertion ──────────────────────────────────────────────────


def test_a_facade_without_get_pool_still_gets_a_heartbeat(facade):
    """THE OBS-04 ASSERTION. Pre-fix this raised AttributeError and wrote nothing."""
    worker = _Probe()

    _run(worker.report_health())

    assert "worker:health:probe_worker" in facade.hashes, (
        "no health key was written. report_health() is reaching for an attribute the "
        "Redis facade does not have, so the status it computed was discarded -- as it "
        "was for every worker in the fleet."
    )


def test_the_heartbeat_does_not_depend_on_get_pool_existing(facade):
    """Stated as a property of the object, not of this test's fake."""
    assert not hasattr(facade, "get_pool")
    _run(_Probe().report_health())
    assert facade.hashes, "the write path still requires get_pool()"


# ── the status is the real one ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "failures,expected",
    [(0, "healthy"), (1, "degraded"), (2, "degraded"), (3, "unhealthy"), (9, "unhealthy")],
)
def test_the_published_status_reflects_actual_job_outcomes(facade, failures, expected):
    worker = _Probe()
    worker._consecutive_failures = failures

    _run(worker.report_health())

    written = facade.hashes["worker:health:probe_worker"]
    assert written["status"] == expected, (
        f"{failures} consecutive failure(s) published {written['status']!r}, expected "
        f"{expected!r}. A permanently-green key is what the docstring forbids."
    )
    assert written["consecutive_failures"] == str(failures)


def test_the_heartbeat_carries_the_facts_a_probe_needs(facade):
    worker = _Probe()
    worker.running = True

    _run(worker.report_health())

    written = facade.hashes["worker:health:probe_worker"]
    for field in ("status", "consecutive_failures", "last_heartbeat", "running"):
        assert field in written, f"the heartbeat omits {field}"
    assert written["running"] == "True"
    # A timestamp a probe can compare against now, not a bare counter.
    assert "T" in written["last_heartbeat"]


def test_the_key_expires_so_a_dead_worker_stops_looking_alive(facade):
    """A key that outlived the worker would be the green fabrication by another route."""
    worker = _Probe(heartbeat_interval=10.0)

    _run(worker.report_health())

    assert facade.ttls["worker:health:probe_worker"] == 30, (
        "expected a TTL of 3 missed heartbeats so absence becomes detectable"
    )


def test_each_worker_gets_its_own_key(facade):
    _run(_Probe(name="command_worker-host-1").report_health())
    _run(_Probe(name="marketplace_expiry_worker").report_health())

    assert "worker:health:command_worker-host-1" in facade.hashes
    assert "worker:health:marketplace_expiry_worker" in facade.hashes


# ── still fail-soft ─────────────────────────────────────────────────────────


def test_a_real_redis_outage_does_not_kill_the_heartbeat_loop(facade):
    """The original `except` was hiding a bug, but it also has a legitimate job."""
    facade.fail_on_hset = True
    worker = _Probe()

    captured = _Capture()
    wb_logger = logging.getLogger("WorkerBase")
    wb_logger.addHandler(captured)
    try:
        _run(worker.report_health())  # must not raise
    finally:
        wb_logger.removeHandler(captured)

    assert any("Failed to update health key" in m for m in captured.messages)


def test_a_genuine_outage_is_distinguishable_from_the_old_silent_failure(facade):
    """Both log the same line, so the difference has to be observable in Redis.

    Pre-fix the warning fired on every single heartbeat with Redis perfectly healthy.
    After the fix it fires only when a write genuinely fails -- so a healthy facade must
    produce no warning at all.
    """
    captured = _Capture()
    wb_logger = logging.getLogger("WorkerBase")
    wb_logger.addHandler(captured)
    try:
        _run(_Probe().report_health())
    finally:
        wb_logger.removeHandler(captured)

    assert not [m for m in captured.messages if "Failed to update health key" in m], (
        "a healthy Redis still produced a heartbeat failure warning; the write path is "
        "still broken and the error is still being swallowed"
    )


# ── negative control ────────────────────────────────────────────────────────


def test_the_pre_fix_accessor_would_have_written_nothing(facade):
    """Proof this file can fail.

    Reconstructs the old access against the same fake. If `get_pool` ever appears on the
    facade this control stops being meaningful and should be revisited -- but then the
    original code would have worked, so the finding itself would need re-examining.
    """
    with pytest.raises(AttributeError):
        facade.get_pool()

    assert facade.hashes == {}, "the fake was pre-populated; the assertions above are vacuous"


def test_the_real_singleton_still_lacks_get_pool_and_still_has_hset():
    """Against the actual object, not a fake.

    This is what makes the fix correct rather than merely test-passing: the accessor the
    code now uses has to exist on the real facade.
    """
    from backend_app.core.cache import redis_manager as real

    assert not hasattr(real, "get_pool"), (
        "get_pool now exists on SharedRedisManager; the original code would have worked "
        "and this finding needs re-examining"
    )
    assert hasattr(real, "hset") and hasattr(real, "expire"), (
        "the facade no longer exposes the accessors report_health() depends on"
    )


def test_report_health_does_not_reference_get_pool_anywhere():
    """Caught by AST, not substring: this file's own prose says `get_pool()` repeatedly."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(WorkerBase.report_health)))
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "get_pool" not in attributes, (
        "report_health() still calls get_pool() in executable code"
    )
    assert {"hset", "expire"} <= attributes
