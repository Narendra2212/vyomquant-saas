"""D6-C: verify the corrected query's count semantics against fixtures. No production data.

Phase 12 found two defects in the evidence query that had been circulating since D8:

  1. it compares ``k.user_id = e.tenant_id::text`` while BOTH columns are ``uuid``, so it
     fails to plan with ``operator does not exist: uuid = text``;
  2. it counts matched LEFT JOIN rows, and ``exchange_keys`` is UNIQUE
     ``(user_id, exchange_id)``, so a tenant holding keys on two venues is counted twice.

Phase 12 demonstrated (2) on text columns and fixed (1) by dropping the cast. This script
verifies the corrected query on fixtures that use the REAL column types (``uuid``) and covers
the three cases the brief names: multiple venue keys per tenant, tenants with no keys, and an
empty execution table.

Everything runs in a scratch schema inside a transaction that is rolled back. No production
database is contacted; the only reachable cluster is the local provisioned test one, which
holds no real connected accounts.
"""

from __future__ import annotations

import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRATCH = "p13_d6c"

ORIGINAL = """
SELECT count(*) FILTER (WHERE k.user_id IS NOT NULL) AS tenants_with_keys,
       count(*) FILTER (WHERE k.user_id IS NULL)     AS tenants_without_keys
  FROM (SELECT DISTINCT tenant_id FROM execution_records) e
  LEFT JOIN exchange_keys k ON k.user_id = e.tenant_id::text
"""

CORRECTED = """
SELECT count(*) AS distinct_tenants,
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM exchange_keys k WHERE k.user_id = e.tenant_id)
       ) AS tenants_with_keys,
       count(*) FILTER (
           WHERE NOT EXISTS (SELECT 1 FROM exchange_keys k WHERE k.user_id = e.tenant_id)
       ) AS tenants_without_keys
  FROM (SELECT DISTINCT tenant_id FROM execution_records) e
"""

TA = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TB = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
TC = "cccccccc-cccc-cccc-cccc-cccccccccccc"

# (name, execution tenants, (user_id, exchange) key rows, expected (distinct, with, without))
SCENARIOS = [
    ("one tenant, two venues; one tenant, no keys",
     [TA, TB], [(TA, "binance"), (TA, "bybit")], (2, 1, 1)),
    ("one tenant, three venues",
     [TA], [(TA, "binance"), (TA, "bybit"), (TA, "okx")], (1, 1, 0)),
    ("three tenants, none with keys",
     [TA, TB, TC], [], (3, 0, 3)),
    ("three tenants, all with one key each",
     [TA, TB, TC], [(TA, "binance"), (TB, "binance"), (TC, "okx")], (3, 3, 0)),
    ("empty execution table",
     [], [(TA, "binance")], (0, 0, 0)),
    ("key row for a tenant that has no executions (must not be counted)",
     [TA], [(TA, "binance"), (TB, "bybit")], (1, 1, 0)),
    ("duplicate execution rows for the same tenant (DISTINCT must collapse)",
     [TA, TA, TA, TB], [(TA, "binance")], (2, 1, 1)),
]


def main() -> int:
    import psycopg2

    # The shared staging/pg_dsn.txt is written by bootstrap_pg.py and read by every
    # PG-gated test and by other concurrent sessions. The cluster was restarted mid-phase
    # after an unclean shutdown and came back on a different port, so the file is stale --
    # but it is NOT edited here, because redirecting it would redirect another
    # workstream's tests too. An explicit override is accepted instead.
    import os

    dsn = os.environ.get("P13_PG_DSN") or (ROOT / "staging" / "pg_dsn.txt").read_text().strip()
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()

    print("=" * 94)
    print("D6-C CORRECTED QUERY - FIXTURE VERIFICATION (uuid columns, as deployed)")
    print("=" * 94)
    cur.execute("SELECT current_setting('port'), current_database()")
    _port, _db = cur.fetchone()
    print(f"connected to 127.0.0.1:{_port}/{_db}"
          f"{'  (DSN overridden via P13_PG_DSN)' if os.environ.get('P13_PG_DSN') else ''}")

    cur.execute(f"DROP SCHEMA IF EXISTS {SCRATCH} CASCADE")
    cur.execute(f"CREATE SCHEMA {SCRATCH}")
    cur.execute(f"SET search_path TO {SCRATCH}")
    # Same types as measured on the real schema: both uuid.
    cur.execute("CREATE TABLE execution_records (id serial primary key, tenant_id uuid)")
    cur.execute(
        "CREATE TABLE exchange_keys ("
        " id serial primary key, user_id uuid NOT NULL, exchange_id text NOT NULL,"
        " CONSTRAINT uq_user_exchange UNIQUE (user_id, exchange_id))"
    )
    print("\nfixture types mirror production: execution_records.tenant_id=uuid,")
    print("exchange_keys.user_id=uuid, UNIQUE (user_id, exchange_id).\n")

    # The original query must fail to plan here, for the same reason it would in production.
    print("### The original query against uuid columns")
    cur.execute("SAVEPOINT s")
    try:
        cur.execute("EXPLAIN " + ORIGINAL)
        cur.fetchall()
        print("  PLANS (unexpected -- the type mismatch did not occur)")
        cur.execute("RELEASE SAVEPOINT s")
        orig_plans = True
    except Exception as exc:
        code = getattr(exc, "pgcode", None)
        print(f"  FAILS: {type(exc).__name__} SQLSTATE={code}")
        print(f"    {str(exc).strip().splitlines()[0]}")
        cur.execute("ROLLBACK TO SAVEPOINT s")
        orig_plans = False

    print("\n### The corrected query, per scenario")
    print(f"\n  {'scenario':<62} {'measured':<16} {'expected':<16} verdict")
    print(f"  {'-'*62} {'-'*16} {'-'*16} -------")

    failures = 0
    for name, tenants, keys, expected in SCENARIOS:
        cur.execute("DELETE FROM exchange_keys")
        cur.execute("DELETE FROM execution_records")
        for t in tenants:
            cur.execute("INSERT INTO execution_records (tenant_id) VALUES (%s)", (t,))
        for uid, exch in keys:
            cur.execute(
                "INSERT INTO exchange_keys (user_id, exchange_id) VALUES (%s,%s)", (uid, exch)
            )
        cur.execute(CORRECTED)
        got = cur.fetchone()
        ok = tuple(got) == expected
        failures += 0 if ok else 1
        print(f"  {name:<62} {str(tuple(got)):<16} {str(expected):<16} "
              f"{'ok' if ok else 'MISMATCH'}")

    print(f"\n  scenarios={len(SCENARIOS)}  mismatches={failures}")

    # Side-by-side on the one scenario that exposes defect (2), using text columns so the
    # original can actually run and be compared.
    print("\n### Side-by-side on text columns, so the original can run and be compared")
    cur.execute("CREATE TABLE er_t (tenant_id text)")
    cur.execute("CREATE TABLE ek_t (user_id text, exchange_id text)")
    cur.execute("INSERT INTO er_t VALUES (%s),(%s)", (TA, TB))
    cur.execute("INSERT INTO ek_t VALUES (%s,'binance'),(%s,'bybit')", (TA, TA))
    print("  fixture: tenant A holds keys on 2 venues, tenant B holds none.")
    print("  truth  : distinct_tenants=2, with_keys=1, without_keys=1")
    cur.execute("""
        SELECT count(*) FILTER (WHERE k.user_id IS NOT NULL) AS tenants_with_keys,
               count(*) FILTER (WHERE k.user_id IS NULL)     AS tenants_without_keys
          FROM (SELECT DISTINCT tenant_id FROM er_t) e
          LEFT JOIN ek_t k ON k.user_id = e.tenant_id
    """)
    o = cur.fetchone()
    cur.execute("""
        SELECT count(*) AS distinct_tenants,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM ek_t k WHERE k.user_id=e.tenant_id)) AS w,
               count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM ek_t k WHERE k.user_id=e.tenant_id)) AS wo
          FROM (SELECT DISTINCT tenant_id FROM er_t) e
    """)
    c = cur.fetchone()
    print(f"  ORIGINAL  -> with_keys={o[0]}, without_keys={o[1]}   "
          f"(sum={o[0]+o[1]} for 2 tenants {'<- OVERCOUNT' if o[0]+o[1] != 2 else ''})")
    print(f"  CORRECTED -> distinct={c[0]}, with_keys={c[1]}, without_keys={c[2]}")

    # Disclosure check: the corrected query must return counts only.
    print("\n### Disclosure check: does the corrected query expose any identifier?")
    cur.execute(f"SET search_path TO {SCRATCH}")
    cur.execute(CORRECTED)
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    print(f"  result columns : {cols}")
    print(f"  result row     : {row}")
    all_int = all(isinstance(v, int) for v in row)
    print(f"  every value is an integer : {all_int}")
    print(f"  verdict : {'counts only, no identifiers' if all_int else 'REVIEW - non-integer returned'}")

    cur.execute("RESET search_path")
    cur.execute(f"DROP SCHEMA IF EXISTS {SCRATCH} CASCADE")
    conn.rollback()
    cur.execute("SELECT count(*) FROM pg_namespace WHERE nspname=%s", (SCRATCH,))
    print(f"\n  rolled back; scratch schema count={cur.fetchone()[0]} (expect 0)")

    print("\n" + "=" * 94)
    print("SUMMARY")
    print("=" * 94)
    print(f"  original query plans against uuid columns : {orig_plans} (expected False)")
    print(f"  corrected query fixture mismatches        : {failures} (expected 0)")
    print(f"  corrected query returns counts only       : {all_int}")
    print()
    print("  NOT ESTABLISHED HERE: any production value. These are fixtures on a test")
    print("  cluster with no real connected accounts. `tenants_without_keys = 0` has NOT")
    print("  been measured in production and remains the load-bearing unmet condition.")

    cur.close()
    conn.close()
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
