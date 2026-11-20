"""What does auth.users / profiles actually require to seed an identity?

The TENANT-01 matrix collapsed to 2 tables because the identity seed failed, so every
`*_user_id_fkey` failed behind it. This reports the real requirements and the enum labels
the generic synthesiser needs.
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

for schema, table in (("auth", "users"), ("public", "profiles")):
    print(f"=== {schema}.{table} NOT NULL columns without defaults ===")
    cur.execute(
        """
        SELECT column_name, data_type, udt_name, character_maximum_length, column_default
          FROM information_schema.columns
         WHERE table_schema=%s AND table_name=%s AND is_nullable='NO'
         ORDER BY ordinal_position
        """,
        (schema, table),
    )
    for name, dtype, udt, maxlen, default in cur.fetchall():
        print(f"   {name:28} {dtype:24} udt={udt:20} max={maxlen} default={default}")
    print()

print("=== FK target of profiles.id ===")
cur.execute(
    """
    SELECT ccu.table_schema, ccu.table_name, ccu.column_name
      FROM information_schema.table_constraints tc
      JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name
      JOIN information_schema.constraint_column_usage ccu
        ON ccu.constraint_name = tc.constraint_name
     WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_name='profiles'
    """
)
print("   ", cur.fetchall())

print()
print("=== can we actually insert an identity? ===")
uid = str(uuid.uuid4())
for attempt, sql, params in (
    ("auth.users(id) only", "INSERT INTO auth.users (id) VALUES (%s)", (uid,)),
):
    try:
        cur.execute("BEGIN")
        cur.execute(sql, params)
        cur.execute("ROLLBACK")
        print(f"   {attempt}: OK")
    except Exception as exc:
        cur.execute("ROLLBACK")
        print(f"   {attempt}: {type(exc).__name__}: {str(exc).splitlines()[0][:140]}")

print()
print("=== enum types in use on the target tables, with labels ===")
cur.execute(
    """
    SELECT DISTINCT c.table_name, c.column_name, c.udt_name
      FROM information_schema.columns c
      JOIN pg_type t ON t.typname = c.udt_name
     WHERE c.table_schema='public' AND t.typtype='e'
       AND c.table_name IN ('positions','execution_records','orders','fills',
                            'paper_orders','paper_positions','signals')
     ORDER BY 1,2
    """
)
rows = cur.fetchall()
for table, col, udt in rows:
    cur.execute(
        "SELECT string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder) "
        "FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid WHERE t.typname=%s",
        (udt,),
    )
    labels = cur.fetchone()[0]
    print(f"   {table}.{col} ({udt}) -> {labels}")

print()
print("=== check constraints on the paper/side tables ===")
cur.execute(
    """
    SELECT rel.relname, con.conname, pg_get_constraintdef(con.oid)
      FROM pg_constraint con
      JOIN pg_class rel ON rel.oid = con.conrelid
      JOIN pg_namespace n ON n.oid = rel.relnamespace AND n.nspname='public'
     WHERE con.contype='c'
       AND rel.relname IN ('paper_orders','paper_positions','library_subscriptions',
                           'signals','payment_methods')
     ORDER BY 1,2
    """
)
for relname, conname, defn in cur.fetchall():
    print(f"   {relname}.{conname}: {defn[:170]}")

conn.close()
print()
print(f"NONCE_END={NONCE}")
