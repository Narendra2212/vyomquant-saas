"""PAPER-02 -- ``get_fresh_market_price`` reads a real, durable last price, or honestly refuses.

THE DEFECT (audit)
    ``get_fresh_market_price`` always returned ``None``: "No one-shot quote source is wired into
    this service yet (the MDS feed is session-scoped)". A manual market order therefore either
    filled at the client's own supplied price (the bug this symbol's own PAPER-02 tests already
    pin as forbidden) or was refused outright with no real price ever reachable.

THE FIX
    ``mds/main.py::broadcast_ohlcv`` already republishes every candle it reads, on both its
    websocket and REST-fallback paths; this adds ONE additive write alongside each existing
    publish: a durable, TTL'd ``mds:last_price:{exchange}:{symbol}`` cache entry. No second
    market-data path, no new exchange connection, no credentials -- the SAME loop, read a second
    way. ``get_fresh_market_price`` reads that cache for a fixed default exchange ("binance",
    matching ``mds/main.py``'s own fallback default), and returns ``None`` -- never a fabricated
    or stale price -- for every case but a real, unexpired cached value.

WHAT THIS FILE PROVES (fakes; no network, no real exchange, no real Redis)
    * the key format ``mds/main.py::last_price_key`` writes and
      ``paper_trading_service.py::_last_price_key`` reads are IDENTICAL -- the two were spelled
      independently (same convention as this codebase's own TRANSPORT_WEBSOCKET/TRANSPORT_REST
      literals) and a test is what holds them equal, not a shared import;
    * ``broadcast_ohlcv`` writes that key, with the TTL, on a real websocket tick AND on a real
      REST-fallback tick;
    * ``get_fresh_market_price`` reads a cached price back correctly;
    * no cached entry, an unreadable cache, a cache read that raises, and a non-numeric cached
      price are all read as ``None`` -- never as an exception, and never as a fabricated price.

NOT SHOWN, ON PURPOSE: a real exchange tick (needs network); an on-demand subscribe for a symbol
nothing is currently streaming -- ``get_fresh_market_price``'s own docstring explains why that
is not attempted here (an unbounded wait on a synchronous, inside-an-HTTP-request call).
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from unittest.mock import patch

import pytest

import backend_app.mds.main as mds_main
from backend_app.backend import paper_trading_service as pts


def test_the_key_format_agrees_between_writer_and_reader():
    assert pts._last_price_key("Binance", "BTC/USDT") == mds_main.last_price_key(
        "Binance", "BTC/USDT"
    ), (
        "the two files spell this key independently on purpose (mds/main.py is a standalone "
        "microservice process and cannot import this backend package) -- this test is what "
        "keeps them from silently drifting apart"
    )
    assert mds_main.last_price_key("BINANCE", "btc/usdt") == "mds:last_price:binance:btc/usdt"


class _FakeRedisOps:
    """Stands in for the handful of SharedRedisManager methods ``broadcast_ohlcv`` calls."""

    def __init__(self):
        self.published = []
        self.setex_calls = {}

    async def publish(self, channel, message):
        self.published.append((channel, message))

    async def setex(self, key, ttl, value):
        self.setex_calls[key] = (ttl, value)


class _OneWebsocketCandleExchange:
    """``watch_ohlcv`` yields one real candle, then the loop is cancelled -- the clean way to
    end ``broadcast_ohlcv``'s otherwise-infinite ``while True`` after exactly one tick."""

    def __init__(self, candle):
        self._candle = candle
        self.calls = 0

    async def watch_ohlcv(self, symbol, timeframe):
        self.calls += 1
        if self.calls == 1:
            return [self._candle]
        raise asyncio.CancelledError()


class _NoWebsocketOneRestCandleExchange:
    """Simulates the documented ``NotSupported`` websocket fallback: the first ``watch_ohlcv``
    call raises an exception whose type NAME contains "NotSupported" (``broadcast_ohlcv``
    string-matches on ``str(type(e))``, so a plain custom exception class named exactly that
    reproduces the branch without needing a real ccxt exception type), then
    ``fetch_ohlcv`` (the REST path) yields one candle before the loop is cancelled."""

    class NotSupported(Exception):
        pass

    def __init__(self, candle):
        self._candle = candle
        self.rest_calls = 0

    async def watch_ohlcv(self, symbol, timeframe):
        raise self.NotSupported("websocket not supported")

    async def fetch_ohlcv(self, symbol, timeframe, limit=2):
        self.rest_calls += 1
        if self.rest_calls == 1:
            return [[0, 0, 0, 0, 0, 0], self._candle]
        raise asyncio.CancelledError()


CANDLE = [1700000000000, 100.0, 110.0, 90.0, 105.5, 12.5]  # [ts, open, high, low, close, volume]


@pytest.mark.asyncio
class TestBroadcastOhlcvWritesTheLastPriceCache:
    async def test_a_websocket_tick_writes_the_cache_with_the_ttl(self, monkeypatch):
        fake_redis = _FakeRedisOps()
        monkeypatch.setattr(mds_main.redis_manager, "publish", fake_redis.publish)
        monkeypatch.setattr(mds_main.redis_manager, "setex", fake_redis.setex)
        monkeypatch.setattr(
            mds_main, "get_exchange_instance",
            lambda ex: _OneWebsocketCandleExchange(CANDLE),
        )

        with pytest.raises(asyncio.CancelledError):
            await mds_main.broadcast_ohlcv("binance", "BTC/USDT")

        key = mds_main.last_price_key("binance", "BTC/USDT")
        assert key in fake_redis.setex_calls
        ttl, raw = fake_redis.setex_calls[key]
        assert ttl == mds_main.LAST_PRICE_TTL_SECONDS
        payload = json.loads(raw)
        assert payload["price"] == 105.5
        assert payload["timestamp"] == 1700000000000
        assert payload["transport"] == mds_main.TRANSPORT_WEBSOCKET

    async def test_a_rest_fallback_tick_also_writes_the_cache(self, monkeypatch):
        fake_redis = _FakeRedisOps()
        monkeypatch.setattr(mds_main.redis_manager, "publish", fake_redis.publish)
        monkeypatch.setattr(mds_main.redis_manager, "setex", fake_redis.setex)
        monkeypatch.setattr(
            mds_main, "get_exchange_instance",
            lambda ex: _NoWebsocketOneRestCandleExchange(CANDLE),
        )

        with pytest.raises(asyncio.CancelledError):
            await mds_main.broadcast_ohlcv("binance", "ETH/USDT")

        key = mds_main.last_price_key("binance", "ETH/USDT")
        assert key in fake_redis.setex_calls
        _, raw = fake_redis.setex_calls[key]
        payload = json.loads(raw)
        assert payload["price"] == 105.5
        assert payload["transport"] == mds_main.TRANSPORT_REST


class _FakeRedisManager:
    """``redis_manager.redis`` is a property returning ``self`` on the real
    ``SharedRedisManager`` (see its own ``.redis`` property docstring); ``get_fresh_market_price``
    calls ``redis_manager.redis.get(...)``, matching ``mds/main.py``'s own ``.redis.publish(...)``
    convention. Mirrored here so a fake that lacks ``.redis`` does not silently exercise this
    module's own ``except Exception`` wrapper instead of the behaviour each test names."""

    def __init__(self, get_impl):
        self._get_impl = get_impl

    @property
    def redis(self):
        return self

    async def get(self, k):
        return self._get_impl(k)


@pytest.mark.asyncio
class TestGetFreshMarketPriceReadsTheCache:
    async def test_a_real_cached_price_is_returned_as_a_decimal(self):
        key = pts._last_price_key(pts.PAPER_PRICE_SOURCE_EXCHANGE, "BTC/USDT")
        cached = {key: json.dumps({"price": 65000.5, "timestamp": 123, "transport": "WEBSOCKET"})}
        fake = _FakeRedisManager(lambda k: cached.get(k))

        with patch("backend_app.core.cache.redis_manager.redis_manager", fake):
            price = await pts.get_fresh_market_price("BTC/USDT")

        assert price == Decimal("65000.5")

    async def test_no_cached_entry_is_none(self):
        fake = _FakeRedisManager(lambda k: None)
        with patch("backend_app.core.cache.redis_manager.redis_manager", fake):
            assert await pts.get_fresh_market_price("NOSYMBOL/USDT") is None

    async def test_a_cache_read_failure_is_none_not_an_exception(self):
        def _boom(k):
            raise RuntimeError("redis connection refused")

        fake = _FakeRedisManager(_boom)
        with patch("backend_app.core.cache.redis_manager.redis_manager", fake):
            assert await pts.get_fresh_market_price("BTC/USDT") is None

    async def test_an_unreadable_payload_is_none(self):
        fake = _FakeRedisManager(lambda k: "{not valid json")
        with patch("backend_app.core.cache.redis_manager.redis_manager", fake):
            assert await pts.get_fresh_market_price("BTC/USDT") is None

    async def test_a_non_numeric_cached_price_is_none(self):
        fake = _FakeRedisManager(lambda k: json.dumps({"price": "not-a-number"}))
        with patch("backend_app.core.cache.redis_manager.redis_manager", fake):
            assert await pts.get_fresh_market_price("BTC/USDT") is None

    async def test_production_default_with_no_mocking_is_none(self):
        """The pre-existing pin (test_paper02_market_orders_need_measured_price.py) must still
        hold: with nothing seeded in any real/mock cache, this returns None, never raises."""
        assert await pts.get_fresh_market_price("BTC-USDT") is None
