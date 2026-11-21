"""LOOP-STALL-03 -- a stall report names OUR code, and the paper sweeper no longer stalls.

Production, 2026-11-21: 514 LOOP-STALL-01 events (VYOMQUANT-API-PRODUCTION-35), every one
titled "ssl.py:1181 in read". The monitor kept only the 12 innermost frames, and a blocking
supabase-py HTTP read is more than 12 frames deep in ssl/httpcore/httpx before it reaches
backend_app, so the report never said which of our loops made the call. The one sample with
a trace was `GET /rest/v1/paper_orders`, the PaperLimitOrderSweeper's 30-second
cross-tenant read, run synchronously on the event loop.
"""
from __future__ import annotations

import sys
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend_app.core import loop_stall_monitor as lsm

_LIB_DEPTH = 20  # deeper than _innermost_frames' 12-frame window


def _deep_stack_frame():
    """A real frame whose innermost 20 frames are 'library' and whose caller is 'ours'."""
    lib_src = "def lib_0(cb):\n    return cb()\n"
    for i in range(1, _LIB_DEPTH):
        lib_src += f"def lib_{i}(cb):\n    return lib_{i - 1}(cb)\n"
    lib_ns: dict = {}
    exec(compile(lib_src, "/usr/local/lib/python3.11/site-packages/httpx/_fake.py", "exec"), lib_ns)

    app_ns: dict = {"lib_top": lib_ns[f"lib_{_LIB_DEPTH - 1}"], "sys": sys}
    app_src = "def sweep_once():\n    return lib_top(lambda: sys._getframe(1))\n"
    exec(compile(app_src, "/app/backend_app/backend/fake_sweeper.py", "exec"), app_ns)
    return app_ns["sweep_once"]()


def test_the_app_frame_is_found_below_the_innermost_window():
    frame = _deep_stack_frame()
    innermost = lsm._innermost_frames(frame)
    assert not any(f.startswith("backend_app/") for f in innermost), "test stack not deep enough"

    app = lsm._app_frames(frame)
    assert app and app[0].startswith("backend_app/backend/fake_sweeper.py")
    assert app[0].endswith("in sweep_once")


def test_the_sentry_title_names_our_frame_not_ssl(monkeypatch):
    import sentry_sdk
    sent = []
    monkeypatch.setattr(sentry_sdk, "capture_message", lambda msg, level=None: sent.append(msg))
    frame = _deep_stack_frame()

    lsm._report_to_sentry(3.0, lsm._innermost_frames(frame), lsm._app_frames(frame))

    assert sent and "backend_app/backend/fake_sweeper.py" in sent[0]
    assert "in sweep_once" in sent[0]


def test_without_any_app_frame_the_innermost_frame_is_still_named(monkeypatch):
    import sentry_sdk
    sent = []
    monkeypatch.setattr(sentry_sdk, "capture_message", lambda msg, level=None: sent.append(msg))

    lsm._report_to_sentry(3.0, ["ssl.py:1181 in read"], [])

    assert sent and "ssl.py:1181 in read" in sent[0]


@pytest.mark.asyncio
async def test_the_sweepers_database_read_runs_off_the_event_loop():
    from backend_app.backend.paper_limit_order_sweeper import PaperLimitOrderSweeper

    loop_thread = threading.get_ident()
    read_threads = []

    def _read(sb):
        read_threads.append(threading.get_ident())
        return []

    with patch("backend_app.core.dependencies.get_service_supabase", return_value=MagicMock()), \
         patch("backend_app.backend.paper.paper_repository.get_open_legacy_limit_order_pairs",
               side_effect=_read), \
         patch("backend_app.backend.paper_trading_service.get_paper_trading_service",
               return_value=MagicMock(check_limit_orders=AsyncMock(return_value=[]))):
        filled = await PaperLimitOrderSweeper().run_once()

    assert filled == 0
    assert read_threads and read_threads[0] != loop_thread
