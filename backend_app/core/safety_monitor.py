"""
Safety Monitor

Tracks and logs all execution attempts, especially blocked unsafe executions.
Used for security auditing and compliance.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class BlockedExecutionEvent:
    """Record of a blocked execution attempt."""
    timestamp: datetime
    source: str
    context: str
    tenant_id: Optional[str]
    strategy_id: Optional[str]
    symbol: Optional[str]
    action: Optional[str]
    details: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "context": self.context,
            "tenant_id": self.tenant_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "action": self.action,
            "details": self.details
        }


class SafetyMonitor:
    """
    Monitors execution safety across the system.
    
    Critical for:
    - Detecting attempted unsafe executions
    - Security auditing
    - Compliance reporting
    - Debugging blocked paths
    """
    
    def __init__(self):
        self._blocked_events: list = []
        self._max_events = 10000  # Prevent memory leak
    
    def log_blocked_execution(
        self,
        source: str,
        context: str,
        tenant_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        symbol: Optional[str] = None,
        action: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log a blocked execution attempt.
        
        CRITICAL: This should be called whenever an unsafe path is blocked.
        """
        event = BlockedExecutionEvent(
            timestamp=datetime.utcnow(),
            source=source,
            context=context,
            tenant_id=tenant_id,
            strategy_id=strategy_id,
            symbol=symbol,
            action=action,
            details=details or {}
        )
        
        # Log to system logger (critical level)
        logger.critical(
            f"🚫 BLOCKED EXECUTION: {source} | Context: {context} | "
            f"Tenant: {tenant_id} | Strategy: {strategy_id} | "
            f"Symbol: {symbol} | Action: {action} | "
            f"Details: {details}"
        )
        
        # Store in memory (for real-time monitoring)
        self._blocked_events.append(event)
        
        # Prevent unbounded growth
        if len(self._blocked_events) > self._max_events:
            self._blocked_events = self._blocked_events[-self._max_events:]
    
    def log_enabled_execution(
        self,
        source: str,
        context: str,
        tenant_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log an allowed execution via safe path.
        
        Used for verifying safe paths are working.
        """
        logger.info(
            f"✅ ALLOWED EXECUTION: {source} | Context: {context} | "
            f"Tenant: {tenant_id} | ExecutionID: {execution_id}"
        )
    
    def get_blocked_events(
        self,
        since: Optional[datetime] = None,
        context: Optional[str] = None,
        limit: int = 100
    ) -> list:
        """Get recent blocked execution events."""
        events = self._blocked_events
        
        if since:
            events = [e for e in events if e.timestamp >= since]
        
        if context:
            events = [e for e in events if e.context == context]
        
        return events[-limit:]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get safety statistics."""
        total_blocked = len(self._blocked_events)
        
        by_context = {}
        for event in self._blocked_events:
            by_context[event.context] = by_context.get(event.context, 0) + 1
        
        return {
            "total_blocked_events": total_blocked,
            "blocked_by_context": by_context,
            "monitoring_active": True
        }


# Global safety monitor instance
safety_monitor = SafetyMonitor()


def log_blocked_execution(
    source: str,
    context: str,
    tenant_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    symbol: Optional[str] = None,
    action: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None
) -> None:
    """Convenience function for logging blocked executions."""
    safety_monitor.log_blocked_execution(
        source=source,
        context=context,
        tenant_id=tenant_id,
        strategy_id=strategy_id,
        symbol=symbol,
        action=action,
        details=details
    )


def log_enabled_execution(
    source: str,
    context: str,
    tenant_id: Optional[str] = None,
    execution_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None
) -> None:
    """Convenience function for logging allowed executions."""
    safety_monitor.log_enabled_execution(
        source=source,
        context=context,
        tenant_id=tenant_id,
        execution_id=execution_id,
        details=details
    )
