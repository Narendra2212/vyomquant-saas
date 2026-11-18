"""tests/test_notif05_sync_dispatch_bridge.py — NOTIF-05

Production holds ZERO notification rows against 1838 `execution_records`, 28
`strategy_deployments` and 6 `support_tickets` (measured 2026-11-18, backend current).
Neither NOTIF-01's persistence fix nor NOTIF-04's preference filter explains it: the
cause is that **nothing dispatches from the execution path at all**.
`backend/execution_engine.py` and the bot runner are SYNCHRONOUS, and the only sync
entry point they could have called was deleted — correctly — for being unsafe:

* `asyncio.get_event_loop()` is deprecated and RAISES in a thread with no loop, which
  is precisely the "sync execution context" it offered itself for;
* on the loop-is-running branch it called `create_task` and kept NO reference, so the
  task was collectable before it ran, and it returned `None` either way — a caller
  could not tell a delivered notification from a collected one.

So a sync producer has had nothing safe to call. This is that thing, and this file
pins the two properties that make it safe to call from inside a fill:

1. **It never raises.** A notification is an observation ABOUT a trade, never a
   participant in it. No failure here — no loop, a closed loop, a raising coroutine,
   a broken Future — may propagate into the code that just executed an order.
2. **The exception is REPORTED.** `run_coroutine_threadsafe` parks the exception on
   the Future; unread, the failure is silent. That silence is the defect that deleted
   the predecessor, so it is asserted directly.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, List

import pytest

from backend_app.core import notification_dispatcher as nd


@pytest.fixture(autouse=True)
def _clean_loop_registration():
    """No test may inherit another's registered loop."""
    nd.register_dispatch_loop(None)
    yield
    nd.register_dispatch_loop(None)


class _Loop:
    """A real event loop on a background thread, which is what production has."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        for _ in range(200):
            if self.loop.is_running():
                return
            time.sleep(0.01)
        raise RuntimeError("loop did not start")

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


@pytest.fixture()
def running_loop():
    lp = _Loop()
    try:
        yield lp
    finally:
        lp.stop()


def _kwargs(**over):
    base = dict(
        user_id="11111111-1111-4111-8111-111111111111",
        event_type="trade_executed",
        category="trading",
        title="Filled",
        message="BTC/USDT buy filled",
    )
    base.update(over)
    return base


# ───────── registration ─────────


def test_no_loop_registered_returns_none_and_does_not_raise() -> None:
    """DEV_MODE, sync unit tests, and a worker started before the API all land here.

    `None` means "there was no loop to queue onto", NOT "it failed" — which is why it
    is logged at debug. The important half is that it does not raise.
    """
    assert nd.dispatch_user_notification_threadsafe(**_kwargs()) is None


def test_the_registered_loop_is_readable_back() -> None:
    sentinel = object()
    nd.register_dispatch_loop(sentinel)
    assert nd.dispatch_loop() is sentinel


def test_registration_is_last_write_wins() -> None:
    """So a fixture or a re-entered lifespan can replace it without a guard."""
    nd.register_dispatch_loop(object())
    second = object()
    nd.register_dispatch_loop(second)
    assert nd.dispatch_loop() is second


def test_a_closed_loop_is_treated_as_no_loop(monkeypatch) -> None:
    """Shutdown ordering: a producer firing after the loop closes must not raise."""
    loop = asyncio.new_event_loop()
    loop.close()
    nd.register_dispatch_loop(loop)
    assert nd.dispatch_user_notification_threadsafe(**_kwargs()) is None


# ───────── it actually dispatches ─────────


def test_a_queued_notification_reaches_the_async_dispatcher(running_loop, monkeypatch) -> None:
    """The feature. Called from this (synchronous) test thread, executed on the loop."""
    seen: List[dict] = []

    async def _fake(**kw):
        seen.append(kw)
        return "notif-id"

    monkeypatch.setattr(nd, "dispatch_user_notification", _fake)
    nd.register_dispatch_loop(running_loop.loop)

    fut = nd.dispatch_user_notification_threadsafe(**_kwargs())
    assert fut is not None
    assert fut.result(timeout=5) == "notif-id"
    assert len(seen) == 1
    assert seen[0]["event_type"] == "trade_executed"


def test_the_caller_can_ignore_the_future_and_the_work_still_runs(running_loop, monkeypatch) -> None:
    """Which is how a fill path will use it: queue and carry on."""
    done = threading.Event()

    async def _fake(**kw):
        done.set()
        return "x"

    monkeypatch.setattr(nd, "dispatch_user_notification", _fake)
    nd.register_dispatch_loop(running_loop.loop)

    nd.dispatch_user_notification_threadsafe(**_kwargs())   # return value dropped
    assert done.wait(timeout=5), "the queued coroutine never ran"


def test_it_does_not_block_the_calling_thread(running_loop, monkeypatch) -> None:
    """A slow notification must not hold up the execution path that produced it."""
    async def _slow(**kw):
        await asyncio.sleep(2.0)
        return "late"

    monkeypatch.setattr(nd, "dispatch_user_notification", _slow)
    nd.register_dispatch_loop(running_loop.loop)

    start = time.monotonic()
    fut = nd.dispatch_user_notification_threadsafe(**_kwargs())
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, f"queueing blocked the caller for {elapsed:.2f}s"
    assert fut is not None
    fut.cancel()


# ───────── failures are reported, not swallowed ─────────


def test_a_raising_coroutine_is_reported_and_not_propagated(running_loop, monkeypatch) -> None:
    """The defect that deleted the predecessor: an unread Future hides the failure.

    The exception must reach `_log_dispatch_failure` (so an operator can see a
    notification was lost) and must NOT reach the caller (so a fill cannot fail
    because an alert could not be stored).
    """
    reported: List[tuple] = []

    async def _boom(**kw):
        raise RuntimeError("supabase unreachable")

    monkeypatch.setattr(nd, "dispatch_user_notification", _boom)
    monkeypatch.setattr(
        nd, "_log_dispatch_failure",
        lambda uid, et, sev, exc: reported.append((uid, et, sev, type(exc).__name__)),
    )
    nd.register_dispatch_loop(running_loop.loop)

    # Must not raise here, which is the caller-facing guarantee.
    fut = nd.dispatch_user_notification_threadsafe(**_kwargs(severity="warning"))
    assert fut is not None

    for _ in range(200):
        if reported:
            break
        time.sleep(0.01)

    assert reported, (
        "a failed notification was not reported. run_coroutine_threadsafe parks the "
        "exception on the Future; unread, the failure is silent — which is exactly why "
        "dispatch_user_notification_sync was deleted"
    )
    uid, et, sev, exc_name = reported[0]
    assert et == "trade_executed"
    assert sev == "warning"
    assert exc_name == "RuntimeError"


def test_a_loop_that_refuses_the_coroutine_is_reported_not_raised(monkeypatch) -> None:
    """`run_coroutine_threadsafe` itself can raise; that must not reach the caller."""
    reported: List[tuple] = []

    class _Hostile:
        def is_closed(self):
            return False

    monkeypatch.setattr(
        nd, "_log_dispatch_failure",
        lambda uid, et, sev, exc: reported.append((et, type(exc).__name__)),
    )
    nd.register_dispatch_loop(_Hostile())

    out = nd.dispatch_user_notification_threadsafe(**_kwargs())
    assert out is None
    assert reported, "a refused queue attempt was neither returned nor reported"


# ───────── the source-level guarantee ─────────


def test_it_uses_run_coroutine_threadsafe_and_holds_the_future() -> None:
    """The shape the deletion note prescribed, asserted so a refactor cannot regress it.

    `create_task` fire-and-forget is the specific thing that made the predecessor
    unsafe: the task was garbage-collectable before it ran.
    """
    import inspect as _inspect

    src = _inspect.getsource(nd.dispatch_user_notification_threadsafe)
    assert "run_coroutine_threadsafe" in src
    assert "add_done_callback" in src, (
        "the Future's exception is not read, so a failed notification is silent"
    )
    assert "create_task" not in src, (
        "create_task fire-and-forget is what made dispatch_user_notification_sync "
        "unsafe; the task was collectable before it ran"
    )


def test_main_registers_the_loop_in_the_lifespan() -> None:
    """Without this call the function above can never have a loop in production."""
    from pathlib import Path

    main_src = (Path(__file__).resolve().parents[1] / "backend_app" / "main.py").read_text(
        encoding="utf-8"
    )
    assert "register_dispatch_loop" in main_src, (
        "main.py no longer registers the dispatch loop, so every synchronous producer "
        "silently queues nothing"
    )
    assert "get_running_loop" in main_src
