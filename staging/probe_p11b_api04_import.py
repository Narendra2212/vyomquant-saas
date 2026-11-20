"""PHASE 11B-A2 — reproduce the API04 import raise with conftest's exact env preamble.

Replicates what `tests/conftest.py` sets before any test module loads, then imports
`backend_app.core.database_pool` and reports the two values its module-level guard reads.
Read-only: no database is contacted (the raise happens before any connection attempt) and
nothing is written.
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("=" * 78)
print("PHASE 11B-A2 - API04 import raise, under conftest's env preamble")
print("=" * 78)

# Exactly what tests/conftest.py does at import (lines 13, 26, 41).
os.environ["REDIS_URL"] = ""
os.environ["VYOMQUANT_MODE"] = "safe"
os.environ["DATABASE_URL"] = ""

print("after conftest preamble:")
for var in ("VYOMQUANT_MODE", "AERORA_MODE", "DATABASE_URL", "ENV"):
    raw = os.environ.get(var)
    print(f"   {var:<16} = {'(unset)' if raw is None else repr(raw)}")

from backend_app.core.safety_config import get_vyomquant_mode  # noqa: E402

mode = get_vyomquant_mode("safe")
print(f"   get_vyomquant_mode('safe') -> {mode!r}  (guard fires when in ['paper','live'])")
print(f"   bool(DATABASE_URL)         -> {bool(os.environ.get('DATABASE_URL'))}")

print("-" * 78)
print("importing backend_app.core.database_pool ...")
try:
    import backend_app.core.database_pool as dp

    print(f"   IMPORTED OK. DATABASE_URL resolved to: "
          f"{'sqlite (fallback)' if str(dp.DATABASE_URL).startswith('sqlite') else '<non-sqlite>'}")
    verdict = "no raise under conftest's preamble"
except RuntimeError as e:
    print(f"   RuntimeError: {e}")
    verdict = "RAISED under conftest's preamble"
except Exception as e:  # noqa: BLE001
    print(f"   {type(e).__name__}: {e}")
    verdict = f"other failure: {type(e).__name__}"

print("-" * 78)
print("checking whether anything else mutates the mode at import:")
for name in ("backend_app.core.config", "backend_app.core.safety_config",
             "backend_app.core.database"):
    print(f"   {name}: {'loaded' if name in sys.modules else 'not loaded'}")
print(f"   VYOMQUANT_MODE now = {os.environ.get('VYOMQUANT_MODE')!r}")
print(f"   AERORA_MODE now    = {os.environ.get('AERORA_MODE')!r}")
print(f"   DATABASE_URL now   = {os.environ.get('DATABASE_URL')!r}")

print("=" * 78)
print(f"VERDICT: {verdict}")
