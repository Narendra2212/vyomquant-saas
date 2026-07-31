import os
import logging
import httpx

logger = logging.getLogger(__name__)


async def send_telegram_alert(chat_id: str, message: str) -> bool:
    """
    Sends notification via Telegram Bot API if TELEGRAM_BOT_TOKEN is present,
    otherwise logs the notification payload safely.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if bot_token and chat_id:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message}
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"Failed to dispatch Telegram message to {chat_id}: {e}")
            return False

    logger.info(f"[TELEGRAM LOG] Sending to {chat_id}: {message}")
    return True

