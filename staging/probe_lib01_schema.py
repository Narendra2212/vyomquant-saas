"""Which tables does the clone path need, and which of them exist in the test database?

The LIB-01 tests are required to run against real PostgreSQL. `clone_strategy` touches
`library_strategies`, `strategies` and `library_ratings`, and its entitlement step reads
whatever `entitlement_resolver` reads. If any of those is absent from the provisioned test
schema the tests cannot be written against it, and that has to be reported rather than
quietly replaced with a double.

Reports, per table: present/absent, and for the present ones the columns the clone path
names, so a test can be written against the real column set rather than a guessed one.
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

NEEDED = {
    "library_strategies": [
        "id", "author_id", "source_strategy_id", "name", "is_active",
        "moderation_status", "clone_count", "source_cloning_enabled",
        "avg_rating", "rating_count", "is_featured", "moderated_by",
    ],
    "strategies": [
        "id", "user_id", "name", "symbol", "timeframe", "exchange_id",
        "buy_logic", "sell_logic", "risk", "indicators", "ml_model_path",
        "source_library_id", "status", "created_at", "updated_at",
    ],
    "library_ratings": ["id", "library_id", "user_id", "rating", "is_verified_clone"],
    "marketplace_subscriptions": ["id", "user_id", "listing_id", "status"],
    "subscriptions": ["id", "user_id", "status"],
    "profiles": ["id"],
}


def main() -> int:
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print(f"DSN={DSN}")
    for table, wanted in NEEDED.items():
        cur.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "  FROM information_schema.columns "
            " WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        )
        rows = {r["column_name"]: r for r in cur.fetchall()}
        if not rows:
            print(f"\n{table}: ABSENT")
            continue
        print(f"\n{table}: PRESENT ({len(rows)} columns)")
        for column in wanted:
            row = rows.get(column)
            if row is None:
                print(f"    {column:<24} MISSING")
            else:
                default = (row["column_default"] or "")[:40]
                print(
                    f"    {column:<24} {row['data_type']:<28} "
                    f"null={row['is_nullable']:<3} default={default}"
                )

    # Unique constraints that bear on concurrent cloning.
    cur.execute(
        "SELECT c.conname, c.contype, pg_get_constraintdef(c.oid) AS def "
        "  FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
        "  JOIN pg_namespace n ON n.oid = t.relnamespace AND n.nspname = 'public' "
        " WHERE t.relname IN ('strategies', 'library_strategies', 'library_ratings') "
        "   AND c.contype IN ('u', 'p') ORDER BY t.relname, c.conname"
    )
    print("\nUNIQUE / PRIMARY KEY constraints:")
    for row in cur.fetchall():
        print(f"    {row['conname']:<44} {row['def']}")

    cur.execute(
        "SELECT indexname, indexdef FROM pg_indexes "
        " WHERE schemaname = 'public' AND tablename = 'strategies' "
        "   AND indexdef ILIKE '%unique%'"
    )
    print("\nUNIQUE indexes on strategies:")
    rows = cur.fetchall()
    if not rows:
        print("    (none)")
    for row in rows:
        print(f"    {row['indexname']}: {row['indexdef']}")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
