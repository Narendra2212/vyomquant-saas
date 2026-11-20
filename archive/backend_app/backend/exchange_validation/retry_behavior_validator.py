"""
Retry Behavior Validator - Exchange Validation

This module validates retry behavior for exchange integration
in the strict algo trading platform.

Author: Principal Institutional Validation Engineer
"""

import logging
from typing import Any, Dict, Optional

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("retry_behavior_validator")


class RetryBehaviorValidator:
    """
    Retry behavior validator for institutional-grade validation.
    
    Validates retry behavior including retry frequency, retry success rate,
    and retry backoff behavior.
    """
    
    def __init__(self):
        """Initialize retry behavior validator."""
        self.redis = redis_manager
    
    async def validate(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run retry behavior validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info("Running retry behavior validation...")
        
        assertions = []
        failures = []
        warnings = []
        
        # Validate retry frequency
        frequency_result = await self._validate_retry_frequency(tenant_id)
        assertions.extend(frequency_result["assertions"])
        failures.extend(frequency_result["failures"])
        warnings.extend(frequency_result["warnings"])
        
        # Validate retry success rate
        success_rate_result = await self._validate_retry_success_rate(tenant_id)
        assertions.extend(success_rate_result["assertions"])
        failures.extend(success_rate_result["failures"])
        warnings.extend(success_rate_result["warnings"])
        
        # Validate retry backoff behavior
        backoff_result = await self._validate_retry_backoff(tenant_id)
        assertions.extend(backoff_result["assertions"])
        failures.extend(backoff_result["failures"])
        warnings.extend(backoff_result["warnings"])
        
        passed = len(failures) == 0
        
        return {
            "passed": passed,
            "assertions": assertions,
            "failures": failures,
            "warnings": warnings
        }
    
    async def _validate_retry_frequency(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate retry frequency."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check retry frequency metrics
            pattern = f"retry_frequency:{tenant_id}:*" if tenant_id else "retry_frequency:*:*"
            keys = await self.redis.keys(pattern)
            
            retry_rates = []
            for key in keys:
                rate = await self.redis.get(key)
                if rate:
                    retry_rates.append(float(rate))
            
            if retry_rates:
                avg_retry_rate = sum(retry_rates) / len(retry_rates)
                max_retry_rate = max(retry_rates)
                
                if avg_retry_rate <= 0.05 and max_retry_rate <= 0.10:
                    assertions.append(f"Retry frequency healthy: avg={avg_retry_rate:.2%}, max={max_retry_rate:.2%}")
                elif avg_retry_rate <= 0.10 and max_retry_rate <= 0.20:
                    warnings.append(f"Retry frequency elevated: avg={avg_retry_rate:.2%}, max={max_retry_rate:.2%}")
                else:
                    failures.append(f"Retry frequency critical: avg={avg_retry_rate:.2%}, max={max_retry_rate:.2%}")
            else:
                warnings.append("No retry frequency data available")
            
            assertions.append("Retry frequency validated")
            
        except Exception as e:
            failures.append(f"Retry frequency validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_retry_success_rate(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate retry success rate."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check retry success rate metrics
            pattern = f"retry_success_rate:{tenant_id}:*" if tenant_id else "retry_success_rate:*:*"
            keys = await self.redis.keys(pattern)
            
            success_rates = []
            for key in keys:
                rate = await self.redis.get(key)
                if rate:
                    success_rates.append(float(rate))
            
            if success_rates:
                avg_success_rate = sum(success_rates) / len(success_rates)
                if avg_success_rate >= 0.90:
                    assertions.append(f"Retry success rate healthy: {avg_success_rate:.2%}")
                elif avg_success_rate >= 0.80:
                    warnings.append(f"Retry success rate degraded: {avg_success_rate:.2%}")
                else:
                    failures.append(f"Retry success rate critical: {avg_success_rate:.2%}")
            else:
                warnings.append("No retry success rate data available")
            
            assertions.append("Retry success rate validated")
            
        except Exception as e:
            failures.append(f"Retry success rate validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_retry_backoff(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate retry backoff behavior."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check retry backoff metrics
            pattern = f"retry_backoff:{tenant_id}:*" if tenant_id else "retry_backoff:*:*"
            keys = await self.redis.keys(pattern)
            
            backoff_times = []
            for key in keys:
                backoff = await self.redis.get(key)
                if backoff:
                    backoff_times.append(float(backoff))
            
            if backoff_times:
                avg_backoff = sum(backoff_times) / len(backoff_times)
                max_backoff = max(backoff_times)
                
                if avg_backoff >= 1000 and max_backoff <= 30000:
                    assertions.append(f"Retry backoff healthy: avg={avg_backoff:.2f}ms, max={max_backoff:.2f}ms")
                elif avg_backoff >= 500 and max_backoff <= 60000:
                    warnings.append(f"Retry backoff suboptimal: avg={avg_backoff:.2f}ms, max={max_backoff:.2f}ms")
                else:
                    failures.append(f"Retry backoff critical: avg={avg_backoff:.2f}ms, max={max_backoff:.2f}ms")
            else:
                warnings.append("No retry backoff data available")
            
            assertions.append("Retry backoff validated")
            
        except Exception as e:
            failures.append(f"Retry backoff validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}


# Global instance
_retry_behavior_validator: RetryBehaviorValidator = None


def get_retry_behavior_validator() -> RetryBehaviorValidator:
    """Get or create retry behavior validator instance."""
    global _retry_behavior_validator
    if _retry_behavior_validator is None:
        _retry_behavior_validator = RetryBehaviorValidator()
    return _retry_behavior_validator
