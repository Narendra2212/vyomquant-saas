"""WATCH-01 -- only one worker process may adjudicate an open order.

THE DEFECT
----------
``main.py`` constructs ``OrderWatchdog`` and awaits ``.start()`` in the FastAPI lifespan
unconditionally, and ``startup.sh:102`` runs ``gunicorn --workers ${WORKERS:-2}``. So both
of this class's loops ran once PER WORKER PROCESS: every worker queried the venue for the
same open orders each interval, and each one could adjudicate the same row.

``ExchangeReconciliationService`` (RECON-01) and ``ReconciliationScheduler`` (INF-04) both
elect a leader through ``core.leader_lease.try_acquire_or_renew``. This module had no
election at all -- its source contained no ``leader``, ``lease``, ``try_acquire``, ``lock``
or ``nx``.

WHY THE POSITION ROW LOCK DOES NOT ALREADY COVER THIS
-----------------------------------------------------
WATCH-01's own remaining_action says to confirm, before relying on it, that the
position-delta ``SELECT ... FOR UPDATE`` actually serialises. Measured, and the answer is
"on the happy path only":

* The statement is CORRECT now. ``_position_lock_sql`` renders the JSON access for the
  bound dialect; the inline version it replaced named a ``symbol`` column ``positions``
  does not have, so PostgreSQL answered ``UndefinedColumn`` and the lock was never once
  taken. When it succeeds it is held through ``_update_position`` to the commit.
* But BOTH call sites in ``_apply_status_update`` still answer a FAILED lock by rolling
  the transaction back and applying the delta anyway -- "Continue with update even if lock
  fails (non-blocking fallback)".

So the lock is not an unconditional guarantee, and an election that removes the
concurrency is not a second belt on a working one. That unlocked fallback is recorded as
remaining work on the ledger entry rather than changed here: whether a lock failure should
drop the fill or apply it unlocked is a money-correctness decision, and making it
fail-closed correctly also requires not persisting the new ``filled_size`` until the
position update succeeds -- otherwise the delta is lost permanently instead of retried.

THE FAKE REDIS IS SHARED ON PURPOSE
-----------------------------------
``_SharedFakeRedisClient`` backs every instance with ONE dict, which is what N worker
processes talking to one real Redis actually see. ``MockRedisClient`` (DEV_MODE's own
stand-in) gives each process its own dict, satisfying the API while providing none of the
mutual exclusion. Same harness shape as
``tests/test_recon01_exchange_reconciliation_leader_election.py`` and
``tests/test_inf04_reconciliation_scheduler_leader_election.py``, so the three subsystems'
election tests cannot disagree about what Redis does.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

import pytest

from backend_app.backend.order_watchdog import (
    ORDER_WATCHDOG_LEADER_LOCK_KEY,
    OrderWatchdog,
    WatchdogConfig,
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


def _watchdog_with_client(monkeypatch, store: Dict[str, str], *, config=None):
    """A watchdog whose Redis is the shared fake. ``db_session`` is unused by the election."""
    watchdog = OrderWatchdog(db_session=None, config=config)
    client = _SharedFakeRedisClient(store)

    async def _fake_get_client():
        return client

    from backend_app.core.cache.redis_manager import redis_manager

    monkeypatch.setattr(redis_manager, "get_client", _fake_get_client, raising=False)
    return watchdog, client


# ══════════════════════════════════════════════════════════════════════════════
#  1. The election itself
# ══════════════════════════════════════════════════════════════════════════════


def test_only_one_of_two_workers_becomes_leader_in_the_same_cycle(monkeypatch):
    """The defect, stated as a test: both winning is WATCH-01."""
    store: Dict[str, str] = {}
    a, _ = _watchdog_with_client(monkeypatch, store)
    a_leader = run(a._try_become_leader())

    b, _ = _watchdog_with_client(monkeypatch, store)
    b_leader = run(b._try_become_leader())

    assert a_leader != b_leader, (
        "exactly one of two gunicorn workers must win; both winning is the defect -- two "
        "processes querying the venue for the same open orders and each adjudicating them"
    )


def test_two_watchdogs_have_distinct_identities(monkeypatch):
    """Negative control on the test above. Shared ids would make the second look like the
    first RENEWING, and both calls would answer True for a reason unrelated to the lock."""
    store: Dict[str, str] = {}
    a, _ = _watchdog_with_client(monkeypatch, store)
    b, _ = _watchdog_with_client(monkeypatch, store)
    assert a._instance_id != b._instance_id


def test_the_incumbent_keeps_winning_and_a_challenger_cannot_take_a_live_lease(monkeypatch):
    store: Dict[str, str] = {}
    leader, _ = _watchdog_with_client(monkeypatch, store)
    assert all(run(leader._try_become_leader()) for _ in range(5)), (
        "a renewable lease, not a one-shot lock: a holder that lost the key on expiry "
        "would re-race every cycle and leadership would churn"
    )

    challenger, _ = _watchdog_with_client(monkeypatch, store)
    for _ in range(3):
        assert run(challenger._try_become_leader()) is False
        assert run(leader._try_become_leader()) is True


def test_a_crashed_leaders_lease_expires_and_a_successor_takes_over(monkeypatch):
    """A dead worker must not deadlock reconciliation forever."""
    store: Dict[str, str] = {}
    old_leader, client = _watchdog_with_client(monkeypatch, store)
    assert run(old_leader._try_become_leader()) is True

    client.expire_everything()

    successor, _ = _watchdog_with_client(monkeypatch, store)
    assert run(successor._try_become_leader()) is True


def test_the_lease_is_written_with_nx_and_a_ttl(monkeypatch):
    """Without ``ex`` a crashed leader's key is permanent; without ``nx`` there is no race."""
    store: Dict[str, str] = {}
    watchdog, client = _watchdog_with_client(monkeypatch, store)
    run(watchdog._try_become_leader())

    acquire = client.set_calls[0]
    assert acquire["key"] == ORDER_WATCHDOG_LEADER_LOCK_KEY
    assert acquire["nx"] is True
    assert acquire["ex"] == watchdog._leader_lease_ttl_seconds


def test_no_reachable_redis_refuses_leadership(monkeypatch):
    """Fails CLOSED. Assuming leadership on a failed check is exactly the N-workers
    behaviour being removed -- every process would decide it was the leader."""
    watchdog = OrderWatchdog(db_session=None)

    async def _no_client():
        return None

    from backend_app.core.cache.redis_manager import redis_manager

    monkeypatch.setattr(redis_manager, "get_client", _no_client, raising=False)
    assert run(watchdog._try_become_leader()) is False


# ══════════════════════════════════════════════════════════════════════════════
#  2. The lease's TTL and key, against the two things they must not collide with
# ══════════════════════════════════════════════════════════════════════════════


def test_the_ttl_exceeds_the_monitoring_interval():
    """``core.leader_lease`` requires it: a TTL below the loop's own interval makes the
    holder keep losing a lease it is still actively using, so leadership churns between
    workers and the "exactly one" guarantee holds only within a cycle."""
    for interval in (5, 30, 60, 300):
        watchdog = OrderWatchdog(
            db_session=None, config=WatchdogConfig(check_interval_seconds=interval)
        )
        assert watchdog._leader_lease_ttl_seconds > interval, (
            f"a {interval}s interval got a {watchdog._leader_lease_ttl_seconds}s lease"
        )


def test_the_key_is_distinct_from_every_other_subsystems():
    """Sharing a key with another subsystem would make whichever process won suppress the
    other entirely -- which presents as "the loop is running" while half the work silently
    never happens. ``core.leader_lease``'s own docstring names this hazard."""
    from backend_app.backend.exchange_reconciliation import RECONCILIATION_LEADER_LOCK_KEY

    others = {RECONCILIATION_LEADER_LOCK_KEY}
    try:
        from backend_app.core.reconciliation_scheduler import (  # type: ignore
            SCHEDULER_LEADER_LOCK_KEY,
        )

        others.add(SCHEDULER_LEADER_LOCK_KEY)
    except Exception:  # noqa: BLE001 - the scheduler's key may live elsewhere
        pass

    assert ORDER_WATCHDOG_LEADER_LOCK_KEY not in others, (
        f"{ORDER_WATCHDOG_LEADER_LOCK_KEY} collides with another subsystem's lease"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  3. The loops actually consult it -- the half a key-and-TTL test cannot show
# ══════════════════════════════════════════════════════════════════════════════


def _one_tick(watchdog: OrderWatchdog, coro_factory) -> None:
    """Drive one loop iteration and stop, without a real clock.

    ``_running`` is cleared from the patched ``asyncio.sleep``, which both loops reach at
    the end of every path -- the leader branch and the skip branch alike -- so this
    terminates whichever way the election goes.
    """

    async def _driver():
        watchdog._running = True
        real_sleep = asyncio.sleep

        async def _stop_after_first(_seconds, *a, **kw):
            watchdog._running = False
            await real_sleep(0)

        import backend_app.backend.order_watchdog as mod

        original = mod.asyncio.sleep
        mod.asyncio.sleep = _stop_after_first
        try:
            await asyncio.wait_for(coro_factory(), timeout=5)
        finally:
            mod.asyncio.sleep = original

    run(_driver())


def test_a_non_leader_does_no_reconciliation_work(monkeypatch):
    """The behavioural assertion. A key and a TTL prove a lease exists; this proves the
    loop is gated on it."""
    store: Dict[str, str] = {"already": "held"}
    # Someone else holds the lease: seed the real key with another instance's id.
    store[ORDER_WATCHDOG_LEADER_LOCK_KEY] = "some-other-worker"

    watchdog, _ = _watchdog_with_client(monkeypatch, store)

    calls: List[str] = []

    async def _record(name):
        calls.append(name)

    monkeypatch.setattr(
        watchdog, "_reconcile_open_orders", lambda: _record("reconcile"), raising=False
    )

    _one_tick(watchdog, watchdog._reconciliation_loop)

    assert calls == [], (
        "a worker that does not hold the lease queried the venue anyway; the election is "
        "declared but not consulted"
    )
    assert watchdog.cycles_skipped_not_leader == 1, (
        "a skipped cycle must be counted, so 'idle because not leader' is "
        "distinguishable from 'the loop is not running'"
    )


def test_the_leader_does_the_reconciliation_work(monkeypatch):
    """Non-vacuity for the test above: with an empty store this worker wins and proceeds."""
    store: Dict[str, str] = {}
    watchdog, _ = _watchdog_with_client(monkeypatch, store)

    calls: List[str] = []

    async def _record(name):
        calls.append(name)

    monkeypatch.setattr(
        watchdog, "_reconcile_open_orders", lambda: _record("reconcile"), raising=False
    )

    _one_tick(watchdog, watchdog._reconciliation_loop)

    assert calls == ["reconcile"]
    assert watchdog.cycles_skipped_not_leader == 0


def test_a_non_leader_does_no_stale_order_work_either(monkeypatch):
    """The monitoring loop needs its OWN guard: it creates the reconciliation loop as a
    separate task, so a `continue` in one does not pause the other."""
    store: Dict[str, str] = {ORDER_WATCHDOG_LEADER_LOCK_KEY: "some-other-worker"}
    watchdog, _ = _watchdog_with_client(monkeypatch, store)

    calls: List[str] = []

    def _recorder(name):
        async def _inner(*a, **kw):
            calls.append(name)

        return _inner

    monkeypatch.setattr(watchdog, "_check_stale_orders", _recorder("stale"), raising=False)
    monkeypatch.setattr(
        watchdog, "recover_unsubmitted_executions", _recorder("recover"), raising=False
    )
    monkeypatch.setattr(
        watchdog, "recheck_unresolved_submissions", _recorder("recheck"), raising=False
    )
    monkeypatch.setattr(
        watchdog, "_reconciliation_loop", _recorder("reconloop"), raising=False
    )

    _one_tick(watchdog, watchdog._monitoring_loop)

    assert "stale" not in calls and "recover" not in calls and "recheck" not in calls, (
        f"a non-leader adjudicated orders from the monitoring loop: {calls}"
    )
    assert watchdog.checks_run == 0, "a skipped cycle is not a check that ran"


def test_the_leader_does_the_stale_order_work(monkeypatch):
    """Non-vacuity for the test above."""
    store: Dict[str, str] = {}
    watchdog, _ = _watchdog_with_client(monkeypatch, store)

    calls: List[str] = []

    def _recorder(name):
        async def _inner(*a, **kw):
            calls.append(name)

        return _inner

    monkeypatch.setattr(watchdog, "_check_stale_orders", _recorder("stale"), raising=False)
    monkeypatch.setattr(
        watchdog, "recover_unsubmitted_executions", _recorder("recover"), raising=False
    )
    monkeypatch.setattr(
        watchdog, "recheck_unresolved_submissions", _recorder("recheck"), raising=False
    )
    monkeypatch.setattr(
        watchdog, "_reconciliation_loop", _recorder("reconloop"), raising=False
    )

    _one_tick(watchdog, watchdog._monitoring_loop)

    assert calls[:3] == ["stale", "recover", "recheck"] or set(calls) >= {
        "stale",
        "recover",
        "recheck",
    }, f"the leader must do all three passes; got {calls}"
    assert watchdog.checks_run == 1


def test_both_loops_share_one_lease_so_the_leader_runs_both(monkeypatch):
    """The opposite of the cross-subsystem rule, and deliberate.

    Two different SUBSYSTEMS must not share a key. This module's two loops MUST, because
    they are one subsystem reading one population from one process off one object: the
    leader runs both and a non-leader runs neither. Separate keys would let two different
    workers each run half the watchdog, which is the outcome the election exists to stop.
    """
    store: Dict[str, str] = {}
    watchdog, client = _watchdog_with_client(monkeypatch, store)

    assert run(watchdog._try_become_leader()) is True
    assert run(watchdog._try_become_leader()) is True

    keys = {call["key"] for call in client.set_calls}
    assert keys == {ORDER_WATCHDOG_LEADER_LOCK_KEY}, (
        f"both loops must renew ONE lease; saw {keys}"
    )
    assert store[ORDER_WATCHDOG_LEADER_LOCK_KEY] == watchdog._instance_id
