"""
tests/test_ws_ticket_redemption.py — the WebSocket ticket's REDEMPTION side.

production-launch-hardening task 8.2 (backend half). Requirements 1.21, 2.21, 3.9.

WHY THIS FILE EXISTS. `POST /api/auth/ws-ticket` has issued tickets since Phase 7B, and
`PHASE_7C_AUTH_ADVERSARIAL_ACCEPTANCE_AUDIT.md` records `/ws/telemetry` resolving
`?token=` through "`_decode_hs256_token / verify_ws_ticket`". `verify_ws_ticket` did not
exist anywhere in the tree. Every ticket ever issued was unredeemable, and the audit
marked the feature PASS on the issuance half alone. These tests cover the half that was
missing, so "the ticket verifies" is an executed assertion rather than a claim in a
report.

CREDENTIALS ARE REFERENCED BY NAME, NEVER BY VALUE. No ticket and no JWT is printed,
asserted against literally, or recorded in a fixture — the point of the change under test
is that these strings do not end up anywhere durable, and a test that prints one would be
the same defect in a different file.

THE NINE ROUTES ARE ENUMERATED FROM THE ROUTER, not hand-listed, so a tenth route added
later without a `ticket` parameter fails here instead of shipping without one.
"""

from __future__ import annotations

import asyncio
import inspect
import secrets

import pytest
from fastapi import HTTPException

from backend_app.api_ws.ws_routes import ws_router
from backend_app.core.cache import redis_manager
from backend_app.core.websocket_auth import (WS_TICKET_TTL_SECONDS,
                                             verify_ws_ticket,
                                             ws_ticket_redis_key)
from backend_app.routers.auth import issue_ws_ticket

CAPTURE_USER_ID = "ticket-subject-uuid-0001"


def _run(coro):
    """Drive one coroutine to completion on a loop of its own.

    Nothing under test holds loop-bound state — the DEV_MODE ticket store is a plain
    dict — so a fresh loop per call costs nothing and keeps these tests independent of
    whatever loop policy the rest of the suite leaves behind.
    """
    return asyncio.run(coro)


#: The handler beneath `@limiter.limit("30/minute")`. The rate limit is a real contract
#: — `tests/regression/baseline/auth_token.json` records the decorator, and
#: `test_phase7b_auth_remediation.py` drives the route over HTTP — but it needs a live
#: `Request` to evaluate, and it is not what these tests are about. The store write is.
_issue_ws_ticket = getattr(issue_ws_ticket, "__wrapped__", None)
assert _issue_ws_ticket is not None, (
    "issue_ws_ticket is no longer wrapped; call it through the app instead"
)


async def _mint_ticket_for(user_id: str) -> str:
    """Issue a ticket through the real handler and return it."""
    response = await _issue_ws_ticket(request=None, current_user={"id": user_id})
    return response["ticket"]


@pytest.fixture(autouse=True)
def _clean_ticket_store():
    """Leave no ticket behind: the mock store is process-wide and a singleton."""
    yield
    store = getattr(redis_manager._mock_client, "_store", None)
    if isinstance(store, dict):
        for key in [k for k in store if str(k).startswith("ws_ticket:")]:
            store.pop(key, None)


# ══════════════════════════════════════════════════════════════════════════
#  SINGLE USE
# ══════════════════════════════════════════════════════════════════════════


class TestTicketIsSingleUse:
    """A ticket admits exactly one connection. Requirement 2.21."""

    def test_ticket_verifies_once_then_is_dead(self):
        ticket = _run(_mint_ticket_for(CAPTURE_USER_ID))

        first = _run(verify_ws_ticket(ticket))
        assert first is not None, "a freshly issued ticket must redeem"
        assert first["sub"] == CAPTURE_USER_ID
        assert first["auth_method"] == "ws_ticket"

        second = _run(verify_ws_ticket(ticket))
        assert second is None, "a redeemed ticket must not redeem a second time"

    def test_redemption_consumes_the_stored_key(self):
        """The key is gone after one redemption, not merely reported as spent."""
        ticket = _run(_mint_ticket_for(CAPTURE_USER_ID))
        store = redis_manager._mock_client._store
        assert ws_ticket_redis_key(ticket) in store

        assert _run(verify_ws_ticket(ticket)) is not None
        assert ws_ticket_redis_key(ticket) not in store

    def test_two_concurrent_redemptions_admit_exactly_one(self):
        """Racing redemptions must not both win — this is why GETDEL is atomic."""
        ticket = _run(_mint_ticket_for(CAPTURE_USER_ID))

        async def race():
            return await asyncio.gather(
                verify_ws_ticket(ticket), verify_ws_ticket(ticket)
            )

        outcomes = _run(race())
        admitted = [outcome for outcome in outcomes if outcome is not None]
        assert len(admitted) == 1, "exactly one of two racing redemptions may succeed"
        assert admitted[0]["sub"] == CAPTURE_USER_ID

    def test_two_tickets_for_one_user_are_independent(self):
        """Spending one ticket does not spend another. Reconnect must still work."""
        first_ticket = _run(_mint_ticket_for(CAPTURE_USER_ID))
        second_ticket = _run(_mint_ticket_for(CAPTURE_USER_ID))

        assert _run(verify_ws_ticket(first_ticket)) is not None
        assert _run(verify_ws_ticket(second_ticket)) is not None


# ══════════════════════════════════════════════════════════════════════════
#  EVERY OTHER OUTCOME IS A REFUSAL
# ══════════════════════════════════════════════════════════════════════════


class TestRedemptionFailsClosed:
    """Absence of evidence is never an admission."""

    def test_expired_ticket_is_refused(self):
        """Expiry is the store's TTL, so it is simulated by removing the key.

        `MockRedisClient.setex` keeps no TTL of its own, so expiry cannot be waited
        out against it; what expiry *is*, from the redeeming side, is "the key is no
        longer there". That is the state asserted, and it is the state Redis leaves
        behind `WS_TICKET_TTL_SECONDS` after issuance.
        """
        ticket = _run(_mint_ticket_for(CAPTURE_USER_ID))
        redis_manager._mock_client._store.pop(ws_ticket_redis_key(ticket), None)

        assert _run(verify_ws_ticket(ticket)) is None

    def test_ttl_is_within_the_requirement_cap(self):
        assert 0 < WS_TICKET_TTL_SECONDS <= 30

    def test_unknown_ticket_is_refused(self):
        """A well-formed value this server never minted."""
        assert _run(verify_ws_ticket(secrets.token_urlsafe(32))) is None

    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param("", id="empty"),
            pytest.param(None, id="absent"),
            pytest.param("   ", id="whitespace"),
            pytest.param(12345, id="not-a-string"),
            # Bounded before a Redis key is built out of it.
            pytest.param("x" * 4096, id="over-length"),
        ],
    )
    def test_malformed_credential_is_refused(self, bad):
        assert _run(verify_ws_ticket(bad)) is None

    def test_ticket_whose_stored_subject_is_blank_is_refused(self):
        """A stored row with no subject resolves to nobody, so it admits nobody."""
        ticket = _run(_mint_ticket_for(CAPTURE_USER_ID))
        redis_manager._mock_client._store[ws_ticket_redis_key(ticket)] = "   "

        assert _run(verify_ws_ticket(ticket)) is None

    def test_no_redis_redemption_returns_none(self, monkeypatch):
        """No ticket store means no ticket is good — not "assume it was"."""

        async def no_store(key):
            return None

        monkeypatch.setattr(redis_manager, "getdel", no_store)
        assert _run(verify_ws_ticket("any-outstanding-ticket-name")) is None

    def test_unreachable_redis_redemption_returns_none(self, monkeypatch):
        """A transport failure denies rather than propagating into the handshake."""

        async def blows_up(key):
            raise ConnectionError("ticket store unreachable")

        monkeypatch.setattr(redis_manager, "getdel", blows_up)
        assert _run(verify_ws_ticket("any-outstanding-ticket-name")) is None


# ══════════════════════════════════════════════════════════════════════════
#  ISSUANCE MUST NOT FAIL OPEN
# ══════════════════════════════════════════════════════════════════════════


class TestIssuanceFailsClosed:
    """A ticket that cannot be redeemed must not be issued at all."""

    def test_no_redis_issuance_returns_503(self, monkeypatch):
        async def unacknowledged(key, ttl, value):
            return None

        monkeypatch.setattr(redis_manager, "setex", unacknowledged)

        with pytest.raises(HTTPException) as raised:
            _run(_mint_ticket_for(CAPTURE_USER_ID))

        assert raised.value.status_code == 503
        assert raised.value.detail["error"] == "WS_TICKET_STORE_UNAVAILABLE"

    def test_503_payload_carries_no_exception_text(self, monkeypatch):
        """The client gets a code, not a stack trace or a driver message."""

        async def blows_up(key, ttl, value):
            raise ConnectionError("redis://ticket-store:6379 refused the connection")

        monkeypatch.setattr(redis_manager, "setex", blows_up)

        with pytest.raises(HTTPException) as raised:
            _run(_mint_ticket_for(CAPTURE_USER_ID))

        rendered = str(raised.value.detail)
        assert "ticket-store" not in rendered
        assert "ConnectionError" not in rendered

    def test_issued_ticket_is_stored_before_it_is_returned(self):
        """The success path is the inverse assertion: issued implies redeemable."""
        ticket = _run(_mint_ticket_for(CAPTURE_USER_ID))
        assert _run(verify_ws_ticket(ticket)) is not None


# ══════════════════════════════════════════════════════════════════════════
#  ALL NINE ROUTES, ENUMERATED FROM THE ROUTER
# ══════════════════════════════════════════════════════════════════════════

#: Every WebSocket route the module publishes, discovered rather than listed.
WS_ROUTES = [
    route
    for route in ws_router.routes
    if getattr(route, "path", "").startswith("/ws/")
]

#: Named so a failure identifies the route rather than an index.
WS_ROUTE_IDS = [route.path for route in WS_ROUTES]


def _query_parameters(route):
    """The handler's parameters, minus the WebSocket and any path parameters."""
    signature = inspect.signature(route.endpoint)
    return {
        name: parameter
        for name, parameter in signature.parameters.items()
        if name != "websocket"
    }


def _is_required(parameter) -> bool:
    """Whether the handshake itself rejects a connection that omits this parameter.

    `Query(...)` renders its missing default as `Ellipsis` on some FastAPI/Pydantic
    pairings and as `PydanticUndefined` on others, so both are recognised rather than
    the test quietly passing on whichever one it does not know about.
    """
    default = parameter.default
    inner = getattr(default, "default", default)
    return inner is Ellipsis or type(inner).__name__ == "PydanticUndefinedType"


class TestEveryRouteAcceptsBothCredentials:
    """The credential contract is uniform across the whole surface."""

    def test_nine_websocket_routes_are_published(self):
        """Pinned so a tenth route cannot be added without this file noticing."""
        assert len(WS_ROUTES) == 9, f"expected nine /ws/ routes, found {WS_ROUTE_IDS}"

    @pytest.mark.parametrize("route", WS_ROUTES, ids=WS_ROUTE_IDS)
    def test_route_accepts_a_ticket(self, route):
        assert "ticket" in _query_parameters(route), (
            f"{route.path} does not accept ?ticket="
        )

    @pytest.mark.parametrize("route", WS_ROUTES, ids=WS_ROUTE_IDS)
    def test_route_still_accepts_a_token(self, route):
        """The one-release overlap: a cached bundle keeps its socket."""
        assert "token" in _query_parameters(route), (
            f"{route.path} no longer accepts ?token=; the overlap release needs it"
        )

    @pytest.mark.parametrize("route", WS_ROUTES, ids=WS_ROUTE_IDS)
    def test_neither_credential_is_required_by_the_handshake(self, route):
        """Both are optional at the signature so *either* can carry the connection.

        A required `token` would reject a ticket-only client during the handshake,
        before any code in the body ran. The refusal for "neither presented" lives in
        the body instead, which the close-code tests below cover.
        """
        parameters = _query_parameters(route)
        for name in ("ticket", "token"):
            assert not _is_required(parameters[name]), (
                f"{route.path}: {name} is declared required"
            )


# ══════════════════════════════════════════════════════════════════════════
#  THE REFUSAL MOVED INTO THE BODY — IT MUST STILL REFUSE
#
#  Making `token` optional on four routes moved "no credential presented" from a
#  handshake rejection to a decision in the handler. So the handler has to be the thing
#  observed making it, on all nine, with the close codes those routes already used.
# ══════════════════════════════════════════════════════════════════════════


class _RecordingWebSocket:
    """Records `close`, and fails loudly if a connection is ever accepted."""

    def __init__(self):
        self.closed_with = None
        self.accepted = False
        self.client_state = None

    async def close(self, code=1000, reason=""):
        if self.closed_with is None:
            self.closed_with = (code, reason)

    async def accept(self):
        self.accepted = True
        raise AssertionError("an unauthenticated connection was accepted")

    async def send_text(self, _text):  # pragma: no cover - never reached
        raise AssertionError("an unauthenticated connection was written to")

    async def receive_text(self):  # pragma: no cover - never reached
        raise AssertionError("an unauthenticated connection was read from")


def _placeholder_arguments(route) -> dict:
    """Values for the handler's non-credential parameters, by annotation."""
    arguments = {}
    for name, parameter in _query_parameters(route).items():
        if name in ("ticket", "token"):
            continue
        arguments[name] = 1 if parameter.annotation is int else "placeholder"
    return arguments


class TestNoCredentialIsRefusedOnEveryRoute:
    """Requirement 3.9 — authentication still applies unchanged on every route."""

    @pytest.mark.parametrize("route", WS_ROUTES, ids=WS_ROUTE_IDS)
    def test_missing_both_credentials_closes_4001(self, route):
        socket = _RecordingWebSocket()
        _run(
            route.endpoint(
                websocket=socket,
                ticket=None,
                token=None,
                **_placeholder_arguments(route),
            )
        )

        assert not socket.accepted
        assert socket.closed_with is not None, f"{route.path} did not close"
        assert socket.closed_with[0] == 4001, (
            f"{route.path} closed with {socket.closed_with[0]}, expected 4001"
        )

    @pytest.mark.parametrize("route", WS_ROUTES, ids=WS_ROUTE_IDS)
    def test_unknown_ticket_closes_without_accepting(self, route):
        """A ticket this server never minted is refused with 4001 or 4003.

        Both codes are accepted because the two are already split across these routes
        by design — 4001 for "credential no good", 4003 for a check that could not be
        completed — and task 8.2 is not permitted to change which one a route uses.
        """
        socket = _RecordingWebSocket()
        _run(
            route.endpoint(
                websocket=socket,
                ticket=secrets.token_urlsafe(32),
                token=None,
                **_placeholder_arguments(route),
            )
        )

        assert not socket.accepted
        assert socket.closed_with is not None, f"{route.path} did not close"
        assert socket.closed_with[0] in (4001, 4003), (
            f"{route.path} closed with {socket.closed_with[0]}, expected 4001 or 4003"
        )
