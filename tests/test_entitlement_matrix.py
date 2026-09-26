"""
tests/test_entitlement_matrix.py - the entitlement withdrawal matrix.

Feature: production-launch-hardening (bugfix), task 12.8, requirement 1.18 / clauses 2.18, 3.11.

WHAT THIS FILE PROVES
----------------------
Requirement 1.18 asks for the fact "entitlement is withdrawn on lapse" to be demonstrated as a
**matrix**: every Subscription state a caller can be in, crossed with every protected operation
the Entitlement_Resolver gates, with **the exact wire code** asserted on each cell - not merely
"refused" or "not 200", and not a status-code class. The four codes the resolver's own mapping
(``entitlement_resolver.WIRE_CODE_FOR_REASON``) can produce are:

    MARKETPLACE_NOT_SUBSCRIBED        403   no Subscription row at all
    MARKETPLACE_SUBSCRIPTION_EXPIRED  403   a Subscription existed; its period has ended
    MARKETPLACE_STRATEGY_UNAVAILABLE  409   the backing artifact no longer resolves
    MARKETPLACE_OPERATION_NOT_PERMITTED 403 the caller's own Subscription is administratively held

and the row this file adds beyond the four - the one clause 3.11 exists to keep separate -
is that a Persistence_Layer read that DID NOT COMPLETE answers ``MARKETPLACE_READ_FAILED``
(503) and is **never folded into** ``MARKETPLACE_NOT_SUBSCRIBED``: a paying subscriber must
never be told they hold no subscription because a read broke.

ROWS: THE SEVEN SUBSCRIPTION STATES, READ THROUGH THE RESOLVER'S OWN LADDER
----------------------------------------------------------------------------
``subscription_state.SubscriptionState`` fixes the vocabulary at seven values (Requirement
11.1): ``PENDING``, ``ACTIVE``, ``EXPIRED``, ``CANCELLED``, ``REFUNDED``, ``PAYMENT_FAILED``,
``SUSPENDED``. ``entitlement_resolver.resolve`` does not branch on the ``status`` column by
enumerating these seven spellings; it asks three narrower questions in order (see
``entitlement_resolver.py``'s own ``resolve()``, cited by line below):

    1. is there a Subscription row for this caller at all?                  -> NOT_SUBSCRIBED
    2. is the row's status the one held-administratively spelling?          -> SUSPENDED_reason
    3. is the row's status anything other than 'active'?                    -> EXPIRED
    4. is `now >= period_expiry` (sweep-independent, Requirement 11.7)?     -> EXPIRED
    5. otherwise, does the Listing's artifact resolve to a live version?   -> SUBSCRIBED / 409

So every one of the six non-``ACTIVE`` persisted states - ``PENDING``, ``EXPIRED``,
``CANCELLED``, ``REFUNDED``, ``PAYMENT_FAILED`` - answers through step 3 as EXPIRED (a lapse:
"a Subscription existed and its period has ended, or its status is a non-active, non-suspended
lapse" - the reason enum's own docstring), ``SUSPENDED`` answers through step 2, an ``ACTIVE``
row past its ``period_expiry`` answers through step 4 as the same EXPIRED code, and the
no-row-at-all case answers through step 1 as NOT_SUBSCRIBED. :data:`STATE_MATRIX_ROWS` states
this mapping explicitly, by name, for every one of the seven states plus the no-subscription
shape, rather than letting the property-test generators' vocabulary stand in for it - this is
the deliberately duplicated, human-readable table Requirement 1.18 asks a matrix test to be.

``tests/property/test_subscription_state_machine.py`` (P-8/P-9/P-10/P-15) and
``tests/property/test_entitlement_expiry_boundary.py`` (P-11) already prove the *state machine*
transitions and the *expiry boundary* itself, generatively, over many instants and many
sequences - this file does not re-derive either and does not duplicate their machinery (no
Hypothesis strategy, no store double that answers PostgREST filters, no sweep). It is the
one-shot, enumerated table the two property files' own docstrings describe as what a reader
should reach for the fixed vocabulary: every state, once, against every gated operation, once.

COLUMNS: THE PROTECTED OPERATIONS
----------------------------------
Grepping every call site of ``entitlement_resolver.resolve`` in ``backend_app/`` (never a
second, parallel admission check - Requirement 11.10, property P-16, "the deployment and
Paper_Session admission decision each equal the Entitlement_Resolver's decision") finds
exactly three:

    backend_app/routers/library.py:2088   clone_strategy            (POST /{id}/clone)
    backend_app/routers/library.py:3034   deploy_marketplace_strategy (POST /{id}/deploy)
    backend_app/backend/paper/paper_session_service.py:1556  start_session (Paper_Session start)

(``backend_app/backend/marketplace/library_entries.py`` also names the resolver's reason
vocabulary, but it does not call ``resolve`` and does not gate anything - it is
``entitlement_reason()``, the *display* mirror the browse/my-strategies cards use to show a
caller their own state, and card projection is task 12.9's surface. A display mirror is not a
protected operation: nothing it computes admits or refuses an action.)

Each column below drives its own real handler/service function, unwrapped, against a fake
Persistence_Layer double built for one cell - the same "call the handler directly with a fake
Supabase double" convention ``tests/test_marketplace_deployment_subscriber_safe.py`` and
``tests/property/test_access_agreement.py`` already use for ``deploy_marketplace_strategy``
and ``clone_strategy``, and the convention ``tests/sandbox_lifecycle/test_paper_session_lifecycle.py``
uses for ``start_session``. No ``TestClient`` exists anywhere in this repository's marketplace
test surface (grepped for), so none is introduced here.

``start_session`` is driven only as far as its own admission step (step 1 of its documented
pipeline, ``paper_session_service.py``'s own numbered comments) - the entitlement decision is
made and refused *before* the simulator guard, the version-lifecycle read, the capital check
and the market-metadata resolve, so every non-entitling and every read-failing cell raises
before any of that heavier infrastructure would be touched. The one entitling cell does not
assert a full session start (that is ``test_task_27_session_service.py``'s and
``test_paper_session_lifecycle.py``'s surface); it asserts the one fact this matrix owns - that
the admission step did not refuse - by asserting the pipeline got past
``PaperStartRefused(VALIDATION_ENTITLEMENT, ...)`` (it may then fail on the version/market
machinery this file deliberately starves, which is not what this cell is about).

THE WIRE CODE, CITED FROM THE RESOLVER'S OWN MAPPING
------------------------------------------------------
``backend_app/backend/marketplace/entitlement_resolver.py``'s ``WIRE_CODE_FOR_REASON``
(defined ~line 200-218) is the one place a reason becomes a wire code:

    EntitlementReason.NOT_SUBSCRIBED         -> MARKETPLACE_NOT_SUBSCRIBED        (403)
    EntitlementReason.EXPIRED                -> MARKETPLACE_SUBSCRIPTION_EXPIRED  (403)
    EntitlementReason.LISTING_UNAVAILABLE    -> MARKETPLACE_STRATEGY_UNAVAILABLE  (409)
    EntitlementReason.SUBSCRIPTION_SUSPENDED -> MARKETPLACE_OPERATION_NOT_PERMITTED (403)
    EntitlementReason.OWNED / SUBSCRIBED     -> None (entitling)

Both ``clone_strategy`` and ``deploy_marketplace_strategy`` raise
``MarketplaceError(entitlement.wire_code)`` verbatim (``library.py`` ~line 2100 and ~3057), so
this file's marketplace-route cells assert the SAME code the resolver's own mapping names -
never a second, hand-written expectation. ``start_session`` does not raise a ``MarketplaceError``
at all (paper's ``PaperError`` carries a distinct catalogue); its cells assert
``entitlement.reason.value`` on the raised ``PaperStartRefused.details["reason"]`` instead, which
is the same resolver reason carried through paper's own wire shape (``paper_session_service.py``
~line 1574-1584).

THE 3.11 NON-REGRESSION: A BROKEN READ IS NEVER A "NO SUBSCRIPTION" ANSWER
----------------------------------------------------------------------------
:class:`TestReadFailureIsNeverFoldedIntoARefusal` simulates the admission read itself raising
(the fake double's ``execute()`` raises ``RuntimeError``, the same fault
``entitlement_resolver._read_admission_row`` catches and re-raises as
``EntitlementReadFailed`` - see ``entitlement_resolver.py``'s "A READ THAT DOES NOT COMPLETE IS
NOT AN ANSWER" section) and asserts, for every one of the three operations, that the caller
receives the READ-FAILURE code at its documented status and status alone - never
``MARKETPLACE_NOT_SUBSCRIBED``, never any of the other three, and never a bare 403. This is
asserted on **wire codes**, not on status classes: a suite that only checked "not 403" would
pass an implementation that answered 500 with no code at all, which is not what Requirement
3.11 asks for.

WHAT IS REAL AND WHAT IS A DOUBLE
-----------------------------------
Real: ``entitlement_resolver.resolve`` (never re-implemented or stubbed - every cell drives the
actual function), ``routers.library.clone_strategy``, ``routers.library.deploy_marketplace_strategy``,
``backend.paper.paper_session_service.start_session``, the shared error catalogue
(``HTTP_STATUS_FOR_CODE``) and every status mapping.

Doubles, one per column, each a plain recording Persistence_Layer double scripted for the row
under test - the same shape (``.table(name).select(...).eq(...).execute()``) every marketplace
test double in this repository already implements. Nothing here is mocked with
``unittest.mock`` standing in for a resolver decision; every cell's answer is the resolver's own
output for the row the double serves.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend_app.backend.marketplace import entitlement_resolver as er
from backend_app.backend.marketplace.errors import (
    HTTP_STATUS_FOR_CODE,
    MARKETPLACE_NOT_SUBSCRIBED,
    MARKETPLACE_OPERATION_NOT_PERMITTED,
    MARKETPLACE_READ_FAILED,
    MARKETPLACE_STRATEGY_UNAVAILABLE,
    MARKETPLACE_SUBSCRIPTION_EXPIRED,
    MarketplaceError,
)
from backend_app.backend.paper import paper_session_service as pss
from backend_app.backend.paper.errors import PAPER_READ_FAILED
from backend_app.routers import library as lib

# ══════════════════════════════════════════════════════════════════════════
# Fixed identities - one row store per cell, never shared, so no cell can leak a fact into
# another's decision.
# ══════════════════════════════════════════════════════════════════════════

OWNER_ID = str(uuid4())
CALLER_ID = str(uuid4())
LISTING_ID = str(uuid4())
OWNER_STRATEGY_ID = str(uuid4())
OWNER_VERSION_ID = str(uuid4())
OWNER_VERSION_LABEL = "v2.0"

#: ``clone_strategy`` and ``deploy_marketplace_strategy`` call
#: ``entitlement_resolver.resolve`` with ``datetime.now(timezone.utc)`` internally - neither
#: route takes an injectable clock - so every instant this file compares an expiry against MUST
#: be anchored to the real wall clock rather than to a fixed calendar date, or a run on a later
#: machine date would silently move ``FUTURE_EXPIRY`` into the past. ``start_session`` is the
#: one column that DOES take ``now`` as an argument (Requirement 11.7's injected clock), and it
#: is passed the same anchored value so all three columns agree about what "now" means for one
#: test run.
NOW = datetime.now(timezone.utc)
PAST_EXPIRY = NOW - timedelta(days=5)
FUTURE_EXPIRY = NOW + timedelta(days=25)


def _run(coro: Any) -> Any:
    """Drive one coroutine to completion. A fresh loop per call is fine here: every cell is a
    handful of in-memory dict lookups, nothing awaits real I/O, and every other marketplace
    entitlement test in this repository (``test_marketplace_deployment_subscriber_safe.py``)
    uses the same ``asyncio.run`` per call for the same reason.
    """
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════════════
# THE SEVEN SUBSCRIPTION STATES, PLUS "NO ROW AT ALL", MAPPED THROUGH THE RESOLVER'S OWN
# LADDER (entitlement_resolver.py resolve(), the "── The caller's own Subscription…" section
# onward) TO THE REASON AND WIRE CODE THIS FILE ASSERTS PER CELL.
# ══════════════════════════════════════════════════════════════════════════


class SubscriptionRow:
    """One shape the ``library_subscriptions`` embed can carry for this caller.

    ``persisted_state`` names the ``SubscriptionState`` member this row instantiates (or
    ``None`` for the no-subscription-row shape), so the matrix is readable as "state x
    operation" and not merely "reason x operation" - the resolver collapses several states onto
    one reason, and naming both keeps that collapse visible rather than hidden by the test.
    """

    __slots__ = ("persisted_state", "status_text", "period_expiry", "has_row")

    def __init__(
        self,
        *,
        persisted_state: Optional[str],
        status_text: Optional[str],
        period_expiry: Optional[datetime],
        has_row: bool,
    ) -> None:
        self.persisted_state = persisted_state
        self.status_text = status_text
        self.period_expiry = period_expiry
        self.has_row = has_row

    def as_embedded_row(self) -> Dict[str, Any]:
        return {
            "id": str(uuid4()),
            "user_id": CALLER_ID,
            "status": self.status_text,
            "period_expiry": (
                None if self.period_expiry is None else self.period_expiry.isoformat()
            ),
        }


#: One row per ``SubscriptionState`` member (Requirement 11.1's seven, via
#: ``subscription_state.STATUS_TEXT_FOR_STATE``'s own spellings) plus the no-subscription shape.
#: An ``ACTIVE`` row is split into two named rows - unexpired and past its ``period_expiry`` -
#: because Requirement 1.18's whole point is that the SAME persisted label answers two different
#: wire codes depending on the instant, which is exactly Requirement 11.7 / property P-11's
#: sweep-independent boundary, cited here rather than re-derived.
NO_SUBSCRIPTION = SubscriptionRow(
    persisted_state=None, status_text=None, period_expiry=None, has_row=False
)
STATE_PENDING = SubscriptionRow(
    persisted_state="PENDING", status_text="pending", period_expiry=None, has_row=True
)
STATE_ACTIVE_UNEXPIRED = SubscriptionRow(
    persisted_state="ACTIVE", status_text="active", period_expiry=FUTURE_EXPIRY, has_row=True
)
STATE_ACTIVE_PAST_EXPIRY = SubscriptionRow(
    persisted_state="ACTIVE (lapsed)",
    status_text="active",
    period_expiry=PAST_EXPIRY,
    has_row=True,
)
STATE_EXPIRED = SubscriptionRow(
    persisted_state="EXPIRED", status_text="expired", period_expiry=PAST_EXPIRY, has_row=True
)
STATE_CANCELLED = SubscriptionRow(
    persisted_state="CANCELLED",
    status_text="cancelled",
    period_expiry=FUTURE_EXPIRY,
    has_row=True,
)
STATE_REFUNDED = SubscriptionRow(
    persisted_state="REFUNDED", status_text="refunded", period_expiry=PAST_EXPIRY, has_row=True
)
STATE_PAYMENT_FAILED = SubscriptionRow(
    persisted_state="PAYMENT_FAILED",
    status_text="payment_failed",
    period_expiry=None,
    has_row=True,
)
STATE_SUSPENDED = SubscriptionRow(
    persisted_state="SUSPENDED",
    status_text="suspended",
    period_expiry=FUTURE_EXPIRY,
    has_row=True,
)

#: (row label, the row shape, the EntitlementReason the resolver's ladder answers with, the wire
#: code that reason maps to). The reason and code columns are asserted against
#: ``er.WIRE_CODE_FOR_REASON`` below (``test_the_matrix_rows_cite_the_resolvers_own_ladder``),
#: not merely declared here, so this table cannot silently drift from the resolver it describes.
STATE_MATRIX_ROWS: List[tuple] = [
    ("no_subscription_row", NO_SUBSCRIPTION, er.EntitlementReason.NOT_SUBSCRIBED,
     MARKETPLACE_NOT_SUBSCRIBED),
    ("PENDING", STATE_PENDING, er.EntitlementReason.EXPIRED, MARKETPLACE_SUBSCRIPTION_EXPIRED),
    ("ACTIVE (unexpired)", STATE_ACTIVE_UNEXPIRED, er.EntitlementReason.SUBSCRIBED, None),
    ("ACTIVE (past period_expiry)", STATE_ACTIVE_PAST_EXPIRY, er.EntitlementReason.EXPIRED,
     MARKETPLACE_SUBSCRIPTION_EXPIRED),
    ("EXPIRED", STATE_EXPIRED, er.EntitlementReason.EXPIRED, MARKETPLACE_SUBSCRIPTION_EXPIRED),
    ("CANCELLED", STATE_CANCELLED, er.EntitlementReason.EXPIRED, MARKETPLACE_SUBSCRIPTION_EXPIRED),
    ("REFUNDED", STATE_REFUNDED, er.EntitlementReason.EXPIRED, MARKETPLACE_SUBSCRIPTION_EXPIRED),
    ("PAYMENT_FAILED", STATE_PAYMENT_FAILED, er.EntitlementReason.EXPIRED,
     MARKETPLACE_SUBSCRIPTION_EXPIRED),
    ("SUSPENDED", STATE_SUSPENDED, er.EntitlementReason.SUBSCRIPTION_SUSPENDED,
     MARKETPLACE_OPERATION_NOT_PERMITTED),
]

#: The one shape with no listing artifact behind it at all - LISTING_UNAVAILABLE / 409. Kept
#: separate from :data:`STATE_MATRIX_ROWS` because it is not a Subscription-state row: an
#: otherwise-entitling ACTIVE Subscription is used, and the refusal comes from the *Listing*
#: side of the resolver's ladder (Requirement 7.11) rather than from the Subscription side -
#: the fourth wire code the task names, and the matrix would be incomplete without it.
UNRESOLVABLE_ARTIFACT_LABEL = "ACTIVE, but source_strategy_id does not resolve"


def test_the_matrix_rows_cite_the_resolvers_own_ladder() -> None:
    """Every declared (row, reason, code) triple is the SAME triple ``resolve()`` itself would
    give for that row, and the code is exactly ``er.WIRE_CODE_FOR_REASON``'s - never a
    hand-maintained duplicate of the mapping the file's docstring quotes.
    """
    for label, row, expected_reason, expected_code in STATE_MATRIX_ROWS:
        assert er.WIRE_CODE_FOR_REASON[expected_reason] == expected_code, (
            f"row {label!r} declares reason {expected_reason.value} -> {expected_code!r}, "
            f"which disagrees with the resolver's own WIRE_CODE_FOR_REASON"
        )
    # Every EntitlementReason the resolver can refuse with appears in this file at least once,
    # OWNED/SUBSCRIBED excluded (they entitle and carry no wire code) - so no refusal reason is
    # silently missing from the matrix.
    refusal_reasons = set(er.EntitlementReason) - er.ENTITLING_REASONS
    covered = {
        reason for _, _, reason, _ in STATE_MATRIX_ROWS if reason in refusal_reasons
    }
    covered.add(er.EntitlementReason.LISTING_UNAVAILABLE)  # the unresolvable-artifact row
    assert covered == refusal_reasons, (
        f"the matrix does not cover every refusal reason: missing "
        f"{sorted(r.value for r in refusal_reasons - covered)}"
    )


# ══════════════════════════════════════════════════════════════════════════
# ONE DOUBLE, SERVING THE THREE READS ALL THREE OPERATIONS' ADMISSION STEP ISSUES:
# the embedded library_strategies/marketplace_submissions/library_subscriptions read, and the
# strategy_versions current-version read. Failure injection is by TABLE NAME, so
# "the admission read did not complete" is modelled the same way for every column.
# ══════════════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    def __init__(self, table: str, store: "EntitlementRowStore") -> None:
        self.table_name = table
        self.store = store
        self.op = "select"
        self.payload: Optional[Dict[str, Any]] = None
        self.columns: Optional[str] = None

    def select(self, cols: Any) -> "_Query":
        self.columns = cols
        return self

    def insert(self, payload: Dict[str, Any]) -> "_Query":
        self.op = "insert"
        self.payload = dict(payload)
        return self

    def eq(self, _col: str, _val: Any) -> "_Query":
        return self

    def single(self) -> "_Query":
        return self

    def execute(self) -> _Resp:
        return self.store._execute(self)


class EntitlementRowStore:
    """One Listing, one caller Subscription row (or none), one version - the shapes the
    admission read and the version read need. ``raise_on`` names the table whose ``execute()``
    raises, which is what a driver failure looks like to
    ``entitlement_resolver._read_admission_row`` / ``_resolve_current_version`` - both catch a
    bare ``Exception`` and re-raise ``EntitlementReadFailed`` (Requirement 30.5).
    """

    def __init__(
        self,
        *,
        subscription: SubscriptionRow,
        version_resolves: bool = True,
        submission_state: str = "PUBLISHED",
        raise_on: Optional[str] = None,
    ) -> None:
        self.subscription = subscription
        self.version_resolves = version_resolves
        self.submission_state = submission_state
        self.raise_on = raise_on
        self.calls: List[_Query] = []
        self.writes: List[_Query] = []

    def table(self, name: str) -> _Query:
        return _Query(name, self)

    def _execute(self, query: _Query) -> _Resp:
        self.calls.append(query)
        if query.op == "insert":
            self.writes.append(query)
            row = dict(query.payload or {})
            row.setdefault("id", str(uuid4()))
            return _Resp([row])
        if query.table_name == "library_strategies":
            # BOTH clone_strategy's own step-1 lookup AND entitlement_resolver's embedded
            # admission read select from this same table through this same injected client
            # (there is exactly ONE `_build_service_client()` call per request - Requirement
            # 11.10, property P-16 - so there is no second client to hand the resolver). The
            # two reads are told apart the way PostgREST itself would tell them apart: by the
            # requested COLUMNS. Only the resolver's own projection
            # (`entitlement_resolver._ENTITLEMENT_SELECT`) asks for the embedded
            # `library_subscriptions(...)` collection; clone's plain step-1 lookup
            # (`"id, author_id, source_strategy_id, name, is_active, moderation_status, "
            # "clone_count, source_cloning_enabled"`) never does. ``raise_on`` therefore fails
            # only the EMBEDDED read when it names "library_strategies" - clone's own,
            # unrelated step-1 lookup failing is a pre-existing 500 this file is not about.
            requested = str(query.columns or "")
            if "library_subscriptions" in requested:
                if self.raise_on == "library_strategies":
                    raise RuntimeError("the admission read did not complete")
                embedded_subscriptions = (
                    []
                    if not self.subscription.has_row
                    else [self.subscription.as_embedded_row()]
                )
                return _Resp(
                    [
                        {
                            "id": LISTING_ID,
                            "author_id": OWNER_ID,
                            "source_strategy_id": OWNER_STRATEGY_ID,
                            "source_cloning_enabled": False,
                            "marketplace_submissions": [
                                {"submission_state": self.submission_state}
                            ],
                            "library_subscriptions": embedded_subscriptions,
                        }
                    ]
                )
            # clone_strategy's own step-1 lookup calls `.single()` and reads `lib_resp.data` as
            # a bare mapping (`lib_entry["is_active"]`), never as a list - so the response here
            # carries the row directly on `.data`, matching what `.single()` returns in the
            # real driver. It is active, approved, not self-owned by the caller, and
            # cloning-enabled, so every refusal a cell in this file observes on the clone
            # column is the entitlement gate's, and only the entitlement gate's.
            return _Resp(
                {
                    "id": LISTING_ID,
                    "author_id": OWNER_ID,
                    "source_strategy_id": OWNER_STRATEGY_ID,
                    "name": "Matrix Fixture Strategy",
                    "is_active": True,
                    "moderation_status": "approved",
                    "clone_count": 0,
                    "source_cloning_enabled": True,
                }
            )
        if query.table_name == "strategy_versions":
            if self.raise_on == "strategy_versions":
                raise RuntimeError("the version read did not complete")
            if not self.version_resolves:
                return _Resp([])
            return _Resp(
                [
                    {
                        "id": OWNER_VERSION_ID,
                        "strategy_id": OWNER_STRATEGY_ID,
                        "version": OWNER_VERSION_LABEL,
                        "is_draft": False,
                    }
                ]
            )
        if query.table_name == "strategies":
            # clone_strategy's step 5 (idempotency check, run only once the entitlement gate
            # has cleared): answering "already cloned" here lets the entitling cell prove the
            # gate was passed and stop there, without pulling clone_strategy's DAG-copy step
            # (step 6 onward - a different concern this matrix is not about) into scope.
            return _Resp([{"id": str(uuid4())}])
        return _Resp([])  # pragma: no cover - no other table is read on this path


class _FakeRequest:
    """The one thing ``clone_strategy`` / ``deploy_marketplace_strategy`` use off ``Request``."""

    def __init__(self, body: Any = None) -> None:
        self._body = body

    async def json(self) -> Any:
        return self._body


class _FakeAuditLogger:
    async def log(self, _action: Any, **_kwargs: Any) -> None:
        return None


@pytest.fixture(autouse=True)
def _rate_limiter_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand ``slowapi``'s decorator down for a direct handler call - the same reason and the
    same seam ``tests/test_marketplace_deployment_subscriber_safe.py`` uses: the wrapper demands
    a genuine ``starlette.requests.Request`` and would otherwise refuse every call in this
    module, and a shared limiter key would make later cells depend on how many earlier cells ran.
    Coverage of the decorator's presence and limit stays in
    ``tests/test_task_33_2_rate_limit_keys.py``.
    """
    from backend_app.core.rate_limit import limiter

    monkeypatch.setattr(limiter, "enabled", False, raising=False)


@pytest.fixture(autouse=True)
def _audit_logger_stubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route every Audit_Log write into a no-op recorder, so no cell needs Redis reachable."""
    monkeypatch.setattr(lib, "get_strategy_audit_logger", lambda: _FakeAuditLogger())


# ══════════════════════════════════════════════════════════════════════════
# COLUMN 1: clone_strategy — POST /{library_id}/clone
# ══════════════════════════════════════════════════════════════════════════


def _clone(store: EntitlementRowStore, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Drive the real, unwrapped ``clone_strategy`` handler against ``store``.

    The two library-lookup gates that run BEFORE the entitlement check (existence,
    active/moderation, self-clone, owner-consent) are cleared by construction (see
    ``EntitlementRowStore._execute``'s ``library_strategies`` branch): the listing is active,
    approved, not self-owned by ``CALLER_ID``, and cloning-enabled, so every refusal this
    column's cells observe is the entitlement gate's, and only the entitlement gate's.

    There is exactly ONE injected client per request (``_build_service_client()`` is called
    once and the same handle is passed to ``entitlement_resolver.resolve`` - Requirement 11.10,
    property P-16), so ``store`` alone - not a second, clone-specific wrapper - answers both
    ``library_strategies`` reads the handler and the resolver issue, told apart by their
    requested columns exactly as PostgREST itself would tell them apart.
    """
    monkeypatch.setattr(lib, "_build_service_client", lambda: store)
    return _run(
        lib.clone_strategy(
            _FakeRequest(),
            LISTING_ID,
            user={"id": CALLER_ID},
            _feature=None,
        )
    )


@pytest.mark.parametrize("label, row, expected_reason, expected_code", STATE_MATRIX_ROWS)
def test_clone_strategy_wire_code_per_subscription_state(
    monkeypatch: pytest.MonkeyPatch, label: str, row: SubscriptionRow, expected_reason: Any,
    expected_code: Optional[str],
) -> None:
    store = EntitlementRowStore(subscription=row)
    if expected_code is None:
        result = _clone(store, monkeypatch)
        assert result["message"] or result.get("new_strategy_id"), (
            f"clone_strategy refused an entitling row (state={label}); "
            f"resolver reason should have been {expected_reason.value}"
        )
        return
    with pytest.raises(MarketplaceError) as caught:
        _clone(store, monkeypatch)
    assert caught.value.code == expected_code, (
        f"clone_strategy: state={label} expected {expected_code!r}, got "
        f"{caught.value.code!r}"
    )
    assert caught.value.http_status == HTTP_STATUS_FOR_CODE[expected_code]


def test_clone_strategy_unresolvable_artifact_is_409_strategy_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EntitlementRowStore(subscription=STATE_ACTIVE_UNEXPIRED, version_resolves=False)
    with pytest.raises(MarketplaceError) as caught:
        _clone(store, monkeypatch)
    assert caught.value.code == MARKETPLACE_STRATEGY_UNAVAILABLE
    assert caught.value.http_status == 409


# ══════════════════════════════════════════════════════════════════════════
# COLUMN 2: deploy_marketplace_strategy — POST /{library_id}/deploy
# ══════════════════════════════════════════════════════════════════════════


def _deploy(store: EntitlementRowStore, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(lib, "_build_service_client", lambda: store)
    return _run(
        lib.deploy_marketplace_strategy(
            _FakeRequest({}),
            LISTING_ID,
            user={"id": CALLER_ID},
        )
    )


@pytest.mark.parametrize("label, row, expected_reason, expected_code", STATE_MATRIX_ROWS)
def test_deploy_strategy_wire_code_per_subscription_state(
    monkeypatch: pytest.MonkeyPatch, label: str, row: SubscriptionRow, expected_reason: Any,
    expected_code: Optional[str],
) -> None:
    store = EntitlementRowStore(subscription=row)
    if expected_code is None:
        result = _deploy(store, monkeypatch)
        assert result["granted_via"] == er.EntitlementReason.SUBSCRIBED.value, (
            f"deploy_marketplace_strategy refused an entitling row (state={label})"
        )
        return
    with pytest.raises(MarketplaceError) as caught:
        _deploy(store, monkeypatch)
    assert caught.value.code == expected_code, (
        f"deploy_marketplace_strategy: state={label} expected {expected_code!r}, got "
        f"{caught.value.code!r}"
    )
    assert caught.value.http_status == HTTP_STATUS_FOR_CODE[expected_code]


def test_deploy_strategy_unresolvable_artifact_is_409_strategy_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EntitlementRowStore(subscription=STATE_ACTIVE_UNEXPIRED, version_resolves=False)
    with pytest.raises(MarketplaceError) as caught:
        _deploy(store, monkeypatch)
    assert caught.value.code == MARKETPLACE_STRATEGY_UNAVAILABLE
    assert caught.value.http_status == 409


# ══════════════════════════════════════════════════════════════════════════
# COLUMN 3: start_session — the Paper_Session admission step
# ══════════════════════════════════════════════════════════════════════════


class _Caller:
    def __init__(self, user_id: str = CALLER_ID) -> None:
        self.id = user_id


def _start(store: EntitlementRowStore) -> Any:
    """Drive the real ``start_session`` far enough to reach its admission step (step 1 of its
    own documented pipeline) and no further. The placeholder ``exchange_id`` / ``symbol`` /
    ``timeframe`` / ``initial_capital_minor`` values are never read for a refused or
    read-failed admission - the function raises at step 1, before the simulator guard, the
    version-lifecycle read, the capital check or the market-metadata resolve.
    """
    return _run(
        pss.start_session(
            store,
            _Caller(),
            listing_id=LISTING_ID,
            exchange_id="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            initial_capital_minor=100_000,
            now=NOW,
        )
    )


@pytest.mark.parametrize("label, row, expected_reason, expected_code", STATE_MATRIX_ROWS)
def test_start_session_wire_code_per_subscription_state(
    label: str, row: SubscriptionRow, expected_reason: Any, expected_code: Optional[str],
) -> None:
    store = EntitlementRowStore(subscription=row)
    if expected_code is None:
        # The entitling cell: the admission step must NOT be what refuses this call. Whatever
        # happens next (this file starves the market/feed/version machinery on purpose - see
        # the module docstring), it must not be PaperStartRefused naming the entitlement gate.
        try:
            _start(store)
        except pss.PaperStartRefused as exc:
            assert exc.details.get("reason") != er.EntitlementReason.NOT_SUBSCRIBED.value
            assert exc.code != "PAPER_START_REFUSED" or exc.details.get("validation") != (
                pss.VALIDATION_ENTITLEMENT
            ), (
                f"start_session refused an entitling row (state={label}) at the admission step: "
                f"{exc.details}"
            )
        except Exception:  # noqa: BLE001 - anything past the admission gate is out of scope here
            pass
        return
    with pytest.raises(pss.PaperStartRefused) as caught:
        _start(store)
    assert caught.value.details.get("reason") == expected_reason.value, (
        f"start_session: state={label} expected resolver reason {expected_reason.value}, got "
        f"{caught.value.details.get('reason')!r}"
    )
    assert caught.value.http_status == 403, (
        "every entitlement refusal from start_session's admission step is 403 - the resolver's "
        "409 (LISTING_UNAVAILABLE) and the resolver's other 403s are both reported through "
        "PaperStartRefused at 403, naming the reason in details rather than choosing a second "
        "status per reason (paper_session_service.py step 1)"
    )


def test_start_session_unresolvable_artifact_is_reported_as_listing_unavailable() -> None:
    store = EntitlementRowStore(subscription=STATE_ACTIVE_UNEXPIRED, version_resolves=False)
    with pytest.raises(pss.PaperStartRefused) as caught:
        _start(store)
    assert caught.value.details.get("reason") == er.EntitlementReason.LISTING_UNAVAILABLE.value


# ══════════════════════════════════════════════════════════════════════════
# THE 3.11 NON-REGRESSION: MARKETPLACE_READ_FAILED is never folded into a refusal
# ══════════════════════════════════════════════════════════════════════════


class TestReadFailureIsNeverFoldedIntoARefusal:
    """For every one of the three protected operations, a Persistence_Layer read that DID NOT
    COMPLETE answers the read-failure code at its documented status, and NEVER one of the four
    "no subscription" / "not permitted" codes - Requirement 3.11's own wording. The row served
    to each double is otherwise an ENTITLING one (an unexpired ACTIVE Subscription): if the
    broken read were folded into a refusal at all, it would be folded into NOT_SUBSCRIBED
    specifically, because that is the code a caller with no admission decision would land on if
    an implementation defaulted "the read failed" to "treat it as absent" - so this is the
    sharpest row to prove it against.
    """

    def test_clone_strategy_read_failure_is_503_never_403(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = EntitlementRowStore(
            subscription=STATE_ACTIVE_UNEXPIRED, raise_on="library_strategies"
        )
        # clone_strategy's OWN pre-entitlement lookup reads library_strategies too (step 1,
        # unrelated to the resolver, and its own unrelated 500 if it failed). `raise_on` here
        # fails only the EMBEDDED admission read (see EntitlementRowStore._execute), so
        # step 1 succeeds and the failure this test observes is the resolver's own.
        with pytest.raises(MarketplaceError) as caught:
            _clone(store, monkeypatch)
        assert caught.value.code == MARKETPLACE_READ_FAILED
        assert caught.value.http_status == 503
        assert caught.value.code != MARKETPLACE_NOT_SUBSCRIBED
        assert caught.value.code not in (
            MARKETPLACE_SUBSCRIPTION_EXPIRED,
            MARKETPLACE_STRATEGY_UNAVAILABLE,
            MARKETPLACE_OPERATION_NOT_PERMITTED,
        )

    def test_clone_strategy_version_read_failure_is_503_never_403_or_409(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = EntitlementRowStore(
            subscription=STATE_ACTIVE_UNEXPIRED, raise_on="strategy_versions"
        )
        with pytest.raises(MarketplaceError) as caught:
            _clone(store, monkeypatch)
        assert caught.value.code == MARKETPLACE_READ_FAILED
        assert caught.value.code != MARKETPLACE_STRATEGY_UNAVAILABLE, (
            "a broken version read must never be reported as LISTING_UNAVAILABLE - that would "
            "tell a subscriber the Listing's artifact is gone when in fact nothing was read"
        )
        assert caught.value.code != MARKETPLACE_NOT_SUBSCRIBED

    def test_deploy_strategy_read_failure_is_503_never_403(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = EntitlementRowStore(
            subscription=STATE_ACTIVE_UNEXPIRED, raise_on="library_strategies"
        )
        with pytest.raises(MarketplaceError) as caught:
            _deploy(store, monkeypatch)
        assert caught.value.code == MARKETPLACE_READ_FAILED
        assert caught.value.http_status == 503
        assert caught.value.code != MARKETPLACE_NOT_SUBSCRIBED
        assert caught.value.code not in (
            MARKETPLACE_SUBSCRIPTION_EXPIRED,
            MARKETPLACE_STRATEGY_UNAVAILABLE,
            MARKETPLACE_OPERATION_NOT_PERMITTED,
        )

    def test_deploy_strategy_version_read_failure_is_503_never_409(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = EntitlementRowStore(
            subscription=STATE_ACTIVE_UNEXPIRED, raise_on="strategy_versions"
        )
        with pytest.raises(MarketplaceError) as caught:
            _deploy(store, monkeypatch)
        assert caught.value.code == MARKETPLACE_READ_FAILED
        assert caught.value.code != MARKETPLACE_STRATEGY_UNAVAILABLE

    def test_start_session_read_failure_is_paper_read_failed_never_a_refusal(self) -> None:
        store = EntitlementRowStore(
            subscription=STATE_ACTIVE_UNEXPIRED, raise_on="library_strategies"
        )
        with pytest.raises(pss.PaperError) as caught:
            _start(store)
        assert caught.value.code == PAPER_READ_FAILED, (
            "start_session's admission read failing must answer paper's own read-failure code "
            "(the PAPER_* counterpart of MARKETPLACE_READ_FAILED), never PaperStartRefused - a "
            "broken read is not a validation the caller's request failed"
        )
        assert not isinstance(caught.value, pss.PaperStartRefused), (
            "a read that did not complete must not be reported through PaperStartRefused, "
            "which is reserved for a real validation naming the caller's actual situation"
        )
        assert caught.value.http_status == 503

    def test_start_session_version_read_failure_is_paper_read_failed(self) -> None:
        store = EntitlementRowStore(
            subscription=STATE_ACTIVE_UNEXPIRED, raise_on="strategy_versions"
        )
        with pytest.raises(pss.PaperError) as caught:
            _start(store)
        assert caught.value.code == PAPER_READ_FAILED
        assert not isinstance(caught.value, pss.PaperStartRefused)

    def test_the_read_failure_code_itself_is_distinct_from_all_four_wire_codes(self) -> None:
        """A domain-agnostic restatement of the same fact, independent of any route: the
        resolver's ``EntitlementReadFailed.wire_code`` is not, and can never be conflated with,
        any of the four codes this matrix's cells assert.
        """
        assert er.EntitlementReadFailed.wire_code == MARKETPLACE_READ_FAILED
        four_refusal_codes = {
            MARKETPLACE_NOT_SUBSCRIBED,
            MARKETPLACE_SUBSCRIPTION_EXPIRED,
            MARKETPLACE_STRATEGY_UNAVAILABLE,
            MARKETPLACE_OPERATION_NOT_PERMITTED,
        }
        assert er.EntitlementReadFailed.wire_code not in four_refusal_codes
        assert HTTP_STATUS_FOR_CODE[MARKETPLACE_READ_FAILED] == 503
