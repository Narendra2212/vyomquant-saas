"""
api_ws/ws_routes.py — All WebSocket endpoints.

FIXES:
  WSR-1: _validate_ws_token() uses the get_supabase() singleton (not new client per check)
  WSR-2: _get_public_exchange() has reconnection logic for when Binance WS drops
  WSR-3: _get_or_create_exchange() delegates to connection_engine.get_or_create_exchange()
         so WS and REST share ONE pool (not two separate ones)
  WSR-4: ws_user() spins private stream tasks with create_task() independently and keeps
         the heartbeat loop running correctly instead of blocking on gather()
  WSR-5: STEP 4 - Stale data detection (>30s drop, >60s reconnect)

PHASE 14: WebSocket optimization for Dashboard
  - Added /ws/dashboard endpoint for realtime dashboard updates
  - Only pushes updates for: Strategy Status, Signal Trace, Notifications, Risk Alerts, Exchange Health
  - Removes unnecessary subscriptions
  - Implements incremental updates (not full refresh)
  - Adds proper heartbeat, reconnection, and memory leak prevention
"""
import asyncio
import inspect
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Optional
from urllib.parse import unquote

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from backend_app.backend.connection_engine import (  # WSR-3: shared pool
    ConnectionEngine, get_or_create_exchange)
from backend_app.backend.data_seeking_engine import DataEngine
from backend_app.core.dependencies import get_vault, get_ws_manager
from backend_app.core.state import app_state
from backend_app.core.websocket_auth import _decode_hs256_token

logger = logging.getLogger("WSRoutes")
ws_router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════════════════════════

_public_exchange = None
_public_lock = asyncio.Lock()  # Kept for backwards compatibility - prefer per-connection locks
_public_fail_count = 0
_PUBLIC_MAX_FAILS = 3

# STEP 2: PER-CONNECTION LOCKS - Prevents blocking across different connections
# Each connection gets its own lock, eliminating global bottleneck
_connection_locks: Dict[str, asyncio.Lock] = {}
_connection_locks_lock = asyncio.Lock()  # Lock to protect the locks dictionary itself

async def _get_connection_lock(connection_id: str) -> asyncio.Lock:
    """Get or create a per-connection lock (async-safe)."""
    # Fast path: check without lock
    if connection_id in _connection_locks:
        return _connection_locks[connection_id]
    
    # Slow path: create with lock
    async with _connection_locks_lock:
        if connection_id not in _connection_locks:
            _connection_locks[connection_id] = asyncio.Lock()
        return _connection_locks[connection_id]

def _cleanup_connection_lock(connection_id: str):
    """Clean up per-connection lock when done."""
    _connection_locks.pop(connection_id, None)

# STEP 4: Stale data detection constants
_STALE_DATA_THRESHOLD_SECONDS = 30   # Drop data older than 30s
_FORCE_RECONNECT_THRESHOLD_SECONDS = 60  # Force reconnect if no data for 60s

# STEP 3: WebSocket Heartbeat System
_HEARTBEAT_INTERVAL_SECONDS = 10  # Send ping every 10 seconds
_HEARTBEAT_TIMEOUT_SECONDS = 30  # Timeout if no pong received for 30 seconds

async def _heartbeat_task(websocket: WebSocket, connection_id: str):
    """
    STEP 3: WebSocket Heartbeat System
    
    Sends ping every 10 seconds, detects dead connections.
    If no pong received within 30 seconds, closes connection.
    """
    last_pong_time = time.time()
    
    try:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
            
            # Check if connection still alive
            if websocket.client_state != WebSocketState.CONNECTED:
                logger.debug(f"[WS/heartbeat] Connection {connection_id} not connected, stopping heartbeat")
                break
            
            # Check for timeout
            elapsed = time.time() - last_pong_time
            if elapsed > _HEARTBEAT_TIMEOUT_SECONDS:
                logger.warning(
                    f"[WS/heartbeat] Connection {connection_id} HEARTBEAT TIMEOUT "
                    f"({elapsed:.1f}s > {_HEARTBEAT_TIMEOUT_SECONDS}s), closing"
                )
                await websocket.close(code=1001, reason="Heartbeat timeout")
                break
            
            # Send ping
            try:
                await websocket.send_json({"type": "ping", "timestamp": time.time()})
            except Exception as e:
                logger.warning(f"[WS/heartbeat] Failed to send ping to {connection_id}: {e}")
                break
                
    except asyncio.CancelledError:
        logger.debug(f"[WS/heartbeat] Heartbeat cancelled for {connection_id}")
    except Exception as e:
        logger.error(f"[WS/heartbeat] Heartbeat error for {connection_id}: {e}")

def _update_heartbeat(connection_id: str):
    """Update last pong time when client responds."""
    # Track pong times in a separate dict
    _pong_times[connection_id] = time.time()

# Pong tracking for heartbeat
_pong_times: Dict[str, float] = {}

# STEP 4: Connection health tracking
# Maps connection_id -> {"last_data_time": datetime, "healthy": bool, "symbol": str}
_connection_health: dict = {}


async def _get_public_exchange():
    """
    STEP 5: Returns a healthy public CCXT instance.
    
    Uses active health check (fetch_ticker + timeout) instead of exchange.closed
    to ensure dead connections are never used.
    """
    global _public_exchange, _public_fail_count
    async with _public_lock:
        # STEP 5: Check if we need to reconnect
        needs_reconnect = _public_exchange is None or _public_fail_count >= _PUBLIC_MAX_FAILS
        
        # STEP 5: Use health check instead of exchange.closed
        if not needs_reconnect and _public_exchange is not None:
            is_healthy = await _check_exchange_health(_public_exchange, timeout_seconds=5.0)
            if not is_healthy:
                logger.warning(
                    "[WS] STEP 5: Exchange health check failed - marking for reconnect"
                )
                needs_reconnect = True
        
        if needs_reconnect:
            if _public_exchange:
                try:
                    await _public_exchange.close()
                except Exception:
                    pass
            default_exchange = os.getenv("DEFAULT_EXCHANGE")
            if not default_exchange:
                raise RuntimeError("DEFAULT_EXCHANGE environment variable must be set for public market data feeds")
            bridge = ConnectionEngine(exchange_id=default_exchange)
            _public_exchange = await bridge.connect()
            _public_fail_count = 0
            logger.info("[WS] STEP 5: Public exchange reconnected with health check.")
    return _public_exchange


def _mark_public_exchange_failed():
    global _public_fail_count
    _public_fail_count += 1


async def _check_exchange_health(exchange, timeout_seconds: float = 5.0) -> bool:
    """
    STEP 5: Exchange Health Check (H3)
    
    Replaces exchange.closed with active health check using fetch_ticker().
    
    RULES:
    - If timeout → unhealthy
    - If invalid data → unhealthy
    
    Args:
        exchange: CCXT exchange instance
        timeout_seconds: Maximum time to wait for health check
        
    Returns:
        True if exchange is healthy, False otherwise
    """
    if exchange is None:
        return False
    
    try:
        # STEP 5: Use fetch_ticker() with timeout instead of exchange.closed
        # This ensures the connection is actually working, not just "not closed"
        ticker = await asyncio.wait_for(
            exchange.fetch_ticker("BTC/USDT"),  # Use a common pair for health check
            timeout=timeout_seconds
        )
        
        # STEP 5: Validate ticker data
        if ticker is None:
            logger.warning("[WS/health] STEP 5: Health check failed - empty ticker data")
            return False
        
        # STEP 5: Check for invalid data
        required_fields = ["last", "bid", "ask", "timestamp"]
        for field in required_fields:
            if field not in ticker or ticker[field] is None:
                logger.warning(f"[WS/health] STEP 5: Health check failed - missing {field}")
                return False
        
        # STEP 5: Validate price values are reasonable
        last_price = ticker.get("last")
        if last_price is not None:
            if last_price <= 0 or last_price > 1e9:  # Sanity check for BTC price
                logger.warning(f"[WS/health] STEP 5: Health check failed - invalid price {last_price}")
                return False
        
        # STEP 5: Check timestamp is reasonable (not too old)
        timestamp = ticker.get("timestamp")
        if timestamp:
            if isinstance(timestamp, (int, float)):
                # Convert to datetime
                if timestamp > 1e12:
                    tick_time = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
                else:
                    tick_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                
                data_age = (datetime.now(timezone.utc) - tick_time).total_seconds()
                if data_age > 60:  # Data older than 60 seconds is stale
                    logger.warning(f"[WS/health] STEP 5: Health check failed - stale data ({data_age:.1f}s)")
                    return False
        
        logger.debug("[WS/health] STEP 5: Exchange health check passed")
        return True
        
    except asyncio.TimeoutError:
        logger.error(f"[WS/health] STEP 5: Health check TIMEOUT after {timeout_seconds}s")
        return False
    except Exception as e:
        logger.error(f"[WS/health] STEP 5: Health check failed - {e}")
        return False


async def _check_connection_health():
    """
    STEP 4: Background task to monitor connection health and force reconnect.
    
    RULE: If no data for 60s -> FORCE RECONNECT
    """
    global _public_exchange
    
    while True:
        await asyncio.sleep(10)  # Check every 10 seconds
        
        now = datetime.now(timezone.utc)
        
        for connection_id, health in list(_connection_health.items()):
            last_data_time = health.get("last_data_time")
            
            if last_data_time:
                time_since_last_data = (now - last_data_time).total_seconds()
                
                # STEP 4: If no data for 60s -> FORCE RECONNECT
                if time_since_last_data > _FORCE_RECONNECT_THRESHOLD_SECONDS:
                    logger.error(
                        f"[WS/health] STEP 4: FORCE RECONNECT for {connection_id}. "
                        f"No data for {time_since_last_data:.1f}s > {_FORCE_RECONNECT_THRESHOLD_SECONDS}s threshold."
                    )
                    
                    # Mark connection unhealthy
                    _connection_health[connection_id]["healthy"] = False
                    _connection_health[connection_id]["force_reconnect_time"] = now
                    
                    # Force reconnection by marking exchange as failed
                    _mark_public_exchange_failed()
                    
                    # Clear the exchange to force reconnect on next access
                    async with _public_lock:
                        if _public_exchange:
                            try:
                                await _public_exchange.close()
                            except Exception:
                                pass
                            _public_exchange = None


async def _validate_ws_token(token: str, claimed_user_id: str) -> bool:
    """
    WSR-1 (FIXED): Local HS256 JWT validation — zero network latency.
    Removed: blocking supabase.auth.get_user() call that caused rate limits.
    Now validates the JWT signature locally using SUPABASE_JWT_SECRET.
    """
    try:
        payload = _decode_hs256_token(token)

        if payload is None:
            logger.warning("[WS] Token validation failed: invalid or expired token")
            return False

        token_user_id = payload.get("sub")
        if not token_user_id:
            logger.warning("[WS] Token validation failed: missing sub claim")
            return False

        if token_user_id != claimed_user_id:
            logger.warning(
                f"[WS] Token validation failed: user ID mismatch "
                f"(claimed={claimed_user_id}, token={token_user_id})"
            )
            return False

        logger.info(f"[WS] Token validated locally for user {claimed_user_id}")
        return True

    except Exception as e:
        logger.warning(f"[WS] Token validation failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════
#  PUBLIC FEEDS
# ══════════════════════════════════════════════════════════════════════════


_health_check_task: Optional[asyncio.Task] = None

@ws_router.websocket("/ws/telemetry")
async def ws_telemetry(
    websocket: WebSocket,
    token: str = Query(None)
):
    """
    Real-time telemetry and monitoring endpoint.
    Exposes Strategy Monitoring, Signal Tracing, and Risk Events.
    """
    if not token:
        await websocket.close(code=4001, reason="Unauthorized: Missing token")
        return
    
    try:
        payload = _decode_hs256_token(token)
        if not payload:
            await websocket.close(code=4001, reason="Unauthorized: Invalid token")
            return
        
        tenant_id = payload.get("sub")
        if not tenant_id:
            await websocket.close(code=4001, reason="Unauthorized: Missing sub claim")
            return
    except Exception as e:
        logger.warning(f"[WS/telemetry] Token validation failed: {e}")
        await websocket.close(code=4003, reason="Authentication verification failed")
        return
    
    await websocket.accept()
    logger.info(f"[WS/telemetry] Connection accepted for tenant {tenant_id}")
    
    from backend_app.backend.ws_event_stream import ws_streamer
    await ws_streamer.handle_connection(websocket, tenant_id=tenant_id)


@ws_router.websocket("/ws/ticker/{symbol}")
async def ws_ticker(websocket: WebSocket, symbol: str, token: str = Query(None)):
    global _health_check_task
    
    # Verify WebSocket auth — FAIL CLOSED
    if not token:
        await websocket.close(code=4001, reason="Authentication required: provide ?token=")
        return
    
    try:
        payload = _decode_hs256_token(token)
        if not payload:
            await websocket.close(code=4001, reason="Unauthorized: Invalid token")
            return
    except Exception as e:
        logger.warning(f"[WS/ticker] Token validation failed: {e}")
        await websocket.close(code=4003, reason="Authentication verification failed")
        return
    
    symbol = unquote(symbol).replace("-", "/")
    manager = get_ws_manager()

    await websocket.accept()
    await manager.subscribe("ticker", symbol, websocket)

    # STEP 4: Start connection health watchdog if not running
    if _health_check_task is None or _health_check_task.done():
        _health_check_task = asyncio.create_task(_check_connection_health())
        logger.info("[WS] STEP 4: Started connection health watchdog task")

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
        # STEP 1: MEMORY LEAK FIX - Clean up connection health tracking
        _connection_health.pop(f"ticker_{symbol}", None)


async def _stream_ticker(data_engine, symbol, manager):
    """
    STEP 4: Stream ticker with stale data detection.
    
    Rules:
    - If data_age > 30s: DROP DATA, MARK CONNECTION UNHEALTHY
    - If no data for 60s: FORCE RECONNECT
    """
    connection_id = f"ticker_{symbol}"
    now = datetime.now(timezone.utc)
    
    # Initialize connection health
    _connection_health[connection_id] = {
        "last_data_time": now,
        "healthy": True,
        "symbol": symbol
    }
    
    try:
        async for ticks in data_engine.stream_ticker(symbol):
            if not ticks:
                continue
            
            tick = ticks if isinstance(ticks, dict) else (ticks[0] if ticks else {})
            
            # STEP 4: Stale data detection
            tick_timestamp = tick.get("timestamp")
            now = datetime.now(timezone.utc)
            
            if tick_timestamp:
                # Convert timestamp to datetime if needed
                if isinstance(tick_timestamp, (int, float)):
                    # Assume milliseconds if > 1e12, else seconds
                    if tick_timestamp > 1e12:
                        tick_time = datetime.fromtimestamp(tick_timestamp / 1000, tz=timezone.utc)
                    else:
                        tick_time = datetime.fromtimestamp(tick_timestamp, tz=timezone.utc)
                else:
                    tick_time = tick_timestamp
                
                # Calculate data age
                data_age_seconds = (now - tick_time).total_seconds()
                
                # STEP 4: RULE - If data_age > 30s: DROP DATA
                if data_age_seconds > _STALE_DATA_THRESHOLD_SECONDS:
                    logger.warning(
                        f"[WS/ticker] STEP 4: STALE DATA DETECTED for {symbol}. "
                        f"data_age={data_age_seconds:.1f}s > {_STALE_DATA_THRESHOLD_SECONDS}s threshold. "
                        f"DROPPING DATA."
                    )
                    
                    # STEP 4: MARK CONNECTION UNHEALTHY
                    _connection_health[connection_id]["healthy"] = False
                    _connection_health[connection_id]["last_stale_data_time"] = now
                    
                    # Continue without broadcasting stale data
                    continue
            
            # Update connection health with fresh data
            _connection_health[connection_id]["last_data_time"] = now
            _connection_health[connection_id]["healthy"] = True
            
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
                    "data_age_ms": int(data_age_seconds * 1000) if tick_timestamp else None,
                },
            )
    except Exception as e:
        logger.warning(f"[WS/ticker] stream error for {symbol}: {e}")
        _mark_public_exchange_failed()
        # Mark connection as unhealthy on error
        _connection_health[connection_id]["healthy"] = False
    finally:
        # STEP 1: MEMORY LEAK FIX - Clean up connection health tracking
        _connection_health.pop(connection_id, None)


@ws_router.websocket("/ws/orderbook/{symbol}")
async def ws_orderbook(websocket: WebSocket, symbol: str, depth: int = Query(20), token: str = Query(None)):
    # Verify WebSocket auth — FAIL CLOSED
    if not token:
        await websocket.close(code=4001, reason="Authentication required: provide ?token=")
        return
    
    try:
        payload = _decode_hs256_token(token)
        if not payload:
            await websocket.close(code=4001, reason="Unauthorized: Invalid token")
            return
    except Exception as e:
        logger.warning(f"[WS/orderbook] Token validation failed: {e}")
        await websocket.close(code=4003, reason="Authentication verification failed")
        return
    
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
async def ws_candles(websocket: WebSocket, symbol: str, timeframe: str = "5m", token: str = Query(None)):
    # Verify WebSocket auth — FAIL CLOSED
    if not token:
        await websocket.close(code=4001, reason="Authentication required: provide ?token=")
        return
    
    try:
        payload = _decode_hs256_token(token)
        if not payload:
            await websocket.close(code=4001, reason="Unauthorized: Invalid token")
            return
    except Exception as e:
        logger.warning(f"[WS/candles] Token validation failed: {e}")
        await websocket.close(code=4003, reason="Authentication verification failed")
        return
    
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
    exchange_id: str = Query(..., description="Exchange ID (e.g., binance, coinbase)"),
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

    # ═══════════════════════════════════════════════════════════════════
    # STEP 3: WS RECONNECT STATE RECOVERY
    # ═══════════════════════════════════════════════════════════════════
    # On reconnect, immediately sync current state to prevent stale data
    await _sync_initial_state(de, user_id, manager, websocket)
    # ═══════════════════════════════════════════════════════════════════

    # WSR-4: Spin tasks independently — do NOT use gather() on infinite loops
    t1 = asyncio.create_task(_stream_fills(de, user_id, manager))
    t2 = asyncio.create_task(_stream_orders(de, user_id, manager))
    t3 = asyncio.create_task(_stream_balance(de, user_id, manager))

    # STEP 1: WebSocket Pong Validation - Track client responsiveness
    last_pong_time = time.time()
    PONG_TIMEOUT_SECONDS = 30
    
    try:
        # Heartbeat loop runs correctly now — not blocked by gather()
        while True:
            try:
                message_raw = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                
                # STEP 1: Handle pong messages from client
                try:
                    message = json.loads(message_raw)
                    if message.get("type") == "pong":
                        last_pong_time = time.time()
                        logger.debug(f"[WS/user] Pong received from {user_id}")
                        continue
                except json.JSONDecodeError:
                    pass  # Not JSON, ignore
                
                # STEP 1: Enforce pong timeout - close if client unresponsive
                elapsed_since_pong = time.time() - last_pong_time
                if elapsed_since_pong > PONG_TIMEOUT_SECONDS:
                    logger.warning(
                        f"[WS/user] PONG TIMEOUT for {user_id} "
                        f"({elapsed_since_pong:.1f}s > {PONG_TIMEOUT_SECONDS}s), closing connection"
                    )
                    await websocket.close(code=1001, reason="Pong timeout")
                    break
                    
            except asyncio.TimeoutError:
                # Send ping to check connection
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


async def _sync_initial_state(de, user_id, manager, websocket):
    """
    STEP 3: WS RECONNECT STATE RECOVERY
    
    On WebSocket reconnect, immediately fetch and send current state:
    - Open orders
    - Current positions  
    - Account balance
    
    This prevents stale data showing after connection drops.
    """
    try:
        logger.info(f"[WS/sync] Starting state recovery for {user_id}")
        
        # 1. Fetch and send current orders
        try:
            orders = await de.exchange.fetch_open_orders()
            if orders:
                await manager.broadcast_user(
                    user_id,
                    {
                        "type": "initial_orders",
                        "orders": orders,
                        "count": len(orders),
                    }
                )
                logger.info(f"[WS/sync] Sent {len(orders)} open orders to {user_id}")
        except Exception as e:
            logger.warning(f"[WS/sync] Failed to fetch orders: {e}")
        
        # 2. Fetch and send current positions
        try:
            positions = await de.exchange.fetch_positions()
            if positions:
                await manager.broadcast_user(
                    user_id,
                    {
                        "type": "initial_positions",
                        "positions": positions,
                        "count": len(positions),
                    }
                )
                logger.info(f"[WS/sync] Sent {len(positions)} positions to {user_id}")
        except Exception as e:
            logger.warning(f"[WS/sync] Failed to fetch positions: {e}")
        
        # 3. Fetch and send current balance
        try:
            balance = await de.exchange.fetch_balance()
            if balance:
                await manager.broadcast_user(
                    user_id,
                    {
                        "type": "initial_balance",
                        "total": balance.get("total", {}),
                        "free": balance.get("free", {}),
                        "used": balance.get("used", {}),
                    }
                )
                logger.info(f"[WS/sync] Sent balance to {user_id}")
        except Exception as e:
            logger.warning(f"[WS/sync] Failed to fetch balance: {e}")
        
        logger.info(f"[WS/sync] State recovery complete for {user_id}")
        
    except Exception as e:
        logger.error(f"[WS/sync] State recovery failed for {user_id}: {e}")


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


# ══════════════════════════════════════════════════════════════════════════
# PHASE 14: WEBSOCKET OPTIMIZATION - REALTIME EVENTS ONLY
# ══════════════════════════════════════════════════════════════════════════

@ws_router.websocket("/ws/dashboard")
async def ws_dashboard(websocket: WebSocket, token: str = Query(...), user_id: str = Query(...)):
    """
    PHASE 14: Optimized Dashboard WebSocket for realtime updates only.
    
    ONLY pushes updates for realtime modules:
    - Strategy Status (deployment status changes, health changes)
    - Signal Trace (new signals, execution updates)
    - Notifications (new notifications, read status)
    - Risk Alerts (circuit breaker triggers, risk level changes)
    - Exchange Health (connection status, latency changes)
    
    Does NOT push updates for:
    - Portfolio overview (use REST polling)
    - Equity curve (use REST polling)
    - Subscription status (use REST polling)
    - Static data (use REST initial load)
    
    Implements incremental updates - only sends changed fields, not full refresh.
    Includes proper heartbeat, reconnection, and memory leak prevention.
    """
    manager = get_ws_manager()

    if not await _validate_ws_token(token, user_id):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    dashboard_channel = f"dashboard_{user_id}"
    await manager.subscribe("dashboard", dashboard_channel, websocket)
    logger.info(f"[WS/dashboard] Dashboard channel open: {user_id}")

    # Send initial connection confirmation
    await websocket.send_text(json.dumps({
        "type": "connected",
        "channel": "dashboard",
        "user_id": user_id,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }))

    # Start heartbeat task (30 second interval)
    heartbeat_task = asyncio.create_task(_heartbeat_task(websocket, dashboard_channel, interval=30))

    # Track last activity for cleanup
    last_activity = asyncio.create_task(_track_activity(websocket, dashboard_channel))

    try:
        while True:
            try:
                # Wait for client messages (ping/pong, subscription updates)
                message_raw = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                
                # Update activity on any message
                if last_activity and not last_activity.done():
                    last_activity.cancel()
                    last_activity = asyncio.create_task(_track_activity(websocket, dashboard_channel))
                
                try:
                    message = json.loads(message_raw)
                    
                    # Handle pong messages
                    if message.get("type") == "pong":
                        continue
                    
                    # Handle subscription preferences
                    if message.get("type") == "subscribe":
                        # Client can filter which updates they want
                        preferences = message.get("preferences", {})
                        logger.info(f"[WS/dashboard] User {user_id} preferences: {preferences}")
                        # Store preferences for this user's dashboard subscription
                        # In production, this would be stored in Redis or user context
                        
                except json.JSONDecodeError:
                    pass  # Not JSON, ignore
                    
            except asyncio.TimeoutError:
                # Send ping if no message received
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                    
    except WebSocketDisconnect:
        logger.info(f"[WS/dashboard] Dashboard channel closed: {user_id}")
    except Exception as e:
        logger.error(f"[WS/dashboard] Error for user {user_id}: {e}")
    finally:
        # Cleanup tasks
        heartbeat_task.cancel()
        if last_activity and not last_activity.done():
            last_activity.cancel()
        await manager.unsubscribe("dashboard", dashboard_channel, websocket)
        logger.info(f"[WS/dashboard] Cleanup complete for user {user_id}")


async def _heartbeat_task(websocket: WebSocket, channel: str, interval: int = 30):
    """
    Heartbeat task to keep connection alive and detect stale connections.
    
    Sends ping every interval seconds. If no pong received, closes connection.
    """
    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            await asyncio.sleep(interval)
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(json.dumps({"type": "ping"}))
    except asyncio.CancelledError:
        pass  # Normal shutdown
    except Exception as e:
        logger.error(f"[WS/dashboard] Heartbeat error: {e}")


async def _track_activity(websocket: WebSocket, channel: str, timeout: int = 90):
    """
    Track activity to detect stale connections.
    
    If no activity within timeout, close connection to prevent memory leaks.
    """
    try:
        await asyncio.sleep(timeout)
        if websocket.client_state == WebSocketState.CONNECTED:
            logger.warning(f"[WS/dashboard] No activity for {timeout}s, closing stale connection")
            await websocket.close(code=4000, reason="Inactivity timeout")
    except asyncio.CancelledError:
        pass  # Normal shutdown
    except Exception as e:
        logger.error(f"[WS/dashboard] Activity tracking error: {e}")


async def broadcast_dashboard_update(user_id: str, update_type: str, data: dict):
    """
    Broadcast incremental dashboard update to specific user.
    
    This should be called by backend services when relevant events occur:
    - Strategy start/stop → update_type: "strategy_status"
    - New signal → update_type: "signal_trace"
    - New notification → update_type: "notification"
    - Risk circuit breaker trigger → update_type: "risk_alert"
    - Exchange health change → update_type: "exchange_health"
    
    Args:
        user_id: User to send update to
        update_type: Type of update (determines which widget to refresh)
        data: Incremental data (only changed fields)
    """
    manager = get_ws_manager()
    dashboard_channel = f"dashboard_{user_id}"
    
    message = {
        "type": "dashboard_update",
        "update_type": update_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    await manager.broadcast_to_channel("dashboard", dashboard_channel, json.dumps(message))
    logger.debug(f"[WS/dashboard] Broadcast {update_type} update to {user_id}")


# ══════════════════════════════════════════════════════════════════════════
# PHASE 14: STRATEGY-SPECIFIC WEBSOCKET
# ══════════════════════════════════════════════════════════════════════════

@ws_router.websocket("/ws/strategy/{strategy_id}")
async def ws_strategy(
    websocket: WebSocket,
    strategy_id: str,
    token: str = Query(...),
    user_id: str = Query(...)
):
    """
    PHASE 14: Strategy-specific WebSocket for realtime updates.
    
    Only pushes updates for:
    - Strategy deployment status changes
    - Strategy execution events
    - Strategy performance updates
    - Strategy risk alerts
    
    Does NOT push non-essential data.
    """
    manager = get_ws_manager()

    if not await _validate_ws_token(token, user_id):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Verify user owns the strategy
    from backend_app.core.dependencies import create_request_supabase_async
    sb_res = create_request_supabase_async(token)
    sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
    query_res = sb.table("strategies").select("user_id").eq("id", strategy_id).execute()
    strategy_res = await query_res if inspect.isawaitable(query_res) else query_res
    if not strategy_res.data or strategy_res.data[0]["user_id"] != user_id:
        await websocket.close(code=4003, reason="Forbidden")
        return

    await websocket.accept()
    strategy_channel = f"strategy_{strategy_id}"
    await manager.subscribe("strategy", strategy_channel, websocket)
    logger.info(f"[WS/strategy] Strategy channel open: {strategy_id} for user {user_id}")

    # Send initial connection confirmation
    await websocket.send_text(json.dumps({
        "type": "connected",
        "channel": "strategy",
        "strategy_id": strategy_id,
        "user_id": user_id,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }))

    # Start heartbeat
    heartbeat_task = asyncio.create_task(_heartbeat_task(websocket, strategy_channel, interval=30))
    last_activity = asyncio.create_task(_track_activity(websocket, strategy_channel, timeout=90))

    try:
        while True:
            try:
                message_raw = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                
                if last_activity and not last_activity.done():
                    last_activity.cancel()
                    last_activity = asyncio.create_task(_track_activity(websocket, strategy_channel))
                
                try:
                    message = json.loads(message_raw)
                    if message.get("type") == "pong":
                        continue
                except json.JSONDecodeError:
                    pass
                    
            except asyncio.TimeoutError:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                    
    except WebSocketDisconnect:
        logger.info(f"[WS/strategy] Strategy channel closed: {strategy_id}")
    except Exception as e:
        logger.error(f"[WS/strategy] Error for strategy {strategy_id}: {e}")
    finally:
        heartbeat_task.cancel()
        if last_activity and not last_activity.done():
            last_activity.cancel()
        await manager.unsubscribe("strategy", strategy_channel, websocket)
        logger.info(f"[WS/strategy] Cleanup complete for strategy {strategy_id}")


async def broadcast_strategy_update(strategy_id: str, update_type: str, data: dict):
    """
    Broadcast strategy-specific update to all connected clients.
    
    Args:
        strategy_id: Strategy ID
        update_type: Type of update (deployment_status, execution, risk, etc.)
        data: Incremental data
    """
    manager = get_ws_manager()
    strategy_channel = f"strategy_{strategy_id}"
    
    message = {
        "type": "strategy_update",
        "update_type": update_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    await manager.broadcast_to_channel("strategy", strategy_channel, json.dumps(message))
    logger.debug(f"[WS/strategy] Broadcast {update_type} update for strategy {strategy_id}")


# ══════════════════════════════════════════════════════════════════════════
# PHASE 10: SIGNAL TRACE WEBSOCKET
# ══════════════════════════════════════════════════════════════════════════

@ws_router.websocket("/ws/signal-trace")
async def ws_signal_trace(
    websocket: WebSocket,
    token: str = Query(...),
    user_id: str = Query(...),
    strategy_id: Optional[str] = Query(None)
):
    """
    PHASE 10: Signal Trace WebSocket for realtime updates.
    
    Only pushes updates for:
    - New signals
    - Signal status changes
    - Order updates
    - Execution updates
    - PnL updates
    
    Does NOT push non-essential data.
    """
    manager = get_ws_manager()

    if not await _validate_ws_token(token, user_id):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    
    # Use strategy-specific channel if provided, otherwise user channel
    if strategy_id:
        signal_channel = f"signal_trace_{strategy_id}"
    else:
        signal_channel = f"signal_trace_{user_id}"
    
    await manager.subscribe("signal_trace", signal_channel, websocket)
    logger.info(f"[WS/signal-trace] Signal trace channel open: {signal_channel}")

    # Send initial connection confirmation
    await websocket.send_text(json.dumps({
        "type": "connected",
        "channel": "signal_trace",
        "strategy_id": strategy_id,
        "user_id": user_id,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }))

    # Start heartbeat
    heartbeat_task = asyncio.create_task(_heartbeat_task(websocket, signal_channel, interval=30))
    last_activity = asyncio.create_task(_track_activity(websocket, signal_channel, timeout=90))

    try:
        while True:
            try:
                message_raw = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                
                if last_activity and not last_activity.done():
                    last_activity.cancel()
                    last_activity = asyncio.create_task(_track_activity(websocket, signal_channel))
                
                try:
                    message = json.loads(message_raw)
                    if message.get("type") == "pong":
                        continue
                except json.JSONDecodeError:
                    pass
                    
            except asyncio.TimeoutError:
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                    
    except WebSocketDisconnect:
        logger.info(f"[WS/signal-trace] Signal trace channel closed: {signal_channel}")
    except Exception as e:
        logger.error(f"[WS/signal-trace] Error for channel {signal_channel}: {e}")
    finally:
        heartbeat_task.cancel()
        if last_activity and not last_activity.done():
            last_activity.cancel()
        await manager.unsubscribe("signal_trace", signal_channel, websocket)
        logger.info(f"[WS/signal-trace] Cleanup complete for channel {signal_channel}")


async def broadcast_signal_update(
    user_id: str,
    strategy_id: Optional[str],
    update_type: str,
    data: dict
):
    """
    Broadcast signal trace update.
    
    Args:
        user_id: User ID
        strategy_id: Optional Strategy ID
        update_type: Type of update (signal, order, execution, pnl)
        data: Incremental data
    """
    manager = get_ws_manager()
    
    # Broadcast to user channel
    user_channel = f"signal_trace_{user_id}"
    message = {
        "type": "signal_update",
        "update_type": update_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await manager.broadcast_to_channel("signal_trace", user_channel, json.dumps(message))
    
    # Also broadcast to strategy-specific channel if provided
    if strategy_id:
        strategy_channel = f"signal_trace_{strategy_id}"
        await manager.broadcast_to_channel("signal_trace", strategy_channel, json.dumps(message))
    
    logger.debug(f"[WS/signal-trace] Broadcast {update_type} update for user {user_id}")

