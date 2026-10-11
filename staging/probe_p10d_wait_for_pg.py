"""Poll the shared test PostgreSQL until it finishes crash recovery and accepts a
connection. Read-only; does not start, stop or reconfigure the server."""
from __future__ import annotations

import pathlib
import sys
import time

import psycopg2

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()

DEADLINE = time.time() + 240
attempt = 0
while time.time() < DEADLINE:
    attempt += 1
    try:
        c = psycopg2.connect(DSN, connect_timeout=5)
        cur = c.cursor()
        cur.execute("select count(*) from information_schema.tables "
                    "where table_schema='public'")
        ntables = cur.fetchone()[0]
        cur.execute("select pg_is_in_recovery()")
        in_recovery = cur.fetchone()[0]
        cur.execute("select version()")
        ver = cur.fetchone()[0].split(",")[0]
        print(f"UP after {attempt} attempt(s): public tables={ntables} "
              f"in_recovery={in_recovery}")
        print(f"   {ver}")
        # Confirm the state Workstream D depends on survived the crash recovery.
        cur.execute("select is_nullable from information_schema.columns "
                    "where table_schema='public' and table_name='fee_records' "
                    "  and column_name='fee_pct'")
        row = cur.fetchone()
        print("   fee_records.fee_pct is_nullable =", row[0] if row else "ABSENT")
        cur.execute("select column_name from information_schema.columns "
                    "where table_schema='public' and table_name='referral_payouts' "
                    "  and column_name in ('payout_method','payment_method')")
        print("   referral_payouts method column  =",
              [r[0] for r in cur.fetchall()])
        c.close()
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        msg = str(e).strip().splitlines()[0][:110]
        print(f"  attempt {attempt}: {type(e).__name__}: {msg}")
        time.sleep(10)

print("STILL DOWN after 240s — PostgreSQL has not completed recovery.")
sys.exit(1)
