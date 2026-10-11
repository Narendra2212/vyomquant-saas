"""Is the paper suite blocked on a PostgreSQL lock left by the run I interrupted?

Read-only by default. Pass ``--terminate-idle-in-transaction`` to end backends that are
sitting in ``idle in transaction`` -- which is what an interrupted pytest leaves behind.
This is the LOCAL test instance in ``staging/pgdata_r1``, not a production database.
"""
from __future__ import annotations

import os
import pathlib
import sys

import psycopg2

#: The port changes whenever `pgserver` restarts, so read the file bootstrap_pg.py writes
#: rather than hard-coding what it was last time.
DSN = (
    os.environ.get("AERORA_TEST_DATABASE_URL")
    or (pathlib.Path(__file__).resolve().parent / "pg_dsn.txt").read_text(encoding="utf-8").strip()
)


def main() -> int:
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("SELECT pg_backend_pid()")
    me = cur.fetchone()[0]

    cur.execute(
        """
        SELECT pid, state, wait_event_type, wait_event,
               now() - xact_start AS xact_age,
               left(coalesce(query, ''), 110)
          FROM pg_stat_activity
         WHERE datname = current_database()
         ORDER BY xact_start NULLS LAST
        """
    )
    rows = cur.fetchall()
    print(f"BACKENDS={len(rows)}  (this probe is pid {me})")
    for pid, state, wet, we, age, query in rows:
        mark = " <-- me" if pid == me else ""
        print(f"  pid={pid:<6} state={str(state):<22} wait={str(wet)}/{str(we)}")
        print(f"         xact_age={age}  q={query!r}{mark}")

    cur.execute(
        """
        SELECT blocked.pid, blocking.pid
          FROM pg_locks blocked
          JOIN pg_locks blocking
            ON blocking.locktype = blocked.locktype
           AND blocking.database IS NOT DISTINCT FROM blocked.database
           AND blocking.relation IS NOT DISTINCT FROM blocked.relation
           AND blocking.transactionid IS NOT DISTINCT FROM blocked.transactionid
           AND blocking.pid <> blocked.pid
           AND blocking.granted
         WHERE NOT blocked.granted
        """
    )
    waits = cur.fetchall()
    print(f"BLOCKED_PAIRS={len(waits)}")
    for blocked_pid, blocking_pid in waits:
        print(f"  pid {blocked_pid} is waiting on pid {blocking_pid}")

    if "--terminate-idle-in-transaction" in sys.argv:
        cur.execute(
            """
            SELECT pg_terminate_backend(pid), pid
              FROM pg_stat_activity
             WHERE datname = current_database()
               AND pid <> pg_backend_pid()
               AND state IN ('idle in transaction',
                             'idle in transaction (aborted)')
            """
        )
        killed = cur.fetchall()
        print(f"TERMINATED={len(killed)} {[p for _, p in killed]}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
