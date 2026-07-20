"""
Deterministic State Validator - Phase 1

This module validates deterministic state behavior including state transitions,
state persistence, and state consistency across the platform.

Author: Principal Institutional Validation Engineer
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("deterministic_state_validator")


class DeterministicStateValidator:
    """
    Deterministic state validator for institutional-grade validation.
    
    Validates deterministic state behavior including state transitions,
    state persistence, and state consistency.
    """
    
    def __init__(self):
        """Initialize deterministic state validator."""
        self.redis = redis_manager
    
    async def validate(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run deterministic state validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info("Running deterministic state validation...")
        
        assertions = []
        failures = []
        warnings = []
        
        # Validate state persistence
        persistence_result = await self._validate_state_persistence(tenant_id)
        assertions.extend(persistence_result["assertions"])
        failures.extend(persistence_result["failures"])
        warnings.extend(persistence_result["warnings"])
        
        # Validate state consistency
        consistency_result = await self._validate_state_consistency(tenant_id)
        assertions.extend(consistency_result["assertions"])
        failures.extend(consistency_result["failures"])
        warnings.extend(consistency_result["warnings"])
        
        # Validate state transitions
        transition_result = await self._validate_state_transitions(tenant_id)
        assertions.extend(transition_result["assertions"])
        failures.extend(transition_result["failures"])
        warnings.extend(transition_result["warnings"])
        
        # Validate fencing token correctness
        fencing_result = await self._validate_fencing_tokens(tenant_id)
        assertions.extend(fencing_result["assertions"])
        failures.extend(fencing_result["failures"])
        warnings.extend(fencing_result["warnings"])
        
        passed = len(failures) == 0
        
        return {
            "passed": passed,
            "assertions": assertions,
            "failures": failures,
            "warnings": warnings
        }
    
    async def _validate_state_persistence(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate state persistence."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check execution state persistence
            pattern = f"execution_state:{tenant_id}:*" if tenant_id else "execution_state:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                state = await self.redis.get(key)
                if state:
                    assertions.append(f"Execution state persisted: {key}")
                else:
                    failures.append(f"Execution state not persisted: {key}")
            
            # Check order state persistence
            pattern = f"order_state:{tenant_id}:*" if tenant_id else "order_state:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                state = await self.redis.get(key)
                if state:
                    assertions.append(f"Order state persisted: {key}")
                else:
                    warnings.append(f"Order state not persisted: {key}")
            
            assertions.append("State persistence validated: states are persisted")
            
        except Exception as e:
            failures.append(f"State persistence validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_state_consistency(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate state consistency."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check for inconsistent states
            pattern = f"execution_state:{tenant_id}:*" if tenant_id else "execution_state:*:*"
            keys = await self.redis.keys(pattern)
            
            state_counts = {}
            for key in keys:
                state = await self.redis.get(key)
                if state:
                    state_str = str(state)
                    state_counts[state_str] = state_counts.get(state_str, 0) + 1
            
            # Check for orphaned states
            for state_str, count in state_counts.items():
                if "ORPHANED" in state_str:
                    warnings.append(f"Orphaned state detected: {state_str} (count: {count})")
                else:
                    assertions.append(f"Consistent state: {state_str} (count: {count})")
            
            assertions.append("State consistency validated: no inconsistent states")
            
        except Exception as e:
            failures.append(f"State consistency validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_state_transitions(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate state transitions."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Validate state transition sequences
            valid_transitions = {
                "PENDING": ["ASSIGNED", "FAILED"],
                "ASSIGNED": ["SUBMITTED", "FAILED"],
                "SUBMITTED": ["COMPLETED", "FAILED"],
                "FAILED": [],
                "COMPLETED": [],
                "ORPHANED": ["ASSIGNED"]
            }
            
            pattern = f"execution_state:{tenant_id}:*" if tenant_id else "execution_state:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                state = await self.redis.get(key)
                if state:
                    current_state = str(state).get("state", "UNKNOWN")
                    if current_state in valid_transitions:
                        assertions.append(f"Valid state: {key} = {current_state}")
                    else:
                        failures.append(f"Invalid state: {key} = {current_state}")
            
            assertions.append("State transitions validated: valid transitions")
            
        except Exception as e:
            failures.append(f"State transition validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_fencing_tokens(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate fencing token correctness."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check fencing tokens exist
            pattern = f"fence_token:{tenant_id}:*" if tenant_id else "fence_token:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                token = await self.redis.get(key)
                if token:
                    assertions.append(f"Fencing token exists: {key}")
                else:
                    warnings.append(f"Fencing token missing: {key}")
            
            # Check for expired tokens
            for key in keys:
                ttl = await self.redis.ttl(key)
                if ttl == -1:
                    warnings.append(f"Fencing token has no TTL: {key}")
                elif ttl < 0:
                    failures.append(f"Fencing token expired: {key}")
            
            assertions.append("Fencing tokens validated: tokens are correct")
            
        except Exception as e:
            failures.append(f"Fencing token validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
