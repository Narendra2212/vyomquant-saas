"""WS-01: the Billing page's account channel was refused at the handshake.

``/ws/user/{user_id}`` declared ``exchange_id`` as a REQUIRED query parameter. The only client
(Billing.jsx) holds the channel for account events and never sends one, so FastAPI failed
validation before the handler ran and uvicorn answered a bare 403. Production logs showed
``WebSocket /ws/user/<id>?ticket=... 403`` right after every ``POST /api/auth/ws-ticket 200``, and
the top bar painted "Not connected to the trading engine" on every Billing visit; a plan bought
there never refreshed live. Without ``exchange_id`` the channel now opens for account events and
touches no exchange.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_app.api_ws import ws_routes


class _Manager:
    def __init__(self):
        self.subscribed, self.unsubscribed = [], []

    async def subscribe(self, channel, key, ws):
        self.subscribed.append((channel, key))

    async def unsubscribe(self, channel, key, ws):
        self.unsubscribed.append((channel, key))


def _client(monkeypatch, *, authorised=True):
    manager = _Manager()
    monkeypatch.setattr(ws_routes, "get_ws_manager", lambda: manager)

    async def _resolve(ticket=None, claimed_user_id=None):
        return authorised

    monkeypatch.setattr(ws_routes, "_resolve_ws_subject", _resolve)

    def _no_vault():
        raise AssertionError("no exchange_id was given, so no exchange keys may be loaded")

    monkeypatch.setattr(ws_routes, "get_vault", _no_vault)
    app = FastAPI()
    app.include_router(ws_routes.ws_router)
    return TestClient(app), manager


def test_the_account_channel_opens_without_an_exchange(monkeypatch):
    client, manager = _client(monkeypatch)
    with client.websocket_connect("/ws/user/u-1?ticket=t") as ws:
        ws.send_text('{"action":"subscribe","channel":"billing"}')
    assert manager.subscribed == [("user", "u-1")]
    assert manager.unsubscribed == [("user", "u-1")], "the hold is released on disconnect"


def test_an_unauthorised_socket_is_still_refused(monkeypatch):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    client, manager = _client(monkeypatch, authorised=False)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/user/u-1?ticket=bad") as ws:
            ws.receive_text()
    assert exc.value.code == 4001
    assert manager.subscribed == []
