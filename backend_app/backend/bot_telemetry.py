"""
Bot Telemetry System for Algo Trading Infrastructure

Provides realtime monitoring for:
- Deployed bots
- Execution status
- Signal generation
- Risk events
- Exchange connectivity

Author: Backend Observability Engineer
"""

import asyncio
import time
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import threading
from functools import wraps


class BotStatus(Enum):
    """Bot execution status states."""
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"


class SignalStatus(Enum):
    """Signal execution status."""
    RECEIVED = "received"
    VALIDATED = "validated"
    RISK_CHECKED = "risk_checked"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"
    BLOCKED = "blocked"


class RiskEventType(Enum):
    """Types of risk events."""
    POSITION_LIMIT = "position_limit"
    DRAWDOWN_BREACH = "drawdown_breach"
    EXPOSURE_LIMIT = "exposure_limit"
    SIGNAL_BLOCKED = "signal_blocked"
    KILL_SWITCH = "kill_switch"
    VOLATILITY_SPIKE = "volatility_spike"


@dataclass
class BotRegistration:
    """Bot registration data."""
    bot_id: str
    strategy_id: str
    strategy_name: str
    exchange: str
    symbol: str
    mode: str  # 'paper' | 'live'
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BotHealthMetrics:
    """Bot health and performance metrics."""
    bot_id: str
    status: BotStatus
    uptime_seconds: float = 0.0
    last_heartbeat: Optional[datetime] = None
    last_signal_at: Optional[datetime] = None
    last_signal_status: Optional[SignalStatus] = None
    execution_latency_ms: float = 0.0
    reconnect_count: int = 0
    websocket_connected: bool = False
    error_count: int = 0
    signal_count: int = 0
    execution_count: int = 0
    pnl_24h: Decimal = Decimal("0")
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SignalEvent:
    """Signal generation event."""
    event_id: str
    bot_id: str
    timestamp: datetime
    signal_type: str  # 'BUY' | 'SELL'
    symbol: str
    price: Decimal
    confidence: float
    dag_path: str
    status: SignalStatus
    ml_confidence: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionEvent:
    """Order execution event."""
    event_id: str
    bot_id: str
    signal_id: str
    timestamp: datetime
    symbol: str
    side: str
    size: Decimal
    price: Decimal
    filled_amount: Decimal
    fees: Decimal
    slippage: Decimal
    latency_ms: float
    success: bool
    error: Optional[str] = None


@dataclass
class RiskEvent:
    """Risk management event."""
    event_id: str
    bot_id: str
    timestamp: datetime
    event_type: RiskEventType
    severity: str  # 'low' | 'medium' | 'high' | 'critical'
    description: str
    signal_id: Optional[str] = None
    blocked: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class BotRegistry:
    """
    Registry for all deployed bots.
    
    Thread-safe storage for bot registrations.
    """
    
    def __init__(self):
        self._bots: Dict[str, BotRegistration] = {}
        self._lock = asyncio.Lock()
    
    async def register_bot(
        self,
        bot_id: str,
        strategy_id: str,
        strategy_name: str,
        exchange: str,
        symbol: str,
        mode: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> BotRegistration:
        """
        Register a new bot deployment.
        
        Args:
            bot_id: Unique bot identifier
            strategy_id: Associated strategy ID
            strategy_name: Human-readable strategy name
            exchange: Exchange name (e.g., 'binance', 'coinbase')
            symbol: Trading pair (e.g., 'BTC/USDT')
            mode: 'paper' or 'live'
            metadata: Optional additional data
            
        Returns:
            BotRegistration object
        """
        async with self._lock:
            registration = BotRegistration(
                bot_id=bot_id,
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                exchange=exchange,
                symbol=symbol,
                mode=mode,
                metadata=metadata or {}
            )
            self._bots[bot_id] = registration
            return registration
    
    async def unregister_bot(self, bot_id: str) -> bool:
        """Remove a bot from registry."""
        async with self._lock:
            if bot_id in self._bots:
                del self._bots[bot_id]
                return True
            return False
    
    async def get_bot(self, bot_id: str) -> Optional[BotRegistration]:
        """Get bot registration by ID."""
        async with self._lock:
            return self._bots.get(bot_id)
    
    async def get_all_bots(self) -> List[BotRegistration]:
        """Get all registered bots."""
        async with self._lock:
            return list(self._bots.values())
    
    async def get_bots_by_strategy(self, strategy_id: str) -> List[BotRegistration]:
        """Get all bots for a specific strategy."""
        async with self._lock:
            return [
                bot for bot in self._bots.values()
                if bot.strategy_id == strategy_id
            ]
    
    async def get_bots_by_exchange(self, exchange: str) -> List[BotRegistration]:
        """Get all bots for a specific exchange."""
        async with self._lock:
            return [
                bot for bot in self._bots.values()
                if bot.exchange == exchange
            ]


class BotHealthTracker:
    """
    Tracks real-time health metrics for all bots.
    
    Maintains thread-safe health state with automatic
    heartbeat tracking and status updates.
    """
    
    def __init__(self, heartbeat_timeout_seconds: float = 60.0):
        self._health: Dict[str, BotHealthMetrics] = {}
        self._lock = asyncio.Lock()
        self._heartbeat_timeout = heartbeat_timeout_seconds
        self._callbacks: List[Callable[[str, BotHealthMetrics], None]] = []
    
    async def update_bot_status(
        self,
        bot_id: str,
        status: BotStatus,
        websocket_connected: Optional[bool] = None,
        error_increment: bool = False
    ) -> BotHealthMetrics:
        """
        Update bot status and health metrics.
        
        Args:
            bot_id: Bot identifier
            status: New status state
            websocket_connected: Optional WebSocket connection state
            error_increment: Whether to increment error count
            
        Returns:
            Updated health metrics
        """
        async with self._lock:
            now = datetime.now(timezone.utc)
            
            if bot_id not in self._health:
                self._health[bot_id] = BotHealthMetrics(
                    bot_id=bot_id,
                    status=status,
                    last_heartbeat=now
                )
            
            health = self._health[bot_id]
            health.status = status
            health.last_heartbeat = now
            health.updated_at = now
            
            if websocket_connected is not None:
                health.websocket_connected = websocket_connected
            
            if error_increment:
                health.error_count += 1
            
            # Calculate uptime
            if health.status == BotStatus.RUNNING:
                # Uptime calculation would track start time
                pass
            
            # Notify callbacks
            for callback in self._callbacks:
                try:
                    callback(bot_id, health)
                except Exception:
                    pass
            
            return health
    
    async def record_heartbeat(self, bot_id: str) -> None:
        """Record bot heartbeat."""
        async with self._lock:
            if bot_id in self._health:
                self._health[bot_id].last_heartbeat = datetime.now(timezone.utc)
    
    async def record_signal(self, bot_id: str, status: SignalStatus) -> None:
        """Record signal generation for a bot."""
        async with self._lock:
            if bot_id in self._health:
                self._health[bot_id].signal_count += 1
                self._health[bot_id].last_signal_at = datetime.now(timezone.utc)
                self._health[bot_id].last_signal_status = status
    
    async def record_execution(
        self,
        bot_id: str,
        latency_ms: float,
        success: bool
    ) -> None:
        """Record execution event for latency tracking."""
        async with self._lock:
            if bot_id in self._health:
                health = self._health[bot_id]
                health.execution_count += 1
                health.execution_latency_ms = latency_ms
                if not success:
                    health.error_count += 1
    
    async def record_reconnect(self, bot_id: str) -> None:
        """Record WebSocket reconnection event."""
        async with self._lock:
            if bot_id in self._health:
                self._health[bot_id].reconnect_count += 1
    
    async def get_bot_health(self, bot_id: str) -> Optional[BotHealthMetrics]:
        """Get current health metrics for a bot."""
        async with self._lock:
            return self._health.get(bot_id)
    
    async def get_all_health(self) -> List[BotHealthMetrics]:
        """Get health metrics for all bots."""
        async with self._lock:
            return list(self._health.values())
    
    async def get_unhealthy_bots(self) -> List[BotHealthMetrics]:
        """Get bots with non-running status."""
        async with self._lock:
            return [
                health for health in self._health.values()
                if health.status != BotStatus.RUNNING
            ]
    
    def on_health_change(self, callback: Callable[[str, BotHealthMetrics], None]) -> None:
        """Register callback for health status changes."""
        self._callbacks.append(callback)
    
    async def check_stale_heartbeats(self) -> List[str]:
        """
        Check for bots with stale heartbeats.
        
        Returns:
            List of stale bot IDs
        """
        async with self._lock:
            now = datetime.now(timezone.utc)
            stale = []
            
            for bot_id, health in self._health.items():
                if health.last_heartbeat:
                    elapsed = (now - health.last_heartbeat).total_seconds()
                    if elapsed > self._heartbeat_timeout:
                        stale.append(bot_id)
                        health.status = BotStatus.RECONNECTING
            
            return stale


class SignalEventStore:
    """
    Event store for signal generation events.
    
    Circular buffer for recent signals with full history
    available via persistence layer.
    """
    
    def __init__(self, max_events_per_bot: int = 1000):
        self._events: Dict[str, deque] = {}
        self._lock = asyncio.Lock()
        self._max_events = max_events_per_bot
        self._sequence = 0
    
    def _generate_event_id(self) -> str:
        """Generate unique event ID."""
        self._sequence += 1
        timestamp = int(time.time() * 1000)
        return f"sig-{timestamp}-{self._sequence}"
    
    async def record_signal_event(
        self,
        bot_id: str,
        signal_type: str,
        symbol: str,
        price: Decimal,
        confidence: float,
        dag_path: str,
        status: SignalStatus,
        ml_confidence: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SignalEvent:
        """
        Record a signal generation event.
        
        Args:
            bot_id: Source bot ID
            signal_type: 'BUY' or 'SELL'
            symbol: Trading pair
            price: Signal price
            confidence: Signal confidence (0-1)
            dag_path: Execution path through DAG
            status: Signal processing status
            ml_confidence: ML model confidence
            metadata: Additional event data
            
        Returns:
            SignalEvent object
        """
        async with self._lock:
            event = SignalEvent(
                event_id=self._generate_event_id(),
                bot_id=bot_id,
                timestamp=datetime.now(timezone.utc),
                signal_type=signal_type,
                symbol=symbol,
                price=price,
                confidence=confidence,
                dag_path=dag_path,
                status=status,
                ml_confidence=ml_confidence,
                metadata=metadata or {}
            )
            
            if bot_id not in self._events:
                self._events[bot_id] = deque(maxlen=self._max_events)
            
            self._events[bot_id].appendleft(event)
            return event
    
    async def update_signal_status(
        self,
        event_id: str,
        new_status: SignalStatus
    ) -> bool:
        """Update status of an existing signal event."""
        async with self._lock:
            for bot_events in self._events.values():
                for event in bot_events:
                    if event.event_id == event_id:
                        event.status = new_status
                        return True
            return False
    
    async def get_recent_signals(
        self,
        bot_id: str,
        limit: int = 50
    ) -> List[SignalEvent]:
        """Get recent signals for a bot."""
        async with self._lock:
            events = self._events.get(bot_id, deque())
            return list(events)[:limit]
    
    async def get_signals_by_status(
        self,
        bot_id: str,
        status: SignalStatus,
        limit: int = 50
    ) -> List[SignalEvent]:
        """Get signals filtered by status."""
        async with self._lock:
            events = self._events.get(bot_id, deque())
            return [e for e in events if e.status == status][:limit]
    
    async def get_signal_stats(self, bot_id: str) -> Dict[str, int]:
        """Get signal count statistics by status."""
        async with self._lock:
            events = self._events.get(bot_id, deque())
            stats = {status.value: 0 for status in SignalStatus}
            for event in events:
                stats[event.status.value] += 1
            return stats


class ExecutionEventStore:
    """
    Event store for order execution events.
    
    Tracks fill prices, fees, slippage, and latency.
    """
    
    def __init__(self, max_events_per_bot: int = 500):
        self._events: Dict[str, deque] = {}
        self._lock = asyncio.Lock()
        self._max_events = max_events_per_bot
        self._sequence = 0
    
    def _generate_event_id(self) -> str:
        """Generate unique event ID."""
        self._sequence += 1
        timestamp = int(time.time() * 1000)
        return f"exec-{timestamp}-{self._sequence}"
    
    async def record_execution_event(
        self,
        bot_id: str,
        signal_id: str,
        symbol: str,
        side: str,
        size: Decimal,
        price: Decimal,
        filled_amount: Decimal,
        fees: Decimal,
        slippage: Decimal,
        latency_ms: float,
        success: bool,
        error: Optional[str] = None
    ) -> ExecutionEvent:
        """
        Record an order execution event.
        
        Args:
            bot_id: Source bot ID
            signal_id: Parent signal ID
            symbol: Trading pair
            side: 'buy' or 'sell'
            size: Order size
            price: Executed price
            filled_amount: Actually filled amount
            fees: Trading fees
            slippage: Price slippage
            latency_ms: Execution latency
            success: Whether execution succeeded
            error: Error message if failed
            
        Returns:
            ExecutionEvent object
        """
        async with self._lock:
            event = ExecutionEvent(
                event_id=self._generate_event_id(),
                bot_id=bot_id,
                signal_id=signal_id,
                timestamp=datetime.now(timezone.utc),
                symbol=symbol,
                side=side,
                size=size,
                price=price,
                filled_amount=filled_amount,
                fees=fees,
                slippage=slippage,
                latency_ms=latency_ms,
                success=success,
                error=error
            )
            
            if bot_id not in self._events:
                self._events[bot_id] = deque(maxlen=self._max_events)
            
            self._events[bot_id].appendleft(event)
            return event
    
    async def get_recent_executions(
        self,
        bot_id: str,
        limit: int = 50
    ) -> List[ExecutionEvent]:
        """Get recent executions for a bot."""
        async with self._lock:
            events = self._events.get(bot_id, deque())
            return list(events)[:limit]
    
    async def get_execution_stats(self, bot_id: str) -> Dict[str, Any]:
        """
        Get execution statistics for a bot.
        
        Returns:
            Dict with success_rate, avg_latency, total_fees, etc.
        """
        async with self._lock:
            events = list(self._events.get(bot_id, deque()))
            
            if not events:
                return {
                    "total": 0,
                    "successful": 0,
                    "failed": 0,
                    "success_rate": 0.0,
                    "avg_latency_ms": 0.0,
                    "total_fees": Decimal("0"),
                    "avg_slippage": Decimal("0")
                }
            
            successful = [e for e in events if e.success]
            failed = [e for e in events if not e.success]
            
            avg_latency = sum(e.latency_ms for e in successful) / len(successful) if successful else 0
            total_fees = sum((e.fees for e in events), Decimal("0"))
            avg_slippage = sum((e.slippage for e in events), Decimal("0")) / len(events)
            
            return {
                "total": len(events),
                "successful": len(successful),
                "failed": len(failed),
                "success_rate": len(successful) / len(events) * 100,
                "avg_latency_ms": round(avg_latency, 2),
                "total_fees": total_fees,
                "avg_slippage": round(avg_slippage, 8)
            }


class RiskEventStore:
    """
    Event store for risk management events.
    
    Tracks position limits, drawdown, exposure, and kill switch events.
    """
    
    def __init__(self, max_events_per_bot: int = 200):
        self._events: Dict[str, deque] = {}
        self._lock = asyncio.Lock()
        self._max_events = max_events_per_bot
        self._sequence = 0
        self._active_blocks: Dict[str, set] = {}  # Track active signal blocks per bot
    
    def _generate_event_id(self) -> str:
        """Generate unique event ID."""
        self._sequence += 1
        timestamp = int(time.time() * 1000)
        return f"risk-{timestamp}-{self._sequence}"
    
    async def record_risk_event(
        self,
        bot_id: str,
        event_type: RiskEventType,
        severity: str,
        description: str,
        signal_id: Optional[str] = None,
        blocked: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> RiskEvent:
        """
        Record a risk management event.
        
        Args:
            bot_id: Source bot ID
            event_type: Type of risk event
            severity: 'low', 'medium', 'high', or 'critical'
            description: Human-readable description
            signal_id: Associated signal ID (if applicable)
            blocked: Whether a signal was blocked
            metadata: Additional event data
            
        Returns:
            RiskEvent object
        """
        async with self._lock:
            event = RiskEvent(
                event_id=self._generate_event_id(),
                bot_id=bot_id,
                timestamp=datetime.now(timezone.utc),
                event_type=event_type,
                severity=severity,
                description=description,
                signal_id=signal_id,
                blocked=blocked,
                metadata=metadata or {}
            )
            
            if bot_id not in self._events:
                self._events[bot_id] = deque(maxlen=self._max_events)
                self._active_blocks[bot_id] = set()
            
            self._events[bot_id].appendleft(event)
            
            if blocked and signal_id:
                self._active_blocks[bot_id].add(signal_id)
            
            return event
    
    async def get_recent_risk_events(
        self,
        bot_id: str,
        limit: int = 50,
        severity_filter: Optional[List[str]] = None
    ) -> List[RiskEvent]:
        """Get recent risk events with optional severity filter."""
        async with self._lock:
            events = self._events.get(bot_id, deque())
            
            if severity_filter:
                return [
                    e for e in events
                    if e.severity in severity_filter
                ][:limit]
            
            return list(events)[:limit]
    
    async def get_critical_events(self, bot_id: str, limit: int = 20) -> List[RiskEvent]:
        """Get critical and high severity events."""
        return await self.get_recent_risk_events(
            bot_id,
            limit=limit,
            severity_filter=['critical', 'high']
        )
    
    async def get_risk_summary(self, bot_id: str) -> Dict[str, Any]:
        """
        Get risk event summary for a bot.
        
        Returns:
            Dict with event counts by severity and type
        """
        async with self._lock:
            events = list(self._events.get(bot_id, deque()))
            
            severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
            type_counts = {t.value: 0 for t in RiskEventType}
            blocked_count = 0
            
            for event in events:
                if event.severity in severity_counts:
                    severity_counts[event.severity] += 1
                if event.event_type.value in type_counts:
                    type_counts[event.event_type.value] += 1
                if event.blocked:
                    blocked_count += 1
            
            return {
                "total_events": len(events),
                "by_severity": severity_counts,
                "by_type": type_counts,
                "blocked_signals": blocked_count,
                "last_event": events[0].timestamp if events else None
            }
    
    async def is_signal_blocked(self, bot_id: str, signal_id: str) -> bool:
        """Check if a specific signal is blocked by risk controls."""
        async with self._lock:
            if bot_id in self._active_blocks:
                return signal_id in self._active_blocks[bot_id]
            return False
    
    async def clear_block(self, bot_id: str, signal_id: str) -> bool:
        """Clear a risk block for a signal."""
        async with self._lock:
            if bot_id in self._active_blocks:
                if signal_id in self._active_blocks[bot_id]:
                    self._active_blocks[bot_id].remove(signal_id)
                    return True
            return False


class BotTelemetryService:
    """
    Unified telemetry service aggregating all monitoring components.
    
    Provides a single interface for bot registration, health tracking,
    event recording, and querying telemetry data.
    """
    
    def __init__(
        self,
        heartbeat_timeout: float = 60.0,
        max_signal_events: int = 1000,
        max_execution_events: int = 500,
        max_risk_events: int = 200
    ):
        self.registry = BotRegistry()
        self.health = BotHealthTracker(heartbeat_timeout)
        self.signals = SignalEventStore(max_signal_events)
        self.executions = ExecutionEventStore(max_execution_events)
        self.risks = RiskEventStore(max_risk_events)
    
    async def register_bot(
        self,
        bot_id: str,
        strategy_id: str,
        strategy_name: str,
        exchange: str,
        symbol: str,
        mode: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> BotRegistration:
        """Register a new bot and initialize health tracking."""
        registration = await self.registry.register_bot(
            bot_id, strategy_id, strategy_name, exchange, symbol, mode, metadata
        )
        
        # Initialize health record
        await self.health.update_bot_status(
            bot_id,
            BotStatus.RUNNING,
            websocket_connected=False
        )
        
        return registration
    
    async def get_full_bot_status(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """
        Get complete bot status including registration, health, and recent events.
        
        Returns:
            Dict with registration, health, recent_signals, recent_risks
        """
        registration = await self.registry.get_bot(bot_id)
        if not registration:
            return None
        
        health = await self.health.get_bot_health(bot_id)
        signals = await self.signals.get_recent_signals(bot_id, limit=10)
        risks = await self.risks.get_recent_risk_events(bot_id, limit=10)
        exec_stats = await self.executions.get_execution_stats(bot_id)
        
        return {
            "registration": registration,
            "health": health,
            "recent_signals": signals,
            "recent_risk_events": risks,
            "execution_stats": exec_stats,
            "signal_stats": await self.signals.get_signal_stats(bot_id) if signals else {},
            "risk_summary": await self.risks.get_risk_summary(bot_id) if risks else {}
        }
    
    async def get_all_bots_status(self) -> List[Dict[str, Any]]:
        """Get full status for all registered bots."""
        bots = await self.registry.get_all_bots()
        statuses = []
        
        for bot in bots:
            status = await self.get_full_bot_status(bot.bot_id)
            if status:
                statuses.append(status)
        
        return statuses
    
    async def get_system_summary(self) -> Dict[str, Any]:
        """
        Get high-level system telemetry summary.
        
        Returns:
            Dict with total bots, active bots, recent signals, alerts, etc.
        """
        all_bots = await self.registry.get_all_bots()
        all_health = await self.health.get_all_health()
        
        running_bots = [h for h in all_health if h.status == BotStatus.RUNNING]
        unhealthy_bots = [h for h in all_health if h.status != BotStatus.RUNNING]
        
        total_signals = sum(h.signal_count for h in all_health)
        total_errors = sum(h.error_count for h in all_health)
        total_reconnects = sum(h.reconnect_count for h in all_health)
        
        # Count critical risk events
        critical_events = 0
        for bot in all_bots:
            risks = await self.risks.get_critical_events(bot.bot_id, limit=100)
            critical_events += len(risks)
        
        return {
            "total_bots": len(all_bots),
            "running_bots": len(running_bots),
            "unhealthy_bots": len(unhealthy_bots),
            "unhealthy_bot_ids": [h.bot_id for h in unhealthy_bots],
            "total_signals_generated": total_signals,
            "total_errors": total_errors,
            "total_reconnects": total_reconnects,
            "critical_risk_events": critical_events,
            "system_health": "healthy" if len(unhealthy_bots) == 0 else "degraded"
        }


# Global telemetry service instance
telemetry = BotTelemetryService()


# Convenience exports
__all__ = [
    'BotTelemetryService',
    'BotRegistry',
    'BotHealthTracker',
    'SignalEventStore',
    'ExecutionEventStore',
    'RiskEventStore',
    'BotRegistration',
    'BotHealthMetrics',
    'SignalEvent',
    'ExecutionEvent',
    'RiskEvent',
    'BotStatus',
    'SignalStatus',
    'RiskEventType',
    'telemetry'
]
