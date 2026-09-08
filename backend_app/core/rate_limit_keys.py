# -*- coding: utf-8 -*-
"""Rate-limit key functions for the Marketplace_API and the Paper_Trading_API.

WHY THIS MODULE EXISTS
    ``backend_app/core/rate_limit.py`` builds the one process-wide ``slowapi`` ``Limiter``
    with ``key_func=get_remote_address``. That default cannot express Requirement 6.7's
    *per authenticated caller* half: every request from one address shares one bucket, so
    two subscribers behind one office NAT share a budget while one subscriber rotating
    addresses gets a fresh one. The global default is deliberately **not** changed here -
    every other router in this codebase relies on it - and each route that wants the
    caller-scoped budget passes :func:`caller_or_address` explicitly::

        @limiter.limit("120/60second", key_func=caller_or_address)

    The catalogue, search and detail routes carry that limit **stacked** with
    ``@limiter.limit("60/60second", key_func=source_address)``. ``slowapi`` files both
    limits under the same ``f"{module}.{function}"`` registry key (``functools.wraps``
    keeps ``__name__`` stable through the first wrapper) and evaluates each with its own
    ``key_func`` in one pass, so the two windows are independent buckets and the request
    must satisfy both. That is Requirement 6.7's "at most 120 per 60s per authenticated
    caller **and** at most 60 per 60s per source address".

WHY A KEY FUNCTION MAY NOT RAISE
    ``Limiter.__evaluate_limits`` calls the key function inside the request path. An
    exception escaping it does not become a 429 - it becomes a 500, so a malformed
    ``Authorization`` header would turn a rate-limit control into an outage. Every failure
    mode here therefore falls back to the source address, and :func:`caller_or_address`
    catches ``BaseException`` subclasses through a deliberate broad ``except`` (see the
    comment at the call site).

WHY THE DECODE IS LOCAL, AND THE ONE CAVEAT
    The token is decoded with ``backend_app.core.auth_middleware.decode_token_local`` -
    the same function ``get_current_user``, the WebSocket authenticator and
    ``library._user_id_from_credentials`` already use. It is a *verifying* decode: the
    signature is checked against a server-determined trust root, so a caller cannot mint a
    ``sub`` of their choosing to win a fresh caller budget. There is **no database read**
    on this path.

    The caveat, stated rather than hidden: ``decode_token_local`` resolves the Supabase
    ES256 signing key through ``PyJWKClient``, which fetches the JWKS document over the
    network **when its in-process key cache is cold** (``cache_keys=True``,
    ``lifespan=3600``). That is at most one fetch per hour per process, not per request,
    and on every authenticated route the cache has already been warmed by
    ``get_current_user``: FastAPI resolves dependencies *before* it calls the endpoint
    callable, and ``slowapi``'s check happens inside the wrapper around that callable. The
    alternative - an unverified ``jwt.decode(..., verify_signature=False)`` - would have
    been free of I/O and is rejected on purpose, because it makes the caller budget
    forgeable by anyone who can write a JSON object. The decode is also memoised on
    ``request.state`` (:data:`_MEMO_ATTRIBUTE`), so a route carrying two stacked limits
    decodes at most once.

WHAT A REFUSED REQUEST ANSWERS
    :func:`marketplace_rate_limit_handler` serialises ``slowapi``'s ``RateLimitExceeded``
    as the catalogue's ``MARKETPLACE_RATE_LIMITED`` (429) through the one structured error
    envelope, so the body carries a stable code and a safe sentence and **no figure** - not
    the limit, not the remaining allowance, not the window. It also injects none of
    ``slowapi``'s ``X-RateLimit-*`` headers, which would disclose the same figures the body
    withholds. The refusal is raised by the wrapper *before* the handler body runs, so a
    rate-limited request computes nothing and persists nothing (Requirement 6.11).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from slowapi.util import get_remote_address

logger = logging.getLogger("RateLimitKeys")

#: The prefix a caller-scoped bucket carries. Distinct from :data:`ADDRESS_PREFIX` so that a
#: user identifier can never collide with an address literal in the limiter's key space.
CALLER_PREFIX = "u:"

#: The prefix an address-scoped bucket carries.
ADDRESS_PREFIX = "ip:"

#: The scheme this module accepts a token under, lower-cased for comparison.
_BEARER = "bearer"

#: Where the per-request decode is memoised. A sentinel distinct from ``None`` is not needed:
#: the memo holds either a string key or the value :data:`_UNSET`.
_MEMO_ATTRIBUTE = "_rate_limit_caller_key"

_UNSET = object()


def _bearer_token(request: Any) -> Optional[str]:
    """The bearer token on ``request``, or ``None``.

    Read off the raw header rather than through ``Depends(bearer_scheme)``, because a key
    function is not a dependency and has no injection context. Returns ``None`` for a
    missing header, a non-bearer scheme and an empty credential alike - all three mean
    "this request carries no caller identity I can use".
    """
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    raw = headers.get("authorization") or headers.get("Authorization")
    if not raw:
        return None
    scheme, _, credential = str(raw).partition(" ")
    if scheme.strip().lower() != _BEARER:
        return None
    credential = credential.strip()
    return credential or None


def source_address(request: Any) -> str:
    """``'ip:' + <client address>``. The address-scoped half of Requirement 6.7.

    A thin, prefixed wrapper around ``slowapi.util.get_remote_address`` rather than that
    function itself, so the address bucket and the fallback bucket of
    :func:`caller_or_address` are provably the same key for the same request: an
    unauthenticated caller must be capped by the 60/60s address window, and that only holds
    if both limits agree on how an address is spelled.
    """
    try:
        address = get_remote_address(request)
    except Exception:  # noqa: BLE001 - a key function that raises turns a 429 into a 500
        logger.warning("could not resolve a client address for rate limiting", exc_info=True)
        address = None
    return f"{ADDRESS_PREFIX}{address or 'unknown'}"


def caller_or_address(request: Any) -> str:
    """``'u:' + sub`` for a caller whose token verifies, else ``'ip:' + <client address>``.

    No database read, and no exception escape - see the module docstring for why both of
    those are properties of the function rather than incidental.
    """
    memo = getattr(getattr(request, "state", None), _MEMO_ATTRIBUTE, _UNSET)
    if memo is not _UNSET:
        return str(memo)

    key = _resolve_caller_key(request)

    state = getattr(request, "state", None)
    if state is not None:
        try:
            setattr(state, _MEMO_ATTRIBUTE, key)
        except Exception:  # noqa: BLE001 - a memo that cannot be stored costs a decode, not a 500
            pass
    return key


def _resolve_caller_key(request: Any) -> str:
    """The uncached half of :func:`caller_or_address`."""
    token = _bearer_token(request)
    if not token:
        return source_address(request)

    try:
        from backend_app.core.auth_middleware import decode_token_local

        claims = decode_token_local(token)
        subject = claims.get("sub") if isinstance(claims, dict) else None
    except Exception:  # noqa: BLE001 - an unusable token is an unauthenticated caller, not a 500
        # Not logged at error level and never with the token: a client sending an expired
        # token is ordinary, and the token is a credential (Requirements 22.9, 26.4).
        logger.debug("rate-limit key fell back to the source address: token not usable")
        return source_address(request)

    if not subject:
        return source_address(request)
    return f"{CALLER_PREFIX}{subject}"


async def marketplace_rate_limit_handler(request: Any, exc: Exception) -> Any:
    """Answer ``slowapi``'s ``RateLimitExceeded`` with ``MARKETPLACE_RATE_LIMITED`` (429).

    Registered in ``main.py`` in place of ``slowapi._rate_limit_exceeded_handler``, whose
    body is ``{"error": "Rate limit exceeded: 120 per 1 minute"}`` - a sentence rather than
    a code, and one that prints the limit back at the caller. The replacement reuses the one
    structured envelope every other Marketplace_API and Paper_Trading_API error already
    uses, so a client branches on ``error.code`` here exactly as it does everywhere else.

    Registration is per application, so this serves every ``@limiter.limit`` route in the
    process, not only the marketplace ones. That is intentional: the codebase had no
    machine-readable 429 before, and one shape for all of them is better than two.
    """
    from fastapi.responses import JSONResponse

    from backend_app.backend.marketplace.errors import (
        MARKETPLACE_RATE_LIMITED,
        REQUEST_ID_HEADER,
        MarketplaceError,
        current_request_id,
        structured_error_body,
    )

    error = MarketplaceError(MARKETPLACE_RATE_LIMITED)
    request_id = current_request_id(request)
    return JSONResponse(
        status_code=error.http_status,
        content=structured_error_body(error, request_id),
        headers={REQUEST_ID_HEADER: request_id},
    )


__all__ = [
    "ADDRESS_PREFIX",
    "CALLER_PREFIX",
    "caller_or_address",
    "marketplace_rate_limit_handler",
    "source_address",
]
