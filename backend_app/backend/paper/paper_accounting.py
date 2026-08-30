"""
backend/paper/paper_accounting.py - the Paper_Accounting_Engine.

Spec: marketplace-subscriptions-paper-trading task 9.1. ``design.md`` ->
"``paper/paper_accounting.py``". Requirements 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.7,
18.8, 18.9, 18.10, 18.12, 18.15.

Every monetary and quantity figure a Paper_Session reports is produced here, in exact
decimal arithmetic, from values passed in as arguments. ``paper_simulator`` owns the
transaction and the writes; this module owns the arithmetic and the invariants.

Exposes
-------
AccountingConfig            the frozen session config this module reads (Requirement 16.12)
Account, Position           the two value objects the engine transforms
ClosedTrade, FillResult     what ``apply_fill`` produces
Drawdown, SessionMetrics    what the metrics path produces
StalePrice                  no validated price for an open position's symbol (Req 18.15)
InvariantViolation          a Requirement 18.3 / 18.4 / 18.5 breach, naming the invariant
quantize_money              money -> the currency's Minor_Units scale (Requirement 18.2)
quantize_qty                quantity -> the symbol's quantity precision (Requirement 18.2)
position_value              one position's value under the retained SHORT convention
position_market_value       the sum over open positions (Requirement 18.3)
unrealized_pnl              open quantity at the latest validated price (Requirement 18.8)
total_equity                available + locked + position_market_value, always computed
violated_invariant          the name of the first breached invariant, or ``None``
invariants_hold             ``violated_invariant(...) is None`` (Requirements 18.3-18.5)
assert_invariants           raises ``InvariantViolation`` naming it (Requirement 18.14)
required_funds              notional + fee + slippage allowance (Requirement 16.6)
lock, unlock                available <-> locked, refusing a negative outcome (Req 18.4)
apply_fill                  the one ledger movement for a fill (Requirements 18.6, 18.7)
recalculate                 revalue positions and recompute equity (Requirements 18.3, 18.8)
last_validated_prices       the prices already recorded on the positions (Req 18.15)
latest_price_at             the newest ``price_at`` among them (Requirement 18.15)
max_drawdown                from persisted snapshots in order (Requirement 18.9)
win_rate                    in [0, 1], or ``None`` for an empty closed set (Req 18.10)
margin_usage                only where the market type supports it (Requirement 18.12)
compute_metrics             the ``paper_metrics`` row, absent fields omitted

WHY THIS MODULE IS PURE
-----------------------
The convention ``marketplace/money.py`` and ``paper/paper_order_state.py`` already set:
module import pulls only the standard library. No FastAPI, no database handle, no HTTP
client, no clock read, no ``random`` draw (``tests/test_paper_no_random.py`` AST-walks this
package and asserts the last two). Every timestamp this module records is one it was
handed, which is what makes a Paper_Session replayable byte-for-byte (Requirement 15.4)
and what lets P-25 ... P-31 drive the whole engine from Hypothesis with no fixture.

WHY THERE IS NO ``float`` IN THIS FILE
--------------------------------------
Requirement 18.1 forbids binary floating-point money and quantity arithmetic outright.
Every numeric value crossing this module's boundary is a ``Decimal``, and a ``float``
argument is refused rather than coerced: a ``float`` means the caller already lost
exactness upstream, and accepting it would launder that error into the ledger. Strings and
``int`` are accepted at the constructors, because ``Decimal('0.07')`` and ``Decimal(7)``
are exact while ``Decimal(0.07)`` is not.

Arithmetic runs inside an explicit ``localcontext(prec=34)``. 34 is the IEEE 754
decimal128 coefficient width and comfortably exceeds anything ``NUMERIC(28,10)`` can hold,
so a product of two persisted figures is exact before it is quantized. It is set
explicitly - and not left to the ambient context - so that a caller who lowered
``getcontext().prec`` cannot silently change what a Paper_Session reports.

WHY ``total_equity`` IS NEVER INCREMENTED
-----------------------------------------
Requirement 18.3 makes ``total_equity == available_balance + locked_balance +
position_market_value`` an exact equality with **zero** tolerance. The only way to keep
that from drifting is to never maintain the left-hand side independently: every function
here that returns an :class:`Account` sets ``total_equity`` from :func:`total_equity`,
which is the sum, and nothing anywhere adds a delta to it. :class:`Account` still carries
the field, because ``paper_accounts.total_equity`` is a persisted column that must be
round-trippable and must be *checkable* - :func:`violated_invariant` exists precisely to
catch a stored row whose ``total_equity`` no longer equals the sum, which is what
Requirement 18.14 asks to be detected and rolled back.

The existing ``paper_trading_service.verify_accounting_invariants`` compares with a
``Decimal("0.05")`` tolerance (line 188). That tolerance is not carried over. Zero
tolerance is what Requirement 18.3 says, and a tolerance is what let the existing
service's short-close path - which credits no cash at all - go unnoticed.

WHY THE ``SHORT`` VALUATION IS ``size * (2 * entry_price - price)``
------------------------------------------------------------------
It is the convention ``paper_trading_service._recalculate_account`` already uses (line
151) and it is retained deliberately, not reinvented. It is what makes the equity identity
hold for a short position without introducing a negative position value, and every figure
``Portfolio.jsx`` shows today is computed from it. Read as ``size * entry_price +
size * (entry_price - price)``, it is the cash the short consumed plus the profit it has
made, which is why a short opened at ``P`` is worth exactly ``size * P`` at the moment it
opens - the same as a long - and why one cash rule covers both sides.

WHY CASH IS THE RESIDUAL OF THE POSITION-VALUE CHANGE
-----------------------------------------------------
:func:`apply_fill` does not compute a cash movement per case (open, add, partial close,
full close, reversal) and then hope the identity survives. It computes the new position
from the fill, values the old and the new position **both at that fill's price**, and moves
cash by the negation of the difference, minus the fee:

    cash_delta = -(new_position_value - old_position_value) - fee

so the change in ``total_equity`` across any fill is exactly ``-fee``, by construction.
Requirement 18.6 (a zero-fee, zero-slippage fill changes equity by exactly zero) and
Requirement 18.7 (equity falls by exactly the recorded fees) are therefore properties of
the definition rather than of five separately-audited branches, and P-27 and P-28 confirm
the code says what this paragraph says rather than propping it up.

WHY A CLOSED POSITION IS ``Decimal('0')`` AND NOT A DELETED ROW
---------------------------------------------------------------
The existing service does ``del user_positions[symbol]`` when ``new_size <=
Decimal("0.00000001")`` (lines 407, 467). That loses the history and uses a tolerance where
Requirement 18.5 wants exactly zero. Here a fully closed position is returned with
``size = Decimal('0')`` and ``closed_at`` set, is never removed from the position map, and
is never compared against ``0.00000001``. ``position_market_value`` skips it because its
size is zero, not because it is absent.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* Any write. ``paper_repository`` owns ``paper_accounts``, ``paper_positions``,
  ``paper_balance_events``, ``paper_trades``, ``paper_equity_snapshots`` and
  ``paper_metrics``; ``paper_simulator`` owns the transaction the writes happen in, and the
  ``ASSERT accounting.invariants_hold`` that sits inside it (Requirement 18.14).
* Idempotency. Requirement 18.13's "a repeated ``fill_event_id`` changes nothing" is a
  ``uq_paper_fill_event`` lookup inside the caller's transaction, before it calls
  :func:`apply_fill` at all. A pure function cannot know it has been called twice.
* Equity snapshot insertion (Requirement 18.11) and its ``series_index``. This module
  computes the ``total_equity`` a snapshot records; the repository writes the row.
* Slippage and fee *rate* application to a fill price. ``paper_simulator`` owns the fill
  model; :func:`apply_fill` is handed the price and the fee that model produced.
* ``PaperError`` and its HTTP status. :class:`StalePrice` and :class:`InvariantViolation`
  are plain module-level exceptions so this module stays importable on its own; the
  service layer maps them onto ``PAPER_INVARIANT_VIOLATION`` and the stale read shape.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# ══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════

#: Working precision for every computation in this module. The IEEE 754 decimal128
#: coefficient width; ``NUMERIC(28,10)`` needs 28, so a product of two persisted figures
#: (up to 56 digits before quantization is irrelevant - the *significant* digits of a
#: realistic price times a realistic size stay well inside 34) is exact before rounding.
#: Set explicitly on every entry point so an ambient ``getcontext().prec`` cannot lower it.
DECIMAL_PRECISION = 34

#: The rounding modes ``config.rounding_mode`` may name (Requirement 18.2: *one* mode,
#: recorded in the session configuration, applied to every computation). The session
#: default is ``ROUND_HALF_EVEN``, which is what the existing paper trading service uses
#: at every ``quantize`` call and what ``design.md``'s frozen config records.
ROUNDING_MODES: frozenset = frozenset(
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

#: The default rounding mode, matching ``design.md``'s frozen ``config.rounding_mode``.
DEFAULT_ROUNDING_MODE = ROUND_HALF_EVEN

#: The two Paper_Position sides (Requirement 18.5: direction is an explicit side value,
#: never a negative size). ``chk`` on ``paper_positions.side`` enforces the same pair.
LONG = "LONG"
SHORT = "SHORT"
SIDES: frozenset = frozenset({LONG, SHORT})

#: The order sides an intent may carry, as ``config.supported_sides`` records them.
BUY = "buy"
SELL = "sell"

#: The one cost-basis convention Requirement 18.8 admits per session, and the only value
#: this module implements: ``new_entry = ((old_size * old_entry) + (qty * fill)) / new_size``
#: - the formula the existing service already applies (lines 433, 496). A session config
#: naming anything else is refused rather than silently treated as weighted average,
#: because Requirement 18.8 requires the recorded convention to be the applied one.
WEIGHTED_AVERAGE = "WEIGHTED_AVERAGE"
COST_BASES: frozenset = frozenset({WEIGHTED_AVERAGE})

#: Market shapes for which margin usage is a measurement rather than an invention
#: (Requirement 18.12). ``asset_universe.SUPPORTED_MARKET_TYPES`` and
#: ``strategy_dag.block_specs.MARKET_TYPES`` are both ``("spot", "swap", "future")``, and
#: of those three only the two derivative shapes are margined. A ``spot`` session reports
#: **no** margin figure - not zero, which would be a measurement that was never made.
MARGIN_MARKET_TYPES: frozenset = frozenset({"swap", "future"})

#: The scale ``paper_metrics.win_rate NUMERIC(6,5)`` stores. Quantizing here rather than
#: letting the database round means the reported win rate and the persisted win rate are
#: the same number.
WIN_RATE_QUANTUM = Decimal("0.00001")

#: The scale ``paper_metrics.max_drawdown_fraction`` is reported at. Both this and
#: :data:`WIN_RATE_QUANTUM` round a value already inside ``[0, 1]``, so quantization
#: cannot carry either bound (Requirements 18.9, 18.10, P-29, P-30).
FRACTION_QUANTUM = Decimal("0.00001")

_ZERO = Decimal("0")
_ONE = Decimal("1")
_TWO = Decimal("2")


# ══════════════════════════════════════════════════════════════════════════
# EXCEPTIONS
# ══════════════════════════════════════════════════════════════════════════


class PaperAccountingError(Exception):
    """Base class for every refusal this module makes."""


class StalePrice(PaperAccountingError):
    """No validated Paper_Market_Data price is available for an open position's symbol.

    Requirement 18.15: raised rather than substituting a synthesised, interpolated or zero
    price. The read path catches this, reports ``position_market_value``,
    ``unrealized_pnl`` and ``total_equity`` as ``stale: true`` against the last validated
    prices (:func:`last_validated_prices`) and sets ``last_price_at``
    (:func:`latest_price_at`). Carries ``symbol`` so the caller can name it.
    """

    def __init__(self, symbol: Any, message: Optional[str] = None) -> None:
        self.symbol = symbol
        super().__init__(
            message
            or f"no validated price for open position symbol {symbol!r}; "
            "a price is never synthesised, interpolated or defaulted to zero"
        )


class InvariantViolation(PaperAccountingError):
    """One of Requirement 18.3, 18.4 or 18.5 does not hold.

    ``invariant`` names it - ``'total_equity'``, ``'available_balance'``,
    ``'locked_balance'``, ``'position_size'`` or ``'position_side'`` - which is what
    Requirement 18.14's ``details.invariant`` reports after the transaction rolls back.
    """

    def __init__(self, invariant: str, message: str) -> None:
        self.invariant = invariant
        super().__init__(message)


class SnapshotOrderError(PaperAccountingError):
    """A persisted equity series was handed over out of timestamp order.

    Requirement 18.9 computes maximum drawdown from snapshots "taken in non-decreasing
    timestamp order". Sorting them here would paper over a repository that read them with
    the wrong ``ORDER BY``, and the drawdown of a resorted series is not the drawdown of
    the series that was read, so the series is refused instead.
    """


class InvalidConfiguration(PaperAccountingError):
    """The session configuration names a rounding mode or cost basis this module cannot apply."""


class InvalidQuantity(PaperAccountingError):
    """A quantity, price or amount argument is not an admissible exact decimal value."""


# ══════════════════════════════════════════════════════════════════════════
# COERCION - THE ONE PLACE A ``float`` IS REFUSED (Requirement 18.1)
# ══════════════════════════════════════════════════════════════════════════


def to_decimal(value: Any, what: str = "value") -> Decimal:
    """Return ``value`` as an exact finite :class:`~decimal.Decimal`.

    ``Decimal``, ``int`` and ``str`` are admitted, because each converts exactly:
    ``Decimal('0.07')`` and ``Decimal(7)`` are the numbers they read as. ``float`` is
    refused rather than coerced - ``0.07`` is not seven hundredths in binary, so a caller
    holding one has already done arithmetic this module cannot vouch for (Requirement 18.1).
    ``bool`` is refused for the adjacent reason: ``isinstance(True, int)`` is ``True``, so
    ``True`` would otherwise arrive as a quantity of one.

    Raises:
        InvalidQuantity: For a ``float``, a ``bool``, ``None``, a non-numeric string, or a
            ``NaN`` / infinity.
    """
    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, bool) or value is None:
        raise InvalidQuantity(
            f"{what} must be an exact decimal value, got {type(value).__name__}: {value!r}"
        )
    elif isinstance(value, float):
        raise InvalidQuantity(
            f"{what} must be an exact decimal value, not a float ({value!r}) - a binary "
            "floating-point value has already lost exactness by the time it reaches the "
            "Paper_Accounting_Engine (Requirement 18.1)"
        )
    elif isinstance(value, (int, str)):
        try:
            decimal_value = Decimal(value.strip() if isinstance(value, str) else value)
        except InvalidOperation as exc:
            raise InvalidQuantity(f"{what} is not a decimal number: {value!r}") from exc
    else:
        raise InvalidQuantity(
            f"{what} must be an exact decimal value, got {type(value).__name__}: {value!r}"
        )

    if not decimal_value.is_finite():
        raise InvalidQuantity(f"{what} is not a finite decimal number: {value!r}")
    return decimal_value


def _optional_decimal(value: Any, what: str) -> Optional[Decimal]:
    """:func:`to_decimal`, but ``None`` passes through as ``None``.

    Used for the position fields that are genuinely absent until a price has been
    validated: ``current_price``, ``unrealized_pnl``. An absent price is absent, and
    Requirement 18.15 forbids replacing it with a zero.
    """
    return None if value is None else to_decimal(value, what)


def _quantum(exponent: int) -> Decimal:
    """The quantum for a decimal exponent: ``2`` -> ``Decimal('0.01')``, ``0`` -> ``Decimal('1')``."""
    if isinstance(exponent, bool) or not isinstance(exponent, int):
        raise InvalidConfiguration(
            f"precision must be an int number of decimal places, got "
            f"{type(exponent).__name__}: {exponent!r}"
        )
    if exponent < 0:
        raise InvalidConfiguration(f"precision must be at or above zero, got {exponent}")
    return _ONE.scaleb(-exponent)


def _resolve_quantum(exponent: Any, what: str) -> Decimal:
    """Accept either a decimal-places ``int`` or an explicit quantum ``Decimal``.

    ``design.md`` writes ``quantize_money(x, exponent)`` with "e.g. ``Decimal('0.01')`` for
    USD", while ``config.minor_unit_exponent`` and ``config.quantity_precision`` are stored
    as integers. Both spellings mean the same scale, so both are admitted here rather than
    forcing every call site to convert.
    """
    if isinstance(exponent, Decimal):
        if not exponent.is_finite() or exponent <= 0:
            raise InvalidConfiguration(f"{what} quantum must be a positive finite Decimal")
        return exponent
    return _quantum(exponent)


def _resolve_rounding(rounding: Any) -> str:
    """Validate a rounding mode name against :data:`ROUNDING_MODES`.

    Requirement 18.2 requires *one* rounding mode, recorded in the session configuration
    and applied to every computation. An unrecognised name is refused rather than defaulted,
    because defaulting would mean the mode the session recorded is not the mode it used.
    """
    if rounding is None:
        raise InvalidConfiguration(
            "rounding mode must be read from the session config, not defaulted at the "
            "call site (Requirement 18.2)"
        )
    if rounding not in ROUNDING_MODES:
        raise InvalidConfiguration(
            f"unsupported rounding mode {rounding!r}; supported: {sorted(ROUNDING_MODES)}"
        )
    return str(rounding)


# ══════════════════════════════════════════════════════════════════════════
# QUANTIZATION (Requirement 18.2)
# ══════════════════════════════════════════════════════════════════════════


def quantize_money(value: Any, exponent: Any, rounding: str) -> Decimal:
    """Quantize a monetary value to the currency's Minor_Units scale.

    Args:
        value: The amount. ``Decimal``, ``int`` or exact decimal ``str``; a ``float`` is
            refused (Requirement 18.1).
        exponent: The currency's minor-unit exponent as an ``int`` (``2`` for USD) or the
            equivalent quantum (``Decimal('0.01')``).
        rounding: The mode from ``config.rounding_mode`` - see :data:`ROUNDING_MODES`. It is
            a required argument, not a defaulted one, so that no call site can quietly round
            differently from the mode the session recorded (Requirement 18.2).

    Returns:
        The amount at exactly that scale, e.g. ``Decimal('1.23')`` for USD.
    """
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        return to_decimal(value, "monetary value").quantize(
            _resolve_quantum(exponent, "money"), rounding=_resolve_rounding(rounding)
        )


def quantize_qty(value: Any, precision: Any, rounding: str) -> Decimal:
    """Quantize a quantity to the symbol's quantity precision.

    ``precision`` is ``config.quantity_precision``, read from the exchange market metadata
    for the session's symbol at start (``design.md``'s frozen config), so the scale compared
    against is a recorded value rather than a guess. Same ``rounding`` contract as
    :func:`quantize_money`.
    """
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        return to_decimal(value, "quantity").quantize(
            _resolve_quantum(precision, "quantity"), rounding=_resolve_rounding(rounding)
        )


# ══════════════════════════════════════════════════════════════════════════
# THE SESSION CONFIGURATION THIS MODULE READS (Requirement 16.12)
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AccountingConfig:
    """The subset of ``paper_sessions.config`` the accounting engine reads.

    Frozen, because ``trg_paper_session_config_immutable`` makes the persisted row frozen:
    a session's fee rate, rounding mode and cost basis are what they were at start, for the
    whole life of the session (Requirement 16.12). Constructing this from the stored JSONB
    is :meth:`from_session_config`'s job.

    Attributes:
        fee_rate: Fee as a fraction of notional, for :func:`required_funds`. The fee
            actually charged on a fill is computed by ``paper_simulator`` and handed to
            :func:`apply_fill`; this rate is only used to *reserve* funds up front.
        slippage_rate: Slippage allowance as a fraction of notional, same role.
        rounding_mode: The one mode of Requirement 18.2.
        cost_basis: The one convention of Requirement 18.8; only
            :data:`WEIGHTED_AVERAGE` is implemented.
        price_precision: Decimal places for a price.
        quantity_precision: Decimal places for a quantity.
        minor_unit_exponent: Decimal places for a monetary amount in the account's currency.
        market_type: The session's market shape (``'spot'``, ``'swap'``, ``'future'``).
            Determines whether a margin figure exists at all (Requirement 18.12). ``None``
            means unrecorded, which is treated as "not margined" - a margin figure is
            reported only where the market type is known to support it.
    """

    fee_rate: Decimal = Decimal("0")
    slippage_rate: Decimal = Decimal("0")
    rounding_mode: str = DEFAULT_ROUNDING_MODE
    cost_basis: str = WEIGHTED_AVERAGE
    price_precision: int = 2
    quantity_precision: int = 8
    minor_unit_exponent: int = 2
    market_type: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "fee_rate", to_decimal(self.fee_rate, "fee_rate"))
        object.__setattr__(
            self, "slippage_rate", to_decimal(self.slippage_rate, "slippage_rate")
        )
        if self.fee_rate < _ZERO:
            raise InvalidConfiguration(f"fee_rate must not be negative: {self.fee_rate}")
        if self.slippage_rate < _ZERO:
            raise InvalidConfiguration(
                f"slippage_rate must not be negative: {self.slippage_rate}"
            )
        object.__setattr__(self, "rounding_mode", _resolve_rounding(self.rounding_mode))
        if self.cost_basis not in COST_BASES:
            raise InvalidConfiguration(
                f"unsupported cost_basis {self.cost_basis!r}; supported: "
                f"{sorted(COST_BASES)}. Requirement 18.8 requires the recorded convention "
                "to be the applied one, so an unimplemented convention is refused rather "
                "than treated as weighted average."
            )
        # Validate each precision eagerly, so a bad config fails at construction rather
        # than on the first quantize halfway through a fill.
        _quantum(self.price_precision)
        _quantum(self.quantity_precision)
        _quantum(self.minor_unit_exponent)
        if self.market_type is not None:
            object.__setattr__(self, "market_type", str(self.market_type).strip().lower())

    # -- derived scales ---------------------------------------------------

    @property
    def money_quantum(self) -> Decimal:
        """The account currency's quantum, e.g. ``Decimal('0.01')`` for USD."""
        return _quantum(self.minor_unit_exponent)

    @property
    def qty_quantum(self) -> Decimal:
        """The symbol's quantity quantum, e.g. ``Decimal('0.00000001')``."""
        return _quantum(self.quantity_precision)

    @property
    def supports_margin(self) -> bool:
        """Whether a margin figure is a measurement for this market type (Req 18.12)."""
        return self.market_type in MARGIN_MARKET_TYPES

    # -- the two quantizers, bound to this session's recorded mode --------

    def money(self, value: Any) -> Decimal:
        """:func:`quantize_money` with this session's exponent and rounding mode."""
        return quantize_money(value, self.minor_unit_exponent, self.rounding_mode)

    def qty(self, value: Any) -> Decimal:
        """:func:`quantize_qty` with this session's precision and rounding mode."""
        return quantize_qty(value, self.quantity_precision, self.rounding_mode)

    def price(self, value: Any) -> Decimal:
        """Quantize to this session's price precision, using its recorded rounding mode."""
        return quantize_money(value, self.price_precision, self.rounding_mode)

    @classmethod
    def from_session_config(cls, config: Mapping[str, Any]) -> "AccountingConfig":
        """Build from the stored ``paper_sessions.config`` JSONB.

        Reads the eight keys this module needs and ignores the rest (the simulator path,
        the validated symbol list, the supported order types). Every numeric field is
        stored as a decimal *string* in that JSONB precisely so it survives a JSON
        round-trip exactly, and it is read back through :func:`to_decimal` here.

        Raises:
            InvalidConfiguration: If a required key is absent, or names a rounding mode or
                cost basis this module cannot apply. Nothing is defaulted silently: a
                session whose recorded configuration cannot be applied must not trade.
        """
        if not isinstance(config, Mapping):
            raise InvalidConfiguration(
                f"session config must be a mapping, got {type(config).__name__}"
            )

        def _require(key: str) -> Any:
            if key not in config or config[key] is None:
                raise InvalidConfiguration(
                    f"session config is missing {key!r}; the accounting engine will not "
                    "substitute a default for a value the session was meant to record"
                )
            return config[key]

        return cls(
            fee_rate=to_decimal(_require("fee_rate"), "config.fee_rate"),
            slippage_rate=to_decimal(_require("slippage_rate"), "config.slippage_rate"),
            rounding_mode=_require("rounding_mode"),
            cost_basis=_require("cost_basis"),
            price_precision=_require("price_precision"),
            quantity_precision=_require("quantity_precision"),
            minor_unit_exponent=_require("minor_unit_exponent"),
            market_type=config.get("market_type"),
        )


# ══════════════════════════════════════════════════════════════════════════
# THE VALUE OBJECTS
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Position:
    """One row of ``paper_positions``, as the engine sees it.

    Frozen: every transformation returns a new :class:`Position`, so a caller holding the
    pre-fill state after a refused fill still holds the pre-fill state. That is what makes
    Requirement 18.14's "roll back so the stored positions remain unchanged" hold in the
    process as well as in the database.

    ``size`` is at or above zero and direction lives in ``side`` (Requirement 18.5). A fully
    closed position has ``size == Decimal('0')`` and ``closed_at`` set; it is never deleted
    and never compared against a ``0.00000001`` tolerance.

    Attributes:
        symbol: The market symbol, e.g. ``'BTC/USDT'``.
        side: :data:`LONG` or :data:`SHORT`. Never inferred from a sign.
        size: Open quantity, ``>= 0``.
        entry_price: The weighted-average entry under ``config.cost_basis``.
        current_price: The latest validated price this position was revalued at, or ``None``
            when it has not been revalued yet. Never a synthesised value (Requirement 18.15).
        unrealized_pnl: Open quantity at ``current_price``, or ``None`` alongside it.
        price_at: The timestamp of ``current_price`` (Requirement 18.8's "recording the
            timestamp of the price used", Requirement 18.15's ``last_price_at``).
        opened_at: When the position first opened.
        closed_at: When ``size`` reached exactly zero, else ``None``.
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

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise InvalidQuantity(f"position symbol must be a non-empty str: {self.symbol!r}")
        if self.side not in SIDES:
            raise InvariantViolation(
                "position_side",
                f"position side must be one of {sorted(SIDES)}, got {self.side!r} "
                "(Requirement 18.5: direction is an explicit side, never a negative size)",
            )
        object.__setattr__(self, "size", to_decimal(self.size, "position size"))
        object.__setattr__(
            self, "entry_price", to_decimal(self.entry_price, "position entry_price")
        )
        object.__setattr__(
            self, "current_price", _optional_decimal(self.current_price, "current_price")
        )
        object.__setattr__(
            self, "unrealized_pnl", _optional_decimal(self.unrealized_pnl, "unrealized_pnl")
        )
        if self.size < _ZERO:
            raise InvariantViolation(
                "position_size",
                f"position size must be at or above zero, got {self.size} for "
                f"{self.symbol} (Requirement 18.5)",
            )

    @property
    def is_open(self) -> bool:
        """Whether the position holds quantity. Exact comparison against zero, no tolerance."""
        return self.size > _ZERO

    @property
    def is_closed(self) -> bool:
        """Whether the position is fully closed: exactly zero size (Requirement 18.5)."""
        return self.size == _ZERO


@dataclass(frozen=True)
class Account:
    """One row of ``paper_accounts``, as the engine sees it.

    ``total_equity`` is carried as a field because the column is persisted and must be
    round-trippable *and* checkable - :func:`violated_invariant` exists to catch a stored
    row whose ``total_equity`` no longer equals ``available_balance + locked_balance +
    position_market_value``. No function in this module ever adds a delta to it; every
    function that returns an :class:`Account` sets it from :func:`total_equity`, the
    computed sum (Requirement 18.3).

    Attributes:
        available_balance: Unencumbered cash, ``>= 0`` (Requirement 18.4).
        locked_balance: Cash reserved against resting orders, ``>= 0`` (Requirement 18.4).
        realized_pnl: Cumulative realized profit and loss, from closed quantity at recorded
            fill prices only (Requirement 18.8). Signed, and excludes fees - a fee is a cost
            recorded on the fill and on ``paper_trades.fee_minor``, not a component of PnL.
        total_equity: The computed sum. Never an independently maintained running total.
        currency: The account currency, for the Minor_Units scale the caller quantizes at.
    """

    available_balance: Decimal
    locked_balance: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    total_equity: Decimal = Decimal("0")
    currency: str = "USD"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "available_balance", to_decimal(self.available_balance, "available_balance")
        )
        object.__setattr__(
            self, "locked_balance", to_decimal(self.locked_balance, "locked_balance")
        )
        object.__setattr__(self, "realized_pnl", to_decimal(self.realized_pnl, "realized_pnl"))
        object.__setattr__(self, "total_equity", to_decimal(self.total_equity, "total_equity"))

    @property
    def cash(self) -> Decimal:
        """``available_balance + locked_balance`` - the cash side of the equity identity."""
        with localcontext() as ctx:
            ctx.prec = DECIMAL_PRECISION
            return self.available_balance + self.locked_balance


@dataclass(frozen=True)
class ClosedTrade:
    """One row of ``paper_trades`` - a round trip whose position reached exactly zero.

    Requirement 18.10 treats a trade as closed when its position quantity reaches zero, and
    ``paper_trades`` carries a row only then. ``realized_pnl`` is the PnL of the closing
    quantity at the recorded fill prices; ``fee`` is recorded beside it rather than netted
    into it, so :func:`win_rate` counts a trade as winning on its trading result
    (Requirement 18.8's "only from closed quantity at recorded fill prices").
    """

    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    realized_pnl: Decimal
    fee: Decimal = Decimal("0")
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", to_decimal(self.quantity, "trade quantity"))
        object.__setattr__(self, "entry_price", to_decimal(self.entry_price, "entry_price"))
        object.__setattr__(self, "exit_price", to_decimal(self.exit_price, "exit_price"))
        object.__setattr__(self, "realized_pnl", to_decimal(self.realized_pnl, "realized_pnl"))
        object.__setattr__(self, "fee", to_decimal(self.fee, "fee"))

    @property
    def is_win(self) -> bool:
        """Requirement 18.10's "realized profit strictly greater than zero"."""
        return self.realized_pnl > _ZERO


@dataclass(frozen=True)
class Valuation:
    """What :func:`recalculate` produces: the revalued account, positions and totals."""

    account: Account
    positions: Mapping[str, Position]
    position_market_value: Decimal
    unrealized_pnl: Decimal
    price_at: Optional[datetime] = None


@dataclass(frozen=True)
class FillResult:
    """What :func:`apply_fill` produces.

    Carries both the new state and the deltas, because the caller writes both: the new
    balances go to ``paper_accounts`` and the position to ``paper_positions``, while the
    deltas are exactly the ``available_delta`` / ``locked_delta`` / ``realized_delta``
    columns of ``paper_balance_events`` (Requirement 26.3's per-balance-change record).

    ``total_equity_delta`` is ``-fee``, always, by the construction described in the module
    docstring: Requirement 18.6 for a zero-fee fill and Requirement 18.7 for a charged one
    are the same identity read at two values of ``fee``.
    """

    account: Account
    positions: Mapping[str, Position]
    position: Position
    realized_pnl: Decimal
    fee: Decimal
    available_delta: Decimal
    locked_delta: Decimal
    realized_delta: Decimal
    total_equity_delta: Decimal
    position_market_value: Decimal
    closed_trade: Optional[ClosedTrade] = None


@dataclass(frozen=True)
class Drawdown:
    """Maximum drawdown, both ways Requirement 18.9 asks for it.

    ``amount`` is an exact ``Decimal >= 0``; ``fraction`` is that amount over the running
    peak at the trough, in ``[0, 1]``. ``fraction`` is ``Decimal('0')`` when the peak at the
    trough is not positive, because a fraction of a non-positive peak is not a measurement.
    """

    amount: Decimal
    fraction: Decimal


@dataclass(frozen=True)
class SessionMetrics:
    """The ``paper_metrics`` row. Absent figures are ``None``, never a zero.

    ``win_rate`` is ``None`` while the closed-trade count is zero (Requirement 18.10) and
    ``margin_usage`` is ``None`` where the market type does not support margin
    (Requirement 18.12). ``chk_paper_win_rate`` and the nullable column exist for exactly
    that reason, so :meth:`to_row` omits an absent key rather than emitting ``0``.
    """

    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_equity: Decimal
    position_market_value: Decimal
    max_drawdown_amount: Decimal
    max_drawdown_fraction: Decimal
    closed_trade_count: int
    total_return_pct: Optional[Decimal] = None
    win_rate: Optional[Decimal] = None
    margin_usage: Optional[Decimal] = None
    stale: bool = False
    price_at: Optional[datetime] = None

    def to_row(self) -> Dict[str, Any]:
        """The persistable mapping, with every absent figure **omitted**.

        Omission, not ``None`` and not ``0``: Requirement 18.10 wants an empty closed-trade
        set reported as absent, and Requirement 18.12 wants an unmargined market to report
        no margin figure "rather than a zero presented as a measurement".
        """
        row: Dict[str, Any] = {
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "total_equity": self.total_equity,
            "position_market_value": self.position_market_value,
            "max_drawdown_amount": self.max_drawdown_amount,
            "max_drawdown_fraction": self.max_drawdown_fraction,
            "closed_trade_count": self.closed_trade_count,
            "stale": self.stale,
        }
        if self.total_return_pct is not None:
            row["total_return_pct"] = self.total_return_pct
        if self.win_rate is not None:
            row["win_rate"] = self.win_rate
        if self.margin_usage is not None:
            row["margin_usage"] = self.margin_usage
        if self.price_at is not None:
            row["price_at"] = self.price_at
        return row


# ══════════════════════════════════════════════════════════════════════════
# VALUATION (Requirements 18.3, 18.8, 18.15)
# ══════════════════════════════════════════════════════════════════════════


def _price_for(symbol: str, prices: Mapping[str, Any]) -> Decimal:
    """The validated price for ``symbol``, or :class:`StalePrice`.

    An absent key and a ``None`` value mean the same thing - no validated Paper_Market_Data
    price exists - and both raise. Requirement 18.15 forbids substituting a synthesised,
    interpolated or zero price, so there is no ``.get(symbol, 0)`` anywhere in this module.
    """
    if not isinstance(prices, Mapping) or symbol not in prices:
        raise StalePrice(symbol)
    price = prices[symbol]
    if price is None:
        raise StalePrice(symbol)
    return to_decimal(price, f"price for {symbol}")


def position_value(position: Position, price: Any) -> Decimal:
    """One position's market value at ``price``, exact.

    ``LONG``  -> ``size * price``
    ``SHORT`` -> ``size * (2 * entry_price - price)``

    The ``SHORT`` convention is the one ``paper_trading_service._recalculate_account`` line
    151 already uses, retained rather than replaced - see the module docstring for why. Read
    as ``size * entry_price + size * (entry_price - price)``, it is the cash the short
    consumed plus the profit it has made, which is what keeps the equity identity holding for
    a short without introducing a negative position value.

    A closed position (``size == 0``) is worth exactly zero under both branches, so it needs
    no special case and no tolerance.
    """
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        current = to_decimal(price, f"price for {position.symbol}")
        if position.side == LONG:
            return position.size * current
        return position.size * (_TWO * position.entry_price - current)


def position_unrealized_pnl(position: Position, price: Any) -> Decimal:
    """One position's unrealized PnL at ``price``, exact.

    ``LONG`` -> ``(price - entry_price) * size``; ``SHORT`` -> ``(entry_price - price) * size``.
    Open quantity only, at the latest validated price (Requirement 18.8). Note the algebraic
    tie to :func:`position_value`: a ``SHORT``'s value is ``size * entry_price`` plus this,
    and a ``LONG``'s is ``size * entry_price`` plus this too.
    """
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        current = to_decimal(price, f"price for {position.symbol}")
        if position.side == LONG:
            return (current - position.entry_price) * position.size
        return (position.entry_price - current) * position.size


def _positions_of(positions: Any) -> List[Position]:
    """Normalise a position map or a position iterable to a list of :class:`Position`."""
    if isinstance(positions, Mapping):
        return list(positions.values())
    return list(positions)


def position_market_value(
    positions: Any,
    prices: Mapping[str, Any],
    config: Optional[AccountingConfig] = None,
) -> Decimal:
    """The sum over the session's **open** positions of their value at the given prices.

    ``design.md``'s pseudocode, one for one: iterate positions with ``size > 0``, look the
    price up, raise :class:`StalePrice` when it is absent, and add ``size * price`` for a
    long or ``size * (2 * entry_price - price)`` for a short.

    Args:
        positions: A ``{symbol: Position}`` mapping or any iterable of positions. Closed
            positions (``size == 0``) are skipped because they are worth zero, not because
            they are absent - they are never deleted (Requirement 18.5).
        prices: ``{symbol: Decimal}`` of latest validated prices. A symbol with an open
            position and no entry here raises.
        config: When given, **each position's value is quantized to the account currency's
            Minor_Units scale before it is summed** (Requirement 18.2), so the total is
            itself at that scale. Quantizing per position rather than the sum is what keeps
            Requirement 18.6 exact: :func:`apply_fill` moves cash by one position's quantized
            value change, and a sum-then-quantize would let a rounding remainder from an
            untouched position leak into that movement. Pass ``None`` for the exact,
            unquantized sum - and then compare against unquantized figures only.

    Returns:
        The total. ``Decimal('0')`` for no open positions.

    Raises:
        StalePrice: If any open position's symbol has no validated price (Requirement 18.15).
    """
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        total = _ZERO
        for position in _positions_of(positions):
            if not position.is_open:
                continue
            value = position_value(position, _price_for(position.symbol, prices))
            total += config.money(value) if config is not None else value
        return total


def unrealized_pnl(
    positions: Any,
    prices: Mapping[str, Any],
    config: Optional[AccountingConfig] = None,
) -> Decimal:
    """Unrealized PnL across open positions, from open quantity at the latest validated price.

    Requirement 18.8's second half. Same ``config`` and :class:`StalePrice` contract as
    :func:`position_market_value`; the timestamp of the price used is recorded on each
    position's ``price_at`` by :func:`recalculate`.
    """
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        total = _ZERO
        for position in _positions_of(positions):
            if not position.is_open:
                continue
            pnl = position_unrealized_pnl(position, _price_for(position.symbol, prices))
            total += config.money(pnl) if config is not None else pnl
        return total


def total_equity(
    account: Account,
    positions: Any,
    prices: Mapping[str, Any],
    config: Optional[AccountingConfig] = None,
) -> Decimal:
    """``available_balance + locked_balance + position_market_value`` - the computed sum.

    This is the **only** producer of a ``total_equity`` figure in the codebase's paper path.
    Requirement 18.3's identity cannot drift from a value that is never maintained
    independently, and every function here that returns an :class:`Account` sets the field
    from this function.

    Raises:
        StalePrice: If any open position's symbol has no validated price. The caller reports
            ``total_equity`` as stale against the last validated prices rather than
            substituting one (Requirement 18.15).
    """
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        return (
            account.available_balance
            + account.locked_balance
            + position_market_value(positions, prices, config)
        )


def violated_invariant(
    account: Account,
    positions: Any,
    prices: Mapping[str, Any],
    config: Optional[AccountingConfig] = None,
) -> Optional[str]:
    """The name of the first breached invariant, or ``None`` when all of them hold.

    The five invariants, in the order Requirement 18.3, 18.4 and 18.5 state them:

    * ``'total_equity'``     - ``total_equity == available + locked + position_market_value``,
      **exact** equality, zero tolerance. The existing service's ``Decimal("0.05")``
      tolerance (``verify_accounting_invariants``, line 188) is deliberately not carried over.
    * ``'available_balance'`` - ``>= 0``
    * ``'locked_balance'``    - ``>= 0``
    * ``'position_size'``     - every ``size >= 0``
    * ``'position_side'``     - every ``side in ('LONG', 'SHORT')``

    The name is what Requirement 18.14's ``details.invariant`` reports, which is why this
    returns a name rather than a bare ``False``.

    Raises:
        StalePrice: If an open position has no validated price. A stale valuation is not an
            invariant breach - it is a missing measurement, and Requirement 18.15 has the
            caller report it as such rather than concluding the ledger is broken.
    """
    all_positions = _positions_of(positions)

    for position in all_positions:
        # Checked before the equity identity so that a malformed position is reported as a
        # malformed position rather than as an equity mismatch it happens to also cause.
        if position.side not in SIDES:
            return "position_side"
        if position.size < _ZERO:
            return "position_size"

    if account.available_balance < _ZERO:
        return "available_balance"
    if account.locked_balance < _ZERO:
        return "locked_balance"

    expected = total_equity(account, all_positions, prices, config)
    if account.total_equity != expected:
        return "total_equity"
    return None


def invariants_hold(
    account: Account,
    positions: Any,
    prices: Mapping[str, Any],
    config: Optional[AccountingConfig] = None,
) -> bool:
    """Whether every Requirement 18.3 / 18.4 / 18.5 invariant holds.

    ``design.md``'s ``invariants_hold(account, positions, prices)``. Equality is exact with
    zero tolerance; see :func:`violated_invariant` for the list and for the ``config``
    contract (pass the session config whenever the stored figures were quantized at its
    scale, which they are on every path that went through this module).
    """
    return violated_invariant(account, positions, prices, config) is None


def assert_invariants(
    account: Account,
    positions: Any,
    prices: Mapping[str, Any],
    config: Optional[AccountingConfig] = None,
) -> None:
    """Raise :class:`InvariantViolation` naming the breach, or return ``None``.

    The form ``paper_simulator`` calls inside its transaction (``ASSERT
    accounting.invariants_hold``): Requirement 18.14 wants the operation rejected within the
    same transaction and an error that *names* the violated invariant, which a boolean
    cannot carry.
    """
    invariant = violated_invariant(account, positions, prices, config)
    if invariant is None:
        return
    if invariant == "total_equity":
        expected = total_equity(account, positions, prices, config)
        raise InvariantViolation(
            invariant,
            f"total_equity {account.total_equity} != available_balance "
            f"{account.available_balance} + locked_balance {account.locked_balance} + "
            f"position_market_value "
            f"{position_market_value(positions, prices, config)} (= {expected}); "
            "Requirement 18.3 admits zero tolerance",
        )
    if invariant == "available_balance":
        raise InvariantViolation(
            invariant,
            f"available_balance must be at or above zero, got {account.available_balance} "
            "(Requirement 18.4)",
        )
    if invariant == "locked_balance":
        raise InvariantViolation(
            invariant,
            f"locked_balance must be at or above zero, got {account.locked_balance} "
            "(Requirement 18.4)",
        )
    offenders = [
        p.symbol
        for p in _positions_of(positions)
        if (p.size < _ZERO if invariant == "position_size" else p.side not in SIDES)
    ]
    raise InvariantViolation(
        invariant,
        f"Requirement 18.5 breached by position(s) {offenders}",
    )


def last_validated_prices(positions: Any) -> Dict[str, Decimal]:
    """The prices already recorded on the positions: ``{symbol: current_price}``.

    Requirement 18.15's read path needs to report ``position_market_value``,
    ``unrealized_pnl`` and ``total_equity`` "against the last validated prices rather than
    against a fresh value that does not exist". This is that price map, built only from
    prices the session already validated and recorded - a position with no ``current_price``
    contributes no entry, so :func:`position_market_value` still raises :class:`StalePrice`
    for it rather than valuing it at zero.
    """
    return {
        p.symbol: p.current_price
        for p in _positions_of(positions)
        if p.current_price is not None
    }


def latest_price_at(positions: Any) -> Optional[datetime]:
    """The newest ``price_at`` among the positions, for Requirement 18.15's ``last_price_at``.

    ``None`` when no position has been revalued yet, which is the honest answer: there is no
    last validated price to report the timestamp of.
    """
    stamps = [p.price_at for p in _positions_of(positions) if p.price_at is not None]
    return max(stamps) if stamps else None


# ══════════════════════════════════════════════════════════════════════════
# FUNDS: required_funds, lock, unlock (Requirements 16.6, 18.4)
# ══════════════════════════════════════════════════════════════════════════


def _attr(source: Any, name: str, what: str) -> Any:
    """Read ``name`` off a mapping or an object, refusing absence rather than defaulting."""
    if isinstance(source, Mapping):
        if name not in source:
            raise InvalidQuantity(f"{what} carries no {name!r}")
        return source[name]
    try:
        return getattr(source, name)
    except AttributeError as exc:
        raise InvalidQuantity(f"{what} carries no {name!r}") from exc


def required_funds(intent: Any, reference_price: Any, config: AccountingConfig) -> Decimal:
    """The cash an order intent must have available before it is accepted.

    ``notional + fee allowance + slippage allowance``, where ``notional`` is
    ``quantity * reference_price`` and the two allowances are that notional times
    ``config.fee_rate`` and ``config.slippage_rate``. The slippage allowance is included
    because a market order fills at an adversely slipped price, so reserving only the
    notional would let an accepted order overdraw ``available_balance`` at fill time and
    breach Requirement 18.4.

    The sum is quantized once, at the end, to the account currency's Minor_Units scale using
    the session's recorded rounding mode (Requirement 18.2). Quantizing the three terms
    separately would make the reserved amount depend on how the expression was grouped.

    Args:
        intent: The order intent, a mapping or an object exposing ``quantity``.
        reference_price: The limit price, or the latest validated price for a market order.
            Never a synthesised one - ``paper_simulator`` rejects the order with
            ``NO_VALIDATED_PRICE`` before it reaches here (Requirement 14.9).
        config: The session's frozen configuration.

    Returns:
        The required cash, at the Minor_Units scale.
    """
    quantity = to_decimal(_attr(intent, "quantity", "order intent"), "intent quantity")
    price = to_decimal(reference_price, "reference_price")
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        notional = quantity * price
        return config.money(notional * (_ONE + config.fee_rate + config.slippage_rate))


def lock(account: Account, amount: Any, config: AccountingConfig) -> Account:
    """Move ``amount`` from ``available_balance`` to ``locked_balance``.

    What ``paper_simulator`` calls when it accepts a resting limit order: the funds stop
    being spendable but do not leave the account. ``available + locked`` is unchanged, so the
    computed ``total_equity`` is unchanged too - which is why this function does not
    recompute it and does not need the prices to do so. That is a property of the identity,
    not a shortcut around it.

    Raises:
        InvariantViolation: If ``amount`` exceeds ``available_balance``. Requirement 18.4 is
            enforced *before* the move rather than detected after it, so a refused lock
            leaves the account object untouched.
        InvalidQuantity: If ``amount`` is negative. Releasing funds is :func:`unlock`'s job;
            a negative lock would be an unlock spelled in a way no reader expects.
    """
    quantized = config.money(amount)
    if quantized < _ZERO:
        raise InvalidQuantity(f"lock amount must not be negative: {quantized}")
    if quantized > account.available_balance:
        raise InvariantViolation(
            "available_balance",
            f"locking {quantized} would drive available_balance below zero from "
            f"{account.available_balance} (Requirement 18.4)",
        )
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        return replace(
            account,
            available_balance=account.available_balance - quantized,
            locked_balance=account.locked_balance + quantized,
        )


def unlock(account: Account, amount: Any, config: AccountingConfig) -> Account:
    """Move ``amount`` from ``locked_balance`` back to ``available_balance``.

    The symmetric partner of :func:`lock`, called when a resting order is cancelled or
    rejected (``paper_balance_events.cause = 'ORDER_UNLOCK'``). Without it ``locked_balance``
    could only ever grow, and Requirement 18.4's ``locked_balance >= 0`` would be trivially
    true for the wrong reason.

    Raises:
        InvariantViolation: If ``amount`` exceeds ``locked_balance``.
        InvalidQuantity: If ``amount`` is negative.
    """
    quantized = config.money(amount)
    if quantized < _ZERO:
        raise InvalidQuantity(f"unlock amount must not be negative: {quantized}")
    if quantized > account.locked_balance:
        raise InvariantViolation(
            "locked_balance",
            f"unlocking {quantized} would drive locked_balance below zero from "
            f"{account.locked_balance} (Requirement 18.4)",
        )
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        return replace(
            account,
            available_balance=account.available_balance + quantized,
            locked_balance=account.locked_balance - quantized,
        )


# ══════════════════════════════════════════════════════════════════════════
# THE FILL (Requirements 18.5, 18.6, 18.7, 18.8)
# ══════════════════════════════════════════════════════════════════════════


def _side_for_order(order_side: Any) -> str:
    """``'buy'`` -> :data:`LONG`, ``'sell'`` -> :data:`SHORT`.

    The direction the fill *adds*. A ``buy`` against an open short closes it rather than
    opening a long, which :func:`apply_fill` works out from the existing position - this
    function only translates the vocabulary.
    """
    if not isinstance(order_side, str):
        raise InvalidQuantity(f"order side must be a str, got {type(order_side).__name__}")
    normalised = order_side.strip().lower()
    if normalised == BUY:
        return LONG
    if normalised == SELL:
        return SHORT
    raise InvalidQuantity(
        f"order side must be {BUY!r} or {SELL!r}, got {order_side!r}"
    )


def _weighted_average_entry(
    old_size: Decimal,
    old_entry: Decimal,
    added_size: Decimal,
    fill_price: Decimal,
    config: AccountingConfig,
) -> Decimal:
    """``((old_size * old_entry) + (added_size * fill_price)) / new_size``.

    Requirement 18.8's one cost-basis convention, ``config.cost_basis ==
    'WEIGHTED_AVERAGE'``, and the formula the existing service already applies (lines 433 and
    496). Quantized to the session's price precision, because the result is persisted as
    ``paper_positions.entry_price`` and every later valuation reads it back.

    Note that quantizing here cannot disturb the equity identity: :func:`apply_fill` values
    the resulting position *after* this rounding, and moves cash by the residual, so the
    rounding lands in cash rather than in a mismatch.
    """
    if config.cost_basis != WEIGHTED_AVERAGE:  # pragma: no cover - refused at construction
        raise InvalidConfiguration(f"unsupported cost_basis {config.cost_basis!r}")
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        new_size = old_size + added_size
        if new_size <= _ZERO:
            raise InvalidQuantity(
                "weighted-average entry is undefined for a non-positive resulting size"
            )
        return config.price(((old_size * old_entry) + (added_size * fill_price)) / new_size)


def _realized_on_close(
    closing_side: str, entry_price: Decimal, exit_price: Decimal, quantity: Decimal
) -> Decimal:
    """PnL of ``quantity`` closed out of a ``closing_side`` position, exact.

    ``LONG`` -> ``(exit - entry) * quantity``; ``SHORT`` -> ``(entry - exit) * quantity``.
    Closed quantity at recorded fill prices, and nothing else - no fee is netted in, because
    Requirement 18.8 says "only from closed quantity at recorded fill prices" and
    ``paper_trades`` carries ``fee_minor`` as its own column. The existing service subtracts
    the fee from ``realized_pnl`` (lines 402, 458); that conflation is deliberately not
    carried over, since it would make :func:`win_rate` a measure of trading result *and*
    fee schedule at once.
    """
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        if closing_side == LONG:
            return (exit_price - entry_price) * quantity
        return (entry_price - exit_price) * quantity


def apply_fill(
    account: Account,
    positions: Mapping[str, Position],
    order: Any,
    quantity: Any,
    price: Any,
    fee: Any,
    config: AccountingConfig,
    *,
    filled_at: Optional[datetime] = None,
    release_from_locked: Any = _ZERO,
) -> FillResult:
    """Apply one fill to the ledger and return the new state and the deltas.

    The single arithmetic path for every fill - opening, adding, partially closing, fully
    closing and reversing through zero - because cash is derived from the position-value
    change rather than computed case by case:

        cash_delta = -(new_position_value - old_position_value) - fee

    with both values taken **at this fill's price**, each quantized to the account currency's
    Minor_Units scale. The change in ``total_equity`` across the fill is therefore exactly
    ``-fee``: Requirement 18.6 (zero fee, zero slippage, equity changes by exactly zero) and
    Requirement 18.7 (equity falls by exactly the recorded fees) are the same identity read
    at two values of ``fee``, and neither depends on which of the five position cases ran.

    The five cases, all of which only decide *what the new position is*:

    ==============================  ==================================================
    No open position                open ``quantity`` at ``price`` on the fill's side
    Same side as the open position  accumulate, entry by ``config.cost_basis``
    Opposite side, ``qty <= size``  close ``quantity``; realized PnL recorded
    Opposite side, ``qty == size``  close fully: ``size = 0``, ``closed_at`` set
    Opposite side, ``qty > size``   close ``size``, then open ``qty - size`` reversed
    ==============================  ==================================================

    A fully closed position is returned with ``size = Decimal('0')`` and ``closed_at`` set,
    and stays in the returned mapping. It is never deleted and never compared against a
    ``0.00000001`` tolerance (Requirement 18.5), unlike the ``del user_positions[symbol]``
    the existing service performs at lines 407 and 467.

    Args:
        account: The account as read inside the caller's transaction.
        positions: ``{symbol: Position}`` for the account, closed positions included.
        order: The order being filled; a mapping or object exposing ``symbol`` and ``side``
            (``'buy'`` / ``'sell'``).
        quantity: The filled quantity, strictly positive.
        price: The fill price, strictly positive.
        fee: The fee the fill model computed, at or above zero. It is quantized to the
            Minor_Units scale here, so the amount deducted is the amount reportable.
        config: The session's frozen configuration.
        filled_at: The fill timestamp, used for ``opened_at`` / ``closed_at`` / ``price_at``.
            Passed in, never read from a clock - that is what keeps replay byte-identical
            (Requirement 15.4).
        release_from_locked: Cash previously locked for this order by :func:`lock` that this
            fill consumes. It is returned to ``available_balance`` before ``cash_delta`` is
            applied, so a limit order's reservation is released and then spent rather than
            spent twice. Defaults to zero, which is the market-order path.

    Returns:
        A :class:`FillResult`. Its ``account.total_equity`` is the computed sum at this
        fill's price for the filled symbol and at each other open position's last validated
        price, so :func:`invariants_hold` holds on the returned state.

    Raises:
        InvalidQuantity: For a non-positive quantity or price, a negative fee, or a
            ``float`` anywhere (Requirement 18.1).
        InvariantViolation: If the resulting ``available_balance`` or ``locked_balance``
            would be negative (Requirement 18.4). Nothing is mutated, so the caller's
            rollback has nothing to undo in memory either (Requirement 18.14).
        StalePrice: If another open position has no recorded price to value it at.
    """
    symbol = _attr(order, "symbol", "order")
    order_side = _attr(order, "side", "order")
    incoming_side = _side_for_order(order_side)

    fill_qty = config.qty(quantity)
    fill_price = config.price(price)
    fill_fee = config.money(fee)
    release = config.money(release_from_locked)

    if fill_qty <= _ZERO:
        raise InvalidQuantity(f"fill quantity must be strictly positive, got {fill_qty}")
    if fill_price <= _ZERO:
        raise InvalidQuantity(f"fill price must be strictly positive, got {fill_price}")
    if fill_fee < _ZERO:
        raise InvalidQuantity(f"fill fee must not be negative, got {fill_fee}")
    if release < _ZERO:
        raise InvalidQuantity(f"release_from_locked must not be negative, got {release}")

    existing = positions.get(symbol) if isinstance(positions, Mapping) else None
    open_position = existing if existing is not None and existing.is_open else None

    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION

        old_value = (
            config.money(position_value(open_position, fill_price))
            if open_position is not None
            else _ZERO
        )

        realized = _ZERO
        closed_trade: Optional[ClosedTrade] = None

        if open_position is None:
            new_position = Position(
                symbol=symbol,
                side=incoming_side,
                size=fill_qty,
                entry_price=fill_price,
                current_price=fill_price,
                unrealized_pnl=_ZERO,
                price_at=filled_at,
                opened_at=filled_at,
                closed_at=None,
            )
        elif open_position.side == incoming_side:
            new_size = config.qty(open_position.size + fill_qty)
            new_position = replace(
                open_position,
                size=new_size,
                entry_price=_weighted_average_entry(
                    open_position.size,
                    open_position.entry_price,
                    fill_qty,
                    fill_price,
                    config,
                ),
                current_price=fill_price,
                unrealized_pnl=None,
                price_at=filled_at,
                closed_at=None,
            )
        else:
            closing_qty = min(fill_qty, open_position.size)
            realized = config.money(
                _realized_on_close(
                    open_position.side,
                    open_position.entry_price,
                    fill_price,
                    closing_qty,
                )
            )
            remaining = config.qty(open_position.size - closing_qty)

            if fill_qty <= open_position.size:
                # Partial or full close. The entry price is untouched: the quantity that
                # stays open was bought at the same average as the quantity that left.
                new_position = replace(
                    open_position,
                    size=remaining,
                    current_price=fill_price,
                    unrealized_pnl=None,
                    price_at=filled_at,
                    closed_at=filled_at if remaining == _ZERO else None,
                )
            else:
                # Reversal through zero: the old position is fully closed and a new one of
                # the opposite side opens with the surplus, priced at this fill.
                new_position = Position(
                    symbol=symbol,
                    side=incoming_side,
                    size=config.qty(fill_qty - open_position.size),
                    entry_price=fill_price,
                    current_price=fill_price,
                    unrealized_pnl=_ZERO,
                    price_at=filled_at,
                    opened_at=filled_at,
                    closed_at=None,
                )

            if remaining == _ZERO:
                # Requirement 18.10: a trade is closed when its position quantity reaches
                # zero - which a reversal does on its way through. A partial close records
                # realized PnL on the account but writes no paper_trades row, because the
                # position has not reached zero yet.
                closed_trade = ClosedTrade(
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

        new_value = config.money(position_value(new_position, fill_price))

        # Cash as the residual of the position-value change. This is the whole of the fill's
        # cash arithmetic; there is no per-case debit or credit anywhere above.
        cash_delta = -(new_value - old_value) - fill_fee

        available_after = account.available_balance + release + cash_delta
        locked_after = account.locked_balance - release

        if locked_after < _ZERO:
            raise InvariantViolation(
                "locked_balance",
                f"releasing {release} would drive locked_balance below zero from "
                f"{account.locked_balance} (Requirement 18.4)",
            )
        if available_after < _ZERO:
            raise InvariantViolation(
                "available_balance",
                f"the fill of {fill_qty} {symbol} at {fill_price} with fee {fill_fee} "
                f"would drive available_balance to {available_after} "
                "(Requirement 18.4)",
            )

        new_positions: Dict[str, Position] = dict(positions) if positions else {}
        new_positions[symbol] = new_position

        # Value the book at this fill's price for the filled symbol and at each other open
        # position's last validated price. Every open position has a recorded current_price,
        # because a position only exists as the result of a fill that set one.
        prices_at_fill = last_validated_prices(new_positions)
        prices_at_fill[symbol] = fill_price
        market_value = position_market_value(new_positions, prices_at_fill, config)

        new_account = Account(
            available_balance=available_after,
            locked_balance=locked_after,
            realized_pnl=account.realized_pnl + realized,
            total_equity=available_after + locked_after + market_value,
            currency=account.currency,
        )

        # Recorded on the position now that the book has a price for it, so the persisted
        # row carries the unrealized PnL and the price_at of the same valuation.
        new_position = replace(
            new_position,
            unrealized_pnl=config.money(
                position_unrealized_pnl(new_position, fill_price)
            ),
        )
        new_positions[symbol] = new_position

        return FillResult(
            account=new_account,
            positions=MappingProxyType(new_positions),
            position=new_position,
            realized_pnl=realized,
            fee=fill_fee,
            available_delta=available_after - account.available_balance,
            locked_delta=locked_after - account.locked_balance,
            realized_delta=realized,
            total_equity_delta=-fill_fee,
            position_market_value=market_value,
            closed_trade=closed_trade,
        )


def recalculate(
    account: Account,
    positions: Mapping[str, Position],
    prices: Mapping[str, Any],
    config: AccountingConfig,
    *,
    price_at: Optional[datetime] = None,
) -> Valuation:
    """Revalue every open position at the given prices and recompute ``total_equity``.

    The replacement for ``paper_trading_service._recalculate_account``: same job, same SHORT
    convention, but the prices are arguments rather than a fallback to the position's own
    ``current_price`` (which is what let the existing service report a pre-disconnection
    price as current), ``total_equity`` is the computed sum, and a missing price raises
    instead of being substituted.

    Cash is untouched. A revaluation moves no money; it only restates what the open
    positions are worth, so ``available_balance`` and ``locked_balance`` come through
    unchanged and ``realized_pnl`` with them.

    Args:
        account: The account to restate.
        positions: ``{symbol: Position}``, closed positions included - they are carried
            through untouched, with ``unrealized_pnl`` set to zero, because a position of
            exactly zero size has no open quantity to be unrealized on.
        prices: ``{symbol: Decimal}`` latest validated prices. Only open positions need one.
        config: The session's frozen configuration.
        price_at: The timestamp of the prices, recorded on each revalued position's
            ``price_at`` (Requirement 18.8's "recording the timestamp of the price used",
            and Requirement 18.15's ``last_price_at``).

    Returns:
        A :class:`Valuation` whose ``account.total_equity`` satisfies Requirement 18.3
        exactly against the returned positions and the given prices.

    Raises:
        StalePrice: If an open position's symbol has no validated price. The caller reports
            the valuation as stale against :func:`last_validated_prices` and
            :func:`latest_price_at` rather than substituting a price (Requirement 18.15).
    """
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        revalued: Dict[str, Position] = {}
        market_value = _ZERO
        unrealized_total = _ZERO

        for symbol, position in positions.items():
            if not position.is_open:
                revalued[symbol] = replace(position, unrealized_pnl=_ZERO)
                continue
            current = _price_for(position.symbol, prices)
            value = config.money(position_value(position, current))
            pnl = config.money(position_unrealized_pnl(position, current))
            revalued[symbol] = replace(
                position,
                current_price=current,
                unrealized_pnl=pnl,
                price_at=price_at if price_at is not None else position.price_at,
            )
            market_value += value
            unrealized_total += pnl

        return Valuation(
            account=replace(
                account,
                total_equity=account.available_balance
                + account.locked_balance
                + market_value,
            ),
            positions=MappingProxyType(revalued),
            position_market_value=market_value,
            unrealized_pnl=unrealized_total,
            price_at=price_at if price_at is not None else latest_price_at(revalued),
        )


# ══════════════════════════════════════════════════════════════════════════
# METRICS (Requirements 18.9, 18.10, 18.12)
# ══════════════════════════════════════════════════════════════════════════


def _snapshot_equity_and_time(snapshot: Any) -> Tuple[Decimal, Optional[Any]]:
    """Read ``(total_equity, taken_at)`` off a snapshot row, an object or a bare value.

    Three spellings are admitted because three callers exist: ``paper_repository`` hands over
    ``paper_equity_snapshots`` rows (mappings), the session worker hands over its own objects,
    and the property tests generate bare equity series with no timestamps at all. A bare value
    carries no ``taken_at``, so the ordering check simply has nothing to check - it does not
    silently pass a series that *did* carry timestamps out of order.
    """
    if isinstance(snapshot, Mapping):
        if "total_equity" not in snapshot:
            raise InvalidQuantity("equity snapshot carries no 'total_equity'")
        return to_decimal(snapshot["total_equity"], "snapshot total_equity"), snapshot.get(
            "taken_at"
        )
    if hasattr(snapshot, "total_equity"):
        return (
            to_decimal(snapshot.total_equity, "snapshot total_equity"),
            getattr(snapshot, "taken_at", None),
        )
    return to_decimal(snapshot, "snapshot total_equity"), None


def max_drawdown(snapshots: Sequence[Any]) -> Drawdown:
    """Maximum drawdown over a session's persisted equity snapshots.

    Requirement 18.9: the largest decline from a running peak to a subsequent trough,
    reported both as an exact ``Decimal >= 0`` and as a fraction of that peak in ``[0, 1]``,
    and **zero while the session holds fewer than two snapshots** - one point describes no
    decline, and reporting a drawdown from it would be reporting a measurement that was not
    made.

    Args:
        snapshots: The persisted series, already ordered by ``taken_at`` ascending (which is
            what ``idx_paper_equity ON (session_id, series_index, taken_at ASC)`` exists
            for). Each element may be a ``paper_equity_snapshots`` row mapping, an object
            with ``total_equity`` / ``taken_at``, or a bare equity value.

    Returns:
        A :class:`Drawdown`. ``fraction`` is ``Decimal('0')`` when the peak at the trough is
        not positive, and is capped at ``Decimal('1')`` - an equity that fell to or below
        zero lost all of its peak, and the uncapped magnitude is still available in
        ``amount``, so the cap loses no information while keeping the column's
        ``0 <= fraction <= 1`` check satisfiable (Requirement 18.9, P-29).

    Raises:
        SnapshotOrderError: If two consecutive snapshots that both carry a ``taken_at`` are
            out of order. The series is refused rather than sorted: the drawdown of a
            resorted series is not the drawdown of the series that was read, and a
            mis-ordered read is a repository bug worth surfacing.
    """
    series = list(snapshots or ())
    if len(series) < 2:
        return Drawdown(amount=_ZERO, fraction=_ZERO)

    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION

        equities: List[Decimal] = []
        previous_time: Any = None
        for snapshot in series:
            equity, taken_at = _snapshot_equity_and_time(snapshot)
            if taken_at is not None and previous_time is not None and taken_at < previous_time:
                raise SnapshotOrderError(
                    "equity snapshots must be in non-decreasing taken_at order "
                    f"(Requirement 18.9); {taken_at!r} follows {previous_time!r}"
                )
            if taken_at is not None:
                previous_time = taken_at
            equities.append(equity)

        peak = equities[0]
        amount = _ZERO
        peak_at_trough = peak
        for equity in equities[1:]:
            if equity > peak:
                peak = equity
            decline = peak - equity
            if decline > amount:
                amount = decline
                peak_at_trough = peak

        if amount <= _ZERO:
            return Drawdown(amount=_ZERO, fraction=_ZERO)
        if peak_at_trough <= _ZERO:
            return Drawdown(amount=amount, fraction=_ZERO)

        fraction = (amount / peak_at_trough).quantize(
            FRACTION_QUANTUM, rounding=DEFAULT_ROUNDING_MODE
        )
        return Drawdown(amount=amount, fraction=min(fraction, _ONE))


def win_rate(
    closed_trades: Iterable[Any], rounding: str = DEFAULT_ROUNDING_MODE
) -> Optional[Decimal]:
    """The fraction of closed trades whose realized profit is strictly above zero.

    Requirement 18.10: ``count(realized_pnl > 0) / count(all)``, in ``[0, 1]``, and
    **absent** - ``None``, not ``Decimal('0')`` - while the closed-trade count is zero. Zero
    would claim the session has lost every trade it made; ``None`` says it has closed none,
    which is the true statement and the reason ``paper_metrics.win_rate`` is nullable.

    ``> 0`` strictly: a break-even trade is neither counted as a win nor excluded from the
    denominator, so the rate is the share of closed trades that made money.

    Args:
        closed_trades: :class:`ClosedTrade` instances, ``paper_trades`` row mappings, or any
            objects exposing ``realized_pnl``.
        rounding: The session's recorded rounding mode, for the quantization to
            :data:`WIN_RATE_QUANTUM` (the scale ``NUMERIC(6,5)`` stores). Rounding a value
            already inside ``[0, 1]`` cannot carry it outside, so the bound survives.

    Returns:
        The rate at :data:`WIN_RATE_QUANTUM` scale, or ``None`` for an empty set.
    """
    total = 0
    wins = 0
    for trade in closed_trades or ():
        total += 1
        realized = to_decimal(_attr(trade, "realized_pnl", "closed trade"), "realized_pnl")
        if realized > _ZERO:
            wins += 1

    if total == 0:
        return None

    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        return (Decimal(wins) / Decimal(total)).quantize(
            WIN_RATE_QUANTUM, rounding=_resolve_rounding(rounding)
        )


def margin_usage(
    positions: Any, prices: Mapping[str, Any], config: AccountingConfig
) -> Optional[Decimal]:
    """Margin committed by the open positions, or ``None`` where margin does not apply.

    Requirement 18.12: computed and recorded only where the session's market type supports
    it, and reported as **absent** where it does not - "rather than a zero presented as a
    measurement". A ``spot`` session borrows nothing, so it has no margin figure at all;
    returning ``Decimal('0')`` for it would read as "measured, and it was zero".

    Where it does apply (:data:`MARGIN_MARKET_TYPES`), the figure is the notional committed:
    the sum over open positions of ``size * latest validated price``. The frozen session
    config records no leverage or initial-margin ratio - ``design.md`` states outright that
    perpetual funding, liquidation and cross-margin are not modelled - so dividing by a
    leverage figure here would mean inventing one. When such a figure is added to
    ``paper_sessions.config``, this is the one place it divides.

    Raises:
        StalePrice: If an open position's symbol has no validated price (Requirement 18.15).
    """
    if not config.supports_margin:
        return None

    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        total = _ZERO
        for position in _positions_of(positions):
            if not position.is_open:
                continue
            price = _price_for(position.symbol, prices)
            total += config.money(position.size * price)
        return total


def compute_metrics(
    account: Account,
    positions: Mapping[str, Position],
    prices: Mapping[str, Any],
    snapshots: Sequence[Any],
    closed_trades: Iterable[Any],
    config: AccountingConfig,
    *,
    initial_capital: Optional[Any] = None,
    stale: bool = False,
    price_at: Optional[datetime] = None,
) -> SessionMetrics:
    """Assemble the ``paper_metrics`` row for a session.

    One place where "absent" is honoured rather than flattened: :attr:`SessionMetrics.win_rate`
    is ``None`` for an empty closed-trade set (Requirement 18.10) and
    :attr:`SessionMetrics.margin_usage` is ``None`` on an unmargined market type
    (Requirement 18.12), and :meth:`SessionMetrics.to_row` omits both rather than emitting a
    zero. ``total_return_pct`` is likewise ``None`` when no positive initial capital was
    recorded, because a return on nothing is not a percentage.

    Args:
        account: The account, already recalculated - ``total_equity`` is read from it, not
            recomputed, so a caller that has not run :func:`recalculate` gets the metrics of
            the state it actually holds. :func:`assert_invariants` is the guard against that
            state being wrong, and it runs in the caller's transaction.
        positions: ``{symbol: Position}``.
        prices: Latest validated prices, for the unrealized and margin figures.
        snapshots: The persisted equity series, for :func:`max_drawdown`.
        closed_trades: The session's ``paper_trades``, for :func:`win_rate`.
        config: The session's frozen configuration.
        initial_capital: The recorded initial simulated capital, for ``total_return_pct``.
        stale: Whether the valuation is against last validated prices rather than current
            ones (Requirement 18.15). Reported, never inferred.
        price_at: The timestamp of the prices used (Requirement 18.8). Defaults to the newest
            ``price_at`` recorded on the positions.

    Raises:
        StalePrice: If an open position has no price in ``prices``. The caller values against
            :func:`last_validated_prices` and passes ``stale=True`` instead of substituting.
    """
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION

        market_value = position_market_value(positions, prices, config)
        open_pnl = unrealized_pnl(positions, prices, config)
        drawdown = max_drawdown(snapshots)
        trades = list(closed_trades or ())

        total_return_pct: Optional[Decimal] = None
        if initial_capital is not None:
            capital = to_decimal(initial_capital, "initial_capital")
            if capital > _ZERO:
                total_return_pct = (
                    (account.total_equity - capital) / capital * Decimal(100)
                ).quantize(FRACTION_QUANTUM, rounding=config.rounding_mode)

        return SessionMetrics(
            realized_pnl=account.realized_pnl,
            unrealized_pnl=open_pnl,
            total_equity=account.total_equity,
            position_market_value=market_value,
            max_drawdown_amount=drawdown.amount,
            max_drawdown_fraction=drawdown.fraction,
            closed_trade_count=len(trades),
            total_return_pct=total_return_pct,
            win_rate=win_rate(trades, config.rounding_mode),
            margin_usage=margin_usage(positions, prices, config),
            stale=stale,
            price_at=price_at if price_at is not None else latest_price_at(positions),
        )


__all__ = [
    # constants
    "DECIMAL_PRECISION",
    "ROUNDING_MODES",
    "DEFAULT_ROUNDING_MODE",
    "LONG",
    "SHORT",
    "SIDES",
    "BUY",
    "SELL",
    "WEIGHTED_AVERAGE",
    "COST_BASES",
    "MARGIN_MARKET_TYPES",
    "WIN_RATE_QUANTUM",
    "FRACTION_QUANTUM",
    # exceptions
    "PaperAccountingError",
    "StalePrice",
    "InvariantViolation",
    "SnapshotOrderError",
    "InvalidConfiguration",
    "InvalidQuantity",
    # value objects
    "AccountingConfig",
    "Account",
    "Position",
    "ClosedTrade",
    "Valuation",
    "FillResult",
    "Drawdown",
    "SessionMetrics",
    # arithmetic
    "to_decimal",
    "quantize_money",
    "quantize_qty",
    "position_value",
    "position_unrealized_pnl",
    "position_market_value",
    "unrealized_pnl",
    "total_equity",
    "violated_invariant",
    "invariants_hold",
    "assert_invariants",
    "last_validated_prices",
    "latest_price_at",
    "required_funds",
    "lock",
    "unlock",
    "apply_fill",
    "recalculate",
    "max_drawdown",
    "win_rate",
    "margin_usage",
    "compute_metrics",
]
