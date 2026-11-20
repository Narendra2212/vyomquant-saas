"""Is the shared test PostgreSQL out of connection slots? Read-only."""
from __future__ import annotations

import pathlib

import psycopg2

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()

c = psycopg2.connect(DSN, connect_timeout=10)
cur = c.cursor()
cur.execute("show max_connections")
print("max_connections      =", cur.fetchone()[0])
cur.execute("select count(*) from pg_stat_activity")
print("current backends     =", cur.fetchone()[0])
cur.execute("show superuser_reserved_connections")
print("superuser_reserved   =", cur.fetchone()[0])
cur.execute("""
    select state, count(*) from pg_stat_activity group by state order by 2 desc
""")
print("by state:")
for state, n in cur.fetchall():
    print(f"   {state!r:<28} {n}")
cur.execute("""
    select application_name, count(*) from pg_stat_activity
     group by application_name order by 2 desc limit 10
""")
print("by application_name:")
for app, n in cur.fetchall():
    print(f"   {app!r:<28} {n}")
cur.execute("""
    select count(*) from pg_stat_activity
     where state = 'idle in transaction'
""")
print("idle in transaction  =", cur.fetchone()[0])
c.close()
