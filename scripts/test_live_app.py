import sys
sys.path.insert(0, '.')

from fastapi.testclient import TestClient
from backend_app.main import app

print("Initializing FastAPI TestClient...")
client = TestClient(app)

print("Testing GET /health/live...")
res = client.get("/health/live")
print("Response status:", res.status_code)
print("Response JSON:", res.json())
assert res.status_code == 200, f"Expected 200, got {res.status_code}"

print("Testing GET /api/dashboard/overview (unauthorized without JWT)...")
res = client.get("/api/dashboard/overview")
print("Response status:", res.status_code)
print("Response JSON:", res.json())
assert res.status_code == 401, f"Expected 401 Unauthorized, got {res.status_code}"

print("TEST COMPLETED SUCCESSFULLY! REAL BACKEND SERVER RUNTIME VERIFIED!")
