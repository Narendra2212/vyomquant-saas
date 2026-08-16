import urllib.request
import urllib.error
import json
import ssl
import sys

BASE_URL = "https://d7d88qs4jmch.cloudfront.net"

ctx = ssl.create_default_context()

def check_url(path, expected_status=200):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            data = resp.read().decode('utf-8', errors='replace')
            status = resp.status
            return status, data, None
    except urllib.error.HTTPError as e:
        data = e.read().decode('utf-8', errors='replace')
        return e.code, data, None
    except Exception as e:
        return None, None, str(e)

print("=" * 60)
print("LIVE PRODUCTION VERIFICATION BATTERY")
print(f"Target: {BASE_URL}")
print("=" * 60)

tests = [
    ("Live HTML Root", "/", 200),
    ("Health Live", "/health/live", 200),
    ("Health Ready", "/health/ready", 200),
    ("Billing Entitlements (Auth Check)", "/api/billing/entitlements", 401),
    ("Support Tickets (Auth Check)", "/api/support/tickets", 401),
    ("Strategies List (Auth Check)", "/api/strategies", 401),
    ("Portfolio Summary (Auth Check)", "/api/portfolio/summary", 401),
    ("Risk Settings (Auth Check)", "/api/risk/settings", 401),
    ("Exchange List (Auth Check)", "/api/exchanges", 401),
    ("Market Symbols", "/api/market/symbols", [200, 401]),
]

all_ok = True
for name, path, expected in tests:
    code, data, err = check_url(path)
    if err:
        print(f"[FAIL] {name:<35} -> Error: {err}")
        all_ok = False
        continue
    
    exp_list = expected if isinstance(expected, list) else [expected]
    status_pass = code in exp_list
    print(f"[{'PASS' if status_pass else 'FAIL'}] {name:<35} -> Status: {code} (Expected: {expected})")
    if not status_pass:
        all_ok = False
        print(f"       Response snippet: {data[:200]}")

print("\n" + "=" * 60)
print("SCANNING LIVE ASSETS FOR BAD DOMAINS / DEAD ORIGINS")
print("=" * 60)

asset_paths = [
    "/assets/index-DSSwnYtf.js",
    "/assets/Strategies-CbCrHEYy.js",
    "/assets/StrategyDetail-iAGAoQsu.js",
    "/assets/Billing-BVNpY1F4.js"
]

bad_patterns = ["api.algo22.io", "vyomquant-alb"]
for a_path in asset_paths:
    code, data, err = check_url(a_path)
    if code != 200:
        print(f"[FAIL] Could not fetch live asset: {a_path} (Status: {code})")
        all_ok = False
        continue
    
    found_bads = [p for p in bad_patterns if p in data]
    if found_bads:
        print(f"[FAIL] Asset {a_path} contains BAD PATTERNS: {found_bads}")
        all_ok = False
    else:
        print(f"[PASS] Asset {a_path} -> 200 OK | Clean of all bad patterns")

print("=" * 60)
print(f"LIVE VERIFICATION RESULT: {'ALL PASS' if all_ok else 'FAILURES DETECTED'}")
print("=" * 60)
sys.exit(0 if all_ok else 1)
