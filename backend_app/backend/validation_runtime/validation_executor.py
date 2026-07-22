"""
Validation Executor - Phase A

This module executes validation checks for continuous institutional-grade
operational validation of the strict algo trading platform.

Author: Principal Institutional Operational Validation Engineer
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from backend_app.backend.validation.deterministic_state_validator import \
    DeterministicStateValidator
from backend_app.backend.validation.execution_correctness_validator import \
    ExecutionCorrectnessValidator
from backend_app.backend.validation.failover_validation_runner import \
    FailoverValidationRunner
from backend_app.backend.validation.reconciliation_validator import \
    ReconciliationValidator
from backend_app.backend.validation.replay_consistency_validator import \
    ReplayConsistencyValidator
from backend_app.backend.validation.websocket_sequence_validator import \
    WebSocketSequenceValidator
from backend_app.backend.validation.workflow_validator import WorkflowValidator

logger = logging.getLogger("validation_executor")


class ValidationExecutor:
    """
    Validation executor for continuous validation execution.
    
    Executes validation checks for institutional-grade operational validation.
    """
    
    def __init__(self):
        """Initialize validation executor."""
        self.workflow_validator = WorkflowValidator()
        self.deterministic_validator = DeterministicStateValidator()
        self.replay_validator = ReplayConsistencyValidator()
        self.websocket_validator = WebSocketSequenceValidator()
        self.failover_validator = FailoverValidationRunner()
        self.reconciliation_validator = ReconciliationValidator()
        self.execution_validator = ExecutionCorrectnessValidator()
    
    async def execute_validation(self, validator_name: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute validation by name.
        
        Args:
            validator_name: Validator name
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info(f"Executing validation: {validator_name}")
        
        start_time = datetime.now(timezone.utc)
        
        try:
            result = await self._execute_validator(validator_name, tenant_id)
            
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            result["execution_time_ms"] = execution_time
            result["timestamp"] = datetime.now(timezone.utc).isoformat()
            
            logger.info(f"Validation complete: {validator_name} - Passed: {result.get('passed', False)}")
            
            return result
            
        except Exception as e:
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            logger.error(f"Validation execution failed: {validator_name} - {e}")
            
            return {
                "passed": False,
                "assertions": [],
                "failures": [f"Validation execution failed: {str(e)}"],
                "warnings": [],
                "execution_time_ms": execution_time,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    async def _execute_validator(self, validator_name: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute specific validator.
        
        Args:
            validator_name: Validator name
            tenant_id: Optional tenant ID
            
        Returns:
            Validation result
        """
        validator_map = {
            "replay_consistency": self.replay_validator,
            "execution_deduplication": self.execution_validator,
            "websocket_sequencing": self.websocket_validator,
            "failover_ownership": self.failover_validator,
            "deterministic_replay": self.replay_validator,
            "reconciliation_integrity": self.reconciliation_validator,
            "fencing_token": self.deterministic_validator,
            "execution_sequencing": self.workflow_validator,
            "workflow": self.workflow_validator,
            "deterministic_state": self.deterministic_validator,
            "execution_correctness": self.execution_validator
        }
        
        if validator_name not in validator_map:
            raise ValueError(f"Unknown validator: {validator_name}")
        
        validator = validator_map[validator_name]
        return await validator.validate(tenant_id)
    
    async def execute_all_validations(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute all validations.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Combined validation result
        """
        logger.info("Executing all validations...")
        
        results = {}
        
        # Execute all validators
        validators = [
            "replay_consistency",
            "execution_deduplication",
            "websocket_sequencing",
            "failover_ownership",
            "deterministic_replay",
            "reconciliation_integrity",
            "fencing_token",
            "execution_sequencing"
        ]
        
        for validator_name in validators:
            result = await self.execute_validation(validator_name, tenant_id)
            results[validator_name] = result
        
        # Calculate summary
        total_assertions = sum(len(r.get("assertions", [])) for r in results.values())
        passed_assertions = sum(len(r.get("assertions", [])) for r in results.values() if r.get("passed", False))
        failed_assertions = sum(len(r.get("failures", [])) for r in results.values())
        total_warnings = sum(len(r.get("warnings", [])) for r in results.values())
        
        summary = {
            "total_validations": len(results),
            "passed_validations": sum(1 for r in results.values() if r.get("passed", False)),
            "failed_validations": sum(1 for r in results.values() if not r.get("passed", False)),
            "total_assertions": total_assertions,
            "passed_assertions": passed_assertions,
            "failed_assertions": failed_assertions,
            "total_warnings": total_warnings,
            "assertion_pass_rate": (passed_assertions / total_assertions * 100) if total_assertions > 0 else 0
        }
        
        return {
            "results": results,
            "summary": summary,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def execute_critical_validations(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute critical validations only.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Combined validation result
        """
        logger.info("Executing critical validations...")
        
        results = {}
        
        # Execute critical validators
        critical_validators = [
            "replay_consistency",
            "execution_deduplication",
            "failover_ownership",
            "fencing_token"
        ]
        
        for validator_name in critical_validators:
            result = await self.execute_validation(validator_name, tenant_id)
            results[validator_name] = result
        
        # Calculate summary
        total_assertions = sum(len(r.get("assertions", [])) for r in results.values())
        passed_assertions = sum(len(r.get("assertions", [])) for r in results.values() if r.get("passed", False))
        failed_assertions = sum(len(r.get("failures", [])) for r in results.values())
        
        summary = {
            "total_validations": len(results),
            "passed_validations": sum(1 for r in results.values() if r.get("passed", False)),
            "failed_validations": sum(1 for r in results.values() if not r.get("passed", False)),
            "total_assertions": total_assertions,
            "passed_assertions": passed_assertions,
            "failed_assertions": failed_assertions,
            "assertion_pass_rate": (passed_assertions / total_assertions * 100) if total_assertions > 0 else 0
        }
        
        return {
            "results": results,
            "summary": summary,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }


# Global instance
_validation_executor: ValidationExecutor = None


def get_validation_executor() -> ValidationExecutor:
    """Get or create validation executor instance."""
    global _validation_executor
    if _validation_executor is None:
        _validation_executor = ValidationExecutor()
    return _validation_executor
