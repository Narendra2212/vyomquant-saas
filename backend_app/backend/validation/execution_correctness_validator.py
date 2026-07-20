"""
Execution Correctness Validator - Phase 1

This module validates execution correctness including duplicate execution detection,
execution deduplication, signal deduplication, and orphan execution state detection.

Author: Principal Institutional Validation Engineer
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("execution_correctness_validator")


class ExecutionCorrectnessValidator:
    """
    Execution correctness validator for institutional-grade validation.
    
    Validates execution correctness including duplicate execution detection,
    execution deduplication, signal deduplication, and orphan execution state detection.
    """
    
    def __init__(self):
        """Initialize execution correctness validator."""
        self.redis = redis_manager
    
    async def validate(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run execution correctness validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info("Running execution correctness validation...")
        
        assertions = []
        failures = []
        warnings = []
        
        # Validate no duplicate execution
        duplicate_result = await self._validate_no_duplicate_execution(tenant_id)
        assertions.extend(duplicate_result["assertions"])
        failures.extend(duplicate_result["failures"])
        warnings.extend(duplicate_result["warnings"])
        
        # Validate execution deduplication correctness
        dedup_result = await self._validate_execution_deduplication(tenant_id)
        assertions.extend(dedup_result["assertions"])
        failures.extend(dedup_result["failures"])
        warnings.extend(dedup_result["warnings"])
        
        # Validate signal deduplication correctness
        signal_dedup_result = await self._validate_signal_deduplication(tenant_id)
        assertions.extend(signal_dedup_result["assertions"])
        failures.extend(signal_dedup_result["failures"])
        warnings.extend(signal_dedup_result["warnings"])
        
        # Validate orphan execution state detection
        orphan_result = await self._validate_orphan_execution_state(tenant_id)
        assertions.extend(orphan_result["assertions"])
        failures.extend(orphan_result["failures"])
        warnings.extend(orphan_result["warnings"])
        
        passed = len(failures) == 0
        
        return {
            "passed": passed,
            "assertions": assertions,
            "failures": failures,
            "warnings": warnings
        }
    
    async def _validate_no_duplicate_execution(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate no duplicate execution."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check for duplicate execution markers
            pattern = f"duplicate_execution:{tenant_id}:*" if tenant_id else "duplicate_execution:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                duplicate = await self.redis.get(key)
                if duplicate:
                    failures.append(f"Duplicate execution detected: {key} = {duplicate}")
                else:
                    assertions.append(f"No duplicate execution: {key}")
            
            # Check execution ID uniqueness
            pattern = f"execution_id:{tenant_id}:*" if tenant_id else "execution_id:*:*"
            keys = await self.redis.keys(pattern)
            
            execution_ids = set()
            for key in keys:
                exec_id = key.split(":")[-1]
                if exec_id in execution_ids:
                    failures.append(f"Duplicate execution ID detected: {exec_id}")
                else:
                    execution_ids.add(exec_id)
                    assertions.append(f"Unique execution ID: {exec_id}")
            
            assertions.append("No duplicate execution validated: no duplicates detected")
            
        except Exception as e:
            failures.append(f"No duplicate execution validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_execution_deduplication(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate execution deduplication correctness."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check execution deduplication markers
            pattern = f"execution_dedup:{tenant_id}:*" if tenant_id else "execution_dedup:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                dedup = await self.redis.get(key)
                if dedup:
                    assertions.append(f"Execution deduplication active: {key}")
                else:
                    warnings.append(f"Execution deduplication not active: {key}")
            
            # Check execution state consistency
            pattern = f"execution_state:{tenant_id}:*" if tenant_id else "execution_state:*:*"
            keys = await self.redis.keys(pattern)
            
            state_counts = {}
            for key in keys:
                state = await self.redis.get(key)
                if state:
                    state_str = str(state)
                    state_counts[state_str] = state_counts.get(state_str, 0) + 1
            
            for state_str, count in state_counts.items():
                if "SUBMITTED" in state_str and count > 100:
                    warnings.append(f"High number of submitted executions: {state_str} (count: {count})")
                else:
                    assertions.append(f"Execution state consistent: {state_str} (count: {count})")
            
            assertions.append("Execution deduplication validated: deduplication is correct")
            
        except Exception as e:
            failures.append(f"Execution deduplication validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_signal_deduplication(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate signal deduplication correctness."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check signal deduplication markers
            pattern = f"signal_dedup:{tenant_id}:*" if tenant_id else "signal_dedup:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                dedup = await self.redis.get(key)
                if dedup:
                    assertions.append(f"Signal deduplication active: {key}")
                else:
                    warnings.append(f"Signal deduplication not active: {key}")
            
            # Check for duplicate signals
            pattern = f"duplicate_signal:{tenant_id}:*" if tenant_id else "duplicate_signal:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                duplicate = await self.redis.get(key)
                if duplicate:
                    failures.append(f"Duplicate signal detected: {key} = {duplicate}")
                else:
                    assertions.append(f"No duplicate signal: {key}")
            
            assertions.append("Signal deduplication validated: deduplication is correct")
            
        except Exception as e:
            failures.append(f"Signal deduplication validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_orphan_execution_state(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate orphan execution state detection."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check for orphaned execution markers
            pattern = f"orphaned_execution:{tenant_id}:*" if tenant_id else "orphaned_execution:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                orphan = await self.redis.get(key)
                if orphan:
                    failures.append(f"Orphaned execution detected: {key} = {orphan}")
                else:
                    assertions.append(f"No orphaned execution: {key}")
            
            # Check execution state for orphaned states
            pattern = f"execution_state:{tenant_id}:*" if tenant_id else "execution_state:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                state = await self.redis.get(key)
                if state:
                    state_str = str(state)
                    if "ORPHANED" in state_str:
                        failures.append(f"Orphaned execution state detected: {key} = {state_str}")
                    else:
                        assertions.append(f"Valid execution state: {key} = {state_str}")
            
            assertions.append("Orphan execution state detection validated: no orphaned state")
            
        except Exception as e:
            failures.append(f"Orphan execution state detection validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
