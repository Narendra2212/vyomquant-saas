-- ═══════════════════════════════════════════════════════════════════════════
-- 064_bt04_backtest_metric_column_widths.sql
--
-- BT-04. A backtest that finished its simulation failed at the final write with
--   22003 "numeric field overflow: A field with precision 6, scale 4 must round to an
--   absolute value less than 10^2"
-- and was recorded as failed with no results.
--
-- CAUSE. `win_rate` was numeric(6,4), which holds |x| < 100. The writer stores the win
-- rate as a PERCENT (0-100, RESULT_COLUMN_SOURCE_KEYS maps it from `win_rate_pct`), so a
-- run in which every trade won — a 100% win rate — could not be stored. Rounding such as
-- 99.99996 -> 100.0000 overflows the same way.
--
-- FIX. Widen, never narrow: every change below only increases precision, so no existing
-- value can fail the cast. The risk-ratio columns are widened as well because they are
-- unbounded by nature (a profit factor with a tiny loss total, a Sharpe over a short
-- window) and were one extreme run away from the same failure.
--
-- IDEMPOTENT: re-running ALTER COLUMN TYPE to the same type is a no-op in effect, and a
-- postflight proves the resulting shape rather than assuming it.
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE public.strategy_backtests
    ALTER COLUMN win_rate      TYPE numeric(8, 4),
    ALTER COLUMN sharpe_ratio  TYPE numeric(14, 4),
    ALTER COLUMN sortino_ratio TYPE numeric(14, 4),
    ALTER COLUMN profit_factor TYPE numeric(14, 4);

DO $bt04_postflight$
DECLARE
    narrow text;
BEGIN
    SELECT string_agg(column_name || '(' || numeric_precision || ',' || numeric_scale || ')', ', ')
      INTO narrow
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'strategy_backtests'
       AND ((column_name = 'win_rate' AND numeric_precision < 8)
         OR (column_name IN ('sharpe_ratio', 'sortino_ratio', 'profit_factor') AND numeric_precision < 14));
    IF narrow IS NOT NULL THEN
        RAISE EXCEPTION '064 postflight FAILED: still too narrow: %', narrow;
    END IF;
END
$bt04_postflight$;

NOTIFY pgrst, 'reload schema';
