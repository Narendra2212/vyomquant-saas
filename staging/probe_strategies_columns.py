"""List `strategies` columns and mark which the API-01 allowlist permits.

Added because the allowlist omitted `description`, which broke the edit route. The point
of this probe is to decide the omission deliberately for EVERY column rather than patching
the one a test happened to catch.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

DSN = "postgresql://postgres:@127.0.0.1:62674/postgres"

import psycopg2  # noqa: E402

from backend_app.routers.strategies import (  # noqa: E402
    _NON_COLUMN_BODY_KEYS, _UPDATABLE_STRATEGY_COLUMNS,
)

conn = psycopg2.connect(DSN)
conn.autocommit = True
cur = conn.cursor()
cur.execute(
    "SELECT column_name, data_type, is_nullable "
    "  FROM information_schema.columns "
    " WHERE table_schema='public' AND table_name='strategies' "
    " ORDER BY ordinal_position"
)
rows = cur.fetchall()

if not rows:
    print("strategies table not found in this database")
    raise SystemExit(1)

print(f"{'column':<28} {'type':<26} {'allowlisted'}")
print("-" * 72)
allowed, refused = [], []
for name, dtype, nullable in rows:
    mark = "YES" if name in _UPDATABLE_STRATEGY_COLUMNS else "no"
    (allowed if name in _UPDATABLE_STRATEGY_COLUMNS else refused).append(name)
    print(f"{name:<28} {dtype:<26} {mark}")

print()
print(f"allowlisted ({len(allowed)}): {sorted(allowed)}")
print()
print(f"refused ({len(refused)}): {sorted(refused)}")
print()
print(f"allowlist entries that are NOT columns: "
      f"{sorted(set(_UPDATABLE_STRATEGY_COLUMNS) - {r[0] for r in rows})}")
print(f"non-column body keys: {sorted(_NON_COLUMN_BODY_KEYS)}")
