"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 1 — BACKEND: alert_engine.py                                      ║
║                                                                          ║
║  Pluggable Alert System with Multiple Providers                          ║
║  Providers: Discord | Telegram | Email | Webhook                         ║
║                                                                          ║
║  Triggers:                                                               ║
║  - Trade executed                                                        ║
║  - Drawdown breach                                                       ║
║  - System failure                                                        ║
║                                                                          ║
║  BUGS FIXED:                                                             ║
║  AE-1  Session used before connect() — now auto-initialises on first use ║
║  AE-2  No rate-limit (429) handling — now exponential backoff            ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

logger = logging.getLogger("AlertEngine")


# ═══════════════════════════════════════════════════════════════════════════
# ALERT PROVIDER INTERFACE
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class AlertMessage:
    """Standardized alert message format."""
    title: str
    body: str
    level: str = "info"  # info, warning, critical
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


class AlertProvider(ABC):
    """Abstract base class for alert providers."""
    
    @abstractmethod
    async def send(self, message: AlertMessage) -> bool:
        """Send alert message. Returns True if successful."""
        pass
    
    @abstractmethod
    def is_configured(self) -> bool:
        """Check if provider is properly configured."""
        pass


# ═══════════════════════════════════════════════════════════════════════════
# DISCORD PROVIDER
# ═══════════════════════════════════════════════════════════════════════════

class DiscordProvider(AlertProvider):
    """Discord webhook alert provider."""
    
    def __init__(self, webhook_url: str, session=None):
        self.webhook_url = webhook_url
        self._session = session
        self._colors = {
            "info": 0x00D4FF,
            "warning": 0xFF8C00,
            "critical": 0xFF0000,
            "success": 0x00FF88,
        }
    
    def is_configured(self) -> bool:
        return bool(self.webhook_url)
    
    async def send(self, message: AlertMessage) -> bool:
        if not self.is_configured():
            return False
        
        color = self._colors.get(message.level, 0x808080)
        
        payload = {
            "username": "Algo22 Overwatch",
            "embeds": [
                {
                    "title": message.title,
                    "description": message.body,
                    "color": color,
                    "timestamp": message.timestamp,
                    "fields": [
                        {"name": k, "value": str(v), "inline": True}
                        for k, v in message.metadata.items()
                    ] if message.metadata else []
                }
            ],
        }
        
        try:
            await self._post_with_retry(self.webhook_url, payload)
            return True
        except Exception as e:
            logger.error(f"[Discord] Failed to send: {e}")
            return False
    
    async def _post_with_retry(self, url: str, payload: dict, max_retries: int = 3):
        """Post with exponential backoff."""
        import aiohttp
        
        session = self._session or aiohttp.ClientSession()
        try:
            for attempt in range(max_retries):
                try:
                    async with session.post(url, json=payload, timeout=10) as resp:
                        if resp.status in (200, 204):
                            return
                        if resp.status == 429:
                            wait = 2**attempt
                            logger.warning(f"[Discord] Rate limited. Waiting {wait}s.")
                            await asyncio.sleep(wait)
                            continue
                        logger.error(f"[Discord] HTTP {resp.status}")
                        return
                except Exception as e:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2**attempt)
                    else:
                        raise
        finally:
            if not self._session:
                await session.close()


# ═══════════════════════════════════════════════════════════════════════════
# TELEGRAM PROVIDER
# ═══════════════════════════════════════════════════════════════════════════

class TelegramProvider(AlertProvider):
    """Telegram bot alert provider."""
    
    def __init__(self, bot_token: str, chat_id: str, session=None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._session = session
    
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)
    
    async def send(self, message: AlertMessage) -> bool:
        if not self.is_configured():
            return False
        
        emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨", "success": "✅"}.get(message.level, "📢")
        text = f"<b>{emoji} {message.title}</b>\n\n{message.body}"
        
        if message.metadata:
            text += "\n\n<b>Details:</b>"
            for k, v in message.metadata.items():
                text += f"\n• {k}: {v}"
        
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        
        try:
            await self._post_with_retry(url, payload)
            return True
        except Exception as e:
            logger.error(f"[Telegram] Failed to send: {e}")
            return False
    
    async def _post_with_retry(self, url: str, payload: dict, max_retries: int = 3):
        """Post with exponential backoff."""
        import aiohttp
        
        session = self._session or aiohttp.ClientSession()
        try:
            for attempt in range(max_retries):
                try:
                    async with session.post(url, json=payload, timeout=10) as resp:
                        if resp.status == 200:
                            return
                        if resp.status == 429:
                            wait = 2**attempt
                            logger.warning(f"[Telegram] Rate limited. Waiting {wait}s.")
                            await asyncio.sleep(wait)
                            continue
                        logger.error(f"[Telegram] HTTP {resp.status}")
                        return
                except Exception as e:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2**attempt)
                    else:
                        raise
        finally:
            if not self._session:
                await session.close()


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL PROVIDER
# ═══════════════════════════════════════════════════════════════════════════

class EmailProvider(AlertProvider):
    """Email SMTP alert provider."""
    
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        username: str,
        password: str,
        from_addr: str,
        to_addrs: List[str],
        use_tls: bool = True,
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self.to_addrs = to_addrs
        self.use_tls = use_tls
    
    def is_configured(self) -> bool:
        return all([self.smtp_host, self.username, self.password, self.to_addrs])
    
    async def send(self, message: AlertMessage) -> bool:
        if not self.is_configured():
            return False
        
        try:
            import aiosmtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
        except ImportError:
            logger.warning("[Email] aiosmtplib not installed, using sync fallback")
            return await self._send_sync(message)
        
        try:
            msg = MIMEMultipart()
            msg["From"] = self.from_addr
            msg["To"] = ", ".join(self.to_addrs)
            msg["Subject"] = f"[{message.level.upper()}] {message.title}"
            
            body = f"{message.body}\n\nTimestamp: {message.timestamp}"
            if message.metadata:
                body += "\n\nMetadata:\n"
                for k, v in message.metadata.items():
                    body += f"  {k}: {v}\n"
            
            msg.attach(MIMEText(body, "plain"))
            
            await aiosmtplib.send(
                msg,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.username,
                password=self.password,
                start_tls=self.use_tls,
            )
            
            logger.info(f"[Email] Sent to {self.to_addrs}")
            return True
            
        except Exception as e:
            logger.error(f"[Email] Failed to send: {e}")
            return False
    
    async def _send_sync(self, message: AlertMessage) -> bool:
        """Synchronous fallback using smtplib."""
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        try:
            msg = MIMEMultipart()
            msg["From"] = self.from_addr
            msg["To"] = ", ".join(self.to_addrs)
            msg["Subject"] = f"[{message.level.upper()}] {message.title}"
            
            body = f"{message.body}\n\nTimestamp: {message.timestamp}"
            msg.attach(MIMEText(body, "plain"))
            
            # Run in thread pool to not block
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._send_smtp_sync, msg)
            
            return True
        except Exception as e:
            logger.error(f"[Email] Sync send failed: {e}")
            return False
    
    def _send_smtp_sync(self, msg):
        """Blocking SMTP send."""
        import smtplib
        
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            if self.use_tls:
                server.starttls()
            server.login(self.username, self.password)
            server.send_message(msg)


# ═══════════════════════════════════════════════════════════════════════════
# WEBHOOK PROVIDER
# ═══════════════════════════════════════════════════════════════════════════

class WebhookProvider(AlertProvider):
    """Generic HTTP webhook provider."""
    
    def __init__(self, webhook_url: str, headers: Optional[Dict[str, str]] = None, session=None):
        self.webhook_url = webhook_url
        self.headers = headers or {"Content-Type": "application/json"}
        self._session = session
    
    def is_configured(self) -> bool:
        return bool(self.webhook_url)
    
    async def send(self, message: AlertMessage) -> bool:
        if not self.is_configured():
            return False
        
        payload = {
            "title": message.title,
            "body": message.body,
            "level": message.level,
            "timestamp": message.timestamp,
            "metadata": message.metadata,
        }
        
        try:
            await self._post_with_retry(self.webhook_url, payload)
            return True
        except Exception as e:
            logger.error(f"[Webhook] Failed to send: {e}")
            return False
    
    async def _post_with_retry(self, url: str, payload: dict, max_retries: int = 3):
        """Post with exponential backoff."""
        import aiohttp
        
        session = self._session or aiohttp.ClientSession()
        try:
            for attempt in range(max_retries):
                try:
                    async with session.post(
                        url, json=payload, headers=self.headers, timeout=10
                    ) as resp:
                        if resp.status in (200, 201, 202, 204):
                            return
                        if resp.status == 429:
                            wait = 2**attempt
                            logger.warning(f"[Webhook] Rate limited. Waiting {wait}s.")
                            await asyncio.sleep(wait)
                            continue
                        logger.error(f"[Webhook] HTTP {resp.status}")
                        return
                except Exception as e:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2**attempt)
                    else:
                        raise
        finally:
            if not self._session:
                await session.close()


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ALERT ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class AlertEngine:
    """
    Pluggable alert system supporting multiple providers.
    
    Providers: Discord, Telegram, Email, Webhook
    Triggers: Trade executed, Drawdown breach, System failure
    """

    def __init__(
        self,
        discord_webhook_url: Optional[str] = None,
        telegram_bot_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        # Email config
        smtp_host: Optional[str] = None,
        smtp_port: int = 587,
        smtp_username: Optional[str] = None,
        smtp_password: Optional[str] = None,
        email_from: Optional[str] = None,
        email_to: Optional[List[str]] = None,
        # Webhook config
        webhook_url: Optional[str] = None,
        webhook_headers: Optional[Dict[str, str]] = None,
    ):
        self._session = None
        self._session_lock = asyncio.Lock()
        
        # Initialize providers
        self.providers: List[AlertProvider] = []
        
        # Discord
        if discord_webhook_url:
            self.providers.append(DiscordProvider(discord_webhook_url, self._session))
            logger.info("[AlertEngine] Discord provider configured")
        
        # Telegram
        if telegram_bot_token and telegram_chat_id:
            self.providers.append(TelegramProvider(telegram_bot_token, telegram_chat_id, self._session))
            logger.info("[AlertEngine] Telegram provider configured")
        
        # Email
        if smtp_host and smtp_username and smtp_password and email_to:
            self.providers.append(EmailProvider(
                smtp_host, smtp_port, smtp_username, smtp_password,
                email_from or smtp_username, email_to
            ))
            logger.info(f"[AlertEngine] Email provider configured (to: {email_to})")
        
        # Webhook
        if webhook_url:
            self.providers.append(WebhookProvider(webhook_url, webhook_headers, self._session))
            logger.info("[AlertEngine] Webhook provider configured")
        
        if not self.providers:
            logger.warning("[AlertEngine] No providers configured! Alerts will be logged only.")
        
        logger.info(f"[AlertEngine] Initialized with {len(self.providers)} providers")

    async def _get_session(self):
        """
        FIX AE-1: Lazy session initialisation — creates the session on first
        use rather than requiring explicit connect() calls. Safe under concurrency.
        """
        import aiohttp

        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
        return self._session

    async def connect(self):
        """Explicit warm-up call during server startup (still supported)."""
        await self._get_session()
        logger.info("AlertEngine session ready.")
    # ── Core dispatch method ──────────────────────────────────────────────

    async def _dispatch(self, message: AlertMessage):
        """Send alert to all configured providers."""
        if not self.providers:
            logger.warning(f"[ALERT] {message.title}: {message.body}")
            return
        
        tasks = [provider.send(message) for provider in self.providers if provider.is_configured()]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            success_count = sum(1 for r in results if r is True)
            logger.info(f"[AlertEngine] Alert sent to {success_count}/{len(tasks)} providers")

    # ── Pre-configured alert triggers ──────────────────────────────────────

    async def send_trade_execution(
        self,
        user_id: str,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        pnl: Optional[float] = None,
    ):
        """
        Trigger: Trade executed
        """
        message = AlertMessage(
            title=f"Trade Executed: {symbol}",
            body=f"{side.upper()} {amount} {symbol} @ ${price:,.4f}",
            level="success",
            metadata={
                "user_id": user_id,
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "price": price,
                "pnl": f"${pnl:.2f}" if pnl else "N/A",
            }
        )
        await self._dispatch(message)

    async def send_drawdown_breach(
        self,
        current_drawdown_pct: float,
        max_allowed_pct: float,
        current_equity: float,
        strategy_id: Optional[str] = None,
    ):
        """
        Trigger: Drawdown breach
        """
        message = AlertMessage(
            title="⚠️ DRAWDOWN BREACH ALERT",
            body=(
                f"Current drawdown {current_drawdown_pct:.2f}% exceeds "
                f"maximum allowed {max_allowed_pct:.2f}%"
            ),
            level="critical",
            metadata={
                "current_drawdown_pct": f"{current_drawdown_pct:.2f}%",
                "max_allowed_pct": f"{max_allowed_pct:.2f}%",
                "current_equity": f"${current_equity:,.2f}",
                "strategy_id": strategy_id or "unknown",
            }
        )
        await self._dispatch(message)

    async def send_system_failure(
        self,
        component: str,
        error_message: str,
        severity: str = "critical",
    ):
        """
        Trigger: System failure
        """
        message = AlertMessage(
            title=f"🚨 SYSTEM FAILURE: {component}",
            body=error_message,
            level=severity,
            metadata={
                "component": component,
                "severity": severity,
            }
        )
        await self._dispatch(message)

    async def send_risk_warning(
        self,
        user_id: str,
        symbol: str,
        reason: str,
    ):
        """
        Trigger: Risk warning (general)
        """
        message = AlertMessage(
            title=f"Risk Warning: {user_id} on {symbol}",
            body=reason,
            level="warning",
            metadata={
                "user_id": user_id,
                "symbol": symbol,
            }
        )
        await self._dispatch(message)

    async def send_critical(self, system: str, message: str):
        """Legacy method for backward compatibility."""
        await self.send_system_failure(system, message, "critical")

    async def send_info(self, message: str):
        """Send info alert."""
        alert = AlertMessage(
            title="System Info",
            body=message,
            level="info",
        )
        await self._dispatch(alert)

    # ── Utility methods ────────────────────────────────────────────────────

    def get_provider_status(self) -> Dict[str, bool]:
        """Get status of all providers."""
        return {
            "discord": any(isinstance(p, DiscordProvider) for p in self.providers),
            "telegram": any(isinstance(p, TelegramProvider) for p in self.providers),
            "email": any(isinstance(p, EmailProvider) for p in self.providers),
            "webhook": any(isinstance(p, WebhookProvider) for p in self.providers),
            "total": len(self.providers),
        }
