"""What does a minimal auth.users insert need, and what references it?

The LIB-01 seeds failed on ``strategies_user_id_fkey`` -> ``auth.users(id)``. To seed a
synthetic owner the test has to create the user row first, so this reports every column that
is NOT NULL with no default (i.e. must be supplied) and every FK that points at auth.users
from the three tables the clone path touches.
"""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DSN = (
    os.environ.get("AERORA_TEST_DATABASE_URL")
    or (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
)

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402


def main() -> int:
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        "SELECT column_name, data_type, is_nullable, column_default "
        "  FROM information_schema.columns "
        " WHERE table_schema = 'auth' AND table_name = 'users' "
        " ORDER BY ordinal_position"
    )
    rows = cur.fetchall()
    if not rows:
        print("auth.users: ABSENT")
        return 0
    print(f"auth.users: PRESENT ({len(rows)} columns)")
    required = [
        r for r in rows if r["is_nullable"] == "NO" and not r["column_default"]
    ]
    print("  MUST BE SUPPLIED (NOT NULL, no default):")
    for row in required:
        print(f"    {row['column_name']:<28} {row['data_type']}")
    if not required:
        print("    (none - only the primary key matters)")

    print("\n  all NOT NULL columns, with their defaults:")
    for row in rows:
        if row["is_nullable"] == "NO":
            print(
                f"    {row['column_name']:<28} {row['data_type']:<26} "
                f"default={(row['column_default'] or '<none>')[:40]}"
            )

    print("\nFOREIGN KEYS into auth.users from the clone path's tables:")
    cur.execute(
        "SELECT t.relname AS table_name, c.conname, pg_get_constraintdef(c.oid) AS def "
        "  FROM pg_constraint c "
        "  JOIN pg_class t ON t.oid = c.conrelid "
        "  JOIN pg_namespace n ON n.oid = t.relnamespace AND n.nspname = 'public' "
        " WHERE c.contype = 'f' "
        "   AND t.relname IN ('strategies', 'library_strategies', 'library_ratings', "
        "                     'profiles') "
        " ORDER BY t.relname, c.conname"
    )
    for row in cur.fetchall():
        print(f"    {row['table_name']:<20} {row['conname']:<44} {row['def']}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
