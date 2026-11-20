-- ============================================================================
-- 077_mkt11_listing_disclosures.sql
--
-- T15 C. The seller-authored disclosures a buyer is shown before paying. Today
-- `marketplace_submissions` has nowhere to put them: its columns are
-- `submission_state`, `eligibility_outcomes`, `evaluator_version`,
-- `rejection_reason`, `reviewed_by` and four timestamps. It is a state machine
-- about ADMISSION, and nine prose-and-money fields with a different lifecycle do
-- not belong under its invariants.
--
-- Measured on a cluster provisioned from repo source, 2026-11-19:
--
--     library_strategies.id            uuid      <- the Listing
--     library_strategies.author_id     uuid
--     library_strategies.symbol        text      <- a SINGLE symbol, which is
--                                                   why supported_pairs exists
--     marketplace_submissions.listing_id  uuid   -> library_strategies(id)
--     strategy_versions.version        varchar   <- the version spelling used here
--
-- WHY A SEPARATE TABLE, NOT NINE COLUMNS
-- --------------------------------------
-- Disclosures are PER-PUBLICATION and must not change under a buyer who already
-- paid on them. A subscriber bought on the strength of the drawdown and capital
-- figures shown at purchase time, so those figures need their own row with its
-- own immutability -- the same argument `strategy_versions` makes for the node
-- graph. Mutable columns on a workflow record cannot carry that.
--
-- Keyed by `(listing_id, version)` for exactly that reason: re-publishing writes
-- a NEW row rather than editing the one a purchase referenced.
--
-- WHAT THIS DELIBERATELY DOES NOT DO
-- ----------------------------------
--   1. It adds no `billing_period_days`. The T15 C decision record asked for one
--      "replacing the hard-coded 30 at listing_projection.py:300". That is a
--      misreading and building it would have been a defect: the Subscription_Period
--      IS one calendar month (Requirement 11.4, and the glossary at requirements
--      line 119), the 30 is a catalogue LABEL whose own comment states that "no
--      arithmetic consumes it", and the authoritative period is computed by
--      `subscription_period.period_for_activation` from the payment confirmation
--      instant. A seller-settable day count would be a number shown to a buyer
--      that does not govern their access -- a stored value nothing honours, which
--      is the defect RISK-03 and RISK-09 both exist because of.
--
--   2. It publishes nothing. This file declares storage only. Requirement 23.2
--      says the Listing_Projection's allow-list "SHALL be limited to" an
--      enumeration that covers trading pairs and exchange compatibility (as
--      "asset and market information") but does NOT name recommended capital,
--      risk classification, leverage information, strategy limitations or
--      investor disclosures. Exposing those five is a requirements amendment, not
--      a migration, and is the one open decision in T15 C. Storing them is
--      correct under either answer, which is why this file can land first.
--
-- PRIVILEGE POSTURE, FOLLOWING MKT-10 AND RISK-09 RATHER THAN RE-ARGUING IT
-- ------------------------------------------------------------------------
-- These are SELLER CLAIMS shown to buyers, which makes them evidence in exactly
-- the sense MKT-10 established: a customer-writable evidence surface is forgeable.
-- So `authenticated` gets SELECT only and every write goes through the API's
-- service-role client, which is where validation lives. `anon` gets nothing --
-- the public catalogue is served by the backend's own projection, not by a direct
-- PostgREST read, so no anon grant is needed and one would only widen the surface.
--
-- IMMUTABILITY IS ENFORCED, NOT DOCUMENTED. `trg_listing_disclosures_immutable`
-- refuses any UPDATE or DELETE once `published_at` is set, including from
-- `service_role`. A trigger rather than a policy because policies do not apply to
-- the service role, and the service role is precisely the writer that must also be
-- unable to rewrite history under a paid subscriber.
--
-- Idempotent throughout (Requirements 24.7, 24.8) and verifies its own outcome
-- inside the transaction, so a wrong result rolls back rather than lands.
-- Rollback: rollback/077_mkt11_rollback.sql
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 0. Preflight. Fail with a named dependency rather than a bare 42P01 from
--    inside a REFERENCES clause -- the reason migration 007 gives for the same
--    guard.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'library_strategies'
    ) THEN
        RAISE EXCEPTION
            'PREFLIGHT: public.library_strategies is absent, so listing_id has '
            'nothing to reference. Run the earlier migrations first.';
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 1. The table.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.marketplace_listing_disclosures (
    listing_id  uuid NOT NULL
        REFERENCES public.library_strategies(id) ON DELETE CASCADE,
    -- `varchar`, matching `strategy_versions.version`, so the two spellings of a
    -- version cannot drift into text-vs-uuid confusion.
    version     varchar(64) NOT NULL,

    -- ── 1. trading pairs ────────────────────────────────────────────────────
    -- An ARRAY because `library_strategies.symbol` holds one symbol and a listing
    -- may be run on several. Constrained, not free text: this gates execution, and
    -- a subscriber's paper session resolves its feed from it.
    supported_pairs          text[] NOT NULL,

    -- ── 2. exchange compatibility ───────────────────────────────────────────
    supported_exchanges      text[] NOT NULL,

    -- ── 3/4. recommended capital ────────────────────────────────────────────
    -- `numeric`, never float: these are money. Currency is carried beside them
    -- because an amount without one is not an amount.
    min_recommended_capital  numeric(20, 8),
    max_recommended_capital  numeric(20, 8),
    capital_currency         char(3),

    -- ── 5. seller-declared maximum drawdown ─────────────────────────────────
    -- A DIFFERENT FACT from the derived `max_drawdown_pct` the projection computes
    -- from the publish-time backtest aggregate (listing_projection.py:197-200).
    -- Both belong, and the separate table is what makes that structural rather
    -- than a naming convention. Stored as a percentage, 0-100.
    declared_max_drawdown_pct numeric(6, 3),

    -- ── 6. risk classification ──────────────────────────────────────────────
    risk_classification      text,

    -- ── 7. leverage ─────────────────────────────────────────────────────────
    -- Both halves: the number an execution path could consult, and the prose a
    -- buyer reads. A number alone cannot say "2x on majors only".
    max_leverage             numeric(6, 2),
    leverage_disclosure      text,

    -- ── 8/9. prose disclosures ──────────────────────────────────────────────
    strategy_limitations     text,
    investor_disclosures     text,

    -- ── provenance ──────────────────────────────────────────────────────────
    -- NULL until the row is published. Setting it freezes the row (section 5).
    published_at             timestamptz,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pk_listing_disclosures PRIMARY KEY (listing_id, version)
);

-- ---------------------------------------------------------------------------
-- 1a. Array validation helper.
--
--     A CHECK constraint may not contain a subquery -- the first version of this
--     file tried `NOT EXISTS (SELECT 1 FROM unnest(...))` inline and PostgreSQL
--     refused it with "cannot use subquery in check constraint". An IMMUTABLE
--     function is the standard way round it: the constraint calls a function, and
--     the subquery lives inside the function rather than in the constraint.
--
--     IMMUTABLE is correct and required here -- the result depends only on the
--     arguments, with no table or setting consulted -- and it is what lets the
--     planner use it in a CHECK at all.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.disclosure_tags_are_valid(
    tags      text[],
    max_count integer,
    max_len   integer
) RETURNS boolean
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT tags IS NOT NULL
       AND array_length(tags, 1) IS NOT NULL
       AND array_length(tags, 1) BETWEEN 1 AND max_count
       -- No NULL element: a NULL in a tag list is an unanswered question, and the
       -- execution path that reads it cannot tell it from "all pairs".
       AND array_position(tags, NULL) IS NULL
       -- No blank and nothing oversized.
       AND NOT EXISTS (
            SELECT 1 FROM unnest(tags) AS t
            WHERE btrim(t) = '' OR length(t) > max_len
       )
$$;

-- ---------------------------------------------------------------------------
-- 2. Constraints, added separately and idempotently so re-running is safe.
--
--    Every one of these refuses a value that would MISLEAD A BUYER, which is the
--    only reason to constrain a disclosure at the column rather than in the route.
--    The route validates too; this is the floor that survives a new writer.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    -- At least one pair, no empty strings, and a bound on how many so the array
    -- cannot become a dumping ground.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'chk_listing_disclosures_pairs') THEN
        ALTER TABLE public.marketplace_listing_disclosures
            ADD CONSTRAINT chk_listing_disclosures_pairs CHECK (
                public.disclosure_tags_are_valid(supported_pairs, 64, 32)
            );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'chk_listing_disclosures_exchanges') THEN
        ALTER TABLE public.marketplace_listing_disclosures
            ADD CONSTRAINT chk_listing_disclosures_exchanges CHECK (
                public.disclosure_tags_are_valid(supported_exchanges, 32, 32)
            );
    END IF;

    -- Capital: non-negative, and max not below min. A max below min is not a
    -- typo a buyer can detect.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'chk_listing_disclosures_capital') THEN
        ALTER TABLE public.marketplace_listing_disclosures
            ADD CONSTRAINT chk_listing_disclosures_capital CHECK (
                (min_recommended_capital IS NULL OR min_recommended_capital >= 0)
                AND (max_recommended_capital IS NULL OR max_recommended_capital >= 0)
                AND (
                    min_recommended_capital IS NULL
                    OR max_recommended_capital IS NULL
                    OR max_recommended_capital >= min_recommended_capital
                )
            );
    END IF;

    -- An amount without a currency is not an amount; a currency must be an ISO
    -- 4217-shaped uppercase code.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'chk_listing_disclosures_currency') THEN
        ALTER TABLE public.marketplace_listing_disclosures
            ADD CONSTRAINT chk_listing_disclosures_currency CHECK (
                (
                    min_recommended_capital IS NULL
                    AND max_recommended_capital IS NULL
                ) = (capital_currency IS NULL)
                AND (capital_currency IS NULL OR capital_currency ~ '^[A-Z]{3}$')
            );
    END IF;

    -- A drawdown is a percentage. 0-100 inclusive; a declared 0 is a real claim
    -- and is permitted, while a negative or >100 figure is meaningless.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'chk_listing_disclosures_drawdown') THEN
        ALTER TABLE public.marketplace_listing_disclosures
            ADD CONSTRAINT chk_listing_disclosures_drawdown CHECK (
                declared_max_drawdown_pct IS NULL
                OR (declared_max_drawdown_pct >= 0 AND declared_max_drawdown_pct <= 100)
            );
    END IF;

    -- A closed vocabulary, because a free-text risk band cannot be compared
    -- between listings and is therefore worse than none.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'chk_listing_disclosures_risk_class') THEN
        ALTER TABLE public.marketplace_listing_disclosures
            ADD CONSTRAINT chk_listing_disclosures_risk_class CHECK (
                risk_classification IS NULL
                OR risk_classification IN ('conservative', 'moderate', 'aggressive', 'speculative')
            );
    END IF;

    -- Leverage at or above 1. Below 1 is not deleveraging, it is a wrong unit.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'chk_listing_disclosures_leverage') THEN
        ALTER TABLE public.marketplace_listing_disclosures
            ADD CONSTRAINT chk_listing_disclosures_leverage CHECK (
                max_leverage IS NULL OR (max_leverage >= 1 AND max_leverage <= 125)
            );
    END IF;

    -- Prose ceilings. Generous, but a disclosure field is not a blob store.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'chk_listing_disclosures_prose') THEN
        ALTER TABLE public.marketplace_listing_disclosures
            ADD CONSTRAINT chk_listing_disclosures_prose CHECK (
                (leverage_disclosure IS NULL OR length(leverage_disclosure) <= 2000)
                AND (strategy_limitations IS NULL OR length(strategy_limitations) <= 4000)
                AND (investor_disclosures IS NULL OR length(investor_disclosures) <= 8000)
            );
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 3. Index. The read is "the disclosures for this listing, most recent
--    publication first"; the primary key serves lookup by (listing_id, version)
--    but not that ordering.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_listing_disclosures_published
    ON public.marketplace_listing_disclosures (listing_id, published_at DESC NULLS LAST);

-- ---------------------------------------------------------------------------
-- 4. RLS and privileges.
--
--    `authenticated` holds SELECT ONLY. Writes go through the API's service-role
--    client, for the reason MKT-10 established: a customer-writable evidence
--    surface is forgeable, and these rows are the seller's claims about their own
--    product. `anon` is granted nothing.
-- ---------------------------------------------------------------------------
ALTER TABLE public.marketplace_listing_disclosures ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS listing_disclosures_owner_read
    ON public.marketplace_listing_disclosures;
CREATE POLICY listing_disclosures_owner_read
    ON public.marketplace_listing_disclosures
    FOR SELECT
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.library_strategies ls
            WHERE ls.id = marketplace_listing_disclosures.listing_id
              AND ls.author_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS listing_disclosures_service_role
    ON public.marketplace_listing_disclosures;
CREATE POLICY listing_disclosures_service_role
    ON public.marketplace_listing_disclosures
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

REVOKE ALL ON public.marketplace_listing_disclosures FROM anon;
REVOKE ALL ON public.marketplace_listing_disclosures FROM authenticated;
GRANT SELECT ON public.marketplace_listing_disclosures TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.marketplace_listing_disclosures TO service_role;

-- ---------------------------------------------------------------------------
-- 5. Immutability once published.
--
--    A TRIGGER, not a policy, and deliberately so: RLS policies do not apply to
--    the service role, and the service role is exactly the writer that must also
--    be unable to rewrite a figure a subscriber already paid on. Re-publishing
--    inserts a new `(listing_id, version)` row instead.
--
--    `published_at` may be set once (NULL -> a value). Everything else about a
--    published row is frozen.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.refuse_published_disclosure_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.published_at IS NOT NULL THEN
            RAISE EXCEPTION
                'marketplace_listing_disclosures(%, %) was published at % and cannot be '
                'deleted: a subscriber may have purchased on these figures. Publish a new '
                'version instead.', OLD.listing_id, OLD.version, OLD.published_at;
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.published_at IS NOT NULL THEN
        RAISE EXCEPTION
            'marketplace_listing_disclosures(%, %) was published at % and is immutable: a '
            'subscriber may have purchased on these figures. Publish a new version instead.',
            OLD.listing_id, OLD.version, OLD.published_at;
    END IF;

    NEW.updated_at := now();
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_listing_disclosures_immutable
    ON public.marketplace_listing_disclosures;
CREATE TRIGGER trg_listing_disclosures_immutable
    BEFORE UPDATE OR DELETE ON public.marketplace_listing_disclosures
    FOR EACH ROW EXECUTE FUNCTION public.refuse_published_disclosure_change();

-- ---------------------------------------------------------------------------
-- 6. Verify the outcome INSIDE the transaction, so a wrong result rolls back.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    v_cols      int;
    v_checks    int;
    v_policies  int;
    v_rls       boolean;
    v_anon      int;
    v_auth      text;
BEGIN
    SELECT count(*) INTO v_cols
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'marketplace_listing_disclosures';

    SELECT count(*) INTO v_checks
    FROM pg_constraint
    WHERE conrelid = 'public.marketplace_listing_disclosures'::regclass AND contype = 'c';

    SELECT count(*) INTO v_policies
    FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'marketplace_listing_disclosures';

    SELECT relrowsecurity INTO v_rls
    FROM pg_class WHERE oid = 'public.marketplace_listing_disclosures'::regclass;

    SELECT count(*) INTO v_anon
    FROM information_schema.role_table_grants
    WHERE table_schema = 'public' AND table_name = 'marketplace_listing_disclosures'
      AND grantee = 'anon';

    SELECT coalesce(string_agg(DISTINCT privilege_type, ',' ORDER BY privilege_type), '')
    INTO v_auth
    FROM information_schema.role_table_grants
    WHERE table_schema = 'public' AND table_name = 'marketplace_listing_disclosures'
      AND grantee = 'authenticated';

    -- 16: listing_id, version, supported_pairs, supported_exchanges, the two
    -- capital bounds, capital_currency, declared_max_drawdown_pct,
    -- risk_classification, max_leverage, leverage_disclosure,
    -- strategy_limitations, investor_disclosures, published_at, created_at,
    -- updated_at. Pinned as a number so a column added without updating this
    -- file's header fails here rather than silently.
    IF v_cols <> 16 THEN
        RAISE EXCEPTION 'VERIFY: expected 16 columns, found %', v_cols;
    END IF;
    IF v_checks < 8 THEN
        RAISE EXCEPTION 'VERIFY: expected at least 8 CHECK constraints, found %', v_checks;
    END IF;
    IF v_policies <> 2 THEN
        RAISE EXCEPTION 'VERIFY: expected 2 policies, found %', v_policies;
    END IF;
    IF NOT v_rls THEN
        RAISE EXCEPTION 'VERIFY: row level security is not enabled';
    END IF;
    IF v_anon <> 0 THEN
        RAISE EXCEPTION 'VERIFY: anon holds % privileges and must hold none', v_anon;
    END IF;
    IF v_auth <> 'SELECT' THEN
        RAISE EXCEPTION
            'VERIFY: authenticated holds "%" and must hold SELECT only -- a seller must '
            'not be able to write the claims a buyer reads (MKT-10)', v_auth;
    END IF;

    RAISE NOTICE 'OK 077: % columns, % checks, % policies, RLS on, anon none, authenticated SELECT',
        v_cols, v_checks, v_policies;
END $$;

COMMIT;
