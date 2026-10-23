-- ==========================================================================
-- 052_legal_acceptance.sql — LEGAL-01
--
-- RECORD WHICH VERSION OF THE LEGAL DOCUMENTS EACH USER ACCEPTED.
--
-- WHAT EXISTED BEFORE: NOTHING.
-- -----------------------------
-- The signup page rendered a checkbox and refused to submit without it
-- (algo22-terminal/src/pages/AuthPage.jsx: `agreedToPolicies` useState, guard
-- at the submit handler). That flag was React state. It gated the local form
-- and WAS NEVER SENT ANYWHERE — not to Supabase Auth, not to any endpoint, not
-- to any table. No `accepted_terms_version`, `terms_accepted`,
-- `legal_acceptance` or equivalent column exists in any of the 51 preceding
-- migrations.
--
-- So the platform could not answer "which terms did this user agree to", for
-- any user, ever. With 182 existing accounts and documents that are about to
-- change materially, that is the question that matters.
--
-- WHY A TABLE AND NOT A COLUMN ON profiles
-- ----------------------------------------
-- A single `profiles.accepted_terms_version` column would be an UPDATE each
-- time the documents change, overwriting the fact that the user once accepted
-- the earlier version. The useful artefact is the HISTORY: which versions this
-- person has accepted, when, and from where. A dispute about a term that was
-- introduced in one version is answered by the row for that version, which an
-- overwriting column destroys.
--
-- So this table is APPEND-ONLY by design and by trigger (section 5). Declining
-- a new version is represented by the ABSENCE of a row, never by mutating one.
--
-- WHY NO FOREIGN KEY TO auth.users
-- --------------------------------
-- `auth.users` is Supabase-managed and lives in a schema this migration set
-- does not own. Every other user-scoped table here (strategy_deployments,
-- strategy_backtests, signals, copilot_sessions) declares `user_id UUID NOT
-- NULL` without a reference for exactly this reason, and 051 learned the same
-- lesson the hard way when a REFERENCES on a Supabase-managed table had to be
-- reconsidered. Consistency with the surrounding schema wins over a constraint
-- that cannot be relied on.
--
-- RLS: READ YOUR OWN, WRITE THROUGH THE SERVER ONLY
-- -------------------------------------------------
-- A user may read their own acceptances — they are entitled to know what they
-- agreed to. NO browser-side INSERT policy exists, deliberately: an acceptance
-- the client could write unaided is worth nothing as evidence, because the
-- client is the party whose agreement is being recorded. Writes come from the
-- backend over the service role, which bypasses RLS, after the server has
-- validated the version against its own constant (core/legal_documents.py).
-- This is the same posture 043 takes for paper trading and 047 for payment
-- methods.
--
-- IDEMPOTENCY
-- -----------
-- Re-running changes nothing. The table, indexes and constraint use IF NOT
-- EXISTS; PostgreSQL has no CREATE POLICY IF NOT EXISTS, so each policy is
-- guarded on pg_policies by schemaname + tablename + policyname — the pattern
-- 018 section 4, 017 section 3d and 008 section 6 already use, rather than
-- DROP POLICY IF EXISTS … CREATE POLICY, which would open a window in which
-- the table is policy-less.
-- ==========================================================================

BEGIN;

-- ==========================================================================
-- SECTION 0 — Preflight
-- ==========================================================================
-- The policies below are granted to the Supabase `authenticated` role. On a
-- database where that role does not exist, CREATE POLICY fails with a bare
-- undefined_object and no context. Fail readably instead — the same read-only
-- assertion 018 section 0 and 004b section 0 make.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        RAISE EXCEPTION
            '052 precondition failed: role "authenticated" does not exist. '
            'This migration targets a Supabase database, where that role, '
            'anon and service_role are created by the platform.';
    END IF;
END $$;

-- ==========================================================================
-- SECTION 1 — public.legal_acceptances
-- ==========================================================================
CREATE TABLE IF NOT EXISTS public.legal_acceptances (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- No FK: see the header. auth.users is Supabase-managed.
    user_id         UUID        NOT NULL,

    -- The version identity of the documents the user was shown, e.g.
    -- '2026-10-23'. Validated server-side against
    -- core/legal_documents.LEGAL_VERSION before this row is written, so an
    -- arbitrary client-supplied string cannot be recorded as published text.
    document_version TEXT       NOT NULL,

    -- Which documents the acceptance covered. Stored explicitly rather than
    -- implied by the version, because the REQUIRED set can itself change
    -- between versions and a row must remain self-describing years later.
    documents       TEXT[]      NOT NULL,

    -- WHEN. The whole point of the record.
    accepted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Evidentiary context. Both nullable: a record is still worth having when
    -- a proxy strips them, and inventing a value would be worse than NULL.
    ip_address      TEXT,
    user_agent      TEXT,

    -- What the user was doing when they accepted: 'signup' for the initial
    -- acceptance, 're_acceptance' when prompted after a version change.
    -- Constrained so a typo cannot create a third silent category.
    context         TEXT        NOT NULL DEFAULT 'signup',

    CONSTRAINT chk_legal_acceptances_context
        CHECK (context IN ('signup', 're_acceptance', 'backfill')),

    -- A non-empty version that looks like a date. Cheap, and it stops a blank
    -- or obviously wrong value from being stored even if a future caller
    -- bypasses the Python validation.
    CONSTRAINT chk_legal_acceptances_version_shape
        CHECK (document_version ~ '^\d{4}-\d{2}-\d{2}$'),

    -- At least one document. An acceptance covering nothing is not one.
    CONSTRAINT chk_legal_acceptances_documents_nonempty
        CHECK (array_length(documents, 1) >= 1)
);

-- ==========================================================================
-- SECTION 2 — One acceptance per user per version
-- ==========================================================================
-- A user clicking accept twice, or a retried request, must not create two
-- rows: the second tells us nothing the first did not and would make "has this
-- user accepted?" a counting question. The unique index makes the insert
-- naturally idempotent, so the API layer can use ON CONFLICT DO NOTHING and
-- treat a duplicate as success rather than as an error to surface.
CREATE UNIQUE INDEX IF NOT EXISTS uq_legal_acceptances_user_version
    ON public.legal_acceptances (user_id, document_version);

-- Backs the common read: "what is this user's latest acceptance?"
CREATE INDEX IF NOT EXISTS idx_legal_acceptances_user_accepted_at
    ON public.legal_acceptances (user_id, accepted_at DESC);

-- Backs the operational read: "who has not accepted the current version?"
CREATE INDEX IF NOT EXISTS idx_legal_acceptances_version
    ON public.legal_acceptances (document_version);

-- ==========================================================================
-- SECTION 3 — Row level security
-- ==========================================================================
ALTER TABLE public.legal_acceptances ENABLE ROW LEVEL SECURITY;

-- 3a) A user may READ their own acceptances. They are entitled to know what
--     they agreed to and when, and the Privacy Policy's access right promises
--     exactly this.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'legal_acceptances'
           AND policyname = 'legal_acceptances_owner_select'
    ) THEN
        CREATE POLICY legal_acceptances_owner_select ON public.legal_acceptances
            FOR SELECT TO authenticated
            USING (user_id = auth.uid());
    END IF;
END $$;

-- 3b) THERE IS DELIBERATELY NO INSERT, UPDATE OR DELETE POLICY.
--     RLS denies by default, so the browser can read its own rows and change
--     nothing. An acceptance the accepting party can write, edit or remove
--     unaided is not evidence of anything. The backend writes over the service
--     role, which bypasses RLS, after validating the version server-side.
--
--     SEC-20 note: this database grants privileges to anon/authenticated on
--     newly created tables via a default ACL, which is why 047 and 043 had to
--     revoke explicitly. Do the same here rather than relying on RLS alone —
--     RLS without a privilege is belt and braces, and the braces were missing
--     on `plan_usage_ledger` until the default-ACL fix landed.
REVOKE ALL ON public.legal_acceptances FROM anon;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.legal_acceptances FROM authenticated;
GRANT SELECT ON public.legal_acceptances TO authenticated;

-- ==========================================================================
-- SECTION 4 — Append-only, enforced
-- ==========================================================================
-- The service role bypasses RLS, so RLS cannot make this append-only against
-- the one writer that matters. A trigger can, and it protects the record from
-- a future bug in our own backend rather than from a hostile client.
--
-- An acceptance is a historical assertion: this person agreed to this text at
-- this moment. There is no legitimate reason to edit or delete one. Correcting
-- a mistake means recording a NEW acceptance, which the unique index in
-- section 2 permits for a different version.
CREATE OR REPLACE FUNCTION public.legal_acceptances_refuse_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'legal_acceptances is append-only: a recorded acceptance is a historical '
        'fact and cannot be % . Record a new acceptance instead.',
        lower(TG_OP);
END $$;

DROP TRIGGER IF EXISTS trg_legal_acceptances_append_only
    ON public.legal_acceptances;
CREATE TRIGGER trg_legal_acceptances_append_only
    BEFORE UPDATE OR DELETE ON public.legal_acceptances
    FOR EACH ROW EXECUTE FUNCTION public.legal_acceptances_refuse_mutation();

-- ==========================================================================
-- SECTION 5 — Documentation
-- ==========================================================================
COMMENT ON TABLE public.legal_acceptances IS
    'LEGAL-01: append-only record of which version of the Terms, Privacy '
    'Policy, Risk Disclosure and Refund Policy each user accepted, and when. '
    'Before this table the signup checkbox was client-side React state that '
    'was never persisted, so the platform could not answer which terms any '
    'user had agreed to. Written only by the backend over the service role '
    'after validating the version against core/legal_documents.LEGAL_VERSION; '
    'users may read their own rows and cannot write any. Append-only is '
    'enforced by trg_legal_acceptances_append_only, not merely by convention.';

COMMENT ON COLUMN public.legal_acceptances.document_version IS
    'Version identity of the documents shown, YYYY-MM-DD. Must match a version '
    'this deployment publishes; the server refuses an unrecognised value rather '
    'than recording agreement to text that was never published.';

COMMENT ON COLUMN public.legal_acceptances.documents IS
    'The document ids this acceptance covered. Stored explicitly rather than '
    'implied by the version, so the row stays self-describing if the required '
    'set changes in a later version.';

COMMENT ON COLUMN public.legal_acceptances.context IS
    'signup | re_acceptance | backfill. backfill exists for records created '
    'administratively rather than by a user action, and must be '
    'distinguishable from a real acceptance.';

COMMIT;

-- ==========================================================================
-- VERIFICATION (run separately; this file does not assert its own success)
-- ==========================================================================
--   SELECT  document_version, context, count(*)
--     FROM  public.legal_acceptances
--    GROUP  BY 1, 2
--    ORDER  BY 1 DESC, 2;
--
--   -- who has not accepted the current version:
--   SELECT  p.id, p.email
--     FROM  public.profiles p
--     LEFT  JOIN public.legal_acceptances la
--             ON la.user_id = p.id
--            AND la.document_version = '2026-10-23'
--    WHERE  la.id IS NULL;
--
--   -- append-only actually holds (both must raise):
--   -- UPDATE public.legal_acceptances SET context = 'signup' WHERE true;
--   -- DELETE FROM public.legal_acceptances WHERE true;
-- ==========================================================================
