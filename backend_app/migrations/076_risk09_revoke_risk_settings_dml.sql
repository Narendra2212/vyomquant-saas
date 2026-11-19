-- ============================================================================
-- 076_risk09_revoke_risk_settings_dml.sql
--
-- RISK-09. `public.risk_settings` grants `authenticated` INSERT and UPDATE, so a
-- customer holding the anon key could PostgREST-write their own risk row directly
-- and set `max_daily_loss`, `max_positions`, `max_leverage` or `kill_switches` to
-- anything, bypassing `PUT /api/risk/settings` and its platform-ceiling clamp.
--
-- Measured read-only against production, 2026-11-18:
--
--     risk_settings      anon            SELECT
--     risk_settings      authenticated   INSERT, SELECT, UPDATE     <-- writable
--
-- WHAT THIS IS NOT, because the distinction decides the severity
-- --------------------------------------------------------------
-- It is NOT an enforcement bypass. `core/risk_manager.validate_trade_request`
-- clamps every limit to the platform ceiling on the order path, independently of
-- this row (RISK-03) -- `min(platform_threshold, requested)`. A forged
-- `max_positions = 100` therefore buys no extra exposure: the engine still
-- enforces its own ceiling.
--
-- What it buys is a LIE, and RISK-03 exists because that matters. A trader who
-- writes a loose limit reads it back unchanged and believes their account permits
-- it, while the engine quietly enforces something tighter. RISK-03's own closing
-- observation was that "a stored limit nothing honours is worse than no limit at
-- all"; this is the route by which such a limit can be stored. It also defeats
-- the clamp's audit value, since the row stops recording what the endpoint
-- decided.
--
-- SAFE BECAUSE THERE IS EXACTLY ONE WRITER, AND IT HAS MOVED
-- ----------------------------------------------------------
-- `routers/risk.py` is the only writer of this table anywhere in the tree, and
-- RISK-09 moved it to the service-role client in the same change as this file.
-- Every other reference in `backend_app` is a SELECT:
--
--     routers/risk.py:511                      select *          (GET /settings)
--     core/risk_manager.py:180                 select *          (order path)
--     dashboard_aggregation_service.py:1062    select 4 columns  (dashboard)
--
-- All three keep `authenticated SELECT` and their RLS layer, so nothing a trader
-- can see changes. Same remedy and same ORDER as MKT-10 for `strategy_backtests`
-- and RISK-07 for `strategy_limits`: move the writer, then take the privilege
-- away. Applying this file BEFORE that code is deployed would make every risk
-- settings save fail with 42501 -- and, because RISK-06 made that failure refuse
-- rather than report success, the trader would at least be told. Do not rely on
-- that; deploy first.
--
-- `anon` loses everything. It is the key that ships in the browser to an
-- UNAUTHENTICATED visitor and has no business reading anyone's risk
-- configuration, let alone writing it. Matches the decision taken for the three
-- execution ledgers and for `strategy_backtests`.
--
-- ROLLBACK: backend_app/migrations/rollback/076_risk09_rollback.sql
-- ============================================================================

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'risk_settings'
    ) THEN
        RAISE NOTICE '076: public.risk_settings absent; nothing to revoke';
        RETURN;
    END IF;

    REVOKE ALL ON public.risk_settings FROM anon;

    -- TRUNCATE/REFERENCES/TRIGGER go with the DML: RLS cannot filter any of the
    -- three, so a policy is no defence against them (SECTION 0 of the hardening
    -- file).
    REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
        ON public.risk_settings FROM authenticated;

    -- Re-granted explicitly rather than assumed, so this migration leaves the read
    -- path working whatever the table's prior grant set was. Three readers depend
    -- on it, one of them the order path.
    GRANT SELECT ON public.risk_settings TO authenticated;

    RAISE NOTICE '076: risk_settings DML revoked from authenticated; SELECT retained';
END $$;

-- ── proof, inside the transaction, so a wrong outcome rolls back ────────────
DO $$
DECLARE
    bad text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'risk_settings'
    ) THEN
        RETURN;
    END IF;

    SELECT string_agg(format('%s:%s', grantee, privilege_type), ', ' ORDER BY grantee)
      INTO bad
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public'
       AND table_name   = 'risk_settings'
       AND (
            (grantee = 'authenticated' AND privilege_type IN
                ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'))
         OR (grantee = 'anon')
       );

    IF bad IS NOT NULL THEN
        RAISE EXCEPTION '076 did not achieve its goal; still present: %', bad;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.role_table_grants
         WHERE table_schema = 'public' AND table_name = 'risk_settings'
           AND grantee = 'authenticated' AND privilege_type = 'SELECT'
    ) THEN
        RAISE EXCEPTION
            '076 removed authenticated''s SELECT. GET /api/risk/settings, the order '
            'path''s own read and the dashboard all depend on it.';
    END IF;

    RAISE NOTICE '076: verified -- authenticated = SELECT only, anon = none';
END $$;

COMMIT;
