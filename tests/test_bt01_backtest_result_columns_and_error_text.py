"""BT-01 — a backtest could never complete in production, and the database's own error text
reached the trader's screen.

Production failed every run with PGRST204 "Could not find the 'daily_returns' column of
'strategy_backtests'": ``001_strategy_architecture.sql`` declares five result columns in a
``CREATE TABLE IF NOT EXISTS`` that was a no-op against the table already in production, and
``BacktestService.update_backtest_results`` writes all of them. The repo's schema-drift scanner
cannot see this writer because it builds its payload from dicts, so these tests read it
directly.
"""
from __future__ import annotations

import re
from pathlib import Path

from backend_app.backend.backtest_service import (
    RESULT_COLUMN_SOURCE_KEYS,
    RESULT_JSON_COLUMN_SOURCE_KEYS,
)
from backend_app.routers.strategy_operations import (
    _GENERIC_BACKTEST_FAILURE,
    _safe_failure_message,
)

MIGRATION = Path(__file__).resolve().parents[1] / "backend_app" / "migrations" / "063_bt01_backtest_result_columns.sql"

# The columns production's strategy_backtests lacked (queried 2026-11-07), i.e. the ones the
# 001 CREATE TABLE IF NOT EXISTS never added.
ADDED_BY_063 = {"winning_trades", "losing_trades", "execution_time_seconds", "monthly_returns", "daily_returns"}


def test_the_writer_still_writes_every_column_063_adds():
    written = set(RESULT_COLUMN_SOURCE_KEYS) | set(RESULT_JSON_COLUMN_SOURCE_KEYS)
    assert ADDED_BY_063 <= written, "the writer no longer targets a column this migration exists for"


def test_migration_063_adds_every_one_of_them_idempotently_and_without_defaults():
    sql = MIGRATION.read_text(encoding="utf-8")
    for column in ADDED_BY_063:
        assert re.search(rf"ADD COLUMN IF NOT EXISTS\s+{column}\b", sql), column
    alter = sql[sql.index("ALTER TABLE"): sql.index("DO $")]
    assert "DEFAULT" not in alter.upper(), "a default would turn an unmeasured metric into a fabricated 0"
    assert "NOTIFY pgrst" in sql, "PostgREST caches the schema; it must be told to reload"


def test_postgrest_error_text_is_never_shown_to_the_trader():
    leaked = (
        "{'code': 'PGRST204', 'details': None, 'hint': None, 'message': \"Could not find the "
        "'daily_returns' column of 'strategy_backtests' in the schema cache\"}"
    )
    assert _safe_failure_message(Exception(leaked)) == _GENERIC_BACKTEST_FAILURE
    assert "strategy_backtests" not in _safe_failure_message(Exception(leaked))


def test_authored_runtime_sentences_still_pass_through():
    authored = "vectorbt is not importable, so this backtest cannot be simulated."
    assert _safe_failure_message(RuntimeError(authored)) == authored
