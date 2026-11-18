"""
routers/support.py — Production-Grade Multi-Tenant Support & Help Center System.

Endpoints:
  GET  /api/support/faqs                        — List categorized FAQs with search
  GET  /api/support/categories                  — Available ticket categories & SLAs
  GET  /api/support/tickets                     — List user's support tickets
  POST /api/support/tickets                     — Create new ticket (attachment METADATA only)
  GET  /api/support/tickets/{ticket_id}         — Get ticket details with conversation history
  POST /api/support/tickets/{ticket_id}/comments — Add comment / reply to ticket
  PUT  /api/support/tickets/{ticket_id}         — Update ticket status (close/reopen/resolve)
  GET  /api/support/admin/tickets               — Staff listing of tickets (admin/support role)
  POST /api/support/admin/tickets/{ticket_id}/reply — Staff reply to ticket

SUP-02: this list previously named two endpoints that do not exist in this
module — `POST /tickets/{ticket_id}/attachments` and
`PUT /admin/tickets/{ticket_id}/status`. There are nine `@router` decorators
here and the list claimed eleven. A docstring is the first thing a reader
trusts about a router, so the two phantoms are removed rather than left to be
discovered by a 404. If either is wanted, it needs writing.

ATTACHMENTS ARE METADATA ONLY. `AttachmentMeta` carries a filename, a content
type and a size; no endpoint accepts file bytes and no storage bucket exists to
hold them. The extension blocklist in `_check_attachment` is a real check on
the declared filename, not a scan of uploaded content.
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
        "question": "What stops an order from bypassing the risk engine?",
        "answer": "Every live order must go through execute_with_idempotency, the single entry point the execution engine enforces: it accepts orders only from the bot runner (a direct or manual call is rejected), requires a strategy_id that exists in the database, computes a deterministic idempotency key for the execution window, and takes a database lock before the order is placed. A duplicate or out-of-band call is refused rather than silently re-executed.",
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
        "answer": "Binance (Testnet), Bybit (Testnet), and OKX (Demo Mode) support simulated trading with real API contracts. Coinbase, KuCoin, and Kraken run on production endpoints exclusively and have no public CCXT sandbox.",
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
        "answer": "Higher subscription tiers unlock more concurrent live bots and strategies, more exchange connections and ML models, and larger monthly allowances for backtests, optimization runs and ML training. Order submission speed is the same on every plan; tiering changes capacity, not execution latency.",
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
        # SUP-02: this answered "Yes ... you can attach log files (.log, .txt,
        # .json, .csv) or screenshots (.png, .jpg)". No endpoint accepts a file
        # and no storage bucket exists to put one in -- production has exactly
        # one bucket, `strategy-images`. What the ticket form actually offers is
        # a text input labelled "Attachment Filename / Log Trace", marked in
        # SupportCenter.jsx as a "Safe Attachment Simulation", and what the API
        # stores is `AttachmentMeta` -- a filename, a content type and a size,
        # with no bytes behind them. Telling a user their screenshot was
        # uploaded when nothing received it is the same class of claim CLAIM-01
        # (42c8ac96) corrected three of; this is a fourth, found by following
        # the attachment path to the storage layer and finding no storage.
        # The extension blocklist IS real and does apply to the filename, so
        # that half of the old answer is kept rather than dropped.
        "question": "Can I attach diagnostic logs to my support request?",
        "answer": "Not as uploaded files -- there is no file upload yet. Paste the relevant log lines or error text directly into the ticket description, which accepts up to 5000 characters. You can also record a filename or log trace in the optional attachment field so support knows what to ask you for; executable file types (.exe, .sh, .bat) are rejected there. Never paste API keys, secrets or private keys: submissions containing them are blocked automatically.",
    },
    {
        "id": "faq-18",
        "category": "general",
        "question": "How does the Emergency Kill Switch work?",
        "answer": "Engaging the Emergency Kill Switch in Risk Settings immediately blocks every new order and strategy execution on your account. It does not retroactively cancel orders already resting at an exchange or change a running deployment's status by itself -- use Close Position / Close All in Portfolio to cancel resting orders and flatten existing exposure.",
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
                # Fail closed. Falling back to the in-memory store here would answer
                # HTTP 200 with an empty (or stale) ticket list while the database is
                # actually unreachable, telling the user they have no tickets when the
                # truth is unknown. The in-memory store below is only a substitute for
                # a *deliberately unconfigured* backend, never for a broken one.
                logger.error(f"[SUPPORT] Ticket fetch failed for {user_id}: {sb_err}")
                raise HTTPException(
                    status_code=503,
                    detail={"error": "TICKETS_FETCH_FAILED", "message": str(sb_err)},
                )

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

    # SUP-01: support_tickets.id is `UUID PRIMARY KEY` (migrations/add_support_tables.sql).
    # A "tkt_"-prefixed id is not a UUID, so the insert below always raised against Supabase
    # (invalid input syntax for type uuid), caught by the except and silently rerouted to
    # the in-memory fallback dict -- which answers this one request but is lost on the next
    # worker restart and invisible to every OTHER worker process in the meantime. A real
    # UUID lets the real insert succeed, which is the actual fix; the in-memory path below
    # is now the genuine degraded-Supabase fallback it was always meant to be.
    ticket_id = str(uuid4())
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
            # SUP-02: this used to log a WARNING, copy the ticket into
            # `_in_memory_tickets` and fall through to `return` — so the user
            # received HTTP 200 and a real ticket id for a ticket the database
            # had rejected. The rejection was not hypothetical: until migration
            # 072 this insert named four columns support_tickets did not
            # declare, so PostgREST failed every statement with PGRST204 and
            # this except body ran on EVERY ticket for ~2.5 months (last stored
            # ticket 2026-08-18, keys added 2026-08-29).
            #
            # The in-memory dict cannot stand in for the table. The API runs
            # WORKERS=2, so a ticket parked in one worker's memory 404s when the
            # next request lands on the other, and it is gone on restart — and
            # the API task was being replaced every ~9.5 minutes during the
            # 2026-11-17 incident.
            #
            # So this now fails honestly. A 503 asks the user to retry, which
            # can succeed; a fabricated 200 guarantees their report is lost and
            # stops them retrying. Deliberately the OPPOSITE of the BILL-07
            # decision, where the invoice handler must NOT raise — there, the
            # caller is Razorpay, raising triggers webhook redelivery, and
            # redelivery extends the paid period, granting a second month for
            # one payment. Here the caller is a person who can press the button
            # again, and nothing is granted by trying.
            logger.error(
                "[SUPPORT] ticket insert REJECTED, nothing stored: %s", sb_err
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Your ticket could not be filed because the support "
                    "database rejected it. Nothing was saved. Please try "
                    "again; if it keeps failing, contact support through "
                    "another channel rather than resubmitting."
                ),
            ) from sb_err
    else:
        # No Supabase configured at all — local development and the test suite.
        # This is the genuine no-backend mode, and it is honest because there is
        # no database that could have accepted the row. It is NOT the path for
        # "a database exists and said no", which is handled above.
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
                # NO CLIENT IS PASSED, DELIBERATELY. `create_notification` resolves the
                # SERVICE-ROLE client when a caller supplies none
                # (`notification_dispatcher._resolve_persistence_client`), and this row
                # is addressed to `support_staff` -- NOT to the caller. Handing it the
                # ticket submitter's own request-scoped client asked that customer to
                # insert a row belonging to somebody else, which the owner policy on
                # `notifications` refuses. The failure was swallowed by the `except`
                # below and logged at debug, so this notification has never persisted;
                # it is part of why production held zero notification rows.
                #
                # `supabase` is still the gate: it tells us a database is configured at
                # all (the test suite overrides it to None). It is just no longer the
                # client the write goes through.
                await create_notification(admin_notification, ws_manager=ws_mgr)
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
    # SUP-01: ticket_comments.id is `UUID PRIMARY KEY` too - see create_ticket's own comment.
    comment_id = str(uuid4())

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
            # SUP-02: both calls were previously fire-and-forget. Neither
            # result was awaited, and `get_request_supabase` may hand back an
            # async client — in which case `.execute()` returns a coroutine
            # that is never awaited, the statement never reaches the database,
            # and the only symptom is a "coroutine was never awaited" warning.
            # That is the bug class commit 475cf655 ("Fix un-awaited Postgrest
            # coroutines") went through this codebase for; the ticket insert
            # above already guards it and this path was missed.
            res_ins = supabase.table("ticket_comments").insert(comment_record).execute()
            if inspect.isawaitable(res_ins):
                await res_ins

            res_upd = supabase.table("support_tickets").update({
                "has_unread": True,
                "status": TicketStatus.IN_PROGRESS.value,
                "updated_at": now_iso,
            }).eq("id", ticket_id).execute()
            if inspect.isawaitable(res_upd):
                await res_upd
        except Exception as sb_err:
            # Same reasoning as the ticket insert above: until 072,
            # `ticket_comments` had no `attachment` column, so this raised
            # PGRST204 and the reply was dropped while the caller got 200.
            logger.error(
                "[SUPPORT] comment insert REJECTED, nothing stored: %s", sb_err
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Your reply could not be saved because the support "
                    "database rejected it. Nothing was saved. Please try again."
                ),
            ) from sb_err
    else:
        # No Supabase configured — local development and the test suite, which
        # overrides `get_request_supabase` to None throughout. Appending here
        # only in that case: when the insert above DID succeed, a second copy
        # in memory would be read back alongside the stored row.
        with _support_lock:
            if ticket_id not in _in_memory_comments:
                _in_memory_comments[ticket_id] = []
            _in_memory_comments[ticket_id].append(comment_record)
            # Inside the lock: this reads len(_in_memory_comments[...]) that the
            # line above just changed, so it is part of the same mutation.
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
    # SUP-01: ticket_comments.id is `UUID PRIMARY KEY` - see create_ticket's own comment.
    comment_id = str(uuid4())

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
            # SUP-02: awaited, for the reason given on the user reply path.
            res_ins = supabase.table("ticket_comments").insert(comment_record).execute()
            if inspect.isawaitable(res_ins):
                await res_ins

            res_upd = supabase.table("support_tickets").update({
                "status": new_status,
                "has_unread": True,
                "updated_at": now_iso,
            }).eq("id", ticket_id).execute()
            if inspect.isawaitable(res_upd):
                await res_upd
        except Exception as sb_err:
            # A staff reply that is not stored is worse than the user's own
            # dropped reply: the customer is waiting on it, and support believes
            # it was sent. Fail loudly so the operator sees it and can resend,
            # rather than logging a warning nobody is watching.
            logger.error(
                "[SUPPORT] staff reply REJECTED, nothing stored: %s", sb_err
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "The reply could not be saved because the support database "
                    "rejected it. Nothing was saved and the customer has not "
                    "been answered. Please try again."
                ),
            ) from sb_err
    else:
        # No Supabase configured — see the user reply path.
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
        # Same reason as the admin notification on ticket creation: no client is passed,
        # so the service-role one is resolved. This row is addressed to
        # `target_user_id` -- the TICKET'S OWNER -- while the caller here is support
        # staff, so writing it through the staff member's request-scoped client asked
        # one user to insert a notification belonging to another. The owner policy
        # refuses that, and the `except` below swallowed it.
        await create_notification(user_notif, ws_manager=ws_mgr)
    except Exception as notif_err:
        logger.debug(f"[SUPPORT] Staff reply notification dispatch non-blocking: {notif_err}")

    return {
        "status": "ok",
        "comment_id": comment_id,
        "new_status": new_status,
        "created_at": now_iso,
    }
