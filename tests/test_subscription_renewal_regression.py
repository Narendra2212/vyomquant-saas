"""
tests/test_subscription_renewal_regression.py - the renewal that took no payment.

Spec: marketplace-subscriptions-paper-trading task 19.4. Requirements 11.6, 11.14, 11.16.

THE DEFECT THIS FILE IS RECORDED AGAINST
----------------------------------------
``POST /api/library/subscriptions/{sub_id}/renew`` used to renew a Subscription by *writing* it::

    update_resp = svc.table("library_subscriptions").update({
        "status": "active",
        "cancelled_at": None,
        "expires_at": None
    }).eq("id", sub_uid).in_("status", ["cancelled", "expired"]).execute()
    ...
    grant_deployment_permission(user_id, lib_id, "subscription", sub_uid)
    ...
    svc.table("library_strategies").update(
        {"subscriber_count": current + 1, "updated_at": now}
    ).eq("id", lib_id).execute()

No provider was contacted. No amount was ever computed. No ``marketplace_settlements`` row was
written. And ``expires_at`` was set to **NULL**, which ``check_deployment_permission`` read as a
*perpetual* entitlement. So one unpriced, unauthenticated-beyond-a-token POST by anybody who had
subscribed once and then cancelled converted a lapsed monthly Subscription into permanent free
access, complete with a ``deployment_permissions`` row to execute the owner's strategy with.

Requirement 11.16 orders that path removed. Requirements 11.6 and 11.14 require a payment
confirmed through the Billing_Integration and recorded as a Settlement_Record before **every**
transition into ``ACTIVE``.

WHAT THIS FILE ASSERTS, AND WHERE EACH ASSERTION BITES
------------------------------------------------------
:class:`TestTheFreeActivationIsGone` - the four deleted writes, asserted as absences against the
    real handler driven over a recording fake. Each of these FAILS against the pre-19.3 body.
:class:`TestTheRenewalIsACheckout` - the endpoint now returns a provider session for the renewal
    amount and **changes no stored state at all**, asserted by deep-comparing every table before
    and after and by counting write statements (zero).
:class:`TestTheDatabaseRefusesAnUnpaidActivation` - the belt to the application's braces: even a
    direct ``UPDATE library_subscriptions SET status='active'`` is refused by
    ``trg_subscription_transition_guard`` unless a matching non-reversal
    ``marketplace_settlements`` row exists.

HOW THE FAILING-FIRST EVIDENCE WAS TAKEN
----------------------------------------
A regression test that passes against the unfixed code is evidence of nothing. Before task 19.3
was applied, the three edits it makes were reverted in a scratch copy of the working tree (the
pre-fix ``renew_subscription`` body quoted above, the ``cancel_subscription`` revoke block, and
the router's own ``grant_deployment_permission``) and this file was run against it:

    python -m pytest tests/test_subscription_renewal_regression.py -q --no-header -p no:randomly
    ==> 23 failed, 7 passed

with, among them:

    TestTheFreeActivationIsGone::test_a_renewal_writes_no_status_active
        AssertionError: renew_subscription still writes status='active' with no confirmed
        payment behind it (Requirements 11.6, 11.14, 11.16);
        payload(s): [{'status': 'active', 'cancelled_at': None, 'expires_at': None}]
    TestTheFreeActivationIsGone::test_a_renewal_writes_no_null_expiry
        AssertionError: renew_subscription still writes 'expires_at' ... 'expires_at': None
    TestTheFreeActivationIsGone::test_a_renewal_grants_no_deployment_permission
        AssertionError: renew_subscription still inserted 1 deployment_permissions row(s)
        with no payment behind them ... 'expires_at': None
    TestTheFreeActivationIsGone::test_a_renewal_does_not_increment_subscriber_count
        AssertionError: renew_subscription still increments subscriber_count before any
        payment is confirmed (Requirement 11.16): [{'subscriber_count': 8, ...}]
    TestTheFreeActivationIsGone::test_the_router_defines_no_second_grant_deployment_permission
        AssertionError: routers/library.py still defines grant_deployment_permission
    TestTheFreeActivationIsGone::test_a_cancellation_revokes_no_permission_and_moves_no_expiry
        AssertionError: cancel_subscription still writes deployment_permissions ...
        statements: [('update', {'is_active': False, 'revoked_at': ...})]
    TestTheRenewalIsACheckout::test_a_renewal_creates_a_provider_session
        AssertionError: the renewal contacted no payment provider ...
        assert [] == ['stripe']
    TestTheRenewalIsACheckout::test_the_renewal_changes_no_stored_state
        AssertionError: a renewal request issued write statement(s) [('update',
        'library_subscriptions', {'status': 'active', 'cancelled_at': None,
        'expires_at': None}), ('insert', 'deployment_permissions', {...}),
        ('update', 'library_strategies', {'subscriber_count': 8, ...})]
    TestTheDatabaseRefusesAnUnpaidActivation
        ::test_the_application_does_not_reach_active_on_its_own_either
        AssertionError: a module outside settlement_service writes status='active'
        directly ... ['backend_app/routers/library.py:3172']

The same command against the fixed tree is 30 passed. Both runs are recorded in the task report.

WHY THE GUARD IS READ STATICALLY
--------------------------------
There is no PostgreSQL in this environment, so the database half is asserted by reading the
migration off disk - the technique every migration test in this repository uses. It is read
through ``tests/test_submission_state_agreement.py``'s own helpers
(:func:`subscription_permitted_pairs_in_db`, ``_subscription_guard_body``) rather than
transcribed here, so there is exactly one parser for "what does the guard admit today" and this
file cannot drift from it.
"""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from backend_app.backend.marketplace import checkout_service as cs
from backend_app.backend.marketplace.errors import (
    MARKETPLACE_CHECKOUT_UNAVAILABLE,
    MARKETPLACE_LISTING_NOT_PURCHASABLE,
    MARKETPLACE_READ_FAILED,
    NOT_FOUND,
    MarketplaceError,
)
from backend_app.routers import library as library_router

# ``renew_subscription`` (5/60s) and ``cancel_subscription`` (10/60s) both carry
# ``@limiter.limit(..., key_func=caller_or_address)`` since task 33.2 (Requirements 6.7, 22.4), and
# ``slowapi``'s wrapper will not run a handler that was not handed a real
# ``starlette.requests.Request``. Both helpers already exist and are reused rather than
# re-written: one builder for "a real request over a hand-built scope", one reset for "forget what
# the limiter has counted".
from tests.test_task_28_1_session_routes import (  # noqa: E402 - shared machinery
    _reset_rate_limit_counters,
)
from tests.test_task_33_2_rate_limit_keys import (  # noqa: E402 - shared machinery
    _request as _real_request,
)

# The guard parser and the effective permitted-edge set, imported rather than re-written. Task
# 19.4 says to read the machine through these; two parsers for one trigger is how the assertion
# and the artifact come to disagree.
from tests.test_submission_state_agreement import (  # noqa: E402 - shared machinery
    _subscription_guard_body,
    subscription_permitted_pairs_in_db,
)

# ``_safe_uuid`` validates every path parameter, so these have to be real UUIDs.
SUBSCRIPTION_ID = "11111111-1111-4111-8111-111111111111"
LISTING_ID = "22222222-2222-4222-8222-222222222222"
PURCHASER_ID = "33333333-3333-4333-8333-333333333333"
OWNER_ID = "44444444-4444-4444-8444-444444444444"
OTHER_USER_ID = "55555555-5555-4555-8555-555555555555"

CALLER = {"id": PURCHASER_ID}

#: A $19.99 Listing, stored exactly. 1999 Minor_Units; 1799 owner share; 200 platform fee.
PRICE_MINOR_1999 = 1999

#: The period the current (paid) month ends at. Cancellation must not move it, and a renewal must
#: not move it either - only ``settlement_service.settle`` may.
CURRENT_EXPIRY = "2025-06-01T00:00:00+00:00"
CURRENT_START = "2025-05-01T00:00:00+00:00"


# ══════════════════════════════════════════════════════════════════════════
# The recording fake Persistence_Layer
# ══════════════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    """A recording supabase-py-shaped query builder.

    Carries ``in_`` as well as ``eq`` because the pre-fix handler used
    ``.in_("status", ["cancelled", "expired"])`` - a builder that could not express the pre-fix
    statement could not run the pre-fix body, and a regression file that cannot run the code it
    is recorded against proves nothing.
    """

    def __init__(self, table: str, client: "FakeSupabase") -> None:
        self.table_name = table
        self.client = client
        self.op = "select"
        self.payload: Optional[Dict[str, Any]] = None
        self.cols: Optional[str] = None
        self.filters: List[Tuple[str, Any]] = []
        self.in_filters: List[Tuple[str, List[Any]]] = []

    def select(self, cols: str = "*") -> "_Query":
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

    def single(self) -> "_Query":
        return self

    def execute(self) -> Any:
        return self.client._execute(self)


class FakeSupabase:
    """Four mutable tables and a complete statement log.

    The statement log is what makes "changes no stored state" a measurement rather than a claim:
    the provider factory appends ``("provider", name)`` to the same ``ops`` list, so the ordering
    and the absence of writes are read off one sequence.
    """

    WRITE_OPS = frozenset({"insert", "update", "delete"})

    def __init__(
        self,
        *,
        subscription_status: str = "cancelled",
        subscription_user: str = PURCHASER_ID,
        listing_rows: Optional[List[Dict[str, Any]]] = None,
        subscription_rows: Optional[List[Dict[str, Any]]] = None,
        raise_on: Optional[set] = None,
    ) -> None:
        self.tables: Dict[str, List[Dict[str, Any]]] = {
            "library_subscriptions": [dict(r) for r in subscription_rows]
            if subscription_rows is not None
            else [
                _subscription_row(status=subscription_status, user_id=subscription_user)
            ],
            "library_strategies": [dict(r) for r in listing_rows]
            if listing_rows is not None
            else [_listing_row()],
            "deployment_permissions": [],
            "marketplace_settlements": [],
        }
        self.raise_on = raise_on or set()
        self.ops: List[Tuple[str, str]] = []
        self.statements: List[_Query] = []

    # ---- the surface under test uses ------------------------------------
    def table(self, name: str) -> _Query:
        return _Query(name, self)

    def note_provider_call(self, provider: str) -> None:
        self.ops.append(("provider", provider))

    def _execute(self, q: _Query) -> Any:
        self.ops.append((q.op, q.table_name))
        self.statements.append(q)

        if (q.op, q.table_name) in self.raise_on:
            raise RuntimeError(f"{q.op} failed on {q.table_name}")

        rows = self.tables.setdefault(q.table_name, [])
        if q.op == "select":
            return _Resp([dict(r) for r in self._matching(q, rows)])
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

    def subscription(self) -> Dict[str, Any]:
        return dict(self.tables["library_subscriptions"][0])

    def provider_calls(self) -> List[str]:
        return [name for op, name in self.ops if op == "provider"]


def _subscription_row(
    *, status: str = "cancelled", user_id: str = PURCHASER_ID
) -> Dict[str, Any]:
    """One ``library_subscriptions`` row mid-way through its life.

    It carries a *real* period, because the whole point of the defect was that the renewal wiped
    it: ``expires_at`` is the column ``check_deployment_permission`` read, and ``period_expiry``
    is the one the guard compares a settlement against.
    """
    return {
        "id": SUBSCRIPTION_ID,
        "library_id": LISTING_ID,
        "user_id": user_id,
        "owner_id": OWNER_ID,
        "status": status,
        "price_minor": PRICE_MINOR_1999,
        "currency": "USD",
        "owner_share_minor": 1799,
        "platform_fee_minor": 200,
        "provider": "stripe",
        "provider_reference": "pi_the_payment_that_bought_this_month",
        "started_at": CURRENT_START,
        "period_start": CURRENT_START,
        "period_expiry": CURRENT_EXPIRY,
        "expires_at": CURRENT_EXPIRY,
        "cancelled_at": "2025-05-20T00:00:00+00:00" if status == "cancelled" else None,
    }


def _listing_row(
    *,
    price_minor: Optional[int] = PRICE_MINOR_1999,
    currency: str = "USD",
    submission_state: Optional[str] = "PUBLISHED",
) -> Dict[str, Any]:
    submissions = [] if submission_state is None else [{"submission_state": submission_state}]
    return {
        "id": LISTING_ID,
        "name": "Momentum Breakout",
        "author_id": OWNER_ID,
        "price_minor": price_minor,
        "currency": currency,
        "is_active": True,
        "subscriber_count": 7,
        "marketplace_submissions": submissions,
    }


# ══════════════════════════════════════════════════════════════════════════
# Provider doubles
# ══════════════════════════════════════════════════════════════════════════


def _ok_factory(db: FakeSupabase, amounts: List[int], reference: str = "cs_renewal_1"):
    def factory(context: cs.CheckoutContext) -> cs.ProviderSession:
        db.note_provider_call(context.provider)
        amounts.append(context.amount_minor)
        factory.seen.append(context)
        return cs.ProviderSession(
            provider=context.provider,
            reference=reference,
            checkout_url="https://provider.example/checkout/renewal",
        )

    factory.seen: List[cs.CheckoutContext] = []
    return factory


def _raising_factory(db: FakeSupabase, amounts: List[int]):
    def factory(context: cs.CheckoutContext) -> cs.ProviderSession:
        db.note_provider_call(context.provider)
        amounts.append(context.amount_minor)
        raise RuntimeError("gateway refused the session")

    factory.seen: List[cs.CheckoutContext] = []
    return factory


def _slow_factory(db: FakeSupabase, amounts: List[int], seconds: float):
    def factory(context: cs.CheckoutContext) -> cs.ProviderSession:
        db.note_provider_call(context.provider)
        amounts.append(context.amount_minor)
        time.sleep(seconds)
        return cs.ProviderSession(provider=context.provider, reference="too_late")

    factory.seen: List[cs.CheckoutContext] = []
    return factory


# ══════════════════════════════════════════════════════════════════════════
# The runner
# ══════════════════════════════════════════════════════════════════════════


def _run_coroutine(coro: Any) -> Any:
    """Run ``coro`` without leaving the thread without a current event loop.

    Not ``asyncio.run``: that closes its loop and then calls ``set_event_loop(None)``, leaving the
    main thread with no current loop, and other suites in this repository still call the
    deprecated ``asyncio.get_event_loop().run_until_complete(...)`` -
    ``tests/test_marketplace_pipeline.py::TestGetAdminUserP01Regression`` is one - which then
    raises ``RuntimeError: There is no current event loop in thread 'MainThread'`` when collected
    after this module. The loop that was current is therefore put back.

    The same helper as ``tests/test_checkout_service.py::_run`` and
    ``tests/test_marketplace_checkout_regression.py::_run_coroutine``, where the reasoning was
    first recorded.
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


class Outcome:
    """What one renewal attempt produced: the body or the error, plus the whole statement log."""

    def __init__(
        self,
        *,
        db: FakeSupabase,
        body: Dict[str, Any],
        error: Optional[BaseException],
        provider_amounts: List[int],
        before: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        self.db = db
        self.body = body
        self.error = error
        self.provider_amounts = provider_amounts
        self.before = before

    @property
    def wire_code(self) -> Optional[str]:
        if isinstance(self.error, MarketplaceError):
            return self.error.code
        return None

    @property
    def status_code(self) -> int:
        if isinstance(self.error, MarketplaceError):
            return self.error.http_status
        if isinstance(self.error, HTTPException):
            return self.error.status_code
        return 200


def run_renewal(
    *,
    db: Optional[FakeSupabase] = None,
    provider: str = "ok",
    caller: Optional[Dict[str, Any]] = None,
    subscription_id: str = SUBSCRIPTION_ID,
    **db_kwargs: Any,
) -> Outcome:
    """Call the real ``renew_subscription`` handler over the recording fake.

    Both service-client accessors are patched, not just ``_build_service_client``: the pre-fix
    body reached the Persistence_Layer a second way, through
    ``grant_deployment_permission``'s own ``_get_service_client()``. Patching only one of them
    would have let the pre-fix grant fail on a missing environment variable and be swallowed by
    its ``except``, and this file would then have "passed" against the defect it exists to catch.
    """
    db = db or FakeSupabase(**db_kwargs)
    amounts: List[int] = []

    if provider == "ok":
        factory = _ok_factory(db, amounts)
    elif provider == "raises":
        factory = _raising_factory(db, amounts)
    elif provider == "slow":
        factory = _slow_factory(db, amounts, seconds=0.4)
    else:  # pragma: no cover - a programming error in the test itself
        raise ValueError(f"unknown provider double {provider!r}")

    before = db.snapshot()
    body: Dict[str, Any] = {}
    error: Optional[BaseException] = None

    with patch.object(library_router, "_build_service_client", return_value=db), patch.object(
        library_router, "_get_service_client", return_value=db
    ), patch.object(cs, "default_provider_session_factory", factory):
        # The limiter's counters are process-wide and every call in this file keys to the same
        # bucket, so 5/60s would refuse the sixth renewal this module drives. Reset, not disabled:
        # the decorator stays in force and ``tests/test_task_33_2_rate_limit_keys.py`` remains the
        # place the limit itself is asserted.
        _reset_rate_limit_counters()
        try:
            result = _run_coroutine(
                library_router.renew_subscription(
                    request=_real_request(
                        path=f"/api/library/subscriptions/{subscription_id}/renew",
                        method="POST",
                    ),
                    sub_id=subscription_id,
                    user=dict(caller or CALLER),
                )
            )
            body = result if isinstance(result, dict) else {}
        except (MarketplaceError, HTTPException) as exc:
            error = exc

    return Outcome(
        db=db, body=body, error=error, provider_amounts=amounts, before=before
    )


def run_cancellation(*, db: Optional[FakeSupabase] = None, **db_kwargs: Any) -> Outcome:
    """Call the real ``cancel_subscription`` handler over the same fake."""
    db = db or FakeSupabase(subscription_status="active", **db_kwargs)
    before = db.snapshot()
    body: Dict[str, Any] = {}
    error: Optional[BaseException] = None

    with patch.object(library_router, "_build_service_client", return_value=db), patch.object(
        library_router, "_get_service_client", return_value=db
    ):
        _reset_rate_limit_counters()
        try:
            result = _run_coroutine(
                library_router.cancel_subscription(
                    request=_real_request(
                        path=f"/api/library/subscriptions/{SUBSCRIPTION_ID}/cancel",
                        method="POST",
                    ),
                    sub_id=SUBSCRIPTION_ID,
                    user=dict(CALLER),
                )
            )
            body = result if isinstance(result, dict) else {}
        except (MarketplaceError, HTTPException) as exc:
            error = exc

    return Outcome(db=db, body=body, error=error, provider_amounts=[], before=before)


# ══════════════════════════════════════════════════════════════════════════
# 1. The free activation is gone (Requirement 11.16)
# ══════════════════════════════════════════════════════════════════════════


class TestTheFreeActivationIsGone:
    """The four writes Requirement 11.16 names, asserted as absences against the real handler."""

    def test_a_renewal_writes_no_status_active(self):
        """``update({"status": "active", ...})`` is gone, and the stored status is unmoved.

        Both halves matter. The statement assertion catches the write wherever it is issued from;
        the stored-value assertion catches an equivalent write spelled differently. Requirement
        11.14: a request that would transition a Subscription into ``ACTIVE`` without a confirmed
        payment leaves the stored Subscription_State unchanged.
        """
        outcome = run_renewal(subscription_status="cancelled")

        activating = [
            payload
            for payload in outcome.db.update_payloads("library_subscriptions")
            if str(payload.get("status", "")).lower() == "active"
        ]
        assert not activating, (
            "renew_subscription still writes status='active' with no confirmed payment behind "
            f"it (Requirements 11.6, 11.14, 11.16); payload(s): {activating}"
        )
        assert outcome.db.subscription()["status"] == "cancelled", (
            "the stored Subscription_State moved on an unpaid renewal request; Requirement 11.14 "
            "requires it left unchanged"
        )

    def test_a_renewal_writes_no_null_expiry(self):
        """``"expires_at": None`` is gone, and the current expiry is untouched.

        A null expiry was not a missing value, it was a *permanent grant*:
        ``check_deployment_permission`` carried a branch reading it as a perpetual subscription,
        so this one key turned a monthly Listing into unlimited access.
        """
        outcome = run_renewal(subscription_status="cancelled")

        for column in ("expires_at", "period_expiry", "period_start", "cancelled_at"):
            offenders = [
                payload
                for payload in outcome.db.update_payloads("library_subscriptions")
                if column in payload
            ]
            assert not offenders, (
                f"renew_subscription still writes {column!r}; the period and the cancellation "
                f"instant are written only by settlement_service.settle on a confirmed payment "
                f"(Requirements 11.5, 11.6). payload(s): {offenders}"
            )

        stored = outcome.db.subscription()
        assert stored["expires_at"] == CURRENT_EXPIRY
        assert stored["period_expiry"] == CURRENT_EXPIRY

    def test_a_renewal_grants_no_deployment_permission(self):
        """No ``deployment_permissions`` row is inserted by a renewal request.

        The grant belongs inside the settlement transaction, where it carries the
        Subscription_Period expiry (``settlement_service.grant_deployment_permission``). Granted
        here it carried ``expires_at: None``.
        """
        outcome = run_renewal(subscription_status="cancelled")

        granted = outcome.db.insert_payloads("deployment_permissions")
        assert not granted, (
            f"renew_subscription still inserted {len(granted)} deployment_permissions row(s) "
            f"with no payment behind them (Requirements 11.6, 11.16): {granted}"
        )

    def test_a_renewal_does_not_increment_subscriber_count(self):
        """No ``subscriber_count`` increment either: nothing was bought yet."""
        outcome = run_renewal(subscription_status="cancelled")

        counted = [
            payload
            for payload in outcome.db.update_payloads("library_strategies")
            if "subscriber_count" in payload
        ]
        assert not counted, (
            "renew_subscription still increments subscriber_count before any payment is "
            f"confirmed (Requirement 11.16): {counted}"
        )

    def test_the_router_defines_no_second_grant_deployment_permission(self):
        """``routers/library.grant_deployment_permission`` is deleted (task 19.3).

        It wrote ``"expires_at": None`` unconditionally and took no expiry parameter, so it could
        not express a Subscription_Period at all;
        ``settlement_service.grant_deployment_permission`` takes the expiry as a required keyword
        and refuses ``None``. Requirement 30.2 admits one implementation, and this asserts which
        one survived.
        """
        assert not hasattr(library_router, "grant_deployment_permission"), (
            "routers/library.py still defines grant_deployment_permission; the authoritative "
            "one is settlement_service's, which cannot write a null expiry"
        )

        from backend_app.backend.marketplace import settlement_service

        assert callable(settlement_service.grant_deployment_permission)

    def test_a_cancellation_revokes_no_permission_and_moves_no_expiry(self):
        """Requirement 11.9: entitlement is retained until the *unchanged* current expiry.

        Cancellation used to flip every ``deployment_permissions`` row for the Subscription to
        ``is_active = False`` immediately, ending access inside a period the purchaser had already
        paid for. Access now lapses with the period, which the grant's own ``expires_at`` already
        carries.
        """
        outcome = run_cancellation()

        revokes = outcome.db.writes("deployment_permissions")
        assert not revokes, (
            "cancel_subscription still writes deployment_permissions; Requirement 11.9 retains "
            f"entitlement until the unchanged current expiry. statements: "
            f"{[(s.op, s.payload) for s in revokes]}"
        )

        stored = outcome.db.subscription()
        assert stored["status"] == "cancelled"
        assert stored["cancelled_at"], "the cancellation instant must be recorded (Req 11.9)"
        assert stored["expires_at"] == CURRENT_EXPIRY, "the current expiry must not move"
        assert stored["period_expiry"] == CURRENT_EXPIRY


# ══════════════════════════════════════════════════════════════════════════
# 2. A renewal is a checkout (Requirements 11.6, 11.14)
# ══════════════════════════════════════════════════════════════════════════


class TestTheRenewalIsACheckout:
    def test_a_renewal_creates_a_provider_session(self):
        """The endpoint's whole output is a provider session for the renewal amount."""
        outcome = run_renewal(subscription_status="cancelled")

        assert outcome.error is None, f"the renewal was refused: {outcome.error}"
        assert outcome.db.provider_calls() == ["stripe"], (
            "the renewal contacted no payment provider; Requirement 11.6 requires a payment "
            "confirmed through the Billing_Integration before a transition into ACTIVE"
        )
        assert outcome.body.get("provider_reference") == "cs_renewal_1"
        assert outcome.body.get("checkout_url")
        assert outcome.body.get("subscription_id") == SUBSCRIPTION_ID
        assert outcome.body.get("provider") == "stripe"

    def test_the_renewal_amount_is_the_listings_price_minor_exactly(self):
        """1999 Minor_Units for a $19.99 Listing, as an ``int``. No float goes near an amount."""
        outcome = run_renewal(subscription_status="expired")

        assert outcome.provider_amounts == [PRICE_MINOR_1999]
        assert type(outcome.provider_amounts[0]) is int
        assert outcome.body.get("amount_minor") == PRICE_MINOR_1999
        assert type(outcome.body["amount_minor"]) is int
        assert outcome.body.get("currency") == "USD"

    def test_the_renewal_changes_no_stored_state(self):
        """Zero write statements, and every table byte-identical before and after.

        This is the strongest form of Requirement 11.16 available: not "does not write the three
        deleted things" but "does not write". The transition into ``ACTIVE`` and the new expiry
        belong to ``settlement_service.settle`` alone.
        """
        outcome = run_renewal(subscription_status="cancelled")

        writes = outcome.db.writes()
        assert not writes, (
            "a renewal request issued write statement(s) "
            f"{[(s.op, s.table_name, s.payload) for s in writes]}; it must change no stored "
            "state of its own (task 19.3)"
        )
        assert outcome.db.snapshot() == outcome.before, (
            "the stored rows differ after a renewal request:\n"
            f"  before: {outcome.before}\n"
            f"  after:  {outcome.db.snapshot()}"
        )

    def test_no_settlement_row_is_written_by_the_endpoint(self):
        """The Settlement_Ledger is written by the webhook path, never by a checkout request."""
        outcome = run_renewal(subscription_status="cancelled")

        assert outcome.db.tables["marketplace_settlements"] == []
        assert not outcome.db.writes("marketplace_settlements")

    def test_an_active_subscription_may_renew_and_is_not_touched(self):
        """Requirement 11.5 / task 19.2: ``active -> active`` extends from the stored expiry.

        ``settlement_service._apply_transition`` handles the renewal in place and extends from
        ``period_expiry``, never from ``now``, so renewing early buys the next month rather than
        shortening this one. The checkout half therefore has to *sell* it - and still write
        nothing.
        """
        outcome = run_renewal(subscription_status="active")

        assert outcome.error is None, f"an active subscriber could not renew: {outcome.error}"
        assert outcome.provider_amounts == [PRICE_MINOR_1999]
        assert not outcome.db.writes()
        assert outcome.db.subscription()["period_expiry"] == CURRENT_EXPIRY

    @pytest.mark.parametrize("status", ["expired", "cancelled", "suspended", "payment_failed"])
    def test_every_activation_source_may_buy_a_renewal(self, status):
        """The renewable set is the set of statuses a confirmed payment can actually activate.

        Read from ``settlement_service.ELIGIBLE_FOR_ACTIVATION``, which
        ``tests/test_submission_state_agreement.py`` asserts equals the database's own sources of
        ``-> active``. Selling a renewal the guard would then refuse is a purchaser charged with
        no access.
        """
        outcome = run_renewal(subscription_status=status)

        assert outcome.error is None, f"a {status} subscription could not renew: {outcome.error}"
        assert outcome.db.provider_calls() == ["stripe"]
        assert not outcome.db.writes()

    def test_the_renewable_set_is_the_activation_set(self):
        """Stated as an equality so the two sets cannot drift apart silently."""
        from backend_app.backend.marketplace.settlement_service import (
            ELIGIBLE_FOR_ACTIVATION,
        )

        assert cs.RENEWABLE_STATUSES == (
            frozenset(ELIGIBLE_FOR_ACTIVATION) - {"pending"}
        ) | {"active"}
        assert "refunded" not in cs.RENEWABLE_STATUSES, "REFUNDED is terminal"
        assert "pending" not in cs.RENEWABLE_STATUSES, (
            "a PENDING row has no period to extend; it belongs on the checkout endpoint"
        )

    @pytest.mark.parametrize("status", ["refunded", "pending"])
    def test_a_non_renewable_status_is_refused_with_no_provider_session(self, status):
        outcome = run_renewal(subscription_status=status)

        assert outcome.wire_code == MARKETPLACE_LISTING_NOT_PURCHASABLE
        assert outcome.status_code == 409
        assert outcome.db.provider_calls() == []
        assert not outcome.db.writes()

    def test_another_purchasers_subscription_is_not_found(self):
        """Requirement 21.4: a Subscription that is not the caller's is indistinguishable from one
        that does not exist, and neither can be renewed by guessing its id."""
        outcome = run_renewal(subscription_user=OTHER_USER_ID)

        assert outcome.wire_code == NOT_FOUND
        assert outcome.status_code == 404
        assert outcome.db.provider_calls() == []
        assert not outcome.db.writes()

    def test_an_unknown_subscription_is_the_same_answer(self):
        outcome = run_renewal(subscription_rows=[])

        assert outcome.wire_code == NOT_FOUND
        assert outcome.status_code == 404
        assert outcome.db.provider_calls() == []

    def test_a_read_that_did_not_complete_is_not_a_missing_subscription(self):
        """Requirements 1.5, 1.7, 30.5: 503, not 404 and not a second charge."""
        outcome = run_renewal(raise_on={("select", "library_subscriptions")})

        assert outcome.wire_code == MARKETPLACE_READ_FAILED
        assert outcome.status_code == 503
        assert outcome.db.provider_calls() == []
        assert not outcome.db.writes()

    def test_an_unpublished_listing_sells_no_renewal(self):
        """A withdrawn Listing is not sold another month: the Entitlement_Resolver would have
        nothing to admit the renewed period against."""
        outcome = run_renewal(listing_rows=[_listing_row(submission_state="REJECTED")])

        assert outcome.wire_code == MARKETPLACE_LISTING_NOT_PURCHASABLE
        assert outcome.db.provider_calls() == []
        assert not outcome.db.writes()

    def test_a_listing_with_no_price_sells_no_renewal(self):
        outcome = run_renewal(listing_rows=[_listing_row(price_minor=None)])

        assert outcome.wire_code == MARKETPLACE_LISTING_NOT_PURCHASABLE
        assert outcome.db.provider_calls() == []
        assert not outcome.db.writes()

    def test_a_provider_failure_leaves_the_subscription_exactly_as_it_was(self):
        """A failed session must not demote a live entitlement to ``payment_failed``.

        This is the one deliberate difference from the first-purchase path, whose ``PENDING`` row
        does move to ``payment_failed``. Here the row is very often ``ACTIVE`` - a subscriber
        renewing before their period ends - and ending a paid-for entitlement because a checkout
        session could not be opened would be a worse defect than the one being fixed.
        """
        outcome = run_renewal(subscription_status="active", provider="raises")

        assert outcome.wire_code == MARKETPLACE_CHECKOUT_UNAVAILABLE
        assert outcome.status_code == 502
        assert outcome.db.subscription()["status"] == "active"
        assert not outcome.db.writes()
        assert outcome.db.snapshot() == outcome.before

    def test_the_provider_deadline_applies_to_a_renewal_too(self):
        """Requirement 9.13's 30 seconds is the one deadline, shared with the checkout path."""
        db = FakeSupabase(subscription_status="cancelled")
        amounts: List[int] = []
        factory = _slow_factory(db, amounts, seconds=0.4)
        before = db.snapshot()

        with patch.object(
            library_router, "_build_service_client", return_value=db
        ), patch.object(library_router, "_get_service_client", return_value=db):
            with pytest.raises(MarketplaceError) as caught:
                _run_coroutine(
                    cs.create_renewal_checkout(
                        caller=dict(CALLER),
                        subscription_id=SUBSCRIPTION_ID,
                        supabase=db,
                        provider_session_factory=factory,
                        deadline_seconds=0.05,
                    )
                )

        assert caught.value.code == MARKETPLACE_CHECKOUT_UNAVAILABLE
        assert caught.value.details.get("timed_out") is True
        assert not db.writes()
        assert db.snapshot() == before
        assert cs.PROVIDER_DEADLINE_SECONDS == 30

    def test_the_session_metadata_names_the_subscription_the_webhook_must_settle(self):
        """The renewal reuses the ONE webhook contract rather than adding a second (Req 9.1).

        ``billing._apply_billing_entitlement`` dispatches on
        ``item_key.startswith("marketplace_")`` and ``_apply_marketplace_entitlement`` reads
        ``metadata["subscription_id"]``. This is also *why* the renewal needs no write: the
        payment finds its Subscription through this metadata, not through a stored
        ``provider_reference`` - and overwriting that reference would destroy the correlation
        ``settlement_service.find_settled_payment`` uses to place a later refund.
        """
        db = FakeSupabase(subscription_status="cancelled")
        amounts: List[int] = []
        factory = _ok_factory(db, amounts)

        with patch.object(cs, "default_provider_session_factory", factory):
            _run_coroutine(
                cs.create_renewal_checkout(
                    caller=dict(CALLER), subscription_id=SUBSCRIPTION_ID, supabase=db
                )
            )

        metadata = cs._settlement_metadata(factory.seen[0])
        assert metadata["subscription_id"] == SUBSCRIPTION_ID
        assert metadata["item_key"] == f"marketplace_{LISTING_ID}"
        assert metadata["user_id"] == PURCHASER_ID
        assert (
            db.subscription()["provider_reference"]
            == "pi_the_payment_that_bought_this_month"
        ), "the reference of the payment that bought the CURRENT period was overwritten"


# ══════════════════════════════════════════════════════════════════════════
# 3. The database refuses an unpaid activation (Requirement 11.14)
# ══════════════════════════════════════════════════════════════════════════


class TestTheDatabaseRefusesAnUnpaidActivation:
    """A direct ``UPDATE ... SET status='active'`` with no Settlement_Record is refused.

    The application half above can only prove that *this* handler no longer activates for free.
    The invariant is stronger than any handler: ``trg_subscription_transition_guard`` refuses the
    write itself, so a future handler, a migration script or a psql session cannot grant a free
    month either. Read off ``008_marketplace_settlement.sql`` section 5c through
    ``tests/test_submission_state_agreement.py``'s own parser, because this environment has no
    PostgreSQL and a second parser would be a second opinion.
    """

    def test_the_guard_refuses_any_activation_with_no_non_reversal_settlement(self):
        body = _subscription_guard_body()

        gate = _activation_branch(body)

        assert "marketplace_settlements" in gate
        assert "NOT EXISTS" in gate.upper(), (
            "the activation branch does not probe for the ABSENCE of a settlement; without the "
            "NOT EXISTS the guard admits an unpaid activation (Requirements 11.6, 11.14)"
        )
        assert "s.subscription_id = NEW.id" in _squeeze(gate), (
            "the settlement probe is not scoped to NEW.id; one purchaser's payment would fund "
            "another's activation"
        )
        assert "s.is_reversal = FALSE" in _squeeze(gate), (
            "the settlement probe does not exclude reversals; a refund would fund an activation"
        )
        assert "23514" in gate, (
            "the refusal does not carry the 23514 SQLSTATE the application translates by, so a "
            "refused activation would surface as an unclassified 500"
        )

    def test_the_refusal_is_unconditional_on_the_source_status_in_008(self):
        """``008``'s own branch names no source: every ``-> active`` edge meets its probe.

        This is what makes "a direct UPDATE ... SET status='active' is refused" true rather than
        true-for-the-edges-somebody-remembered. ``008``'s branch is keyed on
        ``NEW.status = 'active'`` alone, so all five permitted sources - read from the
        migrations, not listed here - reach it.

        ``014`` later adds ONE authorised exception to the EFFECTIVE guard (Requirement 11.17's
        administrative reinstatement); it is asserted in
        :meth:`test_the_effective_guard_relaxes_for_exactly_one_source` below, against the
        effective definition, so this assertion stays a true statement about the file that
        authors the rule.
        """
        gate = _squeeze(_activation_branch(_subscription_guard_body()))

        assert "OLD.status = '" not in gate, (
            "the activation branch is conditioned on OLD.status, so some source status reaches "
            f"'active' without the settlement probe: {gate}"
        )

        activation_sources = {
            frm.lower() for frm, to in subscription_permitted_pairs_in_db() if to == "ACTIVE"
        }
        assert activation_sources, "no permitted edge into 'active' was parsed off the migrations"
        # Every source the guard's first branch will let through still meets the probe, and every
        # source the checkout path will sell a renewal for is one of them.
        assert cs.RENEWABLE_STATUSES - {"active"} <= activation_sources, (
            "checkout_service.RENEWABLE_STATUSES would sell a renewal for a status the guard "
            "refuses to activate: "
            f"{sorted(cs.RENEWABLE_STATUSES - {'active'} - activation_sources)}"
        )

    def test_the_effective_guard_relaxes_for_exactly_one_source(self):
        """The EFFECTIVE guard admits one unpaid route in, and it is not a free month.

        ``CREATE OR REPLACE FUNCTION`` means the definition a database runs is the last one
        authored, so this reads the effective body through
        ``tests/test_submission_state_agreement.subscription_guard_body_in_db()`` - the one
        helper for that question - rather than ``008``'s text.

        Requirement 11.17's administrative reinstatement (task 22 remediation) is the one
        exception: ``suspended -> active`` with the period unchanged, admitted on the payment
        that BOUGHT the period being resumed instead of a new one. What this asserts is that the
        exception is scoped and still paid-for:

        * the relaxation is reached through the named predicate
          ``marketplace_subscription_reinstatement_shape``, whose only admitted source is
          ``suspended`` - ``014``'s postflight proves the four sources Requirement 11.6 names
          are refused by it by CALLING it with each of them;
        * the relaxed arm still probes ``marketplace_settlements``, non-reversal, scoped to
          ``NEW.id``; and
        * ``008``'s probe - the ``>= OLD.period_expiry`` anchor - is still there for everything
          else.
        """
        from tests.test_submission_state_agreement import subscription_guard_body_in_db

        effective = _squeeze(_activation_branch(subscription_guard_body_in_db()))
        whole_body = _squeeze(subscription_guard_body_in_db())

        assert "s.subscription_id = NEW.id" in effective
        assert "s.is_reversal = FALSE" in effective
        assert "23514" in whole_body

        if "marketplace_subscription_reinstatement_shape" not in whole_body:
            # No relaxation authored: the stronger, unconditional statement must still hold.
            assert "OLD.status = '" not in effective, (
                "the activation branch is conditioned on OLD.status with no named admission "
                f"predicate, so some source reaches 'active' unaudited: {effective}"
            )
            return

        # The relaxation is expressed as a call, so the guard body itself names no source.
        assert "OLD.status = '" not in whole_body, (
            "the guard hard-codes a source status inline instead of asking the named admission "
            f"predicate, so nothing executable can prove which sources it admits: {whole_body}"
        )
        # Both arms are present: 008's anchor for every other source, and the reinstatement's own
        # requirement that the period being resumed was paid for.
        assert "s.settled_at >= OLD.period_expiry" in whole_body, (
            "008's non-circular anchor is gone from the effective guard, so the payment that "
            "bought the period now ending could fund the next one"
        )
        assert "s.settled_at <= OLD.period_expiry" in whole_body, (
            "the relaxed arm requires no settlement at all, so a subscription that never paid "
            "could be reinstated into access it never bought (Requirement 11.14)"
        )

    def test_the_guard_is_attached_to_library_subscriptions_before_update(self):
        """A guard function nobody fires guards nothing."""
        from tests.test_submission_state_agreement import (
            MIGRATIONS,
            SUBSCRIPTION_SEED_MIGRATION,
        )

        sql = _squeeze((MIGRATIONS / SUBSCRIPTION_SEED_MIGRATION).read_text(encoding="utf-8"))

        assert "CREATE TRIGGER trg_subscription_transition_guard" in sql
        assert "BEFORE UPDATE ON public.library_subscriptions" in sql, (
            "trg_subscription_transition_guard is not a BEFORE UPDATE trigger on "
            "public.library_subscriptions; an AFTER trigger cannot refuse the write"
        )
        assert "EXECUTE FUNCTION public.marketplace_subscription_guard()" in sql

    def test_the_application_does_not_reach_active_on_its_own_either(self):
        """No module under ``backend_app`` writes ``status='active'`` outside the settlement path.

        The database refuses it; this asserts nothing is *trying*, so a refusal never becomes a
        500 a caller sees.

        TWO writers are permitted, with two different stated preconditions, and the difference
        between them is the whole point:

        * ``settlement_service`` is the only writer of a **PAID** activation. It writes the
          Settlement_Record first, in the same call (Requirement 9.5).
        * ``subscription_reinstatement`` is the explicitly administrative writer added for
          Requirement 11.17: ``suspended -> active`` with the period unchanged and NO new
          payment, admitted by the database only when the period being resumed was already
          bought (``014_subscription_admin_reinstatement.sql``, arm 3a of the guard). It is a
          separate module rather than a branch of ``settle`` precisely so this assertion can
          keep naming ``settlement_service`` as the only writer of a paid activation.
          ``tests/test_subscription_reinstatement_regression.py`` holds it to writing the status
          column alone, writing no Settlement_Record, and auditing every act.

        THE RESOLUTION IS ALSO A STRENGTHENING. The check used to look only for a literal
        ``{"status": "active"}`` dict, so any module that wrote the same value through a
        constant - which is how both permitted writers spell it - slipped past. It now resolves
        module-level string constants and ``STATUS_TEXT_FOR_STATE[SubscriptionState.X]``
        assignments too, so a third writer cannot hide behind a name.
        """
        import ast
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[1]
        marketplace = repo_root / "backend_app" / "backend" / "marketplace"
        allowed = {
            marketplace / "settlement_service.py",
            marketplace / "subscription_reinstatement.py",
        }
        for permitted in allowed:
            assert permitted.exists(), f"{permitted.name} does not exist"

        def _status_constants(tree: ast.AST) -> Dict[str, str]:
            """``{name: status_text}`` for module-level assignments to a status spelling.

            Two shapes, both of which appear in the permitted writers:
            ``_STATUS_ACTIVE = "active"`` and
            ``_STATUS_ACTIVE = STATUS_TEXT_FOR_STATE[SubscriptionState.ACTIVE]``.
            """
            resolved: Dict[str, str] = {}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                    continue
                target = node.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    resolved[target.id] = value.value.lower()
                elif isinstance(value, ast.Subscript):
                    index = value.slice
                    if isinstance(index, ast.Attribute):
                        resolved[target.id] = index.attr.lower()
            return resolved

        offenders: List[str] = []

        for path in (repo_root / "backend_app").rglob("*.py"):
            if path in allowed or "migrations" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
                continue
            constants = _status_constants(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute) and func.attr == "update"):
                    continue
                for arg in node.args:
                    if not isinstance(arg, ast.Dict):
                        continue
                    for key, value in zip(arg.keys, arg.values):
                        if not (isinstance(key, ast.Constant) and key.value == "status"):
                            continue
                        written: Optional[str] = None
                        if isinstance(value, ast.Constant):
                            written = str(value.value).lower()
                        elif isinstance(value, ast.Name):
                            written = constants.get(value.id)
                        elif isinstance(value, ast.Subscript) and isinstance(
                            value.slice, ast.Attribute
                        ):
                            written = value.slice.attr.lower()
                        if written == "active":
                            offenders.append(
                                f"{path.relative_to(repo_root).as_posix()}:{node.lineno}"
                            )

        assert not offenders, (
            "a module outside settlement_service and subscription_reinstatement writes "
            "status='active' directly; a PAID activation needs a Settlement_Record in the same "
            "transaction, and the only unpaid route is Requirement 11.17's administrative "
            f"reinstatement (Requirements 11.6, 11.14, 11.17): {offenders}"
        )

    def test_the_second_writer_writes_no_period_column_and_no_settlement(self):
        """The price of a second permitted writer, paid here (Requirements 11.14, 11.17).

        ``subscription_reinstatement`` is allowed to reach ``active`` without a payment of its
        own. What makes that safe is that it moves nothing else: its UPDATE payload is the
        status column alone, so no period is re-issued, and it inserts nothing into
        ``marketplace_settlements``, so no money is invented. Asserted structurally here, beside
        the allowlist that admits it, rather than only in its own suite.
        """
        import ast
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[1]
            / "backend_app"
            / "backend"
            / "marketplace"
            / "subscription_reinstatement.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))

        forbidden_columns = {"period_start", "period_expiry", "started_at", "expires_at"}
        written_columns: List[str] = []
        settlement_inserts: List[int] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr in {"update", "insert"}:
                for arg in node.args:
                    if not isinstance(arg, ast.Dict):
                        continue
                    for key in arg.keys:
                        if isinstance(key, ast.Constant) and key.value in forbidden_columns:
                            if func.attr == "update":
                                written_columns.append(f"{key.value}:{node.lineno}")

        source = path.read_text(encoding="utf-8")
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "insert"
            ):
                # The table this insert is on: ``supabase.table(X).insert(...)``.
                table_call = node.func.value
                if (
                    isinstance(table_call, ast.Call)
                    and isinstance(table_call.func, ast.Attribute)
                    and table_call.func.attr == "table"
                    and table_call.args
                    and isinstance(table_call.args[0], ast.Name)
                    and table_call.args[0].id == "SETTLEMENT_TABLE"
                ):
                    settlement_inserts.append(node.lineno)

        assert not written_columns, (
            "the reinstatement writer moves a period column, so a purchaser's paid month would "
            f"be re-issued rather than resumed (Requirement 11.17): {written_columns}"
        )
        assert not settlement_inserts, (
            "the reinstatement writer inserts into the Settlement_Ledger at line(s) "
            f"{settlement_inserts}; lifting a hold moves no money (Requirement 11.17)"
        )
        assert "SETTLEMENT_TABLE" in source, (
            "the reinstatement writer never reads the Settlement_Ledger, so it cannot tell a "
            "paid period from one that was never bought (Requirement 11.14)"
        )


# ══════════════════════════════════════════════════════════════════════════
# Small helpers for the SQL reads
# ══════════════════════════════════════════════════════════════════════════


def _squeeze(text: str) -> str:
    """Collapse every run of whitespace to one space, so a reflowed SQL statement still matches."""
    return " ".join(text.split())


def _activation_branch(body: str) -> str:
    """The guard's ``IF NEW.status = 'active' THEN ... END IF;`` branch, as written.

    Located by its own opening condition rather than by line number, so re-indenting or
    re-commenting ``008`` does not silently return an empty string that every assertion below
    would then pass against.
    """
    import re

    match = re.search(
        r"IF\s+NEW\.status\s*=\s*'active'\s+THEN.*?END\s+IF\s*;",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, (
        "008_marketplace_settlement.sql's marketplace_subscription_guard() has no "
        "`IF NEW.status = 'active' THEN ... END IF;` branch, so nothing in the database refuses "
        "an activation with no confirmed payment behind it (Requirements 11.6, 11.14)"
    )
    return match.group(0)
