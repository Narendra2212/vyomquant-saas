"""
tests/test_dep_strat01_strategy_status_follows_its_deployments.py

DEP-STRAT-01: a strategy kept saying ``running`` after its only deployment ended.

Found during the live testnet run: a restart refused (correctly) to resume a LIVE
deployment and marked that deployment ``failed``, but the strategy row stayed ``running``.
The Strategies table showed a live bot that no longer existed, and offered Pause instead
of Deploy.

``DeploymentStateStore.mark_failed`` / ``mark_stopped`` now settle the parent strategy:
only when it is still ``running``/``deploying`` and none of its deployments is still live.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend_app.backend.deployment_state import DeploymentStateStore

pytestmark = pytest.mark.asyncio


class _Query:
    def __init__(self, rows):
        self._rows, self._op, self._payload, self._filters = rows, "select", None, []

    def select(self, *_a, **_k):
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def eq(self, col, val):
        self._filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        self._filters.append(lambda r: r.get(col) in vals)
        return self

    def execute(self):
        hit = [r for r in self._rows if all(f(r) for f in self._filters)]
        if self._op == "update":
            for r in hit:
                r.update(self._payload)
        return SimpleNamespace(data=[dict(r) for r in hit])


class _DB:
    def __init__(self, deployments, strategies):
        self.tables = {"strategy_deployments": deployments, "strategies": strategies}

    def table(self, name):
        return _Query(self.tables[name])


@pytest.fixture(autouse=True)
def _no_quota(monkeypatch):
    monkeypatch.setattr(
        "backend_app.core.subscription_engine.SubscriptionEngine.decrement_quota_usage",
        AsyncMock(return_value=None),
    )


def _dep(id_, status, strategy_id="s1"):
    return {"id": id_, "user_id": "u1", "strategy_id": strategy_id, "status": status}


async def test_a_failed_last_deployment_settles_the_strategy_to_failed():
    db = _DB([_dep("d1", "running")], [{"id": "s1", "status": "running"}])
    assert await DeploymentStateStore(lambda: db).mark_failed("d1", "could not resume") is True
    assert db.tables["strategies"][0]["status"] == "failed"


async def test_a_stopped_last_deployment_settles_the_strategy_to_stopped():
    db = _DB([_dep("d1", "running")], [{"id": "s1", "status": "running"}])
    assert await DeploymentStateStore(lambda: db).mark_stopped("d1") is True
    assert db.tables["strategies"][0]["status"] == "stopped"


async def test_a_strategy_with_another_live_deployment_stays_running():
    db = _DB(
        [_dep("d1", "running"), _dep("d2", "running")],
        [{"id": "s1", "status": "running"}],
    )
    await DeploymentStateStore(lambda: db).mark_failed("d1", "x")
    assert db.tables["strategies"][0]["status"] == "running"


async def test_a_strategy_the_user_already_moved_is_not_overwritten():
    db = _DB([_dep("d1", "running")], [{"id": "s1", "status": "paused"}])
    await DeploymentStateStore(lambda: db).mark_failed("d1", "x")
    assert db.tables["strategies"][0]["status"] == "paused"


async def test_a_stale_report_on_an_ended_deployment_changes_nothing():
    db = _DB([_dep("d1", "stopped")], [{"id": "s1", "status": "running"}])
    assert await DeploymentStateStore(lambda: db).mark_failed("d1", "late") is False
    assert db.tables["strategies"][0]["status"] == "running"
