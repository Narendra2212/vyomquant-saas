"""
backend_app/backend/paper/paper_events.py - the Paper_Channel's vocabulary, payloads and envelope.

Spec: marketplace-subscriptions-paper-trading tasks 26.1 and 26.2. ``design.md`` ->
"``paper/paper_events.py`` and the Paper_Channel". Requirements 19.2, 19.3, 19.7, 26.3.

Exposes
-------
PaperEvent                    the SIXTEEN Paper_Channel event types of Requirement 19.2
PAPER_EVENT_TYPES             the same sixteen as a tuple, in 009's order
PAPER_CHANNEL_EVENTS          the same sixteen as a frozenset - what ``ws_channels`` registers
PAPER_EVENT_SCHEMA_VERSION    ``'paper.v1'``
PAYLOAD_MODEL_FOR_EVENT       ``event type -> the Pydantic model of its payload``
PaperEventEnvelope            the eight-field frame of Requirement 19.3
build_envelope / payload_jsonb / paper_channel / format_emitted_at / utc_now / new_event_id
next_sequence                 the per-session sequence allocator
record_event                  allocate, envelope, append - the one emitting path

THIS MODULE IS THE ONE DEFINITION OF THE EVENT VOCABULARY
---------------------------------------------------------
Before task 26.1 the sixteen types were spelled in three places: ``chk_paper_event_type`` in
``backend_app/migrations/009_paper_trading.sql``, ``paper_repository.PAPER_EVENT_TYPES`` (which
validates the ``event_type`` column) and, for one of them, ``paper_market_feed``'s
``PAPER_ERROR_EVENT_TYPE`` / ``PAPER_EVENT_SCHEMA_VERSION``. 009's own comment says the SQL list
"MUST equal the type set ``paper_events.py`` emits", so:

* :class:`PaperEvent` here is the definition. :data:`PAPER_EVENT_TYPES` and
  :data:`PAPER_CHANNEL_EVENTS` are DERIVED from it, never restated.
* ``paper_repository`` imports :data:`PAPER_EVENT_TYPES` from here (the same tuple object, not a
  copy) and keeps validating the column it owns.
* ``paper_market_feed`` imports :data:`PAPER_EVENT_SCHEMA_VERSION` from here and derives
  ``PAPER_ERROR_EVENT_TYPE`` from :attr:`PaperEvent.ERROR`.
* ``ws_channels`` imports :data:`PAPER_CHANNEL_EVENTS` for ``PAPER_FAMILY.events``.
* 009's CHECK stays the guarantee, and
  ``tests/test_paper_event_schemas.py::test_the_check_constraints_value_list_and_the_vocabulary_
  are_the_same_set`` parses the migration and asserts SET EQUALITY, so the comment is now
  mechanical rather than aspirational.

The audit of the pre-existing definitions found NO disagreement:
``paper_repository.PAPER_EVENT_TYPES`` and both spellings of ``chk_paper_event_type`` (section 12's
inline ``CONSTRAINT`` and section 12b's guarded ``ALTER TABLE``) held the same sixteen values in
the same order. There was duplication to remove, not a defect to fix.

``paper_simulator.SCHEMA_VERSION`` is deliberately NOT folded in. It is the version of
``paper_sessions.config`` - a stored configuration shape read back by
``session_config_from_jsonb`` - and this is the version of a WIRE ENVELOPE read by a browser. They
carry the same literal today and they are two contracts: bumping one must not silently bump the
other.

WHY THIS MODULE IMPORTS ALMOST NOTHING AT MODULE SCOPE
-----------------------------------------------------
``backend_app/backend/ws_channels.py`` imports :data:`PAPER_CHANNEL_EVENTS` from here, and
``paper_repository`` imports :data:`PAPER_EVENT_TYPES`. Both of those import *into* this module's
direction, so this module must not import either of them at module scope or the import would be
circular. Module scope therefore holds the standard library, Pydantic, and the two pure
state-vocabulary leaves (``paper_order_state``, ``order_lifecycle_state``) - nothing else.
:func:`paper_channel`, :func:`next_sequence` and :func:`record_event` reach ``ws_channels`` and
``paper_repository`` through function-local imports, which is the pattern
``core/websocket_auth._owner_relations`` already uses for the same reason.

NO ``random``, AND WHY ``uuid`` IS NOT ONE
-----------------------------------------
``tests/test_paper_no_random.py`` walks this package's source and fails on any ``random`` or
``numpy.random`` reach. :func:`new_event_id` uses :func:`uuid.uuid4`, which is the platform's
CSPRNG rather than the reproducible-sequence generator Requirement 18.1 is about: an event's
identity is not a price, a quantity or a fill decision, and Requirement 15.4's replay compares
balances, positions, order states and equity - not event ids. Nothing in a replay reads an
``event_id``; ``uq_paper_event_id`` reads it, and a collision there must be impossible rather than
deterministic.

EXACT MONEY, ALWAYS
-------------------
Every price, quantity, balance and profit-and-loss field is a :class:`~decimal.Decimal` and a
``float`` is REFUSED at validation (:func:`_refuse_float`), not coerced: by the time a ``float``
reaches here the exactness is gone and ``str(0.07)`` would put a value in a frame that the caller
never held. :func:`payload_jsonb` renders a ``Decimal`` as its exact decimal string, which is what
``paper_events.payload`` stores and what a replay reads back (Requirements 18.1, 15.4) - the same
rule ``paper_repository._jsonb`` applies to the column.

WHAT THIS MODULE DOES NOT DO
----------------------------
No broadcast, no replay, no heartbeat, no slow-consumer policy and no subscription registry. Those
are task 26.4 and they live in ``paper/paper_channel.py``, over the registries and the pending
counter of ``api_ws/ws_manager.py``. This module produces frames and appends them; it does not
deliver them.

``design.md`` names the delivery entry point ``paper_events.broadcast(session_id, frame)``, so
:func:`broadcast` and :func:`replay` exist here as ONE-LINE delegations to ``paper_channel`` - the
name the design uses resolves and the implementation stays in one place. They reach
``paper_channel`` through a function-local import, the same way :func:`paper_channel` reaches
``ws_channels``, because ``paper_channel`` imports this module at module scope.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple, Type

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from typing_extensions import Annotated, Literal

from backend_app.backend.order_lifecycle_state import OrderLifecycleState
from backend_app.backend.paper.paper_order_state import PaperOrderState

# ══════════════════════════════════════════════════════════════════════════
# THE SIXTEEN EVENT TYPES (Requirement 19.2)
# ══════════════════════════════════════════════════════════════════════════

#: ``design.md`` -> "``paper/paper_events.py`` and the Paper_Channel". Same literal as
#: ``paper_sessions.config``'s ``schema_version`` today, and a separate contract - see the module
#: docstring.
PAPER_EVENT_SCHEMA_VERSION = "paper.v1"


class PaperEvent(str, Enum):
    """The sixteen frames ``paper.{session_id}`` carries. Requirement 19.2, exhaustively.

    ``str`` mixin for the reason ``PaperOrderState`` has one: a member round-trips through JSON and
    through the ``paper_events.event_type`` text column with no conversion at the call site, and
    ``paper_repository._require_text`` reads an ``Enum`` as its ``value`` rather than as its
    ``repr``.

    Declared in 009's order, so :data:`PAPER_EVENT_TYPES` and ``chk_paper_event_type``'s value list
    read the same way as well as holding the same set. There is deliberately no seventeenth member:
    a type this enum admits and the CHECK does not is a ``23514`` in production, and the migration's
    comment ("These MUST equal the type set ``paper_events.py`` emits") is asserted mechanically in
    ``tests/test_paper_event_schemas.py``.
    """

    SESSION_STARTED = "paper_session_started"
    SESSION_PAUSED = "paper_session_paused"
    SESSION_RESUMED = "paper_session_resumed"
    SESSION_STOPPED = "paper_session_stopped"
    MARKET_TICK = "market_tick"
    SIGNAL_GENERATED = "signal_generated"
    ORDER_CREATED = "paper_order_created"
    ORDER_ACCEPTED = "paper_order_accepted"
    ORDER_PARTIALLY_FILLED = "paper_order_partially_filled"
    ORDER_FILLED = "paper_order_filled"
    ORDER_REJECTED = "paper_order_rejected"
    POSITION_UPDATED = "paper_position_updated"
    BALANCE_UPDATED = "paper_balance_updated"
    PNL_UPDATED = "paper_pnl_updated"
    DRAWDOWN_UPDATED = "paper_drawdown_updated"
    ERROR = "paper_error"


#: The sixteen as a tuple, in 009's order. ``paper_repository`` imports THIS OBJECT for the
#: ``event_type`` column's ``_one_of`` check, so the column vocabulary and the channel vocabulary
#: cannot drift.
PAPER_EVENT_TYPES: Tuple[str, ...] = tuple(member.value for member in PaperEvent)

#: The sixteen as a set - what ``ws_channels.PAPER_FAMILY.events`` is built from. A ``frozenset``
#: rather than a ``set`` because the family that holds it is a frozen dataclass and a mutable
#: vocabulary in a frozen descriptor is a trap.
PAPER_CHANNEL_EVENTS: FrozenSet[str] = frozenset(PAPER_EVENT_TYPES)

#: The ``paper_error`` codes ``design.md`` names. Not a closed vocabulary and not validated as one:
#: task 26.4 added ``CLIENT_FELL_BEHIND`` and the requirements name the others, so a code the
#: model refused would be a refusal to REPORT an error - which is worse than reporting an
#: unrecognised one. Kept as a named tuple so a producer can reach for the right spelling.
#:
#: Who emits each one: ``FEED_DISCONNECTED`` is ``paper_market_feed``'s and is RECORDED in
#: ``paper_events`` because a dropped feed happened to the session; ``HISTORY_INCOMPLETE``
#: (Requirement 19.9) and ``CLIENT_FELL_BEHIND`` (Requirement 19.11) are ``paper_channel``'s and are
#: NOT recorded, because both are facts about one connection - see
#: ``paper_channel.error_frame``'s docstring for why recording them would be wrong rather than
#: merely unnecessary.
PAPER_ERROR_CODES: Tuple[str, ...] = (
    "FEED_DISCONNECTED",
    "HISTORY_INCOMPLETE",
    "CLIENT_FELL_BEHIND",
    "CONCURRENCY_CONFLICT",
    "SIMULATOR_MISCONFIGURED",
)


def paper_event(value: Any) -> PaperEvent:
    """``value`` as a :class:`PaperEvent`, or ``ValueError`` naming the sixteen.

    A member, a member's value and nothing else. The failure is raised here so a producer that
    reached for a seventeenth type is told which sixteen exist, rather than having the refusal
    arrive from PostgreSQL as a ``23514`` that the error handler has to redact.
    """
    if isinstance(value, PaperEvent):
        return value
    text = "" if value is None else str(value).strip()
    try:
        return PaperEvent(text)
    except ValueError as exc:
        raise ValueError(
            f"{text!r} is not one of the sixteen Paper_Channel event types "
            f"{PAPER_EVENT_TYPES} (Requirement 19.2)"
        ) from exc


# ══════════════════════════════════════════════════════════════════════════
# EXACTNESS: THE FIELD TYPES EVERY PAYLOAD IS BUILT FROM
# ══════════════════════════════════════════════════════════════════════════


def _refuse_float(value: Any) -> Any:
    """Refuse a ``float`` before Pydantic can coerce one into a ``Decimal``.

    Requirement 18.1. ``Decimal(0.07)`` is ``0.070000000000000006938...`` and
    ``Decimal(str(0.07))`` is a value the caller never held either - by the time a binary float
    arrives the exact figure is already gone, so the only safe answer is to refuse it. A
    ``Decimal``, an ``int`` and an exact decimal string all pass.
    """
    if isinstance(value, float):
        raise ValueError(
            "a paper event payload carries exact Decimal money and prices; a float cannot be "
            "reproduced by a replay (Requirement 18.1). Pass a Decimal or an exact decimal string."
        )
    return value


#: An exact money or price value. Refuses a ``float``; renders as an exact decimal string.
ExactDecimal = Annotated[Decimal, BeforeValidator(_refuse_float)]

#: The same, for a figure that is genuinely ABSENT until a price has been validated. ``None`` stays
#: ``None``: Requirement 28.5 forbids substituting a zero for an unavailable value.
OptionalExactDecimal = Annotated[Optional[Decimal], BeforeValidator(_refuse_float)]

#: An exact whole number of Minor_Units. ``float`` refused for the same reason.
MinorUnits = Annotated[int, BeforeValidator(_refuse_float)]

OptionalMinorUnits = Annotated[Optional[int], BeforeValidator(_refuse_float)]


def format_emitted_at(value: Any) -> str:
    """One UTC instant at MICROSECOND resolution, as ``...THH:MM:SS.ffffffZ``.

    Requirement 19.3 asks for "millisecond or finer"; 009 declares
    ``emitted_at TIMESTAMPTZ(6)`` so that two events emitted inside one millisecond still order by
    ``sequence`` rather than colliding on the timestamp. Six fractional digits are therefore always
    written, including when they are all zero - a variable-width timestamp would sort as text
    differently from how it sorts as an instant.

    A naive ``datetime`` is read as UTC, matching ``paper_repository._instant`` and 009's refusal of
    ``timestamp without time zone``. An aware one in another zone is CONVERTED, not relabelled. A
    string is returned unchanged, so an instant read out of a row and written back is exact.
    """
    if value is None:
        raise ValueError("emitted_at is required")
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        moment = moment.astimezone(timezone.utc)
        return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{moment.microsecond:06d}Z"
    text = str(value).strip()
    if not text:
        raise ValueError("emitted_at is required")
    return text


#: A UTC instant in a payload, rendered the way :func:`format_emitted_at` renders one.
Instant = Annotated[str, BeforeValidator(format_emitted_at)]

OptionalInstant = Annotated[
    Optional[str],
    BeforeValidator(lambda value: None if value is None else format_emitted_at(value)),
]


def utc_now() -> datetime:
    """The current instant, UTC and microsecond-resolution.

    The only clock this module reads, and it is read for one thing: an ``emitted_at`` a caller did
    not supply. No price, quantity, balance or business timestamp comes from here - those are handed
    in, which is what keeps a Paper_Session replayable (Requirement 15.4).
    """
    return datetime.now(timezone.utc)


def new_event_id() -> str:
    """A fresh ``event_id``: a UUID4, canonically formatted.

    Unique within the session by ``uq_paper_event_id UNIQUE (session_id, event_id)`` - and this is
    the id a reconnecting client deduplicates on (Requirement 19.8), so a value that could repeat
    would make a replayed event indistinguishable from a new one.
    """
    return str(uuid.uuid4())


class PaperEventPayload(BaseModel):
    """The base every payload model extends: closed to unknown fields.

    ``extra="forbid"`` is the load-bearing setting and it is here rather than repeated sixteen
    times. A model that silently accepted an unlisted key would let a producer put a node id, an
    indicator value, a credential or a payment reference into a frame that a browser then renders -
    which is exactly what Requirements 19.7 and 26.4 forbid, and exactly the failure a permissive
    model could not be tested for.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


# ══════════════════════════════════════════════════════════════════════════
# THE SIXTEEN PAYLOADS - design.md's table, field for field
# ══════════════════════════════════════════════════════════════════════════
#
# WHY `side`, `order_type` AND `feed_state` ARE PLAIN TEXT HERE
# ------------------------------------------------------------
# ``chk_paper_order_side``, ``chk_paper_order_type`` and the feed-state vocabulary are already
# spelled once each - in ``paper_repository.ORDER_SIDES`` / ``ORDER_TYPES`` and in
# ``paper_market_feed``'s ``FEED_STATE_*`` constants. Both of those modules import THIS one, so
# importing them back would be circular; re-spelling their values as a ``Literal`` here would be
# the duplication task 26.1 exists to remove. Every one of these values reaches a frame from a row
# that already passed the CHECK that owns it, so the arbiter is left where it is.
#
# ``order_state`` and ``order_lifecycle_state`` ARE validated, because their vocabularies live in
# two pure leaves (``paper_order_state``, ``order_lifecycle_state``) that import nothing from this
# package and can therefore be imported without a cycle. One definition, imported - not copied.


class PaperSessionStartedPayload(PaperEventPayload):
    """``paper_session_started``.

    ``config_digest`` and not the configuration: the frozen ``paper_sessions.config`` carries fee
    rates, slippage and precision, and a subscriber watching somebody else's shared strategy has no
    business reading them (Requirement 19.7). A digest lets a client tell one configuration from
    another without being told what it is.

    ``initial_capital_minor`` is Minor_Units - an exact whole number - so the starting capital
    cannot arrive as a rounded float.
    """

    session_id: str = Field(min_length=1)
    strategy_ref: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    initial_capital_minor: MinorUnits
    currency: str = Field(min_length=1)
    market_data_source: str = Field(min_length=1)
    feed_transport: Optional[str] = None
    config_digest: str = Field(min_length=1)
    started_at: Instant
    actor_id: str = Field(min_length=1)


class _PaperSessionTransitionPayload(PaperEventPayload):
    """The shape ``design.md`` gives ``_paused`` / ``_resumed`` / ``_stopped``.

    ``session_state`` travels even though the event type implies it, because a client that missed a
    frame must be able to read the current state off the one it did receive rather than infer it
    from a type it has to keep a table for.
    """

    session_id: str = Field(min_length=1)
    session_state: str = Field(min_length=1)
    at: Instant
    actor_id: str = Field(min_length=1)


class PaperSessionPausedPayload(_PaperSessionTransitionPayload):
    """``paper_session_paused``."""


class PaperSessionResumedPayload(_PaperSessionTransitionPayload):
    """``paper_session_resumed``."""


class PaperSessionStoppedPayload(_PaperSessionTransitionPayload):
    """``paper_session_stopped`` - the transition shape plus ``final_metrics``.

    ``final_metrics`` is ``None`` when the metrics could not be computed, and that is a different
    fact from a session that ended flat. Requirement 28.5 forbids reporting the second as the
    first.
    """

    final_metrics: Optional[Mapping[str, Any]] = None


class MarketTickPayload(PaperEventPayload):
    """``market_tick`` - one validated candle, as the feed accepted it.

    ``source_event_id`` is ``paper_market_feed``'s ``sha256(...)`` candle identity, so a client can
    recognise a repeated publication of the same candle; ``latency_ms`` is Requirement 14.10's
    delivery measurement and is ``None`` when it could not be measured, which is different from
    zero and is carried as different.
    """

    symbol: str = Field(min_length=1)
    timestamp: Instant
    open: ExactDecimal
    high: ExactDecimal
    low: ExactDecimal
    close: ExactDecimal
    volume: ExactDecimal
    source_event_id: str = Field(min_length=1)
    latency_ms: OptionalExactDecimal = None
    feed_state: str = Field(min_length=1)


class SignalGeneratedPayload(PaperEventPayload):
    """``signal_generated`` - Requirement 19.7's whole point, and its whole extent.

    These NINE fields and no tenth. There is deliberately no node id, no indicator value, no
    feature value, no ML inference detail and no risk-rule internal: Requirement 19.7 forbids
    Protected_Logic in a Paper_Channel payload and Requirement 23.3 gives the same list for a
    subscriber's read path, so a frame that carried any of them would leak a strategy's logic to
    whoever could open its paper session.

    ``environment`` is pinned to ``'PAPER'`` by the type, not by a convention: Requirement 28.4
    forbids presenting a simulated execution as a live one, and a field that COULD hold ``'LIVE'``
    is a field that eventually will.

    ``price`` is ``None`` when the signal was recorded without a validated price - Requirement 14.9
    forbids synthesising one, and Requirement 28.5 forbids substituting a zero.
    """

    signal_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: str = Field(min_length=1)
    quantity: ExactDecimal
    price: OptionalExactDecimal = None
    order_lifecycle_state: OrderLifecycleState
    generated_at: Instant
    environment: Literal["PAPER"] = "PAPER"


class _PaperOrderPayload(PaperEventPayload):
    """The one shape ``design.md`` gives all five order events.

    One shape, five types, because the five ARE one fact at five points of its life: a client that
    had to reconcile five field sets would render a partially filled order from a different code
    path than a filled one. ``order_state`` carries which point it is, validated against
    ``PaperOrderState``.

    ``rejection_reason`` is present and ``None`` on the four non-rejections rather than absent, so a
    consumer reads one key rather than testing for its existence.
    """

    order_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: str = Field(min_length=1)
    order_type: str = Field(min_length=1)
    quantity: ExactDecimal
    limit_price: OptionalExactDecimal = None
    order_state: PaperOrderState
    filled_quantity: ExactDecimal
    avg_fill_price: OptionalExactDecimal = None
    fee_minor: MinorUnits = 0
    slippage_minor: MinorUnits = 0
    rejection_reason: Optional[str] = None
    at: Instant


class PaperOrderCreatedPayload(_PaperOrderPayload):
    """``paper_order_created``."""


class PaperOrderAcceptedPayload(_PaperOrderPayload):
    """``paper_order_accepted``."""


class PaperOrderPartiallyFilledPayload(_PaperOrderPayload):
    """``paper_order_partially_filled``."""


class PaperOrderFilledPayload(_PaperOrderPayload):
    """``paper_order_filled``."""


class PaperOrderRejectedPayload(_PaperOrderPayload):
    """``paper_order_rejected``."""


class PaperPositionUpdatedPayload(PaperEventPayload):
    """``paper_position_updated``.

    Direction is ``side``, never a negative ``size`` (Requirement 18.5). ``current_price``,
    ``unrealized_pnl`` and ``price_at`` are ``None`` together when no validated price has arrived,
    and ``stale`` says so explicitly (Requirement 18.15).
    """

    symbol: str = Field(min_length=1)
    side: str = Field(min_length=1)
    size: ExactDecimal
    entry_price: ExactDecimal
    current_price: OptionalExactDecimal = None
    unrealized_pnl: OptionalExactDecimal = None
    price_at: OptionalInstant = None
    stale: bool = False


class PaperBalanceUpdatedPayload(PaperEventPayload):
    """``paper_balance_updated``."""

    available_balance: ExactDecimal
    locked_balance: ExactDecimal
    total_equity: ExactDecimal
    currency: str = Field(min_length=1)
    stale: bool = False


class PaperPnlUpdatedPayload(PaperEventPayload):
    """``paper_pnl_updated``.

    ``realized_pnl`` is always known - it is a sum of closed trades. The other three depend on a
    current price and are ``None`` without one, rather than being reported as the realized figure or
    as zero.
    """

    realized_pnl: ExactDecimal
    unrealized_pnl: OptionalExactDecimal = None
    total_pnl: OptionalExactDecimal = None
    total_return_pct: OptionalExactDecimal = None
    price_at: OptionalInstant = None
    stale: bool = False


class PaperDrawdownUpdatedPayload(PaperEventPayload):
    """``paper_drawdown_updated``.

    Both an amount and a fraction, because a fraction alone cannot be rendered as money and an
    amount alone cannot be compared between sessions. ``snapshot_count`` is how many equity
    snapshots the figures were computed over, so a client can tell a drawdown of record from a
    drawdown of two points.
    """

    max_drawdown_amount: ExactDecimal
    max_drawdown_fraction: ExactDecimal
    peak_equity: ExactDecimal
    snapshot_count: int = Field(ge=0)


class PaperErrorPayload(PaperEventPayload):
    """``paper_error`` - the one frame that reports a failure to the subscriber.

    ``message`` is a SAFE sentence. Requirement 22.9 forbids a stack trace, a database error
    string, a query, an internal path or an internal identifier in anything a client receives, and
    ``code`` is what a client branches on so the sentence never has to carry a detail.

    ``recoverable`` distinguishes "the session continues" from "reload from the
    Paper_Trading_API" - the distinction Requirement 19.9's ``HISTORY_INCOMPLETE`` turns on.
    """

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    recoverable: bool
    at: Instant


#: ``event type -> its payload model``. Exhaustive over the sixteen by construction below, and
#: asserted exhaustive in ``tests/test_paper_event_schemas.py``: a type with no model would be a
#: frame nothing validated.
PAYLOAD_MODEL_FOR_EVENT: Dict[str, Type[PaperEventPayload]] = {
    PaperEvent.SESSION_STARTED.value: PaperSessionStartedPayload,
    PaperEvent.SESSION_PAUSED.value: PaperSessionPausedPayload,
    PaperEvent.SESSION_RESUMED.value: PaperSessionResumedPayload,
    PaperEvent.SESSION_STOPPED.value: PaperSessionStoppedPayload,
    PaperEvent.MARKET_TICK.value: MarketTickPayload,
    PaperEvent.SIGNAL_GENERATED.value: SignalGeneratedPayload,
    PaperEvent.ORDER_CREATED.value: PaperOrderCreatedPayload,
    PaperEvent.ORDER_ACCEPTED.value: PaperOrderAcceptedPayload,
    PaperEvent.ORDER_PARTIALLY_FILLED.value: PaperOrderPartiallyFilledPayload,
    PaperEvent.ORDER_FILLED.value: PaperOrderFilledPayload,
    PaperEvent.ORDER_REJECTED.value: PaperOrderRejectedPayload,
    PaperEvent.POSITION_UPDATED.value: PaperPositionUpdatedPayload,
    PaperEvent.BALANCE_UPDATED.value: PaperBalanceUpdatedPayload,
    PaperEvent.PNL_UPDATED.value: PaperPnlUpdatedPayload,
    PaperEvent.DRAWDOWN_UPDATED.value: PaperDrawdownUpdatedPayload,
    PaperEvent.ERROR.value: PaperErrorPayload,
}


def payload_model_for(event_type: Any) -> Type[PaperEventPayload]:
    """The payload model for ``event_type``, which must be one of the sixteen."""
    return PAYLOAD_MODEL_FOR_EVENT[paper_event(event_type).value]


def validate_payload(event_type: Any, payload: Any) -> PaperEventPayload:
    """``payload`` as the model ``event_type`` declares.

    A model instance of the right class passes through; a mapping is validated. A mapping carrying a
    key the model does not declare is REFUSED (``extra="forbid"``), which is the whole mechanism
    Requirement 19.7 rests on.
    """
    model = payload_model_for(event_type)
    if isinstance(payload, model):
        return payload
    if isinstance(payload, PaperEventPayload):
        # A payload of a DIFFERENT type. The type and the payload are one claim, so a mismatched
        # pair is a producer bug and is refused rather than re-validated field by field.
        payload = payload.model_dump()
    return model.model_validate(payload)


def payload_jsonb(payload: Any) -> Dict[str, Any]:
    """One payload as the JSONB mapping ``paper_events.payload`` stores.

    A ``Decimal`` becomes its EXACT decimal string and a ``float`` is refused - the rule
    ``paper_repository._jsonb`` applies to this column, applied here so the value the envelope
    carries on the wire and the value the row carries in the database are the same characters. A
    JSON number cannot be read back exactly, and a replay priced from an inexact close is not a
    replay (Requirements 18.1, 15.4).
    """
    if isinstance(payload, PaperEventPayload):
        payload = payload.model_dump()
    if not isinstance(payload, Mapping):
        raise ValueError(
            f"a paper event payload must be a mapping or a payload model, got "
            f"{type(payload).__name__}"
        )
    return {str(key): _jsonb_value(value) for key, value in payload.items()}


def _jsonb_value(value: Any) -> Any:
    """One payload value, rendered for JSONB. Never a ``float``, never a lossy number."""
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        _refuse_float(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return format_emitted_at(value)
    if isinstance(value, Enum):
        return _jsonb_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _jsonb_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_jsonb_value(item) for item in value]
    raise ValueError(
        f"a paper event payload cannot carry a {type(value).__name__}; it has no JSON "
        f"representation this module will guess at"
    )


# ══════════════════════════════════════════════════════════════════════════
# THE ENVELOPE (Requirement 19.3)
# ══════════════════════════════════════════════════════════════════════════

#: The eight fields ``design.md``'s frame carries, in its order. Named so a producer, a consumer and
#: a test all read one tuple rather than three lists.
ENVELOPE_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "channel",
    "session_id",
    "type",
    "sequence",
    "event_id",
    "emitted_at",
    "payload",
)


def paper_channel(session_id: Any) -> str:
    """``"paper.{session_id}"`` - built by the family, never spelled here.

    ``ws_channels.PAPER_FAMILY`` owns the separator and the identifier shape, and it refuses an
    identifier no subscriber could have subscribed to. Imported inside the function because
    ``ws_channels`` imports this module's vocabulary at module scope.
    """
    from backend_app.backend.ws_channels import PAPER_FAMILY

    return PAPER_FAMILY.channel(session_id)


class PaperEventEnvelope(BaseModel):
    """One Paper_Channel frame. Requirement 19.3's eight fields, and no ninth.

    ``payload`` is the rendered JSONB mapping rather than a model instance, because this object is
    what both the wire frame and the ``paper_events`` row are produced from and those must be the
    same characters. :func:`build_envelope` is what validates the payload against its model before
    it gets here, so an envelope that exists has had its payload checked.

    ``sequence`` is ``>= 1`` (``chk_paper_event_sequence``), ``emitted_at`` is the
    microsecond-resolution UTC rendering of :func:`format_emitted_at`, and ``extra="forbid"`` keeps
    a ninth field - an ``access_token``, a ``user_id``, a ``tenant_id`` - out of a frame.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default=PAPER_EVENT_SCHEMA_VERSION, min_length=1)
    channel: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    type: PaperEvent
    sequence: int = Field(ge=1)
    event_id: str = Field(min_length=1)
    emitted_at: Instant
    payload: Dict[str, Any]

    def frame(self) -> Dict[str, Any]:
        """The frame a subscriber receives, in :data:`ENVELOPE_FIELDS` order."""
        return {
            "schema_version": self.schema_version,
            "channel": self.channel,
            "session_id": self.session_id,
            "type": self.type.value,
            "sequence": self.sequence,
            "event_id": self.event_id,
            "emitted_at": self.emitted_at,
            "payload": dict(self.payload),
        }


def build_envelope(
    *,
    session_id: Any,
    event_type: Any,
    payload: Any,
    sequence: Any,
    emitted_at: Any = None,
    event_id: Optional[str] = None,
    schema_version: str = PAPER_EVENT_SCHEMA_VERSION,
) -> PaperEventEnvelope:
    """One validated envelope for ``payload``.

    ``event_type`` must be one of the sixteen and ``payload`` must validate against THAT type's
    model - the type and the payload are one claim, so a mismatched pair is refused rather than
    forwarded.

    ``emitted_at`` defaults to :func:`utc_now`; ``event_id`` to :func:`new_event_id`. ``sequence``
    is NOT defaulted: it is allocated by :func:`next_sequence` against the database, and a default
    here would be the in-process counter task 26.2 rules out.
    """
    member = paper_event(event_type)
    validated = validate_payload(member, payload)
    return PaperEventEnvelope(
        schema_version=schema_version,
        channel=paper_channel(session_id),
        session_id=str(session_id),
        type=member,
        sequence=sequence,
        event_id=event_id or new_event_id(),
        emitted_at=emitted_at if emitted_at is not None else utc_now(),
        payload=payload_jsonb(validated),
    )


# ══════════════════════════════════════════════════════════════════════════
# THE SEQUENCE ALLOCATOR (Requirement 19.3, task 26.2)
# ══════════════════════════════════════════════════════════════════════════
#
# WHAT `next_sequence` IS, WHAT IT IS NOT, AND WHAT GAP IS LEFT
# ------------------------------------------------------------
# ``design.md`` and task 26.2 write the allocation as
#
#     UPDATE paper_sessions SET event_sequence = event_sequence + 1 WHERE id = :id
#     RETURNING event_sequence
#
# issued inside the emitting transaction, and say the ROW LOCK is what makes the sequence
# contiguous across instances. **This deployment has none of the three pieces that needs.**
# The Persistence_Layer is reached through PostgREST, which speaks one HTTP statement per request:
#
# * There is no ``BEGIN`` / ``COMMIT`` / ``ROLLBACK``, so there is no "emitting transaction" for the
#   allocation to be issued inside. The allocation and the ``paper_events`` INSERT are two requests.
# * There is no ``SELECT ... FOR UPDATE`` and no row lock that survives from a read to a write.
# * There is no ``RETURNING`` clause, and no server-side expression either: a PostgREST ``PATCH``
#   sets a column to a LITERAL, so ``event_sequence = event_sequence + 1`` cannot be expressed at
#   all. (``Prefer: return=representation`` does return the updated row, so the *reading back* half
#   of ``RETURNING`` is available - the arithmetic half is not.)
#
# ``paper_repository.lock_account_for_update`` and ``paper_simulator``'s "WHAT ONE TRANSACTION MEANS
# OVER THIS TRANSPORT" section record this already. So this function implements the strongest thing
# the transport supports, which is the same optimistic protocol the money path uses:
#
#   1. read ``paper_sessions.event_sequence`` -> N (``user_id`` and ``id`` as predicates)
#   2. ``UPDATE ... SET event_sequence = N + 1 WHERE id = :id AND user_id = :uid
#      AND event_sequence = N`` - the read value is a PREDICATE, so a writer that moved the row in
#      between makes the statement match zero rows
#   3. zero rows -> re-read and try again, up to :data:`SEQUENCE_ALLOCATION_ATTEMPTS`; a loser never
#      reuses the number it read
#
# **The residual gap, stated plainly.** The allocation is atomic and the counter is contiguous, but
# the allocation and the INSERT that consumes it are still two requests with no transaction around
# them: a process that dies between them burns a number, so ``paper_events.sequence`` can contain a
# HOLE that Requirement 19.3's "increases by exactly 1" does not allow. What cannot happen is the
# other failure - two events at the same sequence - because ``uq_paper_event_seq UNIQUE (session_id,
# sequence)`` refuses the second INSERT outright and
# ``paper_repository.insert_session_event`` raises ``PaperDuplicateSessionEvent``, which the caller
# can retry with a fresh allocation. That refusal, not a lock, is the mechanism that actually
# delivers contiguity here, and it is the database's rather than this module's.
#
# An in-process counter is NOT used, and would not be an acceptable substitute even ignoring the
# above: it would restart at 1 after a restart and diverge between instances, and neither failure
# is detectable from a frame.
#
# Closing the gap completely means moving the allocation and the INSERT into one database function
# (``rpc``), so PostgreSQL holds the transaction and ``RETURNING`` becomes available. That is a
# schema and deployment change outside tasks 26.1-26.3 and it is recorded here rather than glossed.

#: How many times the compare-and-swap is attempted before the contention is reported. Three, the
#: figure ``paper_simulator.RETRY_ATTEMPTS`` uses for the same kind of loss.
#:
#: There is no sleep between attempts and no jitter: a randomised delay would be a ``random`` draw
#: inside ``backend_app/backend/paper/``, which ``tests/test_paper_no_random.py`` forbids outright,
#: and the contended resource here is a single integer rather than a balance computation - a
#: re-read costs one request.
SEQUENCE_ALLOCATION_ATTEMPTS = 3


def next_sequence(supabase: Any, user_id: Any, session_id: Any) -> int:
    """The sequence this session's next event takes: 1 for the first, +1 for each after it.

    Delegates to ``paper_repository.allocate_session_event_sequence``, because
    ``paper_repository`` is the one module in this package that issues statements - so ``user_id``
    is a predicate on both halves of the swap and the projection stays the one the schema-contract
    test checks (Requirements 21.2, 21.5, 24.9).

    See the section comment above for what this does over PostgREST and what gap remains. In one
    line: an atomic compare-and-swap on ``paper_sessions.event_sequence``, never an in-process
    counter, with ``uq_paper_event_seq`` as the database's own refusal of a collision.

    Raises:
        PaperConcurrencyConflict: the swap lost :data:`SEQUENCE_ALLOCATION_ATTEMPTS` times. No
            number is returned and nothing is written, so the caller emits nothing rather than
            emitting at a sequence it does not own.
        PaperPersistenceError: the session is not readable for this identity - which is the same
            answer for "no such session" and "another user's session", by design.
    """
    from backend_app.backend.paper import paper_repository as repository

    return repository.allocate_session_event_sequence(
        supabase,
        user_id,
        session_id,
        attempts=SEQUENCE_ALLOCATION_ATTEMPTS,
    )


def record_event(
    supabase: Any,
    *,
    user_id: Any,
    session_id: Any,
    event_type: Any,
    payload: Any,
    emitted_at: Any = None,
    event_id: Optional[str] = None,
) -> PaperEventEnvelope:
    """Allocate a sequence, build the envelope, append the row. Returns the envelope.

    The order matters and is not interchangeable: the sequence is allocated FIRST, so the number a
    frame carries is one the database issued and not one this process chose. The append is the last
    statement, so a payload that fails validation writes nothing and consumes no number.

    ``paper_events`` is append-only (``trg_paper_events_append_only``) and this function issues no
    statement of its own - both the allocation and the append go through ``paper_repository``.

    Raises:
        ValidationError: ``payload`` does not match ``event_type``'s model, or carries an unlisted
            field. Nothing is written and the allocated number is burned rather than reused.
        PaperDuplicateSessionEvent: ``uq_paper_event_seq`` or ``uq_paper_event_id`` already holds
            this record. The caller retries with a fresh allocation; the log line is not doubled.
        PaperConcurrencyConflict / PaperPersistenceError: as :func:`next_sequence`.
    """
    from backend_app.backend.paper import paper_repository as repository

    member = paper_event(event_type)
    validated = validate_payload(member, payload)
    rendered = payload_jsonb(validated)

    sequence = next_sequence(supabase, user_id, session_id)
    envelope = PaperEventEnvelope(
        schema_version=PAPER_EVENT_SCHEMA_VERSION,
        channel=paper_channel(session_id),
        session_id=str(session_id),
        type=member,
        sequence=sequence,
        event_id=event_id or new_event_id(),
        emitted_at=emitted_at if emitted_at is not None else utc_now(),
        payload=rendered,
    )

    repository.insert_session_event(
        supabase,
        session_id=envelope.session_id,
        user_id=user_id,
        sequence=envelope.sequence,
        event_id=envelope.event_id,
        event_type=envelope.type.value,
        schema_version=envelope.schema_version,
        payload=envelope.payload,
        emitted_at=envelope.emitted_at,
    )
    return envelope


# ══════════════════════════════════════════════════════════════════════════
# THE NAMES ``design.md`` GIVES THE DELIVERY PATH (tasks 26.4, 26.5)
# ══════════════════════════════════════════════════════════════════════════


async def broadcast(session_id: Any, frame: Mapping[str, Any], **kwargs: Any) -> Any:
    """``paper_channel.broadcast``, under the name ``design.md`` uses for it.

    Requirement 21.7's pre-emit ownership re-verification, the pending-queue policy of Requirement
    19.11 and the per-handler isolation of Requirement 19.13 are all in ``paper_channel``; this is
    the alias, not a second implementation. ``supabase`` is required by that function and is passed
    through, because the re-derivation of the owner is a read against the Persistence_Layer and a
    delivery path that could not reach the database could not perform it.
    """
    from backend_app.backend.paper.paper_channel import broadcast as _broadcast

    return await _broadcast(session_id, frame, **kwargs)


def replay(supabase: Any, **kwargs: Any) -> Any:
    """``paper_channel.replay``, for symmetry with :func:`broadcast`. Requirements 19.8, 19.9."""
    from backend_app.backend.paper.paper_channel import replay as _replay

    return _replay(supabase, **kwargs)


__all__: List[str] = [
    "ENVELOPE_FIELDS",
    "ExactDecimal",
    "Instant",
    "MarketTickPayload",
    "MinorUnits",
    "OptionalExactDecimal",
    "OptionalInstant",
    "OptionalMinorUnits",
    "PAPER_CHANNEL_EVENTS",
    "PAPER_ERROR_CODES",
    "PAPER_EVENT_SCHEMA_VERSION",
    "PAPER_EVENT_TYPES",
    "PAYLOAD_MODEL_FOR_EVENT",
    "PaperBalanceUpdatedPayload",
    "PaperDrawdownUpdatedPayload",
    "PaperErrorPayload",
    "PaperEvent",
    "PaperEventEnvelope",
    "PaperEventPayload",
    "PaperOrderAcceptedPayload",
    "PaperOrderCreatedPayload",
    "PaperOrderFilledPayload",
    "PaperOrderPartiallyFilledPayload",
    "PaperOrderRejectedPayload",
    "PaperPnlUpdatedPayload",
    "PaperPositionUpdatedPayload",
    "PaperSessionPausedPayload",
    "PaperSessionResumedPayload",
    "PaperSessionStartedPayload",
    "PaperSessionStoppedPayload",
    "SEQUENCE_ALLOCATION_ATTEMPTS",
    "SignalGeneratedPayload",
    "broadcast",
    "build_envelope",
    "format_emitted_at",
    "new_event_id",
    "next_sequence",
    "paper_channel",
    "paper_event",
    "payload_jsonb",
    "payload_model_for",
    "record_event",
    "replay",
    "utc_now",
    "validate_payload",
]
