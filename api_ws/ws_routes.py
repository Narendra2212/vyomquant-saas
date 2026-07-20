"""
api_ws/ws_routes.py — All WebSocket endpoints.

FIXES:
  WSR-1: _validate_ws_token() uses the get_supabase() singleton (not new client per check)
  WSR-2: _get_public_exchange() has reconnection logic for when Binance WS drops
  WSR-3: _get_or_create_exchange() delegates to connection_engine.get_or_create_exchange()
         so WS and REST share ONE pool (not two separate ones)
  WSR-4: ws_user() spins private stream tasks with create_task() independently and keeps
         the heartbeat loop running correctly instead of blocking on gather()
"""

import asyncio
import logging
from urllib.parse import unquote

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from fastapi.websockets import WebSocketState

from backend.services.dependencies import (
    get_ws_manager,
    get_vault,
)  # get_supabase temporarily disabled
from backend.services.state import app_state

from backend.engines.data_engine import DataEngine

logger = logging.getLogger("WSRoutes")
ws_router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════════════════════════

_public_exchange = None
_public_lock = asyncio.Lock()
_public_fail_count = 0
_PUBLIC_MAX_FAILS = 3


async def _get_public_exchange():
    """
    WSR-2: Returns a healthy public CCXT instance.
    Resets and reconnects if the instance is closed or has failed too many times.
    """
    global _public_exchange, _public_fail_count
    async with _public_lock:
        needs_reconnect = (
            _public_exchange is None
            or _public_fail_count >= _PUBLIC_MAX_FAILS
            or getattr(_public_exchange, "closed", False)
        )
        if needs_reconnect:
            if _public_exchange:
                try:
                    await _public_exchange.close()
                except Exception:
                    pass
            bridge = ConnectionEngine(exchange_id="binance")
            _public_exchange = await bridge.connect()
            _public_fail_count = 0
            logger.info("[WS] Public exchange reconnected.")
    return _public_exchange


def _mark_public_exchange_failed():
    global _public_fail_count
    _public_fail_count += 1


async def _validate_ws_token(token: str, claimed_user_id: str) -> bool:
    """
    WSR-1: Uses the module-level Supabase singleton instead of creating a new
    client per WebSocket authentication check.
    """
    try:
        supabase = get_supabase()  # WSR-1: singleton
        resp = supabase.auth.get_user(token)
        return resp and resp.user and resp.user.id == claimed_user_id
    except Exception as e:
        logger.warning(f"[WS] Token validation failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════
#  PUBLIC FEEDS
# ══════════════════════════════════════════════════════════════════════════


@ws_router.websocket("/ws/ticker/{symbol}")
async def ws_ticker(websocket: WebSocket, symbol: str):
    symbol = unquote(symbol).replace("-", "/")
    manager = get_ws_manager()

    await websocket.accept()
    await manager.subscribe("ticker", symbol, websocket)

    exchange = await _get_public_exchange()
    data_engine = DataEngine(exchange)
    stream_task = asyncio.create_task(_stream_ticker(data_engine, symbol, manager))

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text('{"type":"ping"}')
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"[WS/ticker] {symbol}: {e}")
    finally:
        stream_task.cancel()
        await manager.unsubscribe("ticker", symbol, websocket)


async def _stream_ticker(data_engine, symbol, manager):
    try:
        async for ticks in data_engine.stream_ticker(symbol):
            if not ticks:
                continue
            tick = ticks if isinstance(ticks, dict) else (ticks[0] if ticks else {})
            await manager.broadcast_ticker(
                symbol,
                {
                    "type": "ticker",
                    "symbol": symbol,
                    "last": tick.get("last"),
                    "bid": tick.get("bid"),
                    "ask": tick.get("ask"),
                    "percentage": tick.get("percentage"),
                    "volume": tick.get("baseVolume"),
                    "high": tick.get("high"),
                    "low": tick.get("low"),
                    "timestamp": tick.get("timestamp"),
                },
            )
    except Exception as e:
        logger.warning(f"[WS/ticker] stream error for {symbol}: {e}")
        _mark_public_exchange_failed()


@ws_router.websocket("/ws/orderbook/{symbol}")
async def ws_orderbook(websocket: WebSocket, symbol: str, depth: int = Query(20)):
    symbol = unquote(symbol).replace("-", "/")
    manager = get_ws_manager()

    await websocket.accept()
    await manager.subscribe("orderbook", symbol, websocket)

    exchange = await _get_public_exchange()
    stream_task = asyncio.create_task(
        _stream_orderbook(DataEngine(exchange), symbol, depth, manager)
    )

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text('{"type":"ping"}')
    except WebSocketDisconnect:
        pass
    finally:
        stream_task.cancel()
        await manager.unsubscribe("orderbook", symbol, websocket)


async def _stream_orderbook(data_engine, symbol, depth, manager):
    try:
        async for book in data_engine.stream_order_book(symbol):
            await manager.broadcast_orderbook(
                symbol,
                {
                    "type": "orderbook",
                    "symbol": symbol,
                    "asks": book.get("asks", [])[:depth],
                    "bids": book.get("bids", [])[:depth],
                    "ts": book.get("timestamp"),
                },
            )
    except Exception as e:
        logger.warning(f"[WS/orderbook] stream error: {e}")
        _mark_public_exchange_failed()


@ws_router.websocket("/ws/candles/{symbol}/{timeframe}")
async def ws_candles(websocket: WebSocket, symbol: str, timeframe: str = "5m"):
    symbol = unquote(symbol).replace("-", "/")
    manager = get_ws_manager()
    channel_key = f"{symbol}_{timeframe}"

    await websocket.accept()
    await manager.subscribe("candles", channel_key, websocket)

    exchange = await _get_public_exchange()
    stream_task = asyncio.create_task(
        _stream_candles(DataEngine(exchange), symbol, timeframe, channel_key, manager)
    )

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=60)
            except asyncio.TimeoutError:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text('{"type":"ping"}')
    except WebSocketDisconnect:
        pass
    finally:
        stream_task.cancel()
        await manager.unsubscribe("candles", channel_key, websocket)


async def _stream_candles(data_engine, symbol, timeframe, channel_key, manager):
    try:
        async for candles in data_engine.stream_live_ohlcv(symbol, timeframe):
            if not candles:
                continue
            latest = candles[-1] if isinstance(candles[0], list) else candles
            await manager.broadcast_candles(
                channel_key,
                {
                    "type": "candle",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "ts": latest[0],
                    "open": latest[1],
                    "high": latest[2],
                    "low": latest[3],
                    "close": latest[4],
                    "volume": latest[5],
                },
            )
    except Exception as e:
        logger.warning(f"[WS/candles] stream error: {e}")
        _mark_public_exchange_failed()


# ══════════════════════════════════════════════════════════════════════════
#  PRIVATE CHANNEL
# ══════════════════════════════════════════════════════════════════════════


@ws_router.websocket("/ws/user/{user_id}")
async def ws_user(
    websocket: WebSocket,
    user_id: str,
    token: str = Query(...),
    exchange_id: str = Query("binance"),
):
    manager = get_ws_manager()

    if not await _validate_ws_token(token, user_id):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    await manager.subscribe("user", user_id, websocket)
    logger.info(f"[WS/user] Private channel open: {user_id}")

    # WSR-3: Use the shared exchange pool
    vault = get_vault()
    keys = vault.load_decrypted_keys(user_id, exchange_id)
    exchange = await get_or_create_exchange(
        user_id, exchange_id, keys["api_key"], keys["secret_key"], keys.get("password")
    )
    de = DataEngine(exchange)

    # WSR-4: Spin tasks independently — do NOT use gather() on infinite loops
    t1 = asyncio.create_task(_stream_fills(de, user_id, manager))
    t2 = asyncio.create_task(_stream_orders(de, user_id, manager))
    t3 = asyncio.create_task(_stream_balance(de, user_id, manager))

    try:
        # Heartbeat loop runs correctly now — not blocked by gather()
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text('{"type":"ping"}')
    except WebSocketDisconnect:
        logger.info(f"[WS/user] Disconnected: {user_id}")
    except Exception as e:
        logger.warning(f"[WS/user] Error: {e}")
    finally:
        t1.cancel()
        t2.cancel()
        t3.cancel()
        await manager.unsubscribe("user", user_id, websocket)


async def _stream_fills(de, user_id, manager):
    try:
        async for trades in de.stream_my_trades():
            if not trades:
                continue
            for trade in (trades if isinstance(trades, list) else [trades]):
                await manager.broadcast_user(
                    user_id,
                    {
                        "type": "fill",
                        "symbol": trade.get("symbol"),
                        "side": trade.get("side"),
                        "amount": trade.get("amount"),
                        "price": trade.get("price"),
                        "fee": trade.get("fee", {}).get("cost"),
                        "order_id": trade.get("order"),
                        "timestamp": trade.get("timestamp"),
                    },
                )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"[WS/fills] stream error for {user_id}: {e}")


async def _stream_orders(de, user_id, manager):
    try:
        async for orders in de.stream_user_orders():
            if not orders:
                continue
            for order in (orders if isinstance(orders, list) else [orders]):
                await manager.broadcast_user(
                    user_id,
                    {
                        "type": "order_update",
                        "order_id": order.get("id"),
                        "symbol": order.get("symbol"),
                        "status": order.get("status"),
                        "filled": order.get("filled"),
                        "remaining": order.get("remaining"),
                        "timestamp": order.get("timestamp"),
                    },
                )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"[WS/orders] stream error for {user_id}: {e}")


async def _stream_balance(de, user_id, manager):
    try:
        async for balance in de.stream_user_balance():
            if not balance:
                continue
            await manager.broadcast_user(
                user_id,
                {
                    "type": "balance",
                    "total": balance.get("total", {}),
                    "free": balance.get("free", {}),
                    "used": balance.get("used", {}),
                },
            )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"[WS/balance] stream error for {user_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════
#  P&L PUSH
# ══════════════════════════════════════════════════════════════════════════


@ws_router.websocket("/ws/pnl/{user_id}")
async def ws_pnl(
    websocket: WebSocket,
    user_id: str,
    token: str = Query(...),
):
    manager = get_ws_manager()

    if not await _validate_ws_token(token, user_id):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    await manager.subscribe("pnl", user_id, websocket)

    push_task = asyncio.create_task(_push_pnl(app_state.telemetry, user_id, manager))

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text('{"type":"ping"}')
    except WebSocketDisconnect:
        pass
    finally:
        push_task.cancel()
        await manager.unsubscribe("pnl", user_id, websocket)


async def _push_pnl(telemetry, user_id, manager):
    while True:
        try:
            result = await telemetry.get_live_pnl(user_id)
            if result and result.get("dataset"):
                cols = result.get("columns", [])
                rows = result.get("dataset", [])
                if rows:
                    pnl_data = {
                        "type": "pnl",
                        **{c["name"]: v for c, v in zip(cols, rows[0])},
                    }
                    await manager.broadcast_pnl(user_id, pnl_data)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"[WS/pnl] QuestDB query error: {e}")
        await asyncio.sleep(2)
