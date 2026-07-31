"""
WebSocket Authentication Middleware - Phase 6 Authentication Hardening

ALGO22 AUTH FIX: Replaced broken ES256/JWKS decoding + supabase.auth.get_user()
network fallback with zero-latency local HS256 decoding using SUPABASE_JWT_SECRET.

Root cause: The previous hotfix attempted ES256 decoding but Supabase signs tokens
with HS256, causing every validation to fail and fall through to a blocking network
call to supabase.auth.get_user(). This triggered rate limits and timeouts, producing
intermittent 401s on WebSocket connections.

Fix: Local HS256 decoding (same pattern as dependencies.get_current_user).
No network calls on the authentication hot path.

Key Features:
- Token validation on WebSocket connection (zero-latency, local only)
- Connection rejection for invalid tokens
- Tenant isolation on WebSocket connections
- HS256 signature verification against SUPABASE_JWT_SECRET
- Audit logging for authentication events

Author: Principal Institutional Platform Security Engineer
"""

import logging
from typing import Any, Dict, Optional

import jwt
from fastapi import WebSocket, status

logger = logging.getLogger("WebSocketAuth")


# ---------------------------------------------------------------------------
# Local HS256 token decoder — shared helper used by both the class and the
# module-level function in ws_routes.py (via this module).
# ---------------------------------------------------------------------------

def _decode_hs256_token(token: str) -> Optional[dict]:
    """
    Decode and verify a Supabase JWT (ES256 or HS256) locally.
    
    This helper is used by the WebSocket authentication middleware and route handlers.
    It delegates to `decode_token_local` to perform zero-latency validation.
    
    Returns the decoded payload dict on success, or None on failure.
    """
    try:
        from backend_app.core.auth_middleware import decode_token_local
        return decode_token_local(token)
    except jwt.exceptions.ExpiredSignatureError:
        logger.warning("[WS/Auth] Token expired")
        return None
    except jwt.exceptions.InvalidTokenError as e:
        logger.warning(f"[WS/Auth] Invalid token: {e}")
        return None
    except Exception as e:
        logger.error(f"[WS/Auth] Unexpected token decode error: {e}")
        return None


class WebSocketAuthMiddleware:
    """
    Comprehensive WebSocket authentication middleware.

    This middleware provides:
    - Zero-latency local HS256 token validation (no network calls)
    - JWT signature verification against SUPABASE_JWT_SECRET
    - Tenant isolation (user ID claim vs path parameter cross-check)
    - Audit logging
    """

    def __init__(self):
        """Initialize WebSocket authentication middleware."""
        self._connection_attempts: Dict[str, int] = {}
        self._failed_attempts: Dict[str, int] = {}

    async def authenticate_websocket(
        self,
        websocket: WebSocket,
        token: Optional[str] = None,
        user_id: Optional[str] = None,
        require_auth: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Authenticate WebSocket connection using local HS256 JWT decoding.

        Args:
            websocket: WebSocket connection
            token: JWT token from query parameter
            user_id: User ID from path parameter
            require_auth: Whether authentication is required

        Returns:
            User data dict if authentication successful, None otherwise.
        """
        # If authentication not required, allow connection
        if not require_auth:
            logger.debug("[WS/Auth] Authentication not required for this endpoint")
            return None

        async def _safe_close(code: int, reason: str):
            try:
                await websocket.close(code=code, reason=reason)
            except Exception:
                pass

        # Require token
        if not token:
            logger.warning("[WS/Auth] Missing token in WebSocket connection")
            await _safe_close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")
            return None

        # Validate token locally
        try:
            user_data = await self._validate_token(token, user_id)

            if not user_data:
                logger.warning(f"[WS/Auth] Invalid token for user {user_id}")
                await _safe_close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
                return None

            logger.info(f"[WS/Auth] WebSocket authenticated for user {user_data.get('id')}")
            self._track_connection_attempt(user_data.get("id"), success=True)
            return user_data

        except Exception as e:
            logger.error(f"[WS/Auth] Authentication error: {e}")
            await _safe_close(code=status.WS_1011_INTERNAL_ERROR, reason="Authentication error")
            return None

    async def _validate_token(
        self, token: str, claimed_user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Validate JWT token using local HS256 decoding.

        No network calls — pure local cryptographic verification.

        Args:
            token: JWT token to validate
            claimed_user_id: User ID claimed by the client (path param)

        Returns:
            User data dict if token is valid, None otherwise.
        """
        payload = _decode_hs256_token(token)
        if payload is None:
            return None

        user_id = payload.get("sub")
        email = payload.get("email", "")
        tenant_id = (
            payload.get("tenant_id")
            or payload.get("app_metadata", {}).get("tenant_id")
            or user_id
        )

        if not user_id:
            logger.warning("[WS/Auth] Invalid token: missing sub claim")
            return None

        # Tenant isolation: token sub must match the path-level user_id
        if claimed_user_id and user_id != claimed_user_id:
            logger.warning(
                f"[WS/Auth] User ID mismatch: claimed={claimed_user_id}, token={user_id}"
            )
            return None

        return {
            "id": user_id,
            "email": email,
            "tenant_id": tenant_id,
            "access_token": token,
            "role": payload.get("role", "authenticated"),
            "app_metadata": payload.get("app_metadata", {}),
        }

    def _track_connection_attempt(self, user_id: str, success: bool):
        """Track connection attempt for rate limiting and monitoring."""
        if user_id not in self._connection_attempts:
            self._connection_attempts[user_id] = 0
            self._failed_attempts[user_id] = 0

        self._connection_attempts[user_id] += 1

        if not success:
            self._failed_attempts[user_id] += 1

        if self._failed_attempts.get(user_id, 0) > 5:
            logger.warning(
                f"[WS/Auth] High failed attempts for user {user_id}: "
                f"{self._failed_attempts[user_id]}"
            )

    def get_connection_stats(self) -> Dict[str, Any]:
        """Get connection statistics."""
        return {
            "total_connections": sum(self._connection_attempts.values()),
            "failed_connections": sum(self._failed_attempts.values()),
            "unique_users": len(self._connection_attempts),
        }


# Global singleton
websocket_auth = WebSocketAuthMiddleware()


def get_websocket_auth() -> WebSocketAuthMiddleware:
    """Get global WebSocket authentication middleware instance."""
    return websocket_auth


async def authenticate_websocket_connection(
    websocket: WebSocket,
    token: Optional[str] = None,
    user_id: Optional[str] = None,
    require_auth: bool = True
) -> Optional[Dict[str, Any]]:
    """
    Convenience function to authenticate WebSocket connection.

    Args:
        websocket: WebSocket connection
        token: JWT token from query parameter
        user_id: User ID from path parameter
        require_auth: Whether authentication is required

    Returns:
        User data if authentication successful, None otherwise
    """
    auth = get_websocket_auth()
    return await auth.authenticate_websocket(websocket, token, user_id, require_auth)
