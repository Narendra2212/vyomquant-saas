-- ══════════════════════════════════════════════════════════════════════════
-- Signal Trace Database Schema
-- ══════════════════════════════════════════════════════════════════════════
-- 
-- Complete signal lifecycle tracking from strategy decision to final execution.
-- ══════════════════════════════════════════════════════════════════════════

-- ══════════════════════════════════════════════════════════════════════════
-- SIGNALS TABLE
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- Strategy Information
    strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    strategy_version VARCHAR(20) NOT NULL,
    deployment_id UUID REFERENCES strategy_deployments(id) ON DELETE SET NULL,
    
    -- Trading Information
    exchange_id VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    worker_id VARCHAR(100),
    
    -- Signal Decision
    decision VARCHAR(10) NOT NULL,  -- BUY, SELL, EXIT, CLOSE, HOLD
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending, accepted, rejected, executed, failed, cancelled, expired
    
    -- Indicators
    indicators JSONB NOT NULL DEFAULT '{}',
    
    -- Market Information
    market_info JSONB NOT NULL DEFAULT '{}',
    
    -- ML/DL Information (if applicable)
    ml_info JSONB,
    
    -- Risk Decision
    risk_passed BOOLEAN,
    risk_reason TEXT,
    position_size DECIMAL(20, 8),
    capital DECIMAL(20, 8),
    exposure DECIMAL(10, 4),
    expected_loss DECIMAL(20, 8),
    expected_reward DECIMAL(20, 8),
    drawdown_check BOOLEAN,
    risk_evaluated_at TIMESTAMPTZ,
    
    -- Order Information
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
    
    -- Execution Information
    trade_id UUID,
    pnl DECIMAL(20, 8),
    realized_pnl DECIMAL(20, 8),
    executed_at TIMESTAMPTZ,
    
    -- Timestamps
    generated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for signals
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

-- Composite indexes for common queries
CREATE INDEX IF NOT EXISTS idx_signals_user_strategy ON signals(user_id, strategy_id);
CREATE INDEX IF NOT EXISTS idx_signals_user_exchange_symbol ON signals(user_id, exchange_id, symbol);
CREATE INDEX IF NOT EXISTS idx_signals_user_status ON signals(user_id, status);

-- ══════════════════════════════════════════════════════════════════════════
-- SIGNAL EVENTS TABLE (for detailed timeline)
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS signal_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id UUID NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    
    -- Event Information
    event_type VARCHAR(50) NOT NULL,  -- SIGNAL_GENERATED, RISK_EVALUATED, ORDER_CREATED, EXCHANGE_RESPONSE, EXECUTED, FAILED, CANCELLED
    event_data JSONB NOT NULL DEFAULT '{}',
    
    -- Timestamp
    event_timestamp TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for signal_events
CREATE INDEX IF NOT EXISTS idx_signal_events_signal_id ON signal_events(signal_id);
CREATE INDEX IF NOT EXISTS idx_signal_events_user_id ON signal_events(user_id);
CREATE INDEX IF NOT EXISTS idx_signal_events_event_type ON signal_events(event_type);
CREATE INDEX IF NOT EXISTS idx_signal_events_event_timestamp ON signal_events(event_timestamp DESC);

-- ══════════════════════════════════════════════════════════════════════════
-- AUDIT TRIGGER FOR UPDATED_AT
-- ══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION update_signals_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_signals_updated_at
    BEFORE UPDATE ON signals
    FOR EACH ROW
    EXECUTE FUNCTION update_signals_updated_at();

-- ══════════════════════════════════════════════════════════════════════════
-- ROW LEVEL SECURITY
-- ══════════════════════════════════════════════════════════════════════════

ALTER TABLE signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE signal_events ENABLE ROW LEVEL SECURITY;

-- RLS Policies for signals
CREATE POLICY "Users can view their own signals"
    ON signals FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "Users can insert their own signals"
    ON signals FOR INSERT
    WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update their own signals"
    ON signals FOR UPDATE
    USING (user_id = auth.uid());

-- RLS Policies for signal_events
CREATE POLICY "Users can view their own signal events"
    ON signal_events FOR SELECT
    USING (user_id = auth.uid());

CREATE POLICY "Users can insert their own signal events"
    ON signal_events FOR INSERT
    WITH CHECK (user_id = auth.uid());

-- ══════════════════════════════════════════════════════════════════════════
-- COMMENTS
-- ══════════════════════════════════════════════════════════════════════════

COMMENT ON TABLE signals IS 'Complete signal lifecycle tracking from strategy decision to final execution';
COMMENT ON TABLE signal_events IS 'Detailed event timeline for each signal';

COMMENT ON COLUMN signals.decision IS 'Signal decision: BUY, SELL, EXIT, CLOSE, HOLD';
COMMENT ON COLUMN signals.status IS 'Signal status: pending, accepted, rejected, executed, failed, cancelled, expired';
COMMENT ON COLUMN signals.indicators IS 'Technical indicator values at signal generation';
COMMENT ON COLUMN signals.market_info IS 'Market information: price, spread, volume, ATR, RSI, MACD, EMA, trend, volatility, regime';
COMMENT ON COLUMN signals.ml_info IS 'ML/DL information: prediction, confidence, probability, threshold, inference time, model version, dataset version, feature version, top features';
COMMENT ON COLUMN signals.risk_passed IS 'Whether risk check passed';
COMMENT ON COLUMN signals.risk_reason IS 'Reason for risk decision';
COMMENT ON COLUMN signals.position_size IS 'Calculated position size';
COMMENT ON COLUMN signals.exposure IS 'Current exposure percentage';
COMMENT ON COLUMN signals.expected_loss IS 'Expected loss for this trade';
COMMENT ON COLUMN signals.expected_reward IS 'Expected reward for this trade';
COMMENT ON COLUMN signals.drawdown_check IS 'Drawdown check result';
COMMENT ON COLUMN signals.latency_ms IS 'Order execution latency in milliseconds';
COMMENT ON COLUMN signals.slippage IS 'Slippage percentage';
COMMENT ON COLUMN signals.pnl IS 'Realized PnL for this signal';