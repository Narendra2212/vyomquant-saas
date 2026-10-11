"""PHASE 10 / WORKSTREAM K -- read-only over-limit aggregate for the QUOTA-01 DB decision.

STRICTLY READ-ONLY. One connection, `default_transaction_read_only` forced on the session,
SELECT statements only, then rollback and close. No DDL, no DML, no migration.

PRIVACY. No customer identifier is emitted. Only aggregate counts and a distribution.

SUBSTRATE HONESTY -- READ THIS BEFORE USING THE NUMBERS. The reachable endpoint is the
LOCAL bootstrap cluster the provisioning runbook documents (`staging/bootstrap_pg.py`,
DSN in `staging/pg_dsn.txt`). Its `exchange_keys` rows are TEST FIXTURES created by this
repository's own suites. The output therefore establishes:

  * that the query is correct and runs, and
  * what THIS substrate contains,

and it does NOT establish how many real customers exceed a plan allowance. That figure can
only come from production or a production replica, which this workstream has no authorized
access to and does not seek.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def banner(text: str) -> None:
    print("=" * 78)
    print(text)
    print("=" * 78)


def main():
    banner("QUOTA-01 OVER-LIMIT AGGREGATE -- READ-ONLY, LOCAL TEST SUBSTRATE")

    dsn_path = pathlib.Path("staging/pg_dsn.txt")
    if not dsn_path.is_file():
        print("  staging/pg_dsn.txt absent -- nothing to query.")
        return
    dsn = dsn_path.read_text(encoding="utf-8", errors="replace").strip()

    m = re.search(r"@?([0-9A-Za-z.\-]+):(\d+)/([A-Za-z0-9_\-]+)", dsn)
    host, port, dbname = (m.group(1), m.group(2), m.group(3)) if m else ("?", "?", "?")
    print(f"  endpoint: host={host} port={port} db={dbname} (credential withheld)")

    # Refuse anything that is not plainly a local loopback cluster.
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("  REFUSING: the DSN does not name a loopback host, so it cannot be")
        print("  confirmed non-production from here. No connection attempted.")
        return
    print("  confirmed loopback / local bootstrap cluster -> non-production")

    try:
        import psycopg2
    except Exception as exc:
        print(f"  psycopg2 not importable ({type(exc).__name__}); cannot query.")
        return

    try:
        conn = psycopg2.connect(dsn, connect_timeout=5)
    except Exception as exc:
        print(f"  connection failed ({type(exc).__name__}); recorded as NOT OBTAINABLE.")
        return

    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            # Does the table even exist on this substrate?
            cur.execute("""
                SELECT EXISTS (
                  SELECT 1 FROM information_schema.tables
                  WHERE table_schema='public' AND table_name='exchange_keys'
                )
            """)
            if not cur.fetchone()[0]:
                print("  public.exchange_keys does not exist here -- NOT OBTAINABLE.")
                return

            cur.execute("SELECT COUNT(*) FROM public.exchange_keys")
            total_rows = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT user_id) FROM public.exchange_keys")
            total_accounts = cur.fetchone()[0]
            print(f"\n  rows in exchange_keys              : {total_rows}")
            print(f"  distinct accounts holding a key     : {total_accounts}")

            # Distribution of connections per account. NO identifiers selected.
            cur.execute("""
                SELECT connections, COUNT(*) AS accounts
                FROM (
                  SELECT user_id, COUNT(DISTINCT exchange_id) AS connections
                  FROM public.exchange_keys GROUP BY user_id
                ) per_account
                GROUP BY connections ORDER BY connections
            """)
            rows = cur.fetchall()
            print("\n  connections-per-account distribution (identifiers not selected):")
            if not rows:
                print("    (no accounts hold any exchange key on this substrate)")
            for connections, accounts in rows:
                print(f"    {connections} connection(s): {accounts} account(s)")

            # The decision-relevant aggregate, against each published allowance.
            # Free=1, Trader=2, Pro Quant=5, Business=8 (SubscriptionEngine._PLANS).
            print("\n  accounts that would EXCEED a given allowance (aggregate only):")
            for label, limit in (("Free", 1), ("Trader", 2), ("Pro Quant", 5), ("Business", 8)):
                cur.execute("""
                    SELECT COUNT(*) FROM (
                      SELECT user_id, COUNT(DISTINCT exchange_id) AS connections
                      FROM public.exchange_keys GROUP BY user_id
                      HAVING COUNT(DISTINCT exchange_id) > %s
                    ) over_limit
                """, (limit,))
                print(f"    > {limit} ({label:9s}): {cur.fetchone()[0]} account(s)")
        conn.rollback()
        print("\n  transaction rolled back; session was read-only throughout.")
    finally:
        conn.close()

    banner("INTERPRETATION")
    print("  These are TEST-FIXTURE counts from a local bootstrap cluster. They show the")
    print("  query is correct and runnable. They are NOT a production population figure,")
    print("  and must not be used as one for the coordinator's remediation decision.")


if __name__ == "__main__":
    main()
