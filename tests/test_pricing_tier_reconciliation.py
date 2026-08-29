"""
tests/test_pricing_tier_reconciliation.py

Test suite verifying the authoritative reconciliation of subscription tiers across:
- SubscriptionTier schema enum (canonical SaaS tiers: free, starter, pro, enterprise + backward-compatible aliases)
- CheckoutRequest schema validation
- DEPLOYMENT_LIMITS and ML_BUILD_LIMITS dictionaries
- EntitlementEngine PlanMapper (canonical round-trip and alias ingestion)
- SubscriptionEngine single source of truth for plan configs, quotas, and alias migration
- FeatureEntitlements feature matrix alignment with SubscriptionEngine capability definitions
"""

import pytest
from pydantic import ValidationError

from backend_app.core.schemas import SubscriptionTier, CheckoutRequest
from backend_app.core.dependencies import DEPLOYMENT_LIMITS, ML_BUILD_LIMITS


class TestPricingTierReconciliation:

    def test_subscription_tier_enum_values(self):
        """Canonical tiers and backward-compatible aliases must be defined."""
        # Canonical SaaS tiers
        assert SubscriptionTier.FREE.value == "free"
        assert SubscriptionTier.STARTER.value == "starter"
        assert SubscriptionTier.PRO.value == "pro"
        assert SubscriptionTier.ENTERPRISE.value == "enterprise"

        # Legacy aliases for payment provider compatibility
        assert SubscriptionTier.PRO_999.value == "pro_999"
        assert SubscriptionTier.ELITE_1999.value == "elite_1999"

    def test_checkout_request_valid_tiers(self):
        """CheckoutRequest must accept both canonical tiers and legacy aliases."""
        # Canonical SaaS tiers
        req_free = CheckoutRequest(tier=SubscriptionTier.FREE, currency="INR")
        req_starter = CheckoutRequest(tier=SubscriptionTier.STARTER, currency="USD")
        req_pro = CheckoutRequest(tier=SubscriptionTier.PRO, currency="INR")
        req_enterprise = CheckoutRequest(tier=SubscriptionTier.ENTERPRISE, currency="USD")

        assert req_free.tier == "free"
        assert req_starter.tier == "starter"
        assert req_pro.tier == "pro"
        assert req_enterprise.tier == "enterprise"

        # Legacy aliases accepted for backward compatibility
        req_pro_999 = CheckoutRequest(tier=SubscriptionTier.PRO_999, currency="INR")
        req_elite = CheckoutRequest(tier=SubscriptionTier.ELITE_1999, currency="USD")
        assert req_pro_999.tier == "pro_999"
        assert req_elite.tier == "elite_1999"

    def test_checkout_request_rejects_stale_tiers(self):
        """CheckoutRequest must reject invalid/malformed tier strings."""
        with pytest.raises(ValidationError):
            CheckoutRequest(tier="pro_99", currency="USD")

        with pytest.raises(ValidationError):
            CheckoutRequest(tier="enterprise_299", currency="USD")

        with pytest.raises(ValidationError):
            CheckoutRequest(tier="invalid_plan_key", currency="USD")

    def test_limit_dictionaries_key_alignment(self):
        """Deployment and ML build limit dictionaries must cover canonical & legacy keys."""
        canonical_and_legacy = {"free", "starter", "pro", "enterprise", "pro_999", "elite_1999"}
        assert canonical_and_legacy.issubset(set(DEPLOYMENT_LIMITS.keys()))
        assert canonical_and_legacy.issubset(set(ML_BUILD_LIMITS.keys()))

        # Canonical limits
        assert DEPLOYMENT_LIMITS["free"] == 1
        assert DEPLOYMENT_LIMITS["starter"] == 2
        assert DEPLOYMENT_LIMITS["pro"] == 5
        assert DEPLOYMENT_LIMITS["enterprise"] == float("inf")

        assert ML_BUILD_LIMITS["free"] == 0
        assert ML_BUILD_LIMITS["starter"] == 0
        assert ML_BUILD_LIMITS["pro"] == 5
        assert ML_BUILD_LIMITS["enterprise"] == 15

        # Legacy aliases align with their canonical counterparts
        assert DEPLOYMENT_LIMITS["pro_999"] == DEPLOYMENT_LIMITS["pro"]
        assert DEPLOYMENT_LIMITS["elite_1999"] == DEPLOYMENT_LIMITS["enterprise"]

    def test_entitlement_engine_plan_mapping_reconciliation(self):
        """Verify PlanMapper reconciles canonical billing plan keys with TenantPlan enums."""
        from backend_app.core.entitlement_engine import PlanMapper, BillingPlan
        from backend_app.core.tenant import TenantPlan

        # Ingestion: Canonical keys to tenant enums
        assert PlanMapper.billing_to_tenant("free") == TenantPlan.FREE
        assert PlanMapper.billing_to_tenant("starter") == TenantPlan.BASIC
        assert PlanMapper.billing_to_tenant("pro") == TenantPlan.PROFESSIONAL
        assert PlanMapper.billing_to_tenant("enterprise") == TenantPlan.ENTERPRISE

        # Ingestion: Legacy aliases to tenant enums
        assert PlanMapper.billing_to_tenant("pro_999") == TenantPlan.PROFESSIONAL
        assert PlanMapper.billing_to_tenant("elite_1999") == TenantPlan.ENTERPRISE
        assert PlanMapper.billing_to_tenant("starter_499") == TenantPlan.BASIC
        assert PlanMapper.billing_to_tenant("basic") == TenantPlan.BASIC
        assert PlanMapper.billing_to_tenant("invalid_plan") == TenantPlan.FREE

        # Egress: Tenant enum to canonical billing string (single source of truth)
        assert PlanMapper.tenant_to_billing(TenantPlan.FREE) == "free"
        assert PlanMapper.tenant_to_billing(TenantPlan.BASIC) == "starter"
        assert PlanMapper.tenant_to_billing(TenantPlan.PROFESSIONAL) == "pro"
        assert PlanMapper.tenant_to_billing(TenantPlan.ENTERPRISE) == "enterprise"

    def test_subscription_engine_migration_reconciliation(self):
        """Verify SubscriptionEngine plan migration reconciles pricing tier keys with engine configs."""
        from backend_app.core.subscription_engine import SubscriptionEngine, Plan, Resource

        # Canonical key passthrough
        assert SubscriptionEngine.migrate_plan_key("free") == Plan.FREE.value
        assert SubscriptionEngine.migrate_plan_key("starter") == Plan.STARTER.value
        assert SubscriptionEngine.migrate_plan_key("pro") == Plan.PRO.value
        assert SubscriptionEngine.migrate_plan_key("enterprise") == Plan.ENTERPRISE.value

        # Legacy key migration to canonical
        assert SubscriptionEngine.migrate_plan_key("pro_999") == Plan.PRO.value
        assert SubscriptionEngine.migrate_plan_key("elite_1999") == Plan.ENTERPRISE.value
        assert SubscriptionEngine.migrate_plan_key("starter_499") == Plan.STARTER.value
        assert SubscriptionEngine.migrate_plan_key("basic") == Plan.STARTER.value

        # Plan config resolution via billing keys (both canonical and legacy)
        free_config = SubscriptionEngine.get_plan_config("free")
        starter_config = SubscriptionEngine.get_plan_config("starter")
        pro_config = SubscriptionEngine.get_plan_config("pro")
        pro_999_config = SubscriptionEngine.get_plan_config("pro_999")
        enterprise_config = SubscriptionEngine.get_plan_config("enterprise")
        elite_config = SubscriptionEngine.get_plan_config("elite_1999")

        assert free_config is not None
        assert starter_config is not None
        assert pro_config is not None
        assert pro_999_config is not None
        assert enterprise_config is not None
        assert elite_config is not None

        # Quota verification
        assert free_config.quotas[Resource.STRATEGIES.value] == 5
        assert starter_config.quotas[Resource.BOTS.value] == 2
        assert pro_config.quotas[Resource.BOTS.value] == 5
        assert pro_config.quotas[Resource.ML_TRAININGS.value] == 5
        assert enterprise_config.quotas[Resource.BOTS.value] == 12
        assert enterprise_config.quotas[Resource.ML_TRAININGS.value] == 15

    def test_feature_entitlement_matrix_reconciliation(self):
        """Verify FeatureEntitlements feature matrix aligns with plan tiers."""
        from backend_app.core.entitlement_engine import FeatureEntitlements, FeatureFlag
        from backend_app.core.tenant import TenantPlan

        # Strategy builder & backtesting are on all plans
        assert FeatureEntitlements.is_feature_available(FeatureFlag.STRATEGY_BUILDER, TenantPlan.FREE)
        assert FeatureEntitlements.is_feature_available(FeatureFlag.BACKTESTING, TenantPlan.FREE)
        assert FeatureEntitlements.is_feature_available(FeatureFlag.PAPER_TRADING, TenantPlan.FREE)

        # Live trading is available on Starter (Basic), Pro (Professional), Enterprise
        assert not FeatureEntitlements.is_feature_available(FeatureFlag.LIVE_TRADING, TenantPlan.FREE)
        assert FeatureEntitlements.is_feature_available(FeatureFlag.LIVE_TRADING, TenantPlan.BASIC)
        assert FeatureEntitlements.is_feature_available(FeatureFlag.LIVE_TRADING, TenantPlan.PROFESSIONAL)
        assert FeatureEntitlements.is_feature_available(FeatureFlag.LIVE_TRADING, TenantPlan.ENTERPRISE)

        # ML training is available on Professional (5/mo) and Enterprise (15/mo)
        assert not FeatureEntitlements.is_feature_available(FeatureFlag.ML_TRAINING, TenantPlan.FREE)
        assert not FeatureEntitlements.is_feature_available(FeatureFlag.ML_TRAINING, TenantPlan.BASIC)
        assert FeatureEntitlements.is_feature_available(FeatureFlag.ML_TRAINING, TenantPlan.PROFESSIONAL)
        assert FeatureEntitlements.is_feature_available(FeatureFlag.ML_TRAINING, TenantPlan.ENTERPRISE)

        # API Access is available on Professional and Enterprise
        assert not FeatureEntitlements.is_feature_available(FeatureFlag.API_ACCESS, TenantPlan.FREE)
        assert FeatureEntitlements.is_feature_available(FeatureFlag.API_ACCESS, TenantPlan.PROFESSIONAL)
        assert FeatureEntitlements.is_feature_available(FeatureFlag.API_ACCESS, TenantPlan.ENTERPRISE)

        # Priority support is exclusive to Enterprise
        assert not FeatureEntitlements.is_feature_available(FeatureFlag.PRIORITY_SUPPORT, TenantPlan.PROFESSIONAL)
        assert FeatureEntitlements.is_feature_available(FeatureFlag.PRIORITY_SUPPORT, TenantPlan.ENTERPRISE)
