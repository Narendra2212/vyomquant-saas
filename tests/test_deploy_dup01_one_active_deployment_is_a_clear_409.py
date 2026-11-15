"""DEPLOY-DUP-01: deploying a strategy that already has an active deployment is a clean 409.

Found on 2026-11-15: the FROM strategy had a PAUSED paper deployment; the user pressed Deploy
(live) and got the database's raw ``duplicate key value violates unique constraint
"uq_strategy_deployments_one_live_per_user"`` (23505). The index is right -- one active
deployment per strategy -- so the app now checks the same rule first: the preflight reports a
failed ``no_active_deployment`` condition, and the write path raises ``DeploymentConflict``
(409) with a sentence telling the user to resume or stop the existing deployment.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from backend_app.backend import strategy_service as ss

REPO = Path(__file__).resolve().parents[1]


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a):
        return self

    async def execute(self):
        return SimpleNamespace(data=list(self.rows))


def _svc(rows):
    svc = ss.StrategyService.__new__(ss.StrategyService)
    sb = SimpleNamespace(table=lambda _n: _Q(rows))
    return svc, sb


def test_a_paused_deployment_fails_the_preflight_condition_with_a_sentence():
    svc, sb = _svc([{"id": "d1", "mode": "paper", "status": "paused"}])
    cond = asyncio.run(svc._no_active_deployment_condition(sb, {"id": "u1"}, "s1"))
    assert cond.name == "no_active_deployment"
    assert cond.code == "STRATEGY_ALREADY_DEPLOYED"
    assert "paper deployment that is paused" in cond.message
    assert "Resume or stop it" in cond.message


def test_stopped_deployments_do_not_block_a_new_deploy():
    svc, sb = _svc([{"id": "d1", "mode": "live", "status": "stopped"},
                    {"id": "d2", "mode": "paper", "status": "failed"}])
    cond = asyncio.run(svc._no_active_deployment_condition(sb, {"id": "u1"}, "s1"))
    assert cond.status == "passed"


def test_an_unreadable_table_is_pending_never_passed():
    svc = ss.StrategyService.__new__(ss.StrategyService)

    def _boom(_n):
        raise RuntimeError("db down")

    cond = asyncio.run(svc._no_active_deployment_condition(
        SimpleNamespace(table=_boom), {"id": "u1"}, "s1"))
    assert cond.status != "passed"


def test_the_conflict_is_a_409_and_carries_no_upgrade_link():
    assert ss.DeploymentConflict.http_status == 409
    assert issubclass(ss.DeploymentConflict, ss.PlanLimitRefused)
    for router in ("strategies.py", "strategy_operations.py"):
        src = (REPO / "backend_app" / "routers" / router).read_text()
        assert "e.http_status == 403" in src, router


def test_the_statuses_match_the_unique_index():
    sql = "\n".join(p.read_text() for p in (REPO / "backend_app" / "migrations").glob("*.sql")
                    if "uq_strategy_deployments_one_live_per_user" in p.read_text())
    for status in ss.ACTIVE_DEPLOYMENT_STATUSES:
        assert f"'{status}'" in sql, status


def test_the_frontend_orders_the_new_condition():
    src = (REPO / "algo22-terminal" / "src" / "lib" / "deployPreflight.js").read_text()
    assert src.index("'plan_allowance'") < src.index("'no_active_deployment'")
