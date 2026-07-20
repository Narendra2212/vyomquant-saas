"""
Connection Manager

STEP 7.9 — PRODUCTION HARDENING

Manages connections with auto-reconnect:
- CCXT exchanges
- WebSocket streams
- Database

Auto-reconnect logic:
┌─────────────────────────────────────────────────────────────────┐
│  Connection lost                                                 │
│       ↓                                                          │
│  Wait: exponential backoff (1s, 2s, 4s, 8s, max 60s)             │
│       ↓                                                          │
│  Attempt reconnect                                               │
│       ↓                                                          │
│  Success?                                                        │
│   ├─ Yes → Reset backoff, resume operations                      │
│   └─ No  → Increment retry, check max retries                    │
│              ↓                                                   │
│              Exceeded? → Alert and mark unhealthy                │
└─────────────────────────────────────────────────────────────────┘
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any, Callable

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """Connection states."""
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclass
class ConnectionConfig:
    """Configuration for connection management."""
    max_retries: int = 10                    # Max reconnection attempts
    base_retry_delay: float = 1.0            # Initial retry delay (seconds)
    max_retry_delay: float = 60.0            # Max retry delay
    connection_timeout: float = 30.0         # Connection timeout
    heartbeat_interval: float = 30.0         # Heartbeat interval
    max_missed_heartbeats: int = 3           # Max missed heartbeats before reconnect


class ManagedConnection(ABC):
    """
    Abstract base for managed connections.
    
    Implements auto-reconnect logic.
    """
    
    def __init__(
        self,
        name: str,
        config: Optional[ConnectionConfig] = None
    ):
        self.name = name
        self.config = config or ConnectionConfig()
        
        self.state = ConnectionState.DISCONNECTED
        self.last_connected: Optional[datetime] = None
        self.last_disconnected: Optional[datetime] = None
        self.retry_count = 0
        self.total_reconnects = 0
        
        # Callbacks
        self._on_connect: List[Callable] = []
        self._on_disconnect: List[Callable] = []
        self._on_reconnect: List[Callable] = []
        
        # Tasks
        self._connection_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Heartbeat tracking
        self._last_heartbeat = time.time()
    
    @abstractmethod
    async def _do_connect(self) -> bool:
        """Actual connection implementation. Return True if successful."""
        pass
    
    @abstractmethod
    async def _do_disconnect(self):
        """Actual disconnection implementation."""
        pass
    
    @abstractmethod
    async def _do_heartbeat(self) -> bool:
        """Send heartbeat/ping. Return True if successful."""
        pass
    
    def on_connect(self, callback: Callable):
        """Register connect callback."""
        self._on_connect.append(callback)
    
    def on_disconnect(self, callback: Callable):
        """Register disconnect callback."""
        self._on_disconnect.append(callback)
    
    def on_reconnect(self, callback: Callable):
        """Register reconnect callback."""
        self._on_reconnect.append(callback)
    
    async def connect(self) -> bool:
        """Connect with auto-reconnect enabled."""
        self._running = True
        
        # Start connection management
        self._connection_task = asyncio.create_task(self._connection_loop())
        
        # Wait for initial connection
        for _ in range(30):  # Wait up to 30 seconds
            if self.state == ConnectionState.CONNECTED:
                return True
            await asyncio.sleep(1)
        
        return False
    
    async def disconnect(self):
        """Disconnect and stop auto-reconnect."""
        self._running = False
        
        # Cancel tasks
        if self._connection_task:
            self._connection_task.cancel()
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # Disconnect
        await self._do_disconnect()
        self.state = ConnectionState.DISCONNECTED
    
    async def _connection_loop(self):
        """Main connection management loop."""
        while self._running:
            try:
                if self.state == ConnectionState.DISCONNECTED:
                    await self._attempt_connect()
                
                elif self.state == ConnectionState.CONNECTED:
                    # Monitor connection health
                    await asyncio.sleep(1)
                    
                    # Check if connection is still healthy
                    if not await self._check_health():
                        logger.warning(f"Connection {self.name} unhealthy, disconnecting")
                        await self._handle_disconnect()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Connection loop error: {e}")
                await asyncio.sleep(1)
    
    async def _attempt_connect(self):
        """Attempt to connect with retry logic."""
        self.state = ConnectionState.CONNECTING
        
        try:
            logger.info(f"Connecting to {self.name}...")
            
            success = await self._do_connect()
            
            if success:
                self.state = ConnectionState.CONNECTED
                self.last_connected = datetime.utcnow()
                self.retry_count = 0
                self.total_reconnects += 1
                
                logger.info(f"Connected to {self.name}")
                
                # Start heartbeat
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                
                # Notify callbacks
                for callback in self._on_connect:
                    try:
                        callback()
                    except Exception as e:
                        logger.error(f"Connect callback error: {e}")
                
                # If this was a reconnect, notify
                if self.total_reconnects > 1:
                    for callback in self._on_reconnect:
                        try:
                            callback()
                        except Exception as e:
                            logger.error(f"Reconnect callback error: {e}")
                
            else:
                await self._handle_connect_failure()
                
        except Exception as e:
            logger.error(f"Connection error: {e}")
            await self._handle_connect_failure()
    
    async def _handle_connect_failure(self):
        """Handle connection failure with retry."""
        self.retry_count += 1
        
        if self.retry_count > self.config.max_retries:
            logger.critical(
                f"Max retries exceeded for {self.name}, marking as failed"
            )
            self.state = ConnectionState.FAILED
            
            # Send alert
            await self._send_failure_alert()
            
            # Wait longer before next attempt
            await asyncio.sleep(self.config.max_retry_delay * 2)
            self.retry_count = 0  # Reset and try again
            return
        
        # Exponential backoff
        delay = min(
            self.config.base_retry_delay * (2 ** (self.retry_count - 1)),
            self.config.max_retry_delay
        )
        
        logger.warning(
            f"Connection failed ({self.retry_count}/{self.config.max_retries}), "
            f"retrying in {delay:.1f}s"
        )
        
        self.state = ConnectionState.RECONNECTING
        await asyncio.sleep(delay)
    
    async def _handle_disconnect(self):
        """Handle unexpected disconnection."""
        self.state = ConnectionState.DISCONNECTED
        self.last_disconnected = datetime.utcnow()
        
        # Stop heartbeat
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        
        # Notify callbacks
        for callback in self._on_disconnect:
            try:
                callback()
            except Exception as e:
                logger.error(f"Disconnect callback error: {e}")
        
        logger.warning(f"Disconnected from {self.name}, will reconnect")
    
    async def _heartbeat_loop(self):
        """Send periodic heartbeats to keep connection alive."""
        missed_heartbeats = 0
        
        while self._running and self.state == ConnectionState.CONNECTED:
            try:
                await asyncio.sleep(self.config.heartbeat_interval)
                
                success = await self._do_heartbeat()
                
                if success:
                    self._last_heartbeat = time.time()
                    missed_heartbeats = 0
                else:
                    missed_heartbeats += 1
                    logger.warning(
                        f"Heartbeat failed ({missed_heartbeats}/"
                        f"{self.config.max_missed_heartbeats})"
                    )
                    
                    if missed_heartbeats >= self.config.max_missed_heartbeats:
                        logger.error("Too many missed heartbeats, reconnecting")
                        await self._handle_disconnect()
                        break
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                missed_heartbeats += 1
    
    async def _check_health(self) -> bool:
        """Check if connection is healthy."""
        # Check heartbeat timeout
        elapsed = time.time() - self._last_heartbeat
        
        if elapsed > (self.config.heartbeat_interval * self.config.max_missed_heartbeats):
            logger.warning(f"Heartbeat timeout for {self.name}")
            return False
        
        return True
    
    async def _send_failure_alert(self):
        """Send alert when connection fails permanently."""
        try:
            from backend_app.backend.alert_system import get_alert_system
            
            alert_system = get_alert_system()
            await alert_system.send_critical_alert(
                title=f"Connection Failed: {self.name}",
                message=f"Connection to {self.name} failed after "
                        f"{self.config.max_retries} reconnection attempts",
                metadata={
                    "connection_name": self.name,
                    "retry_count": self.retry_count,
                    "last_connected": self.last_connected.isoformat() if self.last_connected else None
                }
            )
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get connection metrics."""
        return {
            "name": self.name,
            "state": self.state.value,
            "retry_count": self.retry_count,
            "total_reconnects": self.total_reconnects,
            "last_connected": self.last_connected.isoformat() if self.last_connected else None,
            "last_disconnected": self.last_disconnected.isoformat() if self.last_disconnected else None,
            "connected_duration_seconds": (
                (datetime.utcnow() - self.last_connected).total_seconds()
                if self.last_connected and self.state == ConnectionState.CONNECTED
                else 0
            )
        }


class ConnectionManager:
    """Manages multiple connections."""
    
    def __init__(self):
        self._connections: Dict[str, ManagedConnection] = {}
    
    def register(self, connection: ManagedConnection):
        """Register a managed connection."""
        self._connections[connection.name] = connection
    
    async def connect_all(self):
        """Connect all registered connections."""
        results = {}
        for name, conn in self._connections.items():
            try:
                success = await conn.connect()
                results[name] = success
            except Exception as e:
                logger.error(f"Failed to connect {name}: {e}")
                results[name] = False
        return results
    
    async def disconnect_all(self):
        """Disconnect all connections."""
        for name, conn in self._connections.items():
            try:
                await conn.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting {name}: {e}")
    
    def get_connection(self, name: str) -> Optional[ManagedConnection]:
        """Get a specific connection."""
        return self._connections.get(name)
    
    def get_all_metrics(self) -> Dict[str, Any]:
        """Get metrics for all connections."""
        return {
            name: conn.get_metrics()
            for name, conn in self._connections.items()
        }
    
    def check_all_healthy(self) -> bool:
        """Check if all connections are healthy."""
        for conn in self._connections.values():
            if conn.state != ConnectionState.CONNECTED:
                return False
        return True


# Global instance
_connection_manager: Optional[ConnectionManager] = None


def get_connection_manager() -> ConnectionManager:
    """Get global connection manager."""
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager()
    return _connection_manager
