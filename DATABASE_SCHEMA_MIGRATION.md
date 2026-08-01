# Database Schema Migration for Billing System

## Overview
This document outlines the required Supabase database schema changes to support the new centralized billing system.

## Current State
- `profiles` table is the source of truth for subscription state
- `invoices` table exists in Supabase (not SQLite)
- `library_subscriptions` table for marketplace subscriptions
- No dedicated quota tracking table (using Redis)

## Required Schema Changes

### 1. profiles table - Add new columns

```sql
-- Add billing-related columns to profiles table
ALTER TABLE profiles 
ADD COLUMN IF NOT EXISTS subscription_status TEXT DEFAULT 'active' CHECK (subscription_status IN ('trial', 'active', 'past_due', 'cancelled', 'expired', 'grace_period')),
ADD COLUMN IF NOT EXISTS trial_end_date TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS next_billing_date TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS cancel_at_period_end BOOLEAN DEFAULT false,
ADD COLUMN IF NOT EXISTS pending_downgrade_tier TEXT,
ADD COLUMN IF NOT EXISTS preferred_currency TEXT DEFAULT 'USD' CHECK (preferred_currency IN ('USD', 'INR')),
ADD COLUMN IF NOT EXISTS billing_currency TEXT DEFAULT 'USD' CHECK (billing_currency IN ('USD', 'INR')),
ADD COLUMN IF NOT EXISTS payment_provider TEXT,
ADD COLUMN IF NOT EXISTS payment_id TEXT,
ADD COLUMN IF NOT EXISTS is_frozen BOOLEAN DEFAULT false;

-- Add indexes for performance
CREATE INDEX IF NOT EXISTS idx_profiles_subscription_status ON profiles(subscription_status);
CREATE INDEX IF NOT EXISTS idx_profiles_next_billing_date ON profiles(next_billing_date);
CREATE INDEX IF NOT EXISTS idx_profiles_trial_end_date ON profiles(trial_end_date);
```

### 2. invoices table - Ensure proper schema

```sql
-- Ensure invoices table exists with proper schema
CREATE TABLE IF NOT EXISTS invoices (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    amount_usd DECIMAL(10, 2) NOT NULL DEFAULT 0,
    amount_inr DECIMAL(10, 2) NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD' CHECK (currency IN ('USD', 'INR')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'failed', 'refunded')),
    payment_provider TEXT,
    payment_id TEXT,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add indexes
CREATE INDEX IF NOT EXISTS idx_invoices_user_id ON invoices(user_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoices_created_at ON invoices(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_invoices_payment_id ON invoices(payment_id);
```

### 3. billing_history table - New table for audit trail

```sql
-- Create billing history table for audit trail
CREATE TABLE IF NOT EXISTS billing_history (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (event_type IN ('trial_started', 'subscription_activated', 'subscription_renewed', 'subscription_cancelled', 'subscription_downgraded', 'subscription_upgraded', 'payment_failed', 'payment_refunded')),
    previous_plan TEXT,
    new_plan TEXT,
    amount DECIMAL(10, 2),
    currency TEXT,
    payment_provider TEXT,
    payment_id TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add indexes
CREATE INDEX IF NOT EXISTS idx_billing_history_user_id ON billing_history(user_id);
CREATE INDEX IF NOT EXISTS idx_billing_history_event_type ON billing_history(event_type);
CREATE INDEX IF NOT EXISTS idx_billing_history_created_at ON billing_history(created_at DESC);
```

### 4. quota_usage table - New table for persistent quota tracking

```sql
-- Create quota usage table for persistent tracking (Redis is primary, this is backup)
CREATE TABLE IF NOT EXISTS quota_usage (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    resource TEXT NOT NULL CHECK (resource IN ('strategies', 'bots', 'ml_trainings', 'marketplace_published')),
    usage_count INTEGER NOT NULL DEFAULT 0,
    period_start TIMESTAMPTZ NOT NULL DEFAULT DATE_TRUNC('month', NOW()),
    period_end TIMESTAMPTZ NOT NULL DEFAULT DATE_TRUNC('month', NOW() + INTERVAL '1 month'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, resource, period_start)
);

-- Add indexes
CREATE INDEX IF NOT EXISTS idx_quota_usage_user_id ON quota_usage(user_id);
CREATE INDEX IF NOT EXISTS idx_quota_usage_resource ON quota_usage(resource);
CREATE INDEX IF NOT EXISTS idx_quota_usage_period ON quota_usage(period_start, period_end);
```

### 5. payment_methods table - Update schema

```sql
-- Update payment_methods table (if exists)
CREATE TABLE IF NOT EXISTS payment_methods (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('stripe', 'razorpay')),
    provider_payment_method_id TEXT NOT NULL,
    brand TEXT,
    last4 TEXT,
    expiry_month INTEGER,
    expiry_year INTEGER,
    is_default BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add indexes
CREATE INDEX IF NOT EXISTS idx_payment_methods_user_id ON payment_methods(user_id);
CREATE INDEX IF NOT EXISTS idx_payment_methods_provider ON payment_methods(provider);
CREATE INDEX IF NOT EXISTS idx_payment_methods_is_default ON payment_methods(user_id, is_default);
```

## Row Level Security (RLS) Policies

```sql
-- Enable RLS on all billing tables
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE quota_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_methods ENABLE ROW LEVEL SECURITY;

-- Profiles: Users can read their own, service role can write
CREATE POLICY "Users can read own profile" ON profiles
    FOR SELECT USING (auth.uid() = id);

CREATE POLICY "Service role can update profiles" ON profiles
    FOR UPDATE USING (auth.role() = 'service_role');

-- Invoices: Users can read their own
CREATE POLICY "Users can read own invoices" ON invoices
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Service role can insert invoices" ON invoices
    FOR INSERT WITH CHECK (auth.role() = 'service_role');

-- Billing history: Users can read their own
CREATE POLICY "Users can read own billing history" ON billing_history
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Service role can insert billing history" ON billing_history
    FOR INSERT WITH CHECK (auth.role() = 'service_role');

-- Quota usage: Users can read their own
CREATE POLICY "Users can read own quota usage" ON quota_usage
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Service role can upsert quota usage" ON quota_usage
    FOR ALL USING (auth.role() = 'service_role');

-- Payment methods: Users can read their own
CREATE POLICY "Users can read own payment methods" ON payment_methods
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Service role can manage payment methods" ON payment_methods
    FOR ALL USING (auth.role() = 'service_role');
```

## Functions

```sql
-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Add triggers for updated_at
CREATE TRIGGER update_invoices_updated_at BEFORE UPDATE ON invoices
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_payment_methods_updated_at BEFORE UPDATE ON payment_methods
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_quota_usage_updated_at BEFORE UPDATE ON quota_usage
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Function to check and expire trials
CREATE OR REPLACE FUNCTION check_trial_expiry()
RETURNS void AS $$
BEGIN
    UPDATE profiles
    SET subscription_status = 'cancelled',
        subscription_tier = 'free',
        trial_end_date = NULL,
        next_billing_date = NULL
    WHERE subscription_status = 'trial'
      AND trial_end_date < NOW();
END;
$$ LANGUAGE plpgsql;

-- Function to check subscription expiry and handle grace period
CREATE OR REPLACE FUNCTION check_subscription_expiry()
RETURNS void AS $$
BEGIN
    -- Move expired subscriptions to grace period
    UPDATE profiles
    SET subscription_status = 'grace_period'
    WHERE subscription_status = 'active'
      AND cancel_at_period_end = true
      AND next_billing_date < NOW()
      AND next_billing_date + INTERVAL '7 days' >= NOW();

    -- Downgrade grace period expired subscriptions
    UPDATE profiles
    SET subscription_status = 'cancelled',
        subscription_tier = 'free',
        next_billing_date = NULL,
        cancel_at_period_end = false
    WHERE subscription_status = 'grace_period'
      AND next_billing_date + INTERVAL '7 days' < NOW();
END;
$$ LANGUAGE plpgsql;
```

## Migration Steps

1. Run the schema changes in Supabase SQL editor
2. Verify all tables and indexes are created
3. Test RLS policies
4. Verify functions work correctly
5. Back up existing data before migration

## Rollback Plan

If issues occur, rollback by:
1. Dropping new tables: `billing_history`, `quota_usage`
2. Removing new columns from `profiles` table
3. Restoring from backup if needed
