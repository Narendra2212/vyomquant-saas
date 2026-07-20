import asyncio
import httpx
import os
import time
import random
from dotenv import load_dotenv

load_dotenv()
EMAIL = os.getenv("TEST_USER_EMAIL")
PASSWORD = os.getenv("TEST_USER_PASSWORD")
TARGET_URL = "http://127.0.0.1:8062/api/orders/execute"


async def fire_order(client, order_id, headers):
    # Add 0-100ms jitter to simulate real-world network variance
    await asyncio.sleep(random.uniform(0, 0.1))

    payload = {
        "symbol": "BTC/USDT",
        "amount": 1000.0,  # $1,000 per trade
        "side": "buy",
        "order_type": "market",
    }

    start = time.perf_counter()
    try:
        resp = await client.post(
            TARGET_URL, json=payload, headers=headers, timeout=10.0
        )
        end = time.perf_counter()
        return order_id, resp.status_code, resp.text, (end - start) * 1000
    except Exception as e:
        return order_id, "CRASH", str(e), 0


async def main():
    async with httpx.AsyncClient() as client:
        print("🔐 1. Authenticating...")
        login = await client.post(
            "http://127.0.0.1:8062/api/auth/signin",
            json={"email": EMAIL, "password": PASSWORD},
        )
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        print("🔥 Launching 10 concurrent orders...")
        tasks = [fire_order(client, i, headers) for i in range(10)]
        results = await asyncio.gather(*tasks)

        print("\n📊 STRESS TEST RESULTS:")
        print("-" * 50)
        success = 0
        blocked = 0
        crashed = 0

        for oid, code, text, lat in sorted(results):
            status = (
                "🟢 SUCCESS"
                if code == 200
                else "🔴 BLOCKED" if code == 400 else "💥 CRASHED"
            )
            if code == 200:
                success += 1
            elif code == 400:
                blocked += 1
            else:
                crashed += 1
            print(f"Order {oid:02} | {status} ({code}) | {lat:7.2f}ms | {text[:50]}...")

        print("-" * 50)
        print(f"✅ Total: {success} Wins | {blocked} Denied | {crashed} Crashes")


if __name__ == "__main__":
    asyncio.run(main())
