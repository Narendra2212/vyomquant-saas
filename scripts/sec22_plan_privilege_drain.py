"""SEC-22: plan the next `authenticated` write-grant revokes. READ-ONLY. Prints SQL.

This script never writes to the database. It prints REVOKE statements for an operator to
review and run, because SEC-22's own remaining_action is explicit: "Drain it one table at
a time, each revoke paired with that feature's integration test... Do not sweep."

A table/privilege reaches the plan only when TWO INDEPENDENT arguments agree:

  (1) SOURCE. scripts/sec22_authenticated_write_inventory.py walks the import graph and
      resolves which database role each Supabase client maps to, through the local helper
      wrappers (`_sb`, `_get_supabase`, `_client`), FastAPI `Depends(...)` parameters, and
      clients passed as ordinary function arguments. It found no role-`authenticated`
      write to the table. Its negative control -- rediscovering the four call sites that
      three manual passes missed -- must pass, or this script refuses to produce a plan.

  (2) DATABASE. The table has RLS enabled and NO permissive policy an `authenticated`
      caller could satisfy for the command in question. A grant with no policy behind it
      is already inert, so revoking it cannot change behaviour whatever the source says.

Why two. Deriving this from source alone "proved unreliable three times running" (SEC-22's
own words), and each omission would have taken a working feature offline. Arguing from the
database alone would miss that a policy may exist for a feature that is merely not built
yet. Agreement is the evidence; disagreement means the table is EXCLUDED and reported,
never assumed safe.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGION = "ap-southeast-1"
SECRET = "vyomquant/production/database_url"
ROLE = "authenticated"
WRITE_PRIVS = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")
#: `.upsert()` may INSERT or UPDATE, so one upsert call site defends both privileges.
OP_IMPLIES = {"UPSERT": {"INSERT", "UPDATE"}, "INSERT": {"INSERT"},
              "UPDATE": {"UPDATE"}, "DELETE": {"DELETE"}}
INVENTORY = os.path.join(REPO, "reports",
                         "sec22_authenticated_write_inventory.json")


def production_dsn() -> str:
    out = subprocess.run(
        ["aws", "secretsmanager", "get-secret-value", "--region", REGION,
         "--secret-id", SECRET, "--query", "SecretString", "--output", "text"],
        capture_output=True, text=True, shell=True,
    )
    raw = (out.stdout or "").strip()
    if not raw:
        sys.exit(f"could not read {SECRET}: {(out.stderr or '').strip()[:300]}")
    try:
        obj = json.loads(raw)
        for k in ("DATABASE_URL", "database_url", "url", "dsn"):
            if k in obj:
                raw = obj[k]
                break
    except json.JSONDecodeError:
        pass
    # Catalog/DDL work goes to 5432, not the 6543 transaction pooler.
    return re.sub(r":6543/", ":5432/", raw.strip())


def load_allowlist():
    spec = importlib.util.spec_from_file_location(
        "cpp", os.path.join(REPO, "scripts", "check_production_privileges.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PATH_A_ALLOWLIST


def main() -> int:
    if not os.path.exists(INVENTORY):
        print("running the source inventory first...")
        rc = subprocess.run(
            [sys.executable, os.path.join("scripts",
                                          "sec22_authenticated_write_inventory.py")],
            cwd=REPO,
        ).returncode
        if rc != 0:
            print("the source inventory's negative control FAILED -- no plan produced.")
            return 1

    inv = json.load(open(INVENTORY, encoding="utf-8"))
    if not inv.get("negative_control_passed"):
        print("the source inventory's negative control FAILED -- no plan produced.")
        return 1

    auth_tables: dict[str, list] = inv["authenticated_tables"]
    unresolved = set(inv["unresolved_role_tables"])
    if inv["non_literal_sites"]:
        print("REFUSING: some write targets a table name that cannot be resolved, so")
        print("'nothing writes this table' cannot be concluded for ANY table:")
        for s in inv["non_literal_sites"]:
            print(f"    {s}")
        return 1

    allowlist = load_allowlist()

    import psycopg2
    conn = psycopg2.connect(production_dsn(), connect_timeout=30)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name, privilege_type
          FROM information_schema.role_table_grants
         WHERE table_schema='public' AND grantee=%s
           AND privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')
    """, (ROLE,))
    held: dict[str, set[str]] = {}
    for t, p in cur.fetchall():
        held.setdefault(t, set()).add(p)

    cur.execute("""
        SELECT c.relname, c.relrowsecurity
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname='public' AND c.relkind='r'
    """)
    rls = dict(cur.fetchall())

    cur.execute("SELECT tablename, cmd, roles FROM pg_policies WHERE schemaname='public'")
    policy_cmds: dict[str, set[str]] = {}
    for t, cmd, roles in cur.fetchall():
        rl = list(roles or [])
        if "authenticated" in rl or "public" in rl or not rl:
            policy_cmds.setdefault(t, set()).add(cmd.upper())
    conn.close()

    plan: dict[str, list[str]] = {}
    excluded: list[str] = []

    for table in sorted(held):
        privs = held[table] & set(WRITE_PRIVS)
        if not privs:
            continue
        allowed = set(allowlist.get(table, {}).get(ROLE, set()))
        seen = set()
        for op in auth_tables.get(table, []):
            seen |= OP_IMPLIES.get(op, {op})
        cmds = policy_cmds.get(table, set())

        for priv in sorted(privs):
            if priv in allowed:
                continue  # the allowlist says path-(A) needs it
            if priv in seen:
                excluded.append(f"{table}.{priv}: source shows a path-(A) {priv}")
            elif table in unresolved:
                excluded.append(f"{table}.{priv}: a writer's client role is UNRESOLVED")
            elif not rls.get(table, False):
                excluded.append(f"{table}.{priv}: RLS is DISABLED, the grant is the "
                                f"only control")
            elif "ALL" in cmds:
                excluded.append(f"{table}.{priv}: an RLS policy with cmd=ALL admits it")
            elif priv in cmds:
                excluded.append(f"{table}.{priv}: an RLS policy names {priv}")
            else:
                plan.setdefault(table, []).append(priv)

    total = sum(len(v) for v in plan.values())
    print(f"=== PLAN: {total} grant(s) on {len(plan)} table(s) ===")
    if plan:
        print("BEGIN;")
        for t, privs in sorted(plan.items()):
            print(f"REVOKE {', '.join(privs)} ON public.{t} FROM {ROLE};")
        print("COMMIT;")
        print("\n-- rollback")
        print("BEGIN;")
        for t, privs in sorted(plan.items()):
            print(f"GRANT {', '.join(privs)} ON public.{t} TO {ROLE};")
        print("COMMIT;")
    else:
        print("  nothing new is provably safe; every remaining grant needs a per-feature")
        print("  decision. See the exclusions below for which argument blocks each one.")

    print(f"\n=== EXCLUDED, with the blocking reason: {len(excluded)} ===")
    for line in excluded:
        print(f"  {line}")
    print("\nAfter applying any of the above, run:")
    print("  python scripts/check_production_privileges.py")
    print("and confirm 'path-(A) code retains every write privilege it needs'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
