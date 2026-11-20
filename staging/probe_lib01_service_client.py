"""LIB-01 disposition evidence: why `library.clone_strategy` answers 500.

The sweep's failure message says only "answered 500". The captured log says
`clone_strategy library lookup error: 'NoneType' object has no attribute 'table'`.
That is not a network failure and not a malformed body -- it is a `None` client returned by
a function whose docstring says it "raises 500 if env is not configured".

Measured here, with no app import and no network:

  1. `_get_service_client()` under the exact environment `tests/conftest.py` establishes.
  2. The same call with `DEV_MODE` absent -- the branch the `None` comes from.
  3. Whether `_build_service_client()` (documented to raise) can return `None`.
  4. The same question for `routers/referral.py`, which copies the pattern.

Run:  .venv\\Scripts\\python.exe staging\\probe_lib01_service_client.py
"""
from __future__ import annotations

import importlib
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The conftest-equivalent environment, set BEFORE any backend import.
os.environ["ENV"] = "testing"
os.environ["DEV_MODE"] = "true"
os.environ["VYOMQUANT_MODE"] = "safe"
os.environ["REDIS_URL"] = ""
os.environ["DATABASE_URL"] = ""
os.environ["JWT_SECRET"] = "dev-secret-change-in-production"
os.environ["SUPABASE_JWT_SECRET"] = "dev-secret-change-in-production"
os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)


def describe(label: str, fn) -> None:
    try:
        value = fn()
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:<58} RAISED {type(exc).__name__}: {str(exc)[:90]}")
    else:
        print(f"  {label:<58} RETURNED {value!r}")


def main() -> int:
    print("ENV:")
    for key in ("ENV", "DEV_MODE", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        raw = os.environ.get(key)
        shown = "<unset>" if raw is None else (f"<set len={len(raw)}>" if "KEY" in key else raw)
        print(f"  {key:<28} {shown}")

    lib = importlib.import_module("backend_app.routers.library")
    ref = importlib.import_module("backend_app.routers.referral")

    print("\n1/2. library._get_service_client() -- DEV_MODE decides, ENV is never consulted:")
    describe("DEV_MODE=true,  no SUPABASE_SERVICE_ROLE_KEY", lib._get_service_client)

    os.environ["DEV_MODE"] = "false"
    lib._service_client = None
    describe("DEV_MODE=false, no SUPABASE_SERVICE_ROLE_KEY", lib._get_service_client)

    # The combination RUNTIME-01 was about: a declared production environment that a
    # secondary flag downgrades. Nothing here consults ENV at all.
    os.environ["ENV"] = "production"
    os.environ["DEV_MODE"] = "true"
    lib._service_client = None
    describe("ENV=production + DEV_MODE=true", lib._get_service_client)

    print("\n3. library._build_service_client() -- documented to raise 500:")
    lib._service_client = None
    describe("ENV=production + DEV_MODE=true", lib._build_service_client)

    print("\n4. referral._get_service_client() -- same pattern, measured not assumed:")
    if hasattr(ref, "_service_client"):
        ref._service_client = None
    describe("ENV=production + DEV_MODE=true", ref._get_service_client)

    print("\nVERDICT:")
    lib._service_client = None
    os.environ["ENV"] = "production"
    os.environ["DEV_MODE"] = "true"
    built = None
    raised = None
    try:
        built = lib._build_service_client()
    except Exception as exc:  # noqa: BLE001
        raised = exc
    print(f"  LIB01_BUILD_RETURNS_NONE_UNDER_DEV_MODE={built is None and raised is None}")
    print(f"  LIB01_DEV_MODE_OVERRIDES_ENV_PRODUCTION={built is None and raised is None}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
