"""
backend_app/backend/marketplace/settlement_service.py - the one Settlement_Record writer.

Spec: marketplace-subscriptions-paper-trading task 19.1. ``design.md`` ->
"``marketplace/settlement_service.py``". Requirements 9.5, 9.6, 9.14, 10.1, 10.2, 10.3, 10.4,
10.8, 10.9, 10.10, 10.11, 11.4, 11.5, 11.6, 11.12, 24.6.

Exposes
-------
SETTLEMENT_SUBSCRIPTION_SELECT  the explicit Subscription projection this path reads
SETTLEMENT_EARNINGS_SELECT      the explicit projection the earnings read requests (task 21.1)
EarningsTotals                  one currency's totals over an owner's Settlement_Records
creator_earnings(...)           the per-currency earnings read (Requirements 10.6, 10.7)
MAX_SETTLEMENT_ATTEMPTS         5 - Requirement 10.11's attempt budget
SETTLEMENT_RETRY_WINDOW_SECONDS 60 - Requirement 10.11's window
SETTLEMENT_BACKOFF_SECONDS      the bounded backoff schedule, integer seconds
ELIGIBLE_FOR_ACTIVATION         the five statuses a confirmed payment may move to ``active``
SettlementOutcome               the six terminal outcomes of one :func:`settle` call
SettlementResult                the frozen value type :func:`settle` returns
SettlementPersistenceError      one attempt's read or write did not complete - retried
SettlementPersistFailed         every attempt failed; the ledger is unchanged (Req 10.11)
grant_deployment_permission     the entitlement write; ``expires_at`` is a REQUIRED keyword and
                                may not be ``None``. Since task 19.3 this is the codebase's only
                                ``grant_deployment_permission``
settle(...)                     the single entry point

WHY THIS MODULE IS THE ONLY PATH INTO ``ACTIVE``
------------------------------------------------
Requirement 11.6 makes a confirmed payment the precondition for reaching ``ACTIVE``, and
``trg_subscription_transition_guard`` enforces it in the database independently of any handler
(Requirement 11.14). The replaced ``renew_subscription`` in ``routers/library.py`` set
``status='active'``, cleared ``expires_at`` and granted deployment permission with **no payment
at all** - which, through ``check_deployment_permission``'s "no expiry date means perpetual
subscription" branch, converted a cancelled subscription into unlimited free access. Task 19.3
deleted that path; this module is what replaced it, and it is the only place in the codebase
that writes ``status='active'`` on ``library_subscriptions``.

It is also the only place a Settlement_Record is written, which is what makes Requirement 10.5's
"every payment produces exactly one Settlement_Record" a statement about one function rather
than about a convention.

THE 90/10 SPLIT IS NOT RECOMPUTED HERE
--------------------------------------
:func:`money.split_ninety_ten` is the one implementation (Requirements 10.1, 10.2, 10.3), and
this module calls it rather than re-deriving ``amount * 90 // 100``. Two reasons that matter:
``owner_share + platform_fee == amount`` holds *by construction* there, because the fee is the
residual rather than a second percentage computation, and the whole admissible domain is already
property-tested against that module. A second copy of the arithmetic here would be a second
rounding policy nobody chose.

**No ``float`` appears anywhere in this module.** Every money value is a Python ``int`` number of
Minor_Units; every timeout and backoff constant is an ``int`` number of seconds. ``money`` refuses
a ``float`` amount on the way in, so an inexact upstream value is a refusal rather than a silently
laundered ledger row. ``tests/test_settlement_service.py`` asserts the absence structurally with
an AST walk, the same way task 18.1's suite does for ``checkout_service``.

THE GUARDS COME FIRST, AND THEY WRITE NOTHING (Requirement 9.14)
----------------------------------------------------------------
Two conditions make **no** transition, write **no** Settlement_Record and grant **no**
entitlement:

* **Unmatched.** No ``library_subscriptions`` row for the ``subscription_id`` the provider
  metadata carried. There is nothing to correlate the payment to, so nothing is recorded against
  a guess. ``MARKETPLACE_SETTLEMENT_UNMATCHED``.
* **Mismatched.** The confirmed ``amount_minor`` or ``currency`` differs from what the
  Subscription recorded at checkout. This is the guard against a confirmation for an amount
  nobody agreed to: a webhook body is attacker-influenced in exactly this field, and recording
  what it claims would pay an owner 90 percent of a number this system never quoted.
  ``MARKETPLACE_SETTLEMENT_MISMATCHED``.

Both are ordinary outcomes, not exceptions: the audit line **is** the response, and the caller
(the webhook) has nothing to retry. A partial refund lands here too - see
:data:`SettlementOutcome.MISMATCHED`'s note.

IDEMPOTENCY IS THE DATABASE'S, NOT REDIS'S (Requirements 9.6, 9.7, 10.10, P-6)
------------------------------------------------------------------------------
``uq_settlement_reference_reversal UNIQUE (provider_reference, is_reversal)`` is the correctness
path. The two-phase Redis lock in ``routers/billing.py``'s webhooks is the *fast* path and stays
exactly as it is - but a Redis flush, an expired lock or a redelivery from a second process
bypasses it, and at that point the constraint is the only thing standing between one payment and
two period extensions. So a ``UniqueViolation`` on that constraint is a **no-op plus
``MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED``**, not an error: ``n >= 1`` deliveries of one
provider reference leave exactly one row and one expiry, which is property P-6. The same division
of responsibility ``005b_signal_lifecycle_and_idempotency.sql`` documents for
``uq_signals_idempotency_key``.

There is one case where a unique violation is **not** a duplicate delivery, and it is handled
separately: a retry of *this* call, after an earlier attempt's insert committed but a later step
failed. :class:`_Attempt` remembers that the insert was issued, so the retry recognises the row as
its own and resumes at the transition instead of reporting a duplicate and abandoning an activated
payment. Getting that wrong is a paid-but-no-access defect, so it is state on the attempt rather
than an inference from the error text.

WHY ``SELECT … FOR UPDATE`` IS AN OPTIMISTIC GUARD HERE (design.md's pascal)
---------------------------------------------------------------------------
PostgREST has no ``FOR UPDATE``, and ``supabase-py`` exposes no transaction handle. The row lock
the design draws is held exactly the way ``submission_service.apply_admin_action`` already holds
it, so there is one technique in this package rather than two:

* the read selects only the columns this path needs, inside the same logical step; and
* **every** UPDATE carries ``.eq("status", <the status that was read>)``, so a concurrent writer
  that already moved the row makes this UPDATE match zero rows. A no-op UPDATE is detected and
  reported, never treated as success - which is what turns a lost update into an observable
  outcome instead of a second period extension.

``trg_subscription_transition_guard`` re-checks the edge and the settlement precondition in the
database regardless, and ``chk_ls_active_has_period`` makes an ``ACTIVE`` row without a period
unrepresentable. The application is the fast gate; the database is the arbiter.

"ALL OF IT OR NONE OF IT" WITHOUT A TRANSACTION HANDLE (Requirement 9.5)
-----------------------------------------------------------------------
The same honest boundary ``submission_service`` records. What is actually guaranteed:

1. The Settlement_Record insert is one statement, and the ledger row is the *first* thing
   written - so the money is recorded before any entitlement is granted, never after.
2. Every subsequent step is retried, in place, until it succeeds or the Requirement 10.11 budget
   is exhausted: the transition UPDATE, the ``library_subscription_transitions`` row, the
   entitlement grant and the ``MARKETPLACE_SETTLEMENT_CREATED`` audit. A retry resumes at the
   first incomplete step rather than restarting, so no step runs twice.
3. On total failure ``MARKETPLACE_SETTLEMENT_PERSIST_FAILED`` carries the provider reference for
   operator reconciliation and :class:`SettlementPersistFailed` propagates, so the webhook can
   answer the provider a status that provokes a redelivery. Nothing is swallowed.

What is **not** guaranteed is a single atomic commit across the four writes, because the
Persistence_Layer this codebase talks to does not offer one. The database-side controls are what
make the intermediate states unable to grant anything they should not: a row cannot be ``active``
without a period, and it cannot become ``active`` without a settlement.

WHY THE ENTITLEMENT GRANT LIVES HERE AND CARRIES AN EXPIRY
----------------------------------------------------------
:func:`grant_deployment_permission` here takes the expiry as a **required** keyword and refuses
``None`` outright, so the null-expiry privilege defect is *unrepresentable* on this path rather
than merely absent from it. That distinction is the whole reason this implementation is the
authoritative one, and it is worth restating even though the alternative is gone:
``routers/library.grant_deployment_permission`` wrote ``"expires_at": None`` unconditionally and
took no expiry parameter, so it *could not* express a Subscription_Period even when its caller
knew one. Combined with the perpetual branch ``check_deployment_permission`` carried until task
18.2 removed it, that null was the defect: a monthly Listing granted forever. A caller here that
has no expiry cannot express one, and the refusal is a ``ValueError`` rather than a silently
written null.

Requirement 30.2 admits one ``grant_deployment_permission``, and since task 19.3 there is exactly
one - this one. The router's copy was deleted, not repointed at this module, and the reasoning is
still load-bearing: its only caller was ``renew_subscription``, the no-payment activation path
Requirement 11.16 ordered deleted, so the caller and the writer went together. Repointing would
have meant a service module imported by ``routers/library`` importing that router back, inverting
this package's layering rule. This module still does not edit that file; ``routers/library.py``
carries a comment block where the deleted function stood, recording the same reasoning at the
site a reader is most likely to look for it.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* The webhook plumbing. Signature validation, IP allow-listing, timestamp skew and the Redis lock
  are ``routers/billing.py``'s and are untouched; task 19.2 adds the one call into this module.
* An HTTP status, an error code or a response body for the earnings read. :func:`creator_earnings`
  (task 21.1) is the *read* - it lives here because this module owns the ledger, and a total that
  did not agree with the columns :func:`_settlement_payload` writes would be a total of something
  else. Turning a :class:`SettlementPersistenceError` into ``MARKETPLACE_READ_FAILED`` and shaping
  the response is ``routers/library.creator_analytics``'s, which is where the HTTP surface lives.
  What the write path contributes to the totals is the guarantee they rely on: a payment that did
  not persist writes no row at all, so it cannot enter any figure.
* The expiry sweep. Requirement 11.8's lapse is task 20's; nothing here writes ``expired``.
* Any ``float``, any ``Decimal``, any cross-currency conversion.

TWO REQUIREMENTS DECISIONS, RECORDED HERE AND AT THEIR DECISION SITES (task 19.2)
---------------------------------------------------------------------------------
**1. Renewing an already-``ACTIVE`` Subscription now extends the period (Requirement 11.5).**
:func:`_apply_transition` used to gate the period write behind ``can_transition``, and
Requirement 11.2 holds no ``ACTIVE -> ACTIVE`` pair - so a confirmed payment for a Subscription
that was *already* ``active`` recorded its Settlement_Record and then extended **no period**.
That is the ordinary auto-renew case (a provider that charges before the current expiry), and a
paid renewal that extends nothing is a financial-correctness defect, not a missing nicety. The
resolution: an ``ACTIVE -> ACTIVE`` settlement extends the period through
:func:`period_for_renewal` **anchored on the stored ``period_expiry``**, never on a clock read, so
an early renewal lengthens the paid period instead of truncating it. It is not a transition - no
state changes - so Requirement 11.2 has nothing to say about it, and
``marketplace_subscription_guard``'s first branch returns early for a same-value write precisely
so "the checkout, cancel and renewal paths" can move the period columns (008's header says so at
length). Requirement 11.12's history row is still written, with ``from_state == to_state ==
'active'``, which is exactly how a reader tells an in-place period extension from a transition.
The invariant Requirement 11.14 is about is preserved by *construction* rather than by the
trigger: the Settlement_Record insert is step 2 and the period write is step 3, so no period is
ever extended without a matching ``marketplace_settlements`` row.

**2. ``payment_failed -> active`` is PERMITTED (Requirement 11.6 over Requirement 11.2).**
Requirement 11.2's twelve pairs give ``PAYMENT_FAILED`` one successor (``PENDING``) while
Requirement 11.6 lists ``PAYMENT_FAILED`` among the sources of a transition into ``ACTIVE``. The
two clauses contradict each other; the resolution is 11.6, because a retried payment that later
succeeds must activate the Subscription it paid for. :data:`ELIGIBLE_FOR_ACTIVATION` therefore
carries five statuses - the four derived from the transition table plus ``payment_failed`` - and
:data:`_ACTIVATION_SOURCES_FROM_TRANSITIONS` keeps the derivation visible beside the addition.
``marketplace_subscription_allowed_transitions`` is seeded by 008 from Requirement 11.2 and held
no ``('payment_failed','active')`` row, so ``trg_subscription_transition_guard`` refused this edge
in the database while this module admitted it - and because the Settlement_Record is written first
(Requirement 9.5), that combination recorded the money and then refused the activation. CLOSED by
``backend_app/migrations/012_subscription_payment_failed_activation.sql`` (task 19.15), an additive
migration that seeds exactly that one pair and nothing else. The settlement precondition is
untouched by it: the guard still refuses any transition into ``'active'`` with no qualifying
non-reversal ``marketplace_settlements`` row, which this module satisfies by writing the ledger
row first.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from backend_app.backend.marketplace import money
from backend_app.backend.marketplace.subscription_period import (
    ensure_utc,
    period_for_activation,
    period_for_renewal,
)
from backend_app.backend.marketplace.subscription_state import (
    STATUS_TEXT_FOR_STATE,
    SubscriptionState,
    can_transition,
    normalise_subscription_state,
)

logger = logging.getLogger("MarketplaceSettlement")

__all__ = [
    "SETTLEMENT_SUBSCRIPTION_SELECT",
    "SETTLEMENT_PAYMENT_SELECT",
    "SETTLEMENT_EARNINGS_SELECT",
    "SETTLEMENT_TABLE",
    "SUBSCRIPTION_TABLE",
    "TRANSITION_TABLE",
    "PERMISSION_TABLE",
    "MAX_SETTLEMENT_ATTEMPTS",
    "SETTLEMENT_RETRY_WINDOW_SECONDS",
    "SETTLEMENT_BACKOFF_SECONDS",
    "SETTLEMENT_REFERENCE_UNIQUE_CONSTRAINT",
    "ELIGIBLE_FOR_ACTIVATION",
    "SettlementOutcome",
    "SettlementResult",
    "EarningsTotals",
    "SettlementPersistenceError",
    "SettlementPersistFailed",
    "grant_deployment_permission",
    "settle",
    "find_settled_payment",
    "creator_earnings",
    "audit_uncorrelated_refund",
]


# ══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════

#: The four tables this module touches, named once. A literal table name repeated across the
#: statements is how a typo becomes a PostgREST ``PGRST205`` on a payment path.
SETTLEMENT_TABLE = "marketplace_settlements"
SUBSCRIPTION_TABLE = "library_subscriptions"
TRANSITION_TABLE = "library_subscription_transitions"
PERMISSION_TABLE = "deployment_permissions"

#: The explicit Subscription projection. No ``select("*")``: every column is named, and the set is
#: bound by the ``settlement`` entry of this package's ``COLUMN_CONTRACT`` and asserted a subset of
#: it by ``tests/test_marketplace_paper_schema_contract.py``.
#:
#: ``price_minor`` and ``currency`` are what the confirmed amount is checked against;
#: ``period_start`` and ``period_expiry`` decide activation versus renewal; ``owner_id``,
#: ``library_id`` and ``user_id`` are the ledger row's identity columns; ``status`` is both the
#: transition's source state and the optimistic guard on every UPDATE.
SETTLEMENT_SUBSCRIPTION_SELECT = (
    "id,library_id,user_id,owner_id,status,price_minor,currency,"
    "period_start,period_expiry,provider,provider_reference"
)

#: The explicit Settlement_Record projection :func:`find_settled_payment` reads, and the reason
#: refund correlation does not depend on provider metadata (task 19.16, Requirements 10.4, 10.8).
#:
#: A refund event carries the reference of the payment it refunds - Stripe's ``payment_intent``,
#: Razorpay's ``payment_id`` - and that is the value the payment's own ledger row was written
#: under. So the Subscription a refund belongs to is already recorded here, in a row this module
#: wrote itself, and does not have to be inferred from ``payment_intent_data.metadata`` (which
#: Stripe copies only when the session asked it to) or from a Razorpay refund entity's ``notes``
#: (which a refund does not reliably carry). ``subscription_id``, ``currency`` and
#: ``amount_minor`` are what the reversal needs; the identity columns travel with them so an
#: operator reconciling from the audit line has the whole row.
SETTLEMENT_PAYMENT_SELECT = (
    "id,subscription_id,listing_id,owner_id,purchaser_id,amount_minor,currency,"
    "provider,provider_reference,is_reversal,settled_at"
)

#: The projection :func:`creator_earnings` reads, and nothing more (task 21.1, Requirements 10.6,
#: 10.7).
#:
#: Five columns, because five is what Requirement 10.7's formula needs: the ``currency`` that
#: decides which total a row belongs to, the ``is_reversal`` flag that decides its sign, and the
#: three money columns that are summed. No ``id``, no ``provider_reference``, no ``purchaser_id``,
#: no ``settled_at`` - an aggregate is not a place to carry the identity of the individual payments
#: it is made of, and every column selected on an owner-facing path is a column that can end up in
#: a response by accident. Bounded by the ``settlement`` entry of this package's
#: ``COLUMN_CONTRACT`` and asserted a subset of it by
#: ``tests/test_marketplace_paper_schema_contract.py``.
SETTLEMENT_EARNINGS_SELECT = (
    "currency,is_reversal,owner_share_minor,platform_fee_minor,amount_minor"
)

#: Requirement 10.11's attempt budget. Five attempts, the first included.
MAX_SETTLEMENT_ATTEMPTS = 5

#: Requirement 10.11's window. No attempt is started once the elapsed time plus the next backoff
#: would pass this, so the budget is bounded in wall-clock terms and not only in attempts.
SETTLEMENT_RETRY_WINDOW_SECONDS = 60

#: The bounded backoff, in whole seconds, one entry per gap between attempts: 1, 2, 4, 8 - fifteen
#: seconds of waiting across five attempts, comfortably inside the sixty-second window with room
#: for the attempts themselves. Integer seconds on purpose: a fractional literal would be the one
#: ``float`` in a module whose whole point is that it has none.
SETTLEMENT_BACKOFF_SECONDS: Tuple[int, ...] = (1, 2, 4, 8)

#: The constraint that makes a duplicate delivery a no-op (Requirements 9.7, 10.5, 10.10).
#: Matched case-insensitively against a driver's error text by :func:`_is_duplicate_reference`.
SETTLEMENT_REFERENCE_UNIQUE_CONSTRAINT = "uq_settlement_reference_reversal"

#: The PostgreSQL unique-violation SQLSTATE. Recognised alongside the constraint name because a
#: driver may surface one, the other or both.
_UNIQUE_VIOLATION_SQLSTATE = "23505"

#: ``library_subscriptions.status`` spellings, resolved through the one enum -> column mapping
#: rather than re-spelled as literals.
_STATUS_ACTIVE = STATUS_TEXT_FOR_STATE[SubscriptionState.ACTIVE]
_STATUS_REFUNDED = STATUS_TEXT_FOR_STATE[SubscriptionState.REFUNDED]

#: The statuses with an edge to ``ACTIVE`` in Requirement 11.2's twelve pairs, DERIVED from the
#: transition table rather than transcribed so the derivation stays visible beside the one
#: addition below: ``pending``, ``expired``, ``cancelled``, ``suspended``.
_ACTIVATION_SOURCES_FROM_TRANSITIONS: FrozenSet[str] = frozenset(
    STATUS_TEXT_FOR_STATE[state]
    for state in SubscriptionState
    if can_transition(state, SubscriptionState.ACTIVE)
)

#: The statuses a confirmed non-reversal payment may move to ``active``.
#:
#: REQUIREMENTS DECISION (task 19.2), Requirement 11.6 over Requirement 11.2: ``payment_failed``
#: is a permitted source. Requirement 11.2 gives ``PAYMENT_FAILED`` exactly one successor
#: (``PENDING``) while Requirement 11.6 lists ``PAYMENT_FAILED`` among the sources of a transition
#: into ``ACTIVE`` - the two clauses contradict, and 11.6 is the one that describes reality: a
#: retried payment that later confirms must activate the Subscription it paid for, and refusing it
#: leaves a purchaser charged with no access. design.md's pascal writes the same five statuses
#: (``'pending','expired','cancelled','payment_failed','suspended'``).
#:
#: THE DATABASE AGREES (task 19.15). ``marketplace_subscription_allowed_transitions`` is seeded by
#: ``008_marketplace_settlement.sql`` from Requirement 11.2 and holds no
#: ``('payment_failed','active')`` row; the additive
#: ``012_subscription_payment_failed_activation.sql`` seeds exactly that one pair, so
#: ``trg_subscription_transition_guard`` admits the edge this set admits. The DB permits thirteen
#: pairs - Requirement 11.2's twelve plus this addendum - and
#: ``tests/test_submission_state_agreement.py`` compares this set against the pairs it parses out
#: of BOTH migrations, so a future divergence between the five statuses here and what the guard
#: will accept fails a test rather than charging a purchaser for access they never get.
#: Nothing here weakens the settlement precondition the guard checks separately - the ledger row is
#: written before the transition is attempted.
ELIGIBLE_FOR_ACTIVATION: FrozenSet[str] = _ACTIVATION_SOURCES_FROM_TRANSITIONS | frozenset(
    {STATUS_TEXT_FOR_STATE[SubscriptionState.PAYMENT_FAILED]}
)

#: ``deployment_permissions.granted_via`` for a subscription grant. ``valid_granted_via`` admits
#: only ``'subscription'`` and ``'ownership'``.
_GRANTED_VIA_SUBSCRIPTION = "subscription"

#: The ``library_subscription_transitions.cause`` values this module writes. The column is
#: ``NOT NULL`` and is read by the audit trail and by task 19.11's history property, so the
#: vocabulary is fixed here rather than at each call site.
_CAUSE_SETTLEMENT = "settlement"
_CAUSE_REFUND = "refund"

#: The longest text this module will persist into a free-text column.
_MAX_CAUSE_CHARS = 500


# ══════════════════════════════════════════════════════════════════════════
# THE OUTCOMES
# ══════════════════════════════════════════════════════════════════════════


class SettlementOutcome(str, Enum):
    """What one :func:`settle` call did. Every return lands on exactly one of these.

    ``str``-valued so a member compares equal to its own spelling in an audit line or a log,
    matching :class:`SubscriptionState`.
    """

    #: A Settlement_Record was written and, where the Subscription_State admitted it, the
    #: transition into ``active``, the period, the history row and the entitlement with it.
    RECORDED = "recorded"
    #: A reversal row was written (``is_reversal = TRUE``) and the Subscription moved to
    #: ``refunded``. The original row is untouched - nothing is ever updated or deleted
    #: (Requirement 10.8).
    REVERSED = "reversed"
    #: ``uq_settlement_reference_reversal`` refused the insert: this provider reference has already
    #: been settled. No second record, no second period extension (Requirements 9.6, 10.10, P-6).
    DUPLICATE_IGNORED = "duplicate_ignored"
    #: No Subscription row for the ``subscription_id`` the confirmation carried. Nothing written
    #: (Requirement 9.14).
    UNMATCHED = "unmatched"
    #: The confirmed amount or currency is not what the Subscription recorded. Nothing written
    #: (Requirement 9.14).
    #:
    #: A **partial** refund lands here too, and deliberately: its amount is by definition not the
    #: amount the Subscription recorded, so recording it would put a number nobody agreed to in
    #: the ledger and pay 90 percent of it out. The audit line names both figures, which is what
    #: an operator needs to settle it by hand.
    MISMATCHED = "mismatched"
    #: Every attempt inside the Requirement 10.11 budget failed. Accompanied by
    #: :class:`SettlementPersistFailed`; the ledger is unchanged and the payment enters no earnings
    #: figure.
    PERSIST_FAILED = "persist_failed"

    def __str__(self) -> str:  # pragma: no cover - convenience for log lines
        return self.value


@dataclass(frozen=True)
class SettlementResult:
    """The outcome of one :func:`settle` call, and every figure it recorded.

    Frozen, and every money field is an ``int`` number of Minor_Units or ``None`` - there is no
    ``float`` and no ``Decimal`` field here, so a caller cannot reintroduce the inexactness
    Requirement 10.3 forbids by reading this value back into an arithmetic path.
    """

    outcome: SettlementOutcome
    provider_reference: str
    is_reversal: bool
    subscription_id: Optional[str] = None
    settlement_id: Optional[str] = None
    amount_minor: Optional[int] = None
    owner_share_minor: Optional[int] = None
    platform_fee_minor: Optional[int] = None
    currency: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    period_start: Optional[datetime] = None
    period_expiry: Optional[datetime] = None
    #: True when a period was written by this call - the fact property P-6 counts.
    period_written: bool = False
    #: True when a ``deployment_permissions`` row was written by this call.
    entitlement_granted: bool = False
    #: How many attempts the call took, including the successful one (Requirement 10.11).
    attempts: int = 1

    @property
    def wrote_settlement(self) -> bool:
        """Whether this call added a row to the ledger."""
        return self.outcome in {SettlementOutcome.RECORDED, SettlementOutcome.REVERSED}


@dataclass(frozen=True)
class EarningsTotals:
    """One currency's totals over an owner's Settlement_Records (Requirements 10.6, 10.7).

    Every money field is an ``int`` number of Minor_Units **in this instance's own currency**.
    There is no combined field and no major-unit field: Requirement 10.7 forbids combining
    currencies into one total, and ``float``/``Decimal`` are absent so a caller cannot read a
    total back into an inexact arithmetic path (Requirement 10.3). A caller that needs major units
    for display passes the integer through :func:`money.to_major` at the presentation boundary and
    nowhere else.

    ``owner_total_minor`` may be negative when reversal Settlement_Records outweigh payments in
    this currency; see :func:`creator_earnings` for why that is reported rather than clamped.

    ``settlement_count`` and ``reversal_count`` count rows, not money. They are here because a
    total of zero has two very different causes - no payments, or payments fully reversed - and an
    owner reading a figure is entitled to know which one they are looking at.
    """

    currency: str
    owner_total_minor: int = 0
    platform_total_minor: int = 0
    gross_total_minor: int = 0
    settlement_count: int = 0
    reversal_count: int = 0

    @property
    def row_count(self) -> int:
        """How many Settlement_Records this total is the sum of, reversals included."""
        return self.settlement_count + self.reversal_count


# ══════════════════════════════════════════════════════════════════════════
# THE DEFINED ERROR OUTCOMES
# ══════════════════════════════════════════════════════════════════════════


class SettlementPersistenceError(Exception):
    """One read or write in one attempt did not complete. Retried, never swallowed.

    Raised rather than returned because it is not an answer: a failed Subscription read reported
    as "unmatched" would discard a real payment, and a failed insert reported as "duplicate"
    would discard it silently. :func:`settle` catches this one type, waits, and tries the
    remaining steps again.
    """


class SettlementPersistFailed(Exception):
    """Every attempt inside Requirement 10.11's budget failed.

    Carries the ``provider_reference`` an operator reconciles by, the ``attempts`` made and the
    ``result`` describing what (if anything) reached the ledger. It propagates so the webhook can
    answer the provider a status that provokes a redelivery - a settlement that quietly did not
    happen is the failure mode Requirement 10.11 exists to make visible.

    Requirement 10.11's "leave the Settlement_Ledger unchanged" holds exactly when it is the
    ledger INSERT that never completed, which is the case the requirement is written about. When a
    later step is what failed, the ledger row stands: Requirement 10.8 forbids deleting or
    modifying a persisted Settlement_Record, and the money did move. Read
    ``result.wrote_settlement`` and the ``settlement_recorded`` member of the
    ``MARKETPLACE_SETTLEMENT_PERSIST_FAILED`` metadata to tell the two apart - an operator
    reconciling by hand needs to know which one happened.
    """

    def __init__(
        self,
        provider_reference: str,
        *,
        attempts: int,
        result: SettlementResult,
        cause: Optional[BaseException] = None,
    ) -> None:
        self.provider_reference = provider_reference
        self.attempts = attempts
        self.result = result
        self.cause = cause
        super().__init__(
            f"settlement for provider reference {provider_reference} did not persist after "
            f"{attempts} attempt(s): {cause}"
        )


#: The entitlement writer. Injected so the expiry a grant receives is observable without a
#: database - which is the whole point of the parameter, given that the value it replaced was
#: ``None`` and ``None`` granted permanent access.
GrantPermission = Callable[..., Optional[str]]


# ══════════════════════════════════════════════════════════════════════════
# THE ENTITLEMENT WRITE (Requirement 11.6, and the null-expiry defect)
# ══════════════════════════════════════════════════════════════════════════


def grant_deployment_permission(
    supabase: Any,
    *,
    user_id: str,
    library_id: str,
    subscription_id: str,
    expires_at: datetime,
    granted_via: str = _GRANTED_VIA_SUBSCRIPTION,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Write the ``deployment_permissions`` row for a settled Subscription_Period.

    ``expires_at`` is a **required keyword and may not be ``None``**. Since task 19.3 this is the
    codebase's only ``grant_deployment_permission``; the required keyword is why it is the one
    that survived. ``routers/library.grant_deployment_permission`` wrote ``"expires_at": None``
    unconditionally, and ``check_deployment_permission`` reads a null expiry as a *perpetual*
    subscription - so that null was not a missing value, it was a permanent grant. Here a caller
    that has no expiry cannot express one, and the refusal is a ``ValueError`` rather than a
    silently-written null: the defect is unrepresentable on this path, which is a stronger
    statement than "no caller currently passes None".

    Returns:
        The new permission row's id when the response carried one, otherwise ``None`` - the row is
        written either way, and the id is a convenience for the audit line rather than a fact this
        path depends on.

    Raises:
        ValueError: ``expires_at`` is ``None``, or an identifier is blank.
        SettlementPersistenceError: the write did not complete.
    """
    if expires_at is None:
        raise ValueError(
            "a deployment permission for a subscription must carry the period expiry; a null "
            "expires_at is read as a perpetual subscription by check_deployment_permission, "
            "which is how a monthly listing became permanent access"
        )
    resolved_user = _require_text(user_id, "user_id")
    resolved_library = _require_text(library_id, "library_id")
    resolved_subscription = _require_text(subscription_id, "subscription_id")
    granted_at = _coerce_instant(now)

    payload: Dict[str, Any] = {
        "user_id": resolved_user,
        "library_id": resolved_library,
        "granted_via": granted_via,
        "subscription_id": resolved_subscription,
        "is_active": True,
        "granted_at": granted_at.isoformat(),
        # The period expiry, NOT None. This single value is the fix.
        "expires_at": ensure_utc(expires_at).isoformat(),
    }

    try:
        response = supabase.table(PERMISSION_TABLE).insert(payload).execute()
        rows = _rows(response)
    except SettlementPersistenceError:
        raise
    except Exception as exc:  # noqa: BLE001 - converted to the defined outcome, never swallowed
        raise SettlementPersistenceError(
            f"the deployment permission write for subscription {resolved_subscription} did not "
            f"complete: {exc}"
        ) from exc
    return _as_text(_get(rows[0], "id")) if rows else None


# ══════════════════════════════════════════════════════════════════════════
# ONE ATTEMPT'S PROGRESS
# ══════════════════════════════════════════════════════════════════════════


class _Attempt:
    """What the previous attempts of one :func:`settle` call already accomplished.

    Retrying is *resumption*, not repetition: each step records that it completed, and a later
    attempt skips it. Without this, a transient failure at the transition UPDATE would restart at
    the insert, hit ``uq_settlement_reference_reversal`` with its own row, report
    ``DUPLICATE_IGNORED`` and leave a recorded payment that never granted anything - which is
    worse than the failure it was recovering from.

    ``insert_issued`` is the flag that distinguishes those two readings of one unique violation:
    set before the statement is sent, so a violation seen while it is already ``True`` is this
    call's own row rather than another delivery's.
    """

    __slots__ = (
        "subscription",
        "insert_issued",
        "settlement_recorded",
        "settlement_id",
        "status_moved",
        "transition_written",
        "entitlement_granted",
        "period_start",
        "period_expiry",
        "from_status",
        "to_status",
    )

    def __init__(self) -> None:
        self.subscription: Optional[Mapping[str, Any]] = None
        self.insert_issued: bool = False
        self.settlement_recorded: bool = False
        self.settlement_id: Optional[str] = None
        #: Whether the transition UPDATE has already been applied. Tracked SEPARATELY from
        #: ``transition_written`` because the two steps fail independently: the UPDATE is guarded
        #: by ``.eq("status", <the status that was read>)``, so once it has landed it can never
        #: match again. A retry that re-issued it after the history insert failed would report
        #: "matched no row" on every remaining attempt and burn the whole Requirement 10.11
        #: budget failing at a step that had already succeeded - leaving a payment that IS
        #: recorded and IS activated reported as a persistence failure with no history row.
        self.status_moved: bool = False
        self.transition_written: bool = False
        self.entitlement_granted: bool = False
        self.period_start: Optional[datetime] = None
        self.period_expiry: Optional[datetime] = None
        self.from_status: Optional[str] = None
        self.to_status: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════
# THE ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════


async def settle(
    *,
    provider_reference: str,
    provider: str,
    amount_minor: int,
    currency: str,
    subscription_id: str,
    confirmation_instant: datetime,
    supabase: Any,
    is_reversal: bool = False,
    reverses_reference: Optional[str] = None,
    actor_id: Optional[str] = None,
    grant_permission: Optional[GrantPermission] = None,
    max_attempts: int = MAX_SETTLEMENT_ATTEMPTS,
    retry_window_seconds: int = SETTLEMENT_RETRY_WINDOW_SECONDS,
) -> SettlementResult:
    """Record one confirmed payment (or one reversal) and everything that follows from it.

    The single entry point, and the only path that may move a Subscription to ``active``. It runs
    :func:`_settle_once` under Requirement 10.11's retry budget, resuming at the first step that
    has not completed; a step that never completes leaves the ledger unchanged and writes
    ``MARKETPLACE_SETTLEMENT_PERSIST_FAILED``.

    Args:
        provider_reference: the provider's own transaction reference - the value
            ``uq_settlement_reference_reversal`` deduplicates on and an operator reconciles by.
        provider: ``stripe`` or ``razorpay``. ``chk_settlement_provider`` admits no third.
        amount_minor: the confirmed amount, an ``int`` number of Minor_Units. A ``float`` is
            refused by :mod:`money` rather than coerced (Requirement 10.3).
        currency: the ISO 4217 code the payment settled in. Never converted, and never combined
            with another currency (Requirement 10.7).
        subscription_id: the ``library_subscriptions.id`` from the provider metadata.
        confirmation_instant: the Billing_Integration payment confirmation instant. The period is
            computed from this value, never from a clock read here, so the arithmetic is
            reproducible for an audit (Requirements 11.4, 11.5).
        supabase: the injected Persistence_Layer handle (a service-role client at the webhook).
        is_reversal: whether this confirmation is a refund. A reversal writes an additional row
            with ``is_reversal = TRUE`` and moves the Subscription to ``refunded``; it never
            updates or deletes the original (Requirement 10.8).
        reverses_reference: the reference of the payment being reversed. Defaults to
            ``provider_reference`` - the refunded payment's own reference, which is what both
            providers' refund events carry - and is required to be non-empty for a reversal by
            ``chk_settlement_reversal_reference``.
        actor_id: the identity recorded on the transition row. ``None`` for a provider-driven
            confirmation, which has no human actor.
        grant_permission: the entitlement writer. Defaults to
            :func:`grant_deployment_permission`; injected by the tests so the expiry a grant
            receives is observable.
        max_attempts: Requirement 10.11's attempt budget. Defaults to
            :data:`MAX_SETTLEMENT_ATTEMPTS`.
        retry_window_seconds: Requirement 10.11's window. Defaults to
            :data:`SETTLEMENT_RETRY_WINDOW_SECONDS`; lowered by tests, which cannot afford to
            wait fifteen seconds to prove a backoff exists.

    Returns:
        A :class:`SettlementResult`. ``UNMATCHED``, ``MISMATCHED`` and ``DUPLICATE_IGNORED`` are
        ordinary returns, not exceptions: the audit line is the response and there is nothing for
        the caller to retry.

    Raises:
        ValueError: an argument is blank, or ``confirmation_instant`` is not a datetime.
        money.InvalidAmount: ``amount_minor`` is not an admissible integer number of Minor_Units.
            Raised **before** any read or write, so an inadmissible amount reaches no statement
            (Requirement 10.1, property P-7).
        SettlementPersistFailed: every attempt failed. The ledger is unchanged and
            ``MARKETPLACE_SETTLEMENT_PERSIST_FAILED`` carries the provider reference.
    """
    reference = _require_text(provider_reference, "provider_reference")
    resolved_provider = _require_text(provider, "provider").lower()
    resolved_currency = _normalise_currency(currency)
    subscription_key = _require_text(subscription_id, "subscription_id")
    instant = ensure_utc(confirmation_instant)
    reversal = bool(is_reversal)
    reversed_reference = (
        _require_text(reverses_reference, "reverses_reference") if reverses_reference else reference
    ) if reversal else None

    if not resolved_currency:
        raise ValueError("currency is required")

    # The split is taken ONCE, at the boundary, before anything is read or written. Two things
    # follow from doing it here rather than inside the attempt loop: an inadmissible amount - a
    # ``float``, a negative, an over-maximum - is refused by :mod:`money` before any statement is
    # issued, so it writes no row (Requirement 10.1, property P-7); and every retry records the
    # same two components, because they are computed once rather than recomputed per attempt.
    owner_share_minor, platform_fee_minor = money.split_ninety_ten(amount_minor)

    attempt_state = _Attempt()
    writer = grant_permission or grant_deployment_permission
    started = time.monotonic()
    budget = max(1, int(max_attempts))
    window = max(1, int(retry_window_seconds))
    last_error: Optional[BaseException] = None

    for attempt in range(1, budget + 1):
        try:
            return await _settle_once(
                attempt_state,
                attempt=attempt,
                reference=reference,
                provider=resolved_provider,
                amount_minor=amount_minor,
                owner_share_minor=owner_share_minor,
                platform_fee_minor=platform_fee_minor,
                currency=resolved_currency,
                subscription_id=subscription_key,
                instant=instant,
                supabase=supabase,
                is_reversal=reversal,
                reverses_reference=reversed_reference,
                actor_id=actor_id,
                grant_permission=writer,
            )
        except SettlementPersistenceError as exc:
            last_error = exc
            logger.warning(
                "settlement attempt %s/%s for provider reference %s did not complete: %s",
                attempt,
                budget,
                reference,
                exc,
            )
            if attempt >= budget:
                break
            backoff = _backoff_for(attempt)
            if time.monotonic() - started + backoff >= window:
                # Requirement 10.11 bounds the retries in wall-clock time as well as in count;
                # starting an attempt that cannot finish inside the window is not a retry, it is
                # an unbounded wait on a webhook thread.
                logger.warning(
                    "settlement for provider reference %s abandoned the remaining attempts: the "
                    "next backoff would pass the %ss window",
                    reference,
                    window,
                )
                break
            await asyncio.sleep(backoff)

    result = _persist_failed_result(
        attempt_state,
        reference=reference,
        subscription_id=subscription_key,
        is_reversal=reversal,
        attempts=budget,
    )
    await _audit_persist_failed(
        reference=reference,
        subscription_id=subscription_key,
        provider=resolved_provider,
        amount_minor=amount_minor,
        currency=resolved_currency,
        is_reversal=reversal,
        attempts=budget,
        cause=last_error,
        state=attempt_state,
    )
    raise SettlementPersistFailed(
        reference, attempts=budget, result=result, cause=last_error
    )


async def _settle_once(
    state: _Attempt,
    *,
    attempt: int,
    reference: str,
    provider: str,
    amount_minor: int,
    owner_share_minor: int,
    platform_fee_minor: int,
    currency: str,
    subscription_id: str,
    instant: datetime,
    supabase: Any,
    is_reversal: bool,
    reverses_reference: Optional[str],
    actor_id: Optional[str],
    grant_permission: GrantPermission,
) -> SettlementResult:
    """One pass over design.md's pascal, resuming at the first incomplete step.

    Every step consults ``state`` before acting and records that it completed, so a retry repeats
    nothing. Raises :class:`SettlementPersistenceError` for anything the caller should retry, and
    returns for every outcome that is an answer.
    """
    # ── 1. The locking read (design.md's SELECT … FOR UPDATE) ──
    if state.subscription is None:
        state.subscription = _read_subscription(supabase, subscription_id)

    subscription = state.subscription
    if subscription is None:
        # Requirement 9.14: nothing to correlate the payment to, so nothing is written against a
        # guess. The audit line is the whole response.
        await _audit_unmatched(
            reference=reference,
            subscription_id=subscription_id,
            provider=provider,
            amount_minor=amount_minor,
            currency=currency,
            is_reversal=is_reversal,
        )
        return SettlementResult(
            outcome=SettlementOutcome.UNMATCHED,
            provider_reference=reference,
            is_reversal=is_reversal,
            subscription_id=subscription_id,
            amount_minor=amount_minor,
            currency=currency,
            attempts=attempt,
        )

    recorded_amount = _get(subscription, "price_minor")
    recorded_currency = _normalise_currency(_get(subscription, "currency"))
    if not _amount_matches(recorded_amount, amount_minor) or recorded_currency != currency:
        # Requirement 9.14: a confirmation for an amount nobody agreed to. No transition, no
        # record, no entitlement - the audit names both figures for the operator.
        await _audit_mismatched(
            reference=reference,
            subscription_id=subscription_id,
            provider=provider,
            expected_amount_minor=recorded_amount,
            expected_currency=recorded_currency,
            amount_minor=amount_minor,
            currency=currency,
            is_reversal=is_reversal,
        )
        return SettlementResult(
            outcome=SettlementOutcome.MISMATCHED,
            provider_reference=reference,
            is_reversal=is_reversal,
            subscription_id=subscription_id,
            amount_minor=amount_minor,
            currency=currency,
            from_status=_normalise_status(_get(subscription, "status")),
            attempts=attempt,
        )

    # ── 2+3. The ledger row, the transition and its history row, ATOMICALLY (MKT-03).
    #       Replaces what used to be `_insert_settlement` (step 2) and `_apply_transition`
    #       (step 3) as two SEPARATE PostgREST statements -- the exact gap this finding
    #       names: a crash or an exhausted retry budget between them left a payment
    #       recorded as settled with the Subscription never activated. See
    #       `_record_and_transition`'s own docstring and
    #       backend_app/migrations/050_mkt03_settlement_atomic_transition.sql for the one
    #       Postgres function this now calls through a single `.rpc()`. The split that
    #       feeds it arrived from :func:`settle`, which took it from
    #       ``money.split_ninety_ten`` once. ──
    disposition = _record_and_transition(
        supabase,
        state,
        reference=reference,
        provider=provider,
        subscription=subscription,
        amount_minor=amount_minor,
        owner_share_minor=owner_share_minor,
        platform_fee_minor=platform_fee_minor,
        currency=currency,
        is_reversal=is_reversal,
        reverses_reference=reverses_reference,
        instant=instant,
        actor_id=actor_id,
        subscription_id=subscription_id,
    )
    if state.subscription is not None:
        # Mirrors the original code's explicit `subscription = resumed_subscription`
        # reassignment on the DUPLICATE_RESUMED path: `state.subscription` is updated
        # to the FRESH read exactly there, so this keeps the local `subscription` the
        # same image step 4 below (the entitlement grant) reads `user_id`/`library_id`
        # from.
        subscription = state.subscription

    if disposition == "duplicate_ignored":
        await _audit_duplicate_ignored(
            reference=reference,
            subscription_id=subscription_id,
            provider=provider,
            amount_minor=amount_minor,
            currency=currency,
            is_reversal=is_reversal,
        )
        return SettlementResult(
            outcome=SettlementOutcome.DUPLICATE_IGNORED,
            provider_reference=reference,
            is_reversal=is_reversal,
            subscription_id=subscription_id,
            amount_minor=amount_minor,
            owner_share_minor=owner_share_minor,
            platform_fee_minor=platform_fee_minor,
            currency=currency,
            from_status=_normalise_status(_get(subscription, "status")),
            attempts=attempt,
        )
    if disposition == "duplicate_resumed":
        await _audit_duplicate_resumed(
            reference=reference,
            subscription_id=subscription_id,
            provider=provider,
            amount_minor=amount_minor,
            currency=currency,
            is_reversal=is_reversal,
        )

    # ── 4. The entitlement, carrying the period expiry (never None). ──
    if (
        not state.entitlement_granted
        and state.to_status == _STATUS_ACTIVE
        and state.period_expiry is not None
    ):
        try:
            grant_permission(
                supabase,
                user_id=_as_text(_get(subscription, "user_id")) or "",
                library_id=_as_text(_get(subscription, "library_id")) or "",
                expires_at=state.period_expiry,
                subscription_id=subscription_id,
            )
        except SettlementPersistenceError:
            raise
        except Exception as exc:  # noqa: BLE001 - converted here, never allowed to escape raw
            # The default writer already raises the defined type, but ``grant_permission`` is an
            # INJECTION POINT: a writer that raises anything else would escape ``settle``
            # untouched, taking the Requirement 10.11 retry budget and the
            # ``MARKETPLACE_SETTLEMENT_PERSIST_FAILED`` audit with it - so the entitlement step
            # would be the one step in this module whose failure is neither retried nor recorded.
            raise SettlementPersistenceError(
                f"the entitlement grant for subscription {subscription_id} did not complete: "
                f"{exc}"
            ) from exc
        state.entitlement_granted = True

    # ── 6. The audit, last, and required (Requirement 10.9). ──
    await _audit_settlement_created(
        reference=reference,
        subscription_id=subscription_id,
        provider=provider,
        amount_minor=amount_minor,
        owner_share_minor=owner_share_minor,
        platform_fee_minor=platform_fee_minor,
        currency=currency,
        is_reversal=is_reversal,
        state=state,
    )

    return SettlementResult(
        outcome=(
            SettlementOutcome.REVERSED if is_reversal else SettlementOutcome.RECORDED
        ),
        provider_reference=reference,
        is_reversal=is_reversal,
        subscription_id=subscription_id,
        settlement_id=state.settlement_id,
        amount_minor=amount_minor,
        owner_share_minor=owner_share_minor,
        platform_fee_minor=platform_fee_minor,
        currency=currency,
        from_status=state.from_status,
        to_status=state.to_status,
        period_start=state.period_start,
        period_expiry=state.period_expiry,
        period_written=state.period_expiry is not None and not is_reversal,
        entitlement_granted=state.entitlement_granted,
        attempts=attempt,
    )


# ══════════════════════════════════════════════════════════════════════════
# THE READ
# ══════════════════════════════════════════════════════════════════════════


def _read_subscription(supabase: Any, subscription_id: str) -> Optional[Mapping[str, Any]]:
    """The Subscription this confirmation names, or ``None`` when there is no such row.

    ``None`` means *unmatched* and is an answer. A read that did not complete is **not**: it
    raises, because answering "unmatched" for a broken read would discard a real payment, write
    no Settlement_Record for it and leave the purchaser charged with no access
    (Requirements 1.5, 1.7, 30.5).
    """
    try:
        response = (
            supabase.table(SUBSCRIPTION_TABLE)
            .select(SETTLEMENT_SUBSCRIPTION_SELECT)
            .eq("id", subscription_id)
            .execute()
        )
        rows = _rows(response)
    except SettlementPersistenceError:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome, never swallowed
        raise SettlementPersistenceError(
            f"the subscription read for {subscription_id} did not complete: {exc}"
        ) from exc
    return dict(rows[0]) if rows else None


def _read_settlement_id(
    supabase: Any, *, reference: str, is_reversal: bool
) -> Optional[str]:
    """The existing Settlement_Record's id for ``(provider_reference, is_reversal)``, if any.

    MKT-03's resumption path only: read back the row ``uq_settlement_reference_reversal`` found
    when a redelivery's own insert reported a duplicate, so the RECORDED/REVERSED result this
    call goes on to return names the Settlement that actually carries the money instead of a
    freshly generated id nothing refers to. ``find_settled_payment`` is not reused here because
    it is hardcoded to ``is_reversal=False``; this needs either.
    """
    try:
        response = (
            supabase.table(SETTLEMENT_TABLE)
            .select("id")
            .eq("provider_reference", reference)
            .eq("is_reversal", is_reversal)
            .execute()
        )
        rows = _rows(response)
    except SettlementPersistenceError:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome, never swallowed
        raise SettlementPersistenceError(
            f"reading back the existing settlement for {reference} did not complete: {exc}"
        ) from exc
    # The predicate is restated in the process, for the same reason ``find_settled_payment``
    # restates ``is_reversal`` on its own rows: a double that lost one of the two ``.eq``
    # filters must not let a reversal's row answer for its own payment, or the other way round.
    for row in rows:
        if bool(_get(row, "is_reversal")) == bool(is_reversal):
            return _as_text(_get(row, "id"))
    return None


def find_settled_payment(
    supabase: Any, *, provider_reference: str
) -> Optional[Mapping[str, Any]]:
    """The non-reversal Settlement_Record written under ``provider_reference``, or ``None``.

    HOW A REFUND FINDS ITS SUBSCRIPTION (task 19.16, Requirements 10.4, 10.8)
    ------------------------------------------------------------------------
    A refund event names the payment it refunds and nothing else this system chose: Stripe's
    ``charge.refunded`` carries ``payment_intent``, Razorpay's ``refund.processed`` carries
    ``payment_id``. Both are the value the payment settled under, because
    :func:`settle` records the payment intent (not the Checkout Session id) as
    ``provider_reference``. This function reads that row back, so the Subscription, the currency
    and the original amount a reversal needs come from **the ledger**, not from event metadata.

    That matters because event metadata is not dependable on a refund: Stripe copies Checkout
    Session metadata onto the PaymentIntent only when the session was created with
    ``payment_intent_data.metadata``, and a Razorpay refund entity does not reliably carry the
    payment's ``notes``. ``checkout_service`` now sets both (belt and braces), but correlation
    does not depend on either having arrived.

    Nothing is invented here: this reads a row this module wrote. ``None`` means no payment was
    ever settled under that reference - a plan refund, or a payment this ledger never recorded -
    and the caller's answer to ``None`` must be to write nothing.

    Args:
        supabase: the injected Persistence_Layer handle.
        provider_reference: the refunded payment's provider transaction reference.

    Returns:
        The Settlement_Record as a mapping, or ``None`` when no non-reversal row exists for that
        reference. ``uq_settlement_reference_reversal`` makes at most one such row possible.

    Raises:
        ValueError: ``provider_reference`` is blank.
        SettlementPersistenceError: the read did not complete. **Not** ``None``: answering "no
            such payment" for a broken read would drop a real reversal, leaving an owner credited
            with money that went back to the purchaser.
    """
    reference = _require_text(provider_reference, "provider_reference")
    try:
        response = (
            supabase.table(SETTLEMENT_TABLE)
            .select(SETTLEMENT_PAYMENT_SELECT)
            .eq("provider_reference", reference)
            .eq("is_reversal", False)
            .execute()
        )
        rows = _rows(response)
    except SettlementPersistenceError:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome, never swallowed
        raise SettlementPersistenceError(
            f"the settlement read for provider reference {reference} did not complete: {exc}"
        ) from exc
    for row in rows:
        # The predicate is restated in the process, because a Persistence_Layer double or a
        # future filter change that lost ``is_reversal`` would otherwise let a reversal row be
        # read as the payment it reverses - and the reversal of a reversal is a second earning.
        if not bool(_get(row, "is_reversal")):
            return dict(row)
    return None


# ══════════════════════════════════════════════════════════════════════════
# THE EARNINGS READ (Requirements 10.6, 10.7) - task 21.1
# ══════════════════════════════════════════════════════════════════════════


def creator_earnings(
    supabase: Any, *, owner_id: str
) -> Dict[str, "EarningsTotals"]:
    """Every figure an owner is owed, per currency, in exact integer Minor_Units.

    ONE ROUND TRIP, AND THE OWNER IS A PREDICATE ON IT (Requirements 21.4, 27.2)
    ---------------------------------------------------------------------------
    A single ``SELECT … FROM marketplace_settlements WHERE owner_id = :owner_id``, accumulated in
    Python. The identity is a predicate on the *read*, not a filter applied to rows that already
    crossed the boundary: another creator's ledger is never fetched, so it cannot be reported by
    a handler bug, logged by an exception handler, or reached by guessing an id. The caller passes
    the identity it resolved from the authenticated server-side session; this function performs no
    authorisation of its own and is not a place to add one - it is a read, and its scope is its
    predicate.

    WHY THE READ LIVES HERE AND NOT IN THE ROUTER
    ---------------------------------------------
    This module writes every Settlement_Record. Requirement 10.6's "derive every earnings figure
    by summing persisted Settlement_Records" is a statement about those rows, and it is only
    checkable if the summing and the writing agree about which columns carry the money. Putting
    the read beside :func:`find_settled_payment` - the other ledger read - means the projection is
    declared once (:data:`SETTLEMENT_EARNINGS_SELECT`), bounded by the same ``COLUMN_CONTRACT``
    entry, and cannot drift from :func:`_settlement_payload`'s writes. Query code in
    ``routers/library.py`` could drift the day either side changed.

    THE FORMULA IS REQUIREMENT 10.7's, VERBATIM
    -------------------------------------------
    Per currency: the sum of ``owner_share_minor`` over non-reversal Settlement_Records **minus**
    the sum over reversal Settlement_Records, before any presentation formatting. ``platform_fee``
    and ``amount`` are accumulated the same way, because an owner reconciling a total needs the
    gross and the fee that produced it. Nothing is grouped across currencies and nothing is
    converted: Requirement 10.7 forbids combining Settlement_Records of different currencies into
    a single total, and there is no FX rate in this codebase to combine them with even if it did
    not.

    Every accumulator is a Python ``int``. No ``float`` and no ``Decimal`` appears here or in the
    value type returned, so a caller cannot read a total back into an inexact arithmetic path
    (Requirement 10.3). ``money.split_ninety_ten`` is deliberately **not** called: the split was
    computed and persisted once, at settlement time, and recomputing it from ``amount_minor``
    here would report a number the ledger does not contain if the two ever disagreed.

    A REVERSAL SUBTRACTS, SO A TOTAL MAY BE NEGATIVE
    ------------------------------------------------
    A refund recorded before the payment it reverses appears in this read - possible while a
    payment's insert is being retried (:data:`MAX_SETTLEMENT_ATTEMPTS`) - leaves a negative
    running total. That is reported as it stands rather than clamped to zero: clamping would
    present a figure the ledger does not hold, and a negative owner total is exactly the signal an
    operator needs. :func:`money.to_major` admits a signed amount for the same reason.

    Args:
        supabase: the injected Persistence_Layer handle.
        owner_id: the creator whose ledger is read, resolved server-side by the caller.

    Returns:
        ``{currency: EarningsTotals}``, one entry per currency the owner has at least one
        Settlement_Record in. An owner with no Settlement_Records gets an **empty mapping** - not
        a zero in some assumed currency. There is no currency to state a zero in, and inventing
        one would be the substitution Requirement 28.5 forbids.

    Raises:
        ValueError: ``owner_id`` is blank.
        SettlementPersistenceError: the read did not complete, or a stored ledger value is not an
            integer number of Minor_Units. **Not** an empty mapping for either: "this owner has
            earned nothing" and "the ledger could not be read" are different answers, and
            answering the first for the second is the fabricated zero Requirements 1.5, 1.7 and
            28.2 forbid.
    """
    owner = _require_text(owner_id, "owner_id")
    try:
        response = (
            supabase.table(SETTLEMENT_TABLE)
            .select(SETTLEMENT_EARNINGS_SELECT)
            .eq("owner_id", owner)
            .execute()
        )
        rows = _rows(response)
    except SettlementPersistenceError:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome, never swallowed
        raise SettlementPersistenceError(
            f"the earnings read for owner {owner} did not complete: {exc}"
        ) from exc

    accumulated: Dict[str, EarningsTotals] = {}
    for row in rows:
        currency = _normalise_currency(_get(row, "currency"))
        if not currency:
            # A ledger row with no currency cannot be added to any total: there is no total it
            # belongs to. ``chk_settlement_currency`` makes this unrepresentable in the database,
            # so reaching it means the read did not return what it claims to have returned.
            raise SettlementPersistenceError(
                "a settlement row carries no currency, so its Minor_Units belong to no total"
            )
        is_reversal = bool(_get(row, "is_reversal"))
        sign = -1 if is_reversal else 1
        running = accumulated.get(currency) or EarningsTotals(currency=currency)
        accumulated[currency] = EarningsTotals(
            currency=currency,
            owner_total_minor=running.owner_total_minor
            + sign * _ledger_minor_units(_get(row, "owner_share_minor"), "owner_share_minor"),
            platform_total_minor=running.platform_total_minor
            + sign * _ledger_minor_units(_get(row, "platform_fee_minor"), "platform_fee_minor"),
            gross_total_minor=running.gross_total_minor
            + sign * _ledger_minor_units(_get(row, "amount_minor"), "amount_minor"),
            settlement_count=running.settlement_count + (0 if is_reversal else 1),
            reversal_count=running.reversal_count + (1 if is_reversal else 0),
        )
    return accumulated


def _ledger_minor_units(value: Any, column: str) -> int:
    """A stored ledger amount as an exact ``int``, or a refusal.

    The columns are ``BIGINT`` and PostgREST renders them as JSON integers, but a driver, a view
    or a Persistence_Layer double may hand back the digits as text. Text is accepted and parsed
    exactly; a ``float`` is **not**, because a float that reached this point has already lost
    exactness upstream and adding it to a total would launder that loss into a figure an owner is
    paid on (Requirement 10.3). ``bool`` is refused explicitly, since ``isinstance(True, int)`` is
    ``True`` in Python and ``True`` would otherwise count as one Minor_Unit.

    ``None`` is refused rather than read as zero: all three columns are ``NOT NULL``, so a null
    means the read did not return the column it was asked for, and treating that as zero would
    understate a total silently.
    """
    if value is None:
        raise SettlementPersistenceError(
            f"a settlement row carries no {column}; the total cannot be stated without it"
        )
    if isinstance(value, bool):
        raise SettlementPersistenceError(
            f"a settlement row carries {column}={value!r}, which is not a number of Minor_Units"
        )
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError as exc:
            raise SettlementPersistenceError(
                f"a settlement row carries {column}={value!r}, which is not an integer number "
                f"of Minor_Units"
            ) from exc
    raise SettlementPersistenceError(
        f"a settlement row carries {column} as {type(value).__name__}: {value!r}. Every money "
        f"value in this ledger is an integer number of Minor_Units (Requirement 10.3)"
    )


# ══════════════════════════════════════════════════════════════════════════
# THE LEDGER WRITE (Requirements 10.4, 10.5, 10.8, 10.10)
# ══════════════════════════════════════════════════════════════════════════


def _settlement_payload(
    *,
    settlement_id: str,
    reference: str,
    provider: str,
    subscription: Mapping[str, Any],
    amount_minor: int,
    owner_share_minor: int,
    platform_fee_minor: int,
    currency: str,
    is_reversal: bool,
    reverses_reference: Optional[str],
    settled_at: datetime,
) -> Dict[str, Any]:
    """Every column of the Settlement_Record, and only those columns.

    The closing assertion restates ``chk_settlement_conserved`` in the process that is about to
    issue the insert, so a future edit that computes the fee some other way fails here rather
    than as a ``23514`` from a webhook. It is an assertion about *this module's* arithmetic, not
    a substitute for the constraint - the database checks it again on every row.
    """
    payload: Dict[str, Any] = {
        "id": settlement_id,
        "subscription_id": _as_text(_get(subscription, "id")),
        "listing_id": _as_text(_get(subscription, "library_id")),
        "owner_id": _as_text(_get(subscription, "owner_id")),
        "purchaser_id": _as_text(_get(subscription, "user_id")),
        "amount_minor": amount_minor,
        "owner_share_minor": owner_share_minor,
        "platform_fee_minor": platform_fee_minor,
        "currency": currency,
        "provider": provider,
        "provider_reference": reference,
        "is_reversal": is_reversal,
        "reverses_reference": reverses_reference,
        "settled_at": settled_at.isoformat(),
    }
    assert (
        payload["owner_share_minor"] + payload["platform_fee_minor"] == payload["amount_minor"]
    ), (
        "the 90/10 split must conserve the amount exactly (Requirement 10.2); "
        f"{owner_share_minor} + {platform_fee_minor} != {amount_minor}"
    )
    assert (is_reversal and reverses_reference) or (
        not is_reversal and reverses_reference is None
    ), (
        "chk_settlement_reversal_reference: a reversal carries reverses_reference and a payment "
        "carries none"
    )
    return payload


def _rpc_error_code(exc: Exception) -> str:
    """The SQLSTATE a failed ``.rpc()`` call raised, or ``""`` when none is readable.

    ``record_and_transition_settlement`` (migration ``050_mkt03_settlement_atomic_
    transition.sql``) raises ``RAISE EXCEPTION ... USING ERRCODE = 'MKxxx'``.
    ``postgrest.exceptions.APIError`` carries that SQLSTATE on its own ``.code``
    attribute (PostgREST relays the Postgres error code verbatim); a double that does
    not wrap postgrest's exception type is read by falling back to ``str(exc)``, the
    same two-step reading ``paper_repository._is_unique_violation`` already uses for
    the same reason.
    """
    code = str(getattr(exc, "code", "") or "").strip()
    if code:
        return code
    text = str(exc)
    for candidate in ("MK001", "MK002"):
        if candidate in text:
            return candidate
    return ""


def _decide_transition(
    subscription: Mapping[str, Any], *, is_reversal: bool, instant: datetime
) -> Optional[Dict[str, Any]]:
    """The pure decision ``_apply_transition`` used to make inline, with no I/O.

    Extracted unchanged from the write-performing function this module replaced
    (MKT-03), so that decision stays entirely in Python -- Requirements 11.4, 11.5's
    period arithmetic, ``ELIGIBLE_FOR_ACTIVATION``, ``can_transition`` and the
    ``renewal_in_place`` carve-out are exactly what they were, exercised by the same
    property-test suite (``tests/property/test_subscription_state_machine.py``,
    ``tests/property/test_entitlement_expiry_boundary.py``) -- and only the WRITE of
    what this function decides moves into
    ``backend_app/migrations/050_mkt03_settlement_atomic_transition.sql``.

    Returns:
        ``None`` when the transition is not permitted -- nothing is written and no
        entitlement follows, matching ``_apply_transition``'s own "not permitted"
        branch. Otherwise a dict carrying everything the atomic call and the history
        row need: ``from_status``, ``to_status``, ``period_start``, ``period_expiry``,
        ``cancelled_at`` (the UPDATE's own values -- unchanged, not nulled, for a
        reversal) and ``prior_period_expiry`` / ``new_period_expiry`` for the history
        row.
    """
    from_status = _normalise_status(_get(subscription, "status"))
    stored_expiry = _parse_instant(_get(subscription, "period_expiry"))
    stored_start = _parse_instant(_get(subscription, "period_start"))

    target_state = SubscriptionState.REFUNDED if is_reversal else SubscriptionState.ACTIVE
    target_status = STATUS_TEXT_FOR_STATE[target_state]

    if is_reversal:
        permitted = can_transition(normalise_subscription_state(from_status), target_state)
    else:
        permitted = from_status in ELIGIBLE_FOR_ACTIVATION
        # REQUIREMENTS DECISION (Requirement 11.5, task 19.2): an ``ACTIVE -> ACTIVE``
        # settlement extends the period even though ``can_transition`` says no
        # (Requirement 11.2 lists no such pair) -- see the original `_apply_transition`
        # docstring this was extracted from for the full reasoning, unchanged here.
        renewal_in_place = not permitted and from_status == _STATUS_ACTIVE
        permitted = permitted or renewal_in_place

    if not permitted:
        logger.warning(
            "settlement recorded for subscription %s but %s -> %s is not a permitted "
            "transition, so no state change and no entitlement follow",
            _as_text(_get(subscription, "id")),
            from_status,
            target_status,
        )
        return None

    if is_reversal:
        new_start = stored_start
        new_expiry = stored_expiry
        cancelled_at: Optional[datetime] = instant
    else:
        # The anchor is the STORED ``period_expiry``, not ``now``: :func:`period_for_renewal`
        # extends from the later of the two, so an early renewal lengthens the period
        # instead of truncating it (Requirement 11.5). A row with no stored expiry is a
        # first activation and takes :func:`period_for_activation` (Requirement 11.4).
        if stored_expiry is None:
            new_start, new_expiry = period_for_activation(instant)
        else:
            new_start = stored_start or instant
            new_expiry = period_for_renewal(stored_expiry, instant)
        cancelled_at = None

    return {
        "from_status": from_status,
        "to_status": target_status,
        "period_start": new_start,
        "period_expiry": new_expiry,
        "cancelled_at": cancelled_at,
        "prior_period_expiry": stored_expiry,
        "new_period_expiry": new_expiry,
    }


def _call_record_and_transition(
    supabase: Any,
    *,
    subscription_id: str,
    reference: str,
    is_reversal: bool,
    settlement_payload: Mapping[str, Any],
    proceed_if_duplicate: bool,
    expected_from_status: str,
    decision: Optional[Mapping[str, Any]],
    actor_id: Optional[str],
    instant: datetime,
    user_id: Optional[str],
) -> Dict[str, Any]:
    """One call to ``public.record_and_transition_settlement``
    (``050_mkt03_settlement_atomic_transition.sql``). Translates this module's own
    value types into the jsonb payloads that function expects, and its jsonb result
    back into a plain dict.

    Raises:
        SettlementPersistenceError: the call did not complete, for any reason --
            ``MK001`` (another writer moved the subscription between the read and this
            call) included, which is the exact outcome ``_apply_transition``'s own
            ``.eq("status", from_status)`` guard used to report, and which
            ``settle()``'s Requirement 10.11 retry budget already retries today for it.
    """
    settlement_jsonb = {
        "id": settlement_payload["id"],
        "listing_id": settlement_payload["listing_id"],
        "owner_id": settlement_payload["owner_id"],
        "purchaser_id": settlement_payload["purchaser_id"],
        "amount_minor": settlement_payload["amount_minor"],
        "owner_share_minor": settlement_payload["owner_share_minor"],
        "platform_fee_minor": settlement_payload["platform_fee_minor"],
        "currency": settlement_payload["currency"],
        "provider": settlement_payload["provider"],
        "reverses_reference": settlement_payload["reverses_reference"],
        "settled_at": settlement_payload["settled_at"],
    }

    transition_jsonb: Optional[Dict[str, Any]] = None
    history_jsonb: Optional[Dict[str, Any]] = None
    if decision is not None:
        transition_jsonb = {
            "status": decision["to_status"],
            "period_start": (
                decision["period_start"].isoformat() if decision["period_start"] else None
            ),
            "period_expiry": (
                decision["period_expiry"].isoformat() if decision["period_expiry"] else None
            ),
            "cancelled_at": (
                decision["cancelled_at"].isoformat() if decision["cancelled_at"] else None
            ),
        }
        history_jsonb = {
            "user_id": user_id,
            "from_state": decision["from_status"],
            "to_state": decision["to_status"],
            "cause": _CAUSE_REFUND if is_reversal else _CAUSE_SETTLEMENT,
            "actor_id": actor_id,
            "prior_period_expiry": (
                decision["prior_period_expiry"].isoformat()
                if decision["prior_period_expiry"] else None
            ),
            "new_period_expiry": (
                decision["new_period_expiry"].isoformat()
                if decision["new_period_expiry"] else None
            ),
            "transitioned_at": instant.isoformat(),
        }

    try:
        response = supabase.rpc("record_and_transition_settlement", {
            "p_subscription_id": subscription_id,
            "p_provider_reference": reference,
            "p_is_reversal": is_reversal,
            "p_settlement": settlement_jsonb,
            "p_proceed_if_duplicate": proceed_if_duplicate,
            "p_expected_from_status": expected_from_status,
            "p_transition": transition_jsonb,
            "p_history": history_jsonb,
        }).execute()
    except SettlementPersistenceError:
        raise
    except Exception as exc:  # noqa: BLE001 - classified below, never swallowed
        code = _rpc_error_code(exc)
        if code == "MK001":
            raise SettlementPersistenceError(
                f"the subscription transition for {subscription_id} did not complete: "
                f"another writer moved it while this settlement was in flight ({exc})"
            ) from exc
        raise SettlementPersistenceError(
            f"record_and_transition_settlement for subscription {subscription_id} did "
            f"not complete: {exc}"
        ) from exc

    data = getattr(response, "data", None)
    if not isinstance(data, Mapping):
        raise SettlementPersistenceError(
            f"record_and_transition_settlement for subscription {subscription_id} "
            f"returned no usable result"
        )
    return dict(data)


def _record_and_transition(
    supabase: Any,
    state: _Attempt,
    *,
    reference: str,
    provider: str,
    subscription: Mapping[str, Any],
    amount_minor: int,
    owner_share_minor: int,
    platform_fee_minor: int,
    currency: str,
    is_reversal: bool,
    reverses_reference: Optional[str],
    instant: datetime,
    actor_id: Optional[str],
    subscription_id: str,
) -> str:
    """MKT-03. Steps 2 and 3 (the ledger row, the transition, the history row),
    committed ATOMICALLY in one call to ``record_and_transition_settlement`` instead of
    what used to be up to three separate PostgREST statements (``_insert_settlement``,
    the subscription ``UPDATE``, ``_write_transition_row``'s ``INSERT``). This is what
    closes the finding's own gap: a crash or an exhausted retry budget between the
    ledger insert and the transition can no longer happen, because there is no commit
    boundary between them any more for it to land on.

    The decision of WHAT to write -- whether the transition is permitted, the period
    arithmetic, the renewal-in-place carve-out -- stays entirely in
    :func:`_decide_transition`, called from here with no I/O of its own; this function
    only commits what was decided. Mutates ``state`` exactly as ``_insert_settlement``
    and ``_apply_transition`` used to, for the identical reason: a retry inside this
    one ``settle()`` call must resume rather than repeat.

    Returns:
        ``"fresh"`` -- the ledger row was inserted by this call and the decided
            transition (if any) was applied together with it, atomically.
        ``"duplicate_ignored"`` -- the ledger row already existed from another,
            unrelated delivery, and a fresh read proved the transition is not (or no
            longer) pending. The caller must return ``DUPLICATE_IGNORED`` without
            proceeding to the entitlement step.
        ``"duplicate_resumed"`` -- the ledger row already existed, but a fresh read
            proved the transition is STILL pending (the MKT-03 case this migration
            exists for: an earlier, now-superseded process recorded the money and never
            finished activating it). This call finished it. The caller proceeds to the
            entitlement step as normal.
    """
    if state.settlement_recorded and state.transition_written:
        return "fresh"

    settlement_id = state.settlement_id or str(uuid.uuid4())
    settlement_payload = _settlement_payload(
        settlement_id=settlement_id,
        reference=reference,
        provider=provider,
        subscription=subscription,
        amount_minor=amount_minor,
        owner_share_minor=owner_share_minor,
        platform_fee_minor=platform_fee_minor,
        currency=currency,
        is_reversal=is_reversal,
        reverses_reference=reverses_reference,
        settled_at=instant,
    )
    user_id = _as_text(_get(subscription, "user_id"))
    from_status = _normalise_status(_get(subscription, "status")) or ""
    decision = _decide_transition(subscription, is_reversal=is_reversal, instant=instant)

    result = _call_record_and_transition(
        supabase,
        subscription_id=subscription_id,
        reference=reference,
        is_reversal=is_reversal,
        settlement_payload=settlement_payload,
        proceed_if_duplicate=False,
        expected_from_status=(decision["from_status"] if decision else from_status),
        decision=decision,
        actor_id=actor_id,
        instant=instant,
        user_id=user_id,
    )

    disposition = "fresh"
    if not result.get("inserted"):
        # MKT-03: a duplicate AT THE LEDGER is not necessarily a duplicate at the
        # Subscription. See this module's own header and
        # ``050_mkt03_settlement_atomic_transition.sql`` for why: the row found here
        # belongs to an EARLIER process (a prior delivery that crashed or exhausted its
        # own Requirement 10.11 retry budget before this fix existed, or before THIS
        # call's own ledger insert -- which is itself now atomic with its transition --
        # ever ran). The ``subscription`` read in step 1 may be exactly as stale as the
        # finding describes, so it is re-read here rather than trusted.
        #
        # RESUMABLE MEANS EXACTLY "THE TRANSITION IS STILL PENDING", computed the same
        # way :func:`_decide_transition` decides ``permitted`` -- deliberately WITHOUT
        # its ``renewal_in_place`` addition for a payment, which EXTENDS an
        # already-``active`` period and is ambiguous here between "this exact
        # settlement already activated it" and "a separate, later renewal already
        # did" -- excluded by construction rather than guessed at. A reversal has no
        # analogous extension to double: ``can_transition(..., REFUNDED)`` is already
        # ``False`` once a Subscription IS refunded.
        resumed_subscription = _read_subscription(supabase, subscription_id)
        fresh_status = (
            _normalise_status(_get(resumed_subscription, "status"))
            if resumed_subscription is not None
            else None
        )
        if is_reversal:
            transition_pending = resumed_subscription is not None and can_transition(
                normalise_subscription_state(fresh_status), SubscriptionState.REFUNDED
            )
        else:
            transition_pending = (
                resumed_subscription is not None and fresh_status in ELIGIBLE_FOR_ACTIVATION
            )

        if resumed_subscription is not None and transition_pending:
            disposition = "duplicate_resumed"
            state.subscription = resumed_subscription
            state.settlement_recorded = True
            state.settlement_id = settlement_id
            decision = _decide_transition(
                resumed_subscription, is_reversal=is_reversal, instant=instant
            )
            result = _call_record_and_transition(
                supabase,
                subscription_id=subscription_id,
                reference=reference,
                is_reversal=is_reversal,
                settlement_payload=settlement_payload,
                proceed_if_duplicate=True,
                expected_from_status=(
                    decision["from_status"] if decision else (fresh_status or "")
                ),
                decision=decision,
                actor_id=actor_id,
                instant=instant,
                user_id=_as_text(_get(resumed_subscription, "user_id")),
            )
        else:
            return "duplicate_ignored"
    else:
        state.settlement_recorded = True
        state.settlement_id = settlement_id

    state.from_status = decision["from_status"] if decision else from_status
    if decision is None:
        state.to_status = None
        state.period_start = None
        state.period_expiry = None
    else:
        state.to_status = decision["to_status"]
        state.period_start = None if is_reversal else decision["period_start"]
        state.period_expiry = None if is_reversal else decision["period_expiry"]
    state.transition_written = True
    return disposition


# ══════════════════════════════════════════════════════════════════════════
# THE AUDIT LINES (Requirements 9.14, 10.9, 10.10, 10.11)
# ══════════════════════════════════════════════════════════════════════════


def _audit_metadata(**values: Any) -> Dict[str, Any]:
    """The metadata object for an audit line, with absent members omitted rather than nulled."""
    return {key: value for key, value in values.items() if value is not None}


async def _write_audit(
    action_name: str,
    *,
    reference: str,
    subscription_id: str,
    reason: str,
    metadata: Mapping[str, Any],
    required: bool,
) -> None:
    """Write one audit act. ``required`` decides whether a storage failure is retried.

    ``required=True`` uses ``record_or_raise`` and converts a storage failure into
    :class:`SettlementPersistenceError`, so the settlement is retried rather than left recorded
    but unaudited - Requirement 10.9 conditions the record on being auditable. ``required=False``
    uses the never-raising ``log`` and is used only by the give-up path, where there is nothing
    left to escalate to.

    The audit facility is imported lazily for the reason ``submission_service`` gives: this module
    must stay importable and testable without pulling the audit stack, its Redis handle or an
    event loop behind it.
    """
    try:
        from backend_app.core.audit_trail import (
            StrategyAuditAction,
            get_strategy_audit_logger,
        )
    except Exception as exc:  # noqa: BLE001 - a defined outcome either way, never swallowed
        if required:
            raise SettlementPersistenceError(
                f"the audit facility is unavailable, so {action_name} for {reference} cannot be "
                f"recorded: {exc}"
            ) from exc
        logger.error(
            "the audit facility is unavailable, so %s for %s was not recorded: %s",
            action_name,
            reference,
            exc,
        )
        return

    action = getattr(StrategyAuditAction, action_name, None)
    audit_logger = get_strategy_audit_logger()
    writer = getattr(audit_logger, "record_or_raise", None) if required else None
    if not callable(writer):
        writer = getattr(audit_logger, "log", None)
    if action is None or not callable(writer):
        if required:
            raise SettlementPersistenceError(
                f"no audit writer for {action_name}, so the settlement for {reference} cannot be "
                f"recorded"
            )
        logger.error("no audit writer for %s (provider reference %s)", action_name, reference)
        return

    try:
        await writer(
            action,
            actor_id="billing_integration",
            resource_type="marketplace_settlement",
            resource_id=reference,
            reason=reason,
            metadata=dict(metadata),
        )
    except SettlementPersistenceError:
        raise
    except Exception as exc:  # noqa: BLE001 - converted to the defined outcome, not swallowed
        if required:
            raise SettlementPersistenceError(
                f"the {action_name} audit write for {reference} did not complete: {exc}"
            ) from exc
        logger.error(
            "the %s audit write for %s did not complete: %s", action_name, reference, exc
        )


async def _audit_unmatched(
    *,
    reference: str,
    subscription_id: str,
    provider: str,
    amount_minor: int,
    currency: str,
    is_reversal: bool,
) -> None:
    """``MARKETPLACE_SETTLEMENT_UNMATCHED`` - the whole response to an uncorrelatable payment."""
    await _write_audit(
        "MARKETPLACE_SETTLEMENT_UNMATCHED",
        reference=reference,
        subscription_id=subscription_id,
        reason=(
            "a payment confirmation named a subscription that does not exist, so no settlement "
            "record, no transition and no entitlement were written"
        ),
        metadata=_audit_metadata(
            provider_reference=reference,
            provider=provider,
            subscription_id=subscription_id,
            amount_minor=amount_minor,
            currency=currency,
            is_reversal=is_reversal,
        ),
        required=True,
    )


async def _audit_mismatched(
    *,
    reference: str,
    subscription_id: str,
    provider: str,
    expected_amount_minor: Any,
    expected_currency: str,
    amount_minor: int,
    currency: str,
    is_reversal: bool,
) -> None:
    """``MARKETPLACE_SETTLEMENT_MISMATCHED`` - both figures, for the operator."""
    await _write_audit(
        "MARKETPLACE_SETTLEMENT_MISMATCHED",
        reference=reference,
        subscription_id=subscription_id,
        reason=(
            "a payment confirmation carried an amount or currency the subscription did not "
            "record, so no settlement record, no transition and no entitlement were written"
        ),
        metadata=_audit_metadata(
            provider_reference=reference,
            provider=provider,
            subscription_id=subscription_id,
            expected_amount_minor=expected_amount_minor,
            expected_currency=expected_currency or None,
            confirmed_amount_minor=amount_minor,
            confirmed_currency=currency,
            is_reversal=is_reversal,
        ),
        required=True,
    )


async def audit_uncorrelated_refund(
    *,
    provider_reference: str,
    provider: str,
    amount_minor: Any = None,
    currency: Optional[str] = None,
    event_name: Optional[str] = None,
    reason_detail: Optional[str] = None,
) -> None:
    """``MARKETPLACE_SETTLEMENT_UNMATCHED`` for a refund no Settlement_Record answers to.

    Task 19.16, Requirements 9.14, 10.8. :func:`find_settled_payment` returned ``None``: no
    payment was ever settled under the reference the refund names, so there is no Subscription to
    reverse and guessing one would move an entitlement and an owner's earnings on an inference.
    Nothing is written to the ledger; this line, plus the caller's error log, is the whole
    response, and it carries the provider reference an operator reconciles by.

    ``required=False``: nothing was recorded, so there is no record to condition on being
    auditable (contrast Requirement 10.9), and a failing audit facility must not turn a
    write-nothing outcome into a webhook 500 that the provider redelivers forever.
    """
    await _write_audit(
        "MARKETPLACE_SETTLEMENT_UNMATCHED",
        reference=_require_text(provider_reference, "provider_reference"),
        subscription_id="",
        reason=(
            reason_detail
            or (
                "a refund named a provider transaction reference that no settlement record was "
                "written under, so no reversal record, no transition and no entitlement change "
                "were made"
            )
        ),
        metadata=_audit_metadata(
            provider_reference=provider_reference,
            provider=provider,
            amount_minor=amount_minor,
            currency=currency,
            event=event_name,
            is_reversal=True,
        ),
        required=False,
    )


async def _audit_duplicate_ignored(
    *,
    reference: str,
    subscription_id: str,
    provider: str,
    amount_minor: int,
    currency: str,
    is_reversal: bool,
) -> None:
    """``MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED`` (Requirements 9.6, 10.10)."""
    await _write_audit(
        "MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED",
        reference=reference,
        subscription_id=subscription_id,
        reason=(
            "this provider reference has already been settled, so the redelivery added no "
            "settlement record and extended no period"
        ),
        metadata=_audit_metadata(
            provider_reference=reference,
            provider=provider,
            subscription_id=subscription_id,
            amount_minor=amount_minor,
            currency=currency,
            is_reversal=is_reversal,
            constraint=SETTLEMENT_REFERENCE_UNIQUE_CONSTRAINT,
        ),
        required=True,
    )


async def _audit_duplicate_resumed(
    *,
    reference: str,
    subscription_id: str,
    provider: str,
    amount_minor: int,
    currency: str,
    is_reversal: bool,
) -> None:
    """``MARKETPLACE_SETTLEMENT_DUPLICATE_RESUMED`` (MKT-03).

    Written when a redelivery's own ledger insert found a row an EARLIER process already wrote,
    and the Subscription's fresh state showed the transition that should have followed it never
    landed. Distinct from ``MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED``, which an operator reads as
    "nothing to see" - this line says a payment was found recorded but unactivated, and names
    which provider reference it was, so "how often does this happen" is answerable without
    reconstructing it from the transition history.
    """
    await _write_audit(
        "MARKETPLACE_SETTLEMENT_DUPLICATE_RESUMED",
        reference=reference,
        subscription_id=subscription_id,
        reason=(
            "this provider reference was already recorded in the ledger by an earlier attempt "
            "that did not finish; the Subscription's transition and entitlement are being "
            "completed now rather than left stuck behind an already-written settlement row"
        ),
        metadata=_audit_metadata(
            provider_reference=reference,
            provider=provider,
            subscription_id=subscription_id,
            amount_minor=amount_minor,
            currency=currency,
            is_reversal=is_reversal,
            constraint=SETTLEMENT_REFERENCE_UNIQUE_CONSTRAINT,
        ),
        required=True,
    )


async def _audit_settlement_created(
    *,
    reference: str,
    subscription_id: str,
    provider: str,
    amount_minor: int,
    owner_share_minor: int,
    platform_fee_minor: int,
    currency: str,
    is_reversal: bool,
    state: _Attempt,
) -> None:
    """``MARKETPLACE_SETTLEMENT_CREATED`` with the amount and both split components (Req 10.9).

    Also written for a reversal, which is what Requirement 10.9 asks for: a refund is a
    Settlement_Record too, and the ``is_reversal`` member of the metadata is how a reader tells
    the two apart.
    """
    await _write_audit(
        "MARKETPLACE_SETTLEMENT_CREATED",
        reference=reference,
        subscription_id=subscription_id,
        reason=(
            "a settlement reversal was recorded and the subscription was refunded"
            if is_reversal
            else "a confirmed payment was recorded and the subscription period was written"
        ),
        metadata=_audit_metadata(
            provider_reference=reference,
            provider=provider,
            subscription_id=subscription_id,
            settlement_id=state.settlement_id,
            amount_minor=amount_minor,
            owner_share_minor=owner_share_minor,
            platform_fee_minor=platform_fee_minor,
            currency=currency,
            is_reversal=is_reversal,
            from_state=state.from_status,
            to_state=state.to_status,
            period_start=state.period_start.isoformat() if state.period_start else None,
            period_expiry=state.period_expiry.isoformat() if state.period_expiry else None,
            entitlement_granted=state.entitlement_granted,
        ),
        required=True,
    )


async def _audit_persist_failed(
    *,
    reference: str,
    subscription_id: str,
    provider: str,
    amount_minor: int,
    currency: str,
    is_reversal: bool,
    attempts: int,
    cause: Optional[BaseException],
    state: _Attempt,
) -> None:
    """``MARKETPLACE_SETTLEMENT_PERSIST_FAILED`` with the reference to reconcile by (Req 10.11).

    Written with the never-raising ``log``: this is already the give-up path, and a failure to
    record the failure has nowhere left to escalate to. It is logged at error level as well, so
    the event is visible even if the audit sink is the thing that is down.
    """
    logger.error(
        "settlement for provider reference %s did not persist after %s attempt(s); the ledger is "
        "unchanged and the payment enters no earnings figure: %s",
        reference,
        attempts,
        cause,
    )
    await _write_audit(
        "MARKETPLACE_SETTLEMENT_PERSIST_FAILED",
        reference=reference,
        subscription_id=subscription_id,
        reason=(
            "a payment confirmation could not be persisted within its retry budget, so the "
            "ledger is unchanged and the payment needs operator reconciliation"
        ),
        metadata=_audit_metadata(
            provider_reference=reference,
            provider=provider,
            subscription_id=subscription_id,
            amount_minor=amount_minor,
            currency=currency,
            is_reversal=is_reversal,
            attempts=attempts,
            settlement_recorded=state.settlement_recorded,
            transition_written=state.transition_written,
            entitlement_granted=state.entitlement_granted,
            cause=_truncate(str(cause)) if cause is not None else None,
        ),
        required=False,
    )


def _persist_failed_result(
    state: _Attempt,
    *,
    reference: str,
    subscription_id: str,
    is_reversal: bool,
    attempts: int,
) -> SettlementResult:
    """The :class:`SettlementResult` carried on :class:`SettlementPersistFailed`."""
    return SettlementResult(
        outcome=SettlementOutcome.PERSIST_FAILED,
        provider_reference=reference,
        is_reversal=is_reversal,
        subscription_id=subscription_id,
        settlement_id=state.settlement_id,
        from_status=state.from_status,
        to_status=state.to_status,
        period_start=state.period_start,
        period_expiry=state.period_expiry,
        period_written=False,
        entitlement_granted=state.entitlement_granted,
        attempts=attempts,
    )


# ══════════════════════════════════════════════════════════════════════════
# SMALL PURE UTILITIES
# ══════════════════════════════════════════════════════════════════════════


def _backoff_for(attempt: int) -> int:
    """The wait, in whole seconds, before the attempt after ``attempt``.

    Bounded: the schedule is a fixed tuple, so the wait cannot grow without limit and the total
    stays inside :data:`SETTLEMENT_RETRY_WINDOW_SECONDS` (Requirement 10.11).
    """
    index = min(max(attempt, 1), len(SETTLEMENT_BACKOFF_SECONDS)) - 1
    return SETTLEMENT_BACKOFF_SECONDS[index]


def _is_duplicate_reference(exc: BaseException) -> bool:
    """Whether ``exc`` is ``uq_settlement_reference_reversal`` refusing a second row.

    Matched on the constraint name **and** on the ``23505`` SQLSTATE, because a driver may surface
    either: ``psycopg2`` carries ``pgcode`` and the constraint name in ``diag``, while
    ``supabase-py`` returns a ``dict``-shaped error whose ``message`` carries both as text.
    Matching only the SQLSTATE would swallow a *different* unique violation as a duplicate
    settlement, so the constraint name is required whenever any name is available.
    """
    text = " ".join(
        part
        for part in (
            str(exc),
            str(getattr(exc, "message", "") or ""),
            str(getattr(exc, "details", "") or ""),
            str(getattr(exc, "code", "") or ""),
            str(getattr(exc, "pgcode", "") or ""),
            str(getattr(getattr(exc, "diag", None), "constraint_name", "") or ""),
        )
        if part
    ).lower()
    if SETTLEMENT_REFERENCE_UNIQUE_CONSTRAINT in text:
        return True
    # A bare 23505 with no constraint name: the only unique constraint this module's inserts can
    # violate on ``marketplace_settlements`` is the reference one, so it is read as a duplicate -
    # but only when the table is named, so a violation from another statement cannot borrow it.
    return _UNIQUE_VIOLATION_SQLSTATE in text and SETTLEMENT_TABLE in text


def _amount_matches(recorded: Any, confirmed: int) -> bool:
    """Whether the Subscription's recorded amount equals the confirmed one, exactly.

    ``None`` never matches: a Subscription with no ``price_minor`` recorded nothing to agree with,
    so a confirmation for it is mismatched rather than accepted. The comparison is integer to
    integer - a recorded value that is not an admissible integer number of Minor_Units also fails,
    because there is no non-``float`` reading of it that this module would trust.
    """
    if recorded is None:
        return False
    if isinstance(recorded, bool) or not isinstance(recorded, int):
        # A driver may hand back the BIGINT as text. An exact integer reading is accepted; a
        # fractional or non-numeric one is not, and is never coerced through ``float``.
        try:
            recorded_int = int(str(recorded).strip())
        except (TypeError, ValueError):
            return False
        return recorded_int == confirmed
    return recorded == confirmed


def _rows(response: Any) -> List[Mapping[str, Any]]:
    """The rows of a PostgREST response, or ``[]``. Raises when the response signals an error.

    The same reading ``checkout_service._rows`` and ``entitlement_resolver._rows`` use: rows may
    arrive on ``.data``, under a ``["data"]`` key, as the single mapping ``.single()`` returns, or
    as a bare list in a test double.

    A response carrying a non-empty ``error`` is a statement that DID NOT COMPLETE and raises
    :class:`SettlementPersistenceError`. Reading it as "no rows" is what would turn a failed
    insert into a silent success and a failed read into an unmatched payment.
    """
    error = getattr(response, "error", None)
    if error is None and isinstance(response, Mapping):
        error = response.get("error")
    if error:
        raise SettlementPersistenceError(f"the statement returned an error: {error}")

    if response is None:
        return []
    if isinstance(response, Mapping):
        data = response.get("data", response) if "data" in response else response
    else:
        data = getattr(response, "data", response)
    if data is None:
        return []
    if isinstance(data, Mapping):
        return [data]
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        return [r for r in data if isinstance(r, Mapping)]
    return []


def _get(obj: Any, key: str) -> Any:
    """Read ``key`` from a mapping or as an attribute off an object. ``None`` when absent."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _as_text(value: Any) -> Optional[str]:
    """``value`` as text, or ``None`` when it is ``None``. Ids may be UUIDs or ints."""
    if value is None:
        return None
    return str(value)


def _require_text(value: Any, what: str) -> str:
    """``value`` as non-empty text, or ``ValueError``.

    A blank provider reference or subscription id is a programming error at the call site, not an
    outcome a webhook can act on: without a reference there is nothing for
    ``uq_settlement_reference_reversal`` to deduplicate on, so idempotency would be silently gone.
    """
    text = _as_text(value)
    if not text or not text.strip():
        raise ValueError(f"{what} is required")
    return text.strip()


def _normalise_currency(value: Any) -> str:
    """A currency code as upper-case text. ``''`` when absent, which matches no Subscription."""
    if value is None:
        return ""
    return str(value).strip().upper()


def _normalise_status(value: Any) -> Optional[str]:
    """A ``library_subscriptions.status`` value as lowercase text, or ``None``."""
    if value is None:
        return None
    return str(value).strip().lower()


def _utc_now() -> datetime:
    """The current instant in UTC. The only clock read in this module, and no period uses it."""
    return datetime.now(timezone.utc)


def _coerce_instant(value: Optional[datetime]) -> datetime:
    """``value`` as a ``timezone.utc`` instant, defaulting to now. For timestamps, not periods."""
    if value is None:
        return _utc_now()
    return ensure_utc(value)


def _parse_instant(value: Any) -> Optional[datetime]:
    """A stored ``TIMESTAMPTZ`` as a ``timezone.utc`` datetime, or ``None``.

    Accepts a ``datetime`` (what a driver that maps types returns) and an ISO-8601 string (what
    PostgREST returns over JSON), including the ``Z`` spelling. A value that parses to a naive
    datetime is read as UTC, because that is what the column stores; a value that does not parse
    at all raises, since guessing a period boundary is how a wrong expiry gets written.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_utc(value if value.tzinfo else value.replace(tzinfo=timezone.utc))
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SettlementPersistenceError(
            f"a stored subscription timestamp could not be read: {value!r} ({exc})"
        ) from exc
    return ensure_utc(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))


def _truncate(text: str) -> str:
    """``text`` bounded to :data:`_MAX_CAUSE_CHARS`, flattened onto one line."""
    flattened = " ".join(str(text).split())
    if len(flattened) <= _MAX_CAUSE_CHARS:
        return flattened
    return flattened[: _MAX_CAUSE_CHARS - 1] + "\u2026"
