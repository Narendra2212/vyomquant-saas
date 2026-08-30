-- 006_backtest_evidence_columns.sql  (migration 006)
--
-- PURPOSE
--   Reconcile the two divergent definitions of strategy_backtests so that a
--   completed backtest can carry, on the row itself, every fact the
--   marketplace Evidence_Validator has to read - regardless of which
--   CREATE TABLE IF NOT EXISTS won the race in a given environment.
--
--   Requirements 3.4, 3.8, 24.2, 24.4, 24.7, 24.8, 24.9.
--   Source of the DDL: design.md -> "6. strategy_backtests has two divergent
--   definitions and records no bar count (newly found)", statement for
--   statement, schema-qualified with public. and with the CHECK put behind a
--   pg_constraint guard (see DELIBERATE ADDITIONS below).
--
-- THE DIVERGENCE THIS FILE CLOSES
--   Two files each declare strategy_backtests with CREATE TABLE IF NOT
--   EXISTS, so whichever ran first in a given database decided the shape and
--   the second one silently did nothing:
--
--     definition A - backend_app/migrations/001_strategy_architecture.sql
--                    line 118. id/user_id/strategy_id UUID. HAS version_id
--                    (NOT NULL, REFERENCES strategy_versions(id)),
--                    final_capital, completed_at, error_message. HAS NONE of
--                    the four reproducibility columns.
--     definition B - migrations/006_reconcile_production_database.sql
--                    line 445. id/user_id/strategy_id TEXT. HAS
--                    engine_version, schema_version, dag_hash,
--                    dataset_checksum, completed_at. HAS NO version_id, NO
--                    final_capital and NO error_message (it spells the
--                    failure text "error" instead).
--
--   Neither records an executed bar count. backtesting_engine.py line 189
--   tests len(price_data) < 50 and throws the number away; line 332 logs it;
--   backtest_runtime.run_backtest line 302 knows len(ohlcv_data) and, before
--   task 12.1, did not write it. So no row could satisfy Requirement 3.8's
--   "non-null executed bar count of at least 50" on evidence that existed
--   and was never written down.
--
--   backtest_service.create_backtest writes engine_version, schema_version,
--   dag_hash and dataset_checksum - four columns absent under definition A -
--   which is the PostgreSQL 42703 / PGRST204 condition Requirement 24.9
--   exists to prevent a repeat of.
--
--   This file is the union: nine columns, each added only where it is
--   missing, so BOTH starting shapes end up able to hold the whole of
--   Requirements 3.4 and 3.8. Requirement 24.8's "applies from an empty
--   database and from the current production schema revision" is exactly
--   that claim, and tests/test_backtest_evidence_columns_regression.py
--   asserts it by parsing this file against each base definition.
--
-- SCOPE - NINE COLUMNS, ONE CHECK, TWO INDEXES, ONE TRANSACTION
--     1.0  preflight assertions and the policy/trigger/index inventory
--     1.1  the nine columns, all nullable
--     1.2  column shape assertion (see DELIBERATE ADDITIONS 3)
--     1.3  chk_sb_executed_bar_count, behind a pg_constraint guard
--     1.4  the two indexes
--     1.5  column comments
--     1.6  postflight - the objects exist, and nothing else moved
--
--   Nothing about marketplace_submissions, marketplace_backtest_evidence,
--   library_strategies, paper trading or the signal environment is here.
--   Those are migrations 007 through 010, which depend on this file.
--
-- WHY A SEPARATE FILE RATHER THAN AN EDIT TO 001
--   The same reason 004b/004c/004d/004e and 005a/005b state: migrations here
--   are applied BY HAND, per file, and NOTHING RECORDS WHICH FILES AN
--   ENVIRONMENT HAS RUN - there is no migration table and
--   .github/workflows/03-deploy.yml has no migration step. Editing a file an
--   operator may already have applied leaves no signal that it changed, so
--   the new statements would simply never run. A new filename is the signal.
--   001 and migrations/006_reconcile_production_database.sql are both left
--   byte-identical.
--
--   The name collision with migrations/006_reconcile_production_database.sql
--   is unfortunate and deliberate: this file is 006 of the
--   backend_app/migrations/ sequence (after 005a/005b), and that file is 006
--   of the repository-root migrations/ sequence. They are different
--   sequences with different application histories, and renumbering either
--   would break the ordering an operator has already followed.
--
-- WHY version_id IS ADDED NULLABLE
--   Definition A already has it NOT NULL, and this file does not relax that
--   - the column is left exactly as definition A declared it, constraint
--   included, because ADD COLUMN IF NOT EXISTS does not alter an existing
--   column and weakening an existing control is not this task's business.
--   Under definition B the column does not exist, and adding it NOT NULL
--   would demand a fabricated value for every backtest already on the table.
--   Requirement 27's nullable-only rule for migration columns says the same.
--
--   The consequence is a real one and is handled in the application, not
--   here: a row may carry version_id IS NULL. evidence_validator therefore
--   treats a NULL version_id as a FAILED criterion rather than assuming a
--   version, which is the correct reading of Requirement 3.4's "non-null
--   values" obligation and of Requirement 3.3's "all conditions test one
--   immutable Strategy_Version". NULL means "not recorded", never "matches".
--
--   No FOREIGN KEY to strategy_versions is added. Definition A already has
--   one; under definition B the primary keys are TEXT, strategy_versions may
--   itself be absent or differently typed, and an ALTER ... ADD FOREIGN KEY
--   would be validated against existing rows and could abort this
--   transaction in an environment this file is supposed to repair.
--   Requirement 24.1's per-relationship delete rule is met for the rows this
--   specification INTRODUCES (007 onwards); strategy_backtests is
--   pre-existing and its keys are not rewritten here.
--
-- WHY THE CHECK IS >= 0 AND NOT >= 50
--   chk_sb_executed_bar_count admits NULL or any non-negative count. It
--   keeps a stored count a count: no negative bar count can ever be
--   persisted. Requirement 3.8's 50-bar MINIMUM is a submission-admission
--   rule, not a storage rule - a legitimately short exploratory backtest may
--   be recorded with 12 bars and simply fail its marketplace criterion. A
--   database CHECK of >= 50 would refuse to store that run at all, which
--   would be this migration inventing policy the requirement does not ask
--   for and would make the honest recording of a short run impossible.
--
--   NOTE, because it is load-bearing rather than pedantic: A CHECK
--   CONSTRAINT THAT EVALUATES TO NULL PASSES. executed_bar_count is added
--   NULLABLE with NO DEFAULT, so every pre-existing row has NULL in it the
--   moment this commits, and the IS NULL branch is written explicitly so
--   that the intent is stated rather than relied upon. A reader MUST treat
--   NULL as "no count was recorded" - never as zero, and never as a pass.
--   Requirement 3.8 requires exactly that: a null or absent count is a
--   rejection, not a default.
--
-- FOUR DELIBERATE ADDITIONS BEYOND THE DESIGN'S DDL, AND WHY
--   The design snippet is a schema sketch. These make it safe to apply by
--   hand, twice, to a database whose history nobody recorded. None weakens a
--   control and none changes a column, constraint, index or policy the
--   design specifies.
--
--   1. Preflight assertions (section 1.0). A readable RAISE naming the
--      missing object instead of a bare 42P01 from inside the ALTER, plus a
--      refusal to run without row-level security on public.strategy_backtests
--      (see below), plus the policy/index/trigger inventory that section 1.6
--      re-checks.
--
--   2. A guarded ADD CONSTRAINT block (section 1.3). A bare ADD CONSTRAINT is
--      NOT idempotent: a second run raises 42710 duplicate_object and aborts
--      the whole file. PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS, and
--      removing-then-re-adding would both violate this file's additive-only
--      rule and open a window inside the transaction where the bound is
--      unenforced. Same pattern as 005b section 1.3, 004e section 3 and 004d
--      section 1b.
--
--   3. A column shape assertion (section 1.2), copied from 005b section 1.2.
--      ADD COLUMN IF NOT EXISTS is SILENT when a column of that name already
--      exists with a different type - for instance one added by hand during
--      an investigation, or one carried over from a shape nobody recorded.
--      "The column is there" then does not mean "the column is usable", and
--      the failure surfaces much later as a cast error, a truncated hash or
--      a bar count stored as text that sorts lexically. Because the two base
--      definitions genuinely disagree about the ID types, each column is
--      checked against the SET of types that are actually equivalent for its
--      readers, not against one spelling.
--
--   4. Column comments (section 1.5) and a postflight (section 1.6) that
--      proves the nine columns, the CHECK and the two indexes exist and that
--      this file added no policy, no trigger and no index beyond its own two.
--
-- WHY THIS FILE REFUSES TO RUN WITHOUT RLS ON public.strategy_backtests
--   Every column added here is per-user financial evidence: which immutable
--   version was tested, over which dataset, to what final capital, with what
--   dag hash. Adding them to a table whose row-level isolation is off would
--   let any authenticated caller read another user's unpublished research and
--   the checksums that identify their datasets. Both base definitions enable
--   RLS on this table (001 section "RLS Policies for strategy_backtests";
--   the production reconciliation's PART 7), so a database missing it is a
--   database in an unexpected state, and the right response is to stop.
--
--   That is a refusal to make an existing hole worse, not a claim to fix one.
--   This file deliberately does NOT enable RLS itself and does NOT create,
--   alter or remove a policy - Requirement 25.1's "existing behaviour
--   retained" and the one rule this plan repeats everywhere is that no task
--   rewrites an existing control. Row level security is ROW-scoped, not
--   column-scoped, so the existing owner policies apply unchanged to all
--   nine new columns; tenant isolation is exactly as strong after this
--   migration as before it.
--
-- APPLICATION
--   NOT applied automatically. Apply this file explicitly against the target
--   database, then run the verification queries at the bottom, the way
--   scripts/forensics/apply_migration_007.py applied 007.
--
--   Until it is applied, these columns do not exist. Every code path that
--   reads or writes them must degrade the way backtest_service.py already
--   does for executed_bar_count: a WARNING NAMING THIS FILE
--   ("006_backtest_evidence_columns.sql"), not a 500, and NEVER a results
--   write lost to a column a hand-applied migration has not created yet. A
--   missing column surfaces from PostgREST as PGRST204 on an insert or update
--   and as 42703 undefined_column on a select.
--
-- SAFETY
--   * Additive only (Requirement 24.7). Nothing is removed, renamed,
--     truncated or deleted; no existing column's type, nullability or default
--     is altered; no row is inserted, updated or deleted; no policy is
--     created, altered or removed; row-level security is neither enabled nor
--     disabled; no trigger, GRANT or REVOKE.
--   * Fully idempotent (Requirement 24.7, 24.8). ADD COLUMN IF NOT EXISTS
--     for all nine columns; the CHECK behind a pg_constraint existence guard;
--     CREATE INDEX IF NOT EXISTS for both indexes; COMMENT replaces. A second
--     application adds nothing and raises nothing.
--   * One transaction. Either the nine columns, the CHECK, the two indexes
--     and the comments all exist, or none of them do. A strategy_backtests
--     carrying executed_bar_count without chk_sb_executed_bar_count is never
--     visible to a session.
--   * No existing row is rewritten. Backfilling a bar count for a historical
--     backtest is impossible without re-running it, and fabricating one would
--     manufacture the very evidence Requirement 3 exists to make real.

-- ==========================================================================
-- SECTION 1 - task 11.1: strategy_backtests evidence columns
-- ==========================================================================

BEGIN;

-- 1.0) Preflight -------------------------------------------------------
-- Read-only assertions plus three transaction-local counters. Fails with a
-- readable message instead of a bare undefined_table error, refuses to add
-- per-user research evidence to a table without row-level security, and
-- records the policy/index/trigger inventory so section 1.6 can prove this
-- file left all three alone (no NAME is hard-coded, so an environment that
-- renamed a policy cannot false-alarm; every count is taken inside this one
-- transaction, so they are comparable by construction).
-- Idempotent: reads catalogues, writes nothing but three settings local to
-- this transaction.
DO $$
DECLARE
    rls_on    BOOLEAN;
    policies  INTEGER;
    indexes   INTEGER;
    triggers  INTEGER;
    missing   TEXT;
BEGIN
    IF to_regclass('public.strategy_backtests') IS NULL THEN
        RAISE EXCEPTION
            '006 precondition failed: table public.strategy_backtests does '
            'not exist. Apply backend_app/migrations/'
            '001_strategy_architecture.sql (section 3) first, or '
            'migrations/006_reconcile_production_database.sql (PART 7). This '
            'file reconciles those two definitions; it does not create the '
            'table, because doing so would add a THIRD divergent shape.';
    END IF;

    -- The three columns the composite index of section 1.4 is built on. They
    -- are present under BOTH base definitions, so an absence here means the
    -- table is in a shape neither file produced and section 1.4 would fail
    -- with a message that does not name the cause.
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('user_id'), ('strategy_id'), ('status'))
             AS expected(column_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns c
                WHERE c.table_schema::TEXT = 'public'
                  AND c.table_name::TEXT   = 'strategy_backtests'
                  AND c.column_name::TEXT  = expected.column_name);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            '006 precondition failed: public.strategy_backtests is missing '
            'column(s) %, which both base definitions declare. The table is '
            'in a shape this file was not written against; reconcile it by '
            'hand before re-running.', missing;
    END IF;

    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c
     WHERE c.oid = 'public.strategy_backtests'::regclass;

    IF NOT rls_on THEN
        RAISE EXCEPTION
            '006 refuses to run: row level security is DISABLED on '
            'public.strategy_backtests. Every column this migration adds is '
            'per-user research evidence - the version tested, the dataset '
            'checksum, the dag hash, the final capital - so applying it to a '
            'table without row-level isolation would expose another user''s '
            'unpublished strategy research. Both base definitions enable RLS '
            'on this table, so this database is in an unexpected state. '
            'Apply backend_app/migrations/001_strategy_architecture.sql '
            '(ENABLE ROW LEVEL SECURITY plus the three owner policies) '
            'first. This file deliberately does not enable RLS itself, '
            'because that would be a change to an existing control this task '
            'must leave untouched.';
    END IF;

    SELECT count(*) INTO policies
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'strategy_backtests';

    IF policies = 0 THEN
        RAISE EXCEPTION
            '006 refuses to run: row level security is enabled on '
            'public.strategy_backtests but it has NO policies, so the table '
            'is unreachable to every non-superuser role and a backtest '
            'written here could not be read back as evidence. Apply '
            'backend_app/migrations/001_strategy_architecture.sql''s '
            'strategy_backtests policies first.';
    END IF;

    SELECT count(*) INTO indexes
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'strategy_backtests';

    SELECT count(*) INTO triggers
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.strategy_backtests'::regclass
       AND NOT t.tgisinternal;

    PERFORM set_config('aerora.sb_policies_before', policies::TEXT, true);
    PERFORM set_config('aerora.sb_indexes_before',  indexes::TEXT,  true);
    PERFORM set_config('aerora.sb_triggers_before', triggers::TEXT, true);

    RAISE NOTICE '006 preflight: public.strategy_backtests has RLS enabled, '
                 '% policies, % indexes and % user triggers. This file adds '
                 'no policy and no trigger, adds at most 2 indexes, and '
                 'section 1.6 verifies each of those three facts.',
                 policies, indexes, triggers;
END $$;

-- 1.1) The nine columns ------------------------------------------------
-- design.md "6. strategy_backtests has two divergent definitions", verbatim,
-- in the order it lists them.
--
--   version_id          The immutable Strategy_Version every Backtest_
--                       Condition of a Submission must share (Requirements
--                       3.3, 3.4). NULLABLE - see "WHY version_id IS ADDED
--                       NULLABLE" in the header. Present already under
--                       definition A, where it stays NOT NULL and keeps its
--                       foreign key; added here for definition B.
--   final_capital       The end-of-run capital (Requirement 3.4's persisted
--                       result set). Present under definition A; absent
--                       under definition B, which is why a completed run
--                       reconciled from production has no closing balance.
--   executed_bar_count  The number of bars the run actually executed over -
--                       len(ohlcv_data) at backtest_runtime.run_backtest,
--                       written by task 12.1 through
--                       backtest_service.update_backtest_results.
--                       Requirement 3.8. Absent from BOTH base definitions:
--                       this column exists nowhere before this file.
--   engine_version      \
--   schema_version       |  The four reproducibility columns
--   dag_hash             |  backtest_service.create_backtest already writes.
--   dataset_checksum    /   Present under definition B; absent under
--                       definition A, where writing them is the 42703 /
--                       PGRST204 condition Requirement 24.9 names.
--   completed_at        When the run finished (Requirement 3.2's non-null
--                       completed_at). Present under both definitions;
--                       listed here so that the nine are one group and a
--                       third, unrecorded base shape still ends up complete.
--   error_message       The failure text of a failed run (Requirement 3.2's
--                       "null or empty error_message"). Present under
--                       definition A; definition B spells its own failure
--                       column "error", which this file leaves exactly as it
--                       is - it is read by existing code and renaming or
--                       removing it is forbidden by Requirement 24.7.
--                       A database carrying both simply has both.
--
-- Every column is NULLABLE and carries NO DEFAULT. No existing row is
-- rewritten, no value is fabricated, and Requirement 3's validator is the
-- thing that decides what a NULL means for admission.
-- Idempotent: ADD COLUMN IF NOT EXISTS, so a re-run - and an application
-- against either base definition - skips each column already present.
ALTER TABLE public.strategy_backtests
    ADD COLUMN IF NOT EXISTS version_id          UUID,
    ADD COLUMN IF NOT EXISTS final_capital       NUMERIC(20, 8),
    ADD COLUMN IF NOT EXISTS executed_bar_count  INTEGER,
    ADD COLUMN IF NOT EXISTS engine_version      TEXT,
    ADD COLUMN IF NOT EXISTS schema_version      TEXT,
    ADD COLUMN IF NOT EXISTS dag_hash            TEXT,
    ADD COLUMN IF NOT EXISTS dataset_checksum    TEXT,
    ADD COLUMN IF NOT EXISTS completed_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS error_message       TEXT;

-- 1.2) Column shape assertion ------------------------------------------
-- Copied from 005b_signal_lifecycle_and_idempotency.sql section 1.2, because
-- ADD COLUMN IF NOT EXISTS is SILENT about a pre-existing column of the same
-- name and a different type. Each silence matters here:
--   * a text executed_bar_count would let section 1.3's >= 0 comparison
--     become a cast, would sort lexically ('9' > '137') and would let '' be
--     stored as "a count";
--   * a numeric-typed dag_hash or dataset_checksum could not hold a hex
--     digest, and Requirement 3.6's pairwise-different checksums would be
--     compared as numbers;
--   * a timestamp WITHOUT time zone completed_at silently reinterprets every
--     UTC instant as local time, which Requirement 3.2 reads as evidence and
--     Requirement 24.5 requires to be a real timestamp;
--   * a version_id of a type that cannot hold a UUID makes Requirement 3.3's
--     "all conditions reference the same version_id" an equality over
--     truncated text.
--
-- Each column is checked against the SET of types that are genuinely
-- equivalent for its readers rather than one spelling, because the two base
-- definitions really do disagree: definition A's identifiers are UUID and
-- definition B's are TEXT, and a version_id of either type is usable by
-- PostgREST and by the validator. Likewise 'character varying' is accepted
-- wherever 'text' is expected - a VARCHAR(n) column is what a hand-added
-- column most likely is - and 'double precision' alongside 'numeric' for
-- final_capital, which is a reported figure and not a ledger balance.
-- 'timestamp without time zone' is deliberately NOT accepted for
-- completed_at: that one is not equivalent, it is a silent hour shift.
-- ::TEXT on every information_schema identifier: those columns are the
-- sql_identifier / character_data domains, not text, and an explicit cast
-- keeps the comparison a plain text compare on every server version rather
-- than relying on an implicit operator.
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
                ('version_id',         ARRAY['uuid', 'text', 'character varying']),
                ('final_capital',      ARRAY['numeric', 'double precision']),
                ('executed_bar_count', ARRAY['integer', 'bigint', 'smallint']),
                ('engine_version',     ARRAY['text', 'character varying']),
                ('schema_version',     ARRAY['text', 'character varying']),
                ('dag_hash',           ARRAY['text', 'character varying']),
                ('dataset_checksum',   ARRAY['text', 'character varying']),
                ('completed_at',       ARRAY['timestamp with time zone']),
                ('error_message',      ARRAY['text', 'character varying'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'strategy_backtests'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.strategy_backtests has evidence column(s) of the wrong '
            'shape: %. A pre-existing column of that name was left as it '
            'was, because ADD COLUMN IF NOT EXISTS does not alter one, so '
            '"the column exists" would not have meant "the column is '
            'usable" (Requirements 3.4, 3.8, 24.9). Reconcile it by hand '
            'before re-running this migration.', problems;
    END IF;
END $$;

-- 1.3) chk_sb_executed_bar_count ---------------------------------------
-- Requirement 3.8, and Requirement 24.2's "check constraint enumerating the
-- permitted values". A stored bar count is either absent or a real count:
-- no negative value can be persisted. The bound is >= 0 and NOT >= 50 - see
-- "WHY THE CHECK IS >= 0 AND NOT >= 50" in the header. The IS NULL branch is
-- written explicitly rather than relying on the fact that a NULL CHECK
-- passes, so that the intent is stated in the constraint itself.
--
-- Guarded rather than bare: PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS
-- and a second bare run would raise 42710 and abort the file. No
-- remove-then-re-add, which would both break this file's additive-only rule
-- and open a window inside this transaction where the bound is unenforced.
-- Validated against existing rows on the first run: every pre-existing row
-- has executed_bar_count IS NULL (the column did not exist under either base
-- definition), which the constraint admits, so the ALTER cannot fail on
-- existing data.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname  = 'chk_sb_executed_bar_count'
           AND conrelid = 'public.strategy_backtests'::regclass
    ) THEN
        ALTER TABLE public.strategy_backtests
            ADD CONSTRAINT chk_sb_executed_bar_count
            CHECK (executed_bar_count IS NULL OR executed_bar_count >= 0);
        RAISE NOTICE 'Added chk_sb_executed_bar_count to '
                     'public.strategy_backtests (NULL or a non-negative '
                     'count; Requirement 3.8''s 50-bar minimum is an '
                     'admission rule in evidence_validator, not a storage '
                     'rule here).';
    ELSE
        RAISE NOTICE 'chk_sb_executed_bar_count already present on '
                     'public.strategy_backtests; left unchanged.';
    END IF;
END $$;

-- 1.4) Indexes ---------------------------------------------------------
-- Requirement 24.4. Both serve the Evidence_Validator's own reads, which are
-- the queries this specification adds to this table.
--
-- idx_sb_user_strategy_status: Requirement 3.1 - "each referencing a distinct
-- strategy_backtests row whose user_id equals the strategy owner and whose
-- strategy_id equals the submitted strategy" - combined with Requirement
-- 3.2's status = 'completed'. That is this exact three-column predicate, run
-- once per Backtest_Condition per submission, so it is a composite rather
-- than three separate single-column indexes. 001 indexes user_id, strategy_id
-- and status individually; neither base definition has the composite.
-- Column order is (user_id, strategy_id, status): the leading column is the
-- tenant, which makes the index usable by the owner-scoped policy's reads as
-- well as by the validator's.
CREATE INDEX IF NOT EXISTS idx_sb_user_strategy_status
    ON public.strategy_backtests (user_id, strategy_id, status);

-- idx_strategy_backtests_version_id: Requirement 3.3's "every referenced row
-- references the same version_id" reads by version. Definition A already
-- creates an index of exactly this name on exactly this column, so under
-- definition A this statement is a no-op and under definition B it creates
-- the index alongside the column added in section 1.1. THE NAME IS
-- DELIBERATELY THE ONE 001 USES rather than a new idx_sb_version_id: a new
-- name would leave definition A carrying two identical indexes on the same
-- column, each maintained on every write, for no gain.
CREATE INDEX IF NOT EXISTS idx_strategy_backtests_version_id
    ON public.strategy_backtests (version_id);

-- 1.5) Column comments -------------------------------------------------
-- Intent the schema cannot express, and in three cases a warning a reader
-- genuinely needs. COMMENT replaces, so this is idempotent.
COMMENT ON COLUMN public.strategy_backtests.version_id IS
    'The immutable strategy_versions row this backtest tested (Requirements '
    '3.3, 3.4). NULLABLE, and NULL means NOT RECORDED - never "matches". '
    'evidence_validator MUST treat a NULL version_id as a failed criterion '
    'rather than assuming a version. Left NOT NULL with its foreign key '
    'under the 001 definition, which already declared it; added nullable and '
    'without a foreign key where it was absent, because a NOT NULL column '
    'would have demanded a fabricated version for every existing row.';

COMMENT ON COLUMN public.strategy_backtests.final_capital IS
    'End-of-run capital, part of Requirement 3.4''s persisted result set and '
    'of the immutable evidence copy Requirement 3.9 takes. Written by '
    'backtest_service.update_backtest_results. NULL for a run that has not '
    'completed; never defaulted to the initial capital, which would report a '
    'flat result as a real one.';

COMMENT ON COLUMN public.strategy_backtests.executed_bar_count IS
    'Number of OHLCV bars the run actually executed over - len(ohlcv_data) '
    'at backtest_runtime.run_backtest, passed into '
    'backtest_service.update_backtest_results. Requirement 3.8 requires a '
    'NON-NULL count of at least 50 for a Backtest_Condition to be admitted '
    'to a marketplace Submission, and requires a null, absent or below-50 '
    'count to be REJECTED - so NULL here means "not recorded", never zero '
    'and never a pass. chk_sb_executed_bar_count bounds it at >= 0 only; the '
    '50-bar minimum is evidence_validator''s, so that an honest short '
    'exploratory run can still be recorded. Absent from both pre-006 '
    'definitions of this table, which is why no historical row has one and '
    'why none is backfilled: a count cannot be reconstructed without '
    're-running the backtest, and inventing one would manufacture the '
    'evidence Requirement 3 exists to make real.';

COMMENT ON COLUMN public.strategy_backtests.engine_version IS
    'Backtest engine version that produced this run, written by '
    'backtest_service.create_backtest. Part of Requirement 3.9''s immutable '
    'parameter copy. Absent under the 001 definition, where writing it was '
    'the 42703 / PGRST204 condition Requirement 24.9 names.';

COMMENT ON COLUMN public.strategy_backtests.schema_version IS
    'Strategy DAG schema version of the blueprint this run executed, written '
    'by backtest_service.create_backtest from the blueprint itself. '
    'Reproducibility evidence; not defaulted by this migration, so a row '
    'that did not record one reads as NULL rather than as a guess.';

COMMENT ON COLUMN public.strategy_backtests.dag_hash IS
    'Hash of the execution graph this run executed, written by '
    'backtest_service.create_backtest. Requirement 3.4 requires it non-null '
    'for every admitted Backtest_Condition: it is what makes "these three '
    'conditions tested the same strategy" checkable rather than asserted.';

COMMENT ON COLUMN public.strategy_backtests.dataset_checksum IS
    'Checksum of the exact price series this run consumed. Requirement 3.4 '
    'requires it non-null, and Requirement 3.6 requires the checksums across '
    'a Submission''s Backtest_Conditions to be PAIRWISE DIFFERENT - it is '
    'the evidence that three conditions are three tests and not one dataset '
    'submitted three times.';

COMMENT ON COLUMN public.strategy_backtests.completed_at IS
    'UTC instant the run finished. Requirement 3.2 requires a non-null value '
    'here for every admitted Backtest_Condition. TIMESTAMPTZ, and section '
    '1.2 refuses a timestamp WITHOUT time zone of this name, because that '
    'would reinterpret every recorded instant as local time silently.';

COMMENT ON COLUMN public.strategy_backtests.error_message IS
    'Failure text of a failed run. Requirement 3.2 admits a Backtest_'
    'Condition only when this is null or empty. The production-reconciliation '
    'definition of this table spells its own failure column "error"; that '
    'column is read by existing code and is left exactly as it is, so a '
    'database reconciled from that shape carries both and a reader that '
    'cares about failure text should consult whichever its writer populates.';

-- 1.6) Postflight - the objects exist, and nothing else moved ----------
-- Turns the header's promises into facts about the database. Re-counts the
-- policy, trigger and index inventory recorded in section 1.0: policies and
-- triggers must be unchanged, indexes must have grown by at most the two
-- this section creates (by 1 under the 001 definition, which already has
-- idx_strategy_backtests_version_id, and by 0 on a re-run).
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    policies_before INTEGER := current_setting('aerora.sb_policies_before')::INTEGER;
    indexes_before  INTEGER := current_setting('aerora.sb_indexes_before')::INTEGER;
    triggers_before INTEGER := current_setting('aerora.sb_triggers_before')::INTEGER;
    policies_now    INTEGER;
    indexes_now     INTEGER;
    triggers_now    INTEGER;
    missing         TEXT;
BEGIN
    -- (a) All nine columns present, whichever base definition this database
    --     started from.
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('version_id'), ('final_capital'), ('executed_bar_count'),
                   ('engine_version'), ('schema_version'), ('dag_hash'),
                   ('dataset_checksum'), ('completed_at'), ('error_message'))
             AS expected(column_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns c
                WHERE c.table_schema::TEXT = 'public'
                  AND c.table_name::TEXT   = 'strategy_backtests'
                  AND c.column_name::TEXT  = expected.column_name);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '006 postflight failed: public.strategy_backtests is '
                        'missing column(s) %. Requirements 3.4 and 3.8 need '
                        'all nine on the row.', missing;
    END IF;

    -- (b) The CHECK is present, and is a CHECK.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname  = 'chk_sb_executed_bar_count'
           AND conrelid = 'public.strategy_backtests'::regclass
           AND contype  = 'c'
    ) THEN
        RAISE EXCEPTION '006 postflight failed: chk_sb_executed_bar_count is '
                        'not present on public.strategy_backtests, so a '
                        'negative bar count could be stored as evidence '
                        '(Requirements 3.8, 24.2).';
    END IF;

    -- (c) Both indexes are present.
    SELECT string_agg(expected.index_name, ', ' ORDER BY expected.index_name)
      INTO missing
      FROM (VALUES ('idx_sb_user_strategy_status'),
                   ('idx_strategy_backtests_version_id'))
             AS expected(index_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_indexes p
                WHERE p.schemaname = 'public'
                  AND p.tablename  = 'strategy_backtests'
                  AND p.indexname::TEXT = expected.index_name);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '006 postflight failed: index(es) % were not created '
                        '(Requirement 24.4).', missing;
    END IF;

    -- (d) Nothing else moved.
    SELECT count(*) INTO policies_now
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'strategy_backtests';
    SELECT count(*) INTO indexes_now
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'strategy_backtests';
    SELECT count(*) INTO triggers_now
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.strategy_backtests'::regclass
       AND NOT t.tgisinternal;

    IF policies_now <> policies_before THEN
        RAISE EXCEPTION '006 postflight failed: the row-level security policy '
                        'count on public.strategy_backtests moved from % to '
                        '%. This file must not add, alter or remove a policy.',
                        policies_before, policies_now;
    END IF;

    IF triggers_now <> triggers_before THEN
        RAISE EXCEPTION '006 postflight failed: the user trigger count on '
                        'public.strategy_backtests moved from % to %. This '
                        'file must not add a trigger; the existing '
                        'update_strategy_backtests_updated_at already '
                        'maintains updated_at for the new columns.',
                        triggers_before, triggers_now;
    END IF;

    IF indexes_now > indexes_before + 2 THEN
        RAISE EXCEPTION '006 postflight failed: the index count on '
                        'public.strategy_backtests moved from % to %, more '
                        'than the 2 indexes this file creates.',
                        indexes_before, indexes_now;
    END IF;

    RAISE NOTICE '006 complete: public.strategy_backtests has version_id, '
                 'final_capital, executed_bar_count, engine_version, '
                 'schema_version, dag_hash, dataset_checksum, completed_at '
                 'and error_message, chk_sb_executed_bar_count, '
                 'idx_sb_user_strategy_status and '
                 'idx_strategy_backtests_version_id. Policies % (unchanged), '
                 'triggers % (unchanged), indexes % -> %. '
                 'executed_bar_count is NULL on every pre-existing row and '
                 'is NOT backfilled: a bar count cannot be reconstructed '
                 'without re-running the backtest.',
                 policies_now, triggers_now, indexes_before, indexes_now;
END $$;

COMMIT;

-- ==========================================================================
-- VERIFICATION QUERIES - run these after applying, the way 005a/005b/007 do
-- ==========================================================================
--
-- 1) The nine columns, their types and their nullability:
--
--    SELECT column_name, data_type, is_nullable
--      FROM information_schema.columns
--     WHERE table_schema = 'public'
--       AND table_name   = 'strategy_backtests'
--       AND column_name IN ('version_id','final_capital','executed_bar_count',
--                           'engine_version','schema_version','dag_hash',
--                           'dataset_checksum','completed_at','error_message')
--     ORDER BY column_name;
--
--    Expect 9 rows. is_nullable = 'YES' for all nine EXCEPT version_id under
--    the 001 definition, where it was already NOT NULL and is left so.
--
-- 2) The CHECK:
--
--    SELECT conname, pg_get_constraintdef(oid)
--      FROM pg_constraint
--     WHERE conrelid = 'public.strategy_backtests'::regclass
--       AND conname  = 'chk_sb_executed_bar_count';
--
--    Expect 1 row:
--    CHECK (executed_bar_count IS NULL OR executed_bar_count >= 0)
--
-- 3) The two indexes:
--
--    SELECT indexname, indexdef
--      FROM pg_indexes
--     WHERE schemaname = 'public'
--       AND tablename  = 'strategy_backtests'
--       AND indexname IN ('idx_sb_user_strategy_status',
--                         'idx_strategy_backtests_version_id')
--     ORDER BY indexname;
--
--    Expect 2 rows.
--
-- 4) Row-level security and the policies are as they were:
--
--    SELECT relrowsecurity FROM pg_class
--     WHERE oid = 'public.strategy_backtests'::regclass;      -- expect true
--    SELECT policyname, cmd FROM pg_policies
--     WHERE schemaname = 'public' AND tablename = 'strategy_backtests'
--     ORDER BY policyname;
--
-- 5) No row was rewritten - every pre-existing backtest still has no bar
--    count, and that is correct:
--
--    SELECT count(*) AS rows_total,
--           count(executed_bar_count) AS rows_with_a_bar_count
--      FROM public.strategy_backtests;
--
--    Immediately after applying this file, rows_with_a_bar_count = 0. It
--    grows only as new runs complete through
--    backtest_service.update_backtest_results.
--
-- ==========================================================================
-- END OF MIGRATION 006. NEXT IN DEPENDENCY ORDER:
--   007_marketplace_submissions.sql  (needs the columns added above)
-- ==========================================================================
