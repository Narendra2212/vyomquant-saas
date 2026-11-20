"""Syntax-validate the corrected D6-C evidence query. SYNTAX ONLY.

WHAT THIS DOES AND DOES NOT ESTABLISH
-------------------------------------
The corrected query is addressed to the production database owner. It is NOT run for its
result here: the only reachable database is the provisioned test cluster
(``staging/pg_dsn.txt``), which has no real connected accounts, so every count it returns is
vacuous and is reported as such. What IS established here is that the SQL parses and plans
against the real schema -- so the DB owner receives a query that runs, rather than one that
errors on a column name.

The check is ``EXPLAIN``, which plans without executing, plus a plain run on the empty test
cluster to confirm the column labels come back as named.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The corrected query. Divergence from the D9 Packet-2 form is deliberate and explained in
# the Phase 12 report: the original LEFT JOIN counted matched join rows, and `exchange_keys`
# is UNIQUE (user_id, exchange_id), so a tenant holding keys on two venues was counted twice.
CORRECTED_SQL = """
SELECT count(*) AS distinct_tenants,
       count(*) FILTER (
           WHERE EXISTS (
               SELECT 1 FROM public.exchange_keys k
                WHERE k.user_id = e.tenant_id::text
           )
       ) AS tenants_with_keys,
       count(*) FILTER (
           WHERE NOT EXISTS (
               SELECT 1 FROM public.exchange_keys k
                WHERE k.user_id = e.tenant_id::text
           )
       ) AS tenants_without_keys
  FROM (SELECT DISTINCT tenant_id FROM public.execution_records) e;
"""

# The original, kept so the two can be compared on the same data.
ORIGINAL_SQL = """
SELECT count(*) FILTER (WHERE k.user_id IS NOT NULL) AS tenants_with_keys,
       count(*) FILTER (WHERE k.user_id IS NULL)     AS tenants_without_keys
  FROM (SELECT DISTINCT tenant_id FROM public.execution_records) e
  LEFT JOIN public.exchange_keys k
         ON k.user_id = e.tenant_id::text;
"""

# Demonstrates the defect without touching any real data: one tenant, two venues.
DEMO = """
CREATE TEMP TABLE _d6c_er  (tenant_id text) ON COMMIT DROP;
CREATE TEMP TABLE _d6c_key (user_id text, exchange_id text) ON COMMIT DROP;
INSERT INTO _d6c_er  VALUES ('tenant-A'), ('tenant-B');
INSERT INTO _d6c_key VALUES ('tenant-A','binance'), ('tenant-A','bybit');
"""

DEMO_ORIGINAL = """
SELECT count(*) FILTER (WHERE k.user_id IS NOT NULL) AS tenants_with_keys,
       count(*) FILTER (WHERE k.user_id IS NULL)     AS tenants_without_keys
  FROM (SELECT DISTINCT tenant_id FROM _d6c_er) e
  LEFT JOIN _d6c_key k ON k.user_id = e.tenant_id;
"""

DEMO_CORRECTED = """
SELECT count(*) AS distinct_tenants,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM _d6c_key k WHERE k.user_id = e.tenant_id))
           AS tenants_with_keys,
       count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM _d6c_key k WHERE k.user_id = e.tenant_id))
           AS tenants_without_keys
  FROM (SELECT DISTINCT tenant_id FROM _d6c_er) e;
"""


def main() -> int:
    dsn_file = ROOT / "staging" / "pg_dsn.txt"
    if not dsn_file.exists():
        print("NO DSN FILE -- cannot syntax-check. Reported as unverified.")
        return 1
    dsn = dsn_file.read_text().strip()

    try:
        import psycopg2
    except ImportError:
        print("psycopg2 not importable -- syntax check NOT PERFORMED.")
        return 1

    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()

    print("=== D6-C CORRECTED QUERY: SYNTAX / PLAN CHECK ===")
    print("target: provisioned TEST cluster. Counts here are VACUOUS by construction.\n")

    for label, sql in (("ORIGINAL (D9 Packet 2)", ORIGINAL_SQL), ("CORRECTED (EXISTS)", CORRECTED_SQL)):
        try:
            cur.execute("EXPLAIN " + sql)
            plan_rows = len(cur.fetchall())
            print(f"  {label:<24} EXPLAIN ok ({plan_rows} plan lines)")
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            print(f"  {label:<24} EXPLAIN FAILED: {type(exc).__name__}: {exc}")
            continue
        try:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            print(f"  {label:<24} columns={cols} row(vacuous)={cur.fetchone()}")
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            print(f"  {label:<24} EXECUTE FAILED: {type(exc).__name__}: {exc}")

    print("\n--- the counting defect, demonstrated on temp tables (no real data) ---")
    print("    fixture: tenant-A holds keys on 2 venues, tenant-B holds none.")
    print("    truth:   distinct_tenants=2, with_keys=1, without_keys=1")
    cur.execute(DEMO)
    cur.execute(DEMO_ORIGINAL)
    print(f"    ORIGINAL  -> {dict(zip([d[0] for d in cur.description], cur.fetchone()))}")
    cur.execute(DEMO_CORRECTED)
    print(f"    CORRECTED -> {dict(zip([d[0] for d in cur.description], cur.fetchone()))}")
    conn.rollback()

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
