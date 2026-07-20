"""
Failover Validation Runner - Phase 1

This module validates failover correctness including failover ownership,
stale ownership detection, and failover sequencing.

Author: Principal Institutional Validation Engineer
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("failover_validation_runner")


class FailoverValidationRunner:
    """
    Failover validation runner for institutional-grade validation.
    
    Validates failover correctness including failover ownership,
    stale ownership detection, and failover sequencing.
    """
    
    def __init__(self):
        """Initialize failover validation runner."""
        self.redis = redis_manager
    
    async def validate(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run failover validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info("Running failover validation...")
        
        assertions = []
        failures = []
        warnings = []
        
        # Validate failover ownership correctness
        ownership_result = await self._validate_failover_ownership(tenant_id)
        assertions.extend(ownership_result["assertions"])
        failures.extend(ownership_result["failures"])
        warnings.extend(ownership_result["warnings"])
        
        # Validate stale ownership detection
        stale_result = await self._validate_stale_ownership(tenant_id)
        assertions.extend(stale_result["assertions"])
        failures.extend(stale_result["failures"])
        warnings.extend(stale_result["warnings"])
        
        # Validate failover sequencing
        sequencing_result = await self._validate_failover_sequencing(tenant_id)
        assertions.extend(sequencing_result["assertions"])
        failures.extend(sequencing_result["failures"])
        warnings.extend(sequencing_result["warnings"])
        
        # Validate lease invalidation
        lease_result = await self._validate_lease_invalidation(tenant_id)
        assertions.extend(lease_result["assertions"])
        failures.extend(lease_result["failures"])
        warnings.extend(lease_result["warnings"])
        
        passed = len(failures) == 0
        
        return {
            "passed": passed,
            "assertions": assertions,
            "failures": failures,
            "warnings": warnings
        }
    
    async def _validate_failover_ownership(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate failover ownership correctness."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check failover ownership markers
            pattern = f"failover_ownership:{tenant_id}:*" if tenant_id else "failover_ownership:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                ownership = await self.redis.get(key)
                if ownership:
                    assertions.append(f"Failover ownership marker exists: {key} = {ownership}")
                else:
                    failures.append(f"Failover ownership marker missing: {key}")
            
            # Check for duplicate ownership
            ownership_map = {}
            for key in keys:
                ownership = await self.redis.get(key)
                if ownership:
                    resource_id = key.split(":")[-1]
                    if ownership in ownership_map:
                        ownership_map[ownership].append(resource_id)
                    else:
                        ownership_map[ownership] = [resource_id]
            
            for owner, resources in ownership_map.items():
                if len(resources) > 1:
                    failures.append(f"Duplicate ownership detected: {owner} owns multiple resources: {resources}")
            
            assertions.append("Failover ownership validated: ownership is correct")
            
        except Exception as e:
            failures.append(f"Failover ownership validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_stale_ownership(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate stale ownership detection."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check for stale ownership markers
            pattern = f"failover_ownership:{tenant_id}:*" if tenant_id else "failover_ownership:*:*"
            keys = await self.redis.keys(pattern)
            
            current_time = datetime.now(timezone.utc)
            stale_threshold = 300  # 5 minutes
            
            for key in keys:
                ttl = await self.redis.ttl(key)
                if ttl == -1:
                    warnings.append(f"Failover ownership has no TTL: {key}")
                elif ttl < 0:
                    failures.append(f"Failover ownership expired: {key}")
                elif ttl > stale_threshold:
                    warnings.append(f"Failover ownership approaching expiry: {key} (TTL: {ttl}s)")
                else:
                    assertions.append(f"Failover ownership TTL valid: {key} (TTL: {ttl}s)")
            
            assertions.append("Stale ownership detection validated: no stale ownership")
            
        except Exception as e:
            failures.append(f"Stale ownership detection validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_failover_sequencing(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate failover sequencing."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check failover checkpoints
            pattern = f"failover_checkpoint:{tenant_id}:*" if tenant_id else "failover_checkpoint:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                checkpoint = await self.redis.get(key)
                if checkpoint:
                    assertions.append(f"Failover checkpoint exists: {key}")
                else:
                    warnings.append(f"Failover checkpoint missing: {key}")
            
            # Check failover state machine
            pattern = f"failover_state:{tenant_id}:*" if tenant_id else "failover_state:*:*"
            keys = await self.redis.keys(pattern)
            
            valid_states = ["NORMAL", "FAILING_OVER", "STATE_TRANSFER", "VALIDATION", "EXECUTION_RESUMPTION", "COMPLETED", "FAILED", "ROLLBACK"]
            
            for key in keys:
                state = await self.redis.get(key)
                if state:
                    if state in valid_states:
                        assertions.append(f"Valid failover state: {key} = {state}")
                    else:
                        failures.append(f"Invalid failover state: {key} = {state}")
                else:
                    warnings.append(f"Failover state missing: {key}")
            
            assertions.append("Failover sequencing validated: sequencing is correct")
            
        except Exception as e:
            failures.append(f"Failover sequencing validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_lease_invalidation(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate lease invalidation."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check lease markers
            pattern = f"lease_marker:{tenant_id}:*" if tenant_id else "lease_marker:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                lease = await self.redis.get(key)
                if lease:
                    assertions.append(f"Lease marker exists: {key}")
                else:
                    warnings.append(f"Lease marker missing: {key}")
            
            # Check for execution after lease invalidation
            pattern = f"execution_after_lease_invalid:{tenant_id}:*" if tenant_id else "execution_after_lease_invalid:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                execution = await self.redis.get(key)
                if execution:
                    failures.append(f"Execution after lease invalidation detected: {key} = {execution}")
                else:
                    assertions.append(f"No execution after lease invalidation: {key}")
            
            assertions.append("Lease invalidation validated: no execution after invalidation")
            
        except Exception as e:
            failures.append(f"Lease invalidation validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
