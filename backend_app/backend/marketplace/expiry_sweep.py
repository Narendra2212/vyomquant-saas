"""
backend_app/backend/marketplace/expiry_sweep.py - the Subscription expiry sweep.

Spec: marketplace-subscriptions-paper-trading task 20.1. ``design.md`` ->
"``marketplace/expiry_sweep.py`` and its worker". Requirements 11.8, 11.10, 11.12, 11.15,
24.4, 26.2.

Exposes
-------
SWEEP_INTERVAL_SECONDS      30 - the hosting interval, written once (Requirement 11.8)
SWEEP_TARGET_STATE          ``SubscriptionState.EXPIRED`` - the ONLY state this module writes
EXPIRABLE_STATES            the states that may expire, DERIVED from
                            ``SUBSCRIPTION_TRANSITIONS`` rather than transcribed
EXPIRABLE_STATUS_TEXTS      the same set as the persisted lowercase column spellings
TRANSITION_CAUSE            ``'expiry_sweep'`` - the ``library_subscription_transitions.cause``
AUDIT_REASON_CODE           ``'SUBSCRIPTION_EXPIRED'`` - the audited act's own name
SWEEP_CANDIDATE_SELECT      the explicit projection the candidate read requests
STOPPABLE_DEPLOYMENT_STATUSES / STOPPABLE_SESSION_STATES
                            the rows Requirement 11.15 stops, and only those
METRIC_*                    the four metric names ``design.md`` -> "Observability" lists
SweepOutcome                the frozen value type :func:`sweep` returns
ExpiredSubscription         one expired row, as the sweep learned it
ExpirySweepFailed           the defined error outcome - a failure is RAISED, never swallowed
sweep(supabase, now)        the one entry point
stop_running_sessions_and_deployments(rows, supabase, now)   Requirement 11.15's enforcement
last_run_at()               the health-check input ``marketplace.expiry_sweep.last_run_at``
metrics_snapshot()          the four metrics as one mapping, for a health endpoint

THE MOST IMPORTANT THING ABOUT THIS MODULE: IT IS NOT THE AUTHORITY ON ENTITLEMENT
---------------------------------------------------------------------------------
This sweep is **housekeeping and enforcement**. It is not, and must never become, the thing
that decides whether a caller may execute a Listing's strategy.

``entitlement_resolver.resolve`` compares the injected ``now`` with the Subscription's own
``period_expiry`` on **every** call, and treats a null or past expiry as non-entitling *before*
it would trust the stored ``status`` label. So if this worker is dead, mis-scheduled, or has
never run at all:

  * access still ends **at the expiry instant**, to the microsecond (Requirement 11.7, property
    P-11). A row that still reads ``status = 'active'`` because the sweep never ran is still
    refused by the resolver the moment ``now`` reaches its ``period_expiry``;
  * what lags is only (a) the stored ``status`` label - a *description* of a fact the expiry
    timestamp already established - and (b) the session-stopping action of Requirement 11.15,
    which is a side effect on already-running work, not an admission decision.

Nothing in this module is load-bearing for an access decision, and that is a property of the
code rather than a promise about it:

  * this module has **no** ``resolve``-shaped function, no "is this entitling" helper and no
    caller in an admission path. It is imported by exactly one thing - its worker;
  * it never *reads* an entitlement decision either: it does not import
    ``entitlement_resolver``, so the sweep cannot become a second opinion on one;
  * the only column it writes on ``library_subscriptions`` is ``status`` (plus ``updated_at``).
    It never writes ``period_expiry``, ``period_start`` or ``expires_at``, so it cannot move the
    instant the resolver compares against. :data:`_FORBIDDEN_UPDATE_COLUMNS` asserts that at
    runtime, on every sweep.

IT CAN ONLY EXPIRE - IT CAN NEVER PRODUCE AN ``active`` ROW
-----------------------------------------------------------
:data:`SWEEP_TARGET_STATE` is ``EXPIRED`` and is the single place the target is named. The
update payload is built by :func:`_expiry_payload`, which asserts the status it is about to
write is exactly :data:`SWEEP_TARGET_STATE`'s column text, and the module-level assertion below
refuses to import at all if that target is ``ACTIVE``. Requirement 11.6 makes a transition into
``ACTIVE`` conditional on a Settlement_Record; a housekeeping worker holds no payment evidence
and therefore may never write it. Two layers below this module say the same thing
independently: ``marketplace_subscription_allowed_transitions`` has no ``expired -> active``
edge reachable without the guard's payment check, and
``trg_subscription_transition_guard`` refuses it in the database.

WHY RUNNING IT n TIMES IS THE SAME AS RUNNING IT ONCE (property P-13)
---------------------------------------------------------------------
The predicate is ``period_expiry IS NOT NULL AND period_expiry <= now AND status IN
('active','suspended')``. ``'expired'`` is not in that status set, so after the first run **no
row the first run touched still satisfies the predicate** - the second run selects nothing,
updates nothing, writes no transition row and no audit entry. Idempotence is a consequence of
the predicate, not of a bookkeeping table, a marker column or a "have I run" flag. Task 20.2
asserts it as property P-13.

The three clauses each carry weight, and each is exercised by a test:

  * ``period_expiry IS NOT NULL`` - a Subscription with no period has not expired; it has no
    period. (In SQL the ``<=`` comparison already discards NULL, because ``NULL <= x`` is
    unknown; the clause is written explicitly anyway, because a predicate that relies on
    three-valued logic to exclude a row is one refactor away from including it.)
  * ``period_expiry <= now`` - the boundary is *at* the expiry instant, matching the resolver's
    ``now >= period_expiry``. The two comparisons agree, which is what makes P-11 and P-13
    consistent with each other.
  * ``status IN ('active','suspended')`` - the two states Requirement 11.2 permits an edge to
    ``EXPIRED`` from, and the reason ``cancelled``, ``refunded``, ``pending``,
    ``payment_failed`` and an already-``expired`` row are all left untouched. The set is
    **derived** from :data:`~subscription_state.SUBSCRIPTION_TRANSITIONS` (see
    :data:`EXPIRABLE_STATES`), so a change to the state machine cannot leave this predicate
    behind.

WHY THERE IS A CANDIDATE READ IN FRONT OF THE ONE UPDATE
--------------------------------------------------------
``library_subscription_transitions.from_state`` is ``TEXT NOT NULL`` (008 section 3): Requirement
11.12 wants the prior value recorded, and a history row with a guessed prior state would be
worse than no history at all. PostgREST's ``Prefer: return=representation`` returns the row
image **after** the update, so the one UPDATE cannot tell us whether a given row was ``active``
or ``suspended`` a moment ago.

So :func:`_read_candidates` issues one read of ``(id, user_id, library_id, status,
period_expiry)`` under exactly the sweep predicate, and the UPDATE is then narrowed to those
ids. That narrowing is a *restriction* of the same predicate, never a relaxation of it:

  * the UPDATE still carries all three clauses itself, so a row whose status changed between the
    read and the write (a purchaser cancelling, a renewal landing) is **not** expired by this
    run - it simply falls out of the UPDATE's own ``status IN (…)`` filter and is reconsidered
    30 seconds later. The read proposes; the UPDATE decides;
  * every id the UPDATE returns is therefore in the candidate map, so ``from_state`` is a fact
    read from the row rather than an inference;
  * both statements are served by ``idx_lib_subs_expiry ON library_subscriptions (period_expiry)
    WHERE status IN ('active','suspended')`` (Requirement 24.4) - the partial index matches the
    predicate exactly, so the sweep's cost is proportional to the number of *expiring*
    subscriptions and not to the size of the table.

There is still exactly **one write statement per sweep** against ``library_subscriptions``, and
one transition insert plus one audit entry per expired row, which is what Requirements 11.8 and
11.12 ask for. Nothing is written per candidate that did not expire.

WHY A FAILURE IS RAISED AND NEVER SWALLOWED (Requirements 26.5, 30.5)
--------------------------------------------------------------------
A sweep that swallowed a failure would leave Subscriptions unexpired with **no signal**: the
stored labels would drift, the running deployments of lapsed subscribers would keep running, and
the only evidence would be an absence. So:

  * there is no bare ``except`` and no broad ``except`` that returns a value. Every ``except``
    in this module either re-raises :class:`ExpirySweepFailed` (a *defined* outcome, chained from
    the driver error) or records the failure on the outcome and then raises at the end of the
    sweep;
  * a PostgREST response carrying a non-empty ``error`` envelope is a failure, not "no rows";
  * :func:`last_run_at` advances only on a **fully successful** sweep. A sweep that raised
    leaves it where it was, so the health check that watches
    ``marketplace.expiry_sweep.last_run_at`` goes stale and trips. Recording the attempt would
    make a permanently broken sweep look permanently healthy, which is the failure mode the
    health-check input exists to catch;
  * per-row failures do not abandon the remaining rows. One row whose history insert fails must
    not cost the other nineteen their audit entries, so the loop completes and *then* raises
    with every failure attached.

WHAT REQUIREMENT 11.15 CAN AND CANNOT DO TODAY
----------------------------------------------
:func:`stop_running_sessions_and_deployments` is Requirement 11.15's enforcement, and it is the
sweep's job because the sweep is the only component that learns *when* an expiry happened
without being asked.

  * **Deployments - fully implemented.** ``strategy_deployments`` rows whose
    ``marketplace_listing_id`` is the expired Subscription's Listing and whose ``user_id`` is
    that purchaser are moved to ``'stopped'`` with ``stopped_at`` recorded, scoped to the
    statuses that can be stopped (:data:`STOPPABLE_DEPLOYMENT_STATUSES`). This is the durable
    record the deployment runtime reads.
  * **Paper_Sessions - the persisted half only, deliberately.** The ``paper_sessions`` table
    exists (009_paper_trading.sql), so the sweep moves a ``RUNNING`` or ``PAUSED`` session for
    that Listing owned by that purchaser to ``'STOPPED'`` with ``stopped_at`` recorded. Those
    are the only two source states ``paper_session_allowed_transitions`` permits an edge to
    ``STOPPED`` from, so ``trg_paper_session_guard`` agrees with the filter rather than refusing
    it.

    **What is deferred to task 27.1**, and is honestly not done here: there is no
    Paper_Session_Service yet - no ``paper_session_service.stop_session``, no market-data feed to
    disconnect, no simulation loop to halt, no ``paper_events`` stop event, no WebSocket channel
    to close and no open-order cancellation. Nothing in this repository can currently *start* a
    Paper_Session, so today this update matches zero rows in practice, and this module does not
    pretend otherwise: it claims only the state write it actually performs, and
    :attr:`SweepOutcome.paper_runtime_stop_deferred` says so in the outcome the worker logs.
    When 27.1 lands, the sessions it starts are stopped by this sweep from the first run, and
    27.1 adds the runtime teardown call beside the state write.

  * The 60-second bound of Requirement 11.15 is met by the same margin as Requirement 11.8's:
    hosted at :data:`SWEEP_INTERVAL_SECONDS` (30), the enforcement runs in the same sweep pass
    that discovers the expiry, so the worst case is one interval.

WHY THIS MODULE IMPORTS NO FastAPI AND READS NO CLOCK OF ITS OWN
---------------------------------------------------------------
The layering rule ``design.md`` -> "Architecture" states, and ``entitlement_resolver``,
``eligibility_gate`` and ``checkout_service`` already follow: the ``supabase`` handle is
**passed in** and ``now`` is **passed in**, so the whole sweep is deterministic against a
recording double and the P-13 property test needs no database, no clock and no event loop but
its own. ``errors.py`` imports FastAPI, so it is not imported here; the one catalogue code this
module refers to travels as its string spelling on :attr:`ExpirySweepFailed.wire_code`, the same
convention ``entitlement_resolver`` uses.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from backend_app.backend.marketplace.subscription_state import (
    STATUS_TEXT_FOR_STATE,
    SUBSCRIPTION_TRANSITIONS,
    SubscriptionState,
    can_transition,
)

logger = logging.getLogger("MarketplaceExpirySweep")

# The catalogue code spelling, by string only. ``errors.py`` imports FastAPI and this module
# does not - the same reason ``entitlement_resolver`` mirrors its codes rather than importing
# them. ``tests/test_expiry_sweep.py`` asserts this equals ``errors.MARKETPLACE_READ_FAILED``.
_MARKETPLACE_READ_FAILED = "MARKETPLACE_READ_FAILED"

__all__ = [
    "SWEEP_INTERVAL_SECONDS",
    "SWEEP_TARGET_STATE",
    "EXPIRABLE_STATES",
    "EXPIRABLE_STATUS_TEXTS",
    "EXPIRED_STATUS_TEXT",
    "TRANSITION_CAUSE",
    "AUDIT_REASON_CODE",
    "AUDIT_RESOURCE_TYPE",
    "SUBSCRIPTIONS_TABLE",
    "TRANSITIONS_TABLE",
    "DEPLOYMENTS_TABLE",
    "PAPER_SESSIONS_TABLE",
    "SWEEP_CANDIDATE_SELECT",
    "STOPPABLE_DEPLOYMENT_STATUSES",
    "STOPPABLE_SESSION_STATES",
    "STOPPED_DEPLOYMENT_STATUS",
    "STOPPED_SESSION_STATE",
    "METRIC_DURATION_MS",
    "METRIC_TRANSITIONS",
    "METRIC_ERRORS",
    "METRIC_LAST_RUN_AT",
    "ExpiredSubscription",
    "SweepFailure",
    "SweepOutcome",
    "ExpirySweepFailed",
    "sweep",
    "stop_running_sessions_and_deployments",
    "last_run_at",
    "metrics_snapshot",
    "reset_metrics",
]


# ══════════════════════════════════════════════════════════════════════════
# THE INTERVAL, THE TARGET, AND THE PREDICATE'S STATUS SET
# ══════════════════════════════════════════════════════════════════════════

#: The hosting interval, in seconds. Requirement 11.8 permits "no greater than 60 seconds";
#: 30 is half of that, so the bound holds with a full interval of margin even when one pass is
#: skipped by backpressure or takes longer than expected. Written once, here, so the value the
#: worker schedules and the value the test asserts are the same literal.
SWEEP_INTERVAL_SECONDS: float = 30.0

#: The one state this sweep writes, and the only one it can write. See the module docstring:
#: a housekeeping worker holds no Settlement_Record and therefore may never produce ``ACTIVE``
#: (Requirement 11.6).
SWEEP_TARGET_STATE: SubscriptionState = SubscriptionState.EXPIRED

# The import-time refusal. If a future edit points the target at ``ACTIVE`` - or at any state
# whose meaning is "entitled" - this module does not load, so the mistake is a red suite at
# collection rather than an unpaid activation in production.
assert SWEEP_TARGET_STATE is not SubscriptionState.ACTIVE, (
    "the expiry sweep may only expire. Reaching ACTIVE requires a payment confirmed through "
    "the Billing_Integration and recorded as a Settlement_Record (Requirement 11.6), which a "
    "housekeeping worker does not have."
)

#: The states a Subscription may expire *from*. **Derived** from Requirement 11.2's transition
#: table - every state whose permitted targets include ``EXPIRED`` - rather than transcribed as
#: ``{ACTIVE, SUSPENDED}``. If the state machine ever gains or loses such an edge, this
#: predicate follows it instead of silently disagreeing with it.
EXPIRABLE_STATES: FrozenSet[SubscriptionState] = frozenset(
    state
    for state, targets in SUBSCRIPTION_TRANSITIONS.items()
    if SWEEP_TARGET_STATE in targets
)

#: The same set as the persisted lowercase ``library_subscriptions.status`` spellings, resolved
#: through the one enum -> column mapping rather than re-spelled as literals. Sorted so the
#: emitted filter is stable and comparable in a test.
EXPIRABLE_STATUS_TEXTS: Tuple[str, ...] = tuple(
    sorted(STATUS_TEXT_FOR_STATE[state] for state in EXPIRABLE_STATES)
)

#: The status text the sweep writes.
EXPIRED_STATUS_TEXT: str = STATUS_TEXT_FOR_STATE[SWEEP_TARGET_STATE]

# The predicate must exclude the state it writes, or the sweep would re-select its own output
# forever and P-13 would be false. Asserted at import for the same reason as the target check.
assert EXPIRED_STATUS_TEXT not in EXPIRABLE_STATUS_TEXTS, (
    "the sweep's status predicate must exclude the state it writes, or a second run would "
    "re-expire the rows the first run expired and the sweep would not be idempotent (P-13)."
)

# Every source state must have a *permitted* edge to the target. Derived above, so this holds by
# construction; asserted anyway, because "derived" is a claim about today's code.
assert all(can_transition(state, SWEEP_TARGET_STATE) for state in EXPIRABLE_STATES), (
    "every state the sweep expires from must have a permitted transition to EXPIRED "
    "(Requirement 11.2)."
)


# ══════════════════════════════════════════════════════════════════════════
# TABLES, PROJECTIONS AND THE COLUMNS THE SWEEP MAY NOT TOUCH
# ══════════════════════════════════════════════════════════════════════════

SUBSCRIPTIONS_TABLE = "library_subscriptions"
TRANSITIONS_TABLE = "library_subscription_transitions"
DEPLOYMENTS_TABLE = "strategy_deployments"
PAPER_SESSIONS_TABLE = "paper_sessions"

#: The candidate read's explicit projection. No ``select("*")``: five columns, each with a
#: reason. ``id`` identifies the row, ``status`` is the ``from_state`` Requirement 11.12 wants,
#: ``period_expiry`` is the instant recorded in the audit entry, and ``user_id`` +
#: ``library_id`` are the (purchaser, Listing) pair Requirement 11.15's enforcement is scoped
#: by. The set is bound by the ``expiry_sweep`` entry of this package's ``COLUMN_CONTRACT`` and
#: asserted a subset of it by ``tests/test_marketplace_paper_schema_contract.py``.
SWEEP_CANDIDATE_SELECT = "id,user_id,library_id,status,period_expiry"

#: Columns the sweep's ``library_subscriptions`` update may **never** carry. These are the
#: instants ``entitlement_resolver`` compares ``now`` against; a sweep that could move one of
#: them would be able to extend or curtail access, which is exactly the authority this module
#: must not have. ``expires_at`` is the legacy column ``check_deployment_permission`` reads as
#: *perpetual* when null (see ``checkout_service.OMITTED_ON_PENDING``), so it is refused for the
#: same reason. :func:`_expiry_payload` asserts none of them reached the payload.
_FORBIDDEN_UPDATE_COLUMNS: FrozenSet[str] = frozenset(
    {"period_expiry", "period_start", "expires_at", "renewal_enabled", "user_id", "library_id"}
)

#: ``library_subscription_transitions.cause`` for a sweep-driven expiry. The column is
#: ``TEXT NOT NULL`` with no CHECK (008 section 3: the vocabulary is the application's), and
#: this spelling is the one 008's own comment names for this path.
TRANSITION_CAUSE = "expiry_sweep"

#: The audited act's name, as it appears in the audit record's ``reason`` and metadata. The
#: ``StrategyAuditAction`` member used is ``MARKETPLACE_SUBSCRIPTION_TRANSITIONED`` - the member
#: Requirement 26.2 already allocates to "Subscription_State transition", added by task 14.3.
#: No new audit facility and no new enum member is introduced for the sweep: ``design.md``'s
#: ``audit(SUBSCRIPTION_EXPIRED)`` names the *act*, and this constant is where that name is
#: written so a log reader can grep for it.
AUDIT_REASON_CODE = "SUBSCRIPTION_EXPIRED"

#: The audit record's ``resource_type``. The resource is the Subscription row, so it is named
#: after the table that holds it rather than squeezed into one of the strategy resource kinds.
AUDIT_RESOURCE_TYPE = "library_subscription"

#: ``strategy_deployments.status`` values that can still be stopped. ``'stopped'`` and
#: ``'failed'`` are absent because a run that has already ended does not need stopping, and
#: writing to it would produce a spurious ``stopped_at``. The vocabulary is the one
#: ``001_strategy_architecture.sql`` documents on the column: deploying, running, paused,
#: stopped, failed.
STOPPABLE_DEPLOYMENT_STATUSES: Tuple[str, ...] = ("deploying", "running", "paused")

#: The status a stopped deployment carries.
STOPPED_DEPLOYMENT_STATUS = "stopped"

#: ``paper_sessions.session_state`` values that can be stopped. Exactly the two source states
#: ``paper_session_allowed_transitions`` gives a ``-> STOPPED`` edge (``RUNNING -> STOPPED``,
#: ``PAUSED -> STOPPED``), so ``trg_paper_session_guard`` agrees with this filter instead of
#: refusing the write with 23514. A ``CREATED`` session has not started and has no
#: ``CREATED -> STOPPED`` edge; it cannot run, so it does not need stopping.
STOPPABLE_SESSION_STATES: Tuple[str, ...] = ("RUNNING", "PAUSED")

#: The state a stopped Paper_Session carries.
STOPPED_SESSION_STATE = "STOPPED"


# ══════════════════════════════════════════════════════════════════════════
# THE METRICS (design.md -> "Observability", the expiry-sweep row)
# ══════════════════════════════════════════════════════════════════════════

METRIC_DURATION_MS = "marketplace.expiry_sweep.duration_ms"
METRIC_TRANSITIONS = "marketplace.expiry_sweep.transitions"
METRIC_ERRORS = "marketplace.expiry_sweep.errors"
#: The health-check input. See :func:`last_run_at` for why it advances only on success.
METRIC_LAST_RUN_AT = "marketplace.expiry_sweep.last_run_at"

# Process-local metric state. Deliberately module-level rather than instance state on the
# worker: a health endpoint asks "when did the sweep last complete" without holding a reference
# to the worker object, exactly as ``last_run_at`` is described in design.md. Reset by
# :func:`reset_metrics`, which exists for the tests and for nothing else.
_last_run_at: Optional[datetime] = None
_last_duration_ms: Optional[int] = None
_transitions_total: int = 0
_errors_total: int = 0


def last_run_at() -> Optional[datetime]:
    """The instant the last **fully successful** sweep completed, or ``None`` if none has.

    This is ``marketplace.expiry_sweep.last_run_at`` (Requirement 26.6's health-check input,
    ``design.md`` -> "Observability"). A liveness probe compares it against
    :data:`SWEEP_INTERVAL_SECONDS` and reports unhealthy when it falls further behind than a
    couple of intervals.

    It advances only when a sweep completed with **no** failure. A sweep that raised leaves it
    where it was on purpose: a permanently broken sweep that stamped this value on every attempt
    would look permanently healthy, and the operator would learn about the drift from a
    subscriber instead of from the probe.

    Note what a stale value does *not* mean: it does not mean anybody has extra access. The
    Entitlement_Resolver ends access at ``period_expiry`` whether this ever ran (Requirement
    11.7, P-11); a stale sweep means stored labels and running sessions are lagging, which is a
    housekeeping incident, not an authorisation one.
    """
    return _last_run_at


def metrics_snapshot() -> Dict[str, Any]:
    """The four metrics ``design.md`` names for this sweep, as one mapping.

    Emitted rather than asserted (Requirement 27.6's reading): the health endpoint and the
    worker's log line read this, and no correctness property depends on it.
    """
    return {
        METRIC_DURATION_MS: _last_duration_ms,
        METRIC_TRANSITIONS: _transitions_total,
        METRIC_ERRORS: _errors_total,
        METRIC_LAST_RUN_AT: _last_run_at.isoformat() if _last_run_at else None,
    }


def reset_metrics() -> None:
    """Clear the process-local metric state. For tests; nothing in production calls it."""
    global _last_run_at, _last_duration_ms, _transitions_total, _errors_total
    _last_run_at = None
    _last_duration_ms = None
    _transitions_total = 0
    _errors_total = 0


# ══════════════════════════════════════════════════════════════════════════
# THE DEFINED ERROR OUTCOME
# ══════════════════════════════════════════════════════════════════════════


class ExpirySweepFailed(Exception):
    """The sweep did not complete. Raised - never swallowed, never returned as a count.

    Carries enough for an operator to act without reading the log line that preceded it:

    ``stage``
        which part failed - ``'candidate_read'``, ``'expiry_update'``, ``'transition_insert'``,
        ``'audit_write'``, ``'stop_deployments'`` or ``'stop_paper_sessions'``.
    ``failures``
        every per-row failure the pass collected, so one broken row does not hide nineteen
        others.
    ``outcome``
        the partial :class:`SweepOutcome`, when the pass got far enough to have one. The rows
        listed on it **did** transition in the database; what failed is the bookkeeping or the
        enforcement around them, and an operator needs to know which.

    ``wire_code`` mirrors the shared catalogue's ``MARKETPLACE_READ_FAILED`` for the case where
    an HTTP surface (a health or admin endpoint) ever needs to answer with it. The sweep itself
    has no HTTP surface, so nothing maps it today.
    """

    wire_code: str = _MARKETPLACE_READ_FAILED

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        failures: Optional[Sequence["SweepFailure"]] = None,
        outcome: Optional["SweepOutcome"] = None,
    ) -> None:
        self.stage = stage
        self.failures: Tuple["SweepFailure", ...] = tuple(failures or ())
        self.outcome = outcome
        super().__init__(message)


# ══════════════════════════════════════════════════════════════════════════
# THE VALUE TYPES
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ExpiredSubscription:
    """One Subscription this sweep transitioned to ``EXPIRED``.

    Frozen: it is the record of a write that already happened. ``from_state`` is the prior
    status text read from the row (never inferred - see the module docstring), and
    ``period_expiry`` is the instant that made the row eligible, carried so the audit entry can
    record *why* without a second read.
    """

    subscription_id: str
    user_id: Optional[str]
    listing_id: Optional[str]
    from_state: str
    period_expiry: Optional[datetime]
    to_state: str = EXPIRED_STATUS_TEXT

    def __post_init__(self) -> None:
        # An ExpiredSubscription that claims a target other than EXPIRED cannot be built. The
        # sweep only expires, and the value type says so rather than trusting its constructor.
        if self.to_state != EXPIRED_STATUS_TEXT:
            raise ValueError(
                f"the expiry sweep produces {EXPIRED_STATUS_TEXT!r} and nothing else; "
                f"{self.to_state!r} is not a state it can write"
            )
        if self.from_state not in EXPIRABLE_STATUS_TEXTS:
            raise ValueError(
                f"{self.from_state!r} has no permitted transition to "
                f"{EXPIRED_STATUS_TEXT!r}; the expirable states are "
                f"{list(EXPIRABLE_STATUS_TEXTS)}"
            )


@dataclass(frozen=True)
class SweepFailure:
    """One thing that went wrong, attributed to a stage and (where there is one) a row."""

    stage: str
    message: str
    subscription_id: Optional[str] = None


@dataclass(frozen=True)
class SweepOutcome:
    """What one sweep pass did.

    ``expired`` is the authoritative list: every entry is a row the single UPDATE returned, so
    ``len(outcome.expired)`` is the ``RETURN |rows|`` of ``design.md``'s pseudo-code.
    """

    #: The rows that transitioned, in the order the UPDATE returned them.
    expired: Tuple[ExpiredSubscription, ...] = ()
    #: How many ``library_subscription_transitions`` rows were written - one per expired row.
    transitions_written: int = 0
    #: How many audit entries were written - one per expired row.
    audit_entries_written: int = 0
    #: How many ``strategy_deployments`` rows were moved to ``'stopped'`` (Requirement 11.15).
    deployments_stopped: int = 0
    #: How many ``paper_sessions`` rows were moved to ``'STOPPED'``.
    paper_sessions_stopped: int = 0
    #: The instant this pass ran against, echoed for the caller's log line.
    swept_at: Optional[datetime] = None
    #: How long the pass took, for ``marketplace.expiry_sweep.duration_ms``.
    duration_ms: int = 0
    #: Everything that went wrong. Non-empty means :func:`sweep` raised.
    failures: Tuple[SweepFailure, ...] = ()
    #: Honest about the half of Requirement 11.15 that cannot exist yet: the Paper_Session
    #: *runtime* teardown (feed disconnect, loop halt, stop event, channel close, open-order
    #: cancellation) is task 27.1's, because no Paper_Session_Service exists to hold it. The
    #: persisted ``session_state`` write above is real; this flag is the statement that the
    #: runtime action is not.
    paper_runtime_stop_deferred: bool = True

    @property
    def expired_count(self) -> int:
        """``|rows|`` - the number of Subscriptions this pass transitioned."""
        return len(self.expired)

    @property
    def ok(self) -> bool:
        """Whether the pass completed with no failure."""
        return not self.failures


# ══════════════════════════════════════════════════════════════════════════
# THE ENTRY POINT (Requirements 11.8, 11.10, 11.12, 11.15, 24.4, 26.2)
# ══════════════════════════════════════════════════════════════════════════


async def sweep(
    *,
    supabase: Any,
    now: Optional[datetime] = None,
    stop_running: bool = True,
    audit_writer: Optional[Any] = None,
) -> SweepOutcome:
    """Expire every Subscription whose period has ended, and stop what it was running.

    One pass. Idempotent by its own predicate: a second call with a ``now`` at or after the
    first finds no eligible row and changes nothing (property P-13).

    Args:
        supabase: the injected Persistence_Layer handle (a service-role client in the worker).
            Passed in rather than constructed, so the whole sweep runs against a recording
            double with no network, no credentials and no event loop of its own.
        now: the instant to compare ``period_expiry`` against, in UTC. Passed in so the
            boundary is deterministic under test - the same reason
            ``entitlement_resolver.resolve`` takes it. Defaults to the current UTC instant.
        stop_running: whether to run Requirement 11.15's enforcement. ``True`` in production;
            a test that is only interested in the state transition turns it off rather than
            asserting against writes it did not mean to exercise.
        audit_writer: an optional coroutine function with the ``StrategyAuditLogger.log``
            keyword signature. Injected so the audit write is observable without Redis;
            defaults to the existing shared ``StrategyAuditLogger`` (Requirement 26.2 - no
            second audit facility is introduced).

    Returns:
        A :class:`SweepOutcome`. ``expired`` lists the rows that transitioned.

    Raises:
        ExpirySweepFailed: any read, write, history insert, audit write or enforcement update
            did not complete. Nothing is swallowed: an unexpired Subscription with no signal is
            the failure mode this refusal exists to prevent.
    """
    global _last_run_at, _last_duration_ms, _transitions_total, _errors_total

    instant = _coerce_instant(now)
    started = time.monotonic()
    failures: List[SweepFailure] = []

    # ── 1. The candidate read. Its only job is to learn each row's PRIOR status, which
    #       PostgREST's post-update representation cannot tell us and which
    #       library_subscription_transitions.from_state requires (Requirement 11.12). ──
    #
    # The stages that raise *out* of this function (this read, and the update below) still have
    # to be counted on ``marketplace.expiry_sweep.errors`` before the exception leaves, or the
    # worst failure mode - a read that never completes, so nothing is ever expired - would be
    # the one that reported zero errors. Only the counter is touched; the exception is re-raised
    # unchanged, and ``_last_run_at`` is deliberately left where it was.
    try:
        candidates = _read_candidates(supabase, instant)
    except ExpirySweepFailed:
        _errors_total += 1
        _last_duration_ms = _elapsed_ms(started)
        raise
    if not candidates:
        # Nothing eligible. This is the *normal* case, and it is also what makes the second run
        # of P-13 a no-op: no update, no history row, no audit entry, no enforcement write.
        duration_ms = _elapsed_ms(started)
        _last_run_at = instant
        _last_duration_ms = duration_ms
        return SweepOutcome(swept_at=instant, duration_ms=duration_ms)

    # ── 2. THE one write statement against library_subscriptions. It carries the full
    #       predicate itself and is merely narrowed to the candidate ids, so a row whose
    #       status moved since the read is not expired by this pass. ──
    try:
        updated_rows = _expire_candidates(supabase, instant, list(candidates))
    except ExpirySweepFailed:
        # Counted for the same reason as the read above. Nothing transitioned, so there is no
        # partial outcome to attach and no history to write; the next pass tries again.
        _errors_total += 1
        _last_duration_ms = _elapsed_ms(started)
        raise

    expired: List[ExpiredSubscription] = []
    for row in updated_rows:
        subscription_id = _as_text(_get(row, "id"))
        if subscription_id is None:
            # A returned row with no id cannot be attributed to a candidate, audited or
            # enforced against. It is a broken driver response, not a successful expiry, so it
            # is recorded as a failure rather than counted or ignored.
            failures.append(
                SweepFailure(
                    stage="expiry_update",
                    message="the update returned a row with no id, so it cannot be recorded",
                )
            )
            continue
        candidate = candidates.get(subscription_id)
        if candidate is None:
            # Cannot happen while the update is narrowed to the candidate ids; recorded rather
            # than guessed at, because inventing a ``from_state`` is exactly what the candidate
            # read exists to avoid.
            failures.append(
                SweepFailure(
                    stage="expiry_update",
                    message=(
                        "the update returned a subscription that was not among the candidates, "
                        "so its prior state is unknown and no history row can be written"
                    ),
                    subscription_id=subscription_id,
                )
            )
            continue
        expired.append(candidate)

    # ── 3. One transitions insert and one audit entry per expired row (Requirements 11.12,
    #       26.2). A failure on one row does not abandon the others; every failure is
    #       collected and raised at the end. ──
    transitions_written = 0
    audit_written = 0
    for record in expired:
        try:
            _insert_transition(supabase, record, instant)
            transitions_written += 1
        except ExpirySweepFailed as exc:
            failures.append(
                SweepFailure(
                    stage="transition_insert",
                    message=str(exc),
                    subscription_id=record.subscription_id,
                )
            )
        try:
            await _write_audit(record, instant, audit_writer=audit_writer)
            audit_written += 1
        except ExpirySweepFailed as exc:
            failures.append(
                SweepFailure(
                    stage="audit_write",
                    message=str(exc),
                    subscription_id=record.subscription_id,
                )
            )

    # ── 4. Requirement 11.15's enforcement, in the same pass that discovered the expiry. ──
    deployments_stopped = 0
    sessions_stopped = 0
    if stop_running and expired:
        deployments_stopped, sessions_stopped, stop_failures = (
            await stop_running_sessions_and_deployments(
                expired, supabase=supabase, now=instant
            )
        )
        failures.extend(stop_failures)

    duration_ms = _elapsed_ms(started)
    outcome = SweepOutcome(
        expired=tuple(expired),
        transitions_written=transitions_written,
        audit_entries_written=audit_written,
        deployments_stopped=deployments_stopped,
        paper_sessions_stopped=sessions_stopped,
        swept_at=instant,
        duration_ms=duration_ms,
        failures=tuple(failures),
    )

    _last_duration_ms = duration_ms
    _transitions_total += transitions_written
    _errors_total += len(failures)

    if failures:
        # last_run_at is deliberately NOT advanced: see :func:`last_run_at`.
        logger.error(
            "expiry sweep expired %d subscription(s) but %d step(s) failed: %s",
            len(expired),
            len(failures),
            "; ".join(f"{f.stage}: {f.message}" for f in failures),
        )
        raise ExpirySweepFailed(
            f"the expiry sweep completed with {len(failures)} failure(s)",
            stage=failures[0].stage,
            failures=failures,
            outcome=outcome,
        )

    _last_run_at = instant
    logger.info(
        "expiry sweep expired %d subscription(s) in %d ms; stopped %d deployment(s) and "
        "%d paper session(s)",
        len(expired),
        duration_ms,
        deployments_stopped,
        sessions_stopped,
    )
    return outcome


# ══════════════════════════════════════════════════════════════════════════
# REQUIREMENT 11.15's ENFORCEMENT
# ══════════════════════════════════════════════════════════════════════════


async def stop_running_sessions_and_deployments(
    rows: Sequence[ExpiredSubscription],
    *,
    supabase: Any,
    now: datetime,
) -> Tuple[int, int, List[SweepFailure]]:
    """Stop every running deployment and Paper_Session for each expired Subscription.

    Scoped, per expired row, to that Listing's strategy **owned by that purchaser**: the
    ``(marketplace_listing_id, user_id)`` pair for a deployment and the ``(listing_id,
    user_id)`` pair for a session. Another subscriber's still-paid deployment of the same
    Listing is a different row and is not touched, which is the whole point of scoping by both
    columns rather than by the Listing alone.

    Read the module docstring's "WHAT REQUIREMENT 11.15 CAN AND CANNOT DO TODAY" before
    extending this: the deployment half is complete, the Paper_Session half writes the
    persisted ``session_state`` only, and the Paper_Session *runtime* teardown is task 27.1's
    because no Paper_Session_Service exists to hold it.

    Args:
        rows: the Subscriptions this pass expired.
        supabase: the injected Persistence_Layer handle.
        now: the UTC instant recorded as ``stopped_at``.

    Returns:
        ``(deployments_stopped, paper_sessions_stopped, failures)``. Failures are **returned**
        rather than raised so the caller can attribute them alongside the rest of the pass and
        raise once; :func:`sweep` does exactly that, so nothing is swallowed here either.
    """
    failures: List[SweepFailure] = []
    deployments_stopped = 0
    sessions_stopped = 0
    stamp = _isoformat(now)

    for record in rows:
        if not record.user_id or not record.listing_id:
            # Without both halves of the pair the update would be scoped by one column only,
            # which could stop another purchaser's deployment or every deployment of one
            # purchaser. Refused and recorded rather than issued broadly.
            failures.append(
                SweepFailure(
                    stage="stop_deployments",
                    message=(
                        "the expired subscription carries no purchaser/Listing pair, so a "
                        "correctly scoped stop cannot be issued for it"
                    ),
                    subscription_id=record.subscription_id,
                )
            )
            continue

        # ── Deployments: fully implemented. ──
        try:
            response = (
                supabase.table(DEPLOYMENTS_TABLE)
                .update(
                    {
                        "status": STOPPED_DEPLOYMENT_STATUS,
                        "stopped_at": stamp,
                        "updated_at": stamp,
                    }
                )
                .eq("marketplace_listing_id", record.listing_id)
                .eq("user_id", record.user_id)
                .in_("status", list(STOPPABLE_DEPLOYMENT_STATUSES))
                .execute()
            )
            deployments_stopped += len(_rows(response))
        except ExpirySweepFailed as exc:
            failures.append(
                SweepFailure(
                    stage="stop_deployments",
                    message=str(exc),
                    subscription_id=record.subscription_id,
                )
            )
        except Exception as exc:  # noqa: BLE001 - recorded as a defined outcome, never dropped
            failures.append(
                SweepFailure(
                    stage="stop_deployments",
                    message=f"the deployment stop did not complete: {exc}",
                    subscription_id=record.subscription_id,
                )
            )

        # ── Paper_Sessions: the persisted state write only. See the docstring above. ──
        try:
            response = (
                supabase.table(PAPER_SESSIONS_TABLE)
                .update(
                    {
                        "session_state": STOPPED_SESSION_STATE,
                        "stopped_at": stamp,
                        "updated_at": stamp,
                    }
                )
                .eq("listing_id", record.listing_id)
                .eq("user_id", record.user_id)
                .in_("session_state", list(STOPPABLE_SESSION_STATES))
                .execute()
            )
            sessions_stopped += len(_rows(response))
        except ExpirySweepFailed as exc:
            failures.append(
                SweepFailure(
                    stage="stop_paper_sessions",
                    message=str(exc),
                    subscription_id=record.subscription_id,
                )
            )
        except Exception as exc:  # noqa: BLE001 - recorded as a defined outcome, never dropped
            failures.append(
                SweepFailure(
                    stage="stop_paper_sessions",
                    message=f"the paper session stop did not complete: {exc}",
                    subscription_id=record.subscription_id,
                )
            )

    return deployments_stopped, sessions_stopped, failures


# ══════════════════════════════════════════════════════════════════════════
# THE READS AND WRITES
# ══════════════════════════════════════════════════════════════════════════


def _read_candidates(
    supabase: Any, now: datetime
) -> "Dict[str, ExpiredSubscription]":
    """The rows eligible to expire at ``now``, keyed by id, with their PRIOR status.

    Carries exactly the sweep predicate, so it is served by ``idx_lib_subs_expiry`` and returns
    nothing on a second pass (property P-13). See the module docstring for why this read exists
    at all rather than the single UPDATE's representation being enough.

    A candidate whose status is outside :data:`EXPIRABLE_STATUS_TEXTS` is dropped rather than
    trusted: the filter should have excluded it, and constructing an
    :class:`ExpiredSubscription` from it would raise anyway.

    Raises:
        ExpirySweepFailed: the read did not complete. Not ``{}`` - answering "nothing to expire"
            for a broken read is how a sweep silently stops sweeping.
    """
    try:
        response = (
            supabase.table(SUBSCRIPTIONS_TABLE)
            .select(SWEEP_CANDIDATE_SELECT)
            .not_.is_("period_expiry", "null")
            .lte("period_expiry", _isoformat(now))
            .in_("status", list(EXPIRABLE_STATUS_TEXTS))
            .execute()
        )
        rows = _rows(response)
    except ExpirySweepFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome, never swallowed
        raise ExpirySweepFailed(
            f"the expiry candidate read did not complete: {exc}",
            stage="candidate_read",
        ) from exc

    candidates: Dict[str, ExpiredSubscription] = {}
    for row in rows:
        subscription_id = _as_text(_get(row, "id"))
        status = _normalise_status(_get(row, "status"))
        if subscription_id is None or status not in EXPIRABLE_STATUS_TEXTS:
            continue
        candidates[subscription_id] = ExpiredSubscription(
            subscription_id=subscription_id,
            user_id=_as_text(_get(row, "user_id")),
            listing_id=_as_text(_get(row, "library_id")),
            from_state=status,
            period_expiry=_coerce_optional_instant(_get(row, "period_expiry")),
        )
    return candidates


def _expiry_payload(now: datetime) -> Dict[str, Any]:
    """The ``library_subscriptions`` update payload: the status, and the touch timestamp.

    Two columns, and the assertions that keep it to two. The status is
    :data:`EXPIRED_STATUS_TEXT` resolved through the one enum -> column mapping, and none of
    :data:`_FORBIDDEN_UPDATE_COLUMNS` may appear - so this sweep cannot move the
    ``period_expiry`` the Entitlement_Resolver compares ``now`` against, cannot reassign a
    Subscription's purchaser or Listing, and cannot re-enable renewal.
    """
    payload: Dict[str, Any] = {
        "status": EXPIRED_STATUS_TEXT,
        "updated_at": _isoformat(now),
    }
    # The sweep only expires. A payload that carried any other status - most of all ``active``,
    # which Requirement 11.6 conditions on a Settlement_Record - is refused here rather than
    # left to the database trigger to catch.
    assert payload["status"] == STATUS_TEXT_FOR_STATE[SWEEP_TARGET_STATE], (
        "the expiry sweep writes only the EXPIRED status"
    )
    leaked = _FORBIDDEN_UPDATE_COLUMNS & set(payload)
    assert not leaked, (
        f"the expiry sweep must not write {sorted(leaked)}: those columns are what the "
        f"Entitlement_Resolver decides access from, and this sweep is not the authority on "
        f"entitlement (Requirement 11.7)"
    )
    return payload


def _expire_candidates(
    supabase: Any, now: datetime, candidate_ids: Sequence[str]
) -> List[Mapping[str, Any]]:
    """THE one write statement: expire every still-eligible candidate, returning the rows.

    The full predicate is carried here, not only the id narrowing, for two reasons that both
    matter:

      * a row whose status changed between the candidate read and this write (a cancellation, a
        renewal landing) falls out of ``status IN (…)`` and is **not** expired by this pass. The
        read proposes; this statement decides;
      * the statement is therefore self-sufficient. Idempotence (P-13) is a property of *this*
        predicate: ``'expired'`` is not in the status set, so re-running it changes nothing.

    Raises:
        ExpirySweepFailed: the write did not complete. Nothing was transitioned; the next pass
            30 seconds later tries again.
    """
    try:
        response = (
            supabase.table(SUBSCRIPTIONS_TABLE)
            .update(_expiry_payload(now))
            .not_.is_("period_expiry", "null")
            .lte("period_expiry", _isoformat(now))
            .in_("status", list(EXPIRABLE_STATUS_TEXTS))
            .in_("id", list(candidate_ids))
            .execute()
        )
        return _rows(response)
    except ExpirySweepFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome, never swallowed
        raise ExpirySweepFailed(
            f"the expiry update did not complete: {exc}", stage="expiry_update"
        ) from exc


def _insert_transition(
    supabase: Any, record: ExpiredSubscription, now: datetime
) -> None:
    """One ``library_subscription_transitions`` row for one expiry (Requirement 11.12).

    ``prior_period_expiry`` and ``new_period_expiry`` are both **omitted**, deliberately. 008
    section 3's own comment sets the convention: "a transition that changes no period - a
    suspension, say - records NULL in both rather than repeating the unchanged value twice, so
    'the period moved' and 'the period did not' are distinguishable by reading the row." The
    sweep moves no period; it only relabels a period that already ended. The expiry instant
    that made the row eligible is still recorded - in the audit entry's metadata, where it
    belongs as evidence of *why* rather than as a period change that did not happen.

    ``actor_id`` is omitted for the same kind of reason: Requirement 11.12 asks for "the acting
    identity **where one applies**", and no identity acted. Writing the string ``"system"``
    into a UUID column would be a type error, and writing the purchaser's id would name the
    wrong actor - the purchaser did not expire their own subscription, time did.

    Raises:
        ExpirySweepFailed: the insert did not complete. The state transition already committed,
            so this is a history gap the caller must surface, not tidy away.
    """
    payload = {
        "subscription_id": record.subscription_id,
        "user_id": record.user_id,
        "from_state": record.from_state,
        "to_state": record.to_state,
        "cause": TRANSITION_CAUSE,
        "transitioned_at": _isoformat(now),
    }
    try:
        response = supabase.table(TRANSITIONS_TABLE).insert(payload).execute()
        _rows(response)  # raises on a PostgREST error envelope
    except ExpirySweepFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome, never swallowed
        raise ExpirySweepFailed(
            f"the transition history row for subscription {record.subscription_id} was not "
            f"written: {exc}",
            stage="transition_insert",
        ) from exc


async def _write_audit(
    record: ExpiredSubscription,
    now: datetime,
    *,
    audit_writer: Optional[Any] = None,
) -> None:
    """One ``SUBSCRIPTION_EXPIRED`` audit entry for one expiry (Requirements 11.12, 26.2).

    Uses the **existing** ``StrategyAuditLogger`` and the
    ``MARKETPLACE_SUBSCRIPTION_TRANSITIONED`` action member Requirement 26.2 already allocates
    to "Subscription_State transition", so retention matches every other marketplace record and
    no second audit facility appears (the same reasoning ``eligibility_gate._write_audit``
    records). ``design.md``'s ``audit(SUBSCRIPTION_EXPIRED)`` names the act;
    :data:`AUDIT_REASON_CODE` is where that name is written, and it travels on the ``reason``
    and in the metadata so a log reader can find it by name.

    Requirement 11.12's five facts travel as: ``before`` / ``after`` for the prior and new
    value, ``metadata['cause']`` for the cause, ``actor_id`` = ``'system'`` for "no identity
    acted, this was the scheduled sweep", and the logger's own ``timestamp`` for the UTC
    instant. The Subscription's ``period_expiry`` rides in the metadata as the evidence for the
    transition. No payment credential, provider secret, card datum or other user's identifier
    appears - only this purchaser's own id (Requirement 26.4).

    ``record_or_raise`` is preferred over ``log`` because ``log``'s documented contract is
    never-raises, and a swallowed audit failure on a state change is exactly what Requirement
    26.5 forbids.

    Raises:
        ExpirySweepFailed: the audit entry was not written.
    """
    writer = audit_writer
    action: Any
    if writer is None:
        from backend_app.core.audit_trail import (  # local: keeps import light
            StrategyAuditAction,
            get_strategy_audit_logger,
        )

        audit_logger = get_strategy_audit_logger()
        candidate = getattr(audit_logger, "record_or_raise", None)
        writer = candidate if callable(candidate) else audit_logger.log
        action = StrategyAuditAction.MARKETPLACE_SUBSCRIPTION_TRANSITIONED
    else:
        # An injected writer is handed the act's own name rather than an enum member it may not
        # know about, so a test double needs no import from ``core.audit_trail``.
        action = AUDIT_REASON_CODE

    metadata = {
        "audited_act": AUDIT_REASON_CODE,
        "cause": TRANSITION_CAUSE,
        "listing_id": record.listing_id,
        "period_expiry": _isoformat(record.period_expiry)
        if record.period_expiry
        else None,
        "swept_at": _isoformat(now),
        # The statement that matters for anyone reading this trail during an incident: the
        # label moved, access did not. Access ended at ``period_expiry`` whether or not this
        # sweep ran (Requirement 11.7, property P-11).
        "entitlement_decided_by": "period_expiry",
    }

    try:
        await writer(
            action,
            actor_id="system",
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=record.subscription_id,
            reason=(
                f"{AUDIT_REASON_CODE}: subscription period ended, so the stored state moved "
                f"{record.from_state} -> {record.to_state} by the scheduled expiry sweep"
            ),
            before=record.from_state,
            after=record.to_state,
            metadata=metadata,
        )
    except ExpirySweepFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome, never swallowed
        raise ExpirySweepFailed(
            f"the audit entry for subscription {record.subscription_id} was not written: "
            f"{exc}",
            stage="audit_write",
        ) from exc


# ══════════════════════════════════════════════════════════════════════════
# SMALL PURE UTILITIES
# ══════════════════════════════════════════════════════════════════════════


def _rows(response: Any) -> List[Mapping[str, Any]]:
    """The rows of a PostgREST response, or ``[]``. Raises when the response signals an error.

    Same reading as ``entitlement_resolver._rows``: rows may arrive on ``.data`` (the
    supabase-py convention this codebase uses), under a ``["data"]`` key, or - in a test double
    - as a bare list. A response carrying a non-empty ``error`` is a statement that DID NOT
    COMPLETE and raises :class:`ExpirySweepFailed`; reading it as "no rows" would turn a broken
    update into "nothing was due to expire".
    """
    error = getattr(response, "error", None)
    if error is None and isinstance(response, Mapping):
        error = response.get("error")
    if error:
        raise ExpirySweepFailed(
            f"the statement returned an error envelope: {error}", stage="response_error"
        )

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
    """Read ``key`` from a mapping or an attribute off an object. ``None`` when absent."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _as_text(value: Any) -> Optional[str]:
    """``value`` as text, or ``None``. Ids may arrive as UUID objects."""
    if value is None:
        return None
    text = str(value)
    return text or None


def _normalise_status(value: Any) -> Optional[str]:
    """A ``library_subscriptions.status`` value as lowercase text, or ``None``."""
    if value is None:
        return None
    return str(value).strip().lower() or None


def _coerce_instant(value: Optional[datetime]) -> datetime:
    """``value`` as a tz-aware UTC instant; the current UTC instant when ``None``.

    A naive ``datetime`` is read as UTC rather than rejected: every instant in this
    specification is UTC, and refusing a naive one would make the worker's own
    ``datetime.utcnow()``-shaped callers fail rather than be interpreted correctly.
    """
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError(f"now must be a datetime, not {type(value).__name__}")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _coerce_optional_instant(value: Any) -> Optional[datetime]:
    """A stored timestamp as a tz-aware UTC datetime, or ``None`` when it is absent/unparseable.

    An unparseable value reads as ``None``, which is only ever recorded in the audit metadata -
    it never participates in the eligibility decision, because the *database* evaluated
    ``period_expiry <= now`` and this value is a copy of the column for the record.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(
            timezone.utc
        )
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(
            timezone.utc
        )
    return None


def _isoformat(value: datetime) -> str:
    """A UTC instant as the ISO-8601 text PostgREST accepts for a ``timestamptz``."""
    return _coerce_instant(value).isoformat()


def _elapsed_ms(started: float) -> int:
    """Milliseconds since ``started`` (a ``time.monotonic()`` reading), never negative."""
    return max(0, int((time.monotonic() - started) * 1000))
