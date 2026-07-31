-- 007_create_library_subscriptions.sql
-- Create subscriptions table for marketplace strategy subscriptions

CREATE TABLE IF NOT EXISTS public.library_subscriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  library_id UUID NOT NULL REFERENCES public.library_strategies(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  
  subscription_tier TEXT NOT NULL DEFAULT 'free',
  price_paid NUMERIC(10, 2),
  currency TEXT DEFAULT 'USD',
  payment_provider TEXT, -- 'stripe' or 'razorpay'
  payment_id TEXT,
  
  status TEXT NOT NULL DEFAULT 'active',
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ,
  cancelled_at TIMESTAMPTZ,
  
  CONSTRAINT valid_subscription_tier CHECK (subscription_tier IN ('free', 'pro', 'elite')),
  CONSTRAINT valid_subscription_status CHECK (status IN ('active', 'expired', 'cancelled', 'pending')),
  CONSTRAINT valid_currency CHECK (currency IN ('USD', 'INR')),
  CONSTRAINT library_subscriptions_user_library_idx UNIQUE (library_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_library_subscriptions_library ON public.library_subscriptions(library_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_library_subscriptions_user ON public.library_subscriptions(user_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_library_subscriptions_status ON public.library_subscriptions(status);

CREATE OR REPLACE FUNCTION update_library_subscriptions_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_library_subscriptions_updated_at ON public.library_subscriptions;
CREATE TRIGGER trg_library_subscriptions_updated_at
  BEFORE UPDATE ON public.library_subscriptions
  FOR EACH ROW EXECUTE FUNCTION update_library_subscriptions_updated_at();
