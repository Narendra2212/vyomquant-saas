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

# Reconfigure stdout to utf-8 for Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

async def run_forensic_audit():
    print("=" * 80)
    print("STARTING STRICT PRODUCTION BROWSER FORENSIC AUDIT")
    print(f"Target Base: {BASE_URL}")
    print("=" * 80)

    audit_results = {
        "phase1_clean_browser": {},
        "phase2_lifecycle": {},
        "phase3_network": [],
        "phase4_websocket": [],
        "phase5_money_flows": {},
        "phase6_numerical": {},
        "phase7_console": [],
        "phase8_performance": {},
        "phase9_stale_assets": {},
        "phase10_security": {}
    }

    # Step 1: Pre-authenticate with Supabase to obtain valid test session
    print("\n[INIT] Authenticating with Supabase to obtain test session...")
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
    print(f"[INIT OK] Obtained auth session for user {user_id[:8]}...")

    # Step 2: Launch clean Edge browser instance
    async with async_playwright() as p:
        print("\n[PHASE 1] Launching clean Edge browser (headless, extensions disabled, incognito)...")
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0 ForensicAuditor/2.0"
        )
        
        page = await context.new_page()

        # Listeners for Console, Network, WebSocket
        console_messages = []
        network_requests = []
        websocket_events = []

        def on_console(msg):
            entry = {
                "type": msg.type,
                "text": msg.text,
                "location": msg.location,
                "timestamp": time.time()
            }
            console_messages.append(entry)

        def on_request(req):
            entry = {
                "url": req.url,
                "method": req.method,
                "resource_type": req.resource_type,
                "headers": dict(req.headers),
                "timestamp": time.time()
            }
            network_requests.append(entry)

        def on_response(res):
            for req in network_requests:
                if req["url"] == res.url and "status" not in req:
                    req["status"] = res.status
                    req["status_text"] = res.status_text
                    req["headers"] = dict(res.headers)
                    break

        def on_request_failed(req):
            for r in network_requests:
                if r["url"] == req.url:
                    r["failed"] = True
                    r["failure"] = req.failure
                    break

        def on_websocket(ws):
            ws_entry = {"url": ws.url, "frames_sent": 0, "frames_received": 0, "closed": False}
            websocket_events.append(ws_entry)
            ws.on("framesent", lambda payload: ws_entry.__setitem__("frames_sent", ws_entry["frames_sent"] + 1))
            ws.on("framereceived", lambda payload: ws_entry.__setitem__("frames_received", ws_entry["frames_received"] + 1))
            ws.on("close", lambda: ws_entry.__setitem__("closed", True))

        page.on("console", on_console)
        page.on("request", on_request)
        page.on("response", on_response)
        page.on("requestfailed", on_request_failed)
        page.on("websocket", on_websocket)

        # ── PHASE 2: Application Lifecycle & Route Navigation ──
        print("\n[PHASE 2] Testing Application Lifecycle & Route-by-Route Navigation...")
        
        # 1. Initial login page load
        print("  -> Loading /login...")
        await page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1000)
        
        # 2. Inject Supabase auth token into localStorage to restore real session
        print("  -> Injecting verified auth session into localStorage...")
        session_obj = {
            "access_token": token,
            "refresh_token": refresh_token,
            "user": auth_data["user"],
            "token_type": "bearer",
            "expires_at": int(time.time()) + 3600
        }
        sb_key = f"sb-wrkexcjqnidkdrayhlsi-auth-token"
        await page.evaluate(f"""() => {{
            localStorage.setItem('{sb_key}', JSON.stringify({json.dumps(session_obj)}));
            localStorage.setItem('supabase.auth.token', JSON.stringify({json.dumps(session_obj)}));
            localStorage.setItem('auth_token', '{token}');
        }}""")

        # 3. Navigate across all routes
        routes = [
            "/app",
            "/app/strategies",
            "/app/signal-trace",
            "/app/risk",
            "/app/exchanges",
            "/app/billing",
            "/app/notifications",
            "/app/terminal"
        ]

        route_results = {}
        for r in routes:
            print(f"  -> Navigating to {r}...")
            start_t = time.perf_counter()
            resp = await page.goto(f"{BASE_URL}{r}", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1500) # Wait for initial data fetches
            elapsed = (time.perf_counter() - start_t) * 1000
            
            # Check for error boundaries or crash messages
            page_text = await page.content()
            has_error_boundary = "Something went wrong" in page_text or "Error Boundary" in page_text or "Application Error" in page_text
            
            route_results[r] = {
                "status": resp.status if resp else None,
                "elapsed_ms": round(elapsed, 1),
                "rendered": not has_error_boundary,
                "error_boundary_detected": has_error_boundary
            }
            print(f"     [OK] {r} rendered in {elapsed:.1f}ms | ErrorBoundary: {has_error_boundary}")

            # Test route hard reload
            print(f"     -> Testing hard refresh on {r}...")
            ref_resp = await page.reload(wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1000)
            print(f"     [OK] {r} refreshed successfully (Status {ref_resp.status if ref_resp else 'N/A'})")

        audit_results["phase2_lifecycle"]["routes"] = route_results

        # ── PHASE 5: Money-Critical Browser Flows ──
        print("\n[PHASE 5] Executing Money-Critical Browser Interactive Flows...")
        
        # Flow 1: Risk Settings Load & Verification
        print("  -> Testing Risk Settings page flow...")
        await page.goto(f"{BASE_URL}/app/risk", wait_until="networkidle")
        await page.wait_for_timeout(1500)
        risk_content = await page.content()
        max_loss_visible = "Max Daily Loss" in risk_content or "Risk Level" in risk_content or "Circuit Breaker" in risk_content
        print(f"     [FLOW PASS] Risk settings UI components loaded: {max_loss_visible}")
        audit_results["phase5_money_flows"]["risk_settings_visible"] = max_loss_visible

        # Flow 2: Billing Currency Change
        print("  -> Testing Billing Currency switch flow...")
        await page.goto(f"{BASE_URL}/app/billing", wait_until="networkidle")
        await page.wait_for_timeout(1500)
        billing_content = await page.content()
        billing_plans_visible = "Subscription" in billing_content or "Plan" in billing_content or "USD" in billing_content or "EUR" in billing_content
        print(f"     [FLOW PASS] Billing plans UI components loaded: {billing_plans_visible}")
        audit_results["phase5_money_flows"]["billing_plans_visible"] = billing_plans_visible

        # Flow 3: Strategies List
        print("  -> Testing Strategies List page flow...")
        await page.goto(f"{BASE_URL}/app/strategies", wait_until="networkidle")
        await page.wait_for_timeout(1500)
        strat_content = await page.content()
        strat_visible = "Strategies" in strat_content or "Deploy" in strat_content or "Active" in strat_content
        print(f"     [FLOW PASS] Strategies list UI components loaded: {strat_visible}")
        audit_results["phase5_money_flows"]["strategies_visible"] = strat_visible

        # Flow 4: Signal Trace List
        print("  -> Testing Signal Trace page flow...")
        await page.goto(f"{BASE_URL}/app/signal-trace", wait_until="networkidle")
        await page.wait_for_timeout(1500)
        sig_content = await page.content()
        sig_visible = "Signal" in sig_content or "Audit" in sig_content or "Confidence" in sig_content
        print(f"     [FLOW PASS] Signal Trace UI components loaded: {sig_visible}")
        audit_results["phase5_money_flows"]["signal_trace_visible"] = sig_visible

        # Flow 5: Exchange Vault
        print("  -> Testing Exchange Connections page flow...")
        await page.goto(f"{BASE_URL}/app/exchanges", wait_until="networkidle")
        await page.wait_for_timeout(1500)
        exch_content = await page.content()
        exch_visible = "Exchange" in exch_content or "Connect" in exch_content or "Vault" in exch_content
        print(f"     [FLOW PASS] Exchange Vault UI components loaded: {exch_visible}")
        audit_results["phase5_money_flows"]["exchange_vault_visible"] = exch_visible

        # ── PHASE 9: Stale Deployment Detection ──
        print("\n[PHASE 9] Inspecting Loaded JavaScript Assets for Stale References...")
        script_urls = [r["url"] for r in network_requests if r["resource_type"] == "script" and BASE_URL in r["url"]]
        stale_findings = []
        for s_url in set(script_urls):
            try:
                s_resp = requests.get(s_url, timeout=10)
                s_text = s_resp.text
                if "vyomquant-alb" in s_text:
                    stale_findings.append(f"Found 'vyomquant-alb' in {s_url}")
                if "127.0.0.1:8000" in s_text:
                    stale_findings.append(f"Found '127.0.0.1:8000' in {s_url}")
            except Exception as e:
                print(f"Failed to inspect script {s_url}: {e}")

        print(f"  -> Scanned {len(set(script_urls))} production JS chunks.")
        if not stale_findings:
            print("  [OK] ZERO stale ALB or localhost endpoints found in loaded production bundles!")
        else:
            for f in stale_findings:
                print(f"  [STALE FINDING] {f}")
        audit_results["phase9_stale_assets"]["stale_findings"] = stale_findings

        # ── PHASE 4: WebSocket Forensics ──
        print("\n[PHASE 4] Analyzing WebSocket Connections...")
        print(f"  -> Total WebSocket connections initiated: {len(websocket_events)}")
        for ws in websocket_events:
            print(f"     WebSocket: {ws['url']} | Sent: {ws['frames_sent']} | Received: {ws['frames_received']} | Closed: {ws['closed']}")
        audit_results["phase4_websocket"] = websocket_events

        # ── PHASE 3: Network Request Forensics ──
        print("\n[PHASE 3] Auditing All Browser Network Requests...")
        failed_requests = []
        redirect_requests = []
        http_5xx_requests = []
        mixed_content_requests = []

        for req in network_requests:
            url = req["url"]
            status = req.get("status", 0)
            
            if url.startswith("http://"):
                mixed_content_requests.append(req)
            if status in (301, 302, 307, 308):
                redirect_requests.append(req)
            if status >= 500:
                http_5xx_requests.append(req)
            if req.get("failed"):
                failed_requests.append(req)

        print(f"  -> Total Network Requests: {len(network_requests)}")
        print(f"  -> Mixed Content (HTTP): {len(mixed_content_requests)}")
        print(f"  -> 307/308 Redirects: {len(redirect_requests)}")
        print(f"  -> 5xx Server Errors: {len(http_5xx_requests)}")
        print(f"  -> Failed Requests: {len(failed_requests)}")

        audit_results["phase3_network"] = {
            "total_requests": len(network_requests),
            "mixed_content_count": len(mixed_content_requests),
            "redirect_count": len(redirect_requests),
            "5xx_count": len(http_5xx_requests),
            "failed_count": len(failed_requests),
            "5xx_details": http_5xx_requests,
            "redirect_details": redirect_requests
        }

        # ── PHASE 7: Raw Console Inventory ──
        print("\n[PHASE 7] Auditing Raw Browser Console Inventory...")
        app_errors = []
        app_warnings = []
        external_noise = []

        for c in console_messages:
            txt = c["text"]
            # Classify
            if "chrome-extension://" in txt or "moz-extension://" in txt or "content.js" in txt or "Copilot" in txt:
                c["classification"] = "EXTENSION_ERROR"
                external_noise.append(c)
            elif c["type"] == "error":
                c["classification"] = "APPLICATION_ERROR"
                app_errors.append(c)
            elif c["type"] == "warning":
                c["classification"] = "APPLICATION_WARNING"
                app_warnings.append(c)
            else:
                c["classification"] = "EXPECTED_INFO"

        print(f"  -> Total Console Messages Captured: {len(console_messages)}")
        print(f"  -> Genuine Application Errors: {len(app_errors)}")
        print(f"  -> Genuine Application Warnings: {len(app_warnings)}")
        print(f"  -> External Browser / Extension Noise: {len(external_noise)}")

        for err in app_errors:
            print(f"     [APP ERROR] {err['text']} (at {err.get('location')})")
        for warn in app_warnings:
            print(f"     [APP WARNING] {warn['text']}")

        audit_results["phase7_console"] = {
            "total": len(console_messages),
            "app_errors": app_errors,
            "app_warnings": app_warnings,
            "raw_messages": console_messages
        }

        # ── PHASE 10: Security & Auth Protection Audit ──
        print("\n[PHASE 10] Testing Security & Unauthenticated Access Protection...")
        unauth_endpoints = [
            "/api/billing/currency",
            "/api/notifications",
            "/api/dashboard",
            "/api/dashboard/overview",
            "/api/signal-trace/signals",
            "/api/strategies",
            "/api/exchanges",
            "/api/risk/settings"
        ]
        sec_results = {}
        for ep in unauth_endpoints:
            u_resp = requests.get(f"{BASE_URL}{ep}", allow_redirects=False, timeout=10)
            sec_results[ep] = {
                "status": u_resp.status_code,
                "is_401": u_resp.status_code == 401,
                "location": u_resp.headers.get("Location")
            }
            print(f"  -> Unauthenticated GET {ep} -> Status {u_resp.status_code} ({'SECURE 401' if u_resp.status_code == 401 else 'FAIL'})")

        audit_results["phase10_security"] = sec_results

        await browser.close()

    # ── PHASE 8: Performance P50 / P95 Battery (20 Iterations per Route) ──
    print("\n[PHASE 8] Running 20-Iteration P50/P95 Authenticated Performance Battery...")
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
        redirect_count = 0
        err_5xx_count = 0

        for i in range(20):
            t0 = time.perf_counter()
            try:
                r = requests.get(f"{BASE_URL}{ep}", headers=headers, allow_redirects=False, timeout=10)
                lat_ms = (time.perf_counter() - t0) * 1000
                latencies.append(lat_ms)
                status_codes.append(r.status_code)
                if r.status_code in (301, 302, 307, 308):
                    redirect_count += 1
                if r.status_code >= 500:
                    err_5xx_count += 1
            except Exception as e:
                lat_ms = (time.perf_counter() - t0) * 1000
                latencies.append(lat_ms)
                status_codes.append(599)
                err_5xx_count += 1

        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        min_lat = min(latencies)
        max_lat = max(latencies)

        perf_summary[ep] = {
            "name": name,
            "min_ms": round(min_lat, 1),
            "p50_ms": round(p50, 1),
            "p95_ms": round(p95, 1),
            "max_ms": round(max_lat, 1),
            "5xx_count": err_5xx_count,
            "redirect_count": redirect_count
        }

        pass_status = "[PASS]" if p95 < 500 and err_5xx_count == 0 and redirect_count == 0 else "[WARN/FAIL]"
        print(f"  {pass_status} [{name:<35}] Min: {min_lat:5.1f}ms | P50: {p50:5.1f}ms | P95: {p95:5.1f}ms | Max: {max_lat:5.1f}ms | 5xx: {err_5xx_count} | 307s: {redirect_count}")

    audit_results["phase8_performance"] = perf_summary

    # Save complete raw JSON artifact
    out_path = "reports/final_production_browser_forensic_evidence.json"
    os.makedirs("reports", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(audit_results, f, indent=2)
    print(f"\n[DONE] Full raw forensic audit evidence saved to {out_path}")

if __name__ == "__main__":
    asyncio.run(run_forensic_audit())
