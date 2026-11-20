"""Run the repository's PostgreSQL-gated test lane against the staging server.

PowerShell's `>` redirection writes UTF-16 and multi-line `execute_pwsh` blocks have
silently dropped the command tail in this environment, so the invocation lives here
instead: env vars are set in-process, output is captured as UTF-8, and the summary is
printed to stdout as plain ASCII.

The file list is the one `.github/workflows/01-pr-check.yml`'s `database-tests` job runs,
plus the two gated files that job does not select (`test_tenant_db_context_postgres.py`,
`test_json_extract_dialect_portability.py`). Both halves matter: the first is the lane as
CI defines it, the second is coverage that exists in the tree and has never executed.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

CI_LANE = [
    "tests/test_database_isolation_level_postgres.py",
    "tests/test_atomic_order_cancellation_fix.py",
    "tests/test_position_delta_race_condition_fix.py",
    "tests/test_strategy_lifecycle_concurrency_postgres.py",
]

# Gated on AERORA_TEST_DATABASE_URL but absent from the CI job's selection.
NOT_IN_CI = [
    "tests/test_tenant_db_context_postgres.py",
    "tests/test_json_extract_dialect_portability.py",
]

# CI deselects this one against a documented design conflict (it asserts the general
# SessionLocal reports `serializable`, which core/database.py deliberately does not do).
# Kept deselected here so the result is comparable to CI, then run separately by
# `--include-deselected` so its real state is recorded rather than hidden.
CI_DESELECT = (
    "tests/test_atomic_order_cancellation_fix.py::test_transaction_isolation_for_cancellation"
)


def dsn() -> str:
    override = os.environ.get("AERORA_TEST_DATABASE_URL")
    if override:
        return override
    path = REPO / "staging" / "pg_dsn.txt"
    if not path.exists():
        # Names the data directory explicitly: this lane needs the PROVISIONED cluster,
        # and the bare `start` form takes bootstrap_pg's `pgdata` default, which is a
        # different (empty) cluster on this machine. Phase 10 / Workstream I.
        sys.exit(
            "staging/pg_dsn.txt missing; start the provisioned server first:\n"
            "    python staging/bootstrap_pg.py start --data-dir pgdata_r1 "
            "--expect-provisioned"
        )
    return path.read_text(encoding="utf-8").strip()


def main() -> int:
    url = dsn()
    if not url.startswith("postgresql"):
        sys.exit(f"refusing to run: DSN is not PostgreSQL: {url!r}")
    if "6543" in url or not ("127.0.0.1" in url or "localhost" in url):
        sys.exit(f"refusing to run against a non-local DSN: {url!r}")

    args = sys.argv[1:]
    include_deselected = "--include-deselected" in args
    files = list(CI_LANE) + ([] if "--ci-only" in args else NOT_IN_CI)
    log_name = "pg_lane_deselected.log" if include_deselected else "pg_lane_run.log"

    env = dict(os.environ)
    env["DATABASE_URL"] = url
    env["AERORA_TEST_DATABASE_URL"] = url
    env["ENV"] = "test"
    env["VYOMQUANT_MODE"] = "safe"
    env["PYTHONPATH"] = str(REPO)
    env["PYTHONIOENCODING"] = "utf-8"

    if include_deselected:
        cmd = [sys.executable, "-m", "pytest", CI_DESELECT]
    else:
        cmd = [sys.executable, "-m", "pytest", *files, "--deselect", CI_DESELECT]
    cmd += ["-v", "--tb=short", "-p", "no:cacheprovider", "-o", "addopts=", "--timeout=180"]

    proc = subprocess.run(
        cmd, cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    log = REPO / "staging" / log_name
    log.write_text(out, encoding="utf-8")

    interesting = [
        line for line in out.splitlines()
        if any(k in line for k in ("PASSED", "FAILED", "ERROR", "SKIPPED", "XFAIL", "XPASS"))
        or line.startswith(("E ", "=", "_", "tests/"))
    ]
    for line in interesting[-80:]:
        print(line.encode("ascii", "replace").decode("ascii"))
    print(f"EXIT={proc.returncode}  LOG={log}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
