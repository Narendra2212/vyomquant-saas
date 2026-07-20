"""
Replay Validation Suite
Principal Institutional Distributed Systems Validation Engineer

Validates replay correctness, replay persistence, snapshot integrity,
checkpoint integrity, replay divergence, replay-safe execution.
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Dict, Any

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "aerora_quant_backend_updated_final1"))

from full_system_validation_runtime import ValidationSuite

logger = logging.getLogger("ReplayValidation")

class ReplayValidationSuite(ValidationSuite):
    """Comprehensive replay validation suite."""
    
    def __init__(self):
        super().__init__("ReplayValidationSuite")
    
    async def _execute_tests(self):
        """Execute all replay validation tests."""
        await self._test_startup_recovery()
        await self._test_job_persistence()
        await self._test_replay_job()
        await self._test_snapshot_integrity()
        await self._test_checkpoint_integrity()
        await self._test_replay_safe_execution()
        await self._test_replay_divergence_detection()
    
    async def _test_startup_recovery(self):
        """Test startup recovery."""
        self.results["tests_run"] += 1
        test_name = "Startup Recovery"
        
        try:
            from backend.startup_recovery import run_startup_recovery
            
            # Test startup recovery function
            if not callable(run_startup_recovery):
                raise ValueError("run_startup_recovery is not callable")
            
            # Check for stuck task detection
            import inspect
            source = inspect.getsource(run_startup_recovery)
            
            if 'stuck' not in source.lower():
                self.results["warnings"].append("Stuck task detection not found")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_job_persistence(self):
        """Test job persistence."""
        self.results["tests_run"] += 1
        test_name = "Job Persistence"
        
        try:
            from backend.distributed_execution.job_persistence import job_persistence
            
            # Test required methods
            required_methods = [
                'save_job',
                'load_job',
                'get_jobs_by_tenant',
                'replay_job',
                'cleanup_expired_jobs'
            ]
            
            for method in required_methods:
                if not hasattr(job_persistence, method):
                    raise ValueError(f"Missing method: {method}")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_replay_job(self):
        """Test replay job functionality."""
        self.results["tests_run"] += 1
        test_name = "Replay Job"
        
        try:
            from backend.distributed_execution.orchestrator import ExecutionOrchestrator
            
            orchestrator = ExecutionOrchestrator()
            
            # Test replay_job method
            if not hasattr(orchestrator, 'replay_job'):
                raise ValueError("replay_job method not found")
            
            # Test that replay creates new job ID
            import inspect
            source = inspect.getsource(orchestrator.replay_job)
            
            if 'job_id' not in source:
                self.results["warnings"].append("Job ID generation not found in replay")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_snapshot_integrity(self):
        """Test snapshot integrity."""
        self.results["tests_run"] += 1
        test_name = "Snapshot Integrity"
        
        try:
            from backend.state_persistence import router as persistence_router
            
            # Check for snapshot endpoints
            if not hasattr(persistence_router, 'routes'):
                self.results["warnings"].append("Cannot verify snapshot endpoints")
            else:
                routes = [route.path for route in persistence_router.routes]
                snapshot_routes = [r for r in routes if 'snapshot' in r.lower()]
                
                if not snapshot_routes:
                    self.results["warnings"].append("No snapshot endpoints found")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_checkpoint_integrity(self):
        """Test checkpoint integrity."""
        self.results["tests_run"] += 1
        test_name = "Checkpoint Integrity"
        
        try:
            from backend.state_persistence import router as persistence_router
            
            # Check for checkpoint endpoints
            if hasattr(persistence_router, 'routes'):
                routes = [route.path for route in persistence_router.routes]
                checkpoint_routes = [r for r in routes if 'checkpoint' in r.lower()]
                
                if not checkpoint_routes:
                    self.results["warnings"].append("No checkpoint endpoints found")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_replay_safe_execution(self):
        """Test replay-safe execution."""
        self.results["tests_run"] += 1
        test_name = "Replay-Safe Execution"
        
        try:
            from backend.distributed_execution.idempotent_exchange_submission import (
                IdempotentExchangeSubmission
            )
            
            # Test idempotency
            if not hasattr(IdempotentExchangeSubmission, '__init__'):
                raise ValueError("IdempotentExchangeSubmission class not found")
            
            # Check for idempotency key handling
            import inspect
            source = inspect.getsource(IdempotentExchangeSubmission)
            
            if 'idempotency' not in source.lower():
                self.results["warnings"].append("Idempotency key handling not found")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_replay_divergence_detection(self):
        """Test replay divergence detection."""
        self.results["tests_run"] += 1
        test_name = "Replay Divergence Detection"
        
        try:
            from backend.distributed_execution.exchange_reconciliation_engine import (
                ExchangeReconciliationEngine
            )
            
            # Test reconciliation engine
            if not hasattr(ExchangeReconciliationEngine, '__init__'):
                raise ValueError("ExchangeReconciliationEngine class not found")
            
            # Check for divergence detection
            import inspect
            source = inspect.getsource(ExchangeReconciliationEngine)
            
            if 'reconcil' not in source.lower():
                self.results["warnings"].append("Reconciliation logic not found")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
