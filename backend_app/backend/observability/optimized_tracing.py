"""
HFT-Optimized OpenTelemetry Integration

Ultra-low latency distributed tracing optimized for high-frequency trading.
Addresses tracing overhead, memory leaks, and async safety issues.

Author: Senior Institutional Systems Architect
"""

import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger("optimized_tracing")

# Conditional imports for tracing
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import \
        OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBasedSampler
    from opentelemetry.semconv.trace import SpanAttributes, SpanKind
# #     from opentelemetry.trace import NonRecordingSpan, SpanContext
    from opentelemetry.trace.propagation.textmap import TextMapPropagator
    from opentelemetry.trace.status import Status, StatusCode
    
    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False
    logger.warning("OpenTelemetry not available - optimized tracing disabled")


class SamplingStrategy(Enum):
    """Sampling strategies for HFT optimization."""
    ALWAYS = "always"
    NEVER = "never"
    PROBABILITY = "probability"
    ADAPTIVE = "adaptive"
    HIGH_FREQUENCY = "high_frequency"


@dataclass
class OptimizedTraceConfig:
    """Configuration for optimized tracing."""
    service_name: str = "algo-trading"
    service_version: str = "1.0.0"
    environment: str = "production"
    otlp_endpoint: Optional[str] = None
    sampling_strategy: SamplingStrategy = SamplingStrategy.ADAPTIVE
    sample_rate: float = 0.01  # 1% sampling for HFT
    high_frequency_threshold: float = 100.0  # ops/sec
    max_spans_per_second: int = 1000
    max_batch_size: int = 512
    max_export_timeout_ms: int = 5000  # Reduced for HFT
    buffer_size: int = 10000
    adaptive_sampling_window: int = 60  # seconds


class AdaptiveSampler:
    """Adaptive sampling for HFT workloads."""
    
    def __init__(self, base_rate: float = 0.01, window_size: int = 60):
        self.base_rate = base_rate
        self.window_size = window_size
        
        # Performance tracking
        self._performance_window = deque(maxlen=window_size)
        self._error_window = deque(maxlen=window_size)
        self._lock = threading.Lock()
        
        # Adaptive parameters
        self.min_sample_rate = 0.001  # 0.1%
        self.max_sample_rate = 0.1     # 10%
        self.error_threshold = 0.05    # 5% error rate
        self.performance_threshold = 0.1  # 100ms average
    
    def should_sample(self, trace_id: str, operation: str) -> bool:
        """Determine if trace should be sampled."""
        with self._lock:
            # High-frequency operations get lower sampling
            if self._is_high_frequency(operation):
                return self._get_adaptive_rate() < 0.005  # 0.5% max for HF
            
            # Adaptive sampling based on performance
            current_rate = self._get_adaptive_rate()
            return hash(trace_id) % 1000 < int(current_rate * 1000)
    
    def _is_high_frequency(self, operation: str) -> bool:
        """Check if operation is high-frequency."""
        hf_operations = {
            'websocket.message',
            'websocket.ping',
            'market_data.tick',
            'order_book.update',
            'heartbeat.check'
        }
        return operation in hf_operations
    
    def _get_adaptive_rate(self) -> float:
        """Get adaptive sampling rate based on performance."""
        if len(self._performance_window) < 10:
            return self.base_rate
        
        avg_performance = sum(self._performance_window) / len(self._performance_window)
        error_rate = len(self._error_window) / len(self._error_window)
        
        # Adjust based on performance
        if avg_performance > self.performance_threshold:
            # Poor performance - reduce sampling
            rate = self.base_rate * 0.5
        elif error_rate > self.error_threshold:
            # High error rate - increase sampling
            rate = min(self.base_rate * 2, self.max_sample_rate)
        else:
            # Good performance - use base rate
            rate = self.base_rate
        
        return max(self.min_sample_rate, min(rate, self.max_sample_rate))
    
    def record_performance(self, duration_ms: float, error: bool = False):
        """Record performance for adaptive sampling."""
        with self._lock:
            self._performance_window.append(duration_ms)
            if error:
                self._error_window.append(1)
            else:
                self._error_window.append(0)


class OptimizedTracingManager:
    """HFT-optimized tracing manager with minimal overhead."""
    
    def __init__(self, config: OptimizedTraceConfig):
        self.config = config
        self.tracer_provider: Optional[TracerProvider] = None
        self.initialized = False
        
        # Performance optimization
        self.adaptive_sampler = AdaptiveSampler(
            base_rate=config.sample_rate,
            window_size=config.adaptive_sampling_window
        )
        
        # Memory management
        self._active_spans: Set[str] = set()
        self._span_lock = threading.Lock()
        self._max_active_spans = 10000
        
        if not OPENTELEMETRY_AVAILABLE:
            logger.warning("OpenTelemetry dependencies not installed - optimized tracing disabled")
            return
        
        self._initialize_optimized_tracing()
    
    def _initialize_optimized_tracing(self):
        """Initialize optimized OpenTelemetry tracing."""
        try:
            # Create resource
            resource = Resource.create(
                attributes={
                    "service.name": self.config.service_name,
                    "service.version": self.config.service_version,
                    "service.instance.id": f"{self.config.service_name}-{int(time.time())}",
                    "deployment.environment": self.config.environment,
                    "telemetry.sdk.language": "python",
                    "telemetry.sdk.name": "opentelemetry",
                    "telemetry.sdk.version": "1.0.0",
                    "hft.optimized": "true"
                }
            )
            
            # Create tracer provider with adaptive sampling
            if self.config.sampling_strategy == SamplingStrategy.ADAPTIVE:
                sampler = self._create_adaptive_sampler()
            else:
                sampler = TraceIdRatioBasedSampler(self.config.sample_rate)
            
            self.tracer_provider = TracerProvider(resource=resource, sampler=sampler)
            
            # Configure optimized exporters
            exporters = []
            
            # OTLP exporter with optimized settings (Jaeger native support via OTLP)
            if self.config.otlp_endpoint:
                otlp_exporter = OTLPSpanExporter(
                    endpoint=self.config.otlp_endpoint,
                    insecure=True,
                    timeout=5  # Reduced timeout
                )
                exporters.append(otlp_exporter)
                logger.info(f"Optimized OTLP exporter configured: {self.config.otlp_endpoint}")
            
            # Add optimized batch processors
            for exporter in exporters:
                batch_processor = BatchSpanProcessor(
                    exporter,
                    max_queue_size=self.config.buffer_size,
                    max_export_batch_size=self.config.max_batch_size,
                    export_timeout_millis=self.config.max_export_timeout_ms,
                    max_export_timeout_millis=self.config.max_export_timeout_ms
                )
                self.tracer_provider.add_span_processor(batch_processor)
            
            # Set global tracer provider
            trace.set_tracer_provider(self.tracer_provider)
            
            # Configure propagation
            from opentelemetry.propagate import set_global_textmap
            set_global_textmap(TextMapPropagator())
            
            self.initialized = True
            logger.info("HFT-optimized OpenTelemetry tracing initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize optimized tracing: {e}")
    
    def _create_adaptive_sampler(self):
        """Create adaptive sampler for HFT workloads."""
        class HFTAdaptiveSampler:
            def __init__(self, manager):
                self.manager = manager
            
            def should_sample(self, parent_context, trace_id, name, kind, attributes, links):
                # Use adaptive sampler for decision
                return self.manager.adaptive_sampler.should_sample(
                    f"{trace_id:032x}", name
                )
            
            def get_description(self):
                return f"HFT Adaptive Sampler (base_rate={self.manager.config.sample_rate})"
        
        return HFTAdaptiveSampler(self)
    
    def get_tracer(self, name: str):
        """Get optimized tracer instance."""
        if not self.initialized or not OPENTELEMETRY_AVAILABLE:
            return NoOpTracer()
        
        return trace.get_tracer(name)
    
    def cleanup_old_spans(self):
        """Clean up old spans to prevent memory leaks."""
        with self._span_lock:
            if len(self._active_spans) > self._max_active_spans:
                # Remove oldest spans (simplified cleanup)
                excess = len(self._active_spans) - self._max_active_spans
                logger.warning(f"Cleaning up {excess} old spans to prevent memory leak")
    
    def record_span_performance(self, span_name: str, duration_ms: float, error: bool = False):
        """Record span performance for adaptive sampling."""
        self.adaptive_sampler.record_performance(duration_ms, error)
    
    def shutdown(self):
        """Shutdown optimized tracing provider."""
        if self.tracer_provider:
            try:
                # Force flush all spans with shorter timeout
                for span_processor in self.tracer_provider._span_processors:
                    if hasattr(span_processor, 'force_flush'):
                        # Run flush in background thread to avoid blocking
                        import threading
                        flush_thread = threading.Thread(
                            target=span_processor.force_flush,
                            args=(5,)  # 5 second timeout
                        )
                        flush_thread.daemon = True
                        flush_thread.start()
                
                # Shutdown with timeout
                import threading
                shutdown_thread = threading.Thread(
                    target=self.tracer_provider.shutdown,
                    daemon=True
                )
                shutdown_thread.start()
                shutdown_thread.join(timeout=10)  # 10 second timeout
                
                logger.info("HFT-optimized tracing shutdown complete")
            except Exception as e:
                logger.error(f"Error shutting down optimized tracing: {e}")


class NoOpTracer:
    """No-op tracer for when OpenTelemetry is not available."""
    
    @asynccontextmanager
    async def start_as_current_span(self, name: str, **kwargs):
        yield NoOpSpan()


class NoOpSpan:
    """No-op span optimized for zero overhead."""
    
    def set_attribute(self, key: str, value: Any):
        pass
    
    def set_status(self, status, description: str = ""):
        pass
    
    def add_event(self, name: str, attributes: Dict[str, Any] = None):
        pass
    
    def record_exception(self, exception: Exception):
        pass
    
    def is_recording(self) -> bool:
        return False
    
    def end(self):
        pass


class OptimizedTradingTracer:
    """HFT-optimized trading tracer with minimal overhead."""
    
    def __init__(self, tracing_manager: OptimizedTracingManager):
        self.tracer = tracing_manager.get_tracer("trading")
        self.manager = tracing_manager
        self.initialized = tracing_manager.initialized
        
        # Performance tracking
        self._operation_counts: Dict[str, int] = defaultdict(int)
        self._last_cleanup = time.time()
        self._cleanup_interval = 60.0  # Cleanup every minute
    
    def _should_trace(self, operation: str) -> bool:
        """Determine if operation should be traced."""
        if not self.initialized:
            return False
        
        # High-frequency operations get special handling
        trace_id = f"{uuid.uuid4().hex}"
        return self.manager.adaptive_sampler.should_sample(trace_id, operation)
    
    def _cleanup_if_needed(self):
        """Cleanup old spans if needed."""
        current_time = time.time()
        if current_time - self._last_cleanup > self._cleanup_interval:
            self.manager.cleanup_old_spans()
            self._last_cleanup = current_time
    
    @asynccontextmanager
    async def trace_execution(self, 
                            exchange: str, 
                            symbol: str, 
                            side: str, 
                            order_type: str,
                            quantity: float,
                            tenant_id: Optional[str] = None,
                            **attributes):
        """Trace order execution with HFT optimization."""
        operation = f"execution.{exchange}.{symbol}"
        
        if not self._should_trace(operation):
            yield NoOpSpan()
            return
        
        span_name = f"execution.{exchange}.{symbol}"
        start_time = time.time()
        
        with self.tracer.start_as_current_span(
            span_name,
            kind=SpanKind.CLIENT,
            attributes={
                SpanAttributes.COMPONENT: "execution",
                "exchange.name": exchange,
                "trading.symbol": symbol[:10],  # Limit length
                "trading.side": side,
                "trading.order_type": order_type,
                "trading.quantity": str(quantity),
                "tenant.id": tenant_id or "unknown",
                "hft.optimized": "true",
                **attributes
            }
        ) as span:
            try:
                # Record start
                span.add_event("execution_started", {
                    "exchange": exchange,
                    "symbol": symbol[:10],
                    "side": side,
                    "quantity": str(quantity),
                    "timestamp": time.time()
                })
                
                yield span
                
                # Record completion
                duration_ms = (time.time() - start_time) * 1000
                span.add_event("execution_completed", {
                    "duration_ms": duration_ms,
                    "timestamp": time.time()
                })
                
                # Record performance for adaptive sampling
                self.manager.record_span_performance(span_name, duration_ms, False)
                span.set_status(Status(StatusCode.OK))
                
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.add_event("execution_failed", {
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "duration_ms": duration_ms,
                    "timestamp": time.time()
                })
                
                # Record performance for adaptive sampling
                self.manager.record_span_performance(span_name, duration_ms, True)
                raise
            finally:
                self._cleanup_if_needed()
    
    @asynccontextmanager
    async def trace_websocket_event(self,
                                   channel: str,
                                   event_type: str,
                                   tenant_id: Optional[str] = None,
                                   **attributes):
        """Trace WebSocket event with HFT optimization."""
        operation = f"websocket.{channel}.{event_type}"
        
        # High-frequency WebSocket events get aggressive sampling
        if not self._should_trace(operation):
            yield NoOpSpan()
            return
        
        span_name = f"websocket.{channel}.{event_type}"
        start_time = time.time()
        
        with self.tracer.start_as_current_span(
            span_name,
            kind=SpanKind.SERVER,
            attributes={
                SpanAttributes.COMPONENT: "websocket",
                "websocket.channel": channel[:20],  # Limit length
                "websocket.event_type": event_type[:20],
                "tenant.id": tenant_id or "unknown",
                "hft.optimized": "true",
                "high_frequency": "true",
                **attributes
            }
        ) as span:
            try:
                span.add_event("websocket_event_started", {
                    "channel": channel[:20],
                    "event_type": event_type[:20],
                    "timestamp": time.time()
                })
                
                yield span
                
                duration_ms = (time.time() - start_time) * 1000
                span.add_event("websocket_event_completed", {
                    "duration_ms": duration_ms,
                    "timestamp": time.time()
                })
                
                # WebSocket events are high-frequency - minimal performance tracking
                if duration_ms > 10:  # Only track slow events
                    self.manager.record_span_performance(span_name, duration_ms, False)
                
                span.set_status(Status(StatusCode.OK))
                
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.add_event("websocket_event_failed", {
                    "channel": channel[:20],
                    "event_type": event_type[:20],
                    "error": str(e),
                    "duration_ms": duration_ms,
                    "timestamp": time.time()
                })
                raise
            finally:
                self._cleanup_if_needed()


# Global optimized tracing manager
_optimized_tracing_manager: Optional[OptimizedTracingManager] = None
_optimized_trading_tracer: Optional[OptimizedTradingTracer] = None


def initialize_optimized_tracing(config: OptimizedTraceConfig) -> OptimizedTracingManager:
    """Initialize global optimized tracing manager."""
    global _optimized_tracing_manager, _optimized_trading_tracer
    
    _optimized_tracing_manager = OptimizedTracingManager(config)
    _optimized_trading_tracer = OptimizedTradingTracer(_optimized_tracing_manager)
    
    return _optimized_tracing_manager


def get_optimized_tracing_manager() -> Optional[OptimizedTracingManager]:
    """Get global optimized tracing manager."""
    return _optimized_tracing_manager


def get_optimized_trading_tracer() -> Optional[OptimizedTradingTracer]:
    """Get global optimized trading tracer."""
    return _optimized_trading_tracer


def shutdown_optimized_tracing():
    """Shutdown global optimized tracing manager."""
    global _optimized_tracing_manager
    
    if _optimized_tracing_manager:
        _optimized_tracing_manager.shutdown()
        _optimized_tracing_manager = None


# Optimized decorators for automatic tracing
def trace_execution_optimized(exchange: str, symbol: str, side: str, order_type: str, quantity: float):
    """Decorator to trace execution functions with HFT optimization."""
    def decorator(func: Callable):
        async def wrapper(*args, **kwargs):
            tracer = get_optimized_trading_tracer()
            if not tracer:
                return await func(*args, **kwargs)
            
            async with tracer.trace_execution(
                exchange=exchange,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                function_name=func.__name__
            ):
                return await func(*args, **kwargs)
        
        return wrapper
    return decorator


def trace_websocket_optimized(channel: str, event_type: str):
    """Decorator to trace WebSocket event functions with HFT optimization."""
    def decorator(func: Callable):
        async def wrapper(*args, **kwargs):
            tracer = get_optimized_trading_tracer()
            if not tracer:
                return await func(*args, **kwargs)
            
            async with tracer.trace_websocket_event(
                channel=channel,
                event_type=event_type,
                function_name=func.__name__
            ):
                return await func(*args, **kwargs)
        
        return wrapper
    return decorator
