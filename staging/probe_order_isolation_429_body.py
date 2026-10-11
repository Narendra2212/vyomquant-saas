"""What exactly produces the 429, now that resetting app.state.limiter did not stop it?

The reset hypothesis was half right: the limiter object IS swapped by a re-import
(confirmed, 6/6, in probe_order_isolation_429.py). But resetting both the app-bound and
the module-current limiter before every test did NOT clear the 8 failures, so count
exhaustion is not the whole story -- with a 10/minute limit and a reset before each test,
three requests cannot exhaust anything.

So this probe stops inferring and reads the actual response: status, body and headers,
before and after the re-import, against the real app.
"""
from __future__ import annotations

import importlib
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["ENV"] = "testing"
os.environ["DEV_MODE"] = "true"
os.environ["REDIS_URL"] = ""
os.environ["DATABASE_URL"] = ""
os.environ["VYOMQUANT_MODE"] = "safe"
os.environ["DEFAULT_EXCHANGE"] = "binance"

from fastapi.testclient import TestClient  # noqa: E402

import backend_app.core.rate_limit as rl  # noqa: E402
from backend_app.main import app  # noqa: E402
from backend_app.core.dependencies import (  # noqa: E402
    get_current_user, get_request_supabase)

OWNER = {"id": "11111111-1111-1111-1111-111111111111", "email": "o@x.io",
         "access_token": "tok"}
STRATEGY_ID = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
PATH = f"/api/strategy-operations/strategies/{STRATEGY_ID}/backtests/execute"
BODY = {
    "start_date": "2024-01-01", "end_date": "2024-03-01",
    "initial_capital": 54_321.0, "commission": 0.0025, "slippage": 0.0031,
    "spread": 0.0007, "risk_per_trade": 0.02, "max_drawdown": 0.33,
}


def hammer(label, times):
    """How many requests does it take to get a 429, with no reset in between?"""
    app.dependency_overrides[get_current_user] = lambda: OWNER
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        client = TestClient(app, raise_server_exceptions=False)
        statuses = []
        for _ in range(times):
            statuses.append(client.post(PATH, json=BODY).status_code)
        first_429 = (
            statuses.index(429) + 1 if 429 in statuses else None
        )
        print(f"\n--- {label}: {times} requests, no reset ---")
        print(f"  statuses = {statuses}")
        print(f"  first 429 at request #{first_429}")
        return first_429
    finally:
        app.dependency_overrides.clear()


def probe(label):
    app.dependency_overrides[get_current_user] = lambda: OWNER
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(PATH, json=BODY)
        print(f"\n--- {label} ---")
        print(f"  status = {response.status_code}")
        interesting = {
            key: value for key, value in response.headers.items()
            if "limit" in key.lower() or "retry" in key.lower()
        }
        print(f"  rate headers = {interesting}")
        text = response.text
        print(f"  body[:300] = {text[:300]!r}")
        return response.status_code
    finally:
        app.dependency_overrides.clear()


print(f"module limiter id = {id(rl.limiter):#x}")
print(f"app.state.limiter id = {id(app.state.limiter):#x}")
print(f"same object = {rl.limiter is app.state.limiter}")

before = probe("BEFORE any re-import")

# Reproduce exactly what the three polluter files do.
sys.modules.pop("backend_app.core.rate_limit", None)
reimported = importlib.import_module("backend_app.core.rate_limit")
print(f"\nafter re-import: module limiter id = {id(reimported.limiter):#x}")
print(f"app.state.limiter id = {id(app.state.limiter):#x}")
print(f"same object = {reimported.limiter is app.state.limiter}")

# Reset BOTH, the way the new conftest fixture does.
for name, limiter in (("app-bound", app.state.limiter),
                      ("module-current", reimported.limiter)):
    try:
        limiter.reset()
        print(f"  reset {name}: ok")
    except Exception as exc:
        print(f"  reset {name}: RAISED {type(exc).__name__}: {exc}")

after = probe("AFTER re-import + reset of both limiters")

# Does a reset actually clear a count? Hammer until 429, reset, hammer again.
limit_at = hammer("accumulation without reset", 14)
for limiter in (app.state.limiter, reimported.limiter):
    try:
        limiter.reset()
    except Exception:
        pass
after_reset = probe("one request immediately after resetting both")

print()
print("=" * 74)
print(f"before re-import      : {before}")
print(f"after re-import+reset : {after}")
print(f"first 429 when hammering: request #{limit_at}")
print(f"single request after a reset: {after_reset}")
if after_reset == 429 and limit_at is not None:
    print("\nRESET DOES NOT CLEAR THE COUNT the app is enforcing on.")
elif after_reset != 429 and limit_at is not None:
    print("\nRESET DOES CLEAR IT -- so the suite's 429s come from a path that does not "
          "reach the reset, not from the reset being aimed at the wrong object.")
print("=" * 74)
