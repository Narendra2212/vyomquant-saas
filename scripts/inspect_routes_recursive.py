import sys
import os
from starlette.routing import Mount, Route
from fastapi.routing import APIRoute

sys.path.insert(0, '.')
os.environ["ENV"] = "testing"
os.environ["DEV_MODE"] = "false"

from backend_app.main import app

def inspect_routes(route_list, prefix=""):
    out = []
    for r in route_list:
        if isinstance(r, APIRoute):
            full_path = prefix + r.path
            deps = []
            if hasattr(r, "dependencies") and r.dependencies:
                for d in r.dependencies:
                    dep_fn = getattr(d, "dependency", d)
                    deps.append(getattr(dep_fn, "__name__", str(dep_fn)))
            if hasattr(r, "dependant") and r.dependant.dependencies:
                for d in r.dependant.dependencies:
                    dep_fn = getattr(d, "call", d)
                    deps.append(getattr(dep_fn, "__name__", str(dep_fn)))
            out.append({
                "path": full_path,
                "methods": list(r.methods),
                "name": r.name,
                "endpoint": getattr(r.endpoint, "__name__", str(r.endpoint)),
                "dependencies": deps
            })
        elif isinstance(r, Mount):
            mount_path = prefix + r.path
            sub_routes = getattr(r.app, "routes", [])
            out.extend(inspect_routes(sub_routes, mount_path))
        elif isinstance(r, Route):
            full_path = prefix + r.path
            out.append({
                "path": full_path,
                "methods": list(r.methods) if r.methods else ["GET"],
                "name": r.name,
                "endpoint": getattr(r.endpoint, "__name__", str(r.endpoint)),
                "dependencies": []
            })
    return out

all_endpoints = inspect_routes(app.routes)
print(f"Total endpoints found: {len(all_endpoints)}")

public_allowed = (
    "/api/auth",
    "/api/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/favicon.ico"
)

unprotected = []
for ep in all_endpoints:
    p = ep["path"]
    if any(p.startswith(pref) for pref in public_allowed) or p == "/" or p == "/health":
        continue
    
    # Check if get_current_user or get_admin_user or get_operator_user in dependencies
    has_auth = any(d in ("get_current_user", "get_admin_user", "get_operator_user", "get_request_supabase") for d in ep["dependencies"])
    if not has_auth:
        unprotected.append(ep)

print(f"\nUnprotected Non-Public Endpoints Count: {len(unprotected)}")
for u in unprotected:
    print(f"  {u['methods']} {u['path']} -> {u['endpoint']} | deps: {u['dependencies']}")
