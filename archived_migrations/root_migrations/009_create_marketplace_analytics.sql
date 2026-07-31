-- 009_create_marketplace_analytics.sql
-- Create marketplace analytics table for global marketplace metrics

CREATE TABLE IF NOT EXISTS public.marketplace_analytics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  
  total_strategies INTEGER NOT NULL DEFAULT 0,
  active_strategies INTEGER NOT NULL DEFAULT 0,
  total_subscribers INTEGER NOT NULL DEFAULT 0,
  total_revenue NUMERIC(16, 2) NOT NULL DEFAULT 0.00,
  currency TEXT DEFAULT 'USD',
  
  top_category TEXT,
  top_strategy_id UUID REFERENCES public.library_strategies(id),
  top_creator_id UUID REFERENCES auth.users(id),
  
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  CONSTRAINT valid_currency CHECK (currency IN ('USD', 'INR')),
  CONSTRAINT marketplace_analytics_period_idx UNIQUE (period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_marketplace_analytics_period ON public.marketplace_analytics(period_start, period_end);

CREATE OR REPLACE FUNCTION update_marketplace_analytics_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_marketplace_analytics_updated_at ON public.marketplace_analytics;
CREATE TRIGGER trg_marketplace_analytics_updated_at
  BEFORE UPDATE ON public.marketplace_analytics
  FOR EACH ROW EXECUTE FUNCTION update_marketplace_analytics_updated_at();
