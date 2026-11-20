"""Kill-switch RECOVERY cannot report a lift that did not reach the shared store.

THE DEFECT
    `core/tenant_halt.set_halt` documents that "the caller is told via the return value",
    and `POST /api/risk/kill-switch` (activate) binds it and reports it as `persisted`.
    `POST /api/risk/kill-switch/recover` called `await set_halt(uid, False)` and THREW THE
    ANSWER AWAY, then unconditionally returned:

        {"status": "active", "kill_switch_active": False,
         "message": "Kill switch recovered. Trading is active."}

    When the shared write fails, the Redis key `tenant_halt:<id>` is still set, so
    `is_halted()` -- the authoritative check on the execution path -- keeps refusing every
    order on every worker. The direction of the failure was SAFE. The REPORT was false: the
    user was told trading had resumed, every order they then placed was rejected, and the
    one surface that could have explained why insisted everything was fine.

    There was a second, quieter half. `set_halt` wrote the in-process mirror FIRST and left
    it cleared even when the shared write failed, so this worker's mirror said "not halted"
    while the shared store said "halted". Dashboards read the mirror (`mirror_is_halted`);
    execution reads the store. The two surfaces disagreed about a safety control.

    And `set_halt` returned False for two situations needing OPPOSITE responses: "no shared
    store is configured" (local dev -- the mirror is the whole state, so the write is
    COMPLETE) and "the write failed" (the halt is unchanged there). That is the same
    conflation `core/global_safety.py` records as KILL-01, where `_redis_get` returned None
    for both "key not set" and "Redis unreachable" and a kill switch consequently reported
    itself inactive during an outage.

WHAT THIS FILE PROVES
    1. A failed shared write makes recovery REFUSE with a coded 503 instead of claiming
       success, and the halt stays engaged.
    2. The in-process mirror is RESTORED on that failure, so dashboards and execution agree.
    3. `NO_SHARED_STORE` is not treated as a failure: local dev recovers normally.
    4. A successful write recovers and says so.
    5. Engaging a halt still takes effect locally when the shared write fails -- the
       fail-safe ordering for the ENGAGE direction is deliberately NOT symmetric.
"""
from __future__ import annotations

import pytest

from backend_app.core import tenant_halt
from backend_app.core.tenant_halt import (
    HaltWriteOutcome,
    is_halted,
    mirror_is_halted,
    set_halt,
    set_halt_outcome,
)

TENANT = "usr_kill_switch_probe"


class _Cache:
    """A minimal stand-in for the shared cache, with a switchable failure mode."""

    def __init__(self, *, fail: bool = False):
        self.store: dict = {}
        self.fail = fail

    async def set(self, key, value):
        if self.fail:
            raise ConnectionError("shared store unreachable")
        self.store[key] = value

    async def delete(self, key):
        if self.fail:
            raise ConnectionError("shared store unreachable")
        self.store.pop(key, None)

    async def get(self, key):
        if self.fail:
            raise ConnectionError("shared store unreachable")
        return self.store.get(key)


class _WS:
    async def broadcast_user(self, *_a, **_k):
        return None


@pytest.fixture(autouse=True)
def _clean_mirror():
    tenant_halt._mirror.pop(TENANT, None)
    yield
    tenant_halt._mirror.pop(TENANT, None)


@pytest.fixture
def cache(monkeypatch):
    """Install a stand-in shared cache and hand it back so a test can break it."""
    c = _Cache()

    async def _client():
        return c

    monkeypatch.setattr(tenant_halt, "_client", _client)
    return c


# ── 1. the three outcomes are distinguishable ──────────────────────────────────


@pytest.mark.asyncio
async def test_a_successful_write_is_reported_as_persisted(cache):
    assert await set_halt_outcome(TENANT, True) is HaltWriteOutcome.PERSISTED
    assert await is_halted(TENANT) is True

    assert await set_halt_outcome(TENANT, False) is HaltWriteOutcome.PERSISTED
    assert await is_halted(TENANT) is False


@pytest.mark.asyncio
async def test_no_shared_store_is_not_a_failure(monkeypatch):
    """Local dev: the mirror IS the state, so the write is complete.

    This is the half of the old bare-bool return that must NOT be treated as an error. A
    recovery route that refused here would make the kill switch unrecoverable on every
    developer machine.
    """
    async def _no_client():
        return None

    monkeypatch.setattr(tenant_halt, "_client", _no_client)

    assert await set_halt_outcome(TENANT, True) is HaltWriteOutcome.NO_SHARED_STORE
    assert mirror_is_halted(TENANT) is True

    assert await set_halt_outcome(TENANT, False) is HaltWriteOutcome.NO_SHARED_STORE
    assert mirror_is_halted(TENANT) is False


@pytest.mark.asyncio
async def test_a_failed_write_says_so_rather_than_looking_like_an_absent_store(cache):
    cache.fail = True
    assert await set_halt_outcome(TENANT, False) is HaltWriteOutcome.WRITE_FAILED


# ── 2. the mirror does not drift from the shared store ─────────────────────────


@pytest.mark.asyncio
async def test_a_failed_clear_restores_the_mirror_so_the_surfaces_agree(cache):
    """The quieter half of the defect.

    Clearing optimistically left the mirror saying "not halted" while the shared store said
    "halted": dashboards (mirror) and execution (store) then disagreed about whether
    trading was stopped.
    """
    assert await set_halt_outcome(TENANT, True) is HaltWriteOutcome.PERSISTED
    assert mirror_is_halted(TENANT) is True

    cache.fail = True
    assert await set_halt_outcome(TENANT, False) is HaltWriteOutcome.WRITE_FAILED

    assert mirror_is_halted(TENANT) is True, (
        "the mirror was left cleared after a failed shared write, so this worker now "
        "reports trading active while the shared store still says halted"
    )


@pytest.mark.asyncio
async def test_engaging_a_halt_still_takes_effect_locally_when_the_write_fails(cache):
    """The ENGAGE direction is deliberately NOT symmetric with the clear.

    A halt that cannot reach the shared store must still stop THIS worker immediately --
    stopping is the safe direction, and a partial stop beats no stop. The caller is told via
    the outcome so it can report that the halt is local-only.
    """
    cache.fail = True
    assert await set_halt_outcome(TENANT, True) is HaltWriteOutcome.WRITE_FAILED
    assert mirror_is_halted(TENANT) is True, (
        "a halt that could not be shared must still halt the worker that took the request"
    )


@pytest.mark.asyncio
async def test_the_bool_wrapper_still_means_reached_the_shared_store(cache):
    """`set_halt` is kept for callers that only need the yes/no."""
    assert await set_halt(TENANT, True) is True
    cache.fail = True
    assert await set_halt(TENANT, False) is False


# ── 3. the route refuses instead of claiming a lift ────────────────────────────


@pytest.mark.asyncio
async def test_the_recover_route_refuses_with_a_coded_503_when_unconfirmed(cache):
    """Driven through the real handler, not through HTTP.

    The route's dependencies are supplied directly so this asserts the handler's own
    decision rather than the auth stack's.
    """
    from fastapi import HTTPException

    from backend_app.routers.risk import _user_kill_switch_state, recover_kill_switch

    await set_halt_outcome(TENANT, True)
    _user_kill_switch_state[TENANT] = True
    cache.fail = True

    with pytest.raises(HTTPException) as raised:
        await recover_kill_switch(user={"id": TENANT}, ws_mgr=_WS())

    assert raised.value.status_code == 503
    detail = raised.value.detail
    assert detail["error"] == "KILL_SWITCH_RECOVERY_UNCONFIRMED"
    # The message has to say the account is STILL PROTECTED. A bare failure reads as "your
    # safety control is broken", which is the opposite of what happened.
    assert "remains halted" in detail["message"].lower()

    # And the local fast-path view must not have been cleared either.
    assert _user_kill_switch_state.get(TENANT) is True, (
        "the in-process kill-switch flag was cleared despite the refusal"
    )
    _user_kill_switch_state.pop(TENANT, None)


@pytest.mark.asyncio
async def test_the_recover_route_succeeds_and_reports_persisted(cache):
    from backend_app.routers.risk import _user_kill_switch_state, recover_kill_switch

    await set_halt_outcome(TENANT, True)
    _user_kill_switch_state[TENANT] = True

    result = await recover_kill_switch(user={"id": TENANT}, ws_mgr=_WS())

    assert result["kill_switch_active"] is False
    assert result["persisted"] is True
    assert _user_kill_switch_state.get(TENANT) is False
    assert await is_halted(TENANT) is False
    _user_kill_switch_state.pop(TENANT, None)
