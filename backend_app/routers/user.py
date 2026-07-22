"""
routers/user.py — User profile, notifications, leaderboard, referral.

FIXES:
  USER-1: update_profile() uses a whitelist — blocks self-upgrading subscription_tier
  USER-2: Uses get_supabase() singleton throughout
  USER-3: Leaderboard SQL uses string concat with validated int (safe)
  USER-4: Leaderboard masks user_id to prevent full ID enumeration
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend_app.core.database import get_db
from backend_app.core.dependencies import (get_current_user,
                                           get_request_supabase)
from backend_app.core.models import (InvoiceModel, NotificationSettingsRequest,
                                     SubscriptionModel)
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
    resp = supabase.table("profiles").select("*").eq("id", user["id"]).execute()
    return resp.data[0] if resp.data else {}


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
        raise HTTPException(400, "No valid fields provided.")
    supabase.table("profiles").update(clean).eq("id", user["id"]).execute()
    return {"status": "ok"}


@router.get("/billing/plan")
async def get_billing_plan(
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
    db: Session = Depends(get_db),
):
    sub = db.query(SubscriptionModel).filter(SubscriptionModel.user_id == user["id"]).first()
    if sub:
        return sub.to_dict()
    
    # Fallback to Supabase profiles subscription_tier
    try:
        resp = supabase.table("profiles").select("subscription_tier").eq("id", user["id"]).execute()
        tier = resp.data[0].get("subscription_tier", "free") if resp.data else "free"
    except Exception as e:
        logger.warning(f"Failed to fetch profile for billing fallback: {e}")
        tier = "free"
    
    if tier == "pro_999":
        return {
            "id": "pro",
            "name": "Pro Tier",
            "priceUSD": 12,
            "priceINR": 999,
            "features": ["5 Deployed Algos", "Unlimited Backtesting", "Algorithm Indicators"],
            "nextBillingDate": None,
            "autoRenew": True
        }
    elif tier == "elite_1999":
        return {
            "id": "enterprise",
            "name": "Enterprise Tier",
            "priceUSD": 24,
            "priceINR": 1999,
            "features": ["8 Deployed Algos", "3 ML/DL Models Training", "Algorithm Indicators"],
            "nextBillingDate": None,
            "autoRenew": True
        }
    else:
        return {
            "id": "free",
            "name": "Free Tier",
            "priceUSD": 0,
            "priceINR": 0,
            "features": ["Algorithm Builder", "3 Backtests/mo", "No Deployment"],
            "nextBillingDate": None,
            "autoRenew": False
        }


@router.get("/billing/invoices")
async def get_invoices(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    invs = (
        db.query(InvoiceModel)
        .filter(InvoiceModel.user_id == user["id"])
        .order_by(InvoiceModel.created_at.desc())
        .limit(24)
        .all()
    )
    return [inv.to_dict() for inv in invs]


@router.get("/referral/stats")
async def get_referral_stats(
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    resp = (
        supabase.table("referrals").select("*").eq("referrer_id", user["id"]).execute()
    )
    rows = resp.data or []
    total_earned = sum(r.get("commission_usd", 0) for r in rows)
    pending = sum(
        r.get("commission_usd", 0) for r in rows if r.get("status") == "pending"
    )
    return {
        "total_referrals": len(rows),
        "active_subs": sum(1 for r in rows if r.get("active")),
        "total_earned": round(total_earned, 2),
        "pending_payout": round(pending, 2),
        "referral_link": f"https://algo22.io/ref/{user['id'][:8].upper()}",
    }


@router.get("/notifications/settings")
async def get_notif_settings(
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    resp = (
        supabase.table("notification_settings")
        .select("*")
        .eq("user_id", user["id"])
        .execute()
    )
    return resp.data[0] if resp.data else {}


@router.put("/notifications/settings")
async def update_notif_settings(
    body: NotificationSettingsRequest,
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    data = {
        "user_id": user["id"],
        "channels": body.channels.model_dump(),
        "events": body.events.model_dump(),
    }
    supabase.table("notification_settings").upsert(data).execute()
    return {"status": "ok"}


@router.get("/security/logs")
async def get_security_logs(
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    resp = (
        supabase.table("security_logs")
        .select("*")
        .eq("user_id", user["id"])
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data


@router.get("/leaderboard")
async def get_leaderboard(
    period: str = Query("30d"),
    user: dict = Depends(get_current_user),
):
    from backend_app.core.state import app_state

    period_map = {"7d": 7, "30d": 30, "today": 1, "all": 365}
    days = period_map.get(period, 30)  # safe integer, not user string

    result = await app_state.telemetry.execute_query(
        "SELECT user_id, username, pnl_pct, win_rate, subscription_tier "
        "FROM leaderboard_view "
        "WHERE period_days = " + str(int(days)) + " "  # USER-3: safe int
        "ORDER BY pnl_pct DESC LIMIT 50;"
    )
    if result and result.get("dataset"):
        cols = [c["name"] for c in result["columns"]]
        rows = []
        for i, row in enumerate(result["dataset"]):
            entry = dict(zip(cols, row))
            # USER-4: Mask user_id — only show first 8 chars
            if "user_id" in entry:
                entry["user_id"] = entry["user_id"][:8] + "..."
            rows.append({"rank": i + 1, **entry})
        return rows
    return []
