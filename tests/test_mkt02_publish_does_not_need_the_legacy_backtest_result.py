"""MKT-02: the Marketplace's first publish step required a column nothing writes.

``POST /api/library`` creates the Listing the Submission later references, and it refused any
strategy whose ``strategies.backtest_result`` was empty -- a legacy column no current code path
writes (192 of 192 production strategies: empty). So the only publish control in the UI answered
"Run a backtest before publishing" to a user holding completed backtests, and the Marketplace had
zero listings and no way to get one.

The figures now come from the latest completed ``strategy_backtests`` run when the legacy field is
absent.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend_app.routers import library as lib


class _Query:
    def __init__(self, rows, boom=False):
        self._rows, self._boom, self.calls = rows, boom, []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self

        return record

    def execute(self):
        if self._boom:
            raise RuntimeError("PostgREST unreachable")
        return SimpleNamespace(data=self._rows)


class _Svc:
    def __init__(self, rows, boom=False):
        self.query = _Query(rows, boom)
        self.tables = []

    def table(self, name):
        self.tables.append(name)
        return self.query


RUN = {
    "id": "r1",
    "total_return_pct": 2.7153,
    "sharpe_ratio": 1.4367,
    "max_drawdown": 3.4903,
    "win_rate": 26.5957,
    "profit_factor": 1.3459,
    "total_trades": 94,
    "start_date": "2025-01-01",
    "end_date": "2025-06-30",
    "initial_capital": 10000.0,
}


def test_the_latest_completed_run_becomes_the_listing_figures() -> None:
    svc = _Svc([RUN])

    result = lib._backtest_result_from_latest_run(svc, "s1", "u1")

    assert svc.tables == ["strategy_backtests"]
    assert result["max_drawdown_pct"] == 3.4903, "stored in percent; renamed, not rescaled"
    assert result["win_rate_pct"] == 26.5957
    assert result["total_trades"] == 94 and result["total_return_pct"] == 2.7153
    called = {name: (args, kwargs) for name, args, kwargs in svc.query.calls}
    assert ("status", "completed") == called["eq"][0] or any(
        c[0] == "eq" and c[1] == ("status", "completed") for c in svc.query.calls
    )


def test_it_reads_only_the_owners_completed_runs_newest_first() -> None:
    svc = _Svc([RUN])
    lib._backtest_result_from_latest_run(svc, "s1", "u1")

    eqs = [c[1] for c in svc.query.calls if c[0] == "eq"]
    assert ("strategy_id", "s1") in eqs and ("user_id", "u1") in eqs
    assert ("status", "completed") in eqs
    order = [c for c in svc.query.calls if c[0] == "order"][0]
    assert order[1] == ("completed_at",) and order[2] == {"desc": True}


def test_no_completed_run_is_none_so_the_400_still_fires() -> None:
    assert lib._backtest_result_from_latest_run(_Svc([]), "s1", "u1") is None


def test_a_failed_read_is_a_500_not_a_false_run_a_backtest_400() -> None:
    with pytest.raises(HTTPException) as caught:
        lib._backtest_result_from_latest_run(_Svc([], boom=True), "s1", "u1")

    assert caught.value.status_code == 500
