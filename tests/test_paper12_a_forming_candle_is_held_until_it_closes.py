"""PAPER-12: a candle that is still forming must not be processed, stored or traded on.

mds republishes the forming candle on every socket update (about every 2 s). Each accepted event
was a stored ``paper_market_events`` row, a ``market_tick`` frame, a full strategy evaluation and
a chance to fill an order -- so a 1h session evaluated its strategy on a partial bar ~30 times a
minute and wrote ~40,000 rows a day (observed in production, session ae3ba995: 22 events for ONE
12:00 candle in 40 s).

The feed now holds the latest state of the forming candle and processes it once, with the last
values observed, when a NEWER candle opens. A candle already closed by the wall clock takes the
unchanged path, which is what every replay and fixture relies on.

The cases use 1-minute candles (the fixtures' timeframe); the gate is timeframe-agnostic.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend_app.backend.paper import paper_market_feed as feed
from tests.test_paper_market_feed_events import _deliver, _feed_on, _frame


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 11, 10, hour, minute, second, tzinfo=timezone.utc)


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    """A settable wall clock, starting 20 s into the 12:00 minute."""
    state = {"now": _at(12, 0, 20)}
    monkeypatch.setattr(feed, "_wall_now", lambda: state["now"])
    return state


def test_ticks_of_a_forming_candle_store_nothing_and_return_nothing(clock) -> None:
    open_ms = _ms(_at(12, 0))
    handle, redis, client = _feed_on([_frame(timestamp_ms=open_ms, close=60000.5)])

    assert _deliver(handle) is None
    for close in (60010.5, 60020.5, 60030.5):
        redis.frames.append(_frame(timestamp_ms=open_ms, close=close))
        assert _deliver(handle) is None

    assert client.market_events == []
    assert handle.sequence == 0
    assert handle.forming_held == 4


def test_a_forming_tick_still_marks_the_feed_alive(clock) -> None:
    handle, _redis, _client = _feed_on([_frame(timestamp_ms=_ms(_at(12, 0)), close=60000.5)])

    assert handle.feed_state == feed.FEED_STATE_PENDING
    _deliver(handle)
    assert handle.feed_state != feed.FEED_STATE_PENDING, (
        "a session must not read PENDING until its first candle closes"
    )


def test_the_next_candle_closes_the_held_one_with_its_last_values(clock) -> None:
    handle, redis, client = _feed_on([_frame(timestamp_ms=_ms(_at(12, 0)), close=60000.5)])
    assert _deliver(handle) is None
    redis.frames.append(_frame(timestamp_ms=_ms(_at(12, 0)), close=60040.5))
    assert _deliver(handle) is None

    clock["now"] = _at(12, 1, 3)  # 12:01 has opened; 12:00 is closed
    redis.frames.append(_frame(timestamp_ms=_ms(_at(12, 1)), close=60050.5))
    closed = _deliver(handle)

    assert closed is not None
    assert closed.event_timestamp == _at(12, 0)
    assert str(closed.close) == "60040.5", "the LAST observed values, not the first"
    assert closed.sequence == 1
    assert len(client.market_events) == 1


def test_an_already_closed_candle_is_processed_as_before(clock) -> None:
    handle, _redis, client = _feed_on([_frame(timestamp_ms=_ms(_at(11, 58)), close=60000.5)])

    event = _deliver(handle)

    assert event is not None and event.sequence == 1
    assert len(client.market_events) == 1


def test_a_candle_older_than_the_held_one_is_dropped_as_out_of_order(clock) -> None:
    handle, redis, client = _feed_on([_frame(timestamp_ms=_ms(_at(12, 0)), close=60000.5)])
    assert _deliver(handle) is None
    clock["now"] = _at(12, 1, 5)
    redis.frames.append(_frame(timestamp_ms=_ms(_at(12, 1)), close=60050.5))
    assert _deliver(handle) is not None  # 12:00 closed, 12:01 now held

    redis.frames.append(_frame(timestamp_ms=_ms(_at(12, 0)), close=1.0))
    assert _deliver(handle) is None
    assert len(client.market_events) == 1
