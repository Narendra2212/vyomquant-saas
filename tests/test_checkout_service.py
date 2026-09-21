"""
tests/test_checkout_service.py

Focused unit tests for ``backend_app/backend/marketplace/checkout_service.py`` (task 18.1).

These exercise :func:`create_checkout` against a FAKE Supabase double that serves scripted rows
for the Listing read and keeps a mutable in-memory ``library_subscriptions`` table, and against
an INJECTED provider factory - the same dependency-injection pattern
``tests/test_entitlement_resolver.py`` uses, so the whole checkout path is checkable without a
database, a TestClient, a payment SDK or a network call.

What is asserted
----------------
* **Each refusal** creates no provider session and no Subscription row (Requirement 9.10):
  - the Submission is not ``PUBLISHED``        -> 409 ``MARKETPLACE_LISTING_NOT_PURCHASABLE``
  - the caller already holds an ``ACTIVE`` row -> 409 ``MARKETPLACE_LISTING_NOT_PURCHASABLE``
  - the caller is the Listing's owner          -> 400 ``MARKETPLACE_OWN_LISTING``
* **The amount is exact**: a ``$19.99`` Listing is charged **1999** Minor_Units, not the 1998 the
  replaced ``int(float(price) * 100)`` produced (Requirements 9.2, 10.3). Reinforced by an AST
  walk asserting the module contains no ``float`` call and no float literal at all.
* **The ``PENDING`` insert records both split components** - ``owner_share_minor`` 1799 and
  ``platform_fee_minor`` 200, conserving 1999 - and **omits ``expires_at`` entirely** rather than
  inserting ``NULL`` (Requirements 9.3, 10.1). A null expiry is what
  ``check_deployment_permission``'s "no expiry date means perpetual subscription" branch reads as
  permanent access, so present-and-null and absent are not the same thing here.
* **The commit precedes the provider session** (Requirement 9.3): the recorded operation order
  puts the ``library_subscriptions`` insert strictly before the provider factory call.
* **The deadline is 30 seconds** and the timeout path applies the ``PAYMENT_FAILED`` bookkeeping
  (Requirement 9.13). The constant and the default argument are both asserted to be 30; the
  behavioural test injects a short deadline, because a suite that waited thirty seconds to prove
  a deadline exists would not be run.
* **A provider failure leaves the row present in ``payment_failed`` with a ``failure_cause``**,
  and **no ``delete`` is ever issued** against ``library_subscriptions`` (Requirement 9.4). This
  is the assertion that fails against the replaced
  ``svc.table("library_subscriptions").delete().eq("id", sub_id)`` inside a bare
  ``except: pass``.
* A read that does not complete answers ``MARKETPLACE_READ_FAILED`` (503) rather than a refusal
  or a second charge (Requirements 1.5, 1.7, 30.5).
"""

from __future__ import annotations

import ast
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.marketplace import COLUMN_CONTRACT
from backend_app.backend.marketplace import checkout_service as cs
from backend_app.backend.marketplace.checkout_service import (
    OMITTED_ON_PENDING,
    PROVIDER_DEADLINE_SECONDS,
    CheckoutResult,
    ProviderSession,
    create_checkout,
)
from backend_app.backend.marketplace.errors import (
    MARKETPLACE_CHECKOUT_UNAVAILABLE,
    MARKETPLACE_LISTING_NOT_PURCHASABLE,
    MARKETPLACE_OWN_LISTING,
    MARKETPLACE_READ_FAILED,
    MarketplaceError,
)

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend_app"
    / "backend"
    / "marketplace"
    / "checkout_service.py"
)

CALLER = {"id": "buyer-1"}
OWNER_ID = "owner-9"
LISTING_ID = "listing-abc"

# A $19.99 Listing. This is the exact case the replaced arithmetic got wrong:
# ``int(float("19.99") * 100)`` is 1998, because 19.99 is not nineteen and ninety-nine
# hundredths in binary and ``int()`` truncates the 1998.9999999999998 that results.
PRICE_MINOR_1999 = 1999
OWNER_SHARE_1999 = 1799   # (1999 * 90) // 100
PLATFORM_FEE_1999 = 200   # 1999 - 1799


# ══════════════════════════════════════════════════════════════════════════
# A fake Supabase double with a mutable library_subscriptions table
# ══════════════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    """A recording query builder mimicking the supabase-py fluent chain."""

    def __init__(self, table: str, client: "FakeSupabase") -> None:
        self.table_name = table
        self.client = client
        self.op: str = "select"
        self.payload: Optional[Dict[str, Any]] = None
        self.cols: Optional[str] = None
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
    """Serves scripted Listing rows and a mutable ``library_subscriptions`` table.

    Every executed statement is appended to :attr:`ops` as ``(op, table)``, and the provider
    factory appends ``("provider", provider_name)`` to the SAME list - which is what makes the
    commit-before-provider ordering of Requirement 9.3 a measurable fact rather than a claim.
    """

    def __init__(
        self,
        *,
        listing_rows: Optional[List[Dict[str, Any]]] = None,
        subscription_rows: Optional[List[Dict[str, Any]]] = None,
        raise_on: Optional[set] = None,
        error_on: Optional[set] = None,
    ) -> None:
        self._listing_rows = list(listing_rows or [])
        self.subscription_rows: List[Dict[str, Any]] = [
            dict(r) for r in (subscription_rows or [])
        ]
        #: ``(op, table)`` pairs that raise, e.g. ``{("select", "library_strategies")}``.
        self.raise_on = raise_on or set()
        #: ``(op, table)`` pairs that "complete" with a PostgREST error envelope.
        self.error_on = error_on or set()
        self.ops: List[Tuple[str, str]] = []
        self.statements: List[_Query] = []

    # ---- the supabase-py surface this module uses ------------------------
    def table(self, name: str) -> _Query:
        return _Query(name, self)

    # ---- recording ------------------------------------------------------
    def note_provider_call(self, provider: str) -> None:
        self.ops.append(("provider", provider))

    def _execute(self, q: _Query) -> Any:
        self.ops.append((q.op, q.table_name))
        self.statements.append(q)

        if (q.op, q.table_name) in self.raise_on:
            raise RuntimeError(f"{q.op} failed on {q.table_name}")
        if (q.op, q.table_name) in self.error_on:
            return {"data": None, "error": {"message": f"boom on {q.table_name}"}}

        if q.table_name == "library_strategies":
            return _Resp(list(self._listing_rows))

        if q.table_name == "library_subscriptions":
            if q.op == "select":
                return _Resp([dict(r) for r in self._matching(q)])
            if q.op == "insert":
                row = dict(q.payload or {})
                self.subscription_rows.append(row)
                return _Resp([dict(row)])
            if q.op == "update":
                touched = self._matching(q)
                for row in touched:
                    row.update(q.payload or {})
                return _Resp([dict(r) for r in touched])
            if q.op == "delete":
                touched = self._matching(q)
                for row in touched:
                    self.subscription_rows.remove(row)
                return _Resp([dict(r) for r in touched])

        return _Resp([])

    def _matching(self, q: _Query) -> List[Dict[str, Any]]:
        rows = self.subscription_rows
        for col, val in q.filters:
            rows = [r for r in rows if str(r.get(col)) == str(val)]
        return rows

    # ---- assertions helpers ---------------------------------------------
    def op_names(self) -> List[Tuple[str, str]]:
        return list(self.ops)

    def inserted_payloads(self, table: str) -> List[Dict[str, Any]]:
        return [
            dict(s.payload or {})
            for s in self.statements
            if s.op == "insert" and s.table_name == table
        ]

    def deletes(self, table: str) -> int:
        return sum(1 for s in self.statements if s.op == "delete" and s.table_name == table)

    def wrote_subscription(self) -> bool:
        return any(
            s.table_name == "library_subscriptions" and s.op in {"insert", "update", "delete"}
            for s in self.statements
        )


def _listing_row(
    *,
    author_id: str = OWNER_ID,
    price_minor: Optional[int] = PRICE_MINOR_1999,
    currency: str = "USD",
    submission_state: Optional[str] = "PUBLISHED",
    name: str = "Momentum Breakout",
) -> Dict[str, Any]:
    submissions = [] if submission_state is None else [{"submission_state": submission_state}]
    return {
        "id": LISTING_ID,
        "name": name,
        "author_id": author_id,
        "price_minor": price_minor,
        "currency": currency,
        "is_active": True,
        "marketplace_submissions": submissions,
    }


def _subscription_row(*, status: str, row_id: str = "sub-existing") -> Dict[str, Any]:
    return {
        "id": row_id,
        "library_id": LISTING_ID,
        "user_id": CALLER["id"],
        "status": status,
    }


# ══════════════════════════════════════════════════════════════════════════
# Provider factory doubles
# ══════════════════════════════════════════════════════════════════════════


def _ok_factory(client: FakeSupabase, *, reference: str = "cs_test_123"):
    """A factory that succeeds and records the moment it was called."""

    def factory(context: cs.CheckoutContext) -> ProviderSession:
        client.note_provider_call(context.provider)
        factory.seen.append(context)
        return ProviderSession(
            provider=context.provider,
            reference=reference,
            checkout_url="https://provider.example/checkout/1",
        )

    factory.seen: List[cs.CheckoutContext] = []
    return factory


def _raising_factory(client: FakeSupabase, message: str = "provider exploded"):
    def factory(context: cs.CheckoutContext) -> ProviderSession:
        client.note_provider_call(context.provider)
        raise RuntimeError(message)

    return factory


def _slow_factory(client: FakeSupabase, seconds: float):
    """A blocking factory, which is what both real SDKs are."""

    def factory(context: cs.CheckoutContext) -> ProviderSession:
        client.note_provider_call(context.provider)
        time.sleep(seconds)
        return ProviderSession(provider=context.provider, reference="too_late")

    return factory


def _never_called_factory():
    def factory(context: cs.CheckoutContext) -> ProviderSession:  # pragma: no cover
        raise AssertionError(
            "a refusal must not request a provider session (Requirement 9.10)"
        )

    return factory


def _run(coro: Any) -> Any:
    """Run ``coro`` to completion **without leaving the thread without an event loop**.

    Not ``asyncio.run``: that closes its loop and then calls ``set_event_loop(None)``, leaving the
    main thread with *no* current loop. Other suites in this repository still call the deprecated
    ``asyncio.get_event_loop().run_until_complete(...)`` — ``tests/test_marketplace_pipeline.py``
    ::TestGetAdminUserP01Regression is one — and those raise ``RuntimeError: There is no current
    event loop in thread 'MainThread'`` when they are collected after this module in the same
    session. A test file that breaks its neighbours is a new regression, not a guard against one,
    so the loop that was current is put back (or a fresh usable one installed) before returning.

    Lifted verbatim from ``tests/test_marketplace_checkout_regression.py::_run_coroutine``, which
    is where the reasoning was first recorded.
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


def _checkout(
    client: FakeSupabase,
    factory: Any,
    *,
    caller: Any = None,
    currency: str = "USD",
    deadline_seconds: float = PROVIDER_DEADLINE_SECONDS,
) -> CheckoutResult:
    return _run(
        create_checkout(
            caller=caller or CALLER,
            listing_id=LISTING_ID,
            currency=currency,
            supabase=client,
            provider_session_factory=factory,
            deadline_seconds=deadline_seconds,
        )
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. THE REFUSALS — no provider session, no Subscription row
# ══════════════════════════════════════════════════════════════════════════


def test_unpublished_submission_is_refused_409_and_writes_nothing():
    """Requirement 9.10: a Listing whose Submission is not PUBLISHED is not purchasable."""
    client = FakeSupabase(listing_rows=[_listing_row(submission_state="APPROVED")])

    with pytest.raises(MarketplaceError) as raised:
        _checkout(client, _never_called_factory())

    assert raised.value.code == MARKETPLACE_LISTING_NOT_PURCHASABLE
    assert raised.value.http_status == 409
    assert raised.value.details.get("reason") == "not_published"
    assert not client.wrote_subscription(), "a refusal created a Subscription row"
    assert ("provider", "stripe") not in client.op_names()


def test_listing_with_no_submission_at_all_is_refused_409():
    """Publication is never inferred from ``moderation_status``: no Submission, no purchase."""
    client = FakeSupabase(listing_rows=[_listing_row(submission_state=None)])

    with pytest.raises(MarketplaceError) as raised:
        _checkout(client, _never_called_factory())

    assert raised.value.code == MARKETPLACE_LISTING_NOT_PURCHASABLE
    assert not client.wrote_subscription()


def test_already_active_subscription_is_refused_409_and_writes_nothing():
    """Requirement 9.10: the purchaser already holds an ACTIVE Subscription."""
    client = FakeSupabase(
        listing_rows=[_listing_row()],
        subscription_rows=[_subscription_row(status="active")],
    )

    with pytest.raises(MarketplaceError) as raised:
        _checkout(client, _never_called_factory())

    assert raised.value.code == MARKETPLACE_LISTING_NOT_PURCHASABLE
    assert raised.value.http_status == 409
    assert raised.value.details.get("reason") == "already_subscribed"
    assert not client.wrote_subscription(), "a refusal wrote to library_subscriptions"
    # The existing row is untouched — not re-priced, not moved, not deleted.
    assert client.subscription_rows == [_subscription_row(status="active")]


def test_own_listing_is_refused_400_and_writes_nothing():
    """Requirement 9.10: the purchaser is the Listing's owner -> 400 MARKETPLACE_OWN_LISTING."""
    client = FakeSupabase(listing_rows=[_listing_row(author_id=CALLER["id"])])

    with pytest.raises(MarketplaceError) as raised:
        _checkout(client, _never_called_factory())

    assert raised.value.code == MARKETPLACE_OWN_LISTING
    assert raised.value.http_status == 400
    assert not client.wrote_subscription()


def test_listing_with_no_price_minor_is_refused_and_not_treated_as_free():
    """An unpriced Listing is *not purchasable*, and is not silently read as free.

    The replaced handler's ``if not strat.get("price")`` conflated the two, and read the legacy
    ``price`` mirror rather than the authoritative ``price_minor``.
    """
    client = FakeSupabase(listing_rows=[_listing_row(price_minor=None)])

    with pytest.raises(MarketplaceError) as raised:
        _checkout(client, _never_called_factory())

    assert raised.value.code == MARKETPLACE_LISTING_NOT_PURCHASABLE
    assert raised.value.details.get("reason") == "no_price"
    assert not client.wrote_subscription()


def test_a_read_that_does_not_complete_answers_503_not_a_refusal():
    """Requirements 1.5, 1.7, 30.5: a broken read is neither a refusal nor "not subscribed"."""
    raising = FakeSupabase(
        listing_rows=[_listing_row()], raise_on={("select", "library_strategies")}
    )
    with pytest.raises(MarketplaceError) as raised:
        _checkout(raising, _never_called_factory())
    assert raised.value.code == MARKETPLACE_READ_FAILED
    assert raised.value.http_status == 503

    # The same for a completed response carrying an error envelope, on the subscription read —
    # the shape that must NOT be read as "no rows" and let a subscriber be charged twice.
    enveloped = FakeSupabase(
        listing_rows=[_listing_row()], error_on={("select", "library_subscriptions")}
    )
    with pytest.raises(MarketplaceError) as raised:
        _checkout(enveloped, _never_called_factory())
    assert raised.value.code == MARKETPLACE_READ_FAILED
    assert not enveloped.wrote_subscription()


# ══════════════════════════════════════════════════════════════════════════
# 2. THE AMOUNT IS EXACTLY price_minor (1999, not 1998)
# ══════════════════════════════════════════════════════════════════════════


def test_amount_for_a_1999_listing_is_exactly_1999_minor_units():
    """Requirements 9.2, 10.3: the charged amount is the stored ``price_minor``, unchanged.

    ``int(float("19.99") * 100)`` — the arithmetic this replaces — is 1998.
    """
    client = FakeSupabase(listing_rows=[_listing_row(price_minor=PRICE_MINOR_1999)])
    factory = _ok_factory(client)

    result = _checkout(client, factory)

    assert result.amount_minor == PRICE_MINOR_1999
    assert result.amount_minor != 1998
    assert isinstance(result.amount_minor, int) and not isinstance(result.amount_minor, bool)

    # The amount reaching the provider is the same integer, not a re-derived one.
    assert len(factory.seen) == 1
    assert factory.seen[0].amount_minor == PRICE_MINOR_1999
    assert isinstance(factory.seen[0].amount_minor, int)


def test_module_contains_no_float_call_and_no_float_literal():
    """Requirement 10.3, structurally: no binary floating-point money arithmetic on this path.

    An AST walk rather than a text search, so a ``float`` inside a docstring or a comment does
    not register and a genuine ``float(...)`` call cannot hide behind formatting.
    """
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), str(MODULE_PATH))

    float_calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "float"
    ]
    assert not float_calls, f"float() called at line(s) {float_calls}"

    float_literals = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert not float_literals, f"float literal(s) present: {float_literals}"


# ══════════════════════════════════════════════════════════════════════════
# 3. THE PENDING INSERT — both split components, and no expires_at
# ══════════════════════════════════════════════════════════════════════════


def test_pending_insert_records_both_split_components_and_conserves_the_amount():
    """Requirement 10.1: ``owner_share_minor`` and ``platform_fee_minor`` are both persisted."""
    client = FakeSupabase(listing_rows=[_listing_row()])

    result = _checkout(client, _ok_factory(client))

    payloads = client.inserted_payloads("library_subscriptions")
    assert len(payloads) == 1, "the PENDING row is written in exactly one statement"
    payload = payloads[0]

    assert payload["price_minor"] == PRICE_MINOR_1999
    assert payload["owner_share_minor"] == OWNER_SHARE_1999
    assert payload["platform_fee_minor"] == PLATFORM_FEE_1999
    # chk_ls_split_conserved, evaluated here against the one row image.
    assert payload["owner_share_minor"] + payload["platform_fee_minor"] == payload["price_minor"]

    assert payload["status"] == "pending"
    assert payload["owner_id"] == OWNER_ID
    assert payload["currency"] == "USD"
    assert payload["started_at"], "the creation timestamp is recorded"

    # The result reports the same integers it persisted.
    assert result.owner_share_minor == OWNER_SHARE_1999
    assert result.platform_fee_minor == PLATFORM_FEE_1999


def test_pending_insert_omits_expires_at_entirely_rather_than_writing_null():
    """Requirement 9.3: the column is ABSENT, not present-and-NULL.

    ``check_deployment_permission`` carries a branch reading "no expiry date means perpetual
    subscription", so a row that reaches ``active`` with a null expiry grants permanent access to
    a monthly Listing. ``chk_ls_active_has_period`` makes an ACTIVE row without a period
    unrepresentable, which is only true if the column is left alone.
    """
    client = FakeSupabase(listing_rows=[_listing_row()])
    _checkout(client, _ok_factory(client))

    payload = client.inserted_payloads("library_subscriptions")[0]

    assert "expires_at" not in payload, (
        "expires_at must be OMITTED from the PENDING payload; the replaced handler wrote "
        '"expires_at": None, which check_deployment_permission reads as perpetual access'
    )
    assert "period_start" not in payload
    assert "period_expiry" not in payload
    assert not (OMITTED_ON_PENDING & set(payload)), (
        f"the payload carries columns OMITTED_ON_PENDING forbids: "
        f"{sorted(OMITTED_ON_PENDING & set(payload))}"
    )
    # And nothing later in the successful path adds them either.
    stored = client.subscription_rows[0]
    assert "expires_at" not in stored
    assert "period_expiry" not in stored


def test_omitted_on_pending_names_expires_at_and_the_period_columns():
    """The declared exclusion set is the one the assertion in ``_pending_payload`` reads."""
    assert {"expires_at", "period_start", "period_expiry"} <= OMITTED_ON_PENDING


# ══════════════════════════════════════════════════════════════════════════
# 4. THE COMMIT PRECEDES THE PROVIDER SESSION (Requirement 9.3)
# ══════════════════════════════════════════════════════════════════════════


def test_pending_row_is_committed_before_the_provider_session_is_requested():
    """The insert appears strictly before the provider call in the recorded operation order.

    A provider session created before the row exists is a payment with no record to correlate a
    webhook against.
    """
    client = FakeSupabase(listing_rows=[_listing_row()])

    _checkout(client, _ok_factory(client))

    ops = client.op_names()
    insert_at = ops.index(("insert", "library_subscriptions"))
    provider_at = ops.index(("provider", "stripe"))
    assert insert_at < provider_at, f"provider session requested before the commit: {ops}"

    # The row was already visible to the provider step, carrying its full image.
    assert client.subscription_rows, "the row must exist by the time the provider is called"

    # And the handshake is recorded against that row afterwards, not before.
    reference_update_at = max(
        i for i, op in enumerate(ops) if op == ("update", "library_subscriptions")
    )
    assert provider_at < reference_update_at


def test_successful_checkout_records_the_provider_reference_and_session_instant():
    """Requirements 9.4, 9.12: the handshake lands on the PENDING row before returning."""
    client = FakeSupabase(listing_rows=[_listing_row()])

    result = _checkout(client, _ok_factory(client, reference="cs_live_abc"))

    assert result.provider == "stripe"
    assert result.provider_reference == "cs_live_abc"
    assert result.checkout_url == "https://provider.example/checkout/1"

    stored = client.subscription_rows[0]
    assert stored["provider_reference"] == "cs_live_abc"
    assert stored["provider_session_at"], "provider_session_at must be recorded"
    assert stored["provider"] == "stripe"
    # The status is NOT advanced by checkout — only a confirmed payment may do that.
    assert stored["status"] == "pending"


# ══════════════════════════════════════════════════════════════════════════
# 5. THE 30-SECOND DEADLINE (Requirement 9.13)
# ══════════════════════════════════════════════════════════════════════════


def test_the_declared_provider_deadline_is_thirty_seconds():
    """The constant and the default argument are both 30 — one literal, in one place."""
    import inspect

    assert PROVIDER_DEADLINE_SECONDS == 30
    default = inspect.signature(create_checkout).parameters["deadline_seconds"].default
    assert default == PROVIDER_DEADLINE_SECONDS == 30


def test_a_provider_that_exceeds_the_deadline_is_abandoned_and_marked_payment_failed():
    """The attempt is abandoned, the row is marked ``payment_failed``, and 502 is returned.

    A short deadline is injected: waiting thirty real seconds to demonstrate that a deadline
    exists would make this test one nobody runs. The path under test is the same one the
    30-second default takes.
    """
    client = FakeSupabase(listing_rows=[_listing_row()])

    with pytest.raises(MarketplaceError) as raised:
        _checkout(client, _slow_factory(client, seconds=0.4), deadline_seconds=0.02)

    assert raised.value.code == MARKETPLACE_CHECKOUT_UNAVAILABLE
    assert raised.value.http_status == 502
    assert raised.value.details.get("timed_out") is True

    assert len(client.subscription_rows) == 1, "the row must survive the timeout"
    stored = client.subscription_rows[0]
    assert stored["status"] == "payment_failed"
    assert stored["failure_cause"], "the timeout must be recorded as a failure_cause"
    assert "deadline" in stored["failure_cause"]
    assert stored["failed_at"]
    assert stored["provider_reference"] is None
    assert client.deletes("library_subscriptions") == 0


# ══════════════════════════════════════════════════════════════════════════
# 6. A PROVIDER FAILURE LEAVES THE ROW PRESENT, NEVER DELETED (Requirement 9.4)
# ══════════════════════════════════════════════════════════════════════════


def test_provider_failure_leaves_the_row_in_payment_failed_and_never_deletes_it():
    """Requirement 9.4: the row is UPDATEd, not deleted.

    This is the assertion that fails against the replaced
    ``svc.table("library_subscriptions").delete().eq("id", sub_id)`` inside a bare
    ``except: pass`` — which destroyed the only record that the attempt was ever made.
    """
    client = FakeSupabase(listing_rows=[_listing_row()])

    with pytest.raises(MarketplaceError) as raised:
        _checkout(client, _raising_factory(client, "gateway refused the session"))

    assert raised.value.code == MARKETPLACE_CHECKOUT_UNAVAILABLE
    assert raised.value.http_status == 502
    assert raised.value.details.get("timed_out") is False

    assert client.deletes("library_subscriptions") == 0, (
        "the PENDING row must never be deleted on a provider failure (Requirement 9.4)"
    )
    assert len(client.subscription_rows) == 1
    stored = client.subscription_rows[0]
    assert stored["status"] == "payment_failed"
    assert stored["failure_cause"]
    assert "gateway refused the session" in stored["failure_cause"]
    assert stored["failed_at"]
    # The amount and the split stay on the row: the attempt is auditable.
    assert stored["price_minor"] == PRICE_MINOR_1999
    assert stored["owner_share_minor"] == OWNER_SHARE_1999
    assert stored["platform_fee_minor"] == PLATFORM_FEE_1999


def test_the_client_body_of_a_provider_failure_carries_no_provider_message():
    """Requirement 22.9: the internal cause is persisted, not disclosed.

    ``failure_cause`` holds the provider's own words; the client gets the catalogue sentence for
    ``MARKETPLACE_CHECKOUT_UNAVAILABLE`` and the caller's own identifiers.
    """
    client = FakeSupabase(listing_rows=[_listing_row()])

    with pytest.raises(MarketplaceError) as raised:
        _checkout(client, _raising_factory(client, "SELECT from psycopg blew up"))

    body = raised.value.to_error_object()
    serialised = repr(body).lower()
    assert "psycopg" not in serialised
    assert "select" not in serialised


def test_a_retry_after_a_failed_attempt_reuses_the_row_rather_than_inserting_beside_it():
    """``library_subscriptions`` carries UNIQUE (library_id, user_id), so a second insert raises.

    ``payment_failed -> pending`` is one of Requirement 11.2's twelve permitted edges, so the
    retry moves the existing row back to ``pending`` and clears the previous cause.
    """
    client = FakeSupabase(
        listing_rows=[_listing_row()],
        subscription_rows=[
            {
                **_subscription_row(status="payment_failed", row_id="sub-1"),
                "failure_cause": "the previous attempt failed",
                "failed_at": "2024-01-01T00:00:00+00:00",
            }
        ],
    )

    result = _checkout(client, _ok_factory(client, reference="cs_retry"))

    assert client.inserted_payloads("library_subscriptions") == []
    assert result.subscription_id == "sub-1"
    assert len(client.subscription_rows) == 1
    stored = client.subscription_rows[0]
    assert stored["status"] == "pending"
    assert stored["failure_cause"] is None
    assert stored["failed_at"] is None
    assert stored["provider_reference"] == "cs_retry"
    assert "expires_at" not in stored


# ══════════════════════════════════════════════════════════════════════════
# 7. THE SCHEMA CONTRACT AND THE ONE PROVIDER SET
# ══════════════════════════════════════════════════════════════════════════


def test_the_checkout_manifest_entry_exists_and_covers_both_projections():
    """The module's column reads sit inside its ``COLUMN_CONTRACT`` manifest entry (task 11.6)."""
    entry = COLUMN_CONTRACT.get("checkout")
    assert entry is not None, "COLUMN_CONTRACT has no 'checkout' entry"
    assert entry["module"] == "backend_app.backend.marketplace.checkout_service"

    declared = set()
    for columns in entry["tables"].values():
        declared |= set(columns)

    for literal in (cs.CHECKOUT_LISTING_SELECT, cs.CHECKOUT_SUBSCRIPTION_SELECT):
        for token in literal.replace("(", ",").replace(")", ",").split(","):
            name = token.strip()
            if name:
                assert name in declared, f"{name!r} is read but not declared in the manifest"

    # The write payload is bound too: a column no migration creates is a PGRST204 on a payment
    # path, so the PENDING insert's keys must be declared as well.
    subscription_columns = set(entry["tables"]["library_subscriptions"])
    client = FakeSupabase(listing_rows=[_listing_row()])
    _checkout(client, _ok_factory(client))
    for key in client.inserted_payloads("library_subscriptions")[0]:
        assert key in subscription_columns, f"{key!r} is written but not declared"


def test_no_third_payment_provider_can_be_constructed():
    """Requirement 9.1: exactly two providers, and a ProviderSession cannot name a third."""
    assert cs.SUPPORTED_PROVIDERS == frozenset({"stripe", "razorpay"})
    assert set(cs.PROVIDER_FOR_CURRENCY.values()) <= cs.SUPPORTED_PROVIDERS
    with pytest.raises(ValueError):
        ProviderSession(provider="paypal", reference="x")
    with pytest.raises(ValueError):
        ProviderSession(provider="stripe", reference="")


def test_inr_routes_to_razorpay_and_usd_to_stripe():
    """The same currency -> provider routing ``billing.create_payment_link`` already applies."""
    client = FakeSupabase(listing_rows=[_listing_row(currency="INR")])
    factory = _ok_factory(client, reference="order_abc")

    result = _checkout(client, factory, currency="INR")

    assert result.provider == "razorpay"
    assert result.currency == "INR"
    assert result.amount_minor == PRICE_MINOR_1999
    assert cs.PROVIDER_FOR_CURRENCY["USD"] == "stripe"


def test_a_currency_the_listing_is_not_priced_in_is_refused_without_conversion():
    """Requirement 10.7: no cross-currency arithmetic, so no FX-converted charge."""
    client = FakeSupabase(listing_rows=[_listing_row(currency="USD")])

    with pytest.raises(MarketplaceError) as raised:
        _checkout(client, _never_called_factory(), currency="INR")

    assert raised.value.code == MARKETPLACE_LISTING_NOT_PURCHASABLE
    assert raised.value.details.get("reason") == "currency_not_offered"
    assert not client.wrote_subscription()
