"""
Institutional-Grade Prometheus Metrics Exporter

Production-ready metrics collection for distributed trading system.
Tracks execution, WebSocket, infrastructure, and exchange metrics.

Author: Senior Institutional Systems Architect
"""

import asyncio
import logging
import time
from typing import Any, Dict

import psutil
from prometheus_client import (CollectorRegistry, Counter, Gauge, Histogram,
                               start_http_server)
from prometheus_client.exposition import generate_latest

logger = logging.getLogger("metrics_exporter")


class ExecutionMetrics:
    """Execution-related metrics with institutional-grade tracking."""
    
    def __init__(self, registry: CollectorRegistry):
        # Execution latency metrics with proper buckets for HFT
        self.execution_latency = Histogram(
            'execution_latency_seconds',
            'Time from job submission to completion',
            ['exchange', 'symbol', 'side', 'order_type', 'tenant_id'],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
            registry=registry
        )
        
        self.order_ack_latency = Histogram(
            'order_ack_latency_seconds',
            'Time from order submission to exchange acknowledgment',
            ['exchange', 'symbol', 'side'],
            buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25],
            registry=registry
        )
        
        self.fill_latency = Histogram(
            'fill_latency_seconds',
            'Time from order submission to fill',
            ['exchange', 'symbol', 'side', 'order_type'],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
            registry=registry
        )
        
        # Execution throughput metrics
        self.execution_throughput = Counter(
            'execution_total',
            'Total number of execution attempts',
            ['exchange', 'symbol', 'side', 'status', 'tenant_id'],
            registry=registry
        )
        
        self.execution_success_rate = Gauge(
            'execution_success_rate',
            'Execution success rate (percentage)',
            ['exchange', 'symbol', 'tenant_id'],
            registry=registry
        )
        
        # Rejection metrics
        self.rejection_total = Counter(
            'rejection_total',
            'Total number of order rejections',
            ['exchange', 'symbol', 'reason', 'tenant_id'],
            registry=registry
        )
        
        self.rejection_rate = Gauge(
            'rejection_rate',
            'Order rejection rate (percentage)',
            ['exchange', 'symbol', 'tenant_id'],
            registry=registry
        )
        
        # Worker metrics
        self.worker_active_jobs = Gauge(
            'worker_active_jobs',
            'Number of active jobs per worker',
            ['worker_id', 'exchange'],
            registry=registry
        )
        
        self.worker_job_queue_depth = Gauge(
            'worker_job_queue_depth',
            'Job queue depth per worker',
            ['worker_id'],
            registry=registry
        )
        
        # Risk metrics
        self.risk_validation_latency = Histogram(
            'risk_validation_latency_seconds',
            'Risk validation processing time',
            ['validation_type', 'tenant_id'],
            buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1],
            registry=registry
        )
        
        self.risk_blocks_total = Counter(
            'risk_blocks_total',
            'Total number of risk blocks',
            ['block_reason', 'tenant_id'],
            registry=registry
        )
        
        self.position_limit_utilization = Gauge(
            'position_limit_utilization',
            'Position limit utilization (percentage)',
            ['tenant_id', 'symbol'],
            registry=registry
        )
    
    def record_execution_start(self, exchange: str, symbol: str, side: str, 
                             order_type: str, tenant_id: str):
        """Record execution start."""
        self.execution_throughput.labels(
            exchange=exchange, symbol=symbol, side=side, 
            status='started', tenant_id=tenant_id
        ).inc()
    
    def record_execution_complete(self, exchange: str, symbol: str, side: str,
                                order_type: str, tenant_id: str, latency: float,
                                success: bool):
        """Record execution completion."""
        self.execution_latency.labels(
            exchange=exchange, symbol=symbol, side=side,
            order_type=order_type, tenant_id=tenant_id
        ).observe(latency)
        
        status = 'success' if success else 'failed'
        self.execution_throughput.labels(
            exchange=exchange, symbol=symbol, side=side,
            status=status, tenant_id=tenant_id
        ).inc()
    
    def record_order_ack(self, exchange: str, symbol: str, side: str, latency: float):
        """Record order acknowledgment."""
        self.order_ack_latency.labels(
            exchange=exchange, symbol=symbol, side=side
        ).observe(latency)
    
    def record_fill(self, exchange: str, symbol: str, side: str, 
                    order_type: str, latency: float):
        """Record order fill."""
        self.fill_latency.labels(
            exchange=exchange, symbol=symbol, side=side, order_type=order_type
        ).observe(latency)
    
    def record_rejection(self, exchange: str, symbol: str, reason: str, tenant_id: str):
        """Record order rejection."""
        self.rejection_total.labels(
            exchange=exchange, symbol=symbol, reason=reason, tenant_id=tenant_id
        ).inc()
    
    def update_worker_metrics(self, worker_id: str, exchange: str, 
                             active_jobs: int, queue_depth: int):
        """Update worker metrics."""
        self.worker_active_jobs.labels(
            worker_id=worker_id, exchange=exchange
        ).set(active_jobs)
        
        self.worker_job_queue_depth.labels(
            worker_id=worker_id
        ).set(queue_depth)
    
    def record_risk_validation(self, validation_type: str, tenant_id: str, 
                              latency: float, passed: bool):
        """Record risk validation."""
        self.risk_validation_latency.labels(
            validation_type=validation_type, tenant_id=tenant_id
        ).observe(latency)
        
        if not passed:
            self.risk_blocks_total.labels(
                block_reason=validation_type, tenant_id=tenant_id
            ).inc()


class WebSocketMetrics:
    """WebSocket-related metrics with institutional-grade tracking."""
    
    def __init__(self, registry: CollectorRegistry):
        # Connection metrics
        self.websocket_connections = Gauge(
            'websocket_connections',
            'Number of active WebSocket connections',
            ['channel', 'tenant_id'],
            registry=registry
        )
        
        self.reconnect_rate = Counter(
            'websocket_reconnect_total',
            'Total number of WebSocket reconnections',
            ['exchange', 'symbol', 'reason'],
            registry=registry
        )
        
        self.reconnect_rate_gauge = Gauge(
            'websocket_reconnect_rate',
            'WebSocket reconnection rate per minute',
            ['exchange', 'symbol'],
            registry=registry
        )
        
        # Event metrics
        self.events_total = Counter(
            'websocket_events_total',
            'Total number of WebSocket events',
            ['channel', 'event_type', 'tenant_id'],
            registry=registry
        )
        
        self.events_dropped = Counter(
            'websocket_events_dropped_total',
            'Total number of dropped WebSocket events',
            ['channel', 'reason', 'tenant_id'],
            registry=registry
        )
        
        self.replay_count = Counter(
            'websocket_replay_total',
            'Total number of replay requests',
            ['channel', 'tenant_id'],
            registry=registry
        )
        
        self.replay_events = Counter(
            'websocket_replay_events_total',
            'Total number of events replayed',
            ['channel', 'tenant_id'],
            registry=registry
        )
        
        # Queue metrics
        self.queue_saturation = Gauge(
            'websocket_queue_saturation',
            'WebSocket queue saturation percentage',
            ['queue_type', 'tenant_id'],
            registry=registry
        )
        
        self.queue_depth = Gauge(
            'websocket_queue_depth',
            'WebSocket queue depth',
            ['queue_type', 'tenant_id'],
            registry=registry
        )
        
        # Latency metrics
        self.message_latency = Histogram(
            'websocket_message_latency_seconds',
            'WebSocket message processing latency',
            ['channel', 'event_type'],
            buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25],
            registry=registry
        )
        
        # Reconnect storm detection
        self.reconnect_storm_active = Gauge(
            'websocket_reconnect_storm_active',
            'Active reconnect storms',
            ['exchange'],
            registry=registry
        )
    
    def record_connection(self, channel: str, tenant_id: str, connected: bool):
        """Record WebSocket connection."""
        if connected:
            self.websocket_connections.labels(
                channel=channel, tenant_id=tenant_id
            ).inc()
        else:
            self.websocket_connections.labels(
                channel=channel, tenant_id=tenant_id
            ).dec()
    
    def record_reconnect(self, exchange: str, symbol: str, reason: str):
        """Record WebSocket reconnection."""
        self.reconnect_rate.labels(
            exchange=exchange, symbol=symbol, reason=reason
        ).inc()
    
    def record_event(self, channel: str, event_type: str, tenant_id: str):
        """Record WebSocket event."""
        self.events_total.labels(
            channel=channel, event_type=event_type, tenant_id=tenant_id
        ).inc()
    
    def record_dropped_event(self, channel: str, reason: str, tenant_id: str):
        """Record dropped WebSocket event."""
        self.events_dropped.labels(
            channel=channel, reason=reason, tenant_id=tenant_id
        ).inc()
    
    def record_replay_request(self, channel: str, tenant_id: str):
        """Record replay request."""
        self.replay_count.labels(
            channel=channel, tenant_id=tenant_id
        ).inc()
    
    def record_replay_events(self, channel: str, tenant_id: str, count: int):
        """Record replayed events."""
        self.replay_events.labels(
            channel=channel, tenant_id=tenant_id
        ).inc(count)
    
    def update_queue_metrics(self, queue_type: str, tenant_id: str, 
                            depth: int, max_size: int):
        """Update queue metrics."""
        self.queue_depth.labels(
            queue_type=queue_type, tenant_id=tenant_id
        ).set(depth)
        
        saturation = (depth / max_size) * 100 if max_size > 0 else 0
        self.queue_saturation.labels(
            queue_type=queue_type, tenant_id=tenant_id
        ).set(saturation)
    
    def record_message_latency(self, channel: str, event_type: str, latency: float):
        """Record message processing latency."""
        self.message_latency.labels(
            channel=channel, event_type=event_type
        ).observe(latency)
    
    def record_reconnect_storm(self, exchange: str, active: bool):
        """Record reconnect storm."""
        self.reconnect_storm_active.labels(exchange=exchange).set(1 if active else 0)


class InfrastructureMetrics:
    """Infrastructure-related metrics with institutional-grade tracking."""
    
    def __init__(self, registry: CollectorRegistry):
        # System metrics
        self.cpu_usage = Gauge(
            'cpu_usage_percent',
            'CPU usage percentage',
            ['service', 'instance'],
            registry=registry
        )
        
        self.memory_usage = Gauge(
            'memory_usage_bytes',
            'Memory usage in bytes',
            ['service', 'instance'],
            registry=registry
        )
        
        self.memory_usage_percent = Gauge(
            'memory_usage_percent',
            'Memory usage percentage',
            ['service', 'instance'],
            registry=registry
        )
        
        # Async task metrics
        self.async_tasks_active = Gauge(
            'async_tasks_active',
            'Number of active async tasks',
            ['service', 'instance'],
            registry=registry
        )
        
        self.async_tasks_total = Counter(
            'async_tasks_total',
            'Total number of async tasks created',
            ['service', 'instance', 'task_type'],
            registry=registry
        )
        
        self.async_task_duration = Histogram(
            'async_task_duration_seconds',
            'Async task duration',
            ['service', 'instance', 'task_type'],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
            registry=registry
        )
        
        # Database metrics
        self.database_connections = Gauge(
            'database_connections',
            'Number of database connections',
            ['database', 'service'],
            registry=registry
        )
        
        self.database_query_duration = Histogram(
            'database_query_duration_seconds',
            'Database query duration',
            ['database', 'query_type', 'service'],
            buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25],
            registry=registry
        )
        
        # Cache metrics
        self.cache_hits = Counter(
            'cache_hits_total',
            'Total cache hits',
            ['cache_type', 'service'],
            registry=registry
        )
        
        self.cache_misses = Counter(
            'cache_misses_total',
            'Total cache misses',
            ['cache_type', 'service'],
            registry=registry
        )
        
        self.cache_hit_rate = Gauge(
            'cache_hit_rate',
            'Cache hit rate (percentage)',
            ['cache_type', 'service'],
            registry=registry
        )
        
        # Health metrics
        self.health_status = Gauge(
            'health_status',
            'Service health status (1=healthy, 0=unhealthy)',
            ['service', 'instance', 'check_type'],
            registry=registry
        )
    
    def update_system_metrics(self, service: str, instance: str):
        """Update system metrics."""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            
            self.cpu_usage.labels(service=service, instance=instance).set(cpu_percent)
            self.memory_usage.labels(service=service, instance=instance).set(memory.used)
            self.memory_usage_percent.labels(service=service, instance=instance).set(memory.percent)
        except Exception as e:
            logger.warning(f"Failed to update system metrics: {e}")
    
    def record_async_task_start(self, service: str, instance: str, task_type: str):
        """Record async task start."""
        self.async_tasks_active.labels(service=service, instance=instance).inc()
        self.async_tasks_total.labels(service=service, instance=instance, task_type=task_type).inc()
    
    def record_async_task_complete(self, service: str, instance: str, 
                                 task_type: str, duration: float):
        """Record async task completion."""
        self.async_tasks_active.labels(service=service, instance=instance).dec()
        self.async_task_duration.labels(
            service=service, instance=instance, task_type=task_type
        ).observe(duration)
    
    def update_database_metrics(self, database: str, service: str, 
                               connections: int, query_type: str, duration: float):
        """Update database metrics."""
        self.database_connections.labels(database=database, service=service).set(connections)
        self.database_query_duration.labels(
            database=database, query_type=query_type, service=service
        ).observe(duration)
    
    def record_cache_hit(self, cache_type: str, service: str):
        """Record cache hit."""
        self.cache_hits.labels(cache_type=cache_type, service=service).inc()
    
    def record_cache_miss(self, cache_type: str, service: str):
        """Record cache miss."""
        self.cache_misses.labels(cache_type=cache_type, service=service).inc()
    
    def update_cache_hit_rate(self, cache_type: str, service: str, hit_rate: float):
        """Update cache hit rate."""
        self.cache_hit_rate.labels(cache_type=cache_type, service=service).set(hit_rate)
    
    def record_health_check(self, service: str, instance: str, 
                           check_type: str, healthy: bool):
        """Record health check result."""
        self.health_status.labels(
            service=service, instance=instance, check_type=check_type
        ).set(1 if healthy else 0)


class ExchangeMetrics:
    """Exchange-related metrics with institutional-grade tracking."""
    
    def __init__(self, registry: CollectorRegistry):
        # Connectivity metrics
        self.exchange_rtt = Histogram(
            'exchange_rtt_seconds',
            'Exchange round-trip time',
            ['exchange'],
            buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
            registry=registry
        )
        
        self.disconnect_frequency = Counter(
            'exchange_disconnect_total',
            'Total number of exchange disconnections',
            ['exchange', 'reason'],
            registry=registry
        )
        
        self.disconnect_rate = Gauge(
            'exchange_disconnect_rate',
            'Exchange disconnection rate per hour',
            ['exchange'],
            registry=registry
        )
        
        # Feed metrics
        self.stale_feed_rate = Counter(
            'exchange_stale_feed_total',
            'Total number of stale feed detections',
            ['exchange', 'symbol'],
            registry=registry
        )
        
        self.stale_feed_rate_gauge = Gauge(
            'exchange_stale_feed_rate',
            'Stale feed rate per minute',
            ['exchange', 'symbol'],
            registry=registry
        )
        
        self.feed_latency = Histogram(
            'exchange_feed_latency_seconds',
            'Exchange feed latency',
            ['exchange', 'symbol', 'feed_type'],
            buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25],
            registry=registry
        )
        
        # Reconnect storm metrics
        self.reconnect_storm_detected = Counter(
            'exchange_reconnect_storm_total',
            'Total number of reconnect storms detected',
            ['exchange'],
            registry=registry
        )
        
        self.reconnect_storm_active = Gauge(
            'exchange_reconnect_storm_active',
            'Active reconnect storms',
            ['exchange'],
            registry=registry
        )
        
        # Order book metrics
        self.order_book_depth = Gauge(
            'exchange_order_book_depth',
            'Order book depth',
            ['exchange', 'symbol', 'side'],
            registry=registry
        )
        
        self.order_book_spread = Gauge(
            'exchange_order_book_spread',
            'Order book bid-ask spread',
            ['exchange', 'symbol'],
            registry=registry
        )
        
        # API metrics
        self.api_calls = Counter(
            'exchange_api_calls_total',
            'Total number of exchange API calls',
            ['exchange', 'endpoint', 'method', 'status'],
            registry=registry
        )
        
        self.api_latency = Histogram(
            'exchange_api_latency_seconds',
            'Exchange API call latency',
            ['exchange', 'endpoint', 'method'],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
            registry=registry
        )
    
    def record_rtt(self, exchange: str, rtt: float):
        """Record exchange round-trip time."""
        self.exchange_rtt.labels(exchange=exchange).observe(rtt)
    
    def record_disconnect(self, exchange: str, reason: str):
        """Record exchange disconnection."""
        self.disconnect_frequency.labels(exchange=exchange, reason=reason).inc()
    
    def record_stale_feed(self, exchange: str, symbol: str):
        """Record stale feed detection."""
        self.stale_feed_rate.labels(exchange=exchange, symbol=symbol).inc()
    
    def record_feed_latency(self, exchange: str, symbol: str, feed_type: str, latency: float):
        """Record feed latency."""
        self.feed_latency.labels(
            exchange=exchange, symbol=symbol, feed_type=feed_type
        ).observe(latency)
    
    def record_reconnect_storm(self, exchange: str, active: bool):
        """Record reconnect storm."""
        if active:
            self.reconnect_storm_detected.labels(exchange=exchange).inc()
            self.reconnect_storm_active.labels(exchange=exchange).set(1)
        else:
            self.reconnect_storm_active.labels(exchange=exchange).set(0)
    
    def update_order_book_metrics(self, exchange: str, symbol: str, 
                                 bid_depth: int, ask_depth: int, spread: float):
        """Update order book metrics."""
        self.order_book_depth.labels(
            exchange=exchange, symbol=symbol, side='bid'
        ).set(bid_depth)
        
        self.order_book_depth.labels(
            exchange=exchange, symbol=symbol, side='ask'
        ).set(ask_depth)
        
        self.order_book_spread.labels(
            exchange=exchange, symbol=symbol
        ).set(spread)
    
    def record_api_call(self, exchange: str, endpoint: str, method: str, 
                       status: str, latency: float):
        """Record API call."""
        self.api_calls.labels(
            exchange=exchange, endpoint=endpoint, method=method, status=status
        ).inc()
        
        self.api_latency.labels(
            exchange=exchange, endpoint=endpoint, method=method
        ).observe(latency)


class MetricsCollector:
    """Main metrics collector that coordinates all metric types."""
    
    def __init__(self, service_name: str = "algo-trading"):
        self.service_name = service_name
        self.registry = CollectorRegistry()
        
        # Initialize metric collectors
        self.execution = ExecutionMetrics(self.registry)
        self.websocket = WebSocketMetrics(self.registry)
        self.infrastructure = InfrastructureMetrics(self.registry)
        self.exchange = ExchangeMetrics(self.registry)
        
        # Server configuration
        self.server_port = 8080
        self.server_started = False
        self.instance_id = f"{service_name}-{int(time.time())}"
        
        # Background tasks
        self.system_metrics_task = None
        self.running = False
    
    async def start_server(self, port: int = 8080):
        """Start Prometheus metrics HTTP server."""
        if not self.server_started:
            start_http_server(port, registry=self.registry)
            self.server_started = True
            self.server_port = port
            logger.info(f"Prometheus metrics server started on port {port}")
            
            # Start background system metrics collection
            self.running = True
            self.system_metrics_task = asyncio.create_task(self._collect_system_metrics())
    
    async def stop_server(self):
        """Stop metrics collection."""
        self.running = False
        if self.system_metrics_task:
            self.system_metrics_task.cancel()
            try:
                await self.system_metrics_task
            except asyncio.CancelledError:
                pass
        logger.info("Metrics collector stopped")
    
    async def _collect_system_metrics(self):
        """Background task to collect system metrics."""
        while self.running:
            try:
                self.infrastructure.update_system_metrics(
                    service=self.service_name,
                    instance=self.instance_id
                )
                await asyncio.sleep(10)  # Update every 10 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"System metrics collection error: {e}")
                await asyncio.sleep(5)
    
    def get_metrics(self) -> str:
        """Get current metrics in Prometheus format."""
        return generate_latest(self.registry).decode('utf-8')
    
    def get_all_metrics(self) -> Dict[str, Any]:
        """Get all current metric values for debugging."""
        return {
            "service": self.service_name,
            "instance": self.instance_id,
            "server_port": self.server_port,
            "server_started": self.server_started,
            "running": self.running
        }


# Global metrics collector instance
metrics_collector = MetricsCollector()
