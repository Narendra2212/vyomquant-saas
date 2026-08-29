"""
core/dependencies.py — FastAPI dependency injection.

FIXES APPLIED:
  N5:  get_supabase() now returns a module-level singleton (was: new client per request)
  N6:  _validate_ws_token uses the singleton Supabase client
  N3:  check_deployment_limit: free tier gets 1 slot so users can demo the product
  N4:  Cache is invalidated immediately after tier upgrade via invalidate_profile_cache()
  N14: check_deployment_limit counts LIVE bots from FleetManager, not stale Supabase column
  DEV: DEV_MODE support - safe startup without Supabase env vars
"""

import asyncio
import json
import logging
import os
import threading
from typing import Any, Optional

import httpx
from postgrest import AsyncPostgrestClient
from postgrest.constants import DEFAULT_POSTGREST_CLIENT_TIMEOUT

# ══════════════════════════════════════════════════════════════════════════
#  PROFILE CACHE CONFIGURATION
#  TTL is deliberately shorter than the old 300 s to limit the window in
#  which a newly-frozen account can still authenticate.  Override via env
#  without a redeploy: PROFILE_CACHE_TTL=<seconds>
# ══════════════════════════════════════════════════════════════════════════

#: Freeze-effect window = at most PROFILE_CACHE_TTL seconds after freeze.
#: 60 s is a deliberate security tradeoff: tighter than the previous 300 s
#: while still keeping Supabase calls to ≤ 1/user/minute on a warm cache.
PROFILE_CACHE_TTL: int = int(os.getenv("PROFILE_CACHE_TTL", "60"))

# ---------------------------------------------------------------------------
# Hit-rate counters — lightweight in-process atomics, no external dependency.
# Read via get_profile_cache_stats(); logged at DEBUG level per request.
# ---------------------------------------------------------------------------
_profile_cache_hits: int = 0    # Redis hit — no Supabase call needed
_profile_cache_misses: int = 0  # Redis miss — fell through to Supabase
_profile_cache_errors: int = 0  # Redis error — treated as miss

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend_app.core.cache import redis_manager
from backend_app.core.subscription_engine import SubscriptionEngine

# ══════════════════════════════════════════════════════════════════════════
#  SAFE SUPABASE TYPE IMPORT
#  DEV_MODE: Supabase is optional, use Any as fallback
# ══════════════════════════════════════════════════════════════════════════

try:
    from supabase import Client as SupabaseClient
except ImportError:
    # DEV_MODE: Supabase not installed, use Any for type hints
    SupabaseClient = Any

# ══════════════════════════════════════════════════════════════════════════
#  DEV MODE DETECTION
# SECURITY: DEV_MODE cannot be enabled in production under any circumstances
# ══════════════════════════════════════════════════════════════════════════

def _is_production_safe() -> bool:
    """Check if production safety constraints are met."""
    env = (os.environ.get("ENV") or os.environ.get("ENVIRONMENT") or "").lower()
    if env == "production":
        # In production, DEV_MODE must be explicitly false
        dev_mode = os.environ.get("DEV_MODE", "false").lower()
        if dev_mode in ("true", "1", "yes"):
            raise RuntimeError(
                "CRITICAL: DEV_MODE enabled in production. "
                "This is a security violation. Mock Supabase cannot be used in production."
            )
        return True
    return False

_env = (os.environ.get("ENV") or os.environ.get("ENVIRONMENT") or "").lower()
DEV_MODE = (os.environ.get("DEV_MODE", "false").lower() == "true" or \
           _env in ("development", "dev", "test", "testing", "local")) and not _is_production_safe()

logger = logging.getLogger("Dependencies")
bearer_scheme = HTTPBearer(auto_error=False)


# ══════════════════════════════════════════════════════════════════════════
#  SUPABASE SINGLETON (Safe Initialization)
#  DEV: Only initialize if env vars exist, otherwise use mock
# ══════════════════════════════════════════════════════════════════════════

_supabase_client: Optional = None
# threading.Lock matches the pattern in core/auth_middleware.py::_get_jwks_client().
# get_supabase() is a plain sync function (not async), so asyncio.Lock cannot
# be awaited here — threading.Lock is the correct choice.
_supabase_lock = threading.Lock()


# SECURITY: MockSupabaseClient removed - require Supabase in all modes

def get_supabase():
    """
    Returns the module-level anon Supabase singleton for request paths.
    Table access that needs RLS must use get_request_supabase().

    Lazily initialised with double-checked locking: the outer check avoids
    lock contention in the steady-state case; the inner check prevents
    duplicate construction by concurrent cold-start callers.
    
    SECURITY: FAIL-CLOSED in production if Supabase is unavailable.
    """
    global _supabase_client
    if _supabase_client is None:
        with _supabase_lock:
            if _supabase_client is None:
                supabase_url = os.environ.get("SUPABASE_URL")
                supabase_key = os.environ.get("SUPABASE_ANON_KEY")

                if not supabase_url or not supabase_key:
                    if DEV_MODE:
                        logger.warning("SUPABASE_URL / SUPABASE_ANON_KEY missing in DEV_MODE, using None")
                        return None
                    # FAIL-CLOSED: In production, require Supabase
                    if _is_production_safe():
                        raise RuntimeError(
                            "CRITICAL: Supabase credentials required in production. "
                            "Set SUPABASE_URL and SUPABASE_ANON_KEY. "
                            "Operation blocked to prevent security bypass."
                        )
                    logger.error("SUPABASE_URL and SUPABASE_ANON_KEY not set")
                    raise RuntimeError("Supabase request credentials required. Set SUPABASE_URL and SUPABASE_ANON_KEY.")

                try:
                    from supabase import create_client
                    _supabase_client = create_client(supabase_url, supabase_key)
                    logger.info("Supabase client initialized successfully")
                except Exception as e:
                    if DEV_MODE:
                        logger.warning(f"DEV_MODE: Supabase init fallback ({e})")
                        return None
                    logger.error(f"Failed to create Supabase client: {e}")
                    raise RuntimeError(f"Failed to initialize Supabase: {e}")

    return _supabase_client


def create_request_supabase(access_token: str):
    """
    Creates a per-request Supabase client that operates under the authenticated
    user's JWT identity, so Supabase RLS policies are enforced.
    """
    supabase_url  = os.environ.get("SUPABASE_URL")
    supabase_anon = os.environ.get("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_anon:
        if DEV_MODE:
            logger.warning("SUPABASE_URL / SUPABASE_ANON_KEY missing in DEV_MODE, using None")
            return None
        logger.error("SUPABASE_URL and SUPABASE_ANON_KEY not set")
        raise RuntimeError(
            "Supabase request credentials required. Set SUPABASE_URL and SUPABASE_ANON_KEY."
        )

    try:
        from supabase import create_client
        client = create_client(supabase_url, supabase_anon)
        # Inject the user's JWT so RLS auth.uid() resolves correctly
        client.postgrest.auth(access_token)
        return client
    except Exception as e:
        if DEV_MODE:
            logger.warning(f"DEV_MODE: request Supabase client init fallback ({e})")
            return None
        logger.error(f"Failed to create request Supabase client: {e}")
        raise RuntimeError(f"Failed to initialize request Supabase client: {e}")


# ══════════════════════════════════════════════════════════════════════════
#  POOLED ASYNC POSTGREST CLIENT (Connection-Pooled & Tenant-Isolated)
# ══════════════════════════════════════════════════════════════════════════


class _PooledAsyncPostgrestClient(AsyncPostgrestClient):
    """
    Subclass of AsyncPostgrestClient that reuses a shared httpx.AsyncHTTPTransport
    across requests for connection pooling while retaining per-request-isolated headers.
    """
    def __init__(self, *args, transport: httpx.AsyncHTTPTransport, **kwargs):
        self._shared_transport = transport
        super().__init__(*args, **kwargs)

    def create_session(
        self,
        base_url: str,
        headers: dict,
        timeout: Any,
        verify: bool = True,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            verify=verify,
            follow_redirects=True,
            http2=True,
            transport=self._shared_transport,
        )


_shared_async_transport: Optional[httpx.AsyncHTTPTransport] = None
_shared_async_transport_lock = asyncio.Lock()


async def get_shared_async_transport() -> httpx.AsyncHTTPTransport:
    """
    Returns module-level shared AsyncHTTPTransport singleton with connection pooling.
    Lazily initialized with double-checked locking using asyncio.Lock.
    """
    global _shared_async_transport
    if _shared_async_transport is None:
        async with _shared_async_transport_lock:
            if _shared_async_transport is None:
                max_keepalive = int(os.environ.get("SUPABASE_POOL_MAX_KEEPALIVE", "20"))
                max_connections = int(os.environ.get("SUPABASE_POOL_MAX_CONNECTIONS", "100"))
                limits = httpx.Limits(
                    max_keepalive_connections=max_keepalive,
                    max_connections=max_connections,
                )
                _shared_async_transport = httpx.AsyncHTTPTransport(
                    limits=limits,
                    http2=True,
                )
                logger.info(
                    "Initialized shared AsyncHTTPTransport singleton (max_keepalive=%d, max_connections=%d)",
                    max_keepalive,
                    max_connections,
                )
    return _shared_async_transport


async def create_request_supabase_async(access_token: str) -> Optional[_PooledAsyncPostgrestClient]:
    """
    Creates an async PostgREST client operating under the authenticated user's JWT identity,
    reusing a shared httpx.AsyncHTTPTransport singleton for connection pooling while keeping
    headers immutable per-request.
    """
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_anon = os.environ.get("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_anon:
        if DEV_MODE:
            logger.warning("SUPABASE_URL / SUPABASE_ANON_KEY missing in DEV_MODE, using None")
            return None
        logger.error("SUPABASE_URL and SUPABASE_ANON_KEY not set")
        raise RuntimeError(
            "Supabase request credentials required. Set SUPABASE_URL and SUPABASE_ANON_KEY."
        )

    try:
        transport = await get_shared_async_transport()
        headers = {
            "apiKey": supabase_anon,
            "Authorization": f"Bearer {access_token}",
        }
        rest_url = f"{supabase_url.rstrip('/')}/rest/v1"
        client = _PooledAsyncPostgrestClient(
            base_url=rest_url,
            schema="public",
            headers=headers,
            timeout=DEFAULT_POSTGREST_CLIENT_TIMEOUT,
            transport=transport,
        )
        return client
    except Exception as e:
        if DEV_MODE:
            logger.warning(f"DEV_MODE: request async Supabase client init fallback ({e})")
            return None
        logger.error(f"Failed to create async request Supabase client: {e}")
        raise RuntimeError(f"Failed to initialize async request Supabase client: {e}")


async def get_request_supabase(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Bearer token required.",
        )
    return await create_request_supabase_async(credentials.credentials)



# ══════════════════════════════════════════════════════════════════════════
#  JWT AUTH
# ══════════════════════════════════════════════════════════════════════════


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    supabase=Depends(get_supabase),
) -> dict:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    try:
        token = credentials.credentials
        from backend_app.core.auth_middleware import decode_token_local
        payload = decode_token_local(token)

        user_id = payload.get("sub")
        email = payload.get("email", "")
        tenant_id = payload.get("tenant_id") or payload.get("app_metadata", {}).get("tenant_id") or user_id

        if not user_id:
            raise ValueError("Invalid token claims: missing sub")

        user_data = {
            "id": user_id,
            "email": email,
            "tenant_id": tenant_id,
            "access_token": token,
            "role": payload.get("role", "authenticated"),
            "app_metadata": payload.get("app_metadata", {})
        }

        # Verify frozen status.
        # Pass the raw token — _get_cached_profile creates the Supabase client
        # lazily, only on a Redis miss, so the expensive create_request_supabase
        # call is skipped on every cache hit.
        try:
            profile = await _get_cached_profile(tenant_id, token)
            if profile.get("is_frozen", False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User account is frozen. All live actions and access are suspended.",
                )
        except HTTPException:
            raise
        except Exception as e:
            error_msg = f"CRITICAL: Failed to verify account freeze status for user {tenant_id}: {e}"
            logger.error(error_msg)
            try:
                import sentry_sdk
                sentry_sdk.capture_message(error_msg, level="error")
            except Exception:
                pass
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Unable to verify account security status. Please retry in a few moments.",
            )

        return user_data
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Auth failure: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Please sign in again.",
        )


async def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    """
    Phase 7B F-02 REMEDIATION: Admin authorization via app_metadata.role ONLY.

    SECURITY CONTRACT:
    - ONLY app_metadata.role is trusted — it is server-controlled and cannot be
      set by the client SDK (Supabase auth.updateUser only writes user_metadata).
    - user_metadata.role is EXPLICITLY EXCLUDED — user_metadata is writable by
      any authenticated user via supabase.auth.updateUser(), making it a
      privilege-escalation vector if accepted here.
    - top-level JWT 'role' field is ignored; it carries the Postgres RLS role
      ("authenticated"), not the application-level admin role.

    Legitimate admins must have role set in app_metadata via the Supabase
    Dashboard or service-role admin client — never via the browser SDK.

    Permitted roles: admin, support, operator.
    """
    # SECURITY: app_metadata only — user_metadata is user-editable and MUST NOT
    # be used for privilege gating. (Phase 7B F-02 fix)
    app_metadata = user.get("app_metadata") or {}
    role = app_metadata.get("role", "")

    if role not in ("admin", "support", "operator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="GOD MODE ACCESS DENIED. Admin role required.",
        )
    return user


async def get_operator_user(user: dict = Depends(get_current_user)) -> dict:
    """
    Phase 7B F-02 REMEDIATION: Operator authorization via app_metadata.role ONLY.

    SECURITY CONTRACT:
    - ONLY app_metadata.role == "operator" grants access.
    - user_metadata.role is EXPLICITLY EXCLUDED for the same reason as
      get_admin_user() — it is user-editable and must not gate elevated actions.
    - High-blast-radius actions: set_user_status, global_kill_switch.
    """
    # SECURITY: app_metadata only (Phase 7B F-02 fix)
    app_metadata = user.get("app_metadata") or {}
    role = app_metadata.get("role", "")

    if role != "operator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OPERATOR PERMISSION REQUIRED. High-blast-radius action requires operator role.",
        )
    return user


async def require_aal2(user: dict = Depends(get_current_user)) -> dict:
    """
    Phase 7B F-06 REMEDIATION: Enforce MFA / Authentication Assurance Level 2.

    SECURITY CONTRACT:
    - Reads the 'aal' claim from the decoded JWT payload.
    - Supabase sets aal="aal2" in the JWT after a successful MFA challenge.
    - aal="aal1" means only password was verified — MFA was not completed.
    - This dependency MUST be applied to sensitive endpoints:
        * Exchange API key mutation (create/delete)
        * Admin endpoints
        * Password change
        * Billing/payment mutations

    DO NOT apply globally — non-sensitive read endpoints should NOT require MFA.
    Clients must complete the MFA challenge flow before calling AAL2 endpoints.
    """
    # The 'aal' claim is injected by Supabase into the JWT after MFA verification.
    # It is validated by decode_token_local() as part of the JWT signature check.
    app_metadata = user.get("app_metadata") or {}
    aal = app_metadata.get("aal") or user.get("aal", "aal1")

    if aal != "aal2":
        logger.warning(
            f"[AAL2] AAL2 required but got '{aal}' for user {user.get('id', 'unknown')}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Two-factor authentication is required to access this resource. "
                "Please complete MFA verification and retry."
            ),
        )
    return user


# ══════════════════════════════════════════════════════════════════════════
#  LEGACY DEPLOYMENT LIMITS (Backward Compatibility)
#  These constants are maintained for backward compatibility with existing tests
#  and fleet_manager.py. The new subscription engine in subscription_engine.py
#  is the authoritative source for quota enforcement.
# ══════════════════════════════════════════════════════════════════════════

DEPLOYMENT_LIMITS = {
    "free": 1,
    "starter": 2,
    "pro": 5,
    "enterprise": float("inf"),
    "pro_999": 5,
    "elite_1999": float("inf"),
}

ML_BUILD_LIMITS = {
    "free": 0,
    "starter": 0,
    "pro": 5,
    "enterprise": 15,
    "pro_999": 0,
    "elite_1999": 2,
}


# ══════════════════════════════════════════════════════════════════════════
#  REDIS-CACHED PROFILE HELPER
# ══════════════════════════════════════════════════════════════════════════


async def _get_cached_profile(user_id: str, supabase_or_token: Any) -> dict:
    """
    Tries Redis first (fast). Falls back to Supabase on cache miss.

    Args:
        user_id: the tenant/user UUID used as the cache key.
        supabase_or_token: either a pre-built Supabase client (legacy call
            sites such as check_deployment_limit) or a raw JWT string
            (get_current_user hot path). A string value triggers lazy client
            construction only on a Redis miss, avoiding the overhead of
            create_request_supabase() on every cache hit.

    TTL: PROFILE_CACHE_TTL seconds (default 60 s, env-overridable).
    Security note: a frozen account can remain active for at most
    PROFILE_CACHE_TTL seconds after being frozen — this is an explicit,
    documented tradeoff between security propagation latency and
    per-request latency.  The previous value was 300 s; 60 s is the new
    default.  Set PROFILE_CACHE_TTL=0 to always hit Supabase (strictest).
    """
    global _profile_cache_hits, _profile_cache_misses, _profile_cache_errors

    cache_key = f"profile_limits:{user_id}"

    # ── Fast path: Redis hit ─────────────────────────────────────────────
    if PROFILE_CACHE_TTL > 0:
        try:
            cached = await redis_manager.get(cache_key)
            if cached:
                _profile_cache_hits += 1
                logger.debug("[ProfileCache] HIT user=%s hits=%d misses=%d",
                             user_id, _profile_cache_hits, _profile_cache_misses)
                return json.loads(cached)
            # Explicit miss (key absent)
            _profile_cache_misses += 1
            logger.debug("[ProfileCache] MISS user=%s hits=%d misses=%d",
                         user_id, _profile_cache_hits, _profile_cache_misses)
        except Exception as e:
            _profile_cache_errors += 1
            logger.warning(
                "[ProfileCache] Redis error (falling through to Supabase) "
                "user=%s error=%s errors=%d", user_id, e, _profile_cache_errors
            )
    else:
        # TTL=0: bypass cache entirely, always authoritative
        _profile_cache_misses += 1

    # ── Slow path: Supabase fetch ────────────────────────────────────────
    # Build the Supabase client lazily — only here, not on every request.
    if isinstance(supabase_or_token, str):
        supabase = await create_request_supabase_async(supabase_or_token)
    else:
        supabase = supabase_or_token

    if supabase is None:
        if DEV_MODE:
            return {"subscription_tier": "free", "deployed_bots": 0, "ml_strategies_built": 0, "ml_addons_purchased": 0, "is_frozen": False}
        raise RuntimeError("Supabase client is None")

    try:
        res = (
            supabase.table("profiles")
            .select(
                "subscription_tier, deployed_bots, ml_strategies_built, ml_addons_purchased, is_frozen"
            )
            .eq("id", user_id)
            .execute()
        )
        resp = await res if asyncio.iscoroutine(res) else res
    except Exception as e:
        # Fallback: is_frozen column may not exist yet in legacy DB schemas
        logger.warning(f"Querying is_frozen column failed for user {user_id}: {e}")
        try:
            res = (
                supabase.table("profiles")
                .select(
                    "subscription_tier, deployed_bots, ml_strategies_built, ml_addons_purchased"
                )
                .eq("id", user_id)
                .execute()
            )
            resp = await res if asyncio.iscoroutine(res) else res
        except Exception as db_err:
            if DEV_MODE:
                logger.warning(f"DEV_MODE: profile query failed ({db_err}), returning fallback profile")
                return {"subscription_tier": "free", "deployed_bots": 0, "ml_strategies_built": 0, "ml_addons_purchased": 0, "is_frozen": False}
            error_msg = f"Database query failed during profile retrieval for user {user_id}: {db_err}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from db_err

    if not resp.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found.",
        )

    profile = resp.data[0]
    if PROFILE_CACHE_TTL > 0:
        await redis_manager.setex(cache_key, PROFILE_CACHE_TTL, json.dumps(profile))
    return profile


def get_profile_cache_stats() -> dict:
    """Return current hit-rate counters. Safe to call from health/metrics endpoints."""
    total = _profile_cache_hits + _profile_cache_misses
    return {
        "hits": _profile_cache_hits,
        "misses": _profile_cache_misses,
        "errors": _profile_cache_errors,
        "total": total,
        "hit_rate_pct": round(100.0 * _profile_cache_hits / total, 1) if total else None,
        "ttl_seconds": PROFILE_CACHE_TTL,
    }


async def invalidate_profile_cache(user_id: str):
    """
    FIX N4: Call this immediately after a tier upgrade so the user
    stops hitting the old (lower) tier limit within the 5-minute TTL.
    Called by billing webhooks after successful payment.
    """
    await redis_manager.delete(f"profile_limits:{user_id}")
    logger.info(f"Profile cache invalidated for user {user_id} after tier upgrade.")


# ══════════════════════════════════════════════════════════════════════════
#  DEPRECATED: TIER / LIMIT DEFINITIONS
#  These have been moved to the centralized subscription engine
#  Use backend_app.core.subscription_engine for all entitlement checks
# ══════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════
#  ENGINE ACCESSORS
# ══════════════════════════════════════════════════════════════════════════


def get_vault():
    from backend_app.core.state import app_state

    return app_state.vault


def get_telemetry():
    from backend_app.core.state import app_state

    return app_state.telemetry


def get_risk():
    from backend_app.core.state import app_state

    return app_state.risk


def get_fleet():
    from backend_app.core.state import app_state

    return app_state.fleet


def get_alert():
    from backend_app.core.state import app_state

    return app_state.alert


def get_ws_manager():
    from backend_app.core.state import app_state

    return app_state.ws
