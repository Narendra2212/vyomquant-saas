"""The CHECK constraints on library_strategies, so the LIB-01 seeds are legal.

Seeding `is_featured = TRUE` was refused by `chk_ls_featured_requires_published`. Rather
than guess which columns it ties together, the definitions are read out of the catalogue.
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

    for table in ("library_strategies", "strategies", "library_ratings"):
        cur.execute(
            "SELECT c.conname, pg_get_constraintdef(c.oid) AS def "
            "  FROM pg_constraint c "
            "  JOIN pg_class t ON t.oid = c.conrelid "
            "  JOIN pg_namespace n ON n.oid = t.relnamespace AND n.nspname = 'public' "
            " WHERE t.relname = %s AND c.contype = 'c' ORDER BY c.conname",
            (table,),
        )
        rows = cur.fetchall()
        print(f"\n{table}: {len(rows)} CHECK constraint(s)")
        for row in rows:
            print(f"    {row['conname']}")
            print(f"        {row['def']}")

    # The columns a seed must satisfy for a "published, approved, cloneable" listing.
    cur.execute(
        "SELECT column_name, is_nullable, column_default "
        "  FROM information_schema.columns "
        " WHERE table_schema = 'public' AND table_name = 'library_strategies' "
        "   AND is_nullable = 'NO' AND column_default IS NULL "
        " ORDER BY ordinal_position"
    )
    print("\nlibrary_strategies columns a seed MUST supply (NOT NULL, no default):")
    for row in cur.fetchall():
        print(f"    {row['column_name']}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
