"""LOOP-STALL-01 -- a frozen event loop is reported with the line it is stuck on."""
import asyncio
import logging
import time

import pytest

from backend_app.core.loop_stall_monitor import LoopStallMonitor, start_loop_stall_monitor


def _blocking_call_that_freezes_the_loop():
    time.sleep(0.6)  # a synchronous call inside async code: the defect class being hunted


@pytest.mark.asyncio
async def test_a_stall_is_reported_with_the_blocking_frame(caplog):
    caplog.set_level(logging.WARNING, logger="vyomquant.loop_stall")
    m = LoopStallMonitor(stall_seconds=0.25, tick_seconds=0.05)
    m.start()
    try:
        await asyncio.sleep(0.1)
        _blocking_call_that_freezes_the_loop()
        await asyncio.sleep(0.2)  # let the loop recover and the watcher notice
    finally:
        m.stop()
    text = caplog.text
    assert m.stalls_reported == 1
    assert "event loop unresponsive" in text
    assert "_blocking_call_that_freezes_the_loop" in text, "the stack must name the culprit"
    assert "recovered after" in text


@pytest.mark.asyncio
async def test_a_healthy_loop_reports_nothing(caplog):
    caplog.set_level(logging.WARNING, logger="vyomquant.loop_stall")
    m = LoopStallMonitor(stall_seconds=0.25, tick_seconds=0.05)
    m.start()
    for _ in range(10):
        await asyncio.sleep(0.03)
    m.stop()
    assert m.stalls_reported == 0
    assert "LOOP-STALL-01" not in caplog.text


@pytest.mark.asyncio
async def test_it_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("LOOP_STALL_MONITOR", "0")
    assert start_loop_stall_monitor() is None


@pytest.mark.asyncio
async def test_a_stall_is_also_sent_to_sentry(monkeypatch):
    import sentry_sdk
    sent = []
    monkeypatch.setattr(sentry_sdk, "capture_message", lambda msg, level=None: sent.append((msg, level)))
    m = LoopStallMonitor(stall_seconds=0.25, tick_seconds=0.05)
    m.start()
    try:
        await asyncio.sleep(0.1)
        _blocking_call_that_freezes_the_loop()
        await asyncio.sleep(0.2)
    finally:
        m.stop()
    assert sent and sent[0][0].startswith("LOOP-STALL-01") and sent[0][1] == "warning"
