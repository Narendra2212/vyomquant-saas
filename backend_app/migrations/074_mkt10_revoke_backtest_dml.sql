-- ═══════════════════════════════════════════════════════════════════════════
-- 074_mkt10_revoke_backtest_dml.sql
-- MKT-10: `authenticated` loses INSERT/UPDATE/DELETE on `strategy_backtests`
-- ═══════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS CLOSES
-- `strategy_backtests` holds the five backtests the Marketplace Eligibility_Gate counts
-- as a seller's evidence. Measured against production on 2026-11-18, `authenticated`
-- held DELETE/INSERT/SELECT/UPDATE on it. The browser ships the anon key
-- (`algo22-terminal/src/supabase.js`), so any logged-in seller reached PostgREST
-- directly with their own session and could author the evidence the gate was about to
-- read about them -- write five passing backtests, or delete the ones that failed.
--
-- This was the LIVE half of a pair. Its sibling -- INSERT/UPDATE/DELETE on
-- `execution_records`, `orders` and `positions` -- was closed earlier (MKT-07) and was
-- only ever reachable in a database provisioned from repository source; production was
-- already narrower there. This table had no such caveat.
--
-- WHY IT COULD NOT BE REVOKED BEFORE
-- The application's own writer was the CALLER'S request-scoped client, so revoking
-- would have stopped every backtest from persisting. `backend/backtest_service.py` now
-- performs all three writes through the service-role client (`_get_write_supabase` ->
-- `get_service_supabase_async`). `service_role` keeps its grants and is unaffected by a
-- revoke aimed at `authenticated`.
--
-- WHY SELECT IS RETAINED
-- The READS were deliberately not moved: `get_backtest`, `list_backtests`,
-- `get_backtest_history` and `compare_backtests` still run as the caller, so they are
-- filtered by `backtests_owner_access` as well as by their own `user_id` predicate. A
-- read is not a forgery, and keeping SELECT means a future owner-scoped read needs no
-- privilege change. Revoking SELECT here would break Backtest History for every user.
--
-- WHAT ENFORCES OWNERSHIP ON THE WRITES NOW
-- The service-role client is not subject to RLS, so the predicate on each statement is
-- the whole ownership scope: `.eq("id", …).eq("user_id", …)` on the UPDATE and DELETE,
-- and on the INSERT a payload `user_id` taken from the verified JWT (the INSERT never
-- had a WHERE clause -- RLS checked it with `WITH CHECK (user_id = auth.uid())`).
-- `tests/test_mkt10_backtest_writes_are_service_role.py` fails if any predicate is
-- dropped or if a write drifts back onto the caller's client.
--
-- DELIBERATELY NARROW, AND WHY
-- `migrations/hardening/999_privilege_hardening.sql` carries the same change as part of
-- SECTION 1b, but that whole file CANNOT be applied to a live database: it issues
-- `ALTER TABLE … ENABLE ROW LEVEL SECURITY` across every table in `public`, and those
-- ACCESS EXCLUSIVE locks contend with live traffic -- a dry run against production hit
-- `statement timeout`, then `lock timeout`. This migration therefore issues the
-- privilege changes ALONE. It does not touch RLS: 001 already enabled it on this table
-- and 006 installed `backtests_owner_access`, so an `ALTER TABLE` here would be a no-op
-- that still took the heavy lock for nothing.
--
-- IDEMPOTENT: REVOKE and GRANT are both idempotent, and the table's existence is
-- checked, so re-running changes nothing.
--
-- ROLLBACK: backend_app/migrations/rollback/074_mkt10_rollback.sql
-- ═══════════════════════════════════════════════════════════════════════════

BEGIN;

-- CORRECTION TO WHAT THIS COMMENT USED TO SAY. It read: REVOKE "needs only ACCESS
-- EXCLUSIVE on the catalog row, not the table's data, but it still waits behind open
-- transactions that hold the table." The second half is wrong, and it was asserted
-- rather than measured.
--
-- MEASURED on PostgreSQL 16.2 by reading `pg_locks` for the issuing backend from inside
-- the transaction, before rollback:
--
--   REVOKE INSERT,UPDATE,DELETE ON public.strategy_backtests FROM authenticated
--       -> no relation lock on strategy_backtests at all
--   GRANT SELECT ON public.strategy_backtests TO authenticated
--       -> no relation lock on strategy_backtests at all
--   ALTER TABLE public.strategy_backtests ENABLE ROW LEVEL SECURITY  (already enabled)
--       -> AccessExclusiveLock on public.strategy_backtests
--
-- So this migration does not contend with live traffic on the table, and the
-- `lock_timeout` below is cheap insurance rather than the thing that makes it safe.
--
-- The correction matters beyond this file: the statement that made the full hardening
-- file unapplicable to a live database is the ALTER, not the REVOKE -- and `ALTER TABLE`
-- takes that lock even when the change is a no-op. See SECTION 0b of
-- `migrations/hardening/999_privilege_hardening.sql`, which now guards those.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'strategy_backtests'
    ) THEN
        RAISE NOTICE '074: public.strategy_backtests absent; nothing to revoke';
        RETURN;
    END IF;

    -- `anon` is the key that ships in the browser to an UNAUTHENTICATED visitor. It has
    -- no business reading, let alone writing, anyone's backtests. Matches the decision
    -- taken for the three execution ledgers.
    REVOKE ALL ON public.strategy_backtests FROM anon;

    -- The forgery surface. TRUNCATE/REFERENCES/TRIGGER go with it: RLS cannot filter any
    -- of the three, so a policy is no defence against them (see SECTION 0 of the
    -- hardening file).
    REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
        ON public.strategy_backtests FROM authenticated;

    -- Re-granted explicitly rather than assumed: this migration must leave the read
    -- path working whatever the table's prior grant set was.
    GRANT SELECT ON public.strategy_backtests TO authenticated;

    RAISE NOTICE '074: strategy_backtests DML revoked from authenticated; SELECT retained';
END $$;

-- ── proof, inside the transaction, so a wrong outcome rolls back ──────────
-- The migration refuses to commit a state that does not match its own claim. This is
-- the same shape the NOTIF-02 apply used, and it is what makes the apply self-verifying
-- rather than something an operator has to go and check afterwards.
DO $$
DECLARE
    bad text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'strategy_backtests'
    ) THEN
        RETURN;
    END IF;

    SELECT string_agg(format('%s:%s', grantee, privilege_type), ', ' ORDER BY grantee)
      INTO bad
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public'
       AND table_name   = 'strategy_backtests'
       AND (
            (grantee = 'authenticated' AND privilege_type IN
                ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'))
         OR (grantee = 'anon')
       );

    IF bad IS NOT NULL THEN
        RAISE EXCEPTION '074 did not achieve its goal; still present: %', bad;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.role_table_grants
         WHERE table_schema = 'public' AND table_name = 'strategy_backtests'
           AND grantee = 'authenticated' AND privilege_type = 'SELECT'
    ) THEN
        RAISE EXCEPTION '074 removed authenticated''s SELECT; Backtest History would break';
    END IF;

    RAISE NOTICE '074: verified -- authenticated = SELECT only, anon = none';
END $$;

COMMIT;
