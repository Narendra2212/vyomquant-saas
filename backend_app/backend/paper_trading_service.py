"""
backend/paper_trading_service.py - the Paper_Trading facade, on durable storage.

Spec: marketplace-subscriptions-paper-trading tasks 23.2 and 23.3. ``design.md`` -> "Paper
trading: persistence migration strategy". Requirements 16.8, 16.10, 16.11, 17.1, 17.2, 17.12,
18.1-18.8, 18.15, 13.6, 28.1, 28.3.

WHAT THIS MODULE IS NOW
-----------------------
The same facade, with the storage replaced. Every public method keeps its name, its argument
order and its return shape, and none of them holds a figure in process memory any more:

======================================  =====================================================
was                                     is
======================================  =====================================================
``self._accounts[uid]``                 ``paper_accounts`` where ``session_id IS NULL``
``self._positions[uid][symbol]``        ``paper_positions`` rows keyed ``(account_id, symbol)``
``self._orders[order_id]``              ``paper_orders`` rows
``self._trades[uid]`` list              ``paper_fills`` (every fill) + ``paper_trades`` (closed)
``self._idempotency_cache[key]``        ``paper_orders.idempotency_key`` + its unique indexes
``self._user_locks[uid]`` asyncio.Lock  the ``version`` optimistic protocol on the account row
``self._recalculate_account``           ``paper_accounting.recalculate`` (pure)
======================================  =====================================================

The module-level ``_paper_service_instance`` singleton and :func:`get_paper_trading_service`
stay, because ``routers/risk.py``, ``routers/paper_trading.py`` and
``backend/dashboard_aggregation_service.py`` all import the second by name.

THE DEFAULT ACCOUNT
-------------------
``paper_accounts`` where ``session_id IS NULL`` - the account the six existing ``/api/paper/*``
endpoints have always served. ``uq_paper_account_default`` keeps it single, and every read and
write below reaches it through ``paper/paper_repository.py``, which is the one module that
issues a statement against a ``paper_*`` table. Session-scoped accounts (Requirement 17.6) are
not this module's business; they are reached through ``/api/paper/sessions/*``.

WHY THE FIVE READS ARE STILL SYNCHRONOUS
---------------------------------------
``routers/risk.py`` calls ``get_performance_summary(uid)``, ``get_positions(uid)`` and
``get_or_create_account(uid)`` **synchronously from inside ``async def`` handlers** (lines
304, 309 and 357), and ``backend/dashboard_aggregation_service.py`` does the same in four
places. Making them ``await``-only would break all six call sites at once. So they keep
synchronous signatures and issue their statements through the synchronous ``supabase-py``
client, which is exactly why ``paper_repository`` is synchronous throughout. Only
``place_order``, ``cancel_order`` and ``check_limit_orders`` are ``async``, as they already
were.

REFUSAL, NOT DEGRADATION (Requirements 17.2, 28.3)
--------------------------------------------------
There is no in-memory fallback anywhere in this file. If ``paper_accounts`` does not exist,
``paper_repository.require_persistence`` raises the 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` that
names ``009_paper_trading.sql`` and this module lets it through untouched. If a statement DID
NOT COMPLETE, ``PaperPersistenceError`` becomes 503 ``PAPER_READ_FAILED`` - a different code,
because naming a migration that is in fact applied would send an operator to a file that
changes nothing. A balance served from process memory after this change would be the
fabricated figure Requirement 28.3 forbids, and Requirement 17.2's survive-a-restart guarantee
would read as true while being false.

WHERE THE ARITHMETIC LIVES
--------------------------
``paper/paper_accounting.py``, entirely. This module computes no balance, no equity, no PnL
and no position value: it reads rows, builds the engine's value objects, calls
``apply_fill`` / ``recalculate`` / ``lock`` / ``unlock`` / ``invariants_hold``, and writes what
comes back. The SHORT convention ``size * (2 * entry_price - price)`` that used to live in
``_recalculate_account`` is now ``paper_accounting.position_value``, unchanged.

Two consequences are deliberate improvements the engine brings with it, both of which change
figures the old implementation got wrong rather than figures it got right:

* **Zero tolerance.** ``verify_accounting_invariants`` compared with ``Decimal("0.05")``. It
  now asks ``paper_accounting.invariants_hold``, which is exact equality (Requirement 18.3).
* **A short close credits cash.** The old ``_execute_fill`` moved no cash at all when a short
  was closed, which the ``0.05`` tolerance was wide enough to hide. Cash is now the residual
  of the position-value change, so the equity identity holds for every one of the five fill
  cases by construction (Requirements 18.6, 18.7).

A fully closed position persists at ``size = 0`` with ``closed_at`` set (Requirement 18.5)
instead of being deleted from a dict, so its history survives; the open reads filter
``closed_at IS NULL``, which is what the deletion used to express structurally.

THE ADDITIVE RESPONSE FIELDS (task 23.3)
----------------------------------------
Five, and only five, on top of what each body already carried:

* ``execution_environment: "PAPER"`` (Requirement 13.6) and ``is_simulated: true``
  (Requirement 28.1) on every body, so no consumer can mistake a simulated figure for a live
  one.
* ``session_id: null`` on every default-account body - the honest name for "this is not a
  Paper_Session".
* ``stale`` and ``last_price_at`` (Requirement 18.15) beside every figure derived from a
  market price: ``unrealized_pnl``, a position's market value, ``total_equity`` and
  ``current_price``. ``stale`` is ``true`` when a figure was computed from the last validated
  price recorded on the position rather than from a price supplied with this call, which is
  the honest reading of a read path that has no live feed attached to it yet (task 24.x adds
  one). No price is ever synthesised, interpolated or defaulted to zero.

No existing key is removed, renamed, retyped or re-meaninged.
``tests/test_paper_api_shape_compatibility.py`` and
``tests/regression/test_baseline_unchanged.py`` are what hold that claim.

WHAT IS PERSISTED AND WHAT IS NOT
---------------------------------
``strategy_id`` and ``deployment_id`` are reported as ``null`` on every order and fill,
because ``paper_orders`` has no column for either - it has ``signal_id``, which is a different
thing - and this file will not invent a value it cannot store. That is exactly what the
pre-change baseline recorded for them (``"strategy_id": "null"``), so no response type
changes; what changes is that a caller which passes ``strategy_id`` into ``place_order`` no
longer sees it echoed back. It is still evaluated: the strategy-level risk limits below read
the argument. Recorded in the task report as a gap that needs an additive column, not papered
over here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from backend_app.backend.paper import paper_accounting as accounting
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper.errors import (
    PAPER_CONCURRENCY_CONFLICT,
    PAPER_INVARIANT_VIOLATION,
    PaperError,
    paper_persistence_unavailable,
    paper_read_failed,
)
from backend_app.backend.paper.paper_accounting import Account, AccountingConfig, Position
from backend_app.backend.paper.paper_order_state import PaperOrderState

logger = logging.getLogger("PaperTradingService")


# ══════════════════════════════════════════════════════════════════════════
# THE RETAINED ORDER STATUS VOCABULARY (Requirement 17.12)
# ══════════════════════════════════════════════════════════════════════════


class PaperOrderStatus(str, Enum):
    """The five spellings ``GET /api/paper/orders?status=…`` has always filtered on.

    **Retained, not replaced.** ``paper_orders.order_state`` stores the six canonical
    Paper_Order_State values and ``paper_orders.legacy_status`` carries the value from
    ``paper_order_state.LEGACY_STATUS_FOR_STATE``, which is written by the repository from the
    state and never passed to it. ``ACCEPTED`` and ``PARTIALLY_FILLED`` both map to ``OPEN``
    and ``CREATED`` maps to ``NEW``, so ``?status=OPEN`` keeps its exact meaning - an order
    live on the book, whether untouched or partly filled.
    """

    NEW = "NEW"
    OPEN = "OPEN"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


#: Requirement 13.6: every paper body says which Execution_Environment produced it.
PAPER_EXECUTION_ENVIRONMENT = "PAPER"

#: The two constants the account body has always carried. Neither is a measurement and neither
#: has a column: they are literals this endpoint returned before the repoint and returns after
#: it, kept so no consumer loses a field (Requirement 17.12).
ACCOUNT_NAME = "VyomQuant Paper Trading Account"
ACCOUNT_STATUS = "active"

#: PAPER-02: there is deliberately NO reference-price table. A manual market order fills at a
#: *measured, fresh* price or is refused; a limit order carries the caller's limit price (that is
#: the order's own content, not a fill price). See :func:`get_fresh_market_price`.

#: ``place_order`` takes a bare ``symbol`` with no exchange - a manual order on the user's
#: default paper account was never tied to one venue the way a Paper_Session is
#: (``paper/paper_market_feed.PaperFeedConfig.exchange_id`` is always caller-supplied; this
#: facade has no equivalent field anywhere). "binance" is not an invented default: it is the
#: SAME fallback ``mds/main.py::handle_commands`` already uses when a subscribe command names no
#: exchange (``data.get("exchange", "binance")``), so a bare symbol here reads the identical
#: venue MDS itself falls back to.
PAPER_PRICE_SOURCE_EXCHANGE = "binance"


def _paper_price_exchange() -> str:
    """The venue whose last-price cache this service reads.

    PAPER-02 FIX. This was the module constant ``PAPER_PRICE_SOURCE_EXCHANGE`` used
    directly, hardcoded to ``"binance"``. A Paper_Session runs on ``DEFAULT_EXCHANGE``
    (``routers/paper_trading.py::_paper_venue``), and ``mds/main.py`` keys its cache on
    whatever exchange the subscribe command named -- so on any deployment whose
    ``DEFAULT_EXCHANGE`` is not binance, the sessions wrote keys this reader never looked
    at, and every manual market order refused no matter what was streaming.

    ``DEFAULT_EXCHANGE`` first, then the same ``"binance"`` fallback
    ``mds/main.py::handle_commands`` itself uses for a subscribe command that names no
    exchange (``data.get("exchange", "binance")``) -- so a bare symbol still reads the
    identical venue MDS falls back to, which is what the constant was for.
    """
    return (os.environ.get("DEFAULT_EXCHANGE") or PAPER_PRICE_SOURCE_EXCHANGE).strip().lower()


def _venue_symbol(symbol: str) -> str:
    """``BTC-USDT`` -> ``BTC/USDT``: the form the VENUE and MDS key on.

    PAPER-02 FIX, AND THE REASON THE PREVIOUS FIX WAS INERT
    -------------------------------------------------------
    ``place_order`` normalises its argument with :meth:`PaperTradingService._clean_symbol`,
    which is ``upper().replace("/", "-")`` -- the platform's own canonical internal form,
    used by every ``paper_orders`` and ``paper_positions`` row. It then looked the price
    cache up under THAT form. But ``mds/main.py`` keys the cache on the symbol exactly as
    the subscribe command named it, and for a Paper_Session that is the ccxt market symbol
    ``BTC/USDT`` (``PaperFeedConfig.symbol``, from ``resolve_market_metadata``).

    So the writer wrote ``mds:last_price:binance:BTC/USDT`` and the reader read
    ``mds:last_price:binance:BTC-USDT``. The keys could never match, which means a manual
    market order could never fill EVEN FOR A SYMBOL AN ACTIVE PAPER_SESSION WAS STREAMING
    -- the case PAPER-02's previous fix was recorded as having closed. Every manual market
    order refused with ``NO_FRESH_PRICE``.

    The existing tests did not catch it because they call ``get_fresh_market_price``
    directly with an already-slashed ``"BTC/USDT"``, bypassing ``_clean_symbol`` -- so they
    exercised the key format (which was always consistent between writer and reader) and
    never the symbol form that reaches it from a real order.
    """
    return str(symbol).strip().upper().replace("-", "/")


#: PAPER-02. How long a manual order may wait for an on-demand venue quote before refusing.
#:
#: MEASURED against the live venue (kiro-session-artifacts/measure_ticker_latency.py),
#: ccxt binance ``fetch_ticker``, 36 warm calls across BTC/ETH/DOGE, zero failures:
#:
#:     median 201 ms | p95 ~516 ms | p99 516 ms | max 516 ms
#:     cold first call, including TLS and market loading: 6,105 ms
#:
#: 3 s is ~5.8x the measured warm p99, so an ordinary resolve has ample headroom, while the
#: 6.1 s cold-start case deliberately does NOT fit: the first order in a fresh process
#: refuses honestly instead of holding an HTTP worker for six seconds, and the pooled client
#: it leaves behind makes the next one warm. A refusal here is always safe -- it is the
#: existing ``NO_FRESH_PRICE`` answer, and PAPER-02's whole point is that refusing beats
#: inventing a price.
ON_DEMAND_QUOTE_TIMEOUT_SECONDS = float(
    os.environ.get("PAPER_ONDEMAND_QUOTE_TIMEOUT_SECONDS", "3.0")
)


def _last_price_key(exchange_id: str, symbol: str) -> str:
    """Spelled identically to ``mds/main.py::last_price_key`` - a literal, not a shared import,
    the same choice this codebase already made for ``TRANSPORT_WEBSOCKET``/``TRANSPORT_REST``
    (see that file's own comment): ``mds/main.py`` is a standalone microservice process and
    importing it into this backend package, or vice versa, would couple the two deployments.
    ``tests/test_paper02_fresh_market_price.py`` asserts the two spellings agree."""
    return f"mds:last_price:{str(exchange_id).strip().lower()}:{str(symbol).strip()}"


async def get_fresh_market_price(symbol: str) -> Optional[Decimal]:
    """Return a fresh measured last price for ``symbol``, or ``None`` when there is none.

    PAPER-02. Reads ``mds/main.py::broadcast_ohlcv``'s own last-price cache - the SAME publish
    loop that feeds every Paper_Session's validated feed, not a second market-data path. The key
    format is spelled identically in both files (``mds/main.py`` is a standalone microservice
    process and cannot import this backend package; see its own comment on this) and the two are
    asserted equal by a test.

    ``None`` is the honest, and the ONLY, answer for every case other than a real, unexpired
    cached price: no subscriber has ever been active for this symbol; the one that was has gone
    quiet past the TTL (the stream died, or the exchange stopped sending); the cached payload is
    unreadable. None of these is a reason to fabricate a price, so none of them raises either -
    the caller (``place_order``) already treats ``None`` as its refusal signal
    (``PaperMarketDataUnavailable``).

    THE COVERAGE GAP THIS DOES NOT CLOSE, ON PURPOSE
    -------------------------------------------------
    This reads a cache; it never SUBSCRIBES one. A symbol no Paper_Session (or anything else) is
    currently streaming has nothing cached and this correctly, safely refuses the order - it does
    not fall back to an on-demand subscribe-and-wait. That omission is deliberate, not an
    oversight: ``watch_ohlcv`` on a 1-minute timeframe can legitimately take up to a minute to
    yield its first candle depending on the exchange, which is not a bound this synchronous,
    inside-an-HTTP-request call can safely wait on without real-exchange timing data this sandbox
    has no way to measure. See the ledger for RT-04's identical reasoning about not fabricating a
    measurement nobody made.
    """
    from backend_app.core.cache.redis_manager import redis_manager

    # PAPER-02 FIX: look the symbol up in the form the WRITER keys on, and accept either
    # spelling. `_venue_symbol` is first because that is what a Paper_Session subscribes
    # with (and therefore what mds/main.py keys on); the caller's own spelling is tried
    # second because `data_seeking_engine.stream_live_ohlcv` is the other subscriber and
    # nothing normalises what it is handed. See `_venue_symbol` for why reading only the
    # caller's form made this function unable to return a price at all.
    exchange_id = _paper_price_exchange()
    candidates = []
    for form in (_venue_symbol(symbol), str(symbol).strip()):
        key = _last_price_key(exchange_id, form)
        if key not in candidates:
            candidates.append(key)

    raw = None
    try:
        for key in candidates:
            raw = await redis_manager.redis.get(key)
            if raw is not None:
                break
    except Exception:
        logger.warning("get_fresh_market_price: cache read failed for %s", symbol, exc_info=True)
        return None

    if raw is None:
        return None

    try:
        payload = json.loads(raw)
        price = payload.get("price")
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.warning("get_fresh_market_price: unreadable cached payload for %s", symbol)
        return None

    if price is None:
        return None

    try:
        return Decimal(str(price))
    except Exception:
        logger.warning("get_fresh_market_price: non-numeric cached price for %s: %r", symbol, price)
        return None


async def fetch_venue_quote(
    symbol: str,
    exchange_id: Optional[str] = None,
    *,
    timeout: Optional[float] = None,
) -> Optional[Decimal]:
    """One bounded REST quote from the venue, or ``None``. PAPER-02's on-demand resolve.

    WHY THIS IS A REST ONE-SHOT AND NOT A SUBSCRIBE-AND-WAIT
    --------------------------------------------------------
    PAPER-02's recorded blocker was that an on-demand subscribe could not be bounded:
    "``watch_ohlcv`` on a 1-minute timeframe can legitimately take up to a minute to yield
    its first candle ... not a bound a synchronous, inside-an-HTTP-request order-placement
    call can safely wait on without real-exchange timing data".

    That is true of ``watch_ohlcv``, and it is the wrong thing to wait on. A candle stream
    withholds its first value until a bar CLOSES; a ticker read returns the venue's current
    last price immediately. So the premise is sidestepped rather than measured around: this
    asks for a price, not for a candle. Measured warm cost is ~200 ms median and 516 ms p99
    (see :data:`ON_DEMAND_QUOTE_TIMEOUT_SECONDS`), which an HTTP handler can wait on.

    This is NOT a second market-data path in the sense Requirement 14.2 forbids. It is the
    same REST read ``mds/main.py::broadcast_ohlcv`` already falls back to, reached through
    the platform's single venue-client factory (``connection_engine.get_or_create_exchange``
    under the public, credential-less pool identity that ``routers/market.py`` already uses
    for public market data). No new credentials, no new connection management, no socket.

    THE MOCK REFUSAL IS NOT OPTIONAL HERE
    -------------------------------------
    ``connection_engine._apply_mock_interface``'s ``fetch_ticker`` returns a HARDCODED
    price. Filling from it would reinstate PAPER-02's original defect by a new route, so a
    mock-served connection returns ``None`` -- the same check ``paper_market_feed.open_feed``
    makes for Requirement 14.8.

    Returns ``None``, never raises, for every failure: unreachable venue, timeout, unknown
    symbol, mocked interface, missing ``last``. The caller treats ``None`` as its refusal
    signal, and refusing is always safe.
    """
    venue = (exchange_id or _paper_price_exchange()).strip().lower()
    wait = ON_DEMAND_QUOTE_TIMEOUT_SECONDS if timeout is None else float(timeout)
    market_symbol = _venue_symbol(symbol)

    try:
        from backend_app.backend.asset_universe import markets_are_mocked
        from backend_app.backend.connection_engine import get_or_create_exchange

        exchange = await asyncio.wait_for(
            get_or_create_exchange(
                user_id=_PUBLIC_QUOTE_POOL_IDENTITY,
                exchange_id=venue,
                api_key=None,
                secret_key=None,
            ),
            timeout=wait,
        )
        if exchange is None:
            return None

        if markets_are_mocked(exchange):
            logger.warning(
                "fetch_venue_quote: the resolved %s connection is the DEV_MODE mock "
                "interface, whose prices are hardcoded constants. Refusing to quote %s "
                "from it -- paper trading never fills at an invented price.",
                venue,
                market_symbol,
            )
            return None

        ticker = await asyncio.wait_for(
            exchange.fetch_ticker(market_symbol), timeout=wait
        )
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logger.warning(
            "fetch_venue_quote: %s did not answer for %s within %.1fs; refusing rather "
            "than waiting longer inside a request",
            venue,
            market_symbol,
            wait,
        )
        return None
    except Exception:
        logger.warning(
            "fetch_venue_quote: could not read a %s quote for %s",
            venue,
            market_symbol,
            exc_info=True,
        )
        return None

    last = None
    if isinstance(ticker, Mapping):
        last = ticker.get("last")
        if last is None:
            last = ticker.get("close")
    if last is None:
        logger.warning(
            "fetch_venue_quote: %s returned a ticker for %s with no last price",
            venue,
            market_symbol,
        )
        return None

    try:
        quote = Decimal(str(last))
    except Exception:
        logger.warning(
            "fetch_venue_quote: non-numeric last price for %s: %r", market_symbol, last
        )
        return None

    return quote if quote > 0 else None


#: The pool identity a public, credential-less market-data client is cached under. The same
#: constant `routers/market.py` uses for the same purpose: public market data needs no key,
#: and a shared identity means one pooled client per venue rather than one per user.
_PUBLIC_QUOTE_POOL_IDENTITY = "__public_market_data__"


async def _warm_the_price_cache(symbol: str, exchange_id: str) -> None:
    """Best-effort ``subscribe`` to ``mds:commands`` so the NEXT order reads the cache.

    This is the "subscribe" half of the subscribe-and-wait PAPER-02 deliberately did not
    build, with the waiting removed -- which is what makes it safe to do inside a request.
    Nothing is awaited on the result: this order has already been priced from
    :func:`fetch_venue_quote`, and the subscription exists so subsequent orders for the same
    symbol are served from ``mds:last_price`` at no venue cost.

    Reuses ``paper_market_feed.mds_command`` rather than spelling the wire format a third
    time -- that builder exists precisely so there is ONE place that knows the shape of this
    contract. Failures are logged and swallowed: a cache that did not get warmed costs the
    next order one REST read, which is not a reason to fail an order that is already priced.
    """
    try:
        from backend_app.backend.paper.paper_market_feed import (
            MDS_ACTION_SUBSCRIBE, MDS_COMMAND_CHANNEL, mds_command)
        from backend_app.core.cache.redis_manager import redis_manager

        await redis_manager.redis.publish(
            MDS_COMMAND_CHANNEL,
            mds_command(MDS_ACTION_SUBSCRIBE, exchange_id, _venue_symbol(symbol)),
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug(
            "_warm_the_price_cache: could not publish a subscribe for %s; the next order "
            "will resolve over REST again",
            symbol,
            exc_info=True,
        )


async def resolve_market_price(symbol: str) -> Optional[Decimal]:
    """A measured price for ``symbol``, from the cache or from the venue. Or ``None``.

    PAPER-02's complete answer, in the order that costs least:

    1. :func:`get_fresh_market_price` -- the ``mds:last_price`` cache a Paper_Session or any
       other MDS subscriber has already warmed. Free, and the common case.
    2. :func:`fetch_venue_quote` -- a bounded REST one-shot for a symbol nothing is
       streaming. This is the coverage gap PAPER-02 recorded as deliberately unbuilt.
    3. :func:`_warm_the_price_cache` -- tell MDS to start streaming the pair, so step 1
       answers next time. Fire-and-forget; this order is already priced.

    ``get_fresh_market_price`` is called through the module global on purpose, so the
    ``fresh_paper_quote`` fixture (``tests/conftest.py``) keeps working unchanged for the
    ~13 suites that rely on it: patching that name still short-circuits this function at
    step 1 and never reaches the venue.
    """
    cached = await get_fresh_market_price(symbol)
    if cached is not None:
        return cached

    exchange_id = _paper_price_exchange()
    quote = await fetch_venue_quote(symbol, exchange_id)
    if quote is None:
        return None

    await _warm_the_price_cache(symbol, exchange_id)
    logger.info(
        "resolve_market_price: %s had no cached price; resolved %s from %s over REST and "
        "asked MDS to start streaming it",
        symbol,
        quote,
        exchange_id,
    )
    return quote


#: Decimal places for money (USD minor units), for a price and for a quantity. The first is
#: what every balance the old implementation reported was quantized to (``Decimal("0.01")``),
#: and the second is what it quantized a fill price and a weighted-average entry to
#: (``Decimal("0.00000001")``). Retained so the repoint does not move a reported figure.
MONEY_EXPONENT = 2
PRICE_PRECISION = 8
QUANTITY_PRECISION = 8


class PaperTradingService:
    """The Paper_Trading facade. Holds configuration; holds no figure.

    Every method takes the identity it operates on and reaches the Persistence_Layer through
    ``paper_repository``. The only mutable state on an instance is the configuration passed to
    ``__init__`` and the optional bound Persistence_Layer handle, which exists so a worker, a
    test or the regression capture harness can name the client it wants used.
    """

    def __init__(
        self,
        default_capital: float = 100_000.0,
        default_fee_rate: float = 0.001,
        default_slippage: float = 0.0005,
        supabase: Any = None,
    ):
        # ``str`` first, deliberately: ``Decimal(0.001)`` is not one thousandth, while
        # ``Decimal("0.001")`` is. These three arrive as ``float`` from the existing callers'
        # signatures, and this is the one boundary where that transport value becomes an exact
        # decimal. Nothing downstream ever sees a ``float`` - ``paper_accounting.to_decimal``
        # refuses one outright (Requirement 18.1).
        self.default_capital = Decimal(str(default_capital))
        self.default_fee_rate = Decimal(str(default_fee_rate))
        #: Kept as the ``float`` it always was, because it is part of this class's published
        #: surface; the exact value used in arithmetic is ``self.config.slippage_rate``.
        self.default_slippage = default_slippage

        #: The frozen accounting configuration. One rounding mode, one cost basis, one set of
        #: scales, applied to every computation (Requirements 18.2, 18.8).
        self.config = AccountingConfig(
            fee_rate=self.default_fee_rate,
            slippage_rate=Decimal(str(default_slippage)),
            rounding_mode=ROUND_HALF_EVEN,
            cost_basis=accounting.WEIGHTED_AVERAGE,
            price_precision=PRICE_PRECISION,
            quantity_precision=QUANTITY_PRECISION,
            minor_unit_exponent=MONEY_EXPONENT,
            market_type=None,
        )

        self._supabase = supabase

    # ══════════════════════════════════════════════════════════════════
    # THE PERSISTENCE_LAYER HANDLE
    # ══════════════════════════════════════════════════════════════════

    def bind_persistence(self, supabase: Any) -> None:
        """Use ``supabase`` for every statement from now on. ``None`` unbinds.

        The seam a worker, a test and ``tests/regression/capture_baseline.py`` use to name the
        Persistence_Layer they want. It is not a cache and holds no row: only the handle.
        """
        self._supabase = supabase

    def persistence_client(self, access_token: Optional[str] = None) -> Any:
        """:meth:`_client` under a public name, for a caller outside this class.

        The Paper_Session routes (task 28.1) issue their statements through
        ``paper/paper_session_service`` and ``paper/paper_repository`` rather than through this
        service, but they must reach the SAME handle by the same resolution rule - the bound
        one first, then the platform's own service-role client, then the anon singleton - or a
        test that bound a Persistence_Layer here would find the session routes talking to a
        different one.

        Raises:
            PaperError: ``PAPER_PERSISTENCE_UNAVAILABLE`` when no handle can be obtained.
        """
        return self._client(access_token)

    def _client(self, access_token: Optional[str] = None) -> Any:
        """The Persistence_Layer handle for this call.

        PAPER-03: every statement this facade issues, read or write, now goes through the
        platform's own service-role credential - never a per-request client carrying the
        caller's JWT. ``access_token`` is still accepted, for call-site compatibility with the
        several routers that pass ``user.get("access_token")``, but it no longer selects a
        client.

        Before this fix, rule #2 below was ``create_request_supabase(access_token)``: a client
        scoped to the caller's own identity, which ``auth.uid()`` resolved against the
        ``*_owner_access`` row-level-security policies ``009_paper_trading.sql`` pairs with a
        GRANT of SELECT, INSERT (and, on most tables, UPDATE) to ``authenticated``. Those
        policies check only row OWNERSHIP (``owner_id = auth.uid()`` or equivalent) - they
        cannot and do not check that the values being written are the ones this module's own
        order-matching, risk-gating and accounting logic computed. Any trader's own JWT could
        therefore issue an unmediated PostgREST call against their own ``paper_*`` rows -
        fabricating a balance, a fill price, a position size, or an order's state - skipping
        every check ``place_order``/``cancel_order`` perform. Migration 043 revokes the WRITE
        half of that grant; this is the code half, so the migration does not break a write path
        that was supposed to keep working. (Every read already restates its own ``user_id``/
        ``account_id`` predicate in-process - see ``paper_repository.read_account`` - so moving
        off RLS is not a new scoping gap: those explicit predicates, not RLS, were already what
        made a read return the right row.)

        This is the same move PAPER-04 already made for the bot-driven session loop, which binds
        ``get_service_supabase()`` directly before it can outlive one request's JWT; this
        extends it to the manual, HTTP-driven order path and every other caller that reaches
        this method without a handle already bound.

        Three sources, in descending order of authority:

        1. the handle bound by :meth:`bind_persistence` (how every existing test, and the
           bot-driven session loop, already supply a double or a service client);
        2. the platform's own service-role client (``get_service_supabase``), for a real request
           that bound nothing;
        3. the module-level anon singleton, reached only when service-role credentials are not
           configured at all (``DEV_MODE``'s local-run fallback).

        Raises:
            PaperError: ``PAPER_PERSISTENCE_UNAVAILABLE`` when no handle can be obtained at
                all. With no handle there is no storage, and the alternative would be to
                proceed as though there were - which is the memory fallback this whole task
                exists to remove (Requirements 17.2, 28.3).
        """
        if self._supabase is not None:
            return self._supabase

        client = None
        try:
            from backend_app.core.dependencies import get_service_supabase

            client = get_service_supabase()
        except Exception as exc:  # noqa: BLE001 - falls through to the anon singleton
            logger.warning(
                "The platform service-role Supabase client could not be constructed for a "
                "paper statement; falling back to the anon singleton. Detail: %s",
                exc,
            )
        if client is None:
            try:
                from backend_app.core.dependencies import get_supabase

                client = get_supabase()
            except Exception as exc:  # noqa: BLE001 - classified as an outage below
                logger.error("No Supabase handle is available for paper trading: %s", exc)
                client = None

        if client is None:
            raise paper_persistence_unavailable(repo.PAPER_TRADING_MIGRATION_FILE)
        return client

    # ══════════════════════════════════════════════════════════════════
    # FAILURE TRANSLATION
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _read_failed(exc: BaseException, what: str) -> PaperError:
        """``PaperPersistenceError`` -> 503 ``PAPER_READ_FAILED``, with the detail logged.

        The statement did not complete, so there is no answer - and "no answer" must not be
        served as an empty list or a zero balance. The driver's message goes to the log and
        never into ``details``, which ``redact_details`` would scrub anyway.
        """
        logger.error("A paper %s did not complete: %s", what, exc)
        return paper_read_failed()

    @staticmethod
    def _conflict(
        exc: repo.PaperConcurrencyConflict,
        *,
        phase: Optional[str] = None,
        partial_write: Optional[bool] = None,
    ) -> PaperError:
        """A version- or state-guarded write that matched no row -> 409.

        Requirement 16.10's answer to a lost race. The caller learns it lost instead of being
        told the write succeeded.

        ``phase`` and ``partial_write`` carry the residue into the response rather than
        leaving it to be discovered. This is ``paper_simulator``'s convention, asserted for
        the session path by
        ``test_submit_intents_lock_gives_up_with_the_phase_and_the_partial_write_named``:
        "PostgREST offers no ``ROLLBACK`` for the two statements that committed. So the 409
        names ``phase`` and ``partial_write``... which is visible in the response instead of
        silent." On this path ``phase='FILL'`` with ``partial_write=True`` means one unsettled
        ``paper_fills`` row exists for the order - PAPER-04 - and it is the thing a
        reconciliation query should look for.
        """
        logger.warning(
            "A paper write lost its optimistic guard%s: %s",
            f" during {phase}" if phase else "",
            exc,
        )
        details: Dict[str, Any] = {}
        if phase is not None:
            details["phase"] = phase
        if partial_write is not None:
            details["partial_write"] = partial_write
        return PaperError(PAPER_CONCURRENCY_CONFLICT, details=details or None)

    # ══════════════════════════════════════════════════════════════════
    # SMALL HELPERS
    # ══════════════════════════════════════════════════════════════════

    def _get_timestamp(self) -> str:
        """The current instant in UTC as an ISO-8601 string. Retained name and shape."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _exact(value: Any, what: str) -> Decimal:
        """A transport value as an exact ``Decimal``.

        A ``float`` arrives from the HTTP boundary (``PaperOrderRequest.quantity`` is a
        ``float`` field and cannot change without changing the request schema), and this is
        the single place it becomes exact - through ``str``, so ``0.07`` becomes
        ``Decimal("0.07")`` and not the binary approximation. Everything downstream sees only
        ``Decimal`` and ``paper_accounting.to_decimal`` refuses a ``float`` outright
        (Requirement 18.1).
        """
        if isinstance(value, Decimal):
            return value
        if isinstance(value, bool) or value is None:
            raise ValueError(f"{what} must be a number, got {value!r}")
        return Decimal(str(value))

    def _minor(self, amount: Decimal, column: str) -> int:
        """A money amount as exact integer Minor_Units.

        The amount is quantized to the currency's minor unit first, so the integer is exact
        rather than rounded: a fee is a whole number of minor units or it is not a fee
        (Requirement 18.2).
        """
        quantized = self.config.money(amount)
        scaled = quantized.scaleb(MONEY_EXPONENT)
        if scaled != scaled.to_integral_value():  # pragma: no cover - defensive
            raise ValueError(f"{column} is not an exact number of minor units: {amount}")
        return int(scaled)

    @staticmethod
    def _fingerprint(
        user_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Optional[Decimal],
    ) -> str:
        """A stable digest of the intent, for ``paper_orders.fingerprint``.

        ``NOT NULL`` on the column, and what task 25.3 compares when the same idempotency key
        arrives twice: the same key with a *different* intent is a conflict, not a duplicate
        (Requirement 16.11). Deterministic - a hash of the canonical intent - so it is
        reproducible across processes and carries no clock and no random draw.
        """
        canonical = "|".join(
            [
                user_id,
                symbol,
                side,
                order_type,
                str(quantity),
                "" if price is None else str(price),
            ]
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _text(value: Any) -> Optional[str]:
        return None if value is None else str(value)

    @staticmethod
    def _clean_symbol(symbol: str) -> str:
        return str(symbol).strip().upper().replace("/", "-")

    # ══════════════════════════════════════════════════════════════════
    # ROWS -> THE ACCOUNTING ENGINE'S VALUE OBJECTS
    # ══════════════════════════════════════════════════════════════════

    def _account_of(self, row: Mapping[str, Any]) -> Account:
        """A ``paper_accounts`` row as an :class:`Account`.

        ``total_equity`` is carried across as stored so that
        :meth:`verify_accounting_invariants` can *check* it against the sum. Nothing here
        recomputes it to store it (Requirement 18.3).
        """
        return Account(
            available_balance=accounting.to_decimal(
                row.get("available_balance"), "available_balance"
            ),
            locked_balance=accounting.to_decimal(row.get("locked_balance"), "locked_balance"),
            realized_pnl=accounting.to_decimal(row.get("realized_pnl"), "realized_pnl"),
            total_equity=accounting.to_decimal(row.get("total_equity"), "total_equity"),
            currency=str(row.get("currency") or repo.DEFAULT_CURRENCY),
        )

    def _positions_of(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Tuple[Dict[str, Position], Dict[str, Mapping[str, Any]]]:
        """``paper_positions`` rows as ``{symbol: Position}``, plus the rows by symbol.

        The rows are kept beside the value objects because the response body reports the row's
        own ``created_at``, ``updated_at`` and ``price_at``, which the engine's value object
        has no place for and must not invent.
        """
        positions: Dict[str, Position] = {}
        by_symbol: Dict[str, Mapping[str, Any]] = {}
        for row in rows:
            symbol = str(row.get("symbol"))
            if symbol in positions and not positions[symbol].is_closed:
                # An OPEN row already stands for this symbol. ``uq_paper_position_open`` allows
                # at most one, while closed rows accumulate as history, so a later closed row
                # must not displace the open one - the fill path decides what to do from
                # whichever row it finds here.
                continue
            positions[symbol] = Position(
                symbol=symbol,
                side=str(row.get("side")),
                size=accounting.to_decimal(row.get("size"), "size"),
                entry_price=accounting.to_decimal(row.get("entry_price"), "entry_price"),
                current_price=(
                    None
                    if row.get("current_price") is None
                    else accounting.to_decimal(row.get("current_price"), "current_price")
                ),
                unrealized_pnl=(
                    None
                    if row.get("unrealized_pnl") is None
                    else accounting.to_decimal(row.get("unrealized_pnl"), "unrealized_pnl")
                ),
                price_at=row.get("price_at"),
                opened_at=row.get("opened_at"),
                closed_at=row.get("closed_at"),
            )
            by_symbol[symbol] = row
        return positions, by_symbol

    # ══════════════════════════════════════════════════════════════════
    # VALUATION (Requirements 18.3, 18.8, 18.15)
    # ══════════════════════════════════════════════════════════════════

    def _figures(
        self,
        account: Account,
        positions: Mapping[str, Position],
        prices: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Revalue the book and report what is price-derived, and whether it is stale.

        The replacement for ``_recalculate_account``, and it writes nothing: the arithmetic is
        ``paper_accounting.recalculate`` and the prices are **arguments**. The old
        implementation fell back to the position's own ``current_price`` and reported the
        result as current, which is what let a pre-disconnection price be presented as a fresh
        one.

        ``stale`` is ``true`` when any open position had to be valued at the last validated
        price recorded on its row rather than at a price supplied with this call (Requirement
        18.15), and ``last_price_at`` is the newest such timestamp. A book with no open
        position has no price-derived figure at all, so it is not stale.

        Returns:
            ``{"positions", "total_equity", "unrealized_pnl", "position_market_value",
            "stale", "last_price_at"}``.
        """
        supplied: Dict[str, Decimal] = {
            str(symbol): self._exact(price, f"price for {symbol}")
            for symbol, price in dict(prices or {}).items()
            if price is not None
        }
        merged: Dict[str, Decimal] = dict(
            accounting.last_validated_prices(positions.values())
        )
        merged.update(supplied)

        open_symbols = [p.symbol for p in positions.values() if p.is_open]
        stale = any(symbol not in supplied for symbol in open_symbols)
        last_price_at = accounting.latest_price_at(positions.values())

        try:
            valuation = accounting.recalculate(account, positions, merged, self.config)
        except accounting.StalePrice as exc:
            # No validated price exists for an open position at all - not even a recorded one.
            # Requirement 18.15 forbids synthesising, interpolating or zeroing it, so the
            # price-derived figures are reported exactly as they were last persisted and the
            # answer says so. Unreachable for a row this service wrote: every fill records the
            # price it filled at.
            logger.warning(
                "A paper position has no validated price, so the equity figures are reported "
                "as last persisted and marked stale. Detail: %s",
                exc,
            )
            persisted_unrealized = sum(
                (p.unrealized_pnl for p in positions.values() if p.unrealized_pnl is not None),
                Decimal("0"),
            )
            return {
                "positions": positions,
                "total_equity": account.total_equity,
                "unrealized_pnl": persisted_unrealized,
                "position_market_value": account.total_equity - account.cash,
                "stale": True,
                "last_price_at": self._text(last_price_at),
            }

        return {
            "positions": valuation.positions,
            "total_equity": valuation.account.total_equity,
            "unrealized_pnl": valuation.unrealized_pnl,
            "position_market_value": valuation.position_market_value,
            "stale": stale,
            "last_price_at": self._text(last_price_at),
        }

    # ══════════════════════════════════════════════════════════════════
    # THE RESPONSE BODIES - EVERY EXISTING KEY, PLUS THE FIVE ADDITIVE ONES
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _paper_provenance(stale: Any = None, last_price_at: Any = None) -> Dict[str, Any]:
        """The additive fields of task 23.3, and nothing else.

        ``execution_environment`` and ``is_simulated`` on every body (Requirements 13.6, 28.1);
        ``session_id: null`` because the default account is not a Paper_Session; ``stale`` and
        ``last_price_at`` **only** where the body carries a figure derived from a market price
        (Requirement 18.15) - an order's quantity and limit price are recorded facts, not
        market-derived ones, so they get neither.
        """
        provenance: Dict[str, Any] = {
            "execution_environment": PAPER_EXECUTION_ENVIRONMENT,
            "is_simulated": True,
            "session_id": None,
        }
        if stale is not None:
            provenance["stale"] = bool(stale)
            provenance["last_price_at"] = last_price_at
        return provenance

    def _account_body(
        self,
        row: Mapping[str, Any],
        figures: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """The ``/api/paper/account`` body: the thirteen keys it has always carried.

        The stored money columns are reported **as stored**, unchanged, because that is what
        makes "what the row says is what the endpoint says" checkable. ``unrealized_pnl`` and
        ``total_equity`` are the computed figures - the first has no column at all, and the
        second is recomputed rather than trusted so that Requirement 18.3's identity is what
        the reader sees.
        """
        body = {
            "account_id": str(row.get("id")),
            "user_id": str(row.get("user_id")),
            "name": ACCOUNT_NAME,
            "currency": str(row.get("currency") or repo.DEFAULT_CURRENCY),
            "initial_capital": str(row.get("initial_capital")),
            "available_balance": str(row.get("available_balance")),
            "locked_balance": str(row.get("locked_balance")),
            "total_equity": str(figures["total_equity"]),
            "realized_pnl": str(row.get("realized_pnl")),
            "unrealized_pnl": str(figures["unrealized_pnl"]),
            "status": ACCOUNT_STATUS,
            "created_at": str(row.get("created_at")),
            "updated_at": str(row.get("updated_at")),
        }
        body.update(
            self._paper_provenance(figures["stale"], figures["last_price_at"])
        )
        return body

    def _position_body(
        self,
        row: Mapping[str, Any],
        position: Position,
        stale: bool,
    ) -> Dict[str, Any]:
        """One element of ``/api/paper/positions``: the eight keys it has always carried.

        ``side`` is lower-cased back to ``'long'`` / ``'short'``, which is the spelling the
        response has always used and what ``Portfolio.jsx`` and
        ``dashboard_aggregation_service`` read; ``paper_positions.side`` stores ``LONG`` /
        ``SHORT`` because ``chk_paper_position_side`` says so.

        ``current_price`` and ``unrealized_pnl`` are ``null`` in the one case where the row
        carries no validated price - an absent price stays absent (Requirement 18.15). No path
        in this service produces such a row: a position exists only as the result of a fill,
        and a fill records the price it filled at.
        """
        body = {
            "symbol": position.symbol,
            "side": str(position.side).lower(),
            "size": str(position.size),
            "entry_price": str(position.entry_price),
            "current_price": (
                None if position.current_price is None else str(position.current_price)
            ),
            "unrealized_pnl": (
                None if position.unrealized_pnl is None else str(position.unrealized_pnl)
            ),
            "created_at": str(row.get("created_at")),
            "updated_at": str(row.get("updated_at")),
        }
        body.update(self._paper_provenance(stale, self._text(row.get("price_at"))))
        return body

    def _order_body(
        self,
        row: Mapping[str, Any],
        fills: Sequence[Mapping[str, Any]] = (),
    ) -> Dict[str, Any]:
        """One element of ``/api/paper/orders``: the keys it has always carried.

        ``fee`` and ``execution_id`` appear **only** on an order that has at least one fill,
        which is exactly the union the pre-change baseline recorded: a filled order carried
        them and a resting or cancelled one did not. Adding them unconditionally would add two
        keys to a variant that never had them.

        ``price`` is the average fill price once there is one, and the limit price before that
        - the same value this field has always held. ``strategy_id`` and ``deployment_id`` are
        ``null`` because ``paper_orders`` has no column for either; see the module docstring.
        """
        price = (
            row.get("avg_fill_price")
            if row.get("avg_fill_price") is not None
            else row.get("limit_price")
            if row.get("limit_price") is not None
            else row.get("reference_price")
        )
        body: Dict[str, Any] = {
            "order_id": str(row.get("id")),
            "user_id": str(row.get("user_id")),
            "strategy_id": None,
            "deployment_id": None,
            "symbol": str(row.get("symbol")),
            "side": str(row.get("side")),
            "order_type": str(row.get("order_type")),
            "quantity": str(row.get("quantity")),
            "price": None if price is None else str(price),
            "filled_quantity": str(row.get("filled_quantity")),
            "status": str(row.get("legacy_status")),
            "created_at": str(row.get("created_at")),
            "updated_at": str(row.get("updated_at")),
        }
        if fills:
            fee_minor = sum(int(fill.get("fee_minor") or 0) for fill in fills)
            body["fee"] = str(self.config.money(Decimal(fee_minor).scaleb(-MONEY_EXPONENT)))
            body["execution_id"] = str(fills[0].get("fill_event_id"))
        body.update(self._paper_provenance())
        return body

    def _trade_body(
        self,
        fill: Mapping[str, Any],
        order: Mapping[str, Any],
        realized_pnl: Decimal,
    ) -> Dict[str, Any]:
        """One element of ``/api/paper/trades``: the fill history, key for key.

        This endpoint has always served **fills** - the in-memory ``self._trades[uid]`` list
        held one record per fill, appended by ``_execute_fill`` - so it is served from
        ``paper_fills``, joined to its order for the symbol and side those columns live on and
        to the balance ledger for the realized PnL that fill moved. ``paper_trades`` answers
        the *other* question, closed round-trips, and is what ``/summary``'s closed-trade
        figures count.
        """
        body = {
            "execution_id": str(fill.get("fill_event_id")),
            "order_id": str(fill.get("order_id")),
            "user_id": str(fill.get("user_id")),
            "strategy_id": None,
            "deployment_id": None,
            "symbol": str(order.get("symbol")),
            "side": str(order.get("side")),
            "quantity": str(fill.get("quantity")),
            "price": str(fill.get("price")),
            "fee": str(
                self.config.money(
                    Decimal(int(fill.get("fee_minor") or 0)).scaleb(-MONEY_EXPONENT)
                )
            ),
            "realized_pnl": str(realized_pnl),
            "status": PaperOrderStatus.FILLED.value,
            "executed_at": str(fill.get("filled_at")),
        }
        body.update(self._paper_provenance())
        return body

    # ══════════════════════════════════════════════════════════════════
    # THE READS - SYNCHRONOUS, FOR THE REASON IN THE MODULE DOCSTRING
    # ══════════════════════════════════════════════════════════════════

    def _account_and_positions(
        self,
        sb: Any,
        uid: str,
        initial_capital: Optional[Decimal] = None,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """The default account row and its OPEN positions. Two statements.

        ``get_or_create_account`` creates the account when it is absent, which is the
        behaviour every caller of this facade already relies on - ``routers/risk.py`` and
        ``dashboard_aggregation_service`` both call ``get_or_create_account`` for a user who
        may never have traded.
        """
        account = repo.get_or_create_account(
            sb,
            uid,
            repo.DEFAULT_CURRENCY,
            None,
            initial_capital=(
                self.default_capital if initial_capital is None else initial_capital
            ),
        )
        positions = repo.get_positions(sb, uid, account_id=account["id"])
        return account, positions

    def get_or_create_account(
        self,
        user_id: str,
        initial_capital: Optional[float] = None,
        *,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The user's virtual paper account, created at ``initial_capital`` if it has none.

        Synchronous, and called synchronously from ``async def`` handlers in
        ``routers/risk.py`` (line 357) and ``routers/paper_trading.py``.

        Raises:
            PaperError: 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` when 009 is unapplied, or 503
                ``PAPER_READ_FAILED`` when a statement did not complete. Never a default
                balance (Requirements 17.2, 28.3).
        """
        uid = str(user_id)
        sb = self._client(access_token)
        capital = None if initial_capital is None else self._exact(initial_capital, "capital")
        try:
            row, position_rows = self._account_and_positions(sb, uid, capital)
            positions, _ = self._positions_of(position_rows)
            return self._account_body(row, self._figures(self._account_of(row), positions))
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "account read") from exc

    def get_positions(
        self,
        user_id: str,
        *,
        access_token: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """The user's OPEN paper positions, with their unrealized PnL.

        A fully closed position persists at ``size = 0`` with ``closed_at`` set rather than
        being deleted (Requirement 18.5), and the read filters ``closed_at IS NULL`` in the
        statement - which is what the old ``del user_positions[symbol]`` expressed
        structurally. So this returns exactly the set the dictionary used to hold, and
        ``risk.py``'s ``open_pos_count`` keeps reporting the figure it reports today.
        """
        uid = str(user_id)
        sb = self._client(access_token)
        try:
            row, position_rows = self._account_and_positions(sb, uid)
            positions, by_symbol = self._positions_of(position_rows)
            figures = self._figures(self._account_of(row), positions)
            revalued = figures["positions"]
            return [
                self._position_body(
                    by_symbol[symbol], revalued[symbol], bool(figures["stale"])
                )
                for symbol in by_symbol
                if revalued[symbol].is_open
            ]
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "positions read") from exc

    def get_orders(
        self,
        user_id: str,
        status: Optional[str] = None,
        *,
        access_token: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """The user's paper orders, newest first, optionally filtered by legacy status.

        ``status`` is applied as a predicate on ``paper_orders.legacy_status`` inside the
        statement, so ``?status=OPEN`` returns exactly the orders it returns today:
        ``ACCEPTED`` and ``PARTIALLY_FILLED`` both carry ``OPEN`` there, written by the
        repository from ``LEGACY_STATUS_FOR_STATE`` (Requirement 17.12).
        """
        uid = str(user_id)
        sb = self._client(access_token)
        try:
            account = repo.get_or_create_account(
                sb, uid, repo.DEFAULT_CURRENCY, None, initial_capital=self.default_capital
            )
            rows = repo.get_orders(
                sb,
                uid,
                account_id=account["id"],
                legacy_status=None if not status else str(status).upper(),
            )
            fills_by_order = self._fills_by_order(repo.get_fills(sb, uid))
            return [
                self._order_body(row, fills_by_order.get(str(row.get("id")), []))
                for row in rows
            ]
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "orders read") from exc

    @staticmethod
    def _fills_by_order(
        fills: Sequence[Mapping[str, Any]]
    ) -> Dict[str, List[Mapping[str, Any]]]:
        """``{order_id: [fill, …]}``, newest fill first within each order."""
        grouped: Dict[str, List[Mapping[str, Any]]] = {}
        for fill in fills:
            grouped.setdefault(str(fill.get("order_id")), []).append(fill)
        return grouped

    def get_trades(
        self,
        user_id: str,
        limit: int = 50,
        *,
        access_token: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """The user's fill history, most recent first, at most ``limit`` records.

        Three statements: the fills, the orders they belong to (for ``symbol`` and ``side``,
        which live on the order) and the ``FILL`` balance events (for the realized PnL each
        fill moved). The realized figure is read from the ledger rather than recomputed,
        because the ledger is where it was recorded when the money moved.
        """
        uid = str(user_id)
        sb = self._client(access_token)
        try:
            fills = repo.get_fills(sb, uid, limit=None if limit is None else int(limit))
            if not fills:
                return []
            account = repo.get_or_create_account(
                sb, uid, repo.DEFAULT_CURRENCY, None, initial_capital=self.default_capital
            )
            orders = {
                str(row.get("id")): row
                for row in repo.get_orders(sb, uid, account_id=account["id"])
            }
            realized = self._realized_by_fill(
                repo.get_balance_events(sb, uid, account_id=account["id"], cause="FILL")
            )
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "trades read") from exc

        trades: List[Dict[str, Any]] = []
        for fill in fills:
            order = orders.get(str(fill.get("order_id")))
            if order is None:
                # ``paper_fills.order_id`` is a NOT NULL foreign key, so this is unreachable
                # for a consistent database. Reporting the fill without a symbol or a side
                # would be inventing two values, and dropping it silently would lose a record,
                # so it is dropped loudly.
                logger.error(
                    "A paper fill references an order that is not readable for this identity; "
                    "it is omitted from the fill history. fill=%s order=%s",
                    fill.get("id"),
                    fill.get("order_id"),
                )
                continue
            trades.append(
                self._trade_body(fill, order, realized.get(str(fill.get("id")), Decimal("0")))
            )
        return trades

    def _realized_by_fill(
        self, events: Sequence[Mapping[str, Any]]
    ) -> Dict[str, Decimal]:
        """``{fill_id: realized_delta}`` from the ``FILL`` balance events.

        The per-fill realized PnL, as recorded when the money moved. A fill that realized
        nothing has an event carrying ``realized_delta = 0``, so an *absent* entry means the
        ledger is incomplete rather than that the fill realized nothing - which is why the
        caller logs it rather than treating the two as the same thing.
        """
        realized: Dict[str, Decimal] = {}
        for event in events:
            fill_id = event.get("fill_id")
            if fill_id is None:
                continue
            realized[str(fill_id)] = accounting.to_decimal(
                event.get("realized_delta"), "realized_delta"
            )
        return realized

    def get_performance_summary(
        self,
        user_id: str,
        *,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The paper performance summary, key for key as ``/api/paper/summary`` returns it.

        ``total_trades`` counts fills, and ``closed_trades`` / ``winning_trades`` /
        ``losing_trades`` count **realizing fills** - a fill whose ledger event moved
        ``realized_pnl`` - which is exactly what the old implementation counted (``[t for t in
        trades if Decimal(t["realized_pnl"]) != 0]``). Counting ``paper_trades`` rows instead
        would silently drop every partial close from the win rate.
        """
        uid = str(user_id)
        sb = self._client(access_token)
        try:
            row, position_rows = self._account_and_positions(sb, uid)
            positions, _ = self._positions_of(position_rows)
            figures = self._figures(self._account_of(row), positions)
            fills = repo.get_fills(sb, uid)
            realized = self._realized_by_fill(
                repo.get_balance_events(sb, uid, account_id=row["id"], cause="FILL")
            )
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "summary read") from exc

        account_body = self._account_body(row, figures)
        initial_capital = accounting.to_decimal(row.get("initial_capital"), "initial_capital")
        total_equity = accounting.to_decimal(figures["total_equity"], "total_equity")
        total_pnl = total_equity - initial_capital
        roi_pct = (
            total_pnl / initial_capital * Decimal("100")
            if initial_capital > 0
            else Decimal("0")
        )

        realizing = [
            realized[str(fill.get("id"))]
            for fill in fills
            if str(fill.get("id")) in realized
            and realized[str(fill.get("id"))] != Decimal("0")
        ]
        winning = [amount for amount in realizing if amount > Decimal("0")]
        losing = [amount for amount in realizing if amount < Decimal("0")]

        win_rate = (len(winning) / len(realizing) * 100) if realizing else 0.0
        gross_profit = sum(winning, Decimal("0"))
        gross_loss = abs(sum(losing, Decimal("0")))
        profit_factor = (
            float(gross_profit / gross_loss)
            if gross_loss > 0
            else (float(gross_profit) if gross_profit > 0 else 1.0)
        )

        summary = {
            "account": account_body,
            "total_pnl": str(self.config.money(total_pnl)),
            "roi_pct": float(self.config.money(roi_pct)),
            "total_trades": len(fills),
            "closed_trades": len(realizing),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "open_positions_count": len(
                [p for p in positions.values() if p.is_open]
            ),
        }
        summary.update(
            self._paper_provenance(figures["stale"], figures["last_price_at"])
        )
        return summary

    def verify_accounting_invariants(
        self,
        user_id: str,
        *,
        access_token: Optional[str] = None,
    ) -> bool:
        """Whether every Requirement 18.3 / 18.4 / 18.5 invariant holds, to **zero tolerance**.

        The old implementation compared ``total_equity`` against the sum with a
        ``Decimal("0.05")`` tolerance, which was wide enough to hide the short-close path that
        credited no cash. ``paper_accounting.invariants_hold`` is exact equality, and the
        figures it checks are the persisted ones: the stored ``total_equity`` against the
        stored balances and the stored positions at their last validated prices.
        """
        uid = str(user_id)
        sb = self._client(access_token)
        try:
            row, position_rows = self._account_and_positions(sb, uid)
            positions, _ = self._positions_of(position_rows)
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "invariant read") from exc

        account = self._account_of(row)
        prices = accounting.last_validated_prices(positions.values())
        try:
            invariant = accounting.violated_invariant(
                account, positions, prices, self.config
            )
        except accounting.StalePrice as exc:
            # A missing measurement is not a breached invariant (Requirement 18.15). Saying
            # "the ledger is broken" because a price is absent would be the wrong answer.
            logger.warning(
                "The paper accounting invariants could not be checked because a validated "
                "price is absent: %s",
                exc,
            )
            return True
        if invariant is not None:
            logger.error(
                "A paper accounting invariant does not hold for account %s: %s",
                row.get("id"),
                invariant,
            )
        return invariant is None

    # ══════════════════════════════════════════════════════════════════
    # RESET (Requirement 17.15)
    # ══════════════════════════════════════════════════════════════════

    def reset_account(
        self,
        user_id: str,
        capital: Optional[float] = None,
        *,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reset the account to ``capital``, close every position and cancel every open order.

        Synchronous, as it always was. **History is not deleted**: the filled orders, the fills,
        the closed round-trips and the balance ledger all stay readable, which is what lets
        ``GET /api/paper/orders?status=FILLED`` keep answering after a reset exactly as it does
        today. A position is closed by persisting ``size = 0`` with ``closed_at`` set
        (Requirement 18.5), and the reset itself is recorded as a ``RESET`` balance event so the
        ledger explains the jump instead of a balance simply changing.
        """
        uid = str(user_id)
        sb = self._client(access_token)
        cap = self.default_capital if capital is None else self._exact(capital, "capital")
        occurred_at = self._get_timestamp()

        try:
            account = repo.get_or_create_account(
                sb, uid, repo.DEFAULT_CURRENCY, None, initial_capital=cap
            )
            account_id = str(account["id"])

            for order in repo.get_orders(
                sb,
                uid,
                account_id=account_id,
                legacy_status=PaperOrderStatus.OPEN.value,
            ):
                repo.update_order(
                    sb,
                    user_id=uid,
                    order_id=order["id"],
                    order_state=PaperOrderState.CANCELLED,
                    expected_state=PaperOrderState(order["order_state"]),
                )

            for row in repo.get_positions(sb, uid, account_id=account_id):
                repo.upsert_position(
                    sb,
                    account_id=account_id,
                    user_id=uid,
                    symbol=row["symbol"],
                    side=row["side"],
                    size=Decimal("0"),
                    entry_price=accounting.to_decimal(row["entry_price"], "entry_price"),
                    opened_at=row.get("opened_at") or occurred_at,
                    current_price=row.get("current_price"),
                    unrealized_pnl=Decimal("0"),
                    price_at=row.get("price_at"),
                    closed_at=occurred_at,
                )

            before_row = repo.lock_account_for_update(sb, uid, account_id=account_id)
            before = self._account_of(before_row)
            updated = repo.bump_version(
                sb,
                user_id=uid,
                account_id=account_id,
                expected_version=before_row["version"],
                payload={
                    "initial_capital": cap,
                    "available_balance": cap,
                    "locked_balance": Decimal("0"),
                    "realized_pnl": Decimal("0"),
                    "total_equity": cap,
                    "last_price_at": None,
                    "stale": False,
                },
            )
            repo.insert_balance_event(
                sb,
                account_id=account_id,
                user_id=uid,
                cause="RESET",
                available_delta=cap - before.available_balance,
                locked_delta=-before.locked_balance,
                realized_delta=-before.realized_pnl,
                available_after=cap,
                locked_after=Decimal("0"),
                realized_after=Decimal("0"),
                occurred_at=occurred_at,
            )
        except repo.PaperConcurrencyConflict as exc:
            raise self._conflict(exc) from exc
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "account reset") from exc

        # No open position survives a reset, so nothing in the body is price-derived.
        return self._account_body(
            updated, self._figures(self._account_of(updated), {})
        )

    # ══════════════════════════════════════════════════════════════════
    # PLACING AN ORDER (Requirements 16.6, 16.8, 16.11)
    # ══════════════════════════════════════════════════════════════════

    async def place_order(
        self,
        user_id: str,
        symbol: str,
        side: str,
        order_type: str = "market",
        quantity: float = 0.01,
        price: Optional[float] = None,
        strategy_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        *,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Place a paper market or limit order. Same argument order, same return shape.

        IDEMPOTENCY IS THE DATABASE'S NOW (Requirement 16.8)
        ---------------------------------------------------
        ``self._idempotency_cache`` is gone. The key is persisted on
        ``paper_orders.idempotency_key`` and arbitrated by ``uq_paper_order_idem_default`` -
        the partial unique index on ``(account_id, idempotency_key) WHERE session_id IS NULL``
        that ``013_paper_default_account_children.sql`` adds, because SQL treats two NULL
        ``session_id`` values as distinct and ``uq_paper_order_idem`` alone would silently stop
        de-duplicating the default account's orders. A retry re-reads the recorded order and
        returns it; a concurrent first request that loses the index race is answered the same
        way, by reading the winner.

        PER-USER SERIALISATION IS THE VERSION GUARD NOW (Requirement 16.10)
        ------------------------------------------------------------------
        ``self._user_locks`` is gone with it. It only ever serialised one process, so two
        workers could both move the same balance; the ``paper_accounts.version`` predicate
        makes the loser's UPDATE match no row and surface as 409
        ``PAPER_CONCURRENCY_CONFLICT`` instead of a silently overwritten balance.
        """
        uid = str(user_id)
        sb = self._client(access_token)

        clean_sym = self._clean_symbol(symbol)
        clean_side = str(side).strip().lower()
        clean_type = str(order_type).strip().lower()
        qty_dec = self._exact(quantity, "quantity")

        if qty_dec <= Decimal("0"):
            raise ValueError("Quantity must be greater than 0")
        if clean_side not in ("buy", "sell"):
            raise ValueError("Side must be 'buy' or 'sell'")
        if clean_type not in ("market", "limit"):
            raise ValueError("Order type must be 'market' or 'limit'")

        try:
            account_row, position_rows = self._account_and_positions(sb, uid)
            account_id = str(account_row["id"])

            if idempotency_key:
                recorded = repo.probe_idempotency_key(
                    sb,
                    user_id=uid,
                    idempotency_key=idempotency_key,
                    account_id=account_id,
                )
                if recorded is not None:
                    logger.info(
                        "[PAPER IDEMPOTENCY] An order is already recorded under this key; "
                        "returning it without placing a second one. order=%s",
                        recorded.get("id"),
                    )
                    return self._order_body(
                        recorded, repo.get_fills(sb, uid, order_id=recorded["id"])
                    )

            positions, _ = self._positions_of(position_rows)
            open_positions = {
                symbol_key: position
                for symbol_key, position in positions.items()
                if position.is_open
            }

            # ── CANONICAL RISK EVALUATION ──
            # Unchanged in every threshold and every message; only the figures it reads are
            # persisted ones now.
            from backend_app.routers.risk import (
                get_user_risk_settings_store,
                is_user_kill_switched,
                record_risk_violation,
            )

            if is_user_kill_switched(uid):
                record_risk_violation(
                    uid,
                    "KILL_SWITCH_ACTIVE",
                    "Trading halted: Emergency Risk Kill Switch is active",
                )
                raise ValueError("Trading halted: Risk Kill Switch is active")

            user_risk = get_user_risk_settings_store(uid)
            max_daily_loss = self._exact(
                user_risk.get("max_daily_loss", 50000.0), "max_daily_loss"
            )
            max_positions = int(user_risk.get("max_positions", 20))

            realized_pnl = accounting.to_decimal(
                account_row.get("realized_pnl"), "realized_pnl"
            )
            realized_loss = -min(Decimal("0"), realized_pnl)
            if realized_loss >= max_daily_loss:
                record_risk_violation(
                    uid,
                    "MAX_DAILY_LOSS_EXCEEDED",
                    f"Daily loss ${realized_loss:.2f} reached limit of ${max_daily_loss:.2f}",
                )
                raise ValueError(
                    f"Risk limit exceeded: Max Daily Loss limit reached "
                    f"(${realized_loss:.2f} >= ${max_daily_loss:.2f})"
                )

            cur_positions_count = len(open_positions)
            if clean_sym not in open_positions and cur_positions_count >= max_positions:
                record_risk_violation(
                    uid,
                    "MAX_POSITIONS_EXCEEDED",
                    f"Open positions ({cur_positions_count}) reached limit of {max_positions}",
                )
                raise ValueError(
                    f"Risk limit exceeded: Max open positions reached "
                    f"({cur_positions_count} >= {max_positions})"
                )

            avail_bal = accounting.to_decimal(
                account_row.get("available_balance"), "available_balance"
            )

            # PAPER-02: resolve the execution price from evidence, never from a constant.
            #  - market: the client's ``price`` is IGNORED; fill only at a fresh measured quote.
            #  - limit : the caller's price is the limit itself and is required.
            if clean_type == "market":
                # PAPER-02: cache first, then a bounded on-demand venue quote. See
                # `resolve_market_price`. Still never the client's `price`.
                measured = await resolve_market_price(clean_sym)
                if measured is None or self._exact(measured, "price") <= Decimal("0"):
                    from backend_app.backend.paper.paper_market_feed import (
                        PaperMarketDataUnavailable,
                    )

                    raise PaperMarketDataUnavailable(
                        "NO_FRESH_PRICE",
                        "no fresh measured price is available for this symbol",
                    )
                exec_price = self.config.price(self._exact(measured, "price"))
            else:
                if price is None or self._exact(price, "price") <= Decimal("0"):
                    raise ValueError("A limit order requires a positive price")
                exec_price = self.config.price(self._exact(price, "price"))

            # Strategy-Level Limits Enforcement - thresholds and messages unchanged.
            from backend_app.routers.risk import _user_strategy_limits

            user_strat_limits = _user_strategy_limits.get(uid, {})
            if strategy_id and strategy_id in user_strat_limits:
                strat_limit = user_strat_limits[strategy_id]
                allowed_syms = [
                    self._clean_symbol(s)
                    for s in strat_limit.get("allowed_symbols", [])
                    if s
                ]
                if allowed_syms and clean_sym not in allowed_syms:
                    record_risk_violation(
                        uid,
                        "SYMBOL_RESTRICTED",
                        f"Symbol {clean_sym} not in allowed symbols list for strategy "
                        f"{strategy_id}",
                    )
                    raise ValueError(
                        f"Risk limit exceeded: Symbol {clean_sym} is not allowed for strategy "
                        f"{strategy_id}"
                    )

                max_pos_size = self._exact(
                    strat_limit.get("max_position_size", 1000000.0), "max_position_size"
                )
                prop_notional = qty_dec * exec_price
                if prop_notional > max_pos_size:
                    record_risk_violation(
                        uid,
                        "MAX_POSITION_SIZE_EXCEEDED",
                        f"Order notional ${prop_notional:.2f} exceeds strategy limit "
                        f"${max_pos_size:.2f}",
                    )
                    raise ValueError(
                        f"Risk limit exceeded: Order size ${prop_notional:.2f} exceeds maximum "
                        f"strategy limit ${max_pos_size:.2f}"
                    )

            if clean_type == "limit":
                estimated_cost = self.config.money(
                    qty_dec * exec_price * (Decimal("1") + self.default_fee_rate)
                )
                if clean_side == "buy" and avail_bal < estimated_cost:
                    raise ValueError(
                        f"Insufficient virtual balance (${avail_bal:.2f}) for limit order "
                        f"estimated cost (${estimated_cost:.2f})"
                    )
                return self._place_resting_limit_order(
                    sb,
                    uid,
                    account_id=account_id,
                    symbol=clean_sym,
                    side=clean_side,
                    quantity=qty_dec,
                    limit_price=exec_price,
                    estimated_cost=estimated_cost,
                    idempotency_key=idempotency_key,
                )

            # Market order -> accepted and filled in the same call, as before.
            order_row, already_recorded = self._insert_fund_and_accept(
                sb,
                uid,
                account_id=account_id,
                symbol=clean_sym,
                side=clean_side,
                order_type="market",
                quantity=qty_dec,
                limit_price=None,
                reference_price=exec_price,
                idempotency_key=idempotency_key,
                available_balance=avail_bal,
            )
            if already_recorded:
                return self._order_body(
                    order_row, repo.get_fills(sb, uid, order_id=order_row["id"])
                )
            try:
                return await self._execute_fill(
                    sb, uid, order_row, quantity=qty_dec, price=exec_price
                )
            except accounting.InvariantViolation as exc:
                # PAPER-02's second half. The gate above decided on the balance read at the
                # top of this call; a CONCURRENT writer can still have spent it before the
                # fill re-reads the account, and then ``apply_fill`` refuses. That refusal
                # writes nothing - but the order is already ACCEPTED, and leaving it there
                # is the defect: an order that cannot be funded would stay live on the book
                # with ``rejection_reason = NULL`` for ever.
                #
                # ``ACCEPTED -> REJECTED`` is one of Requirement 16.2's nine transitions and
                # is seeded in ``paper_order_allowed_transitions``, so this is the state
                # machine being used rather than worked around.
                raise self._reject_unfundable_order(
                    sb,
                    uid,
                    order=order_row,
                    expected_state=PaperOrderState(order_row["order_state"]),
                    invariant=exc,
                ) from exc
        except repo.PaperConcurrencyConflict as exc:
            raise self._conflict(exc) from exc
        except accounting.InvariantViolation as exc:
            logger.error("A paper fill was refused by an invariant: %s", exc)
            raise PaperError(
                PAPER_INVARIANT_VIOLATION, details={"invariant": exc.invariant}
            ) from exc
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "order placement") from exc

    def _insert_fund_and_accept(
        self,
        sb: Any,
        uid: str,
        *,
        account_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        limit_price: Optional[Decimal],
        reference_price: Optional[Decimal],
        idempotency_key: Optional[str],
        available_balance: Decimal,
    ) -> Tuple[Dict[str, Any], bool]:
        """Insert at ``CREATED``, gate on funds, then move to ``ACCEPTED``.

        THE GATE IS THE FIX FOR PAPER-02, AND ITS POSITION IS THE WHOLE POINT
        --------------------------------------------------------------------
        This used to be ``_insert_and_accept``: two statements, insert at ``CREATED`` then
        accept, with **no affordability check anywhere on the market path**. ``place_order``
        read ``available_balance`` and used it only in the ``limit`` branch. So an unfundable
        market buy was accepted, and the shortfall was discovered three steps later inside
        ``paper_accounting.apply_fill``, which refused with
        ``InvariantViolation('available_balance')``. That refusal correctly wrote nothing -
        but the order was already ``ACCEPTED`` / ``legacy_status = 'OPEN'`` with
        ``filled_quantity = 0`` and ``rejection_reason = NULL``, and nothing ever went back
        for it. Measured in ``staging/probe_paper02_state.py``:

            order side=buy qty=500 state=ACCEPTED legacy=OPEN filled=0 reason=None
            PAPER02_STRANDED_ORDERS=1   PAPER02_REASON_RECORDED=False

        The gate therefore sits **between** the insert and the accept. Not before the insert,
        because Requirement 16.6 wants the rejection *persisted* as an order carrying its
        reason, and a row that was never written carries nothing. Not after the accept,
        because then the order has already been in a state it could fill from.

        ``CREATED -> REJECTED`` is one of Requirement 16.2's nine transitions and is seeded in
        ``paper_order_allowed_transitions``, so no new state, no new model and no migration is
        involved - this is the existing machine, used.

        WHY ``required_funds`` AND NOT THE LIMIT BRANCH'S ``estimated_cost``
        -------------------------------------------------------------------
        ``paper_accounting.required_funds`` is ``notional * (1 + fee_rate + slippage_rate)``,
        which is exactly the figure Requirement 16.6 prescribes and the one
        ``paper_simulator`` gates on. The ``limit`` branch computes a weaker
        ``estimated_cost`` that omits the slippage allowance; that divergence is **reported as
        PAPER-05, not changed here**, because the same value is also the amount
        ``accounting.lock`` reserves and what the resting fill releases, so raising it is a
        coupled change across the resting-order path rather than a threshold edit.

        A market order still locks nothing (Requirement 16.6's "SHALL lock no funds", and
        ``design.md``'s market path), so the gate is a comparison and not a reservation. The
        atomicity that matters is already present and was measured working: ``apply_fill``
        asserts every invariant **before** the first statement, and ``bump_version`` carries
        the account's ``version`` as a predicate, so two racing workers cannot both move the
        balance. What was missing was never the money - it was the losing order's state.

        Returns:
            ``(order_row, already_recorded)``. ``already_recorded`` is ``True`` when the insert
            lost the idempotency-index race, in which case the row is the order that **won**
            and the caller returns it untouched instead of accepting or filling it a second
            time (Requirement 16.8).

        Raises:
            PaperError: 400 ``PAPER_INSUFFICIENT_FUNDS``, after the order has been persisted
                at ``REJECTED`` carrying ``INSUFFICIENT_FUNDS``.
        """
        created, already_recorded = self._insert_order_or_recorded(
            sb,
            uid,
            account_id=account_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            reference_price=reference_price,
            idempotency_key=idempotency_key,
        )
        if already_recorded:
            return created, True

        price_for_funds = limit_price if limit_price is not None else reference_price
        if price_for_funds is not None:
            required = accounting.required_funds(
                {"quantity": quantity}, price_for_funds, self.config
            )
            # A sell is gated too. ``paper_simulator`` records that ``design.md``'s pseudocode
            # narrows this to ``side = 'buy'`` while Requirement 16.6 carries no side
            # qualifier, and that the two readings differ. The retained behaviour is kept
            # here - only a buy is gated - because a sell that CLOSES a position releases
            # cash rather than consuming it, so gating it on the notional would refuse a
            # position-reducing order that is always affordable. A sell that OPENS a short
            # does consume cash, and that case is caught by the ``InvariantViolation``
            # handler in ``place_order``, which now rejects rather than stranding.
            if side == "buy" and required > available_balance:
                raise self._reject_unfundable_order(
                    sb,
                    uid,
                    order=created,
                    expected_state=PaperOrderState.CREATED,
                    required=required,
                    available=available_balance,
                )

        return (
            repo.update_order(
                sb,
                user_id=uid,
                order_id=created["id"],
                order_state=PaperOrderState.ACCEPTED,
                expected_state=PaperOrderState.CREATED,
            ),
            False,
        )

    def _reject_unfundable_order(
        self,
        sb: Any,
        uid: str,
        *,
        order: Mapping[str, Any],
        expected_state: PaperOrderState,
        required: Optional[Decimal] = None,
        available: Optional[Decimal] = None,
        invariant: Optional[Exception] = None,
    ) -> PaperError:
        """Persist ``order`` at ``REJECTED`` carrying ``INSUFFICIENT_FUNDS``; return the error.

        One statement on ``paper_orders`` and nothing else, which is how Requirement 16.6's
        "SHALL lock no funds" holds here: no statement against ``paper_accounts``,
        ``paper_positions``, ``paper_fills`` or ``paper_balance_events`` is issued at all -
        not issued and undone.

        Returns the exception rather than raising it, so the call site reads
        ``raise self._reject_unfundable_order(...)`` and a reader can see at a glance that the
        persistence happened before the raise.

        THE REASON VOCABULARY IS BORROWED, NOT RE-SPELLED. ``REJECTION_INSUFFICIENT_FUNDS``
        lives in ``paper_simulator`` beside the other nine reasons, and 009 declares no CHECK
        constraint on ``rejection_reason`` - so that tuple, not the database, is the
        enumeration. Writing ``"INSUFFICIENT_FUNDS"`` as a literal here would create a second
        spelling of a closed vocabulary. The import is function-local because
        ``paper_simulator`` is a large module and this service is on the request path for
        every ``/api/paper/*`` call; it is the same reason this module already imports
        ``routers.risk`` and ``core.dependencies`` function-locally.

        WHEN THE INVARIANT IS NOT A FUNDING ONE, THE ORDER IS LEFT ALONE. ``invariant`` is
        only translated to a rejection for ``available_balance``. ``total_equity``,
        ``position_size`` and ``position_side`` are accounting breaches rather than funding
        failures, and labelling one ``INSUFFICIENT_FUNDS`` would put a false reason in the
        ledger; ``locked_balance`` is unreachable here because a market order releases nothing.
        Those keep the existing 500 ``PAPER_INVARIANT_VIOLATION``, which is the right answer
        for a condition an operator has to look at.
        """
        from backend_app.backend.paper.paper_simulator import (
            REJECTION_INSUFFICIENT_FUNDS,
            PaperInsufficientFunds,
        )

        if invariant is not None and getattr(invariant, "invariant", None) != (
            "available_balance"
        ):
            logger.error(
                "A paper fill was refused by the %r invariant, which is not a funding "
                "failure, so order %s is left at %s for an operator to look at: %s",
                getattr(invariant, "invariant", None),
                order.get("id"),
                order.get("order_state"),
                invariant,
            )
            return PaperError(
                PAPER_INVARIANT_VIOLATION,
                details={"invariant": getattr(invariant, "invariant", None)},
            )

        rejected = repo.update_order(
            sb,
            user_id=uid,
            order_id=order["id"],
            order_state=PaperOrderState.REJECTED,
            expected_state=expected_state,
            rejection_reason=REJECTION_INSUFFICIENT_FUNDS,
        )
        logger.info(
            "[PAPER FUNDS] order %s rejected as %s from %s: required %s, available %s. "
            "No balance, position, fill or ledger row was written.",
            rejected.get("id"),
            REJECTION_INSUFFICIENT_FUNDS,
            expected_state.value,
            required,
            available,
        )
        # Both figures are reported at the account currency's Minor_Units scale. ``required``
        # already is - ``required_funds`` quantizes once at the end - but ``available`` comes
        # straight off ``paper_accounts.available_balance``, a ``NUMERIC(28, 10)``, so without
        # this the caller was shown ``required=500.75`` beside
        # ``available=100.0000000000``: two precisions for the same currency in one refusal.
        return PaperInsufficientFunds(
            required="unknown" if required is None else self.config.money(required),
            available="unknown" if available is None else self.config.money(available),
            order_id=rejected.get("id"),
            details={"rejection_reason": REJECTION_INSUFFICIENT_FUNDS},
        )

    def _insert_order_or_recorded(
        self,
        sb: Any,
        uid: str,
        *,
        account_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        limit_price: Optional[Decimal],
        reference_price: Optional[Decimal],
        idempotency_key: Optional[str],
    ) -> Tuple[Dict[str, Any], bool]:
        """Insert the order, or return the one an idempotency key already holds.

        Requirement 16.8, in the case the probe in :meth:`place_order` cannot cover: two first
        requests carrying the same key can both read no order and both insert, and
        ``uq_paper_order_idem_default`` refuses one of them. That refusal is the guarantee
        working, so the loser re-reads and answers with the winner rather than raising - which is
        what makes a retried intent produce exactly one order.

        A conflict on an order with **no** idempotency key is re-raised: without a key there is
        no recorded order to answer with, and inventing one would be worse than the conflict.
        """
        try:
            return (
                repo.insert_order(
                    sb,
                    account_id=account_id,
                    user_id=uid,
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    quantity=quantity,
                    fingerprint=self._fingerprint(
                        uid,
                        symbol,
                        side,
                        order_type,
                        quantity,
                        limit_price or reference_price,
                    ),
                    order_state=PaperOrderState.CREATED,
                    limit_price=limit_price,
                    reference_price=reference_price,
                    idempotency_key=idempotency_key,
                ),
                False,
            )
        except repo.PaperConcurrencyConflict:
            if not idempotency_key:
                raise
            recorded = repo.probe_idempotency_key(
                sb,
                user_id=uid,
                idempotency_key=idempotency_key,
                account_id=account_id,
            )
            if recorded is None:
                raise
            logger.info(
                "[PAPER IDEMPOTENCY] A concurrent request recorded this key first; answering "
                "with its order. order=%s",
                recorded.get("id"),
            )
            return recorded, True

    def _place_resting_limit_order(
        self,
        sb: Any,
        uid: str,
        *,
        account_id: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        limit_price: Decimal,
        estimated_cost: Decimal,
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        """Record a resting limit order and lock a buy's funds. Returns the order body.

        The order is inserted **before** the money moves, so a duplicate key is refused by the
        index before anything is locked, and a lock that then fails leaves the order at
        ``CREATED`` - not on the book - rather than accepted with no reservation behind it.
        That ordering is what stands in for the single transaction PostgREST cannot give
        (Requirement 24.6's limitation, recorded in ``paper_repository``).

        Only a **buy** locks funds, which is the existing behaviour: a sell that opens a short
        is charged at fill time. ``paper_accounting.lock`` refuses a lock that would drive
        ``available_balance`` below zero (Requirement 18.4) instead of detecting it afterwards.
        """
        created, already_recorded = self._insert_order_or_recorded(
            sb,
            uid,
            account_id=account_id,
            symbol=symbol,
            side=side,
            order_type="limit",
            quantity=quantity,
            limit_price=limit_price,
            reference_price=limit_price,
            idempotency_key=idempotency_key,
        )
        if already_recorded:
            # A concurrent request already recorded this key. Its order is on the book with its
            # own reservation behind it, so nothing is locked a second time.
            return self._order_body(
                created, repo.get_fills(sb, uid, order_id=created["id"])
            )

        if side == "buy" and estimated_cost > Decimal("0"):
            occurred_at = self._get_timestamp()
            before_row = repo.lock_account_for_update(sb, uid, account_id=account_id)
            before = self._account_of(before_row)
            locked = accounting.lock(before, estimated_cost, self.config)
            repo.bump_version(
                sb,
                user_id=uid,
                account_id=account_id,
                expected_version=before_row["version"],
                payload={
                    "available_balance": locked.available_balance,
                    "locked_balance": locked.locked_balance,
                    # A lock moves cash between two columns of the same account, so the sum -
                    # and therefore ``total_equity`` - is unchanged. It is written from the
                    # stored value rather than recomputed, because nothing about the book moved.
                    "total_equity": before.total_equity,
                },
            )
            repo.insert_balance_event(
                sb,
                account_id=account_id,
                user_id=uid,
                cause="ORDER_LOCK",
                available_delta=locked.available_balance - before.available_balance,
                locked_delta=locked.locked_balance - before.locked_balance,
                realized_delta=Decimal("0"),
                available_after=locked.available_balance,
                locked_after=locked.locked_balance,
                realized_after=locked.realized_pnl,
                occurred_at=occurred_at,
            )

        accepted = repo.update_order(
            sb,
            user_id=uid,
            order_id=created["id"],
            order_state=PaperOrderState.ACCEPTED,
            expected_state=PaperOrderState.CREATED,
        )
        return self._order_body(accepted)

    # ══════════════════════════════════════════════════════════════════
    # THE FILL (Requirements 18.5, 18.6, 18.7, 18.8, 18.14)
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _assert_account_unmoved(
        sb: Any, uid: str, *, account_id: str, seen: Mapping[str, Any]
    ) -> None:
        """Raise :class:`repo.PaperConcurrencyConflict` if the account's version moved.

        The same predicate ``bump_version`` applies, asked one statement earlier and for a
        different purpose: ``bump_version`` asks it to keep the balance correct, and this asks
        it to avoid writing a fill row that will turn out to be unbacked. Both are needed -
        this one is advisory and racy by nature, and removing ``bump_version``'s guard because
        this exists would be a mistake.

        A read that does not complete raises :class:`repo.PaperPersistenceError`, which is
        **not** retryable and is therefore not converted here: not knowing the version is not
        the same as knowing it changed.

        Reads through ``lock_account_for_update`` rather than ``read_account`` because that is
        the one that takes an ``account_id``; ``read_account`` keys on ``(user_id, currency,
        session_id)``. Neither holds a lock - ``paper_repository`` records that PostgREST
        cannot hold one across requests - so both are plain reads and the name is the only
        difference that matters here.
        """
        current = repo.lock_account_for_update(sb, uid, account_id=account_id)
        if current is None:
            # Nothing to compare against. ``bump_version`` remains the real guard.
            return
        if int(current.get("version", -1)) != int(seen.get("version", -2)):
            raise repo.PaperConcurrencyConflict(
                f"the paper account moved from version {seen.get('version')} to "
                f"{current.get('version')} while this fill was being computed, so nothing "
                "was written",
                table=repo.ACCOUNTS_TABLE,
                row_id=str(account_id),
            )

    def _fill_row_for_attempt(
        self,
        sb: Any,
        uid: str,
        *,
        account_id: str,
        order_row: Mapping[str, Any],
        quantity: Decimal,
        price: Decimal,
        fee: Decimal,
        slippage_cost: Decimal,
        filled_at: datetime,
        attempt: int,
    ) -> Tuple[Dict[str, Any], bool]:
        """The fill row for this attempt, inserted or adopted. ``(fill_row, already_settled)``.

        ``paper_simulator.apply_fill``'s guard 2, for the default-account path. A fill row
        carrying this order's deterministic ``fill_event_id`` means a previous attempt got
        that far. Which of the two things it means is decided by the ledger, not guessed:

        * a ``FILL`` balance event carries the fill's ``id`` -> **the money already moved**.
          This is the no-op Requirement 18.13 asks for, and the caller is answered with the
          order as it now stands rather than having the fill applied a second time.
        * no ledger row carries it -> **the fill row landed and the money did not**. The row
          is adopted and this attempt finishes the movement, so the retry neither inserts a
          second fill nor skips the one that is owed.

        On the first attempt there is nothing to adopt, and the common path is one INSERT with
        no extra reads: the lookup only runs from the second attempt onward.
        """
        if attempt <= 1:
            return (
                repo.insert_fill(
                    sb,
                    order_id=order_row["id"],
                    user_id=uid,
                    fill_event_id=self._fill_event_id(order_row["id"]),
                    quantity=quantity,
                    price=price,
                    fee_minor=self._minor(fee, "fee_minor"),
                    slippage_minor=self._minor(slippage_cost, "slippage_minor"),
                    filled_at=filled_at,
                ),
                False,
            )

        event_id = self._fill_event_id(order_row["id"])
        existing = next(
            (
                row
                for row in repo.get_fills(sb, uid, order_id=order_row["id"])
                if str(row.get("fill_event_id")) == event_id
            ),
            None,
        )
        if existing is None:
            return (
                repo.insert_fill(
                    sb,
                    order_id=order_row["id"],
                    user_id=uid,
                    fill_event_id=event_id,
                    quantity=quantity,
                    price=price,
                    fee_minor=self._minor(fee, "fee_minor"),
                    slippage_minor=self._minor(slippage_cost, "slippage_minor"),
                    filled_at=filled_at,
                ),
                False,
            )

        settled = any(
            str(event.get("fill_id") or "") == str(existing["id"])
            for event in repo.get_balance_events(
                sb, uid, account_id=account_id, cause="FILL"
            )
        )
        if settled:
            logger.info(
                "[PAPER FILL] fill %s for order %s is already settled in the ledger; "
                "attempt %s is a no-op (Requirement 18.13).",
                existing.get("id"),
                order_row.get("id"),
                attempt,
            )
            return dict(existing), True

        logger.warning(
            "[PAPER FILL] resuming fill %s on order %s at attempt %s: the fill row exists "
            "but no ledger row carries it, so the money has not moved.",
            existing.get("id"),
            order_row.get("id"),
            attempt,
        )
        return dict(existing), False

    @staticmethod
    def _fill_event_id(order_id: Any) -> str:
        """The ``fill_event_id`` this path's single fill of ``order_id`` carries.

        DETERMINISTIC, WHICH IT WAS NOT. This was
        ``f"exec_paper_{uuid.uuid4().hex[:12]}"`` - a fresh random value on every call. The
        consequence was that ``uq_paper_fill_event UNIQUE (order_id, fill_event_id)`` could
        never recognise a repeated fill on the default-account path, so the one database
        guarantee against applying a fill twice was inert here. ``paper_simulator``'s
        :func:`market_fill_event_id` already states the rule for the session path - "replay
        produces the same identity, ``uq_paper_fill_event`` recognises it, and the second
        application is the no-op Requirement 18.13 requires - rather than a second fill under
        a fresh ``uuid4``" - and this path simply did not follow it.

        Derived from the order alone, which is sound here because **every order on this path
        fills exactly once**: ``place_order``'s market branch fills on acceptance, and
        ``check_limit_orders`` fills a resting order for its whole ``quantity`` and moves it
        straight to ``FILLED``. There is no partial-fill path through ``_execute_fill``, so a
        per-order identity cannot collide with a legitimate second fill - and if one is ever
        added, the collision is the database refusing to lose track of it, which is the
        correct failure.

        ``market_fill_event_id`` is deliberately not reused: it spells the prefix
        ``market-``, and this method also mints the id for a resting **limit** fill, so
        borrowing it would label half of these rows wrongly. The retained ``exec_paper_``
        prefix is kept so existing rows and log greps stay recognisable.
        """
        return f"exec_paper_{order_id}"

    #: Requirement 16.10's "retry at most 3". The delays mirror ``paper_simulator``'s, which
    #: ``tests/test_paper_order_lifecycle_writes.py`` pins at ``[0.05, 0.10]``, so the two
    #: paths back off the same way rather than two policies drifting apart.
    FILL_ATTEMPTS: int = 3
    FILL_RETRY_DELAYS: Tuple[float, ...] = (0.05, 0.10)

    async def _execute_fill(
        self,
        sb: Any,
        uid: str,
        order_row: Mapping[str, Any],
        *,
        quantity: Decimal,
        price: Decimal,
        release_from_locked: Decimal = Decimal("0"),
    ) -> Dict[str, Any]:
        """Fill one order with at most :data:`FILL_ATTEMPTS` attempts. Returns the order body.

        WHY A RETRY EXISTS HERE NOW (Requirement 16.10)
        -----------------------------------------------
        This path had none, and that was the hole. ``_attempt_fill`` writes the fill row
        **before** the money moves, which is deliberate and is ``paper_simulator``'s
        documented reasoning: ``uq_paper_fill_event`` is what makes a repeated fill a no-op,
        a fill row written before the money moves can be *resumed*, and money moved before
        the fill row exists could be applied twice. That ordering only pays for itself if
        something actually resumes - and on the default-account path nothing did.

        Measured, with two OS processes racing one account
        (``staging/probe_paper02_state.py``, before this change):

            worker A -> raised PaperError PAPER_CONCURRENCY_CONFLICT
            RACE_FILL_ROWS=2   RACE_FILLED=1   RACE_STRANDED=1

        - a fill row with no money behind it, an order left at ``ACCEPTED``, and a 409 for
        the caller. The money was never wrong; the bookkeeping around it was.

        So: conflicts are retried, each attempt re-reads the account and recomputes from the
        balance it finds, and the attempt that finds the funds gone raises
        ``InvariantViolation`` **before its first statement** - which ``place_order`` turns
        into a persisted ``REJECTED`` order. A conflict is the only retryable condition; a
        read that did not complete is surfaced immediately, because repeating a write whose
        outcome is unknown is how a fill gets applied twice.

        WHAT THIS DOES NOT CLAIM TO FIX - PAPER-04
        ------------------------------------------
        A conflict detected *after* ``insert_fill`` has landed still leaves that fill row and
        its position behind, and ``paper_fills`` is append-only so nothing can remove them.
        The window is narrowed by :meth:`_assert_account_unmoved` and the residue is made
        resumable by the deterministic ``fill_event_id``, but it is not closed. Closing it
        needs the fill to become one database statement - ``paper_repository``'s header
        records the absence of a multi-statement transaction over PostgREST as a limitation
        of the transport, and ``paper_simulator`` says the same: "closing the gap completely
        requires moving ``submit_intent`` and ``apply_fill`` into a database" function. That
        is reported as PAPER-04 rather than papered over here.
        """
        conflict: Optional[repo.PaperConcurrencyConflict] = None
        for attempt in range(1, self.FILL_ATTEMPTS + 1):
            try:
                return await self._attempt_fill(
                    sb,
                    uid,
                    order_row,
                    quantity=quantity,
                    price=price,
                    release_from_locked=release_from_locked,
                    attempt=attempt,
                )
            except repo.PaperConcurrencyConflict as exc:
                conflict = exc
                if attempt >= self.FILL_ATTEMPTS:
                    break
                delay = self.FILL_RETRY_DELAYS[
                    min(attempt - 1, len(self.FILL_RETRY_DELAYS) - 1)
                ]
                logger.warning(
                    "[PAPER FILL] attempt %s of %s for order %s lost the version guard; "
                    "retrying in %ss. Nothing from this attempt moved the balance.",
                    attempt,
                    self.FILL_ATTEMPTS,
                    order_row.get("id"),
                    delay,
                )
                await asyncio.sleep(delay)

        assert conflict is not None  # the loop only breaks with one

        # Retries exhausted. Report the residue instead of leaving it to be found: an
        # unsettled fill row - one carrying this order's deterministic ``fill_event_id`` that
        # no ``FILL`` ledger row references - is PAPER-04's footprint, and naming it in the
        # 409 is what the session path does for its own analogous partial write. The probe is
        # wrapped because a failed read here must not replace the conflict the caller needs.
        unsettled: Optional[bool] = None
        try:
            unsettled = self._has_unsettled_fill(
                sb, uid, account_id=str(order_row["account_id"]), order_row=order_row
            )
        except Exception:  # noqa: BLE001 - diagnosis must not mask the conflict
            logger.warning(
                "[PAPER FILL] could not determine whether order %s left an unsettled fill "
                "row; the conflict is reported without that detail.",
                order_row.get("id"),
            )
        raise self._conflict(conflict, phase="FILL", partial_write=unsettled)

    def _has_unsettled_fill(
        self,
        sb: Any,
        uid: str,
        *,
        account_id: str,
        order_row: Mapping[str, Any],
    ) -> bool:
        """Whether a fill row for this order exists that no ``FILL`` ledger row references.

        The PAPER-04 footprint, expressed as the query a reconciliation job would run. Also
        the reason the deterministic ``fill_event_id`` matters beyond de-duplication: with a
        random id there was no way to ask this question about a specific order's fill.
        """
        event_id = self._fill_event_id(order_row["id"])
        fills = [
            row
            for row in repo.get_fills(sb, uid, order_id=order_row["id"])
            if str(row.get("fill_event_id")) == event_id
        ]
        if not fills:
            return False
        settled = {
            str(event.get("fill_id") or "")
            for event in repo.get_balance_events(
                sb, uid, account_id=account_id, cause="FILL"
            )
        }
        return any(str(row["id"]) not in settled for row in fills)

    async def _attempt_fill(
        self,
        sb: Any,
        uid: str,
        order_row: Mapping[str, Any],
        *,
        quantity: Decimal,
        price: Decimal,
        release_from_locked: Decimal = Decimal("0"),
        attempt: int = 1,
    ) -> Dict[str, Any]:
        """One attempt at filling the order. Returns the order body.

        The arithmetic is ``paper_accounting.apply_fill`` and nothing here duplicates it: this
        method reads the account and the positions, applies the retained adverse-slippage and
        fee model to get the fill price and the fee, hands both to the engine, **asserts the
        invariants before writing anything** (Requirement 18.14) and then writes what came
        back:

        1. ``paper_fills`` - the fill, with its exact integer Minor_Units fee and slippage.
           First, because it is the **idempotency anchor**: ``uq_paper_fill_event`` is what
           makes a repeated fill a no-op, and a fill row written before the money moves can be
           resumed, whereas money moved before the fill row exists could be applied twice.
        2. ``paper_accounts`` - the new balances, guarded by the ``version`` that was read.
        3. ``paper_balance_events`` - the ledger row carrying this fill's three deltas, which
           is where the per-fill realized PnL lives, and which carries ``fill_id`` and so is
           also what marks the fill above as **settled** for :meth:`_fill_row_for_attempt`.
        4. ``paper_positions`` - the resulting position; two statements for a reversal, so the
           closed leg keeps its own row and the reopened side gets a fresh one.
        5. ``paper_trades`` - only when a position reached exactly zero (Requirement 18.10).
        6. ``paper_orders`` - the order moved to ``FILLED``, with its filled quantity, average
           fill price, fee and slippage.

        THE POSITION USED TO BE WRITTEN SECOND, AND THAT WAS A DIVERGENCE
        ----------------------------------------------------------------
        This path wrote fill -> **position** -> money -> ledger, while the canonical session
        path (``paper_simulator.apply_fill``) writes fill -> money -> ledger -> position, with
        its steps labelled "write 1" to "write 4" and the money annotated "past this point
        nothing is retried". The two orderings were never reconciled, and the divergence had a
        measurable cost: a version conflict at the account UPDATE left behind both an
        unsettled fill row **and a position**, and a position with no cash movement behind it
        is a phantom holding that breaks the equity identity. Measured in
        ``staging/probe_paper02_phantom.py``: ``fills=1 positions=1 balance_events=0`` with
        the account unchanged.

        The order here now matches the canonical one. The fill row keeps its place because its
        earliness is what makes a resume possible; the position has no such justification, so
        it moves behind the money. A conflicted attempt now leaves one unsettled fill row and
        nothing else.

        These are six statements and PostgREST offers no transaction to put them in, which
        ``paper_repository`` records as a limitation of the transport rather than glossing over.
        The assert runs first, so an arithmetic refusal writes nothing at all; the database's own
        guards (``uq_paper_fill_event``, ``chk_paper_order_fill_bound``,
        ``chk_paper_balances_non_negative``, ``uq_paper_position_open``) and the version
        predicate are what make a partial sequence detectable rather than silent. The one
        residue that remains is PAPER-04, described on :meth:`_execute_fill`.
        """
        account_id = str(order_row["account_id"])
        symbol = str(order_row["symbol"])
        side = str(order_row["side"])

        account_row = repo.lock_account_for_update(sb, uid, account_id=account_id)
        account = self._account_of(account_row)
        position_rows = repo.get_positions(
            sb, uid, account_id=account_id, include_closed=True
        )
        positions, _ = self._positions_of(position_rows)
        existing = positions.get(symbol)

        # The retained fill model: adverse slippage only - a buy slips up, a sell slips down -
        # and a fee as a fraction of the filled notional. Task 24.x/25.x own the model itself;
        # this is the one the figures users see today were produced by.
        fill_qty = self.config.qty(quantity)
        reference = self.config.price(price)
        drift = self.config.slippage_rate if side == "buy" else -self.config.slippage_rate
        fill_price = self.config.price(reference * (Decimal("1") + drift))
        fee = self.config.money(fill_qty * fill_price * self.config.fee_rate)
        slippage_cost = self.config.money(abs(fill_price - reference) * fill_qty)
        filled_at = self._get_timestamp()

        # A release can never exceed what is actually locked; ``apply_fill`` would refuse the
        # fill outright (Requirement 18.4), and the estimate was computed at the limit price
        # while the lock was quantized when it was taken.
        release = min(
            self.config.money(release_from_locked), account.locked_balance
        )

        result = accounting.apply_fill(
            account,
            positions,
            {"symbol": symbol, "side": side},
            quantity=fill_qty,
            price=fill_price,
            fee=fee,
            config=self.config,
            filled_at=filled_at,
            release_from_locked=release,
        )
        # Requirement 18.14: the invariants are asserted on the computed state BEFORE the first
        # statement, so a breach leaves the stored rows untouched rather than needing a rollback
        # PostgREST cannot give.
        accounting.assert_invariants(
            result.account,
            result.positions,
            accounting.last_validated_prices(result.positions.values()),
            self.config,
        )

        # ── The last check before the first statement ──────────────────────────────
        # Re-read the account's version and refuse if it moved while this attempt was
        # computing. Everything above is reads and arithmetic, so a conflict discovered
        # here costs nothing; a conflict discovered at ``bump_version`` instead costs an
        # orphaned fill row and position, because ``paper_fills`` is append-only. This does
        # not close the window - two statements still sit between here and the guard - it
        # moves most of it in front of the writes. See :meth:`_execute_fill`'s PAPER-04 note.
        self._assert_account_unmoved(sb, uid, account_id=account_id, seen=account_row)

        # ── The fill row, which is also the resume anchor ──────────────────────────
        # On a retry the row from the previous attempt is already there. Re-inserting would
        # be refused by ``uq_paper_fill_event (order_id, fill_event_id)`` now that the id is
        # deterministic, so the row is adopted instead: if the ledger already carries it the
        # money moved and this is the no-op Requirement 18.13 asks for; if it does not, this
        # attempt finishes the movement rather than losing it.
        fill_row, already_settled = self._fill_row_for_attempt(
            sb,
            uid,
            account_id=account_id,
            order_row=order_row,
            quantity=fill_qty,
            price=fill_price,
            fee=fee,
            slippage_cost=slippage_cost,
            filled_at=filled_at,
            attempt=attempt,
        )
        if already_settled:
            # The order is re-read rather than reusing ``order_row``: the previous attempt
            # that settled this fill also moved the order to ``FILLED``, and answering with
            # the pre-fill image would report an order that is live on the book.
            # ``paper_repository`` has no single-order read, so this filters the account's
            # orders rather than inventing one.
            current = next(
                (
                    row
                    for row in repo.get_orders(sb, uid, account_id=account_id)
                    if str(row.get("id")) == str(order_row["id"])
                ),
                dict(order_row),
            )
            return self._order_body(
                current, repo.get_fills(sb, uid, order_id=order_row["id"])
            )

        # ── write 2: the money. Guarded by the version that was read ───────────────
        # Everything above this line is a read, arithmetic, or the one anchor statement. A
        # conflict here therefore leaves the fill row and NOTHING ELSE, which is what makes
        # the retry in :meth:`_execute_fill` able to resume rather than reconcile.
        repo.bump_version(
            sb,
            user_id=uid,
            account_id=account_id,
            expected_version=account_row["version"],
            payload={
                "available_balance": result.account.available_balance,
                "locked_balance": result.account.locked_balance,
                "realized_pnl": result.account.realized_pnl,
                "total_equity": result.account.total_equity,
                "last_price_at": filled_at,
                "stale": False,
            },
        )
        repo.insert_balance_event(
            sb,
            account_id=account_id,
            user_id=uid,
            cause="FILL",
            available_delta=result.available_delta,
            locked_delta=result.locked_delta,
            realized_delta=result.realized_delta,
            available_after=result.account.available_balance,
            locked_after=result.account.locked_balance,
            realized_after=result.account.realized_pnl,
            fill_id=fill_row["id"],
            occurred_at=filled_at,
        )

        # ── write 4: the position ──────────────────────────────────────────────────
        # After the money, matching ``paper_simulator.apply_fill``'s "write 4". It used to be
        # written before the account UPDATE, which meant a version conflict left a holding
        # with no cash movement behind it. See this method's docstring.
        new_position = result.position
        if (
            existing is not None
            and existing.is_open
            and existing.side != new_position.side
        ):
            # A reversal through zero. The old side is persisted closed in its own row - its
            # history is not overwritten by the side that replaced it - and the new side is
            # then inserted, which the released partial unique index allows.
            repo.upsert_position(
                sb,
                account_id=account_id,
                user_id=uid,
                symbol=symbol,
                side=existing.side,
                size=Decimal("0"),
                entry_price=existing.entry_price,
                opened_at=existing.opened_at or filled_at,
                current_price=fill_price,
                unrealized_pnl=Decimal("0"),
                price_at=filled_at,
                closed_at=filled_at,
            )
        repo.upsert_position(
            sb,
            account_id=account_id,
            user_id=uid,
            symbol=symbol,
            side=new_position.side,
            size=new_position.size,
            entry_price=new_position.entry_price,
            opened_at=new_position.opened_at or filled_at,
            current_price=new_position.current_price,
            unrealized_pnl=new_position.unrealized_pnl,
            price_at=filled_at,
            closed_at=new_position.closed_at,
        )

        if result.closed_trade is not None:
            closed = result.closed_trade
            repo.insert_trade(
                sb,
                account_id=account_id,
                user_id=uid,
                symbol=closed.symbol,
                side=closed.side,
                quantity=closed.quantity,
                entry_price=closed.entry_price,
                exit_price=closed.exit_price,
                realized_pnl=closed.realized_pnl,
                fee_minor=self._minor(closed.fee, "fee_minor"),
                opened_at=closed.opened_at or filled_at,
                closed_at=closed.closed_at or filled_at,
            )

        filled_order = repo.update_order(
            sb,
            user_id=uid,
            order_id=order_row["id"],
            order_state=PaperOrderState.FILLED,
            expected_state=PaperOrderState(order_row["order_state"]),
            filled_quantity=fill_qty,
            avg_fill_price=fill_price,
            fee_minor=self._minor(fee, "fee_minor"),
            slippage_minor=self._minor(slippage_cost, "slippage_minor"),
        )

        logger.info(
            "[PAPER_TRADE] tenant=%s user=%s order=%s exec=%s sym=%s side=%s qty=%s px=%s "
            "fee=%s pnl=%s",
            uid,
            uid,
            filled_order.get("id"),
            fill_row.get("fill_event_id"),
            symbol,
            side.upper(),
            fill_qty,
            fill_price,
            fee,
            result.realized_pnl,
        )
        return self._order_body(filled_order, [fill_row])

    # ══════════════════════════════════════════════════════════════════
    # CANCELLING (Requirement 16.2, 18.4)
    # ══════════════════════════════════════════════════════════════════

    async def cancel_order(
        self,
        user_id: str,
        order_id: str,
        *,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cancel a resting paper limit order and release the capital it locked.

        Cancelling an already cancelled order is still an idempotent no-op, cancelling a filled
        one still raises ``ValueError("Cannot cancel an already filled order")``, and the
        transition itself is guarded by the state the order was read in - so two concurrent
        cancels do not both release the funds.

        **One behaviour is deliberately different.** Another tenant's order is now *not found*
        rather than ``PermissionError``: ``user_id`` is a predicate on the read, so the row is
        never fetched, and answering "forbidden" for an order that exists while answering "not
        found" for one that does not is itself the existence disclosure Requirement 21.4 closes.

        THIS CANCELS DEFAULT-ACCOUNT ORDERS ONLY
        ---------------------------------------
        The read is narrowed to ``session_id IS NULL`` (``read_order(..., session_id=None)``),
        which is the same default-account scope every other retained ``/api/paper/*`` endpoint
        answers from - :meth:`get_orders`, :meth:`get_positions`, :meth:`check_limit_orders` and
        :meth:`reset_account` all narrow to that one book, and Requirement 17.12 is that the
        retained endpoints keep those semantics.

        A Paper_Session's order is therefore *not found* here, exactly as an unknown id is: same
        status, same code, same message, and ``details`` carrying only the identifier the caller
        supplied. Scoped by ``(user_id, order_id)`` alone this path would have moved a session's
        order to ``CANCELLED`` and released capital on the SESSION's account while writing no
        ``paper_events`` row for it - bypassing the state machine
        ``backend/paper/paper_session_service.py`` owns and leaving a hole in the session's audit
        trail (Requirements 17.7, 17.15, 19.2). Session orders are cancelled by the session's own
        reset, which takes the transition and writes the event.
        """
        uid = str(user_id)
        sb = self._client(access_token)

        try:
            order = repo.read_order(sb, uid, order_id, session_id=None)
            if order is None:
                raise ValueError(f"Order {order_id} not found")

            status = str(order.get("legacy_status"))
            if status == PaperOrderStatus.CANCELLED.value:
                return self._order_body(
                    order, repo.get_fills(sb, uid, order_id=order["id"])
                )
            if status == PaperOrderStatus.FILLED.value:
                raise ValueError("Cannot cancel an already filled order")
            if status != PaperOrderStatus.OPEN.value:
                raise ValueError(f"Cannot cancel order in status '{status}'")

            account_id = str(order.get("account_id"))
            if (
                str(order.get("side")) == "buy"
                and str(order.get("order_type")) == "limit"
                and order.get("limit_price") is not None
            ):
                occurred_at = self._get_timestamp()
                before_row = repo.lock_account_for_update(sb, uid, account_id=account_id)
                before = self._account_of(before_row)
                reserved = self.config.money(
                    accounting.to_decimal(order["quantity"], "quantity")
                    * accounting.to_decimal(order["limit_price"], "limit_price")
                    * (Decimal("1") + self.default_fee_rate)
                )
                # Never more than is actually locked. The old implementation credited the
                # estimate and floored ``locked_balance`` at zero, which could return more cash
                # than was ever reserved; ``unlock`` refuses to overdraw the locked column
                # (Requirement 18.4).
                release = min(reserved, before.locked_balance)
                unlocked = accounting.unlock(before, release, self.config)
                repo.bump_version(
                    sb,
                    user_id=uid,
                    account_id=account_id,
                    expected_version=before_row["version"],
                    payload={
                        "available_balance": unlocked.available_balance,
                        "locked_balance": unlocked.locked_balance,
                        "total_equity": before.total_equity,
                    },
                )
                repo.insert_balance_event(
                    sb,
                    account_id=account_id,
                    user_id=uid,
                    cause="ORDER_UNLOCK",
                    available_delta=unlocked.available_balance - before.available_balance,
                    locked_delta=unlocked.locked_balance - before.locked_balance,
                    realized_delta=Decimal("0"),
                    available_after=unlocked.available_balance,
                    locked_after=unlocked.locked_balance,
                    realized_after=unlocked.realized_pnl,
                    occurred_at=occurred_at,
                )

            cancelled = repo.update_order(
                sb,
                user_id=uid,
                order_id=order["id"],
                order_state=PaperOrderState.CANCELLED,
                expected_state=PaperOrderState(order["order_state"]),
            )
            return self._order_body(
                cancelled, repo.get_fills(sb, uid, order_id=cancelled["id"])
            )
        except repo.PaperConcurrencyConflict as exc:
            raise self._conflict(exc) from exc
        except accounting.InvariantViolation as exc:
            logger.error("A paper cancellation was refused by an invariant: %s", exc)
            raise PaperError(
                PAPER_INVARIANT_VIOLATION, details={"invariant": exc.invariant}
            ) from exc
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "order cancellation") from exc

    # ══════════════════════════════════════════════════════════════════
    # THE LIMIT MATCHING ENGINE
    # ══════════════════════════════════════════════════════════════════

    async def check_limit_orders(
        self,
        symbol: str,
        current_price: Decimal,
        user_id: Optional[str] = None,
        *,
        access_token: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fill the identity's resting limit orders in ``symbol`` that ``current_price`` triggers.

        The fill conditions are unchanged: a buy fills when the price is at or below its limit,
        a sell when it is at or above, and the fill runs through the same
        :meth:`_execute_fill`. A buy's reservation is released and then spent, so the locked
        capital is not spent twice.

        ``user_id`` IS REQUIRED, AND THAT IS THE ONE SIGNATURE CHANGE
        -----------------------------------------------------------
        The in-memory matcher walked ``self._orders.values()`` - every tenant's orders in one
        process dictionary. There is no such read here and there must not be: ``user_id`` is a
        predicate on every statement, row-level security is scoped to one identity, and a
        cross-tenant scan would need an unscoped read of ``paper_orders`` that
        ``paper_repository`` deliberately does not offer (Requirements 21.2, 21.5). So the
        identity is now an argument, and omitting it is refused rather than silently answering
        "nothing matched" - which would look like a working matcher that never fills anything.

        Nothing in the application called this method; ``tests/test_paper_trading_lifecycle.py``
        was its only caller. The per-session matcher tasks 26.x/27.x add is what drives fills
        for a Paper_Session.
        """
        if user_id is None:
            raise ValueError(
                "check_limit_orders requires the user_id whose resting orders are to be "
                "matched: paper orders are persisted per tenant and every read carries user_id "
                "as a predicate, so there is no cross-tenant scan to fall back on"
            )

        uid = str(user_id)
        sb = self._client(access_token)
        clean_sym = self._clean_symbol(symbol)
        cur_px = self.config.price(self._exact(current_price, "current_price"))
        filled_orders: List[Dict[str, Any]] = []

        try:
            account = repo.get_or_create_account(
                sb, uid, repo.DEFAULT_CURRENCY, None, initial_capital=self.default_capital
            )
            resting = repo.get_orders(
                sb,
                uid,
                account_id=account["id"],
                legacy_status=PaperOrderStatus.OPEN.value,
                symbol=clean_sym,
            )

            for order in resting:
                if str(order.get("order_type")) != "limit":
                    continue
                if order.get("limit_price") is None:
                    logger.error(
                        "A resting paper limit order carries no limit price and cannot be "
                        "matched; it is left on the book. order=%s",
                        order.get("id"),
                    )
                    continue

                order_side = str(order.get("side"))
                limit_px = accounting.to_decimal(order["limit_price"], "limit_price")
                qty = accounting.to_decimal(order["quantity"], "quantity")

                should_fill = (order_side == "buy" and cur_px <= limit_px) or (
                    order_side == "sell" and cur_px >= limit_px
                )
                if not should_fill:
                    continue

                reserved = (
                    self.config.money(
                        qty * limit_px * (Decimal("1") + self.default_fee_rate)
                    )
                    if order_side == "buy"
                    else Decimal("0")
                )
                filled_orders.append(
                    await self._execute_fill(
                        sb,
                        uid,
                        order,
                        quantity=qty,
                        price=cur_px,
                        release_from_locked=reserved,
                    )
                )
        except repo.PaperConcurrencyConflict as exc:
            raise self._conflict(exc) from exc
        except accounting.InvariantViolation as exc:
            logger.error("A paper limit fill was refused by an invariant: %s", exc)
            raise PaperError(
                PAPER_INVARIANT_VIOLATION, details={"invariant": exc.invariant}
            ) from exc
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "limit order matching") from exc

        return filled_orders


# ══════════════════════════════════════════════════════════════════════════
# THE SINGLETON
# ══════════════════════════════════════════════════════════════════════════
#
# Retained exactly as it was. ``routers/risk.py``, ``routers/paper_trading.py`` and
# ``backend/dashboard_aggregation_service.py`` all import ``get_paper_trading_service`` by name,
# and the instance now holds configuration only - no balance, no position, no order - so one
# process-wide instance is a configuration holder rather than a store.

_paper_service_instance: Optional[PaperTradingService] = None


def get_paper_trading_service() -> PaperTradingService:
    global _paper_service_instance
    if _paper_service_instance is None:
        _paper_service_instance = PaperTradingService()
    return _paper_service_instance
