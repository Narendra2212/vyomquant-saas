import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()
EMAIL = os.getenv("TEST_USER_EMAIL")
PASSWORD = os.getenv("TEST_USER_PASSWORD")
URL_BASE = "http://127.0.0.1:8019/api"


async def main():
    async with httpx.AsyncClient() as client:
        print("🔐 Authenticating...")
        login = await client.post(
            f"{URL_BASE}/auth/signin", json={"email": EMAIL, "password": PASSWORD}
        )
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        payload = {
            "strategy_name": "AI_Oracle_v1",
            "symbol": "BTC/USDT",
            "timeframe": "15m",
            "exchange_id": "binance",
            "indicators": ["Close", "RSI", "MACD"],
        }

        print("🧠 Triggering ML Training Pipeline...")
        resp = await client.post(
            f"{URL_BASE}/strategies/train-ml", json=payload, headers=headers
        )

        if resp.status_code == 200:
            print("✅ Training Task Queued Successfully!")
            print(f"Server Response: {resp.json()}")
        else:
            print(f"❌ Failed: {resp.text}")


if __name__ == "__main__":
    asyncio.run(main())
