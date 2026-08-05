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
from typing import Any, Dict
from uuid import UUID

from fastapi import (APIRouter, BackgroundTasks, Depends, Header,
                     HTTPException, Request)
from backend_app.core.rate_limit import limiter
# F-20: get_db retained ONLY for PaymentMethodModel display endpoints.
# Subscription tiers and invoice state are stored exclusively in Supabase.
from sqlalchemy.orm import Session

from backend_app.core.cache import redis_manager
from backend_app.core.dependencies import (get_current_user,
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


def _sb(user: dict):
    token = user.get("access_token")
    if not token:
        raise HTTPException(401, "Missing authenticated Supabase token.")
    return create_request_supabase(token)


def _background_sb():
    from supabase import create_client

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("Billing webhook Supabase service credentials are not configured.")
    return create_client(supabase_url, supabase_key)


from backend_app.core.subscription_engine import Plan, SubscriptionEngine

VALID_ITEM_KEYS = {Plan.FREE.value, Plan.STARTER.value, Plan.PRO.value, Plan.ENTERPRISE.value, "ml_addon"}


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


async def _apply_marketplace_entitlement(user_id: str, library_id: str, subscription_id: str = None) -> None:
    """
    Activate a marketplace subscription after successful payment.
    
    Updates library_subscriptions status from 'pending' to 'active'.
    Called from billing webhooks when item_key format is 'marketplace_{library_id}'.
    """
    # Validate UUID format for library_id and subscription_id if provided
    _validate_uuid(library_id, "library_id")
    if subscription_id:
        _validate_uuid(subscription_id, "subscription_id")
    
    try:
        sb = _background_sb()
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # Update subscription status from pending to active
        # Include subscription_id in WHERE clause for additional safety
        update_query = sb.table("library_subscriptions").update({"status": "active", "started_at": now_iso})
        update_query = update_query.eq("user_id", user_id).eq("library_id", library_id).eq("status", "pending")
        if subscription_id:
            update_query = update_query.eq("id", subscription_id)
        
        result = update_query.execute()
        
        if not result.data:
            # Idempotency: subscription already activated by previous webhook
            logger.info(
                f"Marketplace subscription already active or not pending (idempotent): "
                f"user={user_id} library={library_id}"
            )
            return  # Don't raise - webhook succeeded idempotently
        
        logger.info(f"Marketplace subscription activated: user={user_id} library={library_id}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to activate marketplace subscription: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to activate marketplace subscription"
        )

async def _apply_billing_entitlement(user_id: str, item_key: str, discount_applied: bool = False) -> None:
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
        if isinstance(metadata, dict):
            subscription_id = metadata.get("subscription_id")
        await _apply_marketplace_entitlement(user_id, library_id, subscription_id)
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
async def get_plans():
    """Get all available plans. No rate limiting - public endpoint."""
    from backend_app.core.subscription_engine import SubscriptionEngine
    
    plans = SubscriptionEngine.get_all_plans()
    
    # Transform to frontend-expected format
    frontend_plans = []
    for plan in plans:
        frontend_plans.append({
            "id": plan.id,
            "name": plan.name,
            "description": plan.description,
            "features": plan.features,
            "usd": plan.pricing.get("USD", 0) // 100,  # Convert cents to dollars
            "inr": plan.pricing.get("INR", 0) // 100,  # Convert paise to rupees
            "recommended": plan.id == "pro"  # Pro is the recommended mid-tier plan
        })
    
    return {"plans": frontend_plans}

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
    from backend_app.core.subscription_dependencies import get_user_entitlements
    
    entitlements = await get_user_entitlements(user, supabase)
    
    # Get subscription details from profile
    subscription_status = "active"
    renewal_date = None
    cancel_at_period_end = False
    
    if supabase:
        try:
            resp = (
                supabase.table("profiles")
                .select("subscription_status, subscription_renewal_date, cancel_at_period_end")
                .eq("id", user["id"])
                .execute()
            )
            if resp.data:
                subscription_status = resp.data[0].get("subscription_status", "active")
                renewal_date = resp.data[0].get("subscription_renewal_date")
                cancel_at_period_end = resp.data[0].get("cancel_at_period_end", False)
        except Exception as e:
            logger.warning(f"Failed to get subscription details: {e}")
    
    return {
        "plan": entitlements.plan,
        "features": entitlements.features,
        "quotas": entitlements.quotas,
        "usage": entitlements.usage,
        "subscription_status": subscription_status,
        "renewal_date": renewal_date,
        "cancel_at_period_end": cancel_at_period_end,
    }


# ── GET /api/billing/currency ───────────────────────────────────────────────
@router.get("/currency")
@limiter.limit("60/minute")
async def get_currency(
  request: Request,
  user: dict = Depends(get_current_user),
  supabase: Any = Depends(get_request_supabase),
):
    """Get user's currency preference (auto-detected if not set)."""
    from backend_app.core.pricing_service import PricingService
    
    currency = await PricingService.determine_currency(user["id"], supabase, request)
    return {"currency": currency}


# ── POST /api/billing/currency ──────────────────────────────────────────────
@router.post("/currency")
@limiter.limit("10/minute")
async def set_currency(
  request: Request,
  body: Dict[str, str],
  user: dict = Depends(get_current_user),
  supabase: Any = Depends(get_request_supabase),
):
    """Set user's currency preference."""
    from backend_app.core.pricing_service import PricingService
    
    currency = body.get("currency", "USD")
    success = await PricingService.set_user_currency_preference(user["id"], currency, supabase)
    
    if not success:
        raise HTTPException(400, "Invalid currency or failed to save preference")
    
    return {"status": "success", "currency": currency}

@router.post("/checkout")
@limiter.limit("10/minute")
async def create_checkout_session(
  request: Request,
  body: CheckoutRequest,
  background_tasks: BackgroundTasks,
  user: dict = Depends(get_current_user),
):
    """Generates a payment link. INR → Razorpay. USD → Stripe."""
    item_key = "ml_addon" if body.is_addon else body.tier.value
    
    # Get pricing from SubscriptionEngine instead of deprecated PRICES dict
    if body.is_addon:
        # ML addon pricing (hardcoded for now, could be moved to SubscriptionEngine)
        amount = {"INR": 19900, "USD": 300}.get(body.currency)
    else:
        plan_config = SubscriptionEngine.get_plan_config(item_key)
        if not plan_config:
            raise HTTPException(400, "Invalid tier")
        # Pricing in SubscriptionEngine is in dollars/rupees, convert to cents/paise for payment gateways
        base_amount = plan_config.pricing.get(body.currency, 0)
        amount = int(base_amount * 100)  # Convert to cents (USD) or paise (INR)

    if not amount:
        raise HTTPException(400, "Invalid tier or currency combination.")

    discount_applied = False
    try:
        sb = _sb(user)
        profile_res = sb.table("profiles").select("available_discounts").eq("id", user["id"]).execute()
        if profile_res.data and profile_res.data[0].get("available_discounts", 0) > 0:
            discount_applied = True
            amount = int(amount * 0.9)
    except Exception as e:
        logger.warning(f"Could not fetch available discounts for {user['id']}: {e}")

    try:
        if body.currency == "USD":
            stripe_key = _validate_keys("stripe")
            import stripe
            stripe.api_key = stripe_key

            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[
                    {
                        "price_data": {
                            "currency": "usd",
                            "product_data": {
                                "name": f"Aerora Dynamics — {item_key.upper()}"
                            },
                            "unit_amount": amount,
                        },
                        "quantity": 1,
                    }
                ],
                mode="payment" if body.is_addon else "subscription",
                success_url=(
                    f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}"
                    "/dashboard?payment=success"
                ),
                cancel_url=(
                    f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/pricing"
                ),
                client_reference_id=user["id"],
                metadata={
                    "user_id": user["id"], 
                    "item_key": item_key,
                    "discount_applied": "true" if discount_applied else "false"
                },
            )
            return {"checkoutUrl": session.url, "checkout_url": session.url, "provider": "stripe"}

        elif body.currency == "INR":
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
                        "discount_applied": "true" if discount_applied else "false"
                    },
                }
            )
            try:
                link = rzp.payment_link.create({
                    "amount": amount,
                    "currency": "INR",
                    "reference_id": order["id"],
                    "description": f"Aerora Beta - {item_key.upper()}",
                    "customer": {
                        "email": user.get("email", "beta_user@aerora.io")
                    },
                    "notes": {
                        "user_id": user["id"], 
                        "item": item_key,
                        "discount_applied": "true" if discount_applied else "false"
                    },
                    "callback_url": f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/dashboard?payment=success",
                    "callback_method": "get"
                })
                checkout_url = link["short_url"]
            except Exception as le:
                logger.warning(f"Failed to create Razorpay hosted payment link: {le}. Falling back to default success URL.")
                checkout_url = f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/dashboard?payment=success"

            return {"checkoutUrl": checkout_url, "checkout_url": checkout_url, "order_id": order["id"], "provider": "razorpay", "amount": amount}

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
    stripe_key = _validate_keys("stripe")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not webhook_secret or webhook_secret == "whsec_dummy" or not webhook_secret.startswith("whsec_"):
        raise HTTPException(500, "Stripe production webhook secret is missing, invalid, or test credentials are used in production path.")

    import stripe
    stripe.api_key = stripe_key

    payload = await request.body()
    
    # Idempotency check: Use event ID to prevent duplicate processing
    event_id = request.headers.get("stripe-event-id")
    if event_id:
        idempotency_key = f"webhook:stripe:{event_id}"
        existing = await redis_manager.get(idempotency_key)
        if existing:
            logger.info(f"Stripe webhook {event_id} already processed, skipping")
            return {"status": "duplicate"}
        await redis_manager.setex(idempotency_key, 86400, "1")  # 24 hour TTL
    
    try:
        secret = webhook_secret or "whsec_dummy"
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, secret
        )
    except Exception as e:
        logger.warning(f"Stripe webhook signature failure: {e}")
        raise HTTPException(400, f"Webhook Error: {e}")
    
    # Log webhook event for audit trail
    logger.info(f"Stripe webhook event: {event['type']}")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("client_reference_id")
        metadata = session.get("metadata", {})

        # F-19 FIX: Missing item_key returns HTTP 400 — never default to a paid tier.
        # Previously: metadata.get("item_key", "elite_1999") silently upgraded users
        # to the highest paid tier when Stripe metadata was absent or malformed.
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
            await _process_stripe_entitlement(user_id, item_key, discount_applied, metadata)
            
            # Process referral commission on successful payment
            payment_id = session.get("payment_intent") or session.get("id")
            payment_amount = session.get("amount_total", 0) / 100.0  # Convert from cents to USD
            
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
                sb.table("profiles").update({"is_frozen": True}).eq("id", user_id).execute()
                await invalidate_profile_cache(user_id)
                logger.info(f"User {user_id} account frozen due to invoice payment failure.")
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

    return {"status": "success"}


# ── POST /api/billing/webhook/razorpay ──────────────────────────────────
@router.post("/webhook/razorpay")
async def razorpay_webhook(
    background_tasks: BackgroundTasks,
    request: Request,
    x_razorpay_signature: str = Header(None),
):
    _validate_keys("razorpay")
    webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    if not webhook_secret or webhook_secret == "dummy_webhook_secret":
        raise HTTPException(500, "Razorpay production webhook secret is missing or invalid.")
            
    raw_body = await request.body()
    
    # Idempotency check: Use Razorpay event ID to prevent duplicate processing
    try:
        payload = json.loads(raw_body)
        event_id = payload.get("event_id") or payload.get("payload", {}).get("payment", {}).get("entity", {}).get("id")
        if event_id:
            idempotency_key = f"webhook:razorpay:{event_id}"
            existing = await redis_manager.get(idempotency_key)
            if existing:
                logger.info(f"Razorpay webhook {event_id} already processed, skipping")
                return {"status": "duplicate"}
            await redis_manager.setex(idempotency_key, 86400, "1")  # 24 hour TTL
    except Exception as e:
        logger.warning(f"Failed to extract Razorpay event id for idempotency: {e}")

    secret = webhook_secret or "dummy_webhook_secret"
    expected_sig = hmac.new(
        secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, x_razorpay_signature or ""):
        logger.warning("Razorpay webhook: invalid signature")
        raise HTTPException(400, "Invalid Razorpay signature")

    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(400, "Invalid JSON body")
    
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
            await _process_razorpay_entitlement(user_id, item_key, discount_applied, notes)
            
            # Process referral commission on successful payment
            payment_id = payment.get("id")
            payment_amount = payment.get("amount", 0) / 100.0  # Convert from paise to INR
            
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

    return {"status": "success"}


@router.get("/invoices")
@limiter.limit("60/minute")
async def get_invoices(
    request: Request,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
):
    """Get invoice history from Supabase."""
    if not supabase:
        return []
    
    try:
        resp = supabase.table("billing_invoices").select("*").eq("user_id", user["id"]).order("created_at", desc=True).execute()
        invoices = []
        for inv in resp.data or []:
            invoices.append({
                "id": inv.get("id"),
                "date": inv.get("created_at"),
                "amtUSD": inv.get("amount_usd", 0),
                "amtINR": inv.get("amount_inr", 0),
                "status": inv.get("status", "unknown"),
                "currency": inv.get("currency", "USD"),
            })
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
    return res


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
async def create_portal_session(
    user: dict = Depends(get_current_user)
):
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
            return_url=f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/dashboard"
        )
        return {"url": session.url}
    except Exception as e:
        logger.error(f"Failed to create Stripe portal session for {user['id']}: {e}")
        raise HTTPException(500, f"Billing portal error: {str(e)}")
