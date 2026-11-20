"""
Execution Integrity Alarm - Phase A

This module detects and alarms on execution integrity issues for continuous
institutional-grade operational validation of the strict algo trading platform.

Author: Principal Institutional Operational Validation Engineer
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from backend_app.core.cache.redis_manager import redis_manager

from .validation_alert_manager import ValidationAlertManager

logger = logging.getLogger("execution_integrity_alarm")


class ExecutionIntegrityAlarm:
    """
    Execution integrity alarm for continuous validation execution.
    
    Detects and alarms on execution integrity issues for institutional-grade
    operational validation.
    """
    
    def __init__(self):
        """Initialize execution integrity alarm."""
        self.redis = redis_manager
        self.alert_manager = ValidationAlertManager()
    
    async def check_duplicate_execution(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check for duplicate execution.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Duplicate execution check result
        """
        logger.info("Checking duplicate execution...")
        
        duplicates_detected = False
        duplicate_details = []
        
        try:
            # Check for duplicate execution markers
            pattern = f"duplicate_execution:{tenant_id}:*" if tenant_id else "duplicate_execution:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                duplicate = await self.redis.get(key)
                if duplicate:
                    duplicates_detected = True
                    duplicate_details.append({
                        "key": key,
                        "duplicate": duplicate
                    })
            
            # Check execution ID uniqueness
            pattern = f"execution_id:{tenant_id}:*" if tenant_id else "execution_id:*:*"
            keys = await self.redis.keys(pattern)
            
            execution_ids = {}
            for key in keys:
                exec_id = key.split(":")[-1]
                if exec_id in execution_ids:
                    duplicates_detected = True
                    duplicate_details.append({
                        "execution_id": exec_id,
                        "issue": "duplicate_execution_id",
                        "keys": [execution_ids[exec_id], key]
                    })
                else:
                    execution_ids[exec_id] = key
            
            result = {
                "duplicates_detected": duplicates_detected,
                "duplicate_details": duplicate_details,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Trigger alert if duplicates detected
            if duplicates_detected:
                logger.critical(f"Duplicate execution detected: {duplicate_details}")
                await self.alert_manager.trigger_critical_alert(
                    "duplicate_execution",
                    duplicate_details
                )
            
            return result
            
        except Exception as e:
            logger.error(f"Duplicate execution check failed: {e}")
            return {
                "duplicates_detected": False,
                "duplicate_details": [],
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    async def check_execution_deduplication(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check execution deduplication correctness.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Execution deduplication check result
        """
        logger.info("Checking execution deduplication...")
        
        issues = []
        
        try:
            # Check execution deduplication markers
            pattern = f"execution_dedup:{tenant_id}:*" if tenant_id else "execution_dedup:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                dedup = await self.redis.get(key)
                if not dedup:
                    issues.append({
                        "key": key,
                        "issue": "execution_deduplication_not_active"
                    })
            
            # Check execution state consistency
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
                    issues.append({
                        "state": state_str,
                        "count": count,
                        "issue": "orphaned_execution_state"
                    })
            
            result = {
                "deduplication_correct": len(issues) == 0,
                "issues": issues,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Trigger alert if issues detected
            if not result["deduplication_correct"]:
                logger.warning(f"Execution deduplication issues detected: {issues}")
                await self.alert_manager.trigger_high_priority_alert(
                    "execution_deduplication",
                    issues
                )
            
            return result
            
        except Exception as e:
            logger.error(f"Execution deduplication check failed: {e}")
            return {
                "deduplication_correct": False,
                "issues": [],
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    async def check_signal_deduplication(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check signal deduplication correctness.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Signal deduplication check result
        """
        logger.info("Checking signal deduplication...")
        
        issues = []
        
        try:
            # Check signal deduplication markers
            pattern = f"signal_dedup:{tenant_id}:*" if tenant_id else "signal_dedup:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                dedup = await self.redis.get(key)
                if not dedup:
                    issues.append({
                        "key": key,
                        "issue": "signal_deduplication_not_active"
                    })
            
            # Check for duplicate signals
            pattern = f"duplicate_signal:{tenant_id}:*" if tenant_id else "duplicate_signal:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                duplicate = await self.redis.get(key)
                if duplicate:
                    issues.append({
                        "key": key,
                        "duplicate": duplicate,
                        "issue": "duplicate_signal_detected"
                    })
            
            result = {
                "deduplication_correct": len(issues) == 0,
                "issues": issues,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Trigger alert if issues detected
            if not result["deduplication_correct"]:
                logger.warning(f"Signal deduplication issues detected: {issues}")
                await self.alert_manager.trigger_high_priority_alert(
                    "signal_deduplication",
                    issues
                )
            
            return result
            
        except Exception as e:
            logger.error(f"Signal deduplication check failed: {e}")
            return {
                "deduplication_correct": False,
                "issues": [],
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    async def check_orphan_execution_state(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check for orphan execution state.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Orphan execution state check result
        """
        logger.info("Checking orphan execution state...")
        
        orphans_detected = False
        orphan_details = []
        
        try:
            # Check for orphaned execution markers
            pattern = f"orphaned_execution:{tenant_id}:*" if tenant_id else "orphaned_execution:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                orphan = await self.redis.get(key)
                if orphan:
                    orphans_detected = True
                    orphan_details.append({
                        "key": key,
                        "orphan": orphan
                    })
            
            # Check execution state for orphaned states
            pattern = f"execution_state:{tenant_id}:*" if tenant_id else "execution_state:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                state = await self.redis.get(key)
                if state:
                    state_str = str(state)
                    if "ORPHANED" in state_str:
                        orphans_detected = True
                        orphan_details.append({
                            "key": key,
                            "state": state_str,
                            "issue": "orphaned_execution_state"
                        })
            
            result = {
                "orphans_detected": orphans_detected,
                "orphan_details": orphan_details,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Trigger alert if orphans detected
            if orphans_detected:
                logger.warning(f"Orphan execution state detected: {orphan_details}")
                await self.alert_manager.trigger_high_priority_alert(
                    "orphan_execution_state",
                    orphan_details
                )
            
            return result
            
        except Exception as e:
            logger.error(f"Orphan execution state check failed: {e}")
            return {
                "orphans_detected": False,
                "orphan_details": [],
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    async def check_fencing_token_correctness(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Check fencing token correctness.
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Fencing token check result
        """
        logger.info("Checking fencing token correctness...")
        
        issues = []
        
        try:
            # Check fencing tokens exist
            pattern = f"fence_token:{tenant_id}:*" if tenant_id else "fence_token:*:*"
            keys = await self.redis.keys(pattern)
            
            for key in keys:
                token = await self.redis.get(key)
                if not token:
                    issues.append({
                        "key": key,
                        "issue": "fencing_token_missing"
                    })
            
            # Check for expired tokens
            for key in keys:
                ttl = await self.redis.ttl(key)
                if ttl == -1:
                    issues.append({
                        "key": key,
                        "issue": "fencing_token_no_ttl"
                    })
                elif ttl < 0:
                    issues.append({
                        "key": key,
                        "issue": "fencing_token_expired"
                    })
            
            result = {
                "tokens_correct": len(issues) == 0,
                "issues": issues,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Trigger alert if issues detected
            if not result["tokens_correct"]:
                logger.warning(f"Fencing token issues detected: {issues}")
                await self.alert_manager.trigger_high_priority_alert(
                    "fencing_token",
                    issues
                )
            
            return result
            
        except Exception as e:
            logger.error(f"Fencing token check failed: {e}")
            return {
                "tokens_correct": False,
                "issues": [],
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }


# Global instance
_execution_integrity_alarm: ExecutionIntegrityAlarm = None


def get_execution_integrity_alarm() -> ExecutionIntegrityAlarm:
    """Get or create execution integrity alarm instance."""
    global _execution_integrity_alarm
    if _execution_integrity_alarm is None:
        _execution_integrity_alarm = ExecutionIntegrityAlarm()
    return _execution_integrity_alarm
