"""PHASE 11B-A2 -- what does the mode resolver actually return at the moment of the raise?

The previous hypothesis (a paper/live mode declared in the local `.env`) does not survive
inspection: `.env` carries no VYOMQUANT_MODE or AERORA_MODE key. So the resolver is asked
directly, under the exact environment the failing test creates.

Read-only. Prints the resolved MODE, which is not a secret, and no other value.
"""

from __future__ import annotations

import os
import subprocess
import sys

CHILD = r'''
import os, sys
sys.path.insert(0, r"{repo}")

# Reproduce the test's environment precisely:
#   tests/conftest.py blanks DATABASE_URL, then the module's autouse `clean_env`
#   fixture deletes the mode variables.
import tests.conftest            # noqa: F401  -- sets ENV/DEV_MODE/VYOMQUANT_MODE/DATABASE_URL
print("after conftest: VYOMQUANT_MODE=%r DATABASE_URL=%r"
      % (os.environ.get("VYOMQUANT_MODE"), os.environ.get("DATABASE_URL")))

for var in ("ENV", "ENVIRONMENT", "DEV_MODE", "VYOMQUANT_MODE", "AERORA_MODE", "REDIS_URL"):
    os.environ.pop(var, None)
print("after clean_env fixture: VYOMQUANT_MODE=%r DATABASE_URL=%r"
      % (os.environ.get("VYOMQUANT_MODE"), os.environ.get("DATABASE_URL")))

from backend_app.core.safety_config import get_vyomquant_mode
mode = get_vyomquant_mode("safe")
print("get_vyomquant_mode('safe') ->", repr(mode))
print("would raise (mode in paper/live and no DATABASE_URL):",
      mode.lower() in ("paper", "live") and not os.environ.get("DATABASE_URL"))

try:
    import backend_app.core.database_pool  # noqa: F401
    print("database_pool import: OK")
except RuntimeError as exc:
    print("database_pool import: RuntimeError:", exc)
'''


def main():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print("=" * 78)
    print("API04: the mode resolver, under the failing test's exact environment")
    print("=" * 78)
    r = subprocess.run(
        [sys.executable, "-c", CHILD.format(repo=repo)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=repo,
    )
    for line in (r.stdout or "").splitlines():
        print(f"  {line}")
    if r.returncode != 0:
        print("\n  stderr tail:")
        for line in (r.stderr or "").strip().splitlines()[-6:]:
            print(f"    {line}")


if __name__ == "__main__":
    main()
