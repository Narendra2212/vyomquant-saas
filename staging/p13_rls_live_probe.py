"""Measure LIVE Row-Level-Security state, and empirically test what FORCE would do.

WHY THIS EXISTS
---------------
Every prior phase in this programme established RLS posture by counting statements in
migration text. That is not evidence of live state, for a reason visible in the migrations
themselves: ``backend_app/alembic/versions/implement_rls_policies_20260817.py`` wraps both its
``ALTER TABLE ... ENABLE ROW LEVEL SECURITY`` loop and every ``CREATE POLICY`` in
``try/except`` that only ``print``s a warning. The migration reports success whether or not
anything was enabled. So the text says nothing about the database.

This probe reads the actual catalog, and then runs the one experiment that the whole
TENANT-01 safety argument rests on.

THE CLAIM UNDER TEST
--------------------
``backend_app/core/tenant_db_context.py:13-19`` warns that enabling FORCE before the tenant
GUC is wired "converts reads on ``orders``, ``positions`` and ``fills`` into silently empty
result sets". ``backend_app/migrations/026_fee_records_row_level_security.sql:73-78`` states
the mechanism: the application connects as ``postgres``, which OWNS the tables, and a table
owner is exempt from its own policies unless FORCE is set.

Both are prose. This script proves or disproves them on a real PostgreSQL server.

SAFETY
------
Target is the provisioned local test cluster named in ``staging/pg_dsn.txt`` -- never a
deployed database. The experiment runs inside a dedicated scratch schema that is created and
dropped in the same transaction, and the transaction is ROLLED BACK unconditionally. No
existing table, policy, role or row is read for its contents or modified. Nothing is created
outside the scratch schema. No credential, token or row value is printed.
"""

from __future__ import annotations

import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The tables the two policy families target, plus the credential table.
TENANT_TABLES = [
    "orders",
    "positions",
    "fills",
    "execution_records",
    "dag_tasks",
    "idempotency_keys",
    "transaction_records",
    "transaction_checkpoints",
    "reconciliation_mismatches",
    "exchange_keys",
    "fee_records",
]

SCRATCH = "p13_rls_probe"


def main() -> int:
    import psycopg2

    dsn = (ROOT / "staging" / "pg_dsn.txt").read_text().strip()
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()

    print("=" * 78)
    print("PHASE 13 - LIVE RLS STATE AND FORCE-RLS BEHAVIOUR")
    print("target: provisioned local test cluster (staging/pg_dsn.txt). NOT a deployment.")
    print("=" * 78)

    # ------------------------------------------------------------------ identity
    print("\n### 1. WHO AM I, AND DOES RLS APPLY TO ME AT ALL?\n")
    cur.execute("""
        SELECT current_user, session_user,
               (SELECT rolsuper      FROM pg_roles WHERE rolname = current_user),
               (SELECT rolbypassrls  FROM pg_roles WHERE rolname = current_user),
               current_setting('server_version')
    """)
    cu, su, is_super, bypass, ver = cur.fetchone()
    print(f"  current_user        : {cu}")
    print(f"  session_user        : {su}")
    print(f"  rolsuper            : {is_super}")
    print(f"  rolbypassrls        : {bypass}")
    print(f"  server_version      : {ver}")
    print()
    if is_super:
        print("  NOTE: a SUPERUSER bypasses RLS unconditionally -- FORCE does not apply to it.")
    if bypass:
        print("  NOTE: rolbypassrls is set -- this role ignores every policy.")

    # ------------------------------------------------- per-table catalog state
    print("\n### 2. PER-TABLE LIVE STATE (pg_class / pg_policies)\n")
    print(f"  {'table':<30} {'exists':<7} {'owner':<12} {'RLS':<5} {'FORCE':<6} {'policies'}")
    print(f"  {'-'*30} {'-'*7} {'-'*12} {'-'*5} {'-'*6} {'-'*8}")
    summary = {}
    for t in TENANT_TABLES:
        cur.execute(
            """
            SELECT c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner),
                   (SELECT count(*) FROM pg_policies p
                     WHERE p.schemaname='public' AND p.tablename=c.relname)
              FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='public' AND c.relname=%s AND c.relkind='r'
            """,
            (t,),
        )
        row = cur.fetchone()
        if row is None:
            print(f"  {t:<30} {'NO':<7} {'-':<12} {'-':<5} {'-':<6} -")
            summary[t] = None
            continue
        rls, force, owner, npol = row
        summary[t] = (rls, force, owner, npol)
        print(f"  {t:<30} {'yes':<7} {owner:<12} {str(rls):<5} {str(force):<6} {npol}")

    present = {k: v for k, v in summary.items() if v}
    print(f"\n  tables present          : {len(present)}/{len(TENANT_TABLES)}")
    print(f"  with relrowsecurity     : {sum(1 for v in present.values() if v[0])}")
    print(f"  with relforcerowsecurity: {sum(1 for v in present.values() if v[1])}")
    print(f"  owned by current_user   : {sum(1 for v in present.values() if v[2] == cu)}")

    # ------------------------------------------------------------- the policies
    print("\n### 3. EVERY POLICY ON THOSE TABLES (roles, command, predicate)\n")
    cur.execute(
        """
        SELECT tablename, policyname, roles::text, cmd, qual, with_check
          FROM pg_policies
         WHERE schemaname='public' AND tablename = ANY(%s)
         ORDER BY tablename, policyname
        """,
        (TENANT_TABLES,),
    )
    pols = cur.fetchall()
    if not pols:
        print("  (none)")
    cur_table = None
    for tbl, name, roles, cmd, qual, wcheck in pols:
        if tbl != cur_table:
            print(f"\n  -- {tbl} --")
            cur_table = tbl
        print(f"    {name}")
        print(f"      roles={roles}  cmd={cmd}")
        print(f"      USING      : {qual}")
        print(f"      WITH CHECK : {wcheck}")
    print(f"\n  total policies: {len(pols)}")

    # ------------------------------------------------------ the FORCE experiment
    print("\n" + "=" * 78)
    print("### 4. THE EXPERIMENT: does FORCE empty an owner's reads when the GUC is unset?")
    print("=" * 78)
    print("""
  Setup mirrors the product: a tenant-scoped table, owned by the connecting role, with a
  policy comparing tenant_id to current_setting('app.current_tenant_id', true) -- the exact
  predicate of the nine Family A policies. Two rows, two tenants.

  All of it inside a scratch schema, in a transaction that is rolled back.
""")
    cur.execute(f"DROP SCHEMA IF EXISTS {SCRATCH} CASCADE")
    cur.execute(f"CREATE SCHEMA {SCRATCH}")
    cur.execute(f"SET search_path TO {SCRATCH}")
    cur.execute("CREATE TABLE t (id int primary key, tenant_id text, payload text)")
    cur.execute("INSERT INTO t VALUES (1,'11111111-1111-1111-1111-111111111111','a')")
    cur.execute("INSERT INTO t VALUES (2,'22222222-2222-2222-2222-222222222222','b')")
    cur.execute("""
        CREATE POLICY t_tenant_isolation ON t
        FOR ALL
        USING (tenant_id = current_setting('app.current_tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))
    """)

    def count(label: str) -> int:
        cur.execute("SELECT count(*) FROM t")
        n = cur.fetchone()[0]
        print(f"    {label:<58} rows visible = {n}")
        return n

    print("  (a) RLS not yet enabled -- baseline")
    base = count("no RLS, no GUC")

    print("\n  (b) ENABLE ROW LEVEL SECURITY, GUC still unset")
    cur.execute("ALTER TABLE t ENABLE ROW LEVEL SECURITY")
    en_noguc = count("ENABLE only, owner, GUC unset")

    print("\n  (c) FORCE ROW LEVEL SECURITY, GUC still unset  <-- THE DANGEROUS STATE")
    cur.execute("ALTER TABLE t FORCE ROW LEVEL SECURITY")
    force_noguc = count("ENABLE+FORCE, owner, GUC unset")

    print("\n  (d) FORCE still on, GUC now set to tenant 1")
    cur.execute("SELECT set_config('app.current_tenant_id','11111111-1111-1111-1111-111111111111',true)")
    force_guc = count("ENABLE+FORCE, owner, GUC = tenant 1")

    print("\n  (e) FORCE still on, GUC set to a tenant with no rows")
    cur.execute("SELECT set_config('app.current_tenant_id','33333333-3333-3333-3333-333333333333',true)")
    force_other = count("ENABLE+FORCE, owner, GUC = tenant 3 (no rows)")

    print("\n  (f) write path under FORCE with a mismatched GUC (WITH CHECK)")
    cur.execute("SELECT set_config('app.current_tenant_id','11111111-1111-1111-1111-111111111111',true)")
    try:
        cur.execute("INSERT INTO t VALUES (9,'22222222-2222-2222-2222-222222222222','x')")
        print("    INSERT of another tenant's row                              ACCEPTED  <-- leak")
        wcheck = "ACCEPTED"
    except Exception as exc:
        conn.rollback()
        wcheck = type(exc).__name__
        print(f"    INSERT of another tenant's row                              REFUSED ({wcheck})")

    # ------------------------------------------------------------------ verdict
    print("\n" + "-" * 78)
    print("  VERDICT")
    print("-" * 78)
    print(f"    baseline rows                              : {base}")
    print(f"    ENABLE only, owner, no GUC                 : {en_noguc}")
    print(f"    ENABLE+FORCE, owner, no GUC                : {force_noguc}")
    print(f"    ENABLE+FORCE, owner, correct GUC           : {force_guc}")
    print(f"    ENABLE+FORCE, owner, non-matching GUC      : {force_other}")
    print()
    if en_noguc == base and force_noguc == 0:
        print("    CONFIRMED, on a live server:")
        print("      * ENABLE alone does NOT restrict the table owner  (owner exemption real)")
        print("      * FORCE + unset GUC empties the owner's reads     (silent, no error)")
        print("      * FORCE + correct GUC restores exactly that tenant's rows")
        print()
        print("    => tenant_db_context.py's docstring warning is ACCURATE, and")
        print("       026_fee_records_row_level_security.sql:73-78 describes the real mechanism.")
        print("       Enabling FORCE before wiring the GUC would silently empty")
        print("       orders / positions / fills reads on the SQLAlchemy path.")
    elif en_noguc != base:
        print("    UNEXPECTED: ENABLE alone already restricted the owner.")
        print("    The owner-exemption premise does NOT hold on this server. Investigate before")
        print("    relying on any prior phase's reasoning.")
    else:
        print("    UNEXPECTED: FORCE did not empty the owner's reads.")
        print(f"    Expected 0, measured {force_noguc}. The warning may not apply as stated;")
        print("    do not quote it until this is explained.")

    print(f"\n    WITH CHECK on cross-tenant INSERT under FORCE: {wcheck}")

    # ---------------------------------------------------------------- teardown
    cur.execute("RESET search_path")
    cur.execute(f"DROP SCHEMA IF EXISTS {SCRATCH} CASCADE")
    conn.rollback()
    print("\n  transaction ROLLED BACK; scratch schema dropped. Cluster unchanged.")

    cur.execute("SELECT count(*) FROM pg_namespace WHERE nspname=%s", (SCRATCH,))
    print(f"  post-rollback scratch schema count = {cur.fetchone()[0]} (expect 0)")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
