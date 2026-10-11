"""Has a PG-gated suite already written to this shared cluster? Look for its residue.

PHASE 11B / WORKSTREAM 1B — read-only.

WHY
---
Several PG-gated suites create scratch objects in `public` on the shared cluster and drop
them in a `finally`:

    table = f"tenant01_canary_{uuid.uuid4().hex[:8]}"
    cur.execute(f'CREATE TABLE public."{table}" (...)')
    ...
    finally:
        cur.execute(f'DROP TABLE IF EXISTS public."{table}" CASCADE')

A `finally` does not run if the process is killed, the machine sleeps, or the connection
dies mid-test. Any orphan left behind is permanent: it inflates the table count forever
and makes `scripts/verify_schema_drift.py` report drift on every subsequent run.

So this answers two questions that bear directly on whether a shared-cluster lane should
be authorized:

  1. Is there residue from a previous run? If yes, these suites HAVE already written here
     and the cluster is not pristine.
  2. Could the observed drift (89 live tables vs 85 baseline) be residue rather than
     migrations? That changes the verdict in deliverable 2 from "stale baseline" to
     "polluted database", and the two have different fixes.

It also lists the application roles the RLS suites switch into, so deliverable 3 can say
whether a non-owner role suitable for tenant-isolation testing exists at all.

STRICTLY READ-ONLY: enforced `default_transaction_read_only` with readback, SELECTs only.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
DSN_FILE = REPO / "staging" / "pg_dsn.txt"

#: Scratch-object name patterns these suites generate, taken from the source rather than
#: invented: `tenant01_canary_<hex>`, `tenant01_norls_<hex>`, `drift_readonly_ddl_probe`,
#: and the `tenant_probe*` / `tenant_seq_probe` family.
RESIDUE_PATTERNS = [
    ("tenant01#_canary#_%", "test_tenant01_cross_tenant_matrix_postgres.py canary"),
    ("tenant01#_norls#_%", "test_tenant01_cross_tenant_matrix_postgres.py no-RLS canary"),
    ("drift#_readonly#_ddl#_probe%", "test_schema_drift_checker_is_read_only_postgres.py"),
    ("tenant#_probe%", "tenant-context probe tables"),
    ("tenant#_seq#_probe%", "tenant-context sequence probe"),
    ("%canary%", "any canary"),
    ("%probe%", "any probe"),
    ("%scratch%", "any scratch"),
    ("%tmp#_%", "any tmp_"),
]


def _connect(dsn: str):
    import psycopg2
    conn = psycopg2.connect(dsn, connect_timeout=10)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    cur.execute("SHOW default_transaction_read_only")
    if cur.fetchone()[0] != "on":
        conn.close()
        raise SystemExit("ABORT: could not enforce a read-only session.")
    return conn


def main() -> int:
    dsn = DSN_FILE.read_text(encoding="utf-8").strip()
    conn = _connect(dsn)
    cur = conn.cursor()

    print("=" * 78)
    print("RESIDUE SWEEP -- has a PG-gated suite already written here?")
    print("=" * 78)

    print("\n-- scratch-object name patterns in public ---------------------------------")
    print("   (LIKE patterns use ESCAPE '#'; '#_' is a literal underscore. Without it"
          "\n    LIKE treats '_' as a single-character wildcard, which made an earlier"
          "\n    run of this probe report `marketplace_backtest_evidence` as residue"
          "\n    because 'ktest_' matched '_test_'.)")
    total_residue = 0
    for pattern, owner in RESIDUE_PATTERNS:
        cur.execute(
            "select tablename from pg_tables "
            "where schemaname='public' and tablename like %s escape '#' order by 1",
            (pattern,))
        rows = [r[0] for r in cur.fetchall()]
        flag = "RESIDUE" if rows else "clean  "
        print(f"  [{flag}] {pattern:30} ({owner})")
        for name in rows:
            total_residue += 1
            print(f"            !! {name}")
    print(f"\n  scratch objects found: {total_residue}")

    print("\n-- newest tables by creation-adjacent evidence ----------------------------")
    # PostgreSQL does not record table creation time. The closest read-only proxies are
    # the pg_class OID ordering (monotonic within a cluster's lifetime) and the file
    # node. A table created long after provisioning has a visibly higher OID.
    cur.execute("""
        select c.relname, c.oid::bigint, pg_total_relation_size(c.oid) as bytes
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname='public' and c.relkind='r'
        order by c.oid desc limit 15
    """)
    print(f"  {'table':44} {'oid':>10} {'bytes':>10}")
    for name, oid, nbytes in cur.fetchall():
        print(f"  {name:44} {oid:>10} {nbytes:>10}")

    print("\n-- the 4 tables verify_schema_drift reported as unexpected ----------------")
    for name in ("bot_ownership_claims", "fee_records", "referral_obligations",
                 "safety_control_audit"):
        cur.execute("""
            select c.oid::bigint, c.relrowsecurity, c.relforcerowsecurity,
                   (select count(*) from pg_policies p
                    where p.schemaname='public' and p.tablename=c.relname)
            from pg_class c join pg_namespace n on n.oid=c.relnamespace
            where n.nspname='public' and c.relname=%s
        """, (name,))
        row = cur.fetchone()
        if row is None:
            print(f"  {name:28} (absent)")
            continue
        oid, rls, forced, npol = row
        print(f"  {name:28} oid={oid:<9} rls={rls} forced={forced} policies={npol}")
    print("  (these carry RLS and policies, which a crashed canary would not: a canary"
          "\n   is created with at most one policy and a random hex suffix.)")

    print("\n-- roles on this cluster --------------------------------------------------")
    cur.execute("""
        select rolname, rolsuper, rolbypassrls, rolcanlogin, rolinherit
        from pg_roles order by rolsuper desc, rolname
    """)
    print(f"  {'role':26} {'super':>6} {'bypassrls':>10} {'canlogin':>9} {'inherit':>8}")
    app_roles = []
    for name, sup, byp, login, inh in cur.fetchall():
        print(f"  {name:26} {str(sup):>6} {str(byp):>10} {str(login):>9} {str(inh):>8}")
        if not sup and not byp:
            app_roles.append((name, login))

    print("\n-- roles usable for tenant-isolation testing ------------------------------")
    print("  (not superuser, no BYPASSRLS -- RLS would actually be evaluated)")
    if not app_roles:
        print("     NONE")
    for name, login in app_roles:
        print(f"     {name:24} can_login={login}")
    print("\n  NOTE: a role that cannot log in can still be entered with SET ROLE from a")
    print("  superuser session, which is how the RLS suites do it. But SET ROLE from a")
    print("  superuser keeps `rolbypassrls` of the TARGET role, so SET ROLE to a")
    print("  non-BYPASSRLS role DOES subject the session to RLS. Table OWNERSHIP still")
    print("  matters: the target role must not own the table, and 89/89 are owned by")
    print("  postgres, so a SET ROLE to a non-owner role is the only configuration in")
    print("  which these suites evaluate policies at all.")

    print("\n-- table-level grants to the application roles ----------------------------")
    cur.execute("""
        select grantee, privilege_type, count(*)
        from information_schema.table_privileges
        where table_schema='public'
          and grantee in ('anon','authenticated','service_role')
        group by grantee, privilege_type order by grantee, privilege_type
    """)
    rows = cur.fetchall()
    if not rows:
        print("     none -- anon/authenticated/service_role hold no table privileges")
    for grantee, priv, n in rows:
        print(f"     {grantee:16} {priv:12} on {n} tables")

    conn.close()
    schema_residue()
    return 0


# ─────────────────────────────────────────────────────────────────────────────
#  Addendum: SCHEMA-level residue
#
#  The first version of this probe swept `public` for orphan TABLES and reported
#  "clean". That was incomplete. `tests/test_strategy_lifecycle_concurrency_postgres.py`
#  does not create tables in `public` at all -- it creates a whole scratch SCHEMA per
#  run:
#
#      self.schema = "vq_1323_" + uuid.uuid4().hex[:10]
#      cur.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
#      cur.execute(f"CREATE SCHEMA {self.schema}")
#      ...
#      cur.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")   # teardown
#
#  A table sweep cannot see that, so "no residue" was a claim the measurement did not
#  support. This closes the gap.
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA_PATTERNS = [
    ("vq#_1323#_%", "test_strategy_lifecycle_concurrency_postgres.py scratch schema"),
    ("vq#_%", "any vq_ scratch schema"),
    ("%canary%", "any canary schema"),
    ("%probe%", "any probe schema"),
    ("%scratch%", "any scratch schema"),
    ("%tmp%", "any tmp schema"),
]

#: Schemas a provisioned Supabase-shaped cluster legitimately carries.
EXPECTED_SCHEMAS = {
    "public", "auth", "storage", "information_schema", "pg_catalog", "pg_toast",
    "extensions", "graphql", "graphql_public", "realtime", "supabase_functions",
    "vault", "pgbouncer", "net", "cron", "pgsodium", "pgsodium_masks",
    "supabase_migrations", "_realtime", "pg_temp_1", "pg_toast_temp_1",
}


def schema_residue() -> int:
    dsn = DSN_FILE.read_text(encoding="utf-8").strip()
    conn = _connect(dsn)
    cur = conn.cursor()
    print("\n" + "=" * 78)
    print("SCHEMA-LEVEL RESIDUE SWEEP")
    print("=" * 78)

    found = 0
    for pattern, owner in SCHEMA_PATTERNS:
        # ESCAPE '#' so a literal underscore in `vq_1323_` is matched as an underscore
        # and not as LIKE's single-character wildcard. The table sweep above did NOT do
        # this, which is why `%_test_%` produced two false hits.
        cur.execute(
            "select nspname from pg_namespace where nspname like %s escape '#' "
            "order by 1", (pattern,))
        rows = [r[0] for r in cur.fetchall()]
        rows = [r for r in rows if r not in EXPECTED_SCHEMAS]
        print(f"  [{'RESIDUE' if rows else 'clean  '}] {pattern:18} ({owner})")
        for name in rows:
            found += 1
            print(f"            !! {name}")

    print("\n-- every schema on this cluster ------------------------------------------")
    cur.execute("select nspname from pg_namespace order by 1")
    all_schemas = [r[0] for r in cur.fetchall()]
    unexpected = [s for s in all_schemas
                  if s not in EXPECTED_SCHEMAS and not s.startswith("pg_")]
    for name in all_schemas:
        mark = "  <- UNEXPECTED" if name in unexpected else ""
        print(f"     {name}{mark}")
    print(f"\n  schemas total      : {len(all_schemas)}")
    print(f"  unexpected schemas : {len(unexpected)}  {unexpected}")
    print(f"  pattern residue    : {found}")

    conn.close()
    return found


if __name__ == "__main__":
    sys.exit(main())
