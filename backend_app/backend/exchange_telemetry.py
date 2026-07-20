"""
Exchange Connectivity Telemetry Integration

Integrates telemetry into websocket managers, ccxt connectors,
exchange listeners, and event routers.

Author: Senior Exchange Connectivity Engineer
"""

import asyncio
import time
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Callable, Awaitable, Set
from dataclasses import dataclass, field
from enum import Enum
from collections import deque, defaultdict
import logging

logger = logging.getLogger("exchange_telemetry")


# Import telemetry systems — use fully-qualified paths so all modules
# share the SAME singleton instances as the FastAPI server.
from backend_app.backend.bot_telemetry import (
    telemetry, BotStatus, SignalStatus, RiskEventType,
    BotHealthMetrics
)
from backend_app.backend.signal_trace_engine import (
    trace_engine, TraceStatus, NodeType, ValidationResult,
    SignalTraceRecord, DAGNodeTrace, MLInferenceTrace,
    RiskValidationTrace, ExecutionTrace, NodeIO
)
from backend_app.backend.ws_event_stream import (
    ws_streamer, ChannelType, EventType,
    publish_bot_health, publish_execution, publish_risk_event
)


class ConnectionState(Enum):
    """WebSocket connection states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class FeedHealth(Enum):
    """Market data feed health."""
    HEALTHY = "healthy"
    STALE = "stale"
    LAGGING = "lagging"
    DISCONNECTED = "disconnected"


@dataclass
class LatencyMetrics:
    """Exchange latency tracking."""
    exchange: str
    symbol: str
    last_message_time: float
    avg_latency_ms: float
    max_latency_ms: float
    message_count: int = 0
    stale_count: int = 0
    
    def update(self, latency_ms: float):
        """Update latency metrics."""
        self.message_count += 1
        self.last_message_time = time.time()
        
        # Rolling average
        self.avg_latency_ms = (
            (self.avg_latency_ms * (self.message_count - 1) + latency_ms)
            / self.message_count
        )
        self.max_latency_ms = max(self.max_latency_ms, latency_ms)
    
    def is_stale(self, threshold_seconds: float = 30.0) -> bool:
        """Check if feed is stale."""
        return (time.time() - self.last_message_time) > threshold_seconds


class ExchangeTelemetryHooks:
    """
    Telemetry hooks for exchange connectivity layer.
    
    Non-blocking event emission with bounded queues.
    Resilient reconnect handling with automatic recovery.
    """
    
    def __init__(
        self,
        stale_feed_threshold: float = 30.0,  # seconds
        latency_spike_threshold: float = 500.0,  # ms
        heartbeat_timeout: float = 60.0,  # seconds
        max_queue_size: int = 10000
    ):
        self._stale_threshold = stale_feed_threshold
        self._latency_spike_threshold = latency_spike_threshold
        self._heartbeat_timeout = heartbeat_timeout
        
        # Async event queue
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        
        # Connection state tracking
        self._connection_states: Dict[str, ConnectionState] = {}
        self._latency_metrics: Dict[str, LatencyMetrics] = {}
        self._last_heartbeat: Dict[str, float] = {}
        
        # Bot mapping (exchange+symbol -> bot_ids)
        self._exchange_bots: Dict[str, Set[str]] = defaultdict(set)
        
        # Background tasks
        self._monitor_task: Optional[asyncio.Task] = None
        self._event_processor_task: Optional[asyncio.Task] = None
        self._running = False
    
    async def start(self):
        """Start telemetry hooks."""
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitoring_loop())
        self._event_processor_task = asyncio.create_task(self._event_processor())
        logger.info("Exchange telemetry hooks started")
    
    async def stop(self):
        """Stop telemetry hooks."""
        self._running = False
        
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        if self._event_processor_task:
            self._event_processor_task.cancel()
            try:
                await self._event_processor_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Exchange telemetry hooks stopped")
    
    # ========================================================================
    # Registration
    # ========================================================================
    
    def register_exchange_bot(
        self,
        bot_id: str,
        exchange: str,
        symbol: str
    ) -> None:
        """
        Register a bot for exchange telemetry.
        
        Maps bot to exchange+symbol for event routing.
        """
        key = f"{exchange}:{symbol}"
        self._exchange_bots[key].add(bot_id)
        
        # Initialize latency tracking
        if key not in self._latency_metrics:
            self._latency_metrics[key] = LatencyMetrics(
                exchange=exchange,
                symbol=symbol,
                last_message_time=time.time(),
                avg_latency_ms=0.0,
                max_latency_ms=0.0
            )
    
    def unregister_exchange_bot(
        self,
        bot_id: str,
        exchange: str,
        symbol: str
    ) -> None:
        """Unregister a bot from exchange telemetry."""
        key = f"{exchange}:{symbol}"
        self._exchange_bots[key].discard(bot_id)
        
        # Cleanup if no more bots
        if not self._exchange_bots[key]:
            del self._exchange_bots[key]
            if key in self._latency_metrics:
                del self._latency_metrics[key]
    
    def _get_bots_for_exchange(
        self,
        exchange: str,
        symbol: str
    ) -> List[str]:
        """Get all bots subscribed to an exchange+symbol."""
        key = f"{exchange}:{symbol}"
        return list(self._exchange_bots.get(key, set()))
    
    # ========================================================================
    # Non-blocking Event Queue
    # ========================================================================
    
    async def _emit_event(self, event_type: str, data: Dict[str, Any]) -> bool:
        """
        Emit event to processing queue. Non-blocking.
        
        Returns True if queued, False if dropped.
        """
        try:
            self._event_queue.put_nowait({
                "type": event_type,
                "data": data,
                "timestamp": time.time()
            })
            return True
        except asyncio.QueueFull:
            logger.warning(f"Event queue full, dropping {event_type}")
            return False
    
    async def _event_processor(self):
        """Background event processor."""
        while self._running:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=1.0
                )
                
                # Process event
                await self._process_event(event)
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Event processor error: {e}")
    
    async def _process_event(self, event: Dict[str, Any]):
        """Process a single telemetry event."""
        event_type = event["type"]
        data = event["data"]
        
        try:
            if event_type == "websocket_connect":
                await self._handle_websocket_connect(data)
            elif event_type == "websocket_disconnect":
                await self._handle_websocket_disconnect(data)
            elif event_type == "websocket_reconnect":
                await self._handle_websocket_reconnect(data)
            elif event_type == "order_execution":
                await self._handle_order_execution(data)
            elif event_type == "order_rejected":
                await self._handle_order_rejected(data)
            elif event_type == "latency_spike":
                await self._handle_latency_spike(data)
            elif event_type == "stale_feed":
                await self._handle_stale_feed(data)
            elif event_type == "heartbeat_timeout":
                await self._handle_heartbeat_timeout(data)
            elif event_type == "market_data":
                await self._handle_market_data(data)
                
        except Exception as e:
            logger.error(f"Error processing {event_type}: {e}")
    
    # ========================================================================
    # WebSocket Event Hooks
    # ========================================================================
    
    async def on_websocket_connect(
        self,
        exchange: str,
        symbol: str,
        connection_id: str
    ) -> None:
        """Hook: WebSocket connected."""
        key = f"{exchange}:{symbol}"
        self._connection_states[key] = ConnectionState.CONNECTED
        self._last_heartbeat[key] = time.time()
        
        await self._emit_event("websocket_connect", {
            "exchange": exchange,
            "symbol": symbol,
            "connection_id": connection_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
        logger.info(f"WebSocket connected: {exchange}:{symbol}")
    
    async def on_websocket_disconnect(
        self,
        exchange: str,
        symbol: str,
        reason: str = "unknown"
    ) -> None:
        """Hook: WebSocket disconnected."""
        key = f"{exchange}:{symbol}"
        self._connection_states[key] = ConnectionState.DISCONNECTED
        
        await self._emit_event("websocket_disconnect", {
            "exchange": exchange,
            "symbol": symbol,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
        logger.warning(f"WebSocket disconnected: {exchange}:{symbol} - {reason}")
    
    async def on_websocket_reconnect(
        self,
        exchange: str,
        symbol: str,
        attempt: int,
        success: bool
    ) -> None:
        """Hook: WebSocket reconnect attempt."""
        key = f"{exchange}:{symbol}"
        
        if success:
            self._connection_states[key] = ConnectionState.CONNECTED
            self._last_heartbeat[key] = time.time()
        else:
            self._connection_states[key] = ConnectionState.RECONNECTING
        
        await self._emit_event("websocket_reconnect", {
            "exchange": exchange,
            "symbol": symbol,
            "attempt": attempt,
            "success": success,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
        # Update bot telemetry for reconnect count
        bot_ids = self._get_bots_for_exchange(exchange, symbol)
        for bot_id in bot_ids:
            await telemetry.health.record_reconnect(bot_id)
            
            # Emit via websocket
            await publish_bot_health(
                ws_streamer,
                telemetry.registry._bots.get(bot_id, {}).metadata.get("tenant_id", "default"),
                bot_id,
                strategy_id=telemetry.registry._bots.get(bot_id, {}).strategy_id if bot_id in telemetry.registry._bots else None,
                health_data={
                    "event": "reconnect",
                    "exchange": exchange,
                    "symbol": symbol,
                    "attempt": attempt,
                    "websocket_connected": success
                }
            )
        
        if success:
            logger.info(f"WebSocket reconnected: {exchange}:{symbol} (attempt {attempt})")
        else:
            logger.warning(f"WebSocket reconnect failed: {exchange}:{symbol} (attempt {attempt})")
    
    async def on_heartbeat_received(
        self,
        exchange: str,
        symbol: str
    ) -> None:
        """Hook: Heartbeat received."""
        key = f"{exchange}:{symbol}"
        self._last_heartbeat[key] = time.time()
    
    # ========================================================================
    # Market Data Hooks
    # ========================================================================
    
    async def on_market_data_received(
        self,
        exchange: str,
        symbol: str,
        data_type: str,  # 'ticker', 'orderbook', 'trade'
        latency_ms: float,
        payload: Dict[str, Any]
    ) -> None:
        """Hook: Market data received."""
        key = f"{exchange}:{symbol}"
        
        # Update latency metrics
        if key in self._latency_metrics:
            self._latency_metrics[key].update(latency_ms)
        
        # Check for latency spike
        if latency_ms > self._latency_spike_threshold:
            await self._emit_event("latency_spike", {
                "exchange": exchange,
                "symbol": symbol,
                "data_type": data_type,
                "latency_ms": latency_ms,
                "threshold_ms": self._latency_spike_threshold,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
        
        # Queue for processing
        await self._emit_event("market_data", {
            "exchange": exchange,
            "symbol": symbol,
            "data_type": data_type,
            "latency_ms": latency_ms,
            "payload_size": len(str(payload)),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    
    # ========================================================================
    # Execution Hooks
    # ========================================================================
    
    async def on_order_execution(
        self,
        exchange: str,
        symbol: str,
        order_id: str,
        bot_id: str,
        signal_id: str,
        side: str,
        size: Decimal,
        price: Decimal,
        filled_size: Decimal,
        fees: Decimal,
        slippage: Decimal,
        latency_ms: float,
        tenant_id: str
    ) -> None:
        """Hook: Order executed successfully."""
        # Record in telemetry
        await telemetry.executions.record_execution_event(
            bot_id=bot_id,
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            size=size,
            price=price,
            filled_amount=filled_size,
            fees=fees,
            slippage=slippage,
            latency_ms=latency_ms,
            success=True
        )
        
        # Update bot health
        await telemetry.health.record_execution(bot_id, latency_ms, success=True)
        
        # Emit to websocket
        await publish_execution(
            ws_streamer,
            tenant_id,
            bot_id,
            execution_data={
                "status": "filled",
                "order_id": order_id,
                "signal_id": signal_id,
                "symbol": symbol,
                "side": side,
                "size": str(size),
                "price": str(price),
                "filled_size": str(filled_size),
                "fees": str(fees),
                "slippage": str(slippage),
                "latency_ms": latency_ms
            }
        )
        
        logger.info(f"Order executed: {order_id} on {exchange}:{symbol}")
    
    async def on_order_rejected(
        self,
        exchange: str,
        symbol: str,
        order_id: str,
        bot_id: str,
        signal_id: str,
        reason: str,
        error_code: str,
        tenant_id: str
    ) -> None:
        """Hook: Order rejected by exchange."""
        # Record failed execution
        try:
            await telemetry.executions.record_execution_event(
                bot_id=bot_id,
                signal_id=signal_id,
                symbol=symbol,
                side="unknown",
                size=Decimal("0"),
                price=Decimal("0"),
                filled_amount=Decimal("0"),
                fees=Decimal("0"),
                slippage=Decimal("0"),
                latency_ms=0.0,
                success=False,
                error=f"[{error_code}] {reason}"
            )
        except Exception as e:
            logger.error(f"RECORD_EXECUTION_EVENT_FAILED: {e}")
        
        # Update bot health
        await telemetry.health.update_bot_status(
            bot_id=bot_id,
            status=BotStatus.RUNNING,  # Still running, just one error
            error_increment=True
        )
        
        # Emit to websocket
        await publish_execution(
            ws_streamer,
            tenant_id,
            bot_id,
            execution_data={
                "status": "rejected",
                "order_id": order_id,
                "signal_id": signal_id,
                "symbol": symbol,
                "reason": reason,
                "error_code": error_code
            }
        )
        
        # Also emit as risk event
        await publish_risk_event(
            ws_streamer,
            tenant_id,
            bot_id,
            risk_data={
                "event_type": "order_reject",
                "severity": "high" if error_code in ["INSUFFICIENT_FUNDS", "POSITION_LIMIT"] else "medium",
                "description": f"Order rejected: {reason}",
                "signal_id": signal_id,
                "exchange": exchange
            }
        )
        
        logger.error(f"Order rejected: {order_id} on {exchange}:{symbol} - {reason}")
    
    # ========================================================================
    # Internal Event Handlers
    # ========================================================================
    
    async def _handle_websocket_connect(self, data: Dict[str, Any]):
        """Handle websocket connect event."""
        exchange = data["exchange"]
        symbol = data["symbol"]
        
        # Update bot telemetry
        bot_ids = self._get_bots_for_exchange(exchange, symbol)
        for bot_id in bot_ids:
            await telemetry.health.update_bot_status(
                bot_id=bot_id,
                status=BotStatus.RUNNING,
                websocket_connected=True
            )
    
    async def _handle_websocket_disconnect(self, data: Dict[str, Any]):
        """Handle websocket disconnect event."""
        exchange = data["exchange"]
        symbol = data["symbol"]
        reason = data.get("reason", "unknown")
        
        bot_ids = self._get_bots_for_exchange(exchange, symbol)
        for bot_id in bot_ids:
            # Check if bot has other active connections
            # For now, mark as reconnecting
            await telemetry.health.update_bot_status(
                bot_id=bot_id,
                status=BotStatus.RECONNECTING,
                websocket_connected=False
            )
    
    async def _handle_websocket_reconnect(self, data: Dict[str, Any]):
        """Handle websocket reconnect event."""
        exchange = data["exchange"]
        symbol = data["symbol"]
        success = data["success"]
        
        bot_ids = self._get_bots_for_exchange(exchange, symbol)
        for bot_id in bot_ids:
            if success:
                await telemetry.health.update_bot_status(
                    bot_id=bot_id,
                    status=BotStatus.RUNNING,
                    websocket_connected=True
                )
            else:
                await telemetry.health.update_bot_status(
                    bot_id=bot_id,
                    status=BotStatus.ERROR,
                    websocket_connected=False,
                    error_increment=True
                )
    
    async def _handle_order_execution(self, data: Dict[str, Any]):
        """Order execution already handled in hook."""
        pass  # Direct hook is faster
    
    async def _handle_order_rejected(self, data: Dict[str, Any]):
        """Order rejection already handled in hook."""
        pass  # Direct hook is faster
    
    async def _handle_latency_spike(self, data: Dict[str, Any]):
        """Handle latency spike detection."""
        exchange = data["exchange"]
        symbol = data["symbol"]
        latency_ms = data["latency_ms"]
        
        logger.warning(f"Latency spike: {exchange}:{symbol} - {latency_ms:.1f}ms")
        
        # This is informational - no immediate action needed
        # Could trigger alerts in future
    
    async def _handle_stale_feed(self, data: Dict[str, Any]):
        """Handle stale feed detection."""
        exchange = data["exchange"]
        symbol = data["symbol"]
        last_update_seconds = data["last_update_seconds"]
        
        bot_ids = self._get_bots_for_exchange(exchange, symbol)
        for bot_id in bot_ids:
            # Get bot's tenant for websocket
            bot = await telemetry.registry.get_bot(bot_id)
            if bot:
                await publish_bot_health(
                    ws_streamer,
                    bot.metadata.get("tenant_id", "default"),
                    bot_id,
                    strategy_id=bot.strategy_id,
                    health_data={
                        "event": "stale_feed",
                        "exchange": exchange,
                        "symbol": symbol,
                        "last_update_seconds": last_update_seconds,
                        "websocket_connected": True  # Connected but stale
                    }
                )
    
    async def _handle_heartbeat_timeout(self, data: Dict[str, Any]):
        """Handle heartbeat timeout."""
        exchange = data["exchange"]
        symbol = data["symbol"]
        timeout_seconds = data["timeout_seconds"]
        
        logger.error(f"Heartbeat timeout: {exchange}:{symbol} after {timeout_seconds}s")
        
        bot_ids = self._get_bots_for_exchange(exchange, symbol)
        for bot_id in bot_ids:
            await telemetry.health.update_bot_status(
                bot_id=bot_id,
                status=BotStatus.ERROR,
                websocket_connected=False,
                error_increment=True
            )
            
            # Emit critical alert
            bot = await telemetry.registry.get_bot(bot_id)
            if bot:
                await publish_risk_event(
                    ws_streamer,
                    bot.metadata.get("tenant_id", "default"),
                    bot_id,
                    risk_data={
                        "event_type": "kill_switch",
                        "severity": "critical",
                        "description": f"Heartbeat timeout on {exchange}:{symbol}",
                        "exchange": exchange
                    }
                )
    
    async def _handle_market_data(self, data: Dict[str, Any]):
        """Handle market data event (update heartbeat)."""
        exchange = data["exchange"]
        symbol = data["symbol"]
        
        key = f"{exchange}:{symbol}"
        self._last_heartbeat[key] = time.time()
    
    # ========================================================================
    # Monitoring Loop
    # ========================================================================
    
    async def _monitoring_loop(self):
        """Background monitoring for stale feeds and heartbeats."""
        while self._running:
            await asyncio.sleep(5)  # Check every 5 seconds
            
            now = time.time()
            
            # Check for stale feeds
            for key, metrics in list(self._latency_metrics.items()):
                if metrics.is_stale(self._stale_threshold):
                    await self._emit_event("stale_feed", {
                        "exchange": metrics.exchange,
                        "symbol": metrics.symbol,
                        "last_update_seconds": now - metrics.last_message_time,
                        "threshold_seconds": self._stale_threshold,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
            
            # Check for heartbeat timeouts
            for key, last_hb in list(self._last_heartbeat.items()):
                if (now - last_hb) > self._heartbeat_timeout:
                    exchange, symbol = key.split(":")
                    await self._emit_event("heartbeat_timeout", {
                        "exchange": exchange,
                        "symbol": symbol,
                        "last_heartbeat_seconds": now - last_hb,
                        "timeout_seconds": self._heartbeat_timeout,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })


# Global instance
exchange_telemetry = ExchangeTelemetryHooks()


# Convenience wrapper functions for CCXT/WebSocket managers

async def emit_websocket_connect(
    exchange: str,
    symbol: str,
    connection_id: str
) -> None:
    """Emit websocket connect event."""
    await exchange_telemetry.on_websocket_connect(exchange, symbol, connection_id)


async def emit_websocket_disconnect(
    exchange: str,
    symbol: str,
    reason: str = "unknown"
) -> None:
    """Emit websocket disconnect event."""
    await exchange_telemetry.on_websocket_disconnect(exchange, symbol, reason)


async def emit_websocket_reconnect(
    exchange: str,
    symbol: str,
    attempt: int,
    success: bool
) -> None:
    """Emit websocket reconnect event."""
    await exchange_telemetry.on_websocket_reconnect(exchange, symbol, attempt, success)


async def emit_market_data(
    exchange: str,
    symbol: str,
    data_type: str,
    latency_ms: float,
    payload: Dict[str, Any]
) -> None:
    """Emit market data received event."""
    await exchange_telemetry.on_market_data_received(
        exchange, symbol, data_type, latency_ms, payload
    )


async def emit_order_execution(
    exchange: str,
    symbol: str,
    order_id: str,
    bot_id: str,
    signal_id: str,
    side: str,
    size: Decimal,
    price: Decimal,
    filled_size: Decimal,
    fees: Decimal,
    slippage: Decimal,
    latency_ms: float,
    tenant_id: str
) -> None:
    """Emit order execution event."""
    await exchange_telemetry.on_order_execution(
        exchange, symbol, order_id, bot_id, signal_id,
        side, size, price, filled_size, fees, slippage,
        latency_ms, tenant_id
    )


async def emit_order_rejected(
    exchange: str,
    symbol: str,
    order_id: str,
    bot_id: str,
    signal_id: str,
    reason: str,
    error_code: str,
    tenant_id: str
) -> None:
    """Emit order rejected event."""
    await exchange_telemetry.on_order_rejected(
        exchange, symbol, order_id, bot_id, signal_id,
        reason, error_code, tenant_id
    )


async def register_bot_for_exchange(
    bot_id: str,
    exchange: str,
    symbol: str
) -> None:
    """Register bot for exchange telemetry."""
    exchange_telemetry.register_exchange_bot(bot_id, exchange, symbol)


async def unregister_bot_from_exchange(
    bot_id: str,
    exchange: str,
    symbol: str
) -> None:
    """Unregister bot from exchange telemetry."""
    exchange_telemetry.unregister_exchange_bot(bot_id, exchange, symbol)


# Exports
__all__ = [
    'ExchangeTelemetryHooks',
    'exchange_telemetry',
    'ConnectionState',
    'FeedHealth',
    'LatencyMetrics',
    # Event emitters
    'emit_websocket_connect',
    'emit_websocket_disconnect',
    'emit_websocket_reconnect',
    'emit_market_data',
    'emit_order_execution',
    'emit_order_rejected',
    # Registration
    'register_bot_for_exchange',
    'unregister_bot_from_exchange'
]
