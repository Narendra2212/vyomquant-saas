"""What is every PostgreSQL backend doing right now, and who is blocking whom."""
from __future__ import annotations

import os
import pathlib

import psycopg2
import psycopg2.extras

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN = (os.environ.get("AERORA_TEST_DATABASE_URL") or "").strip() or (
    (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
)

conn = psycopg2.connect(DSN, connect_timeout=10)
conn.autocommit = True
with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
    cur.execute(
        """
        SELECT pid, state, wait_event_type, wait_event,
               now() - xact_start AS xact_age,
               now() - query_start AS query_age,
               pg_blocking_pids(pid) AS blocked_by,
               left(regexp_replace(query, '\\s+', ' ', 'g'), 160) AS q
          FROM pg_stat_activity
         WHERE datname = current_database()
         ORDER BY xact_start NULLS LAST
        """
    )
    rows = cur.fetchall()
    print(f"BACKENDS={len(rows)}")
    for r in rows:
        print(
            f"  pid={r['pid']} state={r['state']} wait={r['wait_event_type']}/{r['wait_event']} "
            f"xact_age={r['xact_age']} query_age={r['query_age']} blocked_by={r['blocked_by']}"
        )
        print(f"      q={r['q']}")

    cur.execute(
        "SELECT locktype, mode, granted, pid, objid FROM pg_locks "
        "WHERE locktype = 'advisory' ORDER BY pid"
    )
    adv = cur.fetchall()
    print(f"ADVISORY_LOCKS={len(adv)}")
    for r in adv:
        print(f"  pid={r['pid']} mode={r['mode']} granted={r['granted']} objid={r['objid']}")
conn.close()
