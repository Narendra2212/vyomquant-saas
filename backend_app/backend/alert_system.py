"""
Alert System

STEP 7.8 — PRODUCTION HARDENING

Sends alerts for critical failures.

Channels:
┌─────────────────────────────────────────────────────────────────┐
│  Telegram                                                        │
│  ├─ Instant push notifications                                   │
│  ├─ Bot sends message to channel/group                           │
│  └─ Good for mobile alerts                                       │
├─────────────────────────────────────────────────────────────────┤
│  Email                                                           │
│  ├─ Detailed HTML reports                                        │
│  ├─ Slower but persistent                                        │
│  └─ Good for audit trail                                         │
├─────────────────────────────────────────────────────────────────┤
│  Webhook                                                         │
│  ├─ POST to external systems                                     │
│  └─ Good for integration with PagerDuty, etc.                    │
└─────────────────────────────────────────────────────────────────┘

Alert Levels:
┌─────────────────────────────────────────────────────────────────┐
│  INFO:     Normal operational events                            │
│  WARNING:  Issues that need attention                           │
│  CRITICAL: Immediate action required                            │
│  EMERGENCY: System halt condition                               │
└─────────────────────────────────────────────────────────────────┘
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class Alert:
    """Alert data structure."""
    level: AlertLevel
    title: str
    message: str
    timestamp: datetime
    metadata: Optional[Dict[str, Any]] = None
    tenant_id: Optional[str] = None


class AlertChannel(ABC):
    """Abstract base class for alert channels."""
    
    @abstractmethod
    async def send(self, alert: Alert) -> bool:
        """Send alert through this channel."""
        pass
    
    @abstractmethod
    def is_configured(self) -> bool:
        """Check if channel is properly configured."""
        pass


class TelegramAlertChannel(AlertChannel):
    """
    STEP 7.8: Telegram alert channel.
    
    Sends alerts via Telegram bot.
    
    Setup:
    1. Create bot via @BotFather
    2. Get bot token
    3. Create channel/group
    4. Add bot to channel
    5. Get channel ID
    """
    
    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
    
    def is_configured(self) -> bool:
        """Check if Telegram is configured."""
        return bool(self.bot_token and self.chat_id)
    
    async def send(self, alert: Alert) -> bool:
        """Send alert via Telegram."""
        if not self.is_configured():
            logger.warning("Telegram not configured, skipping")
            return False
        
        try:
            # Import here to avoid dependency if not used
            import aiohttp

            # Format message
            emoji_map = {
                AlertLevel.INFO: "ℹ️",
                AlertLevel.WARNING: "⚠️",
                AlertLevel.CRITICAL: "🚨",
                AlertLevel.EMERGENCY: "🔥"
            }
            
            emoji = emoji_map.get(alert.level, "📢")
            
            message = f"""
{emoji} <b>{alert.level.value.upper()}: {alert.title}</b>

{alert.message}

🕐 {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}
            """
            
            # Add metadata if present
            if alert.metadata:
                message += "\n📋 <b>Details:</b>\n"
                for key, value in alert.metadata.items():
                    message += f"• {key}: {value}\n"
            
            # Send via Telegram API
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        logger.info(f"Telegram alert sent: {alert.title}")
                        return True
                    else:
                        text = await response.text()
                        logger.error(f"Telegram API error: {response.status} - {text}")
                        return False
                        
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")
            return False


class EmailAlertChannel(AlertChannel):
    """
    STEP 7.8: Email alert channel.
    
    Sends alerts via SMTP.
    """
    
    def __init__(
        self,
        smtp_host: Optional[str] = None,
        smtp_port: int = 587,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        from_address: Optional[str] = None,
        to_addresses: Optional[List[str]] = None
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.from_address = from_address
        self.to_addresses = to_addresses or []
    
    def is_configured(self) -> bool:
        """Check if email is configured."""
        return bool(
            self.smtp_host and
            self.smtp_user and
            self.smtp_password and
            self.from_address and
            self.to_addresses
        )
    
    async def send(self, alert: Alert) -> bool:
        """Send alert via email."""
        if not self.is_configured():
            logger.warning("Email not configured, skipping")
            return False
        
        try:
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            import aiosmtplib

            # Build HTML email
            subject = f"[{alert.level.value.upper()}] {alert.title}"
            
            html_body = f"""
            <html>
            <body>
                <h2 style="color: {'red' if alert.level in (AlertLevel.CRITICAL, AlertLevel.EMERGENCY) else 'orange'}">
                    {alert.level.value.upper()}: {alert.title}
                </h2>
                <p><strong>Message:</strong> {alert.message}</p>
                <p><strong>Time:</strong> {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
                
                {self._format_metadata(alert.metadata)}
            </body>
            </html>
            """
            
            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.from_address
            msg['To'] = ', '.join(self.to_addresses)
            
            html_part = MIMEText(html_body, 'html')
            msg.attach(html_part)
            
            # Send
            await aiosmtplib.send(
                msg,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.smtp_user,
                password=self.smtp_password,
                start_tls=True
            )
            
            logger.info(f"Email alert sent: {alert.title}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")
            return False
    
    def _format_metadata(self, metadata: Optional[Dict[str, Any]]) -> str:
        """Format metadata as HTML table."""
        if not metadata:
            return ""
        
        rows = ""
        for key, value in metadata.items():
            rows += f"<tr><td><strong>{key}</strong></td><td>{value}</td></tr>\n"
        
        return f"""
        <h3>Details:</h3>
        <table border="1" cellpadding="5">
            {rows}
        </table>
        """


class WebhookAlertChannel(AlertChannel):
    """
    STEP 7.8: Webhook alert channel.
    
    POSTs alerts to external systems.
    Good for PagerDuty, Slack, Discord, etc.
    """
    
    def __init__(
        self,
        webhook_url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None
    ):
        self.webhook_url = webhook_url
        self.headers = headers or {}
    
    def is_configured(self) -> bool:
        """Check if webhook is configured."""
        return bool(self.webhook_url)
    
    async def send(self, alert: Alert) -> bool:
        """Send alert via webhook."""
        if not self.is_configured():
            logger.warning("Webhook not configured, skipping")
            return False
        
        try:
            import aiohttp
            
            payload = {
                "level": alert.level.value,
                "title": alert.title,
                "message": alert.message,
                "timestamp": alert.timestamp.isoformat(),
                "metadata": alert.metadata or {}
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json=payload,
                    headers=self.headers
                ) as response:
                    if response.status in (200, 201, 202, 204):
                        logger.info(f"Webhook alert sent: {alert.title}")
                        return True
                    else:
                        text = await response.text()
                        logger.error(
                            f"Webhook error: {response.status} - {text}"
                        )
                        return False
                        
        except Exception as e:
            logger.error(f"Failed to send webhook alert: {e}")
            return False


class AlertSystem:
    """
    STEP 7.8: Main alert system.
    
    Manages multiple alert channels and routes alerts appropriately.
    
    Usage:
        alert_system = AlertSystem()
        alert_system.add_channel(TelegramAlertChannel(bot_token, chat_id))
        alert_system.add_channel(EmailAlertChannel(...))
        
        await alert_system.send_critical_alert(
            title="Order Stuck",
            message="Order has been pending for > 5 minutes",
            metadata={"order_id": "12345"}
        )
    """
    
    def __init__(self):
        self._channels: List[AlertChannel] = []
        self._alert_history: List[Alert] = []
        self._max_history = 1000
        
        # Rate limiting
        self._last_alert_time: Dict[str, float] = {}
        self._min_interval_seconds = 60  # Don't spam
    
    def add_channel(self, channel: AlertChannel):
        """Add an alert channel."""
        self._channels.append(channel)
        logger.info(f"Added alert channel: {channel.__class__.__name__}")
    
    async def send_alert(
        self,
        level: AlertLevel,
        title: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None
    ) -> bool:
        """
        Send alert through all configured channels.
        
        Returns:
            True if at least one channel succeeded
        """
        # Rate limiting check
        alert_key = f"{level.value}:{title}"
        now = datetime.utcnow().timestamp()
        
        if alert_key in self._last_alert_time:
            elapsed = now - self._last_alert_time[alert_key]
            if elapsed < self._min_interval_seconds:
                logger.debug(f"Alert rate limited: {title}")
                return False
        
        self._last_alert_time[alert_key] = now
        
        # Create alert
        alert = Alert(
            level=level,
            title=title,
            message=message,
            timestamp=datetime.utcnow(),
            metadata=metadata,
            tenant_id=tenant_id
        )
        
        # Store in history
        self._alert_history.append(alert)
        if len(self._alert_history) > self._max_history:
            self._alert_history.pop(0)
        
        # Send to all channels
        results = []
        for channel in self._channels:
            if channel.is_configured():
                try:
                    success = await channel.send(alert)
                    results.append(success)
                except Exception as e:
                    logger.error(f"Channel error: {e}")
                    results.append(False)
        
        # Log
        if any(results):
            logger.info(f"Alert sent ({level.value}): {title}")
            return True
        else:
            logger.error(f"Failed to send alert: {title}")
            return False
    
    async def send_info_alert(
        self,
        title: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None
    ) -> bool:
        """Send INFO level alert."""
        return await self.send_alert(
            AlertLevel.INFO, title, message, metadata, tenant_id
        )
    
    async def send_warning_alert(
        self,
        title: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None
    ) -> bool:
        """Send WARNING level alert."""
        return await self.send_alert(
            AlertLevel.WARNING, title, message, metadata, tenant_id
        )
    
    async def send_critical_alert(
        self,
        title: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None
    ) -> bool:
        """Send CRITICAL level alert."""
        return await self.send_alert(
            AlertLevel.CRITICAL, title, message, metadata, tenant_id
        )
    
    async def send_emergency_alert(
        self,
        title: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None
    ) -> bool:
        """Send EMERGENCY level alert."""
        return await self.send_alert(
            AlertLevel.EMERGENCY, title, message, metadata, tenant_id
        )
    
    def get_recent_alerts(
        self,
        level: Optional[AlertLevel] = None,
        limit: int = 100
    ) -> List[Alert]:
        """Get recent alerts, optionally filtered by level."""
        alerts = self._alert_history
        
        if level:
            alerts = [a for a in alerts if a.level == level]
        
        return alerts[-limit:]


# Global instance
_alert_system: Optional[AlertSystem] = None


def get_alert_system() -> AlertSystem:
    """Get or create global alert system."""
    global _alert_system
    if _alert_system is None:
        _alert_system = AlertSystem()
        
        # Configure from environment if available
        import os

        # Telegram
        telegram_token = os.getenv("ALERT_TELEGRAM_BOT_TOKEN")
        telegram_chat = os.getenv("ALERT_TELEGRAM_CHAT_ID")
        if telegram_token and telegram_chat:
            _alert_system.add_channel(
                TelegramAlertChannel(telegram_token, telegram_chat)
            )
        
        # Email
        smtp_host = os.getenv("ALERT_SMTP_HOST")
        smtp_user = os.getenv("ALERT_SMTP_USER")
        smtp_pass = os.getenv("ALERT_SMTP_PASSWORD")
        from_addr = os.getenv("ALERT_FROM_EMAIL")
        to_addrs = os.getenv("ALERT_TO_EMAILS", "").split(",")
        
        if all([smtp_host, smtp_user, smtp_pass, from_addr, to_addrs]):
            _alert_system.add_channel(
                EmailAlertChannel(
                    smtp_host=smtp_host,
                    smtp_user=smtp_user,
                    smtp_password=smtp_pass,
                    from_address=from_addr,
                    to_addresses=[e.strip() for e in to_addrs if e.strip()]
                )
            )
    
    return _alert_system
