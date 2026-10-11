"""Re-apply migrations/hardening/999_privilege_hardening.sql against the staging
database and surface every NOTICE / EXCEPTION it raises.

The file is a declarative end state, not a one-time migration, so running it twice
must be safe and must produce the same result both times.
"""

from __future__ import annotations

import os
import sys

import psycopg2
import psycopg2.extensions

SQL_PATH = os.path.join("migrations", "hardening", "999_privilege_hardening.sql")


def main() -> int:
    dsn = os.environ.get("AERORA_TEST_DATABASE_URL")
    if not dsn:
        with open(os.path.join("staging", "pg_dsn.txt"), encoding="utf-8") as fh:
            dsn = fh.read().strip()

    with open(SQL_PATH, encoding="utf-8") as fh:
        sql = fh.read()

    conn = psycopg2.connect(dsn)
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
    except psycopg2.Error as exc:
        print("HARDENING RAISED:")
        print(f"  {exc}")
        conn.close()
        return 1

    for notice in conn.notices:
        print(notice.rstrip())
    conn.close()
    print(f"applied {SQL_PATH} with no exception")
    return 0


if __name__ == "__main__":
    sys.exit(main())
