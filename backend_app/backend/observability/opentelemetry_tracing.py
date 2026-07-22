"""
Institutional-Grade OpenTelemetry Integration

Production-ready distributed tracing for the trading system.
Provides end-to-end tracing across all components.

Author: Senior Institutional Systems Architect
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("opentelemetry_tracing")

# OpenTelemetry imports
try:
    from opentelemetry import baggage, trace
    #     from opentelemetry.baggage.propagation import W3CBaggagePropagator
    from opentelemetry.exporter.jaeger.thrift import JaegerExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import \
        OTLPSpanExporter
    from opentelemetry.instrumentation.aiohttp_client import \
        AioHttpClientInstrumentor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.semconv.trace import SpanAttributes, SpanKind
    from opentelemetry.trace.propagation.textmap import TextMapPropagator
    from opentelemetry.trace.status import Status, StatusCode
    
    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False
    logger.warning("OpenTelemetry not available - tracing disabled")


class TraceStatus(Enum):
    """Trace status levels."""
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass
class TraceConfig:
    """Configuration for tracing."""
    service_name: str = "algo-trading"
    service_version: str = "1.0.0"
    environment: str = "production"
    jaeger_endpoint: Optional[str] = None
    otlp_endpoint: Optional[str] = None
    sample_rate: float = 1.0  # 100% sampling for production
    max_batch_size: int = 512
    max_export_batch_size: int = 512
    max_export_timeout_ms: int = 30000
    export_timeout_ms: int = 30000


class TracingManager:
    """Manages OpenTelemetry tracing configuration and operations."""
    
    def __init__(self, config: TraceConfig):
        self.config = config
        self.tracer_provider: Optional[TracerProvider] = None
        self.initialized = False
        
        if not OPENTELEMETRY_AVAILABLE:
            logger.warning("OpenTelemetry dependencies not installed - tracing disabled")
            return
        
        self._initialize_tracing()
    
    def _initialize_tracing(self):
        """Initialize OpenTelemetry tracing."""
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
                    "telemetry.sdk.version": "1.0.0"
                }
            )
            
            # Create tracer provider
            self.tracer_provider = TracerProvider(resource=resource)
            
            # Configure exporters
            exporters = []
            
            # Jaeger exporter
            if self.config.jaeger_endpoint:
                jaeger_exporter = JaegerExporter(
                    endpoint=self.config.jaeger_endpoint,
                    collector_endpoint=f"{self.config.jaeger_endpoint}/api/traces",
                    udp_split_oversized_batch=True
                )
                exporters.append(jaeger_exporter)
                logger.info(f"Jaeger exporter configured: {self.config.jaeger_endpoint}")
            
            # OTLP exporter
            if self.config.otlp_endpoint:
                otlp_exporter = OTLPSpanExporter(
                    endpoint=self.config.otlp_endpoint,
                    insecure=True
                )
                exporters.append(otlp_exporter)
                logger.info(f"OTLP exporter configured: {self.config.otlp_endpoint}")
            
            # Add batch processors
            for exporter in exporters:
                batch_processor = BatchSpanProcessor(
                    exporter,
                    max_queue_size=self.config.max_batch_size,
                    max_export_batch_size=self.config.max_export_batch_size,
                    export_timeout_millis=self.config.max_export_timeout_ms
                )
                self.tracer_provider.add_span_processor(batch_processor)
            
            # Set global tracer provider
            trace.set_tracer_provider(self.tracer_provider)
            
            # Configure propagation
            set_global_textmap(TextMapPropagator())
            
            # Initialize instrumentation
            self._initialize_instrumentation()
            
            self.initialized = True
            logger.info("OpenTelemetry tracing initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize OpenTelemetry tracing: {e}")
    
    def _initialize_instrumentation(self):
        """Initialize automatic instrumentation."""
        try:
            # FastAPI instrumentation
            if 'FastAPIInstrumentor' in globals():
                FastAPIInstrumentor().instrument(
                    tracer_provider=self.tracer_provider,
                    excluded_urls="/health,/metrics,/ready"
                )
                logger.info("FastAPI instrumentation enabled")
            
            # HTTP client instrumentation
            if 'AioHttpClientInstrumentor' in globals():
                AioHttpClientInstrumentor().instrument(
                    tracer_provider=self.tracer_provider
                )
                logger.info("HTTP client instrumentation enabled")
            
            # Redis instrumentation
            if 'RedisInstrumentor' in globals():
                RedisInstrumentor().instrument(
                    tracer_provider=self.tracer_provider
                )
                logger.info("Redis instrumentation enabled")
            
            # SQLAlchemy instrumentation
            if 'SQLAlchemyInstrumentor' in globals():
                SQLAlchemyInstrumentor().instrument(
                    tracer_provider=self.tracer_provider
                )
                logger.info("SQLAlchemy instrumentation enabled")
                
        except Exception as e:
            logger.warning(f"Failed to initialize some instrumentation: {e}")
    
    def get_tracer(self, name: str):
        """Get a tracer instance."""
        if not self.initialized or not OPENTELEMETRY_AVAILABLE:
            return NoOpTracer()
        
        return trace.get_tracer(name)
    
    def shutdown(self):
        """Shutdown the tracing provider."""
        if self.tracer_provider:
            try:
                # Force flush all spans
                for span_processor in self.tracer_provider._span_processors:
                    if hasattr(span_processor, 'force_flush'):
                        asyncio.create_task(span_processor.force_flush())
                
                # Shutdown
                self.tracer_provider.shutdown()
                logger.info("OpenTelemetry tracing shutdown complete")
            except Exception as e:
                logger.error(f"Error shutting down tracing: {e}")


class NoOpTracer:
    """No-op tracer for when OpenTelemetry is not available."""
    
    @contextmanager
    def start_as_current_span(self, name: str, **kwargs):
        yield NoOpSpan()


class NoOpSpan:
    """No-op span for when OpenTelemetry is not available."""
    
    def set_attribute(self, key: str, value: Any):
        pass
    
    def set_status(self, status: TraceStatus, description: str = ""):
        pass
    
    def add_event(self, name: str, attributes: Dict[str, Any] = None):
        pass
    
    def record_exception(self, exception: Exception):
        pass
    
    def is_recording(self) -> bool:
        return False


class TraceContext:
    """Manages trace context and correlation."""
    
    @staticmethod
    def get_current_trace_id() -> Optional[str]:
        """Get current trace ID."""
        if not OPENTELEMETRY_AVAILABLE:
            return None
        
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            span_context = current_span.get_span_context()
            return span_context.trace_id if span_context else None
        return None
    
    @staticmethod
    def get_current_span_id() -> Optional[str]:
        """Get current span ID."""
        if not OPENTELEMETRY_AVAILABLE:
            return None
        
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            span_context = current_span.get_span_context()
            return span_context.span_id if span_context else None
        return None
    
    @staticmethod
    def set_baggage(key: str, value: str):
        """Set baggage value."""
        if not OPENTELEMETRY_AVAILABLE:
            return
        
        baggage.set_baggage(key, value)
    
    @staticmethod
    def get_baggage(key: str) -> Optional[str]:
        """Get baggage value."""
        if not OPENTELEMETRY_AVAILABLE:
            return None
        
        return baggage.get_baggage(key)
    
    @staticmethod
    def inject_headers(headers: Dict[str, str]) -> Dict[str, str]:
        """Inject trace context into headers."""
        if not OPENTELEMETRY_AVAILABLE:
            return headers
        
        try:
            from opentelemetry.propagate import inject
            inject(headers)
            return headers
        except Exception as e:
            logger.warning(f"Failed to inject trace context: {e}")
            return headers
    
    @staticmethod
    def extract_headers(headers: Dict[str, str]):
        """Extract trace context from headers."""
        if not OPENTELEMETRY_AVAILABLE:
            return
        
        try:
            from opentelemetry.propagate import extract
            extract(headers)
        except Exception as e:
            logger.warning(f"Failed to extract trace context: {e}")


class TradingTracer:
    """Specialized tracer for trading operations."""
    
    def __init__(self, tracing_manager: TracingManager):
        self.tracer = tracing_manager.get_tracer("trading")
        self.initialized = tracing_manager.initialized
    
    @asynccontextmanager
    async def trace_execution(self, 
                            exchange: str, 
                            symbol: str, 
                            side: str, 
                            order_type: str,
                            quantity: float,
                            tenant_id: Optional[str] = None,
                            **attributes):
        """Trace an order execution."""
        if not self.initialized:
            yield NoOpSpan()
            return
        
        span_name = f"execution.{exchange}.{symbol}.{side}"
        
        with self.tracer.start_as_current_span(
            span_name,
            kind=SpanKind.CLIENT,
            attributes={
                SpanAttributes.COMPONENT: "execution",
                "exchange.name": exchange,
                "trading.symbol": symbol,
                "trading.side": side,
                "trading.order_type": order_type,
                "trading.quantity": str(quantity),
                "tenant.id": tenant_id or "unknown",
                **attributes
            }
        ) as span:
            try:
                # Set start time
                span.set_status(Status(StatusCode.OK))
                span.add_event("execution_started", {
                    "exchange": exchange,
                    "symbol": symbol,
                    "side": side,
                    "quantity": str(quantity)
                })
                
                yield span
                
                span.add_event("execution_completed")
                span.set_status(Status(StatusCode.OK))
                
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.add_event("execution_failed", {
                    "error": str(e),
                    "error_type": type(e).__name__
                })
                raise
    
    @asynccontextmanager
    async def trace_risk_validation(self,
                                  validation_type: str,
                                  tenant_id: Optional[str] = None,
                                  **attributes):
        """Trace risk validation."""
        if not self.initialized:
            yield NoOpSpan()
            return
        
        span_name = f"risk_validation.{validation_type}"
        
        with self.tracer.start_as_current_span(
            span_name,
            kind=SpanKind.INTERNAL,
            attributes={
                SpanAttributes.COMPONENT: "risk",
                "risk.validation_type": validation_type,
                "tenant.id": tenant_id or "unknown",
                **attributes
            }
        ) as span:
            try:
                start_time = time.time()
                
                span.add_event("validation_started", {
                    "validation_type": validation_type
                })
                
                yield span
                
                duration_ms = (time.time() - start_time) * 1000
                span.add_event("validation_completed", {
                    "validation_type": validation_type,
                    "duration_ms": duration_ms
                })
                
                span.set_status(Status(StatusCode.OK))
                
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.add_event("validation_failed", {
                    "validation_type": validation_type,
                    "error": str(e)
                })
                raise
    
    @asynccontextmanager
    async def trace_websocket_event(self,
                                   channel: str,
                                   event_type: str,
                                   tenant_id: Optional[str] = None,
                                   **attributes):
        """Trace WebSocket event processing."""
        if not self.initialized:
            yield NoOpSpan()
            return
        
        span_name = f"websocket.{channel}.{event_type}"
        
        with self.tracer.start_as_current_span(
            span_name,
            kind=SpanKind.SERVER,
            attributes={
                SpanAttributes.COMPONENT: "websocket",
                "websocket.channel": channel,
                "websocket.event_type": event_type,
                "tenant.id": tenant_id or "unknown",
                **attributes
            }
        ) as span:
            try:
                span.add_event("websocket_event_started", {
                    "channel": channel,
                    "event_type": event_type
                })
                
                yield span
                
                span.add_event("websocket_event_completed")
                span.set_status(Status(StatusCode.OK))
                
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.add_event("websocket_event_failed", {
                    "channel": channel,
                    "event_type": event_type,
                    "error": str(e)
                })
                raise
    
    @asynccontextmanager
    async def trace_database_query(self,
                                  query_type: str,
                                  database: str,
                                  tenant_id: Optional[str] = None,
                                  **attributes):
        """Trace database query."""
        if not self.initialized:
            yield NoOpSpan()
            return
        
        span_name = f"database.{query_type}"
        
        with self.tracer.start_as_current_span(
            span_name,
            kind=SpanKind.CLIENT,
            attributes={
                SpanAttributes.COMPONENT: "database",
                "db.system": database,
                "db.operation": query_type,
                "tenant.id": tenant_id or "unknown",
                **attributes
            }
        ) as span:
            try:
                start_time = time.time()
                
                span.add_event("query_started", {
                    "query_type": query_type,
                    "database": database
                })
                
                yield span
                
                duration_ms = (time.time() - start_time) * 1000
                span.add_event("query_completed", {
                    "query_type": query_type,
                    "duration_ms": duration_ms
                })
                
                span.set_status(Status(StatusCode.OK))
                
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.add_event("query_failed", {
                    "query_type": query_type,
                    "error": str(e)
                })
                raise


# Global tracing manager
_tracing_manager: Optional[TracingManager] = None
_trading_tracer: Optional[TradingTracer] = None


def initialize_tracing(config: TraceConfig) -> TracingManager:
    """Initialize global tracing manager."""
    global _tracing_manager, _trading_tracer
    
    _tracing_manager = TracingManager(config)
    _trading_tracer = TradingTracer(_tracing_manager)
    
    return _tracing_manager


def get_tracing_manager() -> Optional[TracingManager]:
    """Get global tracing manager."""
    return _tracing_manager


def get_trading_tracer() -> Optional[TradingTracer]:
    """Get global trading tracer."""
    return _trading_tracer


def shutdown_tracing():
    """Shutdown global tracing manager."""
    global _tracing_manager
    
    if _tracing_manager:
        _tracing_manager.shutdown()
        _tracing_manager = None


# Decorators for automatic tracing
def trace_execution(exchange: str, symbol: str, side: str, order_type: str, quantity: float):
    """Decorator to trace execution functions."""
    def decorator(func: Callable):
        async def wrapper(*args, **kwargs):
            tracer = get_trading_tracer()
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


def trace_risk_validation(validation_type: str):
    """Decorator to trace risk validation functions."""
    def decorator(func: Callable):
        async def wrapper(*args, **kwargs):
            tracer = get_trading_tracer()
            if not tracer:
                return await func(*args, **kwargs)
            
            async with tracer.trace_risk_validation(
                validation_type=validation_type,
                function_name=func.__name__
            ):
                return await func(*args, **kwargs)
        
        return wrapper
    return decorator


def trace_websocket_event(channel: str, event_type: str):
    """Decorator to trace WebSocket event functions."""
    def decorator(func: Callable):
        async def wrapper(*args, **kwargs):
            tracer = get_trading_tracer()
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
