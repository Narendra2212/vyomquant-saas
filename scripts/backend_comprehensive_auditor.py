import os
import re
import json

def run_backend_census():
    print("================================================================================")
    print("EXECUTING COMPLETE BACKEND FORENSIC CENSUS & INVENTORY AUDIT")
    print("================================================================================")
    
    backend_dir = "backend_app"
    all_files = []
    for root, _, files in os.walk(backend_dir):
        for f in files:
            if f.endswith('.py') and not f.startswith('__pycache__'):
                all_files.append(os.path.join(root, f))
                
    print(f"Discovered {len(all_files)} backend source files in backend_app/.")
    
    # Classify files
    classifications = {}
    for fpath in all_files:
        rel = os.path.relpath(fpath, backend_dir).replace("\\", "/")
        category = "UTILITY"
        if "routers/" in rel or rel == "main.py":
            category = "API"
        elif "auth" in rel or "jwt" in rel:
            category = "AUTH"
        elif "security" in rel or "tenant" in rel or "encryption" in rel:
            category = "SECURITY"
        elif "execution" in rel or "engine" in rel:
            category = "EXECUTION"
        elif "exchange" in rel or "ccxt" in rel:
            category = "TRADING"
        elif "risk" in rel or "kill_switch" in rel:
            category = "RISK"
        elif "strategy" in rel or "dag" in rel:
            category = "STRATEGY"
        elif "backtest" in rel:
            category = "STRATEGY"
        elif "market" in rel or "ticker" in rel or "pipeline" in rel:
            category = "MARKET DATA"
        elif "database" in rel or "postgres" in rel or "models" in rel or "schemas" in rel or "repository" in rel or "alembic" in rel:
            category = "DATABASE"
        elif "questdb" in rel:
            category = "DATABASE"
        elif "redis" in rel or "cache" in rel:
            category = "CACHE"
        elif "websocket" in rel or "ws" in rel:
            category = "WEBSOCKET"
        elif "worker" in rel or "celery" in rel or "scheduler" in rel:
            category = "WORKER"
        elif "watchdog" in rel or "reconcil" in rel:
            category = "RECONCILIATION"
        elif "billing" in rel or "stripe" in rel or "razorpay" in rel:
            category = "BILLING"
        elif "support" in rel or "ticket" in rel:
            category = "SUPPORT"
        elif "referral" in rel:
            category = "REFERRAL"
        elif "health" in rel:
            category = "HEALTH"
        elif "logging" in rel or "metrics" in rel or "telemetry" in rel or "sentry" in rel:
            category = "OBSERVABILITY"
        elif "config" in rel or "settings" in rel or "env" in rel:
            category = "CONFIGURATION"
            
        classifications[rel] = category
        
    print("\n--- File Classifications Summary ---")
    cat_counts = {}
    for c in classifications.values():
        cat_counts[c] = cat_counts.get(c, 0) + 1
    for cat, cnt in sorted(cat_counts.items()):
        print(f"  {cat:20s}: {cnt:2d} files")

    # 2. Extract mounted routes from main.py and routers
    with open(os.path.join(backend_dir, "main.py"), "r", encoding="utf-8", errors="ignore") as f:
        main_py = f.read()
        
    routers_mounted = re.findall(r'app\.include_router\(\s*([^,\s]+)', main_py)
    print(f"\nDiscovered {len(routers_mounted)} routers mounted in main.py:")
    for r in routers_mounted:
        print(f"  include_router({r})")
        
    # 3. Extract endpoints from routers/*.py
    endpoints = []
    routers_dir = os.path.join(backend_dir, "routers")
    if os.path.exists(routers_dir):
        for rf in os.listdir(routers_dir):
            if rf.endswith(".py"):
                rpath = os.path.join(routers_dir, rf)
                with open(rpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                matches = re.findall(r'@router\.(get|post|put|delete|patch)\(\s*["\']([^"\']+)["\']', content, re.IGNORECASE)
                for verb, path in matches:
                    endpoints.append({
                        "router": rf,
                        "verb": verb.upper(),
                        "path": path
                    })
    print(f"\nTotal HTTP Route Handlers cataloged across routers: {len(endpoints)}")

    os.makedirs("reports", exist_ok=True)
    with open("reports/backend_census.json", "w", encoding="utf-8") as out:
        json.dump({
            "total_files": len(all_files),
            "classifications": classifications,
            "category_counts": cat_counts,
            "routers_mounted": routers_mounted,
            "total_endpoints": len(endpoints),
            "endpoints": endpoints
        }, out, indent=2)

if __name__ == '__main__':
    run_backend_census()
