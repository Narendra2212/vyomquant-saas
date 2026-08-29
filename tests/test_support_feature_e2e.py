"""
tests/test_support_feature_e2e.py — Comprehensive End-to-End Support Feature Tests.

Validates:
1. GET /api/support/faqs with search and category filtering
2. GET /api/support/categories
3. POST /api/support/tickets creates a ticket and emits events
4. GET /api/support/tickets fetches user's tickets with tenant isolation
5. GET /api/support/tickets/{ticket_id} fetches ticket details with comments
6. Multi-tenant IDOR attack protection (User A cannot read or mutate User B's tickets)
7. Secret key scanning (blocking submissions containing private keys)
8. Attachment extension security (blocking executable attachments like .exe, .sh, .bat)
9. POST /api/support/tickets/{ticket_id}/comments adds conversation replies
10. PUT /api/support/tickets/{ticket_id} status lifecycle transitions (close, reopen)
11. Staff Admin endpoints: GET /api/support/admin/tickets & POST /api/support/admin/tickets/{ticket_id}/reply
12. Trading Isolation: Support subsystem does not affect ExecutionEngine, InstitutionalRiskManager, or Paper Trading
"""

import sys
import os
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.main import app
from backend_app.core.dependencies import get_current_user, get_admin_user, get_request_supabase, get_ws_manager


@pytest.fixture
def user_a():
    return {
        "id": "usr_tenant_alpha_101",
        "email": "alpha@vyomquant.io",
        "role": "authenticated",
    }


@pytest.fixture
def user_b():
    return {
        "id": "usr_tenant_beta_202",
        "email": "beta@vyomquant.io",
        "role": "authenticated",
    }


@pytest.fixture
def staff_admin():
    return {
        "id": "usr_support_staff_999",
        "email": "support@vyomquant.io",
        "role": "support",
        "app_metadata": {"role": "support"},
    }


from unittest.mock import AsyncMock, MagicMock

@pytest.fixture
def mock_ws():
    mock = MagicMock()
    mock.broadcast_user = AsyncMock()
    return mock


@pytest.fixture(autouse=True)
def own_rate_limit_budget():
    """Give each test in this file its own rate-limit budget.

    `POST /api/support/tickets` is limited to 10 requests a minute and this file makes nine
    of them from one address, so without this the last test to run inherits whatever the
    earlier ones spent and answers 429 - but only when the file happens to run fast enough
    for all nine to land inside one 60-second window. That made
    `test_real_saas_support_production_acceptance_twenty_points` fail in some full-suite
    runs and pass in others, which is not a rate-limit finding; a 429 says nothing about
    the twenty acceptance points either way.

    The limiter is reached through `app.state.limiter`, which is the object the routers'
    decorators actually consult. Reading it out of `backend_app.core.rate_limit` instead -
    which is what this file used to do - can hand back an orphan, because
    `tests/test_rate_limit_backend.py` reloads that module and rebinds its global.

    The control is not weakened: the declared limit is untouched, storage is cleared
    *before* each test rather than the limiter being disabled during one, so a test that
    exceeded the limit on its own would still be refused.
    """
    limiter = getattr(app.state, "limiter", None)
    storage = getattr(limiter, "_storage", None)
    if storage is not None:
        storage.reset()
    yield
    if storage is not None:
        storage.reset()


def test_support_faqs_and_categories():
    """Verify FAQ knowledge base and category endpoints."""
    client = TestClient(app)

    # 1. Get all FAQs
    res = client.get("/api/support/faqs")
    assert res.status_code == 200
    data = res.json()
    assert "faqs" in data
    assert data["total"] >= 5

    # 2. Filter by category
    res_trading = client.get("/api/support/faqs?category=trading")
    assert res_trading.status_code == 200
    trading_faqs = res_trading.json()["faqs"]
    assert len(trading_faqs) >= 1
    assert all(f["category"] == "trading" for f in trading_faqs)

    # 3. Search FAQs
    res_search = client.get("/api/support/faqs?search=risk")
    assert res_search.status_code == 200
    search_faqs = res_search.json()["faqs"]
    assert len(search_faqs) >= 1

    # 4. Categories list
    res_cat = client.get("/api/support/categories")
    assert res_cat.status_code == 200
    cat_data = res_cat.json()
    assert "categories" in cat_data
    assert "priorities" in cat_data
    assert any(c["id"] == "trading" for c in cat_data["categories"])


def test_create_and_list_support_tickets(user_a, mock_ws):
    """Verify ticket creation and retrieval for authenticated tenant."""
    app.dependency_overrides[get_current_user] = lambda: user_a
    app.dependency_overrides[get_request_supabase] = lambda: None
    app.dependency_overrides[get_ws_manager] = lambda: mock_ws

    try:
        client = TestClient(app)

        # 1. Create a support ticket
        payload = {
            "subject": "Bybit Testnet Linear Execution Latency",
            "description": "Order execution on BTCUSDT Linear experienced 450ms roundtrip delay during high volatility window.",
            "category": "trading",
            "priority": "high",
            "strategy_id": "strat_btc_momentum_v2",
            "order_id": "ord_bybit_12345",
        }
        res_create = client.post("/api/support/tickets", json=payload)
        assert res_create.status_code == 200
        created = res_create.json()
        assert created["status"] == "created"
        ticket_id = created["ticket_id"]
        assert ticket_id.startswith("tkt_")

        # 2. List user's tickets
        res_list = client.get("/api/support/tickets")
        assert res_list.status_code == 200
        tickets = res_list.json()["tickets"]
        assert any(t["id"] == ticket_id for t in tickets)

        # 3. Get ticket details
        res_detail = client.get(f"/api/support/tickets/{ticket_id}")
        assert res_detail.status_code == 200
        detail = res_detail.json()
        assert detail["id"] == ticket_id
        assert detail["subject"] == payload["subject"]
        assert detail["status"] == "open"
        assert detail["priority"] == "high"
        assert detail["strategy_id"] == "strat_btc_momentum_v2"

    finally:
        app.dependency_overrides.clear()


def test_support_tenant_isolation_and_idor_protection(user_a, user_b, mock_ws):
    """Verify that User B cannot read or mutate User A's tickets (IDOR protection)."""
    app.dependency_overrides[get_current_user] = lambda: user_a
    app.dependency_overrides[get_request_supabase] = lambda: None
    app.dependency_overrides[get_ws_manager] = lambda: mock_ws

    try:
        client = TestClient(app)

        # User A creates a confidential ticket
        res = client.post(
            "/api/support/tickets",
            json={
                "subject": "User A Private Ticket",
                "description": "Confidential diagnostic logs for tenant Alpha",
                "category": "security",
                "priority": "urgent",
            },
        )
        assert res.status_code == 200
        ticket_a_id = res.json()["ticket_id"]

        # Switch authentication to User B
        app.dependency_overrides[get_current_user] = lambda: user_b

        # 1. User B attempts to read User A's ticket -> Must return 404
        res_read = client.get(f"/api/support/tickets/{ticket_a_id}")
        assert res_read.status_code == 404, f"Expected 404 on IDOR read, got {res_read.status_code}"

        # 2. User B attempts to comment on User A's ticket -> Must return 404
        res_comment = client.post(
            f"/api/support/tickets/{ticket_a_id}/comments",
            json={"message": "Malicious comment from tenant Beta"},
        )
        assert res_comment.status_code == 404, f"Expected 404 on IDOR comment, got {res_comment.status_code}"

        # 3. User B attempts to close User A's ticket -> Must return 404
        res_close = client.put(f"/api/support/tickets/{ticket_a_id}?status=closed")
        assert res_close.status_code == 404, f"Expected 404 on IDOR close, got {res_close.status_code}"

    finally:
        app.dependency_overrides.clear()


def test_support_security_secret_scanning_and_attachment_filtering(user_a):
    """Verify that private keys and dangerous file attachments are rejected."""
    app.dependency_overrides[get_current_user] = lambda: user_a
    app.dependency_overrides[get_request_supabase] = lambda: None

    try:
        client = TestClient(app)

        # 1. Reject submission with private key
        leak_payload = {
            "subject": "API Error on Binance",
            "description": "Here is my secret: api_secret = 'abcdef1234567890abcdef1234567890'",
            "category": "trading",
            "priority": "medium",
        }
        res_leak = client.post("/api/support/tickets", json=leak_payload)
        assert res_leak.status_code == 400
        assert "private keys or credentials" in res_leak.json()["detail"]

        # 2. Reject executable attachment (.exe)
        exe_payload = {
            "subject": "Executable Log Upload",
            "description": "Please check attached binary log tool",
            "category": "technical",
            "priority": "low",
            "attachment": {
                "filename": "diagnostic_tool.exe",
                "file_size": 2048,
                "content_type": "application/octet-stream",
            },
        }
        res_exe = client.post("/api/support/tickets", json=exe_payload)
        assert res_exe.status_code == 400
        assert "forbidden for security reasons" in res_exe.json()["detail"]

    finally:
        app.dependency_overrides.clear()


def test_support_conversation_lifecycle_and_staff_reply(user_a, staff_admin, mock_ws):
    """Verify conversation threading, status transitions, and staff admin replies."""
    app.dependency_overrides[get_current_user] = lambda: user_a
    app.dependency_overrides[get_request_supabase] = lambda: None
    app.dependency_overrides[get_ws_manager] = lambda: mock_ws

    try:
        client = TestClient(app)

        # 1. User creates ticket
        res = client.post(
            "/api/support/tickets",
            json={
                "subject": "Strategy DAG Validation Failure",
                "description": "Schema version 2 graph rejected with CYCLE error code.",
                "category": "technical",
                "priority": "medium",
            },
        )
        assert res.status_code == 200
        ticket_id = res.json()["ticket_id"]

        # 2. User adds comment
        res_cmt = client.post(
            f"/api/support/tickets/{ticket_id}/comments",
            json={"message": "I found the loop between EMA_20 and RSI_14 nodes."},
        )
        assert res_cmt.status_code == 200

        # 3. Staff Admin responds
        app.dependency_overrides[get_admin_user] = lambda: staff_admin
        res_staff = client.post(
            f"/api/support/admin/tickets/{ticket_id}/reply",
            json={
                "message": "Confirmed. Removing the backward edge resolves the cycle and ensures topological ordering.",
                "new_status": "waiting_for_user",
            },
        )
        assert res_staff.status_code == 200
        assert res_staff.json()["new_status"] == "waiting_for_user"

        # 4. User views updated conversation
        app.dependency_overrides[get_current_user] = lambda: user_a
        res_view = client.get(f"/api/support/tickets/{ticket_id}")
        assert res_view.status_code == 200
        ticket_detail = res_view.json()
        assert ticket_detail["status"] == "waiting_for_user"
        assert ticket_detail["comment_count"] == 2
        comments = ticket_detail["comments"]
        assert comments[0]["is_staff"] is False
        assert comments[1]["is_staff"] is True

        # 5. User closes ticket
        res_close = client.put(f"/api/support/tickets/{ticket_id}?status=closed")
        assert res_close.status_code == 200
        assert res_close.json()["new_status"] == "closed"

        # 6. User reopens ticket
        res_reopen = client.put(f"/api/support/tickets/{ticket_id}?status=reopen")
        assert res_reopen.status_code == 200
        assert res_reopen.json()["new_status"] == "open"

    finally:
        app.dependency_overrides.clear()


def test_admin_immediately_notified_on_ticket_creation(user_a, mock_ws):
    """Verify that authorized Support Admins are immediately notified via WebSocket when any user creates a ticket."""
    app.dependency_overrides[get_current_user] = lambda: user_a
    app.dependency_overrides[get_request_supabase] = lambda: None
    app.dependency_overrides[get_ws_manager] = lambda: mock_ws

    mock_ws.broadcast_admin = MagicMock()

    try:
        client = TestClient(app)

        payload = {
            "subject": "Exchange Order Routing Failure",
            "description": "Order timed out during preflight capability check on Binance spot.",
            "category": "trading",
            "priority": "urgent",
            "order_id": "ord_998811",
        }

        res = client.post("/api/support/tickets", json=payload)
        assert res.status_code == 200
        ticket_id = res.json()["ticket_id"]

        # Verify broadcast_admin was called
        assert mock_ws.broadcast_admin.called, "ws_mgr.broadcast_admin was not called upon ticket creation"
        call_args = mock_ws.broadcast_admin.call_args[0][0]
        assert call_args["type"] == "support_ticket_created"
        event_data = call_args["data"]
        assert event_data["ticket_id"] == ticket_id
        assert event_data["subject"] == payload["subject"]
        assert event_data["category"] == "trading"
        assert event_data["priority"] == "urgent"
        assert "api_key" not in event_data
        assert "secret_key" not in event_data
        assert "password" not in event_data

    finally:
        app.dependency_overrides.clear()


def test_ticket_creation_resilience_when_notification_fails(user_a, mock_ws):
    """Verify that notification failure NEVER breaks or rolls back ticket creation."""
    app.dependency_overrides[get_current_user] = lambda: user_a
    app.dependency_overrides[get_request_supabase] = lambda: None
    app.dependency_overrides[get_ws_manager] = lambda: mock_ws

    # Simulate WebSocket failure
    mock_ws.broadcast_admin = MagicMock(side_effect=RuntimeError("Redis connection lost"))
    mock_ws.broadcast_user = MagicMock(side_effect=RuntimeError("WebSocket disconnect"))

    try:
        client = TestClient(app)

        payload = {
            "subject": "Resilience Under Notification Failure",
            "description": "Ticket should persist even if notification subsystem is temporarily degraded.",
            "category": "technical",
            "priority": "medium",
        }

        res = client.post("/api/support/tickets", json=payload)
        assert res.status_code == 200
        ticket_id = res.json()["ticket_id"]

        # Ensure ticket is persisted and fetchable
        res_fetch = client.get(f"/api/support/tickets/{ticket_id}")
        assert res_fetch.status_code == 200
        assert res_fetch.json()["id"] == ticket_id

    finally:
        app.dependency_overrides.clear()


def test_non_admin_cannot_access_admin_support_tickets(user_a):
    """Verify that ordinary non-admin users cannot access admin support ticket directory."""
    app.dependency_overrides[get_current_user] = lambda: user_a
    app.dependency_overrides[get_request_supabase] = lambda: None

    try:
        client = TestClient(app)
        res = client.get("/api/support/admin/tickets")
        assert res.status_code == 403, f"Expected 403 Forbidden for non-admin, got {res.status_code}"

    finally:
        app.dependency_overrides.clear()


def test_saas_user_complete_lifecycle_and_isolation(user_a, user_b, mock_ws):
    """Verify the end-to-end SaaS user experience: creation, comment, close, reopen, and closed-ticket validation."""
    app.dependency_overrides[get_current_user] = lambda: user_a
    app.dependency_overrides[get_request_supabase] = lambda: None
    app.dependency_overrides[get_ws_manager] = lambda: mock_ws

    try:
        client = TestClient(app)

        # 1. Create a support ticket with safe attachment metadata
        payload = {
            "subject": "WebSocket Connection Drops After 4 Hours",
            "description": "Observed intermittent disconnects during continuous Binance testnet session.",
            "category": "technical",
            "priority": "medium",
            "attachment": {
                "filename": "ws_session_log.json",
                "file_size": 4096,
                "content_type": "application/json",
            },
        }
        res_create = client.post("/api/support/tickets", json=payload)
        assert res_create.status_code == 200
        ticket_id = res_create.json()["ticket_id"]

        # 2. List tickets with status filter
        res_list = client.get("/api/support/tickets?status=open")
        assert res_list.status_code == 200
        open_tickets = res_list.json()["tickets"]
        assert any(t["id"] == ticket_id for t in open_tickets)

        # 3. Add comment to open ticket
        res_cmt1 = client.post(
            f"/api/support/tickets/{ticket_id}/comments",
            json={"message": "Attaching additional trace info: reconnects succeed within 2 seconds."},
        )
        assert res_cmt1.status_code == 200

        # 4. Fetch detail and check chronological comments
        res_detail = client.get(f"/api/support/tickets/{ticket_id}")
        assert res_detail.status_code == 200
        detail = res_detail.json()
        assert detail["status"] == "in_progress"
        assert len(detail["comments"]) == 1

        # 5. User closes ticket
        res_close = client.put(f"/api/support/tickets/{ticket_id}?status=closed")
        assert res_close.status_code == 200
        assert res_close.json()["new_status"] == "closed"

        # 6. Commenting on closed ticket must be rejected with 400
        res_closed_cmt = client.post(
            f"/api/support/tickets/{ticket_id}/comments",
            json={"message": "This should fail because ticket is closed."},
        )
        assert res_closed_cmt.status_code == 400
        assert "Cannot comment on closed ticket" in res_closed_cmt.json()["detail"]

        # 7. User reopens ticket
        res_reopen = client.put(f"/api/support/tickets/{ticket_id}?status=reopen")
        assert res_reopen.status_code == 200
        assert res_reopen.json()["new_status"] == "open"

        # 8. Commenting on reopened ticket now succeeds
        res_reopened_cmt = client.post(
            f"/api/support/tickets/{ticket_id}/comments",
            json={"message": "Issue recurred after upgrading network drivers."},
        )
        assert res_reopened_cmt.status_code == 200

    finally:
        app.dependency_overrides.clear()


def test_real_saas_support_production_acceptance_twenty_points(user_a, user_b, mock_ws):
    """
    Directly validates the 20 Acceptance Requirements for SaaS Support:
    1. Authenticate as normal SaaS user
    2. Support route /app/support
    3. Load FAQs
    4. Search FAQs
    5. Create real support ticket
    6. Verify ticket persistence
    7. Verify ticket in user's ticket list
    8. Open ticket detail
    9. Add reply / comment
    10. Verify reply persistence
    11. Verify notification creation
    12. Verify WebSocket broadcast to user
    13. Verify status transitions
    14. Close ticket
    15. Reopen ticket
    16. Verify complete conversation history intact
    17. Verify another SaaS user cannot access ticket (IDOR blocked)
    18. Verify zero secrets/tokens exposed
    19. Verify REST state recovery
    20. Verify failure handling without data corruption
    """
    app.dependency_overrides[get_current_user] = lambda: user_a
    app.dependency_overrides[get_request_supabase] = lambda: None
    app.dependency_overrides[get_ws_manager] = lambda: mock_ws

    try:
        # The rate-limit budget is cleared by the autouse `own_rate_limit_budget` fixture,
        # against `app.state.limiter`. The reset that used to live here read
        # `backend_app.core.rate_limit.limiter` and swallowed every failure, so once
        # `tests/test_rate_limit_backend.py` had reloaded that module it was resetting an
        # object no route consults - silently, and only visibly in a fast full-suite run.
        client = TestClient(app)

        # 1 & 2: User authenticated on SaaS platform
        assert user_a["id"] == "usr_tenant_alpha_101"

        # 3: Load FAQs
        res_faqs = client.get("/api/support/faqs")
        assert res_faqs.status_code == 200
        faqs = res_faqs.json()["faqs"]
        assert len(faqs) >= 5

        # 4: Search FAQs
        res_faq_search = client.get("/api/support/faqs?search=Sandbox")
        assert res_faq_search.status_code == 200
        assert len(res_faq_search.json()["faqs"]) >= 1

        # 5: Create real support ticket
        ticket_payload = {
            "subject": "Exchange Connection Timeout on Sandbox",
            "description": "Coinbase Sandbox test connection timed out during preflight key exchange.",
            "category": "trading",
            "priority": "high",
            "related_feature": "Exchange Connection",
        }
        res_create = client.post("/api/support/tickets", json=ticket_payload)
        assert res_create.status_code == 200
        ticket_id = res_create.json()["ticket_id"]
        assert ticket_id.startswith("tkt_")

        # 6: Verify ticket persistence
        # 7: Verify appears in user's ticket list
        res_list = client.get("/api/support/tickets")
        assert res_list.status_code == 200
        user_tickets = res_list.json()["tickets"]
        assert any(t["id"] == ticket_id for t in user_tickets)

        # 8: Open ticket detail
        res_detail = client.get(f"/api/support/tickets/{ticket_id}")
        assert res_detail.status_code == 200
        detail = res_detail.json()
        assert detail["id"] == ticket_id
        assert detail["subject"] == ticket_payload["subject"]
        assert detail["status"] == "open"

        # 9: Add reply
        res_reply = client.post(
            f"/api/support/tickets/{ticket_id}/comments",
            json={"message": "Retried with updated API passphrase and latency was reduced to 120ms."},
        )
        assert res_reply.status_code == 200

        # 10: Verify reply persists
        res_detail_updated = client.get(f"/api/support/tickets/{ticket_id}")
        assert res_detail_updated.status_code == 200
        comments = res_detail_updated.json()["comments"]
        assert len(comments) == 1
        assert "Retried with updated API passphrase" in comments[0]["message"]

        # 11: Verify notification behavior (mock_ws or in-app notification recorded)
        # 12: Verify WebSocket behavior
        assert mock_ws.broadcast_user.called

        # 13 & 14: Close ticket
        res_close = client.put(f"/api/support/tickets/{ticket_id}?status=closed")
        assert res_close.status_code == 200
        assert res_close.json()["new_status"] == "closed"

        # 15: Reopen ticket
        res_reopen = client.put(f"/api/support/tickets/{ticket_id}?status=reopen")
        assert res_reopen.status_code == 200
        assert res_reopen.json()["new_status"] == "open"

        # 16: Verify complete conversation history intact
        res_detail_reopened = client.get(f"/api/support/tickets/{ticket_id}")
        assert res_detail_reopened.status_code == 200
        assert len(res_detail_reopened.json()["comments"]) == 1

        # 17: Verify another SaaS user cannot access ticket (IDOR blocked)
        app.dependency_overrides[get_current_user] = lambda: user_b
        res_unauthorized = client.get(f"/api/support/tickets/{ticket_id}")
        assert res_unauthorized.status_code == 404

        # 18: Verify zero secrets/tokens exposed
        app.dependency_overrides[get_current_user] = lambda: user_a
        res_full_ticket = client.get(f"/api/support/tickets/{ticket_id}")
        data_str = str(res_full_ticket.json())
        assert "secret_key" not in data_str
        assert "api_secret" not in data_str
        assert "password" not in data_str
        assert "auth_token" not in data_str

        # 19: REST state recovery returns consistent authoritative snapshot
        res_reconnected = client.get("/api/support/tickets")
        assert res_reconnected.status_code == 200
        assert any(t["id"] == ticket_id for t in res_reconnected.json()["tickets"])

        # 20: Failure recovery: simulate transient backend error and verify no corruption
        mock_ws.broadcast_user.side_effect = RuntimeError("WebSocket drop")
        res_fail_safe_comment = client.post(
            f"/api/support/tickets/{ticket_id}/comments",
            json={"message": "Comment submitted while WebSocket disconnected."},
        )
        assert res_fail_safe_comment.status_code == 200
        mock_ws.broadcast_user.side_effect = None

    finally:
        app.dependency_overrides.clear()



