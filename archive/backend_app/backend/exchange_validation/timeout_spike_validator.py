"""
Timeout Spike Validator - Exchange Validation

This module validates timeout spike behavior for exchange integration
in the strict algo trading platform.

Author: Principal Institutional Validation Engineer
"""

import logging
from typing import Any, Dict, Optional

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("timeout_spike_validator")


class TimeoutSpikeValidator:
    """
    Timeout spike validator for institutional-grade validation.
    
    Validates timeout spike behavior including timeout frequency,
    spike duration, and timeout recovery.
    """
    
    def __init__(self):
        """Initialize timeout spike validator."""
        self.redis = redis_manager
    
    async def validate(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run timeout spike validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info("Running timeout spike validation...")
        
        assertions = []
        failures = []
        warnings = []
        
        # Validate timeout frequency
        frequency_result = await self._validate_timeout_frequency(tenant_id)
        assertions.extend(frequency_result["assertions"])
        failures.extend(frequency_result["failures"])
        warnings.extend(frequency_result["warnings"])
        
        # Validate spike duration
        duration_result = await self._validate_spike_duration(tenant_id)
        assertions.extend(duration_result["assertions"])
        failures.extend(duration_result["failures"])
        warnings.extend(duration_result["warnings"])
        
        # Validate timeout recovery
        recovery_result = await self._validate_timeout_recovery(tenant_id)
        assertions.extend(recovery_result["assertions"])
        failures.extend(recovery_result["failures"])
        warnings.extend(recovery_result["warnings"])
        
        passed = len(failures) == 0
        
        return {
            "passed": passed,
            "assertions": assertions,
            "failures": failures,
            "warnings": warnings
        }
    
    async def _validate_timeout_frequency(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate timeout frequency."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check timeout frequency metrics
            pattern = f"timeout_frequency:{tenant_id}:*" if tenant_id else "timeout_frequency:*:*"
            keys = await self.redis.keys(pattern)
            
            timeout_rates = []
            for key in keys:
                rate = await self.redis.get(key)
                if rate:
                    timeout_rates.append(float(rate))
            
            if timeout_rates:
                avg_timeout_rate = sum(timeout_rates) / len(timeout_rates)
                max_timeout_rate = max(timeout_rates)
                
                if avg_timeout_rate <= 0.01 and max_timeout_rate <= 0.05:
                    assertions.append(f"Timeout frequency healthy: avg={avg_timeout_rate:.2%}, max={max_timeout_rate:.2%}")
                elif avg_timeout_rate <= 0.05 and max_timeout_rate <= 0.10:
                    warnings.append(f"Timeout frequency elevated: avg={avg_timeout_rate:.2%}, max={max_timeout_rate:.2%}")
                else:
                    failures.append(f"Timeout frequency critical: avg={avg_timeout_rate:.2%}, max={max_timeout_rate:.2%}")
            else:
                warnings.append("No timeout frequency data available")
            
            assertions.append("Timeout frequency validated")
            
        except Exception as e:
            failures.append(f"Timeout frequency validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_spike_duration(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate spike duration."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check spike duration metrics
            pattern = f"spike_duration:{tenant_id}:*" if tenant_id else "spike_duration:*:*"
            keys = await self.redis.keys(pattern)
            
            spike_durations = []
            for key in keys:
                duration = await self.redis.get(key)
                if duration:
                    spike_durations.append(float(duration))
            
            if spike_durations:
                avg_spike_duration = sum(spike_durations) / len(spike_durations)
                max_spike_duration = max(spike_durations)
                
                if avg_spike_duration <= 5000 and max_spike_duration <= 30000:
                    assertions.append(f"Spike duration healthy: avg={avg_spike_duration:.2f}ms, max={max_spike_duration:.2f}ms")
                elif avg_spike_duration <= 10000 and max_spike_duration <= 60000:
                    warnings.append(f"Spike duration elevated: avg={avg_spike_duration:.2f}ms, max={max_spike_duration:.2f}ms")
                else:
                    failures.append(f"Spike duration critical: avg={avg_spike_duration:.2f}ms, max={max_spike_duration:.2f}ms")
            else:
                warnings.append("No spike duration data available")
            
            assertions.append("Spike duration validated")
            
        except Exception as e:
            failures.append(f"Spike duration validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_timeout_recovery(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate timeout recovery."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check timeout recovery metrics
            pattern = f"timeout_recovery:{tenant_id}:*" if tenant_id else "timeout_recovery:*:*"
            keys = await self.redis.keys(pattern)
            
            recovery_rates = []
            for key in keys:
                rate = await self.redis.get(key)
                if rate:
                    recovery_rates.append(float(rate))
            
            if recovery_rates:
                avg_recovery_rate = sum(recovery_rates) / len(recovery_rates)
                if avg_recovery_rate >= 0.95:
                    assertions.append(f"Timeout recovery rate healthy: {avg_recovery_rate:.2%}")
                elif avg_recovery_rate >= 0.90:
                    warnings.append(f"Timeout recovery rate degraded: {avg_recovery_rate:.2%}")
                else:
                    failures.append(f"Timeout recovery rate critical: {avg_recovery_rate:.2%}")
            else:
                warnings.append("No timeout recovery data available")
            
            assertions.append("Timeout recovery validated")
            
        except Exception as e:
            failures.append(f"Timeout recovery validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}


# Global instance
_timeout_spike_validator: TimeoutSpikeValidator = None


def get_timeout_spike_validator() -> TimeoutSpikeValidator:
    """Get or create timeout spike validator instance."""
    global _timeout_spike_validator
    if _timeout_spike_validator is None:
        _timeout_spike_validator = TimeoutSpikeValidator()
    return _timeout_spike_validator
