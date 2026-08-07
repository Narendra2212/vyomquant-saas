"""
Quick diagnostic test to confirm the missing 'iss' claim hypothesis.
"""
import os
import time
import jwt

# Set environment like conftest.py does
os.environ["ENV"] = "testing"
os.environ["JWT_SECRET"] = "dev-secret-change-in-production"
os.environ["SUPABASE_JWT_SECRET"] = "dev-secret-change-in-production"

# Current token construction (broken)
def create_broken_token(data: dict) -> str:
    payload = data.copy()
    payload.setdefault("aud", "authenticated")
    payload.setdefault("exp", int(time.time()) + 3600)
    secret = os.environ.get("SUPABASE_JWT_SECRET") or os.environ.get("JWT_SECRET") or "dev-secret-change-in-production"
    return jwt.encode(payload, secret, algorithm="HS256")

# Fixed token construction (with iss claim)
def create_fixed_token(data: dict) -> str:
    payload = data.copy()
    payload.setdefault("aud", "authenticated")
    payload.setdefault("exp", int(time.time()) + 3600)
    payload.setdefault("iss", "algo22-test")  # MISSING CLAIM - THIS IS THE FIX
    secret = os.environ.get("SUPABASE_JWT_SECRET") or os.environ.get("JWT_SECRET") or "dev-secret-change-in-production"
    return jwt.encode(payload, secret, algorithm="HS256")

# Test data
user_data = {
    "sub": "test-user-id-12345",
    "email": "testuser@algo22.io",
    "tenant_id": "tenant-12345",
    "role": "authenticated",
    "app_metadata": {"role": "authenticated", "tenant_id": "tenant-12345"}
}

# Create both tokens
broken_token = create_broken_token(user_data)
fixed_token = create_fixed_token(user_data)

print("=== BROKEN TOKEN ANALYSIS ===")
print(f"Token: {broken_token[:50]}...")
broken_payload = jwt.decode(broken_token, options={"verify_signature": False})
print(f"Payload: {broken_payload}")
print(f"Has 'iss' claim: {'iss' in broken_payload}")
print(f"iss value: {broken_payload.get('iss', 'MISSING')}")

print("\n=== FIXED TOKEN ANALYSIS ===")
print(f"Token: {fixed_token[:50]}...")
fixed_payload = jwt.decode(fixed_token, options={"verify_signature": False})
print(f"Payload: {fixed_payload}")
print(f"Has 'iss' claim: {'iss' in fixed_payload}")
print(f"iss value: {fixed_payload.get('iss', 'MISSING')}")

print("\n=== AUTH MIDDLEWARE CHECK ===")
# Simulate the auth_middleware check
def check_token_fallback(token: str):
    try:
        unverified_payload = jwt.decode(token, options={"verify_signature": False})
        if unverified_payload.get("iss") == "algo22-test":
            return "ACCEPTED - will use HS256 fallback"
        else:
            return f"REJECTED - iss is '{unverified_payload.get('iss', 'MISSING')}', expected 'algo22-test'"
    except Exception as e:
        return f"ERROR: {e}"

print(f"Broken token result: {check_token_fallback(broken_token)}")
print(f"Fixed token result: {check_token_fallback(fixed_token)}")

print("\n=== DIAGNOSIS ===")
print("ROOT CAUSE: test_end_to_end_api_suite.py's create_access_token() is missing the required 'iss': 'algo22-test' claim")
print("FIX: Add payload.setdefault('iss', 'algo22-test') to the create_access_token function")
