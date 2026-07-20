"""
backend/websocket_monitor.py — WebSocket Connection Monitor (STEP 8.7)

PHASE 8: MONITORING + ALERTING + FAILSAFE INFRASTRUCTURE
STEP 8.7: WebSocket Monitor - Data Stream Reliability

Purpose:
  - Track WebSocket connection health in real-time
  - Monitor last message timestamp per connection
  - Count disconnections
  - Auto-reconnect on stale connections (> 10 seconds)
  - Alert on connection issues

Features:
  - Connection state tracking (connected, disconnected, stale)
  - Per-connection message timestamps
  - Disconnect counter with rate limiting
  - Automatic reconnection with exponential backoff
  - Integration with metrics system for alerting

Usage:
    from backend_app.backend.websocket_monitor import WebSocketMonitor, get_websocket_monitor
    
    # Register a WebSocket connection
    monitor = get_websocket_monitor()
    monitor.register_connection("binance_btc", exchange_id=os.getenv("DEFAULT_EXCHANGE", "binance"), symbol="BTC-USD")
    
    # Update on message received
    monitor.record_message("binance_btc")
    
    # Check for stale connections (call periodically)
    await monitor.check_stale_connections()
    
    # Get connection status
    status = monitor.get_connection_status("binance_btc")
"""

import asyncio
import time
import threading
import logging
from typing import Dict, Optional, Callable, Any, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# STEP 8.1: Import metrics for tracking
from backend_app.backend.metrics import (
    record_websocket_disconnect,
    record_websocket_connect,
    record_websocket_close,
)

logger = logging.getLogger("WebSocketMonitor")


class ConnectionState(Enum):
    """WebSocket connection states."""
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    STALE = "stale"  # No message received for > threshold
    RECONNECTING = "reconnecting"


@dataclass
class ConnectionMetrics:
    """Metrics for a single WebSocket connection."""
    connection_id: str
    exchange_id: str
    symbol: str
    state: ConnectionState = ConnectionState.DISCONNECTED
    
    # Timing
    connected_at: Optional[float] = None
    last_message_at: Optional[float] = None
    disconnected_at: Optional[float] = None
    
    # Counters
    total_messages: int = 0
    disconnect_count: int = 0
    reconnect_count: int = 0
    
    # Stale detection
    stale_threshold_seconds: float = 10.0
    is_stale: bool = False
    
    # Metadata
    url: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class WebSocketMonitor:
    """
    STEP 8.7: WebSocket Monitor for Data Stream Reliability
    
    Tracks:
    - Last message time per connection
    - Disconnect count
    - Connection state
    
    Actions on stale > 10 seconds:
    1. Mark connection as stale
    2. Trigger reconnect callback
    3. Send alert via metrics system
    
    Usage:
        monitor = WebSocketMonitor(stale_threshold_seconds=10.0)
        
        # Register connection
        monitor.register_connection("conn_1", "binance", "BTC-USD")
        
        # Register reconnect callback
        monitor.register_reconnect_callback("conn_1", my_reconnect_func)
        
        # Record activity
        monitor.record_message("conn_1")
        
        # Periodic stale check
        await monitor.check_stale_connections()
    """
    
    def __init__(self, stale_threshold_seconds: float = 10.0):
        self.stale_threshold_seconds = stale_threshold_seconds
        self._connections: Dict[str, ConnectionMetrics] = {}
        self._reconnect_callbacks: Dict[str, Callable[[str], Any]] = {}
        self._lock = threading.Lock()
        self._monitoring_task: Optional[asyncio.Task] = None
        
        logger.info(
            f"STEP 8.7: WebSocket Monitor initialized | "
            f"Stale threshold: {stale_threshold_seconds}s"
        )
    
    def register_connection(
        self,
        connection_id: str,
        exchange_id: str,
        symbol: str,
        url: str = "",
        metadata: Optional[Dict[str, Any]] = None
    ) -> ConnectionMetrics:
        """
        Register a new WebSocket connection for monitoring.
        
        Args:
            connection_id: Unique identifier for this connection
            exchange_id: Exchange identifier (e.g., "binance", "coinbase")
            symbol: Trading symbol (e.g., "BTC-USD")
            url: WebSocket URL (optional)
            metadata: Additional connection metadata
        
        Returns:
            ConnectionMetrics object for this connection
        """
        with self._lock:
            if connection_id in self._connections:
                logger.warning(
                    f"STEP 8.7: Connection '{connection_id}' already registered, updating"
                )
            
            metrics = ConnectionMetrics(
                connection_id=connection_id,
                exchange_id=exchange_id,
                symbol=symbol,
                url=url,
                stale_threshold_seconds=self.stale_threshold_seconds,
                metadata=metadata or {},
            )
            self._connections[connection_id] = metrics
            
            logger.info(
                f"STEP 8.7: Registered connection '{connection_id}' | "
                f"Exchange: {exchange_id} | Symbol: {symbol}"
            )
            
            return metrics
    
    def unregister_connection(self, connection_id: str):
        """Unregister a connection from monitoring."""
        with self._lock:
            if connection_id in self._connections:
                del self._connections[connection_id]
                logger.info(f"STEP 8.7: Unregistered connection '{connection_id}'")
    
    def record_connected(self, connection_id: str):
        """Record that a connection is established."""
        with self._lock:
            if connection_id not in self._connections:
                logger.warning(
                    f"STEP 8.7: Connection '{connection_id}' not registered, skipping"
                )
                return
            
            conn = self._connections[connection_id]
            conn.state = ConnectionState.CONNECTED
            conn.connected_at = time.time()
            conn.is_stale = False
            
            # STEP 8.1: Record metric
            record_websocket_connect(exchange=conn.exchange_id)
            
            logger.info(
                f"STEP 8.7: Connection '{connection_id}' CONNECTED | "
                f"Exchange: {conn.exchange_id}"
            )
    
    def record_message(self, connection_id: str):
        """
        Record that a message was received on this connection.
        Resets stale status if previously marked stale.
        """
        with self._lock:
            if connection_id not in self._connections:
                return
            
            conn = self._connections[connection_id]
            now = time.time()
            
            conn.last_message_at = now
            conn.total_messages += 1
            
            # If was stale, mark as recovered
            if conn.is_stale or conn.state == ConnectionState.STALE:
                conn.is_stale = False
                conn.state = ConnectionState.CONNECTED
                logger.info(
                    f"STEP 8.7: Connection '{connection_id}' RECOVERED from stale | "
                    f"Messages: {conn.total_messages}"
                )
    
    def record_disconnected(self, connection_id: str, reason: str = ""):
        """
        Record that a connection was disconnected.
        Increments disconnect counter and triggers alert.
        """
        with self._lock:
            if connection_id not in self._connections:
                return
            
            conn = self._connections[connection_id]
            conn.state = ConnectionState.DISCONNECTED
            conn.disconnected_at = time.time()
            conn.disconnect_count += 1
            
            # STEP 8.1: Record metrics
            record_websocket_disconnect(exchange=conn.exchange_id)
            record_websocket_close(exchange=conn.exchange_id)
            
            logger.warning(
                f"STEP 8.7: Connection '{connection_id}' DISCONNECTED | "
                f"Reason: {reason} | Total disconnects: {conn.disconnect_count}"
            )
    
    def register_reconnect_callback(
        self,
        connection_id: str,
        callback: Callable[[str], Any]
    ):
        """
        Register a callback to execute when reconnection is needed.
        
        Args:
            connection_id: Connection to monitor
            callback: Function(connection_id: str) -> Any
                     Called when stale connection detected
        """
        self._reconnect_callbacks[connection_id] = callback
        logger.info(
            f"STEP 8.7: Registered reconnect callback for '{connection_id}'"
        )
    
    async def check_stale_connections(self) -> List[Dict[str, Any]]:
        """
        Check all connections for stale status (no message > threshold).
        
        Returns:
            List of stale connection reports
        """
        stale_connections = []
        now = time.time()
        
        with self._lock:
            for conn_id, conn in self._connections.items():
                # Skip if not connected
                if conn.state != ConnectionState.CONNECTED:
                    continue
                
                # Check if stale
                if conn.last_message_at is None:
                    # Never received a message - check connect time
                    if conn.connected_at and (now - conn.connected_at) > conn.stale_threshold_seconds:
                        conn.is_stale = True
                        conn.state = ConnectionState.STALE
                else:
                    time_since_last_message = now - conn.last_message_at
                    if time_since_last_message > conn.stale_threshold_seconds:
                        conn.is_stale = True
                        conn.state = ConnectionState.STALE
                
                # Handle stale connection
                if conn.is_stale:
                    stale_report = {
                        "connection_id": conn_id,
                        "exchange_id": conn.exchange_id,
                        "symbol": conn.symbol,
                        "stale_duration_seconds": now - (conn.last_message_at or conn.connected_at or now),
                        "last_message_at": datetime.fromtimestamp(conn.last_message_at).isoformat() if conn.last_message_at else None,
                        "total_messages": conn.total_messages,
                        "disconnect_count": conn.disconnect_count,
                    }
                    stale_connections.append(stale_report)
                    
                    logger.critical(
                        f"🚨 STEP 8.7: STALE CONNECTION DETECTED | "
                        f"'{conn_id}' | Exchange: {conn.exchange_id} | "
                        f"Stale for {stale_report['stale_duration_seconds']:.1f}s | "
                        f"Threshold: {conn.stale_threshold_seconds}s"
                    )
                    
                    # Trigger reconnection
                    await self._trigger_reconnect(conn_id)
        
        return stale_connections
    
    async def _trigger_reconnect(self, connection_id: str):
        """Trigger reconnection for a stale connection."""
        with self._lock:
            if connection_id not in self._connections:
                return
            
            conn = self._connections[connection_id]
            conn.state = ConnectionState.RECONNECTING
            conn.reconnect_count += 1
            
            # Execute reconnect callback if registered
            if connection_id in self._reconnect_callbacks:
                callback = self._reconnect_callbacks[connection_id]
                try:
                    logger.critical(
                        f"🔄 STEP 8.7: TRIGGERING RECONNECT for '{connection_id}' | "
                        f"Attempt: {conn.reconnect_count}"
                    )
                    
                    # Run callback (could be sync or async)
                    if asyncio.iscoroutinefunction(callback):
                        await callback(connection_id)
                    else:
                        callback(connection_id)
                    
                    logger.info(
                        f"✅ STEP 8.7: Reconnect callback completed for '{connection_id}'"
                    )
                    
                except Exception as e:
                    logger.error(
                        f"❌ STEP 8.7: Reconnect callback failed for '{connection_id}': {e}"
                    )
            else:
                logger.warning(
                    f"⚠️ STEP 8.7: No reconnect callback registered for '{connection_id}'"
                )
    
    def get_connection_status(self, connection_id: str) -> Optional[Dict[str, Any]]:
        """Get current status of a connection."""
        with self._lock:
            if connection_id not in self._connections:
                return None
            
            conn = self._connections[connection_id]
            now = time.time()
            
            return {
                "connection_id": conn.connection_id,
                "exchange_id": conn.exchange_id,
                "symbol": conn.symbol,
                "state": conn.state.value,
                "is_stale": conn.is_stale,
                "connected_at": datetime.fromtimestamp(conn.connected_at).isoformat() if conn.connected_at else None,
                "last_message_at": datetime.fromtimestamp(conn.last_message_at).isoformat() if conn.last_message_at else None,
                "disconnected_at": datetime.fromtimestamp(conn.disconnected_at).isoformat() if conn.disconnected_at else None,
                "seconds_since_last_message": now - conn.last_message_at if conn.last_message_at else None,
                "total_messages": conn.total_messages,
                "disconnect_count": conn.disconnect_count,
                "reconnect_count": conn.reconnect_count,
                "stale_threshold_seconds": conn.stale_threshold_seconds,
            }
    
    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all connections."""
        with self._lock:
            return {
                conn_id: self.get_connection_status(conn_id)
                for conn_id in self._connections.keys()
            }
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all connections."""
        with self._lock:
            total = len(self._connections)
            connected = sum(1 for c in self._connections.values() if c.state == ConnectionState.CONNECTED)
            stale = sum(1 for c in self._connections.values() if c.state == ConnectionState.STALE)
            disconnected = sum(1 for c in self._connections.values() if c.state == ConnectionState.DISCONNECTED)
            reconnecting = sum(1 for c in self._connections.values() if c.state == ConnectionState.RECONNECTING)
            
            total_disconnects = sum(c.disconnect_count for c in self._connections.values())
            total_reconnects = sum(c.reconnect_count for c in self._connections.values())
            total_messages = sum(c.total_messages for c in self._connections.values())
            
            return {
                "timestamp": datetime.utcnow().isoformat(),
                "total_connections": total,
                "connected": connected,
                "stale": stale,
                "disconnected": disconnected,
                "reconnecting": reconnecting,
                "total_disconnects": total_disconnects,
                "total_reconnects": total_reconnects,
                "total_messages": total_messages,
                "healthy_percentage": (connected / total * 100) if total > 0 else 0,
            }
    
    async def start_monitoring(self, check_interval_seconds: float = 5.0):
        """
        Start background monitoring task.
        
        Args:
            check_interval_seconds: How often to check for stale connections
        """
        async def monitoring_loop():
            while True:
                try:
                    await self.check_stale_connections()
                    await asyncio.sleep(check_interval_seconds)
                except Exception as e:
                    logger.error(f"STEP 8.7: Monitoring loop error: {e}")
                    await asyncio.sleep(check_interval_seconds)
        
        if self._monitoring_task is None:
            self._monitoring_task = asyncio.create_task(monitoring_loop())
            logger.info(
                f"STEP 8.7: Started background monitoring | "
                f"Interval: {check_interval_seconds}s"
            )
    
    def stop_monitoring(self):
        """Stop background monitoring task."""
        if self._monitoring_task:
            self._monitoring_task.cancel()
            self._monitoring_task = None
            logger.info("STEP 8.7: Stopped background monitoring")


# Global singleton
_websocket_monitor: Optional[WebSocketMonitor] = None
_monitor_lock = threading.Lock()


def get_websocket_monitor(stale_threshold_seconds: float = 10.0) -> WebSocketMonitor:
    """Get the global WebSocket monitor singleton."""
    global _websocket_monitor
    
    with _monitor_lock:
        if _websocket_monitor is None:
            _websocket_monitor = WebSocketMonitor(stale_threshold_seconds)
        return _websocket_monitor


# Alias for WebSocket server compatibility
WebSocketHealthMonitor = WebSocketMonitor
