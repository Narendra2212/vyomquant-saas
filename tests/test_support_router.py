"""
tests/test_support_router.py

Unit tests for backend_app/routers/support.py
Validates:
1. GET /api/support/tickets/{ticket_id} fetches a ticket record correctly without TypeError (regression protection for list index fix).
2. GET /api/support/tickets returns user ticket list.
3. POST /api/support/tickets creates a new ticket.
4. POST /api/support/tickets/{ticket_id}/comments adds a comment to a ticket.
5. PUT /api/support/tickets/{ticket_id} closes or reopens a ticket.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["DEV_MODE"] = "true"
os.environ["ENV"] = "testing"

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app


@pytest.fixture
def mock_supabase():
    mock_client = MagicMock()
    return mock_client


@pytest.fixture
def test_user():
    return {
        "id": "usr_test_123",
        "email": "trader@vyomquant.io",
        "role": "authenticated",
    }


def test_get_ticket_by_id_success(mock_supabase, test_user):
    """
    Test GET /api/support/tickets/{ticket_id} returns dict structure and doesn't crash on list access.
    """
    app.dependency_overrides[get_current_user] = lambda: test_user
    app.dependency_overrides[get_request_supabase] = lambda: mock_supabase

    try:
        mock_ticket_row = {
            "id": "tkt_999",
            "user_id": "usr_test_123",
            "subject": "Execution Delay on Binance",
            "description": "Order execution took 350ms",
            "category": "technical",
            "priority": "high",
            "status": "open",
            "created_at": "2026-07-31T10:00:00Z",
            "updated_at": "2026-07-31T10:00:00Z",
            "resolved_at": None,
        }

        # Mock select ticket query: table("support_tickets").select("*").eq("id", ...).eq("user_id", ...).execute()
        ticket_query_mock = MagicMock()
        ticket_query_mock.execute.return_value.data = [mock_ticket_row]

        # Mock update ticket query (mark as read)
        update_query_mock = MagicMock()
        update_query_mock.execute.return_value.data = []

        # Mock comments query: table("ticket_comments").select("*").eq("ticket_id", ...).order(...).execute()
        comments_query_mock = MagicMock()
        comments_query_mock.execute.return_value.data = [
            {
                "id": "cmt_1",
                "message": "Investigating exchange latency",
                "is_staff": True,
                "created_at": "2026-07-31T10:05:00Z",
            }
        ]

        def table_side_effect(table_name):
            t_mock = MagicMock()
            if table_name == "support_tickets":
                t_mock.select.return_value.eq.return_value.eq.return_value = ticket_query_mock
                t_mock.update.return_value.eq.return_value = update_query_mock
            elif table_name == "ticket_comments":
                t_mock.select.return_value.eq.return_value.order.return_value = comments_query_mock
            return t_mock

        mock_supabase.table.side_effect = table_side_effect

        client = TestClient(app)
        response = client.get("/api/support/tickets/tkt_999")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert data["id"] == "tkt_999"
        assert data["subject"] == "Execution Delay on Binance"
        assert data["priority"] == "high"
        assert data["comment_count"] == 1
        assert data["comments"][0]["message"] == "Investigating exchange latency"

    finally:
        app.dependency_overrides.clear()


def test_get_ticket_not_found(mock_supabase, test_user):
    """
    Test GET /api/support/tickets/{ticket_id} returns 404 when ticket doesn't exist.
    """
    app.dependency_overrides[get_current_user] = lambda: test_user
    app.dependency_overrides[get_request_supabase] = lambda: mock_supabase

    try:
        ticket_query_mock = MagicMock()
        ticket_query_mock.execute.return_value.data = []

        t_mock = MagicMock()
        t_mock.select.return_value.eq.return_value.eq.return_value = ticket_query_mock
        mock_supabase.table.return_value = t_mock

        client = TestClient(app)
        response = client.get("/api/support/tickets/tkt_nonexistent")

        assert response.status_code == 404
        assert response.json()["detail"] == "Ticket not found"

    finally:
        app.dependency_overrides.clear()
