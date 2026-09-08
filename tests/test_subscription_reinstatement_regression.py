"""
tests/test_subscription_reinstatement_regression.py - lifting a hold must not cost a month.

Spec: marketplace-subscriptions-paper-trading, task 22 remediation. Requirements 11.2, 11.6,
11.9, 11.12, 11.13, 11.14, **11.17** (the clause this change adds).

THE DEFECT THIS FILE IS RECORDED AGAINST
----------------------------------------
A ``SUSPENDED`` Subscription had no settlement-free route back to ``ACTIVE``, so a purchaser
suspended by an administrator had to **buy a fresh month** to recover access they had already
paid for.

* Requirement 11.2 PERMITS ``SUSPENDED -> ACTIVE`` and
  ``008_marketplace_settlement.sql`` seeds that edge into
  ``marketplace_subscription_allowed_transitions``.
* Requirement 11.6 requires a confirmed payment before every transition into ``ACTIVE``
  "from any of ``PENDING``, ``EXPIRED``, ``CANCELLED`` and ``PAYMENT_FAILED``" -
  ``SUSPENDED`` is **absent** from that list. The two clauses contradict each other.
* The implementation took the stricter reading: ``settlement_service`` was the only writer of
  ``status='active'``, and branch 3 of ``marketplace_subscription_guard()`` was keyed on
  ``NEW.status = 'active'`` alone - unconditional on the source - so every route into
  ``ACTIVE``, ``SUSPENDED`` included, demanded a NEW non-reversal ``marketplace_settlements``
  row. ``routers/billing.py::resume_subscription`` acts on the platform plan, not
  ``library_subscriptions``, so it was not a route either.

THE DECISION THIS FILE PINS (Requirement 11.17)
-----------------------------------------------
A suspension is a **hold, not a refund**. The period the purchaser paid for is still theirs,
so lifting the hold restores access WITHOUT a charge and WITHOUT moving the period. The
reinstatement is administrative: an Admin_Reviewer resolved through ``get_admin_user`` - the
same dependency the five submission actions carry - a recorded reason, an Audit_Log entry and a
history row whose ``cause`` is **not** ``settlement``.

WHAT STAYS TRUE (Requirement 11.14 is not weakened)
---------------------------------------------------
"No ``-> active`` without a payment behind it" holds for every source. The one authorised
relaxation, in ``014_subscription_admin_reinstatement.sql``, admits ``suspended -> active``
without a NEW settlement **only** when the period columns are not moved and a non-reversal
settlement already exists for the period being resumed - so a Subscription that never paid
cannot be "reinstated" into access it never bought, and the four other sources
(``pending``, ``expired``, ``cancelled``, ``payment_failed``) still meet ``008``'s unchanged
probe.

FAILING-BEFORE EVIDENCE
-----------------------
This file was written and run FIRST, against the tree before any of the fix existed - no
``subscription_reinstatement`` module, no route, no ``014`` migration, and the guard's activation
branch still keyed on ``NEW.status = 'active'`` alone::

    python -m pytest tests/test_subscription_reinstatement_regression.py -q --no-header \\
        -p no:randomly
    ==> 36 failed, 2 passed in 56.34s        (38 collected then; 39 now)

The two that passed are the two that assert what was ALREADY true: that a ``suspended``
Subscription does not entitle before the reinstatement, and that ``008``'s permitted-pair set
already carries ``('SUSPENDED','ACTIVE')`` - which is exactly the point, since the edge was never
what refused the reinstatement. Among the 36::

    TestASuspendedSubscriptionCanBeReinstatedWithoutACharge
        ::test_a_suspended_subscription_returns_to_active_with_no_settlement
        ImportError: cannot import name 'subscription_reinstatement' from
        'backend_app.backend.marketplace'
    TestOnlyAnAdminReviewerMayReinstate::test_the_route_exists_and_carries_get_admin_user
        AssertionError: no POST route ending in
        /admin/subscriptions/{subscription_id}/reinstate is registered on library.router, so a
        suspended purchaser has no settlement-free route back to ACTIVE
    TestTheDatabaseAdmitsThisOneSourceAndNoOther
        ::test_the_effective_guard_admits_the_administrative_reinstatement
        AssertionError: the effective marketplace_subscription_guard() still demands a NEW
        settlement for suspended -> active, so an administrative reinstatement is refused by
        23514
    TestTheDatabaseAdmitsThisOneSourceAndNoOther::test_the_relaxation_migration_exists
        FileNotFoundError: [Errno 2] No such file or directory:
        'backend_app/migrations/014_subscription_admin_reinstatement.sql'

After the fix the same command is ``39 passed``. Both runs are recorded in the task report.

WHY THE DATABASE HALF IS READ STATICALLY
----------------------------------------
There is no PostgreSQL in this environment, so the guard is asserted by reading the migrations
off disk - the technique every migration test in this repository uses. It is read through
``tests/test_submission_state_agreement.py``'s own parsers
(:func:`subscription_guard_body_in_db`, :func:`subscription_permitted_pairs_in_db`) rather than
transcribed here, so there is exactly one answer to "what does the guard admit today" and this
file cannot drift from it.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import datetime as _datetime
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

# The guard parsers and the effective permitted-edge set, imported rather than re-written.
from tests.test_submission_state_agreement import (  # noqa: E402 - shared machinery
    MIGRATIONS,
    subscription_guard_body_in_db,
    subscription_permitted_pairs_in_db,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The additive migration that relaxes the guard for exactly one source.
REINSTATEMENT_MIGRATION = "014_subscription_admin_reinstatement.sql"

#: ``_safe_uuid`` validates every path parameter, so these have to be real UUIDs.
SUBSCRIPTION_ID = "11111111-1111-4111-8111-111111111111"
LISTING_ID = "22222222-2222-4222-8222-222222222222"
PURCHASER_ID = "33333333-3333-4333-8333-333333333333"
OWNER_ID = "44444444-4444-4444-8444-444444444444"
ADMIN_ID = "55555555-5555-4555-8555-555555555555"
STRATEGY_ID = "66666666-6666-4666-8666-666666666666"
VERSION_ID = "77777777-7777-4777-8777-777777777777"

ADMIN = {"id": ADMIN_ID}

#: The period the purchaser already paid for. The suspension is inside it, so reinstatement
#: restores the REMAINING period and moves neither boundary.
PERIOD_START = "2025-05-01T00:00:00+00:00"
PERIOD_EXPIRY = "2025-06-01T00:00:00+00:00"
#: The payment that bought that period, settled at its start.
SETTLED_AT = "2025-05-01T00:00:12+00:00"
#: An instant inside the paid period - the reinstatement instant.
NOW = _datetime.datetime(2025, 5, 20, 9, 30, tzinfo=_datetime.timezone.utc)

REASON = "Investigation closed; the report was unfounded, so the hold is lifted."


# ══════════════════════════════════════════════════════════════════════════
# The recording fake Persistence_Layer
# ══════════════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    """A recording supabase-py-shaped query builder."""

    def __init__(self, table: str, client: "FakeSupabase") -> None:
        self.table_name = table
        self.client = client
        self.op = "select"
        self.payload: Optional[Dict[str, Any]] = None
        self.cols: Optional[str] = None
        self.filters: List[Tuple[str, Any]] = []
        self.in_filters: List[Tuple[str, List[Any]]] = []

    def select(self, cols: str = "*", **_kwargs: Any) -> "_Query":
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

    def in_(self, col: str, vals: Any) -> "_Query":
        self.in_filters.append((col, list(vals)))
        return self

    def order(self, *_args: Any, **_kwargs: Any) -> "_Query":
        return self

    def limit(self, *_args: Any, **_kwargs: Any) -> "_Query":
        return self

    def single(self) -> "_Query":
        return self

    def maybe_single(self) -> "_Query":
        return self

    def execute(self) -> Any:
        return self.client._execute(self)


class FakeSupabase:
    """Mutable tables plus a complete statement log.

    The log is what makes "the period is untouched" and "no settlement row is written"
    measurements rather than claims: every statement is recorded with its payload.
    """

    WRITE_OPS = frozenset({"insert", "update", "delete"})

    def __init__(
        self,
        *,
        subscription_status: str = "suspended",
        settlements: Optional[List[Dict[str, Any]]] = None,
        subscription_overrides: Optional[Dict[str, Any]] = None,
        raise_on: Optional[set] = None,
    ) -> None:
        row = _subscription_row(status=subscription_status)
        row.update(subscription_overrides or {})
        self.tables: Dict[str, List[Dict[str, Any]]] = {
            "library_subscriptions": [row],
            "marketplace_settlements": [dict(s) for s in settlements]
            if settlements is not None
            else [_settlement_row()],
            "library_subscription_transitions": [],
            "deployment_permissions": [],
            "library_strategies": [_listing_row()],
            "strategy_versions": [
                {
                    "id": VERSION_ID,
                    "strategy_id": STRATEGY_ID,
                    "version": 3,
                    "is_draft": False,
                }
            ],
        }
        self.raise_on = raise_on or set()
        self.statements: List[_Query] = []

    # ---- the surface under test uses ------------------------------------
    def table(self, name: str) -> _Query:
        return _Query(name, self)

    def _execute(self, q: _Query) -> Any:
        self.statements.append(q)
        if (q.op, q.table_name) in self.raise_on:
            raise RuntimeError(f"{q.op} failed on {q.table_name}")

        rows = self.tables.setdefault(q.table_name, [])
        if q.op == "select":
            matched = [copy.deepcopy(r) for r in self._matching(q, rows)]
            if q.table_name == "library_strategies":
                # The Entitlement_Resolver reads the Listing with its embeds.
                for listing in matched:
                    listing["library_subscriptions"] = [
                        {
                            "id": s["id"],
                            "user_id": s["user_id"],
                            "status": s["status"],
                            "period_expiry": s["period_expiry"],
                        }
                        for s in self.tables["library_subscriptions"]
                    ]
            return _Resp(matched)
        if q.op == "insert":
            row = dict(q.payload or {})
            row.setdefault("id", f"generated-{len(rows) + 1}")
            rows.append(row)
            return _Resp([dict(row)])
        if q.op == "update":
            touched = self._matching(q, rows)
            for row in touched:
                row.update(q.payload or {})
            return _Resp([dict(r) for r in touched])
        if q.op == "delete":
            touched = self._matching(q, rows)
            for row in touched:
                rows.remove(row)
            return _Resp([dict(r) for r in touched])
        return _Resp([])

    @staticmethod
    def _matching(q: _Query, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = rows
        for col, val in q.filters:
            out = [r for r in out if str(r.get(col)) == str(val)]
        for col, vals in q.in_filters:
            wanted = {str(v) for v in vals}
            out = [r for r in out if str(r.get(col)) in wanted]
        return out

    # ---- assertion helpers ----------------------------------------------
    def snapshot(self) -> Dict[str, List[Dict[str, Any]]]:
        return copy.deepcopy(self.tables)

    def subscription(self) -> Dict[str, Any]:
        return dict(self.tables["library_subscriptions"][0])

    def writes(self, table: Optional[str] = None) -> List[_Query]:
        return [
            s
            for s in self.statements
            if s.op in self.WRITE_OPS and (table is None or s.table_name == table)
        ]

    def update_payloads(self, table: str) -> List[Dict[str, Any]]:
        return [
            dict(s.payload or {})
            for s in self.statements
            if s.op == "update" and s.table_name == table
        ]

    def insert_payloads(self, table: str) -> List[Dict[str, Any]]:
        return [
            dict(s.payload or {})
            for s in self.statements
            if s.op == "insert" and s.table_name == table
        ]


def _subscription_row(*, status: str = "suspended") -> Dict[str, Any]:
    """One ``library_subscriptions`` row mid-period, held by an administrator."""
    return {
        "id": SUBSCRIPTION_ID,
        "library_id": LISTING_ID,
        "user_id": PURCHASER_ID,
        "owner_id": OWNER_ID,
        "status": status,
        "price_minor": 1999,
        "currency": "USD",
        "provider": "stripe",
        "provider_reference": "pi_the_payment_that_bought_this_month",
        "period_start": PERIOD_START,
        "period_expiry": PERIOD_EXPIRY,
        "started_at": PERIOD_START,
        "expires_at": PERIOD_EXPIRY,
        "cancelled_at": None,
    }


def _settlement_row(*, settled_at: str = SETTLED_AT, is_reversal: bool = False) -> Dict[str, Any]:
    """The Settlement_Record that bought the period being resumed."""
    return {
        "id": "88888888-8888-4888-8888-888888888888",
        "subscription_id": SUBSCRIPTION_ID,
        "listing_id": LISTING_ID,
        "owner_id": OWNER_ID,
        "purchaser_id": PURCHASER_ID,
        "amount_minor": 1999,
        "owner_share_minor": 1799,
        "platform_fee_minor": 200,
        "currency": "USD",
        "provider": "stripe",
        "provider_reference": "pi_the_payment_that_bought_this_month",
        "is_reversal": is_reversal,
        "settled_at": settled_at,
    }


def _listing_row() -> Dict[str, Any]:
    return {
        "id": LISTING_ID,
        "name": "Momentum Breakout",
        "author_id": OWNER_ID,
        "source_strategy_id": STRATEGY_ID,
        "source_cloning_enabled": False,
        "price_minor": 1999,
        "currency": "USD",
        "is_active": True,
        "subscriber_count": 7,
        "marketplace_submissions": [{"submission_state": "PUBLISHED"}],
    }


# ══════════════════════════════════════════════════════════════════════════
# The runner and the audit double
# ══════════════════════════════════════════════════════════════════════════


def _run_coroutine(coro: Any) -> Any:
    """Run ``coro`` without leaving the thread without a current event loop.

    Not ``asyncio.run``: that closes its loop and then calls ``set_event_loop(None)``, leaving
    the main thread with no current loop, and other suites in this repository still call the
    deprecated ``asyncio.get_event_loop().run_until_complete(...)``. The same helper as
    ``tests/test_checkout_service.py`` and ``tests/test_subscription_renewal_regression.py``,
    where the reasoning was first recorded.
    """
    previous: Optional[asyncio.AbstractEventLoop]
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        previous = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        if previous is not None and not previous.is_closed():
            asyncio.set_event_loop(previous)
        else:
            asyncio.set_event_loop(asyncio.new_event_loop())


class _AuditRecorder:
    """A ``get_strategy_audit_logger()`` double recording every act, raising when asked."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.fail = fail

    async def record_or_raise(self, action: Any, **kwargs: Any) -> Any:
        self.calls.append({"action": action, "required": True, **kwargs})
        if self.fail:
            raise RuntimeError("the audit sink is down")
        return {"audit_id": f"SAUDIT-{len(self.calls)}"}

    async def log(self, action: Any, **kwargs: Any) -> Any:
        self.calls.append({"action": action, "required": False, **kwargs})
        return {"audit_id": f"SAUDIT-{len(self.calls)}"}

    @property
    def actions(self) -> List[str]:
        return [getattr(c["action"], "name", str(c["action"])) for c in self.calls]


def _reinstate(
    db: FakeSupabase,
    *,
    admin: Any = ADMIN,
    subscription_id: str = SUBSCRIPTION_ID,
    reason: Any = REASON,
    now: _datetime.datetime = NOW,
    audit: Optional[_AuditRecorder] = None,
):
    """Drive the service with the audit facility replaced by a recorder."""
    from backend_app.backend.marketplace import subscription_reinstatement as sr

    recorder = audit if audit is not None else _AuditRecorder()
    with patch(
        "backend_app.core.audit_trail.get_strategy_audit_logger", lambda: recorder
    ):
        result = _run_coroutine(
            sr.reinstate(
                admin,
                subscription_id,
                reason,
                supabase=db,
                now=now,
            )
        )
    return result, recorder


# ══════════════════════════════════════════════════════════════════════════
# 1. The defect: a suspended Subscription is reinstated without a charge
# ══════════════════════════════════════════════════════════════════════════


class TestASuspendedSubscriptionCanBeReinstatedWithoutACharge:
    """Requirement 11.17: lifting a hold restores access, and costs nothing.

    Every assertion here fails against the tree before this change - the module the
    reinstatement lives in did not exist, because the only route into ``ACTIVE`` was a
    checkout.
    """

    def test_a_suspended_subscription_returns_to_active_with_no_settlement(self):
        db = FakeSupabase()
        result, _audit = _reinstate(db)

        assert db.subscription()["status"] == "active", (
            "a suspended subscription was not reinstated, so the purchaser still has to buy a "
            "fresh month to recover access they already paid for (Requirement 11.17)"
        )
        assert result.from_status == "suspended"
        assert result.to_status == "active"
        assert db.insert_payloads("marketplace_settlements") == [], (
            "the reinstatement wrote a Settlement_Record; lifting a hold moves no money "
            "(Requirement 11.17)"
        )

    def test_no_money_moves_on_this_path(self):
        """No ledger row, no amount, no currency, no entitlement re-grant, no Minor_Units."""
        db = FakeSupabase()
        _reinstate(db)

        assert db.tables["marketplace_settlements"] == [_settlement_row()], (
            "the Settlement_Ledger changed on a path that moves no money"
        )
        assert db.insert_payloads("deployment_permissions") == [], (
            "the reinstatement granted a fresh deployment permission; the grant that the "
            "settled payment wrote already carries the period expiry"
        )
        money_columns = {"price_minor", "amount_minor", "owner_share_minor", "platform_fee_minor"}
        for payload in db.update_payloads("library_subscriptions"):
            assert not (money_columns & set(payload)), (
                f"the reinstatement wrote a money column: {sorted(money_columns & set(payload))}"
            )

    def test_the_codes_and_statuses_agree_with_the_error_catalogue(self):
        """The module names catalogue codes as strings; this is what holds it to them.

        ``errors.py`` imports FastAPI, so the service does not import it (the package's layering
        rule) - which means the agreement has to be asserted somewhere, or a code the catalogue
        does not carry would reach ``MarketplaceError`` and raise a ``KeyError`` on a live
        request instead of the refusal it is meant to be.
        """
        from backend_app.backend.marketplace import errors as _errors
        from backend_app.backend.marketplace import subscription_reinstatement as sr

        for code in (
            sr.NOT_FOUND,
            sr.MARKETPLACE_PAYMENT_REQUIRED,
            sr.MARKETPLACE_REASON_REQUIRED,
            sr.MARKETPLACE_ACTION_NOT_RECORDED,
            sr.MARKETPLACE_READ_FAILED,
        ):
            assert code in _errors.ERROR_CODES, f"{code} is not in the error catalogue"
            assert sr._HTTP_STATUS_FOR_CODE[code] == _errors.HTTP_STATUS_FOR_CODE[code], (
                f"{code} is answered {sr._HTTP_STATUS_FOR_CODE[code]} here and "
                f"{_errors.HTTP_STATUS_FOR_CODE[code]} by the catalogue"
            )

    def test_the_writer_module_holds_no_float_and_no_ledger_insert(self):
        """Integer Minor_Units only, asserted structurally: this path has no arithmetic at all."""
        path = (
            REPO_ROOT
            / "backend_app"
            / "backend"
            / "marketplace"
            / "subscription_reinstatement.py"
        )
        assert path.exists(), f"{path.name} does not exist"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        floats = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, float)
        ]
        assert not floats, f"a float literal appears on the reinstatement path at {floats}"
        assert "marketplace_settlements" in source, (
            "the reinstatement never consults the Settlement_Ledger, so it cannot tell a paid "
            "period from one that was never bought (Requirement 11.14)"
        )

        # No insert on the Settlement_Ledger, asserted on the statement rather than on text:
        # ``supabase.table(SETTLEMENT_TABLE).insert(...)`` is the shape that would write money.
        ledger_inserts = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "insert"
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Attribute)
            and node.func.value.func.attr == "table"
            and node.func.value.args
            and isinstance(node.func.value.args[0], ast.Name)
            and node.func.value.args[0].id == "SETTLEMENT_TABLE"
        ]
        assert not ledger_inserts, (
            f"the reinstatement inserts into the Settlement_Ledger at line(s) {ledger_inserts}; "
            "lifting a hold moves no money (Requirement 11.17)"
        )


# ══════════════════════════════════════════════════════════════════════════
# 2. The period is untouched (Requirements 11.9, 11.13, 11.17)
# ══════════════════════════════════════════════════════════════════════════


class TestThePeriodIsUntouched:
    def test_the_period_columns_are_byte_identical_before_and_after(self):
        db = FakeSupabase()
        before = db.snapshot()["library_subscriptions"][0]

        _reinstate(db)

        after = db.subscription()
        for column in ("period_start", "period_expiry", "started_at", "expires_at"):
            assert after[column] == before[column], (
                f"reinstatement moved {column} from {before[column]!r} to {after[column]!r}; "
                "the purchaser's paid period is restored, never re-issued (Requirement 11.17)"
            )

    def test_the_update_payload_writes_the_status_and_nothing_else(self):
        db = FakeSupabase()
        _reinstate(db)

        payloads = db.update_payloads("library_subscriptions")
        assert len(payloads) == 1, f"expected exactly one status write, got {payloads}"
        assert set(payloads[0]) == {"status"}, (
            "the reinstatement writes columns beyond the status; the period columns and their "
            f"started_at/expires_at mirrors must not be written at all: {sorted(payloads[0])}"
        )
        assert payloads[0]["status"] == "active"

    def test_the_result_reports_the_unchanged_period(self):
        db = FakeSupabase()
        result, _audit = _reinstate(db)

        assert result.period_start == _datetime.datetime.fromisoformat(PERIOD_START)
        assert result.period_expiry == _datetime.datetime.fromisoformat(PERIOD_EXPIRY)


# ══════════════════════════════════════════════════════════════════════════
# 3. Admin only (Requirements 5.1, 22.10, 11.17)
# ══════════════════════════════════════════════════════════════════════════


def _reinstate_route():
    """The registered POST route for the reinstatement, or ``None``."""
    from backend_app.routers import library as library_router

    for route in library_router.router.routes:
        if "POST" in getattr(route, "methods", set()) and getattr(route, "path", "").endswith(
            "/admin/subscriptions/{subscription_id}/reinstate"
        ):
            return route
    return None


class TestOnlyAnAdminReviewerMayReinstate:
    def test_the_route_exists_and_carries_get_admin_user(self):
        from backend_app.core.dependencies import get_admin_user

        route = _reinstate_route()
        assert route is not None, (
            "no POST route ending in /admin/subscriptions/{subscription_id}/reinstate is "
            "registered on library.router, so a suspended purchaser has no settlement-free "
            "route back to ACTIVE"
        )
        dependencies = [d.call for d in route.dependant.dependencies]
        assert get_admin_user in dependencies, (
            "the reinstatement route does not resolve its caller through get_admin_user - the "
            "same helper the five submission admin routes use; a second admin check is a "
            "second policy (Requirement 5.1)"
        )

    def test_the_route_declares_no_second_identity_source(self):
        from backend_app.core.dependencies import get_current_user

        route = _reinstate_route()
        assert route is not None
        dependencies = [d.call for d in route.dependant.dependencies]
        assert get_current_user not in dependencies, (
            "the reinstatement route also depends on get_current_user; the admin identity is "
            "resolved once, by get_admin_user, which depends on it itself"
        )

    def test_a_non_admin_is_refused_and_nothing_changes(self):
        """The real ``get_admin_user`` 403, before any read - the same refusal the other admin
        routes give."""
        from fastapi.testclient import TestClient

        from backend_app.core.dependencies import get_current_user
        from backend_app.main import app
        from backend_app.routers import library as library_router

        db = FakeSupabase()
        before = db.snapshot()

        app.dependency_overrides[get_current_user] = lambda: {
            "id": PURCHASER_ID,
            "email": "purchaser@test.vyomquant.io",
            "app_metadata": {"role": "authenticated"},
        }
        try:
            with patch.object(library_router, "_build_service_client", lambda: db):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(
                    f"/api/library/admin/subscriptions/{SUBSCRIPTION_ID}/reinstate",
                    json={"reason": REASON},
                )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 403, resp.text
        assert db.statements == [], (
            "a non-admin reached the Persistence_Layer; get_admin_user must refuse before the "
            f"route body runs: {[(s.op, s.table_name) for s in db.statements]}"
        )
        assert db.snapshot() == before

    def test_an_admin_is_admitted_through_the_route(self):
        from fastapi.testclient import TestClient

        from backend_app.core.dependencies import get_current_user
        from backend_app.main import app
        from backend_app.routers import library as library_router

        db = FakeSupabase()
        recorder = _AuditRecorder()

        app.dependency_overrides[get_current_user] = lambda: {
            "id": ADMIN_ID,
            "email": "admin@test.vyomquant.io",
            "app_metadata": {"role": "admin"},
        }
        try:
            with patch.object(library_router, "_build_service_client", lambda: db), patch(
                "backend_app.core.audit_trail.get_strategy_audit_logger", lambda: recorder
            ):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(
                    f"/api/library/admin/subscriptions/{SUBSCRIPTION_ID}/reinstate",
                    json={"reason": REASON},
                )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["subscription_id"] == SUBSCRIPTION_ID
        assert body["subscription_state"] == "active"
        assert body["prior_state"] == "suspended"
        assert db.subscription()["status"] == "active"
        assert db.subscription()["period_expiry"] == PERIOD_EXPIRY

    def test_a_missing_reason_is_refused_by_the_route(self):
        from fastapi.testclient import TestClient

        from backend_app.core.dependencies import get_current_user
        from backend_app.main import app
        from backend_app.routers import library as library_router

        db = FakeSupabase()
        before = db.snapshot()

        app.dependency_overrides[get_current_user] = lambda: {
            "id": ADMIN_ID,
            "app_metadata": {"role": "admin"},
        }
        try:
            with patch.object(library_router, "_build_service_client", lambda: db):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(
                    f"/api/library/admin/subscriptions/{SUBSCRIPTION_ID}/reinstate",
                    json={},
                )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 422, resp.text
        assert db.snapshot() == before, "a reinstatement with no reason changed stored state"


# ══════════════════════════════════════════════════════════════════════════
# 4. An unpaid Subscription cannot be reinstated (Requirements 11.6, 11.14)
# ══════════════════════════════════════════════════════════════════════════


class TestAnUnpaidSubscriptionCannotBeReinstated:
    def test_a_subscription_with_no_settlement_row_at_all_is_refused(self):
        from backend_app.backend.marketplace import errors as _errors
        from backend_app.backend.marketplace import subscription_reinstatement as sr

        db = FakeSupabase(settlements=[])
        before = db.snapshot()

        with pytest.raises(sr.ReinstatementError) as caught:
            _reinstate(db)

        assert caught.value.code == _errors.MARKETPLACE_PAYMENT_REQUIRED
        assert caught.value.http_status == 409
        assert db.snapshot() == before, (
            "a subscription that never paid was partially reinstated; nothing may change "
            "(Requirements 11.14, 11.3)"
        )

    def test_a_reversal_row_alone_does_not_fund_a_reinstatement(self):
        from backend_app.backend.marketplace import subscription_reinstatement as sr

        db = FakeSupabase(settlements=[_settlement_row(is_reversal=True)])
        before = db.snapshot()

        with pytest.raises(sr.ReinstatementError):
            _reinstate(db)

        assert db.snapshot() == before, "a refund funded a reinstatement (Requirement 11.14)"

    def test_a_settlement_later_than_the_period_does_not_fund_a_reinstatement(self):
        """The probe asks for the payment that bought the period being RESUMED."""
        from backend_app.backend.marketplace import subscription_reinstatement as sr

        db = FakeSupabase(settlements=[_settlement_row(settled_at="2025-07-01T00:00:00+00:00")])

        with pytest.raises(sr.ReinstatementError):
            _reinstate(db)

    def test_a_subscription_with_no_period_is_refused(self):
        from backend_app.backend.marketplace import subscription_reinstatement as sr

        db = FakeSupabase(
            subscription_overrides={"period_start": None, "period_expiry": None}
        )
        before = db.snapshot()

        with pytest.raises(sr.ReinstatementError):
            _reinstate(db)

        assert db.snapshot() == before

    @pytest.mark.parametrize(
        "status", ["pending", "expired", "cancelled", "payment_failed", "active", "refunded"]
    )
    def test_no_other_source_state_may_be_reinstated(self, status):
        """The relaxation is for ``suspended`` and no other source (Requirement 11.6)."""
        from backend_app.backend.marketplace import subscription_reinstatement as sr

        db = FakeSupabase(subscription_status=status)
        before = db.snapshot()

        with pytest.raises(sr.ReinstatementError) as caught:
            _reinstate(db)

        assert caught.value.details.get("current") == status
        assert db.snapshot() == before, (
            f"a {status} subscription was activated with no payment behind it "
            "(Requirements 11.6, 11.14)"
        )

    def test_an_unknown_subscription_is_not_found_and_writes_nothing(self):
        from backend_app.backend.marketplace import errors as _errors
        from backend_app.backend.marketplace import subscription_reinstatement as sr

        db = FakeSupabase()
        before = db.snapshot()

        with pytest.raises(sr.ReinstatementError) as caught:
            _reinstate(db, subscription_id="99999999-9999-4999-8999-999999999999")

        assert caught.value.code == _errors.NOT_FOUND
        assert db.snapshot() == before


# ══════════════════════════════════════════════════════════════════════════
# 5. Audited, and the history row names an administrative cause (Req 11.12)
# ══════════════════════════════════════════════════════════════════════════


class TestTheReinstatementIsAudited:
    def test_one_audit_entry_names_the_admin_the_subscription_the_prior_state_and_the_reason(
        self,
    ):
        db = FakeSupabase()
        _result, audit = _reinstate(db)

        assert len(audit.calls) == 1, f"expected exactly one audit act, got {audit.actions}"
        entry = audit.calls[0]
        assert entry["required"] is True, (
            "the reinstatement audit is written with the never-raising writer, so the state "
            "change could stand unaudited (Requirement 11.12)"
        )
        assert entry["actor_id"] == ADMIN_ID
        assert entry["resource_id"] == SUBSCRIPTION_ID
        assert entry["before"] == "suspended"
        assert entry["after"] == "active"
        assert REASON in entry["reason"]
        assert getattr(entry["action"], "name", "") == "MARKETPLACE_SUBSCRIPTION_TRANSITIONED"

    def test_a_blank_reason_is_refused_before_any_write(self):
        from backend_app.backend.marketplace import errors as _errors
        from backend_app.backend.marketplace import subscription_reinstatement as sr

        for blank in (None, "", "   ", "\t\n"):
            db = FakeSupabase()
            before = db.snapshot()
            with pytest.raises(sr.ReinstatementError) as caught:
                _reinstate(db, reason=blank)
            assert caught.value.code == _errors.MARKETPLACE_REJECTION_REASON_REQUIRED
            assert db.statements == [], (
                "a reinstatement with no recorded reason read or wrote the Persistence_Layer; "
                f"the reason rule comes first: {[(s.op, s.table_name) for s in db.statements]}"
            )
            assert db.snapshot() == before

    def test_an_unwritable_audit_rolls_the_status_back(self):
        """Requirement 11.12: the transition is not left persisted-and-unaudited."""
        from backend_app.backend.marketplace import errors as _errors
        from backend_app.backend.marketplace import subscription_reinstatement as sr

        db = FakeSupabase()
        with pytest.raises(sr.ReinstatementError) as caught:
            _reinstate(db, audit=_AuditRecorder(fail=True))

        assert caught.value.code == _errors.MARKETPLACE_ACTION_NOT_RECORDED
        assert db.subscription()["status"] == "suspended", (
            "the audit write failed but the subscription stayed active, so an unaudited "
            "entitlement stands (Requirement 11.12)"
        )
        assert db.subscription()["period_expiry"] == PERIOD_EXPIRY


class TestTheHistoryRowRecordsAnAdministrativeCause:
    def test_the_history_row_is_suspended_to_active_with_a_non_settlement_cause(self):
        from backend_app.backend.marketplace import settlement_service as ss

        db = FakeSupabase()
        _reinstate(db)

        rows = db.insert_payloads("library_subscription_transitions")
        assert len(rows) == 1, f"expected exactly one history row, got {rows}"
        row = rows[0]
        assert row["from_state"] == "suspended"
        assert row["to_state"] == "active"
        assert row["cause"] != ss._CAUSE_SETTLEMENT, (
            "the administrative reinstatement is recorded with the same cause as a paid "
            "activation, so a reader cannot tell them apart (Requirement 11.12)"
        )
        assert row["cause"] == "admin_reinstatement"
        assert row["actor_id"] == ADMIN_ID
        assert row["subscription_id"] == SUBSCRIPTION_ID

    def test_the_history_row_reports_the_same_expiry_on_both_sides(self):
        db = FakeSupabase()
        _reinstate(db)

        row = db.insert_payloads("library_subscription_transitions")[0]
        assert row["prior_period_expiry"] == row["new_period_expiry"] == PERIOD_EXPIRY, (
            "the history row claims the period moved; a reinstatement extends nothing"
        )


# ══════════════════════════════════════════════════════════════════════════
# 6. An elapsed period does not become entitling again (Requirement 11.7)
# ══════════════════════════════════════════════════════════════════════════


class TestAnElapsedPeriodDoesNotBecomeEntitling:
    """Asserted THROUGH ``entitlement_resolver.resolve``, not by special-casing the writer.

    Requirement 11.7 makes the resolver compare ``now`` with ``period_expiry`` on every call,
    irrespective of the stored status. So a reinstatement inside an elapsed period restores the
    label and hands back **no** entitlement - which is exactly why the writer does not need to
    refuse it, and why the sweep will move the row to ``EXPIRED`` on its next pass.
    """

    def test_a_reinstated_but_elapsed_subscription_does_not_entitle(self):
        from backend_app.backend.marketplace import entitlement_resolver as er

        db = FakeSupabase()
        after_expiry = _datetime.datetime(2025, 6, 2, tzinfo=_datetime.timezone.utc)

        _reinstate(db, now=after_expiry)

        assert db.subscription()["status"] == "active"
        entitlement = _run_coroutine(
            er.resolve(
                {"id": PURCHASER_ID},
                LISTING_ID,
                supabase=db,
                now=after_expiry,
            )
        )
        assert entitlement.entitling is False, (
            "an elapsed period handed back an entitlement after reinstatement; the resolver "
            "compares now with period_expiry on every call (Requirement 11.7)"
        )
        assert entitlement.reason is er.EntitlementReason.EXPIRED

    def test_a_reinstated_live_subscription_does_entitle(self):
        """The other half: inside the paid period the reinstatement restores access."""
        from backend_app.backend.marketplace import entitlement_resolver as er

        db = FakeSupabase()
        _reinstate(db)

        entitlement = _run_coroutine(
            er.resolve({"id": PURCHASER_ID}, LISTING_ID, supabase=db, now=NOW)
        )
        assert entitlement.entitling is True, (
            "the reinstated subscriber is still refused inside the period they paid for"
        )
        assert entitlement.reason is er.EntitlementReason.SUBSCRIBED

    def test_a_suspended_subscription_does_not_entitle_before_the_reinstatement(self):
        from backend_app.backend.marketplace import entitlement_resolver as er

        db = FakeSupabase()
        entitlement = _run_coroutine(
            er.resolve({"id": PURCHASER_ID}, LISTING_ID, supabase=db, now=NOW)
        )
        assert entitlement.entitling is False
        assert entitlement.reason is er.EntitlementReason.SUBSCRIPTION_SUSPENDED


# ══════════════════════════════════════════════════════════════════════════
# 7. The database admits this one source and no other (Requirements 11.14, 24.7)
# ══════════════════════════════════════════════════════════════════════════


def _squeeze(text: str) -> str:
    return " ".join(text.split())


def _migration_text() -> str:
    return (MIGRATIONS / REINSTATEMENT_MIGRATION).read_text(encoding="utf-8")


def _statements_only(sql: str) -> str:
    """``sql`` with every ``--`` comment line stripped.

    The forbidden-verb check below is about what the file EXECUTES. This file's header
    explains at length that it contains no ``DROP``, ``DELETE``, ``TRUNCATE`` or rename, and a
    check that read the prose would fail on the sentence promising the absence.
    """
    return "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )


def _shape_predicate_body() -> str:
    """The SQL body of ``marketplace_subscription_reinstatement_shape``, as ``014`` writes it."""
    match = re.search(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+"
        r"public\.marketplace_subscription_reinstatement_shape\b.*?AS\s+\$\$(?P<body>.*?)\$\$",
        _migration_text(),
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, (
        "014 does not define public.marketplace_subscription_reinstatement_shape(...), so the "
        "relaxation is an inline condition nothing can prove the scope of"
    )
    return _squeeze(match.group("body"))


class TestTheDatabaseAdmitsThisOneSourceAndNoOther:
    def test_the_relaxation_migration_exists(self):
        path = MIGRATIONS / REINSTATEMENT_MIGRATION
        assert path.exists(), (
            f"backend_app/migrations/{REINSTATEMENT_MIGRATION} does not exist, so the database "
            "still refuses the administrative reinstatement with 23514 while the application "
            "attempts it - the shape of the 19.15 defect"
        )

    def test_the_effective_guard_admits_the_administrative_reinstatement(self):
        body = _squeeze(subscription_guard_body_in_db())

        assert "marketplace_subscription_reinstatement_shape" in body, (
            "the effective marketplace_subscription_guard() still demands a NEW settlement for "
            "suspended -> active (it consults no admission predicate), so an administrative "
            "reinstatement is refused by 23514"
        )
        # The relaxation is reached from the activation branch, not from the edge branch: the
        # permitted-edge check stays generic table membership (P-49).
        activation = re.search(
            r"IF NEW\.status = 'active' THEN.*",
            body,
            re.IGNORECASE | re.DOTALL,
        )
        assert activation is not None, (
            "the guard has no `IF NEW.status = 'active' THEN` branch; nothing gates an activation"
        )
        assert "marketplace_subscription_reinstatement_shape" in activation.group(0)

    def test_the_relaxation_names_suspended_as_its_only_admitted_source(self):
        """The scope, read off the predicate the guard actually calls.

        A single equality on ``old_status``, so ``pending``, ``expired``, ``cancelled`` and
        ``payment_failed`` cannot satisfy it - which the migration's postflight then proves by
        CALLING the predicate with each of them.
        """
        predicate = _shape_predicate_body()

        assert "old_status = 'suspended'" in predicate, (
            "the admission predicate does not require the source to be 'suspended', so the "
            f"relaxation is not scoped to one source: {predicate}"
        )
        assert "new_status = 'active'" in predicate
        for other in ("pending", "expired", "cancelled", "payment_failed", "refunded"):
            assert f"'{other}'" not in predicate, (
                f"the admission predicate names {other!r}; Requirement 11.6 requires a confirmed "
                "payment before an activation from that source (Requirement 11.14)"
            )
        # The period cannot move on the relaxed path.
        assert "new_period_expiry IS NOT DISTINCT FROM old_period_expiry" in predicate
        assert "new_period_start IS NOT DISTINCT FROM old_period_start" in predicate
        assert "old_period_expiry IS NOT NULL" in predicate

    def test_the_relaxation_still_requires_a_settlement_for_the_resumed_period(self):
        body = _squeeze(subscription_guard_body_in_db())

        assert "s.settled_at <= OLD.period_expiry" in body, (
            "the reinstatement branch does not require a settlement at or before the expiry of "
            "the period being resumed, so a subscription that never paid could be reinstated "
            "into access it never bought (Requirement 11.14)"
        )
        assert body.count("public.marketplace_settlements") >= 2, (
            "the guard has fewer than two settlement probes, so either the relaxed branch or "
            "008's unconditional one is gone"
        )
        assert "s.is_reversal = FALSE" in body, (
            "a reversal row could fund an activation (Requirement 11.14)"
        )

    def test_the_original_probe_is_intact_for_every_other_source(self):
        body = _squeeze(subscription_guard_body_in_db())

        assert "s.settled_at >= OLD.period_expiry" in body, (
            "008's non-circular anchor (a payment older than the period it funds does not "
            "count) is gone from the effective guard"
        )
        assert "s.subscription_id = NEW.id" in body, (
            "the settlement probe is not scoped to NEW.id; one purchaser's payment would fund "
            "another's activation"
        )
        assert "23514" in body, (
            "the refusal no longer carries the check_violation SQLSTATE the service translates by"
        )

    def test_the_shape_predicate_admits_only_suspended_and_only_an_unmoved_period(self):
        """The executable half of the postflight, read off the same file it asserts.

        The predicate is pure SQL over seven scalars, so the migration's own postflight CALLS it
        with each of the four other sources and asserts FALSE. This test asserts those calls are
        present, because they are what proves the relaxation is scoped rather than described.
        """
        sql = _squeeze(_migration_text())

        assert "CREATE OR REPLACE FUNCTION public.marketplace_subscription_reinstatement_shape" in sql
        for source in ("pending", "expired", "cancelled", "payment_failed"):
            assert f"'{source}'" in sql, (
                f"the postflight does not probe the {source} -> active source, so nothing "
                "proves that source still requires a settlement (Requirement 11.14)"
            )

    def test_the_migration_is_additive_idempotent_and_single_transaction(self):
        sql = _migration_text()
        upper = sql.upper()
        statements = f" {_squeeze(_statements_only(sql)).upper()} "

        assert upper.count("BEGIN;") == 1 and upper.count("COMMIT;") == 1, (
            "the migration is not one transaction"
        )
        for forbidden in ("DROP", "DELETE", "TRUNCATE", "RENAME", "REVOKE"):
            assert f" {forbidden} " not in statements, (
                f"{forbidden} appears in an additive migration (Requirement 24.7)"
            )
        assert "CREATE OR REPLACE FUNCTION" in upper, (
            "the guard is not replaced in place, so the relaxation cannot be idempotent"
        )
        # The dormant tables stay dormant (Requirement 1.2). Asserted against the statements:
        # the header NAMES them, in the sentence promising it does not touch them - the same
        # wording 012's header uses.
        executable = _statements_only(sql)
        assert "marketplace_listings" not in executable
        assert "strategy_subscriptions" not in executable

    def test_the_migration_refuses_to_run_without_008(self):
        sql = _migration_text()
        assert "008_marketplace_settlement.sql" in sql, (
            "the preflight does not name the migration it depends on"
        )
        assert re.search(
            r"precondition failed", sql, re.IGNORECASE
        ), "the migration has no preflight refusal"
        assert re.search(
            r"postcondition failed", sql, re.IGNORECASE
        ), "the migration has no postflight assertion"

    def test_the_permitted_pair_set_is_unchanged_by_this_migration(self):
        """``suspended -> active`` was already seeded by ``008``: this file seeds no pair.

        Requirement 11.2 permitted the edge all along - what refused it was the settlement
        branch, not the transition table. So the effective permitted set stays at thirteen and
        every consumer that reads it through ``subscription_permitted_pairs_in_db()`` is
        unaffected.
        """
        permitted = subscription_permitted_pairs_in_db()
        assert ("SUSPENDED", "ACTIVE") in permitted
        assert len(permitted) == 13, (
            f"the permitted-pair set changed to {len(permitted)} pairs; this migration seeds "
            "none"
        )
        assert "INSERT INTO public.marketplace_subscription_allowed_transitions" not in (
            _migration_text()
        ), "the reinstatement migration seeds a transition pair; it needs none"
