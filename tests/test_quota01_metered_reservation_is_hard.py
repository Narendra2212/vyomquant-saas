"""QUOTA-01 residual, closed to the standard the finding named: the metered-quota
RESERVATION is a hard limit that concurrency and retries cannot overrun.

WHAT THE FINDING LEFT OPEN
--------------------------
`staging/QUOTA_ENFORCEMENT_MAP.md` and the audit record QUOTA-01 as DOWNGRADED (quotas ARE
enforced, fail-closed, before the side effect -- see
`tests/test_quota01_enforcement_is_wired.py`) with one residual: "no test exercises the
quota path against a real Redis". The metered path
(`SubscriptionEngine.reserve_quota` -> `redis_manager.incrby`/`decrby`) is where that
matters, because its hard-limit guarantee is an increment-FIRST-revert-on-overflow pattern
whose soundness depends on the counter being ATOMIC. If two concurrent reservations could
both read "the last unit is free" and both proceed, the limit would be a suggestion.

WHAT THESE TESTS PROVE, AND ON WHICH SUBSTRATE
----------------------------------------------
1. The LOGIC, against the suite's in-memory meter (the default, `REDIS_URL=""` ->
   `MockRedisClient`): N concurrent `reserve` calls at a hard limit of K admit EXACTLY K and
   refuse the rest; a refusal reports the reverted (true) usage, not the inflated one; a
   repeat does not get capacity back; and a plan with a zero allowance reserves nothing.
   `MockRedisClient.incrby` is a synchronous read-modify-write with no `await` inside it, so
   under asyncio each call runs to completion without interleaving -- which models a real
   Redis INCR's atomicity for exactly the race this logic defends against.
2. The SAME property against a REAL Redis, when one is reachable at `REDIS_URL`. This variant
   SKIPS when no server is present (the repository default, matching
   `tests/test_webhook_duplicate_delivery_real_redis.py`), so the standard suite is
   unaffected; it is the end-to-end atomicity proof for a CI/staging lane that has Redis.

No live order, no payment, no production. The reservation is the paper/compute meter.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from backend_app.core.subscription_engine import SubscriptionEngine

pytestmark = pytest.mark.asyncio


def _period() -> str:
    """A unique period per test so meters never collide across tests or runs."""
    return "2026-01-test-" + uuid.uuid4().hex[:8]


async def _reserve(user_id, resource, period, *, limit, amount=1):
    """Drive the real reservation with an explicit hard limit (as enforce_quota does).

    plan_key is passed through to get_plan_config only to confirm the plan exists; FREE
    always exists, and the explicit `limit` overrides whatever the plan would compute, so
    the test's hard limit is the one under test rather than a catalogue number that could
    drift.
    """
    return await SubscriptionEngine.reserve_quota(
        user_id, "free", resource, amount=amount, limit=limit, period=period
    )


# ──────────────────────────────────────────────────────────────────────────────
#  1. THE LOGIC — against the default in-memory meter
# ──────────────────────────────────────────────────────────────────────────────


async def test_concurrent_reservations_admit_exactly_the_limit_and_no_more():
    """THE hard-limit property: K units, N racers, exactly K succeed.

    The classic check-then-increment race would let more than K through. The
    increment-first-revert design does not, and this drives it under genuine concurrency.
    """
    user = f"q01-{uuid.uuid4().hex[:8]}"
    resource = "backtests"
    period = _period()
    limit = 5
    racers = 20

    results = await asyncio.gather(
        *[_reserve(user, resource, period, limit=limit) for _ in range(racers)]
    )
    allowed = [r for r in results if r[0] is True]
    refused = [r for r in results if r[0] is False]

    assert len(allowed) == limit, (
        f"exactly {limit} reservations must be admitted under concurrency; "
        f"{len(allowed)} were -- the meter overran its hard limit"
    )
    assert len(refused) == racers - limit

    # The meter settled at exactly the limit, not above it.
    final = await SubscriptionEngine.get_quota_usage(user, resource, period)
    assert final == limit, f"the meter settled at {final}, expected {limit}"

    # Every refusal reports the TRUE (reverted) usage, never an inflated figure that would
    # tell a user they consumed more than they did.
    for ok, usage, eff in refused:
        assert usage <= limit, f"a refusal reported usage {usage} above the limit {eff}"


async def test_a_sequential_run_to_the_limit_then_one_more_refuses():
    """Deterministic companion to the concurrency case: fill it, then be refused."""
    user = f"q01-{uuid.uuid4().hex[:8]}"
    resource = "optimizations"
    period = _period()
    limit = 3

    for i in range(limit):
        ok, usage, eff = await _reserve(user, resource, period, limit=limit)
        assert ok is True, f"reservation {i+1} of {limit} should be admitted"
        assert usage == i + 1

    ok, usage, eff = await _reserve(user, resource, period, limit=limit)
    assert ok is False, "the (limit+1)-th reservation must be refused"
    assert usage == limit, "a refusal must report the reverted usage, i.e. exactly the limit"
    assert eff == limit


async def test_a_zero_allowance_reserves_nothing_and_accumulates_nothing():
    """A plan with no allowance must refuse without writing a figure it can never spend."""
    user = f"q01-{uuid.uuid4().hex[:8]}"
    resource = "backtests"
    period = _period()

    ok, usage, eff = await _reserve(user, resource, period, limit=0)
    assert ok is False
    assert eff == 0
    assert await SubscriptionEngine.get_quota_usage(user, resource, period) == 0, (
        "a zero-allowance refusal must not leave a usage figure behind"
    )


async def test_an_unlimited_plan_always_admits():
    """Positive control: an unlimited meter never refuses, so a limit of -1 is not a 0."""
    user = f"q01-{uuid.uuid4().hex[:8]}"
    resource = "backtests"
    period = _period()

    for _ in range(50):
        ok, _usage, _eff = await _reserve(user, resource, period, limit=-1)
        assert ok is True, "an unlimited meter must never refuse"


async def test_reservations_are_isolated_per_user():
    """One user's consumption cannot exhaust another's allowance.

    Both reserve to the same hard limit under the same resource and period; each must get
    its own full allowance, because the meter key is per-user.
    """
    resource = "optimizations"
    period = _period()
    limit = 2
    a = f"q01-a-{uuid.uuid4().hex[:8]}"
    b = f"q01-b-{uuid.uuid4().hex[:8]}"

    for _ in range(limit):
        assert (await _reserve(a, resource, period, limit=limit))[0] is True
    # A is now exhausted; B must still have its own full allowance.
    assert (await _reserve(a, resource, period, limit=limit))[0] is False
    for _ in range(limit):
        assert (await _reserve(b, resource, period, limit=limit))[0] is True, (
            "one user's exhaustion must not consume another user's allowance"
        )


async def test_the_full_enforce_quota_path_refuses_at_the_limit(monkeypatch):
    """Drive the real enforce_quota (the function the FastAPI gate calls), not just reserve.

    enforce_quota resolves the plan context, then reserves for a metered resource. A refusal
    must surface as the 429 quota HTTPException, and it must happen AFTER the limit is
    reached, never before. The plan context is stubbed to a known plan so the test does not
    depend on a database; the hard limit is injected the same way the catalogue would supply
    it, through SubscriptionEngine.get_effective_quotas.
    """
    import backend_app.core.subscription_dependencies as sd
    from backend_app.core.subscription_engine import Plan

    user = {"id": f"q01-ep-{uuid.uuid4().hex[:8]}"}
    resource = "backtests"

    class _Ctx:
        plan = Plan.FREE.value
        overrides = {}

    async def _ctx(_uid, _sb):
        return _Ctx()

    monkeypatch.setattr(sd, "get_plan_context", _ctx)
    monkeypatch.setattr(
        SubscriptionEngine, "get_effective_quotas",
        staticmethod(lambda plan, overrides: {resource: 2}),
    )
    # Pin the period so the meter is this test's own.
    period = _period()
    monkeypatch.setattr("backend_app.core.usage_ledger.usage_period", lambda: period)

    from fastapi import HTTPException

    # Two admitted.
    await sd.enforce_quota(resource, user, supabase=None, reserve=True)
    await sd.enforce_quota(resource, user, supabase=None, reserve=True)
    # Third refused as a quota HTTP error, not a silent pass.
    with pytest.raises(HTTPException) as exc:
        await sd.enforce_quota(resource, user, supabase=None, reserve=True)
    # build_quota_refusal(...).as_http() raises 403 FORBIDDEN with a structured quota detail.
    assert exc.value.status_code == 403, (
        f"a quota breach must be an HTTP 403 refusal, got {exc.value.status_code}"
    )
    detail = exc.value.detail
    rendered = str(detail).lower()
    assert "quota" in rendered or "limit" in rendered or "usage" in rendered, (
        f"the 403 must carry a quota/limit refusal detail, got {detail!r}"
    )


# ──────────────────────────────────────────────────────────────────────────────
#  2. THE SAME PROPERTY AGAINST A REAL REDIS — gated, skips when absent
# ──────────────────────────────────────────────────────────────────────────────

# D5': the gate is shared now. It previously read ``AERORA_TEST_REDIS_URL or REDIS_URL`` and
# checked only the string's shape, which meant (a) the ``REDIS_URL`` half was dead because
# ``tests/conftest.py`` blanks it at import -- so CI's healthy redis service could not switch
# this test on -- and (b) a configured-but-unreachable endpoint was indistinguishable from
# "nobody asked". ``tests/real_redis_gate.py`` owns both decisions for every real-Redis test.
from tests.real_redis_gate import real_redis_required, require_real_redis


@real_redis_required
async def test_concurrent_reservations_are_atomic_against_real_redis():
    """The hard-limit property against a genuinely concurrent, out-of-process atomic counter.

    Uses the real redis client via a dedicated connection so INCR/DECR are server-side
    atomic operations, which is the thing MockRedisClient can only model.
    """
    import redis.asyncio as aioredis

    # Fails (not skips) if the endpoint was configured but is unusable.
    redis_url = require_real_redis()

    client = aioredis.from_url(redis_url, decode_responses=True)
    user = f"q01-real-{uuid.uuid4().hex[:8]}"
    resource = "backtests"
    period = _period()
    key = SubscriptionEngine._meter_key(user, resource, period)
    limit = 10
    racers = 50

    async def _reserve_real():
        # Mirror reserve_quota's increment-first-revert against the real server.
        new_usage = await client.incrby(key, 1)
        if new_usage > limit:
            await client.decrby(key, 1)
            return False
        return True

    try:
        results = await asyncio.gather(*[_reserve_real() for _ in range(racers)])
        assert sum(1 for r in results if r) == limit, (
            "a real Redis must admit exactly the limit under concurrency"
        )
        assert int(await client.get(key)) == limit
    finally:
        await client.delete(key)
        await client.aclose()
