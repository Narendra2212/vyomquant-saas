"""tests/test_dashboard_ingester_user_list_regression.py

The dashboard ingester reads the user list from the service that actually holds it.

THE DEFECT
----------
``DashboardDataIngester._ingest_all_users`` read the registered-user map as
``app_state.portfolio_cache_updater._user_exchanges``, behind a ``hasattr`` guard.
NOTHING ASSIGNS THAT ATTRIBUTE. The updater is a module-level singleton in
``backend_app.backend.portfolio_cache_updater`` and ``main.py`` starts it via
``start_portfolio_cache_updater()``, which never touches ``app_state``. So the guard was
False on every pass, the method returned before ingesting anything, and it logged

    PortfolioCacheUpdater not available for user list

once per interval - 588 times in a twelve-hour production CloudWatch window - while the
service it called unavailable was running and had logged ACTIVE at startup.

WHAT IS DELIBERATELY *NOT* CLAIMED HERE
---------------------------------------
Repointing the lookup does not make the pipeline do work. Nothing in the tree calls
``register_user_for_portfolio_updates`` or ``PortfolioCacheUpdater.register_user``, so the
map is empty in production and both the ingester and the cache updater are idle by
construction. That wiring gap is a separate, unfixed defect; these tests pin the lookup and
the logging, and ``test_the_wiring_gap_is_real_and_unfixed`` records the gap so it cannot be
quietly forgotten or mistaken for fixed.
"""

import asyncio
import inspect
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import dashboard_data_ingester as DDI
from backend_app.backend import portfolio_cache_updater as PCU


@pytest.fixture
def updater():
    """The real singleton, with its registry emptied and restored around each test."""
    saved = dict(PCU.portfolio_cache_updater._user_exchanges)
    PCU.portfolio_cache_updater._user_exchanges.clear()
    try:
        yield PCU.portfolio_cache_updater
    finally:
        PCU.portfolio_cache_updater._user_exchanges.clear()
        PCU.portfolio_cache_updater._user_exchanges.update(saved)


@pytest.fixture
def ingester(monkeypatch):
    """A real ingester whose per-user work is recorded instead of hitting Redis/QuestDB."""
    obj = DDI.DashboardDataIngester()
    seen = []

    async def record(user_id, exchange_id):
        seen.append((user_id, exchange_id))

    monkeypatch.setattr(obj, "_ingest_user_data", record)
    obj.seen = seen
    return obj


class TestTheUserListIsReadFromTheSingleton:
    def test_a_registered_user_is_ingested(self, updater, ingester):
        """The defect, at the level a user would notice: nothing was ever ingested."""
        updater.register_user("user-1", "binance")

        asyncio.run(ingester._ingest_all_users())

        assert ingester.seen == [("user-1", "binance")]

    def test_every_registered_user_is_ingested(self, updater, ingester):
        updater.register_user("user-1", "binance")
        updater.register_user("user-2", "kraken")

        asyncio.run(ingester._ingest_all_users())

        assert sorted(ingester.seen) == [("user-1", "binance"), ("user-2", "kraken")]

    def test_the_lookup_does_not_go_through_app_state(self):
        """Structural. The old read is gone from the code, not merely bypassed."""
        src = inspect.getsource(DDI.DashboardDataIngester._ingest_all_users)
        body = src.split(chr(34) * 3)[-1]  # everything after the docstring
        assert "app_state" not in body, (
            "_ingest_all_users still reaches app_state for the user list; that attribute "
            "is never assigned, which is the whole defect"
        )
        assert "portfolio_cache_updater" in body
        assert "registered_users" in body

    def test_one_failing_user_does_not_stop_the_others(self, updater, ingester, monkeypatch):
        """Pre-existing behaviour that must survive: the per-user try/except stays."""
        updater.register_user("bad", "binance")
        updater.register_user("good", "kraken")
        done = []

        async def sometimes_raises(user_id, exchange_id):
            if user_id == "bad":
                raise RuntimeError("that user is unreadable")
            done.append(user_id)

        monkeypatch.setattr(ingester, "_ingest_user_data", sometimes_raises)
        asyncio.run(ingester._ingest_all_users())

        assert done == ["good"]


class TestAnEmptyRegistryIsNotReportedAsAFault:
    def test_no_warning_when_nobody_is_registered(self, updater, ingester, caplog):
        """An empty registry is a state, not a failure, and must not warn every interval."""
        with caplog.at_level(logging.DEBUG, logger=DDI.logger.name):
            asyncio.run(ingester._ingest_all_users())

        assert ingester.seen == []
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == [], (
            "an empty registry was logged at WARNING or above: %s"
            % [r.getMessage() for r in warnings]
        )

    def test_it_no_longer_claims_the_updater_is_unavailable(self, updater, ingester, caplog):
        """The exact production sentence, asserted absent.

        It was false: the updater was running and had logged ACTIVE.
        """
        with caplog.at_level(logging.DEBUG, logger=DDI.logger.name):
            asyncio.run(ingester._ingest_all_users())

        blob = " ".join(r.getMessage() for r in caplog.records)
        assert "not available for user list" not in blob, blob


class TestTheSnapshotCannotBeMutatedUnderAnIterator:
    def test_registered_users_returns_a_copy(self, updater):
        snapshot = updater.registered_users()
        snapshot["injected"] = "binance"

        assert "injected" not in updater._user_exchanges, (
            "registered_users() handed out the live dict, so a caller can register users "
            "by mutating what it returns"
        )

    def test_registering_during_ingestion_does_not_raise(self, updater, ingester, monkeypatch):
        """A registration landing mid-loop used to risk RuntimeError from the live dict.

        ``dictionary changed size during iteration`` is raised by the FOR LOOP, not by the
        writer, so it would have surfaced inside whichever background loop was running.
        """
        updater.register_user("user-1", "binance")

        async def registers_another(user_id, exchange_id):
            updater.register_user("late-arrival", "kraken")

        monkeypatch.setattr(ingester, "_ingest_user_data", registers_another)

        asyncio.run(ingester._ingest_all_users())  # must not raise

        assert "late-arrival" in updater._user_exchanges


def test_the_wiring_gap_is_real_and_unfixed():
    """NOT a fix - a record. Nothing registers users, so the pipeline is idle regardless.

    If a future change adds a caller, this test fails and should be deleted along with the
    "idle by construction" paragraph in ``_ingest_all_users``. Failing here means the gap
    closed, which is good news that must not be silent.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "backend_app"
    callers = []
    for path in root.rglob("*.py"):
        if path.name == "portfolio_cache_updater.py":
            continue  # its own definitions and the thin module-level wrappers
        text = path.read_text(encoding="utf-8", errors="replace")
        if "register_user_for_portfolio_updates(" in text or ".register_user(" in text:
            callers.append(str(path.relative_to(root)))

    assert callers == [], (
        "something now registers users for portfolio updates (%s). The wiring gap this "
        "test records has closed: delete this test and the idle-by-construction note in "
        "DashboardDataIngester._ingest_all_users." % callers
    )
