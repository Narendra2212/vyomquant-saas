"""Stage 0 — Phase 10 baseline environment probe. Read-only.

Records: PostgreSQL reachability + table count + alembic/migration head markers, whether any
live-trading env var is set, and the presence of the Phase 9 artifacts/verifiers. Writes
nothing to any database."""
from __future__ import annotations

import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
dsn = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()

print("=" * 72)
print("PHASE 10 STAGE 0 — ENVIRONMENT BASELINE (read-only)")
print("=" * 72)
print(f"PG DSN: {dsn}")

try:
    import psycopg2

    c = psycopg2.connect(dsn)
    cur = c.cursor()
    cur.execute("select count(*) from information_schema.tables where table_schema='public'")
    ntables = cur.fetchone()[0]
    print(f"PG_UP  public tables = {ntables}")

    # alembic head, if the table exists
    cur.execute("select to_regclass('public.alembic_version')")
    if cur.fetchone()[0] is not None:
        cur.execute("select version_num from alembic_version")
        print("alembic_version rows:", [r[0] for r in cur.fetchall()])
    else:
        print("alembic_version: (table absent)")

    # Phase 9 tables present?
    for t in ("safety_control_audit", "referral_obligations"):
        cur.execute("select to_regclass(%s)", (f"public.{t}",))
        print(f"table {t}: {'present' if cur.fetchone()[0] else 'ABSENT'}")

    # execution_records.venue_order_id (migration 030)
    cur.execute("""select column_name, data_type, is_nullable
                   from information_schema.columns
                   where table_schema='public' and table_name='execution_records'
                     and column_name='venue_order_id'""")
    row = cur.fetchone()
    print("execution_records.venue_order_id:", row if row else "ABSENT")

    c.close()
except Exception as e:  # noqa: BLE001
    print(f"PG_DOWN: {type(e).__name__}: {e}")

print("-" * 72)
print("LIVE-TRADING / DB ENV VARS currently set in this process:")
flagged = {k: v for k, v in os.environ.items()
           if any(s in k.upper() for s in ("LIVE", "TRADING", "PRODUCTION_ROUTER",
                                            "AERORA_TEST", "DATABASE_URL", "VYOMQUANT"))}
if not flagged:
    print("  (none)")
for k, v in sorted(flagged.items()):
    shown = v if len(v) < 60 else v[:57] + "..."
    print(f"  {k} = {shown!r}")

print("-" * 72)
print("PHASE 9 ARTIFACTS / VERIFIERS PRESENT:")
for rel in (
    "staging/verify_phase9_open_findings.py",
    "staging/verify_phase9_consistency.py",
    "staging/finalize_phase9_counts.py",
    "staging/fullreg_phase9_counts.json",
    "staging/full_audit_results.json",
    "staging/trading_audit.json",
    "staging/money_flow_audit.json",
    "staging/marketplace_audit.json",
    "backend_app/migrations/028_safety_control_audit.sql",
    "backend_app/migrations/029_referral_obligations.sql",
    "backend_app/migrations/030_execution_venue_order_id.sql",
):
    print(f"  {'OK ' if (ROOT / rel).exists() else 'MISSING'} {rel}")
