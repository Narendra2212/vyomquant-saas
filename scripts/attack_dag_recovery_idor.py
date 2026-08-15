import os
import sys
import time
import jwt

sys.path.insert(0, '.')
os.environ["ENV"] = "testing"
os.environ["DEV_MODE"] = "false"

print("=" * 80)
print("ADVERSARIAL ATTACK: DAG TASK RECOVERY & TENANT ISOLATION EXPLOIT")
print("=" * 80)

from fastapi.testclient import TestClient
from backend_app.main import app
from backend_app.core.config import settings

client = TestClient(app, raise_server_exceptions=False)
secret = settings.SUPABASE_JWT_SECRET

# 1. Attacker Tenant Token (Regular User - Role: "user", is_admin: False)
attacker_payload = {
    "sub": "11111111-1111-1111-1111-111111111111",
    "iss": "algo22-test",
    "aud": "authenticated",
    "role": "authenticated",
    "app_metadata": {"role": "user"},
    "user_metadata": {"is_admin": False},
    "exp": int(time.time()) + 3600
}
token_attacker = jwt.encode(attacker_payload, secret, algorithm="HS256")
headers_attacker = {"Authorization": f"Bearer {token_attacker}"}

# 2. Admin User Token (Admin Role: "admin")
admin_payload = {
    "sub": "00000000-0000-0000-0000-000000000001",
    "iss": "algo22-test",
    "aud": "authenticated",
    "role": "authenticated",
    "app_metadata": {"role": "admin"},
    "user_metadata": {"is_admin": True},
    "exp": int(time.time()) + 3600
}
token_admin = jwt.encode(admin_payload, secret, algorithm="HS256")
headers_admin = {"Authorization": f"Bearer {token_admin}"}

print("\n[ATTACK 1]: Regular tenant attempting to read global dead-letter queue across all users...")
resp_dead_letter_attacker = client.get("/api/dag/tasks/recovery/dead-letter", headers=headers_attacker)
print(f"  Attacker GET /api/dag/tasks/recovery/dead-letter status: {resp_dead_letter_attacker.status_code}")
assert resp_dead_letter_attacker.status_code == 403, f"Expected 403 Forbidden for tenant user, got {resp_dead_letter_attacker.status_code}"
print("  --> Blocked (403 Forbidden): PASS")

print("\n[ATTACK 2]: Regular tenant attempting to trigger global task recovery...")
resp_trigger_attacker = client.post("/api/dag/tasks/recovery/trigger", headers=headers_attacker)
print(f"  Attacker POST /api/dag/tasks/recovery/trigger status: {resp_trigger_attacker.status_code}")
assert resp_trigger_attacker.status_code == 403, f"Expected 403 Forbidden for tenant user, got {resp_trigger_attacker.status_code}"
print("  --> Blocked (403 Forbidden): PASS")

print("\n[ATTACK 3]: Regular tenant attempting to read global recovery statistics...")
resp_stats_attacker = client.get("/api/dag/tasks/recovery/stats", headers=headers_attacker)
print(f"  Attacker GET /api/dag/tasks/recovery/stats status: {resp_stats_attacker.status_code}")
assert resp_stats_attacker.status_code == 403, f"Expected 403 Forbidden for tenant user, got {resp_stats_attacker.status_code}"
print("  --> Blocked (403 Forbidden): PASS")

print("\n[ATTACK 4]: Admin user accessing recovery endpoints...")
resp_dead_letter_admin = client.get("/api/dag/tasks/recovery/dead-letter", headers=headers_admin)
print(f"  Admin GET /api/dag/tasks/recovery/dead-letter status: {resp_dead_letter_admin.status_code}")
assert resp_dead_letter_admin.status_code == 200, f"Expected 200 OK for admin, got {resp_dead_letter_admin.status_code}"
print("  --> Authorized (200 OK): PASS")
print("  --> BUG-DAG-01 (DAG Task Queue Recovery Admin Gating): RUNTIME_PROVEN PASS")

print("\n" + "=" * 80)
print("ALL DAG RECOVERY AND TENANT ISOLATION EXPLOITS PREVENTED AND RUNTIME PROVEN!")
print("=" * 80)
