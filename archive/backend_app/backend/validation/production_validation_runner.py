"""
Production Validation Runner - Phase 1

This module orchestrates institutional-grade production validation for the strict algo trading platform.
It runs all validators and produces comprehensive validation reports.

Author: Principal Institutional Validation Engineer
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List

from .deterministic_state_validator import DeterministicStateValidator
from .execution_correctness_validator import ExecutionCorrectnessValidator
from .failover_validation_runner import FailoverValidationRunner
from .reconciliation_validator import ReconciliationValidator
from .replay_consistency_validator import ReplayConsistencyValidator
from .websocket_sequence_validator import WebSocketSequenceValidator
from .workflow_validator import WorkflowValidator

logger = logging.getLogger("production_validation_runner")


@dataclass
class ValidationResult:
    """Result of a validation check."""
    validator_name: str
    passed: bool
    assertions: List[str]
    failures: List[str]
    warnings: List[str]
    execution_time_ms: float


@dataclass
class ValidationReport:
    """Comprehensive validation report."""
    validation_id: str
    timestamp: datetime
    results: List[ValidationResult]
    summary: Dict[str, Any]
    
    def get_total_assertions(self) -> int:
        """Get total number of assertions."""
        return sum(len(r.assertions) for r in self.results)
    
    def get_passed_assertions(self) -> int:
        """Get number of passed assertions."""
        return sum(len(r.assertions) for r in self.results if r.passed)
    
    def get_failed_assertions(self) -> int:
        """Get number of failed assertions."""
        return sum(len(r.failures) for r in self.results)
    
    def get_overall_status(self) -> str:
        """Get overall validation status."""
        if all(r.passed for r in self.results):
            return "PASSED"
        elif any(r.failures for r in self.results):
            return "FAILED"
        else:
            return "WARNING"


class ProductionValidationRunner:
    """
    Production validation runner for institutional-grade validation.
    
    Orchestrates all validators and produces comprehensive validation reports.
    """
    
    def __init__(self):
        """Initialize production validation runner."""
        self.workflow_validator = WorkflowValidator()
        self.deterministic_validator = DeterministicStateValidator()
        self.replay_validator = ReplayConsistencyValidator()
        self.websocket_validator = WebSocketSequenceValidator()
        self.failover_validator = FailoverValidationRunner()
        self.reconciliation_validator = ReconciliationValidator()
        self.execution_validator = ExecutionCorrectnessValidator()
    
    async def run_validation(self, tenant_id: str = None) -> ValidationReport:
        """
        Run comprehensive production validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Comprehensive validation report
        """
        validation_id = f"validation_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        logger.info(f"Starting production validation: {validation_id}")
        
        results = []
        
        # Run workflow validation
        logger.info("Running workflow validation...")
        workflow_result = await self._run_validator(
            self.workflow_validator,
            "workflow_validator",
            tenant_id
        )
        results.append(workflow_result)
        
        # Run deterministic state validation
        logger.info("Running deterministic state validation...")
        deterministic_result = await self._run_validator(
            self.deterministic_validator,
            "deterministic_state_validator",
            tenant_id
        )
        results.append(deterministic_result)
        
        # Run replay consistency validation
        logger.info("Running replay consistency validation...")
        replay_result = await self._run_validator(
            self.replay_validator,
            "replay_consistency_validator",
            tenant_id
        )
        results.append(replay_result)
        
        # Run websocket sequence validation
        logger.info("Running websocket sequence validation...")
        websocket_result = await self._run_validator(
            self.websocket_validator,
            "websocket_sequence_validator",
            tenant_id
        )
        results.append(websocket_result)
        
        # Run failover validation
        logger.info("Running failover validation...")
        failover_result = await self._run_validator(
            self.failover_validator,
            "failover_validation_runner",
            tenant_id
        )
        results.append(failover_result)
        
        # Run reconciliation validation
        logger.info("Running reconciliation validation...")
        reconciliation_result = await self._run_validator(
            self.reconciliation_validator,
            "reconciliation_validator",
            tenant_id
        )
        results.append(reconciliation_result)
        
        # Run execution correctness validation
        logger.info("Running execution correctness validation...")
        execution_result = await self._run_validator(
            self.execution_validator,
            "execution_correctness_validator",
            tenant_id
        )
        results.append(execution_result)
        
        # Generate summary
        summary = self._generate_summary(results)
        
        # Create report
        report = ValidationReport(
            validation_id=validation_id,
            timestamp=datetime.now(timezone.utc),
            results=results,
            summary=summary
        )
        
        logger.info(f"Production validation complete: {validation_id} - Status: {report.get_overall_status()}")
        
        return report
    
    async def _run_validator(
        self,
        validator: Any,
        validator_name: str,
        tenant_id: str = None
    ) -> ValidationResult:
        """
        Run a single validator.
        
        Args:
            validator: Validator instance
            validator_name: Validator name
            tenant_id: Optional tenant ID
            
        Returns:
            Validation result
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # Run validation
            result = await validator.validate(tenant_id)
            
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            
            return ValidationResult(
                validator_name=validator_name,
                passed=result.get("passed", False),
                assertions=result.get("assertions", []),
                failures=result.get("failures", []),
                warnings=result.get("warnings", []),
                execution_time_ms=execution_time
            )
            
        except Exception as e:
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            logger.error(f"Validator {validator_name} failed: {e}")
            
            return ValidationResult(
                validator_name=validator_name,
                passed=False,
                assertions=[],
                failures=[f"Validator execution failed: {str(e)}"],
                warnings=[],
                execution_time_ms=execution_time
            )
    
    def _generate_summary(self, results: List[ValidationResult]) -> Dict[str, Any]:
        """
        Generate validation summary.
        
        Args:
            results: List of validation results
            
        Returns:
            Summary dictionary
        """
        total_assertions = sum(len(r.assertions) for r in results)
        passed_assertions = sum(len(r.assertions) for r in results if r.passed)
        failed_assertions = sum(len(r.failures) for r in results)
        total_warnings = sum(len(r.warnings) for r in results)
        total_execution_time_ms = sum(r.execution_time_ms for r in results)
        
        return {
            "total_validators": len(results),
            "passed_validators": sum(1 for r in results if r.passed),
            "failed_validators": sum(1 for r in results if not r.passed),
            "total_assertions": total_assertions,
            "passed_assertions": passed_assertions,
            "failed_assertions": failed_assertions,
            "total_warnings": total_warnings,
            "total_execution_time_ms": total_execution_time_ms,
            "assertion_pass_rate": (passed_assertions / total_assertions * 100) if total_assertions > 0 else 0
        }
    
    def print_report(self, report: ValidationReport):
        """
        Print validation report to console.
        
        Args:
            report: Validation report
        """
        print("\n" + "="*80)
        print("PRODUCTION VALIDATION REPORT")
        print(f"Validation ID: {report.validation_id}")
        print(f"Timestamp: {report.timestamp.isoformat()}")
        print(f"Status: {report.get_overall_status()}")
        print("="*80)
        
        print("\nSUMMARY:")
        for key, value in report.summary.items():
            print(f"  {key}: {value}")
        
        print("\nVALIDATOR RESULTS:")
        for result in report.results:
            status = "✅ PASSED" if result.passed else "❌ FAILED"
            print(f"\n{result.validator_name}: {status}")
            print(f"  Execution Time: {result.execution_time_ms:.2f}ms")
            print(f"  Assertions: {len(result.assertions)}")
            
            if result.failures:
                print(f"  Failures: {len(result.failures)}")
                for failure in result.failures:
                    print(f"    - {failure}")
            
            if result.warnings:
                print(f"  Warnings: {len(result.warnings)}")
                for warning in result.warnings:
                    print(f"    - {warning}")
        
        print("\n" + "="*80)
    
    async def run_and_report(self, tenant_id: str = None) -> ValidationReport:
        """
        Run validation and print report.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation report
        """
        report = await self.run_validation(tenant_id)
        self.print_report(report)
        return report


# Global instance
_production_validation_runner: ProductionValidationRunner = None


def get_production_validation_runner() -> ProductionValidationRunner:
    """Get or create production validation runner instance."""
    global _production_validation_runner
    if _production_validation_runner is None:
        _production_validation_runner = ProductionValidationRunner()
    return _production_validation_runner
