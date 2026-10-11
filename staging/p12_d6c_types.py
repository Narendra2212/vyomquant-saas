"""Measure the real column types behind the D6-C evidence query, and fix the cast.

The first syntax check found that BOTH the D9 Packet-2 query and this phase's first
correction fail to plan with ``operator does not exist: uuid = text``. That is a defect in
the evidence REQUEST, not in the product: the query was written against the type declared in
``migrations/003_create_exchange_keys_table.sql`` (``user_id TEXT``), and the identity
hardening migration converts those columns to ``uuid``. A query that cannot plan would have
come back from the DB owner as an error, not as evidence.

This script measures the types, then settles the cast against the measured types.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

TYPE_SQL = """
SELECT table_name, column_name, data_type
  FROM information_schema.columns
 WHERE table_schema = 'public'
   AND (   (table_name = 'exchange_keys'     AND column_name IN ('user_id','exchange_id'))
        OR (table_name = 'execution_records' AND column_name IN ('tenant_id','user_id')))
 ORDER BY table_name, column_name;
"""

CANDIDATES = {
    "D9 Packet 2 (uuid = text)": """
        SELECT count(*) FILTER (WHERE k.user_id IS NOT NULL) AS tenants_with_keys,
               count(*) FILTER (WHERE k.user_id IS NULL)     AS tenants_without_keys
          FROM (SELECT DISTINCT tenant_id FROM public.execution_records) e
          LEFT JOIN public.exchange_keys k ON k.user_id = e.tenant_id::text;
    """,
    "EXISTS, text cast (uuid = text)": """
        SELECT count(*) AS distinct_tenants,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM public.exchange_keys k
                                               WHERE k.user_id = e.tenant_id::text)) AS tenants_with_keys,
               count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM public.exchange_keys k
                                                   WHERE k.user_id = e.tenant_id::text)) AS tenants_without_keys
          FROM (SELECT DISTINCT tenant_id FROM public.execution_records) e;
    """,
    "EXISTS, both sides ::text": """
        SELECT count(*) AS distinct_tenants,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM public.exchange_keys k
                                               WHERE k.user_id::text = e.tenant_id::text)) AS tenants_with_keys,
               count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM public.exchange_keys k
                                                   WHERE k.user_id::text = e.tenant_id::text)) AS tenants_without_keys
          FROM (SELECT DISTINCT tenant_id FROM public.execution_records) e;
    """,
    "EXISTS, no casts at all": """
        SELECT count(*) AS distinct_tenants,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM public.exchange_keys k
                                               WHERE k.user_id = e.tenant_id)) AS tenants_with_keys,
               count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM public.exchange_keys k
                                                   WHERE k.user_id = e.tenant_id)) AS tenants_without_keys
          FROM (SELECT DISTINCT tenant_id FROM public.execution_records) e;
    """,
}


def main() -> int:
    import psycopg2

    dsn = (ROOT / "staging" / "pg_dsn.txt").read_text().strip()
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    print("=== measured column types (provisioned test cluster) ===")
    cur.execute(TYPE_SQL)
    rows = cur.fetchall()
    for t, c, d in rows:
        print(f"  public.{t}.{c:<12} {d}")
    if not rows:
        print("  (no rows -- one of the tables is absent from this cluster)")

    print("\n=== which formulation actually plans ===")
    for label, sql in CANDIDATES.items():
        try:
            cur.execute("EXPLAIN " + sql)
            cur.fetchall()
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            print(f"  PLANS  {label}")
            print(f"         columns={cols}  row(VACUOUS on test cluster)={cur.fetchone()}")
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            first = str(exc).strip().splitlines()[0]
            print(f"  ERROR  {label}")
            print(f"         {type(exc).__name__}: {first}")

    print("\n=== declared vs deployed type, from the migrations ===")
    for rel, needle in (
        ("migrations/003_create_exchange_keys_table.sql", "user_id"),
        ("migrations/hardening/998_bug049_identity_types.sql", "uuid"),
    ):
        p = ROOT / rel
        if not p.exists():
            print(f"  {rel}: ABSENT")
            continue
        hits = [
            (i, ln.strip())
            for i, ln in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
            if needle in ln.lower()
        ][:3]
        print(f"  {rel}  (first {len(hits)} lines containing {needle!r})")
        for i, ln in hits:
            print(f"    :{i}  {ln[:150]}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
