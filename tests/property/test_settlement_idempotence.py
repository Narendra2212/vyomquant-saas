"""P-6: n deliveries of one provider reference are one delivery.

Feature: marketplace-subscriptions-paper-trading
Design reference: ``design.md § Property-to-test mapping`` ->
``P-6 | tests/property/test_settlement_idempotence.py | confirmations × repetition counts 1…8 |
the single-delivery end state``.

Module under test: ``backend_app/backend/marketplace/settlement_service.py`` (task 19.1).

Properties living here
----------------------
``test_p6_duplicate_confirmation_is_idempotent``  (task 19.6, Requirements 9.6, 9.7, 10.10)

Property 6 in full:

    For all generated payment confirmations and all repetition counts ``n >= 1``: applying the
    same provider transaction reference ``n`` times yields exactly one Settlement_Record and the
    same Subscription period expiry as applying it once.

    **Validates: Requirements 9.6, 9.7, 10.10**

THE ORACLE IS THE SINGLE-DELIVERY END STATE, NOT A RESTATED FORMULA
------------------------------------------------------------------
The design names the oracle: "the single-delivery end state". So every example runs the same
confirmation twice over — once against a fresh Persistence_Layer double, then ``n`` times against
a second fresh one — and compares the two end states row for row. Nothing about
``period_for_activation`` or ``period_for_renewal`` is re-derived here; the claim P-6 makes is an
*equality between two runs*, and re-implementing the calendar arithmetic in the test would
replace that equality with a paraphrase of the code under test. ``tests/property/
test_subscription_period.py`` (P-12, P-14) is where the calendar rule itself is checked, against
``dateutil.relativedelta``.

THE MONEY-RELEVANT HALF, AND WHY THE EQUALITY IS NOT VACUOUS
-----------------------------------------------------------
"Exactly one Settlement_Record" is the ledger half. The half that costs money is "the period is
extended at most once": a Subscription that took ``n`` free months from ``n`` redeliveries of one
webhook is a paid-once, entitled-``n``-times defect, and it is invisible in the ledger because the
ledger row is the thing that was refused.

An equality of the form "n deliveries leave the expiry where one delivery left it" passes
trivially against a double that never extends anything, so the property also asserts the
*control*: the same ``n`` confirmations under ``n`` DISTINCT provider references DO advance the
expiry strictly past the single-delivery expiry. Both halves are needed. Together they say the
deduplication is keyed on the provider reference and on nothing else — which is exactly what
``uq_settlement_reference_reversal UNIQUE (provider_reference, is_reversal)`` says.

THE DOUBLE ENFORCES THE CONSTRAINT, AND THAT IS ASSERTED SEPARATELY
------------------------------------------------------------------
``FakeSupabase`` from ``tests/test_settlement_service.py`` is reused rather than replaced - it is
already the double the unit suite for this module drives, and a third one would be a second
opinion about what PostgREST does. What matters for P-6 is that it *refuses* a second insert of
the same ``(provider_reference, is_reversal)`` pair: against a double that accepted every insert
this whole file would pass against a module with no idempotency at all.
``test_the_double_enforces_the_settlement_reference_unique_constraint`` pins that directly,
including that ``settlement_service._is_duplicate_reference`` reads the raised error as the
duplicate it is, and that the reversal half of the key still admits a refund carrying the
refunded payment's own reference.

THE WALL CLOCK MOVES AND THE PERIOD DOES NOT (Requirements 11.4, 11.5)
---------------------------------------------------------------------
A redelivery arrives later than the payment - minutes, or hours after a Redis key expired. The
period boundaries derive from the provider's own ``confirmation_instant``, never from a clock
read, so a redelivery an hour later derives the same two boundaries. The property asserts that
rather than assuming it: ``settlement_service._utc_now`` is the module's only clock read, and each
delivery in the ``n``-delivery run is served a DIFFERENT frozen instant from the one the
single-delivery run saw. The observable consequence is visible on the entitlement row -
``deployment_permissions.granted_at`` (a timestamp, which does read the clock) differs between the
two runs while ``expires_at`` (the period expiry) is identical. A period derived from a clock read
would move with ``granted_at``; this one does not.

That is also why the module's own ``grant_deployment_permission`` does the writing here rather
than a stub: it is the function that reads the clock, and its ``expires_at`` is the value the
whole entitlement path turns on. ``RecordingGrant`` is subclassed to count the calls, so the
"exactly one entitlement" claim and the real write are the same event.

EVERY STARTING STATE, NOT JUST A FIRST ACTIVATION
-------------------------------------------------
Idempotence has to hold on every path into ``active``, so the generator draws the starting
``library_subscriptions.status`` from all five members of
``settlement_service.ELIGIBLE_FOR_ACTIVATION`` - ``pending``, ``expired``, ``cancelled``,
``suspended``, ``payment_failed`` (the last by the module docstring's requirements decision 2,
Requirement 11.6 over Requirement 11.2) - plus ``active``, the in-place renewal of requirements
decision 1, where a second extension would be the free month. ``pending`` carries no stored
period (checkout writes none); ``active`` always carries one (``chk_ls_active_has_period`` makes
an ``ACTIVE`` row without a period unrepresentable); the other four are drawn both ways, because a
``payment_failed`` row whose first payment never confirmed has no period either.

INTEGER MINOR_UNITS ONLY
------------------------
Every amount here is an ``int`` number of Minor_Units drawn from ``minor_amounts``, floored at 1
by Requirement 9.2. No ``float`` appears in this file, in the arithmetic or in the generators -
the module under test contains none by construction (``tests/test_settlement_service.py`` asserts
that with an AST walk) and a test that introduced one would be asserting against a value the
production path cannot produce.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, NamedTuple, Optional, Tuple

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.marketplace import settlement_service as ss
from backend_app.backend.marketplace.settlement_service import (
    ELIGIBLE_FOR_ACTIVATION,
    SETTLEMENT_REFERENCE_UNIQUE_CONSTRAINT,
    SettlementOutcome,
    grant_deployment_permission,
    settle,
)

# The Persistence_Layer double, the audit recorder, the entitlement recorder and the loop runner
# all already exist for this module's unit suite. Imported rather than re-derived so both suites
# read the settlement path through one double that enforces one constraint.
from tests.test_settlement_service import (
    PROVIDER,
    FakeSupabase,
    FakeUniqueViolation,
    RecordingAuditLogger,
    RecordingGrant,
    _run_coroutine,
    _subscription_row,
)
from tests.strategies.marketplace_generators import minor_amounts, utc_instants

#: The configuration ``design.md § Property-based testing configuration`` prescribes for every
#: property test in this plan: at least 100 examples and no per-example deadline. The deadline is
#: disabled rather than raised because one example drives up to seventeen ``await settle(...)``
#: calls, each on its own event loop, and the first example additionally pays the import cost of
#: the audit stack - a per-example wall-clock budget would flag that as a failure of the property
#: rather than of the machine.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

#: Every ``library_subscriptions.status`` a confirmed non-reversal payment may act on: the five
#: of ``ELIGIBLE_FOR_ACTIVATION`` plus ``active``, the in-place renewal. Derived from the
#: production constant, so a sixth eligible status added later is generated here automatically
#: instead of silently escaping the property.
SETTLEABLE_START_STATUSES: Tuple[str, ...] = tuple(
    sorted(set(ELIGIBLE_FOR_ACTIVATION) | {"active"})
)

#: The reference every delivery in the idempotent run carries. One value, delivered n times.
REDELIVERED_REFERENCE = "pi_redelivered_abc123"

#: How far apart the stored period boundary may sit from the confirmation instant, in days. Both
#: signs: a renewal confirmed before the current expiry (the auto-renew case) and one confirmed
#: after it (the lapsed case) take different branches of ``period_for_renewal``.
_STORED_PERIOD_OFFSET_DAYS = 400


# ---------------------------------------------------------------------------
# Installing the recorders without a pytest fixture
# ---------------------------------------------------------------------------
#
# Hypothesis re-runs the test body many times inside ONE pytest invocation, so a
# function-scoped ``monkeypatch`` fixture would be set up once and shared by every example -
# which Hypothesis reports as a health-check failure and which would leak one example's audit
# recorder into the next. Both patches are therefore scoped to the block that needs them.


@contextmanager
def _recording_audit(client: FakeSupabase) -> Iterator[RecordingAuditLogger]:
    """Install a :class:`RecordingAuditLogger` for the duration of the block.

    ``settlement_service._write_audit`` imports ``get_strategy_audit_logger`` from
    ``backend_app.core.audit_trail`` lazily at call time, so replacing the module attribute is
    enough - and the real ``StrategyAuditAction`` enum still resolves the action names, which is
    what proves ``MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED`` exists rather than assuming it.
    """
    from backend_app.core import audit_trail

    recorder = RecordingAuditLogger(client)
    previous = audit_trail.get_strategy_audit_logger
    audit_trail.get_strategy_audit_logger = lambda: recorder  # type: ignore[assignment]
    try:
        yield recorder
    finally:
        audit_trail.get_strategy_audit_logger = previous  # type: ignore[assignment]


@contextmanager
def _frozen_wall_clock(instant: datetime) -> Iterator[None]:
    """Freeze ``settlement_service._utc_now`` at ``instant`` for the duration of the block.

    ``_utc_now`` is the module's only clock read - its own docstring says so, and "no period uses
    it". Freezing it at a generated instant is how that claim is checked rather than trusted: a
    delivery served a different wall clock must still derive the same two period boundaries from
    the provider's ``confirmation_instant``.
    """
    previous = ss._utc_now
    ss._utc_now = lambda: instant  # type: ignore[assignment]
    try:
        yield
    finally:
        ss._utc_now = previous  # type: ignore[assignment]


class _CountingGrant(RecordingGrant):
    """``RecordingGrant``'s counter in front of the module's REAL entitlement writer.

    The count is what "exactly one entitlement per provider reference" is asserted on; the real
    write is what puts a ``deployment_permissions`` row - carrying both ``granted_at`` (a clock
    read) and ``expires_at`` (the period expiry) - in the double, which is where the wall-clock
    half of this property is observed. A stub would have recorded the count and lost the row.
    """

    def __call__(self, supabase: Any, **kwargs: Any) -> Optional[str]:
        super().__call__(supabase, **kwargs)
        return grant_deployment_permission(supabase, **kwargs)


# ---------------------------------------------------------------------------
# The generated scenario
# ---------------------------------------------------------------------------


class _Scenario(NamedTuple):
    """One confirmed payment, the Subscription it lands on, and how often it is delivered."""

    row: Dict[str, Any]
    status: str
    amount_minor: int
    currency: str
    instant: datetime
    deliveries: int
    skew: timedelta


@st.composite
def redelivery_scenarios(draw: Any) -> _Scenario:
    """A ``library_subscriptions`` row, a matching confirmation, and a repetition count 1…8.

    The row's ``price_minor`` and ``currency`` always AGREE with the confirmation: a disagreement
    is Requirement 9.14's mismatch guard, which writes nothing at all, so it has no end state for
    P-6 to be idempotent about (``tests/test_settlement_service.py`` owns that case). What varies
    is the starting status, the presence and sign of a stored period, the amount, the currency,
    the confirmation instant and how many times the webhook is delivered.
    """
    status = draw(st.sampled_from(SETTLEABLE_START_STATUSES))
    instant = draw(utc_instants())
    amount_minor = draw(minor_amounts(min_value=1))
    currency = draw(st.sampled_from(("USD", "INR")))
    deliveries = draw(st.integers(min_value=1, max_value=8))
    # At least an hour, so the redelivery's wall clock is never accidentally equal to the
    # single-delivery run's and the ``granted_at`` inequality below is a real observation.
    skew = timedelta(seconds=draw(st.integers(min_value=3600, max_value=86400)))

    if status == "pending":
        # Checkout writes no period, so a first activation has none to extend.
        stored_expiry: Optional[datetime] = None
    else:
        offset_days = draw(
            st.integers(
                min_value=-_STORED_PERIOD_OFFSET_DAYS, max_value=_STORED_PERIOD_OFFSET_DAYS
            )
        )
        # ``chk_ls_active_has_period``: an ``active`` row without a period is unrepresentable.
        carries_period = True if status == "active" else draw(st.booleans())
        stored_expiry = instant + timedelta(days=offset_days) if carries_period else None

    row = _subscription_row(
        status=status,
        price_minor=amount_minor,
        currency=currency,
        period_start=(
            (stored_expiry - timedelta(days=31)).isoformat() if stored_expiry else None
        ),
        period_expiry=stored_expiry.isoformat() if stored_expiry else None,
    )
    return _Scenario(
        row=row,
        status=status,
        amount_minor=amount_minor,
        currency=currency,
        instant=instant,
        deliveries=deliveries,
        skew=skew,
    )


# ---------------------------------------------------------------------------
# Applying deliveries
# ---------------------------------------------------------------------------


class _Run(NamedTuple):
    """Everything one run of deliveries left behind."""

    client: FakeSupabase
    audit: RecordingAuditLogger
    grant: _CountingGrant
    outcomes: List[Any]


def _apply(scenario: _Scenario, *, references: List[str], clocks: List[datetime]) -> _Run:
    """Deliver one confirmation once per entry of ``references`` against a FRESH double.

    ``references`` and ``clocks`` are the same length: entry ``i`` is delivery ``i``'s provider
    reference and the wall clock the module sees while it runs. A fresh :class:`FakeSupabase` per
    run is what makes the comparison between runs a comparison of end states rather than of one
    accumulating table.
    """
    assert len(references) == len(clocks)
    client = FakeSupabase(subscriptions=[dict(scenario.row)])
    grant = _CountingGrant(client)
    outcomes: List[Any] = []
    with _recording_audit(client) as audit:
        for reference, clock in zip(references, clocks):
            with _frozen_wall_clock(clock):
                outcomes.append(
                    _run_coroutine(
                        settle(
                            provider_reference=reference,
                            provider=PROVIDER,
                            amount_minor=scenario.amount_minor,
                            currency=scenario.currency,
                            subscription_id=str(scenario.row["id"]),
                            confirmation_instant=scenario.instant,
                            supabase=client,
                            grant_permission=grant,
                        )
                    )
                )
    return _Run(client=client, audit=audit, grant=grant, outcomes=outcomes)


def _without(rows: List[Dict[str, Any]], *volatile: str) -> List[Dict[str, Any]]:
    """``rows`` with the named columns dropped.

    Two columns are volatile by design and carry no claim: the Settlement_Record's ``id`` is a
    fresh ``uuid4`` per insert, and the entitlement row's ``granted_at`` is a clock read, which
    this property deliberately moves. Everything else is compared.
    """
    dropped = set(volatile)
    return [{k: v for k, v in row.items() if k not in dropped} for row in rows]


# ---------------------------------------------------------------------------
# The double really refuses the second row (without this, P-6 is vacuous)
# ---------------------------------------------------------------------------


def test_the_double_enforces_the_settlement_reference_unique_constraint() -> None:
    """``FakeSupabase`` refuses a second insert of one ``(provider_reference, is_reversal)`` pair.

    Not a property - a precondition for the property below being worth anything. Three facts:

    1. a second insert of the same pair RAISES rather than appending a second row;
    2. ``settlement_service._is_duplicate_reference`` reads that error as the duplicate it is,
       which is the reading the ``DUPLICATE_IGNORED`` outcome depends on; and
    3. the reversal half of the key still admits a refund carrying the refunded payment's own
       reference - the coexistence ``uq_settlement_reference_reversal`` is on
       ``(provider_reference, is_reversal)`` for, and not merely on ``provider_reference``.
    """
    client = FakeSupabase(subscriptions=[])

    def _row(*, is_reversal: bool) -> Dict[str, Any]:
        return {
            "id": f"settlement-{is_reversal}",
            "provider_reference": REDELIVERED_REFERENCE,
            "is_reversal": is_reversal,
            "amount_minor": 1999,
            "owner_share_minor": 1799,
            "platform_fee_minor": 200,
        }

    client.table(ss.SETTLEMENT_TABLE).insert(_row(is_reversal=False)).execute()
    assert len(client.settlements) == 1

    try:
        client.table(ss.SETTLEMENT_TABLE).insert(_row(is_reversal=False)).execute()
    except FakeUniqueViolation as exc:
        assert ss._is_duplicate_reference(exc), (
            "the double raised a unique violation the module does not recognise as a duplicate "
            "settlement reference, so DUPLICATE_IGNORED would never be reached"
        )
    else:  # pragma: no cover - the failure message is the point
        raise AssertionError(
            "the double accepted a second row for one (provider_reference, is_reversal) pair, so "
            f"{SETTLEMENT_REFERENCE_UNIQUE_CONSTRAINT} is not enforced and every idempotence "
            "assertion in this module would pass vacuously"
        )
    assert len(client.settlements) == 1

    # The reversal half of the key: a refund of that payment carries the SAME reference and must
    # still be insertable, or Requirement 10.8's additional reversal row would be impossible.
    client.table(ss.SETTLEMENT_TABLE).insert(_row(is_reversal=True)).execute()
    assert len(client.settlements) == 2


# ---------------------------------------------------------------------------
# P-6
# ---------------------------------------------------------------------------


# Feature: marketplace-subscriptions-paper-trading, Property 6 (idempotence, duplicate webhook):
# For all generated payment confirmations and all repetition counts n >= 1: applying the same
# provider transaction reference n times yields exactly one Settlement_Record and the same
# Subscription period expiry as applying it once.
@PROPERTY_SETTINGS
@given(scenario=redelivery_scenarios())
def test_p6_duplicate_confirmation_is_idempotent(scenario: _Scenario) -> None:
    """``n`` deliveries of one provider reference are one delivery, in the ledger and the period.

    **Validates: Requirements 9.6, 9.7, 10.10**
    """
    n = scenario.deliveries

    # Run A: the oracle. One delivery, one wall clock.
    baseline_clock = scenario.instant
    once = _apply(
        scenario, references=[REDELIVERED_REFERENCE], clocks=[baseline_clock]
    )

    # Run B: the same reference n times, each delivery served a DIFFERENT wall clock, none of
    # them equal to run A's. A redelivery arrives later than the payment it repeats.
    many = _apply(
        scenario,
        references=[REDELIVERED_REFERENCE] * n,
        clocks=[baseline_clock + scenario.skew * (i + 1) for i in range(n)],
    )

    # ── Claim 1 - exactly ONE Settlement_Record, and it is the one delivery 1 wrote. ──
    assert len(many.client.settlements) == 1, (
        f"{n} deliveries of {REDELIVERED_REFERENCE!r} left "
        f"{len(many.client.settlements)} settlement records"
    )
    assert _without(many.client.settlements, "id") == _without(
        once.client.settlements, "id"
    ), "the ledger row after n deliveries is not the row a single delivery wrote"

    # ── Claim 2 - the first delivery records; every later one is an ordinary no-op outcome. ──
    assert many.outcomes[0].outcome is SettlementOutcome.RECORDED
    assert many.outcomes[0].period_written is True
    for index, later in enumerate(many.outcomes[1:], start=2):
        assert later.outcome is SettlementOutcome.DUPLICATE_IGNORED, (
            f"delivery {index} of {n} returned {later.outcome}; a redelivery must be an "
            "ordinary DUPLICATE_IGNORED outcome, not an exception and not a second record"
        )
        assert later.period_written is False, f"delivery {index} of {n} extended a period"

    # ── Claim 3 - the money-relevant half: the SAME period expiry as applying it once. ──
    stored_once = once.client.subscription(str(scenario.row["id"]))
    stored_many = many.client.subscription(str(scenario.row["id"]))
    assert stored_many["period_expiry"] == stored_once["period_expiry"], (
        f"{n} deliveries of one reference moved the expiry from "
        f"{stored_once['period_expiry']} to {stored_many['period_expiry']}"
    )
    assert stored_many == stored_once, (
        "the Subscription end state after n deliveries differs from the single-delivery end state"
    )
    assert many.outcomes[0].period_expiry == once.outcomes[0].period_expiry

    # ── Claim 4 - one history row (Requirement 11.12), identical to the single delivery's. ──
    assert many.client.transitions == once.client.transitions
    assert len(many.client.transitions) == 1

    # ── Claim 5 - one entitlement, with the same expiry, under a wall clock that MOVED. ──
    assert len(many.grant.calls) == 1, (
        f"{n} deliveries granted {len(many.grant.calls)} entitlements"
    )
    assert len(many.client.permissions) == 1
    assert _without(many.client.permissions, "id", "granted_at") == _without(
        once.client.permissions, "id", "granted_at"
    ), "the entitlement row after n deliveries differs from the single delivery's"
    assert (
        many.client.permissions[0]["expires_at"] == once.client.permissions[0]["expires_at"]
    ), "the entitlement expiry moved with the wall clock instead of with the period"
    assert (
        many.client.permissions[0]["granted_at"] != once.client.permissions[0]["granted_at"]
    ), (
        "the wall clock did not actually differ between the two runs, so the claim that the "
        "period does not follow it was not exercised"
    )

    # ── Claim 6 - one created audit, and exactly n - 1 duplicate-ignored audits (Req 10.10). ──
    names = many.audit.action_names()
    assert names.count("MARKETPLACE_SETTLEMENT_CREATED") == 1
    assert names.count("MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED") == n - 1, (
        f"{n} deliveries produced "
        f"{names.count('MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED')} duplicate-ignored audit "
        f"entries; expected exactly {n - 1}"
    )
    if n > 1:
        duplicate_metadata = many.audit.metadata_for(
            "MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED"
        )
        assert duplicate_metadata["provider_reference"] == REDELIVERED_REFERENCE
        assert duplicate_metadata["constraint"] == SETTLEMENT_REFERENCE_UNIQUE_CONSTRAINT

    # ── Claim 7 - the control. The equality above is keyed on the REFERENCE, not on the number
    #    of deliveries: n confirmations under n DISTINCT references record n rows and advance the
    #    expiry strictly past the single-delivery expiry. Without this, a double that extended
    #    nothing at all would satisfy claims 1 through 6. ──
    if n > 1:
        distinct = _apply(
            scenario,
            references=[f"{REDELIVERED_REFERENCE}-{i}" for i in range(n)],
            clocks=[baseline_clock] * n,
        )
        assert len(distinct.client.settlements) == n, (
            f"{n} DISTINCT provider references recorded "
            f"{len(distinct.client.settlements)} settlement records; the double is "
            "deduplicating something other than the provider reference"
        )
        assert all(
            outcome.outcome is SettlementOutcome.RECORDED for outcome in distinct.outcomes
        )
        # Parsed, not string-compared: both values are ``isoformat()`` output and Python omits a
        # zero microsecond field, so two equally-formatted-looking timestamps are not always
        # lexicographically ordered the way the instants are.
        distinct_expiry = ss._parse_instant(
            distinct.client.subscription(str(scenario.row["id"]))["period_expiry"]
        )
        once_expiry = ss._parse_instant(stored_once["period_expiry"])
        assert distinct_expiry is not None and once_expiry is not None
        assert distinct_expiry > once_expiry, (
            f"{n} distinct payments did not extend the period past the single payment's expiry "
            f"({distinct_expiry.isoformat()} vs {once_expiry.isoformat()}), so the idempotence "
            "equality above holds for the wrong reason"
        )
        assert (
            distinct.audit.action_names().count("MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED") == 0
        )
