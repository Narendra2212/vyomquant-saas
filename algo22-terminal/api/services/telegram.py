import logging

logger = logging.getLogger(__name__)


async def send_telegram_alert(chat_id: str, message: str):
    """
    PLACEHOLDER: Telegram Bot Integration.
    TODO: Implement aiogram or httpx call to Telegram Bot API here.
    """
    # Example future implementation:
    # bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    # url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    # payload = {"chat_id": chat_id, "text": message}
    # async with httpx.AsyncClient() as client:
    #     await client.post(url, json=payload)

    logger.info(f"[TELEGRAM MOCK] Sending to {chat_id}: {message}")
    return True
