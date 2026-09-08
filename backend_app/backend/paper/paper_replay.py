"""
backend/paper/paper_replay.py - deterministic replay and the P-31 reference ledger.

Spec: marketplace-subscriptions-paper-trading tasks 9.2 (:class:`ReferenceLedger`) and 27.5
(:func:`replay`). ``design.md`` -> "``paper_replay.py`` # deterministic replay + the P-31
reference ledger" and "Deterministic replay (Requirement 15.4, 15.5, P-31)". Requirements
15.4, 15.5, 18.13, and the accounting rules of Requirement 18 that this file re-derives
(18.2 - 18.10, 18.12, 18.15).

This module contains two things. :class:`ReferenceLedger` - the naive second implementation
of Requirement 18's accounting rules, which P-31 compares the simulator against - reads no
clock, no database, no socket and no ``random``, so it stays constructible from a property
test with no fixture at all. :func:`replay` - task 27.5, the audit path of Requirements 15.4
and 15.5 - drives that same ledger over a session's recorded
``(paper_sessions.config, paper_market_events, order intents in paper_orders)`` and therefore
does issue reads, all of them through ``paper_repository`` and all of them carrying
``user_id`` as a predicate. It still reads no clock and no ``random``: every timestamp it
records comes from a recorded event, which is the whole of why the reconstruction is
byte-identical. See "DETERMINISTIC REPLAY" at the bottom of this file.

WHAT A "REFERENCE" LEDGER IS FOR
--------------------------------
:class:`ReferenceLedger` is a **second, independent** implementation of the accounting
rules that ``paper/paper_accounting.py`` implements. It is written from ``requirements.md``
Requirement 18 and the ``design.md`` accounting table, not from that module: it imports
nothing from it, delegates nothing to it, and shares no arithmetic with it. That is the
whole point. A model oracle that called into the implementation it checks would agree with
it by construction and would detect nothing (Requirement 29's model-based property P-31 asks
for agreement between two implementations, which presupposes two).

The two differ deliberately in *style*:

============================  ==============================  ==========================
Concern                       ``paper_accounting``            ``ReferenceLedger``
============================  ==============================  ==========================
State                         frozen dataclasses, returned    one mutable Python dict
Realized PnL, entry price     branch per case, quantized      branch per case, written
                              through helper functions         inline from Requirement 18
Idempotency                   the caller's ``uq_paper_fill_    a ``set`` of seen
                              event`` lookup                   ``fill_event_id`` values
Concurrency, persistence      the caller's transaction,        none of it
                              ``FOR UPDATE``, repository
Partial fills                 the caller caches order state    recomputed every call
============================  ==============================  ==========================

WHY CASH ON A FILL IS THE CHANGE IN POSITION VALUE
--------------------------------------------------
Requirement 18.6 is the normative sentence: a zero-fee, zero-slippage fill "SHALL change
``total_equity`` by exactly zero, evaluated with each affected position valued at that
fill's price". ``design.md``'s prose reading - "cash moves by ``-quantity*price``" - is the
*same* rule only before Requirement 18.2's quantization is applied: for a fill that adds to
an open position, ``quantize(qty * price)`` and
``quantize(new_size * price) - quantize(old_size * price)`` can differ by one minor unit, and
charging the first would break the criterion by that minor unit on ordinary fill sequences.

So this ledger values the position **before** and **after** the fill, both at that fill's
price, and moves cash by the difference less the fee::

    cash_delta = value(old_position, fill_price) - value(new_position, fill_price) - fee

which is one line covering open, add, partial close, full close and reversal alike, and makes
Requirements 18.6 and 18.7 hold by arithmetic rather than by five audited branches. It lands
on the same rule ``paper_accounting`` states in its own docstring - as an oracle must, since
both are reading Requirement 18 - but it is derived and written here, not called there.
Where the two can still disagree is everything the *cases* decide: which side a fill opens,
what the weighted-average entry price becomes, how much quantity a reversal closes, what
realized PnL a close records, when an order reaches ``FILLED``. Those are the divergences
P-31 exists to find, and a disagreement there is a question about the code under test, not a
licence to edit this file until it agrees.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* The fill model. Slippage direction, the limit-price trigger and the participation cap are
  ``paper_simulator``'s (and, for the audit path, ``replay``'s). This ledger is handed a
  quantity, a price and a fee that some caller already decided on.
* Any write, any transaction, any lock, any retry. "No locking, no persistence."
* ``float`` anywhere: Requirement 18.1. A ``float`` argument is refused, not coerced.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import (
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
    InvalidOperation,
    localcontext,
)
from itertools import accumulate
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from backend_app.backend.paper import paper_market_feed as feed
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_simulator as sim

logger = logging.getLogger("PaperReplay")

# ══════════════════════════════════════════════════════════════════════════
# CONSTANTS - the vocabulary of Requirement 18 and Requirement 16
# ══════════════════════════════════════════════════════════════════════════

#: Working precision, matching ``design.md``'s explicit ``localcontext(prec=34)``. Held here
#: so that a difference between this ledger and the engine it models is a difference of
#: *rule* rather than an artefact of one of them running at the ambient context precision.
PRECISION = 34

LONG = "LONG"
SHORT = "SHORT"
SIDES = frozenset({LONG, SHORT})

BUY = "buy"
SELL = "sell"

#: The one cost-basis convention Requirement 18.8 admits per session.
WEIGHTED_AVERAGE = "WEIGHTED_AVERAGE"

#: The rounding modes ``config.rounding_mode`` may name (Requirement 18.2).
ROUNDING_MODES = frozenset(
    {
        ROUND_HALF_EVEN,
        ROUND_HALF_UP,
        ROUND_HALF_DOWN,
        ROUND_UP,
        ROUND_DOWN,
        ROUND_CEILING,
        ROUND_FLOOR,
    }
)
DEFAULT_ROUNDING_MODE = ROUND_HALF_EVEN

#: Market shapes where a margin figure is a measurement rather than an invention
#: (Requirement 18.12). Everything else reports no figure at all - not a zero.
MARGIN_MARKET_TYPES = frozenset({"swap", "future"})

#: The six Paper_Order_State values (Requirement 16.1) and the transitions between them
#: (Requirement 16.2), spelled out again rather than imported: this ledger is the model P-31
#: compares the simulator against, including on order state, and a model that imported the
#: production transition table could not disagree with it. ``PaperOrderState`` is a ``str``
#: enum, so these plain strings compare equal to it without a conversion.
CREATED = "CREATED"
ACCEPTED = "ACCEPTED"
PARTIALLY_FILLED = "PARTIALLY_FILLED"
FILLED = "FILLED"
CANCELLED = "CANCELLED"
REJECTED = "REJECTED"

ORDER_TRANSITIONS: Mapping[str, Tuple[str, ...]] = {
    CREATED: (ACCEPTED, REJECTED),
    ACCEPTED: (PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED),
    PARTIALLY_FILLED: (PARTIALLY_FILLED, FILLED, CANCELLED),
    FILLED: (),
    CANCELLED: (),
    REJECTED: (),
}
TERMINAL_ORDER_STATES = frozenset({FILLED, CANCELLED, REJECTED})

#: The ``paper_equity_snapshots.cause`` values Requirement 18.11 enumerates.
SESSION_START = "SESSION_START"
FILL = "FILL"
FEE = "FEE"
REVALUATION = "REVALUATION"
SESSION_STOP = "SESSION_STOP"

#: The scale ``win_rate NUMERIC(6,5)`` and the drawdown fraction are reported at.
FRACTION_QUANTUM = Decimal("0.00001")

ZERO = Decimal("0")
ONE = Decimal("1")
TWO = Decimal("2")
HUNDRED = Decimal("100")


# ══════════════════════════════════════════════════════════════════════════
# EXCEPTIONS - named apart from ``paper_accounting``'s on purpose
# ══════════════════════════════════════════════════════════════════════════


class ReferenceLedgerError(Exception):
    """Base class for every refusal this ledger makes."""


class ReferenceStalePrice(ReferenceLedgerError):
    """No price is available for an open position's symbol (Requirement 18.15).

    Raised rather than substituting a synthesised, interpolated or zero price. Carries
    ``symbol``.
    """

    def __init__(self, symbol: Any) -> None:
        self.symbol = symbol
        super().__init__(f"no validated price for open position {symbol!r}")


class ReferenceInvariantViolation(ReferenceLedgerError):
    """A Requirement 18.3 / 18.4 / 18.5 breach, naming the invariant in ``invariant``."""

    def __init__(self, invariant: str, message: str) -> None:
        self.invariant = invariant
        super().__init__(message)


class ReferenceOverFill(ReferenceLedgerError):
    """A fill would take an order's filled quantity above its ordered quantity (Req 16.7)."""


class ReferenceBadValue(ReferenceLedgerError):
    """An argument is not an admissible exact decimal value, or is a ``float`` (Req 18.1)."""


class ReferenceBadTransition(ReferenceLedgerError):
    """An order state transition is not one of the nine Requirement 16.2 permits."""


# ══════════════════════════════════════════════════════════════════════════
# EXACT DECIMALS (Requirement 18.1)
# ══════════════════════════════════════════════════════════════════════════


def to_decimal(value: Any, what: str = "value") -> Decimal:
    """Return ``value`` as an exact finite ``Decimal``, refusing ``float`` and ``bool``.

    ``Decimal``, ``int`` and ``str`` convert exactly; ``0.07`` as a binary ``float`` does
    not, and Requirement 18.1 forbids the arithmetic that would follow from accepting it.
    ``bool`` is refused because ``isinstance(True, int)`` would otherwise make ``True`` a
    quantity of one.
    """
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, bool) or value is None or isinstance(value, float):
        raise ReferenceBadValue(
            f"{what} must be an exact decimal value, got {type(value).__name__}: {value!r}"
        )
    elif isinstance(value, (int, str)):
        try:
            result = Decimal(value.strip() if isinstance(value, str) else value)
        except InvalidOperation as exc:
            raise ReferenceBadValue(f"{what} is not a decimal number: {value!r}") from exc
    else:
        raise ReferenceBadValue(
            f"{what} must be an exact decimal value, got {type(value).__name__}: {value!r}"
        )
    if not result.is_finite():
        raise ReferenceBadValue(f"{what} is not finite: {value!r}")
    return result


def _quantum(places: Any, what: str) -> Decimal:
    """``2`` -> ``Decimal('0.01')``. A quantum ``Decimal`` passes straight through."""
    if isinstance(places, Decimal):
        if not places.is_finite() or places <= ZERO:
            raise ReferenceBadValue(f"{what} quantum must be positive and finite: {places}")
        return places
    if isinstance(places, bool) or not isinstance(places, int) or places < 0:
        raise ReferenceBadValue(f"{what} precision must be a non-negative int: {places!r}")
    return ONE.scaleb(-places)


# ══════════════════════════════════════════════════════════════════════════
# THE VALUE OBJECTS - mutable, and named as ``paper_accounting``'s fields are
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class RefPosition:
    """One open or closed position. Field names match ``paper_accounting.Position``.

    Mutable, unlike its frozen counterpart: this ledger keeps one dict and edits it in
    place, which is the naive shape task 9.2 asks for. A fully closed position keeps its
    entry in the dict with ``size == Decimal('0')`` and ``closed_at`` set (Requirement 18.5).
    """

    symbol: str
    side: str
    size: Decimal
    entry_price: Decimal
    current_price: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None
    price_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    @property
    def is_open(self) -> bool:
        """``size > 0``, compared exactly - no ``0.00000001`` tolerance anywhere."""
        return self.size > ZERO

    def copy(self) -> "RefPosition":
        """A shallow copy, so a refused fill can leave the stored position untouched."""
        return RefPosition(
            symbol=self.symbol,
            side=self.side,
            size=self.size,
            entry_price=self.entry_price,
            current_price=self.current_price,
            unrealized_pnl=self.unrealized_pnl,
            price_at=self.price_at,
            opened_at=self.opened_at,
            closed_at=self.closed_at,
        )

    def as_dict(self) -> Dict[str, Any]:
        """The comparable projection P-31 diffs against the simulator's position row."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "size": self.size,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "unrealized_pnl": self.unrealized_pnl,
            "price_at": self.price_at,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
        }


@dataclass
class RefClosedTrade:
    """A round trip whose position reached exactly zero (Requirement 18.10).

    ``realized_pnl`` excludes ``fee``: Requirement 18.8 computes realized PnL from closed
    quantity at recorded fill prices only, and the fee sits beside it the way
    ``paper_trades.fee_minor`` does.
    """

    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    realized_pnl: Decimal
    fee: Decimal = ZERO
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    @property
    def is_win(self) -> bool:
        """Requirement 18.10's "realized profit strictly greater than zero"."""
        return self.realized_pnl > ZERO

    def as_dict(self) -> Dict[str, Any]:
        """The comparable projection of one ``paper_trades`` row."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "realized_pnl": self.realized_pnl,
            "fee": self.fee,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
        }


@dataclass
class RefOrder:
    """One order as the ledger tracks it, for the order-state half of P-31.

    Only the bookkeeping the accounting rules touch: how much is ordered, how much has
    filled, and which of the six Requirement 16.1 states that puts the order in. The
    decision of *when* an order fills is the simulator's and the replay path's.
    """

    order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Optional[Decimal] = None
    filled_quantity: Decimal = ZERO
    order_state: str = CREATED
    rejection_reason: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: Optional[datetime] = None

    def as_dict(self) -> Dict[str, Any]:
        """The comparable projection of one ``paper_orders`` row."""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "quantity": self.quantity,
            "limit_price": self.limit_price,
            "filled_quantity": self.filled_quantity,
            "order_state": self.order_state,
            "rejection_reason": self.rejection_reason,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
        }


@dataclass
class RefFill:
    """What :meth:`ReferenceLedger.apply_fill` reports about one fill.

    ``applied`` is ``False`` and ``duplicate`` is ``True`` for a repeated
    ``fill_event_id``: Requirement 18.13 wants no balance change and no additional equity
    snapshot for a re-delivered event.

    ``total_equity_delta`` is **measured**, not assumed: it is the equity computed after the
    fill minus the equity computed before it, both with this fill's price in hand. The cash
    rule is meant to make it exactly ``-fee``, and reporting the measurement rather than the
    intention is what lets P-27 and P-28 check that claim instead of restating it.
    """

    applied: bool
    duplicate: bool
    symbol: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    realized_pnl: Decimal
    available_delta: Decimal
    locked_delta: Decimal
    total_equity_delta: Decimal
    position: Optional[Dict[str, Any]] = None
    closed_trade: Optional[Dict[str, Any]] = None
    order_state: Optional[str] = None
    fill_event_id: Optional[str] = None
    filled_at: Optional[datetime] = None


@dataclass
class RefDrawdown:
    """Maximum drawdown, as Requirement 18.9 asks for it: an amount and a fraction."""

    amount: Decimal
    fraction: Decimal


@dataclass
class ReferenceLedger:
    """A deliberately naive ledger implementing the Requirement 18 accounting rules.

    One dict of positions, one list of closed trades, one list of equity snapshots, one set
    of seen fill-event ids, and arithmetic written the shortest way the requirements admit.
    No locking, no persistence, no partial-fill caching, no clock, no ``random``.

    ``total_equity`` is not a field. It is :meth:`total_equity`, the computed sum
    ``available_balance + locked_balance + position_market_value``, so Requirement 18.3
    cannot drift here either - though for a different reason than in ``paper_accounting``,
    which keeps the field and recomputes it on every return.

    Typical use as the P-31 model::

        ledger = ReferenceLedger(config, initial_balance=Decimal('10000'))
        order = ledger.submit(intent)                    # CREATED
        ledger.accept(order.order_id)                    # ACCEPTED
        ledger.fill_order(order.order_id, quantity=q, price=p,
                          fee=ledger.fee_from_minor(fee_minor),
                          fill_event_id=event_id, filled_at=ts)
        assert ledger.state() == projection_of(simulator_state)

    Args:
        config: A ``paper_sessions.config`` mapping. The keys read are ``fee_rate``,
            ``slippage_rate``, ``rounding_mode``, ``cost_basis``, ``price_precision``,
            ``quantity_precision``, ``minor_unit_exponent`` and ``market_type``; absent
            keys take the ``design.md`` default, because a naive oracle that refused an
            incomplete config would be harder to drive from a property test than one that
            documents its defaults. Rates may be decimal strings, as the JSONB stores them.
        initial_balance: The session's initial simulated capital, all of it available.
        currency: The Paper_Account currency, for the Minor_Units scale.
        started_at: The timestamp of the ``SESSION_START`` equity snapshot
            (Requirement 18.11). Passed in, never read from a clock (Requirement 15.4).
    """

    config: Mapping[str, Any] = field(default_factory=dict)
    initial_balance: Any = ZERO
    currency: str = "USD"
    started_at: Optional[datetime] = None

    # -- state -------------------------------------------------------------
    available_balance: Decimal = field(init=False, default=ZERO)
    locked_balance: Decimal = field(init=False, default=ZERO)
    realized_pnl: Decimal = field(init=False, default=ZERO)
    positions: Dict[str, RefPosition] = field(init=False, default_factory=dict)
    closed_trades: List[RefClosedTrade] = field(init=False, default_factory=list)
    orders: Dict[str, RefOrder] = field(init=False, default_factory=dict)
    fills: List[RefFill] = field(init=False, default_factory=list)
    equity_series: List[Dict[str, Any]] = field(init=False, default_factory=list)
    seen_fill_events: Set[str] = field(init=False, default_factory=set)
    stale: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        config = dict(self.config or {})

        self.fee_rate = to_decimal(config.get("fee_rate", "0"), "config.fee_rate")
        self.slippage_rate = to_decimal(
            config.get("slippage_rate", "0"), "config.slippage_rate"
        )
        self.cost_basis = str(config.get("cost_basis", WEIGHTED_AVERAGE))
        if self.cost_basis != WEIGHTED_AVERAGE:
            raise ReferenceBadValue(
                f"unsupported cost_basis {self.cost_basis!r}; Requirement 18.8 requires the "
                "recorded convention to be the applied one, and only WEIGHTED_AVERAGE is "
                "implemented"
            )
        self.rounding_mode = str(config.get("rounding_mode", DEFAULT_ROUNDING_MODE))
        if self.rounding_mode not in ROUNDING_MODES:
            raise ReferenceBadValue(f"unsupported rounding mode {self.rounding_mode!r}")

        self.money_quantum = _quantum(config.get("minor_unit_exponent", 2), "money")
        self.qty_quantum = _quantum(config.get("quantity_precision", 8), "quantity")
        self.price_quantum = _quantum(config.get("price_precision", 2), "price")
        self.minor_unit_exponent = int(config.get("minor_unit_exponent", 2))
        market_type = config.get("market_type")
        self.market_type = (
            None if market_type is None else str(market_type).strip().lower()
        )

        self.available_balance = self.money(self.initial_balance)
        if self.available_balance < ZERO:
            raise ReferenceInvariantViolation(
                "available_balance",
                f"initial balance must be at or above zero, got {self.available_balance}",
            )
        self.locked_balance = self.money(ZERO)
        self.realized_pnl = self.money(ZERO)
        self._order_sequence = 0
        self._orders_by_key: Dict[str, str] = {}

        self._snapshot(SESSION_START, self.started_at)

    # ══════════════════════════════════════════════════════════════════
    # QUANTIZATION (Requirement 18.2) - one rounding mode, from the config
    # ══════════════════════════════════════════════════════════════════

    def money(self, value: Any) -> Decimal:
        """Quantize a monetary value to the account currency's Minor_Units scale."""
        with localcontext() as ctx:
            ctx.prec = PRECISION
            return to_decimal(value, "monetary value").quantize(
                self.money_quantum, rounding=self.rounding_mode
            )

    def qty(self, value: Any) -> Decimal:
        """Quantize a quantity to the symbol's quantity precision."""
        with localcontext() as ctx:
            ctx.prec = PRECISION
            return to_decimal(value, "quantity").quantize(
                self.qty_quantum, rounding=self.rounding_mode
            )

    def price(self, value: Any) -> Decimal:
        """Quantize a price to the symbol's price precision."""
        with localcontext() as ctx:
            ctx.prec = PRECISION
            return to_decimal(value, "price").quantize(
                self.price_quantum, rounding=self.rounding_mode
            )

    def fee_from_minor(self, fee_minor: Any) -> Decimal:
        """Convert an integer Minor_Units fee into a ``Decimal`` at the money scale.

        ``paper_fills.fee_minor`` and ``paper_generators.fill_sequences()`` both carry the
        fee as an integer count of minor units; every arithmetic path here takes a
        ``Decimal``. ``Decimal(fee_minor).scaleb(-minor_unit_exponent)`` is the conversion,
        and it is exact - a shift of the exponent, no division.
        """
        if isinstance(fee_minor, bool) or not isinstance(fee_minor, int):
            raise ReferenceBadValue(
                f"fee_minor must be an int count of minor units, got {fee_minor!r}"
            )
        with localcontext() as ctx:
            ctx.prec = PRECISION
            return self.money(Decimal(fee_minor).scaleb(-self.minor_unit_exponent))

    # ══════════════════════════════════════════════════════════════════
    # VALUATION (Requirements 18.3, 18.8, 18.15)
    # ══════════════════════════════════════════════════════════════════

    def _price_for(self, position: RefPosition, prices: Optional[Mapping[str, Any]]) -> Decimal:
        """The price to value ``position`` at: the given one, else the last recorded one.

        Falling back to ``position.current_price`` is Requirement 18.15's "report against
        the last validated price" - :meth:`revalue` is what records that the fallback
        happened, by setting :attr:`stale`. With neither a given nor a recorded price there
        is nothing honest to report, so :class:`ReferenceStalePrice` is raised - never a
        zero, an interpolation or a synthesised value.
        """
        if prices is not None and isinstance(prices, Mapping):
            given = prices.get(position.symbol)
            if given is not None:
                return self.price(given)
        if position.current_price is not None:
            return position.current_price
        raise ReferenceStalePrice(position.symbol)

    def position_value(self, position: RefPosition, price: Any) -> Decimal:
        """``size * price`` for a ``LONG``, ``size * (2*entry_price - price)`` for a ``SHORT``.

        The retained convention (``design.md``; ``paper_trading_service._recalculate_account``
        line 151): the cash the position consumed plus the profit it has made, which is what
        keeps the equity identity holding for a short without a negative position value.
        Quantized to the money scale, because it is a reported monetary value
        (Requirement 18.2).
        """
        with localcontext() as ctx:
            ctx.prec = PRECISION
            current = to_decimal(price, "price")
            if position.side == LONG:
                return self.money(position.size * current)
            if position.side == SHORT:
                return self.money(position.size * (TWO * position.entry_price - current))
            raise ReferenceInvariantViolation(
                "position_side",
                f"position side must be LONG or SHORT, got {position.side!r} "
                "(Requirement 18.5)",
            )

    def position_unrealized_pnl(self, position: RefPosition, price: Any) -> Decimal:
        """``(price - entry) * size`` for a ``LONG``, ``(entry - price) * size`` for a ``SHORT``.

        Open quantity at the latest validated price, and nothing else (Requirement 18.8).
        """
        with localcontext() as ctx:
            ctx.prec = PRECISION
            current = to_decimal(price, "price")
            if position.side == LONG:
                return self.money((current - position.entry_price) * position.size)
            return self.money((position.entry_price - current) * position.size)

    def position_market_value(self, prices: Optional[Mapping[str, Any]] = None) -> Decimal:
        """The sum over open positions of their value (Requirement 18.3).

        Closed positions contribute nothing because their size is exactly zero, not because
        they were removed - they are never removed.
        """
        with localcontext() as ctx:
            ctx.prec = PRECISION
            total = ZERO
            for position in self.positions.values():
                if position.is_open:
                    total += self.position_value(position, self._price_for(position, prices))
            return total

    def unrealized_pnl(self, prices: Optional[Mapping[str, Any]] = None) -> Decimal:
        """Unrealized PnL across open positions only (Requirement 18.8)."""
        with localcontext() as ctx:
            ctx.prec = PRECISION
            total = ZERO
            for position in self.positions.values():
                if position.is_open:
                    total += self.position_unrealized_pnl(
                        position, self._price_for(position, prices)
                    )
            return total

    def total_equity(self, prices: Optional[Mapping[str, Any]] = None) -> Decimal:
        """``available_balance + locked_balance + position_market_value`` (Requirement 18.3).

        Computed on every call. There is no ``total_equity`` field to fall out of step with
        the sum.
        """
        with localcontext() as ctx:
            ctx.prec = PRECISION
            return (
                self.available_balance
                + self.locked_balance
                + self.position_market_value(prices)
            )

    def latest_price_at(self) -> Optional[datetime]:
        """The newest ``price_at`` recorded on any position, or ``None`` (Req 18.15)."""
        stamps = [p.price_at for p in self.positions.values() if p.price_at is not None]
        return max(stamps) if stamps else None

    def last_validated_prices(self) -> Dict[str, Decimal]:
        """``{symbol: current_price}`` for every position that has been priced."""
        return {
            symbol: position.current_price
            for symbol, position in self.positions.items()
            if position.current_price is not None
        }

    # ══════════════════════════════════════════════════════════════════
    # INVARIANTS (Requirements 18.3, 18.4, 18.5, 18.14)
    # ══════════════════════════════════════════════════════════════════

    def violated_invariant(
        self, prices: Optional[Mapping[str, Any]] = None
    ) -> Optional[str]:
        """The name of the first breached invariant, or ``None``.

        The identity of Requirement 18.3 is checked against the *reported* equity, which is
        the computed sum, so it can only fail here if a position's side is unreadable - the
        two balance checks and the two position checks are the ones that can actually
        report a breach.
        """
        for position in self.positions.values():
            if position.side not in SIDES:
                return "position_side"
            if position.size < ZERO:
                return "position_size"
        if self.available_balance < ZERO:
            return "available_balance"
        if self.locked_balance < ZERO:
            return "locked_balance"
        equity = self.total_equity(prices)
        if equity != (
            self.available_balance
            + self.locked_balance
            + self.position_market_value(prices)
        ):  # pragma: no cover - both sides are the same computation, by design
            return "total_equity"
        return None

    def invariants_hold(self, prices: Optional[Mapping[str, Any]] = None) -> bool:
        """Whether every Requirement 18.3 / 18.4 / 18.5 invariant holds, exactly."""
        return self.violated_invariant(prices) is None

    # ══════════════════════════════════════════════════════════════════
    # FUNDS (Requirements 16.6, 18.4)
    # ══════════════════════════════════════════════════════════════════

    def required_funds(self, quantity: Any, reference_price: Any) -> Decimal:
        """``notional + fee allowance + slippage allowance`` at the money scale (Req 16.6)."""
        with localcontext() as ctx:
            ctx.prec = PRECISION
            notional = to_decimal(quantity, "quantity") * to_decimal(
                reference_price, "reference_price"
            )
            return self.money(notional * (ONE + self.fee_rate + self.slippage_rate))

    def lock(self, amount: Any) -> Decimal:
        """Move ``amount`` from ``available_balance`` to ``locked_balance``.

        ``available + locked`` is unchanged, so the computed equity is unchanged and no
        snapshot is written. Refuses rather than overdraws (Requirement 18.4).
        """
        quantized = self.money(amount)
        if quantized < ZERO:
            raise ReferenceBadValue(f"lock amount must not be negative: {quantized}")
        if quantized > self.available_balance:
            raise ReferenceInvariantViolation(
                "available_balance",
                f"locking {quantized} would take available_balance below zero from "
                f"{self.available_balance} (Requirement 18.4)",
            )
        self.available_balance -= quantized
        self.locked_balance += quantized
        return quantized

    def unlock(self, amount: Any) -> Decimal:
        """Move ``amount`` from ``locked_balance`` back to ``available_balance``."""
        quantized = self.money(amount)
        if quantized < ZERO:
            raise ReferenceBadValue(f"unlock amount must not be negative: {quantized}")
        if quantized > self.locked_balance:
            raise ReferenceInvariantViolation(
                "locked_balance",
                f"unlocking {quantized} would take locked_balance below zero from "
                f"{self.locked_balance} (Requirement 18.4)",
            )
        self.locked_balance -= quantized
        self.available_balance += quantized
        return quantized

    # ══════════════════════════════════════════════════════════════════
    # ORDERS (Requirements 16.1, 16.2) - the bookkeeping half of P-31
    # ══════════════════════════════════════════════════════════════════

    def submit(self, intent: Mapping[str, Any]) -> RefOrder:
        """Record an order intent as a ``CREATED`` order and return it.

        An intent whose ``idempotency_key`` has been seen returns the existing order
        unchanged, which is the in-memory reading of ``uq_paper_order_idem``. No validation
        of the intent happens here: the rejection catalogue of Requirement 16.5 is the
        simulator's, and a model that re-derived it would be modelling a different rule than
        the one this file is the oracle for.
        """
        key = intent.get("idempotency_key")
        if key is not None and key in self._orders_by_key:
            return self.orders[self._orders_by_key[key]]

        self._order_sequence += 1
        order_id = f"ref-order-{self._order_sequence}"
        limit_price = intent.get("limit_price")
        order = RefOrder(
            order_id=order_id,
            symbol=intent["symbol"],
            side=str(intent["side"]),
            order_type=str(intent.get("order_type", "market")),
            quantity=self.qty(intent["quantity"]),
            limit_price=None if limit_price is None else self.price(limit_price),
            filled_quantity=self.qty(ZERO),
            order_state=CREATED,
            idempotency_key=key,
            created_at=intent.get("created_at"),
        )
        self.orders[order_id] = order
        if key is not None:
            self._orders_by_key[key] = order_id
        return order

    def _transition(self, order: RefOrder, target: str) -> None:
        """Apply one Requirement 16.2 transition, refusing anything else."""
        if target not in ORDER_TRANSITIONS.get(order.order_state, ()):
            raise ReferenceBadTransition(
                f"{order.order_state} -> {target} is not a permitted Paper_Order_State "
                "transition (Requirement 16.2)"
            )
        order.order_state = target

    def accept(self, order_id: str) -> RefOrder:
        """``CREATED -> ACCEPTED``."""
        order = self.orders[order_id]
        self._transition(order, ACCEPTED)
        return order

    def reject(self, order_id: str, reason: str) -> RefOrder:
        """``CREATED -> REJECTED`` or ``ACCEPTED -> REJECTED``, recording the reason."""
        order = self.orders[order_id]
        self._transition(order, REJECTED)
        order.rejection_reason = reason
        return order

    def cancel(self, order_id: str) -> RefOrder:
        """``ACCEPTED -> CANCELLED`` or ``PARTIALLY_FILLED -> CANCELLED``."""
        order = self.orders[order_id]
        self._transition(order, CANCELLED)
        return order

    def fill_order(
        self,
        order_id: str,
        quantity: Any,
        price: Any,
        fee: Any = ZERO,
        *,
        fee_minor: Optional[int] = None,
        fill_event_id: Optional[str] = None,
        filled_at: Optional[datetime] = None,
        release_from_locked: Any = ZERO,
    ) -> RefFill:
        """Apply a fill to a tracked order: the ledger movement plus the state transition.

        A fill against a terminal order changes nothing (Requirement 16.3). A fill that
        would take ``filled_quantity`` above ``quantity`` raises :class:`ReferenceOverFill`
        **before** any ledger movement, so nothing changes (Requirement 16.7). Otherwise the
        order moves to ``PARTIALLY_FILLED`` or, when the ordered quantity is exactly
        reached, to ``FILLED`` (Requirements 16.13, 16.14).

        The order's filled quantity is recomputed from scratch on every call - there is no
        partial-fill cache here, by design.
        """
        order = self.orders[order_id]
        fill_qty = self.qty(quantity)

        if order.order_state in TERMINAL_ORDER_STATES:
            return self._no_op_fill(
                order.symbol, fill_qty, self.price(price), fill_event_id, filled_at,
                order.order_state, duplicate=False,
            )
        if fill_event_id is not None and fill_event_id in self.seen_fill_events:
            return self._no_op_fill(
                order.symbol, fill_qty, self.price(price), fill_event_id, filled_at,
                order.order_state, duplicate=True,
            )
        if self.qty(order.filled_quantity + fill_qty) > order.quantity:
            raise ReferenceOverFill(
                f"filling {fill_qty} on top of {order.filled_quantity} would exceed the "
                f"ordered quantity {order.quantity} (Requirement 16.7)"
            )

        result = self.apply_fill(
            symbol=order.symbol,
            side=order.side,
            quantity=fill_qty,
            price=price,
            fee=fee,
            fee_minor=fee_minor,
            fill_event_id=fill_event_id,
            filled_at=filled_at,
            release_from_locked=release_from_locked,
        )

        order.filled_quantity = self.qty(order.filled_quantity + fill_qty)
        self._transition(
            order, FILLED if order.filled_quantity == order.quantity else PARTIALLY_FILLED
        )
        result.order_state = order.order_state
        return result

    # ══════════════════════════════════════════════════════════════════
    # THE FILL (Requirements 18.5, 18.6, 18.7, 18.8, 18.13)
    # ══════════════════════════════════════════════════════════════════

    def apply_fill(
        self,
        symbol: str,
        side: Any,
        quantity: Any,
        price: Any,
        fee: Any = ZERO,
        *,
        fee_minor: Optional[int] = None,
        fill_event_id: Optional[str] = None,
        filled_at: Optional[datetime] = None,
        release_from_locked: Any = ZERO,
    ) -> RefFill:
        """Apply one fill to the ledger, case by case, and record an equity snapshot.

        The **position** is computed per case - open, add, partial close, full close,
        reversal through zero - and the **cash** movement is then read off the two
        valuations of that position at this fill's price (see the module docstring)::

            cash_delta = value(old_position, price) - value(new_position, price) - fee

        with ``value`` the ``size * price`` / ``size * (2*entry - price)`` convention of
        :meth:`position_value` and a closed or absent position worth exactly zero. Opening or
        adding therefore debits the notional, closing a ``LONG`` credits ``qty * price``,
        closing a ``SHORT`` credits ``qty * (2*entry - price)`` - the notional it consumed at
        ``entry`` plus the profit it made - and ``total_equity`` moves by exactly ``-fee``
        (Requirements 18.6, 18.7).

        Realized PnL is ``(exit - entry) * qty`` closing a ``LONG`` and ``(entry - exit) *
        qty`` closing a ``SHORT``, with **no fee netted in** (Requirement 18.8). The entry
        price of a position that grows is the weighted average
        ``((old_size*old_entry) + (qty*fill)) / new_size`` (Requirement 18.8); the entry
        price of a position that shrinks is untouched.

        Args:
            symbol: The filled symbol.
            side: The order side - ``'buy'``/``'sell'``, or ``'LONG'``/``'SHORT'`` for a
                caller that already speaks position direction.
            quantity: Filled quantity, strictly positive.
            price: Fill price, strictly positive.
            fee: The fee as a ``Decimal`` at the money scale. Ignored when ``fee_minor``
                is given.
            fee_minor: The fee as an integer count of minor units, the way ``paper_fills``
                and ``fill_sequences()`` carry it. Converted by :meth:`fee_from_minor`.
            fill_event_id: The event identity. A repeat changes nothing and writes no
                snapshot (Requirement 18.13).
            filled_at: The fill timestamp. Passed in, never read from a clock (Req 15.4).
            release_from_locked: Cash previously locked for this order that this fill
                consumes; returned to ``available_balance`` before the movement is applied.

        Returns:
            A :class:`RefFill`. ``applied`` is ``False`` for a duplicate event id.

        Raises:
            ReferenceBadValue: Non-positive quantity or price, negative fee, or a ``float``.
            ReferenceInvariantViolation: If the movement would take ``available_balance`` or
                ``locked_balance`` below zero. Nothing is mutated in that case
                (Requirement 18.14).
        """
        fill_qty = self.qty(quantity)
        fill_price = self.price(price)
        fill_fee = (
            self.fee_from_minor(fee_minor) if fee_minor is not None else self.money(fee)
        )
        release = self.money(release_from_locked)
        incoming = self._direction(side)

        if fill_qty <= ZERO:
            raise ReferenceBadValue(f"fill quantity must be positive, got {fill_qty}")
        if fill_price <= ZERO:
            raise ReferenceBadValue(f"fill price must be positive, got {fill_price}")
        if fill_fee < ZERO:
            raise ReferenceBadValue(f"fill fee must not be negative, got {fill_fee}")
        if release < ZERO:
            raise ReferenceBadValue(f"release_from_locked must not be negative: {release}")

        if fill_event_id is not None and fill_event_id in self.seen_fill_events:
            # Requirement 18.13: a repeated event identifier changes no balance, no
            # position, no realized PnL and adds no equity snapshot.
            return self._no_op_fill(
                symbol, fill_qty, fill_price, fill_event_id, filled_at, None, duplicate=True
            )

        equity_before = self.total_equity({symbol: fill_price})
        existing = self.positions.get(symbol)
        old = existing.copy() if existing is not None else None
        open_position = old if old is not None and old.is_open else None

        realized = ZERO
        closed_trade: Optional[RefClosedTrade] = None

        with localcontext() as ctx:
            ctx.prec = PRECISION

            if open_position is None:
                new_position = RefPosition(
                    symbol=symbol,
                    side=incoming,
                    size=fill_qty,
                    entry_price=fill_price,
                    opened_at=filled_at,
                )
            elif open_position.side == incoming:
                new_size = self.qty(open_position.size + fill_qty)
                new_position = open_position.copy()
                new_position.size = new_size
                new_position.entry_price = self.price(
                    (
                        (open_position.size * open_position.entry_price)
                        + (fill_qty * fill_price)
                    )
                    / new_size
                )
                new_position.closed_at = None
            else:
                closing_qty = min(fill_qty, open_position.size)
                if open_position.side == LONG:
                    realized = self.money(
                        (fill_price - open_position.entry_price) * closing_qty
                    )
                else:
                    realized = self.money(
                        (open_position.entry_price - fill_price) * closing_qty
                    )
                remaining = self.qty(open_position.size - closing_qty)

                if remaining == ZERO:
                    closed_trade = RefClosedTrade(
                        symbol=symbol,
                        side=open_position.side,
                        quantity=closing_qty,
                        entry_price=open_position.entry_price,
                        exit_price=fill_price,
                        realized_pnl=realized,
                        fee=fill_fee,
                        opened_at=open_position.opened_at,
                        closed_at=filled_at,
                    )

                if fill_qty > open_position.size:
                    # Reversal: the old position closes and a new one opens on the other
                    # side with the surplus, which is charged like any other opening -
                    # the cash rule below sees only the old and new valuations, so the
                    # surplus needs no separate debit.
                    surplus = self.qty(fill_qty - open_position.size)
                    new_position = RefPosition(
                        symbol=symbol,
                        side=incoming,
                        size=surplus,
                        entry_price=fill_price,
                        opened_at=filled_at,
                    )
                else:
                    new_position = open_position.copy()
                    new_position.size = remaining
                    new_position.closed_at = filled_at if remaining == ZERO else None

            # The one cash rule (Requirements 18.6, 18.7): what the position was worth at
            # this fill's price, less what it is worth now, less the fee. A closed or absent
            # position is worth exactly zero, so open, add, close and reversal all fall out
            # of this single line and equity moves by exactly ``-fee``.
            old_value = (
                self.position_value(open_position, fill_price)
                if open_position is not None
                else ZERO
            )
            new_value = (
                self.position_value(new_position, fill_price)
                if new_position.is_open
                else ZERO
            )
            cash = old_value - new_value

            available_after = self.available_balance + release + cash - fill_fee
            locked_after = self.locked_balance - release

        if locked_after < ZERO:
            raise ReferenceInvariantViolation(
                "locked_balance",
                f"releasing {release} would take locked_balance below zero from "
                f"{self.locked_balance} (Requirement 18.4)",
            )
        if available_after < ZERO:
            raise ReferenceInvariantViolation(
                "available_balance",
                f"the fill of {fill_qty} {symbol} at {fill_price} with fee {fill_fee} "
                f"would take available_balance to {available_after} (Requirement 18.4)",
            )

        # Commit. Nothing above this line touched stored state, so a refusal leaves the
        # ledger exactly as it was (Requirement 18.14).
        new_position.current_price = fill_price
        new_position.price_at = filled_at
        new_position.unrealized_pnl = self.position_unrealized_pnl(
            new_position, fill_price
        )
        self.positions[symbol] = new_position

        available_before = self.available_balance
        locked_before = self.locked_balance
        self.available_balance = available_after
        self.locked_balance = locked_after
        self.realized_pnl = self.money(self.realized_pnl + realized)
        if closed_trade is not None:
            self.closed_trades.append(closed_trade)
        if fill_event_id is not None:
            self.seen_fill_events.add(fill_event_id)

        self._snapshot(FILL, filled_at)

        result = RefFill(
            applied=True,
            duplicate=False,
            symbol=symbol,
            quantity=fill_qty,
            price=fill_price,
            fee=fill_fee,
            realized_pnl=realized,
            available_delta=self.available_balance - available_before,
            locked_delta=self.locked_balance - locked_before,
            total_equity_delta=self.total_equity() - equity_before,
            position=new_position.as_dict(),
            closed_trade=None if closed_trade is None else closed_trade.as_dict(),
            fill_event_id=fill_event_id,
            filled_at=filled_at,
        )
        self.fills.append(result)
        return result

    def _direction(self, side: Any) -> str:
        """``'buy'`` -> ``LONG``, ``'sell'`` -> ``SHORT``; ``LONG``/``SHORT`` pass through."""
        if not isinstance(side, str):
            raise ReferenceBadValue(f"side must be a str, got {type(side).__name__}")
        normalised = side.strip()
        if normalised.upper() in SIDES:
            return normalised.upper()
        if normalised.lower() == BUY:
            return LONG
        if normalised.lower() == SELL:
            return SHORT
        raise ReferenceBadValue(f"unrecognised side {side!r}")

    def _no_op_fill(
        self,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
        fill_event_id: Optional[str],
        filled_at: Optional[datetime],
        order_state: Optional[str],
        *,
        duplicate: bool,
    ) -> RefFill:
        """The result of a fill that was absorbed: every delta exactly zero.

        Not appended to :attr:`fills` and not snapshotted, which is the whole content of
        Requirement 18.13's "SHALL persist no additional equity snapshot for a repeated
        event identifier".
        """
        position = self.positions.get(symbol)
        return RefFill(
            applied=False,
            duplicate=duplicate,
            symbol=symbol,
            quantity=quantity,
            price=price,
            fee=self.money(ZERO),
            realized_pnl=self.money(ZERO),
            available_delta=self.money(ZERO),
            locked_delta=self.money(ZERO),
            total_equity_delta=self.money(ZERO),
            position=None if position is None else position.as_dict(),
            order_state=order_state,
            fill_event_id=fill_event_id,
            filled_at=filled_at,
        )

    # ══════════════════════════════════════════════════════════════════
    # A STANDALONE FEE (Requirements 18.7, 18.11)
    # ══════════════════════════════════════════════════════════════════

    def apply_fee(
        self,
        amount: Any = ZERO,
        *,
        fee_minor: Optional[int] = None,
        charged_at: Optional[datetime] = None,
    ) -> Decimal:
        """Charge a fee that is not attached to a fill, and snapshot the result.

        Requirement 18.11 lists "each applied fee" as its own equity-snapshot cause, and
        Requirement 18.7 makes the equity movement exactly the fee. No position moves, so
        this is the whole rule: cash down by the fee, one ``FEE`` snapshot. Refused rather
        than overdrawn (Requirement 18.4).
        """
        fee = self.fee_from_minor(fee_minor) if fee_minor is not None else self.money(amount)
        if fee < ZERO:
            raise ReferenceBadValue(f"fee must not be negative, got {fee}")
        if fee > self.available_balance:
            raise ReferenceInvariantViolation(
                "available_balance",
                f"charging {fee} would take available_balance below zero from "
                f"{self.available_balance} (Requirement 18.4)",
            )
        self.available_balance -= fee
        self._snapshot(FEE, charged_at)
        return fee

    # ══════════════════════════════════════════════════════════════════
    # REVALUATION AND SNAPSHOTS (Requirements 18.8, 18.11, 18.15)
    # ══════════════════════════════════════════════════════════════════

    def revalue(
        self,
        prices: Mapping[str, Any],
        price_at: Optional[datetime] = None,
        *,
        cause: str = REVALUATION,
    ) -> Decimal:
        """Revalue every open position at ``prices`` and snapshot the resulting equity.

        Cash is untouched: a revaluation moves no money, it only restates what the open
        positions are worth. Returns the recomputed ``total_equity``.

        A symbol absent from ``prices`` falls back to the position's last recorded price, and
        the revaluation is then reported as :attr:`stale` with that position's ``price_at``
        left where it was - the timestamp of the last validated price actually used
        (Requirement 18.15). A position that has never been priced at all raises
        :class:`ReferenceStalePrice`: there is nothing to fall back to, and a synthesised,
        interpolated or zero price is exactly what Requirement 18.15 forbids.

        :attr:`stale` is recomputed from scratch on every call, so a revaluation that does
        carry a price for every open position clears the flag rather than leaving the session
        marked stale forever.
        """
        missing = False
        for position in self.positions.values():
            if not position.is_open:
                position.unrealized_pnl = self.money(ZERO)
                continue
            given = prices.get(position.symbol) if isinstance(prices, Mapping) else None
            current = self._price_for(position, prices)
            position.current_price = current
            position.unrealized_pnl = self.position_unrealized_pnl(position, current)
            if given is None:
                # Fell back to the last validated price: ``price_at`` stays as it was.
                missing = True
            elif price_at is not None:
                position.price_at = price_at
        self.stale = missing
        equity = self.total_equity()
        self._snapshot(cause, price_at)
        return equity

    def stop(self, stopped_at: Optional[datetime] = None) -> Decimal:
        """Record the ``SESSION_STOP`` equity snapshot (Requirement 18.11)."""
        self._snapshot(SESSION_STOP, stopped_at)
        return self.equity_series[-1]["total_equity"]

    def _snapshot(self, cause: str, taken_at: Optional[datetime]) -> Dict[str, Any]:
        """Append one ``paper_equity_snapshots`` row, in the shape the generators draw.

        Requirement 18.11 wants a snapshot on session start, each applied fill, each applied
        fee, each revaluation and session stop. ``total_equity`` here is the computed sum, so
        the persisted series satisfies Requirement 18.3 row by row.
        """
        market_value = self.position_market_value()
        row = {
            "series_index": len(self.equity_series),
            "total_equity": self.available_balance + self.locked_balance + market_value,
            "available_balance": self.available_balance,
            "locked_balance": self.locked_balance,
            "position_market_value": market_value,
            "stale": self.stale,
            "cause": cause,
            "taken_at": taken_at,
        }
        self.equity_series.append(row)
        return row

    # ══════════════════════════════════════════════════════════════════
    # METRICS (Requirements 18.9, 18.10, 18.12)
    # ══════════════════════════════════════════════════════════════════

    def max_drawdown(self, snapshots: Optional[Sequence[Any]] = None) -> RefDrawdown:
        """Largest decline from a running peak to a later trough (Requirement 18.9).

        Zero below two snapshots - one point describes no decline. ``fraction`` is that
        amount over the peak at the trough, in ``[0, 1]``, and zero when that peak is not
        positive. Accepts the ledger's own series (the default), ``paper_equity_snapshots``
        row mappings, or bare equity values.
        """
        series = list(self.equity_series if snapshots is None else snapshots)
        if len(series) < 2:
            return RefDrawdown(amount=ZERO, fraction=ZERO)

        with localcontext() as ctx:
            ctx.prec = PRECISION
            equities = [self._equity_of(item) for item in series]
            peak = equities[0]
            amount = ZERO
            peak_at_trough = peak
            for equity in equities[1:]:
                if equity > peak:
                    peak = equity
                if peak - equity > amount:
                    amount = peak - equity
                    peak_at_trough = peak

            if amount <= ZERO:
                return RefDrawdown(amount=ZERO, fraction=ZERO)
            if peak_at_trough <= ZERO:
                return RefDrawdown(amount=amount, fraction=ZERO)
            fraction = (amount / peak_at_trough).quantize(
                FRACTION_QUANTUM, rounding=self.rounding_mode
            )
            return RefDrawdown(amount=amount, fraction=min(fraction, ONE))

    @staticmethod
    def _equity_of(item: Any) -> Decimal:
        """``total_equity`` off a snapshot mapping, an object, or a bare value."""
        if isinstance(item, Mapping):
            return to_decimal(item["total_equity"], "snapshot total_equity")
        if hasattr(item, "total_equity"):
            return to_decimal(item.total_equity, "snapshot total_equity")
        return to_decimal(item, "snapshot total_equity")

    def win_rate(self) -> Optional[Decimal]:
        """Winning closed trades over all closed trades, or ``None`` for none at all.

        Requirement 18.10: strictly positive realized profit counts as a win, and an empty
        closed set reports **absent** rather than zero - zero would claim every trade lost.
        """
        total = len(self.closed_trades)
        if total == 0:
            return None
        wins = sum(1 for trade in self.closed_trades if trade.is_win)
        with localcontext() as ctx:
            ctx.prec = PRECISION
            return (Decimal(wins) / Decimal(total)).quantize(
                FRACTION_QUANTUM, rounding=self.rounding_mode
            )

    def margin_usage(
        self, prices: Optional[Mapping[str, Any]] = None
    ) -> Optional[Decimal]:
        """Notional committed by open positions, or ``None`` where margin does not apply.

        Requirement 18.12: a market type that does not support margin reports no figure at
        all, not a zero presented as a measurement.
        """
        if self.market_type not in MARGIN_MARKET_TYPES:
            return None
        with localcontext() as ctx:
            ctx.prec = PRECISION
            total = ZERO
            for position in self.positions.values():
                if position.is_open:
                    total += self.money(
                        position.size * self._price_for(position, prices)
                    )
            return total

    def total_return_pct(self) -> Optional[Decimal]:
        """``(total_equity - initial_capital) / initial_capital * 100``, or ``None``.

        ``None`` when no positive initial capital was recorded: a return on nothing is not a
        percentage.
        """
        capital = to_decimal(self.initial_balance, "initial_balance")
        if capital <= ZERO:
            return None
        with localcontext() as ctx:
            ctx.prec = PRECISION
            return ((self.total_equity() - capital) / capital * HUNDRED).quantize(
                FRACTION_QUANTUM, rounding=self.rounding_mode
            )

    # ══════════════════════════════════════════════════════════════════
    # THE COMPARABLE PROJECTION (P-31)
    # ══════════════════════════════════════════════════════════════════

    def state(self) -> Dict[str, Any]:
        """Everything P-31 compares, as plain built-in structures.

        Exact ``Decimal`` values, no objects of this module's own types, so a property test
        can compare this against a projection of the simulator's persisted state with a bare
        ``==`` and get an exact-decimal comparison rather than an approximate one.
        """
        return {
            "available_balance": self.available_balance,
            "locked_balance": self.locked_balance,
            "realized_pnl": self.realized_pnl,
            "total_equity": self.total_equity(),
            "currency": self.currency,
            "positions": {
                symbol: position.as_dict()
                for symbol, position in self.positions.items()
            },
            "closed_trades": [trade.as_dict() for trade in self.closed_trades],
            "orders": {
                order_id: order.as_dict() for order_id, order in self.orders.items()
            },
            "equity_series": [dict(row) for row in self.equity_series],
            "stale": self.stale,
        }


# ══════════════════════════════════════════════════════════════════════════
# DETERMINISTIC REPLAY (Requirements 15.4, 15.5) - task 27.5
# ══════════════════════════════════════════════════════════════════════════
#
# :func:`replay` reconstructs a session from ``(paper_sessions.config, paper_market_events,
# the recorded order intents in paper_orders)`` against a fresh in-memory store - the
# :class:`ReferenceLedger` above - and returns the final order states, fills, balances,
# positions, realized PnL and equity series. ``design.md`` -> "Deterministic replay
# (Requirement 15.4, 15.5, P-31)", which names this file, this input triple and this store.
#
# WHERE EACH HALF OF THE RECONSTRUCTION COMES FROM, AND WHY IT IS NOT A THIRD PATH
# -------------------------------------------------------------------------------
# A session's behaviour is two separable decisions, and this function borrows both rather
# than re-deriving either:
#
# ==========================  ====================================================================
# The FILL MODEL              ``paper_simulator``'s own functions, called directly:
# (what price, whether a      ``static_rejection_reason``, ``reference_price``,
# resting order triggers,     ``market_fill_price``, ``fee_amount``, ``slippage_amount``,
# how much fills, what fee)   ``limit_fill_triggered``, ``limit_fill_price``,
#                             ``fillable_quantity``, ``market_fill_event_id``,
#                             ``resting_fill_event_id``. Every one is PURE - no clock, no
#                             ``random``, no statement - and every one reads its rates and
#                             precisions off the frozen ``SessionConfig``. So the replay's fill
#                             decisions are not a copy of the simulator's: they ARE the
#                             simulator's, which is what makes "the same order states and fills"
#                             (Requirement 15.4) a fact rather than a hope.
# The ACCOUNTING              :class:`ReferenceLedger`, the fresh in-memory store. NOT
# (balances, positions,       ``paper_accounting`` + ``paper_repository``, because those write
# realized PnL, equity)       to the database and a replay must reconstruct without touching
#                             the recorded session. The two agree by P-31 - that is precisely
#                             what P-31 is for - so the reconstruction is comparable to the
#                             stored figures, and a disagreement is P-31's failure to report.
# ==========================  ====================================================================
#
# WHY NOT ``paper_session_service.step_session``
# ---------------------------------------------
# Its section header offers itself for this ("it is also what lets ``paper_replay`` (task 27.5)
# drive the same per-event path over a recorded event stream instead of a live feed"), and the
# per-event ORDER stated there is followed here exactly: for each recorded event, the intents
# recorded against that bar are submitted, then the resting book is checked against the same
# event, then the book is revalued. What cannot be reused is the callable, and for reasons of
# substance rather than of taste: ``step_session`` takes a live ``FeedHandle`` and pulls the
# next event off it, WRITES every row it computes through ``paper_repository``, EMITS a
# ``paper_events`` record and a Paper_Channel frame per bar, and calls the platform's DAG
# runtime to produce the intents. A replay has a recorded stream and not a feed, must write
# nothing (the reconstruction is against a fresh in-memory store), must emit nothing (a replay
# that re-broadcast a year-old session's ticks would be publishing a fiction to live
# subscribers, and would need a seventeenth ``paper_events`` type ``chk_paper_event_type``
# does not admit), and must take the intents from ``paper_orders`` rather than from the DAG -
# the intents are a recorded INPUT here, which is the whole of Requirement 15.4's "the same
# order intents". So the shape genuinely does not line up, and what is reused is the ORDER and
# the fill model rather than the function.
#
# WHAT MAKES IT BYTE-IDENTICAL
# ----------------------------
# * Every timestamp written into the reconstruction - a fill's ``filled_at``, a position's
#   ``opened_at`` / ``price_at``, an equity point's ``taken_at`` - is the recorded event's
#   ``event_timestamp``. There is no ``now()`` call on this path, and no ``utcnow`` import in
#   this module at all.
# * Every rate is the frozen config's, read back through
#   ``paper_simulator.session_config_from_jsonb``, which REFUSES an incomplete payload rather
#   than completing it (Requirements 16.12, 28.3).
# * No ``random``, anywhere in this package, statically enforced by
#   ``tests/test_paper_no_random.py``.
# * The five OHLCV values come back off ``paper_market_events.payload`` as exact decimal
#   STRINGS, so ``Decimal(text)`` recovers the digits the exchange published. A ``float`` is
#   refused, not converted (Requirement 18.1).
#
# THE INTERLEAVE, AND THE ONE PLACE THE LOG'S RESOLUTION LIMITS IT
# ---------------------------------------------------------------
# An order intent has to be replayed against the event the session priced it from, and the
# only recorded evidence of that pairing is time: ``paper_market_events.received_at`` is when
# the session accepted the event, ``paper_orders.created_at`` is when it inserted the order.
# So an intent belongs to the LAST event that had arrived when it was created - which is
# exactly the pairing ``step_session`` produces, since it submits a bar's intents inside that
# bar's step. Both instants are stored columns; reading them is not a clock read.
#
# Where two orders were created inside the same recorded instant the log does not say which
# came first, so the tie is broken by ``paper_orders.id`` to keep the reconstruction
# deterministic, and :attr:`Reconstruction.tied_intents` REPORTS how many ties were broken
# that way instead of leaving the ambiguity silent.
#
# WHAT THIS DOES NOT RECONSTRUCT, STATED RATHER THAN GLOSSED
# ---------------------------------------------------------
# 1. **A session that was stopped or reset mid-log.** ``stop_session`` cancels resting orders
#    and ``reset_session`` cancels them, closes every position and restarts the equity series
#    (task 27.4). Neither is a market event or an order intent, so neither is in this
#    function's input triple, and a session that was reset replays as though it had not been:
#    its cancelled orders come back ``ACCEPTED`` and its equity series is one series rather
#    than two. Requirement 15.4 is about replaying events, configuration and intents, and the
#    lifecycle operations are outside that; reconstructing them would need
#    ``paper_balance_events`` and ``paper_events`` in the input, which is a wider contract than
#    the design states. :attr:`Reconstruction.session_state` carries the state the session is
#    recorded in so a caller can see that it was stopped or reset.
# 2. **The feed gate.** ``submit_intent`` raises ``FeedNotHealthy`` for a market order on a
#    feed that is not ``HEALTHY`` - BEFORE any order row is inserted. So a gated intent left no
#    recorded intent to replay, and the gate needs no model here: it cannot cause a divergence
#    on any order that exists. ``paper_market_events`` records no per-event feed state, so this
#    is also the only reading available.
# 3. **A retry.** ``with_retries`` re-runs an attempt on a version conflict. The outcome is the
#    same either way (that is what the version guard is for) and the backoff is jitter-free, so
#    a retry changes no figure - only how many statements were issued, which a reconstruction
#    that issues none cannot and need not report.

#: Why a replay refused. Stable strings: a runbook and a test both match on a code rather than on
#: a sentence, the same convention ``paper_simulator.REJECTION_*`` and
#: ``paper_market_feed.INVALID_*`` follow. Every one of them means "the recorded session cannot be
#: reconstructed as it was recorded" - never "reconstruct it under a filled-in value".
REFUSAL_SESSION_UNREADABLE = "SESSION_UNREADABLE"
REFUSAL_CONFIG_INCOMPLETE = "SESSION_CONFIG_INCOMPLETE"
REFUSAL_CAPITAL_UNREADABLE = "INITIAL_CAPITAL_UNREADABLE"
REFUSAL_MARKET_LOG_UNREADABLE = "MARKET_EVENT_LOG_UNREADABLE"
REFUSAL_ORDER_LOG_UNREADABLE = "ORDER_LOG_UNREADABLE"
REFUSAL_EVENT_UNREADABLE = "MARKET_EVENT_UNREADABLE"
REFUSAL_EVENT_IDENTITY_MISMATCH = "MARKET_EVENT_IDENTITY_MISMATCH"
REFUSAL_ORDER_UNREADABLE = "RECORDED_ORDER_UNREADABLE"

REFUSAL_REASONS: Tuple[str, ...] = (
    REFUSAL_SESSION_UNREADABLE,
    REFUSAL_CONFIG_INCOMPLETE,
    REFUSAL_CAPITAL_UNREADABLE,
    REFUSAL_MARKET_LOG_UNREADABLE,
    REFUSAL_ORDER_LOG_UNREADABLE,
    REFUSAL_EVENT_UNREADABLE,
    REFUSAL_EVENT_IDENTITY_MISMATCH,
    REFUSAL_ORDER_UNREADABLE,
)

#: The six payload fields ``paper_market_feed.source_event_id`` builds Requirement 14.7's event
#: identity from. A row carrying all six can have its identity RECOMPUTED rather than trusted,
#: which is why that function is public; a row carrying fewer cannot, and is counted as unverified
#: rather than refused - the pricing fields it does carry are still the ones the session priced
#: from, and refusing a readable log because its payload predates a field would be a refusal to
#: audit rather than an audit.
IDENTITY_PAYLOAD_FIELDS: Tuple[str, ...] = (
    "exchange",
    "symbol",
    "timeframe",
    "timestamp",
    "close",
    "volume",
)


class PaperReplayError(Exception):
    """Base class for every refusal :func:`replay` makes."""


class ReplayRefused(PaperReplayError):
    """The recorded session cannot be reconstructed as it was recorded.

    Raised rather than answered, and rather than reconstructed under a substituted value: a
    session whose configuration is incomplete, whose market-data log did not read, or whose
    recorded rows cannot be read as what they are must not be replayed on a filled-in one
    (Requirements 16.12, 28.3). Nothing is reconstructed when this is raised.

    ``reason`` is one of :data:`REFUSAL_REASONS`, so a caller matches a code.
    """

    def __init__(self, reason: str, detail: str, *, session_id: Any = None) -> None:
        self.reason = str(reason)
        self.detail = str(detail)
        self.session_id = None if session_id is None else str(session_id)
        super().__init__(f"{self.reason}: {self.detail}")


@dataclass(frozen=True)
class ReplayedOrder:
    """One recorded order intent, and the state replaying it put the order in.

    The intent's own values (``symbol`` ... ``limit_price``, ``idempotency_key``, ``created_at``)
    are the RECORDED ones, exact off the ``paper_orders`` row - not the ledger's quantization of
    them, so an order whose recorded quantity carries more decimal places than the session's
    precision (which is Requirement 16.5's ``QUANTITY_PRECISION`` rejection) reports the quantity
    the session recorded rather than a rounded one.

    Everything from ``reference_price`` down is RE-DERIVED by the replay: that is the claim
    Requirement 15.4 makes, and comparing it against the stored columns is what checks it.
    """

    order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Optional[Decimal]
    idempotency_key: Optional[str]
    created_at: Optional[datetime]
    reference_price: Optional[Decimal]
    filled_quantity: Decimal
    avg_fill_price: Optional[Decimal]
    fee_minor: int
    slippage_minor: int
    order_state: str
    rejection_reason: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        """The comparable projection, as plain built-in values and exact ``Decimal``."""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "quantity": self.quantity,
            "limit_price": self.limit_price,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
            "reference_price": self.reference_price,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": self.avg_fill_price,
            "fee_minor": self.fee_minor,
            "slippage_minor": self.slippage_minor,
            "order_state": self.order_state,
            "rejection_reason": self.rejection_reason,
        }


@dataclass(frozen=True)
class ReplayedFill:
    """One fill the replay applied, in the shape ``paper_fills`` records it.

    ``fill_event_id`` is the deterministic identity ``paper_simulator`` derives - from the order
    alone for a market fill, from the order and the event's identity for a resting one - so it is
    the SAME string the original run wrote and the two fill logs can be compared row for row.
    """

    order_id: str
    fill_event_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    fee_minor: int
    slippage: Decimal
    slippage_minor: int
    reference_price: Decimal
    source_event_id: Optional[str]
    filled_at: Optional[datetime]
    realized_pnl: Decimal
    order_state: str

    def as_dict(self) -> Dict[str, Any]:
        """The comparable projection, as plain built-in values and exact ``Decimal``."""
        return {
            "order_id": self.order_id,
            "fill_event_id": self.fill_event_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "fee": self.fee,
            "fee_minor": self.fee_minor,
            "slippage": self.slippage,
            "slippage_minor": self.slippage_minor,
            "reference_price": self.reference_price,
            "source_event_id": self.source_event_id,
            "filled_at": self.filled_at,
            "realized_pnl": self.realized_pnl,
            "order_state": self.order_state,
        }


@dataclass(frozen=True)
class Reconstruction:
    """What :func:`replay` returns: the whole reconstruction, and what it was built from.

    The five subjects task 27.5 names - final order states, fills, balances, positions, realized
    PnL and equity series - plus the closed round trips (a ``paper_trades`` row is written only
    when a position reaches exactly zero, so it is the one place a reversal's split into "the trip
    that closed" and "the surplus that opened the other side" is visible) and the counts an audit
    needs to know what was actually read.

    :meth:`as_dict` is the whole thing as plain built-in structures, so two reconstructions - or a
    reconstruction and a projection of the stored rows - compare with a bare ``==`` and get exact
    ``Decimal`` equality rather than an approximate one.
    """

    session_id: str
    user_id: str
    session_state: Optional[str]
    currency: str
    initial_balance: Decimal
    config: Any
    events_replayed: int
    events_verified: int
    events_unverified: int
    intents_replayed: int
    tied_intents: int
    orders: Tuple[ReplayedOrder, ...]
    fills: Tuple[ReplayedFill, ...]
    balances: Mapping[str, Decimal]
    positions: Mapping[str, Mapping[str, Any]]
    realized_pnl: Decimal
    equity_series: Tuple[Mapping[str, Any], ...]
    closed_trades: Tuple[Mapping[str, Any], ...]
    stale: bool

    @property
    def order_states(self) -> Dict[str, str]:
        """``{order_id: Paper_Order_State}`` - Requirement 15.4's first named subject."""
        return {order.order_id: order.order_state for order in self.orders}

    def as_dict(self) -> Dict[str, Any]:
        """Everything a caller compares, as plain built-in structures."""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "session_state": self.session_state,
            "currency": self.currency,
            "initial_balance": self.initial_balance,
            "events_replayed": self.events_replayed,
            "events_verified": self.events_verified,
            "events_unverified": self.events_unverified,
            "intents_replayed": self.intents_replayed,
            "tied_intents": self.tied_intents,
            "orders": [order.as_dict() for order in self.orders],
            "fills": [fill.as_dict() for fill in self.fills],
            "balances": dict(self.balances),
            "positions": {
                symbol: dict(position) for symbol, position in self.positions.items()
            },
            "realized_pnl": self.realized_pnl,
            "equity_series": [dict(row) for row in self.equity_series],
            "closed_trades": [dict(trade) for trade in self.closed_trades],
            "stale": self.stale,
        }


def _instant(value: Any) -> Optional[datetime]:
    """A ``TIMESTAMPTZ`` value as a tz-aware UTC ``datetime``, or ``None``.

    The reading ``paper_repository._instant`` writes: ISO-8601 with an explicit offset. A trailing
    ``Z`` is translated because ``fromisoformat`` on this interpreter's minimum version does not
    accept it, and a naive value is assumed UTC and made explicit so no comparison here ever puts
    a naive instant beside an aware one.

    NOT a clock read and never a default: an unreadable instant answers ``None``, and every caller
    below turns that ``None`` into a named refusal rather than into ``now()``.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _minor_of(amount: Decimal, exponent: int) -> int:
    """A money amount as the exact integer Minor_Units a ``*_minor`` column stores.

    ``amount.scaleb(exponent)`` and an exactness check - the same reading
    ``paper_simulator._minor_units`` makes, spelled here because it is a private helper there and
    this module does not reach into another's privates. A value that is not a whole number of
    minor units is refused rather than rounded: rounding would change a recorded fee
    (Requirement 18.2).
    """
    scaled = to_decimal(amount, "minor units").scaleb(int(exponent))
    if scaled != scaled.to_integral_value():
        raise ReferenceBadValue(
            f"{amount} is not a whole number of minor units at exponent {exponent}; it is not "
            "rounded here, because rounding a recorded fee or slippage would change the figure "
            "the session reports (Requirement 18.2)"
        )
    return int(scaled)


def _flat_event(row: Mapping[str, Any]) -> Dict[str, Any]:
    """One ``paper_market_events`` row as the flat mapping the fill model reads.

    ``payload`` carries the five OHLCV values as exact decimal strings plus the market, the
    instant and the transport; the row carries the identity, the ordering and the symbol. Flattened
    into one mapping so ``paper_simulator``'s public fill-model functions - which accept exactly
    this shape - are handed the recorded event and nothing else.

    ``id`` is deliberately NOT copied in, and ``source_event_id`` deliberately is. A replay that
    supplied the row's database-generated primary key would produce a fill that differed from the
    recorded one in that column alone, which is a divergence invented by the reconstruction rather
    than found by it. ``paper_simulator`` writes ``paper_fills.market_event_id`` from
    ``event["source_event_id"]`` - the identity ``uq_paper_market_event`` de-duplicates on, which
    the live path's ``paper_market_feed.MarketEvent`` carries and this mapping carries too - so the
    provenance the live path stores is a value this reconstruction reproduces exactly rather than
    one it has to leave out. :attr:`ReplayedFill.source_event_id` reports the same identity.
    """
    payload = row.get("payload")
    event: Dict[str, Any] = dict(payload) if isinstance(payload, Mapping) else {}
    event["symbol"] = row.get("symbol")
    event["timeframe"] = row.get("timeframe")
    event["event_timestamp"] = row.get("event_timestamp")
    event["sequence"] = row.get("sequence")
    event["source_event_id"] = row.get("source_event_id")
    return event


def _recomputed_identity(event: Mapping[str, Any]) -> Optional[str]:
    """Requirement 14.7's event identity, recomputed from the stored payload, or ``None``.

    ``None`` when the payload does not carry all six of :data:`IDENTITY_PAYLOAD_FIELDS`, which is
    "this row's identity cannot be recomputed" and not "it is wrong".
    ``paper_market_feed.source_event_id`` is called rather than re-derived: it is public precisely
    so an audit can recompute an identity from a stored row rather than trusting the stored string,
    and its ``canonical_number`` normalisation is what makes a re-serialised close land on the same
    identity.
    """
    if any(event.get(name) is None for name in IDENTITY_PAYLOAD_FIELDS):
        return None
    try:
        return feed.source_event_id(
            event["exchange"],
            event["symbol"],
            event["timeframe"],
            int(to_decimal(event["timestamp"], "payload timestamp")),
            to_decimal(event["close"], "payload close"),
            to_decimal(event["volume"], "payload volume"),
        )
    except (ReferenceBadValue, InvalidOperation, ValueError, TypeError):
        return None


def _resting_row(order_id: str, intent: Any, created_at: Optional[datetime]) -> Dict[str, Any]:
    """One resting limit order as the mapping the fill model reads it from.

    The same three columns ``paper_simulator.limit_fill_triggered`` /
    ``fillable_quantity`` / ``limit_fill_price`` read off a ``paper_orders`` row - ``side``,
    ``limit_price``, ``quantity`` and ``filled_quantity`` - so the trigger and the participation
    cap are decided by the module that decides them in production, on the shape it decides them
    on. ``filled_quantity`` is updated in place as the replay fills it, exactly as the column is.
    """
    return {
        "id": order_id,
        "symbol": intent.symbol,
        "side": intent.side,
        "order_type": intent.order_type,
        "limit_price": intent.limit_price,
        "quantity": intent.quantity,
        "filled_quantity": ZERO,
        "created_at": created_at,
    }


@dataclass
class _Run:
    """The mutable state of ONE reconstruction. Created per :func:`replay` call, discarded after.

    A value object rather than a pile of arguments threaded through four helpers, and rather than
    module-level state: two replays running in one process must not be able to see each other's
    book, and a module global is exactly how they would.

    Attributes:
        config: the session's frozen :class:`~paper_simulator.SessionConfig`.
        ledger: the fresh in-memory store every figure is accumulated in.
        book: the resting limit orders, as the mappings the fill model reads them from.
        orders: ``{recorded order id: ReplayedOrder}``, rewritten as an order's state moves.
        fills: every applied fill, in the order they were applied.
        ref_ids: ``{recorded order id: the ledger's own order id}``. The ledger mints
            ``ref-order-{n}`` and the reconstruction is keyed by the RECORDED id, so the two are
            mapped rather than one being renamed.
        order_fills: ``{recorded order id: [RefFill, ...]}`` - what ``filled_quantity``,
            ``avg_fill_price`` and ``fee_minor`` are projected from, per order.
        order_slippage: ``{recorded order id: cumulative recorded slippage cost}``.
    """

    config: Any
    ledger: ReferenceLedger
    book: List[Dict[str, Any]] = field(default_factory=list)
    orders: Dict[str, "ReplayedOrder"] = field(default_factory=dict)
    fills: List["ReplayedFill"] = field(default_factory=list)
    ref_ids: Dict[str, str] = field(default_factory=dict)
    order_fills: Dict[str, List[RefFill]] = field(default_factory=dict)
    order_slippage: Dict[str, Decimal] = field(default_factory=dict)

    def record(
        self,
        order_id: str,
        intent: Any,
        created_at: Optional[datetime],
        reference: Optional[Decimal],
    ) -> None:
        """(Re)project one order onto :class:`ReplayedOrder` from its current ledger state."""
        order = self.ledger.orders[self.ref_ids[order_id]]
        self.orders[order_id] = _projected(
            order_id=order_id,
            intent=intent,
            created_at=created_at,
            reference=reference,
            order=order,
            applied=self.order_fills.get(order_id, []),
            slippage=self.order_slippage.get(order_id, ZERO),
            ledger=self.ledger,
        )

    def add_fill(
        self,
        order_id: str,
        intent: Any,
        result: RefFill,
        reference: Decimal,
        slippage: Decimal,
        source_event_id: Any,
    ) -> None:
        """Record one applied fill, and the two per-order totals projected off it."""
        self.order_fills.setdefault(order_id, []).append(result)
        self.order_slippage[order_id] = self.ledger.money(
            self.order_slippage.get(order_id, ZERO) + slippage
        )
        exponent = int(self.config.minor_unit_exponent)
        self.fills.append(
            ReplayedFill(
                order_id=order_id,
                fill_event_id=str(result.fill_event_id),
                symbol=intent.symbol,
                side=intent.side,
                quantity=result.quantity,
                price=result.price,
                fee=result.fee,
                fee_minor=_minor_of(result.fee, exponent),
                slippage=slippage,
                slippage_minor=_minor_of(slippage, exponent),
                reference_price=reference,
                source_event_id=(
                    None if source_event_id is None else str(source_event_id)
                ),
                filled_at=result.filled_at,
                realized_pnl=result.realized_pnl,
                order_state=str(result.order_state),
            )
        )


def _order_sort_key(row: Mapping[str, Any]) -> Tuple[str, str]:
    """``(created_at, id)`` as text - the deterministic order of the recorded intents.

    ``created_at`` first because it is the recorded submission order, ``id`` second because two
    orders created inside one recorded instant are not ordered by the log and something has to
    break the tie deterministically. Compared as ISO-8601 TEXT, which sorts as the instant does
    for the offset-carrying form ``paper_repository._instant`` writes.
    """
    return (str(row.get("created_at") or ""), str(row.get("id") or ""))


def replay(
    supabase: Any, user_id: Any, session_id: Any
) -> Optional[Reconstruction]:
    """Reconstruct one recorded Paper_Session from its log. Requirements 15.4, 15.5. Task 27.5.

    Reads the session's frozen configuration, its append-only ``paper_market_events`` log and its
    recorded order intents, drives them through ``paper_simulator``'s fill model against a fresh
    :class:`ReferenceLedger`, and returns the final order states, fills, balances, positions,
    realized PnL and equity series. **Nothing is written**: the reconstruction lives in the ledger
    and dies with the return value, so replaying a session cannot alter the session it is auditing.

    THE SIGNATURE, AND WHY IT IS NOT ``replay(session_id)``
    ------------------------------------------------------
    ``design.md`` writes it as ``replay(session_id)``. It cannot be, and the difference is a
    security property rather than a style: every read here goes through ``paper_repository``, and
    every one of those functions carries ``user_id`` as a PREDICATE (Requirements 21.2, 21.5). A
    one-argument signature would have to resolve the owner itself and would then be reading rows
    it had not been authorised for. So the caller's identity is an argument, exactly as it is on
    ``read_session_config``, ``read_session_lifecycle`` and ``get_market_events``.

    Args:
        supabase: the caller's RLS-scoped Persistence_Layer handle.
        user_id: the identity the reads are scoped to. A predicate, not a filter applied after.
        session_id: the Paper_Session to reconstruct.

    Returns:
        The :class:`Reconstruction`, or ``None`` when the reads completed and matched nothing.
        ``None`` is deliberately the same answer for "no such session" and "another user's
        session" (Requirements 21.2, 21.5), which is how ``read_session``,
        ``read_session_config`` and ``read_session_lifecycle`` all answer it - a distinct answer
        for the second would disclose that the session exists.

    Raises:
        ReplayRefused: a read DID NOT COMPLETE, the recorded configuration is incomplete, the
            recorded initial capital is unreadable, a recorded event carries no readable instant,
            or a recorded event's identity does not match its payload. ``reason`` is one of
            :data:`REFUSAL_REASONS` and NOTHING is reconstructed - a read that did not complete is
            not a read that found nothing, and a session with an incomplete configuration must not
            be replayed under a filled-in one (Requirements 16.12, 28.3).
    """
    uid = str(user_id)
    sid = str(session_id)

    # ── 1: the session, its frozen configuration and its recorded capital ──
    try:
        row = repo.read_session_lifecycle(supabase, uid, sid)
    except repo.PaperRepositoryError as exc:
        raise ReplayRefused(
            REFUSAL_SESSION_UNREADABLE,
            f"the paper_sessions read for {sid} did not complete, so the frozen configuration "
            f"this replay must apply is unknown: {exc}",
            session_id=sid,
        ) from exc
    if row is None:
        return None

    try:
        config = sim.session_config_from_jsonb(row.get("config"))
    except sim.InvalidSessionConfig as exc:
        raise ReplayRefused(
            REFUSAL_CONFIG_INCOMPLETE,
            f"session {sid} has no complete recorded configuration, so there is no fee rate, "
            f"slippage rate or participation rate to replay it under: {exc}",
            session_id=sid,
        ) from exc

    try:
        capital_minor = int(row["initial_capital_minor"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReplayRefused(
            REFUSAL_CAPITAL_UNREADABLE,
            f"session {sid} records no readable initial_capital_minor, so the balance every "
            f"figure below is measured against is unknown; a zero would be a fabricated one "
            f"(Requirement 28.3)",
            session_id=sid,
        ) from exc
    initial_balance = Decimal(capital_minor).scaleb(-config.minor_unit_exponent)
    currency = str(row.get("currency") or "")

    # ── 2: the two logs. ``[]`` is a real answer; a failed read is not. ──
    try:
        event_rows = repo.get_market_events(supabase, uid, sid)
    except repo.PaperRepositoryError as exc:
        raise ReplayRefused(
            REFUSAL_MARKET_LOG_UNREADABLE,
            f"the paper_market_events read for session {sid} did not complete. Answering with a "
            f"partial or empty event log would report a session that never traded: {exc}",
            session_id=sid,
        ) from exc
    try:
        order_rows = repo.get_orders(supabase, uid, session_id=sid)
    except repo.PaperRepositoryError as exc:
        raise ReplayRefused(
            REFUSAL_ORDER_LOG_UNREADABLE,
            f"the paper_orders read for session {sid} did not complete, so the recorded order "
            f"intents this replay is driven from are unknown: {exc}",
            session_id=sid,
        ) from exc

    # ── 3: the events, in the log's own order, each one checked and flattened ──
    events: List[Dict[str, Any]] = []
    arrivals: List[datetime] = []
    verified = 0
    unverified = 0
    for row_ in sorted(event_rows, key=lambda r: int(r.get("sequence") or 0)):
        event = _flat_event(row_)
        instant = _instant(row_.get("event_timestamp"))
        if instant is None:
            raise ReplayRefused(
                REFUSAL_EVENT_UNREADABLE,
                f"market event {row_.get('source_event_id')!r} of session {sid} carries no "
                f"readable event_timestamp. Every instant this replay records comes from the "
                f"event, so there is nothing to record for this one and no clock is read to "
                f"stand in for it (Requirement 15.4)",
                session_id=sid,
            )
        recomputed = _recomputed_identity(event)
        stored_identity = str(row_.get("source_event_id") or "")
        if recomputed is None:
            unverified += 1
        elif recomputed != stored_identity:
            raise ReplayRefused(
                REFUSAL_EVENT_IDENTITY_MISMATCH,
                f"market event {stored_identity!r} of session {sid} does not match the identity "
                f"its own payload computes ({recomputed!r}). The stored log is not the log the "
                f"session processed, so replaying it would report a history that did not happen "
                f"(Requirements 14.7, 15.5)",
                session_id=sid,
            )
        else:
            verified += 1
        event["event_timestamp"] = instant
        events.append(event)
        arrivals.append(_instant(row_.get("received_at")) or instant)

    # A monotone reading of the arrival column, so the bisect below is over a sorted sequence even
    # if two rows were stamped out of order. ``max`` and not a sort: the log's ``sequence`` order is
    # the order the session processed the events in, and re-sorting by a timestamp would replace it.
    reached: List[datetime] = list(accumulate(arrivals, max)) if arrivals else []

    # ── 4: the intents, oldest first, each assigned to the bar it was created on ──
    intents = sorted(order_rows, key=_order_sort_key)
    buckets: Dict[int, List[Mapping[str, Any]]] = {}
    tied = 0
    seen_keys: Set[Tuple[str, str]] = set()
    previous_created: Optional[str] = None
    for order_row in intents:
        created = _instant(order_row.get("created_at"))
        if created is None:
            raise ReplayRefused(
                REFUSAL_ORDER_UNREADABLE,
                f"recorded order {order_row.get('id')!r} of session {sid} carries no readable "
                f"created_at, so it cannot be placed against the market event the session priced "
                f"it from and the interleave of intents and events is unknown",
                session_id=sid,
            )
        created_text = str(order_row.get("created_at"))
        if created_text == previous_created:
            tied += 1
        previous_created = created_text
        index = bisect_right(reached, created) - 1
        buckets.setdefault(index, []).append(order_row)
        seen_keys.add((created_text, str(order_row.get("id"))))

    # ── 5: the fresh in-memory store ──
    ledger = ReferenceLedger(
        config=config.to_jsonb(),
        initial_balance=initial_balance,
        currency=currency,
        started_at=_instant(row.get("started_at")),
    )
    run = _Run(config=config, ledger=ledger)

    # Intents created before the first recorded event arrived. A market order among them has no
    # validated price and is rejected exactly as the session rejected it; a limit order rests.
    for order_row in buckets.get(-1, ()):
        _replay_intent(order_row, event=None, run=run)

    # ── 6: the per-event path, in ``step_session``'s order ──
    for index, event in enumerate(events):
        for order_row in buckets.get(index, ()):
            _replay_intent(order_row, event=event, run=run)
        _check_resting(event, run=run)
        _revalue(event, run=run)

    state = ledger.state()
    logger.info(
        "[paper-replay] session %s reconstructed from %d recorded market event(s) (%d identity "
        "verified, %d unverifiable) and %d recorded order intent(s); nothing was written",
        sid,
        len(events),
        verified,
        unverified,
        len(intents),
    )
    return Reconstruction(
        session_id=sid,
        user_id=uid,
        session_state=(
            None if row.get("session_state") is None else str(row["session_state"])
        ),
        currency=currency,
        initial_balance=ledger.money(initial_balance),
        config=config,
        events_replayed=len(events),
        events_verified=verified,
        events_unverified=unverified,
        intents_replayed=len(intents),
        tied_intents=tied,
        orders=tuple(
            run.orders[str(order_row["id"])]
            for order_row in intents
            if str(order_row["id"]) in run.orders
        ),
        fills=tuple(run.fills),
        balances={
            "available_balance": state["available_balance"],
            "locked_balance": state["locked_balance"],
            "total_equity": state["total_equity"],
        },
        positions={
            symbol: dict(position) for symbol, position in state["positions"].items()
        },
        realized_pnl=state["realized_pnl"],
        equity_series=tuple(dict(point) for point in state["equity_series"]),
        closed_trades=tuple(dict(trade) for trade in state["closed_trades"]),
        stale=bool(state["stale"]),
    )


def _replay_intent(
    order_row: Mapping[str, Any], *, event: Optional[Mapping[str, Any]], run: _Run
) -> None:
    """Replay one recorded order intent against the bar it was created on.

    ``paper_simulator.submit_intent``'s five steps, in its order, with its own functions doing
    every decision:

    1. static validation -> :func:`paper_simulator.static_rejection_reason`, one of Requirement
       16.5's eight names or ``None``;
    2. the reference price -> its limit for a limit order, ``reference_price(event, side)`` for a
       market one, and ``NO_VALIDATED_PRICE`` when the recorded event supplies none. Nothing is
       synthesised, interpolated or extrapolated (Requirement 14.9);
    3. the funds check against the ledger's ``available_balance`` -> ``INSUFFICIENT_FUNDS``;
    4. accept, and for a limit order lock Requirement 16.6's required funds and add it to the
       resting book;
    5. a market order fills immediately, at ``market_fill_price`` off that reference, with
       ``fee_amount``'s fee and ``slippage_amount``'s recorded cost, under the deterministic
       ``market_fill_event_id``.

    The idempotency probe is step 1 in production and is absent here for a reason that is a fact
    about the input rather than an omission: ``uq_paper_order_idem`` means the recorded log holds
    AT MOST ONE row per ``(session_id, idempotency_key)``, so a duplicate submission left no
    second intent to replay.
    """
    ledger = run.ledger
    config = run.config
    order_id = str(order_row["id"])
    created_at = _instant(order_row.get("created_at"))
    filled_at = None if event is None else event.get("event_timestamp")

    try:
        intent = sim.OrderIntent.from_mapping(
            {
                "symbol": order_row.get("symbol"),
                "side": order_row.get("side"),
                "order_type": order_row.get("order_type"),
                "quantity": order_row.get("quantity"),
                "limit_price": order_row.get("limit_price"),
                "idempotency_key": order_row.get("idempotency_key"),
                "signal_id": order_row.get("signal_id"),
            }
        )
    except sim.InvalidOrderIntent as exc:
        raise ReplayRefused(
            REFUSAL_ORDER_UNREADABLE,
            f"recorded order {order_id!r} cannot be read back as the intent it was: {exc}",
            session_id=str(order_row.get("session_id") or ""),
        ) from exc

    ref_order = ledger.submit(
        {
            "symbol": intent.symbol,
            "side": intent.side,
            "order_type": intent.order_type,
            "quantity": intent.quantity,
            "limit_price": intent.limit_price,
            # The recorded key is NOT carried into the ledger. ``ReferenceLedger.submit`` returns
            # an existing order for a repeated key without comparing the parameters, and two
            # recorded intents of one session cannot share a key anyway
            # (``uq_paper_order_idem``) - so passing it could only ever collapse two distinct
            # recorded orders into one.
            "created_at": created_at,
        }
    )
    run.ref_ids[order_id] = ref_order.order_id

    reason = sim.static_rejection_reason(intent, config)
    if reason is not None:
        ledger.reject(ref_order.order_id, reason)
        run.record(order_id, intent, created_at, None)
        return

    if intent.limit_price is not None:
        reference: Optional[Decimal] = ledger.price(intent.limit_price)
    else:
        derived = sim.reference_price(event, intent.side)
        reference = None if derived is None else ledger.price(derived)
    if reference is None or reference <= ZERO:
        ledger.reject(ref_order.order_id, sim.REJECTION_NO_VALIDATED_PRICE)
        run.record(order_id, intent, created_at, None)
        return

    required = ledger.required_funds(intent.quantity, reference)
    if required > ledger.available_balance:
        ledger.reject(ref_order.order_id, sim.REJECTION_INSUFFICIENT_FUNDS)
        run.record(order_id, intent, created_at, reference)
        return

    ledger.accept(ref_order.order_id)

    if intent.order_type == "limit":
        ledger.lock(required)
        run.book.append(_resting_row(order_id, intent, created_at))
        run.record(order_id, intent, created_at, reference)
        return

    price = sim.market_fill_price(reference, intent.side, config)
    fee = sim.fee_amount(intent.quantity, price, config)
    slippage = sim.slippage_amount(intent.quantity, price, reference, config)
    result = ledger.fill_order(
        ref_order.order_id,
        quantity=intent.quantity,
        price=price,
        fee=fee,
        fill_event_id=sim.market_fill_event_id(order_id),
        filled_at=filled_at,
    )
    if result.applied:
        run.add_fill(
            order_id,
            intent,
            result,
            reference,
            slippage,
            None if event is None else event.get("source_event_id"),
        )
    run.record(order_id, intent, created_at, reference)


def _check_resting(event: Mapping[str, Any], *, run: _Run) -> None:
    """Fill every resting limit order this recorded event triggers.

    ``paper_simulator.check_resting_orders``'s rule, through its own functions: the event's own
    symbol, oldest order first, :func:`paper_simulator.limit_fill_triggered` deciding whether this
    event is the one, :func:`paper_simulator.fillable_quantity` deciding how much (the
    deterministic participation cap, never a probability) and :func:`limit_fill_price` deciding
    the price - exactly the limit, so a resting fill's recorded slippage is zero.

    The release from ``locked_balance`` is the production rule: ``required_funds`` for the filled
    portion at the limit, clamped to what is actually locked, because the lock was quantized once
    for the whole quantity while the releases are quantized per fill and the parts need not sum to
    the whole.
    """
    ledger = run.ledger
    config = run.config
    symbol = event.get("symbol")
    instant = event.get("event_timestamp")
    candidates = [
        row
        for row in sorted(
            run.book, key=lambda r: (str(r.get("created_at") or ""), str(r["id"]))
        )
        if (symbol is None or row.get("symbol") == symbol)
        and sim.limit_fill_triggered(row, event)
    ]
    for row in candidates:
        quantity = sim.fillable_quantity(row, event, config)
        if quantity <= ZERO:
            continue
        price = sim.limit_fill_price(row, config)
        fee = sim.fee_amount(quantity, price, config)
        needed = ledger.required_funds(quantity, price)
        release = needed if needed < ledger.locked_balance else ledger.locked_balance
        order_id = str(row["id"])
        result = ledger.fill_order(
            run.ref_ids[order_id],
            quantity=quantity,
            price=price,
            fee=fee,
            fill_event_id=sim.resting_fill_event_id(order_id, event),
            filled_at=instant,
            release_from_locked=release,
        )
        if not result.applied:
            # A repeated event identity or a terminal order: unchanged, no snapshot and no fill
            # row (Requirements 16.3, 18.13). Recorded as nothing, which is what happened.
            continue
        row["filled_quantity"] = ledger.qty(
            to_decimal(row["filled_quantity"], "filled_quantity") + quantity
        )
        previous = run.orders[order_id]
        intent = sim.OrderIntent.from_mapping(
            {
                "symbol": previous.symbol,
                "side": previous.side,
                "order_type": previous.order_type,
                "quantity": previous.quantity,
                "limit_price": previous.limit_price,
                "idempotency_key": previous.idempotency_key,
            }
        )
        # Zero slippage, and not by omission: a resting order fills at exactly its limit, so the
        # reference IS the fill price and ``|price - reference|`` is zero (task 25.5).
        run.add_fill(
            order_id,
            intent,
            result,
            price,
            ledger.money(ZERO),
            event.get("source_event_id"),
        )
        run.record(order_id, intent, previous.created_at, previous.reference_price)


def _revalue(event: Mapping[str, Any], *, run: _Run) -> None:
    """Revalue the open book at this bar's close and record the equity point.

    ``paper_session_service._revalue``'s rule, including its refusals:

    * **A book with no open position is not revalued**, and no equity point is written. A
      revaluation restates what open positions are worth; with none there is nothing to restate,
      and a row per candle for an idle session would make Requirement 18.9's drawdown a function
      of how long the session ran rather than of what it did.
    * The price supplied is this bar's validated **close**, for the symbol it is for. A position in
      another symbol falls back to its own last validated price and the revaluation is reported
      ``stale`` (Requirement 18.15) - nothing is interpolated and nothing is zeroed.
    * A position that has never been priced at all is not revalued either. It cannot happen for a
      position this replay opened (every fill records its price), so it is logged as the surprise
      it would be rather than silently priced.
    """
    ledger = run.ledger
    if not any(position.is_open for position in ledger.positions.values()):
        return
    close = event.get("close")
    prices: Dict[str, Any] = {}
    if close is not None and event.get("symbol") is not None:
        prices[str(event["symbol"])] = to_decimal(close, "market event close")
    unpriced = [
        position.symbol
        for position in ledger.positions.values()
        if position.is_open
        and position.symbol not in prices
        and position.current_price is None
    ]
    if unpriced:
        logger.warning(
            "[paper-replay] the reconstruction holds an open position with no validated price, so "
            "this bar was not revalued and no price was substituted: %s",
            sorted(unpriced),
        )
        ledger.stale = True
        return
    ledger.revalue(prices, price_at=event.get("event_timestamp"))


def _projected(
    *,
    order_id: str,
    intent: Any,
    created_at: Optional[datetime],
    reference: Optional[Decimal],
    order: RefOrder,
    applied: Sequence[RefFill],
    slippage: Decimal,
    ledger: ReferenceLedger,
) -> ReplayedOrder:
    """One :class:`ReplayedOrder`, with the derived columns projected off the applied fills.

    ``filled_quantity``, ``avg_fill_price`` and ``fee_minor`` are recomputed from the fills
    applied to THIS order rather than incremented, which is the discipline
    ``paper_simulator._fill_event_totals`` records: the columns are a projection of the fill rows,
    so a drift between the two is detectable rather than built in. ``avg_fill_price`` is
    ``quantize(sum(qty x price) / sum(qty))``, the same expression the simulator writes to
    ``paper_orders.avg_fill_price``, and it is ``None`` for an order that never filled - not a
    zero, which would claim it filled at nothing.
    """
    total_quantity = ZERO
    notional = ZERO
    fee_total = ZERO
    for fill in applied:
        total_quantity += fill.quantity
        notional += fill.quantity * fill.price
        fee_total += fill.fee

    average: Optional[Decimal] = None
    if total_quantity > ZERO:
        with localcontext() as ctx:
            ctx.prec = PRECISION
            average = ledger.price(notional / total_quantity)

    return ReplayedOrder(
        order_id=order_id,
        symbol=intent.symbol,
        side=intent.side,
        order_type=intent.order_type,
        quantity=intent.quantity,
        limit_price=intent.limit_price,
        idempotency_key=intent.idempotency_key,
        created_at=created_at,
        reference_price=reference,
        filled_quantity=order.filled_quantity,
        avg_fill_price=average,
        fee_minor=_minor_of(ledger.money(fee_total), ledger.minor_unit_exponent),
        slippage_minor=_minor_of(ledger.money(slippage), ledger.minor_unit_exponent),
        order_state=order.order_state,
        rejection_reason=order.rejection_reason,
    )


__all__ = [
    # constants
    "PRECISION",
    "LONG",
    "SHORT",
    "SIDES",
    "BUY",
    "SELL",
    "WEIGHTED_AVERAGE",
    "ROUNDING_MODES",
    "DEFAULT_ROUNDING_MODE",
    "MARGIN_MARKET_TYPES",
    "CREATED",
    "ACCEPTED",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELLED",
    "REJECTED",
    "ORDER_TRANSITIONS",
    "TERMINAL_ORDER_STATES",
    "SESSION_START",
    "FILL",
    "FEE",
    "REVALUATION",
    "SESSION_STOP",
    "FRACTION_QUANTUM",
    # exceptions
    "ReferenceLedgerError",
    "ReferenceStalePrice",
    "ReferenceInvariantViolation",
    "ReferenceOverFill",
    "ReferenceBadValue",
    "ReferenceBadTransition",
    # value objects
    "RefPosition",
    "RefClosedTrade",
    "RefOrder",
    "RefFill",
    "RefDrawdown",
    # the ledger
    "ReferenceLedger",
    "to_decimal",
    # 27.5 - the deterministic replay
    "IDENTITY_PAYLOAD_FIELDS",
    "PaperReplayError",
    "REFUSAL_CAPITAL_UNREADABLE",
    "REFUSAL_CONFIG_INCOMPLETE",
    "REFUSAL_EVENT_IDENTITY_MISMATCH",
    "REFUSAL_EVENT_UNREADABLE",
    "REFUSAL_MARKET_LOG_UNREADABLE",
    "REFUSAL_ORDER_LOG_UNREADABLE",
    "REFUSAL_ORDER_UNREADABLE",
    "REFUSAL_REASONS",
    "REFUSAL_SESSION_UNREADABLE",
    "Reconstruction",
    "ReplayRefused",
    "ReplayedFill",
    "ReplayedOrder",
    "replay",
]
