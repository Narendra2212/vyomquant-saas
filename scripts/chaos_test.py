import asyncio
import httpx
import os
import uuid
from dotenv import load_dotenv

load_dotenv()
EMAIL = os.getenv("TEST_USER_EMAIL")
PASSWORD = os.getenv("TEST_USER_PASSWORD")
URL = "http://127.0.0.1:8019/api/orders/execute"


async def fire(client, headers, order_id):
    payload = {
        "symbol": "BTC/USDT",
        "amount": 1000.0,
        "side": "buy",
        "order_type": "market",
        "order_id": order_id,
    }
    try:
        # THE FIX: Tell the client to wait up to 65 seconds
        resp = await client.post(URL, json=payload, headers=headers, timeout=65.0)
        return resp.status_code
    except Exception as e:
        # If it fails now, it will tell us exactly why
        print(f"Client Timeout/Error: {e}")
        return 500


async def main():
    async with httpx.AsyncClient() as client:
        login = await client.post(
            "http://127.0.0.1:8019/api/auth/signin",
            json={"email": EMAIL, "password": PASSWORD},
        )
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        unique_ids = [str(uuid.uuid4()) for _ in range(5)]

        print("🧨 Firing 50-thread chaos burst (10 retries per order)...")
        tasks = []
        for uid in unique_ids:
            for _ in range(10):
                tasks.append(fire(client, headers, uid))

        results = await asyncio.gather(*tasks)

        print("\n📊 CHAOS REPORT:")
        print(f"   Total Requests: {len(results)}")
        print(f"   Success (200): {results.count(200)}")
        print(f"   Client Errors (4xx): {sum(1 for r in results if 400 <= r < 500)}")
        print(f"   Server Errors (500): {results.count(500)}")


if __name__ == "__main__":
    asyncio.run(main())
