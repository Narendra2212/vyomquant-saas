"""
Validation Metrics Pipeline - Phase A

This module collects and aggregates validation metrics for continuous
institutional-grade operational validation of the strict algo trading platform.

Author: Principal Institutional Operational Validation Engineer
"""

import logging
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

from .validation_result_store import ValidationResultStore

logger = logging.getLogger("validation_metrics_pipeline")


@dataclass
class ValidationMetrics:
    """Validation metrics."""
    validator_name: str
    total_executions: int
    passed_executions: int
    failed_executions: int
    pass_rate: float
    avg_execution_time_ms: float
    last_execution_time: str
    last_status: str


class ValidationMetricsPipeline:
    """
    Validation metrics pipeline for continuous validation execution.
    
    Collects and aggregates validation metrics for institutional-grade
    operational validation.
    """
    
    def __init__(self):
        """Initialize validation metrics pipeline."""
        self.result_store = ValidationResultStore()
        self.metrics_cache: Dict[str, ValidationMetrics] = {}
        self.cache_ttl = 300  # 5 minutes
    
    async def collect_metrics(self) -> Dict[str, ValidationMetrics]:
        """
        Collect validation metrics.
        
        Returns:
            Dictionary of validation metrics
        """
        logger.info("Collecting validation metrics...")
        
        results = await self.result_store.get_all_results()
        metrics = {}
        
        for validator_name, result in results.items():
            history = await self.result_store.get_history(validator_name, limit=100)
            
            total_executions = len(history)
            passed_executions = sum(1 for h in history if h.get("passed", False))
            failed_executions = total_executions - passed_executions
            pass_rate = (passed_executions / total_executions * 100) if total_executions > 0 else 0
            
            avg_execution_time = result.get("execution_time_ms", 0)
            
            metrics[validator_name] = ValidationMetrics(
                validator_name=validator_name,
                total_executions=total_executions,
                passed_executions=passed_executions,
                failed_executions=failed_executions,
                pass_rate=pass_rate,
                avg_execution_time_ms=avg_execution_time,
                last_execution_time=result.get("timestamp", ""),
                last_status="passed" if result.get("passed", False) else "failed"
            )
        
        self.metrics_cache = metrics
        logger.info(f"Collected metrics for {len(metrics)} validators")
        
        return metrics
    
    async def get_metrics(self, validator_name: str) -> Optional[ValidationMetrics]:
        """
        Get metrics for a specific validator.
        
        Args:
            validator_name: Validator name
            
        Returns:
            Validation metrics or None
        """
        if validator_name in self.metrics_cache:
            return self.metrics_cache[validator_name]
        
        # Collect fresh metrics if not cached
        await self.collect_metrics()
        
        return self.metrics_cache.get(validator_name)
    
    async def get_all_metrics(self) -> Dict[str, ValidationMetrics]:
        """
        Get all metrics.
        
        Returns:
            Dictionary of all validation metrics
        """
        if not self.metrics_cache:
            await self.collect_metrics()
        
        return self.metrics_cache.copy()
    
    async def get_aggregate_metrics(self) -> Dict[str, Any]:
        """
        Get aggregate metrics.
        
        Returns:
            Aggregate metrics
        """
        metrics = await self.get_all_metrics()
        
        total_executions = sum(m.total_executions for m in metrics.values())
        total_passed = sum(m.passed_executions for m in metrics.values())
        total_failed = sum(m.failed_executions for m in metrics.values())
        overall_pass_rate = (total_passed / total_executions * 100) if total_executions > 0 else 0
        avg_execution_time = sum(m.avg_execution_time_ms for m in metrics.values()) / len(metrics) if metrics else 0
        
        critical_validators = [
            name for name, m in metrics.items()
            if m.last_status == "failed" and name in ["replay_consistency", "execution_deduplication", "failover_ownership"]
        ]
        
        return {
            "total_validators": len(metrics),
            "total_executions": total_executions,
            "total_passed": total_passed,
            "total_failed": total_failed,
            "overall_pass_rate": overall_pass_rate,
            "avg_execution_time_ms": avg_execution_time,
            "critical_validators_failed": len(critical_validators),
            "critical_validator_names": critical_validators,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def get_trend_metrics(self, validator_name: str, hours: int = 24) -> Dict[str, Any]:
        """
        Get trend metrics for a validator.
        
        Args:
            validator_name: Validator name
            hours: Number of hours to analyze
            
        Returns:
            Trend metrics
        """
        history = await self.result_store.get_history(validator_name, limit=100)
        
        # Filter by time window
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        recent_history = [
            h for h in history
            if datetime.fromisoformat(h.get("timestamp", "")) > cutoff_time
        ]
        
        if not recent_history:
            return {
                "validator_name": validator_name,
                "time_window_hours": hours,
                "total_executions": 0,
                "passed_executions": 0,
                "failed_executions": 0,
                "pass_rate": 0,
                "trend": "no_data"
            }
        
        total_executions = len(recent_history)
        passed_executions = sum(1 for h in recent_history if h.get("passed", False))
        failed_executions = total_executions - passed_executions
        pass_rate = (passed_executions / total_executions * 100) if total_executions > 0 else 0
        
        # Determine trend
        if pass_rate >= 95:
            trend = "healthy"
        elif pass_rate >= 80:
            trend = "degrading"
        else:
            trend = "critical"
        
        return {
            "validator_name": validator_name,
            "time_window_hours": hours,
            "total_executions": total_executions,
            "passed_executions": passed_executions,
            "failed_executions": failed_executions,
            "pass_rate": pass_rate,
            "trend": trend,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def export_metrics(self) -> Dict[str, Any]:
        """
        Export metrics for external monitoring.
        
        Returns:
            Exported metrics
        """
        aggregate_metrics = await self.get_aggregate_metrics()
        all_metrics = await self.get_all_metrics()
        
        # Convert to exportable format
        metrics_export = {
            "aggregate": aggregate_metrics,
            "validators": {
                name: {
                    "total_executions": m.total_executions,
                    "passed_executions": m.passed_executions,
                    "failed_executions": m.failed_executions,
                    "pass_rate": m.pass_rate,
                    "avg_execution_time_ms": m.avg_execution_time_ms,
                    "last_execution_time": m.last_execution_time,
                    "last_status": m.last_status
                }
                for name, m in all_metrics.items()
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return metrics_export
    
    async def start_metrics_collection(self, interval_seconds: int = 60):
        """
        Start periodic metrics collection.
        
        Args:
            interval_seconds: Collection interval in seconds
        """
        logger.info(f"Starting metrics collection with interval: {interval_seconds}s")
        
        while True:
            try:
                await self.collect_metrics()
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                logger.info("Metrics collection cancelled")
                break
            except Exception as e:
                logger.error(f"Metrics collection error: {e}")
                await asyncio.sleep(interval_seconds)
    
    def clear_cache(self):
        """Clear metrics cache."""
        self.metrics_cache.clear()
        logger.info("Metrics cache cleared")


# Global instance
_validation_metrics_pipeline: ValidationMetricsPipeline = None


def get_validation_metrics_pipeline() -> ValidationMetricsPipeline:
    """Get or create validation metrics pipeline instance."""
    global _validation_metrics_pipeline
    if _validation_metrics_pipeline is None:
        _validation_metrics_pipeline = ValidationMetricsPipeline()
    return _validation_metrics_pipeline
