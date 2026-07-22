"""
websockets/ws_manager.py — WebSocket Connection Manager.

Tracks every connected WebSocket client, routes messages to the
right subscribers, and handles clean disconnection.

STEP 8: Added rate limiting to prevent IP bans from excessive connections.

Channels:
  ticker    — per-symbol price feeds  (DataEngine.stream_ticker)
  orderbook — per-symbol L2 depth     (DataEngine.stream_order_book)
  candles   — per-symbol OHLCV        (DataEngine.stream_live_ohlcv)
  user      — per-user private events (orders, fills, bot status, alerts)
  pnl       — per-user P&L push       (TelemetryEngine.get_live_pnl every 2s)
"""

import asyncio
import json
import logging
from collections import defaultdict
from typing import Dict, Optional, Set

from fastapi import HTTPException, WebSocket

logger = logging.getLogger("WSManager")

# STEP 8: Rate limiting constants to prevent IP bans
MAX_STREAMS_PER_USER = 20  # Maximum streams a single user can open
MAX_GLOBAL_STREAMS = 1000    # Maximum total streams across all users


class ConnectionManager:
    """
    Thread-safe (asyncio-safe) registry of all live WebSocket connections.

    Storage layout:
      _ticker[symbol]   = {ws1, ws2, ...}   — all clients watching this symbol
      _orderbook[symbol] = {ws1, ws2, ...}
      _candles[symbol]   = {ws1, ws2, ...}
      _user[user_id]     = {ws1, ...}        — private per-user channel
      _pnl[user_id]      = {ws1, ...}        — P&L push channel
    """

    def __init__(self):
        self._ticker: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._orderbook: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._candles: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._user: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._pnl: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
        
        # STEP 8: Connection tracking for rate limiting
        # Track connections per user (user_id -> set of websockets)
        self._user_connections: Dict[str, Set[WebSocket]] = defaultdict(set)
        # Track all connections globally
        self._all_connections: Set[WebSocket] = set()

    # ── Register / unregister ──────────────────────────────────────────────

    async def subscribe(self, channel: str, key: str, ws: WebSocket):
        """
        Add a WebSocket to a channel.
        
        STEP 8: Enforces rate limiting:
        - Max streams per user: MAX_STREAMS_PER_USER
        - Max global streams: MAX_GLOBAL_STREAMS
        """
        # STEP 8: Extract user_id from channel key for user-specific limits
        user_id = self._extract_user_id(channel, key)
        
        async with self._lock:
            # STEP 8: Check global limit
            if len(self._all_connections) >= MAX_GLOBAL_STREAMS:
                logger.warning(
                    f"[WS] STEP 8: Global stream limit exceeded "
                    f"({len(self._all_connections)}/{MAX_GLOBAL_STREAMS}). Rejecting connection."
                )
                raise HTTPException(
                    status_code=503,
                    detail=f"Global connection limit reached ({MAX_GLOBAL_STREAMS}). Try again later."
                )
            
            # STEP 8: Check per-user limit
            if user_id:
                user_conn_count = len(self._user_connections.get(user_id, set()))
                if user_conn_count >= MAX_STREAMS_PER_USER:
                    logger.warning(
                        f"[WS] STEP 8: User {user_id} stream limit exceeded "
                        f"({user_conn_count}/{MAX_STREAMS_PER_USER}). Rejecting connection."
                    )
                    raise HTTPException(
                        status_code=429,
                        detail=f"Per-user connection limit reached ({MAX_STREAMS_PER_USER}). Close some streams."
                    )
            
            # Add to channel store
            store = self._get_store(channel)
            store[key].add(ws)
            
            # STEP 8: Track connection
            self._all_connections.add(ws)
            if user_id:
                self._user_connections[user_id].add(ws)
        
        logger.info(
            f"[WS] +sub {channel}/{key} — {len(store[key])} total "
            f"(user: {len(self._user_connections.get(user_id, set())) if user_id else 'N/A'}/"
            f"{MAX_STREAMS_PER_USER}, global: {len(self._all_connections)}/{MAX_GLOBAL_STREAMS})"
        )

    async def unsubscribe(self, channel: str, key: str, ws: WebSocket):
        """Remove a WebSocket from a channel (on disconnect)."""
        store = self._get_store(channel)
        async with self._lock:
            store[key].discard(ws)
            if not store[key]:
                del store[key]
            
            # STEP 8: Clean up connection tracking
            self._all_connections.discard(ws)
            user_id = self._extract_user_id(channel, key)
            if user_id and user_id in self._user_connections:
                self._user_connections[user_id].discard(ws)
                if not self._user_connections[user_id]:
                    del self._user_connections[user_id]
        
        logger.info(f"[WS] -sub {channel}/{key}")

    def _extract_user_id(self, channel: str, key: str) -> Optional[str]:
        """
        STEP 8: Extract user_id from channel/key for rate limiting.
        
        Returns:
            user_id if applicable, None otherwise
        """
        # User and pnl channels have user_id as the key
        if channel in ("user", "pnl"):
            return key
        # For other channels, we can't extract user_id from key alone
        # The WebSocket object itself would need to be tracked separately
        # For now, return None for non-user channels
        return None

    # ── Broadcast helpers ──────────────────────────────────────────────────

    async def broadcast_ticker(self, symbol: str, data: dict):
        await self._broadcast(self._ticker, symbol, data)

    async def broadcast_orderbook(self, symbol: str, data: dict):
        await self._broadcast(self._orderbook, symbol, data)

    async def broadcast_candles(self, symbol: str, data: dict):
        await self._broadcast(self._candles, symbol, data)

    async def broadcast_user(self, user_id: str, data: dict):
        """Push to a specific user's private channel (fills, bot status, alerts)."""
        await self._broadcast(self._user, user_id, data)

    async def broadcast_pnl(self, user_id: str, data: dict):
        await self._broadcast(self._pnl, user_id, data)

    # ── Core send ──────────────────────────────────────────────────────────

    async def _broadcast(self, store: Dict, key: str, data: dict):
        """
        Send JSON to all subscribers of store[key].
        Dead connections are removed automatically.
        
        STEP 8: Also cleans up dead connections from tracking sets.
        """
        dead = set()
        payload = json.dumps(data)

        for ws in list(store.get(key, [])):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)

        if dead:
            async with self._lock:
                store[key] -= dead
                if not store[key]:
                    store.pop(key, None)
                
                # STEP 8: Clean up dead connections from tracking sets
                self._all_connections -= dead
                for user_id in list(self._user_connections.keys()):
                    self._user_connections[user_id] -= dead
                    if not self._user_connections[user_id]:
                        del self._user_connections[user_id]

    async def send_to_socket(self, ws: WebSocket, data: dict):
        """Direct send to a single WebSocket (for request-reply patterns)."""
        try:
            await ws.send_text(json.dumps(data))
        except Exception as e:
            logger.warning(f"[WS] Direct send failed: {e}")

    # ── Utilities ──────────────────────────────────────────────────────────

    def _get_store(self, channel: str) -> Dict:
        mapping = {
            "ticker": self._ticker,
            "orderbook": self._orderbook,
            "candles": self._candles,
            "user": self._user,
            "pnl": self._pnl,
        }
        if channel not in mapping:
            raise ValueError(f"Unknown channel: {channel}")
        return mapping[channel]

    def stats(self) -> dict:
        return {
            "ticker_symbols": len(self._ticker),
            "orderbook_symbols": len(self._orderbook),
            "candle_symbols": len(self._candles),
            "user_connections": sum(len(v) for v in self._user.values()),
            "pnl_connections": sum(len(v) for v in self._pnl.values()),
        }

    # ── Validation Suite Compatibility ────────────────────────────────────

    async def connect(self, ws: WebSocket):
        """Accept and track a new WebSocket connection."""
        async with self._lock:
            self._all_connections.add(ws)
        logger.info(f"[WS] Connected websocket {ws}")

    async def disconnect(self, ws: WebSocket):
        """Remove a WebSocket from all channels and connection tracking."""
        async with self._lock:
            self._all_connections.discard(ws)
            # Remove from user connections
            for user_id in list(self._user_connections.keys()):
                self._user_connections[user_id].discard(ws)
                if not self._user_connections[user_id]:
                    del self._user_connections[user_id]
            # Remove from all other channels
            for store in [self._ticker, self._orderbook, self._candles, self._user, self._pnl]:
                for key in list(store.keys()):
                    store[key].discard(ws)
                    if not store[key]:
                        del store[key]
        logger.info(f"[WS] Disconnected websocket {ws}")

    async def broadcast(self, message: str):
        """Broadcast a text message to all active connections."""
        dead = set()
        for ws in list(self._all_connections):
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        if dead:
            async with self._lock:
                self._all_connections -= dead
                for user_id in list(self._user_connections.keys()):
                    self._user_connections[user_id] -= dead
                    if not self._user_connections[user_id]:
                        del self._user_connections[user_id]
                for store in [self._ticker, self._orderbook, self._candles, self._user, self._pnl]:
                    for key in list(store.keys()):
                        store[key] -= dead
                        if not store[key]:
                            del store[key]

    async def send_personal_message(self, message: str, websocket: WebSocket):
        """Send a message to a specific websocket connection."""
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.warning(f"[WS] send_personal_message failed: {e}")

    @property
    def active_connections(self):
        """Return the active connections list."""
        return list(self._all_connections)

