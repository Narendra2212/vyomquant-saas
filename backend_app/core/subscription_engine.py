"""
core/subscription_engine.py — Centralized Subscription Capability Engine

This is the single source of truth for all subscription-related logic.
All feature gates, quota checks, and plan definitions are centralized here.

Plans:
- FREE: Unlimited builder, unlimited backtesting, 5 saved strategies, no ML, no marketplace, no live deployment
- STARTER: USD 5 / INR 499, 15 saved strategies, unlimited backtesting, 2 live bots, no marketplace, no ML
- PRO: USD 10 / INR 999, 30 saved strategies, 5 ML trainings/month, 5 live bots, marketplace access, 5 public marketplace publishing
- ENTERPRISE: USD 25 / INR 2499, 100 saved strategies, 15 ML trainings/month, 12 live bots, marketplace access, unlimited publishing
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from backend_app.core.cache import redis_manager

logger = logging.getLogger("SubscriptionEngine")


class Plan(Enum):
    """Subscription plans."""
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class Feature(Enum):
    """Feature flags."""
    LIVE_TRADING = "live_trading"
    MARKETPLACE_ACCESS = "marketplace_access"
    MARKETPLACE_PUBLISH = "marketplace_publish"
    ML_TRAINING = "ml_training"
    UNLIMITED_BACKTESTING = "unlimited_backtesting"
    UNLIMITED_BUILDER = "unlimited_builder"
    API_ACCESS = "api_access"
    PRIORITY_SUPPORT = "priority_support"


class Resource(Enum):
    """Resource types for quota tracking."""
    STRATEGIES = "strategies"
    BOTS = "bots"
    ML_TRAININGS = "ml_trainings"
    MARKETPLACE_PUBLISHED = "marketplace_published"


@dataclass
class PlanConfig:
    """Plan configuration."""
    id: str
    name: str
    description: str
    features: List[str]
    quotas: Dict[str, int]
    pricing: Dict[str, int]  # currency -> price in cents/paise


@dataclass
class UserEntitlements:
    """User entitlements."""
    plan: str
    features: List[str]
    quotas: Dict[str, int]
    usage: Dict[str, int]


class SubscriptionEngine:
    """Centralized subscription capability engine."""
    
    # Plan configurations
    _PLANS: Dict[str, PlanConfig] = {
        Plan.FREE.value: PlanConfig(
            id=Plan.FREE.value,
            name="Free",
            description="Perfect for getting started",
            features=[
                Feature.UNLIMITED_BUILDER.value,
                Feature.UNLIMITED_BACKTESTING.value,
            ],
            quotas={
                Resource.STRATEGIES.value: 5,
                Resource.BOTS.value: 0,
                Resource.ML_TRAININGS.value: 0,
                Resource.MARKETPLACE_PUBLISHED.value: 0,
            },
            pricing={"USD": 0, "INR": 0},
        ),
        Plan.STARTER.value: PlanConfig(
            id=Plan.STARTER.value,
            name="Starter",
            description="For individual traders",
            features=[
                Feature.UNLIMITED_BUILDER.value,
                Feature.UNLIMITED_BACKTESTING.value,
                Feature.LIVE_TRADING.value,
            ],
            quotas={
                Resource.STRATEGIES.value: 15,
                Resource.BOTS.value: 2,
                Resource.ML_TRAININGS.value: 0,
                Resource.MARKETPLACE_PUBLISHED.value: 0,
            },
            pricing={"USD": 500, "INR": 49900},  # $5 / ₹499
        ),
        Plan.PRO.value: PlanConfig(
            id=Plan.PRO.value,
            name="Pro",
            description="For serious traders",
            features=[
                Feature.UNLIMITED_BUILDER.value,
                Feature.UNLIMITED_BACKTESTING.value,
                Feature.LIVE_TRADING.value,
                Feature.ML_TRAINING.value,
                Feature.MARKETPLACE_ACCESS.value,
                Feature.MARKETPLACE_PUBLISH.value,
                Feature.API_ACCESS.value,
            ],
            quotas={
                Resource.STRATEGIES.value: 30,
                Resource.BOTS.value: 5,
                Resource.ML_TRAININGS.value: 5,
                Resource.MARKETPLACE_PUBLISHED.value: 5,
            },
            pricing={"USD": 1000, "INR": 99900},  # $10 / ₹999
        ),
        Plan.ENTERPRISE.value: PlanConfig(
            id=Plan.ENTERPRISE.value,
            name="Enterprise",
            description="For teams scaling up",
            features=[
                Feature.UNLIMITED_BUILDER.value,
                Feature.UNLIMITED_BACKTESTING.value,
                Feature.LIVE_TRADING.value,
                Feature.ML_TRAINING.value,
                Feature.MARKETPLACE_ACCESS.value,
                Feature.MARKETPLACE_PUBLISH.value,
                Feature.API_ACCESS.value,
                Feature.PRIORITY_SUPPORT.value,
            ],
            quotas={
                Resource.STRATEGIES.value: 100,
                Resource.BOTS.value: 12,
                Resource.ML_TRAININGS.value: 15,
                Resource.MARKETPLACE_PUBLISHED.value: float("inf"),  # Unlimited
            },
            pricing={"USD": 2500, "INR": 249900},  # $25 / ₹2499
        ),
    }
    
    # Legacy plan key mapping (old -> new)
    _PLAN_MIGRATION: Dict[str, str] = {
        "free": Plan.FREE.value,
        "pro_999": Plan.PRO.value,
        "elite_1999": Plan.ENTERPRISE.value,
        "pro": Plan.PRO.value,
        "elite": Plan.ENTERPRISE.value,
        "BASIC": Plan.FREE.value,
        "PROFESSIONAL": Plan.PRO.value,
        "ENTERPRISE": Plan.ENTERPRISE.value,
    }
    
    @classmethod
    def migrate_plan_key(cls, old_key: str) -> str:
        """Migrate old plan key to new plan key."""
        return cls._PLAN_MIGRATION.get(old_key, Plan.FREE.value)
    
    @classmethod
    def get_plan_config(cls, plan_key: str) -> Optional[PlanConfig]:
        """Get plan configuration."""
        # Migrate old key if necessary
        plan_key = cls.migrate_plan_key(plan_key)
        return cls._PLANS.get(plan_key)
    
    @classmethod
    def get_all_plans(cls) -> List[PlanConfig]:
        """Get all plan configurations."""
        return list(cls._PLANS.values())
    
    @classmethod
    def has_feature(cls, plan_key: str, feature: str) -> bool:
        """Check if plan has feature."""
        config = cls.get_plan_config(plan_key)
        if not config:
            return False
        return feature in config.features
    
    @classmethod
    def get_quota_limit(cls, plan_key: str, resource: str) -> int:
        """Get quota limit for resource."""
        config = cls.get_plan_config(plan_key)
        if not config:
            return 0
        return config.quotas.get(resource, 0)
    
    @classmethod
    async def check_feature_entitlement(
        cls,
        user_id: str,
        plan_key: str,
        feature: str,
    ) -> bool:
        """Check if user has feature entitlement."""
        config = cls.get_plan_config(plan_key)
        if not config:
            logger.warning(f"Unknown plan key: {plan_key} for user {user_id}")
            return False
        
        if feature not in config.features:
            logger.warning(
                f"Feature {feature} not available for plan {plan_key} "
                f"user {user_id}"
            )
            return False
        
        return True
    
    @classmethod
    async def check_quota_entitlement(
        cls,
        user_id: str,
        plan_key: str,
        resource: str,
        current_usage: int,
    ) -> bool:
        """Check if user has quota available."""
        config = cls.get_plan_config(plan_key)
        if not config:
            logger.warning(f"Unknown plan key: {plan_key} for user {user_id}")
            return False
        
        limit = config.quotas.get(resource, 0)
        
        # Unlimited quota
        if limit == float("inf"):
            return True
        
        if current_usage >= limit:
            logger.warning(
                f"Quota exceeded for {resource}: {current_usage}/{limit} "
                f"user {user_id} plan {plan_key}"
            )
            return False
        
        return True
    
    @classmethod
    async def get_user_entitlements(
        cls,
        user_id: str,
        plan_key: str,
        usage: Optional[Dict[str, int]] = None,
    ) -> UserEntitlements:
        """Get user entitlements."""
        config = cls.get_plan_config(plan_key)
        if not config:
            config = cls._PLANS[Plan.FREE.value]
        
        return UserEntitlements(
            plan=config.id,
            features=config.features,
            quotas=config.quotas,
            usage=usage or {},
        )
    
    @classmethod
    async def increment_quota_usage(
        cls,
        user_id: str,
        resource: str,
    ) -> int:
        """Increment quota usage for user."""
        cache_key = f"quota:{user_id}:{resource}"
        
        # Get current usage
        current = await redis_manager.get(cache_key)
        if current is None:
            current = 0
        else:
            current = int(current)
        
        # Increment
        new_usage = current + 1
        await redis_manager.setex(cache_key, 86400, str(new_usage))  # 24 hour TTL
        
        return new_usage
    
    @classmethod
    async def decrement_quota_usage(
        cls,
        user_id: str,
        resource: str,
    ) -> int:
        """Decrement quota usage for user."""
        cache_key = f"quota:{user_id}:{resource}"
        
        # Get current usage
        current = await redis_manager.get(cache_key)
        if current is None:
            return 0
        
        current = int(current)
        new_usage = max(0, current - 1)
        await redis_manager.setex(cache_key, 86400, str(new_usage))
        
        return new_usage
    
    @classmethod
    async def get_quota_usage(cls, user_id: str, resource: str) -> int:
        """Get current quota usage for user."""
        cache_key = f"quota:{user_id}:{resource}"
        current = await redis_manager.get(cache_key)
        if current is None:
            return 0
        return int(current)
    
    @classmethod
    async def reset_monthly_quotas(cls, user_id: str):
        """Reset monthly quotas for user."""
        resources = [
            Resource.ML_TRAININGS.value,
            Resource.MARKETPLACE_PUBLISHED.value,
        ]
        
        for resource in resources:
            cache_key = f"quota:{user_id}:{resource}"
            await redis_manager.delete(cache_key)
        
        logger.info(f"Reset monthly quotas for user {user_id}")
