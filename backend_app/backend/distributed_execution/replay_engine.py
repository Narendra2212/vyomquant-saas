"""
Replay Engine for Deterministic Event Replay

Provides deterministic replay capabilities for exact execution reconstruction,
point-in-time debugging, and replay-safe recovery. Operates in read-only
reconstruction mode to ensure no live execution impact.

Author: Principal Replay and Recovery Engineer
"""

import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .event_signing import event_signer
from .immutable_journal import EventType, ExecutionEvent, immutable_journal
from .sequence_manager import sequence_manager

logger = logging.getLogger("replay_engine")


@dataclass
class ReplayRequest:
    """Replay request with parameters."""
    replay_id: str
    tenant_id: str
    strategy_id: Optional[str] = None
    bot_id: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    start_sequence: Optional[int] = None
    end_sequence: Optional[int] = None
    event_types: Optional[List[EventType]] = None
    read_only: bool = True
    validate_consistency: bool = True
    validate_integrity: bool = True


@dataclass
class ReplayResult:
    """Result of replay operation."""
    replay_id: str
    success: bool
    events_processed: int
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    replay_state: Dict[str, Any]
    validation_results: Dict[str, Any]
    error: Optional[str] = None


@dataclass
class ReplayState:
    """Replay state for tracking progress."""
    replay_id: str
    current_sequence: int
    events_processed: int
    current_state: Dict[str, Any]
    validation_errors: List[str]
    processing_errors: List[str]
    start_time: datetime
    last_update: datetime


class ReplayMode(Enum):
    """Replay operation modes."""
    READ_ONLY = "read_only"
    VALIDATION = "validation"
    RECOVERY = "recovery"
    DEBUG = "debug"


class ReplayEngine:
    """Deterministic replay engine for event-based state reconstruction."""
    
    def __init__(self):
        self.immutable_journal = immutable_journal
        self.sequence_manager = sequence_manager
        self.event_signer = event_signer
        
        # Replay state management
        self.active_replays: Dict[str, ReplayState] = {}
        self.replay_history: List[ReplayResult] = []
        
        # Replay configuration
        self.max_events_per_batch = 1000
        self.validation_batch_size = 100
        self.state_update_interval = 100
        
        # Performance optimization
        self.event_cache: Dict[str, ExecutionEvent] = {}
        self.state_cache: Dict[str, Dict[str, Any]] = {}
        self.validation_cache: Dict[str, bool] = {}
        
        logger.info("Replay engine initialized")
    
    async def initialize(self) -> bool:
        """Initialize replay engine components."""
        try:
            # Initialize dependencies
            await self.immutable_journal.initialize()
            await self.sequence_manager.initialize()
            await self.event_signer.initialize()
            
            logger.info("Replay engine initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize replay engine: {e}")
            return False
    
    async def start_replay(self, request: ReplayRequest) -> ReplayResult:
        """Start deterministic replay operation."""
        try:
            replay_start = time.time()
            
            # Validate replay request
            validation_result = await self._validate_replay_request(request)
            if not validation_result.success:
                return ReplayResult(
                    replay_id=request.replay_id,
                    success=False,
                    events_processed=0,
                    start_time=datetime.now(timezone.utc),
                    end_time=datetime.now(timezone.utc),
                    duration_seconds=0,
                    replay_state={},
                    validation_results=validation_result.validation_results,
                    error=validation_result.error
                )
            
            # Initialize replay state
            replay_state = ReplayState(
                replay_id=request.replay_id,
                current_sequence=request.start_sequence or 0,
                events_processed=0,
                current_state={},
                validation_errors=[],
                processing_errors=[],
                start_time=datetime.now(timezone.utc),
                last_update=datetime.now(timezone.utc)
            )
            
            self.active_replays[request.replay_id] = replay_state
            
            try:
                # Execute replay
                await self._execute_replay(request, replay_state)
                
                # Finalize replay
                result = await self._finalize_replay(request, replay_state)
                
                return result
                
            except Exception as e:
                logger.error(f"Replay execution failed: {e}")
                replay_state.processing_errors.append(str(e))
                
                return ReplayResult(
                    replay_id=request.replay_id,
                    success=False,
                    events_processed=replay_state.events_processed,
                    start_time=replay_state.start_time,
                    end_time=datetime.now(timezone.utc),
                    duration_seconds=time.time() - replay_start,
                    replay_state=asdict(replay_state),
                    validation_results={},
                    error=str(e)
                )
                
        except Exception as e:
            logger.error(f"Failed to start replay: {e}")
            return ReplayResult(
                replay_id=request.replay_id,
                success=False,
                events_processed=0,
                start_time=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc),
                duration_seconds=0,
                replay_state={},
                validation_results={},
                error=str(e)
            )
    
    async def _validate_replay_request(self, request: ReplayRequest) -> 'ValidationResult':
        """Validate replay request parameters."""
        try:
            validation_results = {}
            
            # Validate time range
            if request.start_time and request.end_time:
                if request.start_time >= request.end_time:
                    return ValidationResult(
                        success=False,
                        error="Start time must be before end time"
                    )
                validation_results["time_range_valid"] = True
            
            # Validate sequence range
            if request.start_sequence and request.end_sequence:
                if request.start_sequence >= request.end_sequence:
                    return ValidationResult(
                        success=False,
                        error="Start sequence must be before end sequence"
                    )
                validation_results["sequence_range_valid"] = True
            
            # Validate tenant exists
            if not await self._validate_tenant_exists(request.tenant_id):
                return ValidationResult(
                    success=False,
                    error=f"Tenant {request.tenant_id} not found"
                )
            validation_results["tenant_valid"] = True
            
            # Validate strategy if specified
            if request.strategy_id:
                if not await self._validate_strategy_exists(request.tenant_id, request.strategy_id):
                    return ValidationResult(
                        success=False,
                        error=f"Strategy {request.strategy_id} not found"
                    )
                validation_results["strategy_valid"] = True
            
            # Validate bot if specified
            if request.bot_id:
                if not await self._validate_bot_exists(request.tenant_id, request.bot_id):
                    return ValidationResult(
                        success=False,
                        error=f"Bot {request.bot_id} not found"
                    )
                validation_results["bot_valid"] = True
            
            return ValidationResult(
                success=True,
                validation_results=validation_results
            )
            
        except Exception as e:
            logger.error(f"Replay request validation failed: {e}")
            return ValidationResult(
                success=False,
                error=str(e)
            )
    
    async def _execute_replay(self, request: ReplayRequest, replay_state: ReplayState) -> None:
        """Execute the actual replay operation."""
        try:
            logger.info(f"Starting replay {request.replay_id}")
            
            # Get events for replay
            events = await self._get_replay_events(request)
            total_events = len(events)
            
            logger.info(f"Retrieved {total_events} events for replay")
            
            # Process events in batches
            for batch_start in range(0, total_events, self.max_events_per_batch):
                batch_end = min(batch_start + self.max_events_per_batch, total_events)
                batch_events = events[batch_start:batch_end]
                
                await self._process_event_batch(request, replay_state, batch_events)
                
                # Update replay state
                replay_state.last_update = datetime.now(timezone.utc)
                
                # Progress logging
                if batch_end % (self.max_events_per_batch * 10) == 0 or batch_end == total_events:
                    progress = (batch_end / total_events) * 100
                    logger.info(f"Replay {request.replay_id} progress: {progress:.1f}% ({batch_end}/{total_events})")
            
            logger.info(f"Completed replay {request.replay_id}")
            
        except Exception as e:
            logger.error(f"Replay execution failed: {e}")
            raise
    
    async def _get_replay_events(self, request: ReplayRequest) -> List[ExecutionEvent]:
        """Get events for replay based on request parameters."""
        try:
            events = []
            
            # Determine query parameters
            if request.start_sequence and request.end_sequence:
                # Sequence-based query
                events = await self.immutable_journal.get_events_by_sequence_range(
                    tenant_id=request.tenant_id,
                    start_sequence=request.start_sequence,
                    end_sequence=request.end_sequence
                )
            elif request.start_time and request.end_time:
                # Time-based query
                events = await self.immutable_journal.get_events_by_time_range(
                    tenant_id=request.tenant_id,
                    start_time=request.start_time,
                    end_time=request.end_time
                )
            else:
                # Get all events for tenant
                events = await self.immutable_journal.get_events_by_tenant(
                    tenant_id=request.tenant_id
                )
            
            # Filter by strategy if specified
            if request.strategy_id:
                events = [e for e in events if e.header.strategy_id == request.strategy_id]
            
            # Filter by bot if specified
            if request.bot_id:
                events = [e for e in events if e.header.bot_id == request.bot_id]
            
            # Filter by event types if specified
            if request.event_types:
                events = [e for e in events if e.header.event_type in request.event_types]
            
            # Sort by sequence to ensure deterministic order
            events.sort(key=lambda e: e.header.sequence_id)
            
            return events
            
        except Exception as e:
            logger.error(f"Failed to get replay events: {e}")
            raise
    
    async def _process_event_batch(self, request: ReplayRequest, replay_state: ReplayState, 
                                 events: List[ExecutionEvent]) -> None:
        """Process a batch of events for replay."""
        try:
            for event in events:
                # Validate event integrity if required
                if request.validate_integrity:
                    integrity_result = await self._validate_event_integrity(event)
                    if not integrity_result.is_valid:
                        replay_state.validation_errors.append(
                            f"Event {event.header.event_id} integrity validation failed: {integrity_result.error}"
                        )
                        continue
                
                # Apply event to replay state
                await self._apply_event_to_state(event, replay_state)
                
                # Update replay state
                replay_state.current_sequence = event.header.sequence_id
                replay_state.events_processed += 1
                
                # Validate consistency if required
                if request.validate_consistency and replay_state.events_processed % self.validation_batch_size == 0:
                    consistency_result = await self._validate_state_consistency(replay_state.current_state)
                    if not consistency_result.is_valid:
                        replay_state.validation_errors.append(
                            f"State consistency validation failed at sequence {event.header.sequence_id}: {consistency_result.error}"
                        )
            
        except Exception as e:
            logger.error(f"Failed to process event batch: {e}")
            raise
    
    async def _validate_event_integrity(self, event: ExecutionEvent) -> 'ValidationResult':
        """Validate event integrity."""
        try:
            # Check event cache
            cache_key = f"{event.header.event_id}:{event.header.sequence_id}"
            if cache_key in self.validation_cache:
                return ValidationResult(success=True)
            
            # Verify event signature
            verification_result = await self.event_signer.verify_event(event)
            if not verification_result.is_valid:
                return ValidationResult(
                    success=False,
                    error=verification_result.error
                )
            
            # Cache validation result
            self.validation_cache[cache_key] = True
            
            return ValidationResult(success=True)
            
        except Exception as e:
            logger.error(f"Event integrity validation failed: {e}")
            return ValidationResult(
                success=False,
                error=str(e)
            )
    
    async def _apply_event_to_state(self, event: ExecutionEvent, replay_state: ReplayState) -> None:
        """Apply event to replay state in read-only mode."""
        try:
            # This is a read-only reconstruction - no live state mutation
            # We build the state in memory for analysis and validation
            
            event_type = event.header.event_type
            event.payload
            
            # Apply event based on type
            if event_type == EventType.ORDER_SUBMITTED:
                await self._apply_order_submitted_event(event, replay_state)
            elif event_type == EventType.ORDER_ACCEPTED:
                await self._apply_order_accepted_event(event, replay_state)
            elif event_type == EventType.ORDER_FILLED:
                await self._apply_order_filled_event(event, replay_state)
            elif event_type == EventType.ORDER_CANCELLED:
                await self._apply_order_cancelled_event(event, replay_state)
            elif event_type == EventType.POSITION_OPENED:
                await self._apply_position_opened_event(event, replay_state)
            elif event_type == EventType.POSITION_UPDATED:
                await self._apply_position_updated_event(event, replay_state)
            elif event_type == EventType.POSITION_CLOSED:
                await self._apply_position_closed_event(event, replay_state)
            elif event_type == EventType.RISK_VALIDATED:
                await self._apply_risk_validated_event(event, replay_state)
            elif event_type == EventType.SYSTEM_CHECKPOINT:
                await self._apply_system_checkpoint_event(event, replay_state)
            else:
                logger.debug(f"Unhandled event type: {event_type}")
            
        except Exception as e:
            logger.error(f"Failed to apply event to state: {e}")
            raise
    
    async def _apply_order_submitted_event(self, event: ExecutionEvent, replay_state: ReplayState) -> None:
        """Apply order submitted event to replay state."""
        try:
            payload = event.payload
            
            # Initialize order state if not exists
            order_id = payload.get('order_id')
            if order_id not in replay_state.current_state.get('orders', {}):
                if 'orders' not in replay_state.current_state:
                    replay_state.current_state['orders'] = {}
                
                replay_state.current_state['orders'][order_id] = {
                    'order_id': order_id,
                    'status': 'submitted',
                    'submitted_at': event.header.timestamp.isoformat(),
                    'tenant_id': event.header.tenant_id,
                    'strategy_id': event.header.strategy_id,
                    'bot_id': event.header.bot_id,
                    'signal_id': event.header.signal_id,
                    'exchange': payload.get('exchange'),
                    'symbol': payload.get('symbol'),
                    'side': payload.get('side'),
                    'order_type': payload.get('order_type'),
                    'quantity': payload.get('quantity'),
                    'price': payload.get('price'),
                    'sequence_id': event.header.sequence_id
                }
            
        except Exception as e:
            logger.error(f"Failed to apply order submitted event: {e}")
            raise
    
    async def _apply_order_accepted_event(self, event: ExecutionEvent, replay_state: ReplayState) -> None:
        """Apply order accepted event to replay state."""
        try:
            payload = event.payload
            order_id = payload.get('order_id')
            
            if 'orders' in replay_state.current_state and order_id in replay_state.current_state['orders']:
                replay_state.current_state['orders'][order_id].update({
                    'status': 'accepted',
                    'accepted_at': event.header.timestamp.isoformat(),
                    'exchange_order_id': payload.get('exchange_order_id'),
                    'sequence_id': event.header.sequence_id
                })
            
        except Exception as e:
            logger.error(f"Failed to apply order accepted event: {e}")
            raise
    
    async def _apply_order_filled_event(self, event: ExecutionEvent, replay_state: ReplayState) -> None:
        """Apply order filled event to replay state."""
        try:
            payload = event.payload
            order_id = payload.get('order_id')
            
            if 'orders' in replay_state.current_state and order_id in replay_state.current_state['orders']:
                order_state = replay_state.current_state['orders'][order_id]
                order_state.update({
                    'status': 'filled',
                    'filled_at': event.header.timestamp.isoformat(),
                    'executed_quantity': payload.get('executed_quantity'),
                    'execution_price': payload.get('execution_price'),
                    'fees': payload.get('fees'),
                    'sequence_id': event.header.sequence_id
                })
                
                # Update position state
                await self._update_position_from_fill(event, replay_state)
            
        except Exception as e:
            logger.error(f"Failed to apply order filled event: {e}")
            raise
    
    async def _apply_order_cancelled_event(self, event: ExecutionEvent, replay_state: ReplayState) -> None:
        """Apply order cancelled event to replay state."""
        try:
            payload = event.payload
            order_id = payload.get('order_id')
            
            if 'orders' in replay_state.current_state and order_id in replay_state.current_state['orders']:
                replay_state.current_state['orders'][order_id].update({
                    'status': 'cancelled',
                    'cancelled_at': event.header.timestamp.isoformat(),
                    'cancel_reason': payload.get('cancel_reason'),
                    'sequence_id': event.header.sequence_id
                })
            
        except Exception as e:
            logger.error(f"Failed to apply order cancelled event: {e}")
            raise
    
    async def _apply_position_opened_event(self, event: ExecutionEvent, replay_state: ReplayState) -> None:
        """Apply position opened event to replay state."""
        try:
            payload = event.payload
            
            # Initialize position state
            position_id = payload.get('position_id')
            if 'positions' not in replay_state.current_state:
                replay_state.current_state['positions'] = {}
            
            replay_state.current_state['positions'][position_id] = {
                'position_id': position_id,
                'status': 'open',
                'opened_at': event.header.timestamp.isoformat(),
                'tenant_id': event.header.tenant_id,
                'strategy_id': event.header.strategy_id,
                'bot_id': event.header.bot_id,
                'symbol': payload.get('symbol'),
                'side': payload.get('side'),
                'quantity': payload.get('quantity'),
                'entry_price': payload.get('entry_price'),
                'sequence_id': event.header.sequence_id
            }
            
        except Exception as e:
            logger.error(f"Failed to apply position opened event: {e}")
            raise
    
    async def _apply_position_updated_event(self, event: ExecutionEvent, replay_state: ReplayState) -> None:
        """Apply position updated event to replay state."""
        try:
            payload = event.payload
            position_id = payload.get('position_id')
            
            if 'positions' in replay_state.current_state and position_id in replay_state.current_state['positions']:
                replay_state.current_state['positions'][position_id].update({
                    'updated_at': event.header.timestamp.isoformat(),
                    'current_price': payload.get('current_price'),
                    'unrealized_pnl': payload.get('unrealized_pnl'),
                    'sequence_id': event.header.sequence_id
                })
            
        except Exception as e:
            logger.error(f"Failed to apply position updated event: {e}")
            raise
    
    async def _apply_position_closed_event(self, event: ExecutionEvent, replay_state: ReplayState) -> None:
        """Apply position closed event to replay state."""
        try:
            payload = event.payload
            position_id = payload.get('position_id')
            
            if 'positions' in replay_state.current_state and position_id in replay_state.current_state['positions']:
                position_state = replay_state.current_state['positions'][position_id]
                position_state.update({
                    'status': 'closed',
                    'closed_at': event.header.timestamp.isoformat(),
                    'exit_price': payload.get('exit_price'),
                    'realized_pnl': payload.get('realized_pnl'),
                    'sequence_id': event.header.sequence_id
                })
            
        except Exception as e:
            logger.error(f"Failed to apply position closed event: {e}")
            raise
    
    async def _apply_risk_validated_event(self, event: ExecutionEvent, replay_state: ReplayState) -> None:
        """Apply risk validated event to replay state."""
        try:
            payload = event.payload
            
            # Update risk state
            if 'risk' not in replay_state.current_state:
                replay_state.current_state['risk'] = {}
            
            replay_state.current_state['risk'].update({
                'validated_at': event.header.timestamp.isoformat(),
                'risk_score': payload.get('risk_score'),
                'position_size': payload.get('position_size'),
                'max_position_size': payload.get('max_position_size'),
                'sequence_id': event.header.sequence_id
            })
            
        except Exception as e:
            logger.error(f"Failed to apply risk validated event: {e}")
            raise
    
    async def _apply_system_checkpoint_event(self, event: ExecutionEvent, replay_state: ReplayState) -> None:
        """Apply system checkpoint event to replay state."""
        try:
            payload = event.payload
            
            # Update checkpoint state
            if 'checkpoints' not in replay_state.current_state:
                replay_state.current_state['checkpoints'] = {}
            
            checkpoint_id = payload.get('checkpoint_id')
            replay_state.current_state['checkpoints'][checkpoint_id] = {
                'checkpoint_id': checkpoint_id,
                'created_at': event.header.timestamp.isoformat(),
                'checkpoint_type': payload.get('checkpoint_type'),
                'state_snapshot': payload.get('state_snapshot'),
                'sequence_id': event.header.sequence_id
            }
            
        except Exception as e:
            logger.error(f"Failed to apply system checkpoint event: {e}")
            raise
    
    async def _update_position_from_fill(self, event: ExecutionEvent, replay_state: ReplayState) -> None:
        """Update position state from fill event."""
        try:
            payload = event.payload
            
            # Find related position
            symbol = payload.get('symbol')
            side = payload.get('side')
            executed_quantity = payload.get('executed_quantity')
            execution_price = payload.get('execution_price')
            
            # Look for open position
            if 'positions' in replay_state.current_state:
                for position_id, position_state in replay_state.current_state['positions'].items():
                    if (position_state['symbol'] == symbol and 
                        position_state['side'] == side and 
                        position_state['status'] == 'open'):
                        
                        # Update position with fill information
                        position_state['last_fill_at'] = event.header.timestamp.isoformat()
                        position_state['last_fill_quantity'] = executed_quantity
                        position_state['last_fill_price'] = execution_price
                        position_state['sequence_id'] = event.header.sequence_id
                        break
            
        except Exception as e:
            logger.error(f"Failed to update position from fill: {e}")
            raise
    
    async def _validate_state_consistency(self, state: Dict[str, Any]) -> 'ValidationResult':
        """Validate state consistency."""
        try:
            # Validate orders state
            if 'orders' in state:
                for order_id, order_state in state['orders'].items():
                    # Validate order state consistency
                    if 'status' not in order_state:
                        return ValidationResult(
                            success=False,
                            error=f"Order {order_id} missing status"
                        )
                    
                    # Validate sequence consistency
                    if 'sequence_id' not in order_state:
                        return ValidationResult(
                            success=False,
                            error=f"Order {order_id} missing sequence_id"
                        )
            
            # Validate positions state
            if 'positions' in state:
                for position_id, position_state in state['positions'].items():
                    # Validate position state consistency
                    if 'status' not in position_state:
                        return ValidationResult(
                            success=False,
                            error=f"Position {position_id} missing status"
                        )
                    
                    # Validate sequence consistency
                    if 'sequence_id' not in position_state:
                        return ValidationResult(
                            success=False,
                            error=f"Position {position_id} missing sequence_id"
                        )
            
            return ValidationResult(success=True)
            
        except Exception as e:
            logger.error(f"State consistency validation failed: {e}")
            return ValidationResult(
                success=False,
                error=str(e)
            )
    
    async def _finalize_replay(self, request: ReplayRequest, replay_state: ReplayState) -> ReplayResult:
        """Finalize replay operation and generate result."""
        try:
            end_time = datetime.now(timezone.utc)
            duration = (end_time - replay_state.start_time).total_seconds()
            
            # Final validation
            final_validation = {}
            if request.validate_consistency:
                final_validation['state_consistency'] = await self._validate_state_consistency(replay_state.current_state)
            
            # Create replay result
            result = ReplayResult(
                replay_id=request.replay_id,
                success=len(replay_state.validation_errors) == 0 and len(replay_state.processing_errors) == 0,
                events_processed=replay_state.events_processed,
                start_time=replay_state.start_time,
                end_time=end_time,
                duration_seconds=duration,
                replay_state=asdict(replay_state),
                validation_results=final_validation,
                error=None
            )
            
            # Add to history
            self.replay_history.append(result)
            
            # Clean up active replay
            if request.replay_id in self.active_replays:
                del self.active_replays[request.replay_id]
            
            # Log completion
            logger.info(f"Replay {request.replay_id} completed: {result.events_processed} events in {duration:.2f}s")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to finalize replay: {e}")
            raise
    
    async def get_replay_status(self, replay_id: str) -> Optional[ReplayState]:
        """Get current status of active replay."""
        try:
            return self.active_replays.get(replay_id)
        except Exception as e:
            logger.error(f"Failed to get replay status: {e}")
            return None
    
    async def get_replay_history(self, tenant_id: Optional[str] = None, 
                                 limit: int = 100) -> List[ReplayResult]:
        """Get replay history."""
        try:
            history = self.replay_history
            
            # Filter by tenant if specified
            if tenant_id:
                # Note: This would require tenant_id in ReplayResult
                # For now, return all history
                pass
            
            # Return most recent results
            return history[-limit:] if len(history) > limit else history
            
        except Exception as e:
            logger.error(f"Failed to get replay history: {e}")
            return []
    
    async def cancel_replay(self, replay_id: str) -> bool:
        """Cancel active replay."""
        try:
            if replay_id in self.active_replays:
                del self.active_replays[replay_id]
                logger.info(f"Cancelled replay {replay_id}")
                return True
            else:
                logger.warning(f"Replay {replay_id} not found in active replays")
                return False
                
        except Exception as e:
            logger.error(f"Failed to cancel replay: {e}")
            return False
    
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
    
    async def get_replay_statistics(self) -> Dict[str, Any]:
        """Get replay engine statistics."""
        try:
            return {
                'active_replays': len(self.active_replays),
                'total_replays': len(self.replay_history),
                'event_cache_size': len(self.event_cache),
                'state_cache_size': len(self.state_cache),
                'validation_cache_size': len(self.validation_cache),
                'max_events_per_batch': self.max_events_per_batch,
                'validation_batch_size': self.validation_batch_size,
                'state_update_interval': self.state_update_interval
            }
        except Exception as e:
            logger.error(f"Failed to get replay statistics: {e}")
            return {'error': str(e)}


@dataclass
class ValidationResult:
    """Validation result for replay operations."""
    success: bool
    validation_results: Dict[str, Any] = None
    error: Optional[str] = None
    is_valid: bool = None(init=False)
    
    def __post_init__(self):
        self.is_valid = self.success


# Global replay engine instance
replay_engine = ReplayEngine()
