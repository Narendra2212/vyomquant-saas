-- ═══════════════════════════════════════════════════════════════════════════════════════
-- 080 SEC-DEF-01 follow-up -- the profiles trigger creates the referral code through the
--                             body, not through the caller-checking wrapper.
-- ═══════════════════════════════════════════════════════════════════════════════════════
--
-- FOUND 2026-11-20 running the PostgreSQL CI lane against a schema provisioned from this
-- repository (scripts/provision_database.py). `migrations/referral_system_redesign.sql`
-- installs `tr_profiles_create_referral_code` (AFTER INSERT ON public.profiles), whose
-- SECURITY INVOKER function runs `PERFORM create_referral_code_for_user(NEW.id)`. Since
-- 078 that name is the wrapper that refuses any caller other than the user themself or the
-- service role -- and a profile is inserted by the signup trigger / the auth admin, which
-- is neither. Every INSERT INTO public.profiles failed with
--     SEC-DEF-01: this call may act only for the signed-in user
-- i.e. no user could be created on a repository-provisioned database (239 lane failures).
--
-- PRODUCTION DOES NOT HAVE THIS TRIGGER (checked 2026-11-20: no function there calls
-- create_referral_code_for_user except the 078 wrapper itself), so this is a no-op there
-- and signup in production was not affected by 078.
--
-- THE FIX. The trigger function becomes SECURITY DEFINER and calls the body
-- (`create_referral_code_for_user__impl`) directly. A trigger function cannot be invoked as
-- an RPC (it returns `trigger`), so running it as owner opens no new browser-callable path;
-- EXECUTE is revoked from browser roles regardless. The guard stays in front of the
-- user-callable wrapper.
--
-- Idempotent; skipped where the trigger function or the body is absent.
-- ROLLBACK: re-create trigger_create_referral_code() as SECURITY INVOKER calling
--           create_referral_code_for_user(NEW.id) (migrations/referral_system_redesign.sql).
-- ═══════════════════════════════════════════════════════════════════════════════════════

DO $$
BEGIN
    IF to_regprocedure('public.trigger_create_referral_code()') IS NULL
       OR to_regprocedure('public.create_referral_code_for_user__impl(uuid)') IS NULL THEN
        RETURN;
    END IF;

    EXECUTE $fn$
        CREATE OR REPLACE FUNCTION public.trigger_create_referral_code()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $body$
        BEGIN
            PERFORM public.create_referral_code_for_user__impl(NEW.id);
            RETURN NEW;
        END;
        $body$
    $fn$;
    REVOKE ALL ON FUNCTION public.trigger_create_referral_code() FROM PUBLIC, anon, authenticated;
END $$;

-- ── 2. The guard applies to the BROWSER roles, not to every non-JWT caller ──────────────
--
-- 078's guard trusted only `auth.role() = 'service_role'`, which reads the JWT claim. A
-- caller that holds DATABASE credentials -- the service role or the owner on a direct
-- connection, as the PostgreSQL CI lane, operator scripts and the referral-settlement
-- tooling do -- carries no claim, and was refused (paper05 / ref01 lanes, 19 failures).
--
-- The threat 078 closes arrives only through PostgREST, which always switches to the
-- request's role (`anon` / `authenticated` / `service_role`) with SET ROLE. That role is
-- visible as `current_setting('role')`, and it survives the SECURITY DEFINER switch of the
-- wrapper (verified: inside a definer, `current_user` is the owner but `role` is still
-- `authenticated`). So the guard now applies exactly when the caller is in a browser role.
-- Production behaviour is unchanged: every browser request is still checked against
-- auth.uid(); service-role requests were already let through.
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
    IF current_setting('role', true) NOT IN ('anon', 'authenticated') THEN
        RETURN;  -- not a browser role: a database-credentialed caller (service role / owner)
    END IF;
    IF p_user_id IS NULL OR auth.uid() IS NULL OR auth.uid() <> p_user_id THEN
        RAISE EXCEPTION 'SEC-DEF-01: this call may act only for the signed-in user'
            USING ERRCODE = '42501';
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION public.sec_def01_assert_caller_is(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.sec_def01_assert_caller_is(uuid) TO authenticated, service_role;

NOTIFY pgrst, 'reload schema';
