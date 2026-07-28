-- 001_create_library_strategies.sql
CREATE TABLE IF NOT EXISTS public.library_strategies (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  author_id           UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  source_strategy_id  UUID NOT NULL,

  name                TEXT NOT NULL,
  description         TEXT,
  category            TEXT NOT NULL,
  difficulty          TEXT NOT NULL DEFAULT 'beginner',
  tags                TEXT[] DEFAULT '{}',
  symbol              TEXT NOT NULL,
  timeframe           TEXT NOT NULL,
  exchange_id         TEXT NOT NULL,
  node_count          INTEGER NOT NULL DEFAULT 0,
  has_ml_model        BOOLEAN NOT NULL DEFAULT FALSE,

  backtest_total_return_pct   NUMERIC(10, 4),
  backtest_sharpe_ratio       NUMERIC(10, 4),
  backtest_max_drawdown_pct   NUMERIC(10, 4),
  backtest_win_rate_pct       NUMERIC(10, 4),
  backtest_profit_factor      NUMERIC(10, 4),
  backtest_total_trades       INTEGER,
  backtest_start_date         DATE,
  backtest_end_date           DATE,
  backtest_initial_capital    NUMERIC(16, 2),
  equity_curve_snapshot       JSONB,

  risk_stop_loss_pct          NUMERIC(8, 4),
  risk_take_profit_pct        NUMERIC(8, 4),
  risk_max_position_size      NUMERIC(8, 4),
  risk_max_drawdown_pct       NUMERIC(8, 4),

  clone_count         INTEGER NOT NULL DEFAULT 0,
  avg_rating          NUMERIC(3, 2),
  rating_count        INTEGER NOT NULL DEFAULT 0,

  moderation_status   TEXT NOT NULL DEFAULT 'pending',
  moderation_notes    TEXT,
  moderated_by        UUID REFERENCES auth.users(id),
  moderated_at        TIMESTAMPTZ,

  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  is_featured         BOOLEAN NOT NULL DEFAULT FALSE,
  published_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT valid_category CHECK (category IN (
    'mean_reversion', 'trend_following', 'market_making',
    'arbitrage', 'momentum', 'ml_hybrid', 'other'
  )),
  CONSTRAINT valid_difficulty CHECK (difficulty IN (
    'beginner', 'intermediate', 'advanced', 'pro'
  )),
  CONSTRAINT valid_moderation_status CHECK (moderation_status IN (
    'pending', 'approved', 'rejected', 'featured'
  )),
  CONSTRAINT valid_avg_rating CHECK (avg_rating IS NULL OR (avg_rating >= 1.0 AND avg_rating <= 5.0))
);

CREATE INDEX IF NOT EXISTS idx_library_strategies_author         ON public.library_strategies(author_id);
CREATE INDEX IF NOT EXISTS idx_library_strategies_active_approved ON public.library_strategies(is_active, moderation_status)
  WHERE is_active = TRUE AND moderation_status IN ('approved', 'featured');
CREATE INDEX IF NOT EXISTS idx_library_strategies_category       ON public.library_strategies(category) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_library_strategies_sharpe         ON public.library_strategies(backtest_sharpe_ratio DESC NULLS LAST) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_library_strategies_return         ON public.library_strategies(backtest_total_return_pct DESC NULLS LAST) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_library_strategies_clones         ON public.library_strategies(clone_count DESC) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_library_strategies_rating         ON public.library_strategies(avg_rating DESC NULLS LAST) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_library_strategies_featured       ON public.library_strategies(is_featured, published_at DESC) WHERE is_featured = TRUE;
CREATE INDEX IF NOT EXISTS idx_library_strategies_published_at   ON public.library_strategies(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_library_strategies_tags           ON public.library_strategies USING GIN(tags);

CREATE OR REPLACE FUNCTION update_library_strategies_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_library_strategies_updated_at ON public.library_strategies;
CREATE TRIGGER trg_library_strategies_updated_at
  BEFORE UPDATE ON public.library_strategies
  FOR EACH ROW EXECUTE FUNCTION update_library_strategies_updated_at();
