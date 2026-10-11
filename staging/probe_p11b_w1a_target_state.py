"""PHASE 11B-W1A -- read-only identity and migration state of the W1 target cluster.

STRICTLY READ-ONLY: `set_session(readonly=True)` so the SERVER rejects any write, the
setting is read back to prove it took, SELECT only, then rollback and close.

No DSN, credential or customer identifier is printed.
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def banner(t: str) -> None:
    print("=" * 78); print(t); print("=" * 78)


def main():
    banner("W1 TARGET CLUSTER -- READ-ONLY IDENTITY AND MIGRATION STATE")

    dsn_file = pathlib.Path("staging/pg_dsn.txt")
    if not dsn_file.is_file():
        print("  staging/pg_dsn.txt absent -- no target to inspect."); return
    dsn = dsn_file.read_text(encoding="utf-8").strip()

    m = re.search(r"@?([0-9A-Za-z.\-]+):(\d+)/([A-Za-z0-9_\-]+)", dsn)
    host, port, db = (m.group(1), m.group(2), m.group(3)) if m else ("?", "?", "?")
    print(f"  endpoint: host={host} port={port} db={db}   (credential withheld)")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("  REFUSING: not a loopback host. No connection attempted."); return
    print("  loopback confirmed -> non-production")

    import psycopg2
    conn = psycopg2.connect(dsn, connect_timeout=5)
    try:
        # autocommit=True, matching scripts/verify_schema_drift.py's proven pattern.
        # With autocommit=False psycopg2 defers the session setting to the next
        # transaction, so the read-back saw 'off' and this probe correctly aborted
        # rather than proceeding on an unproven guarantee.
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute("SHOW default_transaction_read_only")
            ro = cur.fetchone()[0]
            print(f"  session read-only enforced by the server: {ro}")
            if ro != "on":
                print("  ABORTING: session is not read-only."); return

            cur.execute("SELECT current_setting('server_version'), "
                        "current_setting('data_directory'), current_database(), "
                        "current_user, pg_postmaster_start_time()")
            ver, datadir, dbname, usr, started = cur.fetchone()
            print(f"\n  server_version : {ver}")
            print(f"  data_directory : {datadir}")
            print(f"  current_database: {dbname}")
            print(f"  connected role : {usr}")
            print(f"  postmaster up since: {started}")

            cur.execute("SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema='public'")
            print(f"  public tables  : {cur.fetchone()[0]}")

            banner("MIGRATION STATE")
            cur.execute("SELECT to_regclass('public.alembic_version')")
            if cur.fetchone()[0] is None:
                print("  alembic_version: ABSENT -- never stamped")
            else:
                cur.execute("SELECT version_num FROM public.alembic_version")
                stamped = [r[0] for r in cur.fetchall()]
                print(f"  alembic_version rows: {stamped}")
                print("  repository single head (W1A graph probe): d4e5f6a7b8c9")
                print(f"  AT HEAD: {stamped == ['d4e5f6a7b8c9']}")

            cur.execute("SELECT to_regclass('public.schema_migrations')")
            if cur.fetchone()[0] is None:
                print("  schema_migrations: ABSENT -- provisioner has never run here")
            else:
                cur.execute("SELECT count(*), min(applied_at), max(applied_at) "
                            "FROM public.schema_migrations")
                n, lo, hi = cur.fetchone()
                print(f"  schema_migrations rows: {n}  (first {lo}, last {hi})")
                cur.execute("SELECT step, count(*) FROM public.schema_migrations "
                            "GROUP BY step ORDER BY step")
                for step, cnt in cur.fetchall():
                    print(f"      step {step:8s} {cnt} file(s)")

            banner("IS THE TARGET DISPOSABLE? (row counts only, no identifiers)")
            print("  Row counts establish that the cluster HOLDS DATA. Whether that data")
            print("  is disposable is an OWNERSHIP question, not one the schema answers.")
            for table in ("profiles", "exchange_keys", "execution_records", "orders",
                          "positions", "fee_records", "referral_payouts",
                          "safety_control_audit"):
                cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                if cur.fetchone()[0] is None:
                    print(f"    {table:24s} ABSENT")
                    continue
                cur.execute(f"SELECT count(*) FROM public.{table}")
                print(f"    {table:24s} {cur.fetchone()[0]} row(s)")

            banner("RLS POSTURE (the TENANT-01 / P11-B09 question)")
            cur.execute("""
                SELECT count(*) FILTER (WHERE relrowsecurity),
                       count(*) FILTER (WHERE relforcerowsecurity),
                       count(*)
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname='public' AND c.relkind='r'
            """)
            enabled, forced, total = cur.fetchone()
            print(f"  public tables      : {total}")
            print(f"  RLS enabled        : {enabled}")
            print(f"  RLS FORCED         : {forced}   <-- FORCE ROW LEVEL SECURITY")
            print(f"  connected role is table owner: {usr!r} (owner-role bypasses "
                  f"non-forced RLS)")
        print("\n  session was read-only throughout (server-enforced); nothing written.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
