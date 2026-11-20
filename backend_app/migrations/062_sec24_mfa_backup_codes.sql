-- ═══════════════════════════════════════════════════════════════════════════
-- 062_sec24_mfa_backup_codes.sql
--
-- SEC-24. Storage for the lost-2FA-device recovery mechanism in
-- `backend_app/backend/mfa_backup_codes.py` and `routers/security.py`.
--
-- WHY THIS EXISTS
-- ───────────────────────────────────────────────────────────────────────────
-- `pages/TwoFA.jsx`'s `handleResetFactor` can reset a TOTP factor, but only
-- from an already-authenticated session. A trader with no device and no other
-- logged-in session had no self-service way back in. Supabase Auth's own MFA
-- API has no recovery-code factor type, so this is a small custom mechanism:
-- ten single-use codes, generated server-side, whose SHA-256 hash is the only
-- form ever stored.
--
-- TWO TABLES
-- ───────────────────────────────────────────────────────────────────────────
--   * `mfa_backup_codes`         — one row per issued code. `code_hash` is a
--     SHA-256 hex digest; the plaintext is never written anywhere, including
--     here. `consumed_at` is set exactly once, by the verifying UPDATE's own
--     `WHERE consumed_at IS NULL` filter, which is what makes a code single-
--     use under concurrent requests racing the same code.
--   * `mfa_backup_code_attempts` — one row per account, tracking failed
--     verification attempts and an optional lockout, independent of this
--     account's set of codes so a lockout survives a code being consumed and
--     clears on a full regenerate (`store_backup_codes` deletes it).
--
-- NO RLS POLICY IS GRANTED TO `anon` OR `authenticated`
-- ───────────────────────────────────────────────────────────────────────────
-- Every access to these two tables goes through the service-role client in
-- `backend_app/backend/mfa_backup_codes.py`, which runs the hash comparison,
-- the one-time-use UPDATE and the rate-limit bookkeeping server-side. RLS is
-- enabled on both tables with NO policy for `anon`/`authenticated`, so even a
-- future client-side Supabase query against them is refused by default rather
-- than needing a policy author to remember to restrict it. The service role
-- bypasses RLS, as it does everywhere else in this codebase.
--
-- IDEMPOTENT, matching the numbered set's own convention: `CREATE TABLE IF
-- NOT EXISTS`, `ADD COLUMN IF NOT EXISTS` were it ever needed, and a
-- postflight that proves the shape rather than assuming the CREATE ran.
-- ═══════════════════════════════════════════════════════════════════════════

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 1 — mfa_backup_codes
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.mfa_backup_codes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    code_hash   text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    consumed_at timestamptz NULL
);

COMMENT ON TABLE public.mfa_backup_codes IS
    'SEC-24. One row per issued MFA backup/recovery code. `code_hash` is a
     SHA-256 hex digest of the normalised plaintext (backend/mfa_backup_codes.py
     ::hash_backup_code); the plaintext itself is returned to the trader exactly
     once, by the generate endpoint, and is never persisted. `consumed_at` is set
     once, by an UPDATE filtered on `consumed_at IS NULL`, which is what makes a
     code single-use. Regenerating deletes every row for the account (used or
     not) and inserts a fresh ten — see `store_backup_codes`.';

COMMENT ON COLUMN public.mfa_backup_codes.code_hash IS
    'SHA-256 hex digest of the normalised code. Never a plaintext code, never
     reversible, and not looked up by anything other than an exact-match
     equality filter from the service-role client.';

-- One account should not hold two live rows hashing to the same value; in
-- practice the alphabet and length make a collision astronomically unlikely,
-- but the constraint costs nothing and documents the single-use invariant at
-- the schema level too.
CREATE UNIQUE INDEX IF NOT EXISTS mfa_backup_codes_user_hash_uidx
    ON public.mfa_backup_codes (user_id, code_hash);

-- The verifying UPDATE and the generate/regenerate DELETE both filter on
-- `user_id`; the common case additionally filters `consumed_at IS NULL`.
CREATE INDEX IF NOT EXISTS mfa_backup_codes_user_unconsumed_idx
    ON public.mfa_backup_codes (user_id)
    WHERE consumed_at IS NULL;

ALTER TABLE public.mfa_backup_codes ENABLE ROW LEVEL SECURITY;
-- Deliberately no policy: every access is through the service-role client,
-- which bypasses RLS. `anon` and `authenticated` are refused by the absence
-- of a permissive policy, not by a restrictive one that could be forgotten.

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 2 — mfa_backup_code_attempts
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.mfa_backup_code_attempts (
    user_id        uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    failed_attempts integer NOT NULL DEFAULT 0,
    locked_until    timestamptz NULL,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.mfa_backup_code_attempts IS
    'SEC-24. Per-account failed-attempt counter for MFA backup-code
     verification, independent of the slowapi per-IP limit the router also
     applies. Five failures (MAX_FAILED_ATTEMPTS) sets `locked_until` fifteen
     minutes out (LOCKOUT_SECONDS); a successful verify, or a regenerate of the
     account''s codes, deletes this row outright.';

ALTER TABLE public.mfa_backup_code_attempts ENABLE ROW LEVEL SECURITY;
-- Same reasoning as above: service-role only, no client-facing policy.

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 3 — Postflight: the shape this feature depends on must be real.
-- ─────────────────────────────────────────────────────────────────────────
DO $sec24_postflight$
BEGIN
    IF to_regclass('public.mfa_backup_codes') IS NULL THEN
        RAISE EXCEPTION '062 postflight FAILED: public.mfa_backup_codes was not created.';
    END IF;

    IF to_regclass('public.mfa_backup_code_attempts') IS NULL THEN
        RAISE EXCEPTION '062 postflight FAILED: public.mfa_backup_code_attempts was not created.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'mfa_backup_codes'
           AND column_name = 'consumed_at'
    ) THEN
        RAISE EXCEPTION
            '062 postflight FAILED: mfa_backup_codes.consumed_at is missing; a '
            'backup code could never be marked used.';
    END IF;

    RAISE NOTICE '062: mfa_backup_codes and mfa_backup_code_attempts present with RLS enabled.';
END
$sec24_postflight$;
