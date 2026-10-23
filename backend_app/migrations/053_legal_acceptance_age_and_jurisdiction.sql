-- ==========================================================================
-- 053_legal_acceptance_age_and_jurisdiction.sql — LEGAL-01
--
-- RECORD THE TWO REPRESENTATIONS THE TERMS ASK FOR AND NOTHING CAPTURED.
--
-- Terms of Service section 3 makes the user represent two things:
--
--   "You must be at least 18 years old, or the age of majority where you live
--    if that is higher, and you must have legal capacity to contract."
--
--   "You must not use the Platform if you are located in, ordinarily resident
--    in, or acting on behalf of a person in a country or region subject to
--    comprehensive sanctions..."
--
-- Both were sentences in a document with NO MECHANISM BEHIND THEM. 052 recorded
-- WHICH documents a user accepted; it did not record these two assertions, so
-- the platform could not show that any user had made them.
--
-- WHY COLUMNS ON legal_acceptances RATHER THAN A NEW TABLE
-- -------------------------------------------------------
-- Both are made at the same instant, by the same act, as accepting the Terms
-- that contain them. Splitting them into their own table would allow a state
-- where someone accepted the Terms but has no age declaration, or vice versa,
-- which is not a state the signup flow can produce and not one worth being able
-- to represent. They belong on the acceptance row.
--
-- NULLABLE, AND THAT IS DELIBERATE
-- --------------------------------
-- The one row 052 already wrote (the verification sentinel) predates these
-- columns, and so would any acceptance recorded between 052 and this file. A NOT
-- NULL column with a backfilled default would manufacture a representation the
-- user never made -- a fabricated legal declaration, which is worse than an
-- honest NULL. NULL means "not asked", and the API requires both for any NEW
-- acceptance, so NULL can only ever mean a pre-053 row.
--
-- THE JURISDICTION COLUMN IS A DECLARATION, NOT A VERIFIED FACT
-- ------------------------------------------------------------
-- It records what the user said. There is no IP geolocation, no KYC and no
-- document check anywhere in this schema, so nothing corroborates it. Named
-- `declared_country` rather than `country` for exactly that reason: a future
-- reader must not mistake it for a verified attribute. See
-- backend_app/core/restricted_jurisdictions.py, which is explicit that this is a
-- declared-country gate and NOT sanctions screening.
--
-- IDEMPOTENCY
-- -----------
-- ADD COLUMN IF NOT EXISTS throughout; constraints guarded on pg_constraint.
-- Re-running changes nothing.
-- ==========================================================================

BEGIN;

-- ==========================================================================
-- SECTION 0 — Preflight
-- ==========================================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'legal_acceptances'
    ) THEN
        RAISE EXCEPTION
            '053 precondition failed: public.legal_acceptances does not exist. '
            'Apply 052_legal_acceptance.sql first.';
    END IF;
END $$;

-- ==========================================================================
-- SECTION 1 — The two representations
-- ==========================================================================

-- The user's assertion that they meet the age requirement. TRUE is the only
-- value the API will write; FALSE is admissible so that a refusal could be
-- recorded deliberately in future rather than being unrepresentable.
ALTER TABLE public.legal_acceptances
    ADD COLUMN IF NOT EXISTS age_confirmed BOOLEAN;

-- ISO 3166-1 alpha-2, as DECLARED by the user. Not verified. See the header.
ALTER TABLE public.legal_acceptances
    ADD COLUMN IF NOT EXISTS declared_country TEXT;

-- ==========================================================================
-- SECTION 2 — Constraints
-- ==========================================================================
-- Shape only. Whether a given code is PERMITTED is decided in
-- restricted_jurisdictions.py, not here: the restricted list is a legal input
-- that counsel must be able to change without a migration, and encoding it in a
-- CHECK would make every revision a schema change and would silently invalidate
-- historical rows recorded under a previous list.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'chk_legal_acceptances_declared_country_shape'
    ) THEN
        ALTER TABLE public.legal_acceptances
            ADD CONSTRAINT chk_legal_acceptances_declared_country_shape
            CHECK (declared_country IS NULL OR declared_country ~ '^[A-Z]{2}$');
    END IF;
END $$;

-- ==========================================================================
-- SECTION 3 — Index
-- ==========================================================================
-- Supports the operational question "where do our users say they are", which is
-- the only read this column has. Partial, because NULL rows are pre-053 and
-- carry no declaration to aggregate.
CREATE INDEX IF NOT EXISTS idx_legal_acceptances_declared_country
    ON public.legal_acceptances (declared_country)
    WHERE declared_country IS NOT NULL;

-- ==========================================================================
-- SECTION 4 — Documentation
-- ==========================================================================
COMMENT ON COLUMN public.legal_acceptances.age_confirmed IS
    'LEGAL-01: the user''s assertion that they meet the age requirement in Terms '
    'section 3 (18+, or the local age of majority if higher). A REPRESENTATION, '
    'not a verified fact -- there is no age or identity verification anywhere in '
    'this schema. NULL means the row predates this column, never that the user '
    'declined.';

COMMENT ON COLUMN public.legal_acceptances.declared_country IS
    'LEGAL-01: ISO 3166-1 alpha-2 country the user DECLARED at acceptance, used '
    'to enforce the Terms'' restricted-jurisdiction clause. DECLARED, NOT '
    'VERIFIED: no IP geolocation, KYC or document check corroborates it, and a '
    'user can simply state a different country. See core/'
    'restricted_jurisdictions.py, which is explicit that this is a '
    'declared-country gate and NOT sanctions screening. NULL means the row '
    'predates this column.';

COMMIT;

-- ==========================================================================
-- VERIFICATION (run separately)
-- ==========================================================================
--   SELECT declared_country, age_confirmed, count(*)
--     FROM public.legal_acceptances
--    GROUP BY 1, 2 ORDER BY 3 DESC;
--
--   -- shape constraint holds (must raise):
--   -- INSERT INTO public.legal_acceptances
--   --   (user_id, document_version, documents, declared_country)
--   -- VALUES (gen_random_uuid(), '2026-10-23', ARRAY['terms'], 'india');
-- ==========================================================================
