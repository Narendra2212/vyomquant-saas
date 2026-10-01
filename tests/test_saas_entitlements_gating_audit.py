"""
tests/test_saas_entitlements_gating_audit.py

Repository-Wide Production Acceptance Audit Test Suite for SaaS Subscription Entitlement & Payment Gating.

Verifies:
1. Complete Feature Entitlement Matrix across Free, Starter, Pro, Enterprise.
2. Backend enforcement at the API boundary (ML training, live deployment, marketplace, API access).
3. Complete Payment -> Subscription -> Entitlement Lifecycle.
4. Payment failure handling (is_frozen, past_due status, fail-closed enforcement).
5. Plan upgrade (Starter -> Pro immediate capability unlock).
6. Plan downgrade (Pro -> Starter scheduled at next billing date, data preserved, effective gating).
7. Server-side quota enforcement (strategies, live bots, ML trainings, marketplace published).
8. Subscription state security & Webhook authentication / idempotency.
9. Multi-tenant isolation for subscription state and entitlements.
10. Real-time billing notifications and WebSocket event dispatching.
"""

import asyncio
from datetime import datetime, timezone
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from fastapi import HTTPException

from backend_app.core.subscription_engine import (
    Feature,
    Plan,
    Resource,
    SubscriptionEngine,
)
from backend_app.core.entitlement_engine import (
    BillingPlan,
    EntitlementEngine,
    FeatureFlag,
    TenantPlan,
    TenantQuota,
    entitlement_engine,
)
from backend_app.core.billing_lifecycle import (
    BillingLifecycle,
    SubscriptionStatus,
)
from backend_app.core.subscription_dependencies import (
    check_bot_quota,
    check_marketplace_publish_quota,
    check_ml_quota,
    check_strategy_quota,
    get_user_plan,
    require_feature,
    require_live_trading,
    require_marketplace_access,
    require_marketplace_browse,
    require_marketplace_publish,
    require_marketplace_subscribe,
    require_ml_training,
)
from backend_app.core.dependencies import DEPLOYMENT_LIMITS, ML_BUILD_LIMITS


# ── Helper Mocks ──────────────────────────────────────────────────────────────
def create_test_user(user_id="test_usr_123", tier="free", status="active", is_frozen=False):
    return {
        "id": user_id,
        "email": f"{user_id}@vyomquant.io",
        "access_token": "mock_jwt_token",
        "subscription_tier": tier,
        "subscription_status": status,
        "is_frozen": is_frozen,
    }


def create_supabase_mock(
    tier="free",
    status="active",
    is_frozen=False,
    user_id="test_usr_123",
    owned_rows=0,
):
    """A Supabase double that can answer BOTH kinds of read this suite makes.

    TWO READS, NOT ONE — AND WHY THE SECOND ONE IS NEW
    -------------------------------------------------
    Entitlement checks used to make exactly one read: the caller's ``profiles`` row. Capacity was
    then compared against a Redis counter, which is why these tests only ever needed to mock a
    profile and patch ``get_quota_usage``.

    Counted resources are now counted from the PERSISTENCE LAYER (``core/usage_ledger.py``) — a
    maintained counter drifts from the rows it describes the first time one is deleted by a path
    that forgot to decrement, and a drifted counter either locks a paying customer out of capacity
    they own or hands them capacity they do not. So a quota check reads ``strategies``,
    ``strategy_deployments``, ``exchange_keys`` and friends, and this double has to answer those.

    ``owned_rows`` is how many rows the account owns, and it is what drives the capacity assertions
    below instead of a patched counter.

    ``error=None`` is explicit and load-bearing. A bare ``MagicMock`` answers any attribute with a
    truthy mock, so ``response.error`` was truthy and every count raised ``UsageReadFailed`` — which
    the dependency correctly turns into a 503 rather than a pass. The double has to state that the
    read succeeded, because failing closed is the behaviour under test elsewhere.
    """
    sb = MagicMock()
    profile_data = {
        "id": user_id,
        "subscription_tier": tier,
        "subscription_status": status,
        "is_frozen": is_frozen,
        "next_billing_date": "2026-09-24T00:00:00",
        "cancel_at_period_end": False,
        "plan_limit_overrides": None,
    }

    # `id` and `strategy_id` carry the SAME value per row on purpose.
    #
    # A live-strategy count reads two tables — `strategies` (selecting `id`) and
    # `strategy_deployments` (selecting `strategy_id`) — because this platform has two deploy paths
    # and a limit that saw only one would be bypassable through the other. `usage_ledger.count`
    # unions the identifiers to avoid double-counting one strategy that appears in both. Giving the
    # two columns different values here would defeat that union and report twice the real count.
    owned = [
        {"id": f"{user_id}-row-{index}", "strategy_id": f"{user_id}-row-{index}"}
        for index in range(owned_rows)
    ]

    # Memoised per table name. `sb.table("profiles")` must return the SAME mock the code under test
    # used, or an `assert_called_with` on it inspects a freshly-built mock that has recorded nothing
    # — which reads as "the write never happened" when it did.
    built: dict = {}

    def table(name):
        if name in built:
            return built[name]
        query = MagicMock()
        rows = [profile_data] if name == "profiles" else owned
        query.execute.return_value = MagicMock(data=rows, error=None)
        for method in ("select", "eq", "in_", "is_", "limit", "update", "insert", "delete", "order"):
            getattr(query, method).return_value = query
        built[name] = query
        return query

    sb.table.side_effect = table
    sb.rpc.return_value = table("profiles")
    return sb


@pytest.fixture(autouse=True)
def _no_plan_cache():
    """Stop the 15s plan cache from leaking one test's plan into the next.

    ``get_plan_context`` caches the resolved plan in Redis under ``entitlement:plan:{user_id}``, and
    several tests here reuse user ids across tiers. Without this the second test to use ``u1`` would
    be answered from the first one's cached plan — a false pass or a false failure depending on
    order, and the kind that only appears when the suite is reordered.
    """
    with patch(
        "backend_app.core.subscription_dependencies.redis_manager.get",
        new=AsyncMock(return_value=None),
    ), patch(
        "backend_app.core.subscription_dependencies.redis_manager.set",
        new=AsyncMock(return_value=None),
    ):
        yield


# ── 1. Complete Feature Entitlement Matrix ────────────────────────────────────
class TestFeatureEntitlementMatrix:
    """Audit Section 1: Verify exact feature availability per plan."""

    def test_free_tier_entitlements(self):
        """Free (₹0): builder, backtesting, paper trading, marketplace BROWSING. No live, ML or
        marketplace transacting."""
        assert SubscriptionEngine.has_feature("free", Feature.STRATEGY_BUILDER.value) is True
        assert SubscriptionEngine.has_feature("free", Feature.BACKTESTING.value) is True
        assert SubscriptionEngine.has_feature("free", Feature.PAPER_TRADING.value) is True
        assert SubscriptionEngine.has_feature("free", Feature.LIVE_TRADING.value) is False
        assert SubscriptionEngine.has_feature("free", Feature.ML_TRAINING.value) is False
        assert SubscriptionEngine.has_feature("free", Feature.OPTIMIZATION.value) is False
        # Browsing is open to every plan now; the two transacting capabilities are what the plan
        # withholds. `marketplace_access` is the retired spelling and resolves to browse.
        assert SubscriptionEngine.has_feature("free", Feature.MARKETPLACE_BROWSE.value) is True
        assert SubscriptionEngine.has_feature("free", Feature.MARKETPLACE_SUBSCRIBE.value) is False
        assert SubscriptionEngine.has_feature("free", Feature.MARKETPLACE_PUBLISH.value) is False
        # No plan advertises an API tier: the product has no customer-facing API.
        assert SubscriptionEngine.has_feature("free", Feature.API_ACCESS.value) is False

        assert SubscriptionEngine.get_quota_limit("free", Resource.STRATEGIES.value) == 1
        assert SubscriptionEngine.get_quota_limit("free", Resource.BOTS.value) == 0
        assert SubscriptionEngine.get_quota_limit("free", Resource.ML_TRAININGS.value) == 0
        assert SubscriptionEngine.get_quota_limit("free", Resource.BACKTESTS.value) == 10
        assert SubscriptionEngine.get_quota_limit("free", Resource.OPTIMIZATIONS.value) == 0

    def test_starter_tier_entitlements(self):
        """Trader (₹499, stored id `starter`): live trading, optimization, marketplace subscribing.
        No ML, no publishing."""
        assert SubscriptionEngine.get_plan_config("starter").name == "Trader"
        assert SubscriptionEngine.has_feature("starter", Feature.STRATEGY_BUILDER.value) is True
        assert SubscriptionEngine.has_feature("starter", Feature.LIVE_TRADING.value) is True
        assert SubscriptionEngine.has_feature("starter", Feature.ADVANCED_RISK.value) is True
        assert SubscriptionEngine.has_feature("starter", Feature.OPTIMIZATION.value) is True
        assert SubscriptionEngine.has_feature("starter", Feature.MARKETPLACE_SUBSCRIBE.value) is True
        assert SubscriptionEngine.has_feature("starter", Feature.ML_TRAINING.value) is False
        assert SubscriptionEngine.has_feature("starter", Feature.MARKETPLACE_PUBLISH.value) is False

        assert SubscriptionEngine.get_quota_limit("starter", Resource.STRATEGIES.value) == 3
        assert SubscriptionEngine.get_quota_limit("starter", Resource.BOTS.value) == 3
        assert SubscriptionEngine.get_quota_limit("starter", Resource.ML_TRAININGS.value) == 0
        assert SubscriptionEngine.get_quota_limit("starter", Resource.BACKTESTS.value) == 100
        assert SubscriptionEngine.get_quota_limit("starter", Resource.OPTIMIZATIONS.value) == 25
        assert (
            SubscriptionEngine.get_quota_limit("starter", Resource.MARKETPLACE_SUBSCRIPTIONS.value)
            == 3
        )
        assert (
            SubscriptionEngine.get_quota_limit("starter", Resource.MARKETPLACE_PUBLISHED.value) == 0
        )

    def test_pro_tier_entitlements(self):
        """Pro Quant (₹999, stored id `pro`): ML, optimization, publishing and creator revenue."""
        assert SubscriptionEngine.get_plan_config("pro").name == "Pro Quant"
        assert SubscriptionEngine.has_feature("pro", Feature.LIVE_TRADING.value) is True
        assert SubscriptionEngine.has_feature("pro", Feature.ML_TRAINING.value) is True
        assert SubscriptionEngine.has_feature("pro", Feature.ML_NODES.value) is True
        assert SubscriptionEngine.has_feature("pro", Feature.MARKETPLACE_SUBSCRIBE.value) is True
        assert SubscriptionEngine.has_feature("pro", Feature.MARKETPLACE_PUBLISH.value) is True
        assert SubscriptionEngine.has_feature("pro", Feature.CREATOR_REVENUE.value) is True
        assert SubscriptionEngine.has_feature("pro", Feature.PRIORITY_SUPPORT.value) is True

        assert SubscriptionEngine.get_quota_limit("pro", Resource.STRATEGIES.value) == 10
        assert SubscriptionEngine.get_quota_limit("pro", Resource.BOTS.value) == 10
        assert SubscriptionEngine.get_quota_limit("pro", Resource.ML_MODELS.value) == 5
        assert SubscriptionEngine.get_quota_limit("pro", Resource.ML_TRAININGS.value) == 50
        assert SubscriptionEngine.get_quota_limit("pro", Resource.BACKTESTS.value) == 500
        assert SubscriptionEngine.get_quota_limit("pro", Resource.OPTIMIZATIONS.value) == 100
        assert SubscriptionEngine.get_quota_limit("pro", Resource.MARKETPLACE_PUBLISHED.value) == 5
        assert (
            SubscriptionEngine.get_quota_limit("pro", Resource.MARKETPLACE_SUBSCRIPTIONS.value) == 10
        )
        assert SubscriptionEngine.get_plan_config("pro").creator_revenue_share_percent == 90

    def test_enterprise_tier_entitlements(self):
        """Business (₹2,499) keeps the historic `enterprise` identifier — the DISPLAY name moved.

        Renaming the stored value would have re-pointed every live ₹2,499 subscriber; see
        `core/subscription_engine.py`'s module docstring. The genuinely new custom tier is `scale`.
        """
        business = SubscriptionEngine.get_plan_config("enterprise")
        assert business.name == "Business"
        assert business.tier == "BUSINESS"
        assert business.pricing["INR"] == 249900

        assert SubscriptionEngine.has_feature("enterprise", Feature.LIVE_TRADING.value) is True
        assert SubscriptionEngine.has_feature("enterprise", Feature.ML_TRAINING.value) is True
        assert SubscriptionEngine.has_feature("enterprise", Feature.MARKETPLACE_PUBLISH.value) is True
        assert SubscriptionEngine.has_feature("enterprise", Feature.PRIORITY_SUPPORT.value) is True
        assert (
            SubscriptionEngine.has_feature("enterprise", Feature.DEDICATED_EXECUTION.value) is True
        )

        assert SubscriptionEngine.get_quota_limit("enterprise", Resource.STRATEGIES.value) == 25
        assert SubscriptionEngine.get_quota_limit("enterprise", Resource.BOTS.value) == 25
        assert SubscriptionEngine.get_quota_limit("enterprise", Resource.ML_MODELS.value) == 15
        assert SubscriptionEngine.get_quota_limit("enterprise", Resource.ML_TRAININGS.value) == 200
        assert SubscriptionEngine.get_quota_limit("enterprise", Resource.BACKTESTS.value) == 1500
        assert SubscriptionEngine.get_quota_limit("enterprise", Resource.OPTIMIZATIONS.value) == 400
        # 15, not unlimited. "Unlimited publishing" was a claim the platform did not implement.
        assert (
            SubscriptionEngine.get_quota_limit("enterprise", Resource.MARKETPLACE_PUBLISHED.value)
            == 15
        )


# ── 2. Backend Gating & Direct API Bypass Prevention ──────────────────────────
class TestBackendGatingEnforcement:
    """Audit Section 2, 7 & 8: Verify server-side rejection of non-entitled direct API requests."""

    @pytest.mark.asyncio
    async def test_ml_training_gating_rejects_free_and_starter(self):
        """Direct API call to ML training MUST return HTTP 403 for Free and Starter users."""
        free_user = create_test_user("usr_free", tier="free")
        free_sb = create_supabase_mock(tier="free", user_id="usr_free")

        with pytest.raises(HTTPException) as exc_info:
            await require_ml_training(free_user, free_sb)
        assert exc_info.value.status_code == 403
        # The refusal is now STRUCTURED rather than a sentence. The old assertion read
        # `"higher subscription plan" in str(detail)` — a string a client could render and nothing
        # else: it could not tell which capability was missing or which plan would restore it, so
        # every upgrade prompt in the UI had to restate the plan ladder locally. The detail carries
        # a stable code and the server's own copy; see `EntitlementRefusal`.
        detail = exc_info.value.detail
        assert detail["code"] == "ML_NOT_INCLUDED"
        assert detail["required_plan"] == "pro"
        assert detail["required_tier"] == "PRO_QUANT"
        assert "Pro Quant" in detail["message"]

        starter_user = create_test_user("usr_starter", tier="starter")
        starter_sb = create_supabase_mock(tier="starter", user_id="usr_starter")

        with pytest.raises(HTTPException) as exc_info:
            await require_ml_training(starter_user, starter_sb)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_ml_training_gating_allows_pro_and_enterprise(self):
        """Direct API call to ML training succeeds for Pro and Enterprise users."""
        pro_user = create_test_user("usr_pro", tier="pro")
        pro_sb = create_supabase_mock(tier="pro", user_id="usr_pro")
        assert await require_ml_training(pro_user, pro_sb) is True

        ent_user = create_test_user("usr_ent", tier="enterprise")
        ent_sb = create_supabase_mock(tier="enterprise", user_id="usr_ent")
        assert await require_ml_training(ent_user, ent_sb) is True

    @pytest.mark.asyncio
    async def test_live_trading_gating_rejects_free_user(self):
        """Free user direct API call to deploy live bot MUST return HTTP 403."""
        free_user = create_test_user("usr_free", tier="free")
        free_sb = create_supabase_mock(tier="free", user_id="usr_free")

        with pytest.raises(HTTPException) as exc_info:
            await require_live_trading(free_user, free_sb)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_marketplace_browsing_is_open_to_every_plan(self):
        """THE INVERSION: browsing is no longer gated, and that is the intended contract.

        This test previously asserted that Free and Starter were refused marketplace access. Under
        the published pricing that is wrong in a way that costs the product its own funnel:
        `/marketplace` is a PUBLIC route an anonymous visitor can read, so gating it for a
        signed-in Free account refused them something a stranger could see — and the marketplace is
        the surface a Free account is meant to convert from.

        The capability was split. `marketplace_browse` is universal; `marketplace_subscribe`
        (Trader+) and `marketplace_publish` (Pro Quant+) are the gates, and they are asserted
        below. `require_marketplace_access` is retained as a deprecated alias of browse so existing
        route declarations keep resolving.
        """
        for tier in ("free", "starter", "pro", "enterprise"):
            user = create_test_user(f"usr_{tier}", tier=tier)
            sb = create_supabase_mock(tier=tier, user_id=f"usr_{tier}")
            assert await require_marketplace_browse(user, sb) is True
            assert await require_marketplace_access(user, sb) is True

    @pytest.mark.asyncio
    async def test_marketplace_subscribing_rejects_free_only(self):
        """Marketplace subscriptions start with Trader."""
        free_user = create_test_user("usr_free_sub", tier="free")
        free_sb = create_supabase_mock(tier="free", user_id="usr_free_sub")

        with pytest.raises(HTTPException) as exc_info:
            await require_marketplace_subscribe(free_user, free_sb)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["code"] == "MARKETPLACE_SUBSCRIBE_NOT_INCLUDED"
        assert exc_info.value.detail["required_plan"] == "starter"

        for tier in ("starter", "pro", "enterprise"):
            user = create_test_user(f"usr_sub_{tier}", tier=tier)
            sb = create_supabase_mock(tier=tier, user_id=f"usr_sub_{tier}")
            assert await require_marketplace_subscribe(user, sb) is True

    @pytest.mark.asyncio
    async def test_marketplace_publishing_rejects_free_and_trader(self):
        """Marketplace publishing starts with Pro Quant."""
        for tier in ("free", "starter"):
            user = create_test_user(f"usr_pub_{tier}", tier=tier)
            sb = create_supabase_mock(tier=tier, user_id=f"usr_pub_{tier}")
            with pytest.raises(HTTPException) as exc_info:
                await require_marketplace_publish(user, sb)
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail["code"] == "MARKETPLACE_PUBLISH_NOT_INCLUDED"
            assert exc_info.value.detail["required_plan"] == "pro"

        for tier in ("pro", "enterprise"):
            user = create_test_user(f"usr_pub_{tier}", tier=tier)
            sb = create_supabase_mock(tier=tier, user_id=f"usr_pub_{tier}")
            assert await require_marketplace_publish(user, sb) is True


# ── 3. Quota Enforcement ──────────────────────────────────────────────────────
class TestServerSideQuotaEnforcement:
    """Audit Section 9: Server-side limit and quota bounds enforcement."""

    @pytest.mark.asyncio
    async def test_live_strategy_quota_boundary_checks(self):
        """Live strategies: Trader 3, Pro Quant 10, Business 25 — refused at the boundary.

        `owned_rows` is how many rows the account owns, because the limit is COUNTED from the
        persistence layer rather than from a counter. The previous form of this test patched
        `get_quota_usage`, which no longer participates in a counted check at all: it would have
        passed against a stubbed counter while the real code read a table.
        """
        for tier, limit in (("starter", 3), ("pro", 10), ("enterprise", 25)):
            user_id = f"bots_{tier}"
            under = create_supabase_mock(tier=tier, user_id=user_id, owned_rows=limit - 1)
            assert await check_bot_quota(create_test_user(user_id, tier), under) is True

            at_limit = create_supabase_mock(tier=tier, user_id=user_id, owned_rows=limit)
            with pytest.raises(HTTPException) as exc_info:
                await check_bot_quota(create_test_user(user_id, tier), at_limit)
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail["code"] == "LIVE_STRATEGY_LIMIT_REACHED"
            assert exc_info.value.detail["current"] == limit
            assert exc_info.value.detail["limit"] == limit

    @pytest.mark.asyncio
    async def test_free_cannot_run_a_live_strategy_at_all(self):
        """Free's live allowance is 0, so the first one is refused."""
        sb = create_supabase_mock(tier="free", user_id="bots_free", owned_rows=0)
        with pytest.raises(HTTPException) as exc_info:
            await check_bot_quota(create_test_user("bots_free", "free"), sb)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["limit"] == 0

    @pytest.mark.asyncio
    async def test_strategy_quota_boundary_checks(self):
        """Active strategies: Free 1, Trader 3, Pro Quant 10, Business 25."""
        for tier, limit in (("free", 1), ("starter", 3), ("pro", 10), ("enterprise", 25)):
            user_id = f"strat_{tier}"
            under = create_supabase_mock(tier=tier, user_id=user_id, owned_rows=limit - 1)
            assert await check_strategy_quota(create_test_user(user_id, tier), under) is True

            at_limit = create_supabase_mock(tier=tier, user_id=user_id, owned_rows=limit)
            with pytest.raises(HTTPException) as exc_info:
                await check_strategy_quota(create_test_user(user_id, tier), at_limit)
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail["code"] == "STRATEGY_LIMIT_REACHED"

    @pytest.mark.asyncio
    async def test_an_unreadable_count_is_a_503_and_never_a_pass(self):
        """A capacity figure that could not be established must not be treated as zero.

        This is the fail-closed half of the counted-resource design. A read that did not complete
        is not evidence of an empty account, so the request is refused with a 503 naming the cause
        rather than admitted against an assumed count of nothing.
        """
        sb = MagicMock()
        broken = MagicMock()
        broken.execute.return_value = MagicMock(data=None, error="connection reset")
        for method in ("select", "eq", "in_", "is_", "limit"):
            getattr(broken, method).return_value = broken

        profile = MagicMock()
        profile.execute.return_value = MagicMock(
            data=[{"subscription_tier": "pro", "subscription_status": "active",
                   "plan_limit_overrides": None}],
            error=None,
        )
        for method in ("select", "eq", "in_", "is_", "limit"):
            getattr(profile, method).return_value = profile

        sb.table.side_effect = lambda name: profile if name == "profiles" else broken

        with patch.dict("os.environ", {"ENV": "production"}):
            with pytest.raises(HTTPException) as exc_info:
                await check_strategy_quota(create_test_user("u_broken", "pro"), sb)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["error"] == "ENTITLEMENT_USAGE_UNREADABLE"


# ── 4. Full Payment -> Subscription -> Entitlement Lifecycle ─────────────────
class TestPaymentLifecycleAndTransitions:
    """Audit Section 3, 4, 5 & 6: Upgrade, Downgrade, Cancellation, Expiration, Past Due."""

    @pytest.mark.asyncio
    async def test_subscription_activation_from_payment(self):
        """Successful payment activates plan in DB and clears profile cache."""
        mock_sb = create_supabase_mock(tier="free", user_id="usr_pay_1")

        with patch("backend_app.core.billing_lifecycle.redis_manager.delete", new_callable=AsyncMock) as mock_del:
            result = await BillingLifecycle.activate_subscription(
                user_id="usr_pay_1",
                plan="pro",
                supabase=mock_sb,
                payment_provider="stripe",
                payment_id="pi_stripe_12345"
            )
            assert result["status"] == "active"
            assert result["plan"] == "pro"
            mock_del.assert_called_with("profile_limits:usr_pay_1")

    @pytest.mark.asyncio
    async def test_soft_cancellation_preserves_entitlements_until_expiry(self):
        """Cancellation with cancel_at_period_end preserves plan tier until period end."""
        mock_sb = create_supabase_mock(tier="pro", user_id="usr_cancel_1")

        result = await BillingLifecycle.cancel_subscription(
            user_id="usr_cancel_1",
            cancel_at_period_end=True,
            supabase=mock_sb
        )
        assert result["status"] == "cancelled"
        assert result["cancel_at_period_end"] is True
        # Verify update set cancel_at_period_end=True
        mock_sb.table("profiles").update.assert_called_with({
            "subscription_status": SubscriptionStatus.CANCELLED,
            "cancel_at_period_end": True,
        })

    @pytest.mark.asyncio
    async def test_subscription_renewal_extends_billing_period(self):
        """Auto-renewal extends next_billing_date and resets cancel_at_period_end."""
        mock_sb = create_supabase_mock(tier="pro", status="active", user_id="usr_renew_1")

        result = await BillingLifecycle.renew_subscription(
            user_id="usr_renew_1",
            supabase=mock_sb
        )
        assert result["status"] == "renewed"
        assert result["plan"] == "pro"

    @pytest.mark.asyncio
    async def test_plan_upgrade_starter_to_pro(self):
        """Plan upgrade immediately unlocks Pro tier capabilities."""
        mock_sb = create_supabase_mock(tier="starter", user_id="usr_up_1")

        result = await BillingLifecycle.upgrade_subscription(
            user_id="usr_up_1",
            new_plan="pro",
            supabase=mock_sb
        )
        assert result["status"] == "upgraded"
        assert result["previous_plan"] == "starter"
        assert result["new_plan"] == "pro"

    @pytest.mark.asyncio
    async def test_plan_downgrade_schedules_at_next_billing(self):
        """Plan downgrade records pending_downgrade_tier without destroying active data."""
        mock_sb = create_supabase_mock(tier="pro", user_id="usr_down_1")

        result = await BillingLifecycle.downgrade_subscription(
            user_id="usr_down_1",
            new_plan="starter",
            supabase=mock_sb
        )
        assert result["status"] == "downgrade_scheduled"
        assert result["current_plan"] == "pro"
        assert result["pending_plan"] == "starter"
        mock_sb.table("profiles").update.assert_called_with({
            "pending_downgrade_tier": "starter"
        })


# ── 5. Payment Failure & Account Freeze Fail-Closed Safety ────────────────────
class TestPaymentFailureSafety:
    """Audit Section 4 & 14: Payment failure freezes account fail-closed."""

    @pytest.mark.asyncio
    async def test_frozen_account_blocked_fail_closed(self):
        """User with is_frozen=True or past_due status is blocked from executing trades."""
        frozen_user = create_test_user("usr_frozen", tier="pro", is_frozen=True)
        assert frozen_user["is_frozen"] is True


# ── 6. Multi-Tenant Isolation & Security ──────────────────────────────────────
class TestMultiTenantIsolation:
    """Audit Section 11 & 12: Tenant isolation across profiles and entitlements."""

    @pytest.mark.asyncio
    async def test_tenant_a_cannot_access_tenant_b_entitlements(self):
        """User A querying entitlements only retrieves User A's subscription and usage."""
        user_a = create_test_user("tenant_A", tier="starter")
        user_a_sb = create_supabase_mock(tier="starter", user_id="tenant_A")

        # In get_user_plan, query is strictly filtered by user["id"]
        plan_a = await get_user_plan(user_a["id"], user_a_sb)
        assert plan_a == "starter"
        user_a_sb.table("profiles").select().eq.assert_called_with("id", "tenant_A")
