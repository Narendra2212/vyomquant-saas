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
                        subscription_status = resp.data[0].get("subscription_status", "active")
                        await redis_manager.setex(cache_key, 300, subscription_status)  # 5 min TTL
                else:
                    subscription_status = "active"
            
            # Block expired/cancelled subscriptions from accessing paid features
            if subscription_status in ["cancelled", "expired"]:
                # Allow access to billing page only
                if not path.startswith("/api/billing"):
                    logger.warning(f"Expired subscription attempt: user {user_id} path {path}")
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Subscription expired. Please renew to access this feature.",
                    )
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Subscription middleware error: {e}")
            # Don't block requests on middleware errors
            pass
        
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
            pass
        
        return await call_next(request)
