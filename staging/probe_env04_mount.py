"""ENV-04 probe: does the production route table actually change with the flag?

Boots `backend_app.main` in THIS process under a given PRODUCTION_ROUTER_ENABLED and prints
whether `/api/execution/signal` is mounted. Run once per setting from a wrapper, because
the flag is consumed at import time.

Usage:  python staging/probe_env04_mount.py <unset|false|true>
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

setting = (sys.argv[1] if len(sys.argv) > 1 else "unset").strip().lower()

os.environ.pop("PRODUCTION_ROUTER_ENABLED", None)
if setting != "unset":
    os.environ["PRODUCTION_ROUTER_ENABLED"] = setting

# ENV=production CANNOT be booted here, and the reason is a control working correctly:
# `core/rate_limit` refuses to start outside an explicitly local environment when it has no
# shared store, because in-memory rate limiting is per-process and would multiply the
# effective limit by the replica count (API-03). Measured:
#
#   IMPORT_FAILED=RuntimeError: FATAL: rate limiting has no shared store. REDIS_URL is set
#   but unreachable (Timeout connecting to server). (ENV=production, REDIS_URL=set).
#
# The mount decision does not read ENV at all — `production_router_enabled()` consults only
# PRODUCTION_ROUTER_ENABLED, and the nine-cell predicate matrix in
# tests/test_env04_production_router_requires_explicit_enablement.py proves that across all
# three environments. So this probe boots a local environment to measure the real route
# table, and the environment-independence is established separately rather than assumed.
os.environ["ENV"] = os.environ.get("AERORA_PROBE_ENV", "development")
os.environ["DEV_MODE"] = os.environ.get("AERORA_PROBE_DEV_MODE", "true")
os.environ["VYOMQUANT_MODE"] = "safe"

print(f"SETTING={setting}")
started = time.time()
try:
    from backend_app.main import app
except BaseException as exc:  # noqa: BLE001 - the point is to see what it is
    print(f"IMPORT_FAILED={type(exc).__name__}: {exc}")
    print(f"ELAPSED={time.time() - started:.1f}")
    raise SystemExit(2)

paths = sorted({r.path for r in app.routes if hasattr(r, "path")})
print(f"ELAPSED={time.time() - started:.1f}")
print(f"ROUTE_COUNT={len(paths)}")
print(f"EXECUTION_SIGNAL_MOUNTED={'/api/execution/signal' in paths}")
print(f"ANY_EXECUTION_PATH={[p for p in paths if p.startswith('/api/execution')]}")

from backend_app.core import feature_flags, safety_config

print(f"PREDICATE={safety_config.production_router_enabled()}")
print(f"SAFETY_FLAG={safety_config.ExecutionFlags.PRODUCTION_ROUTER_ENABLED}")
print(f"FEATURE_FLAG={feature_flags.ExecutionFlags.PRODUCTION_ROUTER_ENABLED}")
