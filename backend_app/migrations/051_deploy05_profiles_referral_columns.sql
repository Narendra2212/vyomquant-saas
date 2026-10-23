-- ==========================================================================
-- 051_deploy05_profiles_referral_columns.sql — two columns the application
--                                              writes and production does not have
-- ==========================================================================
--
-- WHAT THIS RECONCILES
-- --------------------
-- `public.profiles.referred_by_user_id` is written by `routers/auth.py:233` with a
-- service-role client when a new account signs up using a referral code:
--
--     admin_client.table("profiles").update({"referred_by_user_id": referrer_id}) ...
--
-- The column does not exist in production, so that write fails on every referral signup.
-- The relationship itself still lands in `referral_relationships` (written immediately
-- above it), so this column is a denormalised convenience copy rather than the system of
-- record — which is why the failure has been invisible rather than fatal.
--
-- `referral_code` is declared alongside it and carries the same UNIQUE constraint, so both
-- are applied together; splitting them would leave the pair half-present, which is the
-- state this file exists to end.
--
-- WHY THIS FILE EXISTS INSTEAD OF RUNNING THE FILE THAT DECLARES THEM
-- -------------------------------------------------------------------
-- `migrations/referral_system_redesign.sql` declares these two columns, and measurement
-- showed NONE of its objects are present in production — neither column, nor
-- `fk_profiles_referred_by`. But that file is a REDESIGN: it also drops and recreates
-- several functions that `migrations/006_reconcile_production_database.sql` declares with
-- DIFFERENT SIGNATURES (`create_referral_code_for_user` RETURNS VARCHAR vs RETURNS VOID,
-- `process_referral_commission` with different parameters — its own header documents the
-- collision). Running it whole against production would replace live function definitions
-- as a side effect of adding two columns.
--
-- So the two column declarations and their constraints are reproduced here VERBATIM from
-- that file, and nothing else from it is applied. The redesign remains a separate decision.
--
-- WHAT THIS DOES NOT ADD, DELIBERATELY
-- ------------------------------------
-- DEPLOY-05 also named `profiles.role`, `profiles.is_admin` and `profiles.api_calls_limit`
-- as missing. They are deliberately NOT added:
--   * `is_admin` and `api_calls_limit` have ZERO references anywhere in `backend_app`.
--   * admin authority comes from the JWT (`app_metadata.role`) — what `routers/admin.py`,
--     the `waitlist` RLS policies and the frontend's AdminGuard all read — and
--     `routers/user.py::get_profile` already treats a profiles-side role as optional with
--     the token as the source of truth.
-- For those three the DATABASE is right and the migration that declares them is
-- aspirational. Adding a column nothing reads is not reconciliation.
--
-- IDEMPOTENT. Columns use IF NOT EXISTS; both constraints are guarded on pg_constraint by
-- name, because a bare ADD CONSTRAINT raises 42710 on a second run and `profiles` has many
-- writers.
-- ==========================================================================

BEGIN;

-- ==========================================================================
-- SECTION 0 — Preflight
-- ==========================================================================
-- `profiles` is provisioned in Supabase rather than by this migration set, so its
-- existence is asserted rather than assumed — the same preflight 017 makes, for the same
-- reason: a readable failure instead of a 42P01 with no context.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'profiles'
    ) THEN
        RAISE EXCEPTION
            'public.profiles does not exist. It is provisioned in Supabase, not by this '
            'migration set. Create it before running '
            '051_deploy05_profiles_referral_columns.sql.';
    END IF;
END $$;

-- ==========================================================================
-- SECTION 1 — The columns
-- ==========================================================================
-- Verbatim from migrations/referral_system_redesign.sql.
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS referral_code VARCHAR(20);
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS referred_by_user_id UUID;

COMMENT ON COLUMN public.profiles.referred_by_user_id IS
    'The account that referred this one, written by routers/auth.py on referral signup '
    'with a service-role client. A denormalised copy: public.referral_relationships is '
    'the system of record for the relationship, and commission/payout logic reads '
    'referral_wallets, never this column. NULL for an account that arrived without a '
    'referral code.';

COMMENT ON COLUMN public.profiles.referral_code IS
    'This account''s own referral code, kept on profiles for backward compatibility with '
    'readers that predate public.referral_codes. UNIQUE where present.';

-- ==========================================================================
-- SECTION 2 — Constraints, each guarded on pg_constraint
-- ==========================================================================
-- ON DELETE SET NULL rather than CASCADE: deleting a referrer must not delete the accounts
-- they referred. Reproduced from the declaring file.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.profiles'::regclass
           AND conname  = 'fk_profiles_referred_by'
    ) THEN
        ALTER TABLE public.profiles
            ADD CONSTRAINT fk_profiles_referred_by
            FOREIGN KEY (referred_by_user_id)
            REFERENCES public.profiles(id)
            ON DELETE SET NULL;
        RAISE NOTICE 'Added fk_profiles_referred_by to public.profiles.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.profiles'::regclass
           AND conname  = 'uq_profiles_referral_code'
    ) THEN
        ALTER TABLE public.profiles
            ADD CONSTRAINT uq_profiles_referral_code UNIQUE (referral_code);
        RAISE NOTICE 'Added uq_profiles_referral_code to public.profiles.';
    END IF;
END $$;

-- ==========================================================================
-- SECTION 3 — Postconditions
-- ==========================================================================
-- Refuse to finish unless the objects exist. The whole reason this file is needed is that
-- a migration was believed applied and was not, so this one proves its own outcome.
DO $$
DECLARE
    problems TEXT;
BEGIN
    SELECT string_agg(expected.what, ', ' ORDER BY expected.what)
      INTO problems
      FROM (VALUES
                ('column profiles.referral_code'),
                ('column profiles.referred_by_user_id')
           ) AS expected(what)
     WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns c
         WHERE c.table_schema = 'public'
           AND c.table_name   = 'profiles'
           AND ('column profiles.' || c.column_name) = expected.what
     );

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION '051 postcondition failed — missing: %', problems;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.profiles'::regclass
           AND conname  = 'fk_profiles_referred_by'
    ) THEN
        RAISE EXCEPTION
            '051 postcondition failed — fk_profiles_referred_by was not created.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.profiles'::regclass
           AND conname  = 'uq_profiles_referral_code'
    ) THEN
        RAISE EXCEPTION
            '051 postcondition failed — uq_profiles_referral_code was not created.';
    END IF;

    RAISE NOTICE '051_deploy05_profiles_referral_columns.sql applied: referral_code, '
                 'referred_by_user_id, fk_profiles_referred_by, '
                 'uq_profiles_referral_code.';
END $$;

COMMIT;
