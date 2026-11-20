"""
tests/test_bt02_backtest_honors_requested_date_range.py

BT-02: ``start_date``/``end_date`` were accepted by every backtest-shaped request (a plain
backtest, an optimization trial, a walk-forward window's train or test period) and used only to
log and to stamp the ``backtests`` row's metadata. ``DataEngine.fetch_historical_ohlcv`` had no
concept of a date range at all - with ``since=None`` it always answers "the most recent N bars",
so a backtest asked for over 2020 silently ran on whatever the most recent bars happened to be
when it was called, and ANY two requests with different date ranges (most importantly, a
walk-forward window's train and test periods) ran on byte-identical data.

This file proves two things: (1) the new ``since_ms``/``until_ms`` mode on
``fetch_historical_ohlcv`` actually fetches the requested window rather than "most recent", with
the same under-coverage warning discipline the existing "most recent" mode already has; and (2)
``BacktestRuntime.run_backtest`` actually calls it with the window implied by the ``start_date``/
``end_date`` it was given, rather than the no-argument "most recent" mode it used before this fix.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest

from backend_app.backend.backtest_runtime import _requested_window_ms
from backend_app.backend.data_seeking_engine import DataEngine, InsufficientHistoryError

STEP = 15 * 60 * 1000  # 15m bars, in ms
NOW = 1_760_000_000_000


def history(n: int, start: int = 0):
    """``n`` ascending bars, ``STEP`` apart, starting at ``start``."""
    return [[start + i * STEP, 1.0, 1.0, 1.0, 1.0, 1.0] for i in range(n)]


class FakeExchange:
    """ccxt semantics: ``since=None`` -> latest ``limit`` bars; ``since=T`` -> bars >= T ascending.

    The same double ``tests/test_data01_historical_ohlcv_pages_backward.py`` already uses and
    already proves this module's "most recent" mode against - reused rather than re-specified, so
    the two files cannot quietly disagree about what the exchange answers.
    """

    id = "fake"
    has = {}

    def __init__(self, bars, page_cap=500):
        self.bars = bars
        self.calls = []
        self.page_cap = page_cap

    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=500):
        self.calls.append(since)
        limit = min(limit, self.page_cap)
        if since is None:
            return self.bars[-limit:]
        return [b for b in self.bars if b[0] >= since][:limit]


def run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════════════
# 1. THE BOUNDED FETCH ITSELF
# ══════════════════════════════════════════════════════════════════════════


def test_a_bounded_window_returns_exactly_its_bars():
    bars = history(1_000, start=NOW)
    ex = FakeExchange(bars)
    since_ms = NOW + 100 * STEP
    until_ms = NOW + 150 * STEP  # exclusive: bars [100, 150) -> 50 bars

    result = run(
        DataEngine(ex).fetch_historical_ohlcv(
            "BTC/USDT", "15m", limit=10_000, since_ms=since_ms, until_ms=until_ms
        )
    )

    assert len(result) == 50
    assert result[0][0] == since_ms
    assert result[-1][0] == until_ms - STEP
    assert all(b[0] < until_ms for b in result)


def test_a_bounded_window_pages_forward_beyond_one_exchange_page():
    bars = history(2_000, start=NOW)
    ex = FakeExchange(bars, page_cap=500)
    since_ms = NOW
    until_ms = NOW + 1_200 * STEP

    result = run(
        DataEngine(ex).fetch_historical_ohlcv(
            "BTC/USDT", "15m", limit=10_000, since_ms=since_ms, until_ms=until_ms
        )
    )

    assert len(result) == 1_200
    ts = [b[0] for b in result]
    assert ts == sorted(ts) and len(set(ts)) == len(ts)
    assert ts[0] == since_ms
    assert ts[-1] == until_ms - STEP
    assert len(ex.calls) == 3  # 1200 bars / 500-bar exchange page


def test_until_ms_trims_a_page_rather_than_rounding_past_it():
    """``until_ms`` can fall in the MIDDLE of a fetched page; the trim must not over-fetch."""
    bars = history(500, start=NOW)
    ex = FakeExchange(bars, page_cap=500)
    since_ms = NOW
    until_ms = NOW + 237 * STEP  # mid-page, not a page boundary

    result = run(
        DataEngine(ex).fetch_historical_ohlcv(
            "BTC/USDT", "15m", limit=10_000, since_ms=since_ms, until_ms=until_ms
        )
    )

    assert len(result) == 237
    assert result[-1][0] == until_ms - STEP
    assert len(ex.calls) == 1, "the whole window fit in one exchange page"


def test_an_open_ended_window_fetches_forward_from_since_not_most_recent():
    """``until_ms`` omitted: the window starts at ``since_ms``, NOT "the most recent bars"."""
    bars = history(2_000, start=NOW)
    ex = FakeExchange(bars)
    since_ms = NOW + 10 * STEP

    result = run(
        DataEngine(ex).fetch_historical_ohlcv(
            "BTC/USDT", "15m", limit=100, since_ms=since_ms
        )
    )

    assert len(result) == 100
    assert result[0][0] == since_ms, (
        "an open-ended since_ms window must start at since_ms, not at the latest bars"
    )
    assert result[-1][0] == since_ms + 99 * STEP


def test_a_closed_short_window_does_not_warn_as_under_coverage(caplog):
    """A CLOSED window that legitimately holds fewer bars than `limit` is not a coverage gap."""
    bars = history(50, start=NOW)
    ex = FakeExchange(bars)

    with caplog.at_level(logging.WARNING):
        result = run(
            DataEngine(ex).fetch_historical_ohlcv(
                "BTC/USDT",
                "15m",
                limit=10_000,
                since_ms=NOW,
                until_ms=NOW + 50 * STEP,
            )
        )

    assert len(result) == 50
    assert not any("wanted" in r.message for r in caplog.records), (
        "a closed window that legitimately holds fewer bars than the limit must not warn - "
        "it is the window being short, not the exchange under-delivering"
    )


def test_an_open_ended_short_result_still_warns(caplog):
    """An OPEN-ENDED window that came up short IS the ordinary under-coverage case."""
    bars = history(50, start=NOW)
    ex = FakeExchange(bars)

    with caplog.at_level(logging.WARNING):
        result = run(
            DataEngine(ex).fetch_historical_ohlcv(
                "BTC/USDT", "15m", limit=10_000, since_ms=NOW
            )
        )

    assert len(result) == 50
    assert any("wanted 10000 bars" in r.message and "50" in r.message for r in caplog.records)


def test_require_full_on_a_short_open_ended_window_raises():
    bars = history(50, start=NOW)
    ex = FakeExchange(bars)

    with pytest.raises(InsufficientHistoryError):
        run(
            DataEngine(ex).fetch_historical_ohlcv(
                "BTC/USDT", "15m", limit=10_000, since_ms=NOW, require_full=True
            )
        )


def test_no_data_at_or_after_since_returns_empty():
    bars = history(50, start=NOW)
    ex = FakeExchange(bars)
    far_future = NOW + 10_000 * STEP

    result = run(
        DataEngine(ex).fetch_historical_ohlcv(
            "BTC/USDT", "15m", limit=100, since_ms=far_future
        )
    )
    assert result == []


# ══════════════════════════════════════════════════════════════════════════
# 2. THE DATE-STRING CONVERSION BacktestRuntime USES
# ══════════════════════════════════════════════════════════════════════════


def test_requested_window_ms_converts_iso_dates():
    since_ms, until_ms = _requested_window_ms("2023-01-01", "2023-06-01")
    assert since_ms == int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    assert until_ms == int(datetime(2023, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)


def test_requested_window_ms_treats_a_naive_date_as_utc():
    since_ms, _ = _requested_window_ms("2023-01-01T00:00:00", "2023-01-02T00:00:00")
    assert since_ms == int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def test_requested_window_ms_is_none_none_for_blank_or_unreadable_dates():
    assert _requested_window_ms("", "") == (None, None)
    assert _requested_window_ms("not-a-date", "2023-01-01") == (None, int(
        datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000
    ))


# ══════════════════════════════════════════════════════════════════════════
# 3. THE WIRING: run_backtest ACTUALLY PASSES THE WINDOW THROUGH
# ══════════════════════════════════════════════════════════════════════════


class _SpyDataEngine:
    """Records the exact kwargs ``run_backtest`` called ``fetch_historical_ohlcv`` with."""

    def __init__(self, bars):
        self._bars = bars
        self.calls = []

    async def fetch_historical_ohlcv(self, **kwargs):
        self.calls.append(kwargs)
        return self._bars


def test_run_backtest_passes_the_requested_window_to_the_data_engine():
    """The end-to-end wiring: a backtest asked for a specific window fetches THAT window.

    Before this fix, ``run_backtest`` called ``fetch_historical_ohlcv`` with no ``since_ms``/
    ``until_ms`` at all regardless of ``start_date``/``end_date`` - this is the call that proves
    it no longer does.
    """
    from tests.test_backtest_evidence_columns_regression import (
        _bars,
        _service,
        _StubDagEngine,
        _StubStrategyPackage,
        _Supabase,
        _running_row,
        RECONCILED_FOR_BAR_COUNT,
    )
    from backend_app.backend.backtest_runtime import BacktestRuntime

    spy = _SpyDataEngine(_bars())
    sb = _Supabase(RECONCILED_FOR_BAR_COUNT, rows={"strategy_backtests": [_running_row()]})
    runtime = BacktestRuntime(initial_capital=10_000.0)
    runtime.set_data_engine = lambda *_a, **_k: None
    runtime.data_engine = spy
    runtime.dag_engine = _StubDagEngine()
    runtime.backtest_service = _service(sb)

    package = _StubStrategyPackage()

    run(
        runtime.run_backtest(
            strategy_package=package,
            user={"id": "user-1"},
            strategy_id="strat-1",
            version_id="v1",
            version="v1",
            start_date="2023-01-01",
            end_date="2023-02-01",
            exchange_instance=object(),
        )
    )

    assert spy.calls, "run_backtest never called fetch_historical_ohlcv"
    call = spy.calls[0]
    assert call["since_ms"] == int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    assert call["until_ms"] == int(datetime(2023, 2, 1, tzinfo=timezone.utc).timestamp() * 1000)


def test_two_different_windows_are_not_passed_the_same_bounds():
    """The walk-forward-relevant guarantee: two different requested windows differ at the call."""
    from tests.test_backtest_evidence_columns_regression import (
        _bars,
        _service,
        _StubDagEngine,
        _StubStrategyPackage,
        _Supabase,
        _running_row,
        RECONCILED_FOR_BAR_COUNT,
    )
    from backend_app.backend.backtest_runtime import BacktestRuntime

    package = _StubStrategyPackage()

    windows = [("2023-01-01", "2023-02-01"), ("2023-03-01", "2023-04-01")]
    calls = []
    for start, end in windows:
        spy = _SpyDataEngine(_bars())
        sb = _Supabase(RECONCILED_FOR_BAR_COUNT, rows={"strategy_backtests": [_running_row()]})
        runtime = BacktestRuntime(initial_capital=10_000.0)
        runtime.set_data_engine = lambda *_a, **_k: None
        runtime.data_engine = spy
        runtime.dag_engine = _StubDagEngine()
        runtime.backtest_service = _service(sb)
        run(
            runtime.run_backtest(
                strategy_package=package,
                user={"id": "user-1"},
                strategy_id="strat-1",
                version_id="v1",
                version="v1",
                start_date=start,
                end_date=end,
                exchange_instance=object(),
            )
        )
        calls.append(spy.calls[0])

    assert calls[0]["since_ms"] != calls[1]["since_ms"]
    assert calls[0]["until_ms"] != calls[1]["until_ms"]
