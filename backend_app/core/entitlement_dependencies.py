"""
core/entitlement_dependencies.py — FastAPI Dependencies for Entitlement Engine

FastAPI dependency injection functions for the centralized entitlement engine.
These dependencies provide easy integration with existing endpoints.
"""

from fastapi import Depends, HTTPException, status
from typing import Optional

from backend_app.core.dependencies import get_current_user
from backend_app.core.entitlement_engine import (
    EntitlementDecision,
    EntitlementEngine,
    FeatureFlag,
    entitlement_engine,
)


def get_entitlement_engine() -> EntitlementEngine:
    """Dependency to get the entitlement engine singleton."""
    return entitlement_engine


async def require_feature(
    feature: FeatureFlag,
    user: dict = Depends(get_current_user),
    engine: EntitlementEngine = Depends(get_entitlement_engine),
):
    """
    Dependency to require a specific feature for an endpoint.
    
    Raises HTTPException if the user is not entitled to the feature.
    
    Usage:
        @router.post("/api/strategies/ml-train")
        async def ml_train(
            user: dict = Depends(require_feature(FeatureFlag.ML_TRAINING))
        ):
            ...
    """
    billing_plan = user.get("app_metadata", {}).get("subscription_tier", "free")
    
    decision = await engine.check_feature_entitlement(
        user_id=user["id"],
        billing_plan=billing_plan,
        feature=feature
    )
    
    if not decision.is_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "feature_not_available",
                "feature": feature.value,
                "current_plan": billing_plan,
                "reason": decision.reason,
                "upgrade_required": decision.upgrade_required,
                "upgrade_to_plan": decision.upgrade_to_plan,
            }
        )
    
    return user


async def check_feature_optional(
    feature: FeatureFlag,
    user: dict = Depends(get_current_user),
    engine: EntitlementEngine = Depends(get_entitlement_engine),
) -> EntitlementDecision:
    """
    Dependency to optionally check feature entitlement without raising exception.
    
    Returns the entitlement decision so the endpoint can handle it gracefully.
    
    Usage:
        @router.get("/api/strategies")
        async def list_strategies(
            user: dict = Depends(get_current_user),
            ml_decision: EntitlementDecision = Depends(
                check_feature_optional(FeatureFlag.ML_TRAINING)
            )
        ):
            if ml_decision.is_allowed:
                # Include ML strategies
                ...
    """
    billing_plan = user.get("app_metadata", {}).get("subscription_tier", "free")
    
    return await engine.check_feature_entitlement(
        user_id=user["id"],
        billing_plan=billing_plan,
        feature=feature
    )


async def require_quota(
    resource_type: str,
    requested_amount: int = 1,
    user: dict = Depends(get_current_user),
    engine: EntitlementEngine = Depends(get_entitlement_engine),
):
    """
    Dependency to require quota for a resource.
    
    Raises HTTPException if the user has exceeded their quota.
    
    Usage:
        @router.post("/api/strategies/{id}/deploy")
        async def deploy_strategy(
            strategy_id: str,
            user: dict = Depends(require_quota("deployed_bots"))
        ):
            ...
    """
    billing_plan = user.get("app_metadata", {}).get("subscription_tier", "free")
    
    decision = await engine.check_quota_entitlement(
        user_id=user["id"],
        billing_plan=billing_plan,
        resource_type=resource_type,
        requested_amount=requested_amount
    )
    
    if not decision.is_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "quota_exceeded",
                "resource_type": resource_type,
                "current_plan": billing_plan,
                "reason": decision.reason,
                "current_usage": decision.current_usage,
                "limit": decision.limit,
                "upgrade_required": decision.upgrade_required,
                "upgrade_to_plan": decision.upgrade_to_plan,
            }
        )
    
    return user


async def get_entitlements(
    user: dict = Depends(get_current_user),
    engine: EntitlementEngine = Depends(get_entitlement_engine),
):
    """
    Dependency to get all entitlement information for the current user.
    
    Returns a dictionary with all feature and quota information.
    
    Usage:
        @router.get("/api/user/entitlements")
        async def get_user_entitlements(
            entitlements: dict = Depends(get_entitlements)
        ):
            return entitlements
    """
    billing_plan = user.get("app_metadata", {}).get("subscription_tier", "free")
    
    return await engine.get_user_entitlements(
        user_id=user["id"],
        billing_plan=billing_plan
    )
