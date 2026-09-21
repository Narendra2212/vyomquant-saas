"""
tests/test_settlement_service.py

Unit tests for ``backend_app/backend/marketplace/settlement_service.py`` (task 19.1) - the one
Settlement_Record writer and the only path that may set ``library_subscriptions.status='active'``.

Spec: marketplace-subscriptions-paper-trading. Requirements 9.5, 9.6, 9.14, 10.1, 10.2, 10.3,
10.4, 10.8, 10.9, 10.10, 10.11, 11.4, 11.5, 11.6, 11.12, 24.6.

Everything runs against a FAKE Supabase double that keeps mutable in-memory
``library_subscriptions``, ``marketplace_settlements``, ``library_subscription_transitions`` and
``deployment_permissions`` tables and **enforces** ``uq_settlement_reference_reversal`` - the same
dependency-injection pattern ``tests/test_checkout_service.py`` and
``tests/test_expiry_sweep.py`` use, so the whole settlement path is checkable without a database,
a TestClient, a payment SDK or a network call. The double enforces the unique constraint rather
than merely recording the insert, because a double that accepted every insert would make the
duplicate-delivery assertions pass against a module with no idempotency at all.

WHAT THIS FILE PINS
-------------------
1. **The two guards write nothing** (Requirement 9.14). An unmatched ``subscription_id`` and a
   mismatched amount or currency each make no transition, write no Settlement_Record, grant no
   entitlement, and are answered by an audit line rather than an exception.
2. **The split conserves exactly** (Requirements 10.1, 10.2, 10.3) at 0, 1, 99, 100, 101 and
   ``money.MAX_AMOUNT_MINOR``, and the persisted components are the ones
   ``money.split_ninety_ten`` returns rather than a second local computation. Reinforced by an
   AST walk asserting the module contains no ``float`` call, no ``float`` literal and no
   reference to the name at all.
3. **Duplicate delivery is a no-op** (Requirements 9.6, 10.10, property P-6): ``n >= 1``
   deliveries of one provider reference leave exactly one ledger row, exactly one period
   extension, exactly one history row and exactly one entitlement.
4. **The write order puts the money first** (Requirement 9.5): the ledger row is written before
   any entitlement is granted, and a step that never completes grants none.
5. **A reversal adds a row and mutates nothing** (Requirement 10.8): no UPDATE and no DELETE is
   ever issued against ``marketplace_settlements``, and the original row is unchanged byte for
   byte.
6. **The retry budget is 5 attempts inside 60 seconds** (Requirement 10.11), after which the
   ledger is unchanged and ``MARKETPLACE_SETTLEMENT_PERSIST_FAILED`` carries the provider
   reference.
7. **The entitlement grant carries the period expiry, never ``None``.** ``expires_at`` is a
   required keyword here and ``None`` is refused with a ``ValueError``, so the null-expiry
   privilege defect is unrepresentable on this path rather than merely unexercised by it - which
   is what these assertions pin. The defect was real:
   ``routers/library.grant_deployment_permission`` wrote ``"expires_at": None`` unconditionally
   and took no expiry parameter, and ``check_deployment_permission`` read a null expiry as a
   perpetual subscription, so every grant it wrote was permanent access to a monthly Listing.
   Task 19.3 deleted that function together with its only caller, so this module's is now the
   codebase's only ``grant_deployment_permission`` - the assertions below are the reason the
   surviving one is the safe one, not a comparison against a live alternative.
8. **Currencies are never combined** (Requirement 10.7): each ledger row carries its own
   currency, unconverted, and a confirmation in the wrong currency is refused outright.

WHY ``_run_coroutine`` AND NOT ``asyncio.run``
----------------------------------------------
Lifted from ``tests/test_marketplace_checkout_regression.py`` for the reason recorded there:
``asyncio.run`` closes its loop and leaves the main thread with *no* current loop, and other
suites in this repository still call the deprecated
``asyncio.get_event_loop().run_until_complete(...)`` - which then raises ``RuntimeError: There is
no current event loop in thread 'MainThread'``. A test file that breaks a module collected after
it is a new regression, not a guard against one.

WHAT IS DELIBERATELY NOT ASSERTED HERE
--------------------------------------
* A single atomic commit across the four writes. The Persistence_Layer this module talks to
  offers no transaction handle and the module says so; what is asserted instead is the ordering
  that makes the intermediate states safe (the money is recorded before any entitlement) and the
  fact that a step which never completes grants nothing. See
  :func:`test_a_step_after_the_ledger_row_that_never_completes_grants_no_entitlement` for the
  honest boundary and the discrepancy it records.
* The earnings totals of Requirement 10.7 - a *read* over this ledger, and task 21's.
* The webhook plumbing (task 19.2) and the router's ``renew_subscription`` deletion (task 19.3).
"""

from __future__ import annotations

import ast
import asyncio
import atexit
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from backend_app.backend.marketplace import COLUMN_CONTRACT
from backend_app.backend.marketplace import money
from backend_app.backend.marketplace import settlement_service as ss
from backend_app.backend.marketplace.settlement_service import (
    MAX_SETTLEMENT_ATTEMPTS,
    SETTLEMENT_BACKOFF_SECONDS,
    SETTLEMENT_REFERENCE_UNIQUE_CONSTRAINT,
    SETTLEMENT_RETRY_WINDOW_SECONDS,
    SETTLEMENT_SUBSCRIPTION_SELECT,
    SettlementOutcome,
    SettlementPersistFailed,
    grant_deployment_permission,
    settle,
)
from backend_app.backend.marketplace.subscription_period import (
    period_for_activation,
    period_for_renewal,
)
from backend_app.backend.marketplace.subscription_state import (
    STATUS_TEXT_FOR_STATE,
    SubscriptionState,
    can_transition,
)

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend_app"
    / "backend"
    / "marketplace"
    / "settlement_service.py"
)

SUBSCRIPTION_ID = "sub-1"
LISTING_ID = "listing-1"
PURCHASER_ID = "purchaser-1"
OWNER_ID = "owner-9"
REFERENCE = "pi_live_abc123"
PROVIDER = "stripe"

#: A $19.99 Listing, the amount ``tests/test_checkout_service.py`` also uses - so the two halves
#: of the payment path are asserted against the same figures.
AMOUNT_1999 = 1999
OWNER_SHARE_1999 = 1799
PLATFORM_FEE_1999 = 200

CONFIRMED_AT = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# The loop runner (see the module docstring)
# ══════════════════════════════════════════════════════════════════════════


#: This runner's loop, built on first use and reused for the rest of the process.
#:
#: ONE LOOP, NOT ONE PER CALL
#: --------------------------
#: On Windows the default loop is ``ProactorEventLoop``, whose ``_make_self_pipe`` calls
#: ``socket.socketpair()``; Windows has no ``AF_UNIX`` socketpair, so CPython falls back to
#: ``socket._fallback_socketpair``, which binds a listener on ``127.0.0.1``, connects to it and
#: accepts. A loop is therefore two real loopback TCP connections, and closing it parks their
#: ephemeral ports in ``TIME_WAIT`` for minutes, machine-wide, out of a range of 16384.
#:
#: ``tests/property/test_settlement_ledger.py``, ``test_settlement_idempotence.py`` and
#: ``test_subscription_state_machine.py`` import this runner and call it thousands of times per
#: session, so a loop per call consumed the ephemeral range faster than ``TIME_WAIT`` released it -
#: and once it is gone the handshake inside ``_fallback_socketpair`` blocks with no output and no
#: CPU. That is the shape of the hang diagnosed in
#: ``tests/test_paper_order_lifecycle_writes._run_coroutine``; this is the same defect and the same
#: fix. One loop for the process makes it two ports for the process.
_RUNNER_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _runner_loop() -> asyncio.AbstractEventLoop:
    """This runner's loop, created once. Rebuilt only if something has closed it."""
    global _RUNNER_LOOP
    loop = _RUNNER_LOOP
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _RUNNER_LOOP = loop
    return loop


@atexit.register
def _close_the_runner_loop() -> None:
    """Release the loop's two sockets at process exit."""
    global _RUNNER_LOOP
    loop, _RUNNER_LOOP = _RUNNER_LOOP, None
    if loop is not None and not loop.is_closed():
        loop.close()


def _run_coroutine(coro: Any) -> Any:
    """Run ``coro`` to completion **without leaving the thread without an event loop**.

    The loop is this module's one loop rather than a fresh one per call - see :data:`_RUNNER_LOOP`.
    The contract the docstring names is unchanged and is now met without minting anything: whatever
    loop was current is restored afterwards, and when there was none (or a closed one) this
    runner's loop is *left installed*, which is what "not leaving the thread without an event loop"
    asks for. Previously that branch installed a brand-new loop instead, so the common path built
    two loops per call and closed one of them.
    """
    previous: Optional[asyncio.AbstractEventLoop]
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        previous = None

    loop = _runner_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        if previous is not None and previous is not loop and not previous.is_closed():
            asyncio.set_event_loop(previous)


# ══════════════════════════════════════════════════════════════════════════
# A Supabase double that enforces uq_settlement_reference_reversal
# ══════════════════════════════════════════════════════════════════════════


class FakeUniqueViolation(Exception):
    """What a driver raises for ``23505``.

    Both spellings a driver may surface are present in the text - the constraint name and the
    SQLSTATE - because ``settlement_service._is_duplicate_reference`` matches on either and the
    point of the double is to exercise that reading, not to assume it.
    """

    def __init__(self, constraint: str, table: str) -> None:
        self.pgcode = "23505"
        super().__init__(
            f'duplicate key value violates unique constraint "{constraint}" on {table} (23505)'
        )


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    """A recording query builder mimicking the supabase-py fluent chain."""

    def __init__(self, table: str, client: "FakeSupabase") -> None:
        self.table_name = table
        self.client = client
        self.op: str = "select"
        self.cols: Optional[str] = None
        self.payload: Optional[Dict[str, Any]] = None
        self.filters: List[Tuple[str, Any]] = []

    def select(self, cols: str) -> "_Query":
        self.op = "select"
        self.cols = cols
        return self

    def insert(self, payload: Dict[str, Any]) -> "_Query":
        self.op = "insert"
        self.payload = dict(payload)
        return self

    def update(self, payload: Dict[str, Any]) -> "_Query":
        self.op = "update"
        self.payload = dict(payload)
        return self

    def delete(self) -> "_Query":
        self.op = "delete"
        return self

    def eq(self, col: str, val: Any) -> "_Query":
        self.filters.append((col, val))
        return self

    def execute(self) -> Any:
        return self.client._execute(self)


class FakeSupabase:
    """Four mutable tables, the settlement unique constraint, and injectable failures."""

    def __init__(
        self,
        *,
        subscriptions: Optional[List[Dict[str, Any]]] = None,
        settlements: Optional[List[Dict[str, Any]]] = None,
        raise_on: Optional[set] = None,
        error_on: Optional[set] = None,
        fail_times: Optional[Dict[Tuple[str, str], int]] = None,
    ) -> None:
        self.subscriptions: List[Dict[str, Any]] = [dict(r) for r in (subscriptions or [])]
        self.settlements: List[Dict[str, Any]] = [dict(r) for r in (settlements or [])]
        self.transitions: List[Dict[str, Any]] = []
        self.permissions: List[Dict[str, Any]] = []
        #: ``(op, table)`` pairs that raise every time.
        self.raise_on = set(raise_on or set())
        #: ``(op, table)`` pairs that "complete" carrying a PostgREST error envelope.
        self.error_on = set(error_on or set())
        #: ``(op, table)`` -> how many of the first N calls raise; later calls succeed.
        self.fail_times: Dict[Tuple[str, str], int] = dict(fail_times or {})
        #: ``(op, table)`` in execution order, plus ``("audit", <action name>)`` entries.
        self.ops: List[Tuple[str, str]] = []
        self.statements: List[_Query] = []

    # ---- the supabase-py surface this module uses ------------------------
    def table(self, name: str) -> _Query:
        return _Query(name, self)

    # ---- recording ------------------------------------------------------
    def note_audit(self, action_name: str) -> None:
        self.ops.append(("audit", action_name))

    def note_grant(self) -> None:
        self.ops.append(("grant", "injected"))

    def _execute(self, q: _Query) -> Any:
        key = (q.op, q.table_name)
        self.ops.append(key)
        self.statements.append(q)

        remaining = self.fail_times.get(key, 0)
        if remaining:
            self.fail_times[key] = remaining - 1
            raise RuntimeError(f"{q.op} on {q.table_name} did not complete")
        if key in self.raise_on:
            raise RuntimeError(f"{q.op} on {q.table_name} did not complete")
        if key in self.error_on:
            return {"data": None, "error": {"message": f"boom on {q.table_name}"}}

        if q.table_name == ss.SUBSCRIPTION_TABLE:
            if q.op == "select":
                return _Resp([copy.deepcopy(r) for r in self._matching(self.subscriptions, q)])
            if q.op == "update":
                touched = self._matching(self.subscriptions, q)
                for row in touched:
                    row.update(q.payload or {})
                return _Resp([copy.deepcopy(r) for r in touched])

        if q.table_name == ss.SETTLEMENT_TABLE:
            if q.op == "select":
                # The ledger read ``find_settled_payment`` issues (task 19.16). Filtered the way
                # PostgREST filters, including ``.eq("is_reversal", False)``.
                return _Resp([copy.deepcopy(r) for r in self._matching(self.settlements, q)])
            if q.op == "insert":
                row = dict(q.payload or {})
                # uq_settlement_reference_reversal UNIQUE (provider_reference, is_reversal).
                pair = (row.get("provider_reference"), bool(row.get("is_reversal")))
                for existing in self.settlements:
                    if (
                        existing.get("provider_reference"),
                        bool(existing.get("is_reversal")),
                    ) == pair:
                        raise FakeUniqueViolation(
                            SETTLEMENT_REFERENCE_UNIQUE_CONSTRAINT, ss.SETTLEMENT_TABLE
                        )
                self.settlements.append(row)
                return _Resp([dict(row)])

        if q.table_name == ss.TRANSITION_TABLE and q.op == "insert":
            self.transitions.append(dict(q.payload or {}))
            return _Resp([dict(q.payload or {})])

        if q.table_name == ss.PERMISSION_TABLE and q.op == "insert":
            row = dict(q.payload or {})
            row.setdefault("id", f"perm-{len(self.permissions) + 1}")
            self.permissions.append(row)
            return _Resp([dict(row)])

        return _Resp([])

    @staticmethod
    def _matching(rows: List[Dict[str, Any]], q: _Query) -> List[Dict[str, Any]]:
        matched = rows
        for col, val in q.filters:
            matched = [r for r in matched if str(r.get(col)) == str(val)]
        return matched

    # ---- assertion helpers ----------------------------------------------
    def statements_on(self, table: str, *ops: str) -> List[_Query]:
        wanted = set(ops) or None
        return [
            s
            for s in self.statements
            if s.table_name == table and (wanted is None or s.op in wanted)
        ]

    def wrote_anything(self) -> bool:
        """Whether ANY statement other than a read was issued, on any table."""
        return any(s.op in {"insert", "update", "delete"} for s in self.statements)

    def subscription(self, row_id: str = SUBSCRIPTION_ID) -> Dict[str, Any]:
        for row in self.subscriptions:
            if str(row.get("id")) == str(row_id):
                return row
        raise AssertionError(f"no subscription row {row_id!r}")


def _subscription_row(
    *,
    row_id: str = SUBSCRIPTION_ID,
    status: str = "pending",
    price_minor: Optional[int] = AMOUNT_1999,
    currency: str = "USD",
    period_start: Optional[str] = None,
    period_expiry: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": row_id,
        "library_id": LISTING_ID,
        "user_id": PURCHASER_ID,
        "owner_id": OWNER_ID,
        "status": status,
        "price_minor": price_minor,
        "currency": currency,
        "period_start": period_start,
        "period_expiry": period_expiry,
        "provider": PROVIDER,
        "provider_reference": REFERENCE,
    }


# ══════════════════════════════════════════════════════════════════════════
# The audit double
# ══════════════════════════════════════════════════════════════════════════


class RecordingAuditLogger:
    """Records every audit act, and can be made to fail.

    Exposes both ``record_or_raise`` and ``log`` because ``_write_audit`` chooses between them by
    whether the act is required - and which one it chose is itself an assertion this suite makes
    (the give-up path must not be able to fail).
    """

    def __init__(self, client: Optional[FakeSupabase] = None, *, raises: bool = False) -> None:
        self.client = client
        self.raises = raises
        #: ``(method, action_name, kwargs)`` per act.
        self.acts: List[Tuple[str, str, Dict[str, Any]]] = []

    async def record_or_raise(self, action: Any, **kwargs: Any) -> Any:
        return self._note("record_or_raise", action, kwargs)

    async def log(self, action: Any, **kwargs: Any) -> Any:
        return self._note("log", action, kwargs)

    def _note(self, method: str, action: Any, kwargs: Dict[str, Any]) -> Any:
        name = getattr(action, "name", str(action))
        self.acts.append((method, name, dict(kwargs)))
        if self.client is not None:
            self.client.note_audit(name)
        if self.raises:
            raise RuntimeError("the audit sink is down")
        return object()

    # ---- assertion helpers ----------------------------------------------
    def action_names(self) -> List[str]:
        return [name for _, name, _ in self.acts]

    def metadata_for(self, action_name: str) -> Dict[str, Any]:
        for _, name, kwargs in self.acts:
            if name == action_name:
                return dict(kwargs.get("metadata") or {})
        raise AssertionError(
            f"no {action_name} audit entry; recorded: {self.action_names()}"
        )

    def method_for(self, action_name: str) -> str:
        for method, name, _ in self.acts:
            if name == action_name:
                return method
        raise AssertionError(
            f"no {action_name} audit entry; recorded: {self.action_names()}"
        )


class RecordingGrant:
    """The injected entitlement writer. Records the expiry every grant received."""

    def __init__(self, client: Optional[FakeSupabase] = None, *, raises: bool = False) -> None:
        self.client = client
        self.raises = raises
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, supabase: Any, **kwargs: Any) -> Optional[str]:
        self.calls.append(dict(kwargs))
        if self.client is not None:
            self.client.note_grant()
        if self.raises:
            raise RuntimeError("the entitlement write did not complete")
        return f"perm-{len(self.calls)}"

    @property
    def expiries(self) -> List[Any]:
        return [call.get("expires_at") for call in self.calls]


@pytest.fixture()
def audit(monkeypatch: pytest.MonkeyPatch) -> RecordingAuditLogger:
    """Install a recording audit logger in place of the real Redis-backed one.

    ``settlement_service._write_audit`` imports ``get_strategy_audit_logger`` lazily from
    ``backend_app.core.audit_trail`` at call time, so patching the module attribute is enough -
    and the real ``StrategyAuditAction`` enum still resolves the five action names, which is what
    proves they exist rather than assuming it.
    """
    from backend_app.core import audit_trail

    recorder = RecordingAuditLogger()
    monkeypatch.setattr(audit_trail, "get_strategy_audit_logger", lambda: recorder)
    return recorder


def _settle(
    client: FakeSupabase,
    *,
    amount_minor: int = AMOUNT_1999,
    currency: str = "USD",
    reference: str = REFERENCE,
    subscription_id: str = SUBSCRIPTION_ID,
    instant: datetime = CONFIRMED_AT,
    is_reversal: bool = False,
    reverses_reference: Optional[str] = None,
    grant: Any = None,
    actor_id: Optional[str] = None,
    max_attempts: int = MAX_SETTLEMENT_ATTEMPTS,
    retry_window_seconds: int = SETTLEMENT_RETRY_WINDOW_SECONDS,
) -> Any:
    return _run_coroutine(
        settle(
            provider_reference=reference,
            provider=PROVIDER,
            amount_minor=amount_minor,
            currency=currency,
            subscription_id=subscription_id,
            confirmation_instant=instant,
            supabase=client,
            is_reversal=is_reversal,
            reverses_reference=reverses_reference,
            actor_id=actor_id,
            grant_permission=grant,
            max_attempts=max_attempts,
            retry_window_seconds=retry_window_seconds,
        )
    )


@pytest.fixture()
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> List[int]:
    """Record the backoff waits instead of serving them.

    A suite that actually waited the fifteen seconds the schedule specifies to prove a backoff
    exists is a suite nobody runs; the recorded schedule is what Requirement 10.11 is about, and
    it is asserted directly.
    """
    waits: List[int] = []
    real_sleep = asyncio.sleep

    async def _record(seconds: Any) -> None:
        waits.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(ss.asyncio, "sleep", _record)
    return waits


# ══════════════════════════════════════════════════════════════════════════
# 1. THE GUARDS WRITE NOTHING (Requirement 9.14)
# ══════════════════════════════════════════════════════════════════════════


def test_an_unmatched_confirmation_writes_nothing_at_all(audit: RecordingAuditLogger) -> None:
    """No Subscription for the confirmed ``subscription_id``: nothing is recorded against a guess.

    Requirement 9.14: no Subscription_State transition, no Settlement_Record, no entitlement,
    and an Audit_Log entry recording the unmatched confirmation. The audit line IS the response,
    so this is an ordinary return rather than an exception - the webhook has nothing to retry.
    """
    client = FakeSupabase(subscriptions=[])
    grant = RecordingGrant()

    result = _settle(client, grant=grant)

    assert result.outcome is SettlementOutcome.UNMATCHED
    assert not client.wrote_anything(), "an unmatched confirmation issued a write"
    assert client.settlements == []
    assert client.transitions == []
    assert client.permissions == []
    assert grant.calls == []

    assert audit.action_names() == ["MARKETPLACE_SETTLEMENT_UNMATCHED"]
    metadata = audit.metadata_for("MARKETPLACE_SETTLEMENT_UNMATCHED")
    assert metadata["provider_reference"] == REFERENCE
    assert metadata["subscription_id"] == SUBSCRIPTION_ID


@pytest.mark.parametrize(
    "recorded_amount, confirmed_amount, recorded_currency, confirmed_currency",
    [
        # The classic: a confirmation for an amount nobody quoted.
        (AMOUNT_1999, 1998, "USD", "USD"),
        (AMOUNT_1999, 199900, "USD", "USD"),
        # A partial refund's amount is by definition not the recorded amount.
        (AMOUNT_1999, 500, "USD", "USD"),
        # The currency, which is never converted (Requirement 10.7).
        (AMOUNT_1999, AMOUNT_1999, "USD", "INR"),
        # A Subscription that recorded no price agreed to nothing.
        (None, AMOUNT_1999, "USD", "USD"),
    ],
)
def test_a_mismatched_confirmation_writes_nothing_at_all(
    audit: RecordingAuditLogger,
    recorded_amount: Optional[int],
    confirmed_amount: int,
    recorded_currency: str,
    confirmed_currency: str,
) -> None:
    """Requirement 9.14: the confirmed amount or currency is not what the Subscription recorded.

    A webhook body is attacker-influenced in exactly this field. Recording what it claims would
    pay an owner 90 percent of a number this system never quoted, so nothing is written and the
    audit line names BOTH figures for the operator.
    """
    client = FakeSupabase(
        subscriptions=[
            _subscription_row(price_minor=recorded_amount, currency=recorded_currency)
        ]
    )
    grant = RecordingGrant()

    result = _settle(
        client, amount_minor=confirmed_amount, currency=confirmed_currency, grant=grant
    )

    assert result.outcome is SettlementOutcome.MISMATCHED
    assert not client.wrote_anything(), "a mismatched confirmation issued a write"
    assert client.settlements == []
    assert client.transitions == []
    assert client.permissions == []
    assert grant.calls == []
    # The Subscription is left exactly as it was - not re-priced, not moved.
    assert client.subscription()["status"] == "pending"
    assert client.subscription()["price_minor"] == recorded_amount

    assert audit.action_names() == ["MARKETPLACE_SETTLEMENT_MISMATCHED"]
    metadata = audit.metadata_for("MARKETPLACE_SETTLEMENT_MISMATCHED")
    assert metadata["confirmed_amount_minor"] == confirmed_amount
    assert metadata["confirmed_currency"] == confirmed_currency
    assert metadata.get("expected_amount_minor") == recorded_amount


def test_an_inadmissible_amount_is_refused_before_any_statement(
    audit: RecordingAuditLogger,
) -> None:
    """Property P-7: an amount outside Requirement 10.1's domain reaches no statement.

    The split is taken at the boundary, before the read, so a negative or over-maximum amount is
    a refusal rather than a row - and a ``float`` is refused by :mod:`money` rather than coerced
    (Requirement 10.3).
    """
    for bad in (-1, money.MAX_AMOUNT_MINOR + 1, 19.99, True):
        client = FakeSupabase(subscriptions=[_subscription_row()])
        with pytest.raises(money.InvalidAmount):
            _settle(client, amount_minor=bad)  # type: ignore[arg-type]
        assert client.statements == [], f"{bad!r} reached a statement"
        assert audit.acts == []


# ══════════════════════════════════════════════════════════════════════════
# 2. THE SPLIT CONSERVES EXACTLY (Requirements 10.1, 10.2, 10.3)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "amount",
    [
        0,                        # the bottom of the domain
        1,                        # the smallest non-zero amount: 0 + 1, all remainder
        99,                       # 89 + 10
        100,                      # 90 + 10, the only exact case among these
        101,                      # 90 + 11
        money.MAX_AMOUNT_MINOR,   # the top of Requirement 10.1's inclusive domain
    ],
)
def test_the_persisted_split_conserves_the_amount_exactly(
    audit: RecordingAuditLogger, amount: int
) -> None:
    """``owner_share + platform_fee == amount`` on the persisted row, for every amount.

    This is ``chk_settlement_conserved`` evaluated against the one row image the module is about
    to insert, and the components are asserted to be the ones ``money.split_ninety_ten`` returns
    - not a second local computation with its own rounding policy. Every value is an ``int``.
    """
    client = FakeSupabase(subscriptions=[_subscription_row(price_minor=amount)])
    grant = RecordingGrant()

    result = _settle(client, amount_minor=amount, grant=grant)

    assert result.outcome is SettlementOutcome.RECORDED
    assert len(client.settlements) == 1
    row = client.settlements[0]

    expected_owner, expected_fee = money.split_ninety_ten(amount)
    assert row["amount_minor"] == amount
    assert row["owner_share_minor"] == expected_owner
    assert row["platform_fee_minor"] == expected_fee
    assert row["owner_share_minor"] + row["platform_fee_minor"] == row["amount_minor"]

    # chk_settlement_amounts: all three non-negative.
    for column in ("amount_minor", "owner_share_minor", "platform_fee_minor"):
        assert isinstance(row[column], int) and not isinstance(row[column], bool)
        assert row[column] >= 0

    # And the result reports the same integers it persisted.
    assert (result.owner_share_minor, result.platform_fee_minor) == (
        expected_owner,
        expected_fee,
    )
    assert result.owner_share_minor + result.platform_fee_minor == result.amount_minor

    # The audit carries the amount and both components (Requirement 10.9).
    metadata = audit.metadata_for("MARKETPLACE_SETTLEMENT_CREATED")
    assert metadata["amount_minor"] == amount
    assert metadata["owner_share_minor"] == expected_owner
    assert metadata["platform_fee_minor"] == expected_fee


def test_the_module_delegates_the_split_and_never_recomputes_it() -> None:
    """The 90/10 arithmetic appears exactly once, in :mod:`money`.

    An AST walk for a local ``* 90``, ``// 100`` or ``* 0.9``: a second copy of the split here
    would be a second rounding policy nobody chose, and Requirement 10.2's conservation would
    stop holding by construction.
    """
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), str(MODULE_PATH))

    arithmetic = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp)
        and isinstance(node.op, (ast.Mult, ast.FloorDiv, ast.Div))
        and isinstance(node.right, ast.Constant)
        and node.right.value in (90, 100, 10)
    ]
    assert not arithmetic, (
        f"the 90/10 split looks recomputed at line(s) {arithmetic}; money.split_ninety_ten is "
        f"the one implementation (Requirements 10.1, 10.2)"
    )

    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "split_ninety_ten" in calls, "the module must call money.split_ninety_ten"


def test_module_contains_no_float_call_no_float_literal_and_no_float_name() -> None:
    """Requirement 10.3, structurally: no binary floating point on the settlement path.

    An AST walk rather than a text search, so ``float`` inside a docstring does not register and
    a genuine ``float(...)`` cannot hide behind formatting. The same assertion task 18.1's suite
    makes for ``checkout_service``.
    """
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), str(MODULE_PATH))

    float_names = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "float"
    ]
    assert not float_names, f"the name 'float' appears at line(s) {float_names}"

    float_literals = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert not float_literals, f"float literal(s) present: {float_literals}"

    # The backoff schedule and the two budgets are whole seconds, not fractions.
    assert all(isinstance(s, int) for s in SETTLEMENT_BACKOFF_SECONDS)
    assert isinstance(MAX_SETTLEMENT_ATTEMPTS, int)
    assert isinstance(SETTLEMENT_RETRY_WINDOW_SECONDS, int)


# ══════════════════════════════════════════════════════════════════════════
# 3. DUPLICATE DELIVERY: ONE RECORD, ONE PERIOD EXTENSION (Reqs 9.6, 10.10, P-6)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("deliveries", [2, 3, 5])
def test_n_deliveries_leave_exactly_one_record_and_exactly_one_period_extension(
    audit: RecordingAuditLogger, deliveries: int
) -> None:
    """Property P-6: ``uq_settlement_reference_reversal`` makes the redelivery a no-op.

    The Redis lock in ``routers/billing.py`` is the fast path; a Redis flush, an expired lock or
    a redelivery from a second process bypasses it, and at that point the constraint is the only
    thing between one payment and two period extensions. So a ``UniqueViolation`` on it is a
    no-op plus ``MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED`` - NOT an error.
    """
    client = FakeSupabase(subscriptions=[_subscription_row(status="pending")])
    grant = RecordingGrant()

    outcomes = [_settle(client, grant=grant) for _ in range(deliveries)]

    assert outcomes[0].outcome is SettlementOutcome.RECORDED
    for later in outcomes[1:]:
        assert later.outcome is SettlementOutcome.DUPLICATE_IGNORED, (
            "a redelivery must be an ordinary no-op outcome, not an exception"
        )

    # Exactly one ledger row, one history row, one entitlement.
    assert len(client.settlements) == 1
    assert len(client.transitions) == 1
    assert grant.calls and len(grant.calls) == 1

    # Exactly one period extension: the expiry after n deliveries is the one delivery 1 wrote.
    expected_start, expected_expiry = period_for_activation(CONFIRMED_AT)
    stored = client.subscription()
    assert stored["status"] == "active"
    assert stored["period_start"] == expected_start.isoformat()
    assert stored["period_expiry"] == expected_expiry.isoformat()
    assert outcomes[0].period_written is True
    for later in outcomes[1:]:
        assert later.period_written is False

    # One created audit, and one duplicate-ignored audit per redelivery.
    names = audit.action_names()
    assert names.count("MARKETPLACE_SETTLEMENT_CREATED") == 1
    assert names.count("MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED") == deliveries - 1
    duplicate_metadata = audit.metadata_for("MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED")
    assert duplicate_metadata["constraint"] == SETTLEMENT_REFERENCE_UNIQUE_CONSTRAINT
    assert duplicate_metadata["provider_reference"] == REFERENCE


def test_a_duplicate_delivery_extends_no_period_on_a_renewal_either(
    audit: RecordingAuditLogger,
) -> None:
    """The same guarantee for a renewal, where a second extension would be a free month.

    An ``expired`` Subscription with a stored expiry renews through
    :func:`period_for_renewal`, which extends from the later of the stored expiry and the
    confirmation instant (Requirement 11.5). A redelivery must leave that one new expiry alone.
    """
    stored_expiry = CONFIRMED_AT - timedelta(days=2)
    client = FakeSupabase(
        subscriptions=[
            _subscription_row(
                status="expired",
                period_start=(CONFIRMED_AT - timedelta(days=32)).isoformat(),
                period_expiry=stored_expiry.isoformat(),
            )
        ]
    )
    grant = RecordingGrant()

    first = _settle(client, grant=grant)
    expected_expiry = period_for_renewal(stored_expiry, CONFIRMED_AT)
    assert first.outcome is SettlementOutcome.RECORDED
    assert first.period_expiry == expected_expiry
    assert client.subscription()["period_expiry"] == expected_expiry.isoformat()

    after_first = copy.deepcopy(client.subscription())
    second = _settle(client, grant=grant)

    assert second.outcome is SettlementOutcome.DUPLICATE_IGNORED
    assert client.subscription() == after_first, "the redelivery moved the period"
    assert len(client.settlements) == 1
    assert len(grant.calls) == 1


def test_a_second_reference_for_the_same_subscription_is_not_a_duplicate(
    audit: RecordingAuditLogger,
) -> None:
    """The constraint deduplicates a provider REFERENCE, not a subscription.

    A genuine second month is a second provider reference and must record a second ledger row -
    otherwise a renewal would be silently swallowed as a redelivery.
    """
    client = FakeSupabase(subscriptions=[_subscription_row(status="pending")])
    grant = RecordingGrant()

    first = _settle(client, grant=grant, reference="pi_month_1")
    second_instant = CONFIRMED_AT + timedelta(days=20)
    client.subscription()["status"] = "cancelled"  # a permitted source for re-activation
    second = _settle(
        client, grant=grant, reference="pi_month_2", instant=second_instant
    )

    assert first.outcome is SettlementOutcome.RECORDED
    assert second.outcome is SettlementOutcome.RECORDED
    assert len(client.settlements) == 2
    assert {r["provider_reference"] for r in client.settlements} == {
        "pi_month_1",
        "pi_month_2",
    }
    assert len(grant.calls) == 2
    assert second.period_expiry > first.period_expiry


# ══════════════════════════════════════════════════════════════════════════
# 4. "ALL OF IT OR NONE OF IT" — THE ORDER, AND THE OPTIMISTIC LOCK (Req 9.5)
# ══════════════════════════════════════════════════════════════════════════


def test_the_write_order_records_the_money_before_granting_anything(
    audit: RecordingAuditLogger,
) -> None:
    """Requirement 9.5's honest guarantee: the ledger row is FIRST, the audit is LAST.

    The Persistence_Layer this module talks to exposes no transaction handle, so a single atomic
    commit across the four writes is not available. What IS guaranteed, and what this asserts, is
    the ordering that makes every intermediate state safe: the money is recorded before any
    entitlement is granted, never after, and the required audit closes the sequence.
    """
    client = FakeSupabase(subscriptions=[_subscription_row()])
    audit.client = client
    grant = RecordingGrant(client)

    result = _settle(client, grant=grant)
    assert result.outcome is SettlementOutcome.RECORDED

    ops = client.ops
    read_at = ops.index(("select", ss.SUBSCRIPTION_TABLE))
    ledger_at = ops.index(("insert", ss.SETTLEMENT_TABLE))
    transition_at = ops.index(("update", ss.SUBSCRIPTION_TABLE))
    history_at = ops.index(("insert", ss.TRANSITION_TABLE))
    grant_at = ops.index(("grant", "injected"))
    audit_at = ops.index(("audit", "MARKETPLACE_SETTLEMENT_CREATED"))

    assert read_at < ledger_at < transition_at < history_at < grant_at < audit_at, (
        f"the settlement write order is not read -> ledger -> transition -> history -> "
        f"entitlement -> audit: {ops}"
    )


def test_every_transition_update_carries_the_status_it_read_as_an_optimistic_guard(
    audit: RecordingAuditLogger,
) -> None:
    """The stand-in for design.md's ``SELECT … FOR UPDATE``, and why it is honest.

    PostgREST has no ``FOR UPDATE`` and ``supabase-py`` exposes no transaction handle, so the row
    lock is held the way ``submission_service.apply_admin_action`` holds it: every UPDATE carries
    ``.eq("status", <the status that was read>)``, and an UPDATE matching zero rows is reported
    rather than read as success. That is what turns a lost update into an observable outcome
    instead of a second period extension.
    """
    client = FakeSupabase(subscriptions=[_subscription_row(status="pending")])
    _settle(client, grant=RecordingGrant())

    updates = client.statements_on(ss.SUBSCRIPTION_TABLE, "update")
    assert updates, "no transition UPDATE was issued"
    for statement in updates:
        columns = dict(statement.filters)
        assert columns.get("id") == SUBSCRIPTION_ID
        assert columns.get("status") == "pending", (
            f"the UPDATE must be guarded by the status that was read; filters={statement.filters}"
        )
        # The read is explicit, never select("*").
        assert "*" not in (SETTLEMENT_SUBSCRIPTION_SELECT or "")


def test_a_concurrent_writer_that_moves_the_row_is_reported_not_treated_as_success(
    audit: RecordingAuditLogger, no_sleep: List[int]
) -> None:
    """A lost ``FOR UPDATE`` race becomes ``PERSIST_FAILED``, never a second extension.

    The Subscription is moved out from under the settlement between the read and the UPDATE, so
    the guarded UPDATE matches zero rows. That must not be read as "already done": it is reported,
    retried, and finally surfaced - the alternative is granting an entitlement for a period this
    call never wrote.
    """
    client = FakeSupabase(subscriptions=[_subscription_row(status="pending")])
    grant = RecordingGrant()

    original_execute = client._execute
    moved: List[bool] = []

    def _execute(query: _Query) -> Any:
        if (
            query.op == "insert"
            and query.table_name == ss.SETTLEMENT_TABLE
            and not moved
        ):
            moved.append(True)
            response = original_execute(query)
            # Another writer wins the race after the ledger row lands.
            client.subscription()["status"] = "cancelled"
            return response
        return original_execute(query)

    client._execute = _execute  # type: ignore[assignment]

    with pytest.raises(SettlementPersistFailed):
        _settle(client, grant=grant)

    assert client.transitions == [], "a lost race wrote a history row"
    assert grant.calls == [], "a lost race granted an entitlement"
    assert client.subscription()["status"] == "cancelled", "the other writer's value survived"
    assert client.subscription()["period_expiry"] is None, "a period was written anyway"
    assert "MARKETPLACE_SETTLEMENT_PERSIST_FAILED" in audit.action_names()


def test_a_step_after_the_ledger_row_that_never_completes_grants_no_entitlement(
    audit: RecordingAuditLogger, no_sleep: List[int]
) -> None:
    """The honest boundary of Requirement 9.5, asserted in the safe direction.

    The entitlement write never completes. What must hold, and does: **no**
    ``deployment_permissions`` row is created, the failure is surfaced as
    :class:`SettlementPersistFailed` rather than swallowed, and
    ``MARKETPLACE_SETTLEMENT_PERSIST_FAILED`` records which steps DID land so an operator can
    reconcile by the provider reference.

    The ledger row and the transition remain, and that is deliberate rather than overlooked:
    Requirement 10.8 forbids deleting or modifying a persisted Settlement_Record, and the money
    did move. This is the part of "all of it or none of it" the Persistence_Layer cannot provide,
    and the module records it as such rather than claiming otherwise.
    """
    client = FakeSupabase(subscriptions=[_subscription_row()])
    grant = RecordingGrant(raises=True)

    with pytest.raises(SettlementPersistFailed) as raised:
        _settle(client, grant=grant)

    assert client.permissions == [], "an entitlement row was written by a failed grant"
    assert raised.value.result.entitlement_granted is False
    assert raised.value.provider_reference == REFERENCE

    metadata = audit.metadata_for("MARKETPLACE_SETTLEMENT_PERSIST_FAILED")
    assert metadata["provider_reference"] == REFERENCE
    assert metadata["entitlement_granted"] is False
    assert metadata["settlement_recorded"] is True, (
        "the give-up audit must say the ledger row landed, or an operator cannot reconcile"
    )


def test_a_transient_failure_is_resumed_rather_than_restarted(
    audit: RecordingAuditLogger, no_sleep: List[int]
) -> None:
    """A retry resumes at the first incomplete step: one ledger row, one entitlement.

    Without resumption, a transient failure after the insert would restart at the insert, hit
    ``uq_settlement_reference_reversal`` with its OWN row, report a duplicate and abandon an
    activated payment - a paid-but-no-access defect worse than the failure it recovered from.
    """
    client = FakeSupabase(
        subscriptions=[_subscription_row()],
        # The history insert fails once, then succeeds.
        fail_times={("insert", ss.TRANSITION_TABLE): 1},
    )
    grant = RecordingGrant()

    result = _settle(client, grant=grant)

    assert result.outcome is SettlementOutcome.RECORDED
    assert result.attempts == 2, "the successful attempt must report its own number"
    assert len(client.settlements) == 1, "the ledger row was written twice"
    assert len(client.permissions) == 0  # the injected grant does not write rows
    assert len(grant.calls) == 1, "the entitlement was granted twice"
    assert no_sleep == [1], "the first backoff is one second"


# ══════════════════════════════════════════════════════════════════════════
# 5. A REVERSAL ADDS A ROW AND MUTATES NOTHING (Requirement 10.8)
# ══════════════════════════════════════════════════════════════════════════


def test_a_reversal_adds_a_row_and_never_touches_the_original(
    audit: RecordingAuditLogger,
) -> None:
    """Requirement 10.8: a refund is an ADDITIONAL Settlement_Record, referencing the original.

    Nothing updates or deletes a persisted Settlement_Record - which is why the assertion is on
    the STATEMENTS issued against ``marketplace_settlements`` as well as on the rows: a double
    that only compared row images would pass against a module that updated the row and put the
    same values back.
    """
    client = FakeSupabase(subscriptions=[_subscription_row(status="pending")])
    grant = RecordingGrant()

    paid = _settle(client, grant=grant, reference=REFERENCE)
    assert paid.outcome is SettlementOutcome.RECORDED
    original = copy.deepcopy(client.settlements[0])
    period_expiry_before = client.subscription()["period_expiry"]

    reversal = _settle(
        client,
        grant=grant,
        reference=REFERENCE,
        reverses_reference=REFERENCE,
        is_reversal=True,
        instant=CONFIRMED_AT + timedelta(days=3),
    )

    assert reversal.outcome is SettlementOutcome.REVERSED
    assert len(client.settlements) == 2, "the reversal did not add a row"

    # The original is unchanged, and no statement could have changed it.
    assert client.settlements[0] == original
    assert client.statements_on(ss.SETTLEMENT_TABLE, "update", "delete") == [], (
        "a persisted Settlement_Record must never be updated or deleted (Requirement 10.8)"
    )

    reversal_row = client.settlements[1]
    assert reversal_row["is_reversal"] is True
    assert reversal_row["reverses_reference"] == REFERENCE
    assert reversal_row["provider_reference"] == REFERENCE
    # chk_settlement_conserved holds on the reversal row too.
    assert (
        reversal_row["owner_share_minor"] + reversal_row["platform_fee_minor"]
        == reversal_row["amount_minor"]
    )
    assert reversal_row["amount_minor"] == AMOUNT_1999

    # The Subscription moves to REFUNDED, writes no new period, and grants nothing.
    stored = client.subscription()
    assert stored["status"] == STATUS_TEXT_FOR_STATE[SubscriptionState.REFUNDED]
    assert stored["period_expiry"] == period_expiry_before, "a reversal extended a period"
    assert reversal.period_written is False
    assert reversal.entitlement_granted is False
    assert len(grant.calls) == 1, "a reversal granted an entitlement"

    # Requirement 10.9: the audit covers reversal Settlement_Records too, marked as such.
    created = [
        metadata
        for method, name, kwargs in audit.acts
        if name == "MARKETPLACE_SETTLEMENT_CREATED"
        for metadata in [dict(kwargs.get("metadata") or {})]
    ]
    assert len(created) == 2
    assert created[0]["is_reversal"] is False
    assert created[1]["is_reversal"] is True

    # Requirement 11.12: the history carries both transitions with their prior and new values.
    assert [(t["from_state"], t["to_state"]) for t in client.transitions] == [
        ("pending", "active"),
        ("active", "refunded"),
    ]
    assert client.transitions[1]["cause"] == "refund"


def test_a_reversal_and_a_payment_are_distinct_rows_under_the_constraint(
    audit: RecordingAuditLogger,
) -> None:
    """``uq_settlement_reference_reversal`` is on ``(provider_reference, is_reversal)``.

    Both halves of the key matter: the refund of a payment carries the SAME provider reference,
    so a constraint on the reference alone would refuse the reversal Requirement 10.8 demands.
    """
    client = FakeSupabase(subscriptions=[_subscription_row(status="pending")])
    _settle(client, grant=RecordingGrant())
    _settle(client, is_reversal=True, grant=RecordingGrant())

    keys = {(r["provider_reference"], r["is_reversal"]) for r in client.settlements}
    assert keys == {(REFERENCE, False), (REFERENCE, True)}

    # A second reversal of the same reference IS a duplicate, and is ignored.
    again = _settle(client, is_reversal=True, grant=RecordingGrant())
    assert again.outcome is SettlementOutcome.DUPLICATE_IGNORED
    assert len(client.settlements) == 2


# ══════════════════════════════════════════════════════════════════════════
# 5b. THE LEDGER LOOKUP A REFUND CORRELATES BY (task 19.16, Reqs 10.4, 10.8)
# ══════════════════════════════════════════════════════════════════════════


def test_the_payment_lookup_answers_with_the_subscription_currency_and_amount(
    audit: RecordingAuditLogger,
) -> None:
    """A refund names the payment; the payment's own row names everything a reversal needs.

    This is why refund correlation does not depend on provider metadata: Stripe copies Checkout
    Session metadata onto the Charge only when the session asked it to, and a Razorpay refund
    entity does not reliably carry the payment's ``notes``. The row read here is one this module
    wrote, so nothing is inferred.
    """
    client = FakeSupabase(subscriptions=[_subscription_row(status="pending")])
    _settle(client, grant=RecordingGrant())

    found = ss.find_settled_payment(client, provider_reference=REFERENCE)

    assert found is not None
    assert found["subscription_id"] == SUBSCRIPTION_ID
    assert found["currency"] == "USD"
    assert found["amount_minor"] == AMOUNT_1999
    assert bool(found["is_reversal"]) is False

    # No row for an unknown reference, and ``None`` is the whole answer - the caller writes nothing.
    assert ss.find_settled_payment(client, provider_reference="pi_never_settled") is None


def test_the_payment_lookup_never_answers_with_a_reversal_row(
    audit: RecordingAuditLogger,
) -> None:
    """The reversal of a reversal would be a second earning, so the predicate is explicit."""
    client = FakeSupabase(subscriptions=[_subscription_row(status="pending")])
    _settle(client, grant=RecordingGrant())
    _settle(client, is_reversal=True, grant=RecordingGrant())
    assert len(client.settlements) == 2

    found = ss.find_settled_payment(client, provider_reference=REFERENCE)
    assert found is not None
    assert bool(found["is_reversal"]) is False

    # And a reference that only EVER carried a reversal answers ``None``.
    client.settlements = [
        row for row in client.settlements if bool(row.get("is_reversal"))
    ]
    assert ss.find_settled_payment(client, provider_reference=REFERENCE) is None


def test_a_lookup_read_that_did_not_complete_raises_rather_than_answering_none() -> None:
    """A broken read is not "no such payment": that reading would drop a real reversal."""
    client = FakeSupabase(
        subscriptions=[_subscription_row()], raise_on={("select", ss.SETTLEMENT_TABLE)}
    )
    with pytest.raises(ss.SettlementPersistenceError):
        ss.find_settled_payment(client, provider_reference=REFERENCE)

    with pytest.raises(ValueError):
        ss.find_settled_payment(client, provider_reference="")


def test_the_payment_lookup_names_only_columns_it_filters_and_reads() -> None:
    """The projection is explicit, and both halves of the unique key are filtered on."""
    client = FakeSupabase(subscriptions=[_subscription_row()])
    ss.find_settled_payment(client, provider_reference=REFERENCE)

    reads = client.statements_on(ss.SETTLEMENT_TABLE, "select")
    assert len(reads) == 1
    assert reads[0].cols == ss.SETTLEMENT_PAYMENT_SELECT
    assert ("provider_reference", REFERENCE) in reads[0].filters
    assert ("is_reversal", False) in reads[0].filters
    assert "*" not in ss.SETTLEMENT_PAYMENT_SELECT


# ══════════════════════════════════════════════════════════════════════════
# 6. THE RETRY BUDGET, AND THE LEDGER LEFT UNCHANGED (Requirement 10.11)
# ══════════════════════════════════════════════════════════════════════════


def test_the_declared_budget_is_five_attempts_inside_sixty_seconds() -> None:
    """The two constants Requirement 10.11 names, and a backoff schedule that fits inside them."""
    assert MAX_SETTLEMENT_ATTEMPTS == 5
    assert SETTLEMENT_RETRY_WINDOW_SECONDS == 60
    # One gap per pair of attempts, and the whole schedule fits inside the window with room for
    # the attempts themselves.
    assert len(SETTLEMENT_BACKOFF_SECONDS) == MAX_SETTLEMENT_ATTEMPTS - 1
    assert sum(SETTLEMENT_BACKOFF_SECONDS) < SETTLEMENT_RETRY_WINDOW_SECONDS
    # Bounded, and monotonic: the wait cannot grow without limit.
    assert list(SETTLEMENT_BACKOFF_SECONDS) == sorted(SETTLEMENT_BACKOFF_SECONDS)
    assert [ss._backoff_for(n) for n in range(1, 6)] == [1, 2, 4, 8, 8]


def test_a_ledger_insert_that_never_completes_leaves_the_ledger_unchanged(
    audit: RecordingAuditLogger, no_sleep: List[int]
) -> None:
    """Requirement 10.11: five attempts, then the ledger is unchanged and the failure is audited.

    A payment that did not persist writes no row at all, which is what keeps it out of every
    earnings figure - Requirement 10.7's totals are a read over this ledger, so a row that is not
    here cannot enter one.
    """
    client = FakeSupabase(
        subscriptions=[_subscription_row()],
        raise_on={("insert", ss.SETTLEMENT_TABLE)},
    )
    grant = RecordingGrant()

    with pytest.raises(SettlementPersistFailed) as raised:
        _settle(client, grant=grant)

    # Exactly five attempts, and the four bounded waits between them.
    inserts = client.statements_on(ss.SETTLEMENT_TABLE, "insert")
    assert len(inserts) == MAX_SETTLEMENT_ATTEMPTS
    assert no_sleep == list(SETTLEMENT_BACKOFF_SECONDS)
    assert sum(no_sleep) < SETTLEMENT_RETRY_WINDOW_SECONDS

    # The ledger is unchanged, and so is everything downstream of it.
    assert client.settlements == []
    assert client.transitions == []
    assert client.permissions == []
    assert grant.calls == []
    assert client.subscription()["status"] == "pending"
    assert client.subscription()["period_expiry"] is None

    assert raised.value.attempts == MAX_SETTLEMENT_ATTEMPTS
    assert raised.value.result.outcome is SettlementOutcome.PERSIST_FAILED
    assert raised.value.result.wrote_settlement is False

    metadata = audit.metadata_for("MARKETPLACE_SETTLEMENT_PERSIST_FAILED")
    assert metadata["provider_reference"] == REFERENCE, (
        "the give-up audit must carry the provider reference for operator reconciliation"
    )
    assert metadata["settlement_recorded"] is False
    assert metadata["attempts"] == MAX_SETTLEMENT_ATTEMPTS


def test_the_give_up_audit_uses_the_never_raising_writer(
    audit: RecordingAuditLogger, no_sleep: List[int]
) -> None:
    """The give-up path cannot itself fail: there is nowhere left to escalate to.

    Every other act uses ``record_or_raise`` - Requirement 10.9 conditions the record on being
    auditable - but ``MARKETPLACE_SETTLEMENT_PERSIST_FAILED`` uses the never-raising ``log``, so a
    down audit sink cannot turn a reported failure into an unreported one.
    """
    client = FakeSupabase(
        subscriptions=[_subscription_row()],
        raise_on={("insert", ss.SETTLEMENT_TABLE)},
    )
    with pytest.raises(SettlementPersistFailed):
        _settle(client, grant=RecordingGrant())

    assert audit.method_for("MARKETPLACE_SETTLEMENT_PERSIST_FAILED") == "log"


def test_a_required_audit_that_does_not_complete_is_retried_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, no_sleep: List[int]
) -> None:
    """Requirement 10.9: a Settlement_Record that cannot be audited is not a success.

    ``MARKETPLACE_SETTLEMENT_CREATED`` is written with ``record_or_raise``, so a storage failure
    propagates and the settlement is retried rather than left recorded and unaudited.
    """
    from backend_app.core import audit_trail

    recorder = RecordingAuditLogger(raises=True)
    monkeypatch.setattr(audit_trail, "get_strategy_audit_logger", lambda: recorder)

    client = FakeSupabase(subscriptions=[_subscription_row()])
    with pytest.raises(SettlementPersistFailed):
        _settle(client, grant=RecordingGrant())

    assert recorder.action_names().count("MARKETPLACE_SETTLEMENT_CREATED") == (
        MAX_SETTLEMENT_ATTEMPTS
    )
    # Resumption still holds: one ledger row and one history row, five audit attempts.
    assert len(client.settlements) == 1
    assert len(client.transitions) == 1


# ══════════════════════════════════════════════════════════════════════════
# 7. THE ENTITLEMENT CARRIES THE PERIOD EXPIRY, NEVER None
# ══════════════════════════════════════════════════════════════════════════


def test_the_grant_receives_the_period_expiry_and_never_none(
    audit: RecordingAuditLogger,
) -> None:
    """The privilege fix, asserted on the value the grant actually received.

    ``routers/library.grant_deployment_permission`` wrote ``"expires_at": None``
    unconditionally (deleted by task 19.3), and a null expiry was read as a *perpetual*
    subscription - a monthly Listing granted forever. That is what this assertion exists to keep
    out of the surviving writer, whose ``expires_at`` is a required keyword that refuses ``None``.
    The expiry this path passes is the Subscription_Period expiry computed from
    the confirmation instant (Requirement 11.4), and it is observable precisely because the writer
    is injected.
    """
    client = FakeSupabase(subscriptions=[_subscription_row(status="pending")])
    grant = RecordingGrant()

    result = _settle(client, grant=grant)

    expected_start, expected_expiry = period_for_activation(CONFIRMED_AT)
    assert len(grant.calls) == 1
    call = grant.calls[0]
    assert call["expires_at"] is not None, (
        "a null expires_at is a PERMANENT grant, not a missing value"
    )
    assert call["expires_at"] == expected_expiry == result.period_expiry
    assert call["expires_at"] > expected_start
    assert call["user_id"] == PURCHASER_ID
    assert call["library_id"] == LISTING_ID
    assert call["subscription_id"] == SUBSCRIPTION_ID
    assert result.entitlement_granted is True


def test_the_default_writer_persists_the_expiry_rather_than_null(
    audit: RecordingAuditLogger,
) -> None:
    """The same fact through the real default writer, on the row it inserts."""
    client = FakeSupabase(subscriptions=[_subscription_row(status="pending")])

    _settle(client)  # no injected writer: grant_deployment_permission runs

    assert len(client.permissions) == 1
    row = client.permissions[0]
    _, expected_expiry = period_for_activation(CONFIRMED_AT)
    assert row["expires_at"] == expected_expiry.isoformat()
    assert row["expires_at"] is not None
    assert row["granted_via"] == "subscription"
    assert row["is_active"] is True
    assert row["subscription_id"] == SUBSCRIPTION_ID


def test_the_writer_refuses_a_null_expiry_outright() -> None:
    """The defect is unrepresentable on this path, not merely absent from it.

    ``expires_at`` is a required keyword that may not be ``None``, so a caller with no expiry
    cannot express one - and the refusal is a ``ValueError`` before any statement, not a silently
    written null.
    """
    client = FakeSupabase()

    with pytest.raises(ValueError):
        grant_deployment_permission(
            client,
            user_id=PURCHASER_ID,
            library_id=LISTING_ID,
            subscription_id=SUBSCRIPTION_ID,
            expires_at=None,  # type: ignore[arg-type]
        )
    assert client.permissions == []
    assert client.statements == []

    # And the keyword is required, so it cannot be forgotten.
    with pytest.raises(TypeError):
        grant_deployment_permission(  # type: ignore[call-arg]
            client,
            user_id=PURCHASER_ID,
            library_id=LISTING_ID,
            subscription_id=SUBSCRIPTION_ID,
        )


def test_a_reversal_grants_no_entitlement_and_no_expiry(
    audit: RecordingAuditLogger,
) -> None:
    """A refund has no Subscription_Period to grant against, so nothing is granted."""
    client = FakeSupabase(
        subscriptions=[
            _subscription_row(
                status="active",
                period_start=CONFIRMED_AT.isoformat(),
                period_expiry=(CONFIRMED_AT + timedelta(days=30)).isoformat(),
            )
        ]
    )
    grant = RecordingGrant()

    result = _settle(client, is_reversal=True, grant=grant)

    assert result.outcome is SettlementOutcome.REVERSED
    assert grant.calls == []
    assert client.permissions == []
    assert result.period_expiry is None


# ══════════════════════════════════════════════════════════════════════════
# 8. CURRENCIES ARE NEVER COMBINED (Requirement 10.7)
# ══════════════════════════════════════════════════════════════════════════


def test_each_ledger_row_carries_its_own_currency_unconverted(
    audit: RecordingAuditLogger,
) -> None:
    """Two Subscriptions in two currencies produce two rows, each in its own currency.

    Requirement 10.7 forbids combining Settlement_Records of different currencies into one total,
    and the guarantee this module contributes is upstream of the totals: the currency travels onto
    the row verbatim, there is no conversion anywhere, and the two amounts are never added.
    """
    client = FakeSupabase(
        subscriptions=[
            _subscription_row(row_id="sub-usd", price_minor=1999, currency="USD"),
            _subscription_row(row_id="sub-inr", price_minor=49900, currency="INR"),
        ]
    )
    grant = RecordingGrant()

    usd = _settle(
        client,
        subscription_id="sub-usd",
        amount_minor=1999,
        currency="USD",
        reference="pi_usd",
        grant=grant,
    )
    inr = _settle(
        client,
        subscription_id="sub-inr",
        amount_minor=49900,
        currency="INR",
        reference="pi_inr",
        grant=grant,
    )

    assert usd.currency == "USD" and inr.currency == "INR"
    rows = {r["currency"]: r for r in client.settlements}
    assert set(rows) == {"USD", "INR"}
    assert rows["USD"]["amount_minor"] == 1999
    assert rows["INR"]["amount_minor"] == 49900
    # Each row conserves within its own currency; nothing was summed across them.
    for row in rows.values():
        assert row["owner_share_minor"] + row["platform_fee_minor"] == row["amount_minor"]


def test_the_currency_is_matched_case_insensitively_but_stored_upper_case(
    audit: RecordingAuditLogger,
) -> None:
    """``chk_settlement_currency`` admits ``'USD'`` and ``'INR'`` only, so the code is normalised.

    A lower-case confirmation is the same currency, not a mismatch - but the persisted value is
    the ISO 4217 spelling, because a row carrying ``'usd'`` would be refused by the constraint.
    """
    client = FakeSupabase(subscriptions=[_subscription_row(currency="usd")])

    result = _settle(client, currency="Usd", grant=RecordingGrant())

    assert result.outcome is SettlementOutcome.RECORDED
    assert result.currency == "USD"
    assert client.settlements[0]["currency"] == "USD"


# ══════════════════════════════════════════════════════════════════════════
# 9. THE STATE MACHINE AND THE COLUMN CONTRACT
# ══════════════════════════════════════════════════════════════════════════


def test_the_activation_sources_are_the_derived_edges_plus_payment_failed() -> None:
    """``ELIGIBLE_FOR_ACTIVATION`` is the derived edge set plus ``payment_failed`` (task 19.2).

    The four derived members come from Requirement 11.2's twelve pairs rather than being
    transcribed, so they cannot silently disagree with
    ``marketplace_subscription_allowed_transitions``.

    ``payment_failed`` is the recorded requirements decision: Requirement 11.2 gives
    ``PAYMENT_FAILED`` one successor (``PENDING``) while Requirement 11.6 lists it among the
    sources of a transition into ``ACTIVE``, and design.md's pascal writes the same five statuses.
    11.6 wins, because a retried payment that later confirms must activate the Subscription it paid
    for rather than leave a charged purchaser with no access. The derivation and the deliberate
    addition are asserted separately so neither can drift into the other.
    """
    assert ss._ACTIVATION_SOURCES_FROM_TRANSITIONS == frozenset(
        STATUS_TEXT_FOR_STATE[state]
        for state in SubscriptionState
        if can_transition(state, SubscriptionState.ACTIVE)
    )
    assert ss._ACTIVATION_SOURCES_FROM_TRANSITIONS == {
        "pending",
        "expired",
        "cancelled",
        "suspended",
    }
    assert "payment_failed" in ss.ELIGIBLE_FOR_ACTIVATION
    assert ss.ELIGIBLE_FOR_ACTIVATION == {
        "pending",
        "expired",
        "cancelled",
        "suspended",
        "payment_failed",
    }


def test_a_retried_payment_activates_a_payment_failed_subscription(
    audit: RecordingAuditLogger,
) -> None:
    """Requirement 11.6 over Requirement 11.2: ``payment_failed -> active`` is permitted.

    A Subscription whose first payment attempt failed carries no period, so the confirmed retry is
    a first activation (Requirement 11.4) and must grant the entitlement with that period's expiry.
    Before the decision this row recorded its money and moved nowhere, which is the
    charged-with-no-access failure mode.
    """
    client = FakeSupabase(subscriptions=[_subscription_row(status="payment_failed")])
    grant = RecordingGrant()

    result = _settle(client, grant=grant)

    assert result.outcome is SettlementOutcome.RECORDED
    assert (result.from_status, result.to_status) == ("payment_failed", "active")
    stored = client.subscription()
    assert stored["status"] == "active"
    expected_start, expected_expiry = period_for_activation(CONFIRMED_AT)
    assert result.period_start == expected_start
    assert result.period_expiry == expected_expiry
    assert stored["period_expiry"] == expected_expiry.isoformat()
    assert grant.expiries == [expected_expiry]
    assert [(t["from_state"], t["to_state"]) for t in client.transitions] == [
        ("payment_failed", "active")
    ]


def test_the_retried_payment_edge_is_permitted_by_the_database_too(
    audit: RecordingAuditLogger,
) -> None:
    """Task 19.15: ``payment_failed -> active`` end to end - service AND guard agree.

    Task 19.2 widened :data:`ELIGIBLE_FOR_ACTIVATION` and left
    ``marketplace_subscription_allowed_transitions`` seeded from Requirement 11.2's twelve
    pairs, which do not include this edge. Because the Settlement_Record is written FIRST
    (Requirement 9.5 - the money is recorded before any entitlement), the live behaviour was:
    ledger row committed, owner credited, then ``trg_subscription_transition_guard`` refused the
    activation with 23514 from its missing-edge branch. The purchaser paid and got nothing, and
    the ledger and the Subscription_State disagreed permanently.

    ``012_subscription_payment_failed_activation.sql`` seeds the edge. This test asserts the two
    halves TOGETHER, because either one alone is what the defect looked like:

    1. the service runs the transition, with a well-formed Subscription_Period, and
    2. the pair is in the set the guard's membership probe reads - parsed off the migrations by
       ``tests/test_submission_state_agreement.subscription_permitted_pairs_in_db``, the same
       function the state-agreement and P-49 tests read the machine through.

    It also pins the ordering the guard's *settlement* branch depends on: the non-reversal
    ``marketplace_settlements`` row for this subscription exists before the status UPDATE is
    issued, so branch 3 finds a qualifying payment (``OLD.period_expiry IS NULL`` on a first
    activation, so any non-reversal row for the row qualifies) instead of refusing the write.
    """
    from tests.test_submission_state_agreement import subscription_permitted_pairs_in_db

    # (1) The database permits the edge.
    permitted = subscription_permitted_pairs_in_db()
    assert ("PAYMENT_FAILED", "ACTIVE") in permitted, (
        "the seed tables do not permit payment_failed -> active, so the guard would refuse the "
        "activation of a retried payment whose ledger row has already been written"
    )

    # (2) The service performs it, with a well-formed period.
    client = FakeSupabase(subscriptions=[_subscription_row(status="payment_failed")])
    grant = RecordingGrant()

    result = _settle(client, grant=grant)

    assert result.outcome is SettlementOutcome.RECORDED
    assert (result.from_status, result.to_status) == ("payment_failed", "active")

    expected_start, expected_expiry = period_for_activation(CONFIRMED_AT)
    assert result.period_start == expected_start == CONFIRMED_AT
    assert result.period_expiry == expected_expiry
    # Well-formed: both bounds present, tz-aware UTC, and the expiry strictly after the start by
    # one calendar month (Requirement 11.4). ``chk_ls_active_has_period`` makes an ``active`` row
    # without a period unrepresentable, so an activation that wrote none would be refused anyway.
    assert expected_start.tzinfo == timezone.utc
    assert expected_expiry.tzinfo == timezone.utc
    assert expected_expiry > expected_start
    assert (expected_expiry.year, expected_expiry.month, expected_expiry.day) == (2025, 7, 15)
    assert expected_expiry.timetz() == expected_start.timetz()

    stored = client.subscription()
    assert stored["status"] == "active"
    assert stored["period_start"] == expected_start.isoformat()
    assert stored["period_expiry"] == expected_expiry.isoformat()
    # The retained mirrors track the period columns rather than being left null.
    assert stored["started_at"] == stored["period_start"]
    assert stored["expires_at"] == stored["period_expiry"]

    # The entitlement carries the paid expiry, and the history row records the transition.
    assert grant.expiries == [expected_expiry]
    assert [(t["from_state"], t["to_state"]) for t in client.transitions] == [
        ("payment_failed", "active")
    ]

    # (3) The guard's settlement branch has something to find, and finds it BEFORE the UPDATE.
    ledger = [row for row in client.settlements if row["subscription_id"] == SUBSCRIPTION_ID]
    assert len(ledger) == 1
    assert ledger[0]["is_reversal"] is False
    assert ledger[0]["settled_at"] == CONFIRMED_AT.isoformat()
    ops = client.ops
    assert ops.index(("insert", ss.SETTLEMENT_TABLE)) < ops.index(
        ("update", ss.SUBSCRIPTION_TABLE)
    ), (
        "the status UPDATE is issued before the Settlement_Record insert, so the guard's "
        "settlement probe would find no qualifying payment and refuse the activation"
    )


def test_the_addendum_admits_one_edge_and_no_unpaid_activation(
    audit: RecordingAuditLogger,
) -> None:
    """Task 19.15: one more permitted edge, and not one more way into ``active``.

    Two things must remain true after ``012``:

    * the service's activation sources and the database's are the SAME five statuses - a status
      the service would activate but the guard refuses is the defect this remediation closes,
      and the reverse (the database permitting an edge no service path takes) is a permitted
      transition nothing audits; and
    * every ordered pair outside Requirement 11.2's twelve plus that one addendum is still
      absent from the permitted set, so nothing else slipped in with it. The refusal MECHANISM
      is ``tests/property/test_db_transition_guards.py``'s P-49; the *contents* of the permitted
      set are pinned here and in ``tests/test_submission_state_agreement.py``.

    The settlement precondition itself - no ``-> active`` without a qualifying non-reversal
    ``marketplace_settlements`` row - is asserted against the guard body in
    ``tests/test_submission_state_agreement.TestTheAddendumDoesNotOpenTheGate``, and behaviourally
    here: a Subscription whose payment never settled never reaches ``active`` on this path,
    because ``settle`` writes the ledger row first or returns without transitioning at all.
    """
    from tests.test_submission_state_agreement import (
        MIGRATION_012_SUBSCRIPTION_ADDENDUM,
        subscription_permitted_pairs_in_db,
    )

    permitted = subscription_permitted_pairs_in_db()

    # The five sources of an activation, on both sides of the divide.
    db_sources = {frm.lower() for frm, to in permitted if to == "ACTIVE"}
    assert db_sources == set(ss.ELIGIBLE_FOR_ACTIVATION)
    assert len(db_sources) == 5

    # Requirement 11.2's twelve, plus exactly the one named addendum, and nothing else.
    twelve = {
        (STATUS_TEXT_FOR_STATE[a].upper(), STATUS_TEXT_FOR_STATE[b].upper())
        for a in SubscriptionState
        for b in SubscriptionState
        if can_transition(a, b)
    }
    assert len(twelve) == 12
    assert permitted == twelve | MIGRATION_012_SUBSCRIPTION_ADDENDUM
    assert len(permitted) == 13

    # A confirmation that cannot be correlated to a Subscription writes no ledger row and no
    # transition, so no path exists from "a payment arrived" to "some row became active".
    empty = FakeSupabase(subscriptions=[])
    grant = RecordingGrant()
    result = _settle(empty, grant=grant)
    assert result.outcome is SettlementOutcome.UNMATCHED
    assert empty.settlements == []
    assert empty.transitions == []
    assert grant.calls == []
    assert not empty.wrote_anything()


# ══════════════════════════════════════════════════════════════════════════
# 9b. THE ``ACTIVE -> ACTIVE`` RENEWAL (Requirement 11.5, task 19.2's decision 1)
# ══════════════════════════════════════════════════════════════════════════


def _active_row_with_period(stored_expiry: datetime) -> Dict[str, Any]:
    """An ``active`` Subscription whose paid period ends at ``stored_expiry``."""
    return _subscription_row(
        status="active",
        period_start=(stored_expiry - timedelta(days=31)).isoformat(),
        period_expiry=stored_expiry.isoformat(),
    )


def test_a_renewal_of_an_already_active_subscription_extends_the_period(
    audit: RecordingAuditLogger,
) -> None:
    """A paid renewal of a live Subscription extends its period (Requirement 11.5).

    The pre-decision behaviour recorded the Settlement_Record and extended NOTHING, because
    Requirement 11.2 lists no ``ACTIVE -> ACTIVE`` pair and the period write sat behind
    ``can_transition``. A renewal charged for a month that was never granted is a
    financial-correctness defect, so the settlement now writes the period without a transition -
    the status stays ``active`` throughout, which is what
    ``marketplace_subscription_guard``'s same-value branch admits.
    """
    stored_expiry = CONFIRMED_AT + timedelta(days=10)
    client = FakeSupabase(subscriptions=[_active_row_with_period(stored_expiry)])
    grant = RecordingGrant()

    result = _settle(client, grant=grant, reference="pi_renewal_1")

    expected_expiry = period_for_renewal(stored_expiry, CONFIRMED_AT)
    assert result.outcome is SettlementOutcome.RECORDED
    assert (result.from_status, result.to_status) == ("active", "active")
    assert result.period_expiry == expected_expiry
    assert result.period_written is True
    assert result.entitlement_granted is True

    stored = client.subscription()
    assert stored["status"] == "active", "an in-place renewal is not a transition"
    assert stored["period_expiry"] == expected_expiry.isoformat()
    assert stored["expires_at"] == expected_expiry.isoformat()
    # The period START is the one the purchaser already paid for; only the expiry moves.
    assert stored["period_start"] == (stored_expiry - timedelta(days=31)).isoformat()

    # Requirement 11.12: the extension is history too, and ``from_state == to_state`` is how a
    # reader tells an in-place extension from a transition.
    assert [(t["from_state"], t["to_state"]) for t in client.transitions] == [
        ("active", "active")
    ]
    row = client.transitions[0]
    assert row["prior_period_expiry"] == stored_expiry.isoformat()
    assert row["new_period_expiry"] == expected_expiry.isoformat()

    # The entitlement is re-granted carrying the EXTENDED expiry, never a null.
    assert grant.expiries == [expected_expiry]


def test_the_active_renewal_is_anchored_on_the_stored_expiry_not_on_the_confirmation(
    audit: RecordingAuditLogger,
) -> None:
    """The anchor is ``period_expiry``, so an early renewal lengthens rather than truncates.

    Anchoring on the confirmation instant (or on a clock read) would move the expiry BACKWARDS for
    a provider that charges before the current period ends - the purchaser would pay for a month
    and lose the remainder of the one they had.
    """
    stored_expiry = CONFIRMED_AT + timedelta(days=20)
    client = FakeSupabase(subscriptions=[_active_row_with_period(stored_expiry)])

    result = _settle(client, grant=RecordingGrant(), reference="pi_early_renewal")

    assert result.period_expiry == period_for_renewal(stored_expiry, CONFIRMED_AT)
    assert result.period_expiry > stored_expiry, "the renewal shortened the paid period"
    assert result.period_expiry != period_for_activation(CONFIRMED_AT)[1]


def test_an_active_renewal_records_the_money_before_it_extends_anything(
    audit: RecordingAuditLogger, no_sleep: List[int]
) -> None:
    """The extension still requires its Settlement_Record (Requirements 11.6, 11.14).

    ``marketplace_subscription_guard`` waves a same-value write through, so the ordering here is
    what carries the invariant: the ledger insert is the step before the period write, and a ledger
    insert that never completes leaves the stored expiry exactly where it was.
    """
    stored_expiry = CONFIRMED_AT + timedelta(days=10)
    ordered = FakeSupabase(subscriptions=[_active_row_with_period(stored_expiry)])

    _settle(ordered, grant=RecordingGrant(), reference="pi_order")

    ops = [op for op in ordered.ops if op[0] in {"insert", "update", "grant"}]
    assert ops[0] == ("insert", ss.SETTLEMENT_TABLE)
    assert ops.index(("insert", ss.SETTLEMENT_TABLE)) < ops.index(
        ("update", ss.SUBSCRIPTION_TABLE)
    )

    refused = FakeSupabase(
        subscriptions=[_active_row_with_period(stored_expiry)],
        raise_on={("insert", ss.SETTLEMENT_TABLE)},
    )
    with pytest.raises(SettlementPersistFailed):
        _settle(refused, grant=RecordingGrant(), reference="pi_unrecorded")

    assert refused.settlements == []
    assert refused.subscription()["period_expiry"] == stored_expiry.isoformat()
    assert refused.transitions == []
    assert refused.permissions == []


@pytest.mark.parametrize("deliveries", [2, 3])
def test_a_redelivered_renewal_extends_the_active_period_exactly_once(
    audit: RecordingAuditLogger, deliveries: int
) -> None:
    """Property P-6 holds for the in-place renewal too: one reference, one extension.

    Without ``uq_settlement_reference_reversal`` doing the deduplicating, N deliveries of one
    renewal would be N free months on a Subscription that never leaves ``active``.
    """
    stored_expiry = CONFIRMED_AT + timedelta(days=10)
    client = FakeSupabase(subscriptions=[_active_row_with_period(stored_expiry)])
    grant = RecordingGrant()

    outcomes = [
        _settle(client, grant=grant, reference="pi_renewal_dup")
        for _ in range(deliveries)
    ]

    expected_expiry = period_for_renewal(stored_expiry, CONFIRMED_AT)
    assert outcomes[0].outcome is SettlementOutcome.RECORDED
    assert all(o.outcome is SettlementOutcome.DUPLICATE_IGNORED for o in outcomes[1:])
    assert len(client.settlements) == 1
    assert len(client.transitions) == 1
    assert len(grant.calls) == 1
    assert client.subscription()["period_expiry"] == expected_expiry.isoformat()


def test_a_second_refund_of_a_refunded_subscription_still_moves_nothing(
    audit: RecordingAuditLogger,
) -> None:
    """The permissive renewal branch did not open a path out of a terminal state.

    ``refunded`` is terminal and ``REFUNDED -> REFUNDED`` is not a renewal, so a second reversal
    records its ledger row (Requirement 10.8 keeps every one) and transitions nothing.
    """
    client = FakeSupabase(
        subscriptions=[
            _subscription_row(
                status="refunded",
                period_start=CONFIRMED_AT.isoformat(),
                period_expiry=(CONFIRMED_AT + timedelta(days=30)).isoformat(),
            )
        ]
    )
    grant = RecordingGrant()

    result = _settle(
        client, grant=grant, reference="re_second", is_reversal=True
    )

    assert result.outcome is SettlementOutcome.REVERSED
    assert result.from_status == "refunded"
    assert result.to_status is None
    assert len(client.settlements) == 1
    assert client.transitions == []
    assert grant.calls == []
    assert client.subscription()["status"] == "refunded"


@pytest.mark.parametrize("source", ["pending", "expired", "cancelled", "suspended"])
def test_every_permitted_source_reaches_active_with_a_period_and_an_entitlement(
    audit: RecordingAuditLogger, source: str
) -> None:
    """Requirement 11.6: a confirmed payment is what moves each of these to ``ACTIVE``.

    ``chk_ls_active_has_period`` makes an ``ACTIVE`` row without a period unrepresentable, so the
    period is asserted on every one of them - an activation that wrote no period would be a row
    the database refuses.
    """
    has_period = source != "pending"
    stored_expiry = CONFIRMED_AT - timedelta(days=1) if has_period else None
    client = FakeSupabase(
        subscriptions=[
            _subscription_row(
                status=source,
                period_start=(CONFIRMED_AT - timedelta(days=31)).isoformat()
                if has_period
                else None,
                period_expiry=stored_expiry.isoformat() if stored_expiry else None,
            )
        ]
    )
    grant = RecordingGrant()

    result = _settle(client, grant=grant)

    assert result.outcome is SettlementOutcome.RECORDED
    assert result.from_status == source
    assert result.to_status == "active"

    stored = client.subscription()
    assert stored["status"] == "active"
    assert stored["period_start"] and stored["period_expiry"]
    # The retained mirrors are aligned with the period columns, never left null.
    assert stored["expires_at"] == stored["period_expiry"]
    assert stored["started_at"] == stored["period_start"]
    assert stored["cancelled_at"] is None

    expected = (
        period_for_renewal(stored_expiry, CONFIRMED_AT)
        if stored_expiry
        else period_for_activation(CONFIRMED_AT)[1]
    )
    assert result.period_expiry == expected
    assert grant.expiries == [expected]


def test_a_settlement_records_the_history_row_with_both_expiries(
    audit: RecordingAuditLogger,
) -> None:
    """Requirement 11.12: the transition, its cause and both period expiries are retained.

    Both expiries travel on the row, which is what lets an audit reconstruct WHICH period a
    payment bought without joining the ledger.
    """
    stored_expiry = CONFIRMED_AT - timedelta(days=1)
    client = FakeSupabase(
        subscriptions=[
            _subscription_row(status="expired", period_expiry=stored_expiry.isoformat())
        ]
    )

    result = _settle(client, grant=RecordingGrant(), actor_id=None)

    assert len(client.transitions) == 1
    row = client.transitions[0]
    assert row["subscription_id"] == SUBSCRIPTION_ID
    assert row["user_id"] == PURCHASER_ID
    assert (row["from_state"], row["to_state"]) == ("expired", "active")
    assert row["cause"] == "settlement"
    assert row["actor_id"] is None, "a provider-driven confirmation has no human actor"
    assert row["prior_period_expiry"] == stored_expiry.isoformat()
    assert row["new_period_expiry"] == result.period_expiry.isoformat()
    assert row["transitioned_at"] == CONFIRMED_AT.isoformat()


def test_the_period_is_computed_from_the_confirmation_instant_not_from_a_clock_read(
    audit: RecordingAuditLogger,
) -> None:
    """Requirements 11.4, 11.5: the arithmetic is reproducible for an audit.

    The instant arrives as an argument, so settling the same confirmation on any day produces the
    same period. A clock read here would make the expiry unverifiable after the fact.
    """
    instant = datetime(2025, 1, 31, 23, 59, 59, tzinfo=timezone.utc)
    expiries = []
    for _ in range(2):
        client = FakeSupabase(subscriptions=[_subscription_row()])
        result = _settle(client, instant=instant, grant=RecordingGrant())
        expiries.append(result.period_expiry)

    # 31 January clamps to 28 February, and does so identically both times.
    assert expiries[0] == expiries[1] == datetime(
        2025, 2, 28, 23, 59, 59, tzinfo=timezone.utc
    )


def test_the_settlement_manifest_entry_exists_and_covers_the_projection() -> None:
    """The module's one ``.select`` sits inside its ``COLUMN_CONTRACT`` entry (task 11.6).

    A mistyped column on a payment *confirmation* is the PGRST204 condition the manifest exists to
    catch. The older ``settlement_and_subscription`` entry names ``subscription_period`` - pure
    arithmetic that issues no statement - so it bound no projection; this entry names the module
    that actually reads the Persistence_Layer.
    """
    entry = COLUMN_CONTRACT.get("settlement")
    assert entry is not None, "COLUMN_CONTRACT has no 'settlement' entry"
    assert entry["module"] == "backend_app.backend.marketplace.settlement_service"

    declared = set()
    for columns in entry["tables"].values():  # type: ignore[union-attr]
        declared |= set(columns)

    for token in SETTLEMENT_SUBSCRIPTION_SELECT.split(","):
        name = token.strip()
        if name:
            assert name in declared, f"{name!r} is read but not declared in the manifest"

    # The four tables this module writes are all bound, including the entitlement table whose
    # ``expires_at`` column is what the privilege fix depends on.
    tables = set(entry["tables"])  # type: ignore[arg-type]
    assert tables == {
        ss.SUBSCRIPTION_TABLE,
        ss.SETTLEMENT_TABLE,
        ss.TRANSITION_TABLE,
        ss.PERMISSION_TABLE,
    }
    assert "expires_at" in entry["tables"][ss.PERMISSION_TABLE]  # type: ignore[index]


def test_the_ledger_payload_names_every_column_and_only_those_columns(
    audit: RecordingAuditLogger,
) -> None:
    """The insert payload is bound by the manifest too: a write is a 42703 risk like a read."""
    client = FakeSupabase(subscriptions=[_subscription_row()])
    _settle(client, grant=RecordingGrant())

    declared = set(COLUMN_CONTRACT["settlement"]["tables"][ss.SETTLEMENT_TABLE])  # type: ignore[index]
    payload = client.statements_on(ss.SETTLEMENT_TABLE, "insert")[0].payload or {}
    assert set(payload) <= declared, (
        f"the ledger insert writes columns the manifest does not declare: "
        f"{sorted(set(payload) - declared)}"
    )
    assert payload["subscription_id"] == SUBSCRIPTION_ID
    assert payload["listing_id"] == LISTING_ID
    assert payload["owner_id"] == OWNER_ID
    assert payload["purchaser_id"] == PURCHASER_ID
    assert payload["settled_at"] == CONFIRMED_AT.isoformat()
    assert payload["provider"] == PROVIDER
    assert payload["is_reversal"] is False
    assert payload["reverses_reference"] is None


# ══════════════════════════════════════════════════════════════════════════
# 10. A READ THAT DOES NOT COMPLETE IS NOT AN ANSWER (Reqs 1.5, 1.7, 30.5)
# ══════════════════════════════════════════════════════════════════════════


def test_a_broken_subscription_read_is_never_reported_as_unmatched(
    audit: RecordingAuditLogger, no_sleep: List[int]
) -> None:
    """A failed read reported as "unmatched" would discard a real payment.

    Both shapes are covered: a raising driver, and a response that "completes" carrying a
    PostgREST error envelope - the shape that must NOT be read as "no rows".
    """
    for client in (
        FakeSupabase(
            subscriptions=[_subscription_row()],
            raise_on={("select", ss.SUBSCRIPTION_TABLE)},
        ),
        FakeSupabase(
            subscriptions=[_subscription_row()],
            error_on={("select", ss.SUBSCRIPTION_TABLE)},
        ),
    ):
        with pytest.raises(SettlementPersistFailed):
            _settle(client, grant=RecordingGrant())
        assert client.settlements == []
        assert client.transitions == []
        assert client.permissions == []
        assert "MARKETPLACE_SETTLEMENT_UNMATCHED" not in audit.action_names()
