"""
Replay Validator for Multi-Layered Validation

Provides comprehensive validation for replay operations with multi-layered
validation including event validation, state validation, and output validation.
Ensures correctness, completeness, and consistency of replay operations.

Author: Principal Replay and Recovery Engineer
"""

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .event_signing import event_signer
from .immutable_journal import EventType, ExecutionEvent, immutable_journal
from .sequence_manager import sequence_manager

logger = logging.getLogger("replay_validator")


@dataclass
class ValidationRequest:
    """Validation request for replay operations."""
    validation_id: str
    replay_id: str
    tenant_id: str
    strategy_id: Optional[str] = None
    bot_id: Optional[str] = None
    validation_layers: List['ValidationLayer'] = None
    validation_rules: List[str] = None
    strict_mode: bool = False
    include_performance_validation: bool = True


@dataclass
class ValidationResult:
    """Result of validation operation."""
    validation_id: str
    replay_id: str
    success: bool
    validation_layers: List['ValidationLayer']
    validation_summary: Dict[str, Any]
    validation_details: Dict[str, Any]
    performance_metrics: Dict[str, Any]
    validation_time: datetime
    duration_seconds: float
    error: Optional[str] = None


@dataclass
class LayerValidationResult:
    """Result of individual validation layer."""
    layer: 'ValidationLayer'
    success: bool
    score: float  # 0.0 to 1.0
    issues: List[str]
    warnings: List[str]
    metrics: Dict[str, Any]
    details: Dict[str, Any]


class ValidationLayer(Enum):
    """Validation layers for replay operations."""
    EVENT_INTEGRITY = "event_integrity"
    EVENT_SEQUENCE = "event_sequence"
    EVENT_CAUSALITY = "event_causality"
    EVENT_COMPLETENESS = "event_completeness"
    STATE_CONSISTENCY = "state_consistency"
    STATE_INTEGRITY = "state_integrity"
    STATE_COMPLETENESS = "state_completeness"
    OUTPUT_CONSISTENCY = "output_consistency"
    OUTPUT_INTEGRITY = "output_integrity"
    OUTPUT_COMPLETENESS = "output_completeness"
    TEMPORAL_CONSISTENCY = "temporal_consistency"
    LOGICAL_CONSISTENCY = "logical_consistency"
    PERFORMANCE_CONSISTENCY = "performance_consistency"


class ValidationSeverity(Enum):
    """Validation issue severity."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class ValidationIssue:
    """Validation issue with severity and details."""
    severity: ValidationSeverity
    layer: ValidationLayer
    component: str
    message: str
    details: Dict[str, Any]
    timestamp: datetime


class ReplayValidator:
    """Comprehensive replay validator for multi-layered validation."""
    
    def __init__(self):
        self.immutable_journal = immutable_journal
        self.sequence_manager = sequence_manager
        self.event_signer = event_signer
        
        # Validation state
        self.active_validations: Dict[str, Dict[str, Any]] = {}
        self.validation_history: List[ValidationResult] = []
        
        # Validation configuration
        self.validation_timeout = 300  # 5 minutes
        self.max_events_per_validation = 10000
        self.validation_batch_size = 100
        
        # Performance optimization
        self.validation_cache: Dict[str, LayerValidationResult] = {}
        self.rule_cache: Dict[str, Any] = {}
        
        # Validation rules
        self.validation_rules = {}
        
        logger.info("Replay validator initialized")
    
    async def initialize(self) -> bool:
        """Initialize replay validator components."""
        try:
            # Initialize dependencies
            await self.immutable_journal.initialize()
            await self.sequence_manager.initialize()
            await self.event_signer.initialize()
            
            # Initialize validation rules
            self.validation_rules = await self._initialize_validation_rules()
            
            logger.info("Replay validator initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize replay validator: {e}")
            return False
    
    async def validate_replay(self, request: ValidationRequest) -> ValidationResult:
        """Perform comprehensive replay validation."""
        try:
            validation_start = time.time()
            
            # Initialize validation state
            validation_state = {
                'validation_id': request.validation_id,
                'replay_id': request.replay_id,
                'tenant_id': request.tenant_id,
                'strategy_id': request.strategy_id,
                'bot_id': request.bot_id,
                'start_time': datetime.now(timezone.utc),
                'layers': request.validation_layers or list(ValidationLayer),
                'issues': [],
                'layer_results': {},
                'performance_metrics': {}
            }
            
            self.active_validations[request.validation_id] = validation_state
            
            try:
                # Perform validation layers
                layer_results = await self._perform_validation_layers(request, validation_state)
                
                # Calculate validation summary
                validation_summary = await self._calculate_validation_summary(layer_results)
                
                # Calculate performance metrics
                performance_metrics = await self._calculate_performance_metrics(validation_state)
                
                # Create validation result
                result = ValidationResult(
                    validation_id=request.validation_id,
                    replay_id=request.replay_id,
                    success=validation_summary['overall_success'],
                    validation_layers=request.validation_layers or list(ValidationLayer),
                    validation_summary=validation_summary,
                    validation_details={
                        'layer_results': {layer.layer.value: asdict(layer) for layer in layer_results},
                        'issues': validation_state['issues']
                    },
                    performance_metrics=performance_metrics,
                    validation_time=datetime.now(timezone.utc),
                    duration_seconds=time.time() - validation_start
                )
                
                # Add to history
                self.validation_history.append(result)
                
                # Clean up active validation
                if request.validation_id in self.active_validations:
                    del self.active_validations[request.validation_id]
                
                # Log completion
                logger.info(f"Validation {request.validation_id} completed: {result.success}")
                
                return result
                
            except Exception as e:
                logger.error(f"Validation execution failed: {e}")
                
                return ValidationResult(
                    validation_id=request.validation_id,
                    replay_id=request.replay_id,
                    success=False,
                    validation_layers=request.validation_layers or list(ValidationLayer),
                    validation_summary={'overall_success': False, 'error': str(e)},
                    validation_details={'error': str(e)},
                    performance_metrics={},
                    validation_time=datetime.now(timezone.utc),
                    duration_seconds=time.time() - validation_start,
                    error=str(e)
                )
                
        except Exception as e:
            logger.error(f"Failed to validate replay: {e}")
            return ValidationResult(
                validation_id=request.validation_id,
                replay_id=request.replay_id,
                success=False,
                validation_layers=[],
                validation_summary={'overall_success': False, 'error': str(e)},
                validation_details={'error': str(e)},
                performance_metrics={},
                validation_time=datetime.now(timezone.utc),
                duration_seconds=0,
                error=str(e)
            )
    
    async def _perform_validation_layers(self, request: ValidationRequest, validation_state: Dict[str, Any]) -> List[LayerValidationResult]:
        """Perform all validation layers."""
        try:
            layer_results = []
            
            for layer in validation_state['layers']:
                layer_start = time.time()
                
                try:
                    # Perform layer validation
                    layer_result = await self._perform_layer_validation(layer, request, validation_state)
                    
                    # Add performance metrics
                    layer_result.metrics['validation_duration'] = time.time() - layer_start
                    
                    layer_results.append(layer_result)
                    validation_state['layer_results'][layer.value] = layer_result
                    
                    # Log layer completion
                    logger.debug(f"Validation layer {layer.value} completed: score={layer_result.score}")
                    
                except Exception as e:
                    logger.error(f"Validation layer {layer.value} failed: {e}")
                    
                    # Create failed result
                    failed_result = LayerValidationResult(
                        layer=layer,
                        success=False,
                        score=0.0,
                        issues=[f"Layer validation failed: {str(e)}"],
                        warnings=[],
                        metrics={'validation_duration': time.time() - layer_start},
                        details={'error': str(e)}
                    )
                    
                    layer_results.append(failed_result)
                    validation_state['layer_results'][layer.value] = failed_result
            
            return layer_results
            
        except Exception as e:
            logger.error(f"Failed to perform validation layers: {e}")
            raise
    
    async def _perform_layer_validation(self, layer: ValidationLayer, request: ValidationRequest, validation_state: Dict[str, Any]) -> LayerValidationResult:
        """Perform individual validation layer."""
        try:
            # Check cache first
            cache_key = f"{layer.value}:{request.replay_id}"
            if cache_key in self.validation_cache:
                cached_result = self.validation_cache[cache_key]
                logger.debug(f"Using cached validation result for layer {layer.value}")
                return cached_result
            
            # Perform layer-specific validation
            if layer == ValidationLayer.EVENT_INTEGRITY:
                result = await self._validate_event_integrity(request, validation_state)
            elif layer == ValidationLayer.EVENT_SEQUENCE:
                result = await self._validate_event_sequence(request, validation_state)
            elif layer == ValidationLayer.EVENT_CAUSALITY:
                result = await self._validate_event_causality(request, validation_state)
            elif layer == ValidationLayer.EVENT_COMPLETENESS:
                result = await self._validate_event_completeness(request, validation_state)
            elif layer == ValidationLayer.STATE_CONSISTENCY:
                result = await self._validate_state_consistency(request, validation_state)
            elif layer == ValidationLayer.STATE_INTEGRITY:
                result = await self._validate_state_integrity(request, validation_state)
            elif layer == ValidationLayer.STATE_COMPLETENESS:
                result = await self._validate_state_completeness(request, validation_state)
            elif layer == ValidationLayer.OUTPUT_CONSISTENCY:
                result = await self._validate_output_consistency(request, validation_state)
            elif layer == ValidationLayer.OUTPUT_INTEGRITY:
                result = await self._validate_output_integrity(request, validation_state)
            elif layer == ValidationLayer.OUTPUT_COMPLETENESS:
                result = await self._validate_output_completeness(request, validation_state)
            elif layer == ValidationLayer.TEMPORAL_CONSISTENCY:
                result = await self._validate_temporal_consistency(request, validation_state)
            elif layer == ValidationLayer.LOGICAL_CONSISTENCY:
                result = await self._validate_logical_consistency(request, validation_state)
            elif layer == ValidationLayer.PERFORMANCE_CONSISTENCY:
                result = await self._validate_performance_consistency(request, validation_state)
            else:
                raise ValueError(f"Unknown validation layer: {layer}")
            
            # Cache result
            self.validation_cache[cache_key] = result
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to perform layer validation {layer.value}: {e}")
            raise
    
    async def _validate_event_integrity(self, request: ValidationRequest, validation_state: Dict[str, Any]) -> LayerValidationResult:
        """Validate event integrity."""
        try:
            issues = []
            warnings = []
            metrics = {}
            details = {}
            
            # Get replay events
            events = await self._get_replay_events(request)
            
            # Validate event hashes
            hash_validations = 0
            hash_failures = 0
            
            for event in events:
                hash_validations += 1
                
                # Verify event signature
                verification_result = await self.event_signer.verify_event(event)
                if not verification_result.is_valid:
                    hash_failures += 1
                    issues.append(f"Event {event.header.event_id} signature validation failed: {verification_result.error}")
            
            # Calculate metrics
            metrics['total_events'] = len(events)
            metrics['hash_validations'] = hash_validations
            metrics['hash_failures'] = hash_failures
            metrics['hash_success_rate'] = (hash_validations - hash_failures) / hash_validations if hash_validations > 0 else 1.0
            
            # Calculate score
            score = metrics['hash_success_rate']
            
            # Add warnings for low success rate
            if score < 0.95:
                warnings.append(f"Low hash success rate: {score:.2%}")
            
            details['validation_method'] = 'signature_verification'
            details['validation_rules'] = ['event_signature_valid', 'hash_chain_integrity']
            
            return LayerValidationResult(
                layer=ValidationLayer.EVENT_INTEGRITY,
                success=score >= 0.95,
                score=score,
                issues=issues,
                warnings=warnings,
                metrics=metrics,
                details=details
            )
            
        except Exception as e:
            logger.error(f"Event integrity validation failed: {e}")
            raise
    
    async def _validate_event_sequence(self, request: ValidationRequest, validation_state: Dict[str, Any]) -> LayerValidationResult:
        """Validate event sequence."""
        try:
            issues = []
            warnings = []
            metrics = {}
            details = {}
            
            # Get replay events
            events = await self._get_replay_events(request)
            
            # Validate sequence numbers
            sequence_gaps = []
            sequence_duplicates = []
            expected_sequence = events[0].header.sequence_id if events else 0
            
            for i, event in enumerate(events):
                actual_sequence = event.header.sequence_id
                
                # Check for gaps
                if actual_sequence != expected_sequence:
                    sequence_gaps.append((expected_sequence, actual_sequence))
                
                # Check for duplicates
                if i > 0 and events[i-1].header.sequence_id == actual_sequence:
                    sequence_duplicates.append(actual_sequence)
                
                expected_sequence = actual_sequence + 1
            
            # Calculate metrics
            metrics['total_events'] = len(events)
            metrics['sequence_gaps'] = len(sequence_gaps)
            metrics['sequence_duplicates'] = len(sequence_duplicates)
            metrics['sequence_consistency'] = 1.0 - (len(sequence_gaps) + len(sequence_duplicates)) / len(events) if events else 1.0
            
            # Calculate score
            score = metrics['sequence_consistency']
            
            # Add issues for sequence problems
            for gap_start, gap_end in sequence_gaps:
                issues.append(f"Sequence gap detected: {gap_start} to {gap_end}")
            
            for duplicate in sequence_duplicates:
                issues.append(f"Duplicate sequence detected: {duplicate}")
            
            # Add warnings for minor issues
            if score < 0.99:
                warnings.append(f"Sequence consistency below 99%: {score:.2%}")
            
            details['validation_method'] = 'sequence_analysis'
            details['validation_rules'] = ['monotonic_sequence', 'no_sequence_gaps', 'unique_sequence']
            
            return LayerValidationResult(
                layer=ValidationLayer.EVENT_SEQUENCE,
                success=score >= 0.95,
                score=score,
                issues=issues,
                warnings=warnings,
                metrics=metrics,
                details=details
            )
            
        except Exception as e:
            logger.error(f"Event sequence validation failed: {e}")
            raise
    
    async def _validate_event_causality(self, request: ValidationRequest, validation_state: Dict[str, Any]) -> LayerValidationResult:
        """Validate event causality."""
        try:
            issues = []
            warnings = []
            metrics = {}
            details = {}
            
            # Get replay events
            events = await self._get_replay_events(request)
            
            # Validate causality relationships
            causality_violations = []
            causality_orphans = []
            
            # Build causality map
            causality_map = {}
            for event in events:
                event_id = event.header.event_id
                causality_map[event_id] = {
                    'causation_id': event.header.causation_id,
                    'correlation_id': event.header.correlation_id,
                    'timestamp': event.header.timestamp,
                    'sequence_id': event.header.sequence_id
                }
            
            # Check causality consistency
            for event_id, causality_info in causality_map.items():
                causation_id = causality_info['causation_id']
                
                if causation_id and causation_id not in causality_map:
                    causality_orphans.append(causation_id)
                
                if causation_id:
                    parent_info = causality_map.get(causation_id)
                    if parent_info:
                        # Check temporal consistency
                        if causality_info['timestamp'] < parent_info['timestamp']:
                            causality_violations.append((causation_id, event_id))
                        
                        # Check sequence consistency
                        if causality_info['sequence_id'] <= parent_info['sequence_id']:
                            causality_violations.append((causation_id, event_id))
            
            # Calculate metrics
            metrics['total_events'] = len(events)
            metrics['causality_violations'] = len(causality_violations)
            metrics['causality_orphans'] = len(causality_orphans)
            metrics['causality_consistency'] = 1.0 - len(causality_violations) / len(events) if events else 1.0
            
            # Calculate score
            score = metrics['causality_consistency']
            
            # Add issues for causality problems
            for parent_id, child_id in causality_violations:
                issues.append(f"Causality violation: {parent_id} -> {child_id}")
            
            for orphan_id in causality_orphans:
                warnings.append(f"Orphan causation reference: {orphan_id}")
            
            details['validation_method'] = 'causality_analysis'
            details['validation_rules'] = ['temporal_causality', 'sequence_causality', 'causation_integrity']
            
            return LayerValidationResult(
                layer=ValidationLayer.EVENT_CAUSALITY,
                success=score >= 0.95,
                score=score,
                issues=issues,
                warnings=warnings,
                metrics=metrics,
                details=details
            )
            
        except Exception as e:
            logger.error(f"Event causality validation failed: {e}")
            raise
    
    async def _validate_event_completeness(self, request: ValidationRequest, validation_state: Dict[str, Any]) -> LayerValidationResult:
        """Validate event completeness."""
        try:
            issues = []
            warnings = []
            metrics = {}
            details = {}
            
            # Get replay events
            events = await self._get_replay_events(request)
            
            # Validate event completeness
            missing_required_fields = []
            invalid_event_types = []
            
            required_fields = ['event_id', 'event_type', 'sequence_id', 'timestamp', 'tenant_id']
            
            for event in events:
                # Check required fields
                for field in required_fields:
                    if not hasattr(event.header, field) or getattr(event.header, field) is None:
                        missing_required_fields.append(f"{event.header.event_id}:{field}")
                
                # Check event type validity
                if event.header.event_type not in EventType:
                    invalid_event_types.append(f"{event.header.event_id}:{event.header.event_type}")
            
            # Calculate metrics
            metrics['total_events'] = len(events)
            metrics['missing_fields'] = len(missing_required_fields)
            metrics['invalid_types'] = len(invalid_event_types)
            metrics['completeness_score'] = 1.0 - (len(missing_required_fields) + len(invalid_event_types)) / (len(events) * len(required_fields)) if events else 1.0
            
            # Calculate score
            score = metrics['completeness_score']
            
            # Add issues for completeness problems
            for missing_field in missing_required_fields:
                issues.append(f"Missing required field: {missing_field}")
            
            for invalid_type in invalid_event_types:
                issues.append(f"Invalid event type: {invalid_type}")
            
            details['validation_method'] = 'field_validation'
            details['validation_rules'] = ['required_fields_present', 'valid_event_types', 'complete_payloads']
            
            return LayerValidationResult(
                layer=ValidationLayer.EVENT_COMPLETENESS,
                success=score >= 0.95,
                score=score,
                issues=issues,
                warnings=warnings,
                metrics=metrics,
                details=details
            )
            
        except Exception as e:
            logger.error(f"Event completeness validation failed: {e}")
            raise
    
    async def _validate_state_consistency(self, request: ValidationRequest, validation_state: Dict[str, Any]) -> LayerValidationResult:
        """Validate state consistency."""
        try:
            issues = []
            warnings = []
            metrics = {}
            details = {}
            
            # Get replay state
            replay_state = await self._get_replay_state(request)
            
            # Validate state invariants
            invariant_violations = []
            constraint_violations = []
            
            # Check order state consistency
            if 'orders' in replay_state:
                for order_id, order_state in replay_state['orders'].items():
                    # Validate order state invariants
                    if 'status' not in order_state:
                        invariant_violations.append(f"Order {order_id} missing status")
                    
                    # Validate order state constraints
                    if order_state.get('status') == 'filled' and 'executed_quantity' not in order_state:
                        constraint_violations.append(f"Filled order {order_id} missing executed_quantity")
            
            # Check position state consistency
            if 'positions' in replay_state:
                for position_id, position_state in replay_state['positions'].items():
                    # Validate position state invariants
                    if 'status' not in position_state:
                        invariant_violations.append(f"Position {position_id} missing status")
                    
                    # Validate position state constraints
                    if position_state.get('status') == 'open' and 'quantity' not in position_state:
                        constraint_violations.append(f"Open position {position_id} missing quantity")
            
            # Calculate metrics
            total_components = len(replay_state.get('orders', {})) + len(replay_state.get('positions', {}))
            metrics['total_components'] = total_components
            metrics['invariant_violations'] = len(invariant_violations)
            metrics['constraint_violations'] = len(constraint_violations)
            metrics['state_consistency'] = 1.0 - (len(invariant_violations) + len(constraint_violations)) / max(total_components, 1)
            
            # Calculate score
            score = metrics['state_consistency']
            
            # Add issues for state problems
            for violation in invariant_violations:
                issues.append(f"State invariant violation: {violation}")
            
            for violation in constraint_violations:
                issues.append(f"State constraint violation: {violation}")
            
            details['validation_method'] = 'state_analysis'
            details['validation_rules'] = ['state_invariants', 'state_constraints', 'business_rules']
            
            return LayerValidationResult(
                layer=ValidationLayer.STATE_CONSISTENCY,
                success=score >= 0.95,
                score=score,
                issues=issues,
                warnings=warnings,
                metrics=metrics,
                details=details
            )
            
        except Exception as e:
            logger.error(f"State consistency validation failed: {e}")
            raise
    
    async def _validate_state_integrity(self, request: ValidationRequest, validation_state: Dict[str, Any]) -> LayerValidationResult:
        """Validate state integrity."""
        try:
            issues = []
            warnings = []
            metrics = {}
            details = {}
            
            # Get replay state
            replay_state = await self._get_replay_state(request)
            
            # Validate data integrity
            data_type_errors = []
            data_format_errors = []
            reference_errors = []
            
            # Check order data integrity
            if 'orders' in replay_state:
                for order_id, order_state in replay_state['orders'].items():
                    # Validate data types
                    if 'quantity' in order_state and not isinstance(order_state['quantity'], (int, float)):
                        data_type_errors.append(f"Order {order_id} quantity has invalid type")
                    
                    if 'price' in order_state and not isinstance(order_state['price'], (int, float)):
                        data_type_errors.append(f"Order {order_id} price has invalid type")
                    
                    # Validate data formats
                    if 'status' in order_state and order_state['status'] not in ['submitted', 'accepted', 'filled', 'cancelled']:
                        data_format_errors.append(f"Order {order_id} has invalid status: {order_state['status']}")
            
            # Check position data integrity
            if 'positions' in replay_state:
                for position_id, position_state in replay_state['positions'].items():
                    # Validate data types
                    if 'quantity' in position_state and not isinstance(position_state['quantity'], (int, float)):
                        data_type_errors.append(f"Position {position_id} quantity has invalid type")
                    
                    # Validate data formats
                    if 'status' in position_state and position_state['status'] not in ['open', 'closed']:
                        data_format_errors.append(f"Position {position_id} has invalid status: {position_state['status']}")
            
            # Calculate metrics
            total_components = len(replay_state.get('orders', {})) + len(replay_state.get('positions', {}))
            metrics['total_components'] = total_components
            metrics['data_type_errors'] = len(data_type_errors)
            metrics['data_format_errors'] = len(data_format_errors)
            metrics['reference_errors'] = len(reference_errors)
            metrics['data_integrity'] = 1.0 - (len(data_type_errors) + len(data_format_errors) + len(reference_errors)) / max(total_components, 1)
            
            # Calculate score
            score = metrics['data_integrity']
            
            # Add issues for integrity problems
            for error in data_type_errors:
                issues.append(f"Data type error: {error}")
            
            for error in data_format_errors:
                issues.append(f"Data format error: {error}")
            
            for error in reference_errors:
                issues.append(f"Reference error: {error}")
            
            details['validation_method'] = 'data_integrity_check'
            details['validation_rules'] = ['data_type_validation', 'data_format_validation', 'reference_integrity']
            
            return LayerValidationResult(
                layer=ValidationLayer.STATE_INTEGRITY,
                success=score >= 0.95,
                score=score,
                issues=issues,
                warnings=warnings,
                metrics=metrics,
                details=details
            )
            
        except Exception as e:
            logger.error(f"State integrity validation failed: {e}")
            raise
    
    async def _validate_state_completeness(self, request: ValidationRequest, validation_state: Dict[str, Any]) -> LayerValidationResult:
        """Validate state completeness."""
        try:
            issues = []
            warnings = []
            metrics = {}
            details = {}
            
            # Get replay state
            replay_state = await self._get_replay_state(request)
            
            # Validate state completeness
            missing_required_data = []
            incomplete_relationships = []
            
            # Check order state completeness
            if 'orders' in replay_state:
                for order_id, order_state in replay_state['orders'].items():
                    required_fields = ['status', 'tenant_id', 'strategy_id', 'bot_id', 'symbol', 'side']
                    
                    for field in required_fields:
                        if field not in order_state:
                            missing_required_data.append(f"Order {order_id} missing {field}")
            
            # Check position state completeness
            if 'positions' in replay_state:
                for position_id, position_state in replay_state['positions'].items():
                    required_fields = ['status', 'tenant_id', 'strategy_id', 'bot_id', 'symbol', 'side']
                    
                    for field in required_fields:
                        if field not in position_state:
                            missing_required_data.append(f"Position {position_id} missing {field}")
            
            # Calculate metrics
            total_components = len(replay_state.get('orders', {})) + len(replay_state.get('positions', {}))
            metrics['total_components'] = total_components
            metrics['missing_required_data'] = len(missing_required_data)
            metrics['incomplete_relationships'] = len(incomplete_relationships)
            metrics['state_completeness'] = 1.0 - (len(missing_required_data) + len(incomplete_relationships)) / max(total_components, 1)
            
            # Calculate score
            score = metrics['state_completeness']
            
            # Add issues for completeness problems
            for missing in missing_required_data:
                issues.append(f"Missing required data: {missing}")
            
            for incomplete in incomplete_relationships:
                issues.append(f"Incomplete relationship: {incomplete}")
            
            details['validation_method'] = 'completeness_check'
            details['validation_rules'] = ['required_data_present', 'complete_relationships', 'full_state_coverage']
            
            return LayerValidationResult(
                layer=ValidationLayer.STATE_COMPLETENESS,
                success=score >= 0.95,
                score=score,
                issues=issues,
                warnings=warnings,
                metrics=metrics,
                details=details
            )
            
        except Exception as e:
            logger.error(f"State completeness validation failed: {e}")
            raise
    
    async def _validate_output_consistency(self, request: ValidationRequest, validation_state: Dict[str, Any]) -> LayerValidationResult:
        """Validate output consistency."""
        try:
            issues = []
            warnings = []
            metrics = {}
            details = {}
            
            # Get replay outputs
            replay_outputs = await self._get_replay_outputs(request)
            
            # Validate output consistency
            format_inconsistencies = []
            content_inconsistencies = []
            
            # Check output formats
            for output_id, output_data in replay_outputs.items():
                # Validate output schema
                if 'schema' in output_data:
                    schema = output_data['schema']
                    data = output_data.get('data', {})
                    
                    # Basic schema validation
                    for field, field_type in schema.items():
                        if field in data:
                            if field_type == 'string' and not isinstance(data[field], str):
                                format_inconsistencies.append(f"Output {output_id} field {field} type mismatch")
                            elif field_type == 'number' and not isinstance(data[field], (int, float)):
                                format_inconsistencies.append(f"Output {output_id} field {field} type mismatch")
            
            # Calculate metrics
            total_outputs = len(replay_outputs)
            metrics['total_outputs'] = total_outputs
            metrics['format_inconsistencies'] = len(format_inconsistencies)
            metrics['content_inconsistencies'] = len(content_inconsistencies)
            metrics['output_consistency'] = 1.0 - (len(format_inconsistencies) + len(content_inconsistencies)) / max(total_outputs, 1)
            
            # Calculate score
            score = metrics['output_consistency']
            
            # Add issues for consistency problems
            for inconsistency in format_inconsistencies:
                issues.append(f"Format inconsistency: {inconsistency}")
            
            for inconsistency in content_inconsistencies:
                issues.append(f"Content inconsistency: {inconsistency}")
            
            details['validation_method'] = 'output_analysis'
            details['validation_rules'] = ['output_schema_consistency', 'output_content_consistency']
            
            return LayerValidationResult(
                layer=ValidationLayer.OUTPUT_CONSISTENCY,
                success=score >= 0.95,
                score=score,
                issues=issues,
                warnings=warnings,
                metrics=metrics,
                details=details
            )
            
        except Exception as e:
            logger.error(f"Output consistency validation failed: {e}")
            raise
    
    async def _validate_output_integrity(self, request: ValidationRequest, validation_state: Dict[str, Any]) -> LayerValidationResult:
        """Validate output integrity."""
        try:
            issues = []
            warnings = []
            metrics = {}
            details = {}
            
            # Get replay outputs
            replay_outputs = await self._get_replay_outputs(request)
            
            # Validate output integrity
            data_corruption = []
            checksum_failures = []
            
            # Check output data integrity
            for output_id, output_data in replay_outputs.items():
                # Calculate checksum
                data_str = json.dumps(output_data.get('data', {}), sort_keys=True)
                calculated_checksum = hash(data_str)
                
                # Compare with stored checksum if available
                stored_checksum = output_data.get('checksum')
                if stored_checksum and calculated_checksum != stored_checksum:
                    checksum_failures.append(f"Output {output_id} checksum mismatch")
            
            # Calculate metrics
            total_outputs = len(replay_outputs)
            metrics['total_outputs'] = total_outputs
            metrics['data_corruption'] = len(data_corruption)
            metrics['checksum_failures'] = len(checksum_failures)
            metrics['output_integrity'] = 1.0 - (len(data_corruption) + len(checksum_failures)) / max(total_outputs, 1)
            
            # Calculate score
            score = metrics['output_integrity']
            
            # Add issues for integrity problems
            for corruption in data_corruption:
                issues.append(f"Data corruption: {corruption}")
            
            for failure in checksum_failures:
                issues.append(f"Checksum failure: {failure}")
            
            details['validation_method'] = 'integrity_check'
            details['validation_rules'] = ['data_integrity', 'checksum_validation']
            
            return LayerValidationResult(
                layer=ValidationLayer.OUTPUT_INTEGRITY,
                success=score >= 0.95,
                score=score,
                issues=issues,
                warnings=warnings,
                metrics=metrics,
                details=details
            )
            
        except Exception as e:
            logger.error(f"Output integrity validation failed: {e}")
            raise
    
    async def _validate_output_completeness(self, request: ValidationRequest, validation_state: Dict[str, Any]) -> LayerValidationResult:
        """Validate output completeness."""
        try:
            issues = []
            warnings = []
            metrics = {}
            details = {}
            
            # Get replay outputs
            replay_outputs = await self._get_replay_outputs(request)
            
            # Validate output completeness
            missing_outputs = []
            incomplete_outputs = []
            
            # Expected outputs based on events
            expected_outputs = await self._get_expected_outputs(request)
            
            # Check for missing outputs
            for expected_output in expected_outputs:
                if expected_output not in replay_outputs:
                    missing_outputs.append(expected_output)
            
            # Check for incomplete outputs
            for output_id, output_data in replay_outputs.items():
                if 'data' not in output_data or not output_data['data']:
                    incomplete_outputs.append(output_id)
            
            # Calculate metrics
            total_expected = len(expected_outputs)
            total_actual = len(replay_outputs)
            metrics['expected_outputs'] = total_expected
            metrics['actual_outputs'] = total_actual
            metrics['missing_outputs'] = len(missing_outputs)
            metrics['incomplete_outputs'] = len(incomplete_outputs)
            metrics['output_completeness'] = total_actual / max(total_expected, 1)
            
            # Calculate score
            score = metrics['output_completeness']
            
            # Add issues for completeness problems
            for missing in missing_outputs:
                issues.append(f"Missing output: {missing}")
            
            for incomplete in incomplete_outputs:
                issues.append(f"Incomplete output: {incomplete}")
            
            details['validation_method'] = 'completeness_check'
            details['validation_rules'] = ['expected_outputs_present', 'complete_output_data']
            
            return LayerValidationResult(
                layer=ValidationLayer.OUTPUT_COMPLETENESS,
                success=score >= 0.95,
                score=score,
                issues=issues,
                warnings=warnings,
                metrics=metrics,
                details=details
            )
            
        except Exception as e:
            logger.error(f"Output completeness validation failed: {e}")
            raise
    
    async def _validate_temporal_consistency(self, request: ValidationRequest, validation_state: Dict[str, Any]) -> LayerValidationResult:
        """Validate temporal consistency."""
        try:
            issues = []
            warnings = []
            metrics = {}
            details = {}
            
            # Get replay events
            events = await self._get_replay_events(request)
            
            # Validate temporal consistency
            temporal_violations = []
            timestamp_anomalies = []
            
            # Check temporal ordering
            for i in range(1, len(events)):
                prev_event = events[i-1]
                curr_event = events[i]
                
                # Check timestamp ordering
                if curr_event.header.timestamp < prev_event.header.timestamp:
                    temporal_violations.append(f"Timestamp order violation: {prev_event.header.event_id} -> {curr_event.header.event_id}")
                
                # Check for timestamp anomalies (large gaps)
                time_diff = curr_event.header.timestamp - prev_event.header.timestamp
                if time_diff.total_seconds() > 3600:  # 1 hour gap
                    timestamp_anomalies.append(f"Large timestamp gap: {time_diff.total_seconds()}s between {prev_event.header.event_id} and {curr_event.header.event_id}")
            
            # Calculate metrics
            total_events = len(events)
            metrics['total_events'] = total_events
            metrics['temporal_violations'] = len(temporal_violations)
            metrics['timestamp_anomalies'] = len(timestamp_anomalies)
            metrics['temporal_consistency'] = 1.0 - len(temporal_violations) / max(total_events - 1, 1)
            
            # Calculate score
            score = metrics['temporal_consistency']
            
            # Add issues for temporal problems
            for violation in temporal_violations:
                issues.append(f"Temporal violation: {violation}")
            
            for anomaly in timestamp_anomalies:
                warnings.append(f"Timestamp anomaly: {anomaly}")
            
            details['validation_method'] = 'temporal_analysis'
            details['validation_rules'] = ['temporal_ordering', 'timestamp_consistency']
            
            return LayerValidationResult(
                layer=ValidationLayer.TEMPORAL_CONSISTENCY,
                success=score >= 0.95,
                score=score,
                issues=issues,
                warnings=warnings,
                metrics=metrics,
                details=details
            )
            
        except Exception as e:
            logger.error(f"Temporal consistency validation failed: {e}")
            raise
    
    async def _validate_logical_consistency(self, request: ValidationRequest, validation_state: Dict[str, Any]) -> LayerValidationResult:
        """Validate logical consistency."""
        try:
            issues = []
            warnings = []
            metrics = {}
            details = {}
            
            # Get replay state and events
            replay_state = await self._get_replay_state(request)
            await self._get_replay_events(request)
            
            # Validate logical consistency
            business_rule_violations = []
            domain_logic_violations = []
            
            # Check business rule consistency
            if 'orders' in replay_state and 'positions' in replay_state:
                # Validate order-position relationship
                for order_id, order_state in replay_state['orders'].items():
                    if order_state.get('status') == 'filled':
                        # Should have corresponding position
                        symbol = order_state.get('symbol')
                        side = order_state.get('side')
                        
                        position_found = False
                        for position_id, position_state in replay_state['positions'].items():
                            if (position_state.get('symbol') == symbol and 
                                position_state.get('side') == side and 
                                position_state.get('status') == 'open'):
                                position_found = True
                                break
                        
                        if not position_found:
                            business_rule_violations.append(f"Filled order {order_id} has no corresponding position")
            
            # Calculate metrics
            total_components = len(replay_state.get('orders', {})) + len(replay_state.get('positions', {}))
            metrics['total_components'] = total_components
            metrics['business_rule_violations'] = len(business_rule_violations)
            metrics['domain_logic_violations'] = len(domain_logic_violations)
            metrics['logical_consistency'] = 1.0 - (len(business_rule_violations) + len(domain_logic_violations)) / max(total_components, 1)
            
            # Calculate score
            score = metrics['logical_consistency']
            
            # Add issues for logical problems
            for violation in business_rule_violations:
                issues.append(f"Business rule violation: {violation}")
            
            for violation in domain_logic_violations:
                issues.append(f"Domain logic violation: {violation}")
            
            details['validation_method'] = 'logical_analysis'
            details['validation_rules'] = ['business_rules', 'domain_logic', 'application_logic']
            
            return LayerValidationResult(
                layer=ValidationLayer.LOGICAL_CONSISTENCY,
                success=score >= 0.95,
                score=score,
                issues=issues,
                warnings=warnings,
                metrics=metrics,
                details=details
            )
            
        except Exception as e:
            logger.error(f"Logical consistency validation failed: {e}")
            raise
    
    async def _validate_performance_consistency(self, request: ValidationRequest, validation_state: Dict[str, Any]) -> LayerValidationResult:
        """Validate performance consistency."""
        try:
            issues = []
            warnings = []
            metrics = {}
            details = {}
            
            # Get performance metrics
            performance_metrics = await self._get_performance_metrics(request)
            
            # Validate performance consistency
            performance_violations = []
            performance_anomalies = []
            
            # Check replay latency
            replay_latency = performance_metrics.get('replay_latency', 0)
            if replay_latency > 1.0:  # 1 second threshold
                performance_violations.append(f"High replay latency: {replay_latency}s")
            
            # Check memory usage
            memory_usage = performance_metrics.get('memory_usage', 0)
            if memory_usage > 500 * 1024 * 1024:  # 500MB threshold
                performance_violations.append(f"High memory usage: {memory_usage} bytes")
            
            # Check CPU usage
            cpu_usage = performance_metrics.get('cpu_usage', 0)
            if cpu_usage > 0.8:  # 80% threshold
                performance_violations.append(f"High CPU usage: {cpu_usage:.1%}")
            
            # Calculate metrics
            metrics['replay_latency'] = replay_latency
            metrics['memory_usage'] = memory_usage
            metrics['cpu_usage'] = cpu_usage
            metrics['performance_violations'] = len(performance_violations)
            metrics['performance_anomalies'] = len(performance_anomalies)
            metrics['performance_consistency'] = 1.0 - len(performance_violations) / 3.0  # 3 performance checks
            
            # Calculate score
            score = metrics['performance_consistency']
            
            # Add issues for performance problems
            for violation in performance_violations:
                issues.append(f"Performance violation: {violation}")
            
            for anomaly in performance_anomalies:
                warnings.append(f"Performance anomaly: {anomaly}")
            
            details['validation_method'] = 'performance_analysis'
            details['validation_rules'] = ['latency_consistency', 'resource_usage', 'throughput_consistency']
            
            return LayerValidationResult(
                layer=ValidationLayer.PERFORMANCE_CONSISTENCY,
                success=score >= 0.8,  # Lower threshold for performance
                score=score,
                issues=issues,
                warnings=warnings,
                metrics=metrics,
                details=details
            )
            
        except Exception as e:
            logger.error(f"Performance consistency validation failed: {e}")
            raise
    
    async def _get_replay_events(self, request: ValidationRequest) -> List[ExecutionEvent]:
        """Get events for replay validation."""
        try:
            # This would integrate with the actual replay engine
            # For now, return empty list
            return []
            
        except Exception as e:
            logger.error(f"Failed to get replay events: {e}")
            raise
    
    async def _get_replay_state(self, request: ValidationRequest) -> Dict[str, Any]:
        """Get state for replay validation."""
        try:
            # This would integrate with the actual replay engine
            # For now, return empty state
            return {}
            
        except Exception as e:
            logger.error(f"Failed to get replay state: {e}")
            raise
    
    async def _get_replay_outputs(self, request: ValidationRequest) -> Dict[str, Any]:
        """Get outputs for replay validation."""
        try:
            # This would integrate with the actual replay engine
            # For now, return empty outputs
            return {}
            
        except Exception as e:
            logger.error(f"Failed to get replay outputs: {e}")
            raise
    
    async def _get_expected_outputs(self, request: ValidationRequest) -> List[str]:
        """Get expected outputs for validation."""
        try:
            # This would calculate expected outputs based on events
            # For now, return empty list
            return []
            
        except Exception as e:
            logger.error(f"Failed to get expected outputs: {e}")
            raise
    
    async def _get_performance_metrics(self, request: ValidationRequest) -> Dict[str, Any]:
        """Get performance metrics for validation."""
        try:
            # This would collect actual performance metrics
            # For now, return placeholder metrics
            return {
                'replay_latency': 0.1,
                'memory_usage': 100 * 1024 * 1024,  # 100MB
                'cpu_usage': 0.1  # 10%
            }
            
        except Exception as e:
            logger.error(f"Failed to get performance metrics: {e}")
            raise
    
    async def _calculate_validation_summary(self, layer_results: List[LayerValidationResult]) -> Dict[str, Any]:
        """Calculate overall validation summary."""
        try:
            total_layers = len(layer_results)
            successful_layers = sum(1 for result in layer_results if result.success)
            overall_success = successful_layers == total_layers
            
            # Calculate overall score
            if layer_results:
                overall_score = sum(result.score for result in layer_results) / len(layer_results)
            else:
                overall_score = 1.0
            
            # Count total issues and warnings
            total_issues = sum(len(result.issues) for result in layer_results)
            total_warnings = sum(len(result.warnings) for result in layer_results)
            
            return {
                'overall_success': overall_success,
                'overall_score': overall_score,
                'total_layers': total_layers,
                'successful_layers': successful_layers,
                'failed_layers': total_layers - successful_layers,
                'total_issues': total_issues,
                'total_warnings': total_warnings,
                'validation_quality': 'high' if overall_score >= 0.95 else 'medium' if overall_score >= 0.8 else 'low'
            }
            
        except Exception as e:
            logger.error(f"Failed to calculate validation summary: {e}")
            return {'overall_success': False, 'error': str(e)}
    
    async def _calculate_performance_metrics(self, validation_state: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate validation performance metrics."""
        try:
            start_time = validation_state['start_time']
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()
            
            layer_count = len(validation_state['layers'])
            total_issues = sum(len(result.issues) for result in validation_state['layer_results'].values())
            
            return {
                'validation_duration': duration,
                'layers_per_second': layer_count / duration if duration > 0 else 0,
                'issues_per_second': total_issues / duration if duration > 0 else 0,
                'memory_usage': 0,  # Would be calculated from actual metrics
                'cpu_usage': 0.0  # Would be calculated from actual metrics
            }
            
        except Exception as e:
            logger.error(f"Failed to calculate performance metrics: {e}")
            return {'error': str(e)}
    
    async def _initialize_validation_rules(self) -> Dict[str, Any]:
        """Initialize validation rules."""
        try:
            return {
                'event_integrity': [
                    'event_signature_valid',
                    'hash_chain_integrity',
                    'event_data_integrity'
                ],
                'event_sequence': [
                    'monotonic_sequence',
                    'no_sequence_gaps',
                    'unique_sequence'
                ],
                'event_causality': [
                    'temporal_causality',
                    'sequence_causality',
                    'causation_integrity'
                ],
                'state_consistency': [
                    'state_invariants',
                    'state_constraints',
                    'business_rules'
                ],
                'output_consistency': [
                    'output_schema_consistency',
                    'output_content_consistency'
                ],
                'temporal_consistency': [
                    'temporal_ordering',
                    'timestamp_consistency'
                ],
                'logical_consistency': [
                    'business_rules',
                    'domain_logic',
                    'application_logic'
                ],
                'performance_consistency': [
                    'latency_consistency',
                    'resource_usage',
                    'throughput_consistency'
                ]
            }
            
        except Exception as e:
            logger.error(f"Failed to initialize validation rules: {e}")
            return {}
    
    async def get_validation_status(self, validation_id: str) -> Optional[Dict[str, Any]]:
        """Get current status of active validation."""
        try:
            return self.active_validations.get(validation_id)
        except Exception as e:
            logger.error(f"Failed to get validation status: {e}")
            return None
    
    async def get_validation_history(self, tenant_id: Optional[str] = None, 
                                   limit: int = 100) -> List[ValidationResult]:
        """Get validation history."""
        try:
            history = self.validation_history
            
            # Filter by tenant if specified
            if tenant_id:
                # Note: This would require tenant_id in ValidationResult
                # For now, return all history
                pass
            
            # Return most recent results
            return history[-limit:] if len(history) > limit else history
            
        except Exception as e:
            logger.error(f"Failed to get validation history: {e}")
            return []
    
    async def cancel_validation(self, validation_id: str) -> bool:
        """Cancel active validation."""
        try:
            if validation_id in self.active_validations:
                del self.active_validations[validation_id]
                logger.info(f"Cancelled validation {validation_id}")
                return True
            else:
                logger.warning(f"Validation {validation_id} not found in active validations")
                return False
                
        except Exception as e:
            logger.error(f"Failed to cancel validation: {e}")
            return False
    
    async def get_validator_statistics(self) -> Dict[str, Any]:
        """Get validator statistics."""
        try:
            return {
                'active_validations': len(self.active_validations),
                'total_validations': len(self.validation_history),
                'validation_cache_size': len(self.validation_cache),
                'rule_cache_size': len(self.rule_cache),
                'validation_timeout': self.validation_timeout,
                'max_events_per_validation': self.max_events_per_validation,
                'validation_batch_size': self.validation_batch_size
            }
        except Exception as e:
            logger.error(f"Failed to get validator statistics: {e}")
            return {'error': str(e)}


# Global replay validator instance
replay_validator = ReplayValidator()
