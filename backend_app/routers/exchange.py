"""
routers/exchange.py — Exchange API Key Vault.

FIXES:
  EXCH-1: Uses get_supabase() singleton throughout
  EXCH-3: delete_connection() now evicts the stale entry from the exchange pool
"""

import asyncio
from datetime import datetime, timezone
import inspect
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from backend_app.backend.connection_engine import (ConnectionEngine,
                                                   release_exchange)
from backend_app.backend.data_seeking_engine import DataEngine
from backend_app.backend.exchange_key_permissions import inspect_key_permissions
from backend_app.core.dependencies import (get_current_user,
                                           get_request_supabase, get_vault,
                                           get_ws_manager, require_aal2)
from backend_app.core.models import ExchangeKeysRequest, TestConnectionRequest
from backend_app.core.subscription_dependencies import (
    check_exchange_connection_slot, exchange_connection_slot_guard)
from backend_app.backend.redis_manager import get_redis_manager
from backend_app.core.rate_limit import limiter  # BE-CRITICAL-004 FIX
from supabase import Client as SupabaseClient

router = APIRouter()
logger = logging.getLogger("ExchangeRouter")


#: The projection ``list_exchanges`` may use against ``exchange_keys``.
#:
#: ``public.exchange_keys`` in production carries EXACTLY ``user_id``, ``exchange_id``,
#: ``encrypted_api_key``, ``encrypted_secret_key``, ``encrypted_password`` and ``created_at``
#: — queried against the live database. It has no ``updated_at`` and no ``id``.
#: ``migrations/003_create_exchange_keys_table.sql`` declares both (plus a
#: ``trg_exchange_keys_updated_at`` trigger), so production's table was never created by that
#: migration and the repo's SQL does not describe it. The projection here follows the LIVE
#: shape, not the migration.
#:
#: The previous projection named ``updated_at``, which PostgreSQL rejected with ``42703``
#: ("column exchange_keys.updated_at does not exist") on every call — four occurrences in a
#: twelve-hour CloudWatch window — and the handler swallowed into an empty list, so an account
#: holding stored credentials was told it had none.
#: `id` IS SELECTED HERE, AND IT ONLY BECAME SELECTABLE WITH MIGRATION 058.
#:
#: EXCH-03: `exchange_keys` had NO `id` column -- eleven columns, PRIMARY KEY the composite
#: `(user_id, exchange_id)` -- while `deployment_binding.load_owned_exchange_account`
#: resolves a deployment's `exchange_account_id` with `.eq("id", ...)`. So every live
#: deployment answered 404, and no client could obtain a usable handle from here either.
#: `058_exch03_exchange_keys_stable_handle.sql` adds `id` as a unique handle beside that
#: composite key, which remains the primary key and remains what
#: `api_key_vault.load_decrypted_keys(user_id, exchange_id)` resolves credentials by.
#:
#: ORDER OF OPERATIONS MATTERS AND WAS LEARNED THE HARD WAY: an earlier attempt added `id`
#: here BEFORE the migration existed, and `tests/test_exchange_connections_read_regression`
#: `.py`'s fake -- which refuses any column outside this set -- caught it. That fake was
#: right. Selecting a column the table does not have raises `42703`, which this endpoint
#: correctly reports as a 503, taking the whole listing down for every caller. Keep this
#: constant, that fixture and the migration set in step.
_EXCHANGE_KEYS_PROJECTION = "id, exchange_id, created_at"

#: The machine-readable code a connections read that DID NOT COMPLETE answers with, alongside
#: HTTP 503. The same never-swallow rule ``routers/paper_trading.py`` (``PAPER_READ_FAILED``)
#: and ``backend/marketplace/entitlement_resolver.py`` (``MARKETPLACE_READ_FAILED``) apply: a
#: failed read may not be reported as an absence, because "you have no connected exchanges" is
#: a statement about the account that nothing established.
EXCHANGE_CONNECTIONS_READ_FAILED = "EXCHANGE_CONNECTIONS_READ_FAILED"

#: The sentence the client renders for that refusal. It matches the reason
#: ``algo22-terminal/src/design/pageFields.js`` already declares for every figure off this read
#: (``CONNECTIONS_UNREAD``), so the page says the same thing whichever side reports it.
_CONNECTIONS_READ_FAILED_MESSAGE = (
    "Your exchange connections could not be read, so none are listed rather than "
    "reported as absent. Try again in a moment."
)


# ══════════════════════════════════════════════════════════════════════════════════════
#  CREDENTIAL-VERIFICATION REFUSALS (SC-27)
# ══════════════════════════════════════════════════════════════════════════════════════
#
# WHY THE EXCEPTION TEXT MAY NOT BE REFLECTED TO THE CALLER
# ---------------------------------------------------------
# ``store_keys`` and ``test_stored_connection`` both dial the venue with credentials and
# both used to answer ``HTTPException(400, f"...{str(e)}")``. That ``e`` is raised by ccxt
# from a call made with the api key and secret the caller submitted microseconds earlier,
# and a ccxt auth error routinely carries the venue's own response body — which can echo
# the submitted parameters, internal endpoint paths and account identifiers — into its
# message. The handler therefore reflected arbitrary third-party text, on the one request
# in the application that is guaranteed to be holding live broker credentials.
#
# WHY NOT JUST RETURN ONE FLAT SENTENCE
# -------------------------------------
# Because the trader has to be able to act. "Verification failed" leaves a wrong key, a
# key missing the read permission, an IP that is not allowlisted at the venue, and a venue
# in maintenance looking identical, and those need four different actions. So the
# exception is CLASSIFIED against ccxt's exception hierarchy and each class maps to a
# stable code and a sentence this repository controls. The caller learns the CATEGORY of
# the failure and never a byte of the provider's text; the full exception, with traceback,
# goes to the operator log.
#
# ORDERING IS LOAD-BEARING. Verified against the pinned ccxt 4.3.92:
#     PermissionDenied   < AuthenticationError < ExchangeError  < BaseError
#     AccountNotEnabled  < PermissionDenied
#     RateLimitExceeded  < NetworkError        < OperationFailed < BaseError
#     OnMaintenance      < ExchangeNotAvailable < NetworkError
# so the narrow classes must be tested before the broad ones. An ``except`` chain would
# bury the ordering in control flow; the explicit table below puts it where it can be read
# and checked against the hierarchy above.
EXCHANGE_VERIFICATION_REJECTED = "EXCHANGE_VERIFICATION_REJECTED"
EXCHANGE_VERIFICATION_FORBIDDEN = "EXCHANGE_VERIFICATION_FORBIDDEN"
EXCHANGE_VERIFICATION_THROTTLED = "EXCHANGE_VERIFICATION_THROTTLED"
EXCHANGE_VERIFICATION_UNREACHABLE = "EXCHANGE_VERIFICATION_UNREACHABLE"
EXCHANGE_VERIFICATION_FAILED = "EXCHANGE_VERIFICATION_FAILED"
EXCHANGE_UNSUPPORTED = "EXCHANGE_UNSUPPORTED"
EXCHANGE_KEY_STORAGE_FAILED = "EXCHANGE_KEY_STORAGE_FAILED"
EXCHANGE_NO_STORED_CREDENTIALS = "EXCHANGE_NO_STORED_CREDENTIALS"
#: EXCH-02: the submitted key can withdraw funds (or, in strict mode, its permissions could
#: not be established). Refused before anything is stored.
EXCHANGE_KEY_WITHDRAW_ENABLED = "EXCHANGE_KEY_WITHDRAW_ENABLED"
EXCHANGE_KEY_PERMISSIONS_UNVERIFIABLE = "EXCHANGE_KEY_PERMISSIONS_UNVERIFIABLE"
#: EXCH-06: the caller ticked "Testnet / Sandbox Mode" for a venue whose sandbox this
#: platform cannot PROVE it would reach -- either the venue publishes no sandbox endpoint
#: at all (coinbase, kraken and kucoin, measured) or the routing could not be verified.
#: Refused before anything is stored, and it is the caller's request that is wrong rather
#: than their key, so it is a 400 and not the 502 an unclassified error would answer.
EXCHANGE_SANDBOX_UNAVAILABLE = "EXCHANGE_SANDBOX_UNAVAILABLE"

#: The sentence a failed storage write answers with. The write is to Supabase, so the
#: exception is a PostgREST error carrying table and column names; those name this
#: deployment's schema and are not the caller's business.
_KEY_STORAGE_FAILED_MESSAGE = (
    "Your credentials were verified but could not be saved. They have not been stored. "
    "Try again in a moment."
)


def _verification_refusal(exc: BaseException, exchange_id: str) -> HTTPException:
    """The refusal a failed credential verification answers with.

    Classifies ``exc`` into one stable code plus a static sentence. The exception's own
    text is deliberately absent from the result: see the section comment above. Callers
    are expected to log ``exc`` themselves (with ``exc_info``) before raising this.

    Args:
        exc: Whatever the verification raised — a ccxt error, the ``ConnectionError`` /
            ``ValueError`` that ``ConnectionEngine.connect`` raises, or anything else.
        exchange_id: The venue, for the client-facing sentence. Echoed from the request,
            and safe to echo: the caller chose it, and it is already in the response of
            every other endpoint in this router.

    Returns:
        An :class:`HTTPException` ready to ``raise ... from exc``.
    """
    import ccxt as ccxt_base

    venue = str(exchange_id).upper()

    from backend_app.backend.exchange_executor import SandboxVerificationFailed

    # Narrowest first. Each entry is (exception types, HTTP status, code, message).
    table = (
        (
            # EXCH-06/EXCH-01. FIRST, and it has to be: `SandboxVerificationFailed`
            # subclasses this platform's own `ExchangeError`, which subclasses plain
            # `Exception` -- it matches no ccxt type and is not a `ValueError`, so
            # without this entry it fell through to the unclassified 502 below. That
            # would have told the caller the failure was on the venue's side or ours,
            # when in fact their request asked for a sandbox that cannot be reached.
            (SandboxVerificationFailed,),
            400,
            EXCHANGE_SANDBOX_UNAVAILABLE,
            f"Testnet / Sandbox Mode was requested for {venue}, but this platform could "
            f"not confirm that the connection would reach {venue}'s sandbox rather than "
            f"its live environment, so it refused to make it. Some venues publish no "
            f"sandbox at all. Submit the key without Testnet / Sandbox Mode to connect "
            f"to live, or use a venue whose sandbox we can verify. Nothing has been "
            f"stored.",
        ),
        (
            (ccxt_base.PermissionDenied,),
            403,
            EXCHANGE_VERIFICATION_FORBIDDEN,
            f"{venue} accepted the key but refused the permissions it needs. Enable read "
            f"and balance access on the key, check that the key is not restricted to an "
            f"IP other than this platform's, and try again.",
        ),
        (
            (ccxt_base.AuthenticationError,),
            400,
            EXCHANGE_VERIFICATION_REJECTED,
            f"{venue} rejected these credentials. Check the API key, secret and "
            f"passphrase for typos or trailing spaces, confirm the key has not been "
            f"revoked, and try again. Nothing has been stored.",
        ),
        (
            (ccxt_base.RateLimitExceeded, ccxt_base.DDoSProtection),
            429,
            EXCHANGE_VERIFICATION_THROTTLED,
            f"{venue} is rate-limiting this platform, so the credentials could not be "
            f"verified. This is not a problem with your key. Try again in a few minutes.",
        ),
        (
            (ccxt_base.NetworkError, ConnectionError, TimeoutError),
            503,
            EXCHANGE_VERIFICATION_UNREACHABLE,
            f"{venue} could not be reached, so the credentials were not verified and "
            f"nothing has been stored. This is not a problem with your key. Try again in "
            f"a few minutes.",
        ),
        (
            (ValueError,),
            400,
            EXCHANGE_UNSUPPORTED,
            f"'{exchange_id}' is not a venue this platform can connect to. Choose one "
            f"from the supported exchanges list.",
        ),
    )

    for types, status_code, code, message in table:
        if isinstance(exc, types):
            return HTTPException(
                status_code=status_code,
                detail={"error": code, "message": message},
            )

    # Unclassified. A 400 would assert the caller's credentials were at fault, which
    # nothing here established, so this answers 502: the failure is on the far side or in
    # this platform, and the operator log carries the detail.
    return HTTPException(
        status_code=502,
        detail={
            "error": EXCHANGE_VERIFICATION_FAILED,
            "message": (
                f"The connection to {venue} could not be verified, so nothing has been "
                f"stored. The failure has been logged for investigation. Try again in a "
                f"few minutes."
            ),
        },
    )


_CACHED_SUPPORTED_EXCHANGES = None

@router.get("/supported")
@limiter.limit("60/minute")  # BE-CRITICAL-004 FIX: Add rate limiting
async def get_supported_exchanges(request: Request, user: dict = Depends(get_current_user)):  # BE-CRITICAL-004 FIX: Require authentication
    """
    Returns list of all CCXT-supported exchanges with full metadata.
    Includes: id, display name, spot/futures/margin support, sandbox support,
    required auth fields, passphrase requirement, subaccount requirement, status.
    Cached in memory and Redis for 1 hour (exchanges list rarely changes).
    
    BE-CRITICAL-004 FIX: Now requires authentication and rate limiting to prevent abuse.
    """
    global _CACHED_SUPPORTED_EXCHANGES
    if _CACHED_SUPPORTED_EXCHANGES is not None:
        return _CACHED_SUPPORTED_EXCHANGES

    try:
        # Try cache first
        from backend_app.core.cache.redis_manager import redis_manager
        cache_key = "supported_exchanges:metadata"
        try:
            cached_str = await redis_manager.get(cache_key)
            if cached_str:
                _CACHED_SUPPORTED_EXCHANGES = json.loads(cached_str)
                return _CACHED_SUPPORTED_EXCHANGES
        except Exception as e:
            logger.warning(f"Redis cache lookup failed: {e}, fetching from CCXT")
        
        # Fetch from CCXT with full metadata
        import ccxt as ccxt_base
        exchanges = []
        
        for exchange_id in ccxt_base.exchanges:
            try:
                exchange_class = getattr(ccxt_base, exchange_id)
                exchange_instance = exchange_class()
                
                # Extract capabilities
                has = exchange_instance.has
                
                # Determine required auth fields dynamically from CCXT
                required_fields = []
                if has.get('apiKey'):
                    required_fields.append('api_key')
                if has.get('secret'):
                    required_fields.append('secret_key')
                if has.get('password'):
                    required_fields.append('password')
                if has.get('uid'):
                    required_fields.append('uid')
                
                # Check for passphrase/password requirement dynamically
                requires_passphrase = has.get('password')
                
                # Check for subaccount support dynamically via exchange options
                requires_subaccount = False
                try:
                    if hasattr(exchange_instance, 'options') and 'defaultType' in exchange_instance.options:
                        requires_subaccount = True
                except Exception:
                    pass
                
                from backend_app.core.exchange_certification import get_exchange_certification_registry
                cert_registry = get_exchange_certification_registry()
                caps = cert_registry.get_capabilities(exchange_id)
                cert_level = caps.certification_level.value if caps else 0
                is_cert = caps.is_production_certified if caps else False
                cert_status = "certified" if is_cert else ("partially_certified" if cert_level >= 2 else "uncertified")

                exchanges.append({
                    "id": exchange_id,
                    "display_name": exchange_instance.name or exchange_id.upper(),
                    "logo": f"/logos/{exchange_id.lower()}.png",
                    "spot_support": bool(has.get('createOrder', False)),
                    "futures_support": bool(has.get('createFuturesOrder', False) or has.get('futures', False)),
                    "margin_support": bool(has.get('createMarginOrder', False) or has.get('margin', False)),
                    "sandbox_support": bool(has.get('sandbox', False)),
                    "required_fields": required_fields,
                    "requires_passphrase": bool(requires_passphrase),
                    "requires_subaccount": bool(requires_subaccount),
                    "certification_level": cert_level,
                    "is_certified": is_cert,
                    "certification_status": cert_status,
                    "known_limitations": caps.known_limitations if caps else ["Level 0: Metadata only"],
                    "status": "certified" if is_cert else "available"
                })
            except Exception as e:
                logger.warning(f"Failed to load metadata for {exchange_id}: {e}")
                continue
        
        # Sort by display name
        exchanges.sort(key=lambda x: x['display_name'])
        
        result = {"exchanges": exchanges, "total": len(exchanges)}
        _CACHED_SUPPORTED_EXCHANGES = result
        
        # Cache for 1 hour (3600 seconds)
        try:
            await redis_manager.set(cache_key, json.dumps(result), ex=3600)
            logger.debug("Cached supported exchanges with metadata for 1 hour")
        except Exception as e:
            logger.warning(f"Redis cache set failed: {e}")
        
        return result
    except Exception as e:
        logger.error(f"Failed to fetch supported exchanges: {e}")
        raise HTTPException(500, "Failed to retrieve supported exchanges.")


@router.post("/keys")
async def store_keys(
    body: ExchangeKeysRequest,
    user: dict = Depends(require_aal2),
    vault=Depends(get_vault),
    redis_manager=Depends(get_redis_manager),
    supabase: Any = Depends(get_request_supabase),
):
    """
    Store encrypted exchange API keys in vault.
    Tests connection before saving to ensure credentials are valid.
    Invalidates cache on successful storage.

    AUTH-01: THIS ENDPOINT REQUIRES AAL2 (MFA). ``Depends(require_aal2)`` replaces
    ``Depends(get_current_user)`` here.
    ----------------------------------------------------------------------------------
    ``require_aal2`` (``core/dependencies.py``) existed and was applied to ZERO
    endpoints — a repository-wide search for ``Depends(require_aal2)`` returned nothing,
    and the dependency's own code comment said so. Its docstring names the operations it
    "MUST be applied to", and the first of them is "Exchange API key mutation
    (create/delete)". That docstring is this repository's specification for the control,
    so wiring it here is applying the stated rule, not introducing a new one.

    WHY THIS ENDPOINT IS THE HIGHEST-VALUE PLACE FOR IT. Storing exchange API keys is the
    single most valuable credential operation in the product: the keys reach a venue that
    holds the customer's money. An attacker holding a stolen password-only session could
    previously attach their own exchange credentials, or rotate the victim's keys to ones
    they control, with no second factor anywhere in the path.

    ``require_aal2`` itself depends on ``get_current_user``, so authentication,
    token validation, the freeze check and the returned user shape are all unchanged —
    this adds a step-up requirement and subtracts nothing.

    PRODUCT-VISIBLE CONSEQUENCE, STATED DELIBERATELY. A customer who has not enrolled a
    TOTP factor cannot reach ``aal2`` and will receive 403 from this endpoint until they
    enrol. Enrolment is fully implemented and reachable (``algo22-terminal/src/pages/TwoFA.jsx``
    — Supabase ``mfa.enroll``/``challenge``/``verify``), so this is a step the customer
    can complete rather than a dead end. It is still a behaviour change that support
    should know about before launch, and it is recorded as such in
    ``staging/REMEDIATION_REPORT.md``.

    NOT applied to the read and connection-test endpoints in this router
    (``/supported``, ``GET /``, ``/test``, ``/test-stored``, ``/{id}/capabilities``,
    ``/connections/...``): the dependency's docstring says "DO NOT apply globally —
    non-sensitive read endpoints should NOT require MFA", and ``/test`` does not persist
    anything. Only the two MUTATING credential operations are gated.
    Regression: tests/test_auth01_require_aal2_is_wired.py

    THE EXCHANGE-CONNECTION LIMIT (Free 1, Trader 2, Pro Quant 5, Business 8)
    ------------------------------------------------------------------------
    Checked FIRST, before the venue is dialled, so an account at its limit is refused in
    milliseconds rather than after a round trip to the exchange — and so no credential is sent
    anywhere on a request that was going to be refused.

    ``check_exchange_connection_slot`` distinguishes a NEW connection from a key ROTATION: this
    same endpoint is how a trader replaces compromised keys for a venue they are already connected
    to, and ``exchange_keys`` holds one row per (user, venue), so a rotation consumes no slot. An
    account sitting at its limit can still rotate every key it owns. Nothing here disconnects or
    invalidates an existing connection — a downgrade leaves stored credentials alone and only
    prevents adding the next one.

    THE LIMIT IS CHECKED TWICE, ON PURPOSE (QUOTA-01)
    -------------------------------------------------
    The call below is a FAST PRE-CHECK: it refuses an over-limit account in milliseconds and,
    more importantly, refuses it before the credential in this request body is sent anywhere.
    It is an optimisation and a credential-hygiene measure, not the control — it reads the
    account's venue set and returns, and by the time the row is written that figure is stale.

    The CONTROL is ``exchange_connection_slot_guard`` further down, which re-reads the same
    figure under a per-account cross-process lock and holds that lock across the vault write,
    so the read that authorises the write and the write itself are one critical section. Two
    concurrent requests for two DIFFERENT venues previously both passed a single free slot; a
    Free account, whose limit is one, finished holding two venue credentials.
    Regression: tests/test_quota01_exchange_slot_is_atomic.py
    """
    await check_exchange_connection_slot(user, supabase, body.exchange_id)

    bridge = None
    try:
        bridge = ConnectionEngine(
            exchange_id=body.exchange_id,
            api_key=body.api_key,
            secret_key=body.secret_key,
            password=body.password,
            uid=body.uid,
            # EXCH-06. VERIFY THE CREDENTIAL AGAINST THE ENVIRONMENT IT IS FOR.
            #
            # This argument was absent, so a testnet credential was handshaked against
            # the PRODUCTION endpoint. It would have failed authentication there and
            # been rejected as invalid, which means the sandbox toggle could not have
            # worked even once the column existed -- the credential would never have
            # reached the vault to be stored with the flag.
            #
            # It also makes the pre-store check mean what it says: this endpoint's job
            # is to prove the credential works before keeping it, and "works" is a
            # question about a specific environment. And because EXCH-05 taught
            # `ConnectionEngine` to PROVE sandbox routing rather than announce it, a
            # `true` here on a venue with no sandbox (coinbase, kraken, kucoin) raises
            # `SandboxVerificationFailed` BEFORE a client is built -- so such a request
            # is refused at the door with nothing stored, which is the correct answer
            # and is classified by `_verification_refusal` below.
            # `getattr` with a False default, matching how `label` and `uid` are read at
            # the vault call below. The declared body model always carries this field, but
            # this handler is also driven by body-LIKE objects in tests, and an object
            # without the attribute must mean PRODUCTION -- the pre-existing behaviour and
            # the safe direction. An AttributeError here would turn a missing optional
            # field into a 502.
            testnet=getattr(body, "sandbox", False),
        )
        exchange = await bridge.connect()
        await DataEngine(exchange).fetch_wallet_balance_snapshot()
        # EXCH-02: introspect the key's permissions on the same connection (never raises).
        permission_report = await inspect_key_permissions(exchange, body.exchange_id)
    except Exception as e:
        # SC-27: logged in full for an operator, classified for the caller. The previous
        # `f"...{str(e)}"` reflected a ccxt message produced from the credentials in this
        # very request body. `exc_info` is what makes the operator side of this trade
        # work — the caller loses the provider text, so the log has to carry it.
        logger.error(
            "Credential verification failed for user=%s exchange=%s: %s: %s",
            user["id"],
            body.exchange_id,
            type(e).__name__,
            e,
            exc_info=True,
        )
        raise _verification_refusal(e, body.exchange_id) from e
    finally:
        if bridge:
            await bridge.disconnect()

    # EXCH-02: refuse a key that can withdraw funds, BEFORE it is stored. A trading platform
    # never needs withdrawal rights; keeping such a key turns any compromise into theft.
    refusal = permission_report.refuse_reason
    if refusal == "WITHDRAW_ENABLED":
        logger.warning("EXCH-02: refused withdraw-enabled key user=%s exchange=%s",
                       user["id"], body.exchange_id)
        raise HTTPException(
            status_code=400,
            detail={
                "error": EXCHANGE_KEY_WITHDRAW_ENABLED,
                "message": (
                    "This API key has withdrawal permission enabled. For your safety we only "
                    "accept keys limited to reading and trading. Create a new key at the "
                    "exchange with withdrawals disabled (and, ideally, restricted to our "
                    "server IP addresses), then submit it again. Nothing was stored."
                ),
            },
        )
    if refusal == "PERMISSIONS_UNVERIFIABLE":
        raise HTTPException(
            status_code=400,
            detail={
                "error": EXCHANGE_KEY_PERMISSIONS_UNVERIFIABLE,
                "message": (
                    "We could not confirm that this API key is unable to withdraw funds on "
                    "this exchange, so it was not stored."
                ),
            },
        )

    try:
        # QUOTA-01: the authoritative slot decision and the write it authorises, in one
        # critical section per account. The guard re-reads the venue set under a cross-process
        # lock, so a concurrent request for a DIFFERENT venue cannot also be told the last slot
        # is free. It refuses rather than admits when the lock cannot be taken (409 contended,
        # 503 unreachable) — see `core/subscription_dependencies.py` for why that direction is
        # the only safe one. The venue handshake above is deliberately outside the lock: it is
        # the slow part, and serialising it would make an account's key storage wait on a
        # 30-second exchange timeout.
        async with exchange_connection_slot_guard(user, supabase, body.exchange_id):
            vault.store_exchange_keys(
                user_id=user["id"],
                exchange_id=body.exchange_id,
                raw_api_key=body.api_key,
                raw_secret=body.secret_key,
                raw_password=body.password,
                raw_secret_key=body.secret_key,
                label=getattr(body, "label", None),
                uid=getattr(body, "uid", None),
                # EXCH-06: the trader's answer to "Testnet / Sandbox Mode", persisted.
                # Read back by `load_decrypted_keys` and consumed by
                # `order_control._executor_for`, whose `bool(keys.get("sandbox"))` was
                # unconditionally False for as long as this argument was missing.
                # Verified against the sandbox endpoint above BEFORE reaching here, so
                # a stored `true` is a flag whose routing was already proven once.
                sandbox=getattr(body, "sandbox", False),
            )
    except HTTPException:
        # The guard's own refusals (409 in flight, 503 lock unavailable) and the pre-check's
        # quota refusal are already the structured answer this router returns. Re-raised before
        # the generic handlers below so they are not relabelled as a storage failure.
        raise
    except ValueError as e:
        # The vault's own argument validation (`_validate_id`, "api_key and secret_key are
        # required"). These strings are written in this repository and name the caller's
        # own input, so they are safe to return — unlike the provider and database text
        # below. Shaped as the structured refusal the rest of this router uses.
        raise HTTPException(
            status_code=400,
            detail={"error": EXCHANGE_KEY_STORAGE_FAILED, "message": str(e)},
        ) from e
    except Exception as e:
        # SC-27: this `e` is a Supabase/PostgREST error, so its text carries table and
        # column names from this deployment's schema — `exchange_keys`, the encrypted
        # column names, and on a 42703 the exact column that is missing. The previous
        # `f"Failed to store keys in vault: {e}"` handed all of it to the client.
        logger.error(
            "Key storage failed for user=%s exchange=%s: %s: %s",
            user["id"],
            body.exchange_id,
            type(e).__name__,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": EXCHANGE_KEY_STORAGE_FAILED,
                "message": _KEY_STORAGE_FAILED_MESSAGE,
            },
        ) from e

    # CONN-01: evict any pooled client built with the SUPERSEDED credential.
    # ----------------------------------------------------------------------
    # This same endpoint is create-OR-ROTATE: a trader replacing compromised keys for a
    # venue they are already connected to reaches here with new credentials. The in-process
    # connection pool (`backend/connection_engine.py::get_or_create_exchange`) keys on
    # sha256([user_id, exchange_id]) and deliberately omits the credential, so a pooled
    # client built from the OLD keys would keep being served to every in-process caller
    # until the connection was deleted or the process restarted -- the rotation would be
    # silently ineffective (finding CONN-01). `delete_connection` already evicts for exactly
    # this reason; the rotation path must do the same. `release_exchange` pops the pooled
    # client and closes its socket, so the next `get_or_create_exchange` for this pair
    # reconstructs with the freshly stored credential. It is a no-op (pop of an absent key)
    # when nothing was pooled -- i.e. for a first-time connection -- so it is safe on the
    # create path too. Placed AFTER the vault write so an eviction never happens on a
    # request that failed to persist the new keys.
    try:
        await release_exchange(user["id"], body.exchange_id.lower())
    except Exception as evict_err:  # noqa: BLE001
        # Eviction is best-effort hardening, not the operation the caller asked for: the
        # keys are already stored. A failure here means a stale pooled client may linger
        # until the next restart, which is the pre-fix behaviour, so it is logged for an
        # operator rather than failing a store that succeeded.
        logger.warning(
            "CONN-01: could not evict the pooled client after a key rotation for "
            "user=%s exchange=%s (%s); the new credential will take effect on the next "
            "reconnect or restart.",
            user["id"],
            body.exchange_id,
            evict_err,
        )

    # Invalidate cache
    if redis_manager:
        cache_key = f"exchanges:list:{user['id']}"
        await redis_manager.cache_delete(cache_key)
        logger.debug(f"Invalidated cache for user {user['id']} after storing new exchange")

    try:
        from backend_app.core.notification_dispatcher import dispatch_user_notification
        await dispatch_user_notification(
            user_id=user["id"],
            event_type="exchange_connected",
            category="exchange",
            severity="info",
            title=f"Exchange Connected: {body.exchange_id.upper()}",
            message=f"Secure connection to {body.exchange_id.upper()} established.",
            exchange=body.exchange_id,
            metadata={"exchange": body.exchange_id, "label": getattr(body, "label", body.exchange_id), "idempotency_key": f"exchange_connect:{user['id']}:{body.exchange_id}"},
        )
    except Exception as notif_err:
        logger.debug(f"[EXCHANGE] Notification dispatch error: {notif_err}")

    # EXCH-02: tell the customer what we could and could not establish about the key.
    warnings = []
    if not permission_report.verified:
        warnings.append("withdrawal_permission_unverified")
    if permission_report.ip_restricted is False:
        warnings.append("ip_allowlist_not_enabled")
    if not warnings:
        return {"status": "ok", "message": "Exchange keys securely encrypted and stored in vault."}
    return {"status": "ok", "message": "Exchange keys securely encrypted and stored in vault.",
            "key_warnings": warnings}


@router.get("")
@router.get("/")
async def list_exchanges(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
    redis_manager=Depends(get_redis_manager),
    vault=Depends(get_vault),
):
    """
    List user's connected exchanges with full metadata.
    Returns exchange status, permissions, bot count, strategy count, health metrics.

    THE CONNECTIONS READ MAY NOT BE SWALLOWED
    -----------------------------------------
    An empty array here is a statement about the ACCOUNT: "you have no exchange credentials
    stored". A read that did not complete establishes nothing of the kind, so it answers 503
    ``EXCHANGE_CONNECTIONS_READ_FAILED`` instead — the rule ``routers/paper_trading.py`` and
    ``backend/marketplace/entitlement_resolver.py`` already follow for their own reads. The
    previous ``except Exception: keys = []`` is what kept a real, four-times-in-twelve-hours
    production ``42703`` invisible: a trader holding credentials saw "no connected exchanges"
    and no error at all.

    ``connected_at`` IS ``created_at``; THERE IS NO ``last_sync``
    ------------------------------------------------------------
    ``created_at`` is when the credential row was written, which is when the connection was
    established — so it answers ``connected_at`` faithfully. It does NOT answer ``last_sync``:
    a row-creation time is not a synchronisation time, and nothing in this system records when
    a venue was last synchronised. ``last_sync`` is therefore not in the response at all rather
    than carrying a timestamp that would assert a sync that never happened.
    """
    try:
        keys: List[Dict[str, Any]] = []
        bot_counts = {}
        strat_counts = {}
        if not supabase:
            # No client means no read was attempted. Answering `[]` would report an absence
            # nothing looked for.
            logger.error(
                "[EXCHANGE] no Persistence_Layer client for the connections read of %s",
                user["id"],
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": EXCHANGE_CONNECTIONS_READ_FAILED,
                    "message": _CONNECTIONS_READ_FAILED_MESSAGE,
                },
            )

        try:
            q1_res = (
                supabase.table("exchange_keys")
                .select(_EXCHANGE_KEYS_PROJECTION)
                .eq("user_id", user["id"])
                .execute()
            )
            q1 = await q1_res if inspect.isawaitable(q1_res) else q1_res
            read_error = getattr(q1, "error", None)
            rows = getattr(q1, "data", None)
            if read_error or not isinstance(rows, list):
                # A response carrying an error, or one with no readable rows, is a read that
                # did not complete - not an account with nothing stored.
                raise RuntimeError(
                    f"the exchange_keys read returned no readable rows (error={read_error!r})"
                )
            keys = [row for row in rows if isinstance(row, dict)]
        except Exception as e:
            logger.error(f"Failed to fetch exchange keys: {e}")
            raise HTTPException(
                status_code=503,
                detail={
                    "error": EXCHANGE_CONNECTIONS_READ_FAILED,
                    "message": _CONNECTIONS_READ_FAILED_MESSAGE,
                },
            ) from e

        try:
            res_strat = supabase.table("strategies").select("id, exchange_id, status").eq("user_id", user["id"]).execute()
            strat_resp = await res_strat if inspect.isawaitable(res_strat) else res_strat
            strat_data = strat_resp.data if strat_resp and hasattr(strat_resp, "data") and isinstance(strat_resp.data, list) else []
            for s in strat_data:
                ex_id = (s.get("exchange_id") or "").lower()
                if ex_id:
                    strat_counts[ex_id] = strat_counts.get(ex_id, 0) + 1
                    if s.get("status") in ("running", "deployed", "active"):
                        bot_counts[ex_id] = bot_counts.get(ex_id, 0) + 1
        except Exception as e:
            logger.warning(f"Failed to fetch user strategies for exchange aggregation: {e}")

        # Resolve the caller's subscription tier ONCE, before the loop, through the
        # injected APIKeyVault singleton. Reporting a hardcoded "free" here told every
        # paying user their connections were on the free tier. Kept outside the loop so
        # the row count does not turn this into an N+1 lookup.
        try:
            tier_info = vault.get_user_tier(user["id"]) if vault else None
        except Exception as tier_err:
            logger.warning(f"Failed to resolve subscription tier for {user['id']}: {tier_err}")
            tier_info = None
        if not isinstance(tier_info, dict):
            # Fail open to the restricted tier rather than to no tier at all.
            tier_info = {"subscription_tier": "free", "max_api_slots": 1}
        subscription_tier = tier_info.get("subscription_tier") or "free"

        exchanges = []
        for row in keys:
            exchange_id = (row.get("exchange_id") or "unknown").lower()
            exchanges.append({
                # A synthesised composite, kept because the existing exchange UI and
                # `api.exchange.delete` already read it. It is NOT a row key and NOT a
                # usable `exchange_account_id`; `account_id` below is.
                "id": f"{user['id']}_{exchange_id}",
                # EXCH-03: the real `exchange_keys.id` (migration 058), which is what a
                # deployment's `exchange_account_id` must carry. Added ALONGSIDE `id`
                # rather than replacing it, so nothing already consuming this response
                # changes meaning underneath it.
                #
                # Passed through as-is and never defaulted to the composite above: a row
                # with no handle cannot be bound to a deployment, and substituting a value
                # that resolves to nothing would turn that into a 404 at deploy time
                # reading as "your account is missing" rather than "this account has no
                # handle".
                "account_id": row.get("id"),
                "exchange_id": exchange_id,
                "name": exchange_id.upper(),
                "credential_configured": True,
                "masked_key": f"{exchange_id[:3].upper()}{'•' * 24}{exchange_id[-2:].upper() if len(exchange_id) > 2 else ''}",
                "status": "CONNECTED",
                "permissions": ["Spot Trading", "Read"],
                "bot_count": bot_counts.get(exchange_id, 0),
                "strategy_count": strat_counts.get(exchange_id, 0),
                "account_type": "Spot",
                "enabled_features": ["Trading", "Balance"],
                # When the credential row was written, which is when this connection was
                # established. `last_sync` is deliberately absent: see the docstring.
                "connected_at": row.get("created_at"),
                "subscription_tier": subscription_tier,
                "health": "healthy"
            })
        return exchanges
    except HTTPException:
        # Already a structured refusal carrying its own code and status. The blanket
        # `return []` this replaced turned that refusal back into a fabricated absence.
        raise
    except Exception as e:
        # Logged for an operator and re-raised. An unexpected failure assembling the response
        # is not evidence that the account has no connections, so it may not answer as one.
        logger.error(f"[EXCHANGE] the connections response could not be assembled: {e}")
        raise


@router.delete("/{exchange_id}")
async def delete_connection(
    exchange_id: str,
    user: dict = Depends(require_aal2),
    supabase: SupabaseClient = Depends(get_request_supabase),
    redis_manager=Depends(get_redis_manager),
):
    """
    Delete exchange connection with safety checks.
    Warns if bots are running, strategies exist, or positions are open.
    Invalidates cache on successful deletion.

    AUTH-01: THIS ENDPOINT REQUIRES AAL2 (MFA).
    ------------------------------------------------------------------
    The second half of the "Exchange API key mutation (create/delete)" operation that
    ``require_aal2``'s docstring mandates. Deletion is destructive in a way that matters
    beyond the credential itself: it is the operation that would be used to cover tracks
    after an unauthorised key rotation, and this handler also decides what happens to
    running bots and open positions on that venue. See ``store_keys`` above for the full
    reasoning and the product-visible consequence.
    Regression: tests/test_auth01_require_aal2_is_wired.py
    """
    try:
        # Check for active bots using this exchange
        res_bots = (
            supabase.table("strategies")
            .select("id, name")
            .eq("user_id", user["id"])
            .eq("exchange_id", exchange_id.lower())
            .eq("status", "deployed")
            .execute()
        )
        bots_resp = await res_bots if inspect.isawaitable(res_bots) else res_bots
        
        active_bots = bots_resp.data if bots_resp and hasattr(bots_resp, "data") and bots_resp.data else []
        
        if active_bots:
            bot_names = [bot.get("name", bot.get("id")) for bot in active_bots]
            raise HTTPException(
                400,
                f"Cannot delete exchange: {len(active_bots)} active bot(s) running. "
                f"Stop bots first: {', '.join(bot_names[:3])}"
            )

        # Delete from database
        res_del = supabase.table("exchange_keys").delete().eq("user_id", user["id"]).eq(
            "exchange_id", exchange_id.lower()
        ).execute()
        if inspect.isawaitable(res_del):
            await res_del

        # Evict from the exchange pool so the stale socket is closed
        await release_exchange(user["id"], exchange_id.lower())

        # Invalidate cache
        if redis_manager:
            cache_key = f"exchanges:list:{user['id']}"
            await redis_manager.cache_delete(cache_key)
            logger.debug(f"Invalidated cache for user {user['id']} after deleting exchange")

        try:
            from backend_app.core.notification_dispatcher import dispatch_user_notification
            await dispatch_user_notification(
                user_id=user["id"],
                event_type="exchange_disconnected",
                category="exchange",
                severity="warning",
                title=f"Exchange Disconnected: {exchange_id.upper()}",
                message=f"Connection to {exchange_id.upper()} was disconnected.",
                exchange=exchange_id,
                metadata={"exchange": exchange_id, "idempotency_key": f"exchange_disconnect:{user['id']}:{exchange_id}"},
            )
        except Exception as notif_err:
            logger.debug(f"[EXCHANGE] Disconnect notification error: {notif_err}")

        logger.info(f"Exchange {exchange_id} deleted for user {user['id']}")
        return {"status": "ok", "message": f"{exchange_id.upper()} disconnected successfully."}
    except HTTPException:
        raise
    except Exception as e:
        # SC-27: no credential is in play here, but `e` is a Supabase/PostgREST or pool
        # error whose text names this deployment's schema and internals.
        logger.error(
            "Failed to disconnect user=%s exchange=%s: %s: %s",
            user["id"],
            exchange_id,
            type(e).__name__,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "EXCHANGE_DISCONNECT_FAILED",
                "message": (
                    "The exchange could not be disconnected. Nothing has been changed. "
                    "Try again in a moment."
                ),
            },
        ) from e


@router.get("/schema/{exchange_id}")
@router.get("/{exchange_id}/connection-schema")
async def get_exchange_auth_schema(
    exchange_id: str,
    user: dict = Depends(get_current_user),
    redis_manager=Depends(get_redis_manager)
):
    """
    Returns the dynamic authentication schema for a specific exchange.
    Sourced from authoritative ExchangeConnectionSchemaRegistry with Redis caching.
    """
    try:
        exchange_id = exchange_id.lower()
        cache_key = f"exchange_schema:{exchange_id}"
        cached = await redis_manager.cache_get_json(cache_key) if redis_manager else None
        
        if cached:
            return cached
        
        from backend_app.core.exchange_connection_schema import get_exchange_connection_schema_registry
        registry = get_exchange_connection_schema_registry()
        schema_obj = registry.get_schema(exchange_id)
        if schema_obj:
            schema = schema_obj.to_dict()
        else:
            # Fallback for uncertified CCXT venues
            import ccxt as ccxt_base
            if not hasattr(ccxt_base, exchange_id):
                raise HTTPException(404, f"Exchange '{exchange_id}' not supported by CCXT.")
            
            exchange_class = getattr(ccxt_base, exchange_id)
            exchange_instance = exchange_class()
            has = getattr(exchange_instance, 'has', {})
            
            fields = []
            if has.get('apiKey', True):
                fields.append({
                    "name": "api_key",
                    "field_id": "api_key",
                    "label": "API Key",
                    "type": "text",
                    "required": True,
                    "secret": True,
                    "placeholder": "Enter your API key",
                    "description": "Public identifier for your API credentials"
                })
            
            if has.get('secret', True):
                fields.append({
                    "name": "secret_key",
                    "field_id": "secret_key",
                    "label": "Secret Key",
                    "type": "password",
                    "required": True,
                    "secret": True,
                    "placeholder": "Enter your secret key",
                    "description": "Private key for signing requests"
                })
            
            if has.get('password'):
                fields.append({
                    "name": "password",
                    "field_id": "password",
                    "label": "Password / Passphrase",
                    "type": "password",
                    "required": True,
                    "secret": True,
                    "placeholder": "Enter your API passphrase",
                    "description": "Additional security parameter for API authentication"
                })
            
            if has.get('uid'):
                fields.append({
                    "name": "uid",
                    "field_id": "uid",
                    "label": "User ID",
                    "type": "text",
                    "required": True,
                    "secret": False,
                    "placeholder": "Enter your user ID",
                    "description": "Unique identifier for your account"
                })
            
            fields.append({
                "name": "label",
                "field_id": "label",
                "label": "Connection Label",
                "type": "text",
                "required": False,
                "secret": False,
                "placeholder": "e.g., Main Account",
                "description": "Optional label to identify this connection"
            })
            
            schema = {
                "exchange_id": exchange_id,
                "display_name": getattr(exchange_instance, 'name', exchange_id.upper()) or exchange_id.upper(),
                "ccxt_id": exchange_id,
                "certification_level": 0,
                "is_certified": False,
                "fields": fields,
                "supports_testnet": bool(has.get('sandbox', False)),
                "supports_subaccount": bool(has.get('createFuturesOrder') or has.get('futures')),
                "default_account_type": "spot" if has.get('createOrder') else None
            }
        
        # Cache for 1 hour
        if redis_manager:
            await redis_manager.cache_set_json(cache_key, schema, ttl=3600)
        
        return schema
    except HTTPException:
        raise
    except Exception as e:
        # SC-27: ccxt metadata only, no credential involved, but the message is still
        # third-party text reflected verbatim.
        logger.error(
            "Failed to fetch auth schema for exchange=%s: %s: %s",
            exchange_id,
            type(e).__name__,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "EXCHANGE_SCHEMA_UNAVAILABLE",
                "message": (
                    f"The connection form for '{exchange_id}' could not be built. Try "
                    f"again in a moment."
                ),
            },
        ) from e


@router.post("/test")
@limiter.limit("10/minute")
async def test_connection(
    request: Request,
    body: ExchangeKeysRequest,
    user: dict = Depends(get_current_user),
):
    """
    Test exchange connection with provided credentials before saving.
    Validates API keys, permissions, clock synchronization, and API scope.

    SEC-12: THIS ENDPOINT IS AN UNRESTRICTED VALIDITY ORACLE FOR THIRD-PARTY API KEYS.
    ------------------------------------------------------------------------------------
    The caller's own ``api_key``/``secret_key``/etc. never have to belong to the caller
    -- this handler dials the named venue with whatever credentials the request body
    carries and reports back whether they are valid and what they can see. With no
    route-specific limit, any authenticated session could use this platform as a
    high-volume checker for OTHER people's stolen or leaked exchange API keys: submit a
    candidate key/secret pair, learn immediately whether it is live and what balance it
    holds, at whatever rate the platform's generic request handling allows.

    ``@limiter.limit("10/minute")`` (the same bound ``routers/auth.py`` uses for
    account-enumeration-prone endpoints) is the actual fix for "unrestricted": it bounds
    how many third-party credentials a single session can probe per minute, which is
    the dimension this finding's title names.

    ``require_aal2`` was deliberately NOT added here, even though AUTH-01's remediation
    note for this same finding suggested it "matching store_keys". AAL2 raises the cost
    of acting under a given identity once; it does nothing to bound how many probes that
    identity can run afterward, so against bulk third-party-key probing specifically it
    is a one-time toll, not a rate limit. ``store_keys`` needs AAL2 because it PERSISTS
    a credential to this user's own vault (AUTH-01's documented "Exchange API key
    mutation" case); this handler persists nothing. Adding it here would also directly
    contradict tests/test_auth01_require_aal2_is_wired.py::
    test_read_and_test_handlers_do_not_require_aal2, which pins -- with its own
    reasoning -- that ``test_connection`` must NOT require AAL2, per ``require_aal2``'s
    own "DO NOT apply globally" rule for a handler that submits credentials but persists
    nothing. That pinned decision was not re-litigated here.

    Regression: tests/test_sec12_exchange_test_endpoint_is_rate_limited.py
    """
    bridge = None
    try:
        bridge = ConnectionEngine(
            exchange_id=body.exchange_id,
            api_key=body.api_key,
            secret_key=body.secret_key,
            password=body.password,
            uid=body.uid,
        )
        exchange = await bridge.connect()
        
        # Validate keys with lightweight call
        is_valid = await bridge.validate_keys()
        if not is_valid:
            raise HTTPException(400, "Invalid API credentials or insufficient permissions.")
        
        # Fetch balance to verify trading permissions
        balance = await DataEngine(exchange).fetch_wallet_balance_snapshot()
        total_usdt = balance.get("USDT", {}).get("total", 0)
        
        # Check exchange status
        status = await bridge.check_exchange_status()
        
        # Check clock synchronization
        clock_sync = "ok"
        try:
            if hasattr(exchange, 'load_time_difference'):
                await exchange.load_time_difference()
                clock_sync = "synchronized"
        except:
            clock_sync = "assumed_ok"
        
        # Determine permissions based on what we could access
        permissions = ["Read"]
        if total_usdt >= 0:  # Successfully fetched balance
            permissions.append("Spot Trading")
        if exchange.has.get('createFuturesOrder'):
            permissions.append("Futures Trading")
        
        return {
            "status": "CONNECTED",
            "exchange": body.exchange_id.upper(),
            "usdt_balance": round(total_usdt, 2),
            "exchange_status": status,
            "clock_sync": clock_sync,
            "permissions": permissions,
            "message": "Connection verified successfully.",
        }
    except HTTPException:
        raise
    except Exception as e:
        # SC-27: identical exposure to ``store_keys`` and fixed identically — this handler
        # also dials the venue with the api key and secret in the request body, so ccxt's
        # message is not the caller's to read. The ``ValueError`` clause that stood above
        # this one is folded in: the classifier maps it to 400 EXCHANGE_UNSUPPORTED, which
        # is the same status it answered before with the exception's text attached.
        logger.warning(
            "Connection test failed for user=%s exchange=%s: %s: %s",
            user["id"],
            body.exchange_id,
            type(e).__name__,
            e,
            exc_info=True,
        )
        raise _verification_refusal(e, body.exchange_id) from e
    finally:
        if bridge:
            await bridge.disconnect()


@router.post("/test-stored")
async def test_stored_connection(
    body: TestConnectionRequest,
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
):
    """
    Test connection for already-stored exchange keys.
    Used to verify existing connections are still valid.
    """
    bridge = None
    try:
        keys = vault.load_decrypted_keys(
            user["id"],
            body.exchange_id,
        )
        bridge = ConnectionEngine(
            exchange_id=body.exchange_id,
            api_key=keys["api_key"],
            secret_key=keys["secret_key"],
            password=keys.get("password"),
        )
        exchange = await bridge.connect()
        balance = await DataEngine(exchange).fetch_wallet_balance_snapshot()
        total_usdt = balance.get("USDT", {}).get("total", 0)
        status = await bridge.check_exchange_status()
        return {
            "status": "CONNECTED",
            "exchange": body.exchange_id.upper(),
            "usdt_balance": round(total_usdt, 2),
            "exchange_status": status,
            "message": "Stored keys verified successfully.",
        }
    except ValueError as e:
        # 404 is preserved: the frontend branches on it to mean "this venue has no stored
        # credentials", and `load_decrypted_keys` raises ValueError for exactly that.
        #
        # SC-27: the text is not. That ValueError is either
        # `f"No keys found for {user_id}/{exchange_id}."` or, from `_decrypt`, "Decryption
        # failed. Key rotation issue or corrupted data." — a very different condition that
        # this handler has always reported as a 404 and that an operator needs to see. The
        # two are not separable without matching on the message, so the status stays as it
        # was and the log carries which one actually happened.
        logger.warning(
            "Stored-credential lookup failed for user=%s exchange=%s: %s",
            user["id"],
            body.exchange_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=404,
            detail={
                "error": EXCHANGE_NO_STORED_CREDENTIALS,
                "message": (
                    f"No usable stored credentials were found for "
                    f"{str(body.exchange_id).upper()}. Reconnect the exchange to store a "
                    f"key, and if you have just rotated one, store it again."
                ),
            },
        ) from e
    except Exception as e:
        # SC-27: the credentials here come from the vault rather than the request body,
        # but they are live broker credentials all the same and ccxt's message is built
        # from the call they were used in.
        logger.warning(
            "Stored connection test failed for user=%s exchange=%s: %s: %s",
            user["id"],
            body.exchange_id,
            type(e).__name__,
            e,
            exc_info=True,
        )
        raise _verification_refusal(e, body.exchange_id) from e
    finally:
        if bridge:
            await bridge.disconnect()


@router.get("/certification")
async def get_certification_summary(user: dict = Depends(get_current_user)):
    """
    Get authoritative certification matrix across all exchanges.
    """
    from backend_app.core.exchange_certification import get_exchange_certification_registry
    registry = get_exchange_certification_registry()
    return registry.get_certification_matrix()


@router.get("/{exchange_id}/capabilities")
async def get_exchange_capabilities(exchange_id: str, user: dict = Depends(get_current_user)):
    """
    Get capabilities and limits for an individual exchange.
    """
    from backend_app.core.exchange_certification import get_exchange_certification_registry
    registry = get_exchange_certification_registry()
    caps = registry.get_capabilities(exchange_id)
    if not caps:
        raise HTTPException(404, f"Exchange '{exchange_id}' not found in certification registry.")
    
    data = caps.to_dict()
    data["health"] = registry.get_exchange_health(exchange_id).value
    return data


@router.get("/{exchange_id}/health")
async def get_exchange_health_status(exchange_id: str, user: dict = Depends(get_current_user)):
    """
    Get runtime health status of an exchange.
    """
    from backend_app.core.exchange_certification import get_exchange_certification_registry
    registry = get_exchange_certification_registry()
    caps = registry.get_capabilities(exchange_id)
    if not caps:
        raise HTTPException(404, f"Exchange '{exchange_id}' not found.")
    
    return {
        "exchange_id": exchange_id,
        "health": registry.get_exchange_health(exchange_id).value,
        "is_certified": caps.is_production_certified,
        "certification_level": caps.certification_level.value,
    }


@router.post("/{exchange_id}/preflight")
async def validate_preflight(
    exchange_id: str,
    deployment_config: dict,
    user: dict = Depends(get_current_user),
):
    """
    Execute authoritative capability and safety preflight check on candidate live deployment.
    """
    from backend_app.core.exchange_certification import get_exchange_certification_registry
    registry = get_exchange_certification_registry()
    
    config = dict(deployment_config)
    config["exchange_id"] = exchange_id
    
    passed, reason = registry.validate_deployment_preflight(config)
    return {
        "exchange_id": exchange_id,
        "preflight_passed": passed,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# DYNAMIC EXCHANGE CONNECTION LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/connections")
async def list_user_connections(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
    redis_manager=Depends(get_redis_manager),
    vault=Depends(get_vault),
):
    """
    Get all active exchange connections for the user without exposing secrets.
    """
    return await list_exchanges(user=user, supabase=supabase, redis_manager=redis_manager, vault=vault)


@router.post("/connections/{exchange_id}/test")
async def test_connection_by_id(
    exchange_id: str,
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
):
    """
    Test stored exchange credentials safely.
    """
    return await test_stored_connection(
        body=TestConnectionRequest(exchange_id=exchange_id),
        user=user,
        vault=vault,
    )


@router.post("/connections/{exchange_id}/reconnect")
async def reconnect_connection_by_id(
    exchange_id: str,
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
):
    """
    Reconnect stored exchange connection and refresh connectivity state.

    CONN-01: THIS ENDPOINT NOW ACTUALLY RECONNECTS.
    ------------------------------------------------------------------------------------
    It answered ``{"status": "RECONNECTED"}`` while doing nothing to the connection. All it
    did was call ``test_stored_connection``, which builds its own throwaway
    ``ConnectionEngine`` and never touches the in-process pool — so the pooled client that
    the trader pressed "reconnect" *because of* was still there afterwards, still broken, and
    still being served to every in-process caller. The one case the endpoint exists for was
    the one case it could not fix.

    Evicting first is what makes the name true: ``release_exchange`` pops the pooled client
    and closes its socket, so the next ``get_or_create_exchange`` for this pair performs a
    fresh handshake with the stored credential. The subsequent connectivity test then reports
    on a connection that is genuinely new rather than on a throwaway client while a stale one
    survives behind it.

    Eviction runs BEFORE the test, not after, so a venue that is still refusing does not leave
    the old client in place — a caller that gets a failing test result has at least had the
    dead socket cleared. It is a no-op when nothing was pooled.
    Regression: tests/test_conn01_pool_lifecycle_and_shutdown.py
    """
    try:
        await release_exchange(user["id"], exchange_id.lower())
    except Exception as evict_err:  # noqa: BLE001
        # Best-effort, same reasoning as `store_keys`: the caller asked for a reconnect, and a
        # failure to close the OLD socket must not turn into a refusal to test the new one.
        logger.warning(
            "CONN-01: could not evict the pooled client before a reconnect for user=%s "
            "exchange=%s (%s); the connectivity test below still runs, but a stale pooled "
            "client may survive until restart.",
            user["id"],
            exchange_id,
            evict_err,
        )

    test_result = await test_stored_connection(
        body=TestConnectionRequest(exchange_id=exchange_id),
        user=user,
        vault=vault,
    )
    return {
        "status": "RECONNECTED",
        "exchange_id": exchange_id,
        "details": test_result,
        "reconnected_at": datetime.now(timezone.utc).isoformat(),
    }


