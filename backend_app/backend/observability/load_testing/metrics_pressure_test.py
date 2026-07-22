"""
Metrics Pressure Test for HFT Observability

Simulates high-frequency metrics collection to validate optimized observability stack.
Tests batching efficiency, memory growth, and system resource usage.

Author: Principal HFT Infrastructure Engineer
"""

import asyncio
import gc
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger("metrics_pressure_test")


@dataclass
class MetricsPressureConfig:
    """Configuration for metrics pressure test."""
    test_name: str
    num_workers: int
    metrics_per_second_per_worker: int
    batch_size: int
    duration_seconds: int
    memory_pressure_mb: int
    cardinality_limit: int


@dataclass
class MetricsPressureMetrics:
    """Metrics collected during pressure test."""
    test_name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    
    # Metrics collection metrics
    total_metrics_created: int = 0
    metrics_overhead_p50: float = 0.0
    metrics_overhead_p95: float = 0.0
    metrics_overhead_p99: float = 0.0
    batch_latency_p50: float = 0.0
    batch_latency_p95: float = 0.0
    batch_latency_p99: float = 0.0
    
    # Memory metrics
    peak_memory_usage_mb: float = 0.0
    avg_memory_usage_mb: float = 0.0
    memory_growth_rate_mb_per_min: float = 0.0
    
    # System metrics
    cpu_usage_avg: float = 0.0
    cpu_usage_peak: float = 0.0
    gc_pause_time_p50: float = 0.0
    gc_pause_time_p95: float = 0.0
    
    # Cardinality metrics
    unique_label_combinations: int = 0
    cardinality_violations: int = 0


class MetricsPressureTest:
    """Metrics pressure test for HFT observability validation."""
    
    def __init__(self, config: MetricsPressureConfig):
        self.config = config
        self.metrics = MetricsPressureMetrics(test_name=config.test_name)
        self.workers: List[MetricsPressureWorker] = []
        self.running = False
        
        # Performance monitoring
        self.system_monitor = SystemMonitor()
        
        # Thread pool for concurrent operations
        self.executor = ThreadPoolExecutor(max_workers=config.num_workers + 10, thread_name_prefix="metrics_pressure")
        
        logger.info(f"Metrics pressure test initialized: {config.test_name}")
    
    async def run_pressure_test(self) -> MetricsPressureMetrics:
        """Run metrics pressure test."""
        logger.info(f"Starting metrics pressure test: {self.config.test_name}")
        
        self.running = True
        self.metrics.start_time = datetime.now(timezone.utc)
        
        try:
            # Initialize optimized metrics system
            await self._initialize_metrics_system()
            
            # Create worker pool
            await self._create_workers()
            
            # Start system monitoring
            await self.system_monitor.start()
            
            # Run pressure test for specified duration
            start_time = time.time()
            end_time = start_time + self.config.duration_seconds
            
            while time.time() < end_time:
                await asyncio.sleep(1.0)
                
                # Collect system metrics
                await self.system_monitor.collect_metrics()
            
        except Exception as e:
            logger.error(f"Metrics pressure test failed: {e}")
        finally:
            # Cleanup
            await self._cleanup()
            
            self.metrics.end_time = datetime.now(timezone.utc)
            self.metrics.duration_seconds = (
                self.metrics.end_time - self.metrics.start_time
            ).total_seconds()
            
            # Calculate final metrics
            self._calculate_final_metrics()
            
            self.running = False
            logger.info(f"Metrics pressure test completed: {self.config.test_name}")
            return self.metrics
    
    async def _initialize_metrics_system(self):
        """Initialize optimized metrics system."""
        try:
            # Import optimized metrics
            import backend_app.backend.observability.optimized_metrics_exporter as optimized_metrics

            # Initialize optimized metrics collector
            self.metrics_collector = optimized_metrics.optimized_metrics_collector()
            await self.metrics_collector.start(port=8082)  # Different port for testing
            
            logger.info("Optimized metrics system initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize metrics system: {e}")
            raise
    
    async def _create_workers(self):
        """Create metrics pressure workers."""
        logger.info(f"Creating {self.config.num_workers} metrics pressure workers")
        
        for i in range(self.config.num_workers):
            worker = MetricsPressureWorker(
                worker_id=i,
                config=self.config,
                metrics_collector=self.metrics_collector,
                system_monitor=self.system_monitor
            )
            self.workers.append(worker)
    
    async def _cleanup(self):
        """Cleanup all workers and systems."""
        logger.info("Cleaning up metrics pressure test")
        
        # Stop all workers
        tasks = []
        for worker in self.workers:
            task = asyncio.create_task(worker.stop())
            tasks.append(task)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        # Stop system monitoring
        await self.system_monitor.stop()
        
        # Stop metrics collector
        if hasattr(self, 'metrics_collector'):
            await self.metrics_collector.stop()
        
        self.workers.clear()
    
    def _calculate_final_metrics(self):
        """Calculate final performance metrics."""
        if not self.workers:
            return
        
        # Collect all worker metrics
        all_overhead_samples = []
        all_batch_samples = []
        all_memory_samples = []
        
        for worker in self.workers:
            all_overhead_samples.extend(worker.overhead_samples)
            all_batch_samples.extend(worker.batch_samples)
            all_memory_samples.extend(worker.memory_samples)
        
        # Calculate percentile metrics
        if all_overhead_samples:
            sorted_overhead = sorted(all_overhead_samples)
            n = len(sorted_overhead)
            if n > 0:
                self.metrics.metrics_overhead_p50 = sorted_overhead[n // 2]
                self.metrics.metrics_overhead_p95 = sorted_overhead[int(n * 0.95)]
                self.metrics.metrics_overhead_p99 = sorted_overhead[int(n * 0.99)]
        
        if all_batch_samples:
            sorted_batch = sorted(all_batch_samples)
            n = len(sorted_batch)
            if n > 0:
                self.metrics.batch_latency_p50 = sorted_batch[n // 2]
                self.metrics.batch_latency_p95 = sorted_batch[int(n * 0.95)]
                self.metrics.batch_latency_p99 = sorted_batch[int(n * 0.99)]
        
        # System metrics from system monitor
        if self.system_monitor.metrics:
            self.metrics.cpu_usage_avg = self.system_monitor.metrics.get('cpu_avg', 0.0)
            self.metrics.cpu_usage_peak = self.system_monitor.metrics.get('cpu_peak', 0.0)
            self.metrics.peak_memory_usage_mb = self.system_monitor.metrics.get('memory_peak_mb', 0.0)
            self.metrics.avg_memory_usage_mb = self.system_monitor.metrics.get('memory_avg_mb', 0.0)
            self.metrics.gc_pause_time_p50 = self.system_monitor.metrics.get('gc_p50', 0.0)
            self.metrics.gc_pause_time_p95 = self.system_monitor.metrics.get('gc_p95', 0.0)
            
            # Calculate memory growth rate
            if all_memory_samples and len(all_memory_samples) > 1:
                memory_growth = all_memory_samples[-1] - all_memory_samples[0]
                duration_minutes = self.config.duration_seconds / 60
                self.metrics.memory_growth_rate_mb_per_min = memory_growth / max(duration_minutes, 1)
        
        # Calculate total metrics created
        for worker in self.workers:
            self.metrics.total_metrics_created += worker.total_metrics_created
            self.metrics.unique_label_combinations += worker.unique_label_combinations
            self.metrics.cardinality_violations += worker.cardinality_violations


class MetricsPressureWorker:
    """Individual metrics pressure worker."""
    
    def __init__(self, worker_id: int, config: MetricsPressureConfig, 
                 metrics_collector, system_monitor):
        self.worker_id = worker_id
        self.config = config
        self.metrics_collector = metrics_collector
        self.system_monitor = system_monitor
        
        self.running = False
        self.total_metrics_created = 0
        self.unique_label_combinations = 0
        self.cardinality_violations = 0
        
        # Performance tracking
        self.overhead_samples: List[float] = []
        self.batch_samples: List[float] = []
        self.memory_samples: List[float] = []
        
        logger.debug(f"Metrics pressure worker {worker_id} initialized")
    
    async def start(self):
        """Start metrics pressure worker."""
        logger.info(f"Starting metrics pressure worker {self.worker_id}")
        self.running = True
        
        try:
            # Run pressure test
            start_time = time.time()
            end_time = start_time + self.config.duration_seconds
            
            while time.time() < end_time and self.running:
                await self._generate_metrics_pressure()
                await asyncio.sleep(0.001)  # 1ms between batches
                
        except Exception as e:
            logger.error(f"Worker {self.worker_id} failed: {e}")
        finally:
            self.running = False
    
    async def _generate_metrics_pressure(self):
        """Generate high-frequency metrics to test system pressure."""
        start_time = time.time()
        
        # Generate metrics batch
        metrics_batch = []
        for i in range(self.config.batch_size):
            metric_name = f"pressure_metric_{self.worker_id}_{i}"
            labels = {
                'worker': str(self.worker_id),
                'test': self.config.test_name,
                'batch': str(i),
                'unique_id': f"unique_{self.worker_id}_{i}_{int(time.time() * 1000)}"
            }
            value = random.uniform(0.1, 100.0)
            
            # Record metric
            await self.metrics_collector.inc_counter(metric_name, value, labels)
            
            metrics_batch.append(metric_name)
            self.total_metrics_created += 1
            
            # Track unique label combinations
            if len(labels) > 50:  # Simulate cardinality pressure
                self.unique_label_combinations += 1
            else:
                self.unique_label_combinations += 1
        
        # Measure batch processing time
        batch_time = (time.time() - start_time) * 1000
        self.batch_samples.append(batch_time)
        self.overhead_samples.append(batch_time)
        
        # Simulate memory usage
        if random.random() < 0.1:  # 10% chance of memory pressure
            memory_usage = self.config.memory_pressure_mb + random.uniform(0, 50)
        else:
            memory_usage = random.uniform(10, 50)
        
        self.memory_samples.append(memory_usage)
        
        # Check for cardinality violations
        if self.unique_label_combinations > self.config.cardinality_limit:
            self.cardinality_violations += 1
            logger.warning(f"Worker {self.worker_id} cardinality limit exceeded")
    
    async def stop(self):
        """Stop metrics pressure worker."""
        logger.info(f"Stopping metrics pressure worker {self.worker_id}")
        self.running = False


class SystemMonitor:
    """System resource monitor for pressure testing."""
    
    def __init__(self):
        self.running = False
        self.metrics = {}
        self.monitor_thread = None
        self.stop_event = threading.Event()
        
        logger.debug("System monitor initialized")
    
    async def start(self):
        """Start system monitoring."""
        self.running = True
        self.stop_event.clear()
        
        # Start monitoring thread
        self.monitor_thread = threading.Thread(target=self._monitor_system, daemon=True)
        self.monitor_thread.start()
        
        logger.info("System monitoring started")
    
    async def stop(self):
        """Stop system monitoring."""
        self.running = False
        self.stop_event.set()
        
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        
        logger.info("System monitoring stopped")
    
    def _monitor_system(self):
        """Monitor system resources in background thread."""
        import psutil
        
        cpu_samples = []
        memory_samples = []
        gc_pause_samples = []
        
        while not self.stop_event.is_set():
            try:
                # CPU usage
                cpu_percent = psutil.cpu_percent(interval=0.1)
                cpu_samples.append(cpu_percent)
                
                # Memory usage
                memory = psutil.virtual_memory()
                memory_mb = memory.used / (1024 * 1024)
                memory_samples.append(memory_mb)
                
                # GC pause time
                gc.collect()
                gc_start = time.time()
                gc.collect()  # Force GC
                gc_pause = (time.time() - gc_start) * 1000
                gc_pause_samples.append(gc_pause)
                
                # Sleep for monitoring interval
                time.sleep(0.5)
                
            except Exception as e:
                logger.error(f"System monitoring error: {e}")
        
        # Calculate final metrics
        if cpu_samples:
            self.metrics['cpu_avg'] = sum(cpu_samples) / len(cpu_samples)
            self.metrics['cpu_peak'] = max(cpu_samples)
        
        if memory_samples:
            self.metrics['memory_avg'] = sum(memory_samples) / len(memory_samples)
            self.metrics['memory_peak_mb'] = max(memory_samples)
        
        if gc_pause_samples:
            sorted_gc = sorted(gc_pause_samples)
            n = len(sorted_gc)
            if n > 0:
                self.metrics['gc_p50'] = sorted_gc[n // 2]
                self.metrics['gc_p95'] = sorted_gc[int(n * 0.95)]


# Predefined pressure test configurations
PRESSURE_TEST_CONFIGS = {
    "light_pressure": MetricsPressureConfig(
        test_name="light_pressure",
        num_workers=5,
        metrics_per_second_per_worker=10,
        batch_size=10,
        duration_seconds=60,
        memory_pressure_mb=50,
        cardinality_limit=1000
    ),
    
    "medium_pressure": MetricsPressureConfig(
        test_name="medium_pressure",
        num_workers=20,
        metrics_per_second_per_worker=25,
        batch_size=50,
        duration_seconds=60,
        memory_pressure_mb=100,
        cardinality_limit=5000
    ),
    
    "heavy_pressure": MetricsPressureConfig(
        test_name="heavy_pressure",
        num_workers=50,
        metrics_per_second_per_worker=50,
        batch_size=100,
        duration_seconds=60,
        memory_pressure_mb=200,
        cardinality_limit=10000
    ),
    
    "cardinality_stress": MetricsPressureConfig(
        test_name="cardinality_stress",
        num_workers=10,
        metrics_per_second_per_worker=100,
        batch_size=1000,
        duration_seconds=30,
        memory_pressure_mb=100,
        cardinality_limit=100
    )
}


async def run_metrics_pressure_test(config_name: str) -> MetricsPressureMetrics:
    """Run a predefined metrics pressure test."""
    config = PRESSURE_TEST_CONFIGS.get(config_name)
    if not config:
        raise ValueError(f"Unknown pressure test configuration: {config_name}")
    
    logger.info(f"Starting metrics pressure test: {config_name}")
    
    test = MetricsPressureTest(config)
    metrics = await test.run_pressure_test()
    
    # Log results
    logger.info(f"Metrics pressure test results for {config_name}:")
    logger.info(f"  Duration: {metrics.duration_seconds:.2f}s")
    logger.info(f"  Total metrics created: {metrics.total_metrics_created}")
    logger.info(f"  Metrics overhead P50: {metrics.metrics_overhead_p50:.2f}μs")
    logger.info(f"  Metrics overhead P95: {metrics.metrics_overhead_p95:.2f}μs")
    logger.info(f"  Batch latency P50: {metrics.batch_latency_p50:.2f}μs")
    logger.info(f"  Batch latency P95: {metrics.batch_latency_p95:.2f}μs")
    logger.info(f"  Peak memory: {metrics.peak_memory_usage_mb:.1f}MB")
    logger.info(f"  Avg memory: {metrics.avg_memory_usage_mb:.1f}MB")
    logger.info(f"  Cardinality violations: {metrics.cardinality_violations}")
    logger.info(f"  Unique label combos: {metrics.unique_label_combinations}")
    logger.info(f"  CPU avg: {metrics.cpu_usage_avg:.1f}%")
    logger.info(f"  CPU peak: {metrics.cpu_usage_peak:.1f}%")
    logger.info(f"  GC pause P50: {metrics.gc_pause_time_p50:.2f}ms")
    logger.info(f"  GC pause P95: {metrics.gc_pause_time_p95:.2f}ms")
    
    return metrics


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 2:
        print("Usage: python metrics_pressure_test.py <config_name>")
        print("Available configs:", list(PRESSURE_TEST_CONFIGS.keys()))
        sys.exit(1)
    
    config_name = sys.argv[1]
    
    # Run metrics pressure test
    asyncio.run(run_metrics_pressure_test(config_name))
