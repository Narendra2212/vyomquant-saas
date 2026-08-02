"""
tests/test_pricing_tier_reconciliation.py

Test suite verifying the reconciliation of subscription tiers between frontend display and backend enforcement.
- Validates SubscriptionTier enum values ('free', 'pro_999', 'elite_1999')
- Validates CheckoutRequest schema accepts valid tiers and rejects stale strings ('starter', 'pro_99', 'enterprise')
- Verifies DEPLOYMENT_LIMITS and ML_BUILD_LIMITS key consistency
"""

import pytest
from pydantic import ValidationError

from backend_app.core.schemas import SubscriptionTier, CheckoutRequest
from backend_app.core.dependencies import DEPLOYMENT_LIMITS, ML_BUILD_LIMITS


class TestPricingTierReconciliation:

    def test_subscription_tier_enum_values(self):
        assert SubscriptionTier.FREE.value == "free"
        assert SubscriptionTier.PRO.value == "pro_999"
        assert SubscriptionTier.ELITE.value == "elite_1999"

    def test_checkout_request_valid_tiers(self):
        req_free = CheckoutRequest(tier=SubscriptionTier.FREE, currency="INR")
        req_pro = CheckoutRequest(tier=SubscriptionTier.PRO, currency="INR")
        req_elite = CheckoutRequest(tier=SubscriptionTier.ELITE, currency="USD")

        assert req_free.tier == "free"
        assert req_pro.tier == "pro_999"
        assert req_elite.tier == "elite_1999"

    def test_checkout_request_rejects_stale_tiers(self):
        with pytest.raises(ValidationError):
            CheckoutRequest(tier="starter", currency="USD")

        with pytest.raises(ValidationError):
            CheckoutRequest(tier="pro_99", currency="USD")

        with pytest.raises(ValidationError):
            CheckoutRequest(tier="enterprise_299", currency="USD")

    def test_limit_dictionaries_key_alignment(self):
        expected_keys = {"free", "pro_999", "elite_1999"}
        assert set(DEPLOYMENT_LIMITS.keys()) == expected_keys
        assert set(ML_BUILD_LIMITS.keys()) == expected_keys

        assert DEPLOYMENT_LIMITS["free"] == 1
        assert DEPLOYMENT_LIMITS["pro_999"] == 5
        assert DEPLOYMENT_LIMITS["elite_1999"] == float("inf")

        assert ML_BUILD_LIMITS["free"] == 0
        assert ML_BUILD_LIMITS["pro_999"] == 0
        assert ML_BUILD_LIMITS["elite_1999"] == 2

    def test_entitlement_engine_plan_mapping_reconciliation(self):
        """Verify PlanMapper reconciles billing plan keys with TenantPlan enums."""
        from backend_app.core.entitlement_engine import PlanMapper, BillingPlan
        from backend_app.core.tenant import TenantPlan

        # Test billing string to tenant enum conversion
        assert PlanMapper.billing_to_tenant("free") == TenantPlan.FREE
        assert PlanMapper.billing_to_tenant("pro_999") == TenantPlan.PROFESSIONAL
        assert PlanMapper.billing_to_tenant("elite_1999") == TenantPlan.ENTERPRISE
        assert PlanMapper.billing_to_tenant("invalid_plan") == TenantPlan.FREE

        # Test tenant enum to billing string conversion
        assert PlanMapper.tenant_to_billing(TenantPlan.FREE) == "free"
        assert PlanMapper.tenant_to_billing(TenantPlan.PROFESSIONAL) == "pro_999"
        assert PlanMapper.tenant_to_billing(TenantPlan.ENTERPRISE) == "elite_1999"

    def test_subscription_engine_migration_reconciliation(self):
        """Verify SubscriptionEngine plan migration reconciles pricing tier keys with engine configs."""
        from backend_app.core.subscription_engine import SubscriptionEngine, Plan, Resource

        # Test plan key migration
        assert SubscriptionEngine.migrate_plan_key("free") == Plan.FREE.value
        assert SubscriptionEngine.migrate_plan_key("pro_999") == Plan.PRO.value
        assert SubscriptionEngine.migrate_plan_key("elite_1999") == Plan.ENTERPRISE.value

        # Test plan config resolution via billing keys
        free_config = SubscriptionEngine.get_plan_config("free")
        pro_config = SubscriptionEngine.get_plan_config("pro_999")
        elite_config = SubscriptionEngine.get_plan_config("elite_1999")

        assert free_config is not None
        assert pro_config is not None
        assert elite_config is not None

        assert free_config.quotas[Resource.STRATEGIES.value] == 5
        assert pro_config.quotas[Resource.BOTS.value] == 5
        assert elite_config.quotas[Resource.BOTS.value] == 12

    def test_feature_entitlement_matrix_reconciliation(self):
        """Verify FeatureEntitlements feature matrix aligns with plan tiers."""
        from backend_app.core.entitlement_engine import FeatureEntitlements, FeatureFlag
        from backend_app.core.tenant import TenantPlan

        # Strategy builder & backtesting are on all plans
        assert FeatureEntitlements.is_feature_available(FeatureFlag.STRATEGY_BUILDER, TenantPlan.FREE)
        assert FeatureEntitlements.is_feature_available(FeatureFlag.BACKTESTING, TenantPlan.FREE)

        # Live trading is available on Pro/Enterprise
        assert not FeatureEntitlements.is_feature_available(FeatureFlag.LIVE_TRADING, TenantPlan.FREE)
        assert FeatureEntitlements.is_feature_available(FeatureFlag.LIVE_TRADING, TenantPlan.PROFESSIONAL)

        # ML training is available on Enterprise
        assert not FeatureEntitlements.is_feature_available(FeatureFlag.ML_TRAINING, TenantPlan.PROFESSIONAL)
        assert FeatureEntitlements.is_feature_available(FeatureFlag.ML_TRAINING, TenantPlan.ENTERPRISE)
