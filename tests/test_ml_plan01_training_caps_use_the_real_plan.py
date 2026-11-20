"""ML-PLAN-01 -- ML training caps are resolved from the account's real plan.

Production, 2026-11-20: a Pro account saved a strategy with an XGBoost node. The feature
gate admitted it (it reads profiles.subscription_tier), but prepare_training's caps were
resolved from the request user -- JWT claims, no plan -- so the caps fell to FREE:
"MAX_EPOCHS: requested 200 rounds, allowed 0 rounds ... This plan includes no ML training".
Every paid user's training was refused.
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend_app.backend import strategy_service as svc
from backend_app.backend.ml_training_policy import ML_TIER_CAPS, resolve_caps
from backend_app.core.tenant import TenantPlan

JWT_USER = {"id": "11111111-1111-4111-8111-111111111111", "email": "x@y.z"}  # no plan claim


@pytest.mark.asyncio
async def test_the_request_user_gets_the_profile_plan():
    with patch("backend_app.core.subscription_dependencies.get_user_plan",
               AsyncMock(return_value="pro")):
        user = await svc._user_with_plan(object(), JWT_USER)
    assert user["plan"] == "pro"
    assert user["id"] == JWT_USER["id"]
    assert "plan" not in JWT_USER, "the caller's mapping is not mutated"


@pytest.mark.asyncio
async def test_with_the_real_plan_the_caps_allow_training():
    with patch("backend_app.core.subscription_dependencies.get_user_plan",
               AsyncMock(return_value="pro")):
        user = await svc._user_with_plan(object(), JWT_USER)
    from backend_app.backend.ml_models import MODEL_SPECS
    caps = resolve_caps(user, MODEL_SPECS["xgboost"])
    assert caps.max_epochs > 0
    # Without the fix the bare JWT user resolves to FREE, which allows no training.
    assert resolve_caps(JWT_USER, MODEL_SPECS["xgboost"]).max_epochs == 0
    assert ML_TIER_CAPS[TenantPlan.FREE].max_epochs == 0


@pytest.mark.asyncio
async def test_an_unreadable_plan_stays_absent_and_fails_closed():
    with patch("backend_app.core.subscription_dependencies.get_user_plan",
               AsyncMock(side_effect=RuntimeError("db down"))):
        user = await svc._user_with_plan(object(), JWT_USER)
    assert "plan" not in user


@pytest.mark.asyncio
async def test_a_plan_already_present_is_kept_without_a_read():
    reader = AsyncMock(return_value="free")
    with patch("backend_app.core.subscription_dependencies.get_user_plan", reader):
        user = await svc._user_with_plan(object(), {**JWT_USER, "plan": "business"})
    assert user["plan"] == "business"
    reader.assert_not_awaited()


def test_both_call_sites_pass_the_resolved_user():
    import inspect
    src = inspect.getsource(svc)
    assert src.count("user=await _user_with_plan(sb, user),") == 2
    assert "preparation = await prepare_training(\n                plan,\n                user=user," not in src
