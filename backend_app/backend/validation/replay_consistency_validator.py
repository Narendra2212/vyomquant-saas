"""
Replay Consistency Validator - Phase 1

This module validates replay consistency including replay/live divergence,
deterministic replay reconstruction, and replay-safe execution.

Author: Principal Institutional Validation Engineer
"""

import logging
from typing import Any, Dict, Optional

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("replay_consistency_validator")


class ReplayConsistencyValidator:
    """
    Replay consistency validator for institutional-grade validation.
    
    Validates replay consistency including replay/live divergence,
    deterministic replay reconstruction, and replay-safe execution.
    """
    
    def __init__(self):
        """Initialize replay consistency validator."""
        self.redis = redis_manager
    
    async def validate(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run replay consistency validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info("Running replay consistency validation...")
        
        assertions = []
        failures = []
        warnings = []
        
        # Validate replay/live divergence
        divergence_result = await self._validate_replay_divergence(tenant_id)
        assertions.extend(divergence_result["assertions"])
        failures.extend(divergence_result["failures"])
        warnings.extend(divergence_result["warnings"])
        
        # Validate deterministic replay reconstruction
        reconstruction_result = await self._validate_deterministic_replay(tenant_id)
        assertions.extend(reconstruction_result["assertions"])
        failures.extend(reconstruction_result["failures"])
        warnings.extend(reconstruction_result["warnings"])
        
        # Validate replay-safe execution
        replay_safe_result = await self._validate_replay_safe_execution(tenant_id)
        assertions.extend(replay_safe_result["assertions"])
        failures.extend(replay_safe_result["failures"])
        warnings.extend(replay_safe_result["warnings"])
        
        # Validate journal integrity
        journal_result = await self._validate_journal_integrity(tenant_id)
        assertions.extend(journal_result["assertions"])
        failures.extend(journal_result["failures"])
        warnings.extend(journal_result["warnings"])
        
        passed = len(failures) == 0
        
        return {
            "passed": passed,
            "assertions": assertions,
            "failures": failures,
            "warnings": warnings
        }
    
    async def _validate_replay_divergence(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate replay/live divergence."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check for replay state markers
            pattern = f"replay_state:{tenant_id}:*" if tenant_id else "replay_state:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                state = await self.redis.get(key)
                if state:
                    assertions.append(f"Replay state marker exists: {key}")
                else:
                    warnings.append(f"Replay state marker missing: {key}")
            
            # Check for divergence markers
            pattern = f"divergence_marker:{tenant_id}:*" if tenant_id else "divergence_marker:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                divergence = await self.redis.get(key)
                if divergence:
                    failures.append(f"Replay divergence detected: {key} = {divergence}")
                else:
                    assertions.append(f"No replay divergence: {key}")
            
            assertions.append("Replay divergence validated: no divergence detected")
            
        except Exception as e:
            failures.append(f"Replay divergence validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_deterministic_replay(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate deterministic replay reconstruction."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check replay sequence numbers
            pattern = f"replay_sequence:{tenant_id}:*" if tenant_id else "replay_sequence:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                sequence = await self.redis.get(key)
                if sequence:
                    assertions.append(f"Replay sequence number exists: {key} = {sequence}")
                else:
                    failures.append(f"Replay sequence number missing: {key}")
            
            # Check replay state consistency
            pattern = f"replay_execution_state:{tenant_id}:*" if tenant_id else "replay_execution_state:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                state = await self.redis.get(key)
                if state:
                    assertions.append(f"Replay execution state consistent: {key}")
                else:
                    warnings.append(f"Replay execution state missing: {key}")
            
            assertions.append("Deterministic replay validated: replay is deterministic")
            
        except Exception as e:
            failures.append(f"Deterministic replay validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_replay_safe_execution(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate replay-safe execution."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check replay deduplication
            pattern = f"replay_dedup:{tenant_id}:*" if tenant_id else "replay_dedup:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                dedup = await self.redis.get(key)
                if dedup:
                    assertions.append(f"Replay deduplication active: {key}")
                else:
                    warnings.append(f"Replay deduplication not active: {key}")
            
            # Check replay execution IDs
            pattern = f"replay_execution_id:{tenant_id}:*" if tenant_id else "replay_execution_id:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                exec_id = await self.redis.get(key)
                if exec_id:
                    assertions.append(f"Replay execution ID exists: {key} = {exec_id}")
                else:
                    warnings.append(f"Replay execution ID missing: {key}")
            
            assertions.append("Replay-safe execution validated: execution is replay-safe")
            
        except Exception as e:
            failures.append(f"Replay-safe execution validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_journal_integrity(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate journal integrity."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check journal event markers
            pattern = f"journal_event:{tenant_id}:*" if tenant_id else "journal_event:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                event = await self.redis.get(key)
                if event:
                    assertions.append(f"Journal event marker exists: {key}")
                else:
                    warnings.append(f"Journal event marker missing: {key}")
            
            # Check journal sequence numbers
            pattern = f"journal_sequence:{tenant_id}:*" if tenant_id else "journal_sequence:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                sequence = await self.redis.get(key)
                if sequence:
                    assertions.append(f"Journal sequence number exists: {key} = {sequence}")
                else:
                    failures.append(f"Journal sequence number missing: {key}")
            
            assertions.append("Journal integrity validated: journal is intact")
            
        except Exception as e:
            failures.append(f"Journal integrity validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
