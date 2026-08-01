"""
routers/notifications.py — Notification Management

Provides CRUD endpoints for user notifications with pagination and filtering.
Integrates with WebSocket for real-time delivery.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

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
    category: str = Field(..., description="Category: trade, strategy, risk, security, billing, system")
    severity: str = Field(..., description="Severity: info, warning, critical, emergency")
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
    strategy_id: Optional[str]
    exchange: Optional[str]
    metadata: Optional[dict]
    read: bool
    created_at: str


class NotificationListResponse(BaseModel):
    """Response model for notification list."""
    items: List[NotificationResponse]
    total: int
    unread_count: int
    limit: int
    offset: int


# ══════════════════════════════════════════════════════════════════════════
# NOTIFICATION SERVICE
# ══════════════════════════════════════════════════════════════════════════

async def create_notification(
    notification: NotificationCreate,
    supabase: SupabaseClient,
    ws_manager
):
    """
    Create a notification and broadcast via WebSocket.
    
    This is the central function for creating notifications throughout the system.
    It stores to database and broadcasts in real-time.
    """
    try:
        # Store in database
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
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        result = supabase.table("notifications").insert(data).execute()
        
        if result.data:
            notification_id = result.data[0]["id"]
            
            # Broadcast via WebSocket
            await ws_manager.broadcast_user(
                notification.user_id,
                {
                    "type": "notification",
                    "data": {
                        "id": notification_id,
                        **data
                    }
                }
            )
            
            logger.info(f"Notification created and broadcast: {notification_id}")
            return notification_id
            
    except Exception as e:
        logger.error(f"Failed to create notification: {e}")
        raise


# ══════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════

@router.get("/")
async def list_notifications(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    unread_only: bool = Query(False),
    category: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    List user notifications with pagination and filtering.
    """
    if not supabase:
        return NotificationListResponse(
            items=[],
            total=0,
            unread_count=0,
            limit=limit,
            offset=offset
        )
    
    try:
        query = supabase.table("notifications").select("*", count="exact")
        
        # Apply filters
        query = query.eq("user_id", user["id"])
        
        if unread_only:
            query = query.eq("read", False)
        
        if category:
            query = query.eq("category", category)
        
        # Apply pagination
        query = query.order("created_at", desc=True).range(offset, offset + limit - 1)
        
        result = query.execute()
        
        items = result.data or []
        total = result.count or 0
        
        # Get unread count
        unread_query = supabase.table("notifications").select("*", count="exact") \
            .eq("user_id", user["id"]) \
            .eq("read", False)
        unread_result = unread_query.execute()
        unread_count = unread_result.count or 0
        
        return NotificationListResponse(
            items=items,
            total=total,
            unread_count=unread_count,
            limit=limit,
            offset=offset
        )
        
    except Exception as e:
        logger.error(f"Failed to list notifications: {e}")
        raise HTTPException(500, "Failed to retrieve notifications")


@router.put("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Mark a notification as read.
    """
    if not supabase:
        return {"status": "ok"}
    
    try:
        # Verify ownership
        existing = supabase.table("notifications").select("*").eq("id", notification_id).eq("user_id", user["id"]).execute()
        
        if not existing.data:
            raise HTTPException(404, "Notification not found")
        
        # Update
        supabase.table("notifications").update({"read": True}).eq("id", notification_id).execute()
        
        return {"status": "ok"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to mark notification as read: {e}")
        raise HTTPException(500, "Failed to update notification")


@router.put("/read-all")
async def mark_all_notifications_read(
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Mark all user notifications as read.
    """
    if not supabase:
        return {"status": "ok"}
    
    try:
        supabase.table("notifications").update({"read": True}).eq("user_id", user["id"]).execute()
        return {"status": "ok"}
        
    except Exception as e:
        logger.error(f"Failed to mark all notifications as read: {e}")
        raise HTTPException(500, "Failed to update notifications")


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: str,
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Delete a notification.
    """
    if not supabase:
        return {"status": "ok"}
    
    try:
        # Verify ownership
        existing = supabase.table("notifications").select("*").eq("id", notification_id).eq("user_id", user["id"]).execute()
        
        if not existing.data:
            raise HTTPException(404, "Notification not found")
        
        # Delete
        supabase.table("notifications").delete().eq("id", notification_id).execute()
        
        return {"status": "ok"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete notification: {e}")
        raise HTTPException(500, "Failed to delete notification")


@router.delete("/")
async def delete_all_notifications(
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Delete all user notifications.
    """
    if not supabase:
        return {"status": "ok"}
    
    try:
        supabase.table("notifications").delete().eq("user_id", user["id"]).execute()
        return {"status": "ok"}
        
    except Exception as e:
        logger.error(f"Failed to delete all notifications: {e}")
        raise HTTPException(500, "Failed to delete notifications")
