"""Exact live shape of public.execution_records, before 8C touches it.

The capacity reservation sums `size * price` over in-flight rows. Whether that is a
numeric multiplication or a text cast depends on the real column types, and this
repository stores money as `varchar` in at least one table (`fee_records.fee_amount`), so
it is measured rather than assumed. Also lists the status values actually present, since
the reservation's predicate selects on them.
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
print(f"DSN={DSN}")

conn = psycopg2.connect(DSN, connect_timeout=10)
with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
    cur.execute(
        "SELECT column_name, data_type, udt_name, is_nullable, column_default "
        "FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='execution_records' "
        "ORDER BY ordinal_position"
    )
    for r in cur.fetchall():
        print(
            f"  {r['column_name']:<22} {r['data_type']:<28} udt={r['udt_name']:<16} "
            f"null={r['is_nullable']:<3} default={r['column_default']}"
        )

    cur.execute("SELECT count(*) FROM public.execution_records")
    print(f"ROW_COUNT={cur.fetchone()[0]}")

    cur.execute(
        "SELECT status, count(*) FROM public.execution_records GROUP BY status ORDER BY 2 DESC"
    )
    print(f"STATUS_VALUES={[tuple(r) for r in cur.fetchall()]}")

    cur.execute(
        "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = to_regclass('public.execution_records') ORDER BY 1"
    )
    for r in cur.fetchall():
        print(f"  CONSTRAINT {r['conname']}: {r['def']}")

    cur.execute(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname='public' AND tablename='execution_records' ORDER BY 1"
    )
    for r in cur.fetchall():
        print(f"  INDEX {r['indexname']}: {r['indexdef']}")

    # Does the enum (if any) that `status` uses carry lower or upper labels?
    cur.execute(
        """
        SELECT t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder)
          FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
         WHERE t.typname ILIKE '%execution%'
         GROUP BY t.typname
        """
    )
    print(f"ENUMS={[tuple(r) for r in cur.fetchall()]}")

    # Can a numeric multiplication be done directly on these columns?
    try:
        cur.execute(
            "SELECT COALESCE(SUM(ABS(size) * ABS(price)), 0) FROM public.execution_records"
        )
        print(f"DIRECT_NUMERIC_SUM_OK={cur.fetchone()[0]}")
    except Exception as exc:
        conn.rollback()
        print(f"DIRECT_NUMERIC_SUM_FAILED={exc.__class__.__name__}: {exc}")
        cur.execute(
            "SELECT COALESCE(SUM(ABS(size::numeric) * ABS(price::numeric)), 0) "
            "FROM public.execution_records"
        )
        print(f"CAST_NUMERIC_SUM_OK={cur.fetchone()[0]}")

conn.rollback()
conn.close()
