"""PHASE 11B-A2 — why does `database_pool` raise locally? Read-only triage.

`tests/test_api04_store_failure_after_startup.py::
test_the_sensitive_endpoints_that_must_stay_rate_limited_are_decorated` fails at IMPORT
time with::

    backend_app/core/database_pool.py:141
    RuntimeError: DATABASE_URL must be provided in paper/live mode. SQLite fallback disabled.

The raise is guarded by::

    _env_mode = get_vyomquant_mode("safe").lower()
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        if _env_mode in ["paper", "live"]:
            raise RuntimeError(...)
        DATABASE_URL = "sqlite:///./algo22.db"

`tests/conftest.py` sets `VYOMQUANT_MODE=safe` and blanks `DATABASE_URL`, so the raise
should not fire. Exporting `AERORA_MODE=safe` and `VYOMQUANT_MODE=safe` in the shell does
not change the outcome either. This probe asks the resolver directly what it returns and
from where, instead of inferring. Nothing is written and no database is contacted.
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("=" * 78)
print("PHASE 11B-A2 - API04 import failure: where does the mode come from?")
print("=" * 78)

for var in ("AERORA_MODE", "VYOMQUANT_MODE", "ENV", "DATABASE_URL"):
    raw = os.environ.get(var)
    shown = "(unset)" if raw is None else (repr(raw) if var != "DATABASE_URL"
                                           else f"<set, {len(raw)} chars>" if raw else "''")
    print(f"  process env {var:<18} = {shown}")

print("-" * 78)
print("resolver, BEFORE importing tests/conftest.py:")
try:
    from backend_app.core.safety_config import get_vyomquant_mode

    print(f"  get_vyomquant_mode('safe') = {get_vyomquant_mode('safe')!r}")
except Exception as e:  # noqa: BLE001
    print(f"  import/resolve failed: {type(e).__name__}: {e}")

print("-" * 78)
print("does a .env file participate?")
dotenv = ROOT / ".env"
print(f"  .env exists: {dotenv.exists()}")
if dotenv.exists():
    keys = []
    for line in dotenv.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if any(tok in key.upper() for tok in ("MODE", "TRADING", "LIVE", "PAPER")):
            # Mode flags are not secrets; values are printed for these keys only.
            keys.append(line)
    print(f"  mode-ish keys in .env: {keys or 'none'}")

print("-" * 78)
print("what does safety_config read, and in what order?")
try:
    import inspect

    from backend_app.core import safety_config

    src = inspect.getsource(safety_config.get_vyomquant_mode)
    for ln in src.splitlines():
        print("   " + ln)
except Exception as e:  # noqa: BLE001
    print(f"  could not read source: {type(e).__name__}: {e}")

print("=" * 78)
