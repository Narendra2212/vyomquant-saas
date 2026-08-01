-- ═══════════════════════════════════════════════════════════════════════════
-- MIGRATION: Create Plan Configuration Tables
-- Purpose: Database-driven plan configuration for billing system
-- Phase 7.1: Database Schema Creation
-- ═══════════════════════════════════════════════════════════════════════════

-- TABLE: plans
CREATE TABLE IF NOT EXISTS plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT true,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plans_key ON plans(key);

-- TABLE: plan_features
CREATE TABLE IF NOT EXISTS plan_features (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id UUID REFERENCES plans(id) ON DELETE CASCADE,
    feature_key VARCHAR(100) NOT NULL,
    is_enabled BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(plan_id, feature_key)
);

CREATE INDEX IF NOT EXISTS idx_plan_features_plan ON plan_features(plan_id);

-- TABLE: plan_limits
CREATE TABLE IF NOT EXISTS plan_limits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id UUID REFERENCES plans(id) ON DELETE CASCADE,
    resource_type VARCHAR(100) NOT NULL,
    limit_value INTEGER NOT NULL,
    limit_type VARCHAR(20) DEFAULT 'hard',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(plan_id, resource_type)
);

CREATE INDEX IF NOT EXISTS idx_plan_limits_plan ON plan_limits(plan_id);

-- TABLE: plan_prices
CREATE TABLE IF NOT EXISTS plan_prices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id UUID REFERENCES plans(id) ON DELETE CASCADE,
    currency VARCHAR(3) NOT NULL,
    billing_cycle VARCHAR(20) NOT NULL,
    price_cents INTEGER NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(plan_id, currency, billing_cycle)
);

CREATE INDEX IF NOT EXISTS idx_plan_prices_plan ON plan_prices(plan_id);

-- TABLE: plan_migration_mapping
CREATE TABLE IF NOT EXISTS plan_migration_mapping (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    old_plan_key VARCHAR(50) NOT NULL,
    new_plan_key VARCHAR(50) NOT NULL,
    migration_date TIMESTAMPTZ DEFAULT NOW(),
    is_active BOOLEAN DEFAULT true,
    UNIQUE(old_plan_key)
);

CREATE INDEX IF NOT EXISTS idx_plan_migration_old ON plan_migration_mapping(old_plan_key);

-- Insert default plans
INSERT INTO plans (key, name, description, sort_order) VALUES
    ('starter', 'Starter', 'Perfect for getting started', 1),
    ('pro', 'Pro', 'For serious traders', 2),
    ('business', 'Business', 'For teams scaling up', 3),
    ('enterprise', 'Enterprise', 'Custom solutions', 4)
ON CONFLICT (key) DO NOTHING;

-- Insert migration mapping
INSERT INTO plan_migration_mapping (old_plan_key, new_plan_key) VALUES
    ('free', 'starter'),
    ('pro', 'pro'),
    ('pro_999', 'pro'),
    ('elite', 'business'),
    ('elite_1999', 'business'),
    ('enterprise', 'enterprise'),
    ('BASIC', 'starter'),
    ('PROFESSIONAL', 'pro'),
    ('ENTERPRISE', 'enterprise')
ON CONFLICT (old_plan_key) DO NOTHING;
