"""
routers/market.py — REST market data snapshots.

FIXES APPLIED:
  N7/SCALE: Uses exchange pool (get_or_create_exchange) instead of opening a new
            CCXT authenticated connection for every HTTP request.
            At 500 users × frequent chart refreshes, the old approach opened
            thousands of connections per minute.
"""

import logging
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Query, Request

from backend_app.backend.connection_engine import (ConnectionEngine,
                                                   get_or_create_exchange)
from backend_app.backend.data_seeking_engine import DataEngine
from backend_app.core.dependencies import get_current_user, get_vault
from backend_app.core.rate_limit import limiter  # BE-CRITICAL-003 FIX

router = APIRouter()
logger = logging.getLogger("MarketRouter")


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
async def get_symbols(request: Request, user: dict = Depends(get_current_user)):  # BE-CRITICAL-003 FIX: Require authentication
    """
    Get available trading symbols from CCXT
    
    BE-CRITICAL-003 FIX: Now requires authentication and rate limiting to prevent abuse.
    """
    try:
        import ccxt as ccxt_base
        # Get symbols from Binance as the default exchange
        exchange = ccxt_base.binance()
        exchange.load_markets()
        
        # Return USDT pairs only, sorted by symbol name (top 50)
        usdt_pairs = [symbol for symbol in exchange.markets if symbol.endswith('/USDT')]
        # Sort and take top 50
        usdt_pairs.sort()
        return usdt_pairs[:50]
    except Exception as e:
        logger.warning(f"Failed to load symbols from CCXT: {e}, using fallback")
        # Fallback to hardcoded list
        return [
            "BTC/USDT",
            "ETH/USDT", 
            "SOL/USDT",
            "BNB/USDT",
            "XRP/USDT",
            "ADA/USDT",
            "DOGE/USDT",
            "MATIC/USDT",
            "DOT/USDT",
            "LTC/USDT"
        ]


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
