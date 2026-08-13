"""
routers/support.py — Support tickets system.

Endpoints:
  GET  /api/support/tickets — List user's tickets
  POST /api/support/tickets — Create new ticket
  GET  /api/support/tickets/{ticket_id} — Get ticket details
  POST /api/support/tickets/{ticket_id}/comments — Add comment
  PUT  /api/support/tickets/{ticket_id} — Update ticket (close/reopen)
"""

import inspect
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from backend_app.core.dependencies import (get_current_user,
                                           get_request_supabase)
from backend_app.core.models import (AddCommentRequest, CreateTicketRequest,
                                     TicketStatus)
from supabase import Client as SupabaseClient

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
        
        res = query.order("created_at", desc=True).limit(limit).offset(offset).execute()
        resp = await res if inspect.isawaitable(res) else res
        
        tickets = []
        rows = resp.data if resp and hasattr(resp, "data") and resp.data else []
        if rows:
            for row in rows:
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
            "total": len(rows),
            "offset": offset,
            "limit": limit,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching tickets for {user['id']}: {e}")
        raise HTTPException(
            status_code=503,
            detail={"error": "TICKETS_FETCH_FAILED", "message": str(e)},
        )


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
        
        res = supabase.table("support_tickets").insert(data).execute()
        result = await res if inspect.isawaitable(res) else res
        
        if result and hasattr(result, "data") and result.data:
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
        res1 = supabase.table("support_tickets").select("*").eq(
            "id", ticket_id
        ).eq("user_id", user["id"]).execute()
        ticket_resp = await res1 if inspect.isawaitable(res1) else res1
        
        if not ticket_resp or not hasattr(ticket_resp, "data") or not ticket_resp.data:
            raise HTTPException(404, "Ticket not found")
        
        ticket = ticket_resp.data[0]
        
        # Mark as read
        res_upd = supabase.table("support_tickets").update({
            "has_unread": False,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", ticket_id).execute()
        if inspect.isawaitable(res_upd):
            await res_upd
        
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
            res_comm = supabase.table("ticket_comments").select("*").eq(
                "ticket_id", ticket_id
            ).order("created_at", desc=True).execute()
            comments_resp = await res_comm if inspect.isawaitable(res_comm) else res_comm
            
            comments = []
            rows = comments_resp.data if comments_resp and hasattr(comments_resp, "data") and comments_resp.data else []
            if rows:
                for row in rows:
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
        res_chk = supabase.table("support_tickets").select("id, status").eq(
            "id", ticket_id
        ).eq("user_id", user["id"]).execute()
        ticket_check = await res_chk if inspect.isawaitable(res_chk) else res_chk
        
        if not ticket_check or not hasattr(ticket_check, "data") or not ticket_check.data:
            raise HTTPException(404, "Ticket not found")
        
        if ticket_check.data[0]["status"] == TicketStatus.CLOSED.value:
            raise HTTPException(400, "Cannot comment on closed ticket")
        
        # Add comment
        comment_data = {
            "ticket_id": ticket_id,
            "user_id": user["id"],
            "message": body.message,
            "is_staff": False,
            "created_at": datetime.utcnow().isoformat(),
        }
        
        res_ins = supabase.table("ticket_comments").insert(comment_data).execute()
        result = await res_ins if inspect.isawaitable(res_ins) else res_ins
        
        res_cnt = supabase.table("ticket_comments").select("id", count="exact").eq("ticket_id", ticket_id).execute()
        cnt_resp = await res_cnt if inspect.isawaitable(res_cnt) else res_cnt
        comment_count = cnt_resp.count if cnt_resp and hasattr(cnt_resp, "count") else 0

        # Update ticket
        res_upd = supabase.table("support_tickets").update({
            "has_unread": True,  # Mark for staff
            "updated_at": datetime.utcnow().isoformat(),
            "comment_count": comment_count
        }).eq("id", ticket_id).execute()
        if inspect.isawaitable(res_upd):
            await res_upd
        
        return {
            "status": "ok",
            "comment_id": result.data[0]["id"] if result and hasattr(result, "data") and result.data else None,
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
        res_chk = supabase.table("support_tickets").select("id, status").eq(
            "id", ticket_id
        ).eq("user_id", user["id"]).single().execute()
        check = await res_chk if inspect.isawaitable(res_chk) else res_chk
        
        if not check or not hasattr(check, "data") or not check.data:
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
        
        res_upd = supabase.table("support_tickets").update(update_data).eq(
            "id", ticket_id
        ).execute()
        if inspect.isawaitable(res_upd):
            await res_upd
        
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
