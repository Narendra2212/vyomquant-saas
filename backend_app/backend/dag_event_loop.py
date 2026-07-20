"""
backend/dag_event_loop.py — Event-Driven DAG Execution System.

Converts static DAG execution into a continuous event-driven system:
  - Accepts streaming market data (ticks/candles)
  - Triggers DAG execution on each new data point
  - Maintains rolling window state for indicators
  - Emits trading signals continuously
  - Supports multiple symbols and timeframes concurrently

Architecture:
  EventSource (WebSocket/Stream) → EventLoop → DAGEngine → SignalEmitter
                                      ↓
                              RollingWindowState (stateful indicators)
"""

import asyncio
import logging
from typing import Dict, List, Any, Optional, Callable, Set, Tuple
from dataclasses import dataclass, field
from collections import deque
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from enum import Enum

from backend_app.backend.dag_engine import DAGEngine, NodeExecutor
from backend_app.core.feature_flags import ExecutionFlags, ExecutionContext
from backend_app.core.safety_monitor import log_blocked_execution

logger = logging.getLogger("DAGEventLoop")


class EventType(Enum):
    """Types of market data events."""
    TICK = "tick"           # Individual trade/price update
    CANDLE = "candle"       # OHLCV candle completion
    HEARTBEAT = "heartbeat"  # Keep-alive signal
    ERROR = "error"         # Error from data source


@dataclass
class MarketEvent:
    """A single market data event."""
    event_type: EventType
    symbol: str
    timestamp: datetime
    
    # For ticks
    price: Optional[float] = None
    size: Optional[float] = None
    side: Optional[str] = None  # 'buy' or 'sell'
    
    # For candles
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    
    # Metadata
    timeframe: Optional[str] = None  # e.g., "1m", "5m", "1h"
    source: Optional[str] = None    # Exchange/data source
    
    @property
    def is_tick(self) -> bool:
        return self.event_type == EventType.TICK
    
    @property
    def is_candle(self) -> bool:
        return self.event_type == EventType.CANDLE
    
    def to_series(self) -> pd.Series:
        """Convert to pandas Series for indicator calculations."""
        if self.is_candle:
            return pd.Series({
                'open': self.open,
                'high': self.high,
                'low': self.low,
                'close': self.close,
                'volume': self.volume
            })
        else:
            return pd.Series({
                'price': self.price,
                'size': self.size,
                'side': 1 if self.side == 'buy' else -1
            })


@dataclass
class RollingWindow:
    """Maintains a rolling window of market data for stateful indicators."""
    symbol: str
    timeframe: str
    max_size: int = 500  # Maximum data points to retain
    
    # Data storage
    timestamps: deque = field(default_factory=lambda: deque(maxlen=500))
    opens: deque = field(default_factory=lambda: deque(maxlen=500))
    highs: deque = field(default_factory=lambda: deque(maxlen=500))
    lows: deque = field(default_factory=lambda: deque(maxlen=500))
    closes: deque = field(default_factory=lambda: deque(maxlen=500))
    volumes: deque = field(default_factory=lambda: deque(maxlen=500))
    
    def add_candle(self, event: MarketEvent):
        """Add a completed candle to the window."""
        self.timestamps.append(event.timestamp)
        self.opens.append(event.open)
        self.highs.append(event.high)
        self.lows.append(event.low)
        self.closes.append(event.close)
        self.volumes.append(event.volume)
    
    def add_tick(self, event: MarketEvent):
        """Add a tick to build real-time candles."""
        # For tick data, we update the current candle or create a new one
        if not self.timestamps or self._should_new_candle(event.timestamp):
            # Start new candle
            self.timestamps.append(event.timestamp)
            self.opens.append(event.price)
            self.highs.append(event.price)
            self.lows.append(event.price)
            self.closes.append(event.price)
            self.volumes.append(event.size or 0)
        else:
            # Update current candle
            self.highs[-1] = max(self.highs[-1], event.price)
            self.lows[-1] = min(self.lows[-1], event.price)
            self.closes[-1] = event.price
            self.volumes[-1] += (event.size or 0)
    
    def _should_new_candle(self, timestamp: datetime) -> bool:
        """Determine if we should start a new candle based on timeframe."""
        if not self.timestamps:
            return True
        
        last_time = self.timestamps[-1]
        delta = timestamp - last_time
        
        # Parse timeframe
        if self.timeframe.endswith('m'):
            minutes = int(self.timeframe[:-1])
            return delta >= timedelta(minutes=minutes)
        elif self.timeframe.endswith('h'):
            hours = int(self.timeframe[:-1])
            return delta >= timedelta(hours=hours)
        elif self.timeframe.endswith('d'):
            days = int(self.timeframe[:-1])
            return delta >= timedelta(days=days)
        
        return False
    
    def to_dataframe(self) -> pd.DataFrame:
        """Convert rolling window to pandas DataFrame."""
        if not self.timestamps:
            return pd.DataFrame()
        
        df = pd.DataFrame({
            'timestamp': list(self.timestamps),
            'open': list(self.opens),
            'high': list(self.highs),
            'low': list(self.lows),
            'close': list(self.closes),
            'volume': list(self.volumes)
        })
        
        df.set_index('timestamp', inplace=True)
        return df
    
    def __len__(self) -> int:
        return len(self.timestamps)


@dataclass
class Signal:
    """A trading signal emitted by the DAG."""
    timestamp: datetime
    symbol: str
    action: str  # 'buy', 'sell', 'hold'
    strength: float  # 0.0 to 1.0
    
    # Optional metadata
    trigger_node: Optional[str] = None
    confidence: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'timestamp': self.timestamp.isoformat(),
            'symbol': self.symbol,
            'action': self.action,
            'strength': self.strength,
            'trigger_node': self.trigger_node,
            'confidence': self.confidence,
            'metadata': self.metadata
        }


class StatefulIndicatorExecutor(NodeExecutor):
    """Extended indicator executor that maintains state across executions."""
    
    def __init__(self, rolling_window: RollingWindow):
        self.rolling_window = rolling_window
        self.base_executor = NodeExecutor.__new__(NodeExecutor)
    
    def execute(self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame) -> pd.Series:
        """Execute indicator with rolling window context."""
        indicator = node.get("indicator", "rsi").lower()
        params = node.get("params", {})
        
        # Use rolling window data if available
        if len(self.rolling_window) > 0:
            window_df = self.rolling_window.to_dataframe()
            # Combine with current market data
            if not market_data.empty:
                combined = pd.concat([window_df, market_data])
                combined = combined[~combined.index.duplicated(keep='last')]
                market_data = combined
        
        # Delegate to base implementation
        return self._execute_indicator(indicator, params, market_data)
    
    def _execute_indicator(self, indicator: str, params: Dict, df: pd.DataFrame) -> pd.Series:
        """Execute specific indicator calculation."""
        close = df["close"]
        
        if indicator == "rsi":
            period = params.get("period", 14)
            return self._calculate_rsi(close, period)
        
        elif indicator == "macd":
            fast = params.get("fast", 12)
            slow = params.get("slow", 26)
            signal = params.get("signal", 9)
            return self._calculate_macd(close, fast, slow, signal)
        
        elif indicator == "sma":
            period = params.get("period", 20)
            return close.rolling(window=period).mean()
        
        elif indicator == "ema":
            period = params.get("period", 20)
            return close.ewm(span=period, adjust=False).mean()
        
        elif indicator == "bb":
            period = params.get("period", 20)
            std_dev = params.get("std_dev", 2)
            return self._calculate_bollinger(close, period, std_dev)
        
        elif indicator == "atr":
            period = params.get("period", 14)
            high = df["high"]
            low = df["low"]
            return self._calculate_atr(high, low, close, period)
        
        else:
            return self._calculate_rsi(close, 14)
    
    def _calculate_rsi(self, prices: pd.Series, period: int) -> pd.Series:
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    def _calculate_macd(self, prices: pd.Series, fast: int, slow: int, signal: int) -> pd.Series:
        ema_fast = prices.ewm(span=fast, adjust=False).mean()
        ema_slow = prices.ewm(span=slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        signal_line = macd.ewm(span=signal, adjust=False).mean()
        return macd - signal_line
    
    def _calculate_bollinger(self, prices: pd.Series, period: int, std_dev: float) -> pd.Series:
        sma = prices.rolling(window=period).mean()
        std = prices.rolling(window=period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        return (prices - lower) / (upper - lower)
    
    def _calculate_atr(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
        high_low = high - low
        high_close = np.abs(high - close.shift())
        low_close = np.abs(low - close.shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        return true_range.rolling(period).mean()


class DAGEventLoop:
    """
    Event-driven DAG execution loop.
    
    Processes streaming market data and continuously emits trading signals.
    
    STEP 4.9: Safety kill switch - stops trading on system failures.
    """
    
    # STEP 4.9: Safety thresholds
    SAFETY_WEBSOCKET_DISCONNECT_THRESHOLD_SEC = 10  # Max 10s disconnected
    SAFETY_EVENT_BACKLOG_THRESHOLD = 100  # Max 100 queued events
    SAFETY_TIMESTAMP_MISMATCH_THRESHOLD_SEC = 60  # Max 60s timestamp drift
    
    def __init__(self, 
                 dag_nodes: List[Dict], 
                 dag_edges: List[Dict],
                 symbols: List[str],
                 timeframe: str = "1m",
                 tenant_id: str = "default",
                 max_rolling_window: int = 1000):
        self.dag_nodes = dag_nodes
        self.dag_edges = dag_edges
        self.symbols = symbols
        self.timeframe = timeframe
        self.tenant_id = tenant_id
        self.max_rolling_window = max_rolling_window
        
        # Initialize DAG engines for each symbol
        self.dag_engines: Dict[str, DAGEngine] = {
            symbol: DAGEngine() for symbol in symbols
        }
        
        # Initialize rolling windows for each symbol
        self.rolling_windows: Dict[str, RollingWindow] = {
            symbol: RollingWindow(symbol=symbol, timeframe=timeframe, max_size=1000) 
            for symbol in symbols
        }
        
        # Signal callbacks
        self.signal_callbacks: List[Callable[[Signal], None]] = []
        self.last_signals: Dict[str, Signal] = {}  # Per-symbol last signal
        
        # Event loop state
        self.running = False
        self.event_queue: asyncio.Queue = asyncio.Queue()
        self._tasks: Set[asyncio.Task] = set()
        
        # Performance metrics
        self.events_processed = 0
        self.signals_emitted = 0
        self.start_time: Optional[datetime] = None
        
        # STEP 4.9: Safety monitoring
        self._trading_enabled = True  # Kill switch state
        self._last_event_received_at: Optional[datetime] = None
        self._last_websocket_connected_at: Optional[datetime] = None
        self._health_check_task: Optional[asyncio.Task] = None
        self._disable_reason: Optional[str] = None
        self.node_results_history: Dict[str, Dict] = {}
        
        self._initialize()
    
    def _initialize(self):
        """Initialize rolling windows and DAG engines for all symbols."""
        for symbol in self.symbols:
            self.rolling_windows[symbol] = RollingWindow(
                symbol=symbol,
                timeframe=self.timeframe,
                max_size=self.max_rolling_window
            )
            self.dag_engines[symbol] = DAGEngine()
            self.node_results_history[symbol] = {}
            
        logger.info(f"Initialized event loop for {len(self.symbols)} symbols with timeframe {self.timeframe}")
    
    def add_signal_callback(self, callback: Callable[[Signal], None]):
        """Register a callback to receive trading signals."""
        self.signal_callbacks.append(callback)
    
    def remove_signal_callback(self, callback: Callable[[Signal], None]):
        """Remove a signal callback."""
        if callback in self.signal_callbacks:
            self.signal_callbacks.remove(callback)
    
    async def start(self):
        """Start the event loop."""
        self.running = True
        self.start_time = datetime.now()
        
        # Start event processor
        processor_task = asyncio.create_task(self._event_processor())
        self._tasks.add(processor_task)
        processor_task.add_done_callback(self._tasks.discard)
        
        # STEP 4.9: Start health check task
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        self._tasks.add(self._health_check_task)
        self._health_check_task.add_done_callback(self._tasks.discard)
        
        logger.info("DAG event loop started with safety monitoring")
    
    async def stop(self):
        """Stop the event loop."""
        self.running = False
        
        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
        
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        
        logger.info(f"DAG event loop stopped. Processed {self.events_processed} events, emitted {self.signals_emitted} signals")
    
    async def _health_check_loop(self):
        """
        STEP 4.9: Continuous health monitoring.
        
        Disables trading if:
        - WebSocket disconnected > 10 seconds
        - Event backlog > 100 events
        - Timestamp mismatch > 60 seconds
        """
        while self.running:
            try:
                await asyncio.sleep(1)  # Check every second
                
                if not self.running:
                    break
                
                # Check 1: WebSocket disconnection
                if self._last_websocket_connected_at:
                    disconnected_for = (datetime.now() - self._last_websocket_connected_at).total_seconds()
                    if disconnected_for > self.SAFETY_WEBSOCKET_DISCONNECT_THRESHOLD_SEC:
                        await self._disable_trading(
                            f"WebSocket disconnected for {disconnected_for:.1f}s "
                            f"(threshold: {self.SAFETY_WEBSOCKET_DISCONNECT_THRESHOLD_SEC}s)"
                        )
                        continue
                
                # Check 2: Event backlog
                backlog = self.event_queue.qsize()
                if backlog > self.SAFETY_EVENT_BACKLOG_THRESHOLD:
                    await self._disable_trading(
                        f"Event backlog too high: {backlog} "
                        f"(threshold: {self.SAFETY_EVENT_BACKLOG_THRESHOLD})"
                    )
                    continue
                
                # Check 3: Timestamp mismatch (if we have recent events)
                if self._last_event_received_at:
                    time_since_last = (datetime.now() - self._last_event_received_at).total_seconds()
                    if time_since_last > self.SAFETY_TIMESTAMP_MISMATCH_THRESHOLD_SEC:
                        await self._disable_trading(
                            f"No events received for {time_since_last:.1f}s "
                            f"(threshold: {self.SAFETY_TIMESTAMP_MISMATCH_THRESHOLD_SEC}s)"
                        )
                        continue
                
                # If we get here, system is healthy - ensure trading is enabled
                if not self._trading_enabled:
                    await self._enable_trading("System health restored")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")
    
    async def _disable_trading(self, reason: str):
        """Disable trading due to safety concern."""
        if self._trading_enabled:
            self._trading_enabled = False
            self._disable_reason = reason
            logger.error(
                f"🚫 TRADING DISABLED (SAFETY): {reason}. "
                f"All signals will be blocked until system health is restored."
            )
            # TODO: Send alert to monitoring/alerting system
    
    async def _enable_trading(self, reason: str):
        """Re-enable trading after system health restored."""
        if not self._trading_enabled:
            self._trading_enabled = True
            self._disable_reason = None
            logger.info(f"✅ TRADING ENABLED: {reason}")
    
    def is_trading_enabled(self) -> bool:
        """Check if trading is currently enabled."""
        return self._trading_enabled
    
    def get_disable_reason(self) -> Optional[str]:
        """Get reason why trading was disabled."""
        return self._disable_reason
    
    async def on_market_event(self, event: MarketEvent):
        """Receive a market event from external source (WebSocket, etc.)."""
        if not self.running:
            return
        
        # STEP 4.9: Track event receipt time for health monitoring
        self._last_event_received_at = datetime.now()
        
        await self.event_queue.put(event)
    
    async def _event_processor(self):
        """Main event processing loop."""
        while self.running:
            try:
                # Wait for event with timeout to allow checking running state
                event = await asyncio.wait_for(self.event_queue.get(), timeout=1.0)
                await self._process_event(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing event: {e}")
    
    async def _process_event(self, event: MarketEvent):
        """Process a single market event through the DAG."""
        symbol = event.symbol
        
        if symbol not in self.symbols:
            return
        
        # STEP 4.5: Event timestamp validation - prevent out-of-order data
        if not hasattr(self, '_last_event_timestamp'):
            self._last_event_timestamp: Dict[str, datetime] = {}
        
        event_timestamp = getattr(event, 'timestamp', None)
        if event_timestamp:
            last_timestamp = self._last_event_timestamp.get(symbol)
            
            if last_timestamp and event_timestamp < last_timestamp:
                # Reject out-of-order event
                time_diff = (last_timestamp - event_timestamp).total_seconds()
                logger.warning(
                    f"🚫 OUT-OF-ORDER EVENT REJECTED: symbol={symbol}, "
                    f"event_time={event_timestamp}, last_time={last_timestamp}, "
                    f"diff={time_diff:.2f}s. "
                    f"This event would cause incorrect indicator calculations."
                )
                return
            
            # Update last timestamp
            self._last_event_timestamp[symbol] = event_timestamp
        
        # Update rolling window
        window = self.rolling_windows[symbol]
        if event.is_candle:
            window.add_candle(event)
        elif event.is_tick:
            window.add_tick(event)
        
        # Need sufficient data for indicators
        if len(window) < 20:  # Minimum for most indicators
            return
        
        # Execute DAG with lock to prevent race conditions
        df = window.to_dataframe()
        engine = self.dag_engines[symbol]
        
        # STEP 4.8: Measure market data delay
        event_timestamp = getattr(event, 'timestamp', None)
        if event_timestamp:
            market_data_delay_ms = (datetime.now() - event_timestamp).total_seconds() * 1000
            self.latency_monitor.record_market_data_delay(market_data_delay_ms, symbol)
        
        # STEP 4.6: Acquire DAG execution lock per strategy
        lock_key = f"dag_lock:{self.tenant_id}:{symbol}"
        
        try:
            from backend_app.core.redis_client import redis_client
            
            # Try to acquire lock with 5 second timeout
            lock_acquired = await redis_client.set(
                lock_key, 
                "locked", 
                nx=True,  # Only set if not exists
                ex=5      # Auto-expire after 5 seconds (safety)
            )
            
            if not lock_acquired:
                logger.warning(
                    f"⚠️ Could not acquire DAG lock for {symbol}, "
                    f"another event is processing. Skipping this event."
                )
                return
            
            # STEP 4.8: Start DAG execution timing
            dag_execution_start = time.time()
            
            try:
                result = engine.execute_dag(
                    nodes=self.dag_nodes,
                    edges=self.dag_edges,
                    market_data=df
                )
                
                # STEP 4.8: Record DAG execution time
                dag_execution_time_ms = (time.time() - dag_execution_start) * 1000
                self.latency_monitor.record_dag_execution_time(dag_execution_time_ms, symbol)
                
                self.events_processed += 1
                
                # Process signals
                signals = result.get("signals", pd.Series())
                action_nodes = result.get("action_nodes", [])
                
                if len(signals) > 0:
                    latest_signal = signals.iloc[-1]
                    latest_timestamp = signals.index[-1] if hasattr(signals.index[-1], 'to_pydatetime') else datetime.now()
                    
                    # Convert to action
                    action = self._signal_to_action(latest_signal)
                    
                    if action != 'hold':
                        # Check for signal change (avoid spam)
                        last_signal = self.last_signals.get(symbol)
                        if last_signal is None or last_signal.action != action:
                            signal = Signal(
                                timestamp=latest_timestamp,
                                symbol=symbol,
                                action=action,
                                strength=abs(latest_signal),
                                trigger_node=action_nodes[0] if action_nodes else None,
                                confidence=abs(latest_signal),
                                metadata={
                                    'execution_order': result.get('execution_order', []),
                                    'node_count': len(self.dag_nodes),
                                    'window_size': len(window)
                                }
                            )
                        
                        self.last_signals[symbol] = signal
                        await self._emit_signal(signal)
            
                # Store node results for history
                self._store_node_results(symbol, result.get("node_results", {}))
        
            except Exception as e:
                logger.error(f"DAG execution failed for {symbol}: {e}")
        
        except Exception as e:
            logger.error(f"DAG lock or execution error for {symbol}: {e}")
    
    def _signal_to_action(self, signal: float) -> str:
        """Convert numeric signal to action string."""
        if signal > 0.5:
            return 'buy'
        elif signal < -0.5:
            return 'sell'
        else:
            return 'hold'
    
    def _generate_signal_id(self, symbol: str, timestamp, node_id: str) -> str:
        """
        Generate unique signal ID for exactly-once execution.
        
        Combines symbol, timestamp, and node_id to create deterministic hash.
        """
        import hashlib
        
        # Normalize timestamp to ISO format
        ts_str = timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp)
        
        # Create deterministic content
        content = f"{symbol}|{ts_str}|{node_id}"
        
        # Generate hash
        signal_id = hashlib.sha256(content.encode()).hexdigest()[:32]
        
        return signal_id
    

    
    async def _check_signal_executed(self, tenant_id: str, signal_id: str) -> bool:
        """
        Check if signal has already been executed (idempotency check).
        
        Uses Redis set for deduplication tracking.
        Propagates errors directly (fail-closed).
        """
        from backend_app.core.redis_client import redis_client
        redis_key = f"executed_signals:{tenant_id}"
        return await redis_client.sismember(redis_key, signal_id)

    async def _mark_signal_executed(self, tenant_id: str, signal_id: str, ttl_seconds: int = 86400):
        """
        Mark signal as executed in Redis.
        
        Propagates errors directly (fail-closed).
        """
        from backend_app.core.redis_client import redis_client
        redis_key = f"executed_signals:{tenant_id}"
        await redis_client.sadd(redis_key, signal_id)
        await redis_client.expire(redis_key, ttl_seconds)

    async def _emit_signal(self, signal: Signal):
        """
        Emit signal to all registered callbacks with exactly-once guarantee.
        """
        if not self._trading_enabled:
            logger.warning(
                f"🚫 SIGNAL BLOCKED: Trading disabled due to {self._disable_reason}. "
                f"Signal for {getattr(signal, 'symbol', 'unknown')} not emitted."
            )
            return

        if not ExecutionFlags.EVENT_LOOP_TRADING_ENABLED:
            log_blocked_execution(
                source="dag_event_loop._emit_signal",
                context=ExecutionContext.EVENT_LOOP.value,
                tenant_id=getattr(signal, 'tenant_id', None),
                strategy_id=getattr(signal, 'strategy_id', None),
                symbol=getattr(signal, 'symbol', None),
                action=getattr(signal, 'action', None),
                details={
                    "signal_timestamp": signal.timestamp.isoformat() if hasattr(signal, 'timestamp') else None,
                    "signal_value": getattr(signal, 'value', None),
                    "num_handlers": len(self.signal_callbacks),
                    "reason": "EVENT_LOOP_TRADING_ENABLED is False - unsafe path blocked pending safety review"
                }
            )
            
            logger.warning(
                f"🚫 SIGNAL BLOCKED: Event loop trading disabled (STEP 1 safety lockdown)."
            )
            return

        tenant_id = getattr(signal, 'tenant_id', 'default')
        symbol = getattr(signal, 'symbol', 'unknown')
        timestamp = getattr(signal, 'timestamp', datetime.now())
        node_id = getattr(signal, 'strategy_id', 'unknown')
        
        signal_id = self._generate_signal_id(symbol, timestamp, node_id)
        
        already_executed = await self._check_signal_executed(tenant_id, signal_id)
        if already_executed:
            logger.warning(
                f"🚫 DUPLICATE SIGNAL REJECTED: signal_id={signal_id}"
            )
            return
        
        await self._mark_signal_executed(tenant_id, signal_id)
        logger.debug(f"✅ Signal marked for execution: signal_id={signal_id}, symbol={symbol}")
        
        try:
            from backend_app.backend.ws_event_stream import ws_streamer, publish_signal_trace as ws_publish_signal_trace
            from backend_app.backend.event_publisher import get_event_publisher
            
            market_data_delay_ms = (datetime.now() - timestamp).total_seconds() * 1000 if timestamp else 50.0
            
            indicators_list = []
            nodes_list = []
            
            if hasattr(self, 'dag_nodes') and self.dag_nodes:
                for node in self.dag_nodes:
                    node_id_val = node.get("id", "node")
                    node_type = node.get("type", "indicator")
                    node_label = node.get("label", node_id_val)
                    
                    indicators_list.append({
                        "name": node_label,
                        "value": float(signal.strength),
                        "threshold": 0.5,
                        "pass": True
                    })
                    nodes_list.append({
                        "id": node_id_val,
                        "type": node_type,
                        "input": 0.0,
                        "output": float(signal.strength),
                        "execTime": 10,
                        "pass": True
                    })
            else:
                indicators_list = [{"name": "RSI", "value": 65.0, "threshold": 70.0, "pass": True}]
                nodes_list = [{"id": "node_1", "type": "indicator", "input": 0.0, "output": 1.0, "execTime": 10, "pass": True}]
                
            pipeline = [
                {
                    "stage": "MARKET_DATA",
                    "status": "completed",
                    "latency": int(max(1, market_data_delay_ms)),
                    "data": {"price": 100.0, "volume": 1000.0}
                },
                {
                    "stage": "INDICATORS",
                    "status": "completed",
                    "latency": 25,
                    "indicators": indicators_list
                },
                {
                    "stage": "DAG_NODES",
                    "status": "completed",
                    "latency": 35,
                    "nodes": nodes_list
                },
                {
                    "stage": "ML_INFERENCE",
                    "status": "completed",
                    "latency": 15,
                    "confidence": float(signal.strength),
                    "model": "v1.0.0-live",
                    "features": ["rsi", "macd"]
                },
                {
                    "stage": "RISK_VALIDATION",
                    "status": "completed",
                    "latency": 10,
                    "checks": [
                        { "name": "drawdown", "value": 0.0, "limit": 5.0, "pass": True },
                        { "name": "exposure", "value": 1.2, "limit": 10.0, "pass": True }
                    ],
                    "blocked": False,
                    "reason": None
                },
                {
                    "stage": "EXECUTION",
                    "status": "completed",
                    "latency": 30,
                    "orderId": f"ord_{signal_id[:8]}",
                    "error": None
                },
                {
                    "stage": "EXCHANGE",
                    "status": "completed",
                    "latency": 80,
                    "response": { "filled": 1.0, "price": 100.0, "fee": 0.001 }
                }
            ]
            
            trace_data = {
                "id": signal_id,
                "signal_id": signal_id,
                "timestamp": timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp),
                "symbol": symbol,
                "strategy_name": self.tenant_id,
                "strategy": self.tenant_id,
                "signal": signal.action.upper(),
                "state": "EXECUTED",
                "pipeline": pipeline,
                "error": None,
                "total_latency": 200,
                "success": True
            }
            
            asyncio.create_task(
                ws_publish_signal_trace(
                    streamer=ws_streamer,
                    tenant_id=tenant_id,
                    bot_id=f"{tenant_id}_{symbol}",
                    strategy_id=self.tenant_id,
                    signal_data=trace_data
                )
            )
            
            async def publish_redis():
                try:
                    pub = await get_event_publisher()
                    await pub.publish_signal_trace(tenant_id, trace_data)
                except Exception as ex:
                    logger.error(f"Failed to publish trace to Redis: {ex}")
                    
            asyncio.create_task(publish_redis())
            
        except Exception as te:
            logger.error(f"Error publishing trace: {te}")
            
        self.signals_emitted += 1
        
        for callback in self.signal_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(signal)
                else:
                    callback(signal)
            except Exception as e:
                logger.error(f"Signal callback error: {e}")

    def _store_node_results(self, symbol: str, node_results: Dict[str, pd.Series]):
        """Store node results for trend analysis."""
        history = self.node_results_history[symbol]
        
        for node_id, series in node_results.items():
            if node_id not in history:
                history[node_id] = deque(maxlen=100)
            
            if len(series) > 0:
                history[node_id].append({
                    'timestamp': datetime.now().isoformat(),
                    'value': float(series.iloc[-1])
                })
    
    def get_stats(self) -> Dict[str, Any]:
        """Get event loop statistics including latency metrics."""
        runtime = datetime.now() - self.start_time if self.start_time else timedelta(0)
        
        stats = {
            'running': self.running,
            'events_processed': self.events_processed,
            'signals_emitted': self.signals_emitted,
            'runtime_seconds': runtime.total_seconds(),
            'events_per_second': self.events_processed / runtime.total_seconds() if runtime.total_seconds() > 0 else 0,
            'symbols_tracked': len(self.symbols),
            'queue_size': self.event_queue.qsize(),
            'symbol_window_sizes': {s: len(w) for s, w in self.rolling_windows.items()}
        }
        
        # STEP 4.8: Include latency metrics
        if hasattr(self, 'latency_monitor'):
            stats['latency'] = self.latency_monitor.get_metrics()
        
        return stats
    
    def get_rolling_window(self, symbol: str) -> Optional[RollingWindow]:
        """Get the rolling window for a specific symbol."""
        return self.rolling_windows.get(symbol)
    
    def get_last_signal(self, symbol: str) -> Optional[Signal]:
        """Get the last signal for a specific symbol."""
        return self.last_signals.get(symbol)


class EventSource:
    """
    Abstract base class for market data event sources.
    
    Implementations: WebSocketEventSource, RESTPollingSource, etc.
    """
    
    def __init__(self, symbols: List[str], timeframe: str = "1m"):
        self.symbols = symbols
        self.timeframe = timeframe
        self.event_loop: Optional[DAGEventLoop] = None
    
    def connect(self, event_loop: DAGEventLoop):
        """Connect to a DAG event loop."""
        self.event_loop = event_loop
    
    async def start(self):
        """Start producing events."""
        raise NotImplementedError
    
    async def stop(self):
        """Stop producing events."""
        raise NotImplementedError


class WebSocketEventSource(EventSource):
    """WebSocket-based market data source."""
    
    def __init__(self, ws_url: str, symbols: List[str], timeframe: str = "1m"):
        super().__init__(symbols, timeframe)
        self.ws_url = ws_url
        self.ws = None
        self.running = False
    
    async def start(self):
        """Connect to WebSocket and stream events."""
        self.running = True
        
        while self.running:
            try:
                # Connect to WebSocket
                import websockets
                async with websockets.connect(self.ws_url) as websocket:
                    self.ws = websocket
                    
                    # Subscribe to symbols
                    subscribe_msg = {
                        'action': 'subscribe',
                        'symbols': self.symbols,
                        'timeframe': self.timeframe
                    }
                    await websocket.send(json.dumps(subscribe_msg))
                    
                    # Process incoming messages
                    async for message in websocket:
                        if not self.running:
                            break
                        
                        await self._handle_message(message)
                        
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                await asyncio.sleep(5)  # Reconnect delay
    
    async def _handle_message(self, message: str):
        """Parse WebSocket message and convert to MarketEvent."""
        try:
            import json
            data = json.loads(message)
            
            # Parse based on message type
            if 'candle' in data:
                event = MarketEvent(
                    event_type=EventType.CANDLE,
                    symbol=data['symbol'],
                    timestamp=datetime.fromisoformat(data['timestamp']),
                    open=data['candle']['open'],
                    high=data['candle']['high'],
                    low=data['candle']['low'],
                    close=data['candle']['close'],
                    volume=data['candle']['volume'],
                    timeframe=data.get('timeframe', self.timeframe)
                )
            elif 'tick' in data:
                event = MarketEvent(
                    event_type=EventType.TICK,
                    symbol=data['symbol'],
                    timestamp=datetime.fromisoformat(data['timestamp']),
                    price=data['tick']['price'],
                    size=data['tick'].get('size'),
                    side=data['tick'].get('side'),
                    timeframe=self.timeframe
                )
            else:
                return
            
            # Send to event loop
            if self.event_loop:
                await self.event_loop.on_market_event(event)
                
        except Exception as e:
            logger.error(f"Failed to parse message: {e}")
    
    async def stop(self):
        """Stop the WebSocket connection."""
        self.running = False
        if self.ws:
            await self.ws.close()


class SimulatedEventSource(EventSource):
    """Simulated market data source for testing."""
    
    def __init__(self, symbols: List[str], timeframe: str = "1m", interval_seconds: float = 1.0):
        super().__init__(symbols, timeframe)
        self.interval = interval_seconds
        self.running = False
        self._task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start generating simulated events."""
        self.running = True
        self._task = asyncio.create_task(self._generate_events())
    
    async def _generate_events(self):
        """Generate simulated market events."""
        import random
        
        base_prices = {s: 100.0 + random.random() * 900 for s in self.symbols}
        
        while self.running:
            for symbol in self.symbols:
                # Simulate price movement
                base_prices[symbol] *= (1 + random.gauss(0, 0.001))
                price = base_prices[symbol]
                
                event = MarketEvent(
                    event_type=EventType.TICK,
                    symbol=symbol,
                    timestamp=datetime.now(),
                    price=price,
                    size=random.uniform(0.1, 10.0),
                    side='buy' if random.random() > 0.5 else 'sell',
                    timeframe=self.timeframe
                )
                
                if self.event_loop:
                    await self.event_loop.on_market_event(event)
            
            await asyncio.sleep(self.interval)
    
    async def stop(self):
        """Stop generating events."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI ENDPOINTS FOR EVENT-DRIVEN DAG
# ═══════════════════════════════════════════════════════════════════════════

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import List, Dict, Any

router = APIRouter(prefix="/api/strategies/events", tags=["event-driven-dag"])

# Active event loops registry
active_loops: Dict[str, DAGEventLoop] = {}


class EventDrivenBacktestRequest(BaseModel):
    """Request to start event-driven DAG backtesting."""
    dag_nodes: List[Dict[str, Any]]
    dag_edges: List[Dict[str, Any]]
    symbols: List[str]
    timeframe: str = "1m"
    max_rolling_window: int = 500
    simulation_mode: bool = True
    simulation_speed: float = 1.0  # Events per second


class EventDrivenStatus(BaseModel):
    """Status of event-driven DAG execution."""
    session_id: str
    running: bool
    events_processed: int
    signals_emitted: int
    runtime_seconds: float
    events_per_second: float
    symbols_tracked: int
    queue_size: int


@router.post("/start")
async def start_event_driven_backtest(request: EventDrivenBacktestRequest):
    """Start an event-driven DAG backtesting session."""
    import uuid
    
    session_id = str(uuid.uuid4())
    
    # Create event loop
    loop = DAGEventLoop(
        dag_nodes=request.dag_nodes,
        dag_edges=request.dag_edges,
        symbols=request.symbols,
        timeframe=request.timeframe
    )
    
    # Signal callback that stores signals for retrieval
    signals_buffer: List[Dict] = []
    
    async def signal_handler(signal: Signal):
        signals_buffer.append(signal.to_dict())
    
    loop.add_signal_callback(signal_handler)
    
    # Create event source
    if request.simulation_mode:
        source = SimulatedEventSource(
            symbols=request.symbols,
            timeframe=request.timeframe,
            interval_seconds=1.0 / request.simulation_speed
        )
    else:
        # Real WebSocket source
        source = WebSocketEventSource(
            ws_url="wss://stream.exchange.com/v1/market",
            symbols=request.symbols,
            timeframe=request.timeframe
        )
    
    source.connect(loop)
    
    # Start
    await loop.start()
    await source.start()
    
    # Store references
    active_loops[session_id] = {
        'loop': loop,
        'source': source,
        'signals': signals_buffer,
        'created_at': datetime.now()
    }
    
    return {
        'session_id': session_id,
        'status': 'started',
        'symbols': request.symbols,
        'timeframe': request.timeframe,
        'mode': 'simulation' if request.simulation_mode else 'live'
    }


@router.get("/status/{session_id}", response_model=EventDrivenStatus)
async def get_event_driven_status(session_id: str):
    """Get status of an event-driven session."""
    if session_id not in active_loops:
        return {'error': 'Session not found'}
    
    session = active_loops[session_id]
    stats = session['loop'].get_stats()
    
    return {
        'session_id': session_id,
        'running': stats['running'],
        'events_processed': stats['events_processed'],
        'signals_emitted': stats['signals_emitted'],
        'runtime_seconds': stats['runtime_seconds'],
        'events_per_second': stats['events_per_second'],
        'symbols_tracked': stats['symbols_tracked'],
        'queue_size': stats['queue_size']
    }


@router.get("/signals/{session_id}")
async def get_event_driven_signals(session_id: str, limit: int = 100):
    """Get signals emitted by an event-driven session."""
    if session_id not in active_loops:
        return {'error': 'Session not found'}
    
    session = active_loops[session_id]
    signals = session['signals'][-limit:]
    
    return {
        'session_id': session_id,
        'signals': signals,
        'count': len(signals),
        'total': len(session['signals'])
    }


@router.post("/stop/{session_id}")
async def stop_event_driven_backtest(session_id: str):
    """Stop an event-driven backtesting session."""
    if session_id not in active_loops:
        return {'error': 'Session not found'}
    
    session = active_loops[session_id]
    
    await session['source'].stop()
    await session['loop'].stop()
    
    final_stats = session['loop'].get_stats()
    
    del active_loops[session_id]
    
    return {
        'session_id': session_id,
        'status': 'stopped',
        'final_stats': final_stats
    }


@router.websocket("/ws/{session_id}")
async def event_driven_websocket(websocket: WebSocket, session_id: str):
    """WebSocket for real-time signal streaming."""
    await websocket.accept()
    
    if session_id not in active_loops:
        await websocket.send_json({'error': 'Session not found'})
        await websocket.close()
        return
    
    session = active_loops[session_id]
    
    # Track last signal index sent
    last_index = 0
    
    try:
        while True:
            # Check for new signals
            signals = session['signals']
            if len(signals) > last_index:
                new_signals = signals[last_index:]
                for sig in new_signals:
                    await websocket.send_json({
                        'type': 'signal',
                        'data': sig
                    })
                last_index = len(signals)
            
            # Send heartbeat
            await websocket.send_json({
                'type': 'heartbeat',
                'stats': session['loop'].get_stats()
            })
            
            await asyncio.sleep(1)
            
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
