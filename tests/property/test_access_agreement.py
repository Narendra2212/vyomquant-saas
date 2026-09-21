"""Property test for access agreement.

Feature: marketplace-subscriptions-paper-trading
Task 17.7 — **Validates: Requirements 11.10, 11.16, 7.10**
Design reference: ``design.md § Property-to-test mapping`` ->
``P-16 | tests/property/test_access_agreement.py | subscriptions × instants | one call to
entitlement_resolver.resolve compared against both admission gates``; and
``design.md § marketplace/entitlement_resolver.py and subscriber execution`` -> "``resolve`` is
the single admission decision for deployment and for Paper_Session start, so P-16's … holds
because there is only one decision".

Property living here
--------------------
``test_p16_admission_agrees_with_the_resolver``. One property, one test function, so the
scoreboard in ``tests/property/test_property_coverage.py`` reads exactly one ``test_p16_`` name.

WHAT P-16 CLAIMS
----------------
The deployment admission decision and the Paper_Session admission decision each equal
``entitlement_resolver.resolve``'s decision for the same Subscription at the same instant.

It is an *agreement* property, not a policy property: it does not say which callers are admitted
— Requirement 7.10's distinct codes and Requirement 11.7's sweep-independent expiry say that,
and ``tests/test_entitlement_resolver.py`` holds the resolver to them. P-16 says the two
execution-granting gates cannot disagree with each other or with the resolver. That is worth
stating separately because the codebase used to have exactly that disagreement:
``check_deployment_permission`` admitted a row whose stored ``status`` read ``'active'`` and
treated a missing expiry as perpetual, while the period said the access had ended.

HOW THE AGREEMENT IS ASSERTED — AS VALUES, NOT AS OUTCOMES
---------------------------------------------------------
For each generated case the test:

1. calls :func:`entitlement_resolver.resolve` directly, with the instant injected, and checks the
   reason against the case's own declared expectation. That second check is what keeps the case
   pool honest: a generator that quietly produced ``NOT_SUBSCRIBED`` for every kind would make
   the agreement trivially true, and this catches it per example rather than in review;
2. drives ``library.deploy_marketplace_strategy`` against an identical row store with the clock
   frozen at the *same* instant, with :func:`resolve` wrapped in a pass-through spy;
3. asserts the spy was called **exactly once**, with the same caller, the same Listing and the
   same instant, and that the :class:`Entitlement` it returned **equals** the one from step 1.
   :class:`Entitlement` is a frozen dataclass, so this is value equality over the whole decision
   — reason, entitling flag, and every identifier — not merely "both refused";
4. asserts the *outcome* follows that decision: an entitling decision produces exactly one
   ``strategy_deployments`` row and a ``granted_via`` equal to the reason; a non-entitling one
   raises the reason's own wire code at the reason's own HTTP status and writes nothing at all;
5. asserts ``check_deployment_permission`` — the old gate, still the implementation of
   ``GET /{library_id}/deploy/check`` — was not consulted. A second admission check is a second
   answer that could disagree, so "there is only one decision" is asserted rather than assumed.

WHY THE CLOCK IS FROZEN
-----------------------
"For the same Subscription **at the same instant**" is load-bearing. ``resolve`` takes ``now`` as
an argument (that is what makes the expiry comparison a pure, pinnable comparison), while the
route reads ``datetime.now(timezone.utc)`` itself. Comparing a decision taken at one instant with
a decision taken a few microseconds later cannot state this property at the expiry boundary: the
interesting cases are ``period_expiry == now`` and ``period_expiry == now + 1µs``, and a real
clock flips the second one at random. So ``library.datetime`` is replaced per example by a
``datetime`` subclass whose ``now`` returns the generated instant. Both halves then read the same
instant by construction, and :data:`AT_THE_EXPIRY_INSTANT` and
:data:`ONE_MICROSECOND_BEFORE_EXPIRY` become assertable rather than flaky.

WHY THE PROPERTY IS NOT VACUOUS
-------------------------------
If every generated case refused, "the two gates agree" would hold for free. Three guards:

* the case pool spans **all six** resolver reasons and **both sides** of the expiry boundary —
  including the sweep-independence case Requirement 11.7 names (``status = 'active'`` with a past
  ``period_expiry``) and the null-expiry shape Requirement 11.16's removed ``renew_subscription``
  path left behind (``status = 'active'``, expiry cleared);
* three of the kinds are **entitling**, and for those the property asserts a deployment row is
  actually written, so at least a third of the examples exercise the admit path end to end;
* :func:`test_every_admission_case_kind_is_covered_and_the_pool_admits_and_refuses` enumerates
  the pool deterministically, independently of what Hypothesis happens to draw, and fails if a
  reason stops being represented or if the pool becomes all-refusals.

THE PAPER_SESSION HALF — WHAT TASK 27.1 OWES
-------------------------------------------
P-16 is about *both* gates. ``backend_app/backend/paper/paper_session_service.py`` does not exist
yet — ``start_session`` is task 27.1, whose first pipeline step is
``ent <- entitlement_resolver.resolve(...)`` (``design.md § paper/paper_session_service.py``). So
the Paper_Session half cannot be exercised today. It is registered as a **strict xfail** naming
task 27.1 rather than skipped or omitted: it must keep failing until 27.1 lands, and the moment
it starts passing THIS FILE FAILS with ``XPASS``, which is the signal to fold the Paper_Session
gate into the property above. That is the pattern ``tests/test_marketplace_error_surface.py`` and
``tests/test_subscriber_restricted_operations.py`` both use, applied unchanged.

Registered strict xfails in this module (one), with its owning task:

* ``test_the_paper_session_start_path_admits_on_the_same_resolver`` — owner **task 27.1**.

WHAT IS REAL AND WHAT IS A DOUBLE
---------------------------------
Real: ``entitlement_resolver.resolve``, ``library.deploy_marketplace_strategy``, its request
model, ``_owner_version_label``, the error catalogue and every status mapping.

Doubles: the service-role client (a scripted-read, recording-write stand-in — recording is what
makes "wrote nothing at all" an assertion), the Audit_Log writer, and the clock. The double is
local to this module rather than imported from
``tests/test_marketplace_deployment_subscriber_safe.py``: this property needs the resolver and
the route to read *one* row store at *one* frozen instant, and a property test that reaches into
another test module's private helpers breaks whenever that module's task revisits them.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import textwrap
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, NamedTuple, Optional, Tuple
from unittest import mock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.marketplace import entitlement_resolver as er
from backend_app.backend.marketplace.entitlement_resolver import (
    Entitlement,
    EntitlementReason,
)
from backend_app.backend.marketplace.errors import HTTP_STATUS_FOR_CODE, MarketplaceError
from backend_app.core.rate_limit import limiter as _limiter
from backend_app.routers import library as lib
from tests.strategies.marketplace_generators import identifiers, utc_instants

#: The configuration ``design.md § Property-based testing configuration`` prescribes for every
#: property test in this plan: at least 100 examples and no per-example deadline (the first
#: example pays the import cost).
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# ══════════════════════════════════════════════════════════════════════════
# Identities and identifiers
# ══════════════════════════════════════════════════════════════════════════
#
# Fixed, not generated. P-16 is quantified over *subscriptions and instants* (design.md's
# generator column); the identities are the two roles the decision distinguishes — the Listing's
# owner and a caller who is not the owner — and generating them would add draws without adding a
# case. `tests/property/test_tenant_isolation_matrix.py` owns identity quantification.

OWNER_ID = "aa000000-0000-4000-8000-00000000f001"
CALLER_ID = "bb000000-0000-4000-8000-00000000f002"
LISTING_ID = "cc000000-0000-4000-8000-00000000f003"
OWNER_STRATEGY_ID = "dd000000-0000-4000-8000-00000000f004"
OWNER_VERSION_ID = "ee000000-0000-4000-8000-00000000f005"
OWNER_VERSION_LABEL = "v2.7"

#: The persisted ``library_subscriptions.status`` values that are neither ``active`` nor
#: ``suspended``, and are therefore a lapse the resolver answers ``EXPIRED``.
LAPSED_STATUSES: Tuple[str, ...] = (
    "expired",
    "cancelled",
    "refunded",
    "payment_failed",
    "pending",
)

#: Submission states that still entitle an ACTIVE, unexpired Subscription (Requirement 4.11): a
#: Listing going SUSPENDED or UNPUBLISHED does not revoke access already paid for.
LIVE_SUBMISSION_STATES: Tuple[str, ...] = ("PUBLISHED", "SUSPENDED", "UNPUBLISHED")

#: Submission states under which the Listing itself is unavailable.
DEAD_SUBMISSION_STATES: Tuple[str, ...] = (
    "DRAFT",
    "SUBMITTED",
    "UNDER_REVIEW",
    "APPROVED",
    "REJECTED",
)

#: Named for readability in the case table below. Both are exact instants relative to ``now``,
#: which is only meaningful because the clock is frozen (see the module docstring).
AT_THE_EXPIRY_INSTANT = timedelta(0)
ONE_MICROSECOND_BEFORE_EXPIRY = timedelta(microseconds=1)


# ══════════════════════════════════════════════════════════════════════════
# The case pool: subscriptions × instants, one declared reason each
# ══════════════════════════════════════════════════════════════════════════


class AdmissionCase(NamedTuple):
    """One row store plus the instant to decide at, and the reason it is expected to produce."""

    kind: str
    expected_reason: EntitlementReason
    now: datetime
    listing_rows: List[Dict[str, Any]]
    version_rows: List[Dict[str, Any]]
    caller_id: str


#: Every case kind, with the resolver reason it must produce. This mapping is the pool's
#: contract: the property asserts each drawn case against its own entry, and
#: :func:`test_every_admission_case_kind_is_covered_and_the_pool_admits_and_refuses` asserts the
#: mapping covers all six reasons and contains both admissions and refusals.
EXPECTED_REASON_FOR_KIND: Dict[str, EntitlementReason] = {
    # ── entitling ──
    "caller_is_the_owner": EntitlementReason.OWNED,
    "active_and_unexpired": EntitlementReason.SUBSCRIBED,
    # Requirement 11.7's boundary, entitling side: expiry one microsecond in the future.
    "active_expiring_one_microsecond_from_now": EntitlementReason.SUBSCRIBED,
    # ── refusing ──
    "no_subscription_row": EntitlementReason.NOT_SUBSCRIBED,
    # Requirement 11.7's boundary, refusing side: `now >= period_expiry` is not entitling, so the
    # instant the expiry is reached the access has ended.
    "active_expiring_exactly_now": EntitlementReason.EXPIRED,
    # Requirement 11.7's sweep-independence: the stored status still reads 'active' because the
    # expiry sweep has not run (or is dead), and the period has ended anyway.
    "active_status_with_a_past_expiry": EntitlementReason.EXPIRED,
    # Requirement 11.16: the shape the removed `renew_subscription` path produced — status set
    # to active and the expiry cleared, with no confirmed payment. A null expiry is not a
    # perpetual entitlement.
    "active_status_with_a_cleared_expiry": EntitlementReason.EXPIRED,
    "lapsed_status": EntitlementReason.EXPIRED,
    "suspended_subscription": EntitlementReason.SUBSCRIPTION_SUSPENDED,
    "listing_row_absent": EntitlementReason.LISTING_UNAVAILABLE,
    "submission_not_live": EntitlementReason.LISTING_UNAVAILABLE,
    "no_submission_at_all": EntitlementReason.LISTING_UNAVAILABLE,
    "owner_version_unresolvable": EntitlementReason.LISTING_UNAVAILABLE,
}

CASE_KINDS: Tuple[str, ...] = tuple(EXPECTED_REASON_FOR_KIND)


def _version_rows() -> List[Dict[str, Any]]:
    """One saved, non-draft version — what makes ``source_strategy_id`` resolve."""
    return [
        {
            "id": OWNER_VERSION_ID,
            "strategy_id": OWNER_STRATEGY_ID,
            "version": OWNER_VERSION_LABEL,
            "is_draft": False,
        }
    ]


def _subscription(
    *,
    subscription_id: str,
    status: str,
    period_expiry: Optional[datetime],
) -> Dict[str, Any]:
    """The caller's own ``library_subscriptions`` row, as the embedded read returns it."""
    return {
        "id": subscription_id,
        "user_id": CALLER_ID,
        "status": status,
        "period_expiry": None if period_expiry is None else period_expiry.isoformat(),
    }


def _listing_row(
    *,
    author_id: str = OWNER_ID,
    submission_states: Tuple[str, ...] = ("PUBLISHED",),
    subscription: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """A ``library_strategies`` row with the two resources the resolver's one read embeds."""
    return {
        "id": LISTING_ID,
        "author_id": author_id,
        "source_strategy_id": OWNER_STRATEGY_ID,
        "source_cloning_enabled": False,
        "marketplace_submissions": [
            {"submission_state": state} for state in submission_states
        ],
        "library_subscriptions": [] if subscription is None else [subscription],
    }


def build_case(  # noqa: C901 - one flat case table, deliberately explicit
    *,
    kind: str,
    now: datetime,
    subscription_id: str,
    lapsed_status: str,
    live_submission_state: str,
    dead_submission_state: str,
    past_offset: timedelta,
    future_offset: timedelta,
) -> AdmissionCase:
    """Assemble the row store for one case kind at one instant.

    Written as a flat table rather than as composable mutations: each kind is a state a real
    Subscription can be in, and reading them side by side is how a reviewer checks that all six
    reasons and both sides of the expiry boundary are present.
    """
    expected = EXPECTED_REASON_FOR_KIND[kind]
    versions = _version_rows()

    if kind == "caller_is_the_owner":
        rows = [_listing_row(author_id=CALLER_ID)]
    elif kind == "active_and_unexpired":
        rows = [
            _listing_row(
                submission_states=(live_submission_state,),
                subscription=_subscription(
                    subscription_id=subscription_id,
                    status="active",
                    period_expiry=now + future_offset,
                ),
            )
        ]
    elif kind == "active_expiring_one_microsecond_from_now":
        rows = [
            _listing_row(
                subscription=_subscription(
                    subscription_id=subscription_id,
                    status="active",
                    period_expiry=now + ONE_MICROSECOND_BEFORE_EXPIRY,
                ),
            )
        ]
    elif kind == "no_subscription_row":
        rows = [_listing_row()]
    elif kind == "active_expiring_exactly_now":
        rows = [
            _listing_row(
                subscription=_subscription(
                    subscription_id=subscription_id,
                    status="active",
                    period_expiry=now + AT_THE_EXPIRY_INSTANT,
                ),
            )
        ]
    elif kind == "active_status_with_a_past_expiry":
        rows = [
            _listing_row(
                subscription=_subscription(
                    subscription_id=subscription_id,
                    status="active",
                    period_expiry=now - past_offset,
                ),
            )
        ]
    elif kind == "active_status_with_a_cleared_expiry":
        rows = [
            _listing_row(
                subscription=_subscription(
                    subscription_id=subscription_id,
                    status="active",
                    period_expiry=None,
                ),
            )
        ]
    elif kind == "lapsed_status":
        rows = [
            _listing_row(
                subscription=_subscription(
                    subscription_id=subscription_id,
                    status=lapsed_status,
                    period_expiry=now + future_offset,
                ),
            )
        ]
    elif kind == "suspended_subscription":
        rows = [
            _listing_row(
                subscription=_subscription(
                    subscription_id=subscription_id,
                    status="suspended",
                    period_expiry=now + future_offset,
                ),
            )
        ]
    elif kind == "listing_row_absent":
        rows = []
    elif kind == "submission_not_live":
        rows = [
            _listing_row(
                submission_states=(dead_submission_state,),
                subscription=_subscription(
                    subscription_id=subscription_id,
                    status="active",
                    period_expiry=now + future_offset,
                ),
            )
        ]
    elif kind == "no_submission_at_all":
        rows = [
            _listing_row(
                submission_states=(),
                subscription=_subscription(
                    subscription_id=subscription_id,
                    status="active",
                    period_expiry=now + future_offset,
                ),
            )
        ]
    elif kind == "owner_version_unresolvable":
        rows = [
            _listing_row(
                subscription=_subscription(
                    subscription_id=subscription_id,
                    status="active",
                    period_expiry=now + future_offset,
                ),
            )
        ]
        versions = []
    else:  # pragma: no cover - guards a typo in EXPECTED_REASON_FOR_KIND
        raise AssertionError(f"unknown admission case kind {kind!r}")

    return AdmissionCase(
        kind=kind,
        expected_reason=expected,
        now=now,
        listing_rows=rows,
        version_rows=versions,
        caller_id=CALLER_ID,
    )


@st.composite
def admission_cases(draw: Any) -> AdmissionCase:
    """Subscriptions × instants: one case kind, decided at one generated UTC instant.

    ``utc_instants()`` carries the month-length and leap-year boundaries in its pool, so the
    expiry-boundary kinds are exercised on 29 February and 31 December as well as on ordinary
    days. The offsets are bounded well inside a century so ``now ± offset`` cannot leave
    ``datetime``'s range for an instant drawn at either end of that pool.
    """
    return build_case(
        kind=draw(st.sampled_from(CASE_KINDS)),
        now=draw(utc_instants(min_year=2020, max_year=2080)),
        subscription_id=draw(identifiers()),
        lapsed_status=draw(st.sampled_from(LAPSED_STATUSES)),
        live_submission_state=draw(st.sampled_from(LIVE_SUBMISSION_STATES)),
        dead_submission_state=draw(st.sampled_from(DEAD_SUBMISSION_STATES)),
        past_offset=draw(
            st.timedeltas(
                min_value=timedelta(microseconds=1), max_value=timedelta(days=3650)
            )
        ),
        future_offset=draw(
            st.timedeltas(min_value=timedelta(seconds=1), max_value=timedelta(days=3650))
        ),
    )


# ══════════════════════════════════════════════════════════════════════════
# Doubles
# ══════════════════════════════════════════════════════════════════════════


class _Response:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    """One link of the supabase-py fluent chain, recording what it was asked to do."""

    def __init__(self, table: str, client: "RowStore") -> None:
        self.table_name = table
        self.client = client
        self.op = "select"
        self.columns: Optional[str] = None
        self.payload: Any = None
        self.filters: List[Tuple[str, Any]] = []

    def select(self, columns: Any) -> "_Query":
        self.columns = columns
        return self

    def insert(self, payload: Any) -> "_Query":
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload: Any) -> "_Query":
        self.op = "update"
        self.payload = payload
        return self

    def eq(self, column: str, value: Any) -> "_Query":
        self.filters.append((column, value))
        return self

    def single(self) -> "_Query":
        return self

    def execute(self) -> _Response:
        return self.client.execute(self)


class RowStore:
    """A scripted-read, recording-write stand-in for the service-role client.

    Recording rather than applying is the point: "a refused deployment writes nothing at all" is
    only an assertion if every write is observable.
    """

    def __init__(
        self,
        *,
        listing_rows: List[Dict[str, Any]],
        version_rows: List[Dict[str, Any]],
    ) -> None:
        self.listing_rows = listing_rows
        self.version_rows = version_rows
        self.calls: List[_Query] = []

    def table(self, name: str) -> _Query:
        return _Query(name, self)

    def execute(self, query: _Query) -> _Response:
        self.calls.append(query)
        if query.op == "insert":
            row = dict(query.payload)
            row.setdefault("id", "f0000000-0000-4000-8000-00000000dep1")
            return _Response([row])
        if query.op == "update":
            return _Response([dict(query.payload)])
        if query.table_name == "library_strategies":
            return _Response([dict(row) for row in self.listing_rows])
        if query.table_name == "strategy_versions":
            return _Response([dict(row) for row in self.version_rows])
        return _Response([])

    def writes(self) -> List[_Query]:
        return [c for c in self.calls if c.op in ("insert", "update")]

    def writes_to(self, table: str) -> List[_Query]:
        return [c for c in self.writes() if c.table_name == table]

    def reads_of(self, table: str) -> List[_Query]:
        return [c for c in self.calls if c.op == "select" and c.table_name == table]


class _Request:
    """The one thing ``deploy_marketplace_strategy`` uses off ``Request``: ``json()``."""

    def __init__(self, body: Any = None) -> None:
        self._body = body

    async def json(self) -> Any:
        return self._body


class _AuditLogger:
    def __init__(self) -> None:
        self.entries: List[Dict[str, Any]] = []

    async def log(self, action: Any, **kwargs: Any) -> None:
        self.entries.append({"action": action, **kwargs})


def _frozen_datetime(instant: datetime) -> type:
    """A ``datetime`` subclass whose ``now`` is ``instant``.

    A subclass rather than a stub object so everything else ``library.py`` reaches for on the
    name — ``fromisoformat``, ``strptime``, arithmetic — keeps working untouched. Only the clock
    is replaced, which is the one thing that must agree between the two halves of P-16.
    """

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return instant if tz is None else instant.astimezone(tz)

    return _FrozenDatetime


class _ResolveSpy:
    """A pass-through wrapper over the real :func:`resolve`, recording each call.

    Pass-through, not a stub: replacing the decision with a canned one would make the property
    assert that the route echoes a mock, which is not what "the deployment admission decision
    equals the resolver's decision" says.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def __call__(
        self, caller: Any, listing_id: Any, supabase: Any, now: datetime
    ) -> Entitlement:
        entitlement = await _REAL_RESOLVE(caller, listing_id, supabase, now)
        self.calls.append(
            {
                "caller": caller,
                "listing_id": listing_id,
                "now": now,
                "entitlement": entitlement,
            }
        )
        return entitlement


#: Captured at import, before any patching, so the spy always delegates to the real resolver.
_REAL_RESOLVE = er.resolve


class DeployOutcome(NamedTuple):
    """What the deployment gate did: the response, or the error it refused with."""

    response: Optional[Dict[str, Any]]
    error: Optional[MarketplaceError]
    store: RowStore
    spy: _ResolveSpy
    audit: _AuditLogger
    permission_gate_calls: List[Any]


def run_deployment_gate(case: AdmissionCase) -> DeployOutcome:
    """Drive ``deploy_marketplace_strategy`` for ``case``, at ``case.now`` exactly.

    ``mock.patch.object`` context managers rather than a ``monkeypatch`` fixture: Hypothesis's
    ``function_scoped_fixture`` health check exists because a function-scoped fixture is set up
    once for a whole ``@given`` run, not once per example. These are entered and exited inside
    the example.

    ``limiter.enabled`` is among them. ``deploy_marketplace_strategy`` carries
    ``@limiter.limit("10/60second", key_func=caller_or_address)`` (task 33.2), and ``slowapi``'s
    wrapper rejects anything that is not a real ``starlette.requests.Request`` before the handler
    body runs. Calling the handler as a function is what lets both halves of P-16 read one row
    store at one frozen instant, so the decorator is stood down for the call — and it must be
    stood down rather than satisfied, because a hundred examples under one caller key would
    exhaust 10/60s and make later examples answer 429 instead of answering the property. The
    limit itself is asserted from the limiter's registry by
    ``tests/test_task_33_2_rate_limit_keys.py``, so nothing here weakens it.
    """
    store = RowStore(
        listing_rows=[dict(row) for row in case.listing_rows],
        version_rows=[dict(row) for row in case.version_rows],
    )
    spy = _ResolveSpy()
    audit = _AuditLogger()
    permission_gate_calls: List[Any] = []

    def _record_permission_gate(*args: Any, **kwargs: Any) -> Any:
        permission_gate_calls.append((args, kwargs))
        return {"has_permission": True}

    with mock.patch.object(lib, "_build_service_client", lambda: store), mock.patch.object(
        lib, "get_strategy_audit_logger", lambda: audit
    ), mock.patch.object(lib, "datetime", _frozen_datetime(case.now)), mock.patch.object(
        lib, "check_deployment_permission", _record_permission_gate
    ), mock.patch.object(
        er, "resolve", spy
    ), mock.patch.object(
        _limiter, "enabled", False
    ):
        try:
            response = asyncio.run(
                lib.deploy_marketplace_strategy(
                    _Request({}),
                    LISTING_ID,
                    user={"id": case.caller_id},
                )
            )
            error: Optional[MarketplaceError] = None
        except MarketplaceError as exc:
            response, error = None, exc

    return DeployOutcome(
        response=response,
        error=error,
        store=store,
        spy=spy,
        audit=audit,
        permission_gate_calls=permission_gate_calls,
    )


def resolve_directly(case: AdmissionCase) -> Entitlement:
    """The resolver's own decision for ``case``, taken at ``case.now``."""
    store = RowStore(
        listing_rows=[dict(row) for row in case.listing_rows],
        version_rows=[dict(row) for row in case.version_rows],
    )
    return asyncio.run(
        _REAL_RESOLVE({"id": case.caller_id}, LISTING_ID, store, case.now)
    )


# ══════════════════════════════════════════════════════════════════════════
# The property
# ══════════════════════════════════════════════════════════════════════════


@PROPERTY_SETTINGS
@given(case=admission_cases())
def test_p16_admission_agrees_with_the_resolver(case: AdmissionCase) -> None:
    """P-16: the deployment admission decision equals ``entitlement_resolver.resolve``'s
    decision — as a whole value, for the same Subscription, at the same instant — and the
    deployment outcome follows that decision and nothing else.

    The Paper_Session half of the agreement is registered below as a strict xfail owned by
    task 27.1, which is the task that creates ``paper_session_service.start_session``.

    **Validates: Requirements 11.10, 11.16, 7.10**
    """
    # ── The resolver's decision, taken directly at the generated instant ────
    resolved = resolve_directly(case)
    assert resolved.reason is case.expected_reason, (
        f"case {case.kind!r} was built to produce {case.expected_reason.value} but the resolver "
        f"answered {resolved.reason.value}; the case pool no longer means what it says"
    )

    # ── The deployment gate, at the SAME instant ────────────────────────────
    outcome = run_deployment_gate(case)

    # 1. One decision, taken once, on the same inputs.
    assert len(outcome.spy.calls) == 1, (
        f"the deployment path called the Entitlement_Resolver {len(outcome.spy.calls)} times; "
        "P-16 is about ONE admission decision"
    )
    call = outcome.spy.calls[0]
    assert call["caller"] == {"id": case.caller_id}
    assert call["listing_id"] == LISTING_ID
    assert call["now"] == case.now, (
        "the deployment gate decided at a different instant from the resolver, so the two "
        "decisions are not comparable"
    )

    # 2. The decision itself is EQUAL, not merely similarly-flavoured. `Entitlement` is a frozen
    #    dataclass, so this compares the reason, the entitling flag and every identifier.
    assert call["entitlement"] == resolved, (
        f"the deployment gate's decision {call['entitlement']!r} differs from the resolver's "
        f"{resolved!r} for the same Subscription at the same instant"
    )

    # 3. No second admission check. A second answer is something to disagree with.
    assert outcome.permission_gate_calls == [], (
        "deploy_marketplace_strategy consulted check_deployment_permission as well as the "
        "Entitlement_Resolver; there must be exactly one admission decision"
    )

    # 4. The outcome follows the decision.
    if resolved.entitling:
        assert outcome.error is None, (
            f"an entitling decision ({resolved.reason.value}) was refused with "
            f"{outcome.error.code if outcome.error else None}"
        )
        assert outcome.response is not None
        assert outcome.response["granted_via"] == resolved.reason.value
        assert len(outcome.store.writes_to("strategy_deployments")) == 1, (
            "an entitling decision must create exactly one strategy_deployments row"
        )
        assert outcome.store.writes_to("strategies") == [], (
            "no `strategies` row is created for a marketplace deployment (Requirement 7.1)"
        )
    else:
        assert outcome.error is not None, (
            f"a non-entitling decision ({resolved.reason.value}) was admitted: "
            f"{outcome.response!r}"
        )
        assert outcome.error.code == resolved.wire_code, (
            f"the deployment refusal code {outcome.error.code} is not the resolver's wire code "
            f"{resolved.wire_code} for reason {resolved.reason.value}"
        )
        # Requirement 7.10: "not subscribed" and "expired" are distinct codes at 403, and an
        # unavailable Listing is 409. The status comes from the shared catalogue, so this is the
        # published status for the resolver's own code rather than a second mapping.
        assert outcome.error.http_status == HTTP_STATUS_FOR_CODE[resolved.wire_code]
        assert outcome.store.writes() == [], (
            "a refused deployment wrote to "
            f"{sorted({w.table_name for w in outcome.store.writes()})}; Requirement 11.10 "
            "refuses the deployment, leaving nothing behind"
        )


# ══════════════════════════════════════════════════════════════════════════
# Non-vacuity: the pool spans every reason, and admits as well as refuses
# ══════════════════════════════════════════════════════════════════════════


def test_every_admission_case_kind_is_covered_and_the_pool_admits_and_refuses() -> None:
    """Every case kind produces its declared reason, all six reasons appear, and the pool is not
    all-refusals.

    Deterministic and independent of what Hypothesis draws: it enumerates the pool itself. If a
    later edit collapsed several kinds onto ``NOT_SUBSCRIBED``, or removed the entitling kinds,
    P-16 would still pass while asserting almost nothing — and this fails instead.
    """
    now = datetime(2026, 2, 28, 12, 30, 45, 123456, tzinfo=timezone.utc)
    observed: Dict[str, EntitlementReason] = {}

    for kind in CASE_KINDS:
        case = build_case(
            kind=kind,
            now=now,
            subscription_id="11110000-0000-4000-8000-00000000f006",
            lapsed_status="cancelled",
            live_submission_state="SUSPENDED",
            dead_submission_state="REJECTED",
            past_offset=timedelta(days=1),
            future_offset=timedelta(days=30),
        )
        resolved = resolve_directly(case)
        assert resolved.reason is case.expected_reason, (
            f"case {kind!r} declares {case.expected_reason.value} but resolves to "
            f"{resolved.reason.value}"
        )
        observed[kind] = resolved.reason

    reasons = set(observed.values())
    assert reasons == set(EntitlementReason), (
        "the case pool does not span every resolver reason; missing "
        f"{sorted(r.value for r in set(EntitlementReason) - reasons)}"
    )

    entitling = {k for k, r in observed.items() if r in er.ENTITLING_REASONS}
    refusing = set(observed) - entitling
    assert entitling and refusing, (
        "P-16 would be vacuous: the pool must contain both admitted and refused cases, and has "
        f"{len(entitling)} admitted / {len(refusing)} refused"
    )

    # Both sides of the expiry boundary, by name, so removing one is a failure rather than a
    # silent loss of the case Requirement 11.7 is about.
    assert observed["active_expiring_one_microsecond_from_now"] is EntitlementReason.SUBSCRIBED
    assert observed["active_expiring_exactly_now"] is EntitlementReason.EXPIRED
    assert observed["active_status_with_a_past_expiry"] is EntitlementReason.EXPIRED
    assert observed["active_status_with_a_cleared_expiry"] is EntitlementReason.EXPIRED

    # And the deployment gate really does ADMIT for an entitling case, deterministically, so
    # P-16's admit branch is exercised even in the impossible event that Hypothesis draws only
    # refusing kinds.
    admitted = run_deployment_gate(
        build_case(
            kind="active_and_unexpired",
            now=now,
            subscription_id="11110000-0000-4000-8000-00000000f006",
            lapsed_status="cancelled",
            live_submission_state="PUBLISHED",
            dead_submission_state="REJECTED",
            past_offset=timedelta(days=1),
            future_offset=timedelta(days=30),
        )
    )
    assert admitted.error is None, f"an entitling case was refused: {admitted.error!r}"
    assert len(admitted.store.writes_to("strategy_deployments")) == 1


def _called_names(func: Any) -> List[str]:
    """The dotted name of every call in ``func``'s body.

    An AST walk rather than a substring search over the source: both handlers *document* the gate
    they replaced ("replaces the old ``check_deployment_permission``-only gate", "no re-reading of
    ``library_subscriptions.status``"), and a prose mention of a gate is the opposite of a call to
    it. Matching text would fail on the comment that says the call is gone.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    names: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        parts: List[str] = []
        target: Any = node.func
        while isinstance(target, ast.Attribute):
            parts.append(target.attr)
            target = target.value
        if isinstance(target, ast.Name):
            parts.append(target.id)
        if parts:
            names.append(".".join(reversed(parts)))
    return names


def _tables_addressed(func: Any) -> List[str]:
    """Every string literal ``func`` passes to ``.table(...)``."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    tables: List[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "table"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            tables.append(node.args[0].value)
    return tables


def test_resolve_is_the_only_admission_decision_on_the_deployment_path() -> None:
    """The structural half of "there is only one decision" (Requirement 11.10).

    The property above asserts the resolver is *called* once per request and that its verdict is
    the one acted on. This asserts ``deploy_marketplace_strategy`` contains no second gate at
    all — no ``check_deployment_permission`` call, and no read of ``library_subscriptions``, whose
    stored ``status`` label is exactly what the old gate trusted. A second gate that happened not
    to fire for any generated case would pass the property and fail here.
    """
    calls = _called_names(lib.deploy_marketplace_strategy)

    resolver_calls = [name for name in calls if name.endswith("entitlement_resolver.resolve")]
    assert len(resolver_calls) == 1, (
        f"deploy_marketplace_strategy calls the Entitlement_Resolver {len(resolver_calls)} "
        "times; there must be exactly one admission decision"
    )
    assert not [name for name in calls if name.endswith("check_deployment_permission")], (
        "deploy_marketplace_strategy still calls check_deployment_permission, the gate that "
        "trusted the stored status label and treated a missing expiry as perpetual"
    )
    assert "library_subscriptions" not in _tables_addressed(lib.deploy_marketplace_strategy), (
        "deploy_marketplace_strategy reads library_subscriptions itself; the Subscription's "
        "state must reach it only through the single admission decision"
    )


# ══════════════════════════════════════════════════════════════════════════
# The Paper_Session half — strict xfail, owner: task 27.1
# ══════════════════════════════════════════════════════════════════════════

#: The task that creates ``paper_session_service.start_session``. Named on the xfail so a reader
#: knows whose landing should turn it green — and, because the mark is strict, an XPASS fails
#: this file and says "fold the Paper_Session gate into P-16 now".
PAPER_SESSION_OWNER = "task 27.1"


@pytest.mark.xfail(
    strict=True,
    reason=(
        f"backend_app.backend.paper.paper_session_service does not exist yet, so the "
        f"Paper_Session half of P-16 cannot be exercised; owner: {PAPER_SESSION_OWNER} "
        f"(Requirements 11.10, 17.4)"
    ),
)
def test_the_paper_session_start_path_admits_on_the_same_resolver() -> None:
    """``start_session``'s admission decision is ``entitlement_resolver.resolve``, and only that.

    ``design.md § paper/paper_session_service.py`` writes the pipeline with
    ``ent <- entitlement_resolver.resolve(...)`` as its first step and
    ``IF NOT ent.entitling THEN RAISE PaperStartRefused(ent.reason)`` as its second, which is why
    P-16 holds "because there is only one decision". Until task 27.1 lands there is no second
    gate to agree with, and this states what that task owes rather than skipping the half.

    When it lands: assert the structural claims here, then extend
    :func:`test_p16_admission_agrees_with_the_resolver` to drive ``start_session`` for the same
    :class:`AdmissionCase` at the same frozen instant and compare its :class:`Entitlement`
    against ``resolve_directly(case)`` exactly as the deployment half is compared.
    """
    module = importlib.import_module("backend_app.backend.paper.paper_session_service")

    start_session = getattr(module, "start_session", None)
    assert start_session is not None, (
        "paper_session_service has no start_session, so there is no Paper_Session admission "
        "decision to agree with"
    )

    calls = _called_names(start_session)
    assert [name for name in calls if name.endswith("entitlement_resolver.resolve")], (
        "start_session does not call entitlement_resolver.resolve, so the Paper_Session gate is "
        "a second admission decision that can disagree with deployment's"
    )
    assert not [name for name in calls if name.endswith("check_deployment_permission")], (
        "start_session calls check_deployment_permission as well as the Entitlement_Resolver"
    )
    assert "library_subscriptions" not in _tables_addressed(start_session), (
        "start_session reads library_subscriptions itself rather than taking the Subscription's "
        "state from the single admission decision"
    )
