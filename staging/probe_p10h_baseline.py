"""PHASE 10 WORKSTREAM H — Stage 0 baseline. Read-only.

Records, without acting on anything:
  * which cluster the shared DSN currently points at, and whether it is reachable;
  * the schema markers present in it;
  * the on-disk state of BOTH candidate data directories, including whether each has a
    LIVE or a STALE postmaster.

It deliberately does NOT start, stop or connect to `staging/pgdata`. Establishing that a
cluster is not running is done from its postmaster.pid and the process table; starting it
to look inside would create a second live postmaster, which is the opposite of this
workstream's purpose.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
STAGING = ROOT / "staging"
DSN_FILE = STAGING / "pg_dsn.txt"

# The markers `staging/probe_pg_find.py` already uses to recognise a provisioned instance,
# plus the tables the Phase 9/10 suites depend on.
MARKERS = (
    "profiles", "execution_records", "fee_records", "referral_payouts",
    "referral_obligations", "safety_control_audit", "alembic_version",
    "orders", "positions",
)


def _live_pids() -> set[int]:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process -Id (Get-Process | Select-Object -ExpandProperty Id) "
             "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"],
            capture_output=True, text=True, timeout=60,
        ).stdout
        return {int(x) for x in out.split() if x.strip().isdigit()}
    except Exception:  # noqa: BLE001
        return set()


print("=" * 78)
print("PHASE 10 / WS-H / STAGE 0 — PostgreSQL test-environment baseline (read-only)")
print("=" * 78)

# ── 1. The shared DSN ────────────────────────────────────────────────────────
print("1. SHARED DSN FILE")
if DSN_FILE.exists():
    dsn = DSN_FILE.read_text(encoding="utf-8").strip()
    print(f"   staging/pg_dsn.txt = {dsn}")
    print(f"   mtime              = {DSN_FILE.stat().st_mtime}")
else:
    dsn = ""
    print("   staging/pg_dsn.txt ABSENT")

# ── 2. Is it reachable, and is it provisioned? ───────────────────────────────
print("-" * 78)
print("2. IS THE CURRENT TARGET REACHABLE AND PROVISIONED?")
if dsn:
    try:
        import psycopg2

        c = psycopg2.connect(dsn, connect_timeout=8)
        cur = c.cursor()
        cur.execute("select count(*) from information_schema.tables "
                    "where table_schema='public'")
        ntables = cur.fetchone()[0]
        cur.execute("select current_setting('data_directory')")
        datadir = cur.fetchone()[0]
        cur.execute("select pg_is_in_recovery()")
        in_recovery = cur.fetchone()[0]
        print(f"   REACHABLE   public tables = {ntables}")
        print(f"   data_directory            = {datadir}")
        print(f"   pg_is_in_recovery()       = {in_recovery}")
        missing = []
        for t in MARKERS:
            cur.execute("select to_regclass(%s)", (f"public.{t}",))
            if cur.fetchone()[0] is None:
                missing.append(t)
        print(f"   markers present           = {len(MARKERS) - len(missing)}/{len(MARKERS)}")
        if missing:
            print(f"   MISSING markers           = {missing}")
        c.close()
    except Exception as e:  # noqa: BLE001
        print(f"   UNREACHABLE: {type(e).__name__}: "
              f"{str(e).strip().splitlines()[0][:100]}")
else:
    print("   (no DSN to test)")

# ── 3. On-disk state of every candidate data directory ──────────────────────
print("-" * 78)
print("3. CANDIDATE DATA DIRECTORIES (on disk; pgdata is NOT started to inspect it)")
pids = _live_pids()
for d in sorted(p for p in STAGING.iterdir() if p.is_dir() and p.name.startswith("pgdata")):
    pv = d / "PG_VERSION"
    pid_file = d / "postmaster.pid"
    base = d / "base"
    size = sum(f.stat().st_size for f in base.rglob("*") if f.is_file()) if base.exists() else 0
    print(f"   {d.name}")
    print(f"      PG_VERSION      = {pv.read_text(encoding='utf-8').strip() if pv.exists() else 'ABSENT (not a cluster)'}")
    print(f"      base/ bytes     = {size:,}")
    if pid_file.exists():
        lines = pid_file.read_text(encoding="utf-8").splitlines()
        pid = int(lines[0]) if lines and lines[0].strip().isdigit() else None
        port = lines[3].strip() if len(lines) > 3 else "?"
        alive = pid in pids if pid is not None else False
        print(f"      postmaster.pid  = pid={pid} port={port} "
              f"-> {'LIVE' if alive else 'STALE (process gone)'}")
    else:
        print("      postmaster.pid  = absent (not running)")
    stale = list(d.glob("postmaster.pid.stale_*"))
    print(f"      stale pid backups = {len(stale)}")

# ── 4. Who selects the directory, and who writes the DSN ────────────────────
print("-" * 78)
print("4. SELECTION AND DSN-WRITE SITES (static)")
print("   PG_DATA_DIR is set by:")
for rel, val in (
    ("staging/start_pg.ps1", "pgdata_r1"),
    ("staging/pg_keepalive_r1.py", "pgdata_r1 (also hardcodes the path)"),
    ("DATABASE_PROVISIONING_RUNBOOK.md", "pgdata_z1"),
    ("PAPER_ONLY_LAUNCH_READINESS.md", "pgdata_z1"),
    ("RLS_TENANT_ISOLATION_VERIFICATION.md", "pgdata_z1"),
    ("BILLING_SECURITY_VERIFICATION.md", "pgdata_local"),
    ("FINAL_LAUNCH_READINESS_REPORT.md", "pgdata_local"),
):
    print(f"      {rel:<42} -> {val}")
print("   bootstrap_pg.py default when UNSET      -> pgdata")
print("   writers of staging/pg_dsn.txt:")
print("      staging/bootstrap_pg.py   (unconditional)")
print("      staging/pg_keepalive_r1.py(unconditional)")
print("      staging/probe_pg_find.py  (guarded: only if public_tables > 50)")

print("=" * 78)
sys.exit(0)
