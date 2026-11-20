-- 072_sup02_rollback.sql
--
-- Reverses 072_sup02_support_ticket_declared_columns.sql.
--
-- READ THIS BEFORE RUNNING IT. Applying this rollback restores the defect 072
-- exists to fix: the support ticket insert names these columns unconditionally,
-- so dropping them makes every `POST /api/support/tickets` raise PGRST204
-- again, be caught by `try/except ... logger.warning`, and answer 200 with a
-- ticket id for a ticket that was never stored.
--
-- IT ALSO DESTROYS DATA, which 070's rollback did not. 070 dropped columns on a
-- table with zero rows. These columns can hold the only record of which
-- strategy or order a reporter was complaining about, and that context does not
-- exist anywhere else -- it is not duplicated into the description. Dropping
-- them discards it irreversibly.
--
-- So: if the intent is to undo the schema change, take a copy first. The
-- statement below is deliberately commented out and the file does nothing until
-- a human uncomments it, because an accidental run is unrecoverable.
--
-- To preserve what would be lost:
--
--   CREATE TABLE IF NOT EXISTS public._sup02_rollback_backup AS
--   SELECT id,
--          related_feature,
--          strategy_id,
--          order_id,
--          attachment
--     FROM public.support_tickets
--    WHERE related_feature IS NOT NULL
--       OR strategy_id     IS NOT NULL
--       OR order_id        IS NOT NULL
--       OR attachment      IS NOT NULL;
--
--   CREATE TABLE IF NOT EXISTS public._sup02_rollback_backup_comments AS
--   SELECT id, attachment
--     FROM public.ticket_comments
--    WHERE attachment IS NOT NULL;
--
-- ============================================================================

-- ─────────────────────────────────────────────────────────────────────────
-- Report what would be lost, so the decision is informed rather than blind.
-- This part is safe and runs.
-- ─────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    ticket_rows  bigint := 0;
    comment_rows bigint := 0;
BEGIN
    IF to_regclass('public.support_tickets') IS NOT NULL THEN
        EXECUTE $q$
            SELECT count(*) FROM public.support_tickets
             WHERE related_feature IS NOT NULL
                OR strategy_id     IS NOT NULL
                OR order_id        IS NOT NULL
                OR attachment      IS NOT NULL
        $q$ INTO ticket_rows;
    END IF;

    IF to_regclass('public.ticket_comments') IS NOT NULL THEN
        EXECUTE $q$
            SELECT count(*) FROM public.ticket_comments
             WHERE attachment IS NOT NULL
        $q$ INTO comment_rows;
    END IF;

    RAISE NOTICE
        '072 rollback: % ticket row(s) and % comment row(s) carry data in the '
        'columns this file would drop. The DROP statements are commented out; '
        'back the data up and uncomment them deliberately.',
        ticket_rows, comment_rows;
END $$;

-- ─────────────────────────────────────────────────────────────────────────
-- Uncomment deliberately, after backing up.
-- ─────────────────────────────────────────────────────────────────────────
-- ALTER TABLE public.support_tickets
--     DROP COLUMN IF EXISTS related_feature,
--     DROP COLUMN IF EXISTS strategy_id,
--     DROP COLUMN IF EXISTS order_id,
--     DROP COLUMN IF EXISTS attachment;
--
-- ALTER TABLE public.ticket_comments
--     DROP COLUMN IF EXISTS attachment;
