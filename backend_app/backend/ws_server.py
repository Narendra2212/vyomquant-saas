"""
backend/ws_server.py — DEDICATED WEBSOCKET SERVER

STEP 8.11+ — SCALING FOR 500 USERS (~150 ACTIVE)

GOAL: Separate WebSocket handling from main backend to improve:
  - Scalability: WebSocket load doesn't block REST API
  - Stability: Dedicated resources for real-time updates
  - Throughput: Handle 150 concurrent WebSocket connections

ARCHITECTURE:
┌──────────────────────────────────────────────────────────────────────┐
│                         BROWSER / CLIENT                             │
└──────────────┬────────────────────────────────┬────────────────────┘
               │                                │
       ┌───────▼───────┐              ┌────────▼────────┐
       │  REST API     │              │  WebSocket      │
       │  Backend      │              │  Server         │
       │  (8000)       │              │  (8002)         │
       └───────┬───────┘              └────────┬────────┘
               │                                │
               │         Redis Pub/Sub          │
               └──────────────┬────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │   Redis Cluster   │
                    │  (Event Bus)      │
                    └───────────────────┘

EVENT FLOW:
1. Backend generates event (order filled, position update, etc.)
2. Backend PUBLISHES event to Redis channel
3. WebSocket Server SUBSCRIBES to Redis channels
4. WebSocket Server broadcasts to connected clients

BENEFITS:
- ✅ WebSocket connections don't block REST API
- ✅ Can scale WebSocket server independently (4 workers)
- ✅ Clean separation of concerns
- ✅ Better resource allocation
- ✅ Easier monitoring and debugging
"""

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional

import redis.asyncio as aioredis
import uvicorn
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend_app.backend.websocket_manager import (WebSocketConnection,
                                                   WebSocketManager)
from backend_app.backend.websocket_monitor import WebSocketHealthMonitor

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


logger = logging.getLogger("WSServer")

# =============================================================================
# CONFIGURATION
# =============================================================================

WS_SERVER_HOST = os.getenv("WS_SERVER_HOST", "0.0.0.0")  # nosec: B104 - container service binding
WS_SERVER_PORT = int(os.getenv("WS_SERVER_PORT", "8002"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
WS_SERVER_WORKERS = int(os.getenv("WS_SERVER_WORKERS", "4"))

# Redis Pub/Sub channels
CHANNELS = [
    "orders",           # Order updates
    "positions",        # Position updates
    "pnl",              # PnL updates
    "portfolio",        # Portfolio updates
    "signals",          # Trading signals
    "market_data",      # Market data updates
    "alerts",           # System alerts
    "circuit_breaker",  # Circuit breaker state changes
    "signal_trace",     # Signal execution live traces
    "risk_events",      # Risk validation events
]

# =============================================================================
# WEBSOCKET MANAGER (SERVER-SPECIFIC)
# =============================================================================

class WebSocketServerManager(WebSocketManager):
    """
    Extended WebSocket manager for dedicated server.
    
    Adds Redis Pub/Sub integration for cross-server event broadcasting.
    """
    
    def __init__(self):
        super().__init__()
        self._redis: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None
        self._redis_task: Optional[asyncio.Task] = None
        self._monitor: Optional[WebSocketHealthMonitor] = None
        self._shutdown_event = asyncio.Event()
        
    async def connect_redis(self):
        """Connect to Redis for Pub/Sub."""
        try:
            self._redis = await aioredis.from_url(
                REDIS_URL,
                encoding="utf-8",
                decode_responses=True
            )
            self._pubsub = self._redis.pubsub()
            
            # Subscribe to all channels
            await self._pubsub.subscribe(*CHANNELS)
            logger.info(f"[WS Server] Subscribed to channels: {CHANNELS}")
            
            # Start listening for messages
            self._redis_task = asyncio.create_task(
                self._redis_listener(),
                name="redis_listener"
            )
            
            # Initialize health monitor
            self._monitor = WebSocketHealthMonitor()
            
            logger.info("[WS Server] Redis Pub/Sub connected")
            
        except Exception as e:
            logger.error(f"[WS Server] Redis connection failed: {e}")
            raise
    
    async def disconnect_redis(self):
        """Disconnect from Redis."""
        self._shutdown_event.set()
        
        if self._redis_task:
            self._redis_task.cancel()
            try:
                await self._redis_task
            except asyncio.CancelledError:
                pass
        
        if self._pubsub:
            await self._pubsub.unsubscribe(*CHANNELS)
            await self._pubsub.close()
        
        if self._redis:
            await self._redis.close()
        
        logger.info("[WS Server] Redis disconnected")
    
    async def _redis_listener(self):
        """
        Listen for Redis Pub/Sub messages and broadcast to WebSocket clients.
        """
        logger.info("[WS Server] Redis listener started")
        
        try:
            while not self._shutdown_event.is_set():
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0
                )
                
                if message:
                    channel = message['channel']
                    data = json.loads(message['data'])
                    
                    # Extract tenant_id from message
                    tenant_id = data.get('tenant_id')
                    
                    if tenant_id:
                        # Broadcast to specific tenant's clients
                        await self.broadcast_to_tenant(
                            tenant_id=tenant_id,
                            channel=channel,
                            message=data
                        )
                    else:
                        # Broadcast to all subscribers of this channel
                        await self.broadcast(channel, data)
                    
                    logger.debug(f"[WS Server] Broadcasted {channel} to tenant {tenant_id}")
                    
        except asyncio.CancelledError:
            logger.info("[WS Server] Redis listener cancelled")
        except Exception as e:
            logger.error(f"[WS Server] Redis listener error: {e}")
    
    async def broadcast_to_tenant(
        self,
        tenant_id: str,
        channel: str,
        message: Dict[str, Any]
    ):
        """Broadcast message to all clients of a specific tenant."""
        if tenant_id not in self._connections:
            return
        
        disconnected = []
        
        for client_id, conn in self._connections[tenant_id].items():
            if not conn.is_alive:
                disconnected.append(client_id)
                continue
            
            if conn.is_subscribed(channel):
                try:
                    await conn.send(message)
                except Exception as e:
                    logger.error(f"[WS Server] Send error to {client_id}: {e}")
                    disconnected.append(client_id)
        
        # Clean up disconnected clients
        for client_id in disconnected:
            await self.disconnect(tenant_id, client_id)
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get server statistics."""
        total_connections = sum(
            len(clients) for clients in self._connections.values()
        )
        
        return {
            "total_connections": total_connections,
            "total_tenants": len(self._connections),
            "channels": list(self._channel_subscribers.keys()),
            "redis_connected": self._redis is not None and await self._redis.ping(),
            "timestamp": datetime.utcnow().isoformat(),
        }

# =============================================================================
# GLOBAL MANAGER INSTANCE
# =============================================================================

_ws_manager = WebSocketServerManager()

def get_ws_manager() -> WebSocketServerManager:
    """Get WebSocket manager instance."""
    return _ws_manager

# =============================================================================
# FASTAPI APPLICATION
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("[WS Server] Starting up...")
    await _ws_manager.connect_redis()
    logger.info(f"[WS Server] Listening on {WS_SERVER_HOST}:{WS_SERVER_PORT}")
    
    yield
    
    # Shutdown
    logger.info("[WS Server] Shutting down...")
    await _ws_manager.disconnect_redis()
    logger.info("[WS Server] Shutdown complete")

# Create FastAPI app
app = FastAPI(
    title="Trading Platform WebSocket Server",
    description="Dedicated WebSocket server for real-time updates",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================================
# WEBSOCKET ENDPOINTS
# =============================================================================

@app.websocket("/ws/{tenant_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    tenant_id: str,
    token: Optional[str] = Query(None),
    channels: Optional[str] = Query(None)
):
    """
    Main WebSocket endpoint for real-time updates.
    
    Args:
        tenant_id: Tenant identifier
        token: Authentication token (required)
        channels: Comma-separated list of channels to subscribe
    """
    # Verify WebSocket auth — FAIL CLOSED
    if not token:
        await websocket.close(code=4001, reason="Authentication required: provide ?token=")
        return
        
    try:
        from backend_app.core.websocket_auth import _decode_hs256_token
        payload = _decode_hs256_token(token)
        if not payload:
            await websocket.close(code=4001, reason="Unauthorized: Invalid token")
            return
        
        token_user_id = payload.get("sub")
        if not token_user_id:
            await websocket.close(code=4003, reason="Invalid token: missing user ID")
            return
        
        # Verify tenant_id matches token
        if str(token_user_id) != str(tenant_id):
            await websocket.close(code=4003, reason="Unauthorized: tenant_id mismatch")
            return
    except Exception as e:
        logger.warning(f"WebSocket auth failed for tenant {tenant_id}: {e}")
        await websocket.close(code=4003, reason="Authentication verification failed")
        return
    
    manager = get_ws_manager()
    
    # Generate client ID
    client_id = f"{tenant_id}_{datetime.utcnow().timestamp()}_{id(websocket)}"
    
    # Accept connection
    await websocket.accept()
    
    # Register connection
    conn = WebSocketConnection(websocket, tenant_id, client_id)
    await manager.connect(conn)
    
    # Subscribe to requested channels
    if channels:
        requested_channels = [c.strip() for c in channels.split(",")]
        for channel in requested_channels:
            if channel in CHANNELS:
                conn.subscribe(channel)
                logger.info(f"[WS Server] Client {client_id} subscribed to {channel}")
    
    logger.info(f"[WS Server] Client connected: {client_id} (tenant: {tenant_id})")
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message = json.loads(data)
            
            action = message.get("action")
            
            if action == "subscribe":
                channel = message.get("channel")
                if channel in CHANNELS:
                    conn.subscribe(channel)
                    await conn.send({
                        "type": "subscription_confirmed",
                        "channel": channel,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                    
            elif action == "unsubscribe":
                channel = message.get("channel")
                conn.unsubscribe(channel)
                await conn.send({
                    "type": "unsubscription_confirmed",
                    "channel": channel,
                    "timestamp": datetime.utcnow().isoformat()
                })
                
            elif action == "ping":
                await conn.send({
                    "type": "pong",
                    "timestamp": datetime.utcnow().isoformat()
                })
                
            elif action == "get_stats":
                stats = await manager.get_stats()
                await conn.send({
                    "type": "stats",
                    "data": stats
                })
                
    except WebSocketDisconnect:
        logger.info(f"[WS Server] Client disconnected: {client_id}")
    except Exception as e:
        logger.error(f"[WS Server] Error with client {client_id}: {e}")
    finally:
        await manager.disconnect(tenant_id, client_id)


@app.websocket("/ws/public/{channel}")
async def public_websocket(
    websocket: WebSocket,
    channel: str,
    token: str = Query(None)
):
    """Public WebSocket endpoint - authentication required."""
    manager = get_ws_manager()
    
    # Verify WebSocket auth — FAIL CLOSED
    if not token:
        await websocket.close(code=4001, reason="Authentication required: provide ?token=")
        return
        
    try:
        from backend_app.core.websocket_auth import _decode_hs256_token
        payload = _decode_hs256_token(token)
        if not payload:
            await websocket.close(code=4001, reason="Unauthorized: Invalid token")
            return
        
        user_id = payload.get("sub")
        if not user_id:
            await websocket.close(code=4003, reason="Invalid token: missing user ID")
            return
        tenant_id = user_id
    except Exception as e:
        logger.warning(f"WebSocket auth failed for public channel {channel}: {e}")
        await websocket.close(code=4003, reason="Authentication verification failed")
        return
    
    client_id = f"public_{channel}_{datetime.utcnow().timestamp()}_{id(websocket)}"
    
    await websocket.accept()
    
    conn = WebSocketConnection(websocket, tenant_id, client_id)
    await manager.connect(conn)
    
    if channel in CHANNELS:
        conn.subscribe(channel)
    
    logger.info(f"[WS Server] Public client connected: {client_id} (channel: {channel})")
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("action") == "ping":
                await conn.send({
                    "type": "pong",
                    "timestamp": datetime.utcnow().isoformat()
                })
                
    except WebSocketDisconnect:
        logger.info(f"[WS Server] Public client disconnected: {client_id}")
    except Exception as e:
        logger.error(f"[WS Server] Public client error: {e}")
    finally:
        await manager.disconnect(tenant_id, client_id)


# =============================================================================
# REST API ENDPOINTS (For health checks and stats)
# =============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    manager = get_ws_manager()
    stats = await manager.get_stats()
    
    return {
        "status": "healthy" if stats["redis_connected"] else "degraded",
        "service": "websocket-server",
        **stats
    }


@app.get("/health/ready")
async def readiness_check():
    """Readiness check for Kubernetes."""
    manager = get_ws_manager()
    stats = await manager.get_stats()
    
    if not stats["redis_connected"]:
        return {
            "status": "not_ready",
            "reason": "redis_not_connected"
        }, 503
    
    return {
        "status": "ready",
        "service": "websocket-server"
    }


@app.get("/health/live")
async def liveness_check():
    """Liveness check for Kubernetes."""
    return {
        "status": "alive",
        "service": "websocket-server",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/stats")
async def get_stats():
    """Get WebSocket server statistics."""
    manager = get_ws_manager()
    return await manager.get_stats()


@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint."""
    manager = get_ws_manager()
    stats = await manager.get_stats()
    
    # Simple Prometheus format
    metrics = f"""# HELP ws_connections_total Total WebSocket connections
# TYPE ws_connections_total gauge
ws_connections_total {stats['total_connections']}

# HELP ws_tenants_total Total connected tenants
# TYPE ws_tenants_total gauge
ws_tenants_total {stats['total_tenants']}

# HELP ws_redis_connected Redis connection status
# TYPE ws_redis_connected gauge
ws_redis_connected {1 if stats['redis_connected'] else 0}
"""
    
    return metrics


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    logger.info(f"[WS Server] Starting with {WS_SERVER_WORKERS} workers")
    
    # Run server
    uvicorn.run(
        "backend.ws_server:app",
        host=WS_SERVER_HOST,
        port=WS_SERVER_PORT,
        workers=WS_SERVER_WORKERS,
        log_level="info",
        reload=False,  # Disable reload in production
    )
