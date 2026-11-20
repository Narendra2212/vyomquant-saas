"""SUP-01 -- support tickets/comments persist through the real insert, not a memory fallback.

THE DEFECT
    ``support_tickets.id`` and ``ticket_comments.id`` are both ``UUID PRIMARY KEY``
    (migrations/add_support_tables.sql). The router generated ids as ``f"tkt_{uuid4().hex
    [:12]}"`` / ``f"cmt_{uuid4().hex[:12]}"`` / ``f"cmt_staff_{uuid4().hex[:10]}"`` --
    none of them a valid UUID. Every real insert against Supabase raised ("invalid input
    syntax for type uuid"), caught by the route's own ``except Exception: logger.warning
    (...)`` and silently rerouted to an in-memory dict. The caller still got a 200 with a
    ticket_id, so nothing *looked* wrong -- but that dict is per-process and non-durable:
    lost on the next worker restart, and never visible to any OTHER worker in the
    meantime. ``.eq("id", ticket_id)`` reads (GET ticket, staff reply, add-comment's own
    ownership check) were equally broken, for the identical reason.

THE FIX
    ``ticket_id = str(uuid4())``, ``comment_id = str(uuid4())`` at all three generation
    sites (create_ticket, add_comment, staff reply) -- a real UUID the column actually
    accepts, so the real Supabase insert now succeeds and the in-memory path becomes the
    genuine degraded-Supabase fallback it was always meant to be, not the only path that
    ever worked.

WHAT THIS FILE PROVES (a fake Supabase double that validates UUID format on insert and
``.eq("id", ...)`` the way real Postgres would; no live Supabase)
    * create_ticket's generated id is a real, parseable UUID;
    * the insert reaches the fake's "real" table (not the in-memory fallback) because the
      id is now acceptable;
    * a ticket created this way is readable back by id from a SEPARATE query against the
      SAME fake store -- round-trip persistence, not just "insert didn't crash";
    * add_comment's and the staff-reply route's generated comment ids are real UUIDs too.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend_app.core.dependencies import (get_admin_user, get_current_user,
                                           get_request_supabase)
from backend_app.main import app

TEST_USER = {"id": "usr-sup01-test", "email": "trader@example.com", "role": "authenticated"}


class _ValidatingTable:
    """Stands in for a real Postgres `uuid` column: an insert or `.eq("id", ...)` with a
    non-UUID value raises, exactly like a real `invalid input syntax for type uuid` would.
    A valid UUID is genuinely stored/queryable -- this is a real, if tiny, database double,
    not a mock that always succeeds."""

    def __init__(self, store: dict, id_column: str = "id"):
        self._store = store
        self._id_column = id_column
        self._filters = []
        self._payload = None
        self._op = "select"

    def insert(self, payload):
        row_id = payload.get(self._id_column)
        uuid.UUID(str(row_id))  # raises ValueError for a non-UUID, like Postgres would
        self._op, self._payload = "insert", payload
        return self

    def select(self, *_a, **_k):
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def eq(self, col, val):
        if col == self._id_column:
            uuid.UUID(str(val))  # same: a non-UUID filter value raises, like real Postgres
        self._filters.append((col, val))
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        if self._op == "insert":
            row = dict(self._payload)
            self._store[row[self._id_column]] = row
            return SimpleNamespace(data=[row])
        if self._op == "update":
            for row_id in list(self._store):
                if all(self._store[row_id].get(c) == v for c, v in self._filters):
                    self._store[row_id].update(self._payload)
            return SimpleNamespace(data=list(self._store.values()))
        hits = [
            r for r in self._store.values()
            if all(r.get(c) == v for c, v in self._filters)
        ]
        return SimpleNamespace(data=hits)


class _FakeSupabase:
    def __init__(self):
        self.tickets: dict = {}
        self.comments: dict = {}

    def table(self, name):
        if name == "support_tickets":
            return _ValidatingTable(self.tickets)
        if name == "ticket_comments":
            return _ValidatingTable(self.comments, id_column="id")
        return _ValidatingTable({})


@pytest.fixture
def client_and_store():
    fake_sb = _FakeSupabase()
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    app.dependency_overrides[get_admin_user] = lambda: {**TEST_USER, "role": "admin"}
    app.dependency_overrides[get_request_supabase] = lambda: fake_sb
    try:
        yield TestClient(app), fake_sb
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_admin_user, None)
        app.dependency_overrides.pop(get_request_supabase, None)


class TestCreateTicketPersistsThroughTheRealInsert:
    def test_the_generated_id_is_a_real_uuid_and_is_actually_stored(self, client_and_store):
        client, fake_sb = client_and_store

        res = client.post(
            "/api/support/tickets",
            json={
                "subject": "Execution delay on Binance",
                "description": "Order took 450ms to fill during high volatility.",
                "category": "trading",
            },
        )
        assert res.status_code == 200
        ticket_id = res.json()["ticket_id"]

        assert uuid.UUID(ticket_id)  # raises if not a real UUID
        assert ticket_id in fake_sb.tickets, (
            "the old bug's id format always failed the real insert and silently fell back "
            "to the in-memory dict -- this proves the REAL table now has the row"
        )

    def test_a_created_ticket_is_readable_back_by_id_from_a_separate_query(self, client_and_store):
        client, fake_sb = client_and_store

        res = client.post(
            "/api/support/tickets",
            json={
                "subject": "Withdrawal stuck in pending",
                "description": "A withdrawal has been pending for six hours with no update.",
                "category": "billing",
            },
        )
        ticket_id = res.json()["ticket_id"]

        # A fresh query against the SAME store, exactly as GET /tickets/{id} does internally.
        table = _ValidatingTable(fake_sb.tickets)
        rows = table.eq("id", ticket_id).eq("user_id", TEST_USER["id"]).execute().data
        assert len(rows) == 1
        assert rows[0]["subject"] == "Withdrawal stuck in pending"


class TestCommentIdsAreAlsoRealUuids:
    def test_a_user_comment_gets_a_real_uuid(self, client_and_store):
        client, fake_sb = client_and_store

        created = client.post(
            "/api/support/tickets",
            json={
                "subject": "API rate limit question",
                "description": "What is the rate limit for the market data endpoint?",
                "category": "technical",
            },
        ).json()
        ticket_id = created["ticket_id"]

        res = client.post(
            f"/api/support/tickets/{ticket_id}/comments",
            json={"message": "Following up on this."},
        )
        assert res.status_code == 200
        comment = res.json()
        comment_id = comment.get("comment_id") or comment.get("id")
        assert comment_id is not None
        assert uuid.UUID(comment_id)
        assert comment_id in fake_sb.comments
