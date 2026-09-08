"""Property test for the sweep-independent expiry boundary.

Feature: marketplace-subscriptions-paper-trading
Task 19.12 — **Validates: Requirements 11.7**
Design reference: ``design.md § Property-to-test mapping`` ->
``P-11 | tests/property/test_entitlement_expiry_boundary.py | (expiry, instant) pairs incl.
instant == expiry exactly, and sweep-run flag | instant < expiry``; and
``design.md § marketplace/entitlement_resolver.py and subscriber execution`` -> "The expiry sweep
is deliberately *not* on this path. ``entitlement_resolver`` compares ``now()`` with
``period_expiry`` on every call, so a Subscription stops entitling at its expiry instant whether
or not the sweep has run".

Property living here
--------------------
``test_p11_entitlement_is_decided_by_the_expiry_instant``. One property, one test function, so
the scoreboard in ``tests/property/test_property_coverage.py`` reads exactly one ``test_p11_``
name. Everything else in this module is a deterministic companion that keeps that property from
going vacuous, or pins one named case by hand.

WHAT P-11 CLAIMS, IN THE REQUIREMENT'S OWN WORDS
------------------------------------------------
Requirement 11.7: *while the current UTC time is at or after a Subscription's expiry and no
renewal payment has been confirmed, the Entitlement_Resolver treats that Subscription as not
entitling, irrespective of the stored Subscription_State value and irrespective of whether the
expiry sweep has yet run for that Subscription.*

Two halves, and they are asserted separately because they can fail separately:

**The boundary half.** For expiry ``e`` and evaluation instant ``t``, the decision is
``t < e``. The comparison in the resolver is ``now >= period_expiry -> non-entitling``, so
``t == e`` **exactly** is NOT entitling. That single microsecond is the whole content of the
half: an implementation that used ``now > period_expiry`` would pass every test that only ever
looked a second either side of the boundary. So ``e`` is generated at ``t - 1µs``, at ``t``
exactly and at ``t + 1µs`` as named draws, alongside a broad ±10-year band, and
:func:`test_the_microsecond_triple_around_the_expiry_instant_is_decided_as_the_requirement_states`
pins the same three points by hand, varying ``t`` and holding ``e``, so the case survives even if
the generator is later re-parameterised. A null ``period_expiry`` is non-entitling too, whatever
the ``status`` label reads — a cleared expiry is not a perpetual entitlement.

**The sweep-independence half — this is the metamorphic part.** The same ``(subscription, t)``
pair must yield the same decision whether the row's ``status`` still reads ``'active'`` because
the sweep has not run, or has already been moved to ``'expired'`` by
``backend_app/backend/marketplace/expiry_sweep.py``. The sweep is housekeeping, not the
authority. So each example is decided **twice, in both orderings**, against two independent but
identically-built row stores:

    store A:  resolve  ->  sweep  ->  resolve          (the sweep is dead when we ask)
    store B:  sweep    ->  resolve                     (the sweep got there first)

and all three :class:`Entitlement` values must be **equal as whole values** — reason, entitling
flag and every identifier — not merely "all three refused". That equality is the metamorphic
relation: relabelling ``status`` is an operation that must not move the decision.

WHY THE METAMORPHIC HALF IS NOT VACUOUS
---------------------------------------
"Running the sweep changed nothing" would be trivially true if the sweep changed no row. So every
example asserts what the sweep actually did to the stored label, from the requirement's side:
when the row is expirable (``status`` in ``active``/``suspended``, non-null ``period_expiry``,
``period_expiry <= t``) the label **must** have moved to ``'expired'``, and otherwise it must be
untouched. An example where the sweep is a no-op cannot masquerade as evidence of
sweep-independence, and a sweep that stopped working would fail this file rather than quietly
make it pass.

WHY THE REASON IS ASSERTED, NOT ONLY THE BOOLEAN
------------------------------------------------
A denial for the wrong cause is a bug the boolean cannot see. Requirement 7.10 makes
``NOT_SUBSCRIBED`` and ``EXPIRED`` distinct answers — a caller who never subscribed and a
subscriber whose period lapsed are told different things, and the two must not collapse onto one
code. So every assertion is on :class:`EntitlementReason` and on the catalogue
:attr:`Entitlement.wire_code` behind it, with the shared ``HTTP_STATUS_FOR_CODE`` giving the
status. The expected reason is computed by :func:`expected_reason`, a **restatement of the
requirement ladder** written independently of the resolver's control flow, so the property is
not merely echoing the implementation back at itself.

The neighbouring policy cases Requirement 4.11 fixes ride along in the same generator, because
they are exactly the ones an over-eager expiry check would break:

* a ``SUSPENDED`` or ``UNPUBLISHED`` **Listing** still entitles an ``ACTIVE`` Subscription until
  its expiry — the seller hiding the Listing does not revoke access already paid for;
* an unresolvable ``source_strategy_id`` is ``LISTING_UNAVAILABLE`` -> 409 (Requirement 7.11),
  and it is reached only on the *entitling* side of the boundary: past the expiry the answer is
  ``EXPIRED``, because the subscription ended before the artifact was ever looked for.

Note what this module deliberately does **not** quantify over: a *suspended Subscription*
(``status = 'suspended'``) is included in the pool because the sweep expires it too, but the
metamorphic claim for it is only that the decision stays **non-entitling** across the sweep. Its
reason legitimately moves ``SUBSCRIPTION_SUSPENDED -> EXPIRED`` once the sweep relabels the row,
which is the state machine's business (P-8, P-13), not P-11's. Requirement 11.7 is about
entitlement, and entitlement is invariant there; claiming reason-invariance for it would be
asserting something the specification does not say.

WHAT IS REAL AND WHAT IS A DOUBLE
---------------------------------
Real: ``entitlement_resolver.resolve``, ``expiry_sweep.sweep``, the reason enum, the wire-code
mapping and the shared error catalogue's statuses. Both entry points already take ``now`` as an
argument and the ``supabase`` handle by injection, which is what makes the boundary a pure,
pinnable comparison — no clock is patched anywhere in this file.

Doubles: :class:`SubscriptionStore`, a **mutating** stand-in for the service-role client, and a
recording audit writer. Mutating is the point: the sweep's ``UPDATE`` has to be genuinely visible
to the resolver's next read, or the metamorphic half would be comparing a relabel that never
happened. The store is local to this module rather than borrowed from
``tests/test_settlement_service.py``'s ``FakeSupabase``: that double serves ``eq`` filters over
flat tables, and this property needs the resolver's *embedded* ``library_strategies`` read and
the sweep's ``not_.is_`` / ``lte`` / ``in_`` predicate over one shared, mutable subscription list.
``_run_coroutine`` **is** taken from there, so this file introduces no second loop runner.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.marketplace import expiry_sweep as sweep_mod
from backend_app.backend.marketplace.entitlement_resolver import (
    ENTITLING_REASONS,
    WIRE_CODE_FOR_REASON,
    Entitlement,
    EntitlementReason,
    resolve,
)
from backend_app.backend.marketplace.errors import HTTP_STATUS_FOR_CODE
from tests.strategies.marketplace_generators import identifiers, utc_instants
from tests.test_settlement_service import _run_coroutine

#: The configuration ``design.md § Property-based testing configuration`` prescribes, matching
#: ``tests/property/test_settlement_ledger.py``: at least 100 examples, no per-example deadline.
#:
#: ``deadline=None`` because every example runs three ``async`` resolves and two ``async`` sweeps
#: on freshly created event loops; loop setup jitter on a loaded machine is wider than the work
#: being measured, and a millisecond budget would turn that jitter into a flaky failure that says
#: nothing about the boundary. Run time is bounded by ``max_examples`` and by the fact that an
#: example touches at most one listing row and one subscription row, which keeps the module to a
#: few seconds — far inside the two-minute budget.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# ══════════════════════════════════════════════════════════════════════════
# Identities and identifiers
# ══════════════════════════════════════════════════════════════════════════
#
# Fixed, not generated. P-11 is quantified over ``(expiry, instant)`` pairs and the sweep-run
# flag (design.md's generator column); the caller is one non-owner subscriber, because an owner
# is entitled with no subscription at all and so has no expiry boundary to cross.
# `tests/property/test_tenant_isolation_matrix.py` owns identity quantification.

OWNER_ID = "aa000000-0000-4000-8000-0000000011a7"
CALLER_ID = "bb000000-0000-4000-8000-0000000011a8"
LISTING_ID = "cc000000-0000-4000-8000-0000000011a9"
SOURCE_STRATEGY_ID = "dd000000-0000-4000-8000-0000000011aa"
VERSION_ID = "ee000000-0000-4000-8000-0000000011ab"

#: The resolver's exact projection of the embedded ``library_subscriptions`` collection
#: (``entitlement_resolver._ENTITLEMENT_SELECT``). The double serves these four columns and no
#: others, so a decision that came to depend on some further column would fail here rather than
#: read a value PostgREST would never have returned.
SUBSCRIPTION_EMBED_COLUMNS: Tuple[str, ...] = ("id", "user_id", "status", "period_expiry")

#: Submission states that still entitle an ACTIVE, unexpired Subscription (Requirement 4.11).
LIVE_SUBMISSION_STATES: Tuple[str, ...] = ("PUBLISHED", "SUSPENDED", "UNPUBLISHED")

#: The persisted ``library_subscriptions.status`` spellings this module drives. ``'active'`` is
#: the case Requirement 11.7 is about; ``'suspended'`` is included because the sweep expires it
#: too, so it exercises the sweep's own predicate (see the module docstring for why its *reason*
#: is not claimed invariant).
DRIVEN_STATUSES: Tuple[str, ...] = ("active", "suspended")

#: The three instants that decide the boundary, as offsets applied to ``expiry = t + offset``.
#: ``ONE_MICROSECOND`` is the finest distinction ``datetime`` can express, which is why the
#: boundary is stated at that resolution rather than at a second.
ONE_MICROSECOND = timedelta(microseconds=1)
EXPIRY_AT_THE_EVALUATION_INSTANT = timedelta(0)
EXPIRY_ONE_MICROSECOND_AFTER = ONE_MICROSECOND
EXPIRY_ONE_MICROSECOND_BEFORE = -ONE_MICROSECOND


# ══════════════════════════════════════════════════════════════════════════
# The row store: a MUTATING stand-in for the service-role client
# ══════════════════════════════════════════════════════════════════════════


class _Response:
    """A PostgREST-shaped response. Rows on ``.data``, the convention this codebase uses."""

    def __init__(self, data: Any) -> None:
        self.data = data


class _NotFilter:
    """The ``.not_`` link of the fluent chain, so ``not_.is_(col, "null")`` parses."""

    def __init__(self, query: "_Query") -> None:
        self._query = query

    def is_(self, column: str, value: Any) -> "_Query":
        self._query.filters.append(("not_is", column, value))
        return self._query


class _Query:
    """One statement of the supabase-py fluent chain, recorded rather than executed blind."""

    def __init__(self, table: str, store: "SubscriptionStore") -> None:
        self.table_name = table
        self.store = store
        self.op = "select"
        self.columns: Optional[str] = None
        self.payload: Optional[Dict[str, Any]] = None
        self.filters: List[Tuple[str, str, Any]] = []

    @property
    def not_(self) -> _NotFilter:
        return _NotFilter(self)

    def select(self, columns: Any) -> "_Query":
        self.op = "select"
        self.columns = columns
        return self

    def update(self, payload: Dict[str, Any]) -> "_Query":
        self.op = "update"
        self.payload = dict(payload)
        return self

    def insert(self, payload: Dict[str, Any]) -> "_Query":
        self.op = "insert"
        self.payload = dict(payload)
        return self

    def eq(self, column: str, value: Any) -> "_Query":
        self.filters.append(("eq", column, value))
        return self

    def lte(self, column: str, value: Any) -> "_Query":
        self.filters.append(("lte", column, value))
        return self

    def in_(self, column: str, values: Sequence[Any]) -> "_Query":
        self.filters.append(("in", column, list(values)))
        return self

    def single(self) -> "_Query":
        return self

    def execute(self) -> _Response:
        return self.store.execute(self)


def _instant(value: Any) -> Optional[datetime]:
    """A stored timestamp as a tz-aware UTC ``datetime``, or ``None``.

    The store keeps ``period_expiry`` as the ISO-8601 text a JSON driver returns, so the
    comparison the double performs is over parsed instants rather than over strings — string
    ordering would silently disagree with the database at exactly the boundary this file is
    about (``+00:00`` versus ``Z``, differing microsecond widths).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _matches(row: Dict[str, Any], filters: Sequence[Tuple[str, str, Any]]) -> bool:
    """Whether ``row`` satisfies every filter, the way PostgREST would evaluate them."""
    for kind, column, value in filters:
        actual = row.get(column)
        if kind == "eq":
            if actual is None or str(actual) != str(value):
                return False
        elif kind == "in":
            if actual is None or str(actual) not in {str(v) for v in value}:
                return False
        elif kind == "not_is":
            # The sweep's ``not_.is_("period_expiry", "null")``. Nothing else is expected, and an
            # unexpected spelling is reported rather than silently treated as "matches".
            assert str(value).lower() == "null", (
                f"the double only models not_.is_(col, 'null'); got {value!r}"
            )
            if actual is None:
                return False
        elif kind == "lte":
            left, right = _instant(actual), _instant(value)
            # ``NULL <= x`` is unknown in SQL, so a null-expiry row is not selected. Written
            # explicitly because that is exactly the row the resolver must also refuse.
            if left is None or right is None or not left <= right:
                return False
        else:  # pragma: no cover - guards a typo in the chain above
            raise AssertionError(f"unmodelled filter kind {kind!r}")
    return True


class SubscriptionStore:
    """The Persistence_Layer double: one listing, one mutable subscription list, versions.

    Mutating, not scripted: the sweep's ``UPDATE`` on ``library_subscriptions`` is applied in
    place, so the resolver's *next* embedded read sees the relabelled ``status``. Without that,
    the sweep-independence half of P-11 would be comparing a relabel that never happened.

    The ``library_strategies`` read is served as PostgREST serves it — the flat listing columns
    plus the two embedded collections, with ``library_subscriptions`` projected to
    :data:`SUBSCRIPTION_EMBED_COLUMNS` and **rebuilt from the live subscription list on every
    read**. Rows are returned carrying their ``user_id`` (rather than pre-filtered to the
    caller), which additionally exercises the resolver's own in-memory re-scoping: a foreign
    subscription must not entitle this caller.
    """

    def __init__(
        self,
        *,
        listing_row: Optional[Dict[str, Any]],
        subscription_rows: Sequence[Dict[str, Any]],
        version_rows: Sequence[Dict[str, Any]],
    ) -> None:
        self.listing_row = None if listing_row is None else dict(listing_row)
        self.subscriptions: List[Dict[str, Any]] = [dict(r) for r in subscription_rows]
        self.version_rows: List[Dict[str, Any]] = [dict(r) for r in version_rows]
        self.statements: List[_Query] = []
        self.transitions: List[Dict[str, Any]] = []

    # ---- the supabase-py surface both modules under test use -------------
    def table(self, name: str) -> _Query:
        return _Query(name, self)

    def execute(self, query: _Query) -> _Response:
        self.statements.append(query)
        table, op = query.table_name, query.op

        if table == "library_strategies" and op == "select":
            if self.listing_row is None:
                return _Response([])
            return _Response([self._embedded_listing()])

        if table == "strategy_versions" and op == "select":
            return _Response(copy.deepcopy(self.version_rows))

        if table == sweep_mod.SUBSCRIPTIONS_TABLE:
            matched = [r for r in self.subscriptions if _matches(r, query.filters)]
            if op == "select":
                return _Response(copy.deepcopy(matched))
            if op == "update":
                for row in matched:
                    row.update(query.payload or {})
                # ``Prefer: return=representation`` — the row image AFTER the update.
                return _Response(copy.deepcopy(matched))

        if table == sweep_mod.TRANSITIONS_TABLE and op == "insert":
            self.transitions.append(dict(query.payload or {}))
            return _Response([dict(query.payload or {})])

        # strategy_deployments / paper_sessions: Requirement 11.15's enforcement. Nothing is
        # running in these examples, so the correct answer is "no rows matched".
        return _Response([])

    # ---- helpers --------------------------------------------------------
    def _embedded_listing(self) -> Dict[str, Any]:
        row = dict(self.listing_row or {})
        row["library_subscriptions"] = [
            {column: sub.get(column) for column in SUBSCRIPTION_EMBED_COLUMNS}
            for sub in self.subscriptions
        ]
        return copy.deepcopy(row)

    def status_of(self, subscription_id: str) -> Optional[str]:
        """The stored ``status`` label of one subscription row, as it stands right now."""
        for row in self.subscriptions:
            if str(row.get("id")) == str(subscription_id):
                return row.get("status")
        return None

    def expiry_of(self, subscription_id: str) -> Any:
        """The stored ``period_expiry`` of one subscription row, as it stands right now."""
        for row in self.subscriptions:
            if str(row.get("id")) == str(subscription_id):
                return row.get("period_expiry")
        return None


class RecordingAuditWriter:
    """The sweep's injected audit writer. Records; never fails."""

    def __init__(self) -> None:
        self.entries: List[Dict[str, Any]] = []

    async def __call__(self, action: Any, **kwargs: Any) -> None:
        self.entries.append({"action": action, **kwargs})


# ══════════════════════════════════════════════════════════════════════════
# The case: one (expiry, instant) pair, one listing shape
# ══════════════════════════════════════════════════════════════════════════


class BoundaryCase(NamedTuple):
    """One Subscription, one evaluation instant, and the row shape around them."""

    #: Which listing/subscription shape this is. Names the counterexample.
    shape: str
    #: The evaluation instant ``t`` handed to the resolver and to the sweep.
    now: datetime
    #: The Subscription's ``period_expiry`` ``e``, or ``None`` for a cleared period.
    expiry: Optional[datetime]
    #: The persisted ``library_subscriptions.status`` label.
    status: str
    #: The Listing's ``marketplace_submissions.submission_state``.
    submission_state: str
    #: Whether ``source_strategy_id`` resolves to a live Strategy_Version (Requirement 7.11).
    version_resolves: bool
    subscription_id: str

    @property
    def has_subscription(self) -> bool:
        return self.shape != "no_subscription_row"

    @property
    def sweep_should_relabel(self) -> bool:
        """Whether the sweep's own predicate selects this row at :attr:`now`.

        ``period_expiry IS NOT NULL AND period_expiry <= now AND status IN
        ('active','suspended')`` — Requirement 11.8's predicate, written here from the
        requirement rather than read off the sweep, so "the sweep actually did something" is an
        independent claim.
        """
        return (
            self.has_subscription
            and self.expiry is not None
            and self.expiry <= self.now
            and self.status in sweep_mod.EXPIRABLE_STATUS_TEXTS
        )


#: The shapes the generator draws. Each is a real state a Subscription/Listing pair can be in,
#: and each is chosen because an expiry check that was wrong in a *different* way would show up
#: in it: a listing the seller hid, a listing whose artifact is gone, a caller who never bought,
#: a period that was cleared instead of set.
SHAPES: Tuple[str, ...] = (
    "live_listing",
    "hidden_listing",
    "unresolvable_source_strategy",
    "no_subscription_row",
    "cleared_period",
)


def expected_reason(case: BoundaryCase) -> EntitlementReason:
    """The reason Requirement 11.7 (with 7.10, 7.11 and 4.11) demands for ``case``.

    Written as the requirement's ladder, deliberately **not** as a mirror of the resolver's
    branch order, so the property compares two independent readings of the specification:

    1. no Subscription row at all is ``NOT_SUBSCRIBED`` — distinct from ``EXPIRED``, whatever
       the instant (Requirement 7.10);
    2. a suspended Subscription is held, and that answer survives until the sweep relabels it;
    3. a cleared (null) ``period_expiry`` has no period, so it does not entitle (Requirement
       11.16's leftover shape) — the ``status`` label does not matter;
    4. ``t >= e`` is not entitling, and it **dominates** every listing-side condition below:
       past the expiry the subscription has ended, so nothing about the Listing is even asked;
    5. inside the period, an unresolvable ``source_strategy_id`` is ``LISTING_UNAVAILABLE``
       (Requirement 7.11, 409);
    6. otherwise the Subscription entitles — including when the Listing itself is ``SUSPENDED``
       or ``UNPUBLISHED`` (Requirement 4.11).
    """
    if not case.has_subscription:
        return EntitlementReason.NOT_SUBSCRIBED
    if case.status == "suspended":
        return EntitlementReason.SUBSCRIPTION_SUSPENDED
    if case.expiry is None:
        return EntitlementReason.EXPIRED
    if case.now >= case.expiry:
        return EntitlementReason.EXPIRED
    if not case.version_resolves:
        return EntitlementReason.LISTING_UNAVAILABLE
    return EntitlementReason.SUBSCRIBED


def _subscription_row(
    *, subscription_id: str, status: str, expiry: Optional[datetime]
) -> Dict[str, Any]:
    """The caller's own ``library_subscriptions`` row, as the driver stores it.

    ``period_expiry`` is kept as ISO-8601 **text** with microsecond precision, which is what a
    JSON driver returns and therefore the shape the resolver must parse. Storing a ``datetime``
    here would skip the parse and let a text-handling bug at the boundary go unseen.
    ``library_id`` and ``user_id`` are present because the sweep scopes Requirement 11.15's
    enforcement by that pair.
    """
    return {
        "id": subscription_id,
        "user_id": CALLER_ID,
        "library_id": LISTING_ID,
        "status": status,
        "period_expiry": None if expiry is None else expiry.isoformat(),
    }


def _listing_row(*, submission_state: str) -> Dict[str, Any]:
    """A ``library_strategies`` row with the submission collection the one read embeds."""
    return {
        "id": LISTING_ID,
        "author_id": OWNER_ID,
        "source_strategy_id": SOURCE_STRATEGY_ID,
        "source_cloning_enabled": False,
        "marketplace_submissions": [{"submission_state": submission_state}],
    }


def build_store(case: BoundaryCase) -> SubscriptionStore:
    """A fresh row store for ``case``. Called once per ordering, so the two never share state."""
    return SubscriptionStore(
        listing_row=_listing_row(submission_state=case.submission_state),
        subscription_rows=(
            []
            if not case.has_subscription
            else [
                _subscription_row(
                    subscription_id=case.subscription_id,
                    status=case.status,
                    expiry=case.expiry,
                )
            ]
        ),
        version_rows=(
            [
                {
                    "id": VERSION_ID,
                    "strategy_id": SOURCE_STRATEGY_ID,
                    "version": 3,
                    "is_draft": False,
                }
            ]
            if case.version_resolves
            else []
        ),
    )


def build_case(
    *,
    shape: str,
    now: datetime,
    offset: Optional[timedelta],
    status: str,
    submission_state: str,
    subscription_id: str,
) -> BoundaryCase:
    """Assemble one case. ``offset`` is ``e - t``; ``None`` means a cleared period."""
    expiry = None if offset is None else now + offset
    return BoundaryCase(
        shape=shape,
        now=now,
        expiry=None if shape == "cleared_period" else expiry,
        status=status,
        submission_state=(
            "PUBLISHED" if shape == "live_listing" else submission_state
        ),
        version_resolves=shape != "unresolvable_source_strategy",
        subscription_id=subscription_id,
    )


#: ``e - t``. The three named draws are the boundary itself; the broad band is there so the
#: property is a statement about all instants and not only about the three interesting ones.
#: ±10 years around instants drawn from 2020…2080 stays far inside ``datetime``'s range.
_OFFSETS = st.one_of(
    st.just(EXPIRY_AT_THE_EVALUATION_INSTANT),
    st.just(EXPIRY_ONE_MICROSECOND_AFTER),
    st.just(EXPIRY_ONE_MICROSECOND_BEFORE),
    st.timedeltas(min_value=-timedelta(days=3650), max_value=timedelta(days=3650)),
)


@st.composite
def boundary_cases(draw: Any) -> BoundaryCase:
    """``(expiry, instant)`` pairs across every shape, with the boundary triple weighted in.

    ``utc_instants()`` carries month-length and leap-year boundaries in its pool, so the
    microsecond triple is exercised on 29 February and 31 December as well as on ordinary days —
    the days a period computed by calendar-month arithmetic actually lands on.
    """
    return build_case(
        shape=draw(st.sampled_from(SHAPES)),
        now=draw(utc_instants(min_year=2020, max_year=2080)),
        offset=draw(_OFFSETS),
        status=draw(st.sampled_from(DRIVEN_STATUSES)),
        submission_state=draw(st.sampled_from(LIVE_SUBMISSION_STATES)),
        subscription_id=draw(identifiers()),
    )


# ══════════════════════════════════════════════════════════════════════════
# Driving the two real entry points
# ══════════════════════════════════════════════════════════════════════════


def resolve_at(store: SubscriptionStore, now: datetime) -> Entitlement:
    """The resolver's decision for ``store`` at ``now``.

    The caller is the authenticated server-side identity and nothing else participates: only
    ``{"id": CALLER_ID}`` is passed, and the Listing key is the path value. No identifier
    reaches the decision from a body, query or message (Requirements 7.7, 21.1).
    """
    return _run_coroutine(resolve({"id": CALLER_ID}, LISTING_ID, store, now))


def run_sweep(store: SubscriptionStore, now: datetime) -> Any:
    """One real expiry-sweep pass over ``store`` at ``now``.

    ``stop_running=False``: Requirement 11.15's enforcement is
    ``tests/property/test_expiry_sweep_idempotence.py``'s and task 20's business, and P-11 is
    about the *label* the sweep writes being irrelevant to the decision. The audit writer is
    injected so no Redis-backed logger is reached.
    """
    return _run_coroutine(
        sweep_mod.sweep(
            supabase=store,
            now=now,
            stop_running=False,
            audit_writer=RecordingAuditWriter(),
        )
    )


# ══════════════════════════════════════════════════════════════════════════
# The property
# ══════════════════════════════════════════════════════════════════════════


@PROPERTY_SETTINGS
@given(case=boundary_cases())
def test_p11_entitlement_is_decided_by_the_expiry_instant(case: BoundaryCase) -> None:
    """P-11: the Entitlement_Resolver entitles for ``t < e`` and refuses for ``t >= e`` —
    ``t == e`` exactly included — and the same ``(subscription, t)`` pair yields the same
    decision whether the expiry sweep has run or not.

    **Validates: Requirements 11.7**
    """
    # ══ 1. The boundary half, with the sweep never having run ══════════════
    store_a = build_store(case)
    before = resolve_at(store_a, case.now)

    expected = expected_reason(case)
    assert before.reason is expected, (
        f"shape={case.shape} status={case.status} e-t="
        f"{None if case.expiry is None else case.expiry - case.now}: Requirement 11.7's ladder "
        f"demands {expected.value}, the resolver answered {before.reason.value}"
    )
    assert before.entitling is (expected in ENTITLING_REASONS), (
        f"the entitling flag {before.entitling!r} disagrees with reason {before.reason.value}"
    )

    # The boundary itself, stated as the comparison rather than via the ladder, so a
    # counterexample points straight at the microsecond. Only for the shape where nothing else
    # can refuse: an active Subscription on a live Listing whose artifact resolves.
    if (
        case.shape in ("live_listing", "hidden_listing")
        and case.status == "active"
        and case.expiry is not None
    ):
        assert before.entitling is (case.now < case.expiry), (
            f"the decision at t={case.now.isoformat()} for e={case.expiry.isoformat()} "
            f"(e - t = {case.expiry - case.now}) was entitling={before.entitling!r}; the "
            f"boundary is t < e, so t == e exactly must NOT entitle"
        )

    # A null period_expiry is non-entitling whatever the status label reads.
    if case.has_subscription and case.expiry is None:
        assert not before.entitling, (
            f"a Subscription with a cleared period_expiry and status={case.status!r} was "
            "treated as entitling; a missing expiry is not a perpetual entitlement"
        )

    # The reason's wire code and its published status — a denial for the wrong cause is a bug
    # the boolean cannot see (Requirement 7.10).
    assert before.wire_code == WIRE_CODE_FOR_REASON[before.reason]
    if before.entitling:
        assert before.wire_code is None
        assert before.version_id == VERSION_ID, (
            "an entitling decision must carry the current live Strategy_Version it was resolved "
            "against (Requirement 7.5)"
        )
    else:
        assert before.wire_code in HTTP_STATUS_FOR_CODE, (
            f"{before.wire_code!r} is not in the shared error catalogue"
        )

    # ══ 2. The sweep runs on the SAME store; the decision must not move ════
    run_sweep(store_a, case.now)

    # Non-vacuity: the sweep must genuinely have relabelled an expirable row, or "the sweep
    # changed nothing" is not evidence of anything.
    label_after_sweep = store_a.status_of(case.subscription_id)
    if case.sweep_should_relabel:
        assert label_after_sweep == sweep_mod.EXPIRED_STATUS_TEXT, (
            f"the sweep left status={label_after_sweep!r} on a row it was due to expire "
            f"(e={case.expiry}, t={case.now}); sweep-independence cannot be demonstrated by a "
            "sweep that did nothing"
        )
    elif case.has_subscription:
        assert label_after_sweep == case.status, (
            f"the sweep moved status {case.status!r} -> {label_after_sweep!r} on a row outside "
            f"its predicate (e={case.expiry}, t={case.now})"
        )
    # The sweep may never move the instant the decision is made from (Requirement 11.7).
    if case.has_subscription:
        assert _instant(store_a.expiry_of(case.subscription_id)) == case.expiry, (
            "the sweep changed period_expiry; it is not the authority on when access ends"
        )

    after_sweep_on_a = resolve_at(store_a, case.now)

    # ══ 3. The other ordering: an independent store the sweep reached first ═
    store_b = build_store(case)
    run_sweep(store_b, case.now)
    after = resolve_at(store_b, case.now)

    # The metamorphic relation. `Entitlement` is a frozen dataclass, so for the case Requirement
    # 11.7 names this is value equality over the whole decision — reason, entitling flag and
    # every identifier. For a *suspended* Subscription only the entitling flag is claimed: the
    # sweep legitimately relabels it, moving the reason SUBSCRIPTION_SUSPENDED -> EXPIRED, which
    # is the state machine's business (P-8, P-13) and not P-11's.
    assert before.entitling is after.entitling is after_sweep_on_a.entitling, (
        f"the decision depends on whether the sweep has run: before={before.entitling!r}, "
        f"after(same store)={after_sweep_on_a.entitling!r}, after(sweep first)="
        f"{after.entitling!r} for e={case.expiry}, t={case.now}"
    )
    if case.status != "suspended":
        assert before == after, (
            f"the decision differs by sweep ordering: {before!r} with the sweep dead versus "
            f"{after!r} with the sweep having run first, for the same Subscription at the same "
            "instant"
        )
        assert before == after_sweep_on_a, (
            f"running the sweep changed the decision on the same store: {before!r} -> "
            f"{after_sweep_on_a!r}"
        )


# ══════════════════════════════════════════════════════════════════════════
# Deterministic companions: the named cases, pinned by hand
# ══════════════════════════════════════════════════════════════════════════

#: A fixed expiry with a non-zero microsecond field, so ``e ± 1µs`` cannot be confused with a
#: rounding artefact at a whole second.
FIXED_EXPIRY = datetime(2026, 2, 28, 12, 30, 45, 123456, tzinfo=timezone.utc)


def _decide(
    *,
    now: datetime,
    expiry: Optional[datetime],
    status: str = "active",
    submission_state: str = "PUBLISHED",
    version_resolves: bool = True,
    with_subscription: bool = True,
    sweep_first: bool = False,
) -> Entitlement:
    """Resolve one hand-built case, optionally letting the sweep run first."""
    case = BoundaryCase(
        shape="live_listing" if with_subscription else "no_subscription_row",
        now=now,
        expiry=expiry,
        status=status,
        submission_state=submission_state,
        version_resolves=version_resolves,
        subscription_id="11110000-0000-4000-8000-0000000011ac",
    )
    store = build_store(case)
    if sweep_first:
        run_sweep(store, now)
    return resolve_at(store, now)


def test_the_microsecond_triple_around_the_expiry_instant_is_decided_as_the_requirement_states() -> None:
    """``t = e - 1µs`` entitles, ``t = e`` does not, ``t = e + 1µs`` does not.

    The property above varies ``e`` and holds ``t``; this varies ``t`` and holds ``e``, which is
    how Requirement 11.7 is worded ("while the current UTC time is at or after ... expiry"). Both
    framings must agree, and pinning it by hand means the case survives a later
    re-parameterisation of the generator.

    Validates: Requirements 11.7
    """
    one_before = _decide(now=FIXED_EXPIRY - ONE_MICROSECOND, expiry=FIXED_EXPIRY)
    assert one_before.reason is EntitlementReason.SUBSCRIBED
    assert one_before.entitling, "one microsecond before the expiry the Subscription entitles"

    exactly_at = _decide(now=FIXED_EXPIRY, expiry=FIXED_EXPIRY)
    assert not exactly_at.entitling, (
        "t == e exactly must NOT entitle: the boundary is `now >= period_expiry -> not "
        "entitling`, so the expiry instant itself is outside the period"
    )
    assert exactly_at.reason is EntitlementReason.EXPIRED

    one_after = _decide(now=FIXED_EXPIRY + ONE_MICROSECOND, expiry=FIXED_EXPIRY)
    assert not one_after.entitling
    assert one_after.reason is EntitlementReason.EXPIRED

    # And the same three points give the same answers with the sweep having run first.
    for offset in (-ONE_MICROSECOND, timedelta(0), ONE_MICROSECOND):
        instant = FIXED_EXPIRY + offset
        dead_sweep = _decide(now=instant, expiry=FIXED_EXPIRY)
        swept = _decide(now=instant, expiry=FIXED_EXPIRY, sweep_first=True)
        assert dead_sweep == swept, (
            f"at t = e {offset:+} the decision changed because the sweep ran: {dead_sweep!r} "
            f"versus {swept!r}"
        )


def test_a_null_period_expiry_is_non_entitling_whatever_the_status_label_says() -> None:
    """A cleared period is not a perpetual entitlement, and ``status = 'active'`` cannot save it.

    This is the shape the removed ``renew_subscription`` path left behind (Requirement 11.16):
    the label set to active and the expiry cleared, with no confirmed payment.

    Validates: Requirements 11.7
    """
    for status in ("active", "expired", "cancelled", "pending", "payment_failed", "refunded"):
        decision = _decide(now=FIXED_EXPIRY, expiry=None, status=status)
        assert not decision.entitling, (
            f"a null period_expiry with status={status!r} was treated as entitling"
        )
        assert decision.reason is EntitlementReason.EXPIRED, (
            f"status={status!r} with a null expiry answered {decision.reason.value}"
        )


def test_the_sweep_is_not_the_authority_on_when_access_ends() -> None:
    """A row still labelled ``'active'`` because the sweep is dead is refused past its expiry;
    a row the sweep already relabelled is admitted before its expiry.

    Both directions matter. The first is Requirement 11.7's headline. The second is the trap on
    the other side: an implementation that read the ``status`` label would also *deny* a row the
    sweep had wrongly touched, and would look correct on the first case alone.

    Validates: Requirements 11.7
    """
    past = FIXED_EXPIRY + timedelta(days=1)
    dead_sweep = _decide(now=past, expiry=FIXED_EXPIRY)
    assert dead_sweep.reason is EntitlementReason.EXPIRED
    assert not dead_sweep.entitling, (
        "an 'active' label survives a dead sweep, but the access does not: the resolver "
        "compares the instant, not the label"
    )
    assert _decide(now=past, expiry=FIXED_EXPIRY, sweep_first=True) == dead_sweep

    inside = FIXED_EXPIRY - timedelta(days=1)
    unswept = _decide(now=inside, expiry=FIXED_EXPIRY)
    swept = _decide(now=inside, expiry=FIXED_EXPIRY, sweep_first=True)
    assert unswept.reason is EntitlementReason.SUBSCRIBED
    assert unswept == swept, (
        "the sweep's presence changed an in-period decision; its predicate should not have "
        "selected the row at all"
    )


def test_not_subscribed_and_expired_are_distinct_answers_that_do_not_collapse() -> None:
    """A caller who never subscribed and a subscriber whose period ended get different codes.

    Requirement 7.10 keeps the two apart, and the boundary property is only meaningful if they
    are: collapsing ``NOT_SUBSCRIBED`` onto ``EXPIRED`` would make "the expiry decided it" true
    of every refusal, including refusals the expiry had nothing to do with.

    Validates: Requirements 11.7
    """
    never = _decide(now=FIXED_EXPIRY, expiry=None, with_subscription=False)
    lapsed = _decide(now=FIXED_EXPIRY, expiry=FIXED_EXPIRY)

    assert never.reason is EntitlementReason.NOT_SUBSCRIBED
    assert lapsed.reason is EntitlementReason.EXPIRED
    assert never.reason is not lapsed.reason
    assert never.wire_code != lapsed.wire_code, (
        f"NOT_SUBSCRIBED and EXPIRED collapsed onto the one wire code {never.wire_code!r}"
    )
    assert HTTP_STATUS_FOR_CODE[never.wire_code] == 403
    assert HTTP_STATUS_FOR_CODE[lapsed.wire_code] == 403
    # A subscription id travels on the lapsed answer and not on the never-subscribed one, so the
    # two are distinguishable by their payload as well as by their code.
    assert lapsed.subscription_id is not None
    assert never.subscription_id is None


def test_a_suspended_or_unpublished_listing_still_entitles_until_the_expiry_instant() -> None:
    """A Listing the seller hid does not revoke an already-paid, unexpired Subscription, and it
    does not extend one either.

    Requirement 4.11. The submission state and the expiry instant are independent axes, and
    ``t >= e`` refuses on every one of them.

    Validates: Requirements 11.7
    """
    for state in LIVE_SUBMISSION_STATES:
        inside = _decide(
            now=FIXED_EXPIRY - ONE_MICROSECOND,
            expiry=FIXED_EXPIRY,
            submission_state=state,
        )
        assert inside.reason is EntitlementReason.SUBSCRIBED, (
            f"a {state} Listing refused an ACTIVE, unexpired Subscription with "
            f"{inside.reason.value}"
        )
        at_the_instant = _decide(
            now=FIXED_EXPIRY, expiry=FIXED_EXPIRY, submission_state=state
        )
        assert at_the_instant.reason is EntitlementReason.EXPIRED, (
            f"a {state} Listing answered {at_the_instant.reason.value} at t == e; the expiry "
            "decides, and it decides the same way for every live submission state"
        )


def test_an_unresolvable_source_strategy_is_listing_unavailable_at_409() -> None:
    """Inside the period, an artifact that no longer resolves is ``LISTING_UNAVAILABLE`` -> 409;
    past the expiry the answer is ``EXPIRED``, because the subscription ended first.

    Requirement 7.11 for the code and the status, Requirement 11.7 for the ordering: an ended
    Subscription is told its period ended, not that the seller's Listing is broken.

    Validates: Requirements 11.7
    """
    inside = _decide(
        now=FIXED_EXPIRY - ONE_MICROSECOND, expiry=FIXED_EXPIRY, version_resolves=False
    )
    assert inside.reason is EntitlementReason.LISTING_UNAVAILABLE
    assert not inside.entitling
    assert HTTP_STATUS_FOR_CODE[inside.wire_code] == 409

    past = _decide(now=FIXED_EXPIRY, expiry=FIXED_EXPIRY, version_resolves=False)
    assert past.reason is EntitlementReason.EXPIRED, (
        "past the expiry the resolver must answer EXPIRED rather than reach the artifact read"
    )


def test_the_case_pool_spans_both_sides_of_the_boundary_and_every_reason_it_claims() -> None:
    """The generator's pool is not all-refusals, covers both sides of the boundary, and the
    sweep genuinely relabels the rows the metamorphic half depends on.

    Deterministic and independent of what Hypothesis draws: it enumerates the pool itself. If a
    later edit made every shape refuse, or made the sweep a no-op for every case, P-11 would
    still pass while asserting nothing — and this fails instead.

    Validates: Requirements 11.7
    """
    now = FIXED_EXPIRY
    observed: Dict[Tuple[str, str, str], EntitlementReason] = {}
    relabelled = 0

    for shape in SHAPES:
        for status in DRIVEN_STATUSES:
            # ``t`` relative to ``e``, named from the evaluation instant's point of view: the
            # offset is ``e - t``, so ``e`` one microsecond LATER puts ``t`` one microsecond
            # BEFORE the expiry.
            for offset_name, offset in (
                ("before", EXPIRY_ONE_MICROSECOND_AFTER),
                ("at", EXPIRY_AT_THE_EVALUATION_INSTANT),
                ("after", EXPIRY_ONE_MICROSECOND_BEFORE),
            ):
                case = build_case(
                    shape=shape,
                    now=now,
                    offset=offset,
                    status=status,
                    submission_state="SUSPENDED",
                    subscription_id="11110000-0000-4000-8000-0000000011ad",
                )
                store = build_store(case)
                decision = resolve_at(store, now)
                assert decision.reason is expected_reason(case), (
                    f"({shape}, {status}, t {offset_name} e) declares "
                    f"{expected_reason(case).value} but resolves to {decision.reason.value}"
                )
                observed[(shape, status, offset_name)] = decision.reason
                run_sweep(store, now)
                if case.sweep_should_relabel:
                    assert (
                        store.status_of(case.subscription_id)
                        == sweep_mod.EXPIRED_STATUS_TEXT
                    )
                    relabelled += 1

    reasons = set(observed.values())
    assert EntitlementReason.SUBSCRIBED in reasons, "the pool admits nobody; P-11 would be vacuous"
    assert {
        EntitlementReason.EXPIRED,
        EntitlementReason.NOT_SUBSCRIBED,
        EntitlementReason.LISTING_UNAVAILABLE,
        EntitlementReason.SUBSCRIPTION_SUSPENDED,
    } <= reasons, (
        "the pool no longer distinguishes the refusal causes Requirements 7.10 and 7.11 keep "
        f"apart; it produces only {sorted(r.value for r in reasons)}"
    )

    # Both sides of the boundary, by name, for the shape the boundary is about.
    assert observed[("live_listing", "active", "before")] is EntitlementReason.SUBSCRIBED
    assert observed[("live_listing", "active", "at")] is EntitlementReason.EXPIRED
    assert observed[("live_listing", "active", "after")] is EntitlementReason.EXPIRED

    assert relabelled > 0, (
        "the sweep relabelled nothing anywhere in the pool, so the sweep-independence half of "
        "P-11 is asserting nothing"
    )
