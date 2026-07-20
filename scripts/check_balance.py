import httpx
import os
from dotenv import load_dotenv

load_dotenv()
EMAIL = os.getenv("TEST_USER_EMAIL")
PASSWORD = os.getenv("TEST_USER_PASSWORD")
BASE_URL = "http://127.0.0.1:8062/api"

# Adding a longer timeout (20s) because we've been hitting the DB hard
with httpx.Client(timeout=20.0) as client:
    try:
        print("🔐 Signing in...")
        login = client.post(
            f"{BASE_URL}/auth/signin", json={"email": EMAIL, "password": PASSWORD}
        )
        login.raise_for_status()

        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        print("💰 Fetching balance...")
        resp = client.get(f"{BASE_URL}/user/profile", headers=headers)
        resp.raise_for_status()

        data = resp.json()
        balance = data.get("balance", 0.0)
        print("\n✅ SUCCESS!")
        print(f"📊 User: {EMAIL}")
        print(f"💰 Current Wallet Balance: ${float(balance):,.2f}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        if "resp" in locals():
            print(f"📦 Server said: {resp.text}")
