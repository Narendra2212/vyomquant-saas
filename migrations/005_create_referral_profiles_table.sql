-- Migration: 005_create_referral_profiles_table.sql
-- Purpose: Create referral_profiles table for user referral tracking
-- Author: Principal Software Architect
-- Date: 2025-08-02

BEGIN;

-- ══════════════════════════════════════════════════════════════════════════
-- CREATE TABLE: referral_profiles
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS referral_profiles (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL UNIQUE,
    referral_code VARCHAR(20) NOT NULL UNIQUE,
    total_referrals INTEGER DEFAULT 0,
    active_referrals INTEGER DEFAULT 0,
    pending_earnings DECIMAL(20, 8) DEFAULT 0.0,
    approved_earnings DECIMAL(20, 8) DEFAULT 0.0,
    paid_earnings DECIMAL(20, 8) DEFAULT 0.0,
    lifetime_earnings DECIMAL(20, 8) DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Foreign key to profiles table
    CONSTRAINT fk_referral_profiles_user_id 
        FOREIGN KEY (user_id) 
        REFERENCES profiles(id) 
        ON DELETE CASCADE
);

-- ══════════════════════════════════════════════════════════════════════════
-- INDEXES
-- ══════════════════════════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_referral_profiles_user_id ON referral_profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_referral_profiles_referral_code ON referral_profiles(referral_code);
CREATE INDEX IF NOT EXISTS idx_referral_profiles_total_referrals ON referral_profiles(total_referrals DESC);
CREATE INDEX IF NOT EXISTS idx_referral_profiles_created_at ON referral_profiles(created_at DESC);

-- ══════════════════════════════════════════════════════════════════════════
-- ROW LEVEL SECURITY
-- ══════════════════════════════════════════════════════════════════════════

ALTER TABLE referral_profiles ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only access their own referral profile
CREATE POLICY "referral_profiles_authenticated_owner" ON referral_profiles
    FOR ALL
    TO authenticated
    USING (auth.uid()::text = user_id)
    WITH CHECK (auth.uid()::text = user_id);

-- ══════════════════════════════════════════════════════════════════════════
-- TRIGGERS
-- ══════════════════════════════════════════════════════════════════════════

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_referral_profiles_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_referral_profiles_updated_at ON referral_profiles;
CREATE TRIGGER trg_referral_profiles_updated_at
    BEFORE UPDATE ON referral_profiles
    FOR EACH ROW
    EXECUTE FUNCTION update_referral_profiles_updated_at();

-- ══════════════════════════════════════════════════════════════════════════
-- COMMENTS
-- ══════════════════════════════════════════════════════════════════════════

COMMENT ON TABLE referral_profiles IS 'User referral profile tracking for dashboard aggregation';
COMMENT ON COLUMN referral_profiles.referral_code IS 'Unique referral code for sharing';
COMMENT ON COLUMN referral_profiles.total_referrals IS 'Total number of users referred';
COMMENT ON COLUMN referral_profiles.active_referrals IS 'Number of active referred users';
COMMENT ON COLUMN referral_profiles.pending_earnings IS 'Earnings pending approval';
COMMENT ON COLUMN referral_profiles.approved_earnings IS 'Earnings approved for payout';
COMMENT ON COLUMN referral_profiles.paid_earnings IS 'Earnings already paid out';
COMMENT ON COLUMN referral_profiles.lifetime_earnings IS 'Total lifetime earnings';

COMMIT;
