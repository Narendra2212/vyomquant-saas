-- ═══════════════════════════════════════════════════════════════════════════════════════
-- 078 SEC-DEF-01 -- SECURITY DEFINER functions reachable with the anon key or another
--                   user's id, closed. Narrowing only: no table, column or row changes.
-- ═══════════════════════════════════════════════════════════════════════════════════════
--
-- FOUND 2026-11-19 in the final launch certification (Supabase security advisor, then
-- reproduced). Each function below runs as its owner (SECURITY DEFINER, so RLS does not
-- apply), takes the TARGET user's id from the caller, and checks nothing about the caller:
--
--   increment_ml_addon(target_user_id)          anon EXECUTE -- REPRODUCED: an unauthenticated
--                                               POST /rest/v1/rpc/increment_ml_addon with only
--                                               the public anon key raised the QA account's
--                                               ml_addons_purchased 0 -> 1 (reverted). 023
--                                               declared this revoke; production still had
--                                               `=X` and `anon=X` -- drift, not a new bug.
--   execute_secure_v2 / _v3(target_id, amount)  anon EXECUTE -- debits profiles.balance of ANY
--                                               user. No caller in the application.
--   reverse_referral_commission(payment_id)     anon EXECUTE -- reverses any referrer's
--                                               commission. Only caller: billing.py through
--                                               a SERVICE-ROLE client.
--   create_referral_code_for_user(user_uuid)    anon EXECUTE -- writes codes/wallets for any
--                                               user. Caller: referral.py, USER-scoped client.
--   create_referral_payout_request(p_user_id..) authenticated EXECUTE -- any signed-in user can
--                                               file a payout request against ANOTHER user's
--                                               wallet, to payout details they choose. Caller:
--                                               referral.py, USER-scoped client.
--   paper_apply_fill_atomic(p_user_id, ...)     authenticated EXECUTE -- any signed-in user can
--                                               write fills into another user's paper account.
--                                               Caller: the paper simulator.
--
-- THE FIX, BY CALLER
--   * Service-role-only primitives (no browser caller): EXECUTE revoked from PUBLIC, anon and
--     authenticated. service_role keeps it.
--   * User-facing RPCs: the body is renamed to <name>__impl (service_role only) and a thin
--     wrapper with the SAME name, arguments and result takes its place. The wrapper refuses
--     (SQLSTATE 42501) unless the caller IS the target user or is the service role, then
--     delegates. Callers change nothing. An `auth.uid()` guard is used here and not for the
--     service-role primitives because these ARE called with the user's own JWT.
--
-- Idempotent: every step checks the catalogue first, so a re-run is a no-op.
--
-- ROLLBACK (restores the pre-078 state exactly):
--   GRANT EXECUTE ON FUNCTION public.increment_ml_addon(uuid) TO PUBLIC, anon, authenticated;
--   ... likewise execute_secure_v2(uuid,numeric), execute_secure_v3(uuid,numeric,uuid),
--       reverse_referral_commission(varchar,text);
--   then for each wrapped function F:  DROP FUNCTION public.F(<args>);
--                                      ALTER FUNCTION public.F__impl(<args>) RENAME TO F;
--   and re-grant as recorded above (create_referral_code_for_user: PUBLIC, anon,
--   authenticated; the other two: authenticated).
-- ═══════════════════════════════════════════════════════════════════════════════════════

-- ── 1. Service-role-only primitives ─────────────────────────────────────────────────────
DO $$
DECLARE
    sig text;
BEGIN
    FOREACH sig IN ARRAY ARRAY[
        'public.increment_ml_addon(uuid)',
        'public.execute_secure_v2(uuid, numeric)',
        'public.execute_secure_v3(uuid, numeric, uuid)',
        'public.reverse_referral_commission(character varying, text)'
    ] LOOP
        IF to_regprocedure(sig) IS NOT NULL THEN
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon, authenticated', sig);
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO service_role', sig);
        END IF;
    END LOOP;
END $$;

-- ── 2. User-facing RPCs: move the body aside, put a caller-checking wrapper in front ────
DO $$
BEGIN
    IF to_regprocedure('public.create_referral_code_for_user__impl(uuid)') IS NULL
       AND to_regprocedure('public.create_referral_code_for_user(uuid)') IS NOT NULL THEN
        ALTER FUNCTION public.create_referral_code_for_user(uuid)
            RENAME TO create_referral_code_for_user__impl;
    END IF;

    IF to_regprocedure('public.create_referral_payout_request__impl(uuid, numeric, text, jsonb)') IS NULL
       AND to_regprocedure('public.create_referral_payout_request(uuid, numeric, text, jsonb)') IS NOT NULL THEN
        ALTER FUNCTION public.create_referral_payout_request(uuid, numeric, text, jsonb)
            RENAME TO create_referral_payout_request__impl;
    END IF;

    IF to_regprocedure('public.paper_apply_fill_atomic__impl(uuid, uuid, uuid, uuid, text, bigint, text, uuid, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb)') IS NULL
       AND to_regprocedure('public.paper_apply_fill_atomic(uuid, uuid, uuid, uuid, text, bigint, text, uuid, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb)') IS NOT NULL THEN
        ALTER FUNCTION public.paper_apply_fill_atomic(uuid, uuid, uuid, uuid, text, bigint, text, uuid, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb)
            RENAME TO paper_apply_fill_atomic__impl;
    END IF;
END $$;

-- The bodies are reachable only by the service role and by the wrappers (which run as owner).
DO $$
DECLARE
    sig text;
BEGIN
    FOREACH sig IN ARRAY ARRAY[
        'public.create_referral_code_for_user__impl(uuid)',
        'public.create_referral_payout_request__impl(uuid, numeric, text, jsonb)',
        'public.paper_apply_fill_atomic__impl(uuid, uuid, uuid, uuid, text, bigint, text, uuid, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb)'
    ] LOOP
        IF to_regprocedure(sig) IS NOT NULL THEN
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon, authenticated', sig);
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO service_role', sig);
        END IF;
    END LOOP;
END $$;

-- The single caller rule: the target user themself, or the service role.
CREATE OR REPLACE FUNCTION public.sec_def01_assert_caller_is(p_user_id uuid)
RETURNS void
LANGUAGE plpgsql
STABLE
SET search_path = public
AS $$
BEGIN
    IF coalesce(auth.role(), '') = 'service_role' THEN
        RETURN;
    END IF;
    IF p_user_id IS NULL OR auth.uid() IS NULL OR auth.uid() <> p_user_id THEN
        RAISE EXCEPTION 'SEC-DEF-01: this call may act only for the signed-in user'
            USING ERRCODE = '42501';
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION public.sec_def01_assert_caller_is(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.sec_def01_assert_caller_is(uuid) TO authenticated, service_role;

CREATE OR REPLACE FUNCTION public.create_referral_code_for_user(user_uuid uuid)
RETURNS character varying
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    PERFORM public.sec_def01_assert_caller_is(user_uuid);
    RETURN public.create_referral_code_for_user__impl(user_uuid);
END;
$$;

CREATE OR REPLACE FUNCTION public.create_referral_payout_request(
    p_user_id uuid,
    p_amount_usd numeric,
    p_payout_method text,
    p_payout_details jsonb DEFAULT '{}'::jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    PERFORM public.sec_def01_assert_caller_is(p_user_id);
    RETURN public.create_referral_payout_request__impl(
        p_user_id, p_amount_usd, p_payout_method, p_payout_details
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.paper_apply_fill_atomic(
    p_user_id uuid,
    p_account_id uuid,
    p_order_id uuid,
    p_session_id uuid,
    p_fill_event_id text,
    p_expected_account_version bigint,
    p_expected_order_state text,
    p_resume_fill_id uuid,
    p_fill jsonb,
    p_account jsonb,
    p_balance_event jsonb,
    p_order jsonb,
    p_position jsonb,
    p_closed_leg jsonb,
    p_trade jsonb,
    p_equity_snapshot jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    PERFORM public.sec_def01_assert_caller_is(p_user_id);
    RETURN public.paper_apply_fill_atomic__impl(
        p_user_id, p_account_id, p_order_id, p_session_id, p_fill_event_id,
        p_expected_account_version, p_expected_order_state, p_resume_fill_id,
        p_fill, p_account, p_balance_event, p_order, p_position, p_closed_leg,
        p_trade, p_equity_snapshot
    );
END;
$$;

-- Wrapper grants: exactly the roles that legitimately call each one.
REVOKE ALL ON FUNCTION public.create_referral_code_for_user(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.create_referral_code_for_user(uuid) TO authenticated, service_role;

REVOKE ALL ON FUNCTION public.create_referral_payout_request(uuid, numeric, text, jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.create_referral_payout_request(uuid, numeric, text, jsonb) TO authenticated, service_role;

REVOKE ALL ON FUNCTION public.paper_apply_fill_atomic(uuid, uuid, uuid, uuid, text, bigint, text, uuid, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.paper_apply_fill_atomic(uuid, uuid, uuid, uuid, text, bigint, text, uuid, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb) TO authenticated, service_role;

NOTIFY pgrst, 'reload schema';
