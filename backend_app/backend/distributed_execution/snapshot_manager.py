"""
Snapshot Manager for State Image Management

Provides efficient state image management for fast recovery, debugging,
and audit capabilities. Supports full, incremental, and component snapshots
with deterministic creation and restoration.

Author: Principal Replay and Recovery Engineer
"""

import gzip
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from .immutable_journal import ExecutionEvent, immutable_journal
from .sequence_manager import sequence_manager

logger = logging.getLogger("snapshot_manager")


@dataclass
class SnapshotRequest:
    """Snapshot creation request."""
    snapshot_id: str
    tenant_id: str
    strategy_id: Optional[str] = None
    bot_id: Optional[str] = None
    snapshot_type: 'SnapshotType' = None
    component_filter: Optional[List[str]] = None
    include_metadata: bool = True
    compress_data: bool = True
    validate_integrity: bool = True


@dataclass
class SnapshotResult:
    """Result of snapshot operation."""
    snapshot_id: str
    success: bool
    snapshot_type: 'SnapshotType'
    created_at: datetime
    size_bytes: int
    compressed_size_bytes: int
    component_count: int
    event_count: int
    metadata: Dict[str, Any]
    error: Optional[str] = None


@dataclass
class SnapshotMetadata:
    """Snapshot metadata."""
    snapshot_id: str
    tenant_id: str
    strategy_id: Optional[str]
    bot_id: Optional[str]
    snapshot_type: 'SnapshotType'
    created_at: datetime
    created_by: str
    size_bytes: int
    compressed_size_bytes: int
    component_count: int
    event_count: int
    sequence_range: Tuple[int, int]
    time_range: Tuple[datetime, datetime]
    checksum: str
    version: int


@dataclass
class ComponentSnapshot:
    """Snapshot data for a specific component."""
    component_name: str
    component_type: str
    snapshot_data: Dict[str, Any]
    metadata: Dict[str, Any]
    created_at: datetime
    sequence_id: int


class SnapshotType(Enum):
    """Snapshot types."""
    FULL = "full"
    INCREMENTAL = "incremental"
    COMPONENT = "component"
    WORKER = "worker"
    QUEUE = "queue"
    WEBSOCKET = "websocket"
    JOB = "job"
    TELEMETRY = "telemetry"
    DAG = "dag"


class SnapshotManager:
    """Manages state snapshots for fast recovery and debugging."""
    
    def __init__(self):
        self.immutable_journal = immutable_journal
        self.sequence_manager = sequence_manager
        
        # Snapshot storage
        self.snapshot_storage: Dict[str, SnapshotMetadata] = {}
        self.snapshot_data: Dict[str, bytes] = {}
        
        # Snapshot configuration
        self.compression_level = 6
        self.max_snapshot_size = 100 * 1024 * 1024  # 100MB
        self.snapshot_ttl = 86400 * 30  # 30 days
        self.max_snapshots_per_tenant = 100
        
        # Performance optimization
        self.snapshot_cache: Dict[str, ComponentSnapshot] = {}
        self.metadata_cache: Dict[str, SnapshotMetadata] = {}
        
        logger.info("Snapshot manager initialized")
    
    async def initialize(self) -> bool:
        """Initialize snapshot manager components."""
        try:
            # Initialize dependencies
            await self.immutable_journal.initialize()
            await self.sequence_manager.initialize()
            
            logger.info("Snapshot manager initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize snapshot manager: {e}")
            return False
    
    async def create_snapshot(self, request: SnapshotRequest) -> SnapshotResult:
        """Create a snapshot of system state."""
        try:
            snapshot_start = time.time()
            
            # Validate snapshot request
            validation_result = await self._validate_snapshot_request(request)
            if not validation_result.success:
                return SnapshotResult(
                    snapshot_id=request.snapshot_id,
                    success=False,
                    snapshot_type=request.snapshot_type,
                    created_at=datetime.now(timezone.utc),
                    size_bytes=0,
                    compressed_size_bytes=0,
                    component_count=0,
                    event_count=0,
                    metadata={},
                    error=validation_result.error
                )
            
            # Collect snapshot data
            snapshot_data = await self._collect_snapshot_data(request)
            
            # Create snapshot metadata
            metadata = await self._create_snapshot_metadata(request, snapshot_data)
            
            # Compress snapshot data if requested
            compressed_data = snapshot_data
            if request.compress_data:
                compressed_data = await self._compress_snapshot_data(snapshot_data)
            
            # Validate integrity if requested
            if request.validate_integrity:
                integrity_result = await self._validate_snapshot_integrity(metadata, compressed_data)
                if not integrity_result.success:
                    return SnapshotResult(
                        snapshot_id=request.snapshot_id,
                        success=False,
                        snapshot_type=request.snapshot_type,
                        created_at=datetime.now(timezone.utc),
                        size_bytes=0,
                        compressed_size_bytes=0,
                        component_count=0,
                        event_count=0,
                        metadata={},
                        error=integrity_result.error
                    )
            
            # Store snapshot
            await self._store_snapshot(metadata, compressed_data)
            
            # Calculate statistics
            size_bytes = len(json.dumps(snapshot_data).encode('utf-8'))
            compressed_size_bytes = len(compressed_data)
            component_count = len(snapshot_data.get('components', {}))
            event_count = sum(comp.get('event_count', 0) for comp in snapshot_data.get('components', {}).values())
            
            # Create result
            result = SnapshotResult(
                snapshot_id=request.snapshot_id,
                success=True,
                snapshot_type=request.snapshot_type,
                created_at=metadata.created_at,
                size_bytes=size_bytes,
                compressed_size_bytes=compressed_size_bytes,
                component_count=component_count,
                event_count=event_count,
                metadata=asdict(metadata)
            )
            
            # Log completion
            duration = time.time() - snapshot_start
            logger.info(f"Snapshot {request.snapshot_id} created in {duration:.2f}s: {component_count} components, {event_count} events")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to create snapshot {request.snapshot_id}: {e}")
            return SnapshotResult(
                snapshot_id=request.snapshot_id,
                success=False,
                snapshot_type=request.snapshot_type,
                created_at=datetime.now(timezone.utc),
                size_bytes=0,
                compressed_size_bytes=0,
                component_count=0,
                event_count=0,
                metadata={},
                error=str(e)
            )
    
    async def restore_snapshot(self, snapshot_id: str, component_filter: Optional[List[str]] = None) -> Dict[str, Any]:
        """Restore state from snapshot."""
        try:
            # Get snapshot metadata
            metadata = self.snapshot_storage.get(snapshot_id)
            if not metadata:
                raise ValueError(f"Snapshot {snapshot_id} not found")
            
            # Get snapshot data
            compressed_data = self.snapshot_data.get(snapshot_id)
            if not compressed_data:
                raise ValueError(f"Snapshot data {snapshot_id} not found")
            
            # Decompress data if needed
            snapshot_data = compressed_data
            if metadata.compressed_size_bytes < metadata.size_bytes:
                snapshot_data = await self._decompress_snapshot_data(compressed_data)
            
            # Validate integrity
            integrity_result = await self._validate_snapshot_integrity(metadata, snapshot_data)
            if not integrity_result.success:
                raise ValueError(f"Snapshot integrity validation failed: {integrity_result.error}")
            
            # Filter components if requested
            if component_filter:
                if 'components' in snapshot_data:
                    snapshot_data['components'] = {
                        k: v for k, v in snapshot_data['components'].items()
                        if k in component_filter
                    }
            
            # Restore state
            restored_state = await self._restore_snapshot_state(snapshot_data)
            
            logger.info(f"Restored snapshot {snapshot_id}: {len(restored_state.get('components', {}))} components")
            
            return restored_state
            
        except Exception as e:
            logger.error(f"Failed to restore snapshot {snapshot_id}: {e}")
            raise
    
    async def list_snapshots(self, tenant_id: str, snapshot_type: Optional[SnapshotType] = None,
                           limit: int = 100) -> List[SnapshotMetadata]:
        """List available snapshots."""
        try:
            snapshots = []
            
            for snapshot_id, metadata in self.snapshot_storage.items():
                if metadata.tenant_id != tenant_id:
                    continue
                
                if snapshot_type and metadata.snapshot_type != snapshot_type:
                    continue
                
                snapshots.append(metadata)
            
            # Sort by creation time (newest first)
            snapshots.sort(key=lambda s: s.created_at, reverse=True)
            
            return snapshots[:limit] if len(snapshots) > limit else snapshots
            
        except Exception as e:
            logger.error(f"Failed to list snapshots: {e}")
            return []
    
    async def delete_snapshot(self, snapshot_id: str) -> bool:
        """Delete a snapshot."""
        try:
            if snapshot_id in self.snapshot_storage:
                del self.snapshot_storage[snapshot_id]
            
            if snapshot_id in self.snapshot_data:
                del self.snapshot_data[snapshot_id]
            
            # Clear caches
            if snapshot_id in self.metadata_cache:
                del self.metadata_cache[snapshot_id]
            
            logger.info(f"Deleted snapshot {snapshot_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete snapshot {snapshot_id}: {e}")
            return False
    
    async def _validate_snapshot_request(self, request: SnapshotRequest) -> 'ValidationResult':
        """Validate snapshot request."""
        try:
            # Validate tenant exists
            if not await self._validate_tenant_exists(request.tenant_id):
                return ValidationResult(
                    success=False,
                    error=f"Tenant {request.tenant_id} not found"
                )
            
            # Validate strategy if specified
            if request.strategy_id:
                if not await self._validate_strategy_exists(request.tenant_id, request.strategy_id):
                    return ValidationResult(
                        success=False,
                        error=f"Strategy {request.strategy_id} not found"
                    )
            
            # Validate bot if specified
            if request.bot_id:
                if not await self._validate_bot_exists(request.tenant_id, request.bot_id):
                    return ValidationResult(
                        success=False,
                        error=f"Bot {request.bot_id} not found"
                    )
            
            # Check snapshot limit
            tenant_snapshots = await self.list_snapshots(request.tenant_id)
            if len(tenant_snapshots) >= self.max_snapshots_per_tenant:
                return ValidationResult(
                    success=False,
                    error=f"Maximum snapshots per tenant exceeded ({self.max_snapshots_per_tenant})"
                )
            
            return ValidationResult(success=True)
            
        except Exception as e:
            logger.error(f"Snapshot request validation failed: {e}")
            return ValidationResult(
                success=False,
                error=str(e)
            )
    
    async def _collect_snapshot_data(self, request: SnapshotRequest) -> Dict[str, Any]:
        """Collect snapshot data from system components."""
        try:
            snapshot_data = {
                'snapshot_id': request.snapshot_id,
                'tenant_id': request.tenant_id,
                'strategy_id': request.strategy_id,
                'bot_id': request.bot_id,
                'snapshot_type': request.snapshot_type,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'components': {}
            }
            
            # Collect component data based on snapshot type
            if request.snapshot_type == SnapshotType.FULL:
                await self._collect_full_snapshot_data(snapshot_data, request)
            elif request.snapshot_type == SnapshotType.INCREMENTAL:
                await self._collect_incremental_snapshot_data(snapshot_data, request)
            elif request.snapshot_type == SnapshotType.COMPONENT:
                await self._collect_component_snapshot_data(snapshot_data, request)
            elif request.snapshot_type == SnapshotType.WORKER:
                await self._collect_worker_snapshot_data(snapshot_data, request)
            elif request.snapshot_type == SnapshotType.QUEUE:
                await self._collect_queue_snapshot_data(snapshot_data, request)
            elif request.snapshot_type == SnapshotType.WEBSOCKET:
                await self._collect_websocket_snapshot_data(snapshot_data, request)
            elif request.snapshot_type == SnapshotType.JOB:
                await self._collect_job_snapshot_data(snapshot_data, request)
            elif request.snapshot_type == SnapshotType.TELEMETRY:
                await self._collect_telemetry_snapshot_data(snapshot_data, request)
            elif request.snapshot_type == SnapshotType.DAG:
                await self._collect_dag_snapshot_data(snapshot_data, request)
            
            return snapshot_data
            
        except Exception as e:
            logger.error(f"Failed to collect snapshot data: {e}")
            raise
    
    async def _collect_full_snapshot_data(self, snapshot_data: Dict[str, Any], request: SnapshotRequest) -> None:
        """Collect full system snapshot data."""
        try:
            # Get current sequence
            current_sequence = await self.sequence_manager.get_current_sequence(request.tenant_id)
            
            # Collect all component states
            components = {}
            
            # Worker state
            worker_state = await self._collect_worker_state(request)
            components['workers'] = worker_state
            
            # Queue state
            queue_state = await self._collect_queue_state(request)
            components['queues'] = queue_state
            
            # WebSocket state
            websocket_state = await self._collect_websocket_state(request)
            components['websockets'] = websocket_state
            
            # Job state
            job_state = await self._collect_job_state(request)
            components['jobs'] = job_state
            
            # Telemetry state
            telemetry_state = await self._collect_telemetry_state(request)
            components['telemetry'] = telemetry_state
            
            # DAG state
            dag_state = await self._collect_dag_state(request)
            components['dag'] = dag_state
            
            # Add sequence information
            components['sequence_info'] = {
                'current_sequence': current_sequence,
                'tenant_id': request.tenant_id,
                'strategy_id': request.strategy_id,
                'bot_id': request.bot_id
            }
            
            snapshot_data['components'] = components
            
        except Exception as e:
            logger.error(f"Failed to collect full snapshot data: {e}")
            raise
    
    async def _collect_incremental_snapshot_data(self, snapshot_data: Dict[str, Any], request: SnapshotRequest) -> None:
        """Collect incremental snapshot data."""
        try:
            # Get last snapshot for comparison
            last_snapshot = await self._get_last_snapshot(request.tenant_id)
            
            if not last_snapshot:
                # No previous snapshot, collect full data
                await self._collect_full_snapshot_data(snapshot_data, request)
                return
            
            # Get events since last snapshot
            last_sequence = last_snapshot.sequence_range[1]
            events = await self.immutable_journal.get_events_by_sequence_range(
                tenant_id=request.tenant_id,
                start_sequence=last_sequence + 1,
                end_sequence=None
            )
            
            # Process events to get state changes
            state_changes = await self._process_events_for_incremental(events, request)
            
            snapshot_data['components'] = state_changes
            snapshot_data['base_snapshot_id'] = last_snapshot.snapshot_id
            
        except Exception as e:
            logger.error(f"Failed to collect incremental snapshot data: {e}")
            raise
    
    async def _collect_component_snapshot_data(self, snapshot_data: Dict[str, Any], request: SnapshotRequest) -> None:
        """Collect specific component snapshot data."""
        try:
            components = {}
            
            # Collect only specified components
            if request.component_filter:
                for component_name in request.component_filter:
                    if component_name == 'workers':
                        components['workers'] = await self._collect_worker_state(request)
                    elif component_name == 'queues':
                        components['queues'] = await self._collect_queue_state(request)
                    elif component_name == 'websockets':
                        components['websockets'] = await self._collect_websocket_state(request)
                    elif component_name == 'jobs':
                        components['jobs'] = await self._collect_job_state(request)
                    elif component_name == 'telemetry':
                        components['telemetry'] = await self._collect_telemetry_state(request)
                    elif component_name == 'dag':
                        components['dag'] = await self._collect_dag_state(request)
            
            snapshot_data['components'] = components
            
        except Exception as e:
            logger.error(f"Failed to collect component snapshot data: {e}")
            raise
    
    async def _collect_worker_snapshot_data(self, snapshot_data: Dict[str, Any], request: SnapshotRequest) -> None:
        """Collect worker-specific snapshot data."""
        try:
            worker_state = await self._collect_worker_state(request)
            snapshot_data['components'] = {'workers': worker_state}
            
        except Exception as e:
            logger.error(f"Failed to collect worker snapshot data: {e}")
            raise
    
    async def _collect_queue_snapshot_data(self, snapshot_data: Dict[str, Any], request: SnapshotRequest) -> None:
        """Collect queue-specific snapshot data."""
        try:
            queue_state = await self._collect_queue_state(request)
            snapshot_data['components'] = {'queues': queue_state}
            
        except Exception as e:
            logger.error(f"Failed to collect queue snapshot data: {e}")
            raise
    
    async def _collect_websocket_snapshot_data(self, snapshot_data: Dict[str, Any], request: SnapshotRequest) -> None:
        """Collect WebSocket-specific snapshot data."""
        try:
            websocket_state = await self._collect_websocket_state(request)
            snapshot_data['components'] = {'websockets': websocket_state}
            
        except Exception as e:
            logger.error(f"Failed to collect websocket snapshot data: {e}")
            raise
    
    async def _collect_job_snapshot_data(self, snapshot_data: Dict[str, Any], request: SnapshotRequest) -> None:
        """Collect job-specific snapshot data."""
        try:
            job_state = await self._collect_job_state(request)
            snapshot_data['components'] = {'jobs': job_state}
            
        except Exception as e:
            logger.error(f"Failed to collect job snapshot data: {e}")
            raise
    
    async def _collect_telemetry_snapshot_data(self, snapshot_data: Dict[str, Any], request: SnapshotRequest) -> None:
        """Collect telemetry-specific snapshot data."""
        try:
            telemetry_state = await self._collect_telemetry_state(request)
            snapshot_data['components'] = {'telemetry': telemetry_state}
            
        except Exception as e:
            logger.error(f"Failed to collect telemetry snapshot data: {e}")
            raise
    
    async def _collect_dag_snapshot_data(self, snapshot_data: Dict[str, Any], request: SnapshotRequest) -> None:
        """Collect DAG-specific snapshot data."""
        try:
            dag_state = await self._collect_dag_state(request)
            snapshot_data['components'] = {'dag': dag_state}
            
        except Exception as e:
            logger.error(f"Failed to collect DAG snapshot data: {e}")
            raise
    
    async def _collect_worker_state(self, request: SnapshotRequest) -> Dict[str, Any]:
        """Collect worker state for snapshot."""
        try:
            # This would integrate with the actual worker system
            # For now, return a placeholder structure
            return {
                'worker_count': 0,
                'active_workers': {},
                'worker_configurations': {},
                'worker_performance': {},
                'last_updated': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to collect worker state: {e}")
            raise
    
    async def _collect_queue_state(self, request: SnapshotRequest) -> Dict[str, Any]:
        """Collect queue state for snapshot."""
        try:
            # This would integrate with the actual queue system
            # For now, return a placeholder structure
            return {
                'queue_count': 0,
                'queue_states': {},
                'queue_metrics': {},
                'queue_configurations': {},
                'last_updated': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to collect queue state: {e}")
            raise
    
    async def _collect_websocket_state(self, request: SnapshotRequest) -> Dict[str, Any]:
        """Collect WebSocket state for snapshot."""
        try:
            # This would integrate with the actual WebSocket system
            # For now, return a placeholder structure
            return {
                'connection_count': 0,
                'active_connections': {},
                'subscription_states': {},
                'message_buffers': {},
                'last_updated': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to collect websocket state: {e}")
            raise
    
    async def _collect_job_state(self, request: SnapshotRequest) -> Dict[str, Any]:
        """Collect job state for snapshot."""
        try:
            # This would integrate with the actual job system
            # For now, return a placeholder structure
            return {
                'job_count': 0,
                'active_jobs': {},
                'job_history': {},
                'job_metrics': {},
                'last_updated': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to collect job state: {e}")
            raise
    
    async def _collect_telemetry_state(self, request: SnapshotRequest) -> Dict[str, Any]:
        """Collect telemetry state for snapshot."""
        try:
            # This would integrate with the actual telemetry system
            # For now, return a placeholder structure
            return {
                'metric_count': 0,
                'active_metrics': {},
                'metric_history': {},
                'performance_metrics': {},
                'last_updated': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to collect telemetry state: {e}")
            raise
    
    async def _collect_dag_state(self, request: SnapshotRequest) -> Dict[str, Any]:
        """Collect DAG state for snapshot."""
        try:
            # This would integrate with the actual DAG system
            # For now, return a placeholder structure
            return {
                'dag_count': 0,
                'active_dags': {},
                'dag_states': {},
                'dag_performance': {},
                'last_updated': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to collect DAG state: {e}")
            raise
    
    async def _create_snapshot_metadata(self, request: SnapshotRequest, snapshot_data: Dict[str, Any]) -> SnapshotMetadata:
        """Create snapshot metadata."""
        try:
            # Calculate sequence range
            sequence_range = await self._calculate_sequence_range(snapshot_data)
            
            # Calculate time range
            time_range = await self._calculate_time_range(snapshot_data)
            
            # Calculate checksum
            checksum = await self._calculate_snapshot_checksum(snapshot_data)
            
            metadata = SnapshotMetadata(
                snapshot_id=request.snapshot_id,
                tenant_id=request.tenant_id,
                strategy_id=request.strategy_id,
                bot_id=request.bot_id,
                snapshot_type=request.snapshot_type,
                created_at=datetime.fromisoformat(snapshot_data['created_at']),
                created_by="snapshot_manager",
                size_bytes=0,  # Will be calculated after compression
                compressed_size_bytes=0,  # Will be calculated after compression
                component_count=len(snapshot_data.get('components', {})),
                event_count=sum(comp.get('event_count', 0) for comp in snapshot_data.get('components', {}).values()),
                sequence_range=sequence_range,
                time_range=time_range,
                checksum=checksum,
                version=1
            )
            
            return metadata
            
        except Exception as e:
            logger.error(f"Failed to create snapshot metadata: {e}")
            raise
    
    async def _calculate_sequence_range(self, snapshot_data: Dict[str, Any]) -> Tuple[int, int]:
        """Calculate sequence range for snapshot."""
        try:
            sequences = []
            
            # Extract sequence information from components
            for component_name, component_data in snapshot_data.get('components', {}).items():
                if 'sequence_info' in component_data:
                    sequences.append(component_data['sequence_info'].get('current_sequence', 0))
            
            if sequences:
                return (min(sequences), max(sequences))
            else:
                return (0, 0)
                
        except Exception as e:
            logger.error(f"Failed to calculate sequence range: {e}")
            return (0, 0)
    
    async def _calculate_time_range(self, snapshot_data: Dict[str, Any]) -> Tuple[datetime, datetime]:
        """Calculate time range for snapshot."""
        try:
            created_at = datetime.fromisoformat(snapshot_data['created_at'])
            return (created_at, created_at)
            
        except Exception as e:
            logger.error(f"Failed to calculate time range: {e}")
            now = datetime.now(timezone.utc)
            return (now, now)
    
    async def _calculate_snapshot_checksum(self, snapshot_data: Dict[str, Any]) -> str:
        """Calculate checksum for snapshot data."""
        try:
            import hashlib

            # Create canonical representation
            canonical_data = json.dumps(snapshot_data, sort_keys=True, separators=(',', ':'))
            
            # Calculate SHA-256 checksum
            checksum = hashlib.sha256(canonical_data.encode('utf-8')).hexdigest()
            
            return checksum
            
        except Exception as e:
            logger.error(f"Failed to calculate snapshot checksum: {e}")
            return ""
    
    async def _compress_snapshot_data(self, snapshot_data: bytes) -> bytes:
        """Compress snapshot data."""
        try:
            return gzip.compress(snapshot_data, compresslevel=self.compression_level)
            
        except Exception as e:
            logger.error(f"Failed to compress snapshot data: {e}")
            raise
    
    async def _decompress_snapshot_data(self, compressed_data: bytes) -> bytes:
        """Decompress snapshot data."""
        try:
            return gzip.decompress(compressed_data)
            
        except Exception as e:
            logger.error(f"Failed to decompress snapshot data: {e}")
            raise
    
    async def _validate_snapshot_integrity(self, metadata: SnapshotMetadata, snapshot_data: Union[bytes, Dict[str, Any]]) -> 'ValidationResult':
        """Validate snapshot integrity."""
        try:
            # Convert to dict if bytes
            if isinstance(snapshot_data, bytes):
                try:
                    snapshot_data = json.loads(snapshot_data.decode('utf-8'))
                except Exception:
                    snapshot_data = await self._decompress_snapshot_data(snapshot_data)
                    snapshot_data = json.loads(snapshot_data.decode('utf-8'))
            
            # Validate checksum
            calculated_checksum = await self._calculate_snapshot_checksum(snapshot_data)
            if calculated_checksum != metadata.checksum:
                return ValidationResult(
                    success=False,
                    error="Checksum validation failed"
                )
            
            # Validate component count
            component_count = len(snapshot_data.get('components', {}))
            if component_count != metadata.component_count:
                return ValidationResult(
                    success=False,
                    error=f"Component count mismatch: expected {metadata.component_count}, got {component_count}"
                )
            
            # Validate event count
            event_count = sum(comp.get('event_count', 0) for comp in snapshot_data.get('components', {}).values())
            if event_count != metadata.event_count:
                return ValidationResult(
                    success=False,
                    error=f"Event count mismatch: expected {metadata.event_count}, got {event_count}"
                )
            
            return ValidationResult(success=True)
            
        except Exception as e:
            logger.error(f"Snapshot integrity validation failed: {e}")
            return ValidationResult(
                success=False,
                error=str(e)
            )
    
    async def _store_snapshot(self, metadata: SnapshotMetadata, compressed_data: bytes) -> None:
        """Store snapshot data and metadata."""
        try:
            # Store in memory (in production, this would be stored in Redis/S3)
            self.snapshot_storage[metadata.snapshot_id] = metadata
            self.snapshot_data[metadata.snapshot_id] = compressed_data
            
            # Update cache
            self.metadata_cache[metadata.snapshot_id] = metadata
            
            # Enforce TTL
            await self._enforce_snapshot_ttl()
            
        except Exception as e:
            logger.error(f"Failed to store snapshot: {e}")
            raise
    
    async def _restore_snapshot_state(self, snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
        """Restore state from snapshot data."""
        try:
            # This would integrate with actual system components
            # For now, return the snapshot data as-is
            return snapshot_data
            
        except Exception as e:
            logger.error(f"Failed to restore snapshot state: {e}")
            raise
    
    async def _get_last_snapshot(self, tenant_id: str) -> Optional[SnapshotMetadata]:
        """Get the most recent snapshot for tenant."""
        try:
            snapshots = await self.list_snapshots(tenant_id)
            return snapshots[0] if snapshots else None
            
        except Exception as e:
            logger.error(f"Failed to get last snapshot: {e}")
            return None
    
    async def _process_events_for_incremental(self, events: List[ExecutionEvent], request: SnapshotRequest) -> Dict[str, Any]:
        """Process events to determine state changes for incremental snapshot."""
        try:
            # This would process events to determine state changes
            # For now, return a placeholder structure
            return {
                'events_processed': len(events),
                'state_changes': {},
                'sequence_range': (events[0].header.sequence_id if events else 0, events[-1].header.sequence_id if events else 0)
            }
            
        except Exception as e:
            logger.error(f"Failed to process events for incremental: {e}")
            raise
    
    async def _enforce_snapshot_ttl(self) -> None:
        """Enforce snapshot TTL by removing old snapshots."""
        try:
            current_time = datetime.now(timezone.utc)
            expired_snapshots = []
            
            for snapshot_id, metadata in self.snapshot_storage.items():
                age_seconds = (current_time - metadata.created_at).total_seconds()
                if age_seconds > self.snapshot_ttl:
                    expired_snapshots.append(snapshot_id)
            
            # Remove expired snapshots
            for snapshot_id in expired_snapshots:
                await self.delete_snapshot(snapshot_id)
            
            if expired_snapshots:
                logger.info(f"Removed {len(expired_snapshots)} expired snapshots")
                
        except Exception as e:
            logger.error(f"Failed to enforce snapshot TTL: {e}")
    
    async def _validate_tenant_exists(self, tenant_id: str) -> bool:
        """Validate tenant exists."""
        try:
            # Check if tenant has any events
            events = await self.immutable_journal.get_events_by_tenant(tenant_id, limit=1)
            return len(events) > 0
        except Exception as e:
            logger.error(f"Failed to validate tenant exists: {e}")
            return False
    
    async def _validate_strategy_exists(self, tenant_id: str, strategy_id: str) -> bool:
        """Validate strategy exists."""
        try:
            # Check if strategy has any events
            events = await self.immutable_journal.get_events_by_strategy(tenant_id, strategy_id, limit=1)
            return len(events) > 0
        except Exception as e:
            logger.error(f"Failed to validate strategy exists: {e}")
            return False
    
    async def _validate_bot_exists(self, tenant_id: str, bot_id: str) -> bool:
        """Validate bot exists."""
        try:
            # Check if bot has any events
            events = await self.immutable_journal.get_events_by_bot(tenant_id, bot_id, limit=1)
            return len(events) > 0
        except Exception as e:
            logger.error(f"Failed to validate bot exists: {e}")
            return False
    
    async def get_snapshot_statistics(self) -> Dict[str, Any]:
        """Get snapshot manager statistics."""
        try:
            return {
                'total_snapshots': len(self.snapshot_storage),
                'snapshot_cache_size': len(self.snapshot_cache),
                'metadata_cache_size': len(self.metadata_cache),
                'max_snapshot_size': self.max_snapshot_size,
                'snapshot_ttl': self.snapshot_ttl,
                'max_snapshots_per_tenant': self.max_snapshots_per_tenant,
                'compression_level': self.compression_level
            }
        except Exception as e:
            logger.error(f"Failed to get snapshot statistics: {e}")
            return {'error': str(e)}


@dataclass
class ValidationResult:
    """Validation result for snapshot operations."""
    success: bool
    error: Optional[str] = None
    is_valid: bool = field(init=False)
    
    def __post_init__(self):
        self.is_valid = self.success


# Global snapshot manager instance
snapshot_manager = SnapshotManager()
