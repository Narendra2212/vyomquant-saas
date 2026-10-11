-- ═══════════════════════════════════════════════════════════════════════════
-- REFERRAL SYSTEM REDESIGN - NORMALIZED SCHEMA
-- ═══════════════════════════════════════════════════════════════════════════
-- Migration: 001_referral_system_redesign
-- Description: Complete redesign of referral system with proper normalization
-- Author: Principal Database Architect
-- Date: 2026-08-01
-- ═══════════════════════════════════════════════════════════════════════════

-- ═══════════════════════════════════════════════════════════════════════════
-- EXTENSION: pgcrypto
-- ═══════════════════════════════════════════════════════════════════════════
-- generate_unique_referral_code() below calls gen_random_bytes(4), which is
-- pgcrypto's, not core PostgreSQL's. Before this line, no file in this
-- repository issued CREATE EXTENSION at all -- the only occurrence of the
-- phrase was inside an error-message string in 009_paper_trading.sql, which
-- tells an operator to run it but does not run it.
--
-- WHY NOTHING CAUGHT IT. 009's preflight checks for gen_random_uuid(), and
-- gen_random_uuid() has been in core PostgreSQL since version 13. So on any
-- modern server that check passes whether or not pgcrypto is installed, and the
-- one function that genuinely requires the extension went unverified.
--
-- WHAT IT COSTS. create_referral_code_for_user() is invoked by the
-- trigger_create_referral_code trigger on public.profiles, so without pgcrypto
-- EVERY INSERT INTO public.profiles fails:
--
--     UndefinedFunction: function gen_random_bytes(integer) does not exist
--     CONTEXT: PL/pgSQL function generate_unique_referral_code() line 10
--              SQL statement "SELECT create_referral_code_for_user(NEW.id)"
--
-- A profiles row is created for every user, so on a database provisioned from
-- this repository alone, user signup is broken outright. Production is unaffected
-- today because Supabase enables pgcrypto by default -- which is precisely why
-- the dependency stayed invisible, and why a disaster-recovery rebuild onto plain
-- PostgreSQL would not have worked.
--
-- WHY THIS IS NOT A BARE `CREATE EXTENSION IF NOT EXISTS pgcrypto`. That form
-- still fails hard where the extension is not INSTALLED ON THE SERVER at all --
-- `IF NOT EXISTS` only suppresses the "already exists" case, not a missing
-- control file -- and it reports the problem as a bare FeatureNotSupported
-- pointing at a filesystem path, which says nothing about what breaks.
--
-- So the real dependency is tested first: `gen_random_bytes` being resolvable.
-- That is satisfied by pgcrypto in production, and it is also satisfied by the
-- stand-in `scripts/provision_database.py --create-substrate` installs on servers
-- that ship no pgcrypto package. Where neither holds, CREATE EXTENSION is
-- attempted and any refusal is re-raised as a message that names the consequence.
DO $pgcrypto_dependency$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'gen_random_bytes') THEN
        RAISE NOTICE 'referral_system_redesign: gen_random_bytes() already available';
        RETURN;
    END IF;

    BEGIN
        CREATE EXTENSION IF NOT EXISTS pgcrypto;
    EXCEPTION WHEN others THEN
        RAISE EXCEPTION
            'referral_system_redesign requires pgcrypto: generate_unique_referral_code() '
            'calls gen_random_bytes(), and trigger_create_referral_code on '
            'public.profiles calls that function, so without it EVERY INSERT INTO '
            'public.profiles fails and no user can sign up. CREATE EXTENSION was '
            'attempted and refused: %', SQLERRM;
    END;
END $pgcrypto_dependency$;

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
CREATE INDEX IF NOT EXISTS idx_referral_codes_code ON referral_codes(code);
CREATE INDEX IF NOT EXISTS idx_referral_codes_user ON referral_codes(user_id);

-- RLS Policy: Users can only see their own referral code
ALTER TABLE referral_codes ENABLE ROW LEVEL SECURITY;

-- The policies this redesign SUPERSEDES must be dropped, not merely out-voted.
--
-- `006_reconcile_production_database.sql` line 569 creates
--     POLICY referral_codes_select FOR SELECT TO authenticated
--     USING (user_id = auth.uid() OR is_active = true)
-- and this file used to create `referral_codes_select_own` alongside it without removing
-- it. Permissive RLS policies are OR-ed together, so the broader predicate still decided
-- the outcome and the narrower one changed nothing. The redesign report states the
-- intent as "Users can only see their own referral code"; it was not achieved.
--
-- Measured, not inferred: `staging/anon_read_sweep.py authenticated` seeds a referral
-- code owned by customer A and reads it as customer B, and before this drop it reported
--     referral_codes   authenticated saw 1 of 1 row(s)
-- i.e. any logged-in customer could enumerate every active referral code together with
-- the `user_id` that owns it -- the whole referrer list.
DROP POLICY IF EXISTS referral_codes_select ON referral_codes;
DROP POLICY IF EXISTS referral_codes_insert ON referral_codes;

DROP POLICY IF EXISTS referral_codes_select_own ON referral_codes;
CREATE POLICY referral_codes_select_own ON referral_codes
    FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS referral_codes_insert_own ON referral_codes;
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
CREATE INDEX IF NOT EXISTS idx_referral_relationships_referrer ON referral_relationships(referrer_id);
CREATE INDEX IF NOT EXISTS idx_referral_relationships_referred ON referral_relationships(referred_id);
CREATE INDEX IF NOT EXISTS idx_referral_relationships_status ON referral_relationships(status);

-- RLS Policy: Users can see relationships where they are referrer or referred
ALTER TABLE referral_relationships ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS referral_relationships_select_own ON referral_relationships;
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
CREATE INDEX IF NOT EXISTS idx_referral_commissions_referrer ON referral_commissions(referrer_id);
CREATE INDEX IF NOT EXISTS idx_referral_commissions_referred ON referral_commissions(referred_id);
CREATE INDEX IF NOT EXISTS idx_referral_commissions_status ON referral_commissions(status);
CREATE INDEX IF NOT EXISTS idx_referral_commissions_payment ON referral_commissions(payment_id);
CREATE INDEX IF NOT EXISTS idx_referral_commissions_created ON referral_commissions(created_at DESC);

-- RLS Policy: Users can only see their own commissions
ALTER TABLE referral_commissions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS referral_commissions_select_own ON referral_commissions;
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
CREATE INDEX IF NOT EXISTS idx_referral_wallets_user ON referral_wallets(user_id);

-- RLS Policy: Users can only see their own wallet
ALTER TABLE referral_wallets ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS referral_wallets_select_own ON referral_wallets;
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
CREATE INDEX IF NOT EXISTS idx_referral_payouts_user ON referral_payouts(user_id);
CREATE INDEX IF NOT EXISTS idx_referral_payouts_status ON referral_payouts(status);
CREATE INDEX IF NOT EXISTS idx_referral_payouts_created ON referral_payouts(created_at DESC);

-- RLS Policy: Users can only see their own payouts
ALTER TABLE referral_payouts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS referral_payouts_select_own ON referral_payouts;
CREATE POLICY referral_payouts_select_own ON referral_payouts
    FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

-- ═══════════════════════════════════════════════════════════════════════════
-- MIGRATION: Add referral_code to profiles table for backward compatibility
-- ═══════════════════════════════════════════════════════════════════════════
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS referral_code VARCHAR(20);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS referred_by_user_id UUID;

-- Add foreign key and unique constraints, each behind a pg_constraint guard.
--
-- These were bare `ALTER TABLE ... ADD CONSTRAINT`, which PostgreSQL refuses on a
-- second run with `42710 DuplicateObject: constraint "fk_profiles_referred_by" for
-- relation "profiles" already exists`. `020_declare_pre_existing_tables.sql` already
-- declares `profiles` and other files touch it, so this file is not the only writer and
-- cannot assume a clean slate.
--
-- The guard pattern is the one the rest of this repository already uses -- see
-- `backend_app/migrations/009_paper_trading.sql`, whose header records that it wraps
-- "every ADD CONSTRAINT behind a pg_constraint guard". Found by provisioning staging
-- from source; this file was not idempotent in three separate ways (indexes, policies
-- and these constraints).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_profiles_referred_by'
    ) THEN
        ALTER TABLE profiles
            ADD CONSTRAINT fk_profiles_referred_by
            FOREIGN KEY (referred_by_user_id)
            REFERENCES profiles(id)
            ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_profiles_referral_code'
    ) THEN
        ALTER TABLE profiles
            ADD CONSTRAINT uq_profiles_referral_code UNIQUE (referral_code);
    END IF;
END $$;

-- ═══════════════════════════════════════════════════════════════════════════
-- DROP THE SUPERSEDED DEFINITIONS FIRST
-- ═══════════════════════════════════════════════════════════════════════════
-- Three of the functions below are ALSO declared by
-- `migrations/006_reconcile_production_database.sql`, with different signatures:
--
--   function                        006 declares        this file declares
--   create_referral_code_for_user   RETURNS VARCHAR     RETURNS VOID
--   process_referral_commission     (p_payment_id ...)  (p_referred_id ...)
--   reverse_referral_commission     (p_payment_id ...)  (p_payment_id ...)
--
-- `CREATE OR REPLACE FUNCTION` CANNOT CHANGE A RETURN TYPE. PostgreSQL refuses with
-- `42P13 InvalidFunctionDefinition: cannot change return type of existing function`,
-- so this file aborted whenever 006 had run first -- which it always does, because 006
-- is numbered and this file is not, and the numbered set is applied first.
--
-- Found by provisioning staging from source (scripts/provision_database.py).
--
-- This file is the REDESIGN and is intended to supersede, so the old definitions are
-- dropped explicitly rather than replaced. Dropped by name across all overloads,
-- because the two files disagree about the argument lists too and naming one signature
-- would miss the other. `CASCADE` is deliberately NOT used: if something depends on one
-- of these functions, that dependency needs a decision, not a silent removal.
DO $$
DECLARE
    fn record;
BEGIN
    FOR fn IN
        SELECT p.oid::regprocedure AS signature
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN (
              'create_referral_code_for_user',
              'process_referral_commission',
              'reverse_referral_commission'
          )
    LOOP
        EXECUTE format('DROP FUNCTION IF EXISTS %s', fn.signature);
        RAISE NOTICE 'referral_system_redesign: dropped superseded function %', fn.signature;
    END LOOP;
END $$;

-- ═══════════════════════════════════════════════════════════════════════════
-- FUNCTION: generate_unique_referral_code
-- Purpose: Generate a unique referral code with retry logic
-- ═══════════════════════════════════════════════════════════════════════════
-- THE LOCAL VARIABLE WAS NAMED `code`, AND SO IS referral_codes.code.
--
-- The uniqueness check read `SELECT 1 FROM referral_codes WHERE code = code`, where
-- both sides could be either the PL/pgSQL variable or the table column. PostgreSQL does
-- not guess:
--
--     AmbiguousColumn: column reference "code" is ambiguous
--
-- raised from inside this function, which `create_referral_code_for_user` calls, which
-- `trigger_create_referral_code` on `public.profiles` calls -- so the symptom was that
-- every INSERT INTO public.profiles failed. User signup, again, for a second and
-- entirely separate reason from the missing pgcrypto above. Found by executing the
-- insert against a database provisioned from source; no test in the suite inserts a
-- profiles row against PostgreSQL, so nothing caught either one.
--
-- Worth noting that resolving the ambiguity the other way would not have saved it: read
-- as column-equals-column, `WHERE code = code` is true for every row with a non-null
-- code, so `NOT EXISTS` would be false as soon as one code existed, all ten attempts
-- would fail, and the function would end at its own RAISE EXCEPTION. Two different
-- failures, one typo.
--
-- Renamed to `v_code`, matching the `v_` convention this file already uses for locals
-- (`v_referrer_id`, `v_commission_amount`, `v_commission_id`). The column references
-- are also schema-qualified now so the comparison cannot be misread again.
CREATE OR REPLACE FUNCTION generate_unique_referral_code()
RETURNS VARCHAR(20) AS $$
DECLARE
    v_code VARCHAR(20);
    max_attempts INTEGER := 10;
    attempt INTEGER := 0;
BEGIN
    WHILE attempt < max_attempts LOOP
        attempt := attempt + 1;
        -- Generate code: VQ-XXXXXX (6 random alphanumeric)
        v_code := 'VQ-' || upper(substring(encode(gen_random_bytes(4), 'base64'), 1, 6));

        -- Remove non-alphanumeric characters
        v_code := regexp_replace(v_code, '[^A-Z0-9]', '', 'g');
        v_code := 'VQ-' || substring(v_code, 4, 6);

        -- Check if code already exists
        IF NOT EXISTS (
               SELECT 1 FROM referral_codes rc WHERE rc.code = v_code
           ) AND
           NOT EXISTS (
               SELECT 1 FROM profiles p WHERE p.referral_code = v_code
           ) THEN
            RETURN v_code;
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

-- ═══════════════════════════════════════════════════════════════════════════
-- LEGACY RELATION `referrals`: ABSENT HERE, GUARDED, DELIBERATELY NOT DELETED
-- ═══════════════════════════════════════════════════════════════════════════
-- `referrals` is the PRE-REDESIGN table this file supersedes: one flat row per
-- referral, carrying referrer_id, referred_id, status and commission_usd. The
-- two statements below read it to carry that history forward into
-- referral_relationships and referral_wallets.
--
-- IT DOES NOT EXIST IN THIS DEPLOYMENT. to_regclass('public.referrals') is NULL
-- and the relation is absent from pg_tables. Nothing in this repository creates
-- it except the Alembic revision
-- backend_app/alembic/versions/6f1b3d9c8a7e_add_referrals.py, and Alembic is
-- vestigial here: production's alembic_version holds one row, d97ffff9c3bb, and
-- no workflow, Dockerfile or script runs `alembic upgrade` at all.
--
-- Unguarded, these two statements abort the whole file with
--     ERROR 42P01: relation "referrals" does not exist
-- which made this migration NOT REPLAYABLE. Every other statement here is
-- written to be re-runnable - CREATE TABLE IF NOT EXISTS, ON CONFLICT DO
-- NOTHING - yet a staging or DR rebuild from the migration set died partway
-- through the referral system on a relation that nothing declares.
--
-- Each statement is therefore wrapped in a to_regclass existence guard and run
-- through EXECUTE, so its relation names are never resolved while the table is
-- absent. The statements are preserved VERBATIM inside the guard, with ONE
-- deliberate exception recorded below: on a database that still carries the
-- legacy table this IS the correct migration and has to keep running. Deleting
-- the backfill would throw that path away.
--
-- THE ONE EXCEPTION: the wallets initialisation below is NOT byte-for-byte the
-- pre-guard text. It read
--     COALESCE(SUM(r.commission_usd), 0) FILTER (WHERE r.status = 'pending')
-- three times, which puts FILTER on COALESCE. FILTER attaches only to an
-- aggregate call, so PostgreSQL answered
--     ERROR 42601: syntax error at or near "FILTER"
-- whether or not `referrals` existed - observed on the TAKEN branch too, with
-- the legacy table present. That statement had therefore never executed
-- successfully on any database, so there was no legacy behaviour to preserve
-- and nothing to be faithful to; verbatim preservation was the MEANS of keeping
-- a working legacy path, not an end in itself. FILTER was moved inside COALESCE
-- in all three per-status expressions -
--     COALESCE(SUM(r.commission_usd) FILTER (WHERE r.status = 'pending'), 0)
-- - which is the only reading consistent with the target columns
-- (pending_/approved_/paid_balance_usd are per-status conditional sums) and
-- keeps COALESCE where it belongs: OUTSIDE, as the null-to-zero default for a
-- profile with no referral rows, where the filtered SUM is NULL. The fourth
-- expression, lifetime_earnings_usd, carries no FILTER and is unchanged.
-- Nothing else in the statement, the guard or the EXECUTE wrapper moved.
--
-- The guard tests the UNQUALIFIED name, exactly as the statements it guards do,
-- so guard and statement can never disagree about which relation is meant.
--
-- One consequence, stated plainly: where `referrals` is absent the wallet
-- initialisation is skipped ENTIRELY - it is not degraded into a zero-balance
-- insert for every profile. That matches what this deployment actually holds
-- (5 referral_wallets rows against 180 profiles), so the guard reproduces
-- production rather than diverging from it.
--
-- DO NOT RE-APPLY THIS FILE TO PRODUCTION to "pick the backfill up". Production
-- already has all five referral_* tables, created by
-- migrations/006_reconcile_production_database.sql, whose column sets differ
-- from this file's; and this file's own ALTER TABLE profiles statements have
-- never run there. The fix below is about REPLAYABILITY on a rebuilt database,
-- nothing else.
--
-- tests/test_schema_table_reference_drift.py pins both halves of this: that
-- `referrals` is still referenced and still declared by no migration, and that
-- EVERY statement in this file that reads it sits inside a to_regclass guard.
-- ═══════════════════════════════════════════════════════════════════════════
-- Migrate existing referral relationships
--   guarded: see the note above. The SQL inside EXECUTE is the original,
--   unchanged, and is what runs on a database that still has `referrals`.
DO $referrals_guard$
BEGIN
    IF to_regclass('referrals') IS NULL THEN
        RAISE NOTICE 'referral_system_redesign: legacy relation "referrals" is absent; skipping the referral_relationships backfill';
    ELSE
        EXECUTE $referrals_relationships_backfill$
INSERT INTO referral_relationships (referrer_id, referred_id, referral_code_id, status)
SELECT 
    r.referrer_id,
    r.referred_id,
    rc.id,
    r.status
FROM referrals r
LEFT JOIN referral_codes rc ON rc.user_id = r.referrer_id
ON CONFLICT (referred_id) DO NOTHING;
$referrals_relationships_backfill$;
    END IF;
END
$referrals_guard$;

-- Initialize wallets for existing users
--   guarded for the same reason. Skipped wholesale where `referrals` is absent,
--   which is the behaviour the note above records.
-- ═══════════════════════════════════════════════════════════════════════════
-- FORWARD MIGRATION: 006's THREE-COLUMN WALLET -> THIS FILE'S FOUR-STATE WALLET
-- ═══════════════════════════════════════════════════════════════════════════
-- The CREATE TABLE IF NOT EXISTS above is a no-op on every real database, because
-- `006_reconcile_production_database.sql` creates `referral_wallets` first with a
-- different column set:
--
--     006 (what the table has)   balance_usd, total_earned_usd, pending_usd
--     this file (what everything else expects)
--                                pending_balance_usd, approved_balance_usd,
--                                paid_balance_usd, lifetime_earnings_usd
--
-- So the table kept 006's shape while the rest of the referral system moved to this
-- one, and `IF NOT EXISTS` is precisely what hid it. That divergence is BUG-035, and it
-- is not a cosmetic naming difference -- it broke every money path in the feature:
--
--   * `process_referral_commission` (the version THIS FILE installs, which is the one
--     actually present -- verified against pg_proc) writes `pending_balance_usd` and
--     `lifetime_earnings_usd`. Against 006's table that is `UndefinedColumn`, so
--     CREDITING a referral commission fails.
--   * `reverse_referral_commission` writes `pending_balance_usd`,
--     `approved_balance_usd` and `lifetime_earnings_usd`. So REVERSING a commission on
--     a refund fails.
--   * `routers/referral.py` reads all four columns in three endpoints, and its payout
--     handler does `float(wallet.get("approved_balance_usd", 0))`. A missing column
--     cannot come back from `select("*")`, so that resolves to 0 and -- with the $10
--     minimum -- EVERY payout request is answered
--     "Insufficient approved balance. Available: $0.00", silently, for every user.
--
-- WHICH SHAPE IS CANONICAL, AND HOW THAT WAS DECIDED. Not by preference. The installed
-- functions write the four-state shape, the router reads the four-state shape, and a
-- search of `backend_app/` finds NO reader of 006's three columns at all. The table is
-- the only thing out of step, so it is the thing that moves.
--
-- WHY THE MAPPING IS WHAT IT IS, STATED EXPLICITLY BECAUSE IT IS MONEY:
--     pending_balance_usd   <- pending_usd        same meaning: earned, not yet cleared
--     approved_balance_usd  <- balance_usd        006's spendable balance is what the
--                                                 payout path calls "approved"
--     lifetime_earnings_usd <- total_earned_usd   same meaning
--     paid_balance_usd      <- total_earned_usd - balance_usd - pending_usd
--
-- The last one is DERIVED, because 006 never tracked it: anything earned that is neither
-- spendable nor pending must already have been paid out. That derivation is only valid
-- if the three columns are mutually consistent, so it is CHECKED first and the migration
-- RAISES on any row where it would go negative, having changed nothing. A negative would
-- mean 006's own columns disagreed, and inventing a paid-out figure for a referrer is
-- not something a migration may guess at.
--
-- WHY THE OLD COLUMNS ARE DROPPED. Leaving them is how this happened: two column sets on
-- one table, each believed to be the live one by different code. Nothing reads them.
DO $wallet_forward_migration$
DECLARE
    inconsistent integer;
    sample text;
BEGIN
    IF to_regclass('public.referral_wallets') IS NULL THEN
        RAISE NOTICE 'referral_wallets does not exist; nothing to migrate';
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'referral_wallets'
          AND column_name = 'approved_balance_usd'
    ) THEN
        RAISE NOTICE 'referral_wallets already carries the four-state shape; nothing to do';
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'referral_wallets'
          AND column_name = 'balance_usd'
    ) THEN
        RAISE EXCEPTION
            'referral_wallets has neither the four-state shape nor 006''s three-column '
            'shape. Refusing to guess at its layout.';
    END IF;

    -- Consistency precheck, before anything is altered.
    SELECT count(*) INTO inconsistent
    FROM public.referral_wallets
    WHERE coalesce(total_earned_usd, 0)
          - coalesce(balance_usd, 0) - coalesce(pending_usd, 0) < 0;

    IF inconsistent > 0 THEN
        SELECT string_agg(
                   format('user=%s earned=%s balance=%s pending=%s',
                          user_id, total_earned_usd, balance_usd, pending_usd),
                   '; ')
          INTO sample
          FROM (
              SELECT * FROM public.referral_wallets
              WHERE coalesce(total_earned_usd, 0)
                    - coalesce(balance_usd, 0) - coalesce(pending_usd, 0) < 0
              LIMIT 3
          ) bad;
        RAISE EXCEPTION
            'referral_wallets: % row(s) have total_earned_usd < balance_usd + '
            'pending_usd, so paid_balance_usd cannot be derived without inventing a '
            'figure. NOTHING HAS BEEN ALTERED. Reconcile these rows first: %',
            inconsistent, sample;
    END IF;

    ALTER TABLE public.referral_wallets
        ADD COLUMN pending_balance_usd  DECIMAL(10, 2) DEFAULT 0.00,
        ADD COLUMN approved_balance_usd DECIMAL(10, 2) DEFAULT 0.00,
        ADD COLUMN paid_balance_usd     DECIMAL(10, 2) DEFAULT 0.00,
        ADD COLUMN lifetime_earnings_usd DECIMAL(10, 2) DEFAULT 0.00;

    UPDATE public.referral_wallets
       SET pending_balance_usd   = coalesce(pending_usd, 0),
           approved_balance_usd  = coalesce(balance_usd, 0),
           lifetime_earnings_usd = coalesce(total_earned_usd, 0),
           paid_balance_usd      = coalesce(total_earned_usd, 0)
                                   - coalesce(balance_usd, 0)
                                   - coalesce(pending_usd, 0);

    -- THE OLD COLUMNS ARE DELIBERATELY NOT DROPPED.
    --
    -- An earlier version of this block ended with
    --     ALTER TABLE public.referral_wallets
    --         DROP COLUMN balance_usd, DROP COLUMN total_earned_usd, DROP COLUMN pending_usd;
    -- and `tests/test_marketplace_paper_schema_contract.py::test_no_destructive_statements`
    -- rejected it: Requirement 24.7 forbids destructive statements anywhere in the
    -- migration set. The test is right and the requirement is right. These three columns
    -- hold MONEY BALANCES. A DROP COLUMN executed against production is irreversible, and
    -- if the backfill above turns out to have mapped something wrongly, the original
    -- figures are the only way to find out -- or to put it back.
    --
    -- Keeping them costs nothing and risks nothing:
    --   * all three are nullable with DEFAULT 0.00 (006 lines 660-662), so no INSERT
    --     anywhere needs to mention them;
    --   * no code reads them. `routers/referral.py` reads the four-state columns in all
    --     three endpoints that touch a wallet;
    --   * no installed function writes them. Verified against `pg_proc` rather than
    --     against these files, because the question is what the database has --
    --     `staging/which_referral_funcs.py` reports:
    --         process_referral_commission   redesign four-state shape
    --         reverse_referral_commission   redesign four-state shape
    --     `CREATE OR REPLACE FUNCTION` means the last file to run wins, and this one
    --     runs after 006.
    --
    -- So they are vestigial, readable, and available for reconciliation. Removing them is
    -- a separate decision for a separate change, taken once the four-state figures have
    -- been checked against real balances -- see POST_LAUNCH_IMPROVEMENTS.md.

    RAISE NOTICE 'referral_wallets migrated to the four-state shape (BUG-035); '
                 'the three 006 columns are retained for reconciliation';
END
$wallet_forward_migration$;

-- The non-negative invariant this file's CREATE TABLE declares. Added separately so it
-- also lands on a table that arrived via 006 and was converted above.
DO $wallet_check$
BEGIN
    IF to_regclass('public.referral_wallets') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_constraint
           WHERE conname = 'chk_non_negative_balances'
       ) THEN
        ALTER TABLE public.referral_wallets
            ADD CONSTRAINT chk_non_negative_balances CHECK (
                pending_balance_usd >= 0 AND
                approved_balance_usd >= 0 AND
                paid_balance_usd >= 0 AND
                lifetime_earnings_usd >= 0
            );
        RAISE NOTICE 'referral_wallets: non-negative balance constraint added';
    END IF;
END
$wallet_check$;

--   SECOND CONDITION, AND WHY THE FIRST ONE WAS NOT ENOUGH.
--   This backfill writes pending_balance_usd / approved_balance_usd /
--   paid_balance_usd / lifetime_earnings_usd. Those are THIS file's columns. They are
--   not the columns `referral_wallets` actually has on a provisioned database:
--   `006_reconcile_production_database.sql` declares the same table as
--   balance_usd / total_earned_usd / pending_usd, it runs earlier, and the
--   CREATE TABLE IF NOT EXISTS above therefore does nothing. 006 is the file that
--   reconciles against production, so its shape is the real one and this file's is
--   the one that never landed.
--
--   The `referrals` check alone hid that. While `referrals` was absent this block
--   skipped and the mismatch was never reached -- and `referrals` was absent because
--   the alembic revision that creates it, 6f1b3d9c8a7e, sits downstream of
--   `add_foreign_keys`, which could not commit, so `upgrade head` never got there.
--   Repairing that revision made `referrals` exist, this block fired for the first
--   time, and it failed:
--
--     UndefinedColumn: column "pending_balance_usd" of relation
--     "referral_wallets" does not exist
--
--   So the guard now tests the thing the statement actually depends on. Where 006's
--   shape is in place this skips and says so; it does not write one shape's data into
--   the other's columns, and it does not silently convert a schema conflict into a
--   provisioning failure. The divergence itself is a defect in its own right and is
--   reported separately -- five tables in this file disagree with 006, and
--   `routers/referral.py` writes `approved_balance_usd`, a column the live shape does
--   not have.
DO $referrals_wallet_guard$
BEGIN
    IF to_regclass('referrals') IS NULL THEN
        RAISE NOTICE 'referral_system_redesign: legacy relation "referrals" is absent; skipping the referral_wallets initialisation';
    ELSIF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'referral_wallets'
          AND column_name = 'pending_balance_usd'
    ) THEN
        RAISE NOTICE 'referral_system_redesign: referral_wallets does not carry this file''s column set (006_reconcile_production_database.sql declares balance_usd/total_earned_usd/pending_usd instead); skipping the referral_wallets initialisation';
    ELSE
        -- `p.id`, NOT `id`. It was unqualified, and both `profiles` and `referrals`
        -- have an `id` column, so the statement failed with
        --
        --     AmbiguousColumn: column reference "id" is ambiguous
        --
        -- which means this backfill had never once run successfully. Three things hid
        -- that in sequence: `referrals` did not exist (the alembic revision creating it
        -- was downstream of a revision that could not commit -- BUG-028), then the
        -- column set did not match (BUG-035, fixed above), and only then was the
        -- statement itself reached.
        EXECUTE $referrals_wallets_backfill$
INSERT INTO referral_wallets (user_id, pending_balance_usd, approved_balance_usd, paid_balance_usd, lifetime_earnings_usd)
SELECT 
    p.id,
    COALESCE(SUM(r.commission_usd) FILTER (WHERE r.status = 'pending'), 0),
    COALESCE(SUM(r.commission_usd) FILTER (WHERE r.status = 'approved'), 0),
    COALESCE(SUM(r.commission_usd) FILTER (WHERE r.status = 'paid'), 0),
    COALESCE(SUM(r.commission_usd), 0)
FROM profiles p
LEFT JOIN referrals r ON r.referrer_id = p.id
GROUP BY p.id
ON CONFLICT (user_id) DO NOTHING;
$referrals_wallets_backfill$;
    END IF;
END
$referrals_wallet_guard$;

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
