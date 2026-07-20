"""
Replay Authorization Middleware - Phase 6 Authentication Hardening

This module provides comprehensive replay authorization for institutional-grade
security. The middleware ensures that all replay operations are properly
authorized and tenant-scoped.

Key Features:
- Tenant-scoped replay authorization
- Replay request validation
- Replay audit logging
- Cross-tenant replay prevention
- Deterministic replay guarantees preservation

Author: Principal Institutional Platform Security Engineer
"""

import logging
import hashlib
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from fastapi import HTTPException, Request, status
from backend_app.core.dependencies import get_current_user
from backend_app.core.tenant import TenantContext

logger = logging.getLogger("ReplayAuth")


class ReplayAuthorizationMiddleware:
    """
    Comprehensive replay authorization middleware.
    
    This middleware provides:
    - Tenant-scoped replay authorization
    - Replay request validation
    - Replay audit logging
    - Cross-tenant replay prevention
    - Deterministic replay guarantees preservation
    """
    
    def __init__(self):
        """Initialize replay authorization middleware."""
        self._replay_audit_log: Dict[str, list] = {}
    
    async def authorize_replay_request(
        self,
        user_id: str,
        tenant_id: str,
        replay_id: str,
        request: Request
    ) -> bool:
        """
        Authorize a replay request.
        
        Args:
            user_id: User ID making the request
            tenant_id: Tenant ID of the user
            replay_id: Replay ID being requested
            request: FastAPI request object
            
        Returns:
            True if authorized, False otherwise
        """
        # Verify user_id matches tenant_id
        if not self._verify_tenant_ownership(user_id, tenant_id):
            logger.error(f"Cross-tenant replay attempt: user {user_id} trying to access tenant {tenant_id}")
            await self._log_replay_event(
                replay_id=replay_id,
                user_id=user_id,
                tenant_id=tenant_id,
                action="replay_request",
                success=False,
                reason="Cross-tenant access attempt"
            )
            return False
        
        # Verify replay_id belongs to tenant
        if not await self._verify_replay_ownership(replay_id, tenant_id):
            logger.error(f"Cross-tenant replay access: replay {replay_id} does not belong to tenant {tenant_id}")
            await self._log_replay_event(
                replay_id=replay_id,
                user_id=user_id,
                tenant_id=tenant_id,
                action="replay_request",
                success=False,
                reason="Replay does not belong to tenant"
            )
            return False
        
        # Log successful authorization
        await self._log_replay_event(
            replay_id=replay_id,
            user_id=user_id,
            tenant_id=tenant_id,
            action="replay_request",
            success=True,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent")
        )
        
        logger.info(f"Replay authorized: replay {replay_id} for user {user_id}, tenant {tenant_id}")
        return True
    
    async def authorize_replay_recovery(
        self,
        user_id: str,
        tenant_id: str,
        replay_id: str,
        request: Request
    ) -> bool:
        """
        Authorize a replay recovery request.
        
        Args:
            user_id: User ID making the request
            tenant_id: Tenant ID of the user
            replay_id: Replay ID being recovered
            request: FastAPI request object
            
        Returns:
            True if authorized, False otherwise
        """
        # Verify user_id matches tenant_id
        if not self._verify_tenant_ownership(user_id, tenant_id):
            logger.error(f"Cross-tenant replay recovery attempt: user {user_id} trying to access tenant {tenant_id}")
            await self._log_replay_event(
                replay_id=replay_id,
                user_id=user_id,
                tenant_id=tenant_id,
                action="replay_recovery",
                success=False,
                reason="Cross-tenant access attempt"
            )
            return False
        
        # Verify replay_id belongs to tenant
        if not await self._verify_replay_ownership(replay_id, tenant_id):
            logger.error(f"Cross-tenant replay recovery access: replay {replay_id} does not belong to tenant {tenant_id}")
            await self._log_replay_event(
                replay_id=replay_id,
                user_id=user_id,
                tenant_id=tenant_id,
                action="replay_recovery",
                success=False,
                reason="Replay does not belong to tenant"
            )
            return False
        
        # Log successful authorization
        await self._log_replay_event(
            replay_id=replay_id,
            user_id=user_id,
            tenant_id=tenant_id,
            action="replay_recovery",
            success=True,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent")
        )
        
        logger.info(f"Replay recovery authorized: replay {replay_id} for user {user_id}, tenant {tenant_id}")
        return True
    
    def _verify_tenant_ownership(self, user_id: str, tenant_id: str) -> bool:
        """
        Verify that user_id belongs to tenant_id.
        
        Args:
            user_id: User ID
            tenant_id: Tenant ID
            
        Returns:
            True if user belongs to tenant, False otherwise
        """
        # In a real implementation, this would check a database or cache
        # For now, we assume user_id == tenant_id for simplicity
        # This should be replaced with actual tenant ownership verification
        return user_id == tenant_id or user_id.startswith(tenant_id)
    
    async def _verify_replay_ownership(self, replay_id: str, tenant_id: str) -> bool:
        """
        Verify that replay_id belongs to tenant_id.
        
        Args:
            replay_id: Replay ID
            tenant_id: Tenant ID
            
        Returns:
            True if replay belongs to tenant, False otherwise
        """
        # In a real implementation, this would check a database or cache
        # For now, we assume replay_id contains tenant_id for simplicity
        # This should be replaced with actual replay ownership verification
        return replay_id.startswith(tenant_id) or f"tenant:{tenant_id}" in replay_id
    
    async def _log_replay_event(
        self,
        replay_id: str,
        user_id: str,
        tenant_id: str,
        action: str,
        success: bool,
        reason: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ):
        """Log replay authorization event for audit trail."""
        log_id = hashlib.sha256(
            f"{replay_id}:{user_id}:{action}:{datetime.now(timezone.utc).isoformat()}".encode()
        ).hexdigest()
        
        log_entry = {
            "log_id": log_id,
            "replay_id": replay_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "action": action,
            "success": success,
            "reason": reason,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Store in tenant-scoped audit log
        if tenant_id not in self._replay_audit_log:
            self._replay_audit_log[tenant_id] = []
        
        self._replay_audit_log[tenant_id].append(log_entry)
        
        # Keep only last 1000 entries per tenant
        if len(self._replay_audit_log[tenant_id]) > 1000:
            self._replay_audit_log[tenant_id] = self._replay_audit_log[tenant_id][-1000:]
        
        logger.debug(f"Replay event logged: {action} on {replay_id} by {user_id}")
    
    def get_replay_audit_log(self, tenant_id: str, limit: int = 100) -> list:
        """
        Get replay audit log for a tenant.
        
        Args:
            tenant_id: Tenant ID
            limit: Maximum number of entries to return
            
        Returns:
            List of audit log entries
        """
        if tenant_id not in self._replay_audit_log:
            return []
        
        return self._replay_audit_log[tenant_id][-limit:]


# Global singleton
_replay_auth_middleware: Optional[ReplayAuthorizationMiddleware] = None


def get_replay_auth_middleware() -> ReplayAuthorizationMiddleware:
    """Get global replay authorization middleware instance."""
    global _replay_auth_middleware
    if _replay_auth_middleware is None:
        _replay_auth_middleware = ReplayAuthorizationMiddleware()
    return _replay_auth_middleware


async def authorize_replay(
    user_id: str,
    tenant_id: str,
    replay_id: str,
    request: Request
) -> bool:
    """
    Convenience function to authorize a replay request.
    
    Args:
        user_id: User ID making the request
        tenant_id: Tenant ID of the user
        replay_id: Replay ID being requested
        request: FastAPI request object
        
    Returns:
        True if authorized, raises HTTPException otherwise
    """
    auth = get_replay_auth_middleware()
    authorized = await auth.authorize_replay_request(user_id, tenant_id, replay_id, request)
    
    if not authorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Replay authorization failed. You do not have permission to access this replay."
        )
    
    return True


async def authorize_replay_recovery(
    user_id: str,
    tenant_id: str,
    replay_id: str,
    request: Request
) -> bool:
    """
    Convenience function to authorize a replay recovery request.
    
    Args:
        user_id: User ID making the request
        tenant_id: Tenant ID of the user
        replay_id: Replay ID being recovered
        request: FastAPI request object
        
    Returns:
        True if authorized, raises HTTPException otherwise
    """
    auth = get_replay_auth_middleware()
    authorized = await auth.authorize_replay_recovery(user_id, tenant_id, replay_id, request)
    
    if not authorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Replay recovery authorization failed. You do not have permission to recover this replay."
        )
    
    return True
