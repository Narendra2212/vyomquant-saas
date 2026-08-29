"""
backend_app/backend/order_lifecycle_state.py - the canonical order/signal status vocabulary.

Spec: trading-lifecycle-integration task 1.1. ``design.md`` -> "Canonical status vocabulary
and its reconciliation (Requirement 16)". Requirements 16.1, 16.2, 16.3, 16.4, 16.5.

Exposes
-------
OrderLifecycleState                the 9 canonical values (Requirement 16.1)
TERMINAL_STATES / NON_TERMINAL_STATES
ORDER_LIFECYCLE_STATE_VALUES       the vocabulary as wire/DB strings, for the CHECK constraint
ORDER_LIFECYCLE_TRANSITIONS        the transition table (Requirement 16.4)
ORDER_STATE_MAP / SIGNALS_STATUS_MAP / TRACE_STATUS_MAP
                                   the three one-directional reconciliation tables (16.3)
INTERNAL_SUBSTATE_MAP              16.3's internal-only pre-submission sub-states
resolve_order_lifecycle_state(...) the conflict resolver (Requirement 16.5)
assert_transition_legal(...)       the transition gate (Requirement 16.4/16.6)

WHY THIS MODULE IS PURE
-----------------------
Same convention ``strategy_dag/schema.py`` already establishes: module import pulls only
the standard library - no database handle, no HTTP client, no FastAPI import, no I/O. The
reconciliation tables are consulted by the Live_Runtime, by ``signal_service``, by the
signal-trace router, by the 005b backfill and by the migration's own ``CHECK`` constraint;
every one of those callers must be able to import the vocabulary without dragging a
connection pool or an event loop behind it, and a transition must be testable without a
TestClient and without a venue.

That is also why the three source vocabularies are **transcribed as their wire strings**
rather than imported as enums. ``core.order_state_engine.OrderState`` lives in a module
that imports ``background_tasks`` and an exchange client; ``signal_trace_engine.TraceStatus``
lives beside an asyncio engine; ``signals.status`` is a DB-unenforced text column with no
Python enum at all. All three are ``str``-valued, so a lookup keyed by the string value
accepts the enum member, the raw column value and the wire payload identically, and the
mapping stays total without an import cycle. Tests 1.2 pin the transcription against the
real enums, so a value added to either source enum cannot drift away from these tables
unnoticed.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
Persisting a transition. ``design.md`` sketches ``record_transition`` alongside these
tables, but writing to ``order_lifecycle_transitions`` needs a database handle, which is
exactly what this module must not have. The write order the codebase already proves for
the sibling problem (``strategy_lifecycle.apply_version_state``: gate, then write, then
audit) is kept - :func:`assert_transition_legal` is the gate, and ``signal_service``
(task 10.2) owns the write and the audit that follow it.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, FrozenSet, Mapping, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════════
# THE CANONICAL VOCABULARY (Requirement 16.1)
# ══════════════════════════════════════════════════════════════════════════


class OrderLifecycleState(str, Enum):
    """The 9 values, and the only values, reported as an ``Order_Lifecycle_State``.

    ``str``-valued for the same reason ``OrderState`` is: the member is its own wire and
    column value, so a JSON response, a ``CHECK`` constraint and a comparison in Python
    all speak one spelling. Uppercase because Requirement 16.1 spells it uppercase and
    ``signals.order_lifecycle_state``'s ``CHECK`` is generated from
    :data:`ORDER_LIFECYCLE_STATE_VALUES`.
    """

    #: Minted and persisted; nothing has been reported about it yet.
    GENERATED = "GENERATED"
    #: Accepted for submission or mid pre-submission check; not yet at the exchange.
    PENDING = "PENDING"
    #: At the exchange, confirmed or awaiting confirmation, no fill yet.
    SUBMITTED = "SUBMITTED"
    #: At least one fill, quantity outstanding. Repeats on every further partial fill.
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    #: Fully filled.
    EXECUTED = "EXECUTED"
    #: Filled *and* reconciled against position close. Terminal.
    CLOSED = "CLOSED"
    #: Could not be completed. Terminal.
    FAILED = "FAILED"
    #: Cancelled, or expired before it ever became an order. Terminal.
    CANCELLED = "CANCELLED"
    #: Refused before reaching the exchange (risk validation, exchange rejection). Terminal.
    REJECTED = "REJECTED"

    def __str__(self) -> str:  # pragma: no cover - convenience for log lines
        return self.value


#: Requirement 16.1's four terminal states: no transition leaves them.
TERMINAL_STATES: FrozenSet[OrderLifecycleState] = frozenset(
    {
        OrderLifecycleState.CLOSED,
        OrderLifecycleState.FAILED,
        OrderLifecycleState.CANCELLED,
        OrderLifecycleState.REJECTED,
    }
)

#: Requirement 16.1's five non-terminal states. Exhaustive with :data:`TERMINAL_STATES`
#: over the enum - a test pins that, so a tenth value cannot arrive uncategorised.
NON_TERMINAL_STATES: FrozenSet[OrderLifecycleState] = frozenset(
    {
        OrderLifecycleState.GENERATED,
        OrderLifecycleState.PENDING,
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.PARTIALLY_EXECUTED,
        OrderLifecycleState.EXECUTED,
    }
)

#: The vocabulary as plain strings, in the requirement's own order. This is the list the
#: 005b migration's ``chk_signals_order_lifecycle_state`` and
#: ``chk_order_lifecycle_transitions_to_state`` are written from, so the constraint and
#: the enum cannot disagree.
ORDER_LIFECYCLE_STATE_VALUES: Tuple[str, ...] = tuple(
    state.value for state in OrderLifecycleState
)


# ══════════════════════════════════════════════════════════════════════════
# THE TRANSITION TABLE (Requirement 16.4)
# ══════════════════════════════════════════════════════════════════════════

#: ``design.md``'s table, one entry per state, transcribed edge by edge - the same shape
#: ``strategy_lifecycle.VERSION_TRANSITIONS`` already establishes, including the "every
#: state is a key, terminal states map to ``()``" rule that keeps a missing key from
#: silently reading as terminal.
#:
#: Three edges deserve their note:
#:
#: * ``PARTIALLY_EXECUTED -> PARTIALLY_EXECUTED`` is a real self-edge: Requirement 16.4
#:   asks for it explicitly so repeated partial fills each record a transition.
#: * The failure branches (``REJECTED`` / ``FAILED`` / ``CANCELLED``) are reachable from
#:   every non-terminal state *that the underlying ``OrderState`` machine permits*.
#:   ``OrderState`` permits cancel and fail from every non-terminal state, with one
#:   asymmetry transcribed rather than smoothed over: an order that is already ``filled``
#:   cannot be cancelled or rejected, so ``EXECUTED`` offers only ``CLOSED`` and
#:   ``FAILED`` (a settlement/reconciliation failure after a fill is real; a cancellation
#:   after a fill is not).
#: * ``CLOSED`` is reachable only from ``EXECUTED``, and only through this table: no
#:   source vocabulary has an equivalent value (see :data:`ORDER_STATE_MAP`).
ORDER_LIFECYCLE_TRANSITIONS: Dict[OrderLifecycleState, Tuple[OrderLifecycleState, ...]] = {
    OrderLifecycleState.GENERATED: (
        OrderLifecycleState.PENDING,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.FAILED,
        OrderLifecycleState.CANCELLED,
    ),
    OrderLifecycleState.PENDING: (
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.FAILED,
        OrderLifecycleState.CANCELLED,
    ),
    OrderLifecycleState.SUBMITTED: (
        OrderLifecycleState.PARTIALLY_EXECUTED,
        OrderLifecycleState.EXECUTED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.FAILED,
        OrderLifecycleState.CANCELLED,
    ),
    OrderLifecycleState.PARTIALLY_EXECUTED: (
        OrderLifecycleState.PARTIALLY_EXECUTED,
        OrderLifecycleState.EXECUTED,
        OrderLifecycleState.FAILED,
        OrderLifecycleState.CANCELLED,
    ),
    OrderLifecycleState.EXECUTED: (
        OrderLifecycleState.CLOSED,
        OrderLifecycleState.FAILED,
    ),
    OrderLifecycleState.CLOSED: (),
    OrderLifecycleState.FAILED: (),
    OrderLifecycleState.CANCELLED: (),
    OrderLifecycleState.REJECTED: (),
}


# ══════════════════════════════════════════════════════════════════════════
# THE THREE RECONCILIATION TABLES (Requirements 16.2, 16.3)
# ══════════════════════════════════════════════════════════════════════════

#: ``core/order_state_engine.py``'s ``OrderState``, every member, keyed by its own
#: ``str`` value. One-directional: a source value maps forward onto the canonical
#: vocabulary and nothing maps back, because the canonical vocabulary has a state
#: (``CLOSED``) no source carries - deciding "closed" needs position-close /
#: fill-deduplication data that none of the three sources holds alone.
ORDER_STATE_MAP: Dict[str, OrderLifecycleState] = {
    "created": OrderLifecycleState.PENDING,  # not yet sent to the exchange
    "submitted": OrderLifecycleState.SUBMITTED,
    "open": OrderLifecycleState.SUBMITTED,  # confirmed open, no fill yet
    "partial": OrderLifecycleState.PARTIALLY_EXECUTED,
    "filled": OrderLifecycleState.EXECUTED,
    "cancelling": OrderLifecycleState.PENDING,  # cancel requested, not yet confirmed
    "cancelled": OrderLifecycleState.CANCELLED,
    "failed": OrderLifecycleState.FAILED,
    "timed_out": OrderLifecycleState.FAILED,
}

#: ``signals.status`` as ``002_signal_trace.sql`` / ``003_signal_trace_restoration.sql``
#: leave it: a DB-unenforced text column with seven observed spellings and no Python
#: enum. Every one of them maps, so the 005b backfill (task 3.3) leaves no row ``NULL``.
SIGNALS_STATUS_MAP: Dict[str, OrderLifecycleState] = {
    "pending": OrderLifecycleState.PENDING,
    "accepted": OrderLifecycleState.SUBMITTED,
    "rejected": OrderLifecycleState.REJECTED,
    "executed": OrderLifecycleState.EXECUTED,
    "failed": OrderLifecycleState.FAILED,
    "cancelled": OrderLifecycleState.CANCELLED,
    "expired": OrderLifecycleState.CANCELLED,  # an expired signal never became an order
}

#: ``signal_trace_engine.TraceStatus``, every member. ``TraceStatus`` describes how far a
#: DAG *evaluation* got, which is why two of its values are pre-order states.
TRACE_STATUS_MAP: Dict[str, OrderLifecycleState] = {
    "pending": OrderLifecycleState.PENDING,
    "running": OrderLifecycleState.PENDING,  # mid-DAG-evaluation, no order yet
    "completed": OrderLifecycleState.EXECUTED,  # decision reached EXECUTE and it settled
    "failed": OrderLifecycleState.FAILED,
    "blocked": OrderLifecycleState.REJECTED,  # risk validation blocked it
    "timeout": OrderLifecycleState.FAILED,
}

#: Requirement 16.3's carve-out. ``risk-check-pending`` and ``approved-pending-submission``
#: are **not** enum members anywhere in this codebase - they exist only as intermediate
#: booleans on ``RiskValidationTrace`` - but the requirement names them explicitly and
#: says they report as ``PENDING``, so they are mapped here rather than left to a caller
#: to guess. Kept in their own table so the three source tables stay exactly their source
#: vocabularies; :func:`map_signals_status` consults both.
INTERNAL_SUBSTATE_MAP: Dict[str, OrderLifecycleState] = {
    "risk_check_pending": OrderLifecycleState.PENDING,
    "approved_pending_submission": OrderLifecycleState.PENDING,
}

#: The internal sub-state names as Requirement 16.3 spells them (hyphenated). Both
#: spellings resolve, because :func:`normalise_source_value` folds ``-`` onto ``_``.
INTERNAL_PRESUBMISSION_SUBSTATES: Tuple[str, ...] = (
    "risk-check-pending",
    "approved-pending-submission",
)

#: Source names in the resolver's priority order (Requirement 16.5), published so a
#: caller reporting a conflict can name the source that won.
SOURCE_PRIORITY: Tuple[str, ...] = ("order_state", "signals_status", "trace_status")

_SOURCE_TABLES: Dict[str, Mapping[str, OrderLifecycleState]] = {
    "order_state": ORDER_STATE_MAP,
    "signals_status": SIGNALS_STATUS_MAP,
    "trace_status": TRACE_STATUS_MAP,
}


# ══════════════════════════════════════════════════════════════════════════
# REFUSALS
# ══════════════════════════════════════════════════════════════════════════


class OrderLifecycleRejected(Exception):
    """One classified refusal from this module.

    Field-for-field the shape ``deployment_binding.DeployRejected`` already establishes
    (``code``, ``message``, ``details``, ``http_status``, ``to_detail()``), so a router
    that maps one maps this too - but declared here rather than inherited, because
    ``deployment_binding`` imports a database handle and this module must not.

    Deliberately not a ``ValueError``: several call sites already read a bare
    ``ValueError`` as "not found", and an illegal transition is neither missing nor a
    client typo - it is Requirement 16.6's rejected write, and the prior value stands.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[Mapping[str, Any]] = None,
        *,
        http_status: int = 409,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: Dict[str, Any] = dict(details or {})
        self.http_status = int(http_status)

    def to_detail(self) -> Dict[str, Any]:
        """The FastAPI ``detail`` body. ``error`` first, matching the router shape."""
        return {"error": self.code, "message": self.message, **self.details}


class UnknownSourceStatus(OrderLifecycleRejected):
    """A source reading outside its own vocabulary reached the reconciliation layer.

    A refusal and not a fallback on purpose. Requirement 16.3 makes the mapping *total*
    over each source vocabulary, so an unmapped value means a source grew a state nobody
    reconciled; guessing ``GENERATED``, or quietly deferring to the next-priority source,
    would report a state about real money that no source actually claimed.
    """

    def __init__(self, source: str, value: Any, known: Tuple[str, ...]):
        super().__init__(
            "ORDER_LIFECYCLE_SOURCE_STATE_UNRECOGNISED",
            f"{source}={value!r} is not one of {list(known)}, so it cannot be reconciled "
            f"onto an Order_Lifecycle_State.",
            {"source": source, "value": None if value is None else str(value), "recognised": list(known)},
            http_status=500,
        )
        self.source = source
        self.value = value


# ══════════════════════════════════════════════════════════════════════════
# NORMALISATION
# ══════════════════════════════════════════════════════════════════════════


def normalise_source_value(value: Any) -> Optional[str]:
    """A source reading as a reconciliation-table key, or ``None`` when it is absent.

    Accepts the enum member (``OrderState.FILLED``), the raw column value (``"filled"``),
    a wire string of any case, and the hyphenated spelling of an internal sub-state.
    ``None`` and blank both mean "this source reported nothing", which the resolver
    treats as "ask the next source" - distinct from an unrecognised value, which is a
    refusal.
    """
    if value is None:
        return None
    raw = getattr(value, "value", value)
    text = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    return text or None


def normalise_lifecycle_state(value: Any) -> Optional[OrderLifecycleState]:
    """``value`` as an :class:`OrderLifecycleState`, or ``None`` when outside the 9.

    Returns ``None`` rather than raising, following ``strategy_lifecycle``'s
    ``normalise_lifecycle_state``: the caller decides whether an unrecognised label is a
    refusal (in :func:`assert_transition_legal` it is) and gets to say what it was.
    """
    if isinstance(value, OrderLifecycleState):
        return value
    if value is None:
        return None
    raw = getattr(value, "value", value)
    text = str(raw).strip().upper().replace("-", "_").replace(" ", "_")
    try:
        return OrderLifecycleState(text)
    except ValueError:
        return None


def is_terminal(state: Any) -> bool:
    """Whether ``state`` is one of Requirement 16.1's four terminal states."""
    return normalise_lifecycle_state(state) in TERMINAL_STATES


# ══════════════════════════════════════════════════════════════════════════
# THE MAPPING, PER SOURCE (Requirement 16.3)
# ══════════════════════════════════════════════════════════════════════════


def _map_through(
    table: Mapping[str, OrderLifecycleState],
    source: str,
    value: Any,
    *,
    extra: Optional[Mapping[str, OrderLifecycleState]] = None,
) -> Optional[OrderLifecycleState]:
    key = normalise_source_value(value)
    if key is None:
        return None
    mapped = table.get(key)
    if mapped is None and extra is not None:
        mapped = extra.get(key)
    if mapped is None:
        known = tuple(table) + (tuple(extra) if extra else ())
        raise UnknownSourceStatus(source, value, known)
    return mapped


def map_order_state(value: Any) -> Optional[OrderLifecycleState]:
    """``OrderState`` (member or value) onto the canonical vocabulary. ``None`` if absent."""
    return _map_through(ORDER_STATE_MAP, "order_state", value)


def map_signals_status(value: Any) -> Optional[OrderLifecycleState]:
    """``signals.status`` onto the canonical vocabulary. ``None`` if absent.

    Also accepts Requirement 16.3's internal-only pre-submission sub-states
    (:data:`INTERNAL_PRESUBMISSION_SUBSTATES`), both of which report as ``PENDING``.
    """
    return _map_through(
        SIGNALS_STATUS_MAP, "signals_status", value, extra=INTERNAL_SUBSTATE_MAP
    )


def map_trace_status(value: Any) -> Optional[OrderLifecycleState]:
    """``TraceStatus`` (member or value) onto the canonical vocabulary. ``None`` if absent."""
    return _map_through(TRACE_STATUS_MAP, "trace_status", value)


# ══════════════════════════════════════════════════════════════════════════
# CONFLICT RESOLUTION (Requirement 16.5)
# ══════════════════════════════════════════════════════════════════════════


def resolve_order_lifecycle_state(
    order_state: Any = None,
    signals_status: Any = None,
    trace_status: Any = None,
) -> OrderLifecycleState:
    """The single reported ``Order_Lifecycle_State`` for one signal/order pair.

    Requirement 16.5's fixed priority, exactly: ``OrderState`` > ``signals.status`` >
    ``TraceStatus``. The highest-priority source that reported *anything* decides, whether
    or not the lower-priority sources agree, so two readings of the same three inputs can
    never differ - it is a pure function of its arguments and consults nothing else.

    ``GENERATED`` when no source reported: the signal was just minted and nothing has
    happened to it yet. That is a real state, not a default - it is where every signal
    legitimately starts.

    Raises
        :class:`UnknownSourceStatus` when a source reported a value outside its own
        vocabulary. Never silently falls through to the next source: the priority order is
        about *which source is authoritative*, not about which source is parseable.
    """
    for source, value in (
        ("order_state", order_state),
        ("signals_status", signals_status),
        ("trace_status", trace_status),
    ):
        if source == "signals_status":
            mapped = map_signals_status(value)
        else:
            mapped = _map_through(_SOURCE_TABLES[source], source, value)
        if mapped is not None:
            return mapped
    return OrderLifecycleState.GENERATED


def resolution_report(
    order_state: Any = None,
    signals_status: Any = None,
    trace_status: Any = None,
) -> Dict[str, Any]:
    """:func:`resolve_order_lifecycle_state`, plus what each source said and who won.

    For the signal-trace detail view and for diagnosing a genuine three-way disagreement:
    the resolver returns one value by design, and this says why that value and not
    another. Read-only, allocates nothing outside its own return.
    """
    per_source: Dict[str, Optional[str]] = {}
    winner: Optional[str] = None
    resolved = OrderLifecycleState.GENERATED

    for source, value in (
        ("order_state", order_state),
        ("signals_status", signals_status),
        ("trace_status", trace_status),
    ):
        mapped = (
            map_signals_status(value)
            if source == "signals_status"
            else _map_through(_SOURCE_TABLES[source], source, value)
        )
        per_source[source] = mapped.value if mapped is not None else None
        if mapped is not None and winner is None:
            winner, resolved = source, mapped

    reported = {v for v in per_source.values() if v is not None}
    return {
        "order_lifecycle_state": resolved.value,
        "resolved_from": winner,
        "sources": per_source,
        "priority": list(SOURCE_PRIORITY),
        "conflict": len(reported) > 1,
    }


# ══════════════════════════════════════════════════════════════════════════
# THE TRANSITION GATE (Requirements 16.4, 16.6)
# ══════════════════════════════════════════════════════════════════════════


def legal_transitions(state: Any) -> Tuple[OrderLifecycleState, ...]:
    """Every state ``state`` may legally move to. ``()`` for terminal or unrecognised."""
    label = normalise_lifecycle_state(state)
    if label is None:
        return ()
    return ORDER_LIFECYCLE_TRANSITIONS.get(label, ())


def is_transition_legal(current: Any, next_state: Any) -> bool:
    """Whether ``current -> next_state`` is an edge in Requirement 16.4's table."""
    target = normalise_lifecycle_state(next_state)
    return target is not None and target in legal_transitions(current)


def assert_transition_legal(
    current: Any,
    next_state: Any,
    *,
    signal_id: Optional[str] = None,
    order_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> Tuple[OrderLifecycleState, OrderLifecycleState]:
    """Requirement 16.4's gate, run **before** the write (Requirement 16.6).

    Returns
        ``(current, next_state)`` normalised, when the transition is legal.

    Raises
        :class:`OrderLifecycleRejected` (409). ``ORDER_LIFECYCLE_STATE_UNRECOGNISED`` when
        either end is outside the 9 values - which is a refusal, not a pass-through,
        because that write would be a 23514 from ``chk_signals_order_lifecycle_state`` -
        and ``ORDER_LIFECYCLE_TRANSITION_INVALID`` when both are real states but the edge
        is not in the table. Both carry ``order_lifecycle_state`` (the current one, by
        name), ``requested_state`` and ``legal_transitions``, so the caller can report the
        rejected transition rather than just "invalid", and the caller leaves the prior
        value unchanged by never reaching its own write.
    """
    current_label = normalise_lifecycle_state(current)
    target_label = normalise_lifecycle_state(next_state)
    subject = _subject(signal_id, order_id)

    if current_label is None or target_label is None:
        unknown = "current" if current_label is None else "requested"
        raise OrderLifecycleRejected(
            "ORDER_LIFECYCLE_STATE_UNRECOGNISED",
            f"Cannot move {subject} from {current!r} to {next_state!r}: the {unknown} "
            f"state is not one of {list(ORDER_LIFECYCLE_STATE_VALUES)}.",
            {
                "order_lifecycle_state": current_label.value
                if current_label
                else (None if current is None else str(current)),
                "requested_state": target_label.value
                if target_label
                else (None if next_state is None else str(next_state)),
                "recognised_states": list(ORDER_LIFECYCLE_STATE_VALUES),
                "legal_transitions": [s.value for s in legal_transitions(current)],
                "signal_id": signal_id,
                "order_id": order_id,
                "reason": reason,
            },
        )

    if target_label in ORDER_LIFECYCLE_TRANSITIONS.get(current_label, ()):
        return current_label, target_label

    allowed = [s.value for s in ORDER_LIFECYCLE_TRANSITIONS.get(current_label, ())]
    raise OrderLifecycleRejected(
        "ORDER_LIFECYCLE_TRANSITION_INVALID",
        f"Cannot move {subject} from {current_label.value} to {target_label.value}: that "
        f"transition is not part of the order lifecycle. From {current_label.value} the "
        f"legal transitions are "
        f"{', '.join(allowed) if allowed else 'none - it is a terminal state'}.",
        {
            "order_lifecycle_state": current_label.value,
            "requested_state": target_label.value,
            "legal_transitions": allowed,
            "signal_id": signal_id,
            "order_id": order_id,
            "reason": reason,
        },
    )


def _subject(signal_id: Optional[str], order_id: Optional[str]) -> str:
    if signal_id and order_id:
        return f"signal {signal_id} (order {order_id})"
    if signal_id:
        return f"signal {signal_id}"
    if order_id:
        return f"order {order_id}"
    return "this signal"


__all__ = [
    "INTERNAL_PRESUBMISSION_SUBSTATES",
    "INTERNAL_SUBSTATE_MAP",
    "NON_TERMINAL_STATES",
    "ORDER_LIFECYCLE_STATE_VALUES",
    "ORDER_LIFECYCLE_TRANSITIONS",
    "ORDER_STATE_MAP",
    "OrderLifecycleRejected",
    "OrderLifecycleState",
    "SIGNALS_STATUS_MAP",
    "SOURCE_PRIORITY",
    "TERMINAL_STATES",
    "TRACE_STATUS_MAP",
    "UnknownSourceStatus",
    "assert_transition_legal",
    "is_terminal",
    "is_transition_legal",
    "legal_transitions",
    "map_order_state",
    "map_signals_status",
    "map_trace_status",
    "normalise_lifecycle_state",
    "normalise_source_value",
    "resolution_report",
    "resolve_order_lifecycle_state",
]
