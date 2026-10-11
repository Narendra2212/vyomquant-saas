"""TRADE-02 / KILL-01 — the global kill switch, measured rather than assumed.

WHAT THE AUDIT RECORDED, AND WHAT READING THE CODE ACTUALLY FOUND
-----------------------------------------------------------------
TRADE-02 was recorded as "the kill switch may not be enforced on the live order path".
That part turned out to be the *least* of it. The live path does consult it, inline at
`backend_app/core/execution_engine.py:720-740`, and
:func:`test_a_triggered_switch_blocks_at_the_execution_boundary` drives the real engine
with the real `GlobalKillSwitch` to prove it.

What reading `core/global_safety.py` found instead were two defects that made the
module's own documented fail-safe policy untrue, both from one root cause: `_redis_get`
returned ``None`` both when Redis answered "no such key" and when Redis could not be
reached at all.

**KILL-01 — the switch failed OPEN on a Redis outage.** Because `_redis_get` swallowed
every exception, the fail-closed ``except`` branch at the bottom of ``is_active()`` was
unreachable for a connection error. Control fell through to ``if value is None: return
False``, so an outage reported the kill switch as NOT ACTIVE and a *triggered* switch
stopped blocking the instant Redis went away. The module docstring promised fail-closed.
The code failed open. It was worse than an outage-only problem: `redis_manager` is a
``__getattr__`` proxy that raises ``AttributeError`` when the manager was never
initialised, so any worker that had not brought Redis up read "switch off" too.

**The mirror image — permanent lockout.** Once the local latch was engaged, recovery
lifted it only ``if probe_result is not None``. The resting state of a healthy system is
"key absent", i.e. ``None``. So a fully recovered Redis with no kill-switch key could
never lift the latch, contradicting the same docstring's promise that transient failures
"do NOT permanently deadlock the platform".

**TRADE-02's own residue — an asymmetric TTL.** ``activate()`` wrote the key with
``ex=86400``; ``deactivate()`` wrote the "off" value with no expiry. "Stopped" decayed
back to "trading permitted" after 24 hours unattended, while "permitted" was durable
forever. A safety control that drifts toward the unsafe state is worse than one that is
merely absent, because it looks present.

HOW THIS FILE MEASURES
----------------------
A controllable fake Redis stands in for the backend, patched over
`backend_app.backend.redis_manager.redis_manager` (which `_redis_get` imports at call
time, so the module attribute is the right seam). The fake can be made to fail, can be
recovered, honours TTLs against a virtual clock, and records every operation — so "the
key carries no expiry" is asserted from what was actually sent, not inferred.

Every "it blocks" assertion is paired with a negative control that it does not block
unconditionally, and every "it does not expire" assertion is paired with a control
proving the fake honours TTLs at all.

CLOSED IN PHASE 9 (this section used to be "what is not claimed")
-----------------------------------------------------------------
Two of the three items below were gaps recorded here as passing tests. Both are now
closed, and the tests that pinned them have been inverted rather than deleted:

* ``enforce_kill_switch()`` HAD zero call sites (KILL-03) -- it raised correctly and
  guarded nothing. It is now called in ``exchange_executor.place_order`` as the last
  statement before ``create_order``, which also closes KILL-04: the boundary used to
  assert the signed check NAME ``"kill_switch"`` rather than re-read the state, and the
  only genuine read was upstream of the idempotency claim, the capacity transaction and
  the rate limiter. :func:`test_enforce_kill_switch_raises_when_engaged` keeps the half
  that was always worth asserting; the call-site placement is asserted in
  ``tests/test_kill03_authoritative_global_halt.py``.
* ``deactivate()`` took NO authorization argument and its only trail was a Redis list
  whose ``EXPIRE`` is applied in ``activate()`` alone (KILL-02). It now requires an
  operator identity, an authorization source and explicit acknowledgement of every
  outstanding halt, all keyword-only with no defaults, and records both applied and
  REFUSED transitions to ``public.safety_control_audit``.
  :func:`test_reset_now_requires_authorization_evidence` is the inverted assertion.

STILL TRUE, AND DELIBERATE
--------------------------
* ``activate()`` does not cancel or close anything. The emergency policy is "refuse new
  orders", not "flatten the book".
  :func:`test_activation_refuses_new_orders_and_does_not_flatten` pins that as the
  intended behaviour so a reader does not assume positions are protected. One consequence
  worth stating: because the halt is enforced on the NEW-ORDER path, a position-reducing
  order is also blocked, while ``cancel_order`` is not gated and still works.
* The multi-process test runs two real OS processes against a shared file-backed store.
  It proves `GlobalKillSwitch` reads authoritative state from its backend rather than
  from process memory. It is not a test against a real Redis cluster; no Redis server was
  available in this environment.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import pathlib
import subprocess
import sys
import tempfile
import textwrap
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

import pytest

import backend_app.backend.redis_manager as redis_manager_module
from backend_app.core.global_safety import (
    ExecutionBlocked,
    GlobalKillSwitch,
    KillSwitchResetRefused,
    RedisUnavailable,
    enforce_kill_switch,
    get_global_kill_switch,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════════════
#  THE CONTROLLABLE BACKEND
# ══════════════════════════════════════════════════════════════════════════


class FakeRedisDown(Exception):
    """What the fake raises when it is made unreachable.

    Deliberately NOT a subclass of anything `global_safety` knows about: the point is
    that an arbitrary backend exception must still be translated to `RedisUnavailable`
    and must still fail closed.
    """


class FakeRedis:
    """A Redis stand-in with a virtual clock, TTL support and an operation log."""

    def __init__(self) -> None:
        self.store: Dict[str, str] = {}
        self.deadlines: Dict[str, float] = {}
        self.lists: Dict[str, List[str]] = {}
        self.now: float = 0.0
        self.fail: bool = False
        self.calls: List[tuple] = []
        #: KILL-02's hold registry. Separate from `store`/`lists` so a SET operation on a
        #: string key cannot silently succeed, the way a loosely-typed fake would allow.
        self.sets: Dict[str, set] = {}

    # -- helpers ---------------------------------------------------------

    def _tick(self) -> None:
        """Drop anything whose TTL has passed on the virtual clock."""
        for key, deadline in list(self.deadlines.items()):
            if self.now >= deadline:
                self.store.pop(key, None)
                self.deadlines.pop(key, None)

    def _guard(self, op: str, key: str) -> None:
        self.calls.append((op, key))
        if self.fail:
            raise FakeRedisDown(f"fake redis is down ({op} {key})")
        self._tick()

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self._tick()

    def ttl_calls_for(self, key: str) -> List[tuple]:
        return [c for c in self.calls if len(c) > 1 and c[1] == key]

    # -- the surface `global_safety` uses --------------------------------

    async def get(self, key: str):
        self._guard("get", key)
        return self.store.get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex=None):
        """``nx``/``ex`` added for the KILL-02 transition lock.

        `GlobalKillSwitch._acquire_transition_lock` issues
        `SET key value NX EX ttl` -- one atomic Redis operation, so two processes cannot
        both believe they hold the lock. Modelled faithfully: `nx` refuses when the key is
        already present, which is what makes
        `test_a_concurrent_reset_is_refused_rather_than_interleaved` mean anything.
        """
        self._guard("set", key)
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex:
            self.deadlines[key] = self.now + ex
        else:
            self.deadlines.pop(key, None)  # SET clears any previous expiry, as Redis does
        return True

    async def delete(self, key: str):
        self._guard("delete", key)
        self.store.pop(key, None)
        self.deadlines.pop(key, None)
        return 1

    async def setex(self, key: str, ttl: int, value: str):
        self._guard("setex", key)
        self.store[key] = value
        self.deadlines[key] = self.now + ttl
        return True

    async def lpush(self, key: str, value: str):
        self._guard("lpush", key)
        self.lists.setdefault(key, []).insert(0, value)
        return True

    async def lrange(self, key: str, start: int, end: int):
        self._guard("lrange", key)
        items = self.lists.get(key, [])
        return items[start : (None if end == -1 else end + 1)]

    async def expire(self, key: str, seconds: int):
        self._guard("expire", key)
        if key in self.store or key in self.lists:
            self.deadlines[key] = self.now + seconds
        return True

    # -- the SET surface, added for KILL-02's hold registry ---------------
    #
    # `activate()` records an independent HOLD per halting subsystem, and `deactivate()`
    # refuses unless every live hold was acknowledged -- which is what makes "a reset
    # cannot silently re-enable trading while a separate halt remains active" a checked
    # property. These three operations are what that registry is built from.
    #
    # They go through `_guard` like everything else, so `backend.fail = True` makes the
    # hold set unreadable and `deactivate()` must then fail closed rather than assume
    # there are no holds. That is the same conflation KILL-01 was.

    async def sadd(self, key: str, *values: str):
        self._guard("sadd", key)
        self.sets.setdefault(key, set()).update(values)
        return len(values)

    async def srem(self, key: str, *values: str):
        self._guard("srem", key)
        self.sets.setdefault(key, set()).difference_update(values)
        return len(values)

    async def smembers(self, key: str):
        self._guard("smembers", key)
        return set(self.sets.get(key, set()))


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Patch the fake over the module attribute `_redis_get` imports at call time."""
    fake = FakeRedis()
    monkeypatch.setattr(redis_manager_module, "redis_manager", fake)
    return fake


@pytest.fixture
def switch(backend: FakeRedis) -> GlobalKillSwitch:
    """A fresh switch, so no state leaks between tests through the module singleton."""
    return GlobalKillSwitch()


@pytest.fixture(autouse=True)
def captured_audit(monkeypatch: pytest.MonkeyPatch) -> List[dict]:
    """Capture the durable audit writes instead of performing them.

    KILL-02/KILL-03 added `public.safety_control_audit` as the system of record, written
    by `core.safety_audit.record_safety_transition`. That function never raises -- a
    failed audit write must not prevent a safety control from engaging -- so leaving it
    live here would be harmless but would attempt a real `SessionLocal()` per transition,
    making this file depend on whatever `DATABASE_URL` happens to be configured.

    Capturing instead lets the tests below assert WHAT would be recorded with no database
    at all. The genuine writer, and the table's constraints and privileges, are exercised
    against real PostgreSQL in
    `tests/test_kill03_authoritative_global_halt.py`'s `realaudit` section.
    """
    recorded: List[dict] = []

    async def _capture(**kwargs):
        recorded.append(kwargs)
        return "audit-stub-id"

    import backend_app.core.safety_audit as safety_audit

    # `global_safety` imports the symbol inside each method body, so the module attribute
    # is resolved at call time and this is the effective seam.
    monkeypatch.setattr(safety_audit, "record_safety_transition", _capture)
    return recorded


#: The authorization evidence an authorized reset must now carry. KILL-02: `deactivate()`
#: used to take none at all, so there was no such thing as a denied reset. Defined once so
#: every call site below uses the same shape.
AUTHORIZED_RESET = {
    "operator_id": "3f3d8a9c-0000-4000-8000-000000000001",
    "operator_email": "operator@example.invalid",
    "authorization_source": "fastapi:get_operator_user",
}


# ══════════════════════════════════════════════════════════════════════════
#  1. KILL-01 — A BACKEND FAILURE FAILS CLOSED
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_redis_get_raises_instead_of_returning_none_on_failure(switch, backend):
    """The root cause, asserted directly.

    Everything else in this section follows from this one behaviour. Before the fix this
    returned ``None`` and the caller could not tell it from "key not set".
    """
    backend.fail = True
    with pytest.raises(RedisUnavailable):
        await switch._redis_get(switch.KEY)


@pytest.mark.asyncio
async def test_the_pre_fix_redis_get_would_fail_open(switch, backend, monkeypatch):
    """NEGATIVE CONTROL for this whole section.

    Everything above would pass trivially if `is_active()` had some other reason to
    return True during an outage. This restores the EXACT pre-fix `_redis_get` body —
    catch everything, return ``None`` — on one instance, and shows the switch then
    reports trading as permitted while the backend is unreachable.

    So the fail-closed behaviour is attributable to this specific change and not to
    something incidental. This is the same device as
    `tests/test_money02_billing_amount_precision.py`'s `_PRE_FIX_SOURCE`.
    """

    async def _pre_fix_redis_get(key: str):
        try:
            from backend_app.backend.redis_manager import redis_manager

            return await redis_manager.get(key)
        except Exception:
            return None  # the defect, verbatim

    monkeypatch.setattr(switch, "_redis_get", _pre_fix_redis_get)
    backend.fail = True

    assert await switch.is_active() is False, (
        "the pre-fix `_redis_get` no longer produces the fail-open behaviour, so the "
        "KILL-01 regression tests above are not demonstrably testing the fix"
    )


@pytest.mark.asyncio
async def test_the_pre_fix_probe_would_never_lift_the_latch(switch, backend, monkeypatch):
    """NEGATIVE CONTROL for the permanent-lockout fix.

    With the pre-fix `_redis_get`, a recovered backend holding no key returns ``None``,
    which the old probe read as "still down". The latch never lifted. Reproduced here by
    re-creating the old condition the probe used.
    """
    backend.fail = True
    assert await switch.is_active() is True
    assert switch._local_fallback_active is True

    backend.fail = False
    switch._redis_failure_at = None

    probe_result = await switch._redis_get(switch.KEY)
    assert probe_result is None, "the key should be absent on a healthy resting system"
    # The old code's decision was `if probe_result is not None: lift`. With `None` from a
    # HEALTHY backend, that branch is never taken.
    assert (probe_result is not None) is False, (
        "the pre-fix recovery condition would have lifted the latch here, so the "
        "permanent-lockout test above is not demonstrably testing the fix"
    )
    # And with the fix in place it does lift, on the same inputs.
    assert await switch.is_active() is False
    assert switch._local_fallback_active is False


@pytest.mark.asyncio
async def test_redis_get_still_returns_none_for_a_genuinely_absent_key(switch, backend):
    """Negative control for the test above: `None` must still mean "not set".

    If the fix had made every read raise, the switch would be permanently engaged and the
    test above would pass for the wrong reason.
    """
    assert await switch._redis_get(switch.KEY) is None


@pytest.mark.asyncio
async def test_an_unreachable_backend_blocks_rather_than_permits(switch, backend):
    """KILL-01. The kill switch was never even triggered here.

    A backend outage must not be read as "trading is allowed". Before the fix this
    returned False.
    """
    backend.fail = True
    assert await switch.is_active() is True, (
        "a Redis outage reported the kill switch as INACTIVE. This is KILL-01: the switch "
        "fails OPEN, so trading continues precisely when authoritative safety state cannot "
        "be read."
    )


@pytest.mark.asyncio
async def test_a_healthy_backend_with_no_key_permits(switch, backend):
    """Negative control: the switch is not simply always active."""
    assert await switch.is_active() is False


@pytest.mark.asyncio
async def test_an_uninitialised_redis_manager_blocks(switch, monkeypatch):
    """The broader case, and the likelier one in a worker.

    `redis_manager` is a `__getattr__` proxy that raises `AttributeError` when the
    manager was never initialised — not a connection error. That is not Redis being down;
    it is Redis never having been brought up, and it used to read as "switch off".
    """

    class Uninitialised:
        def __getattr__(self, name):
            raise AttributeError(
                f"redis_manager has no attribute '{name}' and is not initialized yet"
            )

    monkeypatch.setattr(redis_manager_module, "redis_manager", Uninitialised())
    assert await switch.is_active() is True


@pytest.mark.asyncio
async def test_a_triggered_switch_blocks_while_the_backend_is_healthy(switch, backend):
    """The ordinary case, asserted so a fail-closed fix cannot hide a broken happy path."""
    await switch.activate("unit test", triggered_by="pytest")
    assert await switch.is_active() is True
    assert backend.store[switch.KEY] == "1"


# ══════════════════════════════════════════════════════════════════════════
#  2. THE LATCH LIFTS — NO PERMANENT LOCKOUT
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_latch_stays_engaged_while_the_backend_is_still_down(switch, backend):
    backend.fail = True
    assert await switch.is_active() is True
    # Past the probe interval, still down.
    switch._redis_failure_at = None  # force a probe rather than waiting 10s
    assert await switch.is_active() is True
    assert switch._local_fallback_active is True


@pytest.mark.asyncio
async def test_the_latch_lifts_when_the_backend_recovers_with_no_key_set(switch, backend):
    """The mirror image of KILL-01.

    Recovery used to be decided by ``probe_result is not None``, and a healthy system's
    resting state IS ``None``. So a recovered Redis with no kill-switch key could never
    lift the latch and the platform stayed blocked forever.
    """
    backend.fail = True
    assert await switch.is_active() is True
    assert switch._local_fallback_active is True

    backend.fail = False
    switch._redis_failure_at = None  # make the next call probe immediately

    assert await switch.is_active() is False, (
        "the local latch did not lift after the backend recovered with no kill-switch key "
        "set. That is a permanent lockout: the module docstring promises transient "
        "failures do not deadlock the platform."
    )
    assert switch._local_fallback_active is False


@pytest.mark.asyncio
async def test_the_latch_lifts_into_the_active_state_if_the_key_says_so(switch, backend):
    """Recovery must read the real state, not assume "off"."""
    backend.fail = True
    assert await switch.is_active() is True

    backend.store[switch.KEY] = "1"
    backend.fail = False
    switch._redis_failure_at = None

    assert await switch.is_active() is True
    assert switch._local_fallback_active is False, "the latch should have lifted"
    assert switch._last_state is True, "and the state should come from the key"


# ══════════════════════════════════════════════════════════════════════════
#  3. EXPIRY — A STOPPED SYSTEM DOES NOT RESTART ITSELF
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_activate_writes_the_state_key_with_no_expiry(switch, backend):
    """Asserted from the operation actually issued, not from the resulting value.

    `_redis_set` routes a TTL through `setex` and a TTL-less write through `set`, so the
    operation name is the evidence.
    """
    await switch.activate("no-ttl check", triggered_by="pytest")

    ops = [op for op, key in backend.calls if key == switch.KEY]
    assert "set" in ops, f"activate() did not SET the state key; ops were {ops}"
    assert "setex" not in ops, (
        "activate() wrote the kill-switch state key with a TTL. It will expire on its own "
        "and trading will resume unattended, while deactivate() writes 'off' durably - so "
        "the control decays toward the UNSAFE state."
    )
    assert switch.KEY not in backend.deadlines


@pytest.mark.asyncio
async def test_the_activation_metadata_does_not_outlive_the_state(switch, backend):
    """An active switch with no recorded reason is an operator with no information."""
    await switch.activate("metadata check", triggered_by="pytest")
    metadata_key = f"{switch.KEY}:metadata"
    assert metadata_key in backend.store
    assert metadata_key not in backend.deadlines, (
        "the activation metadata carries a TTL, so it can vanish while the switch is "
        "still engaged"
    )


@pytest.mark.asyncio
async def test_a_stopped_system_is_still_stopped_a_day_later(switch, backend):
    """The behavioural form of the TTL fix, over the old 24-hour horizon."""
    await switch.activate("expiry check", triggered_by="pytest")
    switch._local_fallback_active = False  # force the Redis path, not the local latch
    switch._redis_failure_at = None

    backend.advance(86_400 + 60)

    assert await switch.is_active() is True, (
        "the kill switch expired on its own and reported trading as permitted 24 hours "
        "after an operator stopped the system"
    )


@pytest.mark.asyncio
async def test_the_fake_backend_really_does_honour_ttls(switch, backend):
    """Negative control for the test above.

    If the fake ignored expiry, "still active a day later" would pass regardless of what
    `activate()` did. This writes the key the way the PRE-FIX code wrote it and shows it
    does expire under the same clock advance.
    """
    await backend.setex(switch.KEY, 86_400, "1")
    assert await switch.is_active() is True

    backend.advance(86_400 + 60)
    switch._local_fallback_active = False
    switch._redis_failure_at = None

    assert await switch.is_active() is False, (
        "the fake backend does not honour TTLs, so the expiry assertions above prove "
        "nothing"
    )


@pytest.mark.asyncio
async def test_deactivate_is_as_durable_as_activate(switch, backend):
    """Symmetry is the property. Neither direction may be the one that decays.

    The reset now carries authorization evidence (KILL-02) and acknowledges the hold the
    activation registered. The property under test is unchanged: the "off" value must be
    written without a TTL, exactly as the "on" value is.
    """
    await switch.activate("sym", triggered_by="pytest")
    await switch.deactivate(
        "sym", acknowledge_holds=["pytest"], **AUTHORIZED_RESET
    )

    assert backend.store[switch.KEY] == "0"
    assert switch.KEY not in backend.deadlines
    switch._local_fallback_active = False
    assert await switch.is_active() is False


# ══════════════════════════════════════════════════════════════════════════
#  4. CONCURRENT TRIGGERS, RESTART, MULTI-PROCESS VISIBILITY
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_concurrent_triggers_all_converge_on_active(switch, backend):
    """Eight simultaneous activations must leave one coherent state and eight audit rows."""
    await asyncio.gather(
        *(switch.activate(f"trigger {i}", triggered_by=f"source-{i}") for i in range(8))
    )

    assert backend.store[switch.KEY] == "1"
    assert await switch.is_active() is True
    history = await switch.get_history(limit=100)
    assert len(history) == 8, f"expected 8 audit entries, got {len(history)}"
    assert all("ACTIVATE" in entry for entry in history)


@pytest.mark.asyncio
async def test_a_second_instance_observes_the_same_state(backend):
    """Two instances, one store: the state must not live in process memory.

    `_local_fallback_active` and `_last_state` are per-instance by design, so the only
    thing that can make two workers agree is the shared backend.
    """
    api = GlobalKillSwitch()
    worker = GlobalKillSwitch()

    assert await worker.is_active() is False  # control
    await api.activate("triggered by the API process", triggered_by="api")

    assert await worker.is_active() is True, (
        "a switch triggered in one process was not observed in another. Every API and "
        "worker process must see the same authoritative state."
    )


@pytest.mark.asyncio
async def test_a_fresh_instance_after_a_restart_still_sees_the_switch(backend):
    """A worker restart discards all in-memory latches. The store is what survives."""
    first = GlobalKillSwitch()
    await first.activate("before restart", triggered_by="pytest")

    restarted = GlobalKillSwitch()
    assert restarted._local_fallback_active is False, "a fresh instance starts unlatched"
    assert await restarted.is_active() is True, (
        "the kill switch did not survive a process restart, so a rolling deploy would "
        "silently resume trading"
    )


@pytest.mark.asyncio
async def test_an_outage_does_not_let_a_second_worker_diverge(backend):
    """The two defects compound, and this is where it mattered most.

    The API triggers the switch while Redis is down: the local latch engages but the write
    fails, so there is nothing in the store. A second worker then has no authoritative
    state to read. Before the fix it read `None` and happily kept trading while the
    operator believed the system was stopped. Now both fail closed.
    """
    api = GlobalKillSwitch()
    worker = GlobalKillSwitch()

    backend.fail = True
    await api.activate("stop everything", triggered_by="operator")

    assert api._local_fallback_active is True
    assert backend.store.get(api.KEY) is None, "the write was supposed to fail"

    assert await worker.is_active() is True, (
        "a second worker kept trading during a Redis outage while an operator had "
        "triggered the kill switch. The operator has no way to know."
    )


def test_two_real_os_processes_agree_through_a_shared_store(tmp_path):
    """Genuinely two processes, one store, asserted from the child's own exit state.

    The store is a JSON file rather than a Redis server (none was available here), so what
    this proves is that `GlobalKillSwitch` derives its answer from the backend and not
    from process-local memory. A child process that never called `activate()` must still
    report the switch as engaged.
    """
    engaged = tmp_path / "store_engaged.json"
    engaged.write_text(json.dumps({"system:kill_switch": "1"}), encoding="utf-8")
    empty = tmp_path / "store_empty.json"
    empty.write_text(json.dumps({}), encoding="utf-8")

    # ONE child invocation takes BOTH measurements. Importing the backend is the
    # expensive part of this test, and running the child twice doubled it for no extra
    # evidence - the first version of this test did, and the pytest process was killed by
    # a console control event while waiting.
    child = textwrap.dedent(
        """
        import asyncio, json, pathlib, sys
        sys.path.insert(0, sys.argv[3])

        import backend_app.backend.redis_manager as rm
        from backend_app.core.global_safety import GlobalKillSwitch

        class FileBackedRedis:
            def __init__(self, path):
                self.path = pathlib.Path(path)
            def _load(self):
                return json.loads(self.path.read_text(encoding="utf-8"))
            async def get(self, key):
                return self._load().get(key)
            async def set(self, key, value):
                data = self._load(); data[key] = value
                self.path.write_text(json.dumps(data), encoding="utf-8"); return True
            async def setex(self, key, ttl, value):
                return await self.set(key, value)
            async def lpush(self, key, value):
                return True
            async def lrange(self, key, a, b):
                return []
            async def expire(self, key, seconds):
                return True

        def read(path):
            rm.redis_manager = FileBackedRedis(path)
            return asyncio.run(GlobalKillSwitch().is_active())

        engaged = read(sys.argv[1])
        empty = read(sys.argv[2])
        print("ENGAGED=" + str(engaged))
        print("EMPTY=" + str(empty))
        sys.exit(0 if (engaged is True and empty is False) else 1)
        """
    )
    script = tmp_path / "child_reads_switch.py"
    script.write_text(child, encoding="utf-8")

    done = subprocess.run(
        [sys.executable, str(script), str(engaged), str(empty), str(_REPO_ROOT)],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=str(_REPO_ROOT),
    )

    assert "ENGAGED=True" in done.stdout, (
        f"a separate OS process did not observe the engaged kill switch.\n"
        f"stdout: {done.stdout}\nstderr: {done.stderr[-2000:]}"
    )
    # Negative control, in the same run: the same child against an empty store must
    # disagree, proving it read the store rather than defaulting to blocked.
    assert "EMPTY=False" in done.stdout, (
        f"the child reported blocked even with an empty store, so the assertion above "
        f"proves nothing.\nstdout: {done.stdout}\nstderr: {done.stderr[-2000:]}"
    )
    assert done.returncode == 0, f"child exited {done.returncode}: {done.stdout}"


# ══════════════════════════════════════════════════════════════════════════
#  5. THE CANONICAL EXECUTION BOUNDARY
# ══════════════════════════════════════════════════════════════════════════


def _intent(**overrides: Any) -> Dict[str, Any]:
    intent: Dict[str, Any] = {
        "tenant_id": uuid4(),
        "strategy_id": "strat-kill-1",
        "symbol": "BTCUSDT",
        "side": "buy",
        "size": Decimal("0.25"),
        "price": Decimal("61234.50"),
        "source": "bot_runner",
    }
    intent.update(overrides)
    return intent


class RefusingExchange:
    """Any venue call at all is a failure. The order must never get this far."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def __getattr__(self, name: str):
        def _boom(*_a, **_k):
            self.calls.append(name)
            raise AssertionError(
                f"the live order path called exchange.{name} with the kill switch engaged"
            )

        return _boom


@pytest.mark.asyncio
async def test_a_triggered_switch_blocks_at_the_execution_boundary(
    monkeypatch: pytest.MonkeyPatch, backend: FakeRedis
):
    """The REAL GlobalKillSwitch, the REAL engine, no recording stub.

    `tests/test_execution_environment_guard.py` proves this with a `RecordingKillSwitch`.
    That is the right tool for its purpose but it cannot show that the production class
    blocks, only that the engine consults whatever object it is given. Here the actual
    `GlobalKillSwitch` is wired in over a backend that can be controlled, and the engine
    is driven through `execute_with_idempotency`.
    """
    from backend_app.core.execution_engine import ExecutionEngine
    import backend_app.core.global_safety as global_safety

    exchange = RefusingExchange()
    engine = ExecutionEngine(
        portfolio_state={"total_equity": Decimal("100000.0")},
        exchange_executor=exchange,
    )

    real_switch = GlobalKillSwitch()
    await real_switch.activate("boundary test", triggered_by="pytest")
    monkeypatch.setattr(global_safety, "get_global_kill_switch", lambda: real_switch)

    result = await engine.execute_with_idempotency(**_intent())

    assert result["status"] == "blocked", f"the order was not blocked: {result}"
    assert "kill switch" in result["message"].lower(), result["message"]
    assert exchange.calls == [], f"the order reached the venue: {exchange.calls}"


@pytest.mark.asyncio
async def test_the_same_order_is_not_blocked_once_the_switch_is_cleared(
    monkeypatch: pytest.MonkeyPatch, backend: FakeRedis
):
    """Negative control for the boundary test.

    Without it, "blocked" could be any of the other refusals on that path.

    The order must still be refused — the live path has five independent refusals and
    this suite must not disable any of them — so the assertion is specifically that the
    KILL SWITCH is no longer the thing refusing it. `_validate_strategy_exists` is
    stubbed to refuse, which is the next gate after the kill switch and reaches no
    database. `SessionLocal` is replaced by something that raises, so if the path ever
    does get as far as opening a connection this test fails loudly instead of hanging on
    a network timeout. (The first version of this test omitted that, let the order run on
    to `check_idempotent_execution`, and the whole pytest process stalled.)
    """
    from backend_app.core.execution_engine import ExecutionEngine
    import backend_app.core.execution_engine as engine_module
    import backend_app.core.global_safety as global_safety

    engine = ExecutionEngine(
        portfolio_state={"total_equity": Decimal("100000.0")},
        exchange_executor=RefusingExchange(),
    )

    def _no_session(*_a, **_k):
        raise AssertionError(
            "the order path opened a database session; this test must not touch a database"
        )

    monkeypatch.setattr(engine_module, "SessionLocal", _no_session)
    monkeypatch.setattr(engine, "_validate_strategy_exists", lambda *_a, **_k: False)

    real_switch = GlobalKillSwitch()
    await real_switch.activate("boundary control", triggered_by="pytest")
    await real_switch.deactivate(
        "boundary control cleared",
        acknowledge_holds=["pytest"],
        **AUTHORIZED_RESET,
    )
    monkeypatch.setattr(global_safety, "get_global_kill_switch", lambda: real_switch)

    result = await engine.execute_with_idempotency(**_intent())

    message = (result.get("message") or "").lower()
    assert "kill switch" not in message, (
        f"the kill switch still refused the order after deactivation: {result}"
    )
    # And it WAS refused, by the next gate down. A cleared kill switch must not become a
    # green light.
    assert result.get("status") != "success", (
        f"a cleared kill switch let the order through every other gate: {result}"
    )


@pytest.mark.asyncio
async def test_enforce_kill_switch_raises_when_engaged(backend: FakeRedis, monkeypatch):
    """`enforce_kill_switch()` refuses when the switch is engaged.

    HISTORY OF THIS TEST, BECAUSE THE CHANGE MATTERS
    -----------------------------------------------
    This was `test_enforce_kill_switch_works_but_guards_nothing`, and it asserted BOTH
    that the function raises AND that `call_sites == []` -- i.e. it pinned KILL-03, that
    the one function whose purpose is to refuse execution had zero call sites across 401
    `backend_app` source files. Its own failure message said:

        "That is an improvement, but TRADING_SAFETY_AUDIT.md records it as dead code and
         must be corrected."

    Phase 9 made that correction: `enforce_kill_switch()` is now called in
    `exchange_executor.place_order`, as the last statement before `create_order`. The
    zero-call-site half of this test is therefore retired, and the assertion that replaces
    it is strictly stronger -- it is not enough that a call site exists, it must be on the
    canonical venue boundary and it must precede the irreversible write. That lives in
    `tests/test_kill03_authoritative_global_halt.py`:

        test_enforce_kill_switch_now_has_a_real_call_site_on_the_venue_path
        test_the_call_site_is_before_the_venue_write_not_after

    What remains here is the half that was always worth asserting: the function's own
    behaviour, plus the inline gateway check it does NOT replace.
    """
    import backend_app.core.global_safety as global_safety

    engaged = GlobalKillSwitch()
    await engaged.activate("enforce test", triggered_by="pytest")
    monkeypatch.setattr(global_safety, "get_global_kill_switch", lambda: engaged)

    with pytest.raises(ExecutionBlocked):
        await enforce_kill_switch()

    # NEGATIVE CONTROL: it does not raise unconditionally.
    #
    # The backend must be cleared as well as the instance replaced. A fresh
    # `GlobalKillSwitch` reads the authoritative state rather than carrying its own, so
    # with `system:kill_switch` still set to "1" from the activation above it would
    # correctly report ACTIVE -- which is the whole point of the control being in the
    # backend, and is what the first version of this negative control got wrong.
    backend.store.pop(GlobalKillSwitch.KEY, None)
    backend.sets.pop(GlobalKillSwitch.HOLDS_KEY, None)
    cleared = GlobalKillSwitch()
    monkeypatch.setattr(global_safety, "get_global_kill_switch", lambda: cleared)
    await enforce_kill_switch()

    # Defence in depth: the inline gateway check must not have been removed in favour of
    # the boundary one. The gateway refuses early and cheaply, before an idempotency claim
    # is taken; the boundary closes the window between that check and the venue write.
    # Both are wanted.
    engine_source = (
        _REPO_ROOT / "backend_app" / "core" / "execution_engine.py"
    ).read_text(encoding="utf-8", errors="replace")
    assert "get_global_kill_switch" in engine_source and "is_active" in engine_source, (
        "execution_engine.py no longer consults the kill switch inline. The venue-boundary "
        "check still exists, but losing this one means an order takes an idempotency claim "
        "and runs the capacity transaction before anything notices the halt."
    )


# ══════════════════════════════════════════════════════════════════════════
#  6. RESET AUTHORIZATION, AUDIT TRAIL, AND THE EMERGENCY POLICY
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_both_transitions_are_recorded_in_the_audit_trail(
    switch, backend, captured_audit
):
    """Both transitions reach BOTH trails: the Redis view and the durable record.

    The Redis list is retained unchanged and is still asserted here. What changed in
    Phase 9 is that it is no longer the system of record -- every helper that writes it
    swallows exceptions, and its `EXPIRE` is applied only in `activate()`, so a reset
    entry inherits a partially-elapsed clock. The durable row is the second half of this
    test.
    """
    await switch.activate("because of X", triggered_by="reconciliation_engine")
    await switch.deactivate(
        "X resolved",
        triggered_by="operator-alice",
        acknowledge_holds=["reconciliation_engine"],
        **AUTHORIZED_RESET,
    )

    history = await switch.get_history(limit=10)
    assert len(history) == 2, f"expected two audit entries, got {history}"
    deactivation, activation = history[0], history[1]
    assert "ACTIVATE" in activation and "because of X" in activation
    assert "reconciliation_engine" in activation
    assert "DEACTIVATE" in deactivation and "X resolved" in deactivation
    assert "operator-alice" in deactivation

    # The durable leg: both transitions recorded as APPLIED, with the operator evidence
    # the Redis entry cannot carry.
    assert [r["action"] for r in captured_audit] == ["ACTIVATE", "DEACTIVATE"]
    assert all(r["outcome"] == "APPLIED" for r in captured_audit)
    reset_record = captured_audit[1]
    assert reset_record["operator_email"] == AUTHORIZED_RESET["operator_email"]
    assert reset_record["authorization_source"] == AUTHORIZED_RESET["authorization_source"]


@pytest.mark.asyncio
async def test_a_failed_reset_refuses_loudly_instead_of_half_clearing(switch, backend):
    """Clearing locally while other processes stay blocked is a split brain.

    THE CONTRACT GOT STRONGER IN PHASE 9, AND THIS TEST WITH IT
    -----------------------------------------------------------
    It used to expect `RuntimeError("... Manual Redis fix required")`. That was raised
    AFTER `deactivate()` had already cleared `_local_fallback_active` -- the method
    cleared the latch first and attempted the Redis write second -- so the very split
    brain this test's own docstring warns about was what the implementation did on a
    backend failure: this process unlatched, the others still blocked.

    Now the reset validates everything BEFORE mutating anything. With the backend down,
    the hold set is unreadable, so it refuses with `RESET_STATE_UNVERIFIABLE` and touches
    no state at all -- which is both earlier and safer. "I could not read the holds" is
    not evidence that there are none; conflating those two is KILL-01's shape.

    The `RuntimeError` path still exists for the case this one no longer reaches: an
    AUTHORIZED reset whose state write then fails. That is a different event -- the reset
    was permitted and the backend failed -- and it demands manual repair rather than being
    reported as a refusal.
    """
    await switch.activate("before failed reset", triggered_by="pytest")
    backend.fail = True

    with pytest.raises(KillSwitchResetRefused) as excinfo:
        await switch.deactivate(
            "attempted reset",
            triggered_by="pytest",
            acknowledge_holds=["pytest"],
            **AUTHORIZED_RESET,
        )
    assert excinfo.value.code == "RESET_STATE_UNVERIFIABLE"

    # NOTHING WAS MUTATED. This is the half the old ordering could not provide.
    assert switch._local_fallback_active is True, (
        "the local latch was cleared during a failed reset, so this process now permits "
        "trades while every other process is still blocked"
    )
    backend.fail = False
    assert await switch.is_active() is True
    assert await switch.get_active_holds() == {"pytest"}, (
        "the hold was released by a reset that was refused"
    )


def test_reset_now_requires_authorization_evidence():
    """KILL-02, corrected.

    HISTORY OF THIS TEST
    --------------------
    This was `test_reset_is_audited_but_takes_no_authorization`. It asserted
    `set(signature.parameters) == {"self", "reason", "triggered_by"}` and that none of
    `operator`/`credential`/`token`/`role`/`actor_id` appeared -- recording, as a passing
    test, that an emergency control could be cleared by any caller and attributed to any
    string they chose. Its docstring said:

        "If a future change adds real authorization, this test fails and the documentation
         gets corrected."

    Phase 9 added it, and the documentation is corrected in
    staging/TRADING_SAFETY_AUDIT.md. The assertion is inverted: the evidence must be
    present, and present in the SIGNATURE rather than validated in the body, so a caller
    that has not established who is acting cannot call the method at all. Same structural
    reasoning TRADE-05 applied to `risk_approved`.

    The behavioural half -- that a reset without meaningful evidence is refused and
    recorded -- is in `tests/test_kill03_authoritative_global_halt.py`.
    """
    signature = inspect.signature(GlobalKillSwitch.deactivate)
    names = set(signature.parameters)

    assert {"operator_id", "authorization_source", "acknowledge_holds"} <= names, (
        f"deactivate()'s signature is {sorted(names)}, which no longer requires "
        f"authorization evidence. KILL-02 is reopened: an emergency control can be "
        f"cleared without establishing who did it or on what authority."
    )
    for required in ("operator_id", "authorization_source", "acknowledge_holds"):
        param = signature.parameters[required]
        assert param.default is inspect.Parameter.empty, (
            f"{required!r} acquired a default, so the requirement became advisory"
        )

    # `triggered_by` survives as an optional label, not as the authorization.
    assert signature.parameters["triggered_by"].default is None


@pytest.mark.asyncio
async def test_activation_refuses_new_orders_and_does_not_flatten(switch, backend):
    """The intended emergency policy, stated as a test.

    `activate()` cancels nothing and closes nothing: it blocks NEW orders. Resting orders
    at the venue and open positions are unaffected. That is a deliberate policy, but it is
    not what "kill switch" suggests, so it is pinned here — a reader must not assume
    positions are protected by triggering this.
    """
    cancelled: List[Any] = []

    class Watcher:
        def __getattr__(self, name):
            def _record(*a, **k):
                cancelled.append(name)

            return _record

    switch_under_test = GlobalKillSwitch()
    await switch_under_test.activate("policy check", triggered_by="pytest")

    assert cancelled == []
    source = inspect.getsource(GlobalKillSwitch.activate).lower()
    for verb in ("cancel", "close_position", "flatten", "liquidate"):
        assert verb not in source, (
            f"activate() now performs {verb!r}. The emergency policy changed from "
            f"'refuse new orders' to acting on existing exposure, and "
            f"TRADING_SAFETY_AUDIT.md must be updated to match."
        )


@pytest.mark.asyncio
async def test_get_status_reports_the_latch_and_not_just_the_switch(switch, backend):
    """An operator needs to know WHY it is blocked: triggered, or backend unreachable."""
    backend.fail = True
    status = await switch.get_status()

    assert status["kill_switch_active"] is True
    assert status["local_latch_active"] is True, (
        "the status endpoint cannot distinguish a deliberately triggered switch from a "
        "fail-closed latch caused by an unreachable backend"
    )
    assert status["redis_failure_at"] is not None


def test_the_module_documents_the_policy_it_implements():
    """The docstring and the code disagreed; that is how KILL-01 survived review."""
    source = (
        _REPO_ROOT / "backend_app" / "core" / "global_safety.py"
    ).read_text(encoding="utf-8", errors="replace")

    assert "KILL-01" in source, (
        "the KILL-01 explanation has been removed from global_safety.py. The fail-open "
        "behaviour it describes is subtle enough that a future edit could reintroduce it."
    )
    # The specific line that used to be the defect must not come back.
    assert "return None  # Handled by caller" not in source, (
        "_redis_get swallows errors and returns None again. That makes 'key absent' and "
        "'backend unreachable' indistinguishable and reopens KILL-01."
    )
