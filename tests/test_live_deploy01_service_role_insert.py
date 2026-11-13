"""
tests/test_live_deploy01_service_role_insert.py

LIVE-DEPLOY-01: a LIVE deployment could never be created.

WHAT WAS WRONG
--------------
SEC-16 (migration ``034_strategy_deployments_browser_role_guard.sql``) lets the browser
roles (``anon`` / ``authenticated``) insert PAPER deployment rows only, so a user holding
their own JWT cannot write ``mode='live'`` through PostgREST and skip the API's gates. Its
own header recorded the follow-up: "enabling live deploys requires moving that one insert
to a service-role client AFTER the server-side entitlement and live-gate checks". That move
was never made, so ``deploy_version`` kept inserting under the caller's JWT and every live
deploy failed in production with::

    42501 public.strategy_deployments: role authenticated may only create paper deployments

``deploy_version`` also checked no PLATFORM live gate at all: on a paper platform the row
would have been written and the deployment would only have failed at its first order.

WHAT IS ASSERTED
----------------
1. A live deploy on a platform that is not live for this tenant is refused before any
   quota is reserved or any row written, with the gate's own code.
2. On a live platform the live row is written by the service-role client, never by the
   caller's client.
3. A paper deploy still goes through the caller's client and never touches the trusted one.
4. No trusted writer -> a classified 503 with the quota released, never a fallback to the
   caller's client.
5. Migration 034's guard is still there: the fix moves the writer, it does not open the
   browser roles.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import deployment_binding as db  # noqa: E402
from tests import test_task_8_2_deployment_binding as h  # noqa: E402

pytestmark = pytest.mark.asyncio

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_MIGRATION = (
    REPO_ROOT / "backend_app" / "migrations" / "034_strategy_deployments_browser_role_guard.sql"
)

LIVE_PAYLOAD = {"mode": "live", "exchange_account_id": h.ACCOUNT_ID, "execution_config": {}}
LIVE_ENV = {
    "VYOMQUANT_MODE": "live",
    "VYOMQUANT_ENABLE_LIVE_TRADING": "true",
    "VYOMQUANT_LIVE_TENANT_ALLOWLIST": h.USER_ID,
}


@pytest.fixture(autouse=True)
def _clean_module_state():
    db.reset_binding_column_support()
    db.reset_venue_timeframe_cache()
    yield
    db.reset_binding_column_support()
    db.reset_venue_timeframe_cache()


async def _deploy(user_sb, *, payload, env, service_client, environment="live", release=None, reserve=None):
    """``deploy_version`` with the quota and fleet seams granted, and the platform env and
    the trusted writer under the test's control (unlike the 8.2 harness, which makes them
    the same fake)."""
    service = h._service(user_sb)
    fleet = MagicMock()
    fleet.start_bot = AsyncMock(return_value=(True, "started"))
    reserve = reserve or AsyncMock(return_value=(True, 1, 10))
    release = release or AsyncMock(return_value=None)
    patches = [
        patch("backend_app.core.subscription_dependencies.get_user_plan", AsyncMock(return_value="pro")),
        patch("backend_app.core.subscription_engine.SubscriptionEngine.reserve_quota", reserve),
        patch(
            "backend_app.core.subscription_engine.SubscriptionEngine.check_feature_entitlement",
            AsyncMock(return_value=True),
        ),
        patch("backend_app.core.subscription_engine.SubscriptionEngine.decrement_quota_usage", release),
        patch.dict(os.environ, env, clear=False),
        patch("backend_app.core.dependencies.get_service_supabase", service_client),
    ]
    for p in patches:
        p.start()
    for key in set(LIVE_ENV) - set(env):
        os.environ.pop(key, None)
    app_state_patch = patch("backend_app.core.state.app_state")
    app_state_patch.start().fleet = fleet
    try:
        result = await service.deploy_version(
            user=h._user(),
            strategy_id=h.STRATEGY_ID,
            version="v3.0",
            environment=environment,
            binding_request=db.BindingRequest.from_payload(payload),
        )
    finally:
        app_state_patch.stop()
        for p in reversed(patches):
            p.stop()
    return result, reserve, release


def _never_called():
    raise AssertionError("the trusted writer must not be consulted for this deploy")


@pytest.mark.parametrize(
    "env, code",
    [
        ({"VYOMQUANT_MODE": "paper"}, "LIVE_TRADING_DISABLED_BY_PLATFORM_MODE"),
        ({"VYOMQUANT_MODE": "live", "VYOMQUANT_ENABLE_LIVE_TRADING": "false"}, "LIVE_TRADING_NOT_ENABLED"),
        (
            {
                "VYOMQUANT_MODE": "live",
                "VYOMQUANT_ENABLE_LIVE_TRADING": "true",
                "VYOMQUANT_LIVE_TENANT_ALLOWLIST": "someone-else",
            },
            "LIVE_TENANT_NOT_ALLOWLISTED",
        ),
    ],
)
async def test_a_live_deploy_is_refused_unless_the_platform_is_live_for_this_tenant(
    seeded_universe, env, code
):
    user_sb = h._Supabase(h._default_rows())
    with pytest.raises(db.DeployRejected) as caught:
        await _deploy(user_sb, payload=LIVE_PAYLOAD, env=env, service_client=_never_called)
    assert caught.value.code == code
    assert caught.value.http_status == 403
    assert user_sb.inserted("strategy_deployments") == []


async def test_the_platform_gate_runs_before_any_quota_is_reserved(seeded_universe):
    user_sb = h._Supabase(h._default_rows())
    reserve = AsyncMock(return_value=(True, 1, 10))
    with pytest.raises(db.DeployRejected):
        await _deploy(
            user_sb, payload=LIVE_PAYLOAD, env={"VYOMQUANT_MODE": "paper"},
            service_client=_never_called, reserve=reserve,
        )
    reserve.assert_not_awaited()


async def test_the_live_row_is_written_by_the_trusted_client_not_the_callers(seeded_universe):
    user_sb = h._Supabase(h._default_rows())
    trusted = h._Supabase(h._default_rows())
    result, _reserve, release = await _deploy(
        user_sb, payload=LIVE_PAYLOAD, env=LIVE_ENV, service_client=lambda: trusted
    )
    assert user_sb.inserted("strategy_deployments") == []
    (row,) = trusted.inserted("strategy_deployments")
    assert row["mode"] == "live"
    assert row["user_id"] == h.USER_ID
    assert row["exchange_account_id"] == h.ACCOUNT_ID
    assert result["binding"]["binding_stored"] is True
    release.assert_not_awaited()


async def test_a_paper_deploy_still_uses_the_callers_client(seeded_universe):
    user_sb = h._Supabase(h._default_rows())
    await _deploy(
        user_sb, payload={"mode": "paper"}, env={"VYOMQUANT_MODE": "paper"},
        service_client=_never_called, environment="paper",
    )
    (row,) = user_sb.inserted("strategy_deployments")
    assert row["mode"] == "paper"


@pytest.mark.parametrize("unavailable", [lambda: None, _never_called], ids=["none", "raises"])
async def test_no_trusted_writer_is_a_503_and_releases_the_quota(seeded_universe, unavailable):
    user_sb = h._Supabase(h._default_rows())
    release = AsyncMock(return_value=None)
    with pytest.raises(db.DeployRejected) as caught:
        await _deploy(
            user_sb, payload=LIVE_PAYLOAD, env=LIVE_ENV, service_client=unavailable,
            release=release,
        )
    assert caught.value.code == "LIVE_DEPLOY_WRITER_UNAVAILABLE"
    assert caught.value.http_status == 503
    # The quota this attempt reserved is given back: nothing was deployed.
    release.assert_awaited_once()
    # Never a fallback to the caller's client.
    assert user_sb.inserted("strategy_deployments") == []


def test_the_browser_role_guard_is_still_in_force():
    sql = GUARD_MIGRATION.read_text(encoding="utf-8")
    assert "may only create paper deployments" in sql
    assert "current_user NOT IN ('anon', 'authenticated')" in sql


@pytest.fixture
def seeded_universe():
    from backend_app.backend import asset_universe as au

    au.reset_asset_universe_state_for_tests()
    au._local_universe = h._universe()  # noqa: SLF001
    yield au._local_universe  # noqa: SLF001
    au.reset_asset_universe_state_for_tests()
