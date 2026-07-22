
"""
HFT-Optimized Prometheus Metrics Exporter

Ultra-low latency metrics collection optimized for high-frequency trading.
Addresses all critical performance and safety issues.

Author: Senior Institutional Systems Architect
"""
import asyncio
import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Set

from aiohttp import web

logger = logging.getLogger("optimized_metrics_exporter")

# HFT-optimized imports
try:
    from prometheus_client import (CONTENT_TYPE_LATEST, CollectorRegistry,
                                   Counter, Gauge, Histogram)
    from prometheus_client.exposition import generate_latest
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("Prometheus client not available")

# Async system monitoring
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("psutil not available - system metrics disabled")


class HFTOptimizedMetrics:
    """HFT-optimized metrics with async safety and low overhead."""
    
    def __init__(self, registry: CollectorRegistry, max_cardinality: int = 10000):
        self.registry = registry
        self.max_cardinality = max_cardinality
        
        # Async-safe metrics storage
        self._metrics_lock = asyncio.Lock()
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._histograms: Dict[str, Histogram] = {}
        
        # Cardinality tracking
        self._label_combinations: Dict[str, Set[frozenset]] = defaultdict(set)
        self._cardinality_lock = threading.Lock()
        
        # Batching queue
        self._batch_queue = asyncio.Queue(maxsize=1000)
        self._batch_processor_task = None
        self._batch_size = 100
        self._batch_timeout = 1.0
        
        # Performance optimization
        self._sampling_rate = 1.0  # Configurable sampling
        self._high_freq_threshold = 1000  # ops/sec
        
    async def initialize(self):
        """Initialize async components."""
        self._batch_processor_task = asyncio.create_task(self._batch_processor())
    
    async def shutdown(self):
        """Shutdown async components."""
        if self._batch_processor_task:
            self._batch_processor_task.cancel()
            try:
                await self._batch_processor_task
            except asyncio.CancelledError:
                pass
    
    def _check_cardinality(self, metric_name: str, labels: Dict[str, str]) -> bool:
        """Check if adding this label combination exceeds cardinality limits."""
        label_set = frozenset(labels.items())
        
        with self._cardinality_lock:
            current_combinations = self._label_combinations[metric_name]
            
            if label_set in current_combinations:
                return True  # Already exists, safe
            
            total_combinations = sum(len(combos) for combos in self._label_combinations.values())
            
            if total_combinations >= self.max_cardinality:
                logger.warning(f"Cardinality limit exceeded for metric {metric_name}")
                return False
            
            current_combinations.add(label_set)
            return True
    
    async def _batch_processor(self):
        """Background task to process metric batches."""
        while True:
            try:
                batch = []
                deadline = time.time() + self._batch_timeout
                
                # Collect batch
                while len(batch) < self._batch_size and time.time() < deadline:
                    try:
                        item = await asyncio.wait_for(
                            self._batch_queue.get(), 
                            timeout=max(0.1, deadline - time.time())
                        )
                        batch.append(item)
                    except asyncio.TimeoutError:
                        break
                
                # Process batch
                if batch:
                    await self._process_batch(batch)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Batch processor error: {e}")
                await asyncio.sleep(0.1)
    
    async def _process_batch(self, batch: List[Dict[str, Any]]):
        """Process a batch of metric updates."""
        # Process in thread pool to avoid blocking event loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._process_batch_sync, batch)
    
    def _process_batch_sync(self, batch: List[Dict[str, Any]]):
        """Synchronous batch processing."""
        for item in batch:
            metric_type = item['type']
            metric_name = item['name']
            labels = item.get('labels', {})
            value = item['value']
            
            try:
                if metric_type == 'counter':
                    if metric_name in self._counters:
                        self._counters[metric_name].labels(**labels).inc(value)
                elif metric_type == 'gauge':
                    if metric_name in self._gauges:
                        self._gauges[metric_name].labels(**labels).set(value)
                elif metric_type == 'histogram':
                    if metric_name in self._histograms:
                        self._histograms[metric_name].labels(**labels).observe(value)
            except Exception as e:
                logger.debug(f"Metric update failed: {e}")
    
    async def inc_counter(self, name: str, value: float = 1.0, labels: Dict[str, str] = None):
        """Thread-safe counter increment with batching."""
        if not self._check_cardinality(name, labels or {}):
            return
        
        try:
            await self._batch_queue.put({
                'type': 'counter',
                'name': name,
                'value': value,
                'labels': labels or {}
            })
        except asyncio.QueueFull:
            # Drop metric if queue full (HFT optimization)
            pass
    
    async def set_gauge(self, name: str, value: float, labels: Dict[str, str] = None):
        """Thread-safe gauge setting with batching."""
        if not self._check_cardinality(name, labels or {}):
            return
        
        try:
            await self._batch_queue.put({
                'type': 'gauge',
                'name': name,
                'value': value,
                'labels': labels or {}
            })
        except asyncio.QueueFull:
            pass
    
    async def observe_histogram(self, name: str, value: float, labels: Dict[str, str] = None):
        """Thread-safe histogram observation with batching."""
        if not self._check_cardinality(name, labels or {}):
            return
        
        try:
            await self._batch_queue.put({
                'type': 'histogram',
                'name': name,
                'value': value,
                'labels': labels or {}
            })
        except asyncio.QueueFull:
            pass


class AsyncSystemMetrics:
    """Async system metrics collector with minimal blocking."""
    
    def __init__(self, metrics: HFTOptimizedMetrics):
        self.metrics = metrics
        self.running = False
        self.update_interval = 5.0  # 5 seconds, not 1
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sys_metrics")
        
        # Cached system info
        self._cpu_count = None
        self._memory_total = None
        self._disk_total = None
        
        # Initialize cached values
        self._init_cached_values()
    
    def _init_cached_values(self):
        """Initialize cached system values once."""
        try:
            if PSUTIL_AVAILABLE:
                self._cpu_count = psutil.cpu_count()
                self._memory_total = psutil.virtual_memory().total
                self._disk_total = psutil.disk_usage('/').total
        except Exception as e:
            logger.warning(f"Failed to initialize system metrics: {e}")
    
    async def start(self):
        """Start async system metrics collection."""
        self.running = True
        asyncio.create_task(self._collection_loop())
    
    async def stop(self):
        """Stop system metrics collection."""
        self.running = False
        self.executor.shutdown(wait=True)
    
    async def _collection_loop(self):
        """Async collection loop with minimal blocking."""
        while self.running:
            try:
                # Collect system metrics in thread pool
                loop = asyncio.get_event_loop()
                metrics_data = await loop.run_in_executor(
                    self.executor, 
                    self._collect_system_metrics_sync
                )
                
                # Update metrics asynchronously
                await self._update_metrics(metrics_data)
                
                await asyncio.sleep(self.update_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"System metrics collection error: {e}")
                await asyncio.sleep(1.0)
    
    def _collect_system_metrics_sync(self) -> Dict[str, float]:
        """Synchronous system metrics collection (runs in thread pool)."""
        if not PSUTIL_AVAILABLE:
            return {}
        
        try:
            # Use non-blocking calls where possible
            cpu_percent = psutil.cpu_percent(interval=None)  # Non-blocking
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Get load average if available (Unix only)
            load_avg = None
            if hasattr(psutil, 'getloadavg'):
                load_avg = psutil.getloadavg()[0]  # 1-minute average
            
            return {
                'cpu_percent': cpu_percent,
                'memory_percent': memory.percent,
                'memory_used': memory.used,
                'memory_available': memory.available,
                'disk_percent': disk.percent,
                'disk_free': disk.free,
                'load_average': load_avg
            }
            
        except Exception as e:
            logger.debug(f"System metrics collection failed: {e}")
            return {}
    
    async def _update_metrics(self, data: Dict[str, float]):
        """Update metrics asynchronously."""
        service_name = "algo-trading"
        instance_id = "hft-instance"
        
        if 'cpu_percent' in data:
            await self.metrics.set_gauge(
                'cpu_usage_percent', 
                data['cpu_percent'],
                {'service': service_name, 'instance': instance_id}
            )
        
        if 'memory_percent' in data:
            await self.metrics.set_gauge(
                'memory_usage_percent',
                data['memory_percent'],
                {'service': service_name, 'instance': instance_id}
            )
        
        if 'load_average' in data and data['load_average'] is not None:
            await self.metrics.set_gauge(
                'system_load_average',
                data['load_average'],
                {'service': service_name, 'instance': instance_id}
            )


class HFTExecutionMetrics(HFTOptimizedMetrics):
    """HFT-optimized execution metrics with ultra-low latency."""
    
    def __init__(self, registry: CollectorRegistry):
        super().__init__(registry, max_cardinality=5000)  # Lower limit for execution
        
        # HFT-optimized buckets (microsecond precision)
        self.execution_latency = Histogram(
            'execution_latency_microseconds',
            'Execution latency in microseconds',
            ['exchange', 'symbol', 'side'],
            buckets=[1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
            registry=registry
        )
        
        self.execution_throughput = Counter(
            'execution_total',
            'Total executions',
            ['exchange', 'status'],
            registry=registry
        )
        
        self.execution_success_rate = Gauge(
            'execution_success_rate',
            'Execution success rate',
            ['exchange'],
            registry=registry
        )
        
        # High-frequency metrics with sampling
        self._execution_count = 0
        self._success_count = 0
        self._last_sample_time = time.time()
        self._sample_interval = 1.0  # Sample every second
    
    async def record_execution(self, exchange: str, symbol: str, side: str, 
                             latency_us: float, success: bool):
        """Record execution with HFT optimization."""
        # Convert to microseconds for HFT precision
        latency_micros = latency_us * 1_000_000
        
        # Record latency (always, for latency tracking)
        await self.observe_histogram(
            'execution_latency_microseconds',
            latency_micros,
            {'exchange': exchange, 'symbol': symbol[:10], 'side': side}  # Limit symbol length
        )
        
        # Record throughput (with sampling for high frequency)
        self._execution_count += 1
        if success:
            self._success_count += 1
        
        # Sample throughput metrics
        current_time = time.time()
        if current_time - self._last_sample_time >= self._sample_interval:
            await self._sample_throughput(exchange, current_time)
    
    async def _sample_throughput(self, exchange: str, current_time: float):
        """Sample throughput metrics."""
        self._execution_count / self._sample_interval
        success_rate = (self._success_count / self._execution_count * 100) if self._execution_count > 0 else 0
        
        await self.inc_counter(
            'execution_total',
            self._execution_count,
            {'exchange': exchange, 'status': 'sampled'}
        )
        
        await self.set_gauge(
            'execution_success_rate',
            success_rate,
            {'exchange': exchange}
        )
        
        # Reset counters
        self._execution_count = 0
        self._success_count = 0
        self._last_sample_time = current_time


class HFTWebSocketMetrics(HFTOptimizedMetrics):
    """HFT-optimized WebSocket metrics with minimal overhead."""
    
    def __init__(self, registry: CollectorRegistry):
        super().__init__(registry, max_cardinality=3000)
        
        self.websocket_connections = Gauge(
            'websocket_connections',
            'Active WebSocket connections',
            ['channel'],
            registry=registry
        )
        
        self.message_rate = Gauge(
            'websocket_message_rate',
            'WebSocket message rate per second',
            ['channel', 'event_type'],
            registry=registry
        )
        
        # High-frequency message counting with sampling
        self._message_counts: Dict[str, int] = defaultdict(int)
        self._last_sample_time = time.time()
        self._sample_interval = 5.0  # Sample every 5 seconds
    
    async def record_message(self, channel: str, event_type: str):
        """Record WebSocket message with sampling."""
        key = f"{channel}:{event_type}"
        self._message_counts[key] += 1
        
        # Sample periodically
        current_time = time.time()
        if current_time - self._last_sample_time >= self._sample_interval:
            await self._sample_message_rates(current_time)
    
    async def _sample_message_rates(self, current_time: float):
        """Sample message rates."""
        interval = current_time - self._last_sample_time
        
        for key, count in self._message_counts.items():
            channel, event_type = key.split(':', 1)
            rate = count / interval
            
            await self.set_gauge(
                'websocket_message_rate',
                rate,
                {'channel': channel[:20], 'event_type': event_type[:20]}  # Limit length
            )
        
        # Reset counters
        self._message_counts.clear()
        self._last_sample_time = current_time
    
    async def update_connections(self, connections: Dict[str, int]):
        """Update connection counts."""
        for channel, count in connections.items():
            await self.set_gauge(
                'websocket_connections',
                count,
                {'channel': channel[:20]}  # Limit channel length
            )


class OptimizedMetricsCollector:
    """HFT-optimized metrics collector with minimal overhead."""
    
    def __init__(self, service_name: str = "algo-trading"):
        self.service_name = service_name
        self.registry = CollectorRegistry()
        
        # Initialize optimized metrics
        self.execution = HFTExecutionMetrics(self.registry)
        self.websocket = HFTWebSocketMetrics(self.registry)
        self.system = AsyncSystemMetrics(HFTOptimizedMetrics(self.registry))
        
        # Server configuration
        self.server_port = 8080
        self.server_started = False
        
        # Performance optimization
        self._metrics_cache = None
        self._cache_time = 0
        self._cache_ttl = 0.1  # 100ms cache for metrics endpoint
    
    async def start(self, port: int = 8080):
        """Start optimized metrics server."""
        if not PROMETHEUS_AVAILABLE:
            logger.error("Prometheus client not available")
            return
        
        # Initialize async components
        await self.execution.initialize()
        await self.websocket.initialize()
        await self.system.start()
        
        # Start HTTP server
        if not self.server_started:
            from aiohttp import web
            
            app = web.Application()
            app.router.add_get('/metrics', self._metrics_handler)
            app.router.add_get('/health', self._health_handler)
            
            runner = web.AppRunner(app)
            await runner.setup()
            
            site = web.TCPSite(runner, '0.0.0.0', port)
            await site.start()
            
            self.server_started = True
            self.server_port = port
            logger.info(f"Optimized metrics server started on port {port}")
    
    async def stop(self):
        """Stop metrics collector."""
        await self.execution.shutdown()
        await self.websocket.shutdown()
        await self.system.stop()
        logger.info("Optimized metrics collector stopped")
    
    async def _metrics_handler(self, request):
        """Async metrics handler with caching."""
        current_time = time.time()
        
        # Check cache
        if (self._metrics_cache and 
            current_time - self._cache_time < self._cache_ttl):
            return web.Response(
                body=self._metrics_cache,
                content_type=CONTENT_TYPE_LATEST
            )
        
        # Generate metrics in thread pool
        loop = asyncio.get_event_loop()
        metrics_data = await loop.run_in_executor(None, generate_latest, self.registry)
        
        # Cache result
        self._metrics_cache = metrics_data
        self._cache_time = current_time
        
        return web.Response(
            body=metrics_data,
            content_type=CONTENT_TYPE_LATEST
        )
    
    async def _health_handler(self, request):
        """Health check endpoint."""
        return web.json_response({
            'status': 'healthy',
            'service': self.service_name,
            'server_port': self.server_port,
            'cache_ttl': self._cache_ttl
        })


# Global optimized metrics collector
optimized_metrics_collector = OptimizedMetricsCollector()

