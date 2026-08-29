"""
WebSocket Manager

STEP 5.5 — REAL-TIME WEBSOCKET PUSH

Pushes real-time updates to frontend clients.

WebSocket Channels:
┌─────────────────────────────────────────────────────────────────┐
│  Channel: orders                                                  │
│  Events: ORDER_FILLED, ORDER_PARTIAL, ORDER_CANCELLED           │
│  Payload: {order_id, status, filled, price, timestamp}          │
├─────────────────────────────────────────────────────────────────┤
│  Channel: positions                                               │
│  Events: POSITION_UPDATED, POSITION_LIQUIDATED                    │
│  Payload: {position_id, symbol, size, pnl, timestamp}           │
├─────────────────────────────────────────────────────────────────┤
│  Channel: pnl                                                     │
│  Events: PnL_UPDATED                                               │
│  Payload: {total_pnl, unrealized, realized, timestamp}        │
├─────────────────────────────────────────────────────────────────┤
│  Channel: portfolio                                               │
│  Events: PORTFOLIO_UPDATED                                         │
│  Payload: {equity, exposure, positions, timestamp}              │
└─────────────────────────────────────────────────────────────────┘

Features:
- Tenant isolation (clients only receive their data)
- Channel subscription
- Automatic reconnection
- Heartbeat/ping
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Set, Tuple

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WebSocketConnection:
    """Represents a single WebSocket connection."""
    
    def __init__(
        self,
        websocket: WebSocket,
        tenant_id: str,
        client_id: str,
        user_id: Optional[str] = None,
    ):
        self.websocket = websocket
        self.tenant_id = tenant_id
        self.client_id = client_id
        # strategy-builder task 6.7: the authenticated user behind this socket, kept
        # separately from ``tenant_id`` because they are not the same thing — a tenant
        # can hold several users, and `training.{job_id}` is authorised against
        # ``job.user_id``, not against the tenant. Defaults to ``tenant_id`` so every
        # existing caller keeps its current behaviour.
        self.user_id = str(user_id) if user_id else str(tenant_id)
        self.subscribed_channels: Set[str] = set()
        self.connected_at = datetime.utcnow()
        self.last_ping = datetime.utcnow()
        self._alive = True
    
    async def send(self, message: Dict[str, Any]):
        """Send message to client."""
        try:
            await self.websocket.send_json(message)
        except Exception as e:
            logger.error(f"Send error to {self.client_id}: {e}")
            self._alive = False
    
    def subscribe(self, channel: str):
        """Subscribe to a channel."""
        self.subscribed_channels.add(channel)
        logger.info(f"Client {self.client_id} subscribed to {channel}")
    
    def unsubscribe(self, channel: str):
        """Unsubscribe from a channel."""
        self.subscribed_channels.discard(channel)
        logger.info(f"Client {self.client_id} unsubscribed from {channel}")
    
    def is_subscribed(self, channel: str) -> bool:
        """Check if subscribed to channel."""
        return channel in self.subscribed_channels
    
    @property
    def is_alive(self) -> bool:
        """Check if connection is alive."""
        return self._alive


class WebSocketManager:
    """
    STEP 5.5: WebSocket connection manager.
    
    Manages all WebSocket connections and broadcasts events.
    """
    
    def __init__(self):
        # tenant_id -> {client_id: WebSocketConnection}
        self._connections: Dict[str, Dict[str, WebSocketConnection]] = {}
        
        # Channel -> tenant_id -> {client_ids}
        self._channel_subscribers: Dict[str, Dict[str, Set[str]]] = {
            "orders": {},
            "positions": {},
            "pnl": {},
            "portfolio": {},
            "all": {}  # Special channel for all updates
        }
        
        # strategy-builder tasks 6.7 and 8.5: authorised subscriptions to the
        # owner-scoped parameterised channels — `training.{job_id}`,
        # `builder.validation.{strategy_id}`, `strategy.{strategy_id}`,
        # `deployment.{deployment_id}` and `execution.{deployment_id}`.
        #
        # channel name -> {(tenant_id, client_id)}
        #
        # A SEPARATE registry from ``_channel_subscribers`` on purpose. That map is
        # broadcast through ``broadcast_to_tenant``, which also delivers to every
        # subscriber of the special ``"all"`` channel in the same tenant. A tenant is
        # not a user, so routing per-resource frames through it would hand one tenant
        # member another member's training progress, deployment state or fills. This
        # registry is populated only by ``subscribe_owned``, which the endpoint calls
        # only after ``authorize_channel_subscription`` resolved the resource's owner,
        # and it is read only by ``broadcast_owned_event``.
        #
        # Task 8.5 keyed it by the FULL channel name rather than by a bare resource id.
        # Task 6.7 keyed it by ``job_id``, which worked while there was one family, and
        # would collide the moment there were five: a strategy and a deployment can
        # share an identifier, and ``deployment.{id}`` and ``execution.{id}`` share one
        # by design.
        self._owned_subscribers: Dict[str, Set[Tuple[str, str]]] = {}
        
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        
        logger.info("WebSocketManager initialized")
    
    async def connect(
        self,
        websocket_or_connection,
        tenant_id: str,
        client_id: str
    ) -> WebSocketConnection:
        """Accept new WebSocket connection or register existing connection."""
        from fastapi import WebSocket
        
        if isinstance(websocket_or_connection, WebSocket):
            # Legacy behavior: accept WebSocket and create connection
            await websocket_or_connection.accept()
            connection = WebSocketConnection(websocket_or_connection, tenant_id, client_id)
        else:
            # New behavior: connection already created and accepted
            connection = websocket_or_connection
        
        # Store connection
        if tenant_id not in self._connections:
            self._connections[tenant_id] = {}
        self._connections[tenant_id][client_id] = connection
        
        logger.info(f"WebSocket connected: {client_id} (tenant={tenant_id})")
        
        return connection
    
    def disconnect(self, tenant_id: str, client_id: str):
        """Remove WebSocket connection."""
        if tenant_id in self._connections:
            self._connections[tenant_id].pop(client_id, None)
            
            # Clean up empty tenant
            if not self._connections[tenant_id]:
                del self._connections[tenant_id]
        
        # Remove from all channel subscriptions
        for channel, tenant_subs in self._channel_subscribers.items():
            if tenant_id in tenant_subs:
                tenant_subs[tenant_id].discard(client_id)
        
        # strategy-builder tasks 6.7 / 8.5: and from every authorised owner-scoped
        # channel, so a reconnecting client cannot inherit a subscription it did not
        # re-earn. This is what makes Requirement 23.5's resubscribe honest: the new
        # connection is authorised again from scratch.
        for channel in list(self._owned_subscribers.keys()):
            self._owned_subscribers[channel].discard((tenant_id, client_id))
            if not self._owned_subscribers[channel]:
                del self._owned_subscribers[channel]
                self._release_signal_sequence(channel)
        
        logger.info(f"WebSocket disconnected: {client_id}")
    
    def subscribe(self, tenant_id: str, client_id: str, channel: str):
        """Subscribe client to channel."""
        # Get connection
        connection = self._get_connection(tenant_id, client_id)
        if not connection:
            return
        
        # Subscribe
        connection.subscribe(channel)
        
        # Register in channel subscribers
        if channel not in self._channel_subscribers:
            self._channel_subscribers[channel] = {}
        
        if tenant_id not in self._channel_subscribers[channel]:
            self._channel_subscribers[channel][tenant_id] = set()
        
        self._channel_subscribers[channel][tenant_id].add(client_id)
    
    def unsubscribe(self, tenant_id: str, client_id: str, channel: str):
        """Unsubscribe client from channel."""
        # Get connection
        connection = self._get_connection(tenant_id, client_id)
        if connection:
            connection.unsubscribe(channel)
        
        # Remove from channel subscribers
        if channel in self._channel_subscribers:
            if tenant_id in self._channel_subscribers[channel]:
                self._channel_subscribers[channel][tenant_id].discard(client_id)
    
    # ── the owner-scoped parameterised channels (tasks 6.7, 8.5) ──────────
    #
    # Requirements 15.11 (training status and per-epoch progress are pushed), 20.12
    # (node runtime state reaches the canvas), 21.5 and 21.6 (every subscription
    # authorised against the resource owner, a refusal reported), 23.1 (all of it
    # multiplexed over the one existing connection).
    #
    # These methods are transport only. They do NOT decide who may subscribe;
    # ``core/websocket_auth.authorize_channel_subscription`` does, and
    # ``subscribe_owned`` is reachable only after it said yes.

    def subscribe_owned(self, tenant_id: str, client_id: str, channel: str) -> bool:
        """Register an ALREADY-AUTHORISED subscription to an owner-scoped ``channel``.

        The caller must have obtained an allow from
        ``authorize_channel_subscription`` first. This is deliberately not the place
        that checks: a transport method that both authorises and registers would be
        one refactor away from someone calling the registration half directly.

        Returns False when the connection is not known to this manager, or when
        ``channel`` is not a well-formed owner-scoped channel name — a name no family
        routes has no owner, so it must not enter a registry whose whole premise is
        that everything in it was authorised against one.
        """
        from backend_app.backend.ws_channels import (
            SIGNAL_FAMILY,
            parse_owned_channel,
            seed_signal_sequence,
        )

        reference = parse_owned_channel(channel)
        if reference is None:
            logger.warning(
                "Refusing to register %r as an owned subscription: no channel family "
                "routes that name.",
                channel,
            )
            return False

        connection = self._get_connection(tenant_id, client_id)
        if not connection:
            return False

        connection.subscribe(channel)
        subscribers = self._owned_subscribers.setdefault(channel, set())
        first = not subscribers
        subscribers.add((tenant_id, client_id))

        # trading-lifecycle-integration task 14.2: "seeded from the deployment's own row
        # on subscribe". Reachable only after ``authorize_channel_subscription`` read
        # that deployment's row.
        #
        # ``restart`` is passed for the FIRST subscriber and only then. That page's
        # ``expectedSequence`` is 1, and a producer publishing to a channel nobody was
        # watching has already advanced the counter past it - frames that reached
        # nobody. For a LATER subscriber the same restart would hand the first page a
        # ``seq`` it has already processed, which Requirement 23.6 has it discard,
        # silently dropping a real state change.
        if reference.family is SIGNAL_FAMILY:
            seed_signal_sequence(reference.resource_id, restart=first)

        return True

    def unsubscribe_owned(self, tenant_id: str, client_id: str, channel: str) -> None:
        """Drop a subscription to an owner-scoped ``channel`` (Requirement 23.2)."""
        connection = self._get_connection(tenant_id, client_id)
        if connection:
            try:
                connection.unsubscribe(channel)
            except ValueError:
                pass

        subscribers = self._owned_subscribers.get(channel)
        if subscribers is not None:
            subscribers.discard((tenant_id, client_id))
            if not subscribers:
                del self._owned_subscribers[channel]
                self._release_signal_sequence(channel)

    def owned_subscriber_count(self, channel: str) -> int:
        """How many connections are subscribed to ``channel``."""
        return len(self._owned_subscribers.get(channel, set()))

    def _release_signal_sequence(self, channel: str) -> None:
        """Drop ``channel``'s ``seq`` counter, once its LAST subscriber has gone.

        Task 14.2. The counter may only be forgotten when no connection still holds an
        ``expectedSequence`` for it; while one does, a restarted counter republishes
        numbers that connection has already processed and Requirement 23.6 has it
        discard them, which would lose real state changes.
        """
        from backend_app.backend.ws_channels import (
            SIGNAL_FAMILY,
            release_signal_sequence,
        )

        resource_id = SIGNAL_FAMILY.parse(channel)
        if resource_id is not None:
            release_signal_sequence(resource_id)

    async def broadcast_owned_event(
        self,
        channel: str,
        event: str,
        payload: Dict[str, Any],
        owner_id: Optional[str] = None,
    ) -> int:
        """Push one frame to the authorised subscribers of ``channel``.

        Delivery is confined to connections that appear in
        ``_owned_subscribers[channel]`` — that is, connections whose ownership of that
        resource was resolved at subscribe time. When ``owner_id`` is given it is
        re-checked against each connection's ``user_id`` before the send: an owner
        cannot change (``training_jobs.user_id``, ``strategies.user_id`` and
        ``strategy_deployments.user_id`` are set once, and the owner-scoped RLS update
        policies reuse their USING clause for the new row, so none can be reassigned),
        so this can only ever agree — which is exactly why it is cheap enough to keep
        as a second line of defence.

        The frame carries the resource id under its family's own field name
        (``job_id``, ``strategy_id``, ``deployment_id``), so a client reads which
        resource a frame is about from a named field rather than by parsing a channel.

        Returns the number of connections the frame reached, so a caller can tell
        "nobody was listening" from "sent".
        """
        from backend_app.backend.ws_channels import parse_owned_channel

        reference = parse_owned_channel(channel)
        if reference is None:
            logger.warning(
                "Refusing to publish %r on %r: no channel family routes that name.",
                event,
                channel,
            )
            return 0

        if not reference.family.is_event(event):
            # An unrecognised frame type is a programming error in the producer, not
            # something to forward to a browser as if it were a state change. A
            # ``training.progress`` on a deployment channel is refused for the same
            # reason as an invented event name.
            logger.warning(
                "Refusing to publish unknown %s event %r on %s",
                reference.family.namespace,
                event,
                channel,
            )
            return 0

        targets = list(self._owned_subscribers.get(channel, set()))
        if not targets:
            return 0

        message = {
            "type": event,
            "channel": channel,
            reference.resource: reference.resource_id,
            **dict(payload or {}),
            "broadcast_time": datetime.utcnow().isoformat(),
        }

        delivered = 0
        disconnected = []
        for tenant_id, client_id in targets:
            connection = self._get_connection(tenant_id, client_id)
            if not connection or not connection.is_alive:
                disconnected.append((tenant_id, client_id))
                continue
            if owner_id is not None and connection.user_id != str(owner_id):
                logger.warning(
                    "Dropping %s for %s: the subscribed connection is not the "
                    "resource owner.",
                    event,
                    channel,
                )
                continue
            try:
                await connection.send(message)
                delivered += 1
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Broadcast error to {client_id} on {channel}: {exc}")
                disconnected.append((tenant_id, client_id))

        for tid, cid in disconnected:
            self.disconnect(tid, cid)

        return delivered

    # ── task 6.7's training methods, now wrappers over the general ones ───
    #
    # Kept by name and answering exactly what they answered before, because they are
    # what ``strategy_service.publish_training_event`` and
    # ``tests/test_training_realtime_channels.py`` call.

    def subscribe_training(self, tenant_id: str, client_id: str, job_id: str) -> bool:
        """Register an ALREADY-AUTHORISED subscription to ``training.{job_id}``."""
        from backend_app.backend.ws_channels import training_channel

        try:
            channel = training_channel(job_id)
        except ValueError:
            return False
        return self.subscribe_owned(tenant_id, client_id, channel)

    def unsubscribe_training(self, tenant_id: str, client_id: str, job_id: str) -> None:
        """Drop a subscription to ``training.{job_id}`` (Requirement 23.2)."""
        from backend_app.backend.ws_channels import training_channel

        try:
            channel = training_channel(job_id)
        except ValueError:
            return
        self.unsubscribe_owned(tenant_id, client_id, channel)

    def training_subscriber_count(self, job_id: str) -> int:
        """How many connections are subscribed to ``training.{job_id}``."""
        from backend_app.backend.ws_channels import training_channel

        try:
            return self.owned_subscriber_count(training_channel(job_id))
        except ValueError:
            return 0

    async def broadcast_training_event(
        self,
        job_id: str,
        event: str,
        payload: Dict[str, Any],
        owner_id: Optional[str] = None,
    ) -> int:
        """Push one training frame to the subscribers of ``training.{job_id}``."""
        from backend_app.backend.ws_channels import training_channel

        try:
            channel = training_channel(job_id)
        except ValueError:
            logger.warning("Refusing to publish %r for job %r", event, job_id)
            return 0
        return await self.broadcast_owned_event(
            channel, event, payload, owner_id=owner_id
        )

    # ── the four channels task 8.5 adds, one named producer seam each ─────
    #
    # Thin by design: each is one line over ``broadcast_owned_event``, and exists so a
    # producer names the channel it is publishing to in its own vocabulary rather than
    # composing a channel string. A producer that composed one would be a second place
    # for the separator to be got wrong.

    async def broadcast_validation_event(
        self,
        strategy_id: str,
        event: str,
        payload: Dict[str, Any],
        owner_id: Optional[str] = None,
    ) -> int:
        """Publish on ``builder.validation.{strategy_id}``."""
        from backend_app.backend.ws_channels import BUILDER_VALIDATION_FAMILY

        return await self._broadcast_family(
            BUILDER_VALIDATION_FAMILY, strategy_id, event, payload, owner_id
        )

    async def broadcast_strategy_event(
        self,
        strategy_id: str,
        event: str,
        payload: Dict[str, Any],
        owner_id: Optional[str] = None,
    ) -> int:
        """Publish on ``strategy.{strategy_id}``."""
        from backend_app.backend.ws_channels import STRATEGY_FAMILY

        return await self._broadcast_family(
            STRATEGY_FAMILY, strategy_id, event, payload, owner_id
        )

    async def broadcast_deployment_event(
        self,
        deployment_id: str,
        event: str,
        payload: Dict[str, Any],
        owner_id: Optional[str] = None,
    ) -> int:
        """Publish on ``deployment.{deployment_id}``."""
        from backend_app.backend.ws_channels import DEPLOYMENT_FAMILY

        return await self._broadcast_family(
            DEPLOYMENT_FAMILY, deployment_id, event, payload, owner_id
        )

    async def broadcast_execution_event(
        self,
        deployment_id: str,
        event: str,
        payload: Dict[str, Any],
        owner_id: Optional[str] = None,
    ) -> int:
        """Publish on ``execution.{deployment_id}``."""
        from backend_app.backend.ws_channels import EXECUTION_FAMILY

        return await self._broadcast_family(
            EXECUTION_FAMILY, deployment_id, event, payload, owner_id
        )

    async def broadcast_signal_event(
        self,
        deployment_id: str,
        event: str,
        payload: Dict[str, Any],
        owner_id: Optional[str] = None,
    ) -> int:
        """Publish on ``signal.{deployment_id}`` — trading-lifecycle-integration 14.2.

        The fifth of these one-line seams, and it takes an ALREADY-BUILT payload:
        ``ws_channels.signal_frame`` is what stamps the ``seq`` and the content-level
        dedup key, so this method neither assigns a sequence number nor projects a
        signal. A frame arriving here with no ``seq`` would be published without one
        rather than silently repaired — which is why the producer
        (``signal_service.publish_signal_frame``) always builds through
        ``signal_frame``.
        """
        from backend_app.backend.ws_channels import SIGNAL_FAMILY

        return await self._broadcast_family(
            SIGNAL_FAMILY, deployment_id, event, payload, owner_id
        )

    async def broadcast_runtime_state(
        self,
        deployment_id: str,
        runtime_state: Any,
        owner_id: Optional[str] = None,
    ) -> int:
        """Publish task 8.4's ``PlanRuntimeState`` on ``deployment.{deployment_id}``.

        Requirement 20.12. The labels, the bar counts and the missing-port lists are
        whatever ``PlanRuntimeState.to_dict()`` says they are — ``ws_channels``
        projects them and this method sends them. Neither decides them.
        """
        from backend_app.backend.ws_channels import (
            DeploymentEvent,
            runtime_state_frame,
        )

        frame = runtime_state_frame(deployment_id, runtime_state)
        return await self.broadcast_deployment_event(
            deployment_id,
            DeploymentEvent.RUNTIME_STATE.value,
            {"runtime_state": frame["runtime_state"]},
            owner_id=owner_id,
        )

    async def broadcast_canvas_state(
        self,
        strategy_id: str,
        canvas_state: Any,
        owner_id: Optional[str] = None,
    ) -> int:
        """Publish task 8.3's ``canvas_state`` on ``strategy.{strategy_id}``.

        Requirement 9.9's deployed lock. The verdict is the backend's; this sends it.
        """
        from backend_app.backend.ws_channels import StrategyEvent, canvas_state_frame

        frame = canvas_state_frame(strategy_id, canvas_state)
        return await self.broadcast_strategy_event(
            strategy_id,
            StrategyEvent.CANVAS_STATE.value,
            {"canvas_state": frame["canvas_state"]},
            owner_id=owner_id,
        )

    async def _broadcast_family(
        self,
        family: Any,
        resource_id: str,
        event: str,
        payload: Dict[str, Any],
        owner_id: Optional[str],
    ) -> int:
        try:
            channel = family.channel(resource_id)
        except ValueError:
            logger.warning(
                "Refusing to publish %r for %s %r: not a well-formed identifier.",
                event,
                family.resource,
                resource_id,
            )
            return 0
        return await self.broadcast_owned_event(
            channel, event, payload, owner_id=owner_id
        )

    async def broadcast_to_tenant(
        self,
        tenant_id: str,
        channel: str,
        message: Dict[str, Any]
    ):
        """
        STEP 5.5: Broadcast message to all clients in tenant subscribed to channel.
        
        Args:
            tenant_id: Target tenant
            channel: Channel name (orders, positions, pnl, portfolio)
            message: Message payload
        """
        if tenant_id not in self._connections:
            return
        
        # Add timestamp and channel
        message["channel"] = channel
        message["broadcast_time"] = datetime.utcnow().isoformat()
        
        # Get subscribed clients
        subscribed_clients = self._channel_subscribers.get(channel, {}).get(tenant_id, set())
        all_clients = self._channel_subscribers.get("all", {}).get(tenant_id, set())
        target_clients = subscribed_clients | all_clients
        
        # Send to all subscribed clients
        disconnected = []
        
        for client_id in target_clients:
            connection = self._get_connection(tenant_id, client_id)
            if connection and connection.is_alive:
                try:
                    await connection.send(message)
                except Exception as e:
                    logger.error(f"Broadcast error to {client_id}: {e}")
                    disconnected.append((tenant_id, client_id))
            else:
                disconnected.append((tenant_id, client_id))
        
        # Clean up disconnected clients
        for tid, cid in disconnected:
            self.disconnect(tid, cid)
    
    async def push_order_update(
        self,
        tenant_id: str,
        order_id: str,
        status: str,
        filled: str,
        price: str,
        execution_id: str
    ):
        """Push order update to frontend."""
        await self.broadcast_to_tenant(
            tenant_id=tenant_id,
            channel="orders",
            message={
                "type": "order_update",
                "order_id": order_id,
                "execution_id": execution_id,
                "status": status,
                "filled": filled,
                "price": price,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    
    async def push_position_update(
        self,
        tenant_id: str,
        position_id: str,
        symbol: str,
        size: str,
        avg_price: str,
        unrealized_pnl: str
    ):
        """Push position update to frontend."""
        await self.broadcast_to_tenant(
            tenant_id=tenant_id,
            channel="positions",
            message={
                "type": "position_update",
                "position_id": position_id,
                "symbol": symbol,
                "size": size,
                "avg_price": avg_price,
                "unrealized_pnl": unrealized_pnl,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    
    async def push_pnl_update(
        self,
        tenant_id: str,
        total_pnl: str,
        unrealized_pnl: str,
        realized_pnl: str
    ):
        """Push PnL update to frontend."""
        await self.broadcast_to_tenant(
            tenant_id=tenant_id,
            channel="pnl",
            message={
                "type": "pnl_update",
                "total_pnl": total_pnl,
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl": realized_pnl,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    
    async def push_portfolio_update(
        self,
        tenant_id: str,
        equity: str,
        exposure: str,
        position_count: int
    ):
        """Push portfolio update to frontend."""
        await self.broadcast_to_tenant(
            tenant_id=tenant_id,
            channel="portfolio",
            message={
                "type": "portfolio_update",
                "equity": equity,
                "exposure": exposure,
                "position_count": position_count,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    
    def _get_connection(
        self,
        tenant_id: str,
        client_id: str
    ) -> Optional[WebSocketConnection]:
        """Get connection by tenant and client ID."""
        return self._connections.get(tenant_id, {}).get(client_id)
    
    async def start(self):
        """Start WebSocket manager with heartbeat."""
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("WebSocketManager started")
    
    async def stop(self):
        """Stop WebSocket manager."""
        self._running = False
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # Close all connections
        for tenant_id, clients in self._connections.items():
            for client_id, connection in clients.items():
                try:
                    await connection.websocket.close()
                except Exception:
                    pass
        
        self._connections.clear()
        logger.info("WebSocketManager stopped")
    
    async def _heartbeat_loop(self):
        """Send periodic heartbeats to keep connections alive."""
        while self._running:
            try:
                await asyncio.sleep(30)  # 30-second heartbeat
                
                # Send ping to all connections
                for tenant_id, clients in list(self._connections.items()):
                    for client_id, connection in list(clients.items()):
                        if not connection.is_alive:
                            self.disconnect(tenant_id, client_id)
                            continue
                        
                        try:
                            await connection.websocket.send_json({
                                "type": "ping",
                                "timestamp": datetime.utcnow().isoformat()
                            })
                            connection.last_ping = datetime.utcnow()
                        except Exception:
                            self.disconnect(tenant_id, client_id)
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get WebSocket statistics."""
        total_connections = sum(
            len(clients) for clients in self._connections.values()
        )
        
        from backend_app.backend.ws_channels import TRAINING_FAMILY

        training = {
            channel: clients
            for channel, clients in self._owned_subscribers.items()
            if TRAINING_FAMILY.claims(channel)
        }

        return {
            "total_connections": total_connections,
            "tenants": len(self._connections),
            # strategy-builder task 6.7: per-job training channels are counted
            # separately because they live in their own registry (see __init__).
            # Task 8.5 kept both figures meaning exactly what they meant, and added
            # the same pair over every owner-scoped family.
            "training_channels": len(training),
            "training_subscribers": sum(len(clients) for clients in training.values()),
            "owned_channels": len(self._owned_subscribers),
            "owned_subscribers": sum(
                len(clients) for clients in self._owned_subscribers.values()
            ),
            "channels": {
                channel: sum(
                    len(clients) for clients in tenant_subs.values()
                )
                for channel, tenant_subs in self._channel_subscribers.items()
            }
        }


# Global instance
_websocket_manager: Optional[WebSocketManager] = None


def get_websocket_manager() -> WebSocketManager:
    """Get or create global WebSocket manager."""
    global _websocket_manager
    if _websocket_manager is None:
        _websocket_manager = WebSocketManager()
    return _websocket_manager


# FastAPI WebSocket endpoint
async def websocket_endpoint(
    websocket: WebSocket,
    token: str  # JWT token for authentication
):
    """
    FastAPI WebSocket endpoint.
    
    Usage:
        ws = new WebSocket("wss://api.example.com/ws?token=...");
        ws.send(JSON.stringify({"action": "subscribe", "channel": "orders"}));
        ws.send(JSON.stringify({"action": "subscribe", "channel": "training.<job_id>"}));
        ws.send(JSON.stringify({"action": "subscribe", "channel": "deployment.<id>"}));
        ws.send(JSON.stringify({"action": "auth", "token": "<refreshed>"}));
    
    strategy-builder tasks 6.7 and 8.5: this is the ONE connection everything
    multiplexes over (Requirement 23.1). Every owner-scoped subscription —
    ``training.{job_id}``, ``builder.validation.{strategy_id}``,
    ``strategy.{strategy_id}``, ``deployment.{deployment_id}``,
    ``execution.{deployment_id}`` — is authorised against the owner of the row it
    names and REFUSED, with a reported reason, when it is not this user's
    (Requirements 21.5, 21.6). The refusal closes the subscription, not the
    connection: the client's other channels keep flowing, which is what makes a
    refusal reportable rather than a disconnect the client has to guess about.

    Task 8.5 added the ``auth`` action for Requirement 23.3. A refreshed token
    REPLACES the identity future subscriptions are authorised against; it does not
    reconnect, and it does not retroactively bless anything already subscribed. A
    token that does not decode, or one for a different user, closes the connection
    rather than leaving it running under an identity the server can no longer verify.
    """
    # Authenticate and get tenant_id from token
    try:
        from backend_app.core.websocket_auth import _decode_hs256_token
        payload = _decode_hs256_token(token)
        if not payload:
            await websocket.close(code=1008, reason="Invalid token")
            return
        
        user_id = payload.get("sub")
        if not user_id:
            await websocket.close(code=1008, reason="Invalid token: missing user ID")
            return
        
        tenant_id = payload.get("tenant_id") or payload.get("app_metadata", {}).get("tenant_id") or user_id
        client_id = f"{tenant_id}_{id(websocket)}"
    except Exception as e:
        await websocket.close(code=1008, reason="Invalid token")
        return
    
    # The identity this connection's subscriptions are authorised against. The token
    # was verified above, so this is the authenticated user, not a client-supplied one.
    user = {
        "id": user_id,
        "tenant_id": tenant_id,
        "access_token": token,
    }
    
    # Imported here rather than at module scope, matching the existing lazy import of
    # ``_decode_hs256_token`` above: this module is imported widely and the
    # authorisation seam reaches the training service, which must not be pulled in as
    # an import-time dependency of the transport.
    from backend_app.backend.ws_channels import (
        claims_owned_namespace,
        parse_owned_channel,
    )
    from backend_app.core.websocket_auth import authorize_channel_subscription
    
    # Get manager
    manager = get_websocket_manager()
    
    # Connect
    await websocket.accept()
    connection = WebSocketConnection(websocket, tenant_id, client_id, user_id=user_id)
    # ``connect`` needs the keys it stores the connection under; omitting them raised a
    # TypeError before the first frame could be read, so no subscription of any kind
    # was reachable through this endpoint.
    await manager.connect(connection, tenant_id, client_id)
    
    try:
        while True:
            # Receive message
            data = await websocket.receive_json()
            
            action = data.get("action")
            channel = data.get("channel")
            
            if action == "subscribe" and channel:
                reference = parse_owned_channel(channel)
                if reference is not None or claims_owned_namespace(channel):
                    # strategy-builder tasks 6.7 / 8.5, Requirements 21.5, 21.6:
                    # resolve the resource's owner before the client is subscribed to
                    # anything, and report a refusal instead of
                    # accepting-then-not-delivering.
                    decision = await authorize_channel_subscription(channel, user)
                    if not decision.allowed:
                        await connection.send(decision.refusal_frame())
                        continue
                    manager.subscribe_owned(tenant_id, client_id, channel)
                else:
                    manager.subscribe(tenant_id, client_id, channel)
                await connection.send({
                    "type": "subscribed",
                    "channel": channel
                })
                
            elif action == "unsubscribe" and channel:
                if parse_owned_channel(channel) is not None:
                    # No authorisation on the way out: dropping a subscription can only
                    # ever reduce what this connection receives.
                    manager.unsubscribe_owned(tenant_id, client_id, channel)
                else:
                    manager.unsubscribe(tenant_id, client_id, channel)
                await connection.send({
                    "type": "unsubscribed",
                    "channel": channel
                })
                
            elif action == "auth":
                # Requirement 23.3: a refreshed token reauthenticates the EXISTING
                # connection. No reconnect, and no re-blessing of what is already
                # subscribed — those were authorised against the identity that held at
                # the time, and this identity must be the same user anyway.
                refreshed = data.get("token")
                payload = _decode_hs256_token(refreshed) if refreshed else None
                if not payload or payload.get("sub") != user_id:
                    # A token that does not verify, or one for another user, is not a
                    # refresh. Closing beats running on under an identity the server
                    # cannot vouch for, and beats silently keeping the old one while
                    # the client believes it has re-presented credentials.
                    await connection.send({
                        "type": "auth_failed",
                        "reason": (
                            "The refreshed token could not be verified for this "
                            "connection's user, so the connection was closed."
                        ),
                    })
                    manager.disconnect(tenant_id, client_id)
                    await websocket.close(
                        code=1008, reason="Reauthentication failed"
                    )
                    return
                user["access_token"] = refreshed
                await connection.send({
                    "type": "authenticated",
                    "timestamp": datetime.utcnow().isoformat(),
                })
                
            elif action == "ping":
                await connection.send({
                    "type": "pong",
                    "timestamp": datetime.utcnow().isoformat()
                })
                
    except WebSocketDisconnect:
        manager.disconnect(tenant_id, client_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(tenant_id, client_id)
