-- 016_strategy_backtests_version_label.sql  (backend_app/migrations, migration 016)
--
-- PURPOSE
--   Reconcile the type of ONE column against the two divergent declarations of
--   public.strategy_backtests, so that a backtest row can record the version
--   LABEL the application actually writes:
--
--     public.strategy_backtests.version  INTEGER DEFAULT 1  ->  VARCHAR(20)
--
--   001_strategy_architecture.sql line 125 declares this column
--   "version VARCHAR(20) NOT NULL" - in the same CREATE TABLE whose sibling
--   strategy_versions.version carries the comment 'e.g., "v1.0", "v1.1",
--   "v2.0"'. migrations/006_reconcile_production_database.sql line 449
--   redeclares the SAME column as "version INTEGER DEFAULT 1". Both use
--   CREATE TABLE IF NOT EXISTS, so whichever ran first in a given database
--   decided the shape and the other silently did nothing. PRODUCTION HAS 006's
--   SHAPE: integer, nullable, DEFAULT 1.
--
--   THE APPLICATION WRITES A LABEL. backend_app/backend/backtest_service.py
--   (create_backtest, the "version": version key of backtest_data) persists
--   whatever its caller names the version, and the caller -
--   backend_app/routers/strategy_operations.py line 1462 - computes
--
--       version_label = str(version_row.get("version") or "v1.0")
--
--   a string such as "v1.0" taken off strategy_versions.version, which is
--   itself VARCHAR(20). Against an integer column PostgreSQL therefore refuses
--   the insert outright:
--
--       {'code': '22P02',
--        'message': 'invalid input syntax for type integer: "v1.0"'}
--
--   That is not a degraded row or a wrong value written quietly - it is EVERY
--   BACKTEST INSERT FAILING. CloudWatch records it as
--   "ERROR:StrategyOperationsRouter:Error executing backtest for strategy
--   f83975d7-... : {'code': '22P02', ...}". Until this file is applied, no
--   backtest can be recorded in an environment that took 006's shape.
--
-- WHY THE TYPE IS WRONG AND THE VALUE IS RIGHT
--   The obvious alternative fix - coerce the label to an integer at the call
--   site - was rejected, and the schema is what disagrees with the rest of the
--   system. Every other version column in this repository, and in production,
--   is text:
--
--     strategies.current_version       VARCHAR(20) DEFAULT 'v1.0'
--                                      (001 line 283, 003 line 37)
--     strategy_versions.version        VARCHAR(20) NOT NULL   -- 'e.g., "v1.0"'
--                                      (001 line 25; UNIQUE(strategy_id, version))
--     strategy_deployments.version     VARCHAR(20) NOT NULL   (001 line 78)
--     signals.strategy_version         VARCHAR(20) NOT NULL   (002 line 18)
--     research_reports.version         VARCHAR(20) NOT NULL   (001 line 207)
--
--   strategy_backtests.version is the ONLY integer among them, so it is the
--   outlier rather than the standard. And the label is not reducible to an
--   integer: "v1.0" has no integer spelling, int("v1.0") raises, and
--   int(...)-ing the numeric part would map "v1.0", "v1.1" and "v1.9" onto the
--   single value 1 - silently recording three different immutable versions as
--   the same one, which is the defect the hardcoded 1 in create_backtest
--   already caused once (see that method's comment).
--
--   The correct value to store is the label the immutable version carries, and
--   the correct type for it is the type every other copy of that label already
--   has.
--
-- WHY THIS FILE IS NOT ADDITIVE-ONLY, AND WHY THAT IS STILL SAFE
--   Every other migration in this directory promises "additive only" and means
--   it: no ALTER COLUMN, no DROP, no rewritten row. THIS FILE BREAKS THAT
--   PATTERN DELIBERATELY - it is the first here to change an existing column's
--   type - so the justification is stated plainly rather than assumed:
--
--     * The column as declared in production is UNUSABLE, not merely
--       imperfect. It rejects 100% of the writes the application makes to it.
--       An additive migration cannot repair a type; a second column would
--       leave two spellings of one fact and require every reader to know which
--       is real.
--     * public.strategy_backtests currently holds ZERO ROWS in production, so
--       there is no history to migrate, no value to reinterpret and nothing
--       that can be lost. Section 4 re-counts the rows and fails if the count
--       moved.
--     * The cast is EXPLICIT and TOTAL. USING version::text is defined for
--       every integer (and for every value a text-ish column could already
--       hold), so the statement is safe even if rows appear between now and the
--       moment an operator applies this file - which is exactly why the cast is
--       spelled out rather than left to PostgreSQL to infer. A bare
--       ALTER ... TYPE VARCHAR(20) with no USING clause fails with 42804 on an
--       integer column; this one does not.
--     * NOTHING ELSE CHANGES. No column is added or dropped, no row is
--       inserted, updated or deleted, no policy is created, altered or
--       removed, RLS is neither enabled nor disabled, no trigger, no GRANT, no
--       REVOKE. Sections 0 and 4 record and re-check the policy, trigger and
--       index inventory and the row count, so "nothing else changed" is
--       verified rather than promised.
--
-- WHY THE DEFAULT IS DROPPED, AND WHY IT IS DROPPED FIRST
--   TWO independent reasons, and the ordering is load-bearing:
--
--   1. CORRECTNESS. DEFAULT 1 is a lie in a label column. Carried through the
--      cast it would become DEFAULT '1' - a version label that names no
--      version, matches no strategy_versions.version row and reads as though
--      the run had been made against something real. The caller ALWAYS supplies
--      the label (strategy_operations.py line 1462 falls back to "v1.0" rather
--      than omitting it, and create_backtest always includes the key), so the
--      default has no legitimate user: its only remaining effect would be to
--      turn a future writer's omission into a plausible-looking fabrication
--      instead of an error somebody notices.
--
--   2. MECHANICS. The DROP DEFAULT must happen BEFORE the type change, not
--      after. PostgreSQL re-coerces a surviving DEFAULT expression into the
--      column's new type, and there is no assignment cast from integer to
--      character varying, so an ALTER ... TYPE issued while DEFAULT 1 is still
--      attached aborts the whole transaction with
--
--          ERROR: default for column "version" cannot be cast automatically
--                 to type character varying
--
--      Section 1 therefore runs before section 2. Reversing the two sections
--      makes this file fail on precisely the databases it exists to repair.
--
--   NOTE what is NOT done here: this file does not SET NOT NULL. Under 001's
--   shape the column is already NOT NULL and ALTER ... TYPE preserves that, so
--   that constraint survives untouched. Under 006's shape the column is
--   nullable, and adding NOT NULL would be a NEW control this bug does not
--   call for - the failure being fixed is a rejected insert, not an accepted
--   NULL - and would be one more thing to reason about on a table whose two
--   base definitions already disagree. The nullability of the column is left
--   exactly as this file found it, and section 4 asserts that it did not move.
--
-- WHY VARCHAR(20) AND NOT TEXT
--   Because 001 already says VARCHAR(20), and this file's job is to reconcile
--   006 against 001 rather than to introduce a third opinion. Twenty characters
--   is what every sibling label column allows ("v1.0" is four), and matching
--   strategy_versions.version exactly means a label can be copied between the
--   two tables without a truncation risk that depends on which table it came
--   from. TEXT would be marginally more permissive and would make
--   strategy_backtests.version the outlier again, in the other direction.
--
-- WHY A SEPARATE FILE RATHER THAN AN EDIT TO 001 OR 006
--   The reason every file from 004b onwards states: migrations here are applied
--   BY HAND, per file, and NOTHING RECORDS WHICH FILES AN ENVIRONMENT HAS RUN -
--   there is no migration table and .github/workflows/03-deploy.yml has no
--   migration step. Editing a file an operator may already have applied leaves
--   no signal that it changed, so the new statements would simply never run. A
--   new filename is the signal. 001_strategy_architecture.sql and
--   migrations/006_reconcile_production_database.sql are both left
--   byte-identical, divergence and all: they are the historical record of how
--   the two shapes came to exist.
--
-- LOCKING AND COST
--   ALTER TABLE ... ALTER COLUMN ... TYPE takes ACCESS EXCLUSIVE on
--   public.strategy_backtests and REWRITES the table and every index on it. On
--   the production table - zero rows - that is instantaneous. On a populated
--   table it is not, and it blocks every reader and writer for the duration, so
--   AN OPERATOR APPLYING THIS TO A LARGE strategy_backtests SHOULD USE A
--   MAINTENANCE WINDOW. Section 0 reports the row count it is about to rewrite
--   before anything is altered.
--
-- SAFETY
--   * No row is inserted, updated or deleted. No column is added, dropped or
--     renamed. No constraint, index, policy, trigger, GRANT or REVOKE is
--     created, altered or removed. RLS is neither enabled nor disabled.
--   * Fully idempotent. Both mutating statements sit behind
--     information_schema guards: section 1 drops the default only while one
--     exists, section 2 changes the type only while it is not already
--     VARCHAR(20) - so a re-run neither rewrites the table a second time nor
--     raises. Section 3's COMMENT replaces. Sections 0 and 4 read catalogues.
--   * One transaction. Either the default is gone AND the type is VARCHAR(20)
--     AND the comment is in place, or none of the three happened. No session
--     ever sees a version column mid-conversion.
--   * Applying this file changes no value that is already stored: an integer 1
--     becomes the string '1', which is the same fact in the new type. It does
--     not invent labels for existing rows, because inventing one would mean
--     claiming a version that row was never run against.
--
-- APPLICATION
--   NOT applied automatically. Apply this file explicitly against the target
--   database, then run the verification queries at the bottom, the way 005a and
--   007-015 were applied. DEPENDS ON public.strategy_backtests, created by
--   001_strategy_architecture.sql (section 3) or by
--   migrations/006_reconcile_production_database.sql (PART 7).
--
--   Until it is applied, an environment carrying 006's shape rejects every
--   backtest insert with 22P02 and the router logs the error it logs today. The
--   application code needs no change and gets none: "version": version already
--   sends the right value, and the only thing wrong is the type waiting to
--   receive it.

BEGIN;

-- 0) Preflight -----------------------------------------------------------
-- Read-only assertions, plus the baselines section 4 re-checks. Fails with a
-- readable message naming the missing or misconfigured object instead of a bare
-- 42P01 / 42804 from inside the ALTER.
--
-- No policy, index, trigger or constraint NAME is hard-coded, so an environment
-- that renamed one cannot false-alarm section 4; every baseline is taken inside
-- this one transaction, which makes it comparable by construction.
--
-- Idempotent: reads catalogues and writes nothing but settings local to this
-- transaction.
DO $$
DECLARE
    rls_on        BOOLEAN;
    policy_count  INTEGER;
    trigger_count INTEGER;
    index_names   TEXT;
    col_type      TEXT;
    col_length    INTEGER;
    col_null      TEXT;
    col_default   TEXT;
    row_count     BIGINT;
    dependents    TEXT;
BEGIN
    IF to_regclass('public.strategy_backtests') IS NULL THEN
        RAISE EXCEPTION
            '016 precondition failed: table public.strategy_backtests does not '
            'exist. Apply backend_app/migrations/001_strategy_architecture.sql '
            '(section 3) or migrations/006_reconcile_production_database.sql '
            '(PART 7) first. This file reconciles the version column of those '
            'two definitions; it creates no table, because doing so would add '
            'a THIRD divergent shape.';
    END IF;

    SELECT c.data_type::TEXT,
           c.character_maximum_length,
           c.is_nullable::TEXT,
           c.column_default
      INTO col_type, col_length, col_null, col_default
      FROM information_schema.columns c
     WHERE c.table_schema::TEXT = 'public'
       AND c.table_name::TEXT   = 'strategy_backtests'
       AND c.column_name::TEXT  = 'version';

    IF col_type IS NULL THEN
        RAISE EXCEPTION
            '016 precondition failed: public.strategy_backtests has no version '
            'column at all. BOTH base definitions declare one (001 line 125 as '
            'VARCHAR(20) NOT NULL, migrations/006_reconcile_production_'
            'database.sql line 449 as INTEGER DEFAULT 1), so this table is in '
            'a shape neither file produced. Reconcile it by hand before '
            're-running: this file changes an existing column''s type and will '
            'not add a column, because a version column added here would have '
            'no relationship to the label the application has been trying to '
            'write.';
    END IF;

    -- The version label is part of a user's own research record, and this file
    -- rewrites the table that holds it under ACCESS EXCLUSIVE. Both base
    -- definitions enable RLS on this table (001's "RLS Policies for
    -- strategy_backtests"; the production reconciliation's PART 7), so a
    -- database missing it is in an unexpected state and the right response is
    -- to stop rather than to rewrite it. This file deliberately does NOT enable
    -- RLS itself - that would be a change to an existing control, which is
    -- outside a type reconciliation's business.
    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c
     WHERE c.oid = 'public.strategy_backtests'::regclass;

    IF NOT rls_on THEN
        RAISE EXCEPTION
            '016 refuses to run: row level security is DISABLED on '
            'public.strategy_backtests. This file rewrites that table to fix '
            'the type of a column holding per-user research evidence, and both '
            'base definitions enable RLS on it, so this database is in an '
            'unexpected state. Apply backend_app/migrations/'
            '001_strategy_architecture.sql''s ENABLE ROW LEVEL SECURITY plus '
            'its strategy_backtests policies first. This file deliberately '
            'does not enable RLS itself.';
    END IF;

    SELECT count(*) INTO policy_count
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'strategy_backtests';

    IF policy_count = 0 THEN
        RAISE EXCEPTION
            '016 refuses to run: row level security is enabled on '
            'public.strategy_backtests but the table has NO policies, so it is '
            'unreachable to every non-superuser role and a backtest written '
            'here could not be read back. Apply '
            'backend_app/migrations/001_strategy_architecture.sql''s '
            'strategy_backtests policies first.';
    END IF;

    -- A view or materialised view selecting this column would make the type
    -- change fail with a bare "cannot alter type of a column used by a view or
    -- rule". Name the dependents instead, so an operator knows what to drop and
    -- recreate rather than reading that error and guessing.
    SELECT string_agg(DISTINCT dependent.relname::TEXT, ', ')
      INTO dependents
      FROM pg_depend d
      JOIN pg_rewrite r      ON r.oid = d.objid
      JOIN pg_class dependent ON dependent.oid = r.ev_class
      JOIN pg_attribute a    ON a.attrelid = d.refobjid
                           AND a.attnum   = d.refobjsubid
     WHERE d.classid    = 'pg_rewrite'::regclass
       AND d.refclassid = 'pg_class'::regclass
       AND d.refobjid   = 'public.strategy_backtests'::regclass
       AND a.attname    = 'version'
       AND dependent.relkind IN ('v', 'm')
       AND dependent.oid <> 'public.strategy_backtests'::regclass;

    IF dependents IS NOT NULL THEN
        RAISE EXCEPTION
            '016 refuses to run: the relation(s) % select '
            'public.strategy_backtests.version, and PostgreSQL will not change '
            'the type of a column a view or rule depends on. No migration in '
            'this repository creates such a view, so this one was created '
            'outside them: drop it, apply this file, and recreate it against '
            'the VARCHAR(20) column.', dependents;
    END IF;

    SELECT count(*) INTO row_count FROM public.strategy_backtests;

    SELECT count(*) INTO trigger_count
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.strategy_backtests'::regclass
       AND NOT t.tgisinternal;

    -- indexname is of type "name"; cast to text explicitly rather than relying
    -- on an implicit name -> text coercion inside string_agg. Index NAMES are
    -- compared, not a count: ALTER ... TYPE rebuilds every index on the table,
    -- and the names must come back identical.
    SELECT coalesce(string_agg(indexname::TEXT, ',' ORDER BY indexname), '')
      INTO index_names
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'strategy_backtests';

    PERFORM set_config('aerora.sbv_policies_before', policy_count::TEXT,  true);
    PERFORM set_config('aerora.sbv_triggers_before', trigger_count::TEXT, true);
    PERFORM set_config('aerora.sbv_indexes_before',  index_names,         true);
    PERFORM set_config('aerora.sbv_rows_before',     row_count::TEXT,     true);
    PERFORM set_config('aerora.sbv_nullable_before', col_null,            true);

    RAISE NOTICE '016 preflight: public.strategy_backtests.version is '
                 'currently % (max length %, nullable %, default %). The table '
                 'holds % row(s) and will be rewritten under ACCESS EXCLUSIVE; '
                 'it has RLS enabled with % policies, % non-internal '
                 'trigger(s) and % index(es), none of which this file changes.',
                 col_type,
                 coalesce(col_length::TEXT, 'n/a'),
                 col_null,
                 coalesce(col_default, 'none'),
                 row_count,
                 policy_count,
                 trigger_count,
                 coalesce(array_length(string_to_array(nullif(index_names, ''), ','), 1), 0);
END $$;

-- 1) Drop the misleading DEFAULT -----------------------------------------
-- DEFAULT 1 (migrations/006_reconcile_production_database.sql line 449) is a
-- label that names no version. It must go for its own sake, and it must go
-- BEFORE section 2: PostgreSQL re-coerces a surviving default into the column's
-- new type, and integer -> character varying has no assignment cast, so an
-- ALTER ... TYPE with the default still attached aborts with "default for
-- column "version" cannot be cast automatically to type character varying".
-- See "WHY THE DEFAULT IS DROPPED, AND WHY IT IS DROPPED FIRST" in the header.
--
-- Idempotent: guarded by an information_schema check, so a re-run (no default
-- present) skips the ALTER entirely and says so.
DO $$
DECLARE
    col_default TEXT;
BEGIN
    SELECT c.column_default INTO col_default
      FROM information_schema.columns c
     WHERE c.table_schema::TEXT = 'public'
       AND c.table_name::TEXT   = 'strategy_backtests'
       AND c.column_name::TEXT  = 'version';

    IF col_default IS NOT NULL THEN
        ALTER TABLE public.strategy_backtests
            ALTER COLUMN version DROP DEFAULT;
        RAISE NOTICE 'Dropped DEFAULT % from public.strategy_backtests.version '
                     '(a default label would record a version nothing ran '
                     'against; the caller always supplies the real one).',
                     col_default;
    ELSE
        RAISE NOTICE 'public.strategy_backtests.version already has no '
                     'DEFAULT; left unchanged.';
    END IF;
END $$;

-- 2) The type change -----------------------------------------------------
-- integer -> VARCHAR(20), with the cast written out: USING version::text is
-- defined for every integer, so this is safe even if rows appear between the
-- reading of this file and its application. Without the USING clause PostgreSQL
-- refuses an integer -> varchar change with 42804 cannot_coerce.
--
-- This is the statement that repairs the 22P02 on every backtest insert.
--
-- Idempotent: guarded on the CURRENT type, so a database already carrying 001's
-- VARCHAR(20) - or a second run of this file - skips the ALTER and does NOT
-- rewrite the table again. The guard is what keeps a re-run cheap as well as
-- correct.
DO $$
DECLARE
    col_type   TEXT;
    col_length INTEGER;
    too_long   BIGINT;
    longest    INTEGER;
BEGIN
    SELECT c.data_type::TEXT, c.character_maximum_length
      INTO col_type, col_length
      FROM information_schema.columns c
     WHERE c.table_schema::TEXT = 'public'
       AND c.table_name::TEXT   = 'strategy_backtests'
       AND c.column_name::TEXT  = 'version';

    IF col_type = 'character varying' AND col_length = 20 THEN
        RAISE NOTICE 'public.strategy_backtests.version is already '
                     'VARCHAR(20) (001''s shape); the table is not rewritten '
                     'on this run.';
        RETURN;
    END IF;

    -- The cast cannot lose an integer (11 characters at most), but it CAN
    -- truncate a text-ish value longer than 20, which would abort with a bare
    -- 22001 string_data_right_truncation. Say which value and how long instead.
    -- Costs one sequential scan of a table that holds zero rows in production.
    SELECT count(*), coalesce(max(length(version::TEXT)), 0)
      INTO too_long, longest
      FROM public.strategy_backtests
     WHERE length(version::TEXT) > 20;

    IF too_long > 0 THEN
        RAISE EXCEPTION
            '016 refuses to proceed: % row(s) of public.strategy_backtests '
            'carry a version whose text form is longer than 20 characters (the '
            'longest is %). Converting to VARCHAR(20) would truncate them, so '
            'this file stops instead of silently shortening a recorded version '
            'label. Widen the target length in a NEW migration, or correct '
            'those rows by hand, then re-run.', too_long, longest;
    END IF;

    ALTER TABLE public.strategy_backtests
        ALTER COLUMN version TYPE VARCHAR(20) USING version::text;

    RAISE NOTICE 'Converted public.strategy_backtests.version from % to '
                 'VARCHAR(20) (USING version::text). The column can now hold '
                 'the label the application writes - "v1.0" - which it '
                 'rejected with 22P02 on every insert before this ran.',
                 col_type;
END $$;

-- 3) Column comment ------------------------------------------------------
-- Intent the schema cannot express, recorded where \d+ and every schema browser
-- will show it - including the fact that this column had the wrong type in
-- production, so the next person to read the two divergent CREATE TABLE
-- statements knows which one won and which one is authoritative now.
-- COMMENT is naturally idempotent: it replaces.
COMMENT ON COLUMN public.strategy_backtests.version IS
    'The version LABEL this backtest ran against - a string such as "v1.0", copied from '
    'strategy_versions.version (itself VARCHAR(20)) by '
    'backend_app/routers/strategy_operations.py and persisted by '
    'backend_app/backend/backtest_service.py::create_backtest. RECONCILED BY MIGRATION 016: '
    '001_strategy_architecture.sql declared this column VARCHAR(20) NOT NULL, '
    'migrations/006_reconcile_production_database.sql redeclared it INTEGER DEFAULT 1, both '
    'with CREATE TABLE IF NOT EXISTS, and production took the integer - so every backtest '
    'insert was refused with 22P02 invalid input syntax for type integer: "v1.0". Every other '
    'version column in this schema (strategies.current_version, strategy_versions.version, '
    'strategy_deployments.version, signals.strategy_version) is VARCHAR(20); this one was the '
    'only integer. Deliberately has NO DEFAULT: a default of ''1'' would name no version, '
    'match no strategy_versions row and read as though the run had been made against '
    'something real, and the caller always supplies the label. Not an ordinal and not a '
    'counter - do not parse it as one: "v1.0", "v1.1" and "v1.9" are three different '
    'immutable versions that any integer reading collapses onto 1, which is the defect the '
    'hardcoded 1 in create_backtest caused before it was removed. version_id is the '
    'foreign key that IDENTIFIES the version; this column is the human-readable label of the '
    'same version.';

-- 4) Postflight - the shape is right, and nothing else moved -------------
-- Turns the header's promises into facts about the database: the column is
-- VARCHAR(20) with no default, its nullability is exactly what section 0 found,
-- no row was gained or lost by the rewrite, and the policy, trigger and index
-- inventory recorded in section 0 is unchanged (ALTER ... TYPE rebuilds every
-- index, so the index NAMES are compared, not counted).
--
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    policies_before INTEGER := current_setting('aerora.sbv_policies_before')::INTEGER;
    triggers_before INTEGER := current_setting('aerora.sbv_triggers_before')::INTEGER;
    indexes_before  TEXT    := current_setting('aerora.sbv_indexes_before');
    rows_before     BIGINT  := current_setting('aerora.sbv_rows_before')::BIGINT;
    nullable_before TEXT    := current_setting('aerora.sbv_nullable_before');
    policies_now    INTEGER;
    triggers_now    INTEGER;
    indexes_now     TEXT;
    rows_now        BIGINT;
    rls_on          BOOLEAN;
    col_type        TEXT;
    col_length      INTEGER;
    col_null        TEXT;
    col_default     TEXT;
BEGIN
    SELECT c.data_type::TEXT,
           c.character_maximum_length,
           c.is_nullable::TEXT,
           c.column_default
      INTO col_type, col_length, col_null, col_default
      FROM information_schema.columns c
     WHERE c.table_schema::TEXT = 'public'
       AND c.table_name::TEXT   = 'strategy_backtests'
       AND c.column_name::TEXT  = 'version';

    IF col_type <> 'character varying' OR col_length <> 20 THEN
        RAISE EXCEPTION
            '016 postflight failed: public.strategy_backtests.version is % '
            '(max length %), not VARCHAR(20). The application writes a label '
            'like "v1.0" to this column, so any other type reinstates the '
            '22P02 this file exists to end.',
            col_type, coalesce(col_length::TEXT, 'n/a');
    END IF;

    IF col_default IS NOT NULL THEN
        RAISE EXCEPTION
            '016 postflight failed: public.strategy_backtests.version still '
            'carries the DEFAULT "%". A default label names no version and '
            'would turn a writer''s omission into a plausible-looking '
            'fabrication.', col_default;
    END IF;

    -- ALTER ... TYPE preserves nullability. Under 001's shape the column is
    -- NOT NULL and stays so; under 006's it is nullable and stays so. Either
    -- way this file must not have moved it.
    IF col_null <> nullable_before THEN
        RAISE EXCEPTION
            '016 postflight failed: the nullability of '
            'public.strategy_backtests.version moved from is_nullable=% to %. '
            'This file issues no SET/DROP NOT NULL and ALTER ... TYPE '
            'preserves the constraint, so this should be unreachable.',
            nullable_before, col_null;
    END IF;

    SELECT count(*) INTO rows_now FROM public.strategy_backtests;

    IF rows_now <> rows_before THEN
        RAISE EXCEPTION
            '016 postflight failed: public.strategy_backtests held % row(s) '
            'before the conversion and holds % now. This file contains no '
            'INSERT, UPDATE, DELETE or TRUNCATE - a rewrite preserves every '
            'row - so this should be unreachable.', rows_before, rows_now;
    END IF;

    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c
     WHERE c.oid = 'public.strategy_backtests'::regclass;

    IF NOT rls_on THEN
        RAISE EXCEPTION
            '016 postflight failed: row level security is no longer enabled on '
            'public.strategy_backtests. This file issues no ENABLE/DISABLE ROW '
            'LEVEL SECURITY.';
    END IF;

    SELECT count(*) INTO policies_now
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'strategy_backtests';

    IF policies_now <> policies_before THEN
        RAISE EXCEPTION
            '016 postflight failed: public.strategy_backtests had % policies '
            'before this migration and has % now. This file issues no '
            'CREATE/ALTER/DROP POLICY.', policies_before, policies_now;
    END IF;

    SELECT count(*) INTO triggers_now
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.strategy_backtests'::regclass
       AND NOT t.tgisinternal;

    IF triggers_now <> triggers_before THEN
        RAISE EXCEPTION
            '016 postflight failed: public.strategy_backtests had % '
            'non-internal trigger(s) before this migration and has % now. '
            'This file creates and drops no trigger.',
            triggers_before, triggers_now;
    END IF;

    SELECT coalesce(string_agg(indexname::TEXT, ',' ORDER BY indexname), '')
      INTO indexes_now
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'strategy_backtests';

    IF indexes_now <> indexes_before THEN
        RAISE EXCEPTION
            '016 postflight failed: the index inventory on '
            'public.strategy_backtests moved from [%] to [%]. ALTER ... TYPE '
            'REBUILDS the indexes on a table but must not add, drop or rename '
            'one, and this file creates none of its own.',
            indexes_before, indexes_now;
    END IF;

    RAISE NOTICE '016 complete: public.strategy_backtests.version is '
                 'VARCHAR(20) with no default and is_nullable=%, holding the '
                 'same % row(s) as before. Policies, triggers and indexes are '
                 'unchanged. A backtest insert carrying "version": "v1.0" now '
                 'succeeds where it was refused with 22P02 invalid input '
                 'syntax for type integer.', col_null, rows_now;
END $$;

COMMIT;

-- ==========================================================================
-- VERIFICATION - run these AFTER applying this file
-- ==========================================================================
--
-- 1. The column has the reconciled shape. Expect one row:
--    version | character varying | 20 | (null default)
--
--   SELECT column_name, data_type, character_maximum_length,
--          is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'public' AND table_name = 'strategy_backtests'
--     AND column_name = 'version';
--
-- 2. The label the application writes is now accepted. Expect the string back,
--    with NO 22P02. This casts a literal and writes nothing - it is the same
--    coercion the failing INSERT performed.
--
--   SELECT 'v1.0'::VARCHAR(20) AS accepted_label;
--
--   And the pre-fix behaviour, for contrast - expect
--   22P02 invalid input syntax for type integer:
--
--   -- SELECT 'v1.0'::INTEGER;
--
-- 3. No stored value was lost or reinterpreted. On a table that was empty
--    before the conversion (production) expect zero rows; otherwise expect
--    every row that held an integer to now hold its text spelling and nothing
--    else to have changed.
--
--   SELECT count(*) AS rows, count(version) AS with_version,
--          min(length(version)) AS shortest, max(length(version)) AS longest
--   FROM public.strategy_backtests;
--
--   SELECT version, count(*) FROM public.strategy_backtests
--   GROUP BY version ORDER BY count(*) DESC LIMIT 20;
--
-- 4. Nothing else moved. Expect the same policies, triggers and index names as
--    before the migration, and rowsecurity = true. RLS is row-scoped, so the
--    existing owner policies cover this column exactly as they did before.
--
--   SELECT policyname, cmd, roles, qual, with_check FROM pg_policies
--   WHERE schemaname = 'public' AND tablename = 'strategy_backtests'
--   ORDER BY policyname;
--
--   SELECT tgname FROM pg_trigger
--   WHERE tgrelid = 'public.strategy_backtests'::regclass AND NOT tgisinternal
--   ORDER BY tgname;
--
--   SELECT indexname, indexdef FROM pg_indexes
--   WHERE schemaname = 'public' AND tablename = 'strategy_backtests'
--   ORDER BY indexname;
--
--   SELECT relrowsecurity, relforcerowsecurity
--   FROM pg_class WHERE oid = 'public.strategy_backtests'::regclass;
--
-- 5. End to end, the condition CloudWatch reported is gone: run a backtest
--    (POST /api/strategies/{id}/backtest) and expect a strategy_backtests row
--    whose version reads as a label rather than an error in the log. Expect the
--    newest row's version to match the strategy's own current_version.
--
--   SELECT b.id, b.version, s.current_version, b.status, b.created_at
--   FROM public.strategy_backtests b
--   JOIN public.strategies s ON s.id::text = b.strategy_id::text
--   ORDER BY b.created_at DESC LIMIT 5;
--
-- 6. Idempotency. Re-running this whole file must report no error, must leave
--    the results of 1, 3 and 4 unchanged, and must NOT rewrite the table a
--    second time - section 2 says so in its NOTICE ("already VARCHAR(20) ...
--    the table is not rewritten on this run").
