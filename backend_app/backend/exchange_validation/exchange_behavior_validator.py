"""
Exchange Behavior Validator - Exchange Validation

This module validates exchange behavior for the strict algo trading platform,
ensuring exchanges behave according to expected specifications.

Author: Principal Institutional Validation Engineer
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("exchange_behavior_validator")


class ExchangeBehaviorValidator:
    """
    Exchange behavior validator for institutional-grade validation.
    
    Validates exchange behavior including order acceptance, execution,
    and response patterns.
    """
    
    def __init__(self):
        """Initialize exchange behavior validator."""
        self.redis = redis_manager
    
    async def validate(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run exchange behavior validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info("Running exchange behavior validation...")
        
        assertions = []
        failures = []
        warnings = []
        
        # Validate order acceptance behavior
        acceptance_result = await self._validate_order_acceptance(tenant_id)
        assertions.extend(acceptance_result["assertions"])
        failures.extend(acceptance_result["failures"])
        warnings.extend(acceptance_result["warnings"])
        
        # Validate execution behavior
        execution_result = await self._validate_execution_behavior(tenant_id)
        assertions.extend(execution_result["assertions"])
        failures.extend(execution_result["failures"])
        warnings.extend(execution_result["warnings"])
        
        # Validate response time behavior
        response_result = await self._validate_response_time_behavior(tenant_id)
        assertions.extend(response_result["assertions"])
        failures.extend(response_result["failures"])
        warnings.extend(response_result["warnings"])
        
        # Validate error handling behavior
        error_result = await self._validate_error_handling_behavior(tenant_id)
        assertions.extend(error_result["assertions"])
        failures.extend(error_result["failures"])
        warnings.extend(error_result["warnings"])
        
        passed = len(failures) == 0
        
        return {
            "passed": passed,
            "assertions": assertions,
            "failures": failures,
            "warnings": warnings
        }
    
    async def _validate_order_acceptance(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate order acceptance behavior."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check order acceptance rate
            pattern = f"order_acceptance:{tenant_id}:*" if tenant_id else "order_acceptance:*:*"
            keys = await self.redis.keys(pattern)
            
            acceptance_rates = []
            for key in keys:
                rate = await self.redis.get(key)
                if rate:
                    acceptance_rates.append(float(rate))
            
            if acceptance_rates:
                avg_acceptance = sum(acceptance_rates) / len(acceptance_rates)
                if avg_acceptance >= 0.95:
                    assertions.append(f"Order acceptance rate healthy: {avg_acceptance:.2%}")
                elif avg_acceptance >= 0.90:
                    warnings.append(f"Order acceptance rate degraded: {avg_acceptance:.2%}")
                else:
                    failures.append(f"Order acceptance rate critical: {avg_acceptance:.2%}")
            else:
                warnings.append("No order acceptance data available")
            
            assertions.append("Order acceptance behavior validated")
            
        except Exception as e:
            failures.append(f"Order acceptance validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_execution_behavior(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate execution behavior."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check execution latency
            pattern = f"execution_latency:{tenant_id}:*" if tenant_id else "execution_latency:*:*"
            keys = await self.redis.keys(pattern)
            
            latencies = []
            for key in keys:
                latency = await self.redis.get(key)
                if latency:
                    latencies.append(float(latency))
            
            if latencies:
                avg_latency = sum(latencies) / len(latencies)
                if avg_latency <= 1000:  # 1 second
                    assertions.append(f"Execution latency healthy: {avg_latency:.2f}ms")
                elif avg_latency <= 5000:  # 5 seconds
                    warnings.append(f"Execution latency elevated: {avg_latency:.2f}ms")
                else:
                    failures.append(f"Execution latency critical: {avg_latency:.2f}ms")
            else:
                warnings.append("No execution latency data available")
            
            assertions.append("Execution behavior validated")
            
        except Exception as e:
            failures.append(f"Execution behavior validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_response_time_behavior(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate response time behavior."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check response time consistency
            pattern = f"response_time:{tenant_id}:*" if tenant_id else "response_time:*:*"
            keys = await self.redis.keys(pattern)
            
            response_times = []
            for key in keys:
                response_time = await self.redis.get(key)
                if response_time:
                    response_times.append(float(response_time))
            
            if response_times:
                avg_response = sum(response_times) / len(response_times)
                max_response = max(response_times)
                
                if avg_response <= 500 and max_response <= 2000:
                    assertions.append(f"Response time consistent: avg={avg_response:.2f}ms, max={max_response:.2f}ms")
                elif avg_response <= 1000 and max_response <= 5000:
                    warnings.append(f"Response time elevated: avg={avg_response:.2f}ms, max={max_response:.2f}ms")
                else:
                    failures.append(f"Response time critical: avg={avg_response:.2f}ms, max={max_response:.2f}ms")
            else:
                warnings.append("No response time data available")
            
            assertions.append("Response time behavior validated")
            
        except Exception as e:
            failures.append(f"Response time validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_error_handling_behavior(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate error handling behavior."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check error rate
            pattern = f"error_rate:{tenant_id}:*" if tenant_id else "error_rate:*:*"
            keys = await self.redis.keys(pattern)
            
            error_rates = []
            for key in keys:
                error_rate = await self.redis.get(key)
                if error_rate:
                    error_rates.append(float(error_rate))
            
            if error_rates:
                avg_error_rate = sum(error_rates) / len(error_rates)
                if avg_error_rate <= 0.01:  # 1%
                    assertions.append(f"Error rate healthy: {avg_error_rate:.2%}")
                elif avg_error_rate <= 0.05:  # 5%
                    warnings.append(f"Error rate elevated: {avg_error_rate:.2%}")
                else:
                    failures.append(f"Error rate critical: {avg_error_rate:.2%}")
            else:
                warnings.append("No error rate data available")
            
            assertions.append("Error handling behavior validated")
            
        except Exception as e:
            failures.append(f"Error handling validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}


# Global instance
_exchange_behavior_validator: ExchangeBehaviorValidator = None


def get_exchange_behavior_validator() -> ExchangeBehaviorValidator:
    """Get or create exchange behavior validator instance."""
    global _exchange_behavior_validator
    if _exchange_behavior_validator is None:
        _exchange_behavior_validator = ExchangeBehaviorValidator()
    return _exchange_behavior_validator
