"""PAPER-04 -- a session whose loop died now says so, and a live one is never touched.

THE DEFECT
    "The loop still runs as an asyncio.Task inside the web process, so a worker
    restart/redeploy still ends every in-flight loop with no attempt to resume it, and
    there is no terminal state to mark an orphaned RUNNING session with (migration 009
    chk_paper_session_state admits only CREATED/RUNNING/PAUSED/STOPPED)."

    The row therefore sat at RUNNING forever: counted against Requirement 27.4's
    concurrency cap, read as live by every client, and no figure it reported would ever
    advance again.

WHAT IS FIXED HERE, AND WHAT IS NOT
    Fixed: the state exists (ABANDONED), the loop declares liveness, and a boot-time pass
    marks orphans. The death becomes VISIBLE and the row's state becomes TRUE.
    NOT fixed, and not claimed: the loops still run in the web process. That half needs
    the worker service INF-03 covers.

THE HAZARD THIS FILE MOSTLY EXISTS TO PIN
    A boot sweep that marked every RUNNING row ABANDONED would be WORSE than the defect.
    `startup.sh` runs `gunicorn --workers ${WORKERS:-2}`, so if worker B restarts while
    worker A is healthily running sessions, B would abandon A's LIVE sessions and report
    real in-flight paper trading as a crash. "Is this row's loop alive?" cannot be
    answered from the row's state, and cannot be answered by asking the local process
    either, because the owner may be a different one.

    So the population is HEARTBEAT-STALE rows, never RUNNING rows, and the write is a
    guarded UPDATE. Those two facts are what make the pass safe to run from every worker
    and safe to repeat, with no leader election.

SCOPE
    Fakes only: no database, no Supabase, no event loop beyond asyncio.run. The SQL half
    (migration 054's constraint, the two allowed-transition pairs) is asserted by reading
    the migration, because a unit test cannot apply it.
"""
from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_session_service as svc

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "backend_app" / "migrations" / "054_paper04_abandoned_session_state.sql"
)
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class _FakeRepo:
    """Stands in for the two repository functions the pass uses."""

    def __init__(self, stale_rows, fail_with=None):
        self._stale = list(stale_rows)
        self._fail_with = fail_with
        self.transitions = []
        self.stale_before = None

    def list_stale_running_sessions(self, supabase, *, stale_before):
        self.stale_before = stale_before
        return list(self._stale)

    def transition_session_state(self, supabase, **kwargs):
        self.transitions.append(kwargs)
        if self._fail_with is not None:
            raise self._fail_with
        return {"id": kwargs.get("session_id")}


def _row(session_id="s1", user_id="u1", last_beat=None):
    return {
        "id": session_id,
        "user_id": user_id,
        "session_state": "RUNNING",
        "last_heartbeat_at": last_beat,
        "started_at": "2026-01-01T00:00:00+00:00",
        "symbol": "BTC/USDT",
    }


def _run(fake, **kwargs):
    import backend_app.backend.paper.paper_session_service as module

    original = module.repo
    module.repo = fake
    try:
        return asyncio.run(
            svc.reconcile_orphaned_sessions(object(), now=NOW, **kwargs)
        )
    finally:
        module.repo = original


# ══════════════════════════════════════════════════════════════════════════════
#  1. The state exists, across all five coupled places
# ══════════════════════════════════════════════════════════════════════════════


def test_the_column_vocabulary_admits_the_fifth_state():
    assert "ABANDONED" in repo.SESSION_STATES


def test_the_edge_table_can_reach_it_from_running():
    assert svc.STATE_ABANDONED in svc.PAPER_SESSION_TRANSITIONS[svc.STATE_RUNNING]


def test_it_is_terminal_except_for_reset():
    assert svc.PAPER_SESSION_TRANSITIONS[svc.STATE_ABANDONED] == (svc.STATE_CREATED,)


def test_there_is_no_resume_from_abandoned():
    """The loop's in-flight position died with the process and was never persisted, so a
    resume would cross a gap of unknown length while reporting continuity."""
    assert svc.STATE_RUNNING not in svc.PAPER_SESSION_TRANSITIONS[svc.STATE_ABANDONED]


def test_abandon_is_a_system_operation_with_no_user_route():
    """It is in SESSION_OPERATIONS because every edge must have an operation, and out of
    USER_SESSION_OPERATIONS because no user abandons a session."""
    assert svc.OPERATION_ABANDON in svc.SESSION_OPERATIONS
    assert svc.OPERATION_ABANDON not in svc.USER_SESSION_OPERATIONS
    assert svc.USER_SESSION_OPERATIONS == (
        svc.OPERATION_START, svc.OPERATION_PAUSE, svc.OPERATION_RESUME,
        svc.OPERATION_STOP, svc.OPERATION_RESET,
    )


def test_the_migration_extends_both_sql_halves():
    """The CHECK constraint is necessary but NOT sufficient: trg_paper_session_guard
    independently refuses any pair absent from paper_session_allowed_transitions, so
    without the seed the trigger would reject the transition after Python accepted it."""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "'CREATED', 'RUNNING', 'PAUSED', 'STOPPED', 'ABANDONED'" in sql
    assert "paper_session_allowed_transitions" in sql
    assert "'RUNNING',   'ABANDONED'" in sql
    assert "'ABANDONED', 'CREATED'" in sql
    assert "last_heartbeat_at" in sql


# ══════════════════════════════════════════════════════════════════════════════
#  2. A live session in ANOTHER worker is never abandoned
# ══════════════════════════════════════════════════════════════════════════════


def test_the_population_is_heartbeat_stale_and_not_merely_running():
    """THE LOAD-BEARING ONE. The pass must ask the repository for STALE rows.

    If it ever asks for RUNNING rows instead, a restarting worker abandons the sessions
    another worker is healthily running.
    """
    import inspect

    source = inspect.getsource(svc.reconcile_orphaned_sessions)
    assert "list_stale_running_sessions" in source
    assert "stale_before" in source


def test_the_cutoff_is_in_the_past_by_the_configured_window():
    fake = _FakeRepo([])
    _run(fake, abandon_after_seconds=900)

    expected = (NOW - timedelta(seconds=900)).isoformat()
    assert fake.stale_before == expected, (
        "the cutoff must be NOW minus the window; a cutoff at or after now would select "
        "every running session including live ones"
    )


def test_a_live_session_is_not_abandoned_because_nothing_reports_it_stale():
    """The repository decides the population; an empty answer must mean an empty pass."""
    fake = _FakeRepo([])

    summary = _run(fake)

    assert summary["examined"] == 0
    assert summary["abandoned"] == 0
    assert fake.transitions == []


def test_the_write_is_guarded_on_running_so_a_race_cannot_double_write():
    fake = _FakeRepo([_row()])

    _run(fake)

    assert len(fake.transitions) == 1
    assert fake.transitions[0]["from_state"] == svc.STATE_RUNNING, (
        "without from_state=RUNNING the UPDATE is unguarded, and two workers racing the "
        "same orphan would both write"
    )
    assert fake.transitions[0]["to_state"] == svc.STATE_ABANDONED


def test_a_lost_race_is_counted_separately_from_an_error():
    """Two workers racing is EXPECTED, not a fault, and the log must say which happened."""
    class PaperConcurrencyConflict(Exception):
        pass

    fake = _FakeRepo([_row()], fail_with=PaperConcurrencyConflict("no row matched"))

    summary = _run(fake)

    assert summary["raced"] == 1
    assert summary["errors"] == 0
    assert summary["abandoned"] == 0


def test_a_real_error_is_counted_as_one():
    fake = _FakeRepo([_row()], fail_with=RuntimeError("database is on fire"))

    summary = _run(fake)

    assert summary["errors"] == 1
    assert summary["raced"] == 0


# ══════════════════════════════════════════════════════════════════════════════
#  3. The pass itself never breaks a boot
# ══════════════════════════════════════════════════════════════════════════════


def test_an_unreadable_store_returns_a_summary_rather_than_raising():
    class _Broken:
        def list_stale_running_sessions(self, supabase, *, stale_before):
            raise RuntimeError("persistence unavailable")

    summary = _run(_Broken())

    assert summary["errors"] == 1
    assert summary["abandoned"] == 0


def test_no_persistence_at_all_is_a_summary_not_an_exception():
    """The real repository raises PaperError when the migration is not applied."""
    summary = asyncio.run(svc.reconcile_orphaned_sessions(None, now=NOW))

    assert summary["abandoned"] == 0
    assert summary["errors"] == 1


def test_several_orphans_are_each_attempted_even_if_one_fails():
    calls = []

    class _OneBadApple:
        stale_before = None

        def list_stale_running_sessions(self, supabase, *, stale_before):
            return [_row("s1"), _row("s2"), _row("s3")]

        def transition_session_state(self, supabase, **kwargs):
            calls.append(kwargs["session_id"])
            if kwargs["session_id"] == "s2":
                raise RuntimeError("transient")
            return {}

    summary = _run(_OneBadApple())

    assert calls == ["s1", "s2", "s3"], "one failure must not abort the rest of the pass"
    assert summary["abandoned"] == 2
    assert summary["errors"] == 1


# ══════════════════════════════════════════════════════════════════════════════
#  4. The loop declares liveness, and does so BEFORE it blocks
# ══════════════════════════════════════════════════════════════════════════════


def test_the_loop_beats_before_stepping_not_after():
    """Beating after `step_session` would make a healthy heartbeat's age track the BAR
    INTERVAL rather than liveness -- so the staleness window would have to exceed the
    slowest timeframe offered, and a window that wide cannot notice a death promptly."""
    import inspect

    source = inspect.getsource(svc.session_loop)
    beat = source.index("touch_session_heartbeat")
    step = source.index("await step_session")
    assert beat < step, (
        "the heartbeat must be written before the loop blocks on the feed"
    )


def test_a_paused_session_also_beats():
    """A PAUSED loop is alive -- it is sitting in the poll. If it stopped beating, a long
    pause would be indistinguishable from a dead loop."""
    import inspect

    source = inspect.getsource(svc.session_loop)
    paused_block = source.split("STATE_PAUSED")[1][:600]
    assert "touch_session_heartbeat" in paused_block


def test_the_heartbeat_never_raises():
    """A liveness record that could kill the session it reports on is worse than a missed
    beat, which self-corrects on the next iteration."""
    repo.touch_session_heartbeat(None, user_id="u1", session_id="s1")
    repo.touch_session_heartbeat(object(), user_id="", session_id="")


def test_the_window_errs_long_rather_than_short():
    """Too short abandons live sessions and stops real trading; too long leaves an orphan
    visible-as-RUNNING for longer, which is merely slow."""
    assert svc.ABANDON_AFTER_SECONDS >= 300
    assert svc.ABANDON_AFTER_SECONDS <= 3600


# ══════════════════════════════════════════════════════════════════════════════
#  5. It runs at boot
# ══════════════════════════════════════════════════════════════════════════════


def test_the_lifespan_runs_the_pass_and_awaits_it():
    """A startup that reported ACTIVE before reconciling would serve its first requests
    with sessions still lying about their state."""
    main_src = (
        pathlib.Path(__file__).resolve().parents[1] / "backend_app" / "main.py"
    ).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in main_src.splitlines() if not line.lstrip().startswith("#")
    )
    assert "reconcile_orphaned_sessions" in code
    assert "await reconcile_orphaned_sessions" in code, (
        "the pass must be awaited, not spawned: it is bounded, and its whole purpose is "
        "to be true before the first request is served"
    )
    assert "paper_session_reconciliation" in code, (
        "the outcome must reach service_status like every other runtime service"
    )
