"""
routers/notifications.py — Notification Management

Provides CRUD endpoints for user notifications with pagination, filtering, and real-time WebSocket delivery.
Enforces multi-tenant data isolation and IDOR protection.
"""

import asyncio
import inspect
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend_app.core.dependencies import get_current_user, get_request_supabase, get_ws_manager
from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)
router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════════════════

class NotificationCreate(BaseModel):
    """Request model for creating a notification (internal use)."""
    user_id: str
    type: str = Field(..., description="Notification type")
    category: str = Field(..., description="Category: trade, strategy, risk, security, billing, system, support, exchange")
    severity: str = Field("info", description="Severity: info, warning, critical, emergency")
    title: str = Field(..., description="Notification title")
    message: str = Field(..., description="Notification message")
    strategy_id: Optional[str] = None
    exchange: Optional[str] = None
    metadata: Optional[dict] = None


class NotificationResponse(BaseModel):
    """Response model for a notification."""
    id: str
    user_id: str
    type: str
    category: str
    severity: str
    title: str
    message: str
    strategy_id: Optional[str] = None
    exchange: Optional[str] = None
    metadata: Optional[dict] = None
    read: bool
    created_at: str


class NotificationListResponse(BaseModel):
    """Response model for notification list."""
    items: List[NotificationResponse]
    total: int
    unread_count: int
    limit: int
    offset: int


class NotificationSettingsModel(BaseModel):
    """Settings model for user notifications."""
    email_notifications: bool = True
    push_notifications: bool = True
    trade_alerts: bool = True
    risk_alerts: bool = True
    system_alerts: bool = True


# ══════════════════════════════════════════════════════════════════════════
# IN-MEMORY RESILIENT STORAGE FOR DEV / TEST / DB-DECOUPLING
#
# HY-04: this used to be a plain, unbounded dict -- one entry retained forever for
# every notification ever created in this process's lifetime, across every tenant,
# whether or not it was also durably persisted to Supabase (`list_notifications` and
# friends below read Supabase FIRST and only fall back to this dict when Supabase has
# nothing, so a persisted entry's in-memory copy is pure, unbounded-growth dead
# weight). The idempotency check also scanned every single entry on every single
# notification created (`for existing_id, existing_data in
# _in_memory_notifications.items(): ...`), so it got slower as the unbounded dict
# grew, not just memory-hungry.
#
# Fixed with: (1) a hard cap (_MAX_IN_MEMORY_NOTIFICATIONS, env-overridable), evicting
# the OLDEST entries once exceeded -- plain dicts preserve insertion order (Python
# 3.7+) and no code here ever re-inserts an existing key, so the first key is reliably
# the oldest; (2) an O(1) idempotency index (`_idempotency_index`) keyed by
# `(user_id, idempotency_key)`, replacing the full-dict scan, kept in sync on insert,
# eviction and delete.
# ══════════════════════════════════════════════════════════════════════════

_notifications_lock = threading.Lock()
_in_memory_notifications: Dict[str, Dict[str, Any]] = {}
_in_memory_settings: Dict[str, Dict[str, Any]] = {}

#: Each entry is a handful of short strings/dicts -- a few hundred bytes -- so 5,000
#: is a low-single-digit-MB ceiling, not a capacity promise. This is a FALLBACK cache,
#: not the source of truth (Supabase is, when reachable); the cap exists to turn
#: unbounded growth into bounded, oldest-first data loss, never into an OOM.
_MAX_IN_MEMORY_NOTIFICATIONS: int = int(os.environ.get("MAX_IN_MEMORY_NOTIFICATIONS", "5000"))

#: (user_id, idempotency_key) -> notification_id. O(1) replacement for the full-dict
#: scan the idempotency check used to do on every create.
_idempotency_index: Dict[tuple, str] = {}


def _idempotency_index_key(notif: Dict[str, Any]) -> Optional[tuple]:
    key = (notif.get("metadata") or {}).get("idempotency_key")
    if not key:
        return None
    return (notif.get("user_id"), key)


def _forget_notification_locked(notification_id: str) -> None:
    """Remove a notification and its idempotency index entry. Caller holds the lock."""
    notif = _in_memory_notifications.pop(notification_id, None)
    if notif is None:
        return
    idx_key = _idempotency_index_key(notif)
    if idx_key is not None and _idempotency_index.get(idx_key) == notification_id:
        del _idempotency_index[idx_key]


def _evict_oldest_until_within_capacity_locked() -> None:
    """Caller holds the lock. Evicts the OLDEST entries (insertion order) until the
    store is back at or under _MAX_IN_MEMORY_NOTIFICATIONS."""
    while len(_in_memory_notifications) > _MAX_IN_MEMORY_NOTIFICATIONS:
        oldest_id = next(iter(_in_memory_notifications))
        _forget_notification_locked(oldest_id)


def _is_duplicate_notification(exc: Exception) -> bool:
    """Is this a unique violation on uq_notifications_user_idempotency?

    NOTIF-01. Migration 071 is what makes duplicate suppression durable, so a
    collision here means "this event was already delivered" -- the desired end
    state -- rather than a lost notification.

    Narrow on purpose, and the same shape as
    ``core/webhook_idempotency._is_unique_violation`` and
    ``routers/billing._is_duplicate_invoice``: the client surfaces a PostgREST error
    whose class varies by version, so SQLSTATE and message are both checked rather
    than the exception type. Anything not recognisably a uniqueness conflict is
    treated as a real failure, because misreading a genuine write error as "already
    delivered" would drop a notification and log nothing.
    """
    code = getattr(exc, "code", None)
    if code == "23505":
        return True
    text = str(exc).lower()
    return "23505" in text or "duplicate key" in text or (
        "already exists" in text and "uq_notifications_user_idempotency" in text
    )


async def _existing_notification_id(
    supabase: Any, user_id: str, idempotency_key: str
) -> Optional[str]:
    """The id of the notification that already claimed this (user, key).

    Read back rather than invented: the caller may use the returned id, and the
    first delivery is the one that actually happened. Returns ``None`` if the row
    cannot be read, in which case the caller falls through to its failure path
    rather than fabricating an id.
    """
    try:
        res = (
            supabase.table("notifications")
            .select("id")
            .eq("user_id", user_id)
            .eq("idempotency_key", idempotency_key)
            .limit(1)
            .execute()
        )
        result = await res if inspect.isawaitable(res) else res
        rows = getattr(result, "data", None) or []
        return rows[0].get("id") if rows else None
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[NOTIFICATIONS] could not read back the de-duplicated notification "
            "for user %s: %s", user_id, exc,
        )
        return None


# ══════════════════════════════════════════════════════════════════════════
# NOTIFICATION SERVICE
# ══════════════════════════════════════════════════════════════════════════

async def create_notification(
    notification: NotificationCreate,
    supabase: Optional[SupabaseClient] = None,
    ws_manager: Optional[Any] = None
) -> str:
    """
    Create a notification, persist to DB (with thread-safe memory fallback),
    and broadcast via WebSocket. Non-blocking and resilient to transport errors.
    """
    idempotency_key = (notification.metadata or {}).get("idempotency_key")
    if idempotency_key:
        with _notifications_lock:
            existing_id = _idempotency_index.get((notification.user_id, idempotency_key))
            if existing_id is not None and existing_id in _in_memory_notifications:
                return existing_id

    #: The id used ONLY when this notification does not reach the database. The
    #: `notif_` prefix is deliberate and pinned by
    #: tests/test_notifications_feature_e2e.py -- it marks an id that exists in this
    #: process and nowhere else.
    notification_id = f"notif_{uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()

    # ══════════════════════════════════════════════════════════════════════
    # NOTIF-01: `id` IS NOT SENT. THE COLUMN ALREADY GENERATES IT.
    # ══════════════════════════════════════════════════════════════════════
    # `notifications.id` is `uuid NOT NULL DEFAULT gen_random_uuid()`, and this
    # payload used to send `"id": "notif_<12 hex>"`. That is not a uuid, so Postgres
    # rejected the whole insert with `invalid input syntax for type uuid` -- EVERY
    # time, for every notification, on both databases. Combined with the dispatcher
    # defect (18 of 19 call sites passed no client at all), it is the complete
    # explanation for production holding ZERO notification rows.
    #
    # Omitting the key lets the column's own default produce a real uuid, which is
    # then read back below. The local `notification_id` above stays as the
    # in-memory fallback id, so the two stores keep distinguishable id shapes and
    # the existing tests that assert the `notif_` prefix still describe exactly
    # what they did before: a notification that never reached the database.
    data = {
        "user_id": notification.user_id,
        "type": notification.type,
        "category": notification.category,
        "severity": notification.severity,
        "title": notification.title,
        "message": notification.message,
        "strategy_id": notification.strategy_id,
        "exchange": notification.exchange,
        "metadata": notification.metadata or {},
        "read": False,
        "created_at": now_iso,
        # NOTIF-01: promoted out of `metadata` so migration 071's partial unique
        # index can enforce it. Still written inside `metadata` too (above), which
        # is what makes 071 cheaply reversible -- see its rollback header.
        "idempotency_key": idempotency_key or None,
    }

    persisted = False
    if supabase:
        try:
            res = supabase.table("notifications").insert(data).execute()
            result = await res if inspect.isawaitable(res) else res
            if result and hasattr(result, "data") and result.data:
                data = result.data[0]
                notification_id = data.get("id", notification_id)
                persisted = True
        except Exception as sb_err:
            # ══════════════════════════════════════════════════════════════
            # NOTIF-01: A COLLISION IS THE DEDUPE WORKING, NOT A FAILURE
            # ══════════════════════════════════════════════════════════════
            # `uq_notifications_user_idempotency` makes the duplicate check
            # durable: the dict consulted at the top of this function dies on
            # restart, cannot see another task's entries, and evicts at 5,000, so
            # before 071 a redelivered event produced a second notification in all
            # three of those situations. Postgres now refuses it.
            #
            # The first notification is the one that happened, so the correct
            # answer is its id -- read back over the same client rather than
            # invented, because the caller may use it.
            recovered = None
            if idempotency_key and _is_duplicate_notification(sb_err):
                recovered = await _existing_notification_id(
                    supabase, notification.user_id, idempotency_key
                )
                if recovered:
                    logger.info(
                        "[NOTIFICATIONS] NOTIF-01: event '%s' for user %s already "
                        "delivered as %s; redelivery de-duplicated.",
                        notification.type, notification.user_id, recovered,
                    )
                    return recovered

            # A real failure. Level matches what was lost: an actionable alert
            # that never persisted is the NOTIF-01 defect happening, and DEBUG is
            # not enabled in production, so it would leave no trace at all.
            if str(notification.severity).lower() in ("warning", "error", "critical"):
                logger.error(
                    "[NOTIFICATIONS] %s notification '%s' for user %s was NOT "
                    "persisted (%s: %s). It survives only in this process and is "
                    "lost on restart.",
                    notification.severity, notification.type,
                    notification.user_id, type(sb_err).__name__, sb_err,
                )
            else:
                logger.debug(f"[NOTIFICATIONS] Supabase insert fallback: {sb_err}")

    # NOTIF-01: `id` is no longer part of the INSERT payload (the column generates
    # it), so it has to be put back before this dict becomes the in-memory record --
    # the listing endpoints read `id` straight off it, and without this they answer
    # the literal string "None" for every notification served from memory.
    #
    # `setdefault`, not assignment: when the insert succeeded, `data` is the row
    # Postgres returned and already carries the real uuid. Overwriting it with the
    # local `notif_`-prefixed id would make the memory copy disagree with the
    # database about the identity of the same notification.
    data.setdefault("id", notification_id)

    with _notifications_lock:
        _in_memory_notifications[notification_id] = data
        if idempotency_key:
            _idempotency_index[(notification.user_id, idempotency_key)] = notification_id
        _evict_oldest_until_within_capacity_locked()

    # Real-time WebSocket delivery
    if ws_manager:
        try:
            await ws_manager.broadcast_user(
                notification.user_id,
                {
                    "type": "notification",
                    "data": {
                        **data
                    }
                }
            )
            logger.info(f"[NOTIFICATIONS] Broadcasted notification {notification_id} to user {notification.user_id}")
        except Exception as ws_err:
            logger.debug(f"[NOTIFICATIONS] WebSocket broadcast error: {ws_err}")

    return notification_id


# ══════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════

@router.get("", response_model=NotificationListResponse)
@router.get("/", response_model=NotificationListResponse)
async def list_notifications(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    unread_only: bool = Query(False),
    category: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """
    List user notifications with pagination, filtering, and strict tenant isolation.
    """
    user_id = user["id"]
    items = []
    total = 0
    unread_count = 0

    if supabase:
        try:
            columns = "id, user_id, type, category, severity, title, message, strategy_id, exchange, metadata, read, created_at"
            query = supabase.table("notifications").select(columns, count="exact").eq("user_id", user_id)
            if unread_only:
                query = query.eq("read", False)
            if category and category.lower() != "all":
                query = query.eq("category", category)
            query = query.order("created_at", desc=True).range(offset, offset + limit - 1)

            unread_query = supabase.table("notifications").select("id", count="exact").eq("user_id", user_id).eq("read", False)

            q1_exec = query.execute()
            q2_exec = unread_query.execute()

            res, unread_res = await asyncio.gather(
                q1_exec if inspect.isawaitable(q1_exec) else asyncio.sleep(0, result=q1_exec),
                q2_exec if inspect.isawaitable(q2_exec) else asyncio.sleep(0, result=q2_exec),
                return_exceptions=True
            )

            if not isinstance(res, Exception) and res and hasattr(res, "data") and res.data is not None:
                items = res.data
                total = res.count if hasattr(res, "count") and res.count is not None else len(items)
                if not isinstance(unread_res, Exception) and unread_res and hasattr(unread_res, "count") and unread_res.count is not None:
                    unread_count = unread_res.count
        except Exception as err:
            logger.debug(f"[NOTIFICATIONS] Supabase list fallback: {err}")

    if not items and not total:
        with _notifications_lock:
            user_notifs = [n for n in _in_memory_notifications.values() if n.get("user_id") == user_id]
            user_notifs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            
            unread_count = sum(1 for n in user_notifs if not n.get("read", False))
            
            filtered = user_notifs
            if unread_only:
                filtered = [n for n in filtered if not n.get("read", False)]
            if category and category.lower() != "all":
                filtered = [n for n in filtered if n.get("category") == category]
            
            total = len(filtered)
            items = filtered[offset:offset + limit]

    return NotificationListResponse(
        items=[
            NotificationResponse(
                id=str(it.get("id")),
                user_id=str(it.get("user_id")),
                type=str(it.get("type", "general")),
                category=str(it.get("category", "system")),
                severity=str(it.get("severity", "info")),
                title=str(it.get("title", "")),
                message=str(it.get("message", "")),
                strategy_id=it.get("strategy_id"),
                exchange=it.get("exchange"),
                metadata=it.get("metadata") or {},
                read=bool(it.get("read", False)),
                created_at=str(it.get("created_at", datetime.now(timezone.utc).isoformat())),
            )
            for it in items
        ],
        total=total,
        unread_count=unread_count,
        limit=limit,
        offset=offset
    )


@router.get("/unread-count")
async def get_unread_count(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """
    Get authoritative unread notification count for authenticated user.
    """
    user_id = user["id"]
    if supabase:
        try:
            q = supabase.table("notifications").select("id", count="exact").eq("user_id", user_id).eq("read", False).execute()
            res = await q if inspect.isawaitable(q) else q
            if res and hasattr(res, "count") and res.count is not None:
                return {"unread_count": res.count}
        except Exception as e:
            logger.debug(f"[NOTIFICATIONS] Supabase unread count fallback: {e}")

    with _notifications_lock:
        count = sum(1 for n in _in_memory_notifications.values() if n.get("user_id") == user_id and not n.get("read", False))
        return {"unread_count": count}


@router.put("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """
    Mark a single notification as read with strict IDOR verification.
    """
    user_id = user["id"]
    found = False

    if supabase:
        try:
            q1 = supabase.table("notifications").select("id").eq("id", notification_id).eq("user_id", user_id).execute()
            existing = await q1 if inspect.isawaitable(q1) else q1
            if existing and hasattr(existing, "data") and existing.data:
                found = True
                q2 = supabase.table("notifications").update({"read": True}).eq("id", notification_id).eq("user_id", user_id).execute()
                if inspect.isawaitable(q2):
                    await q2
        except Exception as e:
            logger.debug(f"[NOTIFICATIONS] Supabase mark read fallback: {e}")

    with _notifications_lock:
        if notification_id in _in_memory_notifications:
            notif = _in_memory_notifications[notification_id]
            if notif.get("user_id") != user_id:
                raise HTTPException(status_code=404, detail="Notification not found")
            notif["read"] = True
            found = True

    if not found:
        raise HTTPException(status_code=404, detail="Notification not found")

    return {"status": "ok", "id": notification_id, "read": True}


@router.put("/read-all")
async def mark_all_notifications_read(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """
    Mark all notifications for the authenticated user as read.
    """
    user_id = user["id"]
    if supabase:
        try:
            q = supabase.table("notifications").update({"read": True}).eq("user_id", user_id).execute()
            if inspect.isawaitable(q):
                await q
        except Exception as e:
            logger.debug(f"[NOTIFICATIONS] Supabase mark all read fallback: {e}")

    with _notifications_lock:
        for notif in _in_memory_notifications.values():
            if notif.get("user_id") == user_id:
                notif["read"] = True

    return {"status": "ok", "message": "All notifications marked as read"}


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: str,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """
    Delete a single notification with strict IDOR verification.
    """
    user_id = user["id"]
    found = False

    if supabase:
        try:
            q1 = supabase.table("notifications").select("id").eq("id", notification_id).eq("user_id", user_id).execute()
            existing = await q1 if inspect.isawaitable(q1) else q1
            if existing and hasattr(existing, "data") and existing.data:
                found = True
                q2 = supabase.table("notifications").delete().eq("id", notification_id).eq("user_id", user_id).execute()
                if inspect.isawaitable(q2):
                    await q2
        except Exception as e:
            logger.debug(f"[NOTIFICATIONS] Supabase delete fallback: {e}")

    with _notifications_lock:
        if notification_id in _in_memory_notifications:
            notif = _in_memory_notifications[notification_id]
            if notif.get("user_id") != user_id:
                raise HTTPException(status_code=404, detail="Notification not found")
            _forget_notification_locked(notification_id)
            found = True

    if not found:
        raise HTTPException(status_code=404, detail="Notification not found")

    return {"status": "ok", "id": notification_id}


@router.delete("", response_model=Dict[str, Any])
@router.delete("/", response_model=Dict[str, Any])
async def delete_all_notifications(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """
    Delete all notifications for the authenticated user.
    """
    user_id = user["id"]
    if supabase:
        try:
            q = supabase.table("notifications").delete().eq("user_id", user_id).execute()
            if inspect.isawaitable(q):
                await q
        except Exception as e:
            logger.debug(f"[NOTIFICATIONS] Supabase delete all fallback: {e}")

    with _notifications_lock:
        keys_to_delete = [k for k, v in _in_memory_notifications.items() if v.get("user_id") == user_id]
        for k in keys_to_delete:
            _forget_notification_locked(k)

    return {"status": "ok", "message": "All notifications deleted"}
