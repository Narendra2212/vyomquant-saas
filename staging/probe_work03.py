"""WORK-03: what does the schema already give me for a cross-pod bot claim?

Before inventing a table, establish what exists: strategy_deployments' shape and its
one-live index, whether a claims table already exists, and what FleetManager could key on.
"""
from __future__ import annotations

import sys
import uuid

import psycopg2

NONCE = sys.argv[1] if len(sys.argv) > 1 else uuid.uuid4().hex[:12]
print(f"NONCE={NONCE}")

conn = psycopg2.connect("postgresql://postgres:@127.0.0.1:62674/postgres")
conn.autocommit = True
cur = conn.cursor()

print("=== strategy_deployments columns ===")
cur.execute(
    "SELECT column_name, data_type, is_nullable, column_default "
    "FROM information_schema.columns WHERE table_name='strategy_deployments' "
    "ORDER BY ordinal_position"
)
for r in cur.fetchall():
    print(f"   {r[0]:26} {r[1]:28} null={r[2]:4} default={str(r[3])[:40]}")

print()
print("=== indexes on strategy_deployments ===")
cur.execute(
    "SELECT indexname, indexdef FROM pg_indexes "
    "WHERE tablename='strategy_deployments' ORDER BY indexname"
)
for name, defn in cur.fetchall():
    print(f"   {name}")
    print(f"      {defn[:200]}")

print()
print("=== does any bot/ownership claim table already exist? ===")
cur.execute(
    "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
    "AND (table_name LIKE '%claim%' OR table_name LIKE '%ownership%' "
    "OR table_name LIKE '%bot%' OR table_name LIKE '%lease%') ORDER BY 1"
)
rows = cur.fetchall()
print("   " + (", ".join(r[0] for r in rows) if rows else "NONE"))

print()
print("=== applied migrations (if tracked) ===")
cur.execute(
    "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
    "AND table_name IN ('schema_migrations','_migrations','alembic_version')"
)
tracked = [r[0] for r in cur.fetchall()]
print("   tracking tables:", tracked or "none")
for t in tracked:
    try:
        cur.execute(f'SELECT * FROM public."{t}" ORDER BY 1 DESC LIMIT 8')
        print(f"   {t}: {cur.fetchall()}")
    except Exception as exc:
        print(f"   {t}: {exc}")

print()
print("=== gen_random_uuid available? ===")
cur.execute("SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname='gen_random_uuid')")
print("   gen_random_uuid:", cur.fetchone()[0])

conn.close()
print()
print(f"NONCE_END={NONCE}")
