-- ═══════════════════════════════════════════════════════════════════════════
-- 063_bt01_backtest_result_columns.sql
--
-- BT-01. Every backtest in production failed at the final write with
--   PGRST204 "Could not find the 'daily_returns' column of 'strategy_backtests'
--   in the schema cache"
-- and no backtest row could ever reach status='completed'.
--
-- WHY THE COLUMNS WERE MISSING
-- ───────────────────────────────────────────────────────────────────────────
-- `001_strategy_architecture.sql` declares them in `CREATE TABLE IF NOT EXISTS
-- strategy_backtests`. In production that table already existed with an earlier
-- shape, so the statement was a no-op and the five result columns below were
-- never added. `BacktestService.update_backtest_results` writes every one of
-- them (RESULT_COLUMN_SOURCE_KEYS / RESULT_JSON_COLUMN_SOURCE_KEYS), so the
-- whole UPDATE was rejected.
--
-- NO DEFAULTS, ON PURPOSE. The writer sends SQL NULL for a metric the run did
-- not produce (task 7.8: "a ``0`` that reads like a measurement" is the defect),
-- so these columns must be able to hold NULL and must not invent a zero.
--
-- IDEMPOTENT. ADD COLUMN IF NOT EXISTS, and a postflight that proves the shape
-- rather than assuming the ALTER ran. PostgREST caches the schema, so the final
-- statement asks it to reload.
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE public.strategy_backtests
    ADD COLUMN IF NOT EXISTS winning_trades         integer,
    ADD COLUMN IF NOT EXISTS losing_trades          integer,
    ADD COLUMN IF NOT EXISTS execution_time_seconds numeric(10, 2),
    ADD COLUMN IF NOT EXISTS monthly_returns        jsonb,
    ADD COLUMN IF NOT EXISTS daily_returns          jsonb;

DO $bt01_postflight$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
      FROM unnest(ARRAY['winning_trades', 'losing_trades', 'execution_time_seconds',
                        'monthly_returns', 'daily_returns']) AS c
     WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'strategy_backtests'
           AND column_name = c);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '063 postflight FAILED: strategy_backtests is missing: %', missing;
    END IF;
END
$bt01_postflight$;

NOTIFY pgrst, 'reload schema';
