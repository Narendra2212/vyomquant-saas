import os
import sys

# Ensure backend_app is importable
sys.path.insert(0, os.getcwd())

from fastapi.testclient import TestClient
from backend_app.main import app

def test_fastapi_endpoints():
    client = TestClient(app)
    
    # 1. Test /health (ALB)
    res = client.get("/health")
    print(f"GET /health -> {res.status_code} {res.json().get('status')}")
    assert res.status_code == 200
    
    # 2. Test /health/live
    res = client.get("/health/live")
    print(f"GET /health/live -> {res.status_code} {res.json()}")
    assert res.status_code == 200
    
    # 3. Test /health/ready
    res = client.get("/health/ready")
    print(f"GET /health/ready -> {res.status_code} {res.json()}")
    assert res.status_code == 200

    # 4. Test /api/health
    res = client.get("/api/health")
    print(f"GET /api/health -> {res.status_code} {res.json().get('status')}")
    assert res.status_code in (200, 503)

    # 5. Test /api/health/live
    res = client.get("/api/health/live")
    print(f"GET /api/health/live -> {res.status_code} {res.json()}")
    assert res.status_code == 200

    # 6. Test /api/health/ready
    res = client.get("/api/health/ready")
    print(f"GET /api/health/ready -> {res.status_code} {res.json()}")
    assert res.status_code == 200

    # 7. Test /api/health/redis
    res = client.get("/api/health/redis")
    print(f"GET /api/health/redis -> {res.status_code} {res.json().get('status')}")
    assert res.status_code == 200

    # 8. Test /api/billing/plans (public)
    res = client.get("/api/billing/plans")
    print(f"GET /api/billing/plans -> {res.status_code}, plans count: {len(res.json().get('plans', []))}")
    assert res.status_code == 200

    # 9. Test /api/test-basic
    res = client.get("/api/test-basic")
    print(f"GET /api/test-basic -> {res.status_code} {res.json()}")
    assert res.status_code == 200

    print("\nALL FASTAPI INTEGRATION ENDPOINTS VERIFIED SUCCESSFULLY!")

if __name__ == '__main__':
    test_fastapi_endpoints()
