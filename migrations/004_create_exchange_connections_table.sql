-- Migration: 004_create_exchange_connections_table.sql
-- Purpose: Create exchange_connections table for tracking user exchange connection status
-- Author: Principal Software Architect
-- Date: 2025-08-02

BEGIN;

-- ══════════════════════════════════════════════════════════════════════════
-- CREATE TABLE: exchange_connections
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS exchange_connections (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    exchange_id TEXT NOT NULL,
    is_active BOOLEAN DEFAULT FALSE,
    connection_status TEXT DEFAULT 'disconnected',
    last_connected_at TIMESTAMPTZ,
    last_heartbeat_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT uq_exchange_connections_user_exchange UNIQUE (user_id, exchange_id)
);

-- ══════════════════════════════════════════════════════════════════════════
-- INDEXES
-- ══════════════════════════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_exchange_connections_user_id ON exchange_connections(user_id);
CREATE INDEX IF NOT EXISTS idx_exchange_connections_exchange_id ON exchange_connections(exchange_id);
CREATE INDEX IF NOT EXISTS idx_exchange_connections_is_active ON exchange_connections(is_active);
CREATE INDEX IF NOT EXISTS idx_exchange_connections_created_at ON exchange_connections(created_at DESC);

-- ══════════════════════════════════════════════════════════════════════════
-- ROW LEVEL SECURITY
-- ══════════════════════════════════════════════════════════════════════════

ALTER TABLE exchange_connections ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only access their own exchange connections
CREATE POLICY "exchange_connections_authenticated_owner" ON exchange_connections
    FOR ALL
    TO authenticated
    USING (auth.uid()::text = user_id)
    WITH CHECK (auth.uid()::text = user_id);

-- ══════════════════════════════════════════════════════════════════════════
-- TRIGGERS
-- ══════════════════════════════════════════════════════════════════════════

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_exchange_connections_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_exchange_connections_updated_at ON exchange_connections;
CREATE TRIGGER trg_exchange_connections_updated_at
    BEFORE UPDATE ON exchange_connections
    FOR EACH ROW
    EXECUTE FUNCTION update_exchange_connections_updated_at();

-- ══════════════════════════════════════════════════════════════════════════
-- COMMENTS
-- ══════════════════════════════════════════════════════════════════════════

COMMENT ON TABLE exchange_connections IS 'User exchange connection status tracking for dashboard aggregation';
COMMENT ON COLUMN exchange_connections.is_active IS 'Whether the exchange connection is currently active';
COMMENT ON COLUMN exchange_connections.connection_status IS 'Detailed connection status (connected, disconnected, error)';
COMMENT ON COLUMN exchange_connections.last_connected_at IS 'Last successful connection timestamp';
COMMENT ON COLUMN exchange_connections.last_heartbeat_at IS 'Last heartbeat received from exchange';

COMMIT;
