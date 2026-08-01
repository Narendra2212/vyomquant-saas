# Phase 14: Testing Plan
**Verify All Billing Flows**

Generated: 2025-01-08
Project: VyomQuant SaaS
Scope: Comprehensive testing plan for billing system

---

## Executive Summary

This phase defines the testing strategy for all billing flows, including unit tests, integration tests, and end-to-end tests. Testing covers all components identified in Phases 1-13 and ensures the billing system functions correctly after all remediation efforts.

---

## Testing Strategy

### Test Levels

1. **Unit Tests** - Test individual functions and classes
2. **Integration Tests** - Test component interactions
3. **End-to-End Tests** - Test complete user flows
4. **Security Tests** - Test security vulnerabilities
5. **Performance Tests** - Test system under load

### Test Environment

- **Development:** Local testing with mock data
- **Staging:** Pre-production with test payment providers
- **Production:** Monitoring and smoke tests only

---

## Unit Tests

### Entitlement Engine Tests

**File:** `tests/test_entitlement_engine.py`

**Test Cases:**
- [ ] Test PlanMapper.billing_to_tenant() for all plans
- [ ] Test PlanMapper.tenant_to_billing() for all plans
- [ ] Test FeatureEntitlements.is_feature_available() for all features
- [ ] Test FeatureEntitlements.get_required_plan() for all features
- [ ] Test EntitlementEngine.check_feature_entitlement() with all plans
- [ ] Test EntitlementEngine.check_quota_entitlement() with all resources
- [ ] Test EntitlementEngine.get_user_entitlements() returns correct data
- [ ] Test entitlement cache invalidation works correctly

**Coverage Goal:** 90%

---

### Plan Service Tests

**File:** `tests/test_plan_service.py`

**Test Cases:**
- [ ] Test get_plan_config() returns correct plan data
- [ ] Test get_all_plans() returns all active plans
- [ ] Test get_plan_features() returns correct features
- [ ] Test get_plan_limits() returns correct limits
- [ ] Test get_plan_prices() returns correct prices
- [ ] Test migrate_plan_key() maps old keys correctly
- [ ] Test plan caching works correctly

**Coverage Goal:** 85%

---

### Currency Detection Tests

**File:** `tests/test_country_detection.py`

**Test Cases:**
- [ ] Test detect_from_ip() with known IPs
- [ ] Test detect_from_request() with IP header
- [ ] Test detect_from_request() with Accept-Language header
- [ ] Test country_to_currency() mapping
- [ ] Test detect_currency() with user preference
- [ ] Test detect_currency() fallback to default

**Coverage Goal:** 80%

---

### Regional Pricing Tests

**File:** `tests/test_regional_pricing.py`

**Test Cases:**
- [ ] Test get_regional_price() for USD
- [ ] Test get_regional_price() for INR
- [ ] Test get_regional_discount() for India
- [ ] Test get_regional_discount() for other regions
- [ ] Test pricing calculations are correct

**Coverage Goal:** 85%

---

### Billing Router Tests

**File:** `tests/test_billing_router.py`

**Test Cases:**
- [ ] Test checkout session creation for Stripe
- [ ] Test checkout session creation for Razorpay
- [ ] Test checkout with invalid tier
- [ ] Test checkout with invalid currency
- [ ] Test checkout with discount
- [ ] Test Stripe webhook signature verification
- [ ] Test Razorpay webhook signature verification
- [ ] Test webhook idempotency
- [ ] Test webhook processes entitlement correctly
- [ ] Test webhook processes referral commission correctly
- [ ] Test billing portal session creation

**Coverage Goal:** 85%

---

### Dependencies Tests

**File:** `tests/test_dependencies.py`

**Test Cases:**
- [ ] Test get_current_user() with valid token
- [ ] Test get_current_user() with invalid token
- [ ] Test get_current_user() with frozen user
- [ ] Test check_deployment_limit() with free tier
- [ ] Test check_deployment_limit() with pro tier
- [ ] Test check_deployment_limit() with elite tier
- [ ] Test check_deployment_limit() with exceeded limit
- [ ] Test check_ml_build_limit() with free tier
- [ ] Test check_ml_build_limit() with elite tier
- [ ] Test check_ml_build_limit() with exceeded limit
- [ ] Test profile cache hit
- [ ] Test profile cache miss
- [ ] Test profile cache invalidation

**Coverage Goal:** 90%

---

## Integration Tests

### Payment Flow Integration Tests

**File:** `tests/integration/test_payment_flow.py`

**Test Cases:**
- [ ] Test complete Stripe payment flow
- [ ] Test complete Razorpay payment flow
- [ ] Test payment with discount
- [ ] Test payment failure handling
- [ ] Test refund processing
- [ ] Test chargeback processing

**Environment:** Staging with test payment providers

---

### Webhook Integration Tests

**File:** `tests/integration/test_webhooks.py`

**Test Cases:**
- [ ] Test Stripe checkout.session.completed webhook
- [ ] Test Stripe customer.subscription.deleted webhook
- [ ] Test Stripe customer.subscription.updated webhook
- [ ] Test Stripe invoice.payment_failed webhook
- [ ] Test Razorpay payment.captured webhook
- [ ] Test webhook idempotency
- [ ] Test webhook signature verification
- [ ] Test webhook rate limiting

**Environment:** Staging with test payment providers

---

### Entitlement Integration Tests

**File:** `tests/integration/test_entitlement.py`

**Test Cases:**
- [ ] Test feature entitlement check integration
- [ ] Test quota entitlement check integration
- [ ] Test entitlement cache integration
- [ ] Test entitlement with new plan service
- [ ] Test entitlement with database plans

**Environment:** Development with test database

---

### Database Integration Tests

**File:** `tests/integration/test_database.py`

**Test Cases:**
- [ ] Test plan tables CRUD operations
- [ ] Test plan features CRUD operations
- [ ] Test plan limits CRUD operations
- [ ] Test plan prices CRUD operations
- [ ] Test migration mapping operations
- [ ] Test payment tracking CRUD operations
- [ ] Test webhook event logging
- [ ] Test billing audit logging

**Environment:** Development with test database

---

## End-to-End Tests

### User Registration and Onboarding

**File:** `tests/e2e/test_onboarding.py`

**Test Cases:**
- [ ] Test user can register
- [ ] Test user can complete wizard
- [ ] Test user can select plan
- [ ] Test user can complete checkout
- [ ] Test user entitlement granted after payment

**Environment:** Staging

---

### Plan Upgrade Flow

**File:** `tests/e2e/test_upgrade.py`

**Test Cases:**
- [ ] Test free user can upgrade to pro
- [ ] Test pro user can upgrade to business
- [ ] Test business user can upgrade to enterprise
- [ ] Test upgrade with Stripe
- [ ] Test upgrade with Razorpay
- [ ] Test upgrade with discount
- [ ] Test entitlement updated after upgrade

**Environment:** Staging

---

### Plan Downgrade Flow

**File:** `tests/e2e/test_downgrade.py`

**Test Cases:**
- [ ] Test pro user can downgrade to starter
- [ ] Test business user can downgrade to pro
- [ ] Test downgrade with prorated refund
- [ ] Test features removed after downgrade
- [ ] Test quota updated after downgrade

**Environment:** Staging

---

### Plan Cancellation Flow

**File:** `tests/e2e/test_cancellation.py`

**Test Cases:**
- [ ] Test user can cancel subscription
- [ ] Test cancellation grace period
- [ ] Test data retention after cancellation
- [ ] Test user can reactivate subscription
- [ ] Test entitlement removed after cancellation

**Environment:** Staging

---

### ML Addon Purchase Flow

**File:** `tests/e2e/test_ml_addon.py`

**Test Cases:**
- [ ] Test elite user can purchase ML addon
- [ ] Test ML addon counter incremented
- [ ] Test ML addon entitlement granted
- [ ] Test ML addon refund processing

**Environment:** Staging

---

### Referral Commission Flow

**File:** `tests/e2e/test_referral.py`

**Test Cases:**
- [ ] Test referral code generation
- [ ] Test user can apply referral code
- [ ] Test commission granted on payment
- [ ] Test commission wallet updated
- [ ] Test commission payout request
- [ ] Test commission reversal on refund

**Environment:** Staging

---

### Currency Detection Flow

**File:** `tests/e2e/test_currency.py`

**Test Cases:**
- [ ] Test Indian user sees INR pricing
- [ ] Test US user sees USD pricing
- [ ] Test user can manually change currency
- [ ] Test currency preference persists
- [ ] Test checkout uses correct currency

**Environment:** Staging

---

## Security Tests

### Webhook Security Tests

**File:** `tests/security/test_webhook_security.py`

**Test Cases:**
- [ ] Test webhook with invalid signature is rejected
- [ ] Test webhook with missing signature is rejected
- [ ] Test webhook from unauthorized IP is rejected
- [ ] Test webhook with old timestamp is rejected
- [ ] Test replay attack is prevented
- [ ] Test webhook spoofing is prevented

**Environment:** Staging

---

### Entitlement Bypass Tests

**File:** `tests/security/test_entitlement_bypass.py`

**Test Cases:**
- [ ] Test free user cannot access live trading
- [ ] Test free user cannot access ML training
- [ ] Test pro user cannot access ML training
- [ ] Test quota enforcement cannot be bypassed
- [ ] Test frozen user cannot access any features

**Environment:** Staging

---

### Input Validation Tests

**File:** `tests/security/test_input_validation.py`

**Test Cases:**
- [ ] Test invalid tier values are rejected
- [ ] Test invalid currency values are rejected
- [ ] Test SQL injection attempts are blocked
- [ ] Test XSS attempts are blocked
- [ ] Test CSRF attempts are blocked

**Environment:** Development

---

### Rate Limiting Tests

**File:** `tests/security/test_rate_limiting.py`

**Test Cases:**
- [ ] Test webhook rate limiting works
- [ ] Test checkout rate limiting works
- [ ] Test API rate limiting works
- [ ] Test rate limit headers are correct

**Environment:** Staging

---

## Performance Tests

### Load Tests

**File:** `tests/performance/test_load.py`

**Test Cases:**
- [ ] Test checkout endpoint under load (100 req/sec)
- [ ] Test webhook endpoint under load (50 req/sec)
- [ ] Test plan endpoint under load (200 req/sec)
- [ ] Test entitlement checks under load (500 req/sec)
- [ ] Test database queries under load

**Environment:** Staging

---

### Stress Tests

**File:** `tests/performance/test_stress.py`

**Test Cases:**
- [ ] Test system with 1000 concurrent users
- [ ] Test system with 10000 concurrent users
- [ ] Test webhook processing under stress
- [ ] Test database under stress
- [ ] Test Redis under stress

**Environment:** Staging

---

## Test Data

### Test Users

| User ID | Plan | Currency | Purpose |
|---------|------|----------|---------|
| test_user_free | free | USD | Free tier testing |
| test_user_pro | pro | USD | Pro tier testing |
| test_user_business | business | USD | Business tier testing |
| test_user_enterprise | enterprise | USD | Enterprise tier testing |
| test_user_inr | pro | INR | INR pricing testing |

### Test Payment Methods

- Stripe test card: 4242 4242 4242 4242
- Razorpay test card: Available in Razorpay test mode

### Test Referral Codes

- Generated for each test user
- Used for referral commission testing

---

## Test Execution

### Local Testing

```bash
# Run unit tests
pytest tests/unit/ -v

# Run integration tests
pytest tests/integration/ -v

# Run E2E tests
pytest tests/e2e/ -v

# Run security tests
pytest tests/security/ -v

# Run performance tests
pytest tests/performance/ -v

# Run all tests
pytest tests/ -v --cov=backend_app
```

### CI/CD Integration

```yaml
# .github/workflows/test.yml
name: Test Billing System

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run unit tests
        run: pytest tests/unit/ -v
      - name: Run integration tests
        run: pytest tests/integration/ -v
      - name: Run security tests
        run: pytest tests/security/ -v
```

---

## Test Reporting

### Coverage Report

```bash
pytest tests/ --cov=backend_app --cov-report=html --cov-report=term
```

**Target Coverage:**
- Unit tests: 90%
- Integration tests: 80%
- Overall: 85%

### Test Results

**Pass Criteria:**
- All unit tests pass
- All integration tests pass
- All E2E tests pass
- All security tests pass
- Coverage targets met

**Fail Criteria:**
- Any critical test fails
- Coverage below target
- Security test fails

---

## Test Maintenance

### Test Updates

When code changes:
1. Update affected tests
2. Add new tests for new features
3. Remove tests for removed features
4. Update test data

### Test Review

Monthly review:
1. Review test coverage
2. Review test failures
3. Review flaky tests
4. Update test documentation

---

## Next Steps

1. Review and approve this testing plan
2. Set up test environment
3. Implement unit tests
4. Implement integration tests
5. Implement E2E tests
6. Implement security tests
7. Implement performance tests
8. Execute all tests
9. Fix any failures
10. Deploy to staging
11. Run smoke tests in production

---

**End of Phase 14 Testing Plan**
