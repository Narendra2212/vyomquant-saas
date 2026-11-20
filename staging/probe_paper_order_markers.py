"""Is `paper_orders.idempotency_key LIKE 'probe-%'` residue, or a live leak?

`tests/test_production_security_probe_rolls_back_postgres.py::test_no_synthetic_marker_value_survives`
counts marker rows AFTER its probe runs, so a row committed by an EARLIER run is
indistinguishable from one the current run leaked. The row's own timestamps settle it.
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
conn.autocommit = True
with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
    cur.execute("SELECT now() AS now, current_setting('TimeZone') AS tz")
    row = cur.fetchone()
    print(f"SERVER_NOW={row['now']}  TZ={row['tz']}")

    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='paper_orders' ORDER BY ordinal_position"
    )
    cols = [(r["column_name"], r["data_type"]) for r in cur.fetchall()]
    print(f"PAPER_ORDERS_COLS={cols}")

    cur.execute("SELECT count(*) FROM public.paper_orders")
    print(f"PAPER_ORDERS_TOTAL={cur.fetchone()[0]}")

    cur.execute(
        "SELECT * FROM public.paper_orders WHERE idempotency_key LIKE 'probe-%%' "
        "ORDER BY 1 LIMIT 20"
    )
    marked = cur.fetchall()
    print(f"MARKED_ROWS={len(marked)}")
    for r in marked:
        print("  ROW:")
        for key in r.keys():
            print(f"    {key} = {r[key]!r}")

    # Anything else carrying the probe's identity marker?
    cur.execute(
        """
        SELECT c.table_name, c.column_name
          FROM information_schema.columns c
          JOIN information_schema.tables t
            ON t.table_schema = c.table_schema AND t.table_name = c.table_name
         WHERE c.table_schema = 'public'
           AND t.table_type = 'BASE TABLE'
           AND c.data_type IN ('text', 'character varying')
         ORDER BY 1, 2
        """
    )
    candidates = [(r["table_name"], r["column_name"]) for r in cur.fetchall()]

hits = []
with conn.cursor() as cur:
    for table, column in candidates:
        for pattern in ("probe-%", "%@example.invalid"):
            try:
                cur.execute(
                    f'SELECT count(*) FROM public."{table}" WHERE "{column}" LIKE %s',
                    (pattern,),
                )
                n = cur.fetchone()[0]
            except Exception as exc:  # pragma: no cover
                conn.rollback()
                print(f"  SKIP {table}.{column}: {exc}")
                continue
            if n:
                hits.append((table, column, pattern, n))

print(f"ALL_MARKER_HITS={len(hits)}")
for table, column, pattern, n in hits:
    print(f"  {table}.{column} LIKE {pattern!r} -> {n}")
conn.close()
