-- ============================================================================
-- rollback/069_risk01_rollback.sql
--
-- Reverses 069_risk01_risk_violation_audit.sql.
--
-- WHAT THIS DESTROYS. Unlike the 068 rollback, which only drops an index,
-- this drops a TABLE AND ITS ROWS. Those rows are the only durable record of
-- why a customer's bot refused to trade. Once dropped they are not
-- recoverable from the application, because the writer keeps only a bounded
-- in-process mirror.
--
-- Take a copy first if the trail has any value:
--
--     \copy (SELECT * FROM public.risk_violation_audit ORDER BY recorded_at)
--       TO 'risk_violation_audit_backup.csv' WITH CSV HEADER
--
-- AFTER THIS RUNS, record_risk_violation falls back to its in-process
-- dictionary only — the exact defect RISK-01 was raised to fix. The code
-- tolerates the table's absence by design (the audit write is best-effort and
-- never blocks a refusal), so rolling back does NOT stop risk enforcement.
-- It stops the evidence.
-- ============================================================================

DROP TABLE IF EXISTS public.risk_violation_audit;

DO $$
BEGIN
    IF to_regclass('public.risk_violation_audit') IS NOT NULL THEN
        RAISE EXCEPTION
            '069 rollback: public.risk_violation_audit is still present.';
    END IF;

    RAISE NOTICE '069 rollback: public.risk_violation_audit dropped. Risk '
                 'refusals are no longer durable; the in-process mirror is '
                 'all that remains.';
END $$;
