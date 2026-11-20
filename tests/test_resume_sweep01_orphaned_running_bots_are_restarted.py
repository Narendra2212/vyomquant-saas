"""RESUME-SWEEP-01: a bot left without an owner by a rolling deploy is restarted.

Found in production on the 2026-11-14 deploy: the new ECS task booted while the old one
still ran paper deployment 52ced23f, so the boot-time resume correctly skipped it ("another
process owns it"). The old task was then stopped -- claim released, row still ``running`` --
and nothing looked again: the row said running, nothing ran it, no claim existed. With live
resume on, every deploy would orphan every live bot the same way.

``FleetManager.resume_orphaned_deployments`` runs every 60 s in each process and restarts
only ``running`` rows that are old enough, not in this process's fleet, and have NO live
claim anywhere.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend_app.backend.fleet_manager import FleetManager

REPO = Path(__file__).resolve().parents[1]


class _Store:
    def __init__(self, rows):
        self.rows, self.failed, self.asked, self.stopped = rows, [], [], []

    async def rows_in(self, statuses):
        self.asked.append(statuses)
        return [r for r in self.rows if r["status"] in statuses]

    async def mark_stopped(self, dep_id):
        self.stopped.append(dep_id)
        return True

    async def mark_failed(self, dep_id, reason):
        self.failed.append((dep_id, reason))
        return True


def _old():
    return (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()


def _row(id_="d1", user="u1", symbol="BTC/USDT", status="running", updated=None):
    # Timestamps are taken when the test RUNS, not at import: a long suite would otherwise
    # make a "fresh" row look minutes old.
    updated = updated or _old()
    return {"id": id_, "user_id": user, "exchange_symbol": symbol, "status": status,
            "updated_at": updated}


def _run(rows, *, claim, local=(), rebuild=None):
    fleet = FleetManager.__new__(FleetManager)
    fleet._active_fleet = {k: {} for k in local}
    store = _Store(rows)
    fleet._store = lambda: store
    started = []

    async def _start(user, symbol, blueprint, *, paper_trading):
        started.append((user, symbol, paper_trading))
        return True, "ok"

    fleet.start_bot = _start
    rebuild = rebuild or (lambda row: ({"symbol": row["exchange_symbol"]}, True))
    with patch("backend_app.core.bot_ownership.claim_is_live", lambda *_a: claim):
        outcome = asyncio.run(fleet.resume_orphaned_deployments(rebuild))
    return outcome, started, store


def test_a_running_row_with_no_live_claim_is_restarted():
    outcome, started, _ = _run([_row()], claim=False)
    assert started == [("u1", "BTC/USDT", True)]
    assert outcome["started"] == 1


def test_a_bot_owned_elsewhere_is_left_alone():
    outcome, started, store = _run([_row()], claim=True)
    assert started == [] and store.failed == [] and outcome["skipped"] == 1


def test_an_unreadable_claim_store_is_left_alone():
    _outcome, started, store = _run([_row()], claim=None)
    assert started == [] and store.failed == []


def test_a_bot_in_this_process_is_left_alone():
    _outcome, started, _ = _run([_row()], claim=False, local=("u1_BTC_USDT",))
    assert started == []


def test_a_freshly_written_row_waits_for_the_next_pass():
    """The deploy path writes the row before start_bot takes the claim."""
    _outcome, started, _ = _run([_row(updated=datetime.now(timezone.utc).isoformat())], claim=False)
    assert started == []


def test_only_running_rows_are_considered():
    _outcome, started, store = _run([_row(status="deploying")], claim=False)
    assert started == [] and ("running",) in store.asked
    assert not any("deploying" in a for a in store.asked)


def test_a_row_that_cannot_be_rebuilt_is_marked_failed():
    def _boom(_row):
        raise RuntimeError("version deleted")

    outcome, started, store = _run([_row()], claim=False, rebuild=_boom)
    assert started == [] and outcome["failed"] == 1
    assert store.failed and "version deleted" in store.failed[0][1]


def test_the_sweep_is_started_at_boot_and_stopped_before_the_fleet_shuts_down():
    src = (REPO / "backend_app" / "main.py").read_text()
    assert "sweep_orphaned_deployments_forever(app_state.fleet)" in src
    shutdown = src.split("Graceful shutdown", 1)[1]
    assert shutdown.index("_orphan_sweep_task.cancel()") < shutdown.index("fleet.shutdown_all()")


def test_the_loop_survives_a_failed_pass():
    from backend_app.backend import deployment_runtime as rt

    calls = []

    class _Fleet:
        async def resume_orphaned_deployments(self, _rebuild):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("db blip")
            raise asyncio.CancelledError

    async def scenario():
        await rt.sweep_orphaned_deployments_forever(_Fleet(), client_factory=lambda: None,
                                                    interval=0)

    try:
        asyncio.run(scenario())
    except asyncio.CancelledError:
        pass
    assert len(calls) == 2


# ── STOPPING-SWEEP-01: a stop nothing will ever confirm ─────────────────────────────────


def test_a_stopping_row_with_no_runtime_is_finalised():
    outcome, started, store = _run([_row(status="stopping")], claim=False)
    assert store.stopped == ["d1"] and started == [] and outcome["finalised_stop"] == 1


def test_a_stopping_row_in_this_process_is_left_to_its_heartbeat():
    _o, _s, store = _run([_row(status="stopping")], claim=False, local=("u1_BTC_USDT",))
    assert store.stopped == []


def test_a_fresh_stopping_row_waits_for_the_owner():
    _o, _s, store = _run([_row(status="stopping", updated=datetime.now(timezone.utc).isoformat())],
                         claim=False)
    assert store.stopped == []


def test_a_stopping_row_behind_a_live_claim_waits_then_is_finalised():
    five = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _o, _s, store = _run([_row(status="stopping", updated=five)], claim=True)
    assert store.stopped == []
    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    _o, _s, store = _run([_row(status="stopping", updated=old)], claim=True)
    assert store.stopped == ["d1"]


def test_an_unreadable_claim_never_finalises_a_stop():
    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    _o, _s, store = _run([_row(status="stopping", updated=old)], claim=None)
    assert store.stopped == []
