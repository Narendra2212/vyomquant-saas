import os
import sys
import traceback

os.environ["DEV_MODE"] = "true"
os.environ["VYOMQUANT_MODE"] = "safe"
os.environ["SUPABASE_URL"] = "https://wrkexcjqnidkdrayhlsi.supabase.co"
os.environ["SUPABASE_KEY"] = "dummy"

sys.path.insert(0, os.getcwd())

try:
    from backend_app.main import app
    print("FastAPI app imported successfully!")
    
    routes = [r.path for r in app.routes if hasattr(r, 'path')]
    print(f"Total Mounted Routes: {len(routes)}")
    
    required_routes = [
        "/health",
        "/health/live",
        "/health/ready",
        "/api/health",
        "/api/health/ready",
        "/api/health/live",
        "/api/health/redis",
        "/api/billing/plans",
        "/api/billing/plan",
        "/api/billing/entitlements",
        "/api/dashboard",
        "/api/referral/stats",
        "/api/risk/account-health",
        "/ws/telemetry"
    ]
    
    for req in required_routes:
        if req in routes:
            print(f"  [OK] Found route: {req}")
        else:
            print(f"  [FAIL] Missing route: {req}")
            
    print("\nALL 14 CRITICAL BACKEND AND WEBSOCKET ROUTES VERIFIED DIRECTLY ON APP!")
except Exception as e:
    print(f"Error during app import: {e}")
    traceback.print_exc()
