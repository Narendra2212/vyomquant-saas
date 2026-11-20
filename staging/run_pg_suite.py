"""Run every PostgreSQL/Redis-gated test file against the live staging infrastructure.

One place to re-run the whole gated set, so the launch report's evidence is reproducible
with a single command instead of a remembered pytest invocation. Sets both opt-ins the
repository defines -- AERORA_TEST_DATABASE_URL and AERORA_TEST_REDIS_URL -- which is what
turns these files from skips into real executions.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

GATED_FILES = [
    # Infrastructure-backed security and integrity
    "tests/test_tenant_isolation_rls_postgres.py",
    "tests/test_cross_tenant_read_sweep_postgres.py",
    "tests/test_schema_drift_checker_is_read_only_postgres.py",
    "tests/test_production_security_probe_rolls_back_postgres.py",
    "tests/test_bug049_identity_types_postgres.py",
    "tests/test_billing_data_isolation_postgres.py",
    "tests/test_tenant_referential_integrity_postgres.py",
    "tests/test_orm_models_match_provisioned_schema.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
    "tests/test_pgcrypto_preflight_postgres.py",
    "tests/test_signup_integration_postgres.py",
    "tests/test_position_lifecycle_postgres.py",
    "tests/test_referral_money_lifecycle_postgres.py",
    # The lane CI defines, plus the two gated files it does not select
    "tests/test_database_isolation_level_postgres.py",
    "tests/test_atomic_order_cancellation_fix.py",
    "tests/test_position_delta_race_condition_fix.py",
    "tests/test_strategy_lifecycle_concurrency_postgres.py",
    "tests/test_tenant_db_context_postgres.py",
    "tests/test_json_extract_dialect_portability.py",
]

#: CI deselects this against a documented design conflict: it asserts the general
#: SessionLocal reports `serializable`, which core/database.py deliberately scopes to
#: FINANCIAL sessions only. Kept deselected so the result is comparable to CI.
DESELECT = (
    "tests/test_atomic_order_cancellation_fix.py::test_transaction_isolation_for_cancellation"
)


def main() -> int:
    dsn_file = REPO / "staging" / "pg_dsn.txt"
    if not dsn_file.exists():
        # Explicit data directory: this suite needs the PROVISIONED cluster, and the bare
        # `start` form takes bootstrap_pg's `pgdata` default, which is a different (empty)
        # cluster on this machine. Phase 10 / Workstream I.
        sys.exit(
            "staging/pg_dsn.txt missing; start the provisioned server first:\n"
            "    python staging/bootstrap_pg.py start --data-dir pgdata_r1 "
            "--expect-provisioned"
        )
    dsn = dsn_file.read_text(encoding="utf-8").strip()
    if "6543" in dsn or not ("127.0.0.1" in dsn or "localhost" in dsn):
        sys.exit(f"refusing to run against a non-local DSN: {dsn!r}")

    env = dict(os.environ)
    env.update(
        DATABASE_URL=dsn,
        AERORA_TEST_DATABASE_URL=dsn,
        AERORA_TEST_REDIS_URL=os.environ.get(
            "AERORA_TEST_REDIS_URL", "redis://127.0.0.1:6379/0"
        ),
        ENV="test",
        VYOMQUANT_MODE="safe",
        PYTHONPATH=str(REPO),
        PYTHONIOENCODING="utf-8",
    )

    # ONE PYTEST PROCESS PER FILE, DELIBERATELY.
    #
    # Running all eleven in a single process died with 0xC0000409
    # (STATUS_STACK_BUFFER_OVERRUN) and wrote nothing at all, so every file's result was
    # lost to one file's crash. The likely culprit is `redis.asyncio`'s connection
    # finaliser running against an already-closed event loop -- it was already emitting
    # "RuntimeError: Event loop is closed" from `__del__` in earlier runs -- but the
    # cause matters less than the consequence: a single process makes the whole suite's
    # evidence hostage to the worst-behaved member of it.
    #
    # Per-file processes also mean the result is not sensitive to collection order, which
    # the main suite has a known problem with (test_task_6_1_backtest_execute.py passes
    # alone and fails in the full run).
    extra = [a for a in sys.argv[1:] if a]
    log_lines: list[str] = []
    results: list[tuple[str, int, str]] = []

    for path in GATED_FILES:
        cmd = [
            sys.executable, "-m", "pytest", path,
            "-q", "--tb=short", "-p", "no:cacheprovider", "-o", "addopts=",
            "--timeout=300", *extra,
        ]
        if DESELECT.startswith(path):
            cmd += ["--deselect", DESELECT]
        proc = subprocess.run(
            cmd, cwd=REPO, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        out = proc.stdout + proc.stderr
        log_lines.append("=" * 78)
        log_lines.append(f"{path}  (exit {proc.returncode})")
        log_lines.append("=" * 78)
        log_lines.append(out)

        summary = next(
            (
                line.strip()
                for line in reversed(out.splitlines())
                if (" passed" in line or " failed" in line or " error" in line
                    or " skipped" in line)
            ),
            "(no summary line — process produced no output)",
        )
        results.append((path, proc.returncode, summary))
        print(f"{'ok ' if proc.returncode == 0 else 'FAIL'} {path}")
        print(f"      {summary.encode('ascii', 'replace').decode('ascii')[:150]}")

    (REPO / "staging" / "pg_suite.log").write_text("\n".join(log_lines), encoding="utf-8")

    bad = [(p, rc) for p, rc, _s in results if rc != 0]
    print()
    print("-" * 78)
    print(f"{len(results) - len(bad)}/{len(results)} file(s) green")
    for p, rc in bad:
        print(f"  non-zero exit {rc}: {p}")
    print("log=staging/pg_suite.log")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
