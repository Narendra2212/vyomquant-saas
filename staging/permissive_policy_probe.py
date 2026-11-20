"""Find tables where a permissive (qual = true) policy is reachable by anon or
authenticated, i.e. where RLS is enabled but does not actually constrain rows.

Exit 0  -> every such table is reference / catalogue data with no per-customer
           ownership column, so an unconstrained policy is not a tenant leak.
Exit 1  -> at least one table carrying a per-customer column is readable or
           writable without a row predicate. That is a tenant-isolation defect.

This is the complement of staging/exposure_check.py, which only looks at tables
where RLS is switched OFF. A table can have RLS enabled and still leak if its
policy predicate is the constant TRUE.
"""

from __future__ import annotations

import sys

import psycopg2

from dsn import resolve_dsn

# Columns that make a row belong to one specific customer.
#
# This exact list appears in three places and they must not drift apart, because a
# column missing from one of them is a table nobody checks:
#   * staging/exposure_check.py            (RLS switched OFF)
#   * staging/permissive_policy_probe.py   (RLS ON, predicate TRUE -- this file)
#   * migrations/hardening/999_privilege_hardening.sql section 4 (the provisioning gate)
# `creator_id` was absent from an earlier revision of the first one, and that omission
# is why `creator_analytics` -- creator_id plus total_earnings -- was missed.
OWNER_COLUMNS = (
    "user_id",
    "tenant_id",
    "profile_id",
    "owner_id",
    "author_id",
    "customer_id",
    "subscriber_id",
    "referrer_id",
    "created_by",
    "creator_id",
    "seller_id",
    "account_id",
)

QUERY = """
SELECT p.tablename,
       p.policyname,
       array_to_string(p.roles, ',') AS roles,
       p.cmd,
       (
         SELECT string_agg(c.column_name, ',' ORDER BY c.column_name)
         FROM information_schema.columns c
         WHERE c.table_schema = 'public'
           AND c.table_name = p.tablename
           AND c.column_name = ANY(%s)
       ) AS owner_columns
FROM pg_policies p
WHERE p.schemaname = 'public'
  AND p.permissive = 'PERMISSIVE'
  AND coalesce(p.qual, p.with_check) = 'true'
  AND (
        'anon' = ANY(p.roles)
     OR 'authenticated' = ANY(p.roles)
     OR 'public' = ANY(p.roles)
  )
ORDER BY p.tablename, p.policyname
"""


def main() -> int:
    conn = psycopg2.connect(resolve_dsn())
    conn.set_session(readonly=True, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(QUERY, (list(OWNER_COLUMNS),))
        rows = cur.fetchall()
    conn.close()

    leaks = [r for r in rows if r[4]]
    benign = [r for r in rows if not r[4]]

    print("=" * 78)
    print("PERMISSIVE (qual = TRUE) POLICIES REACHABLE BY anon / authenticated")
    print("=" * 78)
    print("A policy whose predicate is the constant TRUE applies no row filter.")
    print("RLS being ENABLED on the table is then irrelevant for that command.")
    print("-" * 78)
    print("A. TABLES WITH A PER-CUSTOMER COLUMN AND AN UNCONSTRAINED POLICY")
    print("-" * 78)
    if leaks:
        for table, policy, roles, cmd, owner in leaks:
            print(f"  {table:<44} {cmd:<7} {roles:<14} owner cols: {owner}")
            print(f"      policy: {policy}")
    else:
        print("  (none)")
    print("-" * 78)
    print("B. REFERENCE / CATALOGUE TABLES (no per-customer column)")
    print("-" * 78)
    for table, policy, roles, cmd, _ in benign:
        print(f"  {table:<44} {cmd:<7} {roles:<14} {policy}")
    print("-" * 78)
    print(f"SUMMARY: {len(leaks)} table(s) in group A, {len(benign)} in group B")

    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
