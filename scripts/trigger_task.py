import httpx
import os
from dotenv import load_dotenv

load_dotenv()

EMAIL = os.getenv("TEST_USER_EMAIL")
PASSWORD = os.getenv("TEST_USER_PASSWORD")
BASE_URL = "http://127.0.0.1:8062/api"

with httpx.Client() as client:
    print("\n🔐 1. Knocking on the Security Vault...")
    login = client.post(
        f"{BASE_URL}/auth/signin", json={"email": EMAIL, "password": PASSWORD}
    )

    if login.status_code != 200:
        print(f"❌ Auth Failed: {login.text}")
        exit()

    token = login.json()["access_token"]
    print("✅ Token acquired! Firing authenticated payload at Standard User Engine...")

    # 2. Fire at the standard /user/profile route instead of Admin!
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get(f"{BASE_URL}/user/profile", headers=headers)

    print(f"\n📡 Response Status: {response.status_code}")
    try:
        print(f"📦 Response Data: {response.json()}")
    except:
        print(f"📦 Response Text: {response.text}")
