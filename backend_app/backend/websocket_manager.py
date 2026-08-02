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
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WebSocketConnection:
    """Represents a single WebSocket connection."""
    
    def __init__(self, websocket: WebSocket, tenant_id: str, client_id: str):
        self.websocket = websocket
        self.tenant_id = tenant_id
        self.client_id = client_id
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
        
        return {
            "total_connections": total_connections,
            "tenants": len(self._connections),
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
    
    # Get manager
    manager = get_websocket_manager()
    
    # Connect
    await websocket.accept()
    connection = WebSocketConnection(websocket, tenant_id, client_id)
    await manager.connect(connection)
    
    try:
        while True:
            # Receive message
            data = await websocket.receive_json()
            
            action = data.get("action")
            channel = data.get("channel")
            
            if action == "subscribe" and channel:
                manager.subscribe(tenant_id, client_id, channel)
                await connection.send({
                    "type": "subscribed",
                    "channel": channel
                })
                
            elif action == "unsubscribe" and channel:
                manager.unsubscribe(tenant_id, client_id, channel)
                await connection.send({
                    "type": "unsubscribed",
                    "channel": channel
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
