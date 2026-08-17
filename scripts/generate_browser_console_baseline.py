import asyncio
import json
import os
import sys
import time
from typing import Dict, List, Any
import requests
from playwright.async_api import async_playwright

BASE_URL = "https://d7d88qs4jmch.cloudfront.net"
SUPABASE_URL = "https://wrkexcjqnidkdrayhlsi.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Indya2V4Y2pxbmlka2RyYXlobHNpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQ3ODkyOTIsImV4cCI6MjA5MDM2NTI5Mn0.NTXqianuwy4xLw4FMY09Z0Q70wf7KWCIzkejaU2sR8s"

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

async def run_baseline():
    print("=" * 80)
    print("PHASE 0/1: RUNNING PRODUCTION BROWSER FORENSIC BASELINE AUDIT")
    print(f"Target Base: {BASE_URL}")
    print("=" * 80)

    # 1. Obtain test session
    print("\n[STEP 1] Authenticating with Supabase to obtain test session...")
    auth_resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"email": "test@test.com", "password": "test123"},
        timeout=10
    )
    auth_data = auth_resp.json()
    token = auth_data["access_token"]
    refresh_token = auth_data.get("refresh_token", "")
    user_id = auth_data["user"]["id"]
    print(f"[AUTH OK] User ID: {user_id}")

    session_obj = {
        "access_token": token,
        "refresh_token": refresh_token,
        "user": auth_data["user"],
        "token_type": "bearer",
        "expires_at": int(time.time()) + 3600
    }
    sb_key = f"sb-wrkexcjqnidkdrayhlsi-auth-token"

    all_console_events = []
    all_page_errors = []
    all_unhandled_rejections = []
    all_network_requests = []
    all_ws_events = []
    route_timings = {}
    route_details = {}

    public_routes = ["/", "/signin", "/signup", "/wizard", "/2fa"]
    authenticated_routes = [
        "/app/dashboard",
        "/app/strategies",
        "/app/builder",
        "/app/backtest",
        "/app/marketplace",
        "/app/exchange",
        "/app/risk",
        "/app/portfolio",
        "/app/trades",
        "/app/signal-trace",
        "/app/billing",
        "/app/profile",
        "/app/security-logs",
        "/app/support",
        "/app/notifications"
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="msedge",
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-extensions",
                "--disable-plugins",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--no-first-run"
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=False,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0 ForensicBaseline/2.0"
        )
        page = await context.new_page()

        # Route-scoped listeners
        current_route = "/"

        def on_console(msg):
            entry = {
                "route": current_route,
                "type": msg.type,
                "text": msg.text,
                "location": msg.location,
                "timestamp": time.time()
            }
            all_console_events.append(entry)

        def on_page_error(err):
            entry = {
                "route": current_route,
                "error": str(err),
                "timestamp": time.time()
            }
            all_page_errors.append(entry)

        def on_request(req):
            entry = {
                "route": current_route,
                "url": req.url,
                "method": req.method,
                "resource_type": req.resource_type,
                "headers": dict(req.headers),
                "timestamp": time.time()
            }
            all_network_requests.append(entry)

        def on_response(res):
            for req in all_network_requests:
                if req["url"] == res.url and "status" not in req:
                    req["status"] = res.status
                    req["status_text"] = res.status_text
                    req["headers"] = dict(res.headers)
                    break

        def on_request_failed(req):
            for r in all_network_requests:
                if r["url"] == req.url:
                    r["failed"] = True
                    r["failure"] = req.failure
                    break

        def on_websocket(ws):
            ws_entry = {"route": current_route, "url": ws.url, "frames_sent": 0, "frames_received": 0, "closed": False, "errors": []}
            all_ws_events.append(ws_entry)
            ws.on("framesent", lambda payload: ws_entry.__setitem__("frames_sent", ws_entry["frames_sent"] + 1))
            ws.on("framereceived", lambda payload: ws_entry.__setitem__("frames_received", ws_entry["frames_received"] + 1))
            ws.on("close", lambda: ws_entry.__setitem__("closed", True))
            ws.on("socketerror", lambda err: ws_entry["errors"].append(str(err)))

        page.on("console", on_console)
        page.on("pageerror", on_page_error)
        page.on("request", on_request)
        page.on("response", on_response)
        page.on("requestfailed", on_request_failed)
        page.on("websocket", on_websocket)

        # 1. Audit Public Routes
        print("\n--- Auditing Public Routes ---")
        for r in public_routes:
            current_route = r
            print(f"Testing public route: {r} ...")
            t0 = time.perf_counter()
            resp = await page.goto(f"{BASE_URL}{r}", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1000)
            lat = (time.perf_counter() - t0) * 1000
            route_timings[r] = round(lat, 1)
            route_details[r] = {"status": resp.status if resp else None, "rendered": True}

            # Hard refresh test
            await page.reload(wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(500)

        # 2. Inject session for authenticated routes
        print("\n--- Injecting Authentication Session ---")
        await page.evaluate(f"""() => {{
            localStorage.setItem('{sb_key}', JSON.stringify({json.dumps(session_obj)}));
            localStorage.setItem('supabase.auth.token', JSON.stringify({json.dumps(session_obj)}));
            localStorage.setItem('auth_token', '{token}');
        }}""")

        # 3. Audit Authenticated Routes
        print("\n--- Auditing Authenticated Routes ---")
        for r in authenticated_routes:
            current_route = r
            print(f"Testing authenticated route: {r} ...")
            t0 = time.perf_counter()
            resp = await page.goto(f"{BASE_URL}{r}", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1500)
            lat = (time.perf_counter() - t0) * 1000
            route_timings[r] = round(lat, 1)
            
            content = await page.content()
            has_error = "Something went wrong" in content or "Error Boundary" in content
            route_details[r] = {"status": resp.status if resp else None, "rendered": not has_error, "error_boundary": has_error}

            # Hard refresh test
            print(f"  -> Testing hard refresh on {r}...")
            await page.reload(wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1000)

        await browser.close()

    # Classification & Analysis
    app_errors = []
    app_warnings = []
    external_noise = []
    gotrue_warnings = []

    for c in all_console_events:
        txt = c["text"]
        if "Multiple GoTrueClient instances" in txt or "GoTrueClient" in txt:
            gotrue_warnings.append(c)
            c["classification"] = "APPLICATION_WARNING"
            app_warnings.append(c)
        elif "chrome-extension://" in txt or "moz-extension://" in txt or "Copilot" in txt:
            c["classification"] = "EXTENSION"
            external_noise.append(c)
        elif c["type"] == "error":
            c["classification"] = "APPLICATION_ERROR"
            app_errors.append(c)
        elif c["type"] == "warning":
            c["classification"] = "APPLICATION_WARNING"
            app_warnings.append(c)
        else:
            c["classification"] = "INFO"

    # API performance check
    print("\n--- Running 20-Iteration P50/P95 API Performance Baseline ---")
    perf_endpoints = [
        ("Dashboard Complete Aggregation", "/api/dashboard?equity_days=30"),
        ("Dashboard Portfolio Overview", "/api/dashboard/overview"),
        ("Signal Trace Audit List", "/api/signal-trace/signals?limit=50&offset=0"),
        ("Strategies List", "/api/strategies"),
        ("Risk Management Settings", "/api/risk/settings"),
        ("Connected Exchange Vault", "/api/exchanges"),
        ("Billing Currency Preference", "/api/billing/currency"),
        ("Billing Plans List", "/api/billing/plans"),
        ("Billing Entitlements", "/api/billing/entitlements"),
        ("Notifications List", "/api/notifications?limit=100")
    ]
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "ForensicPerformanceAuditor/2.0"
    }

    perf_summary = {}
    for name, ep in perf_endpoints:
        latencies = []
        status_codes = []
        for i in range(20):
            t0 = time.perf_counter()
            try:
                r = requests.get(f"{BASE_URL}{ep}", headers=headers, allow_redirects=False, timeout=10)
                lat = (time.perf_counter() - t0) * 1000
                latencies.append(lat)
                status_codes.append(r.status_code)
            except Exception:
                lat = (time.perf_counter() - t0) * 1000
                latencies.append(lat)
                status_codes.append(599)
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        perf_summary[ep] = {
            "name": name,
            "min_ms": round(min(latencies), 1),
            "p50_ms": round(p50, 1),
            "p95_ms": round(p95, 1),
            "max_ms": round(max(latencies), 1),
            "status_codes": list(set(status_codes))
        }
        print(f"  [{name:<35}] Min: {min(latencies):5.1f}ms | P50: {p50:5.1f}ms | P95: {p95:5.1f}ms | Max: {max(latencies):5.1f}ms")

    # Duplicate API requests analysis
    api_requests = [r for r in all_network_requests if "/api/" in r["url"]]
    api_call_counts = {}
    for r in api_requests:
        url_path = r["url"].split(".net")[-1]
        api_call_counts[url_path] = api_call_counts.get(url_path, 0) + 1

    baseline_report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": BASE_URL,
        "routes_audited": {**route_details},
        "route_timings_ms": route_timings,
        "console_summary": {
            "total_events": len(all_console_events),
            "app_errors_count": len(app_errors),
            "app_warnings_count": len(app_warnings),
            "external_count": len(external_noise),
            "gotrue_warnings_count": len(gotrue_warnings)
        },
        "console_app_errors": app_errors,
        "console_app_warnings": app_warnings,
        "page_errors": all_page_errors,
        "network_summary": {
            "total_network_requests": len(all_network_requests),
            "failed_requests": [r for r in all_network_requests if r.get("failed")],
            "5xx_requests": [r for r in all_network_requests if r.get("status", 0) >= 500],
            "4xx_requests": [r for r in all_network_requests if 400 <= r.get("status", 0) < 500],
            "redirects": [r for r in all_network_requests if r.get("status", 0) in (301, 302, 307, 308)],
            "mixed_content": [r for r in all_network_requests if r["url"].startswith("http://")]
        },
        "api_request_counts": api_call_counts,
        "api_performance_p50_p95": perf_summary,
        "websocket_events": all_ws_events
    }

    out_file = "reports/browser_console_baseline.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(baseline_report, f, indent=2)

    print(f"\n[DONE] Baseline report generated at {out_file}")
    print(f"App Errors: {len(app_errors)} | App Warnings: {len(app_warnings)} | Page Errors: {len(all_page_errors)}")

if __name__ == "__main__":
    asyncio.run(run_baseline())
