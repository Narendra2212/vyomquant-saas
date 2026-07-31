-- 006_add_marketplace_pricing.sql
-- Add pricing and subscription fields to library_strategies

ALTER TABLE public.library_strategies 
ADD COLUMN IF NOT EXISTS price NUMERIC(10, 2),
ADD COLUMN IF NOT EXISTS currency TEXT DEFAULT 'USD',
ADD COLUMN IF NOT EXISTS subscription_tier TEXT DEFAULT 'free',
ADD COLUMN IF NOT EXISTS cover_image TEXT,
ADD COLUMN IF NOT EXISTS verification_status TEXT DEFAULT 'unverified',
ADD COLUMN IF NOT EXISTS evaluation_score NUMERIC(5, 2),
ADD COLUMN IF NOT EXISTS subscriber_count INTEGER NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS deployment_requirements JSONB,
ADD COLUMN IF NOT EXISTS version_history JSONB DEFAULT '[]'::jsonb;

ALTER TABLE public.library_strategies 
ADD CONSTRAINT valid_currency CHECK (currency IN ('USD', 'INR')),
ADD CONSTRAINT valid_subscription_tier CHECK (subscription_tier IN ('free', 'pro', 'elite')),
ADD CONSTRAINT valid_verification_status CHECK (verification_status IN ('unverified', 'verified', 'suspended')),
ADD CONSTRAINT valid_evaluation_score CHECK (evaluation_score IS NULL OR (evaluation_score >= 0 AND evaluation_score <= 100));

CREATE INDEX IF NOT EXISTS idx_library_strategies_price ON public.library_strategies(price) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_library_strategies_subscriber_count ON public.library_strategies(subscriber_count DESC) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_library_strategies_verification ON public.library_strategies(verification_status) WHERE is_active = TRUE;
