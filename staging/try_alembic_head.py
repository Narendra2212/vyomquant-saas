"""Attempt `alembic upgrade head` against the staging database and record the outcome.

WHY THIS IS WORTH RUNNING. `.github/workflows/01-pr-check.yml` stops provisioning at the
branchpoint `d97ffff9c3bb` and states that `upgrade head` CANNOT provision a fresh
database: the next revision on one branch, `add_foreign_keys`, adds
`FOREIGN KEY (tenant_id) REFERENCES profiles(id)` and `profiles` has no CREATE TABLE in
any migration. That measurement was taken against a BARE postgres.

`scripts/provision_database.py` changes the precondition. Its step 0 applies
`020_declare_pre_existing_tables.sql`, which declares `profiles`, BEFORE alembic runs. So
the stated cause of the failure is absent here, and whether head is reachable is an open
empirical question rather than a settled one.

It matters because `implement_rls_policies` sits downstream of `add_foreign_keys` on the
path to head. If head is unreachable then the nine tenant-isolation policies that
migration declares -- on orders, positions, fills, dag_tasks, execution_records,
idempotency_keys, transaction_records, transaction_checkpoints and
reconciliation_mismatches -- are never installed on any database built by the only
provisioning path that works, which is a different and more serious condition than the
"policies exist but their GUC is never set" already on record.

Runs one revision at a time so the exact failing revision and statement are captured
rather than a single opaque failure at the end.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def dsn() -> str:
    return (REPO / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()


def run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def main() -> int:
    url = dsn()
    if "6543" in url or not ("127.0.0.1" in url or "localhost" in url):
        sys.exit(f"refusing to touch a non-local database: {url!r}")

    env = dict(os.environ)
    env["DATABASE_URL"] = url
    env["PYTHONPATH"] = str(REPO)
    env["PYTHONIOENCODING"] = "utf-8"
    env["ENV"] = "test"
    env["VYOMQUANT_MODE"] = "safe"

    log: list[str] = []

    def emit(s: str = "") -> None:
        log.append(s)
        print(s.encode("ascii", "replace").decode("ascii"))

    cur = run(["current"], env)
    emit("=" * 78)
    emit("ALEMBIC UPGRADE HEAD -- reachability test against staging PostgreSQL")
    emit("=" * 78)
    emit("BEFORE (alembic current):")
    emit(cur.stdout.strip() or "(empty)")
    if cur.stderr.strip():
        emit("stderr: " + cur.stderr.strip())
    emit()

    # The two branches off d97ffff9c3bb, in dependency order, then the mergepoint.
    sequence = [
        "add_foreign_keys",
        "implement_rls_policies",
        "e88f9911b5a2",
        "6f1b3d9c8a7e",
        "merge_heads",
    ]

    failed_at: str | None = None
    for rev in sequence:
        emit("-" * 78)
        emit(f"upgrade -> {rev}")
        proc = run(["upgrade", rev], env)
        tail_out = proc.stdout.strip().splitlines()
        tail_err = proc.stderr.strip().splitlines()
        for line in tail_out[-25:]:
            emit("  out| " + line)
        for line in tail_err[-40:]:
            emit("  err| " + line)
        emit(f"  rc={proc.returncode}")
        if proc.returncode != 0:
            failed_at = rev
            break

    emit()
    emit("-" * 78)
    after = run(["current"], env)
    emit("AFTER (alembic current):")
    emit(after.stdout.strip() or "(empty)")
    emit()
    if failed_at:
        emit(f"RESULT: head NOT reachable; first failure at revision {failed_at!r}")
    else:
        emit("RESULT: head reachable; every revision applied")

    (REPO / "staging" / "alembic_head_attempt.log").write_text(
        "\n".join(log), encoding="utf-8"
    )
    return 1 if failed_at else 0


if __name__ == "__main__":
    sys.exit(main())
