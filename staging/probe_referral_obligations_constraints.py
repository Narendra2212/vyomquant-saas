"""Read-only: dump referral_obligations columns, NOT NULL/no-default set, CHECK
constraints and FKs, so the sweep CHECK_OVERRIDES entry is grounded in the real schema
rather than guessed."""
from __future__ import annotations

import pathlib
import psycopg2

ROOT = pathlib.Path(__file__).resolve().parents[1]
dsn = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()

c = psycopg2.connect(dsn)
cur = c.cursor()

print("=== columns (NOT NULL, no default, not generated) ===")
cur.execute("""
    SELECT column_name, data_type, udt_name, character_maximum_length
    FROM information_schema.columns
    WHERE table_schema='public' AND table_name='referral_obligations'
      AND is_nullable='NO' AND column_default IS NULL AND is_generated='NEVER'
    ORDER BY ordinal_position
""")
for r in cur.fetchall():
    print(" ", r)

print("\n=== ALL columns (for context) ===")
cur.execute("""
    SELECT column_name, data_type, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema='public' AND table_name='referral_obligations'
    ORDER BY ordinal_position
""")
for r in cur.fetchall():
    print(" ", r)

print("\n=== CHECK constraints ===")
cur.execute("""
    SELECT con.conname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class c ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname='public'
    WHERE c.relname='referral_obligations' AND con.contype='c'
    ORDER BY con.conname
""")
for r in cur.fetchall():
    print(" ", r[0])
    print("    ", r[1])

print("\n=== UNIQUE / PK constraints ===")
cur.execute("""
    SELECT con.conname, con.contype, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class c ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname='public'
    WHERE c.relname='referral_obligations' AND con.contype IN ('u','p')
    ORDER BY con.conname
""")
for r in cur.fetchall():
    print(" ", r[0], r[1])
    print("    ", r[2])

print("\n=== FOREIGN KEYS ===")
cur.execute("""
    SELECT con.conname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class c ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname='public'
    WHERE c.relname='referral_obligations' AND con.contype='f'
    ORDER BY con.conname
""")
for r in cur.fetchall():
    print(" ", r[0])
    print("    ", r[1])

c.close()
