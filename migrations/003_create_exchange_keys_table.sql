-- Migration: 003_create_exchange_keys_table.sql
-- Purpose: Create exchange_keys table for storing encrypted exchange API credentials
-- Author: Principal Software Architect
-- Date: 2025-08-02

BEGIN;

-- ══════════════════════════════════════════════════════════════════════════
-- CREATE TABLE: exchange_keys
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS exchange_keys (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid(),
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
