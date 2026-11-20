"""Why can the `anon` role read `fee_records` when 43 other tables block it?

Two different mechanisms could allow it and the fix differs:
  * RLS is not enabled on the table (or has a permissive policy), or
  * the `anon` role holds a SELECT GRANT that the other 43 tables do not.

Measured, not guessed.
"""

import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

dsn = (
    pathlib.Path(__file__).resolve().parent / "pg_dsn.txt"
).read_text(encoding="utf-8").strip()

conn = psycopg2.connect(dsn, connect_timeout=10)
cur = conn.cursor()

TABLES = ("fee_records", "paper_orders", "invoices", "strategies")

print("=== relrowsecurity / relforcerowsecurity ===")
cur.execute(
    """
    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = ANY(%s)
    ORDER BY c.relname
    """,
    (list(TABLES),),
)
for name, rls, forced in cur.fetchall():
    print(f"{name:20} rls_enabled={rls} forced={forced}")

print("\n=== policies ===")
cur.execute(
    """
    SELECT tablename, policyname, cmd, roles::text, qual
    FROM pg_policies
    WHERE schemaname = 'public' AND tablename = ANY(%s)
    ORDER BY tablename, policyname
    """,
    (list(TABLES),),
)
rows = cur.fetchall()
if not rows:
    print("(no policies on any of these tables)")
for table, policy, cmd, roles, qual in rows:
    print(f"{table:20} {policy:40} {cmd:8} roles={roles} qual={str(qual)[:80]}")

print("\n=== grants to anon / authenticated ===")
cur.execute(
    """
    SELECT table_name, grantee, privilege_type
    FROM information_schema.role_table_grants
    WHERE table_schema = 'public'
      AND table_name = ANY(%s)
      AND grantee IN ('anon', 'authenticated', 'PUBLIC')
    ORDER BY table_name, grantee, privilege_type
    """,
    (list(TABLES),),
)
rows = cur.fetchall()
if not rows:
    print("(no grants to anon/authenticated/PUBLIC on any of these tables)")
for table, grantee, privilege in rows:
    print(f"{table:20} {grantee:15} {privilege}")

print("\n=== how many public tables grant SELECT to anon ===")
cur.execute(
    """
    SELECT count(DISTINCT table_name)
    FROM information_schema.role_table_grants
    WHERE table_schema = 'public' AND grantee = 'anon' AND privilege_type = 'SELECT'
    """
)
print(f"ANON_SELECTABLE_TABLES={cur.fetchone()[0]}")

cur.execute(
    """
    SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity = false
    """
)
print(f"PUBLIC_TABLES_WITHOUT_RLS={cur.fetchone()[0]}")

cur.execute(
    """
    SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity = false
    ORDER BY c.relname LIMIT 25
    """
)
print("WITHOUT_RLS_SAMPLE=" + ", ".join(r[0] for r in cur.fetchall()))

cur.close()
conn.close()
