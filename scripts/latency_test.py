import httpx
import os
import time
from statistics import mean, stdev
from dotenv import load_dotenv

load_dotenv()
EMAIL = os.getenv("TEST_USER_EMAIL")
PASSWORD = os.getenv("TEST_USER_PASSWORD")
URL = "http://127.0.0.1:8062/api/orders/execute"


def run_audit():
    with httpx.Client(timeout=10.0) as client:
        # 1. Warm-up: Sign in (we don't measure this)
        print("🔐 Authenticating...")
        login = client.post(
            "http://127.0.0.1:8062/api/auth/signin",
            json={"email": EMAIL, "password": PASSWORD},
        )
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Payload for a tiny trade
        payload = {
            "symbol": "BTC/USDT",
            "amount": 1,
            "side": "buy",
            "order_type": "market",
        }

        latencies = []
        print("⏱️ Auditing 20 execution cycles...\n")

        for i in range(20):
            start = time.perf_counter()
            resp = client.post(URL, json=payload, headers=headers)
            end = time.perf_counter()

            # Record time in milliseconds
            milli = (end - start) * 1000

            if resp.status_code in [200, 201]:
                latencies.append(milli)
                print(f"Cycle {i+1:02}: 🟢 {milli:.2f}ms")
            else:
                print(f"Cycle {i+1:02}: 🔴 FAILED ({resp.status_code})")

        if latencies:
            print("\n📈 AERORA DYNAMICS LATENCY REPORT:")
            print(f"   Fastest: {min(latencies):.2f}ms")
            print(f"   Slowest: {max(latencies):.2f}ms")
            print(f"   Average: {mean(latencies):.2f}ms")
            if len(latencies) > 1:
                print(f"   Jitter (StdDev): {stdev(latencies):.2f}ms")
        else:
            print(
                "\n❌ Audit Failed: No successful trades recorded. Check balance or server logs."
            )


if __name__ == "__main__":
    run_audit()
