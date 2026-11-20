"""BT-04 -- a 100% win rate must be storable.

Production: a finished run failed at the final write with numeric field overflow because
``strategy_backtests.win_rate`` was numeric(6,4) (|x| < 100) while the writer stores a percent.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "backend_app/migrations/064_bt04_backtest_metric_column_widths.sql"


def _widths(sql: str):
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"ALTER COLUMN (\w+)\s+TYPE numeric\((\d+),\s*4\)", sql)}


def test_win_rate_can_hold_one_hundred_percent():
    precision = _widths(MIGRATION.read_text())["win_rate"]
    # numeric(p, 4) holds |x| < 10**(p-4); 100.0000 needs p - 4 >= 3.
    assert precision - 4 >= 3


def test_the_unbounded_ratios_are_widened_too():
    widths = _widths(MIGRATION.read_text())
    for column in ("sharpe_ratio", "sortino_ratio", "profit_factor"):
        assert widths[column] >= 12, column


def test_the_migration_only_widens():
    # Comments explain the old shape, so only executable lines are checked.
    sql = "\n".join(l for l in MIGRATION.read_text().splitlines() if not l.strip().startswith("--"))
    assert "DROP COLUMN" not in sql.upper()
    assert "numeric(6" not in sql
