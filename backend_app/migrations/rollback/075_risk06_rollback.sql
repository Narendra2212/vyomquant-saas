-- ============================================================================
-- rollback/075_risk06_rollback.sql
--
-- Reverses 075_risk06_risk_settings_circuit_breaker_armed.sql.
--
-- WHAT THIS DESTROYS. Dropping `circuit_breaker_armed` drops every trader's
-- declared armed state. There is no second copy: before 075 the value lived
-- only in a process-local dict that died with the worker, which is the defect
-- 075 fixed. Take a copy first -- it is one small table:
--
--     \copy (SELECT user_id, circuit_breaker_armed, max_daily_loss,
--                   max_positions, max_leverage, kill_switches, updated_at
--              FROM public.risk_settings ORDER BY updated_at)
--       TO 'risk_settings_backup.csv' WITH CSV HEADER
--
-- AFTER THIS RUNS the system returns to the state 075 corrected, and that is a
-- regression, not a neutral undo:
--
--   * `PUT /api/risk/settings` fails again with 42703 on EVERY save, because
--     the handler's record names this column and the upsert is atomic -- so
--     max_daily_loss, max_positions, max_leverage and kill_switches stop
--     persisting too, not just this one field;
--   * with the paired application change in place the endpoint will now
--     RETURN 503 on that failure rather than reporting a false success. That
--     is louder than the original defect and is deliberate: a trader is told
--     their risk limits did not save. Expect save failures to be reported by
--     users immediately, which is the correct outcome for a database that
--     cannot store risk configuration;
--   * the Dashboard's circuit-breaker indicator returns to always showing the
--     default (armed) regardless of what the trader set.
--
-- IF YOU ONLY WANT THE OLD BEHAVIOUR BACK, roll back the application change
-- and KEEP this column. The column is additive and harmless on its own; it is
-- the 503 that changes what a caller sees. Rolling back the column while
-- keeping the 503 is the one combination that makes the endpoint unusable.
--
-- Enforcement is unaffected either way: core/risk_manager.py clamps on the
-- order path and does not read this column.
-- ============================================================================

ALTER TABLE public.risk_settings
    DROP COLUMN IF EXISTS circuit_breaker_armed;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'risk_settings'
           AND column_name = 'circuit_breaker_armed'
    ) THEN
        RAISE EXCEPTION '075 rollback failed: circuit_breaker_armed still present.';
    END IF;

    RAISE NOTICE
        '075 rolled back: circuit_breaker_armed dropped. PUT /api/risk/settings '
        'will fail with 42703 again -- and will now say so with a 503 instead of '
        'claiming success.';
END $$;
