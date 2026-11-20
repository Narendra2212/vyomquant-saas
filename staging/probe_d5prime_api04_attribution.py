"""D5' attribution probe: is the api04 failure caused by this workstream?

The failing test is
``tests/test_api04_store_failure_after_startup.py::test_the_sensitive_endpoints_that_must_stay_rate_limited_are_decorated``.
It dies at IMPORT of `backend_app.core.database_pool` with

    RuntimeError: DATABASE_URL must be provided in paper/live mode. SQLite fallback disabled.

This reproduces that condition OUTSIDE pytest entirely -- no conftest, no test module, and
nothing authored by this workstream in the import path -- to establish attribution.

The test's own fixture does `monkeypatch.delenv("VYOMQUANT_MODE")`. `tests/conftest.py` sets
`VYOMQUANT_MODE=safe` precisely so the SQLite fallback is permitted; with it deleted,
`database_pool` sees paper/live mode, finds `DATABASE_URL` blank, and raises at module scope.
"""

from __future__ import annotations

import os
import subprocess
import sys

CODE = "import backend_app.core.database_pool; print('imported ok')"


def run(label: str, mutate) -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = ""          # what conftest leaves it as
    env["PYTHONPATH"] = "."
    mutate(env)
    r = subprocess.run(
        [sys.executable, "-c", CODE],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    print(f"\n{label}")
    print(f"  exit={r.returncode}  stdout={(r.stdout or '').strip()[:40]!r}")
    for line in (r.stderr or "").splitlines():
        if "RuntimeError" in line or "database_pool.py" in line:
            print(f"    {line.strip()[:120]}")


def main():
    print("=" * 78)
    print("D5' ATTRIBUTION: the api04 failure, reproduced with no test code involved")
    print("=" * 78)

    run(
        "A. VYOMQUANT_MODE DELETED (what the api04 fixture does)",
        lambda e: (e.pop("VYOMQUANT_MODE", None), e.pop("AERORA_MODE", None)),
    )
    run(
        "B. VYOMQUANT_MODE=safe (what tests/conftest.py sets)",
        lambda e: e.__setitem__("VYOMQUANT_MODE", "safe"),
    )

    print("\n" + "=" * 78)
    print("CONCLUSION")
    print("=" * 78)
    print("  If A raises and B imports cleanly, the failure is a pre-existing interaction")
    print("  between committed application code (`backend_app/core/database_pool.py`, which")
    print("  `git status` reports CLEAN) and that test's own deletion of VYOMQUANT_MODE.")
    print("  Nothing authored by D5' is in this import path: `tests/real_redis_gate.py` is")
    print("  imported only by the two quota files, and the CI YAML is not read by pytest.")


if __name__ == "__main__":
    main()
