"""
backend_app/backend/marketplace/subscription_state.py - the Subscription_State machine.

Spec: marketplace-subscriptions-paper-trading task 5.2. ``design.md`` ->
"``marketplace/subscription_state.py``". Requirements 11.1, 11.2, 11.6.

Exposes
-------
SubscriptionState               the 7 canonical values (Requirement 11.1)
SUBSCRIPTION_TRANSITIONS        the twelve permitted pairs (Requirement 11.2)
PAYMENT_REQUIRED_TARGETS        ``{ACTIVE}`` - the targets a Settlement_Record gates (11.6)
can_transition(current, target) the transition gate
requires_confirmed_payment(t)   whether reaching ``t`` needs a confirmed payment (11.6)
STATUS_TEXT_FOR_STATE           the one enum -> persisted ``library_subscriptions.status``
                                mapping, lowercase
STATE_FOR_STATUS_TEXT           its inverse, for reading a stored row back
SUBSCRIPTION_STATUS_VALUES      the seven lowercase spellings, in requirement order - the
                                list the widened ``valid_subscription_status`` CHECK is
                                written from
SUBSCRIPTION_TRANSITION_TEXT_PAIRS
                                the twelve pairs as lowercase text, the shape
                                ``marketplace_subscription_allowed_transitions`` is seeded
                                with and the state-agreement test compares against

WHY THIS MODULE IS PURE
-----------------------
Same convention ``strategy_dag/schema.py`` and ``backend/order_lifecycle_state.py`` already
establish: import pulls only the standard library - no database handle, no Supabase client,
no FastAPI import, no I/O. The table is consulted by ``settlement_service``, by the expiry
sweep, by the subscription routes, by the ``008_marketplace_settlement.sql`` seed and by the
migration's own ``CHECK`` constraint; every one of those callers must be able to import the
vocabulary without dragging a connection pool behind it, and a transition must be testable
without a TestClient and without a payment provider.

THE PERSISTED SPELLING (Requirement 11.1, root-cause note in design.md)
-----------------------------------------------------------------------
``library_subscriptions.status`` is lowercase text today - ``'pending'``, ``'active'``,
``'cancelled'``, ``'expired'`` - and
``archived_migrations/root_migrations/007_create_library_subscriptions.sql`` carries
``CONSTRAINT valid_subscription_status CHECK (status IN ('active','expired','cancelled',
'pending'))``. ``008_marketplace_settlement.sql`` widens that constraint *additively* to the
seven lowercase spellings, adding ``'refunded'``, ``'payment_failed'`` and ``'suspended'``.
No existing value is renamed, so ``billing.py``'s ``.eq("status", "pending")`` in
``_apply_marketplace_entitlement`` and ``library.py``'s ``.eq("status", "active")`` in
``check_deployment_permission`` keep working unchanged.

:data:`STATUS_TEXT_FOR_STATE` is the *one* place that mapping is spelled, in either
direction. A caller that needs the column value asks for it here rather than lowercasing an
enum member inline, which is how the enum and the column drift apart.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
Persisting a transition, and deciding whether a payment actually exists. This module answers
"is this pair permitted" and "does this target need a payment"; it cannot answer "was a
payment confirmed", because that is a ``marketplace_settlements`` read.
``settlement_service`` owns that, and ``trg_subscription_transition_guard`` enforces both
independently of the application (Requirements 11.3, 11.14) - the database is the arbiter,
this module is the fast, testable gate in front of it.

Entitlement is also not here. Requirement 11.7 makes the *period expiry* authoritative, not
the stored label: ``entitlement_resolver`` compares ``now`` with ``period_expiry`` directly,
so a dead expiry sweep cannot extend access. Nothing in this module should be read as
"``ACTIVE`` means entitled".
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, FrozenSet, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════════
# THE VOCABULARY (Requirement 11.1)
# ══════════════════════════════════════════════════════════════════════════


class SubscriptionState(str, Enum):
    """The 7 values, and the only values, a Subscription_State takes.

    ``str``-valued so a member compares equal to its own wire spelling, matching
    ``OrderLifecycleState``. Uppercase because Requirement 11.1 spells it uppercase; the
    lowercase *column* spelling lives in :data:`STATUS_TEXT_FOR_STATE` and nowhere else.
    """

    #: Row created, payment not yet confirmed. The initial state.
    PENDING = "PENDING"
    #: Paid and within its period. Requires a Settlement_Record to reach (Requirement 11.6).
    ACTIVE = "ACTIVE"
    #: Period expiry has passed and no renewal was confirmed (Requirement 11.8).
    EXPIRED = "EXPIRED"
    #: Purchaser cancelled renewal; entitlement runs to the unchanged expiry (Req 11.9).
    CANCELLED = "CANCELLED"
    #: A settlement reversal landed. Terminal - no transition leaves it.
    REFUNDED = "REFUNDED"
    #: The confirmation attempt for a `PENDING` subscription failed; retryable.
    PAYMENT_FAILED = "PAYMENT_FAILED"
    #: Administratively held. May resume to `ACTIVE` (on payment) or lapse to `EXPIRED`.
    SUSPENDED = "SUSPENDED"

    def __str__(self) -> str:  # pragma: no cover - convenience for log lines
        return self.value


# ══════════════════════════════════════════════════════════════════════════
# THE TRANSITION TABLE (Requirement 11.2)
# ══════════════════════════════════════════════════════════════════════════

#: Requirement 11.2's twelve pairs, one entry per state - the same shape
#: ``strategy_lifecycle.VERSION_TRANSITIONS`` and ``ORDER_LIFECYCLE_TRANSITIONS`` already
#: use, including the "every state is a key, a state with no successor maps to ``()``"
#: rule that keeps a missing key from silently reading as terminal.
#:
#: No state lists itself, so a same-value write is rejected by the same rule that rejects
#: any other illegal pair. ``REFUNDED`` is the single terminal state: a reversal is the end
#: of that subscription's life, and re-subscribing is a new row.
SUBSCRIPTION_TRANSITIONS: Dict[SubscriptionState, Tuple[SubscriptionState, ...]] = {
    SubscriptionState.PENDING: (
        SubscriptionState.ACTIVE,
        SubscriptionState.PAYMENT_FAILED,
        SubscriptionState.CANCELLED,
    ),
    SubscriptionState.ACTIVE: (
        SubscriptionState.EXPIRED,
        SubscriptionState.CANCELLED,
        SubscriptionState.REFUNDED,
        SubscriptionState.SUSPENDED,
    ),
    SubscriptionState.EXPIRED: (SubscriptionState.ACTIVE,),
    SubscriptionState.CANCELLED: (SubscriptionState.ACTIVE,),
    SubscriptionState.SUSPENDED: (
        SubscriptionState.ACTIVE,
        SubscriptionState.EXPIRED,
    ),
    SubscriptionState.PAYMENT_FAILED: (SubscriptionState.PENDING,),
    SubscriptionState.REFUNDED: (),
}

#: Requirement 11.6: every transition **into** these targets needs a payment confirmed
#: through the Billing_Integration and recorded as a Settlement_Record first - from any of
#: ``PENDING``, ``EXPIRED``, ``CANCELLED``, ``PAYMENT_FAILED`` and ``SUSPENDED``. A set
#: rather than a hard-coded ``target == ACTIVE`` comparison so the rule is stated once and
#: ``trg_subscription_transition_guard``'s SQL equivalent can be read against it.
PAYMENT_REQUIRED_TARGETS: FrozenSet[SubscriptionState] = frozenset({SubscriptionState.ACTIVE})

#: ``REFUNDED`` - the only state with no permitted successor. Derived from the table rather
#: than transcribed, so it cannot disagree with it.
TERMINAL_STATES: FrozenSet[SubscriptionState] = frozenset(
    state for state, targets in SUBSCRIPTION_TRANSITIONS.items() if not targets
)


# ══════════════════════════════════════════════════════════════════════════
# THE ONE ENUM <-> COLUMN MAPPING
# ══════════════════════════════════════════════════════════════════════════

#: The persisted ``library_subscriptions.status`` text for each state. The first four
#: spellings are the ones already stored today and are reproduced exactly; the last three
#: are the additive ones ``008_marketplace_settlement.sql`` adds to
#: ``valid_subscription_status``.
STATUS_TEXT_FOR_STATE: Dict[SubscriptionState, str] = {
    SubscriptionState.PENDING: "pending",
    SubscriptionState.ACTIVE: "active",
    SubscriptionState.EXPIRED: "expired",
    SubscriptionState.CANCELLED: "cancelled",
    SubscriptionState.REFUNDED: "refunded",
    SubscriptionState.PAYMENT_FAILED: "payment_failed",
    SubscriptionState.SUSPENDED: "suspended",
}

#: The inverse. Total over :data:`STATUS_TEXT_FOR_STATE`'s values by construction, so a row
#: written through this module always reads back.
STATE_FOR_STATUS_TEXT: Dict[str, SubscriptionState] = {
    text: state for state, text in STATUS_TEXT_FOR_STATE.items()
}

#: The seven lowercase spellings in Requirement 11.1's order - the list the widened
#: ``valid_subscription_status`` CHECK is generated from, so the constraint and the enum
#: cannot disagree.
SUBSCRIPTION_STATUS_VALUES: Tuple[str, ...] = tuple(
    STATUS_TEXT_FOR_STATE[state] for state in SubscriptionState
)

#: The twelve pairs as ``(from_state, to_state)`` enum members. Flattened from
#: :data:`SUBSCRIPTION_TRANSITIONS` so there is one source of truth for the edge set.
SUBSCRIPTION_TRANSITION_PAIRS: Tuple[Tuple[SubscriptionState, SubscriptionState], ...] = tuple(
    (current, target)
    for current, targets in SUBSCRIPTION_TRANSITIONS.items()
    for target in targets
)

#: The same twelve pairs as lowercase column text - the shape
#: ``marketplace_subscription_allowed_transitions`` is seeded with, and what
#: ``tests/test_subscription_state_agreement.py`` (task 14.6's sibling) compares the seed
#: statements against.
SUBSCRIPTION_TRANSITION_TEXT_PAIRS: Tuple[Tuple[str, str], ...] = tuple(
    (STATUS_TEXT_FOR_STATE[current], STATUS_TEXT_FOR_STATE[target])
    for current, target in SUBSCRIPTION_TRANSITION_PAIRS
)


# ══════════════════════════════════════════════════════════════════════════
# NORMALISATION AND THE GATE
# ══════════════════════════════════════════════════════════════════════════


def normalise_subscription_state(value: Any) -> Optional[SubscriptionState]:
    """``value`` as a :class:`SubscriptionState`, or ``None`` when outside the 7.

    Accepts the member itself, the uppercase requirement spelling, and the lowercase
    column value, so a row read straight out of ``library_subscriptions`` resolves without
    the caller reaching for :data:`STATE_FOR_STATUS_TEXT` first.

    Returns ``None`` rather than raising, following ``strategy_lifecycle`` and
    ``order_lifecycle_state``: the caller decides whether an unrecognised label is a
    refusal and gets to say what it was in its own error body.
    """
    if isinstance(value, SubscriptionState):
        return value
    if value is None:
        return None
    raw = getattr(value, "value", value)
    text = str(raw).strip().replace("-", "_").replace(" ", "_")
    if not text:
        return None
    try:
        return SubscriptionState(text.upper())
    except ValueError:
        return None


def status_text(state: Any) -> str:
    """The persisted ``library_subscriptions.status`` text for ``state``.

    Raises :class:`ValueError` on an unrecognised value - unlike the readers above, this is
    a *write* path, and defaulting a status the caller could not name would put an
    unconstrained value in front of ``valid_subscription_status``.
    """
    resolved = normalise_subscription_state(state)
    if resolved is None:
        raise ValueError(
            f"{state!r} is not one of {list(SUBSCRIPTION_STATUS_VALUES)}, so it has no "
            f"library_subscriptions.status spelling."
        )
    return STATUS_TEXT_FOR_STATE[resolved]


def legal_transitions(state: Any) -> Tuple[SubscriptionState, ...]:
    """Every state ``state`` may legally move to. ``()`` for terminal or unrecognised.

    The reachability closure P-8 walks (task 19.8) is built from this, so the property test
    never re-transcribes the edge set it is meant to be checking.
    """
    resolved = normalise_subscription_state(state)
    if resolved is None:
        return ()
    return SUBSCRIPTION_TRANSITIONS[resolved]


def can_transition(current: Any, target: Any) -> bool:
    """Whether ``current -> target`` is one of Requirement 11.2's twelve pairs.

    Unrecognised on either side is ``False``, not an exception: an unknown label is by
    definition not in the permitted set, and every caller of this gate already has a
    refusal path for a rejected transition
    (``MARKETPLACE_SUBSCRIPTION_TRANSITION_REJECTED``) but not necessarily one for a
    surprise ``ValueError``.

    Note what this deliberately does **not** check: whether a Settlement_Record exists for
    a transition into ``ACTIVE``. Use :func:`requires_confirmed_payment` to learn that the
    check is needed; only ``settlement_service`` and
    ``trg_subscription_transition_guard`` can perform it.
    """
    resolved_current = normalise_subscription_state(current)
    resolved_target = normalise_subscription_state(target)
    if resolved_current is None or resolved_target is None:
        return False
    return resolved_target in SUBSCRIPTION_TRANSITIONS[resolved_current]


def requires_confirmed_payment(target: Any) -> bool:
    """Whether reaching ``target`` requires a recorded Settlement_Record (Req 11.6)."""
    return normalise_subscription_state(target) in PAYMENT_REQUIRED_TARGETS


def is_terminal(state: Any) -> bool:
    """Whether ``state`` has no permitted successor (``REFUNDED``)."""
    return normalise_subscription_state(state) in TERMINAL_STATES


__all__ = [
    "SubscriptionState",
    "SUBSCRIPTION_TRANSITIONS",
    "PAYMENT_REQUIRED_TARGETS",
    "TERMINAL_STATES",
    "STATUS_TEXT_FOR_STATE",
    "STATE_FOR_STATUS_TEXT",
    "SUBSCRIPTION_STATUS_VALUES",
    "SUBSCRIPTION_TRANSITION_PAIRS",
    "SUBSCRIPTION_TRANSITION_TEXT_PAIRS",
    "normalise_subscription_state",
    "status_text",
    "legal_transitions",
    "can_transition",
    "requires_confirmed_payment",
    "is_terminal",
]
