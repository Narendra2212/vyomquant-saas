"""Property tests for the Subscription_State machine: reachability, refusal, period
well-formedness and history preservation.

Feature: marketplace-subscriptions-paper-trading
Design reference: ``design.md § Property-to-test mapping`` ->
``P-8 … P-10, P-15 | tests/property/test_subscription_state_machine.py | operation sequences
over the 7 states | SUBSCRIPTION_TRANSITIONS reachability closure``.

Properties living here
----------------------
``test_p8_every_subscription_state_is_reachable_from_pending``   (task 19.8, Reqs 11.1, 11.2)
``test_p9_illegal_subscription_transition_leaves_state``          (task 19.9, Req 11.3)
``test_p10_active_subscription_period_is_well_formed``            (task 19.10, Req 11.13)
``test_p15_subscription_and_settlement_history_never_shrinks``    (task 19.11, Reqs 10.8, 11.11)

THE PERMITTED SET IS THIRTEEN PAIRS, NOT TWELVE
-----------------------------------------------
Requirement 11.2 enumerates twelve pairs. Requirement 11.6 *also* names ``PAYMENT_FAILED``
among the sources of a transition into ``ACTIVE``, and the two clauses contradict each other.
Task 19.2 resolved the contradiction in favour of 11.6 - ``settlement_service``'s
``ELIGIBLE_FOR_ACTIVATION`` carries five statuses - and task 19.15 closed the resulting
application/database divergence with the additive
``backend_app/migrations/012_subscription_payment_failed_activation.sql``, which seeds the one
``('payment_failed','active')`` edge into
``marketplace_subscription_allowed_transitions``.

So the set the Persistence_Layer admits is
``SUBSCRIPTION_TRANSITIONS ∪ MIGRATION_012_SUBSCRIPTION_ADDENDUM`` - **thirteen** pairs. Both
P-8 and P-9 are stated against that *effective* set, read through the one helper that answers
"what does ``trg_subscription_transition_guard`` admit today",
``tests.test_submission_state_agreement.subscription_permitted_pairs_in_db()``, which parses
both migrations off disk. Nothing here transcribes a pair list. Stating P-9 against 008's
twelve alone would have it claim the database refuses an edge 012 permits, which is the
opposite of true - and would have hidden the very divergence 19.15 was written to close.

``ACTIVE -> ACTIVE`` IS NOT A TRANSITION
----------------------------------------
A confirmed payment for a Subscription that is already ``active`` is an **in-place renewal**:
no state changes, and the period is extended by ``period_for_renewal`` anchored on the stored
``period_expiry`` (``settlement_service._apply_transition``, requirements decision 1 in that
module's docstring). No self-edge is seeded, and ``marketplace_subscription_guard``'s first
branch returns early for a same-value write precisely so the renewal path can move the period
columns. :data:`IN_PLACE_RENEWAL` names the pair once, and both P-8 and P-9 exclude it: P-9
must not demand an error for an operation the design requires to succeed.

WHAT "THE ATTEMPT" MEANS FOR P-9, AND WHY IT IS NOT P-49 AGAIN
--------------------------------------------------------------
``tests/property/test_db_transition_guards.py::test_p49_database_refuses_illegal_transitions``
already owns the *mechanism*: the guard refuses by generic membership against the seed table
and its ``RAISE`` names both states. That is not repeated here. What these four properties
cover is the **contents** of that set and the **service behaviour** in front of it:

* the effective thirteen pairs make every one of Requirement 11.1's seven states reachable
  from ``PENDING``, and the reachable set is closed under the operations real writers perform
  (P-8);
* for every pair absent from that set, the writers that *can* target the pair's destination -
  ``settlement_service.settle`` for ``active`` and ``refunded``, ``expiry_sweep.sweep`` for
  ``expired``, and the cancel path's guarded UPDATE for ``cancelled`` - leave the stored
  status, period start and period expiry exactly where they were, write no history row and
  grant no entitlement, while the application gate ``can_transition`` answers ``False``
  (P-9);
* every ``active`` row a real writer produces carries a period start, a period expiry and
  ``expiry > start`` (P-10);
* no operation sequence ever removes a Subscription row or a Settlement_Record (P-15).

Two of the seven states - ``PENDING`` and ``SUSPENDED`` - are the destination of no write in
these three modules, so no attempt can be constructed for them through a sanctioned path.
That is asserted as a fact (they are not in :data:`ATTEMPTABLE_TARGETS`) rather than skipped,
so a future writer that starts producing one of them fails this property instead of slipping
past it; a *direct* UPDATE to either is refused by the database, which is P-49's statement.

THE DOUBLE
----------
``tests/test_settlement_service.py``'s ``FakeSupabase`` is reused rather than replaced: it
keeps mutable in-memory ``library_subscriptions``, ``marketplace_settlements``,
``library_subscription_transitions`` and ``deployment_permissions`` tables and it **enforces**
``uq_settlement_reference_reversal``, which is what makes a redelivered refund a genuine
no-op here rather than an assumed one. ``RecordingGrant`` and ``RecordingAuditLogger`` come
from the same place, as does ``_run_coroutine`` (``asyncio.run`` closes its loop and leaves
the thread without one, which breaks modules collected afterwards).

:class:`_SubscriptionStore` subclasses that double to add the three PostgREST filters the
expiry sweep uses - ``.not_.is_``, ``.lte`` and ``.in_`` - so the **real**
``expiry_sweep.sweep`` drives the ``expired`` operation instead of a modelled status write.
The instant-parsing helper the ``lte`` filter needs is imported from
``tests/test_expiry_sweep.py`` rather than copied.

ONE OPERATION HAS NO SERVICE TO DRIVE IT
----------------------------------------
``cancel`` is still a FastAPI route body in ``backend_app/routers/library.py`` -
``POST /subscriptions/{sub_id}/cancel`` - with no service module behind it, and task 19.3 has
not landed. :func:`_cancel` therefore issues that route's own statement,
``update({'status': 'cancelled', …}).eq('id', …).eq('status', 'active')``, against the double:
the same payload and the same optimistic ``.eq('status','active')`` guard. That guard is what
makes the operation a no-op from every state except ``active``, which is exactly the fact P-9
needs from it. This file does not modify that router.

MONEY
-----
Every amount is an integer number of Minor_Units drawn from ``minor_amounts``; no ``float``
appears near an amount, and no arithmetic is performed on one here at all. The 90/10 split is
``money.split_ninety_ten``'s, reached through ``settle``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.marketplace import expiry_sweep as sweep_module
from backend_app.backend.marketplace import settlement_service as ss
from backend_app.backend.marketplace.settlement_service import (
    ELIGIBLE_FOR_ACTIVATION,
    SettlementOutcome,
    SettlementPersistFailed,
    settle,
)
from backend_app.backend.marketplace.subscription_period import (
    add_one_calendar_month,
    period_for_activation,
    period_for_renewal,
)
from backend_app.backend.marketplace.subscription_state import (
    PAYMENT_REQUIRED_TARGETS,
    STATUS_TEXT_FOR_STATE,
    SUBSCRIPTION_TRANSITIONS,
    SubscriptionState,
    can_transition,
    normalise_subscription_state,
)

# The doubles, the loop runner and the row builder - reused, not re-invented.
from tests.test_settlement_service import (
    PROVIDER,
    SUBSCRIPTION_ID,
    FakeSupabase as _SettlementSupabase,
    RecordingAuditLogger,
    RecordingGrant,
    _Query as _SettlementQuery,
    _run_coroutine,
    _subscription_row,
)

# The instant parser the sweep's ``lte`` filter needs, from the sweep's own suite.
from tests.test_expiry_sweep import _as_instant

# The ONE helper that answers "what does trg_subscription_transition_guard admit today",
# and the named constant for the Requirement 11.6 addendum. Never transcribed.
from tests.test_submission_state_agreement import (
    MIGRATION_012_SUBSCRIPTION_ADDENDUM,
    subscription_permitted_pairs_in_db,
)
from tests.strategies.marketplace_generators import minor_amounts, utc_instants

StatePair = Tuple[SubscriptionState, SubscriptionState]

#: The configuration ``design.md § Property-based testing configuration`` prescribes, matching
#: the decorator every other module in ``tests/property/`` uses: at least 100 examples where
#: the example is cheap, no per-example deadline (each example runs an ``async`` service call
#: through a fresh event loop, and the first one pays the migration read and parse cost).
#: ``max_examples`` is held down on the sequence-driven properties so the whole file finishes
#: in well under three minutes.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

SEQUENCE_SETTINGS = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


# ══════════════════════════════════════════════════════════════════════════
# THE EFFECTIVE PERMITTED SET (Requirement 11.2 + the Requirement 11.6 addendum)
# ══════════════════════════════════════════════════════════════════════════


def _effective_permitted_pairs() -> FrozenSet[StatePair]:
    """Every ``(from, to)`` edge the Persistence_Layer admits, as enum members.

    Read through :func:`subscription_permitted_pairs_in_db`, which parses 008's twelve-pair
    seed and 012's one-pair addendum off disk and unions them. Resolved to
    :class:`SubscriptionState` members through ``normalise_subscription_state`` so nothing in
    this module depends on a casing convention.
    """
    pairs: set = set()
    for from_text, to_text in subscription_permitted_pairs_in_db():
        source = normalise_subscription_state(from_text)
        target = normalise_subscription_state(to_text)
        assert source is not None and target is not None, (
            f"the permitted set carries ({from_text!r}, {to_text!r}), which is not one of "
            f"Requirement 11.1's seven Subscription_State values"
        )
        pairs.add((source, target))
    assert pairs, "the effective permitted set parsed to zero pairs"
    return frozenset(pairs)


#: The thirteen pairs, as enum members.
PERMITTED_PAIRS: FrozenSet[StatePair] = _effective_permitted_pairs()

#: The same thirteen as persisted ``library_subscriptions.status`` text, resolved through the
#: one enum -> column mapping.
PERMITTED_STATUS_PAIRS: FrozenSet[Tuple[str, str]] = frozenset(
    (STATUS_TEXT_FOR_STATE[source], STATUS_TEXT_FOR_STATE[target])
    for source, target in PERMITTED_PAIRS
)

#: The in-place renewal. NOT a transition: no state changes, the period is extended from the
#: stored ``period_expiry`` by ``period_for_renewal``, and no self-edge is seeded. Named once
#: so both P-8 and P-9 exclude it by the same constant.
IN_PLACE_RENEWAL: StatePair = (SubscriptionState.ACTIVE, SubscriptionState.ACTIVE)
IN_PLACE_RENEWAL_STATUS_PAIR: Tuple[str, str] = (
    STATUS_TEXT_FOR_STATE[SubscriptionState.ACTIVE],
    STATUS_TEXT_FOR_STATE[SubscriptionState.ACTIVE],
)

#: The states each module under test is able to WRITE, derived from production constants
#: rather than transcribed: ``settlement_service`` reaches ``PAYMENT_REQUIRED_TARGETS`` (i.e.
#: ``ACTIVE``) for a payment and ``REFUNDED`` for a reversal, ``expiry_sweep`` reaches its own
#: ``SWEEP_TARGET_STATE``, and the cancel route reaches ``CANCELLED``.
SETTLEMENT_TARGETS: FrozenSet[SubscriptionState] = frozenset(PAYMENT_REQUIRED_TARGETS) | {
    SubscriptionState.REFUNDED
}
SWEEP_TARGETS: FrozenSet[SubscriptionState] = frozenset({sweep_module.SWEEP_TARGET_STATE})
CANCEL_TARGETS: FrozenSet[SubscriptionState] = frozenset({SubscriptionState.CANCELLED})
ATTEMPTABLE_TARGETS: FrozenSet[SubscriptionState] = (
    SETTLEMENT_TARGETS | SWEEP_TARGETS | CANCEL_TARGETS
)

_STATUS_ACTIVE = STATUS_TEXT_FOR_STATE[SubscriptionState.ACTIVE]
_STATUS_CANCELLED = STATUS_TEXT_FOR_STATE[SubscriptionState.CANCELLED]

#: The states a Subscription row may sensibly be seeded in with a stored period. ``PENDING``
#: and ``PAYMENT_FAILED`` have never been paid, so they carry no period.
_STATES_WITH_A_PERIOD: FrozenSet[SubscriptionState] = frozenset(
    {
        SubscriptionState.ACTIVE,
        SubscriptionState.EXPIRED,
        SubscriptionState.CANCELLED,
        SubscriptionState.SUSPENDED,
        SubscriptionState.REFUNDED,
    }
)


StateSet = FrozenSet[SubscriptionState]


def _reachable_from(origin: SubscriptionState, pairs: FrozenSet[StatePair]) -> StateSet:
    """The transitive closure of ``pairs`` from ``origin``, ``origin`` included.

    The oracle ``design.md`` names for these properties: a breadth-first walk of the edge set,
    computed here rather than asserted from a hand-written list, so it follows the effective
    permitted set instead of a transcription of it.
    """
    seen = {origin}
    frontier = [origin]
    while frontier:
        state = frontier.pop()
        for source, target in pairs:
            if source is state and target not in seen:
                seen.add(target)
                frontier.append(target)
    return frozenset(seen)


def _shortest_path(
    origin: SubscriptionState, target: SubscriptionState, pairs: FrozenSet[StatePair]
) -> Optional[List[SubscriptionState]]:
    """A witness path ``origin -> … -> target``, or ``None`` when unreachable.

    Only used to make a counterexample readable: "PENDING -> ACTIVE -> SUSPENDED" says far
    more about a reachability failure than "SUSPENDED is not reachable" does.
    """
    if origin is target:
        return [origin]
    previous: Dict[SubscriptionState, SubscriptionState] = {}
    queue = [origin]
    seen = {origin}
    while queue:
        state = queue.pop(0)
        for source, destination in sorted(
            pairs, key=lambda pair: (pair[0].value, pair[1].value)
        ):
            if source is not state or destination in seen:
                continue
            seen.add(destination)
            previous[destination] = state
            if destination is target:
                path = [target]
                while path[-1] is not origin:
                    path.append(previous[path[-1]])
                return list(reversed(path))
            queue.append(destination)
    return None


#: Every state reachable from ``PENDING`` under the effective permitted set.
REACHABLE_FROM_PENDING: StateSet = _reachable_from(
    SubscriptionState.PENDING, PERMITTED_PAIRS
)


# ══════════════════════════════════════════════════════════════════════════
# THE DOUBLE, WIDENED WITH THE THREE FILTERS THE EXPIRY SWEEP USES
# ══════════════════════════════════════════════════════════════════════════


class _StoreQuery(_SettlementQuery):
    """The settlement double's query builder plus ``.not_.is_``, ``.lte`` and ``.in_``.

    The settlement suite's builder records ``.eq`` only, because ``settlement_service`` issues
    nothing else. ``expiry_sweep`` narrows on ``period_expiry IS NOT NULL``,
    ``period_expiry <= now`` and ``status IN (…)``, and those filters must be **applied**, not
    merely accepted: a double that swallowed them and then updated every row would let this
    file pass against a sweep with no predicate at all - and would report an expiry the sweep
    never performed as a permitted transition.
    """

    class _Not:
        """The ``.not_`` accessor of the PostgREST chain. Only ``is_`` is needed."""

        def __init__(self, query: "_StoreQuery") -> None:
            self._query = query

        def is_(self, column: str, value: Any) -> "_StoreQuery":
            self._query.extra_filters.append(("not.is", column, value))
            return self._query

    def __init__(self, table: str, client: "_SubscriptionStore") -> None:
        super().__init__(table, client)
        self.extra_filters: List[Tuple[str, str, Any]] = []

    @property
    def not_(self) -> "_StoreQuery._Not":
        return _StoreQuery._Not(self)

    def lte(self, column: str, value: Any) -> "_StoreQuery":
        self.extra_filters.append(("lte", column, value))
        return self

    def in_(self, column: str, values: Any) -> "_StoreQuery":
        self.extra_filters.append(("in", column, list(values)))
        return self


class _SubscriptionStore(_SettlementSupabase):
    """The settlement double, serving the expiry sweep's chain as well.

    Everything else - the four tables, ``uq_settlement_reference_reversal``, the recorded
    statement list and the ``(op, table)`` failure injection - is inherited unchanged.
    """

    def table(self, name: str) -> _StoreQuery:
        return _StoreQuery(name, self)

    @staticmethod
    def _matching(rows: List[Dict[str, Any]], q: Any) -> List[Dict[str, Any]]:
        matched = _SettlementSupabase._matching(rows, q)
        for kind, column, value in getattr(q, "extra_filters", ()):
            if kind == "in":
                wanted = {str(item) for item in value}
                matched = [row for row in matched if str(row.get(column)) in wanted]
            elif kind == "lte":
                bound = _as_instant(value)
                kept: List[Dict[str, Any]] = []
                for row in matched:
                    left = _as_instant(row.get(column))
                    # SQL three-valued logic: NULL <= x is unknown, so the row is not matched.
                    if left is not None and bound is not None and left <= bound:
                        kept.append(row)
                matched = kept
            elif kind == "not.is":
                matched = [row for row in matched if row.get(column) is not None]
        return matched

    # ---- readings the properties make -----------------------------------
    def subscription_status(self, row_id: str = SUBSCRIPTION_ID) -> Optional[str]:
        return _status_text(self.subscription(row_id).get("status"))

    def period(self, row_id: str = SUBSCRIPTION_ID) -> Tuple[Any, Any]:
        row = self.subscription(row_id)
        return row.get("period_start"), row.get("period_expiry")

    def settlement_snapshot(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.settlements]

    def transition_pairs(self) -> List[Tuple[Optional[str], Optional[str]]]:
        return [
            (_status_text(row.get("from_state")), _status_text(row.get("to_state")))
            for row in self.transitions
        ]

    def destructive_statements(self) -> List[Tuple[str, str]]:
        """Every DELETE, and every UPDATE against the append-only ledger."""
        return [
            (statement.op, statement.table_name)
            for statement in self.statements
            if statement.op == "delete"
            or (statement.op == "update" and statement.table_name == ss.SETTLEMENT_TABLE)
            or (statement.op == "update" and statement.table_name == ss.TRANSITION_TABLE)
        ]


def _status_text(value: Any) -> Optional[str]:
    """A stored ``library_subscriptions.status`` value as lowercase text, or ``None``."""
    if value is None:
        return None
    return str(value).strip().lower()


@pytest.fixture(autouse=True)
def _recording_audit(monkeypatch: pytest.MonkeyPatch) -> RecordingAuditLogger:
    """Install a recording audit logger in place of the real Redis-backed one.

    Autouse rather than requested by name: a function-scoped fixture named in a ``@given``
    test's signature is reset once per test *function*, not per example, which Hypothesis
    (rightly) health-checks. None of these four properties asserts on the audit trail -
    Requirements 10.9 and 11.12 are ``tests/test_settlement_service.py``'s and
    ``tests/test_expiry_sweep.py``'s - but ``settlement_service`` treats a failed *required*
    audit as a persistence failure and retries it, so a live audit stack would turn every
    example into a five-attempt retry against Redis.
    """
    from backend_app.core import audit_trail

    recorder = RecordingAuditLogger()
    monkeypatch.setattr(audit_trail, "get_strategy_audit_logger", lambda: recorder)
    return recorder


# ══════════════════════════════════════════════════════════════════════════
# THE OPERATIONS - each one a real writer, or the cancel route's own statement
# ══════════════════════════════════════════════════════════════════════════

#: The four operations P-15 names. ``renew`` is a confirmed payment: on a row that has never
#: been paid it is the first activation, on a live row it is the in-place renewal, and it is
#: the same writer either way.
SEQUENCE_OPERATIONS: Tuple[str, ...] = ("renew", "cancel", "expire", "refund")


@dataclass
class _Step:
    """One applied operation, with the store either side of it."""

    operation: str
    instant: datetime
    before_status: Optional[str]
    after_status: Optional[str]
    before_period: Tuple[Any, Any]
    after_period: Tuple[Any, Any]
    before_subscription_rows: int
    after_subscription_rows: int
    before_settlements: List[Dict[str, Any]]
    after_settlements: List[Dict[str, Any]]
    before_transitions: int
    after_transitions: int
    outcome: Any = None

    @property
    def moved(self) -> bool:
        return self.before_status != self.after_status

    @property
    def status_pair(self) -> Tuple[Optional[str], Optional[str]]:
        return (self.before_status, self.after_status)


class _Journal:
    """One generated operation sequence applied to one Subscription, step by step.

    Holds the double, the injected entitlement writer and the per-step record the properties
    assert over. A single instance is used per Hypothesis example.
    """

    def __init__(
        self,
        *,
        start: SubscriptionState,
        amount_minor: int,
        currency: str,
        base: datetime,
    ) -> None:
        self.start = start
        self.amount_minor = amount_minor
        self.currency = currency
        self.base = base
        self.client = _SubscriptionStore(
            subscriptions=[_seed_row(start, amount_minor, currency, base)]
        )
        self.grant = RecordingGrant()
        self.sweep_audits: List[Tuple[Any, Dict[str, Any]]] = []
        self.steps: List[_Step] = []
        self._reference_counter = 0
        self._settled_references: List[str] = []
        self._reversed_references: set = set()

    # ---- the operations -------------------------------------------------
    def _next_reference(self) -> str:
        self._reference_counter += 1
        return f"pi_seq_{self._reference_counter:03d}"

    async def _renew(self, instant: datetime) -> Any:
        """A confirmed payment through the one Settlement_Record writer."""
        reference = self._next_reference()
        try:
            result = await settle(
                provider_reference=reference,
                provider=PROVIDER,
                amount_minor=self.amount_minor,
                currency=self.currency,
                subscription_id=SUBSCRIPTION_ID,
                confirmation_instant=instant,
                supabase=self.client,
                grant_permission=self.grant,
            )
        except SettlementPersistFailed as exc:  # pragma: no cover - no failure is injected
            return exc.result
        if result.wrote_settlement:
            self._settled_references.append(reference)
        return result

    async def _refund(self, instant: datetime) -> Any:
        """A reversal through the same writer, correlated the way task 19.16 correlates it.

        The reference is the *payment's* own, read back out of the ledger by
        ``find_settled_payment`` - never taken from provider metadata. With no payment on the
        ledger yet, the refund names a reference this system never settled, which must write
        nothing at all.
        """
        outstanding = [
            reference
            for reference in self._settled_references
            if reference not in self._reversed_references
        ]
        reference = outstanding[0] if outstanding else "pi_never_settled"
        if outstanding:
            payment = ss.find_settled_payment(self.client, provider_reference=reference)
            assert payment is not None, (
                f"the ledger lost the payment written under {reference!r}; a Settlement_Record "
                f"is never deleted (Requirement 10.8)"
            )
        else:
            assert (
                ss.find_settled_payment(self.client, provider_reference=reference) is None
            )
            return None
        try:
            result = await settle(
                provider_reference=reference,
                provider=PROVIDER,
                amount_minor=self.amount_minor,
                currency=self.currency,
                subscription_id=SUBSCRIPTION_ID,
                confirmation_instant=instant,
                supabase=self.client,
                is_reversal=True,
                reverses_reference=reference,
                grant_permission=self.grant,
            )
        except SettlementPersistFailed as exc:  # pragma: no cover - no failure is injected
            return exc.result
        self._reversed_references.add(reference)
        return result

    async def _expire(self, instant: datetime) -> Any:
        """The real expiry sweep, with Requirement 11.15's enforcement out of scope here."""

        async def _audit(action: Any, **kwargs: Any) -> Any:
            self.sweep_audits.append((action, dict(kwargs)))
            return {"audit_id": f"AUDIT-{len(self.sweep_audits)}"}

        return await sweep_module.sweep(
            supabase=self.client,
            now=instant,
            stop_running=False,
            audit_writer=_audit,
        )

    def _cancel(self, instant: datetime) -> Any:
        """The cancel route's own statement (see the module docstring)."""
        return (
            self.client.table(ss.SUBSCRIPTION_TABLE)
            .update(
                {
                    "status": _STATUS_CANCELLED,
                    "cancelled_at": instant.isoformat(),
                }
            )
            .eq("id", SUBSCRIPTION_ID)
            # The route's optimistic guard: cancelling is a no-op from every other state.
            .eq("status", _STATUS_ACTIVE)
            .execute()
        )

    # ---- the driver -----------------------------------------------------
    async def run(self, operations: Sequence[str], instants: Sequence[datetime]) -> None:
        assert len(operations) == len(instants)
        for operation, instant in zip(operations, instants):
            before = self._snapshot()
            if operation == "renew":
                outcome: Any = await self._renew(instant)
            elif operation == "refund":
                outcome = await self._refund(instant)
            elif operation == "expire":
                outcome = await self._expire(instant)
            elif operation == "cancel":
                outcome = self._cancel(instant)
            else:  # pragma: no cover - the vocabulary is closed
                raise AssertionError(f"unknown operation {operation!r}")
            self.steps.append(self._step(operation, instant, before, outcome))

    def _snapshot(self) -> Dict[str, Any]:
        return {
            "status": self.client.subscription_status(),
            "period": self.client.period(),
            "subscription_rows": len(self.client.subscriptions),
            "settlements": self.client.settlement_snapshot(),
            "transitions": len(self.client.transitions),
        }

    def _step(
        self, operation: str, instant: datetime, before: Dict[str, Any], outcome: Any
    ) -> _Step:
        return _Step(
            operation=operation,
            instant=instant,
            before_status=before["status"],
            after_status=self.client.subscription_status(),
            before_period=before["period"],
            after_period=self.client.period(),
            before_subscription_rows=before["subscription_rows"],
            after_subscription_rows=len(self.client.subscriptions),
            before_settlements=before["settlements"],
            after_settlements=self.client.settlement_snapshot(),
            before_transitions=before["transitions"],
            after_transitions=len(self.client.transitions),
            outcome=outcome,
        )

    def trace(self) -> str:
        """The applied sequence as ``pending -[renew]-> active -[cancel]-> cancelled``."""
        if not self.steps:
            return str(self.start.value)
        parts = [str(self.steps[0].before_status)]
        for step in self.steps:
            parts.append(f"-[{step.operation}]->")
            parts.append(str(step.after_status))
        return " ".join(parts)


def _seed_row(
    status: SubscriptionState, amount_minor: int, currency: str, base: datetime
) -> Dict[str, Any]:
    """One ``library_subscriptions`` row in ``status``, with a period where one belongs.

    Built with ``tests/test_settlement_service.py``'s row builder so the column set is the one
    ``SETTLEMENT_SUBSCRIPTION_SELECT`` projects. A state that has been paid for carries a
    period that started ten days before ``base``; ``PENDING`` and ``PAYMENT_FAILED`` have never
    been paid, so they carry none - which is also what makes
    ``chk_ls_active_has_period``'s invariant non-vacuous for the rows that do.
    """
    row = _subscription_row(
        status=STATUS_TEXT_FOR_STATE[status],
        price_minor=amount_minor,
        currency=currency,
    )
    if status in _STATES_WITH_A_PERIOD:
        start = base - timedelta(days=10)
        row["period_start"] = start.isoformat()
        row["period_expiry"] = add_one_calendar_month(start).isoformat()
    return row


# ══════════════════════════════════════════════════════════════════════════
# GENERATORS
# ══════════════════════════════════════════════════════════════════════════

_STATES: Tuple[SubscriptionState, ...] = tuple(SubscriptionState)

#: The states no writer in the three modules this file drives can produce, so no attempt at a
#: pair ending in one of them can be constructed here.
#:
#: ``PENDING`` is written by the checkout path when a Subscription row is created
#: (Requirement 11.3) and ``PAYMENT_FAILED`` by the same path when provider session creation
#: fails (Requirement 11.4); ``SUSPENDED`` is an administrative hold with no writer in the
#: codebase yet. Their edges are covered by ``tests/test_checkout_service.py``,
#: ``tests/test_billing_e2e.py`` and - for a direct Persistence_Layer UPDATE to any absent
#: pair - by ``test_p49_database_refuses_illegal_transitions``. Derived by subtraction so it
#: cannot silently disagree with :data:`ATTEMPTABLE_TARGETS`, and pinned by name in
#: :func:`test_the_attempt_space_is_the_three_writers_this_file_drives`.
NO_SANCTIONED_WRITER_HERE: StateSet = frozenset(_STATES) - ATTEMPTABLE_TARGETS


def _state_pairs() -> st.SearchStrategy:
    """Every ordered pair over Requirement 11.1's seven values - 49 of them."""
    return st.tuples(st.sampled_from(_STATES), st.sampled_from(_STATES))


def _absent_state_pairs() -> st.SearchStrategy:
    """The ordered pairs ABSENT from the effective permitted set, and not the in-place renewal.

    ``IN_PLACE_RENEWAL`` is filtered out because it is not a transition at all: no state
    changes, and ``settlement_service`` is required to extend the period for it
    (Requirement 11.5). Demanding an error for it would make P-9 contradict P-14.
    """
    return _state_pairs().filter(
        lambda pair: pair not in PERMITTED_PAIRS and pair != IN_PLACE_RENEWAL
    )


def _activation_sources() -> st.SearchStrategy:
    """Every state a confirmed payment may activate from, plus the in-place renewal source.

    Drawn from ``ELIGIBLE_FOR_ACTIVATION`` - the production set, five statuses including the
    Requirement 11.6 ``payment_failed`` source - so this generator follows the service rather
    than a transcription of it.
    """
    sources = sorted(ELIGIBLE_FOR_ACTIVATION | {_STATUS_ACTIVE})
    return st.sampled_from(sources).map(
        lambda text: normalise_subscription_state(text)
    )


def _monotone_instants(base: datetime, offsets: Sequence[int]) -> List[datetime]:
    """``base`` advanced by the cumulative day offsets, so the clock only moves forward.

    A step 40 days on is past a one-calendar-month period, which is what makes the ``expire``
    operation reachable; a step 0 days on is not, which is what keeps the not-yet-due branch
    reachable too.
    """
    instants: List[datetime] = []
    cursor = base
    for offset in offsets:
        cursor = cursor + timedelta(days=int(offset))
        instants.append(cursor)
    return instants


def _sequences() -> st.SearchStrategy:
    """``(start_state, operations, day_offsets, amount, currency, base_instant)``."""
    return st.tuples(
        st.sampled_from(_STATES),
        st.lists(st.sampled_from(SEQUENCE_OPERATIONS), min_size=1, max_size=5),
        st.lists(st.sampled_from((0, 1, 12, 40, 65)), min_size=5, max_size=5),
        minor_amounts(1, 10_000_000),
        st.sampled_from(("USD", "INR")),
        utc_instants(2020, 2026),
    )


def _run_sequence(
    start: SubscriptionState,
    operations: Sequence[str],
    offsets: Sequence[int],
    amount_minor: int,
    currency: str,
    base: datetime,
) -> _Journal:
    """Apply ``operations`` to a Subscription seeded in ``start``, and return the journal."""
    journal = _Journal(
        start=start, amount_minor=amount_minor, currency=currency, base=base
    )
    instants = _monotone_instants(base, list(offsets)[: len(operations)])
    _run_coroutine(journal.run(operations, instants))
    return journal


# ══════════════════════════════════════════════════════════════════════════
# PRECONDITIONS - the set these properties quantify over is the one the DB admits
# ══════════════════════════════════════════════════════════════════════════


def test_the_effective_permitted_set_is_the_twelve_pairs_plus_the_one_addendum() -> None:
    """Thirteen pairs, enumerated on both sides, before any property leans on the count.

    Not a restatement of ``tests/test_submission_state_agreement.py``'s agreement test but the
    precondition these four properties need: if "permitted" here meant 008's twelve alone,
    P-9 would quantify over a pair the guard admits and claim the database refuses it, and P-8
    would compute its reachability closure over the wrong graph.
    """
    expected = (
        frozenset(
            (source, target)
            for source, targets in SUBSCRIPTION_TRANSITIONS.items()
            for target in targets
        )
        | frozenset(
            (
                normalise_subscription_state(source),
                normalise_subscription_state(target),
            )
            for source, target in MIGRATION_012_SUBSCRIPTION_ADDENDUM
        )
    )
    assert PERMITTED_PAIRS == expected, (
        "the effective permitted set and SUBSCRIPTION_TRANSITIONS ∪ the Requirement 11.6 "
        "addendum describe different machines.\n"
        f"  in the database only: {sorted((a.value, b.value) for a, b in PERMITTED_PAIRS - expected)}\n"
        f"  in Python only: {sorted((a.value, b.value) for a, b in expected - PERMITTED_PAIRS)}"
    )
    assert len(PERMITTED_PAIRS) == 13, (
        f"the effective permitted set holds {len(PERMITTED_PAIRS)} pairs; Requirement 11.2's "
        f"twelve plus 012's one addendum is thirteen"
    )
    assert (
        SubscriptionState.PAYMENT_FAILED,
        SubscriptionState.ACTIVE,
    ) in PERMITTED_PAIRS, (
        "012's ('payment_failed','active') addendum is missing from the effective set, so a "
        "retried payment would record its Settlement_Record and then be refused activation"
    )
    assert IN_PLACE_RENEWAL not in PERMITTED_PAIRS, (
        "an active -> active self-edge has been seeded; the in-place renewal is not a "
        "transition and must not become one"
    )
    # The service's activation sources and the database's must be the same five, or P-9's
    # "absent" space would not match what settle() will actually attempt.
    db_activation_sources = frozenset(
        STATUS_TEXT_FOR_STATE[source]
        for source, target in PERMITTED_PAIRS
        if target is SubscriptionState.ACTIVE
    )
    assert ELIGIBLE_FOR_ACTIVATION == db_activation_sources, (
        f"settlement_service.ELIGIBLE_FOR_ACTIVATION is {sorted(ELIGIBLE_FOR_ACTIVATION)} but "
        f"the database admits activation from {sorted(db_activation_sources)}"
    )


def test_the_attempt_space_is_the_three_writers_this_file_drives() -> None:
    """Which Subscription_States a real writer here can target, and which it cannot.

    P-9 constructs a genuine attempt for every absent pair whose destination a writer can
    reach, and records the rest as unreachable-by-any-sanctioned-writer. That partition has to
    be pinned by name, or the recorded half would quietly grow and P-9 would go green by
    attempting less and less.
    """
    assert SETTLEMENT_TARGETS == {SubscriptionState.ACTIVE, SubscriptionState.REFUNDED}
    assert SWEEP_TARGETS == {SubscriptionState.EXPIRED}
    assert CANCEL_TARGETS == {SubscriptionState.CANCELLED}
    assert NO_SANCTIONED_WRITER_HERE == {
        # Written by the checkout path: the row is created PENDING (Requirement 11.3) and moved
        # to PAYMENT_FAILED when provider session creation fails (Requirement 11.4).
        SubscriptionState.PENDING,
        SubscriptionState.PAYMENT_FAILED,
        # An administrative hold. No writer in the codebase produces it yet.
        SubscriptionState.SUSPENDED,
    }, (
        f"the states with no sanctioned writer in settlement_service, expiry_sweep or the "
        f"cancel route are now "
        f"{sorted(state.value for state in NO_SANCTIONED_WRITER_HERE)}; P-9 attempts a real "
        f"write for every other destination, so this partition must be re-read deliberately"
    )


# ══════════════════════════════════════════════════════════════════════════
# P-8
# ══════════════════════════════════════════════════════════════════════════

# Feature: marketplace-subscriptions-paper-trading, Property 8 (invariant, reachability): for
# all generated sequences of Subscription operations, every persisted Subscription_State is
# reachable from PENDING by the transitions of Requirement 11.2 (as amended by the Requirement
# 11.6 addendum), and no persisted transition is outside that set.
#
# **Validates: Requirements 11.1, 11.2**


def test_p8_every_subscription_state_is_reachable_from_pending() -> None:
    """Every state is reachable from `PENDING`, and reachability is closed under operations.

    Three claims, the first static over the edge set and the other two quantified over
    generated operation sequences driven through the real writers:

    1. the reachability closure of the effective permitted set from ``PENDING`` is *all seven*
       of Requirement 11.1's values, with a witness path for each;
    2. every status a real operation persists is in that closure; and
    3. every persisted transition - both the ``(before, after)`` pair the store actually moved
       through and every ``library_subscription_transitions`` row - is in the permitted set,
       the one exception being the ``active -> active`` in-place renewal, which is not a
       transition.

    **Validates: Requirements 11.1, 11.2**
    """
    # ---- Claim 1: the closure covers all seven values ----
    assert len(_STATES) == 7, (
        f"Requirement 11.1 fixes the Subscription_State vocabulary at 7 values; "
        f"SubscriptionState has {len(_STATES)}"
    )
    unreachable = [state for state in _STATES if state not in REACHABLE_FROM_PENDING]
    assert not unreachable, (
        "these Subscription_States are not reachable from PENDING under the effective "
        f"permitted set: {[state.value for state in unreachable]}. The permitted set is "
        f"{sorted((a.value, b.value) for a, b in PERMITTED_PAIRS)}"
    )
    for state in _STATES:
        path = _shortest_path(SubscriptionState.PENDING, state, PERMITTED_PAIRS)
        assert path is not None, f"no witness path PENDING -> {state.value}"

    # ---- Claims 2 and 3: quantified over generated operation sequences ----
    _p8_over_sequences()


@SEQUENCE_SETTINGS
@given(scenario=_sequences())
def _p8_over_sequences(scenario: Tuple[Any, ...]) -> None:
    """Every persisted state is reachable, and every persisted transition is permitted."""
    start, operations, offsets, amount_minor, currency, base = scenario

    # Seeding is itself legitimate only because the start state is reachable from PENDING; the
    # witness is asserted so the property never quantifies from an unreachable state.
    assert start in REACHABLE_FROM_PENDING
    journal = _run_sequence(start, operations, offsets, amount_minor, currency, base)

    reachable_texts = {STATUS_TEXT_FOR_STATE[state] for state in REACHABLE_FROM_PENDING}

    for index, step in enumerate(journal.steps):
        assert step.after_status in reachable_texts, (
            f"step {index} ({step.operation}) persisted status {step.after_status!r}, which "
            f"is not reachable from PENDING. Sequence: {journal.trace()}"
        )
        if not step.moved:
            # A no-op. The in-place renewal lives here: same status, extended period.
            continue
        assert step.status_pair in PERMITTED_STATUS_PAIRS, (
            f"step {index} ({step.operation}) persisted the transition {step.status_pair}, "
            f"which is not one of the {len(PERMITTED_STATUS_PAIRS)} permitted pairs. "
            f"Sequence: {journal.trace()}"
        )

    # Every history row records a permitted pair, or the in-place renewal - which is exactly
    # how a reader tells a period extension from a transition (Requirement 11.12).
    for pair in journal.client.transition_pairs():
        assert (
            pair in PERMITTED_STATUS_PAIRS or pair == IN_PLACE_RENEWAL_STATUS_PAIR
        ), (
            f"library_subscription_transitions records {pair}, which is neither a permitted "
            f"transition nor the active -> active in-place renewal. "
            f"Sequence: {journal.trace()}"
        )


# ══════════════════════════════════════════════════════════════════════════
# P-9
# ══════════════════════════════════════════════════════════════════════════

# Feature: marketplace-subscriptions-paper-trading, Property 9 (invariant, illegal transition
# rejection): for all pairs of Subscription_States (s1, s2) not present in the permitted
# transition set, attempting s1 -> s2 leaves the stored state at s1 and returns an error.
#
# **Validates: Requirements 11.3**


@PROPERTY_SETTINGS
@given(
    pair=_absent_state_pairs(),
    amount_minor=minor_amounts(1, 10_000_000),
    currency=st.sampled_from(("USD", "INR")),
    base=utc_instants(2020, 2026),
)
def test_p9_illegal_subscription_transition_leaves_state(
    pair: StatePair, amount_minor: int, currency: str, base: datetime
) -> None:
    """An absent pair is refused, and the stored state, start and expiry are untouched.

    For every one of the 36 ordered pairs absent from the effective thirteen (excluding the
    ``active -> active`` in-place renewal, which is not a transition):

    * the application gate ``can_transition`` answers ``False``, which is the refusal every
      caller of the gate branches on; and
    * where a sanctioned writer can *target* ``s2`` at all, driving that writer against a row
      stored at ``s1`` leaves the stored status, ``period_start`` and ``period_expiry``
      exactly as they were, reports no transition into ``s2``, writes no
      ``library_subscription_transitions`` row for the pair, and grants no entitlement.

    Two of the seven states are the destination of no write in ``settlement_service``,
    ``expiry_sweep`` or the cancel route, so no attempt can be constructed for them through a
    sanctioned path; that is asserted as a fact about the writers rather than skipped. A
    *direct* Persistence_Layer UPDATE to any absent pair is refused by
    ``trg_subscription_transition_guard``, which is
    ``test_p49_database_refuses_illegal_transitions``'s statement and is not repeated here.

    Note what a refused *transition* does not undo: a reversal that cannot move the
    Subscription still leaves its Settlement_Record on the ledger, because the money did move
    and Requirement 10.8 forbids removing it. That is a ledger fact, not a state change, and
    P-15 is where it is asserted.

    **Validates: Requirements 11.3**
    """
    source, target = pair
    assert pair not in PERMITTED_PAIRS
    assert pair != IN_PLACE_RENEWAL

    # The application gate. Unrecognised-or-absent is False, and every caller branches on it.
    assert can_transition(source, target) is False, (
        f"can_transition({source.value}, {target.value}) is True, but the pair is absent from "
        f"the {len(PERMITTED_PAIRS)} pairs the Persistence_Layer admits"
    )

    if target not in ATTEMPTABLE_TARGETS:
        # PENDING, PAYMENT_FAILED and SUSPENDED. Recorded, not skipped: the set is pinned by
        # name in test_the_attempt_space_is_the_three_writers_this_file_drives, so a writer
        # that starts producing one of them changes that test rather than slipping past this
        # one, and the pair's refusal by a direct UPDATE is P-49's statement.
        assert target in NO_SANCTIONED_WRITER_HERE
        return

    journal = _Journal(
        start=source, amount_minor=amount_minor, currency=currency, base=base
    )
    before_status = journal.client.subscription_status()
    before_period = journal.client.period()
    before_transitions = journal.client.transition_pairs()
    assert before_status == STATUS_TEXT_FOR_STATE[source]

    if target is SubscriptionState.ACTIVE:
        # A confirmed payment. ``settle`` consults ELIGIBLE_FOR_ACTIVATION and refuses to move
        # a row whose status has no edge into ``active``.
        outcome = _run_coroutine(journal._renew(base))
        reported = getattr(outcome, "to_status", None)
    elif target is SubscriptionState.REFUNDED:
        # A reversal. ``_apply_transition`` consults ``can_transition`` for a reversal, so an
        # absent pair makes no state change. The ledger row is written and stays written.
        _run_coroutine(journal._renew(base))
        # Put the row back where the pair says it is - the payment above may have activated it -
        # so the reversal is attempted from ``source`` and from nowhere else.
        journal.client.subscription()["status"] = STATUS_TEXT_FOR_STATE[source]
        journal.client.subscription()["period_start"] = before_period[0]
        journal.client.subscription()["period_expiry"] = before_period[1]
        journal.grant.calls.clear()
        outcome = _run_coroutine(journal._refund(base))
        reported = getattr(outcome, "to_status", None)
    elif target is SubscriptionState.EXPIRED:
        # The real sweep, run well past any stored expiry. Its predicate admits only the two
        # states with a seeded edge into ``expired``.
        outcome = _run_coroutine(journal._expire(base + timedelta(days=400)))
        assert outcome.expired == (), (
            f"the expiry sweep expired a Subscription stored at {source.value}, which has no "
            f"permitted edge to expired"
        )
        reported = None
    else:
        assert target is SubscriptionState.CANCELLED
        outcome = journal._cancel(base)
        matched = getattr(outcome, "data", None) or []
        assert not matched, (
            f"the cancel statement matched a row stored at {source.value}; its optimistic "
            f".eq('status','active') guard is what makes cancelling a no-op from every other "
            f"state"
        )
        reported = None

    after_status = journal.client.subscription_status()
    after_period = journal.client.period()

    assert after_status == STATUS_TEXT_FOR_STATE[source], (
        f"the attempt {source.value} -> {target.value} moved the stored state to "
        f"{after_status!r}; Requirement 11.3 requires it to stay at "
        f"{STATUS_TEXT_FOR_STATE[source]!r}"
    )
    assert after_period == before_period, (
        f"the attempt {source.value} -> {target.value} changed the stored period from "
        f"{before_period} to {after_period}; Requirement 11.3 leaves both boundaries unchanged"
    )
    assert reported != STATUS_TEXT_FOR_STATE[target], (
        f"the writer reported a transition into {target.value} that it did not make"
    )
    assert reported is None, (
        f"the writer reported to_status={reported!r} for the refused pair "
        f"{source.value} -> {target.value}; no transition happened, so nothing may be reported"
    )
    new_pairs = journal.client.transition_pairs()[len(before_transitions):]
    rejected_pair = (STATUS_TEXT_FOR_STATE[source], STATUS_TEXT_FOR_STATE[target])
    assert rejected_pair not in new_pairs, (
        f"a library_subscription_transitions row records the refused pair {rejected_pair}"
    )
    assert journal.grant.calls == [], (
        f"an entitlement was granted for the refused pair "
        f"{source.value} -> {target.value}: {journal.grant.calls}"
    )
    # Nothing was deleted, and the append-only ledger was never updated.
    assert journal.client.destructive_statements() == []


# ══════════════════════════════════════════════════════════════════════════
# P-10
# ══════════════════════════════════════════════════════════════════════════

# Feature: marketplace-subscriptions-paper-trading, Property 10 (invariant, active period
# well-formedness): for all Subscriptions in state ACTIVE, the period start is non-null, the
# expiry is non-null, and expiry > start.
#
# **Validates: Requirements 11.13**


@PROPERTY_SETTINGS
@given(
    source=_activation_sources(),
    amount_minor=minor_amounts(1, 10_000_000),
    currency=st.sampled_from(("USD", "INR")),
    stored_start=utc_instants(2020, 2026),
    confirmation_instant=utc_instants(2020, 2026),
)
def test_p10_active_subscription_period_is_well_formed(
    source: SubscriptionState,
    amount_minor: int,
    currency: str,
    stored_start: datetime,
    confirmation_instant: datetime,
) -> None:
    """Every `ACTIVE` row a confirmed payment produces has a well-formed period.

    Quantified over all five of ``ELIGIBLE_FOR_ACTIVATION``'s sources - including the
    Requirement 11.6 ``payment_failed`` source - plus the ``active -> active`` in-place
    renewal, and over both period branches: a first activation with no stored expiry
    (``period_for_activation``, Requirement 11.4) and a renewal anchored on the stored one
    (``period_for_renewal``, Requirement 11.5). The confirmation instant is drawn
    independently of the stored start, so an early renewal and a late one are both reached.

    ``chk_ls_active_has_period`` states the same invariant in the database
    (``status <> 'active' OR (period_start IS NOT NULL AND period_expiry IS NOT NULL AND
    period_expiry > period_start))``); this is the service-side half, asserted on the rows the
    one writer that may set ``status='active'`` actually persists.

    **Validates: Requirements 11.13**
    """
    journal = _Journal(
        start=source,
        amount_minor=amount_minor,
        currency=currency,
        base=stored_start + timedelta(days=10),
    )
    # The period the row carried BEFORE the payment, read off the store rather than recomputed:
    # it is what decides the first-activation branch from the renewal branch.
    seeded_start = _as_instant(journal.client.subscription().get("period_start"))
    seeded_expiry = _as_instant(journal.client.subscription().get("period_expiry"))

    result = _run_coroutine(journal._renew(confirmation_instant))
    assert result.outcome in {
        SettlementOutcome.RECORDED,
        SettlementOutcome.REVERSED,
    }, f"a confirmed payment for {source.value} answered {result.outcome}"

    row = journal.client.subscription()
    status = _status_text(row.get("status"))
    assert status == _STATUS_ACTIVE, (
        f"a confirmed payment for a {source.value} Subscription left it at {status!r}; every "
        f"member of ELIGIBLE_FOR_ACTIVATION plus the in-place renewal reaches active"
    )

    start = _as_instant(row.get("period_start"))
    expiry = _as_instant(row.get("period_expiry"))
    assert start is not None, (
        f"an active Subscription has a null period_start (source {source.value}, confirmed "
        f"{confirmation_instant.isoformat()})"
    )
    assert expiry is not None, (
        f"an active Subscription has a null period_expiry (source {source.value}, confirmed "
        f"{confirmation_instant.isoformat()})"
    )
    assert expiry > start, (
        f"an active Subscription has expiry {expiry.isoformat()} which is not strictly after "
        f"start {start.isoformat()} (source {source.value})"
    )

    # The retained mirrors carry the same expiry, never the NULL check_deployment_permission
    # reads as perpetual access - the null-expiry privilege defect, restated as an invariant.
    assert _as_instant(row.get("expires_at")) == expiry, (
        f"the expires_at mirror is {row.get('expires_at')!r} rather than the period expiry "
        f"{expiry.isoformat()}"
    )

    # The boundaries agree with the period arithmetic, whichever branch was taken: a first
    # activation (Requirement 11.4) when the row carried no expiry, a renewal anchored on the
    # stored expiry (Requirement 11.5) when it did.
    if seeded_expiry is None:
        expected_start, expected_expiry = period_for_activation(confirmation_instant)
    else:
        expected_start = seeded_start
        expected_expiry = period_for_renewal(seeded_expiry, confirmation_instant)
    assert expiry == expected_expiry, (
        f"the persisted expiry {expiry.isoformat()} is not the one the period arithmetic "
        f"gives, {expected_expiry.isoformat()}"
    )
    assert start == expected_start

    # An entitlement was granted, and it carries the expiry rather than None.
    assert journal.grant.calls, "an activation granted no entitlement"
    for granted in journal.grant.expiries:
        assert granted is not None, "an entitlement was granted with a null expiry"
        assert _as_instant(granted) == expiry


@SEQUENCE_SETTINGS
@given(scenario=_sequences())
def test_the_active_period_invariant_survives_every_operation_sequence(
    scenario: Tuple[Any, ...]
) -> None:
    """P-10 held after each step of a generated cancel/expire/refund/renew sequence.

    The same invariant as :func:`test_p10_active_subscription_period_is_well_formed`, checked
    after *every* operation rather than after one payment - so an operation that leaves a row
    ``active`` while moving or clearing a period boundary is caught. Named without a
    ``test_p{n}_`` prefix on purpose: the coverage scoreboard counts one function per property,
    and this is the same property under a longer sequence.

    **Validates: Requirements 11.13**
    """
    start, operations, offsets, amount_minor, currency, base = scenario
    journal = _run_sequence(start, operations, offsets, amount_minor, currency, base)

    for index, step in enumerate(journal.steps):
        if step.after_status != _STATUS_ACTIVE:
            continue
        row_start = _as_instant(step.after_period[0])
        row_expiry = _as_instant(step.after_period[1])
        assert row_start is not None and row_expiry is not None, (
            f"after step {index} ({step.operation}) the Subscription is active with period "
            f"{step.after_period}; an active row has both boundaries. "
            f"Sequence: {journal.trace()}"
        )
        assert row_expiry > row_start, (
            f"after step {index} ({step.operation}) the active Subscription has expiry "
            f"{row_expiry.isoformat()} which is not strictly after start "
            f"{row_start.isoformat()}. Sequence: {journal.trace()}"
        )


# ══════════════════════════════════════════════════════════════════════════
# P-15
# ══════════════════════════════════════════════════════════════════════════

# Feature: marketplace-subscriptions-paper-trading, Property 15 (invariant, history
# preservation): for all generated sequences of cancel, expire, refund and renew operations,
# the count of persisted Subscription rows never decreases, and no Settlement_Record is
# removed.
#
# **Validates: Requirements 10.8, 11.11**


@SEQUENCE_SETTINGS
@given(scenario=_sequences())
def test_p15_subscription_and_settlement_history_never_shrinks(
    scenario: Tuple[Any, ...]
) -> None:
    """No cancel, expire, refund or renew ever removes history.

    Four claims, asserted after every step of the generated sequence:

    1. the count of persisted Subscription rows never decreases (Requirement 11.11 - a
       Subscription row is retained permanently and is not deleted on cancellation, expiry or
       refund);
    2. every Settlement_Record present before a step is still present after it, **byte for
       byte** - a reversal is an additional row with ``is_reversal = TRUE``, never an update to
       the row it reverses (Requirement 10.8);
    3. the ``library_subscription_transitions`` history only grows; and
    4. structurally, no ``DELETE`` was issued against any table and no ``UPDATE`` against
       ``marketplace_settlements`` or ``library_subscription_transitions`` - so claim 2 holds
       because the writers cannot express the mutation, not merely because these examples did
       not trigger it.

    Claim 2 is a subset check on identical row dictionaries rather than on counts, because a
    ledger that replaced a row with a different one of the same shape would keep the count
    unchanged while losing an owner's earning.

    **Validates: Requirements 10.8, 11.11**
    """
    start, operations, offsets, amount_minor, currency, base = scenario
    journal = _run_sequence(start, operations, offsets, amount_minor, currency, base)

    for index, step in enumerate(journal.steps):
        # 1. Subscription rows are retained.
        assert step.after_subscription_rows >= step.before_subscription_rows, (
            f"step {index} ({step.operation}) removed a Subscription row: "
            f"{step.before_subscription_rows} -> {step.after_subscription_rows}. "
            f"Sequence: {journal.trace()}"
        )
        assert step.after_subscription_rows >= 1, (
            f"step {index} ({step.operation}) left no Subscription row at all. "
            f"Sequence: {journal.trace()}"
        )

        # 2. Every Settlement_Record survives unchanged.
        for row in step.before_settlements:
            assert row in step.after_settlements, (
                f"step {index} ({step.operation}) removed or modified the Settlement_Record "
                f"{row.get('provider_reference')!r} "
                f"(is_reversal={row.get('is_reversal')!r}). A persisted Settlement_Record is "
                f"never updated or deleted; a reversal is a second row. "
                f"Sequence: {journal.trace()}"
            )
        assert len(step.after_settlements) >= len(step.before_settlements)

        # 3. The transition history only grows.
        assert step.after_transitions >= step.before_transitions, (
            f"step {index} ({step.operation}) removed a "
            f"library_subscription_transitions row. Sequence: {journal.trace()}"
        )

    # 4. The mutation is not merely absent from these examples - it was never expressed.
    assert journal.client.destructive_statements() == [], (
        f"the sequence issued {journal.client.destructive_statements()}; a Settlement_Record "
        f"and a transition row are append-only, and no Subscription row is ever deleted. "
        f"Sequence: {journal.trace()}"
    )

    # A refund is an ADDITIONAL row: the reversals and the payments coexist under
    # uq_settlement_reference_reversal (provider_reference, is_reversal).
    final = journal.client.settlement_snapshot()
    keys = [
        (row.get("provider_reference"), bool(row.get("is_reversal"))) for row in final
    ]
    assert len(keys) == len(set(keys)), (
        f"the ledger holds two rows for one (provider_reference, is_reversal) pair: {keys}"
    )
