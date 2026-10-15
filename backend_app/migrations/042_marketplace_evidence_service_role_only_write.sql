-- backend_app/migrations/042_marketplace_evidence_service_role_only_write.sql
--
-- SEC-09 (P2). Migration 007's grants let `authenticated` INSERT directly into
-- `marketplace_backtest_evidence` and `marketplace_price_evaluations` -- the two tables
-- that exist specifically to carry an INDEPENDENT record of how a marketplace listing's
-- strategy actually performed and what it should be priced. Confirmed by reading the
-- real application code (backend/marketplace/submission_service.py,
-- backend/marketplace/pricing_evaluator.py, routers/library.py): every legitimate read
-- AND write of these two tables already goes through a service-role client
-- (`_build_service_client()`/`svc`), never the caller's own JWT. The `authenticated`
-- INSERT grant and the `*_owner_access` RLS policy (migration 007, `FOR ALL TO
-- authenticated USING (auth.uid() = owner_id) WITH CHECK (auth.uid() = owner_id)`) are
-- therefore not used by anything this platform does -- they exist only as a surface that
-- lets a marketplace listing's own owner write directly into their own "independent"
-- evidence/price-evaluation record via a raw PostgREST call with their own access token,
-- fabricating backtest results or a price evaluation a buyer would otherwise reasonably
-- take as an attestation this platform computed.
--
-- The fix is exactly what this finding's own remediation note asks for: revoke the
-- `authenticated` WRITE privilege; nothing else. SELECT is left untouched (migration 007
-- granted it; this migration does not re-litigate read access, which this finding does not
-- name). The RLS policy itself is also left in place -- once INSERT is not GRANTed at the
-- table level, a satisfied `WITH CHECK` can never be reached, so narrowing the GRANT alone
-- closes the write path without touching the policy migration 007 already reasoned about.
--
-- `marketplace_submissions` (also authenticated-writable per migration 007) is
-- deliberately NOT touched here -- this finding names price evaluations and backtest
-- evidence specifically, and the submissions table's own direct-write grant is a separate
-- question this migration does not decide.
--
-- Idempotent: REVOKE is idempotent by definition.

REVOKE INSERT ON public.marketplace_backtest_evidence FROM authenticated;
REVOKE INSERT ON public.marketplace_price_evaluations  FROM authenticated;

-- Postflight: authenticated can still read (migration 007's intent, unchanged) but can no
-- longer write either table; service_role is unaffected.
DO $$
BEGIN
    IF has_table_privilege('authenticated', 'public.marketplace_backtest_evidence', 'INSERT') THEN
        RAISE EXCEPTION 'postflight failed: authenticated can still INSERT into marketplace_backtest_evidence';
    END IF;
    IF has_table_privilege('authenticated', 'public.marketplace_price_evaluations', 'INSERT') THEN
        RAISE EXCEPTION 'postflight failed: authenticated can still INSERT into marketplace_price_evaluations';
    END IF;
    IF NOT has_table_privilege('authenticated', 'public.marketplace_backtest_evidence', 'SELECT') THEN
        RAISE EXCEPTION 'postflight failed: authenticated lost SELECT on marketplace_backtest_evidence, which this migration does not intend to change';
    END IF;
    IF NOT has_table_privilege('authenticated', 'public.marketplace_price_evaluations', 'SELECT') THEN
        RAISE EXCEPTION 'postflight failed: authenticated lost SELECT on marketplace_price_evaluations, which this migration does not intend to change';
    END IF;
    IF NOT has_table_privilege('service_role', 'public.marketplace_backtest_evidence', 'INSERT') THEN
        RAISE EXCEPTION 'postflight failed: service_role lost INSERT on marketplace_backtest_evidence';
    END IF;
    IF NOT has_table_privilege('service_role', 'public.marketplace_price_evaluations', 'INSERT') THEN
        RAISE EXCEPTION 'postflight failed: service_role lost INSERT on marketplace_price_evaluations';
    END IF;
END
$$;
