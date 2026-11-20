"""PAUSE-01: Pause / Resume / Stop on the Strategies page follow the deployments.

Found on 2026-11-15 with a running LIVE FROM bot:

1. Pause refused 409 "already paused" because ``strategies.is_active`` was false -- the version
   deploy path sets ``status='running'`` and never sets ``is_active``.
2. Pause, when it did run, STOPPED the deployments, and Resume started a fresh PAPER deployment
   -- so resuming a live bot silently turned it into a paper bot.
3. A paused strategy offered Deploy, not Resume, and nothing offered Stop; Delete refuses while a
   deployment is active, so a paused strategy could not be deleted either.
4. Stop required the live-trading entitlement and returned early unless the row said running.

Now: Pause moves running deployments to ``paused`` through the deployment lifecycle, Resume moves
the same deployments back, Stop ends running AND paused ones and is never plan-gated.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend_app.backend import strategy_lifecycle as lc
from backend_app.routers import strategies as routes

REPO = Path(__file__).resolve().parents[1]


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


def _call(route, row, moved, **extra):
    q = _Q(row)
    sb = SimpleNamespace(table=lambda _n: q)
    service = SimpleNamespace(
        transition_all_deployments=AsyncMock(return_value=moved),
        stop_all_deployments=AsyncMock(return_value=moved),
    )
    with patch.object(routes, "_sb", AsyncMock(return_value=sb)), \
         patch("backend_app.backend.strategy_service.get_strategy_service",
               AsyncMock(return_value=service)):
        out = asyncio.run(route("s1", user={"id": "u1"}, fleet=None, **extra))
    return out, service, row


# ── Pause ────────────────────────────────────────────────────────────────────────────────


def test_a_running_strategy_with_is_active_false_is_paused_not_stopped():
    out, service, row = _call(routes.pause_strategy,
                              {"status": "running", "is_active": False, "symbol": "BTC/USDC"}, 1)
    assert out["status"] == "paused" and row["status"] == "paused"
    service.transition_all_deployments.assert_awaited_once_with(
        {"id": "u1"}, "s1", lc.ACTION_PAUSE, "running")
    service.stop_all_deployments.assert_not_awaited()


def test_an_already_paused_strategy_with_nothing_running_is_409():
    with pytest.raises(HTTPException) as exc:
        _call(routes.pause_strategy, {"status": "paused", "is_active": False}, 0)
    assert exc.value.status_code == 409


def test_a_stale_paused_row_does_not_shield_a_running_deployment():
    out, service, _ = _call(routes.pause_strategy, {"status": "paused", "is_active": False}, 1)
    assert out["status"] == "paused"
    service.transition_all_deployments.assert_awaited_once()


# ── Resume ───────────────────────────────────────────────────────────────────────────────


def test_resume_continues_the_paused_deployments_and_never_deploys_paper():
    with patch.object(routes, "_deploy_current_version_paper",
                      AsyncMock(side_effect=AssertionError("must not redeploy"))):
        out, service, row = _call(routes.resume_strategy, {"status": "paused"}, 1)
    assert out["status"] == "running" and out["resumed"] == 1
    service.transition_all_deployments.assert_awaited_once_with(
        {"id": "u1"}, "s1", lc.ACTION_RESUME, "paused")
    assert row["status"] == "running"


def test_resume_with_nothing_paused_is_409_nothing_to_resume():
    with pytest.raises(HTTPException) as exc:
        _call(routes.resume_strategy, {"status": "stopped"}, 0)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "NOTHING_TO_RESUME"


# ── Stop ─────────────────────────────────────────────────────────────────────────────────


def test_stop_is_not_plan_gated():
    params = inspect.signature(routes.stop_bot).parameters
    assert "_feature" not in params, "stopping a bot must never require a paid plan"


def test_stop_ends_a_paused_strategy_too():
    ws = SimpleNamespace(broadcast_user=AsyncMock())
    out, service, row = _call(routes.stop_bot, {"status": "paused", "symbol": "BTC/USDC"}, 1,
                              ws_mgr=ws)
    assert out["status"] == "stopped" and row["status"] == "stopped"
    service.stop_all_deployments.assert_awaited_once()


def test_stop_all_deployments_includes_paused_rows():
    from backend_app.backend.strategy_service import StrategyService

    src = inspect.getsource(StrategyService.stop_all_deployments)
    assert '.in_("status", ["running", "paused"])' in src


# ── The row offers ───────────────────────────────────────────────────────────────────────


def test_the_strategies_row_offers_resume_and_stop_once_paused():
    src = (REPO / "algo22-terminal" / "src" / "lib" / "rowActions.js").read_text()
    body = src.split("export function ownerRowActions", 1)[1].split("\n}\n", 1)[0]
    assert "status === 'paused') lifecycle = [ROW_ACTION.RESUME, ROW_ACTION.STOP]" in body
    assert "status === 'running') lifecycle = [ROW_ACTION.PAUSE, ROW_ACTION.STOP]" in body
    page = (REPO / "algo22-terminal" / "src" / "pages" / "Strategies.jsx").read_text()
    assert "[ROW_ACTION.RESUME]: {" in page and "[ROW_ACTION.STOP]: {" in page
