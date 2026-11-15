"""PAUSE-01: Pause refused a running live bot as "already paused".

Found on 2026-11-15: the owner could not pause (and therefore could not delete) a running live
FROM deployment. `POST /api/strategies/{id}/pause` refused 409 whenever `strategies.is_active`
was false -- and the version deploy path sets `status='running'` without ever setting
`is_active`, so every strategy deployed through it hit this. The deployments now decide.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend_app.routers import strategies as routes


class _Q:
    def __init__(self, row):
        self.row, self.updated = row, None

    def select(self, *_a, **_k):
        return self

    def update(self, payload):
        self.updated = payload
        return self

    def eq(self, *_a):
        return self

    async def execute(self):
        if self.updated is not None:
            self.row.update(self.updated)
        return SimpleNamespace(data=[dict(self.row)])


def _pause(row, stopped):
    q = _Q(row)
    sb = SimpleNamespace(table=lambda _n: q)
    service = SimpleNamespace(stop_all_deployments=AsyncMock(return_value=stopped))
    with patch.object(routes, "_sb", AsyncMock(return_value=sb)), \
         patch("backend_app.backend.strategy_service.get_strategy_service",
               AsyncMock(return_value=service)):
        out = asyncio.run(routes.pause_strategy("s1", user={"id": "u1"}, fleet=None))
    return out, service, row


def test_a_running_strategy_with_is_active_false_is_paused():
    """The production case: status running, is_active false, one live deployment."""
    out, service, row = _pause({"status": "running", "is_active": False, "symbol": "BTC/USDC"}, 1)
    assert out["status"] == "paused"
    service.stop_all_deployments.assert_awaited_once()
    assert row["status"] == "paused"


def test_an_already_paused_strategy_with_nothing_running_is_409():
    with pytest.raises(HTTPException) as exc:
        _pause({"status": "paused", "is_active": False}, 0)
    assert exc.value.status_code == 409


def test_a_row_saying_paused_while_a_deployment_still_runs_is_still_stopped():
    """The deployments are the truth: a stale `paused` row must not shield a running bot."""
    out, service, _row = _pause({"status": "paused", "is_active": False}, 1)
    assert out["status"] == "paused"
    service.stop_all_deployments.assert_awaited_once()


def test_resume_refuses_while_running_whatever_is_active_says():
    q = _Q({"status": "running", "is_active": False, "dag_config": {}})
    sb = SimpleNamespace(table=lambda _n: q)
    with patch.object(routes, "_sb", AsyncMock(return_value=sb)):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(routes.resume_strategy("s1", user={"id": "u1"}, fleet=None))
    assert exc.value.status_code == 409
