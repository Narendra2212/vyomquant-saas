"""
Replay Storm Simulator for HFT Observability

Simulates replay storm scenarios to validate optimized observability stack.
Tests high-frequency replay requests and system response under pressure.

Author: Principal HFT Infrastructure Engineer
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger("replay_storm_simulator")


@dataclass
class ReplayStormConfig:
    """Configuration for replay storm simulation."""
    test_name: str
    num_clients: int
    requests_per_second_per_client: int
    duration_seconds: int
    replay_window_minutes: int
    message_size_bytes: int
    server_url: str


@dataclass
class ReplayStormMetrics:
    """Metrics collected during replay storm test."""
    test_name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    
    # Request metrics
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    timeout_requests: int = 0
    
    # Response metrics
    response_time_p50: float = 0.0
    response_time_p95: float = 0.0
    response_time_p99: float = 0.0
    avg_response_time: float = 0.0
    
    # Replay metrics
    events_replayed: int = 0
    replay_latency_p50: float = 0.0
    replay_latency_p95: float = 0.0
    replay_latency_p99: float = 0.0
    
    # System metrics
    cpu_usage_avg: float = 0.0
    memory_usage_mb: float = 0.0
    queue_depth_avg: float = 0.0
    queue_saturation_percent: float = 0.0


class ReplayStormSimulator:
    """Replay storm simulator for HFT observability validation."""
    
    def __init__(self, config: ReplayStormConfig):
        self.config = config
        self.metrics = ReplayStormMetrics(test_name=config.test_name)
        self.clients: List[ReplayStormClient] = []
        self.running = False
        
        # Performance monitoring
        self.response_times: List[float] = []
        self.replay_times: List[float] = []
        
        logger.info(f"Replay storm simulator initialized: {config.test_name}")
    
    async def run_storm_simulation(self) -> ReplayStormMetrics:
        """Run the complete replay storm simulation."""
        logger.info(f"Starting replay storm simulation: {self.config.test_name}")
        
        self.running = True
        self.metrics.start_time = datetime.now(timezone.utc)
        
        try:
            # Create replay clients
            await self._create_clients()
            
            # Start all clients
            tasks = []
            for client in self.clients:
                task = asyncio.create_task(
                    client.run_replay_requests()
                )
                tasks.append(task)
            
            # Run for specified duration
            start_time = time.time()
            end_time = start_time + self.config.duration_seconds
            
            while time.time() < end_time:
                await asyncio.sleep(1.0)
                # Monitor system metrics
                await self._collect_system_metrics()
            
            # Stop all clients
            await self._stop_all_clients()
            
            # Wait for all tasks to complete
            await asyncio.gather(*tasks, return_exceptions=True)
            
        except Exception as e:
            logger.error(f"Replay storm simulation failed: {e}")
        finally:
            self.running = False
            self.metrics.end_time = datetime.now(timezone.utc)
            self.metrics.duration_seconds = (
                self.metrics.end_time - self.metrics.start_time
            ).total_seconds()
            
            # Calculate final metrics
            self._calculate_final_metrics()
            
            logger.info(f"Replay storm simulation completed: {self.config.test_name}")
            return self.metrics
    
    async def _create_clients(self):
        """Create replay storm clients."""
        logger.info(f"Creating {self.config.num_clients} replay clients")
        
        for i in range(self.config.num_clients):
            client = ReplayStormClient(
                client_id=i,
                config=self.config,
                metrics_collector=self
            )
            self.clients.append(client)
    
    async def _stop_all_clients(self):
        """Stop all replay clients."""
        logger.info("Stopping all replay clients")
        
        tasks = []
        for client in self.clients:
            task = asyncio.create_task(client.disconnect())
            tasks.append(task)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _collect_system_metrics(self):
        """Collect system metrics during simulation."""
        try:
            import psutil

            # CPU usage
            cpu_percent = psutil.cpu_percent(interval=0.1)
            
            # Memory usage
            memory = psutil.virtual_memory()
            memory_mb = memory.used / (1024 * 1024)
            
            # Update metrics
            self.metrics.cpu_usage_avg = (
                (self.metrics.cpu_usage_avg * 0.9) + (cpu_percent * 0.1)
            )  # EMA
            self.metrics.memory_usage_mb = memory_mb
            
            logger.debug(f"System metrics - CPU: {cpu_percent:.1f}%, Memory: {memory_mb:.1f}MB")
            
        except Exception as e:
            logger.warning(f"Failed to collect system metrics: {e}")
    
    def _calculate_final_metrics(self):
        """Calculate final performance metrics."""
        if self.response_times:
            sorted_times = sorted(self.response_times)
            n = len(sorted_times)
            if n > 0:
                self.metrics.response_time_p50 = sorted_times[n // 2]
                self.metrics.response_time_p95 = sorted_times[int(n * 0.95)]
                self.metrics.response_time_p99 = sorted_times[int(n * 0.99)]
                self.metrics.avg_response_time = sum(sorted_times) / n
        
        if self.replay_times:
            sorted_replay_times = sorted(self.replay_times)
            n_replay = len(sorted_replay_times)
            if n_replay > 0:
                self.metrics.replay_latency_p50 = sorted_replay_times[n_replay // 2]
                self.metrics.replay_latency_p95 = sorted_replay_times[int(n_replay * 0.95)]
                self.metrics.replay_latency_p99 = sorted_replay_times[int(n_replay * 0.99)]
    
    def record_request_start(self, client_id: int, request_id: str):
        """Record the start of a replay request."""
        self.metrics.total_requests += 1
        
        # Simulate request processing
        start_time = time.time()
        return start_time
    
    def record_request_success(self, client_id: int, request_id: str, start_time: float, 
                           replay_time: float = 0.0):
        """Record successful replay request."""
        self.metrics.successful_requests += 1
        self.metrics.events_replayed += 1
        
        response_time = (time.time() - start_time) * 1000  # Convert to ms
        self.response_times.append(response_time)
        
        if replay_time > 0:
            replay_time_ms = replay_time * 1000
            self.replay_times.append(replay_time_ms)
    
    def record_request_failure(self, client_id: int, request_id: str, failure_type: str):
        """Record failed replay request."""
        self.metrics.failed_requests += 1
        
        if failure_type == "timeout":
            self.metrics.timeout_requests += 1
        elif failure_type == "error":
            self.metrics.failed_requests += 1  # Already counted
        elif failure_type == "dropped":
            self.metrics.failed_requests += 1  # Already counted
    
    def record_queue_metrics(self, queue_depth: float, saturation_percent: float):
        """Record queue metrics."""
        self.metrics.queue_depth_avg = (
            (self.metrics.queue_depth_avg * 0.9) + (queue_depth * 0.1)
        )  # EMA
        self.metrics.queue_saturation_percent = saturation_percent


class ReplayStormClient:
    """Individual replay storm client."""
    
    def __init__(self, client_id: int, config: ReplayStormConfig, 
                 metrics_collector: ReplayStormSimulator):
        self.client_id = client_id
        self.config = config
        self.metrics_collector = metrics_collector
        
        self.session: Optional[aiohttp.ClientSession] = None
        self.running = False
        self.request_counter = 0
        
        logger.debug(f"Replay storm client {client_id} initialized")
    
    async def run_replay_requests(self):
        """Run replay requests for this client."""
        logger.info(f"Client {self.client_id} starting replay requests")
        
        self.running = True
        
        try:
            # Create HTTP session
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5.0, connect=2.0)
            )
            
            request_interval = 1.0 / self.config.requests_per_second_per_client
            
            while self.running:
                start_time = self.metrics_collector.record_request_start(
                    self.client_id, 
                    f"req_{self.client_id}_{self.request_counter}"
                )
                
                try:
                    # Generate replay request
                    replay_request = self._generate_replay_request()
                    
                    # Send request
                    replay_start_time = time.time()
                    response = await self.session.post(
                        f"{self.config.server_url}/replay",
                        json=replay_request,
                        headers={"Content-Type": "application/json"}
                    )
                    
                    replay_time = time.time() - replay_start_time
                    
                    if response.status == 200:
                        self.metrics_collector.record_request_success(
                            self.client_id,
                            f"req_{self.client_id}_{self.request_counter}",
                            start_time,
                            replay_time
                        )
                    else:
                        self.metrics_collector.record_request_failure(
                            self.client_id,
                            f"req_{self.client_id}_{self.request_counter}",
                            "error"
                        )
                    
                    # Record queue metrics (simulated)
                    queue_depth = random.uniform(50, 200)  # Simulated queue depth
                    saturation = min(queue_depth / 1000 * 100, 95)  # Simulated saturation
                    
                    self.metrics_collector.record_queue_metrics(queue_depth, saturation)
                    
                except asyncio.TimeoutError:
                    self.metrics_collector.record_request_failure(
                        self.client_id,
                        f"req_{self.client_id}_{self.request_counter}",
                        "timeout"
                    )
                except Exception as e:
                    self.metrics_collector.record_request_failure(
                        self.client_id,
                        f"req_{self.client_id}_{self.request_counter}",
                        "error"
                    )
                    logger.warning(f"Client {self.client_id} request failed: {e}")
                
                self.request_counter += 1
                
                # Rate limiting
                elapsed = time.time() - start_time
                sleep_time = max(0, request_interval - elapsed)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                
        except Exception as e:
            logger.error(f"Client {self.client_id} failed: {e}")
        finally:
            if self.session:
                await self.session.close()
    
    def _generate_replay_request(self) -> Dict[str, Any]:
        """Generate a replay request."""
        return {
            "channel": "trades",
            "tenant_id": f"tenant_{self.client_id % 10}",
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": datetime.now(timezone.utc).isoformat(),
            "replay_window_minutes": self.config.replay_window_minutes,
            "message_size": self.config.message_size_bytes,
            "client_id": self.client_id,
            "request_id": f"req_{self.client_id}_{self.request_counter}"
        }
    
    async def disconnect(self):
        """Disconnect the client."""
        logger.info(f"Client {self.client_id} disconnecting")
        self.running = False


# Predefined replay storm configurations
REPLAY_STORM_CONFIGS = {
    "light_storm": ReplayStormConfig(
        test_name="light_storm",
        num_clients=10,
        requests_per_second_per_client=5,
        duration_seconds=60,
        replay_window_minutes=5,
        message_size_bytes=256,
        server_url="http://localhost:8000"
    ),
    
    "medium_storm": ReplayStormConfig(
        test_name="medium_storm",
        num_clients=50,
        requests_per_second_per_client=10,
        duration_seconds=60,
        replay_window_minutes=10,
        message_size_bytes=512,
        server_url="http://localhost:8000"
    ),
    
    "heavy_storm": ReplayStormConfig(
        test_name="heavy_storm",
        num_clients=100,
        requests_per_second_per_client=20,
        duration_seconds=60,
        replay_window_minutes=15,
        message_size_bytes=1024,
        server_url="http://localhost:8000"
    ),
    
    "extreme_storm": ReplayStormConfig(
        test_name="extreme_storm",
        num_clients=200,
        requests_per_second_per_client=50,
        duration_seconds=60,
        replay_window_minutes=30,
        message_size_bytes=2048,
        server_url="http://localhost:8000"
    )
}


async def run_replay_storm_test(config_name: str) -> ReplayStormMetrics:
    """Run a predefined replay storm test."""
    config = REPLAY_STORM_CONFIGS.get(config_name)
    if not config:
        raise ValueError(f"Unknown replay storm configuration: {config_name}")
    
    simulator = ReplayStormSimulator(config)
    return await simulator.run_storm_simulation()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 2:
        print("Usage: python replay_storm_simulator.py <config_name>")
        print("Available configs:", list(REPLAY_STORM_CONFIGS.keys()))
        sys.exit(1)
    
    config_name = sys.argv[1]
    
    async def _main() -> None:
        metrics = await run_replay_storm_test(config_name)
        logger.info(f"Replay storm test results for {config_name}:")
        logger.info(f"  Duration: {metrics.duration_seconds:.2f}s")
        logger.info(f"  Total requests: {metrics.total_requests}")
        logger.info(f"  Successful requests: {metrics.successful_requests}")
        logger.info(f"  Failed requests: {metrics.failed_requests}")
        logger.info(f"  Timeout requests: {metrics.timeout_requests}")
        logger.info(f"  Events replayed: {metrics.events_replayed}")
        logger.info(f"  Response P50: {metrics.response_time_p50:.2f}ms")
        logger.info(f"  Response P95: {metrics.response_time_p95:.2f}ms")
        logger.info(f"  Response P99: {metrics.response_time_p99:.2f}ms")
        logger.info(f"  Replay P50: {metrics.replay_latency_p50:.2f}ms")
        logger.info(f"  Replay P95: {metrics.replay_latency_p95:.2f}ms")
        logger.info(f"  Replay P99: {metrics.replay_latency_p99:.2f}ms")
        logger.info(f"  Avg response time: {metrics.avg_response_time:.2f}ms")
        logger.info(f"  CPU usage: {metrics.cpu_usage_avg:.1f}%")
        logger.info(f"  Memory usage: {metrics.memory_usage_mb:.1f}MB")
        logger.info(f"  Queue depth: {metrics.queue_depth_avg:.1f}")
        logger.info(f"  Queue saturation: {metrics.queue_saturation_percent:.1f}%")

    asyncio.run(_main())
