import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import (Any, Callable, Dict, List, Mapping, Optional, Set, Tuple)

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from backend_app.backend.dag_engine import DAGEngine, NodeExecutor
from backend_app.core.feature_flags import ExecutionContext, ExecutionFlags
from backend_app.core.safety_monitor import log_blocked_execution

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

    #: ``design.md``'s canonical ``Candle.is_closed``, carried on the event so a feed that
    #: knows can say so. ``None`` means "the feed did not say", and the closed-bar rule is
    #: then decided by the interval and the clock in ``market_data_contract``. ``False`` is
    #: **authoritative** and outranks that inference: a feed reporting a forming bar (a
    #: Binance kline update with ``x: false``, for instance) knows something the clock does
    #: not. Added by task 7.10; every existing construction site leaves it unset.
    is_closed: Optional[bool] = None
    
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
    
    def append_bar(
        self,
        timestamp: Any,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
    ) -> bool:
        """Append one **closed** bar. First wins on a repeated timestamp.

        Requirement 19.4 and ``design.md``'s "Duplicate candle | first wins; duplicate
        counted": a bar whose timestamp is already the newest one held, or is older than it,
        is refused rather than appended or overwritten. Before task 7.10 this deque appended
        unconditionally, so a re-sent bar became a second row for one timestamp and a later
        arrival could revise a bar the runtime had already computed a signal from.

        This is the last line rather than the only one - :class:`ClosedBarGate` resolves
        duplicates, ordering and the closed-bar rule at the arrival seam through the
        platform's one ingest contract, and refuses the bar long before it gets here. The
        guard stays because this deque is also written directly (the golden-plan test drives
        ``add_candle`` itself) and because a window with two rows for one timestamp is a
        wrong answer, not a slow one.

        Counting belongs to the seam, not here: ``ClosedBarGate.counters`` is the tally
        Requirements 19.3/19.4/19.5 are about, and a second count in this method would be a
        figure nobody could reconcile with it.
        """
        if self.timestamps:
            try:
                if timestamp <= self.timestamps[-1]:
                    return False
            except TypeError:
                # A tz-aware timestamp meeting tz-naive ones, or the reverse. The ordering
                # of the resulting frame would be undefined, and an indicator computed on an
                # undefined ordering is a wrong answer, so the bar is refused rather than
                # appended on an unchecked comparison.
                logger.error(
                    "RollingWindow %s/%s: refusing a bar whose timestamp cannot be compared "
                    "with the window's own (%r against %r) - mixed timezone awareness.",
                    self.symbol,
                    self.timeframe,
                    timestamp,
                    self.timestamps[-1],
                )
                return False
        self.timestamps.append(timestamp)
        self.opens.append(open_)
        self.highs.append(high)
        self.lows.append(low)
        self.closes.append(close)
        self.volumes.append(volume)
        return True

    def add_candle(self, event: MarketEvent) -> bool:
        """Add a completed candle to the window. ``True`` when it was appended."""
        return self.append_bar(
            event.timestamp,
            event.open,
            event.high,
            event.low,
            event.close,
            event.volume,
        )
    
    def add_tick(self, event: MarketEvent):
        """Refused. A tick is not a bar, and this window feeds indicator computation.

        This method used to build a **forming** candle in place - appending the first tick of
        an interval as a whole bar and then mutating ``highs[-1]`` / ``lows[-1]`` /
        ``closes[-1]`` on every later tick - and ``_process_event`` handed the resulting
        frame straight to the ``DAGEngine``. So one bar timestamp produced a different
        indicator value on every tick, which is precisely what Requirement 19.2 forbids and
        what ``design.md`` means by "the DAG must never compute an indicator on a forming bar
        and then recompute a different value for the same bar". It was the live path's
        forming-bar source, and there is an order router at the end of that path.

        Task 7.10 moved tick aggregation to :class:`TickBarBuilder`, which holds the forming
        bar **outside** this window and releases it to :class:`ClosedBarGate` only once the
        interval has elapsed. Raising here rather than quietly deleting the method follows
        the posture ``market_data_validation.GapHandler._forward_fill`` already takes for a
        repair the platform no longer permits: a caller that still wants tick-by-tick
        indicator values finds out immediately instead of being served them.
        """
        raise RuntimeError(
            "RollingWindow.add_tick is refused: it built a forming candle inside the window "
            "that feeds indicator computation (Requirement 19.2). Route ticks through "
            "ClosedBarGate / TickBarBuilder, which release a bar only once it is closed."
        )
    
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


# ═══════════════════════════════════════════════════════════════════════════
# THE ARRIVAL SEAM ON THE STREAMING PATH (task 7.10, Requirements 19.2-19.4)
# ═══════════════════════════════════════════════════════════════════════════


def normalise_instant(value: Any) -> Optional[pd.Timestamp]:
    """``value`` as a tz-naive UTC ``pd.Timestamp``, or ``None`` when unreadable.

    The same normalisation ``market_data_contract`` applies to an arriving timestamp, reached
    through the contract itself rather than repeated here: one interpretation of "when", or a
    naive local timestamp and a UTC one would silently name different instants.
    """
    from backend_app.backend.market_data_contract import to_utc_naive

    return to_utc_naive(value)


class TickBarBuilder:
    """Aggregates ticks into bars, and holds the in-progress one **outside** the window.

    A tick that has not completed a bar is legitimate input for something - a live price
    display, a stop check, position sizing - and is not input for indicator computation. This
    class is where that distinction lives: :attr:`last_price` is updated by every tick and is
    what a price consumer reads, while a bar is only ever *released* once a later interval's
    tick proves the previous one finished. Nothing here reaches an executor; the released bar
    still has to pass :class:`ClosedBarGate`, which is the authority on whether it is closed.

    Bucketing is arithmetic on the interval, floored against the Unix epoch, so a tick-built
    bar carries the same open time an exchange's own bar for that interval would - which
    matters when a symbol receives both candle and tick events, since a mis-aligned open time
    would look like a duplicate or a late arrival to the gate. The alignment is exact for
    every interval up to ``1d``; for ``3d`` and ``1w`` the epoch grid may not be the grid a
    particular venue publishes weekly bars on, which is a real limitation of building bars
    from ticks at those intervals and not something this class can infer.

    Deliberately not a validator and deliberately not a gate: the closed-bar rule, the
    duplicate rule and the late-event rule are all ``market_data_contract``'s, and this class
    reimplements none of them.
    """

    #: The instant the interval grid is measured from. Naive UTC, matching the contract.
    _EPOCH = pd.Timestamp("1970-01-01")

    def __init__(self, symbol: str, timeframe: str, interval: timedelta):
        self.symbol = symbol
        self.timeframe = str(timeframe)
        self.interval = pd.Timedelta(interval)
        self.open_time: Optional[pd.Timestamp] = None
        self.open: Optional[float] = None
        self.high: Optional[float] = None
        self.low: Optional[float] = None
        self.close: Optional[float] = None
        self.volume: float = 0.0
        #: The most recent trade price seen, whatever bar it belongs to.
        self.last_price: Optional[float] = None
        self.last_price_at: Optional[pd.Timestamp] = None

    def bucket(self, timestamp: Any) -> Optional[pd.Timestamp]:
        """The open time of the bar ``timestamp`` falls in, or ``None`` if unreadable."""
        moment = normalise_instant(timestamp)
        if moment is None:
            return None
        return self._EPOCH + ((moment - self._EPOCH) // self.interval) * self.interval

    def observe(self, price: Any, size: Any, timestamp: Any) -> Optional[Dict[str, Any]]:
        """Fold one tick in. Returns the candle it *completed*, or ``None``.

        Three outcomes, and the third is the one that matters:

        1. The tick belongs to the bar being built -> the bar is updated, nothing released.
        2. The tick opens a later bar -> the bar being built is complete and is returned as a
           candle mapping; the new one starts from this tick.
        3. The tick belongs to an **earlier** bar than the one being built -> ``None``, and
           nothing is mutated. A tick cannot revise a bar this builder has already released,
           because the gate may already have computed a signal from it (Requirement 19.3).
           The drop is counted by the caller, which owns the counters.

        A tick with no readable price, size or timestamp returns ``None`` and changes
        nothing; the caller counts it as malformed.
        """
        moment = normalise_instant(timestamp)
        if moment is None:
            return None
        try:
            value = float(price)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(value):
            return None
        try:
            quantity = 0.0 if size is None else float(size)
        except (TypeError, ValueError):
            quantity = 0.0
        if not np.isfinite(quantity):
            quantity = 0.0

        bucket = self.bucket(moment)
        if bucket is None:
            return None

        if self.open_time is not None and bucket < self.open_time:
            return None

        self.last_price = value
        self.last_price_at = moment

        completed: Optional[Dict[str, Any]] = None
        if self.open_time is None:
            self._start(bucket, value, quantity)
        elif bucket == self.open_time:
            self.high = max(self.high, value)
            self.low = min(self.low, value)
            self.close = value
            self.volume += quantity
        else:
            completed = self.candle()
            self._start(bucket, value, quantity)
        return completed

    def candle(self) -> Optional[Dict[str, Any]]:
        """The bar being built, as the candle mapping the ingest contract parses.

        ``is_closed`` is deliberately **absent**: whether the interval has elapsed is the
        gate's decision, taken against a clock, not this builder's. Returning it as closed
        here would be this class certifying its own output.
        """
        if self.open_time is None:
            return None
        return {
            "open_time": self.open_time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }

    def _start(self, bucket: pd.Timestamp, price: float, size: float) -> None:
        self.open_time = bucket
        self.open = price
        self.high = price
        self.low = price
        self.close = price
        self.volume = size


class ClosedBarGate:
    """One symbol's arrival seam: raw events in, closed bars out. Requirement 19.2.

    The whole point of this class is that it **delegates**. The closed-bar rule, first-wins
    on duplicates, the late-event drop and every counter come from
    ``market_data_contract.ClosedBarIngest`` - the same gate task 7.5 put in front of the node
    preview and the data-quality report, constructed here with ``drop_late=True`` because
    this is a streaming path and ``design.md`` says "re-sort historical; for live, drop late
    events older than the last closed bar and count them". There is no second rule here, no
    second interval table and no second duplicate policy; if the two paths ever disagreed
    about what a closed bar is, a backtest and the deployment of the same version would
    disagree about what it trades.

    What it adds is the two things a stream needs and a batch does not: a clock that moves
    (``ClosedBarIngest.advance_to``) and tick aggregation held outside the executor's window
    (:class:`TickBarBuilder`).
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        *,
        now: Optional[datetime] = None,
    ):
        from backend_app.backend.market_data_contract import ClosedBarIngest

        self.symbol = symbol
        self.timeframe = str(timeframe)
        # `drop_late=True` is not a preference: this gate sits on a path with an order router
        # at the end of it, so a bar arriving older than one already computed on is dropped
        # and counted rather than re-sorted into place (Requirement 19.3).
        self._ingest = ClosedBarIngest(self.timeframe, drop_late=True, now=now)
        self.interval: timedelta = self._ingest.interval
        self.ticks = TickBarBuilder(symbol, self.timeframe, self.interval)

    @property
    def counters(self) -> Any:
        """The ingest contract's own ``IngestCounters``. Nothing re-tallies them."""
        return self._ingest.counters

    @property
    def last_price(self) -> Optional[float]:
        """The most recent trade price, from any tick, closed bar or not."""
        return self.ticks.last_price

    def admit(
        self,
        event: "MarketEvent",
        *,
        now: Optional[datetime] = None,
    ) -> List[Tuple[Any, List[float]]]:
        """The closed bars ``event`` produced, in timestamp order. Usually none or one.

        A candle event is offered to the ingest contract directly. A tick event is folded
        into the forming bar and only the bar it *completes*, if any, is offered. Either way
        the contract decides admission, and what comes back is the bar it accepted -
        ``[(open_time, [open, high, low, close, volume])]``, drained so the gate stays bounded
        over the life of a deployment.
        """
        if now is not None:
            self._ingest.advance_to(now)

        if event.is_candle:
            candle: Optional[Dict[str, Any]] = {
                "open_time": event.timestamp,
                "open": event.open,
                "high": event.high,
                "low": event.low,
                "close": event.close,
                "volume": event.volume,
            }
            if event.is_closed is not None:
                candle["is_closed"] = event.is_closed
        elif event.is_tick:
            candle = self.ticks.observe(event.price, event.size, event.timestamp)
            if candle is None:
                # Either the tick is still inside the forming bar - the ordinary case, and
                # not an event to count - or it was unreadable or older than the bar being
                # built. The first is silence; the other two are counted where they are seen,
                # because only the builder knows which happened.
                return []
        else:
            return []

        if candle is None:
            return []
        self._ingest.offer(candle)
        return self._ingest.drain()

    def count_rejected_event(self, *, late: bool = False) -> None:
        """Record an event the caller dropped before this gate saw it.

        ``DAGEventLoop._process_event`` carries its own out-of-order guard (STEP 4.5) which
        returns before the seam is reached. That guard is a control and stays, but
        Requirement 19.3 asks that a late event be **counted** as well as discarded, so the
        loop reports it into the same tally rather than starting a second one. Written on the
        contract's own public counter fields so there is one set of figures for this path.
        """
        self.counters.offered += 1
        if late:
            self.counters.out_of_order += 1
            self.counters.late_events += 1

    def to_dict(self) -> Dict[str, Any]:
        """What this seam saw, for ``get_stats``. The contract's counters, republished."""
        return {
            "timeframe": self.timeframe,
            "closed_bars_only": True,
            "drop_late": True,
            "counters": self.counters.to_dict(),
            "forming_bar_open_time": (
                self.ticks.open_time.isoformat() if self.ticks.open_time else None
            ),
            "last_price": self.last_price,
        }


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
                 dag_nodes: Optional[List[Dict]] = None,
                 dag_edges: Optional[List[Dict]] = None,
                 symbols: Optional[List[str]] = None,
                 timeframe: str = "1m",
                 tenant_id: str = "default",
                 max_rolling_window: int = 1000,
                 strategy_id: Optional[str] = None,
                 plan: Optional[Any] = None,
                 registry: Any = None,
                 clock: Optional[Callable[[], datetime]] = None):
        """
        Args:
            dag_nodes, dag_edges: loose node/edge lists. LEGACY input, kept so existing
                callers keep working unchanged.
            plan: a ``CompiledPlan``. When supplied it is AUTHORITATIVE: ``dag_nodes``
                and ``dag_edges`` are derived from it and any lists passed alongside are
                ignored. This is how a deployment consumes the persisted
                ``compiled_plan`` rather than a loose dict (task 2.4, Requirement 22.3),
                so a live run and a backtest of the same version execute the same node
                set in the same order (Requirement 22.4).
            registry: the descriptor source the plan is adapted against. ``None`` resolves
                the assembled registry lazily. Passed explicitly by
                :meth:`from_version_row` so the side, operator and window a node executes
                with come from the same registry the plan was loaded against.
            clock: the instant "is this bar closed?" is measured against, returning tz-naive
                UTC. ``None`` reads the wall clock. Injected rather than read directly so a
                test can *state* the instant instead of racing it (task 7.10); a deployment
                never passes it, because a feed that supplies its own notion of now could
                certify its own forming bars as closed.

        Use :meth:`from_version_row` rather than resolving a plan yourself; it applies
        the recompile-only-on-hash-mismatch rule from Requirement 22.5.
        """
        self.plan = plan
        self.dag_hash: Optional[str] = None
        self.warmup_bars: int = 0
        if plan is not None:
            from backend_app.backend.strategy_compiler import plan_to_engine_graph

            derived_nodes, derived_edges = plan_to_engine_graph(plan, registry)
            self.dag_nodes = derived_nodes
            self.dag_edges = derived_edges
            self.dag_hash = plan.dag_hash
            self.warmup_bars = plan.warmup_bars
            logger.info(
                "DAG event loop bound to compiled plan %s: %d nodes, warmup %d bars",
                plan.dag_hash,
                len(derived_nodes),
                plan.warmup_bars,
            )
        else:
            if dag_nodes is None:
                raise ValueError(
                    "DAGEventLoop needs either a compiled plan or a dag_nodes list"
                )
            self.dag_nodes = dag_nodes
            self.dag_edges = dag_edges if dag_edges is not None else []
        self.symbols = symbols if symbols is not None else []
        self.timeframe = timeframe
        self.tenant_id = tenant_id
        self.max_rolling_window = max_rolling_window
        self.strategy_id = strategy_id
        self._clock = clock
        
        # Initialize DAG engines for each symbol
        self.dag_engines: Dict[str, DAGEngine] = {
            symbol: DAGEngine() for symbol in self.symbols
        }
        
        # Initialize rolling windows for each symbol
        self.rolling_windows: Dict[str, RollingWindow] = {
            symbol: RollingWindow(symbol=symbol, timeframe=timeframe, max_size=1000) 
            for symbol in self.symbols
        }

        # The arrival seam, one per symbol (task 7.10). ``None`` for a symbol whose bar
        # interval the market data pipeline cannot measure: see ``_initialize``.
        self.closed_bar_gates: Dict[str, Optional[ClosedBarGate]] = {}
        
        # Signal callbacks
        self.signal_callbacks: List[Callable[[Signal], None]] = []
        self.last_signals: Dict[str, Signal] = {}  # Per-symbol last signal

        # ── the deployment signal path (task 10.3) ──────────────────────────────────
        # `None` until `attach_signal_path` is called, and while it is `None` this loop
        # behaves exactly as it did before task 10.3: nothing below is reached, no plan is
        # executed through `execute_plan`, and `_emit_signal` remains the only emission.
        self._signal_path: Any = None
        #: The descriptor source the plan was adapted against. Stored (it was previously
        #: read and discarded) so `execute_plan` resolves ports, warmups and order
        #: semantics against the SAME registry `plan_to_engine_graph` used above - two
        #: registries would mean two answers about what a node's side or window is.
        self.registry: Any = registry
        #: One `PlanRuntimeState` per symbol, kept ACROSS events. `execute_plan`'s own
        #: contract asks for this ("a long-lived deployment keeps one across
        #: evaluations"), and it is what lets task 8.5's readiness view and task 10.3's
        #: closure gate read the same per-node verdict the last evaluation produced.
        self.plan_runtime_states: Dict[str, Any] = {}
        #: `node_id -> the active model_versions row`, supplied by whoever attached the
        #: signal path. The engine does not go looking for a model version and must not:
        #: an ML node with no entry here is AWAITING_MODEL, which is the fail-closed
        #: direction (Requirement 17.6 of the strategy-builder spec).
        self.model_versions: Mapping[str, Mapping[str, Any]] = {}
        self.artifact_store: Any = None
        
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

    @classmethod
    def from_version_row(
        cls,
        row: Any,
        *,
        symbols: Optional[List[str]] = None,
        timeframe: Optional[str] = None,
        tenant_id: str = "default",
        max_rolling_window: int = 1000,
        strategy_id: Optional[str] = None,
        registry: Any = None,
    ) -> "DAGEventLoop":
        """An event loop bound to a stored version's compiled plan.

        Reads the persisted ``compiled_plan`` and executes it as-is when its identity
        hash still matches the version's graph; recompiles only when it does not
        (Requirement 22.5). A row whose ``compiled_plan`` is absent or NULL - which is
        every existing row until migration 004 part 1 is applied - is compiled from its
        graph rather than rejected.

        ``symbols`` and ``timeframe`` default to what the graph's DATA nodes declare, so
        a live run subscribes to the market the author actually configured rather than to
        a literal (SB-06).

        Raises
            ``strategy_compiler.ValidationError`` when the version's graph no longer
            compiles, and ``CompilerError`` when the row carries no graph. A deployment
            is refused rather than run against a plan nobody validated.
        """
        from backend_app.backend.strategy_compiler import load_plan
        from backend_app.backend.strategy_dag.schema import BlockCategory

        loaded = load_plan(row, registry)
        logger.info(
            "Version plan loaded for event loop: dag_hash=%s reused=%s reason=%s",
            loaded.dag_hash,
            loaded.reused,
            loaded.reason,
        )

        graph_symbols: List[str] = []
        graph_timeframes: List[str] = []
        for node in loaded.graph.nodes:
            if node.category is not BlockCategory.DATA:
                continue
            symbol = node.params.get("symbol")
            frame = node.params.get("timeframe")
            if isinstance(symbol, str) and symbol and symbol not in graph_symbols:
                graph_symbols.append(symbol)
            if isinstance(frame, str) and frame and frame not in graph_timeframes:
                graph_timeframes.append(frame)

        return cls(
            symbols=symbols if symbols is not None else graph_symbols,
            timeframe=(
                timeframe
                if timeframe is not None
                else (graph_timeframes[0] if graph_timeframes else "1m")
            ),
            tenant_id=tenant_id,
            max_rolling_window=max_rolling_window,
            strategy_id=strategy_id,
            plan=loaded.plan,
            registry=registry,
        )

    def _initialize(self):
        """Initialize rolling windows, arrival gates and DAG engines for all symbols."""
        from backend_app.backend.market_data_contract import MarketDataContractError

        for symbol in self.symbols:
            self.rolling_windows[symbol] = RollingWindow(
                symbol=symbol,
                timeframe=self.timeframe,
                max_size=self.max_rolling_window
            )
            self.dag_engines[symbol] = DAGEngine()
            self.node_results_history[symbol] = {}
            try:
                self.closed_bar_gates[symbol] = ClosedBarGate(
                    symbol, self.timeframe, now=self._now()
                )
            except MarketDataContractError as exc:
                # An interval the market data pipeline has no length for cannot be told
                # closed from forming, so this loop computes nothing for that symbol rather
                # than computing on bars it cannot classify. Recorded here and refused per
                # event in ``_ingest_event``; not raised, because a constructor that throws
                # would take down callers that only ever read the loop's configuration.
                self.closed_bar_gates[symbol] = None
                logger.error(
                    "No closed-bar gate for %s at %s (%s: %s). This loop will refuse every "
                    "market event for that symbol: an unmeasurable bar interval cannot be "
                    "told closed from forming (Requirement 19.2).",
                    symbol,
                    self.timeframe,
                    exc.code,
                    exc.message,
                )

        logger.info(f"Initialized event loop for {len(self.symbols)} symbols with timeframe {self.timeframe}")

    def _now(self) -> datetime:
        """The instant the closed-bar rule is measured against, tz-naive UTC."""
        if self._clock is not None:
            return self._clock()
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def _ingest_event(self, event: MarketEvent) -> bool:
        """Route ``event`` through the market data contract. ``True`` if a bar was admitted.

        The one place this runtime turns an arriving event into a row an executor may compute
        on, and the answer to Requirement 19.2 on the streaming path. Before task 7.10 this
        work was two lines inline in :meth:`_process_event` - ``add_candle`` for a candle,
        ``add_tick`` for a tick - and the tick branch built a forming bar inside the window
        that was handed straight to the ``DAGEngine``.

        What happens now:

        * The event goes to :class:`ClosedBarGate`, i.e. to
          ``market_data_contract.ClosedBarIngest`` with ``drop_late=True``. A forming bar, a
          duplicate (first wins) and an arrival older than the last bar computed on are each
          dropped and counted there.
        * Only a bar the contract admitted is appended to the :class:`RollingWindow`.
        * A tick updates the forming bar and the last price, and returns ``False`` until it
          completes an interval. So a strategy that used to be evaluated on every tick is now
          evaluated once per closed bar. That is a real change in cadence and it is the
          correct one: the per-tick evaluations were computing an indicator on a bar that was
          still moving, so two of them for the same bar disagreed.

        Returns ``False`` for a symbol this loop does not track, for a symbol with no usable
        gate, and for every event that produced no closed bar.
        """
        symbol = event.symbol
        window = self.rolling_windows.get(symbol)
        gate = self.closed_bar_gates.get(symbol)
        if window is None or gate is None:
            if window is not None and gate is None:
                logger.warning(
                    "Refusing market event for %s: no closed-bar gate for timeframe %s.",
                    symbol,
                    self.timeframe,
                )
            return False

        admitted = False
        for open_time, values in gate.admit(event, now=self._now()):
            if window.append_bar(open_time, *values):
                admitted = True
        return admitted

    def market_data_state(self) -> Dict[str, Any]:
        """What each symbol's arrival seam has seen. Requirements 19.3, 19.4, 19.5.

        The ingest contract's own counters, republished per symbol and never re-derived, so
        the dropped forming bars, duplicates and late events on a running deployment are
        readable from the loop instead of only from a log line.
        """
        state: Dict[str, Any] = {}
        for symbol, gate in self.closed_bar_gates.items():
            state[symbol] = (
                gate.to_dict()
                if gate is not None
                else {
                    "timeframe": self.timeframe,
                    "closed_bars_only": True,
                    "gate": "unavailable",
                    "reason": "the market data pipeline has no bar length for this interval",
                }
            )
        return state
    
    # ═══════════════════════════════════════════════════════════════════════
    # THE DEPLOYMENT SIGNAL PATH (task 10.3, Requirements 14.3, 14.5 - 14.8)
    # ═══════════════════════════════════════════════════════════════════════

    def attach_signal_path(
        self,
        signal_path: Any,
        *,
        model_versions: Optional[Mapping[str, Mapping[str, Any]]] = None,
        artifact_store: Any = None,
    ) -> None:
        """Route this loop's ACTION-node evaluation into a deployment's signal path.

        ``signal_path`` is a ``signal_service.LiveSignalPath`` for the Deployment this loop
        is running. Attaching it changes ONE thing about this loop, and changes it
        completely: from here on a market event is evaluated through
        ``DAGEngine.execute_plan`` and the resulting Trade_Intents are routed to
        ``generate_signal`` / ``submit_signal``, instead of through ``execute_dag`` and
        ``_emit_signal``.

        WHY IT REPLACES THE LEGACY EMISSION RATHER THAN RUNNING ALONGSIDE IT
            Requirement 11.2 forbids a second order-placement path. Two evaluations of the
            same bar - one through ``execute_dag`` whose ``Signal`` reaches
            ``signal_callbacks``, one through ``execute_plan`` whose intents reach the
            signal path - would be exactly that: two independent decisions about the same
            event, either of which could produce an order. So a deployment gets one, and
            it is the one that carries the readiness gate Requirement 14.3 asks for.

            ``execute_dag`` cannot be that one. It has no ``PlanRuntimeState``, so it
            cannot say whether an ACTION node's upstream closure is ``READY`` - it
            executes every node unconditionally and sums the action series. A signal
            generated from it would be a signal generated from a warming indicator, which
            is what 14.3 exists to prevent. ``execute_plan`` enforces the closure gate
            internally and publishes the per-node verdict, which is why the wiring goes
            through it.

        Requires a compiled plan. A loop built from loose ``dag_nodes`` has no plan to
        execute, no ports to resolve and no warmups to compose, so attaching a signal path
        to one is refused here rather than discovered as silence at the first bar.

        Args:
            signal_path: the deployment's ``LiveSignalPath``.
            model_versions: ``node_id -> the active model_versions row`` for this
                deployment's ML/DL nodes. Absent entries leave those nodes
                ``AWAITING_MODEL``, which holds their actions dormant rather than trading
                on an unverified model.
            artifact_store: artifact store override, threaded to ``model_readiness``.
        """
        if signal_path is None:
            raise ValueError(
                "attach_signal_path(None) is not a way to detach; call "
                "detach_signal_path() so the intent is readable at the call site."
            )
        if self.plan is None:
            raise ValueError(
                "This event loop has no compiled plan, so its ACTION-node evaluation "
                "cannot report whether an action's upstream closure is READY "
                "(Requirement 14.3) and no Signal may be generated from it. Build the "
                "loop with DAGEventLoop.from_version_row (or plan=...) before attaching "
                "a signal path."
            )
        self._signal_path = signal_path
        self.model_versions = dict(model_versions or {})
        self.artifact_store = artifact_store
        self.plan_runtime_states = {}
        logger.info(
            "DAG event loop bound to the signal path of deployment %s: ACTION-node "
            "output now routes through signal_service (plan %s, %d symbol(s)).",
            getattr(signal_path, "deployment_id", "?"),
            self.dag_hash,
            len(self.symbols),
        )

    def detach_signal_path(self) -> None:
        """Stop routing to the signal path. The loop returns to its legacy emission."""
        self._signal_path = None
        self.plan_runtime_states = {}

    @property
    def signal_path(self) -> Any:
        """The attached ``LiveSignalPath``, or ``None``."""
        return self._signal_path

    def _runtime_state_for(self, symbol: str) -> Any:
        """This symbol's ``PlanRuntimeState``, created on first use and kept thereafter."""
        state = self.plan_runtime_states.get(symbol)
        if state is None:
            from backend_app.backend.dag_engine import PlanRuntimeState

            state = PlanRuntimeState(
                model_versions=self.model_versions,
                artifact_store=self.artifact_store,
            )
            self.plan_runtime_states[symbol] = state
        return state

    def _feed_observation_for(self, symbol: str, event: MarketEvent) -> Dict[str, Any]:
        """What this loop can say about ``symbol``'s feed. Measured, never assumed.

        The loop is in the best position to answer Requirement 14.6's question, because it
        is the thing the events arrive at: the age is measured from the newest bar the
        arrival seam actually ADMITTED (not from the newest event received, which may have
        been a forming bar or a duplicate the contract dropped), and the bar count is the
        window an executor would read. The CLASSIFICATION is not done here - `feed_state`
        owns every threshold, and `LiveSignalPath.observe_feed_state` calls it.

        ``connected`` is reported ``True`` only while this loop's own kill switch is closed
        and a qualifying bar has been admitted. That is a narrower claim than "a socket
        object exists", which is the exact failure mode `feed_state`'s docstring names.
        """
        window = self.rolling_windows.get(symbol)
        newest = window.timestamps[-1] if (window is not None and len(window)) else None
        return {
            "symbol": symbol,
            "available_bars": len(window) if window is not None else None,
            "last_event_time": newest if newest is not None else event.timestamp,
            "connected": bool(self._trading_enabled) and newest is not None,
        }

    async def _route_deployment_signals(
        self, symbol: str, engine: DAGEngine, window: pd.DataFrame, event: MarketEvent
    ) -> List[Any]:
        """Evaluate the plan for one event and route its ACTION output. Never raises.

        ``design.md`` -> the Live_Runtime signal path. Requirements 14.3, 14.5, 14.6,
        14.7, 14.8.

        THE THREE THINGS THIS METHOD IS, IN ORDER
            1. **The feed gate.** One measurement per event, classified by `feed_state` and
               applied to the deployment's signal path, which suspends or resumes
               generation on a transition (Requirement 14.6). A suspended path still gets
               the evaluation - node readiness stays observable during an outage, the same
               disposition `execute_plan` takes for a SYSTEM FREEZE - but generates nothing.
            2. **The evaluation.** `execute_plan` against the plan this loop is bound to
               and the `PlanRuntimeState` kept for this symbol, so warmup and readiness
               accumulate across events instead of restarting on each one.
            3. **The routing.** Every returned Trade_Intent goes to the signal path, each
               contained independently.

        WHY THE ENTIRE BODY IS INSIDE ONE ``except``
            Requirement 14.7: "a node evaluation error on one event SHALL NOT halt the
            Live_Runtime's processing of later events". `execute_plan` raises - a
            `DAGExecutionError` for an unusable window or an inconsistent plan, an
            `ExecutionBlocked` when the platform's execution guard refuses a triggered
            intent, and whatever an executor raised for this bar. All of them are facts
            about THIS event. They are recorded against the Deployment through
            `LiveSignalPath.record_evaluation_failure` and processing continues, which is
            the requirement stated as code.

            `asyncio.CancelledError` is deliberately not caught: a cancelled task is the
            loop being shut down, not an event failing, and swallowing it would make
            `stop()` unable to stop this method.
        """
        path = self._signal_path
        outcomes: List[Any] = []
        if path is None:  # pragma: no cover - callers check, this is the second guard
            return outcomes

        try:
            observation = self._feed_observation_for(symbol, event)
            report = path.observe_feed_state(**observation)
            live = await path.apply_feed_state(report)

            state = self._runtime_state_for(symbol)
            intents = engine.execute_plan(
                self.plan, window, state, registry=self.registry
            )
            self._record_node_history(symbol, engine)

            if not intents:
                return outcomes
            if not live:
                # Evaluated, deliberately not routed. The intents are real, but Requirement
                # 14.6 forbids generating a Signal from stale or gapped market data, and
                # the path's own gate is what refuses - asked here so the refusal is
                # counted and reported per action rather than silently skipped.
                return await path.on_action_outputs(
                    intents, plan=self.plan, runtime_state=state
                )

            return await path.on_action_outputs(
                intents,
                plan=self.plan,
                runtime_state=state,
                market_context=self._market_context_for(symbol, window, event),
            )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - Requirement 14.7, contained per event
            outcomes.append(
                await path.record_evaluation_failure(
                    exc,
                    stage="plan evaluation",
                    symbol=symbol,
                    event_time=getattr(event, "timestamp", None),
                )
            )
            return outcomes

    def _record_node_history(self, symbol: str, engine: DAGEngine) -> None:
        """Store this evaluation's node results for trend analysis. Never raises.

        Guarded, unlike the legacy branch's own call, because on this path it runs BEFORE
        the routing: ``_store_node_results`` coerces each node's latest value with
        ``float()``, and a node whose primary value is a frame or a feature matrix rather
        than a numeric series would raise. A trend-analysis buffer must not be able to
        suppress a Signal - the history is an observability convenience, the intent is the
        decision.
        """
        try:
            self._store_node_results(symbol, engine.node_results)
        except Exception as exc:  # noqa: BLE001 - a history buffer never fails a decision
            logger.warning(
                "Could not store node results for %s (%s); the evaluation itself stands.",
                symbol,
                exc,
            )

    def _market_context_for(
        self, symbol: str, window: pd.DataFrame, event: MarketEvent
    ) -> Dict[str, Any]:
        """The market facts this event's decision was made against.

        The bar the plan was evaluated on, not the event that arrived: after task 7.10 an
        event and the bar it produced are different things (a tick completes a bar that
        opened earlier, a forming bar is dropped), and the audit answer to "what did this
        decision see" is the closed bar the executors read.
        """
        gate = self.closed_bar_gates.get(symbol)
        context: Dict[str, Any] = {"symbol": symbol, "timeframe": self.timeframe}
        if window is not None and not window.empty:
            context["bar_time"] = str(window.index[-1])
            if "close" in window.columns:
                close = window["close"].iloc[-1]
                if close is not None and close == close:  # not NaN
                    context["price"] = float(close)
        if gate is not None and gate.last_price is not None:
            context["last_price"] = float(gate.last_price)
        event_time = getattr(event, "timestamp", None)
        if event_time is not None:
            context["event_time"] = (
                event_time.isoformat()
                if hasattr(event_time, "isoformat")
                else str(event_time)
            )
        tracked = self.rolling_windows.get(symbol)
        context["bars_in_window"] = 0 if tracked is None else len(tracked)
        return {k: v for k, v in context.items() if v is not None}

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
            try:
                from backend_app.core.state import app_state
                if hasattr(app_state, "telemetry") and app_state.telemetry:
                    await app_state.telemetry.emit_alert("TRADING_DISABLED", {"reason": reason})
            except Exception as e:
                logger.warning(f"Could not publish safety alert: {e}")
    
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
    
    async def _acquire_dag_lock(self, symbol: str) -> bool:
        """STEP 4.6's per-symbol DAG execution lock. ``True`` when this event holds it.

        Extracted so the legacy emission and the deployment signal path take the SAME lock
        under the SAME key with the same 5-second safety expiry. Two evaluations of one
        symbol must not overlap whichever path they are on, and a second lock helper with
        its own key would have permitted exactly that.

        Raises whatever Redis raises: the caller treats an unreachable lock as "this event
        is not evaluated", which is what the pre-existing handler already did.
        """
        from backend_app.core.cache.redis_manager import redis_manager

        redis_client = await redis_manager.get_client()
        acquired = await redis_client.set(
            f"dag_lock:{self.tenant_id}:{symbol}",
            "locked",
            nx=True,  # Only set if not exists
            ex=5,     # Auto-expire after 5 seconds (safety)
        )
        return bool(acquired)

    async def _process_event_for_deployment(
        self, symbol: str, window: RollingWindow, event: MarketEvent
    ) -> List[Any]:
        """One event, for a loop with a deployment signal path attached (task 10.3).

        The deployment counterpart of the legacy branch in :meth:`_process_event`, and it
        keeps that branch's two surrounding controls unchanged - the market-data delay
        measurement and the per-symbol DAG execution lock - because both are about the
        event, not about what is done with the result.

        The lock is taken here for the same reason it is taken there: two overlapping
        evaluations of one symbol would compute against two different windows and could
        each produce an intent for the same bar. A lock that cannot be reached means this
        event is not evaluated, which is the safe direction and is what the legacy branch
        already did with it.

        Returns the routing outcomes, so a caller (and a test) can see what one event did
        without reading a log. ``events_processed`` is incremented for an event that was
        actually evaluated, matching the legacy branch's own accounting.
        """
        engine = self.dag_engines[symbol]

        # STEP 4.8: Measure market data delay
        self._record_market_data_delay(symbol, getattr(event, "timestamp", None))

        try:
            if not await self._acquire_dag_lock(symbol):
                logger.warning(
                    "⚠️ Could not acquire DAG lock for %s, another event is processing. "
                    "Skipping this event.",
                    symbol,
                )
                return []
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - an unreachable lock is not an evaluation
            logger.error("DAG lock error for %s: %s", symbol, exc)
            return []

        outcomes = await self._route_deployment_signals(
            symbol, engine, window.to_dataframe(), event
        )
        self.events_processed += 1
        self.signals_emitted += sum(
            1 for outcome in outcomes if getattr(outcome, "generated", False)
        )
        return outcomes

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
                # Discarded here, and counted where every other late event on this path is
                # counted (Requirement 19.3 asks for both). This guard runs before the
                # arrival seam, so without this the tally would be missing exactly the
                # events the guard caught.
                gate = self.closed_bar_gates.get(symbol)
                if gate is not None:
                    gate.count_rejected_event(late=True)
                return
            
            # Update last timestamp
            self._last_event_timestamp[symbol] = event_timestamp
        
        # Update the rolling window through the market data contract: closed bars only, first
        # wins on a duplicate, late arrivals dropped and counted (Requirements 19.2 - 19.4).
        # A tick that has not completed a bar returns False, so nothing below runs for it.
        window = self.rolling_windows[symbol]
        if not self._ingest_event(event):
            return

        # Task 10.3: a deployment's signal path replaces the legacy emission for this
        # loop entirely - see `attach_signal_path` on why one evaluation per event rather
        # than two. Nothing below this branch runs for a deployment, and nothing in this
        # branch runs for a loop with no signal path attached.
        #
        # The `len(window) < 20` floor below is deliberately NOT applied here. On the plan
        # path readiness is decided per node against the compiler's own composed warmup
        # (WARMING until the window reaches it), and the feed gate needs to see a short
        # window to report INSUFFICIENT_DATA for it (Requirement 14.6). A blanket floor
        # would hide both facts for the first twenty bars of every deployment.
        if self._signal_path is not None:
            await self._process_event_for_deployment(symbol, window, event)
            return

        # Need sufficient data for indicators
        if len(window) < 20:  # Minimum for most indicators
            return
        
        # Execute DAG with lock to prevent race conditions
        df = window.to_dataframe()
        engine = self.dag_engines[symbol]
        
        # STEP 4.8: Measure market data delay
        self._record_market_data_delay(symbol, getattr(event, 'timestamp', None))
        
        try:
            # STEP 4.6: Acquire DAG execution lock per strategy
            if not await self._acquire_dag_lock(symbol):
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
                monitor = getattr(self, 'latency_monitor', None)
                if monitor is not None:
                    monitor.record_dag_execution_time(dag_execution_time_ms, symbol)
                
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
                        # Check for signal change (avoid spam). The two statements below sat
                        # OUTSIDE this branch and read `signal` unconditionally, so a second
                        # bar carrying the same action raised `UnboundLocalError` and was
                        # logged as a DAG execution failure. Emission is unchanged - a
                        # repeated action still emits nothing, which is what "avoid spam"
                        # means - but it is now the intended silence rather than a crash.
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
                                    'execution_order': (
                                        list(self.plan.execution_order)
                                        if self.plan is not None
                                        else result.get('execution_order', [])
                                    ),
                                    'dag_hash': self.dag_hash,
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
    
    def _record_market_data_delay(self, symbol: str, event_timestamp: Any) -> None:
        """Record how stale ``event_timestamp`` is, if a latency monitor is attached.

        Two defects were in the three lines this replaces, and both stopped the DAG from
        running at all rather than producing a wrong number. ``self.latency_monitor`` is
        never assigned by ``__init__`` - ``get_stats`` already guards with ``hasattr`` for the
        same reason - so recording it raised ``AttributeError`` on the first event carrying a
        timestamp, and ``_event_processor``'s ``except Exception`` logged it as an unexplained
        processing error. And ``datetime.now()`` is naive local while an event timestamp may
        be tz-aware, which raises ``TypeError`` on the subtraction and is also why the
        instant is taken from ``_now`` (UTC) rather than from local time.

        A missing monitor is silence here on purpose: a measurement nobody collects must not
        be able to stop a strategy from being evaluated.
        """
        monitor = getattr(self, 'latency_monitor', None)
        if monitor is None or not event_timestamp:
            return
        moment = normalise_instant(event_timestamp)
        if moment is None:
            return
        delay_ms = (pd.Timestamp(self._now()) - moment).total_seconds() * 1000
        try:
            monitor.record_market_data_delay(delay_ms, symbol)
        except Exception as exc:  # a metrics sink must never stop an evaluation
            logger.warning(f"Could not record market data delay for {symbol}: {exc}")

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
        import inspect
        from backend_app.core.cache.redis_manager import redis_manager
        redis_client = await redis_manager.get_client()
        redis_key = f"executed_signals:{tenant_id}"
        res = redis_client.sismember(redis_key, signal_id)
        if inspect.isawaitable(res):
            res = await res
        return bool(res)

    async def _mark_signal_executed(self, tenant_id: str, signal_id: str, ttl_seconds: int = 86400):
        """
        Mark signal as executed in Redis.
        
        Propagates errors directly (fail-closed).
        """
        import inspect
        from backend_app.core.cache.redis_manager import redis_manager
        redis_client = await redis_manager.get_client()
        redis_key = f"executed_signals:{tenant_id}"
        res1 = redis_client.sadd(redis_key, signal_id)
        if inspect.isawaitable(res1):
            await res1
        res2 = redis_client.expire(redis_key, ttl_seconds)
        if inspect.isawaitable(res2):
            await res2

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
                "🚫 SIGNAL BLOCKED: Event loop trading disabled (STEP 1 safety lockdown)."
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
        
        # ════════════════════════════════════════════════════════════════════
        # NO LEGACY ``signal_trace`` BROADCAST FROM HERE (task 14.3, Req 23.7)
        # ════════════════════════════════════════════════════════════════════
        # This method used to publish a signal-status frame TWICE for every
        # emitted Signal: once in-process via
        # ``ws_event_stream.publish_signal_trace`` (channel
        # ``ChannelType.SIGNAL_TRACE``) and once via Redis through
        # ``EventPublisher.publish_signal_trace``, which ``ws_server.py``'s
        # ``_redis_listener`` fans straight back out under that same channel
        # name. Both were deliveries of a signal status update to a client over
        # a FLAT broadcast: ``ChannelType.SIGNAL_TRACE`` names no owner and no
        # deployment, so its subscribers are never authorised against the
        # resource the frame describes. Requirement 23.7 forbids exactly that.
        #
        # Task 14.2 replaced it. A Signal's lifecycle now reaches the
        # Signal_Trace_Page on the owned, per-deployment channel
        # ``signal.{deployment_id}`` - ``signal_service.publish_signal_generated``
        # / ``publish_signal_status_changed`` / ``publish_signal_snapshot``, each
        # published only after the transition is persisted and audited, each
        # carrying Requirement 23.6's per-channel ``seq`` and Requirement 18.4's
        # content-level dedup key.
        #
        # THE CHANNEL ITSELF IS NOT REMOVED. ``ChannelType.SIGNAL_TRACE``,
        # ``CHANNEL_EVENTS[ChannelType.SIGNAL_TRACE]``,
        # ``ws_event_stream.publish_signal_trace`` and
        # ``EventPublisher.publish_signal_trace`` all stay exactly as they are,
        # for any other consumer this spec does not cover. 23.7 retires the
        # channel for THIS traffic, not the channel.
        #
        # NOTHING IS PUBLISHED IN ITS PLACE HERE. This is the LEGACY emission
        # branch - the one ``attach_signal_path`` bypasses entirely - and it
        # holds no deployment identity, so it has no ``signal.{deployment_id}``
        # channel to publish on. A loop that must report signals to the page
        # attaches a ``LiveSignalPath`` (task 10.3) and publishes from there; a
        # loop that has not is one whose ``signal_callbacks`` are the intended
        # consumers, which is what the rest of this method still serves.
        #
        # A defect retired along with it, recorded so it is not reintroduced:
        # the payload this block built was largely FABRICATED. Every pipeline
        # stage was hardcoded ``"completed"`` with an invented latency; the risk
        # checks reported fixed passing numbers; the EXECUTION and EXCHANGE
        # stages claimed an order id and a filled/priced/fee'd exchange response
        # before any order existed; ``state`` was the literal ``"EXECUTED"`` and
        # ``success`` the literal ``True``; and with no ``dag_nodes`` the
        # indicators degraded to a hardcoded ``RSI 65.0``. The SIGNAL_FAMILY
        # frame carries no such invention: it is projected from the persisted
        # record by ``Signal.to_public_dict``.

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
            'symbol_window_sizes': {s: len(w) for s, w in self.rolling_windows.items()},
            # Task 7.10: what the arrival seam dropped, per symbol. The ingest contract's own
            # counters, so the forming bars, duplicates and late events a running deployment
            # refused are readable rather than only logged (Requirements 19.3 - 19.5).
            'market_data': self.market_data_state(),
        }

        # Task 10.3: what the deployment's signal path did and what is currently holding
        # it back. `None` for a loop with no signal path, which is every non-deployment
        # caller. Republished rather than re-derived, so the suspension an operator reads
        # here is the one the path is actually enforcing.
        if self._signal_path is not None:
            to_dict = getattr(self._signal_path, 'to_dict', None)
            stats['signal_path'] = to_dict() if callable(to_dict) else None
            stats['node_runtime_states'] = {
                symbol: state.to_dict()
                for symbol, state in self.plan_runtime_states.items()
                if hasattr(state, 'to_dict')
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
                    # UTC, not local. A naive local timestamp names a different instant from
                    # the UTC one every frame on this platform is indexed in, and the
                    # closed-bar rule compares an arriving open time against a UTC clock: on a
                    # host east of Greenwich a local stamp is in the future, so every bar
                    # built from these ticks would read as still forming and nothing would
                    # ever be evaluated (task 7.10).
                    timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
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
    
    # Verify WebSocket auth — FAIL CLOSED
    # No token → deny. Verification error → deny. Tenant mismatch → deny.
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Authentication required: provide ?token=")
        return

    try:
        from backend_app.core.websocket_auth import _decode_hs256_token
        payload = _decode_hs256_token(token)
        if not payload:
            await websocket.close(code=4003, reason="Invalid or expired authentication token")
            return
        
        user_id = payload.get("sub")
        if not user_id:
            await websocket.close(code=4003, reason="Invalid token: missing user ID")
            return
    except Exception as e:
        logger.warning(f"WebSocket auth failed for session {session_id}: {e}")
        await websocket.close(code=4003, reason="Authentication verification failed")
        return
    
    # Only accept connection after successful authentication
    await websocket.accept()
    
    if session_id not in active_loops:
        await websocket.send_json({'error': 'Session not found'})
        await websocket.close()
        return
    
    session = active_loops[session_id]
    
    # Verify user owns this session
    if session.get('user_id') and str(session.get('user_id')) != str(user_id):
        await websocket.close(code=4003, reason="Unauthorized: session belongs to different user")
        return
    
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
