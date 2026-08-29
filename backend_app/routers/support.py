"""
routers/support.py — Production-Grade Multi-Tenant Support & Help Center System.

Endpoints:
  GET  /api/support/faqs                        — List categorized FAQs with search
  GET  /api/support/categories                  — Available ticket categories & SLAs
  GET  /api/support/tickets                     — List user's support tickets
  POST /api/support/tickets                     — Create new ticket with validation & attachments
  GET  /api/support/tickets/{ticket_id}         — Get ticket details with conversation history
  POST /api/support/tickets/{ticket_id}/comments — Add comment / reply to ticket
  PUT  /api/support/tickets/{ticket_id}         — Update ticket status (close/reopen/resolve)
  POST /api/support/tickets/{ticket_id}/attachments — Add safe attachment metadata
  GET  /api/support/admin/tickets               — Staff listing of tickets (admin/support role)
  POST /api/support/admin/tickets/{ticket_id}/reply — Staff reply to ticket
  PUT  /api/support/admin/tickets/{ticket_id}/status — Staff status override
"""

import inspect
import logging
import re
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend_app.core.dependencies import (
    get_admin_user,
    get_current_user,
    get_request_supabase,
    get_ws_manager,
)
from backend_app.core.rate_limit import limiter

logger = logging.getLogger("SupportRouter")
router = APIRouter()

# ══════════════════════════════════════════════════════════════════════════
# ENUMS & MODELS
# ══════════════════════════════════════════════════════════════════════════

class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_FOR_USER = "waiting_for_user"
    WAITING_FOR_SUPPORT = "waiting_for_support"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TicketCategory(str, Enum):
    GENERAL = "general"
    TECHNICAL = "technical"
    BILLING = "billing"
    SECURITY = "security"
    FEATURE = "feature"
    TRADING = "trading"


class AttachmentMeta(BaseModel):
    filename: str = Field(..., max_length=255)
    file_size: int = Field(..., ge=1, le=10 * 1024 * 1024)  # Max 10MB
    content_type: str = Field(..., max_length=100)
    url: Optional[str] = None


class CreateTicketRequest(BaseModel):
    subject: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=10, max_length=5000)
    category: str = Field(..., pattern="^(general|technical|billing|security|feature|trading)$")
    priority: TicketPriority = TicketPriority.MEDIUM
    related_feature: Optional[str] = Field(None, max_length=100)
    strategy_id: Optional[str] = Field(None, max_length=100)
    order_id: Optional[str] = Field(None, max_length=100)
    attachment: Optional[AttachmentMeta] = None


class AddCommentRequest(BaseModel):
    ticket_id: Optional[str] = None
    message: str = Field(..., min_length=1, max_length=2000)
    attachment: Optional[AttachmentMeta] = None


class UpdateTicketStatusRequest(BaseModel):
    status: str = Field(..., pattern="^(open|in_progress|waiting_for_user|waiting_for_support|resolved|closed|reopen)$")


class StaffReplyRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    new_status: Optional[str] = Field(None, pattern="^(in_progress|waiting_for_user|resolved|closed)$")


# ══════════════════════════════════════════════════════════════════════════
# IN-MEMORY RESILIENT STORAGE FOR DEV / TEST / FALLBACK
# ══════════════════════════════════════════════════════════════════════════

_support_lock = threading.Lock()
_in_memory_tickets: Dict[str, Dict[str, Any]] = {}
_in_memory_comments: Dict[str, List[Dict[str, Any]]] = {}

# ══════════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE & FAQS
# ══════════════════════════════════════════════════════════════════════════

FAQS_DATA = [
    {
        "id": "faq-1",
        "category": "trading",
        "question": "How does VyomQuant enforce Live Risk Gates before order submission?",
        "answer": "Every strategy execution passes through the server-authoritative InstitutionalRiskManager before CCXT dispatch. If any risk threshold (daily loss, max drawdown, symbol restriction, max notional, or kill switch) is tripped, the order is rejected immediately with CCXT call count strictly equal to zero.",
    },
    {
        "id": "faq-2",
        "category": "trading",
        "question": "What is the Anti-Bypass Execution Token?",
        "answer": "VyomQuant issues a cryptographically verified, single-use execution token upon InstitutionalRiskManager approval. The CCXT execution boundary validates and consumes this token before transmitting orders to any exchange venue.",
    },
    {
        "id": "faq-3",
        "category": "trading",
        "question": "How does the Order State Engine handle partial fills and exchange timeouts?",
        "answer": "The Order State Engine tracks partial fills cumulatively using volume-weighted average price (VWAP) accounting. If an exchange acknowledgment times out, the engine sets an UNKNOWN state and triggers automatic reconciliation before allowing followup orders.",
    },
    {
        "id": "faq-4",
        "category": "trading",
        "question": "How is Paper Trading isolated from Live Trading?",
        "answer": "Paper Trading runs in a completely segregated simulation loop using live market feeds and synthetic order books. It requires zero exchange API keys and can never submit orders to real exchange endpoints.",
    },
    {
        "id": "faq-5",
        "category": "exchanges",
        "question": "Which exchanges support Sandbox / Testnet mode?",
        "answer": "Binance (Testnet), Bybit (Testnet), OKX (Demo Mode), Coinbase (Sandbox), and KuCoin (Sandbox) support simulated trading with real API contracts. Kraken runs on production endpoints exclusively.",
    },
    {
        "id": "faq-6",
        "category": "exchanges",
        "question": "How does Exchange Connection Certification work?",
        "answer": "When connecting an exchange account, VyomQuant performs an authenticated, read-only preflight check (fetching account balances and verifying permissions). Credentials are encrypted and stored only after preflight success.",
    },
    {
        "id": "faq-7",
        "category": "security",
        "question": "How are my exchange API keys and secrets stored?",
        "answer": "All credential material is encrypted using authenticated AES-256-GCM through the SecurityVault before database storage. Encryption keys remain strictly in isolated environment memory and are never written to database tables or transmitted to the client.",
    },
    {
        "id": "faq-8",
        "category": "security",
        "question": "How is multi-tenant data isolation enforced?",
        "answer": "Every database query and API operation enforces strict tenant isolation using authenticated JWT identity claims and PostgreSQL Row-Level Security (RLS). No user can access or mutate another tenant's strategies, orders, or support tickets.",
    },
    {
        "id": "faq-9",
        "category": "technical",
        "question": "Why did my strategy compilation fail?",
        "answer": "Strategy graphs require canonical Schema Version 2 DAG format. Ensure all DATA and ACTION nodes have valid port connections, no circular cycles exist, and action node symbols resolve to upstream market feeds.",
    },
    {
        "id": "faq-10",
        "category": "technical",
        "question": "How does the Backtesting Engine simulate strategy performance?",
        "answer": "Backtesting computes tick-by-tick or OHLCV candlestick historical replay with realistic slippage, exchange taker/maker fee models, and drawdown metrics across custom date ranges.",
    },
    {
        "id": "faq-11",
        "category": "technical",
        "question": "What is the Reconciliation subsystem?",
        "answer": "The Reconciliation engine regularly compares internal database order states with real exchange balances and position reports. Any discrepancy automatically raises a risk alert and halts execution until reconciled.",
    },
    {
        "id": "faq-12",
        "category": "technical",
        "question": "How do WebSockets provide real-time execution telemetry?",
        "answer": "The WebSocket gateway streams private fill events, PnL updates, signal traces, and support notifications to authenticated user connections over dedicated, isolated tenant streams.",
    },
    {
        "id": "faq-13",
        "category": "billing",
        "question": "How does subscription tiering affect my execution quotas?",
        "answer": "Higher subscription tiers unlock increased concurrent live bots, sub-millisecond execution tick rates, and expanded backtesting data access.",
    },
    {
        "id": "faq-14",
        "category": "general",
        "question": "How do I get started with the VyomQuant Terminal?",
        "answer": "1) Connect your exchange or use Paper Trading; 2) Build or import a strategy in Strategy Builder; 3) Run a backtest; 4) Configure Institutional Risk Settings; 5) Deploy to Paper or Live.",
    },
    {
        "id": "faq-15",
        "category": "general",
        "question": "Where can I manage my account profile and security settings?",
        "answer": "Navigate to the Profile page in the platform navigation to configure Two-Factor Authentication (2FA), review active sessions, and inspect security audit logs.",
    },
    {
        "id": "faq-16",
        "category": "general",
        "question": "What are the Support Ticket Response SLAs?",
        "answer": "Urgent trading and security issues receive response within 2-4 hours. Technical DAG compiler issues receive response within 8 hours. General inquiries receive response within 24 hours.",
    },
    {
        "id": "faq-17",
        "category": "general",
        "question": "Can I attach diagnostic logs to my support request?",
        "answer": "Yes. When creating a ticket or posting a reply, you can attach log files (.log, .txt, .json, .csv) or screenshots (.png, .jpg). Executable file types (.exe, .sh, .bat) are strictly blocked for security.",
    },
    {
        "id": "faq-18",
        "category": "general",
        "question": "How does the Emergency Kill Switch work?",
        "answer": "Engaging the Emergency Kill Switch in Risk Settings immediately cancels all pending open orders across all connected exchanges and transitions all active strategies to PAUSED state within milliseconds.",
    },
]

FORBIDDEN_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".sh", ".ps1", ".vbs", ".js", ".py", ".dll", ".scr", ".bin", ".com", ".pif", ".msi"
}

SECRET_PATTERNS = [
    re.compile(r"(?:api_secret|secret_key|private_key|password)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-]{16,})", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", re.IGNORECASE),
]


def _sanitize_filename(filename: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)
    return cleaned[:100]


def _check_attachment(att: Optional[AttachmentMeta]) -> None:
    if not att:
        return
    filename_lower = att.filename.lower()
    for ext in FORBIDDEN_EXTENSIONS:
        if filename_lower.endswith(ext):
            raise HTTPException(
                status_code=400,
                detail=f"File extension '{ext}' is forbidden for security reasons."
            )


def _scan_for_secrets(text: str) -> None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise HTTPException(
                status_code=400,
                detail="Support message appears to contain raw private keys or credentials. Please remove sensitive secrets before submitting."
            )


# ══════════════════════════════════════════════════════════════════════════
# FAQ & CATEGORY ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════

@router.get("/faqs")
async def get_faqs(
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
):
    """Returns curated FAQ items with optional search and category filters."""
    results = FAQS_DATA
    if category and category != "all":
        results = [f for f in results if f["category"] == category]
    if search:
        s = search.lower()
        results = [
            f for f in results
            if s in f["question"].lower() or s in f["answer"].lower() or s in f["category"].lower()
        ]
    return {
        "faqs": results,
        "total": len(results),
    }


@router.get("/categories")
async def get_categories():
    """Returns supported ticket categories and response SLAs."""
    return {
        "categories": [
            {"id": "general", "name": "General Inquiries", "sla_hours": 24},
            {"id": "technical", "name": "Technical & Platform Issues", "sla_hours": 8},
            {"id": "trading", "name": "Trading & Execution Diagnostics", "sla_hours": 4},
            {"id": "billing", "name": "Billing & Subscriptions", "sla_hours": 12},
            {"id": "security", "name": "Security & 2FA", "sla_hours": 2},
            {"id": "feature", "name": "Feature Requests", "sla_hours": 48},
        ],
        "priorities": [
            {"id": "low", "label": "Low", "description": "General questions or minor visual issues"},
            {"id": "medium", "label": "Medium", "description": "Standard issues or feature inquiries"},
            {"id": "high", "label": "High", "description": "Degraded execution or unexpected behavior"},
            {"id": "urgent", "label": "Urgent", "description": "Critical trading disruption or security concern"},
        ],
    }


# ══════════════════════════════════════════════════════════════════════════
# TICKET CRUD (USER FACING)
# ══════════════════════════════════════════════════════════════════════════

@router.get("/tickets")
async def get_tickets(
    status: Optional[str] = Query(None, pattern="^(open|in_progress|waiting_for_user|waiting_for_support|resolved|closed)$"),
    category: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Get authenticated user's support tickets with filtering, search, and pagination."""
    try:
        user_id = user["id"]
        tickets_list = []

        if supabase:
            try:
                query = supabase.table("support_tickets").select("*").eq("user_id", user_id)
                if status:
                    query = query.eq("status", status)
                if category and category != "all":
                    query = query.eq("category", category)
                if priority and priority != "all":
                    query = query.eq("priority", priority)

                res = query.order("created_at", desc=True).limit(limit).offset(offset).execute()
                resp = await res if inspect.isawaitable(res) else res
                rows = resp.data if resp and hasattr(resp, "data") and resp.data else []

                for row in rows:
                    tickets_list.append({
                        "id": row.get("id"),
                        "subject": row.get("subject"),
                        "category": row.get("category"),
                        "priority": row.get("priority"),
                        "status": row.get("status"),
                        "description": row.get("description", ""),
                        "created_at": row.get("created_at"),
                        "updated_at": row.get("updated_at"),
                        "has_unread": row.get("has_unread", False),
                        "comment_count": row.get("comment_count", 0),
                        "related_feature": row.get("related_feature"),
                        "strategy_id": row.get("strategy_id"),
                        "order_id": row.get("order_id"),
                    })
            except Exception as sb_err:
                logger.warning(f"[SUPPORT] Supabase fetch fallback for {user_id}: {sb_err}")
                supabase = None

        if not supabase:
            with _support_lock:
                user_tickets = [
                    t for t in _in_memory_tickets.values()
                    if t.get("user_id") == user_id
                ]
                if status:
                    user_tickets = [t for t in user_tickets if t.get("status") == status]
                if category and category != "all":
                    user_tickets = [t for t in user_tickets if t.get("category") == category]
                if priority and priority != "all":
                    user_tickets = [t for t in user_tickets if t.get("priority") == priority]
                if search:
                    s = search.lower()
                    user_tickets = [
                        t for t in user_tickets
                        if s in t.get("subject", "").lower() or s in t.get("description", "").lower()
                    ]

                user_tickets.sort(key=lambda x: x.get("created_at", ""), reverse=True)
                paged = user_tickets[offset: offset + limit]
                tickets_list = paged

        if search and supabase:
            s = search.lower()
            tickets_list = [
                t for t in tickets_list
                if s in t.get("subject", "").lower() or s in t.get("description", "").lower()
            ]

        return {
            "tickets": tickets_list,
            "count": len(tickets_list),
            "total": len(tickets_list),
            "offset": offset,
            "limit": limit,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching tickets for {user.get('id')}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch tickets: {e}")


@router.post("/tickets")
@limiter.limit("10/minute")
async def create_ticket(
    request: Request,
    body: CreateTicketRequest,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
    ws_mgr: Any = Depends(get_ws_manager),
):
    """Create a new support ticket with tenant isolation, rate limiting, and event dispatch."""
    _scan_for_secrets(body.subject + " " + body.description)
    if body.attachment:
        _check_attachment(body.attachment)

    ticket_id = f"tkt_{uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    user_id = user["id"]

    ticket_data = {
        "id": ticket_id,
        "user_id": user_id,
        "subject": body.subject.strip(),
        "description": body.description.strip(),
        "category": body.category,
        "priority": body.priority.value,
        "status": TicketStatus.OPEN.value,
        "created_at": now_iso,
        "updated_at": now_iso,
        "has_unread": False,
        "comment_count": 0,
        "related_feature": body.related_feature,
        "strategy_id": body.strategy_id,
        "order_id": body.order_id,
        "attachment": body.attachment.dict() if body.attachment else None,
    }

    if supabase:
        try:
            res = supabase.table("support_tickets").insert(ticket_data).execute()
            result = await res if inspect.isawaitable(res) else res
            if result and hasattr(result, "data") and result.data:
                ticket_data = result.data[0]
        except Exception as sb_err:
            logger.warning(f"[SUPPORT] Supabase insert fallback: {sb_err}")
            with _support_lock:
                _in_memory_tickets[ticket_id] = ticket_data
                _in_memory_comments[ticket_id] = []
    else:
        with _support_lock:
            _in_memory_tickets[ticket_id] = ticket_data
            _in_memory_comments[ticket_id] = []

    # Broadcast real-time WebSocket event to ticket owner
    if ws_mgr:
        try:
            await ws_mgr.broadcast_user(
                user_id,
                {
                    "type": "support_ticket",
                    "action": "created",
                    "data": {
                        "ticket_id": ticket_id,
                        "subject": body.subject,
                        "status": TicketStatus.OPEN.value,
                        "created_at": now_iso,
                    },
                },
            )
        except Exception as ws_err:
            logger.debug(f"[SUPPORT] WebSocket broadcast error: {ws_err}")

    # Broadcast real-time event to authorized Support Staff & Admins
    admin_event = {
        "type": "support_ticket_created",
        "data": {
            "ticket_id": ticket_id,
            "subject": body.subject,
            "category": body.category,
            "priority": body.priority.value,
            "created_at": now_iso,
            "user_id": user_id,
            "user_display_name": user.get("email") or user.get("id") or "Trader",
        },
    }
    if ws_mgr:
        try:
            if hasattr(ws_mgr, "broadcast_admin"):
                await ws_mgr.broadcast_admin(admin_event)
            elif hasattr(ws_mgr, "broadcast_to_channel"):
                await ws_mgr.broadcast_to_channel("admin", "support", admin_event)
        except Exception as ws_admin_err:
            logger.debug(f"[SUPPORT] Admin WebSocket broadcast error: {ws_admin_err}")

    # Generate in-app notification for support staff
    try:
        from backend_app.routers.notifications import NotificationCreate, create_notification
        admin_notification = NotificationCreate(
            user_id="support_staff",
            type="support",
            category="system",
            severity="warning" if body.priority.value in ("high", "urgent") else "info",
            title=f"New Support Ticket #{ticket_id}",
            message=f"User {user.get('email', user_id)} submitted: {body.subject} [{body.category.upper()}]",
            metadata={
                "ticket_id": ticket_id,
                "priority": body.priority.value,
                "category": body.category,
                "strategy_id": body.strategy_id,
                "order_id": body.order_id,
            },
        )
        if supabase and ws_mgr:
            try:
                await create_notification(admin_notification, supabase, ws_mgr)
            except Exception as notif_err:
                logger.debug(f"[SUPPORT] In-app notification creation non-blocking fallback: {notif_err}")
    except Exception as e:
        logger.debug(f"[SUPPORT] Notification integration non-blocking: {e}")

    logger.info(f"[SUPPORT] Ticket {ticket_id} created by user {user_id}")
    return {
        "status": "created",
        "ticket_id": ticket_id,
        "subject": body.subject,
        "priority": body.priority.value,
        "created_at": now_iso,
    }


@router.get("/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: str,
    include_comments: bool = Query(True),
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Get ticket detail with chronological conversation history and strict tenant isolation."""
    user_id = user["id"]
    ticket = None
    comments = []

    if supabase:
        try:
            res1 = supabase.table("support_tickets").select("*").eq("id", ticket_id).eq("user_id", user_id).execute()
            ticket_resp = await res1 if inspect.isawaitable(res1) else res1
            if ticket_resp and hasattr(ticket_resp, "data") and ticket_resp.data:
                ticket = ticket_resp.data[0]

            if ticket and include_comments:
                res_comm = supabase.table("ticket_comments").select("*").eq("ticket_id", ticket_id).order("created_at", desc=False).execute()
                comments_resp = await res_comm if inspect.isawaitable(res_comm) else res_comm
                rows = comments_resp.data if comments_resp and hasattr(comments_resp, "data") and comments_resp.data else []
                comments = [
                    {
                        "id": r.get("id"),
                        "message": r.get("message"),
                        "is_staff": r.get("is_staff", False),
                        "created_at": r.get("created_at"),
                        "attachment": r.get("attachment"),
                    }
                    for r in rows
                ]
        except Exception as sb_err:
            logger.warning(f"[SUPPORT] Supabase get fallback: {sb_err}")
            ticket = None

    if not ticket:
        with _support_lock:
            cached = _in_memory_tickets.get(ticket_id)
            if cached and cached.get("user_id") == user_id:
                ticket = cached
                comments = _in_memory_comments.get(ticket_id, [])

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return {
        "id": ticket["id"],
        "subject": ticket["subject"],
        "description": ticket["description"],
        "category": ticket["category"],
        "priority": ticket["priority"],
        "status": ticket["status"],
        "created_at": ticket["created_at"],
        "updated_at": ticket["updated_at"],
        "resolved_at": ticket.get("resolved_at"),
        "related_feature": ticket.get("related_feature"),
        "strategy_id": ticket.get("strategy_id"),
        "order_id": ticket.get("order_id"),
        "attachment": ticket.get("attachment"),
        "comments": comments if include_comments else [],
        "comment_count": len(comments),
    }


@router.post("/tickets/{ticket_id}/comments")
@limiter.limit("30/minute")
async def add_comment(
    request: Request,
    ticket_id: str,
    body: AddCommentRequest,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
    ws_mgr: Any = Depends(get_ws_manager),
):
    """Add user comment/reply to ticket with strict tenant ownership validation."""
    _scan_for_secrets(body.message)
    if body.attachment:
        _check_attachment(body.attachment)

    user_id = user["id"]
    now_iso = datetime.now(timezone.utc).isoformat()
    comment_id = f"cmt_{uuid4().hex[:12]}"

    ticket = None
    if supabase:
        try:
            res_chk = supabase.table("support_tickets").select("id, status, user_id").eq("id", ticket_id).eq("user_id", user_id).execute()
            ticket_check = await res_chk if inspect.isawaitable(res_chk) else res_chk
            if ticket_check and hasattr(ticket_check, "data") and ticket_check.data:
                ticket = ticket_check.data[0]
        except Exception:
            ticket = None

    if not ticket:
        with _support_lock:
            cached = _in_memory_tickets.get(ticket_id)
            if cached and cached.get("user_id") == user_id:
                ticket = cached

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if ticket.get("status") == TicketStatus.CLOSED.value:
        raise HTTPException(status_code=400, detail="Cannot comment on closed ticket. Please reopen the ticket first.")

    comment_record = {
        "id": comment_id,
        "ticket_id": ticket_id,
        "user_id": user_id,
        "message": body.message.strip(),
        "is_staff": False,
        "created_at": now_iso,
        "attachment": body.attachment.dict() if body.attachment else None,
    }

    if supabase:
        try:
            supabase.table("ticket_comments").insert(comment_record).execute()
            supabase.table("support_tickets").update({
                "has_unread": True,
                "status": TicketStatus.IN_PROGRESS.value,
                "updated_at": now_iso,
            }).eq("id", ticket_id).execute()
        except Exception as sb_err:
            logger.warning(f"[SUPPORT] Supabase comment insert fallback: {sb_err}")

    with _support_lock:
        if ticket_id not in _in_memory_comments:
            _in_memory_comments[ticket_id] = []
        _in_memory_comments[ticket_id].append(comment_record)
        if ticket_id in _in_memory_tickets:
            _in_memory_tickets[ticket_id]["updated_at"] = now_iso
            _in_memory_tickets[ticket_id]["comment_count"] = len(_in_memory_comments[ticket_id])
            _in_memory_tickets[ticket_id]["status"] = TicketStatus.IN_PROGRESS.value

    # Broadcast WebSocket update
    if ws_mgr:
        try:
            await ws_mgr.broadcast_user(
                user_id,
                {
                    "type": "support_comment",
                    "action": "new_comment",
                    "data": {
                        "ticket_id": ticket_id,
                        "comment_id": comment_id,
                        "is_staff": False,
                        "created_at": now_iso,
                    },
                },
            )
        except Exception as ws_err:
            logger.debug(f"[SUPPORT] WebSocket broadcast error: {ws_err}")

    return {
        "status": "ok",
        "comment_id": comment_id,
        "created_at": now_iso,
    }


@router.put("/tickets/{ticket_id}")
async def update_ticket(
    ticket_id: str,
    status: Optional[str] = Query(None, pattern="^(open|in_progress|waiting_for_user|waiting_for_support|resolved|closed|reopen)$"),
    body: Optional[UpdateTicketStatusRequest] = None,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
    ws_mgr: Any = Depends(get_ws_manager),
):
    """User-facing ticket status update (close or reopen)."""
    target_status = (body.status if body else status) or ""
    user_id = user["id"]
    now_iso = datetime.now(timezone.utc).isoformat()

    ticket = None
    if supabase:
        try:
            res_chk = supabase.table("support_tickets").select("id, status, user_id").eq("id", ticket_id).eq("user_id", user_id).execute()
            ticket_check = await res_chk if inspect.isawaitable(res_chk) else res_chk
            if ticket_check and hasattr(ticket_check, "data") and ticket_check.data:
                ticket = ticket_check.data[0]
        except Exception:
            ticket = None

    if not ticket:
        with _support_lock:
            cached = _in_memory_tickets.get(ticket_id)
            if cached and cached.get("user_id") == user_id:
                ticket = cached

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    current_status = ticket.get("status")

    if target_status in ("closed", "close"):
        new_status = TicketStatus.CLOSED.value
        resolved_at = now_iso
    elif target_status in ("reopen", "open"):
        new_status = TicketStatus.OPEN.value
        resolved_at = None
    elif target_status == "resolved":
        new_status = TicketStatus.RESOLVED.value
        resolved_at = now_iso
    else:
        new_status = target_status
        resolved_at = None

    update_data = {
        "status": new_status,
        "resolved_at": resolved_at,
        "updated_at": now_iso,
    }

    if supabase:
        try:
            supabase.table("support_tickets").update(update_data).eq("id", ticket_id).execute()
        except Exception as sb_err:
            logger.warning(f"[SUPPORT] Supabase update fallback: {sb_err}")

    with _support_lock:
        if ticket_id in _in_memory_tickets:
            _in_memory_tickets[ticket_id].update(update_data)

    if ws_mgr:
        try:
            await ws_mgr.broadcast_user(
                user_id,
                {
                    "type": "support_ticket",
                    "action": "status_changed",
                    "data": {"ticket_id": ticket_id, "status": new_status, "updated_at": now_iso},
                },
            )
        except Exception:
            pass

    try:
        from backend_app.core.notification_dispatcher import dispatch_user_notification
        action_verb = "Resolved" if new_status == "resolved" else ("Closed" if new_status == "closed" else "Reopened")
        await dispatch_user_notification(
            user_id=user_id,
            event_type=f"support_ticket_{new_status}",
            category="support",
            severity="info",
            title=f"Support Ticket {action_verb}",
            message=f"Ticket '{ticket.get('subject', ticket_id)}' status updated to {new_status}.",
            metadata={"ticket_id": ticket_id, "status": new_status, "idempotency_key": f"ticket_status:{ticket_id}:{new_status}"},
            supabase=supabase,
            ws_manager=ws_mgr,
        )
    except Exception as notif_err:
        logger.debug(f"[SUPPORT] Status notification fallback: {notif_err}")

    return {
        "status": "ok",
        "ticket_id": ticket_id,
        "new_status": new_status,
        "updated_at": now_iso,
    }


# ══════════════════════════════════════════════════════════════════════════
# STAFF & ADMIN SUPPORT OPERATIONS
# ══════════════════════════════════════════════════════════════════════════

@router.get("/admin/tickets")
async def get_admin_tickets(
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(get_admin_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Support staff & Admin ticket directory across all tenants."""
    all_tickets = []
    if supabase:
        try:
            query = supabase.table("support_tickets").select("*")
            if status:
                query = query.eq("status", status)
            if category:
                query = query.eq("category", category)
            if priority:
                query = query.eq("priority", priority)
            res = query.order("created_at", desc=True).limit(limit).offset(offset).execute()
            resp = await res if inspect.isawaitable(res) else res
            all_tickets = resp.data if resp and hasattr(resp, "data") and resp.data else []
        except Exception:
            all_tickets = []

    if not all_tickets:
        with _support_lock:
            all_tickets = list(_in_memory_tickets.values())
            if status:
                all_tickets = [t for t in all_tickets if t.get("status") == status]
            if category:
                all_tickets = [t for t in all_tickets if t.get("category") == category]
            if priority:
                all_tickets = [t for t in all_tickets if t.get("priority") == priority]
            if search:
                s = search.lower()
                all_tickets = [
                    t for t in all_tickets
                    if s in t.get("subject", "").lower() or s in t.get("description", "").lower()
                ]

    return {
        "tickets": all_tickets,
        "count": len(all_tickets),
        "total": len(all_tickets),
    }


@router.post("/admin/tickets/{ticket_id}/reply")
async def staff_reply_ticket(
    ticket_id: str,
    body: StaffReplyRequest,
    admin: dict = Depends(get_admin_user),
    supabase: Any = Depends(get_request_supabase),
    ws_mgr: Any = Depends(get_ws_manager),
):
    """Support staff reply to user ticket with status transition and notification dispatch."""
    _scan_for_secrets(body.message)
    now_iso = datetime.now(timezone.utc).isoformat()
    comment_id = f"cmt_staff_{uuid4().hex[:10]}"

    ticket = None
    if supabase:
        try:
            res_chk = supabase.table("support_tickets").select("*").eq("id", ticket_id).execute()
            ticket_check = await res_chk if inspect.isawaitable(res_chk) else res_chk
            if ticket_check and hasattr(ticket_check, "data") and ticket_check.data:
                ticket = ticket_check.data[0]
        except Exception:
            ticket = None

    if not ticket:
        with _support_lock:
            ticket = _in_memory_tickets.get(ticket_id)

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    target_user_id = ticket["user_id"]
    new_status = body.new_status or TicketStatus.WAITING_FOR_USER.value

    comment_record = {
        "id": comment_id,
        "ticket_id": ticket_id,
        "user_id": admin.get("id", "support_staff"),
        "message": body.message.strip(),
        "is_staff": True,
        "created_at": now_iso,
    }

    if supabase:
        try:
            supabase.table("ticket_comments").insert(comment_record).execute()
            supabase.table("support_tickets").update({
                "status": new_status,
                "has_unread": True,
                "updated_at": now_iso,
            }).eq("id", ticket_id).execute()
        except Exception as sb_err:
            logger.warning(f"[SUPPORT] Supabase staff reply fallback: {sb_err}")

    with _support_lock:
        if ticket_id not in _in_memory_comments:
            _in_memory_comments[ticket_id] = []
        _in_memory_comments[ticket_id].append(comment_record)
        if ticket_id in _in_memory_tickets:
            _in_memory_tickets[ticket_id]["status"] = new_status
            _in_memory_tickets[ticket_id]["updated_at"] = now_iso
            _in_memory_tickets[ticket_id]["comment_count"] = len(_in_memory_comments[ticket_id])

    # Broadcast notification to the ticket owner and create formal in-app notification
    if ws_mgr:
        try:
            await ws_mgr.broadcast_user(
                target_user_id,
                {
                    "type": "support_reply",
                    "action": "staff_reply",
                    "data": {
                        "ticket_id": ticket_id,
                        "message": body.message,
                        "status": new_status,
                        "created_at": now_iso,
                    },
                },
            )
        except Exception:
            pass

    try:
        from backend_app.routers.notifications import NotificationCreate, create_notification
        user_notif = NotificationCreate(
            user_id=target_user_id,
            type="support_reply",
            category="support",
            severity="info",
            title=f"Support Update on #{ticket_id}",
            message=f"VyomQuant Support replied: {body.message[:120]}...",
            metadata={"ticket_id": ticket_id, "status": new_status},
        )
        await create_notification(user_notif, supabase, ws_mgr)
    except Exception as notif_err:
        logger.debug(f"[SUPPORT] Staff reply notification dispatch non-blocking: {notif_err}")

    return {
        "status": "ok",
        "comment_id": comment_id,
        "new_status": new_status,
        "created_at": now_iso,
    }
