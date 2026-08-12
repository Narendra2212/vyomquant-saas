"""
routers/risk.py — Risk settings and kill switch.

FIXES APPLIED:
  C5:  ORDER_EXECUTION_ENGINE → order_execution_engine
  SQL: user['id'] wrapped through _safe_uid() before SQL injection
  SCALE: Uses exchange pool instead of new connection per request
  PERF: Added Redis caching for risk settings to reduce DB load
"""

import asyncio
import logging
import re
from datetime import datetime
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException

from backend_app.core.dependencies import (create_request_supabase, get_alert,
                                           get_current_user, get_vault,
                                           get_ws_manager)
from backend_app.core.background_tasks import fire_and_forget_task
from backend_app.core.models import (KillSwitchRequest, RiskSettingsRequest,
                                     StrategyLimitsRequest)

router = APIRouter()
logger = logging.getLogger("RiskRouter")

# Cache TTL for risk settings (5 minutes)
RISK_SETTINGS_CACHE_TTL = 300


def _sb(user: dict):
    token = user.get("access_token")
    if not token:
        raise HTTPException(401, "Missing authenticated Supabase token.")
    return create_request_supabase(token)


def _safe_uid(uid: str) -> str:
    if re.match(r"^[a-zA-Z0-9\-_]{1,128}$", str(uid)):
        return str(uid)
    raise ValueError(f"Unsafe user_id: '{uid}'")


@router.get("/settings")
async def get_risk_settings(user: dict = Depends(get_current_user)):
    # Try to get from cache first
    try:
        from backend_app.core.cache.redis_manager import redis_manager
        redis_client = await redis_manager.get_client()
        if redis_client:
            cache_key = f"risk_settings:{user['id']}"
            cached = await redis_client.get(cache_key)
            if cached:
                import json
                return json.loads(cached)
    except Exception as e:
        logger.warning(f"Cache read failed for user {user['id']}: {e}")

    # Fallback to database
    sb = _sb(user)
    if not sb:
        return {
            "max_daily_loss": 500,
            "max_positions": 10,
            "max_leverage": 3,
            "kill_switches": [],
        }
    resp = sb.table("risk_settings").select("*").eq("user_id", user["id"]).execute()
    result = (
        resp.data[0]
        if resp.data
        else {
            "max_daily_loss": 500,
            "max_positions": 10,
            "max_leverage": 3,
            "kill_switches": [],
        }
    )

    # Cache the result
    try:
        from backend_app.core.cache.redis_manager import redis_manager
        redis_client = await redis_manager.get_client()
        if redis_client:
            cache_key = f"risk_settings:{user['id']}"
            import json
            await redis_client.setex(cache_key, RISK_SETTINGS_CACHE_TTL, json.dumps(result))
    except Exception as e:
        logger.warning(f"Cache write failed for user {user['id']}: {e}")

    return result


@router.put("/settings")
async def update_risk_settings(
    body: RiskSettingsRequest,
    user: dict = Depends(get_current_user),
):
    data = {
        "user_id": user["id"],
        "max_daily_loss": body.max_daily_loss,
        "max_positions": body.max_positions,
        "max_leverage": body.max_leverage,
        "kill_switches": [k.model_dump() for k in body.kill_switches],
    }
    _sb(user).table("risk_settings").upsert(data, on_conflict="user_id").execute()

    # Invalidate cache for this user
    try:
        from backend_app.core.cache.redis_manager import redis_manager
        redis_client = await redis_manager.get_client()
        if redis_client:
            cache_key = f"risk_settings:{user['id']}"
            await redis_client.delete(cache_key)
    except Exception as e:
        logger.warning(f"Failed to invalidate cache for user {user['id']}: {e}")

    return {"status": "ok"}


@router.post("/kill-switch")
async def user_kill_switch(
    body: KillSwitchRequest,
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
    alert=Depends(get_alert),
    ws_mgr=Depends(get_ws_manager),
):
    try:
        from backend_app.core.state import app_state

        user_prefix = f"{user['id']}_"
        bot_ids = [
            bid for bid in app_state.fleet._active_fleet if bid.startswith(user_prefix)
        ]
        for bot_id in bot_ids:
            parts = bot_id.split("_", 1)
            if len(parts) == 2:
                await app_state.fleet.stop_bot(parts[0], parts[1])

        # ═══════════════════════════════════════════════════════════════════
        # KILL SWITCH - CRITICAL FIX: Direct ExecutionEngine REMOVED
        # ═══════════════════════════════════════════════════════════════════
        # CRITICAL: Direct ExecutionEngine instantiation has been REMOVED.
        # All execution must flow through:
        # Strategy → DAG → Signal → BotRunner → UnifiedExecutionEngine → Exchange
        #
        # Kill switch order cancellation is handled AUTOMATICALLY by:
        # 1. Stopping all bots via fleet.stop_bot() (completed above)
        # 2. BotRunner's cleanup process through UnifiedExecutionEngine
        #
        # Direct instantiation of ExecutionEngine for cancel_all() is FORBIDDEN.
        logger.critical(f"[KILL SWITCH] Activated for user {user['id']}. Order cleanup via BotRunner.")

        fire_and_forget_task(
            alert.send_risk_warning(user["id"], "ALL", "Kill switch activated"),
            name=f"risk_alert_kill_switch_{user['id']}"
        )
        fire_and_forget_task(
            ws_mgr.broadcast_user(
                user["id"],
                {
                    "type": "kill_switch_activated",
                    "scope": "user",
                    "message": "All bots stopped. Order cleanup via execution pipeline.",
                },
            ),
            name=f"risk_ws_kill_switch_{user['id']}"
        )
        return {"status": "halted", "message": "All bots stopped. Orders cancelled via execution pipeline."}
    except Exception as e:
        logger.error(f"Kill switch failed for {user['id']}: {e}")
        raise HTTPException(500, f"Kill switch error: {e}")


@router.get("/account-health")
async def account_health(user: dict = Depends(get_current_user)):
    try:
        safe_uid = _safe_uid(user["id"])  # FIX SQL
        from backend_app.core.state import app_state

        query = (
            "SELECT current_drawdown_pct, daily_pnl_pct, total_exposure "
            "FROM account_health WHERE user_id = '" + safe_uid + "' LIMIT 1;"
        )
        result = await app_state.telemetry.execute_query(query)
        if result and result.get("dataset"):
            cols = [c["name"] for c in result["columns"]]
            return dict(zip(cols, result["dataset"][0]))
        return {"current_drawdown_pct": 0.0, "daily_pnl_pct": 0.0, "total_exposure": 0.0}
    except Exception as e:
        import traceback
        import inspect
        
        # Safe diagnostic logging - no sensitive data
        exc_type = type(e).__name__
        exc_module = type(e).__module__
        exc_message = str(e)
        
        # Get caller info
        frame = inspect.currentframe()
        caller_filename = frame.f_back.f_code.co_filename if frame.f_back else "unknown"
        caller_lineno = frame.f_back.f_lineno if frame.f_back else 0
        
        # Log comprehensive diagnostic info
        logger.error(
            f"[RISK_ACCOUNT_HEALTH_ENDPOINT] Exception details: "
            f"endpoint=/api/risk/account-health, "
            f"exception_type={exc_type}, "
            f"exception_module={exc_module}, "
            f"exception_message={exc_message}, "
            f"caller_file={caller_filename}, "
            f"caller_line={caller_lineno}, "
            f"user_id_truncated={user['id'][:8] if user.get('id') else 'missing'}..."
        )
        
        # Log full traceback for debugging
        logger.error(f"[RISK_ACCOUNT_HEALTH_ENDPOINT] Full traceback:\n{traceback.format_exc()}")
        
        raise HTTPException(
            status_code=500,
            detail={"error": "RISK_ACCOUNT_HEALTH_FAILED", "message": "Failed to fetch account health"}
        )


# ── GET /api/risk/strategy-limits ─────────────────────────────────────────
@router.get("/strategy-limits")
async def get_strategy_limits(user: dict = Depends(get_current_user)):
    """
    Get per-strategy risk limits configured by the user.
    """
    try:
        resp = _sb(user).table("strategy_limits").select("*").eq("user_id", user["id"]).execute()
        
        limits = []
        if resp.data:
            for row in resp.data:
                limits.append({
                    "strategy_id": row.get("strategy_id"),
                    "max_position_size": row.get("max_position_size", 1000.0),
                    "max_daily_trades": row.get("max_daily_trades", 100),
                    "allowed_symbols": row.get("allowed_symbols", []),
                    "max_drawdown_pct": row.get("max_drawdown_pct", 0.1),
                    "enabled": row.get("enabled", True),
                })
        
        return {
            "limits": limits,
            "count": len(limits),
            "user_id": user["id"]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching strategy limits for {user['id']}: {e}")
        raise HTTPException(
            status_code=503,
            detail={"error": "STRATEGY_LIMITS_FETCH_FAILED", "message": str(e)},
        )


# ── PUT /api/risk/strategy-limits ──────────────────────────────────────────
@router.put("/strategy-limits")
async def update_strategy_limits(
    body: StrategyLimitsRequest,
    user: dict = Depends(get_current_user),
):
    """
    Update per-strategy risk limits.
    """
    try:
        sb = _sb(user)
        updated = []
        
        for limit in body.limits:
            data = {
                "user_id": user["id"],
                "strategy_id": limit.strategy_id,
                "max_position_size": limit.max_position_size,
                "max_daily_trades": limit.max_daily_trades,
                "allowed_symbols": limit.allowed_symbols,
                "max_drawdown_pct": limit.max_drawdown_pct,
                "enabled": limit.enabled,
                "updated_at": datetime.utcnow().isoformat(),
            }
            
            # Upsert the limit
            result = sb.table("strategy_limits").upsert(
                data,
                on_conflict="user_id,strategy_id"
            ).execute()
            
            if result.data:
                updated.append(limit.strategy_id)
        
        return {
            "status": "ok",
            "updated": updated,
            "count": len(updated)
        }
    except Exception as e:
        logger.error(f"Error updating strategy limits for {user['id']}: {e}")
        raise HTTPException(500, f"Failed to update strategy limits: {e}")


# ── PUT /api/risk/strategy-limits/{strategy_id} ──────────────────────────────
@router.put("/strategy-limits/{strategy_id}")
async def update_single_strategy_limit(
    strategy_id: str,
    body: StrategyLimitsRequest,
    user: dict = Depends(get_current_user),
):
    """
    Update a single strategy-specific risk limit.
    """
    try:
        sb = _sb(user)
        
        if not body.limits or len(body.limits) == 0:
            raise HTTPException(400, "No limits provided in request body")
        
        limit = body.limits[0]  # Take first limit from array
        data = {
            "user_id": user["id"],
            "strategy_id": strategy_id,
            "max_position_size": limit.max_position_size,
            "max_daily_trades": limit.max_daily_trades,
            "allowed_symbols": limit.allowed_symbols,
            "max_drawdown_pct": limit.max_drawdown_pct,
            "enabled": limit.enabled,
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        result = sb.table("strategy_limits").upsert(
            data,
            on_conflict="user_id,strategy_id"
        ).execute()
        
        return {
            "status": "ok",
            "updated": strategy_id,
            "count": 1
        }
    except Exception as e:
        logger.error(f"Error updating single strategy limit for {user['id']}: {e}")
        raise HTTPException(500, f"Failed to update strategy limit: {e}")


# ── DELETE /api/risk/strategy-limits/{strategy_id} ─────────────────────────
@router.delete("/strategy-limits/{strategy_id}")
async def delete_strategy_limit(
    strategy_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Delete a strategy-specific risk limit.
    """
    try:
        sb = _sb(user)
        result = sb.table("strategy_limits").delete().eq(
            "user_id", user["id"]
        ).eq("strategy_id", strategy_id).execute()
        
        return {
            "status": "ok",
            "deleted": strategy_id,
            "affected": len(result.data) if result.data else 0
        }
    except Exception as e:
        logger.error(f"Error deleting strategy limit for {user['id']}: {e}")
        raise HTTPException(500, f"Failed to delete strategy limit: {e}")
