"""tests/test_sec12_exchange_test_endpoint_is_rate_limited.py

SEC-12 (P3) -- ``POST /api/exchanges/test`` dials the named venue with WHATEVER
``api_key``/``secret_key``/etc. the request body carries, and reports back whether they
are valid and what balance they hold. Nothing requires those credentials to belong to
the caller. With no route-specific limit, any authenticated session could use this
platform as a high-volume checker for someone else's leaked or stolen exchange API
keys: submit a candidate pair, learn immediately whether it is live, at whatever rate
the platform's generic request handling allowed -- "an unrestricted validity oracle",
per the finding's own title.

THE FIX
-------
``@limiter.limit("10/minute")`` on ``backend_app.routers.exchange.test_connection`` --
the same bound ``routers/auth.py`` already uses for account-enumeration-prone
endpoints. ``require_aal2`` was deliberately NOT added (see the handler's own docstring
for the reasoning): it would also contradict
``tests/test_auth01_require_aal2_is_wired.py::test_read_and_test_handlers_do_not_require_aal2``,
which pins, for its own reasons, that this handler must NOT require AAL2.

WHAT THIS FILE PROVES
----------------------
1. the decorator is present on ``test_connection`` specifically, by source inspection
   (not merely present somewhere in the file);
2. the limiter mechanism it uses genuinely rejects a request over budget, driven end to
   end through the real route function mounted on a FastAPI app with the real limiter
   object wired exactly as ``main.py`` wires it -- with ``ConnectionEngine`` stubbed so
   no test run ever dials a real exchange or any third-party network endpoint.

Uses the real, importable ``backend_app.core.rate_limit.limiter`` singleton, which this
process resolves to in-memory storage under ``DEV_MODE=true`` (no ``REDIS_URL``) -- see
``backend_app/core/rate_limit.py``'s own ``resolve_rate_limit_config``. No Redis needed.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402
from slowapi.middleware import SlowAPIMiddleware  # noqa: E402

from backend_app.core.dependencies import get_current_user  # noqa: E402
from backend_app.core.rate_limit import limiter  # noqa: E402
import backend_app.routers.exchange as exchange_module  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
EXCHANGE_SOURCE = REPO / "backend_app" / "routers" / "exchange.py"


def test_the_limiter_decorator_is_applied_to_test_connection_specifically():
    """Static check: parses the module and asserts a ``limiter.limit`` call decorates
    the ``test_connection`` function node, not merely appears somewhere in the file.
    """
    tree = ast.parse(EXCHANGE_SOURCE.read_text(encoding="utf-8"))
    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "test_connection":
            found = node
            break
    assert found is not None, "test_connection was not found in backend_app/routers/exchange.py"

    def _is_limiter_limit_call(decorator) -> bool:
        if not isinstance(decorator, ast.Call):
            return False
        func = decorator.func
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "limit"
            and isinstance(func.value, ast.Name)
            and func.value.id == "limiter"
        )

    assert any(_is_limiter_limit_call(d) for d in found.decorator_list), (
        "test_connection must carry a @limiter.limit(...) decorator -- SEC-12's own "
        "title names this endpoint as an UNRESTRICTED oracle; removing the decorator "
        "reintroduces exactly that."
    )


class _FakeConnectionEngine:
    """Stands in for the real ``ConnectionEngine`` so this test never dials a real
    exchange or touches the network, however many requests it sends through the route.
    """

    def __init__(self, *args, **kwargs):
        pass

    async def connect(self):
        raise ValueError("stub: no real network in this test")

    async def disconnect(self):
        pass


def _rate_limited_app(monkeypatch, user: dict):
    monkeypatch.setattr(exchange_module, "ConnectionEngine", _FakeConnectionEngine)

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, lambda request, exc: fastapi.responses.JSONResponse(
        status_code=429, content={"error": "rate_limited"}
    ))
    app.add_middleware(SlowAPIMiddleware)
    app.add_api_route(
        "/test", exchange_module.test_connection, methods=["POST"]
    )
    app.dependency_overrides[get_current_user] = lambda: user

    return TestClient(app)


@pytest.fixture
def aal1_user():
    return {"id": "user-sec12", "app_metadata": {"aal": "aal1"}}


@pytest.fixture(autouse=True)
def _trust_x_forwarded_for(monkeypatch):
    """The limiter's key function (SEC-10's ``trusted_client_address``) ignores
    ``X-Forwarded-For`` entirely unless ``TRUSTED_PROXY_HOPS`` is set, falling back to
    the TestClient's single fixed socket-peer address for every request. Without this,
    every test in this file would share one bucket in the limiter's process-lifetime
    in-memory store, each interfering with the others' counts. Setting it lets each
    test give itself (and, within one test, each simulated caller) its own key.
    """
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")


def test_the_eleventh_request_in_a_minute_from_the_same_caller_is_rejected(monkeypatch, aal1_user):
    client = _rate_limited_app(monkeypatch, aal1_user)
    body = {
        "exchange_id": "binance",
        "api_key": "candidate-key",
        "secret_key": "candidate-secret",
    }
    headers = {"X-Forwarded-For": "203.0.113.10"}

    statuses = [client.post("/test", json=body, headers=headers).status_code for _ in range(11)]

    # The first 10 reach the real handler (and fail there, cheaply, via the stub --
    # never a 429); the 11th is the one the rate limiter itself must reject.
    assert statuses[:10] != [429] * 10, "the limit must allow the configured budget through"
    assert 429 in statuses, (
        "no request in 11 rapid calls to /test from the same caller was rate-limited -- "
        "the oracle this finding names is unbounded again"
    )
    assert statuses[-1] == 429, "the 11th request specifically must be the one rejected for a 10/minute limit"


def test_two_different_callers_each_get_their_own_budget(monkeypatch):
    """NEGATIVE CONTROL: the limiter must not conflate two different clients into one
    shared budget (it keys on the trusted client address, not globally) -- a bug here
    would make the limit far stricter in production than "10/minute per caller" once
    more than one real user is testing a connection at once.
    """
    client = _rate_limited_app(monkeypatch, {"id": "user-a", "app_metadata": {"aal": "aal1"}})
    body = {"exchange_id": "binance", "api_key": "k", "secret_key": "s"}

    # Caller A spends its whole budget first.
    for _ in range(10):
        client.post("/test", json=body, headers={"X-Forwarded-For": "203.0.113.20"})

    # Caller B, a different address, must still have a full budget of its own.
    statuses_b = [
        client.post("/test", json=body, headers={"X-Forwarded-For": "203.0.113.21"}).status_code
        for _ in range(10)
    ]
    assert 429 not in statuses_b, (
        "a different caller's requests were counted against caller A's budget -- the "
        "limiter is not actually per-caller"
    )
