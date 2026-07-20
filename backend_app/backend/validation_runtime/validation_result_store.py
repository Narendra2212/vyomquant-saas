"""
Validation Result Store - Phase A

This module stores and retrieves validation results for continuous
institutional-grade operational validation of the strict algo trading platform.

Author: Principal Institutional Operational Validation Engineer
"""

import logging
import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("validation_result_store")


class ValidationResultStore:
    """
    Validation result store for continuous validation execution.
    
    Stores and retrieves validation results for institutional-grade
    operational validation.
    """
    
    def __init__(self):
        """Initialize validation result store."""
        self.redis = redis_manager
        self.result_ttl = 86400  # 24 hours
        self.alert_ttl = 604800  # 7 days for alerts
    
    async def store_result(self, validator_name: str, result: Dict[str, Any]) -> bool:
        """
        Store validation result.
        
        Args:
            validator_name: Validator name
            result: Validation result
            
        Returns:
            True if stored successfully
        """
        try:
            # Store result
            key = f"validation_result:{validator_name}"
            await self.redis.setex(key, self.result_ttl, json.dumps(result))
            
            # Store result history
            history_key = f"validation_history:{validator_name}"
            timestamp = datetime.now(timezone.utc).isoformat()
            history_entry = {
                "timestamp": timestamp,
                "passed": result.get("passed", False),
                "failures": result.get("failures", []),
                "warnings": result.get("warnings", [])
            }
            
            # Add to list
            await self.redis.lpush(history_key, json.dumps(history_entry))
            await self.redis.expire(history_key, self.result_ttl)
            
            # Trim history to last 100 entries
            await self.redis.ltrim(history_key, 0, 99)
            
            logger.info(f"Stored validation result: {validator_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to store validation result: {validator_name} - {e}")
            return False
    
    async def get_result(self, validator_name: str) -> Optional[Dict[str, Any]]:
        """
        Get latest validation result.
        
        Args:
            validator_name: Validator name
            
        Returns:
            Validation result or None
        """
        try:
            key = f"validation_result:{validator_name}"
            result = await self.redis.get(key)
            
            if result:
                return json.loads(result)
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get validation result: {validator_name} - {e}")
            return None
    
    async def get_history(self, validator_name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get validation result history.
        
        Args:
            validator_name: Validator name
            limit: Number of entries to retrieve
            
        Returns:
            List of validation results
        """
        try:
            history_key = f"validation_history:{validator_name}"
            entries = await self.redis.lrange(history_key, 0, limit - 1)
            
            history = []
            for entry in entries:
                history.append(json.loads(entry))
            
            return history
            
        except Exception as e:
            logger.error(f"Failed to get validation history: {validator_name} - {e}")
            return []
    
    async def store_alert(self, alert_type: str, alert_data: Dict[str, Any]) -> bool:
        """
        Store validation alert.
        
        Args:
            alert_type: Alert type
            alert_data: Alert data
            
        Returns:
            True if stored successfully
        """
        try:
            # Store alert
            key = f"validation_alert:{alert_type}"
            alert_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "alert_type": alert_type,
                "data": alert_data
            }
            
            await self.redis.setex(key, self.alert_ttl, json.dumps(alert_entry))
            
            # Store alert history
            history_key = f"alert_history:{alert_type}"
            await self.redis.lpush(history_key, json.dumps(alert_entry))
            await self.redis.expire(history_key, self.alert_ttl)
            
            # Trim history to last 100 entries
            await self.redis.ltrim(history_key, 0, 99)
            
            logger.warning(f"Stored validation alert: {alert_type}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to store validation alert: {alert_type} - {e}")
            return False
    
    async def get_alert(self, alert_type: str) -> Optional[Dict[str, Any]]:
        """
        Get latest alert.
        
        Args:
            alert_type: Alert type
            
        Returns:
            Alert data or None
        """
        try:
            key = f"validation_alert:{alert_type}"
            alert = await self.redis.get(key)
            
            if alert:
                return json.loads(alert)
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get alert: {alert_type} - {e}")
            return None
    
    async def get_alert_history(self, alert_type: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get alert history.
        
        Args:
            alert_type: Alert type
            limit: Number of entries to retrieve
            
        Returns:
            List of alerts
        """
        try:
            history_key = f"alert_history:{alert_type}"
            entries = await self.redis.lrange(history_key, 0, limit - 1)
            
            history = []
            for entry in entries:
                history.append(json.loads(entry))
            
            return history
            
        except Exception as e:
            logger.error(f"Failed to get alert history: {alert_type} - {e}")
            return []
    
    async def get_all_results(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all validation results.
        
        Returns:
            Dictionary of all validation results
        """
        try:
            pattern = "validation_result:*"
            keys = await self.redis.keys(pattern)
            
            results = {}
            for key in keys:
                validator_name = key.split(":")[-1]
                result = await self.redis.get(key)
                if result:
                    results[validator_name] = json.loads(result)
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to get all validation results: {e}")
            return {}
    
    async def get_all_alerts(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all alerts.
        
        Returns:
            Dictionary of all alerts
        """
        try:
            pattern = "validation_alert:*"
            keys = await self.redis.keys(pattern)
            
            alerts = {}
            for key in keys:
                alert_type = key.split(":")[-1]
                alert = await self.redis.get(key)
                if alert:
                    alerts[alert_type] = json.loads(alert)
            
            return alerts
            
        except Exception as e:
            logger.error(f"Failed to get all alerts: {e}")
            return {}
    
    async def cleanup_old_results(self, days: int = 7):
        """
        Clean up old validation results.
        
        Args:
            days: Number of days to keep
        """
        try:
            # This would scan and delete old results
            # For now, rely on Redis TTL
            logger.info(f"Result cleanup relies on TTL for {days} days")
            
        except Exception as e:
            logger.error(f"Result cleanup failed: {e}")
    
    async def get_validation_summary(self) -> Dict[str, Any]:
        """
        Get validation summary.
        
        Returns:
            Validation summary
        """
        try:
            results = await self.get_all_results()
            alerts = await self.get_all_alerts()
            
            total_validations = len(results)
            passed_validations = sum(1 for r in results.values() if r.get("passed", False))
            failed_validations = total_validations - passed_validations
            
            total_alerts = len(alerts)
            critical_alerts = sum(1 for a in alerts.values() if a.get("data", {}).get("severity") == "critical")
            
            return {
                "total_validations": total_validations,
                "passed_validations": passed_validations,
                "failed_validations": failed_validations,
                "total_alerts": total_alerts,
                "critical_alerts": critical_alerts,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to get validation summary: {e}")
            return {}


# Global instance
_validation_result_store: ValidationResultStore = None


def get_validation_result_store() -> ValidationResultStore:
    """Get or create validation result store instance."""
    global _validation_result_store
    if _validation_result_store is None:
        _validation_result_store = ValidationResultStore()
    return _validation_result_store
