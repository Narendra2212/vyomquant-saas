"""
tests/test_stream01_quiet_market_still_closes_candles.py

STREAM-01: a bot on a market nobody was trading never evaluated its strategy.

Candles are built from trade ticks. On a quiet market - an exchange TESTNET's USDC pairs,
or a thin real pair overnight - or on a websocket that stalls without raising, no tick
arrived, so no candle closed and the strategy never ran. The bot stayed "running",
silently. Measured in production during the live testnet run: a BTC/USDC bot reported
``phase: waiting_for_market_data, ticks: 0`` from boot (OBS-01's runtime_status).

``BotRunner._ticks_with_watchdog`` now yields a zero-volume tick at the venue's REST last
price once a bar boundary has passed with no trade, closing the bar through the same
``process_live_tick`` path a real trade uses.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend_app.backend.master_executor import BotRunner


class _SilentStream:
    """A trade stream that never yields: a quiet market or a stalled websocket."""

    def __init__(self):
        self.cancelled = False

    def stream_live_ticks(self, _symbol):
        outer = self

        class _It:
            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    outer.cancelled = True
                    raise

        return _It()


def _bot(*, timeframe_sec=60, last=50_000.0, ticker_error=None):
    bot = BotRunner.__new__(BotRunner)
    bot.symbol = "BTC/USDC"
    bot.timeframe_sec = timeframe_sec
    bot.blueprint = {}
    bot.graph_eval = None
    engine = _SilentStream()
    if ticker_error is not None:
        fetch = AsyncMock(side_effect=ticker_error)
    else:
        fetch = AsyncMock(return_value={"last": last})
    engine.exchange = SimpleNamespace(fetch_ticker=fetch)
    bot.data_engine = engine
    return bot


def test_no_synthetic_tick_before_a_bar_boundary_has_passed():
    bot = _bot(timeframe_sec=60)
    bot._last_tick_ms = 1_000_000 * 60_000 + 10_000          # 10 s into a bar
    with patch("backend_app.backend.master_executor.time.time",
               return_value=(1_000_000 * 60_000 + 40_000) / 1000):  # same bar, 40 s in
        assert asyncio.run(bot._synthetic_tick_if_bar_due()) is None
    bot.data_engine.exchange.fetch_ticker.assert_not_awaited()


def test_a_passed_bar_boundary_yields_a_zero_volume_tick_at_the_rest_price():
    bot = _bot(timeframe_sec=60, last=68_900.5)
    bot._last_tick_ms = 1_000_000 * 60_000 + 10_000
    now_s = (1_000_001 * 60_000 + 5_000) / 1000                # next bar, past the grace
    with patch("backend_app.backend.master_executor.time.time", return_value=now_s):
        tick = asyncio.run(bot._synthetic_tick_if_bar_due())
    assert tick == {"timestamp": int(now_s * 1000), "price": 68_900.5, "amount": 0.0,
                    "synthetic": True}
    assert bot._runtime_status["phase"] == "streaming_quiet_market"
    assert bot._runtime_status["synthetic_ticks"] == 1


def test_a_failed_price_poll_is_reported_and_yields_nothing():
    bot = _bot(timeframe_sec=60, ticker_error=RuntimeError("venue down"))
    bot._last_tick_ms = 1_000_000 * 60_000
    with patch("backend_app.backend.master_executor.time.time",
               return_value=(1_000_002 * 60_000 + 5_000) / 1000):
        assert asyncio.run(bot._synthetic_tick_if_bar_due()) is None
    assert bot._runtime_status["phase"] == "no_market_data"
    assert "venue down" in bot._runtime_status["last_error"]


def test_a_silent_stream_still_produces_ticks_and_its_read_is_never_lost():
    """The watchdog yields synthetic ticks while the stream's pending read stays alive."""
    bot = _bot(timeframe_sec=1, last=100.0)   # 1 s bars so a boundary passes quickly
    bot._watchdog_idle_seconds = lambda: 0.05

    async def scenario():
        gen = bot._ticks_with_watchdog()
        got = await asyncio.wait_for(gen.__anext__(), timeout=5)
        await gen.aclose()
        return got

    tick = asyncio.run(scenario())
    assert tick["synthetic"] is True and tick["price"] == 100.0 and tick["amount"] == 0.0
    assert bot.data_engine.cancelled, "closing the watchdog must cancel the pending stream read"


def test_a_zero_volume_tick_past_the_boundary_closes_the_bar():
    """The synthetic tick closes a bar through the same aggregator a real trade uses."""
    from backend_app.backend.data_processing_engine import DataProcessorEngine

    proc = DataProcessorEngine(["BTC/USDC"], [60], max_history=10)
    t0 = 1_000_000 * 60_000
    assert proc.process_live_tick("BTC/USDC", t0 + 5_000, 100.0, 1.0) == []
    closed = proc.process_live_tick("BTC/USDC", t0 + 65_000, 101.0, 0.0)
    assert len(closed) == 1 and closed[0]["close"] == 100.0
