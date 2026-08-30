"""
tests/test_library_schema_contract.py

Regression test for the production defect:

    Trending strategies DB error: {code: 42703,
      message: column library_strategies.price does not exist}

backend_app/routers/library.py SELECTs and INSERTs a set of marketplace
columns on public.library_strategies. The migration that created those
columns was archived and never applied, so every marketplace request logged
PostgreSQL 42703 (undefined_column) and silently returned empty results.

This test asserts the schema contract: every marketplace column the router
depends on must be created by a migration in migrations/. It fails if a
developer adds a column reference in the router without a matching migration,
or if a required migration is archived/removed.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "migrations"
# The second, independently numbered migration sequence. Applied by hand, per
# file, exactly like migrations/ — see the header of
# backend_app/migrations/006_backtest_evidence_columns.sql on why the two
# sequences exist and why neither is renumbered. The marketplace columns of
# the marketplace-subscriptions-paper-trading spec land here, so a contract
# test that read only migrations/ would report them missing.
BACKEND_MIGRATIONS_DIR = REPO_ROOT / "backend_app" / "migrations"
LIBRARY_ROUTER = REPO_ROOT / "backend_app" / "routers" / "library.py"

# Marketplace columns backend_app/routers/library.py reads and writes on
# public.library_strategies. Derived from the SELECT projections in
# browse_library / trending / featured and the INSERT in publish_strategy.
REQUIRED_LIBRARY_COLUMNS = {
    "price",
    "currency",
    "subscription_tier",
    "cover_image",
    "verification_status",
    "evaluation_score",
    "subscriber_count",
    # marketplace-subscriptions-paper-trading, added by
    # backend_app/migrations/007_marketplace_submissions.sql (task 11.2).
    # price_minor is the authoritative price in Minor_Units (Requirement
    # 8.12), price above is retained and mirrored from it by trigger;
    # source_cloning_enabled is Requirement 7.4's stored consent;
    # supported_timeframes, market_type and condition_count are on the
    # Listing_Projection allow-list (Requirement 6.2).
    "price_minor",
    "source_cloning_enabled",
    "supported_timeframes",
    "market_type",
    "condition_count",
}


def _migration_sql() -> str:
    """Concatenate every migration in both applied migration sequences."""
    assert MIGRATIONS_DIR.is_dir(), f"missing migrations dir: {MIGRATIONS_DIR}"
    assert BACKEND_MIGRATIONS_DIR.is_dir(), (
        f"missing migrations dir: {BACKEND_MIGRATIONS_DIR}"
    )
    parts = []
    for directory in (MIGRATIONS_DIR, BACKEND_MIGRATIONS_DIR):
        for path in sorted(directory.glob("*.sql")):
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _columns_added_to_library_strategies(sql: str) -> set:
    """
    Collect column names added to public.library_strategies.

    Handles both forms used in this repo:
      ALTER TABLE public.library_strategies ADD COLUMN IF NOT EXISTS <col> ...
      CREATE TABLE ... public.library_strategies ( <col> <type>, ... )
    """
    found = set()
    lowered = sql.lower()

    # ALTER TABLE ... ADD COLUMN [IF NOT EXISTS] <col>
    # Scan each ALTER TABLE public.library_strategies statement up to its
    # terminating semicolon, then pull every ADD COLUMN inside it.
    for m in re.finditer(r"alter\s+table\s+(?:public\.)?library_strategies(.*?);", lowered, re.S):
        body = m.group(1)
        for cm in re.finditer(r"add\s+column\s+(?:if\s+not\s+exists\s+)?([a-z0-9_]+)", body):
            found.add(cm.group(1))

    # CREATE TABLE public.library_strategies ( ... )
    for m in re.finditer(r"create\s+table[^;]*?library_strategies\s*\((.*?)\n\s*\)\s*;", lowered, re.S):
        body = m.group(1)
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("--"):
                continue
            cm = re.match(r"([a-z0-9_]+)\s+[a-z]", line)
            if cm:
                found.add(cm.group(1))

    return found


def test_migrations_dir_exists_and_is_non_empty():
    """Guard: the parser below is meaningless if migrations/ is empty."""
    sql = _migration_sql()
    assert sql.strip(), "migrations/ contains no SQL"
    assert "library_strategies" in sql.lower(), (
        "no migration in migrations/ references library_strategies"
    )


@pytest.mark.parametrize("column", sorted(REQUIRED_LIBRARY_COLUMNS))
def test_required_marketplace_column_has_migration(column):
    """
    Every marketplace column the library router depends on must be created
    by a migration in migrations/.

    Regression guard for PostgreSQL 42703 undefined_column errors on
    /api/library trending and featured endpoints.
    """
    added = _columns_added_to_library_strategies(_migration_sql())
    assert column in added, (
        f"library_strategies.{column} is referenced by "
        f"backend_app/routers/library.py but no migration in migrations/ "
        f"creates it. This reproduces the production defect: PostgreSQL "
        f"42703 undefined_column, which the router swallows as a warning "
        f"and returns empty marketplace results. "
        f"Columns currently created by migrations: {sorted(added)}"
    )


def test_router_price_reference_is_covered():
    """
    Tie the test to the actual source: if library.py stops referencing
    price, this test should be revisited rather than silently passing.
    """
    assert LIBRARY_ROUTER.is_file(), f"missing router: {LIBRARY_ROUTER}"
    src = LIBRARY_ROUTER.read_text(encoding="utf-8", errors="replace")
    assert "price" in src, (
        "library.py no longer references price; update "
        "REQUIRED_LIBRARY_COLUMNS in this test accordingly"
    )
    added = _columns_added_to_library_strategies(_migration_sql())
    assert "price" in added
