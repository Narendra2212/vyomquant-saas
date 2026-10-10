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
import os
import uuid
from urllib.parse import urlsplit

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


# ──────────────────────────────────────────────────────────────────────────────
#  3. THE PRODUCTION PATH AGAINST A REAL REDIS — gated, skips when absent
# ──────────────────────────────────────────────────────────────────────────────
#
# WHY THE TEST ABOVE WAS NOT ENOUGH, MEASURED (Phase 11B-A11/A12).
#
# `test_concurrent_reservations_are_atomic_against_real_redis` passed in CI run
# 38047483583 and that pass meant less than it looked like. It opens its OWN client and
# re-implements the increment-first-revert inline -- its comment says so: "Mirror
# reserve_quota's increment-first-revert against the real server." It borrows
# `SubscriptionEngine._meter_key` for the key name and nothing else. So what it
# establishes is that REDIS's INCR/DECR are atomic, which was never in doubt, and not
# that `SubscriptionEngine.reserve_quota` -- the function the FastAPI quota gate actually
# calls -- is correct against a real server. The six logic tests that DO call
# `reserve_quota` run against `MockRedisClient`.
#
# QUOTA-01's metered path therefore had NO real-Redis production-path evidence despite a
# green test. These two tests close exactly that gap and nothing wider: they drive
# `reserve_quota` and `get_quota_usage` themselves, through the shared cache manager, and
# cross-check the figure against the raw key.
#
# NO PRODUCTION BEHAVIOUR IS CHANGED, AND NO SEMANTICS ARE RE-SPECIFIED. The asserted
# properties are the ones `reserve_quota`'s own docstring claims: the increment happens
# first and is reverted on overflow, and a refusal reports the reverted figure rather than
# the inflated one.


async def _app_cache_or_fail():
    """Resolve the cache manager `reserve_quota` really uses, or FAIL. Never a mock.

    `backend_app.core.subscription_engine` does ``from backend_app.core.cache import
    redis_manager``, and that name is the ONE `SharedRedisManager` instance for the whole
    session, so pointing it at the real server here is what puts the production function on
    real Redis.

    `_bypass_dev_mode(True)` is the manager's own supported seam for this and is the same
    one `tests/test_webhook_duplicate_delivery_real_redis.py` uses. It is listed in
    `tests/conftest.py`'s `_REDIS_SINGLETON_SEAMS`, whose autouse
    `_restore_shared_redis_singleton` fixture restores it after every test -- so flipping it
    cannot leak into another file. No application code is touched.

    FAIL, NOT SKIP, when the manager hands back `MockRedisClient` or `None`. By the time
    this runs the gate has already established the opt-in is set AND the endpoint answers
    PING, so a mock here is a wiring fault, not an absent server -- and the in-memory dict
    would prove nothing (BUG-027). A required real-Redis assertion may not be satisfied by a
    skip.
    """
    from backend_app.core.cache import redis_manager
    from backend_app.core.cache.redis_manager import MockRedisClient

    redis_manager._bypass_dev_mode(True)
    cache = await redis_manager._get_active_cache()
    if cache is None or isinstance(cache, MockRedisClient):
        pytest.fail(
            f"the real-Redis gate passed, but the shared cache manager resolved to "
            f"{type(cache).__name__} instead of a real cache, so `reserve_quota` would "
            f"have been measured against an in-process dict. This is a cache-manager "
            f"wiring fault rather than a missing server, so it FAILS instead of skipping. "
            f"Check DEV_MODE / ENV and whether `_bypass_dev_mode(True)` took effect."
        )
    return redis_manager


async def _probe_client_on_the_apps_database():
    """A direct client on the database the APPLICATION cache uses, derived not guessed.

    THE DATABASE IS NOT A FREE CHOICE, AND GUESSING IT IS THE BUG THIS AVOIDS.
    `backend_app/backend/redis_manager.py` partitions by purpose -- `REDIS_DB_CACHE = 0`,
    `REDIS_DB_QUEUE = 1`, `REDIS_DB_EVENTS = 2` -- and `get_redis_url(db)` OVERRIDES
    whatever database a URL named, by appending a `db=` query parameter that redis-py
    resolves in preference to the path. Phase 11B-A11 measured it:
    ``ConnectionPool.from_url('redis://localhost:6379/15?db=0').connection_kwargs['db']``
    is ``0``. A test that built its client from `AERORA_TEST_REDIS_URL` therefore read a
    database the application never writes to, and two webhook assertions failed while a
    third PASSED VACUOUSLY because it asserted a key was absent.
    
    So the URL is taken from the application's own function rather than re-deriving the
    database here. Duplicating that selection logic is what drifts the moment
    `REDIS_DB_CACHE` or the override changes.
    """
    import redis.asyncio as aioredis

    from backend_app.backend.redis_manager import REDIS_DB_CACHE, get_redis_url
    from tests.real_redis_gate import ALLOW_REMOTE_VAR, _LOOPBACK_HOSTS

    url = get_redis_url(REDIS_DB_CACHE)

    # The same refusal the shared gate applies to its own opt-in, applied to the URL we
    # derived: this helper WRITES and DELETES keys, so it must not be able to reach a
    # shared or managed instance because a deployment-shaped REDIS_HOST was present.
    # The endpoint is never echoed.
    host = (urlsplit(url).hostname or "").lower()
    if host not in _LOOPBACK_HOSTS and (os.environ.get(ALLOW_REMOTE_VAR) or "").strip() != "1":
        pytest.fail(
            f"the application's effective cache URL (database {REDIS_DB_CACHE}) names a "
            f"non-loopback host and {ALLOW_REMOTE_VAR}=1 was not set. Refusing to connect. "
            f"(The host is deliberately not echoed.)"
        )
    return aioredis.from_url(url, decode_responses=True)


async def _prove_one_keyspace(manager, client) -> None:
    """Prove the manager and this file's client see the SAME keys, both directions.

    Without this, a keyspace split is indistinguishable from a quota defect: the meter
    would look empty to one side and full to the other, and an assertion that a key is
    ABSENT would pass for the wrong reason. Both directions are checked because a one-way
    probe cannot tell "the app wrote somewhere else" from "the app did not write".

    Sentinels are namespaced so they cannot collide with an application key, carry a short
    TTL so an interrupted run leaves no litter, and are removed through both paths.
    """
    nonce = uuid.uuid4().hex
    app_wrote = f"aerora:test:q01-keyspace:app:{nonce}"
    test_wrote = f"aerora:test:q01-keyspace:test:{nonce}"
    token = f"q01-probe-{nonce}"
    try:
        await manager.set(app_wrote, token, ex=30)
        seen_by_test = await client.get(app_wrote)
        if seen_by_test != token:
            pytest.fail(
                f"KEYSPACE SPLIT: the application cache manager wrote a sentinel and this "
                f"file's client cannot see it (read {seen_by_test!r}). Both ends answered, "
                f"so they are not looking at the same database. Every quota figure below "
                f"would be measured on the wrong keyspace. (No endpoint is echoed.)"
            )
        await client.set(test_wrote, token, ex=30)
        seen_by_app = await manager.get(test_wrote)
        if seen_by_app != token:
            pytest.fail(
                f"KEYSPACE SPLIT (reverse): this file wrote a sentinel and the application "
                f"cache manager cannot see it (read {seen_by_app!r})."
            )
    finally:
        for k in (app_wrote, test_wrote):
            try:
                await client.delete(k)
            except Exception:  # noqa: BLE001
                pass
            try:
                await manager.delete(k)
            except Exception:  # noqa: BLE001
                pass


@real_redis_required
async def test_production_reserve_quota_admits_exactly_the_limit_against_real_redis():
    """THE production hard-limit proof: `reserve_quota` itself, 50 racers, limit 10.

    This is the function `subscription_dependencies.enforce_quota` calls for a metered
    resource, driven concurrently against a real out-of-process counter. The figure is then
    read back through the production reader AND off the raw key, because agreeing with
    itself is not evidence -- `get_quota_usage` and the key have to agree.
    """
    redis_url = require_real_redis()  # fails (not skips) if configured but unusable
    assert redis_url  # the gate returned an endpoint; it is deliberately not echoed

    manager = await _app_cache_or_fail()
    client = await _probe_client_on_the_apps_database()

    user = f"q01-prod-{uuid.uuid4().hex[:8]}"
    resource = "backtests"
    period = _period()
    key = SubscriptionEngine._meter_key(user, resource, period)
    limit = 10
    racers = 50

    try:
        await _prove_one_keyspace(manager, client)
        await client.delete(key)

        results = await asyncio.gather(
            *[
                SubscriptionEngine.reserve_quota(
                    user, "free", resource, amount=1, limit=limit, period=period
                )
                for _ in range(racers)
            ]
        )
        allowed = [r for r in results if r[0] is True]
        refused = [r for r in results if r[0] is False]

        assert len(allowed) == limit, (
            f"production reserve_quota admitted {len(allowed)} of {racers} concurrent "
            f"reservations against a limit of {limit}; the hard limit was overrun on a real "
            f"Redis"
        )
        assert len(refused) == racers - limit

        # The production reader and the raw key must agree, and both must be exactly the
        # limit -- not above it (overrun) and not below it (over-refusal).
        via_api = await SubscriptionEngine.get_quota_usage(user, resource, period)
        via_key = int(await client.get(key))
        assert via_api == limit, f"get_quota_usage reported {via_api}, expected {limit}"
        assert via_key == limit, f"the raw meter key holds {via_key}, expected {limit}"

        # A refusal must report the REVERTED figure, never the inflated one, and the meter
        # must never be driven negative by the revert.
        for ok, usage, eff in refused:
            assert eff == limit
            assert 0 <= usage <= limit, (
                f"a refusal reported usage {usage}, outside [0, {limit}] -- the revert "
                f"either did not happen or overshot"
            )

        # A retry after exhaustion is still refused, and does not erode the figure.
        ok, usage, _eff = await SubscriptionEngine.reserve_quota(
            user, "free", resource, amount=1, limit=limit, period=period
        )
        assert ok is False, "a retry against an exhausted meter was admitted"
        assert usage == limit, f"a refused retry moved the meter to {usage}"
        assert int(await client.get(key)) == limit
    finally:
        # Only this test's own keys. The user id and period are unique per run, so this
        # cannot touch another test's meter or any application data.
        try:
            await client.delete(key)
        finally:
            await client.aclose()


@real_redis_required
async def test_production_zero_allowance_writes_no_meter_against_real_redis():
    """A zero-allowance refusal must leave NO key behind on a real server.

    `reserve_quota` short-circuits before incrementing when the effective limit is <= 0,
    precisely so a plan with no allowance cannot accumulate a figure it can never spend.
    On the in-memory substrate "no key" and "key holding 0" are easy to confuse; on a real
    server EXISTS answers it exactly.
    """
    redis_url = require_real_redis()
    assert redis_url

    manager = await _app_cache_or_fail()
    client = await _probe_client_on_the_apps_database()

    user = f"q01-prod-zero-{uuid.uuid4().hex[:8]}"
    resource = "backtests"
    period = _period()
    key = SubscriptionEngine._meter_key(user, resource, period)

    try:
        await _prove_one_keyspace(manager, client)
        await client.delete(key)

        ok, usage, eff = await SubscriptionEngine.reserve_quota(
            user, "free", resource, amount=1, limit=0, period=period
        )
        assert ok is False
        assert eff == 0
        assert usage == 0

        assert await client.exists(key) == 0, (
            "a zero-allowance refusal created a meter key; a plan with no allowance must "
            "not accumulate a usage figure it can never spend"
        )
        assert await SubscriptionEngine.get_quota_usage(user, resource, period) == 0
    finally:
        try:
            await client.delete(key)
        finally:
            await client.aclose()
