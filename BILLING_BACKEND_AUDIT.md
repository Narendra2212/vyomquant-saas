# Billing System Backend Audit
**Phase 4: Backend Audit - Inspect Billing Services**

Generated: 2025-01-08
Project: VyomQuant SaaS
Scope: Billing & Subscription System Backend Services

---

## Executive Summary

The backend billing services are consolidated in a single router (`billing.py`) with Stripe and Razorpay integration. The code has undergone several security fixes but still lacks idempotency, proper error handling, and comprehensive logging. Payment gateway SDKs are lazily initialized, and webhook handlers have signature verification but no idempotency keys.

---

## 1. Billing Router Overview

**File:** `backend_app/routers/billing.py` (584 lines)

**Purpose:** Main billing router for subscription management, payment processing, and webhook handling

**Security Fixes Documented:**
- F-09: DEV_MODE checkout no longer grants paid tiers
- F-19: Stripe webhook returns HTTP 400 if item_key is absent
- F-20: Entitlement writes only to Supabase (SQLite retired)
- N1/N11: Stripe webhook reads actual tier from metadata
- N2: Razorpay webhook reads raw body first for signature verification
- N10: Payment SDKs initialized lazily
- N4: Cache invalidated after successful payment

---

## 2. Endpoints

### 2.1 POST /api/billing/checkout
**Lines:** 132-239

**Purpose:** Create checkout session for Stripe (USD) or Razorpay (INR)

**Request Body:**
```python
class CheckoutRequest(BaseModel):
    tier: SubscriptionTier  # free, pro_999, elite_1999
    currency: str  # USD or INR
    is_addon: bool = False  # For ML addon purchase
```

**Logic:**
1. Validate tier and currency combination against PRICES dict
2. Check `available_discounts` in profiles
3. Apply 10% discount if available
4. Create Stripe session (USD) or Razorpay order (INR)
5. Return checkout URL

**Pricing Table (Lines 124-128):**
```python
PRICES = {
    "pro_999": {"INR": 99900, "USD": 1200},  # paise / cents
    "elite_1999": {"INR": 199900, "USD": 2400},
    "ml_addon": {"INR": 19900, "USD": 300},
}
```

**Issues:**
- No idempotency key on checkout creation
- No validation that user can purchase (not frozen, not already on higher tier)
- Discount applied without tracking which discount used
- No audit trail of checkout initiation
- No validation of payment amount against expected amount
- No retry logic for failed checkout creation

**Security Concerns:**
- Discount check and application not atomic (race condition possible)
- No rate limiting on checkout endpoint
- No validation of user eligibility before checkout

### 2.2 POST /api/billing/webhook/stripe
**Lines:** 243-377

**Purpose:** Handle Stripe webhook events

**Events Handled:**
- `checkout.session.completed` - Payment successful
- `customer.subscription.deleted` - Subscription cancelled
- `customer.subscription.updated` - Subscription updated
- `invoice.payment_failed` - Payment failed

**Logic:**
1. Validate Stripe secret key
2. Verify webhook signature
3. Parse event type
4. Extract user_id and item_key from metadata
5. Process entitlement via `_process_stripe_entitlement()`
6. Process referral commission via RPC
7. Handle subscription deletion/update
8. Freeze account on payment failure

**Issues:**
- No idempotency key on webhook processing
- Duplicate webhooks could grant entitlement twice
- No retry logic for failed webhooks
- No webhook event logging
- No validation that payment amount matches expected amount
- Commission processing failure doesn't fail webhook (silent failure)

**Security Concerns:**
- Signature verification present (good)
- No additional validation of payment amount
- No validation of user eligibility
- No rate limiting on webhook endpoint

**Critical Fix (F-19):**
```python
# Previously: metadata.get("item_key", "elite_1999") silently upgraded users
# Now: Returns HTTP 400 if item_key missing
item_key = metadata.get("item_key")
if not item_key:
    raise HTTPException(400, "Missing item_key in Stripe session metadata")
```

### 2.3 POST /api/billing/webhook/razorpay
**Lines:** 381-452

**Purpose:** Handle Razorpay webhook events

**Events Handled:**
- `payment.captured` - Payment successful

**Logic:**
1. Validate Razorpay keys
2. Verify webhook signature (HMAC-SHA256)
3. Parse JSON payload
4. Extract user_id and item_key from notes
5. Process entitlement via `_process_razorpay_entitlement()`
6. Process referral commission via RPC

**Issues:**
- No idempotency key on webhook processing
- Only handles `payment.captured` event (no cancellation/update)
- No retry logic for failed webhooks
- No webhook event logging
- Commission processing failure doesn't fail webhook
- Payment amount passed as USD but is actually INR (line 439 comment)

**Security Concerns:**
- Signature verification present (good)
- DEV_MODE bypasses signature verification (line 407)
- No additional validation of payment amount
- No validation of user eligibility
- No rate limiting on webhook endpoint

**Critical Fix (N2):**
```python
# Reads raw body FIRST for signature verification
raw_body = await request.body()
expected_sig = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
```

### 2.4 GET /api/billing/payment-methods
**Lines:** 455-472

**Purpose:** List saved payment methods

**Logic:**
1. Query PaymentMethodModel from SQLite
2. Return card metadata (brand, last4, expiry)

**Issues:**
- Uses deprecated SQLite PaymentMethodModel
- Should use Stripe/Razorpay customer API
- No pagination
- No filtering

**Security Concerns:**
- User isolation via user_id filter (good)
- No additional validation

### 2.5 POST /api/billing/payment-methods
**Lines:** 475-531

**Purpose:** Add payment method

**Request Body:**
```python
class AddPaymentMethodRequest(BaseModel):
    payment_method_id: str
    brand: str
    last4: str
    expiry_month: int
    expiry_year: int
    set_as_default: bool = False
```

**Logic:**
1. Validate required card metadata fields
2. Set all existing methods to non-default if set_as_default
3. Create new PaymentMethodModel record
4. Return created method

**Issues:**
- Uses deprecated SQLite PaymentMethodModel
- Should use Stripe/Razorpay customer API
- No validation that payment_method_id exists in Stripe/Razorpay
- No validation of card expiry date

**Security Concerns:**
- Requires real card metadata from frontend (good - no fabrication)
- User isolation via user_id (good)
- No additional validation

**Critical Fix (Security):**
```python
# Require real card metadata from request — never fabricate it
missing = [f for f, v in [("brand", card_brand), ("last4", card_last4), ...] if not v]
if missing:
    raise HTTPException(422, f"Missing required card metadata fields: {', '.join(missing)}")
```

### 2.6 DELETE /api/billing/payment-methods/{method_id}
**Lines:** 534-549

**Purpose:** Delete payment method

**Logic:**
1. Query PaymentMethodModel by user_id and method_id
2. Delete if found
3. Return deleted method_id

**Issues:**
- Uses deprecated SQLite PaymentMethodModel
- Should use Stripe/Razorpay customer API
- No validation that method is not default
- No validation that method is not in use

**Security Concerns:**
- User isolation via user_id and method_id (good)
- No additional validation

### 2.7 POST /api/billing/portal
**Lines:** 552-583

**Purpose:** Create Stripe billing portal session

**Logic:**
1. Search Stripe customer by email
2. Create customer if not found
3. Set user_id in customer metadata
4. Create billing portal session
5. Return portal URL

**Issues:**
- Stripe-only (no Razorpay equivalent)
- No error handling for customer creation failure
- No validation that customer exists
- No audit trail of portal access

**Security Concerns:**
- User isolation via email (weak - email can change)
- No additional validation

---

## 3. Helper Functions

### 3.1 _validate_keys(provider)
**Lines:** 44-59

**Purpose:** Validate payment provider API keys

**Logic:**
- Stripe: Validates `STRIPE_SECRET_KEY` starts with `sk_live_` in production
- Razorpay: Validates `RAZORPAY_KEY_ID` starts with `rzp_live_` in production

**Issues:**
- No validation that keys are actually valid (just format check)
- No key rotation mechanism
- No key expiration check

**Security Concerns:**
- DEV_MODE bypass (lines 48, 54-58)
- Good format validation

### 3.2 _apply_billing_entitlement(user_id, item_key, discount_applied)
**Lines:** 70-109

**Purpose:** Apply billing entitlement to user

**Logic:**
1. Validate item_key against VALID_ITEM_KEYS
2. If ML addon: call `increment_ml_addon` RPC
3. Else: update `subscription_tier` in profiles
4. Invalidate profile cache
5. Log entitlement grant

**Issues:**
- No validation that user is eligible for upgrade
- No check if user already on same/higher tier
- No rollback if subsequent steps fail
- No audit trail of tier changes
- No notification to user

**Security Concerns:**
- VALID_ITEM_KEYS validation (good)
- No additional validation

### 3.3 _process_stripe_entitlement(user_id, item_key, discount_applied)
**Lines:** 113-115

**Purpose:** Process Stripe entitlement

**Logic:**
- Calls `_apply_billing_entitlement()`
- Logs processing

**Issues:**
- No additional logic beyond wrapper
- No error handling

### 3.4 _process_razorpay_entitlement(user_id, item_key, discount_applied)
**Lines:** 118-120

**Purpose:** Process Razorpay entitlement

**Logic:**
- Calls `_apply_billing_entitlement()`
- Logs processing

**Issues:**
- No additional logic beyond wrapper
- No error handling

### 3.5 _sb(user)
**Lines:** 32-40

**Purpose:** Create Supabase client for user

**Logic:**
- Creates request Supabase client with user's access token
- Enables RLS

**Issues:**
- No error handling for invalid token
- No caching

### 3.6 _background_sb()
**Lines:** 62-67

**Purpose:** Create Supabase client for background tasks

**Logic:**
- Creates Supabase client with service role key
- Bypasses RLS

**Issues:**
- No error handling
- No validation of service role key

---

## 4. Payment Gateway Integration

### 4.1 Stripe Integration

**SDK:** stripe (imported lazily)

**Endpoints Used:**
- `stripe.checkout.Session.create()` - Create checkout session
- `stripe.Webhook.construct_event()` - Verify webhook signature
- `stripe.Customer.list()` - List customers by email
- `stripe.Customer.create()` - Create customer
- `stripe.Customer.modify()` - Update customer metadata
- `stripe.Customer.retrieve()` - Retrieve customer
- `stripe.Subscription.retrieve()` - Retrieve subscription
- `stripe.billing_portal.Session.create()` - Create portal session

**Configuration:**
- `STRIPE_SECRET_KEY` - API secret key
- `STRIPE_WEBHOOK_SECRET` - Webhook signature verification

**Issues:**
- No Stripe customer ID stored in profiles
- Customer lookup by email (not reliable)
- No subscription ID stored in profiles
- No webhook event logging
- No retry logic for failed API calls

**Security Concerns:**
- Signature verification present (good)
- Key validation present (good)
- DEV_MODE bypass (line 252)

### 4.2 Razorpay Integration

**SDK:** razorpay (imported lazily)

**Endpoints Used:**
- `razorpay.Client()` - Initialize client
- `rzp.order.create()` - Create order
- `rzp.payment_link.create()` - Create payment link

**Configuration:**
- `RAZORPAY_KEY_ID` - API key ID
- `RAZORPAY_KEY_SECRET` - API secret
- `RAZORPAY_WEBHOOK_SECRET` - Webhook signature verification

**Issues:**
- No Razorpay customer ID stored in profiles
- No subscription management (only one-time payments)
- No webhook event logging
- No retry logic for failed API calls
- Payment link creation can fail (fallback to default URL)

**Security Concerns:**
- Signature verification present (good)
- Key validation present (good)
- DEV_MODE bypass (line 407)
- Payment link creation failure fallback (line 232)

---

## 5. Database Operations

### 5.1 Supabase Operations

**Tables Accessed:**
- `profiles` - User profiles and billing state
- `referral_commissions` - Referral commission records

**Operations:**
- `select()` - Fetch profile data
- `update()` - Update subscription_tier, is_frozen
- `rpc()` - Call database functions (increment_ml_addon, process_referral_commission)

**Issues:**
- No transaction support
- No retry logic for failed operations
- No validation of update results
- No audit logging

### 5.2 SQLite Operations

**Tables Accessed:**
- `payment_methods` - Payment method records (deprecated)

**Operations:**
- `query()` - Query payment methods
- `add()` - Add payment method
- `delete()` - Delete payment method
- `commit()` - Commit transaction

**Issues:**
- Deprecated per comments (F-20)
- Should be migrated to Supabase
- No audit logging
- No validation

---

## 6. Referral Commission Integration

### 6.1 Commission Processing

**Location:** Lines 295-310 (Stripe), 428-444 (Razorpay)

**Logic:**
1. Extract payment_id and payment_amount from webhook
2. Call `process_referral_commission` RPC
3. Pass: referred_id, payment_id, subscription_tier, payment_amount_usd
4. Log success/failure

**Issues:**
- Commission processing failure doesn't fail webhook
- No validation that commission amount is correct
- No cap on total commissions per referrer
- No fraud detection
- Razorpay passes INR as USD (line 439 comment)

**Security Concerns:**
- No validation of payment amount
- No validation of referral relationship
- Commission processing in same transaction as entitlement (could fail independently)

---

## 7. Cache Invalidation

### 7.1 Profile Cache Invalidation

**Location:** Line 109 (entitlement), Line 371 (freeze), Line 161 (in function)

**Logic:**
- Calls `invalidate_profile_cache(user_id)` after entitlement grant
- Calls `invalidate_profile_cache(user_id)` after account freeze
- Deletes Redis key: `profile_limits:{user_id}`

**Issues:**
- No validation that cache invalidation succeeded
- No retry logic for failed invalidation
- Cache TTL is 60s (user can have wrong tier for up to 60s)

**Security Concerns:**
- Good practice (immediate invalidation)
- No additional concerns

---

## 8. Error Handling

### 8.1 Current Error Handling

**Pattern:**
```python
try:
    # operation
except Exception as e:
    logger.error(f"Operation failed: {e}")
    raise HTTPException(status_code, detail)
```

**Issues:**
- Generic exception catching
- No specific error types
- No error classification
- No user-friendly error messages
- No error tracking/analytics

### 8.2 Missing Error Handling

- No retry logic for failed API calls
- No circuit breaker for payment gateway failures
- No fallback for webhook failures
- No dead letter queue for failed webhooks
- No alerting for critical failures

---

## 9. Logging

### 9.1 Current Logging

**Levels Used:**
- `logger.info()` - Successful operations
- `logger.warning()` - Non-critical issues
- `logger.error()` - Failures

**Issues:**
- No structured logging
- No correlation IDs
- No log aggregation
- No log retention policy
- No log analysis

### 9.2 Missing Logging

- No webhook event logging
- No checkout initiation logging
- No payment method change logging
- No tier change logging
- No cache invalidation logging
- No commission processing logging

---

## 10. Race Conditions

### 10.1 Webhook Idempotency
**Problem:** No idempotency key on webhook processing
**Scenario:**
1. Stripe sends webhook
2. Webhook processed, entitlement granted
3. Network timeout, no response to Stripe
4. Stripe retries webhook
5. Entitlement granted again (duplicate)

**Impact:** Double entitlement, incorrect counters

**Recommendation:**
- Add idempotency key to webhook processing
- Track processed webhook IDs in Redis
- Return 200 on duplicate webhooks

### 10.2 Discount Application Race Condition
**Problem:** Discount check and application not atomic
**Scenario:**
1. User has 1 discount
2. User starts two checkouts concurrently
3. Both check: `available_discounts = 1`
4. Both apply discount
5. Counter decremented twice (to -1)

**Impact:** Negative counter, discount abuse

**Recommendation:**
- Use atomic decrement in database
- Or use Redis distributed lock

### 10.3 Counter Increment Race Condition
**Problem:** Counter increment not atomic
**Scenario:**
1. User starts ML training
2. Counter fetched: `ml_strategies_built = 1`
3. User starts second ML training concurrently
4. Counter fetched: `ml_strategies_built = 1`
5. Both increment to 2
6. Actual: 3 models built, counter shows 2

**Impact:** Counter drift, quota bypass possible

**Recommendation:**
- Use atomic increment in database
- Or use Redis counter with lock

---

## 11. Security Vulnerabilities

### 11.1 Webhook Spoofing
**Status:** Partially mitigated
**Mitigation:** Signature verification
**Issues:**
- No additional validation of payment amount
- No validation of user eligibility
- No rate limiting on webhook endpoints

### 11.2 Entitlement Bypass
**Status:** Partially mitigated
**Mitigation:** Dependency checks on protected endpoints
**Issues:**
- Not all endpoints protected
- Hard quota enforcer not integrated
- Tenant middleware not used for billing

### 11.3 Counter Manipulation
**Status:** Vulnerable
**Problem:** Counters in profiles can be updated directly
**Issues:**
- No validation on counter updates
- No audit trail of counter changes
- User could potentially update own counters via profile update (blocked by whitelist)

### 11.4 Cache Poisoning
**Status:** Low risk
**Mitigation:** Redis cache with TTL
**Issues:**
- Cache key predictable
- No cache versioning
- No cache invalidation on config changes

### 11.5 DEV_MODE Bypasses
**Status:** High risk in production
**Problem:** DEV_MODE bypasses security checks
**Issues:**
- Signature verification bypass (line 407)
- Key validation bypass (lines 48, 54-58)
- No validation that DEV_MODE is actually development

**Recommendation:**
- Remove DEV_MODE bypasses in production
- Add environment validation
- Add feature flags for development features

---

## 12. Missing Features

### 12.1 Plan Downgrade
**Status:** Not implemented
**Problem:** No way to downgrade plan
**Issues:**
- No prorated refunds
- No feature removal on downgrade
- No data retention policy

### 12.2 Plan Cancellation
**Status:** Partially implemented
**Problem:** Only Stripe subscription deletion handled
**Issues:**
- No cancellation for Razorpay
- No cancellation grace period
- No data retention policy

### 12.3 Subscription Renewal
**Status:** Not implemented
**Problem:** No automatic renewal logic
**Issues:**
- No renewal reminders
- No renewal failure handling
- No retry logic for failed renewals

### 12.4 Usage Tracking
**Status:** Partially implemented
**Problem:** No comprehensive usage tracking
**Issues:**
- No backtest usage tracking
- No API call tracking
- No storage usage tracking
- No bandwidth usage tracking

### 12.5 Invoice Management
**Status:** Not implemented
**Problem:** No invoice management system
**Issues:**
- No invoice generation
- No invoice PDF generation
- No invoice history tracking
- No invoice email delivery

---

## 13. Recommendations

### 13.1 Immediate (P0)
1. Add webhook idempotency
2. Fix counter race conditions
3. Remove DEV_MODE bypasses in production
4. Add webhook event logging
5. Implement retry logic for failed webhooks

### 13.2 Short-term (P1)
1. Migrate payment methods to Stripe/Razorpay
2. Add plan downgrade/cancellation
3. Add usage tracking
4. Create audit trail
5. Implement error classification

### 13.3 Long-term (P2)
1. Implement subscription renewal
2. Add invoice management
3. Implement fraud detection
4. Add billing analytics
5. Create billing admin dashboard

---

## 14. Next Steps

**Phase 4: Backend Audit - Find Race Conditions/Security Issues**
- Document all race conditions
- Document all security vulnerabilities
- Create remediation plan
- Prioritize fixes

**Phase 5: Database Review**
- Verify schema and indexes
- Normalize schema where required
- Create migration plan

---

**End of Phase 4 Backend Audit**
