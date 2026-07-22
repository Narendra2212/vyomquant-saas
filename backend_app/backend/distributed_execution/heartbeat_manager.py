"""
Heartbeat Manager

This module implements the heartbeat management system for Phase 3 distributed orchestration.
The heartbeat manager provides distributed health monitoring, failure detection, and recovery
coordination while preserving deterministic guarantees.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from backend_app.core.cache.redis_manager import redis_manager

from .orchestration_safety_guarantees import (DeterministicAssignmentGuarantee,
                                              OperationIsolationGuarantee,
                                              SplitBrainPreventionGuarantee)

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """Health status levels."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class ComponentType(Enum):
    """Component types for heartbeat."""
    WORKER = "worker"
    COORDINATOR = "coordinator"
    QUEUE_MANAGER = "queue_manager"
    LEASE_MANAGER = "lease_manager"
    ORPHAN_RECOVERER = "orphan_recoverer"
    SYSTEM = "system"


@dataclass
class HealthMetrics:
    """Health metrics for components."""
    cpu_utilization: float = 0.0
    memory_utilization: float = 0.0
    disk_utilization: float = 0.0
    network_io: float = 0.0
    response_time_ms: float = 0.0
    error_rate: float = 0.0
    throughput: float = 0.0
    active_connections: int = 0
    queue_depth: int = 0
    processing_rate: float = 0.0


@dataclass
class HeartbeatMessage:
    """Heartbeat message structure."""
    heartbeat_id: str
    source_id: str
    source_type: ComponentType
    timestamp: datetime
    sequence: int
    status: HealthStatus
    health_score: float
    metrics: HealthMetrics
    capabilities: Dict[str, Any] = field(default_factory=dict)
    load: Dict[str, Any] = field(default_factory=dict)
    health_indicators: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    final: bool = False


@dataclass
class AggregatedHeartbeat:
    """Aggregated heartbeat for multiple components."""
    aggregation_id: str
    aggregation_level: str
    timestamp: datetime
    component_count: int
    healthy_count: int
    degraded_count: int
    unhealthy_count: int
    critical_count: int
    overall_health_score: float
    aggregated_metrics: Dict[str, Any]
    health_distribution: Dict[str, int]
    alerts: List[Dict[str, Any]] = field(default_factory=list)


class HeartbeatManager:
    """
    Heartbeat Manager
    
    Manages distributed heartbeat generation, collection, analysis, and
    failure detection with deterministic guarantees.
    """
    
    def __init__(self):
        self.heartbeat_channel = "heartbeats"
        self.health_status_key_prefix = "health_status"
        self.heartbeat_history_key_prefix = "heartbeat_history"
        self.aggregated_heartbeat_key_prefix = "aggregated_heartbeat"
        
        # Safety guarantees
        self.deterministic_guarantee = DeterministicAssignmentGuarantee()
        self.isolation_guarantee = OperationIsolationGuarantee()
        self.split_brain_guarantee = SplitBrainPreventionGuarantee()
        
        # Heartbeat state
        self.component_registry: Dict[str, Dict[str, Any]] = {}
        self.last_heartbeats: Dict[str, datetime] = {}
        self.health_status: Dict[str, Dict[str, Any]] = {}
        self.heartbeat_generators: Dict[str, Any] = {}
        
        # Configuration
        self.default_interval = 30  # seconds
        self.timeout_multiplier = 3  # 3x interval for timeout
        self.aggregation_interval = 60  # seconds
        self.history_retention = timedelta(days=7)
        self.health_check_interval = 30  # seconds
        
        # Background tasks
        self.collection_task: Optional[asyncio.Task] = None
        self.aggregation_task: Optional[asyncio.Task] = None
        self.health_check_task: Optional[asyncio.Task] = None
        self.running = False
    
    async def initialize(self) -> bool:
        """Initialize the heartbeat manager."""
        try:
            logger.info("Initializing heartbeat manager")
            
            # Load existing component registry
            await self._load_component_registry()
            
            # Start background tasks
            self.running = True
            self.collection_task = asyncio.create_task(self._heartbeat_collection_loop())
            self.aggregation_task = asyncio.create_task(self._heartbeat_aggregation_loop())
            self.health_check_task = asyncio.create_task(self._health_check_loop())
            
            logger.info("Heartbeat manager initialized")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize heartbeat manager: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop the heartbeat manager."""
        try:
            logger.info("Stopping heartbeat manager")
            
            self.running = False
            
            # Cancel background tasks
            tasks = [
                self.collection_task,
                self.aggregation_task,
                self.health_check_task
            ]
            
            for task in tasks:
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            # Stop all heartbeat generators
            for generator_id, generator in self.heartbeat_generators.items():
                if hasattr(generator, 'stop'):
                    await generator.stop()
            
            # Persist registry state
            await self._persist_registry_state()
            
            logger.info("Heartbeat manager stopped")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop heartbeat manager: {e}")
            return False
    
    async def register_component(
        self,
        component_id: str,
        component_type: ComponentType,
        interval_seconds: int = None,
        capabilities: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Register component for heartbeat monitoring."""
        try:
            component_info = {
                "component_id": component_id,
                "component_type": component_type.value,
                "last_heartbeat": datetime.now(timezone.utc),
                "last_sequence": 0,
                "current_status": HealthStatus.UNKNOWN.value,
                "health_score": 0.0,
                "capabilities": capabilities or {},
                "metadata": metadata or {},
                "interval_seconds": interval_seconds or self.default_interval,
                "timeout_seconds": (interval_seconds or self.default_interval) * self.timeout_multiplier
            }
            
            self.component_registry[component_id] = component_info
            
            # Store in Redis
            registry_key = f"{self.health_status_key_prefix}:{component_id}"
            await redis_manager.hset(registry_key, mapping=component_info)
            await redis_manager.expire(registry_key, component_info["timeout_seconds"] + 60)
            
            logger.info(f"Component registered for heartbeat: {component_id} ({component_type.value})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to register component {component_id}: {e}")
            return False
    
    async def unregister_component(self, component_id: str) -> bool:
        """Unregister component from heartbeat monitoring."""
        try:
            # Remove from registry
            self.component_registry.pop(component_id, None)
            self.last_heartbeats.pop(component_id, None)
            self.health_status.pop(component_id, None)
            
            # Stop heartbeat generator if exists
            if component_id in self.heartbeat_generators:
                generator = self.heartbeat_generators[component_id]
                if hasattr(generator, 'stop'):
                    await generator.stop()
                self.heartbeat_generators.pop(component_id, None)
            
            # Remove from Redis
            registry_key = f"{self.health_status_key_prefix}:{component_id}"
            await redis_manager.delete(registry_key)
            
            logger.info(f"Component unregistered: {component_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to unregister component {component_id}: {e}")
            return False
    
    async def start_heartbeat_generator(
        self,
        component_id: str,
        component_type: ComponentType,
        metrics_collector: Optional[Any] = None,
        interval_seconds: int = None
    ) -> bool:
        """Start heartbeat generator for component."""
        try:
            if component_id in self.heartbeat_generators:
                logger.warning(f"Heartbeat generator already exists for: {component_id}")
                return False
            
            # Create heartbeat generator
            generator = HeartbeatGenerator(
                component_id,
                component_type.value,
                interval_seconds or self.default_interval,
                metrics_collector
            )
            
            # Start generator
            await generator.start()
            
            self.heartbeat_generators[component_id] = generator
            
            logger.info(f"Heartbeat generator started: {component_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start heartbeat generator for {component_id}: {e}")
            return False
    
    async def process_heartbeat(self, heartbeat_data: Dict[str, Any]) -> bool:
        """Process incoming heartbeat message."""
        try:
            # Parse heartbeat
            heartbeat = self._parse_heartbeat(heartbeat_data)
            
            # Validate heartbeat
            if not await self._validate_heartbeat(heartbeat):
                logger.warning(f"Invalid heartbeat from {heartbeat.source_id}")
                return False
            
            # Update component registry
            await self._update_component_registry(heartbeat)
            
            # Store heartbeat
            await self._store_heartbeat(heartbeat)
            
            # Update health status
            await self._update_health_status(heartbeat)
            
            # Check for alerts
            await self._check_alert_conditions(heartbeat)
            
            logger.debug(f"Processed heartbeat from {heartbeat.source_id}")
            return True
            
        except Exception as e:
            logger.error(f"Heartbeat processing failed: {e}")
            return False
    
    async def get_component_health(self, component_id: str) -> Optional[Dict[str, Any]]:
        """Get component health status."""
        try:
            if component_id in self.health_status:
                return self.health_status[component_id]
            
            # Load from Redis
            health_key = f"{self.health_status_key_prefix}:{component_id}"
            health_data = await redis_manager.hgetall(health_key)
            
            if health_data:
                self.health_status[component_id] = health_data
                return health_data
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get component health {component_id}: {e}")
            return None
    
    async def get_all_component_health(self) -> Dict[str, Dict[str, Any]]:
        """Get health status of all components."""
        try:
            return self.health_status.copy()
            
        except Exception as e:
            logger.error(f"Failed to get all component health: {e}")
            return {}
    
    async def get_aggregated_health(self, aggregation_level: str = "system") -> Optional[AggregatedHeartbeat]:
        """Get aggregated health status."""
        try:
            aggregation_key = f"{self.aggregated_heartbeat_key_prefix}:{aggregation_level}"
            aggregation_data = await redis_manager.get(aggregation_key)
            
            if aggregation_data:
                return self._parse_aggregated_heartbeat(json.loads(aggregation_data))
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get aggregated health: {e}")
            return None
    
    # Private methods
    
    def _parse_heartbeat(self, heartbeat_data: Dict[str, Any]) -> HeartbeatMessage:
        """Parse heartbeat message from data."""
        try:
            metrics_data = heartbeat_data.get("metrics", {})
            metrics = HealthMetrics(
                cpu_utilization=metrics_data.get("cpu_utilization", 0.0),
                memory_utilization=metrics_data.get("memory_utilization", 0.0),
                disk_utilization=metrics_data.get("disk_utilization", 0.0),
                network_io=metrics_data.get("network_io", 0.0),
                response_time_ms=metrics_data.get("response_time_p95_ms", 0.0),
                error_rate=metrics_data.get("error_rate", 0.0),
                throughput=metrics_data.get("processing_rate", 0.0),
                active_connections=metrics_data.get("active_connections", 0),
                queue_depth=metrics_data.get("queue_depth", 0),
                processing_rate=metrics_data.get("processing_rate", 0.0)
            )
            
            return HeartbeatMessage(
                heartbeat_id=heartbeat_data["heartbeat_id"],
                source_id=heartbeat_data["source_id"],
                source_type=ComponentType(heartbeat_data["source_type"]),
                timestamp=datetime.fromisoformat(heartbeat_data["timestamp"]),
                sequence=heartbeat_data["sequence"],
                status=HealthStatus(heartbeat_data["status"]),
                health_score=heartbeat_data["health_score"],
                metrics=metrics,
                capabilities=heartbeat_data.get("capabilities", {}),
                load=heartbeat_data.get("load", {}),
                health_indicators=heartbeat_data.get("health_indicators", {}),
                metadata=heartbeat_data.get("metadata", {}),
                final=heartbeat_data.get("final", False)
            )
            
        except Exception as e:
            logger.error(f"Failed to parse heartbeat: {e}")
            raise
    
    def _parse_aggregated_heartbeat(self, aggregation_data: Dict[str, Any]) -> AggregatedHeartbeat:
        """Parse aggregated heartbeat from data."""
        try:
            return AggregatedHeartbeat(
                aggregation_id=aggregation_data["aggregation_id"],
                aggregation_level=aggregation_data["aggregation_level"],
                timestamp=datetime.fromisoformat(aggregation_data["timestamp"]),
                component_count=aggregation_data["component_count"],
                healthy_count=aggregation_data["healthy_count"],
                degraded_count=aggregation_data["degraded_count"],
                unhealthy_count=aggregation_data["unhealthy_count"],
                critical_count=aggregation_data["critical_count"],
                overall_health_score=aggregation_data["overall_health_score"],
                aggregated_metrics=aggregation_data["aggregated_metrics"],
                health_distribution=aggregation_data["health_distribution"],
                alerts=aggregation_data.get("alerts", [])
            )
            
        except Exception as e:
            logger.error(f"Failed to parse aggregated heartbeat: {e}")
            raise
    
    async def _validate_heartbeat(self, heartbeat: HeartbeatMessage) -> bool:
        """Validate heartbeat message."""
        try:
            # Check required fields
            if not heartbeat.heartbeat_id or not heartbeat.source_id:
                return False
            
            # Check timestamp
            now = datetime.now(timezone.utc)
            heartbeat_time = heartbeat.timestamp
            
            # Reject heartbeats too far in future or past
            if abs((now - heartbeat_time).total_seconds()) > 300:  # 5 minutes
                return False
            
            # Check sequence (must be increasing)
            last_sequence = self.last_heartbeats.get(heartbeat.source_id, 0)
            if heartbeat.sequence <= last_sequence:
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Heartbeat validation failed: {e}")
            return False
    
    async def _update_component_registry(self, heartbeat: HeartbeatMessage):
        """Update component registry with heartbeat info."""
        try:
            component_info = {
                "component_id": heartbeat.source_id,
                "component_type": heartbeat.source_type.value,
                "last_heartbeat": heartbeat.timestamp,
                "last_sequence": heartbeat.sequence,
                "current_status": heartbeat.status.value,
                "health_score": heartbeat.health_score,
                "capabilities": heartbeat.capabilities,
                "metadata": heartbeat.metadata
            }
            
            self.component_registry[heartbeat.source_id] = component_info
            self.last_heartbeats[heartbeat.source_id] = heartbeat.sequence
            
            # Store in Redis
            registry_key = f"{self.health_status_key_prefix}:{heartbeat.source_id}"
            await redis_manager.hset(registry_key, mapping=component_info)
            
            # Set expiration based on component timeout
            if heartbeat.source_id in self.component_registry:
                timeout = self.component_registry[heartbeat.source_id]["timeout_seconds"]
                await redis_manager.expire(registry_key, timeout + 60)
            
        except Exception as e:
            logger.error(f"Component registry update failed: {e}")
    
    async def _store_heartbeat(self, heartbeat: HeartbeatMessage):
        """Store heartbeat in storage."""
        try:
            # Store latest heartbeat
            latest_key = f"latest_heartbeat:{heartbeat.source_id}"
            heartbeat_data = self._serialize_heartbeat(heartbeat)
            
            await redis_manager.set(latest_key, json.dumps(heartbeat_data), ex=300)
            
            # Store in history
            history_key = f"{self.heartbeat_history_key_prefix}:{heartbeat.source_id}:{heartbeat.sequence}"
            await redis_manager.setex(
                history_key,
                int(self.history_retention.total_seconds()),
                json.dumps(heartbeat_data)
            )
            
        except Exception as e:
            logger.error(f"Heartbeat storage failed: {e}")
    
    async def _update_health_status(self, heartbeat: HeartbeatMessage):
        """Update component health status."""
        try:
            # Calculate health trend
            previous_status = self.health_status.get(heartbeat.source_id, {}).get("status")
            status_changed = previous_status != heartbeat.status.value
            
            # Update health status
            health_info = {
                "status": heartbeat.status.value,
                "health_score": heartbeat.health_score,
                "last_updated": heartbeat.timestamp,
                "status_changed": status_changed,
                "previous_status": previous_status,
                "metrics": self._serialize_metrics(heartbeat.metrics),
                "indicators": heartbeat.health_indicators
            }
            
            self.health_status[heartbeat.source_id] = health_info
            
            # Store in Redis
            health_key = f"{self.health_status_key_prefix}:{heartbeat.source_id}"
            await redis_manager.hset(health_key, mapping=health_info)
            
            # Notify status change
            if status_changed:
                await self._notify_status_change(heartbeat.source_id, previous_status, heartbeat.status.value)
            
        except Exception as e:
            logger.error(f"Health status update failed: {e}")
    
    async def _check_alert_conditions(self, heartbeat: HeartbeatMessage):
        """Check heartbeat for alert conditions."""
        try:
            alerts = []
            
            # Check health score
            if heartbeat.health_score < 0.5:
                alerts.append({
                    "component_id": heartbeat.source_id,
                    "severity": "critical",
                    "type": "health_score_low",
                    "message": f"Health score {heartbeat.health_score:.2f} below threshold",
                    "value": heartbeat.health_score
                })
            elif heartbeat.health_score < 0.7:
                alerts.append({
                    "component_id": heartbeat.source_id,
                    "severity": "warning",
                    "type": "health_score_degraded",
                    "message": f"Health score {heartbeat.health_score:.2f} degraded",
                    "value": heartbeat.health_score
                })
            
            # Check resource utilization
            cpu_util = heartbeat.metrics.cpu_utilization
            if cpu_util > 0.9:
                alerts.append({
                    "component_id": heartbeat.source_id,
                    "severity": "critical",
                    "type": "cpu_high",
                    "message": f"CPU utilization {cpu_util:.1%} critical",
                    "value": cpu_util
                })
            elif cpu_util > 0.8:
                alerts.append({
                    "component_id": heartbeat.source_id,
                    "severity": "warning",
                    "type": "cpu_elevated",
                    "message": f"CPU utilization {cpu_util:.1%} elevated",
                    "value": cpu_util
                })
            
            # Check memory utilization
            mem_util = heartbeat.metrics.memory_utilization
            if mem_util > 0.9:
                alerts.append({
                    "component_id": heartbeat.source_id,
                    "severity": "critical",
                    "type": "memory_high",
                    "message": f"Memory utilization {mem_util:.1%} critical",
                    "value": mem_util
                })
            
            # Check error rate
            error_rate = heartbeat.metrics.error_rate
            if error_rate > 0.05:
                alerts.append({
                    "component_id": heartbeat.source_id,
                    "severity": "critical",
                    "type": "error_rate_high",
                    "message": f"Error rate {error_rate:.2%} critical",
                    "value": error_rate
                })
            
            # Send alerts
            for alert in alerts:
                await self._send_alert(alert)
            
        except Exception as e:
            logger.error(f"Alert condition check failed: {e}")
    
    def _serialize_heartbeat(self, heartbeat: HeartbeatMessage) -> Dict[str, Any]:
        """Serialize heartbeat to dictionary."""
        return {
            "heartbeat_id": heartbeat.heartbeat_id,
            "source_id": heartbeat.source_id,
            "source_type": heartbeat.source_type.value,
            "timestamp": heartbeat.timestamp.isoformat(),
            "sequence": heartbeat.sequence,
            "status": heartbeat.status.value,
            "health_score": heartbeat.health_score,
            "metrics": self._serialize_metrics(heartbeat.metrics),
            "capabilities": heartbeat.capabilities,
            "load": heartbeat.load,
            "health_indicators": heartbeat.health_indicators,
            "metadata": heartbeat.metadata,
            "final": heartbeat.final
        }
    
    def _serialize_metrics(self, metrics: HealthMetrics) -> Dict[str, Any]:
        """Serialize metrics to dictionary."""
        return {
            "cpu_utilization": metrics.cpu_utilization,
            "memory_utilization": metrics.memory_utilization,
            "disk_utilization": metrics.disk_utilization,
            "network_io": metrics.network_io,
            "response_time_p95_ms": metrics.response_time_ms,
            "error_rate": metrics.error_rate,
            "throughput": metrics.throughput,
            "active_connections": metrics.active_connections,
            "queue_depth": metrics.queue_depth,
            "processing_rate": metrics.processing_rate
        }
    
    async def _notify_status_change(self, component_id: str, previous_status: str, new_status: str):
        """Notify component status change."""
        try:
            # Log status change
            logger.info(f"Component status changed: {component_id} {previous_status} → {new_status}")
            
            # Store status change event
            status_change_key = f"status_change:{component_id}:{int(time.time())}"
            status_change_data = {
                "component_id": component_id,
                "previous_status": previous_status,
                "new_status": new_status,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            await redis_manager.setex(
                status_change_key,
                86400,  # 24 hours
                json.dumps(status_change_data)
            )
            
        except Exception as e:
            logger.error(f"Status change notification failed: {e}")
    
    async def _send_alert(self, alert: Dict[str, Any]):
        """Send alert notification."""
        try:
            # Store alert
            alert_key = f"alert:{alert['component_id']}:{int(time.time())}"
            await redis_manager.setex(
                alert_key,
                3600,  # 1 hour
                json.dumps(alert)
            )
            
            # Log alert
            logger.warning(f"Alert: {alert['type']} for {alert['component_id']} - {alert['message']}")
            
            # Could integrate with external alerting systems here
            
        except Exception as e:
            logger.error(f"Alert sending failed: {e}")
    
    async def _load_component_registry(self):
        """Load existing component registry from Redis."""
        try:
            # Get all health status keys
            health_keys = await redis_manager.keys(f"{self.health_status_key_prefix}:*")
            
            for health_key in health_keys:
                health_data = await redis_manager.hgetall(health_key)
                if health_data:
                    component_id = health_data.get("component_id")
                    if component_id:
                        self.component_registry[component_id] = health_data
                        self.last_heartbeats[component_id] = int(health_data.get("last_sequence", 0))
                        self.health_status[component_id] = health_data
            
            logger.info(f"Loaded {len(self.component_registry)} components from registry")
            
        except Exception as e:
            logger.error(f"Failed to load component registry: {e}")
    
    async def _persist_registry_state(self):
        """Persist registry state."""
        try:
            # Components are already persisted individually
            # This could be extended to persist registry metadata
            registry_metadata = {
                "last_persisted": datetime.now(timezone.utc).isoformat(),
                "total_components": len(self.component_registry),
                "active_generators": len(self.heartbeat_generators)
            }
            
            await redis_manager.set("heartbeat_manager_metadata", json.dumps(registry_metadata))
            
        except Exception as e:
            logger.error(f"Failed to persist registry state: {e}")
    
    async def _heartbeat_collection_loop(self):
        """Background heartbeat collection loop."""
        while self.running:
            try:
                # Subscribe to Redis heartbeat channel
                pubsub = redis_manager.pubsub()
                await pubsub.subscribe(self.heartbeat_channel)
                
                async for message in pubsub.listen():
                    if not self.running:
                        break
                    
                    if message["type"] == "message":
                        try:
                            heartbeat_data = json.loads(message["data"])
                            await self.process_heartbeat(heartbeat_data)
                        except Exception as e:
                            logger.error(f"Heartbeat processing error: {e}")
                
            except Exception as e:
                logger.error(f"Heartbeat collection loop error: {e}")
                await asyncio.sleep(5)
    
    async def _heartbeat_aggregation_loop(self):
        """Background heartbeat aggregation loop."""
        while self.running:
            try:
                await self._aggregate_heartbeats()
                await asyncio.sleep(self.aggregation_interval)
                
            except Exception as e:
                logger.error(f"Heartbeat aggregation loop error: {e}")
                await asyncio.sleep(10)
    
    async def _health_check_loop(self):
        """Background health check loop."""
        while self.running:
            try:
                await self._perform_health_checks()
                await asyncio.sleep(self.health_check_interval)
                
            except Exception as e:
                logger.error(f"Health check loop error: {e}")
                await asyncio.sleep(10)
    
    async def _aggregate_heartbeats(self):
        """Aggregate heartbeat data."""
        try:
            current_time = datetime.now(timezone.utc)
            
            # Count components by health status
            health_counts = {
                "healthy": 0,
                "degraded": 0,
                "unhealthy": 0,
                "critical": 0,
                "unknown": 0
            }
            
            total_metrics = {
                "total_cpu_utilization": 0.0,
                "total_memory_utilization": 0.0,
                "total_active_connections": 0,
                "total_queue_depth": 0,
                "total_processing_rate": 0.0,
                "total_error_rate": 0.0
            }
            
            component_count = len(self.health_status)
            
            for component_id, health_info in self.health_status.items():
                status = health_info.get("status", "unknown")
                health_info.get("health_score", 0.0)
                
                if status in health_counts:
                    health_counts[status] += 1
                
                # Aggregate metrics
                metrics = health_info.get("metrics", {})
                if isinstance(metrics, dict):
                    total_metrics["total_cpu_utilization"] += float(metrics.get("cpu_utilization", 0))
                    total_metrics["total_memory_utilization"] += float(metrics.get("memory_utilization", 0))
                    total_metrics["total_active_connections"] += int(metrics.get("active_connections", 0))
                    total_metrics["total_queue_depth"] += int(metrics.get("queue_depth", 0))
                    total_metrics["total_processing_rate"] += float(metrics.get("processing_rate", 0))
                    total_metrics["total_error_rate"] += float(metrics.get("error_rate", 0))
            
            # Calculate averages
            if component_count > 0:
                avg_metrics = {
                    "avg_cpu_utilization": total_metrics["total_cpu_utilization"] / component_count,
                    "avg_memory_utilization": total_metrics["total_memory_utilization"] / component_count,
                    "avg_active_connections": total_metrics["total_active_connections"] / component_count,
                    "avg_queue_depth": total_metrics["total_queue_depth"] / component_count,
                    "avg_processing_rate": total_metrics["total_processing_rate"] / component_count,
                    "avg_error_rate": total_metrics["total_error_rate"] / component_count
                }
            else:
                avg_metrics = {}
            
            # Calculate overall health score
            overall_health_score = 0.0
            if component_count > 0:
                overall_health_score = (
                    (health_counts["healthy"] * 1.0 +
                    health_counts["degraded"] * 0.7 +
                    health_counts["unhealthy"] * 0.3 +
                    health_counts["critical"] * 0.1)
                ) / component_count
            
            # Create aggregated heartbeat
            aggregated_heartbeat = AggregatedHeartbeat(
                aggregation_id=f"agg_{int(time.time())}",
                aggregation_level="system",
                timestamp=current_time,
                component_count=component_count,
                healthy_count=health_counts["healthy"],
                degraded_count=health_counts["degraded"],
                unhealthy_count=health_counts["unhealthy"],
                critical_count=health_counts["critical"],
                overall_health_score=overall_health_score,
                aggregated_metrics={**avg_metrics, **total_metrics},
                health_distribution=health_counts
            )
            
            # Store aggregated heartbeat
            aggregation_key = f"{self.aggregated_heartbeat_key_prefix}:system"
            await redis_manager.setex(
                aggregation_key,
                3600,  # 1 hour
                json.dumps(self._serialize_aggregated_heartbeat(aggregated_heartbeat))
            )
            
        except Exception as e:
            logger.error(f"Heartbeat aggregation failed: {e}")
    
    def _serialize_aggregated_heartbeat(self, aggregated: AggregatedHeartbeat) -> Dict[str, Any]:
        """Serialize aggregated heartbeat."""
        return {
            "aggregation_id": aggregated.aggregation_id,
            "aggregation_level": aggregated.aggregation_level,
            "timestamp": aggregated.timestamp.isoformat(),
            "component_count": aggregated.component_count,
            "healthy_count": aggregated.healthy_count,
            "degraded_count": aggregated.degraded_count,
            "unhealthy_count": aggregated.unhealthy_count,
            "critical_count": aggregated.critical_count,
            "overall_health_score": aggregated.overall_health_score,
            "aggregated_metrics": aggregated.aggregated_metrics,
            "health_distribution": aggregated.health_distribution,
            "alerts": aggregated.alerts
        }
    
    async def _perform_health_checks(self):
        """Perform health checks on all components."""
        try:
            current_time = datetime.now(timezone.utc)
            
            for component_id, component_info in self.component_registry.items():
                # Check heartbeat timeout
                last_heartbeat = component_info.get("last_heartbeat")
                if last_heartbeat:
                    if isinstance(last_heartbeat, str):
                        last_heartbeat = datetime.fromisoformat(last_heartbeat)
                    
                    timeout_seconds = component_info.get("timeout_seconds", 90)
                    time_since_heartbeat = (current_time - last_heartbeat).total_seconds()
                    
                    if time_since_heartbeat > timeout_seconds:
                        logger.warning(f"Component heartbeat timeout: {component_id}")
                        
                        # Update health status to unhealthy
                        if component_id in self.health_status:
                            self.health_status[component_id]["status"] = HealthStatus.UNHEALTHY.value
                            self.health_status[component_id]["last_updated"] = current_time.isoformat()
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")


class HeartbeatGenerator:
    """Generates heartbeats for a component."""
    
    def __init__(
        self,
        component_id: str,
        component_type: str,
        interval_seconds: int = 30,
        metrics_collector: Optional[Any] = None
    ):
        self.component_id = component_id
        self.component_type = component_type
        self.interval = interval_seconds
        self.metrics_collector = metrics_collector
        
        self.running = False
        self.sequence = 0
        self.last_heartbeat = None
        
        # Heartbeat configuration
        self.compression_enabled = True
        self.signature_enabled = True
    
    async def start(self):
        """Start heartbeat generation."""
        logger.info(f"Starting heartbeat generator for {self.component_id}")
        self.running = True
        
        # Start heartbeat loop
        asyncio.create_task(self._heartbeat_loop())
        
        # Send initial heartbeat
        await self._send_heartbeat()
    
    async def stop(self):
        """Stop heartbeat generation."""
        logger.info(f"Stopping heartbeat generator for {self.component_id}")
        self.running = False
        
        # Send final heartbeat
        await self._send_heartbeat(final=True)
    
    async def _heartbeat_loop(self):
        """Main heartbeat generation loop."""
        while self.running:
            try:
                await self._send_heartbeat()
                await asyncio.sleep(self.interval)
                
            except Exception as e:
                logger.error(f"Heartbeat generation error: {e}")
                await asyncio.sleep(5)
    
    async def _send_heartbeat(self, final: bool = False):
        """Generate and send heartbeat."""
        try:
            self.sequence += 1
            
            # Collect metrics
            metrics = await self._collect_metrics()
            
            # Assess health
            health_assessment = await self._assess_health(metrics)
            
            # Create heartbeat
            heartbeat = HeartbeatMessage(
                heartbeat_id=f"hb_{self.component_id}_{self.sequence}",
                source_id=self.component_id,
                source_type=ComponentType(self.component_type),
                timestamp=datetime.now(timezone.utc),
                sequence=self.sequence,
                status=health_assessment.status,
                health_score=health_assessment.score,
                metrics=metrics,
                capabilities=await self._get_capabilities(),
                load=await self._get_load_info(),
                health_indicators=health_assessment.indicators,
                metadata=await self._get_metadata(),
                final=final
            )
            
            # Validate heartbeat
            if await self._validate_heartbeat(heartbeat):
                # Send heartbeat
                await self._publish_heartbeat(heartbeat)
                self.last_heartbeat = heartbeat
                
                logger.debug(f"Sent heartbeat {heartbeat.heartbeat_id}")
            else:
                logger.warning("Heartbeat validation failed")
                
        except Exception as e:
            logger.error(f"Heartbeat send failed: {e}")
    
    async def _collect_metrics(self) -> HealthMetrics:
        """Collect component metrics."""
        try:
            if self.metrics_collector:
                return await self.metrics_collector.collect_metrics()
            else:
                # Default metrics
                return HealthMetrics()
                
        except Exception as e:
            logger.error(f"Metrics collection failed: {e}")
            return HealthMetrics()
    
    async def _assess_health(self, metrics: HealthMetrics) -> Any:
        """Assess component health."""
        try:
            # Resource health
            cpu_health = 1.0 - min(metrics.cpu_utilization * 2, 1.0)
            memory_health = 1.0 - min(metrics.memory_utilization * 2, 1.0)
            
            # Performance health
            performance_health = 1.0 - min(metrics.error_rate * 10, 1.0)
            
            # Overall health score
            health_score = (cpu_health * 0.3 + memory_health * 0.3 + performance_health * 0.4)
            
            # Determine status
            if health_score >= 0.9:
                status = HealthStatus.HEALTHY
            elif health_score >= 0.7:
                status = HealthStatus.DEGRADED
            elif health_score >= 0.5:
                status = HealthStatus.UNHEALTHY
            else:
                status = HealthStatus.CRITICAL
            
            # Health indicators
            indicators = {
                "cpu": "optimal" if cpu_health > 0.8 else "degraded" if cpu_health > 0.6 else "critical",
                "memory": "optimal" if memory_health > 0.8 else "degraded" if memory_health > 0.6 else "critical",
                "performance": "optimal" if performance_health > 0.8 else "degraded" if performance_health > 0.6 else "critical"
            }
            
            return type('HealthAssessment', (), {
                'status': status,
                'score': health_score,
                'indicators': indicators
            })()
            
        except Exception as e:
            logger.error(f"Health assessment failed: {e}")
            return type('HealthAssessment', (), {
                'status': HealthStatus.UNKNOWN,
                'score': 0.0,
                'indicators': {}
            })()
    
    async def _get_capabilities(self) -> Dict[str, Any]:
        """Get component capabilities."""
        try:
            # This would be implemented based on component type
            return {}
            
        except Exception as e:
            logger.error(f"Capabilities collection failed: {e}")
            return {}
    
    async def _get_load_info(self) -> Dict[str, Any]:
        """Get component load information."""
        try:
            # This would be implemented based on component type
            return {}
            
        except Exception as e:
            logger.error(f"Load info collection failed: {e}")
            return {}
    
    async def _get_metadata(self) -> Dict[str, Any]:
        """Get component metadata."""
        try:
            return {
                "version": "1.0.0",
                "uptime_seconds": int(time.time()),
                "generator_type": "heartbeat_generator"
            }
            
        except Exception as e:
            logger.error(f"Metadata collection failed: {e}")
            return {}
    
    async def _validate_heartbeat(self, heartbeat: HeartbeatMessage) -> bool:
        """Validate heartbeat before sending."""
        try:
            # Basic validation
            if not heartbeat.heartbeat_id or not heartbeat.source_id:
                return False
            
            # Timestamp validation
            now = datetime.now(timezone.utc)
            if abs((now - heartbeat.timestamp).total_seconds()) > 60:
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Heartbeat validation failed: {e}")
            return False
    
    async def _publish_heartbeat(self, heartbeat: HeartbeatMessage):
        """Publish heartbeat to Redis channel."""
        try:
            heartbeat_data = {
                "heartbeat_id": heartbeat.heartbeat_id,
                "source_id": heartbeat.source_id,
                "source_type": heartbeat.source_type.value,
                "timestamp": heartbeat.timestamp.isoformat(),
                "sequence": heartbeat.sequence,
                "status": heartbeat.status.value,
                "health_score": heartbeat.health_score,
                "metrics": {
                    "cpu_utilization": heartbeat.metrics.cpu_utilization,
                    "memory_utilization": heartbeat.metrics.memory_utilization,
                    "disk_utilization": heartbeat.metrics.disk_utilization,
                    "network_io": heartbeat.metrics.network_io,
                    "response_time_p95_ms": heartbeat.metrics.response_time_ms,
                    "error_rate": heartbeat.metrics.error_rate,
                    "throughput": heartbeat.metrics.throughput,
                    "active_connections": heartbeat.metrics.active_connections,
                    "queue_depth": heartbeat.metrics.queue_depth,
                    "processing_rate": heartbeat.metrics.processing_rate
                },
                "capabilities": heartbeat.capabilities,
                "load": heartbeat.load,
                "health_indicators": heartbeat.health_indicators,
                "metadata": heartbeat.metadata,
                "final": heartbeat.final
            }
            
            await redis_manager.publish("heartbeats", json.dumps(heartbeat_data))
            
        except Exception as e:
            logger.error(f"Heartbeat publish failed: {e}")


# Global heartbeat manager instance
heartbeat_manager = HeartbeatManager()
