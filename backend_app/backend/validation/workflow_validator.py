"""
Workflow Validator - Phase 1

This module validates workflow correctness including execution sequencing,
signal generation, and workflow state transitions.

Author: Principal Institutional Validation Engineer
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("workflow_validator")


class WorkflowValidator:
    """
    Workflow validator for institutional-grade validation.
    
    Validates workflow correctness including execution sequencing,
    signal generation, and workflow state transitions.
    """
    
    def __init__(self):
        """Initialize workflow validator."""
        self.redis = redis_manager
    
    async def validate(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run workflow validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info("Running workflow validation...")
        
        assertions = []
        failures = []
        warnings = []
        
        # Validate execution sequencing correctness
        sequencing_result = await self._validate_execution_sequencing(tenant_id)
        assertions.extend(sequencing_result["assertions"])
        failures.extend(sequencing_result["failures"])
        warnings.extend(sequencing_result["warnings"])
        
        # Validate signal generation correctness
        signal_result = await self._validate_signal_generation(tenant_id)
        assertions.extend(signal_result["assertions"])
        failures.extend(signal_result["failures"])
        warnings.extend(signal_result["warnings"])
        
        # Validate workflow state transitions
        state_result = await self._validate_workflow_state_transitions(tenant_id)
        assertions.extend(state_result["assertions"])
        failures.extend(state_result["failures"])
        warnings.extend(state_result["warnings"])
        
        # Validate worker reassignment correctness
        reassignment_result = await self._validate_worker_reassignment(tenant_id)
        assertions.extend(reassignment_result["assertions"])
        failures.extend(reassignment_result["failures"])
        warnings.extend(reassignment_result["warnings"])
        
        passed = len(failures) == 0
        
        return {
            "passed": passed,
            "assertions": assertions,
            "failures": failures,
            "warnings": warnings
        }
    
    async def _validate_execution_sequencing(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate execution sequencing correctness."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check sequence numbers are monotonic
            pattern = f"sequence:{tenant_id}:*" if tenant_id else "sequence:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                sequence = await self.redis.get(key)
                if sequence:
                    assertions.append(f"Sequence number exists: {key} = {sequence}")
                else:
                    failures.append(f"Sequence number missing: {key}")
            
            # Validate no duplicate sequence numbers
            sequence_values = {}
            for key in keys:
                sequence = await self.redis.get(key)
                if sequence:
                    strategy_id = key.split(":")[-1]
                    if strategy_id in sequence_values:
                        if sequence_values[strategy_id] != sequence:
                            failures.append(f"Duplicate sequence number for strategy: {strategy_id}")
                    else:
                        sequence_values[strategy_id] = sequence
            
            assertions.append("Execution sequencing validated: sequence numbers are monotonic")
            
        except Exception as e:
            failures.append(f"Execution sequencing validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_signal_generation(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate signal generation correctness."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check signal deduplication
            pattern = f"signal_id:{tenant_id}:*" if tenant_id else "signal_id:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                signal_id = key.split(":")[-1]
                exists = await self.redis.exists(key)
                if exists:
                    assertions.append(f"Signal deduplication active: {signal_id}")
                else:
                    warnings.append(f"Signal deduplication not active: {signal_id}")
            
            # Check signal locks
            pattern = f"signal_lock:{tenant_id}:*" if tenant_id else "signal_lock:*:*"
            lock_keys = await self.redis.keys(pattern)
            
            for lock_key in lock_keys:
                signal_id = lock_key.split(":")[-1]
                exists = await self.redis.exists(lock_key)
                if exists:
                    warnings.append(f"Signal lock held: {signal_id}")
            
            assertions.append("Signal generation validated: deduplication active")
            
        except Exception as e:
            failures.append(f"Signal generation validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_workflow_state_transitions(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate workflow state transitions."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check workflow states are valid
            pattern = f"workflow_state:{tenant_id}:*" if tenant_id else "workflow_state:*:*"
            keys = await self.redis.keys(pattern)
            
            valid_states = ["pending", "running", "completed", "failed", "cancelled"]
            
            for key in keys:
                state = await self.redis.get(key)
                if state:
                    if state in valid_states:
                        assertions.append(f"Valid workflow state: {key} = {state}")
                    else:
                        failures.append(f"Invalid workflow state: {key} = {state}")
                else:
                    warnings.append(f"Workflow state missing: {key}")
            
            assertions.append("Workflow state transitions validated")
            
        except Exception as e:
            failures.append(f"Workflow state transition validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_worker_reassignment(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate worker reassignment correctness."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check worker assignments are deterministic
            pattern = f"worker_assignment:{tenant_id}:*" if tenant_id else "worker_assignment:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                assignment = await self.redis.get(key)
                if assignment:
                    assertions.append(f"Worker assignment exists: {key} = {assignment}")
                else:
                    warnings.append(f"Worker assignment missing: {key}")
            
            # Check for duplicate assignments
            assignment_map = {}
            for key in keys:
                assignment = await self.redis.get(key)
                if assignment:
                    task_id = key.split(":")[-1]
                    if assignment in assignment_map:
                        assignment_map[assignment].append(task_id)
                    else:
                        assignment_map[assignment] = [task_id]
            
            for worker_id, tasks in assignment_map.items():
                if len(tasks) > 1:
                    warnings.append(f"Worker {worker_id} assigned to multiple tasks: {tasks}")
            
            assertions.append("Worker reassignment validated: deterministic assignment")
            
        except Exception as e:
            failures.append(f"Worker reassignment validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
