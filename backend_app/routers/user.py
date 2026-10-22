"""
routers/user.py — User profile, notifications, referral.

FIXES:
  USER-1: update_profile() uses a whitelist — blocks self-upgrading subscription_tier
  USER-2: Uses get_supabase() singleton throughout
"""

import inspect
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend_app.core.database import get_db
from backend_app.core.dependencies import (get_current_user,
                                           get_request_supabase)
from backend_app.core.models import NotificationSettingsRequest
from supabase import Client as SupabaseClient

router = APIRouter()
logger = logging.getLogger("UserRouter")

# USER-1: Only these fields may be updated by the user themselves
PROFILE_ALLOWED_FIELDS = {
    "username",
    "display_name",
    "avatar_url",
    "bio",
    "telegram_id",
}


@router.get("/user/profile")
async def get_profile(
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    if not supabase:
        return {"id": user["id"], "email": user.get("email"), "role": user.get("role", "user")}
    try:
        res = supabase.table("profiles").select("*").eq("id", user["id"]).execute()
        resp = await res if inspect.isawaitable(res) else res
        profile = resp.data[0] if resp and hasattr(resp, "data") and resp.data else {}
        # Ensure role is always present from auth token if not in profiles table
        if profile and "role" not in profile:
            profile["role"] = user.get("role", "user")
        return profile
    except Exception as e:
        logger.error(f"Failed to fetch profile for user {user['id']}: {e}")
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve profile. Please try again later."
        )


@router.put("/user/profile")
async def update_profile(
    data: dict,
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    # USER-1: Whitelist — reject any attempt to modify privileged columns
    disallowed = set(data.keys()) - PROFILE_ALLOWED_FIELDS
    if disallowed:
        raise HTTPException(
            403,
            f"Cannot update protected fields: {sorted(disallowed)}. "
            f"Allowed: {sorted(PROFILE_ALLOWED_FIELDS)}",
        )
    clean = {k: v for k, v in data.items() if k in PROFILE_ALLOWED_FIELDS}
    if not clean:
        raise HTTPException(400, "No valid profile fields provided to update.")

    if not supabase:
        try:
            from backend_app.core.notification_dispatcher import dispatch_user_notification
            await dispatch_user_notification(
                user_id=user["id"],
                event_type="profile_updated",
                category="security",
                severity="info",
                title="Profile Information Updated",
                message=f"Profile details updated ({', '.join(sorted(clean.keys()))}).",
                metadata={"updated_fields": list(clean.keys()), "idempotency_key": f"profile_update:{user['id']}"},
            )
        except Exception:
            pass
        return {"status": "success", "updated_fields": list(clean.keys())}

    res = supabase.table("profiles").update(clean).eq("id", user["id"]).execute()
    if inspect.isawaitable(res):
        await res

    try:
        from backend_app.core.notification_dispatcher import dispatch_user_notification
        await dispatch_user_notification(
            user_id=user["id"],
            event_type="profile_updated",
            category="security",
            severity="info",
            title="Profile Information Updated",
            message=f"Profile details updated ({', '.join(sorted(clean.keys()))}).",
            metadata={"updated_fields": list(clean.keys()), "idempotency_key": f"profile_update:{user['id']}"},
        )
    except Exception:
        pass

    return {"status": "ok"}


@router.get("/notifications/settings")
async def get_notif_settings(
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    if not supabase:
        return {}
    try:
        res = (
            supabase.table("notification_settings")
            .select("*")
            .eq("user_id", user["id"])
            .execute()
        )
        resp = await res if inspect.isawaitable(res) else res
        return resp.data[0] if resp and hasattr(resp, "data") and resp.data else {}
    except Exception as e:
        logger.error(f"Failed to fetch notification settings for user {user['id']}: {e}")
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve notification settings. Please try again later."
        )


@router.put("/notifications/settings")
async def update_notif_settings(
    body: NotificationSettingsRequest,
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    if not supabase:
        return {"status": "ok"}
    data = {
        "user_id": user["id"],
        "channels": body.channels.model_dump(),
        "events": body.events.model_dump(),
    }
    res = supabase.table("notification_settings").upsert(data).execute()
    if inspect.isawaitable(res):
        await res
    return {"status": "ok"}


@router.get("/security/logs")
async def get_security_logs(
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    from backend_app.core.cache.redis_manager import redis_manager
    cache_key = f"user:security_logs:{user['id']}:{limit}"
    try:
        cached = await redis_manager.get(cache_key)
        if cached:
            import json
            return json.loads(cached)
    except Exception:
        pass

    if not supabase:
        return []
    try:
        res = (
            supabase.table("security_logs")
            .select("*")
            .eq("user_id", user["id"])
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        resp = await res if inspect.isawaitable(res) else res
        rows = resp.data if resp and hasattr(resp, "data") and resp.data else []
        try:
            import json
            await redis_manager.set(cache_key, json.dumps(rows), ex=10)
        except Exception:
            pass
        return rows
    except Exception as e:
        logger.error(f"Failed to fetch security logs for user {user['id']}: {e}")
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve security logs. Please try again later."
        )


# ══════════════════════════════════════════════════════════════════════════
# PRIV-01: account data export and account deletion
# ══════════════════════════════════════════════════════════════════════════


@router.get("/user/account/export")
async def export_account(
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """Every category of this user's own data this product knows how to read.

    See ``backend/account_lifecycle.py``'s own module docstring for exactly which
    categories are covered and why connected exchanges are listed by name only.
    """
    if not supabase:
        raise HTTPException(
            status_code=503, detail="Unable to export account data. Please try again later."
        )
    from backend_app.backend.account_lifecycle import export_account_data

    try:
        return await export_account_data(supabase, user_id=user["id"])
    except Exception as e:
        logger.error(f"Failed to export account data for user {user['id']}: {e}")
        raise HTTPException(
            status_code=503,
            detail="Unable to export account data. Please try again later."
        )


class DeleteAccountRequest(BaseModel):
    """Requires the caller to type a confirmation phrase, so a client bug or a stray
    DELETE call cannot destroy account access by accident (Requirement-shaped: there is
    no undo for the PII anonymisation half of this operation)."""
    confirm: str = Field(
        ..., description='Must be exactly "DELETE MY ACCOUNT" to proceed.'
    )


@router.delete("/user/account")
async def delete_account_endpoint(
    body: DeleteAccountRequest,
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """Stop runtimes, revoke exchange keys, anonymise PII, lock the account.

    See ``backend/account_lifecycle.py``'s own module docstring for exactly what this does
    and does not do, and why: this anonymises the profile and freezes the account rather
    than physically cascade-deleting every historical row that references this user.
    """
    if body.confirm != "DELETE MY ACCOUNT":
        raise HTTPException(
            status_code=422,
            detail='confirm must be exactly "DELETE MY ACCOUNT" to proceed.',
        )
    if not supabase:
        raise HTTPException(
            status_code=503, detail="Unable to delete account. Please try again later."
        )
    from backend_app.backend.account_lifecycle import delete_account

    # Step 4 of delete_account freezes the account and anonymises its email. Both are
    # platform-controlled columns that the guard trigger on `public.profiles` refuses to
    # let the `authenticated` role write -- it has to, because a role that can SET
    # is_frozen can also CLEAR it (SEC-07). So the one privileged step gets a service-role
    # client; steps 1-3 keep `supabase` because RLS is what scopes them to this user.
    # Resolved here rather than inside delete_account so tests keep injecting one double.
    service_supabase = None
    try:
        from backend_app.core.dependencies import get_service_supabase_async

        service_supabase = await get_service_supabase_async()
    except Exception as e:  # noqa: BLE001 - absence is handled by the fallback below
        logger.warning(
            f"Service-role client unavailable for account deletion of {user['id']}: {e}"
        )

    try:
        return await delete_account(
            supabase,
            user_id=user["id"],
            user=user,
            service_supabase=service_supabase,
        )
    except Exception as e:
        logger.error(f"Failed to delete account for user {user['id']}: {e}")
        raise HTTPException(
            status_code=503,
            detail="Unable to delete account. Please try again later."
        )
