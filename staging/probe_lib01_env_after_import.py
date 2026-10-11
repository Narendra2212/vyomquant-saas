"""Which branch of library._get_service_client actually runs, measured after the import.

The first probe printed the environment BEFORE importing the router, so it could not see
whatever the import itself puts there. This one prints it on both sides and then reports,
for each DEV_MODE setting, the exact exception type -- so the branch is identified rather
than inferred.
"""
from __future__ import annotations

import inspect
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WATCH = ("ENV", "DEV_MODE", "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY",
         "SUPABASE_SERVICE_ROLE_KEY")


def dump(label: str) -> None:
    print(f"{label}:")
    for key in WATCH:
        raw = os.environ.get(key)
        if raw is None:
            shown = "<unset>"
        elif "KEY" in key or "SECRET" in key:
            shown = f"<set len={len(raw)}>"
        else:
            shown = raw
        print(f"  {key:<28} {shown}")


os.environ["ENV"] = "testing"
os.environ["DEV_MODE"] = "true"
os.environ["VYOMQUANT_MODE"] = "safe"
os.environ["REDIS_URL"] = ""
os.environ["DATABASE_URL"] = ""
os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)

dump("BEFORE IMPORT")

import importlib  # noqa: E402

lib = importlib.import_module("backend_app.routers.library")

dump("AFTER IMPORT")

print("\nsource of library._get_service_client:")
for i, line in enumerate(inspect.getsource(lib._get_service_client).splitlines(), 1):
    print(f"  {i:>2} {line}")

print("\nbranch taken, per DEV_MODE:")
for dev in ("true", "false"):
    os.environ["DEV_MODE"] = dev
    lib._service_client = None
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    missing = (not url) or (not key)
    try:
        value = lib._get_service_client()
    except Exception as exc:  # noqa: BLE001
        print(
            f"  DEV_MODE={dev:<5} url_or_key_missing={missing!s:<5} "
            f"-> RAISED {type(exc).__module__}.{type(exc).__name__}: {str(exc)[:70]}"
        )
    else:
        print(
            f"  DEV_MODE={dev:<5} url_or_key_missing={missing!s:<5} "
            f"-> RETURNED {value!r}"
        )
