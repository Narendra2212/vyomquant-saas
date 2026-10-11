"""Inventory the row level security actually present on the staging database.

Reads pg_policy / pg_class rather than the migration text, so what it reports is what
PostgreSQL ended up with after the whole provisioning order ran -- including the places
where a later file replaced an earlier file's policy.

Three distinctions this makes that reading the SQL cannot:

  * RLS enabled vs. RLS forced. A table's owner bypasses its policies unless FORCE ROW
    LEVEL SECURITY is set. Everything here is owned by `postgres`, so a connection that
    is the owner sees every row regardless of policy.
  * Policies that reference auth.uid() vs. policies that reference
    current_setting('app.current_tenant_id'). The second group is only live if something
    on the request path sets that GUC.
  * Whether `authenticated` even holds table privileges. A query that fails with
    `permission denied` is not evidence of working tenant isolation; it is evidence of a
    missing GRANT, and the two must not be conflated in a launch report.

Output is written as UTF-8 to staging/rls_inventory.txt.
"""

from __future__ import annotations

import collections
import pathlib
import sys

import psycopg2

REPO = pathlib.Path(__file__).resolve().parent.parent


def dsn() -> str:
    return (REPO / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()


QUERY_TABLES = """
SELECT c.relname,
       c.relrowsecurity,
       c.relforcerowsecurity,
       pg_get_userbyid(c.relowner) AS owner,
       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS n_policies
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relname
"""

QUERY_POLICIES = """
SELECT c.relname AS table_name,
       p.polname,
       p.polcmd,
       COALESCE(pg_get_expr(p.polqual, p.polrelid), '') AS using_expr,
       COALESCE(pg_get_expr(p.polwithcheck, p.polrelid), '') AS check_expr,
       COALESCE(array_to_string(ARRAY(
           SELECT pg_get_userbyid(r) FROM unnest(p.polroles) AS r
       ), ','), '') AS roles
FROM pg_policy p
JOIN pg_class c ON c.oid = p.polrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
ORDER BY c.relname, p.polname
"""

QUERY_GRANTS = """
SELECT table_name, grantee, string_agg(DISTINCT privilege_type, ',' ORDER BY privilege_type) AS privs
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('authenticated', 'anon', 'service_role')
GROUP BY table_name, grantee
ORDER BY table_name, grantee
"""

CMD = {"r": "SELECT", "a": "INSERT", "w": "UPDATE", "d": "DELETE", "*": "ALL"}


def main() -> int:
    out: list[str] = []

    def emit(line: str = "") -> None:
        out.append(line)

    with psycopg2.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute(QUERY_TABLES)
        tables = cur.fetchall()
        cur.execute(QUERY_POLICIES)
        policies = cur.fetchall()
        cur.execute(QUERY_GRANTS)
        grants = cur.fetchall()

    grant_map: dict[tuple[str, str], str] = {(t, g): p for t, g, p in grants}
    by_table: dict[str, list] = collections.defaultdict(list)
    for row in policies:
        by_table[row[0]].append(row)

    rls_on = [t for t in tables if t[1]]
    forced = [t for t in tables if t[2]]
    emit("=" * 78)
    emit("RLS INVENTORY (staging PostgreSQL, provisioned from repository source)")
    emit("=" * 78)
    emit(f"public tables                : {len(tables)}")
    emit(f"RLS enabled                  : {len(rls_on)}")
    emit(f"FORCE ROW LEVEL SECURITY     : {len(forced)}")
    emit(f"total policies               : {len(policies)}")
    emit()

    # Which predicate family does each policy belong to?
    fam = collections.Counter()
    tables_by_fam: dict[str, set[str]] = collections.defaultdict(set)
    for tname, _pn, _pc, using, check, _roles in policies:
        blob = f"{using} {check}"
        if "auth.uid()" in blob:
            key = "auth.uid()"
        elif "app.current_tenant_id" in blob:
            key = "app.current_tenant_id"
        elif "auth.role()" in blob or "service_role" in blob:
            key = "role-based"
        elif blob.strip() in ("", "true"):
            key = "unconditional (true)"
        else:
            key = "other"
        fam[key] += 1
        tables_by_fam[key].add(tname)
    emit("POLICY PREDICATE FAMILIES")
    for key, n in fam.most_common():
        emit(f"  {key:<28} {n:>4} policies over {len(tables_by_fam[key])} tables")
    emit()

    if tables_by_fam.get("app.current_tenant_id"):
        emit("Tables whose isolation depends on current_setting('app.current_tenant_id'):")
        for t in sorted(tables_by_fam["app.current_tenant_id"]):
            emit(f"  - {t}")
        emit()

    if tables_by_fam.get("unconditional (true)"):
        emit("Policies with an unconditional predicate (grant any row to their roles):")
        for t in sorted(tables_by_fam["unconditional (true)"]):
            for row in by_table[t]:
                if f"{row[3]} {row[4]}".strip() in ("", "true"):
                    emit(f"  - {t}.{row[1]}  cmd={CMD.get(row[2], row[2])}  roles={row[5] or 'PUBLIC'}")
        emit()

    emit("RLS ENABLED BUT ZERO POLICIES (deny-all for non-owner, non-bypass roles):")
    none_ = [t[0] for t in tables if t[1] and t[4] == 0]
    for t in none_:
        emit(f"  - {t}")
    if not none_:
        emit("  (none)")
    emit()

    emit("RLS DISABLED (no row filtering at all):")
    off = [t[0] for t in tables if not t[1]]
    for t in off:
        g = grant_map.get((t, "authenticated"), "")
        emit(f"  - {t:<44} authenticated={g or 'no grant'}")
    emit()

    emit("=" * 78)
    emit("PER-TABLE DETAIL (RLS-enabled tables only)")
    emit("=" * 78)
    for t in tables:
        name, rowsec, force, owner, npol = t
        if not rowsec:
            continue
        ga = grant_map.get((name, "authenticated"), "")
        gs = grant_map.get((name, "service_role"), "")
        emit(f"\n{name}  (owner={owner} force={force} policies={npol})")
        emit(f"  grants: authenticated=[{ga}] service_role=[{gs}]")
        for _tn, pn, pc, using, check, roles in by_table[name]:
            emit(f"  - {pn}  cmd={CMD.get(pc, pc)}  roles={roles or 'PUBLIC'}")
            if using:
                emit(f"      USING      {using}")
            if check:
                emit(f"      WITH CHECK {check}")

    text = "\n".join(out)
    (REPO / "staging" / "rls_inventory.txt").write_text(text, encoding="utf-8")
    # Print only the summary half; the per-table detail is long.
    head = text.split("PER-TABLE DETAIL")[0]
    print(head.encode("ascii", "replace").decode("ascii"))
    print(f"[full detail written to staging/rls_inventory.txt, {len(text)} chars]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
