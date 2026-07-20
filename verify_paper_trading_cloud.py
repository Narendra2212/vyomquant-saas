import asyncio
import httpx
import uuid
import time
import os
from supabase import create_client, Client

SUPABASE_URL = "https://YOUR_PROJECT_REF.supabase.co"
SUPABASE_KEY = "YOUR_SUPABASE_SERVICE_ROLE_KEY"
BASE_URL = "https://backend-production-d57af.up.railway.app"

async def safe_db_query(db: Client, func, *args, **kwargs):
    """Safely query database with up to 3 retries in case of transient network issues."""
    for attempt in range(3):
        try:
            # Execute the table/query building functions
            return func(*args, **kwargs)
        except Exception as e:
            if attempt == 2:
                raise e
            print(f"Database query attempt {attempt+1} failed ({e}). Retrying in 2 seconds...")
            await asyncio.sleep(2.0)

async def run_paper_trading_validation():
    print("Starting Cloud Paper Trading Journey Validation...")
    
    # Initialize Supabase client
    db: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    # Generate random test user
    uid = str(uuid.uuid4())[:8]
    email = f"cloud_val_{uid}@aerora.io"
    password = f"CloudValPassword123!"
    username = f"cloud_val_{uid}"
    
    failures = []
    warnings = []
    report = [
        "# Cloud Paper Trading Journey Validation Report",
        "",
        f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Backend Endpoint:** `{BASE_URL}`",
        f"**Supabase Endpoint:** `{SUPABASE_URL}`",
        "",
    ]
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Register User
        report.append("## Step 1: User Registration")
        register_payload = {
            "email": email,
            "password": password,
            "username": username
        }
        try:
            r = await client.post(f"{BASE_URL}/api/auth/register", json=register_payload)
            report.append(f"- `POST /api/auth/register` status: **{r.status_code}**")
            if r.status_code == 201:
                reg_data = r.json()
                token = reg_data.get("access_token")
                report.append(f"- User registered successfully. Auto-login token length: {len(token) if token else 0}")
            else:
                failures.append(f"REGISTRATION_FAILED: Status {r.status_code}, Body: {r.text}")
                report.append(f"- Registration failed: {r.text}")
                token = None
        except Exception as e:
            failures.append(f"REGISTRATION_EXCEPTION: {e}")
            report.append(f"- Registration exception: {e}")
            token = None
            
        # 2. Login User (to confirm credentials work)
        report.append("\n## Step 2: User Login")
        login_payload = {
            "email": email,
            "password": password
        }
        user_id = None
        try:
            r = await client.post(f"{BASE_URL}/api/auth/login", json=login_payload)
            report.append(f"- `POST /api/auth/login` status: **{r.status_code}**")
            if r.status_code == 200:
                login_data = r.json()
                token = login_data.get("access_token")
                user_id = login_data.get("user", {}).get("id")
                report.append(f"- JWT Login Successful.")
                report.append(f"- Issued Token: `{token[:15]}...{token[-15:]}`")
                report.append(f"- Tenant User ID: `{user_id}`")
            else:
                failures.append(f"LOGIN_FAILED: Status {r.status_code}, Body: {r.text}")
                report.append(f"- Login failed: {r.text}")
        except Exception as e:
            failures.append(f"LOGIN_EXCEPTION: {e}")
            report.append(f"- Login exception: {e}")
            
        if not token or not user_id:
            report.append("\n### Validation aborted due to missing JWT or User ID")
            write_report(report, failures, warnings)
            return

        # Insert profile row directly via service role key
        report.append("\n## Step 2b: Profile Provisioning")
        try:
            print(f"Provisioning profile in DB for user: {user_id}")
            def do_insert():
                return db.table("profiles").insert({
                    "id": user_id,
                    "username": username,
                    "subscription_tier": "free",
                    "balance": 1000.0,
                    "deployed_bots": 0,
                    "ml_strategies_built": 0,
                    "ml_addons_purchased": 0
                }).execute()
            await safe_db_query(db, do_insert)
            report.append("- Profile row successfully provisioned directly in database.")
        except Exception as e:
            warnings.append(f"PROFILE_PROVISIONING_WARNING: {e}")
            report.append(f"- Profile provisioning warning/error: {e}")

        headers = {"Authorization": f"Bearer {token}"}
        
        # 3. Create Strategy
        report.append("\n## Step 3: Create Strategy")
        strategy_payload = {
            "name": f"Cloud Val Strategy {uid}",
            "symbol": "BTC/USDT",
            "timeframe": "5m"
        }
        strategy_id = None
        try:
            r = await client.post(f"{BASE_URL}/api/strategies/", headers=headers, json=strategy_payload)
            report.append(f"- `POST /api/strategies/` status: **{r.status_code}**")
            if r.status_code in (200, 201):
                strategy_id = r.json().get("strategy_id")
                report.append(f"- Strategy created successfully.")
                report.append(f"- Strategy ID: `{strategy_id}`")
            else:
                failures.append(f"STRATEGY_CREATION_FAILED: Status {r.status_code}, Body: {r.text}")
                report.append(f"- Strategy creation failed: {r.text}")
        except Exception as e:
            failures.append(f"STRATEGY_CREATION_EXCEPTION: {e}")
            report.append(f"- Strategy creation exception: {e}")
            
        if not strategy_id:
            report.append("\n### Validation aborted due to missing Strategy ID")
            write_report(report, failures, warnings)
            return

        # 4. Check DB Row Counts before executing paper trade
        report.append("\n## Step 4: Verification of Database State (Before)")
        try:
            def get_count_before():
                return db.table("execution_records").select("execution_id", count="exact").eq("tenant_id", user_id).execute()
            count_before = await safe_db_query(db, get_count_before)
            records_count_before = count_before.count or 0
            report.append(f"- `execution_records` count before trade for user `{user_id}`: **{records_count_before}**")
        except Exception as e:
            warnings.append(f"DB_QUERY_WARNING: {e}")
            report.append(f"- DB query warning: {e}")
            records_count_before = 0

        # 5. Execute Paper Trade via POST /api/execution/signal
        report.append("\n## Step 5: Execute Paper Trade (Signal Generation)")
        signal_payload = {
            "strategy_id": strategy_id,
            "symbol": "BTC/USDT",
            "signal": "buy",
            "confidence": 0.98,
            "price": 61250.0
        }
        try:
            r = await client.post(f"{BASE_URL}/api/execution/signal", headers=headers, json=signal_payload)
            report.append(f"- `POST /api/execution/signal` status: **{r.status_code}**")
            if r.status_code in (200, 201):
                sig_data = r.json()
                status = sig_data.get("status")
                exec_id = sig_data.get("execution_id")
                message = sig_data.get("message")
                report.append(f"- Signal Accepted. Status: `{status}`, Execution ID: `{exec_id}`")
                report.append(f"- Message: `{message}`")
            else:
                failures.append(f"SIGNAL_POST_FAILED: Status {r.status_code}, Body: {r.text}")
                report.append(f"- Signal execution post failed: {r.text}")
                exec_id = None
        except Exception as e:
            failures.append(f"SIGNAL_POST_EXCEPTION: {e}")
            report.append(f"- Signal execution post exception: {e}")
            exec_id = None

        # 6. Wait and Check Database Row Counts (After)
        report.append("\n## Step 6: Verification of Database Persistence (After)")
        await asyncio.sleep(5.0) # Wait for database persistence
        
        try:
            def get_count_after():
                return db.table("execution_records").select("execution_id", count="exact").eq("tenant_id", user_id).execute()
            count_after = await safe_db_query(db, get_count_after)
            records_count_after = count_after.count or 0
            report.append(f"- `execution_records` count after trade for user `{user_id}`: **{records_count_after}**")
            
            diff = records_count_after - records_count_before
            if diff > 0:
                report.append(f"- **SUCCESS**: Execution record was successfully saved (+{diff} rows).")
                
                # Fetch record details
                def get_records():
                    return db.table("execution_records").select("*").eq("tenant_id", user_id).execute()
                records = await safe_db_query(db, get_records)
                if records.data:
                    rec = records.data[0]
                    report.append(f"  - Persisted Symbol: `{rec.get('symbol')}`")
                    report.append(f"  - Persisted Side: `{rec.get('side')}`")
                    report.append(f"  - Persisted Size: `{rec.get('size')}`")
                    report.append(f"  - Persisted Price: `{rec.get('price')}`")
                    report.append(f"  - Persisted Execution ID: `{rec.get('execution_id')}`")
                    report.append(f"  - Created At: `{rec.get('created_at')}`")
            else:
                failures.append(f"EXECUTION_RECORD_PERSISTENCE_FAILED: Count before: {records_count_before}, Count after: {records_count_after}")
                report.append("- **FAILURE**: No execution record found in database after signal execution.")
        except Exception as e:
            failures.append(f"DB_QUERY_EXCEPTION: {e}")
            report.append(f"- DB query exception: {e}")

    write_report(report, failures, warnings)

def write_report(report, failures, warnings):
    report.append("\n## Verdict & Summary")
    if failures:
        report.append("### Critical Failures:")
        for f in failures:
            report.append(f"- {f}")
        verdict = "DEPLOYMENT_FAILED_VERIFICATION"
    else:
        verdict = "DEPLOYED_AND_OPERATIONAL"
        report.append("✅ **All critical checks passed. Cloud Paper Trading Journey is certified.**")
        
    if warnings:
        report.append("### Warnings:")
        for w in warnings:
            report.append(f"- {w}")
            
    report.append(f"\n### Final Status Verdict: `{verdict}`")
    
    # Save the report
    with open("paper_trading_cloud_validation.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report))
        
    print(f"Certification complete. Verdict: {verdict}")

if __name__ == "__main__":
    asyncio.run(run_paper_trading_validation())
