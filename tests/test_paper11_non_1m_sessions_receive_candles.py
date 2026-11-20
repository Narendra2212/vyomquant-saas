"""PAPER-11 -- a paper session on a timeframe other than 1m must be able to receive candles.

Production: a RUNNING 1h session had ``feed_state = PENDING`` and zero ``paper_market_events`` after
twelve minutes. ``mds`` streamed ONLY 1m candles (``timeframe = '1m'`` was hard-coded) and the session's
feed refuses any candle whose timeframe is not its own, so every candle was dropped as the wrong market.
Every timeframe except 1m was a silent dead end.
"""
from __future__ import annotations

import json

import pytest

from backend_app.backend.paper import paper_market_feed as feed

EX, SYM = "binance", "BTC/USDT"


def test_the_1m_wiring_is_byte_for_byte_what_it_always_was():
    assert feed.mds_data_channel(EX, SYM) == "mds:data:binance:BTC/USDT"
    assert feed.mds_data_channel(EX, SYM, "1m") == "mds:data:binance:BTC/USDT"
    assert json.loads(feed.mds_command("subscribe", "Binance", SYM, "1m")) == {
        "action": "subscribe", "exchange": "binance", "symbol": SYM,
    }
    assert json.loads(feed.mds_command("subscribe", EX, SYM)) == {
        "action": "subscribe", "exchange": "binance", "symbol": SYM,
    }


@pytest.mark.parametrize("tf", ["5m", "15m", "1h", "4h", "1d"])
def test_another_timeframe_has_its_own_channel_and_names_itself_in_the_command(tf):
    assert feed.mds_data_channel(EX, SYM, tf) == f"mds:data:binance:BTC/USDT:{tf}"
    assert feed.mds_data_channel(EX, SYM, tf) != feed.mds_data_channel(EX, SYM)
    assert json.loads(feed.mds_command("subscribe", EX, SYM, tf))["timeframe"] == tf
    assert json.loads(feed.mds_command("unsubscribe", EX, SYM, tf))["timeframe"] == tf


def test_a_sessions_feed_listens_where_its_timeframe_is_published():
    cfg = feed.PaperFeedConfig(
        session_id="s", user_id="u", exchange_id="Binance", symbol=SYM, timeframe="1h"
    )
    assert cfg.channel == "mds:data:binance:BTC/USDT:1h"
    one_minute = feed.PaperFeedConfig(
        session_id="s", user_id="u", exchange_id="Binance", symbol=SYM, timeframe="1m"
    )
    assert one_minute.channel == "mds:data:binance:BTC/USDT"


def test_the_subscribe_and_the_unsubscribe_name_the_same_stream():
    import asyncio

    published = []

    class _Redis:
        async def publish(self, channel, message):
            published.append((channel, json.loads(message)))

    asyncio.run(feed.publish_unsubscribe(_Redis(), exchange_id=EX, symbol=SYM, timeframe="1h"))
    (channel, message), = published
    assert channel == feed.MDS_COMMAND_CHANNEL
    assert message == {"action": "unsubscribe", "exchange": EX, "symbol": SYM, "timeframe": "1h"}


def test_the_service_publishes_on_the_channel_the_session_listens_on():
    mds = pytest.importorskip("backend_app.mds.main")
    for tf in ("1m", "5m", "15m", "1h", "4h"):
        assert mds.data_channel(EX, SYM, tf) == feed.mds_data_channel(EX, SYM, tf), tf
    assert mds.BASE_TIMEFRAME == feed.MDS_BASE_TIMEFRAME


def test_a_candle_for_the_sessions_own_timeframe_is_accepted_not_dropped():
    cfg = feed.PaperFeedConfig(
        session_id="s", user_id="u", exchange_id="binance", symbol=SYM, timeframe="1h"
    )
    payload = {"exchange": EX, "symbol": SYM, "timeframe": "1h"}
    assert feed._payload_matches_market(payload, cfg) is True
    assert feed._payload_matches_market({**payload, "timeframe": "1m"}, cfg) is False
