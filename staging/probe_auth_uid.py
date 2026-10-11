"""How does auth.uid() resolve here, and does RLS actually bite for `authenticated`?

Needed before writing the cross-tenant matrix: the test has to impersonate a request the
way PostgREST does, or it will prove nothing.
"""
from __future__ import annotations

import sys
import uuid

import psycopg2

NONCE = sys.argv[1] if len(sys.argv) > 1 else uuid.uuid4().hex[:12]
print(f"NONCE={NONCE}")

DSN = "postgresql://postgres:@127.0.0.1:62674/postgres"
conn = psycopg2.connect(DSN)
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='auth' AND p.proname='uid'")
row = cur.fetchone()
print("=== auth.uid() definition ===")
print(row[0] if row else "NOT FOUND")

print()
print("=== the three policies on `orders`, in full ===")
cur.execute(
    """
    SELECT p.polname, p.polcmd, pg_get_userbyid(r.oid) AS role,
           pg_catalog.pg_get_expr(p.polqual, p.polrelid)     AS using_expr,
           pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid) AS check_expr
      FROM pg_policy p
      JOIN pg_class c ON c.oid = p.polrelid AND c.relname = 'orders'
      LEFT JOIN LATERAL unnest(p.polroles) AS pr(oid) ON true
      LEFT JOIN pg_roles r ON r.oid = pr.oid
     ORDER BY p.polname
    """
)
for name, cmd, role, using, check in cur.fetchall():
    print(f"  {name} cmd={cmd} role={role}")
    print(f"      USING      : {using}")
    print(f"      WITH CHECK : {check}")

print()
print("=== does RLS bite for `authenticated`? (empirical) ===")
tid_a = str(uuid.uuid4())
tid_b = str(uuid.uuid4())
print(f"tenant A = {tid_a}")
print(f"tenant B = {tid_b}")

cur.execute("SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name='orders' ORDER BY ordinal_position")
cols = cur.fetchall()
print()
print("orders columns:")
for c in cols:
    print(f"   {c[0]:28} {c[1]:28} nullable={c[2]}")

print()
print("=== grants on orders ===")
cur.execute("SELECT grantee, privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name='orders' AND grantee IN ('anon','authenticated','service_role') "
            "ORDER BY grantee, privilege_type")
for g in cur.fetchall():
    print(f"   {g[0]:16} {g[1]}")

conn.close()
print()
print(f"NONCE_END={NONCE}")
