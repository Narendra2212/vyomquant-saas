"""API-03 -- GET /api/stats reports genuine platform-wide figures, or fails honestly.

THE DEFECT
    The handler called the PER-USER dashboard aggregator with a sentinel
    ``user={"id": "public"}`` identity that owns nothing. Every per-user-scoped
    sub-query inside that aggregator filters by ``user_id``, and no real account has the
    id ``"public"``, so most figures came back as that non-existent account's empty
    state -- not a platform aggregate. ``total_pnl`` was literally ``today_pnl`` under a
    mislabelled key, and ``daily_pnl``/``weekly_pnl``/``monthly_pnl`` all read the exact
    same number. A failed read answered 200 with every figure forced to ``0``,
    indistinguishable on the wire from a genuinely idle platform.

THE FIX
    ``backend_app.backend.platform_stats.get_platform_stats`` -- a dedicated,
    service-role aggregation over ``strategies`` (live-strategy count),istrategy_
    deployments`` (running count) and ``trade_history`` (trade count, win count, and
    four DISTINCTLY WINDOWED pnl sums). A statement that does not complete raises
    ``PlatformStatsUnavailable``, which ``main.py``'s handler turns into a 503 rather
    than a false "all zero" 200.

WHAT THIS FILE PROVES (a minimal fake Supabase double; no real database)
    * each figure is computed from the RIGHT table with the RIGHT predicate;
    * the four pnl windows are genuinely different sums, not the same number four times;
    * a query that does not complete raises rather than producing a fabricated zero;
    * the ``strategies.archived_at`` "column may not exist yet" landmine
    (``005a_strategy_archive.sql`` is applied by hand) degrades the same way
    ``strategies.py``'s own list route does -- count every row -- rather than raising a
    raw ``42703``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from backend_app.backend.platform_stats import (
    PlatformStatsUnavailable,
    get_platform_stats,
)


def _run(coro):
    return asyncio.run(coro)


class _Result:
    def __init__(self, data: List[dict], count: Optional[int]) -> None:
        self.data = data
        self.count = count


class _Query:
    def __init__(self, rows: List[dict], select: str, *, fail: bool = False) -> None:
        self._rows = rows
        self._select = select
        self._fail = fail
        self._count_mode: Optional[str] = None
        self._filters: List[Any] = []

    def select(self, columns: str, count: Optional[str] = None) -> "_Query":
        self._select = columns
        self._count_mode = count
        return self

    def eq(self, column: str, value: Any) -> "_Query":
        self._filters.append(lambda r: r.get(column) == value)
        return self

    def gt(self, column: str, value: Any) -> "_Query":
        self._filters.append(lambda r: (r.get(column) or 0) > value)
        return self

    def is_(self, column: str, value: Any) -> "_Query":
        if value in (None, "null"):
            self._filters.append(lambda r: r.get(column) is None)
        else:
            self._filters.append(lambda r: r.get(column) == value)
        return self

    def execute(self) -> _Result:
        if self._fail:
            raise RuntimeError("simulated statement failure")
        matched = [r for r in self._rows if all(f(r) for f in self._filters)]
        count = len(matched) if self._count_mode == "exact" else None
        return _Result(matched, count)


class FakeSupabase:
    """A table-name-keyed double. ``tables['strategies'] = [...]`` seeds rows."""

    def __init__(self, tables: Dict[str, List[dict]]) -> None:
        self.tables = tables
        self.fail_tables: set = set()
        self.archived_at_missing = False

    def table(self, name: str) -> _Query:
        if self.archived_at_missing and name == "strategies":
            return _FailingArchivedAtQuery(self.tables.get(name, []), "*")
        return _Query(self.tables.get(name, []), "*", fail=name in self.fail_tables)


class _FailingArchivedAtQuery(_Query):
    """Simulates 005a's column not existing yet: `.is_("archived_at", ...)` raises."""

    def is_(self, column: str, value: Any) -> "_Query":
        if column == "archived_at":
            raise RuntimeError(
                'column strategies.archived_at does not exist (42703, "schema cache")'
            )
        return super().is_(column, value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _base_tables() -> Dict[str, List[dict]]:
    now = _now()
    return {
        "strategies": [
            {"id": "s1", "archived_at": None},
            {"id": "s2", "archived_at": None},
            {"id": "s3", "archived_at": _iso(now)},  # archived -- must not be counted
        ],
        "strategy_deployments": [
            {"id": "d1", "status": "running"},
            {"id": "d2", "status": "running"},
            {"id": "d3", "status": "stopped"},
        ],
        "trade_history": [
            {
                "trade_id": "t1",
                "realized_pnl": 100.0,
                "closed_at": _iso(now),  # today
            },
            {
                "trade_id": "t2",
                "realized_pnl": -40.0,
                "closed_at": _iso(now - timedelta(days=3)),  # this week, not today
            },
            {
                "trade_id": "t3",
                "realized_pnl": 20.0,
                "closed_at": _iso(now - timedelta(days=20)),  # this month, not this week
            },
            {
                "trade_id": "t4",
                "realized_pnl": -5.0,
                "closed_at": _iso(now - timedelta(days=90)),  # lifetime only
            },
        ],
    }


class TestEachFigureComesFromTheRightTableAndPredicate:
    def test_total_strategies_excludes_the_archived_row(self):
        sb = FakeSupabase(_base_tables())
        result = _run(get_platform_stats(sb))
        assert result["total_strategies"] == 2

    def test_active_bots_counts_only_running_deployments(self):
        sb = FakeSupabase(_base_tables())
        result = _run(get_platform_stats(sb))
        assert result["active_bots"] == 2

    def test_total_trades_and_win_rate(self):
        sb = FakeSupabase(_base_tables())
        result = _run(get_platform_stats(sb))
        assert result["total_trades"] == 4
        # t1 (100.0) and t3 (20.0) are winners -> 2/4
        assert result["win_rate"] == pytest.approx(0.5)

    def test_no_trades_is_an_honest_zero_win_rate_not_a_division_error(self):
        tables = _base_tables()
        tables["trade_history"] = []
        sb = FakeSupabase(tables)
        result = _run(get_platform_stats(sb))
        assert result["total_trades"] == 0
        assert result["win_rate"] == 0.0


class TestTheFourPnlWindowsAreGenuinelyDistinct:
    def test_the_old_bug_is_fixed_four_different_numbers_not_one(self):
        """THE regression this whole fix is for: all four used to be the same value."""
        sb = FakeSupabase(_base_tables())
        result = _run(get_platform_stats(sb))

        figures = {
            result["total_pnl"],
            result["daily_pnl"],
            result["weekly_pnl"],
            result["monthly_pnl"],
        }
        assert len(figures) == 4, (
            f"the four pnl figures are not distinct: {result}. This is exactly the "
            "defect this fix closes -- four labels reporting one number."
        )

    def test_total_pnl_is_the_lifetime_sum(self):
        sb = FakeSupabase(_base_tables())
        result = _run(get_platform_stats(sb))
        assert result["total_pnl"] == pytest.approx(100.0 - 40.0 + 20.0 - 5.0)

    def test_daily_pnl_is_only_todays_closes(self):
        sb = FakeSupabase(_base_tables())
        result = _run(get_platform_stats(sb))
        assert result["daily_pnl"] == pytest.approx(100.0)

    def test_weekly_pnl_includes_today_and_this_week(self):
        sb = FakeSupabase(_base_tables())
        result = _run(get_platform_stats(sb))
        assert result["weekly_pnl"] == pytest.approx(100.0 - 40.0)

    def test_monthly_pnl_includes_the_week_and_the_rest_of_the_month(self):
        sb = FakeSupabase(_base_tables())
        result = _run(get_platform_stats(sb))
        assert result["monthly_pnl"] == pytest.approx(100.0 - 40.0 + 20.0)


class TestAFailureIsHonestNeverAFabricatedZero:
    def test_no_service_role_client_raises(self):
        with pytest.raises(PlatformStatsUnavailable):
            _run(get_platform_stats(None))

    def test_a_statement_that_does_not_complete_raises_rather_than_returning_zero(self):
        sb = FakeSupabase(_base_tables())
        sb.fail_tables.add("trade_history")
        with pytest.raises(PlatformStatsUnavailable):
            _run(get_platform_stats(sb))

    def test_the_strategy_deployments_table_failing_also_raises(self):
        sb = FakeSupabase(_base_tables())
        sb.fail_tables.add("strategy_deployments")
        with pytest.raises(PlatformStatsUnavailable):
            _run(get_platform_stats(sb))


class TestTheArchivedAtColumnMayNotExistYet:
    """005a_strategy_archive.sql is applied by hand; this column can legitimately be
    absent. strategies.py's own list route treats "column absent" as "nothing is
    archived" -- this module must degrade the same way, not raise a raw 42703.
    """

    def test_a_missing_archived_at_column_counts_every_strategy(self):
        sb = FakeSupabase(_base_tables())
        sb.archived_at_missing = True
        result = _run(get_platform_stats(sb))
        # All 3 rows count now, including the one that WOULD have been archived --
        # consistent with "the column doesn't exist, so nothing is archived".
        assert result["total_strategies"] == 3

    def test_a_different_strategies_failure_still_raises(self):
        """NEGATIVE CONTROL: not every strategies-table failure is swallowed, only the
        specific, identified missing-column one.
        """
        sb = FakeSupabase(_base_tables())
        sb.fail_tables.add("strategies")
        with pytest.raises(PlatformStatsUnavailable):
            _run(get_platform_stats(sb))
