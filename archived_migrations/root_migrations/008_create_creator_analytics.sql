-- 008_create_creator_analytics.sql
-- Create creator analytics table for marketplace creators

CREATE TABLE IF NOT EXISTS public.creator_analytics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  creator_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  library_id UUID REFERENCES public.library_strategies(id) ON DELETE SET NULL,
  
  total_earnings NUMERIC(16, 2) NOT NULL DEFAULT 0.00,
  currency TEXT DEFAULT 'USD',
  total_subscribers INTEGER NOT NULL DEFAULT 0,
  active_subscribers INTEGER NOT NULL DEFAULT 0,
  total_clones INTEGER NOT NULL DEFAULT 0,
  
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  CONSTRAINT valid_currency CHECK (currency IN ('USD', 'INR')),
  CONSTRAINT creator_analytics_creator_period_idx UNIQUE (creator_id, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_creator_analytics_creator ON public.creator_analytics(creator_id);
CREATE INDEX IF NOT EXISTS idx_creator_analytics_library ON public.creator_analytics(library_id);
CREATE INDEX IF NOT EXISTS idx_creator_analytics_period ON public.creator_analytics(period_start, period_end);

CREATE OR REPLACE FUNCTION update_creator_analytics_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_creator_analytics_updated_at ON public.creator_analytics;
CREATE TRIGGER trg_creator_analytics_updated_at
  BEFORE UPDATE ON public.creator_analytics
  FOR EACH ROW EXECUTE FUNCTION update_creator_analytics_updated_at();
