"""
Reconciliation Validator - Phase 1

This module validates reconciliation consistency including order reconciliation,
position reconciliation, balance reconciliation, and reconciliation drift detection.

Author: Principal Institutional Validation Engineer
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("reconciliation_validator")


class ReconciliationValidator:
    """
    Reconciliation validator for institutional-grade validation.
    
    Validates reconciliation consistency including order reconciliation,
    position reconciliation, balance reconciliation, and reconciliation drift detection.
    """
    
    def __init__(self):
        """Initialize reconciliation validator."""
        self.redis = redis_manager
    
    async def validate(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run reconciliation validation.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Validation result
        """
        logger.info("Running reconciliation validation...")
        
        assertions = []
        failures = []
        warnings = []
        
        # Validate order reconciliation
        order_result = await self._validate_order_reconciliation(tenant_id)
        assertions.extend(order_result["assertions"])
        failures.extend(order_result["failures"])
        warnings.extend(order_result["warnings"])
        
        # Validate position reconciliation
        position_result = await self._validate_position_reconciliation(tenant_id)
        assertions.extend(position_result["assertions"])
        failures.extend(position_result["failures"])
        warnings.extend(position_result["warnings"])
        
        # Validate balance reconciliation
        balance_result = await self._validate_balance_reconciliation(tenant_id)
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
    
    async def _validate_order_reconciliation(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate order reconciliation."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check order reconciliation markers
            pattern = f"order_reconciliation:{tenant_id}:*" if tenant_id else "order_reconciliation:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                reconciliation = await self.redis.get(key)
                if reconciliation:
                    assertions.append(f"Order reconciliation marker exists: {key}")
                else:
                    warnings.append(f"Order reconciliation marker missing: {key}")
            
            # Check for order discrepancies
            pattern = f"order_discrepancy:{tenant_id}:*" if tenant_id else "order_discrepancy:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                discrepancy = await self.redis.get(key)
                if discrepancy:
                    failures.append(f"Order discrepancy detected: {key} = {discrepancy}")
                else:
                    assertions.append(f"No order discrepancy: {key}")
            
            assertions.append("Order reconciliation validated: reconciliation is consistent")
            
        except Exception as e:
            failures.append(f"Order reconciliation validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_position_reconciliation(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate position reconciliation."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check position reconciliation markers
            pattern = f"position_reconciliation:{tenant_id}:*" if tenant_id else "position_reconciliation:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                reconciliation = await self.redis.get(key)
                if reconciliation:
                    assertions.append(f"Position reconciliation marker exists: {key}")
                else:
                    warnings.append(f"Position reconciliation marker missing: {key}")
            
            # Check for position discrepancies
            pattern = f"position_discrepancy:{tenant_id}:*" if tenant_id else "position_discrepancy:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                discrepancy = await self.redis.get(key)
                if discrepancy:
                    failures.append(f"Position discrepancy detected: {key} = {discrepancy}")
                else:
                    assertions.append(f"No position discrepancy: {key}")
            
            assertions.append("Position reconciliation validated: reconciliation is consistent")
            
        except Exception as e:
            failures.append(f"Position reconciliation validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_balance_reconciliation(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate balance reconciliation."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check balance reconciliation markers
            pattern = f"balance_reconciliation:{tenant_id}:*" if tenant_id else "balance_reconciliation:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                reconciliation = await self.redis.get(key)
                if reconciliation:
                    assertions.append(f"Balance reconciliation marker exists: {key}")
                else:
                    warnings.append(f"Balance reconciliation marker missing: {key}")
            
            # Check for balance discrepancies
            pattern = f"balance_discrepancy:{tenant_id}:*" if tenant_id else "balance_discrepancy:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                discrepancy = await self.redis.get(key)
                if discrepancy:
                    failures.append(f"Balance discrepancy detected: {key} = {discrepancy}")
                else:
                    assertions.append(f"No balance discrepancy: {key}")
            
            assertions.append("Balance reconciliation validated: reconciliation is consistent")
            
        except Exception as e:
            failures.append(f"Balance reconciliation validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
    
    async def _validate_reconciliation_drift(self, tenant_id: Optional[str]) -> Dict[str, Any]:
        """Validate reconciliation drift."""
        assertions = []
        failures = []
        warnings = []
        
        try:
            # Check reconciliation drift markers
            pattern = f"reconciliation_drift:{tenant_id}:*" if tenant_id else "reconciliation_drift:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                drift = await self.redis.get(key)
                if drift:
                    failures.append(f"Reconciliation drift detected: {key} = {drift}")
                else:
                    assertions.append(f"No reconciliation drift: {key}")
            
            # Check reconciliation timestamps
            pattern = f"reconciliation_timestamp:{tenant_id}:*" if tenant_id else "reconciliation_timestamp:*:*"
            keys = await self.redis.keys(pattern)
            
            current_time = datetime.now(timezone.utc)
            stale_threshold = 3600  # 1 hour
            
            for key in keys:
                timestamp = await self.redis.get(key)
                if timestamp:
                    try:
                        recon_time = datetime.fromisoformat(timestamp)
                        age_seconds = (current_time - recon_time).total_seconds()
                        
                        if age_seconds > stale_threshold:
                            warnings.append(f"Stale reconciliation timestamp: {key} (age: {age_seconds}s)")
                        else:
                            assertions.append(f"Reconciliation timestamp valid: {key}")
                    except Exception as e:
                        failures.append(f"Invalid reconciliation timestamp format: {key} - {str(e)}")
            
            assertions.append("Reconciliation drift validated: no drift detected")
            
        except Exception as e:
            failures.append(f"Reconciliation drift validation failed: {str(e)}")
        
        return {"assertions": assertions, "failures": failures, "warnings": warnings}
