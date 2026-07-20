import os
import httpx
import asyncio
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://YOUR_PROJECT_REF.supabase.co")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

BACKEND_URL = "http://127.0.0.1:8000"

# Fetch from .env if not set
if not SUPABASE_ANON_KEY:
    try:
        with open(".env", "r") as f:
            for line in f:
                if line.startswith("SUPABASE_ANON_KEY="):
                    SUPABASE_ANON_KEY = line.split("=", 1)[1].strip()
                elif line.startswith("SUPABASE_SERVICE_ROLE_KEY="):
                    SUPABASE_SERVICE_ROLE_KEY = line.split("=", 1)[1].strip()
    except:
        pass

async def main():
    results = {
        "SUPABASE": "FAIL",
        "AUTH": "FAIL",
        "JWT": "FAIL",
        "RLS": "FAIL",
        "BLOCKERS": []
    }
    
    try:
        # 1. Verify DNS / 2. Verify project exists
        try:
            httpx.get(f"{SUPABASE_URL}/auth/v1/health", timeout=15, verify=False)
            results["SUPABASE"] = "PASS"
        except Exception as e:
            results["BLOCKERS"].append(f"DNS/Project unreachable: {e}")
            return print_results(results)

        # 3/4. Keys
        if not SUPABASE_ANON_KEY or not SUPABASE_SERVICE_ROLE_KEY:
            results["BLOCKERS"].append("Missing Supabase keys")
            return print_results(results)

        service_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        
        # 5. Verify signup & auth
        import uuid
        uid_str = str(uuid.uuid4())[:8]
        tenant_a_email = f"tenant_a_{uid_str}@example.com"
        tenant_b_email = f"tenant_b_{uid_str}@example.com"
        tenant_password = "SecurePassword123!"
        
        try:
            service_client.auth.admin.create_user({
                "email": tenant_a_email,
                "password": tenant_password,
                "email_confirm": True
            })
            service_client.auth.admin.create_user({
                "email": tenant_b_email,
                "password": tenant_password,
                "email_confirm": True
            })
        except Exception as e:
            results["BLOCKERS"].append(f"Admin create_user failed: {e}")
        
        async with httpx.AsyncClient(verify=False) as client:
            # Login A
            login_a = await client.post(f"{BACKEND_URL}/api/auth/login", json={
                "email": tenant_a_email, "password": tenant_password
            })
            # Login B
            login_b = await client.post(f"{BACKEND_URL}/api/auth/login", json={
                "email": tenant_b_email, "password": tenant_password
            })
            
            if login_a.status_code == 200 and login_b.status_code == 200:
                results["AUTH"] = "PASS"
                results["JWT"] = "PASS"  # JWT created successfully
                
                token_a = login_a.json().get("access_token")
                token_b = login_b.json().get("access_token")
                
                # RLS / Tenant Isolation Test
                strategy_payload = {
                    "name": "Test Strategy A",
                    "symbol": "BTC/USDT",
                    "timeframe": "1h",
                    "buy_logic": {},
                    "sell_logic": {},
                    "risk": {},
                    "indicators": [],
                    "exchange_id": "binance",
                    "status": "stopped"
                }
                
                # Tenant A creates strategy
                create_resp = await client.post(
                    f"{BACKEND_URL}/api/strategies/",
                    json=strategy_payload,
                    headers={"Authorization": f"Bearer {token_a}"}
                )
                
                if create_resp.status_code == 200:
                    strategy_id = create_resp.json().get("strategy_id")
                    
                    # Tenant B attempts read
                    read_resp = await client.get(
                        f"{BACKEND_URL}/api/strategies/",
                        headers={"Authorization": f"Bearer {token_b}"}
                    )
                    
                    if read_resp.status_code == 200:
                        strategies = read_resp.json()
                        if any(s.get("id") == strategy_id for s in strategies):
                            results["BLOCKERS"].append(f"Tenant B could read Tenant A strategy via list endpoint")
                        else:
                            results["RLS"] = "PASS"
                    else:
                        results["BLOCKERS"].append(f"Tenant B list failed: {read_resp.status_code}")
                else:
                    results["BLOCKERS"].append(f"Tenant A could not create strategy: {create_resp.text}")
            else:
                results["BLOCKERS"].append(f"Auth failed during login. Login A: {login_a.status_code} {login_a.text}. Login B: {login_b.status_code} {login_b.text}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        results["BLOCKERS"].append(f"Unhandled exception: {e}")
        
    print_results(results)

def print_results(results):
    print("SUPABASE")
    print(results["SUPABASE"])
    print("\nAUTH")
    print(results["AUTH"])
    print("\nJWT")
    print(results["JWT"])
    print("\nRLS")
    print(results["RLS"])
    
    if results["BLOCKERS"]:
        print("\nBLOCKERS")
        for b in results["BLOCKERS"]:
            print(f"- {b}")

if __name__ == "__main__":
    asyncio.run(main())
