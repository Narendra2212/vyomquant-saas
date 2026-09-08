-- 010_signal_environment.sql  (backend_app/migrations, migration 010)
--
-- PURPOSE
--   Give public.signals the two columns the Signal_Trace extension needs so a
--   signal row can name the Execution_Environment it was produced under and,
--   for a paper signal, the Paper_Session it belongs to:
--
--     1. signals.environment TEXT - the Execution_Environment of Requirement
--        13.1, one of exactly BACKTEST, PAPER, LIVE, enforced by
--        chk_signals_environment rather than by convention. Requirement 23.1.
--     2. signals.paper_session_id UUID - the Paper_Session a PAPER signal was
--        generated inside, REFERENCES public.paper_sessions(id) ON DELETE SET
--        NULL, so deleting a session never removes a signal row. Requirements
--        23.2, 23.7, 24.4.
--
--   Source of the definition: design.md § "Signal Trace extension", statement
--   for statement, schema-qualified with public. and following
--   005b_signal_lifecycle_and_idempotency.sql - the same RLS precondition
--   check, the same column-shape assertion, the same pg_constraint-guarded
--   ADD CONSTRAINT, one transaction, no DROP, no row deletion.
--
--   The three environment values are NOT invented here. They are transcribed
--   from ExecutionEnvironment in backend_app/backend/execution_environment.py
--   (task 13.1), in EXECUTION_ENVIRONMENTS' own order:
--
--     BACKTEST, PAPER, LIVE
--
--   That module's docstring names this file's chk_signals_environment as one
--   of the consumers of that vocabulary ("Everything that needs the
--   vocabulary - the chk_signals_environment check constraint ... - derives
--   it from here, so the constraint and the code cannot drift"). If a fourth
--   value is ever added there, this constraint is what must be widened in a
--   NEW migration file - never edited in place here, for the reason under
--   APPLICATION below.
--
-- WHY environment IS NOT NULL AND order_lifecycle_state (005b) IS NOT
--   READ THIS BEFORE ASSUMING THE TWO COLUMNS ARE ADDED THE SAME WAY:
--
--   005b added order_lifecycle_state NULLABLE and left it so, because its
--   back-fill (mapping legacy signals.status through SIGNALS_STATUS_MAP) is a
--   JUDGEMENT that belongs in application code, and a fabricated
--   Order_Lifecycle_State on financial history would be a lie. Its header
--   records the hazard it accepted: "A CHECK CONSTRAINT THAT EVALUATES TO NULL
--   PASSES", leaving Requirement 16.1's "exactly 9 values" nominal until that
--   later back-fill runs.
--
--   Requirement 23.1 admits no such gap, and this file closes it, because the
--   back-fill value here is NOT a judgement. Every signals row that exists
--   before this migration was produced by the live path - the only path that
--   wrote signals before the Paper_Session and the backtest recorder existed -
--   so 'LIVE' is a FACT about those rows, not a default asserted for
--   convenience. So this file:
--
--     * back-fills every pre-existing row to 'LIVE'
--       (UPDATE ... WHERE environment IS NULL, section 1.3a),
--     * sets DEFAULT 'LIVE' so a writer that does not name the environment
--       still records a true value for the only path that existed before
--       (section 1.3b),
--     * sets NOT NULL so the three-value vocabulary is TOTAL over
--       public.signals from the moment this file commits (section 1.3c),
--
--   all in the SAME transaction as the ADD COLUMN and the CHECK. There is no
--   window in which a session can see a signals row carrying environment
--   without chk_signals_environment, and no window in which environment is
--   both present and NULL. This is the deliberate departure from 005b that
--   design.md § "Signal Trace extension" calls out by name.
--
--   paper_session_id stays NULLABLE with NO default and NO back-fill: a
--   LIVE or BACKTEST signal has no Paper_Session, and a NULL there is the true
--   value, not a missing one. Only a PAPER signal carries a non-NULL
--   paper_session_id (Requirement 23.2), written by the recorder at generation
--   time; this file invents none.
--
-- WHY SET NOT NULL IS AN OPERATOR-VISIBLE COST, AND WHERE THIS FILE BELONGS
--   ALTER TABLE ... ALTER COLUMN environment SET NOT NULL takes an
--   ACCESS EXCLUSIVE lock on public.signals and SCANS THE TABLE to prove no
--   row violates it (the back-fill in the same transaction is what guarantees
--   none does). On a large signals table that scan is not instant and that
--   lock blocks every reader and writer for its duration, so THIS FILE BELONGS
--   IN A MAINTENANCE WINDOW, applied the way 007/008/009 were: by hand, per
--   file, with the verification queries at the bottom run afterwards. It is
--   NOT applied automatically. RE-RUNNING IT IS A NO-OP - see SAFETY.
--
-- WHY THIS FILE REFUSES TO RUN WITHOUT RLS ON public.signals
--   Exactly the refusal 005b makes, for the same reason and by the same means.
--   environment and paper_session_id sit on rows that record a user's real
--   trading decisions across three environments, and paper_session_id links a
--   signal to a Paper_Session that is itself owner-scoped. Adding them to a
--   table whose row-level isolation is off would let any authenticated caller
--   read another user's signals - and learn which of their signals were paper
--   versus live, and which paper session each belonged to. So section 1.0
--   raises a NAMED error if relrowsecurity is false, rather than proceeding.
--   That is a refusal to make an existing hole worse, not a claim to fix one:
--   the remedy is to apply 003_signal_trace_restoration.sql sections 8 and 9
--   (or 002_signal_trace.sql) first. This file deliberately does NOT enable
--   RLS itself and does NOT create, alter or drop a policy - Requirement 21.4's
--   RLS on public.signals already exists and the one rule this plan repeats
--   everywhere is that no task weakens or rewrites an existing control.
--
--   Row level security is ROW-scoped, not column-scoped, so the three existing
--   owner policies keep applying unchanged to both new columns. Tenant
--   isolation on public.signals is exactly as strong after this migration as
--   before it.
--
-- WHY THIS FILE REFUSES TO RUN WITHOUT paper_sessions (migration 009)
--   fk_signals_paper_session REFERENCES public.paper_sessions(id). If 009 has
--   not landed, that REFERENCES clause would raise a bare 42P01 from inside an
--   ALTER, or - worse - leave a dangling reference. Section 1.0 therefore
--   raises a NAMED error pointing at 009_paper_trading.sql when
--   public.paper_sessions is absent, rather than proceeding. design.md §
--   "Signal Trace extension": "paper_sessions must therefore exist before this
--   migration, so 009_paper_trading.sql precedes 010_signal_environment.sql.
--   Applying 010 alone raises a named error rather than creating a dangling FK."
--
-- WHY A SEPARATE FILE RATHER THAN AN EDIT TO 002/003 OR 005b
--   The same reason 005b and 004b/004c/004d/004e state: migrations here are
--   applied BY HAND, per file, and NOTHING RECORDS WHICH FILES AN ENVIRONMENT
--   HAS RUN - there is no migration table and .github/workflows/03-deploy.yml
--   has no migration step. Appending to a file an operator may already have
--   applied leaves no signal that the file changed, so the new statements
--   would simply never run. A new filename is the signal. 002, 003 and 005b
--   are left byte-identical.
--
-- WHY THE TWO INDEXES
--   idx_signals_user_environment (user_id, environment, generated_at DESC):
--   Requirement 23.4's environment filter on the Signal_Trace_Page, scoped to
--   the caller's own signals (Requirement 23.6) and ordered newest first, is
--   answered by one index scan - "this user's PAPER signals, most recent
--   first" without scanning their whole signal history across environments.
--   Not partial: every one of the three environments is a value the trace page
--   filters on, and NULL cannot occur once section 1.3c commits.
--
--   idx_signals_paper_session (paper_session_id) WHERE paper_session_id IS NOT
--   NULL: the reverse lookup "which signals belong to this Paper_Session", and
--   the index PostgreSQL uses to find the rows to NULL out when a session is
--   deleted through the ON DELETE SET NULL. PARTIAL, because only PAPER signals
--   carry a non-NULL paper_session_id and LIVE/BACKTEST signals - the vast
--   majority on an established table - would otherwise each add a dead index
--   entry.
--
-- APPLICATION
--   NOT applied automatically. Apply this file explicitly against the target
--   database IN A MAINTENANCE WINDOW (see the SET NOT NULL note above), then
--   run the verification queries at the bottom, the way 007/008/009 were
--   applied. DEPENDS ON public.signals (002/003) with RLS enabled, and on
--   public.paper_sessions (009) for the FK target.
--
--   Until it is applied, these two columns do not exist. Every code path that
--   reads or writes them must degrade the way signal_service already does for
--   005b's order_lifecycle_state: a WARNING NAMING THIS FILE
--   ("010_signal_environment.sql"), not a 500, conditional on the same
--   migration probe signal_service performs. A missing column surfaces from
--   PostgREST as an undefined-column error on the insert.
--
-- SAFETY
--   * Additive only (Requirement 24.7). No DROP, no TRUNCATE, no DELETE, no
--     ALTER COLUMN TYPE, no rename, no CREATE/ALTER/DROP POLICY, no
--     ENABLE/DISABLE ROW LEVEL SECURITY, no DROP INDEX, no CREATE TRIGGER, no
--     GRANT, no REVOKE. The one UPDATE writes 'LIVE' ONLY into rows where
--     environment IS NULL - it rewrites no value that is already set, so on a
--     re-run (every row already non-NULL) it touches zero rows.
--   * Fully idempotent (Requirements 24.7, 24.8). ADD COLUMN IF NOT EXISTS for
--     both columns; the back-fill matches nothing on a second run; SET DEFAULT
--     and SET NOT NULL are no-ops when the column already carries them (both
--     guarded by an information_schema check so a re-run neither re-scans the
--     table nor raises); the CHECK and the FK behind pg_constraint existence
--     guards; CREATE INDEX IF NOT EXISTS for both indexes; COMMENT replaces. A
--     re-run adds nothing, rewrites nothing and raises nothing.
--   * One transaction. Either both columns, the back-fill, the DEFAULT, the
--     NOT NULL, chk_signals_environment, fk_signals_paper_session, the two
--     indexes and the comments exist, or none of them do. A signals table
--     carrying environment without chk_signals_environment, or carrying it
--     NULL, is never visible to a session.
--   * The legacy signals.status and 005b's order_lifecycle_state columns are
--     NOT touched, NOT constrained and NOT dropped by this file.

-- ==========================================================================
-- SECTION 1 - task 11.5: public.signals Execution_Environment + Paper_Session
-- ==========================================================================

BEGIN;

-- 1.0) Preflight -------------------------------------------------------
-- Read-only assertions plus three transaction-local counters. Fails with a
-- readable, NAMED message instead of a bare undefined_table error, refuses to
-- add trading-record columns to a table without row-level security, refuses to
-- create a dangling FK to a paper_sessions that 009 has not created, and
-- records the policy/index/trigger inventory so section 1.6 can prove this
-- file left policies and triggers alone and added exactly two indexes.
-- Idempotent: reads catalogues, writes nothing but three settings local to
-- this transaction.
DO $$
DECLARE
    rls_on    BOOLEAN;
    policies  INTEGER;
    indexes   INTEGER;
    triggers  INTEGER;
BEGIN
    IF to_regclass('public.signals') IS NULL THEN
        RAISE EXCEPTION
            '010 precondition failed: table public.signals does not exist. '
            'Apply backend_app/migrations/002_signal_trace.sql (or '
            '003_signal_trace_restoration.sql section 4) first. This file adds '
            'signals.environment and signals.paper_session_id (Requirement '
            '23.1) and creates no signals table of its own.';
    END IF;

    -- The FK target. 009 creates public.paper_sessions; without it the
    -- REFERENCES clause in section 1.5 would raise a bare 42P01 from inside an
    -- ALTER. Name the file to apply, per design.md § "Signal Trace extension".
    IF to_regclass('public.paper_sessions') IS NULL THEN
        RAISE EXCEPTION
            '010 precondition failed: table public.paper_sessions does not '
            'exist. signals.paper_session_id references it '
            '(fk_signals_paper_session, ON DELETE SET NULL), so this migration '
            'depends on it. Apply backend_app/migrations/009_paper_trading.sql '
            'first. This file does not create paper_sessions - applying 010 '
            'alone must raise this named error rather than create a dangling '
            'foreign key (Requirements 23.2, 24.4).';
    END IF;

    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c
     WHERE c.oid = 'public.signals'::regclass;

    IF NOT rls_on THEN
        RAISE EXCEPTION
            '010 refuses to run: row level security is DISABLED on '
            'public.signals. This migration records the Execution_Environment '
            'and the owning Paper_Session on every signal, so applying it to a '
            'table without row-level isolation would expose another user''s '
            'trading records - which of their signals are paper versus live, '
            'and which paper session each belongs to. Apply '
            'backend_app/migrations/003_signal_trace_restoration.sql sections '
            '8 and 9 (RLS + the three owner policies) first. This file '
            'deliberately does not enable RLS itself, because that would be a '
            'change to an existing control this task must leave untouched '
            '(Requirement 21.4).';
    END IF;

    SELECT count(*) INTO policies
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'signals';

    IF policies = 0 THEN
        RAISE EXCEPTION
            '010 refuses to run: row level security is enabled on '
            'public.signals but it has NO policies, so the table is '
            'unreachable to every non-superuser role and a signal written here '
            'could not be read back. Apply '
            'backend_app/migrations/003_signal_trace_restoration.sql section 9 '
            'first.';
    END IF;

    SELECT count(*) INTO indexes
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'signals';

    SELECT count(*) INTO triggers
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.signals'::regclass
       AND NOT t.tgisinternal;

    PERFORM set_config('aerora.signals010_policies_before', policies::TEXT, true);
    PERFORM set_config('aerora.signals010_indexes_before',  indexes::TEXT,  true);
    PERFORM set_config('aerora.signals010_triggers_before', triggers::TEXT, true);

    RAISE NOTICE '010 preflight: public.signals has RLS enabled, % policies, '
                 '% indexes and % user triggers; public.paper_sessions is '
                 'present for the FK. This file adds no policy and no trigger, '
                 'adds exactly 2 indexes, and section 1.6 verifies each of '
                 'those facts.',
                 policies, indexes, triggers;
END $$;

-- 1.1) The two columns --------------------------------------------------
-- design.md § "Signal Trace extension", verbatim.
--
--   environment       The Execution_Environment (Requirement 23.1, 13.1): one
--                     of BACKTEST, PAPER, LIVE, fixed by
--                     chk_signals_environment (section 1.4). Added NULLABLE
--                     here so ADD COLUMN does not rewrite existing rows with a
--                     DEFAULT; sections 1.3a-c back-fill it, default it and
--                     make it NOT NULL, all in this transaction, so it is
--                     total the moment the file commits.
--   paper_session_id  The Paper_Session a PAPER signal belongs to (Requirement
--                     23.2), REFERENCES public.paper_sessions(id) ON DELETE SET
--                     NULL (section 1.5). NULLABLE with NO default: a LIVE or
--                     BACKTEST signal has none, and NULL there is the true
--                     value.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, so a re-run skips each column already
-- present. Neither column is NOT NULL AT THIS POINT, so this statement rewrites
-- no existing row and back-fills no DEFAULT; that is sections 1.3a-c's job.
ALTER TABLE public.signals
    ADD COLUMN IF NOT EXISTS environment      TEXT,
    ADD COLUMN IF NOT EXISTS paper_session_id UUID;

-- 1.2) Column shape assertion ------------------------------------------
-- ADD COLUMN IF NOT EXISTS is SILENT about a pre-existing column of the same
-- name and a different type - for instance one added by hand during an
-- investigation. Both silences would matter here:
--   * a non-text environment turns section 1.4's IN-list into a cast
--     comparison, and an enum or integer column would not admit the spellings
--     execution_environment.py writes;
--   * a non-uuid paper_session_id makes section 1.5's FK equality a cast
--     against paper_sessions.id (a uuid), and could make the ADD CONSTRAINT
--     fail with a type-mismatch rather than be created.
-- 'character varying' is accepted wherever 'text' is expected. This mirrors
-- 005b section 1.2 statement for statement.
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
      -- ::TEXT on every information_schema identifier: those columns are the
      -- sql_identifier / character_data domains, not text, and an explicit
      -- cast keeps the comparison a plain text compare on every server version.
      FROM (VALUES
                ('environment',      ARRAY['text', 'character varying']),
                ('paper_session_id', ARRAY['uuid'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'signals'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.signals has environment column(s) of the wrong shape: %. '
            'A pre-existing column of that name was left as it was, because '
            'ADD COLUMN IF NOT EXISTS does not alter one. Reconcile it by hand '
            'before re-running this migration.', problems;
    END IF;
END $$;

-- 1.3a) Back-fill existing rows to LIVE --------------------------------
-- Requirement 23.7. Every signals row that existed before this migration was
-- produced by the live path (the only signal writer that predates the
-- Paper_Session and the backtest recorder), so 'LIVE' is a FACT about those
-- rows, not a default asserted for convenience. See the header's "WHY
-- environment IS NOT NULL".
-- Idempotent: matches only rows with environment IS NULL, of which there are
-- none on a re-run, so a second application updates ZERO rows. It never
-- rewrites an environment that is already set (Requirement 23.7's "SHALL NOT
-- delete or rewrite any existing signal row" is honoured for every row that
-- already carries a value).
UPDATE public.signals SET environment = 'LIVE' WHERE environment IS NULL;

-- 1.3b) DEFAULT 'LIVE' -------------------------------------------------
-- So a writer that does not name the environment still records a true value
-- for the only path that existed before this feature. Guarded by an
-- information_schema check so a re-run is a genuine no-op (SET DEFAULT is
-- cheap, but the guard keeps the NOTICE honest and mirrors section 1.3c).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'signals'
           AND column_name = 'environment'
           AND (column_default IS NULL OR column_default NOT LIKE '%LIVE%')
    ) THEN
        ALTER TABLE public.signals ALTER COLUMN environment SET DEFAULT 'LIVE';
        RAISE NOTICE 'Set DEFAULT ''LIVE'' on public.signals.environment.';
    ELSE
        RAISE NOTICE 'public.signals.environment already defaults to ''LIVE''; '
                     'left unchanged.';
    END IF;
END $$;

-- 1.3c) SET NOT NULL ---------------------------------------------------
-- Makes the three-value vocabulary TOTAL over public.signals from the moment
-- this file commits (Requirement 23.1). The back-fill in 1.3a guarantees no
-- row violates it, so the ALTER's table scan cannot fail on existing data.
--
-- OPERATOR NOTE: this ALTER takes an ACCESS EXCLUSIVE lock on public.signals
-- and SCANS the table to prove no NULL remains. On a large signals table that
-- blocks every reader and writer for the scan's duration - THIS FILE BELONGS
-- IN A MAINTENANCE WINDOW.
--
-- Guarded by an is_nullable check so a re-run neither re-scans the table nor
-- raises: the second application sees environment already NOT NULL and skips
-- the ALTER entirely. This is the guard that makes SET NOT NULL idempotent.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'signals'
           AND column_name = 'environment'
           AND is_nullable = 'YES'
    ) THEN
        ALTER TABLE public.signals ALTER COLUMN environment SET NOT NULL;
        RAISE NOTICE 'Set NOT NULL on public.signals.environment (table '
                     'scanned once under ACCESS EXCLUSIVE; the section 1.3a '
                     'back-fill guaranteed no NULL remained).';
    ELSE
        RAISE NOTICE 'public.signals.environment is already NOT NULL; the '
                     'table scan is skipped on this re-run.';
    END IF;
END $$;

-- 1.4) chk_signals_environment -----------------------------------------
-- Requirement 23.1, 13.1. The three values, in the order EXECUTION_ENVIRONMENTS
-- lists them (backend_app/backend/execution_environment.py, task 13.1). This
-- IN-list and that tuple are the same vocabulary written twice, once for Python
-- and once for the database; nothing here may be edited without editing there,
-- and Requirement 24.7 permits only ADDING values, never removing or respelling
-- one.
--
-- Guarded rather than bare: PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS and
-- a second bare run would raise 42710 and abort the file. No DROP-then-ADD,
-- which would open a window inside this transaction where the vocabulary is
-- unenforced. Validated against existing rows on the first run: every row now
-- carries 'LIVE' (or a value a writer set), so the ALTER cannot fail on
-- existing data.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname  = 'chk_signals_environment'
           AND conrelid = 'public.signals'::regclass
    ) THEN
        ALTER TABLE public.signals
            ADD CONSTRAINT chk_signals_environment
            CHECK (environment IN ('BACKTEST', 'PAPER', 'LIVE'));
        RAISE NOTICE 'Added chk_signals_environment to public.signals (the 3 '
                     'Execution_Environment values of Requirement 13.1).';
    ELSE
        RAISE NOTICE 'chk_signals_environment already present on '
                     'public.signals; left unchanged.';
    END IF;
END $$;

-- 1.5) fk_signals_paper_session ----------------------------------------
-- Requirements 23.2, 23.7, 24.4. REFERENCES public.paper_sessions(id) ON
-- DELETE SET NULL - deleting a Paper_Session NULLs paper_session_id on its
-- signals rather than deleting them, so no signal row is ever removed
-- (Requirement 23.7's "SHALL NOT delete or rewrite any existing signal row").
-- design.md § "Delete rules": "signals.paper_session_id uses ON DELETE SET
-- NULL, so deleting a session never removes a signal row."
--
-- Guarded rather than bare, same as the CHECK above. Validated on the first
-- run against existing rows: every pre-existing row has paper_session_id NULL
-- (this file added the column NULL and never back-filled it), and a NULL FK
-- value is always valid, so the ALTER cannot fail on existing data.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname  = 'fk_signals_paper_session'
           AND conrelid = 'public.signals'::regclass
    ) THEN
        ALTER TABLE public.signals
            ADD CONSTRAINT fk_signals_paper_session
            FOREIGN KEY (paper_session_id)
            REFERENCES public.paper_sessions(id) ON DELETE SET NULL;
        RAISE NOTICE 'Added fk_signals_paper_session to public.signals '
                     '(REFERENCES public.paper_sessions(id) ON DELETE SET '
                     'NULL).';
    ELSE
        RAISE NOTICE 'fk_signals_paper_session already present on '
                     'public.signals; left unchanged.';
    END IF;
END $$;

-- 1.6) Indexes ---------------------------------------------------------
-- idx_signals_user_environment: Requirements 23.4, 23.6. The environment
-- filter on the Signal_Trace_Page, scoped to the caller's own signals and
-- ordered newest first, served by one index scan. Not partial: all three
-- environments are filtered on, and NULL cannot occur after section 1.3c.
CREATE INDEX IF NOT EXISTS idx_signals_user_environment
    ON public.signals (user_id, environment, generated_at DESC);

-- idx_signals_paper_session: the reverse lookup "which signals belong to this
-- Paper_Session", and the index the ON DELETE SET NULL uses to find rows to
-- NULL when a session is deleted. PARTIAL, because only PAPER signals carry a
-- non-NULL paper_session_id and the LIVE/BACKTEST majority would otherwise each
-- add a dead index entry.
CREATE INDEX IF NOT EXISTS idx_signals_paper_session
    ON public.signals (paper_session_id)
    WHERE paper_session_id IS NOT NULL;

-- 1.7) Column comments -------------------------------------------------
-- Intent the schema cannot express. COMMENT replaces, so this is idempotent.
COMMENT ON COLUMN public.signals.environment IS
    'Execution_Environment this signal was produced under (Requirement 23.1): '
    'one of BACKTEST, PAPER, LIVE, fixed by chk_signals_environment and '
    'written from ExecutionEnvironment / EXECUTION_ENVIRONMENTS in '
    'backend_app/backend/execution_environment.py. NOT NULL with DEFAULT '
    '''LIVE'': every signals row that existed before migration 010 was produced '
    'by the live path, so ''LIVE'' is a fact back-filled onto them, not a '
    'placeholder. Unlike 005b''s nullable order_lifecycle_state, the vocabulary '
    'is total from the moment 010 commits (back-fill, SET DEFAULT and SET NOT '
    'NULL run in the same transaction as the ADD COLUMN and the CHECK).';

COMMENT ON COLUMN public.signals.paper_session_id IS
    'The Paper_Session a PAPER signal was generated inside (Requirement 23.2), '
    'REFERENCES public.paper_sessions(id) ON DELETE SET NULL '
    '(fk_signals_paper_session). NULL for a LIVE or BACKTEST signal, and for a '
    'PAPER signal only until the recorder sets it - a NULL here is the true '
    'value, never back-filled. Deleting a Paper_Session NULLs this column '
    'rather than deleting the signal row (Requirement 23.7). signals.'
    'deployment_id stays NULL for a paper signal: a Paper_Session is not a '
    'deployment, and paper_session_id is the applicable identifier.';

-- 1.8) Postflight - the objects exist, and nothing else moved ----------
-- Turns the header's promises into facts about the database. Proves environment
-- is NOT NULL, the CHECK and the FK exist, and re-counts the policy, trigger
-- and index inventory recorded in section 1.0: policies and triggers must be
-- unchanged, indexes must have grown by exactly the two this section creates
-- (0 on a re-run, since both are IF NOT EXISTS).
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    policies_before INTEGER := current_setting('aerora.signals010_policies_before')::INTEGER;
    indexes_before  INTEGER := current_setting('aerora.signals010_indexes_before')::INTEGER;
    triggers_before INTEGER := current_setting('aerora.signals010_triggers_before')::INTEGER;
    policies_now    INTEGER;
    indexes_now     INTEGER;
    triggers_now    INTEGER;
    env_nullable    TEXT;
    missing         TEXT;
BEGIN
    -- (a) Both columns exist and environment is NOT NULL.
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('environment'), ('paper_session_id'))
             AS expected(column_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns c
                WHERE c.table_schema = 'public'
                  AND c.table_name   = 'signals'
                  AND c.column_name  = expected.column_name);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '010 postflight failed: column(s) % are absent from '
                        'public.signals.', missing;
    END IF;

    SELECT c.is_nullable INTO env_nullable
      FROM information_schema.columns c
     WHERE c.table_schema = 'public' AND c.table_name = 'signals'
       AND c.column_name = 'environment';

    IF env_nullable <> 'NO' THEN
        RAISE EXCEPTION
            '010 postflight failed: public.signals.environment is still '
            'NULLABLE, so Requirement 23.1''s three-value vocabulary is not '
            'total. The section 1.3a back-fill or 1.3c SET NOT NULL did not '
            'take effect.';
    END IF;

    -- (b) The CHECK and the FK exist.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'chk_signals_environment'
           AND conrelid = 'public.signals'::regclass) THEN
        RAISE EXCEPTION '010 postflight failed: chk_signals_environment is '
                        'absent from public.signals.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'fk_signals_paper_session'
           AND conrelid = 'public.signals'::regclass
           AND contype = 'f') THEN
        RAISE EXCEPTION '010 postflight failed: fk_signals_paper_session is '
                        'absent from public.signals.';
    END IF;

    -- (c) The two indexes exist.
    SELECT string_agg(expected.indexname, ', ' ORDER BY expected.indexname)
      INTO missing
      FROM (VALUES ('idx_signals_user_environment'),
                   ('idx_signals_paper_session'))
             AS expected(indexname)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_indexes i
                WHERE i.schemaname = 'public'
                  AND i.tablename  = 'signals'
                  AND i.indexname  = expected.indexname);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '010 postflight failed: index(es) % are absent from '
                        'public.signals.', missing;
    END IF;

    -- (d) This file added no policy and no trigger, and exactly two indexes.
    SELECT count(*) INTO policies_now
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'signals';
    SELECT count(*) INTO indexes_now
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'signals';
    SELECT count(*) INTO triggers_now
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.signals'::regclass
       AND NOT t.tgisinternal;

    IF policies_now <> policies_before THEN
        RAISE EXCEPTION
            '010 postflight failed: public.signals policy count changed from % '
            'to %. This file must create, alter and drop no policy '
            '(Requirement 21.4).', policies_before, policies_now;
    END IF;

    IF triggers_now <> triggers_before THEN
        RAISE EXCEPTION
            '010 postflight failed: public.signals user-trigger count changed '
            'from % to %. This file attaches no trigger.',
            triggers_before, triggers_now;
    END IF;

    IF indexes_now NOT IN (indexes_before, indexes_before + 1, indexes_before + 2) THEN
        RAISE EXCEPTION
            '010 postflight failed: public.signals index count changed from % '
            'to %, but this file creates exactly the 2 named indexes (fewer new '
            'ones on a re-run where they already existed).',
            indexes_before, indexes_now;
    END IF;

    RAISE NOTICE '010 complete: public.signals.environment (NOT NULL, DEFAULT '
                 '''LIVE'', chk_signals_environment over BACKTEST/PAPER/LIVE) '
                 'and paper_session_id (FK to paper_sessions ON DELETE SET '
                 'NULL) exist with their 2 indexes. Policies (%) and user '
                 'triggers (%) unchanged; index count % -> %. No pre-existing '
                 'control was weakened.',
                 policies_now, triggers_now, indexes_before, indexes_now;
END $$;

COMMIT;

-- ==========================================================================
-- VERIFICATION QUERIES - run these after applying, the way 007/008/009 do
-- ==========================================================================
--
-- 1) Both columns, their types, nullability and default:
--
--    SELECT column_name, data_type, is_nullable, column_default
--      FROM information_schema.columns
--     WHERE table_schema = 'public' AND table_name = 'signals'
--       AND column_name IN ('environment', 'paper_session_id')
--     ORDER BY column_name;
--    -- Expect: environment  text  NO  'LIVE'::text
--    --         paper_session_id  uuid  YES  (null)
--
-- 2) The check enumerates exactly BACKTEST, PAPER, LIVE (Requirement 13.1):
--
--    SELECT conname, pg_get_constraintdef(oid)
--      FROM pg_constraint
--     WHERE conrelid = 'public.signals'::regclass
--       AND conname = 'chk_signals_environment';
--    -- Must match EXECUTION_ENVIRONMENTS in
--    -- backend_app/backend/execution_environment.py (task 13.1).
--
-- 3) The FK and its ON DELETE SET NULL (Requirement 23.7):
--
--    SELECT conname, pg_get_constraintdef(oid)
--      FROM pg_constraint
--     WHERE conrelid = 'public.signals'::regclass
--       AND conname = 'fk_signals_paper_session';
--    -- Expect: FOREIGN KEY (paper_session_id) REFERENCES paper_sessions(id)
--    --         ON DELETE SET NULL
--
-- 4) No pre-existing signal row is deleted when its Paper_Session is deleted
--    (Requirement 23.7). Inside a transaction you ROLL BACK:
--
--    BEGIN;
--      -- pick a paper session with at least one signal:
--      DELETE FROM public.paper_sessions WHERE id = '<a session id>';
--      -- expect: success. Its signals remain, with paper_session_id now NULL.
--      SELECT count(*) FROM public.signals
--       WHERE paper_session_id = '<that session id>';   -- expect 0 (all NULLed)
--    ROLLBACK;
--
-- 5) The vocabulary is total - no NULL and no out-of-set value remains:
--
--    SELECT environment, count(*) FROM public.signals
--     GROUP BY environment ORDER BY environment;
--    -- Every group MUST be one of BACKTEST, PAPER, LIVE; no NULL group.
--
-- 6) The two indexes exist:
--
--    SELECT indexname, indexdef FROM pg_indexes
--     WHERE schemaname = 'public' AND tablename = 'signals'
--       AND indexname IN ('idx_signals_user_environment',
--                         'idx_signals_paper_session')
--     ORDER BY indexname;
--    -- idx_signals_paper_session MUST carry WHERE (paper_session_id IS NOT NULL).
--
-- 7) Idempotency (Requirements 24.7, 24.8). Re-running this whole file must
--    report no error, must update ZERO rows in the section 1.3a back-fill, must
--    NOT re-scan the table for SET NOT NULL, and must leave every count above
--    unchanged.
--
-- ==========================================================================
-- END OF MIGRATION 010. This is the LAST migration in dependency order:
--   006 -> 007 -> 008 -> 009 -> 010. Nothing depends on this file.
-- ==========================================================================
