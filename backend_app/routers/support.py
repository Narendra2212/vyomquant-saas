"""
routers/support.py — Support tickets system.

Endpoints:
  GET  /api/support/tickets — List user's tickets
  POST /api/support/tickets — Create new ticket
  GET  /api/support/tickets/{ticket_id} — Get ticket details
  POST /api/support/tickets/{ticket_id}/comments — Add comment
  PUT  /api/support/tickets/{ticket_id} — Update ticket (close/reopen)
"""

import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client as SupabaseClient

from backend_app.core.dependencies import get_current_user, get_request_supabase, DEV_MODE
from backend_app.core.models import (
    CreateTicketRequest,
    AddCommentRequest,
    TicketStatus,
    TicketPriority,
)

router = APIRouter()
logger = logging.getLogger("SupportRouter")


# ── GET /api/support/tickets ───────────────────────────────────────────────
@router.get("/tickets")
async def get_tickets(
    status: Optional[str] = Query(None, pattern="^(open|in_progress|resolved|closed)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Get user's support tickets with optional filtering.
    """
    try:
        query = supabase.table("support_tickets").select("*").eq("user_id", user["id"])
        
        if status:
            query = query.eq("status", status)
        
        resp = query.order("created_at", desc=True).limit(limit).offset(offset).execute()
        
        tickets = []
        if resp.data:
            for row in resp.data:
                tickets.append({
                    "id": row.get("id"),
                    "subject": row.get("subject"),
                    "category": row.get("category"),
                    "priority": row.get("priority"),
                    "status": row.get("status"),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                    "has_unread": row.get("has_unread", False),
                    "comment_count": row.get("comment_count", 0),
                })
        
        return {
            "tickets": tickets,
            "count": len(tickets),
            "total": len(resp.data) if resp.data else 0,
            "offset": offset,
            "limit": limit,
        }
    except Exception as e:
        logger.error(f"Error fetching tickets for {user['id']}: {e}")
        return {"tickets": [], "count": 0, "total": 0, "offset": offset, "limit": limit}


# ── POST /api/support/tickets ──────────────────────────────────────────────
@router.post("/tickets")
async def create_ticket(
    body: CreateTicketRequest,
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Create a new support ticket.
    """
    try:
        data = {
            "user_id": user["id"],
            "subject": body.subject,
            "description": body.description,
            "category": body.category,
            "priority": body.priority.value,
            "status": TicketStatus.OPEN.value,
            "created_at": datetime.utcnow().isoformat(),
            "has_unread": False,
            "comment_count": 0,
        }
        
        result = supabase.table("support_tickets").insert(data).execute()
        
        if result.data:
            ticket = result.data[0]
            logger.info(f"Created ticket {ticket['id']} for user {user['id']}")
            return {
                "status": "created",
                "ticket_id": ticket["id"],
                "subject": body.subject,
                "priority": body.priority.value,
                "created_at": ticket["created_at"],
            }
        else:
            raise HTTPException(500, "Failed to create ticket")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating ticket for {user['id']}: {e}")
        raise HTTPException(500, f"Failed to create ticket: {e}")


# ── GET /api/support/tickets/{ticket_id} ───────────────────────────────────
@router.get("/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: str,
    include_comments: bool = Query(True),
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Get ticket details with comments.
    """
    try:
        # Get ticket
        ticket_resp = supabase.table("support_tickets").select("*").eq(
            "id", ticket_id
        ).eq("user_id", user["id"]).single().execute()
        
        if not ticket_resp.data:
            raise HTTPException(404, "Ticket not found")
        
        ticket = ticket_resp.data
        
        # Mark as read
        supabase.table("support_tickets").update({
            "has_unread": False,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", ticket_id).execute()
        
        result = {
            "id": ticket["id"],
            "subject": ticket["subject"],
            "description": ticket["description"],
            "category": ticket["category"],
            "priority": ticket["priority"],
            "status": ticket["status"],
            "created_at": ticket["created_at"],
            "updated_at": ticket["updated_at"],
            "resolved_at": ticket.get("resolved_at"),
        }
        
        # Get comments if requested
        if include_comments:
            comments_resp = supabase.table("ticket_comments").select("*").eq(
                "ticket_id", ticket_id
            ).order("created_at", desc=True).execute()
            
            comments = []
            if comments_resp.data:
                for row in comments_resp.data:
                    comments.append({
                        "id": row.get("id"),
                        "message": row.get("message"),
                        "is_staff": row.get("is_staff", False),
                        "created_at": row.get("created_at"),
                    })
            
            result["comments"] = comments
            result["comment_count"] = len(comments)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching ticket {ticket_id} for {user['id']}: {e}")
        raise HTTPException(500, f"Failed to fetch ticket: {e}")


# ── POST /api/support/tickets/{ticket_id}/comments ─────────────────────────
@router.post("/tickets/{ticket_id}/comments")
async def add_comment(
    ticket_id: str,
    body: AddCommentRequest,
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Add a comment to a ticket.
    """
    try:
        # Verify ticket ownership
        ticket_check = supabase.table("support_tickets").select("id, status").eq(
            "id", ticket_id
        ).eq("user_id", user["id"]).single().execute()
        
        if not ticket_check.data:
            raise HTTPException(404, "Ticket not found")
        
        if ticket_check.data["status"] == TicketStatus.CLOSED.value:
            raise HTTPException(400, "Cannot comment on closed ticket")
        
        # Add comment
        comment_data = {
            "ticket_id": ticket_id,
            "user_id": user["id"],
            "message": body.message,
            "is_staff": False,
            "created_at": datetime.utcnow().isoformat(),
        }
        
        result = supabase.table("ticket_comments").insert(comment_data).execute()
        
        # Update ticket
        supabase.table("support_tickets").update({
            "has_unread": True,  # Mark for staff
            "updated_at": datetime.utcnow().isoformat(),
            "comment_count": supabase.table("ticket_comments").select("id", count="exact").eq("ticket_id", ticket_id).execute().count
        }).eq("id", ticket_id).execute()
        
        return {
            "status": "ok",
            "comment_id": result.data[0]["id"] if result.data else None,
            "created_at": comment_data["created_at"],
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding comment to ticket {ticket_id}: {e}")
        raise HTTPException(500, f"Failed to add comment: {e}")


# ── PUT /api/support/tickets/{ticket_id} ───────────────────────────────────
@router.put("/tickets/{ticket_id}")
async def update_ticket(
    ticket_id: str,
    status: Optional[str] = Query(None, pattern="^(closed|reopen)$"),
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Update ticket status (close or reopen).
    """
    try:
        # Verify ownership
        check = supabase.table("support_tickets").select("id, status").eq(
            "id", ticket_id
        ).eq("user_id", user["id"]).single().execute()
        
        if not check.data:
            raise HTTPException(404, "Ticket not found")
        
        current_status = check.data["status"]
        
        if status == "closed" and current_status != TicketStatus.CLOSED.value:
            new_status = TicketStatus.CLOSED.value
            update_data = {
                "status": new_status,
                "resolved_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }
        elif status == "reopen" and current_status == TicketStatus.CLOSED.value:
            new_status = TicketStatus.OPEN.value
            update_data = {
                "status": new_status,
                "resolved_at": None,
                "updated_at": datetime.utcnow().isoformat(),
            }
        else:
            raise HTTPException(400, f"Invalid status transition from {current_status} to {status}")
        
        result = supabase.table("support_tickets").update(update_data).eq(
            "id", ticket_id
        ).execute()
        
        return {
            "status": "ok",
            "ticket_id": ticket_id,
            "new_status": new_status,
            "updated_at": update_data["updated_at"],
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating ticket {ticket_id}: {e}")
        raise HTTPException(500, f"Failed to update ticket: {e}")
