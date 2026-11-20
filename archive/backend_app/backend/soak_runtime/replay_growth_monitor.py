"""
Replay Growth Monitor - Soak Runtime

This module monitors replay growth stability during long-duration runtime
for the strict algo trading platform. It detects replay growth anomalies,
buffer expansion, and sequence drift with bounded memory and async safety.

Author: Principal Institutional Operational Validation Engineer
"""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("replay_growth_monitor")


@dataclass
class ReplayGrowthMetrics:
    """Replay growth metrics."""
    timestamp: datetime
    replay_count: int
    replay_buffer_size: int
    sequence_drift: int
    growth_rate: float


@dataclass
class ReplayGrowthAlert:
    """Replay growth alert."""
    alert_type: str
    severity: str
    timestamp: datetime
    details: Dict[str, Any]


class ReplayGrowthMonitor:
    """
    Replay growth monitor for long-duration runtime.
    
    Monitors replay growth stability with bounded memory and async safety.
    Read-only validation only - zero execution mutation.
    """
    
    def __init__(self, max_history_samples: int = 1000):
        """
        Initialize replay growth monitor.
        
        Args:
            max_history_samples: Maximum number of history samples to keep (bounded memory)
        """
        self.redis = redis_manager
        self.max_history_samples = max_history_samples
        
        # Bounded memory - fixed-size deque
        self.metrics_history: deque[ReplayGrowthMetrics] = deque(maxlen=max_history_samples)
        
        # Alert thresholds
        self.growth_rate_warning_threshold = 1.5  # 50% growth rate
        self.growth_rate_critical_threshold = 2.0  # 100% growth rate
        self.buffer_size_warning_threshold = 1000000  # 1M entries
        self.buffer_size_critical_threshold = 10000000  # 10M entries
        self.sequence_drift_warning_threshold = 100
        self.sequence_drift_critical_threshold = 1000
        
        # Alert callbacks
        self.alert_callbacks: List[callable] = []
    
    def register_alert_callback(self, callback: callable):
        """
        Register alert callback.
        
        Args:
            callback: Alert callback function
        """
        self.alert_callbacks.append(callback)
    
    async def collect_metrics(self, tenant_id: Optional[str] = None) -> ReplayGrowthMetrics:
        """
        Collect replay growth metrics (read-only).
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            Replay growth metrics
        """
        try:
            # Count replay entries (read-only)
            pattern = f"replay_state:{tenant_id}:*" if tenant_id else "replay_state:*:*"
            replay_keys = await self.redis.keys(pattern)
            replay_count = len(replay_keys)
            
            # Estimate buffer size (read-only)
            pattern = f"replay_execution:{tenant_id}:*" if tenant_id else "replay_execution:*:*"
            buffer_keys = await self.redis.keys(pattern)
            replay_buffer_size = len(buffer_keys)
            
            # Calculate sequence drift (read-only)
            sequence_drift = await self._calculate_sequence_drift(tenant_id)
            
            # Calculate growth rate (read-only)
            growth_rate = await self._calculate_growth_rate(replay_count)
            
            metrics = ReplayGrowthMetrics(
                timestamp=datetime.now(timezone.utc),
                replay_count=replay_count,
                replay_buffer_size=replay_buffer_size,
                sequence_drift=sequence_drift,
                growth_rate=growth_rate
            )
            
            # Store in bounded history
            self.metrics_history.append(metrics)
            
            return metrics
            
        except Exception as e:
            logger.error(f"Failed to collect replay growth metrics: {e}")
            return ReplayGrowthMetrics(
                timestamp=datetime.now(timezone.utc),
                replay_count=0,
                replay_buffer_size=0,
                sequence_drift=0,
                growth_rate=0.0
            )
    
    async def _calculate_sequence_drift(self, tenant_id: Optional[str] = None) -> int:
        """
        Calculate sequence drift (read-only).
        
        Args:
            tenant_id: Optional tenant ID
            
        Returns:
            Sequence drift
        """
        try:
            pattern = f"replay_sequence:{tenant_id}:*" if tenant_id else "replay_sequence:*:*"
            keys = await self.redis.keys(pattern)
            
            if not keys:
                return 0
            
            sequences = []
            for key in keys:
                sequence = await self.redis.get(key)
                if sequence:
                    sequences.append(int(sequence))
            
            if not sequences:
                return 0
            
            # Calculate drift as difference between max and expected
            max_sequence = max(sequences)
            expected_sequence = len(sequences)
            
            drift = max_sequence - expected_sequence
            return max(0, drift)
            
        except Exception as e:
            logger.error(f"Failed to calculate sequence drift: {e}")
            return 0
    
    async def _calculate_growth_rate(self, current_count: int) -> float:
        """
        Calculate growth rate (read-only).
        
        Args:
            current_count: Current replay count
            
        Returns:
            Growth rate (multiplier)
        """
        if len(self.metrics_history) < 2:
            return 0.0
        
        # Get previous metrics
        previous_metrics = self.metrics_history[-2]
        previous_count = previous_metrics.replay_count
        
        if previous_count == 0:
            return 0.0
        
        growth_rate = current_count / previous_count
        return growth_rate
    
    async def check_growth_anomalies(self, tenant_id: Optional[str] = None) -> List[ReplayGrowthAlert]:
        """
        Check for replay growth anomalies (read-only).
        
        Args:
            tenant_id: Optional tenant ID for scoped validation
            
        Returns:
            List of alerts
        """
        alerts = []
        
        try:
            metrics = await self.collect_metrics(tenant_id)
            
            # Check growth rate
            if metrics.growth_rate >= self.growth_rate_critical_threshold:
                alert = ReplayGrowthAlert(
                    alert_type="replay_growth_critical",
                    severity="critical",
                    timestamp=datetime.now(timezone.utc),
                    details={
                        "growth_rate": metrics.growth_rate,
                        "replay_count": metrics.replay_count,
                        "threshold": self.growth_rate_critical_threshold
                    }
                )
                alerts.append(alert)
                await self._emit_alert(alert)
                
            elif metrics.growth_rate >= self.growth_rate_warning_threshold:
                alert = ReplayGrowthAlert(
                    alert_type="replay_growth_warning",
                    severity="warning",
                    timestamp=datetime.now(timezone.utc),
                    details={
                        "growth_rate": metrics.growth_rate,
                        "replay_count": metrics.replay_count,
                        "threshold": self.growth_rate_warning_threshold
                    }
                )
                alerts.append(alert)
                await self._emit_alert(alert)
            
            # Check buffer size
            if metrics.replay_buffer_size >= self.buffer_size_critical_threshold:
                alert = ReplayGrowthAlert(
                    alert_type="replay_growth_critical",
                    severity="critical",
                    timestamp=datetime.now(timezone.utc),
                    details={
                        "buffer_size": metrics.replay_buffer_size,
                        "threshold": self.buffer_size_critical_threshold,
                        "issue": "buffer_expansion_critical"
                    }
                )
                alerts.append(alert)
                await self._emit_alert(alert)
                
            elif metrics.replay_buffer_size >= self.buffer_size_warning_threshold:
                alert = ReplayGrowthAlert(
                    alert_type="replay_growth_warning",
                    severity="warning",
                    timestamp=datetime.now(timezone.utc),
                    details={
                        "buffer_size": metrics.replay_buffer_size,
                        "threshold": self.buffer_size_warning_threshold,
                        "issue": "buffer_expansion_warning"
                    }
                )
                alerts.append(alert)
                await self._emit_alert(alert)
            
            # Check sequence drift
            if metrics.sequence_drift >= self.sequence_drift_critical_threshold:
                alert = ReplayGrowthAlert(
                    alert_type="replay_divergence_detected",
                    severity="critical",
                    timestamp=datetime.now(timezone.utc),
                    details={
                        "sequence_drift": metrics.sequence_drift,
                        "threshold": self.sequence_drift_critical_threshold,
                        "issue": "sequence_drift_critical"
                    }
                )
                alerts.append(alert)
                await self._emit_alert(alert)
                
            elif metrics.sequence_drift >= self.sequence_drift_warning_threshold:
                alert = ReplayGrowthAlert(
                    alert_type="replay_divergence_detected",
                    severity="warning",
                    timestamp=datetime.now(timezone.utc),
                    details={
                        "sequence_drift": metrics.sequence_drift,
                        "threshold": self.sequence_drift_warning_threshold,
                        "issue": "sequence_drift_warning"
                    }
                )
                alerts.append(alert)
                await self._emit_alert(alert)
            
            return alerts
            
        except Exception as e:
            logger.error(f"Failed to check growth anomalies: {e}")
            return []
    
    async def _emit_alert(self, alert: ReplayGrowthAlert):
        """
        Emit alert to registered callbacks.
        
        Args:
            alert: Alert to emit
        """
        for callback in self.alert_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(alert)
                else:
                    callback(alert)
            except Exception as e:
                logger.error(f"Alert callback failed: {e}")
    
    async def get_growth_trend(self, hours: int = 24) -> Dict[str, Any]:
        """
        Get replay growth trend (read-only).
        
        Args:
            hours: Number of hours to analyze
            
        Returns:
            Growth trend data
        """
        try:
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            
            # Filter metrics by time window
            recent_metrics = [
                m for m in self.metrics_history
                if m.timestamp > cutoff_time
            ]
            
            if not recent_metrics:
                return {
                    "time_window_hours": hours,
                    "data_points": 0,
                    "trend": "no_data"
                }
            
            # Calculate trend
            initial_count = recent_metrics[0].replay_count
            final_count = recent_metrics[-1].replay_count
            growth = final_count - initial_count
            growth_rate = (growth / initial_count * 100) if initial_count > 0 else 0
            
            avg_growth_rate = sum(m.growth_rate for m in recent_metrics) / len(recent_metrics)
            
            # Determine trend
            if growth_rate > 50:
                trend = "critical_growth"
            elif growth_rate > 20:
                trend = "high_growth"
            elif growth_rate > 0:
                trend = "normal_growth"
            elif growth_rate > -20:
                trend = "stable"
            else:
                trend = "declining"
            
            return {
                "time_window_hours": hours,
                "data_points": len(recent_metrics),
                "initial_count": initial_count,
                "final_count": final_count,
                "growth": growth,
                "growth_rate_percent": growth_rate,
                "avg_growth_rate": avg_growth_rate,
                "trend": trend,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to get growth trend: {e}")
            return {
                "time_window_hours": hours,
                "data_points": 0,
                "trend": "error",
                "error": str(e)
            }
    
    async def start_monitoring(self, interval_seconds: int = 60, tenant_id: Optional[str] = None):
        """
        Start continuous monitoring (async safe).
        
        Args:
            interval_seconds: Monitoring interval in seconds
            tenant_id: Optional tenant ID for scoped validation
        """
        logger.info(f"Starting replay growth monitoring with interval: {interval_seconds}s")
        
        while True:
            try:
                # Check for anomalies
                alerts = await self.check_growth_anomalies(tenant_id)
                
                # Log alerts
                for alert in alerts:
                    if alert.severity == "critical":
                        logger.critical(f"Replay growth alert: {alert.alert_type} - {alert.details}")
                    elif alert.severity == "warning":
                        logger.warning(f"Replay growth alert: {alert.alert_type} - {alert.details}")
                
                await asyncio.sleep(interval_seconds)
                
            except asyncio.CancelledError:
                logger.info("Replay growth monitoring cancelled")
                break
            except Exception as e:
                logger.error(f"Replay growth monitoring error: {e}")
                await asyncio.sleep(interval_seconds)
    
    def get_memory_usage(self) -> Dict[str, Any]:
        """
        Get memory usage statistics.
        
        Returns:
            Memory usage data
        """
        return {
            "history_samples": len(self.metrics_history),
            "max_history_samples": self.max_history_samples,
            "memory_usage_percent": (len(self.metrics_history) / self.max_history_samples * 100)
        }
    
    def clear_history(self):
        """Clear metrics history (bounded memory management)."""
        self.metrics_history.clear()
        logger.info("Replay growth metrics history cleared")


# Global instance
_replay_growth_monitor: ReplayGrowthMonitor = None


def get_replay_growth_monitor(max_history_samples: int = 1000) -> ReplayGrowthMonitor:
    """Get or create replay growth monitor instance."""
    global _replay_growth_monitor
    if _replay_growth_monitor is None:
        _replay_growth_monitor = ReplayGrowthMonitor(max_history_samples)
    return _replay_growth_monitor
