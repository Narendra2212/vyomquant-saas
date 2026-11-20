"""Is `public.risk_settings` seeded by a trigger when a profile is created?

`staging/anon_read_sweep.py` has a `TRIGGER_SEEDED` set for tables whose row already
exists by the time the sweep tries to insert one. Adding `risk_settings` to it is only
correct if that is actually why the insert hits
`duplicate key value violates unique constraint "risk_settings_user_id_key"`.
The alternative — a stale row left by an earlier run — needs a different fix.
"""
from __future__ import annotations

import os
import pathlib
import uuid

import psycopg2
import psycopg2.extras

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN = (os.environ.get("AERORA_TEST_DATABASE_URL") or "").strip() or (
    (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
)
print(f"DSN={DSN}")

conn = psycopg2.connect(DSN, connect_timeout=10)
with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
    cur.execute(
        "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = to_regclass('public.risk_settings') ORDER BY 1"
    )
    for r in cur.fetchall():
        print(f"CONSTRAINT {r['conname']}: {r['def']}")

    cur.execute("SELECT count(*) FROM public.risk_settings")
    print(f"RISK_SETTINGS_ROWS={cur.fetchone()[0]}")
    cur.execute("SELECT count(*) FROM public.profiles")
    print(f"PROFILES_ROWS={cur.fetchone()[0]}")

    # Every trigger on profiles, and whether its function mentions risk_settings.
    cur.execute(
        """
        SELECT t.tgname,
               p.proname,
               pg_get_functiondef(p.oid) ILIKE '%risk_settings%' AS touches_risk_settings
          FROM pg_trigger t
          JOIN pg_class c ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_proc p ON p.oid = t.tgfoid
         WHERE n.nspname = 'public' AND c.relname = 'profiles' AND NOT t.tgisinternal
         ORDER BY 1
        """
    )
    rows = cur.fetchall()
    print(f"TRIGGERS_ON_PROFILES={len(rows)}")
    for r in rows:
        print(f"  {r['tgname']} -> {r['proname']}  touches_risk_settings={r['touches_risk_settings']}")

    # Any trigger anywhere whose function writes risk_settings.
    cur.execute(
        """
        SELECT n.nspname, c.relname AS on_table, t.tgname, p.proname
          FROM pg_trigger t
          JOIN pg_class c ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_proc p ON p.oid = t.tgfoid
         WHERE NOT t.tgisinternal
           AND pg_get_functiondef(p.oid) ILIKE '%insert into%risk_settings%'
         ORDER BY 1, 2, 3
        """
    )
    rows = cur.fetchall()
    print(f"TRIGGERS_INSERTING_RISK_SETTINGS={len(rows)}")
    for r in rows:
        print(f"  {r['nspname']}.{r['on_table']} {r['tgname']} -> {r['proname']}")

conn.rollback()

# The decisive experiment: create a profile in a transaction and see whether a
# risk_settings row appears without anyone inserting one. Rolled back.
probe_id = uuid.uuid4()
with conn.cursor() as cur:
    cur.execute("SELECT count(*) FROM public.risk_settings WHERE user_id = %s", (str(probe_id),))
    print(f"BEFORE_INSERT_RISK_ROWS_FOR_PROBE={cur.fetchone()[0]}")
    cur.execute(
        "INSERT INTO public.profiles (id, email) VALUES (%s, %s)",
        (str(probe_id), f"{probe_id}@example.invalid"),
    )
    cur.execute("SELECT count(*) FROM public.risk_settings WHERE user_id = %s", (str(probe_id),))
    n = cur.fetchone()[0]
    print(f"AFTER_PROFILE_INSERT_RISK_ROWS_FOR_PROBE={n}")
    print("VERDICT=" + ("TRIGGER_SEEDED" if n else "NOT_TRIGGER_SEEDED"))
conn.rollback()
conn.close()
