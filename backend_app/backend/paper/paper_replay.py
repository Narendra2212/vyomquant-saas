"""
backend/paper/paper_replay.py - deterministic replay and the P-31 reference ledger.

Spec: marketplace-subscriptions-paper-trading task 9.2. ``design.md`` -> "``paper_replay.py``
# deterministic replay + the P-31 reference ledger". Requirements 15.4, 18.13, and the
accounting rules of Requirement 18 that this file re-derives (18.2 - 18.10, 18.12, 18.15).

This module currently contains :class:`ReferenceLedger` only. ``replay(session_id)`` -
the audit path of Requirements 15.4 and 15.5 that reconstructs a session from
``(paper_sessions.config, paper_market_events, the recorded order intents in
paper_orders)`` - is task 27.5 and lands at the bottom of this file, driving this same
ledger. Nothing here reads a clock, a database, a socket or ``random``, so it stays
importable from a property test with no fixture at all.

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

from dataclasses import dataclass, field
from datetime import datetime
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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

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
# ``replay(session_id)`` belongs here, below :class:`ReferenceLedger`, and is deliberately
# not implemented yet (task 9.2 covers the ledger only). It reconstructs a session from
# ``(paper_sessions.config, paper_market_events, the recorded order intents in
# paper_orders)`` against a fresh in-memory store - this ledger - and returns the final
# order states, fills, balances, positions, realized PnL and equity series. Every timestamp
# it records comes from the event payload rather than from ``now()``, which is what makes
# the reconstruction byte-identical to the original run.


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
]
