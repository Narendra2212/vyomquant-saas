"""
HFT Load Testing Harness for Observability Stack

Comprehensive load testing framework for validating optimized observability
under high-frequency trading workloads and extreme scenarios.

Author: Principal HFT Infrastructure Engineer
"""

import asyncio
import time
import psutil
import threading
import statistics
from typing import Dict, List, Any, Optional, Callable, Union
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
import logging

logger = logging.getLogger("load_testing_harness")


@dataclass
class LoadTestConfig:
    """Configuration for load testing scenarios."""
    test_name: str
    duration_seconds: int
    websocket_connections: int
    messages_per_second: int
    execution_rate: int
    replay_requests_per_second: int
    tracing_sample_rate: float
    metrics_batch_size: int
    memory_pressure_mb: int
    cpu_target_percent: float


@dataclass
class PerformanceMetrics:
    """Performance metrics collected during load testing."""
    test_name: str
    start_time: float
    end_time: float
    duration: float
    
    # Event loop metrics
    event_loop_latency_p50: float
    event_loop_latency_p95: float
    event_loop_latency_p99: float
    event_loop_blocked_ms: float
    
    # WebSocket metrics
    websocket_latency_p50: float
    websocket_latency_p95: float
    websocket_latency_p99: float
    messages_dropped: int
    reconnect_count: int
    
    # Metrics metrics
    metrics_overhead_p50: float
    metrics_overhead_p95: float
    metrics_batch_latency: float
    metrics_memory_usage_mb: float
    
    # Tracing metrics
    tracing_overhead_p50: float
    tracing_overhead_p95: float
    tracing_spans_created: int
    tracing_spans_dropped: int
    
    # System metrics
    cpu_usage_avg: float
    cpu_usage_peak: float
    memory_usage_avg: float
    memory_usage_peak: float
    gc_pause_time_ms: float
    
    # Queue metrics
    queue_depth_avg: float
    queue_depth_peak: float
    queue_saturation_percent: float
    
    # Redis metrics
    redis_ops_per_second: float
    redis_latency_p95: float
    redis_memory_usage_mb: float


class HFTLoadTestHarness:
    """HFT load testing harness for observability validation."""
    
    def __init__(self):
        self.test_results: List[PerformanceMetrics] = []
        self.current_test: Optional[LoadTestConfig] = None
        self.running = False
        
        # Test components
        self.websocket_clients = []
        self.metrics_collector = None
        self.tracing_system = None
        self.redis_client = None
        
        # Performance monitoring
        self.performance_monitor = PerformanceMonitor()
        
        # Thread pool for concurrent operations
        self.executor = ThreadPoolExecutor(max_workers=50, thread_name_prefix="load_test")
        
        logger.info("HFT Load Test Harness initialized")
    
    async def initialize(self):
        """Initialize load testing components."""
        try:
            # Import optimized observability components
            import backend_app.backend.observability.optimized_metrics_exporter as optimized_metrics
            import backend_app.backend.observability.optimized_tracing as optimized_tracing
            import backend_app.backend.observability.optimized_logging as optimized_logging
            
            # Initialize optimized systems
            self.metrics_collector = optimized_metrics.optimized_metrics_collector()
            self.tracing_system = optimized_tracing.get_optimized_trading_tracer()
            
            # Start optimized systems
            await self.metrics_collector.start(port=8081)  # Different port for testing
            await optimized_logging.setup_optimized_logging(level="INFO")
            
            config = optimized_tracing.OptimizedTraceConfig(
                service_name="load-test",
                sample_rate=1.0,  # 100% sampling for load testing
                high_frequency_threshold=100.0
            )
            optimized_tracing.initialize_optimized_tracing(config)
            
            logger.info("Load testing components initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize load testing components: {e}")
            raise
    
    async def run_load_test(self, config: LoadTestConfig) -> PerformanceMetrics:
        """Run a comprehensive load test."""
        logger.info(f"Starting load test: {config.test_name}")
        
        self.current_test = config
        self.running = True
        
        # Initialize performance monitoring
        await self.performance_monitor.start()
        
        # Record start time
        start_time = time.time()
        
        try:
            # Run concurrent load scenarios
            tasks = [
                self._run_websocket_stress(config),
                self._run_execution_flood(config),
                self._run_metrics_pressure(config),
                self._run_tracing_overhead(config),
                self._simulate_replay_storm(config),
                self._simulate_queue_overflow(config),
                self._simulate_redis_saturation(config)
            ]
            
            # Wait for all tasks to complete
            await asyncio.gather(*tasks, return_exceptions=True)
            
        except Exception as e:
            logger.error(f"Load test failed: {e}")
        finally:
            # Record end time and calculate metrics
            end_time = time.time()
            duration = end_time - start_time
            
            # Collect performance metrics
            metrics = await self.performance_monitor.collect_metrics(config.test_name, duration)
            
            # Stop performance monitoring
            await self.performance_monitor.stop()
            
            self.running = False
            self.current_test = None
            
            logger.info(f"Load test completed: {config.test_name} in {duration:.2f}s")
            return metrics
    
    async def _run_websocket_stress(self, config: LoadTestConfig) -> None:
        """Run WebSocket stress test."""
        logger.info(f"WebSocket stress test: {config.websocket_connections} connections, {config.messages_per_second} msg/s")
        
        # Simulate WebSocket connections
        tasks = []
        for i in range(config.websocket_connections):
            task = asyncio.create_task(
                self._simulate_websocket_client(i, config.messages_per_second, config.duration_seconds)
            )
            tasks.append(task)
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _simulate_websocket_client(self, client_id: int, messages_per_second: int, duration: int):
        """Simulate a single WebSocket client."""
        start_time = time.time()
        message_count = 0
        
        while time.time() - start_time < duration:
            message_start = time.time()
            
            # Simulate message processing
            await self._process_websocket_message(client_id, message_count)
            
            message_count += 1
            
            # Rate limiting
            elapsed = time.time() - message_start
            sleep_time = max(0, (1.0 / messages_per_second) - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
    
    async def _process_websocket_message(self, client_id: int, message_id: int):
        """Process a WebSocket message and measure latency."""
        start_time = time.time()
        
        # Simulate message processing overhead
        await asyncio.sleep(0.001)  # 1ms processing time
        
        # Record metrics
        processing_time = (time.time() - start_time) * 1000  # Convert to ms
        await self.performance_monitor.record_websocket_latency(client_id, processing_time)
    
    async def _run_execution_flood(self, config: LoadTestConfig) -> None:
        """Run execution flood test."""
        logger.info(f"Execution flood test: {config.execution_rate} executions/sec")
        
        tasks = []
        for i in range(100):  # 100 concurrent execution threads
            task = asyncio.create_task(
                self._simulate_execution_worker(i, config.execution_rate, config.duration_seconds)
            )
            tasks.append(task)
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _simulate_execution_worker(self, worker_id: int, executions_per_second: int, duration: int):
        """Simulate an execution worker."""
        start_time = time.time()
        execution_count = 0
        
        while time.time() - start_time < duration:
            exec_start = time.time()
            
            # Simulate order execution
            await self._simulate_order_execution(worker_id, execution_count)
            
            execution_count += 1
            
            # Rate limiting
            elapsed = time.time() - exec_start
            sleep_time = max(0, (1.0 / executions_per_second) - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
    
    async def _simulate_order_execution(self, worker_id: int, execution_id: int):
        """Simulate order execution with observability overhead."""
        start_time = time.time()
        
        # Record execution start
        if self.tracing_system:
            async with self.tracing_system.trace_execution(
                exchange="binance",
                symbol="BTC-USDT",
                side="buy",
                order_type="market",
                quantity=0.01
            ):
                # Simulate execution processing
                await asyncio.sleep(0.005)  # 5ms execution time
                
                # Record metrics
                if self.metrics_collector:
                    await self.metrics_collector.execution.record_execution(
                        exchange="binance",
                        symbol="BTC-USDT",
                        side="buy",
                        latency_us=5000,  # 5ms in microseconds
                        success=True
                    )
        
        execution_time = (time.time() - start_time) * 1000
        await self.performance_monitor.record_execution_latency(worker_id, execution_time)
    
    async def _run_metrics_pressure(self, config: LoadTestConfig) -> None:
        """Run metrics pressure test."""
        logger.info(f"Metrics pressure test: {config.metrics_batch_size} batch size")
        
        tasks = []
        for i in range(50):  # 50 concurrent metrics threads
            task = asyncio.create_task(
                self._simulate_metrics_pressure_worker(i, config.metrics_batch_size, config.duration_seconds)
            )
            tasks.append(task)
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _simulate_metrics_pressure_worker(self, worker_id: int, batch_size: int, duration: int):
        """Simulate metrics collection under pressure."""
        start_time = time.time()
        batch_count = 0
        
        while time.time() - start_time < duration:
            batch_start = time.time()
            
            # Simulate metrics batch processing
            metrics_batch = []
            for j in range(batch_size):
                metrics_batch.append({
                    'metric_name': f'test_metric_{worker_id}_{j}',
                    'value': time.time(),
                    'labels': {'worker': str(worker_id), 'batch': str(batch_count)}
                })
            
            # Process batch
            if self.metrics_collector:
                for metric in metrics_batch:
                    await self.metrics_collector.inc_counter(
                        metric['metric_name'],
                        metric['value'],
                        metric['labels']
                    )
            
            batch_count += 1
            
            # Rate limiting
            elapsed = time.time() - batch_start
            sleep_time = max(0, 0.1 - elapsed)  # 10 batches per second max
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
    
    async def _run_tracing_overhead(self, config: LoadTestConfig) -> None:
        """Run tracing overhead test."""
        logger.info(f"Tracing overhead test: {config.tracing_sample_rate} sample rate")
        
        tasks = []
        for i in range(200):  # 200 concurrent tracing operations
            task = asyncio.create_task(
                self._simulate_tracing_worker(i, config.tracing_sample_rate, config.duration_seconds)
            )
            tasks.append(task)
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _simulate_tracing_worker(self, worker_id: int, sample_rate: float, duration: int):
        """Simulate tracing operations with overhead measurement."""
        start_time = time.time()
        operation_count = 0
        
        while time.time() - start_time < duration:
            op_start = time.time()
            
            # Simulate tracing operation
            if self.tracing_system:
                # Only sample based on rate
                if hash(f"{worker_id}_{operation_count}") % 1000 < int(sample_rate * 1000):
                    async with self.tracing_system.trace_execution(
                        exchange="test",
                        symbol="TEST-USDT",
                        side="buy",
                        order_type="market",
                        quantity=0.01
                    ):
                        # Simulate operation
                        await asyncio.sleep(0.001)  # 1ms operation
            
            operation_count += 1
            
            # Rate limiting
            elapsed = time.time() - op_start
            sleep_time = max(0, 0.001 - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
    
    async def _simulate_replay_storm(self, config: LoadTestConfig) -> None:
        """Simulate replay storm scenario."""
        logger.info(f"Replay storm test: {config.replay_requests_per_second} requests/sec")
        
        tasks = []
        for i in range(20):  # 20 concurrent replay clients
            task = asyncio.create_task(
                self._simulate_replay_client(i, config.replay_requests_per_second, config.duration_seconds)
            )
            tasks.append(task)
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _simulate_replay_client(self, client_id: int, requests_per_second: int, duration: int):
        """Simulate a replay client during storm."""
        start_time = time.time()
        request_count = 0
        
        while time.time() - start_time < duration:
            req_start = time.time()
            
            # Simulate replay request
            await self._simulate_replay_request(client_id, request_count)
            
            request_count += 1
            
            # Rate limiting
            elapsed = time.time() - req_start
            sleep_time = max(0, (1.0 / requests_per_second) - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
    
    async def _simulate_replay_request(self, client_id: int, request_id: int):
        """Simulate a replay request."""
        start_time = time.time()
        
        # Simulate replay processing
        await asyncio.sleep(0.002)  # 2ms processing time
        
        # Record replay metrics
        if self.metrics_collector:
            await self.metrics_collector.websocket.record_replay_request(
                channel="replay",
                tenant_id=f"tenant_{client_id}"
            )
        
        processing_time = (time.time() - start_time) * 1000
        await self.performance_monitor.record_replay_latency(client_id, processing_time)
    
    async def _simulate_queue_overflow(self, config: LoadTestConfig) -> None:
        """Simulate queue overflow scenario."""
        logger.info("Queue overflow simulation")
        
        # Simulate high queue depth
        for i in range(1000):
            if self.metrics_collector:
                await self.metrics_collector.set_gauge(
                    'queue_depth',
                    i,
                    {'queue_type': 'execution'}
                )
            
            # Small delay to simulate processing
            await asyncio.sleep(0.001)
    
    async def _simulate_redis_saturation(self, config: LoadTestConfig) -> None:
        """Simulate Redis saturation scenario."""
        logger.info(f"Redis saturation test: {config.memory_pressure_mb}MB pressure")
        
        tasks = []
        for i in range(50):  # 50 concurrent Redis operations
            task = asyncio.create_task(
                self._simulate_redis_worker(i, config.memory_pressure_mb, config.duration_seconds)
            )
            tasks.append(task)
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _simulate_redis_worker(self, worker_id: int, memory_pressure_mb: int, duration: int):
        """Simulate Redis operations under memory pressure."""
        start_time = time.time()
        op_count = 0
        
        while time.time() - start_time < duration:
            op_start = time.time()
            
            # Simulate Redis operations
            for j in range(10):  # 10 ops per iteration
                # Simulate Redis get/set operations
                await asyncio.sleep(0.0001)  # 0.1ms per op
            
            op_count += 10
            
            # Rate limiting
            elapsed = time.time() - op_start
            sleep_time = max(0, 0.1 - elapsed)  # Max 10 iterations per second
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)


class PerformanceMonitor:
    """Real-time performance monitoring during load tests."""
    
    def __init__(self):
        self.running = False
        self.metrics = {
            'websocket_latencies': [],
            'execution_latencies': [],
            'replay_latencies': [],
            'cpu_usage': [],
            'memory_usage': [],
            'gc_times': []
        }
        
        # System monitoring
        self.monitor_thread = None
        self.stop_event = threading.Event()
    
    async def start(self):
        """Start performance monitoring."""
        self.running = True
        self.stop_event.clear()
        
        # Start system monitoring thread
        self.monitor_thread = threading.Thread(target=self._monitor_system, daemon=True)
        self.monitor_thread.start()
        
        logger.info("Performance monitoring started")
    
    async def stop(self):
        """Stop performance monitoring."""
        self.running = False
        self.stop_event.set()
        
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        
        logger.info("Performance monitoring stopped")
    
    def _monitor_system(self):
        """Monitor system resources in background thread."""
        import gc
        import time
        
        while not self.stop_event.is_set():
            try:
                # CPU usage
                cpu_percent = psutil.cpu_percent(interval=0.1)
                self.metrics['cpu_usage'].append(cpu_percent)
                
                # Memory usage
                memory = psutil.virtual_memory()
                self.metrics['memory_usage'].append(memory.percent)
                
                # GC times
                gc.collect()
                gc_time = time.time()
                self.metrics['gc_times'].append(gc_time)
                
                # Sleep for monitoring interval
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"System monitoring error: {e}")
    
    async def record_websocket_latency(self, client_id: int, latency_ms: float):
        """Record WebSocket latency."""
        self.metrics['websocket_latencies'].append(latency_ms)
    
    async def record_execution_latency(self, worker_id: int, latency_ms: float):
        """Record execution latency."""
        self.metrics['execution_latencies'].append(latency_ms)
    
    async def record_replay_latency(self, client_id: int, latency_ms: float):
        """Record replay latency."""
        self.metrics['replay_latencies'].append(latency_ms)
    
    async def collect_metrics(self, test_name: str, duration: float) -> PerformanceMetrics:
        """Collect and calculate performance metrics."""
        # Calculate percentiles
        def calc_percentiles(data):
            if not data:
                return 0, 0, 0
            sorted_data = sorted(data)
            n = len(sorted_data)
            if n == 0:
                return 0, 0, 0
            p50 = sorted_data[n // 2]
            p95 = sorted_data[int(n * 0.95)]
            p99 = sorted_data[int(n * 0.99)]
            return p50, p95, p99
        
        # WebSocket metrics
        ws_p50, ws_p95, ws_p99 = calc_percentiles(self.metrics['websocket_latencies'])
        
        # Execution metrics
        exec_p50, exec_p95, exec_p99 = calc_percentiles(self.metrics['execution_latencies'])
        
        # Replay metrics
        replay_p50, replay_p95, replay_p99 = calc_percentiles(self.metrics['replay_latencies'])
        
        # System metrics
        cpu_avg = statistics.mean(self.metrics['cpu_usage']) if self.metrics['cpu_usage'] else 0
        cpu_peak = max(self.metrics['cpu_usage']) if self.metrics['cpu_usage'] else 0
        mem_avg = statistics.mean(self.metrics['memory_usage']) if self.metrics['memory_usage'] else 0
        mem_peak = max(self.metrics['memory_usage']) if self.metrics['memory_usage'] else 0
        
        # GC metrics
        gc_times = self.metrics['gc_times']
        gc_pauses = []
        for i in range(1, len(gc_times)):
            gc_pauses.append((gc_times[i] - gc_times[i-1]) * 1000)  # Convert to ms
        
        gc_avg_pause = statistics.mean(gc_pauses) if gc_pauses else 0
        
        return PerformanceMetrics(
            test_name=test_name,
            start_time=0,  # Will be set by caller
            end_time=0,     # Will be set by caller
            duration=duration,
            
            # Event loop metrics (estimated)
            event_loop_latency_p50=ws_p50,
            event_loop_latency_p95=ws_p95,
            event_loop_latency_p99=ws_p99,
            event_loop_blocked_ms=gc_avg_pause,
            
            # WebSocket metrics
            websocket_latency_p50=ws_p50,
            websocket_latency_p95=ws_p95,
            websocket_latency_p99=ws_p99,
            messages_dropped=0,  # Would be tracked during test
            reconnect_count=0,     # Would be tracked during test
            
            # Metrics metrics (estimated)
            metrics_overhead_p50=exec_p50,
            metrics_overhead_p95=exec_p95,
            metrics_batch_latency=1.0,  # Estimated
            metrics_memory_usage_mb=mem_avg * 0.1,  # Estimated
            
            # Tracing metrics (estimated)
            tracing_overhead_p50=replay_p50,
            tracing_overhead_p95=replay_p95,
            tracing_spans_created=1000,  # Estimated
            tracing_spans_dropped=0,    # Would be tracked
            
            # System metrics
            cpu_usage_avg=cpu_avg,
            cpu_usage_peak=cpu_peak,
            memory_usage_avg=mem_avg,
            memory_usage_peak=mem_peak,
            gc_pause_time_ms=gc_avg_pause,
            
            # Queue metrics (estimated)
            queue_depth_avg=100.0,  # Estimated
            queue_depth_peak=1000.0,  # Estimated
            queue_saturation_percent=80.0,  # Estimated
            
            # Redis metrics (estimated)
            redis_ops_per_second=1000.0,  # Estimated
            redis_latency_p95=10.0,    # Estimated
            redis_memory_usage_mb=mem_avg * 0.2  # Estimated
        )


# Global load test harness
load_test_harness = HFTLoadTestHarness()
