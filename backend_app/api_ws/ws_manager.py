"""
websockets/ws_manager.py — WebSocket Connection Manager.

Tracks every connected WebSocket client, routes messages to the
right subscribers, and handles clean disconnection.

STEP 8: Added rate limiting to prevent IP bans from excessive connections.

Channels:
  ticker       — per-symbol price feeds  (DataEngine.stream_ticker)
  orderbook    — per-symbol L2 depth     (DataEngine.stream_order_book)
  candles      — per-symbol OHLCV        (DataEngine.stream_live_ohlcv)
  user         — per-user private events (orders, fills, strategy status, alerts)
  pnl          — per-user P&L push       (TelemetryEngine.get_live_pnl every 2s)
  marketplace  — marketplace events (new strategies, ratings, subscriptions)
  dashboard    — per-user dashboard realtime updates (PHASE 14)
  strategy     — per-strategy realtime updates (PHASE 14)
  signal_trace — per-user/strategy signal trace realtime updates (PHASE 10)
  paper        — per-Paper_Session simulated-trading events (task 26.4)

TASK 26.4 (marketplace-subscriptions-paper-trading) ADDED THREE THINGS AND REBUILT NOTHING
------------------------------------------------------------------------------------------
Requirements 19.10, 19.11 and 19.12 are answered by extending what is already here rather than
by a second manager:

1. ``"paper"`` joins :meth:`ConnectionManager._get_store`'s mapping, so ``paper.{session_id}``
   subscriptions live in the same registry every other channel uses and are swept by the same
   :meth:`unsubscribe` / :meth:`disconnect` that already sweep ``_all_connections``,
   ``_user_connections`` and the per-channel stores (Requirement 19.10's "remove a closed
   connection's subscriptions from every registry it was added to").
2. :meth:`_stores` replaces the two hard-coded six-store lists that :meth:`disconnect` and
   :meth:`broadcast` swept. Those lists omitted ``_dashboard``, ``_strategy``,
   ``_signal_trace`` and ``_admin``, so a disconnect left rows behind in four registries. This
   only ever removes MORE, never less.
3. The per-connection PENDING-QUEUE DEPTH counter of Requirement 19.11
   (:data:`MAX_PENDING_EVENTS_PER_CONNECTION`, :meth:`note_pending`, :meth:`note_delivered`,
   :meth:`pending_depth`, :meth:`has_fallen_behind`, :meth:`forget_connection`). The counter is
   here rather than in the paper package because it is a property of a CONNECTION, not of an
   event vocabulary, and because every channel gets it for free once it lives with the registry.

The HEARTBEAT is deliberately untouched. It lives in ``backend_app/api_ws/ws_routes.py``
(``_heartbeat_task``, ``_HEARTBEAT_INTERVAL_SECONDS = 10``, ``_HEARTBEAT_TIMEOUT_SECONDS = 30``)
and closes a socket that has stopped answering; task 26.4 uses it as it stands.
"""

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException, WebSocket

from backend_app.core.cache import redis_manager

logger = logging.getLogger("WSManager")

# STEP 8: Rate limiting constants to prevent IP bans
MAX_STREAMS_PER_USER = 20  # Maximum streams a single user can open
MAX_GLOBAL_STREAMS = 1000    # Maximum total streams across all users

#: Requirement 19.11's ceiling: the number of events that may be OUTSTANDING for one connection
#: before it is classified as having fallen behind.
#:
#: "Exceeds 1000" is read strictly - a depth of exactly 1000 is still served, and 1001 is not
#: (:meth:`ConnectionManager.has_fallen_behind`). The figure is a count of events rather than of
#: bytes because that is the unit the requirement states and the unit a client resumes on: a
#: consumer told it fell behind reconnects with a ``last_sequence``, and sequences are counted.
MAX_PENDING_EVENTS_PER_CONNECTION = 1000


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
        self._marketplace: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._dashboard: Dict[str, Set[WebSocket]] = defaultdict(set)  # PHASE 14: Dashboard channel
        self._strategy: Dict[str, Set[WebSocket]] = defaultdict(set)  # PHASE 14: Strategy channel
        self._signal_trace: Dict[str, Set[WebSocket]] = defaultdict(set)  # PHASE 10: Signal Trace channel
        self._admin: Dict[str, Set[WebSocket]] = defaultdict(set)  # Admin & Staff channel
        # Task 26.4: per-Paper_Session subscriptions, keyed by session id. The channel NAME
        # ("paper.{session_id}") is owned by ws_channels.PAPER_FAMILY; this store holds the key.
        self._paper: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
        
        # STEP 8: Connection tracking for rate limiting
        # Track connections per user (user_id -> set of websockets)
        self._user_connections: Dict[str, Set[WebSocket]] = defaultdict(set)
        # Track all connections globally
        self._all_connections: Set[WebSocket] = set()

        # Task 26.4 / Requirement 19.11: how many events have been handed to each connection and
        # not yet acknowledged as sent. A plain dict keyed on the connection object (identity
        # hashing, which is what every other registry here relies on) and erased by
        # :meth:`forget_connection`, so it cannot outlive the socket it counts for.
        self._pending: Dict[Any, int] = {}
        
        self._listener_task: Optional[asyncio.Task] = None

    # ── Redis Bridge ───────────────────────────────────────────────────────

    def start_bridge(self):
        """Start the Redis Pub/Sub listener for cross-instance broadcasts."""
        if not self._listener_task:
            self._listener_task = asyncio.create_task(self._redis_listener_loop())

    async def _redis_listener_loop(self):
        """Listen for messages on the Redis ws_bridge channel and route them locally."""
        while True:
            if not redis_manager.pool:
                await asyncio.sleep(1)
                continue
            
            try:
                pubsub = redis_manager.pool.pubsub()
                await pubsub.subscribe("ws_bridge")
                logger.info("[WS] Subscribed to Redis ws_bridge for cross-instance broadcasts")
                
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        try:
                            payload = json.loads(message["data"])
                            channel = payload.get("channel")
                            key = payload.get("key")
                            data = payload.get("data")
                            
                            if channel and key and data is not None:
                                store = self._get_store(channel)
                                await self._broadcast(store, key, data)
                        except Exception as e:
                            logger.error(f"[WS] Error processing cross-instance message: {e}")
            except Exception as e:
                logger.warning(f"[WS] Redis Pub/Sub listener disconnected: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    # ── Register / unregister ──────────────────────────────────────────────

    async def subscribe(
        self, channel: str, key: str, ws: WebSocket, user_id: Optional[str] = None
    ):
        """
        Add a WebSocket to a channel.
        
        STEP 8: Enforces rate limiting:
        - Max streams per user: MAX_STREAMS_PER_USER
        - Max global streams: MAX_GLOBAL_STREAMS

        ``user_id`` (task 26.4) is the AUTHENTICATED identity of the subscription, passed by
        callers whose channel key is not itself a user id. ``paper.{session_id}`` is the case that
        needs it: the key is a Paper_Session id, so :meth:`_extract_user_id` can read nothing from
        it, and without this the connection would never enter ``_user_connections`` - which is one
        of the registries Requirement 19.10 requires a closed connection to be removed from, and
        the registry the per-user stream limit is counted in.

        It is NEVER a client-supplied value: ``paper_channel.subscribe`` takes it from the
        authenticated user mapping (Requirement 21.1).
        """
        # STEP 8: Extract user_id from channel key for user-specific limits
        if user_id is None:
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

    async def unsubscribe(
        self, channel: str, key: str, ws: WebSocket, user_id: Optional[str] = None
    ):
        """Remove a WebSocket from a channel (on disconnect).

        ``user_id`` (task 26.4) mirrors :meth:`subscribe`: a subscription that entered
        ``_user_connections`` under an explicitly passed identity has to leave it under the same
        one, or Requirement 19.10's "every registry it was added to" would be false for exactly
        the channels that needed the parameter.

        The pending-event counter is erased here too, because a connection that has left every
        registry cannot have anything outstanding for it.
        """
        store = self._get_store(channel)
        async with self._lock:
            store[key].discard(ws)
            if not store[key]:
                del store[key]
            
            # STEP 8: Clean up connection tracking
            self._all_connections.discard(ws)
            if user_id is None:
                user_id = self._extract_user_id(channel, key)
            if user_id and user_id in self._user_connections:
                self._user_connections[user_id].discard(ws)
                if not self._user_connections[user_id]:
                    del self._user_connections[user_id]
            self._pending.pop(ws, None)
        
        logger.info(f"[WS] -sub {channel}/{key}")

    def _extract_user_id(self, channel: str, key: str) -> Optional[str]:
        """
        STEP 8: Extract user_id from channel/key for rate limiting.
        
        Returns:
            user_id if applicable, None otherwise
        """
        # User, pnl, dashboard, and strategy channels have user_id in the key
        if channel in ("user", "pnl", "dashboard", "strategy"):
            # For dashboard channel, key is "dashboard_{user_id}"
            if channel == "dashboard":
                return key.replace("dashboard_", "")
            # For strategy channel, key is "strategy_{strategy_id}" - we can't extract user_id from this
            # Strategy-specific rate limiting would need to be done differently
            if channel == "strategy":
                return None
            return key
        # For other channels, we can't extract user_id from key alone
        # The WebSocket object itself would need to be tracked separately
        # For now, return None for non-user channels
        return None

    # ── Broadcast helpers ──────────────────────────────────────────────────

    async def _publish_to_bridge(self, channel: str, key: str, data: dict):
        """Publish a message to Redis if available, else fallback to local broadcast."""
        if redis_manager.pool:
            try:
                payload = json.dumps({
                    "channel": channel,
                    "key": key,
                    "data": data
                })
                await redis_manager.pool.publish("ws_bridge", payload)
                return
            except Exception as e:
                logger.warning(f"[WS] Failed to publish to Redis ws_bridge, falling back to local: {e}")
        
        # Fallback to local
        store = self._get_store(channel)
        await self._broadcast(store, key, data)

    async def broadcast_ticker(self, symbol: str, data: dict):
        await self._publish_to_bridge("ticker", symbol, data)

    async def broadcast_orderbook(self, symbol: str, data: dict):
        await self._publish_to_bridge("orderbook", symbol, data)

    async def broadcast_candles(self, symbol: str, data: dict):
        await self._publish_to_bridge("candles", symbol, data)

    async def broadcast_user(self, user_id: str, data: dict):
        """Push to a specific user's private channel (fills, strategy status, alerts)."""
        await self._publish_to_bridge("user", user_id, data)

    async def broadcast_pnl(self, user_id: str, data: dict):
        await self._publish_to_bridge("pnl", user_id, data)

    async def broadcast_admin(self, data: dict):
        """Broadcast administrative & support events to staff and admin subscribers."""
        await self._publish_to_bridge("admin", "support", data)

    async def broadcast_marketplace(self, event_type: str, data: dict):
        """Broadcast marketplace events (new strategies, ratings, subscriptions)."""
        await self._publish_to_bridge("marketplace", event_type, data)

    async def broadcast_to_channel(self, channel: str, key: str, data):
        """
        PHASE 7: Broadcast to a specific channel key.
        
        Used for dashboard incremental updates where we want to send
        to a specific user's dashboard channel only.
        """
        store = self._get_store(channel)
        if isinstance(data, str):
            await self._broadcast(store, key, json.loads(data))
        else:
            await self._broadcast(store, key, data)

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
                for corpse in dead:
                    self._pending.pop(corpse, None)

    async def send_to_socket(self, ws: WebSocket, data: dict):
        """Direct send to a single WebSocket (for request-reply patterns)."""
        try:
            await ws.send_text(json.dumps(data))
        except Exception as e:
            logger.warning(f"[WS] Direct send failed: {e}")

    async def send_to_user_ws(self, ws: WebSocket, data: dict):
        """Direct send without going through a named channel (for initial payloads)."""
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
            "marketplace": self._marketplace,
            "dashboard": self._dashboard,  # PHASE 14: Dashboard channel
            "strategy": self._strategy,    # PHASE 14: Strategy channel
            "signal_trace": self._signal_trace,  # PHASE 10: Signal Trace channel
            "admin": self._admin,          # Admin & Support channel
            "paper": self._paper,          # Task 26.4: paper.{session_id}
        }
        if channel not in mapping:
            raise ValueError(f"Unknown channel: {channel}")
        return mapping[channel]

    def _stores(self) -> List[Dict[str, Set[WebSocket]]]:
        """Every per-channel store, so a sweep cannot miss one.

        :meth:`disconnect` and :meth:`broadcast` used to sweep a hard-coded list of six stores
        that omitted ``_dashboard``, ``_strategy``, ``_signal_trace`` and ``_admin`` - so a
        disconnected socket stayed registered in four of them. Requirement 19.10 requires removal
        from **every** registry a connection was added to, and a list written out at two call
        sites is a list that gets a new store added to it at one.
        """
        return [
            self._ticker,
            self._orderbook,
            self._candles,
            self._user,
            self._pnl,
            self._marketplace,
            self._dashboard,
            self._strategy,
            self._signal_trace,
            self._admin,
            self._paper,
        ]

    # ── Pending-queue depth (Requirement 19.11) ───────────────────────────

    def pending_depth(self, ws: Any) -> int:
        """How many events are outstanding for ``ws`` right now. ``0`` for an unknown socket."""
        return self._pending.get(ws, 0)

    def note_pending(self, ws: Any, count: int = 1) -> int:
        """Record ``count`` more events handed to ``ws``, and return the new depth.

        Called BEFORE the send is attempted, which is the only order in which the counter can
        answer Requirement 19.11's question: a depth measured after the await has already been
        drained by it, and a consumer that never drains would be measured at zero forever.
        """
        depth = self._pending.get(ws, 0) + int(count)
        self._pending[ws] = depth
        return depth

    def note_delivered(self, ws: Any, count: int = 1) -> int:
        """Record ``count`` events as sent, and return the new depth. Never below zero."""
        depth = self._pending.get(ws, 0) - int(count)
        if depth <= 0:
            self._pending.pop(ws, None)
            return 0
        self._pending[ws] = depth
        return depth

    def has_fallen_behind(self, ws: Any) -> bool:
        """Whether ``ws`` has MORE than :data:`MAX_PENDING_EVENTS_PER_CONNECTION` outstanding.

        Strictly more: Requirement 19.11 says "exceeds 1000", so a connection sitting at exactly
        the ceiling is still served. A boundary read the other way would close a consumer that is
        keeping up with the limit it was given.
        """
        return self.pending_depth(ws) > MAX_PENDING_EVENTS_PER_CONNECTION

    def forget_connection(self, ws: Any) -> None:
        """Erase the pending count for ``ws``. Idempotent."""
        self._pending.pop(ws, None)

    def stats(self) -> dict:
        return {
            "ticker_symbols": len(self._ticker),
            "orderbook_symbols": len(self._orderbook),
            "candle_symbols": len(self._candles),
            "user_connections": sum(len(v) for v in self._user.values()),
            "pnl_connections": sum(len(v) for v in self._pnl.values()),
            "marketplace_connections": sum(len(v) for v in self._marketplace.values()),
            "dashboard_connections": sum(len(v) for v in self._dashboard.values()),
            "strategy_connections": sum(len(v) for v in self._strategy.values()),
            "paper_sessions": len(self._paper),
            "paper_connections": sum(len(v) for v in self._paper.values()),
        }

    # ── Validation Suite Compatibility ────────────────────────────────────

    async def connect(self, ws: WebSocket):
        """Accept and track a new WebSocket connection."""
        async with self._lock:
            self._all_connections.add(ws)
        logger.info(f"[WS] Connected websocket {ws}")

    async def disconnect(self, ws: WebSocket):
        """Remove a WebSocket from all channels and connection tracking.

        Requirement 19.10's "remove a closed connection's subscriptions from every registry it was
        added to" - every registry being :meth:`_stores`, ``_all_connections``,
        ``_user_connections`` and the pending-event counter.
        """
        async with self._lock:
            self._all_connections.discard(ws)
            # Remove from user connections
            for user_id in list(self._user_connections.keys()):
                self._user_connections[user_id].discard(ws)
                if not self._user_connections[user_id]:
                    del self._user_connections[user_id]
            # Remove from all other channels
            for store in self._stores():
                for key in list(store.keys()):
                    store[key].discard(ws)
                    if not store[key]:
                        del store[key]
            self._pending.pop(ws, None)
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
                for store in self._stores():
                    for key in list(store.keys()):
                        store[key] -= dead
                        if not store[key]:
                            del store[key]
                for corpse in dead:
                    self._pending.pop(corpse, None)

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


# ── Backward-compatible singleton export ────────────────────────────────────
# This maintains compatibility with code that imports 'manager' directly
manager = ConnectionManager()

