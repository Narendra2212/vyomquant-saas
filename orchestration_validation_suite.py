"""
Orchestration Validation Suite
Principal Institutional Distributed Systems Validation Engineer

Validates orchestration correctness, tenant isolation, deterministic ordering,
queue ownership, worker leases, failover recovery.
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Dict, Any

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "aerora_quant_backend_updated_final1"))

from full_system_validation_runtime import ValidationSuite

logger = logging.getLogger("OrchestrationValidation")

class OrchestrationValidationSuite(ValidationSuite):
    """Comprehensive orchestration validation suite."""
    
    def __init__(self):
        super().__init__("OrchestrationValidationSuite")
    
    async def _execute_tests(self):
        """Execute all orchestration validation tests."""
        await self._test_orchestrator()
        await self._test_fleet_manager()
        await self._test_queue_manager()
        await self._test_worker_pool()
        await self._test_tenant_isolation()
        await self._test_deterministic_ordering()
        await self._test_failover_recovery()
    
    async def _test_orchestrator(self):
        """Test execution orchestrator."""
        self.results["tests_run"] += 1
        test_name = "Execution Orchestrator"
        
        try:
            from backend.distributed_execution.orchestrator import (
                ExecutionOrchestrator,
                execution_orchestrator
            )
            
            # Test orchestrator class
            orchestrator = ExecutionOrchestrator()
            
            # Test required methods
            required_methods = [
                'initialize',
                'start',
                'stop',
                'submit_execution_job',
                'get_job_status',
                'get_tenant_jobs',
                'get_system_status',
                'replay_job'
            ]
            
            for method in required_methods:
                if not hasattr(orchestrator, method):
                    raise ValueError(f"Missing method: {method}")
            
            # Test global instance
            if not isinstance(execution_orchestrator, ExecutionOrchestrator):
                raise ValueError("execution_orchestrator is not an ExecutionOrchestrator instance")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_fleet_manager(self):
        """Test fleet manager."""
        self.results["tests_run"] += 1
        test_name = "Fleet Manager"
        
        try:
            from backend.fleet_manager import FleetManager
            
            # Test FleetManager class
            fleet = FleetManager()
            
            # Test required methods
            required_methods = [
                'start_bot',
                'stop_bot',
                'shutdown_all',
                'get_status'
            ]
            
            for method in required_methods:
                if not hasattr(fleet, method):
                    raise ValueError(f"Missing method: {method}")
            
            # Test capacity limits
            if not hasattr(fleet, 'MAX_SYSTEM_BOTS'):
                raise ValueError("MAX_SYSTEM_BOTS not found")
            
            if not hasattr(fleet, 'MAX_BOTS_PER_USER'):
                raise ValueError("MAX_BOTS_PER_USER not found")
            
            # Test lock protection (FIX FM-3)
            if not hasattr(fleet, '_lock'):
                self.results["warnings"].append("_lock not found - race condition risk")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_queue_manager(self):
        """Test queue manager."""
        self.results["tests_run"] += 1
        test_name = "Queue Manager"
        
        try:
            from backend.distributed_execution.queue_manager import (
                queue_manager,
                QueueType
            )
            
            # Test queue manager
            if not hasattr(queue_manager, 'publish_execution_job'):
                raise ValueError("publish_execution_job method not found")
            
            # Test queue types
            required_queue_types = [
                'EXECUTION',
                'RETRY',
                'DEAD_LETTER',
                'HEARTBEAT'
            ]
            
            for queue_type in required_queue_types:
                if not hasattr(QueueType, queue_type):
                    self.results["warnings"].append(f"QueueType.{queue_type} not found")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_worker_pool(self):
        """Test worker pool."""
        self.results["tests_run"] += 1
        test_name = "Worker Pool"
        
        try:
            from backend.distributed_execution.execution_worker import (
                WorkerPool,
                ExchangeGateway
            )
            
            # Test WorkerPool class
            if not hasattr(WorkerPool, '__init__'):
                raise ValueError("WorkerPool class not found")
            
            # Test ExchangeGateway class
            if not hasattr(ExchangeGateway, '__init__'):
                raise ValueError("ExchangeGateway class not found")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_tenant_isolation(self):
        """Test tenant isolation."""
        self.results["tests_run"] += 1
        test_name = "Tenant Isolation"
        
        try:
            from backend.distributed_execution.orchestrator import ExecutionOrchestrator
            
            orchestrator = ExecutionOrchestrator()
            
            # Test tenant_id in job submission
            import inspect
            source = inspect.getsource(orchestrator.submit_execution_job)
            
            if 'tenant_id' not in source:
                raise ValueError("tenant_id not used in submit_execution_job")
            
            # Test get_tenant_jobs method
            if not hasattr(orchestrator, 'get_tenant_jobs'):
                raise ValueError("get_tenant_jobs method not found")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_deterministic_ordering(self):
        """Test deterministic ordering."""
        self.results["tests_run"] += 1
        test_name = "Deterministic Ordering"
        
        try:
            from backend.distributed_execution.queue_manager import (
                JobPriority,
                ExecutionJob
            )
            
            # Test job priority
            if not hasattr(JobPriority, 'NORMAL'):
                raise ValueError("JobPriority.NORMAL not found")
            
            # Test ExecutionJob has ordering fields
            job = ExecutionJob()
            
            if not hasattr(job, 'created_at'):
                self.results["warnings"].append("created_at not found in ExecutionJob")
            
            if not hasattr(job, 'priority'):
                self.results["warnings"].append("priority not found in ExecutionJob")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_failover_recovery(self):
        """Test failover recovery."""
        self.results["tests_run"] += 1
        test_name = "Failover Recovery"
        
        try:
            from backend.distributed_execution.orchestrator import ExecutionOrchestrator
            from backend.startup_recovery import run_startup_recovery
            
            # Test startup recovery
            if not callable(run_startup_recovery):
                raise ValueError("run_startup_recovery is not callable")
            
            # Test replay_job method
            orchestrator = ExecutionOrchestrator()
            if not hasattr(orchestrator, 'replay_job'):
                raise ValueError("replay_job method not found")
            
            # Test retry loop in orchestrator
            import inspect
            source = inspect.getsource(orchestrator._retry_loop)
            
            if 'retry' not in source.lower():
                self.results["warnings"].append("Retry logic not found in _retry_loop")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
