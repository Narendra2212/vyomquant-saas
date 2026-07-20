"""
core/rate_limit_middleware.py — FASTAPI RATE LIMIT MIDDLEWARE

STEP 5: RATE LIMITING INTEGRATION

Automatically applies rate limiting to trade endpoints.

USAGE:
    from fastapi import FastAPI
    from backend_app.core.rate_limit_middleware import RateLimitMiddleware
    
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)
    
    # Or use decorator for specific endpoints
    from backend_app.core.rate_limit_middleware import require_rate_limit
    
    @app.post("/api/trade")
    @require_rate_limit
    async def place_trade(request: Request):
        ...
"""

import time
import logging
from typing import Optional, Callable
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

try:
    from backend_app.core.rate_limiter import rate_limiter, RateLimitExceeded
except ImportError:
    from aerora_quant_backend_updated_final1.core.rate_limiter import rate_limiter, RateLimitExceeded

logger = logging.getLogger("RateLimitMiddleware")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware that automatically rate limits trade endpoints.
    
    Applies to:
    - POST /api/orders/*
    - POST /api/trades/*
    - POST /api/positions/open
    """
    
    # Endpoints that require rate limiting
    RATE_LIMITED_PATHS = [
        "/api/orders",
        "/api/trades",
        "/api/positions/open",
        "/api/execution",
    ]
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with rate limiting."""
        
        # Only apply to POST requests to trade endpoints
        if request.method != "POST":
            return await call_next(request)
        
        path = request.url.path
        
        # Check if this is a rate-limited endpoint
        if not any(path.startswith(p) for p in self.RATE_LIMITED_PATHS):
            return await call_next(request)
        
        # Get user ID from request (auth token or header)
        user_id = self._get_user_id(request)
        
        if not user_id:
            # No user identification - allow but log warning
            logger.warning(f"Rate limit check skipped - no user ID for {path}")
            return await call_next(request)
        
        try:
            # Check trade rate limit
            await rate_limiter.check_trade_allowed(user_id)
            
            # For position opening, also check position limit
            if "position" in path.lower() and "open" in path.lower():
                await rate_limiter.check_position_limit(user_id)
            
            # Process the request
            response = await call_next(request)
            
            # Add rate limit headers to response
            await self._add_rate_limit_headers(response, user_id)
            
            return response
            
        except RateLimitExceeded as e:
            logger.warning(f"[RateLimit] User {user_id} exceeded limit: {e.limit_type}")
            
            return JSONResponse(
                status_code=429,  # Too Many Requests
                content={
                    "error": "Rate limit exceeded",
                    "detail": e.message,
                    "limit_type": e.limit_type,
                    "current": e.current,
                    "limit": e.max_allowed,
                    "retry_after": e.retry_after,
                },
                headers={
                    "Retry-After": str(int(e.retry_after)),
                    "X-RateLimit-Limit": str(e.max_allowed),
                    "X-RateLimit-Remaining": "0",
                }
            )
    
    def _get_user_id(self, request: Request) -> Optional[str]:
        """Extract user ID from request."""
        # Try Authorization header (Bearer token)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            # In production, decode JWT and extract user_id
            # For now, use a simple approach
            token = auth.replace("Bearer ", "")
            # Mock: token contains user_id
            if "user_" in token or "tenant_" in token:
                return token.split("_")[-1] if "_" in token else token
        
        # Try X-User-ID header
        user_id = request.headers.get("X-User-ID")
        if user_id:
            return user_id
        
        # Try query param (for testing)
        user_id = request.query_params.get("user_id")
        if user_id:
            return user_id
        
        return None
    
    async def _add_rate_limit_headers(self, response: Response, user_id: str):
        """Add rate limit status headers to response."""
        try:
            status = await rate_limiter.get_status(user_id)
            
            response.headers["X-RateLimit-Trades-Second"] = f"{status.trades_per_second}/{status.trades_per_second_limit}"
            response.headers["X-RateLimit-Trades-Minute"] = f"{status.trades_per_minute}/{status.trades_per_minute_limit}"
            response.headers["X-RateLimit-Positions"] = f"{status.open_positions}/{status.max_positions}"
            
        except Exception:
            pass  # Don't fail the request if headers can't be added


def require_rate_limit(func: Callable) -> Callable:
    """
    Decorator to apply rate limiting to a specific endpoint.
    
    Usage:
        @app.post("/api/custom-trade")
        @require_rate_limit
        async def custom_trade(request: Request):
            ...
    """
    async def wrapper(*args, **kwargs):
        # Extract request from args/kwargs
        request = None
        for arg in args:
            if isinstance(arg, Request):
                request = arg
                break
        
        if not request:
            for v in kwargs.values():
                if isinstance(v, Request):
                    request = v
                    break
        
        if request:
            # Get user ID
            user_id = None
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth.replace("Bearer ", "")
                user_id = token.split("_")[-1] if "_" in token else token
            
            if not user_id:
                user_id = request.headers.get("X-User-ID")
            
            if user_id:
                try:
                    await rate_limiter.check_trade_allowed(user_id)
                except RateLimitExceeded as e:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": "Rate limit exceeded",
                            "detail": e.message,
                            "limit_type": e.limit_type,
                            "retry_after": e.retry_after,
                        }
                    )
        
        return await func(*args, **kwargs)
    
    return wrapper
