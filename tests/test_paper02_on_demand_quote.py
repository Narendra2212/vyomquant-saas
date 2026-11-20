"""PAPER-02 -- the two defects that made a manual market order unfillable, and the fix.

DEFECT 1: THE PREVIOUS FIX WAS INERT. MEASURED, NOT INFERRED.
-------------------------------------------------------------
PAPER-02 was recorded as "manual orders succeed for streamed symbols, refuse honestly
otherwise". The first half was false. ``place_order`` normalises its symbol with
``_clean_symbol`` (``upper().replace("/", "-")`` -> ``BTC-USDT``) and looked the price cache
up under that form, while ``mds/main.py`` keys the cache on the symbol the subscribe command
named -- for a Paper_Session the ccxt market symbol ``BTC/USDT``. So:

    writer:  mds:last_price:binance:BTC/USDT
    reader:  mds:last_price:binance:BTC-USDT

Those keys never match, so a manual market order could NOT fill even for a symbol an active
Paper_Session was streaming. Every one refused with ``NO_FRESH_PRICE``.

The existing tests missed it because they call ``get_fresh_market_price("BTC/USDT")``
directly with an already-slashed symbol, bypassing ``_clean_symbol`` -- exercising the key
FORMAT (always consistent between writer and reader) and never the symbol FORM that reaches
it from a real order. ``test_the_key_format_agrees_between_writer_and_reader`` passed
throughout.

DEFECT 2: THE HARDCODED VENUE.
------------------------------
The reader hardcoded ``"binance"`` while a Paper_Session runs on ``DEFAULT_EXCHANGE``, so any
deployment on another venue wrote keys this reader never looked at.

THE COVERAGE GAP, NOW CLOSED
----------------------------
PAPER-02's recorded blocker: an on-demand subscribe could not be bounded because
``watch_ohlcv`` on a 1-minute timeframe "can legitimately take up to a minute to yield its
first candle", and the sandbox had "no reachable exchange to measure that against".

Both halves are addressed rather than argued with. The venue IS reachable. And the premise is
sidestepped: a candle stream withholds its first value until a bar CLOSES, whereas a ticker
read returns the current last price immediately. So the on-demand path asks for a price, not
for a candle. Measured against the live venue (36 warm calls, BTC/ETH/DOGE, zero failures):
median 201 ms, p99 516 ms; cold first call 6,105 ms. The bound is 3 s -- ~5.8x the warm p99,
and deliberately below the cold-start cost so the first order in a fresh process refuses
honestly rather than holding an HTTP worker for six seconds.

This is "subscribe and DON'T wait": the order is priced from REST, and a subscribe is
published so the next order for the same symbol is served from the cache for free.

SCOPE
-----
All tests here use fakes except ``TestAgainstTheLiveVenue``, which is skipped unless
``PAPER02_LIVE_VENUE=1``. No order is ever placed at a real venue by anything in this file --
paper trading does not reach a venue to execute, only to read a price.
"""
from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal

import pytest

import importlib

import backend_app.mds.main as mds_main
from backend_app.backend import paper_trading_service as pts

#: Captured at import, BEFORE conftest's autouse `_no_on_demand_venue_quotes` guard can
#: replace it. That guard exists so no other suite reaches the network; this file is the one
#: that tests the on-demand path, so `_with_venue` restores the real function and supplies a
#: fake venue underneath it.
_REAL_FETCH_VENUE_QUOTE = pts.fetch_venue_quote


def _use_redis(monkeypatch, fake):
    """Patch the redis manager on its own module object.

    `importlib.import_module` rather than `import backend_app.core.cache.redis_manager as
    m`, because `backend_app/core/cache/__init__.py` re-exports the `redis_manager`
    INSTANCE under the same name as the submodule, so the plain import binds the object and
    `setattr` then fails with "SharedRedisManager has no attribute 'redis_manager'".
    """
    module = importlib.import_module("backend_app.core.cache.redis_manager")
    monkeypatch.setattr(module, "redis_manager", fake)


class _FakeRedis:
    """``redis_manager.redis`` returns self on the real manager; mirrored here."""

    def __init__(self, seeded=None):
        self.store = dict(seeded or {})
        self.published = []

    @property
    def redis(self):
        return self

    async def get(self, key):
        return self.store.get(key)

    async def publish(self, channel, message):
        self.published.append((channel, message))

    async def setex(self, key, ttl, value):
        self.store[key] = value


def _cached(price, exchange="binance", symbol="BTC/USDT"):
    return {
        mds_main.last_price_key(exchange, symbol): json.dumps(
            {"price": price, "timestamp": 1, "transport": "WEBSOCKET"}
        )
    }


# ══════════════════════════════════════════════════════════════════════════════
#  1. Defect 1: the symbol form, which is what made the fix inert
# ══════════════════════════════════════════════════════════════════════════════


def test_the_writers_key_and_the_readers_key_agree_for_an_order_shaped_symbol():
    """THE HEADLINE. The comparison the old tests never made.

    `_clean_symbol` is what `place_order` actually passes, so this is the only form that
    matters -- and it is the one that did not work.
    """
    svc = pts.PaperTradingService()
    order_form = svc._clean_symbol("BTC/USDT")
    assert order_form == "BTC-USDT", "the platform's internal form is dash-separated"

    written = mds_main.last_price_key("binance", "BTC/USDT")
    read = pts._last_price_key(pts._paper_price_exchange(), pts._venue_symbol(order_form))

    assert read == written, (
        f"a Paper_Session writes {written} and a manual order reads {read}; when these "
        f"differ no manual market order can ever fill, whatever is streaming"
    )


def test_a_cached_price_is_found_using_the_form_place_order_passes(monkeypatch):
    """End to end through the real reader, with the symbol as an order supplies it."""
    svc = pts.PaperTradingService()
    order_form = svc._clean_symbol("BTC/USDT")  # 'BTC-USDT'
    fake = _FakeRedis(_cached(65_000.5))

    _use_redis(monkeypatch, fake)
    price = asyncio.run(pts.get_fresh_market_price(order_form))

    assert price == Decimal("65000.5"), (
        "the dash form must resolve against the slash-keyed cache entry"
    )


def test_the_slashed_form_still_works_too(monkeypatch):
    """Backward compatibility: the direct callers the old tests used must not break."""
    fake = _FakeRedis(_cached(65_000.5))
    _use_redis(monkeypatch, fake)
    assert asyncio.run(pts.get_fresh_market_price("BTC/USDT")) == Decimal("65000.5")


def test_a_dash_keyed_cache_entry_is_also_read(monkeypatch):
    """`data_seeking_engine.stream_live_ohlcv` is the other subscriber and nothing
    normalises what it is handed, so both spellings are accepted."""
    fake = _FakeRedis(_cached(42.0, symbol="FOO-BAR"))
    _use_redis(monkeypatch, fake)
    assert asyncio.run(pts.get_fresh_market_price("FOO-BAR")) == Decimal("42")


# ══════════════════════════════════════════════════════════════════════════════
#  2. Defect 2: the venue is configuration, not a constant
# ══════════════════════════════════════════════════════════════════════════════


def test_the_price_venue_follows_default_exchange(monkeypatch):
    monkeypatch.setenv("DEFAULT_EXCHANGE", "kraken")
    assert pts._paper_price_exchange() == "kraken"


def test_the_venue_falls_back_to_the_same_default_mds_uses(monkeypatch):
    """`mds/main.py::handle_commands` uses `data.get("exchange", "binance")`."""
    monkeypatch.delenv("DEFAULT_EXCHANGE", raising=False)
    assert pts._paper_price_exchange() == pts.PAPER_PRICE_SOURCE_EXCHANGE == "binance"


def test_a_session_on_another_venue_is_readable(monkeypatch):
    """The case the hardcoded constant broke outright."""
    monkeypatch.setenv("DEFAULT_EXCHANGE", "kraken")
    fake = _FakeRedis(_cached(31_337.0, exchange="kraken"))
    _use_redis(monkeypatch, fake)
    assert asyncio.run(pts.get_fresh_market_price("BTC-USDT")) == Decimal("31337")


# ══════════════════════════════════════════════════════════════════════════════
#  3. The on-demand resolve: bounded, and never from a mock
# ══════════════════════════════════════════════════════════════════════════════


class _FakeVenue:
    def __init__(self, last=123.45, delay=0.0, raises=None):
        self._last, self._delay, self._raises = last, delay, raises
        self.asked = []

    async def fetch_ticker(self, symbol):
        self.asked.append(symbol)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises:
            raise self._raises
        return {"symbol": symbol, "last": self._last}


def _with_venue(monkeypatch, venue, mocked=False):
    """Supply a fake venue, and re-enable the real on-demand resolve for this test."""
    import backend_app.backend.asset_universe as au
    import backend_app.backend.connection_engine as ce

    async def _get(**kwargs):
        return venue

    monkeypatch.setattr(ce, "get_or_create_exchange", _get)
    monkeypatch.setattr(au, "markets_are_mocked", lambda ex: mocked)
    # Undo conftest's suite-wide guard: this file is where the on-demand path is tested.
    monkeypatch.setattr(pts, "fetch_venue_quote", _REAL_FETCH_VENUE_QUOTE)


def test_an_unstreamed_symbol_is_priced_from_the_venue(monkeypatch):
    """The coverage gap PAPER-02 recorded as deliberately unbuilt."""
    venue = _FakeVenue(last=99.5)
    _with_venue(monkeypatch, venue)

    quote = asyncio.run(pts.fetch_venue_quote("NEW-USDT", "binance"))

    assert quote == Decimal("99.5")
    assert venue.asked == ["NEW/USDT"], (
        "the venue must be asked in its own symbol form, not the platform's"
    )


def test_a_mocked_interface_is_refused(monkeypatch):
    """`_apply_mock_interface.fetch_ticker` returns a HARDCODED price.

    Filling from it would reinstate PAPER-02's original defect by a new route.
    """
    _with_venue(monkeypatch, _FakeVenue(last=50_000.0), mocked=True)
    assert asyncio.run(pts.fetch_venue_quote("BTC-USDT", "binance")) is None


def test_a_slow_venue_is_bounded_and_refuses(monkeypatch):
    """An HTTP request handler must not be held open indefinitely."""
    _with_venue(monkeypatch, _FakeVenue(delay=5.0))
    quote = asyncio.run(pts.fetch_venue_quote("BTC-USDT", "binance", timeout=0.05))
    assert quote is None


def test_the_bound_has_real_headroom_over_the_measured_p99():
    """Measured: warm p99 516 ms. Pinned so the constant cannot drift into fragility."""
    measured_p99_seconds = 0.516
    assert pts.ON_DEMAND_QUOTE_TIMEOUT_SECONDS >= measured_p99_seconds * 3
    assert pts.ON_DEMAND_QUOTE_TIMEOUT_SECONDS <= 10.0, (
        "a bound this long would hold a request worker rather than refusing honestly"
    )


@pytest.mark.parametrize(
    "ticker",
    [{"last": None, "close": None}, {}, {"last": "not-a-number"}, {"last": 0},
     {"last": -5}],
)
def test_an_unusable_ticker_is_none_not_a_fill(monkeypatch, ticker):
    class _Odd:
        async def fetch_ticker(self, symbol):
            return ticker

    _with_venue(monkeypatch, _Odd())
    assert asyncio.run(pts.fetch_venue_quote("BTC-USDT", "binance")) is None


def test_a_venue_error_is_none_not_an_exception(monkeypatch):
    _with_venue(monkeypatch, _FakeVenue(raises=RuntimeError("venue down")))
    assert asyncio.run(pts.fetch_venue_quote("BTC-USDT", "binance")) is None


def test_a_close_only_ticker_is_accepted(monkeypatch):
    class _CloseOnly:
        async def fetch_ticker(self, symbol):
            return {"close": 77.25}

    _with_venue(monkeypatch, _CloseOnly())
    assert asyncio.run(pts.fetch_venue_quote("BTC-USDT", "binance")) == Decimal("77.25")


# ══════════════════════════════════════════════════════════════════════════════
#  4. resolve_market_price: cheapest source first, then warm the cache
# ══════════════════════════════════════════════════════════════════════════════


def test_the_cache_is_preferred_and_the_venue_is_not_touched(monkeypatch):
    venue = _FakeVenue()
    _with_venue(monkeypatch, venue)
    fake = _FakeRedis(_cached(65_000.0))
    _use_redis(monkeypatch, fake)

    assert asyncio.run(pts.resolve_market_price("BTC-USDT")) == Decimal("65000")
    assert venue.asked == [], "a cached price must cost nothing at the venue"


def test_a_cache_miss_falls_through_to_the_venue_and_publishes_a_subscribe(monkeypatch):
    """The 'subscribe' half, with the waiting removed: the NEXT order reads the cache."""
    _with_venue(monkeypatch, _FakeVenue(last=5.5))
    fake = _FakeRedis()
    _use_redis(monkeypatch, fake)

    assert asyncio.run(pts.resolve_market_price("NEW-USDT")) == Decimal("5.5")

    assert fake.published, "a subscribe must be published so the cache warms"
    channel, message = fake.published[-1]
    assert channel == "mds:commands"
    payload = json.loads(message)
    assert payload == {"action": "subscribe", "exchange": "binance",
                       "symbol": "NEW/USDT"}, (
        "the command must name the pair in the form mds/main.py keys its stream table on"
    )


def test_a_failed_subscribe_does_not_fail_an_already_priced_order(monkeypatch):
    _with_venue(monkeypatch, _FakeVenue(last=5.5))

    class _NoPublish(_FakeRedis):
        async def publish(self, channel, message):
            raise RuntimeError("redis down")

    _use_redis(monkeypatch, _NoPublish())
    assert asyncio.run(pts.resolve_market_price("NEW-USDT")) == Decimal("5.5")


def test_no_price_anywhere_is_still_none(monkeypatch):
    """The honest refusal is preserved; it is now a last resort rather than the only one."""
    _with_venue(monkeypatch, _FakeVenue(raises=RuntimeError("no such market")))
    _use_redis(monkeypatch, _FakeRedis())
    assert asyncio.run(pts.resolve_market_price("NOPE-USDT")) is None


# ══════════════════════════════════════════════════════════════════════════════
#  5. The order path still refuses rather than inventing
# ══════════════════════════════════════════════════════════════════════════════


def _service(monkeypatch):
    svc = pts.PaperTradingService()
    monkeypatch.setattr(svc, "_client", lambda token=None: object())
    monkeypatch.setattr(
        svc, "_account_and_positions",
        lambda sb, uid: (
            {"id": "a1", "available_balance": "100000", "total_equity": "100000",
             "realized_pnl": "0", "initial_balance": "100000", "balance": "100000"}, []),
    )
    return svc


def test_a_market_order_with_no_resolvable_price_is_still_refused(monkeypatch):
    """Unchanged guarantee: a client-supplied price must never rescue a market order."""
    from backend_app.backend.paper.paper_market_feed import PaperMarketDataUnavailable

    svc = _service(monkeypatch)

    async def _none(symbol):
        return None

    monkeypatch.setattr(pts, "resolve_market_price", _none)

    with pytest.raises(PaperMarketDataUnavailable):
        asyncio.run(svc.place_order("u1", "BTC-USDT", "buy", "market", 0.01, price=1.0))


# ══════════════════════════════════════════════════════════════════════════════
#  6. Against the live venue (opt-in)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not os.environ.get("PAPER02_LIVE_VENUE", "").strip(),
    reason="set PAPER02_LIVE_VENUE=1 to read a real quote from the configured venue",
)
class TestAgainstTheLiveVenue:
    """Reads a real price. Places no order -- paper trading never reaches a venue to
    execute, only to read a price."""

    def test_a_real_quote_is_resolved_within_the_bound(self, monkeypatch):
        import backend_app.backend.asset_universe as au

        monkeypatch.setattr(au, "markets_are_mocked", lambda ex: False)
        # conftest's autouse guard replaces this with a refusal for the whole suite; the
        # whole point of this test is to call the real one.
        monkeypatch.setattr(pts, "fetch_venue_quote", _REAL_FETCH_VENUE_QUOTE)

        quote = asyncio.run(pts.fetch_venue_quote("BTC-USDT", "binance", timeout=30.0))

        assert quote is not None, "the live venue returned no quote"
        assert quote > 0
        assert Decimal("1000") < quote < Decimal("10000000"), (
            f"{quote} is not a credible BTC/USDT price; the resolve read the wrong thing"
        )
