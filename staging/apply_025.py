"""Apply migration 025 (WORK-03 bot ownership claims) to the LOCAL test database."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import psycopg2

NONCE = sys.argv[1] if len(sys.argv) > 1 else uuid.uuid4().hex[:12]
print(f"NONCE={NONCE}")

DSN = "postgresql://postgres:@127.0.0.1:62674/postgres"
path = Path(__file__).resolve().parent.parent / "backend_app" / "migrations" / \
    "025_work03_bot_ownership_claims.sql"
sql = path.read_text(encoding="utf-8")
print(f"applying {path.name} ({len(sql)} bytes)")

conn = psycopg2.connect(DSN)
conn.autocommit = True
cur = conn.cursor()


class _Notices:
    pass


try:
    cur.execute(sql)
    print("APPLIED OK")
except Exception as exc:
    print(f"FAILED: {type(exc).__name__}: {exc}")
    sys.exit(1)

for notice in conn.notices:
    print("   " + notice.strip())

print()
print("=== verification ===")
cur.execute(
    "SELECT column_name, data_type FROM information_schema.columns "
    "WHERE table_name='bot_ownership_claims' ORDER BY ordinal_position"
)
for r in cur.fetchall():
    print(f"   {r[0]:14} {r[1]}")

cur.execute(
    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE conrelid='public.bot_ownership_claims'::regclass ORDER BY conname"
)
print()
for name, defn in cur.fetchall():
    print(f"   {name}: {defn[:110]}")

cur.execute(
    "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
    "WHERE table_name='bot_ownership_claims' ORDER BY grantee, privilege_type"
)
print()
for g in cur.fetchall():
    print(f"   grant {g[0]:16} {g[1]}")

conn.close()
print()
print(f"NONCE_END={NONCE}")
