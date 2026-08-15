"""
core/subscription_middleware.py — Subscription Middleware for FastAPI

Provides middleware for subscription checks at the request level.
This ensures all requests are validated for subscription compliance.
"""

import logging
from typing import Callable

from fastapi import HTTPException, Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware

from backend_app.core.cache import redis_manager
from backend_app.core.subscription_engine import Plan, SubscriptionEngine

logger = logging.getLogger("SubscriptionMiddleware")


class SubscriptionMiddleware(BaseHTTPMiddleware):
    """
    Middleware to check subscription status for protected routes.
    
    This middleware:
    - Checks if user account is frozen
    - Validates subscription status
    - Caches subscription status for performance
    """
    
    # Paths that bypass subscription checks
    BYPASS_PATHS = {
        "/api/auth",
        "/api/billing/webhook",
        "/api/health",
        "/docs",
        "/openapi.json",
        "/favicon.ico",
    }
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process request through subscription middleware.
        """
        path = request.url.path
        
        # Bypass subscription checks for public paths
        if any(path.startswith(bypass) for bypass in self.BYPASS_PATHS):
            return await call_next(request)
        
        # Get user from request state (set by auth middleware)
        user = getattr(request.state, "user", None)
        
        if not user:
            # No user info - let auth middleware handle it
            return await call_next(request)
        
        user_id = user.get("id")
        
        if not user_id:
            return await call_next(request)
        
        try:
            # Check if account is frozen
            cache_key = f"subscription:frozen:{user_id}"
            is_frozen = await redis_manager.get(cache_key)
            
            if is_frozen is None:
                # Fetch from database
                from backend_app.core.dependencies import get_request_supabase
                supabase = get_request_supabase(user.get("access_token"))
                
                if supabase:
                    resp = (
                        supabase.table("profiles")
                        .select("is_frozen")
                        .eq("id", user_id)
                        .execute()
                    )
                    
                    if resp.data:
                        is_frozen = resp.data[0].get("is_frozen", False)
                        await redis_manager.setex(cache_key, 300, str(is_frozen))  # 5 min TTL
                else:
                    is_frozen = False
            
            if is_frozen == "True" or is_frozen is True:
                logger.warning(f"Frozen account attempt: user {user_id} path {path}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Account is frozen due to payment issues. Please update your payment method.",
                )
            
            # Check subscription status
            cache_key = f"subscription:status:{user_id}"
            subscription_status = await redis_manager.get(cache_key)
            
            if subscription_status is None:
                # Fetch from database
                from backend_app.core.dependencies import get_request_supabase
                supabase = get_request_supabase(user.get("access_token"))

                if supabase:
                    resp = (
                        supabase.table("profiles")
                        .select("subscription_status", "subscription_tier")
                        .eq("id", user_id)
                        .execute()
                    )

                    if resp.data:
                        subscription_status = resp.data[0].get("subscription_status") or "unknown"
                        await redis_manager.setex(cache_key, 300, subscription_status)  # 5 min TTL
                    else:
                        # Profile row missing — treat as unknown, not active
                        subscription_status = "unknown"
                else:
                    # BUG-FIX UK-02: DB client unavailable → status is UNKNOWN, not active.
                    # Defaulting to "active" would grant free access to paid features
                    # whenever the Supabase connection is down.
                    subscription_status = "unknown"

            # Block expired/cancelled/unknown subscriptions from paid features
            if subscription_status in ["cancelled", "expired"] or subscription_status not in ["active", "trialing", "past_due"]:
                # Always allow billing and auth paths so users can renew
                if not path.startswith("/api/billing") and not path.startswith("/api/auth"):
                    logger.warning(
                        f"Subscription gate blocked: user={user_id} status={subscription_status} path={path}"
                    )
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=(
                            "Subscription expired or unavailable. Please renew to access this feature."
                            if subscription_status in ["cancelled", "expired"]
                            else "Subscription status could not be verified. Please try again."
                        ),
                    )

        except HTTPException:
            raise
        except Exception as e:
            # BUG-FIX UK-03: NEVER silently pass on subscription errors for paid features.
            # Bare `pass` previously let every request through on any DB/Redis failure,
            # effectively granting paid access to all users whenever middleware errored.
            logger.error(f"Subscription middleware error: {e}")
            if not path.startswith("/api/billing") and not path.startswith("/api/auth") and not path.startswith("/api/health"):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Subscription verification temporarily unavailable. Please retry.",
                )
        
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware for rate limiting based on subscription tier.
    
    Higher tiers get higher rate limits.
    """
    
    RATE_LIMITS = {
        Plan.FREE.value: 100,  # requests per minute
        Plan.STARTER.value: 300,
        Plan.PRO.value: 1000,
        Plan.ENTERPRISE.value: float("inf"),  # unlimited
    }
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process request through rate limit middleware.
        """
        path = request.url.path
        
        # Bypass rate limiting for webhooks and health checks
        if any(bypass in path for bypass in ["/webhook", "/health", "/docs"]):
            return await call_next(request)
        
        user = getattr(request.state, "user", None)
        
        if not user:
            return await call_next(request)
        
        user_id = user.get("id")
        
        if not user_id:
            return await call_next(request)
        
        try:
            # Get user's plan
            from backend_app.core.dependencies import get_request_supabase
            supabase = get_request_supabase(user.get("access_token"))
            
            if supabase:
                resp = (
                    supabase.table("profiles")
                    .select("subscription_tier")
                    .eq("id", user_id)
                    .execute()
                )
                
                if resp.data:
                    tier = resp.data[0].get("subscription_tier", Plan.FREE.value)
                    tier = SubscriptionEngine.migrate_plan_key(tier)
                    
                    rate_limit = self.RATE_LIMITS.get(tier, 100)
                    
                    if rate_limit != float("inf"):
                        # Check rate limit
                        cache_key = f"ratelimit:{user_id}:{path}"
                        current = await redis_manager.get(cache_key)
                        
                        if current is None:
                            await redis_manager.setex(cache_key, 60, "1")  # 1 minute window
                        else:
                            count = int(current)
                            if count >= rate_limit:
                                logger.warning(f"Rate limit exceeded: user {user_id} path {path}")
                                raise HTTPException(
                                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                    detail=f"Rate limit exceeded. Maximum {rate_limit} requests per minute for your plan.",
                                )
                            await redis_manager.incr(cache_key)
        
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Rate limit middleware error: {e}")
            # Don't block requests on middleware errors
        return await call_next(request)


async def get_user_subscription(user_id: str) -> dict:
    """Get subscription status for user from Redis cache or profile.

    Returns the cached subscription state. If the cache is unavailable or
    unpopulated, returns status='unknown' — callers MUST treat unknown as
    NOT confirmed-active and fail closed (do not permit trading).
    """
    if not user_id:
        # BUG-FIX UK-04: empty user_id → unknown, not active.
        return {"status": "unknown", "tier": "free"}
    try:
        cache_key = f"subscription:status:{user_id}"
        sub_status = await redis_manager.get(cache_key)
        if sub_status:
            return {"status": sub_status, "user_id": user_id}
    except Exception as e:
        logger.warning(f"Failed to fetch subscription cache for {user_id}: {e}")
    # BUG-FIX UK-04: Redis miss or exception → return unknown, not active.
    # Returning "active" here previously allowed cancelled subscribers to keep
    # trading whenever Redis was down or the cache had expired.
    return {"status": "unknown", "user_id": user_id}

