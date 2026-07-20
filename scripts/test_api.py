import os
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from main import app

load_dotenv()
client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    print("\n✅ Health check passed! The engine is online.")


def test_auth_flow():
    # SECURELY loading from .env! No hardcoded strings.
    real_email = os.getenv("TEST_USER_EMAIL")
    real_password = os.getenv("TEST_USER_PASSWORD")

    if not real_email or not real_password:
        assert False, "❌ Missing credentials in .env file!"

    login_response = client.post(
        "/api/auth/signin", json={"email": real_email, "password": real_password}
    )
    assert login_response.status_code == 200, f"Signin failed: {login_response.text}"

    data = login_response.json()
    assert "access_token" in data
    print(f"\n🔐 Vault unlocked! VIP Token acquired: {data['access_token'][:20]}...")
