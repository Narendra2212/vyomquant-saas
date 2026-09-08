"""
routers/billing.py — Subscription management, Stripe + Razorpay webhooks.

SPRINT 2 SECURITY FIXES:
  F-09: DEV_MODE checkout no longer grants paid tiers. Returns mock URL only.
  F-19: Stripe webhook returns HTTP 400 if item_key is absent from session
        metadata instead of silently defaulting to 'elite_1999'.
  F-20: _apply_billing_entitlement writes ONLY to Supabase profiles table.
        SQLite SubscriptionModel / InvoiceModel storage is retired.
        Supabase is the single source of truth for billing state.

PREVIOUS FIXES:
  N1/N11: Stripe webhook reads the actual tier from session metadata.
  N2:     Razorpay webhook reads raw body FIRST (for signature verification),
          then parses as JSON — body stream cannot be read twice.
  N10:    Payment SDKs initialised lazily inside functions, not at import time.
  N4:     Cache invalidated immediately after successful payment.
"""

import hashlib
import hmac
import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
from uuid import UUID
import ipaddress

import inspect
from fastapi import (APIRouter, BackgroundTasks, Depends, Header,
                     HTTPException, Request, Query)
from backend_app.core.rate_limit import limiter
# F-20: get_db retained ONLY for PaymentMethodModel display endpoints.
# Subscription tiers and invoice state are stored exclusively in Supabase.
from sqlalchemy.orm import Session

# MARKETPLACE SETTLEMENT (task 19.2). ``settlement_service`` is the one Settlement_Record writer
# and the only path that may set ``library_subscriptions.status='active'``; ``money`` owns the
# 90/10 split and refuses an inexact amount. Both import only the standard library, so neither
# adds a FastAPI or Persistence_Layer dependency to this module's import cost.
from backend_app.backend.marketplace import money as marketplace_money
from backend_app.backend.marketplace import settlement_service
from backend_app.core.cache import redis_manager
from backend_app.core.dependencies import (create_request_supabase_async,
                                           get_current_user,
                                           get_request_supabase,
                                           invalidate_profile_cache)
from backend_app.core.database import get_db
from backend_app.core.realtime_sync import RealtimeSync
from backend_app.core.models import AddPaymentMethodRequest, PaymentMethodModel
from backend_app.core.schemas import CheckoutRequest

router = APIRouter()
logger = logging.getLogger("BillingRouter")


def _validate_keys(provider: str) -> str:
    if provider == "stripe":
        key = os.environ.get("STRIPE_SECRET_KEY")
        if not key or key == "sk_test_dummy" or not key.startswith("sk_live_"):
            raise HTTPException(500, "Stripe production secret key is missing, invalid, or test credentials are used in production path.")
        return key
    elif provider == "razorpay":
        key = os.environ.get("RAZORPAY_KEY_ID")
        secret = os.environ.get("RAZORPAY_KEY_SECRET")
        if not key or key == "rzp_test_dummy" or not key.startswith("rzp_live_"):
            raise HTTPException(500, "Razorpay production key ID is missing, invalid, or test credentials are used in production path.")
        if not secret or secret == "dummy_secret":
            raise HTTPException(500, "Razorpay production key secret is missing or invalid.")
        return key


async def _sb(user: dict):
    token = user.get("access_token")
    if not token:
        raise HTTPException(401, "Missing authenticated Supabase token.")
    res = create_request_supabase_async(token)
    return await res if inspect.isawaitable(res) else res


def _background_sb():
    from supabase import create_client

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("Billing webhook Supabase service credentials are not configured.")
    return create_client(supabase_url, supabase_key)


from backend_app.core.subscription_engine import Plan, SubscriptionEngine

VALID_ITEM_KEYS = {Plan.FREE.value, Plan.STARTER.value, Plan.PRO.value, Plan.ENTERPRISE.value, "ml_addon"}

# The item_key prefix that marks a marketplace Subscription payment, written once. The dispatch in
# _apply_billing_entitlement keeps its literal spelling deliberately: design.md and
# checkout_service both name that exact expression as THE one marketplace funnel.
_MARKETPLACE_ITEM_PREFIX = "marketplace_"


# BE-CRITICAL-006 FIX: Payment provider IP allowlists
# These are the official IP ranges for Stripe and Razorpay webhooks
STRIPE_WEBHOOK_IPS = [
    # Stripe webhook IPs (can be updated from https://stripe.com/docs/ips)
    "54.187.174.169",
    "54.187.205.235",
    "54.187.216.72",
    "54.241.31.127",
    "54.241.31.135",
    "54.241.34.82",
]

RAZORPAY_WEBHOOK_IPS = [
    # Razorpay webhook IPs (can be updated from Razorpay documentation)
    "13.232.22.250",
    "13.232.118.80",
    "52.66.201.93",
    "52.66.207.93",
]


def _is_allowed_ip(client_ip: str, allowed_ips: List[str]) -> bool:
    """
    BE-CRITICAL-006 FIX: Check if client IP is in allowlist.
    
    Returns True if IP is in the allowed list, False otherwise.
    """
    try:
        ip_obj = ipaddress.ip_address(client_ip)
        for allowed in allowed_ips:
            if ip_obj == ipaddress.ip_address(allowed):
                return True
        return False
    except Exception as e:
        logger.error(f"IP validation error: {e}")
        return False


def _validate_webhook_ip(request: Request, provider: str) -> None:
    """
    BE-CRITICAL-006 FIX: Validate webhook request comes from allowed payment provider IP.
    
    Raises HTTPException if IP is not in allowlist.
    Can be disabled in development mode via ENABLE_WEBHOOK_IP_VALIDATION env var.
    """
    # SECURITY: Always require IP validation in production
    env = os.environ.get("ENV", "development").lower()
    if env == "production":
        enable_ip_validation = True
    else:
        enable_ip_validation = os.getenv("ENABLE_WEBHOOK_IP_VALIDATION", "true").lower() == "true"
    
    if not enable_ip_validation:
        logger.warning("Webhook IP validation disabled - development mode")
        return
    
    # Get client IP from request
    # Check X-Forwarded-For header first (for proxies/load balancers)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "unknown"
    
    # Select appropriate allowlist based on provider
    if provider == "stripe":
        allowed_ips = STRIPE_WEBHOOK_IPS
    elif provider == "razorpay":
        allowed_ips = RAZORPAY_WEBHOOK_IPS
    else:
        raise HTTPException(500, f"Unknown payment provider: {provider}")
    
    # Check if IP is allowed
    if not _is_allowed_ip(client_ip, allowed_ips):
        logger.warning(f"Webhook request from disallowed IP: {client_ip} for provider: {provider}")
        raise HTTPException(403, "Webhook request from unauthorized IP address")


def _validate_webhook_signature(request: Request, provider: str, body: bytes) -> None:
    """
    WEBHOOK SECURITY: Validate webhook signature to prevent message tampering.
    
    Args:
        request: FastAPI request object
        provider: Payment provider ("stripe" or "razorpay")
        body: Raw request body bytes
    
    Raises HTTPException if signature is invalid.
    """
    if provider == "stripe":
        # Stripe signature validation
        signature = request.headers.get("stripe-signature")
        if not signature:
            raise HTTPException(403, "Missing Stripe signature")
        
        stripe_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
        if not stripe_secret:
            raise HTTPException(500, "Stripe webhook secret not configured")
        
        try:
            import stripe
            event = stripe.Webhook.construct_event(
                body.decode('utf-8'),
                signature,
                stripe_secret
            )
            logger.info("Stripe webhook signature validated successfully")
        except Exception as e:
            logger.error(f"Stripe signature validation failed: {e}")
            raise HTTPException(403, "Invalid Stripe signature")
    
    elif provider == "razorpay":
        # Razorpay signature validation
        signature = request.headers.get("x-razorpay-signature")
        if not signature:
            raise HTTPException(403, "Missing Razorpay signature")
        
        razorpay_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
        if not razorpay_secret:
            raise HTTPException(500, "Razorpay webhook secret not configured")
        
        try:
            # Razorpay uses HMAC SHA256 signature
            expected_signature = hmac.new(
                razorpay_secret.encode(),
                body,
                hashlib.sha256
            ).hexdigest()
            
            if not hmac.compare_digest(expected_signature, signature):
                logger.error("Razorpay signature validation failed")
                raise HTTPException(403, "Invalid Razorpay signature")
            
            logger.info("Razorpay webhook signature validated successfully")
        except Exception as e:
            logger.error(f"Razorpay signature validation failed: {e}")
            raise HTTPException(403, "Invalid Razorpay signature")


def _validate_webhook_timestamp(request: Request, provider: str) -> None:
    """
    WEBHOOK SECURITY: Validate webhook timestamp to prevent replay attacks.
    
    Rejects webhooks older than 5 minutes to prevent replay attacks.
    """
    timestamp_header = None
    if provider == "stripe":
        timestamp_header = request.headers.get("stripe-signature")  # Stripe includes timestamp in signature
    elif provider == "razorpay":
        timestamp_header = request.headers.get("x-razorpay-timestamp")
    
    if not timestamp_header:
        logger.warning(f"Missing timestamp header for {provider} webhook")
        return  # Allow but log warning
    
    try:
        # Parse timestamp (implementation depends on provider format)
        from datetime import datetime, timezone
        webhook_time = datetime.fromtimestamp(int(timestamp_header), tz=timezone.utc)
        current_time = datetime.now(timezone.utc)
        
        # Reject webhooks older than 5 minutes
        if (current_time - webhook_time).total_seconds() > 300:
            logger.warning(f"Webhook timestamp too old: {webhook_time}")
            raise HTTPException(403, "Webhook timestamp too old - possible replay attack")
        
        logger.info(f"Webhook timestamp validated for {provider}")
    except Exception as e:
        logger.warning(f"Timestamp validation failed for {provider}: {e}")
        # Don't fail on timestamp issues but log for monitoring


def _validate_uuid(value: str, field_name: str) -> str:
    """
    Validate that a string is a valid UUID.
    Raises HTTPException(400) if invalid.
    """
    try:
        UUID(value)
        return value
    except (ValueError, AttributeError, TypeError) as e:
        logger.error(f"Invalid {field_name} format: {value}")
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name} format: must be a valid UUID"
        )


def _exact_minor_units(value: Any) -> Optional[int]:
    """``value`` as an exact integer number of Minor_Units, or ``None``.

    MARKETPLACE SETTLEMENT (task 19.2), Requirement 10.3: money is integer Minor_Units and nothing
    else on this path. A provider sends the amount as an integer (Stripe's ``amount_total`` in
    cents, Razorpay's ``amount`` in paise) and a JSON decoder may hand it back as text; both of
    those read exactly. A ``float`` returns ``None`` rather than being truncated or rounded, so an
    inexact value is a refusal to settle instead of a silently laundered ledger row - the same
    reading ``settlement_service._amount_matches`` applies to the stored side of the comparison.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _epoch_instant(value: Any) -> Optional[datetime]:
    """A provider's whole-second epoch timestamp as a UTC datetime, or ``None``.

    Used for the payment confirmation instant the Subscription_Period is computed from
    (Requirements 11.4, 11.5). Taking it from the provider's own event rather than from a clock
    read here makes the period reproducible: a redelivery an hour later derives the same two
    boundaries, so the arithmetic is verifiable after the fact.
    """
    seconds = _exact_minor_units(value)
    if seconds is None or seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _stripe_settlement_metadata(metadata: Optional[dict], session: Any) -> dict:
    """The Stripe session metadata plus the provider facts a Settlement_Record needs.

    MARKETPLACE SETTLEMENT (task 19.2). ``checkout_service`` puts ``item_key``,
    ``subscription_id``, ``library_id`` and ``currency`` in the session metadata; what it cannot
    put there is what the provider actually confirmed - the transaction reference, the charged
    amount and the settlement currency - and those are exactly the three values Requirement 9.14
    makes the mismatch guard out of. They are read off the event here, beside the branch that owns
    the event, and travel through the ONE entitlement funnel
    (``_apply_billing_entitlement`` -> ``_apply_marketplace_entitlement``) rather than through a
    second webhook path.

    ``payment_intent`` is preferred over the session id because the refund events carry the payment
    intent, so a payment and its later reversal deduplicate against the same
    ``uq_settlement_reference_reversal`` reference. Nothing is added for a plan purchase: the
    metadata is returned unchanged unless the item is a marketplace item.
    """
    enriched = dict(metadata or {})
    if not str(enriched.get("item_key") or "").startswith(_MARKETPLACE_ITEM_PREFIX):
        return enriched
    enriched["provider"] = "stripe"
    enriched["provider_reference"] = session.get("payment_intent") or session.get("id")
    enriched["amount_minor"] = session.get("amount_total")
    enriched["currency"] = session.get("currency") or enriched.get("currency")
    enriched["confirmed_at_epoch"] = session.get("created")
    return enriched


def _razorpay_settlement_metadata(notes: Optional[dict], payment: Any) -> dict:
    """The Razorpay ``notes`` plus the provider facts a Settlement_Record needs.

    The Razorpay half of :func:`_stripe_settlement_metadata`, and the same contract:
    ``notes["item"]`` is this provider's spelling of ``item_key`` (``checkout_service`` writes
    both halves), ``payment["amount"]`` is already in paise - the Minor_Unit - and
    ``payment["id"]`` is the reference the ``refund.processed`` event carries as ``payment_id``.
    """
    enriched = dict(notes or {})
    item_key = str(enriched.get("item") or enriched.get("item_key") or "")
    if not item_key.startswith(_MARKETPLACE_ITEM_PREFIX):
        return enriched
    enriched["provider"] = "razorpay"
    enriched["provider_reference"] = payment.get("id")
    enriched["amount_minor"] = payment.get("amount")
    enriched["currency"] = payment.get("currency") or enriched.get("currency")
    enriched["confirmed_at_epoch"] = payment.get("created_at")
    return enriched


async def _apply_marketplace_entitlement(
    user_id: str,
    library_id: str,
    subscription_id: str = None,
    *,
    provider: Optional[str] = None,
    provider_reference: Optional[str] = None,
    amount_minor: Any = None,
    currency: Optional[str] = None,
    confirmation_instant: Optional[datetime] = None,
) -> None:
    """
    Record a confirmed marketplace payment and activate the Subscription it paid for.

    Called from billing webhooks when item_key format is 'marketplace_{library_id}' — the ONE
    branch both providers funnel into via ``_apply_billing_entitlement``'s
    ``item_key.startswith("marketplace_")`` test. There is no second webhook route, no second
    signature path and no second entitlement funnel; ``_validate_webhook_signature``,
    ``_validate_webhook_ip``, ``STRIPE_WEBHOOK_IPS``, ``_validate_webhook_timestamp`` and the
    two-phase Redis idempotency lock are untouched (Requirements 9.1, 9.9, 22.5, 22.6, 25.2).

    WHY THE STATUS UPDATE THAT USED TO BE HERE IS GONE (Requirements 9.5, 10.4, 11.6, 11.14)
    ----------------------------------------------------------------------------------------
    This function used to flip ``library_subscriptions.status`` from ``pending`` to ``active``
    itself, set no period expiry and record no settlement. Three things were wrong with that, and
    one call replaces all three: the payment left no Settlement_Record, so no owner earning existed
    and the 90/10 split was never computed (Requirements 10.4, 10.5); the row became ``active``
    with a null expiry, which ``check_deployment_permission`` read as perpetual access; and it was a
    second writer of ``status='active'``, which ``trg_subscription_transition_guard`` now refuses
    outright because no matching ``marketplace_settlements`` row exists at that point.

    ``settlement_service.settle`` is the one Settlement_Record writer and the only path that may
    set ``status='active'``. It performs the amount/currency match (Requirement 9.14), the exact
    integer split (Requirements 10.1, 10.2), the ledger insert under
    ``uq_settlement_reference_reversal`` (Requirements 9.6, 9.7, 10.10), the Subscription_State
    transition, the Subscription_Period write (Requirements 11.4, 11.5), the history row
    (Requirement 11.12) and the entitlement grant carrying the period expiry - in that order, with
    the money recorded before anything is granted, and audits every outcome (Requirements 10.9,
    10.11).

    Outcomes and the HTTP answer each one produces:

    * ``RECORDED`` / ``DUPLICATE_IGNORED`` - success. A redelivery is a no-op by construction, so
      the webhook is idempotent whether or not the Redis lock survived (Requirement 9.6).
    * ``UNMATCHED`` / ``MISMATCHED`` - Requirement 9.14: nothing is written, the Audit_Log entry
      **is** the response, and the webhook returns success because there is nothing for the
      provider to retry. A confirmation for an amount nobody agreed to must not become an earning.
    * ``SettlementPersistFailed`` - Requirement 10.11's budget is exhausted. This raises 500 so the
      provider redelivers; the ledger is unchanged and the audit line carries the provider
      reference for reconciliation. Silence here would be a payment that vanished.
    """
    # Validate UUID format for library_id and subscription_id if provided
    _validate_uuid(library_id, "library_id")
    if subscription_id:
        _validate_uuid(subscription_id, "subscription_id")

    confirmed_amount = _exact_minor_units(amount_minor)
    missing = [
        name
        for name, value in (
            ("subscription_id", subscription_id),
            ("provider", provider),
            ("provider_reference", provider_reference),
            ("amount_minor", confirmed_amount),
            ("currency", currency),
        )
        if not value and value != 0
    ]
    if missing:
        # No settlement context means no Settlement_Record can be written, and Requirement 11.6
        # admits no activation without one. Activating anyway is precisely the defect this task
        # removes, so this refuses instead — loudly, because it can only be an integration fault.
        logger.error(
            f"Marketplace payment confirmation cannot be settled: missing {', '.join(missing)} "
            f"(user={user_id} library={library_id})"
        )
        raise HTTPException(
            status_code=400,
            detail="Marketplace payment confirmation is missing its settlement context",
        )

    try:
        result = await settlement_service.settle(
            provider_reference=str(provider_reference),
            provider=str(provider),
            amount_minor=confirmed_amount,
            currency=str(currency),
            subscription_id=str(subscription_id),
            confirmation_instant=confirmation_instant or datetime.now(timezone.utc),
            supabase=_background_sb(),
        )
    except settlement_service.SettlementPersistFailed as persist_error:
        logger.error(
            f"Marketplace settlement did not persist for provider reference "
            f"{provider_reference}: {persist_error}"
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to activate marketplace subscription"
        )
    except marketplace_money.InvalidAmount as amount_error:
        # The confirmed amount is not an admissible integer number of Minor_Units. Refused before
        # any statement, so nothing is recorded; a redelivery would carry the same amount.
        logger.error(
            f"Marketplace payment confirmation carried an inadmissible amount "
            f"({provider_reference}): {amount_error}"
        )
        raise HTTPException(
            status_code=400,
            detail="Marketplace payment confirmation carried an unusable amount",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to activate marketplace subscription: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to activate marketplace subscription"
        )

    if result.outcome in (
        settlement_service.SettlementOutcome.UNMATCHED,
        settlement_service.SettlementOutcome.MISMATCHED,
    ):
        # Requirement 9.14: no transition, no record, no entitlement — and the audit entry
        # settle() already wrote is the response. Nothing for the provider to retry.
        logger.error(
            f"Marketplace payment confirmation {result.outcome.value}: "
            f"reference={provider_reference} subscription={subscription_id}"
        )
        return

    logger.info(
        f"Marketplace settlement {result.outcome.value}: user={user_id} library={library_id} "
        f"subscription={subscription_id} status={result.from_status}->{result.to_status} "
        f"expiry={result.period_expiry}"
    )


async def _settle_marketplace_reversal(
    *,
    provider: str,
    payment_reference: Optional[str],
    event_metadata: Optional[dict],
    amount_minor: Any,
    currency: Optional[str],
    event_name: str,
) -> None:
    """Record a refund of a marketplace payment as a reversal Settlement_Record.

    MARKETPLACE SETTLEMENT (task 19.2), Requirements 10.4, 10.8. Called from the refund branches
    the webhooks already have — Stripe's ``charge.refunded`` and Razorpay's ``refund.processed`` —
    which already reverse the referral commission. This adds the ledger half: one
    ``settle(..., is_reversal=True)`` call, taken **only** when the refunded payment's ``item_key``
    began with ``marketplace_``, so a plan refund passes through untouched.

    Requirement 10.8 is why this is a second row rather than an edit: a persisted
    Settlement_Record is never updated or deleted, and the reversal references the original
    provider transaction reference through ``reverses_reference`` (which defaults to the payment's
    own reference — the value both providers' refund events carry). ``uq_settlement_reference_
    reversal`` is on ``(provider_reference, is_reversal)``, so the payment row and its reversal
    coexist and a redelivered refund is still a no-op.

    A refund we cannot correlate to a Subscription writes nothing and is logged at error level for
    operator reconciliation: inventing the Subscription a refund belongs to would move an
    entitlement on a guess.

    WHERE THE CORRELATION COMES FROM (task 19.16, Requirements 10.4, 10.8)
    ---------------------------------------------------------------------
    From the ledger, not from the event's metadata. This function used to read
    ``metadata["subscription_id"]`` off the refund event, and **neither provider carries it
    there** by default: Stripe copies Checkout Session metadata onto the PaymentIntent/Charge only
    when the session was created with ``payment_intent_data.metadata``, and a Razorpay refund
    entity does not reliably carry the payment's ``notes``. The consequence in production was that
    most marketplace refunds took the "cannot be correlated" branch and wrote no reversal at all -
    the owner kept an earning for returned money and the refunded subscriber kept the entitlement.

    ``settlement_service.find_settled_payment`` reads the payment's own Settlement_Record back by
    the reference the refund event carries - Stripe's ``payment_intent``, Razorpay's
    ``payment_id``, which is exactly what ``settle`` recorded as ``provider_reference``. The
    Subscription, the currency and the original amount therefore come from a row this system wrote
    itself: authoritative, and nothing is fabricated. ``checkout_service`` also now sets
    ``payment_intent_data.metadata`` (and the matching Razorpay ``notes``) so the provider carries
    the same facts, but that is belt and braces - correlation no longer depends on it.

    Three refusals, all of which write nothing:

    * the ledger read did not complete - 500, so the provider redelivers. A broken read is not
      "no such payment";
    * no Settlement_Record exists for that reference - audited as
      ``MARKETPLACE_SETTLEMENT_UNMATCHED`` and logged at error for reconciliation;
    * the refunded amount is not an exact integer number of Minor_Units - refused, never
      truncated or rounded (Requirement 10.3).

    A PARTIAL refund is passed through with the amount the provider actually returned, so
    ``settle``'s Requirement 9.14 guard sees that it is not the amount the Subscription recorded
    and answers ``MISMATCHED``: nothing is written and the audit names both figures. Requirement
    10.8 contemplates a reversal "equal to the refunded portion", which this does not yet record -
    an open requirements decision, reported rather than silently invented, because a partial
    reversal also has to decide what happens to the entitlement.
    """
    metadata = dict(event_metadata or {})
    item_key = str(metadata.get("item_key") or metadata.get("item") or "")
    if item_key and not item_key.startswith(_MARKETPLACE_ITEM_PREFIX):
        # Positively identified as a plan refund: the marketplace ledger has nothing to say about
        # it, so it passes through untouched and without a ledger read. An ABSENT item_key is NOT
        # treated this way — that is the ordinary shape of both providers' refund events, and
        # treating it as "not marketplace" is the defect this function no longer has.
        return

    if not payment_reference:
        logger.error(
            f"Marketplace refund {event_name} carried no payment reference, so no Settlement_"
            f"Record can be correlated to it and nothing was recorded"
        )
        return

    reference = str(payment_reference)
    try:
        payment = settlement_service.find_settled_payment(
            _background_sb(), provider_reference=reference
        )
    except settlement_service.SettlementPersistenceError as read_error:
        # A read that did not complete is not an answer. Treating it as "no such payment" would
        # discard a real reversal and keep paying an owner for money that went back.
        logger.error(
            f"Marketplace refund {event_name} could not read the settlement ledger for payment "
            f"{reference}: {read_error}"
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to record marketplace refund"
        )

    subscription_id = payment.get("subscription_id") if payment else None
    if not payment or not subscription_id:
        # Requirement 9.14, and the reason this is not a guess: no payment was settled under this
        # reference, so there is no Subscription to reverse. Plan refunds reach here too when the
        # event carried no item_key at all, which is why the audit line names the reference rather
        # than asserting a marketplace intent.
        logger.error(
            f"Marketplace refund {event_name} cannot be correlated: no settlement record exists "
            f"for payment {reference}, so no reversal was written (operator reconciliation)"
        )
        await settlement_service.audit_uncorrelated_refund(
            provider_reference=reference,
            provider=provider,
            amount_minor=_exact_minor_units(amount_minor),
            currency=currency,
            event_name=event_name,
        )
        return

    ledger_currency = str(payment.get("currency") or "")
    original_amount = _exact_minor_units(payment.get("amount_minor"))
    refunded_amount = _exact_minor_units(amount_minor)
    if refunded_amount is None:
        # Requirement 10.3: a float refund amount is refused, not truncated and not rounded.
        logger.error(
            f"Marketplace refund {event_name} carried no exact integer amount in Minor_Units "
            f"({amount_minor!r} for payment {reference}), so no reversal was written"
        )
        return

    event_currency = str(currency or "").strip().upper()
    if event_currency and event_currency != ledger_currency.strip().upper():
        # A refund denominated in a currency the payment did not settle in is not a refund of this
        # payment. Nothing is written, and the ledger's own currency is never overwritten.
        logger.error(
            f"Marketplace refund {event_name} for payment {reference} reported {event_currency} "
            f"against a ledger row settled in {ledger_currency}, so no reversal was written"
        )
        return

    if original_amount is not None and refunded_amount != original_amount:
        # Reported, then passed to settle(), whose Requirement 9.14 guard writes nothing and
        # audits MISMATCHED. Requirement 10.8's "equal to the refunded portion" is an open
        # decision (see the docstring), so a partial reversal is not invented here.
        logger.error(
            f"Marketplace refund {event_name} for payment {reference} is not a full refund: "
            f"{refunded_amount} of {original_amount} {ledger_currency}. No reversal row is "
            f"written for a partial refund; it is recorded as a mismatch for reconciliation"
        )

    try:
        result = await settlement_service.settle(
            provider_reference=reference,
            provider=provider,
            amount_minor=refunded_amount,
            currency=ledger_currency,
            subscription_id=str(subscription_id),
            confirmation_instant=datetime.now(timezone.utc),
            supabase=_background_sb(),
            is_reversal=True,
            reverses_reference=reference,
        )
    except settlement_service.SettlementPersistFailed as persist_error:
        # Requirement 10.11: raise so the provider redelivers. A refund that silently did not
        # reach the ledger keeps paying an owner for money that came back.
        logger.error(
            f"Marketplace reversal did not persist for payment {reference}: {persist_error}"
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to record marketplace refund"
        )
    except marketplace_money.InvalidAmount as amount_error:
        logger.error(
            f"Marketplace refund {event_name} carried an inadmissible amount "
            f"({reference}): {amount_error}"
        )
        return

    if result.outcome in (
        settlement_service.SettlementOutcome.UNMATCHED,
        settlement_service.SettlementOutcome.MISMATCHED,
    ):
        # Requirement 9.14: nothing written, and settle()'s audit line is the response. Logged at
        # error because a refund that recorded no reversal leaves an owner credited with money the
        # purchaser got back, which an operator has to reconcile by hand.
        logger.error(
            f"Marketplace reversal {result.outcome.value} for payment {reference}: "
            f"subscription={subscription_id} refunded={refunded_amount} "
            f"settled={original_amount} {ledger_currency}"
        )
        return

    logger.info(
        f"Marketplace reversal {result.outcome.value} for payment {reference}: "
        f"subscription={subscription_id} status={result.from_status}->{result.to_status}"
    )


async def _apply_billing_entitlement(user_id: str, item_key: str, discount_applied: bool = False, metadata: Optional[dict] = None) -> None:
    """
    F-20: Writes billing state ONLY to Supabase profiles table.
    SQLite SubscriptionModel / InvoiceModel storage is retired.
    Supabase is the single source of truth for subscription_tier.
    
    Marketplace subscriptions (item_key format: marketplace_{library_id}) are handled
    separately - they update library_subscriptions table, not profiles.subscription_tier.
    """
    # Handle marketplace subscriptions separately
    if item_key.startswith("marketplace_"):
        library_id = item_key.replace("marketplace_", "")
        # Extract subscription_id from metadata if available for additional safety
        subscription_id = None
        # The settlement context both providers put on the metadata via
        # _stripe_settlement_metadata / _razorpay_settlement_metadata (task 19.2). This is the ONE
        # marketplace funnel — the provider facts travel through it rather than through a second
        # webhook path.
        provider = None
        provider_reference = None
        amount_minor = None
        currency = None
        confirmation_instant = None
        if isinstance(metadata, dict):
            subscription_id = metadata.get("subscription_id")
            provider = metadata.get("provider")
            provider_reference = metadata.get("provider_reference")
            amount_minor = metadata.get("amount_minor")
            currency = metadata.get("currency")
            confirmation_instant = _epoch_instant(metadata.get("confirmed_at_epoch"))
        await _apply_marketplace_entitlement(
            user_id,
            library_id,
            subscription_id,
            provider=provider,
            provider_reference=provider_reference,
            amount_minor=amount_minor,
            currency=currency,
            confirmation_instant=confirmation_instant,
        )
        return
    
    if item_key not in VALID_ITEM_KEYS:
        logger.error(f"Invalid billing entitlement requested: user={user_id} tier={item_key}")
        raise HTTPException(
            status_code=400,
            detail=f"Invalid billing item key: {item_key}"
        )
    
    # Get previous plan for notification
    previous_plan = None
    try:
        sb = _background_sb()
        resp = sb.table("profiles").select("subscription_tier").eq("id", user_id).execute()
        if resp.data:
            previous_plan = resp.data[0].get("subscription_tier")
    except Exception as e:
        logger.warning(f"Failed to get previous plan for user {user_id}: {e}")
    
    try:
        sb = _background_sb()
        if item_key == "ml_addon":
            sb.rpc("increment_ml_addon", {"target_user_id": user_id}).execute()
            logger.info(f"ML addon applied atomically for user {user_id}")
        else:
            sb.table("profiles").update(
                {"subscription_tier": item_key}
            ).eq("id", user_id).execute()
            logger.info(f"Subscription tier updated for user {user_id}: tier={item_key}")

    except Exception as e:
        logger.error(f"Failed to update Supabase profile for user {user_id}: {e}")
        raise

    # Invalidate profile cache so the new tier takes effect immediately (FIX N4)
    await invalidate_profile_cache(user_id)
    
    # Realtime sync: broadcast to WebSocket
    await RealtimeSync.sync_subscription_change(
        user_id,
        "subscription_updated",
        {
            "item_key": item_key,
            "previous_plan": previous_plan,
            "new_plan": item_key if item_key != "ml_addon" else previous_plan,
            "discount_applied": discount_applied,
        }
    )

    try:
        from backend_app.core.notification_dispatcher import dispatch_user_notification
        await dispatch_user_notification(
            user_id=user_id,
            event_type="subscription_updated",
            category="billing",
            severity="info",
            title=f"Plan Updated: {item_key.upper()}",
            message=f"Your subscription plan has been upgraded to {item_key.upper()}.",
            metadata={"item_key": item_key, "previous_plan": previous_plan, "idempotency_key": f"billing_plan:{user_id}:{item_key}"},
        )
    except Exception as notif_err:
        logger.debug(f"[BILLING] Notification dispatch error: {notif_err}")


async def _process_stripe_entitlement(user_id: str, item_key: str, discount_applied: bool = False, metadata: dict = None) -> None:
    await _apply_billing_entitlement(user_id, item_key, discount_applied, metadata or {})
    logger.info(f"Stripe: Processed '{item_key}' for user {user_id} (Discount: {discount_applied})")


async def _process_razorpay_entitlement(user_id: str, item_key: str, discount_applied: bool = False, metadata: dict = None) -> None:
    await _apply_billing_entitlement(user_id, item_key, discount_applied, metadata or {})
    logger.info(f"Razorpay: Processed '{item_key}' for user {user_id} (Discount: {discount_applied})")


# ── Pricing table ────────────────────────────────────────────────────────
# Pricing now managed by SubscriptionEngine
# Deprecated PRICES dict removed - all pricing now comes from SubscriptionEngine.get_plan_config().pricing


# ── GET /api/billing/plans ───────────────────────────────────────────────
@router.get("/plans")
async def get_plans(
    request: Request,
    currency: Optional[str] = Query(None, description="Optional manual currency code override (e.g. USD, INR, EUR, GBP, JPY)")
):
    """
    Get all available plans with server-authoritative country detection and FX-localized pricing.
    No rate limiting - public endpoint.
    """
    from backend_app.core.pricing_service import PricingService
    
    pricing_context = await PricingService.determine_pricing_context(
        user_id=None,
        supabase=None,
        request=request,
        currency_override=currency,
    )
    
    return await PricingService.get_localized_plans(pricing_context)

# ── GET /api/billing/test ───────────────────────────────────────────────
@router.get("/test")
async def test_endpoint():
    """Test endpoint to verify billing router is loaded."""
    return {"status": "ok", "router": "billing"}


# ── GET /api/billing/entitlements ───────────────────────────────────────────
@router.get("/entitlements")
@limiter.limit("60/minute")
async def get_entitlements(
  request: Request,
  user: dict = Depends(get_current_user),
  supabase: Any = Depends(get_request_supabase),
):
    """Get user's current entitlements."""
    try:
        cache_key = f"billing:entitlements:{user['id']}"
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            cached = await redis_manager.get(cache_key)
            if cached:
                import json
                return json.loads(cached)
        except Exception as cache_err:
            logger.debug(f"Billing entitlements cache read error: {cache_err}")

        from backend_app.core.subscription_dependencies import get_user_entitlements
        
        entitlements = await get_user_entitlements(user, supabase)
        
        # ── Fetch real subscription lifecycle state from Supabase profiles ──
        subscription_status = "active"
        renewal_date = None
        cancel_at_period_end = False
        try:
            if supabase:
                profile_res = supabase.table("profiles").select(
                    "subscription_status,next_billing_date,cancel_at_period_end"
                ).eq("id", user["id"]).execute()
                profile_resp = await profile_res if inspect.isawaitable(profile_res) else profile_res
                if profile_resp and profile_resp.data:
                    row = profile_resp.data[0]
                    subscription_status = row.get("subscription_status") or "active"
                    renewal_date = row.get("next_billing_date")
                    cancel_at_period_end = bool(row.get("cancel_at_period_end", False))
        except Exception as _profile_err:
            logger.debug(f"Could not read lifecycle profile fields for {user['id']}: {_profile_err}")
        
        res_data = {
            "plan": entitlements.plan,
            "features": entitlements.features,
            "quotas": entitlements.quotas,
            "usage": entitlements.usage,
            "subscription_status": subscription_status,
            "renewal_date": renewal_date,
            "cancel_at_period_end": cancel_at_period_end,
        }

        try:
            from backend_app.core.cache.redis_manager import redis_manager
            import json
            await redis_manager.set(cache_key, json.dumps(res_data), ex=10)
        except Exception as cache_write_err:
            logger.debug(f"Billing entitlements cache write error: {cache_write_err}")

        return res_data
    except Exception as e:
        import traceback
        
        # Safe diagnostic logging - no sensitive data
        exc_type = type(e).__name__
        exc_module = type(e).__module__
        exc_message = str(e)
        
        # Get caller info
        frame = inspect.currentframe()
        caller_filename = frame.f_back.f_code.co_filename if frame.f_back else "unknown"
        caller_lineno = frame.f_back.f_lineno if frame.f_back else 0
        
        # Log comprehensive diagnostic info
        logger.error(
            f"[BILLING_ENTITLEMENTS_ENDPOINT] Exception details: "
            f"endpoint=/api/billing/entitlements, "
            f"exception_type={exc_type}, "
            f"exception_module={exc_module}, "
            f"exception_message={exc_message}, "
            f"caller_file={caller_filename}, "
            f"caller_line={caller_lineno}, "
            f"user_id_truncated={user['id'][:8] if user.get('id') else 'missing'}..."
        )
        
        # Log full traceback for debugging
        logger.error(f"[BILLING_ENTITLEMENTS_ENDPOINT] Full traceback:\n{traceback.format_exc()}")
        
        raise HTTPException(
            status_code=500,
            detail={"error": "BILLING_ENTITLEMENTS_FAILED", "message": "Failed to fetch billing entitlements"}
        )


# ── GET /api/billing/plan ────────────────────────────────────────────────
@router.get("/plan")
@limiter.limit("60/minute")
async def get_current_plan(
    request: Request,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Get user's current billing plan (alias for /api/billing/entitlements)."""
    return await get_entitlements(request, user, supabase)


# ── GET /api/billing/currency ────────────────────────────────────────────
@router.get("/currency")
@limiter.limit("60/minute")
async def get_currency(
  request: Request,
  user: dict = Depends(get_current_user),
  supabase: Any = Depends(get_request_supabase),
):
    """Get user's complete currency & country context (auto-detected from trusted IP or saved preference)."""
    cache_key = f"billing:currency_ctx:{user.get('id', '')}"
    try:
        from backend_app.core.cache.redis_manager import redis_manager
        cached = await redis_manager.get(cache_key)
        if cached:
            import json
            return json.loads(cached)
    except Exception:
        pass

    try:
        from backend_app.core.pricing_service import PricingService
        ctx = await PricingService.determine_pricing_context(user.get("id", ""), supabase, request)
        res = {
            "country": ctx["country"],
            "country_name": ctx["country_name"],
            "currency": ctx["currency"],
            "currency_symbol": ctx["currency_symbol"],
            "currency_source": ctx["currency_source"],
            "checkout_currency": ctx["checkout_currency"],
            "checkout_currency_symbol": ctx["checkout_currency_symbol"],
            "checkout_provider": ctx["checkout_provider"],
            "is_direct_checkout": ctx["is_direct_checkout"],
            "fx_rate": ctx["fx_rate"],
            "fx_rate_timestamp": ctx["fx_rate_timestamp"],
            "is_fallback": ctx["is_fallback"],
            "supported_currencies": PricingService.get_localized_plans.__globals__["FXService"].get_supported_display_currencies(),
        }
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            import json
            await redis_manager.set(cache_key, json.dumps(res), ex=10)
        except Exception:
            pass
        return res
    except Exception as e:
        logger.warning(f"Failed to determine currency context for user {user.get('id')}: {e}")
        from datetime import datetime, timezone
        return {
            "country": "US",
            "country_name": "United States",
            "currency": "USD",
            "currency_symbol": "$",
            "currency_source": "fallback",
            "checkout_currency": "USD",
            "checkout_currency_symbol": "$",
            "checkout_provider": "stripe",
            "is_direct_checkout": True,
            "fx_rate": 1.0,
            "fx_rate_timestamp": datetime.now(timezone.utc).isoformat(),
            "is_fallback": True,
            "supported_currencies": [],
        }


# ── POST /api/billing/currency ──────────────────────────────────────────────
@router.post("/currency")
@limiter.limit("10/minute")
async def set_currency(
  request: Request,
  body: Dict[str, str],
  user: dict = Depends(get_current_user),
  supabase: Any = Depends(get_request_supabase),
):
    """Set user's manual currency preference."""
    try:
        from backend_app.core.pricing_service import PricingService
        currency = body.get("currency", "USD")
        success = await PricingService.set_user_currency_preference(user["id"], currency, supabase)
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            await redis_manager.delete(f"billing:currency_ctx:{user['id']}")
            await redis_manager.delete(f"billing:currency:{user['id']}")
        except Exception:
            pass
        if not success:
            return {"status": "ok", "currency": "USD", "warning": "Failed to persist preference"}
        return {"status": "ok", "currency": currency}
    except Exception as e:
        logger.warning(f"Failed to set currency preference: {e}")
        return {"status": "ok", "currency": "USD"}
    
    return {"status": "success", "currency": currency}


# ── GET /api/billing/invoices ───────────────────────────────────────────────
@router.get("/invoices")
@limiter.limit("60/minute")
async def get_invoices(
    request: Request,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Get invoice history from Supabase."""
    cache_key = f"billing:invoices:{user['id']}"
    try:
        from backend_app.core.cache.redis_manager import redis_manager
        cached = await redis_manager.get(cache_key)
        if cached:
            import json
            return json.loads(cached)
    except Exception:
        pass

    if not supabase:
        return []
    
    try:
        res = supabase.table("billing_invoices").select("*").eq("user_id", user["id"]).order("created_at", desc=True).execute()
        resp = await res if inspect.isawaitable(res) else res
        invoices = []
        rows = resp.data if resp and hasattr(resp, "data") and resp.data else []
        for inv in rows:
            invoices.append({
                "id": inv.get("id"),
                "date": inv.get("created_at"),
                "amtUSD": inv.get("amount_usd", 0),
                "amtINR": inv.get("amount_inr", 0),
                "status": inv.get("status", "unknown"),
                "currency": inv.get("currency", "USD"),
            })
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            import json
            await redis_manager.set(cache_key, json.dumps(invoices), ex=10)
        except Exception:
            pass
        return invoices
    except Exception as e:
        logger.warning(f"Failed to fetch invoices for user {user['id']}: {e}")
        return []


@router.get("/payment-methods")
@limiter.limit("60/minute")
async def get_payment_methods(
    request: Request,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cache_key = f"billing:payment_methods:{user['id']}"
    try:
        from backend_app.core.cache.redis_manager import redis_manager
        cached = await redis_manager.get(cache_key)
        if cached:
            import json
            return json.loads(cached)
    except Exception:
        pass

    methods = db.query(PaymentMethodModel).filter(PaymentMethodModel.user_id == user["id"]).all()
    res = []
    for m in methods:
        res.append({
            "id": m.id,
            "type": "card",
            "brand": m.brand,
            "last4": m.last4,
            "expiry_month": m.expiry_month,
            "expiry_year": m.expiry_year,
            "is_default": m.is_default
        })
    try:
        from backend_app.core.cache.redis_manager import redis_manager
        import json
        await redis_manager.set(cache_key, json.dumps(res), ex=10)
    except Exception:
        pass
    return res

@router.post("/checkout")
@limiter.limit("10/minute")
async def create_checkout_session(
  request: Request,
  body: CheckoutRequest,
  background_tasks: BackgroundTasks,
  user: dict = Depends(get_current_user),
  supabase: Any = Depends(get_request_supabase),
):
    """
    Generates a server-authoritative payment link with FX conversion and gateway routing.
    Server calculates the exact minor units and validates gateway capability.
    INR → Razorpay. All other supported currencies (USD, EUR, GBP, JPY, CAD, etc.) → Stripe.
    """
    from backend_app.core.fx_service import FXService
    from backend_app.core.pricing_service import PricingService

    item_key = "ml_addon" if body.is_addon else body.tier.value

    # Step 1: Resolve currency from body or user context
    requested_currency = (body.currency or "USD").strip().upper()

    # Step 2: Resolve base USD price from canonical SubscriptionEngine
    if body.is_addon:
        base_usd = 3.00  # $3.00 USD for ML Addon
    else:
        plan_config = SubscriptionEngine.get_plan_config(item_key)
        if not plan_config:
            raise HTTPException(400, f"Invalid subscription tier: {item_key}")
        # Pricing in SubscriptionEngine is stored in cents/paise
        base_usd = plan_config.pricing.get("USD", 0) / 100.0

    # Free plan cannot be checked out
    if base_usd <= 0 and not body.is_addon:
        raise HTTPException(400, "Cannot checkout for free tier.")

    # Step 3: Server-Authoritative FX Localization & Minor Unit Calculation
    localized = await FXService.localize_price(base_usd, requested_currency)
    checkout_currency = localized.checkout_currency
    provider = localized.checkout_provider
    amount = localized.checkout_amount_minor

    # Step 4: Check and apply discounts
    discount_applied = False
    try:
        if supabase:
            query_res = supabase.table("profiles").select("available_discounts").eq("id", user["id"]).execute()
            profile_res = await query_res if inspect.isawaitable(query_res) else query_res
            if profile_res.data and profile_res.data[0].get("available_discounts", 0) > 0:
                discount_applied = True
                amount = int(round(amount * 0.9))
    except Exception as e:
        logger.warning(f"Could not fetch available discounts for {user['id']}: {e}")

    if amount <= 0:
        raise HTTPException(400, "Calculated checkout amount must be greater than zero.")

    try:
        if provider == "stripe":
            stripe_key = _validate_keys("stripe")
            import stripe
            stripe.api_key = stripe_key

            session_params = {
                "payment_method_types": ["card"],
                "line_items": [
                    {
                        "price_data": {
                            "currency": checkout_currency.lower(),
                            "product_data": {
                                "name": f"Aerora Dynamics — {item_key.upper()}"
                            },
                            "unit_amount": amount,
                            # Add recurring interval for subscription mode
                            **({
                                "recurring": {"interval": "month"}
                            } if not body.is_addon else {}),
                        },
                        "quantity": 1,
                    }
                ],
                "mode": "payment" if body.is_addon else "subscription",
                "success_url": (
                    f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}"
                    "/app/billing?payment=success"
                ),
                "cancel_url": (
                    f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/app/billing"
                ),
                "client_reference_id": user["id"],
                "metadata": {
                    "user_id": user["id"],
                    "item_key": item_key,
                    "currency": checkout_currency,
                    "fx_rate": str(localized.fx_rate),
                    "discount_applied": "true" if discount_applied else "false",
                },
            }
            # For subscription mode: attach metadata to the Stripe Subscription object
            # so that customer.subscription.* webhooks can resolve item_key and currency
            if not body.is_addon:
                session_params["subscription_data"] = {
                    "metadata": {
                        "user_id": user["id"],
                        "item_key": item_key,
                        "currency": checkout_currency,
                        "fx_rate": str(localized.fx_rate),
                        "discount_applied": "true" if discount_applied else "false",
                    }
                }
            session = stripe.checkout.Session.create(**session_params)
            return {
                "checkoutUrl": session.url,
                "checkout_url": session.url,
                "provider": "stripe",
                "currency": checkout_currency,
                "amount": amount,
            }

        elif provider == "razorpay":
            rzp_key = _validate_keys("razorpay")
            import razorpay
            rzp = razorpay.Client(
                auth=(
                    rzp_key,
                    os.environ["RAZORPAY_KEY_SECRET"],
                )
            )
            order = rzp.order.create(
                {
                    "amount": amount,
                    "currency": "INR",
                    "receipt": f"receipt_{user['id'][:8]}",
                    "notes": {
                        "user_id": user["id"], 
                        "item": item_key,
                        "currency": "INR",
                        "fx_rate": str(localized.fx_rate),
                        "discount_applied": "true" if discount_applied else "false"
                    },
                }
            )
            try:
                link = rzp.payment_link.create({
                    "amount": amount,
                    "currency": "INR",
                    "reference_id": order["id"],
                    "description": f"Aerora Dynamics - {item_key.upper()}",
                    "customer": {
                        "email": user.get("email", "user@aerora.io")
                    },
                    "notes": {
                        "user_id": user["id"], 
                        "item": item_key,
                        "currency": "INR",
                        "fx_rate": str(localized.fx_rate),
                        "discount_applied": "true" if discount_applied else "false"
                    },
                    "callback_url": f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/app/billing?payment=success",
                    "callback_method": "get"
                })
                checkout_url = link["short_url"]
            except Exception as le:
                logger.warning(f"Failed to create Razorpay hosted payment link: {le}. Falling back to default success URL.")
                checkout_url = f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/app/billing?payment=success"

            return {
                "checkoutUrl": checkout_url,
                "checkout_url": checkout_url,
                "order_id": order["id"],
                "provider": "razorpay",
                "currency": "INR",
                "amount": amount,
            }
        else:
            raise HTTPException(500, f"Unsupported payment provider for currency: {checkout_currency}")

    except Exception as e:
        logger.error(f"Checkout creation failed for {user['id']}: {e}")
        raise HTTPException(500, "Failed to initialize payment gateway.")


# ── POST /api/billing/webhook/stripe ────────────────────────────────────
@router.post("/webhook/stripe")
async def stripe_webhook(
    background_tasks: BackgroundTasks,
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
):
    # WEBHOOK SECURITY: Validate webhook IP before processing
    _validate_webhook_ip(request, "stripe")
    
    stripe_key = _validate_keys("stripe")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not webhook_secret or webhook_secret == "whsec_dummy" or not webhook_secret.startswith("whsec_"):
        raise HTTPException(500, "Stripe production webhook secret is missing, invalid, or test credentials are used in production path.")

    import stripe
    stripe.api_key = stripe_key

    payload = await request.body()
    
    # WEBHOOK SECURITY: Validate webhook signature
    _validate_webhook_signature(request, "stripe", payload)
    
    # WEBHOOK SECURITY: Validate webhook timestamp
    _validate_webhook_timestamp(request, "stripe")
    
    try:
        secret = webhook_secret or "whsec_dummy"
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, secret
        )
    except Exception as e:
        logger.warning(f"Stripe webhook signature failure: {e}")
        raise HTTPException(400, f"Webhook Error: {e}")
    
    # BUG-FIX BILL-01 / BUG-IB-04: Crash-safe two-phase idempotency.
    # 1. Acquire short-lived processing lock (60s TTL). If crash occurs, key expires in 60s instead of blocking retries for 24h.
    # 2. On DB commit / successful handling, transition key to "completed" with 86400s TTL.
    # 3. On failure / exception before DB commit, release key immediately so retries can execute.
    event_id = event.get("id") or request.headers.get("stripe-event-id")
    idempotency_key = f"webhook:stripe:{event_id}" if event_id else None
    if idempotency_key:
        cached_val = await redis_manager.get(idempotency_key)
        if cached_val in ("completed", "1", "processing"):
            logger.info(f"Stripe webhook {event_id} already processed or in-flight, skipping")
            return {"status": "duplicate"}
        acquired = await redis_manager.set(idempotency_key, "processing", nx=True, ex=60)
        if not acquired:
            logger.info(f"Stripe webhook {event_id} lock collision, skipping")
            return {"status": "duplicate"}

    try:
        # Log webhook event for audit trail
        logger.info(f"Stripe webhook event: {event['type']}")

        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            user_id = session.get("client_reference_id")
            metadata = session.get("metadata", {})

            # F-19 FIX: Missing item_key returns HTTP 400 — never default to a paid tier.
            item_key = metadata.get("item_key")
            if not item_key:
                logger.error(
                    f"Stripe webhook: missing item_key in session metadata "
                    f"for client_reference_id={user_id}. Metadata received: {metadata}"
                )
                raise HTTPException(
                    status_code=400,
                    detail="Missing item_key in Stripe session metadata. Cannot process entitlement.",
                )

            if not user_id:
                logger.error("Stripe webhook: missing client_reference_id")
                raise HTTPException(
                    status_code=400,
                    detail="Missing client_reference_id in Stripe session"
                )

            discount_applied = metadata.get("discount_applied") == "true"
            try:
                await _process_stripe_entitlement(user_id, item_key, discount_applied, _stripe_settlement_metadata(metadata, session))
                
                # Process referral commission on successful payment
                payment_id = session.get("payment_intent") or session.get("id")
                payment_amount = session.get("amount_total", 0) / 100.0  # Convert from cents to USD
                
                # ── Insert billing_invoices record ──
                try:
                    sb_inv = _background_sb()
                    sb_inv.table("billing_invoices").insert({
                        "user_id": user_id,
                        "provider": "stripe",
                        "provider_payment_id": payment_id or "",
                        "amount_usd": round(payment_amount, 2),
                        "amount_inr": 0,
                        "currency": "USD",
                        "status": "paid",
                        "plan": item_key,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }).execute()
                except Exception as inv_err:
                    logger.warning(f"[BILLING] Could not insert billing_invoices (Stripe): {inv_err}")

                # ── Dispatch payment_succeeded notification ──
                try:
                    from backend_app.core.notification_dispatcher import dispatch_user_notification
                    await dispatch_user_notification(
                        user_id=user_id,
                        event_type="payment_succeeded",
                        category="billing",
                        severity="info",
                        title="Payment Successful",
                        message=f"Your payment of ${payment_amount:.2f} USD was successful. Plan: {item_key.upper()}.",
                        metadata={"item_key": item_key, "payment_id": payment_id, "provider": "stripe"},
                    )
                except Exception as notif_err:
                    logger.debug(f"[BILLING] payment_succeeded notification error: {notif_err}")

                if payment_amount > 0:
                    try:
                        sb = _background_sb()
                        sb.rpc("process_referral_commission", {
                            "p_referred_id": user_id,
                            "p_payment_id": payment_id,
                            "p_subscription_tier": item_key,
                            "p_payment_amount_usd": payment_amount
                        }).execute()
                        logger.info(f"Referral commission processed for Stripe payment {payment_id}")
                    except Exception as ref_err:
                        logger.error(f"Failed to process referral commission for payment {payment_id}: {ref_err}")
                        # Don't fail the webhook if commission processing fails
            except Exception as e:
                logger.error(f"Stripe entitlement processing failed: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Entitlement update failed: {e}"
                )

        elif event["type"] in ("customer.subscription.deleted", "customer.subscription.updated"):
            sub_obj = event["data"]["object"]
            user_id = sub_obj.get("metadata", {}).get("user_id")

            if not user_id:
                cust_id = sub_obj.get("customer")
                cust = stripe.Customer.retrieve(cust_id)
                user_id = cust.get("metadata", {}).get("user_id")

            if not user_id:
                logger.error(f"Stripe subscription event {event['type']} missing user_id reference.")
                raise HTTPException(
                    status_code=400,
                    detail="Missing user_id in Stripe subscription event metadata"
                )

            if event["type"] == "customer.subscription.deleted":
                try:
                    await _process_stripe_entitlement(user_id, "free")
                    logger.info(f"Stripe: Processed cancellation (free) for user {user_id}")
                except Exception as e:
                    logger.error(f"Stripe subscription deletion handler failed: {e}")
                    raise HTTPException(500, f"Subscription deletion failed: {e}")
            else:
                # updated: check updated item_key
                item_key = sub_obj.get("metadata", {}).get("item_key")
                if not item_key:
                    items = sub_obj.get("items", {}).get("data", [])
                    if items:
                        price_id = items[0].get("price", {}).get("id")
                        logger.warning(f"No item_key in subscription update metadata. Resolving by price_id: {price_id}")
                if item_key:
                    try:
                        await _process_stripe_entitlement(user_id, item_key)
                        logger.info(f"Stripe: Processed update ({item_key}) for user {user_id}")
                    except Exception as e:
                        logger.error(f"Stripe subscription update handler failed: {e}")
                        raise HTTPException(500, f"Subscription update failed: {e}")

        elif event["type"] == "invoice.payment_succeeded":
            # ── Subscription renewal: mark active and extend billing period ──
            invoice_obj = event["data"]["object"]
            sub_id = invoice_obj.get("subscription")
            if sub_id:
                renewal_user_id = None
                try:
                    sub = stripe.Subscription.retrieve(sub_id)
                    renewal_user_id = sub.get("metadata", {}).get("user_id")
                    if not renewal_user_id:
                        cust_id = invoice_obj.get("customer")
                        cust = stripe.Customer.retrieve(cust_id)
                        renewal_user_id = cust.get("metadata", {}).get("user_id")
                    if renewal_user_id:
                        renewal_item_key = sub.get("metadata", {}).get("item_key", "")
                        from backend_app.core.billing_lifecycle import BillingLifecycle
                        sb_renew = _background_sb()
                        await BillingLifecycle.renew_subscription(renewal_user_id, sb_renew)
                        await invalidate_profile_cache(renewal_user_id)
                        # Insert billing_invoices record for renewal
                        renewal_amount = invoice_obj.get("amount_paid", 0) / 100.0
                        renewal_payment_id = invoice_obj.get("payment_intent") or invoice_obj.get("id", "")
                        try:
                            sb_inv = _background_sb()
                            sb_inv.table("billing_invoices").insert({
                                "user_id": renewal_user_id,
                                "provider": "stripe",
                                "provider_payment_id": renewal_payment_id,
                                "amount_usd": round(renewal_amount, 2),
                                "amount_inr": 0,
                                "currency": "USD",
                                "status": "paid",
                                "plan": renewal_item_key or "unknown",
                                "created_at": datetime.now(timezone.utc).isoformat(),
                            }).execute()
                        except Exception as inv_err:
                            logger.warning(f"[BILLING] Could not insert billing_invoices (Stripe renewal): {inv_err}")
                        # Dispatch renewal notification
                        try:
                            from backend_app.core.notification_dispatcher import dispatch_user_notification
                            await dispatch_user_notification(
                                user_id=renewal_user_id,
                                event_type="subscription_renewed",
                                category="billing",
                                severity="info",
                                title="Subscription Renewed",
                                message=f"Your subscription has been renewed successfully. Amount: ${renewal_amount:.2f} USD.",
                                metadata={"item_key": renewal_item_key, "payment_id": renewal_payment_id, "provider": "stripe"},
                            )
                        except Exception as notif_err:
                            logger.debug(f"[BILLING] subscription_renewed notification error: {notif_err}")
                        logger.info(f"Stripe: Renewal processed for user {renewal_user_id}")
                except Exception as renew_err:
                    logger.error(f"Stripe renewal processing error for sub {sub_id}: {renew_err}")

        elif event["type"] == "invoice.payment_failed":
            invoice_obj = event["data"]["object"]
            sub_id = invoice_obj.get("subscription")
            user_id = None
            if sub_id:
                sub = stripe.Subscription.retrieve(sub_id)
                user_id = sub.get("metadata", {}).get("user_id")
            if not user_id:
                cust_id = invoice_obj.get("customer")
                cust = stripe.Customer.retrieve(cust_id)
                user_id = cust.get("metadata", {}).get("user_id")

            if user_id:
                try:
                    sb = _background_sb()
                    sb.table("profiles").update({
                        "is_frozen": True,
                        "subscription_status": "past_due",
                    }).eq("id", user_id).execute()
                    await invalidate_profile_cache(user_id)
                    logger.info(f"User {user_id} account frozen due to invoice payment failure.")
                    # Dispatch payment_failed notification
                    try:
                        from backend_app.core.notification_dispatcher import dispatch_user_notification
                        await dispatch_user_notification(
                            user_id=user_id,
                            event_type="payment_failed",
                            category="billing",
                            severity="critical",
                            title="Payment Failed — Action Required",
                            message="Your subscription payment failed. Please update your payment method to restore access.",
                            metadata={"provider": "stripe", "action_url": "/app/billing"},
                        )
                    except Exception as notif_err:
                        logger.debug(f"[BILLING] payment_failed notification error: {notif_err}")
                    # Broadcast via WebSocket for immediate UI refresh
                    await RealtimeSync.sync_subscription_change(
                        user_id, "payment_failed",
                        {"subscription_status": "past_due", "action": "update_payment_method"}
                    )
                except Exception as e:
                    logger.error(f"Stripe payment failure handler failed to freeze account: {e}")
                    raise HTTPException(500, f"Payment failure handler failed: {e}")

        elif event["type"] in ("charge.refunded", "charge.refund.updated"):
            # Handle refunds and chargebacks - reverse referral commission
            charge_obj = event["data"]["object"]
            payment_id = charge_obj.get("id")
            
            if payment_id:
                try:
                    sb = _background_sb()
                    sb.rpc("reverse_referral_commission", {
                        "p_payment_id": payment_id,
                        "p_reversal_reason": event["type"]
                    }).execute()
                    logger.info(f"Referral commission reversed for Stripe payment {payment_id} due to {event['type']}")
                except Exception as ref_err:
                    logger.error(f"Failed to reverse referral commission for payment {payment_id}: {ref_err}")
                    # Don't fail the webhook if commission reversal fails

                # MARKETPLACE SETTLEMENT (task 19.2), Requirements 10.4, 10.8: the ledger half of a
                # refund. Only ``charge.refunded`` means money actually went back — ``charge.
                # refund.updated`` also fires when a refund FAILS, and recording a reversal for a
                # refund that never completed would subtract an owner's earning for money that
                # never left and cancel a live entitlement. The reference is the payment intent, so
                # the reversal deduplicates against the same reference the payment settled under.
                if event["type"] == "charge.refunded":
                    await _settle_marketplace_reversal(
                        provider="stripe",
                        payment_reference=charge_obj.get("payment_intent") or payment_id,
                        event_metadata=charge_obj.get("metadata") or {},
                        amount_minor=charge_obj.get("amount_refunded"),
                        currency=charge_obj.get("currency"),
                        event_name=event["type"],
                    )

        # Successfully completed — store completed status with 24h TTL
        if idempotency_key:
            await redis_manager.set(idempotency_key, "completed", ex=86400)

        return {"status": "success"}

    except Exception:
        # On failure or rollback, release processing lock so retry can proceed
        if idempotency_key:
            await redis_manager.delete(idempotency_key)
        raise


# ── POST /api/billing/webhook/razorpay ──────────────────────────────────
@router.post("/webhook/razorpay")
async def razorpay_webhook(
    background_tasks: BackgroundTasks,
    request: Request,
    x_razorpay_signature: str = Header(None),
):
    # WEBHOOK SECURITY: Validate webhook IP before processing
    _validate_webhook_ip(request, "razorpay")
    
    _validate_keys("razorpay")
    webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    if not webhook_secret or webhook_secret == "dummy_webhook_secret":
        raise HTTPException(500, "Razorpay production webhook secret is missing or invalid.")

    raw_body = await request.body()
    
    # WEBHOOK SECURITY: Validate webhook signature
    _validate_webhook_signature(request, "razorpay", raw_body)
    
    # WEBHOOK SECURITY: Validate webhook timestamp
    _validate_webhook_timestamp(request, "razorpay")

    event_id = None
    try:
        payload = json.loads(raw_body)
        event_id = payload.get("event_id") or payload.get("payload", {}).get("payment", {}).get("entity", {}).get("id")
    except Exception as e:
        logger.warning(f"Failed to extract Razorpay event id for idempotency: {e}")

    # BUG-FIX IB-01 / BUG-IB-04: Crash-safe two-phase idempotency.
    idempotency_key = f"webhook:razorpay:{event_id}" if event_id else None
    if idempotency_key:
        cached_val = await redis_manager.get(idempotency_key)
        if cached_val in ("completed", "1", "processing"):
            logger.info(f"Razorpay webhook {event_id} already processed or in-flight, skipping")
            return {"status": "duplicate"}
        acquired = await redis_manager.set(idempotency_key, "processing", nx=True, ex=60)
        if not acquired:
            logger.info(f"Razorpay webhook {event_id} lock collision, skipping")
            return {"status": "duplicate"}

    secret = webhook_secret or "dummy_webhook_secret"
    expected_sig = hmac.new(
        secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, x_razorpay_signature or ""):
        if idempotency_key:
            await redis_manager.delete(idempotency_key)
        logger.warning("Razorpay webhook: invalid signature")
        raise HTTPException(400, "Invalid Razorpay signature")

    try:
        payload = json.loads(raw_body)
    except Exception:
        if idempotency_key:
            await redis_manager.delete(idempotency_key)
        raise HTTPException(400, "Invalid JSON body")
    
    try:
        # Log webhook event for audit trail
        event_type = payload.get("event", "unknown")
        logger.info(f"Razorpay webhook event: {event_type}")

        if payload.get("event") == "payment.captured":
            payment = payload["payload"]["payment"]["entity"]
            notes = payment.get("notes", {})
            user_id = notes.get("user_id")
            item_key = notes.get("item") or notes.get("item_key")  # Support both keys for compatibility

            if not user_id or not item_key:
                logger.error("Razorpay webhook: missing user_id or item in notes")
                raise HTTPException(
                    status_code=400,
                    detail="Missing user_id or item in Razorpay payment notes"
                )

            discount_applied = notes.get("discount_applied") == "true"
            try:
                await _process_razorpay_entitlement(user_id, item_key, discount_applied, _razorpay_settlement_metadata(notes, payment))
                
                payment_id = payment.get("id")
                payment_amount = payment.get("amount", 0) / 100.0  # Convert from paise to INR

                # ── Insert billing_invoices record ──
                try:
                    sb_inv = _background_sb()
                    sb_inv.table("billing_invoices").insert({
                        "user_id": user_id,
                        "provider": "razorpay",
                        "provider_payment_id": payment_id or "",
                        "amount_usd": 0,
                        "amount_inr": round(payment_amount, 2),
                        "currency": "INR",
                        "status": "paid",
                        "plan": item_key,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }).execute()
                except Exception as inv_err:
                    logger.warning(f"[BILLING] Could not insert billing_invoices (Razorpay): {inv_err}")

                # ── Dispatch payment_succeeded notification ──
                try:
                    from backend_app.core.notification_dispatcher import dispatch_user_notification
                    await dispatch_user_notification(
                        user_id=user_id,
                        event_type="payment_succeeded",
                        category="billing",
                        severity="info",
                        title="Payment Successful",
                        message=f"Your payment of ₹{payment_amount:.2f} INR was successful. Plan: {item_key.upper()}.",
                        metadata={"item_key": item_key, "payment_id": payment_id, "provider": "razorpay"},
                    )
                except Exception as notif_err:
                    logger.debug(f"[BILLING] payment_succeeded notification error: {notif_err}")

                if payment_amount > 0:
                    try:
                        sb = _background_sb()
                        sb.rpc("process_referral_commission", {
                            "p_referred_id": user_id,
                            "p_payment_id": payment_id,
                            "p_subscription_tier": item_key,
                            "p_payment_amount_usd": payment_amount  # Note: This is INR, not USD
                        }).execute()
                        logger.info(f"Referral commission processed for Razorpay payment {payment_id}")
                    except Exception as ref_err:
                        logger.error(f"Failed to process referral commission for payment {payment_id}: {ref_err}")
                        # Don't fail the webhook if commission processing fails
            except Exception as e:
                logger.error(f"Razorpay entitlement processing failed: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Entitlement update failed: {e}"
                )

        elif payload.get("event") in ("refund.processed", "refund.failed"):
            # Handle Razorpay refunds - reverse referral commission
            refund = payload.get("payload", {}).get("refund", {}).get("entity", {})
            payment_id = refund.get("payment_id")
            
            if payment_id:
                try:
                    sb = _background_sb()
                    sb.rpc("reverse_referral_commission", {
                        "p_payment_id": payment_id,
                        "p_reversal_reason": payload.get("event")
                    }).execute()
                    logger.info(f"Referral commission reversed for Razorpay payment {payment_id} due to {payload.get('event')}")
                except Exception as ref_err:
                    logger.error(f"Failed to reverse referral commission for payment {payment_id}: {ref_err}")
                    # Don't fail the webhook if commission reversal fails

                # MARKETPLACE SETTLEMENT (task 19.2), Requirements 10.4, 10.8: the ledger half of a
                # refund, on the branch that already exists. ``refund.failed`` shares this branch
                # but is deliberately NOT settled: Razorpay's refund.failed means the refund did
                # NOT go through, so recording a reversal for it would subtract an owner's earning
                # for money that never came back and would move a paid Subscription to REFUNDED.
                # ``refund.processed`` is the event where the money moved.
                if payload.get("event") == "refund.processed":
                    await _settle_marketplace_reversal(
                        provider="razorpay",
                        payment_reference=payment_id,
                        event_metadata=refund.get("notes") or {},
                        amount_minor=refund.get("amount"),
                        currency=refund.get("currency"),
                        event_name=payload.get("event"),
                    )

        # Successfully completed — store completed status with 24h TTL
        if idempotency_key:
            await redis_manager.set(idempotency_key, "completed", ex=86400)

        return {"status": "success"}

    except Exception:
        # On failure or rollback, release processing lock so retry can proceed
        if idempotency_key:
            await redis_manager.delete(idempotency_key)
        raise





@router.post("/payment-methods")
@limiter.limit("10/minute")
async def add_payment_method(
    request: Request,
    body: AddPaymentMethodRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Require real card metadata from the request — never fabricate it.
    # The frontend must pass brand/last4/expiry from the Stripe.js confirmCardSetup
    # response (PaymentMethod object) before calling this endpoint.
    card_brand = getattr(body, "brand", None)
    card_last4 = getattr(body, "last4", None)
    card_expiry_month = getattr(body, "expiry_month", None)
    card_expiry_year = getattr(body, "expiry_year", None)

    missing = [f for f, v in [
        ("brand", card_brand),
        ("last4", card_last4),
        ("expiry_month", card_expiry_month),
        ("expiry_year", card_expiry_year),
    ] if not v]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required card metadata fields: {', '.join(missing)}. "
                   "Supply real card attributes from the Stripe PaymentMethod response."
        )

    if body.set_as_default:
        db.query(PaymentMethodModel).filter(PaymentMethodModel.user_id == user["id"]).update({"is_default": False})

    import uuid
    new_method = PaymentMethodModel(
        id=str(uuid.uuid4()),
        user_id=user["id"],
        payment_method_id=body.payment_method_id,
        brand=card_brand,
        last4=card_last4,
        expiry_month=card_expiry_month,
        expiry_year=card_expiry_year,
        is_default=body.set_as_default,
        created_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    )
    db.add(new_method)
    db.commit()
    db.refresh(new_method)
    return {
        "status": "ok",
        "payment_method": {
            "id": new_method.id,
            "type": "card",
            "brand": new_method.brand,
            "last4": new_method.last4,
            "expiry_month": new_method.expiry_month,
            "expiry_year": new_method.expiry_year,
            "is_default": new_method.is_default
        }
    }


@router.delete("/payment-methods/{method_id}")
async def delete_payment_method(
    method_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    method = db.query(PaymentMethodModel).filter(
        PaymentMethodModel.user_id == user["id"],
        PaymentMethodModel.id == method_id
    ).first()
    if not method:
        raise HTTPException(404, "Payment method not found.")
    
    db.delete(method)
    db.commit()
    return {"status": "ok", "deleted": method_id}


@router.post("/portal")
@limiter.limit("5/minute")
async def create_portal_session(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """Create Stripe Billing Portal session for payment management."""
    stripe_key = _validate_keys("stripe")
    import stripe
    stripe.api_key = stripe_key

    try:
        # Search Stripe customer by email
        customers = stripe.Customer.list(email=user["email"])
        if customers.data:
            customer = customers.data[0]
            if not customer.metadata.get("user_id"):
                stripe.Customer.modify(customer.id, metadata={"user_id": user["id"]})
            customer_id = customer.id
        else:
            customer = stripe.Customer.create(
                email=user["email"],
                metadata={"user_id": user["id"]},
                name=user.get("username", user["email"])
            )
            customer_id = customer.id

        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/app/billing"
        )
        return {"url": session.url}
    except Exception as e:
        logger.error(f"Failed to create Stripe portal session for {user['id']}: {e}")
        raise HTTPException(500, f"Billing portal error: {str(e)}")


# ── POST /api/billing/cancel ─────────────────────────────────────────────────
@router.post("/cancel")
@limiter.limit("5/minute")
async def cancel_subscription(
    request: Request,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """
    Cancel the user's active subscription at period end.
    Subscription remains active until next_billing_date.
    Server-authoritative: updates profiles.cancel_at_period_end and subscription_status.
    """
    user_id = user["id"]
    try:
        from backend_app.core.billing_lifecycle import BillingLifecycle
        sb = _background_sb()
        result = await BillingLifecycle.cancel_subscription(
            user_id=user_id,
            cancel_at_period_end=True,
            supabase=sb,
        )
        await invalidate_profile_cache(user_id)
        # Broadcast via WebSocket
        await RealtimeSync.sync_subscription_change(
            user_id, "subscription_cancelled",
            {"cancel_at_period_end": True, "status": "cancelled"}
        )
        # Dispatch cancellation notification
        try:
            from backend_app.core.notification_dispatcher import dispatch_user_notification
            await dispatch_user_notification(
                user_id=user_id,
                event_type="subscription_cancelled",
                category="billing",
                severity="warning",
                title="Subscription Cancellation Scheduled",
                message="Your subscription will be cancelled at the end of the current billing period. You can resume anytime.",
                metadata={"action_url": "/app/billing"},
            )
        except Exception as notif_err:
            logger.debug(f"[BILLING] cancellation notification error: {notif_err}")
        return {"status": "ok", "detail": result.get("message", "Cancellation scheduled"), "cancel_at_period_end": True}
    except Exception as e:
        logger.error(f"Failed to cancel subscription for user {user_id}: {e}")
        raise HTTPException(500, f"Cancellation failed: {str(e)}")


# ── POST /api/billing/resume ──────────────────────────────────────────────────
@router.post("/resume")
@limiter.limit("5/minute")
async def resume_subscription(
    request: Request,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """
    Reverse a pending cancellation: restore subscription_status to active.
    Server-authoritative: clears cancel_at_period_end and restores active status.
    """
    user_id = user["id"]
    try:
        sb = _background_sb()
        sb.table("profiles").update({
            "cancel_at_period_end": False,
            "subscription_status": "active",
        }).eq("id", user_id).execute()
        await invalidate_profile_cache(user_id)
        # Broadcast via WebSocket
        await RealtimeSync.sync_subscription_change(
            user_id, "cancellation_reversed",
            {"cancel_at_period_end": False, "status": "active"}
        )
        # Dispatch resumption notification
        try:
            from backend_app.core.notification_dispatcher import dispatch_user_notification
            await dispatch_user_notification(
                user_id=user_id,
                event_type="cancellation_reversed",
                category="billing",
                severity="info",
                title="Subscription Resumed",
                message="Your subscription cancellation has been reversed. Your plan will renew as usual.",
                metadata={"action_url": "/app/billing"},
            )
        except Exception as notif_err:
            logger.debug(f"[BILLING] resume notification error: {notif_err}")
        return {"status": "ok", "detail": "Subscription cancellation reversed. Plan is now active.", "cancel_at_period_end": False}
    except Exception as e:
        logger.error(f"Failed to resume subscription for user {user_id}: {e}")
        raise HTTPException(500, f"Resume failed: {str(e)}")
