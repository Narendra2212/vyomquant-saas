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
        return {"status": "success", "updated_fields": list(clean.keys())}
    res = supabase.table("profiles").update(clean).eq("id", user["id"]).execute()
    if inspect.isawaitable(res):
        await res
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
        return resp.data if resp and hasattr(resp, "data") and resp.data else []
    except Exception as e:
        logger.error(f"Failed to fetch security logs for user {user['id']}: {e}")
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve security logs. Please try again later."
        )
