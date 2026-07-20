"""
Validation Scheduler - Phase A

This module schedules continuous validation execution for institutional-grade
operational validation of the strict algo trading platform.

Author: Principal Institutional Operational Validation Engineer
"""

import logging
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

from .validation_executor import ValidationExecutor
from .validation_result_store import ValidationResultStore
from .validation_alert_manager import ValidationAlertManager

logger = logging.getLogger("validation_scheduler")


@dataclass
class ValidationSchedule:
    """Validation schedule configuration."""
    validator_name: str
    interval_seconds: int
    enabled: bool = True
    priority: str = "normal"  # normal, high, critical


class ValidationScheduler:
    """
    Validation scheduler for continuous validation execution.
    
    Schedules and orchestrates continuous validation execution for
    institutional-grade operational validation.
    """
    
    def __init__(self):
        """Initialize validation scheduler."""
        self.executor = ValidationExecutor()
        self.result_store = ValidationResultStore()
        self.alert_manager = ValidationAlertManager()
        
        self.schedules: Dict[str, ValidationSchedule] = {}
        self.running = False
        self.tasks: List[asyncio.Task] = []
        
        # Default schedules
        self._register_default_schedules()
    
    def _register_default_schedules(self):
        """Register default validation schedules."""
        # Replay consistency validation - every 30 seconds
        self.schedules["replay_consistency"] = ValidationSchedule(
            validator_name="replay_consistency",
            interval_seconds=30,
            enabled=True,
            priority="critical"
        )
        
        # Execution deduplication validation - every 60 seconds
        self.schedules["execution_deduplication"] = ValidationSchedule(
            validator_name="execution_deduplication",
            interval_seconds=60,
            enabled=True,
            priority="critical"
        )
        
        # WebSocket sequencing validation - every 60 seconds
        self.schedules["websocket_sequencing"] = ValidationSchedule(
            validator_name="websocket_sequencing",
            interval_seconds=60,
            enabled=True,
            priority="high"
        )
        
        # Failover ownership validation - every 120 seconds
        self.schedules["failover_ownership"] = ValidationSchedule(
            validator_name="failover_ownership",
            interval_seconds=120,
            enabled=True,
            priority="high"
        )
        
        # Deterministic replay validation - every 300 seconds
        self.schedules["deterministic_replay"] = ValidationSchedule(
            validator_name="deterministic_replay",
            interval_seconds=300,
            enabled=True,
            priority="normal"
        )
        
        # Reconciliation integrity validation - every 300 seconds
        self.schedules["reconciliation_integrity"] = ValidationSchedule(
            validator_name="reconciliation_integrity",
            interval_seconds=300,
            enabled=True,
            priority="normal"
        )
        
        # Fencing token validation - every 60 seconds
        self.schedules["fencing_token"] = ValidationSchedule(
            validator_name="fencing_token",
            interval_seconds=60,
            enabled=True,
            priority="high"
        )
        
        # Execution sequencing validation - every 120 seconds
        self.schedules["execution_sequencing"] = ValidationSchedule(
            validator_name="execution_sequencing",
            interval_seconds=120,
            enabled=True,
            priority="high"
        )
    
    async def start(self):
        """Start validation scheduler."""
        if self.running:
            logger.warning("Validation scheduler already running")
            return
        
        logger.info("Starting validation scheduler...")
        self.running = True
        
        # Start scheduled tasks
        for schedule_name, schedule in self.schedules.items():
            if schedule.enabled:
                task = asyncio.create_task(
                    self._run_scheduled_validation(schedule_name, schedule)
                )
                self.tasks.append(task)
        
        logger.info(f"Validation scheduler started with {len(self.tasks)} scheduled tasks")
    
    async def stop(self):
        """Stop validation scheduler."""
        if not self.running:
            logger.warning("Validation scheduler not running")
            return
        
        logger.info("Stopping validation scheduler...")
        self.running = False
        
        # Cancel all tasks
        for task in self.tasks:
            task.cancel()
        
        # Wait for tasks to complete
        await asyncio.gather(*self.tasks, return_exceptions=True)
        
        self.tasks.clear()
        logger.info("Validation scheduler stopped")
    
    async def _run_scheduled_validation(self, schedule_name: str, schedule: ValidationSchedule):
        """
        Run scheduled validation.
        
        Args:
            schedule_name: Schedule name
            schedule: Validation schedule
        """
        logger.info(f"Starting scheduled validation: {schedule_name} (interval: {schedule.interval_seconds}s)")
        
        while self.running:
            try:
                # Execute validation
                result = await self.executor.execute_validation(schedule.validator_name)
                
                # Store result
                await self.result_store.store_result(schedule_name, result)
                
                # Check for critical failures
                if not result.get("passed", False) and schedule.priority == "critical":
                    logger.critical(f"Critical validation failure: {schedule_name}")
                    await self.alert_manager.trigger_critical_alert(
                        schedule_name,
                        result.get("failures", [])
                    )
                
                # Check for high priority failures
                if not result.get("passed", False) and schedule.priority == "high":
                    logger.warning(f"High priority validation failure: {schedule_name}")
                    await self.alert_manager.trigger_high_priority_alert(
                        schedule_name,
                        result.get("failures", [])
                    )
                
                # Wait for next interval
                await asyncio.sleep(schedule.interval_seconds)
                
            except asyncio.CancelledError:
                logger.info(f"Scheduled validation cancelled: {schedule_name}")
                break
            except Exception as e:
                logger.error(f"Scheduled validation error: {schedule_name} - {e}")
                await asyncio.sleep(schedule.interval_seconds)
    
    async def run_validation_now(self, validator_name: str) -> Dict[str, Any]:
        """
        Run validation immediately.
        
        Args:
            validator_name: Validator name
            
        Returns:
            Validation result
        """
        logger.info(f"Running immediate validation: {validator_name}")
        
        result = await self.executor.execute_validation(validator_name)
        await self.result_store.store_result(validator_name, result)
        
        return result
    
    def update_schedule(self, schedule_name: str, interval_seconds: int, enabled: bool = True):
        """
        Update validation schedule.
        
        Args:
            schedule_name: Schedule name
            interval_seconds: Interval in seconds
            enabled: Whether schedule is enabled
        """
        if schedule_name in self.schedules:
            self.schedules[schedule_name].interval_seconds = interval_seconds
            self.schedules[schedule_name].enabled = enabled
            logger.info(f"Updated schedule: {schedule_name} (interval: {interval_seconds}s, enabled: {enabled})")
        else:
            logger.warning(f"Schedule not found: {schedule_name}")
    
    def get_schedules(self) -> Dict[str, ValidationSchedule]:
        """Get all schedules."""
        return self.schedules.copy()
    
    def get_schedule_status(self) -> Dict[str, Any]:
        """Get scheduler status."""
        return {
            "running": self.running,
            "scheduled_tasks": len(self.tasks),
            "schedules": {
                name: {
                    "interval_seconds": schedule.interval_seconds,
                    "enabled": schedule.enabled,
                    "priority": schedule.priority
                }
                for name, schedule in self.schedules.items()
            }
        }


# Global instance
_validation_scheduler: ValidationScheduler = None


def get_validation_scheduler() -> ValidationScheduler:
    """Get or create validation scheduler instance."""
    global _validation_scheduler
    if _validation_scheduler is None:
        _validation_scheduler = ValidationScheduler()
    return _validation_scheduler
