-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 9J: Minimal Signal Trace Schema Restoration
-- ══════════════════════════════════════════════════════════════════════════
--
-- This migration restores ONLY the missing database schema required by the
-- existing application code, based on Phase 9I forensic audit.
--
-- Restores:
-- 1. strategies table columns (current_version, environment) - status column left unchanged
-- 2. strategy_versions table (with restored_from column)
-- 3. strategy_deployments table (with initial_capital, exchange_id as VARCHAR)
-- 4. signals table (complete schema per signal_service.py)
--
-- Does NOT create:
-- - signal_events (application generates timeline in-memory)
-- - exchanges table (application stores exchange_id as string)
-- - marketplace tables (not used by current application)
--
-- Safety:
-- - All CREATE TABLE use IF NOT EXISTS
-- - All CREATE INDEX use IF NOT EXISTS
-- - All triggers check schema + table + trigger name
-- - All policies check schemaname + tablename + policyname
-- - No DROP, DELETE, TRUNCATE, or data updates
-- - Preserves tenant isolation with auth.uid()
-- - No DELETE policies
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ══════════════════════════════════════════════════════════════════════════
-- 1. Add required columns to strategies table
-- ══════════════════════════════════════════════════════════════════════════
-- NOTE: status column already exists in production with DEFAULT 'stopped'
-- We do NOT modify existing status behavior - only add missing columns

ALTER TABLE strategies ADD COLUMN IF NOT EXISTS current_version VARCHAR(20) DEFAULT 'v1.0';
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS environment VARCHAR(20) DEFAULT 'paper';

CREATE INDEX IF NOT EXISTS idx_strategies_current_version ON strategies(current_version);
CREATE INDEX IF NOT EXISTS idx_strategies_environment ON strategies(environment);

-- ══════════════════════════════════════════════════════════════════════════
-- 2. Create strategy_versions table (with restored_from)
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS strategy_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    version VARCHAR(20) NOT NULL,
    blueprint JSONB NOT NULL,
    execution_graph JSONB,
    is_draft BOOLEAN DEFAULT TRUE,
    is_current BOOLEAN DEFAULT FALSE,
    is_read_only BOOLEAN DEFAULT FALSE,
    original_version_id UUID REFERENCES strategy_versions(id),
    cloned_from_version UUID REFERENCES strategy_versions(id),
    backtest_results JSONB,
    restored_from VARCHAR(20),  -- Added for restore_version functionality (strategy_service.py line 512)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT unique_strategy_version UNIQUE(strategy_id, version)
);

CREATE INDEX IF NOT EXISTS idx_strategy_versions_strategy_id ON strategy_versions(strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_versions_is_current ON strategy_versions(is_current) WHERE is_current = TRUE;
CREATE INDEX IF NOT EXISTS idx_strategy_versions_is_draft ON strategy_versions(is_draft) WHERE is_draft = TRUE;
CREATE INDEX IF NOT EXISTS idx_strategy_versions_created_at ON strategy_versions(created_at DESC);

-- ══════════════════════════════════════════════════════════════════════════
-- 3. Create strategy_deployments table (with initial_capital, exchange_id as VARCHAR)
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS strategy_deployments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES strategy_versions(id) ON DELETE CASCADE,
    version VARCHAR(20) NOT NULL,
    environment VARCHAR(20) NOT NULL DEFAULT 'paper',
    exchange_id VARCHAR(50),  -- VARCHAR(50), NO FK to exchanges (table does not exist in production)
    exchange_symbol VARCHAR(50),
    worker_region VARCHAR(20) DEFAULT 'us-east-1',
    worker_id VARCHAR(100),
    status VARCHAR(20) NOT NULL DEFAULT 'deploying',
    error_message TEXT,
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    total_pnl DECIMAL(20, 8) DEFAULT 0,
    roi_pct DECIMAL(10, 4) DEFAULT 0,
    initial_capital DECIMAL(20, 8) DEFAULT 0,  -- Added for strategy_operations.py line 1388
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    stopped_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_strategy_deployments_strategy_id ON strategy_deployments(strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_deployments_user_id ON strategy_deployments(user_id);
CREATE INDEX IF NOT EXISTS idx_strategy_deployments_status ON strategy_deployments(status);
CREATE INDEX IF NOT EXISTS idx_strategy_deployments_environment ON strategy_deployments(environment);
CREATE INDEX IF NOT EXISTS idx_strategy_deployments_created_at ON strategy_deployments(created_at DESC);

-- ══════════════════════════════════════════════════════════════════════════
-- 4. Create signals table (complete schema per signal_service.py)
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    strategy_version VARCHAR(20) NOT NULL,
    deployment_id UUID REFERENCES strategy_deployments(id) ON DELETE SET NULL,
    exchange_id VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    worker_id VARCHAR(100),
    decision VARCHAR(10) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    indicators JSONB NOT NULL DEFAULT '{}',
    market_info JSONB NOT NULL DEFAULT '{}',
    ml_info JSONB,
    risk_passed BOOLEAN,
    risk_reason TEXT,
    position_size DECIMAL(20, 8),
    capital DECIMAL(20, 8),
    exposure DECIMAL(10, 4),
    expected_loss DECIMAL(20, 8),
    expected_reward DECIMAL(20, 8),
    drawdown_check BOOLEAN,
    risk_evaluated_at TIMESTAMPTZ,
    order_id UUID,
    exchange_order_id VARCHAR(100),
    order_status VARCHAR(20),
    quantity DECIMAL(20, 8),
    filled DECIMAL(20, 8),
    remaining DECIMAL(20, 8),
    average_price DECIMAL(20, 8),
    fees DECIMAL(20, 8),
    slippage DECIMAL(10, 6),
    latency_ms DECIMAL(10, 2),
    order_updated_at TIMESTAMPTZ,
    trade_id UUID,
    pnl DECIMAL(20, 8),
    realized_pnl DECIMAL(20, 8),
    executed_at TIMESTAMPTZ,
    generated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signals_user_id ON signals(user_id);
CREATE INDEX IF NOT EXISTS idx_signals_strategy_id ON signals(strategy_id);
CREATE INDEX IF NOT EXISTS idx_signals_deployment_id ON signals(deployment_id);
CREATE INDEX IF NOT EXISTS idx_signals_exchange_id ON signals(exchange_id);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_decision ON signals(decision);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_generated_at ON signals(generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_risk_evaluated_at ON signals(risk_evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_executed_at ON signals(executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_user_strategy ON signals(user_id, strategy_id);
CREATE INDEX IF NOT EXISTS idx_signals_user_exchange_symbol ON signals(user_id, exchange_id, symbol);
CREATE INDEX IF NOT EXISTS idx_signals_user_status ON signals(user_id, status);

-- ══════════════════════════════════════════════════════════════════════════
-- 5. Create shared updated_at function (for strategy_versions, strategy_deployments)
-- ══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ══════════════════════════════════════════════════════════════════════════
-- 6. Create signals-specific updated_at function
-- ══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION update_signals_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ══════════════════════════════════════════════════════════════════════════
-- 7. Create triggers with schema + table + trigger name checks
-- ══════════════════════════════════════════════════════════════════════════

DO $$
BEGIN
    -- strategy_versions trigger
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trigger_update_strategy_versions_updated_at'
        AND n.nspname = 'public'
        AND c.relname = 'strategy_versions'
    ) THEN
        CREATE TRIGGER trigger_update_strategy_versions_updated_at
            BEFORE UPDATE ON strategy_versions
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
    END IF;
    
    -- strategy_deployments trigger
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trigger_update_strategy_deployments_updated_at'
        AND n.nspname = 'public'
        AND c.relname = 'strategy_deployments'
    ) THEN
        CREATE TRIGGER trigger_update_strategy_deployments_updated_at
            BEFORE UPDATE ON strategy_deployments
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
    END IF;
    
    -- signals trigger (uses signals-specific function)
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trigger_update_signals_updated_at'
        AND n.nspname = 'public'
        AND c.relname = 'signals'
    ) THEN
        CREATE TRIGGER trigger_update_signals_updated_at
            BEFORE UPDATE ON signals
            FOR EACH ROW
            EXECUTE FUNCTION update_signals_updated_at();
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- 8. Enable RLS
-- ══════════════════════════════════════════════════════════════════════════

ALTER TABLE strategy_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_deployments ENABLE ROW LEVEL SECURITY;
ALTER TABLE signals ENABLE ROW LEVEL SECURITY;

-- ══════════════════════════════════════════════════════════════════════════
-- 9. Create RLS policies with schemaname + tablename + policyname checks
-- ══════════════════════════════════════════════════════════════════════════

-- strategy_versions policies
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE schemaname = 'public'
        AND tablename = 'strategy_versions'
        AND policyname = 'Users can view versions of their own strategies'
    ) THEN
        CREATE POLICY "Users can view versions of their own strategies"
            ON strategy_versions FOR SELECT
            USING (
                EXISTS (
                    SELECT 1 FROM strategies s
                    WHERE s.id = strategy_versions.strategy_id
                    AND s.user_id = auth.uid()
                )
            );
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE schemaname = 'public'
        AND tablename = 'strategy_versions'
        AND policyname = 'Users can insert versions for their own strategies'
    ) THEN
        CREATE POLICY "Users can insert versions for their own strategies"
            ON strategy_versions FOR INSERT
            WITH CHECK (
                EXISTS (
                    SELECT 1 FROM strategies s
                    WHERE s.id = strategy_versions.strategy_id
                    AND s.user_id = auth.uid()
                )
            );
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE schemaname = 'public'
        AND tablename = 'strategy_versions'
        AND policyname = 'Users can update versions of their own strategies'
    ) THEN
        CREATE POLICY "Users can update versions of their own strategies"
            ON strategy_versions FOR UPDATE
            USING (
                EXISTS (
                    SELECT 1 FROM strategies s
                    WHERE s.id = strategy_versions.strategy_id
                    AND s.user_id = auth.uid()
                )
            );
    END IF;
END $$;

-- strategy_deployments policies
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE schemaname = 'public'
        AND tablename = 'strategy_deployments'
        AND policyname = 'Users can view their own deployments'
    ) THEN
        CREATE POLICY "Users can view their own deployments"
            ON strategy_deployments FOR SELECT
            USING (user_id = auth.uid());
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE schemaname = 'public'
        AND tablename = 'strategy_deployments'
        AND policyname = 'Users can insert their own deployments'
    ) THEN
        CREATE POLICY "Users can insert their own deployments"
            ON strategy_deployments FOR INSERT
            WITH CHECK (user_id = auth.uid());
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE schemaname = 'public'
        AND tablename = 'strategy_deployments'
        AND policyname = 'Users can update their own deployments'
    ) THEN
        CREATE POLICY "Users can update their own deployments"
            ON strategy_deployments FOR UPDATE
            USING (user_id = auth.uid());
    END IF;
END $$;

-- signals policies
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE schemaname = 'public'
        AND tablename = 'signals'
        AND policyname = 'Users can view their own signals'
    ) THEN
        CREATE POLICY "Users can view their own signals"
            ON signals FOR SELECT
            USING (user_id = auth.uid());
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE schemaname = 'public'
        AND tablename = 'signals'
        AND policyname = 'Users can insert their own signals'
    ) THEN
        CREATE POLICY "Users can insert their own signals"
            ON signals FOR INSERT
            WITH CHECK (user_id = auth.uid());
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE schemaname = 'public'
        AND tablename = 'signals'
        AND policyname = 'Users can update their own signals'
    ) THEN
        CREATE POLICY "Users can update their own signals"
            ON signals FOR UPDATE
            USING (user_id = auth.uid());
    END IF;
END $$;

COMMIT;
