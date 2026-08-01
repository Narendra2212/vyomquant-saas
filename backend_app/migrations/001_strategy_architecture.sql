-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 13: Database Schema Optimization for Strategy Architecture
-- ══════════════════════════════════════════════════════════════════════════
-- 
-- This migration adds the new tables required for the Strategy-centric
-- architecture to replace Bot Monitor with Strategy-based operations.
--
-- Tables added:
-- 1. strategy_versions - Immutable version control
-- 2. strategy_deployments - Deployment tracking
-- 3. strategy_backtests - Complete backtest history
-- 4. marketplace_listings - Marketplace integration
-- 5. strategy_subscriptions - Subscription workflow
--
-- Also adds indexes and foreign keys for performance and data integrity.
-- ══════════════════════════════════════════════════════════════════════════

-- ══════════════════════════════════════════════════════════════════════════
-- 1. STRATEGY VERSIONS TABLE
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS strategy_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    version VARCHAR(20) NOT NULL,  -- e.g., "v1.0", "v1.1", "v2.0"
    
    -- Immutable blueprint data (visual graph)
    blueprint JSONB NOT NULL,
    
    -- Compiled execution graph (IR) - PHASE G: Compiler-based architecture
    execution_graph JSONB,
    
    -- Version metadata
    is_draft BOOLEAN DEFAULT TRUE,
    is_current BOOLEAN DEFAULT FALSE,
    is_read_only BOOLEAN DEFAULT FALSE,
    
    -- Version relationships
    original_version_id UUID REFERENCES strategy_versions(id),
    cloned_from_version UUID REFERENCES strategy_versions(id),
    
    -- Backtest results (if backtested)
    backtest_results JSONB,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT unique_strategy_version UNIQUE(strategy_id, version),
    CONSTRAINT check_single_current_version CHECK (
        NOT EXISTS (
            SELECT 1 FROM strategy_versions s2
            WHERE s2.strategy_id = strategy_versions.strategy_id
            AND s2.is_current = TRUE
            AND s2.id != strategy_versions.id
        )
    )
);

-- Indexes for strategy_versions
CREATE INDEX IF NOT EXISTS idx_strategy_versions_strategy_id ON strategy_versions(strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_versions_is_current ON strategy_versions(is_current) WHERE is_current = TRUE;
CREATE INDEX IF NOT EXISTS idx_strategy_versions_is_draft ON strategy_versions(is_draft) WHERE is_draft = TRUE;
CREATE INDEX IF NOT EXISTS idx_strategy_versions_created_at ON strategy_versions(created_at DESC);

-- ══════════════════════════════════════════════════════════════════════════
-- 2. STRATEGY DEPLOYMENTS TABLE
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS strategy_deployments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- Version reference (immutable)
    version_id UUID NOT NULL REFERENCES strategy_versions(id) ON DELETE CASCADE,
    version VARCHAR(20) NOT NULL,
    
    -- Deployment configuration
    environment VARCHAR(20) NOT NULL DEFAULT 'paper',  -- paper, live, cloud, local
    exchange_id UUID REFERENCES exchanges(id),
    exchange_symbol VARCHAR(50),
    
    -- Worker configuration
    worker_region VARCHAR(20) DEFAULT 'us-east-1',
    worker_id VARCHAR(100),
    
    -- Deployment status
    status VARCHAR(20) NOT NULL DEFAULT 'deploying',  -- deploying, running, paused, stopped, failed
    error_message TEXT,
    
    -- Performance metrics (from deployment)
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    total_pnl DECIMAL(20, 8) DEFAULT 0,
    roi_pct DECIMAL(10, 4) DEFAULT 0,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    stopped_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for strategy_deployments
CREATE INDEX IF NOT EXISTS idx_strategy_deployments_strategy_id ON strategy_deployments(strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_deployments_user_id ON strategy_deployments(user_id);
CREATE INDEX IF NOT EXISTS idx_strategy_deployments_status ON strategy_deployments(status);
CREATE INDEX IF NOT EXISTS idx_strategy_deployments_environment ON strategy_deployments(environment);
CREATE INDEX IF NOT EXISTS idx_strategy_deployments_created_at ON strategy_deployments(created_at DESC);

-- ══════════════════════════════════════════════════════════════════════════
-- 3. STRATEGY BACKTESTS TABLE
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS strategy_backtests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- Version reference
    version_id UUID NOT NULL REFERENCES strategy_versions(id) ON DELETE CASCADE,
    version VARCHAR(20) NOT NULL,
    
    -- Blueprint at time of backtest
    blueprint JSONB NOT NULL,
    
    -- Backtest parameters
    dataset VARCHAR(100) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    initial_capital DECIMAL(20, 8) NOT NULL,
    commission DECIMAL(10, 6) DEFAULT 0.001,
    slippage DECIMAL(10, 6) DEFAULT 0.0005,
    
    -- Backtest status
    status VARCHAR(20) NOT NULL DEFAULT 'running',  -- running, completed, failed
    error_message TEXT,
    
    -- Performance results
    total_return DECIMAL(20, 8) DEFAULT 0,
    total_return_pct DECIMAL(10, 4) DEFAULT 0,
    win_rate DECIMAL(10, 4) DEFAULT 0,
    max_drawdown DECIMAL(10, 4) DEFAULT 0,
    sharpe_ratio DECIMAL(10, 4) DEFAULT 0,
    sortino_ratio DECIMAL(10, 4) DEFAULT 0,
    profit_factor DECIMAL(10, 4) DEFAULT 0,
    
    -- Trade metrics
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    avg_trade DECIMAL(20, 8) DEFAULT 0,
    avg_win DECIMAL(20, 8) DEFAULT 0,
    avg_loss DECIMAL(20, 8) DEFAULT 0,
    
    -- Final values
    final_capital DECIMAL(20, 8) DEFAULT 0,
    
    -- Execution metrics
    execution_time_seconds DECIMAL(10, 2) DEFAULT 0,
    
    -- Data arrays (stored as JSONB)
    equity_curve JSONB,
    monthly_returns JSONB,
    daily_returns JSONB,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for strategy_backtests
CREATE INDEX IF NOT EXISTS idx_strategy_backtests_strategy_id ON strategy_backtests(strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_backtests_user_id ON strategy_backtests(user_id);
CREATE INDEX IF NOT EXISTS idx_strategy_backtests_version_id ON strategy_backtests(version_id);
CREATE INDEX IF NOT EXISTS idx_strategy_backtests_status ON strategy_backtests(status);
CREATE INDEX IF NOT EXISTS idx_strategy_backtests_created_at ON strategy_backtests(created_at DESC);

-- ══════════════════════════════════════════════════════════════════════════
-- 4. MARKETPLACE LISTINGS TABLE
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS marketplace_listings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- Listing details
    title VARCHAR(100) NOT NULL,
    description TEXT,
    
    -- Pricing
    pricing JSONB NOT NULL DEFAULT '{}',  -- {monthly_price, one_time_price, etc.}
    
    -- Categorization
    category VARCHAR(50) DEFAULT 'custom',
    tags TEXT[] DEFAULT ARRAY[]::TEXT[],
    
    -- Visibility
    visibility VARCHAR(20) DEFAULT 'public',  -- public, private, unlisted
    
    -- Version reference
    version VARCHAR(20) NOT NULL,
    
    -- Permissions
    allow_clone BOOLEAN DEFAULT FALSE,
    allow_modify BOOLEAN DEFAULT FALSE,
    
    -- Publication status
    is_published BOOLEAN DEFAULT FALSE,
    published_at TIMESTAMPTZ,
    
    -- Marketplace metrics
    view_count INTEGER DEFAULT 0,
    subscriber_count INTEGER DEFAULT 0,
    rating_avg DECIMAL(3, 2) DEFAULT 0,
    rating_count INTEGER DEFAULT 0,
    
    -- Revenue
    total_revenue DECIMAL(20, 8) DEFAULT 0,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for marketplace_listings
CREATE INDEX IF NOT EXISTS idx_marketplace_listings_strategy_id ON marketplace_listings(strategy_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_listings_user_id ON marketplace_listings(user_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_listings_is_published ON marketplace_listings(is_published) WHERE is_published = TRUE;
CREATE INDEX IF NOT EXISTS idx_marketplace_listings_category ON marketplace_listings(category);
CREATE INDEX IF NOT EXISTS idx_marketplace_listings_tags ON marketplace_listings USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_marketplace_listings_created_at ON marketplace_listings(created_at DESC);

-- ══════════════════════════════════════════════════════════════════════════
-- 5. STRATEGY SUBSCRIPTIONS TABLE
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS strategy_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    marketplace_listing_id UUID NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
    
    -- Original strategy reference
    original_strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    original_version_id UUID NOT NULL REFERENCES strategy_versions(id) ON DELETE CASCADE,
    original_version VARCHAR(20) NOT NULL,
    
    -- User's configured strategy copy
    user_strategy_id UUID REFERENCES strategies(id) ON DELETE SET NULL,
    
    -- User configuration (exchange, parameters, etc.)
    configuration JSONB DEFAULT '{}',
    
    -- Subscription status
    status VARCHAR(20) NOT NULL DEFAULT 'active',  -- active, cancelled, expired
    
    -- Subscription metrics
    auto_renew BOOLEAN DEFAULT FALSE,
    expires_at TIMESTAMPTZ,
    
    -- Timestamps
    subscribed_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for strategy_subscriptions
CREATE INDEX IF NOT EXISTS idx_strategy_subscriptions_user_id ON strategy_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_strategy_subscriptions_marketplace_listing_id ON strategy_subscriptions(marketplace_listing_id);
CREATE INDEX IF NOT EXISTS idx_strategy_subscriptions_original_strategy_id ON strategy_subscriptions(original_strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_subscriptions_status ON strategy_subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_strategy_subscriptions_subscribed_at ON strategy_subscriptions(subscribed_at DESC);

-- ══════════════════════════════════════════════════════════════════════════
-- 6. UPDATE STRATEGIES TABLE FOR NEW FIELDS
-- ══════════════════════════════════════════════════════════════════════════

-- Add new columns to strategies table (if they don't exist)
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS current_version VARCHAR(20) DEFAULT 'v1.0';
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS environment VARCHAR(20) DEFAULT 'paper';
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS is_published BOOLEAN DEFAULT FALSE;
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS marketplace_listing_id UUID REFERENCES marketplace_listings(id);
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS is_subscribed BOOLEAN DEFAULT FALSE;
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS is_read_only BOOLEAN DEFAULT FALSE;
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS subscription_id UUID REFERENCES strategy_subscriptions(id);
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS cloned_from UUID REFERENCES strategies(id);
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'draft';

-- Add indexes for new strategy columns
CREATE INDEX IF NOT EXISTS idx_strategies_current_version ON strategies(current_version);
CREATE INDEX IF NOT EXISTS idx_strategies_environment ON strategies(environment);
CREATE INDEX IF NOT EXISTS idx_strategies_is_published ON strategies(is_published) WHERE is_published = TRUE;
CREATE INDEX IF NOT EXISTS idx_strategies_is_subscribed ON strategies(is_subscribed) WHERE is_subscribed = TRUE;
CREATE INDEX IF NOT EXISTS idx_strategies_subscription_id ON strategies(subscription_id);

-- ══════════════════════════════════════════════════════════════════════════
-- 7. AUDIT TRIGGERS FOR HISTORY TRACKING
-- ══════════════════════════════════════════════════════════════════════════

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Add updated_at triggers to all new tables
CREATE TRIGGER update_strategy_versions_updated_at BEFORE UPDATE ON strategy_versions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_strategy_deployments_updated_at BEFORE UPDATE ON strategy_deployments
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_strategy_backtests_updated_at BEFORE UPDATE ON strategy_backtests
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_marketplace_listings_updated_at BEFORE UPDATE ON marketplace_listings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_strategy_subscriptions_updated_at BEFORE UPDATE ON strategy_subscriptions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ══════════════════════════════════════════════════════════════════════════
-- 8. REMOVE BOT-RELATED TABLES (if they exist)
-- ══════════════════════════════════════════════════════════════════════════

-- Note: These tables may not exist in your schema. If they do, uncomment to drop them.
-- DROP TABLE IF EXISTS bots CASCADE;
-- DROP TABLE IF EXISTS bot_metrics CASCADE;
-- DROP TABLE IF EXISTS bot_configs CASCADE;

-- ══════════════════════════════════════════════════════════════════════════
-- 9. ROW LEVEL SECURITY (RLS) POLICIES
-- ══════════════════════════════════════════════════════════════════════════

-- Enable RLS on new tables
ALTER TABLE strategy_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_deployments ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_backtests ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketplace_listings ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_subscriptions ENABLE ROW LEVEL SECURITY;

-- RLS Policies for strategy_versions
CREATE POLICY "Users can view versions of their own strategies"
    ON strategy_versions FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM strategies s
            WHERE s.id = strategy_versions.strategy_id
            AND s.user_id = auth.uid()
        )
    );

CREATE POLICY "Users can insert versions for their own strategies"
    ON strategy_versions FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM strategies s
            WHERE s.id = strategy_versions.strategy_id
            AND s.user_id = auth.uid()
        )
    );

CREATE POLICY "Users can update versions of their own strategies"
    ON strategy_versions FOR UPDATE
    USING (
        EXISTS (
            SELECT 1 FROM strategies s
            WHERE s.id = strategy_versions.strategy_id
            AND s.user_id = auth.uid()
        )
    );

-- RLS Policies for strategy_deployments
CREATE POLICY "Users can view their own deployments"
    ON strategy_deployments FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "Users can insert their own deployments"
    ON strategy_deployments FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update their own deployments"
    ON strategy_deployments FOR UPDATE
    USING (user_id = auth.uid());

-- RLS Policies for strategy_backtests
CREATE POLICY "Users can view their own backtests"
    ON strategy_backtests FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "Users can insert their own backtests"
    ON strategy_backtests FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update their own backtests"
    ON strategy_backtests FOR UPDATE
    USING (user_id = auth.uid());

-- RLS Policies for marketplace_listings
CREATE POLICY "Users can view their own listings"
    ON marketplace_listings FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "Users can insert their own listings"
    ON marketplace_listings FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update their own listings"
    ON marketplace_listings FOR UPDATE
    USING (user_id = auth.uid());

CREATE POLICY "All users can view published listings"
    ON marketplace_listings FOR SELECT
    USING (is_published = TRUE);

-- RLS Policies for strategy_subscriptions
CREATE POLICY "Users can view their own subscriptions"
    ON strategy_subscriptions FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "Users can insert their own subscriptions"
    ON strategy_subscriptions FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update their own subscriptions"
    ON strategy_subscriptions FOR UPDATE
    USING (user_id = auth.uid());

-- ══════════════════════════════════════════════════════════════════════════
-- 10. STRATEGY RESEARCH REPORTS TABLE
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS strategy_research_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES strategy_versions(id) ON DELETE CASCADE,
    version VARCHAR(20) NOT NULL,
    
    -- Research configuration
    optimization_method VARCHAR(50),
    validation_method VARCHAR(50),
    n_iterations INTEGER,
    
    -- Research results
    optimization_results JSONB,
    best_parameters JSONB,
    walk_forward_results JSONB,
    monte_carlo_results JSONB,
    sensitivity_results JSONB,
    benchmark_comparison JSONB,
    
    -- Strategy score
    strategy_score JSONB,
    overall_quality_score DECIMAL(5, 4),
    
    -- Warnings
    warnings TEXT[],
    
    -- Deployment gate
    deployment_approved BOOLEAN DEFAULT FALSE,
    deployment_gate_reason TEXT,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for strategy_research_reports
CREATE INDEX IF NOT EXISTS idx_strategy_research_reports_strategy_id ON strategy_research_reports(strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_research_reports_user_id ON strategy_research_reports(user_id);
CREATE INDEX IF NOT EXISTS idx_strategy_research_reports_version_id ON strategy_research_reports(version_id);
CREATE INDEX IF NOT EXISTS idx_strategy_research_reports_created_at ON strategy_research_reports(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_research_reports_quality_score ON strategy_research_reports(overall_quality_score DESC);

-- RLS for strategy_research_reports
ALTER TABLE strategy_research_reports ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own research reports"
    ON strategy_research_reports FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "Users can create their own research reports"
    ON strategy_research_reports FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update their own research reports"
    ON strategy_research_reports FOR UPDATE
    USING (user_id = auth.uid());

CREATE POLICY "Users can delete their own research reports"
    ON strategy_research_reports FOR DELETE
    USING (user_id = auth.uid());

-- ══════════════════════════════════════════════════════════════════════════
-- 11. COMMENTS/DOCUMENTATION
-- ══════════════════════════════════════════════════════════════════════════

COMMENT ON TABLE strategy_versions IS 'Immutable strategy versions for version control';
COMMENT ON TABLE strategy_deployments IS 'Tracking of strategy deployments (bot instances)';
COMMENT ON TABLE strategy_backtests IS 'Complete backtest history for strategies';
COMMENT ON TABLE marketplace_listings IS 'Marketplace listings for published strategies';
COMMENT ON TABLE strategy_subscriptions IS 'User subscriptions to marketplace strategies';
COMMENT ON TABLE strategy_research_reports IS 'Research and optimization reports for strategies';

COMMENT ON COLUMN strategy_versions.is_current IS 'Marks the currently active version for the strategy';
COMMENT ON COLUMN strategy_versions.is_draft IS 'Marks whether version is a draft (not backtested)';
COMMENT ON COLUMN strategy_deployments.environment IS 'Deployment environment: paper, live, cloud, local';
COMMENT ON COLUMN strategy_backtests.status IS 'Backtest execution status';
COMMENT ON COLUMN marketplace_listings.visibility IS 'Listing visibility: public, private, unlisted';
COMMENT ON COLUMN strategy_subscriptions.status IS 'Subscription status: active, cancelled, expired';
COMMENT ON COLUMN strategy_research_reports.deployment_approved IS 'Whether strategy passed deployment gate';