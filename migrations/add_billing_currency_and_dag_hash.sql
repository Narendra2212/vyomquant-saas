-- ============================================================
-- Supabase Migration: Add Missing Billing & Strategy Columns
-- Run this in the Supabase SQL Editor (production project)
-- ============================================================

-- 1. Add currency preference columns to profiles table
--    These are queried by /api/billing/currency endpoint.
--    Without them, PricingService silently falls back to USD (safe,
--    no 500), but users cannot persist their currency preference.

ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS preferred_currency TEXT DEFAULT 'USD'
    CHECK (preferred_currency IN ('USD', 'INR')),
  ADD COLUMN IF NOT EXISTS billing_currency TEXT DEFAULT 'USD'
    CHECK (billing_currency IN ('USD', 'INR'));

-- Create index for fast currency lookups
CREATE INDEX IF NOT EXISTS idx_profiles_preferred_currency
  ON profiles (preferred_currency);

-- 2. Add dag_hash column to strategies table.
--    Currently dag_hash is stored inside the buy_logic JSONB blob
--    as a workaround. This column enables direct indexing & integrity checks.

ALTER TABLE strategies
  ADD COLUMN IF NOT EXISTS dag_hash TEXT,
  ADD COLUMN IF NOT EXISTS dag_version INTEGER DEFAULT 1,
  ADD COLUMN IF NOT EXISTS dag_schema_version TEXT,
  ADD COLUMN IF NOT EXISTS execution_order JSONB;

-- Backfill dag_hash from existing JSONB storage
UPDATE strategies
  SET dag_hash = buy_logic->>'_dag_hash'
WHERE dag_hash IS NULL
  AND buy_logic IS NOT NULL
  AND buy_logic->>'_dag_hash' IS NOT NULL;

-- Create index for dag_hash lookups
CREATE INDEX IF NOT EXISTS idx_strategies_dag_hash
  ON strategies (dag_hash)
  WHERE dag_hash IS NOT NULL;

-- 3. Verify columns exist (diagnostic query - no-op otherwise)
SELECT
  column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name IN ('profiles', 'strategies')
  AND column_name IN (
    'preferred_currency', 'billing_currency',
    'dag_hash', 'dag_version', 'dag_schema_version', 'execution_order'
  )
ORDER BY table_name, column_name;
