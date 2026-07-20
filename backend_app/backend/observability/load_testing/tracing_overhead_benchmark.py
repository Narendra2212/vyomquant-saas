"""
Tracing Overhead Benchmark for HFT Observability

Measures tracing overhead under various sampling rates and workloads.
Validates that optimized tracing maintains performance under HFT conditions.

Author: Principal HFT Infrastructure Engineer
"""

import asyncio
import time
import random
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import statistics

logger = logging.getLogger("tracing_overhead_benchmark")


@dataclass
class TracingBenchConfig:
    """Configuration for tracing benchmark."""
    test_name: str
    num_workers: int
    operations_per_second: int
    duration_seconds: int
    sampling_rates: List[float]
    operation_types: List[str]
    span_complexity: int  # Number of attributes per span


@dataclass
class TracingBenchMetrics:
    """Metrics collected during tracing benchmark."""
    test_name: str
    sampling_rate: float
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    
    # Performance metrics
    operations_per_second: float = 0.0
    span_overhead_p50: float = 0.0
    span_overhead_p95: float = 0.0
    span_overhead_p99: float = 0.0
    memory_usage_mb: float = 0.0
    cpu_usage_percent: float = 0.0
    
    # Tracing metrics
    spans_created: int = 0
    spans_dropped: int = 0
    spans_exported: int = 0
    export_overhead_p50: float = 0.0
    export_overhead_p95: float = 0.0


class TracingOverheadBench:
    """Tracing overhead benchmark for HFT observability."""
    
    def __init__(self, config: TracingBenchConfig):
        self.config = config
        self.metrics = TracingBenchMetrics(test_name=config.test_name)
        self.workers: List[TracingBenchWorker] = []
        self.running = False
        
        # Performance monitoring
        self.system_monitor = SystemMonitor()
        
        logger.info(f"Tracing overhead benchmark initialized: {config.test_name}")
    
    async def run_benchmark(self) -> TracingBenchMetrics:
        """Run complete tracing overhead benchmark."""
        logger.info(f"Starting tracing overhead benchmark: {self.config.test_name}")
        
        self.running = True
        self.metrics.start_time = datetime.now(timezone.utc)
        
        try:
            # Initialize optimized tracing system
            await self._initialize_tracing_system()
            
            # Create workers
            await self._create_workers()
            
            # Run benchmark for each sampling rate
            for sampling_rate in self.config.sampling_rates:
                logger.info(f"Testing sampling rate: {sampling_rate}")
                await self._test_sampling_rate(sampling_rate)
            
        except Exception as e:
            logger.error(f"Tracing benchmark failed: {e}")
        finally:
            # Cleanup
            await self._cleanup()
            
            self.metrics.end_time = datetime.now(timezone.utc)
            self.metrics.duration_seconds = (
                self.metrics.end_time - self.metrics.start_time
            ).total_seconds()
            
            # Calculate final metrics
            self._calculate_final_metrics()
            
            logger.info(f"Tracing overhead benchmark completed: {self.config.test_name}")
            return self.metrics
    
    async def _initialize_tracing_system(self):
        """Initialize optimized tracing system."""
        try:
            # Import optimized tracing
            import backend_app.backend.observability.optimized_tracing as optimized_tracing
            
            # Configure for benchmark
            config = optimized_tracing.OptimizedTraceConfig(
                service_name="tracing_benchmark",
                sample_rate=1.0,  # Will be overridden per test
                sampling_strategy=optimized_tracing.SamplingStrategy.ADAPTIVE,
                high_frequency_threshold=10.0,
                max_spans_per_second=1000,
                buffer_size=1000,
                max_export_timeout_ms=5000,
                adaptive_sampling_window=30,
            )
            
            optimized_tracing.initialize_optimized_tracing(config)
            logger.info("Optimized tracing system initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize tracing system: {e}")
            raise
    
    async def _create_workers(self):
        """Create tracing benchmark workers."""
        logger.info(f"Creating {self.config.num_workers} tracing benchmark workers")
        
        for i in range(self.config.num_workers):
            worker = TracingBenchWorker(
                worker_id=i,
                config=self.config,
                system_monitor=self.system_monitor
            )
            self.workers.append(worker)
    
    async def _test_sampling_rate(self, sampling_rate: float):
        """Test a specific sampling rate."""
        # Update tracing configuration
        try:
            import backend_app.backend.observability.optimized_tracing as optimized_tracing
            
            # Create new config with specific sampling rate
            config = optimized_tracing.OptimizedTraceConfig(
                service_name="tracing_benchmark",
                sample_rate=sampling_rate,
                sampling_strategy=optimized_tracing.SamplingStrategy.ADAPTIVE,
                high_frequency_threshold=10.0,
                max_spans_per_second=1000,
                buffer_size=1000,
                max_export_timeout_ms=5000,
                adaptive_sampling_window=30
            )
            
            # Reinitialize tracing with new sampling rate
            optimized_tracing.initialize_optimized_tracing(config)
            
            logger.info(f"Testing with sampling rate: {sampling_rate}")
            
            # Start all workers
            tasks = []
            for worker in self.workers:
                task = asyncio.create_task(
                    worker.run_benchmark(sampling_rate)
                )
                tasks.append(task)
            
            # Run for specified duration
            start_time = time.time()
            end_time = start_time + self.config.duration_seconds
            
            while time.time() < end_time:
                await asyncio.sleep(1.0)
            
            # Stop all workers
            for task in tasks:
                task.cancel()
            
            # Wait for all tasks to complete
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
        except Exception as e:
            logger.error(f"Failed to test sampling rate {sampling_rate}: {e}")
    
    async def _cleanup(self):
        """Cleanup all workers and systems."""
        logger.info("Cleaning up tracing benchmark")
        
        # Stop all workers
        tasks = []
        for worker in self.workers:
            task = asyncio.create_task(worker.stop())
            tasks.append(task)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        # Stop system monitoring
        await self.system_monitor.stop()
        
        # Stop tracing system
        try:
            import backend_app.backend.observability.optimized_tracing as optimized_tracing
            optimized_tracing.shutdown_optimized_tracing()
        except:
            pass
        
        self.workers.clear()
        self.running = False
    
    def _calculate_final_metrics(self):
        """Calculate final benchmark metrics."""
        if not self.workers:
            return
        
        # Aggregate metrics from all workers
        all_spans_created = sum(w.spans_created for w in self.workers)
        all_spans_dropped = sum(w.spans_dropped for w in self.workers)
        all_spans_exported = sum(w.spans_exported for w in self.workers)
        all_overhead_samples = []
        all_operations_samples = []
        
        for worker in self.workers:
            all_overhead_samples.extend(worker.overhead_samples)
            all_operations_samples.extend(worker.operation_samples)
        
        # Calculate percentiles
        if all_overhead_samples:
            sorted_overhead = sorted(all_overhead_samples)
            n = len(sorted_overhead)
            if n > 0:
                self.metrics.span_overhead_p50 = sorted_overhead[n // 2]
                self.metrics.span_overhead_p95 = sorted_overhead[int(n * 0.95)]
                self.metrics.span_overhead_p99 = sorted_overhead[int(n * 0.99)]
        
        if all_operations_samples:
            sorted_ops = sorted(all_operations_samples)
            n_ops = len(sorted_ops)
            if n_ops > 0:
                self.metrics.operations_per_second = n_ops / self.metrics.duration_seconds if self.metrics.duration_seconds else 0
        
        # Get system metrics
        system_metrics = self.system_monitor.get_final_metrics()
        if system_metrics:
            self.metrics.cpu_usage_percent = system_metrics.get('cpu_avg', 0.0)
            self.metrics.memory_usage_mb = system_metrics.get('memory_avg_mb', 0.0)
    
        self.metrics.spans_created = all_spans_created
        self.metrics.spans_dropped = all_spans_dropped
        self.metrics.spans_exported = all_spans_exported


class TracingBenchWorker:
    """Individual tracing benchmark worker."""
    
    def __init__(self, worker_id: int, config: TracingBenchConfig, system_monitor):
        self.worker_id = worker_id
        self.config = config
        self.system_monitor = system_monitor
        
        # Metrics tracking
        self.spans_created = 0
        self.spans_dropped = 0
        self.spans_exported = 0
        self.overhead_samples: List[float] = []
        self.operation_samples: List[float] = []
        
        self.running = False
        
        logger.debug(f"Tracing benchmark worker {worker_id} initialized")
    
    async def run_benchmark(self, sampling_rate: float) -> None:
        """Run benchmark for a specific sampling rate."""
        logger.info(f"Worker {self.worker_id} starting benchmark with sampling rate {sampling_rate}")
        
        self.running = True
        start_time = time.time()
        
        try:
            # Run operations for specified duration
            end_time = start_time + self.config.duration_seconds
            
            while time.time() < end_time:
                operation_start = time.time()
                
                # Perform tracing operation
                await self._perform_tracing_operation(sampling_rate)
                
                operation_time = (time.time() - operation_start) * 1000  # Convert to microseconds
                self.operation_samples.append(operation_time)
                
                # Rate limiting
                elapsed = time.time() - operation_start
                sleep_time = max(0, (1.0 / self.config.operations_per_second) - elapsed)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
            
        except Exception as e:
            logger.error(f"Worker {self.worker_id} benchmark failed: {e}")
        finally:
            self.running = False
    
    async def _perform_tracing_operation(self, sampling_rate: float):
        """Perform a single tracing operation."""
        try:
            # Import optimized tracing
            import backend_app.backend.observability.optimized_tracing as optimized_tracing
            
            # Get trading tracer
            tracer = optimized_tracing.get_optimized_trading_tracer()
            
            # Perform operation based on type
            for op_type in self.config.operation_types:
                if op_type == "execution":
                    async with tracer.trace_execution(
                        exchange="binance",
                        symbol="BTC-USDT",
                        side="buy",
                        order_type="market",
                        quantity=0.01,
                        tenant_id=f"tenant_{self.worker_id}"
                    ):
                        # Simulate operation
                        await asyncio.sleep(0.001)  # 1ms operation
                elif op_type == "websocket":
                    async with tracer.trace_websocket_event(
                        channel="trades",
                        event_type="update",
                        tenant_id=f"tenant_{self.worker_id}"
                    ):
                        # Simulate WebSocket event
                        await asyncio.sleep(0.0005)  # 0.5ms operation
                elif op_type == "risk_validation":
                    async with tracer.trace_execution(
                        exchange="binance",
                        symbol="BTC-USDT",
                        side="buy",
                        order_type="market",
                        quantity=0.01,
                        tenant_id=f"tenant_{self.worker_id}"
                    ):
                        # Simulate risk validation
                        await asyncio.sleep(0.0002)  # 0.2ms operation
            
            # Update metrics
            self.spans_created += len(self.config.operation_types)
            
        except Exception as e:
            logger.error(f"Failed to perform tracing operation: {e}")
            self.spans_dropped += len(self.config.operation_types)
    
    async def stop(self):
        """Stop the benchmark worker."""
        logger.info(f"Stopping tracing benchmark worker {self.worker_id}")
        self.running = False


class SystemMonitor:
    """System resource monitor for benchmark."""
    
    def __init__(self):
        self.running = False
        self.metrics = {}
        self.monitor_thread = None
        self.stop_event = threading.Event()
        
        # Metrics tracking
        self.cpu_samples: List[float] = []
        self.memory_samples: List[float] = []
        self.gc_pause_samples: List[float] = []
    
    async def start(self):
        """Start system monitoring."""
        self.running = True
        self.stop_event.clear()
        
        # Start monitoring thread
        self.monitor_thread = threading.Thread(target=self._monitor_system, daemon=True)
        self.monitor_thread.start()
        
        logger.info("System monitor started")
    
    async def stop(self):
        """Stop system monitoring."""
        self.running = False
        self.stop_event.set()
        
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        
        logger.info("System monitor stopped")
    
    def _monitor_system(self):
        """Monitor system resources in background thread."""
        import gc
        import psutil
        
        while not self.stop_event.is_set():
            try:
                # CPU usage
                cpu_percent = psutil.cpu_percent(interval=0.1)
                self.cpu_samples.append(cpu_percent)
                
                # Memory usage
                memory = psutil.virtual_memory()
                memory_mb = memory.used / (1024 * 1024)
                self.memory_samples.append(memory_mb)
                
                # GC pause time
                gc.collect()
                gc_start = time.time()
                gc.collect()  # Force GC
                gc_pause = (time.time() - gc_start) * 1000
                self.gc_pause_samples.append(gc_pause)
                
                # Sleep for monitoring interval
                time.sleep(0.5)
                
            except Exception as e:
                logger.error(f"System monitoring error: {e}")
    
    def get_final_metrics(self) -> Dict[str, Any]:
        """Get final system metrics."""
        if not self.cpu_samples:
            return {}
        
        return {
            'cpu_avg': statistics.mean(self.cpu_samples) if self.cpu_samples else 0.0,
            'cpu_peak': max(self.cpu_samples) if self.cpu_samples else 0.0,
            'memory_avg_mb': statistics.mean(self.memory_samples) if self.memory_samples else 0.0,
            'memory_peak_mb': max(self.memory_samples) if self.memory_samples else 0.0,
            'gc_p50': statistics.mean(self.gc_pause_samples) if self.gc_pause_samples else 0.0,
            'gc_p95': statistics.quantiles(self.gc_pause_samples, [0.95])[0] if self.gc_pause_samples else 0.0
        }


# Predefined benchmark configurations
BENCH_CONFIGS = {
    "light_load": TracingBenchConfig(
        test_name="light_load",
        num_workers=5,
        operations_per_second=10,
        duration_seconds=60,
        sampling_rates=[1.0, 0.5, 0.1, 0.01],
        operation_types=["execution"],
        span_complexity=5
    ),
    
    "medium_load": TracingBenchConfig(
        test_name="medium_load",
        num_workers=10,
        operations_per_second=50,
        duration_seconds=60,
        sampling_rates=[1.0, 0.5, 0.1, 0.01],
        operation_types=["execution", "websocket"],
        span_complexity=10
    ),
    
    "heavy_load": TracingBenchConfig(
        test_name="heavy_load",
        num_workers=20,
        operations_per_second=100,
        duration_seconds=60,
        sampling_rates=[1.0, 0.5, 0.1, 0.01],
        operation_types=["execution", "websocket", "risk_validation"],
        span_complexity=20
    ),
    
    "high_frequency": TracingBenchConfig(
        test_name="high_frequency",
        num_workers=10,
        operations_per_second=1000,
        duration_seconds=30,
        sampling_rates=[0.1, 0.01, 0.001],
        operation_types=["websocket"],
        span_complexity=5
    )
}


async def run_tracing_benchmark(config_name: str) -> TracingBenchMetrics:
    """Run a predefined tracing benchmark."""
    config = BENCH_CONFIGS.get(config_name)
    if not config:
        raise ValueError(f"Unknown benchmark configuration: {config_name}")
    
    logger.info(f"Starting tracing benchmark: {config_name}")
    
    bench = TracingOverheadBench(config)
    metrics = await bench.run_benchmark()
    
    # Log results
    logger.info(f"Tracing benchmark results for {config_name}:")
    logger.info(f"  Sampling rate: {metrics.sampling_rate}")
    logger.info(f"  Duration: {metrics.duration_seconds:.2f}s")
    logger.info(f"  Operations/sec: {metrics.operations_per_second:.2f}")
    logger.info(f"  Span overhead P50: {metrics.span_overhead_p50:.2f}μs")
    logger.info(f"  Span overhead P95: {metrics.span_overhead_p95:.2f}μs")
    logger.info(f"  Spans created: {metrics.spans_created}")
    logger.info(f"  Spans dropped: {metrics.spans_dropped}")
    logger.info(f"  Spans exported: {metrics.spans_exported}")
    logger.info(f"  CPU usage: {metrics.cpu_usage_percent:.1f}%")
    logger.info(f"  Memory usage: {metrics.memory_usage_mb:.1f}MB")
    
    return metrics


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 2:
        print("Usage: python tracing_overhead_benchmark.py <config_name>")
        print("Available configs:", list(BENCH_CONFIGS.keys()))
        sys.exit(1)
    
    config_name = sys.argv[1]
    
    # Run tracing benchmark
    asyncio.run(run_tracing_benchmark(config_name))
