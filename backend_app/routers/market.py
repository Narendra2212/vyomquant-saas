"""
routers/market.py — REST market data snapshots.

FIXES APPLIED:
  N7/SCALE: Uses exchange pool (get_or_create_exchange) instead of opening a new
            CCXT authenticated connection for every HTTP request.
            At 500 users × frequent chart refreshes, the old approach opened
            thousands of connections per minute.
"""

import logging
from typing import List, Optional
from urllib.parse import unquote

from fastapi import (APIRouter, Depends, HTTPException, Query, Request,
                     Response)

from backend_app.backend.connection_engine import (ConnectionEngine,
                                                   get_or_create_exchange)
from backend_app.backend.data_seeking_engine import DataEngine
from backend_app.core.dependencies import get_current_user, get_vault
from backend_app.core.rate_limit import limiter  # BE-CRITICAL-003 FIX

router = APIRouter()
logger = logging.getLogger("MarketRouter")


#: The whole matching set, expressed as a page size because the discovery service takes one.
#:
#: `/symbols` returns every market that matches the caller's filters — the first-fifty
#: truncation it used to apply is the defect task 7.2 removes, and replacing it with a
#: bigger undisclosed cap would be the same defect with a bigger number. This is a ceiling
#: only in the arithmetic sense: the pager slices, so the value allocates nothing, and a
#: CCXT universe across every venue the platform's executor supports is four figures of
#: markets. If it were ever exceeded, `next_cursor` would be non-null and the response would
#: say `X-Asset-Truncated: true` rather than passing a partial set off as the whole one.
SYMBOLS_WHOLE_SET = 1_000_000

#: The ceiling on a *caller-requested* `limit`. Bounds what one client can ask to be
#: assembled per request; it does not bound the default answer.
SYMBOLS_REQUESTED_LIMIT_CEILING = 5000


async def _get_data_engine(user, vault, exchange_id: str) -> DataEngine:
    """
    SCALE FIX: Shares one CCXT instance per user via the exchange pool.
    Falls back to a public unauthenticated connection if keys not available.
    """
    try:
        keys = vault.load_decrypted_keys(
            user["id"],
            exchange_id,
            access_token=user.get("access_token"),
        )
        # FIX: Guard against None or incomplete dict from vault
        if not keys or not isinstance(keys, dict):
            raise ValueError("Invalid or missing keys from vault")
        
        exchange = await get_or_create_exchange(
            user_id=user["id"],
            exchange_id=exchange_id,
            api_key=keys["api_key"],
            secret_key=keys["secret_key"],
            password=keys.get("password"),
        )
        return DataEngine(exchange)
    except Exception:
        # Public fallback — market data doesn't need auth
        bridge = ConnectionEngine(exchange_id=exchange_id)
        exchange = await bridge.connect()
        return DataEngine(exchange)


@router.get("/candles/{symbol}/{timeframe}")
async def get_candles(
    symbol: str,
    timeframe: str = "5m",
    limit: int = Query(200, ge=1, le=1000),
    exchange: str = Query("binance"),
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
):
    symbol = unquote(symbol).replace("-", "/")
    de = await _get_data_engine(user, vault, exchange)
    return await de.fetch_historical_ohlcv(symbol, timeframe, limit)


@router.get("/orderbook/{symbol}")
async def get_orderbook(
    symbol: str,
    limit: int = Query(20, ge=1, le=100),
    exchange: str = Query("binance"),
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
):
    symbol = unquote(symbol).replace("-", "/")
    de = await _get_data_engine(user, vault, exchange)
    book = await de.fetch_order_book_snapshot(symbol, limit)
    return {"asks": book["asks"][:limit], "bids": book["bids"][:limit]}


@router.get("/ticker/{symbol}")
async def get_ticker(
    symbol: str,
    exchange: str = Query("binance"),
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
):
    symbol = unquote(symbol).replace("-", "/")
    de = await _get_data_engine(user, vault, exchange)
    return await de.fetch_ticker_snapshot(symbol)


@router.get("/funding/{symbol}")
async def get_funding_rate(
    symbol: str,
    exchange: str = Query("binance"),
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
):
    symbol = unquote(symbol).replace("-", "/")
    de = await _get_data_engine(user, vault, exchange)
    return await de.fetch_funding_rate(symbol)


@router.get("/symbols")
@limiter.limit("60/minute")  # BE-CRITICAL-003 FIX: Add rate limiting
async def get_symbols(  # BE-CRITICAL-003 FIX: Require authentication
    request: Request,
    response: Response,
    search: Optional[str] = Query(
        None, max_length=64, description="Free-text match over symbol, base and quote."
    ),
    base: Optional[str] = Query(None, max_length=32, description="Base currency, exact."),
    quote: Optional[str] = Query(
        None,
        max_length=32,
        description=(
            "Quote currency, exact. Pass 'USDT' to get the USDT pairs this endpoint used "
            "to be hardwired to; omit it to get every quote the platform can trade."
        ),
    ),
    market_type: Optional[str] = Query(
        None, description="One of 'spot', 'swap', 'future'."
    ),
    active_only: bool = Query(
        True, description="Keep only markets the venue reports as active."
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=SYMBOLS_REQUESTED_LIMIT_CEILING,
        description=(
            "Optional caller-requested cap. Omit it and the whole matching set is "
            "returned; there is no server-imposed truncation."
        ),
    ),
    user: dict = Depends(get_current_user),
):
    """The trading symbols the platform can actually trade, served from the cached universe.

    Spec: strategy-builder task 7.2. Requirements 11.1, 11.5, 11.6.

    WHAT THIS USED TO DO, AND WHY NONE OF IT SURVIVED
    -------------------------------------------------
    Four defects, all removed:

    * ``ccxt.binance().load_markets()`` **synchronously inside the request** - a blocking
      network round-trip on the event loop, once per caller, against one hardwired venue.
      The universe is now assembled off the request path by
      ``asset_universe.AssetUniverseRefresher`` and cached for 6 h; this handler reads the
      cache and nothing else (Requirement 11.5).
    * ``symbol.endswith('/USDT')`` - a filter that hid every market with another quote.
      The quote is now a *caller's* filter with no default, so nothing is hidden by
      default and ``?quote=USDT`` reproduces the old view deliberately.
    * ``usdt_pairs[:50]`` - an undisclosed alphabetical truncation, which is why no author
      could ever select a market beyond the letter B. There is no server-imposed cap now.
      ``limit`` exists for callers that want fewer, and when it truncates the response says
      so in ``X-Asset-Truncated`` alongside the real ``X-Asset-Total``.
    * the ten-symbol ``except`` fallback - the actual lie: a hardcoded list served with a
      200, indistinguishable to a client from a live answer. An unavailable universe is now
      ``503 ASSET_UNIVERSE_UNAVAILABLE`` (Requirement 11.6). Not a fallback, and not an
      empty array either - an empty array would read as "this platform lists nothing",
      which is the same untruth in a different costume.

    RESPONSE SHAPE: DELIBERATELY UNCHANGED
    --------------------------------------
    A bare JSON array of symbol strings, exactly as before. ``typed-client.ts`` types this
    as ``Promise<string[]>``, ``api/modules/market.js`` documents it as ``string[]`` and
    ``DataPipelineContext.jsx`` tests the body with ``Array.isArray`` - changing the shape
    would break all three at runtime, and the builder's selector wiring is task 7.3's
    footprint. The provenance the richer shape would carry is served in headers instead, and
    the canonical record-bearing, paginated surface already exists at
    ``GET /api/strategy-operations/assets`` (task 7.1), which is what ``Link`` points at.
    A second full-fat discovery response here would be a second surface to keep in step.

    Headers
        ``X-Asset-Total`` markets matching the filters; ``X-Asset-Returned`` distinct
        symbols in the body (lower than the total when one symbol trades as more than one
        market type); ``X-Asset-Truncated``; ``X-Asset-Universe-Hash``,
        ``X-Asset-Universe-Stale`` and ``X-Asset-Universe-Age-Seconds`` for provenance.

    Status codes
        **200** the matching symbols, possibly an empty array when a *filter* matched
        nothing - which is a different statement from "the universe is unavailable".
        **503** ``ASSET_UNIVERSE_UNAVAILABLE`` with ``Retry-After``.
        **401** from ``get_current_user``; **429** from the existing limiter.
    """
    from backend_app.backend.asset_universe import (AssetQuery,
                                                    AssetUniverseUnavailable,
                                                    RETRY_AFTER_SECONDS,
                                                    discover_assets)

    query = AssetQuery(
        search=search,
        base=base,
        quote=quote,
        market_type=market_type,
        active_only=active_only,
        # No cursor: pagination belongs to `/api/strategy-operations/assets`, which
        # publishes the cursor. Here `limit` is the only narrowing, and omitting it means
        # "all of it" rather than "the first fifty".
        limit=limit if limit is not None else SYMBOLS_WHOLE_SET,
        cursor=None,
    )

    try:
        page = await discover_assets(query)
    except AssetUniverseUnavailable as e:
        logger.warning("Symbol list unavailable — no cached universe to serve: %s", e)
        raise HTTPException(
            status_code=503,
            detail={
                "error": getattr(e, "code", "ASSET_UNIVERSE_UNAVAILABLE"),
                "message": str(e),
                "last_refresh_error": e.last_error,
                "refresh_in_flight": e.refresh_in_flight,
                "retry_after_seconds": RETRY_AFTER_SECONDS,
            },
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )

    # One symbol can be two markets — `BTC/USDT` spot and `BTC/USDT` future on venues that
    # list both — and this endpoint's contract is a list of symbol *strings*. De-duplicated
    # in served order (listing breadth, then symbol) rather than re-sorted alphabetically:
    # alphabetical order is what made the old truncation so damaging.
    symbols: List[str] = []
    seen = set()
    for asset in page["assets"]:
        symbol = asset["symbol"]
        if symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)

    meta = page["source_meta"]
    response.headers["X-Asset-Total"] = str(page["total"])
    response.headers["X-Asset-Returned"] = str(len(symbols))
    # True only when a caller-supplied `limit` actually cut the set short. `next_cursor` is
    # the service's own statement that records were left behind.
    response.headers["X-Asset-Truncated"] = (
        "true" if page["next_cursor"] is not None else "false"
    )
    response.headers["X-Asset-Universe-Hash"] = meta["universe_hash"]
    response.headers["X-Asset-Universe-Stale"] = "true" if meta["stale"] else "false"
    response.headers["X-Asset-Universe-Age-Seconds"] = str(meta["age_seconds"])
    response.headers["Link"] = (
        '</api/strategy-operations/assets>; rel="successor-version"'
    )
    # Authenticated response: never a shared cache.
    response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return symbols


@router.get("/data/{symbol}/{timeframe}")
async def get_market_data(
    symbol: str,
    timeframe: str = "5m",
    limit: int = Query(200, ge=1, le=1000),
    exchange: str = Query("binance"),
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
):
    """Alias for /candles endpoint for frontend compatibility"""
    return await get_candles(symbol, timeframe, limit, exchange, user, vault)

@router.get("/data/historical")
async def get_historical_data(
    symbol: str,
    timeframe: str = "5m",
    start_date: str = None,
    end_date: str = None,
    exchange: str = Query("binance"),
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
):
    symbol = unquote(symbol).replace("-", "/")
    de = await _get_data_engine(user, vault, exchange)
    # limit defaults to 1000 since start_date/end_date is not supported directly in de.fetch_historical_ohlcv
    return await de.fetch_historical_ohlcv(symbol, timeframe, 1000)
