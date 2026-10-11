"""Why 8 tests in test_task_6_1_backtest_execute.py answer 429 only inside the full suite.

THE OBSERVATION
---------------
In the full-suite ordering all 8 failures are an HTTP **429** where 409 / 200 / 404 / 422 /
500 was expected (two of them surface as `KeyError: 'backtest_id'` / `'status'` because the
429 body carries neither). Run alone the file is 33 passed, and 33 passed at HEAD too.

The file already defends itself:

    @pytest.fixture(autouse=True)
    def clear_rate_limit():
        from backend_app.core.rate_limit import limiter
        limiter.reset()

autouse and function-scoped, so it runs before every test. For the 429 to survive it, the
object that fixture resets must not be the object the app's routes are counting on.

THE HYPOTHESIS
--------------
`tests/test_rate_limiter_fails_closed_in_production.py::_reload` does

    sys.modules.pop("backend_app.core.rate_limit", None)
    importlib.import_module("backend_app.core.rate_limit")

and `tests/test_rate_limit_backend.py` does `importlib.reload(rate_limit_module)`. Either
REBINDS `rate_limit.limiter` to a brand-new `Limiter`. But the routers were decorated with
`@limiter.limit(...)` at THEIR import time, so every registered route still closes over the
ORIGINAL object. From then on:

  * the app counts requests on limiter_A;
  * `from backend_app.core.rate_limit import limiter` yields limiter_B;
  * `clear_rate_limit` resets limiter_B, so limiter_A's counters never clear.

Ordering fits: the two rate-limit files run at 57%, `test_task_6_1` at 80%.

This script tests that mechanically, without running the suite.
"""
from __future__ import annotations

import importlib
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
# The suite's own environment, so the limiter resolves to memory storage exactly as it
# does under pytest (tests/conftest.py sets these at import).
os.environ["ENV"] = "testing"
os.environ["DEV_MODE"] = "true"
os.environ.pop("REDIS_URL", None)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")


import backend_app.core.rate_limit as rl  # noqa: E402

limiter_a = rl.limiter
print(f"original limiter id={id(limiter_a):#x}")

# The app, imported the way the test suite imports it. Its routes are decorated now.
from backend_app.main import app  # noqa: E402


def app_limiter():
    """The limiter object FastAPI actually holds, read off app.state."""
    return getattr(app.state, "limiter", None)


bound = app_limiter()
check(
    "1. the app holds the limiter the routers were decorated with",
    bound is limiter_a,
    f"app.state.limiter id={id(bound):#x} original id={id(limiter_a):#x}",
)

# ── now reproduce what the two rate-limit test files do ───────────────────
sys.modules.pop("backend_app.core.rate_limit", None)
rl_reimported = importlib.import_module("backend_app.core.rate_limit")
limiter_b = rl_reimported.limiter

check(
    "2. a sys.modules.pop + re-import produces a DIFFERENT limiter object",
    limiter_b is not limiter_a,
    f"re-imported id={id(limiter_b):#x} original id={id(limiter_a):#x}",
)

check(
    "3. the app STILL holds the original object after the re-import",
    app_limiter() is limiter_a,
    f"app.state.limiter id={id(app_limiter()):#x}",
)

check(
    "4. so `from ...rate_limit import limiter` now yields the WRONG object for a reset",
    limiter_b is not app_limiter(),
    "which is exactly what clear_rate_limit() imports and resets",
)

# ── and importlib.reload does the same ───────────────────────────────────
importlib.reload(rl_reimported)
limiter_c = rl_reimported.limiter
check(
    "5. importlib.reload also rebinds it",
    limiter_c is not limiter_b,
    f"after reload id={id(limiter_c):#x} before id={id(limiter_b):#x}",
)

# ── the consequence: resetting B does not clear A ────────────────────────
storage_a = getattr(getattr(limiter_a, "_storage", None), "storage", None)
storage_b = getattr(getattr(limiter_b, "_storage", None), "storage", None)
check(
    "6. the two limiters have separate storage",
    storage_a is not storage_b,
    f"A storage id={id(storage_a):#x} B storage id={id(storage_b):#x} "
    f"(types {type(storage_a).__name__}/{type(storage_b).__name__})",
)

print()
print("CONSEQUENCE FOR THE 8 FAILURES")
print("=" * 74)
print("  The app counts every TestClient request on limiter A for the whole session.")
print("  After an earlier file re-imports or reloads core.rate_limit, the autouse")
print("  clear_rate_limit() fixture resets limiter B and leaves A untouched, so A's")
print("  per-minute windows accumulate across thousands of requests and the routes")
print("  start answering 429.")
print()
failed = sum(1 for _, ok, _ in results if not ok)
print(f"VERDICT={'HYPOTHESIS_CONFIRMED' if failed == 0 else f'{failed}_CHECKS_FAILED'}")
