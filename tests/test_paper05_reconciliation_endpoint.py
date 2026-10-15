"""
tests/test_paper05_reconciliation_endpoint.py - the admin surface over PAPER-05's repair tool.

``backend/paper/paper_reconciliation.py`` and its own test file prove the comparison itself is
correct; this file proves the one thing they cannot: that
``GET /api/admin/paper/sessions/{session_id}/reconcile`` actually wires it up -- gated behind
``get_admin_user``, reading through the service-role client so an admin can reconcile a session
that is not their own, and translating a clean/dirty/unknown/refused report into the right HTTP
answer.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_admin_user
from backend_app.main import app
from backend_app.routers import admin as admin_router
from tests.test_paper_replay import (
    OTHER_USER,
    SESSION,
    USER,
    _pinned_feed_clock,  # noqa: F401 -- pytest fixture, used by name via test parameters
    _run_the_fixture_session,
)


def _as_admin():
    app.dependency_overrides[get_admin_user] = lambda: {
        "id": "admin-1", "app_metadata": {"role": "admin"},
    }


def _clear_overrides():
    app.dependency_overrides.clear()


def test_reconcile_endpoint_requires_admin(_pinned_feed_clock) -> None:
    client = TestClient(app)
    res = client.get(
        f"/api/admin/paper/sessions/{SESSION}/reconcile", params={"user_id": USER}
    )
    assert res.status_code in (401, 403)


def test_reconcile_endpoint_reports_a_clean_session(_pinned_feed_clock, monkeypatch) -> None:
    supabase, _config, _account_id = _run_the_fixture_session(_pinned_feed_clock)
    monkeypatch.setattr(admin_router, "get_service_supabase", lambda: supabase)
    _as_admin()
    try:
        client = TestClient(app)
        res = client.get(
            f"/api/admin/paper/sessions/{SESSION}/reconcile", params={"user_id": USER}
        )
    finally:
        _clear_overrides()

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["clean"] is True
    assert body["mismatches"] == []
    assert body["session_id"] == SESSION
    assert body["user_id"] == USER


def test_reconcile_endpoint_reports_a_tampered_session(_pinned_feed_clock, monkeypatch) -> None:
    from decimal import Decimal

    supabase, _config, _account_id = _run_the_fixture_session(_pinned_feed_clock)
    account = supabase.accounts[0]
    account["available_balance"] = str(
        Decimal(str(account["available_balance"])) + Decimal("1")
    )
    monkeypatch.setattr(admin_router, "get_service_supabase", lambda: supabase)
    _as_admin()
    try:
        client = TestClient(app)
        res = client.get(
            f"/api/admin/paper/sessions/{SESSION}/reconcile", params={"user_id": USER}
        )
    finally:
        _clear_overrides()

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["clean"] is False
    assert "balances" in body["mismatches"]


def test_reconcile_endpoint_404s_for_an_unknown_session(_pinned_feed_clock, monkeypatch) -> None:
    supabase, _config, _account_id = _run_the_fixture_session(_pinned_feed_clock)
    monkeypatch.setattr(admin_router, "get_service_supabase", lambda: supabase)
    _as_admin()
    try:
        client = TestClient(app)
        res = client.get(
            "/api/admin/paper/sessions/44444444-4444-4444-8444-444444444444/reconcile",
            params={"user_id": USER},
        )
    finally:
        _clear_overrides()

    assert res.status_code == 404


def test_reconcile_endpoint_404s_for_another_tenants_session(
    _pinned_feed_clock, monkeypatch
) -> None:
    """The same non-disclosure ``paper_replay.replay`` already gives, carried through unchanged.

    Asking to reconcile this session under ``OTHER_USER`` must answer identically to asking for a
    session that does not exist at all -- a distinct answer would tell the caller the session
    exists and whose it is.
    """
    supabase, _config, _account_id = _run_the_fixture_session(_pinned_feed_clock)
    monkeypatch.setattr(admin_router, "get_service_supabase", lambda: supabase)
    _as_admin()
    try:
        client = TestClient(app)
        res = client.get(
            f"/api/admin/paper/sessions/{SESSION}/reconcile", params={"user_id": OTHER_USER}
        )
    finally:
        _clear_overrides()

    assert res.status_code == 404
