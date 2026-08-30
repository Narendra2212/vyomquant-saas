-- 007_marketplace_submissions.sql  (backend_app/migrations, migration 007)
--
-- PURPOSE
--   Create the Submission side of the Marketplace: the review-workflow row,
--   its append-only transition history, the immutable Backtest_Evidence
--   copy, the Price_Range evaluations, and the two-column transition seed
--   table that makes Requirement 4.4's "THE Persistence_Layer SHALL reject
--   the write" true against a direct SQL UPDATE rather than only against a
--   service module.
--
--   Requirements 1.2, 2.8, 3.9, 3.10, 4.5, 4.12, 4.13, 7.4, 8.4, 21.2,
--   21.3, 24.1, 24.2, 24.3, 24.4, 24.5, 24.7, 24.9.
--   Source of the DDL: design.md -> "Data Models" (the five tables and the
--   library_strategies additive columns) and design.md -> "Where the state
--   lives and how the database is the arbiter" (the guard function, the
--   partial unique index and the projection trigger), statement for
--   statement, schema-qualified with public. and with every ADD CONSTRAINT
--   put behind a pg_constraint guard (see DELIBERATE ADDITIONS below).
--
-- WHAT THIS FILE ADDS
--     1  marketplace_submission_allowed_transitions  the eleven-pair seed
--     2  marketplace_submissions                     the review row
--     3  marketplace_submission_transitions          append-only history
--     4  marketplace_backtest_evidence               the immutable copy
--     5  marketplace_price_evaluations               the Price_Range record
--     6  library_strategies                          five additive columns
--     7  four functions and six triggers
--     8  row level security, policies, grants
--     9  comments, postflight
--
--   Nothing about marketplace_settlements, library_subscriptions, the
--   paper_* tables or signals.environment is here. Those are migrations 008
--   through 010, which depend on this file.
--
-- WHY THE SEED TABLE IS A TABLE AND NOT AN INLINE CASE
--   marketplace_submission_allowed_transitions holds the same eleven pairs
--   as SUBMISSION_TRANSITIONS in
--   backend_app/backend/marketplace/submission_state.py. It is a table
--   because tests/test_submission_state_agreement.py (task 14.6) reads the
--   INSERT statements of section 1 and that Python constant and asserts they
--   are the SAME SET - the technique
--   tests/test_version_consumer_agreement.py already uses for the three
--   spellings of the canonical version column. An inline CASE inside the
--   trigger function would be a second definition of the state machine that
--   no test could compare against the first, and the two would drift the
--   first time a state was added.
--
--   The eleven pairs, and no twelfth (Requirement 4.2):
--     DRAFT        -> SUBMITTED
--     SUBMITTED    -> UNDER_REVIEW, REJECTED
--     UNDER_REVIEW -> APPROVED, REJECTED
--     APPROVED     -> PUBLISHED
--     PUBLISHED    -> SUSPENDED, UNPUBLISHED
--     SUSPENDED    -> PUBLISHED, UNPUBLISHED
--     REJECTED     -> DRAFT
--     UNPUBLISHED  -> (terminal, no row)
--   No state lists itself, so a same-value write is not a transition at all
--   and is short-circuited by the guard's first branch rather than by a
--   separate self-transition rule. UNPUBLISHED contributes no row, which is
--   exactly what makes it terminal.
--
-- WHY THE MODERATION PROJECTION IS A TRIGGER
--   Requirement 4.12 demands ONE shared definition of Submission_State ->
--   library_strategies.moderation_status, and Requirement 4.6 demands that
--   "every public catalogue response served after that transaction commits
--   includes the Listing". A handler writing library_strategies itself
--   would satisfy neither: a second write can be forgotten, can be skipped
--   by a direct SQL UPDATE, and can be spelled differently at each call
--   site. trg_submission_projects_moderation_status applies
--   MODERATION_STATUS_FOR_STATE and IS_ACTIVE_FOR_STATE from
--   submission_state.py inside the same transaction as the transition, so
--   the legacy column cannot drift from the review lifecycle and the
--   existing .eq("is_active", True).in_("moderation_status",
--   ["approved","featured"]) predicates in backend_app/routers/library.py
--   keep working unchanged in MEANING (Requirement 25.1).
--
--   The mapping, one line per state, written as an 8-branch plpgsql CASE
--   with no ELSE fall-through so that a ninth state cannot be projected by
--   accident:
--     DRAFT, SUBMITTED, UNDER_REVIEW, APPROVED -> 'pending'
--     PUBLISHED                                -> 'approved'
--     REJECTED, SUSPENDED, UNPUBLISHED         -> 'rejected'
--     is_active = TRUE for PUBLISHED and for nothing else
--   APPROVED deliberately stays 'pending': approved-but-not-yet-published
--   must remain invisible to the catalogue (Requirement 4.7). 'featured' is
--   never produced - featuring stays library_strategies.is_featured, which
--   this projection does not touch.
--
-- WHY chk_ls_featured_requires_published IS ADDED "NOT VALID" FIRST
--   design.md specifies
--     chk_ls_featured_requires_published
--       CHECK (is_featured = FALSE OR moderation_status = 'approved')
--   and backend_app/routers/library.py's admin_moderate_strategy currently
--   writes moderation_status = 'featured' together with is_featured = TRUE
--   (line 1750). A production library_strategies can therefore already hold
--   rows this constraint refuses, and a plain ADD CONSTRAINT would be
--   VALIDATED against them and would ABORT this whole transaction - which
--   Requirement 24.8's "applies from an empty database and from a database
--   at the current production schema revision" forbids.
--
--   So the constraint is added NOT VALID (it applies to every subsequent
--   INSERT and UPDATE from that moment - future writes are fully enforced),
--   and then VALIDATE CONSTRAINT is attempted inside an exception block. On
--   an empty or already-clean database the VALIDATE succeeds and the
--   constraint ends up ordinary and fully validated. On a database carrying
--   legacy 'featured' rows the VALIDATE fails, the savepoint rolls back, the
--   constraint stays NOT VALID, and a NOTICE names the offending row count
--   so an operator can reconcile them. Either way the invariant holds for
--   every new write, no existing row is rewritten and nothing is dropped.
--
--   This file does NOT update those rows to fix them. Rewriting an
--   operator's moderation decisions is not a migration's business, and
--   Requirement 24.7's additive-only rule is the same rule read from the
--   other side.
--
--   The same constraint has one FORWARD consequence that had to be resolved
--   rather than documented away: an Admin_Reviewer suspending a FEATURED
--   Listing would move moderation_status off 'approved' and hit the check,
--   making Requirement 4.10's SUSPENDED transition impossible for exactly
--   the Listings the platform promoted. trg_submission_projects_moderation_
--   status therefore CLEARS is_featured whenever the projected status is not
--   'approved' (section 7c). It never sets it - featuring is an editorial
--   decision, not a consequence of publication.
--
-- WHY THE APPEND-ONLY GUARDS EXEMPT A CASCADE
--   Requirement 3.10 wants a modify or delete of persisted evidence
--   refused. Requirement 24.1 wants an explicit delete rule per
--   relationship, and design.md § Data Models states that
--   marketplace_submissions cascades from library_strategies and from
--   auth.users. Those two pull in opposite directions: an unconditional
--   BEFORE DELETE ... RAISE on marketplace_backtest_evidence would make
--   deleting a Listing, or a user account, impossible - the cascade would
--   reach the evidence row and abort.
--
--   marketplace_append_only_guard() therefore refuses every UPDATE
--   unconditionally, and refuses a DELETE only WHILE THE PARENT SUBMISSION
--   ROW STILL EXISTS. PostgreSQL performs a referential CASCADE as an AFTER
--   trigger on the parent, so by the time the child's BEFORE DELETE trigger
--   runs during a cascade the parent row is already gone; a direct
--   "DELETE FROM marketplace_backtest_evidence WHERE ..." always runs with
--   the parent present. The test is therefore exact, not heuristic: a
--   request to delete evidence is refused, and the declared cascade of a
--   deleted Listing or a closed account still completes.
--
-- SEVEN DELIBERATE ADDITIONS BEYOND THE DESIGN'S DDL, AND WHY
--   The design snippets are a schema sketch. These make the file safe to
--   apply by hand, twice, to a database whose history nobody recorded. None
--   weakens a control and none changes a column, constraint, index or policy
--   the design specifies.
--
--   1. Preflight assertions (section 0). A readable RAISE naming the missing
--      dependency instead of a bare 42P01 from inside a REFERENCES clause,
--      plus the library_strategies policy/trigger/index inventory that
--      section 9 re-checks so this file can prove it changed no existing
--      control on that pre-existing table.
--
--   2. Shape assertions after each CREATE TABLE IF NOT EXISTS (sections
--      1a, 2a, 3a, 4a, 5a), copied from 004d section 1a and 005b section
--      1.2. CREATE TABLE IF NOT EXISTS and ADD COLUMN IF NOT EXISTS are
--      SILENT when an object of that name already exists with a different
--      shape, so "the table is there" would not mean "the table is usable"
--      and the failure would surface much later as a cast error, a
--      truncated hash or an amount stored as text that sorts lexically.
--
--   3. Guarded ADD CONSTRAINT blocks. A bare ADD CONSTRAINT is NOT
--      idempotent: a second run raises 42710 duplicate_object and aborts the
--      whole file. PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS, and
--      removing-then-re-adding would both violate this file's additive-only
--      rule and open a window inside the transaction where the invariant is
--      unenforced. Same pattern as 006 section 1.3, 005b section 1.3, 004e
--      section 3 and 004d section 1b.
--
--   4. pg_trigger-guarded CREATE TRIGGER blocks. PostgreSQL has no CREATE
--      TRIGGER IF NOT EXISTS and this repository's PostgreSQL baseline
--      predates CREATE OR REPLACE TRIGGER (PG14). Same pattern as 004c
--      section 2 and 003_signal_trace_restoration.sql section 7.
--
--   5. pg_policies-guarded CREATE POLICY blocks (section 8), the pattern
--      004d section 3 and 004b section 4 already use, rather than the
--      DROP POLICY IF EXISTS ... CREATE POLICY of
--      migrations/006_reconcile_production_database.sql: a DROP would open a
--      window in which the table is policy-less, and on the pre-existing
--      library_strategies it would be a change to an existing control.
--
--   6. An own updated_at function, public.marketplace_touch_updated_at(),
--      rather than CREATE OR REPLACE on the shared
--      public.update_updated_at_column() that 001 defines. Replacing a
--      function fourteen existing triggers already call would be a change to
--      an existing control; a new function is additive by construction.
--
--   7. Table and column comments (section 10) and a postflight (section 11)
--      that proves the five tables, every named constraint, every named
--      index, every trigger and every policy exist, that the seed holds
--      exactly eleven rows, and that this file added no policy, no trigger
--      and no index to library_strategies beyond its own.
--
-- WHY THIS FILE REFUSES TO RUN WITHOUT library_strategies AND auth.users
--   marketplace_submissions.listing_id REFERENCES library_strategies(id)
--   and owner_id REFERENCES auth.users(id); the projection trigger writes
--   library_strategies. Creating these tables without those two present
--   would either fail with a bare undefined_table from inside a REFERENCES
--   clause or - worse, if the FK were dropped to get past it - produce a
--   Submission table whose rows point at nothing, which is precisely the
--   42703/PGRST204 class of failure Requirement 24.9 exists to prevent a
--   repeat of. Section 0 stops with a message naming the file to apply
--   first.
--
-- APPLICATION
--   NOT applied automatically. There is no migration table in this
--   repository and .github/workflows/03-deploy.yml has no migration step:
--   migrations here are applied BY HAND, per file, and nothing records which
--   files an environment has run. Apply this file explicitly against the
--   target database, then run the verification queries at the bottom, the
--   way scripts/forensics/apply_migration_007.py applied the root
--   migrations/007.
--
--   Until it is applied, these tables and columns do not exist. Every code
--   path that reads or writes them must degrade the way
--   backend_app/backend/backtest_service.py already does: a WARNING NAMING
--   THIS FILE ("007_marketplace_submissions.sql"), not a 500, and never a
--   write lost to a table a hand-applied migration has not created yet. A
--   missing table surfaces from PostgREST as PGRST205 and as 42P01
--   undefined_table on a select; a missing column as PGRST204 and 42703.
--
--   DEPENDS ON backend_app/migrations/006_backtest_evidence_columns.sql.
--   marketplace_backtest_evidence copies executed_bar_count, version_id,
--   dataset_checksum, dag_hash, engine_version and final_capital out of
--   strategy_backtests; before 006 reconciled that table those columns did
--   not all exist in either of its two definitions, so the evidence copy of
--   Requirement 3.9 had nothing to read. Section 0 asserts 006 has landed.
--
-- SAFETY
--   * Additive only (Requirement 24.7). No DROP TABLE, no DROP COLUMN, no
--     DROP CONSTRAINT, no ALTER ... RENAME, no DELETE FROM, no TRUNCATE, no
--     UPDATE of an existing row. No existing column's type, nullability or
--     default is altered. No existing policy, trigger, index or grant is
--     created, altered or removed - the only pre-existing table touched is
--     library_strategies, and only by ADD COLUMN IF NOT EXISTS, two guarded
--     ADD CONSTRAINTs and one new trigger of its own.
--   * Fully idempotent (Requirements 24.7, 24.8). CREATE TABLE IF NOT
--     EXISTS; ADD COLUMN IF NOT EXISTS; every ADD CONSTRAINT behind a
--     pg_constraint guard; CREATE INDEX IF NOT EXISTS; CREATE OR REPLACE
--     FUNCTION; every CREATE TRIGGER behind a pg_trigger guard; every CREATE
--     POLICY behind a pg_policies guard; the seed as INSERT ... ON CONFLICT
--     DO NOTHING; COMMENT replaces; GRANT and REVOKE are idempotent by
--     definition. A second application adds nothing and raises nothing.
--   * One transaction. Either all five tables, the eleven seed rows, every
--     constraint, index, function, trigger, policy and grant exist, or none
--     of them do. A marketplace_submissions without
--     trg_submission_transition_guard - a Submission table whose state
--     machine is unenforced - is never visible to a session.
--   * The seed is the only INSERT. It writes eleven rows of platform-global
--     reference data and no user data, and ON CONFLICT DO NOTHING makes a
--     re-run write nothing at all.

-- ==========================================================================
-- task 11.2: the Submission side of the Marketplace
-- ==========================================================================

BEGIN;

-- 0) Preflight ---------------------------------------------------------
-- Read-only assertions plus the library_strategies inventory. Fails with a
-- readable message instead of a bare undefined_table from inside a
-- REFERENCES clause, and records the policy/index/trigger counts of the one
-- pre-existing table this file touches so section 11 can prove it added
-- nothing there beyond its own single trigger and no policy at all. No NAME
-- is hard-coded, so an environment that renamed a policy cannot
-- false-alarm; every count is taken inside this one transaction, so they
-- are comparable by construction.
-- Idempotent: reads catalogues, writes nothing but four settings local to
-- this transaction.
DO $$
DECLARE
    ls_policies  INTEGER;
    ls_indexes   INTEGER;
    ls_triggers  INTEGER;
    ls_rls       BOOLEAN;
    missing      TEXT;
BEGIN
    IF to_regclass('public.library_strategies') IS NULL THEN
        RAISE EXCEPTION
            '007 precondition failed: table public.library_strategies does '
            'not exist. It is the single authoritative Listing table '
            '(Requirement 1.1) and marketplace_submissions.listing_id '
            'references it. Apply the library_strategies definition first '
            '(archived_migrations/root_migrations/'
            '001_create_library_strategies.sql, then migrations/'
            '007_add_marketplace_pricing_columns.sql). This file does not '
            'create it, because doing so would add a second divergent '
            'definition of the Listing table - the exact failure mode '
            'backend_app/migrations/006_backtest_evidence_columns.sql exists '
            'to repair on strategy_backtests.';
    END IF;

    IF to_regclass('auth.users') IS NULL THEN
        RAISE EXCEPTION
            '007 precondition failed: table auth.users does not exist. '
            'owner_id and reviewed_by reference it, and Requirement 24.1 '
            'requires a foreign key from every row introduced by this '
            'specification to the user it belongs to. This database is not '
            'a Supabase database, or the auth schema has not been '
            'provisioned.';
    END IF;

    -- The library_strategies columns this file's constraints, projection
    -- trigger and price mirror read or write. All are present in the base
    -- definition, so an absence means the table is in a shape this file was
    -- not written against and section 5 would fail with a message that does
    -- not name the cause.
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('id'), ('author_id'), ('source_strategy_id'),
                   ('moderation_status'), ('is_active'), ('is_featured'),
                   ('price'), ('currency'), ('updated_at'))
             AS expected(column_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns c
                WHERE c.table_schema::TEXT = 'public'
                  AND c.table_name::TEXT   = 'library_strategies'
                  AND c.column_name::TEXT  = expected.column_name);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            '007 precondition failed: public.library_strategies is missing '
            'column(s) %. price and currency come from migrations/'
            '007_add_marketplace_pricing_columns.sql, the rest from the base '
            'definition. Apply those first; this file mirrors price from '
            'price_minor and projects moderation_status and is_active onto '
            'this table, so it cannot run against a table missing them.',
            missing;
    END IF;

    -- Migration 006 must have landed: marketplace_backtest_evidence copies
    -- executed_bar_count, version_id, dataset_checksum, dag_hash,
    -- engine_version and final_capital out of strategy_backtests, and
    -- before 006 no single definition of that table held all six.
    IF to_regclass('public.strategy_backtests') IS NULL THEN
        RAISE EXCEPTION
            '007 precondition failed: table public.strategy_backtests does '
            'not exist. Requirement 3.9''s immutable evidence copy is read '
            'from it. Apply backend_app/migrations/'
            '001_strategy_architecture.sql then '
            '006_backtest_evidence_columns.sql first.';
    END IF;

    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('executed_bar_count'), ('version_id'),
                   ('dataset_checksum'), ('dag_hash'), ('engine_version'),
                   ('final_capital'))
             AS expected(column_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns c
                WHERE c.table_schema::TEXT = 'public'
                  AND c.table_name::TEXT   = 'strategy_backtests'
                  AND c.column_name::TEXT  = expected.column_name);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            '007 precondition failed: public.strategy_backtests is missing '
            'column(s) %, so the immutable Backtest_Evidence copy of '
            'Requirement 3.9 would have nothing to read for them. Apply '
            'backend_app/migrations/006_backtest_evidence_columns.sql '
            'first - it is this file''s declared dependency.', missing;
    END IF;

    SELECT count(*) INTO ls_policies
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'library_strategies';

    SELECT count(*) INTO ls_indexes
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'library_strategies';

    SELECT count(*) INTO ls_triggers
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.library_strategies'::regclass
       AND NOT t.tgisinternal;

    SELECT c.relrowsecurity INTO ls_rls
      FROM pg_class c
     WHERE c.oid = 'public.library_strategies'::regclass;

    PERFORM set_config('aerora.ls_policies_before', ls_policies::TEXT, true);
    PERFORM set_config('aerora.ls_indexes_before',  ls_indexes::TEXT,  true);
    PERFORM set_config('aerora.ls_triggers_before', ls_triggers::TEXT, true);

    RAISE NOTICE '007 preflight: public.library_strategies has RLS %, % '
                 'policies, % indexes and % user triggers. This file adds '
                 'no policy and at most 1 trigger there, and section 11 '
                 'verifies both.',
                 CASE WHEN ls_rls THEN 'enabled' ELSE 'DISABLED' END,
                 ls_policies, ls_indexes, ls_triggers;
END $$;

-- ==========================================================================
-- SECTION 1 - marketplace_submission_allowed_transitions and its seed
-- ==========================================================================
-- Created FIRST because marketplace_submission_guard() in section 7 selects
-- from it: the guard must never be able to exist while the table it consults
-- does not, or an illegal transition would raise 42P01 instead of 23514 and
-- the error the API translates would be the wrong one.
--
-- The primary key is COMPOSITE - (from_state, to_state) - and there is no id
-- column, which departs from the "every new table carries id UUID PRIMARY
-- KEY" rule design.md § Data Models states for the OWNED tables. Two
-- reasons, both load-bearing: the pair IS the identity of a transition, so a
-- surrogate key would permit the same edge to be seeded twice under two
-- ids; and INSERT ... ON CONFLICT DO NOTHING needs a unique index over
-- exactly those two columns to be idempotent at all.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS.
CREATE TABLE IF NOT EXISTS public.marketplace_submission_allowed_transitions (
    from_state  TEXT NOT NULL,
    to_state    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_submission_allowed_transitions PRIMARY KEY (from_state, to_state)
);

-- 1a) Shape assertion --------------------------------------------------
-- CREATE TABLE IF NOT EXISTS is silent about a pre-existing table of the
-- same name and a different shape. A from_state or to_state of a non-text
-- type would make the guard's equality comparison a cast; a missing column
-- would make the seed below fail with a message that does not name the
-- cause.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    problems TEXT;
BEGIN
    SELECT string_agg(
               format('%s (expected %s, found %s)',
                      expected.column_name,
                      array_to_string(expected.accepted, ' or '),
                      coalesce(actual.data_type::TEXT, 'no such column')),
               '; ' ORDER BY expected.column_name)
      INTO problems
      FROM (VALUES
                ('from_state', ARRAY['text', 'character varying']),
                ('to_state',   ARRAY['text', 'character varying']),
                ('created_at', ARRAY['timestamp with time zone']),
                ('updated_at', ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'marketplace_submission_allowed_transitions'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.marketplace_submission_allowed_transitions has column(s) '
            'of the wrong shape: %. A pre-existing table of that name was '
            'left as it was, because CREATE TABLE IF NOT EXISTS does not '
            'alter one. Reconcile it by hand before re-running this '
            'migration.', problems;
    END IF;
END $$;

-- 1b) The eleven-pair seed ---------------------------------------------
-- Requirement 4.2's eleven permitted transitions, and no twelfth. This set
-- MUST equal SUBMISSION_TRANSITIONS in
-- backend_app/backend/marketplace/submission_state.py; task 14.6's
-- tests/test_submission_state_agreement.py parses the VALUES list below and
-- that Python constant and fails if either holds a pair the other does not.
-- Keep the two in step or the test says so.
--
-- Written as one VALUES list rather than eleven statements so the parser has
-- one thing to read, and grouped by from_state in the requirement's own
-- order.
--
-- UNPUBLISHED appears only as a to_state, never as a from_state: it is the
-- one terminal state (Requirement 4.2's "any transition out of
-- UNPUBLISHED"). No row has from_state = to_state, so a same-value write is
-- refused by exactly the rule that refuses any other illegal edge.
--
-- Idempotent: ON CONFLICT DO NOTHING against pk_submission_allowed_
-- transitions. A re-run inserts nothing. NOTHING IS EVER DELETED FROM HERE
-- by this file - Requirement 24.7 forbids a DELETE, so removing an edge in
-- future is a new migration's job, not this one's re-run.
INSERT INTO public.marketplace_submission_allowed_transitions (from_state, to_state)
VALUES
    ('DRAFT',        'SUBMITTED'),
    ('SUBMITTED',    'UNDER_REVIEW'),
    ('SUBMITTED',    'REJECTED'),
    ('UNDER_REVIEW', 'APPROVED'),
    ('UNDER_REVIEW', 'REJECTED'),
    ('APPROVED',     'PUBLISHED'),
    ('PUBLISHED',    'SUSPENDED'),
    ('PUBLISHED',    'UNPUBLISHED'),
    ('SUSPENDED',    'PUBLISHED'),
    ('SUSPENDED',    'UNPUBLISHED'),
    ('REJECTED',     'DRAFT')
ON CONFLICT (from_state, to_state) DO NOTHING;

-- ==========================================================================
-- SECTION 2 - marketplace_submissions
-- ==========================================================================
-- design.md § Data Models -> "marketplace_submissions", column for column.
--
--   listing_id           the Listing this Submission is about. ON DELETE
--                        CASCADE: deleting a Listing removes its review
--                        workflow, which has no meaning without it
--                        (Requirement 24.1).
--   source_strategy_id   the strategy the partial unique index keys on. NOT
--                        a foreign key to public.strategies: that table's
--                        id type differs between environments (see 006's
--                        header on the same divergence) and an ALTER ... ADD
--                        FOREIGN KEY validated against existing rows could
--                        abort this transaction. The value is resolved
--                        server-side from the Listing, never client-supplied
--                        (Requirement 7.5).
--   owner_id             the RLS owner column. ON DELETE CASCADE from
--                        auth.users (Requirement 24.1).
--   version_id           the admitted Strategy_Version (Requirement 2.12).
--                        NULLABLE and without a foreign key, for the reason
--                        006's header states at length: strategy_versions
--                        may be absent or differently typed, and NULL here
--                        means NOT RECORDED, never "matches".
--   submission_state     TEXT NOT NULL DEFAULT 'DRAFT' (Requirement 4.3).
--                        NOT NULL, so unlike 005b's nullable
--                        order_lifecycle_state there is no NULL-passes hole
--                        in chk_submission_state.
--   eligibility_outcomes the per-criterion Eligibility_Gate outcome the
--                        Admin_Reviewer detail response reads (Requirement
--                        5.3). JSONB NOT NULL DEFAULT '[]' - an empty list,
--                        not NULL, so "no outcomes recorded" and "the gate
--                        recorded an empty result" are the same readable
--                        thing rather than a NULL a reader must guess about.
--   evaluator_version    which server version decided admission
--                        (Requirement 2.11).
--   rejection_reason     bounded by chk_submission_rejection_reason
--                        (Requirements 4.8, 5.5).
--   reviewed_by          ON DELETE SET NULL, not CASCADE: a reviewer's
--                        account being closed must not delete the
--                        Submissions they reviewed (Requirement 24.1's
--                        "explicit delete rule per relationship" - this is
--                        the explicit choice).
--   submitted_at         the ordering key of Requirement 5.2's "ordered by
--   reviewed_at          submission time descending".
--   published_at         Requirement 4.6's publication timestamp in UTC.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. The two inline CONSTRAINT clauses
-- are the design's; section 2b re-asserts them under guards for the case
-- where this table already existed without them.
CREATE TABLE IF NOT EXISTS public.marketplace_submissions (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id            UUID NOT NULL REFERENCES public.library_strategies(id) ON DELETE CASCADE,
    source_strategy_id    UUID NOT NULL,
    owner_id              UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    version_id            UUID,

    submission_state      TEXT NOT NULL DEFAULT 'DRAFT',
    eligibility_outcomes  JSONB NOT NULL DEFAULT '[]'::JSONB,
    evaluator_version     TEXT NOT NULL,

    rejection_reason      TEXT,
    reviewed_by           UUID REFERENCES auth.users(id) ON DELETE SET NULL,

    submitted_at          TIMESTAMPTZ,
    reviewed_at           TIMESTAMPTZ,
    published_at          TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_submission_state CHECK (submission_state IN (
        'DRAFT', 'SUBMITTED', 'UNDER_REVIEW', 'APPROVED',
        'PUBLISHED', 'REJECTED', 'SUSPENDED', 'UNPUBLISHED')),
    CONSTRAINT chk_submission_rejection_reason CHECK (
        rejection_reason IS NULL
        OR (btrim(rejection_reason) <> ''
            AND length(btrim(rejection_reason)) <= 2000))
);

-- 2a) Shape assertion --------------------------------------------------
-- Each silence CREATE TABLE IF NOT EXISTS would otherwise keep matters:
--   * a submission_state of a non-text type makes the seed-table lookup in
--     the guard a cast rather than a comparison;
--   * a JSON (not JSONB) eligibility_outcomes cannot be indexed or compared
--     and preserves duplicate keys, so Requirement 5.3's per-criterion read
--     becomes order-dependent;
--   * a timestamp WITHOUT time zone submitted_at, reviewed_at or
--     published_at silently reinterprets every UTC instant as local time,
--     which Requirements 4.3, 4.6 and 24.5 read as evidence. That type is
--     deliberately NOT accepted: it is not equivalent, it is a silent hour
--     shift;
--   * a non-uuid id or listing_id makes the partial unique index and the
--     projection trigger's WHERE id = NEW.listing_id an equality over
--     truncated text.
-- ::TEXT on every information_schema identifier: those columns are the
-- sql_identifier / character_data domains, not text, and an explicit cast
-- keeps the comparison a plain text compare on every server version.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    problems TEXT;
BEGIN
    SELECT string_agg(
               format('%s (expected %s, found %s)',
                      expected.column_name,
                      array_to_string(expected.accepted, ' or '),
                      coalesce(actual.data_type::TEXT, 'no such column')),
               '; ' ORDER BY expected.column_name)
      INTO problems
      FROM (VALUES
                ('id',                   ARRAY['uuid']),
                ('listing_id',           ARRAY['uuid']),
                ('source_strategy_id',   ARRAY['uuid']),
                ('owner_id',             ARRAY['uuid']),
                ('version_id',           ARRAY['uuid']),
                ('submission_state',     ARRAY['text', 'character varying']),
                ('eligibility_outcomes', ARRAY['jsonb']),
                ('evaluator_version',    ARRAY['text', 'character varying']),
                ('rejection_reason',     ARRAY['text', 'character varying']),
                ('reviewed_by',          ARRAY['uuid']),
                ('submitted_at',         ARRAY['timestamp with time zone']),
                ('reviewed_at',          ARRAY['timestamp with time zone']),
                ('published_at',         ARRAY['timestamp with time zone']),
                ('created_at',           ARRAY['timestamp with time zone']),
                ('updated_at',           ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'marketplace_submissions'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.marketplace_submissions has column(s) of the wrong '
            'shape: %. A pre-existing table of that name was left as it was, '
            'because CREATE TABLE IF NOT EXISTS does not alter one, so "the '
            'table exists" would not have meant "the table is usable" '
            '(Requirements 4.5, 24.9). Reconcile it by hand before '
            're-running this migration.', problems;
    END IF;
END $$;

-- 2b) Constraint guards ------------------------------------------------
-- No-ops on a fresh run: both constraints were declared inline above. They
-- exist so that a marketplace_submissions created earlier WITHOUT them gains
-- them rather than silently keeping the invariants unenforced.
--
-- Idempotent: each ADD CONSTRAINT runs only when pg_constraint holds no
-- constraint of that name on this table, so a re-run adds nothing and cannot
-- raise 42710 duplicate_object.
DO $$
BEGIN
    -- Requirement 4.5's "check constraint enumerating the permitted values",
    -- and Requirement 24.2's. The 8 values are SUBMISSION_STATE_VALUES in
    -- submission_state.py, in the requirement's own order. The column is NOT
    -- NULL, so there is no NULL row that passes by evaluating to NULL.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.marketplace_submissions'::regclass
           AND conname  = 'chk_submission_state'
    ) THEN
        ALTER TABLE public.marketplace_submissions
            ADD CONSTRAINT chk_submission_state CHECK (submission_state IN (
                'DRAFT', 'SUBMITTED', 'UNDER_REVIEW', 'APPROVED',
                'PUBLISHED', 'REJECTED', 'SUSPENDED', 'UNPUBLISHED'));
        RAISE NOTICE 'Added chk_submission_state to '
                     'public.marketplace_submissions (the 8 values of '
                     'Requirement 4.1).';
    END IF;

    -- Requirements 4.8 and 5.5. btrim(...) <> '' rejects a whitespace-only
    -- reason, and the length bound is taken AFTER trimming, so "  x  " is a
    -- 1-character reason and 2000 spaces around a 2000-character reason
    -- still passes. NULL is admitted, because a Submission that is not
    -- REJECTED has no reason - the "REJECTED implies a reason" half is
    -- transition-scoped and lives in marketplace_submission_guard(), where
    -- it can see that the row is ENTERING that state.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.marketplace_submissions'::regclass
           AND conname  = 'chk_submission_rejection_reason'
    ) THEN
        ALTER TABLE public.marketplace_submissions
            ADD CONSTRAINT chk_submission_rejection_reason CHECK (
                rejection_reason IS NULL
                OR (btrim(rejection_reason) <> ''
                    AND length(btrim(rejection_reason)) <= 2000));
        RAISE NOTICE 'Added chk_submission_rejection_reason to '
                     'public.marketplace_submissions (NULL, or 1..2000 '
                     'characters after trimming).';
    END IF;
END $$;

-- 2c) Indexes ----------------------------------------------------------
-- design.md § Data Models -> "marketplace_submissions", "Indexes:".
--
-- uq_submission_open_per_strategy is Requirement 4.5's partial unique index
-- and, per Requirement 2.8, THE FINAL ARBITER for concurrent submissions:
-- two simultaneous POST /api/library/submissions for one strategy end with
-- one row and one 23505, which submission_service translates into
-- MARKETPLACE_SUBMISSION_ALREADY_OPEN (409), creating no row and changing no
-- state. It is PARTIAL for a reason: the uniqueness must hold over the OPEN
-- states only. The four are OPEN_STATES in submission_state.py -
-- PUBLISHED is in the set because Requirement 2.7 also forbids a second
-- Submission while a Listing for the same source_strategy_id is live. A
-- plain UNIQUE (source_strategy_id) would instead make a resubmission after
-- a rejection impossible (Requirement 4.2's REJECTED -> DRAFT edge would
-- lead nowhere); no index at all would make the double-click race
-- representable.
CREATE UNIQUE INDEX IF NOT EXISTS uq_submission_open_per_strategy
    ON public.marketplace_submissions (source_strategy_id)
    WHERE submission_state IN ('SUBMITTED', 'UNDER_REVIEW', 'APPROVED', 'PUBLISHED');

-- Requirement 24.4's "Submission lookup by state", with the column order and
-- the two DESC keys of Requirement 5.2's "ordered by submission time
-- descending with the Submission identifier as tie-break" - so the admin
-- list is one index scan and the tie-break is not a sort. id DESC is part of
-- the index because a page boundary that falls between two Submissions
-- sharing a submitted_at would otherwise be unstable, and a caller paging
-- through it would see one row twice and another not at all.
CREATE INDEX IF NOT EXISTS idx_submissions_state
    ON public.marketplace_submissions (submission_state, submitted_at DESC, id DESC);

-- The owner's own list, and the column every RLS owner policy on this table
-- filters on: without it each policy-scoped read is a sequential scan.
CREATE INDEX IF NOT EXISTS idx_submissions_owner
    ON public.marketplace_submissions (owner_id);

-- The Listing_Projection's join (design.md: LEFT JOIN marketplace_submissions
-- sub ON sub.listing_id = ls.id) and the ON DELETE CASCADE from
-- library_strategies, which without an index on the referencing column is a
-- sequential scan per deleted Listing.
CREATE INDEX IF NOT EXISTS idx_submissions_listing
    ON public.marketplace_submissions (listing_id);

-- ==========================================================================
-- SECTION 3 - marketplace_submission_transitions
-- ==========================================================================
-- design.md § Data Models -> "marketplace_submission_transitions".
-- Requirement 4.13's record of every Submission_State transition: the prior
-- value, the new value, the acting identity, the reason where one applies,
-- and the timestamp in UTC, "retained unmodified thereafter".
--
-- owner_id is DENORMALISED onto this table rather than resolved through
-- submission_id. It is the RLS owner column, and an owner policy that had to
-- reach marketplace_submissions to find the owner would either need a
-- subquery in the policy predicate - evaluated per row, on every read - or a
-- SECURITY DEFINER helper, which is a privilege-escalation surface. Copying
-- the owner is the cheaper and smaller-blast-radius answer, and it cannot
-- drift because this table is append-only.
--
-- from_state and to_state carry NO CHECK over the 8 values, deliberately.
-- This is a HISTORY table: if a future migration retires a state, the rows
-- recording transitions into it must remain readable, and a CHECK
-- enumerating today's vocabulary would make that migration a choice between
-- dropping a constraint (forbidden here) and losing history. The vocabulary
-- is enforced where it is written - chk_submission_state on the live row -
-- and Requirement 24.2 asks for the check on Submission_State, which is that
-- column, not on its audit trail.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS.
CREATE TABLE IF NOT EXISTS public.marketplace_submission_transitions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id    UUID NOT NULL REFERENCES public.marketplace_submissions(id) ON DELETE CASCADE,
    owner_id         UUID NOT NULL,
    from_state       TEXT NOT NULL,
    to_state         TEXT NOT NULL,
    actor_id         UUID,
    reason           TEXT,
    transitioned_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3a) Shape assertion --------------------------------------------------
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    problems TEXT;
BEGIN
    SELECT string_agg(
               format('%s (expected %s, found %s)',
                      expected.column_name,
                      array_to_string(expected.accepted, ' or '),
                      coalesce(actual.data_type::TEXT, 'no such column')),
               '; ' ORDER BY expected.column_name)
      INTO problems
      FROM (VALUES
                ('id',              ARRAY['uuid']),
                ('submission_id',   ARRAY['uuid']),
                ('owner_id',        ARRAY['uuid']),
                ('from_state',      ARRAY['text', 'character varying']),
                ('to_state',        ARRAY['text', 'character varying']),
                ('actor_id',        ARRAY['uuid']),
                ('reason',          ARRAY['text', 'character varying']),
                ('transitioned_at', ARRAY['timestamp with time zone']),
                ('created_at',      ARRAY['timestamp with time zone']),
                ('updated_at',      ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'marketplace_submission_transitions'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.marketplace_submission_transitions has column(s) of the '
            'wrong shape: %. Requirement 4.13''s history must be readable in '
            'timestamp order with a real timestamptz; reconcile the table by '
            'hand before re-running this migration.', problems;
    END IF;
END $$;

-- 3b) Index ------------------------------------------------------------
-- Requirement 4.13's and 5.3's "ordered by transition timestamp ASCENDING".
-- ASC is written explicitly rather than left to the default so that the
-- index and the requirement read the same way, and so that a later reader
-- does not "tidy" it into DESC to match idx_submissions_state. The leading
-- column also serves the ON DELETE CASCADE from marketplace_submissions.
CREATE INDEX IF NOT EXISTS idx_submission_transitions
    ON public.marketplace_submission_transitions (submission_id, transitioned_at ASC);

-- The owner's own history read, and the column the RLS owner policy filters.
CREATE INDEX IF NOT EXISTS idx_submission_transitions_owner
    ON public.marketplace_submission_transitions (owner_id);

-- ==========================================================================
-- SECTION 4 - marketplace_backtest_evidence
-- ==========================================================================
-- design.md § Data Models -> "marketplace_backtest_evidence".
-- Requirement 3.9's immutable copy: written in the SAME transaction as the
-- Submission, one row per Backtest_Condition, carrying source_backtest_id
-- and a copy of the exact parameters and the exact metrics read from
-- strategy_backtests. It is a COPY and not a join for the reason Requirement
-- 3.11 states: every backtest figure the Marketplace displays is derived
-- from the persisted evidence, so a later re-run, edit or deletion of the
-- source strategy_backtests row cannot retroactively change what a
-- subscriber was shown.
--
-- EVERY parameter and EVERY metric column is NOT NULL. That is not
-- decoration: Requirement 3.4 requires the parameters non-null and
-- Requirement 2.6 requires all seven metrics non-null and finite for
-- admission, so a row that could hold NULL here would be a row that
-- represents evidence the Evidence_Validator already refused. The validator
-- decides admission; this table refuses to store the inadmissible shape at
-- all. NUMERIC throughout and never double precision - a metric compared for
-- pairwise distinctness or summed into a performance figure must not carry
-- binary floating-point error (Requirement 8.13).
--
-- source_backtest_id carries NO foreign key to strategy_backtests. The two
-- pre-006 definitions of that table disagree about the type of its id (UUID
-- under 001, TEXT under the production reconciliation - see 006's header),
-- so an ALTER ... ADD FOREIGN KEY would be validated against existing rows
-- and could abort this transaction in exactly the environments this
-- specification has to apply to. More importantly, a CASCADE from
-- strategy_backtests would let deleting a backtest delete the evidence
-- Requirement 3.10 declares immutable, and a RESTRICT would make deleting
-- any backtest ever referenced by a Submission impossible. The copy is
-- deliberately free-standing: it survives its source, which is the whole
-- point of taking it.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. The inline CONSTRAINT clauses are
-- the design's; section 4b re-asserts them under guards.
CREATE TABLE IF NOT EXISTS public.marketplace_backtest_evidence (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id       UUID NOT NULL REFERENCES public.marketplace_submissions(id) ON DELETE CASCADE,
    owner_id            UUID NOT NULL,
    source_backtest_id  UUID NOT NULL,
    condition_index     INTEGER NOT NULL,

    -- The immutable parameter copy (Requirements 3.4, 3.9).
    dataset             TEXT NOT NULL,
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    initial_capital     NUMERIC(20, 8) NOT NULL,
    commission          NUMERIC(10, 6) NOT NULL,
    slippage            NUMERIC(10, 6) NOT NULL,
    dataset_checksum    TEXT NOT NULL,
    dag_hash            TEXT NOT NULL,
    engine_version      TEXT NOT NULL,
    executed_bar_count  INTEGER NOT NULL,
    version_id          UUID NOT NULL,

    -- The immutable metric copy (Requirements 2.6, 3.9).
    total_return_pct    NUMERIC(20, 8) NOT NULL,
    sharpe_ratio        NUMERIC(20, 8) NOT NULL,
    sortino_ratio       NUMERIC(20, 8) NOT NULL,
    max_drawdown_pct    NUMERIC(20, 8) NOT NULL,
    win_rate_pct        NUMERIC(20, 8) NOT NULL,
    profit_factor       NUMERIC(20, 8) NOT NULL,
    total_trades        INTEGER NOT NULL,
    final_capital       NUMERIC(20, 8) NOT NULL,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_evidence_submission_backtest UNIQUE (submission_id, source_backtest_id),
    CONSTRAINT uq_evidence_submission_checksum UNIQUE (submission_id, dataset_checksum),
    CONSTRAINT chk_evidence_window CHECK (end_date - start_date + 1 >= 90),
    CONSTRAINT chk_evidence_trades CHECK (total_trades >= 20),
    CONSTRAINT chk_evidence_bars   CHECK (executed_bar_count >= 50)
);

-- 4a) Shape assertion --------------------------------------------------
-- The silences that matter most on this table:
--   * a text executed_bar_count or total_trades would make chk_evidence_bars
--     and chk_evidence_trades casts, would sort lexically ('9' > '137') and
--     would let '' be stored as "a count";
--   * a double precision metric reintroduces the binary floating-point error
--     Requirement 8.13 forbids into the Pricing_Evaluator's inputs, which are
--     read from exactly these columns;
--   * a timestamp WITHOUT time zone or a text start_date / end_date makes
--     chk_evidence_window's date arithmetic either a cast or an error, and
--     Requirement 3.7's 90-calendar-day span uncheckable;
--   * a numeric dataset_checksum cannot hold a hex digest, and Requirement
--     3.6's pairwise-different checksums would be compared as numbers.
-- 'character varying' is accepted wherever 'text' is expected - a VARCHAR(n)
-- is what a hand-added column most likely is - and 'bigint'/'smallint'
-- alongside 'integer' for the two counts. 'double precision' is deliberately
-- NOT accepted for any NUMERIC column.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    problems TEXT;
BEGIN
    SELECT string_agg(
               format('%s (expected %s, found %s)',
                      expected.column_name,
                      array_to_string(expected.accepted, ' or '),
                      coalesce(actual.data_type::TEXT, 'no such column')),
               '; ' ORDER BY expected.column_name)
      INTO problems
      FROM (VALUES
                ('id',                 ARRAY['uuid']),
                ('submission_id',      ARRAY['uuid']),
                ('owner_id',           ARRAY['uuid']),
                ('source_backtest_id', ARRAY['uuid']),
                ('condition_index',    ARRAY['integer', 'bigint', 'smallint']),
                ('dataset',            ARRAY['text', 'character varying']),
                ('start_date',         ARRAY['date']),
                ('end_date',           ARRAY['date']),
                ('initial_capital',    ARRAY['numeric']),
                ('commission',         ARRAY['numeric']),
                ('slippage',           ARRAY['numeric']),
                ('dataset_checksum',   ARRAY['text', 'character varying']),
                ('dag_hash',           ARRAY['text', 'character varying']),
                ('engine_version',     ARRAY['text', 'character varying']),
                ('executed_bar_count', ARRAY['integer', 'bigint', 'smallint']),
                ('version_id',         ARRAY['uuid']),
                ('total_return_pct',   ARRAY['numeric']),
                ('sharpe_ratio',       ARRAY['numeric']),
                ('sortino_ratio',      ARRAY['numeric']),
                ('max_drawdown_pct',   ARRAY['numeric']),
                ('win_rate_pct',       ARRAY['numeric']),
                ('profit_factor',      ARRAY['numeric']),
                ('total_trades',       ARRAY['integer', 'bigint', 'smallint']),
                ('final_capital',      ARRAY['numeric']),
                ('created_at',         ARRAY['timestamp with time zone']),
                ('updated_at',         ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'marketplace_backtest_evidence'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.marketplace_backtest_evidence has column(s) of the wrong '
            'shape: %. This table is the ONLY source of every backtest figure '
            'the Marketplace displays (Requirement 3.11), so a wrong type '
            'here is a wrong figure shown to a paying subscriber. Reconcile '
            'it by hand before re-running this migration.', problems;
    END IF;
END $$;

-- 4b) Constraint guards ------------------------------------------------
-- No-ops on a fresh run: all five were declared inline above.
--
-- Idempotent: each ADD CONSTRAINT runs only when pg_constraint holds no
-- constraint of that name on this table.
DO $$
BEGIN
    -- Requirement 3.1's "each referencing a DISTINCT strategy_backtests
    -- row". Requirement 24.3 lists the uniqueness constraints this
    -- specification must apply; this is the one that makes "one dataset
    -- submitted three times under one backtest id" unrepresentable.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.marketplace_backtest_evidence'::regclass
           AND conname  = 'uq_evidence_submission_backtest'
    ) THEN
        ALTER TABLE public.marketplace_backtest_evidence
            ADD CONSTRAINT uq_evidence_submission_backtest
                UNIQUE (submission_id, source_backtest_id);
        RAISE NOTICE 'Added uq_evidence_submission_backtest (Requirement 3.1).';
    END IF;

    -- Requirement 3.6's "all dataset_checksum values in the set pairwise
    -- different", enforced BY THE DATABASE and not only by the validator.
    -- This is the evidence that three conditions are three tests rather than
    -- one dataset submitted three times under three backtest ids - which
    -- uq_evidence_submission_backtest alone would happily admit.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.marketplace_backtest_evidence'::regclass
           AND conname  = 'uq_evidence_submission_checksum'
    ) THEN
        ALTER TABLE public.marketplace_backtest_evidence
            ADD CONSTRAINT uq_evidence_submission_checksum
                UNIQUE (submission_id, dataset_checksum);
        RAISE NOTICE 'Added uq_evidence_submission_checksum (Requirement 3.6).';
    END IF;

    -- Requirement 3.7's "at least 90 calendar days between start_date and
    -- end_date INCLUSIVE". The + 1 is the inclusivity: a window of
    -- 2024-01-01..2024-03-30 is 90 days, and (end - start) alone would count
    -- it as 89 and reject it. date - date yields an integer number of days in
    -- PostgreSQL, so no interval arithmetic and no time zone enters here.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.marketplace_backtest_evidence'::regclass
           AND conname  = 'chk_evidence_window'
    ) THEN
        ALTER TABLE public.marketplace_backtest_evidence
            ADD CONSTRAINT chk_evidence_window
                CHECK (end_date - start_date + 1 >= 90);
        RAISE NOTICE 'Added chk_evidence_window (Requirement 3.7, inclusive '
                     'calendar days).';
    END IF;

    -- Requirement 3.7's "total_trades of at least 20", the
    -- statistical-significance threshold the Backtest_Engine already warns
    -- on. Unlike 006's chk_sb_executed_bar_count, the bound here IS the
    -- admission bound: this table holds only ADMITTED evidence, so a row
    -- below it is not a short exploratory run honestly recorded, it is a
    -- Submission that should never have been created.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.marketplace_backtest_evidence'::regclass
           AND conname  = 'chk_evidence_trades'
    ) THEN
        ALTER TABLE public.marketplace_backtest_evidence
            ADD CONSTRAINT chk_evidence_trades CHECK (total_trades >= 20);
        RAISE NOTICE 'Added chk_evidence_trades (Requirement 3.7, >= 20).';
    END IF;

    -- Requirement 3.8's "non-null executed bar count of at least the
    -- Backtest_Engine's existing 50-bar minimum". The column is NOT NULL, so
    -- the "null, absent or below 50 is a rejection" half of that criterion is
    -- structural here: an evidence row with no bar count cannot exist.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.marketplace_backtest_evidence'::regclass
           AND conname  = 'chk_evidence_bars'
    ) THEN
        ALTER TABLE public.marketplace_backtest_evidence
            ADD CONSTRAINT chk_evidence_bars CHECK (executed_bar_count >= 50);
        RAISE NOTICE 'Added chk_evidence_bars (Requirement 3.8, >= 50).';
    END IF;
END $$;

-- 4c) Indexes ----------------------------------------------------------
-- The Admin_Reviewer detail read (Requirement 5.3: "every
-- marketplace_backtest_evidence row's parameters and metrics"), the
-- Pricing_Evaluator's input read (design.md: SELECT ... FROM
-- marketplace_backtest_evidence WHERE submission_id = :id) and the ON DELETE
-- CASCADE from marketplace_submissions, all in condition order.
-- uq_evidence_submission_backtest's implicit index already leads with
-- submission_id, but not in condition_index order, so the detail response
-- would still sort.
CREATE INDEX IF NOT EXISTS idx_evidence_submission
    ON public.marketplace_backtest_evidence (submission_id, condition_index);

-- The column the RLS owner policy filters on.
CREATE INDEX IF NOT EXISTS idx_evidence_owner
    ON public.marketplace_backtest_evidence (owner_id);

-- ==========================================================================
-- SECTION 5 - marketplace_price_evaluations
-- ==========================================================================
-- design.md § Data Models -> "marketplace_price_evaluations", and the
-- set_price pseudocode of design.md § pricing.
--
-- The persisted Price_Range. inputs_digest is what makes Requirement 8.3's
-- determinism checkable and Requirement 8.8's server-side enforcement
-- cheap: set_price looks up the most recent row for
-- (submission_id, currency, inputs_digest) and only recomputes when the
-- evidence has changed, so the range a price is checked against is provably
-- the range that was computed from the evidence then on the table.
--
-- NOTE, because Requirement 8.7 is a requirement about ABSENCE: there is NO
-- accuracy, confidence, precision or error column here, and none may be
-- added. A Price_Range is guidance derived from evidence, not a prediction,
-- and this specification reports no accuracy figure for it because it has no
-- labelled outcome to measure one against. The table is shaped so that a
-- future caller cannot store one without a migration that has to justify
-- itself.
--
-- All three bounds are BIGINT Minor_Units, never NUMERIC and never a float
-- (Requirements 8.1, 8.12, 8.13). Both currencies money.py supports have
-- minor-unit exponent 2, so a bound is a whole number of cents or paise and
-- an integer column is the exact representation, not an approximation of one.
--
-- This table is NOT append-only. Requirement 8.3 requires repeated
-- evaluation under one evaluator version to be identical, which makes a
-- second row for the same digest harmless, and set_price reads
-- ORDER BY created_at DESC LIMIT 1. There is deliberately no unique
-- constraint over (submission_id, currency, inputs_digest): a new evaluator
-- version must be able to record its own range for the same inputs without
-- overwriting the range a price was previously accepted against.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. The inline CONSTRAINT clause is
-- the design's; section 5b re-asserts it under a guard.
CREATE TABLE IF NOT EXISTS public.marketplace_price_evaluations (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id            UUID NOT NULL REFERENCES public.marketplace_submissions(id) ON DELETE CASCADE,
    owner_id                 UUID NOT NULL,
    currency                 TEXT NOT NULL,
    inputs_digest            TEXT NOT NULL,
    minimum_price_minor      BIGINT NOT NULL,
    recommended_price_minor  BIGINT NOT NULL,
    maximum_price_minor      BIGINT NOT NULL,
    evaluator_version        TEXT NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_price_range_ordered CHECK (
        minimum_price_minor >= 1
        AND minimum_price_minor <= recommended_price_minor
        AND recommended_price_minor <= maximum_price_minor
        AND maximum_price_minor <= 100000000)
);

-- 5a) Shape assertion --------------------------------------------------
-- A numeric or double precision bound would let a fractional Minor_Unit be
-- stored - half a cent - which Requirement 8.1's "in Minor_Units" and
-- Property P-51's "all three are Python int values" both forbid, and which
-- would make the <= comparisons in set_price float comparisons. 'integer' is
-- accepted alongside 'bigint' because it holds the 100000000 ceiling; a
-- smallint does not and is refused.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    problems TEXT;
BEGIN
    SELECT string_agg(
               format('%s (expected %s, found %s)',
                      expected.column_name,
                      array_to_string(expected.accepted, ' or '),
                      coalesce(actual.data_type::TEXT, 'no such column')),
               '; ' ORDER BY expected.column_name)
      INTO problems
      FROM (VALUES
                ('id',                      ARRAY['uuid']),
                ('submission_id',           ARRAY['uuid']),
                ('owner_id',                ARRAY['uuid']),
                ('currency',                ARRAY['text', 'character varying']),
                ('inputs_digest',           ARRAY['text', 'character varying']),
                ('minimum_price_minor',     ARRAY['bigint', 'integer']),
                ('recommended_price_minor', ARRAY['bigint', 'integer']),
                ('maximum_price_minor',     ARRAY['bigint', 'integer']),
                ('evaluator_version',       ARRAY['text', 'character varying']),
                ('created_at',              ARRAY['timestamp with time zone']),
                ('updated_at',              ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'marketplace_price_evaluations'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.marketplace_price_evaluations has column(s) of the wrong '
            'shape: %. The three bounds must be integer Minor_Units '
            '(Requirements 8.1, 8.12, 8.13); a fractional or floating type '
            'here makes the server-side price check a float comparison. '
            'Reconcile it by hand before re-running this migration.', problems;
    END IF;
END $$;

-- 5b) Constraint guard -------------------------------------------------
-- No-op on a fresh run: declared inline above.
DO $$
BEGIN
    -- Requirement 8.4, and Property P-51's
    -- 1 <= minimum <= recommended <= maximum <= 100000000 in full. Written as
    -- one constraint rather than four so that a violation names the ordering
    -- rule that was broken rather than one endpoint of it. The lower bound is
    -- 1 and not 0: a Price_Range whose minimum is zero is a free Listing
    -- dressed as a priced one, and Requirement 8.4 states the domain starts
    -- at one Minor_Unit.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.marketplace_price_evaluations'::regclass
           AND conname  = 'chk_price_range_ordered'
    ) THEN
        ALTER TABLE public.marketplace_price_evaluations
            ADD CONSTRAINT chk_price_range_ordered CHECK (
                minimum_price_minor >= 1
                AND minimum_price_minor <= recommended_price_minor
                AND recommended_price_minor <= maximum_price_minor
                AND maximum_price_minor <= 100000000);
        RAISE NOTICE 'Added chk_price_range_ordered (Requirement 8.4).';
    END IF;
END $$;

-- 5c) Indexes ----------------------------------------------------------
-- design.md § Data Models: idx_price_eval_lookup ON (submission_id, currency,
-- inputs_digest, created_at DESC). This is set_price's lookup verbatim -
-- "WHERE submission_id = :id AND currency = :currency AND inputs_digest =
-- :digest ORDER BY created_at DESC LIMIT 1" - so the enforcement read is one
-- index scan of one row and never a sort over a Submission's evaluation
-- history. created_at DESC is part of the index, not a sort step, because
-- Requirement 8.15's rate limit means the hot path is this read and not the
-- computation behind it.
CREATE INDEX IF NOT EXISTS idx_price_eval_lookup
    ON public.marketplace_price_evaluations (submission_id, currency, inputs_digest, created_at DESC);

-- The column the RLS owner policy filters on.
CREATE INDEX IF NOT EXISTS idx_price_eval_owner
    ON public.marketplace_price_evaluations (owner_id);

-- ==========================================================================
-- SECTION 6 - library_strategies: five additive columns
-- ==========================================================================
-- design.md § Data Models -> "Additive columns on existing tables", the
-- library_strategies row.
--
--   price_minor            The Listing price in Minor_Units (Requirement
--                          8.12). BIGINT, so money.py::amount_for_listing
--                          returns the stored integer UNCHANGED and no float
--                          appears between the price an owner set and the
--                          amount a purchaser is charged. NULLABLE: a Listing
--                          that has not been priced yet has no price, and a
--                          default of 0 would be a free Listing invented by a
--                          migration.
--   source_cloning_enabled Requirement 7.4's per-Listing source-cloning
--                          choice: stored, DEFAULTED TO DISABLED, and
--                          existing rows BACK-FILLED to disabled. NOT NULL
--                          DEFAULT FALSE does all three in one statement -
--                          on PostgreSQL 11+ that back-fills without
--                          rewriting the table. It is NOT NULL on purpose:
--                          a NULL here would be an absent consent decision,
--                          and Requirement 7.3 turns "not enabled" into a
--                          403, so the safe reading must be structural rather
--                          than left to each call site's coalesce.
--   supported_timeframes   TEXT[]. On the Listing_Projection's allow-list
--                          (Requirement 6.2's "supported timeframes"). An
--                          array and not a comma-joined TEXT because a
--                          reader that has to split a string will eventually
--                          split it differently from the writer.
--   market_type            On the allow-list (Requirement 6.2's "asset and
--                          market information"). No CHECK: the vocabulary of
--                          market types is the exchange layer's, not this
--                          migration's, and enumerating it here would make
--                          adding a venue a schema change.
--   condition_count        The number of Backtest_Conditions behind the
--                          Listing (Requirement 6.2's "number of
--                          Backtest_Conditions"). Denormalised from
--                          marketplace_backtest_evidence so that a catalogue
--                          page is a constant number of round trips
--                          independent of the Listing count (Requirement
--                          27.1, Property P-57) rather than one count(*) per
--                          Listing.
--
-- price NUMERIC(10,2) is RETAINED, untouched, and mirrored FROM price_minor
-- by the trigger in section 7. Requirement 25 and 24.7: browse_library,
-- trending, featured, get_library_detail and creator_analytics all read
-- price today, and Requirement 1.4 requires creator_analytics to keep
-- computing from it.
--
-- Every column is added with ADD COLUMN IF NOT EXISTS, so a re-run skips each
-- one already present and nothing existing is altered.
ALTER TABLE public.library_strategies
    ADD COLUMN IF NOT EXISTS price_minor             BIGINT,
    ADD COLUMN IF NOT EXISTS source_cloning_enabled  BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS supported_timeframes    TEXT[],
    ADD COLUMN IF NOT EXISTS market_type             TEXT,
    ADD COLUMN IF NOT EXISTS condition_count         INTEGER;

-- 6a) Column shape assertion -------------------------------------------
-- ADD COLUMN IF NOT EXISTS is SILENT when a column of that name already
-- exists with a different type - one added by hand during an investigation,
-- say. Each silence here is a defect:
--   * a NUMERIC or double precision price_minor lets a fractional Minor_Unit
--     be stored, so amount_for_listing would either raise or launder a half
--     cent into a charge (Requirements 8.12, 8.13);
--   * a NULLABLE source_cloning_enabled makes Requirement 7.4's "default that
--     choice to disabled" depend on every reader's coalesce;
--   * a TEXT supported_timeframes turns Requirement 6.2's list into a string
--     the reader must split;
--   * a TEXT condition_count sorts lexically and cannot be compared to a
--     count.
-- The NOT NULL and the DEFAULT of source_cloning_enabled are asserted too,
-- not just its type, because those two ARE the requirement in that case.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    problems     TEXT;
    cloning_null TEXT;
    cloning_dflt TEXT;
BEGIN
    SELECT string_agg(
               format('%s (expected %s, found %s)',
                      expected.column_name,
                      array_to_string(expected.accepted, ' or '),
                      coalesce(actual.data_type::TEXT, 'no such column')),
               '; ' ORDER BY expected.column_name)
      INTO problems
      FROM (VALUES
                ('price_minor',            ARRAY['bigint', 'integer']),
                ('source_cloning_enabled', ARRAY['boolean']),
                ('supported_timeframes',   ARRAY['ARRAY']),
                ('market_type',            ARRAY['text', 'character varying']),
                ('condition_count',        ARRAY['integer', 'bigint', 'smallint']),
                -- the two retained columns the mirror trigger writes and reads
                ('price',                  ARRAY['numeric']),
                ('currency',               ARRAY['text', 'character varying'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'library_strategies'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.library_strategies has marketplace column(s) of the wrong '
            'shape: %. A pre-existing column of that name was left as it was, '
            'because ADD COLUMN IF NOT EXISTS does not alter one, so "the '
            'column exists" would not have meant "the column is usable" '
            '(Requirements 7.4, 8.12, 24.9). Reconcile it by hand before '
            're-running this migration.', problems;
    END IF;

    SELECT c.is_nullable::TEXT, coalesce(c.column_default::TEXT, '(none)')
      INTO cloning_null, cloning_dflt
      FROM information_schema.columns c
     WHERE c.table_schema::TEXT = 'public'
       AND c.table_name::TEXT   = 'library_strategies'
       AND c.column_name::TEXT  = 'source_cloning_enabled';

    IF cloning_null <> 'NO' OR cloning_dflt NOT ILIKE '%false%' THEN
        RAISE EXCEPTION
            'public.library_strategies.source_cloning_enabled is nullable=% '
            'with default %, but Requirement 7.4 requires the source-cloning '
            'choice to be STORED, DEFAULTED TO DISABLED and existing rows '
            'BACK-FILLED to disabled. A pre-existing nullable or '
            'true-defaulted column of that name was left as it was; '
            'reconcile it by hand - a Listing whose owner never made this '
            'choice must not be clonable.', cloning_null, cloning_dflt;
    END IF;
END $$;

-- 6b) chk_ls_price_minor -----------------------------------------------
-- design.md § Data Models. Requirement 8.4's domain, applied to the STORED
-- Listing price and not only to the evaluated range, so a price that never
-- went through set_price - a direct SQL UPDATE, a script, a future handler -
-- still cannot be outside it.
--
-- NULL is admitted: an unpriced Listing has no price, and the alternative
-- would be to invent one for every existing row. Validated against existing
-- rows on the first run, which cannot fail: price_minor was added nullable
-- with no default in section 6, so every pre-existing row holds NULL in it
-- the moment this commits.
--
-- Guarded rather than bare: PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS
-- and a second bare run would raise 42710 and abort the file.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.library_strategies'::regclass
           AND conname  = 'chk_ls_price_minor'
    ) THEN
        ALTER TABLE public.library_strategies
            ADD CONSTRAINT chk_ls_price_minor CHECK (
                price_minor IS NULL
                OR (price_minor >= 1 AND price_minor <= 100000000));
        RAISE NOTICE 'Added chk_ls_price_minor to public.library_strategies '
                     '(NULL, or 1..100000000 Minor_Units; Requirement 8.4).';
    ELSE
        RAISE NOTICE 'chk_ls_price_minor already present; left unchanged.';
    END IF;
END $$;

-- 6c) chk_ls_featured_requires_published -------------------------------
-- design.md § Data Models:
--   chk_ls_featured_requires_published
--     CHECK (is_featured = FALSE OR moderation_status = 'approved')
--
-- A featured Listing that is not catalogue-visible is a promoted row nobody
-- can reach, and - worse - a route by which a SUSPENDED or UNPUBLISHED
-- Listing keeps appearing in the featured rail while Requirement 4.7 says it
-- must appear nowhere. The constraint makes that state unrepresentable.
--
-- ADDED "NOT VALID" FIRST, THEN VALIDATED BEST-EFFORT. See "WHY
-- chk_ls_featured_requires_published IS ADDED NOT VALID FIRST" in the header
-- for the full reasoning; in short, backend_app/routers/library.py's
-- admin_moderate_strategy currently writes moderation_status = 'featured'
-- alongside is_featured = TRUE, so production rows this constraint refuses
-- may already exist, and a validated ADD CONSTRAINT would abort this whole
-- transaction - which Requirement 24.8 forbids.
--
-- NOT VALID is NOT a weaker constraint for new data: PostgreSQL enforces a
-- NOT VALID CHECK on every subsequent INSERT and UPDATE. It only means "the
-- rows that were already here were not examined". So the invariant holds
-- from this commit onward either way, and on a clean database the VALIDATE
-- succeeds and the constraint ends up ordinary.
--
-- The VALIDATE runs inside a nested BEGIN ... EXCEPTION block, which is a
-- savepoint: if it fails, only the VALIDATE is rolled back, the constraint
-- remains in place NOT VALID, and this migration still commits.
--
-- Idempotent twice over: the ADD is behind a pg_constraint name guard, and
-- the VALIDATE is behind a convalidated guard, so a re-run on a database
-- where the constraint is already valid does nothing and a re-run where it
-- is still NOT VALID re-attempts the validation (which is what an operator
-- who has just cleaned up the legacy rows wants).
DO $$
DECLARE
    offenders BIGINT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.library_strategies'::regclass
           AND conname  = 'chk_ls_featured_requires_published'
    ) THEN
        ALTER TABLE public.library_strategies
            ADD CONSTRAINT chk_ls_featured_requires_published CHECK (
                is_featured = FALSE OR moderation_status = 'approved')
            NOT VALID;
        RAISE NOTICE 'Added chk_ls_featured_requires_published to '
                     'public.library_strategies NOT VALID; every subsequent '
                     'INSERT and UPDATE is checked from now on.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid    = 'public.library_strategies'::regclass
           AND conname     = 'chk_ls_featured_requires_published'
           AND convalidated
    ) THEN
        BEGIN
            ALTER TABLE public.library_strategies
                VALIDATE CONSTRAINT chk_ls_featured_requires_published;
            RAISE NOTICE 'chk_ls_featured_requires_published validated: no '
                         'existing library_strategies row is featured '
                         'without moderation_status = ''approved''.';
        EXCEPTION
            WHEN check_violation THEN
                SELECT count(*) INTO offenders
                  FROM public.library_strategies
                 WHERE is_featured AND moderation_status <> 'approved';

                RAISE NOTICE
                    'chk_ls_featured_requires_published could NOT be '
                    'validated: % existing library_strategies row(s) are '
                    'featured with a moderation_status other than '
                    '''approved'' - almost certainly the legacy '
                    '''featured'' value admin_moderate_strategy writes '
                    '(backend_app/routers/library.py line 1750). The '
                    'constraint stays in place and IS enforced for every new '
                    'INSERT and UPDATE; those existing rows were left '
                    'untouched, because rewriting an operator''s moderation '
                    'decisions is not a migration''s business and '
                    'Requirement 24.7 forbids this file from updating an '
                    'existing row. Reconcile them by hand (set '
                    'moderation_status = ''approved'' where the Listing '
                    'should stay featured, or is_featured = FALSE where it '
                    'should not) and re-run this file to validate.',
                    offenders;
        END;
    ELSE
        RAISE NOTICE 'chk_ls_featured_requires_published already validated; '
                     'left unchanged.';
    END IF;
END $$;

-- ==========================================================================
-- SECTION 7 - functions and triggers
-- ==========================================================================
-- CREATE OR REPLACE FUNCTION is naturally idempotent. Every CREATE TRIGGER is
-- behind a pg_trigger existence guard, because PostgreSQL has no CREATE
-- TRIGGER IF NOT EXISTS and this repository's PostgreSQL baseline predates
-- CREATE OR REPLACE TRIGGER (PG14) - the same pattern 004c section 2 and
-- 003_signal_trace_restoration.sql section 7 use.

-- 7a) updated_at ------------------------------------------------------
-- Requirement 24.5: creation and update timestamps on every table introduced
-- by this specification. created_at is DEFAULT NOW() on each table; this is
-- the update half.
--
-- A NEW function rather than CREATE OR REPLACE on the shared
-- public.update_updated_at_column() that 001 defines and fourteen existing
-- triggers already call. Replacing a function those triggers depend on would
-- be a change to an existing control, which no task in this plan may make;
-- a new function is additive by construction.
CREATE OR REPLACE FUNCTION public.marketplace_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    target TEXT;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'marketplace_submissions',
        'marketplace_submission_transitions',
        'marketplace_backtest_evidence',
        'marketplace_price_evaluations',
        'marketplace_submission_allowed_transitions'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger t
              JOIN pg_class c     ON c.oid = t.tgrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE t.tgname   = 'trg_' || target || '_updated_at'
               AND n.nspname  = 'public'
               AND c.relname  = target
               AND NOT t.tgisinternal
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE UPDATE ON public.%I '
                'FOR EACH ROW EXECUTE FUNCTION '
                'public.marketplace_touch_updated_at()',
                'trg_' || target || '_updated_at', target);
        END IF;
    END LOOP;
END $$;

-- 7b) marketplace_submission_guard() + trg_submission_transition_guard --
-- design.md -> "Where the state lives and how the database is the arbiter",
-- point 3, verbatim in behaviour. THIS IS WHAT MAKES REQUIREMENT 4.4 TRUE
-- RATHER THAN ASPIRATIONAL: "IF a write would set a Submission_State to a
-- value not reachable from its current value ... THEN THE Persistence_Layer
-- SHALL reject the write". Not the service layer. The database. A psql
-- session with the service key cannot move a Submission along an edge the
-- seed table does not hold.
--
-- Three branches, in this order and for these reasons:
--
--   1. A same-value write RETURNS NEW immediately. It is not a transition -
--      no state changed - so it is not the transition table's business.
--      Requirement 4.2's "including any transition from a value to that same
--      value" is satisfied by the seed holding no self-edge: a genuine
--      attempt to "transition" DRAFT -> DRAFT changes nothing and records
--      nothing, and the handler that meant to move the row will find it
--      still in DRAFT. Note this branch also lets an UPDATE that touches
--      only rejection_reason, reviewed_by or published_at through, which is
--      what the admin actions need.
--
--   2. An edge absent from marketplace_submission_allowed_transitions raises
--      with ERRCODE 23514 (check_violation) and a message naming BOTH states,
--      which is Requirement 4.4's "return an error naming the current
--      Submission_State and the rejected transition". 23514 rather than a
--      bare P0001 so that submission_service can distinguish this from a
--      connection error by SQLSTATE and translate it to
--      MARKETPLACE_TRANSITION_NOT_PERMITTED (409) without parsing English.
--
--   3. Entering REJECTED without a usable reason raises the same 23514.
--      chk_submission_rejection_reason already bounds the COLUMN, but it
--      cannot express "REJECTED requires one" - a CHECK sees only the row,
--      and a row-level "state = REJECTED implies reason IS NOT NULL" would
--      also refuse a row that was ALREADY rejected and is being touched for
--      an unrelated reason. Only the trigger can see that the row is
--      ENTERING that state. Requirements 4.8, 4.9, 5.5, 5.10.
--
-- No transition-history row is written here. Requirement 4.4 requires the
-- rejected write to "record no transition history entry", and history is
-- inserted by the application AFTER the UPDATE returns, in the same
-- transaction - so a raise here means the INSERT never runs and the rollback
-- of this statement leaves nothing behind. A trigger that wrote history
-- itself would also write it for a transition the application then failed to
-- audit, which Requirement 5.6 and 5.11 forbid in the other direction.
--
-- NEW.updated_at := now() is in the design's snippet and is kept, even
-- though trg_marketplace_submissions_updated_at from section 7a also sets
-- it. BEFORE triggers on one table fire in NAME order, so
-- trg_marketplace_submissions_updated_at runs first and this guard runs
-- second; both assign NOW(), which is the same value throughout a
-- transaction, so the two writes cannot disagree. Keeping the line means the
-- design's snippet and this function do not differ, and it also means the
-- guard alone is sufficient if the generic trigger is ever absent.
--
-- CREATE OR REPLACE FUNCTION is naturally idempotent.
CREATE OR REPLACE FUNCTION public.marketplace_submission_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.submission_state = OLD.submission_state THEN
        RETURN NEW;                      -- a no-op write is not a transition
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.marketplace_submission_allowed_transitions t
         WHERE t.from_state = OLD.submission_state
           AND t.to_state   = NEW.submission_state
    ) THEN
        RAISE EXCEPTION
            'disallowed Submission_State transition % -> %',
            OLD.submission_state, NEW.submission_state
            USING ERRCODE = '23514';
    END IF;

    IF NEW.submission_state = 'REJECTED'
       AND (NEW.rejection_reason IS NULL
            OR btrim(NEW.rejection_reason) = ''
            OR length(btrim(NEW.rejection_reason)) > 2000) THEN
        RAISE EXCEPTION
            'REJECTED requires a rejection reason of 1..2000 characters'
            USING ERRCODE = '23514';
    END IF;

    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
          JOIN pg_class c     ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname  = 'trg_submission_transition_guard'
           AND n.nspname = 'public'
           AND c.relname = 'marketplace_submissions'
           AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_submission_transition_guard
            BEFORE UPDATE ON public.marketplace_submissions
            FOR EACH ROW EXECUTE FUNCTION public.marketplace_submission_guard();
    END IF;
END $$;

-- 7c) trg_submission_projects_moderation_status -------------------------
-- design.md -> "trg_submission_projects_moderation_status is an AFTER INSERT
-- OR UPDATE trigger applying MODERATION_STATUS_FOR_STATE and
-- IS_ACTIVE_FOR_STATE to the library_strategies row".
--
-- Requirement 4.12's ONE shared definition, and Requirement 4.6's "every
-- public catalogue response served after that transaction commits includes
-- the Listing" - true without a second write from the handler, and true even
-- for a state change made by a direct SQL UPDATE.
--
-- THE MAPPING BELOW MUST EQUAL MODERATION_STATUS_FOR_STATE AND
-- IS_ACTIVE_FOR_STATE in backend_app/backend/marketplace/submission_state.py.
-- It is written as a plpgsql CASE statement with one branch per state and NO
-- ELSE fall-through - the ELSE raises - so a ninth state cannot be projected
-- onto the catalogue by accident, and so a parser has one line per state to
-- read.
--
--     DRAFT        -> 'pending'    is_active FALSE
--     SUBMITTED    -> 'pending'    is_active FALSE
--     UNDER_REVIEW -> 'pending'    is_active FALSE
--     APPROVED     -> 'pending'    is_active FALSE   <- deliberately pending
--     PUBLISHED    -> 'approved'   is_active TRUE    <- the only visible one
--     REJECTED     -> 'rejected'   is_active FALSE
--     SUSPENDED    -> 'rejected'   is_active FALSE
--     UNPUBLISHED  -> 'rejected'   is_active FALSE
--
-- APPROVED staying 'pending' is the whole of Requirement 4.7: an approved but
-- unpublished Listing must be invisible, and 'approved' is the value the
-- existing browse_library / trending / featured / get_library_detail
-- predicates treat as visible. SUSPENDED and UNPUBLISHED map to 'rejected'
-- because that is the only retained value meaning "not in the catalogue"; it
-- says NOTHING about Subscriptions, which Requirements 4.10 and 4.11 keep
-- running to their period expiry - those live on library_subscriptions and
-- this trigger does not touch that table.
--
-- 'featured' is never produced as a moderation_status. Featuring stays
-- library_strategies.is_featured, set by admin_moderate_strategy and read by
-- get_featured_strategies, and is orthogonal to the Submission lifecycle - so
-- the existing .in_("moderation_status", ["approved","featured"]) predicates
-- keep working unchanged in meaning.
--
-- ONE DELIBERATE ADDITION BEYOND THE TWO MAPPINGS: when the projected status
-- is NOT 'approved', is_featured is cleared to FALSE. This is not decoration,
-- it is what makes two requirements co-satisfiable.
-- chk_ls_featured_requires_published from section 6c refuses a row that is
-- featured while moderation_status <> 'approved'. Without this line, an
-- Admin_Reviewer suspending a FEATURED Listing would get a check violation
-- and the transition would be impossible - so Requirement 4.10's "WHEN a
-- Submission enters SUSPENDED ..." could not happen at all for a featured
-- Listing, and Requirement 4.7's "exclude the Listing from every public
-- response" would be unreachable for it. Clearing the flag is also the
-- honest reading of the constraint's intent: a Listing that has left the
-- catalogue is not a featured Listing. It is never set the other way - a
-- Submission entering PUBLISHED does NOT become featured - because featuring
-- is an editorial decision and inventing one here would promote a Listing
-- nobody chose to promote.
--
-- AFTER rather than BEFORE, and FOR EACH ROW: the projection must happen only
-- once the Submission row is actually stored, so a transition the guard
-- rejects projects nothing (Requirement 4.4's "leave the Listing's visibility
-- to the Listing_Projection unchanged").
--
-- The UPDATE is a no-op when nothing would change - IS DISTINCT FROM on both
-- columns - so a Submission UPDATE that touches only rejection_reason does
-- not bump library_strategies.updated_at and does not wake the existing
-- trg_library_strategies_updated_at for nothing.
--
-- CREATE OR REPLACE FUNCTION is naturally idempotent.
CREATE OR REPLACE FUNCTION public.marketplace_project_listing_state()
RETURNS TRIGGER AS $$
DECLARE
    target_status TEXT;
    target_active BOOLEAN;
BEGIN
    -- Nested rather than one "TG_OP = 'UPDATE' AND NEW... = OLD..."
    -- condition: on an INSERT, OLD is an UNASSIGNED record, and PostgreSQL
    -- does not guarantee that a boolean AND short-circuits, so touching
    -- OLD.submission_state in the same expression risks "record old is not
    -- assigned yet" on every Submission insert.
    IF TG_OP = 'UPDATE' THEN
        IF NEW.submission_state = OLD.submission_state THEN
            RETURN NULL;                 -- nothing moved, nothing to project
        END IF;
    END IF;

    CASE NEW.submission_state
        WHEN 'DRAFT'        THEN target_status := 'pending';
        WHEN 'SUBMITTED'    THEN target_status := 'pending';
        WHEN 'UNDER_REVIEW' THEN target_status := 'pending';
        WHEN 'APPROVED'     THEN target_status := 'pending';
        WHEN 'PUBLISHED'    THEN target_status := 'approved';
        WHEN 'REJECTED'     THEN target_status := 'rejected';
        WHEN 'SUSPENDED'    THEN target_status := 'rejected';
        WHEN 'UNPUBLISHED'  THEN target_status := 'rejected';
        ELSE
            RAISE EXCEPTION
                'no moderation_status mapping for Submission_State % - '
                'MODERATION_STATUS_FOR_STATE in '
                'backend_app/backend/marketplace/submission_state.py and '
                'this trigger must define all 8 values (Requirement 4.12)',
                NEW.submission_state
                USING ERRCODE = '23514';
    END CASE;

    target_active := (NEW.submission_state = 'PUBLISHED');

    UPDATE public.library_strategies ls
       SET moderation_status = target_status,
           is_active         = target_active,
           -- see the note above: a Listing that is not catalogue-visible is
           -- not a featured Listing, and leaving the flag set would make
           -- chk_ls_featured_requires_published refuse the transition.
           is_featured       = CASE WHEN target_status = 'approved'
                                    THEN ls.is_featured
                                    ELSE FALSE END
     WHERE ls.id = NEW.listing_id
       AND (ls.moderation_status IS DISTINCT FROM target_status
            OR ls.is_active      IS DISTINCT FROM target_active
            OR (target_status <> 'approved' AND ls.is_featured));

    RETURN NULL;                         -- AFTER trigger: return is ignored
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
          JOIN pg_class c     ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname  = 'trg_submission_projects_moderation_status'
           AND n.nspname = 'public'
           AND c.relname = 'marketplace_submissions'
           AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_submission_projects_moderation_status
            AFTER INSERT OR UPDATE ON public.marketplace_submissions
            FOR EACH ROW EXECUTE FUNCTION public.marketplace_project_listing_state();
    END IF;
END $$;

-- 7d) The append-only guards -------------------------------------------
-- Requirement 4.13's "SHALL retain each recorded entry unmodified
-- thereafter" for the transition history, and Requirement 3.10's "SHALL
-- reject the request ... and SHALL leave every persisted Backtest_Evidence
-- value unchanged" for the evidence copy.
--
-- ONE function, two triggers. TG_TABLE_NAME and TG_OP put the table and the
-- operation in the message, so a single definition covers both tables and
-- there is no second copy to keep in step. 23514 (check_violation) for the
-- same reason the transition guard uses it: submission_service translates by
-- SQLSTATE into MARKETPLACE_EVIDENCE_IMMUTABLE (409) without parsing English.
--
-- THE DELETE BRANCH DELIBERATELY EXEMPTS A REFERENTIAL CASCADE. See "WHY THE
-- APPEND-ONLY GUARDS EXEMPT A CASCADE" in the header. An unconditional raise
-- would make deleting a Listing (Requirement 24.1's declared ON DELETE
-- CASCADE from library_strategies) or closing a user account (the cascade
-- from auth.users) impossible, because the cascade reaches these child rows
-- and would abort. PostgreSQL runs a referential CASCADE as an AFTER trigger
-- on the parent, so during a cascade the parent marketplace_submissions row
-- is ALREADY GONE when this BEFORE DELETE trigger fires, while a direct
-- "DELETE FROM marketplace_backtest_evidence WHERE ..." always runs with the
-- parent present. The parent-existence test is therefore exact rather than
-- heuristic: a REQUEST to delete evidence is refused; the declared cascade of
-- a deleted Listing or a closed account still completes.
--
-- The REVOKE UPDATE, DELETE grants of section 9 are the second layer. They
-- are not sufficient alone - a table owner and, in some Supabase projects,
-- service_role bypass table privileges - which is why the trigger is the
-- primary control and the REVOKE the defence in depth.
--
-- CREATE OR REPLACE FUNCTION is naturally idempotent.
CREATE OR REPLACE FUNCTION public.marketplace_append_only_guard()
RETURNS TRIGGER AS $$
DECLARE
    parent_exists BOOLEAN;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            '% is append-only: UPDATE is not permitted (row %)',
            TG_TABLE_NAME, OLD.id
            USING ERRCODE = '23514';
    END IF;

    -- TG_OP = 'DELETE'
    SELECT EXISTS (
        SELECT 1 FROM public.marketplace_submissions s
         WHERE s.id = OLD.submission_id
    ) INTO parent_exists;

    IF parent_exists THEN
        RAISE EXCEPTION
            '% is append-only: DELETE is not permitted while submission % '
            'exists (row %)',
            TG_TABLE_NAME, OLD.submission_id, OLD.id
            USING ERRCODE = '23514';
    END IF;

    -- The parent Submission is already gone, so this DELETE is the declared
    -- ON DELETE CASCADE of Requirement 24.1 and not a request to mutate
    -- evidence or history. Let it through.
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
          JOIN pg_class c     ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname  = 'trg_submission_transitions_append_only'
           AND n.nspname = 'public'
           AND c.relname = 'marketplace_submission_transitions'
           AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_submission_transitions_append_only
            BEFORE UPDATE OR DELETE ON public.marketplace_submission_transitions
            FOR EACH ROW EXECUTE FUNCTION public.marketplace_append_only_guard();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
          JOIN pg_class c     ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname  = 'trg_evidence_append_only'
           AND n.nspname = 'public'
           AND c.relname = 'marketplace_backtest_evidence'
           AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_evidence_append_only
            BEFORE UPDATE OR DELETE ON public.marketplace_backtest_evidence
            FOR EACH ROW EXECUTE FUNCTION public.marketplace_append_only_guard();
    END IF;
END $$;

-- 7e) trg_ls_price_mirror ----------------------------------------------
-- design.md § Data Models: "price NUMERIC(10,2) retained and mirrored from
-- price_minor by trigger", and Requirement 25's "existing behaviour
-- retained": browse_library, trending, featured, get_library_detail and
-- creator_analytics all read price today, and Requirement 1.4 requires
-- creator_analytics to keep computing its figures from price and avg_rating.
--
-- ONE DIRECTION ONLY: price_minor is authoritative and price is derived.
-- Mirroring the other way as well would make two columns each other's source
-- of truth, and the first write that set only price would produce a Listing
-- whose displayed price and charged amount disagree - which is the defect
-- design.md § "root causes" records for the current handler. A caller that
-- writes only price therefore does NOT change what a purchaser is charged,
-- and that is the intended reading: money is price_minor.
--
-- The conversion is exact and integral, never a float: price_minor is a whole
-- number of Minor_Units and both currencies money.py supports (USD, INR)
-- have ISO 4217 minor-unit exponent 2, so price = price_minor / 100 with
-- scale 2 is the same number, not a rounding of it. NUMERIC division, then an
-- explicit ROUND to 2, so the value fits NUMERIC(10,2) exactly rather than
-- relying on an implicit assignment cast. The ceiling of chk_ls_price_minor
-- is 100000000 Minor_Units = 1000000.00 major units, which needs 7 integral
-- digits and fits NUMERIC(10,2)'s 8.
--
-- A currency OUTSIDE the supported set leaves price ALONE rather than
-- guessing an exponent. A currency with exponent 0 or 3 divided by 100 would
-- be a displayed price wrong by two orders of magnitude, and inventing that
-- silently is worse than leaving a stale value that a reader can notice.
-- valid_currency on this table already restricts the column to USD and INR,
-- so this branch is unreachable today; it exists so that adding a third
-- currency is a visible, deliberate edit here rather than a silent
-- mis-conversion.
--
-- A NULL price_minor leaves price alone too: an unpriced Listing has nothing
-- to mirror, and writing NULL over a price an existing row already carries
-- would be this trigger deleting data (Requirement 24.7).
--
-- CREATE OR REPLACE FUNCTION is naturally idempotent.
CREATE OR REPLACE FUNCTION public.library_strategies_mirror_price()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.price_minor IS NULL THEN
        RETURN NEW;
    END IF;

    IF NEW.currency IS NULL OR NEW.currency IN ('USD', 'INR') THEN
        -- Both supported currencies have minor-unit exponent 2. A NULL
        -- currency is treated as the column default 'USD', which is what the
        -- existing default and valid_currency together already mean.
        NEW.price := ROUND(NEW.price_minor::NUMERIC / 100, 2);
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
          JOIN pg_class c     ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname  = 'trg_ls_price_mirror'
           AND n.nspname = 'public'
           AND c.relname = 'library_strategies'
           AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_ls_price_mirror
            BEFORE INSERT OR UPDATE ON public.library_strategies
            FOR EACH ROW EXECUTE FUNCTION public.library_strategies_mirror_price();
    END IF;
END $$;

-- ==========================================================================
-- SECTION 8 - row level security
-- ==========================================================================
-- Requirements 21.2 and 21.3. Enable FIRST, then add the policies: enabling
-- RLS is DEFAULT-DENY, so between these statements the tables are unreachable
-- rather than open - and every statement is inside this one transaction, so
-- no session ever observes the intermediate state. That ordering is
-- Requirement 21.3's "SHALL default to denying access, such that a query
-- executed against any table introduced by this specification without a
-- resolved authenticated identity or the service role returns zero rows and
-- performs zero writes".
--
-- Idempotent: ENABLE ROW LEVEL SECURITY is a no-op when RLS is already on
-- (the same unguarded form 003_signal_trace_restoration.sql section 8 and
-- 004d section 3 use).
--
-- public.library_strategies is DELIBERATELY ABSENT from this list. It is a
-- pre-existing table; enabling or disabling RLS on it, or touching one of its
-- policies, would be a change to an existing control that no task in this
-- plan may make (Requirement 25.1). Section 11 proves its policy count did
-- not move.
ALTER TABLE public.marketplace_submissions                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.marketplace_submission_transitions         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.marketplace_backtest_evidence              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.marketplace_price_evaluations              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.marketplace_submission_allowed_transitions ENABLE ROW LEVEL SECURITY;

-- The owner policy and the service-role policy on every new table, following
-- library_subscriptions in migrations/006_reconcile_production_database.sql
-- (lines 87-99) in predicate shape - FOR ALL TO authenticated USING
-- (auth.uid() = <owner column>) WITH CHECK (the same) - and
-- deployment_permissions in the same file.
--
-- WITH CHECK repeats the USING predicate rather than being omitted. Omitting
-- it makes PostgreSQL reuse USING for the new row, which is equivalent here,
-- but stating it means a later reader cannot mistake the omission for "any
-- new row is allowed" and cannot "simplify" the policy into that.
--
-- Guarded on pg_policies by schemaname + tablename + policyname, the pattern
-- 004d section 3, 004b section 4 and 003_signal_trace_restoration.sql section
-- 9 use, rather than the DROP POLICY IF EXISTS ... CREATE POLICY of
-- migrations/006_reconcile_production_database.sql: PostgreSQL has no CREATE
-- POLICY IF NOT EXISTS, and a DROP would open a window inside this
-- transaction in which the table has no policy at all - which, with RLS on,
-- is closed rather than open, but is still a state this file should not
-- create.
DO $$
DECLARE
    spec RECORD;
BEGIN
    -- (table, owner column) for the four owner-scoped tables. Every one of
    -- them carries the owner's id on the row itself - the two child tables
    -- denormalise it rather than reaching marketplace_submissions through a
    -- subquery, because a policy predicate is evaluated PER ROW and a
    -- subquery there would be a join on every read; and a SECURITY DEFINER
    -- helper to avoid that would be a privilege-escalation surface.
    FOR spec IN
        SELECT * FROM (VALUES
            ('marketplace_submissions',            'owner_id', 'submissions'),
            ('marketplace_submission_transitions', 'owner_id', 'submission_transitions'),
            ('marketplace_backtest_evidence',      'owner_id', 'evidence'),
            ('marketplace_price_evaluations',      'owner_id', 'price_evaluations')
        ) AS t(table_name, owner_column, short_name)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
             WHERE schemaname = 'public'
               AND tablename  = spec.table_name
               AND policyname = spec.short_name || '_owner_access'
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I FOR ALL TO authenticated '
                'USING (auth.uid() = %I) WITH CHECK (auth.uid() = %I)',
                spec.short_name || '_owner_access', spec.table_name,
                spec.owner_column, spec.owner_column);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
             WHERE schemaname = 'public'
               AND tablename  = spec.table_name
               AND policyname = spec.short_name || '_service_role'
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I FOR ALL TO service_role '
                'USING (true) WITH CHECK (true)',
                spec.short_name || '_service_role', spec.table_name);
        END IF;
    END LOOP;
END $$;

-- marketplace_submission_allowed_transitions is the one new table with NO
-- OWNER, because it holds no user data: eleven rows of platform-global
-- reference data naming the edges of a state machine that is published in
-- requirements.md. There is nothing to scope to a tenant, so the owner-policy
-- shape of Requirement 21.2 has no owner column to name.
--
-- What it gets instead is READ TO EVERYONE, WRITE TO NOBODY BUT service_role:
--
--   * a permissive FOR SELECT USING (true) policy, applying to PUBLIC. It has
--     to be readable by every role that can UPDATE a Submission, because
--     marketplace_submission_guard() SELECTs from it inside the trigger and
--     that SELECT runs as the INVOKING role, under RLS. The function is
--     deliberately NOT SECURITY DEFINER - making the state-machine guard run
--     with the definer's privileges would be a privilege-escalation surface
--     for the sake of hiding a table of eleven public constants.
--   * NO insert, update or delete policy for authenticated or anon. With RLS
--     on, default-deny means a caller cannot add an edge to the state machine
--     and so cannot authorise a transition Requirement 4.2 forbids. That is
--     the whole security property of this table, and it is achieved by the
--     ABSENCE of a policy rather than by one.
--   * the service_role FOR ALL policy, so a future migration or a backend
--     job can seed a new edge.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'marketplace_submission_allowed_transitions'
           AND policyname = 'submission_transitions_seed_read'
    ) THEN
        CREATE POLICY submission_transitions_seed_read
            ON public.marketplace_submission_allowed_transitions
            FOR SELECT USING (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'marketplace_submission_allowed_transitions'
           AND policyname = 'submission_transitions_seed_service_role'
    ) THEN
        CREATE POLICY submission_transitions_seed_service_role
            ON public.marketplace_submission_allowed_transitions
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

-- ==========================================================================
-- SECTION 9 - grants
-- ==========================================================================
-- Table privileges are the layer BELOW row level security: RLS decides which
-- rows a role may touch, a GRANT decides whether it may touch the table at
-- all. Both are needed - a policy on a table the role has no privilege on
-- grants nothing, and a privilege without a policy reaches no rows.
--
-- Following 004d section 5: revoke everything from anon and authenticated
-- first so the grant list below is the WHOLE list rather than an addition to
-- whatever a previous run or a Supabase default left behind, then grant
-- exactly what each role needs.
--
-- Idempotent: REVOKE and GRANT are both idempotent by definition.
REVOKE ALL ON public.marketplace_submissions                    FROM anon;
REVOKE ALL ON public.marketplace_submissions                    FROM authenticated;
REVOKE ALL ON public.marketplace_submission_transitions         FROM anon;
REVOKE ALL ON public.marketplace_submission_transitions         FROM authenticated;
REVOKE ALL ON public.marketplace_backtest_evidence              FROM anon;
REVOKE ALL ON public.marketplace_backtest_evidence              FROM authenticated;
REVOKE ALL ON public.marketplace_price_evaluations              FROM anon;
REVOKE ALL ON public.marketplace_price_evaluations              FROM authenticated;
REVOKE ALL ON public.marketplace_submission_allowed_transitions FROM anon;
REVOKE ALL ON public.marketplace_submission_allowed_transitions FROM authenticated;

-- An owner reads their Submissions, creates one, and moves it along the one
-- edge that is theirs (DRAFT -> SUBMITTED). No DELETE: Requirement 4.2 has
-- no edge that removes a Submission - withdrawal is UNPUBLISHED, a state -
-- and Requirement 4.13's history would be orphaned by a delete.
GRANT SELECT, INSERT, UPDATE ON public.marketplace_submissions TO authenticated;

-- Append-only tables: SELECT and INSERT, never UPDATE or DELETE
-- (Requirements 3.10, 4.13). The trigger of section 7d is the primary
-- control; this is the layer below it.
GRANT SELECT, INSERT ON public.marketplace_submission_transitions TO authenticated;
GRANT SELECT, INSERT ON public.marketplace_backtest_evidence      TO authenticated;

-- A Price_Range evaluation is written on the owner's behalf and then read
-- back by set_price. Not append-only by trigger (see section 5's header), but
-- there is no reason for a client to rewrite one.
GRANT SELECT, INSERT ON public.marketplace_price_evaluations TO authenticated;

-- The state machine is readable and not writable. The guard's SELECT runs as
-- the invoking role, so authenticated MUST be able to read it or every
-- Submission UPDATE would fail with a permission error instead of a
-- transition verdict.
GRANT SELECT ON public.marketplace_submission_allowed_transitions TO authenticated;

-- service_role: everything the backend does, and no more. Notably no UPDATE
-- and no DELETE on the two append-only tables and none on the seed beyond
-- INSERT - the design's "REVOKE UPDATE, DELETE ... FROM authenticated,
-- service_role".
GRANT SELECT, INSERT, UPDATE ON public.marketplace_submissions                    TO service_role;
GRANT SELECT, INSERT         ON public.marketplace_submission_transitions         TO service_role;
GRANT SELECT, INSERT         ON public.marketplace_backtest_evidence              TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.marketplace_price_evaluations              TO service_role;
GRANT SELECT, INSERT         ON public.marketplace_submission_allowed_transitions TO service_role;

-- The explicit REVOKE the design names, stated after the GRANTs so that it is
-- the last word on these two tables even if a GRANT list above is ever
-- widened by accident. Requirements 3.10 and 4.13.
REVOKE UPDATE, DELETE ON public.marketplace_submission_transitions FROM anon, authenticated, service_role;
REVOKE UPDATE, DELETE ON public.marketplace_backtest_evidence      FROM anon, authenticated, service_role;
REVOKE UPDATE, DELETE ON public.marketplace_submission_allowed_transitions FROM anon, authenticated;
REVOKE DELETE         ON public.marketplace_submissions            FROM anon, authenticated;

-- ==========================================================================
-- SECTION 10 - comments
-- ==========================================================================
-- Intent the schema cannot express, recorded where \d+ and every schema
-- browser will show it, and in several cases a warning a reader genuinely
-- needs. COMMENT replaces, so this is idempotent.

COMMENT ON TABLE public.marketplace_submissions IS
    'The Marketplace review workflow for one Listing (Requirement 4). '
    'submission_state is the AUTHORITATIVE review lifecycle value; '
    'library_strategies.moderation_status and is_active are DERIVED from it '
    'by trg_submission_projects_moderation_status and must never be written '
    'directly by a handler (Requirement 4.12). This table stores no Listing '
    'state and no Subscription state, which is what Requirement 1.1 forbids '
    'a third table from doing.';

COMMENT ON COLUMN public.marketplace_submissions.source_strategy_id IS
    'The strategy behind the Listing, and the key of '
    'uq_submission_open_per_strategy. Resolved SERVER-SIDE from the Listing, '
    'never accepted from a request body (Requirements 7.5, 21.1). No foreign '
    'key to public.strategies: that table''s id type differs between '
    'environments, and an ALTER ... ADD FOREIGN KEY validated against '
    'existing rows could abort the migration in exactly the databases it has '
    'to apply to.';

COMMENT ON COLUMN public.marketplace_submissions.version_id IS
    'The immutable Strategy_Version the Eligibility_Gate admitted '
    '(Requirement 2.12). NULLABLE, and NULL means NOT RECORDED - never '
    '"matches". A reader deciding admission must treat NULL as a failed '
    'criterion rather than assuming a version.';

COMMENT ON COLUMN public.marketplace_submissions.submission_state IS
    'One of the 8 values of Requirement 4.1, enforced by '
    'chk_submission_state. Transitions are enforced by '
    'trg_submission_transition_guard against '
    'marketplace_submission_allowed_transitions, so an illegal edge is '
    'refused even by a direct SQL UPDATE with the service key (Requirement '
    '4.4). Only PUBLISHED is visible to the Listing_Projection (Requirements '
    '4.6, 4.7).';

COMMENT ON COLUMN public.marketplace_submissions.eligibility_outcomes IS
    'The per-criterion Eligibility_Gate verdict, one object per criterion '
    'with its stable code and pass/fail, read by the Admin_Reviewer detail '
    'response (Requirement 5.3). DEFAULT ''[]'' and NOT NULL: "no outcomes '
    'recorded" is an empty list, not a NULL a reader has to guess about.';

COMMENT ON COLUMN public.marketplace_submissions.rejection_reason IS
    'The Admin_Reviewer''s reason, TRIMMED, 1..2000 characters '
    '(Requirements 4.8, 5.5). chk_submission_rejection_reason bounds the '
    'column; marketplace_submission_guard() additionally refuses to ENTER '
    'REJECTED without one, which a row-level CHECK cannot express. It is '
    'owner-visible, so it must carry no internal threshold, identifier, '
    'column name, query text, stack trace or other user''s data '
    '(Requirement 4.8).';

COMMENT ON COLUMN public.marketplace_submissions.reviewed_by IS
    'The Admin_Reviewer who acted. ON DELETE SET NULL, deliberately: closing '
    'a reviewer''s account must not delete the Submissions they reviewed '
    '(Requirement 24.1''s explicit per-relationship delete rule).';

COMMENT ON TABLE public.marketplace_submission_allowed_transitions IS
    'The eleven permitted Submission_State edges of Requirement 4.2, as data. '
    'MUST equal SUBMISSION_TRANSITIONS in '
    'backend_app/backend/marketplace/submission_state.py; '
    'tests/test_submission_state_agreement.py asserts the two are the same '
    'set. Platform-global reference data, no owner, no user data: RLS is on '
    'with a read-to-everyone SELECT policy (the transition guard SELECTs from '
    'here as the invoking role) and NO write policy for authenticated, so a '
    'caller cannot add an edge and thereby authorise a transition the '
    'requirement forbids. UNPUBLISHED appears only as a to_state - it is the '
    'one terminal state - and no row has from_state = to_state, so a '
    'same-value write is not a transition.';

COMMENT ON TABLE public.marketplace_submission_transitions IS
    'Requirement 4.13''s record of every Submission_State transition: prior '
    'value, new value, acting identity, reason where one applies, timestamp '
    'in UTC. APPEND-ONLY - trg_submission_transitions_append_only refuses '
    'every UPDATE and every DELETE whose parent Submission still exists, and '
    'UPDATE/DELETE are revoked from anon, authenticated and service_role. A '
    'DELETE is permitted only as the declared ON DELETE CASCADE of a deleted '
    'Submission, which by then has already gone. from_state and to_state '
    'carry no CHECK on purpose: a history row naming a state a later '
    'migration retires must stay readable.';

COMMENT ON TABLE public.marketplace_backtest_evidence IS
    'Requirement 3.9''s IMMUTABLE COPY of the parameters and metrics of one '
    'Backtest_Condition, written in the same transaction as the Submission. '
    'It is a copy and not a join so that a later re-run, edit or deletion of '
    'the source strategy_backtests row cannot retroactively change what a '
    'subscriber was shown (Requirement 3.11). Every parameter and metric '
    'column is NOT NULL because a row that could hold NULL would represent '
    'evidence the Evidence_Validator already refused. APPEND-ONLY: '
    'trg_evidence_append_only plus revoked UPDATE/DELETE (Requirement 3.10). '
    'source_backtest_id carries no foreign key deliberately - the copy must '
    'survive its source.';

COMMENT ON COLUMN public.marketplace_backtest_evidence.dataset_checksum IS
    'Checksum of the exact price series this condition consumed. '
    'uq_evidence_submission_checksum makes Requirement 3.6''s '
    'PAIRWISE-DIFFERENT rule a database constraint rather than only a '
    'validator rule: it is the evidence that three conditions are three '
    'tests and not one dataset submitted three times.';

COMMENT ON COLUMN public.marketplace_backtest_evidence.executed_bar_count IS
    'Bars the source run actually executed over. NOT NULL and '
    'chk_evidence_bars >= 50, so Requirement 3.8''s "reject a '
    'Backtest_Condition whose recorded bar count is null, absent, or below '
    '50" is structural here - unlike strategy_backtests, where 006''s '
    'chk_sb_executed_bar_count admits NULL so that an honest short '
    'exploratory run can still be recorded.';

COMMENT ON TABLE public.marketplace_price_evaluations IS
    'A persisted Price_Range for one Submission and currency, in Minor_Units '
    '(Requirement 8.1). inputs_digest identifies the evidence it was computed '
    'from, so set_price can prove the range it enforces against is the range '
    'that evidence produces (Requirements 8.3, 8.8). NO accuracy, confidence, '
    'precision or error column exists and none may be added: Requirement 8.7 '
    'is a requirement about ABSENCE - a Price_Range is guidance derived from '
    'evidence, not a prediction with a measurable error. Deliberately NOT '
    'unique over (submission_id, currency, inputs_digest): a new evaluator '
    'version must be able to record its own range without overwriting the one '
    'a price was previously accepted against.';

COMMENT ON COLUMN public.library_strategies.price_minor IS
    'The Listing price in Minor_Units, and the AUTHORITATIVE price '
    '(Requirement 8.12). money.py::amount_for_listing returns this integer '
    'unchanged, so no float appears between the price an owner set and the '
    'amount a purchaser is charged. The legacy price NUMERIC(10,2) is derived '
    'from this column by trg_ls_price_mirror and is for display and for '
    'creator_analytics only (Requirement 1.4); writing price alone does NOT '
    'change what a purchaser is charged. NULL means unpriced - never free.';

COMMENT ON COLUMN public.library_strategies.source_cloning_enabled IS
    'The owner''s explicit per-Listing source-cloning consent (Requirement '
    '7.4). NOT NULL DEFAULT FALSE, and existing rows are back-filled to '
    'FALSE, so a Listing whose owner never made this choice is NOT clonable '
    'and clone_strategy answers 403 MARKETPLACE_CLONING_DISABLED '
    '(Requirement 7.3). NOT NULL on purpose: a NULL would be an absent '
    'consent decision that every call site would have to coalesce '
    'identically.';

COMMENT ON COLUMN public.library_strategies.supported_timeframes IS
    'The timeframes this Listing may be executed on. On the '
    'Listing_Projection allow-list (Requirement 6.2). TEXT[] and not a '
    'comma-joined string, because a reader that has to split a string will '
    'eventually split it differently from the writer.';

COMMENT ON COLUMN public.library_strategies.market_type IS
    'Market classification for the Listing (Requirement 6.2''s asset and '
    'market information). No CHECK: the vocabulary of market types belongs to '
    'the exchange layer, and enumerating it here would make adding a venue a '
    'schema change.';

COMMENT ON COLUMN public.library_strategies.condition_count IS
    'How many Backtest_Conditions stand behind this Listing (Requirement '
    '6.2). Denormalised from marketplace_backtest_evidence so a catalogue '
    'page costs a constant number of round trips independent of the Listing '
    'count (Requirement 27.1, Property P-57) rather than one count(*) per '
    'Listing.';

COMMENT ON FUNCTION public.marketplace_submission_guard() IS
    'Requirement 4.4''s enforcement: rejects any Submission_State transition '
    'absent from marketplace_submission_allowed_transitions, and any '
    'transition into REJECTED without a 1..2000-character trimmed reason, '
    'with ERRCODE 23514 and a message naming both states. A same-value write '
    'returns early - it is not a transition. Writes no history row: '
    'Requirement 4.4 requires a rejected write to record none, and the '
    'application inserts history after the UPDATE returns, in the same '
    'transaction.';

COMMENT ON FUNCTION public.marketplace_project_listing_state() IS
    'Requirement 4.12''s single shared projection of Submission_State onto '
    'library_strategies.moderation_status and is_active, and Requirement '
    '4.6''s "every public response served after that transaction commits '
    'includes the Listing". MUST equal MODERATION_STATUS_FOR_STATE and '
    'IS_ACTIVE_FOR_STATE in submission_state.py. Never produces the legacy '
    '''featured'' moderation_status. It clears is_featured when the projected '
    'status is not ''approved'' - without that, '
    'chk_ls_featured_requires_published would make suspending or '
    'unpublishing a FEATURED Listing impossible and Requirements 4.7 and '
    '4.10 unreachable for it. It never SETS is_featured: featuring is an '
    'editorial decision, not a consequence of publication.';

COMMENT ON FUNCTION public.marketplace_append_only_guard() IS
    'Refuses every UPDATE, and every DELETE whose parent Submission still '
    'exists, on marketplace_submission_transitions and '
    'marketplace_backtest_evidence (Requirements 3.10, 4.13). The '
    'parent-existence test is what lets Requirement 24.1''s declared ON '
    'DELETE CASCADE from library_strategies and auth.users still complete: '
    'PostgreSQL runs a cascade as an AFTER trigger on the parent, so during '
    'one the parent row is already gone, while a direct DELETE always runs '
    'with it present.';

COMMENT ON FUNCTION public.library_strategies_mirror_price() IS
    'Mirrors the retained legacy library_strategies.price from the '
    'authoritative price_minor, ONE DIRECTION ONLY, so the existing '
    'browse_library / trending / featured / creator_analytics readers keep '
    'working unchanged (Requirements 25, 1.4). Exact and integral: both '
    'supported currencies have ISO 4217 minor-unit exponent 2. Leaves price '
    'untouched for a NULL price_minor and for any currency outside the '
    'supported set, rather than guessing an exponent and displaying a price '
    'wrong by two orders of magnitude.';

COMMENT ON FUNCTION public.marketplace_touch_updated_at() IS
    'Requirement 24.5''s update timestamp for the tables this specification '
    'introduces. A separate function from the shared '
    'public.update_updated_at_column() that 001 defines, because replacing a '
    'function fourteen existing triggers already call would be a change to an '
    'existing control.';

-- ==========================================================================
-- SECTION 11 - postflight
-- ==========================================================================
-- Turns the header's promises into facts about the database: the five tables,
-- every named constraint, every named index, every function, every trigger,
-- every policy, the eleven seed rows, and - on the one pre-existing table
-- this file touches - that its policy count did not move and that it gained
-- at most the one trigger of section 7e.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    ls_policies_before INTEGER := current_setting('aerora.ls_policies_before')::INTEGER;
    ls_indexes_before  INTEGER := current_setting('aerora.ls_indexes_before')::INTEGER;
    ls_triggers_before INTEGER := current_setting('aerora.ls_triggers_before')::INTEGER;
    ls_policies_now    INTEGER;
    ls_indexes_now     INTEGER;
    ls_triggers_now    INTEGER;
    missing            TEXT;
    seed_rows          BIGINT;
    rls_off            TEXT;
BEGIN
    -- (a) The five tables exist.
    SELECT string_agg(expected.table_name, ', ' ORDER BY expected.table_name)
      INTO missing
      FROM (VALUES ('marketplace_submissions'),
                   ('marketplace_submission_transitions'),
                   ('marketplace_backtest_evidence'),
                   ('marketplace_price_evaluations'),
                   ('marketplace_submission_allowed_transitions'))
             AS expected(table_name)
     WHERE to_regclass('public.' || expected.table_name) IS NULL;

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '007 postflight failed: table(s) % were not created.',
                        missing;
    END IF;

    -- (b) The library_strategies columns exist.
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('price_minor'), ('source_cloning_enabled'),
                   ('supported_timeframes'), ('market_type'),
                   ('condition_count'))
             AS expected(column_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns c
                WHERE c.table_schema::TEXT = 'public'
                  AND c.table_name::TEXT   = 'library_strategies'
                  AND c.column_name::TEXT  = expected.column_name);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '007 postflight failed: public.library_strategies is '
                        'missing column(s) % (Requirements 6.2, 7.4, 8.12).',
                        missing;
    END IF;

    -- (c) Every named constraint exists, on the right table.
    SELECT string_agg(format('%s on %s', expected.conname, expected.table_name),
                      ', ' ORDER BY expected.conname)
      INTO missing
      FROM (VALUES
                ('chk_submission_state',              'marketplace_submissions'),
                ('chk_submission_rejection_reason',   'marketplace_submissions'),
                ('uq_evidence_submission_backtest',   'marketplace_backtest_evidence'),
                ('uq_evidence_submission_checksum',   'marketplace_backtest_evidence'),
                ('chk_evidence_window',               'marketplace_backtest_evidence'),
                ('chk_evidence_trades',               'marketplace_backtest_evidence'),
                ('chk_evidence_bars',                 'marketplace_backtest_evidence'),
                ('chk_price_range_ordered',           'marketplace_price_evaluations'),
                ('chk_ls_price_minor',                'library_strategies'),
                ('chk_ls_featured_requires_published','library_strategies'),
                ('pk_submission_allowed_transitions', 'marketplace_submission_allowed_transitions')
            ) AS expected(conname, table_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_constraint pc
                WHERE pc.conname  = expected.conname
                  AND pc.conrelid = ('public.' || expected.table_name)::regclass);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '007 postflight failed: constraint(s) % are absent '
                        '(Requirements 4.5, 8.4, 24.2, 24.3).', missing;
    END IF;

    -- (d) Every named index exists. uq_submission_open_per_strategy is the
    --     one whose PARTIAL predicate is the load-bearing part, so it is
    --     re-checked for partiality below.
    SELECT string_agg(format('%s on %s', expected.indexname, expected.table_name),
                      ', ' ORDER BY expected.indexname)
      INTO missing
      FROM (VALUES
                ('uq_submission_open_per_strategy',  'marketplace_submissions'),
                ('idx_submissions_state',            'marketplace_submissions'),
                ('idx_submissions_owner',            'marketplace_submissions'),
                ('idx_submissions_listing',          'marketplace_submissions'),
                ('idx_submission_transitions',       'marketplace_submission_transitions'),
                ('idx_submission_transitions_owner', 'marketplace_submission_transitions'),
                ('idx_evidence_submission',          'marketplace_backtest_evidence'),
                ('idx_evidence_owner',               'marketplace_backtest_evidence'),
                ('idx_price_eval_lookup',            'marketplace_price_evaluations'),
                ('idx_price_eval_owner',             'marketplace_price_evaluations')
            ) AS expected(indexname, table_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_indexes pi
                WHERE pi.schemaname     = 'public'
                  AND pi.tablename      = expected.table_name
                  AND pi.indexname::TEXT = expected.indexname);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '007 postflight failed: index(es) % were not created '
                        '(Requirement 24.4).', missing;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public'
           AND tablename  = 'marketplace_submissions'
           AND indexname  = 'uq_submission_open_per_strategy'
           AND indexdef ILIKE '%UNIQUE%'
           AND indexdef ILIKE '%WHERE%'
    ) THEN
        RAISE EXCEPTION
            '007 postflight failed: uq_submission_open_per_strategy exists but '
            'is not a PARTIAL UNIQUE index. Both halves are load-bearing: '
            'without UNIQUE the concurrent-submission race of Requirement 2.8 '
            'is representable, and without the WHERE clause a resubmission '
            'after a rejection (Requirement 4.2''s REJECTED -> DRAFT -> '
            'SUBMITTED path) becomes impossible.';
    END IF;

    -- (e) The four functions and the six triggers exist.
    SELECT string_agg(expected.proname, ', ' ORDER BY expected.proname)
      INTO missing
      FROM (VALUES ('marketplace_submission_guard'),
                   ('marketplace_project_listing_state'),
                   ('marketplace_append_only_guard'),
                   ('library_strategies_mirror_price'),
                   ('marketplace_touch_updated_at'))
             AS expected(proname)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_proc p
                 JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public'
                  AND p.proname = expected.proname);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '007 postflight failed: function(s) % are absent.',
                        missing;
    END IF;

    SELECT string_agg(format('%s on %s', expected.tgname, expected.table_name),
                      ', ' ORDER BY expected.tgname)
      INTO missing
      FROM (VALUES
                ('trg_submission_transition_guard',           'marketplace_submissions'),
                ('trg_submission_projects_moderation_status', 'marketplace_submissions'),
                ('trg_submission_transitions_append_only',    'marketplace_submission_transitions'),
                ('trg_evidence_append_only',                  'marketplace_backtest_evidence'),
                ('trg_ls_price_mirror',                       'library_strategies'),
                ('trg_marketplace_submissions_updated_at',    'marketplace_submissions')
            ) AS expected(tgname, table_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_trigger t
                WHERE t.tgname   = expected.tgname
                  AND t.tgrelid  = ('public.' || expected.table_name)::regclass
                  AND NOT t.tgisinternal);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '007 postflight failed: trigger(s) % are absent. '
                        'Requirement 4.4''s "THE Persistence_Layer SHALL '
                        'reject the write" and Requirements 3.10 and 4.13''s '
                        'immutability depend on them.', missing;
    END IF;

    -- (f) The seed holds exactly the eleven pairs of Requirement 4.2, no
    --     twelfth, and no self-edge.
    SELECT count(*) INTO seed_rows
      FROM public.marketplace_submission_allowed_transitions;

    IF seed_rows <> 11 THEN
        RAISE EXCEPTION
            '007 postflight failed: '
            'marketplace_submission_allowed_transitions holds % row(s), not '
            'the 11 permitted transitions of Requirement 4.2. It must equal '
            'SUBMISSION_TRANSITIONS in '
            'backend_app/backend/marketplace/submission_state.py, which '
            'tests/test_submission_state_agreement.py asserts.', seed_rows;
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.marketplace_submission_allowed_transitions
         WHERE from_state = to_state
    ) THEN
        RAISE EXCEPTION
            '007 postflight failed: '
            'marketplace_submission_allowed_transitions holds a self-edge. '
            'Requirement 4.2 forbids "any transition from a value to that '
            'same value", and the guard relies on the absence of such a row.';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.marketplace_submission_allowed_transitions
         WHERE from_state = 'UNPUBLISHED'
    ) THEN
        RAISE EXCEPTION
            '007 postflight failed: '
            'marketplace_submission_allowed_transitions holds an edge OUT of '
            'UNPUBLISHED. Requirement 4.2 makes it the one terminal state.';
    END IF;

    -- (g) RLS is on, and each new table has at least the two policies.
    SELECT string_agg(expected.table_name, ', ' ORDER BY expected.table_name)
      INTO rls_off
      FROM (VALUES ('marketplace_submissions'),
                   ('marketplace_submission_transitions'),
                   ('marketplace_backtest_evidence'),
                   ('marketplace_price_evaluations'),
                   ('marketplace_submission_allowed_transitions'))
             AS expected(table_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_class c
                WHERE c.oid = ('public.' || expected.table_name)::regclass
                  AND c.relrowsecurity);

    IF rls_off IS NOT NULL THEN
        RAISE EXCEPTION '007 postflight failed: row level security is DISABLED '
                        'on %. Requirement 21.2 requires it on every table '
                        'this specification introduces.', rls_off;
    END IF;

    SELECT string_agg(expected.table_name, ', ' ORDER BY expected.table_name)
      INTO missing
      FROM (VALUES ('marketplace_submissions'),
                   ('marketplace_submission_transitions'),
                   ('marketplace_backtest_evidence'),
                   ('marketplace_price_evaluations'),
                   ('marketplace_submission_allowed_transitions'))
             AS expected(table_name)
     WHERE (SELECT count(*) FROM pg_policies p
             WHERE p.schemaname = 'public'
               AND p.tablename  = expected.table_name) < 2;

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '007 postflight failed: table(s) % have fewer than the '
                        '2 policies this file creates, so with RLS enabled '
                        'they are unreachable to every non-superuser role '
                        '(Requirements 21.2, 21.3).', missing;
    END IF;

    -- (h) Nothing else moved on the one pre-existing table this file touches.
    SELECT count(*) INTO ls_policies_now
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'library_strategies';
    SELECT count(*) INTO ls_indexes_now
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'library_strategies';
    SELECT count(*) INTO ls_triggers_now
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.library_strategies'::regclass
       AND NOT t.tgisinternal;

    IF ls_policies_now <> ls_policies_before THEN
        RAISE EXCEPTION '007 postflight failed: the row-level security policy '
                        'count on public.library_strategies moved from % to '
                        '%. This file must not add, alter or remove a policy '
                        'on a pre-existing table (Requirement 25.1).',
                        ls_policies_before, ls_policies_now;
    END IF;

    IF ls_indexes_now <> ls_indexes_before THEN
        RAISE EXCEPTION '007 postflight failed: the index count on '
                        'public.library_strategies moved from % to %. This '
                        'file creates no index there.',
                        ls_indexes_before, ls_indexes_now;
    END IF;

    IF ls_triggers_now > ls_triggers_before + 1 THEN
        RAISE EXCEPTION '007 postflight failed: the user trigger count on '
                        'public.library_strategies moved from % to %, more '
                        'than the 1 trigger (trg_ls_price_mirror) this file '
                        'creates there.', ls_triggers_before, ls_triggers_now;
    END IF;

    RAISE NOTICE '007 complete: marketplace_submissions, '
                 'marketplace_submission_transitions, '
                 'marketplace_backtest_evidence, '
                 'marketplace_price_evaluations and '
                 'marketplace_submission_allowed_transitions exist with % '
                 'seed rows, every named constraint and index, 5 functions '
                 'and 6 triggers, RLS enabled with an owner and a '
                 'service-role policy each. library_strategies gained '
                 'price_minor, source_cloning_enabled, supported_timeframes, '
                 'market_type and condition_count, chk_ls_price_minor, '
                 'chk_ls_featured_requires_published and trg_ls_price_mirror; '
                 'its policies % (unchanged), indexes % (unchanged), '
                 'triggers % -> %. No existing row was rewritten.',
                 seed_rows, ls_policies_now, ls_indexes_now,
                 ls_triggers_before, ls_triggers_now;
END $$;

COMMIT;

-- ==========================================================================
-- VERIFICATION QUERIES - run these after applying, the way 005a/005b/006 do
-- ==========================================================================
--
-- 1) The five tables and their column counts:
--
--    SELECT table_name, count(*) AS columns
--      FROM information_schema.columns
--     WHERE table_schema = 'public'
--       AND table_name IN ('marketplace_submissions',
--                          'marketplace_submission_transitions',
--                          'marketplace_backtest_evidence',
--                          'marketplace_price_evaluations',
--                          'marketplace_submission_allowed_transitions')
--     GROUP BY table_name ORDER BY table_name;
--
--    Expect 5 rows: marketplace_backtest_evidence 26,
--    marketplace_price_evaluations 11, marketplace_submission_allowed_
--    transitions 4, marketplace_submission_transitions 10,
--    marketplace_submissions 15.
--
-- 2) The eleven transition pairs, which MUST equal SUBMISSION_TRANSITIONS in
--    backend_app/backend/marketplace/submission_state.py:
--
--    SELECT from_state, to_state
--      FROM public.marketplace_submission_allowed_transitions
--     ORDER BY from_state, to_state;
--
--    Expect exactly 11 rows:
--      APPROVED     -> PUBLISHED
--      DRAFT        -> SUBMITTED
--      PUBLISHED    -> SUSPENDED
--      PUBLISHED    -> UNPUBLISHED
--      REJECTED     -> DRAFT
--      SUBMITTED    -> REJECTED
--      SUBMITTED    -> UNDER_REVIEW
--      SUSPENDED    -> PUBLISHED
--      SUSPENDED    -> UNPUBLISHED
--      UNDER_REVIEW -> APPROVED
--      UNDER_REVIEW -> REJECTED
--    No row with from_state = to_state, and no row with
--    from_state = 'UNPUBLISHED'.
--
-- 3) The CHECK and UNIQUE constraints:
--
--    SELECT conrelid::regclass AS on_table, conname, contype,
--           pg_get_constraintdef(oid)
--      FROM pg_constraint
--     WHERE conname IN ('chk_submission_state',
--                       'chk_submission_rejection_reason',
--                       'uq_evidence_submission_backtest',
--                       'uq_evidence_submission_checksum',
--                       'chk_evidence_window', 'chk_evidence_trades',
--                       'chk_evidence_bars', 'chk_price_range_ordered',
--                       'chk_ls_price_minor',
--                       'chk_ls_featured_requires_published')
--     ORDER BY on_table, conname;
--
--    Expect 10 rows. chk_ls_featured_requires_published may show
--    convalidated = false on a database carrying legacy 'featured' rows - see
--    query 9.
--
-- 4) The indexes, and above all the PARTIAL predicate of the unique one:
--
--    SELECT tablename, indexname, indexdef
--      FROM pg_indexes
--     WHERE schemaname = 'public'
--       AND tablename LIKE 'marketplace%'
--     ORDER BY tablename, indexname;
--
--    uq_submission_open_per_strategy must read:
--      CREATE UNIQUE INDEX uq_submission_open_per_strategy ON
--      public.marketplace_submissions USING btree (source_strategy_id)
--      WHERE (submission_state = ANY (ARRAY['SUBMITTED'::text,
--      'UNDER_REVIEW'::text, 'APPROVED'::text, 'PUBLISHED'::text]))
--
-- 5) The state machine is enforced against a DIRECT UPDATE (Requirement 4.4,
--    Property P-49). Run inside a transaction you ROLL BACK:
--
--    BEGIN;
--      -- pick any Submission
--      SELECT id, submission_state FROM public.marketplace_submissions LIMIT 1;
--      -- a DRAFT row: DRAFT -> PUBLISHED is not one of the eleven edges
--      UPDATE public.marketplace_submissions
--         SET submission_state = 'PUBLISHED' WHERE id = '<the id>';
--      -- expect: ERROR 23514 disallowed Submission_State transition
--      --         DRAFT -> PUBLISHED
--    ROLLBACK;
--
--    And the rejection-reason half:
--
--    BEGIN;
--      UPDATE public.marketplace_submissions
--         SET submission_state = 'REJECTED', rejection_reason = '   '
--       WHERE id = '<a SUBMITTED id>';
--      -- expect: ERROR 23514 REJECTED requires a rejection reason of
--      --         1..2000 characters
--    ROLLBACK;
--
-- 6) The moderation projection (Requirements 4.6, 4.7, 4.12):
--
--    BEGIN;
--      UPDATE public.marketplace_submissions
--         SET submission_state = 'SUBMITTED' WHERE id = '<a DRAFT id>';
--      SELECT ls.moderation_status, ls.is_active
--        FROM public.library_strategies ls
--        JOIN public.marketplace_submissions s ON s.listing_id = ls.id
--       WHERE s.id = '<the id>';
--      -- expect ('pending', false)
--    ROLLBACK;
--
--    Then walk it to PUBLISHED through UNDER_REVIEW and APPROVED and expect
--    ('approved', true) - and ('pending', false) at APPROVED, which is
--    Requirement 4.7.
--
-- 7) The append-only guards (Requirements 3.10, 4.13):
--
--    BEGIN;
--      UPDATE public.marketplace_backtest_evidence
--         SET total_return_pct = 999 WHERE id = '<any id>';
--      -- expect: ERROR 23514 marketplace_backtest_evidence is append-only
--      ROLLBACK;
--    BEGIN;
--      DELETE FROM public.marketplace_backtest_evidence WHERE id = '<any id>';
--      -- expect: ERROR 23514 ... DELETE is not permitted while submission
--      --         <uuid> exists
--    ROLLBACK;
--
--    And the cascade that MUST still work:
--
--    BEGIN;
--      DELETE FROM public.marketplace_submissions WHERE id = '<any id>';
--      -- expect: success. Its transitions and evidence went with it.
--    ROLLBACK;
--
-- 8) The price mirror (Requirement 25, 1.4):
--
--    BEGIN;
--      UPDATE public.library_strategies
--         SET price_minor = 4999, currency = 'USD' WHERE id = '<any id>';
--      SELECT price_minor, price, currency FROM public.library_strategies
--       WHERE id = '<the id>';
--      -- expect price_minor = 4999, price = 49.00
--    ROLLBACK;
--
-- 9) Whether chk_ls_featured_requires_published could be validated, and what
--    to reconcile if not:
--
--    SELECT conname, convalidated FROM pg_constraint
--     WHERE conrelid = 'public.library_strategies'::regclass
--       AND conname  = 'chk_ls_featured_requires_published';
--
--    SELECT id, name, moderation_status, is_featured
--      FROM public.library_strategies
--     WHERE is_featured AND moderation_status <> 'approved';
--
--    Any rows here are the legacy 'featured' moderation_status
--    admin_moderate_strategy writes. The constraint is enforced for every new
--    write regardless; set moderation_status = 'approved' where the Listing
--    should stay featured, or is_featured = FALSE where it should not, then
--    re-run this file to validate.
--
-- 10) RLS and the policies:
--
--    SELECT tablename, policyname, cmd, roles
--      FROM pg_policies
--     WHERE schemaname = 'public' AND tablename LIKE 'marketplace%'
--     ORDER BY tablename, policyname;
--
--    Expect 2 per table, 10 in all. And library_strategies must be
--    UNCHANGED - compare against the count section 0 reported.
--
-- 11) Grants. Expect authenticated: SELECT+INSERT+UPDATE on
--     marketplace_submissions and marketplace_price_evaluations,
--     SELECT+INSERT on marketplace_submission_transitions and
--     marketplace_backtest_evidence, SELECT only on
--     marketplace_submission_allowed_transitions. No DELETE anywhere.
--
--    SELECT table_name, grantee, string_agg(privilege_type, ',' ORDER BY
--           privilege_type) AS privileges
--      FROM information_schema.role_table_grants
--     WHERE table_schema = 'public' AND table_name LIKE 'marketplace%'
--       AND grantee IN ('anon', 'authenticated', 'service_role')
--     GROUP BY table_name, grantee ORDER BY table_name, grantee;
--
-- 12) Idempotency (Requirements 24.7, 24.8, Property P-58). Re-running this
--     whole file must report no error, must leave the seed at 11 rows, and
--     must leave every count above unchanged.
--
-- ==========================================================================
-- END OF MIGRATION 007. NEXT IN DEPENDENCY ORDER:
--   008_marketplace_settlement.sql  (needs marketplace_submissions above)
-- ==========================================================================
