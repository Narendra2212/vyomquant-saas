import sys
import os
import traceback

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

os.environ["ENV"] = "test"
os.environ["DEV_MODE"] = "true"
os.environ["VYOMQUANT_MODE"] = "paper"
os.environ["DATABASE_URL"] = "postgresql://postgres.wrkexcjqnidkdrayhlsi:Narendra%40221203221203@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres"
os.environ["SUPABASE_URL"] = "https://wrkexcjqnidkdrayhlsi.supabase.co"
os.environ["SUPABASE_KEY"] = "dummy"

sys.path.insert(0, os.getcwd())

modules_to_test = [
    "backend_app.core.safety_config",
    "backend_app.core.dependencies",
    "backend_app.core.database",
    "backend_app.routers.auth",
    "backend_app.routers.exchange",
    "backend_app.routers.library",
    "backend_app.routers.market",
    "backend_app.routers.orders",
    "backend_app.routers.strategies",
    "backend_app.routers.portfolio",
    "backend_app.routers.dashboard",
    "backend_app.routers.strategy_operations",
    "backend_app.routers.user",
    "backend_app.routers.referral",
    "backend_app.routers.notifications",
    "backend_app.routers.admin",
    "backend_app.routers.risk",
    "backend_app.routers.billing",
    "backend_app.routers.security",
    "backend_app.routers.analytics",
    "backend_app.routers.distributed_execution",
    "backend_app.routers.health",
    "backend_app.routers.health_websocket",
    "backend_app.routers.signals",
    "backend_app.routers.support",
    "backend_app.api_ws.ws_routes",
    "backend_app.main"
]

success_count = 0
for mod in modules_to_test:
    try:
        __import__(mod)
        print(f"[OK] {mod}", flush=True)
        success_count += 1
    except Exception as e:
        print(f"[FAIL] {mod}: {e}", flush=True)
        traceback.print_exc()

print(f"\nSuccessfully imported {success_count}/{len(modules_to_test)} modules!", flush=True)
