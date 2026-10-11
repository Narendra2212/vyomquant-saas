"""Falsification harness for the two WORK-03 completion fixes.

A test that passes against the fixed code proves nothing unless it also FAILS against
the broken code. This drives the same two scenarios with the fix neutralised and
asserts the oracle notices.

  Case A: `_boot_and_run` does not release  -> the claim row must SURVIVE (the leak).
  Case B: no heartbeat loop runs            -> the claim must become TAKEABLE.

Run: .venv\\Scripts\\python.exe staging\\falsify_work03_fixes.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

DSN = "postgresql://postgres:@127.0.0.1:62674/postgres"
os.environ["AERORA_TEST_DATABASE_URL"] = DSN
os.environ["DATABASE_URL"] = DSN

import psycopg2  # noqa: E402

from backend_app.core import bot_ownership as bo  # noqa: E402
from backend_app.backend import fleet_manager as fm  # noqa: E402


def conn():
    c = psycopg2.connect(DSN)
    c.autocommit = True
    return c


def rows(user_id):
    cur = conn().cursor()
    cur.execute(
        "SELECT symbol, owner_token::text FROM public.bot_ownership_claims "
        "WHERE user_id = %s", (user_id,),
    )
    return cur.fetchall()


def cleanup(user_id):
    cur = conn().cursor()
    cur.execute("DELETE FROM public.bot_ownership_claims WHERE user_id = %s", (user_id,))


class RecordingTask:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def done(self):
        return False


results = []


# ── Case A: neutralise the boot-failure release ─────────────────────────────
def case_a():
    user_id, symbol = str(uuid.uuid4()), f"BTC/F{uuid.uuid4().hex[:6]}"
    claim = bo.acquire(conn().cursor(), user_id, symbol, "pod-a")
    assert claim.granted, "setup failed: claim not granted"
    assert len(rows(user_id)) == 1

    original = bo.release_bot
    bo.release_bot = lambda c: False  # the pre-fix behaviour: nothing is released
    try:
        class BootFailingBot:
            async def _recover_live_state(self):
                raise RuntimeError("simulated boot failure")

        fleet = fm.FleetManager()
        key = fleet._bot_key(user_id, symbol)
        fleet._active_fleet[key] = {
            "task": RecordingTask(), "claim": claim, "symbol": symbol,
        }
        asyncio.run(fleet._boot_and_run(BootFailingBot(), key))
        leaked = rows(user_id)
    finally:
        bo.release_bot = original
        cleanup(user_id)

    ok = len(leaked) == 1
    results.append((
        "A: boot-failure leak is detectable",
        ok,
        f"claim rows after an unreleased boot failure = {len(leaked)} (expected 1)",
    ))


# ── Case B: neutralise the heartbeat loop ───────────────────────────────────
def case_b():
    user_id, symbol = str(uuid.uuid4()), f"BTC/F{uuid.uuid4().hex[:6]}"
    claim = bo.acquire(conn().cursor(), user_id, symbol, "pod-a")
    assert claim.granted, "setup failed: claim not granted"

    # No heartbeat loop at all. Let the claim age exactly as it would in production.
    cur = conn().cursor()
    cur.execute(
        "UPDATE public.bot_ownership_claims "
        "   SET heartbeat_at = now() - make_interval(secs => %s) "
        " WHERE user_id = %s", (bo.effective_stale_seconds() + 30, user_id),
    )

    rival = bo.acquire(conn().cursor(), user_id, symbol, "pod-b")
    cleanup(user_id)

    ok = rival.granted and rival.owner_token != claim.owner_token
    results.append((
        "B: unheartbeated claim is takeable (the gap)",
        ok,
        f"rival outcome = {rival.outcome}, granted = {rival.granted}",
    ))


# ── Case C: with the heartbeat loop, the same claim is NOT takeable ─────────
def case_c():
    user_id, symbol = str(uuid.uuid4()), f"BTC/F{uuid.uuid4().hex[:6]}"
    claim = bo.acquire(conn().cursor(), user_id, symbol, "pod-a")
    assert claim.granted, "setup failed: claim not granted"

    cur = conn().cursor()
    cur.execute(
        "UPDATE public.bot_ownership_claims "
        "   SET heartbeat_at = now() - make_interval(secs => %s) "
        " WHERE user_id = %s", (bo.effective_stale_seconds() + 30, user_id),
    )

    interval = bo.HEARTBEAT_INTERVAL_SECONDS
    bo.HEARTBEAT_INTERVAL_SECONDS = 1
    try:
        fleet = fm.FleetManager()
        key = fleet._bot_key(user_id, symbol)
        task = RecordingTask()
        fleet._active_fleet[key] = {"task": task, "claim": claim, "symbol": symbol}

        async def drive():
            try:
                await asyncio.wait_for(fleet._heartbeat_claim(key, claim), timeout=3.0)
            except asyncio.TimeoutError:
                pass

        asyncio.run(drive())
        rival = bo.acquire(conn().cursor(), user_id, symbol, "pod-b")
        cancelled = task.cancelled
    finally:
        bo.HEARTBEAT_INTERVAL_SECONDS = interval
        cleanup(user_id)

    ok = (not rival.granted) and (not cancelled)
    results.append((
        "C: heartbeated claim is NOT takeable (the fix)",
        ok,
        f"rival outcome = {rival.outcome}, owner bot cancelled = {cancelled}",
    ))


for fn in (case_a, case_b, case_c):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        results.append((fn.__name__, False, f"harness error: {exc!r}"))

print("FALSIFICATION RESULTS")
print("=" * 72)
failed = 0
for name, ok, detail in results:
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}")
    print(f"         {detail}")
    if not ok:
        failed += 1
print("=" * 72)
print(f"VERDICT={'ALL_CONTROLS_BEHAVED' if failed == 0 else f'{failed}_CONTROL_FAILURES'}")
