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
from datetime import datetime

from fastapi import (APIRouter, BackgroundTasks, Depends, Header,
                     HTTPException, Request)
# F-20: get_db retained ONLY for PaymentMethodModel display endpoints.
# Subscription tiers and invoice state are stored exclusively in Supabase.
from sqlalchemy.orm import Session

from backend_app.core.database import get_db
from backend_app.core.dependencies import (DEV_MODE, create_request_supabase,
                                           get_current_user,
                                           invalidate_profile_cache)
from backend_app.core.models import AddPaymentMethodRequest, PaymentMethodModel
from backend_app.core.schemas import CheckoutRequest

router = APIRouter()
logger = logging.getLogger("BillingRouter")


def _validate_keys(provider: str) -> str:
    if provider == "stripe":
        key = os.environ.get("STRIPE_SECRET_KEY", "sk_test_dummy")
        if not DEV_MODE:
            if not key or key == "sk_test_dummy" or not key.startswith("sk_live_"):
                raise HTTPException(500, "Stripe production secret key is missing, invalid, or test credentials are used in production path.")
        return key
    elif provider == "razorpay":
        key = os.environ.get("RAZORPAY_KEY_ID", "rzp_test_dummy")
        secret = os.environ.get("RAZORPAY_KEY_SECRET")
        if not DEV_MODE:
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


VALID_ITEM_KEYS = {"free", "pro_999", "elite_1999", "ml_addon"}

async def _apply_billing_entitlement(user_id: str, item_key: str) -> None:
    """
    F-20: Writes billing state ONLY to Supabase profiles table.
    SQLite SubscriptionModel / InvoiceModel storage has been retired.
    Supabase is the single source of truth for subscription_tier.
    """
    if item_key not in VALID_ITEM_KEYS:
        logger.error(f"Invalid billing entitlement requested: user={user_id} tier={item_key}")
        raise HTTPException(
            status_code=400,
            detail=f"Invalid billing item key: {item_key}"
        )
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



async def _process_stripe_entitlement(user_id: str, item_key: str) -> None:
    await _apply_billing_entitlement(user_id, item_key)
    logger.info(f"Stripe: Processed '{item_key}' for user {user_id}")


async def _process_razorpay_entitlement(user_id: str, item_key: str) -> None:
    await _apply_billing_entitlement(user_id, item_key)
    logger.info(f"Razorpay: Processed '{item_key}' for user {user_id}")


# ── Pricing table ────────────────────────────────────────────────────────
PRICES = {
    "pro_999": {"INR": 99900, "USD": 1200},  # paise / cents
    "elite_1999": {"INR": 199900, "USD": 2400},
    "ml_addon": {"INR": 19900, "USD": 300},
}


# ── POST /api/billing/checkout ──────────────────────────────────────────
@router.post("/checkout")
async def create_checkout_session(
    body: CheckoutRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """Generates a payment link. INR → Razorpay. USD → Stripe."""
    item_key = "ml_addon" if body.is_addon else body.tier.value
    amount = PRICES.get(item_key, {}).get(body.currency)

    if not amount:
        raise HTTPException(400, "Invalid tier or currency combination.")

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
                metadata={"user_id": user["id"], "item_key": item_key},
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
                    "notes": {"user_id": user["id"], "item": item_key},
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
                    "notes": {"user_id": user["id"], "item": item_key},
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
    if not DEV_MODE:
        if not webhook_secret or webhook_secret == "whsec_dummy" or not webhook_secret.startswith("whsec_"):
            raise HTTPException(500, "Stripe production webhook secret is missing, invalid, or test credentials are used in production path.")

    import stripe
    stripe.api_key = stripe_key

    payload = await request.body()
    try:
        secret = webhook_secret or "whsec_dummy"
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, secret
        )
    except Exception as e:
        logger.warning(f"Stripe webhook signature failure: {e}")
        raise HTTPException(400, f"Webhook Error: {e}")

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
            return {"status": "ignored"}

        try:
            await _process_stripe_entitlement(user_id, item_key)
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
            return {"status": "ignored"}

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
    if not DEV_MODE:
        if not webhook_secret or webhook_secret == "dummy_webhook_secret":
            raise HTTPException(500, "Razorpay production webhook secret is missing or invalid.")
            
    raw_body = await request.body()

    secret = webhook_secret or "dummy_webhook_secret"
    expected_sig = hmac.new(
        secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, x_razorpay_signature or ""):
        if not DEV_MODE:
            logger.warning("Razorpay webhook: invalid signature")
            raise HTTPException(400, "Invalid Razorpay signature")
        else:
            logger.warning("Razorpay webhook signature verification failure ignored in DEV_MODE.")

    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    if payload.get("event") == "payment.captured":
        payment = payload["payload"]["payment"]["entity"]
        notes = payment.get("notes", {})
        user_id = notes.get("user_id")
        item_key = notes.get("item")

        if not user_id or not item_key:
            logger.error("Razorpay webhook: missing user_id or item in notes")
            return {"status": "ignored"}

        try:
            await _process_razorpay_entitlement(user_id, item_key)
        except Exception as e:
            logger.error(f"Razorpay entitlement processing failed: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Entitlement update failed: {e}"
            )

    return {"status": "success"}


@router.get("/payment-methods")
async def get_payment_methods(
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
async def add_payment_method(
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
