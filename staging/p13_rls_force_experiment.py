"""Does FORCE ROW LEVEL SECURITY empty a table OWNER's reads? Corrected experiment.

WHY THERE IS A SECOND VERSION OF THIS
-------------------------------------
``staging/p13_rls_live_probe.py`` ran this experiment and measured "FORCE did not empty the
owner's reads", contradicting ``tenant_db_context.py:13-19``. That result was an artefact of
the harness, not a finding, and the harness printed the reason itself in its own section 1:

    current_user : postgres
    rolsuper     : True
    rolbypassrls : True

A PostgreSQL SUPERUSER bypasses row-level security unconditionally -- ENABLE and FORCE alike.
Testing FORCE while connected as a superuser tests nothing. The first run's verdict is
withdrawn, and this file replaces it.

WHAT THE PRODUCTION SITUATION ACTUALLY IS
-----------------------------------------
``backend_app/migrations/026_fee_records_row_level_security.sql:73-78`` states that the
application connects as ``postgres`` and that ``postgres`` OWNS the tables. Live
introspection of the test cluster confirms the ownership half for all 11 tenant-scoped
tables (``staging/p13_rls_live.txt`` section 2: ``owner=postgres`` x11).

But OWNER and SUPERUSER are different exemptions, and they behave differently under FORCE:

  * a table OWNER is exempt from its own policies under ENABLE, and that exemption is
    REMOVED by FORCE;
  * a SUPERUSER is exempt under both, and FORCE cannot remove it.

On this local cluster ``postgres`` is a superuser. On Supabase the ``postgres`` role is NOT
a superuser. So the local cluster cannot reproduce production RLS behaviour while connected
as ``postgres`` -- which is a finding in its own right, and the reason this experiment
provisions its own non-superuser owner role instead.

SAFETY
------
Scratch schema and a scratch role, both created and dropped inside one transaction that is
ROLLED BACK. ``CREATE ROLE`` is transactional in PostgreSQL; the teardown verifies the role
is gone after rollback rather than assuming it. No existing table, policy, role or row is
modified. No row contents, credentials or tokens are printed. Target is the local test
cluster only.
"""

from __future__ import annotations

import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRATCH = "p13_force_probe"
ROLE = "p13_force_owner"

T1 = "11111111-1111-1111-1111-111111111111"
T2 = "22222222-2222-2222-2222-222222222222"
T3 = "33333333-3333-3333-3333-333333333333"


def main() -> int:
    import psycopg2

    dsn = (ROOT / "staging" / "pg_dsn.txt").read_text().strip()
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()

    print("=" * 78)
    print("PHASE 13 - FORCE RLS vs TABLE OWNER (corrected)")
    print("=" * 78)

    cur.execute(
        "SELECT current_user,"
        " (SELECT rolsuper FROM pg_roles WHERE rolname=current_user),"
        " (SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user)"
    )
    cu, sup, byp = cur.fetchone()
    print(f"\nharness connects as {cu!r}  rolsuper={sup}  rolbypassrls={byp}")
    print("that role is used only to SET UP the experiment, never to measure it.\n")

    # ---------------------------------------------------------------- provision
    cur.execute(f"DROP SCHEMA IF EXISTS {SCRATCH} CASCADE")
    cur.execute(f"CREATE SCHEMA {SCRATCH}")
    cur.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{ROLE}') "
        f"THEN EXECUTE 'DROP OWNED BY {ROLE}'; EXECUTE 'DROP ROLE {ROLE}'; END IF; END $$;"
    )
    cur.execute(f"CREATE ROLE {ROLE} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE LOGIN")
    cur.execute(f"GRANT ALL ON SCHEMA {SCRATCH} TO {ROLE}")

    cur.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=%s", (ROLE,)
    )
    r_sup, r_byp = cur.fetchone()
    print(f"scratch role {ROLE!r}: rolsuper={r_sup}  rolbypassrls={r_byp}")
    if r_sup or r_byp:
        print("  ABORT: scratch role is still exempt; the experiment would be invalid again.")
        conn.rollback()
        return 1
    print("  neither exemption present -> FORCE can actually apply to this role.\n")

    cur.execute(f"SET search_path TO {SCRATCH}")
    cur.execute("CREATE TABLE t (id int primary key, tenant_id text, payload text)")
    cur.execute("INSERT INTO t VALUES (1,%s,'a'), (2,%s,'b')", (T1, T2))
    cur.execute(f"ALTER TABLE t OWNER TO {ROLE}")
    cur.execute("""
        CREATE POLICY t_tenant_isolation ON t
        FOR ALL
        USING (tenant_id = current_setting('app.current_tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))
    """)
    cur.execute(
        "SELECT pg_get_userbyid(relowner) FROM pg_class c JOIN pg_namespace n"
        " ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname='t'",
        (SCRATCH,),
    )
    print(f"table owner is now {cur.fetchone()[0]!r}; policy predicate matches Family A.\n")

    def measure(label: str) -> object:
        # Savepoint rather than conn.rollback(): a full rollback here would discard the
        # whole experiment including the scratch role, and then report a misleading number.
        cur.execute("SAVEPOINT m")
        try:
            cur.execute("SELECT count(*) FROM t")
            n = cur.fetchone()[0]
            cur.execute("RELEASE SAVEPOINT m")
            print(f"  {label:<52} rows = {n}")
            return n
        except Exception as exc:
            cur.execute("ROLLBACK TO SAVEPOINT m")
            print(f"  {label:<52} ERROR {type(exc).__name__}")
            return type(exc).__name__

    print("-" * 78)
    print(f"MEASURED AS THE TABLE OWNER ({ROLE}, not a superuser)")
    print("-" * 78)

    cur.execute(f"SET ROLE {ROLE}")
    cur.execute("SELECT current_user")
    print(f"  (now acting as {cur.fetchone()[0]!r})\n")

    print("(a) no RLS yet")
    a = measure("baseline, no RLS, no GUC")

    cur.execute("RESET ROLE")
    cur.execute("ALTER TABLE t ENABLE ROW LEVEL SECURITY")
    cur.execute(f"SET ROLE {ROLE}")
    print("\n(b) ENABLE only  -- owner exemption should still apply")
    b = measure("ENABLE, owner, GUC unset")

    cur.execute("RESET ROLE")
    cur.execute("ALTER TABLE t FORCE ROW LEVEL SECURITY")
    cur.execute(f"SET ROLE {ROLE}")
    print("\n(c) ENABLE+FORCE, GUC unset  <-- THE DANGEROUS STATE")
    c = measure("ENABLE+FORCE, owner, GUC unset")

    print("\n(d) ENABLE+FORCE, GUC = tenant 1")
    cur.execute("SELECT set_config('app.current_tenant_id',%s,true)", (T1,))
    d = measure("ENABLE+FORCE, owner, GUC = tenant 1")

    print("\n(e) ENABLE+FORCE, GUC = tenant 3 (owns no rows)")
    cur.execute("SELECT set_config('app.current_tenant_id',%s,true)", (T3,))
    e = measure("ENABLE+FORCE, owner, GUC = tenant 3")

    print("\n(f) WITH CHECK: insert another tenant's row while GUC = tenant 1")
    cur.execute("SELECT set_config('app.current_tenant_id',%s,true)", (T1,))
    # SAVEPOINT, because a refused INSERT aborts the whole transaction otherwise and the
    # teardown below can no longer run -- which is exactly how the first run of this file
    # ended, leaving a scratch ROLE whose cleanup could not be verified.
    cur.execute("SAVEPOINT before_cross_tenant_insert")
    try:
        cur.execute("INSERT INTO t VALUES (9,%s,'x')", (T2,))
        wcheck = "ACCEPTED (cross-tenant write permitted)"
        cur.execute("ROLLBACK TO SAVEPOINT before_cross_tenant_insert")
    except Exception as exc:
        # PostgreSQL reports a WITH CHECK violation as SQLSTATE 42501
        # (insufficient_privilege) with the message "new row violates row-level security
        # policy", so the class name alone is ambiguous. Record the code and the message.
        code = getattr(exc, "pgcode", None)
        msg = str(getattr(exc, "pgerror", exc) or exc).strip().splitlines()[0]
        wcheck = f"REFUSED ({type(exc).__name__}, SQLSTATE={code})"
        cur.execute("ROLLBACK TO SAVEPOINT before_cross_tenant_insert")
        print(f"  server message: {msg}")
    print(f"  {wcheck}")

    cur.execute("RESET ROLE")

    # ------------------------------------------------------------------ verdict
    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    print(f"  (a) baseline, no RLS                  : {a}")
    print(f"  (b) ENABLE,       owner, GUC unset    : {b}")
    print(f"  (c) ENABLE+FORCE, owner, GUC unset    : {c}")
    print(f"  (d) ENABLE+FORCE, owner, GUC=tenant1  : {d}")
    print(f"  (e) ENABLE+FORCE, owner, GUC=tenant3  : {e}")
    print(f"  (f) cross-tenant INSERT under FORCE   : {wcheck}")
    print()

    owner_exempt_under_enable = (b == a)
    force_empties = (c == 0)
    guc_restores = (d == 1)
    other_tenant_empty = (e == 0)

    print(f"  owner exempt under ENABLE alone       : {owner_exempt_under_enable}")
    print(f"  FORCE empties reads with GUC unset    : {force_empties}")
    print(f"  correct GUC restores that tenant only : {guc_restores}")
    print(f"  non-matching GUC yields zero rows     : {other_tenant_empty}")
    print()

    if owner_exempt_under_enable and force_empties and guc_restores:
        print("  CONFIRMED ON A LIVE SERVER:")
        print("    * ENABLE alone leaves the table owner unrestricted.")
        print("    * FORCE removes that exemption, and with the GUC unset the owner sees")
        print("      ZERO rows -- silently, with no error raised.")
        print("    * Setting the GUC restores exactly the matching tenant's rows.")
        print()
        print("  => tenant_db_context.py:13-19 is ACCURATE. Enabling FORCE before the GUC is")
        print("     wired would convert reads on orders/positions/fills into silently empty")
        print("     result sets on the SQLAlchemy path. The sequencing constraint is REAL.")
    else:
        print("  NOT CONFIRMED as stated. Record the measured numbers above and do not")
        print("  quote the docstring warning until the difference is explained.")

    # ---------------------------------------------------------------- teardown
    cur.execute("RESET search_path")
    cur.execute(f"DROP SCHEMA IF EXISTS {SCRATCH} CASCADE")
    cur.execute(f"DROP OWNED BY {ROLE}")
    cur.execute(f"DROP ROLE IF EXISTS {ROLE}")
    conn.rollback()

    cur.execute("SELECT count(*) FROM pg_namespace WHERE nspname=%s", (SCRATCH,))
    ns = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM pg_roles WHERE rolname=%s", (ROLE,))
    rl = cur.fetchone()[0]
    print(f"\n  rolled back. scratch schema count={ns} (expect 0), scratch role count={rl} (expect 0)")
    if ns or rl:
        print("  WARNING: teardown left residue. Investigate before trusting the cluster state.")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
