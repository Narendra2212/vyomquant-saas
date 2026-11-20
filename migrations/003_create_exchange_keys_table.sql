-- Migration: 003_create_exchange_keys_table.sql
-- Purpose: Create exchange_keys table for storing encrypted exchange API credentials
-- Author: Principal Software Architect
-- Date: 2025-08-02

BEGIN;

-- ══════════════════════════════════════════════════════════════════════════
-- CREATE TABLE: exchange_keys
-- ══════════════════════════════════════════════════════════════════════════

-- `id` IS `uuid`, AND READ THE NEXT PARAGRAPH BEFORE CHANGING IT BACK.
--
-- This column read `id TEXT PRIMARY KEY DEFAULT gen_random_uuid()` until 2026-10-25.
-- That declaration was wrong in a way PostgreSQL hides: `gen_random_uuid()` RETURNS
-- `uuid`, so every default-generated value was implicitly cast uuid -> text on the way
-- in. The column held uuid values the whole time; only its declared type disagreed.
--
-- It mattered because this file is the ONLY place `exchange_keys` is created, while
-- `backend_app/migrations/058_exch03_exchange_keys_stable_handle.sql` declares the same
-- column `id UUID` (deliberately, to match `strategy_deployments.exchange_account_id
-- uuid`, which is what makes a deployment's exchange account a single-column foreign
-- handle). Two migrations declaring one column with two types means THE TYPE A REBUILT
-- DATABASE GETS DEPENDS ON WHICH FILE RAN LAST -- exactly the
-- `strategy_backtests.version` defect that 001/006 shipped and
-- `016_strategy_backtests_version_label.sql` had to repair. On a table holding exchange
-- API credentials that is not a cosmetic risk: a fresh environment could end up with a
-- credential handle of a different type from the one the deployment binding expects.
-- Caught by tests/test_schema_table_reference_drift.py::
-- TestColumnTypesAreDeclaredOnce::test_the_divergence_inventory_has_not_grown.
--
-- PRODUCTION ALREADY CARRIES `uuid` (measured against the live database, which also
-- carries `uq_exchange_keys_id` from 058), so `uuid` is not a new choice here -- it is
-- the shape that actually exists, written down. 058 now agrees with this file instead of
-- contradicting it.
--
-- NO `ALTER COLUMN ... TYPE` IS SHIPPED WITH THIS, deliberately. `CREATE TABLE IF NOT
-- EXISTS` means this edit changes NEW databases only; a database provisioned from the
-- old text declaration keeps `text`, and 058's `ADD COLUMN IF NOT EXISTS id UUID` is a
-- no-op there. Retyping it would mean retyping a PRIMARY KEY on the credentials table,
-- which 058's own comments considered and declined for that reason, and which production
-- does not need because production is already `uuid`. If such a database is ever found,
-- that retype is its own migration with its own backup -- not a silent side effect of
-- this one.
CREATE TABLE IF NOT EXISTS exchange_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    exchange_id TEXT NOT NULL,
    encrypted_api_key TEXT NOT NULL,
    encrypted_secret_key TEXT NOT NULL,
    encrypted_password TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT uq_exchange_keys_user_exchange UNIQUE (user_id, exchange_id)
);

-- ══════════════════════════════════════════════════════════════════════════
-- INDEXES
-- ══════════════════════════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_exchange_keys_user_id ON exchange_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_exchange_keys_exchange_id ON exchange_keys(exchange_id);
CREATE INDEX IF NOT EXISTS idx_exchange_keys_created_at ON exchange_keys(created_at DESC);

-- ══════════════════════════════════════════════════════════════════════════
-- ROW LEVEL SECURITY
-- ══════════════════════════════════════════════════════════════════════════

ALTER TABLE exchange_keys ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only access their own exchange keys
CREATE POLICY "exchange_keys_authenticated_owner" ON exchange_keys
    FOR ALL
    TO authenticated
    USING (auth.uid()::text = user_id)
    WITH CHECK (auth.uid()::text = user_id);

-- ══════════════════════════════════════════════════════════════════════════
-- TRIGGERS
-- ══════════════════════════════════════════════════════════════════════════

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_exchange_keys_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_exchange_keys_updated_at ON exchange_keys;
CREATE TRIGGER trg_exchange_keys_updated_at
    BEFORE UPDATE ON exchange_keys
    FOR EACH ROW
    EXECUTE FUNCTION update_exchange_keys_updated_at();

-- ══════════════════════════════════════════════════════════════════════════
-- COMMENTS
-- ══════════════════════════════════════════════════════════════════════════

COMMENT ON TABLE exchange_keys IS 'Encrypted storage for exchange API credentials. Keys are encrypted before storage using vault encryption.';
COMMENT ON COLUMN exchange_keys.encrypted_api_key IS 'Encrypted API key using vault encryption';
COMMENT ON COLUMN exchange_keys.encrypted_secret_key IS 'Encrypted secret key using vault encryption';
COMMENT ON COLUMN exchange_keys.encrypted_password IS 'Encrypted password (optional, for exchanges requiring passphrase)';

COMMIT;
