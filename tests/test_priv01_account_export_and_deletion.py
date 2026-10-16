"""
tests/test_priv01_account_export_and_deletion.py

PRIV-01: "No account deletion or data export." Neither existed anywhere in this codebase.
backend/account_lifecycle.py adds both; this file tests the two functions directly against
doubles for every collaborator, plus the two routes end to end against the real FastAPI app.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend_app.backend import account_lifecycle as al
from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app

USER_ID = "user-priv01-1"
USER = {"id": USER_ID, "email": "trader@example.com"}


def run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════════════
# The double for a direct-table-read / profile-update client
# ══════════════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client: "_FakeSupabase", table: str):
        self._client = client
        self._table = table
        self._filters: Dict[str, Any] = {}
        self._mode = "select"
        self._payload: Optional[Dict[str, Any]] = None

    def select(self, *_a, **_kw):
        self._mode = "select"
        return self

    def update(self, payload, *_a, **_kw):
        self._mode = "update"
        self._payload = payload
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def execute(self):
        if self._client.raise_on == self._table:
            raise RuntimeError(f"{self._table} read did not complete")
        rows = self._client.tables.get(self._table, [])
        matched = [
            r for r in rows if all(str(r.get(k)) == str(v) for k, v in self._filters.items())
        ]
        if self._mode == "update":
            if not matched:
                return _Resp([])
            for row in matched:
                row.update(self._payload)
            self._client.updates.append((self._table, dict(self._payload)))
            return _Resp(matched)
        return _Resp(matched)


class _FakeSupabase:
    def __init__(self, tables: Optional[Dict[str, List[Dict[str, Any]]]] = None):
        self.tables = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.raise_on: Optional[str] = None
        self.updates: List[Any] = []

    def table(self, name: str) -> _Query:
        return _Query(self, name)


# ══════════════════════════════════════════════════════════════════════════
# 1. export_account_data
# ══════════════════════════════════════════════════════════════════════════


def test_export_reads_every_direct_table_scoped_to_the_user(monkeypatch):
    sb = _FakeSupabase(
        tables={
            "profiles": [{"id": USER_ID, "username": "trader1"}, {"id": "other", "username": "x"}],
            "strategies": [{"id": "s1", "user_id": USER_ID}, {"id": "s2", "user_id": "other"}],
            "strategy_backtests": [{"id": "b1", "user_id": USER_ID}],
            "library_subscriptions": [{"id": "sub1", "user_id": USER_ID}],
            "library_strategies": [{"id": "l1", "author_id": USER_ID}],
            "marketplace_settlements": [{"id": "st1", "purchaser_id": USER_ID}],
        }
    )
    monkeypatch.setattr(al.paper_repo, "list_sessions", lambda sb, uid: [{"id": "ps1"}])
    monkeypatch.setattr(
        al, "APIKeyVault",
        lambda: type("V", (), {"list_connected_exchanges": lambda self, uid: ["binance"]})(),
    )

    result = run(al.export_account_data(sb, user_id=USER_ID))

    assert result["user_id"] == USER_ID
    cats = result["categories"]
    assert cats["profiles"]["ok"] is True
    assert cats["profiles"]["count"] == 1
    assert cats["profiles"]["rows"][0]["id"] == USER_ID
    assert cats["strategies"]["count"] == 1
    assert cats["strategies"]["rows"][0]["id"] == "s1"
    assert cats["strategy_backtests"]["count"] == 1
    assert cats["library_subscriptions"]["count"] == 1
    assert cats["library_strategies"]["count"] == 1
    assert cats["marketplace_settlements"]["count"] == 1
    assert cats["paper_sessions"]["ok"] is True
    assert cats["paper_sessions"]["rows"] == [{"id": "ps1"}]
    assert cats["connected_exchanges"]["rows"] == ["binance"]


def test_export_never_includes_decrypted_exchange_credentials(monkeypatch):
    sb = _FakeSupabase()
    monkeypatch.setattr(al.paper_repo, "list_sessions", lambda sb, uid: [])
    monkeypatch.setattr(
        al, "APIKeyVault",
        lambda: type("V", (), {"list_connected_exchanges": lambda self, uid: ["binance"]})(),
    )

    result = run(al.export_account_data(sb, user_id=USER_ID))

    dumped = str(result)
    assert "api_key" not in dumped.lower()
    assert "secret" not in dumped.lower()
    assert result["categories"]["connected_exchanges"]["rows"] == ["binance"]


def test_a_failing_category_does_not_abort_the_whole_export(monkeypatch):
    sb = _FakeSupabase(tables={"profiles": [{"id": USER_ID}]})
    sb.raise_on = "strategies"
    monkeypatch.setattr(al.paper_repo, "list_sessions", lambda sb, uid: [])
    monkeypatch.setattr(
        al, "APIKeyVault",
        lambda: type("V", (), {"list_connected_exchanges": lambda self, uid: []})(),
    )

    result = run(al.export_account_data(sb, user_id=USER_ID))

    assert result["categories"]["profiles"]["ok"] is True
    assert result["categories"]["strategies"]["ok"] is False
    assert "error" in result["categories"]["strategies"]


# ══════════════════════════════════════════════════════════════════════════
# 2. delete_account
# ══════════════════════════════════════════════════════════════════════════


def test_delete_account_stops_deployments_sessions_and_revokes_keys(monkeypatch):
    sb = _FakeSupabase(tables={"profiles": [{"id": USER_ID, "email": "trader@example.com"}]})
    stop_deployments = AsyncMock(return_value={"stopped": ["dep-1"], "failed": []})
    monkeypatch.setattr(al, "stop_deployments_for_reason", stop_deployments)

    monkeypatch.setattr(
        al.paper_repo, "list_sessions",
        lambda sb, uid: [
            {"id": "ps-running", "session_state": "RUNNING"},
            {"id": "ps-paused", "session_state": "PAUSED"},
            {"id": "ps-stopped", "session_state": "STOPPED"},
            {"id": "ps-created", "session_state": "CREATED"},
        ],
    )
    stop_paper = AsyncMock(return_value=None)
    monkeypatch.setattr(al, "stop_paper_session", stop_paper)

    deleted_keys = []

    class _Vault:
        def list_connected_exchanges(self, uid):
            return ["binance", "kraken"]

        def delete_exchange_keys(self, uid, exchange_id):
            deleted_keys.append(exchange_id)
            return True

    monkeypatch.setattr(al, "APIKeyVault", lambda: _Vault())

    summary = run(al.delete_account(sb, user_id=USER_ID, user=USER))

    stop_deployments.assert_awaited_once()
    assert stop_deployments.call_args.kwargs["user_id"] == USER_ID
    assert stop_deployments.call_args.kwargs["triggered_by"] == al.TRIGGER_USER

    # Only the two ACTIVE sessions are stopped, in the order listed.
    assert stop_paper.await_count == 2
    stopped_ids = {call.args[2] for call in stop_paper.await_args_list}
    assert stopped_ids == {"ps-running", "ps-paused"}
    assert set(summary["paper_sessions"]["stopped"]) == {"ps-running", "ps-paused"}
    assert summary["paper_sessions"]["failed"] == []

    assert set(deleted_keys) == {"binance", "kraken"}
    assert set(summary["exchange_keys"]["revoked"]) == {"binance", "kraken"}

    profile = sb.tables["profiles"][0]
    assert profile["is_frozen"] is True
    assert profile["email"] != "trader@example.com"
    assert profile["username"] is None
    assert profile["full_name"] is None
    assert summary["profile"]["ok"] is True


def test_delete_account_raises_when_the_profile_update_matches_no_row(monkeypatch):
    """The one step that must land - a profile row that cannot be found/updated must fail
    the whole operation rather than report success over an unchanged, still-identifiable
    account."""
    sb = _FakeSupabase(tables={"profiles": []})  # no row for this user at all
    monkeypatch.setattr(al, "stop_deployments_for_reason", AsyncMock(return_value={}))
    monkeypatch.setattr(al.paper_repo, "list_sessions", lambda sb, uid: [])
    monkeypatch.setattr(
        al, "APIKeyVault",
        lambda: type("V", (), {"list_connected_exchanges": lambda self, uid: []})(),
    )

    with pytest.raises(RuntimeError, match="matched no row"):
        run(al.delete_account(sb, user_id=USER_ID, user=USER))


def test_delete_account_tolerates_one_failing_exchange_revocation(monkeypatch):
    sb = _FakeSupabase(tables={"profiles": [{"id": USER_ID}]})
    monkeypatch.setattr(al, "stop_deployments_for_reason", AsyncMock(return_value={}))
    monkeypatch.setattr(al.paper_repo, "list_sessions", lambda sb, uid: [])

    class _Vault:
        def list_connected_exchanges(self, uid):
            return ["binance", "broken_exchange"]

        def delete_exchange_keys(self, uid, exchange_id):
            if exchange_id == "broken_exchange":
                raise RuntimeError("vault write did not complete")
            return True

    monkeypatch.setattr(al, "APIKeyVault", lambda: _Vault())

    summary = run(al.delete_account(sb, user_id=USER_ID, user=USER))

    assert summary["exchange_keys"]["revoked"] == ["binance"]
    assert summary["exchange_keys"]["failed"] == ["broken_exchange"]
    assert summary["profile"]["ok"] is True, (
        "one exchange failing to revoke must not stop the profile from being anonymised"
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. The routes
# ══════════════════════════════════════════════════════════════════════════


def test_export_endpoint_requires_auth():
    client = TestClient(app)
    res = client.get("/api/user/account/export")
    assert res.status_code in (401, 403)


def test_delete_endpoint_requires_the_confirmation_phrase():
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_request_supabase] = lambda: _FakeSupabase(
        tables={"profiles": [{"id": USER_ID}]}
    )
    try:
        client = TestClient(app)
        res = client.request(
            "DELETE", "/api/user/account", json={"confirm": "please delete"}
        )
        assert res.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_delete_endpoint_succeeds_with_the_exact_confirmation_phrase(monkeypatch):
    monkeypatch.setattr(al, "stop_deployments_for_reason", AsyncMock(return_value={}))
    monkeypatch.setattr(al.paper_repo, "list_sessions", lambda sb, uid: [])
    monkeypatch.setattr(
        al, "APIKeyVault",
        lambda: type("V", (), {"list_connected_exchanges": lambda self, uid: []})(),
    )

    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_request_supabase] = lambda: _FakeSupabase(
        tables={"profiles": [{"id": USER_ID}]}
    )
    try:
        client = TestClient(app)
        res = client.request(
            "DELETE", "/api/user/account", json={"confirm": "DELETE MY ACCOUNT"}
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["profile"]["ok"] is True
        assert body["profile"]["is_frozen"] is True
    finally:
        app.dependency_overrides.clear()
