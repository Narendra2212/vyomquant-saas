"""
core/entitlement_engine.py — Centralized Entitlement Engine

Unified entitlement checking system for billing and feature gating.
Integrates with existing quota enforcement and provides a single source
of truth for all entitlement checks across the application.

This engine:
- Maps billing plan names to tenant plan names
- Provides unified entitlement check interface
- Integrates with hard_quota_enforcer
- Supports feature flags
- Caches entitlement decisions for performance
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from backend_app.core.cache import redis_manager
from backend_app.core.hard_quota_enforcer import HardQuotaEnforcer
from backend_app.core.tenant import TenantContext, TenantPlan, TenantQuota

logger = logging.getLogger("EntitlementEngine")


class BillingPlan(Enum):
    """Billing plan names from payment providers AND SubscriptionEngine canonical names.

    BUG-FIX MC-01: Added all SubscriptionEngine plan names so that plan strings written
    by BillingLifecycle (e.g. 'starter', 'pro', 'enterprise') are not silently
    downgraded to TenantPlan.FREE by PlanMapper.billing_to_tenant().
    """
    FREE = "free"
    # Legacy payment-provider keys
    PRO_999 = "pro_999"
    ELITE_1999 = "elite_1999"
    ML_ADDON = "ml_addon"
    # SubscriptionEngine canonical plan names (Plan enum values)
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"
    # Additional legacy aliases
    STARTER_499 = "starter_499"
    BASIC = "basic"
    PROFESSIONAL = "professional"


class FeatureFlag(Enum):
    """Feature flags that can be gated by plan."""
    # Core Features
    STRATEGY_BUILDER = "strategy_builder"
    BACKTESTING = "backtesting"
    PAPER_TRADING = "paper_trading"
    LIVE_TRADING = "live_trading"
    
    # Advanced Features
    ML_TRAINING = "ml_training"
    ALGORITHM_INDICATORS = "algorithm_indicators"
    TELEGRAM_ALERTS = "telegram_alerts"
    EMAIL_ALERTS = "email_alerts"
    
    # Premium Features
    API_ACCESS = "api_access"
    PRIORITY_SUPPORT = "priority_support"
    ADVANCED_ANALYTICS = "advanced_analytics"
    CUSTOM_STRATEGIES = "custom_strategies"
    
    # Marketplace Features
    MARKETPLACE_ACCESS = "marketplace_access"
    STRATEGY_SHARING = "strategy_sharing"
    STRATEGY_SUBSCRIPTION = "strategy_subscription"


@dataclass
class EntitlementDecision:
    """Result of an entitlement check."""
    is_allowed: bool
    plan: str
    feature: str
    reason: Optional[str] = None
    current_usage: Optional[int] = None
    limit: Optional[int] = None
    upgrade_required: bool = False
    upgrade_to_plan: Optional[str] = None


class PlanMapper:
    """Maps billing plan names to tenant plan names.

    BUG-FIX MC-01: PlanMapper.billing_to_tenant() now uses
    SubscriptionEngine.migrate_plan_key() as the canonical normalizer,
    so there is a single source of truth for plan name aliases.
    Direct BillingPlan enum lookup is retained for legacy payment-provider keys.
    """

    BILLING_TO_TENANT = {
        BillingPlan.FREE: TenantPlan.FREE,
        BillingPlan.PRO_999: TenantPlan.PROFESSIONAL,
        BillingPlan.ELITE_1999: TenantPlan.ENTERPRISE,
        BillingPlan.ML_ADDON: TenantPlan.PROFESSIONAL,
        # SubscriptionEngine canonical names
        BillingPlan.STARTER: TenantPlan.BASIC,
        BillingPlan.STARTER_499: TenantPlan.BASIC,
        BillingPlan.BASIC: TenantPlan.BASIC,
        BillingPlan.PRO: TenantPlan.PROFESSIONAL,
        BillingPlan.PROFESSIONAL: TenantPlan.PROFESSIONAL,
        BillingPlan.ENTERPRISE: TenantPlan.ENTERPRISE,
    }

    TENANT_TO_BILLING = {
        TenantPlan.FREE: BillingPlan.FREE,
        TenantPlan.BASIC: BillingPlan.STARTER,
        TenantPlan.PROFESSIONAL: BillingPlan.PRO,
        TenantPlan.ENTERPRISE: BillingPlan.ENTERPRISE,
    }

    @classmethod
    def billing_to_tenant(cls, billing_plan: str) -> TenantPlan:
        """Convert billing plan name to tenant plan enum.

        Uses SubscriptionEngine.migrate_plan_key() as canonical normalizer
        before attempting BillingPlan enum lookup, ensuring all historical
        and current plan name aliases resolve correctly.
        """
        from backend_app.core.subscription_engine import SubscriptionEngine
        # Normalize via SubscriptionEngine (single source of truth for aliases)
        normalized = SubscriptionEngine.migrate_plan_key(billing_plan)
        try:
            billing_enum = BillingPlan(normalized)
            return cls.BILLING_TO_TENANT.get(billing_enum, TenantPlan.FREE)
        except ValueError:
            # Fall back to direct lookup with original key
            try:
                billing_enum = BillingPlan(billing_plan)
                return cls.BILLING_TO_TENANT.get(billing_enum, TenantPlan.FREE)
            except ValueError:
                logger.warning(
                    f"Unknown billing plan: {billing_plan!r} (normalized: {normalized!r}), "
                    f"defaulting to FREE"
                )
                return TenantPlan.FREE
    
    @classmethod
    def tenant_to_billing(cls, tenant_plan: TenantPlan) -> str:
        """Convert tenant plan enum to billing plan name."""
        billing_enum = cls.TENANT_TO_BILLING.get(tenant_plan, BillingPlan.FREE)
        return billing_enum.value


class FeatureEntitlements:
    """Defines which features are available for each plan."""
    
    # Feature availability matrix
    FEATURE_AVAILABILITY = {
        # Core Features - Available on all plans
        FeatureFlag.STRATEGY_BUILDER: {TenantPlan.FREE, TenantPlan.BASIC, TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE},
        FeatureFlag.BACKTESTING: {TenantPlan.FREE, TenantPlan.BASIC, TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE},
        FeatureFlag.PAPER_TRADING: {TenantPlan.FREE, TenantPlan.BASIC, TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE},
        
        # Live Trading - Pro and above
        FeatureFlag.LIVE_TRADING: {TenantPlan.BASIC, TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE},
        
        # Advanced Features - Professional and above
        # BUG-FIX MC-22: ML_TRAINING now available on PROFESSIONAL (matching SubscriptionEngine
        # PRO plan which also grants ml_training). Previous ENTERPRISE-only restriction
        # contradicted SubscriptionEngine._PLANS["pro"].features.
        FeatureFlag.ML_TRAINING: {TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE},
        FeatureFlag.ALGORITHM_INDICATORS: {TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE},
        FeatureFlag.TELEGRAM_ALERTS: {TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE},
        FeatureFlag.EMAIL_ALERTS: {TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE},
        
        # Premium Features - Professional and Enterprise
        # BUG-FIX IB-02: API_ACCESS was ENTERPRISE-only here, but SubscriptionEngine._PLANS["pro"]
        # includes "api_access". Aligning with SubscriptionEngine as the single source of truth.
        FeatureFlag.API_ACCESS: {TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE},
        FeatureFlag.PRIORITY_SUPPORT: {TenantPlan.ENTERPRISE},
        FeatureFlag.ADVANCED_ANALYTICS: {TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE},
        FeatureFlag.CUSTOM_STRATEGIES: {TenantPlan.ENTERPRISE},
        
        # Marketplace Features - All plans
        FeatureFlag.MARKETPLACE_ACCESS: {TenantPlan.FREE, TenantPlan.BASIC, TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE},
        FeatureFlag.STRATEGY_SHARING: {TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE},
        FeatureFlag.STRATEGY_SUBSCRIPTION: {TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE},
    }
    
    @classmethod
    def is_feature_available(cls, feature: FeatureFlag, plan: TenantPlan) -> bool:
        """Check if a feature is available for a given plan."""
        available_plans = cls.FEATURE_AVAILABILITY.get(feature, set())
        return plan in available_plans
    
    @classmethod
    def get_required_plan(cls, feature: FeatureFlag) -> Optional[TenantPlan]:
        """Get the minimum plan required for a feature."""
        available_plans = cls.FEATURE_AVAILABILITY.get(feature, set())
        if not available_plans:
            return None
        
        # Return the minimum plan that has the feature
        plan_order = [TenantPlan.FREE, TenantPlan.BASIC, TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE]
        for plan in plan_order:
            if plan in available_plans:
                return plan
        return None


class EntitlementEngine:
    """
    Centralized entitlement engine for all feature and quota checks.
    
    This engine provides a unified interface for checking:
    - Feature availability based on plan
    - Resource quotas based on plan
    - Usage limits based on plan
    - Upgrade requirements
    
    It integrates with the existing hard_quota_enforcer for service-layer
    enforcement and uses Redis for caching entitlement decisions.
    """
    
    def __init__(self):
        self.quota_enforcer = HardQuotaEnforcer()
        self.plan_mapper = PlanMapper()
        self.feature_entitlements = FeatureEntitlements()
        self._cache_ttl = 300  # 5 minutes
    
    async def check_feature_entitlement(
        self,
        user_id: str,
        billing_plan: str,
        feature: FeatureFlag,
        check_usage: bool = False
    ) -> EntitlementDecision:
        """
        Check if a user is entitled to use a feature.
        
        Args:
            user_id: User ID
            billing_plan: Billing plan name (e.g., "free", "pro_999", "elite_1999")
            feature: Feature flag to check
            check_usage: Whether to check current usage against limits
        
        Returns:
            EntitlementDecision with check result
        """
        # Convert billing plan to tenant plan
        tenant_plan = self.plan_mapper.billing_to_tenant(billing_plan)
        
        # Check if feature is available for this plan
        is_available = self.feature_entitlements.is_feature_available(feature, tenant_plan)
        
        if is_available:
            return EntitlementDecision(
                is_allowed=True,
                plan=billing_plan,
                feature=feature.value,
                reason=f"Feature available on {billing_plan} plan"
            )
        
        # Feature not available - determine upgrade requirement
        required_plan = self.feature_entitlements.get_required_plan(feature)
        upgrade_to = self.plan_mapper.tenant_to_billing(required_plan) if required_plan else None
        
        return EntitlementDecision(
            is_allowed=False,
            plan=billing_plan,
            feature=feature.value,
            reason=f"Feature requires {upgrade_to or 'higher'} plan",
            upgrade_required=True,
            upgrade_to_plan=upgrade_to
        )
    
    async def check_quota_entitlement(
        self,
        user_id: str,
        billing_plan: str,
        resource_type: str,
        requested_amount: int = 1
    ) -> EntitlementDecision:
        """
        Check if a user has quota available for a resource.
        
        Args:
            user_id: User ID
            billing_plan: Billing plan name
            resource_type: Type of resource (e.g., "deployed_bots", "ml_models")
            requested_amount: Amount requested
        
        Returns:
            EntitlementDecision with check result
        """
        # Convert billing plan to tenant plan
        tenant_plan = self.plan_mapper.billing_to_tenant(billing_plan)
        
        # Get quota configuration for this plan
        quota = TenantQuota.for_plan(tenant_plan)
        
        # Map resource type to quota field
        quota_mapping = {
            "deployed_bots": ("max_dag_sessions", "DAG sessions"),
            "ml_models": ("max_dag_sessions", "ML models"),  # ML uses same quota for now
            "positions": ("max_positions", "positions"),
            "daily_trades": ("max_daily_trades", "daily trades"),
        }
        
        quota_field, resource_name = quota_mapping.get(resource_type, (None, resource_type))
        
        if quota_field is None:
            logger.warning(f"Unknown resource type: {resource_type}")
            return EntitlementDecision(
                is_allowed=True,
                plan=billing_plan,
                feature=resource_type,
                reason="Resource type not configured, allowing by default"
            )
        
        limit = getattr(quota, quota_field, float("inf"))
        
        # Get current usage from Redis
        if resource_type == "deployed_bots":
            # Use FleetManager for deployed bots (ground truth)
            from backend_app.core.state import app_state
            user_prefix = f"{user_id}_"
            current = sum(
                1 for k in app_state.fleet._active_fleet if k.startswith(user_prefix)
            )
        else:
            # Use Redis for other resources
            cache_key = f"user:{user_id}:quota:{resource_type}"
            current = int(await redis_manager.get(cache_key) or 0)
        
        # Check if quota is exceeded
        if current + requested_amount > limit:
            # Determine upgrade requirement
            upgrade_to = None
            if tenant_plan == TenantPlan.FREE:
                upgrade_to = "pro_999"
            elif tenant_plan == TenantPlan.BASIC or tenant_plan == TenantPlan.PROFESSIONAL:
                upgrade_to = "elite_1999"
            
            return EntitlementDecision(
                is_allowed=False,
                plan=billing_plan,
                feature=resource_type,
                reason=f"{resource_name} limit reached: {current}/{int(limit) if limit != float('inf') else '∞'}",
                current_usage=current,
                limit=int(limit) if limit != float('inf') else None,
                upgrade_required=True,
                upgrade_to_plan=upgrade_to
            )
        
        return EntitlementDecision(
            is_allowed=True,
            plan=billing_plan,
            feature=resource_type,
            current_usage=current,
            limit=int(limit) if limit != float('inf') else None
        )
    
    async def get_user_entitlements(
        self,
        user_id: str,
        billing_plan: str
    ) -> Dict[str, Any]:
        """
        Get all entitlement information for a user.
        
        Args:
            user_id: User ID
            billing_plan: Billing plan name
        
        Returns:
            Dictionary with all entitlement information
        """
        # Convert billing plan to tenant plan
        tenant_plan = self.plan_mapper.billing_to_tenant(billing_plan)
        quota = TenantQuota.for_plan(tenant_plan)
        
        # Check all features
        feature_status = {}
        for feature in FeatureFlag:
            decision = await self.check_feature_entitlement(user_id, billing_plan, feature)
            feature_status[feature.value] = {
                "available": decision.is_allowed,
                "upgrade_required": decision.upgrade_required,
                "upgrade_to": decision.upgrade_to_plan
            }
        
        # Get quota status
        quota_status = {
            "deployed_bots": {
                "limit": quota.max_dag_sessions,
                "used": await self._get_deployed_bot_count(user_id),
            },
            "positions": {
                "limit": quota.max_positions,
                "used": await self._get_position_count(user_id),
            },
            "daily_trades": {
                "limit": quota.max_daily_trades,
                "used": await self._get_daily_trade_count(user_id),
            },
        }
        
        return {
            "user_id": user_id,
            "billing_plan": billing_plan,
            "tenant_plan": tenant_plan.value,
            "features": feature_status,
            "quotas": quota_status,
        }
    
    async def _get_deployed_bot_count(self, user_id: str) -> int:
        """Get current deployed bot count from FleetManager."""
        from backend_app.core.state import app_state
        user_prefix = f"{user_id}_"
        return sum(
            1 for k in app_state.fleet._active_fleet if k.startswith(user_prefix)
        )
    
    async def _get_position_count(self, user_id: str) -> int:
        """Get current position count from Redis."""
        key = f"user:{user_id}:positions"
        return int(await redis_manager.scard(key) or 0)
    
    async def _get_daily_trade_count(self, user_id: str) -> int:
        """Get today's trade count from Redis."""
        from datetime import datetime
        today = datetime.utcnow().strftime("%Y-%m-%d")
        key = f"user:{user_id}:daily_trades:{today}"
        return int(await redis_manager.get(key) or 0)
    
    async def invalidate_entitlement_cache(self, user_id: str):
        """Invalidate cached entitlement decisions for a user."""
        # Invalidate profile cache (from dependencies.py)
        await redis_manager.delete(f"profile_limits:{user_id}")
        logger.info(f"Entitlement cache invalidated for user {user_id}")


# Global singleton instance
entitlement_engine = EntitlementEngine()
