"""ORD-01: GET /api/orders/open answered 500 for an account with no key for the venue."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_current_user, get_vault
from backend_app.routers import orders


class _Vault:
    def __init__(self, exc=None):
        self.exc = exc

    def load_decrypted_keys(self, uid, ex):
        if self.exc:
            raise self.exc
        return {"api_key": "k", "secret_key": "s"}


def _client(vault, monkeypatch=None):
    app = FastAPI()
    app.include_router(orders.router, prefix="/api/orders")
    app.dependency_overrides[get_current_user] = lambda: {"id": "11111111-1111-1111-1111-111111111111"}
    app.dependency_overrides[get_vault] = lambda: vault
    return TestClient(app, raise_server_exceptions=False)


def test_no_connected_exchange_is_404_not_500():
    r = _client(_Vault(ValueError("No keys found for u/binance."))).get("/api/orders/open?exchange_id=binance")
    assert r.status_code == 404
    assert "EXCHANGE_NOT_CONNECTED" in r.text


def test_a_venue_failure_is_502_not_an_empty_list(monkeypatch):
    async def _boom(**k):
        raise RuntimeError("venue down")

    monkeypatch.setattr(orders, "get_or_create_exchange", _boom)
    r = _client(_Vault()).get("/api/orders/open?exchange_id=binance")
    assert r.status_code == 502
    assert "EXCHANGE_UNAVAILABLE" in r.text


def test_an_owner_mismatch_is_not_disguised_as_not_connected():
    r = _client(_Vault(PermissionError("owner mismatch"))).get("/api/orders/open?exchange_id=binance")
    assert r.status_code == 500 and "EXCHANGE_NOT_CONNECTED" not in r.text
