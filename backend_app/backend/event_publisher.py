"""
backend/event_publisher.py — REDIS PUB/UB EVENT PUBLISHER

STEP 8.11+ — SCALING FOR 500 USERS

Publishes events to Redis Pub/Sub for the WebSocket server to broadcast.

USAGE:
    from backend_app.backend.event_publisher import EventPublisher
    
    publisher = EventPublisher()
    await publisher.publish_order_filled(tenant_id, order_data)
    await publisher.publish_position_update(tenant_id, position_data)
    await publisher.publish_signal(tenant_id, signal_data)
"""

import asyncio
import json
import logging
import os
from typing import Dict, Any, Optional
from datetime import datetime

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Redis configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Channel names
CHANNEL_ORDERS = "orders"
CHANNEL_POSITIONS = "positions"
CHANNEL_PNL = "pnl"
CHANNEL_PORTFOLIO = "portfolio"
CHANNEL_SIGNALS = "signals"
CHANNEL_MARKET_DATA = "market_data"
CHANNEL_ALERTS = "alerts"
CHANNEL_CIRCUIT_BREAKER = "circuit_breaker"


class EventPublisher:
    """
    Publishes events to Redis Pub/Sub for WebSocket broadcasting.
    
    This allows the backend to fire-and-forget events, while the
    WebSocket server handles the actual client broadcasting.
    """
    
    _instance: Optional['EventPublisher'] = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._redis: Optional[aioredis.Redis] = None
        self._initialized = True
    
    async def connect(self):
        """Connect to Redis."""
        if self._redis is None:
            try:
                self._redis = await aioredis.from_url(
                    REDIS_URL,
                    encoding="utf-8",
                    decode_responses=True
                )
                logger.info("[EventPublisher] Redis connected")
            except Exception as e:
                logger.error(f"[EventPublisher] Redis connection failed: {e}")
                raise
    
    async def disconnect(self):
        """Disconnect from Redis."""
        if self._redis:
            await self._redis.close()
            self._redis = None
            logger.info("[EventPublisher] Redis disconnected")
    
    async def _publish(self, channel: str, data: Dict[str, Any]):
        """Publish data to a Redis channel."""
        if self._redis is None:
            await self.connect()
        
        try:
            message = json.dumps(data)
            await self._redis.publish(channel, message)
            logger.debug(f"[EventPublisher] Published to {channel}")
        except Exception as e:
            logger.error(f"[EventPublisher] Failed to publish to {channel}: {e}")
    
    # =============================================================================
    # ORDER EVENTS
    # =============================================================================
    
    async def publish_order_filled(
        self,
        tenant_id: str,
        order_id: str,
        symbol: str,
        side: str,
        filled_qty: float,
        filled_price: float,
        pnl: Optional[float] = None,
        **kwargs
    ):
        """Publish order filled event."""
        await self._publish(CHANNEL_ORDERS, {
            "event": "ORDER_FILLED",
            "tenant_id": tenant_id,
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "filled_qty": filled_qty,
            "filled_price": filled_price,
            "pnl": pnl,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        })
    
    async def publish_order_partial(
        self,
        tenant_id: str,
        order_id: str,
        symbol: str,
        filled_qty: float,
        remaining_qty: float,
        **kwargs
    ):
        """Publish order partial fill event."""
        await self._publish(CHANNEL_ORDERS, {
            "event": "ORDER_PARTIAL",
            "tenant_id": tenant_id,
            "order_id": order_id,
            "symbol": symbol,
            "filled_qty": filled_qty,
            "remaining_qty": remaining_qty,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        })
    
    async def publish_order_cancelled(
        self,
        tenant_id: str,
        order_id: str,
        symbol: str,
        reason: str,
        **kwargs
    ):
        """Publish order cancelled event."""
        await self._publish(CHANNEL_ORDERS, {
            "event": "ORDER_CANCELLED",
            "tenant_id": tenant_id,
            "order_id": order_id,
            "symbol": symbol,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        })
    
    async def publish_order_rejected(
        self,
        tenant_id: str,
        order_id: str,
        symbol: str,
        reason: str,
        **kwargs
    ):
        """Publish order rejected event."""
        await self._publish(CHANNEL_ORDERS, {
            "event": "ORDER_REJECTED",
            "tenant_id": tenant_id,
            "order_id": order_id,
            "symbol": symbol,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        })
    
    # =============================================================================
    # POSITION EVENTS
    # =============================================================================
    
    async def publish_position_opened(
        self,
        tenant_id: str,
        position_id: str,
        symbol: str,
        side: str,
        size: float,
        entry_price: float,
        **kwargs
    ):
        """Publish position opened event."""
        await self._publish(CHANNEL_POSITIONS, {
            "event": "POSITION_OPENED",
            "tenant_id": tenant_id,
            "position_id": position_id,
            "symbol": symbol,
            "side": side,
            "size": size,
            "entry_price": entry_price,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        })
    
    async def publish_position_updated(
        self,
        tenant_id: str,
        position_id: str,
        symbol: str,
        size: float,
        unrealized_pnl: float,
        mark_price: float,
        **kwargs
    ):
        """Publish position updated event."""
        await self._publish(CHANNEL_POSITIONS, {
            "event": "POSITION_UPDATED",
            "tenant_id": tenant_id,
            "position_id": position_id,
            "symbol": symbol,
            "size": size,
            "unrealized_pnl": unrealized_pnl,
            "mark_price": mark_price,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        })
    
    async def publish_position_closed(
        self,
        tenant_id: str,
        position_id: str,
        symbol: str,
        realized_pnl: float,
        exit_price: float,
        **kwargs
    ):
        """Publish position closed event."""
        await self._publish(CHANNEL_POSITIONS, {
            "event": "POSITION_CLOSED",
            "tenant_id": tenant_id,
            "position_id": position_id,
            "symbol": symbol,
            "realized_pnl": realized_pnl,
            "exit_price": exit_price,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        })
    
    # =============================================================================
    # PNL EVENTS
    # =============================================================================
    
    async def publish_pnl_update(
        self,
        tenant_id: str,
        total_pnl: float,
        unrealized_pnl: float,
        realized_pnl: float,
        daily_pnl: float,
        **kwargs
    ):
        """Publish PnL update event."""
        await self._publish(CHANNEL_PNL, {
            "event": "PNL_UPDATED",
            "tenant_id": tenant_id,
            "total_pnl": total_pnl,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "daily_pnl": daily_pnl,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        })
    
    # =============================================================================
    # PORTFOLIO EVENTS
    # =============================================================================
    
    async def publish_portfolio_update(
        self,
        tenant_id: str,
        equity: float,
        exposure: float,
        available_balance: float,
        positions: list,
        **kwargs
    ):
        """Publish portfolio update event."""
        await self._publish(CHANNEL_PORTFOLIO, {
            "event": "PORTFOLIO_UPDATED",
            "tenant_id": tenant_id,
            "equity": equity,
            "exposure": exposure,
            "available_balance": available_balance,
            "positions": positions,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        })
    
    # =============================================================================
    # SIGNAL EVENTS
    # =============================================================================
    
    async def publish_signal(
        self,
        tenant_id: str,
        signal_id: str,
        strategy_id: str,
        symbol: str,
        side: str,
        confidence: float,
        metadata: Dict[str, Any],
        **kwargs
    ):
        """Publish trading signal event."""
        await self._publish(CHANNEL_SIGNALS, {
            "event": "SIGNAL_GENERATED",
            "tenant_id": tenant_id,
            "signal_id": signal_id,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "side": side,
            "confidence": confidence,
            "metadata": metadata,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        })
    
    async def publish_signal_executed(
        self,
        tenant_id: str,
        signal_id: str,
        order_id: str,
        execution_price: float,
        **kwargs
    ):
        """Publish signal executed event."""
        await self._publish(CHANNEL_SIGNALS, {
            "event": "SIGNAL_EXECUTED",
            "tenant_id": tenant_id,
            "signal_id": signal_id,
            "order_id": order_id,
            "execution_price": execution_price,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        })
    
    # =============================================================================
    # MARKET DATA EVENTS
    # =============================================================================
    
    async def publish_market_data(
        self,
        tenant_id: str,
        symbol: str,
        price: float,
        bid: float,
        ask: float,
        volume_24h: float,
        **kwargs
    ):
        """Publish market data update."""
        await self._publish(CHANNEL_MARKET_DATA, {
            "event": "MARKET_DATA",
            "tenant_id": tenant_id,
            "symbol": symbol,
            "price": price,
            "bid": bid,
            "ask": ask,
            "volume_24h": volume_24h,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        })
    
    # =============================================================================
    # ALERT EVENTS
    # =============================================================================
    
    async def publish_alert(
        self,
        tenant_id: str,
        level: str,  # info, warning, critical
        title: str,
        message: str,
        **kwargs
    ):
        """Publish system alert."""
        await self._publish(CHANNEL_ALERTS, {
            "event": "ALERT",
            "tenant_id": tenant_id,
            "level": level,
            "title": title,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        })
    
    async def publish_circuit_breaker(
        self,
        tenant_id: str,
        state: str,  # opened, closed
        reason: str,
        **kwargs
    ):
        """Publish circuit breaker state change."""
        await self._publish(CHANNEL_CIRCUIT_BREAKER, {
            "event": f"CIRCUIT_BREAKER_{state.upper()}",
            "tenant_id": tenant_id,
            "state": state,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        })

    async def publish_signal_trace(self, tenant_id: str, data: Dict[str, Any]):
        """Publish signal trace event."""
        await self._publish("signal_trace", {
            "tenant_id": tenant_id,
            "payload": data,
            "timestamp": datetime.utcnow().isoformat(),
        })

    async def publish_risk_event(self, tenant_id: str, data: Dict[str, Any]):
        """Publish risk event."""
        await self._publish("risk_events", {
            "tenant_id": tenant_id,
            "payload": data,
            "timestamp": datetime.utcnow().isoformat(),
        })


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

_event_publisher: Optional[EventPublisher] = None


async def get_event_publisher() -> EventPublisher:
    """Get or create EventPublisher instance."""
    global _event_publisher
    if _event_publisher is None:
        _event_publisher = EventPublisher()
        await _event_publisher.connect()
    return _event_publisher
