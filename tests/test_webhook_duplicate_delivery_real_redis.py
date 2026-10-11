"""Duplicate webhook delivery, executed against a real Redis.

WHY THIS FILE EXISTS. `tests/test_billing_e2e.py` checks this behaviour by counting a
string in the source:

    assert source.count("await redis_manager.delete(idempotency_key)") >= 2

That establishes two `delete` calls are written down. It does not establish that a
redelivered webhook is ignored, that the first delivery is not ignored, that the lock is
released when processing raises, or that any of it works against a real Redis. Payment
webhooks are the one place where processing the same event twice moves money twice, so
"the line is present in the file" is not a sufficient standard. Nothing in the suite had
ever executed the handler.

WHAT THIS NEEDS, AND WHAT IT DELIBERATELY DOES NOT. No Razorpay account and no payment
credentials. Razorpay's webhook authentication is HMAC-SHA256 over the raw body with a
shared secret, so a correctly signed request can be produced locally by setting the
secret and signing the payload -- which is what the handler verifies and all it verifies.
That makes the dedup control fully testable here.

It does NOT make the end-to-end payment path testable. Granting an entitlement calls
Supabase, and a real sandbox settlement needs provider credentials this environment does
not have. So the event used below is one the handler parses, locks, and finishes without
granting anything. The control under test is the idempotency lock; the settlement path
remains unverified and is reported as such rather than implied by this file passing.

The handler is invoked directly rather than through TestClient: it takes `Request` and a
header, and calling it as a function avoids standing up the whole application's
dependency graph to exercise four lines of locking. The Request is a stand-in; Redis is
real.

Gated by ``tests/real_redis_gate.py`` on ``AERORA_TEST_REDIS_URL``: unset SKIPS, so the
default suite is unaffected; configured-but-unreachable FAILS rather than skipping.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import uuid
from urllib.parse import urlsplit

import pytest
import pytest_asyncio

WEBHOOK_SECRET = "test-webhook-secret-not-a-real-credential"

# A SECOND VARIABLE NAME, FOR THE SAME REASON `AERORA_TEST_DATABASE_URL` EXISTS.
#
# `tests/conftest.py` line 13 sets `os.environ["REDIS_URL"] = ""` unconditionally and
# deliberately, so the 12,000-test suite runs on the in-memory `MockRedisClient` and is
# not sensitive to whatever Redis happens to be on the developer's machine. That is the
# right default and is not changed here.
#
# It does mean no Redis-backed control in this repository has ever executed against a
# real server, which matters because `MockRedisClient` is a plain dict on a module-global
# instance -- per process, so `SET NX` always succeeds. That is BUG-027: the distributed
# locks behind `master_executor`'s execution lock and `claim_submission`'s claim silently
# became per-pod no-ops while still reporting success.
#
# So this file opts in under its own name, exactly as the PostgreSQL files do. Unset,
# everything here skips and the default lane is untouched.
#
# THE GATE IS SHARED NOW (Phase 11B-A3).
#
# This file used to own its own predicate:
#
#     def _redis_reachable() -> bool: ...          # '' or no PING -> False
#     pytestmark = [pytest.mark.skipif(not _redis_reachable(), reason="... unset or
#                   unreachable ..."), pytest.mark.asyncio]
#
# It read the right VARIABLE, so it was never a production-safety problem. The defect was
# that one predicate answered two different questions and gave both the same outcome:
# "nobody asked for a real Redis" and "the Redis you configured is dead" both produced a
# SKIP. The two quota files adopted `tests/real_redis_gate.py` and therefore FAIL on the
# second case, so a dead CI Redis would have failed those three tests while showing these
# six as green skips -- which is exactly the false-assurance mode D5' exists to remove, and
# it is the acceptance rule that a required Redis assertion may not be satisfied by a skip.
#
# `real_redis_required` skips ONLY for the legitimate opt-out (variable unset). The autouse
# fixture below supplies the other half, because a pytest marker cannot express a failure.
# Business logic, signature validation, idempotency behaviour, lock semantics and the
# production Redis configuration are untouched by this change.
#
# `_LOOPBACK_HOSTS` and `ALLOW_REMOTE_VAR` are imported alongside the public surface
# because `_app_cache_url()` below has to apply the SAME production-safety rule to a
# second URL, and duplicating the host list here would let the two copies drift. The gate
# remains the single place that decides what "safe to connect to" means.
from tests.real_redis_gate import (ALLOW_REMOTE_VAR, _LOOPBACK_HOSTS,
                                   real_redis_required, redis_url as _gate_redis_url,
                                   require_real_redis)

#: Kept as a module-level constant because the fixtures below hand it to the backend cache
#: manager, but now sourced from the shared gate rather than read from the environment a
#: second time -- one opt-in name, resolved in one place.
#:
#: THIS IS THE OPT-IN, NOT THE KEYSPACE. See `_app_cache_url()`.
REDIS_URL = _gate_redis_url()


def _app_cache_url() -> str:
    """The URL the APPLICATION's cache is on -- not the one this file asked for. F2.

    ═══════════════════════════════════════════════════════════════════════════════
    WHY THIS EXISTS (Phase 11B-A9 -> A12)
    ═══════════════════════════════════════════════════════════════════════════════
    Two of the six tests below failed in CI run 38044138718 because the synchronous test
    client and the application handler were writing to DIFFERENT REDIS DATABASES on the
    same server. Not a dedup defect, not a mock, not a dead server -- a keyspace split:

        tests/test_webhook_duplicate_delivery_real_redis.py::
            test_the_lock_is_recorded_in_redis_under_the_documented_key   FAILED
              -> AssertionError: no idempotency key at webhook:razorpay:evt_... after a delivery
            test_an_in_flight_delivery_blocks_a_concurrent_one            FAILED
              -> AssertionError: ... was still in flight was processed anyway: {'status': 'success'}

    `backend_app/backend/redis_manager.py` pins the cache to its own database and
    OVERRIDES whatever database a URL named::

        37: REDIS_DB_CACHE = 0
        43: REDIS_URL = os.getenv("REDIS_URL", "").strip()      # captured at IMPORT
        69:     query_params['db'] = str(db)                    # overrides the URL's own db
        86:     return f"redis://{auth}{REDIS_HOST}:{REDIS_PORT}/{db}"   # or builds it fresh
       141:     cache = await aioredis.from_url(get_redis_url(REDIS_DB_CACHE), ...)

    In CI, `REDIS_URL: redis://localhost:6379` and `AERORA_TEST_REDIS_URL:
    redis://localhost:6379/15`. Both branches of `get_redis_url` therefore resolve to
    DB 0 -- the non-empty branch yields `redis://localhost:6379?db=0`, and redis-py
    resolves the query parameter in preference to any path, measured on redis 5.0.8::

        parse_url('redis://localhost:6379/15?db=0') -> {'db': 0, 'host': 'localhost', 'port': 6379}

    while this file's client went to DB 15. So the handler's lock was written, correctly,
    somewhere the test never looked.

    Note also that line 43 is a MODULE-LEVEL CONSTANT read once at import. The
    `monkeypatch.setenv("REDIS_URL", ...)` in `billing_env` and the `os.environ`
    assignment in `real_redis_cache` cannot retroactively change it, so pointing the
    application at DB 15 from here is not available as an option.

    WHY DERIVE RATHER THAN HARD-CODE DB 0. `get_redis_url` is a pure function of that
    module's constants, so calling it returns the exact string `initialize()` passed to
    `aioredis.from_url` -- by construction, not by coincidence. A hard-coded `/0` would
    agree today and drift the moment `REDIS_DB_CACHE`, the override, or the host/port
    fallback changes, and it would reintroduce this bug silently.

    WHY THE IMPORT IS LAZY. It must happen AFTER `real_redis_cache` has resolved the
    manager. By then `backend_app.backend.redis_manager` is imported and its constants
    are frozen to whatever the application itself observed, so this function cannot
    report a URL the application never used.

    WHAT THIS DOES NOT DO. It does not set, export, or mutate `REDIS_URL`, `REDIS_HOST`
    or `REDIS_PORT`, and it does not touch application code. The opt-in and liveness
    decision stays entirely on `AERORA_TEST_REDIS_URL` via the shared gate. The only
    thing that moves is where THIS FILE'S OWN client looks.

    TRADE-OFF, STATED. The test client no longer sits on the dedicated database index
    named by `AERORA_TEST_REDIS_URL`; it sits on the application's cache database. That
    is strictly less keyspace isolation than `/15` promised -- but `/15` was never where
    the application wrote, so the isolation was illusory and bought nothing. Every key
    this file creates is uuid-suffixed and registered for teardown. Restoring real
    isolation requires `get_redis_url` to honour an explicitly configured database, which
    is an application change, owned elsewhere, and out of scope here.
    """
    from backend_app.backend.redis_manager import REDIS_DB_CACHE, get_redis_url

    url = get_redis_url(REDIS_DB_CACHE)

    # The same refusal the gate applies to `AERORA_TEST_REDIS_URL`, applied to the URL we
    # derived. Without it, a deployment-shaped `REDIS_HOST` would quietly point this
    # file's client -- which WRITES and DELETES keys -- at a shared or managed instance.
    # FAIL, not skip: a required real-Redis assertion may not be satisfied by a skip, and
    # the endpoint is deliberately not echoed (CI logs are world-readable).
    host = (urlsplit(url).hostname or "").lower()
    if host not in _LOOPBACK_HOSTS and (os.environ.get(ALLOW_REMOTE_VAR) or "").strip() != "1":
        pytest.fail(
            f"the application's effective cache URL (REDIS_URL / REDIS_HOST / REDIS_PORT, "
            f"database {REDIS_DB_CACHE}) names a non-loopback host and "
            f"{ALLOW_REMOTE_VAR}=1 was not set. Refusing to connect: this file writes and "
            f"deletes keys, so it must not be able to reach a shared or managed instance "
            f"by accident. (The host is deliberately not echoed.)"
        )
    return url


#: Sentinel keys written by the keyspace probe below. Prefixed and uuid-suffixed so they
#: cannot collide with an application key, and given a short TTL so an interrupted run
#: cannot leave litter behind even if teardown never executes.
_PROBE_PREFIX = "aerora:test:keyspace-probe"
_PROBE_TTL_SECONDS = 30


async def _prove_one_keyspace(manager, client) -> None:
    """Prove the test client and the application cache manager see the SAME keys. F3.

    This is the assertion whose absence let Phase 11B-A6 ship a green-looking run in which
    two tests were measuring an empty database. It is deliberately BIDIRECTIONAL, because
    the two failures were in opposite directions and one direction alone would have caught
    only one of them:

        app writes -> test reads    `test_the_lock_is_recorded_in_redis_under_the_documented_key`
        test writes -> app reads    `test_an_in_flight_delivery_blocks_a_concurrent_one`

    It runs per test rather than once per session on purpose: `_redis_manager`,
    `_dev_mode_bypass` and `_test_mode` are injection seams on a session-wide singleton
    (see `_REDIS_SINGLETON_SEAMS` in `tests/conftest.py`), so agreement proven at session
    start is not agreement now. Four commands per test is not worth optimising.

    A FAILED WRITE IS REPORTED AS A FAILED WRITE, not as a mismatch. The manager's proxies
    swallow every exception and return `None`/`False`
    (`backend_app/core/cache/redis_manager.py`, `get`/`set`/`delete`), so a cache fault and
    an empty keyspace are indistinguishable from the return value alone. Checking the write
    result first keeps the two apart instead of blaming the wrong thing.

    Everything here FAILS rather than skips, and the mock is never accepted as real: by the
    time this runs, `real_redis_required` has established the opt-in is set,
    `_real_redis_is_usable` has established the endpoint answers PING, and
    `real_redis_cache` has established the manager handed back something that is not
    `MockRedisClient` and not `None`.
    """
    import redis as _redis

    from backend_app.backend.redis_manager import REDIS_DB_CACHE

    nonce = uuid.uuid4().hex
    key_app_wrote = f"{_PROBE_PREFIX}:app-wrote:{nonce}"
    key_test_wrote = f"{_PROBE_PREFIX}:test-wrote:{nonce}"
    token = f"keyspace-probe-{nonce}"

    try:
        # ── direction 1: the application writes, this file must be able to read it ──
        wrote = await manager.set(key_app_wrote, token, ex=_PROBE_TTL_SECONDS)
        if not wrote:
            pytest.fail(
                f"the application cache manager REFUSED a write it was handed a real "
                f"cache for (`set` returned {wrote!r}). Its proxies return False on any "
                f"exception and log a warning, so the reason is in the captured log, not "
                f"in this value. This is not a keyspace mismatch -- the write never "
                f"landed anywhere."
            )

        seen_by_test = client.get(key_app_wrote)
        if seen_by_test != token:
            pytest.fail(
                f"KEYSPACE SPLIT: the application cache manager wrote a sentinel and this "
                f"file's client cannot see it (read {seen_by_test!r}, expected the "
                f"sentinel). Both clients answered, so there is a Redis on both ends -- "
                f"they are not looking at the same one. This file derives its URL from "
                f"`get_redis_url(REDIS_DB_CACHE)` with REDIS_DB_CACHE={REDIS_DB_CACHE}, "
                f"so a mismatch means the manager is connected to something that function "
                f"does not describe: an injected `_redis_manager`, a connection opened "
                f"before REDIS_URL reached its current value, or a second server. "
                f"`test_the_lock_is_recorded_in_redis_under_the_documented_key` would "
                f"fail next with 'no idempotency key ... after a delivery', which is the "
                f"CI failure this probe exists to name at its cause."
            )

        # ── direction 2: this file writes, the application must be able to read it ──
        client.set(key_test_wrote, token, ex=_PROBE_TTL_SECONDS)
        seen_by_app = await manager.get(key_test_wrote)
        if seen_by_app != token:
            pytest.fail(
                f"KEYSPACE SPLIT (reverse direction): this file wrote a sentinel and the "
                f"application cache manager cannot see it (read {seen_by_app!r}). Its "
                f"`get` returns None both for 'absent' and for 'the call raised', so "
                f"check the captured log before concluding which. "
                f"`test_an_in_flight_delivery_blocks_a_concurrent_one` depends on exactly "
                f"this direction -- it installs the `processing` marker itself -- and "
                f"would fail next with 'processed anyway'."
            )
    except (_redis.exceptions.RedisError, OSError) as exc:
        # The gate PINGed `AERORA_TEST_REDIS_URL`; this client is on the URL the
        # APPLICATION uses, which may be a different endpoint. A failure here means that
        # second endpoint is unusable. Fail closed, and do not echo it.
        pytest.fail(
            f"the shared gate reached the endpoint named by its own opt-in variable, but "
            f"this file's client could not use the application's effective cache URL "
            f"(database {REDIS_DB_CACHE}): {type(exc).__name__}. The two are allowed to "
            f"differ, so a reachable opt-in endpoint does not imply a reachable "
            f"application endpoint. (Neither is echoed.)"
        )
    finally:
        # Through BOTH paths, so the sentinels are gone whichever side can still reach
        # them, and regardless of which assertion above fired. `pytest.fail` raises a
        # BaseException subclass, so it passes straight through these handlers.
        try:
            await manager.delete(key_app_wrote, key_test_wrote)
        except Exception:  # noqa: BLE001
            pass
        try:
            client.delete(key_app_wrote, key_test_wrote)
        except Exception:  # noqa: BLE001
            pass


# `pytest.ini` sets asyncio_mode = strict, so the async tests below need the marker
# explicitly -- without it they are collected and never awaited, which pytest reports as
# a pass. Applied at module level alongside the gate so neither can be forgotten on a
# new test in this file.
pytestmark = [real_redis_required, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _real_redis_is_usable():
    """Fail -- never skip -- when the opt-in is set but the server is not usable.

    AUTOUSE AND FIRST, deliberately, for two reasons:

    * it covers every test in this file, including ones added later, which a per-test call
      at the top of six bodies does not;
    * it runs BEFORE `billing_env` / `real_redis_cache` / `redis_client`, so a dead
      endpoint is reported with the gate's explicit reason instead of surfacing as a
      connection error from inside whichever fixture happened to dial first.

    `require_real_redis()` skips only for the unset case, which `real_redis_required` has
    already handled by the time a fixture runs, and calls `pytest.fail` for a configured
    endpoint that failed PING or named a non-loopback host without
    `AERORA_TEST_REDIS_ALLOW_REMOTE=1`.
    """
    require_real_redis()


class _FakeRequest:
    """The three things the handler touches on its Request: body, headers, client."""

    def __init__(self, body: bytes, headers: dict[str, str]):
        self._body = body
        self.headers = headers
        self.client = type("C", (), {"host": "127.0.0.1"})()

    async def body(self) -> bytes:
        return self._body


def _signed(payload: dict) -> tuple[bytes, str]:
    raw = json.dumps(payload).encode()
    sig = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, sig


@pytest.fixture
def billing_env(monkeypatch):
    """The environment the handler's own guards require, and nothing more.

    IP validation is disabled rather than spoofed: the allowlist is a separate control
    with its own tests, and `_validate_webhook_ip` reads this flag outside production by
    design. ENV is forced to a non-production value so that remains true -- if ENV were
    `production` the flag is ignored and the check is unconditional, which is the correct
    behaviour and would simply make this test unable to reach the code under test.
    """
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ENABLE_WEBHOOK_IP_VALIDATION", "false")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_key_id_placeholder")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "rzp_test_key_secret_placeholder")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.delenv("DISABLE_REDIS", raising=False)


# `pytest_asyncio.fixture`, not `pytest.fixture`: under asyncio_mode = strict the plain
# decorator hands the test a coroutine object instead of awaiting it, and the failure
# surfaces as a confusing AttributeError inside the test rather than at the fixture.
@pytest_asyncio.fixture
async def real_redis_cache(billing_env):
    """Point the shared manager at the real server for the duration of one test.

    `_get_active_cache()` returns the in-memory mock whenever `DEV_MODE` is set and
    `_dev_mode_bypass` is false, and the suite sets `DEV_MODE`. `_bypass_dev_mode(True)`
    is the manager's own seam for this, so it is used rather than assigning the private
    attribute directly. `REDIS_URL` is restored here too, because conftest blanked it at
    import and the backend manager reads it when it connects.

    `conftest.py`'s autouse `_restore_shared_redis_singleton` fixture snapshots and
    restores every seam afterwards -- `_dev_mode_bypass` is in its
    `_REDIS_SINGLETON_SEAMS` list -- so flipping it here cannot leak into another test,
    which is the whole reason that fixture exists.

    If the manager still hands back the mock, the test FAILS rather than passing or
    skipping (Phase 11B-A5, see the guard below): a green result from the in-memory dict
    would be precisely the false assurance this file was written to remove.

    Note that this fixture establishes only that the manager returned a REAL cache. It
    does not establish that the real cache is the one this file's own client is looking
    at -- that is a separate question, it was answered "no" in CI run 38044138718, and
    `redis_client` / `_prove_one_keyspace` now answer it explicitly.
    """
    from backend_app.core.cache import redis_manager
    from backend_app.core.cache.redis_manager import MockRedisClient

    os.environ["REDIS_URL"] = REDIS_URL
    redis_manager._bypass_dev_mode(True)
    cache = await redis_manager._get_active_cache()
    if cache is None or isinstance(cache, MockRedisClient):
        # FAIL, NOT SKIP (Phase 11B-A5).
        #
        # This was `pytest.skip(...)`, and by the time this line is reached a skip is the
        # wrong answer. Two things are already established:
        #
        #   * `real_redis_required` (pytestmark) has confirmed AERORA_TEST_REDIS_URL IS
        #     SET -- otherwise collection would have skipped and this fixture would never
        #     run;
        #   * `_real_redis_is_usable` (autouse, above) has confirmed the endpoint ANSWERS
        #     PING, from a permitted host.
        #
        # So reaching here means: a real Redis was asked for, the shared gate reached it,
        # and the APPLICATION cache manager still did not hand back a real cache. That is
        # a wiring or configuration fault, not an absent server -- and the two quota files
        # sharing this gate would FAIL on the same broken service, so reporting it as a
        # skip here is the same false-assurance asymmetry D5' exists to remove. A required
        # real-Redis assertion may not be satisfied by a skip.
        #
        # `_get_active_cache()` reaches this state via one of two branches
        # (backend_app/core/cache/redis_manager.py:727-745), both measured in
        # staging/probe_p11b4_cache_fallback.py:
        #   * no `.cache` from `_ensure_manager()` and `_mock_cache_is_acceptable()` true
        #     -> MockRedisClient  (what ENV=test produces)
        #   * the same with the predicate false -> None
        #
        # The DETECTION below is unchanged and was already correct; only the disposition
        # moves. No application code is touched: the manager's fail-soft behaviour is
        # deliberate and is owned elsewhere.
        pytest.fail(
            f"the shared real-Redis gate PASSED its PING, but redis_manager resolved to "
            f"{type(cache).__name__} instead of a real cache. AERORA_TEST_REDIS_URL is "
            f"set and the endpoint answered, so this is a cache-manager wiring fault, not "
            f"a missing server -- it FAILS rather than skipping, because the in-memory "
            f"dict would prove nothing (see BUG-027) and a skip here would hide a broken "
            f"service behind a green run. Check DEV_MODE / ENV, whether "
            f"`_bypass_dev_mode(True)` took effect, and whether the manager could open "
            f"its own connection to the same endpoint the gate just reached."
        )
    return redis_manager


@pytest_asyncio.fixture
async def handler(real_redis_cache, monkeypatch):
    """The webhook handler, with the credential gate stepped around deliberately.

    `_validate_keys` runs before the idempotency block and accepts LIVE key prefixes
    only, in every environment, with no test-credential bypass -- its own docstring says
    so and gives the reason: a `rzp_test_` key collects no money, so honouring one on a
    path that grants a paid entitlement would hand out plans for free. That rule is
    correct and is NOT being relaxed; `test_the_credential_gate_refuses_test_keys` below
    asserts it still refuses, against this same handler, before anything else runs.

    But it also means the dedup control behind it cannot be reached with sandbox
    credentials, and reaching it with live ones is not acceptable in a verification
    environment. Those are the only two options the handler offers, so the gate is
    neutralised here for the duration of these tests and the thing under test -- the
    two-phase Redis lock -- is exercised on its own.

    What that leaves unverified is stated rather than implied: no end-to-end settlement,
    no provider interaction, no entitlement grant. Only the redelivery behaviour.
    """
    import backend_app.routers.billing as billing

    monkeypatch.setattr(billing, "_validate_keys", lambda provider: "rzp_live_stub")
    return billing.razorpay_webhook


async def test_the_credential_gate_refuses_test_keys(billing_env):
    """The gate stepped around above is real, and is checked before stepping around it.

    If this ever stops refusing, the fixture above is hiding a weakened control rather
    than isolating a working one, and these tests would be worth less than nothing.
    """
    from fastapi import HTTPException

    from backend_app.routers.billing import _validate_keys

    with pytest.raises(HTTPException) as excinfo:
        _validate_keys("razorpay")
    assert excinfo.value.status_code == 500
    assert "TEST key id" in str(excinfo.value.detail), excinfo.value.detail


# `pytest_asyncio.fixture` and a dependency on `real_redis_cache`, both required by F2/F3:
# the URL must be derived AFTER the application manager has resolved (see
# `_app_cache_url`), and the probe has to await the manager's async proxies.
@pytest_asyncio.fixture
async def redis_client(real_redis_cache):
    """A synchronous client on the SAME keyspace as the application cache, proven.

    Was `_redis.Redis.from_url(REDIS_URL, ...)` -- the opt-in URL, whose database index
    the application overrides -- which is the Phase 11B-A9 keyspace split. It now resolves
    through `_app_cache_url()`, and `_prove_one_keyspace()` demonstrates the agreement
    before any test body runs rather than leaving it assumed.

    Teardown is unchanged in intent and now actually effective: because this client is
    finally on the database the handler writes to, deleting the registered keys removes
    the handler's locks instead of deleting nothing from an unrelated database.
    """
    import redis as _redis

    client = _redis.Redis.from_url(_app_cache_url(), decode_responses=True)
    created: list[str] = []
    try:
        await _prove_one_keyspace(real_redis_cache, client)
        yield client, created
    finally:
        for key in created:
            try:
                client.delete(key)
            except Exception:  # noqa: BLE001
                pass
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def _event(event_id: str) -> dict:
    """An event the handler locks and completes without granting an entitlement.

    `payment.authorized` is parsed and logged; only `payment.captured` reaches
    `_process_razorpay_entitlement`. That keeps this test on the idempotency control and
    off the Supabase-dependent settlement path.
    """
    return {
        "event_id": event_id,
        "event": "payment.authorized",
        "payload": {"payment": {"entity": {"id": f"pay_{event_id}", "amount": 49900}}},
    }


async def _deliver(handler, payload: dict):
    raw, sig = _signed(payload)
    from fastapi import BackgroundTasks

    request = _FakeRequest(raw, {"x-razorpay-signature": sig})
    return await handler(BackgroundTasks(), request, sig)


# ── the mandatory test ───────────────────────────────────────────────────────


async def test_a_redelivered_webhook_is_ignored(handler, redis_client):
    """The same event_id twice: processed once, ignored the second time."""
    client, created = redis_client
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    created.append(f"webhook:razorpay:{event_id}")

    first = await _deliver(handler, _event(event_id))
    second = await _deliver(handler, _event(event_id))

    assert first.get("status") != "duplicate", (
        f"the FIRST delivery was treated as a duplicate ({first}); a webhook that is "
        f"never processed is a lost settlement, which is the opposite failure and just "
        f"as expensive"
    )
    assert second.get("status") == "duplicate", (
        f"the SECOND delivery of event {event_id} was NOT deduplicated: {second}. A "
        f"redelivery would be processed again."
    )


async def test_the_lock_is_recorded_in_redis_under_the_documented_key(handler, redis_client):
    """The key is the one the code's comments name, and it carries a TTL.

    A lock with no expiry that is never cleared would dedup for ever, including after a
    legitimate later event reuses an id; a lock with no key at all would dedup nothing.
    """
    client, created = redis_client
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    key = f"webhook:razorpay:{event_id}"
    created.append(key)

    await _deliver(handler, _event(event_id))

    value = client.get(key)
    assert value is not None, f"no idempotency key at {key} after a delivery"
    assert value in ("processing", "completed", "1"), f"unexpected lock value {value!r}"

    ttl = client.ttl(key)
    assert ttl > 0, (
        f"idempotency key {key} has no TTL (ttl={ttl}); it would dedup for ever"
    )


async def test_a_different_event_id_is_not_deduplicated(handler, redis_client):
    """Dedup must key on the event, not be a blanket second-request block.

    A lock accidentally written under a constant key would pass the redelivery test above
    and silently discard every subsequent distinct payment.
    """
    client, created = redis_client
    first_id = f"evt_{uuid.uuid4().hex[:12]}"
    second_id = f"evt_{uuid.uuid4().hex[:12]}"
    created.extend([f"webhook:razorpay:{first_id}", f"webhook:razorpay:{second_id}"])

    first = await _deliver(handler, _event(first_id))
    second = await _deliver(handler, _event(second_id))

    assert first.get("status") != "duplicate"
    assert second.get("status") != "duplicate", (
        f"a DIFFERENT event ({second_id}) was deduplicated against {first_id}: {second}. "
        f"Every payment after the first would be discarded."
    )


async def test_an_in_flight_delivery_blocks_a_concurrent_one(handler, redis_client):
    """The `processing` half of the two-phase lock.

    A redelivery that arrives while the first is still being processed -- which is the
    realistic race, since providers retry on timeout -- must be refused on the strength
    of the `processing` marker alone, before any `completed` value exists. Simulated by
    installing the marker the way the handler does and then delivering.
    """
    client, created = redis_client
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    key = f"webhook:razorpay:{event_id}"
    created.append(key)

    client.set(key, "processing", nx=True, ex=60)
    result = await _deliver(handler, _event(event_id))

    assert result.get("status") == "duplicate", (
        f"a delivery arriving while {event_id} was still in flight was processed "
        f"anyway: {result}"
    )


async def test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind(handler, redis_client):
    """Refusal must not poison the key, or a forged request becomes a denial of
    settlement for the real event that follows it."""
    from fastapi import BackgroundTasks, HTTPException

    client, created = redis_client
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    key = f"webhook:razorpay:{event_id}"
    created.append(key)

    raw = json.dumps(_event(event_id)).encode()
    request = _FakeRequest(raw, {"x-razorpay-signature": "deadbeef"})

    with pytest.raises(HTTPException) as excinfo:
        await handler(BackgroundTasks(), request, "deadbeef")
    assert excinfo.value.status_code in (400, 403), excinfo.value.status_code

    assert client.get(key) is None, (
        f"a rejected forgery left an idempotency lock at {key}; the genuine delivery of "
        f"{event_id} would then be discarded as a duplicate"
    )
