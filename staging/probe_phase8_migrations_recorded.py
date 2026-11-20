"""Confirm 026 and 027 are recorded in public.schema_migrations, and that the row
security / currency state they claim is actually present.

The Phase 8 artifacts state that both migrations were applied and verified. That claim
should rest on the catalogue, not on the fact that the apply script printed "applied".
"""
from __future__ import annotations

import os
import pathlib

import psycopg2
import psycopg2.extras

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN = (os.environ.get("AERORA_TEST_DATABASE_URL") or "").strip() or (
    (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
)

conn = psycopg2.connect(DSN, connect_timeout=10)
conn.autocommit = True
with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
    cur.execute(
        "SELECT filename, step, applied_at FROM public.schema_migrations "
        " WHERE filename LIKE '%%026%%' OR filename LIKE '%%027%%' ORDER BY filename"
    )
    rows = cur.fetchall()
    print(f"RECORDED_MIGRATIONS={len(rows)}")
    for r in rows:
        print(f"  {r['filename']}  step={r['step']}  at={r['applied_at']}")

    cur.execute(
        "SELECT c.relrowsecurity, c.relforcerowsecurity, "
        "       (SELECT count(*) FROM pg_policies p "
        "         WHERE p.schemaname='public' AND p.tablename='fee_records') AS policies "
        "  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        " WHERE n.nspname='public' AND c.relname='fee_records'"
    )
    r = cur.fetchone()
    print(
        f"FEE_RECORDS rls={r['relrowsecurity']} forced={r['relforcerowsecurity']} "
        f"policies={r['policies']}"
    )

    cur.execute(
        "SELECT count(*) FROM information_schema.role_table_grants "
        " WHERE table_schema='public' AND table_name='fee_records' "
        "   AND grantee IN ('anon','authenticated')"
    )
    print(f"FEE_RECORDS_BROWSER_GRANTS={cur.fetchone()[0]}")

    for table in ("referral_commissions", "referral_wallets", "referral_payouts"):
        cur.execute(
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
            " WHERE table_schema='public' AND table_name=%s AND column_name='currency'",
            (table,),
        )
        col = cur.fetchone()
        cur.execute(
            "SELECT count(*) FROM pg_constraint "
            " WHERE conrelid = to_regclass(%s) AND contype='c' "
            "   AND pg_get_constraintdef(oid) ILIKE '%%currency%%'",
            (f"public.{table}",),
        )
        checks = cur.fetchone()[0]
        if col is None:
            print(f"{table}: NO CURRENCY COLUMN")
        else:
            print(
                f"{table}: currency {col['data_type']} nullable={col['is_nullable']} "
                f"default={col['column_default']} currency_checks={checks}"
            )
conn.close()
