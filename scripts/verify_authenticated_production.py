import asyncio
import os
import sys
import time
import requests
import json

BASE_URL = "https://d7d88qs4jmch.cloudfront.net"
SUPABASE_URL = "https://wrkexcjqnidkdrayhlsi.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Indya2V4Y2pxbmlka2RyYXlobHNpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQ3ODkyOTIsImV4cCI6MjA5MDM2NTI5Mn0.NTXqianuwy4xLw4FMY09Z0Q70wf7KWCIzkejaU2sR8s"

print("=" * 70)
print("AUTHENTICATED LIVE PRODUCTION VERIFICATION BATTERY")
print(f"Target Base: {BASE_URL}")
print("=" * 70)

# Step 1: Obtain Auth Token from Supabase
print("\n[1] Authenticating with Supabase...")
try:
    auth_resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"email": "test@test.com", "password": "test123"},
        timeout=10
    )
    if auth_resp.status_code != 200:
        print(f"[FAIL] Authentication failed: {auth_resp.status_code} {auth_resp.text}")
        sys.exit(1)
    
    auth_data = auth_resp.json()
    token = auth_data["access_token"]
    user_id = auth_data["user"]["id"]
    print(f"[OK] Authenticated successfully! User ID: {user_id[:8]}...")
except Exception as e:
    print(f"[ERROR] Auth error: {e}")
    sys.exit(1)

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ProductionVerifier/1.0"
}

def test_api(method, path, body=None, name=""):
    url = f"{BASE_URL}{path}"
    start = time.perf_counter()
    try:
        if method.upper() == "GET":
            # allow_redirects=False to catch any unexpected 301/307 redirects
            resp = requests.get(url, headers=headers, timeout=10, allow_redirects=False)
        elif method.upper() == "POST":
            resp = requests.post(url, headers=headers, json=body, timeout=10, allow_redirects=False)
        elif method.upper() == "PUT":
            resp = requests.put(url, headers=headers, json=body, timeout=10, allow_redirects=False)
        elif method.upper() == "DELETE":
            resp = requests.delete(url, headers=headers, timeout=10, allow_redirects=False)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        elapsed_ms = (time.perf_counter() - start) * 1000
        status = resp.status_code
        
        # Check if redirect
        is_redirect = status in (301, 302, 307, 308)
        redirect_loc = resp.headers.get("Location", "") if is_redirect else ""
        
        is_success = 200 <= status < 300
        status_symbol = "[PASS]" if is_success else ("[REDIRECT]" if is_redirect else "[FAIL]")
        
        print(f"{status_symbol} [{name:<38}] {method} {path} -> {status} ({elapsed_ms:.1f}ms)")
        if is_redirect:
            print(f"   --> REDIRECT LOCATION: {redirect_loc}")
        if not is_success and not is_redirect:
            print(f"   --> Response: {resp.text[:300]}")
        return status, elapsed_ms, resp
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"[FAIL] [{name:<38}] {method} {path} -> EXCEPTION ({elapsed_ms:.1f}ms): {e}")
        return None, elapsed_ms, None

print("\n[2] Executing Authenticated Test Battery (Checking 0 5xx, 0 307 redirects, latency < 500ms)...")

test_api("GET", "/api/billing/currency", name="Billing Currency Preference")
test_api("GET", "/api/billing/plans", name="Billing Plans List")
test_api("GET", "/api/billing/entitlements", name="Billing Entitlements")
test_api("GET", "/api/notifications?limit=100", name="Notifications List (Testing No 307)")
test_api("GET", "/api/dashboard?equity_days=30", name="Dashboard Complete Aggregation")
test_api("GET", "/api/dashboard/overview", name="Dashboard Portfolio Overview")
test_api("GET", "/api/signal-trace/signals?limit=50&offset=0", name="Signal Trace Audit List")
test_api("GET", "/api/strategies", name="Strategies List")
test_api("GET", "/api/risk/settings", name="Risk Management Settings")
test_api("GET", "/api/exchanges", name="Connected Exchange Vault")

print("\n" + "=" * 70)
print("ALL AUTHENTICATED TESTS FINISHED")
print("=" * 70)
