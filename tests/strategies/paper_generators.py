"""Shared Hypothesis generators for the paper-trading property tests.

Spec: `.kiro/specs/marketplace-subscriptions-paper-trading` task 2.2, Requirement 29.3.

Every generator here yields **plain built-in structures** — `dict`, `list`, `str`, `int`,
`decimal.Decimal`, `datetime` — and imports nothing from `backend_app`. That is deliberate:
the modules that consume these values do not exist yet.

Coupling this module must be kept in step with
--------------------------------------------------
| Structure drawn here            | Consumer (created by a later task)                     |
|---------------------------------|--------------------------------------------------------|
| `market_event_streams()` items  | `backend_app/backend/paper/paper_market_feed.py`        |
|                                 | `next_validated_event` / `paper_market_events` rows      |
|                                 | (task 24, Requirements 14.7, 15.5)                      |
| `order_intents()` items         | `paper/paper_simulator.py::submit_intent` (task 24,     |
|                                 | Requirements 16.5, 16.6, 16.8)                          |
| `fill_sequences()` fills        | `paper/paper_simulator.py::apply_fill` and              |
|                                 | `paper/paper_accounting.py::apply_fill` (tasks 9, 24)   |
| `equity_series()` snapshots     | `paper_equity_snapshots` rows consumed by               |
|                                 | `paper/paper_accounting.py` drawdown (task 9, Req 18.9) |

The key names are the column names of `design.md § paper_orders / paper_fills /
paper_equity_snapshots / paper_market_events`, so a consumer can accept a drawn dict
directly. If a consumer renames a field, this module is the single place to follow it.

Exact decimal money only (Requirements 18.1, 8.13)
--------------------------------------------------
No binary `float` is ever produced. Prices, quantities and equity values are `Decimal`
built by exponent-shifting an integer count of ticks, so they are exact and carry a fixed
exponent. Fees and slippage are integer Minor_Units (`fee_minor`, `slippage_minor`), which
is how `paper_fills` stores them. `assert_no_binary_floats` is exported so a consuming
property test can hold the whole drawn structure to that rule.
"""

from __future__ import annotations

import hashlib
import string
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hypothesis import strategies as st

__all__ = [
    "DEFAULT_SESSION_CONFIG",
    "EQUITY_SNAPSHOT_CAUSES",
    "EXCHANGES",
    "FEED_TRANSPORTS",
    "INTERVAL_SECONDS",
    "MAX_ORDER_QUANTITY",
    "MINOR_UNIT_EXPONENT",
    "PRICE_PRECISION",
    "QUANTITY_PRECISION",
    "SUPPORTED_ORDER_TYPES",
    "SUPPORTED_SIDES",
    "TIMEFRAMES",
    "UNVALIDATED_SYMBOLS",
    "VALIDATED_SYMBOLS",
    "assert_no_binary_floats",
    "equity_series",
    "fill_sequences",
    "idempotency_keys",
    "invalid_order_intents",
    "market_event_streams",
    "market_events",
    "order_intent",
    "order_intents",
    "prices",
    "quantities",
    "session_config",
    "source_event_id_for",
]

# --------------------------------------------------------------------------------------
# Constants mirroring `paper_sessions.config` in design.md
# --------------------------------------------------------------------------------------

PRICE_PRECISION = 2
QUANTITY_PRECISION = 8
MINOR_UNIT_EXPONENT = 2
MAX_ORDER_QUANTITY = Decimal("1000000")

VALIDATED_SYMBOLS: Tuple[str, ...] = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
# Symbols deliberately absent from `config.validated_symbols`, for the
# SYMBOL_NOT_VALIDATED rejection path (Requirement 16.5).
UNVALIDATED_SYMBOLS: Tuple[str, ...] = ("DOGE/USDT", "XRP/USDT")

SUPPORTED_ORDER_TYPES: Tuple[str, ...] = ("market", "limit")
SUPPORTED_SIDES: Tuple[str, ...] = ("buy", "sell")
TIME_IN_FORCE: Tuple[str, ...] = ("GTC", "IOC")
EXCHANGES: Tuple[str, ...] = ("binance", "kraken")
TIMEFRAMES: Tuple[str, ...] = ("1m", "5m", "15m", "1h")
FEED_TRANSPORTS: Tuple[str, ...] = ("WEBSOCKET", "REST")
EQUITY_SNAPSHOT_CAUSES: Tuple[str, ...] = (
    "SESSION_START",
    "FILL",
    "FEE",
    "REVALUATION",
    "SESSION_STOP",
)

INTERVAL_SECONDS: Dict[str, int] = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}

#: The frozen session configuration of `design.md § paper_sessions.config`. Rates are
#: strings so a consumer builds a `Decimal` from them without a `float` ever existing.
DEFAULT_SESSION_CONFIG: Dict[str, Any] = {
    "fee_rate": "0.0010",
    "slippage_rate": "0.0005",
    "participation_rate": "0.10",
    "rounding_mode": "ROUND_HALF_EVEN",
    "cost_basis": "WEIGHTED_AVERAGE",
    "price_precision": PRICE_PRECISION,
    "quantity_precision": QUANTITY_PRECISION,
    "minor_unit_exponent": MINOR_UNIT_EXPONENT,
    "max_order_quantity": str(MAX_ORDER_QUANTITY),
    "supported_order_types": list(SUPPORTED_ORDER_TYPES),
    "supported_sides": list(SUPPORTED_SIDES),
    "validated_symbols": list(VALIDATED_SYMBOLS),
    "market_data_source": "mds.watch_ohlcv",
    "simulator": "backend_app.backend.paper.paper_simulator.PaperSimulator",
    "schema_version": "paper.v1",
}

_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)
_MAX_INSTANT_OFFSET_SECONDS = 3 * 365 * 24 * 3600

_PRICE_TICK_MIN = 1
_PRICE_TICK_MAX = 10_000_000  # 100_000.00 at PRICE_PRECISION = 2
_QUANTITY_TICK_MAX = 10_000_000_000  # 100.00000000 at QUANTITY_PRECISION = 8

_IDEMPOTENCY_ALPHABET = string.ascii_letters + string.digits + "-_"


# --------------------------------------------------------------------------------------
# Exact-decimal helpers
# --------------------------------------------------------------------------------------


def _from_ticks(ticks: int, exponent: int) -> Decimal:
    """Return ``ticks * 10**-exponent`` exactly, with a fixed decimal exponent.

    `Decimal.scaleb` shifts the exponent without touching the coefficient, so no
    rounding and no binary float is involved.
    """
    return Decimal(int(ticks)).scaleb(-exponent).quantize(Decimal(1).scaleb(-exponent))


def _to_minor(amount: Decimal) -> int:
    """Quantize a `Decimal` amount to integer Minor_Units under the config's rounding."""
    return int(
        amount.scaleb(MINOR_UNIT_EXPONENT).to_integral_value(rounding=ROUND_HALF_EVEN)
    )


def source_event_id_for(
    exchange: str,
    symbol: str,
    timeframe: str,
    timestamp: datetime,
    close: Decimal,
    volume: Optional[Decimal],
) -> str:
    """Mirror `design.md`'s `source_event_id` for an OHLCV payload.

    ``sha256(f"{exchange}|{symbol}|{timeframe}|{timestamp}|{close}|{volume}")`` — stable
    for a republished candle, different for a revised one, which is the dedupe semantics
    Requirement 14.7 needs. The canonical implementation will live in
    `paper/paper_market_feed.py` (task 24); a consumer should read the `source_event_id`
    carried on the drawn event rather than recompute it here.
    """
    payload = "|".join(
        [
            exchange,
            symbol,
            timeframe,
            timestamp.isoformat(),
            str(close),
            "" if volume is None else str(volume),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assert_no_binary_floats(value: Any, path: str = "$") -> None:
    """Raise `AssertionError` if a binary `float` (or `complex`) appears anywhere.

    Walks dicts, lists, tuples and sets to any depth. Requirements 18.1 and 8.13 forbid
    binary floating-point money arithmetic, so a generator that leaked a `float` would
    silently weaken every property that consumes it.
    """
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (float, complex)):
        raise AssertionError(f"binary float at {path}: {value!r}")
    if isinstance(value, dict):
        for key, item in value.items():
            assert_no_binary_floats(key, f"{path}.{key!r}")
            assert_no_binary_floats(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            assert_no_binary_floats(item, f"{path}[{index}]")


# --------------------------------------------------------------------------------------
# Primitive strategies
# --------------------------------------------------------------------------------------


def prices(
    *,
    min_ticks: int = _PRICE_TICK_MIN,
    max_ticks: int = _PRICE_TICK_MAX,
    precision: int = PRICE_PRECISION,
) -> st.SearchStrategy:
    """Strictly positive prices as `Decimal` with exactly `precision` decimal places."""
    pool = [t for t in (min_ticks, min_ticks + 1, 100, max_ticks) if min_ticks <= t <= max_ticks]
    return st.one_of(
        st.sampled_from(pool),
        st.integers(min_value=min_ticks, max_value=max_ticks),
    ).map(lambda ticks: _from_ticks(ticks, precision))


def quantities(
    *,
    min_ticks: int = 1,
    max_ticks: int = _QUANTITY_TICK_MAX,
    precision: int = QUANTITY_PRECISION,
) -> st.SearchStrategy:
    """Strictly positive quantities as `Decimal` at `config.quantity_precision`.

    Bounded by `config.max_order_quantity`, so a drawn quantity is valid input rather
    than a QUANTITY_ABOVE_MAX rejection. `invalid_order_intents()` covers that path.
    """
    return st.integers(min_value=min_ticks, max_value=max_ticks).map(
        lambda ticks: _from_ticks(ticks, precision)
    )


def idempotency_keys() -> st.SearchStrategy:
    """Keys of 1–128 characters, the length window `chk_paper_order_idem_len` allows."""
    return st.one_of(
        st.just("k"),
        st.just("k" * 128),
        st.text(alphabet=_IDEMPOTENCY_ALPHABET, min_size=1, max_size=128),
    )


def utc_instants() -> st.SearchStrategy:
    """Timezone-aware UTC instants at whole-second resolution.

    Private to the paper generators on purpose: `marketplace_generators.utc_instants()`
    carries the month-boundary pool the calendar-period properties need, which is a
    different concern from a market-data clock.
    """
    return st.integers(min_value=0, max_value=_MAX_INSTANT_OFFSET_SECONDS).map(
        lambda seconds: _EPOCH + timedelta(seconds=seconds)
    )


def session_config(*, zero_cost: bool = False) -> st.SearchStrategy:
    """A frozen `paper_sessions.config` dict with drawn fee and slippage rates.

    `zero_cost=True` pins both rates to ``"0"``, which is the precondition of the
    cash-conservation property (Requirement 18.6, P-27).
    """
    if zero_cost:
        return st.just(_config_with(fee_rate="0", slippage_rate="0"))
    return st.builds(
        _config_with,
        fee_rate=st.sampled_from(["0", "0.0005", "0.0010", "0.0025"]),
        slippage_rate=st.sampled_from(["0", "0.0005", "0.0010"]),
    )


def _config_with(*, fee_rate: str, slippage_rate: str) -> Dict[str, Any]:
    config = dict(DEFAULT_SESSION_CONFIG)
    config["fee_rate"] = fee_rate
    config["slippage_rate"] = slippage_rate
    return config


# --------------------------------------------------------------------------------------
# Market data events and streams
# --------------------------------------------------------------------------------------


def _draw_market_event(
    draw: Any,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    timestamp: datetime,
) -> Dict[str, Any]:
    """Draw one validated OHLCV event for a fixed (exchange, symbol, timeframe, ts)."""
    low_ticks = draw(st.integers(min_value=_PRICE_TICK_MIN, max_value=_PRICE_TICK_MAX))
    open_offset = draw(st.integers(min_value=0, max_value=10_000))
    close_offset = draw(st.integers(min_value=0, max_value=10_000))
    high_extra = draw(st.integers(min_value=0, max_value=10_000))

    low = _from_ticks(low_ticks, PRICE_PRECISION)
    open_price = _from_ticks(low_ticks + open_offset, PRICE_PRECISION)
    close = _from_ticks(low_ticks + close_offset, PRICE_PRECISION)
    high = _from_ticks(
        low_ticks + max(open_offset, close_offset) + high_extra, PRICE_PRECISION
    )

    volume = draw(
        st.one_of(
            st.none(),  # no volume reported -> whole fill, per `fillable_quantity`
            st.integers(min_value=0, max_value=_QUANTITY_TICK_MAX).map(
                lambda ticks: _from_ticks(ticks, QUANTITY_PRECISION)
            ),
        )
    )
    spread_ticks = draw(st.integers(min_value=0, max_value=100))
    has_quotes = draw(st.booleans())
    bid = close - _from_ticks(spread_ticks, PRICE_PRECISION) if has_quotes else None
    ask = close + _from_ticks(spread_ticks, PRICE_PRECISION) if has_quotes else None
    if bid is not None and bid <= Decimal(0):
        bid = _from_ticks(_PRICE_TICK_MIN, PRICE_PRECISION)
    latency_ms = _from_ticks(
        draw(st.integers(min_value=0, max_value=5_000_000)), 3
    )  # NUMERIC(10,3)

    return {
        "kind": "candle",
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "bid": bid,
        "ask": ask,
        "transport": draw(st.sampled_from(FEED_TRANSPORTS)),
        "latency_ms": latency_ms,
        "received_at": timestamp + timedelta(milliseconds=int(latency_ms)),
        "source_event_id": source_event_id_for(
            exchange, symbol, timeframe, timestamp, close, volume
        ),
    }


@st.composite
def market_events(
    draw: Any,
    *,
    symbols: Sequence[str] = VALIDATED_SYMBOLS,
    timeframes: Sequence[str] = TIMEFRAMES,
) -> Dict[str, Any]:
    """One validated market event, every field drawn.

    Shape: the payload `paper_market_events` records plus the `symbol`, `timeframe` and
    `event_timestamp` it stores as columns. `low <= min(open, close) <= max(open, close)
    <= high` holds by construction, so the event is one `market_data_validation.validate`
    would admit. `volume` is `None` on some draws, which is the "no volume reported"
    branch of `fillable_quantity`.
    """
    return _draw_market_event(
        draw,
        exchange=draw(st.sampled_from(tuple(EXCHANGES))),
        symbol=draw(st.sampled_from(tuple(symbols))),
        timeframe=draw(st.sampled_from(tuple(timeframes))),
        timestamp=draw(utc_instants()),
    )


@st.composite
def market_event_streams(
    draw: Any,
    *,
    min_events: int = 1,
    max_events: int = 10,
    symbols: Sequence[str] = VALIDATED_SYMBOLS[:2],
    with_duplicates: bool = True,
    with_reordering: bool = True,
    with_gaps: bool = True,
    with_disconnections: bool = True,
) -> Dict[str, Any]:
    """A feed delivery order: duplicates, reorderings, gaps and disconnection points.

    Returns::

        {
          "exchange": str,
          "timeframe": str,
          "symbols": [str, ...],
          "events": [event, ...],          # delivery order, as the feed hands them over
          "duplicate_indices": [int, ...], # positions in `events` that are re-deliveries
          "swapped_indices": [int, ...],   # positions i swapped with i+1
          "disconnections": [{"before_index": int, "downtime_ms": int}, ...],
        }

    `events` is deliberately **not** the accepted stream. It contains repeated
    `source_event_id` values (a republished candle keeps its id) and timestamps that
    regress for a symbol. The dedupe-and-monotonicity projection is the oracle a
    consuming property computes for itself (Requirements 14.7, 15.5, P-54); computing it
    here would make the generator its own oracle.

    `disconnections[i]["before_index"]` is the position in `events` at which the
    subscription dropped: the feed is degraded from just before that event is delivered
    until the first event after it that passes validation (Requirement 14.5, P-56).
    Indices are strictly increasing.
    """
    exchange = draw(st.sampled_from(tuple(EXCHANGES)))
    timeframe = draw(st.sampled_from(tuple(TIMEFRAMES)))
    stream_symbols = draw(
        st.lists(
            st.sampled_from(tuple(symbols)),
            min_size=1,
            max_size=min(2, len(symbols)),
            unique=True,
        )
    )
    count = draw(st.integers(min_value=min_events, max_value=max_events))
    start = draw(utc_instants())
    interval = timedelta(seconds=INTERVAL_SECONDS[timeframe])

    cursor: Dict[str, datetime] = {symbol: start for symbol in stream_symbols}
    events: List[Dict[str, Any]] = []
    for index in range(count):
        symbol = stream_symbols[index % len(stream_symbols)]
        step = draw(st.integers(min_value=1, max_value=3)) if with_gaps else 1
        cursor[symbol] = cursor[symbol] + interval * step
        events.append(
            _draw_market_event(
                draw,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                timestamp=cursor[symbol],
            )
        )

    # Re-deliveries: a verbatim copy of an earlier event, so its `source_event_id`
    # repeats exactly the way `mds/main.py` republishing the same candle would.
    duplicate_indices: List[int] = []
    if with_duplicates and events:
        duplicate_count = draw(st.integers(min_value=0, max_value=3))
        for _ in range(duplicate_count):
            source = draw(st.integers(min_value=0, max_value=len(events) - 1))
            position = draw(st.integers(min_value=0, max_value=len(events)))
            events.insert(position, dict(events[source]))
            duplicate_indices.append(position)

    # Reorderings: an adjacent swap makes the delivered timestamp sequence regress.
    swapped_indices: List[int] = []
    if with_reordering and len(events) >= 2:
        swap_count = draw(st.integers(min_value=0, max_value=2))
        for _ in range(swap_count):
            index = draw(st.integers(min_value=0, max_value=len(events) - 2))
            events[index], events[index + 1] = events[index + 1], events[index]
            swapped_indices.append(index)

    disconnections: List[Dict[str, int]] = []
    if with_disconnections and events:
        cut_points = draw(
            st.lists(
                st.integers(min_value=0, max_value=len(events) - 1),
                min_size=0,
                max_size=2,
                unique=True,
            )
        )
        for before_index in sorted(cut_points):
            disconnections.append(
                {
                    "before_index": before_index,
                    # The backoff ladder of design.md: 1s, 2s, 4s, 8s, 16s, then 30s.
                    "downtime_ms": draw(
                        st.sampled_from([1_000, 2_000, 4_000, 8_000, 16_000, 30_000])
                    ),
                }
            )

    return {
        "exchange": exchange,
        "timeframe": timeframe,
        "symbols": stream_symbols,
        "events": events,
        "duplicate_indices": sorted(duplicate_indices),
        "swapped_indices": sorted(swapped_indices),
        "disconnections": disconnections,
    }


# --------------------------------------------------------------------------------------
# Order intents
# --------------------------------------------------------------------------------------


def _draw_order_intent(
    draw: Any,
    *,
    symbols: Sequence[str],
    order_types: Sequence[str],
    sides: Sequence[str],
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    order_type = draw(st.sampled_from(tuple(order_types)))
    return {
        "idempotency_key": (
            idempotency_key if idempotency_key is not None else draw(idempotency_keys())
        ),
        "symbol": draw(st.sampled_from(tuple(symbols))),
        "side": draw(st.sampled_from(tuple(sides))),
        "order_type": order_type,
        "quantity": draw(quantities()),
        # A market intent carries no limit price; `submit_intent` then prices it from
        # `session.last_validated_price` (Requirement 14.9 — never a synthesised price).
        "limit_price": draw(prices()) if order_type == "limit" else None,
        "time_in_force": draw(st.sampled_from(TIME_IN_FORCE)),
        "signal_id": draw(st.one_of(st.none(), st.uuids().map(str))),
        "created_at": draw(utc_instants()),
    }


@st.composite
def order_intent(
    draw: Any,
    *,
    symbols: Sequence[str] = VALIDATED_SYMBOLS,
    order_types: Sequence[str] = SUPPORTED_ORDER_TYPES,
    sides: Sequence[str] = SUPPORTED_SIDES,
) -> Dict[str, Any]:
    """One intent that passes every static check of `submit_intent` (Requirement 16.5)."""
    return _draw_order_intent(
        draw, symbols=symbols, order_types=order_types, sides=sides
    )


@st.composite
def order_intents(
    draw: Any,
    *,
    min_size: int = 1,
    max_size: int = 5,
    symbols: Sequence[str] = VALIDATED_SYMBOLS,
    order_types: Sequence[str] = SUPPORTED_ORDER_TYPES,
    sides: Sequence[str] = SUPPORTED_SIDES,
    unique_keys: bool = True,
) -> List[Dict[str, Any]]:
    """A batch of statically valid order intents for `paper_simulator.submit_intent`.

    Each intent is::

        {"idempotency_key", "symbol", "side", "order_type", "quantity" (Decimal),
         "limit_price" (Decimal | None), "time_in_force", "signal_id", "created_at"}

    With `unique_keys=True` (the default) the keys are distinct, so the batch describes
    distinct orders rather than an idempotent retry — `uq_paper_order_idem` would collapse
    the latter. The idempotence and confluence properties (P-22, P-23) build their own
    repeats by re-submitting a drawn intent, which keeps the retry explicit in the test.
    """
    count = draw(st.integers(min_value=min_size, max_value=max_size))
    keys: List[str] = []
    if unique_keys:
        keys = draw(
            st.lists(
                idempotency_keys(), min_size=count, max_size=count, unique=True
            )
        )
    return [
        _draw_order_intent(
            draw,
            symbols=symbols,
            order_types=order_types,
            sides=sides,
            idempotency_key=keys[index] if unique_keys else None,
        )
        for index in range(count)
    ]


@st.composite
def invalid_order_intents(draw: Any) -> Tuple[Dict[str, Any], str]:
    """An intent violating exactly one static check, with the reason it must produce.

    `submit_intent` evaluates its checks as a `first_of`, so an intent with two
    violations has no single well-defined `rejection_reason`. Each draw here breaks one
    rule and leaves the rest satisfied (Requirement 16.5, P-24).
    """
    intent = draw(order_intent())
    reason = draw(
        st.sampled_from(
            [
                "QUANTITY_NOT_POSITIVE",
                "QUANTITY_ABOVE_MAX",
                "QUANTITY_PRECISION",
                "SYMBOL_NOT_VALIDATED",
                "ORDER_TYPE_UNSUPPORTED",
                "SIDE_UNSUPPORTED",
                "LIMIT_PRICE_NOT_POSITIVE",
                "LIMIT_PRICE_PRECISION",
            ]
        )
    )

    if reason == "QUANTITY_NOT_POSITIVE":
        intent["quantity"] = draw(
            st.one_of(
                st.just(Decimal("0")),
                quantities().map(lambda quantity: -quantity),
            )
        )
    elif reason == "QUANTITY_ABOVE_MAX":
        intent["quantity"] = MAX_ORDER_QUANTITY + draw(
            st.integers(min_value=1, max_value=1000).map(Decimal)
        )
    elif reason == "QUANTITY_PRECISION":
        # One more decimal place than `config.quantity_precision` allows.
        intent["quantity"] = _from_ticks(
            draw(st.integers(min_value=1, max_value=10**9)) * 10 + 1,
            QUANTITY_PRECISION + 1,
        )
    elif reason == "SYMBOL_NOT_VALIDATED":
        intent["symbol"] = draw(st.sampled_from(UNVALIDATED_SYMBOLS))
    elif reason == "ORDER_TYPE_UNSUPPORTED":
        intent["order_type"] = draw(
            st.sampled_from(["stop", "stop_limit", "trailing_stop", "MARKET"])
        )
        intent["limit_price"] = None
    elif reason == "SIDE_UNSUPPORTED":
        intent["side"] = draw(st.sampled_from(["long", "short", "BUY", "close"]))
    elif reason == "LIMIT_PRICE_NOT_POSITIVE":
        intent["order_type"] = "limit"
        intent["limit_price"] = draw(
            st.one_of(st.just(Decimal("0")), prices().map(lambda price: -price))
        )
    else:  # LIMIT_PRICE_PRECISION
        intent["order_type"] = "limit"
        intent["limit_price"] = _from_ticks(
            draw(st.integers(min_value=1, max_value=10**6)) * 10 + 1,
            PRICE_PRECISION + 1,
        )

    return intent, reason


# --------------------------------------------------------------------------------------
# Fill sequences
# --------------------------------------------------------------------------------------


@st.composite
def fill_sequences(
    draw: Any,
    *,
    min_fills: int = 1,
    max_fills: int = 5,
    zero_cost: bool = False,
    symbols: Sequence[str] = VALIDATED_SYMBOLS,
    with_duplicates: bool = True,
) -> Dict[str, Any]:
    """An order plus an ordered run of fills against it.

    Returns::

        {
          "config": {...},                  # frozen session config
          "currency": "USD",
          "initial_balance": Decimal,       # account available balance before the order
          "order": {"symbol", "side", "order_type", "quantity", "limit_price",
                    "reference_price", "idempotency_key"},
          "fills": [{"fill_event_id", "market_event_id", "quantity", "price",
                     "fee_minor", "slippage_minor", "filled_at"}, ...],
          "duplicate_indices": [int, ...],  # fills re-delivered with the same event id
        }

    The cumulative filled quantity never exceeds `order["quantity"]`, so the sequence is
    one `apply_fill` accepts rather than a `PaperOverFill` (Requirement 16.7). `fee_minor`
    and `slippage_minor` are integer Minor_Units computed by the design's formulas from
    `config`, so `paper_accounting` and `paper_replay.ReferenceLedger` are handed the same
    exact costs. `zero_cost=True` gives zero fee, zero slippage and every fill at the
    reference price — the precondition of cash conservation (Requirement 18.6, P-27).

    `duplicate_indices` positions carry a fill already present in the sequence, identical
    `fill_event_id` included, which `uq_paper_fill_event` must absorb without any balance
    change (Requirement 16.9, P-21).
    """
    config = draw(session_config(zero_cost=zero_cost))
    fee_rate = Decimal(config["fee_rate"])
    slippage_rate = Decimal(config["slippage_rate"])

    symbol = draw(st.sampled_from(tuple(symbols)))
    side = draw(st.sampled_from(SUPPORTED_SIDES))
    order_type = draw(st.sampled_from(SUPPORTED_ORDER_TYPES))
    reference_price = draw(prices())
    order_quantity_ticks = draw(
        st.integers(min_value=max_fills, max_value=_QUANTITY_TICK_MAX)
    )
    order_quantity = _from_ticks(order_quantity_ticks, QUANTITY_PRECISION)

    order = {
        "idempotency_key": draw(idempotency_keys()),
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "quantity": order_quantity,
        "limit_price": reference_price if order_type == "limit" else None,
        "reference_price": reference_price,
    }

    fill_count = draw(st.integers(min_value=min_fills, max_value=max_fills))
    weights = draw(
        st.lists(
            st.integers(min_value=1, max_value=order_quantity_ticks),
            min_size=fill_count,
            max_size=fill_count,
        )
    )
    start = draw(utc_instants())

    fills: List[Dict[str, Any]] = []
    remaining_ticks = order_quantity_ticks
    for index, weight in enumerate(weights):
        if remaining_ticks <= 0:
            break
        quantity_ticks = min(weight, remaining_ticks)
        remaining_ticks -= quantity_ticks
        quantity = _from_ticks(quantity_ticks, QUANTITY_PRECISION)

        if zero_cost:
            price = reference_price
        else:
            # A fill moves against the order by at most the configured slippage rate,
            # the adverse-direction-only convention of design.md's fill model.
            drift = (reference_price * slippage_rate).quantize(
                Decimal(1).scaleb(-PRICE_PRECISION), rounding=ROUND_HALF_EVEN
            )
            price = reference_price + drift if side == "buy" else reference_price - drift
            if price <= Decimal(0):
                price = _from_ticks(_PRICE_TICK_MIN, PRICE_PRECISION)

        fee_minor = _to_minor(quantity * price * fee_rate)
        slippage_minor = _to_minor(quantity * abs(price - reference_price))
        fills.append(
            {
                "fill_event_id": f"fill-{index}-{quantity_ticks}",
                # The `paper_fills.market_event_id` back-reference: the id of the
                # `paper_market_events` row this fill was priced from.
                "market_event_id": source_event_id_for(
                    EXCHANGES[0],
                    symbol,
                    "1m",
                    start + timedelta(seconds=index),
                    price,
                    quantity,
                ),
                "quantity": quantity,
                "price": price,
                "fee_minor": fee_minor,
                "slippage_minor": slippage_minor,
                "filled_at": start + timedelta(seconds=index),
            }
        )

    duplicate_indices: List[int] = []
    if with_duplicates and fills:
        duplicate_count = draw(st.integers(min_value=0, max_value=2))
        for _ in range(duplicate_count):
            source = draw(st.integers(min_value=0, max_value=len(fills) - 1))
            position = draw(st.integers(min_value=0, max_value=len(fills)))
            fills.insert(position, dict(fills[source]))
            duplicate_indices.append(position)

    notional = order_quantity * reference_price
    initial_balance = (notional * Decimal(2)).quantize(
        Decimal(1).scaleb(-MINOR_UNIT_EXPONENT), rounding=ROUND_HALF_EVEN
    )

    return {
        "config": config,
        "currency": "USD",
        "initial_balance": initial_balance,
        "order": order,
        "fills": fills,
        "duplicate_indices": sorted(duplicate_indices),
    }


# --------------------------------------------------------------------------------------
# Equity series
# --------------------------------------------------------------------------------------


@st.composite
def equity_series(
    draw: Any,
    *,
    min_size: int = 0,
    max_size: int = 12,
) -> List[Dict[str, Any]]:
    """An append-only `paper_equity_snapshots` run in non-decreasing `taken_at` order.

    Each snapshot is::

        {"series_index": int, "total_equity": Decimal, "available_balance": Decimal,
         "locked_balance": Decimal, "position_market_value": Decimal, "stale": bool,
         "cause": str, "taken_at": datetime}

    `total_equity == available_balance + locked_balance + position_market_value` holds as
    exact `Decimal` equality with zero tolerance, which is the identity
    `paper_accounting.invariants_hold` asserts (Requirement 18.3, P-25). `series_index` is
    `0..n-1` and `taken_at` never regresses, so the series is already in the order
    drawdown is computed from (Requirement 18.9, P-29). Series of length 0 and 1 are
    drawn, because max drawdown is zero below two snapshots.
    """
    count = draw(st.integers(min_value=min_size, max_value=max_size))
    taken_at = draw(utc_instants())

    snapshots: List[Dict[str, Any]] = []
    for index in range(count):
        if index:
            taken_at = taken_at + timedelta(
                seconds=draw(st.integers(min_value=0, max_value=3600))
            )
        available = _from_ticks(
            draw(st.integers(min_value=0, max_value=10**11)), MINOR_UNIT_EXPONENT
        )
        locked = _from_ticks(
            draw(st.integers(min_value=0, max_value=10**10)), MINOR_UNIT_EXPONENT
        )
        position_value = _from_ticks(
            draw(st.integers(min_value=0, max_value=10**11)), MINOR_UNIT_EXPONENT
        )
        if index == 0:
            cause = "SESSION_START"
        elif index == count - 1:
            cause = draw(st.sampled_from(["FILL", "FEE", "REVALUATION", "SESSION_STOP"]))
        else:
            cause = draw(st.sampled_from(["FILL", "FEE", "REVALUATION"]))
        snapshots.append(
            {
                "series_index": index,
                "total_equity": available + locked + position_value,
                "available_balance": available,
                "locked_balance": locked,
                "position_market_value": position_value,
                "stale": draw(st.booleans()),
                "cause": cause,
                "taken_at": taken_at,
            }
        )
    return snapshots
