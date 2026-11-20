"""
Partial Fill Validator - Exchange Validation

This module validates partial fill behavior for exchange integration
in the strict algo trading platform.

Author: Principal Institutional Validation Engineer
"""

import logging
from typing import Any, Dict, Optional

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("partial_fill_validator")


class PartialFillValidator:
    """
    Partial fill validator for institutional-grade validation.
    
    Validates partial fill behavior including fill ratios, fill timing,
    and partial fill handling.
    """
    
    def __init__(self):
        """Initialize partial fill validator."""
        self.redis = redis_manager
    
    async def validate(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run partial fill validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info("Running partial fill validation...")
        
        assertions = []
        failures = []
        warnings = []
        
        # Validate fill ratio
        fill_ratio_result = await self._validate_fill_ratio(tenant_id)
        assertions.extend(fill_ratio_result["assertions"])
        failures.extend(fill_ratio_result["failures"])
        warnings.extend(fill_ratio_result["warnings"])
        
        # Validate fill timing
        fill_timing_result = await self._validate_fill_timing(tenant_id)
        assertions.extend(fill_timing_result["assertions"])
        failures.extend(fill_timing_result["failures"])
        warnings.extend(fill_timing_result["warnings"])
        
        # Validate partial fill handling
        handling_result = await self._validate_partial_fill_handling(tenant_id)
        assertions.extend(handling_result["assertions"])
        failures.extend(handling_result["failures"])
        warnings.extend(handling_result["warnings"])
        
        passed = len(failures) == 0
        
        return {
            "passed": passed,
            "assertions": assertions,
            "failures": failures,
            "warnings": warnings
        }
    
    async def _validate_fill_ratio(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate fill ratio."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check fill ratio metrics
            pattern = f"fill_ratio:{tenant_id}:*" if tenant_id else "fill_ratio:*:*"
            keys = await self.redis.keys(pattern)
            
            fill_ratios = []
            for key in keys:
                ratio = await self.redis.get(key)
                if ratio:
                    fill_ratios.append(float(ratio))
            
            if fill_ratios:
                avg_fill_ratio = sum(fill_ratios) / len(fill_ratios)
                min_fill_ratio = min(fill_ratios)
                
                if avg_fill_ratio >= 0.95 and min_fill_ratio >= 0.90:
                    assertions.append(f"Fill ratio healthy: avg={avg_fill_ratio:.2%}, min={min_fill_ratio:.2%}")
                elif avg_fill_ratio >= 0.85 and min_fill_ratio >= 0.80:
                    warnings.append(f"Fill ratio degraded: avg={avg_fill_ratio:.2%}, min={min_fill_ratio:.2%}")
                else:
                    failures.append(f"Fill ratio critical: avg={avg_fill_ratio:.2%}, min={min_fill_ratio:.2%}")
            else:
                warnings.append("No fill ratio data available")
            
            assertions.append("Fill ratio validated")
            
        except Exception as e:
            failures.append(f"Fill ratio validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_fill_timing(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate fill timing."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check fill timing metrics
            pattern = f"fill_timing:{tenant_id}:*" if tenant_id else "fill_timing:*:*"
            keys = await self.redis.keys(pattern)
            
            fill_times = []
            for key in keys:
                timing = await self.redis.get(key)
                if timing:
                    fill_times.append(float(timing))
            
            if fill_times:
                avg_fill_time = sum(fill_times) / len(fill_times)
                max_fill_time = max(fill_times)
                
                if avg_fill_time <= 2000 and max_fill_time <= 10000:
                    assertions.append(f"Fill timing healthy: avg={avg_fill_time:.2f}ms, max={max_fill_time:.2f}ms")
                elif avg_fill_time <= 5000 and max_fill_time <= 30000:
                    warnings.append(f"Fill timing elevated: avg={avg_fill_time:.2f}ms, max={max_fill_time:.2f}ms")
                else:
                    failures.append(f"Fill timing critical: avg={avg_fill_time:.2f}ms, max={max_fill_time:.2f}ms")
            else:
                warnings.append("No fill timing data available")
            
            assertions.append("Fill timing validated")
            
        except Exception as e:
            failures.append(f"Fill timing validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_partial_fill_handling(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate partial fill handling."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check partial fill handling metrics
            pattern = f"partial_fill_handling:{tenant_id}:*" if tenant_id else "partial_fill_handling:*:*"
            keys = await self.redis.keys(pattern)
            
            handling_rates = []
            for key in keys:
                rate = await self.redis.get(key)
                if rate:
                    handling_rates.append(float(rate))
            
            if handling_rates:
                avg_handling_rate = sum(handling_rates) / len(handling_rates)
                if avg_handling_rate >= 0.98:
                    assertions.append(f"Partial fill handling rate healthy: {avg_handling_rate:.2%}")
                elif avg_handling_rate >= 0.95:
                    warnings.append(f"Partial fill handling rate degraded: {avg_handling_rate:.2%}")
                else:
                    failures.append(f"Partial fill handling rate critical: {avg_handling_rate:.2%}")
            else:
                warnings.append("No partial fill handling data available")
            
            assertions.append("Partial fill handling validated")
            
        except Exception as e:
            failures.append(f"Partial fill handling validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}


# Global instance
_partial_fill_validator: PartialFillValidator = None


def get_partial_fill_validator() -> PartialFillValidator:
    """Get or create partial fill validator instance."""
    global _partial_fill_validator
    if _partial_fill_validator is None:
        _partial_fill_validator = PartialFillValidator()
    return _partial_fill_validator
