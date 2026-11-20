"""One shared renewable leader lease for background loops that must not fan out.

WHY THIS MODULE EXISTS
----------------------
``gunicorn`` runs ``--workers ${WORKERS:-2}``. Every worker process runs the FastAPI
lifespan startup hook independently, so any loop started there exists once per worker. For
a loop that only reads, that is wasteful. For one that reconciles shared rows against a
venue, it is a correctness problem: N processes examining and mutating the same
``execution_records`` / ``orders`` rows concurrently, each unaware of the others.

INF-04 fixed that for :class:`~backend_app.core.reconciliation_scheduler.Reconciliation
Scheduler` by electing a leader through Redis. RECON-01 needs the same guarantee for
:class:`~backend_app.backend.exchange_reconciliation.ExchangeReconciliationService`, and
its ledger entry names the existing implementation as "the template to reuse, not yet
applied here". Reusing it by copying 25 lines would mean two implementations of a
distributed-locking rule that must not drift, so the primitive lives here once and both
callers delegate to it.

A RENEWABLE LEASE, NOT A ONE-SHOT LOCK
--------------------------------------
A process that already holds the lease re-``SET``s the SAME key with its OWN value to
extend the TTL -- a plain ``SET`` with no ``NX``, which always succeeds for the current
holder. A process that does not hold it attempts ``SET key value NX EX`` once; winning
that call is what makes it the new leader.

A one-shot-only lock would be wrong here, and subtly so: every process would lose the key
when it expired and then re-race for it every cycle, so leadership would churn constantly
and the "exactly one at a time" property would hold only within a cycle, not across them.
The lease also means a crashed leader's hold expires on its own after ``ttl_seconds``
rather than deadlocking the subsystem forever.

FAILS CLOSED
------------
No reachable Redis answers ``False`` -- never-leader -- rather than letting every process
assume it has no competition. That direction matters: the failure mode of guessing "yes"
is the exact silent per-process fan-out this primitive exists to prevent, while the
failure mode of guessing "no" is that reconciliation pauses, which is visible and safe.

See ``redis_manager.get_client``'s own docstring for why a DEV_MODE-only mock must never
stand in for Redis outside a development deployment: a per-process dict satisfies this
API while providing none of the mutual exclusion, which
``tests/test_redis_fallback_does_not_fake_a_distributed_lock.py`` already guards for the
other distributed locks in this codebase.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def try_acquire_or_renew(
    *,
    lock_key: str,
    instance_id: str,
    ttl_seconds: int,
    subsystem: str,
) -> bool:
    """``True`` iff the caller holds the lease for ``lock_key`` for this cycle.

    Args:
        lock_key: The Redis key naming the lease. MUST be distinct per subsystem --
            two different loops sharing one key would make each one's leader suppress
            the other entirely, which looks exactly like "the loop is running" while
            half the work never happens.
        instance_id: Stable, per-process identity. A module-level singleton's
            ``uuid4()`` is the intended source: it is constant for the process's whole
            lifetime and distinct from every other worker's, which is exactly the
            identity a renewable lease needs to tell "still me, renew" apart from
            "someone else, back off".
        ttl_seconds: Lease duration. Must exceed the loop's own interval, or the holder
            will keep losing a lease it is still actively using.
        subsystem: Name used in log lines so two callers' leadership churn can be told
            apart in one log stream.

    Returns:
        ``True`` to proceed with this cycle's work, ``False`` to skip it. Never raises:
        every failure path answers ``False``.
    """
    # Imported here, not at module scope, to keep this module importable by tests and by
    # callers that never touch Redis -- the same lazy-import convention
    # ReconciliationScheduler._try_become_leader already used.
    from backend_app.core.cache.redis_manager import redis_manager

    try:
        client = await redis_manager.get_client()
        if client is None:
            logger.warning(
                "[%s] No reachable Redis client, so leadership is refused and this "
                "cycle is skipped. Failing closed: assuming leadership here would let "
                "every worker process run this loop independently.", subsystem,
            )
            return False

        current_holder: Optional[str] = await client.get(lock_key)
        if current_holder == instance_id:
            # Already the leader: renew by re-setting our own value. Plain SET, no NX,
            # so it always succeeds for the current holder.
            await client.set(lock_key, instance_id, ex=ttl_seconds)
            return True

        acquired = await client.set(lock_key, instance_id, nx=True, ex=ttl_seconds)
        if acquired:
            logger.info(
                "[%s] Acquired the leader lease (key=%s, ttl=%ss, instance=%s).",
                subsystem, lock_key, ttl_seconds, instance_id,
            )
        return bool(acquired)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[%s] Leadership check failed (%s: %s); refusing leadership for this cycle.",
            subsystem, exc.__class__.__name__, exc,
        )
        return False
