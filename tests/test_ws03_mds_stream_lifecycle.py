"""WS-03 follow-ups — a failing mds stream must stop failing, and unsubscribe must unsubscribe.

WHAT WAS WRONG
--------------
Two defects in ``backend_app/mds/main.py``, both found by DRIVING the service during WS-03
rather than by reading it, and both recorded there as outstanding when that finding closed.

1. **A permanent fault was retried forever at a fixed rate.** The websocket loop caught every
   exception, logged it, slept five seconds and tried again, with nothing counting. When ccxt
   4.3.92 turned out to be incompatible with the pinned aiohttp, every ``watch_ohlcv`` call
   raised ``'aiohttp._websocket.reader_c.WebSocketReader' object has no attribute
   'parse_frame'`` -- measured at 34 error events in three minutes, per subscription,
   indefinitely. The service was scaled to zero to stop the bleeding.

   The loop had a designed fallback the whole time: ``NotSupported`` breaks out into REST
   polling, and every payload carries a ``transport`` field so a subscriber can tell which
   path it is reading. A websocket failing a dozen times consecutively is operationally the
   same condition, learned by observation instead of declaration. **Had a ceiling existed,
   WS-03 would have been a degraded feed rather than a total outage** -- REST candles,
   correctly labelled, instead of nothing at all.

2. **``unsubscribe`` was ``pass``**, under the comment "Memory optimization / cleanup is
   omitted for this implementation". It is not a memory optimization. The probe published an
   unsubscribe in a ``finally`` and the five-second error loop carried straight on. An
   unsubscribe that does not stop the stream is not an unsubscribe.

   And because ``subscribe`` asked only whether the KEY was present, a task that had ended
   left an entry behind that could never be replaced: every later subscribe for that pair
   matched the dead entry and silently did nothing.

NO NETWORK. Every exchange here is a local object, and ``asyncio.sleep`` is replaced so the
ceilings are reached in microseconds instead of a minute.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import backend_app.mds.main as mds_main


class _FakeRedisOps:
    """The handful of manager methods ``broadcast_ohlcv`` calls."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []
        self.setex_calls: dict[str, tuple[int, str]] = {}

    async def publish(self, channel, message):
        self.published.append((channel, message))

    async def setex(self, key, ttl, value):
        self.setex_calls[key] = (ttl, value)


CANDLE = [1700000000000, 100.0, 110.0, 90.0, 105.5, 12.5]


class _NotSupported(Exception):
    """``broadcast_ohlcv`` string-matches on ``str(type(e))``, so the NAME is the contract."""


@pytest.fixture
def instant_sleep(monkeypatch):
    """Replace the retry backoff so a ceiling of N costs microseconds, not N*5 seconds.

    Patched on the `asyncio` module the module under test holds, which is the real one, so
    this is deliberately scoped to a single test by `monkeypatch`. The replacement awaits
    nothing -- awaiting the real `sleep` would recurse -- and the loops it runs inside are
    bounded by the ceilings under test, so nothing spins indefinitely.
    """

    async def _no_wait(_seconds):
        return None

    monkeypatch.setattr(mds_main.asyncio, "sleep", _no_wait)


@pytest.fixture
def wired_redis(monkeypatch):
    fake = _FakeRedisOps()
    monkeypatch.setattr(mds_main.redis_manager, "publish", fake.publish)
    monkeypatch.setattr(mds_main.redis_manager, "setex", fake.setex)
    return fake


@pytest.fixture(autouse=True)
def _clear_streams():
    mds_main.active_streams.clear()
    yield
    mds_main.active_streams.clear()


# ══════════════════════════════════════════════════════════════════════════════════════
#  1 — THE RETRY CEILING
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_a_permanently_failing_websocket_falls_back_to_rest(
    monkeypatch, wired_redis, instant_sleep
):
    """The WS-03 shape: every websocket call raises the same error, forever.

    It must stop attempting the websocket and deliver over REST instead -- which is the
    outcome that would have kept paper sessions fed through WS-03.
    """
    monkeypatch.setattr(mds_main, "WS_CONSECUTIVE_FAILURE_CEILING", 3)

    class AlwaysBrokenSocket:
        def __init__(self):
            self.ws_calls = 0
            self.rest_calls = 0

        async def watch_ohlcv(self, symbol, timeframe):
            self.ws_calls += 1
            # The exact WS-03 error, by message and by type.
            raise AttributeError(
                "'aiohttp._websocket.reader_c.WebSocketReader' object has no attribute "
                "'parse_frame'"
            )

        async def fetch_ohlcv(self, symbol, timeframe, limit=2):
            self.rest_calls += 1
            if self.rest_calls == 1:
                return [[0, 0, 0, 0, 0, 0], CANDLE]
            raise asyncio.CancelledError()

    venue = AlwaysBrokenSocket()
    monkeypatch.setattr(mds_main, "get_exchange_instance", lambda _ex: venue)

    with pytest.raises(asyncio.CancelledError):
        await mds_main.broadcast_ohlcv("binance", "BTC/USDT")

    assert venue.ws_calls == 3, (
        "the websocket was attempted a number of times other than the ceiling -- it either "
        "gave up early or kept retrying past it"
    )
    assert venue.rest_calls >= 1, "it never fell through to the REST transport"

    # And the candle that arrived is LABELLED as the fallback, so a subscriber knows.
    assert wired_redis.published, "no candle was published at all"
    _, raw = wired_redis.published[-1]
    assert json.loads(raw)["transport"] == mds_main.TRANSPORT_REST


@pytest.mark.asyncio
async def test_a_successful_candle_resets_the_failure_count(
    monkeypatch, wired_redis, instant_sleep
):
    """CONSECUTIVE, not cumulative.

    Without a reset, a long-lived healthy stream would accumulate occasional errors and
    eventually demote itself to REST for no reason. Here the ceiling is 3 and a success
    lands on the third call, so the fallback must not happen until call 6.
    """
    monkeypatch.setattr(mds_main, "WS_CONSECUTIVE_FAILURE_CEILING", 3)

    class FlakyThenBroken:
        def __init__(self):
            self.ws_calls = 0
            self.rest_calls = 0

        async def watch_ohlcv(self, symbol, timeframe):
            self.ws_calls += 1
            if self.ws_calls == 3:
                return [CANDLE]
            raise RuntimeError("transient socket fault")

        async def fetch_ohlcv(self, symbol, timeframe, limit=2):
            self.rest_calls += 1
            raise asyncio.CancelledError()

    venue = FlakyThenBroken()
    monkeypatch.setattr(mds_main, "get_exchange_instance", lambda _ex: venue)

    with pytest.raises(asyncio.CancelledError):
        await mds_main.broadcast_ohlcv("binance", "BTC/USDT")

    assert venue.ws_calls == 6, (
        f"expected 2 failures, a success that resets the count, then 3 more failures to "
        f"reach the ceiling; the websocket was called {venue.ws_calls} times"
    )


@pytest.mark.asyncio
async def test_the_rest_path_stops_rather_than_polling_a_dead_symbol_forever(
    monkeypatch, wired_redis, instant_sleep
):
    """There is nothing after REST, so the stream STOPS instead of falling through.

    A symbol that can never poll -- delisted, or a typo in a subscribe command -- otherwise
    retries for the life of the container. Stopping returns normally, which is what lets the
    done-callback deregister it and the pair become re-subscribable.
    """
    monkeypatch.setattr(mds_main, "REST_CONSECUTIVE_FAILURE_CEILING", 3)

    class NoSocketAndNoRest:
        def __init__(self):
            self.rest_calls = 0

        async def watch_ohlcv(self, symbol, timeframe):
            raise _NotSupported("websocket not supported")

        async def fetch_ohlcv(self, symbol, timeframe, limit=2):
            self.rest_calls += 1
            raise ValueError("binance does not have market symbol NOPE/USDT")

    venue = NoSocketAndNoRest()
    monkeypatch.setattr(mds_main, "get_exchange_instance", lambda _ex: venue)

    # Returns. No exception, no infinite loop, and the test itself would hang if it looped.
    result = await asyncio.wait_for(
        mds_main.broadcast_ohlcv("binance", "NOPE/USDT"), timeout=10
    )

    assert result is None
    assert venue.rest_calls == 3
    assert not wired_redis.published, "a failing poll published a candle"


def test_the_shipped_ceilings_are_bounded_and_nonzero():
    """The values that actually ship, as opposed to the ones the tests above inject."""
    assert 1 < mds_main.WS_CONSECUTIVE_FAILURE_CEILING <= 60
    assert 1 < mds_main.REST_CONSECUTIVE_FAILURE_CEILING <= 200
    # REST is the last resort, so it must be more patient than the socket it fell back from.
    assert (
        mds_main.REST_CONSECUTIVE_FAILURE_CEILING
        > mds_main.WS_CONSECUTIVE_FAILURE_CEILING
    )


# ══════════════════════════════════════════════════════════════════════════════════════
#  2 — UNSUBSCRIBE, AND THE REGISTRY THAT TRACKS IT
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_unsubscribe_cancels_the_stream_and_deregisters_it():
    """The defect: this used to be `pass`, and the stream ran on."""
    started = asyncio.Event()

    async def forever():
        started.set()
        while True:
            await asyncio.sleep(0)

    key = ("binance", "BTC/USDT")
    task = asyncio.create_task(forever())
    mds_main.active_streams[key] = task
    await started.wait()

    await mds_main._cancel_stream(key, "binance", "BTC/USDT")

    assert task.cancelled() or task.done(), "the stream task is still running"
    assert key not in mds_main.active_streams


@pytest.mark.asyncio
async def test_unsubscribing_a_pair_that_is_not_streaming_is_a_no_op():
    await mds_main._cancel_stream(("binance", "NOPE/USDT"), "binance", "NOPE/USDT")
    assert mds_main.active_streams == {}


@pytest.mark.asyncio
async def test_a_finished_stream_is_forgotten_so_the_pair_can_be_resubscribed():
    """`active_streams` must mean "running", not "was started once".

    The old `key not in active_streams` check made a stopped stream permanently
    unrestartable, and the health endpoint's `active_streams` count drift upward forever.
    """

    async def ends_immediately():
        return None

    key = ("binance", "BTC/USDT")
    task = asyncio.create_task(ends_immediately())
    mds_main.active_streams[key] = task
    task.add_done_callback(lambda t, k=key: mds_main._forget_stream(k, t))

    await task
    await asyncio.sleep(0)  # let the done-callback run

    assert key not in mds_main.active_streams


@pytest.mark.asyncio
async def test_forgetting_does_not_deregister_a_replacement_stream():
    """The identity guard, which is the subtle half of the cleanup.

    Between a task ending and its callback running, a re-subscribe may already have
    registered a NEW task under the same key. Popping blindly would deregister the live
    stream and leave it running untracked -- unstoppable by unsubscribe and invisible to
    the health count.
    """

    async def ends_immediately():
        return None

    async def forever():
        while True:
            await asyncio.sleep(0)

    key = ("binance", "BTC/USDT")
    old = asyncio.create_task(ends_immediately())
    await old

    replacement = asyncio.create_task(forever())
    mds_main.active_streams[key] = replacement

    # The OLD task's callback fires late, after the replacement is registered.
    mds_main._forget_stream(key, old)

    assert mds_main.active_streams.get(key) is replacement, (
        "the late callback for a finished task deregistered its live replacement"
    )

    replacement.cancel()
    try:
        await replacement
    except asyncio.CancelledError:
        pass


# ══════════════════════════════════════════════════════════════════════════════════════
#  3 — THE COMMAND HANDLER, where both defects were observable
# ══════════════════════════════════════════════════════════════════════════════════════

class _FakePubSub:
    """Yields a fixed script of commands, then ends -- which ends `handle_commands`.

    IT YIELDS TO THE EVENT LOOP BETWEEN COMMANDS, and that is not test convenience. A real
    Redis pubsub awaits a socket read between messages, so the tasks `handle_commands`
    spawned get to run. A fake that delivered the whole script in one uninterrupted pass
    would have `handle_commands` cancel a stream that had never been scheduled and judge a
    task "not done" that had never had the chance to finish -- measuring the fake's
    scheduling rather than the handler's logic.
    """

    def __init__(self, commands):
        self._commands = commands
        self.subscribed_to: list[str] = []

    async def subscribe(self, channel):
        self.subscribed_to.append(channel)

    async def listen(self):
        for command in self._commands:
            yield {"type": "message", "data": json.dumps(command)}
            # Enough iterations for a spawned task to be scheduled, run, finish, and have
            # its done-callback execute.
            for _ in range(4):
                await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_subscribe_then_unsubscribe_leaves_nothing_running(monkeypatch):
    """End to end over the command channel, which is how WS-03's probe drove it."""
    pubsub = _FakePubSub([
        {"action": "subscribe", "exchange": "binance", "symbol": "BTC/USDT"},
        {"action": "unsubscribe", "exchange": "binance", "symbol": "BTC/USDT"},
    ])
    monkeypatch.setattr(mds_main.redis_manager, "pubsub", lambda: pubsub, raising=False)

    started = asyncio.Event()

    async def forever(_exchange_id, _symbol):
        started.set()
        while True:
            await asyncio.sleep(0)

    monkeypatch.setattr(mds_main, "broadcast_ohlcv", forever)

    await asyncio.wait_for(mds_main.handle_commands(), timeout=10)

    assert pubsub.subscribed_to == ["mds:commands"]
    assert started.is_set(), "the subscribe never started a stream"
    assert mds_main.active_streams == {}, (
        "the unsubscribe left a stream registered, which is the defect: a dead "
        "subscription that keeps running and cannot be restarted"
    )


@pytest.mark.asyncio
async def test_a_pair_whose_stream_ended_can_be_subscribed_again(monkeypatch):
    """The second half of the `pass` defect.

    `subscribe` asked only whether the KEY was present. A task that had ended left its
    entry behind, so every later subscribe for that pair matched a corpse and did nothing.
    """
    starts = []

    async def ends_immediately(exchange_id, symbol):
        starts.append((exchange_id, symbol))

    pubsub = _FakePubSub([
        {"action": "subscribe", "exchange": "binance", "symbol": "BTC/USDT"},
        {"action": "subscribe", "exchange": "binance", "symbol": "BTC/USDT"},
    ])
    monkeypatch.setattr(mds_main.redis_manager, "pubsub", lambda: pubsub, raising=False)
    monkeypatch.setattr(mds_main, "broadcast_ohlcv", ends_immediately)

    await asyncio.wait_for(mds_main.handle_commands(), timeout=10)
    # Let the first task finish and its callback deregister it before judging the second.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(starts) == 2, (
        f"the second subscribe was refused because a finished task was still registered; "
        f"streams started: {starts}"
    )
