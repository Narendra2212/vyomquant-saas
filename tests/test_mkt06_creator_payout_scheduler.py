"""MKT-06: the weekly creator-payout scheduler's leader election and sweep logic.

Mirrors `tests/test_inf04_reconciliation_scheduler_leader_election.py` for the
leader-election half (the SAME primitive, reused under its own lease key), and adds
coverage for the parts specific to this scheduler: the durable "is a sweep due" check,
and that a sweep delegates every per-creator decision to `create_creator_payout`
rather than recomputing a balance itself.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import pytest

from backend_app.core.creator_payout_scheduler import CreatorPayoutScheduler


def run(coro):
    return asyncio.run(coro)


class _SharedFakeRedisClient:
    """Real Redis's ``SET key value NX EX`` semantics, over a store SHARED across
    instances -- what N worker processes talking to one real Redis instance would
    see, exactly as the reconciliation-scheduler leader-election test file's own
    fake does."""

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
        self._store.clear()


def _scheduler_with_client(monkeypatch, store: Dict[str, str]) -> CreatorPayoutScheduler:
    scheduler = CreatorPayoutScheduler()
    client = _SharedFakeRedisClient(store)

    async def _fake_get_client():
        return client

    from backend_app.core.cache.redis_manager import redis_manager

    monkeypatch.setattr(redis_manager, "get_client", _fake_get_client, raising=False)
    return scheduler


# ══════════════════════════════════════════════════════════════════════════
#  PART 1 — LEADER ELECTION (same primitive as INF-04, own lease key)
# ══════════════════════════════════════════════════════════════════════════


def test_uses_its_own_lease_key_independent_of_the_reconciliation_scheduler():
    from backend_app.core.reconciliation_scheduler import LEADER_LOCK_KEY as recon_key

    assert CreatorPayoutScheduler().__dict__["_leader_lock_key"] != recon_key


def test_only_one_of_two_scheduler_instances_becomes_leader_in_the_same_cycle(
    monkeypatch,
):
    store: Dict[str, str] = {}
    a = _scheduler_with_client(monkeypatch, store)
    a_is_leader = run(a._try_become_leader())
    b = _scheduler_with_client(monkeypatch, store)
    b_is_leader = run(b._try_become_leader())

    assert a_is_leader != b_is_leader, "exactly one of the two processes must win leadership"
    assert a_is_leader or b_is_leader


def test_the_leader_renews_its_own_lease_rather_than_losing_it_next_cycle(monkeypatch):
    store: Dict[str, str] = {}
    scheduler = _scheduler_with_client(monkeypatch, store)

    results = [run(scheduler._try_become_leader()) for _ in range(5)]

    assert all(results), f"the same leader must keep winning every cycle, got {results}"


def test_a_different_instance_cannot_steal_an_active_lease(monkeypatch):
    store: Dict[str, str] = {}
    leader = _scheduler_with_client(monkeypatch, store)
    assert run(leader._try_become_leader()) is True

    challenger = _scheduler_with_client(monkeypatch, store)
    for _ in range(3):
        assert run(challenger._try_become_leader()) is False
        assert run(leader._try_become_leader()) is True


def test_a_crashed_leaders_lease_expires_and_another_worker_takes_over(monkeypatch):
    store: Dict[str, str] = {}
    old_leader = _scheduler_with_client(monkeypatch, store)
    assert run(old_leader._try_become_leader()) is True

    client = _SharedFakeRedisClient(store)
    client.expire_everything()

    new_leader = _scheduler_with_client(monkeypatch, store)
    assert run(new_leader._try_become_leader()) is True


def test_fails_closed_when_redis_is_unreachable(monkeypatch):
    scheduler = CreatorPayoutScheduler()

    async def _unreachable():
        return None

    from backend_app.core.cache.redis_manager import redis_manager

    monkeypatch.setattr(redis_manager, "get_client", _unreachable, raising=False)

    assert run(scheduler._try_become_leader()) is False


def test_fails_closed_when_redis_errors(monkeypatch):
    scheduler = CreatorPayoutScheduler()

    async def _raises():
        raise ConnectionError("redis unreachable")

    from backend_app.core.cache.redis_manager import redis_manager

    monkeypatch.setattr(redis_manager, "get_client", _raises, raising=False)

    assert run(scheduler._try_become_leader()) is False


def test_a_non_leader_cycle_does_not_run_the_sweep(monkeypatch):
    store: Dict[str, str] = {"leader:creator_payout_scheduler": "someone-else"}
    scheduler = _scheduler_with_client(monkeypatch, store)

    sweep_calls = []

    async def _fake_run_sweep():
        sweep_calls.append(1)

    scheduler._run_sweep = _fake_run_sweep

    async def _one_tick():
        is_leader = await scheduler._try_become_leader()
        if is_leader:
            await scheduler._run_sweep()
        return is_leader

    is_leader = run(_one_tick())

    assert is_leader is False
    assert sweep_calls == [], "a non-leader process must not run the sweep"


# ══════════════════════════════════════════════════════════════════════════
#  PART 2 — SWEEP-DUE CHECK (durable, survives restarts)
# ══════════════════════════════════════════════════════════════════════════


class _FakeFilter:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        class _Resp:
            pass

        r = _Resp()
        r.data = self._rows
        return r


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return _FakeFilter(self._rows)


class _FakeSvcNoClient:
    def table(self, name):
        return _FakeTable([])


def _svc_with_last_finished(finished_at_iso):
    class _Svc:
        def table(self, name):
            return _FakeTable([{"finished_at": finished_at_iso}] if finished_at_iso else [])

    return _Svc()


def test_sweep_is_due_when_no_sweep_has_ever_finished(monkeypatch):
    scheduler = CreatorPayoutScheduler()
    monkeypatch.setattr(scheduler, "_service_client", lambda: _FakeSvcNoClient())

    assert run(scheduler._sweep_is_due()) is True


def test_sweep_is_not_due_within_the_interval_of_a_completed_sweep(monkeypatch):
    scheduler = CreatorPayoutScheduler(sweep_interval=7 * 86400)
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    monkeypatch.setattr(scheduler, "_service_client", lambda: _svc_with_last_finished(recent))

    assert run(scheduler._sweep_is_due()) is False


def test_sweep_is_due_once_the_interval_has_elapsed_since_the_last_completion(
    monkeypatch,
):
    scheduler = CreatorPayoutScheduler(sweep_interval=7 * 86400)
    stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    monkeypatch.setattr(scheduler, "_service_client", lambda: _svc_with_last_finished(stale))

    assert run(scheduler._sweep_is_due()) is True


def test_a_crashed_sweep_with_no_finished_at_does_not_count_as_a_completion(monkeypatch):
    """The exact restart-recovery property the module docstring describes: a sweep
    that started and never finished must not push the next real attempt away."""
    scheduler = CreatorPayoutScheduler(sweep_interval=7 * 86400)
    # not_.is_("finished_at", "null") filters these rows out server-side; a fake
    # that returns nothing when asked for finished rows simulates that correctly.
    monkeypatch.setattr(scheduler, "_service_client", lambda: _FakeSvcNoClient())

    assert run(scheduler._sweep_is_due()) is True


def test_sweep_is_not_due_when_no_service_client_is_available(monkeypatch):
    """Fails closed in the SAFE direction for a due-check: if the scheduler cannot
    even read whether a sweep is due, it must not assume one is due and hammer a
    half-broken database -- it should wait for the next tick instead."""
    scheduler = CreatorPayoutScheduler()
    monkeypatch.setattr(scheduler, "_service_client", lambda: None)

    assert run(scheduler._sweep_is_due()) is False


# ══════════════════════════════════════════════════════════════════════════
#  PART 3 — THE SWEEP DELEGATES EVERY DECISION TO create_creator_payout
# ══════════════════════════════════════════════════════════════════════════


class _RecordingRpc:
    def __init__(self, calls, owner_id, created_rows):
        self._calls = calls
        self._owner_id = owner_id
        self._created_rows = created_rows

    def execute(self):
        self._calls.append(self._owner_id)

        class _Resp:
            pass

        r = _Resp()
        r.data = self._created_rows
        return r


class _RecordingSvc:
    """A fake service client whose `marketplace_settlements` select returns a fixed
    set of distinct owner_ids, and whose `.rpc("create_creator_payout", ...)` calls
    are recorded -- proving the scheduler calls the atomic function once per owner
    and does NOT itself inspect settlement amounts or currencies."""

    def __init__(self, owner_ids, created_rows_by_owner=None):
        self._owner_ids = owner_ids
        self._created_rows_by_owner = created_rows_by_owner or {}
        self.rpc_calls = []
        self.sweep_rows = []

    def table(self, name):
        if name == "marketplace_settlements":
            return _SettlementsTable(self._owner_ids)
        if name == "marketplace_creator_payout_sweeps":
            return _SweepTable(self.sweep_rows)
        raise AssertionError(f"unexpected table {name!r}")

    def rpc(self, name, params):
        assert name == "create_creator_payout"
        owner_id = params["p_owner_id"]
        created = self._created_rows_by_owner.get(owner_id, [])
        return _RecordingRpc(self.rpc_calls, owner_id, created)


class _SettlementsTable:
    def __init__(self, owner_ids):
        self._owner_ids = owner_ids

    def select(self, *_a, **_k):
        class _Q:
            def __init__(self, owner_ids):
                self._owner_ids = owner_ids

            def execute(self):
                class _Resp:
                    pass

                r = _Resp()
                r.data = [{"owner_id": o} for o in self._owner_ids]
                return r

        return _Q(self._owner_ids)


class _SweepTable:
    def __init__(self, rows):
        self._rows = rows

    def insert(self, payload):
        class _Q:
            def __init__(self, rows):
                self._rows = rows

            def execute(self):
                row = {"id": "sweep-1"}
                self._rows.append(row)

                class _Resp:
                    pass

                r = _Resp()
                r.data = [row]
                return r

        return _Q(self._rows)

    def update(self, payload):
        class _Q:
            def eq(self, *_a, **_k):
                return self

            def execute(self):
                class _Resp:
                    pass

                r = _Resp()
                r.data = []
                return r

        return _Q()


def test_run_sweep_calls_create_creator_payout_once_per_distinct_owner(monkeypatch):
    svc = _RecordingSvc(owner_ids=["owner-a", "owner-b", "owner-b", "owner-c"])
    scheduler = CreatorPayoutScheduler()
    monkeypatch.setattr(scheduler, "_service_client", lambda: svc)

    run(scheduler._run_sweep())

    assert sorted(svc.rpc_calls) == ["owner-a", "owner-b", "owner-c"], (
        "the sweep must call create_creator_payout exactly once per DISTINCT owner, "
        "even though owner-b appeared twice in the settlement rows"
    )


def test_run_sweep_does_not_inspect_amounts_or_currencies_itself(monkeypatch):
    """The scheduler's own job is enumeration, not arithmetic: a creator with zero
    unpaid balance still gets exactly one RPC call (which itself returns nothing),
    never a locally-computed skip."""
    svc = _RecordingSvc(
        owner_ids=["owner-zero-balance"],
        created_rows_by_owner={"owner-zero-balance": []},
    )
    scheduler = CreatorPayoutScheduler()
    monkeypatch.setattr(scheduler, "_service_client", lambda: svc)

    run(scheduler._run_sweep())

    assert svc.rpc_calls == ["owner-zero-balance"], (
        "the scheduler must still call create_creator_payout for a zero-balance "
        "creator and let THAT function decide to create nothing"
    )


def test_run_sweep_records_a_finished_sweep_row(monkeypatch):
    svc = _RecordingSvc(owner_ids=["owner-a"], created_rows_by_owner={"owner-a": [{"id": "p1"}]})
    scheduler = CreatorPayoutScheduler()
    monkeypatch.setattr(scheduler, "_service_client", lambda: svc)

    run(scheduler._run_sweep())

    assert len(svc.sweep_rows) == 1, "exactly one sweep row must be inserted"


def test_a_single_owners_rpc_failure_does_not_abort_the_whole_sweep(monkeypatch):
    class _PartiallyFailingSvc(_RecordingSvc):
        def rpc(self, name, params):
            if params["p_owner_id"] == "owner-bad":
                class _FailingRpc:
                    def execute(self):
                        raise RuntimeError("simulated RPC failure")

                return _FailingRpc()
            return super().rpc(name, params)

    svc = _PartiallyFailingSvc(owner_ids=["owner-bad", "owner-good"])
    scheduler = CreatorPayoutScheduler()
    monkeypatch.setattr(scheduler, "_service_client", lambda: svc)

    # Must not raise -- one creator's failure must not abort the sweep for others.
    run(scheduler._run_sweep())

    assert "owner-good" in svc.rpc_calls, (
        "a failure for one owner must not prevent the sweep from reaching the next one"
    )
