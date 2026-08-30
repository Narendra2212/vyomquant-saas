"""
backend_app/backend/marketplace/submission_state.py - the Submission 8-state machine.

Spec: marketplace-subscriptions-paper-trading task 5.1. ``design.md`` ->
"``marketplace/submission_state.py`` - the 8-state machine" and "The mapping (one
definition, Requirement 4.12)". Requirements 4.1, 4.2, 4.6, 4.7, 4.12, 2.7.

Exposes
-------
SubmissionState               the 8 canonical values (Requirement 4.1)
SUBMISSION_STATE_VALUES       the vocabulary as DB/wire strings, for ``chk_submission_state``
SUBMISSION_TRANSITIONS        the eleven permitted edges, one key per state (Requirement 4.2)
PUBLIC_STATES                 the states the Listing_Projection may expose (4.6, 4.7)
OPEN_STATES                   the states that occupy the one-open-Submission slot (2.7, 4.5)
MODERATION_STATUS_FOR_STATE   the single shared Submission_State -> ``moderation_status``
IS_ACTIVE_FOR_STATE           the single shared Submission_State -> ``is_active`` (4.12)
can_transition(...)           the transition predicate every call site consults
normalise_submission_state / legal_transitions / is_public / is_open
moderation_status_for / is_active_for

WHY THIS MODULE IS PURE
-----------------------
Same convention ``backend/order_lifecycle_state.py`` and ``backend/strategy_lifecycle.py``
already establish, and the layering rule ``design.md`` states for this package: module
import pulls only the standard library - no database handle, no HTTP client, no FastAPI
import, no I/O. This table is consulted by the publish path, by the admin review actions,
by the Listing_Projection, by the Entitlement_Resolver and by the migration that seeds
``marketplace_submission_allowed_transitions``; every one of those callers must be able to
import it without dragging a connection pool or an event loop behind it, and a transition
must be testable without a TestClient.

THE DATABASE IS THE ARBITER, THIS IS THE GATE
---------------------------------------------
:func:`can_transition` is consulted *before* a write, inside the same transaction that read
the current state. It is not the enforcement: ``chk_submission_state`` enumerates the 8
values and ``trg_submission_transition_guard`` re-checks the edge against
``marketplace_submission_allowed_transitions`` independently of any application module
(Requirement 4.4). ``tests/test_submission_state_agreement.py`` pins the seed table against
:data:`SUBMISSION_TRANSITIONS`, so the two cannot drift.

NO STATE LISTS ITSELF
---------------------
Requirement 4.2 rejects "any transition from a value to that same value" explicitly, so a
same-value write is refused by exactly the rule that refuses any other illegal edge - there
is no separate self-transition branch to keep in step. ``UNPUBLISHED`` has an empty target
tuple and is the one terminal state. Every state is a key, including the terminal one, so a
missing key can never be misread as "terminal by accident".

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* Writing a transition or its history row. That needs a database handle, which is what this
  module must not have. The write order the codebase already proves for the sibling problem
  (``strategy_lifecycle.apply_version_state``: gate, then write, then audit) is kept - this
  module is the gate; the submission service owns the write and the audit.
* ``'featured'``. Featuring stays exactly what it is today -
  ``library_strategies.is_featured``, set by ``admin_moderate_strategy``, read by
  ``get_featured_strategies`` - and is orthogonal to the Submission lifecycle.
  :data:`MODERATION_STATUS_FOR_STATE` therefore never produces it, which is what keeps the
  existing ``.in_("moderation_status", ["approved", "featured"])`` predicates in
  ``library.py`` unchanged in meaning.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, FrozenSet, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════════
# THE VOCABULARY (Requirement 4.1)
# ══════════════════════════════════════════════════════════════════════════


class SubmissionState(str, Enum):
    """The 8 values, and the only values, a Submission_State may hold.

    ``str``-valued for the same reason ``OrderLifecycleState`` is: the member is its own
    wire and column value, so a JSON response, the ``chk_submission_state`` ``CHECK``, the
    ``marketplace_submission_allowed_transitions`` seed and a comparison in Python all
    speak one spelling. Uppercase because Requirement 4.1 spells it uppercase and
    ``marketplace_submissions.submission_state`` stores it verbatim.
    """

    #: Created, not yet offered for review. The state every Submission starts in (Req 4.3).
    DRAFT = "DRAFT"
    #: Offered by the owner and admitted by the Eligibility_Gate; awaiting a reviewer.
    SUBMITTED = "SUBMITTED"
    #: Claimed by an Admin_Reviewer and being examined.
    UNDER_REVIEW = "UNDER_REVIEW"
    #: Verified by an Admin_Reviewer, not yet visible in the catalogue (Req 4.7).
    APPROVED = "APPROVED"
    #: Live in the public catalogue. The only state the Listing_Projection exposes (Req 4.6).
    PUBLISHED = "PUBLISHED"
    #: Refused, carrying a reviewer-supplied reason of 1..2000 characters (Req 4.8).
    REJECTED = "REJECTED"
    #: Withdrawn from the catalogue by the platform; existing Subscriptions run on (Req 4.10).
    SUSPENDED = "SUSPENDED"
    #: Withdrawn permanently. Terminal: Requirement 4.2 permits no transition out of it.
    UNPUBLISHED = "UNPUBLISHED"

    def __str__(self) -> str:  # pragma: no cover - convenience for log lines
        return self.value


#: The vocabulary as plain strings, in the requirement's own order. This is the list
#: ``007_marketplace_submissions.sql``'s ``chk_submission_state`` is written from, so the
#: constraint and the enum cannot disagree (Requirements 4.5, 24.2).
SUBMISSION_STATE_VALUES: Tuple[str, ...] = tuple(
    state.value for state in SubmissionState
)


# ══════════════════════════════════════════════════════════════════════════
# THE TRANSITION TABLE (Requirement 4.2)
# ══════════════════════════════════════════════════════════════════════════

#: Requirement 4.2's eleven pairs, one entry per state - the same shape
#: ``strategy_lifecycle.VERSION_TRANSITIONS`` already establishes, including the "every
#: state is a key, a terminal state maps to ``()``" rule.
#:
#: Eleven edges, and no twelfth:
#:
#: * ``DRAFT -> SUBMITTED`` - the owner offers it.
#: * ``SUBMITTED -> UNDER_REVIEW | REJECTED`` - a reviewer picks it up, or refuses outright.
#: * ``UNDER_REVIEW -> APPROVED | REJECTED`` - the review verdict.
#: * ``APPROVED -> PUBLISHED`` - the publication act, which is also the only edge that
#:   makes the Listing visible (Requirement 4.6).
#: * ``PUBLISHED -> SUSPENDED | UNPUBLISHED`` - withdrawal, reversible or not.
#: * ``SUSPENDED -> PUBLISHED | UNPUBLISHED`` - reinstatement, or permanent withdrawal.
#: * ``REJECTED -> DRAFT`` - the owner's route back to a fresh attempt.
#:
#: No state lists itself, so a same-value write is refused by the same rule that refuses
#: any other illegal edge (Requirement 4.2's explicit clause). ``UNPUBLISHED`` is terminal.
SUBMISSION_TRANSITIONS: Dict[SubmissionState, Tuple[SubmissionState, ...]] = {
    SubmissionState.DRAFT: (SubmissionState.SUBMITTED,),
    SubmissionState.SUBMITTED: (
        SubmissionState.UNDER_REVIEW,
        SubmissionState.REJECTED,
    ),
    SubmissionState.UNDER_REVIEW: (
        SubmissionState.APPROVED,
        SubmissionState.REJECTED,
    ),
    SubmissionState.APPROVED: (SubmissionState.PUBLISHED,),
    SubmissionState.PUBLISHED: (
        SubmissionState.SUSPENDED,
        SubmissionState.UNPUBLISHED,
    ),
    SubmissionState.SUSPENDED: (
        SubmissionState.PUBLISHED,
        SubmissionState.UNPUBLISHED,
    ),
    SubmissionState.REJECTED: (SubmissionState.DRAFT,),
    SubmissionState.UNPUBLISHED: (),
}


# ══════════════════════════════════════════════════════════════════════════
# THE TWO STATE SETS
# ══════════════════════════════════════════════════════════════════════════

#: The states in which a Listing is visible to a non-owner. Exactly one (Requirements 4.6,
#: 4.7). The Listing_Projection and property P-50 read this set rather than re-spelling
#: ``== 'PUBLISHED'`` at each call site.
PUBLIC_STATES: FrozenSet[SubmissionState] = frozenset({SubmissionState.PUBLISHED})

#: The states that occupy the "one open Submission per strategy" slot - the same four the
#: partial unique index ``uq_submission_open_per_strategy`` is written from (Requirements
#: 2.7, 4.5). ``PUBLISHED`` is in the set because Requirement 2.7 also forbids a second
#: Submission while a Listing for the same ``source_strategy_id`` is live.
OPEN_STATES: FrozenSet[SubmissionState] = frozenset(
    {
        SubmissionState.SUBMITTED,
        SubmissionState.UNDER_REVIEW,
        SubmissionState.APPROVED,
        SubmissionState.PUBLISHED,
    }
)


# ══════════════════════════════════════════════════════════════════════════
# THE ONE SHARED PROJECTION ONTO THE LEGACY COLUMNS (Requirement 4.12)
# ══════════════════════════════════════════════════════════════════════════

#: Requirement 4.12's single definition: each Submission_State maps to exactly one existing
#: ``library_strategies.moderation_status`` value. Applied by the database trigger
#: ``trg_submission_projects_moderation_status``, never by a call site, so the review
#: lifecycle and the legacy column cannot drift.
#:
#: ``APPROVED`` deliberately stays ``'pending'``: approved-but-not-yet-published must remain
#: invisible to the catalogue (Requirement 4.7), and ``'approved'`` is the value the existing
#: ``browse_library``/``get_library_detail`` predicates treat as visible.
#:
#: ``SUSPENDED`` and ``UNPUBLISHED`` map to ``'rejected'`` because that is the only retained
#: value meaning "not in the catalogue"; it says nothing about Subscriptions, which
#: Requirements 4.10 and 4.11 keep running to their period expiry.
#:
#: ``'featured'`` never appears as a value here - see the module docstring.
MODERATION_STATUS_FOR_STATE: Dict[SubmissionState, str] = {
    SubmissionState.DRAFT: "pending",
    SubmissionState.SUBMITTED: "pending",
    SubmissionState.UNDER_REVIEW: "pending",
    SubmissionState.APPROVED: "pending",
    SubmissionState.PUBLISHED: "approved",
    SubmissionState.REJECTED: "rejected",
    SubmissionState.SUSPENDED: "rejected",
    SubmissionState.UNPUBLISHED: "rejected",
}

#: The companion half of Requirement 4.12's one definition: ``library_strategies.is_active``
#: is true for a published Submission and for nothing else, which is what makes
#: Requirement 4.6's "every public response served after that transaction commits includes
#: the Listing" true without a second write from the handler.
IS_ACTIVE_FOR_STATE: Dict[SubmissionState, bool] = {
    state: state in PUBLIC_STATES for state in SubmissionState
}


# ══════════════════════════════════════════════════════════════════════════
# THE PREDICATES
# ══════════════════════════════════════════════════════════════════════════


def normalise_submission_state(value: Any) -> Optional[SubmissionState]:
    """``value`` as a :class:`SubmissionState`, or ``None`` when outside the 8.

    Accepts a member, the column string, and a differently-cased or padded spelling of it,
    so a value read from the database, from an enum and from a wire payload all resolve
    identically. Returns ``None`` rather than raising, following
    ``strategy_lifecycle.normalise_lifecycle_state``: the caller decides whether an
    unrecognised label is a refusal - in :func:`can_transition` it is - and gets to say what
    it was.
    """
    if isinstance(value, SubmissionState):
        return value
    if value is None:
        return None
    label = str(value).strip().upper().replace("-", "_").replace(" ", "_")
    if not label:
        return None
    try:
        return SubmissionState(label)
    except ValueError:
        return None


def legal_transitions(state: Any) -> Tuple[SubmissionState, ...]:
    """Every state ``state`` may legally move to. ``()`` for terminal or unrecognised."""
    label = normalise_submission_state(state)
    if label is None:
        return ()
    return SUBMISSION_TRANSITIONS.get(label, ())


def can_transition(current: Any, target: Any) -> bool:
    """Whether ``current -> target`` is one of Requirement 4.2's eleven permitted edges.

    ``False`` for a same-value pair, for any edge out of ``UNPUBLISHED``, and for any value
    outside the 8 - an unreadable current state never authorises a write.
    """
    resolved_target = normalise_submission_state(target)
    if resolved_target is None:
        return False
    return resolved_target in legal_transitions(current)


def is_public(state: Any) -> bool:
    """Whether a Listing whose Submission is in ``state`` is exposed publicly (4.6, 4.7)."""
    return normalise_submission_state(state) in PUBLIC_STATES


def is_open(state: Any) -> bool:
    """Whether ``state`` occupies the one-open-Submission-per-strategy slot (2.7, 4.5)."""
    return normalise_submission_state(state) in OPEN_STATES


def moderation_status_for(state: Any) -> Optional[str]:
    """The ``library_strategies.moderation_status`` for ``state``, or ``None`` if unknown.

    ``None`` rather than a guess: a Submission_State the mapping does not know must not be
    projected onto the catalogue-visible ``'approved'`` by default.
    """
    label = normalise_submission_state(state)
    if label is None:
        return None
    return MODERATION_STATUS_FOR_STATE[label]


def is_active_for(state: Any) -> bool:
    """The ``library_strategies.is_active`` for ``state``. ``False`` when unrecognised."""
    label = normalise_submission_state(state)
    if label is None:
        return False
    return IS_ACTIVE_FOR_STATE[label]


__all__ = [
    "IS_ACTIVE_FOR_STATE",
    "MODERATION_STATUS_FOR_STATE",
    "OPEN_STATES",
    "PUBLIC_STATES",
    "SUBMISSION_STATE_VALUES",
    "SUBMISSION_TRANSITIONS",
    "SubmissionState",
    "can_transition",
    "is_active_for",
    "is_open",
    "is_public",
    "legal_transitions",
    "moderation_status_for",
    "normalise_submission_state",
]
