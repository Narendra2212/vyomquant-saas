"""
Reconciliation Stability Runner - Exchange Validation

This module runs reconciliation stability validation for exchange integration
in the strict algo trading platform.

Author: Principal Institutional Validation Engineer
"""

import logging
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("reconciliation_stability_runner")


class ReconciliationStabilityRunner:
    """
    Reconciliation stability runner for institutional-grade validation.
    
    Runs reconciliation stability validation including order reconciliation,
    position reconciliation, and balance reconciliation.
    """
    
    def __init__(self):
        """Initialize reconciliation stability runner."""
        self.redis = redis_manager
    
    async def run_validation(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run reconciliation stability validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info("Running reconciliation stability validation...")
        
        assertions = []
        failures = []
        warnings = []
        
        # Validate order reconciliation stability
        order_result = await self._validate_order_reconciliation_stability(tenant_id)
        assertions.extend(order_result["assertions"])
        failures.extend(order_result["failures"])
        warnings.extend(order_result["warnings"])
        
        # Validate position reconciliation stability
        position_result = await self._validate_position_reconciliation_stability(tenant_id)
        assertions.extend(position_result["assertions"])
        failures.extend(position_result["failures"])
        warnings.extend(position_result["warnings"])
        
        # Validate balance reconciliation stability
        balance_result = await self._validate_balance_reconciliation_stability(tenant_id)
        assertions.extend(balance_result["assertions"])
        failures.extend(balance_result["failures"])
        warnings.extend(balance_result["warnings"])
        
        # Validate reconciliation drift
        drift_result = await self._validate_reconciliation_drift(tenant_id)
        assertions.extend(drift_result["assertions"])
        failures.extend(drift_result["failures"])
        warnings.extend(drift_result["warnings"])
        
        passed = len(failures) == 0
        
        return {
            "passed": passed,
            "assertions": assertions,
            "failures": failures,
            "warnings": warnings
        }
    
    async def _validate_order_reconciliation_stability(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate order reconciliation stability."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check order reconciliation metrics
            pattern = f"order_reconciliation_stability:{tenant_id}:*" if tenant_id else "order_reconciliation_stability:*:*"
            keys = await self.redis.keys(pattern)
            
            stability_scores = []
            for key in keys:
                score = await self.redis.get(key)
                if score:
                    stability_scores.append(float(score))
            
            if stability_scores:
                avg_stability = sum(stability_scores) / len(stability_scores)
                if avg_stability >= 0.98:
                    assertions.append(f"Order reconciliation stability healthy: {avg_stability:.2%}")
                elif avg_stability >= 0.95:
                    warnings.append(f"Order reconciliation stability degraded: {avg_stability:.2%}")
                else:
                    failures.append(f"Order reconciliation stability critical: {avg_stability:.2%}")
            else:
                warnings.append("No order reconciliation stability data available")
            
            assertions.append("Order reconciliation stability validated")
            
        except Exception as e:
            failures.append(f"Order reconciliation stability validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_position_reconciliation_stability(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate position reconciliation stability."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check position reconciliation metrics
            pattern = f"position_reconciliation_stability:{tenant_id}:*" if tenant_id else "position_reconciliation_stability:*:*"
            keys = await self.redis.keys(pattern)
            
            stability_scores = []
            for key in keys:
                score = await self.redis.get(key)
                if score:
                    stability_scores.append(float(score))
            
            if stability_scores:
                avg_stability = sum(stability_scores) / len(stability_scores)
                if avg_stability >= 0.98:
                    assertions.append(f"Position reconciliation stability healthy: {avg_stability:.2%}")
                elif avg_stability >= 0.95:
                    warnings.append(f"Position reconciliation stability degraded: {avg_stability:.2%}")
                else:
                    failures.append(f"Position reconciliation stability critical: {avg_stability:.2%}")
            else:
                warnings.append("No position reconciliation stability data available")
            
            assertions.append("Position reconciliation stability validated")
            
        except Exception as e:
            failures.append(f"Position reconciliation stability validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_balance_reconciliation_stability(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate balance reconciliation stability."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check balance reconciliation metrics
            pattern = f"balance_reconciliation_stability:{tenant_id}:*" if tenant_id else "balance_reconciliation_stability:*:*"
            keys = await self.redis.keys(pattern)
            
            stability_scores = []
            for key in keys:
                score = await self.redis.get(key)
                if score:
                    stability_scores.append(float(score))
            
            if stability_scores:
                avg_stability = sum(stability_scores) / len(stability_scores)
                if avg_stability >= 0.99:
                    assertions.append(f"Balance reconciliation stability healthy: {avg_stability:.2%}")
                elif avg_stability >= 0.97:
                    warnings.append(f"Balance reconciliation stability degraded: {avg_stability:.2%}")
                else:
                    failures.append(f"Balance reconciliation stability critical: {avg_stability:.2%}")
            else:
                warnings.append("No balance reconciliation stability data available")
            
            assertions.append("Balance reconciliation stability validated")
            
        except Exception as e:
            failures.append(f"Balance reconciliation stability validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_reconciliation_drift(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate reconciliation drift."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check reconciliation drift metrics
            pattern = f"reconciliation_drift:{tenant_id}:*" if tenant_id else "reconciliation_drift:*:*"
            keys = await self.redis.keys(pattern)
            
            drift_values = []
            for key in keys:
                drift = await self.redis.get(key)
                if drift:
                    drift_values.append(float(drift))
            
            if drift_values:
                avg_drift = sum(drift_values) / len(drift_values)
                max_drift = max(drift_values)
                
                if avg_drift <= 0.001 and max_drift <= 0.01:
                    assertions.append(f"Reconciliation drift healthy: avg={avg_drift:.4%}, max={max_drift:.4%}")
                elif avg_drift <= 0.005 and max_drift <= 0.05:
                    warnings.append(f"Reconciliation drift elevated: avg={avg_drift:.4%}, max={max_drift:.4%}")
                else:
                    failures.append(f"Reconciliation drift critical: avg={avg_drift:.4%}, max={max_drift:.4%}")
            else:
                warnings.append("No reconciliation drift data available")
            
            assertions.append("Reconciliation drift validated")
            
        except Exception as e:
            failures.append(f"Reconciliation drift validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def start_continuous_monitoring(self, interval_seconds: int = 300, tenant_id: Optional[str] = None):
        """
        Start continuous reconciliation stability monitoring.
        
        Args:
            interval_seconds: Monitoring interval in seconds
            tenant_id: Optional tenant ID for scoped validation
        """
        logger.info(f"Starting reconciliation stability monitoring with interval: {interval_seconds}s")
        
        while True:
            try:
                result = await self.run_validation(tenant_id)
                
                if not result["passed"]:
                    logger.warning(f"Reconciliation stability validation failed: {result['failures']}")
                
                await asyncio.sleep(interval_seconds)
                
            except asyncio.CancelledError:
                logger.info("Reconciliation stability monitoring cancelled")
                break
            except Exception as e:
                logger.error(f"Reconciliation stability monitoring error: {e}")
                await asyncio.sleep(interval_seconds)


# Global instance
_reconciliation_stability_runner: ReconciliationStabilityRunner = None


def get_reconciliation_stability_runner() -> ReconciliationStabilityRunner:
    """Get or create reconciliation stability runner instance."""
    global _reconciliation_stability_runner
    if _reconciliation_stability_runner is None:
        _reconciliation_stability_runner = ReconciliationStabilityRunner()
    return _reconciliation_stability_runner
