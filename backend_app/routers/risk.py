"""
routers/risk.py — Production-Grade Risk Management & Risk Settings API.

Comprehensive End-to-End Features:
- User-level & Tenant-level risk configuration (max daily loss, max positions, leverage, kill switches)
- Real-time risk status & utilization metrics (daily loss vs limit, position utilization, drawdown)
- Emergency kill-switch activation and recovery
- Strategy-specific capital allocation and trade limits
- Audit logging of risk violations and policy enforcement
- WebSocket real-time event broadcasting
- Cache invalidation and multi-tenant security
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import inspect
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, validator

from backend_app.core.dependencies import (
    create_request_supabase_async, get_alert, get_current_user, get_vault, get_ws_manager
)
from backend_app.core.background_tasks import fire_and_forget_task
from backend_app.core.cache.redis_manager import redis_manager

router = APIRouter()
logger = logging.getLogger("RiskRouter")

RISK_SETTINGS_CACHE_TTL = 300

# ═══════════════════════════════════════════════════════════════════════════
# IN-MEMORY STORE (Fallback & High-Performance Server-Authoritative State)
# ═══════════════════════════════════════════════════════════════════════════

_user_risk_settings: Dict[str, Dict[str, Any]] = {}
_user_strategy_limits: Dict[str, Dict[str, Dict[str, Any]]] = {} # user_id -> {strategy_id: limit_dict}
_user_kill_switch_state: Dict[str, bool] = {} # user_id -> is_active
_risk_violations_log: Dict[str, List[Dict[str, Any]]] = {} # user_id -> list of violations


def get_user_risk_settings_store(user_id: str) -> Dict[str, Any]:
    uid = str(user_id)
    if uid not in _user_risk_settings:
        _user_risk_settings[uid] = {
            "max_daily_loss": 500.0,
            "max_positions": 10,
            "max_leverage": 3,
            "circuit_breaker_armed": True,
            "circuit_breaker_breaches": 0,
            "kill_switches": {
                "loss": True,
                "blackswan": True,
                "streak": False,
                "capital": True
            },
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
    return _user_risk_settings[uid]


def is_user_kill_switched(user_id: str) -> bool:
    return _user_kill_switch_state.get(str(user_id), False)


def record_risk_violation(user_id: str, rule: str, reason: str, details: Optional[Dict[str, Any]] = None) -> None:
    uid = str(user_id)
    entry = {
        "id": f"violation_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
        "user_id": uid,
        "rule": rule,
        "reason": reason,
        "details": details or {},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    _risk_violations_log.setdefault(uid, []).append(entry)
    logger.warning(f"[RISK_VIOLATION] user={uid} rule={rule} reason={reason}")


def _safe_uid(uid: str) -> str:
    if re.match(r"^[a-zA-Z0-9\-_]{1,128}$", str(uid)):
        return str(uid)
    raise ValueError(f"Unsafe user_id: '{uid}'")


async def _sb(user: dict):
    token = user.get("access_token")
    if not token:
        return None
    try:
        res = create_request_supabase_async(token)
        return await res if inspect.isawaitable(res) else res
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════

class RiskSettingsUpdateRequest(BaseModel):
    max_daily_loss: Optional[float] = Field(500.0, gt=0, le=1_000_000, description="Max daily loss in USD")
    max_positions: Optional[int] = Field(10, gt=0, le=100, description="Max concurrent open positions")
    max_leverage: Optional[int] = Field(3, ge=1, le=50, description="Max allowed account leverage")
    circuit_breaker_armed: Optional[bool] = Field(True, description="Armed state of institutional circuit breaker")
    kill_switches: Optional[Any] = Field(default=None, description="Kill switch configurations")


class RiskValidateRequest(BaseModel):
    max_daily_loss: Optional[float] = None
    max_positions: Optional[int] = None
    max_leverage: Optional[int] = None


class UserKillSwitchRequest(BaseModel):
    scope: str = "user"
    confirm_code: Optional[str] = "CONFIRM"
    reason: Optional[str] = "Emergency manual halt triggered"


# ═══════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/settings")
async def get_risk_settings(user: dict = Depends(get_current_user)):
    """
    Get authoritative risk settings for authenticated user.
    """
    uid = str(user.get("id") or user.get("sub"))
    
    # Try Cache
    try:
        redis_client = await redis_manager.get_client()
        if redis_client:
            cached = await redis_client.get(f"risk_settings:{uid}")
            if cached:
                return json.loads(cached)
    except Exception as e:
        logger.debug(f"Redis cache read skipped for user {uid}: {e}")

    # Memory / Database authoritative store
    settings = get_user_risk_settings_store(uid)

    # Populate from Supabase if table exists
    sb = await _sb(user)
    if sb:
        try:
            q = sb.table("risk_settings").select("*").eq("user_id", uid).limit(1).execute()
            resp = await q if inspect.isawaitable(q) else q
            if resp and hasattr(resp, "data") and resp.data:
                row = resp.data[0]
                settings.update({
                    "max_daily_loss": float(row.get("max_daily_loss", settings["max_daily_loss"])),
                    "max_positions": int(row.get("max_positions", settings["max_positions"])),
                    "max_leverage": int(row.get("max_leverage", settings["max_leverage"])),
                    "circuit_breaker_armed": bool(row.get("circuit_breaker_armed", True)),
                })
        except Exception as e:
            logger.debug(f"DB fetch fallback to memory store for user {uid}: {e}")

    return settings


@router.put("/settings")
async def update_risk_settings(
    body: RiskSettingsUpdateRequest,
    user: dict = Depends(get_current_user),
    ws_mgr = Depends(get_ws_manager),
):
    """
    Validate and save updated risk configuration.
    """
    uid = str(user.get("id") or user.get("sub"))

    if body.max_daily_loss is not None and body.max_daily_loss <= 0:
        raise HTTPException(400, "Max daily loss must be greater than 0")
    if body.max_positions is not None and body.max_positions <= 0:
        raise HTTPException(400, "Max positions must be greater than 0")
    if body.max_leverage is not None and body.max_leverage < 1:
        raise HTTPException(400, "Max leverage must be at least 1")

    # Format kill switches
    current_settings = get_user_risk_settings_store(uid)
    ks_dict = current_settings.get("kill_switches", {})
    if isinstance(body.kill_switches, dict):
        ks_dict = body.kill_switches
    elif isinstance(body.kill_switches, list):
        for item in body.kill_switches:
            if isinstance(item, dict) and "key" in item:
                ks_dict[item["key"]] = bool(item.get("active", item.get("enabled", True)))
            elif hasattr(item, "key"):
                ks_dict[item.key] = bool(getattr(item, "active", getattr(item, "enabled", True)))

    updated_record = {
        "user_id": uid,
        "max_daily_loss": float(body.max_daily_loss if body.max_daily_loss is not None else current_settings["max_daily_loss"]),
        "max_positions": int(body.max_positions if body.max_positions is not None else current_settings["max_positions"]),
        "max_leverage": int(body.max_leverage if body.max_leverage is not None else current_settings["max_leverage"]),
        "circuit_breaker_armed": bool(body.circuit_breaker_armed if body.circuit_breaker_armed is not None else current_settings.get("circuit_breaker_armed", True)),
        "kill_switches": ks_dict,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    _user_risk_settings[uid] = updated_record

    # Persist to database if available
    sb = await _sb(user)
    if sb:
        try:
            q = sb.table("risk_settings").upsert(updated_record, on_conflict="user_id").execute()
            await q if inspect.isawaitable(q) else q
        except Exception as e:
            logger.warning(f"Database persist for risk_settings skipped: {e}")

    # Invalidate Redis cache
    try:
        redis_client = await redis_manager.get_client()
        if redis_client:
            await redis_client.delete(f"risk_settings:{uid}")
    except Exception as e:
        logger.debug(f"Redis cache delete skipped: {e}")

    # Emit real-time WebSocket event
    try:
        fire_and_forget_task(
            ws_mgr.broadcast_user(
                uid,
                {
                    "type": "risk.settings.updated",
                    "settings": updated_record,
                    "timestamp": updated_record["updated_at"]
                }
            ),
            name=f"risk_ws_update_{uid}"
        )
    except Exception as e:
        logger.debug(f"WebSocket broadcast skipped: {e}")

    try:
        from backend_app.core.notification_dispatcher import dispatch_user_notification
        await dispatch_user_notification(
            user_id=uid,
            event_type="risk_settings_updated",
            category="risk",
            severity="info",
            title="Risk Parameters Updated",
            message=f"Risk controls updated (Max Loss: ${updated_record['max_daily_loss']:,}, Max Positions: {updated_record['max_positions']}).",
            metadata={"settings": updated_record, "idempotency_key": f"risk_settings:{uid}"},
            ws_manager=ws_mgr,
        )
    except Exception as notif_err:
        logger.debug(f"[RISK] Notification dispatch error: {notif_err}")

    return {
        "status": "ok",
        "message": "Risk settings saved successfully",
        "data": updated_record,
        **updated_record
    }


@router.post("/validate")
async def validate_risk_configuration(
    body: RiskValidateRequest,
    user: dict = Depends(get_current_user)
):
    """
    Validates proposed risk settings without saving.
    """
    errors = []
    if body.max_daily_loss is not None and body.max_daily_loss <= 0:
        errors.append("max_daily_loss must be > 0")
    if body.max_positions is not None and (body.max_positions <= 0 or body.max_positions > 100):
        errors.append("max_positions must be between 1 and 100")
    if body.max_leverage is not None and (body.max_leverage < 1 or body.max_leverage > 50):
        errors.append("max_leverage must be between 1 and 50")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
    }


@router.get("/status")
async def get_risk_status(user: dict = Depends(get_current_user)):
    """
    Get live risk metrics, utilization percentages, and status badge.
    """
    uid = str(user.get("id") or user.get("sub"))
    settings = get_user_risk_settings_store(uid)
    max_loss = settings["max_daily_loss"]
    max_pos = settings["max_positions"]
    
    # Query current positions and paper/live account performance.
    #
    # marketplace-subscriptions-paper-trading task 23.5: these two reads reach the **persisted**
    # Paper_Account (Requirement 25.5). The arithmetic below is untouched - only where its inputs
    # come from changed. Both reads carry the caller's token so the row-level-security policies on
    # the ``paper_*`` tables apply to the identity that made the request, and both are scoped to
    # ``uid``. A refusal - 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` when 009 is unapplied, 503
    # ``PAPER_READ_FAILED`` when a statement did not complete - is deliberately **not** caught
    # here: an unreadable account is an outage, and answering 200 with a default balance would
    # show a trader a healthy margin they may not have (Requirements 17.2, 28.3).
    from backend_app.backend.paper_trading_service import get_paper_trading_service
    paper_svc = get_paper_trading_service()
    paper_summary = paper_svc.get_performance_summary(uid, access_token=user.get("access_token"))
    acct = paper_summary.get("account", {})
    
    realized_loss = float(-min(Decimal("0"), Decimal(str(acct.get("realized_pnl", "0")))))
    loss_utilization_pct = round((realized_loss / max_loss * 100), 2) if max_loss > 0 else 0.0
    # A fully closed position persists at ``size = 0`` instead of being deleted (Requirement
    # 18.5), so "open" is a predicate on size here as well as in the read. The read already
    # filters ``closed_at IS NULL`` in the statement and drops a zero-size row in Python, and
    # this is the second, independent boundary: a row left at ``size = 0`` with ``closed_at``
    # still null - what a process that stopped between the fill insert and the close marker
    # leaves behind - must not be reported as an open position, because at ``max_positions`` it
    # would block an order. Compared as ``Decimal``, never through binary float.
    open_positions = paper_svc.get_positions(uid, access_token=user.get("access_token"))
    open_pos_count = len([
        position for position in open_positions
        if Decimal(str(position.get("size", "0"))) > Decimal("0")
    ])
    pos_utilization_pct = round((open_pos_count / max_pos * 100), 2) if max_pos > 0 else 0.0
    
    kill_active = is_user_kill_switched(uid)
    
    # Status calculation
    if kill_active:
        risk_level = "BLOCKED"
    elif loss_utilization_pct >= 100 or pos_utilization_pct >= 100:
        risk_level = "BLOCKED"
    elif loss_utilization_pct >= 85 or pos_utilization_pct >= 85:
        risk_level = "CRITICAL"
    elif loss_utilization_pct >= 60 or pos_utilization_pct >= 60:
        risk_level = "WARNING"
    else:
        risk_level = "SAFE"

    return {
        "status": risk_level,
        "kill_switch_active": kill_active,
        "daily_loss": {
            "current": realized_loss,
            "max": max_loss,
            "utilization_pct": loss_utilization_pct
        },
        "positions": {
            "current": open_pos_count,
            "max": max_pos,
            "utilization_pct": pos_utilization_pct
        },
        "leverage": {
            "configured_max": settings["max_leverage"],
            "current": 1.0
        },
        "drawdown_pct": 0.0,
        "circuit_breaker_armed": settings.get("circuit_breaker_armed", True),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/margin-health")
async def get_margin_health(user: dict = Depends(get_current_user)):
    """
    Get live margin health and overall risk score.
    """
    uid = str(user.get("id") or user.get("sub"))
    # Task 23.5: the persisted Paper_Account, scoped to ``uid`` and read under the caller's token.
    # ``get_or_create_account`` raises rather than returning a default when the Persistence_Layer
    # cannot answer, and that refusal travels to the client as its catalogued 503 (Requirement
    # 28.3) - there is no ``except`` here for it to be swallowed by.
    from backend_app.backend.paper_trading_service import get_paper_trading_service
    paper_svc = get_paper_trading_service()
    acct = paper_svc.get_or_create_account(uid, access_token=user.get("access_token"))
    
    # Exact decimal, parsed from the stored text: money must not round-trip through binary float
    # (Requirements 8.13, 16.12, 18.1). The ``.get`` defaults are retained from before the
    # repoint and are unreachable - the account body always carries all three keys - and are
    # spelled as strings so the parse is uniform.
    avail = Decimal(str(acct.get("available_balance", "100000.0")))
    locked = Decimal(str(acct.get("locked_balance", "0.0")))
    total = Decimal(str(acct.get("total_equity", "100000.0")))
    
    # The same two ratios and the same score. ``float`` at the response boundary only, so the
    # JSON stays ``{"margin_ratio": 9.76, "free_margin": 68.33, "risk_score": 14}``.
    margin_ratio = float(round((locked / total * 100), 2)) if total > 0 else 0.0
    free_margin = float(round((avail / total * 100), 2)) if total > 0 else 100.0
    risk_score = min(100, int(margin_ratio * 0.8 + (100 - free_margin) * 0.2))

    return {
        "margin_ratio": margin_ratio,
        "free_margin": free_margin,
        "risk_score": risk_score,
        "currency": "USD"
    }


@router.post("/kill-switch")
async def activate_kill_switch(
    body: Optional[UserKillSwitchRequest] = None,
    user: dict = Depends(get_current_user),
    ws_mgr = Depends(get_ws_manager)
):
    """
    Instantly halt trading and activate user-level emergency kill switch.
    """
    if body is None:
        body = UserKillSwitchRequest()
    uid = str(user.get("id") or user.get("sub"))
    _user_kill_switch_state[uid] = True
    record_risk_violation(uid, "KILL_SWITCH_MANUAL_ACTIVATION", body.reason or "Emergency kill switch activated by user")

    fire_and_forget_task(
        ws_mgr.broadcast_user(
            uid,
            {
                "type": "risk.kill_switch_activated",
                "scope": "user",
                "message": "Emergency Kill Switch Activated: All new executions blocked.",
                "reason": body.reason,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ),
        name=f"risk_ws_kill_{uid}"
    )

    try:
        from backend_app.core.notification_dispatcher import dispatch_user_notification
        await dispatch_user_notification(
            user_id=uid,
            event_type="kill_switch_activated",
            category="risk",
            severity="critical",
            title="Emergency Kill Switch Activated",
            message="Emergency Kill Switch is ACTIVE. All new trading and strategy executions are halted.",
            metadata={"reason": body.reason or "Manual kill switch triggered", "idempotency_key": f"kill_switch_active:{uid}"},
            ws_manager=ws_mgr,
        )
    except Exception as notif_err:
        logger.debug(f"[RISK] Notification dispatch error: {notif_err}")

    logger.critical(f"[KILL SWITCH ACTIVATED] User {uid} has activated emergency kill switch.")
    return {
        "status": "halted",
        "kill_switch_active": True,
        "message": "Emergency Kill Switch is ACTIVE. All new strategy and order executions are halted."
    }


@router.post("/kill-switch/recover")
async def recover_kill_switch(
    user: dict = Depends(get_current_user),
    ws_mgr = Depends(get_ws_manager)
):
    """
    Deactivate emergency kill switch and resume normal trading operations.
    """
    uid = str(user.get("id") or user.get("sub"))
    _user_kill_switch_state[uid] = False

    fire_and_forget_task(
        ws_mgr.broadcast_user(
            uid,
            {
                "type": "risk.kill_switch_recovered",
                "scope": "user",
                "message": "Emergency Kill Switch Deactivated: Trading resumed.",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ),
        name=f"risk_ws_recover_{uid}"
    )

    try:
        from backend_app.core.notification_dispatcher import dispatch_user_notification
        await dispatch_user_notification(
            user_id=uid,
            event_type="kill_switch_recovered",
            category="risk",
            severity="info",
            title="Trading Operations Resumed",
            message="Emergency Kill Switch deactivated. Strategy and order executions are now active.",
            metadata={"idempotency_key": f"kill_switch_recover:{uid}"},
            ws_manager=ws_mgr,
        )
    except Exception as notif_err:
        logger.debug(f"[RISK] Notification dispatch error: {notif_err}")

    logger.info(f"[KILL SWITCH RECOVERED] User {uid} has deactivated emergency kill switch.")
    return {
        "status": "active",
        "kill_switch_active": False,
        "message": "Kill switch recovered. Trading is active."
    }


@router.get("/violations")
async def get_risk_violations(
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user)
):
    """
    Get audit history of risk rejections and policy violations for user.
    """
    uid = str(user.get("id") or user.get("sub"))
    violations = _risk_violations_log.get(uid, [])
    return {
        "violations": sorted(violations, key=lambda x: x.get("timestamp", ""), reverse=True)[:limit],
        "count": len(violations)
    }


# ── STRATEGY LIMITS ───────────────────────────────────────────────────────

class StrategyLimitItem(BaseModel):
    strategy_id: Optional[str] = None
    max_position_size: Optional[float] = Field(1000.0, gt=0)
    max_daily_trades: Optional[int] = Field(100, gt=0)
    allowed_symbols: Optional[List[str]] = Field(default_factory=list)
    max_drawdown_pct: Optional[float] = Field(0.1, ge=0, le=1)
    enabled: Optional[bool] = True


class StrategyLimitsPayload(BaseModel):
    limits: Optional[List[StrategyLimitItem]] = None
    max_position_size: Optional[float] = None
    max_daily_trades: Optional[int] = None
    allowed_symbols: Optional[List[str]] = None
    max_drawdown_pct: Optional[float] = None
    enabled: Optional[bool] = None


@router.get("/strategy-limits")
async def get_strategy_limits(user: dict = Depends(get_current_user)):
    """
    Get per-strategy risk limits configured by the user.
    """
    uid = str(user.get("id") or user.get("sub"))
    user_limits = _user_strategy_limits.get(uid, {})
    limits_list = list(user_limits.values())
    return {
        "limits": limits_list,
        "count": len(limits_list),
        "user_id": uid
    }


@router.put("/strategy-limits")
async def update_strategy_limits(
    body: StrategyLimitsPayload,
    user: dict = Depends(get_current_user)
):
    """
    Batch update per-strategy risk limits.
    """
    uid = str(user.get("id") or user.get("sub"))
    store = _user_strategy_limits.setdefault(uid, {})
    updated = []
    if body.limits:
        for item in body.limits:
            sid = item.strategy_id or f"strat_{len(store)+1}"
            data = item.dict(exclude_none=True)
            data["strategy_id"] = sid
            store[sid] = data
            updated.append(sid)

    return {
        "status": "ok",
        "updated": updated,
        "count": len(updated)
    }


@router.put("/strategy-limits/{strategy_id}")
async def update_single_strategy_limit(
    strategy_id: str,
    body: Union[StrategyLimitsPayload, Dict[str, Any]],
    user: dict = Depends(get_current_user)
):
    """
    Update a single strategy-specific risk limit.
    Supports direct field payload e.g. { "max_position_size": 25.0 } or nested { "limits": [...] }.
    """
    uid = str(user.get("id") or user.get("sub"))
    store = _user_strategy_limits.setdefault(uid, {})
    
    if isinstance(body, dict):
        limit_data = body.copy()
    elif hasattr(body, "limits") and body.limits:
        limit_data = body.limits[0].dict(exclude_none=True) if hasattr(body.limits[0], "dict") else body.limits[0]
    elif hasattr(body, "dict"):
        limit_data = body.dict(exclude_none=True)
    else:
        limit_data = {}

    limit_data["strategy_id"] = str(strategy_id)
    store[str(strategy_id)] = limit_data
    return {
        "status": "ok",
        "updated": str(strategy_id),
        "data": limit_data,
        "count": 1
    }


@router.delete("/strategy-limits/{strategy_id}")
async def delete_strategy_limit(
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Delete a strategy-specific risk limit.
    """
    uid = str(user.get("id") or user.get("sub"))
    store = _user_strategy_limits.get(uid, {})
    deleted = store.pop(str(strategy_id), None)
    return {
        "status": "ok",
        "deleted": str(strategy_id),
        "affected": 1 if deleted else 0
    }
