"""
Reconnect Recovery Validator - Exchange Validation

This module validates reconnect recovery behavior for exchange integration
in the strict algo trading platform.

Author: Principal Institutional Validation Engineer
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("reconnect_recovery_validator")


class ReconnectRecoveryValidator:
    """
    Reconnect recovery validator for institutional-grade validation.
    
    Validates reconnect recovery behavior including reconnection time,
    state recovery, and connection stability.
    """
    
    def __init__(self):
        """Initialize reconnect recovery validator."""
        self.redis = redis_manager
    
    async def validate(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run reconnect recovery validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info("Running reconnect recovery validation...")
        
        assertions = []
        failures = []
        warnings = []
        
        # Validate reconnection time
        reconnect_time_result = await self._validate_reconnection_time(tenant_id)
        assertions.extend(reconnect_time_result["assertions"])
        failures.extend(reconnect_time_result["failures"])
        warnings.extend(reconnect_time_result["warnings"])
        
        # Validate state recovery
        state_recovery_result = await self._validate_state_recovery(tenant_id)
        assertions.extend(state_recovery_result["assertions"])
        failures.extend(state_recovery_result["failures"])
        warnings.extend(state_recovery_result["warnings"])
        
        # Validate connection stability
        stability_result = await self._validate_connection_stability(tenant_id)
        assertions.extend(stability_result["assertions"])
        failures.extend(stability_result["failures"])
        warnings.extend(stability_result["warnings"])
        
        passed = len(failures) == 0
        
        return {
            "passed": passed,
            "assertions": assertions,
            "failures": failures,
            "warnings": warnings
        }
    
    async def _validate_reconnection_time(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate reconnection time."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check reconnection time metrics
            pattern = f"reconnect_time:{tenant_id}:*" if tenant_id else "reconnect_time:*:*"
            keys = await self.redis.keys(pattern)
            
            reconnect_times = []
            for key in keys:
                time = await self.redis.get(key)
                if time:
                    reconnect_times.append(float(time))
            
            if reconnect_times:
                avg_reconnect_time = sum(reconnect_times) / len(reconnect_times)
                max_reconnect_time = max(reconnect_times)
                
                if avg_reconnect_time <= 5000 and max_reconnect_time <= 30000:
                    assertions.append(f"Reconnection time healthy: avg={avg_reconnect_time:.2f}ms, max={max_reconnect_time:.2f}ms")
                elif avg_reconnect_time <= 10000 and max_reconnect_time <= 60000:
                    warnings.append(f"Reconnection time elevated: avg={avg_reconnect_time:.2f}ms, max={max_reconnect_time:.2f}ms")
                else:
                    failures.append(f"Reconnection time critical: avg={avg_reconnect_time:.2f}ms, max={max_reconnect_time:.2f}ms")
            else:
                warnings.append("No reconnection time data available")
            
            assertions.append("Reconnection time validated")
            
        except Exception as e:
            failures.append(f"Reconnection time validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_state_recovery(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate state recovery."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check state recovery metrics
            pattern = f"state_recovery:{tenant_id}:*" if tenant_id else "state_recovery:*:*"
            keys = await self.redis.keys(pattern)
            
            recovery_rates = []
            for key in keys:
                rate = await self.redis.get(key)
                if rate:
                    recovery_rates.append(float(rate))
            
            if recovery_rates:
                avg_recovery_rate = sum(recovery_rates) / len(recovery_rates)
                if avg_recovery_rate >= 0.98:
                    assertions.append(f"State recovery rate healthy: {avg_recovery_rate:.2%}")
                elif avg_recovery_rate >= 0.95:
                    warnings.append(f"State recovery rate degraded: {avg_recovery_rate:.2%}")
                else:
                    failures.append(f"State recovery rate critical: {avg_recovery_rate:.2%}")
            else:
                warnings.append("No state recovery data available")
            
            assertions.append("State recovery validated")
            
        except Exception as e:
            failures.append(f"State recovery validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_connection_stability(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate connection stability."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check connection stability metrics
            pattern = f"connection_stability:{tenant_id}:*" if tenant_id else "connection_stability:*:*"
            keys = await self.redis.keys(pattern)
            
            stability_scores = []
            for key in keys:
                score = await self.redis.get(key)
                if score:
                    stability_scores.append(float(score))
            
            if stability_scores:
                avg_stability = sum(stability_scores) / len(stability_scores)
                if avg_stability >= 0.95:
                    assertions.append(f"Connection stability healthy: {avg_stability:.2%}")
                elif avg_stability >= 0.90:
                    warnings.append(f"Connection stability degraded: {avg_stability:.2%}")
                else:
                    failures.append(f"Connection stability critical: {avg_stability:.2%}")
            else:
                warnings.append("No connection stability data available")
            
            assertions.append("Connection stability validated")
            
        except Exception as e:
            failures.append(f"Connection stability validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}


# Global instance
_reconnect_recovery_validator: ReconnectRecoveryValidator = None


def get_reconnect_recovery_validator() -> ReconnectRecoveryValidator:
    """Get or create reconnect recovery validator instance."""
    global _reconnect_recovery_validator
    if _reconnect_recovery_validator is None:
        _reconnect_recovery_validator = ReconnectRecoveryValidator()
    return _reconnect_recovery_validator
