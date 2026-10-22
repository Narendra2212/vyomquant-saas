"""RECON-01: ExchangeReconciliationService may only reconcile from one process.

WHY THIS FILE EXISTS
--------------------
RECON-01's ledger entry lists three blockers that kept
``ExchangeReconciliationService`` from being safe to start. One was closed earlier
(per-tenant executor resolution, commit ea69b94). This file closes the first of the two
remaining:

    "(1) No leader election -- nothing stops N worker processes (gunicorn runs
    --workers ${WORKERS:-2} with no leader election, per
    test_exchange_reconciliation_startup_assessment.py's own finding) from all
    reconciling the same rows concurrently; core/reconciliation_scheduler.py already
    built exactly this primitive for a different class in this codebase and remains the
    template to reuse, not yet applied here."

It is reused, not copied: the mechanics now live once in ``core.leader_lease`` and both
``ReconciliationScheduler`` (INF-04) and this service delegate to them. Two copies of a
distributed-locking rule is how the two drift, and a drifted lock is worse than no lock
because it looks like protection.

WHAT THIS FILE DELIBERATELY DOES *NOT* DO
-----------------------------------------
It does not start the service. RECON-01's second blocker is still open -- nothing
enumerates tenants, so nothing calls ``reconcile_open_orders`` for anyone -- and
``tests/test_p10d_exchange_reconciliation_fails_safely.py::
test_the_service_is_not_registered_in_the_application_lifespan`` PINS the service as
absent from the FastAPI lifespan on purpose. That test passing is part of this change's
own acceptance: closing blocker 1 must not quietly start a loop whose remaining blocker
makes it unsafe. ``test_the_service_is_still_not_started_anywhere`` below asserts the
same thing from this file, so the two findings cannot be confused for one another.

THE FAKE REDIS IS SHARED ON PURPOSE
-----------------------------------
``_SharedFakeRedisClient`` backs every instance with ONE dict, which is what N worker
processes talking to one real Redis instance actually see. ``MockRedisClient`` (DEV_MODE's
own stand-in) gives each process its own dict, satisfying the API while providing none of
the mutual exclusion -- the exact bug class
``tests/test_redis_fallback_does_not_fake_a_distributed_lock.py`` guards for the other
distributed locks here. Same harness shape as
``tests/test_inf04_reconciliation_scheduler_leader_election.py``, so the two subsystems'
leader-election tests cannot disagree about what Redis does.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Dict, List, Optional

import pytest

from backend_app.backend.exchange_reconciliation import (
    RECONCILIATION_LEADER_LOCK_KEY,
    RECONCILIATION_LEADER_LOCK_TTL_SECONDS,
    ExchangeReconciliationService,
)


def run(coro):
    return asyncio.run(coro)


class _SharedFakeRedisClient:
    """Real Redis's ``SET key value NX EX`` semantics over a SHARED store."""

    def __init__(self, store: Dict[str, str]):
        self._store = store
        self.set_calls: List[dict] = []

    async def get(self, key: str) -> Optional[str]:
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int = None, **kwargs):
        nx = kwargs.get("nx", False)
        self.set_calls.append({"key": key, "value": value, "ex": ex, "nx": nx})
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    def expire_everything(self) -> None:
        """A lease's TTL elapsing, without a real clock."""
        self._store.clear()


def _service_with_client(monkeypatch, store: Dict[str, str]):
    """A service instance whose Redis is the shared fake. ``db_session`` is unused here."""
    service = ExchangeReconciliationService(db_session=None)
    client = _SharedFakeRedisClient(store)

    async def _fake_get_client():
        return client

    from backend_app.core.cache.redis_manager import redis_manager

    monkeypatch.setattr(redis_manager, "get_client", _fake_get_client, raising=False)
    return service, client


# ══════════════════════════════════════════════════════════════════════════════
#  1. The election itself
# ══════════════════════════════════════════════════════════════════════════════


def test_only_one_of_two_processes_becomes_leader_in_the_same_cycle(monkeypatch):
    store: Dict[str, str] = {}
    a, _ = _service_with_client(monkeypatch, store)
    a_leader = run(a._try_become_leader())

    b, _ = _service_with_client(monkeypatch, store)
    b_leader = run(b._try_become_leader())

    assert a_leader != b_leader, (
        "exactly one of two worker processes must win leadership; both winning is the "
        "RECON-01 defect itself"
    )


def test_two_instances_have_distinct_identities(monkeypatch):
    """Negative control on the test above.

    If both instances shared an id, the second would look like the first *renewing* and
    both calls would return True for a reason that has nothing to do with the lock.
    """
    store: Dict[str, str] = {}
    a, _ = _service_with_client(monkeypatch, store)
    b, _ = _service_with_client(monkeypatch, store)
    assert a._instance_id != b._instance_id


def test_the_same_leader_keeps_winning_every_cycle(monkeypatch):
    """A renewable lease, not a one-shot lock.

    A one-shot-only lock would make the holder lose the key on expiry and re-race every
    cycle, so leadership would churn and "exactly one at a time" would hold only within
    a cycle, never across them.
    """
    store: Dict[str, str] = {}
    leader, _ = _service_with_client(monkeypatch, store)

    results = [run(leader._try_become_leader()) for _ in range(5)]

    assert all(results), f"the incumbent must keep winning; got {results}"


def test_a_challenger_cannot_take_a_live_lease(monkeypatch):
    store: Dict[str, str] = {}
    leader, _ = _service_with_client(monkeypatch, store)
    assert run(leader._try_become_leader()) is True

    challenger, _ = _service_with_client(monkeypatch, store)
    for _ in range(3):
        assert run(challenger._try_become_leader()) is False
        # The incumbent keeps renewing between attempts, as it would in the real loop.
        assert run(leader._try_become_leader()) is True


def test_a_crashed_leaders_lease_expires_and_a_successor_takes_over(monkeypatch):
    """A dead worker must not deadlock the subsystem forever."""
    store: Dict[str, str] = {}
    old_leader, client = _service_with_client(monkeypatch, store)
    assert run(old_leader._try_become_leader()) is True

    client.expire_everything()  # the crashed leader stops renewing; the TTL elapses

    successor, _ = _service_with_client(monkeypatch, store)
    assert run(successor._try_become_leader()) is True


def test_the_lease_is_written_with_a_ttl_and_nx(monkeypatch):
    """Without ``ex`` a crashed leader's key is permanent; without ``nx`` there is no race."""
    store: Dict[str, str] = {}
    service, client = _service_with_client(monkeypatch, store)
    run(service._try_become_leader())

    acquire = client.set_calls[0]
    assert acquire["nx"] is True, "the acquiring SET must use NX or two processes both win"
    assert acquire["ex"] == RECONCILIATION_LEADER_LOCK_TTL_SECONDS
    assert acquire["key"] == RECONCILIATION_LEADER_LOCK_KEY


def test_the_renewal_does_not_use_nx(monkeypatch):
    """The incumbent re-sets its own key; ``NX`` would make every renewal fail."""
    store: Dict[str, str] = {}
    service, client = _service_with_client(monkeypatch, store)
    run(service._try_become_leader())
    client.set_calls.clear()

    assert run(service._try_become_leader()) is True
    renewal = client.set_calls[0]
    assert renewal["nx"] is False
    assert renewal["ex"] == RECONCILIATION_LEADER_LOCK_TTL_SECONDS


# ══════════════════════════════════════════════════════════════════════════════
#  2. Failing closed
# ══════════════════════════════════════════════════════════════════════════════


def test_no_reachable_redis_means_no_leadership(monkeypatch):
    """Guessing "yes" here reproduces the defect in every worker at once."""
    service = ExchangeReconciliationService(db_session=None)

    async def _none():
        return None

    from backend_app.core.cache.redis_manager import redis_manager
    monkeypatch.setattr(redis_manager, "get_client", _none, raising=False)

    assert run(service._try_become_leader()) is False


def test_a_raising_redis_means_no_leadership(monkeypatch):
    service = ExchangeReconciliationService(db_session=None)

    async def _raises():
        raise RuntimeError("redis is down")

    from backend_app.core.cache.redis_manager import redis_manager
    monkeypatch.setattr(redis_manager, "get_client", _raises, raising=False)

    assert run(service._try_become_leader()) is False, (
        "a failed leadership check must refuse, not raise out of the loop and not "
        "assume leadership"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  3. The loop actually honours the election
# ══════════════════════════════════════════════════════════════════════════════


def _run_one_cycle(service, *, is_leader: bool, reconciled: list):
    """Drive exactly one iteration of ``start_reconciliation_loop`` and stop."""

    async def _leader():
        return is_leader

    async def _reconcile(tenant_id, exchange_id=None):
        reconciled.append(tenant_id)
        return []

    service._try_become_leader = _leader
    service.reconcile_open_orders = _reconcile

    real_sleep = asyncio.sleep

    async def _sleep_then_stop(_seconds):
        service._running = False
        await real_sleep(0)

    async def _drive():
        import backend_app.backend.exchange_reconciliation as mod
        original = mod.asyncio.sleep
        mod.asyncio.sleep = _sleep_then_stop
        try:
            await service.start_reconciliation_loop("tenant-1", interval_seconds=0)
        finally:
            mod.asyncio.sleep = original

    run(_drive())


def test_a_non_leader_cycle_does_not_reconcile_anything():
    """The whole point: a worker that is not the leader must touch nothing."""
    service = ExchangeReconciliationService(db_session=None)
    reconciled: list = []

    _run_one_cycle(service, is_leader=False, reconciled=reconciled)

    assert reconciled == [], (
        "a non-leader worker called reconcile_open_orders; that is N processes "
        "reconciling the same rows, which is RECON-01's first blocker"
    )


def test_a_leader_cycle_does_reconcile():
    """Positive control: without this, the test above passes for a trivial reason."""
    service = ExchangeReconciliationService(db_session=None)
    reconciled: list = []

    _run_one_cycle(service, is_leader=True, reconciled=reconciled)

    assert reconciled == ["tenant-1"]


def test_leadership_is_rechecked_every_cycle_not_once_at_startup():
    """Read from the loop's source: the check must be inside the while body.

    A check hoisted above the loop would let a process that won once keep reconciling
    forever after its lease lapsed -- including after a network partition healed and
    another worker legitimately took over.
    """
    source = inspect.getsource(
        ExchangeReconciliationService.start_reconciliation_loop
    )
    while_at = source.index("while self._running")
    assert "_try_become_leader" in source[while_at:], (
        "the leadership check is not inside the loop body; it must be re-evaluated "
        "every cycle because the lease is renewable and revocable"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  4. One implementation, two subsystems, two keys
# ══════════════════════════════════════════════════════════════════════════════


def test_the_two_subsystems_do_not_share_a_lock_key():
    """Sharing one key would make each subsystem's leader suppress the other.

    That failure mode is particularly nasty: both loops are 'running', a leader exists,
    nothing errors -- and half the work never happens.
    """
    from backend_app.core.reconciliation_scheduler import LEADER_LOCK_KEY

    assert RECONCILIATION_LEADER_LOCK_KEY != LEADER_LOCK_KEY


def test_both_subsystems_delegate_to_the_one_shared_primitive():
    """No second copy of the locking rule.

    RECON-01's entry said the INF-04 primitive "remains the template to reuse". Reuse
    here means delegation, not duplication: two copies drift, and a drifted lock looks
    like protection while providing less.
    """
    from backend_app.core.reconciliation_scheduler import ReconciliationScheduler

    for owner, method in (
        (ExchangeReconciliationService, ExchangeReconciliationService._try_become_leader),
        (ReconciliationScheduler, ReconciliationScheduler._try_become_leader),
    ):
        source = inspect.getsource(method)
        assert "try_acquire_or_renew" in source, (
            f"{owner.__name__}._try_become_leader does not delegate to "
            "core.leader_lease.try_acquire_or_renew"
        )
        assert "nx=True" not in source, (
            f"{owner.__name__}._try_become_leader still implements the lock itself; the "
            "mechanics belong in core.leader_lease so the two cannot drift"
        )


def test_the_shared_primitive_fails_closed_by_construction():
    """Read from the primitive's source: no path may return True on an error."""
    from backend_app.core import leader_lease

    source = inspect.getsource(leader_lease.try_acquire_or_renew)
    except_at = source.index("except Exception")
    assert "return False" in source[except_at:], (
        "the exception path must refuse leadership"
    )
    assert "return True" not in source[except_at:], (
        "no error path may grant leadership"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  5. The remaining blocker is still a blocker
# ══════════════════════════════════════════════════════════════════════════════


def test_the_service_is_still_not_started_anywhere():
    """Closing blocker 1 must NOT quietly start the loop.

    RECON-01's blocker 2 -- no tenant enumeration -- is still open, so starting this
    service would run a loop that reconciles nobody while holding a lease. This mirrors
    test_p10d_exchange_reconciliation_fails_safely.py's own lifespan assertion, stated
    here too so a future change to THIS file's subject cannot silently also start the
    service.
    """
    import pathlib

    main_py = pathlib.Path(__file__).resolve().parent.parent / "backend_app" / "main.py"
    text = main_py.read_text(encoding="utf-8")
    assert "ExchangeReconciliationService(" not in text, (
        "ExchangeReconciliationService is now constructed in main.py. Leader election "
        "alone does not make it safe to start: nothing enumerates tenants yet "
        "(RECON-01 blocker 2), so the loop would hold a lease and reconcile nobody."
    )
    assert "start_reconciliation_loop" not in text, (
        "main.py now starts the reconciliation loop; see above"
    )
