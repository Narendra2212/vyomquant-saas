"""
Real-Time Event Sync System

STEP 5 — REAL-TIME EVENT SYNC SYSTEM

Ensures system state updates instantly from exchange events.

Event Flow:
┌─────────────────────────────────────────────────────────────────┐
│  Exchange WebSocket                                               │
│       ↓                                                          │
│  Event Listener (WebSocket client)                                │
│       ↓                                                          │
│  Event Queue (ordered by timestamp/sequence)                   │
│       ↓                                                          │
│  Event Router (routes by type)                                  │
│       ↓                                                          │
│  Event Handlers:                                                  │
│   ├─ OrderHandler → update execution_record                     │
│   ├─ PositionHandler → update position                           │
│   └─ PnLHandler → update PnL                                     │
│       ↓                                                          │
│  WebSocket Push → Frontend                                        │
│       ↓                                                          │
│  Latency Log                                                      │
└─────────────────────────────────────────────────────────────────┘

STEP 5.9: Idempotent processing - same event twice = no issue
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum, auto
from typing import Dict, List, Optional, Any, Callable, Set
from uuid import UUID
import time

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5.2 — EVENT TYPES
# ═══════════════════════════════════════════════════════════════════════════════

class EventType(Enum):
    """
    STEP 5.2: Exchange event types.
    
    These events come from exchange WebSocket streams.
    """
    # Order events
    ORDER_FILLED = "order_filled"           # Order completely filled
    ORDER_PARTIAL = "order_partial"         # Partial fill
    ORDER_CANCELLED = "order_cancelled"     # Order cancelled
    ORDER_REJECTED = "order_rejected"       # Order rejected
    ORDER_UPDATED = "order_updated"         # Generic order update
    
    # Position events
    POSITION_UPDATED = "position_updated"     # Position size/price changed
    POSITION_LIQUIDATED = "position_liquidated"  # Position liquidated
    
    # Trade events
    TRADE_EXECUTED = "trade_executed"        # New trade executed
    
    # Market events
    TICK_PRICE = "tick_price"               # Price tick
    ORDER_BOOK_UPDATE = "order_book_update" # Order book change
    
    # System events
    CONNECTION_ESTABLISHED = "connection_established"
    CONNECTION_LOST = "connection_lost"
    RECONNECTED = "reconnected"


class EventPriority(Enum):
    """Event processing priority."""
    CRITICAL = 0    # Fills, liquidations
    HIGH = 1      # Order updates
    NORMAL = 2    # Position updates
    LOW = 3       # Price ticks


@dataclass
class ExchangeEvent:
    """
    STEP 5.2: Standardized exchange event.
    
    All exchange events are normalized to this format.
    """
    # Identifiers
    event_id: str                           # Unique event ID
    event_type: EventType                   # Type of event
    
    # Source
    exchange_id: str                        # Exchange (binance, coinbase, etc.)
    tenant_id: str                        # Tenant UUID
    
    # Related entities
    order_id: Optional[str] = None        # Exchange order ID
    execution_id: Optional[str] = None    # Our execution ID
    symbol: Optional[str] = None          # Trading symbol
    
    # Event data
    data: Dict[str, Any] = field(default_factory=dict)
    
    # Timing (STEP 5.7: Latency tracking)
    exchange_timestamp: Optional[datetime] = None   # When exchange sent
    received_at: datetime = field(default_factory=datetime.utcnow)  # When we received
    processed_at: Optional[datetime] = None         # When processed
    
    # Sequencing (STEP 5.8: Order guarantee)
    sequence_id: Optional[int] = None     # Exchange sequence number
    
    # Idempotency (STEP 5.9)
    processed: bool = False
    
    @property
    def latency_ms(self) -> float:
        """STEP 5.7: Calculate event latency in milliseconds."""
        if self.exchange_timestamp:
            delta = self.received_at - self.exchange_timestamp
            return delta.total_seconds() * 1000
        return 0.0
    
    @property
    def processing_time_ms(self) -> float:
        """Calculate processing time."""
        if self.processed_at:
            delta = self.processed_at - self.received_at
            return delta.total_seconds() * 1000
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5.1 — EVENT INGESTION LAYER
# ═══════════════════════════════════════════════════════════════════════════════

class ExchangeWebSocketClient(ABC):
    """
    Abstract base class for exchange WebSocket clients.
    
    STEP 5.1: Listen to WebSocket events from exchanges.
    """
    
    def __init__(self, exchange_id: str, tenant_id: str):
        self.exchange_id = exchange_id
        self.tenant_id = tenant_id
        self._connected = False
        self._event_callbacks: List[Callable[[ExchangeEvent], None]] = []
        self._last_sequence_id: int = 0
    
    @abstractmethod
    async def connect(self):
        """Connect to exchange WebSocket."""
        pass
    
    @abstractmethod
    async def disconnect(self):
        """Disconnect from exchange."""
        pass
    
    @abstractmethod
    async def subscribe_orders(self, symbols: List[str]):
        """Subscribe to order updates."""
        pass
    
    @abstractmethod
    async def subscribe_trades(self, symbols: List[str]):
        """Subscribe to trade stream."""
        pass
    
    def on_event(self, callback: Callable[[ExchangeEvent], None]):
        """Register event callback."""
        self._event_callbacks.append(callback)
    
    def _emit_event(self, event: ExchangeEvent):
        """Emit event to all registered callbacks."""
        for callback in self._event_callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Event callback error: {e}")
    
    def _normalize_event(self, raw_event: Dict[str, Any]) -> ExchangeEvent:
        """
        Normalize raw exchange event to standard format.
        
        Override in subclass for exchange-specific format.
        """
        return ExchangeEvent(
            event_id=f"evt_{raw_event.get('id', time.time())}",
            event_type=EventType.ORDER_UPDATED,
            exchange_id=self.exchange_id,
            tenant_id=self.tenant_id,
            order_id=raw_event.get('order_id'),
            symbol=raw_event.get('symbol'),
            data=raw_event,
            exchange_timestamp=datetime.fromtimestamp(
                raw_event.get('timestamp', time.time()) / 1000
            ) if 'timestamp' in raw_event else None
        )


class EventListener:
    """
    STEP 5.1: Main event ingestion layer.
    
    Manages multiple exchange WebSocket connections,
    normalizes events, and queues for processing.
    
    Features:
    - Multi-exchange support
    - Automatic reconnection
    - Event deduplication
    - Latency tracking
    """
    
    def __init__(self):
        self._clients: Dict[str, ExchangeWebSocketClient] = {}
        self._event_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._processed_event_ids: Set[str] = set()
        self._running = False
        self._listener_task: Optional[asyncio.Task] = None
        self._event_router: Optional['EventRouter'] = None
        
        logger.info("EventListener initialized")
    
    def register_client(self, client: ExchangeWebSocketClient):
        """Register an exchange WebSocket client."""
        key = f"{client.exchange_id}:{client.tenant_id}"
        self._clients[key] = client
        
        # Register callback
        client.on_event(self._on_exchange_event)
        
        logger.info(f"Registered client: {key}")
    
    def _on_exchange_event(self, event: ExchangeEvent):
        """
        STEP 5.9: Receive event from exchange.
        
        Deduplication check happens here.
        """
        # STEP 5.9: Idempotency - check if already processed
        if event.event_id in self._processed_event_ids:
            logger.debug(f"Duplicate event dropped: {event.event_id}")
            return
        
        # STEP 5.7: Latency tracking - log slow events
        latency_ms = event.latency_ms
        if latency_ms > 1000:  # More than 1 second
            logger.warning(
                f"SLOW EVENT: {event.event_id} | "
                f"type={event.event_type.value} | "
                f"latency={latency_ms:.2f}ms"
            )
        
        # Add to processed set (with size limit to prevent memory bloat)
        self._processed_event_ids.add(event.event_id)
        if len(self._processed_event_ids) > 100000:
            # Clear oldest 50% (simple approach)
            self._processed_event_ids = set(list(self._processed_event_ids)[50000:])
        
        # Queue event with priority
        priority = self._get_event_priority(event.event_type)
        
        # STEP 5.8: Order guarantee - include sequence/timestamp in queue key
        sequence_key = event.sequence_id or int(event.received_at.timestamp() * 1000000)
        
        # (priority, sequence_key, event)
        asyncio.create_task(self._event_queue.put((priority, sequence_key, event)))
        
        logger.debug(
            f"Event queued: {event.event_id} | "
            f"type={event.event_type.value} | "
            f"priority={priority}"
        )
    
    def _get_event_priority(self, event_type: EventType) -> int:
        """Get processing priority for event type."""
        priority_map = {
            EventType.ORDER_FILLED: EventPriority.CRITICAL.value,
            EventType.ORDER_PARTIAL: EventPriority.CRITICAL.value,
            EventType.POSITION_LIQUIDATED: EventPriority.CRITICAL.value,
            EventType.ORDER_CANCELLED: EventPriority.HIGH.value,
            EventType.ORDER_REJECTED: EventPriority.HIGH.value,
            EventType.POSITION_UPDATED: EventPriority.NORMAL.value,
            EventType.TICK_PRICE: EventPriority.LOW.value,
        }
        return priority_map.get(event_type, EventPriority.NORMAL.value)
    
    async def start(self, event_router: 'EventRouter'):
        """Start event listener and processing loop."""
        self._running = True
        self._event_router = event_router
        
        # Connect all clients
        for key, client in self._clients.items():
            try:
                await client.connect()
                logger.info(f"Connected: {key}")
            except Exception as e:
                logger.error(f"Failed to connect {key}: {e}")
        
        # Start event processing loop
        self._listener_task = asyncio.create_task(self._event_processing_loop())
        
        logger.info("EventListener started")
    
    async def stop(self):
        """Stop event listener."""
        self._running = False
        
        # Disconnect all clients
        for key, client in self._clients.items():
            try:
                await client.disconnect()
                logger.info(f"Disconnected: {key}")
            except Exception as e:
                logger.error(f"Error disconnecting {key}: {e}")
        
        # Cancel processing loop
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        
        logger.info("EventListener stopped")
    
    async def _event_processing_loop(self):
        """STEP 5.1: Main event processing loop."""
        while self._running:
            try:
                # Get event from queue (with timeout for health checks)
                priority, sequence_key, event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=1.0
                )
                
                # STEP 5.8: Order guarantee - events processed in sequence order
                # STEP 5.3: Route event to appropriate handler
                if self._event_router:
                    await self._event_router.route_event(event)
                
                # Mark as processed
                event.processed_at = datetime.utcnow()
                
                # STEP 5.7: Log processing time
                processing_time = event.processing_time_ms
                if processing_time > 100:  # More than 100ms
                    logger.warning(
                        f"SLOW PROCESSING: {event.event_id} | "
                        f"time={processing_time:.2f}ms"
                    )
                
            except asyncio.TimeoutError:
                # No events, continue loop
                continue
            except Exception as e:
                logger.error(f"Event processing error: {e}", exc_info=True)
    
    async def health_check(self) -> Dict[str, Any]:
        """Get health status of event listener."""
        return {
            "running": self._running,
            "clients": len(self._clients),
            "queue_size": self._event_queue.qsize(),
            "processed_events": len(self._processed_event_ids),
            "connected": [
                key for key, client in self._clients.items()
                if getattr(client, '_connected', False)
            ]
        }


# ═══════════════════════════════════════════════════════════════════════════════
# EXAMPLE EXCHANGE CLIENT (Binance-style)
# ═══════════════════════════════════════════════════════════════════════════════

class BinanceWebSocketClient(ExchangeWebSocketClient):
    """
    Production-grade Binance WebSocket client.
    
    Features:
    - Heartbeat/ping-pong for connection health
    - Timeout wrapping on all operations
    - Auto-reconnect with exponential backoff
    - Message validation
    """
    
    # Heartbeat configuration
    HEARTBEAT_INTERVAL = 10.0  # Send ping every 10 seconds
    PONG_TIMEOUT = 5.0  # Expect pong within 5 seconds
    
    # Timeout configuration
    RECV_TIMEOUT = 30.0  # 30 second timeout on recv()
    CONNECT_TIMEOUT = 10.0  # 10 second connect timeout
    
    # Reconnect configuration
    MAX_RECONNECT_DELAY = 30.0  # Cap at 30 seconds
    RECONNECT_MAX_RETRIES = None  # Infinite retries (None = forever)
    
    def __init__(self, tenant_id: str, api_key: Optional[str] = None):
        super().__init__("binance", tenant_id)
        self.api_key = api_key
        self._ws = None
        
        # Heartbeat state
        self._last_pong_time: Optional[float] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        
        # Reconnect state
        self._reconnect_count: int = 0
        self._reconnect_task: Optional[asyncio.Task] = None
        self._should_reconnect: bool = True
        
        # Message validation state
        self._messages_received: int = 0
        self._messages_invalid: int = 0
    
    async def connect(self):
        """Connect to Binance WebSocket with timeout."""
        import aiohttp
        
        url = f"wss://stream.binance.com:9443/ws/order@{self.tenant_id}"
        
        try:
            self._session = aiohttp.ClientSession()
            self._ws = await asyncio.wait_for(
                self._session.ws_connect(url),
                timeout=self.CONNECT_TIMEOUT
            )
            self._connected = True
            self._last_pong_time = time.time()
            self._reconnect_count = 0
            
            # Start heartbeat
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            
            # Start message handler
            asyncio.create_task(self._message_handler())
            
            logger.info(f"✅ WebSocket connected: {self.exchange_id} | {self.tenant_id}")
            
        except asyncio.TimeoutError:
            logger.error(f"🚫 WebSocket connect timeout: {url}")
            raise
        except Exception as e:
            logger.error(f"🚫 WebSocket connect failed: {e}")
            raise
    
    async def disconnect(self):
        """Disconnect and stop reconnection."""
        self._should_reconnect = False
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        
        if self._ws:
            await self._ws.close()
        if hasattr(self, '_session'):
            await self._session.close()
        
        self._connected = False
        logger.info(f"🔌 WebSocket disconnected: {self.exchange_id} | {self.tenant_id}")
    
    async def subscribe_orders(self, symbols: List[str]):
        """Subscribe to order updates."""
        if self._ws and self._connected:
            await self._ws.send_json({
                "method": "SUBSCRIBE",
                "params": [f"{s.lower()}@executionReport" for s in symbols],
                "id": 1
            })
    
    async def _heartbeat_loop(self):
        """Send ping every HEARTBEAT_INTERVAL, check for pong."""
        while self._connected and self._ws:
            try:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                
                if not self._connected:
                    break
                
                # Send ping
                await self._ws.ping()
                
                # Check if we received pong recently
                if self._last_pong_time:
                    time_since_pong = time.time() - self._last_pong_time
                    if time_since_pong > self.PONG_TIMEOUT + self.HEARTBEAT_INTERVAL:
                        logger.warning(
                            f"⚠️ No pong received for {time_since_pong:.1f}s, "
                            f"triggering reconnect"
                        )
                        asyncio.create_task(self._trigger_reconnect())
                        break
                        
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                break
    
    async def _trigger_reconnect(self):
        """Trigger reconnection with exponential backoff."""
        if not self._should_reconnect:
            return
        
        # Calculate delay with exponential backoff
        delay = min(2 ** self._reconnect_count, self.MAX_RECONNECT_DELAY)
        self._reconnect_count += 1
        
        logger.info(
            f"🔄 Reconnecting in {delay}s "
            f"(attempt #{self._reconnect_count})"
        )
        
        await asyncio.sleep(delay)
        
        try:
            # Clean up old connection
            if self._ws:
                await self._ws.close()
            if hasattr(self, '_session'):
                await self._session.close()
            
            # Reconnect
            await self.connect()
            
        except Exception as e:
            logger.error(f"Reconnect failed: {e}")
            # Schedule another reconnect
            if self._should_reconnect:
                asyncio.create_task(self._trigger_reconnect())
    
    async def _message_handler(self):
        """Handle incoming WebSocket messages with timeout and validation."""
        import aiohttp
        
        while self._connected and self._ws:
            try:
                # ⏱️ TIMEOUT WRAP: 30 second timeout on recv()
                msg = await asyncio.wait_for(
                    self._ws.receive(),
                    timeout=self.RECV_TIMEOUT
                )
                
                # Handle pong
                if msg.type == aiohttp.WSMsgType.PONG:
                    self._last_pong_time = time.time()
                    continue
                
                # Handle ping
                if msg.type == aiohttp.WSMsgType.PING:
                    await self._ws.pong()
                    continue
                
                # Handle close
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                    logger.warning(f"WebSocket closed: {self.exchange_id}")
                    if self._should_reconnect:
                        asyncio.create_task(self._trigger_reconnect())
                    break
                
                # Handle error
                if msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {msg.data}")
                    if self._should_reconnect:
                        asyncio.create_task(self._trigger_reconnect())
                    break
                
                # 📋 MESSAGE VALIDATION
                if msg.type != aiohttp.WSMsgType.TEXT:
                    self._messages_invalid += 1
                    logger.warning(f"Invalid message type: {msg.type}")
                    continue
                
                # Validate: empty messages
                if not msg.data or msg.data.strip() == '':
                    self._messages_invalid += 1
                    logger.warning("Empty message received")
                    continue
                
                # Validate: JSON parse
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError as e:
                    self._messages_invalid += 1
                    logger.warning(f"Invalid JSON: {e} | Data: {msg.data[:100]}")
                    continue
                
                # Validate: required fields
                if not isinstance(data, dict):
                    self._messages_invalid += 1
                    logger.warning(f"Message not a dict: {type(data)}")
                    continue
                
                # Count valid message
                self._messages_received += 1
                
                # Normalize and emit
                event = self._normalize_event(data)
                self._emit_event(event)
                
            except asyncio.TimeoutError:
                logger.warning(f"⏱️ Receive timeout ({self.RECV_TIMEOUT}s), reconnecting...")
                if self._should_reconnect:
                    asyncio.create_task(self._trigger_reconnect())
                break
                
            except Exception as e:
                logger.error(f"Message handler error: {e}", exc_info=True)
                if self._should_reconnect:
                    asyncio.create_task(self._trigger_reconnect())
                break
    
    def get_connection_stats(self) -> Dict[str, Any]:
        """Get connection statistics."""
        return {
            "connected": self._connected,
            "reconnect_count": self._reconnect_count,
            "messages_received": self._messages_received,
            "messages_invalid": self._messages_invalid,
            "last_pong_age": (
                time.time() - self._last_pong_time 
                if self._last_pong_time else None
            ),
            "heartbeat_interval": self.HEARTBEAT_INTERVAL,
            "recv_timeout": self.RECV_TIMEOUT
        }
    
    def _normalize_event(self, raw: Dict[str, Any]) -> ExchangeEvent:
        """Normalize Binance execution report to standard format."""
        # Map Binance event type
        event_type_map = {
            "ORDER_FILLED": EventType.ORDER_FILLED,
            "PARTIALLY_FILLED": EventType.ORDER_PARTIAL,
            "CANCELED": EventType.ORDER_CANCELLED,
            "REJECTED": EventType.ORDER_REJECTED,
        }
        
        binance_type = raw.get("executionType", "")
        event_type = event_type_map.get(binance_type, EventType.ORDER_UPDATED)
        
        return ExchangeEvent(
            event_id=f"binance_{raw.get('i', time.time())}",
            event_type=event_type,
            exchange_id=self.exchange_id,
            tenant_id=self.tenant_id,
            order_id=str(raw.get("i")),  # orderId
            symbol=raw.get("s"),  # symbol
            data=raw,
            exchange_timestamp=datetime.fromtimestamp(raw.get("T", 0) / 1000),
            sequence_id=raw.get("u")  # updateId
        )


# Global instance
_event_listener: Optional[EventListener] = None


def get_event_listener() -> EventListener:
    """Get or create global event listener."""
    global _event_listener
    if _event_listener is None:
        _event_listener = EventListener()
    return _event_listener
