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
from tests.real_redis_gate import (real_redis_required, redis_url as _gate_redis_url,
                                   require_real_redis)

#: Kept as a module-level constant because the fixtures below hand it to the backend cache
#: manager, but now sourced from the shared gate rather than read from the environment a
#: second time -- one opt-in name, resolved in one place.
REDIS_URL = _gate_redis_url()


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

    If the manager still hands back the mock, the test skips rather than passing: a green
    result from the in-memory dict would be precisely the false assurance this file was
    written to remove.
    """
    from backend_app.core.cache import redis_manager
    from backend_app.core.cache.redis_manager import MockRedisClient

    os.environ["REDIS_URL"] = REDIS_URL
    redis_manager._bypass_dev_mode(True)
    cache = await redis_manager._get_active_cache()
    if cache is None or isinstance(cache, MockRedisClient):
        pytest.skip(
            f"redis_manager still resolves to {type(cache).__name__}; cannot exercise "
            f"the lock against the real server, and the in-memory dict would prove "
            f"nothing (see BUG-027)."
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


@pytest.fixture
def redis_client():
    import redis as _redis

    client = _redis.Redis.from_url(REDIS_URL, decode_responses=True)
    created: list[str] = []
    yield client, created
    for key in created:
        try:
            client.delete(key)
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
