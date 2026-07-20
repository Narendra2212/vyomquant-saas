"""
Validation Alert Manager - Phase A

This module manages validation alerts for continuous institutional-grade
operational validation of the strict algo trading platform.

Author: Principal Institutional Operational Validation Engineer
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from .validation_result_store import ValidationResultStore

logger = logging.getLogger("validation_alert_manager")


class ValidationAlertManager:
    """
    Validation alert manager for continuous validation execution.
    
    Manages validation alerts for institutional-grade operational validation.
    """
    
    def __init__(self):
        """Initialize validation alert manager."""
        self.result_store = ValidationResultStore()
        self.alert_cooldown = 300  # 5 minutes
    
    async def trigger_critical_alert(self, validator_name: str, failures: List[str]):
        """
        Trigger critical alert.
        
        Args:
            validator_name: Validator name
            failures: List of failures
        """
        logger.critical(f"CRITICAL ALERT: {validator_name} - Failures: {failures}")
        
        alert_data = {
            "severity": "critical",
            "validator_name": validator_name,
            "failures": failures,
            "action": "freeze_execution"
        }
        
        await self.result_store.store_alert(f"critical_{validator_name}", alert_data)
        
        # Trigger critical failure actions
        await self._trigger_critical_failure_actions(validator_name, failures)
    
    async def trigger_high_priority_alert(self, validator_name: str, failures: List[str]):
        """
        Trigger high priority alert.
        
        Args:
            validator_name: Validator name
            failures: List of failures
        """
        logger.warning(f"HIGH PRIORITY ALERT: {validator_name} - Failures: {failures}")
        
        alert_data = {
            "severity": "high",
            "validator_name": validator_name,
            "failures": failures,
            "action": "monitor"
        }
        
        await self.result_store.store_alert(f"high_{validator_name}", alert_data)
    
    async def trigger_normal_alert(self, validator_name: str, warnings: List[str]):
        """
        Trigger normal alert.
        
        Args:
            validator_name: Validator name
            warnings: List of warnings
        """
        logger.info(f"NORMAL ALERT: {validator_name} - Warnings: {warnings}")
        
        alert_data = {
            "severity": "normal",
            "validator_name": validator_name,
            "warnings": warnings,
            "action": "log"
        }
        
        await self.result_store.store_alert(f"normal_{validator_name}", alert_data)
    
    async def _trigger_critical_failure_actions(self, validator_name: str, failures: List[str]):
        """
        Trigger critical failure actions.
        
        Args:
            validator_name: Validator name
            failures: List of failures
        """
        logger.critical(f"Triggering critical failure actions for: {validator_name}")
        
        # 1. Freeze execution
        await self._freeze_execution()
        
        # 2. Trigger CRITICAL alert
        await self._send_critical_alert(validator_name, failures)
        
        # 3. Snapshot state
        await self._snapshot_state(validator_name)
        
        # 4. Preserve replay evidence
        await self._preserve_replay_evidence(validator_name)
    
    async def _freeze_execution(self):
        """Freeze execution."""
        logger.critical("FREEZING EXECUTION - CRITICAL VALIDATION FAILURE")
        
        # This would integrate with the execution engine to freeze execution
        # For now, we log the action
        try:
            # Set freeze flag
            from backend_app.core.cache.redis_manager import redis_manager
            await redis_manager.set("execution_freeze", "true")
            await redis_manager.set("execution_freeze_reason", "critical_validation_failure")
            await redis_manager.set("execution_freeze_timestamp", datetime.now(timezone.utc).isoformat())
            
            logger.critical("Execution freeze flag set")
            
        except Exception as e:
            logger.error(f"Failed to freeze execution: {e}")
    
    async def _send_critical_alert(self, validator_name: str, failures: List[str]):
        """
        Send critical alert.
        
        Args:
            validator_name: Validator name
            failures: List of failures
        """
        logger.critical(f"SENDING CRITICAL ALERT: {validator_name}")
        
        # This would integrate with alerting system (PagerDuty, Slack, etc.)
        # For now, we log the action
        alert_message = f"CRITICAL VALIDATION FAILURE: {validator_name}\nFailures: {failures}"
        logger.critical(alert_message)
    
    async def _snapshot_state(self, validator_name: str):
        """
        Snapshot state.
        
        Args:
            validator_name: Validator name
        """
        logger.critical(f"SNAPPING STATE FOR: {validator_name}")
        
        # This would integrate with state snapshot system
        # For now, we log the action
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            
            # Record snapshot
            snapshot_key = f"validation_snapshot:{validator_name}"
            snapshot_data = {
                "validator_name": validator_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reason": "critical_validation_failure"
            }
            
            await redis_manager.set(snapshot_key, str(snapshot_data))
            
            logger.critical(f"State snapshot recorded: {snapshot_key}")
            
        except Exception as e:
            logger.error(f"Failed to snapshot state: {e}")
    
    async def _preserve_replay_evidence(self, validator_name: str):
        """
        Preserve replay evidence.
        
        Args:
            validator_name: Validator name
        """
        logger.critical(f"PRESERVING REPLAY EVIDENCE FOR: {validator_name}")
        
        # This would integrate with replay evidence preservation system
        # For now, we log the action
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            
            # Record evidence preservation
            evidence_key = f"replay_evidence:{validator_name}"
            evidence_data = {
                "validator_name": validator_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reason": "critical_validation_failure"
            }
            
            await redis_manager.set(evidence_key, str(evidence_data))
            
            logger.critical(f"Replay evidence preserved: {evidence_key}")
            
        except Exception as e:
            logger.error(f"Failed to preserve replay evidence: {e}")
    
    async def check_alert_cooldown(self, validator_name: str) -> bool:
        """
        Check if alert is in cooldown.
        
        Args:
            validator_name: Validator name
            
        Returns:
            True if in cooldown
        """
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            
            cooldown_key = f"alert_cooldown:{validator_name}"
            exists = await redis_manager.exists(cooldown_key)
            
            return bool(exists)
            
        except Exception as e:
            logger.error(f"Failed to check alert cooldown: {e}")
            return False
    
    async def set_alert_cooldown(self, validator_name: str):
        """
        Set alert cooldown.
        
        Args:
            validator_name: Validator name
        """
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            
            cooldown_key = f"alert_cooldown:{validator_name}"
            await redis_manager.setex(cooldown_key, self.alert_cooldown, "cooldown")
            
        except Exception as e:
            logger.error(f"Failed to set alert cooldown: {e}")
    
    async def get_active_alerts(self) -> List[Dict[str, Any]]:
        """
        Get active alerts.
        
        Returns:
            List of active alerts
        """
        try:
            alerts = await self.result_store.get_all_alerts()
            
            active_alerts = []
            for alert_type, alert_data in alerts.items():
                if alert_data.get("data", {}).get("severity") in ["critical", "high"]:
                    active_alerts.append({
                        "alert_type": alert_type,
                        "data": alert_data
                    })
            
            return active_alerts
            
        except Exception as e:
            logger.error(f"Failed to get active alerts: {e}")
            return []
    
    async def clear_alert(self, alert_type: str) -> bool:
        """
        Clear alert.
        
        Args:
            alert_type: Alert type
            
        Returns:
            True if cleared successfully
        """
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            
            key = f"validation_alert:{alert_type}"
            await redis_manager.delete(key)
            
            logger.info(f"Cleared alert: {alert_type}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to clear alert: {alert_type} - {e}")
            return False
    
    async def unfreeze_execution(self) -> bool:
        """
        Unfreeze execution.
        
        Returns:
            True if unfrozen successfully
        """
        logger.critical("UNFREEZING EXECUTION")
        
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            
            await redis_manager.delete("execution_freeze")
            await redis_manager.delete("execution_freeze_reason")
            await redis_manager.delete("execution_freeze_timestamp")
            
            logger.critical("Execution unfrozen")
            return True
            
        except Exception as e:
            logger.error(f"Failed to unfreeze execution: {e}")
            return False


# Global instance
_validation_alert_manager: ValidationAlertManager = None


def get_validation_alert_manager() -> ValidationAlertManager:
    """Get or create validation alert manager instance."""
    global _validation_alert_manager
    if _validation_alert_manager is None:
        _validation_alert_manager = ValidationAlertManager()
    return _validation_alert_manager
