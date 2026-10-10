"""QUOTA-01: the exchange-connection limit holds under concurrency, retry and failure.

THE DEFECT, MEASURED BEFORE THE FIX (``staging/p10c_probe_before.txt``)
----------------------------------------------------------------------
``check_exchange_connection_slot`` reads the account's venue set, decides, and returns; the
handler then writes the row. Between those two steps the figure it decided on is stale. Two
concurrent requests for DIFFERENT venues therefore both read ``len(existing) == limit - 1``
and both pass:

    plan limit for exchange_connections (free): 1
      binance    -> ADMITTED
      kraken     -> ADMITTED
    connections now held: 2 (binance, kraken)

A Free account, whose limit is ONE, finished holding two sets of venue credentials.

``exchange_keys`` carries ``UNIQUE (user_id, exchange_id)``, which dedupes the SAME venue —
that is why a double-submit of one venue was never a problem, and why the race needs two
DIFFERENT venues to show. The constraint says nothing about the number of distinct rows,
which is what the plan limit is about.

THE FIX
-------
``exchange_connection_slot_guard`` (``core/subscription_dependencies.py``) takes a short
per-account ``SET NX`` lock on the shared cache, RE-READS the venue set inside it, decides
again, and holds the lock across the caller's write. ``store_keys`` wraps its vault write in
it. The original dependency stays where it is as a fast pre-check, so an over-limit account is
still refused before its credential is sent to a venue.

The lock is cross-process, which is the point: an ``asyncio.Lock`` would be per worker, and
this runs under several. It FAILS CLOSED — 409 when another request for the account holds it,
503 when the lock store cannot be reached — because a guard that cannot run has not run, and
admitting on those grounds is the fail-open shape ``HardQuotaEnforcer`` was deprecated for.

WHAT IS NOT CLAIMED
-------------------
This is not a database constraint and is weaker than one. The durable fix has the shape WORK-03
used for bots (``pk_bot_ownership_claims``): a constraint in the schema, which holds whatever
any application process believes. That is a shared migration and is recorded as a coordinator
dependency in ``staging/PHASE10_WSC_QUOTA_CONNECTION_SAFETY.md`` rather than taken here.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Set

import pytest
from fastapi import HTTPException

from backend_app.core import subscription_dependencies as sd
from backend_app.core import usage_ledger
from backend_app.core.subscription_engine import Resource

EXCHANGE_CONNECTIONS = Resource.EXCHANGE_CONNECTIONS.value


# ══════════════════════════════════════════════════════════════════════════════════════════
# A lock store with Redis's SET NX semantics, and the two failure modes that matter
# ══════════════════════════════════════════════════════════════════════════════════════════


class _LockStore:
    """An in-process stand-in for the shared cache, with real ``SET NX`` semantics.

    ``nx`` must mean "only if absent" or this suite would prove nothing: the whole guard rests
    on exactly one caller winning the key. ``unavailable`` models the facade's behaviour when
    there is no cache outside development — ``set`` returns ``False`` and ``get`` returns
    ``None`` — which is what distinguishes a contended lock from an unreachable one.
    """

    def __init__(self):
        self.store: Dict[str, str] = {}
        self.unavailable = False
        self.raise_on_set = False
        self.raise_on_get = False
        self.set_calls = 0
        self.delete_calls = 0

    async def set(self, key: str, value: str, **kwargs):
        self.set_calls += 1
        if self.raise_on_set:
            raise RuntimeError("cache connection reset")
        if self.unavailable:
            return False
        if kwargs.get("nx") and key in self.store:
            return False
        self.store[key] = value
        return True

    async def get(self, key: str):
        if self.raise_on_get:
            raise RuntimeError("cache connection reset")
        if self.unavailable:
            return None
        return self.store.get(key)

    async def delete(self, *keys: str):
        self.delete_calls += 1
        for key in keys:
            self.store.pop(key, None)
        return len(keys)


class _Account:
    """One account's committed ``exchange_keys`` rows, plus the plan limit over them."""

    def __init__(self, limit: int, venues: Optional[Set[str]] = None):
        self.limit = limit
        self.venues: Set[str] = set(venues or ())
        self.read_count = 0

    async def exchange_ids(self, user_id, supabase):
        self.read_count += 1
        # Reflects only what has been COMMITTED, exactly as a database read would. A fake that
        # returned pending writes would hide the very window being tested.
        await asyncio.sleep(0)
        return set(self.venues)


@pytest.fixture
def slot_env(monkeypatch):
    """Wire the guard to a controlled lock store, plan and account, and restore afterwards.

    Everything real runs: ``exchange_connection_slot_guard``,
    ``check_exchange_connection_slot``, the refusal builders and the plan-limit lookup. Only the
    cache and the ``exchange_keys`` read are substituted, because those are the two things that
    would otherwise require a Redis and a Postgres to be standing.
    """
    accounts: Dict[str, _Account] = {}
    locks = _LockStore()
    plans: Dict[str, str] = {}

    async def _exchange_ids(user_id, supabase):
        return await accounts[user_id].exchange_ids(user_id, supabase)

    async def _plan_context(user_id, supabase):
        return sd.PlanContext(
            plan=plans.get(user_id, "free"), tier="free", display_name="Free"
        )

    monkeypatch.setattr(usage_ledger, "exchange_ids", _exchange_ids)
    monkeypatch.setattr(sd.usage_ledger, "exchange_ids", _exchange_ids)
    monkeypatch.setattr(sd, "get_plan_context", _plan_context)
    monkeypatch.setattr(sd, "redis_manager", locks)

    yield type(
        "SlotEnv",
        (),
        {"accounts": accounts, "locks": locks, "plans": plans},
    )()


async def _attempt_store(env, user_id: str, venue: str, *, delay: float = 0.0):
    """One ``POST /api/exchange/keys`` as far as the slot control is concerned.

    The pre-check, then the guarded write — the handler's real order. ``delay`` widens the
    window between the guarded re-read and the commit, which is where the race lived.
    """
    user = {"id": user_id}
    account = env.accounts[user_id]
    try:
        # The fast pre-check the handler runs first.
        await sd.check_exchange_connection_slot(user, object(), venue)
        async with sd.exchange_connection_slot_guard(user, object(), venue):
            if delay:
                await asyncio.sleep(delay)
            account.venues.add(venue.strip().lower())  # the vault write
        return ("ADMITTED", None)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return ("REFUSED", (exc.status_code, detail.get("error")))


# ══════════════════════════════════════════════════════════════════════════════════════════
# 1. Concurrency — the measured defect, inverted
# ══════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_two_concurrent_stores_of_different_venues_cannot_both_take_one_free_slot(
    slot_env,
):
    """The exact interleaving from the probe. A Free account must end with ONE connection."""
    slot_env.accounts["u"] = _Account(limit=1)

    results = await asyncio.gather(
        _attempt_store(slot_env, "u", "binance", delay=0.01),
        _attempt_store(slot_env, "u", "kraken", delay=0.01),
    )

    admitted = [r for r in results if r[0] == "ADMITTED"]
    assert len(admitted) == 1, (
        f"{len(admitted)} of 2 concurrent requests were admitted against a limit of 1: {results}"
    )
    assert len(slot_env.accounts["u"].venues) == 1, (
        f"the account holds {slot_env.accounts['u'].venues} against a limit of 1"
    )
    # The loser is refused with a code a client can act on, not a generic 500.
    refused = [r for r in results if r[0] == "REFUSED"]
    assert len(refused) == 1
    status_code, error = refused[0][1]
    assert status_code in (409, 403), f"unexpected refusal status {status_code} ({error})"


@pytest.mark.asyncio
async def test_eight_concurrent_stores_admit_exactly_the_limit(slot_env):
    """Scaled up, with a limit above one, so the guard is not just a mutual-exclusion accident.

    A Pro Quant account (limit 5) receiving eight concurrent connection requests for eight
    distinct venues must finish holding exactly five.
    """
    slot_env.accounts["u"] = _Account(limit=5)
    slot_env.plans["u"] = "pro"

    venues = ["binance", "kraken", "coinbase", "okx", "bybit", "kucoin", "gate", "bitget"]
    results = await asyncio.gather(
        *[_attempt_store(slot_env, "u", v, delay=0.005) for v in venues]
    )

    held = slot_env.accounts["u"].venues
    assert len(held) <= 5, f"the account holds {len(held)} connections against a limit of 5: {held}"
    assert len(held) == 5, (
        f"the guard admitted only {len(held)} of 5 available slots, which is over-refusal: "
        f"{results}"
    )


@pytest.mark.asyncio
async def test_concurrent_requests_for_the_same_venue_are_a_rotation_not_two_slots(slot_env):
    """Two concurrent rotations of one venue must not be refused, and must not consume a slot.

    Rotating a compromised key is the security-relevant operation here, and a plan limit is the
    last thing that should obstruct it. An account AT its cap must still be able to rotate.
    """
    slot_env.accounts["u"] = _Account(limit=1, venues={"binance"})

    results = await asyncio.gather(
        _attempt_store(slot_env, "u", "binance", delay=0.01),
        _attempt_store(slot_env, "u", "binance", delay=0.01),
    )

    # Both are rotations of a venue already held. Neither may be refused as over-limit; one may
    # be asked to retry because the account has a change in flight, which is not a quota answer.
    for outcome, info in results:
        if outcome == "REFUSED":
            status_code, error = info
            assert status_code == 409, (
                f"a key rotation was refused as over-limit ({status_code} {error}); an account "
                f"at its cap must still be able to rotate the keys it owns"
            )
    assert slot_env.accounts["u"].venues == {"binance"}, "a rotation consumed a slot"


@pytest.mark.asyncio
async def test_different_accounts_do_not_contend_with_each_other(slot_env):
    """QUOTA-01 tenant isolation: the lock is per account, so one tenant cannot stall another.

    A lock keyed too broadly — on the resource rather than the account — would make every
    customer's key storage serialise behind every other's, turning a correctness fix into a
    throughput defect and letting one tenant's traffic refuse another's requests with 409.
    """
    for uid in ("a", "b", "c", "d"):
        slot_env.accounts[uid] = _Account(limit=1)

    results = await asyncio.gather(
        *[_attempt_store(slot_env, uid, "binance", delay=0.01) for uid in "abcd"]
    )

    assert all(r[0] == "ADMITTED" for r in results), (
        f"accounts contended with each other: {results}"
    )
    for uid in "abcd":
        assert slot_env.accounts[uid].venues == {"binance"}


@pytest.mark.asyncio
async def test_one_tenant_cannot_consume_or_alter_another_tenants_quota(slot_env):
    """An exhausted account must not affect a different account's capacity.

    The usage figure and the limit are both derived from the caller's own token-derived id, so
    this is really a test that no shared counter was introduced by the guard — a per-resource
    rather than per-account lock key, or a global admission count, would show here.
    """
    slot_env.accounts["rich"] = _Account(limit=5)
    slot_env.plans["rich"] = "pro"
    slot_env.accounts["poor"] = _Account(limit=1)

    # `poor` exhausts its single slot.
    assert (await _attempt_store(slot_env, "poor", "binance"))[0] == "ADMITTED"
    outcome, info = await _attempt_store(slot_env, "poor", "kraken")
    assert outcome == "REFUSED" and info[0] == 403

    # `rich` is unaffected, and still has its full allowance.
    for venue in ("binance", "kraken", "coinbase", "okx", "bybit"):
        assert (await _attempt_store(slot_env, "rich", venue))[0] == "ADMITTED", venue
    assert len(slot_env.accounts["rich"].venues) == 5
    # And `poor` did not gain capacity from `rich`'s plan.
    assert slot_env.accounts["poor"].venues == {"binance"}


# ══════════════════════════════════════════════════════════════════════════════════════════
# 2. Retry and failure accounting
# ══════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_retrying_a_refused_request_does_not_eventually_succeed(slot_env):
    """A retry must not be a way through. Twenty attempts at an exhausted cap, all refused.

    The counted-resource path reads persistence rather than a counter, so there is nothing for a
    retry to erode — but that is a property of the design, not a given, and a guard that leaked
    an admission every N attempts would show here.
    """
    slot_env.accounts["u"] = _Account(limit=1, venues={"binance"})

    outcomes = []
    for _ in range(20):
        outcomes.append(await _attempt_store(slot_env, "u", "kraken"))

    assert all(o[0] == "REFUSED" for o in outcomes), (
        f"a retry got through: {[o for o in outcomes if o[0] == 'ADMITTED']}"
    )
    assert all(o[1][0] == 403 for o in outcomes), f"inconsistent refusals: {set(o[1] for o in outcomes)}"
    assert slot_env.accounts["u"].venues == {"binance"}


@pytest.mark.asyncio
async def test_the_lock_is_released_when_the_guarded_write_raises(slot_env):
    """Accounting stays consistent after a failure: a failed write must not wedge the account.

    If the lock leaked on the exception path, the account's NEXT request would be refused 409
    until the TTL expired — a failed key rotation would lock the customer out of retrying for
    fifteen seconds, which is the kind of residue that looks like an outage.
    """
    slot_env.accounts["u"] = _Account(limit=2)
    user = {"id": "u"}

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        async with sd.exchange_connection_slot_guard(user, object(), "binance"):
            raise _Boom("vault write failed")

    assert slot_env.locks.store == {}, "the lock survived an exception inside the guard"
    # The account is immediately usable again, and nothing was counted for the failed attempt.
    assert slot_env.accounts["u"].venues == set()
    assert (await _attempt_store(slot_env, "u", "binance"))[0] == "ADMITTED"


@pytest.mark.asyncio
async def test_a_failed_write_consumes_no_capacity(slot_env):
    """The figure is COUNTED from persistence, so a write that did not happen cannot be counted.

    Five failed attempts followed by a real one: the account must still have its full allowance.
    This is the counted-resource counterpart to the metered path's deliberate
    "a failed backtest is not refunded" rule — the difference is intentional, because a counted
    resource has nothing to refund.
    """
    slot_env.accounts["u"] = _Account(limit=1)
    user = {"id": "u"}

    for _ in range(5):
        with pytest.raises(RuntimeError):
            async with sd.exchange_connection_slot_guard(user, object(), "binance"):
                raise RuntimeError("write failed")

    assert slot_env.accounts["u"].venues == set()
    assert (await _attempt_store(slot_env, "u", "binance"))[0] == "ADMITTED", (
        "failed attempts consumed the account's only slot"
    )


@pytest.mark.asyncio
async def test_the_lock_is_released_on_the_guards_own_refusal(slot_env):
    """An over-limit refusal raised INSIDE the lock must still release it.

    The guard re-checks after acquiring, so the refusal is raised while holding the lock. A
    `finally` that only ran on the success path would leave it held for the TTL.
    """
    slot_env.accounts["u"] = _Account(limit=1, venues={"binance"})
    user = {"id": "u"}

    with pytest.raises(HTTPException) as excinfo:
        async with sd.exchange_connection_slot_guard(user, object(), "kraken"):
            pytest.fail("the body must not run when the guard refuses")

    assert excinfo.value.status_code == 403
    assert slot_env.locks.store == {}, "the lock survived the guard's own refusal"


# ══════════════════════════════════════════════════════════════════════════════════════════
# 3. Fail-closed when the guard cannot run
# ══════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_an_unreachable_lock_store_refuses_rather_than_admits(slot_env):
    """The decisive direction. A guard that cannot run must not wave the request through.

    This is the precise failure mode ``HardQuotaEnforcer`` was deprecated for: its usage counts
    come through a facade that swallows errors and answers zero, so a cache outage made every
    limit admit. The guard answers 503 instead.
    """
    slot_env.accounts["u"] = _Account(limit=1)
    slot_env.locks.unavailable = True
    user = {"id": "u"}

    with pytest.raises(HTTPException) as excinfo:
        async with sd.exchange_connection_slot_guard(user, object(), "binance"):
            pytest.fail("the write ran while the limit could not be enforced")

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["error"] == "ENTITLEMENT_SLOT_LOCK_UNAVAILABLE"
    assert slot_env.accounts["u"].venues == set()


@pytest.mark.asyncio
async def test_a_lock_store_that_raises_also_refuses(slot_env):
    """An exception from the cache is the same answer as an absent one: refuse.

    Stated separately because the two take different branches — a raising client is caught, a
    missing one returns falsy — and a fix for one could easily miss the other.
    """
    slot_env.accounts["u"] = _Account(limit=1)
    slot_env.locks.raise_on_set = True
    user = {"id": "u"}

    with pytest.raises(HTTPException) as excinfo:
        async with sd.exchange_connection_slot_guard(user, object(), "binance"):
            pytest.fail("the write ran while the limit could not be enforced")

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["error"] == "ENTITLEMENT_SLOT_LOCK_UNAVAILABLE"


@pytest.mark.asyncio
async def test_a_contended_lock_is_409_and_not_503(slot_env):
    """Contention and unavailability are different events and get different codes.

    409 tells a client to retry a request that is fine; 503 says the control is broken. Both
    refuse, so a misclassification is never an admission — but a client that cannot tell them
    apart will retry a broken deployment in a loop.
    """
    slot_env.accounts["u"] = _Account(limit=2)
    user = {"id": "u"}

    async with sd.exchange_connection_slot_guard(user, object(), "binance"):
        # A second request for the same account arrives while the first holds the lock.
        with pytest.raises(HTTPException) as excinfo:
            async with sd.exchange_connection_slot_guard(user, object(), "kraken"):
                pytest.fail("two requests held the same account's slot lock at once")

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "EXCHANGE_CONNECTION_CHANGE_IN_FLIGHT"
    # Released by the outer guard on exit, so the account is usable again immediately.
    assert slot_env.locks.store == {}


@pytest.mark.asyncio
async def test_the_lock_carries_a_ttl_so_a_killed_worker_cannot_wedge_an_account(slot_env):
    """The lock must expire. A worker killed mid-write would otherwise hold it forever.

    Asserted on the arguments passed to the cache rather than by waiting, because a test that
    slept for the real TTL would add fifteen seconds to the suite for no extra information.
    """
    captured: List[dict] = []
    real_set = slot_env.locks.set

    async def _recording_set(key, value, **kwargs):
        captured.append({"key": key, **kwargs})
        return await real_set(key, value, **kwargs)

    slot_env.locks.set = _recording_set
    slot_env.accounts["u"] = _Account(limit=2)

    async with sd.exchange_connection_slot_guard({"id": "u"}, object(), "binance"):
        pass

    assert captured, "the guard did not touch the lock store"
    call = captured[0]
    assert call.get("nx") is True, "the lock was not taken with SET NX; it is not a lock"
    assert isinstance(call.get("ex"), int) and call["ex"] > 0, (
        f"the lock has no expiry ({call.get('ex')!r}); a killed worker would hold it forever"
    )
    assert "u" in call["key"], "the lock key is not scoped to the account"
    assert EXCHANGE_CONNECTIONS in call["key"], "the lock key is not scoped to the resource"


# ══════════════════════════════════════════════════════════════════════════════════════════
# 4. Negative controls — this suite must be able to fail
# ══════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_guard_that_does_not_lock_fails_this_suite(slot_env):
    """Deliberately break the enforcement path and confirm the concurrency test notices.

    With ``nx`` neutered the lock stops excluding anyone, which is precisely the pre-fix
    behaviour: both concurrent requests re-read the same stale figure and both pass. If this
    assertion did not hold, the concurrency tests above would be passing for some other reason
    and would not be measuring the guard at all.
    """
    slot_env.accounts["u"] = _Account(limit=1)

    async def _set_without_nx(key, value, **kwargs):
        kwargs.pop("nx", None)  # the sabotage: every caller "acquires"
        slot_env.locks.store[key] = value
        return True

    slot_env.locks.set = _set_without_nx

    results = await asyncio.gather(
        _attempt_store(slot_env, "u", "binance", delay=0.01),
        _attempt_store(slot_env, "u", "kraken", delay=0.01),
    )

    admitted = [r for r in results if r[0] == "ADMITTED"]
    assert len(admitted) == 2, (
        "removing NX did NOT reproduce the overshoot, so these tests are not measuring the "
        f"lock: {results}"
    )
    assert len(slot_env.accounts["u"].venues) == 2


@pytest.mark.asyncio
async def test_a_guard_that_does_not_recheck_fails_this_suite(slot_env):
    """The second half of the fix is the RE-READ, not only the lock.

    A guard that locked but trusted the handler's earlier pre-check would serialise the writes
    and still admit both, because both pre-checks ran before either write. This pins that the
    re-read inside the lock is load-bearing.
    """
    slot_env.accounts["u"] = _Account(limit=1)
    real_check = sd.check_exchange_connection_slot

    async def _no_recheck(user, supabase, exchange_id):
        return None  # the sabotage: the guard trusts the stale pre-check

    sd.check_exchange_connection_slot = _no_recheck
    try:
        # Both pre-checks run first, against an empty account, exactly as concurrent requests
        # would; then both writes happen under the lock.
        await real_check({"id": "u"}, object(), "binance")
        await real_check({"id": "u"}, object(), "kraken")
        for venue in ("binance", "kraken"):
            async with sd.exchange_connection_slot_guard({"id": "u"}, object(), venue):
                slot_env.accounts["u"].venues.add(venue)
    finally:
        sd.check_exchange_connection_slot = real_check

    assert len(slot_env.accounts["u"].venues) == 2, (
        "removing the re-read did NOT reproduce the overshoot, so the re-read is not what "
        "these tests are measuring"
    )

    # And with the real guard restored, the same sequence is bounded.
    slot_env.accounts["v"] = _Account(limit=1)
    await real_check({"id": "v"}, object(), "binance")
    await real_check({"id": "v"}, object(), "kraken")
    admitted = 0
    for venue in ("binance", "kraken"):
        try:
            async with sd.exchange_connection_slot_guard({"id": "v"}, object(), venue):
                slot_env.accounts["v"].venues.add(venue)
            admitted += 1
        except HTTPException:
            pass
    assert admitted == 1, "the restored guard admitted more than the limit"
    assert len(slot_env.accounts["v"].venues) == 1


# ══════════════════════════════════════════════════════════════════════════════════════════
# 5. The guard is wired, not merely available
# ══════════════════════════════════════════════════════════════════════════════════════════


def test_store_keys_uses_the_guard_around_its_vault_write():
    """A control with no call sites is this repository's recurring finding; pin the wiring.

    Read as source rather than exercised end to end, because driving the whole handler needs a
    vault, a venue and a Supabase. ``tests/test_conn01_rotation_evicts_pooled_client.py`` drives
    the handler itself; this asserts the guard is in it and WRAPS the write, which is the
    property that matters — a guard entered after the write would prove nothing.
    """
    import inspect

    from backend_app.routers import exchange as xr

    src = inspect.getsource(xr.store_keys)
    assert "exchange_connection_slot_guard" in src, (
        "store_keys does not use exchange_connection_slot_guard; the connection limit is back "
        "to a read-then-write race (QUOTA-01)"
    )

    guard_at = src.index("exchange_connection_slot_guard(")
    write_at = src.index("vault.store_exchange_keys(")
    assert guard_at < write_at, (
        "the guard is entered after the vault write, so the write it is supposed to authorise "
        "has already happened"
    )

    # The fast pre-check is still there: an over-limit account must be refused before its
    # credential is sent to a venue.
    assert "check_exchange_connection_slot" in src, (
        "the pre-check was removed; an over-limit account would now have its credential sent "
        "to the exchange before being refused"
    )
    assert src.index("check_exchange_connection_slot(") < src.index("ConnectionEngine("), (
        "the pre-check no longer runs before the venue handshake"
    )


# ══════════════════════════════════════════════════════════════════════════════════════════
# 6. The same property against a REAL Redis — gated, skips when absent
# ══════════════════════════════════════════════════════════════════════════════════════════
#
# The tests above drive the real guard against an in-process lock store with genuine ``SET NX``
# semantics, which is what makes the admission counting decidable here. What that substrate
# CANNOT model is the thing the guard actually relies on in production: that ``SET NX`` is
# atomic ACROSS PROCESSES, on a server. Two uvicorn workers are not two coroutines.
#
# So the property is restated against a real server and gated.
#
# D5': THE GATE MOVED, AND THE GATE WAS THE DEFECT. This block previously read
# ``AERORA_TEST_REDIS_URL or REDIS_URL`` and tested only the SHAPE of the string. Both halves
# were wrong. ``tests/conftest.py`` blanks ``REDIS_URL`` unconditionally at import, so the
# fallback could never contribute a value -- which is why the CI ``unit-tests`` job, with a
# healthy ``redis:7-alpine`` service exported as ``REDIS_URL``, still skipped these tests
# (measured: ``staging/p10k_gate_predicate.txt``). And a shape-only check cannot tell a
# reachable server from a dead one, so a broken CI service would have read as a skip.
#
# ``tests/real_redis_gate.py`` now owns that decision for every real-Redis test: one opt-in
# name, a PING to decide reachability, a refusal to touch a non-loopback host without an
# explicit flag, and three distinct outcomes -- unset SKIPS, configured-but-broken FAILS,
# reachable RUNS. A skip here is NOT a pass.
from tests.real_redis_gate import real_redis_required, require_real_redis


@real_redis_required
@pytest.mark.asyncio
async def test_the_slot_lock_is_mutually_exclusive_against_real_redis():
    """Exactly one of N concurrent acquirers may hold one account's slot lock on a real server.

    Driven through the production ``_acquire_slot_lock`` against a real client, so what is
    measured is the code that runs in production rather than a restatement of it.
    """
    import uuid as _uuid

    import redis.asyncio as aioredis

    # Fails (not skips) if the endpoint was configured but is unusable.
    redis_url = require_real_redis()

    client = aioredis.from_url(redis_url, decode_responses=True)
    user = f"q01-slot-{_uuid.uuid4().hex[:8]}"
    lock_key = sd._slot_lock_key(user, EXCHANGE_CONNECTIONS)

    class _RealCache:
        """The facade's surface, backed by the real server."""

        async def set(self, key, value, **kwargs):
            return await client.set(key, value, nx=kwargs.get("nx", False), ex=kwargs.get("ex"))

        async def get(self, key):
            return await client.get(key)

        async def delete(self, *keys):
            return await client.delete(*keys)

    original = sd.redis_manager
    sd.redis_manager = _RealCache()
    try:
        await client.delete(lock_key)
        # Twenty racers; the wait budget is deliberately bypassed by asking for the raw
        # acquire, because what is being measured is exclusivity, not queueing.
        outcomes = await asyncio.gather(
            *[
                sd._acquire_slot_lock(lock_key, _uuid.uuid4().hex, user)
                for _ in range(20)
            ]
        )
        acquired = [o for o in outcomes if o == "acquired"]
        assert len(acquired) == 1, (
            f"{len(acquired)} of 20 concurrent acquirers took the same account's lock on a "
            f"real Redis; it is not mutually exclusive: {outcomes}"
        )
        # Everyone else was refused, and refused as CONTENDED rather than as unavailable --
        # the server was reachable throughout.
        assert all(o == "contended" for o in outcomes if o != "acquired"), outcomes
        assert await client.get(lock_key) is not None, "the lock was not actually written"
        assert await client.ttl(lock_key) > 0, "the lock has no TTL on the real server"
    finally:
        sd.redis_manager = original
        await client.delete(lock_key)
        await client.aclose()


@real_redis_required
@pytest.mark.asyncio
async def test_the_guard_admits_exactly_the_limit_against_real_redis(slot_env):
    """End to end on a real server: concurrent stores cannot exceed the plan's connection cap.

    The account state stays in-process (it stands for ``exchange_keys``, which the Postgres
    lane covers); the LOCK is the real one. That is the division of labour the finding needs:
    the race was in the lock, not in the table.
    """
    import uuid as _uuid

    import redis.asyncio as aioredis

    redis_url = require_real_redis()

    client = aioredis.from_url(redis_url, decode_responses=True)
    user = f"q01-slot-e2e-{_uuid.uuid4().hex[:8]}"
    lock_key = sd._slot_lock_key(user, EXCHANGE_CONNECTIONS)

    class _RealCache:
        async def set(self, key, value, **kwargs):
            return await client.set(key, value, nx=kwargs.get("nx", False), ex=kwargs.get("ex"))

        async def get(self, key):
            return await client.get(key)

        async def delete(self, *keys):
            return await client.delete(*keys)

    slot_env.accounts[user] = _Account(limit=1)
    slot_env.locks = _RealCache()
    original = sd.redis_manager
    sd.redis_manager = slot_env.locks
    try:
        await client.delete(lock_key)
        results = await asyncio.gather(
            _attempt_store(slot_env, user, "binance", delay=0.01),
            _attempt_store(slot_env, user, "kraken", delay=0.01),
        )
        held = slot_env.accounts[user].venues
        assert len(held) == 1, (
            f"the account holds {held} against a limit of 1 on a real Redis: {results}"
        )
    finally:
        sd.redis_manager = original
        await client.delete(lock_key)
        await client.aclose()


# ══════════════════════════════════════════════════════════════════════════════════════════
# 7. Lock OWNERSHIP on release — found in the Workstream C closeout review
# ══════════════════════════════════════════════════════════════════════════════════════════
#
# The guard writes a per-request ownership token as the lock's VALUE. The first implementation
# then released with a bare `delete(lock_key)` and never read that token back, which is the
# classic unlock defect: a holder whose critical section outlives the TTL deletes whichever
# request legitimately owns the lock by then, and a third request can acquire while the second
# is still writing. Two concurrent writers is exactly the overshoot the guard exists to prevent.
#
# These tests pin the ownership check. See `_release_slot_lock`.


@pytest.mark.asyncio
async def test_the_guard_does_not_delete_a_successors_lock_when_its_own_expired(slot_env):
    """The defect, reproduced as a scenario: an expired holder must not free its successor.

    The holder's key is removed mid-critical-section (what the TTL would do) and a DIFFERENT
    request's token is written in its place. On exit the first holder must leave that alone.
    """
    slot_env.accounts["u"] = _Account(limit=5)
    user = {"id": "u"}
    lock_key = sd._slot_lock_key("u", EXCHANGE_CONNECTIONS)

    async with sd.exchange_connection_slot_guard(user, object(), "binance"):
        # The TTL fires and a waiting request takes the lock.
        slot_env.locks.store[lock_key] = "SUCCESSOR-TOKEN"

    assert slot_env.locks.store.get(lock_key) == "SUCCESSOR-TOKEN", (
        "the expired holder deleted a different request's lock on the way out; a third "
        "request could now enter the critical section alongside the successor"
    )


@pytest.mark.asyncio
async def test_a_normal_exit_releases_the_lock(slot_env):
    """The ownership check must not stop the ordinary case from releasing.

    A positive control for the test above: an over-careful release that never deleted anything
    would make every account's next request wait out the full TTL.
    """
    slot_env.accounts["u"] = _Account(limit=5)
    lock_key = sd._slot_lock_key("u", EXCHANGE_CONNECTIONS)

    async with sd.exchange_connection_slot_guard({"id": "u"}, object(), "binance"):
        assert slot_env.locks.store.get(lock_key), "the lock was not held inside the guard"

    assert lock_key not in slot_env.locks.store, "the owner's own lock was not released"
    # And the account is immediately usable again rather than waiting out the TTL.
    assert (await _attempt_store(slot_env, "u", "kraken"))[0] == "ADMITTED"


@pytest.mark.asyncio
async def test_release_reports_each_ownership_outcome(slot_env):
    """`_release_slot_lock`'s four answers, asserted directly.

    Each is a different operator signal — `not-owner` and `expired` mean mutual exclusion was
    already lost and the limit may have been exceeded, which is why they are distinguishable
    rather than collapsed into "released".
    """
    lock_key = sd._slot_lock_key("u", EXCHANGE_CONNECTIONS)

    # Owner releases.
    slot_env.locks.store[lock_key] = "MINE"
    assert await sd._release_slot_lock(lock_key, "MINE", "u") == "released"
    assert lock_key not in slot_env.locks.store

    # No key came back. NOTE: this is deliberately NOT called "expired" -- the facade's `get`
    # is fail-soft and answers None both for a key the TTL removed and for a cache it could
    # not reach, so the two are one observation here. Pinned as `absent` so the code cannot
    # quietly start claiming it knows which.
    assert await sd._release_slot_lock(lock_key, "MINE", "u") == "absent"

    # Held by someone else -> not deleted.
    slot_env.locks.store[lock_key] = "THEIRS"
    assert await sd._release_slot_lock(lock_key, "MINE", "u") == "not-owner"
    assert slot_env.locks.store[lock_key] == "THEIRS", "another holder's lock was deleted"

    # A cache that returns nothing (the facade's swallow-and-return-None path) still must not
    # delete anything.
    slot_env.locks.unavailable = True
    assert await sd._release_slot_lock(lock_key, "MINE", "u") == "absent"
    slot_env.locks.unavailable = False
    assert slot_env.locks.store[lock_key] == "THEIRS", "a degraded read deleted a live lock"

    # A cache that RAISES is the one case that is distinguishable, and is also not deleted.
    slot_env.locks.raise_on_get = True
    assert await sd._release_slot_lock(lock_key, "MINE", "u") == "unverified"
    slot_env.locks.raise_on_get = False
    assert slot_env.locks.store[lock_key] == "THEIRS"


@pytest.mark.asyncio
async def test_a_bare_delete_on_release_fails_this_suite(slot_env):
    """Negative control: restore the bare delete and confirm these tests catch it.

    If this assertion did not hold, the ownership tests above would be passing for some other
    reason and the defect could be reintroduced unnoticed.
    """
    slot_env.accounts["u"] = _Account(limit=5)
    lock_key = sd._slot_lock_key("u", EXCHANGE_CONNECTIONS)

    original = sd._release_slot_lock

    async def _bare_delete(key, token, user_id):
        await slot_env.locks.delete(key)  # the pre-fix behaviour
        return "released"

    sd._release_slot_lock = _bare_delete
    try:
        async with sd.exchange_connection_slot_guard({"id": "u"}, object(), "binance"):
            slot_env.locks.store[lock_key] = "SUCCESSOR-TOKEN"
    finally:
        sd._release_slot_lock = original

    assert lock_key not in slot_env.locks.store, (
        "restoring the bare delete did NOT reproduce the defect, so these tests are not "
        "measuring the ownership check"
    )
