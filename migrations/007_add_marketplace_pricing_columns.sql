-- 007_add_marketplace_pricing_columns.sql
--
-- PURPOSE
--   Reconcile public.library_strategies with the columns that
--   backend_app/routers/library.py already SELECTs and INSERTs.
--
-- DEFECT THIS FIXES
--   Production CloudWatch logs show, on every marketplace request:
--     Trending strategies DB error: {code: 42703,
--       message: column library_strategies.price does not exist}
--     Featured strategies DB error: {code: 42703, ...}
--   42703 = undefined_column. The router catches the error and logs a
--   warning, so /api/library trending and featured silently return empty
--   results instead of real data. publish_strategy also INSERTs price,
--   currency, subscription_tier, cover_image, evaluation_score and
--   verification_status, so publishing is affected by the same gap.
--
-- HISTORY
--   archived_migrations/root_migrations/006_add_marketplace_pricing.sql
--   introduced these columns but was never applied to production. Its bare
--   ADD CONSTRAINT statements are not idempotent, so re-running it aborts
--   with "constraint already exists". migrations/006_reconcile_production_
--   database.sql added only subscriber_count and missed the rest.
--
-- SAFETY
--   * Additive only. No DROP, TRUNCATE or DELETE. No data loss.
--   * Fully idempotent: safe to run repeatedly.
--   * ADD COLUMN ... DEFAULT backfills existing rows (PostgreSQL 11+),
--     so the CHECK constraints below cannot be violated by existing data.
--   * Constraints are dropped-if-exists then re-added, making the script
--     re-runnable. Column CHECKs also permit NULL.
--   * Indexes use IF NOT EXISTS.

BEGIN;

-- 1) Columns -----------------------------------------------------------
ALTER TABLE public.library_strategies
    ADD COLUMN IF NOT EXISTS price                   NUMERIC(10, 2),
    ADD COLUMN IF NOT EXISTS currency                TEXT    DEFAULT 'USD',
    ADD COLUMN IF NOT EXISTS subscription_tier       TEXT    DEFAULT 'free',
    ADD COLUMN IF NOT EXISTS cover_image             TEXT,
    ADD COLUMN IF NOT EXISTS verification_status     TEXT    DEFAULT 'unverified',
    ADD COLUMN IF NOT EXISTS evaluation_score        NUMERIC(5, 2),
    ADD COLUMN IF NOT EXISTS subscriber_count        INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS deployment_requirements JSONB,
    ADD COLUMN IF NOT EXISTS version_history         JSONB   DEFAULT '[]'::jsonb;

-- 2) Constraints (idempotent) ------------------------------------------
ALTER TABLE public.library_strategies DROP CONSTRAINT IF EXISTS valid_currency;
ALTER TABLE public.library_strategies DROP CONSTRAINT IF EXISTS valid_subscription_tier;
ALTER TABLE public.library_strategies DROP CONSTRAINT IF EXISTS valid_verification_status;
ALTER TABLE public.library_strategies DROP CONSTRAINT IF EXISTS valid_evaluation_score;
ALTER TABLE public.library_strategies DROP CONSTRAINT IF EXISTS valid_price;

ALTER TABLE public.library_strategies
    ADD CONSTRAINT valid_currency
        CHECK (currency IS NULL OR currency IN ('USD', 'INR')),
    ADD CONSTRAINT valid_subscription_tier
        CHECK (subscription_tier IS NULL OR subscription_tier IN ('free', 'pro', 'elite')),
    ADD CONSTRAINT valid_verification_status
        CHECK (verification_status IS NULL OR verification_status IN ('unverified', 'verified', 'suspended')),
    ADD CONSTRAINT valid_evaluation_score
        CHECK (evaluation_score IS NULL OR (evaluation_score >= 0 AND evaluation_score <= 100)),
    ADD CONSTRAINT valid_price
        CHECK (price IS NULL OR price >= 0);

-- 3) Indexes -----------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_library_strategies_price
    ON public.library_strategies(price) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_library_strategies_subscriber_count
    ON public.library_strategies(subscriber_count DESC) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_library_strategies_verification
    ON public.library_strategies(verification_status) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_library_strategies_evaluation_score
    ON public.library_strategies(evaluation_score DESC NULLS LAST) WHERE is_active = TRUE;

COMMIT;

-- VERIFICATION (run after applying):
--   SELECT column_name, data_type, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'public'
--     AND table_name  = 'library_strategies'
--     AND column_name IN ('price','currency','subscription_tier','cover_image',
--                         'verification_status','evaluation_score',
--                         'subscriber_count','deployment_requirements',
--                         'version_history')
--   ORDER BY column_name;
-- Expect 9 rows.
