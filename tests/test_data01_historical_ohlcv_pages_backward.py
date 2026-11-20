"""DATA-01 regression: fetch_historical_ohlcv must honour `limit` beyond one exchange page."""
import asyncio
import logging

import pytest

from backend_app.backend.data_seeking_engine import DataEngine, InsufficientHistoryError

STEP = 15 * 60 * 1000
NOW = 1_760_000_000_000


def history(n):
    return [[NOW - i * STEP, 1.0, 1.0, 1.0, 1.0, 1.0] for i in range(n)][::-1]  # oldest first


class FakeExchange:
    """ccxt semantics: since=None -> latest `limit` bars; since=T -> bars >= T ascending."""

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


def test_requests_beyond_one_page_return_the_requested_count():
    ex = FakeExchange(history(20_000))
    bars = run(DataEngine(ex).fetch_historical_ohlcv("BTC/USDT", "15m", limit=10_000))
    assert len(bars) == 10_000
    ts = [b[0] for b in bars]
    assert ts == sorted(ts) and len(set(ts)) == len(ts), "bars must be ascending and unique"
    assert ts[-1] == NOW, "the window ends at the latest bar"
    assert ts[0] == NOW - 9_999 * STEP, "and spans exactly the requested range with no gaps"
    assert all(b - a == STEP for a, b in zip(ts, ts[1:]))
    assert len(ex.calls) == 20  # 10_000 / 500


def test_single_page_request_is_one_call():
    ex = FakeExchange(history(2_000))
    bars = run(DataEngine(ex).fetch_historical_ohlcv("BTC/USDT", "15m", limit=300))
    assert len(bars) == 300 and len(ex.calls) == 1


def test_short_history_returns_everything_and_warns(caplog):
    ex = FakeExchange(history(1_200))
    with caplog.at_level(logging.WARNING):
        bars = run(DataEngine(ex).fetch_historical_ohlcv("BTC/USDT", "15m", limit=5_000))
    assert len(bars) == 1_200
    assert any("wanted 5000 bars" in r.message and "1200" in r.message for r in caplog.records)


def test_require_full_refuses_instead_of_truncating():
    ex = FakeExchange(history(1_200))
    with pytest.raises(InsufficientHistoryError):
        run(DataEngine(ex).fetch_historical_ohlcv("BTC/USDT", "15m", limit=5_000, require_full=True))


def test_smaller_exchange_page_cap_still_reaches_the_limit():
    ex = FakeExchange(history(5_000), page_cap=200)
    bars = run(DataEngine(ex).fetch_historical_ohlcv("BTC/USDT", "15m", limit=1_000))
    # exchange returns <= 200 per call; paging backward must still accumulate 1000 bars
    assert len(bars) == 1_000
    assert bars[-1][0] == NOW


def test_empty_exchange_returns_empty():
    assert run(DataEngine(FakeExchange([])).fetch_historical_ohlcv("BTC/USDT", "1h", limit=10)) == []


def test_unsupported_timeframe_is_rejected():
    with pytest.raises(ValueError):
        run(DataEngine(FakeExchange(history(10))).fetch_historical_ohlcv("BTC/USDT", "fortnight", limit=10))
