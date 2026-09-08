# -*- coding: utf-8 -*-
"""Task 33.2 — the rate-limit key helper, and the limits declared on top of it.

WHAT THIS FILE ASSERTS, AND WHY EACH CLAIM IS MADE WHERE IT IS
    1. :func:`caller_or_address` in its three states — a token that verifies, a token that does
       not, and no token at all — driven against a real ``starlette.requests.Request`` rather
       than a stub, because the function reads ``request.headers`` and ``request.client`` and a
       double would let either of those be wrong.
    2. That ``slowapi`` 0.1.9 really does evaluate TWO stacked ``@limiter.limit`` decorators with
       DIFFERENT ``key_func``s on one route. This is the load-bearing assumption of Requirement
       6.7's "120/60s per authenticated caller **and** 60/60s per source address", and reading
       the library's source is not the same as running it: ``Limiter._route_limits`` is keyed by
       ``f"{func.__module__}.{func.__name__}"`` and the inner wrapper sets
       ``request.state._rate_limiting_complete``, either of which could have made the second
       decorator a no-op. Asserted behaviourally, over separate caller and address budgets.
    3. That a refused request answers ``MARKETPLACE_RATE_LIMITED`` (429) through the one
       structured envelope, with **no figure** in the body, and that the handler body did not run
       — Requirement 6.11's "return no Listing data and leave the Listing unchanged" is a claim
       about work not done, so it is asserted by counting the work.
    4. That every limit in ``design.md``'s per-endpoint table is registered where the table says,
       read out of the registry ``slowapi`` consults per request rather than out of the source.

WHY THE ENFORCEMENT TESTS DO NOT USE ``TestClient``
    Requirement 6.7 splits its two windows by *source address*, and Starlette's ``TestClient``
    hard-codes ``scope["client"]`` to ``["testclient", 50000]`` (see
    ``starlette.testclient._TestClientTransport.handle_request``) with no parameter to vary it.
    A test that cannot change the address cannot show that the address bucket is a bucket. So the
    address- and caller-separation tests call ``Limiter._check_request_limit`` — the exact method
    the decorator's wrapper calls, which walks ``_route_limits`` and evaluates each limit with
    its own ``key_func`` — against synthetic requests whose address and ``Authorization`` header
    are both under the test's control. The end-to-end claims (the 429 body, the un-run handler,
    the handler registered on the real app) do go through ``TestClient``, where the fixed address
    costs nothing.

NO ``asyncio.run`` ANYWHERE
    Deliberately. This repository drives one shared event loop
    (``tests/test_paper_order_lifecycle_writes._run_coroutine`` and its ``_HARNESS_LOOP`` note);
    a fresh loop per call exhausts Windows loopback ports. Nothing here needs a loop: the key
    functions and ``_check_request_limit`` are synchronous, and ``TestClient`` owns its own
    portal.

Validates: Requirements 6.7, 6.11, 22.4
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set

import jwt
import pytest
from fastapi import FastAPI, Request
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request as StarletteRequest
from starlette.testclient import TestClient

from backend_app.backend.marketplace.errors import (
    ERROR_ENVELOPE_KEYS,
    MARKETPLACE_RATE_LIMITED,
)
from backend_app.core.rate_limit import limiter
from backend_app.core.rate_limit_keys import (
    ADDRESS_PREFIX,
    CALLER_PREFIX,
    caller_or_address,
    marketplace_rate_limit_handler,
    source_address,
)
from backend_app.routers import library as library_router
from backend_app.routers import paper_trading as paper_router

# The limiter's counters are process-wide and this file deliberately exhausts several of them.
# The reset helper task 28.1 already wrote is reused rather than duplicated — one definition of
# "forget what the limiter has counted", so a change to the storage backend has one place to go.
from tests.test_task_28_1_session_routes import _reset_rate_limit_counters

#: The HS256 secret ``tests/conftest.py`` exports and ``auth_middleware``'s test path reads.
_TEST_SECRET = "dev-secret-change-in-production"

#: ``decode_token_local`` tries the Supabase ES256 JWKS first and only falls back to the HS256
#: test secret for a token claiming this issuer. Anything else it refuses outright.
_TEST_ISSUER = "algo22-test"

_CALLER_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
_CALLER_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"

_ADDRESS_X = "198.51.100.11"
_ADDRESS_Y = "198.51.100.22"
_ADDRESS_Z = "198.51.100.33"


# ══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _token(subject: str, *, expires_in: int = 3600, issuer: str = _TEST_ISSUER) -> str:
    """A token ``decode_token_local`` accepts, or - with a changed issuer or lifetime - refuses."""
    return jwt.encode(
        {
            "sub": subject,
            "email": f"{subject}@example.test",
            "role": "authenticated",
            "aud": "authenticated",
            "iss": issuer,
            "iat": int(time.time()) - 5,
            "exp": int(time.time()) + expires_in,
        },
        _TEST_SECRET,
        algorithm="HS256",
    )


def _request(
    *,
    path: str = "/api/library",
    address: Optional[str] = _ADDRESS_X,
    authorization: Optional[str] = None,
    method: str = "GET",
) -> StarletteRequest:
    """A real ``Request`` over a hand-built scope, so address and header are both controllable.

    ``method`` exists for the callers outside this file that invoke a rate-limited handler
    directly (``tests/test_marketplace_concurrency._renewal_burst`` and
    ``tests/test_subscription_renewal_regression``, both of which drive ``POST`` routes). It is
    not read by any limit registered in this codebase - none of them is ``per_method`` and none
    restricts ``methods`` - but a request that says ``GET`` at a ``POST`` route is a lie the next
    reader would have to re-derive, and one builder with a parameter beats a second builder.
    """
    headers: List[Any] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    scope: Dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": (address, 44321) if address else None,
        "server": ("testserver", 80),
    }
    return StarletteRequest(scope)


def _authenticated(subject: str, *, address: str, path: str) -> StarletteRequest:
    return _request(path=path, address=address, authorization=f"Bearer {_token(subject)}")


def _registered(handler_key: str) -> List[Any]:
    return list(limiter._route_limits.get(handler_key, []))


def _limit_strings(handler_key: str) -> Set[str]:
    return {str(item.limit) for item in _registered(handler_key)}


def _key_funcs(handler_key: str) -> Set[Any]:
    return {item.key_func for item in _registered(handler_key)}


def _library_key(handler: str) -> str:
    return f"{library_router.__name__}.{handler}"


def _paper_key(handler: str) -> str:
    return f"{paper_router.__name__}.{handler}"


@pytest.fixture(autouse=True)
def _clean_counters():
    """Every test in this file starts and ends with an empty limiter."""
    _reset_rate_limit_counters()
    yield
    _reset_rate_limit_counters()


# ══════════════════════════════════════════════════════════════════════════
#  1. THE KEY HELPER, IN ITS THREE STATES
# ══════════════════════════════════════════════════════════════════════════


class TestTheKeyHelper:
    """``'u:' + sub`` when the token verifies, ``'ip:' + address`` in every other case.

    The third of these is the one that matters most operationally: a key function runs inside
    ``Limiter.__evaluate_limits``, so an exception escaping it is a 500, not a 429 — a malformed
    ``Authorization`` header would then turn a rate-limit control into an outage.
    """

    def test_a_token_that_verifies_keys_the_caller(self) -> None:
        key = caller_or_address(
            _request(authorization=f"Bearer {_token(_CALLER_A)}")
        )
        assert key == f"{CALLER_PREFIX}{_CALLER_A}", (
            f"an authenticated caller was keyed {key!r} rather than by its subject; Requirement "
            f"6.7's per-caller window cannot exist without this"
        )

    @pytest.mark.parametrize(
        "header,why",
        [
            ("Bearer not-a-token", "not a JWT at all"),
            ("Bearer ", "the scheme with no credential"),
            ("Bearer " + "a.b.c", "three segments of rubbish"),
            ("Basic dXNlcjpwYXNz", "a scheme this API does not accept"),
            ("Bearer" + _TEST_SECRET, "no space after the scheme"),
        ],
    )
    def test_a_token_that_does_not_verify_falls_back_to_the_address_without_raising(
        self, header: str, why: str
    ) -> None:
        key = caller_or_address(_request(authorization=header))
        assert key == f"{ADDRESS_PREFIX}{_ADDRESS_X}", (
            f"a request whose Authorization header is {why} was keyed {key!r}; it must fall back "
            f"to the source address"
        )

    @pytest.mark.parametrize(
        "kwargs,why",
        [
            ({"expires_in": -60}, "an expired token"),
            ({"issuer": "somebody-else"}, "a foreign issuer"),
        ],
    )
    def test_a_refused_token_falls_back_to_the_address_without_raising(
        self, kwargs: Dict[str, Any], why: str
    ) -> None:
        """``decode_token_local`` re-raises ``ExpiredSignatureError`` rather than returning None.

        That is exactly the escape the helper has to stop: the exception is raised from inside the
        limiter's evaluation loop, where nothing is going to catch it and turn it into a 429.
        """
        header = f"Bearer {_token(_CALLER_A, **kwargs)}"
        key = caller_or_address(_request(authorization=header))
        assert key == f"{ADDRESS_PREFIX}{_ADDRESS_X}", (
            f"{why} was keyed {key!r} rather than by the source address"
        )

    def test_no_token_keys_the_address(self) -> None:
        key = caller_or_address(_request())
        assert key == f"{ADDRESS_PREFIX}{_ADDRESS_X}"

    def test_the_unauthenticated_fallback_is_the_same_string_the_address_key_produces(
        self,
    ) -> None:
        """Not cosmetic. Requirement 6.7 caps an UNAUTHENTICATED caller at 60/60s per address.

        On a catalogue route the 120/60s limit is keyed by :func:`caller_or_address` and the
        60/60s limit by :func:`source_address`. For an anonymous request both must name the same
        bucket, or the anonymous caller would be measured against 120 and 60 separately and the
        stricter figure would still be the one that decided — but by accident rather than by
        construction.
        """
        request = _request()
        assert caller_or_address(request) == source_address(request)

    def test_an_address_that_cannot_be_resolved_still_yields_a_key(self) -> None:
        """A limit whose key is empty is SKIPPED by ``slowapi``, so ``None`` would disarm it."""
        key = caller_or_address(_request(address=None))
        assert key.startswith(ADDRESS_PREFIX) and key != ADDRESS_PREFIX

    def test_the_decode_happens_once_per_request(self) -> None:
        """The memo is why a route may carry two limits without paying for two decodes."""
        request = _request(authorization=f"Bearer {_token(_CALLER_A)}")
        first = caller_or_address(request)
        assert getattr(request.state, "_rate_limit_caller_key") == first
        assert caller_or_address(request) == first

    def test_no_database_client_is_needed_to_key_a_caller(self) -> None:
        """The helper reads claims, never rows.

        Asserted by making every service-role client accessor the marketplace has explode: if the
        key function touched one, this would raise instead of returning a key.
        """

        def _explode(*_args: Any, **_kwargs: Any):
            raise AssertionError("a rate-limit key function performed a database read")

        original = library_router._build_service_client
        library_router._build_service_client = _explode  # type: ignore[assignment]
        try:
            key = caller_or_address(
                _request(authorization=f"Bearer {_token(_CALLER_B)}")
            )
        finally:
            library_router._build_service_client = original  # type: ignore[assignment]
        assert key == f"{CALLER_PREFIX}{_CALLER_B}"


# ══════════════════════════════════════════════════════════════════════════
#  2. TWO STACKED LIMITS WITH DIFFERENT KEY FUNCTIONS ACTUALLY WORK
# ══════════════════════════════════════════════════════════════════════════

#: The path the stacked probe is evaluated under. ``Limiter`` was built with the default
#: ``key_style="url"``, so the limit's *scope* is the request path — a path of its own therefore
#: gives this probe a bucket no real route shares.
_STACKED_PATH = "/probe/task-33-2/stacked"

#: The same, for the probe that carries the caller limit alone.
_CALLER_ONLY_PATH = "/probe/task-33-2/caller-only"


@limiter.limit("3/60second", key_func=caller_or_address)
@limiter.limit("2/60second", key_func=source_address)
async def stacked_probe(request: Request) -> Dict[str, bool]:
    """A route shaped exactly like a catalogue route: a caller window and an address window.

    The figures are 3 and 2 rather than 120 and 60 so the windows can be exhausted in five
    requests instead of a hundred and eighty. What is under test is the mechanism, and the
    mechanism does not know what the numbers are.
    """
    return {"ran": True}


@limiter.limit("2/60second", key_func=caller_or_address)
async def caller_only_probe(request: Request) -> Dict[str, bool]:
    """The caller window on its own, so a caller budget can be observed unconfounded."""
    return {"ran": True}


def _check(request: StarletteRequest, endpoint: Any) -> bool:
    """Ask the limiter what it would do with ``request`` on ``endpoint``. True when admitted.

    ``_check_request_limit`` is the method ``slowapi``'s own wrapper calls, with the same
    ``in_middleware=False``. Calling it directly is what lets the source address vary.
    """
    try:
        limiter._check_request_limit(request, endpoint, False)
    except RateLimitExceeded:
        return False
    return True


class TestStackedLimitsWithDifferentKeyFunctionsAreBothEnforced:
    """The assumption Requirement 6.7 rests on, proved against the installed ``slowapi``.

    Three things could have made the second decorator a no-op, and none of them do:

    * both decorators file under ``f"{func.__module__}.{func.__name__}"``, and ``functools.wraps``
      keeps both attributes stable through the inner wrapper — so the second call *appends* to
      one registry entry rather than creating a second entry that nothing reads;
    * ``__evaluate_limits`` calls ``lim.key_func`` per limit, not the limiter's default;
    * the inner wrapper's ``request.state._rate_limiting_complete`` guard suppresses a *second
      evaluation pass*, not the second limit — both limits are already in the one entry the
      outer pass reads.
    """

    def test_both_limits_are_filed_under_the_one_registry_entry(self) -> None:
        key = f"{__name__}.stacked_probe"
        assert len(_registered(key)) == 2, (
            f"the two stacked decorators produced {len(_registered(key))} registered limits; if "
            f"this is 1, slowapi discarded one of them and Requirement 6.7's split is a fiction"
        )
        assert _limit_strings(key) == {"3 per 60 second", "2 per 60 second"}
        assert _key_funcs(key) == {caller_or_address, source_address}, (
            "the two limits do not carry the two different key functions they were declared with"
        )

    def test_the_address_window_binds_across_two_callers_from_one_address(self) -> None:
        """Two callers, one address, an address window of 2: the third request is refused.

        This is the half that makes an unauthenticated flood from one host bounded no matter how
        many identities it presents.
        """
        first = _check(_authenticated(_CALLER_A, address=_ADDRESS_X, path=_STACKED_PATH), stacked_probe)
        second = _check(_authenticated(_CALLER_B, address=_ADDRESS_X, path=_STACKED_PATH), stacked_probe)
        third = _check(_authenticated(_CALLER_B, address=_ADDRESS_X, path=_STACKED_PATH), stacked_probe)

        assert [first, second] == [True, True], (
            "the address window refused a request before its allowance of 2 was spent"
        )
        assert third is False, (
            "a third request from the same address was admitted; the 60/60s-per-address half of "
            "Requirement 6.7 is declared but not enforced"
        )

    def test_the_caller_window_binds_across_two_addresses_from_one_caller(self) -> None:
        """One caller, three addresses, a caller window of 3: the fourth request is refused.

        Each address is used at most twice, so the address window (2) never fires — the refusal
        can only come from the caller window, which is the point. Rotating source addresses does
        not buy a fresh budget.
        """
        admitted = [
            _check(_authenticated(_CALLER_A, address=_ADDRESS_X, path=_STACKED_PATH), stacked_probe),
            _check(_authenticated(_CALLER_A, address=_ADDRESS_X, path=_STACKED_PATH), stacked_probe),
            _check(_authenticated(_CALLER_A, address=_ADDRESS_Y, path=_STACKED_PATH), stacked_probe),
        ]
        fourth = _check(
            _authenticated(_CALLER_A, address=_ADDRESS_Z, path=_STACKED_PATH), stacked_probe
        )

        assert admitted == [True, True, True], (
            f"the caller window refused a request before its allowance of 3 was spent: {admitted}"
        )
        assert fourth is False, (
            "a fourth request from one caller was admitted because it arrived from a fresh "
            "address; the per-caller half of Requirement 6.7 is not being applied"
        )

    def test_two_callers_from_one_address_get_separate_caller_budgets(self) -> None:
        """The caller window alone, so the address window cannot be what refuses.

        Caller A spends its allowance of 2 from address X and is refused a third time. Caller B,
        from the SAME address, is admitted — which is the whole reason the helper exists: the
        global ``key_func=get_remote_address`` would have refused B as well.
        """
        a_first = _check(
            _authenticated(_CALLER_A, address=_ADDRESS_X, path=_CALLER_ONLY_PATH),
            caller_only_probe,
        )
        a_second = _check(
            _authenticated(_CALLER_A, address=_ADDRESS_X, path=_CALLER_ONLY_PATH),
            caller_only_probe,
        )
        a_third = _check(
            _authenticated(_CALLER_A, address=_ADDRESS_X, path=_CALLER_ONLY_PATH),
            caller_only_probe,
        )
        b_first = _check(
            _authenticated(_CALLER_B, address=_ADDRESS_X, path=_CALLER_ONLY_PATH),
            caller_only_probe,
        )

        assert [a_first, a_second, a_third] == [True, True, False], (
            f"caller A's own window did not bind at 2: {[a_first, a_second, a_third]}"
        )
        assert b_first is True, (
            "caller B was refused on its first request because caller A had spent the address's "
            "budget; the two callers do not have separate budgets"
        )

    def test_an_anonymous_caller_is_bounded_by_the_address_window(self) -> None:
        """No token, so both windows key by address and the stricter (2) decides."""
        codes = [
            _check(_request(path=_STACKED_PATH, address=_ADDRESS_Y), stacked_probe)
            for _ in range(3)
        ]
        assert codes == [True, True, False], (
            f"an anonymous caller was not capped at the 60/60s-equivalent address window: {codes}"
        )


# ══════════════════════════════════════════════════════════════════════════
#  3. WHAT A REFUSED REQUEST ANSWERS, AND WHAT IT DOES NOT DO
# ══════════════════════════════════════════════════════════════════════════

#: Every call the probe handler below completed. A rate-limited request must not add to it.
_HANDLER_EFFECTS: List[str] = []


#: An application wired the way ``main.py`` wires the real one, and nothing else. Built at import
#: rather than per test, and this is not a style choice: ``Limiter.limit`` APPENDS to
#: ``_route_limits[f"{module}.{name}"]``, so a factory that decorated the handler on every call
#: would leave the route carrying one limit in the first test, two in the second and n in the nth
#: — each hit once per request, so the allowance would appear to shrink as the file ran.
#:
#: The real application is not used here because the body assertions want an allowance of 2 rather
#: than 60, and adding a route to ``backend_app.main.app`` would leave it there for the session.
_REFUSAL_APP = FastAPI()
_REFUSAL_APP.state.limiter = limiter
_REFUSAL_APP.add_exception_handler(RateLimitExceeded, marketplace_rate_limit_handler)


@_REFUSAL_APP.get("/probe/refusal")
@limiter.limit("2/60second", key_func=caller_or_address)
async def refusal_probe(request: Request) -> Dict[str, int]:
    # The "compute" and the "persist" a refused request must not reach (Requirement 6.11).
    _HANDLER_EFFECTS.append("ran")
    return {"effects": len(_HANDLER_EFFECTS)}


def _no_digits_outside_the_request_id(body: Dict[str, Any], raw: str) -> str:
    """``raw`` with the request identifier's own digits removed, so a figure cannot hide in it."""
    request_id = str(body.get("request_id") or "")
    return raw.replace(request_id, "") if request_id else raw


class TestARefusedRequestAnswersTheCatalogueCodeAndComputesNothing:
    """Requirement 6.11 and Requirement 22.4's error half, over the real handler.

    ``slowapi``'s own ``_rate_limit_exceeded_handler`` answers
    ``{"error": "Rate limit exceeded: 2 per 1 minute"}`` — a sentence with no machine-readable
    code, which prints the limit and the window back at the caller. The replacement is asserted
    on both counts: the code is there, and the figures are not.
    """

    def test_the_refusal_carries_the_catalogue_code_and_the_one_envelope(self) -> None:
        _HANDLER_EFFECTS.clear()
        with TestClient(_REFUSAL_APP) as client:
            allowed = [client.get("/probe/refusal").status_code for _ in range(2)]
            refused = client.get("/probe/refusal")

        assert allowed == [200, 200], f"the allowance of 2 was not honoured: {allowed}"
        assert refused.status_code == 429

        body = refused.json()
        assert set(body) == set(ERROR_ENVELOPE_KEYS), (
            f"the 429 body's keys are {sorted(body)}; the structured envelope carries "
            f"{sorted(ERROR_ENVELOPE_KEYS)} and nothing else"
        )
        assert body["error"]["code"] == MARKETPLACE_RATE_LIMITED, (
            f"the 429 answered {body['error']['code']!r} rather than {MARKETPLACE_RATE_LIMITED!r}"
        )

    def test_the_refusal_body_carries_no_figure(self) -> None:
        _HANDLER_EFFECTS.clear()
        with TestClient(_REFUSAL_APP) as client:
            for _ in range(2):
                client.get("/probe/refusal")
            refused = client.get("/probe/refusal")

        body = refused.json()
        assert body["error"]["details"] == {}, (
            f"the 429 body carries details {body['error']['details']!r}; a refusal has no "
            f"caller values to report"
        )
        scrubbed = _no_digits_outside_the_request_id(body, refused.text)
        offending = sorted({character for character in scrubbed if character.isdigit()})
        assert offending == [], (
            f"the 429 body contains the digits {offending} outside its request identifier, so it "
            f"is disclosing a limit, a window or a remaining allowance: {refused.text!r}"
        )

    def test_no_rate_limit_header_discloses_what_the_body_withholds(self) -> None:
        _HANDLER_EFFECTS.clear()
        with TestClient(_REFUSAL_APP) as client:
            for _ in range(2):
                client.get("/probe/refusal")
            refused = client.get("/probe/refusal")

        leaked = sorted(
            name
            for name in refused.headers
            if name.lower().startswith("x-ratelimit") or name.lower() == "retry-after"
        )
        assert leaked == [], (
            f"the refusal carries {leaked}, which prints back the figures the body deliberately "
            f"omits"
        )

    def test_a_refused_request_runs_no_handler_body(self) -> None:
        """Requirement 6.11's "return no Listing data and leave the Listing unchanged".

        The decorator's wrapper checks the limit BEFORE it awaits the endpoint, so the refusal is
        raised with the handler body untouched. Asserted by counting: three requests, an allowance
        of two, two recorded effects.
        """
        _HANDLER_EFFECTS.clear()
        with TestClient(_REFUSAL_APP) as client:
            statuses = [client.get("/probe/refusal").status_code for _ in range(3)]

        assert statuses == [200, 200, 429], statuses
        assert len(_HANDLER_EFFECTS) == 2, (
            f"the handler body ran {len(_HANDLER_EFFECTS)} times for two admitted requests; a "
            f"rate-limited request computed or persisted something (Requirement 6.11)"
        )


class TestTheRealApplicationAnswersTheSameWay:
    """The wiring, and one end-to-end refusal through ``main.py``'s whole stack."""

    def test_the_application_registers_the_structured_rate_limit_handler(self) -> None:
        from backend_app.main import app

        assert app.exception_handlers.get(RateLimitExceeded) is marketplace_rate_limit_handler, (
            "backend_app.main still answers a 429 with slowapi's default handler, so a refusal "
            "carries no MARKETPLACE_RATE_LIMITED code"
        )

    def test_the_catalogue_refuses_the_sixty_first_request_from_one_address(self) -> None:
        """``GET /api/library/categories`` — 60/60s per address, armed and not merely declared.

        The categories route memoises its answer in a module global, which is pre-seeded here so
        that sixty admitted requests cost sixty dictionary lookups rather than sixty reads. What
        is asserted is the sixty-first answer, and that the answer is the catalogue's code.
        """
        from backend_app.main import app

        original = library_router._CACHED_CATEGORIES
        library_router._CACHED_CATEGORIES = {"categories": []}
        try:
            with TestClient(app) as client:
                codes = [
                    client.get("/api/library/categories").status_code for _ in range(60)
                ]
                refused = client.get("/api/library/categories")
        finally:
            library_router._CACHED_CATEGORIES = original

        assert 429 not in codes, (
            f"the catalogue refused request {codes.index(429) + 1} of 60, before its 60/60s "
            f"address allowance was spent"
        )
        assert refused.status_code == 429, (
            f"the sixty-first catalogue read in a minute was answered "
            f"{refused.status_code}; Requirement 6.7's address window is not enforced"
        )
        assert refused.json()["error"]["code"] == MARKETPLACE_RATE_LIMITED


# ══════════════════════════════════════════════════════════════════════════
#  4. EVERY LIMIT IN THE DESIGN'S PER-ENDPOINT TABLE
# ══════════════════════════════════════════════════════════════════════════

#: ``design.md`` -> "Per-endpoint review", the catalogue rows: 120/60s per caller stacked with
#: 60/60s per address. ``get_creator_profile`` is not a row of its own but is a catalogue read by
#: every other measure, so Requirement 6.7's "every catalogue, search and detail endpoint"
#: reaches it.
CATALOGUE_HANDLERS = (
    "browse_library",
    "get_featured_strategies",
    "get_trending_strategies",
    "get_categories",
    "get_library_detail",
    "get_creator_profile",
)

#: The authenticated rows of the same table: handler -> the window the table names for it. Each
#: carries that window keyed by :func:`caller_or_address`.
AUTHENTICATED_LIMITS = {
    "my_library": "120 per 60 second",
    "my_strategies": "120 per 60 second",
    "create_submission_route": "10 per 60 second",
    "get_own_submission": "120 per 60 second",
    "submission_price_range": "30 per 60 second",
    "submission_set_price": "30 per 60 second",
    "update_library_settings": "20 per 60 second",
    "admin_list_submissions": "120 per 60 second",
    "admin_submission_detail": "120 per 60 second",
    "admin_approve_submission": "60 per 60 second",
    "admin_reject_submission": "60 per 60 second",
    "admin_publish_submission": "60 per 60 second",
    "admin_suspend_submission": "60 per 60 second",
    "admin_unpublish_submission": "60 per 60 second",
    "admin_reinstate_subscription": "60 per 60 second",
    "create_marketplace_checkout": "5 per 60 second",
    "renew_subscription": "5 per 60 second",
    "cancel_subscription": "10 per 60 second",
    "clone_strategy": "20 per 60 second",
    "deploy_marketplace_strategy": "10 per 60 second",
    "creator_analytics": "60 per 60 second",
    "subscriber_analytics": "60 per 60 second",
}

#: The paper rows of the table. Already satisfied before this task, at exactly these figures, and
#: keyed by the limiter's global ``get_remote_address``. They are NOT given a caller-keyed limit:
#: ``tests/test_paper_session_api.py::test_no_retained_endpoint_collected_a_second_limit`` asserts
#: one limit per paper handler and
#: ``tests/test_task_28_1_session_routes.py::test_each_session_route_carries_its_rate_limit``
#: asserts the decorator's source verbatim, so either change would fail a suite this task must
#: leave green. Recorded here so the omission is a documented decision rather than a gap.
PAPER_LIMITS = {
    "get_paper_account": "120 per 1 minute",
    "get_paper_positions": "120 per 1 minute",
    "get_paper_orders": "120 per 1 minute",
    "get_paper_trades": "120 per 1 minute",
    "get_paper_summary": "120 per 1 minute",
    "reset_paper_account": "30 per 1 minute",
    "place_paper_order": "60 per 1 minute",
    "cancel_paper_order": "60 per 1 minute",
    "start_paper_session": "10 per 1 minute",
    "list_paper_sessions": "120 per 1 minute",
    "get_paper_session": "120 per 1 minute",
    "pause_paper_session": "60 per 1 minute",
    "resume_paper_session": "60 per 1 minute",
    "stop_paper_session": "60 per 1 minute",
    "reset_paper_session": "60 per 1 minute",
    "get_paper_session_orders": "120 per 1 minute",
    "get_paper_session_fills": "120 per 1 minute",
    "get_paper_session_positions": "120 per 1 minute",
    "get_paper_session_trades": "120 per 1 minute",
    "get_paper_session_equity": "120 per 1 minute",
    "get_paper_session_metrics": "120 per 1 minute",
    "get_paper_session_events": "120 per 1 minute",
}


class TestTheDesignTableIsApplied:
    """Read out of ``Limiter._route_limits``, which is what decides a request.

    The source of a decorator says what somebody wrote. The registry says what will be enforced,
    and the two differ whenever a decorator lands on a wrapper whose ``__name__`` moved, or when
    two handlers share a name and their limits merge.
    """

    @pytest.mark.parametrize("handler", CATALOGUE_HANDLERS)
    def test_a_catalogue_route_carries_both_windows_of_requirement_6_7(
        self, handler: str
    ) -> None:
        key = _library_key(handler)
        limits = {(str(item.limit), item.key_func) for item in _registered(key)}
        assert ("120 per 60 second", caller_or_address) in limits, (
            f"{handler} does not carry Requirement 6.7's 120/60s per authenticated caller; its "
            f"limits are {sorted((text, func.__name__) for text, func in limits)}"
        )
        assert ("60 per 60 second", source_address) in limits, (
            f"{handler} does not carry Requirement 6.7's 60/60s per source address; its limits "
            f"are {sorted((text, func.__name__) for text, func in limits)}"
        )

    @pytest.mark.parametrize("handler", sorted(AUTHENTICATED_LIMITS))
    def test_an_authenticated_route_carries_its_window_keyed_by_the_caller(
        self, handler: str
    ) -> None:
        key = _library_key(handler)
        limits = {(str(item.limit), item.key_func) for item in _registered(key)}
        expected = (AUTHENTICATED_LIMITS[handler], caller_or_address)
        assert expected in limits, (
            f"{handler} is not limited at {AUTHENTICATED_LIMITS[handler]!r} per authenticated "
            f"caller; its limits are "
            f"{sorted((text, func.__name__) for text, func in limits)} (Requirement 22.4)"
        )

    @pytest.mark.parametrize("handler", sorted(PAPER_LIMITS))
    def test_a_paper_route_keeps_the_single_address_keyed_limit_task_28_pinned(
        self, handler: str
    ) -> None:
        key = _paper_key(handler)
        assert _limit_strings(key) == {PAPER_LIMITS[handler]}, (
            f"{handler} is limited at {sorted(_limit_strings(key))} rather than at "
            f"{PAPER_LIMITS[handler]!r}; tasks 28.1 and 28.3 pin this figure and the number of "
            f"limits on it"
        )

    def test_no_endpoint_in_the_table_lost_a_limit(self) -> None:
        """Every named handler has at least one registered limit. An unlimited route is the gap
        Requirement 22.4 exists to close, and it is invisible in a per-handler assertion that
        parametrises over the table itself if the table's key is simply absent from the registry.
        """
        missing = sorted(
            handler
            for handler in tuple(CATALOGUE_HANDLERS) + tuple(AUTHENTICATED_LIMITS)
            if not _registered(_library_key(handler))
        )
        assert missing == [], f"these library handlers are not rate limited at all: {missing}"

    def test_the_write_paths_are_stricter_than_the_read_paths(self) -> None:
        """Requirement 22.4's ordering, not merely its presence.

        "a stricter limit on publication, price evaluation, checkout creation, renewal, session
        start and order placement than on read endpoints" — asserted as an inequality over the
        registry, so a future edit that levels them up fails here rather than silently.
        """
        reads = 120
        stricter = {
            "create_submission_route": "publication",
            "submission_price_range": "price evaluation",
            "create_marketplace_checkout": "checkout creation",
            "renew_subscription": "renewal",
        }
        for handler, what in stricter.items():
            amounts = {
                item.limit.amount for item in _registered(_library_key(handler))
            }
            assert amounts and max(amounts) < reads, (
                f"{what} ({handler}) is limited at {sorted(amounts)}, which is not stricter than "
                f"the {reads}/60s read endpoints (Requirement 22.4)"
            )
        session_start = {
            item.limit.amount for item in _registered(_paper_key("start_paper_session"))
        }
        order_placement = {
            item.limit.amount for item in _registered(_paper_key("place_paper_order"))
        }
        assert session_start and max(session_start) < reads
        assert order_placement and max(order_placement) < reads
