# Billing System Database Review
**Phase 5: Database Review - Verify Schema and Indexes**

Generated: 2025-01-08
Project: VyomQuant SaaS
Scope: Billing & Subscription System Database Schema

---

## Executive Summary

The billing system uses a hybrid database approach with Supabase as the primary source of truth for subscription state, but retains legacy SQLite models for payment methods. There are no dedicated billing tables for plan configuration, subscription history, or invoice tracking. The schema is poorly normalized with billing state mixed with user profile data.

---

## 1. Database Architecture

### 1.1 Primary Database: Supabase (PostgreSQL)
**Purpose:** Single source of truth for billing state

**Tables Used for Billing:**
- `profiles` - User profiles with billing state
- `referral_codes` - Referral code management
- `referral_relationships` - Referral relationships
- `referral_commissions` - Commission tracking
- `referral_wallets` - Referral earnings wallets
- `referral_payouts` - Payout requests

### 1.2 Secondary Database: SQLite
**Purpose:** Legacy fallback storage (deprecated)

**Tables Used for Billing:**
- `subscriptions` - Subscription records (DEPRECATED)
- `invoices` - Invoice records (DEPRECATED but still used)
- `payment_methods` - Payment method records (STILL IN USE)

---

## 2. Supabase Tables Analysis

### 2.1 profiles Table
**Purpose:** User profiles with billing state

**Columns:**
- `id` (UUID, primary key) - User ID
- `subscription_tier` (VARCHAR) - Current plan (free, pro_999, elite_1999)
- `deployed_bots` (INTEGER) - Bot deployment counter (drifts out of sync)
- `ml_strategies_built` (INTEGER) - ML model counter
- `ml_addons_purchased` (INTEGER) - ML addon counter
- `is_frozen` (BOOLEAN) - Account freeze flag
- `available_discounts` (INTEGER) - Discount counter
- `referral_code` (VARCHAR) - Referral code (backward compatibility)
- `referred_by_user_id` (UUID) - Referrer ID

**Issues:**
- Billing state mixed with profile data
- No foreign key constraints on billing columns
- No validation on subscription_tier values
- Counter columns can drift out of sync
- No audit trail for tier changes
- No indexes on billing columns
- No constraints on counter values (can go negative)

**Indexes:**
- Primary key on `id`
- No indexes on billing-related columns

**Recommendations:**
- Create dedicated billing tables
- Add foreign key constraints
- Add check constraints on subscription_tier
- Add indexes on subscription_tier for queries
- Add audit trail table for tier changes

### 2.2 referral_codes Table
**Purpose:** Store unique referral codes for each user

**Columns:**
- `id` (UUID, primary key)
- `user_id` (UUID, foreign key to profiles)
- `code` (VARCHAR(20), unique)
- `is_active` (BOOLEAN)
- `created_at` (TIMESTAMPTZ)
- `updated_at` (TIMESTAMPTZ)

**Constraints:**
- Unique constraint on `code`
- Unique constraint on `user_id`
- Foreign key to profiles (ON DELETE CASCADE)

**Indexes:**
- `idx_referral_codes_code` on `code`
- `idx_referral_codes_user` on `user_id`

**RLS Policies:**
- Users can view own referral code
- Users can create own referral code

**Issues:**
- None significant

### 2.3 referral_relationships Table
**Purpose:** Track who referred whom

**Columns:**
- `id` (UUID, primary key)
- `referrer_id` (UUID, foreign key to profiles)
- `referred_id` (UUID, foreign key to profiles)
- `referral_code_id` (UUID, foreign key to referral_codes)
- `status` (VARCHAR) - pending, active, cancelled
- `created_at` (TIMESTAMPTZ)
- `updated_at` (TIMESTAMPTZ)

**Constraints:**
- Unique constraint on `referred_id`
- Check constraint: `referrer_id != referred_id` (no self-referral)
- Foreign keys to profiles (ON DELETE CASCADE)
- Foreign key to referral_codes (ON DELETE CASCADE)

**Indexes:**
- `idx_referral_relationships_referrer` on `referrer_id`
- `idx_referral_relationships_referred` on `referred_id`
- `idx_referral_relationships_status` on `status`

**RLS Policies:**
- Users can view relationships where they are referrer or referred

**Issues:**
- None significant

### 2.4 referral_commissions Table
**Purpose:** Track every commission earned from referrals

**Columns:**
- `id` (UUID, primary key)
- `referrer_id` (UUID, foreign key to profiles)
- `referred_id` (UUID, foreign key to profiles)
- `referral_relationship_id` (UUID, foreign key to referral_relationships)
- `payment_id` (VARCHAR(100))
- `subscription_tier` (VARCHAR(50))
- `payment_amount_usd` (DECIMAL(10, 2))
- `commission_rate` (DECIMAL(5, 4), default 0.20)
- `commission_amount_usd` (DECIMAL(10, 2))
- `status` (VARCHAR) - pending, approved, paid, reversed
- `reversal_reason` (TEXT)
- `created_at` (TIMESTAMPTZ)
- `updated_at` (TIMESTAMPTZ)
- `paid_at` (TIMESTAMPTZ)
- `reversed_at` (TIMESTAMPTZ)

**Indexes:**
- `idx_referral_commissions_referrer` on `referrer_id`
- `idx_referral_commissions_referred` on `referred_id`
- `idx_referral_commissions_status` on `status`
- `idx_referral_commissions_payment` on `payment_id`
- `idx_referral_commissions_created` on `created_at DESC`

**RLS Policies:**
- Users can view own commissions

**Issues:**
- No validation on commission_rate
- No cap on total commissions per referrer
- No fraud detection
- Payment amount passed as USD but can be INR (from Razorpay)

### 2.5 referral_wallets Table
**Purpose:** Track referral earnings wallet for each user

**Columns:**
- `id` (UUID, primary key)
- `user_id` (UUID, foreign key to profiles, unique)
- `pending_balance_usd` (DECIMAL(10, 2), default 0.00)
- `approved_balance_usd` (DECIMAL(10, 2), default 0.00)
- `paid_balance_usd` (DECIMAL(10, 2), default 0.00)
- `lifetime_earnings_usd` (DECIMAL(10, 2), default 0.00)
- `created_at` (TIMESTAMPTZ)
- `updated_at` (TIMESTAMPTZ)

**Constraints:**
- Unique constraint on `user_id`
- Check constraint: All balances >= 0

**Indexes:**
- `idx_referral_wallets_user` on `user_id`

**RLS Policies:**
- Users can view own wallet

**Issues:**
- None significant

### 2.6 referral_payouts Table
**Purpose:** Track payout requests and status

**Columns:**
- `id` (UUID, primary key)
- `user_id` (UUID, foreign key to profiles)
- `amount_usd` (DECIMAL(10, 2))
- `status` (VARCHAR) - pending, approved, processing, paid, failed, rejected
- `payment_method` (VARCHAR)
- `payment_details` (JSONB)
- `admin_notes` (TEXT)
- `rejection_reason` (TEXT)
- `created_at` (TIMESTAMPTZ)
- `updated_at` (TIMESTAMPTZ)
- `approved_at` (TIMESTAMPTZ)
- `processing_at` (TIMESTAMPTZ)
- `paid_at` (TIMESTAMPTZ)
- `failed_at` (TIMESTAMPTZ)

**Constraints:**
- Check constraint: `amount_usd > 0`

**Indexes:**
- `idx_referral_payouts_user` on `user_id`
- `idx_referral_payouts_status` on `status`
- `idx_referral_payouts_created` on `created_at DESC`

**RLS Policies:**
- Users can view own payouts

**Issues:**
- None significant

---

## 3. SQLite Tables Analysis

### 3.1 subscriptions Table (DEPRECATED)
**Purpose:** Local fallback subscription storage

**Model:** `SubscriptionModel` (core/models/billing.py)

**Columns:**
- `id` (String(36), primary key)
- `user_id` (String(36), unique, indexed)
- `plan_id` (String(32), default "free")
- `name` (String(64))
- `priceUSD` (Float)
- `priceINR` (Float)
- `features` (JSON)
- `nextBillingDate` (String)
- `autoRenew` (Boolean)
- `created_at` (DateTime)
- `updated_at` (DateTime)

**Issues:**
- DEPRECATED per comments (F-20)
- Should be removed
- No foreign key constraints
- No validation on plan_id
- No indexes on plan_id

**Recommendations:**
- Remove table and model
- Migrate any data to Supabase if needed

### 3.2 invoices Table (DEPRECATED but still used)
**Purpose:** Local fallback invoice storage

**Model:** `InvoiceModel` (core/models/billing.py)

**Columns:**
- `id` (String(36), primary key)
- `user_id` (String(36), indexed)
- `date` (String(64))
- `amtUSD` (Float)
- `amtINR` (Float)
- `status` (String(32), default "pending")
- `created_at` (DateTime)

**Issues:**
- DEPRECATED per comments (F-20)
- BUT still used by user.py endpoint (`GET /billing/invoices`)
- No real invoice data from payment providers
- No foreign key constraints
- No validation on status
- No indexes on status or date

**Recommendations:**
- Create proper invoice table in Supabase
- Migrate invoice data from payment providers
- Remove SQLite table after migration

### 3.3 payment_methods Table (STILL IN USE)
**Purpose:** Local fallback payment method storage

**Model:** `PaymentMethodModel` (core/models/billing.py)

**Columns:**
- `id` (String(36), primary key)
- `user_id` (String(36), indexed)
- `payment_method_id` (String(64))
- `brand` (String(32))
- `last4` (String(4))
- `expiry_month` (Integer)
- `expiry_year` (Integer)
- `is_default` (Boolean)
- `created_at` (String(64))

**Issues:**
- Should use Stripe/Razorpay customer API
- No foreign key constraints
- No validation on expiry dates
- No indexes on is_default
- No audit trail of changes

**Security Concerns:**
- No hardcoded defaults (good - security fix applied)
- Requires real card metadata from frontend (good)

**Recommendations:**
- Migrate to Stripe/Razorpay customer API
- Remove SQLite table after migration
- Add audit trail for payment method changes

---

## 4. Missing Database Components

### 4.1 Plan Configuration Table
**Status:** Not implemented
**Problem:** No database-driven plan configuration

**Missing:**
- `plans` table with plan definitions
- `plan_features` table with feature mappings
- `plan_prices` table with regional pricing
- `plan_limits` table with resource limits

**Impact:**
- Plan definitions hardcoded in code
- Cannot update plans without code deployment
- No versioning of plan changes
- No A/B testing of plans

### 4.2 Subscription History Table
**Status:** Not implemented
**Problem:** No audit trail of subscription changes

**Missing:**
- `subscription_history` table
- Tier change timestamps
- Change reasons
- Change sources (webhook, admin, etc.)

**Impact:**
- No audit trail
- Cannot track subscription lifecycle
- Cannot analyze churn
- Cannot debug billing issues

### 4.3 Invoice Table
**Status:** Partially implemented (deprecated SQLite)
**Problem:** No proper invoice tracking

**Missing:**
- `invoices` table in Supabase
- Invoice PDF generation
- Invoice email delivery
- Invoice payment tracking

**Impact:**
- Users cannot see real payment history
- No invoice management
- No tax calculation
- No compliance tracking

### 4.4 Payment History Table
**Status:** Not implemented
**Problem:** No payment transaction tracking

**Missing:**
- `payments` table
- Payment provider IDs
- Payment statuses
- Payment amounts
- Payment timestamps

**Impact:**
- Cannot track payment lifecycle
- Cannot reconcile payments
- Cannot handle refunds
- Cannot detect fraud

### 4.5 Usage Tracking Tables
**Status:** Not implemented
**Problem:** No usage tracking

**Missing:**
- `usage_metrics` table
- `usage_daily` table
- `usage_monthly` table
- Usage by resource type

**Impact:**
- Cannot track actual usage
- Cannot enforce quotas accurately
- Cannot bill based on usage
- Cannot analyze usage patterns

### 4.6 Billing Audit Log Table
**Status:** Not implemented
**Problem:** No audit trail for billing events

**Missing:**
- `billing_audit_log` table
- Event timestamps
- Event types
- Event sources
- Event details

**Impact:**
- Cannot audit billing operations
- Cannot debug issues
- Cannot detect fraud
- Cannot comply with regulations

---

## 5. Database Functions

### 5.1 generate_unique_referral_code()
**Location:** referral_system_redesign.sql (lines 199-224)

**Purpose:** Generate unique referral code with retry logic

**Logic:**
- Generate code: VQ-XXXXXX (6 random alphanumeric)
- Remove non-alphanumeric characters
- Check if code already exists
- Retry up to 10 times

**Issues:**
- None significant

### 5.2 create_referral_code_for_user()
**Location:** referral_system_redesign.sql (lines 230-252)

**Purpose:** Create referral code for new user

**Logic:**
- Generate unique code
- Insert into referral_codes table
- Update profiles table for backward compatibility
- Create referral wallet

**Issues:**
- Not atomic with profile creation
- No retry on collision

### 5.3 process_referral_commission()
**Location:** referral_system_redesign.sql (lines 258-320)

**Purpose:** Process commission on successful payment

**Logic:**
- Get referrer information from referral_relationships
- Calculate commission (20%)
- Create commission record
- Update relationship status to active
- Update referrer wallet

**Issues:**
- Commission rate hardcoded (20%)
- No validation of payment amount
- No cap on total commissions
- No fraud detection

### 5.4 reverse_referral_commission()
**Location:** referral_system_redesign.sql (lines 326-364)

**Purpose:** Reverse commission on refund or chargeback

**Logic:**
- Find commission by payment_id
- Update commission status to reversed
- Reverse wallet balance (if not already paid)

**Issues:**
- No validation of reversal reason
- No audit trail of reversals

### 5.5 trigger_create_referral_code()
**Location:** referral_system_redesign.sql (lines 369-383)

**Purpose:** Auto-create referral code on user creation

**Logic:**
- Trigger on profiles table AFTER INSERT
- Calls create_referral_code_for_user()

**Issues:**
- None significant

---

## 6. Triggers

### 6.1 tr_profiles_create_referral_code
**Location:** referral_system_redesign.sql (lines 380-383)

**Purpose:** Auto-create referral code on user creation

**Trigger:** AFTER INSERT ON profiles

**Issues:**
- None significant

---

## 7. Index Analysis

### 7.1 Existing Indexes

**referral_codes:**
- `idx_referral_codes_code` on `code` (good)
- `idx_referral_codes_user` on `user_id` (good)

**referral_relationships:**
- `idx_referral_relationships_referrer` on `referrer_id` (good)
- `idx_referral_relationships_referred` on `referred_id` (good)
- `idx_referral_relationships_status` on `status` (good)

**referral_commissions:**
- `idx_referral_commissions_referrer` on `referrer_id` (good)
- `idx_referral_commissions_referred` on `referred_id` (good)
- `idx_referral_commissions_status` on `status` (good)
- `idx_referral_commissions_payment` on `payment_id` (good)
- `idx_referral_commissions_created` on `created_at DESC` (good)

**referral_wallets:**
- `idx_referral_wallets_user` on `user_id` (good)

**referral_payouts:**
- `idx_referral_payouts_user` on `user_id` (good)
- `idx_referral_payouts_status` on `status` (good)
- `idx_referral_payouts_created` on `created_at DESC` (good)

### 7.2 Missing Indexes

**profiles:**
- No index on `subscription_tier` (needed for queries)
- No index on `is_frozen` (needed for queries)
- No composite index on `(subscription_tier, is_frozen)` (needed for admin queries)

**subscriptions (SQLite):**
- No index on `plan_id` (needed for queries)
- No index on `status` (needed for queries)

**invoices (SQLite):**
- No index on `status` (needed for queries)
- No index on `date` (needed for sorting)

**payment_methods (SQLite):**
- No index on `is_default` (needed for queries)

---

## 8. Constraint Analysis

### 8.1 Existing Constraints

**referral_codes:**
- Unique constraint on `code` (good)
- Unique constraint on `user_id` (good)
- Foreign key to profiles (good)

**referral_relationships:**
- Unique constraint on `referred_id` (good)
- Check constraint: `referrer_id != referred_id` (good)
- Foreign keys to profiles (good)
- Foreign key to referral_codes (good)

**referral_commissions:**
- Foreign keys to profiles (good)
- Foreign key to referral_relationships (good)

**referral_wallets:**
- Unique constraint on `user_id` (good)
- Check constraint: All balances >= 0 (good)

**referral_payouts:**
- Check constraint: `amount_usd > 0` (good)

### 8.2 Missing Constraints

**profiles:**
- No check constraint on `subscription_tier` (should be ENUM)
- No check constraint on counters (should be >= 0)
- No foreign key on `referred_by_user_id`

**subscriptions (SQLite):**
- No check constraint on `plan_id`
- No foreign key on `user_id`

**invoices (SQLite):**
- No check constraint on `status`
- No foreign key on `user_id`

**payment_methods (SQLite):**
- No check constraint on expiry dates
- No foreign key on `user_id`

---

## 9. Data Integrity Issues

### 9.1 Counter Drift
**Problem:** Counters in profiles can drift out of sync

**Affected Counters:**
- `deployed_bots` - Never updated (comment says it drifts)
- `ml_strategies_built` - Incremented but never decremented
- `ml_addons_purchased` - Incremented but never decremented
- `available_discounts` - Decremented but implementation incomplete

**Impact:**
- Quota enforcement inaccurate
- Users can run out of slots incorrectly
- No way to recover slots

**Recommendation:**
- Remove `deployed_bots` counter
- Use FleetManager as ground truth
- Implement proper counter synchronization
- Add counter reconciliation

### 9.2 Tier Validation
**Problem:** No validation on subscription_tier values

**Current Values:**
- free, pro_999, elite_1999 (backend billing)
- free, pro, enterprise (backend user)
- FREE, BASIC, PROFESSIONAL, ENTERPRISE (backend tenant)

**Impact:**
- Invalid tier values can be inserted
- Mapping errors possible
- Quota enforcement failures

**Recommendation:**
- Add check constraint with ENUM
- Standardize tier naming
- Add validation in application layer

### 9.3 Currency Validation
**Problem:** No validation on payment amounts

**Issues:**
- Razorpay passes INR as USD to commission function
- No currency tracking in payments
- No conversion rate tracking

**Impact:**
- Incorrect commission calculations
- Financial reporting errors

**Recommendation:**
- Add currency column to payments
- Track conversion rates
- Validate currency consistency

---

## 10. Normalization Issues

### 10.1 First Normal Form (1NF)
**Status:** Mostly compliant
**Issues:**
- `features` column in subscriptions table is JSON (not atomic)

### 10.2 Second Normal Form (2NF)
**Status:** Compliant
**Issues:**
- None significant

### 10.3 Third Normal Form (3NF)
**Status:** Partially compliant
**Issues:**
- Billing state mixed with profile data (not fully normalized)
- Counter columns in profiles (should be separate)

### 10.4 Boyce-Codd Normal Form (BCNF)
**Status:** Partially compliant
**Issues:**
- Multiple candidate keys in some tables
- No proper foreign key constraints

---

## 11. Performance Issues

### 11.1 Missing Indexes
**Impact:**
- Slow queries on subscription_tier
- Slow admin queries on frozen users
- Slow invoice queries

**Recommendation:**
- Add indexes on frequently queried columns
- Add composite indexes for common query patterns
- Analyze query performance

### 11.2 N+1 Query Problem
**Impact:**
- Multiple queries to fetch user + billing data
- No join optimization

**Recommendation:**
- Use joins where appropriate
- Add view for common queries
- Cache frequently accessed data

### 11.3 No Query Optimization
**Impact:**
- No query plan analysis
- No slow query logging
- No performance monitoring

**Recommendation:**
- Enable query logging
- Analyze slow queries
- Optimize frequently used queries

---

## 12. Migration Issues

### 12.1 Legacy Data Migration
**Status:** Partially complete
**Issues:**
- Referral data migrated (referral_system_redesign.sql)
- Billing data not migrated
- No migration plan for subscriptions/invoices

**Recommendation:**
- Create migration plan for billing data
- Migrate subscriptions to Supabase
- Migrate invoices to Supabase
- Remove SQLite tables after migration

### 12.2 Schema Versioning
**Status:** Not implemented
**Issues:**
- No schema version tracking
- No rollback mechanism
- No migration testing

**Recommendation:**
- Implement schema versioning
- Add rollback mechanism
- Add migration testing

---

## 13. Recommendations

### 13.1 Immediate (P0)
1. Create dedicated billing tables in Supabase
2. Add indexes on subscription_tier and is_frozen
3. Add check constraints on subscription_tier
4. Fix counter drift issues
5. Migrate payment methods to Stripe/Razorpay

### 13.2 Short-term (P1)
1. Create plan configuration table
2. Create subscription history table
3. Create invoice table in Supabase
4. Create payment history table
5. Add audit trail table

### 13.3 Long-term (P2)
1. Create usage tracking tables
2. Implement schema versioning
3. Add query optimization
4. Implement data validation
5. Create data reconciliation

---

## 14. Next Steps

**Phase 5: Database Review - Normalize Schema**
- Create normalization plan
- Design new billing tables
- Create migration scripts
- Test migrations

**Phase 6: Feature Gating**
- Build centralized entitlement engine
- Verify every page uses entitlement engine

---

**End of Phase 5 Database Review**
