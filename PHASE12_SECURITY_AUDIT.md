# Phase 12: Security Audit
**Audit All Security Vulnerabilities in Billing System**

Generated: 2025-01-08
Project: VyomQuant SaaS
Scope: Comprehensive security audit of billing system

---

## Executive Summary

The billing system has several critical security vulnerabilities that require immediate remediation. The most critical issues are DEV_MODE bypasses in production, lack of webhook idempotency, and missing input validation. This audit consolidates all security findings from previous phases and provides a comprehensive remediation plan.

---

## Critical Security Vulnerabilities (P0)

### 1. DEV_MODE Security Bypasses

**Location:** `routers/billing.py` (lines 252, 407, 48, 54-58)

**Issue:** DEV_MODE bypasses signature verification and key validation in production

**Impact:**
- Webhook spoofing possible in production if DEV_MODE is enabled
- Invalid API keys can be used if DEV_MODE is enabled
- Payment security compromised

**Current Code:**
```python
# Line 252 - Stripe webhook
if not DEV_MODE:
    if not webhook_secret or webhook_secret == "whsec_dummy":
        raise HTTPException(500, "Stripe production webhook secret is missing")

# Line 407 - Razorpay webhook
if not DEV_MODE:
    logger.warning("Razorpay webhook signature verification failure ignored in DEV_MODE.")
else:
    raise HTTPException(400, "Invalid Razorpay signature")

# Lines 48, 54-58 - Key validation
if not DEV_MODE:
    if not key or key == "sk_test_dummy" or not key.startswith("sk_live_"):
        raise HTTPException(500, "Stripe production secret key is missing")
```

**Fix Required:**
```python
# Remove DEV_MODE bypasses entirely
if not webhook_secret or webhook_secret == "whsec_dummy" or not webhook_secret.startswith("whsec_"):
    raise HTTPException(500, "Stripe production webhook secret is missing")

if not key or key == "sk_test_dummy" or not key.startswith("sk_live_"):
    raise HTTPException(500, "Stripe production secret key is missing")
```

**Priority:** P0 - Critical
**Effort:** 1 hour

---

### 2. Webhook Idempotency Missing

**Location:** `routers/billing.py` (lines 243-452)

**Issue:** No idempotency key tracking for webhooks

**Impact:**
- Duplicate webhooks can grant entitlement twice
- Double billing possible
- Counter drift

**Current Code:**
```python
@router.post("/webhook/stripe")
async def stripe_webhook(...):
    # No idempotency check
    event = stripe.Webhook.construct_event(payload, stripe_signature, secret)
    
    # Process webhook
    await _process_stripe_entitlement(user_id, item_key, discount_applied)
```

**Fix Required:**
```python
@router.post("/webhook/stripe")
async def stripe_webhook(...):
    event = stripe.Webhook.construct_event(payload, stripe_signature, secret)
    event_id = event["id"]
    
    # Check idempotency
    cache_key = f"webhook:stripe:{event_id}"
    if await redis_manager.exists(cache_key):
        logger.info(f"Stripe webhook {event_id} already processed")
        return {"status": "already_processed"}
    
    # Process webhook
    await _process_stripe_entitlement(user_id, item_key, discount_applied)
    
    # Mark as processed
    await redis_manager.setex(cache_key, 86400, "1")
```

**Priority:** P0 - Critical
**Effort:** 2 hours

---

### 3. Counter Race Conditions

**Location:** `core/dependencies.py` (lines 406-416, 472-493)

**Issue:** Counter operations not atomic

**Impact:**
- Counter drift allows quota bypass
- Users can exceed limits
- Financial loss

**Current Code:**
```python
# Counter fetched from database
profile = await _get_cached_profile(user["id"], supabase)
built = profile.get("ml_strategies_built", 0)

# Check limit
if built >= total_allowed:
    raise HTTPException(403, "ML strategy limit reached")

# Counter incremented elsewhere (not atomic)
```

**Fix Required:**
```python
# Use atomic increment in database
await sb.rpc("increment_counter", {
    "target_user_id": user_id,
    "counter_column": "ml_strategies_built"
}).execute()
```

**Priority:** P0 - Critical
**Effort:** 4 hours

---

### 4. Plan Naming Convention Inconsistency

**Location:** Multiple files

**Issue:** 6 different plan naming conventions

**Impact:**
- Mapping errors cause incorrect tier assignment
- Quota enforcement failures
- Security bypass possible

**Fix Required:**
- Standardize to single naming convention
- Add mapping function for backward compatibility
- Add validation on plan values

**Priority:** P0 - Critical
**Effort:** 6 hours

---

## High Priority Security Issues (P1)

### 5. No Webhook Rate Limiting

**Location:** `routers/billing.py` (lines 243, 381)

**Issue:** No rate limiting on webhook endpoints

**Impact:**
- DoS attacks possible
- API abuse
- Resource exhaustion

**Fix Required:**
```python
from slowapi import Limiter
limiter = Limiter(key_func=get_remote_address)

@router.post("/webhook/stripe")
@limiter.limit("100/minute")
async def stripe_webhook(...):
    # ...
```

**Priority:** P1 - High
**Effort:** 2 hours

---

### 6. No Webhook IP Whitelist

**Location:** `routers/billing.py` (lines 243, 381)

**Issue:** No IP whitelist for webhook endpoints

**Impact:**
- Webhook spoofing from unauthorized IPs
- Security bypass possible

**Fix Required:**
```python
STRIPE_WEBHOOK_IPS = ["3.18.12.63", "3.130.192.231", ...]

@router.post("/webhook/stripe")
async def stripe_webhook(request: Request, ...):
    client_ip = request.client.host
    if client_ip not in STRIPE_WEBHOOK_IPS:
        logger.warning(f"Stripe webhook from unauthorized IP: {client_ip}")
        raise HTTPException(403, "Unauthorized")
```

**Priority:** P1 - High
**Effort:** 2 hours

---

### 7. No Webhook Timestamp Validation

**Location:** `routers/billing.py` (lines 243, 381)

**Issue:** No timestamp validation on webhooks

**Impact:**
- Replay attacks possible
- Old webhooks can be replayed

**Fix Required:**
```python
event_timestamp = event["created"]
current_timestamp = int(time.time())
if abs(current_timestamp - event_timestamp) > 300:  # 5 minutes
    logger.warning(f"Webhook timestamp too old: {event_timestamp}")
    raise HTTPException(400, "Webhook timestamp too old")
```

**Priority:** P1 - High
**Effort:** 1 hour

---

### 8. No Input Validation on Checkout

**Location:** `routers/billing.py` (lines 132-239)

**Issue:** No validation of tier and currency values

**Impact:**
- Invalid values can be submitted
- Security bypass possible

**Fix Required:**
```python
class CheckoutRequest(BaseModel):
    tier: SubscriptionTier
    currency: str = Field(..., regex="^(USD|INR)$")
    is_addon: bool = False
    
    @validator('tier')
    def validate_tier(cls, v):
        if v not in [SubscriptionTier.STARTER, SubscriptionTier.PRO, 
                     SubscriptionTier.BUSINESS, SubscriptionTier.ENTERPRISE]:
            raise ValueError("Invalid tier")
        return v
```

**Priority:** P1 - High
**Effort:** 2 hours

---

### 9. No Payment Amount Validation

**Location:** `routers/billing.py` (lines 132-239)

**Issue:** No validation that payment amount matches expected amount

**Impact:**
- Payment amount manipulation possible
- Financial loss

**Fix Required:**
```python
expected_amount = PRICES.get(item_key, {}).get(body.currency)
if amount != expected_amount:
    logger.warning(f"Payment amount mismatch: {amount} vs {expected_amount}")
    raise HTTPException(400, "Invalid payment amount")
```

**Priority:** P1 - High
**Effort:** 2 hours

---

### 10. No User Eligibility Validation

**Location:** `routers/billing.py` (lines 132-239)

**Issue:** No validation that user can purchase (not frozen, not already on higher tier)

**Impact:**
- Frozen users can purchase
- Users can downgrade to same tier
- Security bypass possible

**Fix Required:**
```python
# Check if user is frozen
profile = await _get_cached_profile(user["id"], supabase)
if profile.get("is_frozen", False):
    raise HTTPException(403, "User account is frozen")

# Check if user already on higher tier
current_tier = profile.get("subscription_tier", "free")
if is_higher_tier(current_tier, body.tier.value):
    raise HTTPException(400, "Cannot downgrade to same or lower tier")
```

**Priority:** P1 - High
**Effort:** 2 hours

---

## Medium Priority Security Issues (P2)

### 11. No Audit Trail for Billing Operations

**Location:** All billing endpoints

**Issue:** No audit trail for billing operations

**Impact:**
- Cannot audit billing operations
- Cannot debug issues
- Cannot detect fraud

**Fix Required:**
```python
# Create billing_audit_log table
INSERT INTO billing_audit_log (event_type, user_id, details, created_at)
VALUES ($1, $2, $3, NOW())
```

**Priority:** P2 - Medium
**Effort:** 4 hours

---

### 12. No Referral Fraud Detection

**Location:** `routers/billing.py` (lines 295-310, 428-444)

**Issue:** No fraud detection for referral commissions

**Impact:**
- Referral abuse possible
- Financial loss

**Fix Required:**
```python
# Add fraud detection
if is_suspicious_referral(referrer_id, referred_id):
    logger.warning(f"Suspicious referral: {referrer_id} -> {referred_id}")
    raise HTTPException(400, "Referral flagged for review")
```

**Priority:** P2 - Medium
**Effort:** 6 hours

---

### 13. No Cap on Total Commissions

**Location:** `migrations/referral_system_redesign.sql` (lines 258-320)

**Issue:** No cap on total commissions per referrer

**Impact:**
- Unlimited commission payouts
- Financial loss

**Fix Required:**
```sql
-- Add commission cap to referral_wallets
ALTER TABLE referral_wallets ADD COLUMN commission_cap_usd DECIMAL(10, 2) DEFAULT 10000.00;

-- Update commission processing to check cap
IF (pending_balance_usd + commission_amount) > commission_cap_usd THEN
    -- Don't add commission
END IF;
```

**Priority:** P2 - Medium
**Effort:** 2 hours

---

### 14. Cache Poisoning Risk

**Location:** `core/dependencies.py` (lines 286-374)

**Issue:** Cache key predictable, no cache versioning

**Impact:**
- Cache poisoning possible
- Wrong data served

**Fix Required:**
```python
# Add cache versioning
cache_key = f"profile_limits:{user_id}:v2"

# Add cache validation
if cached and not validate_cache_integrity(cached):
    await redis_manager.delete(cache_key)
    return await _get_cached_profile(user_id, supabase)
```

**Priority:** P2 - Medium
**Effort:** 4 hours

---

## Low Priority Security Issues (P3)

### 15. No Encryption at Rest for Sensitive Data

**Location:** Database schema

**Issue:** Sensitive billing data not encrypted at rest

**Impact:**
- Data breach exposes sensitive data

**Fix Required:**
- Encrypt payment method data
- Encrypt subscription details
- Use database encryption

**Priority:** P3 - Low
**Effort:** 8 hours

---

### 16. No API Versioning

**Location:** All billing endpoints

**Issue:** No API versioning

**Impact:**
- Breaking changes affect all clients
- No backward compatibility

**Fix Required:**
```python
# Add versioning to endpoints
@router.post("/api/v1/billing/checkout")
@router.post("/api/v2/billing/checkout")
```

**Priority:** P3 - Low
**Effort:** 4 hours

---

### 17. No CORS Configuration

**Location:** `main.py`

**Issue:** CORS configuration may be too permissive

**Impact:**
- CSRF attacks possible
- Unauthorized API access

**Fix Required:**
```python
# Restrict CORS to specific origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.vyomquant.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
```

**Priority:** P3 - Low
**Effort:** 1 hour

---

## Security Best Practices Not Implemented

### 1. Security Headers

**Missing Headers:**
- Content-Security-Policy
- X-Frame-Options
- X-Content-Type-Options
- Strict-Transport-Security
- Referrer-Policy

**Fix Required:**
```python
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

app.add_middleware(TrustedHostMiddleware, allowed_hosts=["app.vyomquant.com"])
app.add_middleware(HTTPSRedirectMiddleware)
```

### 2. Request Size Limits

**Issue:** No request size limits

**Fix Required:**
```python
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 10 * 1024 * 1024:  # 10MB
            raise HTTPException(413, "Request too large")
        return await call_next(request)
```

### 3. Response Size Limits

**Issue:** No response size limits

**Fix Required:**
```python
# Add response size limits in API responses
```

### 4. SQL Injection Protection

**Status:** Protected by Supabase RLS
**Recommendation:** Continue using parameterized queries

### 5. XSS Protection

**Status:** Not applicable (API only)
**Recommendation:** Add CSP headers for future web UI

---

## Security Testing Recommendations

### 1. Penetration Testing

**Scope:**
- Webhook endpoints
- Checkout endpoint
- Plan endpoint
- Payment method endpoints

**Tools:**
- OWASP ZAP
- Burp Suite
- Custom test scripts

### 2. Security Scanning

**Tools:**
- Bandit (Python security scanner)
- Safety (dependency vulnerability scanner)
- Snyk (dependency vulnerability scanner)

### 3. Manual Security Review

**Checklist:**
- [ ] All inputs validated
- [ ] All outputs sanitized
- [ ] All secrets properly stored
- [ ] All encryption properly implemented
- [ ] All authentication properly implemented
- [ ] All authorization properly implemented

---

## Remediation Timeline

### Week 1: Critical Fixes (P0)
1. Remove DEV_MODE bypasses (1 hour)
2. Add webhook idempotency (2 hours)
3. Fix counter race conditions (4 hours)
4. Standardize plan naming (6 hours)

### Week 2: High Priority Fixes (P1)
1. Add webhook rate limiting (2 hours)
2. Add webhook IP whitelist (2 hours)
3. Add webhook timestamp validation (1 hour)
4. Add input validation (2 hours)
5. Add payment amount validation (2 hours)
6. Add user eligibility validation (2 hours)

### Week 3: Medium Priority Fixes (P2)
1. Add audit trail (4 hours)
2. Add referral fraud detection (6 hours)
3. Add commission caps (2 hours)
4. Fix cache poisoning (4 hours)

### Week 4: Low Priority Fixes (P3)
1. Add encryption at rest (8 hours)
2. Add API versioning (4 hours)
3. Configure CORS (1 hour)
4. Add security headers (2 hours)

---

## Rollback Plan

If security fixes cause issues:

1. **Feature Flags:** Add feature flags to enable/disable security fixes
2. **Gradual Rollout:** Roll out fixes gradually to subset of users
3. **Monitoring:** Monitor for errors and security events
4. **Quick Revert:** Have rollback plan ready for each fix

**Rollback Commands:**
```bash
export ENABLE_WEBHOOK_IDEMPOTENCY=false
export ENABLE_WEBHOOK_RATE_LIMITING=false
export ENABLE_WEBHOOK_IP_WHITELIST=false
```

---

## Next Steps

1. Review and approve this security audit
2. Begin Week 1 critical fixes
3. Proceed through remediation timeline
4. Complete security testing
5. Deploy to staging
6. Monitor for issues
7. Deploy to production

---

**End of Phase 12 Security Audit**
