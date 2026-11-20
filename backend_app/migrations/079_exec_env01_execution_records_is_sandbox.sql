-- ═══════════════════════════════════════════════════════════════════════════════════════
-- 079 EXEC-ENV-01 -- execution_records records whether a venue fill was SANDBOX (testnet).
-- Additive: one nullable column, no backfill, no data change.
-- ═══════════════════════════════════════════════════════════════════════════════════════
--
-- FOUND 2026-11-19 in the final launch certification. The marketplace's MP_LIVE_HISTORY
-- criterion counts "live" fills as execution_records rows with an exchange_id -- and a
-- Binance TESTNET fill has one. Nothing in the row says testnet: strategy FROM already had
-- 15.7 h of testnet fills toward the 24 h real-money live-history bar. Testnet activity
-- must never be presented as live performance.
--
-- NULL = not recorded (every row written before this change): the eligibility gate counts
-- only rows explicitly FALSE, so unknown history never qualifies -- fail closed. New rows are
-- stamped at submission from the executor (requested sandbox OR verified sandbox => TRUE).
--
-- ROLLBACK: ALTER TABLE public.execution_records DROP COLUMN IF EXISTS is_sandbox;
ALTER TABLE public.execution_records ADD COLUMN IF NOT EXISTS is_sandbox boolean;

COMMENT ON COLUMN public.execution_records.is_sandbox IS
    'EXEC-ENV-01: TRUE = the venue order went to a sandbox/testnet endpoint; FALSE = real '
    'money; NULL = not recorded (pre-079). Marketplace live history counts FALSE only.';
