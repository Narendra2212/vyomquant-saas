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

import json
import logging
import os
from typing import Any, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend_app.core.cache import redis_manager

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
# ══════════════════════════════════════════════════════════════════════════

_env = os.environ.get("ENV", "").lower()
DEV_MODE = (os.environ.get("DEV_MODE", "false").lower() == "true" or \
           _env == "development") and _env != "production"

logger = logging.getLogger("Dependencies")
bearer_scheme = HTTPBearer(auto_error=False)


# ══════════════════════════════════════════════════════════════════════════
#  SUPABASE SINGLETON (Safe Initialization)
#  DEV: Only initialize if env vars exist, otherwise use mock
# ══════════════════════════════════════════════════════════════════════════

_supabase_client: Optional = None


# SECURITY: MockSupabaseClient removed - require Supabase in all modes

def get_supabase():
    """
    Returns the module-level anon Supabase singleton for request paths.
    Table access that needs RLS must use get_request_supabase().
    """
    global _supabase_client
    if _supabase_client is None:
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_ANON_KEY")
        
        if not supabase_url or not supabase_key:
            if DEV_MODE:
                logger.warning("SUPABASE_URL / SUPABASE_ANON_KEY missing in DEV_MODE, using None")
                return None
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
        client.auth.set_session(access_token=access_token, refresh_token="")
        client.postgrest.auth(access_token)
        return client
    except Exception as e:
        if DEV_MODE:
            logger.warning(f"DEV_MODE: request Supabase client init fallback ({e})")
            return None
        logger.error(f"Failed to create request Supabase client: {e}")
        raise RuntimeError(f"Failed to initialize request Supabase client: {e}")


async def get_request_supabase(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Bearer token required.",
        )
    return create_request_supabase(credentials.credentials)



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

        # Verify frozen status
        try:
            profile = await _get_cached_profile(tenant_id, create_request_supabase(token))
            if profile.get("is_frozen", False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User account is frozen. All live actions and access are suspended.",
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Failed to check is_frozen for user {tenant_id}: {e}")

        return user_data
    except Exception as e:
        logger.warning(f"Auth failure: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Please sign in again.",
        )


async def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    """
    F-21 FIX: Only users with app_metadata.role == "admin" may access admin
    endpoints. "service_role" is removed — it is a Supabase internal credential
    for server-side background jobs, not a human admin role. Accepting it here
    would allow any SERVICE_ROLE_KEY bearer to call all admin endpoints.
    """
    role = user.get("role") or user.get("app_metadata", {}).get("role", "")
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="GOD MODE ACCESS DENIED. Admin role required.",
        )
    return user


# ══════════════════════════════════════════════════════════════════════════
#  REDIS-CACHED PROFILE HELPER
# ══════════════════════════════════════════════════════════════════════════


async def _get_cached_profile(user_id: str, supabase: Any) -> dict:
    """
    Tries Redis first (fast). Falls back to Supabase on cache miss.
    TTL: 5 minutes (300 seconds).
    """
    cache_key = f"profile_limits:{user_id}"

    cached = await redis_manager.get(cache_key)
    if cached:
        try:
            return json.loads(cached)
        except Exception:
            pass

    # Cache miss: query Supabase
    try:
        resp = (
            supabase.table("profiles")
            .select(
                "subscription_tier, deployed_bots, ml_strategies_built, ml_addons_purchased, is_frozen"
            )
            .eq("id", user_id)
            .execute()
        )
    except Exception as e:
        # Fallback: is_frozen column may not exist yet
        logger.warning(f"Failed to check is_frozen for user {user_id}: {e}")
        try:
            resp = (
                supabase.table("profiles")
                .select(
                    "subscription_tier, deployed_bots, ml_strategies_built, ml_addons_purchased"
                )
                .eq("id", user_id)
                .execute()
            )
        except Exception:
            # Return safe defaults if profiles table is inaccessible
            return {"subscription_tier": "free", "is_frozen": False}

    if not resp.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found.",
        )

    profile = resp.data[0]
    await redis_manager.setex(cache_key, 300, json.dumps(profile))
    return profile


async def invalidate_profile_cache(user_id: str):
    """
    FIX N4: Call this immediately after a tier upgrade so the user
    stops hitting the old (lower) tier limit within the 5-minute TTL.
    Called by billing webhooks after successful payment.
    """
    await redis_manager.delete(f"profile_limits:{user_id}")
    logger.info(f"Profile cache invalidated for user {user_id} after tier upgrade.")


# ══════════════════════════════════════════════════════════════════════════
#  TIER / LIMIT DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════

# Max simultaneous deployed bots per tier
# FIX N3: Free tier gets 1 so users can demo the product before paying
DEPLOYMENT_LIMITS = {
    "free": 1,  # FIX N3: was 0 — blocked free users completely
    "pro_999": 5,  # Up to 5 concurrent bots
    "elite_1999": float("inf"),
}

ML_BUILD_LIMITS = {
    "free": 0,  # No ML training on free tier
    "pro_999": 0,  # No ML training on pro — elite only
    "elite_1999": 2,  # Base 2 models + purchased add-ons
}


# ══════════════════════════════════════════════════════════════════════════
#  DEPLOYMENT LIMIT CHECK
# ══════════════════════════════════════════════════════════════════════════


async def check_deployment_limit(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """
    FIX N14: Counts LIVE bots from FleetManager (ground truth) rather than
             from the Supabase 'deployed_bots' counter column, which can
             drift out of sync when bots crash without decrementing it.
    FIX N3:  Free tier now gets 1 slot.
    """
    from backend_app.core.state import app_state

    profile = await _get_cached_profile(user["id"], supabase)
    if profile.get("is_frozen", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is frozen. All deployments are blocked.",
        )
    tier = profile.get("subscription_tier", "free")
    allowed = DEPLOYMENT_LIMITS.get(tier, 0)

    # FIX N14: Count from FleetManager, not Supabase column
    user_prefix = f"{user['id']}_"
    live_bots = sum(
        1 for k in app_state.fleet._active_fleet if k.startswith(user_prefix)
    )

    if live_bots >= allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Bot limit reached: {live_bots}/{int(allowed) if allowed != float('inf') else '∞'} "
                f"bots deployed on the '{tier}' plan. "
                f"{'Upgrade to deploy more.' if tier != 'elite_1999' else 'Contact support.'}"
            ),
        )
    return True


# ══════════════════════════════════════════════════════════════════════════
#  ML BUILD LIMIT CHECK
# ══════════════════════════════════════════════════════════════════════════


async def check_ml_build_limit(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    profile = await _get_cached_profile(user["id"], supabase)
    tier = profile.get("subscription_tier", "free")
    built = profile.get("ml_strategies_built", 0)
    addons = profile.get("ml_addons_purchased", 0)

    base_allowed = ML_BUILD_LIMITS.get(tier, 0)
    if base_allowed == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ML/DL model training requires the Elite 1999 plan.",
        )

    total_allowed = base_allowed + addons
    if built >= total_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"ML strategy limit reached ({built}/{total_allowed}). "
                "Purchase an add-on (199 INR) for one additional model slot."
            ),
        )
    return True


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
