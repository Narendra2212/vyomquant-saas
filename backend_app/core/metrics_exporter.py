"""
core/metrics_exporter.py — PROMETHEUS METRICS EXPORTER FOR AUTO-SCALING

STEP 10: Auto-scaling infrastructure requires custom metrics.

This module exposes metrics for Kubernetes HPA and Prometheus monitoring:
- WebSocket connections per pod
- Redis queue sizes
- API request latency
- Custom business metrics

USAGE:
    from backend_app.core.metrics_exporter import metrics_exporter
    
    # Start metrics server
    await metrics_exporter.start(port=8000)
    
    # Update metrics
    metrics_exporter.set_websocket_connections(1500)
    metrics_exporter.set_queue_size("dag_tasks", 250)
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime

# Prometheus client
try:
    from prometheus_client import (
        start_http_server, Gauge, Counter, Histogram,
        Info, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

logger = logging.getLogger("MetricsExporter")


@dataclass
class MetricsExporterConfig:
    """Configuration for metrics exporter."""
    port: int = 8000
    host: str = "0.0.0.0"
    update_interval: float = 15.0  # Update metrics every 15s
    enable_websocket_metrics: bool = True
    enable_queue_metrics: bool = True
    enable_latency_metrics: bool = True


class MetricsExporter:
    """
    Prometheus metrics exporter for auto-scaling.
    
    Exports metrics for:
    - Kubernetes HPA (Horizontal Pod Autoscaler)
    - Prometheus monitoring
    - Custom business metrics
    
    Metrics:
    - websocket_active_connections: Number of active WS connections
    - websocket_connections_per_pod: Average connections per pod
    - redis_queue_length: Length of Redis queues
    - api_request_duration_seconds: API request latency
    - dag_queue_size: DAG task queue size (for HPA)
    - execution_queue_size: Execution queue size (for HPA)
    """
    
    def __init__(self, config: Optional[MetricsExporterConfig] = None):
        self.config = config or MetricsExporterConfig()
        
        if not PROMETHEUS_AVAILABLE:
            logger.warning("[MetricsExporter] Prometheus client not available")
            return
        
        # Create registry
        self._registry = CollectorRegistry()
        
        # WebSocket metrics
        self._websocket_connections = Gauge(
            'websocket_active_connections',
            'Number of active WebSocket connections',
            ['pod', 'shard'],
            registry=self._registry
        )
        
        self._websocket_connections_total = Gauge(
            'websocket_active_connections_total',
            'Total WebSocket connections across all pods',
            registry=self._registry
        )
        
        self._websocket_pods = Gauge(
            'websocket_pod_count',
            'Number of WebSocket server pods',
            registry=self._registry
        )
        
        # Queue metrics
        self._queue_length = Gauge(
            'redis_queue_length',
            'Redis queue length',
            ['queue', 'database'],
            registry=self._registry
        )
        
        self._dag_queue_size = Gauge(
            'dag_queue_size',
            'DAG task queue size (for HPA)',
            registry=self._registry
        )
        
        self._execution_queue_size = Gauge(
            'execution_queue_size',
            'Execution order queue size (for HPA)',
            registry=self._registry
        )
        
        # API metrics
        self._api_requests = Counter(
            'api_requests_total',
            'Total API requests',
            ['method', 'endpoint', 'status'],
            registry=self._registry
        )
        
        self._api_latency = Histogram(
            'api_request_duration_seconds',
            'API request latency',
            ['method', 'endpoint'],
            buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
            registry=self._registry
        )
        
        # Business metrics
        self._active_users = Gauge(
            'trading_active_users',
            'Number of active users',
            registry=self._registry
        )
        
        self._orders_per_second = Gauge(
            'trading_orders_per_second',
            'Order placement rate',
            registry=self._registry
        )
        
        self._trades_executed = Counter(
            'trading_trades_executed_total',
            'Total trades executed',
            ['symbol', 'side'],
            registry=self._registry
        )
        
        # System info
        self._info = Info(
            'trading_app',
            'Trading application information',
            registry=self._registry
        )
        
        # State
        self._running = False
        self._update_task: Optional[asyncio.Task] = None
        self._http_thread = None
        
        logger.info("[MetricsExporter] Initialized")
    
    async def start(self):
        """Start the metrics exporter."""
        if not PROMETHEUS_AVAILABLE:
            logger.warning("[MetricsExporter] Cannot start - Prometheus not available")
            return
        
        self._running = True
        
        # Set app info
        self._info.info({
            'version': '1.0.0',
            'scaling_phase': '1000_users',
        })
        
        # Start HTTP server
        try:
            start_http_server(
                port=self.config.port,
                addr=self.config.host,
                registry=self._registry
            )
            logger.info(
                f"[MetricsExporter] HTTP server started on "
                f"{self.config.host}:{self.config.port}"
            )
        except Exception as e:
            logger.error(f"[MetricsExporter] Failed to start HTTP server: {e}")
            return
        
        # Start background updater
        self._update_task = asyncio.create_task(self._update_loop())
    
    async def stop(self):
        """Stop the metrics exporter."""
        self._running = False
        
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
        
        logger.info("[MetricsExporter] Stopped")
    
    async def _update_loop(self):
        """Background loop to update metrics."""
        while self._running:
            try:
                await self._collect_metrics()
                await asyncio.sleep(self.config.update_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[MetricsExporter] Update error: {e}")
                await asyncio.sleep(1)
    
    async def _collect_metrics(self):
        """Collect and update metrics from various sources."""
        try:
            # Update WebSocket metrics
            if self.config.enable_websocket_metrics:
                await self._update_websocket_metrics()
            
            # Update queue metrics
            if self.config.enable_queue_metrics:
                await self._update_queue_metrics()
            
        except Exception as e:
            logger.error(f"[MetricsExporter] Collect error: {e}")
    
    async def _update_websocket_metrics(self):
        """Update WebSocket connection metrics."""
        try:
            from backend_app.backend.websocket_cluster import get_websocket_cluster_manager
            
            cluster = await get_websocket_cluster_manager()
            stats = cluster.get_cluster_stats()
            
            # Set total connections
            self._websocket_connections_total.set(stats['total_connections'])
            
            # Set per-pod connections
            for instance_stat in stats.get('instance_stats', []):
                pod_id = instance_stat['instance_id']
                connections = instance_stat['total_connections']
                shard_id = instance_stat['shard_id']
                
                self._websocket_connections.labels(
                    pod=pod_id,
                    shard=str(shard_id)
                ).set(connections)
            
            # Set pod count
            self._websocket_pods.set(stats['num_shards'])
            
        except Exception as e:
            logger.debug(f"[MetricsExporter] WebSocket metrics error: {e}")
    
    async def _update_queue_metrics(self):
        """Update Redis queue metrics."""
        try:
            from backend_app.core.redis_cluster import get_redis_cluster_manager, RedisDatabase
            
            redis = await get_redis_cluster_manager()
            
            # DAG queue
            dag_length = await redis.queue_length("dag_tasks")
            self._queue_length.labels(queue='dag_tasks', database='1').set(dag_length)
            self._dag_queue_size.set(dag_length)
            
            # Execution queue
            exec_length = await redis.queue_length("execution_tasks")
            self._queue_length.labels(queue='execution_tasks', database='1').set(exec_length)
            self._execution_queue_size.set(exec_length)
            
        except Exception as e:
            logger.debug(f"[MetricsExporter] Queue metrics error: {e}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # PUBLIC API FOR UPDATING METRICS
    # ═══════════════════════════════════════════════════════════════════════
    
    def set_websocket_connections(self, pod: str, shard: int, count: int):
        """Set WebSocket connections for a specific pod."""
        if not PROMETHEUS_AVAILABLE:
            return
        
        self._websocket_connections.labels(pod=pod, shard=str(shard)).set(count)
    
    def set_websocket_total(self, count: int):
        """Set total WebSocket connections across all pods."""
        if not PROMETHEUS_AVAILABLE:
            return
        
        self._websocket_connections_total.set(count)
    
    def set_queue_size(self, queue_name: str, size: int, database: str = "1"):
        """Set queue size metric."""
        if not PROMETHEUS_AVAILABLE:
            return
        
        self._queue_length.labels(queue=queue_name, database=database).set(size)
        
        # Update HPA-specific metrics
        if queue_name == "dag_tasks":
            self._dag_queue_size.set(size)
        elif queue_name == "execution_tasks":
            self._execution_queue_size.set(size)
    
    def record_api_request(self, method: str, endpoint: str, status: int, duration: float):
        """Record API request metrics."""
        if not PROMETHEUS_AVAILABLE:
            return
        
        status_str = str(status)
        
        # Increment counter
        self._api_requests.labels(method=method, endpoint=endpoint, status=status_str).inc()
        
        # Record latency
        self._api_latency.labels(method=method, endpoint=endpoint).observe(duration)
    
    def set_active_users(self, count: int):
        """Set active users metric."""
        if not PROMETHEUS_AVAILABLE:
            return
        
        self._active_users.set(count)
    
    def set_orders_per_second(self, rate: float):
        """Set orders per second metric."""
        if not PROMETHEUS_AVAILABLE:
            return
        
        self._orders_per_second.set(rate)
    
    def increment_trades(self, symbol: str, side: str, count: int = 1):
        """Increment trade counter."""
        if not PROMETHEUS_AVAILABLE:
            return
        
        self._trades_executed.labels(symbol=symbol, side=side).inc(count)
    
    def get_metrics_text(self) -> str:
        """Get current metrics as Prometheus text format."""
        if not PROMETHEUS_AVAILABLE:
            return ""
        
        return generate_latest(self._registry).decode('utf-8')


# Global singleton
metrics_exporter = MetricsExporter()


def get_metrics_exporter() -> MetricsExporter:
    """Get global metrics exporter instance."""
    return metrics_exporter


# Decorator for API latency tracking
def track_api_latency(endpoint: str):
    """
    Decorator to track API request latency.
    
    Usage:
        @track_api_latency("/api/orders")
        async def place_order(request):
            # Process order
            pass
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            import time
            start = time.time()
            
            try:
                result = await func(*args, **kwargs)
                status = 200
                return result
            except Exception as e:
                status = 500
                raise
            finally:
                duration = time.time() - start
                metrics_exporter.record_api_request(
                    method=func.__name__,
                    endpoint=endpoint,
                    status=status,
                    duration=duration
                )
        
        return wrapper
    return decorator
