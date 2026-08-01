-- Risk Settings Database Schema Migration
-- Creates tables for user risk configuration and audit trail
-- Date: 2026-08-01

BEGIN;

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 1: Create risk_settings table
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS risk_settings (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    max_daily_loss DECIMAL(10, 2) NOT NULL DEFAULT 500.00,
    max_positions INTEGER NOT NULL DEFAULT 10,
    max_leverage INTEGER NOT NULL DEFAULT 3,
    kill_switches JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id)
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_risk_settings_user_id ON risk_settings(user_id);

-- Enable RLS
ALTER TABLE risk_settings ENABLE ROW LEVEL SECURITY;

-- RLS Policies: Users can only access their own risk settings
CREATE POLICY "risk_settings_authenticated_owner" ON risk_settings
    FOR ALL
    TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 2: Create strategy_limits table
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS strategy_limits (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    strategy_id TEXT NOT NULL,
    max_position_size DECIMAL(10, 2) NOT NULL DEFAULT 1000.00,
    max_daily_trades INTEGER NOT NULL DEFAULT 100,
    allowed_symbols TEXT[] DEFAULT '{}',
    max_drawdown_pct DECIMAL(5, 4) NOT NULL DEFAULT 0.1000,
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, strategy_id)
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_strategy_limits_user_id ON strategy_limits(user_id);
CREATE INDEX IF NOT EXISTS idx_strategy_limits_strategy_id ON strategy_limits(strategy_id);

-- Enable RLS
ALTER TABLE strategy_limits ENABLE ROW LEVEL SECURITY;

-- RLS Policies: Users can only access their own strategy limits
CREATE POLICY "strategy_limits_authenticated_owner" ON strategy_limits
    FOR ALL
    TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 3: Create risk_settings_audit table for audit trail
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS risk_settings_audit (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (event_type IN ('created', 'updated', 'kill_switch_activated', 'kill_switch_deactivated')),
    previous_values JSONB,
    new_values JSONB,
    changed_by TEXT NOT NULL, -- auth.uid() of the user who made the change
    ip_address TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_risk_settings_audit_user_id ON risk_settings_audit(user_id);
CREATE INDEX IF NOT EXISTS idx_risk_settings_audit_event_type ON risk_settings_audit(event_type);
CREATE INDEX IF NOT EXISTS idx_risk_settings_audit_created_at ON risk_settings_audit(created_at DESC);

-- Enable RLS
ALTER TABLE risk_settings_audit ENABLE ROW LEVEL SECURITY;

-- RLS Policies: Users can only read their own audit logs, service role can write
CREATE POLICY "risk_settings_audit_read_own" ON risk_settings_audit
    FOR SELECT
    TO authenticated
    USING (auth.uid() = user_id);

CREATE POLICY "risk_settings_audit_insert_service" ON risk_settings_audit
    FOR INSERT
    TO service_role
    WITH CHECK (true);

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 4: Create trigger for automatic updated_at and audit logging
-- ══════════════════════════════════════════════════════════════════════════

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply trigger to risk_settings
DROP TRIGGER IF EXISTS update_risk_settings_updated_at ON risk_settings;
CREATE TRIGGER update_risk_settings_updated_at
    BEFORE UPDATE ON risk_settings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Apply trigger to strategy_limits
DROP TRIGGER IF EXISTS update_strategy_limits_updated_at ON strategy_limits;
CREATE TRIGGER update_strategy_limits_updated_at
    BEFORE UPDATE ON strategy_limits
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Function to log risk settings changes to audit table
CREATE OR REPLACE FUNCTION log_risk_settings_changes()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO risk_settings_audit (
        user_id,
        event_type,
        previous_values,
        new_values,
        changed_by,
        metadata
    )
    VALUES (
        NEW.user_id,
        'updated',
        row_to_json(OLD)::jsonb,
        row_to_json(NEW)::jsonb,
        auth.uid(),
        jsonb_build_object('table', 'risk_settings')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply audit trigger to risk_settings
DROP TRIGGER IF EXISTS log_risk_settings_changes_trigger ON risk_settings;
CREATE TRIGGER log_risk_settings_changes_trigger
    AFTER UPDATE ON risk_settings
    FOR EACH ROW
    EXECUTE FUNCTION log_risk_settings_changes();

COMMIT;
