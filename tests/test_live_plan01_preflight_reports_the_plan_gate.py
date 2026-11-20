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


# ── QUOTA-MODE-01: paper deploys draw on the paper allowance ──────────────────────────


def test_paper_deploys_use_the_paper_allowance_and_live_the_bot_allowance():
    from backend_app.backend.strategy_service import deployment_quota_resource

    assert deployment_quota_resource("paper") == "paper_strategies"
    assert deployment_quota_resource("PAPER") == "paper_strategies"
    assert deployment_quota_resource(None, "paper") == "paper_strategies"
    assert deployment_quota_resource("live") == "bots"
    # Unrecognised counts against the stricter live allowance.
    assert deployment_quota_resource(None, None) == "bots"


def test_the_free_plan_can_run_its_one_paper_strategy():
    """The Free plan sets bots=0 and paper_strategies=1: reserving `bots` for a paper deploy
    made its own paper allowance unusable ("Quota exceeded for bots: 0/0")."""
    from backend_app.backend.strategy_service import deployment_quota_resource
    from backend_app.core.subscription_engine import SubscriptionEngine

    resource = deployment_quota_resource("paper")
    assert SubscriptionEngine.get_quota_limit("free", resource) >= 1
    assert SubscriptionEngine.get_quota_limit("free", "bots") == 0


def _allowance(plan, used, mode="paper"):
    service = StrategyService.__new__(StrategyService)
    with patch("backend_app.core.subscription_dependencies.get_user_plan", AsyncMock(return_value=plan)), \
         patch("backend_app.core.subscription_engine.SubscriptionEngine.get_quota_usage",
               AsyncMock(return_value=used)):
        return asyncio.run(service._plan_allowance_condition(
            None, {"id": "u1"}, SimpleNamespace(mode=mode), mode))


def test_the_preflight_reports_a_used_up_paper_allowance():
    c = _allowance("free", 1)
    assert c.status == "failed" and c.code == "PAPER_STRATEGY_LIMIT_REACHED"
    assert "allows 1 running paper strategies" in c.message


def test_the_preflight_passes_a_free_slot():
    assert _allowance("free", 0).status == "passed"


def test_a_plan_refusal_is_a_403_with_its_own_code_not_version_not_found():
    from backend_app.backend.strategy_service import PlanLimitRefused

    e = PlanLimitRefused("PAPER_STRATEGY_LIMIT_REACHED", "x")
    assert e.http_status == 403 and e.code == "PAPER_STRATEGY_LIMIT_REACHED"
    assert not isinstance(e, ValueError)  # so the ValueError -> 404 mapping cannot catch it


def test_a_zero_allowance_says_not_included_rather_than_stop_one():
    c = _allowance("free", 0, mode="live")
    assert c.status == "failed"
    assert "does not include running live strategies" in c.message
    assert "Stop one" not in c.message
