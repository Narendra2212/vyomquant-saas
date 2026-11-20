"""``POST .../backtests/start`` answers with a handle, and refuses what ``execute`` refuses.

WHY THE ROUTE EXISTS. The ALB in front of this service has a 60-second idle timeout. A real
backtest over a long window takes longer, so the synchronous route's response was killed in
flight and the browser -- which waits 300 s -- reported a 504 for a run that was still going
and that still persisted its own result. The run was fine; only the answer was lost.

WHAT THIS FILE PINS
-------------------
1. **It answers before the run finishes.** That is the entire point, and it is asserted
   against the run's own completion flag rather than against elapsed time: a 202 arrives
   while the simulation has provably not returned.
2. **The handle is the ``strategy_backtests`` id**, taken from the runtime's
   ``on_record_created`` callback -- not a job id in a second store. The row is created with
   ``status='running'`` before the simulation and moved to ``completed``/``failed`` by the
   same ``BacktestService``, so the row IS the job record and
   ``GET /api/backtests/{backtest_id}`` is the poll.
3. **A refusal is a refusal, not an accepted job.** Every check that precedes persistence --
   ownership, the archive gate, the version load -- answers here exactly as it does on
   ``execute``. A request that was rejected must never look like one that was started,
   because a client that got a 202 would poll for a row that does not exist.
4. **It runs the version's own artifact**, through ``run_version_backtest`` like the
   synchronous route (Requirement 5.2), and is metered by the same quota dependency.

WHY IT MATTERS THAT THIS FILE IS THOROUGH ON POINT 3. ``execute``'s refusals used to be
recorded in ``tests/regression/baseline/surface_backtester.json`` as the handler's own
``raises`` list. They are not any more: the body moved into ``_execute_version_backtest``,
which both routes share, and that capture reads a handler's own ast. The coverage was not
dropped, it moved here and into ``test_task_6_1_backtest_execute.py`` -- from a derived
snapshot to direct assertions. See that baseline's
``_baseline_note_backtest_execute_shares_its_core``.

Doubles are imported from ``test_task_6_1_backtest_execute`` rather than redefined, so the
fake strategy service -- which applies the ownership predicate (``id`` AND ``user_id``)
exactly as ``StrategyService.get_strategy`` does -- has one definition for both routes.
"""

import asyncio
import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_archive as archive
from backend_app.routers import strategy_operations as SO

from tests.test_task_6_1_backtest_execute import (  # noqa: F401 - fixtures are used by name
    BACKTEST_ID,
    BODY,
    CURRENT_VERSION_ID,
    FOREIGN_VERSION_ID,
    OLDER_VERSION_ID,
    OWNER,
    STRATEGY_ID,
    FakeStrategyService,
    RecordingFeed,
    _strategy_row,
    _version_row,
    as_intruder,
    as_owner,
    clear_rate_limit,
    feed,
    fresh_archive_probe,
    service,
)

CANONICAL_PATH = f"/api/strategy-operations/strategies/{STRATEGY_ID}/backtests/start"
COMPAT_PATH = f"/api/strategies/{STRATEGY_ID}/backtests/start"

#: Long enough that a route which WAITED for the run could not answer first, short enough
#: that the suite does not crawl. The assertion is on the run's completion flag, not on this
#: number, so it is a margin rather than a measurement.
RUN_DURATION_S = 0.5


class AnnouncingRuntime:
    """A run that creates its row, then takes a while -- like the real one.

    ``on_record_created`` is invoked before the simulated work, which is where the real
    runtime invokes it: ``BacktestService.create_backtest`` inserts the row in `PHASE K`,
    before the historical fetch in `PHASE B`.
    """

    def __init__(self, body, raises=None, announce=True, duration=RUN_DURATION_S):
        self.body = body
        self.calls: List[Dict[str, Any]] = []
        self.raises = raises
        self.announce = announce
        self.duration = duration
        self.completed = False
        self.result = {
            "backtest_id": BACKTEST_ID,
            "status": "completed",
            "results": {"Total Trades": 7, "final_capital": 61_000.0},
        }

    async def run_version_backtest(self, *, on_record_created=None, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        if self.announce and on_record_created is not None:
            on_record_created(BACKTEST_ID)
        await asyncio.sleep(self.duration)
        self.completed = True
        return self.result

    async def run_backtest(self, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError(
            "The asynchronous route must go through run_version_backtest, so that the "
            "version's own persisted plan is what executes (Requirement 5.2)."
        )


@pytest.fixture
def announcing(monkeypatch):
    """Installs the runtime above behind the endpoint's own factory seam."""
    holder: Dict[str, Any] = {}

    def factory(body):
        holder["runtime"] = AnnouncingRuntime(
            body, raises=holder.get("raises"), announce=holder.get("announce", True)
        )
        return holder["runtime"]

    monkeypatch.setattr(SO, "_backtest_runtime_for", factory)
    return holder


def _install_service(monkeypatch, strategy_row):
    state = FakeStrategyService(
        strategies=[strategy_row],
        versions=[
            _version_row(CURRENT_VERSION_ID, "v2.0"),
            _version_row(OLDER_VERSION_ID, "v1.0"),
            _version_row(FOREIGN_VERSION_ID, "v9.0", strategy_id="other-strategy"),
        ],
    )

    async def factory():
        return state

    monkeypatch.setattr(SO, "get_strategy_service", factory)
    return state


def post(client, body=None, path=CANONICAL_PATH):
    return client.post(path, json={**BODY, **(body or {})})


# ---------------------------------------------------------------------------
# 1. It answers with a handle, before the run has finished
# ---------------------------------------------------------------------------


class TestItAnswersBeforeTheRunFinishes:
    def test_it_answers_202_while_the_run_is_still_going(
        self, as_owner, service, feed, announcing
    ):
        response = post(as_owner)

        assert response.status_code == 202, response.text
        # The claim, asserted against the run itself rather than against a clock: the
        # response is already here and the simulation has not returned.
        assert announcing["runtime"].completed is False

    def test_the_handle_is_the_backtest_row_id(self, as_owner, service, feed, announcing):
        response = post(as_owner)

        body = response.json()
        assert body["backtest_id"] == BACKTEST_ID
        assert body["status"] == "running"

    def test_it_does_not_answer_with_the_result(self, as_owner, service, feed, announcing):
        """A handle, not a result. The caller polls `GET /api/backtests/{id}` for the rest."""
        body = post(as_owner).json()

        assert "results" not in body
        assert sorted(body) == ["backtest_id", "status"]

    def test_the_run_is_started_through_the_version_aware_entry_point(
        self, as_owner, service, feed, announcing
    ):
        """Requirement 5.2: the version's own persisted plan is what executes.

        `AnnouncingRuntime.run_backtest` raises if it is ever reached.
        """
        post(as_owner)

        calls = announcing["runtime"].calls
        assert len(calls) == 1
        assert calls[0]["version_row"]["id"] == CURRENT_VERSION_ID
        # The feed comes from the module's one seam, not from a venue named in the router.
        assert calls[0]["exchange_instance"] is RecordingFeed.SENTINEL

    def test_both_registered_paths_answer(self, as_owner, service, feed, announcing):
        assert post(as_owner, path=CANONICAL_PATH).status_code == 202
        assert post(as_owner, path=COMPAT_PATH).status_code == 202


# ---------------------------------------------------------------------------
# 2. A refusal is a refusal, never an accepted job
# ---------------------------------------------------------------------------


class TestARefusalIsNotAnAcceptedJob:
    """Every check that precedes persistence. A 202 for a rejected request would send the
    client to poll a row that was never created."""

    def test_a_stranger_gets_the_same_404_as_a_missing_strategy(
        self, as_intruder, service, feed, announcing
    ):
        response = post(as_intruder)

        assert response.status_code == 404, response.text
        assert response.json()["detail"]["error"] == "STRATEGY_NOT_FOUND"
        # Nothing was started on another tenant's strategy.
        assert "runtime" not in announcing

    def test_a_missing_strategy_is_the_same_404(self, as_owner, service, feed, announcing):
        response = as_owner.post(
            "/api/strategies/99999999-9999-4999-8999-999999999999/backtests/start",
            json=BODY,
        )

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "STRATEGY_NOT_FOUND"

    def test_an_archived_strategy_is_refused_rather_than_started(
        self, as_owner, feed, announcing, monkeypatch
    ):
        """Requirement 3.3, through the same ArchiveRejected mapping the deploy route uses."""
        _install_service(
            monkeypatch,
            _strategy_row(**{archive.ARCHIVED_AT_COLUMN: "2024-05-01T00:00:00+00:00"}),
        )

        response = post(as_owner)

        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error"] == "STRATEGY_ARCHIVED"
        assert "runtime" not in announcing

    def test_another_strategys_version_is_refused_rather_than_started(
        self, as_owner, service, feed, announcing
    ):
        response = post(as_owner, body={"version_id": FOREIGN_VERSION_ID})

        assert response.status_code in (404, 422), response.text
        assert response.json()["detail"]["error"] == "BACKTEST_VERSION_UNAVAILABLE"
        assert "runtime" not in announcing

    def test_a_malformed_body_is_refused_before_anything_starts(
        self, as_owner, service, feed, announcing
    ):
        response = as_owner.post(CANONICAL_PATH, json={"start_date": "not-a-date"})

        assert response.status_code == 422
        assert "runtime" not in announcing


# ---------------------------------------------------------------------------
# 3. The route's own wiring
# ---------------------------------------------------------------------------


class TestTheRouteIsWiredLikeItsSynchronousSibling:
    def test_it_is_metered_by_the_same_quota_dependency(self):
        """Without this it would be an unmetered way to run the identical computation."""
        import inspect

        from backend_app.core.subscription_dependencies import check_backtest_quota

        params = inspect.signature(SO.start_backtest).parameters
        quota = params["_quota"].default
        assert getattr(quota, "dependency", None) is check_backtest_quota

    def test_it_shares_its_core_with_the_synchronous_route(self):
        """One ownership check, one archive gate, one version load, one refusal mapping.

        Asserted structurally because it is the reason the extraction exists: two routes
        maintaining their own copy of an authorization boundary is how one of them
        eventually stops making it.
        """
        import inspect

        for fn in (SO.execute_backtest, SO.start_backtest):
            source = inspect.getsource(fn)
            assert "_execute_version_backtest" in source, (
                f"{fn.__name__} no longer delegates to the shared core; the two routes can "
                "now drift on ownership and refusals."
            )

    def test_the_runtime_seam_is_optional(self):
        """`on_record_created` must stay optional, or the synchronous route changes too."""
        import inspect

        from backend_app.backend.backtest_runtime import BacktestRuntime

        for name in ("run_backtest", "run_version_backtest"):
            param = inspect.signature(getattr(BacktestRuntime, name)).parameters[
                "on_record_created"
            ]
            assert param.default is None
            assert param.kind is inspect.Parameter.KEYWORD_ONLY
