"""
WebSocket Authentication Middleware - Phase 6 Authentication Hardening

ALGO22 AUTH FIX: Replaced broken ES256/JWKS decoding + supabase.auth.get_user()
network fallback with zero-latency local HS256 decoding using SUPABASE_JWT_SECRET.

Root cause: The previous hotfix attempted ES256 decoding but Supabase signs tokens
with HS256, causing every validation to fail and fall through to a blocking network
call to supabase.auth.get_user(). This triggered rate limits and timeouts, producing
intermittent 401s on WebSocket connections.

Fix: Local HS256 decoding (same pattern as dependencies.get_current_user).
No network calls on the authentication hot path.

Key Features:
- Token validation on WebSocket connection (zero-latency, local only)
- Connection rejection for invalid tokens
- Tenant isolation on WebSocket connections
- HS256 signature verification against SUPABASE_JWT_SECRET
- Audit logging for authentication events

Author: Principal Institutional Platform Security Engineer
"""

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import jwt
from fastapi import WebSocket, status

logger = logging.getLogger("WebSocketAuth")


# ---------------------------------------------------------------------------
# Local HS256 token decoder — shared helper used by both the class and the
# module-level function in ws_routes.py (via this module).
# ---------------------------------------------------------------------------

def _decode_hs256_token(token: str) -> Optional[dict]:
    """
    Decode and verify a Supabase JWT (ES256 or HS256) locally.
    
    This helper is used by the WebSocket authentication middleware and route handlers.
    It delegates to `decode_token_local` to perform zero-latency validation.
    
    Returns the decoded payload dict on success, or None on failure.
    """
    try:
        from backend_app.core.auth_middleware import decode_token_local
        return decode_token_local(token)
    except jwt.exceptions.ExpiredSignatureError:
        logger.warning("[WS/Auth] Token expired")
        return None
    except jwt.exceptions.InvalidTokenError as e:
        logger.warning(f"[WS/Auth] Invalid token: {e}")
        return None
    except Exception as e:
        logger.error(f"[WS/Auth] Unexpected token decode error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  SINGLE-USE WEBSOCKET TICKET — REDEMPTION
#
#  production-launch-hardening task 8.2. Requirements 1.21, 2.21, 3.9.
#
#  WHY A TICKET EXISTS AT ALL. The browser ``WebSocket`` constructor cannot set
#  request headers, so the socket credential has to travel in the URL, and a URL is
#  written to CloudFront and ALB access logs and to browser history. A session JWT
#  there is replayable for as long as it is valid. A ticket is not: it is opaque, it
#  lives ≤ 30 s, and it is consumed by the first connection that presents it, so a
#  logged ticket buys an attacker nothing.
#
#  "CONSUMED BY THE FIRST CONNECTION" IS THE WHOLE PROPERTY, so it is enforced with
#  an atomic read-and-delete (``redis_manager.getdel``) rather than a read followed by
#  a delete. Two sockets racing on the same ticket must not both be admitted, and a
#  separate GET and DEL is exactly the window in which they would be.
#
#  FAIL CLOSED, WITHOUT EXCEPTION. No ticket store, an unreachable store, an unknown
#  ticket, an expired ticket, an already-consumed ticket, a stored value with no
#  subject in it — every one of these is ``None``. There is no branch here that admits
#  a connection because it could not find evidence against it. That is the failure
#  mode this task was raised to remove: the issuance side already returned tickets
#  that were never stored, which is an admission decision made on absence of evidence.
#
#  THE TICKET VALUE IS NEVER LOGGED. It is a bearer credential for its whole life, so
#  it is referred to by length only. The resolved user id is logged, as every other
#  authentication path here already does.
# ═══════════════════════════════════════════════════════════════════════════

#: Redis key prefix for an outstanding ticket. Canonical: ``routers/auth.py`` imports
#: this rather than re-spelling it, because two spellings of a key prefix is one
#: rename away from issuing into one namespace and redeeming from another — which
#: would present exactly as "every ticket is unknown", i.e. as a total outage of the
#: feature, with nothing in either module looking wrong.
WS_TICKET_REDIS_PREFIX = "ws_ticket:"

#: Ticket lifetime. Requirement 2.21 caps this at 30 s; it is the TTL on the stored
#: key, so expiry is enforced by Redis rather than by anything here remembering to
#: check a timestamp.
WS_TICKET_TTL_SECONDS = 30

#: Recorded on the resolved identity so a caller can tell *how* a connection
#: authenticated. Not a JWT claim — deliberately named so it cannot be mistaken for
#: one if this dict is ever logged or forwarded.
WS_TICKET_AUTH_METHOD = "ws_ticket"

#: An upper bound on what is worth looking up. ``secrets.token_urlsafe(32)`` is 43
#: characters; anything remotely near this bound is not a ticket this server minted,
#: and there is no reason to build a Redis key out of an unbounded query string.
_WS_TICKET_MAX_LENGTH = 256


def ws_ticket_redis_key(ticket: str) -> str:
    """The Redis key an outstanding ``ticket`` is stored under."""
    return f"{WS_TICKET_REDIS_PREFIX}{ticket}"


async def verify_ws_ticket(ticket: str) -> Optional[dict]:
    """Redeem a single-use WebSocket ticket, returning the identity it names.

    Returns a dict in the same shape ``_decode_hs256_token`` returns — keyed on
    ``sub`` — so the nine route handlers can treat a ticket and a JWT
    interchangeably and no call site has to know which credential it got. The dict
    carries only the subject: a ticket is a reference to a user, not a claims
    bundle, and inventing a ``role`` or an ``email`` that was never verified at
    redemption time would be worse than omitting them. ``auth_method`` says which
    credential it came from.

    Returns ``None`` on **every** failure, including every failure of the ticket
    store itself. A ticket that cannot be proven good is not good.

    Consumes the ticket: a second call with the same value returns ``None``,
    whether or not the first call's caller went on to accept the socket.
    """
    if not ticket or not isinstance(ticket, str):
        return None

    if len(ticket) > _WS_TICKET_MAX_LENGTH:
        logger.warning(
            "[WS/Auth] Ticket rejected: %d characters exceeds the %d-character bound.",
            len(ticket),
            _WS_TICKET_MAX_LENGTH,
        )
        return None

    try:
        from backend_app.core.cache import redis_manager

        stored = await redis_manager.getdel(ws_ticket_redis_key(ticket))
    except Exception as exc:  # noqa: BLE001 - an unreachable store denies
        logger.warning(
            "[WS/Auth] Ticket redemption could not reach the ticket store (%s); "
            "refused.",
            exc,
        )
        return None

    if stored is None:
        # Unknown, expired, or already redeemed. ONE message for all three on
        # purpose: distinguishing them would tell a holder of a guessed value
        # whether it ever existed.
        logger.info(
            "[WS/Auth] Ticket rejected: unknown, expired or already consumed "
            "(length=%d).",
            len(ticket),
        )
        return None

    user_id = stored.decode("utf-8", "replace") if isinstance(stored, bytes) else str(stored)
    user_id = user_id.strip()
    if not user_id:
        logger.warning(
            "[WS/Auth] Ticket rejected: the ticket store holds no subject against it."
        )
        return None

    logger.info("[WS/Auth] Ticket redeemed for user %s", user_id)
    return {"sub": user_id, "auth_method": WS_TICKET_AUTH_METHOD}


class WebSocketAuthMiddleware:
    """
    Comprehensive WebSocket authentication middleware.

    This middleware provides:
    - Zero-latency local HS256 token validation (no network calls)
    - JWT signature verification against SUPABASE_JWT_SECRET
    - Tenant isolation (user ID claim vs path parameter cross-check)
    - Audit logging
    """

    def __init__(self):
        """Initialize WebSocket authentication middleware."""
        self._connection_attempts: Dict[str, int] = {}
        self._failed_attempts: Dict[str, int] = {}

    async def authenticate_websocket(
        self,
        websocket: WebSocket,
        token: Optional[str] = None,
        user_id: Optional[str] = None,
        require_auth: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Authenticate WebSocket connection using local HS256 JWT decoding.

        Args:
            websocket: WebSocket connection
            token: JWT token from query parameter
            user_id: User ID from path parameter
            require_auth: Whether authentication is required

        Returns:
            User data dict if authentication successful, None otherwise.
        """
        # If authentication not required, allow connection
        if not require_auth:
            logger.debug("[WS/Auth] Authentication not required for this endpoint")
            return None

        async def _safe_close(code: int, reason: str):
            try:
                await websocket.close(code=code, reason=reason)
            except Exception:
                pass

        # Require token
        if not token:
            logger.warning("[WS/Auth] Missing token in WebSocket connection")
            await _safe_close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")
            return None

        # Validate token locally
        try:
            user_data = await self._validate_token(token, user_id)

            if not user_data:
                logger.warning(f"[WS/Auth] Invalid token for user {user_id}")
                await _safe_close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
                return None

            logger.info(f"[WS/Auth] WebSocket authenticated for user {user_data.get('id')}")
            self._track_connection_attempt(user_data.get("id"), success=True)
            return user_data

        except Exception as e:
            logger.error(f"[WS/Auth] Authentication error: {e}")
            await _safe_close(code=status.WS_1011_INTERNAL_ERROR, reason="Authentication error")
            return None

    async def _validate_token(
        self, token: str, claimed_user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Validate JWT token using local HS256 decoding.

        No network calls — pure local cryptographic verification.

        Args:
            token: JWT token to validate
            claimed_user_id: User ID claimed by the client (path param)

        Returns:
            User data dict if token is valid, None otherwise.
        """
        payload = _decode_hs256_token(token)
        if payload is None:
            return None

        user_id = payload.get("sub")
        email = payload.get("email", "")
        tenant_id = (
            payload.get("tenant_id")
            or payload.get("app_metadata", {}).get("tenant_id")
            or user_id
        )

        if not user_id:
            logger.warning("[WS/Auth] Invalid token: missing sub claim")
            return None

        # Tenant isolation: token sub must match the path-level user_id
        if claimed_user_id and user_id != claimed_user_id:
            logger.warning(
                f"[WS/Auth] User ID mismatch: claimed={claimed_user_id}, token={user_id}"
            )
            return None

        return {
            "id": user_id,
            "email": email,
            "tenant_id": tenant_id,
            "access_token": token,
            "role": payload.get("role", "authenticated"),
            "app_metadata": payload.get("app_metadata", {}),
        }

    def _track_connection_attempt(self, user_id: str, success: bool):
        """Track connection attempt for rate limiting and monitoring."""
        if user_id not in self._connection_attempts:
            self._connection_attempts[user_id] = 0
            self._failed_attempts[user_id] = 0

        self._connection_attempts[user_id] += 1

        if not success:
            self._failed_attempts[user_id] += 1

        if self._failed_attempts.get(user_id, 0) > 5:
            logger.warning(
                f"[WS/Auth] High failed attempts for user {user_id}: "
                f"{self._failed_attempts[user_id]}"
            )

    def get_connection_stats(self) -> Dict[str, Any]:
        """Get connection statistics."""
        return {
            "total_connections": sum(self._connection_attempts.values()),
            "failed_connections": sum(self._failed_attempts.values()),
            "unique_users": len(self._connection_attempts),
        }


# Global singleton
websocket_auth = WebSocketAuthMiddleware()


def get_websocket_auth() -> WebSocketAuthMiddleware:
    """Get global WebSocket authentication middleware instance."""
    return websocket_auth


async def authenticate_websocket_connection(
    websocket: WebSocket,
    token: Optional[str] = None,
    user_id: Optional[str] = None,
    require_auth: bool = True
) -> Optional[Dict[str, Any]]:
    """
    Convenience function to authenticate WebSocket connection.

    Args:
        websocket: WebSocket connection
        token: JWT token from query parameter
        user_id: User ID from path parameter
        require_auth: Whether authentication is required

    Returns:
        User data if authentication successful, None otherwise
    """
    auth = get_websocket_auth()
    return await auth.authenticate_websocket(websocket, token, user_id, require_auth)

# ═══════════════════════════════════════════════════════════════════════════
#  CHANNEL SUBSCRIPTION AUTHORISATION
#
#  strategy-builder task 6.7. Requirements 21.5, 21.6 (and 15.11, 23.1 for the
#  channel this first serves).
#
#  Authenticating the CONNECTION is not authorising a SUBSCRIPTION. Everything
#  above this line answers "is this a real user"; from here down the question is
#  "does the resource this channel names belong to that user".
#
#  THREE RULES, AND WHY EACH IS SHAPED THIS WAY
#  -------------------------------------------
#  1. **A refusal is reported, never swallowed.** Requirement 21.6 says the
#     subscription is refused AND the refusal is reported. So this returns a
#     decision object carrying a code and a human reason for the client to see,
#     rather than a bare bool that a caller could quietly drop.
#
#  2. **A failed lookup denies.** If the owner of the referenced resource cannot
#     be established — no database client, an unapplied migration, a transport
#     error — the answer is "no". Allowing on error would turn any outage into an
#     authorisation bypass. The reason names what went wrong (and, for an
#     unapplied migration, which file to apply) so the failure is diagnosable
#     without being permissive.
#
#  3. **Only the channels named here are judged here.** Every other channel keeps
#     exactly the authorisation it already had — this function returns "allowed"
#     for them and says so explicitly. That is a deliberate no-op rather than an
#     endorsement: task 6.7 adds one channel, and silently taking over the
#     decision for `market.*`, `pnl`, `dashboard` or `bot_status` would change
#     controls this task never analysed. Broadening is not the risk here;
#     changing them at all is.
# ═══════════════════════════════════════════════════════════════════════════

#: The subscription was refused because the named resource is not this user's, or
#: does not exist. ONE code covers both on purpose: under
#: ``training_jobs``'s ``tj_owner_select`` RLS policy another user's job and a
#: nonexistent job are the same empty result, and distinguishing them to the
#: client would turn this channel into an existence oracle for other tenants'
#: job identifiers (Requirement 21.4 permits either answer and forbids leaking
#: the resource).
CHANNEL_REFUSED_FORBIDDEN = "CHANNEL_FORBIDDEN"

#: The channel name is not one this server routes, or is malformed.
CHANNEL_REFUSED_UNKNOWN = "CHANNEL_UNKNOWN"

#: No authenticated identity was presented with the subscription.
CHANNEL_REFUSED_UNAUTHENTICATED = "CHANNEL_UNAUTHENTICATED"

#: The owner could not be established, so the answer is no. Distinct from
#: ``CHANNEL_FORBIDDEN`` because it is an operator's problem, not the user's, and
#: the reason string names the fix.
CHANNEL_REFUSED_OWNER_UNRESOLVED = "CHANNEL_OWNER_UNRESOLVED"


@dataclass(frozen=True)
class ChannelAuthorization:
    """The decision for one (channel, user) pair.

    ``allowed`` is the only thing a transport may branch on. ``code`` and
    ``reason`` exist so the refusal can be REPORTED (Requirement 21.6) instead of
    being a silent no-op.
    """

    allowed: bool
    channel: str
    code: str = ""
    reason: str = ""
    owner_id: Optional[str] = None

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.allowed

    def refusal_frame(self) -> Dict[str, Any]:
        """The frame a transport sends back on a refusal.

        Carries the channel, a stable machine code and a sentence for a human. It
        carries no resource data at all: on the forbidden path nothing about the
        job was read, and on every path the owner id stays server-side.
        """
        return {
            "type": "subscription_refused",
            "channel": self.channel,
            "code": self.code,
            "reason": self.reason,
        }


def _allowed(channel: str, owner_id: Optional[str] = None) -> ChannelAuthorization:
    return ChannelAuthorization(True, channel, owner_id=owner_id)


def _refused(channel: str, code: str, reason: str) -> ChannelAuthorization:
    return ChannelAuthorization(False, channel, code=code, reason=reason)


def _user_identity(user: Any) -> Optional[str]:
    """The requesting user's id, from either a dict or an object with ``.id``."""
    if user is None:
        return None
    if isinstance(user, dict):
        candidate = user.get("id") or user.get("sub") or user.get("user_id")
    else:  # pragma: no cover - defensive; the platform passes dicts
        candidate = getattr(user, "id", None)
    text = str(candidate) if candidate else ""
    return text or None


async def _execute_query(query: Any) -> Any:
    """Await ``query`` when the client is async, return it when it is not.

    The same two-shaped-clients problem ``strategy_service._execute`` documents:
    the pooled PostgREST client this platform builds per request is async, and
    several test doubles and the sync ``supabase-py`` client are not.
    """
    result = query.execute()
    if inspect.isawaitable(result):
        return await result
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  WHICH RELATION RECORDS THE OWNER
#
#  strategy-builder task 8.5. Requirements 21.5, 21.6 for the four channels it adds:
#  ``builder.validation.{strategy_id}``, ``strategy.{strategy_id}``,
#  ``deployment.{deployment_id}`` and ``execution.{deployment_id}``.
#
#  ``ws_channels`` knows the channel names and which resource each one names. It
#  deliberately does not know which table records that resource's owner — that map is
#  here, because this is the module that has to hold an owner to compare against, and
#  because a family descriptor carrying a table name would be a second place for the
#  relation name to drift from the service that reads it.
#
#  ONE RESOLVER, SIX FAMILIES, AND WHY THAT IS THE SAFE DIRECTION
#  --------------------------------------------------------------
#  Task 6.7 wrote the ownership lookup for one channel. Four copies of it would be
#  four places for "a failed lookup denies" to be got wrong, and the copy that got it
#  wrong would be an authorisation bypass rather than a cosmetic divergence. So the
#  lookup is written once, over a per-family descriptor, and every refusal branch is
#  shared: no client, unloadable service, missing relation, transport failure, no
#  visible row, a row with no owner, and a row owned by somebody else all deny, for
#  every family, by construction.
#
#  ``execution.{deployment_id}``, ``deployment.{deployment_id}`` and
#  ``signal.{deployment_id}`` resolve through the SAME row, which is correct rather
#  than lazy: a deployment's fills, its lifecycle and the signals it produced all
#  belong to whoever owns the deployment, and giving any one of those streams a
#  second ownership rule would be inventing a way for them to disagree about who may
#  watch one running strategy.
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _OwnerRelation:
    """Where one channel family's owner is recorded.

    ``migration`` is named in the degradation warning and in the refusal reason so an
    operator is told which file to apply rather than left with "unavailable". It is
    ``None`` only for a relation this repository's migrations do not create — naming
    the wrong file would be worse than naming none.
    """

    table: str
    migration: Optional[str]
    #: The noun used in the sentence the client is shown ("training job", "strategy").
    noun: str
    owner_column: str = "user_id"


#: ``strategy_deployments`` — migration ``001_strategy_architecture.sql``, which is
#: where the relation and its ``user_id`` come from. 004e adds binding *columns* to
#: it; this lookup reads only ``id`` and ``user_id``, so 004e is deliberately not the
#: file named here.
_DEPLOYMENTS_RELATION = _OwnerRelation(
    table="strategy_deployments",
    migration="backend_app/migrations/001_strategy_architecture.sql",
    noun="deployment",
)

#: ``strategies`` — a base platform relation that predates this repository's
#: ``migrations/`` directory, so there is no file to name. Its absence is still a
#: refusal; only the sentence is shorter.
_STRATEGIES_RELATION = _OwnerRelation(
    table="strategies",
    migration=None,
    noun="strategy",
)

#: ``paper_sessions`` — migration ``009_paper_trading.sql`` section 3, which creates the relation
#: and its ``user_id``. marketplace-subscriptions-paper-trading task 26.3.
#:
#: This is the WHOLE of the Paper_Channel's authorisation change. It adds no resolver: the lookup
#: below already reads ``id`` and the owner column of whatever relation the family maps to, and
#: every one of its refusal branches — no client, unloadable service, unmapped family, transport
#: failure, no visible row, a row with no owner, a row owned by somebody else — already denies. In
#: particular ``_forbidden`` gives ONE sentence for "another user's paper session" and "no such
#: paper session", which is exactly what Requirements 19.4 and 21.4 require and is not a property
#: this entry had to arrange.
_PAPER_SESSIONS_RELATION = _OwnerRelation(
    table="paper_sessions",
    migration="backend_app/migrations/009_paper_trading.sql",
    noun="paper session",
)


def _owner_relations() -> Dict[str, _OwnerRelation]:
    """``channel namespace -> the relation that records the owner``.

    Built on call, not at import, because the training entry takes its table and
    migration names from ``strategy_service`` rather than re-spelling them, and this
    module sits on the authentication hot path: importing the training service at
    module scope would make every WebSocket connection pay for the compiler.
    """
    from backend_app.backend.ws_channels import (
        BUILDER_VALIDATION_FAMILY,
        DEPLOYMENT_FAMILY,
        EXECUTION_FAMILY,
        PAPER_FAMILY,
        SIGNAL_FAMILY,
        STRATEGY_FAMILY,
        TRAINING_FAMILY,
    )

    relations: Dict[str, _OwnerRelation] = {
        BUILDER_VALIDATION_FAMILY.namespace: _STRATEGIES_RELATION,
        STRATEGY_FAMILY.namespace: _STRATEGIES_RELATION,
        DEPLOYMENT_FAMILY.namespace: _DEPLOYMENTS_RELATION,
        EXECUTION_FAMILY.namespace: _DEPLOYMENTS_RELATION,
        # trading-lifecycle-integration task 14.1. `signal.{deployment_id}` is the
        # THIRD family keyed on a deployment id, and this line is the entire extent
        # of its authorisation: it maps onto the deployment lookup already written
        # above rather than adding a resolver of its own. A signal belongs to
        # whoever owns the deployment that produced it, and giving the signal stream
        # a second ownership rule — through `signals.user_id`, say — would be
        # inventing a way for the two to disagree about who may watch one running
        # strategy, exactly as task 8.5 declined to do for `execution`.
        SIGNAL_FAMILY.namespace: _DEPLOYMENTS_RELATION,
        # marketplace-subscriptions-paper-trading task 26.3. `paper.{session_id}` is the first
        # family keyed on a Paper_Session, so this is the first entry pointing at
        # `paper_sessions` — and it is still one map entry rather than a lookup. Requirement 19.5
        # asks for ownership to be RE-VERIFIED at subscription time rather than trusting the
        # identifier in the subscribe message, and that is what the resolver below does for every
        # family: it reads the relation, compares the owner to the authenticated identity, and
        # refuses on anything else. Requirement 21.4's byte-identical refusal is `_forbidden`'s,
        # unchanged.
        PAPER_FAMILY.namespace: _PAPER_SESSIONS_RELATION,
    }

    try:
        from backend_app.backend.strategy_service import (
            TRAINING_JOBS_TABLE,
            TRAINING_MIGRATION,
        )
    except Exception as exc:  # noqa: BLE001 - the family is then simply unmapped
        logger.error(
            "[WS/Auth] The training service could not be loaded (%s), so training "
            "subscriptions cannot be authorised and will be refused.",
            exc,
        )
        return relations

    relations[TRAINING_FAMILY.namespace] = _OwnerRelation(
        table=TRAINING_JOBS_TABLE,
        migration=TRAINING_MIGRATION,
        noun="training job",
    )
    return relations


def _is_missing_relation_error(exc: BaseException, table: str) -> bool:
    """True only when ``exc`` definitively says ``table`` does not exist.

    The generic PostgreSQL / PostgREST missing-relation codes are recognised by
    ``strategy_service.is_missing_training_table_error`` — the same reuse
    ``model_versioning.is_missing_model_table_error`` makes, and for the same reason:
    one vocabulary for the whole Strategy Builder. The table-named phrasing is added
    here so a message that names *this* relation is recognised too.

    Anything else is **not** classified as a missing relation, and therefore lands on
    the generic refusal branch rather than being reported as an unapplied migration.
    """
    text = str(exc).lower()
    if not text:
        return False
    try:
        from backend_app.backend.strategy_service import (
            is_missing_training_table_error,
        )

        if is_missing_training_table_error(exc):
            return True
    except Exception:  # noqa: BLE001 - fall through to the local phrasing check
        pass
    if table.lower() not in text:
        return False
    return any(
        phrase in text
        for phrase in (
            "does not exist",
            "not exist",
            "could not find the table",
            "unknown table",
        )
    )


def _unresolved(
    channel: str, relation: _OwnerRelation, detail: str = ""
) -> ChannelAuthorization:
    """The shared "the owner could not be established, so no" refusal."""
    reason = (
        f"Ownership of that {relation.noun} could not be verified, so the "
        f"subscription was refused."
    )
    if detail:
        reason = f"{reason} {detail}"
    return _refused(channel, CHANNEL_REFUSED_OWNER_UNRESOLVED, reason)


def _forbidden(channel: str, relation: _OwnerRelation) -> ChannelAuthorization:
    """The shared "not this user's resource, or no such resource" refusal.

    ONE sentence for both, for the reason ``CHANNEL_REFUSED_FORBIDDEN`` documents:
    under the owner-scoped RLS policies another user's row and a nonexistent row are
    the same empty result, and telling the client which would turn every one of these
    channels into an existence oracle for other tenants' identifiers.
    """
    return _refused(
        channel,
        CHANNEL_REFUSED_FORBIDDEN,
        f"That {relation.noun} is not available to this user, so the subscription "
        f"was refused.",
    )


async def _resolve_owned_channel_owner(
    reference: Any, user: Any, supabase: Any = None
) -> ChannelAuthorization:
    """Resolve the owner of the resource ``reference`` names and compare it to ``user``.

    ``reference`` is a ``ws_channels.OwnedChannelRef`` — a family plus a resource id.

    Returns an ALLOW only when a row was read and its owner column equals the
    requesting user's id. Every other outcome — no client, missing relation, transport
    error, no row, a row with no owner, a row owned by somebody else — is a refusal.

    The unapplied-migration path is the one worth naming: there is no local
    PostgreSQL and none of ``004``, ``004b``, ``004c``, ``004d`` or ``004e`` has been
    applied, so this lookup can legitimately hit a relation that does not exist. That
    is logged as a warning naming the file (never a 500, matching what every other
    Strategy Builder read does) and it DENIES. A missing ownership table is precisely
    the situation in which "allow" would be indefensible.
    """
    channel = reference.channel
    family = reference.family
    resource_id = reference.resource_id

    relation = _owner_relations().get(family.namespace)
    if relation is None:
        # A family with no owner relation cannot be authorised, so it is refused. This
        # is reachable only when the service holding the relation name failed to
        # import, which is already logged by ``_owner_relations``.
        return _refused(
            channel,
            CHANNEL_REFUSED_OWNER_UNRESOLVED,
            "Ownership for that channel could not be established, so the "
            "subscription was refused.",
        )

    requester = _user_identity(user)
    if not requester:
        return _refused(
            channel,
            CHANNEL_REFUSED_UNAUTHENTICATED,
            f"This subscription carries no authenticated user, so ownership of the "
            f"{relation.noun} could not be checked.",
        )

    client = supabase
    if client is None:
        try:
            from backend_app.core.dependencies import create_request_supabase_async

            token = user.get("access_token") if isinstance(user, dict) else None
            client = await create_request_supabase_async(token)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[WS/Auth] No database client for a subscription to %s (%s); refused.",
                channel,
                exc,
            )
            client = None

    if client is None:
        return _unresolved(
            channel,
            relation,
            "No database client is available.",
        )

    try:
        query = (
            client.table(relation.table)
            .select(f"id,{relation.owner_column}")
            .eq("id", resource_id)
            .limit(1)
        )
        result = await _execute_query(query)
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed blindly
        if _is_missing_relation_error(exc, relation.table):
            apply_hint = (
                f" Apply {relation.migration}." if relation.migration else ""
            )
            logger.warning(
                "[WS/Auth] Subscription to %s refused: %s does not exist.%s Detail: %s",
                channel,
                relation.table,
                apply_hint,
                exc,
            )
            return _unresolved(
                channel,
                relation,
                f"{relation.table} does not exist;"
                + (f" apply {relation.migration}." if relation.migration else ""),
            )
        logger.warning(
            "[WS/Auth] Subscription to %s refused: the ownership lookup failed (%s).",
            channel,
            exc,
        )
        return _unresolved(channel, relation)

    rows = (getattr(result, "data", None) or []) if result is not None else []
    if not rows:
        # Either no such row, or one belonging to another user that this client's RLS
        # policy filtered out. Same answer, same code.
        logger.info(
            "[WS/Auth] Subscription to %s refused for user %s: no such %s is visible "
            "to them.",
            channel,
            requester,
            relation.noun,
        )
        return _forbidden(channel, relation)

    owner_id = rows[0].get(relation.owner_column)
    owner_text = str(owner_id) if owner_id else ""
    if not owner_text:
        logger.warning(
            "[WS/Auth] Subscription to %s refused: the %s row carries no %s.",
            channel,
            relation.noun,
            relation.owner_column,
        )
        return _unresolved(
            channel,
            relation,
            f"That {relation.noun} records no owner.",
        )

    if owner_text != requester:
        # Reached when the reading client is not RLS-scoped to the requester (a
        # service-role client, for instance). The refusal is explicit either way —
        # this is Requirement 21.6's case, and it is never a silent no-op.
        logger.warning(
            "[WS/Auth] Subscription to %s refused: the %s belongs to another user "
            "(requested by %s).",
            channel,
            relation.noun,
            requester,
        )
        return _forbidden(channel, relation)

    return _allowed(channel, owner_id=owner_text)


async def _resolve_training_job_owner(
    job_id: str, user: Any, supabase: Any = None
) -> ChannelAuthorization:
    """Task 6.7's entry point, now a wrapper over the shared resolver.

    Kept by name because the module docstring and ``websocket_manager`` refer to it,
    and because a caller that has a job id rather than a channel reference should not
    have to build one.
    """
    from backend_app.backend.ws_channels import TRAINING_FAMILY, OwnedChannelRef

    return await _resolve_owned_channel_owner(
        OwnedChannelRef(TRAINING_FAMILY, str(job_id)), user, supabase=supabase
    )


async def authorize_channel_subscription(
    channel: Any, user: Any, supabase: Any = None
) -> ChannelAuthorization:
    """Decide whether ``user`` may subscribe to ``channel``.

    Judges the six owner-scoped families ``ws_channels.OWNED_CHANNEL_FAMILIES``
    publishes — ``training.{job_id}``, ``builder.validation.{strategy_id}``,
    ``strategy.{strategy_id}``, ``deployment.{deployment_id}``,
    ``execution.{deployment_id}`` and ``signal.{deployment_id}`` — each against the
    ``user_id`` of the row the channel names, and returns "allowed" for every other
    channel, leaving their existing controls untouched (see the rules at the top of
    this section).

    Never raises: a transport that is mid-message must get a decision, not an
    exception. An unexpected failure is logged and DENIES.
    """
    from backend_app.backend.ws_channels import (
        claims_owned_namespace,
        parse_owned_channel,
    )

    name = channel if isinstance(channel, str) else ""
    if not name:
        return _refused(
            "",
            CHANNEL_REFUSED_UNKNOWN,
            "No channel was named in the subscription.",
        )

    reference = parse_owned_channel(name)
    if reference is None:
        if claims_owned_namespace(name):
            # In one of these families but naming no resource: the bare namespace, or
            # an empty, over-long or wildcard id segment. Refused by name, before any
            # lookup — there is nothing here that could be authorised.
            return _refused(
                name,
                CHANNEL_REFUSED_UNKNOWN,
                "That channel names no valid resource, so the subscription was "
                "refused.",
            )
        # Not a channel this function judges. Its own controls still apply.
        return _allowed(name)

    try:
        return await _resolve_owned_channel_owner(reference, user, supabase=supabase)
    except Exception as exc:  # noqa: BLE001 - a failed check denies
        logger.error(
            "[WS/Auth] Subscription authorisation failed unexpectedly for %s (%s); "
            "refused.",
            name,
            exc,
        )
        return _refused(
            name,
            CHANNEL_REFUSED_OWNER_UNRESOLVED,
            "Ownership of that resource could not be verified, so the subscription "
            "was refused.",
        )
