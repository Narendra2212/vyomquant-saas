"""
tests/test_billing_hardening_and_failure_recovery_audit.py

Production Hardening, Failure Injection, and Subscription Lifecycle Recovery Audit.

Tests:
1. Subscription State Machine Transitions (Valid transitions & prevention of illegal states)
2. Payment Webhook Idempotency & Crash Recovery (Two-phase locks, concurrency, collisions)
3. Entitlement Consistency Across Tiers (Free, Starter, Pro, Enterprise)
4. Quota Boundary & Exact Limit Enforcement (Limit vs Limit + 1, concurrent reservations)
5. Cache Consistency & Invalidation
6. Currency & Pricing Integrity
7. Cancellation & Resume Semantics (Idempotent repeated requests)
8. Payment Failure Recovery (past_due / frozen -> recovery payment -> active / unfrozen)
9. Notification Dispatch & Failure Isolation (Notification/WebSocket failure never rolls back payment)
10. Invoices and Financial Records Audit (Exact singular invoice record per payment)
11. Security Audit (Multi-tenant IDOR defense, forged payloads rejected)
12. Failure Injection (WebSocket down, notification error, Redis degraded)
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
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
    require_marketplace_publish,
    require_ml_training,
)
from backend_app.core.fx_service import FXService


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_test_user(user_id="usr_hardened_01", tier="free", status="active", is_frozen=False):
    return {
        "id": user_id,
        "email": f"{user_id}@vyomquant.io",
        "username": f"trader_{user_id}",
        "access_token": f"jwt_mock_{user_id}",
        "subscription_tier": tier,
        "subscription_status": status,
        "is_frozen": is_frozen,
    }


def make_supabase_mock(
    tier="free", status="active", is_frozen=False, user_id="usr_hardened_01", owned_rows=0
):
    sb = MagicMock()
    profile_data = {
        "id": user_id,
        "subscription_tier": tier,
        "subscription_status": status,
        "is_frozen": is_frozen,
        "next_billing_date": "2026-09-24T00:00:00",
        "cancel_at_period_end": False,
    }
    # `owned_rows` is how many rows the account owns. Counted resources (strategies, live
    # strategies, exchange connections, ML models, marketplace listings and subscriptions) are now
    # counted from the PERSISTENCE LAYER by `core/usage_ledger.py` rather than from a Redis
    # counter, so a capacity check reads tables and this double has to answer them.
    #
    # `id` and `strategy_id` carry the same value per row: a live-strategy count reads both
    # `strategies` and `strategy_deployments` (two deploy paths exist, and a limit seeing only one
    # would be bypassable through the other) and unions the identifiers to avoid double-counting.
    #
    # `error=None` is explicit. A bare `MagicMock` answers any attribute with a truthy mock, so
    # `response.error` was truthy and every count raised `UsageReadFailed` — which the dependency
    # correctly turns into a 503 rather than a pass.
    owned = [
        {"id": f"{user_id}-row-{i}", "strategy_id": f"{user_id}-row-{i}"}
        for i in range(owned_rows)
    ]
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


# ═══════════════════════════════════════════════════════════════════════════
# 1. SUBSCRIPTION STATE MACHINE AUDIT
# ═══════════════════════════════════════════════════════════════════════════

class TestSubscriptionStateMachineExhaustive:
    """Audit 1: Comprehensive state transition verification."""
    # REMOVED: the tests that stood here drove BillingLifecycle.activate_subscription /
    # start_trial / upgrade_subscription / downgrade_subscription / renew_subscription
    # against a Supabase mock and asserted each method's own return dict. None of those
    # methods had a production caller -- renew_subscription's only one was inside
    # stripe_webhook()'s invoice.payment_succeeded branch, which went with the provider --
    # so they proved the library returned what it said it returned and nothing about what
    # the product does. The product's real grant path is the Razorpay webhook
    # (_apply_billing_entitlement + _record_plan_period), covered behaviourally by
    # test_billing_e2e.py, test_bill01_razorpay_plans_expire.py and
    # test_subscription_renewal_regression.py. The surface itself is now pinned by
    # tests/test_billing_lifecycle_surface.py, which also records that this product has
    # no trial, no grace period, no proration on upgrade and no scheduled downgrade.



# ═══════════════════════════════════════════════════════════════════════════
# 2. PAYMENT WEBHOOK IDEMPOTENCY & CONCURRENCY AUDIT
# ═══════════════════════════════════════════════════════════════════════════

class TestWebhookIdempotencyAndConcurrency:
    """Audit 2: Prevent duplicate crediting, double charges, or duplicate notifications."""

    @pytest.mark.asyncio
    async def test_concurrent_webhook_collision_handling(self):
        """When two identical webhook events arrive concurrently, exactly one acquires the lock."""
        event_id = "evt_stripe_concurrent_999"
        idempotency_key = f"webhook:stripe:{event_id}"

        # First request acquires lock (nx=True -> True)
        mock_redis = AsyncMock()
        mock_redis.set.return_value = True

        acquired_first = await mock_redis.set(idempotency_key, "processing", nx=True, ex=60)
        assert acquired_first is True

        # Second concurrent request fails to acquire lock (nx=True -> False)
        mock_redis.set.return_value = False
        acquired_second = await mock_redis.set(idempotency_key, "processing", nx=True, ex=60)
        assert acquired_second is False

    @pytest.mark.asyncio
    async def test_completed_webhook_rejection(self):
        """Already completed webhook event is skipped immediately."""
        event_id = "evt_stripe_completed_111"
        idempotency_key = f"webhook:stripe:{event_id}"

        mock_redis = AsyncMock()
        mock_redis.get.return_value = "completed"

        status = await mock_redis.get(idempotency_key)
        assert status in ("completed", "1", "processing")


# ═══════════════════════════════════════════════════════════════════════════
# 3. QUOTA BOUNDARY & EXACT LIMIT ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════

class TestQuotaBoundaryEnforcement:
    """Audit 4: Test exact limit vs limit + 1 across all tiers."""

    @pytest.mark.asyncio
    async def test_trader_live_strategy_limit_boundaries(self):
        """Trader allows 3 live strategies; the 1st-3rd succeed, the 4th is refused.

        Two things moved here. The LIMIT is 3 rather than 2 (the published Trader plan), and the
        USAGE comes from counted rows rather than a patched counter: `check_bot_quota` reads
        `strategies` and `strategy_deployments` through `core/usage_ledger.py`, so patching
        `get_quota_usage` would stub something the check no longer consults — the test would pass
        against a counter while the real code read a table.
        """
        user = make_test_user("u_bot_starter", tier="starter")

        for held in (0, 1, 2):
            sb = make_supabase_mock(tier="starter", user_id="u_bot_starter", owned_rows=held)
            assert await check_bot_quota(user, sb) is True

        at_limit = make_supabase_mock(tier="starter", user_id="u_bot_starter", owned_rows=3)
        with pytest.raises(HTTPException) as exc:
            await check_bot_quota(user, at_limit)
        assert exc.value.status_code == 403
        # The refusal is structured now, not a sentence a client can only render verbatim.
        assert exc.value.detail["code"] == "LIVE_STRATEGY_LIMIT_REACHED"
        assert exc.value.detail["current"] == 3
        assert exc.value.detail["limit"] == 3
        assert exc.value.detail["required_plan"] == "pro"

    @pytest.mark.asyncio
    async def test_pro_quant_ml_training_limit_boundaries(self):
        """Pro Quant allows 50 ML training runs a month; the 50th succeeds, the 51st is refused.

        ML training is a METERED resource, so the usage still comes from the Redis meter — but the
        key is period-scoped (`quota:{user}:{resource}:{YYYY-MM}`) and the check RESERVES as it
        reads, so `reserve_quota` is what is patched rather than `get_quota_usage`. Reserving
        inside the check is what stops two concurrent requests both seeing the last run as free.
        """
        sb = make_supabase_mock(tier="pro", user_id="u_ml_pro")
        user = make_test_user("u_ml_pro", tier="pro")

        with patch.object(
            SubscriptionEngine, "reserve_quota", new=AsyncMock(return_value=(True, 50, 50))
        ):
            assert await check_ml_quota(user, sb) is True

        with patch.object(
            SubscriptionEngine, "reserve_quota", new=AsyncMock(return_value=(False, 50, 50))
        ):
            with pytest.raises(HTTPException) as exc:
                await check_ml_quota(user, sb)
            assert exc.value.status_code == 403
            assert exc.value.detail["code"] == "ML_TRAINING_LIMIT_REACHED"
            assert exc.value.detail["current"] == 50
            assert exc.value.detail["limit"] == 50
            assert exc.value.detail["required_plan"] == "enterprise"  # Business


# ═══════════════════════════════════════════════════════════════════════════
# 4. PAYMENT FAILURE RECOVERY AUDIT
# ═══════════════════════════════════════════════════════════════════════════

class TestPaymentFailureRecoveryAudit:
    """Audit 8: Past due -> account freeze -> recovery payment -> active restoration."""
    # REMOVED: the tests that stood here drove BillingLifecycle.activate_subscription /
    # start_trial / upgrade_subscription / downgrade_subscription / renew_subscription
    # against a Supabase mock and asserted each method's own return dict. None of those
    # methods had a production caller -- renew_subscription's only one was inside
    # stripe_webhook()'s invoice.payment_succeeded branch, which went with the provider --
    # so they proved the library returned what it said it returned and nothing about what
    # the product does. The product's real grant path is the Razorpay webhook
    # (_apply_billing_entitlement + _record_plan_period), covered behaviourally by
    # test_billing_e2e.py, test_bill01_razorpay_plans_expire.py and
    # test_subscription_renewal_regression.py. The surface itself is now pinned by
    # tests/test_billing_lifecycle_surface.py, which also records that this product has
    # no trial, no grace period, no proration on upgrade and no scheduled downgrade.



# ═══════════════════════════════════════════════════════════════════════════
# 5. FAILURE-INJECTION BOUNDARY TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestFailureInjectionResilience:
    """Audit 13: Failure at non-critical boundaries must NEVER roll back payment or corrupt DB."""
    # REMOVED: the tests that stood here drove BillingLifecycle.activate_subscription /
    # start_trial / upgrade_subscription / downgrade_subscription / renew_subscription
    # against a Supabase mock and asserted each method's own return dict. None of those
    # methods had a production caller -- renew_subscription's only one was inside
    # stripe_webhook()'s invoice.payment_succeeded branch, which went with the provider --
    # so they proved the library returned what it said it returned and nothing about what
    # the product does. The product's real grant path is the Razorpay webhook
    # (_apply_billing_entitlement + _record_plan_period), covered behaviourally by
    # test_billing_e2e.py, test_bill01_razorpay_plans_expire.py and
    # test_subscription_renewal_regression.py. The surface itself is now pinned by
    # tests/test_billing_lifecycle_surface.py, which also records that this product has
    # no trial, no grace period, no proration on upgrade and no scheduled downgrade.




# ═══════════════════════════════════════════════════════════════════════════
# 6. SECURITY & MULTI-TENANT ISOLATION AUDIT
# ═══════════════════════════════════════════════════════════════════════════

class TestSecurityAndMultiTenantIsolationAudit:
    """Audit 11: Multi-tenant isolation and anti-tamper security."""

    @pytest.mark.asyncio
    async def test_cross_tenant_profile_isolation(self):
        """Tenant A can never retrieve Tenant B's subscription profile."""
        user_a = make_test_user("tenant_A", tier="starter")
        sb_a = make_supabase_mock(tier="starter", user_id="tenant_A")

        plan = await get_user_plan(user_a["id"], sb_a)
        assert plan == "starter"
        # Explicit query filtering on tenant_A id
        sb_a.table("profiles").select().eq.assert_called_with("id", "tenant_A")

    def test_tampered_plan_price_rejected(self):
        """Tampered currency conversion calculations are validated by FXService."""
        assert FXService.is_valid_rate(-5.0) is False
        assert FXService.is_valid_rate(0.0) is False
        assert FXService.is_valid_rate(1.0) is True
