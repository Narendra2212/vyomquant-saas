-- ============================================================================
-- rollback/068_strat01_rollback.sql
--
-- Reverses 068_strat01_strategy_name_uniqueness.sql.
--
-- WHAT THIS UNDOES, AND WHAT IT DOES NOT.
-- It drops the index and nothing else. It does NOT un-archive any row that
-- `scripts/reconcile_duplicate_strategy_names.py` archived — that script is
-- separately reversible and prints the exact UPDATE needed to restore each
-- row it touched. Those are two decisions and they roll back independently,
-- which is the reason the reconciliation was never put in the migration.
--
-- AFTER THIS RUNS, duplicate live strategy names become possible again. If
-- the create path has not yet been given an idempotency key, the double
-- submit that produced the 2026-09-27 group of six can recur.
--
-- CONCURRENTLY, AND WHY IT IS HERE RATHER THAN IN THE MIGRATION.
-- DROP INDEX takes an ACCESS EXCLUSIVE lock on public.strategies for the
-- duration. On the measured 168 live rows that is milliseconds. On a much
-- larger table, run the concurrent form instead, OUTSIDE any transaction
-- block (it cannot run inside one, which is why the migration runner cannot
-- carry it):
--
--     DROP INDEX CONCURRENTLY IF EXISTS public.uq_strategies_owner_name_live;
--
-- The matching out-of-band CREATE, for the same reason, is:
--
--     CREATE UNIQUE INDEX CONCURRENTLY uq_strategies_owner_name_live
--         ON public.strategies (user_id, lower(name))
--         WHERE archived_at IS NULL;
--
-- If a CONCURRENTLY build is interrupted it leaves an INVALID index that
-- still enforces nothing but does occupy space. Find it with:
--
--     SELECT i.relname, idx.indisvalid
--       FROM pg_index idx JOIN pg_class i ON i.oid = idx.indexrelid
--      WHERE i.relname = 'uq_strategies_owner_name_live';
--
-- and drop it before retrying.
-- ============================================================================

DROP INDEX IF EXISTS public.uq_strategies_owner_name_live;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public'
           AND tablename  = 'strategies'
           AND indexname  = 'uq_strategies_owner_name_live'
    ) THEN
        RAISE EXCEPTION
            '068 rollback: uq_strategies_owner_name_live is still present.';
    END IF;

    RAISE NOTICE '068 rollback: uq_strategies_owner_name_live dropped. '
                 'Duplicate live strategy names are possible again.';
END $$;
