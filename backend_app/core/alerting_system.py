"""
core/alerting_system.py — Multi-Channel Alerting System

🔴 STEP 8 — ALERTING SYSTEM (MANDATORY)

Provides comprehensive alerting across multiple channels to ensure
no critical events go unnoticed.

TRIGGER ALERTS FOR:
- UNKNOWN order status
- Exchange timeout
- Reconciliation mismatch
- Kill switch activation
- Rate limit breach

CHANNELS:
- logs (structured)
- webhook (HTTP POST)
- email (optional)

EXPECTED RESULT:
✔ No silent failures
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import aiohttp

from backend_app.backend.redis_manager import redis_manager

logger = logging.getLogger("AlertingSystem")


class AlertSeverity(Enum):
    """Severity levels for alerts."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AlertType(Enum):
    """Types of alerts."""
    UNKNOWN_ORDER_STATUS = "unknown_order_status"
    EXCHANGE_TIMEOUT = "exchange_timeout"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"
    KILL_SWITCH_ACTIVATED = "kill_switch_activated"
    RATE_LIMIT_BREACH = "rate_limit_breach"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    POSITION_MISMATCH = "position_mismatch"
    WEBSOCKET_DISCONNECT = "websocket_disconnect"
    ORDER_EXECUTION_FAILED = "order_execution_failed"
    SYSTEM_ERROR = "system_error"
    # ── Strategy Builder (strategy-builder task 9.2, Requirements 24.4/24.5) ──
    # Added here rather than in a builder-shaped enum of their own, so there is one alert
    # vocabulary and one dispatcher. The conditions, their thresholds and the reason each
    # threshold is what it is live in `backend/builder_alerts.py`; this module still owns
    # delivery, severity routing and the five-minute deduplication window. The dedup key is
    # `(type, source, tenant_id)`, and all four are raised with `tenant_id=None` because
    # they are platform-health facts rather than tenant events.
    BUILDER_INTENT_BLOCKED_NON_FINITE = "builder_intent_blocked_non_finite"
    BUILDER_FEED_STALE = "builder_feed_stale"
    BUILDER_TRAINING_QUEUE_DEPTH = "builder_training_queue_depth"
    BUILDER_ASSET_UNIVERSE_STALE = "builder_asset_universe_stale"


@dataclass
class Alert:
    """
    Alert data structure.
    
    Contains all information needed for multi-channel delivery.
    """
    alert_id: str
    timestamp: datetime
    alert_type: AlertType
    severity: AlertSeverity
    title: str
    message: str
    source: str
    tenant_id: Optional[str]
    execution_id: Optional[str]
    details: Dict[str, Any]
    suggested_action: Optional[str]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "alert_id": self.alert_id,
            "timestamp": self.timestamp.isoformat(),
            "alert_type": self.alert_type.value,
            "severity": self.severity.value,
            "title": self.title,
            "message": self.message,
            "source": self.source,
            "tenant_id": self.tenant_id,
            "execution_id": self.execution_id,
            "details": self.details,
            "suggested_action": self.suggested_action
        }
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), default=str)


class AlertingSystem:
    """
    🔴 STEP 8 — ALERTING SYSTEM (MANDATORY)
    
    Multi-channel alerting system for critical trading events.
    
    ALERT TRIGGERS:
        ✓ UNKNOWN order status
        ✓ Exchange timeout
        ✓ Reconciliation mismatch
        ✓ Kill switch activation
        ✓ Rate limit breach
        ✓ Circuit breaker open
        ✓ Position mismatch
        ✓ WebSocket disconnect
        ✓ Order execution failed
        ✓ System error
    
    CHANNELS:
        1. Logs - Structured JSON logging
        2. Webhook - HTTP POST to external systems
        3. Email - SMTP email notifications (optional)
        4. Redis - Pub/Sub for real-time subscribers
    
    DEDUPLICATION:
        - Alerts deduplicated by (type, source, tenant_id) hash
        - 5-minute deduplication window
    
    EXPECTED RESULT:
        ✔ No silent failures
    """
    
    # Deduplication window in seconds
    DEDUP_WINDOW_SECONDS = 300  # 5 minutes
    
    def __init__(
        self,
        webhook_url: Optional[str] = None,
        smtp_host: Optional[str] = None,
        smtp_port: int = 587,
        alert_email: Optional[str] = None,
        from_email: Optional[str] = None
    ):
        self.webhook_url = webhook_url
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.alert_email = alert_email
        self.from_email = from_email
        
        self._alert_sequence = 0
        self._handlers: List[Callable] = []
    
    def _generate_alert_id(self) -> str:
        """Generate unique alert ID."""
        self._alert_sequence += 1
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        return f"ALERT-{timestamp}-{self._alert_sequence:06d}"
    
    def _generate_dedup_key(
        self,
        alert_type: AlertType,
        source: str,
        tenant_id: Optional[str]
    ) -> str:
        """Generate deduplication key."""
        key_parts = [alert_type.value, source, tenant_id or "global"]
        key_hash = hash(tuple(key_parts)) % 10000000
        return f"alert_dedup:{key_hash}"
    
    async def _check_dedup(
        self,
        alert_type: AlertType,
        source: str,
        tenant_id: Optional[str]
    ) -> bool:
        """
        Check if this alert should be deduplicated.
        
        Returns True if alert should be sent (not a duplicate).
        """
        try:
            dedup_key = self._generate_dedup_key(alert_type, source, tenant_id)
            
            # Check if key exists
            exists = await redis_manager.exists(dedup_key)
            
            if exists:
                # Duplicate - don't send
                return False
            
            # Set dedup key with TTL
            await redis_manager.setex(
                dedup_key,
                self.DEDUP_WINDOW_SECONDS,
                "1"
            )
            
            return True
        
        except Exception as e:
            logger.error(f"STEP 8: Dedup check failed: {e}")
            # Fail-safe: send the alert
            return True
    
    async def send_alert(
        self,
        alert_type: AlertType,
        severity: AlertSeverity,
        title: str,
        message: str,
        source: str,
        tenant_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        suggested_action: Optional[str] = None,
        force: bool = False
    ) -> Optional[Alert]:
        """
        Send alert through all channels.
        
        Args:
            alert_type: Type of alert
            severity: Severity level
            title: Short alert title
            message: Detailed message
            source: Component that triggered alert
            tenant_id: Optional tenant identifier
            execution_id: Optional execution identifier
            details: Additional context
            suggested_action: Recommended action
            force: Skip deduplication if True
            
        Returns:
            Alert object if sent, None if deduplicated
        """
        # Check deduplication
        if not force:
            should_send = await self._check_dedup(alert_type, source, tenant_id)
            if not should_send:
                logger.debug(f"STEP 8: Alert deduplicated: {alert_type.value}")
                return None
        
        # Create alert
        alert = Alert(
            alert_id=self._generate_alert_id(),
            timestamp=datetime.utcnow(),
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            source=source,
            tenant_id=tenant_id,
            execution_id=execution_id,
            details=details or {},
            suggested_action=suggested_action
        )
        
        # Send to all channels concurrently
        tasks = [
            self._send_to_log(alert),
            self._send_to_webhook(alert),
            self._send_to_redis_pubsub(alert),
        ]
        
        # Email only for critical/high severity
        if severity in (AlertSeverity.CRITICAL, AlertSeverity.HIGH):
            if self.smtp_host and self.alert_email:
                tasks.append(self._send_to_email(alert))
        
        # Execute all channels
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Check for failures
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"STEP 8: Alert channel {i} failed: {result}")
        
        logger.info(
            f"STEP 8: Alert sent: {alert.alert_id} - {alert.title} "
            f"({alert.severity.value})"
        )
        
        return alert
    
    async def _send_to_log(self, alert: Alert):
        """Send alert to structured log."""
        log_entry = alert.to_json()
        
        # Use appropriate log level
        if alert.severity == AlertSeverity.CRITICAL:
            logger.critical(log_entry)
        elif alert.severity == AlertSeverity.HIGH:
            logger.error(log_entry)
        elif alert.severity == AlertSeverity.MEDIUM:
            logger.warning(log_entry)
        else:
            logger.info(log_entry)
    
    async def _send_to_webhook(self, alert: Alert):
        """Send alert via HTTP POST webhook."""
        if not self.webhook_url:
            return
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json=alert.to_dict(),
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status >= 400:
                        logger.error(
                            f"STEP 8: Webhook failed: {response.status}"
                        )
        
        except Exception as e:
            logger.error(f"STEP 8: Webhook error: {e}")
    
    async def _send_to_email(self, alert: Alert):
        """Send alert via email (optional)."""
        if not self.smtp_host or not self.alert_email:
            return
        
        try:
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            import aiosmtplib

            # Create message
            msg = MIMEMultipart()
            msg["From"] = self.from_email or "alerts@aerora.trading"
            msg["To"] = self.alert_email
            msg["Subject"] = f"[{alert.severity.value.upper()}] {alert.title}"
            
            # Body
            body = f"""
ALERT: {alert.title}

Severity: {alert.severity.value.upper()}
Type: {alert.alert_type.value}
Source: {alert.source}
Time: {alert.timestamp.isoformat()}
Tenant: {alert.tenant_id or "N/A"}
Execution: {alert.execution_id or "N/A"}

Message:
{alert.message}

Details:
{json.dumps(alert.details, indent=2, default=str)}

Suggested Action:
{alert.suggested_action or "None"}

Alert ID: {alert.alert_id}
            """
            
            msg.attach(MIMEText(body, "plain"))
            
            # Send email
            await aiosmtplib.send(
                msg,
                hostname=self.smtp_host,
                port=self.smtp_port,
                use_tls=True
            )
            
            logger.info(f"STEP 8: Email alert sent: {alert.alert_id}")
        
        except Exception as e:
            logger.error(f"STEP 8: Email alert failed: {e}")
    
    async def _send_to_redis_pubsub(self, alert: Alert):
        """Publish alert to Redis Pub/Sub for real-time subscribers."""
        try:
            channel = f"alerts:{alert.severity.value}"
            await redis_manager.publish(channel, alert.to_json())
            
            # Also publish to all-alerts channel
            await redis_manager.publish("alerts:all", alert.to_json())
        
        except Exception as e:
            logger.error(f"STEP 8: Redis pub/sub failed: {e}")
    
    # ═══════════════════════════════════════════════════════════════════
    # PRE-DEFINED ALERT TRIGGERS
    # ═══════════════════════════════════════════════════════════════════
    
    async def alert_unknown_order_status(
        self,
        tenant_id: str,
        execution_id: str,
        order_id: str,
        details: Dict[str, Any]
    ):
        """Alert: UNKNOWN order status detected."""
        await self.send_alert(
            alert_type=AlertType.UNKNOWN_ORDER_STATUS,
            severity=AlertSeverity.CRITICAL,
            title="Order Status UNKNOWN",
            message=f"Order {order_id} has UNKNOWN status. Manual intervention required.",
            source="order_watchdog",
            tenant_id=tenant_id,
            execution_id=execution_id,
            details=details,
            suggested_action="Check exchange directly and reconcile position."
        )
    
    async def alert_exchange_timeout(
        self,
        tenant_id: str,
        exchange_id: str,
        operation: str,
        timeout_seconds: float,
        details: Dict[str, Any]
    ):
        """Alert: Exchange timeout occurred."""
        await self.send_alert(
            alert_type=AlertType.EXCHANGE_TIMEOUT,
            severity=AlertSeverity.HIGH,
            title=f"Exchange Timeout: {exchange_id}",
            message=f"Operation '{operation}' timed out after {timeout_seconds}s on {exchange_id}.",
            source="exchange_executor",
            tenant_id=tenant_id,
            details=details,
            suggested_action="Check exchange status and consider kill switch if persistent."
        )
    
    async def alert_reconciliation_mismatch(
        self,
        tenant_id: str,
        execution_id: str,
        local_state: Dict[str, Any],
        exchange_state: Dict[str, Any],
        details: Dict[str, Any]
    ):
        """Alert: Reconciliation mismatch detected."""
        await self.send_alert(
            alert_type=AlertType.RECONCILIATION_MISMATCH,
            severity=AlertSeverity.CRITICAL,
            title="Reconciliation Mismatch",
            message=f"Local state does not match exchange state for execution {execution_id}.",
            source="reconciliation_engine",
            tenant_id=tenant_id,
            execution_id=execution_id,
            details={
                **details,
                "local_state": local_state,
                "exchange_state": exchange_state
            },
            suggested_action="Investigate immediately. Kill switch may be activated."
        )
    
    async def alert_kill_switch_activated(
        self,
        reason: str,
        triggered_by: str,
        tenant_id: Optional[str] = None
    ):
        """Alert: Kill switch activated."""
        await self.send_alert(
            alert_type=AlertType.KILL_SWITCH_ACTIVATED,
            severity=AlertSeverity.CRITICAL,
            title="🔴 KILL SWITCH ACTIVATED 🔴",
            message=f"Global kill switch activated. Reason: {reason}",
            source=triggered_by,
            tenant_id=tenant_id,
            details={"reason": reason},
            suggested_action="Investigate root cause before deactivating. Manual intervention required.",
            force=True  # Always send, never deduplicate
        )
    
    async def alert_rate_limit_breach(
        self,
        tenant_id: str,
        operation: str,
        current_count: int,
        limit: int,
        details: Dict[str, Any]
    ):
        """Alert: Rate limit breached."""
        await self.send_alert(
            alert_type=AlertType.RATE_LIMIT_BREACH,
            severity=AlertSeverity.HIGH,
            title="Rate Limit Breached",
            message=f"Rate limit exceeded for '{operation}': {current_count}/{limit}",
            source="distributed_rate_limiter",
            tenant_id=tenant_id,
            details={
                **details,
                "current_count": current_count,
                "limit": limit
            },
            suggested_action="Review rate limiting configuration or reduce request frequency."
        )
    
    async def alert_circuit_breaker_open(
        self,
        exchange_id: str,
        failure_count: int,
        threshold: int,
        details: Dict[str, Any]
    ):
        """Alert: Circuit breaker opened."""
        await self.send_alert(
            alert_type=AlertType.CIRCUIT_BREAKER_OPEN,
            severity=AlertSeverity.HIGH,
            title=f"Circuit Breaker OPEN: {exchange_id}",
            message=f"Circuit breaker opened after {failure_count} failures (threshold: {threshold}).",
            source="circuit_breaker",
            details={
                **details,
                "exchange_id": exchange_id,
                "failure_count": failure_count,
                "threshold": threshold
            },
            suggested_action="Wait for recovery timeout or manually verify exchange health."
        )
    
    async def alert_position_mismatch(
        self,
        tenant_id: str,
        symbol: str,
        local_position: float,
        exchange_position: float,
        difference: float,
        details: Dict[str, Any]
    ):
        """Alert: Position mismatch detected."""
        await self.send_alert(
            alert_type=AlertType.POSITION_MISMATCH,
            severity=AlertSeverity.CRITICAL,
            title=f"Position Mismatch: {symbol}",
            message=f"Position mismatch for {symbol}: local={local_position}, exchange={exchange_position}, diff={difference}",
            source="consistency_checker",
            tenant_id=tenant_id,
            details={
                **details,
                "symbol": symbol,
                "local_position": local_position,
                "exchange_position": exchange_position,
                "difference": difference
            },
            suggested_action="Kill switch may activate. Investigate immediately."
        )
    
    async def alert_websocket_disconnect(
        self,
        tenant_id: str,
        exchange_id: str,
        channel: str,
        details: Dict[str, Any]
    ):
        """Alert: WebSocket disconnected."""
        await self.send_alert(
            alert_type=AlertType.WEBSOCKET_DISCONNECT,
            severity=AlertSeverity.MEDIUM,
            title=f"WebSocket Disconnect: {exchange_id}",
            message=f"WebSocket channel '{channel}' disconnected from {exchange_id}.",
            source="exchange_websocket_listener",
            tenant_id=tenant_id,
            details={
                **details,
                "exchange_id": exchange_id,
                "channel": channel
            },
            suggested_action="Will auto-reconnect. Check if persistent issue."
        )
    
    async def alert_order_execution_failed(
        self,
        tenant_id: str,
        execution_id: str,
        order_id: str,
        error_message: str,
        details: Dict[str, Any]
    ):
        """Alert: Order execution failed."""
        await self.send_alert(
            alert_type=AlertType.ORDER_EXECUTION_FAILED,
            severity=AlertSeverity.HIGH,
            title="Order Execution Failed",
            message=f"Order {order_id} execution failed: {error_message}",
            source="unified_execution_engine",
            tenant_id=tenant_id,
            execution_id=execution_id,
            details={
                **details,
                "order_id": order_id,
                "error_message": error_message
            },
            suggested_action="Check order status and retry if appropriate."
        )


# Global singleton instance
_alerting_system: Optional[AlertingSystem] = None


def get_alerting_system(
    webhook_url: Optional[str] = None,
    smtp_host: Optional[str] = None,
    alert_email: Optional[str] = None
) -> AlertingSystem:
    """Get or create the alerting system instance."""
    global _alerting_system
    if _alerting_system is None:
        _alerting_system = AlertingSystem(
            webhook_url=webhook_url,
            smtp_host=smtp_host,
            alert_email=alert_email
        )
    return _alerting_system


# Convenience exports
__all__ = [
    "AlertingSystem",
    "Alert",
    "AlertType",
    "AlertSeverity",
    "get_alerting_system",
]
