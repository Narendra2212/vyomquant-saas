import requests
import json
import sys

BASE_URL = "https://d7d88qs4jmch.cloudfront.net"

endpoints = [
    "/api/dashboard?equity_days=30",
    "/api/dashboard/overview",
    "/api/signal-trace/signals?limit=50&offset=0",
    "/api/strategies",
    "/api/risk/settings",
    "/api/exchanges",
    "/api/billing/currency",
    "/api/billing/plans",
    "/api/billing/entitlements",
    "/api/notifications?limit=100"
]

print("=" * 80)
print("SECURITY HARD GATE: UNAUTHENTICATED ACCESS VERIFICATION")
print(f"Target: {BASE_URL}")
print("=" * 80)

all_passed = True
results = {}

for path in endpoints:
    url = f"{BASE_URL}{path}"
    try:
        # Request WITHOUT Authorization header
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 SecurityGate/1.0"}, timeout=10, allow_redirects=False)
        status = resp.status_code
        passed = (status == 401)
        
        # Verify no data leakage
        body_text = resp.text
        has_leakage = False
        if passed and ("user_id" in body_text or "equity" in body_text or "balance" in body_text):
            has_leakage = True
            passed = False
            
        results[path] = {
            "status": status,
            "passed": passed,
            "has_leakage": has_leakage,
            "body_snippet": body_text[:100]
        }
        
        print(f"[{'PASS' if passed else 'FAIL'}] GET {path:<50} -> {status} (Expected: 401, No Leakage: {not has_leakage})")
        if not passed:
            all_passed = False
            print(f"   --> Body: {body_text[:200]}")
            
    except Exception as e:
        print(f"[FAIL] GET {path:<50} -> Exception: {e}")
        all_passed = False

print("=" * 80)
print(f"SECURITY HARD GATE RESULT: {'ALL PASS (100% 401 Unauthenticated)' if all_passed else 'FAIL'}")
print("=" * 80)

with open("reports/security_battery.json", "w") as f:
    json.dump(results, f, indent=2)

sys.exit(0 if all_passed else 1)
