"""
WebSocket Event Streaming Layer

Realtime streaming for Bot Monitoring + Signal Visualization.

Author: Senior Realtime Systems Engineer
"""

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

# Import canonical channel constants
# P0-04 FIX: Support both package-level import (backend.ws_channels) and
# direct sys.path import (ws_channels) so this module loads correctly whether
# executed as `python -m backend.ws_event_stream` or via sys.path injection.
try:
    from backend_app.backend.ws_channels import (ChannelType, EventType,
                                                                                                  is_valid_channel)
except ImportError:
    from backend_app.backend.ws_channels import ChannelType, EventType, is_valid_channel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ws_event_stream")


@dataclass
class WebSocketMessage:
    """
    Standardized WebSocket message format.
    
    Includes deduplication fields for replay safety:
    - event_id: Unique event identifier (preserved across replay)
    - sequence_id: Monotonic sequence number per channel/tenant
    - message_id: Unique message identifier (may change on replay)
    """
    type: str
    channel: str
    timestamp: str
    bot_id: Optional[str]
    strategy_id: Optional[str]
    tenant_id: str
    payload: Dict[str, Any]
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_id: Optional[str] = None  # Global unique event ID (preserved on replay)
    sequence_id: Optional[int] = None  # Monotonic sequence number
    
    def __post_init__(self):
        """Ensure event_id is set for deduplication."""
        if not self.event_id:
            # Generate event_id from message_id if not provided
            self.event_id = self.message_id
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps({
            "type": self.type,
            "channel": self.channel,
            "timestamp": self.timestamp,
            "bot_id": self.bot_id,
            "strategy_id": self.strategy_id,
            "tenant_id": self.tenant_id,
            "payload": self.payload,
            "message_id": self.message_id,
            "event_id": self.event_id,
            "sequence_id": self.sequence_id
        }, default=str)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WebSocketMessage":
        """Create from dictionary preserving event_id for deduplication."""
        return cls(
            type=data["type"],
            channel=data["channel"],
            timestamp=data["timestamp"],
            bot_id=data.get("bot_id"),
            strategy_id=data.get("strategy_id"),
            tenant_id=data["tenant_id"],
            payload=data["payload"],
            message_id=data.get("message_id", str(uuid.uuid4())),
            event_id=data.get("event_id"),  # Preserved from original event
            sequence_id=data.get("sequence_id")  # Preserved from original event
        )


@dataclass
class ClientConnection:
    """Represents a connected WebSocket client."""
    connection_id: str
    tenant_id: str
    websocket: Any  # WebSocket object (e.g., from websockets or fastapi)
    connected_at: datetime
    last_ping: float
    subscriptions: Set[ChannelType] = field(default_factory=set)
    message_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1000))
    is_connected: bool = True
    
    def subscribe(self, channel: ChannelType) -> bool:
        """Subscribe to a channel."""
        if channel not in self.subscriptions:
            self.subscriptions.add(channel)
            logger.info(f"Client {self.connection_id} subscribed to {channel.value}")
            return True
        return False
    
    def unsubscribe(self, channel: ChannelType) -> bool:
        """Unsubscribe from a channel."""
        if channel in self.subscriptions:
            self.subscriptions.remove(channel)
            logger.info(f"Client {self.connection_id} unsubscribed from {channel.value}")
            return True
        return False
    
    def is_subscribed(self, channel: ChannelType) -> bool:
        """Check if subscribed to channel."""
        return channel in self.subscriptions
    
    def update_ping(self):
        """Update last ping timestamp."""
        self.last_ping = time.time()
    
    def is_stale(self, timeout_seconds: float = 60.0) -> bool:
        """Check if connection is stale (no ping received)."""
        return (time.time() - self.last_ping) > timeout_seconds


class SubscriptionManager:
    """
    Manages WebSocket subscriptions with tenant isolation.
    
    Features:
    - Channel-based subscriptions
    - Tenant isolation
    - Connection tracking
    """
    
    def __init__(self):
        # connection_id -> ClientConnection
        self._connections: Dict[str, ClientConnection] = {}
        
        # tenant_id -> Set[connection_id]
        self._tenant_connections: Dict[str, Set[str]] = defaultdict(set)
        
        # channel -> Set[connection_id]
        self._channel_subscribers: Dict[ChannelType, Set[str]] = {
            channel: set() for channel in ChannelType
        }
        
        # tenant_id + channel -> Set[connection_id]
        self._tenant_channel_subscribers: Dict[str, Dict[ChannelType, Set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        
        self._lock = asyncio.Lock()
    
    async def register_connection(
        self,
        connection_id: str,
        tenant_id: str,
        websocket: Any
    ) -> ClientConnection:
        """
        Register a new WebSocket connection.
        
        Args:
            connection_id: Unique connection identifier
            tenant_id: Tenant for isolation
            websocket: WebSocket object
            
        Returns:
            ClientConnection instance
        """
        async with self._lock:
            client = ClientConnection(
                connection_id=connection_id,
                tenant_id=tenant_id,
                websocket=websocket,
                connected_at=datetime.now(timezone.utc),
                last_ping=time.time()
            )
            
            self._connections[connection_id] = client
            self._tenant_connections[tenant_id].add(connection_id)
            
            logger.info(f"Registered connection {connection_id} for tenant {tenant_id}")
            return client
    
    async def unregister_connection(self, connection_id: str) -> bool:
        """
        Unregister and cleanup a connection.
        
        Args:
            connection_id: Connection to remove
            
        Returns:
            True if removed, False if not found
        """
        async with self._lock:
            client = self._connections.pop(connection_id, None)
            if not client:
                return False
            
            # Remove from tenant
            self._tenant_connections[client.tenant_id].discard(connection_id)
            
            # Remove from all channels
            for channel in list(client.subscriptions):
                self._channel_subscribers[channel].discard(connection_id)
                self._tenant_channel_subscribers[client.tenant_id][channel].discard(connection_id)
            
            client.is_connected = False
            logger.info(f"Unregistered connection {connection_id}")
            return True
    
    async def subscribe(
        self,
        connection_id: str,
        channel: ChannelType
    ) -> bool:
        """
        Subscribe a connection to a channel.
        
        Args:
            connection_id: Client connection
            channel: Channel to subscribe
            
        Returns:
            True if successful
        """
        async with self._lock:
            client = self._connections.get(connection_id)
            if not client:
                return False
            
            if client.subscribe(channel):
                self._channel_subscribers[channel].add(connection_id)
                self._tenant_channel_subscribers[client.tenant_id][channel].add(connection_id)
                return True
            return False
    
    async def unsubscribe(
        self,
        connection_id: str,
        channel: ChannelType
    ) -> bool:
        """
        Unsubscribe a connection from a channel.
        
        Args:
            connection_id: Client connection
            channel: Channel to unsubscribe
            
        Returns:
            True if successful
        """
        async with self._lock:
            client = self._connections.get(connection_id)
            if not client:
                return False
            
            if client.unsubscribe(channel):
                self._channel_subscribers[channel].discard(connection_id)
                self._tenant_channel_subscribers[client.tenant_id][channel].discard(connection_id)
                return True
            return False
    
    async def get_subscribers(
        self,
        channel: ChannelType,
        tenant_id: Optional[str] = None
    ) -> List[ClientConnection]:
        """
        Get all subscribers for a channel.
        
        Args:
            channel: Target channel
            tenant_id: Optional tenant filter
            
        Returns:
            List of subscribed ClientConnections
        """
        async with self._lock:
            if tenant_id:
                connection_ids = self._tenant_channel_subscribers[tenant_id][channel]
            else:
                connection_ids = self._channel_subscribers[channel]
            
            return [
                self._connections[cid]
                for cid in connection_ids
                if cid in self._connections and self._connections[cid].is_connected
            ]
    
    async def get_connection(self, connection_id: str) -> Optional[ClientConnection]:
        """Get connection by ID."""
        async with self._lock:
            return self._connections.get(connection_id)
    
    async def get_tenant_connections(self, tenant_id: str) -> List[ClientConnection]:
        """Get all connections for a tenant."""
        async with self._lock:
            return [
                self._connections[cid]
                for cid in self._tenant_connections[tenant_id]
                if cid in self._connections
            ]
    
    async def get_all_connections(self) -> List[ClientConnection]:
        """Get all active connections."""
        async with self._lock:
            return list(self._connections.values())
    
    async def cleanup_stale_connections(self, timeout_seconds: float = 60.0) -> int:
        """
        Remove stale connections (no ping received).
        
        Args:
            timeout_seconds: Stale threshold
            
        Returns:
            Number of connections removed
        """
        removed = 0
        async with self._lock:
            stale_ids = [
                cid for cid, client in self._connections.items()
                if client.is_stale(timeout_seconds)
            ]
        
        for cid in stale_ids:
            await self.unregister_connection(cid)
            removed += 1
        
        if removed:
            logger.warning(f"Cleaned up {removed} stale connections")
        
        return removed


class EventReplayBuffer:
    """
    Event replay buffer for reconnect recovery.
    
    Maintains recent events that can be replayed to clients
    upon reconnection. Supports timestamp-based and 
    sequence-based replay with ordering guarantees.
    """
    
    def __init__(self, max_events_per_channel: int = 500, retention_seconds: float = 300):
        self._max_events = max_events_per_channel
        self._buffers: Dict[ChannelType, deque] = {
            channel: deque(maxlen=max_events_per_channel)
            for channel in ChannelType
        }
        self._tenant_buffers: Dict[str, Dict[ChannelType, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=max_events_per_channel))
        )
        self._retention_seconds = retention_seconds
        self._lock = asyncio.Lock()
        
        # Sequence counter per channel
        self._sequence_counters: Dict[ChannelType, int] = {
            channel: 0 for channel in ChannelType
        }
        self._tenant_sequence_counters: Dict[str, Dict[ChannelType, int]] = defaultdict(
            lambda: defaultdict(int)
        )
    
    async def store_event(self, message: WebSocketMessage) -> None:
        """
        Store event in replay buffer with sequence ID.
        
        Args:
            message: WebSocketMessage to store
        """
        async with self._lock:
            channel = ChannelType(message.channel)
            tenant_id = message.tenant_id
            
            # Get next sequence number
            self._sequence_counters[channel] += 1
            sequence_id = self._sequence_counters[channel]
            
            self._tenant_sequence_counters[tenant_id][channel] += 1
            tenant_sequence_id = self._tenant_sequence_counters[tenant_id][channel]
            
            # Assign sequence_id to message for deduplication
            message.sequence_id = tenant_sequence_id
            
            # Ensure event_id is set (for deduplication)
            if not message.event_id:
                message.event_id = message.message_id
            
            # Parse message timestamp
            msg_timestamp = time.time()
            if message.timestamp:
                try:
                    # Parse ISO format timestamp
                    dt = datetime.fromisoformat(message.timestamp.replace('Z', '+00:00'))
                    msg_timestamp = dt.timestamp()
                except Exception:
                    pass
            
            event_entry = {
                "message": message,
                "stored_timestamp": time.time(),
                "message_timestamp": msg_timestamp,
                "sequence_id": sequence_id,
                "tenant_sequence_id": tenant_sequence_id,
                "event_id": message.event_id  # Store for dedup reference
            }
            
            # Global buffer
            self._buffers[channel].append(event_entry)
            
            # Tenant-specific buffer
            self._tenant_buffers[tenant_id][channel].append(event_entry)
    
    async def replay_recent_events(
        self,
        channel: ChannelType,
        tenant_id: str,
        since_timestamp: Optional[float] = None,
        since_sequence_id: Optional[int] = None,
        limit: int = 500
    ) -> List[WebSocketMessage]:
        """
        Replay recent events for a channel/tenant.
        
        Supports replay by:
        - Timestamp (ISO string or epoch seconds)
        - Sequence ID (for exact ordering)
        
        Args:
            channel: Target channel
            tenant_id: Tenant filter
            since_timestamp: Only events after this time (epoch seconds or ISO string)
            since_sequence_id: Only events after this sequence ID
            limit: Max events to return (default 500, max 1000)
            
        Returns:
            List of WebSocketMessage objects in chronological order
        """
        # Enforce max limit
        limit = min(limit, 1000)
        
        # Parse timestamp if string
        parsed_timestamp = None
        if since_timestamp:
            if isinstance(since_timestamp, str):
                try:
                    dt = datetime.fromisoformat(since_timestamp.replace('Z', '+00:00'))
                    parsed_timestamp = dt.timestamp()
                except Exception:
                    parsed_timestamp = 0
            else:
                parsed_timestamp = float(since_timestamp)
        
        async with self._lock:
            buffer = self._tenant_buffers[tenant_id][channel]
            
            events = []
            now = time.time()
            
            # Iterate from oldest to newest (chronological order)
            for item in list(buffer):
                # Check retention (don't replay stale events)
                if (now - item["stored_timestamp"]) > self._retention_seconds:
                    continue
                
                # Check timestamp filter
                if parsed_timestamp and item["message_timestamp"] <= parsed_timestamp:
                    continue
                
                # Check sequence filter
                if since_sequence_id and item["tenant_sequence_id"] <= since_sequence_id:
                    continue
                
                events.append(item["message"])
                
                if len(events) >= limit:
                    break
            
            # Sort by sequence_id for strict monotonic ordering
            # This guarantees correct order regardless of clock skew
            events.sort(key=lambda m: m.sequence_id or 0)
            
            return events
    
    async def get_last_event_info(
        self,
        channel: ChannelType,
        tenant_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get info about the last event in buffer.
        
        Args:
            channel: Target channel
            tenant_id: Tenant filter
            
        Returns:
            Dict with timestamp and sequence_id, or None
        """
        async with self._lock:
            buffer = self._tenant_buffers[tenant_id][channel]
            
            if not buffer:
                return None
            
            last = buffer[-1]
            return {
                "timestamp": last["message_timestamp"],
                "sequence_id": last["tenant_sequence_id"],
                "message_id": last["message"].message_id
            }
    
    async def get_stats(self, tenant_id: str) -> Dict[str, Any]:
        """
        Get buffer statistics for a tenant.
        
        Args:
            tenant_id: Tenant to get stats for
            
        Returns:
            Dict with buffer sizes and event counts per channel
        """
        async with self._lock:
            stats = {}
            
            for channel in ChannelType:
                buffer = self._tenant_buffers[tenant_id][channel]
                last_info = None
                
                if buffer:
                    last = buffer[-1]
                    first = buffer[0]
                    last_info = {
                        "last_sequence": last["tenant_sequence_id"],
                        "first_sequence": first["tenant_sequence_id"],
                        "event_count": len(buffer)
                    }
                
                stats[channel.value] = {
                    "buffer_size": len(buffer),
                    "last_event": last_info
                }
            
            return stats


class WebSocketEventStreamer:
    """
    WebSocket Event Streaming Layer.
    
    Manages realtime streaming for bot monitoring and signal visualization.
    Features subscription management, tenant isolation, reconnect recovery,
    heartbeat handling, and backpressure protection.
    """
    
    def __init__(
        self,
        heartbeat_interval: float = 30.0,
        heartbeat_timeout: float = 60.0,
        max_queue_size: int = 1000,
        replay_buffer_size: int = 500,
        replay_retention_seconds: float = 300,
        # Scalability limits
        max_channels_per_client: int = 10,
        max_replay_per_minute: int = 5,
        max_connections_per_tenant: int = 100,
        rate_limit_window_seconds: float = 60.0
    ):
        self.subscriptions = SubscriptionManager()
        self.replay_buffer = EventReplayBuffer(replay_buffer_size, replay_retention_seconds)
        
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_timeout = heartbeat_timeout
        self._max_queue_size = max_queue_size
        
        # Scalability limits
        self._max_channels_per_client = max_channels_per_client
        self._max_replay_per_minute = max_replay_per_minute
        self._max_connections_per_tenant = max_connections_per_tenant
        self._rate_limit_window = rate_limit_window_seconds
        
        # Rate limiting tracking: connection_id -> { replay_count, last_replay_time }
        self._replay_rate_limits: Dict[str, Dict[str, Any]] = {}
        self._rate_limit_lock = asyncio.Lock()
        
        # Tenant connection tracking for limits
        self._tenant_connection_counts: Dict[str, int] = defaultdict(int)
        
        # Background tasks
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._rate_limit_cleanup_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Backpressure tracking
        self._dropped_messages: Dict[str, int] = defaultdict(int)
        self._slow_consumer_count: Dict[str, int] = defaultdict(int)
    
    async def start(self):
        """Start the WebSocket event streamer."""
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_stale_connections())
        self._rate_limit_cleanup_task = asyncio.create_task(self._cleanup_rate_limits())
        logger.info("WebSocket event streamer started")
    
    async def stop(self):
        """Stop the WebSocket event streamer."""
        self._running = False
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        if self._rate_limit_cleanup_task:
            self._rate_limit_cleanup_task.cancel()
            try:
                await self._rate_limit_cleanup_task
            except asyncio.CancelledError:
                pass
        
        # Close all connections
        connections = await self.subscriptions.get_all_connections()
        for conn in connections:
            await self.subscriptions.unregister_connection(conn.connection_id)
        
        logger.info("WebSocket event streamer stopped")
    
    async def handle_connection(
        self,
        websocket: Any,
        tenant_id: str,
        connection_id: Optional[str] = None
    ) -> str:
        """
        Handle a new WebSocket connection.
        
        Args:
            websocket: WebSocket object
            tenant_id: Tenant identifier for isolation
            connection_id: Optional custom connection ID
            
        Returns:
            Connection ID
        """
        conn_id = connection_id or str(uuid.uuid4())
        
        # Check tenant connection limits
        tenant_conns = await self.subscriptions.get_tenant_connections(tenant_id)
        if len(tenant_conns) >= self._max_connections_per_tenant:
            logger.error(f"Tenant {tenant_id} exceeded max connections ({self._max_connections_per_tenant})")
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": f"Maximum {self._max_connections_per_tenant} connections allowed per tenant"
            }))
            await websocket.close(1013, "Tenant connection limit exceeded")  # 1013 = Try Again Later
            return conn_id
        
        # Register connection
        client = await self.subscriptions.register_connection(
            conn_id, tenant_id, websocket
        )
        
        try:
            # Handle messages
            while client.is_connected and self._running:
                try:
                    # Receive message with timeout
                    message = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=self._heartbeat_interval
                    )
                    
                    # Handle ping/pong
                    data = json.loads(message)
                        
                    if data.get("type") == "ping":
                        client.update_ping()
                        await websocket.send_text(json.dumps({
                            "type": "pong",
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }))
                    
                    elif data.get("type") == "subscribe":
                        channel_name = data.get("channel")
                        if not is_valid_channel(channel_name):
                            logger.warning(f"Unknown websocket channel subscription: '{channel_name}'")
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "message": f"Invalid channel: '{channel_name}'. Valid channels: {', '.join(['bot_status', 'signal_trace', 'execution_events', 'risk_events', 'deployment_events'])}"
                            }))
                            continue
                        channel = ChannelType(channel_name)
                        result = await self.subscribe(conn_id, channel)
                        if result["success"]:
                            await websocket.send_text(json.dumps({
                                "type": "subscribed",
                                "channel": channel.value
                            }))
                        else:
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "message": result.get("error", "Subscription failed")
                            }))
                    
                    elif data.get("type") == "unsubscribe":
                        channel_name = data.get("channel")
                        if not is_valid_channel(channel_name):
                            continue  # Silently ignore invalid unsubscribe
                        channel = ChannelType(channel_name)
                        await self.unsubscribe(conn_id, channel)
                        await websocket.send_text(json.dumps({
                            "type": "unsubscribed",
                            "channel": channel.value
                        }))
                    
                    elif data.get("type") in ["replay", "replay_request"]:
                        since = data.get("since") or data.get("since_timestamp")
                        since_sequence_id = data.get("since_sequence_id")
                        channel_name = data.get("channel")
                        channel = None
                        if channel_name:
                            if not is_valid_channel(channel_name):
                                logger.warning(f"Unknown websocket channel in replay request: '{channel_name}'")
                                await websocket.send_text(json.dumps({
                                    "type": "error",
                                    "message": f"Invalid channel for replay: '{channel_name}'"
                                }))
                                continue
                            channel = ChannelType(channel_name)
                        # Pass both timestamp and sequence_id (sequence_id preferred for strict ordering)
                        await self._handle_replay_request(client, channel, since, since_sequence_id)
                    
                except asyncio.TimeoutError:
                    # Send heartbeat
                    await self._send_ping(websocket)
                    
                    # Check if client is stale
                    if client.is_stale(self._heartbeat_timeout):
                        logger.warning(f"Connection {conn_id} stale, closing")
                        break
                        
        except Exception as e:
            logger.error(f"Connection {conn_id} error: {e}")
        
        finally:
            await self.subscriptions.unregister_connection(conn_id)
        
        return conn_id
    
    async def subscribe(self, connection_id: str, channel: ChannelType) -> Dict[str, Any]:
        """
        Subscribe connection to a channel with limit enforcement.
        
        Returns:
            Dict with success status and optional error message
        """
        # Check max channels per client
        client = await self.subscriptions.get_connection(connection_id)
        if client:
            current_subs = len(client.subscriptions)
            if current_subs >= self._max_channels_per_client:
                logger.warning(f"Connection {connection_id} exceeded max channels ({self._max_channels_per_client})")
                return {
                    "success": False,
                    "error": f"Maximum {self._max_channels_per_client} channels allowed per client"
                }
        
        success = await self.subscriptions.subscribe(connection_id, channel)
        return {"success": success}
    
    async def unsubscribe(self, connection_id: str, channel: ChannelType) -> bool:
        """Unsubscribe connection from a channel."""
        return await self.subscriptions.unsubscribe(connection_id, channel)
    
    async def publish_event(
        self,
        event_type: EventType,
        channel: ChannelType,
        tenant_id: str,
        bot_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        payload: Dict[str, Any] = None
    ) -> WebSocketMessage:
        """
        Publish an event to a channel.
        
        Args:
            event_type: Type of event
            channel: Target channel
            tenant_id: Tenant identifier
            bot_id: Optional bot ID
            strategy_id: Optional strategy ID
            payload: Event data
            
        Returns:
            Created WebSocketMessage
        """
        message = WebSocketMessage(
            type=event_type.value,
            channel=channel.value,
            timestamp=datetime.now(timezone.utc).isoformat(),
            bot_id=bot_id,
            strategy_id=strategy_id,
            tenant_id=tenant_id,
            payload=payload or {}
        )
        
        # Store in replay buffer
        await self.replay_buffer.store_event(message)
        
        # Get subscribers
        subscribers = await self.subscriptions.get_subscribers(channel, tenant_id)
        
        # Send to subscribers
        for client in subscribers:
            try:
                # Check backpressure - slow consumer detection
                queue_size = client.message_queue.qsize()
                
                if queue_size >= self._max_queue_size:
                    # Slow consumer - queue full
                    self._dropped_messages[client.connection_id] += 1
                    self._slow_consumer_count[client.connection_id] += 1
                    
                    # Disconnect persistent slow consumers (>100 drops)
                    if self._slow_consumer_count[client.connection_id] > 100:
                        logger.error(f"Disconnecting slow consumer {client.connection_id} (excessive drops)")
                        await self.subscriptions.unregister_connection(client.connection_id)
                    else:
                        logger.warning(f"Dropping message for {client.connection_id} (backpressure: {queue_size}/{self._max_queue_size})")
                    continue
                
                # Check for approaching backpressure (80% full)
                if queue_size >= (self._max_queue_size * 0.8):
                    logger.warning(f"Client {client.connection_id} queue at {queue_size}/{self._max_queue_size}")
                
                await client.websocket.send_text(message.to_json())
            except Exception as e:
                logger.error(f"Failed to send to {client.connection_id}: {e}")
                await self.subscriptions.unregister_connection(client.connection_id)
        
        return message
    
    async def broadcast_to_tenant(
        self,
        event_type: EventType,
        tenant_id: str,
        payload: Dict[str, Any] = None
    ) -> None:
        """
        Broadcast event to all channels for a tenant.
        
        Args:
            event_type: Type of event
            tenant_id: Target tenant
            payload: Event data
        """
        for channel in ChannelType:
            await self.publish_event(
                event_type=event_type,
                channel=channel,
                tenant_id=tenant_id,
                payload=payload
            )
    
    async def replay_recent_events(
        self,
        connection_id: str,
        channel: ChannelType,
        since_timestamp: Optional[float] = None,
        since_sequence_id: Optional[int] = None
    ) -> int:
        """
        Replay recent events to a connection.
        
        Args:
            connection_id: Target connection
            channel: Channel to replay
            since_timestamp: Start from this time (fallback)
            since_sequence_id: Start from this sequence ID (preferred for strict ordering)
            
        Returns:
            Number of events replayed
        """
        client = await self.subscriptions.get_connection(connection_id)
        if not client:
            return 0
        
        # Prefer sequence-based replay for strict ordering guarantees
        events = await self.replay_buffer.replay_recent_events(
            channel, client.tenant_id, 
            since_timestamp=since_timestamp,
            since_sequence_id=since_sequence_id
        )
        
        sent = 0
        for event in events:
            try:
                await client.websocket.send_text(event.to_json())
                sent += 1
            except Exception as e:
                import traceback
                logger.error(f"Replay failed for {connection_id}: {e}\n{traceback.format_exc()}")
                break
        
        if since_sequence_id is not None:
            logger.info(f"Replayed {sent} events to {connection_id} since sequence {since_sequence_id}")
        else:
            logger.info(f"Replayed {sent} events to {connection_id}")
        return sent
    
    async def _send_ping(self, websocket: Any) -> None:
        """Send ping message."""
        try:
            await websocket.send_text(json.dumps({
                "type": "ping",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }))
        except Exception as e:
            logger.debug(f"Ping failed: {e}")
    
    async def _handle_replay_request(
        self,
        client: ClientConnection,
        channel: Optional[ChannelType],
        since_timestamp: Optional[float] = None,
        since_sequence_id: Optional[int] = None
    ) -> None:
        """
        Handle client replay request.
        
        Supports both timestamp-based and sequence-based replay.
        Sequence-based replay is preferred for strict ordering guarantees.
        """
        conn_id = client.connection_id
        
        # Rate limiting check
        async with self._rate_limit_lock:
            now = time.time()
            rate_data = self._replay_rate_limits.get(conn_id, {"count": 0, "last_replay_time": 0})
            
            # Reset if outside window
            if (now - rate_data["last_replay_time"]) > self._rate_limit_window:
                rate_data = {"count": 0, "last_replay_time": now}
            
            # Check limit
            if rate_data["count"] >= self._max_replay_per_minute:
                logger.warning(f"Rate limit exceeded for replay requests from {conn_id}")
                await client.websocket.send_text(json.dumps({
                    "type": "error",
                    "message": f"Rate limit exceeded: max {self._max_replay_per_minute} replays per {self._rate_limit_window}s"
                }))
                return
            
            # Increment counter
            rate_data["count"] += 1
            rate_data["last_replay_time"] = now
            self._replay_rate_limits[conn_id] = rate_data
        
        if channel:
            # Prefer sequence-based replay if sequence_id provided
            count = await self.replay_recent_events(
                conn_id, channel, 
                since_timestamp=since_timestamp,
                since_sequence_id=since_sequence_id
            )
            await client.websocket.send_text(json.dumps({
                "type": "replay_complete",
                "channel": channel.value,
                "events_replayed": count,
                "since_sequence_id": since_sequence_id  # Echo back for client tracking
            }))
        else:
            # Replay all subscribed channels
            total = 0
            for ch in client.subscriptions:
                count = await self.replay_recent_events(
                    conn_id, ch,
                    since_timestamp=since_timestamp,
                    since_sequence_id=since_sequence_id
                )
                total += count
            
            await client.websocket.send_text(json.dumps({
                "type": "replay_complete",
                "events_replayed": total,
                "since_sequence_id": since_sequence_id
            }))
    
    async def _heartbeat_loop(self) -> None:
        """Background heartbeat loop."""
        while self._running:
            await asyncio.sleep(self._heartbeat_interval)
            
            connections = await self.subscriptions.get_all_connections()
            for client in connections:
                try:
                    await self._send_ping(client.websocket)
                    
                    # Check staleness
                    if client.is_stale(self._heartbeat_timeout):
                        logger.warning(f"Stale connection {client.connection_id}")
                        await self.subscriptions.unregister_connection(client.connection_id)
                        
                except Exception as e:
                    logger.debug(f"Heartbeat error for {client.connection_id}: {e}")
                    await self.subscriptions.unregister_connection(client.connection_id)
    
    async def _cleanup_stale_connections(self) -> None:
        """Periodic cleanup of stale connections and rate limits."""
        while self._running:
            await asyncio.sleep(30)  # Check every 30 seconds
            try:
                removed = await self.subscriptions.cleanup_stale_connections(self._heartbeat_timeout)
                if removed > 0:
                    logger.info(f"Cleaned up {removed} stale connections")
                
                # Clean up backpressure tracking for disconnected clients
                active_connections = await self.subscriptions.get_all_connections()
                active_ids = {c.connection_id for c in active_connections}
                
                # Remove disconnected clients from dropped messages tracking
                for cid in list(self._dropped_messages.keys()):
                    if cid not in active_ids:
                        del self._dropped_messages[cid]
                
                for cid in list(self._slow_consumer_count.keys()):
                    if cid not in active_ids:
                        del self._slow_consumer_count[cid]
                        
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
    
    async def _cleanup_rate_limits(self) -> None:
        """Periodic cleanup of rate limit counters."""
        while self._running:
            await asyncio.sleep(self._rate_limit_window)
            try:
                async with self._rate_limit_lock:
                    now = time.time()
                    expired = [
                        cid for cid, data in self._replay_rate_limits.items()
                        if (now - data.get("last_replay_time", 0)) > self._rate_limit_window
                    ]
                    for cid in expired:
                        del self._replay_rate_limits[cid]
            except Exception as e:
                logger.error(f"Rate limit cleanup error: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get streaming statistics."""
        return {
            "active_connections": len(self.subscriptions._connections),
            "subscriptions_by_channel": {
                ch.value: len(subs)
                for ch, subs in self.subscriptions._channel_subscribers.items()
            },
            "dropped_messages": dict(self._dropped_messages),
            "heartbeat_interval": self._heartbeat_interval,
            "heartbeat_timeout": self._heartbeat_timeout
        }


# Helper functions for common event types

async def publish_bot_health(
    streamer: WebSocketEventStreamer,
    tenant_id: str,
    bot_id: str,
    strategy_id: str,
    health_data: Dict[str, Any]
) -> WebSocketMessage:
    """Publish bot health update."""
    return await streamer.publish_event(
        event_type=EventType.BOT_HEALTH,
        channel=ChannelType.BOT_STATUS,
        tenant_id=tenant_id,
        bot_id=bot_id,
        strategy_id=strategy_id,
        payload=health_data
    )


async def publish_signal_trace(
    streamer: WebSocketEventStreamer,
    tenant_id: str,
    bot_id: str,
    strategy_id: str,
    signal_data: Dict[str, Any]
) -> WebSocketMessage:
    """Publish signal trace update."""
    # Determine event type from signal status
    status = signal_data.get("status", "received")
    event_map = {
        "received": EventType.SIGNAL_RECEIVED,
        "validated": EventType.SIGNAL_VALIDATED,
        "risk_checked": EventType.SIGNAL_RISK_CHECKED,
        "executed": EventType.SIGNAL_EXECUTED,
        "rejected": EventType.SIGNAL_REJECTED,
        "failed": EventType.SIGNAL_FAILED
    }
    
    return await streamer.publish_event(
        event_type=event_map.get(status, EventType.SIGNAL_RECEIVED),
        channel=ChannelType.SIGNAL_TRACE,
        tenant_id=tenant_id,
        bot_id=bot_id,
        strategy_id=strategy_id,
        payload=signal_data
    )


async def publish_execution(
    streamer: WebSocketEventStreamer,
    tenant_id: str,
    bot_id: str,
    execution_data: Dict[str, Any]
) -> WebSocketMessage:
    """Publish execution event."""
    status = execution_data.get("status", "submitted")
    event_map = {
        "submitted": EventType.ORDER_SUBMITTED,
        "filled": EventType.ORDER_FILLED,
        "partial": EventType.ORDER_PARTIAL,
        "rejected": EventType.ORDER_REJECTED,
        "error": EventType.ORDER_ERROR
    }
    
    return await streamer.publish_event(
        event_type=event_map.get(status, EventType.ORDER_SUBMITTED),
        channel=ChannelType.EXECUTION_EVENTS,
        tenant_id=tenant_id,
        bot_id=bot_id,
        payload=execution_data
    )


async def publish_risk_event(
    streamer: WebSocketEventStreamer,
    tenant_id: str,
    bot_id: str,
    risk_data: Dict[str, Any]
) -> WebSocketMessage:
    """Publish risk event."""
    event_type = risk_data.get("event_type", "warning")
    event_map = {
        "block": EventType.RISK_BLOCK,
        "warning": EventType.RISK_WARNING,
        "kill_switch": EventType.KILL_SWITCH,
        "position_limit": EventType.POSITION_LIMIT,
        "drawdown": EventType.DRAWDOWN_ALERT
    }
    
    return await streamer.publish_event(
        event_type=event_map.get(event_type, EventType.RISK_WARNING),
        channel=ChannelType.RISK_EVENTS,
        tenant_id=tenant_id,
        bot_id=bot_id,
        payload=risk_data
    )


async def publish_deployment(
    streamer: WebSocketEventStreamer,
    tenant_id: str,
    strategy_id: str,
    deployment_data: Dict[str, Any]
) -> WebSocketMessage:
    """Publish deployment event."""
    status = deployment_data.get("status", "started")
    event_map = {
        "started": EventType.DEPLOY_STARTED,
        "success": EventType.DEPLOY_SUCCESS,
        "failed": EventType.DEPLOY_FAILED,
        "bot_started": EventType.BOT_STARTED,
        "bot_stopped": EventType.BOT_STOPPED
    }
    
    return await streamer.publish_event(
        event_type=event_map.get(status, EventType.DEPLOY_STARTED),
        channel=ChannelType.DEPLOYMENT_EVENTS,
        tenant_id=tenant_id,
        strategy_id=strategy_id,
        payload=deployment_data
    )


# Global streamer instance
ws_streamer = WebSocketEventStreamer()


# Exports
__all__ = [
    'WebSocketEventStreamer',
    'SubscriptionManager',
    'EventReplayBuffer',
    'WebSocketMessage',
    'ClientConnection',
    'ChannelType',
    'EventType',
    'publish_bot_health',
    'publish_signal_trace',
    'publish_execution',
    'publish_risk_event',
    'publish_deployment',
    'ws_streamer'
]
