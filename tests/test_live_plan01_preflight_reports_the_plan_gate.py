"""LIVE-PLAN-01: the deploy preflight reports the live-trading plan gate.

Found during the launch new-user run: a Free user's live preflight said only "bind an exchange
account" (LIVE_REQUIRES_EXCHANGE_ACCOUNT). The plan check -- "Live trading requires a paid
subscription plan" -- lived only on the write path, so the user would go and connect exchange
keys and be refused anyway at the final Deploy. The preflight now reports it as
``live_entitlement``, for a live deploy only.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend_app.backend.strategy_service import StrategyService


def _condition(plan: str, has_live, mode="live"):
    service = StrategyService.__new__(StrategyService)
    request = SimpleNamespace(mode=mode)
    plan_mock = AsyncMock(return_value=plan)
    if isinstance(has_live, Exception):
        entitled = AsyncMock(side_effect=has_live)
    else:
        entitled = AsyncMock(return_value=has_live)
    with patch("backend_app.core.subscription_dependencies.get_user_plan", plan_mock), \
         patch("backend_app.core.subscription_engine.SubscriptionEngine.check_feature_entitlement",
               entitled):
        return asyncio.run(service._live_entitlement_condition(None, {"id": "u1"}, request, mode))


def test_a_free_user_is_told_to_upgrade_before_connecting_keys():
    c = _condition("free", False)
    assert c.name == "live_entitlement"
    assert c.status == "failed"
    assert c.code == "LIVE_TRADING_NOT_INCLUDED"
    assert "Upgrade to Starter" in c.message
    assert c.detail["upgrade_url"] == "/app/billing"


def test_a_paid_plan_passes():
    c = _condition("starter", True)
    assert c.status == "passed" and c.detail == {"plan": "starter"}


def test_paper_deploys_do_not_report_it():
    assert _condition("free", False, mode="paper") is None


def test_an_unreadable_plan_is_pending_never_passed():
    c = _condition("free", RuntimeError("db down"))
    assert c.status == "pending"


@pytest.mark.parametrize("mode", ["live", "LIVE"])
def test_mode_is_read_case_insensitively(mode):
    assert _condition("free", False, mode=mode).status == "failed"


def test_the_frontend_orders_it_first_after_prerequisites():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "algo22-terminal" / "src" / "lib"
           / "deployPreflight.js").read_text()
    order = src.split("PREFLIGHT_CONDITION_ORDER = Object.freeze([", 1)[1].split("]);", 1)[0]
    names = [line.strip().strip("',") for line in order.splitlines()
             if line.strip().startswith("'")]
    assert names[:2] == ["deploy_prerequisites", "live_entitlement"]
