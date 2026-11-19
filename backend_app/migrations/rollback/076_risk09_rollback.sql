-- ============================================================================
-- rollback/076_risk09_rollback.sql
--
-- Reverses 076_risk09_revoke_risk_settings_dml.sql.
--
-- WHAT THIS RESTORES, stated as the exposure it is: `authenticated` regains
-- INSERT and UPDATE on `public.risk_settings`, so any customer holding the anon
-- key can PostgREST-write their own risk row again and store a limit this
-- platform's endpoint would have clamped.
--
-- It is not an enforcement bypass -- `core/risk_manager.validate_trade_request`
-- clamps on the order path regardless -- but it lets a trader read back a limit
-- the engine will not honour, which is the defect RISK-03 exists to prevent.
--
-- NO DATA IS AT RISK EITHER WAY. This file changes privileges only; no row is
-- read, written or dropped. That makes it safe to run in an incident, which is
-- the case it exists for.
--
-- ONLY ROLL THIS BACK IF 076 IS IMPLICATED IN AN INCIDENT. The realistic one:
-- the service-role writer is unavailable (SUPABASE_SERVICE_ROLE_KEY missing or
-- rotated) and every risk settings save is refusing with 503
-- `RISK_SETTINGS_PERSIST_FAILED`. Restoring the grant does NOT fix that on its
-- own -- `routers/risk.py` asks for the service-role client explicitly and
-- raises rather than falling back to the caller's, deliberately, so that a
-- missing key cannot silently re-widen the write surface. Fixing the key is the
-- remedy; this rollback is only for the case where the code has also been
-- reverted.
--
-- `anon` is NOT restored. It held SELECT before 076 and nothing in the product
-- reads risk configuration as an unauthenticated visitor, so returning it would
-- re-open a surface no feature needs. If some future anonymous read genuinely
-- requires it, grant it deliberately in its own migration with that reader
-- named.
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'risk_settings'
    ) THEN
        RAISE NOTICE '076 rollback: public.risk_settings absent; nothing to restore';
        RETURN;
    END IF;

    GRANT INSERT, UPDATE ON public.risk_settings TO authenticated;

    RAISE NOTICE
        '076 rolled back: authenticated regained INSERT/UPDATE on risk_settings. A '
        'customer can write their own risk row directly again. anon was NOT restored.';
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'risk_settings'
    ) THEN
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.role_table_grants
         WHERE table_schema = 'public' AND table_name = 'risk_settings'
           AND grantee = 'authenticated' AND privilege_type = 'INSERT'
    ) THEN
        RAISE EXCEPTION '076 rollback failed: authenticated still has no INSERT.';
    END IF;
END $$;
