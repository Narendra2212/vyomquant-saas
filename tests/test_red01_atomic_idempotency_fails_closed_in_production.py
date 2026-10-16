"""RED-01: DistributedIdempotency._atomic_check_and_set used to fail OPEN on a Redis
error, unlike its own sibling start_processing.

core/cache/redis_manager.py's SharedRedisManager.incrby/decrby/get/etc. swallow a
connection error and return a neutral value (0, None) -- a deliberate, documented
design for the many callers where that is harmless. The FIN-CRITICAL-004 atomic
check-and-set this module added specifically to close a duplicate-order-execution race
was NOT one of those callers: its own `except Exception` handler returned
``(None, False)`` unconditionally, identical to the normal "lock not acquired" outcome
of a healthy, merely-contended lock. A caller cannot tell "someone else is already
processing this order" apart from "Redis could not answer the question at all" --
and the retry loop around it (``check_idempotency_atomic``) ultimately falls through
to ``start_processing``, which correctly raises in production, so a persistent outage
was eventually caught, just after needless retries, and a TRANSIENT failure mid-call
could still have let an order through unprotected on that one attempt.

Fixed to match start_processing's own, already-correct contract: in production, a
Redis failure during this specific check raises rather than returning a value a caller
cannot distinguish from "no problem here."
"""

from __future__ import annotations

import pytest

from backend_app.core.distributed_idempotency import DistributedIdempotency


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ENV", raising=False)


class TestAtomicCheckAndSetFailsClosedInProduction:
    @pytest.mark.asyncio
    async def test_a_redis_failure_raises_in_production_rather_than_reporting_no_lock(
        self, monkeypatch
    ):
        monkeypatch.setenv("ENV", "production")
        idempotency = DistributedIdempotency()

        async def _raise(*args, **kwargs):
            raise ConnectionError("simulated Redis outage")

        monkeypatch.setattr(
            "backend_app.core.distributed_idempotency.redis_manager.eval_lua", _raise
        )

        with pytest.raises(RuntimeError, match="CRITICAL"):
            await idempotency._atomic_check_and_set(
                "tenant-1", "order-1", "owner-1", "binance"
            )

    @pytest.mark.asyncio
    async def test_outside_production_the_old_graceful_degradation_still_applies(
        self, monkeypatch
    ):
        """Unchanged behaviour for dev/test: a non-production Redis outage still
        degrades to (None, False) rather than crashing every local/test run that
        does not have a real Redis available."""
        idempotency = DistributedIdempotency()

        async def _raise(*args, **kwargs):
            raise ConnectionError("simulated Redis outage")

        monkeypatch.setattr(
            "backend_app.core.distributed_idempotency.redis_manager.eval_lua", _raise
        )

        cached_result, lock_acquired = await idempotency._atomic_check_and_set(
            "tenant-1", "order-1", "owner-1", "binance"
        )

        assert cached_result is None
        assert lock_acquired is False

    @pytest.mark.asyncio
    async def test_a_healthy_redis_in_production_is_unaffected(self, monkeypatch):
        """The fix only changes the failure path; a working Redis in production must
        still acquire the lock exactly as before."""
        monkeypatch.setenv("ENV", "production")
        idempotency = DistributedIdempotency()

        async def _healthy(script, keys, args):
            return [0, args[0]]  # lock acquired, payload echoed back

        monkeypatch.setattr(
            "backend_app.core.distributed_idempotency.redis_manager.eval_lua", _healthy
        )

        cached_result, lock_acquired = await idempotency._atomic_check_and_set(
            "tenant-1", "order-1", "owner-1", "binance"
        )

        assert cached_result is None
        assert lock_acquired is True
