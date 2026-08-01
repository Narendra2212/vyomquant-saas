# Billing System Testing Plan

## Overview
This document outlines the comprehensive testing plan for the new billing and subscription system. All flows must be tested end-to-end to ensure production readiness.

## Test Environment Setup

### Prerequisites
- Supabase database with schema migrations applied
- Redis server running
- Stripe test account configured
- Razorpay test account configured
- Frontend build with new billing endpoints
- Backend server running with new subscription engine

### Test Users
Create test users for each plan tier:
- `test_free@example.com` - FREE plan
- `test_starter@example.com` - STARTER plan
- `test_pro@example.com` - PRO plan
- `test_enterprise@example.com` - ENTERPRISE plan

## Test Cases

### 1. Plan Upgrade Flow

#### Test 1.1: Free to Starter Upgrade
**Steps:**
1. Login as FREE user
2. Navigate to billing page
3. Select STARTER plan
4. Complete checkout (Stripe USD)
5. Verify plan updated in Supabase
6. Verify cache invalidated
7. Verify WebSocket notification received
8. Verify features unlocked (live trading)
9. Verify quota updated (15 strategies, 2 bots)

**Expected Results:**
- Plan changes from FREE to STARTER
- Invoice created in Supabase
- Cache invalidated
- WebSocket notification sent
- Live trading feature available
- Strategy quota: 15
- Bot quota: 2

#### Test 1.2: Starter to Pro Upgrade
**Steps:**
1. Login as STARTER user
2. Navigate to billing page
3. Select PRO plan
4. Complete checkout (Razorpay INR)
5. Verify plan updated
6. Verify ML training feature unlocked
7. Verify marketplace access unlocked
8. Verify quota updated (30 strategies, 5 bots, 5 ML)

**Expected Results:**
- Plan changes from STARTER to PRO
- ML training feature available
- Marketplace access available
- Strategy quota: 30
- Bot quota: 5
- ML quota: 5

#### Test 1.3: Pro to Enterprise Upgrade
**Steps:**
1. Login as PRO user
2. Navigate to billing page
3. Select ENTERPRISE plan
4. Complete checkout
5. Verify plan updated
6. Verify priority support unlocked
7. Verify unlimited marketplace publishing

**Expected Results:**
- Plan changes from PRO to ENTERPRISE
- Priority support available
- Marketplace publishing: unlimited

### 2. Plan Downgrade Flow

#### Test 2.1: Pro to Starter Downgrade
**Steps:**
1. Login as PRO user
2. Initiate downgrade to STARTER
3. Verify downgrade scheduled for next billing date
4. Verify current plan still PRO
5. Wait for billing date
6. Verify plan downgraded to STARTER
7. Verify ML training feature removed
8. Verify marketplace access removed

**Expected Results:**
- Downgrade scheduled correctly
- Plan changes at next billing date
- Features removed appropriately

#### Test 2.2: Immediate Cancellation
**Steps:**
1. Login as PRO user
2. Cancel subscription immediately
3. Verify plan downgraded to FREE
4. Verify all paid features removed
5. Verify active bots stopped

**Expected Results:**
- Plan changes to FREE immediately
- All paid features removed
- Active bots stopped

### 3. Quota Enforcement

#### Test 3.1: Strategy Save Quota
**Steps:**
1. Login as FREE user (5 strategies)
2. Save 5 strategies
3. Attempt to save 6th strategy
4. Verify error message

**Expected Results:**
- 5 strategies saved successfully
- 6th save blocked with quota error

#### Test 3.2: Bot Deployment Quota
**Steps:**
1. Login as STARTER user (2 bots)
2. Deploy 2 bots
3. Attempt to deploy 3rd bot
4. Verify error message

**Expected Results:**
- 2 bots deployed successfully
- 3rd deployment blocked with quota error

#### Test 3.3: ML Training Quota
**Steps:**
1. Login as PRO user (5 ML trainings)
2. Train 5 ML models
3. Attempt to train 6th model
4. Verify error message

**Expected Results:**
- 5 models trained successfully
- 6th training blocked with quota error

#### Test 3.4: Marketplace Publishing Quota
**Steps:**
1. Login as PRO user (5 marketplace publishes)
2. Publish 5 strategies to marketplace
3. Attempt to publish 6th strategy
4. Verify error message

**Expected Results:**
- 5 strategies published successfully
- 6th publish blocked with quota error

### 4. Feature Gates

#### Test 4.1: Live Trading Gate
**Steps:**
1. Login as FREE user
2. Attempt to deploy bot
3. Verify error message

**Expected Results:**
- Deployment blocked with feature error

#### Test 4.2: ML Training Gate
**Steps:**
1. Login as STARTER user
2. Attempt to train ML model
3. Verify error message

**Expected Results:**
- Training blocked with feature error

#### Test 4.3: Marketplace Access Gate
**Steps:**
1. Login as STARTER user
2. Attempt to access marketplace
3. Verify error message

**Expected Results:**
- Access blocked with feature error

#### Test 4.4: Marketplace Publish Gate
**Steps:**
1. Login as STARTER user
2. Attempt to publish to marketplace
3. Verify error message

**Expected Results:**
- Publish blocked with feature error

### 5. Payment Webhooks

#### Test 5.1: Stripe Checkout Completed
**Steps:**
1. Create Stripe checkout session
2. Complete payment in test mode
3. Verify webhook received
4. Verify plan updated
5. Verify invoice created
6. Verify referral commission processed

**Expected Results:**
- Webhook processed successfully
- Plan updated
- Invoice created
- Referral commission processed

#### Test 5.2: Razorpay Payment Captured
**Steps:**
1. Create Razorpay order
2. Complete payment in test mode
3. Verify webhook received
4. Verify plan updated
5. Verify invoice created

**Expected Results:**
- Webhook processed successfully
- Plan updated
- Invoice created

#### Test 5.3: Payment Failed
**Steps:**
1. Trigger payment failure
2. Verify webhook received
3. Verify account frozen
4. Verify user notified via WebSocket

**Expected Results:**
- Account frozen
- WebSocket notification sent

#### Test 5.4: Webhook Idempotency
**Steps:**
1. Send duplicate webhook event
2. Verify only processed once
3. Verify idempotency key set in Redis

**Expected Results:**
- Duplicate webhook rejected
- Idempotency key set

### 6. Billing Lifecycle

#### Test 6.1: Trial Start
**Steps:**
1. Create new user
2. Start trial
3. Verify trial end date set
4. Verify STARTER features available
5. Wait for trial expiry
6. Verify downgraded to FREE

**Expected Results:**
- Trial started correctly
- Features available during trial
- Downgraded after expiry

#### Test 6.2: Subscription Renewal
**Steps:**
1. Setup recurring payment
2. Wait for billing date
3. Verify payment processed
4. Verify plan renewed
5. Verify next billing date updated

**Expected Results:**
- Payment processed
- Plan renewed
- Billing date updated

#### Test 6.3: Grace Period
**Steps:**
1. Cancel subscription at period end
2. Wait for billing date
3. Verify in grace period
4. Verify features still available
5. Wait for grace period expiry
6. Verify downgraded to FREE

**Expected Results:**
- Grace period activated
- Features available during grace
- Downgraded after grace expiry

### 7. Country-Aware Pricing

#### Test 7.1: INR User Detection
**Steps:**
1. Set user locale to hi-IN
2. Navigate to billing page
3. Verify prices shown in INR
4. Complete checkout in INR

**Expected Results:**
- Prices displayed in INR
- Checkout uses Razorpay

#### Test 7.2: USD User Detection
**Steps:**
1. Set user locale to en-US
2. Navigate to billing page
3. Verify prices shown in USD
4. Complete checkout in USD

**Expected Results:**
- Prices displayed in USD
- Checkout uses Stripe

#### Test 7.3: Currency Preference Override
**Steps:**
1. Set user currency preference to USD
2. Navigate to billing page
3. Verify prices shown in USD regardless of locale

**Expected Results:**
- User preference overrides locale detection

### 8. Realtime Synchronization

#### Test 8.1: WebSocket Notification
**Steps:**
1. Complete payment
2. Monitor WebSocket connection
3. Verify subscription update notification received

**Expected Results:**
- WebSocket notification received immediately

#### Test 8.2: Cache Invalidation
**Steps:**
1. Load user entitlements (cached)
2. Complete payment
3. Load user entitlements again
4. Verify fresh data fetched

**Expected Results:**
- Cache invalidated after payment
- Fresh data fetched on next request

#### Test 8.3: Quota Reached Notification
**Steps:**
1. Reach quota limit
2. Verify WebSocket notification sent
3. Verify error message displayed

**Expected Results:**
- WebSocket notification sent
- Error message displayed

### 9. Database Integrity

#### Test 9.1: Subscription State
**Steps:**
1. Check profiles table
2. Verify subscription_tier correct
3. Verify subscription_status correct
4. Verify next_billing_date correct

**Expected Results:**
- All subscription fields correct

#### Test 9.2: Invoice Records
**Steps:**
1. Check invoices table
2. Verify invoice created
3. Verify amount correct
4. Verify status correct

**Expected Results:**
- Invoice record correct

#### Test 9.3: Billing History
**Steps:**
1. Check billing_history table
2. Verify event logged
3. Verify metadata correct

**Expected Results:**
- History record correct

### 10. Security Tests

#### Test 10.1: Webhook Signature Validation
**Steps:**
1. Send webhook with invalid signature
2. Verify rejected

**Expected Results:**
- Invalid webhook rejected

#### Test 10.2: Plan Modification Protection
**Steps:**
1. Attempt to update subscription_tier via API
2. Verify blocked

**Expected Results:**
- Direct plan update blocked

#### Test 10.3: Frozen Account Enforcement
**Steps:**
1. Freeze account
2. Attempt to deploy bot
3. Verify blocked

**Expected Results:**
- Frozen account blocked

## Test Execution Checklist

- [ ] All upgrade flows tested
- [ ] All downgrade flows tested
- [ ] All quota limits tested
- [ ] All feature gates tested
- [ ] All webhooks tested
- [ ] All lifecycle events tested
- [ ] All pricing scenarios tested
- [ ] All sync mechanisms tested
- [ ] Database integrity verified
- [ ] Security measures verified

## Sign-off

**Tester:** _______________
**Date:** _______________
**Status:** _______________
