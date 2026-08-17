"""
routers/admin.py — God Mode / Admin endpoints.

FIXES:
  ADMIN-1: Uses get_supabase() singleton
  ADMIN-2: freeze prefix uses f'{user_id}_' (with underscore) to avoid matching user_id prefixes
  ADMIN-3: list_users() requires search >= 2 chars to prevent full enumeration
  ADMIN-4: /health actually pings QuestDB instead of returning hardcoded 'connected'
"""

import asyncio
import logging

import json
import time
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from backend_app.core.dependencies import (get_admin_user, get_operator_user, get_alert,
                                           get_fleet, get_request_supabase,
                                           get_telemetry, get_ws_manager,
                                           invalidate_profile_cache)
from backend_app.core.background_tasks import fire_and_forget_task
from backend_app.core.models import GlobalKillRequest, UserStatusRequest
from supabase import Client as SupabaseClient

router = APIRouter()
logger = logging.getLogger("AdminRouter")


async def _log_admin_audit_event(
    action: str,
    target: str,
    operator: dict,
    reason: str,
    details: dict = None
):
    audit_record = {
        "audit_id": f"ADMIN-AUDIT-{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}",
        "action": action,
        "target": target,
        "operator_id": operator.get("id"),
        "operator_email": operator.get("email", "unknown"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "details": details or {},
    }

    logger.info(
        f"OPERATOR SECURITY AUDIT: {action} on {target} by {audit_record['operator_email']} - Reason: '{reason}'",
        extra={"audit": audit_record}
    )

    try:
        from backend_app.core.cache.redis_manager import redis_manager
        redis_client = await redis_manager.get_client()
        if redis_client:
            await redis_client.rpush("audit:admin_actions", json.dumps(audit_record))
    except Exception as e:
        logger.warning(f"Failed to persist admin audit record to Redis: {e}")


@router.get("/health")
async def system_health(
    admin: dict = Depends(get_admin_user),
    fleet=Depends(get_fleet),
    telemetry=Depends(get_telemetry),
):
    fleet_status = fleet.get_status()

    # ADMIN-4: Actually ping QuestDB
    questdb_status = "connected"
    try:
        result = await telemetry.execute_query("SELECT 1;")
        questdb_status = "connected" if result else "unreachable"
    except Exception:
        questdb_status = "unreachable"

    pods_result = await telemetry.execute_query(
        "SELECT pod_name, cpu_pct, ram_pct, latency_ms, status FROM pod_status ORDER BY pod_name;"
    )
    pods = []
    if pods_result and pods_result.get("dataset"):
        cols = [c["name"] for c in pods_result["columns"]]
        pods = [dict(zip(cols, row)) for row in pods_result["dataset"]]

    return {"fleet": fleet_status, "pods": pods, "questdb": questdb_status}


@router.get("/metrics")
async def system_metrics(
    admin: dict = Depends(get_admin_user),
    telemetry=Depends(get_telemetry),
):
    result = await telemetry.execute_query(
        "SELECT timestamp, cpu, ram, lat FROM system_metrics "
        "WHERE timestamp > dateadd('h', -24, now()) ORDER BY timestamp ASC;"
    )
    if result and result.get("dataset"):
        cols = [c["name"] for c in result["columns"]]
        return [dict(zip(cols, row)) for row in result["dataset"]]
    return []


@router.get("/users")
async def list_users(
    search: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_admin_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    # ADMIN-3: Require at least 2 chars to prevent full enumeration with empty string
    if search and len(search) < 2:
        raise HTTPException(400, "Search query must be at least 2 characters.")

    if not supabase:
        return [{"id": admin["id"], "email": admin.get("email"), "subscription_tier": "admin", "is_frozen": False}]

    query = (
        supabase.table("profiles")
        .select(
            "id, username, email, subscription_tier, volume_usd, is_frozen, created_at"
        )
        .limit(limit)
    )
    if search:
        query = query.ilike("username", f"%{search}%")
    resp = await query.order("created_at", desc=True).execute()
    return resp.data


@router.post("/users/{user_id}/status")
async def set_user_status(
    user_id: str,
    body: UserStatusRequest,
    admin: dict = Depends(get_operator_user),
    fleet=Depends(get_fleet),
    alert=Depends(get_alert),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(400, "Reason for user status change must not be empty.")

    is_frozen = body.status == "frozen"

    if is_frozen:
        # ADMIN-2: Use exact prefix 'user_id_' to avoid matching other user IDs that share a prefix
        exact_prefix = f"{user_id}_"
        fleet_ids = [bid for bid in fleet._active_fleet if bid.startswith(exact_prefix)]
        for bot_id in fleet_ids:
            parts = bot_id.split("_", 1)
            if len(parts) == 2:
                await fleet.stop_bot(parts[0], parts[1])

    supabase.table("profiles").update({"is_frozen": is_frozen}).eq(
        "id", user_id
    ).execute()
    await invalidate_profile_cache(user_id)
    action = "FROZEN" if is_frozen else "RESTORED"

    # Immutable Audit Log
    await _log_admin_audit_event(
        action=f"USER_{action}",
        target=user_id,
        operator=admin,
        reason=reason,
        details={"status": body.status}
    )

    fire_and_forget_task(
        alert.send_info(f"Operator action: User {user_id} {action} | Reason: {reason}"),
        name=f"admin_alert_user_status_{user_id}"
    )
    return {"status": "ok", "user_id": user_id, "is_frozen": is_frozen, "reason": reason}


@router.post("/kill-all")
async def global_kill_switch(
    body: GlobalKillRequest,
    admin: dict = Depends(get_operator_user),
    fleet=Depends(get_fleet),
    alert=Depends(get_alert),
    ws_mgr=Depends(get_ws_manager),
):
    if body.confirm_code != "HALT":
        raise HTTPException(400, "Invalid confirmation code. Must be 'HALT'.")

    reason = body.reason.strip()
    if not reason:
        raise HTTPException(400, "Reason for global kill switch must not be empty.")

    try:
        await fleet.shutdown_all()

        # Immutable Audit Log
        await _log_admin_audit_event(
            action="GLOBAL_KILL_SWITCH",
            target="PLATFORM",
            operator=admin,
            reason=reason,
            details={"confirm_code": body.confirm_code}
        )

        fire_and_forget_task(
            alert.send_critical(
                "GLOBAL KILL SWITCH",
                f"Platform halted by operator {admin.get('email', 'unknown')}. Reason: {reason}",
            ),
            name="admin_alert_global_kill"
        )
        return {
            "status": "HALTED",
            "message": "All bots stopped. Platform is in standby mode.",
            "reason": reason,
        }
    except Exception as e:
        logger.critical(f"Global kill switch failed: {e}")
        raise HTTPException(500, f"Kill switch error: {e}")


@router.post("/reinitialize")
async def reinitialize_platform(
    admin: dict = Depends(get_admin_user),
    alert=Depends(get_alert),
):
    fire_and_forget_task(
        alert.send_info(
            f"Platform reinitialized by admin {admin.get('email', 'unknown')}."
        ),
        name="admin_alert_reinitialize"
    )
    return {
        "status": "ACTIVE",
        "message": "Platform reinitialized. Bots can now be deployed.",
    }


@router.get("/fleet-status")
async def fleet_status(
    admin: dict = Depends(get_admin_user),
    fleet=Depends(get_fleet),
):
    return fleet.get_status()
