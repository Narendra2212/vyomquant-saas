"""
backend/paper/paper_order_state.py - The Paper_Order_State machine.

The single definition of the six paper order states, the transitions between them and the
terminal set. Requirement 16.1 fixes the value set, Requirement 16.2 the permitted
transitions and Requirement 16.3 the terminal states. Everything that writes a paper
order state - ``paper_simulator`` before it writes, the ``chk_paper_order_state`` check
constraint and the ``trg_paper_order_transition_guard`` trigger generated from
``paper_order_allowed_transitions`` - derives its rule from here, so the three cannot
drift apart.

Exposes
-------
PaperOrderState              the six values of Requirement 16.1
PAPER_ORDER_TRANSITIONS      state -> permitted targets (Requirement 16.2)
TERMINAL                     {FILLED, CANCELLED, REJECTED} (Requirement 16.3)
can_transition(a, b)         membership test over PAPER_ORDER_TRANSITIONS
is_terminal(state)           membership test over TERMINAL
LEGACY_STATUS_FOR_STATE      state -> retained ``PaperOrderStatus`` spelling
legacy_status_for(state)     lookup helper over that mapping

Notes
-----
* ``PARTIALLY_FILLED -> PARTIALLY_FILLED`` is the one self-transition Requirement 16.2
  permits, so successive partial fills of one order are representable. No other state
  lists itself.
* The legacy spellings are written as plain strings rather than imported from
  ``paper_trading_service.PaperOrderStatus``: this module performs no I/O and imports
  nothing beyond the standard library, and ``paper_trading_service`` is a service module.
  A parity test asserts the strings here are exactly the ``PaperOrderStatus`` members.
* Module import pulls only the standard library: no database handle, no HTTP client, no
  FastAPI import, no ``random`` draw.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet, Mapping, Tuple


class PaperOrderState(str, Enum):
    """The six Paper_Order_State values of Requirement 16.1.

    ``str`` mixin so a value round-trips through JSON and through the
    ``paper_orders.order_state`` text column without a conversion at the call site.
    """

    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


#: The permitted transitions of Requirement 16.2, as ``state -> targets``. Every state is
#: a key, so a lookup never needs a default, and the three terminal states carry an empty
#: target tuple. ``PARTIALLY_FILLED`` is the only state listing itself.
PAPER_ORDER_TRANSITIONS: Mapping[PaperOrderState, Tuple[PaperOrderState, ...]] = {
    PaperOrderState.CREATED: (
        PaperOrderState.ACCEPTED,
        PaperOrderState.REJECTED,
    ),
    PaperOrderState.ACCEPTED: (
        PaperOrderState.PARTIALLY_FILLED,
        PaperOrderState.FILLED,
        PaperOrderState.CANCELLED,
        PaperOrderState.REJECTED,
    ),
    PaperOrderState.PARTIALLY_FILLED: (
        PaperOrderState.PARTIALLY_FILLED,
        PaperOrderState.FILLED,
        PaperOrderState.CANCELLED,
    ),
    PaperOrderState.FILLED: (),
    PaperOrderState.CANCELLED: (),
    PaperOrderState.REJECTED: (),
}

#: Requirement 16.3: no transition leaves any of these states.
TERMINAL: FrozenSet[PaperOrderState] = frozenset(
    {
        PaperOrderState.FILLED,
        PaperOrderState.CANCELLED,
        PaperOrderState.REJECTED,
    }
)

#: Requirement 17.12: the retained ``PaperOrderStatus`` spelling for each new state.
#: ``paper_orders.order_state`` stores the six values above; ``paper_orders.legacy_status``
#: carries the value from this mapping, so ``GET /api/paper/orders?status=OPEN`` keeps its
#: exact meaning - an order that is live on the book, whether untouched or partly filled.
#: ``CREATED`` maps to ``NEW``; ``ACCEPTED`` and ``PARTIALLY_FILLED`` both map to ``OPEN``;
#: the three terminals keep their own spelling. One mapping, in one place, the same
#: technique as ``MODERATION_STATUS_FOR_STATE``.
LEGACY_STATUS_FOR_STATE: Mapping[PaperOrderState, str] = {
    PaperOrderState.CREATED: "NEW",
    PaperOrderState.ACCEPTED: "OPEN",
    PaperOrderState.PARTIALLY_FILLED: "OPEN",
    PaperOrderState.FILLED: "FILLED",
    PaperOrderState.CANCELLED: "CANCELLED",
    PaperOrderState.REJECTED: "REJECTED",
}


def can_transition(current: PaperOrderState, target: PaperOrderState) -> bool:
    """Return whether ``current -> target`` is one of the Requirement 16.2 transitions.

    A transition out of a terminal state is never permitted, because every terminal state
    carries an empty target tuple (Requirement 16.3). Unknown values answer ``False``
    rather than raising, so a caller validating a stored string can reject the write and
    name the rejected transition (Requirement 16.4).
    """
    return target in PAPER_ORDER_TRANSITIONS.get(current, ())


def is_terminal(state: PaperOrderState) -> bool:
    """Return whether ``state`` is one of ``FILLED``, ``CANCELLED`` or ``REJECTED``."""
    return state in TERMINAL


def legacy_status_for(state: PaperOrderState) -> str:
    """Return the retained ``PaperOrderStatus`` spelling for ``state``.

    Raises ``KeyError`` for a value outside ``PaperOrderState`` rather than substituting a
    default: a fabricated ``legacy_status`` would change what
    ``GET /api/paper/orders?status=OPEN`` returns.
    """
    return LEGACY_STATUS_FOR_STATE[PaperOrderState(state)]


def _reachable_from(origin: PaperOrderState) -> FrozenSet[PaperOrderState]:
    """Return the transitive closure of ``PAPER_ORDER_TRANSITIONS`` from ``origin``.

    The oracle for the reachability property: every persisted state must be reachable
    from ``CREATED`` under the Requirement 16.2 transitions.
    """
    seen: Dict[PaperOrderState, None] = {origin: None}
    frontier = [origin]
    while frontier:
        state = frontier.pop()
        for target in PAPER_ORDER_TRANSITIONS.get(state, ()):
            if target not in seen:
                seen[target] = None
                frontier.append(target)
    return frozenset(seen)


#: Every one of the six values is reachable from ``CREATED``; there is no orphan state.
REACHABLE_FROM_CREATED: FrozenSet[PaperOrderState] = _reachable_from(
    PaperOrderState.CREATED
)


__all__ = [
    "PaperOrderState",
    "PAPER_ORDER_TRANSITIONS",
    "TERMINAL",
    "LEGACY_STATUS_FOR_STATE",
    "REACHABLE_FROM_CREATED",
    "can_transition",
    "is_terminal",
    "legacy_status_for",
]
