"""
core/tenant_middleware.py — Multi-Tenant Middleware & Quota Enforcement.

FastAPI middleware for tenant context extraction and cross-tenant access protection.
"""

import logging
import time
from datetime import datetime
from typing import List, Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from backend_app.core.cache import redis_manager
from backend_app.core.tenant import (CrossTenantAccessError,
                                     QuotaExceededError,
                                     RateLimitExceededError, TenantContext,
                                     TenantKeyBuilder, TenantPlan, TenantQuota)

logger = logging.getLogger("TenantMiddleware")

# Security scheme
security = HTTPBearer(auto_error=False)


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware for extracting tenant context from JWT and enforcing security.
    
    Injects tenant context into request.state for all downstream handlers.
    """
    
    def __init__(
        self,
        app: ASGIApp,
        skip_paths: Optional[List[str]] = None
    ):
        super().__init__(app)
        self.skip_paths = skip_paths or [
            "/health",
            "/docs",
            "/openapi.json",
            "/api/auth/login",
            "/api/auth/register",
        ]
    
    async def dispatch(self, request: Request, call_next):
        """Process request with tenant context."""
        
        # Skip auth for certain paths
        if any(request.url.path.startswith(path) for path in self.skip_paths):
            return await call_next(request)
        
        try:
            # Extract and validate tenant
            tenant = await self._extract_tenant(request)
            request.state.tenant = tenant
            request.state.user_id = tenant.user_id
            request.state.tenant_id = tenant.tenant_id
            
            # Log request
            logger.debug(
                f"Request: {request.method} {request.url.path} "
                f"(tenant: {tenant.user_id})"
            )
            
            # Process request
            start_time = time.time()
            response = await call_next(request)
            
            # Add tenant headers to response
            response.headers["X-Tenant-ID"] = tenant.tenant_id
            
            # Log completion
            duration = time.time() - start_time
            logger.debug(
                f"Response: {response.status_code} ({duration:.3f}s) "
                f"(tenant: {tenant.user_id})"
            )
            
            return response
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Tenant middleware error: {e}")
            raise HTTPException(500, "Internal server error")
    
    async def _extract_tenant(self, request: Request) -> TenantContext:
        """Extract tenant context from JWT token."""
        
        # Get token from header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(401, "Missing or invalid authorization header")
        
        token = auth_header.replace("Bearer ", "")
        
        try:
            # Decode JWT (implement your JWT decoding here)
            payload = self._decode_jwt(token)
            
            # Build tenant context
            plan = TenantPlan(payload.get("plan", "free"))
            
            tenant = TenantContext(
                user_id=payload["sub"],
                tenant_id=payload.get("tenant_id", payload["sub"]),
                email=payload.get("email", ""),
                plan=plan,
                permissions=payload.get("permissions", []),
                quota=TenantQuota.for_plan(plan),
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
            
            # TENANT ISOLATION: Validate tenant_id matches user_id unless explicitly separated
            # This prevents cross-tenant access attempts
            requested_tenant_id = request.query_params.get("tenant_id") or request.headers.get("X-Tenant-ID")
            if requested_tenant_id and requested_tenant_id != tenant.tenant_id:
                logger.critical(
                    f"TENANT ISOLATION VIOLATION: User {tenant.user_id} attempted to access tenant {requested_tenant_id}"
                )
                raise HTTPException(403, "Cross-tenant access denied")
            
            return tenant
            
        except Exception as e:
            logger.warning(f"JWT validation failed: {e}")
            raise HTTPException(401, "Invalid token")
    
    def _decode_jwt(self, token: str) -> dict:
        """Decode JWT token using local dual-algorithm decoding (ES256/HS256)."""
        from backend_app.core.auth_middleware import decode_token_local
        return decode_token_local(token)


class QuotaEnforcer:
    """
    Enforces resource quotas per tenant.
    
    Checks resource limits before allowing operations.
    """
    
    @staticmethod
    async def check_dag_session_limit(tenant: TenantContext) -> bool:
        """Check if tenant can create new DAG session."""
        key = TenantKeyBuilder.dag_sessions_set(tenant.user_id)
        current = await redis_manager.scard(key)
        
        if current >= tenant.quota.max_dag_sessions:
            raise QuotaExceededError(
                "dag_sessions",
                tenant.quota.max_dag_sessions,
                current,
                tenant.user_id
            )
        return True
    
    @staticmethod
    async def check_position_limit(tenant: TenantContext) -> bool:
        """Check if tenant can open new position."""
        key = TenantKeyBuilder.positions_set(tenant.user_id)
        current = await redis_manager.scard(key)
        
        if current >= tenant.quota.max_positions:
            raise QuotaExceededError(
                "positions",
                tenant.quota.max_positions,
                current,
                tenant.user_id
            )
        return True
    
    @staticmethod
    async def check_websocket_limit(tenant: TenantContext) -> bool:
        """Check if tenant can open new WebSocket connection."""
        key = TenantKeyBuilder.websocket_connections_set(tenant.user_id)
        current = await redis_manager.scard(key)
        
        if current >= tenant.quota.max_websocket_connections:
            raise QuotaExceededError(
                "websocket_connections",
                tenant.quota.max_websocket_connections,
                current,
                tenant.user_id
            )
        return True
    
    @staticmethod
    async def check_backtest_parallel_limit(tenant: TenantContext) -> bool:
        """Check if tenant can run parallel backtest."""
        key = f"user:{tenant.user_id}:backtests:active"
        current = await redis_manager.scard(key)
        
        if current >= tenant.quota.max_backtest_parallel:
            raise QuotaExceededError(
                "backtest_parallel",
                tenant.quota.max_backtest_parallel,
                current,
                tenant.user_id
            )
        return True
    
    @staticmethod
    async def check_order_rate_limit(tenant: TenantContext) -> bool:
        """Check order rate limit per minute."""
        key = TenantKeyBuilder.rate_limit(tenant.user_id, "orders", "1m")
        current = await redis_manager.incr(key)
        
        if current == 1:
            # Set expiry for new counter
            await redis_manager.expire(key, 60)
        
        if current > tenant.quota.max_orders_per_minute:
            raise RateLimitExceededError(
                "orders",
                tenant.quota.max_orders_per_minute,
                "1m",
                tenant.user_id
            )
        return True
    
    @staticmethod
    async def check_dag_node_limit(tenant: TenantContext, node_count: int) -> bool:
        """Check if DAG node count is within quota."""
        if node_count > tenant.quota.max_dag_nodes:
            raise QuotaExceededError(
                "dag_nodes",
                tenant.quota.max_dag_nodes,
                node_count,
                tenant.user_id
            )
        return True
    
    @staticmethod
    async def check_symbol_limit(tenant: TenantContext, symbol_count: int) -> bool:
        """Check if symbol count is within quota."""
        if symbol_count > tenant.quota.max_symbols_per_dag:
            raise QuotaExceededError(
                "symbols_per_dag",
                tenant.quota.max_symbols_per_dag,
                symbol_count,
                tenant.user_id
            )
        return True
    
    @staticmethod
    async def get_quota_usage(tenant: TenantContext) -> dict:
        """Get current quota usage for tenant."""
        return {
            "dag_sessions": await redis_manager.scard(
                TenantKeyBuilder.dag_sessions_set(tenant.user_id)
            ),
            "positions": await redis_manager.scard(
                TenantKeyBuilder.positions_set(tenant.user_id)
            ),
            "websocket_connections": await redis_manager.scard(
                TenantKeyBuilder.websocket_connections_set(tenant.user_id)
            ),
        }


class CrossTenantProtection:
    """
    Prevents cross-tenant resource access.
    
    Verifies that resources belong to the requesting tenant.
    """
    
    @staticmethod
    def verify_ownership(
        tenant: TenantContext,
        resource_owner_id: str,
        resource_id: str
    ) -> None:
        """Verify tenant owns the resource."""
        if not tenant.owns_resource(resource_owner_id):
            # Log security event
            logger.warning(
                f"Cross-tenant access attempt: {tenant.user_id} tried to access "
                f"{resource_owner_id}'s resource {resource_id}"
            )
            raise CrossTenantAccessError(tenant.user_id, resource_owner_id, resource_id)
    
    @staticmethod
    def verify_resource_key(tenant: TenantContext, resource_key: str) -> None:
        """Verify resource key belongs to tenant."""
        expected_prefix = f"user:{tenant.user_id}:"
        
        if not resource_key.startswith(expected_prefix):
            owner = resource_key.split(":")[1] if ":" in resource_key else "unknown"
            logger.warning(
                f"Cross-tenant access via key: {tenant.user_id} -> {resource_key}"
            )
            raise CrossTenantAccessError(tenant.user_id, owner, resource_key)


# Dependency functions for FastAPI

async def get_tenant(request: Request) -> TenantContext:
    """Dependency to get tenant context."""
    if not hasattr(request.state, "tenant"):
        raise HTTPException(401, "Tenant context not available")
    return request.state.tenant


async def get_optional_tenant(request: Request) -> Optional[TenantContext]:
    """Dependency to get optional tenant context (for public endpoints)."""
    return getattr(request.state, "tenant", None)


async def require_admin(tenant: TenantContext = Depends(get_tenant)) -> TenantContext:
    """Dependency to require admin privileges."""
    if not tenant.is_admin():
        raise HTTPException(403, "Admin privileges required")
    return tenant


class TenantQuotaDependency:
    """Factory for quota check dependencies."""
    
    @staticmethod
    def dag_sessions():
        async def check(tenant: TenantContext = Depends(get_tenant)):
            await QuotaEnforcer.check_dag_session_limit(tenant)
            return tenant
        return check
    
    @staticmethod
    def positions():
        async def check(tenant: TenantContext = Depends(get_tenant)):
            await QuotaEnforcer.check_position_limit(tenant)
            return tenant
        return check
    
    @staticmethod
    def order_rate():
        async def check(tenant: TenantContext = Depends(get_tenant)):
            await QuotaEnforcer.check_order_rate_limit(tenant)
            return tenant
        return check


# Security event logging
async def log_security_event(
    event_type: str,
    tenant: TenantContext,
    details: dict
):
    """Log security event for audit."""
    event = {
        "timestamp": datetime.utcnow().isoformat(),
        "event_type": event_type,
        "tenant_id": tenant.tenant_id,
        "user_id": tenant.user_id,
        "ip_address": tenant.ip_address,
        "details": details,
    }
    
    # Log to security audit
    logger.warning(f"Security event: {event}")
    
    # Store in database for audit trail
    await redis_manager.lpush(
        "security:audit_log",
        event
    )
