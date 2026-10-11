"""KILL-03 / KILL-02 / KILL-04 — the halt control, measured end to end.

WHAT THE THREE FINDINGS WERE, AND WHAT THIS FILE PROVES
=======================================================

**KILL-03 — the halt was not a halt.** `POST /api/admin/kill-all` called
`fleet.shutdown_all()` and nothing else. That method iterates a snapshot of the
IN-PROCESS `_active_fleet` dict, so the "global" halt stopped this pod's bots and left
every other process trading; nothing durable recorded that trading was stopped, so a
restarted worker resumed immediately. Separately, `enforce_kill_switch()` --- the function
whose entire purpose is to refuse execution --- had **zero call sites across 401
`backend_app` source files**. It raised correctly and guarded nothing.

**KILL-04 — the venue boundary asserted a name, not a state.** The signed
`LiveOrderAuthorization` carries the check name `"kill_switch"` in its set, minted at
`execution_engine.py:1270`. That is a true statement about a check that ran *earlier*: the
only genuine `is_active()` read is at `execution_engine.py:770`, and between that line and
`create_order` the code takes an idempotency claim, runs the advisory-lock capacity
transaction, commits to the database and awaits the rate limiter. A halt engaged in that
window was never noticed.

**KILL-02 — the reset was unauthenticated and the trail was expiring.**
`deactivate(reason, triggered_by=None)` had no credential, role or operator parameter;
`triggered_by` was free text nothing verified. There was no such thing as a *denied*
reset. The audit trail was a Redis LIST whose `EXPIRE` is applied only in `activate()`, so
a reset entry inherited a partially-elapsed 7-day clock, and every Redis helper involved
swallows exceptions --- so the platform could be halted with no record at all.

HOW THIS FILE MEASURES
======================
Two oracles, and nothing is asserted about a function in isolation:

* `RecordingExchange.create_order` --- `calls == []` means no order reached a venue. Every
  "it was blocked" assertion is paired with a **negative control** proving the same order
  DOES reach `create_order` when the switch is off, so an empty list is evidence rather
  than an artefact of a broken fake or an unrelated refusal.
* `public.safety_control_audit` --- read back over an INDEPENDENT psycopg2 connection, so
  "it was recorded" means committed and visible to another session, not merely passed to
  an ORM.

`CCXTExchangeExecutor` is built with `object.__new__` so no constructor side effect or
network call occurs **and `place_order` is the real method body** --- the same construction
`tests/test_trade04_live_order_gate_at_venue_boundary.py` uses, for the same reason: a
hand-written stand-in would pass whether or not the gate exists.

A NOTE ON WHY THE FAKE REDIS IS MANDATORY HERE
----------------------------------------------
With no Redis, `redis_manager` is an uninitialised `__getattr__` proxy that raises
`AttributeError`, so `_redis_get` raises `RedisUnavailable` and `is_active()` **fails
closed and returns True**. That is correct behaviour (it is KILL-01's fix) and it means a
test that merely calls `place_order` in a bare environment is blocked for the wrong
reason. Every test below therefore installs an explicit, controllable backend and states
whether the switch is engaged --- so a refusal is attributable to the halt and a success is
attributable to its absence.

WHAT IS NOT CLAIMED
-------------------
* No real Redis server and no real exchange are involved. The multi-worker and
  worker-restart cases are proven against a shared file-backed store and separate
  `GlobalKillSwitch` instances, which is what establishes that state comes from the
  backend rather than from process memory. It is not a test against a Redis cluster.
* The durable-audit section requires PostgreSQL and SKIPS without it. A skip is not a
  pass; `test_the_audit_table_requirement_is_explicit` asserts the gate exists so the
  skip cannot be silent.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import pathlib
import sys
import textwrap
import threading
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import backend_app.backend.redis_manager as redis_manager_module  # noqa: E402
from backend_app.backend.exchange_executor import \
    CCXTExchangeExecutor  # noqa: E402
from backend_app.core.global_safety import (  # noqa: E402
    ExecutionBlocked, GlobalKillSwitch, KillSwitchResetRefused,
    RedisUnavailable, enforce_kill_switch)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

TENANT = uuid.uuid4()
SYMBOL = "BTC/USDT"
SIZE = Decimal("0.01")

#: Evidence an authorized reset must carry. Named once so every test uses the same shape
#: and a change to the contract shows up as one edit rather than fifteen.
AUTHORIZED = {
    "operator_id": str(uuid.uuid4()),
    "operator_email": "operator@example.invalid",
    "authorization_source": "fastapi:get_operator_user",
}


def _run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════════════
#  THE CONTROLLABLE BACKEND
# ══════════════════════════════════════════════════════════════════════════


class FakeRedisDown(Exception):
    """What the fake raises when made unreachable.

    Deliberately not a subclass of anything `global_safety` knows about: an arbitrary
    backend exception must still be translated to `RedisUnavailable` and must still fail
    closed.
    """


class FakeRedis:
    """A Redis stand-in supporting the operations `global_safety` actually calls.

    Strings (`get`/`set`), lists (`lpush`/`lrange`), sets (`sadd`/`srem`/`smembers`),
    `expire` and `delete`, plus `nx`/`ex` on `set` for the transition lock. Records every
    operation so "the key carries no expiry" and "the lock was taken" are asserted from
    what was actually sent.
    """

    def __init__(self) -> None:
        self.store: Dict[str, str] = {}
        self.sets: Dict[str, set] = {}
        self.lists: Dict[str, List[str]] = {}
        self.expiries: Dict[str, int] = {}
        self.fail: bool = False
        self.calls: List[tuple] = []

    def _check(self):
        if self.fail:
            raise FakeRedisDown("fake redis is unreachable")

    async def get(self, key):
        self._check()
        self.calls.append(("get", key))
        return self.store.get(key)

    async def set(self, key, value, nx=False, ex=None):
        self._check()
        self.calls.append(("set", key, value, nx, ex))
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex:
            self.expiries[key] = ex
        return True

    async def setex(self, key, ex, value):
        self._check()
        self.calls.append(("setex", key, ex, value))
        self.store[key] = value
        self.expiries[key] = ex
        return True

    async def delete(self, key):
        self._check()
        self.calls.append(("delete", key))
        self.store.pop(key, None)
        return 1

    async def lpush(self, key, value):
        self._check()
        self.calls.append(("lpush", key, value))
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    async def lrange(self, key, start, end):
        self._check()
        self.calls.append(("lrange", key, start, end))
        items = self.lists.get(key, [])
        return items[start:] if end == -1 else items[start:end + 1]

    async def expire(self, key, seconds):
        self._check()
        self.calls.append(("expire", key, seconds))
        self.expiries[key] = seconds
        return True

    async def sadd(self, key, *values):
        self._check()
        self.calls.append(("sadd", key) + values)
        self.sets.setdefault(key, set()).update(values)
        return len(values)

    async def srem(self, key, *values):
        self._check()
        self.calls.append(("srem", key) + values)
        self.sets.setdefault(key, set()).difference_update(values)
        return len(values)

    async def smembers(self, key):
        self._check()
        self.calls.append(("smembers", key))
        return set(self.sets.get(key, set()))


@pytest.fixture
def backend(monkeypatch):
    """Install the fake as the module attribute `_redis_get` imports at call time."""
    fake = FakeRedis()
    monkeypatch.setattr(redis_manager_module, "redis_manager", fake)
    monkeypatch.setattr(redis_manager_module, "_redis_manager", fake, raising=False)
    return fake


@pytest.fixture
def switch(backend):
    """A FRESH GlobalKillSwitch, so the module singleton does not leak between tests."""
    return GlobalKillSwitch()


@pytest.fixture(autouse=True)
def no_db_audit(monkeypatch, request):
    """Stub the durable audit write for every test except the PostgreSQL section.

    `record_safety_transition` never raises --- a failed audit write must not prevent a
    halt --- so leaving it live would merely log CRITICAL and return None. It is stubbed
    anyway, for two reasons: it would otherwise attempt a real `SessionLocal()` per
    transition (slow, and dependent on whatever DATABASE_URL happens to be configured),
    and capturing the calls lets the sections below assert WHAT would have been recorded
    without needing a database.

    The PostgreSQL section opts out via the `realaudit` marker and exercises the genuine
    writer.
    """
    recorded: List[dict] = []
    if request.node.get_closest_marker("realaudit"):
        return recorded

    async def _fake(**kwargs):
        recorded.append(kwargs)
        return str(uuid.uuid4())

    import backend_app.core.safety_audit as sa

    # `global_safety` imports the symbol INSIDE the method body
    # (`from backend_app.core.safety_audit import ... record_safety_transition`), so the
    # module attribute is resolved at call time and patching it here is the effective
    # seam. Patching a name bound at import time would not have worked.
    monkeypatch.setattr(sa, "record_safety_transition", _fake)
    return recorded


@pytest.fixture
def audit_sink(no_db_audit):
    """The list of audit records the stub captured, in order."""
    return no_db_audit


# ══════════════════════════════════════════════════════════════════════════
#  THE VENUE ORACLE
# ══════════════════════════════════════════════════════════════════════════


class RecordingExchange:
    """Stands in for the ccxt client. Records every write; performs none."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.calls.append((symbol, type_, side, amount, price, params))
        return {
            "id": "venue-order-1",
            "filled": amount,
            "remaining": 0,
            "average": price or 50_000,
            "status": "closed",
        }

    async def cancel_order(self, order_id, symbol=None):
        self.calls.append(("cancel", order_id, symbol))
        return {"id": order_id, "status": "canceled"}


class _Normalizer:
    """The minimum surface `place_order` asks of its normalizer."""

    def format_symbol_for_ccxt(self, symbol):
        return str(symbol)

    def map_order_side(self, side):
        return getattr(side, "value", str(side)).lower()

    def map_order_type(self, order_type):
        return getattr(order_type, "value", str(order_type)).lower()

    def normalize_size(self, _symbol, size):
        return str(size)

    def normalize_price(self, _symbol, price):
        return str(price)

    def market_minimums(self, _symbol):
        # Permissive, so a refusal here is never the venue-minimum check.
        return {"min_amount": Decimal("0"), "min_cost": Decimal("0")}


class _RateLimiter:
    async def acquire(self, _op):
        return None


def build_executor(exchange: RecordingExchange) -> CCXTExchangeExecutor:
    """A REAL CCXTExchangeExecutor whose venue client is the recorder.

    Identical construction to `tests/test_trade04_live_order_gate_at_venue_boundary.py`'s
    `build_executor`, deliberately: the two files must be measuring the same `place_order`.
    """
    ex = object.__new__(CCXTExchangeExecutor)
    ex._exchange = exchange
    ex._connected = True
    ex._current_execution_id = None
    ex._current_validation_token = None
    ex._consumed_execution_id = None
    ex._current_live_authorization = None
    ex.exchange_id = "binance"
    ex.sandbox = False
    ex.normalizer = _Normalizer()
    ex.rate_limiter = _RateLimiter()
    ex.api_key = "k"
    ex.api_secret = "s"
    ex.password = None
    return ex


def arm_for_venue(executor, exchange_id="binance", size=SIZE, symbol=SYMBOL):
    """Give the executor everything `place_order` needs to reach `create_order`.

    The anti-bypass token AND a complete signed live authorisation --- i.e. the state of
    an order that has passed every gate the gateway mints. That is what makes the
    kill-switch assertions below meaningful: the ONLY thing left that can refuse the order
    is the halt.
    """
    from backend_app.core.global_safety import generate_validation_token
    from backend_app.core.live_execution_gate import (REQUIRED_CHECKS,
                                                      authorize_live_order)

    exec_id = f"exec-{uuid.uuid4().hex[:12]}"
    executor._current_execution_id = exec_id
    executor._current_validation_token = generate_validation_token(
        exec_id, symbol, size
    )
    executor._current_live_authorization = authorize_live_order(
        execution_id=exec_id,
        tenant_id=str(TENANT),
        strategy_id="strategy-under-test",
        symbol=symbol,
        side="buy",
        size=size,
        exchange_id=exchange_id,
        checks=REQUIRED_CHECKS,
    )
    return exec_id


def place(executor, size=SIZE, symbol=SYMBOL):
    from backend_app.core.models.pydantic_models import OrderSide, OrderType

    return executor.place_order(
        symbol=symbol, side=OrderSide.buy, order_type=OrderType.market, size=size
    )


# ══════════════════════════════════════════════════════════════════════════
#  1. THE CHAIN: AUTHORIZED OPERATOR ACTION -> REFUSED EXCHANGE SUBMISSION
# ══════════════════════════════════════════════════════════════════════════


def test_the_negative_control_an_unhalted_order_reaches_create_order(switch, backend):
    """THE NEGATIVE CONTROL, FIRST, BECAUSE EVERYTHING ELSE DEPENDS ON IT.

    A fully authorised order with the switch OFF must reach `create_order`. Without this,
    every `calls == []` below could be an artefact of a broken fake, a missing
    authorisation, or any other refusal on the path --- and the file would prove nothing.

    Note the switch is explicitly readable-and-off here: the backend answers and the key
    is absent. In a bare environment `is_active()` fails closed and this would be blocked
    for the wrong reason.
    """
    exchange = RecordingExchange()
    executor = build_executor(exchange)
    arm_for_venue(executor)

    import backend_app.core.global_safety as gs
    gs._global_kill_switch = switch  # the boundary reads the module singleton

    result = _run(place(executor))

    assert exchange.calls, (
        "the negative control failed: a fully authorised order with the kill switch OFF "
        "did not reach create_order, so no 'it was blocked' assertion in this file is "
        "evidence of anything"
    )
    assert result.success is True
    assert _run(switch.is_active()) is False


def test_an_operator_halt_refuses_the_order_at_the_venue_boundary(switch, backend):
    """THE WHOLE CHAIN. Authorized halt -> `create_order` never called.

    The order is armed with a complete, valid, signed live authorisation, so the only
    thing that can refuse it is the halt. Pair this with the negative control above.
    """
    exchange = RecordingExchange()
    executor = build_executor(exchange)
    arm_for_venue(executor)

    import backend_app.core.global_safety as gs
    gs._global_kill_switch = switch

    _run(switch.activate(
        "incident 1234: venue reporting stale fills",
        triggered_by="operator:operator@example.invalid",
        operator_id=AUTHORIZED["operator_id"],
        operator_email=AUTHORIZED["operator_email"],
        authorization_source=AUTHORIZED["authorization_source"],
    ))

    with pytest.raises(ExecutionBlocked):
        _run(place(executor))

    assert exchange.calls == [], (
        "an order reached the exchange client while the global kill switch was engaged"
    )


def test_the_halt_is_enforced_even_with_a_perfect_authorization(switch, backend):
    """KILL-04, stated as the property it is.

    The signed authorisation carries the check name `"kill_switch"` and verifies cleanly.
    Before this fix that name was the ONLY kill-switch evidence at the boundary, and it
    describes a check that ran before the idempotency claim, the capacity transaction and
    the rate limiter. So an authorisation minted while the switch was off must still be
    refused if the switch is engaged by the time the order reaches the venue.
    """
    exchange = RecordingExchange()
    executor = build_executor(exchange)

    import backend_app.core.global_safety as gs
    gs._global_kill_switch = switch

    # Mint the authorisation while trading is permitted...
    arm_for_venue(executor)
    assert _run(switch.is_active()) is False

    # ...then the platform is halted in the window before submission.
    _run(switch.activate("halted mid-flight", triggered_by="health_monitor"))

    with pytest.raises(ExecutionBlocked):
        _run(place(executor))

    assert exchange.calls == [], (
        "an authorisation minted before the halt was honoured after it; the boundary is "
        "still asserting the check NAME rather than re-reading the state"
    )


def test_the_authorization_check_still_runs_and_is_not_replaced(switch, backend):
    """The halt check is ADDITIVE. An unauthorised order is still refused as before.

    Guards against a fix that accidentally reorders the boundary so the kill-switch read
    short-circuits the live-authorisation gate.
    """
    from backend_app.core.live_execution_gate import LiveExecutionRefused

    exchange = RecordingExchange()
    executor = build_executor(exchange)

    import backend_app.core.global_safety as gs
    gs._global_kill_switch = switch

    from backend_app.core.global_safety import generate_validation_token
    exec_id = "exec-no-auth"
    executor._current_execution_id = exec_id
    executor._current_validation_token = generate_validation_token(exec_id, SYMBOL, SIZE)
    executor._current_live_authorization = None  # the missing artefact

    with pytest.raises(LiveExecutionRefused) as excinfo:
        _run(place(executor))

    assert excinfo.value.code == "LIVE_EXECUTION_NOT_AUTHORIZED"
    assert exchange.calls == []


def test_a_direct_executor_call_without_the_token_is_still_refused(switch, backend):
    """Direct-bypass attempt: skip the gateway entirely.

    `verify_and_consume_token` runs before both the authorisation gate and the halt check,
    so this is refused by the anti-bypass token. Asserted so that "the halt is enforced"
    is not mistaken for "the halt is the only thing enforced".
    """
    exchange = RecordingExchange()
    executor = build_executor(exchange)

    import backend_app.core.global_safety as gs
    gs._global_kill_switch = switch

    with pytest.raises(ValueError, match="Bypass attempt detected"):
        _run(place(executor))

    assert exchange.calls == []


def test_a_direct_executor_call_during_a_halt_is_refused_even_when_armed(switch, backend):
    """The bypass path and the halt path are independent, and BOTH must hold.

    An attacker who has somehow obtained a valid token and authorisation (the strongest
    position a bypass can reach) still cannot place an order during a halt.
    """
    exchange = RecordingExchange()
    executor = build_executor(exchange)
    arm_for_venue(executor)

    import backend_app.core.global_safety as gs
    gs._global_kill_switch = switch

    _run(switch.activate("halted", triggered_by="operator:ops"))

    with pytest.raises(ExecutionBlocked):
        _run(place(executor))
    assert exchange.calls == []


# ══════════════════════════════════════════════════════════════════════════
#  2. enforce_kill_switch() NOW GUARDS SOMETHING
# ══════════════════════════════════════════════════════════════════════════


def _real_call_sites() -> List[str]:
    """Every genuine `enforce_kill_switch(...)` call node in application source.

    AST-based, not textual. A prior phase's checks were fooled three separate times by
    substring and regex scans matching comments, docstrings and nested parentheses, so
    this walks the tree and counts `ast.Call` nodes only.
    """
    import ast

    sites: List[str] = []
    for root_name in ("backend_app", "backend", "scripts", "core"):
        root = _REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "enforce_kill_switch" not in source:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name == "enforce_kill_switch":
                    rel = path.relative_to(_REPO_ROOT).as_posix()
                    sites.append(f"{rel}:{node.lineno}")
    return sorted(sites)


def test_enforce_kill_switch_now_has_a_real_call_site_on_the_venue_path():
    """KILL-03's headline, inverted.

    The Phase 8 test asserted `call_sites == []` and its own failure message said: "That
    is an improvement, but TRADING_SAFETY_AUDIT.md records it as dead code and must be
    corrected." This is that correction. A safety function with no call sites is not a
    safety function, so the assertion is now that it HAS one --- and specifically that the
    one it has is on the canonical venue boundary, because a call site somewhere
    irrelevant would satisfy a naive count while guarding nothing.
    """
    sites = _real_call_sites()
    assert sites, (
        "enforce_kill_switch() has no real call sites again. It raises correctly and "
        "guards nothing, which is KILL-03."
    )
    assert any(
        s.startswith("backend_app/backend/exchange_executor.py:") for s in sites
    ), (
        "enforce_kill_switch() is called somewhere, but NOT in exchange_executor.py --- "
        f"so the canonical venue boundary is unguarded again. Sites: {sites}"
    )


def test_the_call_site_is_before_the_venue_write_not_after():
    """Placement is the whole value. After `create_order` it would guard nothing.

    Asserted on line order within `place_order`'s own AST range, so a future edit that
    moves the check below the submission fails here rather than silently reopening the
    window.
    """
    import ast

    source = (_REPO_ROOT / "backend_app" / "backend" / "exchange_executor.py").read_text(
        encoding="utf-8", errors="replace"
    )
    tree = ast.parse(source)

    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "place_order":
            # The real implementation, not the abstract declaration: pick the longest.
            if target is None or (node.end_lineno - node.lineno) > (target.end_lineno - target.lineno):
                target = node
    assert target is not None, "place_order no longer exists in exchange_executor.py"

    enforce_lines = []
    create_lines = []
    for node in ast.walk(target):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "enforce_kill_switch":
            enforce_lines.append(node.lineno)
        elif name == "create_order":
            create_lines.append(node.lineno)

    assert enforce_lines, "place_order no longer calls enforce_kill_switch"
    assert create_lines, "place_order no longer calls create_order; repoint this guard"
    assert min(enforce_lines) < min(create_lines), (
        f"enforce_kill_switch is called at line(s) {enforce_lines} but create_order at "
        f"{create_lines} --- the halt check must precede the irreversible write"
    )


def test_the_gateway_check_is_still_there_too():
    """Defence in depth: the inline gateway read at execution_engine must not be removed.

    The boundary check shrinks the window to nothing; the gateway check refuses early and
    cheaply without taking an idempotency claim. Both are wanted.
    """
    engine_source = (
        _REPO_ROOT / "backend_app" / "core" / "execution_engine.py"
    ).read_text(encoding="utf-8", errors="replace")
    assert "get_global_kill_switch" in engine_source
    assert "is_active" in engine_source


# ══════════════════════════════════════════════════════════════════════════
#  3. THE ADMIN ENDPOINT ENGAGES THE AUTHORITATIVE SWITCH
# ══════════════════════════════════════════════════════════════════════════


def test_the_kill_all_handler_engages_the_global_switch():
    """KILL-03's other half, asserted structurally.

    `/api/admin/kill-all` previously called only `fleet.shutdown_all()`, which is
    in-process. Measured then: zero references to `GlobalKillSwitch`,
    `get_global_kill_switch` or the Redis key in the whole router.

    Scoped to the handler's own AST range, so a reference elsewhere in the module (the new
    read-only status endpoint, for instance) cannot satisfy this by accident.
    """
    import ast

    source = (_REPO_ROOT / "backend_app" / "routers" / "admin.py").read_text(
        encoding="utf-8", errors="replace"
    )
    tree = ast.parse(source)

    handler = next(
        (
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "global_kill_switch"
        ),
        None,
    )
    assert handler is not None, "the /kill-all handler `global_kill_switch` is gone"

    body = ast.get_source_segment(source, handler) or ""
    assert "get_global_kill_switch" in body, (
        "/api/admin/kill-all does not reference the global kill switch, so the halt is "
        "in-process only and a restarted worker resumes trading. That is KILL-03."
    )
    assert ".activate(" in body, (
        "/api/admin/kill-all resolves the kill switch but never activates it"
    )


def test_the_switch_is_engaged_before_the_local_fleet_is_torn_down():
    """Order matters: `shutdown_all()` can raise partway through.

    If the teardown were first and failed, some bots would still be running, nothing would
    be recorded, and the handler's `except` would answer 500 --- telling the operator the
    halt failed while trading continued. Engaging the authoritative switch first means
    every order path refuses regardless of what the teardown does.
    """
    import ast

    source = (_REPO_ROOT / "backend_app" / "routers" / "admin.py").read_text(
        encoding="utf-8", errors="replace"
    )
    tree = ast.parse(source)
    handler = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "global_kill_switch"
    )

    activate_line = None
    shutdown_line = None
    for node in ast.walk(handler):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None)
        if name == "activate" and activate_line is None:
            activate_line = node.lineno
        elif name == "shutdown_all" and shutdown_line is None:
            shutdown_line = node.lineno

    assert activate_line is not None, "the handler no longer activates the switch"
    assert shutdown_line is not None, "the handler no longer stops the local fleet"
    assert activate_line < shutdown_line, (
        f"fleet.shutdown_all() at line {shutdown_line} runs BEFORE "
        f"kill_switch.activate() at line {activate_line}. A teardown that raises partway "
        f"would leave bots running with nothing recorded."
    )


def test_a_status_surface_exists_for_verifying_the_halt():
    """The brief requires a way to verify that every worker observes the halt.

    Before this there was no HTTP read of the global switch at all --- only
    `/api/admin/fleet-status`, which reports this pod's in-process fleet and is exactly
    what made `/kill-all` look like it worked.
    """
    source = (_REPO_ROOT / "backend_app" / "routers" / "admin.py").read_text(
        encoding="utf-8", errors="replace"
    )
    assert '@router.get("/kill-switch")' in source, (
        "no read-only kill-switch status endpoint exists, so an operator cannot verify "
        "the authoritative halt state"
    )
    assert "holds_readable" in source, (
        "the status surface does not distinguish 'no holds' from 'could not read the "
        "holds' --- the two facts KILL-01 conflated"
    )


# ══════════════════════════════════════════════════════════════════════════
#  4. KILL-02: RESET AUTHORIZATION
# ══════════════════════════════════════════════════════════════════════════


def test_the_reset_signature_now_requires_authorization_evidence():
    """KILL-02's correction, and the inverse of the Phase 8 assertion.

    The Phase 8 test asserted the signature was exactly `{self, reason, triggered_by}`
    and that none of `operator`/`credential`/`token`/`role`/`actor_id` appeared --- then
    said in its own message: "If an authorization parameter was added,
    TRADING_SAFETY_AUDIT.md's finding about unauthenticated reset must be updated."

    The evidence is required BY THE SIGNATURE, not validated in the body: a caller that
    has not established who is acting cannot call the method at all. That is the same
    structural reasoning TRADE-05 applied to `risk_approved`.
    """
    sig = inspect.signature(GlobalKillSwitch.deactivate)
    names = set(sig.parameters)

    for required in ("operator_id", "authorization_source", "acknowledge_holds"):
        assert required in names, (
            f"deactivate() no longer takes {required!r}, so an emergency control can be "
            f"cleared without establishing who did it or on what authority. That is "
            f"KILL-02."
        )
        param = sig.parameters[required]
        assert param.default is inspect.Parameter.empty, (
            f"{required!r} has a default, so a caller can omit it and the requirement is "
            f"advisory rather than structural"
        )
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{required!r} is positional; keyword-only prevents a caller satisfying it by "
            f"accident with a misordered argument"
        )


def test_activate_deliberately_does_not_require_the_same_evidence():
    """The asymmetry, asserted so it reads as a decision rather than an oversight.

    Engaging a safety control is the safe direction and must stay available to the four
    automatic triggers, which have no operator identity. Clearing one is the dangerous
    direction.
    """
    sig = inspect.signature(GlobalKillSwitch.activate)
    for optional in ("operator_id", "operator_email", "authorization_source"):
        assert sig.parameters[optional].default is None, (
            f"activate()'s {optional!r} is now mandatory, which would stop "
            f"trigger_on_exchange_unhealthy and the three other automatic triggers from "
            f"being able to halt the platform at all"
        )


@pytest.mark.parametrize("missing", ["operator_id", "authorization_source"])
def test_a_reset_without_meaningful_evidence_is_refused(switch, backend, missing):
    """The signature makes the arguments mandatory to SUPPLY; this makes them mandatory
    to be MEANINGFUL. An empty string would otherwise satisfy the signature and record an
    anonymous reset."""
    _run(switch.activate("halted", triggered_by="operator:ops"))

    evidence = dict(AUTHORIZED)
    evidence[missing] = ""

    with pytest.raises(KillSwitchResetRefused) as excinfo:
        _run(switch.deactivate(
            "attempting a reset", acknowledge_holds=["operator:ops"], **evidence
        ))

    assert excinfo.value.code == "RESET_UNAUTHORIZED"
    assert _run(switch.is_active()) is True, "a refused reset cleared the switch anyway"


def test_a_reset_without_a_reason_is_refused(switch, backend):
    _run(switch.activate("halted", triggered_by="operator:ops"))

    with pytest.raises(KillSwitchResetRefused) as excinfo:
        _run(switch.deactivate(
            "   ", acknowledge_holds=["operator:ops"], **AUTHORIZED
        ))

    assert excinfo.value.code == "RESET_REASON_MISSING"
    assert _run(switch.is_active()) is True


def test_an_authorized_reset_with_all_holds_acknowledged_succeeds(switch, backend):
    """The positive control. Without it, "resets are refused" could mean "all resets are
    refused", which would be a different defect."""
    _run(switch.activate("halted", triggered_by="operator:ops"))
    assert _run(switch.is_active()) is True

    _run(switch.deactivate(
        "incident resolved, venue healthy for 30 minutes",
        acknowledge_holds=["operator:ops"],
        **AUTHORIZED,
    ))

    assert _run(switch.is_active()) is False
    assert _run(switch.get_active_holds()) == set()


def test_a_reset_refuses_while_an_unacknowledged_halt_remains(switch, backend):
    """THE "SILENT RE-ENABLE" CASE, which is the substantive half of KILL-02.

    Two independent halts: an operator's, and the reconciliation engine's. The operator
    resolves their own incident and resets, acknowledging only their own hold. Before the
    hold set existed the state was a single string `"1"`, so that reset cleared BOTH ---
    re-enabling trading while the reconciliation engine was still reporting a mismatch,
    with nothing anywhere saying so.
    """
    _run(switch.activate("operator incident", triggered_by="operator:ops"))
    _run(switch.activate("position mismatch", triggered_by="reconciliation_engine"))

    assert _run(switch.get_active_holds()) == {"operator:ops", "reconciliation_engine"}

    with pytest.raises(KillSwitchResetRefused) as excinfo:
        _run(switch.deactivate(
            "my incident is resolved",
            acknowledge_holds=["operator:ops"],
            **AUTHORIZED,
        ))

    assert excinfo.value.code == "RESET_HOLDS_UNACKNOWLEDGED"
    assert "reconciliation_engine" in excinfo.value.detail
    assert _run(switch.is_active()) is True, (
        "the switch was cleared while the reconciliation engine's halt was still active"
    )
    assert "reconciliation_engine" in _run(switch.get_active_holds())


def test_acknowledging_every_hold_explicitly_does_clear_it(switch, backend):
    """The override exists and is explicit. An operator who HAS reviewed both halts can
    clear them; what they cannot do is clear one they never saw."""
    _run(switch.activate("operator incident", triggered_by="operator:ops"))
    _run(switch.activate("position mismatch", triggered_by="reconciliation_engine"))

    _run(switch.deactivate(
        "both reviewed: mismatch was a stale cache, venue healthy",
        acknowledge_holds=["operator:ops", "reconciliation_engine"],
        **AUTHORIZED,
    ))

    assert _run(switch.is_active()) is False
    assert _run(switch.get_active_holds()) == set()


def test_a_reset_fails_closed_when_the_hold_set_cannot_be_read(switch, backend):
    """KILL-01's shape, in the reset path.

    "I could not read the holds" is not evidence that there are none. If the backend is
    unreachable the reset must refuse, not assume an empty set and clear the switch.
    """
    _run(switch.activate("halted", triggered_by="operator:ops"))
    backend.fail = True

    with pytest.raises(KillSwitchResetRefused) as excinfo:
        _run(switch.deactivate(
            "attempting a reset during an outage",
            acknowledge_holds=["operator:ops"],
            **AUTHORIZED,
        ))

    assert excinfo.value.code == "RESET_STATE_UNVERIFIABLE"

    backend.fail = False
    assert _run(switch.is_active()) is True, (
        "the switch was cleared during a backend outage, when the holds could not be read"
    )


def test_the_latch_is_not_cleared_before_the_authoritative_write(switch, backend):
    """An ordering fix, asserted.

    The previous implementation cleared `_local_fallback_active` FIRST and only then
    attempted the Redis write, raising afterwards if it failed --- so a failed reset left
    THIS process unlatched while the others stayed blocked. That is the split brain its
    own docstring warned about. State is now written first and the latch cleared last.
    """
    src = inspect.getsource(GlobalKillSwitch.deactivate)
    write_at = src.find('_redis_set(self.KEY, "0")')
    latch_at = src.find("self._local_fallback_active = False")
    assert write_at != -1 and latch_at != -1
    assert write_at < latch_at, (
        "deactivate() clears the local latch before writing the authoritative state, so "
        "a failed write leaves this process permitting trades while others are blocked"
    )


def test_a_refused_reset_is_itself_recorded(switch, backend, audit_sink):
    """A denied attempt on an emergency control is exactly the event an operator needs.

    Nothing recorded it before, because there was no such thing as a denied reset.
    Recorded BEFORE the exception propagates, so discarding the exception cannot produce
    an unrecorded attempt.
    """
    _run(switch.activate("halted", triggered_by="operator:ops"))
    audit_sink.clear()

    with pytest.raises(KillSwitchResetRefused):
        _run(switch.deactivate(
            "no authority", acknowledge_holds=["operator:ops"],
            operator_id="", operator_email=None,
            authorization_source="fastapi:get_operator_user",
        ))

    refusals = [r for r in audit_sink if r.get("outcome") == "REFUSED"]
    assert refusals, f"a refused reset recorded nothing: {audit_sink}"
    assert refusals[0]["action"] == "DEACTIVATE"
    assert refusals[0]["detail"]["refusal_code"] == "RESET_UNAUTHORIZED"


def test_both_transitions_are_recorded_with_operator_evidence(switch, backend, audit_sink):
    audit_sink.clear()
    _run(switch.activate(
        "because of X",
        triggered_by="operator:ops",
        operator_id=AUTHORIZED["operator_id"],
        operator_email=AUTHORIZED["operator_email"],
        authorization_source=AUTHORIZED["authorization_source"],
    ))
    _run(switch.deactivate(
        "X resolved", acknowledge_holds=["operator:ops"], **AUTHORIZED
    ))

    assert [r["action"] for r in audit_sink] == ["ACTIVATE", "DEACTIVATE"]
    assert all(r["outcome"] == "APPLIED" for r in audit_sink)
    for record in audit_sink:
        assert record["operator_email"] == AUTHORIZED["operator_email"]
        assert record["authorization_source"] == AUTHORIZED["authorization_source"]


def test_the_redis_history_still_records_both_transitions(switch, backend):
    """The operational view is retained, unchanged. It is simply no longer the record."""
    _run(switch.activate("because of X", triggered_by="reconciliation_engine"))
    _run(switch.deactivate(
        "X resolved", acknowledge_holds=["reconciliation_engine"], **AUTHORIZED
    ))

    history = _run(switch.get_history(limit=10))
    assert len(history) == 2, f"expected two history entries, got {history}"
    deactivation, activation = history[0], history[1]
    assert "ACTIVATE" in activation and "because of X" in activation
    assert "DEACTIVATE" in deactivation and "X resolved" in deactivation


# ══════════════════════════════════════════════════════════════════════════
#  5. CONCURRENCY, RESTART, MULTIPLE WORKERS
# ══════════════════════════════════════════════════════════════════════════


def test_a_concurrent_reset_is_refused_rather_than_interleaved(switch, backend):
    """Race protection. A reset that reads the hold set before a concurrent activation
    registers its hold would clear a halt it never saw.

    The transition lock is taken with `SET NX EX`, one atomic operation, so holding it is
    simulated here by taking it directly --- which is what a second process doing a
    transition looks like from this one's point of view.
    """
    _run(switch.activate("first halt", triggered_by="operator:ops"))

    # Another process is mid-transition.
    _run(backend.set(switch.TRANSITION_LOCK_KEY, "1", nx=True, ex=15))

    with pytest.raises(KillSwitchResetRefused) as excinfo:
        _run(switch.deactivate(
            "racing reset", acknowledge_holds=["operator:ops"], **AUTHORIZED
        ))

    assert excinfo.value.code == "RESET_CONTENDED"
    assert _run(switch.is_active()) is True


def test_activation_is_not_refused_by_a_contended_lock(switch, backend):
    """The asymmetry again, and it matters under contention.

    A reset refuses when it cannot serialise. An activation proceeds: refusing to halt
    because a lock was busy would make an emergency control unavailable at exactly the
    wrong moment.
    """
    _run(backend.set(switch.TRANSITION_LOCK_KEY, "1", nx=True, ex=15))

    _run(switch.activate("urgent halt during contention", triggered_by="health_monitor"))

    assert _run(switch.is_active()) is True


def test_two_simultaneous_activations_both_register_their_holds(switch, backend):
    """Simultaneous activation. Two subsystems halting at once must both be recorded, or
    a later reset would clear a halt that nobody acknowledged."""
    second = GlobalKillSwitch()

    async def both():
        await asyncio.gather(
            switch.activate("venue unhealthy", triggered_by="health_monitor"),
            second.activate("mismatch", triggered_by="reconciliation_engine"),
        )

    _run(both())

    assert _run(switch.get_active_holds()) == {"health_monitor", "reconciliation_engine"}
    assert _run(switch.is_active()) is True


def test_a_restarted_worker_still_sees_the_halt(switch, backend):
    """Worker restart. State must come from the backend, not from process memory.

    A fresh `GlobalKillSwitch` is what a restarted process constructs: `__init__` sets
    `_local_fallback_active = False` and `_last_state = False`, so if the halt lived in
    memory the new instance would permit trading.
    """
    _run(switch.activate("halted before the restart", triggered_by="operator:ops"))

    restarted = GlobalKillSwitch()
    assert restarted._local_fallback_active is False, "premise: a fresh instance is unlatched"
    assert _run(restarted.is_active()) is True, (
        "a restarted worker did not observe the halt, so the state is in process memory "
        "rather than in the shared backend"
    )


def test_a_restarted_worker_also_sees_the_outstanding_holds(switch, backend):
    """...and can therefore refuse a reset it should refuse."""
    _run(switch.activate("a", triggered_by="health_monitor"))
    _run(switch.activate("b", triggered_by="reconciliation_engine"))

    restarted = GlobalKillSwitch()
    assert _run(restarted.get_active_holds()) == {
        "health_monitor", "reconciliation_engine"
    }


def test_multiple_workers_agree_only_through_the_shared_backend(switch, backend):
    """Multiple workers. Three independent instances, one backend."""
    workers = [GlobalKillSwitch() for _ in range(3)]
    assert [_run(w.is_active()) for w in workers] == [False, False, False]

    _run(switch.activate("platform halt", triggered_by="operator:ops"))

    assert [_run(w.is_active()) for w in workers] == [True, True, True], (
        "not every worker observed the halt"
    )

    _run(switch.deactivate(
        "resolved", acknowledge_holds=["operator:ops"], **AUTHORIZED
    ))
    assert [_run(w.is_active()) for w in workers] == [False, False, False]


def test_every_worker_refuses_at_its_own_venue_boundary_during_a_halt(switch, backend):
    """The multi-worker case carried all the way to the oracle.

    Three executors, each its own recorder --- i.e. three worker processes each holding a
    fully authorised order. One halt, three refusals, zero venue calls.
    """
    import backend_app.core.global_safety as gs

    exchanges = [RecordingExchange() for _ in range(3)]
    executors = [build_executor(x) for x in exchanges]
    for ex in executors:
        arm_for_venue(ex)

    gs._global_kill_switch = switch
    _run(switch.activate("platform halt", triggered_by="operator:ops"))

    for ex in executors:
        with pytest.raises(ExecutionBlocked):
            _run(place(ex))

    assert all(x.calls == [] for x in exchanges), (
        f"an order reached a venue during the halt: "
        f"{[x.calls for x in exchanges]}"
    )


def test_the_state_store_being_unavailable_blocks_rather_than_permits(switch, backend):
    """Unavailable state storage, carried to the oracle.

    `is_active()` engages its latch and returns True on any backend error, so an
    unreadable halt state refuses the order. Paired with the negative control at the top
    of this file, which proves the same order succeeds when the state is readable and off.
    """
    import backend_app.core.global_safety as gs

    exchange = RecordingExchange()
    executor = build_executor(exchange)
    arm_for_venue(executor)
    gs._global_kill_switch = switch

    backend.fail = True

    with pytest.raises(ExecutionBlocked):
        _run(place(executor))

    assert exchange.calls == [], (
        "an order reached the venue while the authoritative halt state was unreadable"
    )


def test_the_halt_does_not_block_cancelling_a_resting_order(switch, backend):
    """The documented emergency policy, pinned.

    This platform's policy --- already asserted by
    `test_trade02_kill_switch_fails_closed.py::
    test_activation_refuses_new_orders_and_does_not_flatten` --- is to refuse NEW orders
    and leave resting orders and open positions alone. `cancel_order` is therefore
    deliberately NOT gated, so an operator can still withdraw working orders during a
    halt. A position-reducing order IS blocked, because it is a new order; that is a
    consequence of the existing policy and is recorded in TRADING_SAFETY_AUDIT.md.
    """
    import backend_app.core.global_safety as gs

    exchange = RecordingExchange()
    executor = build_executor(exchange)
    gs._global_kill_switch = switch
    _run(switch.activate("halted", triggered_by="operator:ops"))

    result = _run(executor.cancel_order("venue-order-1", SYMBOL))

    assert any(c[0] == "cancel" for c in exchange.calls), (
        "cancelling a resting order was blocked by the halt. That contradicts the "
        "documented policy (refuse new orders; leave existing exposure alone) and would "
        "deny an operator the one risk-reducing action the policy does permit."
    )
    assert result is not None


# ══════════════════════════════════════════════════════════════════════════
#  6. THE DURABLE AUDIT TRAIL (PostgreSQL)
# ══════════════════════════════════════════════════════════════════════════
#
# EXPLICIT DATABASE REQUIREMENT. Gated on AERORA_TEST_DATABASE_URL naming PostgreSQL --
# the same mechanism tests/test_money04_*.py and tests/test_trade01_*.py use. There is
# deliberately NO fallback to staging/pg_dsn.txt: Phase 8 measured that such a fallback
# made tests/test_tenant02_*.py run in BOTH lanes, so the CI-comparable lane was
# CI-comparable rather than CI-identical. A clean CI runner must skip these, visibly.

_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")

pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. "
        "A SKIP here is NOT a pass -- the durable audit trail is unverified."
    ),
)


def test_the_audit_table_requirement_is_explicit():
    """The gate itself, so a skip can never be silent.

    Asserts two things: the PostgreSQL requirement is read from the environment, and this
    module contains no CODE that resolves a database through a local file. The second is
    the CI-reproducibility finding -- Phase 8 measured that
    `tests/test_tenant02_fee_records_rls_postgres.py` falls back to a local DSN file and
    therefore ran in BOTH lanes, so the CI-comparable lane was CI-comparable rather than
    CI-identical.

    AST-BASED, AND THAT IS NOT INCIDENTAL. The first version of this test scanned its own
    source TEXT for "pg_dsn.txt" and failed -- on the comment above explaining why the
    fallback is forbidden. That is the same substring-versus-code error that produced
    three false "premise gone" verdicts in Phase 8's open-finding verifier and four false
    positives in the MONEY-04 remediation. Comments and docstrings are not AST nodes, so
    parsing the tree lets the prose name the forbidden thing freely while still asserting
    that no executable statement does it.
    """
    import ast

    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    env_reads = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) in {"get", "environ"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert "AERORA_TEST_DATABASE_URL" in env_reads, (
        "the PostgreSQL requirement is no longer read from AERORA_TEST_DATABASE_URL, so "
        f"the gate is not the explicit one. Environment reads found: {sorted(env_reads)}"
    )

    # Docstrings ARE Constant nodes, so they are excluded explicitly rather than by
    # hoping the walk skips them.
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)

    # EXCLUDE THIS FUNCTION'S OWN SUBTREE. A detector that scans the whole module
    # including itself always matches, because the needle it searches for is a literal in
    # its own body -- which is exactly what the second version of this test did. The
    # subject is the suite's database-resolution code, not the assertion machinery looking
    # at it.
    me = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and n.name == "test_the_audit_table_requirement_is_explicit"
    )
    own_lines = range(me.lineno, (me.end_lineno or me.lineno) + 1)

    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
        and getattr(node, "lineno", -1) not in own_lines
    ]
    offenders = [lit for lit in literals if "pg_dsn" in lit]
    assert offenders == [], (
        "this suite resolves a database through a local DSN file, which makes the "
        "CI-comparable lane depend on a file a clean CI runner does not have and stop "
        f"representing a clean CI environment. Offending literals: {offenders}"
    )


@pg_only
@pytest.mark.realaudit
def test_an_activation_lands_a_durable_row_visible_to_another_connection():
    """`record_safety_transition` commits, and an INDEPENDENT connection can see it.

    The oracle is a separate psycopg2 connection, not the ORM session that wrote the row:
    "it was recorded" has to mean committed and visible elsewhere, not merely staged.
    """
    import psycopg2

    from backend_app.core.safety_audit import (ACTION_ACTIVATE,
                                               CONTROL_GLOBAL_KILL_SWITCH,
                                               OUTCOME_APPLIED,
                                               record_safety_transition)

    marker = f"phase9-probe-{uuid.uuid4().hex[:12]}"
    operator = str(uuid.uuid4())

    audit_id = _run(record_safety_transition(
        control=CONTROL_GLOBAL_KILL_SWITCH,
        action=ACTION_ACTIVATE,
        outcome=OUTCOME_APPLIED,
        reason=marker,
        triggered_by="operator:probe",
        operator_id=operator,
        operator_email="probe@example.invalid",
        authorization_source="fastapi:get_operator_user",
        detail={"probe": True},
    ))
    assert audit_id, "the durable audit write returned no id"

    conn = psycopg2.connect(_PG_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT control, action, outcome, reason, operator_id::text, "
                "       operator_email, triggered_by, authorization_source, detail "
                "  FROM public.safety_control_audit WHERE audit_id = %s",
                (audit_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None, "the audit row is not visible to an independent connection"
    assert row[0] == CONTROL_GLOBAL_KILL_SWITCH
    assert row[1] == ACTION_ACTIVATE
    assert row[2] == OUTCOME_APPLIED
    assert row[3] == marker
    assert row[4] == operator
    assert row[7] == "fastapi:get_operator_user"
    assert row[8] == {"probe": True}, "detail did not round-trip as queryable jsonb"


@pg_only
@pytest.mark.realaudit
def test_the_audit_row_carries_no_expiry_and_a_server_timestamp():
    """Retention is permanent, and the timestamp is the server's.

    No duration is declared anywhere, deliberately: the repository contains no
    authoritative retention policy for safety audit records (every statement found is
    relative), and permanence is the only choice consistent with its two absolute
    statements about financial and audit rows. See
    backend_app/migrations/028_safety_control_audit.sql's header.
    """
    import psycopg2

    conn = psycopg2.connect(_PG_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_default, is_nullable FROM information_schema.columns "
                " WHERE table_schema='public' AND table_name='safety_control_audit' "
                "   AND column_name='recorded_at'"
            )
            default, nullable = cur.fetchone()
            assert nullable == "NO"
            assert "now()" in (default or ""), (
                "recorded_at has no now() default, so an audit row could be undated"
            )

            # No TTL mechanism: no expiry column, and nothing scheduled to delete.
            cur.execute(
                "SELECT count(*) FROM information_schema.columns "
                " WHERE table_schema='public' AND table_name='safety_control_audit' "
                "   AND column_name IN ('expires_at','ttl','retention_until')"
            )
            assert cur.fetchone()[0] == 0, (
                "an expiry column appeared on the safety audit trail; retention is "
                "permanent by decision"
            )
    finally:
        conn.close()


@pg_only
@pytest.mark.realaudit
def test_the_trail_is_append_only_for_the_backend_role():
    """An audit trail its own writer can rewrite is not evidence."""
    import psycopg2

    conn = psycopg2.connect(_PG_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                " WHERE table_schema='public' AND table_name='safety_control_audit' "
                "   AND grantee='service_role' ORDER BY privilege_type"
            )
            granted = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()

    assert "SELECT" in granted and "INSERT" in granted, (
        f"service_role cannot append to or read the safety audit trail: {granted}"
    )
    assert "UPDATE" not in granted and "DELETE" not in granted, (
        f"service_role can rewrite the safety audit trail: {granted}"
    )


@pg_only
@pytest.mark.realaudit
def test_no_browser_role_can_read_the_halt_reasons():
    """Halt reasons name the incident that caused them. Not customer-facing."""
    import psycopg2

    conn = psycopg2.connect(_PG_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT grantee, privilege_type "
                "  FROM information_schema.role_table_grants "
                " WHERE table_schema='public' AND table_name='safety_control_audit' "
                "   AND grantee IN ('anon','authenticated')"
            )
            leaked = cur.fetchall()

            cur.execute(
                "SELECT relrowsecurity FROM pg_class c "
                "  JOIN pg_namespace n ON n.oid = c.relnamespace "
                " WHERE n.nspname='public' AND c.relname='safety_control_audit'"
            )
            rls_on = cur.fetchone()[0]
    finally:
        conn.close()

    assert leaked == [], f"a browser role holds privileges on the safety audit trail: {leaked}"
    assert rls_on is True, "row level security is not enabled on safety_control_audit"


@pg_only
@pytest.mark.realaudit
def test_a_blank_reason_is_refused_by_the_database():
    """The endpoint already refuses an empty reason; the constraint makes it true of every
    writer rather than of one caller."""
    import psycopg2

    conn = psycopg2.connect(_PG_URL)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.CheckViolation):
                cur.execute(
                    "INSERT INTO public.safety_control_audit "
                    "       (control, action, outcome, reason, triggered_by) "
                    "VALUES ('global_kill_switch','ACTIVATE','APPLIED','   ','probe')"
                )
    finally:
        conn.close()


@pg_only
@pytest.mark.realaudit
def test_an_unknown_action_is_refused_by_the_database():
    import psycopg2

    conn = psycopg2.connect(_PG_URL)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.errors.CheckViolation):
                cur.execute(
                    "INSERT INTO public.safety_control_audit "
                    "       (control, action, outcome, reason, triggered_by) "
                    "VALUES ('global_kill_switch','WIPE','APPLIED','probe','probe')"
                )
    finally:
        conn.close()


def test_no_source_file_updates_or_deletes_the_safety_audit_trail():
    """The owner-side append-only guarantee.

    The GRANT in migration 028 binds a non-owner connection; it cannot bind the table
    OWNER, which is how the application connects today. The owner-side guarantee is
    therefore the absence of any UPDATE or DELETE statement against this table in the
    codebase, which is what this asserts. Stated in 028's own comment as the reason this
    test exists.
    """
    offenders: List[str] = []
    for root_name in ("backend_app", "scripts"):
        root = _REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for path in list(root.rglob("*.py")) + list(root.rglob("*.sql")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "safety_control_audit" not in text:
                continue
            lowered = text.lower()
            for verb in ("update public.safety_control_audit",
                         "delete from public.safety_control_audit",
                         "truncate public.safety_control_audit",
                         "drop table public.safety_control_audit"):
                if verb in lowered:
                    offenders.append(f"{path.relative_to(_REPO_ROOT).as_posix()}: {verb}")

    assert offenders == [], (
        "the safety audit trail is mutated by application source, so it is not evidence:\n  "
        + "\n  ".join(offenders)
    )
