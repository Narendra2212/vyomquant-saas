# Billing System Business Logic Audit
**Phase 2: Business Logic Audit - Trace Billing Lifecycle**

Generated: 2025-01-08
Project: VyomQuant SaaS
Scope: Billing & Subscription System Business Logic

---

## Executive Summary

The billing lifecycle is fragmented across multiple components with inconsistent state management. While the core flow works (payment → webhook → entitlement), there are significant gaps in usage tracking, counter synchronization, and edge case handling. The system lacks a unified business logic layer, leading to scattered entitlement checks and potential race conditions.

---

## 1. Complete Billing Lifecycle Flow

### 1.1 User Registration & Onboarding
**Flow:**
1. User signs up via `POST /auth/signup` (routers/auth.py)
2. Referral code validation (if provided)
3. User created in Supabase auth.users
4. Profile created in Supabase profiles with default values:
   - `subscription_tier = "free"`
   - `deployed_bots = 0`
   - `ml_strategies_built = 0`
   - `ml_addons_purchased = 0`
   - `available_discounts = 0`
   - `is_frozen = false`
5. Referral code generated via `create_referral_code_for_user()` RPC

**Issues:**
- No validation that profile creation succeeded
- No default plan configuration in database
- Referral code generation not atomic with profile creation

### 1.2 Plan Selection & Checkout
**Flow:**
1. User views plans in `Billing.jsx` or `Pricing.jsx` (hardcoded)
2. User selects plan and clicks upgrade
3. Frontend calls `POST /api/billing/checkout` with:
   - `tier`: Plan identifier
   - `currency`: "USD" or "INR"
   - `is_addon`: Boolean for ML addon
4. Backend validates keys (`_validate_keys()`)
5. Backend checks `available_discounts` in profiles
6. If discount available: applies 10% discount
7. Creates Stripe session (USD) or Razorpay order (INR)
8. Returns checkout URL to frontend

**Issues:**
- No validation that selected tier exists in backend
- No validation that user can purchase (not frozen, not already on higher tier)
- Discount applied without tracking which discount used
- No idempotency on checkout creation
- No audit trail of checkout initiation

### 1.3 Payment Processing
**Flow:**
1. User completes payment on Stripe/Razorpay
2. Payment provider sends webhook:
   - Stripe: `POST /api/billing/webhook/stripe`
   - Razorpay: `POST /api/billing/webhook/razorpay`
3. Webhook signature verified
4. Event type checked (`checkout.session.completed` / `payment.captured`)
5. Extracts metadata:
   - `user_id` / `client_reference_id`
   - `item_key` (plan identifier)
   - `discount_applied`
6. Calls `_apply_billing_entitlement(user_id, item_key)`

**Issues:**
- No idempotency key on webhook processing
- Duplicate webhooks could grant entitlement twice
- No retry logic for failed webhooks
- No webhook event logging
- No validation that payment amount matches expected amount

### 1.4 Entitlement Application
**Flow:**
1. `_apply_billing_entitlement()` validates `item_key` against `VALID_ITEM_KEYS`
2. If `item_key == "ml_addon"`:
   - Calls `increment_ml_addon` RPC
   - Increments `ml_addons_purchased` counter
3. Else:
   - Updates `profiles.subscription_tier` to `item_key`
4. Calls `invalidate_profile_cache(user_id)`
5. Logs entitlement grant

**Issues:**
- No validation that user is eligible for upgrade
- No check if user already on same/higher tier
- No rollback if subsequent steps fail
- No audit trail of tier changes
- No notification to user of successful upgrade

### 1.5 Referral Commission Processing
**Flow:**
1. After entitlement grant, webhook calls `process_referral_commission` RPC
2. RPC validates referral relationship exists
3. Calculates commission (20% of payment amount)
4. Creates commission record in `referral_commissions`
5. Updates `referral_wallets.pending_balance_usd`
6. Sets commission status to "pending"

**Issues:**
- Commission processing failure doesn't fail the webhook
- No validation that commission amount is correct
- No cap on total commissions per referrer
- No fraud detection for self-referrals (already prevented at signup)

### 1.6 Account Freeze on Payment Failure
**Flow:**
1. Stripe webhook receives `invoice.payment_failed` event
2. Extracts `user_id` from subscription metadata
3. Updates `profiles.is_frozen = true`
4. Calls `invalidate_profile_cache(user_id)`
5. Logs freeze event

**Issues:**
- No retry logic before freezing
- No notification to user before freeze
- No grace period
- No manual unfreeze mechanism for users
- No validation that freeze succeeded

### 1.7 Entitlement Enforcement
**Flow:**
1. User attempts action (deploy bot, train ML, etc.)
2. Dependency checks called:
   - `check_deployment_limit()` for bot deployment
   - `check_ml_build_limit()` for ML training
3. `check_deployment_limit()`:
   - Fetches profile from cache/Supabase
   - Checks `is_frozen` flag
   - Gets `subscription_tier`
   - Maps to `DEPLOYMENT_LIMITS` dict
   - Counts live bots from FleetManager
   - Returns 403 if limit exceeded
4. `check_ml_build_limit()`:
   - Fetches profile from cache/Supabase
   - Gets `subscription_tier`
   - Maps to `ML_BUILD_LIMITS` dict
   - Gets `ml_strategies_built` and `ml_addons_purchased`
   - Returns 403 if limit exceeded

**Issues:**
- Limits hardcoded in Python, not database
- No usage tracking for backtests, API calls, etc.
- Counter drift possible (deployed_bots not decremented on bot stop)
- No quota usage API for frontend display
- No warning before hitting limits

### 1.8 Counter Updates
**Flow:**
1. Bot deployment: No counter update (uses FleetManager count)
2. ML training: Increments `ml_strategies_built` in profiles
3. ML addon purchase: Increments `ml_addons_purchased` via RPC
4. Discount usage: Decrements `available_discounts` (no code found for this)

**Issues:**
- `deployed_bots` counter never updated (comment says it drifts)
- No counter decrement on bot deletion/stop
- No counter decrement on ML model deletion
- No counter reset on plan downgrade
- No counter validation (can go negative)

---

## 2. Entitlement Check Locations

### 2.1 Deployment Limits
**File:** `backend_app/core/dependencies.py`
**Function:** `check_deployment_limit()`
**Used by:**
- `routers/strategies.py` - Bot deployment endpoint (`POST /api/strategies/{id}/start`)
- `backend/fleet_manager.py` - Fleet manager bot capacity check

**Logic:**
```python
tier = profile.get("subscription_tier", "free")
allowed = DEPLOYMENT_LIMITS.get(tier, 0)
live_bots = count_live_bots_from_fleet_manager()
if live_bots >= allowed:
    raise HTTPException(403, "Bot limit reached")
```

**Issues:**
- Hardcoded `DEPLOYMENT_LIMITS` dict
- No database-driven configuration
- No warning before limit reached
- No grace period for existing bots

### 2.2 ML Build Limits
**File:** `backend_app/core/dependencies.py`
**Function:** `check_ml_build_limit()`
**Used by:**
- `routers/strategies.py` - ML training endpoint (`POST /api/strategies/ml-train`)

**Logic:**
```python
tier = profile.get("subscription_tier", "free")
built = profile.get("ml_strategies_built", 0)
addons = profile.get("ml_addons_purchased", 0)
base_allowed = ML_BUILD_LIMITS.get(tier, 0)
total_allowed = base_allowed + addons
if built >= total_allowed:
    raise HTTPException(403, "ML strategy limit reached")
```

**Issues:**
- Hardcoded `ML_BUILD_LIMITS` dict
- Counter incremented in strategies.py but not validated
- No counter decrement on model deletion
- No ML model lifecycle management

### 2.3 Backtest Limits
**File:** `backend_app/core/hard_quota_enforcer.py`
**Function:** `enforce_backtest_parallel_limit()`
**Used by:**
- Not currently used in any router (dead code)

**Logic:**
```python
key = f"user:{user_id}:backtests:active"
current = await redis_manager.scard(key)
limit = tenant.quota.max_backtest_parallel
if current >= limit:
    raise ConcurrentOperationQuotaError
```

**Issues:**
- Not integrated into backtest endpoint
- No backtest counter in profiles
- No monthly backtest limit
- Quota defined in tenant.py but not enforced

### 2.4 Order Rate Limits
**File:** `backend_app/core/hard_quota_enforcer.py`
**Function:** `enforce_order_rate_limit()`
**Used by:**
- Not currently used in any router (dead code)

**Issues:**
- Not integrated into orders router
- No order rate limiting in production
- Only rate limiting via `@limiter` decorator (30/minute)

### 2.5 Position Limits
**File:** `backend_app/core/hard_quota_enforcer.py`
**Function:** `enforce_position_limit()`
**Used by:**
- Not currently used in any router (dead code)

**Issues:**
- Not integrated into portfolio/execution
- No position limit enforcement
- Only risk settings limit (max_positions in risk_settings)

### 2.6 Account Freeze Check
**File:** `backend_app/core/dependencies.py`
**Function:** `get_current_user()` (via `_get_cached_profile()`)
**Used by:**
- All authenticated endpoints (via `get_current_user` dependency)

**Logic:**
```python
profile = await _get_cached_profile(tenant_id, token)
if profile.get("is_frozen", False):
    raise HTTPException(403, "User account is frozen")
```

**Issues:**
- Cache TTL (60s) means frozen users can act for up to 60s
- No notification to user of freeze
- No freeze reason stored
- No freeze history/audit trail

---

## 3. Counter Synchronization Issues

### 3.1 deployed_bots Counter
**Status:** Drifts out of sync
**Problem:**
- Counter in profiles never updated
- FleetManager count used as ground truth
- Comment in code: "can drift out of sync when bots crash without decrementing it"

**Impact:**
- Counter is useless
- Relies on FleetManager state
- No database record of deployment history

**Recommendation:**
- Remove `deployed_bots` from profiles
- Use FleetManager as single source of truth
- Or implement proper counter synchronization

### 3.2 ml_strategies_built Counter
**Status:** Incremented but never decremented
**Problem:**
- Incremented in `routers/strategies.py` after ML training
- Never decremented on model deletion
- No validation that counter is accurate

**Impact:**
- Users can run out of ML slots even after deleting models
- No way to recover slots without manual intervention
- Counter can exceed actual model count

**Recommendation:**
- Implement counter decrement on model deletion
- Add counter validation/reconciliation
- Or remove counter and count actual models

### 3.3 ml_addons_purchased Counter
**Status:** Only incremented via RPC
**Problem:**
- Incremented via `increment_ml_addon` RPC on purchase
- Never decremented
- No validation of purchase history

**Impact:**
- No way to track which addons were purchased
- No refund mechanism
- No addon expiration

**Recommendation:**
- Create `ml_addon_purchases` table for audit trail
- Add addon expiration logic
- Implement refund mechanism

### 3.4 available_discounts Counter
**Status:** Decremented but implementation incomplete
**Problem:**
- Checked in checkout endpoint
- Decremented by 1 when applied (no code found)
- No tracking of which discounts used
- No discount expiration

**Impact:**
- Discount usage not tracked
- No audit trail of discount applications
- No way to revoke discounts

**Recommendation:**
- Create `discount_usage` table
- Track discount application history
- Add discount expiration logic

---

## 4. Plan Mapping Inconsistencies

### 4.1 Plan Identifier Mappings

| Context | Free | Pro | Elite | ML Addon |
|---------|------|-----|-------|----------|
| Frontend (Billing.jsx) | `free` | `pro` | `enterprise` | N/A |
| Frontend (Pricing.jsx) | `Free` | `Pro` | `Elite` | N/A |
| Backend Billing (billing.py) | `free` | `pro_999` | `elite_1999` | `ml_addon` |
| Backend User (user.py) | `free` | `pro` | `enterprise` | N/A |
| Backend Tenant (tenant.py) | `FREE` | `BASIC` | `PROFESSIONAL` | N/A |
| Library (library.py) | `free` | `pro` | `elite` | N/A |

**Issues:**
- 6 different naming conventions
- No canonical mapping function
- Mapping logic scattered in user.py
- High risk of mapping errors

### 4.2 Price Mappings

| Plan | Backend Key | USD | INR |
|------|-------------|-----|-----|
| Free | `free` | 0 | 0 |
| Pro | `pro_999` | 12.00 | 999 |
| Elite | `elite_1999` | 24.00 | 1999 |
| ML Addon | `ml_addon` | 3.00 | 199 |

**Issues:**
- Prices hardcoded in billing.py
- No database configuration
- No regional pricing beyond USD/INR
- No annual/monthly pricing variants

---

## 5. Race Conditions & Concurrency Issues

### 5.1 Webhook Idempotency
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

### 5.2 Counter Increment Race Condition
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

### 5.3 Cache Invalidation Race Condition
**Problem:** Cache invalidation not atomic with entitlement grant
**Scenario:**
1. Payment webhook received
2. Entitlement granted in database
3. Cache invalidation called
4. User makes request before invalidation completes
5. User gets old tier from cache

**Impact:** User temporarily has wrong tier (60s window)

**Recommendation:**
- Make entitlement grant and cache invalidation atomic
- Or reduce cache TTL to 5s

### 5.4 Discount Application Race Condition
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

---

## 6. Missing Business Logic

### 6.1 Plan Downgrade
**Status:** Not implemented
**Problem:**
- No way to downgrade plan
- No prorated refunds
- No feature removal on downgrade

**Recommendation:**
- Implement plan downgrade endpoint
- Add prorated refund logic
- Add feature removal logic

### 6.2 Plan Cancellation
**Status:** Partially implemented
**Problem:**
- Stripe subscription deletion handled
- No cancellation for Razorpay
- No cancellation grace period
- No data retention policy

**Recommendation:**
- Implement cancellation for both providers
- Add grace period before feature removal
- Define data retention policy

### 6.3 Subscription Renewal
**Status:** Not implemented
**Problem:**
- No automatic renewal logic
- No renewal reminders
- No renewal failure handling

**Recommendation:**
- Implement automatic renewal
- Add renewal reminders
- Handle renewal failures gracefully

### 6.4 Usage Tracking
**Status:** Partially implemented
**Problem:**
- No backtest usage tracking
- No API call tracking
- No storage usage tracking
- No bandwidth usage tracking

**Recommendation:**
- Implement comprehensive usage tracking
- Add usage analytics dashboard
- Add usage alerts

### 6.5 Quota Warnings
**Status:** Not implemented
**Problem:**
- No warning before hitting limits
- No quota usage display
- No upgrade prompts

**Recommendation:**
- Implement quota warning system
- Add usage display to frontend
- Add upgrade prompts

---

## 7. Database RPC Functions

### 7.1 increment_ml_addon
**Location:** referral_system_redesign.sql
**Purpose:** Increment ML addon counter
**Called by:** billing.py webhook handler
**Issues:**
- No validation of user eligibility
- No audit trail of addon purchase
- No expiration logic

### 7.2 process_referral_commission
**Location:** referral_system_redesign.sql
**Purpose:** Process referral commission on payment
**Called by:** billing.py webhook handler
**Issues:**
- Commission failure doesn't fail webhook
- No fraud detection
- No commission cap

### 7.3 create_referral_code_for_user
**Location:** referral_system_redesign.sql
**Purpose:** Generate referral code for new user
**Called by:** auth.py signup handler
**Issues:**
- Not atomic with profile creation
- No retry on collision

---

## 8. Security Issues

### 8.1 Webhook Spoofing
**Status:** Partially mitigated
**Mitigation:** Signature verification
**Issues:**
- No additional validation of payment amount
- No validation of user eligibility
- No rate limiting on webhook endpoints

### 8.2 Entitlement Bypass
**Status:** Partially mitigated
**Mitigation:** Dependency checks on protected endpoints
**Issues:**
- Not all endpoints protected
- Hard quota enforcer not integrated
- Tenant middleware not used for billing

### 8.3 Counter Manipulation
**Status:** Vulnerable
**Problem:** Counters in profiles can be updated directly
**Issues:**
- No validation on counter updates
- No audit trail of counter changes
- User could potentially update own counters via profile update (blocked by whitelist)

### 8.4 Cache Poisoning
**Status:** Low risk
**Mitigation:** Redis cache with TTL
**Issues:**
- Cache key predictable
- No cache versioning
- No cache invalidation on config changes

---

## 9. Audit Trail Gaps

### 9.1 Missing Audit Events
- Plan changes
- Counter changes
- Discount usage
- Entitlement grants
- Webhook processing
- Payment failures
- Account freezes

### 9.2 Current Audit Trail
- Admin actions (admin.py)
- Referral commissions (referral_commissions table)
- Support tickets (support_tickets table)

### 9.3 Recommendations
- Create `billing_audit_log` table
- Log all billing-related events
- Add immutable audit trail
- Add audit log viewer for admins

---

## 10. Recommendations Summary

### 10.1 Immediate (P0)
1. Add webhook idempotency
2. Fix counter race conditions
3. Standardize plan naming convention
4. Move plan definitions to database
5. Implement centralized entitlement engine

### 10.2 Short-term (P1)
1. Implement plan downgrade/cancellation
2. Add usage tracking
3. Add quota warnings
4. Create audit trail
5. Fix counter synchronization

### 10.3 Long-term (P2)
1. Implement subscription renewal
2. Add regional pricing
3. Implement fraud detection
4. Add billing analytics
5. Create billing admin dashboard

---

## 11. Next Steps

**Phase 3: Frontend Audit**
- Inspect all billing UI components
- Identify hardcoded plan references
- Find missing feature flags
- Document upgrade flows

**Phase 4: Backend Audit**
- Inspect all billing services
- Find race conditions
- Identify security vulnerabilities
- Review webhook handlers

---

**End of Phase 2 Business Logic Audit**
