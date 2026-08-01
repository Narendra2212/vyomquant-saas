-- ═══════════════════════════════════════════════════════════════════════════
-- REFERRAL SYSTEM REDESIGN - NORMALIZED SCHEMA
-- ═══════════════════════════════════════════════════════════════════════════
-- Migration: 001_referral_system_redesign
-- Description: Complete redesign of referral system with proper normalization
-- Author: Principal Database Architect
-- Date: 2026-08-01
-- ═══════════════════════════════════════════════════════════════════════════

-- ═══════════════════════════════════════════════════════════════════════════
-- TABLE: referral_codes
-- Purpose: Store unique referral codes for each user
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS referral_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    code VARCHAR(20) NOT NULL UNIQUE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT uq_referral_codes_user UNIQUE (user_id)
);

-- Index for fast code lookup
CREATE INDEX idx_referral_codes_code ON referral_codes(code);
CREATE INDEX idx_referral_codes_user ON referral_codes(user_id);

-- RLS Policy: Users can only see their own referral code
ALTER TABLE referral_codes ENABLE ROW LEVEL SECURITY;

CREATE POLICY referral_codes_select_own ON referral_codes
    FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

CREATE POLICY referral_codes_insert_own ON referral_codes
    FOR INSERT
    TO authenticated
    WITH CHECK (user_id = auth.uid());

-- ═══════════════════════════════════════════════════════════════════════════
-- TABLE: referral_relationships
-- Purpose: Track who referred whom (one-time relationship)
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS referral_relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referrer_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    referred_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    referral_code_id UUID NOT NULL REFERENCES referral_codes(id) ON DELETE CASCADE,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'cancelled')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT uq_referral_relationships_referred UNIQUE (referred_id),
    CONSTRAINT chk_no_self_referral CHECK (referrer_id != referred_id)
);

-- Indexes for relationship queries
CREATE INDEX idx_referral_relationships_referrer ON referral_relationships(referrer_id);
CREATE INDEX idx_referral_relationships_referred ON referral_relationships(referred_id);
CREATE INDEX idx_referral_relationships_status ON referral_relationships(status);

-- RLS Policy: Users can see relationships where they are referrer or referred
ALTER TABLE referral_relationships ENABLE ROW LEVEL SECURITY;

CREATE POLICY referral_relationships_select_own ON referral_relationships
    FOR SELECT
    TO authenticated
    USING (referrer_id = auth.uid() OR referred_id = auth.uid());

-- ═══════════════════════════════════════════════════════════════════════════
-- TABLE: referral_commissions
-- Purpose: Track every commission earned from referrals
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS referral_commissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referrer_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    referred_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    referral_relationship_id UUID NOT NULL REFERENCES referral_relationships(id) ON DELETE CASCADE,
    payment_id VARCHAR(100) NOT NULL,
    subscription_tier VARCHAR(50) NOT NULL,
    payment_amount_usd DECIMAL(10, 2) NOT NULL,
    commission_rate DECIMAL(5, 4) DEFAULT 0.20, -- 20%
    commission_amount_usd DECIMAL(10, 2) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'paid', 'reversed')),
    reversal_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    paid_at TIMESTAMPTZ,
    reversed_at TIMESTAMPTZ
);

-- Indexes for commission queries
CREATE INDEX idx_referral_commissions_referrer ON referral_commissions(referrer_id);
CREATE INDEX idx_referral_commissions_referred ON referral_commissions(referred_id);
CREATE INDEX idx_referral_commissions_status ON referral_commissions(status);
CREATE INDEX idx_referral_commissions_payment ON referral_commissions(payment_id);
CREATE INDEX idx_referral_commissions_created ON referral_commissions(created_at DESC);

-- RLS Policy: Users can only see their own commissions
ALTER TABLE referral_commissions ENABLE ROW LEVEL SECURITY;

CREATE POLICY referral_commissions_select_own ON referral_commissions
    FOR SELECT
    TO authenticated
    USING (referrer_id = auth.uid());

-- ═══════════════════════════════════════════════════════════════════════════
-- TABLE: referral_wallets
-- Purpose: Track referral earnings wallet for each user
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS referral_wallets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    pending_balance_usd DECIMAL(10, 2) DEFAULT 0.00,
    approved_balance_usd DECIMAL(10, 2) DEFAULT 0.00,
    paid_balance_usd DECIMAL(10, 2) DEFAULT 0.00,
    lifetime_earnings_usd DECIMAL(10, 2) DEFAULT 0.00,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT uq_referral_wallets_user UNIQUE (user_id),
    CONSTRAINT chk_non_negative_balances CHECK (
        pending_balance_usd >= 0 AND 
        approved_balance_usd >= 0 AND 
        paid_balance_usd >= 0 AND 
        lifetime_earnings_usd >= 0
    )
);

-- Index for wallet lookups
CREATE INDEX idx_referral_wallets_user ON referral_wallets(user_id);

-- RLS Policy: Users can only see their own wallet
ALTER TABLE referral_wallets ENABLE ROW LEVEL SECURITY;

CREATE POLICY referral_wallets_select_own ON referral_wallets
    FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

-- ═══════════════════════════════════════════════════════════════════════════
-- TABLE: referral_payouts
-- Purpose: Track payout requests and status
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS referral_payouts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    amount_usd DECIMAL(10, 2) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'processing', 'paid', 'failed', 'rejected')),
    payment_method VARCHAR(50),
    payment_details JSONB,
    admin_notes TEXT,
    rejection_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    approved_at TIMESTAMPTZ,
    processing_at TIMESTAMPTZ,
    paid_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    
    CONSTRAINT chk_positive_payout CHECK (amount_usd > 0)
);

-- Indexes for payout queries
CREATE INDEX idx_referral_payouts_user ON referral_payouts(user_id);
CREATE INDEX idx_referral_payouts_status ON referral_payouts(status);
CREATE INDEX idx_referral_payouts_created ON referral_payouts(created_at DESC);

-- RLS Policy: Users can only see their own payouts
ALTER TABLE referral_payouts ENABLE ROW LEVEL SECURITY;

CREATE POLICY referral_payouts_select_own ON referral_payouts
    FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

-- ═══════════════════════════════════════════════════════════════════════════
-- MIGRATION: Add referral_code to profiles table for backward compatibility
-- ═══════════════════════════════════════════════════════════════════════════
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS referral_code VARCHAR(20);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS referred_by_user_id UUID;

-- Add foreign key constraint
ALTER TABLE profiles 
    ADD CONSTRAINT fk_profiles_referred_by 
    FOREIGN KEY (referred_by_user_id) 
    REFERENCES profiles(id) 
    ON DELETE SET NULL;

-- Add unique constraint for referral_code
ALTER TABLE profiles ADD CONSTRAINT uq_profiles_referral_code UNIQUE (referral_code);

-- ═══════════════════════════════════════════════════════════════════════════
-- FUNCTION: generate_unique_referral_code
-- Purpose: Generate a unique referral code with retry logic
-- ═══════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION generate_unique_referral_code()
RETURNS VARCHAR(20) AS $$
DECLARE
    code VARCHAR(20);
    max_attempts INTEGER := 10;
    attempt INTEGER := 0;
BEGIN
    WHILE attempt < max_attempts LOOP
        attempt := attempt + 1;
        -- Generate code: VQ-XXXXXX (6 random alphanumeric)
        code := 'VQ-' || upper(substring(encode(gen_random_bytes(4), 'base64'), 1, 6));
        
        -- Remove non-alphanumeric characters
        code := regexp_replace(code, '[^A-Z0-9]', '', 'g');
        code := 'VQ-' || substring(code, 4, 6);
        
        -- Check if code already exists
        IF NOT EXISTS (SELECT 1 FROM referral_codes WHERE code = code) AND
           NOT EXISTS (SELECT 1 FROM profiles WHERE referral_code = code) THEN
            RETURN code;
        END IF;
    END LOOP;
    
    RAISE EXCEPTION 'Failed to generate unique referral code after % attempts', max_attempts;
END;
$$ LANGUAGE plpgsql;

-- ═══════════════════════════════════════════════════════════════════════════
-- FUNCTION: create_referral_code_for_user
-- Purpose: Create referral code for a new user
-- ═══════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION create_referral_code_for_user(user_uuid UUID)
RETURNS VOID AS $$
DECLARE
    new_code VARCHAR(20);
BEGIN
    -- Generate unique code
    new_code := generate_unique_referral_code();
    
    -- Insert into referral_codes table
    INSERT INTO referral_codes (user_id, code)
    VALUES (user_uuid, new_code);
    
    -- Update profiles table for backward compatibility
    UPDATE profiles 
    SET referral_code = new_code 
    WHERE id = user_uuid;
    
    -- Create referral wallet
    INSERT INTO referral_wallets (user_id)
    VALUES (user_uuid)
    ON CONFLICT (user_id) DO NOTHING;
END;
$$ LANGUAGE plpgsql;

-- ═══════════════════════════════════════════════════════════════════════════
-- FUNCTION: process_referral_commission
-- Purpose: Create commission record and update wallet on successful payment
-- ═══════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION process_referral_commission(
    p_referred_id UUID,
    p_payment_id VARCHAR(100),
    p_subscription_tier VARCHAR(50),
    p_payment_amount_usd DECIMAL(10, 2)
)
RETURNS VOID AS $$
DECLARE
    v_referrer_id UUID;
    v_relationship_id UUID;
    v_referral_code_id UUID;
    v_commission_amount DECIMAL(10, 2);
BEGIN
    -- Get referrer information
    SELECT referrer_id, id, referral_code_id
    INTO v_referrer_id, v_relationship_id, v_referral_code_id
    FROM referral_relationships
    WHERE referred_id = p_referred_id AND status = 'pending'
    LIMIT 1;
    
    IF v_referrer_id IS NULL THEN
        RETURN; -- No pending referral found
    END IF;
    
    -- Calculate commission (20%)
    v_commission_amount := p_payment_amount_usd * 0.20;
    
    -- Create commission record
    INSERT INTO referral_commissions (
        referrer_id,
        referred_id,
        referral_relationship_id,
        payment_id,
        subscription_tier,
        payment_amount_usd,
        commission_amount_usd,
        status
    ) VALUES (
        v_referrer_id,
        p_referred_id,
        v_relationship_id,
        p_payment_id,
        p_subscription_tier,
        p_payment_amount_usd,
        v_commission_amount,
        'pending'
    );
    
    -- Update referral relationship to active
    UPDATE referral_relationships
    SET status = 'active', updated_at = NOW()
    WHERE id = v_relationship_id;
    
    -- Update referrer wallet
    INSERT INTO referral_wallets (user_id, pending_balance_usd, lifetime_earnings_usd)
    VALUES (v_referrer_id, v_commission_amount, v_commission_amount)
    ON CONFLICT (user_id) 
    DO UPDATE SET
        pending_balance_usd = referral_wallets.pending_balance_usd + v_commission_amount,
        lifetime_earnings_usd = referral_wallets.lifetime_earnings_usd + v_commission_amount,
        updated_at = NOW();
END;
$$ LANGUAGE plpgsql;

-- ═══════════════════════════════════════════════════════════════════════════
-- FUNCTION: reverse_referral_commission
-- Purpose: Reverse commission on refund or chargeback
-- ═══════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION reverse_referral_commission(
    p_payment_id VARCHAR(100),
    p_reversal_reason TEXT
)
RETURNS VOID AS $$
DECLARE
    v_commission RECORD;
BEGIN
    -- Find commission by payment_id
    SELECT * INTO v_commission
    FROM referral_commissions
    WHERE payment_id = p_payment_id AND status IN ('pending', 'approved', 'paid')
    LIMIT 1;
    
    IF v_commission IS NULL THEN
        RETURN; -- No commission found
    END IF;
    
    -- Update commission status
    UPDATE referral_commissions
    SET 
        status = 'reversed',
        reversal_reason = p_reversal_reason,
        reversed_at = NOW(),
        updated_at = NOW()
    WHERE id = v_commission.id;
    
    -- Reverse wallet balance (only if not already paid out)
    IF v_commission.status != 'paid' THEN
        UPDATE referral_wallets
        SET 
            pending_balance_usd = GREATEST(0, pending_balance_usd - v_commission.commission_amount_usd),
            approved_balance_usd = GREATEST(0, approved_balance_usd - v_commission.commission_amount_usd),
            lifetime_earnings_usd = GREATEST(0, lifetime_earnings_usd - v_commission.commission_amount_usd),
            updated_at = NOW()
        WHERE user_id = v_commission.referrer_id;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- ═══════════════════════════════════════════════════════════════════════════
-- TRIGGER: Auto-create referral code on user creation
-- ═══════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION trigger_create_referral_code()
RETURNS TRIGGER AS $$
BEGIN
    -- Create referral code for new user
    PERFORM create_referral_code_for_user(NEW.id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger on profiles table
DROP TRIGGER IF EXISTS tr_profiles_create_referral_code ON profiles;
CREATE TRIGGER tr_profiles_create_referral_code
    AFTER INSERT ON profiles
    FOR EACH ROW
    EXECUTE FUNCTION trigger_create_referral_code();

-- ═══════════════════════════════════════════════════════════════════════════
-- MIGRATION: Migrate existing referral data to new schema
-- ═══════════════════════════════════════════════════════════════════════════
-- Migrate existing referral codes from profiles
INSERT INTO referral_codes (user_id, code, is_active)
SELECT id, referral_code, TRUE
FROM profiles
WHERE referral_code IS NOT NULL
ON CONFLICT (user_id) DO NOTHING;

-- Migrate existing referral relationships
INSERT INTO referral_relationships (referrer_id, referred_id, referral_code_id, status)
SELECT 
    r.referrer_id,
    r.referred_id,
    rc.id,
    r.status
FROM referrals r
LEFT JOIN referral_codes rc ON rc.user_id = r.referrer_id
ON CONFLICT (referred_id) DO NOTHING;

-- Initialize wallets for existing users
INSERT INTO referral_wallets (user_id, pending_balance_usd, approved_balance_usd, paid_balance_usd, lifetime_earnings_usd)
SELECT 
    id,
    COALESCE(SUM(r.commission_usd), 0) FILTER (WHERE r.status = 'pending'),
    COALESCE(SUM(r.commission_usd), 0) FILTER (WHERE r.status = 'approved'),
    COALESCE(SUM(r.commission_usd), 0) FILTER (WHERE r.status = 'paid'),
    COALESCE(SUM(r.commission_usd), 0)
FROM profiles p
LEFT JOIN referrals r ON r.referrer_id = p.id
GROUP BY p.id
ON CONFLICT (user_id) DO NOTHING;

-- ═══════════════════════════════════════════════════════════════════════════
-- CLEANUP: Remove old available_discounts column (will be handled by payout system)
-- ═══════════════════════════════════════════════════════════════════════════
-- Note: Keeping available_discounts for now as it may be used elsewhere
-- Will be removed in a separate migration after verification

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICATION QUERIES
-- ═══════════════════════════════════════════════════════════════════════════
-- Verify tables created
SELECT 
    'referral_codes' as table_name, 
    COUNT(*) as row_count 
FROM referral_codes
UNION ALL
SELECT 
    'referral_relationships', 
    COUNT(*) 
FROM referral_relationships
UNION ALL
SELECT 
    'referral_commissions', 
    COUNT(*) 
FROM referral_commissions
UNION ALL
SELECT 
    'referral_wallets', 
    COUNT(*) 
FROM referral_wallets
UNION ALL
SELECT 
    'referral_payouts', 
    COUNT(*) 
FROM referral_payouts;

-- Verify functions created
SELECT 
    p.proname as function_name,
    pg_get_functiondef(p.oid) as function_definition
FROM pg_proc p
JOIN pg_namespace n ON p.pronamespace = n.oid
WHERE n.nspname = 'public'
AND p.proname IN (
    'generate_unique_referral_code',
    'create_referral_code_for_user',
    'process_referral_commission',
    'reverse_referral_commission'
);

-- ═══════════════════════════════════════════════════════════════════════════
-- END OF MIGRATION
-- ═══════════════════════════════════════════════════════════════════════════
