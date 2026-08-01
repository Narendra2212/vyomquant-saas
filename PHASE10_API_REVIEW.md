# Phase 10: API Review
**Audit Every Billing Endpoint and Remove Unused Endpoints**

Generated: 2025-01-08
Project: VyomQuant SaaS
Scope: Billing API endpoint audit

---

## Executive Summary

The billing API has 7 endpoints across 2 routers (billing.py and user.py). Some endpoints are deprecated or incomplete. This phase audits all billing endpoints, identifies issues, and recommends removal or updates.

---

## Billing Router Endpoints

### 1. POST /api/billing/checkout

**File:** `routers/billing.py` (lines 132-239)

**Purpose:** Create checkout session for Stripe or Razorpay

**Request Body:**
```python
class CheckoutRequest(BaseModel):
    tier: SubscriptionTier
    currency: str  # USD or INR
    is_addon: bool = False
```

**Issues:**
- No idempotency key
- No validation that user can purchase (not frozen, not already on higher tier)
- Discount applied without tracking which discount used
- No audit trail of checkout initiation
- No validation of payment amount against expected amount

**Recommendation:**
- Add idempotency key
- Add user eligibility validation
- Add audit logging
- Add payment amount validation

**Status:** Keep (needs updates)

---

### 2. POST /api/billing/webhook/stripe

**File:** `routers/billing.py` (lines 243-377)

**Purpose:** Process Stripe webhook events

**Events Handled:**
- checkout.session.completed
- customer.subscription.deleted
- customer.subscription.updated
- invoice.payment_failed

**Issues:**
- No idempotency key tracking
- No rate limiting
- No IP whitelist
- No webhook event logging
- Commission processing failure doesn't fail webhook

**Recommendation:**
- Add idempotency key tracking
- Add rate limiting
- Add IP whitelist
- Add webhook event logging
- Make commission failure fail webhook

**Status:** Keep (needs updates)

---

### 3. POST /api/billing/webhook/razorpay

**File:** `routers/billing.py` (lines 381-452)

**Purpose:** Process Razorpay webhook events

**Events Handled:**
- payment.captured

**Issues:**
- No idempotency key tracking
- No rate limiting
- No IP whitelist
- No webhook event logging
- Only handles payment.captured (no subscription events)
- Commission processing failure doesn't fail webhook
- DEV_MODE bypasses signature verification

**Recommendation:**
- Add idempotency key tracking
- Add rate limiting
- Add IP whitelist
- Add webhook event logging
- Add subscription event handlers
- Remove DEV_MODE bypass

**Status:** Keep (needs updates)

---

### 4. GET /api/billing/payment-methods

**File:** `routers/billing.py` (lines 455-472)

**Purpose:** List saved payment methods

**Issues:**
- Uses deprecated SQLite PaymentMethodModel
- Should use Stripe/Razorpay customer API
- No pagination
- No filtering

**Recommendation:**
- Migrate to Stripe/Razorpay customer API
- Remove SQLite table
- Add pagination
- Add filtering

**Status:** Deprecate (replace with Stripe/Razorpay API)

---

### 5. POST /api/billing/payment-methods

**File:** `routers/billing.py` (lines 475-531)

**Purpose:** Add payment method

**Issues:**
- Uses deprecated SQLite PaymentMethodModel
- Should use Stripe/Razorpay customer API
- No validation that payment_method_id exists in Stripe/Razorpay
- No validation of card expiry date

**Recommendation:**
- Migrate to Stripe/Razorpay customer API
- Remove SQLite table
- Add validation

**Status:** Deprecate (replace with Stripe/Razorpay API)

---

### 6. DELETE /api/billing/payment-methods/{method_id}

**File:** `routers/billing.py` (lines 534-549)

**Purpose:** Delete payment method

**Issues:**
- Uses deprecated SQLite PaymentMethodModel
- Should use Stripe/Razorpay customer API
- No validation that method is not default
- No validation that method is not in use

**Recommendation:**
- Migrate to Stripe/Razorpay customer API
- Remove SQLite table
- Add validation

**Status:** Deprecate (replace with Stripe/Razorpay API)

---

### 7. POST /api/billing/portal

**File:** `routers/billing.py` (lines 552-583)

**Purpose:** Create Stripe billing portal session

**Issues:**
- Stripe-only (no Razorpay equivalent)
- No error handling for customer creation failure
- No validation that customer exists
- No audit trail of portal access

**Recommendation:**
- Add Razorpay equivalent
- Add error handling
- Add audit logging

**Status:** Keep (needs updates)

---

## User Router Billing Endpoints

### 8. GET /api/billing/plan

**File:** `routers/user.py` (lines 74-122)

**Purpose:** Get user's billing plan information

**Issues:**
- Returns hardcoded plan info
- No database-driven plan configuration
- No feature availability
- No quota status

**Recommendation:**
- Use new plan service
- Return feature availability
- Return quota status

**Status:** Keep (needs updates)

---

### 9. GET /api/billing/invoices

**File:** `routers/user.py` (lines 124-136)

**Purpose:** Get user's invoices

**Issues:**
- Uses deprecated SQLite InvoiceModel
- No real invoice data from payment providers
- No pagination
- No filtering

**Recommendation:**
- Create invoices table in Supabase
- Migrate invoice data from payment providers
- Remove SQLite table
- Add pagination

**Status:** Deprecate (replace with Supabase invoices)

---

## Recommended Endpoint Changes

### Keep and Update

1. **POST /api/billing/checkout**
   - Add idempotency key
   - Add user eligibility validation
   - Add audit logging

2. **POST /api/billing/webhook/stripe**
   - Add idempotency key tracking
   - Add rate limiting
   - Add IP whitelist
   - Add webhook event logging

3. **POST /api/billing/webhook/razorpay**
   - Add idempotency key tracking
   - Add rate limiting
   - Add IP whitelist
   - Add webhook event logging
   - Remove DEV_MODE bypass

4. **POST /api/billing/portal**
   - Add Razorpay equivalent
   - Add error handling
   - Add audit logging

5. **GET /api/billing/plan**
   - Use new plan service
   - Return feature availability
   - Return quota status

### Deprecate and Replace

1. **GET /api/billing/payment-methods**
   - Replace with Stripe/Razorpay customer API
   - Remove SQLite table

2. **POST /api/billing/payment-methods**
   - Replace with Stripe/Razorpay customer API
   - Remove SQLite table

3. **DELETE /api/billing/payment-methods/{method_id}**
   - Replace with Stripe/Razorpay customer API
   - Remove SQLite table

4. **GET /api/billing/invoices**
   - Replace with Supabase invoices table
   - Remove SQLite table

### New Endpoints to Add

1. **GET /api/billing/currency**
   - Get detected currency for user
   - Return currency symbol and supported currencies

2. **GET /api/billing/pricing**
   - Get pricing for all plans
   - Support currency and billing cycle parameters

3. **GET /api/user/entitlements**
   - Get all entitlement information
   - Return feature availability and quota status

4. **PUT /api/user/currency-preference**
   - Set user's preferred currency
   - Save to profile

5. **GET /api/billing/payments**
   - Get payment history
   - From new payments table

6. **POST /api/billing/upgrade**
   - Upgrade plan
   - Create checkout session for upgrade

7. **POST /api/billing/downgrade**
   - Downgrade plan
   - Calculate prorated refund

8. **POST /api/billing/cancel**
   - Cancel subscription
   - Handle cancellation logic

---

## Endpoint Security Audit

### Authentication

**All endpoints:** Require `get_current_user` dependency ✓

**Issues:**
- No additional role checks for admin operations
- No rate limiting on checkout endpoint

**Recommendation:**
- Add rate limiting to checkout endpoint
- Add admin role checks for admin operations

### Authorization

**Issues:**
- No validation that user owns payment method
- No validation that user owns invoice
- No validation that user can modify plan

**Recommendation:**
- Add ownership validation
- Add plan eligibility validation

### Input Validation

**Issues:**
- No validation of tier values
- No validation of currency values
- No validation of payment_method_id format

**Recommendation:**
- Add input validation using Pydantic models
- Add custom validators for payment provider IDs

---

## API Documentation

### Current State

- No OpenAPI/Swagger documentation for billing endpoints
- No API versioning
- No deprecation warnings

### Recommendations

1. Add OpenAPI documentation using FastAPI's built-in support
2. Add API versioning (e.g., /api/v1/billing/...)
3. Add deprecation warnings for deprecated endpoints
4. Add response examples
5. Add error response documentation

---

## Migration Plan

### Phase 10.1: Update Existing Endpoints

**Priority:** P0 (Critical)
**Effort:** 8 hours

**Tasks:**
1. Add idempotency to checkout endpoint
2. Add rate limiting to webhooks
3. Add webhook event logging
4. Update plan endpoint to use new plan service
5. Remove DEV_MODE bypasses

### Phase 10.2: Deprecate Payment Method Endpoints

**Priority:** P1 (High)
**Effort:** 4 hours

**Tasks:**
1. Add deprecation warnings to payment method endpoints
2. Document migration path
3. Create Stripe/Razorpay customer API endpoints
4. Update frontend to use new endpoints

### Phase 10.3: Deprecate Invoice Endpoint

**Priority:** P1 (High)
**Effort:** 4 hours

**Tasks:**
1. Add deprecation warning to invoice endpoint
2. Create invoices table in Supabase
3. Migrate invoice data from payment providers
4. Create new invoice endpoint
5. Update frontend to use new endpoint

### Phase 10.4: Add New Endpoints

**Priority:** P1 (High)
**Effort:** 8 hours

**Tasks:**
1. Add GET /api/billing/currency endpoint
2. Add GET /api/billing/pricing endpoint
3. Add GET /api/user/entitlements endpoint
4. Add PUT /api/user/currency-preference endpoint
5. Add GET /api/billing/payments endpoint

### Phase 10.5: Add Plan Management Endpoints

**Priority:** P2 (Medium)
**Effort:** 8 hours

**Tasks:**
1. Add POST /api/billing/upgrade endpoint
2. Add POST /api/billing/downgrade endpoint
3. Add POST /api/billing/cancel endpoint
4. Add plan change logic
5. Add prorated refund calculation

---

## Testing Plan

### Unit Tests
- Test all endpoint request/response schemas
- Test input validation
- Test error handling

### Integration Tests
- Test checkout flow
- Test webhook processing
- Test plan retrieval
- Test currency detection

### End-to-End Tests
- Test complete payment flow
- Test plan upgrade/downgrade
- Test cancellation flow

---

## Rollback Plan

If issues arise:

1. **Revert Changes:** Use git revert to undo endpoint changes
2. **Feature Flags:** Add feature flags to enable/disable new endpoints
3. **Keep Old Endpoints:** Keep deprecated endpoints as fallback

**Rollback Commands:**
```bash
git revert <commit-hash>
export USE_NEW_BILLING_ENDPOINTS=false
```

---

## Next Steps

1. Review and approve this API review
2. Begin Phase 10.1 (Update existing endpoints)
3. Proceed through phases 10.2-10.5
4. Complete testing
5. Deploy to staging
6. Monitor for issues
7. Deploy to production

---

**End of Phase 10 API Review**
