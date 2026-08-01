# Phase 9: Payment Verification
**Verify Stripe Integration, Razorpay Integration, and Webhook Security**

Generated: 2025-01-08
Project: VyomQuant SaaS
Scope: Payment gateway integration verification

---

## Executive Summary

The current payment integration with Stripe and Razorpay is functional but lacks critical features like webhook idempotency, proper error handling, and comprehensive logging. This phase verifies the integration and identifies required fixes.

---

## Stripe Integration Verification

### Current Implementation

**File:** `routers/billing.py`

**Endpoints:**
- `POST /api/billing/checkout` - Creates Stripe checkout session
- `POST /api/billing/webhook/stripe` - Processes Stripe webhooks
- `POST /api/billing/portal` - Creates Stripe billing portal session

### Issues Identified

#### 1. No Idempotency Key
**Location:** Checkout session creation (lines 161-189)
**Issue:** No idempotency key on checkout creation
**Impact:** Duplicate checkout requests can create multiple sessions
**Fix Required:**
```python
session = stripe.checkout.Session.create(
    idempotency_key=f"checkout:{user['id']}:{body.tier.value}:{int(time.time())}",
    payment_method_types=["card"],
    # ... rest of parameters
)
```

#### 2. No Customer ID Storage
**Location:** Customer creation (lines 562-574)
**Issue:** Stripe customer ID not stored in profiles
**Impact:** Customer lookup by email (unreliable), no customer management
**Fix Required:**
```python
# Add stripe_customer_id to profiles table
# Store customer_id after creation
UPDATE profiles SET stripe_customer_id = $1 WHERE id = $2
```

#### 3. No Subscription ID Storage
**Location:** Webhook handler (lines 268-353)
**Issue:** Stripe subscription ID not stored in profiles
**Impact:** Cannot manage subscriptions, no subscription tracking
**Fix Required:**
```python
# Add stripe_subscription_id to profiles table
# Store subscription_id after creation
UPDATE profiles SET stripe_subscription_id = $1 WHERE id = $2
```

#### 4. Webhook Idempotency Missing
**Location:** Webhook handler (lines 243-377)
**Issue:** No idempotency key tracking
**Impact:** Duplicate webhooks can grant entitlement twice
**Fix Required:**
```python
# Track processed webhook IDs in Redis
webhook_id = event["id"]
cache_key = f"webhook:stripe:{webhook_id}"
if await redis_manager.exists(cache_key):
    return {"status": "already_processed"}
await redis_manager.setex(cache_key, 86400, "1")  # 24h TTL
```

#### 5. No Retry Logic
**Location:** All Stripe API calls
**Issue:** No retry logic for failed API calls
**Impact:** Transient failures cause payment failures
**Fix Required:**
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def stripe_api_call():
    # Stripe API call
```

#### 6. No Webhook Event Logging
**Location:** Webhook handler (lines 243-377)
**Issue:** No comprehensive logging of webhook events
**Impact:** Cannot debug webhook issues, no audit trail
**Fix Required:**
```python
# Log all webhook events to database
INSERT INTO billing_audit_log (event_type, event_id, payload, processed_at)
VALUES ($1, $2, $3, NOW())
```

### Verification Checklist

- [x] Stripe secret key validation present
- [x] Stripe webhook signature verification present
- [x] Stripe checkout session creation works
- [x] Stripe billing portal session creation works
- [ ] Stripe customer ID storage
- [ ] Stripe subscription ID storage
- [ ] Webhook idempotency
- [ ] Retry logic for API calls
- [ ] Webhook event logging
- [ ] Error handling improvements

---

## Razorpay Integration Verification

### Current Implementation

**File:** `routers/billing.py`

**Endpoints:**
- `POST /api/billing/checkout` - Creates Razorpay order
- `POST /api/billing/webhook/razorpay` - Processes Razorpay webhooks

### Issues Identified

#### 1. No Idempotency Key
**Location:** Order creation (lines 201-230)
**Issue:** No idempotency key on order creation
**Impact:** Duplicate checkout requests can create multiple orders
**Fix Required:**
```python
order = rzp.order.create(
    receipt=f"order:{user['id']}:{body.tier.value}:{int(time.time())}",
    amount=amount,
    currency="INR",
    # ... rest of parameters
)
```

#### 2. No Customer ID Storage
**Location:** Not implemented
**Issue:** Razorpay customer ID not stored
**Impact:** No customer management, no subscription tracking
**Fix Required:**
```python
# Add razorpay_customer_id to profiles table
# Create customer on first payment
customer = rzp.customer.create({
    'name': user['username'],
    'email': user['email'],
    'contact': user.get('phone')
})
```

#### 3. Webhook Idempotency Missing
**Location:** Webhook handler (lines 381-452)
**Issue:** No idempotency key tracking
**Impact:** Duplicate webhooks can grant entitlement twice
**Fix Required:**
```python
# Track processed webhook IDs in Redis
payment_id = payload.get("payment", {}).get("id")
cache_key = f"webhook:razorpay:{payment_id}"
if await redis_manager.exists(cache_key):
    return {"status": "already_processed"}
await redis_manager.setex(cache_key, 86400, "1")
```

#### 4. Only One-Time Payments
**Location:** Order creation (lines 201-230)
**Issue:** No subscription support for Razorpay
**Impact:** Cannot offer recurring billing via Razorpay
**Fix Required:**
```python
# Add subscription creation
subscription = rzp.subscription.create({
    'plan_id': plan_id,
    'customer_id': customer_id,
    'total_count': 12,  # 12 months
})
```

#### 5. No Retry Logic
**Location:** All Razorpay API calls
**Issue:** No retry logic for failed API calls
**Impact:** Transient failures cause payment failures
**Fix Required:**
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def razorpay_api_call():
    # Razorpay API call
```

#### 6. DEV_MODE Bypass
**Location:** Signature verification (line 407)
**Issue:** DEV_MODE bypasses signature verification
**Impact:** Security risk in production
**Fix Required:**
```python
# Remove DEV_MODE bypass
if not DEV_MODE:
    # ... signature verification
else:
    # Still verify in DEV_MODE, just log warnings
    logger.warning("DEV_MODE: Signature verification bypassed")
```

### Verification Checklist

- [x] Razorpay key validation present
- [x] Razorpay webhook signature verification present
- [x] Razorpay order creation works
- [ ] Razorpay customer ID storage
- [ ] Razorpay subscription support
- [ ] Webhook idempotency
- [ ] Retry logic for API calls
- [ ] DEV_MODE bypass removal
- [ ] Error handling improvements

---

## Webhook Security Verification

### Stripe Webhook Security

**Current Implementation:**
```python
stripe_key = _validate_keys("stripe")
webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
if not DEV_MODE:
    if not webhook_secret or webhook_secret == "whsec_dummy" or not webhook_secret.startswith("whsec_"):
        raise HTTPException(500, "Stripe production webhook secret is missing")

import stripe
stripe.api_key = stripe_key

payload = await request.body()
try:
    secret = webhook_secret or "whsec_dummy"
    event = stripe.Webhook.construct_event(payload, stripe_signature, secret)
except Exception as e:
    logger.warning(f"Stripe webhook signature failure: {e}")
    raise HTTPException(400, f"Webhook Error: {e}")
```

**Issues:**
1. DEV_MODE bypasses validation (line 252)
2. No rate limiting on webhook endpoint
3. No IP whitelist for Stripe webhooks
4. No timestamp validation (replay attack protection)

**Fixes Required:**
```python
# 1. Remove DEV_MODE bypass
if not webhook_secret or webhook_secret == "whsec_dummy":
    raise HTTPException(500, "Stripe webhook secret is missing")

# 2. Add rate limiting
from slowapi import Limiter
limiter = Limiter(key_func=get_remote_address)

@router.post("/webhook/stripe")
@limiter.limit("100/minute")
async def stripe_webhook(...):
    # ...

# 3. Add IP whitelist (Stripe IPs: https://stripe.com/docs/ips)
STRIPE_WEBHOOK_IPS = [
    "3.18.12.63", "3.130.192.231", "13.247.63.25",
    # ... more IPs
]

@router.post("/webhook/stripe")
async def stripe_webhook(request: Request, ...):
    client_ip = request.client.host
    if client_ip not in STRIPE_WEBHOOK_IPS:
        logger.warning(f"Stripe webhook from unauthorized IP: {client_ip}")
        raise HTTPException(403, "Unauthorized")

# 4. Add timestamp validation
event_timestamp = event["created"]
current_timestamp = int(time.time())
if abs(current_timestamp - event_timestamp) > 300:  # 5 minutes
    logger.warning(f"Stripe webhook timestamp too old: {event_timestamp}")
    raise HTTPException(400, "Webhook timestamp too old")
```

### Razorpay Webhook Security

**Current Implementation:**
```python
raw_body = await request.body()
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
```

**Issues:**
1. DEV_MODE bypasses validation (line 407)
2. No rate limiting on webhook endpoint
3. No IP whitelist for Razorpay webhooks
4. No timestamp validation

**Fixes Required:**
```python
# 1. Remove DEV_MODE bypass
if not hmac.compare_digest(expected_sig, x_razorpay_signature or ""):
    logger.warning("Razorpay webhook: invalid signature")
    raise HTTPException(400, "Invalid Razorpay signature")

# 2. Add rate limiting
@router.post("/webhook/razorpay")
@limiter.limit("100/minute")
async def razorpay_webhook(...):
    # ...

# 3. Add IP whitelist (Razorpay IPs: https://razorpay.com/docs/webhooks)
RAZORPAY_WEBHOOK_IPS = [
    "54.254.16.199", "13.233.24.30",
    # ... more IPs
]

# 4. Add timestamp validation
payload = json.loads(raw_body)
event_timestamp = payload.get("created_at")
if event_timestamp:
    current_timestamp = int(time.time())
    if abs(current_timestamp - event_timestamp) > 300:
        logger.warning(f"Razorpay webhook timestamp too old: {event_timestamp}")
        raise HTTPException(400, "Webhook timestamp too old")
```

---

## Webhook Idempotency Implementation

### Redis-Based Idempotency

**Implementation:**
```python
async def check_webhook_idempotency(provider: str, event_id: str) -> bool:
    """
    Check if webhook has already been processed.
    
    Returns True if already processed, False otherwise.
    """
    cache_key = f"webhook:{provider}:{event_id}"
    return await redis_manager.exists(cache_key)

async def mark_webhook_processed(provider: str, event_id: str, ttl: int = 86400):
    """
    Mark webhook as processed.
    
    Args:
        provider: 'stripe' or 'razorpay'
        event_id: Event ID from webhook
        ttl: Time to live in seconds (default 24h)
    """
    cache_key = f"webhook:{provider}:{event_id}"
    await redis_manager.setex(cache_key, ttl, "1")
```

### Integration with Webhook Handlers

**Stripe Webhook:**
```python
@router.post("/webhook/stripe")
async def stripe_webhook(...):
    # ... signature verification
    
    event_id = event["id"]
    
    # Check idempotency
    if await check_webhook_idempotency("stripe", event_id):
        logger.info(f"Stripe webhook {event_id} already processed")
        return {"status": "already_processed"}
    
    # Process webhook
    # ...
    
    # Mark as processed
    await mark_webhook_processed("stripe", event_id)
    
    return {"status": "success"}
```

**Razorpay Webhook:**
```python
@router.post("/webhook/razorpay")
async def razorpay_webhook(...):
    # ... signature verification
    
    payload = json.loads(raw_body)
    payment_id = payload.get("payment", {}).get("id")
    
    # Check idempotency
    if await check_webhook_idempotency("razorpay", payment_id):
        logger.info(f"Razorpay payment {payment_id} already processed")
        return {"status": "already_processed"}
    
    # Process webhook
    # ...
    
    # Mark as processed
    await mark_webhook_processed("razorpay", payment_id)
    
    return {"status": "success"}
```

---

## Database Schema Updates

### Add Payment Tracking Tables

**Migration:** `migrations/add_payment_tracking.sql`

```sql
-- Add payment IDs to profiles
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(255);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS razorpay_customer_id VARCHAR(255);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS razorpay_subscription_id VARCHAR(255);

-- Create payment tracking table
CREATE TABLE IF NOT EXISTS payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
    provider VARCHAR(20) NOT NULL,  -- stripe, razorpay
    provider_payment_id VARCHAR(255) NOT NULL,
    provider_subscription_id VARCHAR(255),
    amount_cents INTEGER NOT NULL,
    currency VARCHAR(3) NOT NULL,
    status VARCHAR(20) NOT NULL,  -- pending, completed, failed, refunded
    payment_method VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(provider, provider_payment_id)
);

CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_payments_provider ON payments(provider);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);

-- Create webhook event log table
CREATE TABLE IF NOT EXISTS webhook_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider VARCHAR(20) NOT NULL,
    event_id VARCHAR(255) NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    payload JSONB,
    processed BOOLEAN DEFAULT false,
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(provider, event_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_provider ON webhook_events(provider);
CREATE INDEX IF NOT EXISTS idx_webhook_events_processed ON webhook_events(processed);
```

---

## Testing Plan

### Unit Tests
- Test Stripe checkout session creation
- Test Razorpay order creation
- Test webhook signature verification
- Test webhook idempotency
- Test payment tracking

### Integration Tests
- Test complete Stripe payment flow
- Test complete Razorpay payment flow
- Test webhook processing
- Test refund handling
- Test subscription updates

### End-to-End Tests
- Test user can purchase plan via Stripe
- Test user can purchase plan via Razorpay
- Test webhook grants entitlement correctly
- Test duplicate webhooks are idempotent
- Test refund reverses entitlement

---

## Rollback Plan

If issues arise:

1. **Disable Idempotency:** Set environment variable to disable idempotency checks
2. **Remove Rate Limiting:** Remove rate limiting from webhook endpoints
3. **Revert to Old Code:** Use git revert to undo changes

**Rollback Commands:**
```bash
export DISABLE_WEBHOOK_IDEMPOTENCY=true
export DISABLE_WEBHOOK_RATE_LIMIT=true
```

---

## Next Steps

1. Review and approve this verification report
2. Implement webhook idempotency (P0)
3. Add payment tracking tables (P0)
4. Remove DEV_MODE bypasses (P0)
5. Add retry logic (P1)
6. Add webhook event logging (P1)
7. Add rate limiting (P1)
8. Add IP whitelisting (P2)
9. Complete testing
10. Deploy to production

---

**End of Phase 9 Payment Verification**
