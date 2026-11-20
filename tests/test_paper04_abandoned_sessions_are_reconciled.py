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


# ══════════════════════════════════════════════════════════════════════════════
#  7. THE REASON IS WRITTEN TO THE ROW, NOT ONLY TO A LOG LINE
# ══════════════════════════════════════════════════════════════════════════════
#  Migration 054 added `abandoned_reason` and its COMMENT says it is "set by the
#  boot-time reconciliation pass". Nothing set it: the sentence was built and handed
#  to logger.warning, and `transition_session_state` had no parameter that could
#  carry it. The column was inert and an ABANDONED row could not say WHY -- which is
#  the one question 054 created it to answer.


def test_the_abandon_writes_a_reason_to_the_row():
    fake = _FakeRepo([_row(last_beat="2026-01-01T00:00:00+00:00")])
    _run(fake)
    assert fake.transitions, "nothing was transitioned"
    written = fake.transitions[0]
    assert "abandoned_reason" in written, (
        "the reason is not passed to the row, so paper_sessions.abandoned_reason stays "
        "NULL and migration 054's column is inert"
    )
    reason = written["abandoned_reason"]
    # It has to be useful to somebody reading the row months later.
    assert "heartbeat" in reason.lower()
    assert "2026-01-01T00:00:00+00:00" in reason, "the last beat is not named"


def test_a_session_that_never_beat_still_gets_a_readable_reason():
    fake = _FakeRepo([_row(last_beat=None)])
    _run(fake)
    reason = fake.transitions[0]["abandoned_reason"]
    assert "ever" in reason.lower(), (
        "a session with no heartbeat at all must say so rather than render None"
    )
    assert "None" not in reason


def test_the_repository_can_actually_write_the_column():
    """The service passing a reason is useless if the statement layer drops it. Pins the
    parameter on the one write path to `session_state`."""
    import inspect

    sig = inspect.signature(repo.transition_session_state)
    assert "abandoned_reason" in sig.parameters, (
        "transition_session_state cannot carry a reason, so nothing can set the column"
    )
    source = inspect.getsource(repo.transition_session_state)
    assert 'values["abandoned_reason"]' in source, (
        "the parameter exists but is never put in the UPDATE payload"
    )
    # Written only when named, like the three lifecycle timestamps beside it: an
    # unrelated transition must not blank a reason the row already carries.
    assert "if abandoned_reason is not None:" in source


# ══════════════════════════════════════════════════════════════════════════════
#  8. RECONCILIATION KEEPS RUNNING, RATHER THAN ONLY AT BOOT
# ══════════════════════════════════════════════════════════════════════════════
#  The boot pass covered the redeploy case by accident (a redeploy produces a boot).
#  It did not cover a death that produces NO boot -- an escaped exception, a feed that
#  never reconnects, a worker that outlives its task. Those orphans sat at RUNNING
#  indefinitely, which is the condition PAPER-04 describes.


def test_the_sweep_interval_is_shorter_than_the_abandon_window():
    """Or an orphan waits more than one window to be noticed, and the timer adds
    nothing over the boot pass it joins."""
    assert svc.RECONCILE_INTERVAL_SECONDS < svc.ABANDON_AFTER_SECONDS
    # Detection is bounded by window + interval. Keep that inside ~20 minutes.
    assert svc.ABANDON_AFTER_SECONDS + svc.RECONCILE_INTERVAL_SECONDS <= 1200


def test_the_periodic_loop_sweeps_repeatedly_and_survives_a_failure():
    """Three cycles: a good one, one whose client cannot be resolved, a good one. The
    loop must not die on the middle one -- a sweep that stops at the first failure is
    the boot-only behaviour again, with extra steps."""
    calls = []

    async def _drive():
        sweeps = {"n": 0}

        async def _fake_reconcile(client, **kwargs):
            sweeps["n"] += 1
            calls.append(client)
            return {"examined": 0, "abandoned": 0, "raced": 0, "errors": 0}

        clients = [object(), None, object()]

        def _get_client():
            if not clients:
                raise asyncio.CancelledError
            return clients.pop(0)

        original = svc.reconcile_orphaned_sessions
        svc.reconcile_orphaned_sessions = _fake_reconcile
        try:
            task = asyncio.create_task(
                svc.run_reconciliation_loop(_get_client, interval=0)
            )
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            svc.reconcile_orphaned_sessions = original
        return sweeps["n"]

    swept = asyncio.run(_drive())
    assert swept >= 2, (
        f"the loop swept {swept} time(s); a None client in the middle must be skipped "
        f"without ending the loop"
    )


def test_the_periodic_loop_does_not_swallow_cancellation():
    """Cancelled on shutdown like every other background loop. A loop that caught
    CancelledError would hang the shutdown it was asked to leave."""
    import inspect

    source = inspect.getsource(svc.run_reconciliation_loop)
    assert "except asyncio.CancelledError:" in source
    assert "raise" in source.split("except asyncio.CancelledError:")[1][:80]


def test_the_lifespan_starts_the_periodic_sweep_and_cancels_it():
    main_py = pathlib.Path(__file__).resolve().parents[1] / "backend_app" / "main.py"
    source = main_py.read_text(encoding="utf-8")
    assert "run_reconciliation_loop" in source, "the timer is never started"
    assert "_paper_reconcile_task = asyncio.create_task(" in source, (
        "spawned is required, not awaited -- this loop never finishes"
    )
    assert "_paper_reconcile_task.cancel()" in source, (
        "a background task nothing cancels delays every shutdown by its interval"
    )


def test_the_sweep_needs_no_leader_election_and_says_why():
    """Deliberately runs in every worker. Pinned because 'remove the duplication' is
    the obvious-looking change that would turn redundancy into a single point of
    failure -- and because adding a Redis lease in front of an already-idempotent
    property is what reconcile_orphaned_sessions' own docstring argues against."""
    import inspect

    source = inspect.getsource(svc.run_reconciliation_loop)
    assert "leader" in source.lower(), "the decision is undocumented"
    assert "leader_lease" not in source.replace("core.leader_lease", ""), (
        "a lease was introduced; if that is deliberate, this test should be the thing "
        "that is updated, with the reason"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  9. THE SHUTDOWN DRAIN: THE HOSTING HALF
# ══════════════════════════════════════════════════════════════════════════════
#  At shutdown this process knows the one thing no sweep can know: exactly which
#  sessions it was hosting, and that their loops are ending now. Recording that
#  directly turns a redeploy from "stops silently, discovered up to 1200s later" into
#  "marked at the moment it happens".


def test_the_drain_abandons_every_session_this_process_hosts():
    fake = _FakeRepo([])
    import backend_app.backend.paper.paper_session_service as module

    original = module.repo
    module.repo = fake
    try:
        summary = asyncio.run(svc.abandon_sessions_hosted_here(
            object(), [("s1", "u1"), ("s2", "u2")], reason="host shutting down",
        ))
    finally:
        module.repo = original

    assert summary == {"examined": 2, "abandoned": 2, "raced": 0, "errors": 0}
    assert [t["session_id"] for t in fake.transitions] == ["s1", "s2"]
    for t in fake.transitions:
        assert t["from_state"] == svc.STATE_RUNNING, (
            "the drain must be guarded on RUNNING, or it would overwrite a state the "
            "user set a moment earlier"
        )
        assert t["to_state"] == svc.STATE_ABANDONED
        assert t["abandoned_reason"] == "host shutting down"


def test_the_drain_scopes_each_write_to_the_sessions_own_user():
    fake = _FakeRepo([])
    import backend_app.backend.paper.paper_session_service as module

    original = module.repo
    module.repo = fake
    try:
        asyncio.run(svc.abandon_sessions_hosted_here(
            object(), [("s1", "u1"), ("s2", "u2")], reason="r",
        ))
    finally:
        module.repo = original
    assert {(t["session_id"], t["user_id"]) for t in fake.transitions} == {
        ("s1", "u1"), ("s2", "u2")
    }


def test_the_drain_treats_a_lost_race_as_expected_not_as_an_error():
    """The user stopped it a moment ago, or the sweep got there first. Either way the
    guarded UPDATE matches nothing and that is the correct outcome, not a failure."""
    # A local stand-in, exactly as the reconciliation tests above use: the pass
    # classifies by exception CLASS NAME, so what matters is the name, not the identity.
    class PaperConcurrencyConflict(Exception):
        pass

    fake = _FakeRepo([], fail_with=PaperConcurrencyConflict("no row matched"))
    import backend_app.backend.paper.paper_session_service as module

    original = module.repo
    module.repo = fake
    try:
        summary = asyncio.run(svc.abandon_sessions_hosted_here(
            object(), [("s1", "u1")], reason="r",
        ))
    finally:
        module.repo = original
    assert summary["raced"] == 1
    assert summary["errors"] == 0
    assert summary["abandoned"] == 0


def test_the_drain_never_raises_so_a_shutdown_cannot_fail_on_it():
    fake = _FakeRepo([], fail_with=RuntimeError("database is on fire"))
    import backend_app.backend.paper.paper_session_service as module

    original = module.repo
    module.repo = fake
    try:
        summary = asyncio.run(svc.abandon_sessions_hosted_here(
            object(), [("s1", "u1")], reason="r",
        ))
    finally:
        module.repo = original
    assert summary["errors"] == 1


def test_the_drain_is_a_no_op_when_this_process_hosts_nothing():
    """The common case for a worker that served no start. Must not touch the database
    at all -- a drain that swept every RUNNING row would abandon other workers' live
    sessions, which is the hazard the heartbeat design exists to avoid."""
    fake = _FakeRepo([])
    import backend_app.backend.paper.paper_session_service as module

    original = module.repo
    module.repo = fake
    try:
        summary = asyncio.run(
            svc.abandon_sessions_hosted_here(object(), [], reason="r")
        )
    finally:
        module.repo = original
    assert summary == {"examined": 0, "abandoned": 0, "raced": 0, "errors": 0}
    assert fake.transitions == []


def test_the_drain_population_is_this_process_registry_not_a_database_query():
    """THE decisive property. `gunicorn --workers 2` means "a RUNNING row" and "a row
    this process runs" are different sets, and draining the first abandons another
    worker's live sessions."""
    import inspect

    from backend_app.routers import paper_trading as rt

    source = inspect.getsource(rt.sessions_hosted_here)
    assert "_SESSION_RUNTIMES" in source, (
        "the drain population must come from the process-local registry"
    )

    # EXECUTABLE STATEMENTS ONLY, via ast. The docstring explains the RUNNING-row hazard
    # at length and necessarily uses the word, so a substring search over the whole
    # function reads the explanation as the offence -- the same trap migration 056's
    # tests had to avoid, and the first version of this test fell into it.
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(source))
    fn = tree.body[0]
    statements = [n for n in fn.body if not (
        isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, str)
    )]
    body = "\n".join(ast.dump(n) for n in statements)
    for forbidden in ("list_stale_running_sessions", "session_state", "RUNNING"):
        assert forbidden not in body, (
            f"sessions_hosted_here executes {forbidden!r}; it must not consult the "
            f"database for which sessions to drain"
        )


def test_the_registry_accessor_reports_session_and_owner():
    from backend_app.routers import paper_trading as rt

    rt._SESSION_RUNTIMES.clear()
    try:
        rt._remember_runtime("sess-1", "user-1", feed=object(), loop_task=None)
        rt._remember_runtime("sess-2", "user-2", feed=object(), loop_task=None)
        assert set(rt.sessions_hosted_here()) == {
            ("sess-1", "user-1"), ("sess-2", "user-2")
        }
    finally:
        rt._SESSION_RUNTIMES.clear()


def test_the_accessor_snapshots_rather_than_views_the_registry():
    """The caller transitions each entry while request handlers mutate the dict. A view
    would raise RuntimeError: dictionary changed size during iteration."""
    from backend_app.routers import paper_trading as rt

    rt._SESSION_RUNTIMES.clear()
    try:
        rt._remember_runtime("sess-1", "user-1", feed=object(), loop_task=None)
        hosted = rt.sessions_hosted_here()
        rt._SESSION_RUNTIMES.clear()
        assert hosted == [("sess-1", "user-1")], "the result tracked the live dict"
    finally:
        rt._SESSION_RUNTIMES.clear()


def test_the_lifespan_drains_before_it_stops_the_engines():
    """While the database handle still works, and before anything else is torn down."""
    main_py = pathlib.Path(__file__).resolve().parents[1] / "backend_app" / "main.py"
    source = main_py.read_text(encoding="utf-8")
    assert "drain_sessions_hosted_here" in source, "the drain is never called"
    drain_at = source.index("drain_sessions_hosted_here")
    assert drain_at > source.index("yield  #"), "the drain must be on the shutdown side"
    assert drain_at < source.index("_consistency_checker is not None"), (
        "the drain must run before the engines are stopped"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  10. AN UNAPPLIED MIGRATION MUST NOT BE SILENT AGAIN
# ══════════════════════════════════════════════════════════════════════════════
#  Why this whole mechanism was decorative in production: 054 shipped and was never
#  applied, so the heartbeat UPDATE failed with UndefinedColumn on every iteration of
#  every session -- into a try/except that logged at DEBUG. Measured against
#  production 2026-10-24: the write, the stale read and the five-state CHECK were all
#  refused, and nothing above debug ever said so.


class _MissingColumn:
    """A client whose UPDATE fails the way an unapplied 054 makes it fail."""

    def table(self, *_a, **_k):
        raise RuntimeError(
            'column "last_heartbeat_at" of relation "paper_sessions" does not exist'
        )


def _beat_against(monkeypatch, client, times=1):
    """Drive ``touch_session_heartbeat`` so the UPDATE is actually REACHED.

    ``require_persistence`` runs first and raises ``PAPER_PERSISTENCE_UNAVAILABLE`` in the
    unit lane, where ``paper_accounts`` does not exist -- so without stubbing it the fake
    client is never consulted and these tests would be asserting about the wrong
    exception. That message carries no schema marker, so it classifies as transient, which
    is correct (persistence being unavailable is a separate, already-reported condition)
    but is not what is under test here.
    """
    monkeypatch.setattr(repo, "require_persistence", lambda *_a, **_k: None)
    monkeypatch.setattr(repo, "_HEARTBEAT_SCHEMA_REPORTED", False, raising=False)
    for _ in range(times):
        repo.touch_session_heartbeat(client, user_id="u1", session_id="s1")


def test_a_schema_error_in_the_heartbeat_is_reported_at_error_level(caplog, monkeypatch):
    with caplog.at_level("DEBUG"):
        _beat_against(monkeypatch, _MissingColumn())
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errors, (
        "a missing column was logged below ERROR; that is exactly how an unapplied 054 "
        "stayed invisible in production"
    )
    message = errors[0].getMessage()
    assert "054" in message, "the message must name the migration to apply"
    assert "last_heartbeat_at" in message


def test_the_schema_error_is_reported_once_per_process_not_once_per_beat(
    caplog, monkeypatch
):
    """The loop beats once per iteration per session. Reporting every time would be
    thousands of identical lines, which reads as noise and gets filtered."""
    with caplog.at_level("DEBUG"):
        _beat_against(monkeypatch, _MissingColumn(), times=5)
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1, f"reported {len(errors)} times, expected once"


def test_a_transient_failure_stays_at_debug_and_does_not_cry_wolf(caplog):
    """A timeout is a missed beat, self-correcting on the next iteration. Reporting it
    as a deployment defect would train operators to ignore the real alarm."""
    class _Flaky:
        def table(self, *_a, **_k):
            raise TimeoutError("read timed out")

    repo._HEARTBEAT_SCHEMA_REPORTED = False
    try:
        with caplog.at_level("DEBUG"):
            repo.touch_session_heartbeat(_Flaky(), user_id="u1", session_id="s1")
        assert not [r for r in caplog.records if r.levelname == "ERROR"]
        assert [r for r in caplog.records if r.levelname == "DEBUG"]
    finally:
        repo._HEARTBEAT_SCHEMA_REPORTED = False


def test_the_schema_classifier_separates_the_two_cases():
    assert repo._looks_like_missing_schema(
        RuntimeError('column "x" does not exist')) is True
    assert repo._looks_like_missing_schema(RuntimeError("42703")) is True
    # A CHECK violation is how an unapplied 054 rejects the ABANDONED *value* rather
    # than the column, so it belongs on the same side.
    assert repo._looks_like_missing_schema(RuntimeError("23514 check constraint")) is True
    assert repo._looks_like_missing_schema(TimeoutError("read timed out")) is False
    assert repo._looks_like_missing_schema(ConnectionError("reset by peer")) is False


def test_the_heartbeat_still_never_raises_whatever_happens():
    class _Hostile:
        def table(self, *_a, **_k):
            raise RuntimeError('column "last_heartbeat_at" does not exist')

    repo._HEARTBEAT_SCHEMA_REPORTED = False
    try:
        repo.touch_session_heartbeat(_Hostile(), user_id="u1", session_id="s1")
    finally:
        repo._HEARTBEAT_SCHEMA_REPORTED = False


def test_054_is_inventoried_so_an_unapplied_migration_is_detectable():
    """The deeper fix: DEPLOY-05's evidence probe now covers 054's objects, so
    "declared in a merged migration" and "present in the database" stop being the same
    claim. Without this entry nothing would ever have asked."""
    from scripts.check_production_schema_drift import DECLARED_OBJECTS

    declared = [o for o in DECLARED_OBJECTS
                if o[2] == "054_paper04_abandoned_session_state.sql"]
    assert declared, "054 is not in the inventory, so its application is unverifiable"
    identifiers = {o[1] for o in declared}
    assert "paper_sessions.last_heartbeat_at" in identifiers
    assert "paper_sessions.abandoned_reason" in identifiers


# ══════════════════════════════════════════════════════════════════════════════
#  11. THE REQUIREMENT NOW NAMES THE FIFTH STATE
# ══════════════════════════════════════════════════════════════════════════════
#  Migration 054 said: "Requirement 17.7 should be updated to five; this file is the
#  evidence for that edit rather than a silent departure from it." That edit was
#  outstanding -- the spec still named four, so the database and the requirement
#  disagreed and the migration was the only record of which was right.


def _requirement_17_7() -> str:
    spec = (pathlib.Path(__file__).resolve().parents[1] / ".kiro" / "specs"
            / "marketplace-subscriptions-paper-trading" / "requirements.md")
    text = spec.read_text(encoding="utf-8")
    start = text.index("THE Paper_Session_Service SHALL maintain each Paper_Session")
    return text[start:start + 4000]


def test_the_requirement_names_all_five_states():
    clause = _requirement_17_7()
    for state in ("CREATED", "RUNNING", "PAUSED", "STOPPED", "ABANDONED"):
        assert state in clause, f"Requirement 17.7 does not name {state}"


def test_the_requirement_permits_reset_from_abandoned():
    clause = _requirement_17_7()
    assert "reset from `STOPPED` or `ABANDONED`" in clause, (
        "054 seeds ('ABANDONED','CREATED') but the requirement does not permit it, so "
        "the database and the spec disagree about a legal operation"
    )


def test_the_requirement_records_that_there_is_no_resume():
    clause = _requirement_17_7()
    assert "no `ABANDONED` → `RUNNING` edge" in clause


def test_the_requirement_gives_the_real_reason_resume_is_refused():
    """The reason matters more than the refusal. 054 said the in-flight state "was never
    persisted", and that is NOT true: orders, positions, balances, the equity series and
    the bar history are each written per bar, and the bar cursor is recoverable from
    paper_market_events. The actual obstacle is the bars that closed while nothing was
    running -- Requirement 17.9 fixes the per-bar pipeline, so a session that skipped
    bars never evaluated the signals they would have produced. Keeping the wrong reason
    on the record invites someone to "fix" persistence and then enable a resume that is
    still unsound."""
    clause = _requirement_17_7()
    assert "persisted" in clause, "the clause does not address durability at all"
    assert "17.9" in clause, "the real obstacle -- the skipped bars -- is not named"
    low = clause.lower()
    assert "discontinuous" in low or "skipped bars" in low
