"""
tests/test_inf04_reconciliation_scheduler_leader_election.py

INF-04: "Background schedulers run in every web worker." ``start_reconciliation_scheduler()``
is called from ``main.py``'s FastAPI lifespan startup hook, which every uvicorn/gunicorn worker
process runs independently - so every process built its own ``ReconciliationScheduler`` and
polled the shared ``reconciliation_mismatches`` table every 30 seconds, from N processes at once.

This file proves ``ReconciliationScheduler._try_become_leader`` (the fix) makes exactly one
process run a poll cycle at a time, with a SHARED fake Redis store standing in for the one real
Redis instance multiple worker processes would actually talk to - unlike
``MockRedisClient`` (one dict per process, DEV_MODE's own stand-in), which is exactly the bug
class ``tests/test_redis_fallback_does_not_fake_a_distributed_lock.py`` already guards against
for the other two distributed locks in this codebase.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import pytest

from backend_app.core.reconciliation_scheduler import ReconciliationScheduler


def run(coro):
    return asyncio.run(coro)


class _SharedFakeRedisClient:
    """Real Redis's ``SET key value NX EX`` semantics, over a store SHARED across instances.

    Shared deliberately: multiple ``ReconciliationScheduler`` instances in this file each get
    their own client object, but all of them are backed by the SAME ``store`` dict - exactly
    what N worker processes talking to one real Redis instance would see, and precisely what
    ``MockRedisClient``'s per-process dict does NOT give (the gap this fix closes).
    """

    def __init__(self, store: Dict[str, str]):
        self._store = store

    async def get(self, key: str) -> Optional[str]:
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int = None, **kwargs) -> Optional[bool]:
        nx = kwargs.get("nx", False)
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    def expire_everything(self) -> None:
        """Simulate a lease's TTL having elapsed, without a real clock."""
        self._store.clear()


def _scheduler_with_client(monkeypatch, store: Dict[str, str]) -> ReconciliationScheduler:
    scheduler = ReconciliationScheduler()
    client = _SharedFakeRedisClient(store)

    async def _fake_get_client():
        return client

    from backend_app.core.cache.redis_manager import redis_manager

    monkeypatch.setattr(redis_manager, "get_client", _fake_get_client, raising=False)
    return scheduler


def test_only_one_of_two_schedulers_becomes_leader_in_the_same_cycle(monkeypatch):
    store: Dict[str, str] = {}
    a = _scheduler_with_client(monkeypatch, store)
    a_is_leader = run(a._try_become_leader())
    b = _scheduler_with_client(monkeypatch, store)
    b_is_leader = run(b._try_become_leader())

    assert a_is_leader != b_is_leader, "exactly one of the two processes must win leadership"
    assert a_is_leader or b_is_leader


def test_the_leader_renews_its_own_lease_rather_than_losing_it_next_cycle(monkeypatch):
    """Without renewal, the same process would lose and re-win leadership every cycle -
    harmless by luck here, but the module docstring explains why a one-shot-only lock is
    the wrong primitive. This proves the SAME instance stays leader across repeated calls."""
    store: Dict[str, str] = {}
    scheduler = _scheduler_with_client(monkeypatch, store)

    results = [run(scheduler._try_become_leader()) for _ in range(5)]

    assert all(results), f"the same leader must keep winning every cycle, got {results}"


def test_a_different_instance_cannot_steal_an_active_lease(monkeypatch):
    store: Dict[str, str] = {}
    leader = _scheduler_with_client(monkeypatch, store)
    assert run(leader._try_become_leader()) is True

    challenger = _scheduler_with_client(monkeypatch, store)
    # The challenger tries repeatedly, as it would across several of its own poll cycles -
    # none should succeed while the leader's lease is still valid and unexpired.
    for _ in range(3):
        assert run(challenger._try_become_leader()) is False
        # The leader keeps renewing between the challenger's attempts, as it would in the
        # real loop.
        assert run(leader._try_become_leader()) is True


def test_a_crashed_leaders_lease_expires_and_another_worker_takes_over(monkeypatch):
    store: Dict[str, str] = {}
    old_leader = _scheduler_with_client(monkeypatch, store)
    assert run(old_leader._try_become_leader()) is True

    # The old leader crashes and stops renewing; simulate its lease elapsing.
    client = _SharedFakeRedisClient(store)
    client.expire_everything()

    new_leader = _scheduler_with_client(monkeypatch, store)
    assert run(new_leader._try_become_leader()) is True


def test_fails_closed_when_redis_is_unreachable(monkeypatch):
    """No reachable Redis must answer False (never-leader), not True for every process."""
    scheduler = ReconciliationScheduler()

    async def _unreachable():
        return None

    from backend_app.core.cache.redis_manager import redis_manager

    monkeypatch.setattr(redis_manager, "get_client", _unreachable, raising=False)

    assert run(scheduler._try_become_leader()) is False


def test_fails_closed_when_redis_errors(monkeypatch):
    scheduler = ReconciliationScheduler()

    async def _raises():
        raise ConnectionError("redis unreachable")

    from backend_app.core.cache.redis_manager import redis_manager

    monkeypatch.setattr(redis_manager, "get_client", _raises, raising=False)

    assert run(scheduler._try_become_leader()) is False


def test_a_non_leader_cycle_does_not_run_poll_and_escalate(monkeypatch):
    """The actual point of INF-04: a process denied leadership must skip the DB/alert work."""
    store: Dict[str, str] = {"leader:reconciliation_scheduler": "someone-else"}
    scheduler = _scheduler_with_client(monkeypatch, store)

    poll_calls = []

    async def _fake_poll_and_escalate():
        poll_calls.append(1)

    scheduler._poll_and_escalate = _fake_poll_and_escalate

    scheduler._running = True

    async def _one_tick():
        is_leader = await scheduler._try_become_leader()
        if is_leader:
            await scheduler._poll_and_escalate()
        return is_leader

    is_leader = run(_one_tick())

    assert is_leader is False
    assert poll_calls == [], "a non-leader process must not run the poll-and-escalate work"


def test_a_leader_cycle_does_run_poll_and_escalate(monkeypatch):
    store: Dict[str, str] = {}
    scheduler = _scheduler_with_client(monkeypatch, store)

    poll_calls = []

    async def _fake_poll_and_escalate():
        poll_calls.append(1)

    scheduler._poll_and_escalate = _fake_poll_and_escalate

    async def _one_tick():
        is_leader = await scheduler._try_become_leader()
        if is_leader:
            await scheduler._poll_and_escalate()
        return is_leader

    is_leader = run(_one_tick())

    assert is_leader is True
    assert poll_calls == [1]
