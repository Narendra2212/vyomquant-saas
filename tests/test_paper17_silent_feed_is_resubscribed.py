"""PAPER-17: a feed whose channel goes silent is dropped and re-subscribed, not waited on forever.

Production session c2f57492 (2026-11-11): the market-data service was replaced by a deploy at
07:23:55, seventeen seconds after the session asked it for BTC/USDT. The new ``mds`` process held
no stream for the pair and was never asked again; the session received one candle, then waited on
the data channel with no timeout - ``feed_state`` HEALTHY, no heartbeat - until reconciliation
marked it ABANDONED. Nothing was logged and nothing errored.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from backend_app.backend.paper import paper_market_feed as feed
PaperFeedDisconnected = feed.PaperFeedDisconnected
from tests.test_paper_market_feed_selection import (
    RecordingRedis,
    _admitting,
    _client,
    _open,
    _run_coroutine,
)
from tests.test_paper_market_feed_selection import (
    _fresh_probe,  # noqa: F401 - autouse, used by name
)


class _SilentPubSub:
    """A subscription that confirms, then never delivers anything (an ``mds`` that lost the pair)."""

    def __init__(self, redis: "SilentRedis") -> None:
        self.redis = redis
        self.channels: list = []

    async def subscribe(self, channel: str) -> None:
        self.channels.append(channel)
        self.redis.log.append(("subscribe", channel))

    async def unsubscribe(self, *channels: str) -> None:
        self.redis.log.append(("unsubscribe", ",".join(channels)))

    async def close(self) -> None:
        self.redis.log.append(("close", ""))

    def listen(self) -> Any:
        async def _frames() -> Any:
            yield {"type": "subscribe", "channel": self.channels[0], "data": 1}
            await asyncio.Event().wait()  # silence, forever
            yield {}  # pragma: no cover

        return _frames()


class SilentRedis(RecordingRedis):
    def pubsub(self) -> Any:  # type: ignore[override]
        self.pubsub_calls += 1
        self.log.append(("pubsub", ""))
        return _SilentPubSub(self)


def _subscribes(redis: RecordingRedis) -> int:
    return sum(1 for c in redis.commands() if c.get("action") == feed.MDS_ACTION_SUBSCRIBE)


def test_the_stall_window_is_bounded_by_default() -> None:
    assert 0 < feed.FEED_STALL_SECONDS <= 300


def test_a_silent_channel_raises_the_dropped_subscription() -> None:
    redis = SilentRedis()
    handle = _open(redis=redis, measurements=_admitting(), supabase=_client())
    handle.stall_seconds = 0.05

    with pytest.raises(PaperFeedDisconnected, match="no market data"):
        _run_coroutine(asyncio.wait_for(handle._receive(), timeout=5))


def test_a_silent_channel_is_marked_degraded_and_resubscribed(monkeypatch) -> None:
    """The recovery path: the next read reconnects, which re-publishes ``subscribe`` - the command
    a restarted ``mds`` needs to start the pair's stream again."""
    monkeypatch.setattr(feed, "backoff_delay_seconds", lambda attempt: Decimal("0"))
    client = _client()
    redis = SilentRedis()
    handle = _open(redis=redis, measurements=_admitting(), supabase=client)
    handle.stall_seconds = 0.05
    assert _subscribes(redis) == 1

    result = _run_coroutine(asyncio.wait_for(feed.next_validated_event(handle), timeout=5))

    assert result is None
    assert _subscribes(redis) == 2, "the stall must re-publish the mds subscribe command"
    assert client.sessions[0].get("feed_state") == "DEGRADED"
