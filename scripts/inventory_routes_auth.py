import sys
import os
import json

sys.path.insert(0, '.')
os.environ["ENV"] = "testing"
os.environ["DEV_MODE"] = "false"

from backend_app.main import app

def get_all_routes(router, prefix=""):
    result = []
    for r in getattr(router, "routes", []):
        if hasattr(r, "routes"):
            # Sub-router or mount
            sub_prefix = prefix + (getattr(r, "path", "") or getattr(r, "prefix", ""))
            result.extend(get_all_routes(r, sub_prefix))
        else:
            path = prefix + (getattr(r, "path", "") or getattr(r, "path_format", ""))
            methods = list(getattr(r, "methods", []))
            endpoint = getattr(r, "endpoint", None)
            
            # Extract dependencies
            deps = []
            if hasattr(r, "dependencies"):
                for d in r.dependencies:
                    dep_fn = getattr(d, "dependency", d)
                    deps.append(getattr(dep_fn, "__name__", str(dep_fn)))
            if hasattr(r, "dependant"):
                for d in getattr(r.dependant, "dependencies", []):
                    dep_fn = getattr(d, "call", d)
                    deps.append(getattr(dep_fn, "__name__", str(dep_fn)))
            
            result.append({
                "path": path,
                "methods": methods,
                "endpoint": getattr(endpoint, "__name__", str(endpoint)),
                "dependencies": deps
            })
    return result

all_routes = get_all_routes(app)
print(f"Total Routes Registered: {len(all_routes)}")

public_allowed_prefixes = ("/api/auth", "/docs", "/openapi.json", "/health", "/api/health", "/redoc", "/favicon.ico")

unprotected = []
for r in all_routes:
    path = r["path"]
    methods = r["methods"]
    deps = r["dependencies"]
    
    if any(path.startswith(p) for p in public_allowed_prefixes) or path == "/" or "openapi" in path:
        continue
        
    has_auth = any(d in ("get_current_user", "get_admin_user", "get_operator_user", "get_request_supabase") for d in deps)
    if not has_auth:
        unprotected.append(r)

print(f"\nUnprotected Non-Public Routes ({len(unprotected)}):")
for u in unprotected:
    print(f"  {u['methods']} {u['path']} -> {u['endpoint']} (deps: {u['dependencies']})")
