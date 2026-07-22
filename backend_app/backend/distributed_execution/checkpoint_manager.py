
"""
Checkpoint Manager for Fast Recovery Points

Provides fast recovery points for efficient replay and recovery operations.
Supports time-based, event-based, and threshold-based checkpointing with
deterministic creation and restoration.

Author: Principal Replay and Recovery Engineer
"""
import asyncio
import gzip
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from .immutable_journal import ExecutionEvent, immutable_journal
from .sequence_manager import sequence_manager

logger = logging.getLogger("checkpoint_manager")


@dataclass
class CheckpointRequest:
    """Checkpoint creation request."""
    checkpoint_id: str
    tenant_id: str
    strategy_id: Optional[str] = None
    bot_id: Optional[str] = None
    checkpoint_type: 'CheckpointType' = None
    trigger_type: 'CheckpointTriggerType' = None
    component_filter: Optional[List[str]] = None
    include_metadata: bool = True
    compress_data: bool = True
    validate_integrity: bool = True


@dataclass
class CheckpointResult:
    """Result of checkpoint operation."""
    checkpoint_id: str
    success: bool
    checkpoint_type: 'CheckpointType'
    trigger_type: 'CheckpointTriggerType'
    created_at: datetime
    sequence_id: int
    size_bytes: int
    compressed_size_bytes: int
    component_count: int
    event_count: int
    metadata: Dict[str, Any]
    error: Optional[str] = None


@dataclass
class CheckpointMetadata:
    """Checkpoint metadata."""
    checkpoint_id: str
    tenant_id: str
    strategy_id: Optional[str]
    bot_id: Optional[str]
    checkpoint_type: 'CheckpointType'
    trigger_type: 'CheckpointTriggerType'
    created_at: datetime
    created_by: str
    sequence_id: int
    size_bytes: int
    compressed_size_bytes: int
    component_count: int
    event_count: int
    checksum: str
    version: int
    ttl_seconds: int


@dataclass
class CheckpointData:
    """Checkpoint data for recovery."""
    checkpoint_id: str
    sequence_id: int
    created_at: datetime
    component_states: Dict[str, Any]
    event_buffer: List[Dict[str, Any]]
    metadata: Dict[str, Any]


class CheckpointType(Enum):
    """Checkpoint types."""
    FULL = "full"
    INCREMENTAL = "incremental"
    COMPONENT = "component"
    WORKER = "worker"
    QUEUE = "queue"
    WEBSOCKET = "websocket"
    JOB = "job"
    TELEMETRY = "telemetry"
    DAG = "dag"
    RECOVERY = "recovery"
    DEBUG = "debug"
    AUDIT = "audit"


class CheckpointTriggerType(Enum):
    """Checkpoint trigger types."""
    MANUAL = "manual"
    PERIODIC = "periodic"
    EVENT_BASED = "event_based"
    THRESHOLD_BASED = "threshold_based"
    TIME_BASED = "time_based"
    VOLUME_BASED = "volume_based"
    ERROR_BASED = "error_based"


class CheckpointManager:
    """Manages checkpoints for fast recovery and replay."""
    
    def __init__(self):
        self.immutable_journal = immutable_journal
        self.sequence_manager = sequence_manager
        
        # Checkpoint storage
        self.checkpoint_storage: Dict[str, CheckpointMetadata] = {}
        self.checkpoint_data: Dict[str, bytes] = {}
        
        # Checkpoint configuration
        self.compression_level = 6
        self.max_checkpoint_size = 50 * 1024 * 1024  # 50MB
        self.checkpoint_ttl = 86400 * 7  # 7 days
        self.max_checkpoints_per_tenant = 50
        
        # Periodic checkpoint configuration
        self.periodic_interval = 3600  # 1 hour
        self.last_periodic_checkpoint = {}
        
        # Threshold-based checkpoint configuration
        self.event_threshold = 1000  # Create checkpoint every 1000 events
        self.volume_threshold = 100 * 1024 * 1024  # 100MB of data
        
        # Performance optimization
        self.checkpoint_cache: Dict[str, CheckpointData] = {}
        self.metadata_cache: Dict[str, CheckpointMetadata] = {}
        
        logger.info("Checkpoint manager initialized")
    
    async def initialize(self) -> bool:
        """Initialize checkpoint manager components."""
        try:
            # Initialize dependencies
            await self.immutable_journal.initialize()
            await self.sequence_manager.initialize()
            
            # Start periodic checkpoint scheduler
            asyncio.create_task(self._periodic_checkpoint_scheduler())
            
            logger.info("Checkpoint manager initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize checkpoint manager: {e}")
            return False
    
    async def create_checkpoint(self, request: CheckpointRequest) -> CheckpointResult:
        """Create a checkpoint for fast recovery."""
        try:
            checkpoint_start = time.time()
            
            # Validate checkpoint request
            validation_result = await self._validate_checkpoint_request(request)
            if not validation_result.success:
                return CheckpointResult(
                    checkpoint_id=request.checkpoint_id,
                    success=False,
                    checkpoint_type=request.checkpoint_type,
                    trigger_type=request.trigger_type,
                    created_at=datetime.now(timezone.utc),
                    sequence_id=0,
                    size_bytes=0,
                    compressed_size_bytes=0,
                    component_count=0,
                    event_count=0,
                    metadata={},
                    error=validation_result.error
                )
            
            # Get current sequence
            current_sequence = await self.sequence_manager.get_current_sequence(request.tenant_id)
            
            # Collect checkpoint data
            checkpoint_data = await self._collect_checkpoint_data(request, current_sequence)
            
            # Create checkpoint metadata
            metadata = await self._create_checkpoint_metadata(request, current_sequence, checkpoint_data)
            
            # Compress checkpoint data if requested
            compressed_data = checkpoint_data
            if request.compress_data:
                compressed_data = await self._compress_checkpoint_data(checkpoint_data)
            
            # Validate integrity if requested
            if request.validate_integrity:
                integrity_result = await self._validate_checkpoint_integrity(metadata, compressed_data)
                if not integrity_result.success:
                    return CheckpointResult(
                        checkpoint_id=request.checkpoint_id,
                        success=False,
                        checkpoint_type=request.checkpoint_type,
                        trigger_type=request.trigger_type,
                        created_at=datetime.now(timezone.utc),
                        sequence_id=current_sequence,
                        size_bytes=0,
                        compressed_size_bytes=0,
                        component_count=0,
                        event_count=0,
                        metadata={},
                        error=integrity_result.error
                    )
            
            # Store checkpoint
            await self._store_checkpoint(metadata, compressed_data)
            
            # Calculate statistics
            size_bytes = len(json.dumps(checkpoint_data).encode('utf-8'))
            compressed_size_bytes = len(compressed_data)
            component_count = len(checkpoint_data.get('component_states', {}))
            event_count = len(checkpoint_data.get('event_buffer', []))
            
            # Create result
            result = CheckpointResult(
                checkpoint_id=request.checkpoint_id,
                success=True,
                checkpoint_type=request.checkpoint_type,
                trigger_type=request.trigger_type,
                created_at=metadata.created_at,
                sequence_id=current_sequence,
                size_bytes=size_bytes,
                compressed_size_bytes=compressed_size_bytes,
                component_count=component_count,
                event_count=event_count,
                metadata=asdict(metadata)
            )
            
            # Log completion
            duration = time.time() - checkpoint_start
            logger.info(f"Checkpoint {request.checkpoint_id} created in {duration:.2f}s at sequence {current_sequence}")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to create checkpoint {request.checkpoint_id}: {e}")
            return CheckpointResult(
                checkpoint_id=request.checkpoint_id,
                success=False,
                checkpoint_type=request.checkpoint_type,
                trigger_type=request.trigger_type,
                created_at=datetime.now(timezone.utc),
                sequence_id=0,
                size_bytes=0,
                compressed_size_bytes=0,
                component_count=0,
                event_count=0,
                metadata={},
                error=str(e)
            )
    
    async def restore_checkpoint(self, checkpoint_id: str, component_filter: Optional[List[str]] = None) -> Dict[str, Any]:
        """Restore state from checkpoint."""
        try:
            # Get checkpoint metadata
            metadata = self.checkpoint_storage.get(checkpoint_id)
            if not metadata:
                raise ValueError(f"Checkpoint {checkpoint_id} not found")
            
            # Get checkpoint data
            compressed_data = self.checkpoint_data.get(checkpoint_id)
            if not compressed_data:
                raise ValueError(f"Checkpoint data {checkpoint_id} not found")
            
            # Decompress data if needed
            checkpoint_data = compressed_data
            if metadata.compressed_size_bytes < metadata.size_bytes:
                checkpoint_data = await self._decompress_checkpoint_data(compressed_data)
            
            # Validate integrity
            integrity_result = await self._validate_checkpoint_integrity(metadata, checkpoint_data)
            if not integrity_result.success:
                raise ValueError(f"Checkpoint integrity validation failed: {integrity_result.error}")
            
            # Filter components if requested
            if component_filter:
                if 'component_states' in checkpoint_data:
                    checkpoint_data['component_states'] = {
                        k: v for k, v in checkpoint_data['component_states'].items()
                        if k in component_filter
                    }
            
            # Restore state
            restored_state = await self._restore_checkpoint_state(checkpoint_data)
            
            logger.info(f"Restored checkpoint {checkpoint_id} at sequence {metadata.sequence_id}")
            
            return restored_state
            
        except Exception as e:
            logger.error(f"Failed to restore checkpoint {checkpoint_id}: {e}")
            raise
    
    async def list_checkpoints(self, tenant_id: str, checkpoint_type: Optional[CheckpointType] = None,
                             limit: int = 100) -> List[CheckpointMetadata]:
        """List available checkpoints."""
        try:
            checkpoints = []
            
            for checkpoint_id, metadata in self.checkpoint_storage.items():
                if metadata.tenant_id != tenant_id:
                    continue
                
                if checkpoint_type and metadata.checkpoint_type != checkpoint_type:
                    continue
                
                checkpoints.append(metadata)
            
            # Sort by sequence (newest first)
            checkpoints.sort(key=lambda c: c.sequence_id, reverse=True)
            
            return checkpoints[:limit] if len(checkpoints) > limit else checkpoints
            
        except Exception as e:
            logger.error(f"Failed to list checkpoints: {e}")
            return []
    
    async def delete_checkpoint(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint."""
        try:
            if checkpoint_id in self.checkpoint_storage:
                del self.checkpoint_storage[checkpoint_id]
            
            if checkpoint_id in self.checkpoint_data:
                del self.checkpoint_data[checkpoint_id]
            
            # Clear caches
            if checkpoint_id in self.metadata_cache:
                del self.metadata_cache[checkpoint_id]
            
            logger.info(f"Deleted checkpoint {checkpoint_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete checkpoint {checkpoint_id}: {e}")
            return False
    
    async def get_latest_checkpoint(self, tenant_id: str, checkpoint_type: Optional[CheckpointType] = None) -> Optional[CheckpointMetadata]:
        """Get the most recent checkpoint for tenant."""
        try:
            checkpoints = await self.list_checkpoints(tenant_id, checkpoint_type)
            return checkpoints[0] if checkpoints else None
            
        except Exception as e:
            logger.error(f"Failed to get latest checkpoint: {e}")
            return None
    
    async def create_automatic_checkpoint(self, tenant_id: str, trigger_type: CheckpointTriggerType,
                                       reason: Optional[str] = None) -> CheckpointResult:
        """Create automatic checkpoint based on trigger."""
        try:
            checkpoint_id = str(uuid.uuid4())
            
            request = CheckpointRequest(
                checkpoint_id=checkpoint_id,
                tenant_id=tenant_id,
                checkpoint_type=CheckpointType.FULL,
                trigger_type=trigger_type,
                include_metadata=True,
                compress_data=True,
                validate_integrity=True
            )
            
            result = await self.create_checkpoint(request)
            
            if result.success and reason:
                # Add reason to metadata
                if checkpoint_id in self.checkpoint_storage:
                    self.checkpoint_storage[checkpoint_id].created_by = f"automatic_{trigger_type.value}"
                    self.checkpoint_storage[checkpoint_id].ttl_seconds = self.checkpoint_ttl
            
                logger.info(f"Automatic checkpoint created: {reason}")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to create automatic checkpoint: {e}")
            raise
    
    async def _validate_checkpoint_request(self, request: CheckpointRequest) -> 'ValidationResult':
        """Validate checkpoint request."""
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
            
            # Check checkpoint limit
            tenant_checkpoints = await self.list_checkpoints(request.tenant_id)
            if len(tenant_checkpoints) >= self.max_checkpoints_per_tenant:
                return ValidationResult(
                    success=False,
                    error=f"Maximum checkpoints per tenant exceeded ({self.max_checkpoints_per_tenant})"
                )
            
            return ValidationResult(success=True)
            
        except Exception as e:
            logger.error(f"Checkpoint request validation failed: {e}")
            return ValidationResult(
                success=False,
                error=str(e)
            )
    
    async def _collect_checkpoint_data(self, request: CheckpointRequest, sequence_id: int) -> Dict[str, Any]:
        """Collect checkpoint data from system components."""
        try:
            checkpoint_data = CheckpointData(
                checkpoint_id=request.checkpoint_id,
                sequence_id=sequence_id,
                created_at=datetime.now(timezone.utc),
                component_states={},
                event_buffer=[],
                metadata={}
            )
            
            # Collect component states based on checkpoint type
            if request.checkpoint_type == CheckpointType.FULL:
                await self._collect_full_checkpoint_data(checkpoint_data, request)
            elif request.checkpoint_type == CheckpointType.INCREMENTAL:
                await self._collect_incremental_checkpoint_data(checkpoint_data, request)
            elif request.checkpoint_type == CheckpointType.COMPONENT:
                await self._collect_component_checkpoint_data(checkpoint_data, request)
            elif request.checkpoint_type == CheckpointType.WORKER:
                await self._collect_worker_checkpoint_data(checkpoint_data, request)
            elif request.checkpoint_type == CheckpointType.QUEUE:
                await self._collect_queue_checkpoint_data(checkpoint_data, request)
            elif request.checkpoint_type == CheckpointType.WEBSOCKET:
                await self._collect_websocket_checkpoint_data(checkpoint_data, request)
            elif request.checkpoint_type == CheckpointType.JOB:
                await self._collect_job_checkpoint_data(checkpoint_data, request)
            elif request.checkpoint_type == CheckpointType.TELEMETRY:
                await self._collect_telemetry_checkpoint_data(checkpoint_data, request)
            elif request.checkpoint_type == CheckpointType.DAG:
                await self._collect_dag_checkpoint_data(checkpoint_data, request)
            elif request.checkpoint_type == CheckpointType.RECOVERY:
                await self._collect_recovery_checkpoint_data(checkpoint_data, request)
            elif request.checkpoint_type == CheckpointType.DEBUG:
                await self._collect_debug_checkpoint_data(checkpoint_data, request)
            elif request.checkpoint_type == CheckpointType.AUDIT:
                await self._collect_audit_checkpoint_data(checkpoint_data, request)
            
            return asdict(checkpoint_data)
            
        except Exception as e:
            logger.error(f"Failed to collect checkpoint data: {e}")
            raise
    
    async def _collect_full_checkpoint_data(self, checkpoint_data: CheckpointData, request: CheckpointRequest) -> None:
        """Collect full system checkpoint data."""
        try:
            # Collect all component states
            component_states = {}
            
            # Worker state
            worker_state = await self._collect_worker_state(request)
            component_states['workers'] = worker_state
            
            # Queue state
            queue_state = await self._collect_queue_state(request)
            component_states['queues'] = queue_state
            
            # WebSocket state
            websocket_state = await self._collect_websocket_state(request)
            component_states['websockets'] = websocket_state
            
            # Job state
            job_state = await self._collect_job_state(request)
            component_states['jobs'] = job_state
            
            # Telemetry state
            telemetry_state = await self._collect_telemetry_state(request)
            component_states['telemetry'] = telemetry_state
            
            # DAG state
            dag_state = await self._collect_dag_state(request)
            component_states['dag'] = dag_state
            
            # Collect recent events for replay buffer
            event_buffer = await self._collect_recent_events(request, checkpoint_data.sequence_id)
            
            checkpoint_data.component_states = component_states
            checkpoint_data.event_buffer = event_buffer
            
        except Exception as e:
            logger.error(f"Failed to collect full checkpoint data: {e}")
            raise
    
    async def _collect_incremental_checkpoint_data(self, checkpoint_data: CheckpointData, request: CheckpointRequest) -> None:
        """Collect incremental checkpoint data."""
        try:
            # Get last checkpoint for comparison
            last_checkpoint = await self.get_latest_checkpoint(request.tenant_id)
            
            if not last_checkpoint:
                # No previous checkpoint, collect full data
                await self._collect_full_checkpoint_data(checkpoint_data, request)
                return
            
            # Get events since last checkpoint
            last_sequence = last_checkpoint.sequence_id
            events = await self.immutable_journal.get_events_by_sequence_range(
                tenant_id=request.tenant_id,
                start_sequence=last_sequence + 1,
                end_sequence=checkpoint_data.sequence_id
            )
            
            # Process events to get state changes
            state_changes = await self._process_events_for_checkpoint(events, request)
            
            checkpoint_data.component_states = state_changes
            checkpoint_data.event_buffer = [asdict(event) for event in events[-100:]]  # Last 100 events
            
        except Exception as e:
            logger.error(f"Failed to collect incremental checkpoint data: {e}")
            raise
    
    async def _collect_component_checkpoint_data(self, checkpoint_data: CheckpointData, request: CheckpointRequest) -> None:
        """Collect specific component checkpoint data."""
        try:
            component_states = {}
            
            # Collect only specified components
            if request.component_filter:
                for component_name in request.component_filter:
                    if component_name == 'workers':
                        component_states['workers'] = await self._collect_worker_state(request)
                    elif component_name == 'queues':
                        component_states['queues'] = await self._collect_queue_state(request)
                    elif component_name == 'websockets':
                        component_states['websockets'] = await self._collect_websocket_state(request)
                    elif component_name == 'jobs':
                        component_states['jobs'] = await self._collect_job_state(request)
                    elif component_name == 'telemetry':
                        component_states['telemetry'] = await self._collect_telemetry_state(request)
                    elif component_name == 'dag':
                        component_states['dag'] = await self._collect_dag_state(request)
            
            checkpoint_data.component_states = component_states
            
        except Exception as e:
            logger.error(f"Failed to collect component checkpoint data: {e}")
            raise
    
    async def _collect_worker_checkpoint_data(self, checkpoint_data: CheckpointData, request: CheckpointRequest) -> None:
        """Collect worker-specific checkpoint data."""
        try:
            worker_state = await self._collect_worker_state(request)
            checkpoint_data.component_states = {'workers': worker_state}
            
        except Exception as e:
            logger.error(f"Failed to collect worker checkpoint data: {e}")
            raise
    
    async def _collect_queue_checkpoint_data(self, checkpoint_data: CheckpointData, request: CheckpointRequest) -> None:
        """Collect queue-specific checkpoint data."""
        try:
            queue_state = await self._collect_queue_state(request)
            checkpoint_data.component_states = {'queues': queue_state}
            
        except Exception as e:
            logger.error(f"Failed to collect queue checkpoint data: {e}")
            raise
    
    async def _collect_websocket_checkpoint_data(self, checkpoint_data: CheckpointData, request: CheckpointRequest) -> None:
        """Collect WebSocket-specific checkpoint data."""
        try:
            websocket_state = await self._collect_websocket_state(request)
            checkpoint_data.component_states = {'websockets': websocket_state}
            
        except Exception as e:
            logger.error(f"Failed to collect websocket checkpoint data: {e}")
            raise
    
    async def _collect_job_checkpoint_data(self, checkpoint_data: CheckpointData, request: CheckpointRequest) -> None:
        """Collect job-specific checkpoint data."""
        try:
            job_state = await self._collect_job_state(request)
            checkpoint_data.component_states = {'jobs': job_state}
            
        except Exception as e:
            logger.error(f"Failed to collect job checkpoint data: {e}")
            raise
    
    async def _collect_telemetry_checkpoint_data(self, checkpoint_data: CheckpointData, request: CheckpointRequest) -> None:
        """Collect telemetry-specific checkpoint data."""
        try:
            telemetry_state = await self._collect_telemetry_state(request)
            checkpoint_data.component_states = {'telemetry': telemetry_state}
            
        except Exception as e:
            logger.error(f"Failed to collect telemetry checkpoint data: {e}")
            raise
    
    async def _collect_dag_checkpoint_data(self, checkpoint_data: CheckpointData, request: CheckpointRequest) -> None:
        """Collect DAG-specific checkpoint data."""
        try:
            dag_state = await self._collect_dag_state(request)
            checkpoint_data.component_states = {'dag': dag_state}
            
        except Exception as e:
            logger.error(f"Failed to collect DAG checkpoint data: {e}")
            raise
    
    async def _collect_recovery_checkpoint_data(self, checkpoint_data: CheckpointData, request: CheckpointRequest) -> None:
        """Collect recovery-specific checkpoint data."""
        try:
            # Collect recovery-relevant state
            recovery_state = {
                'sequence_info': {
                    'current_sequence': checkpoint_data.sequence_id,
                    'tenant_id': request.tenant_id,
                    'strategy_id': request.strategy_id,
                    'bot_id': request.bot_id
                },
                'recent_events': await self._collect_recent_events(request, checkpoint_data.sequence_id),
                'error_state': await self._collect_error_state(request),
                'performance_state': await self._collect_performance_state(request)
            }
            
            checkpoint_data.component_states = recovery_state
            
        except Exception as e:
            logger.error(f"Failed to collect recovery checkpoint data: {e}")
            raise
    
    async def _collect_debug_checkpoint_data(self, checkpoint_data: CheckpointData, request: CheckpointRequest) -> None:
        """Collect debug-specific checkpoint data."""
        try:
            # Collect debug-relevant state
            debug_state = {
                'sequence_info': {
                    'current_sequence': checkpoint_data.sequence_id,
                    'tenant_id': request.tenant_id,
                    'strategy_id': request.strategy_id,
                    'bot_id': request.bot_id
                },
                'recent_events': await self._collect_recent_events(request, checkpoint_data.sequence_id),
                'debug_logs': await self._collect_debug_logs(request),
                'performance_metrics': await self._collect_performance_state(request)
            }
            
            checkpoint_data.component_states = debug_state
            
        except Exception as e:
            logger.error(f"Failed to collect debug checkpoint data: {e}")
            raise
    
    async def _collect_audit_checkpoint_data(self, checkpoint_data: CheckpointData, request: CheckpointRequest) -> None:
        """Collect audit-specific checkpoint data."""
        try:
            # Collect audit-relevant state
            audit_state = {
                'sequence_info': {
                    'current_sequence': checkpoint_data.sequence_id,
                    'tenant_id': request.tenant_id,
                    'strategy_id': request.strategy_id,
                    'bot_id': request.bot_id
                },
                'recent_events': await self._collect_recent_events(request, checkpoint_data.sequence_id),
                'audit_trail': await self._collect_audit_trail(request),
                'compliance_state': await self._collect_compliance_state(request)
            }
            
            checkpoint_data.component_states = audit_state
            
        except Exception as e:
            logger.error(f"Failed to collect audit checkpoint data: {e}")
            raise
    
    async def _collect_recent_events(self, request: CheckpointRequest, sequence_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """Collect recent events for replay buffer."""
        try:
            # Get events before current sequence
            start_sequence = max(0, sequence_id - limit)
            events = await self.immutable_journal.get_events_by_sequence_range(
                tenant_id=request.tenant_id,
                start_sequence=start_sequence,
                end_sequence=sequence_id
            )
            
            # Convert to dictionaries
            event_dicts = []
            for event in events:
                event_dict = {
                    'event_id': event.header.event_id,
                    'event_type': event.header.event_type.value,
                    'sequence_id': event.header.sequence_id,
                    'timestamp': event.header.timestamp.isoformat(),
                    'tenant_id': event.header.tenant_id,
                    'strategy_id': event.header.strategy_id,
                    'bot_id': event.header.bot_id,
                    'payload': event.payload
                }
                event_dicts.append(event_dict)
            
            return event_dicts
            
        except Exception as e:
            logger.error(f"Failed to collect recent events: {e}")
            raise
    
    async def _collect_worker_state(self, request: CheckpointRequest) -> Dict[str, Any]:
        """Collect worker state for checkpoint."""
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
    
    async def _collect_queue_state(self, request: CheckpointRequest) -> Dict[str, Any]:
        """Collect queue state for checkpoint."""
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
    
    async def _collect_websocket_state(self, request: CheckpointRequest) -> Dict[str, Any]:
        """Collect WebSocket state for checkpoint."""
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
    
    async def _collect_job_state(self, request: CheckpointRequest) -> Dict[str, Any]:
        """Collect job state for checkpoint."""
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
    
    async def _collect_telemetry_state(self, request: CheckpointRequest) -> Dict[str, Any]:
        """Collect telemetry state for checkpoint."""
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
    
    async def _collect_dag_state(self, request: CheckpointRequest) -> Dict[str, Any]:
        """Collect DAG state for checkpoint."""
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
    
    async def _collect_error_state(self, request: CheckpointRequest) -> Dict[str, Any]:
        """Collect error state for checkpoint."""
        try:
            # This would integrate with error tracking systems
            return {
                'error_count': 0,
                'recent_errors': {},
                'error_types': {},
                'error_trends': {},
                'last_updated': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to collect error state: {e}")
            raise
    
    async def _collect_performance_state(self, request: CheckpointRequest) -> Dict[str, Any]:
        """Collect performance state for checkpoint."""
        try:
            # This would integrate with performance monitoring systems
            return {
                'cpu_usage': 0.0,
                'memory_usage': 0.0,
                'network_io': 0.0,
                'disk_io': 0.0,
                'response_times': {},
                'last_updated': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to collect performance state: {e}")
            raise
    
    async def _collect_debug_logs(self, request: CheckpointRequest) -> Dict[str, Any]:
        """Collect debug logs for checkpoint."""
        try:
            # This would integrate with logging systems
            return {
                'log_count': 0,
                'recent_logs': {},
                'log_levels': {},
                'log_sources': {},
                'last_updated': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to collect debug logs: {e}")
            raise
    
    async def _collect_audit_trail(self, request: CheckpointRequest) -> Dict[str, Any]:
        """Collect audit trail for checkpoint."""
        try:
            # This would integrate with audit systems
            return {
                'audit_count': 0,
                'recent_audits': {},
                'audit_types': {},
                'audit_sources': {},
                'last_updated': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to collect audit trail: {e}")
            raise
    
    async def _collect_compliance_state(self, request: CheckpointRequest) -> Dict[str, Any]:
        """Collect compliance state for checkpoint."""
        try:
            # This would integrate with compliance systems
            return {
                'compliance_status': 'unknown',
                'regulatory_checks': {},
                'policy_violations': {},
                'compliance_reports': {},
                'last_updated': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to collect compliance state: {e}")
            raise
    
    async def _process_events_for_checkpoint(self, events: List[ExecutionEvent], request: CheckpointRequest) -> Dict[str, Any]:
        """Process events to determine state changes for checkpoint."""
        try:
            # This would process events to determine state changes
            # For now, return a placeholder structure
            return {
                'events_processed': len(events),
                'state_changes': {},
                'sequence_range': (events[0].header.sequence_id if events else 0, events[-1].header.sequence_id if events else 0)
            }
            
        except Exception as e:
            logger.error(f"Failed to process events for checkpoint: {e}")
            raise
    
    async def _create_checkpoint_metadata(self, request: CheckpointRequest, sequence_id: int, checkpoint_data: Dict[str, Any]) -> CheckpointMetadata:
        """Create checkpoint metadata."""
        try:
            # Calculate checksum
            checksum = await self._calculate_checkpoint_checksum(checkpoint_data)
            
            metadata = CheckpointMetadata(
                checkpoint_id=request.checkpoint_id,
                tenant_id=request.tenant_id,
                strategy_id=request.strategy_id,
                bot_id=request.bot_id,
                checkpoint_type=request.checkpoint_type,
                trigger_type=request.trigger_type,
                created_at=checkpoint_data['created_at'],
                created_by="checkpoint_manager",
                sequence_id=sequence_id,
                size_bytes=0,  # Will be calculated after compression
                compressed_size_bytes=0,  # Will be calculated after compression
                component_count=len(checkpoint_data.get('component_states', {})),
                event_count=len(checkpoint_data.get('event_buffer', [])),
                checksum=checksum,
                version=1,
                ttl_seconds=self.checkpoint_ttl
            )
            
            return metadata
            
        except Exception as e:
            logger.error(f"Failed to create checkpoint metadata: {e}")
            raise
    
    async def _calculate_checkpoint_checksum(self, checkpoint_data: Dict[str, Any]) -> str:
        """Calculate checksum for checkpoint data."""
        try:
            import hashlib

            # Create canonical representation
            canonical_data = json.dumps(checkpoint_data, sort_keys=True, separators=(',', ':'))
            
            # Calculate SHA-256 checksum
            checksum = hashlib.sha256(canonical_data.encode('utf-8')).hexdigest()
            
            return checksum
            
        except Exception as e:
            logger.error(f"Failed to calculate checkpoint checksum: {e}")
            return ""
    
    async def _compress_checkpoint_data(self, checkpoint_data: bytes) -> bytes:
        """Compress checkpoint data."""
        try:
            return gzip.compress(checkpoint_data, compresslevel=self.compression_level)
            
        except Exception as e:
            logger.error(f"Failed to compress checkpoint data: {e}")
            raise
    
    async def _decompress_checkpoint_data(self, compressed_data: bytes) -> bytes:
        """Decompress checkpoint data."""
        try:
            return gzip.decompress(compressed_data)
            
        except Exception as e:
            logger.error(f"Failed to decompress checkpoint data: {e}")
            raise
    
    async def _validate_checkpoint_integrity(self, metadata: CheckpointMetadata, checkpoint_data: Union[bytes, Dict[str, Any]]) -> 'ValidationResult':
        """Validate checkpoint integrity."""
        try:
            # Convert to dict if bytes
            if isinstance(checkpoint_data, bytes):
                try:
                    checkpoint_data = json.loads(checkpoint_data.decode('utf-8'))
                except Exception:
                    checkpoint_data = await self._decompress_checkpoint_data(checkpoint_data)
                    checkpoint_data = json.loads(checkpoint_data.decode('utf-8'))
            
            # Validate checksum
            calculated_checksum = await self._calculate_checkpoint_checksum(checkpoint_data)
            if calculated_checksum != metadata.checksum:
                return ValidationResult(
                    success=False,
                    error="Checksum validation failed"
                )
            
            # Validate component count
            component_count = len(checkpoint_data.get('component_states', {}))
            if component_count != metadata.component_count:
                return ValidationResult(
                    success=False,
                    error=f"Component count mismatch: expected {metadata.component_count}, got {component_count}"
                )
            
            # Validate event count
            event_count = len(checkpoint_data.get('event_buffer', []))
            if event_count != metadata.event_count:
                return ValidationResult(
                    success=False,
                    error=f"Event count mismatch: expected {metadata.event_count}, got {event_count}"
                )
            
            return ValidationResult(success=True)
            
        except Exception as e:
            logger.error(f"Checkpoint integrity validation failed: {e}")
            return ValidationResult(
                success=False,
                error=str(e)
            )
    
    async def _store_checkpoint(self, metadata: CheckpointMetadata, compressed_data: bytes) -> None:
        """Store checkpoint data and metadata."""
        try:
            # Store in memory (in production, this would be stored in Redis/S3)
            self.checkpoint_storage[metadata.checkpoint_id] = metadata
            self.checkpoint_data[metadata.checkpoint_id] = compressed_data
            
            # Update cache
            self.metadata_cache[metadata.checkpoint_id] = metadata
            
            # Enforce TTL
            await self._enforce_checkpoint_ttl()
            
        except Exception as e:
            logger.error(f"Failed to store checkpoint: {e}")
            raise
    
    async def _restore_checkpoint_state(self, checkpoint_data: Dict[str, Any]) -> Dict[str, Any]:
        """Restore state from checkpoint."""
        try:
            # This would integrate with actual system components
            # For now, return the checkpoint data as-is
            return checkpoint_data
            
        except Exception as e:
            logger.error(f"Failed to restore checkpoint state: {e}")
            raise
    
    async def _periodic_checkpoint_scheduler(self) -> None:
        """Schedule periodic checkpoints."""
        try:
            while True:
                await asyncio.sleep(self.periodic_interval)
                
                # Get all tenants with recent activity
                active_tenants = await self._get_active_tenants()
                
                for tenant_id in active_tenants:
                    last_checkpoint_time = self.last_periodic_checkpoint.get(tenant_id, datetime.min)
                    
                    # Check if it's time for periodic checkpoint
                    if datetime.now(timezone.utc) - last_checkpoint_time > timedelta(seconds=self.periodic_interval):
                        try:
                            result = await self.create_automatic_checkpoint(
                                tenant_id=tenant_id,
                                trigger_type=CheckpointTriggerType.PERIODIC,
                                reason=f"Periodic checkpoint after {self.periodic_interval} seconds"
                            )
                            
                            if result.success:
                                self.last_periodic_checkpoint[tenant_id] = datetime.now(timezone.utc)
                                logger.info(f"Periodic checkpoint created for tenant {tenant_id}")
                            else:
                                logger.error(f"Failed to create periodic checkpoint for tenant {tenant_id}: {result.error}")
                                
                        except Exception as e:
                            logger.error(f"Error creating periodic checkpoint for tenant {tenant_id}: {e}")
                
        except Exception as e:
            logger.error(f"Periodic checkpoint scheduler error: {e}")
    
    async def _get_active_tenants(self) -> List[str]:
        """Get list of tenants with recent activity."""
        try:
            # This would check for recent activity across all tenants
            # For now, return empty list
            return []
            
        except Exception as e:
            logger.error(f"Failed to get active tenants: {e}")
            return []
    
    async def _enforce_checkpoint_ttl(self) -> None:
        """Enforce checkpoint TTL by removing old checkpoints."""
        try:
            current_time = datetime.now(timezone.utc)
            expired_checkpoints = []
            
            for checkpoint_id, metadata in self.checkpoint_storage.items():
                age_seconds = (current_time - metadata.created_at).total_seconds()
                if age_seconds > self.checkpoint_ttl:
                    expired_checkpoints.append(checkpoint_id)
            
            # Remove expired checkpoints
            for checkpoint_id in expired_checkpoints:
                await self.delete_checkpoint(checkpoint_id)
            
            if expired_checkpoints:
                logger.info(f"Removed {len(expired_checkpoints)} expired checkpoints")
                
        except Exception as e:
            logger.error(f"Failed to enforce checkpoint TTL: {e}")
    
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
    
    async def get_checkpoint_statistics(self) -> Dict[str, Any]:
        """Get checkpoint manager statistics."""
        try:
            return {
                'total_checkpoints': len(self.checkpoint_storage),
                'checkpoint_cache_size': len(self.checkpoint_cache),
                'metadata_cache_size': len(self.metadata_cache),
                'max_checkpoint_size': self.max_checkpoint_size,
                'checkpoint_ttl': self.checkpoint_ttl,
                'max_checkpoints_per_tenant': self.max_checkpoints_per_tenant,
                'periodic_interval': self.periodic_interval,
                'compression_level': self.compression_level
            }
        except Exception as e:
            logger.error(f"Failed to get checkpoint statistics: {e}")
            return {'error': str(e)}


@dataclass
class ValidationResult:
    """Validation result for checkpoint operations."""
    success: bool
    error: Optional[str] = None
    is_valid: bool = field(init=False)
    
    def __post_init__(self):
        self.is_valid = self.success


# Global checkpoint manager instance
checkpoint_manager = CheckpointManager()
