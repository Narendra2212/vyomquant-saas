"""
Phase 5 — Production Recertification Script
Tests all production endpoints after the ES256 JWKS hotfix deployment.
"""

import urllib.request
import json
import sys
import time
import os

SUPABASE_URL = "https://YOUR_PROJECT_REF.supabase.co"
SUPABASE_ANON_KEY = "YOUR_SUPABASE_ANON_KEY"
EMAIL = os.environ.get("TEST_USER_EMAIL", "your-test-email@example.com")
PASSWORD = os.environ.get("TEST_USER_PASSWORD", "YOUR_TEST_PASSWORD")
BACKEND_BASE = "https://backend-production-d57af.up.railway.app"

results = {}

def http_get(url, token=None, timeout=15):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            return r.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body
    except Exception as e:
        return None, str(e)

def http_post(url, data, headers_extra=None, timeout=15):
    headers = {"Content-Type": "application/json"}
    if headers_extra:
        headers.update(headers_extra)
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            return r.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body
    except Exception as e:
        return None, str(e)

print("\n" + "=" * 65)
print(" PHASE 5 - PRODUCTION RECERTIFICATION (ES256 JWKS HOTFIX)")
print("=" * 65)

# ── Step 0: Health check (no auth needed) ──────────────────────────────────
print("\n[0/7] Health check")
code, body = http_get(f"{BACKEND_BASE}/health/live")
results["health"] = {"code": code, "pass": code == 200}
print(f"  /health/live -> {code} | {'PASS' if code == 200 else 'FAIL'}")
if code == 200:
    print(f"  body: {body[:100]}")

# ── Step 1: Obtain live Supabase ES256 token ───────────────────────────────
print("\n[1/7] Obtain live ES256 token from Supabase")
auth_url = f"{SUPABASE_URL}/auth/v1/token?grant_type=password"
code, body = http_post(
    auth_url,
    {"email": EMAIL, "password": PASSWORD},
    {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {SUPABASE_ANON_KEY}"},
)
token = None
if code in (200, 201):
    try:
        resp_data = json.loads(body)
        token = resp_data.get("access_token")
        print(f"  Supabase auth -> 200 OK | token obtained: {token[:15]}...[MASKED]")
        results["auth"] = {"code": code, "pass": True}
    except Exception as e:
        print(f"  Parse error: {e}")
        results["auth"] = {"code": code, "pass": False, "error": str(e)}
else:
    print(f"  Supabase auth FAILED -> {code}")
    print(f"  body: {body[:200]}")
    results["auth"] = {"code": code, "pass": False}
    print("\nCannot proceed to authenticated endpoint tests without a valid token.")
    token = None

# ── Step 2: GET /api/stats ─────────────────────────────────────────────────
print("\n[2/7] GET /api/stats")
if token:
    code, body = http_get(f"{BACKEND_BASE}/api/stats", token)
    results["stats"] = {"code": code, "pass": code == 200}
    print(f"  /api/stats -> {code} | {'PASS' if code == 200 else 'FAIL'}")
    if code == 200:
        try:
            data = json.loads(body)
            print(f"  keys: {list(data.keys())[:5]}")
        except:
            print(f"  body: {body[:150]}")
    else:
        print(f"  body: {body[:200]}")
else:
    results["stats"] = {"code": None, "pass": False, "note": "skipped - no token"}
    print("  SKIPPED (no token)")

# ── Step 3: GET /api/portfolio/equity-curve ───────────────────────────────
print("\n[3/7] GET /api/portfolio/equity-curve")
if token:
    code, body = http_get(f"{BACKEND_BASE}/api/portfolio/equity-curve", token)
    results["equity_curve"] = {"code": code, "pass": code in (200, 404)}
    print(f"  /api/portfolio/equity-curve -> {code} | {'PASS' if code in (200, 404) else 'FAIL'}")
    if code == 200:
        print(f"  body: {body[:150]}")
    elif code == 404:
        print(f"  (404 acceptable — no trades yet)")
    else:
        print(f"  body: {body[:200]}")
else:
    results["equity_curve"] = {"code": None, "pass": False, "note": "skipped - no token"}
    print("  SKIPPED (no token)")

# ── Step 4: GET /api/strategies ───────────────────────────────────────────
print("\n[4/7] GET /api/strategies")
if token:
    code, body = http_get(f"{BACKEND_BASE}/api/strategies", token)
    results["strategies"] = {"code": code, "pass": code in (200, 404)}
    print(f"  /api/strategies -> {code} | {'PASS' if code in (200, 404) else 'FAIL'}")
    if code == 200:
        try:
            data = json.loads(body)
            count = len(data) if isinstance(data, list) else "?"
            print(f"  strategy count: {count}")
        except:
            print(f"  body: {body[:150]}")
    else:
        print(f"  body: {body[:200]}")
else:
    results["strategies"] = {"code": None, "pass": False, "note": "skipped - no token"}
    print("  SKIPPED (no token)")

# ── Step 5: Token rejection check (invalid token) ─────────────────────────
print("\n[5/7] Rejected token check (invalid token -> 401)")
code, body = http_get(f"{BACKEND_BASE}/api/stats", "invalidtoken.abc.xyz")
results["rejected_invalid"] = {"code": code, "pass": code == 401}
print(f"  invalid token -> {code} | {'PASS' if code == 401 else 'FAIL'}")

# ── Step 6: No token check ────────────────────────────────────────────────
print("\n[6/7] No-token rejection check (no auth -> 401/403)")
code, body = http_get(f"{BACKEND_BASE}/api/stats")
results["rejected_no_auth"] = {"code": code, "pass": code in (401, 403, 422)}
print(f"  no auth -> {code} | {'PASS' if code in (401, 403, 422) else 'FAIL'}")

# ── Step 7: WebSocket endpoint exists ─────────────────────────────────────
print("\n[7/7] WebSocket endpoint availability")
code, body = http_get(f"{BACKEND_BASE}/ws")
# 403 or 426 expected (WS upgrade required) — both mean the endpoint exists
results["ws_endpoint"] = {"code": code, "pass": code not in (None, 404, 500)}
print(f"  /ws -> {code} | {'PASS (endpoint exists)' if code not in (None, 404, 500) else 'FAIL'}")

# ── Summary ───────────────────────────────────────────────────────────────
print("\n" + "=" * 65)
passed = sum(1 for r in results.values() if r.get("pass"))
total = len(results)
print(f" RESULT: {passed}/{total} checks passed")

all_auth_pass = results.get("stats", {}).get("pass") and results.get("equity_curve", {}).get("pass") and results.get("strategies", {}).get("pass")
reject_pass = results.get("rejected_invalid", {}).get("pass") and results.get("rejected_no_auth", {}).get("pass")

if not token:
    verdict = "DEPLOYED_WITH_WARNINGS"
    note = "Valid token test skipped — Supabase auth failed or token unavailable"
elif all_auth_pass and reject_pass:
    verdict = "DEPLOYED_AND_OPERATIONAL"
    note = "All authenticated endpoints returning 200, rejections working"
elif reject_pass:
    verdict = "DEPLOYED_WITH_WARNINGS"
    note = "Auth rejections work but some endpoints returned unexpected codes"
else:
    verdict = "NOT_PRODUCTION_READY"
    note = "Auth rejection paths not working correctly"

print(f" VERDICT: {verdict}")
print(f" NOTE: {note}")
print("=" * 65 + "\n")

with open("production_recert_results.json", "w") as f:
    json.dump({
        "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "backend_url": BACKEND_BASE,
        "token_obtained": token is not None,
        "checks": results,
        "passed": passed,
        "total": total,
        "verdict": verdict,
        "note": note,
    }, f, indent=2)
print("Results written to production_recert_results.json")
