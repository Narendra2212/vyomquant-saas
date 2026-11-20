"""EXCH-SAVE-01 — Test Connection and Save must verify a credential against the SAME
environment.

Found in production: a trader's Bybit key passed "Test Connection" and was then refused by
"Save". One of the contributing defects was that ``store_keys`` builds its
``ConnectionEngine`` with ``testnet=body.sandbox`` (EXCH-06) while ``test_connection`` did
not pass ``testnet`` at all. With "Testnet / Sandbox Mode" ticked, the test dialled
PRODUCTION and reported success for a credential the save would then verify against the
SANDBOX — two buttons on one form disagreeing about the same key.

These tests drive the real ``test_connection`` handler through FastAPI with a recording stub
in place of ``ConnectionEngine`` (no network), and assert the environment it was asked for.
"""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402
from slowapi.middleware import SlowAPIMiddleware  # noqa: E402

from backend_app.core.dependencies import get_current_user  # noqa: E402
from backend_app.core.rate_limit import limiter  # noqa: E402
import backend_app.routers.exchange as exchange_module  # noqa: E402


class _RecordingConnectionEngine:
    calls: list[dict] = []

    def __init__(self, **kwargs):
        type(self).calls.append(kwargs)

    async def connect(self):
        raise ValueError("stub: no real network in this test")

    async def disconnect(self):
        pass


@pytest.fixture
def client(monkeypatch):
    _RecordingConnectionEngine.calls = []
    monkeypatch.setattr(exchange_module, "ConnectionEngine", _RecordingConnectionEngine)
    # Each test gets its own limiter bucket (see test_sec12 for why this is needed).
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(
        RateLimitExceeded,
        lambda request, exc: fastapi.responses.JSONResponse(status_code=429, content={}),
    )
    app.add_middleware(SlowAPIMiddleware)
    app.add_api_route("/test", exchange_module.test_connection, methods=["POST"])
    app.dependency_overrides[get_current_user] = lambda: {"id": "user-exch-save01"}
    return TestClient(app)


def _post(client, ip: str, **extra):
    body = {"exchange_id": "bybit", "api_key": "k", "secret_key": "s", **extra}
    return client.post("/test", json=body, headers={"X-Forwarded-For": ip})


def test_a_testnet_request_is_tested_against_the_testnet(client):
    _post(client, "198.51.100.11", sandbox=True)
    assert _RecordingConnectionEngine.calls, "the handler never built a ConnectionEngine"
    assert _RecordingConnectionEngine.calls[-1].get("testnet") is True, (
        "Test Connection with Testnet / Sandbox Mode ticked dialled production — the save "
        "would verify the same key against the sandbox and disagree"
    )


def test_a_request_without_the_toggle_is_tested_against_production(client):
    _post(client, "198.51.100.12")
    assert _RecordingConnectionEngine.calls[-1].get("testnet") is False


def test_an_explicit_live_request_is_tested_against_production(client):
    _post(client, "198.51.100.13", sandbox=False)
    assert _RecordingConnectionEngine.calls[-1].get("testnet") is False


def test_the_save_path_reads_the_same_field_the_same_way():
    """Both handlers must derive the environment from the same expression, so they cannot
    drift apart again without this test noticing."""
    import inspect

    expr = 'testnet=getattr(body, "sandbox", False)'
    assert expr in inspect.getsource(exchange_module.store_keys)
    assert expr in inspect.getsource(exchange_module.test_connection)
