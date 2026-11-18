-- ═══════════════════════════════════════════════════════════════════════════
-- rollback/074_mkt10_rollback.sql — undo 074_mkt10_revoke_backtest_dml.sql
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Restores `authenticated`'s INSERT/UPDATE/DELETE on `strategy_backtests`, i.e. puts
-- the forgery surface back. Use it only if the writer move is itself being reverted.
--
-- WHEN YOU WOULD ACTUALLY NEED THIS
-- Only if `backend/backtest_service.py` is rolled back to writing through the caller's
-- request-scoped client. In that order: roll the CODE back first, then this. Running
-- this alone does not fix anything -- it re-opens the hole while the application happily
-- keeps writing as `service_role`, which never needed the grant.
--
-- If backtests have stopped persisting and you are reaching for this file, the likely
-- cause is `SUPABASE_SERVICE_ROLE_KEY` missing from a task definition, not this
-- privilege. Check that first: the service-role client fails closed and raises outside
-- DEV_MODE, so the symptom is a loud 5xx on the backtest path, not silent data loss.
--
-- NOT RESTORED: TRUNCATE, REFERENCES and TRIGGER. 074 revoked them alongside the DML and
-- this file leaves them revoked, because no code path has ever used them and RLS cannot
-- filter them (SECTION 0 of the hardening file). Restoring them would widen the surface
-- beyond what existed before 074 in substance, even though the pre-074 grant set
-- nominally included them.
--
-- `anon` IS ALSO NOT RESTORED. Pre-074 production gave `anon` SELECT on this table. That
-- is the unauthenticated browser key being able to read backtests, and it was wrong
-- independently of MKT-10. Restoring the DML without restoring that is deliberate.
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'strategy_backtests'
    ) THEN
        RAISE NOTICE '074 rollback: public.strategy_backtests absent; nothing to do';
        RETURN;
    END IF;

    GRANT SELECT, INSERT, UPDATE, DELETE ON public.strategy_backtests TO authenticated;

    RAISE WARNING
        '074 rollback: authenticated can write strategy_backtests again. The Marketplace '
        'Eligibility_Gate now reads evidence its own subject can author. Revert '
        'backend/backtest_service.py to the request-scoped client in the same change, or '
        're-apply 074.';
END $$;

COMMIT;
