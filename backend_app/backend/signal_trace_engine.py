"""
Signal Trace Engine for DAG Observability

Allows frontend to visualize:
Market Data → Indicators → DAG Nodes → ML → Risk → Execution → Exchange Response

Author: Senior DAG Observability Engineer
"""

import asyncio
import time
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Callable, Set
from dataclasses import dataclass, field
from enum import Enum, auto
from collections import deque, defaultdict
import json


class TraceStatus(Enum):
    """Signal trace lifecycle status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"


class NodeType(Enum):
    """Types of DAG nodes."""
    MARKET_DATA = "market_data"
    INDICATOR = "indicator"
    OPERATOR = "operator"
    LOGIC = "logic"
    ML_MODEL = "ml_model"
    RISK = "risk"
    EXECUTION = "execution"
    ORDERBOOK = "orderbook"
    TICKER = "ticker"


class ValidationResult(Enum):
    """Node validation results."""
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    BLOCK = "block"


@dataclass
class NodeIO:
    """Node input/output data."""
    key: str
    value: Any
    dtype: str = "float"  # float, int, bool, string, array
    shape: Optional[tuple] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DAGNodeTrace:
    """
    Trace record for a single DAG node execution.
    
    Captures:
    - Node metadata (id, type, label)
    - Execution timing
    - Input/output values
    - Pass/fail status
    - Error details
    """
    node_id: str
    node_type: NodeType
    node_label: str
    
    # Execution timing
    start_time: datetime
    end_time: Optional[datetime] = None
    execution_ms: float = 0.0
    
    # Inputs/Outputs
    inputs: List[NodeIO] = field(default_factory=list)
    outputs: List[NodeIO] = field(default_factory=list)
    
    # Status
    status: ValidationResult = ValidationResult.PASS
    error_message: Optional[str] = None
    
    # Metadata
    cache_hit: bool = False
    retry_count: int = 0
    
    def complete(self, status: ValidationResult, outputs: List[NodeIO] = None):
        """Mark node execution as complete."""
        self.end_time = datetime.now(timezone.utc)
        self.execution_ms = (self.end_time - self.start_time).total_seconds() * 1000
        self.status = status
        if outputs:
            self.outputs = outputs


@dataclass
class MLInferenceTrace:
    """
    Machine learning inference trace.
    
    Captures model prediction details:
    - Model ID and version
    - Feature vector
    - Confidence scores
    - Prediction result
    - Inference latency
    """
    model_id: str
    model_version: str
    
    # Timing
    inference_start: datetime
    inference_end: Optional[datetime] = None
    inference_ms: float = 0.0
    
    # Features
    features: Dict[str, Any] = field(default_factory=dict)
    feature_vector: List[float] = field(default_factory=list)
    
    # Prediction
    prediction: Optional[str] = None  # 'BUY', 'SELL', 'HOLD'
    confidence: float = 0.0
    probabilities: Dict[str, float] = field(default_factory=dict)
    
    # Status
    status: ValidationResult = ValidationResult.PASS
    error_message: Optional[str] = None
    
    def complete(self, prediction: str, confidence: float, probabilities: Dict[str, float] = None):
        """Mark ML inference as complete."""
        self.inference_end = datetime.now(timezone.utc)
        self.inference_ms = (self.inference_end - self.inference_start).total_seconds() * 1000
        self.prediction = prediction
        self.confidence = confidence
        if probabilities:
            self.probabilities = probabilities


@dataclass
class RiskValidationTrace:
    """
    Risk validation trace.
    
    Captures risk checks:
    - Position limits
    - Drawdown checks
    - Exposure limits
    - Signal blocking decisions
    - Kill switch status
    """
    # Checks performed
    checks: List[str] = field(default_factory=list)
    
    # Position data
    current_position: Decimal = Decimal("0")
    position_limit: Decimal = Decimal("0")
    would_exceed: bool = False
    
    # Exposure
    current_exposure: Decimal = Decimal("0")
    exposure_limit: Decimal = Decimal("0")
    exposure_pct: float = 0.0
    
    # Drawdown
    current_drawdown: float = 0.0
    max_drawdown_limit: float = 0.0
    
    # Decision
    passed: bool = True
    blocked: bool = False
    block_reason: Optional[str] = None
    
    # Timing
    validation_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    validation_end: Optional[datetime] = None
    validation_ms: float = 0.0
    
    def complete(self, passed: bool, blocked: bool = False, reason: str = None):
        """Mark risk validation as complete."""
        self.validation_end = datetime.now(timezone.utc)
        self.validation_ms = (self.validation_end - self.validation_start).total_seconds() * 1000
        self.passed = passed
        self.blocked = blocked
        self.block_reason = reason
        if blocked:
            self.status = ValidationResult.BLOCK


@dataclass
class ExecutionTrace:
    """
    Order execution trace.
    
    Captures:
    - Order parameters
    - Exchange response
    - Fill details
    - Latency breakdown
    - Error information
    """
    # Order params
    signal_type: str  # 'BUY' | 'SELL'
    symbol: str
    order_type: str  # 'market' | 'limit'
    requested_size: Decimal
    requested_price: Optional[Decimal] = None
    
    # Exchange response
    exchange: Optional[str] = None
    order_id: Optional[str] = None
    status: str = "pending"  # pending, filled, partial, rejected, error
    
    # Fill details
    filled_size: Decimal = Decimal("0")
    filled_price: Optional[Decimal] = None
    fill_percent: float = 0.0
    fees: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    
    # Latency
    submission_time: Optional[datetime] = None
    response_time: Optional[datetime] = None
    exchange_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    
    # Error
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    
    def record_fill(self, size: Decimal, price: Decimal, fees: Decimal, slippage: Decimal):
        """Record fill details."""
        self.filled_size = size
        self.filled_price = price
        self.fees = fees
        self.slippage = slippage
        if self.requested_size > 0:
            self.fill_percent = float(size / self.requested_size * 100)


@dataclass
class SignalTraceRecord:
    """
    Complete signal trace record.
    
    Represents the entire lifecycle of a signal from
    generation through execution.
    """
    # Identity
    trace_id: str
    signal_id: str
    strategy_id: str
    strategy_name: str
    bot_id: str
    
    # Market context
    symbol: str
    exchange: str
    
    # Timing
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_latency_ms: float = 0.0
    
    # Status
    status: TraceStatus = TraceStatus.PENDING
    final_decision: Optional[str] = None  # 'EXECUTE', 'BLOCK', 'REJECT', 'FAIL'
    
    # Pipeline stages
    node_traces: List[DAGNodeTrace] = field(default_factory=list)
    ml_trace: Optional[MLInferenceTrace] = None
    risk_trace: Optional[RiskValidationTrace] = None
    execution_trace: Optional[ExecutionTrace] = None
    
    # Error tracking
    errors: List[Dict[str, Any]] = field(default_factory=list)
    
    # Metadata
    tags: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def add_node_trace(self, trace: DAGNodeTrace):
        """Add a node execution trace."""
        self.node_traces.append(trace)
    
    def complete(self, status: TraceStatus, decision: str):
        """Mark trace as complete."""
        self.status = status
        self.final_decision = decision
        self.completed_at = datetime.now(timezone.utc)
        if self.started_at:
            self.total_latency_ms = (self.completed_at - self.started_at).total_seconds() * 1000
    
    def to_frontend_format(self) -> Dict[str, Any]:
        """Convert to format expected by frontend."""
        return {
            "id": self.trace_id,
            "signal_id": self.signal_id,
            "strategy": self.strategy_name,
            "symbol": self.symbol,
            "timestamp": self.created_at.isoformat(),
            "status": self.status.value,
            "final_decision": self.final_decision,
            "latency_ms": self.total_latency_ms,
            "pipeline": [
                {
                    "stage": "market_data",
                    "nodes": [n for n in self.node_traces if n.node_type == NodeType.MARKET_DATA],
                    "status": "completed" if any(n.node_type == NodeType.MARKET_DATA for n in self.node_traces) else "pending"
                },
                {
                    "stage": "indicators",
                    "nodes": [n for n in self.node_traces if n.node_type == NodeType.INDICATOR],
                    "status": "completed" if any(n.node_type == NodeType.INDICATOR for n in self.node_traces) else "pending"
                },
                {
                    "stage": "dag_nodes",
                    "nodes": [
                        {
                            "node_id": n.node_id,
                            "type": n.node_type.value,
                            "label": n.node_label,
                            "inputs": [{"key": io.key, "value": str(io.value), "dtype": io.dtype} for io in n.inputs],
                            "outputs": [{"key": io.key, "value": str(io.value), "dtype": io.dtype} for io in n.outputs],
                            "execution_ms": n.execution_ms,
                            "status": n.status.value,
                            "error": n.error_message
                        }
                        for n in self.node_traces
                        if n.node_type not in [NodeType.MARKET_DATA, NodeType.INDICATOR, NodeType.ML_MODEL, NodeType.RISK, NodeType.EXECUTION]
                    ],
                    "status": "completed" if len(self.node_traces) > 2 else "pending"
                },
                {
                    "stage": "ml_inference",
                    "model": self.ml_trace.model_id if self.ml_trace else None,
                    "confidence": self.ml_trace.confidence if self.ml_trace else None,
                    "prediction": self.ml_trace.prediction if self.ml_trace else None,
                    "inference_ms": self.ml_trace.inference_ms if self.ml_trace else None,
                    "status": "completed" if self.ml_trace else "pending"
                },
                {
                    "stage": "risk_validation",
                    "passed": self.risk_trace.passed if self.risk_trace else None,
                    "blocked": self.risk_trace.blocked if self.risk_trace else False,
                    "block_reason": self.risk_trace.block_reason if self.risk_trace else None,
                    "exposure_pct": self.risk_trace.exposure_pct if self.risk_trace else None,
                    "validation_ms": self.risk_trace.validation_ms if self.risk_trace else None,
                    "status": "completed" if self.risk_trace else "pending"
                },
                {
                    "stage": "execution",
                    "order_id": self.execution_trace.order_id if self.execution_trace else None,
                    "filled_size": str(self.execution_trace.filled_size) if self.execution_trace else None,
                    "filled_price": str(self.execution_trace.filled_price) if self.execution_trace else None,
                    "fill_percent": self.execution_trace.fill_percent if self.execution_trace else None,
                    "fees": str(self.execution_trace.fees) if self.execution_trace else None,
                    "slippage": str(self.execution_trace.slippage) if self.execution_trace else None,
                    "exchange_latency_ms": self.execution_trace.exchange_latency_ms if self.execution_trace else None,
                    "status": self.execution_trace.status if self.execution_trace else "pending"
                }
            ],
            "errors": self.errors
        }


class SignalTraceBuffer:
    """
    Async queue for signal trace events.
    
    Non-blocking event buffering with configurable
    batch processing.
    """
    
    def __init__(self, max_size: int = 10000, batch_size: int = 100, flush_interval_ms: float = 100):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._batch_size = batch_size
        self._flush_interval = flush_interval_ms / 1000
        self._running = False
        self._flush_task: Optional[asyncio.Task] = None
        self._handlers: List[Callable[[List[SignalTraceRecord]], None]] = []
    
    async def enqueue(self, trace: SignalTraceRecord) -> bool:
        """
        Add trace to buffer. Non-blocking.
        
        Returns:
            True if enqueued, False if dropped (queue full)
        """
        try:
            self._queue.put_nowait(trace)
            return True
        except asyncio.QueueFull:
            # Drop oldest event
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(trace)
                return True
            except asyncio.QueueEmpty:
                return False
    
    def on_flush(self, handler: Callable[[List[SignalTraceRecord]], None]):
        """Register handler for batched trace processing."""
        self._handlers.append(handler)
    
    async def start(self):
        """Start background flush task."""
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
    
    async def stop(self):
        """Stop buffer and flush remaining."""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self._flush_batch()
    
    async def _flush_loop(self):
        """Background flush loop."""
        while self._running:
            try:
                await asyncio.wait_for(
                    self._queue.get(),
                    timeout=self._flush_interval
                )
                # Check if we have a batch ready
                if self._queue.qsize() >= self._batch_size - 1:
                    await self._flush_batch()
            except asyncio.TimeoutError:
                # Flush on timeout
                await self._flush_batch()
    
    async def _flush_batch(self):
        """Flush batch of traces to handlers."""
        batch: List[SignalTraceRecord] = []
        while len(batch) < self._batch_size:
            try:
                trace = self._queue.get_nowait()
                batch.append(trace)
            except asyncio.QueueEmpty:
                break
        
        if batch:
            for handler in self._handlers:
                try:
                    handler(batch)
                except Exception as e:
                    print(f"Trace handler error: {e}")


class SignalTraceEngine:
    """
    Signal Trace Engine for DAG observability.
    
    Manages the complete signal trace lifecycle from
    creation through completion.
    """
    
    def __init__(
        self,
        max_traces: int = 10000,
        retention_seconds: float = 3600,  # 1 hour default
        batch_size: int = 100
    ):
        self._traces: Dict[str, SignalTraceRecord] = {}
        self._strategy_traces: Dict[str, Set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._max_traces = max_traces
        self._retention_seconds = retention_seconds
        
        # Event buffering
        self._buffer = SignalTraceBuffer(batch_size=batch_size)
        
        # Background cleanup
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
    
    async def start(self):
        """Start the trace engine."""
        await self._buffer.start()
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def stop(self):
        """Stop the trace engine."""
        self._running = False
        await self._buffer.stop()
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
    
    async def start_trace(
        self,
        signal_id: str,
        strategy_id: str,
        strategy_name: str,
        bot_id: str,
        symbol: str,
        exchange: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SignalTraceRecord:
        """
        Start a new signal trace.
        
        Args:
            signal_id: Unique signal identifier
            strategy_id: Parent strategy ID
            strategy_name: Human-readable strategy name
            bot_id: Source bot ID
            symbol: Trading pair
            exchange: Exchange name
            metadata: Additional context
            
        Returns:
            SignalTraceRecord for the new trace
        """
        trace = SignalTraceRecord(
            trace_id=str(uuid.uuid4()),
            signal_id=signal_id,
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            bot_id=bot_id,
            symbol=symbol,
            exchange=exchange,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            status=TraceStatus.RUNNING,
            metadata=metadata or {}
        )
        
        async with self._lock:
            # Enforce retention limit
            if len(self._traces) >= self._max_traces:
                # Remove oldest
                oldest_id = min(self._traces.keys(), key=lambda k: self._traces[k].created_at)
                old_trace = self._traces.pop(oldest_id)
                self._strategy_traces[old_trace.strategy_id].discard(oldest_id)
            
            self._traces[trace.trace_id] = trace
            self._strategy_traces[strategy_id].add(trace.trace_id)
        
        return trace
    
    async def trace_node_execution(
        self,
        trace_id: str,
        node_id: str,
        node_type: NodeType,
        node_label: str,
        inputs: List[NodeIO],
        outputs: List[NodeIO] = None,
        status: ValidationResult = ValidationResult.PASS,
        error_message: str = None,
        cache_hit: bool = False
    ) -> Optional[DAGNodeTrace]:
        """
        Trace a DAG node execution.
        
        Non-blocking - creates trace record and returns immediately.
        """
        trace = DAGNodeTrace(
            node_id=node_id,
            node_type=node_type,
            node_label=node_label,
            start_time=datetime.now(timezone.utc),
            inputs=inputs or [],
            outputs=outputs or [],
            status=status,
            error_message=error_message,
            cache_hit=cache_hit
        )
        
        if outputs:
            trace.complete(status, outputs)
        
        async with self._lock:
            if trace_id in self._traces:
                self._traces[trace_id].add_node_trace(trace)
                return trace
        
        return None
    
    async def trace_ml_inference(
        self,
        trace_id: str,
        model_id: str,
        model_version: str,
        features: Dict[str, Any],
        prediction: str = None,
        confidence: float = 0.0,
        probabilities: Dict[str, float] = None
    ) -> Optional[MLInferenceTrace]:
        """
        Trace ML model inference.
        
        Non-blocking - records inference results.
        """
        ml_trace = MLInferenceTrace(
            model_id=model_id,
            model_version=model_version,
            inference_start=datetime.now(timezone.utc),
            features=features,
            prediction=prediction,
            confidence=confidence,
            probabilities=probabilities or {}
        )
        
        ml_trace.complete(prediction, confidence, probabilities)
        
        async with self._lock:
            if trace_id in self._traces:
                self._traces[trace_id].ml_trace = ml_trace
                return ml_trace
        
        return None
    
    async def trace_risk_validation(
        self,
        trace_id: str,
        checks: List[str],
        position_data: Dict[str, Decimal] = None,
        exposure_data: Dict[str, Any] = None,
        drawdown_data: Dict[str, float] = None,
        passed: bool = True,
        blocked: bool = False,
        block_reason: str = None
    ) -> Optional[RiskValidationTrace]:
        """
        Trace risk validation checks.
        
        Non-blocking - records risk decision.
        """
        position_data = position_data or {}
        exposure_data = exposure_data or {}
        
        risk_trace = RiskValidationTrace(
            checks=checks,
            current_position=position_data.get('current', Decimal("0")),
            position_limit=position_data.get('limit', Decimal("0")),
            would_exceed=position_data.get('would_exceed', False),
            current_exposure=exposure_data.get('current', Decimal("0")),
            exposure_limit=exposure_data.get('limit', Decimal("0")),
            exposure_pct=exposure_data.get('pct', 0.0),
            current_drawdown=drawdown_data.get('current', 0.0) if drawdown_data else 0.0,
            max_drawdown_limit=drawdown_data.get('limit', 0.0) if drawdown_data else 0.0
        )
        
        risk_trace.complete(passed, blocked, block_reason)
        
        async with self._lock:
            if trace_id in self._traces:
                self._traces[trace_id].risk_trace = risk_trace
                if blocked:
                    self._traces[trace_id].status = TraceStatus.BLOCKED
                return risk_trace
        
        return None
    
    async def trace_execution(
        self,
        trace_id: str,
        signal_type: str,
        symbol: str,
        order_type: str,
        size: Decimal,
        price: Decimal = None,
        exchange: str = None,
        order_id: str = None,
        filled_size: Decimal = None,
        filled_price: Decimal = None,
        fees: Decimal = None,
        slippage: Decimal = None,
        exchange_latency_ms: float = 0.0,
        status: str = "pending",
        error_message: str = None
    ) -> Optional[ExecutionTrace]:
        """
        Trace order execution.
        
        Non-blocking - records exchange response.
        """
        exec_trace = ExecutionTrace(
            signal_type=signal_type,
            symbol=symbol,
            order_type=order_type,
            requested_size=size,
            requested_price=price,
            exchange=exchange,
            order_id=order_id,
            status=status,
            exchange_latency_ms=exchange_latency_ms
        )
        
        if filled_size and filled_size > 0:
            exec_trace.record_fill(
                filled_size,
                filled_price or price,
                fees or Decimal("0"),
                slippage or Decimal("0")
            )
        
        if error_message:
            exec_trace.error_message = error_message
            exec_trace.status = "error"
        
        async with self._lock:
            if trace_id in self._traces:
                self._traces[trace_id].execution_trace = exec_trace
                return exec_trace
        
        return None
    
    async def complete_trace(
        self,
        trace_id: str,
        status: TraceStatus,
        decision: str,
        error: str = None
    ) -> Optional[SignalTraceRecord]:
        """
        Mark trace as complete.
        
        Args:
            trace_id: Trace to complete
            status: Final status
            decision: Final decision (EXECUTE, BLOCK, REJECT, FAIL)
            error: Error message if failed
            
        Returns:
            Completed trace record
        """
        async with self._lock:
            if trace_id in self._traces:
                trace = self._traces[trace_id]
                trace.complete(status, decision)
                
                if error:
                    trace.errors.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "message": error
                    })
                
                # Queue for persistence/export
                await self._buffer.enqueue(trace)
                
                return trace
        
        return None
    
    async def get_trace(self, trace_id: str) -> Optional[SignalTraceRecord]:
        """Get trace by ID."""
        async with self._lock:
            return self._traces.get(trace_id)
    
    async def get_recent_traces(
        self,
        strategy_id: str = None,
        limit: int = 50,
        status_filter: List[TraceStatus] = None
    ) -> List[SignalTraceRecord]:
        """Get recent traces with optional filtering."""
        async with self._lock:
            if strategy_id:
                trace_ids = self._strategy_traces.get(strategy_id, set())
                traces = [self._traces[tid] for tid in trace_ids if tid in self._traces]
            else:
                traces = list(self._traces.values())
            
            # Sort by created_at desc
            traces.sort(key=lambda t: t.created_at, reverse=True)
            
            if status_filter:
                traces = [t for t in traces if t.status in status_filter]
            
            return traces[:limit]
    
    async def get_traces_for_frontend(
        self,
        strategy_id: str = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get traces formatted for frontend visualization."""
        traces = await self.get_recent_traces(strategy_id, limit)
        return [t.to_frontend_format() for t in traces]
    
    async def _cleanup_loop(self):
        """Background cleanup of old traces."""
        while self._running:
            await asyncio.sleep(60)  # Check every minute
            
            async with self._lock:
                now = datetime.now(timezone.utc)
                to_remove = []
                
                for trace_id, trace in self._traces.items():
                    age = (now - trace.created_at).total_seconds()
                    if age > self._retention_seconds:
                        to_remove.append(trace_id)
                
                for trace_id in to_remove:
                    trace = self._traces.pop(trace_id, None)
                    if trace:
                        self._strategy_traces[trace.strategy_id].discard(trace_id)


# Global engine instance
trace_engine = SignalTraceEngine()


# Convenience exports
__all__ = [
    'SignalTraceEngine',
    'SignalTraceBuffer',
    'SignalTraceRecord',
    'DAGNodeTrace',
    'MLInferenceTrace',
    'RiskValidationTrace',
    'ExecutionTrace',
    'NodeIO',
    'TraceStatus',
    'NodeType',
    'ValidationResult',
    'trace_engine'
]
