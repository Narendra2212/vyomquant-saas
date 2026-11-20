"""WS-03 follow-up — the dag worker's Redis registration was a dropped coroutine.

WHAT WAS WRONG
--------------
``DAGWorker.__init__`` ended with ``self._register_worker()``, and that method was::

    def _register_worker(self):
        redis_manager.sadd(TaskQueueKeyBuilder.worker_registry(), self.worker_id)
        logger.info(f"Worker registered: {self.worker_id}")

``SharedRedisManager.sadd`` is an ``async def``. The call therefore built a coroutine object
and threw it away, which Python reported as::

    RuntimeWarning: coroutine 'SharedRedisManager.sadd' was never awaited

observed verbatim in the dag worker's own startup log in production. **The registry was never
written**, while the very next line logged "Worker registered". Anything reading
``dag:workers:registry`` saw an empty set however many workers were running.

``cleanup()`` had the same bug in the other direction -- an un-awaited ``srem`` -- and the two
concealed each other: deregistering a worker that was never registered left a set that looked
correct by being empty.

A constructor cannot await, which is the whole reason the bug was possible there. Registration
moved to ``start()``, which can.

WHY THE WARNING IS THE ASSERTION
--------------------------------
The first test below asserts on the ``RuntimeWarning`` itself rather than on a mock call
count. That warning IS the defect's signature: a test that only checked "was sadd called"
would pass against the broken code, because the broken code did call it -- it just never
awaited the result.
"""
from __future__ import annotations

import warnings

import pytest

from backend_app.backend import dag_worker as dw
from backend_app.core.dag_task_queue import TaskQueueKeyBuilder

REGISTRY = TaskQueueKeyBuilder.worker_registry()


class _RecordingRedis:
    """Records awaited set operations. Both methods are coroutines, as the real ones are."""

    def __init__(self):
        self.sets: dict[str, set] = {}
        self.sadd_awaited = 0
        self.srem_awaited = 0

    async def sadd(self, key, *members):
        self.sadd_awaited += 1
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    async def srem(self, key, *members):
        self.srem_awaited += 1
        self.sets.get(key, set()).difference_update(members)
        return len(members)


@pytest.fixture
def recording_redis(monkeypatch):
    fake = _RecordingRedis()
    monkeypatch.setattr(dw.redis_manager, "sadd", fake.sadd)
    monkeypatch.setattr(dw.redis_manager, "srem", fake.srem)
    return fake


def test_constructing_a_worker_drops_no_coroutine_and_touches_no_registry(recording_redis):
    """The defect's signature, asserted directly.

    If registration ever moves back into `__init__`, this fails on the RuntimeWarning
    rather than on a call count -- which is the distinction that matters, because the broken
    code DID call `sadd`.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        worker = dw.DAGWorker(worker_id="unit-test-worker")

    never_awaited = [
        w for w in caught
        if issubclass(w.category, RuntimeWarning) and "never awaited" in str(w.message)
    ]
    assert not never_awaited, (
        f"construction dropped an un-awaited coroutine: "
        f"{[str(w.message) for w in never_awaited]}"
    )
    assert recording_redis.sadd_awaited == 0, (
        "the constructor wrote to the registry; it cannot await, so registration belongs "
        "in start()"
    )
    assert worker.worker_id == "unit-test-worker"


@pytest.mark.asyncio
async def test_registration_is_awaited_and_actually_writes_the_registry(recording_redis):
    worker = dw.DAGWorker(worker_id="unit-test-worker")

    await worker._register_worker()

    assert recording_redis.sadd_awaited == 1
    assert recording_redis.sets[REGISTRY] == {"unit-test-worker"}, (
        "the registry does not contain this worker, which is the production symptom: the "
        "log said 'Worker registered' and the set was empty"
    )


@pytest.mark.asyncio
async def test_cleanup_awaits_its_deregistration(recording_redis):
    worker = dw.DAGWorker(worker_id="unit-test-worker")
    await worker._register_worker()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await worker.cleanup()

    never_awaited = [
        w for w in caught
        if issubclass(w.category, RuntimeWarning) and "never awaited" in str(w.message)
    ]
    assert not never_awaited, f"cleanup dropped a coroutine: {[str(w.message) for w in never_awaited]}"
    assert recording_redis.srem_awaited == 1
    assert recording_redis.sets[REGISTRY] == set()


@pytest.mark.asyncio
async def test_start_registers_BEFORE_it_begins_processing(monkeypatch, recording_redis):
    """Ordering, not merely occurrence.

    A worker that is claiming and executing DAG tasks while absent from the registry is the
    state this ordering exists to prevent. The base `start()` is replaced with a recorder so
    no main loop, heartbeat loop, or database connection is started by this test.
    """
    order: list[str] = []

    async def fake_base_start(_self):
        order.append("base_start")

    monkeypatch.setattr(dw.WorkerBase, "start", fake_base_start)

    worker = dw.DAGWorker(worker_id="unit-test-worker")
    original_register = worker._register_worker

    async def recording_register():
        order.append("register")
        await original_register()

    monkeypatch.setattr(worker, "_register_worker", recording_register)

    await worker.start()

    assert order == ["register", "base_start"], (
        f"registration must happen before the worker starts processing; got {order}"
    )
    assert recording_redis.sets[REGISTRY] == {"unit-test-worker"}
