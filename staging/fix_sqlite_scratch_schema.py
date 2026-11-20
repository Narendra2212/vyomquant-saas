"""Bring the gitignored SQLite scratch DB (algo22.db) up to the current ORM schema.

WHY THIS EXISTS
---------------
The CI-comparable lane sets no DATABASE_URL, so `backend_app.core.database` falls back to
`sqlite:///./algo22.db` -- a PERSISTED file. `Base.metadata.create_all()` (run only in
`main.py` at app startup, which unit tests do not import) creates MISSING TABLES but never
ADDS COLUMNS to an existing one. So a scratch DB created before migration 030 keeps an
`execution_records` table with no `venue_order_id` column, and every unit test that drives
the real engine against that file now fails with `no such column: venue_order_id` the
moment the TRADE-07 evidence write runs.

This is an ENVIRONMENTAL artifact, not a product or test defect: the file is a stale local
scratch database, gitignored, 3 MB, from an earlier run. The equivalent of applying the
migration on a real database is, for SQLite, adding the column. This script does exactly
that -- additively, idempotently, and ONLY to the local scratch file.

It refuses anything that is not a local SQLite file, for the same reason the migration
runner refuses a non-local DSN: it issues DDL.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The columns migration 030 (and the current ORM) expect on execution_records that an old
#: scratch DB may lack. Only additive, nullable columns -- exactly what create_all would
#: have produced on a fresh file.
_WANTED_COLUMNS = {
    "venue_order_id": "VARCHAR",
}


def main() -> int:
    db_url = os.getenv("DATABASE_URL", "sqlite:///./algo22.db")
    if "sqlite" not in db_url.lower():
        print(f"REFUSED: DATABASE_URL is {db_url!r}, not SQLite; this only fixes the "
              f"local scratch file.")
        return 2

    # Resolve the sqlite file path from the URL.
    path = db_url.split("sqlite:///")[-1] if "sqlite:///" in db_url else "algo22.db"
    db_path = (ROOT / path).resolve() if not os.path.isabs(path) else pathlib.Path(path)

    print(f"DB_FILE = {db_path}")
    if not db_path.exists():
        print("STATUS = absent; create_all will build it fresh from the current ORM, so "
              "nothing to do here.")
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    ("execution_records",))
        if cur.fetchone() is None:
            print("STATUS = execution_records table absent; create_all will build it "
                  "fresh. Nothing to do.")
            return 0

        cur.execute("PRAGMA table_info(execution_records)")
        existing = {row[1] for row in cur.fetchall()}
        print(f"EXISTING_COLUMNS = {sorted(existing)}")

        added = []
        for column, sqltype in _WANTED_COLUMNS.items():
            if column not in existing:
                # Additive, nullable, no default -- identical to what the ORM declares and
                # what create_all would have produced on a fresh file.
                cur.execute(
                    f"ALTER TABLE execution_records ADD COLUMN {column} {sqltype}"
                )
                added.append(column)
        conn.commit()

        if added:
            print(f"ADDED = {added}")
        else:
            print("ADDED = none (schema already current)")

        cur.execute("PRAGMA table_info(execution_records)")
        now = {row[1] for row in cur.fetchall()}
        missing = set(_WANTED_COLUMNS) - now
        if missing:
            print(f"FAILED: still missing {sorted(missing)}")
            return 4
        print("STATUS = execution_records has every wanted column")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
