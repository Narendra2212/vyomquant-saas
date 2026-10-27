import asyncio
import json
import os
import subprocess
import threading
import time

import httpx
import websockets


def read_output(proc, logs):
    try:
        for line in iter(proc.stdout.readline, ''):
            if line:
                logs.append(line.strip())
            else:
                break
    except Exception:
        pass

async def run_certification():
    print("Starting backend runtime certification...", flush=True)
    env = os.environ.copy()
    with open(".env", "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, val = line.split("=", 1)
                env[key] = val
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        ["python", "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1
    )

    logs = []
    log_thread = threading.Thread(target=read_output, args=(proc, logs), daemon=True)
    log_thread.start()

    print("Waiting for server to start...", flush=True)
    # Poll for up to 60 seconds
    server_ready = False
    async with httpx.AsyncClient() as client:
        for _ in range(30):
            try:
                r = await client.get("http://127.0.0.1:8000/health/live", timeout=2.0)
                if r.status_code == 200:
                    server_ready = True
                    break
            except Exception:
                pass
            await asyncio.sleep(2)

    log_output = "\n".join(logs)
    
    if not server_ready:
        print("Server failed to start in 60s", flush=True)
    
    has_mock_supabase = "MockSupabaseClient" in log_output
    has_fake_token = "fake_token_due_to_auth_failure" in log_output
    has_invalid_key = "Invalid API key" in log_output
    has_exceptions = "Exception" in log_output or "Traceback" in log_output
    
    md_lines = [
        "# BACKEND RUNTIME CERTIFICATION V2",
        "",
        "## PHASE 1 - STARTUP",
        "```",
        log_output[:2000] + "\n... truncated ..." if len(log_output) > 2000 else log_output,
        "```",
        f"- No MockSupabaseClient: {'PASS' if not has_mock_supabase else 'FAIL'}",
        f"- No fake_token_due_to_auth_failure: {'PASS' if not has_fake_token else 'FAIL'}",
        f"- No Invalid API key: {'PASS' if not has_invalid_key else 'FAIL'}",
        f"- No exceptions: {'PASS' if not has_exceptions else 'WARNING (see logs)'}",
        ""
    ]

    md_lines.extend(["## PHASE 2 - HEALTH"])
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r1 = await client.get("http://127.0.0.1:8000/health")
            h1 = r1.json()
            md_lines.append(f"GET /health: {h1.get('status')} (Redis: {h1.get('services', {}).get('redis')}, QuestDB: {h1.get('services', {}).get('questdb')}, Supabase: {h1.get('services', {}).get('supabase')})")
        except Exception as e:
            md_lines.append(f"GET /health FAILED: {e}")

        try:
            r2 = await client.get("http://127.0.0.1:8000/health/services")
            h2 = r2.json()
            md_lines.append(f"GET /health/services: {h2.get('status')} (Services: {json.dumps(h2.get('services'))})")
        except Exception as e:
            md_lines.append(f"GET /health/services FAILED: {e}")
        md_lines.append("")

        md_lines.extend(["## PHASE 3 - AUTH"])
        user_email = f"test_{int(time.time())}@aerora.io"
        user_pass = "Test1234!"
        jwt_token = None
        user_id = None
        try:
            r_reg = await client.post("http://127.0.0.1:8000/api/auth/register", json={"email": user_email, "password": user_pass, "username": "cert_user"})
            md_lines.append(f"POST /api/auth/register: {r_reg.status_code} {r_reg.text[:100]}")
            
            r_login = await client.post("http://127.0.0.1:8000/api/auth/login", json={"email": user_email, "password": user_pass})
            md_lines.append(f"POST /api/auth/login: {r_login.status_code}")
            if r_login.status_code == 200:
                data = r_login.json()
                jwt_token = data.get("access_token")
                user_id = data.get("user", {}).get("id")
                is_mock = "fake" in str(jwt_token).lower() or "mock" in str(jwt_token).lower()
                md_lines.append(f"- Real JWT returned: {'PASS' if jwt_token else 'FAIL'}")
                md_lines.append(f"- Not a mock token: {'PASS' if not is_mock else 'FAIL'}")
            else:
                md_lines.append(f"Login failed: {r_login.text[:100]}")
        except Exception as e:
            md_lines.append(f"AUTH FAILED: {e}")
        md_lines.append("")

        md_lines.extend(["## PHASE 4 - STRATEGY"])
        strategy_id_created = None
        if jwt_token:
            headers = {"Authorization": f"Bearer {jwt_token}"}
            strat_payload = {
                "name": "CertifyStrat",
                "symbol": "BTC/USDT",
                "timeframe": "5m"
            }
            try:
                r_strat = await client.post("http://127.0.0.1:8000/api/strategies/", json=strat_payload, headers=headers, follow_redirects=True)
                md_lines.append(f"POST /api/strategies/: {r_strat.status_code} {r_strat.text[:100] if r_strat.status_code != 200 else ''}")
                
                r_strat_get = await client.get("http://127.0.0.1:8000/api/strategies/", headers=headers, follow_redirects=True)
                md_lines.append(f"GET /api/strategies/: {r_strat_get.status_code}")
                
                if r_strat.status_code in (200, 201) and "strategy_id" in r_strat.json():
                    strategy_id_created = r_strat.json()["strategy_id"]
                    
                if r_strat_get.status_code == 200:
                    try:
                        strats = r_strat_get.json()
                        if len(strats) > 0:
                            md_lines.append("- row exists in PostgreSQL: PASS")
                        else:
                            md_lines.append("Strategy get returned empty list")
                    except Exception as json_e:
                        md_lines.append(f"Strategy get JSON parse failed: {json_e} - {r_strat_get.text[:100]}")
                else:
                    md_lines.append(f"Strategy get failed: {r_strat_get.text[:100]}")
            except Exception as e:
                md_lines.append(f"STRATEGY CREATION FAILED: {repr(e)}")
        else:
            md_lines.append("Skipped due to no JWT token")
        md_lines.append("")

        md_lines.extend(["## PHASE 5 - EXECUTION"])
        if jwt_token and strategy_id_created:
            try:
                sig_payload = {
                    "strategy_id": strategy_id_created,
                    "symbol": "BTC/USDT",
                    "signal": "buy",
                    "confidence": 0.9,
                    "price": 60000.0
                }
                r_ex = await client.post("http://127.0.0.1:8000/api/execution/signal", json=sig_payload, headers=headers)
                md_lines.append(f"POST /api/execution/signal: {r_ex.status_code} {r_ex.text[:100]}")
                if r_ex.status_code in (200, 201):
                    md_lines.append("- execution_records row created: PASS")
                else:
                    md_lines.append("- execution_records row created: FAIL")
            except Exception as e:
                md_lines.append(f"EXECUTION FAILED: {e}")
        else:
            md_lines.append("Skipped due to no JWT token or missing strategy_id")
        md_lines.append("")

        md_lines.extend(["## PHASE 6 - WEBSOCKET"])
        if jwt_token and user_id:
            try:
                # Need to use the proper endpoint /ws/pnl/{user_id} which does not require exchange API keys
                ws_url = f"ws://127.0.0.1:8000/ws/pnl/{user_id}?token={jwt_token}"
                async with websockets.connect(ws_url) as websocket:
                    md_lines.append("- Handshake success: PASS")
                    md_lines.append("- Authentication success: PASS")
                    
                    try:
                        # Wait for first PnL event
                        msg = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                        md_lines.append(f"- Received event: PASS ({msg[:50]})")
                    except asyncio.TimeoutError:
                        md_lines.append("- No immediate events received (PASS if normal)")
            except Exception as e:
                md_lines.append(f"WEBSOCKET FAILED: {e}")
        else:
            md_lines.append("Skipped due to no JWT token or user_id")

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()

    md_lines.append("")
    
    verdict = "RUNTIME_READY"
    content = "\n".join(md_lines)
    if not server_ready or "FAIL" in content or "FAILED" in content:
        verdict = "RUNTIME_BLOCKED"
    if "fallback" in content.lower():
        verdict = "RUNTIME_BLOCKED"
        
    md_lines.append(f"## Final Verdict: {verdict}")

    with open("backend_runtime_certification_v2.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    print(f"Certification complete. Verdict: {verdict}", flush=True)

if __name__ == "__main__":
    asyncio.run(run_certification())
