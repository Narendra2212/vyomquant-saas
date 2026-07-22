"""
WebSocket Stress Test for HFT Observability

Simulates high-frequency WebSocket traffic to validate observability overhead.
Tests connection storms, message floods, and reconnection scenarios.

Author: Principal HFT Infrastructure Engineer
"""

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import websockets

logger = logging.getLogger("websocket_stress_test")


@dataclass
class WebSocketStressConfig:
    """Configuration for WebSocket stress test."""
    test_name: str
    num_clients: int
    messages_per_second_per_client: int
    duration_seconds: int
    message_size_bytes: int
    reconnect_interval_seconds: float
    burst_intervals: List[int]  # Seconds when to trigger bursts


@dataclass
class WebSocketStressMetrics:
    """Metrics collected during WebSocket stress test."""
    test_name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    
    # Connection metrics
    total_connections: int = 0
    successful_connections: int = 0
    failed_connections: int = 0
    reconnect_count: int = 0
    
    # Message metrics
    total_messages_sent: int = 0
    total_messages_received: int = 0
    messages_dropped: int = 0
    
    # Latency metrics (microseconds)
    send_latency_p50: float = 0.0
    send_latency_p95: float = 0.0
    send_latency_p99: float = 0.0
    receive_latency_p50: float = 0.0
    receive_latency_p95: float = 0.0
    receive_latency_p99: float = 0.0
    
    # Performance metrics
    cpu_usage_percent: float = 0.0
    memory_usage_mb: float = 0.0
    event_loop_blocked_ms: float = 0.0


class WebSocketStressTest:
    """WebSocket stress test for HFT observability validation."""
    
    def __init__(self, config: WebSocketStressConfig):
        self.config = config
        self.metrics = WebSocketStressMetrics(test_name=config.test_name)
        self.clients: List[WebSocketStressClient] = []
        self.running = False
        self.server_url = "ws://localhost:8000/ws"  # Local WebSocket server
        
        # Performance monitoring
        self.latency_samples: List[float] = []
        self.message_timestamps: Dict[str, float] = {}
        
        logger.info(f"WebSocket stress test initialized: {config.test_name}")
    
    async def run_stress_test(self) -> WebSocketStressMetrics:
        """Run the complete WebSocket stress test."""
        logger.info(f"Starting WebSocket stress test: {self.config.test_name}")
        
        self.running = True
        self.metrics.start_time = datetime.now(timezone.utc)
        
        try:
            # Create WebSocket clients
            await self._create_clients()
            
            # Start stress test
            await self._run_stress_scenario()
            
        except Exception as e:
            logger.error(f"WebSocket stress test failed: {e}")
        finally:
            # Cleanup
            await self._cleanup()
            
            self.metrics.end_time = datetime.now(timezone.utc)
            self.metrics.duration_seconds = (
                self.metrics.end_time - self.metrics.start_time
            ).total_seconds()
            
            # Calculate final metrics
            self._calculate_final_metrics()
            
            logger.info(f"WebSocket stress test completed: {self.config.test_name}")
            return self.metrics
    
    async def _create_clients(self):
        """Create WebSocket stress clients."""
        logger.info(f"Creating {self.config.num_clients} WebSocket clients")
        
        for i in range(self.config.num_clients):
            client = WebSocketStressClient(
                client_id=i,
                config=self.config,
                server_url=self.server_url,
                metrics_collector=self
            )
            self.clients.append(client)
    
    async def _run_stress_scenario(self):
        """Run the stress test scenario."""
        logger.info("Starting stress test scenario")
        
        # Start all clients
        tasks = []
        for client in self.clients:
            task = asyncio.create_task(
                self._run_client_with_monitoring(client)
            )
            tasks.append(task)
        
        # Monitor for duration
        start_time = time.time()
        
        while time.time() - start_time < self.config.duration_seconds:
            await asyncio.sleep(1.0)
            
            # Check for burst intervals
            current_time = int(time.time() - start_time)
            if current_time in self.config.burst_intervals:
                logger.info(f"Triggering burst at second {current_time}")
                await self._trigger_burst()
        
        # Wait for all tasks to complete
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _run_client_with_monitoring(self, client: 'WebSocketStressClient'):
        """Run a single client with monitoring."""
        try:
            await client.connect_and_run()
        except Exception as e:
            logger.error(f"Client {client.client_id} failed: {e}")
            self.metrics.failed_connections += 1
    
    async def _trigger_burst(self):
        """Trigger a burst of messages from all clients."""
        logger.info("Triggering message burst")
        
        tasks = []
        for client in self.clients:
            if client.connected:
                task = asyncio.create_task(client.send_burst())
                tasks.append(task)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _cleanup(self):
        """Cleanup all WebSocket clients."""
        logger.info("Cleaning up WebSocket clients")
        
        tasks = []
        for client in self.clients:
            task = asyncio.create_task(client.disconnect())
            tasks.append(task)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        self.running = False
    
    def _calculate_final_metrics(self):
        """Calculate final performance metrics."""
        if self.latency_samples:
            sorted_samples = sorted(self.latency_samples)
            n = len(sorted_samples)
            if n > 0:
                self.metrics.send_latency_p50 = sorted_samples[n // 2]
                self.metrics.send_latency_p95 = sorted_samples[int(n * 0.95)]
                self.metrics.send_latency_p99 = sorted_samples[int(n * 0.99)]
    
    def record_message_sent(self, client_id: int, message_id: str, send_time: float):
        """Record a message sent event."""
        self.message_timestamps[message_id] = send_time
        self.metrics.total_messages_sent += 1
    
    def record_message_received(self, client_id: int, message_id: str, receive_time: float):
        """Record a message received event."""
        send_time = self.message_timestamps.get(message_id, 0.0)
        if send_time > 0:
            latency = (receive_time - send_time) * 1_000_000  # Convert to microseconds
            self.latency_samples.append(latency)
            # Remove old timestamp to prevent memory growth
            if len(self.message_timestamps) > 10000:
                oldest_key = min(self.message_timestamps.keys())
                del self.message_timestamps[oldest_key]
        
        self.metrics.total_messages_received += 1
    
    def record_connection(self, client_id: int, success: bool):
        """Record connection event."""
        self.metrics.total_connections += 1
        if success:
            self.metrics.successful_connections += 1
        else:
            self.metrics.failed_connections += 1
    
    def record_reconnect(self, client_id: int):
        """Record reconnection event."""
        self.metrics.reconnect_count += 1
    
    def record_message_dropped(self, client_id: int):
        """Record message drop event."""
        self.metrics.messages_dropped += 1


class WebSocketStressClient:
    """Individual WebSocket stress client."""
    
    def __init__(self, client_id: int, config: WebSocketStressConfig, 
                 server_url: str, metrics_collector: WebSocketStressTest):
        self.client_id = client_id
        self.config = config
        self.server_url = server_url
        self.metrics_collector = metrics_collector
        
        self.websocket = None
        self.connected = False
        self.running = False
        
        # Message generation
        self.message_counter = 0
        
        logger.debug(f"WebSocket stress client {client_id} initialized")
    
    async def connect_and_run(self):
        """Connect to WebSocket server and run stress test."""
        try:
            logger.info(f"Client {self.client_id} connecting to {self.server_url}")
            
            # Connect to WebSocket
            self.websocket = await websockets.connect(
                self.server_url,
                ping_interval=10,
                ping_timeout=5,
                close_timeout=1
            )
            
            self.connected = True
            self.running = True
            self.metrics_collector.record_connection(self.client_id, True)
            
            logger.info(f"Client {self.client_id} connected successfully")
            
            # Run stress loop
            await self._stress_loop()
            
        except Exception as e:
            logger.error(f"Client {self.client_id} connection failed: {e}")
            self.metrics_collector.record_connection(self.client_id, False)
            raise
    
    async def _stress_loop(self):
        """Main stress loop for the client."""
        logger.info(f"Client {self.client_id} starting stress loop")
        
        message_interval = 1.0 / self.config.messages_per_second_per_client
        
        while self.running and self.connected:
            try:
                start_time = time.time()
                
                # Send message
                message = self._generate_message()
                send_time = time.time()
                
                await self.websocket.send(message)
                
                self.metrics_collector.record_message_sent(
                    self.client_id, 
                    message['id'], 
                    send_time
                )
                
                # Wait for rate limiting
                elapsed = time.time() - start_time
                sleep_time = max(0, message_interval - elapsed)
                
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                
                # Handle reconnection logic
                if random.random() < 0.001:  # 0.1% chance of disconnect
                    await self._handle_reconnect()
                
            except Exception as e:
                logger.error(f"Client {self.client_id} stress loop error: {e}")
                self.metrics_collector.record_message_dropped(self.client_id)
                await asyncio.sleep(0.1)
    
    async def send_burst(self):
        """Send a burst of messages."""
        logger.info(f"Client {self.client_id} sending burst")
        
        for i in range(10):  # Send 10 messages in burst
            try:
                message = self._generate_message()
                send_time = time.time()
                
                await self.websocket.send(message)
                
                self.metrics_collector.record_message_sent(
                    self.client_id,
                    message['id'],
                    send_time
                )
                
                await asyncio.sleep(0.01)  # 10ms between burst messages
                
            except Exception as e:
                logger.error(f"Client {self.client_id} burst message failed: {e}")
                self.metrics_collector.record_message_dropped(self.client_id)
    
    async def _handle_reconnect(self):
        """Handle reconnection."""
        logger.info(f"Client {self.client_id} handling reconnection")
        
        self.connected = False
        self.metrics_collector.record_reconnect(self.client_id)
        
        try:
            if self.websocket:
                await self.websocket.close()
            
            # Wait before reconnecting
            await asyncio.sleep(self.config.reconnect_interval_seconds)
            
            # Reconnect
            await self.connect_and_run()
            
        except Exception as e:
            logger.error(f"Client {self.client_id} reconnection failed: {e}")
            self.running = False
    
    def _generate_message(self) -> Dict[str, Any]:
        """Generate a test message."""
        self.message_counter += 1
        
        message = {
            "id": f"msg_{self.client_id}_{self.message_counter}",
            "client_id": self.client_id,
            "timestamp": time.time(),
            "type": "stress_test",
            "size": self.config.message_size_bytes,
            "data": "x" * (self.config.message_size_bytes - 100)  # Account for JSON overhead
        }
        
        return json.dumps(message)
    
    async def disconnect(self):
        """Disconnect the WebSocket client."""
        logger.info(f"Client {self.client_id} disconnecting")
        
        self.running = False
        self.connected = False
        
        if self.websocket:
            await self.websocket.close()
            self.websocket = None


# Predefined stress test configurations
STRESS_TEST_CONFIGS = {
    "low_frequency": WebSocketStressConfig(
        test_name="low_frequency_stress",
        num_clients=10,
        messages_per_second_per_client=10,
        duration_seconds=60,
        message_size_bytes=256,
        reconnect_interval_seconds=30.0,
        burst_intervals=[15, 30, 45]
    ),
    
    "medium_frequency": WebSocketStressConfig(
        test_name="medium_frequency_stress",
        num_clients=50,
        messages_per_second_per_client=25,
        duration_seconds=60,
        message_size_bytes=512,
        reconnect_interval_seconds=15.0,
        burst_intervals=[10, 20, 30, 40, 50]
    ),
    
    "high_frequency": WebSocketStressConfig(
        test_name="high_frequency_stress",
        num_clients=100,
        messages_per_second_per_client=50,
        duration_seconds=60,
        message_size_bytes=1024,
        reconnect_interval_seconds=10.0,
        burst_intervals=[5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]
    ),
    
    "connection_storm": WebSocketStressConfig(
        test_name="connection_storm",
        num_clients=200,
        messages_per_second_per_client=5,
        duration_seconds=60,
        message_size_bytes=128,
        reconnect_interval_seconds=5.0,
        burst_intervals=[2, 4, 6, 8, 10, 12, 14, 16, 18, 20]
    ),
    
    "message_flood": WebSocketStressConfig(
        test_name="message_flood",
        num_clients=20,
        messages_per_second_per_client=100,
        duration_seconds=30,
        message_size_bytes=256,
        reconnect_interval_seconds=60.0,
        burst_intervals=[5, 10, 15, 20, 25]
    )
}


async def run_websocket_stress_test(config_name: str) -> WebSocketStressMetrics:
    """Run a predefined WebSocket stress test."""
    config = STRESS_TEST_CONFIGS.get(config_name)
    if not config:
        raise ValueError(f"Unknown stress test configuration: {config_name}")
    
    logger.info(f"Starting WebSocket stress test: {config_name}")
    
    test = WebSocketStressTest(config)
    metrics = await test.run_stress_test()
    
    # Log results
    logger.info(f"WebSocket stress test results for {config_name}:")
    logger.info(f"  Duration: {metrics.duration_seconds:.2f}s")
    logger.info(f"  Connections: {metrics.successful_connections}/{metrics.total_connections} successful")
    logger.info(f"  Messages: {metrics.total_messages_sent} sent, {metrics.total_messages_received} received")
    logger.info(f"  Dropped: {metrics.messages_dropped}")
    logger.info(f"  Reconnects: {metrics.reconnect_count}")
    logger.info(f"  Latency P50: {metrics.send_latency_p50:.2f}μs")
    logger.info(f"  Latency P95: {metrics.send_latency_p95:.2f}μs")
    logger.info(f"  Latency P99: {metrics.send_latency_p99:.2f}μs")
    
    return metrics


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 2:
        print("Usage: python websocket_stress_test.py <config_name>")
        print("Available configs:", list(STRESS_TEST_CONFIGS.keys()))
        sys.exit(1)
    
    config_name = sys.argv[1]
    
    # Run the stress test
    asyncio.run(run_websocket_stress_test(config_name))
