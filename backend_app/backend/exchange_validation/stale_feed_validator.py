"""
Stale Feed Validator - Exchange Validation

This module validates stale feed behavior for exchange integration
in the strict algo trading platform.

Author: Principal Institutional Validation Engineer
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("stale_feed_validator")


class StaleFeedValidator:
    """
    Stale feed validator for institutional-grade validation.
    
    Validates stale feed behavior including feed latency, data freshness,
    and stale data detection.
    """
    
    def __init__(self):
        """Initialize stale feed validator."""
        self.redis = redis_manager
    
    async def validate(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run stale feed validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info("Running stale feed validation...")
        
        assertions = []
        failures = []
        warnings = []
        
        # Validate feed latency
        latency_result = await self._validate_feed_latency(tenant_id)
        assertions.extend(latency_result["assertions"])
        failures.extend(latency_result["failures"])
        warnings.extend(latency_result["warnings"])
        
        # Validate data freshness
        freshness_result = await self._validate_data_freshness(tenant_id)
        assertions.extend(freshness_result["assertions"])
        failures.extend(freshness_result["failures"])
        warnings.extend(freshness_result["warnings"])
        
        # Validate stale data detection
        detection_result = await self._validate_stale_data_detection(tenant_id)
        assertions.extend(detection_result["assertions"])
        failures.extend(detection_result["failures"])
        warnings.extend(detection_result["warnings"])
        
        passed = len(failures) == 0
        
        return {
            "passed": passed,
            "assertions": assertions,
            "failures": failures,
            "warnings": warnings
        }
    
    async def _validate_feed_latency(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate feed latency."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check feed latency metrics
            pattern = f"feed_latency:{tenant_id}:*" if tenant_id else "feed_latency:*:*"
            keys = await self.redis.keys(pattern)
            
            latencies = []
            for key in keys:
                latency = await self.redis.get(key)
                if latency:
                    latencies.append(float(latency))
            
            if latencies:
                avg_latency = sum(latencies) / len(latencies)
                max_latency = max(latencies)
                
                if avg_latency <= 100 and max_latency <= 500:
                    assertions.append(f"Feed latency healthy: avg={avg_latency:.2f}ms, max={max_latency:.2f}ms")
                elif avg_latency <= 250 and max_latency <= 1000:
                    warnings.append(f"Feed latency elevated: avg={avg_latency:.2f}ms, max={max_latency:.2f}ms")
                else:
                    failures.append(f"Feed latency critical: avg={avg_latency:.2f}ms, max={max_latency:.2f}ms")
            else:
                warnings.append("No feed latency data available")
            
            assertions.append("Feed latency validated")
            
        except Exception as e:
            failures.append(f"Feed latency validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_data_freshness(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate data freshness."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check data freshness metrics
            pattern = f"data_freshness:{tenant_id}:*" if tenant_id else "data_freshness:*:*"
            keys = await self.redis.keys(pattern)
            
            freshness_scores = []
            for key in keys:
                score = await self.redis.get(key)
                if score:
                    freshness_scores.append(float(score))
            
            if freshness_scores:
                avg_freshness = sum(freshness_scores) / len(freshness_scores)
                if avg_freshness >= 0.98:
                    assertions.append(f"Data freshness healthy: {avg_freshness:.2%}")
                elif avg_freshness >= 0.95:
                    warnings.append(f"Data freshness degraded: {avg_freshness:.2%}")
                else:
                    failures.append(f"Data freshness critical: {avg_freshness:.2%}")
            else:
                warnings.append("No data freshness data available")
            
            assertions.append("Data freshness validated")
            
        except Exception as e:
            failures.append(f"Data freshness validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_stale_data_detection(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate stale data detection."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check stale data detection metrics
            pattern = f"stale_data_detection:{tenant_id}:*" if tenant_id else "stale_data_detection:*:*"
            keys = await self.redis.keys(pattern)
            
            detection_rates = []
            for key in keys:
                rate = await self.redis.get(key)
                if rate:
                    detection_rates.append(float(rate))
            
            if detection_rates:
                avg_detection_rate = sum(detection_rates) / len(detection_rates)
                if avg_detection_rate >= 0.99:
                    assertions.append(f"Stale data detection rate healthy: {avg_detection_rate:.2%}")
                elif avg_detection_rate >= 0.95:
                    warnings.append(f"Stale data detection rate degraded: {avg_detection_rate:.2%}")
                else:
                    failures.append(f"Stale data detection rate critical: {avg_detection_rate:.2%}")
            else:
                warnings.append("No stale data detection data available")
            
            assertions.append("Stale data detection validated")
            
        except Exception as e:
            failures.append(f"Stale data detection validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}


# Global instance
_stale_feed_validator: StaleFeedValidator = None


def get_stale_feed_validator() -> StaleFeedValidator:
    """Get or create stale feed validator instance."""
    global _stale_feed_validator
    if _stale_feed_validator is None:
        _stale_feed_validator = StaleFeedValidator()
    return _stale_feed_validator
