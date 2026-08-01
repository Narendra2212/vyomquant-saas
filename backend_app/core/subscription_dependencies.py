"""
core/subscription_dependencies.py — FastAPI Dependencies for Subscription Engine

Provides FastAPI dependency injection functions for subscription checks.
All entitlement checks should use these dependencies.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, status

from backend_app.core.cache import redis_manager
from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.core.subscription_engine import (
    Feature,
    Plan,
    Resource,
    SubscriptionEngine,
)

logger = logging.getLogger("SubscriptionDependencies")


async def _get_user_plan(user_id: str, supabase: Any) -> str:
    """Get user's plan from Supabase."""
    try:
        resp = (
            supabase.table("profiles")
            .select("subscription_tier")
            .eq("id", user_id)
            .execute()
        )
        if resp.data:
            tier = resp.data[0].get("subscription_tier", Plan.FREE.value)
            # Migrate old plan key if necessary
            return SubscriptionEngine.migrate_plan_key(tier)
        return Plan.FREE.value
    except Exception as e:
        logger.error(f"Failed to get user plan: {e}")
        return Plan.FREE.value


async def require_feature(
    feature: str,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """
    Require user to have specific feature.
    Raises 403 if feature not available.
    """
    plan_key = await _get_user_plan(user["id"], supabase)
    
    has_feature = await SubscriptionEngine.check_feature_entitlement(
        user["id"], plan_key, feature
    )
    
    if not has_feature:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Feature '{feature}' requires a higher subscription plan.",
        )
    
    return True


async def check_feature_optional(
    feature: str,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """
    Check if user has feature (optional).
    Returns boolean instead of raising exception.
    """
    plan_key = await _get_user_plan(user["id"], supabase)
    
    return await SubscriptionEngine.check_feature_entitlement(
        user["id"], plan_key, feature
    )


async def require_quota(
    resource: str,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """
    Require user to have quota available.
    Raises 403 if quota exceeded.
    """
    plan_key = await _get_user_plan(user["id"], supabase)
    
    # Get current usage
    current_usage = await SubscriptionEngine.get_quota_usage(user["id"], resource)
    
    has_quota = await SubscriptionEngine.check_quota_entitlement(
        user["id"], plan_key, resource, current_usage
    )
    
    if not has_quota:
        config = SubscriptionEngine.get_plan_config(plan_key)
        limit = config.quotas.get(resource, 0) if config else 0
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Quota exceeded for {resource}: {current_usage}/{limit}. Upgrade your plan to continue.",
        )
    
    return True


async def increment_usage(
    resource: str,
    user: dict = Depends(get_current_user),
):
    """
    Increment usage counter for resource.
    Call this after successful resource creation.
    """
    new_usage = await SubscriptionEngine.increment_quota_usage(user["id"], resource)
    logger.info(f"Incremented {resource} usage for user {user['id']}: {new_usage}")
    return new_usage


async def decrement_usage(
    resource: str,
    user: dict = Depends(get_current_user),
):
    """
    Decrement usage counter for resource.
    Call this after resource deletion.
    """
    new_usage = await SubscriptionEngine.decrement_quota_usage(user["id"], resource)
    logger.info(f"Decremented {resource} usage for user {user['id']}: {new_usage}")
    return new_usage


async def get_user_entitlements(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """
    Get user's full entitlement information.
    """
    plan_key = await _get_user_plan(user["id"], supabase)
    
    # Get current usage for all resources
    usage = {}
    for resource in Resource:
        usage[resource.value] = await SubscriptionEngine.get_quota_usage(
            user["id"], resource.value
        )
    
    entitlements = await SubscriptionEngine.get_user_entitlements(
        user["id"], plan_key, usage
    )
    
    return entitlements


# Feature-specific dependencies for convenience
async def require_live_trading(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Require live trading feature."""
    return await require_feature(Feature.LIVE_TRADING.value, user, supabase)


async def require_marketplace_access(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Require marketplace access."""
    return await require_feature(Feature.MARKETPLACE_ACCESS.value, user, supabase)


async def require_marketplace_publish(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Require marketplace publishing."""
    return await require_feature(Feature.MARKETPLACE_PUBLISH.value, user, supabase)


async def require_ml_training(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Require ML training feature."""
    return await require_feature(Feature.ML_TRAINING.value, user, supabase)


# Quota-specific dependencies for convenience
async def check_strategy_quota(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Check strategy save quota."""
    return await require_quota(Resource.STRATEGIES.value, user, supabase)


async def check_bot_quota(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Check bot deployment quota."""
    return await require_quota(Resource.BOTS.value, user, supabase)


async def check_ml_quota(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Check ML training quota."""
    return await require_quota(Resource.ML_TRAININGS.value, user, supabase)


async def check_marketplace_publish_quota(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Check marketplace publishing quota."""
    return await require_quota(Resource.MARKETPLACE_PUBLISHED.value, user, supabase)
