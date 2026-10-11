"""TASK 8 / 1C -- does the real application + router graph import under each config?

1A found 14 modules importing `backend_app.core.rate_limit`, every one of them taking
only the `limiter` name. This imports the application and each of those routers for real,
under three configurations, and reports per-module outcomes rather than a single
aggregate -- so a failure names the module that failed.

Run in subprocesses, one per configuration, because import-time config is sticky.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

NONCE = sys.argv[1] if len(sys.argv) > 1 else uuid.uuid4().hex[:12]
print(f"NONCE={NONCE}")

REPO = Path(__file__).resolve().parent.parent
PY = str(REPO / ".venv" / "Scripts" / "python.exe")

MODULES = [
    "backend_app.main",
    "backend_app.routers.auth",
    "backend_app.routers.billing",
    "backend_app.routers.copilot",
    "backend_app.routers.dashboard",
    "backend_app.routers.exchange",
    "backend_app.routers.health",
    "backend_app.routers.library",
    "backend_app.routers.market",
    "backend_app.routers.paper_trading",
    "backend_app.routers.signal_trace",
    "backend_app.routers.strategies",
    "backend_app.routers.strategy_operations",
    "backend_app.routers.support",
]

CHILD = r'''
import importlib, json, sys, traceback
results = {}
for name in %(mods)s:
    try:
        importlib.import_module(name)
        results[name] = "OK"
    except BaseException as exc:
        results[name] = f"{type(exc).__name__}: {str(exc)[:110]}"
try:
    import backend_app.core.rate_limit as rl
    results["__storage__"] = rl.config.storage_uri
    results["__shared__"] = str(rl.config.is_shared)
except BaseException as exc:
    results["__storage__"] = f"{type(exc).__name__}"
    results["__shared__"] = "n/a"
print("RESULTJSON" + json.dumps(results))
''' % {"mods": json.dumps(MODULES)}


def run(label: str, overrides: dict) -> None:
    env = dict(os.environ)
    for key in ("ENV", "ENVIRONMENT", "DEV_MODE", "VYOMQUANT_MODE", "AERORA_MODE",
                "REDIS_URL"):
        env.pop(key, None)
    env.update({k: v for k, v in overrides.items() if v is not None})
    env["PYTHONPATH"] = str(REPO)

    proc = subprocess.run([PY, "-c", CHILD], cwd=str(REPO), env=env,
                          capture_output=True, text=True, timeout=600)
    payload = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("RESULTJSON"):
            payload = json.loads(line[len("RESULTJSON"):])
    print()
    print("=" * 92)
    print(f"CONFIG: {label}   (child rc={proc.returncode})")
    print("=" * 92)
    if payload is None:
        print("NO RESULT -- the child died before reporting. Tail:")
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
        for line in tail[-12:]:
            print("   ", line[:160])
        return
    print(f"limiter storage: {payload.pop('__storage__', '?')}  "
          f"shared={payload.pop('__shared__', '?')}")
    ok = [m for m, v in payload.items() if v == "OK"]
    bad = {m: v for m, v in payload.items() if v != "OK"}
    print(f"imported OK: {len(ok)}/{len(payload)}")
    for mod, err in bad.items():
        print(f"  FAILED {mod}: {err}")
    if not bad:
        print("  (every module imported)")


run("documented local: ENV=development, no REDIS_URL", {"ENV": "development"})
run("test-suite shape: ENV=testing, DEV_MODE=true, REDIS_URL=''",
    {"ENV": "testing", "DEV_MODE": "true", "REDIS_URL": ""})
run("workspace .env as-is (ENV absent, REDIS_URL unreachable)", {})

print()
print(f"NONCE_END={NONCE}")
