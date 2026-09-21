-- 015_strategy_last_signal_at.sql  (backend_app/migrations, migration 015)
--
-- PURPOSE
--   Give public.strategies the column BC-3 already publishes, so that
--   GET /api/strategies can report WHEN A STRATEGY LAST SIGNALLED instead of
--   reporting null forever:
--
--     strategies.last_signal_at TIMESTAMPTZ NULL
--
--   vyomquant-ui-redesign task 12.3 (follow-up), design.md §7.2,
--   Requirements 4.1, 19.1, 19.2.
--
-- WHY THIS FILE EXISTS AT ALL - READ THIS FIRST
--   BC-3 landed the READER: backend_app/routers/strategies.py publishes
--   last_signal_at on every entry of every strategies list, taking it off the
--   strategies row through the same key the dashboard strategy projection
--   already reads (dashboard_aggregation_service.get_strategies, published
--   there as last_signal_time). That projection is correct and additive, and
--   it reports null when the key is absent - which is honest.
--
--   But NO MIGRATION IN THIS REPOSITORY DECLARED THE COLUMN. Zero occurrences
--   of last_signal_at under backend_app/migrations/ before this file. So the
--   field reported null on every database, on every page load, permanently -
--   and task 12's own preamble says no field's not-available state may be its
--   permanent outcome. The follow-up is a PRODUCER, not another reader: this
--   file plus the write in backend_app/backend/strategy_last_signal.py.
--
-- WHY A STORED COLUMN AND NOT A DERIVED MAX(generated_at)
--   BC-4 (last_execution_at, task 12.4) needs no column: execution_records is
--   a SQLAlchemy/PostgreSQL table in this application, so
--   ExecutionRecordRepository.get_last_execution_at_by_strategy issues
--   MAX(created_at) ... GROUP BY strategy_id - one grouped aggregate per page,
--   no denormalised column, no write. That is the better shape wherever it is
--   available, and the obvious question is why last_signal_at does not use it.
--
--   IT IS NOT AVAILABLE FOR public.signals. Three reasons, each checked:
--
--     1. There is no SQLAlchemy model for signals. backend_app/core/models/
--        holds billing, dag_task, execution_record, execution_tables,
--        pydantic_models and reconciliation - and no signals. Every reader and
--        writer of public.signals in this repository goes through PostgREST
--        (sb.table("signals")), so BC-4's route - hand the statement to a
--        session - has no session to hand it to.
--
--     2. PostgREST's grouped aggregates are OFF unless an operator turns them
--        on. max() over a GROUP BY is gated behind db-aggregates-enabled,
--        which defaults to false, and on Supabase is set per instance on the
--        authenticator role (pgrst.db_aggregates_enabled). This repository
--        pins postgrest==0.16.8 and nothing in it sets that flag. A derived
--        read would therefore report null on every database whose operator had
--        not flipped an undocumented server setting - which is EXACTLY the
--        outcome this follow-up exists to end, moved from "no column" to "no
--        server flag".
--
--     3. A VIEW over public.signals would bypass RLS on this baseline. A
--        normal view executes with its OWNER's privileges, so row-level
--        security on the underlying signals table does not apply to callers
--        reading through it; security_invoker = true (PostgreSQL 15) is what
--        fixes that, and this repository's PostgreSQL baseline predates
--        PostgreSQL 14 (007, 008 and 009 all record that CREATE OR REPLACE
--        TRIGGER, a PG14 feature, is unavailable and use pg_trigger guards
--        instead). A view here would be a TENANT ISOLATION HOLE - one user
--        reading which strategies another user's account is signalling on -
--        and migration 010's header states the rule this file follows: no
--        migration in this plan weakens an existing control.
--
--   So the honest options were a denormalised column or a trigger, and the
--   column won. See the WHY NOT A TRIGGER note below.
--
-- WHY NOT A TRIGGER ON public.signals
--   There is precedent for triggers here (007, 008, 009, 013, 014), so the
--   objection is not novelty. It is that a trigger would sit INSIDE THE SIGNAL
--   INSERT'S TRANSACTION, and the signal write is the one path in this system
--   that must not become able to fail:
--
--     * An AFTER INSERT trigger that UPDATEs the strategy row takes a row lock
--       on public.strategies for the duration of the signal's transaction. Two
--       signals for the same strategy then serialise on that lock, and any
--       other transaction holding that strategy row - a rename, an archive, a
--       version bump - blocks signal inserts behind it. A lock wait is not an
--       exception, so an EXCEPTION WHEN OTHERS block inside the trigger does
--       NOT contain it; statement_timeout then aborts the whole INSERT. That
--       is a new way for a generated signal to be lost.
--     * The trigger runs as the INSERTing role, so RLS on public.strategies
--       applies to its UPDATE. Where the signal's user_id and the strategy
--       row's user_id differ, the UPDATE matches zero rows and the trigger is
--       a silent no-op - a producer that looks installed and writes nothing.
--     * Every trigger this repository already has is either a GUARD that
--       rejects a bad transition or a touch_updated_at on the SAME row. None
--       writes to a different table. This would be the first, and it would be
--       invisible to anyone reading Python.
--
--   The write therefore lives in application code, AFTER the signal row is
--   confirmed persisted and OFF the awaited signal path entirely. See
--   backend_app/backend/strategy_last_signal.py, whose docstring states the
--   isolation it guarantees.
--
-- WHY NULLABLE, WITH NO DEFAULT, AND WHY THAT IS THE WHOLE CONTRACT
--   Requirement 19.2. A strategy that has never signalled must report null -
--   not an epoch, not its creation timestamp, not now(). The column's SHAPE is
--   the entire two-state vocabulary, exactly as 005a's archived_at is:
--
--     last_signal_at IS NULL      -> this strategy has never signalled
--     last_signal_at IS NOT NULL  -> it has, and the value says when
--
--   There is no third state to forbid and no value to validate beyond "is it
--   set", so there is no CHECK constraint here for the same reason 005a has
--   none. And that makes the shape load-bearing, which is why section 2
--   asserts it instead of trusting ADD COLUMN IF NOT EXISTS:
--
--     * NOT NULL would be unsatisfiable on a table of existing strategies,
--       and satisfying it with a back-fill would mean asserting that every
--       strategy ever created has signalled. It has not.
--     * A DEFAULT of now() would make EVERY STRATEGY CREATED FROM THEN ON
--       claim it had signalled at the moment it was saved - a trader looking
--       at the Strategies page would see a brand-new draft reporting a live
--       signal. That is the single worst outcome reachable from this file and
--       precisely the fabrication Requirement 19.2 forbids.
--     * A plain TIMESTAMP (without time zone) would make the recorded instant
--       ambiguous and its rendering dependent on the session TimeZone.
--       public.signals.generated_at - the value this column mirrors - is
--       TIMESTAMPTZ, and two spellings of one instant is one too many.
--
--   There is NO BACK-FILL in this file, and that is deliberate rather than an
--   omission. The true value for every existing strategy is derivable in
--   principle - MAX(generated_at) FROM public.signals GROUP BY strategy_id -
--   but running it here would mean this migration writing a value into every
--   strategy row of every tenant, under an ACCESS EXCLUSIVE-adjacent lock, on
--   a table whose RLS this file must not step around. Compare 010, which DOES
--   back-fill signals.environment to 'LIVE' - and note WHY it was allowed to:
--   'LIVE' was a FACT about every pre-existing row (no other path could have
--   written one), not a judgement. Here the value is not a fact this file
--   knows; it is the result of a cross-table aggregate. So every existing
--   strategy starts at NULL - "has not signalled since this column existed" -
--   and each earns a real timestamp the first time it signals. NULL is the
--   honest reading of "no producer has reported one yet".
--
--   An operator who WANTS the history seeded may run the one-off statement in
--   verification query 6 at the bottom of this file, by hand, with the cost
--   and the locking in front of them. It is deliberately not part of the
--   migration.
--
-- WHY THERE IS NO INDEX (THE DIFFERENCE FROM 005a)
--   005a added idx_strategies_archived_at because archived_at is in the
--   default listing's WHERE clause (user_id = ? AND archived_at IS NULL), so
--   every page load reads through it. NOTHING FILTERS OR ORDERS BY
--   last_signal_at. It is read only as one key of the select("*") the listing
--   already issues (see list_strategies' "WHY THE SELECT IS *" note) and
--   written only by primary-key-and-owner equality. An index on it would add
--   an index write to the signal path for no read at all, which is the wrong
--   trade on the hottest write in the system.
--
-- WHY THIS FILE REFUSES TO RUN WITHOUT RLS ON public.strategies
--   The same refusal 005a and 010 make, for the same reason and by the same
--   means. last_signal_at says when a user's strategy last fired - which is
--   a statement about their live trading activity. Adding it to a table whose
--   row-level isolation is off would let any authenticated caller read, and
--   write, another user's trading tempo. Section 0 raises a NAMED error if
--   relrowsecurity is false, or if it is true with no policies at all (a state
--   in which the table is unreachable to every non-superuser role, so a
--   timestamp written here could not be read back). The remedy is to apply
--   rls_migration.sql first.
--
--   THIS FILE DELIBERATELY DOES NOT ENABLE RLS ITSELF. That would be a change
--   to the very control this task must leave untouched, and it would paper
--   over a misconfiguration an operator needs to see. Row-level security is
--   ROW-scoped, not column-scoped, so whatever owner-scoped policies
--   public.strategies already has keep applying unchanged to last_signal_at.
--   Tenant isolation on this table is exactly as strong after this migration
--   as before it.
--
-- WHY A SEPARATE FILE RATHER THAN AN EDIT TO 005a
--   The reason every file from 004b onwards states: migrations here are
--   applied BY HAND, per file, and NOTHING RECORDS WHICH FILES AN ENVIRONMENT
--   HAS RUN - there is no migration table, and .github/workflows/03-deploy.yml
--   has no migration step. Appending to a file an operator may already have
--   applied leaves no signal that the file changed, so the new statements
--   would simply never run. A new filename is the signal. 005a is left
--   byte-identical.
--
-- LOCKING AND COST
--   ALTER TABLE ... ADD COLUMN of a nullable column WITH NO DEFAULT is a
--   catalogue-only change - PostgreSQL rewrites no rows - so it holds
--   ACCESS EXCLUSIVE on public.strategies only momentarily. There is no index
--   build, no back-fill, no SET NOT NULL and therefore no table scan. Unlike
--   010, this file does NOT need a maintenance window.
--
-- SAFETY
--   * Additive only (Requirement 19.1). No statement in this file modifies a
--     row: no UPDATE, no DELETE, no TRUNCATE, no DEFAULT. No DROP, no
--     ALTER COLUMN, no rename, no ADD/DROP CONSTRAINT, no CREATE/ALTER/DROP
--     POLICY, no ENABLE/DISABLE ROW LEVEL SECURITY, no CREATE/DROP INDEX, no
--     CREATE/ALTER/DROP TRIGGER, no GRANT, no REVOKE. It adds exactly one
--     column and one column comment.
--   * Fully idempotent. ADD COLUMN IF NOT EXISTS and COMMENT (which
--     replaces). A re-run adds nothing, rewrites nothing and raises nothing;
--     sections 0, 2 and 4 read catalogues and write nothing.
--   * One transaction. Either the column and its comment exist, or neither
--     does.
--   * Applying this file reports no signal for any strategy: every existing
--     row has last_signal_at NULL immediately afterwards, which is the same
--     answer GET /api/strategies gave before the column existed. The only
--     thing that changes on application is that a FUTURE signal can now be
--     recorded.
--
-- APPLICATION
--   NOT applied automatically. Apply this file explicitly against the target
--   database, then run the verification queries at the bottom, the way 005a
--   and 007-014 were applied. DEPENDS ON public.strategies (part of the
--   pre-existing Supabase schema, not created by any migration here) with RLS
--   enabled.
--
--   Until it is applied, the column does not exist. Both code paths that
--   touch it already degrade rather than fail:
--
--     * The READER (routers/strategies.py::list_strategies) reads select("*"),
--       so the key is merely absent and last_signal_at is published as null -
--       never dropped from the response, never guessed. Naming the column in
--       the projection would raise 42703 and take the Strategies page down
--       over an optional field, which is why _LIST_COLUMNS deliberately does
--       not contain it.
--     * The WRITER (backend_app/backend/strategy_last_signal.py) classifies
--       the 42703 / PGRST204, logs a WARNING NAMING THIS FILE
--       ("015_strategy_last_signal_at.sql"), caches the verdict so it stops
--       re-attempting, and re-probes after a few minutes so applying this
--       file to a running fleet takes effect without a redeploy. It NEVER
--       reports a write it did not make, and it never fails the signal that
--       occasioned it.

BEGIN;

-- 0) Preflight -----------------------------------------------------------
-- Read-only assertions, plus the four transaction-local baselines section 4
-- re-checks. Fails with a readable message naming the missing or
-- misconfigured object instead of a bare 42P01 from inside the ALTER.
--
-- No policy, index, trigger or constraint NAME is hard-coded, so an
-- environment that renamed one cannot false-alarm section 4; every baseline is
-- taken inside this one transaction, which makes it comparable by
-- construction. This is 005a section 0, unchanged apart from the setting
-- names and the messages.
--
-- Idempotent: reads catalogues and writes nothing but settings local to this
-- transaction.
DO $$
DECLARE
    rls_on          BOOLEAN;
    policy_count    INTEGER;
    index_names     TEXT;
    fk_signatures   TEXT;
    trigger_count   INTEGER;
BEGIN
    IF to_regclass('public.strategies') IS NULL THEN
        RAISE EXCEPTION
            '015 precondition failed: table public.strategies does not exist. '
            'This table is not created by any migration in this repository - '
            'it is part of the pre-existing Supabase schema - so there is '
            'nothing to reconcile here: point this migration at the database '
            'that actually holds the strategies table.';
    END IF;

    -- last_signal_at records when a user's strategy last fired, which is a
    -- statement about their live trading activity. This file will not enable
    -- RLS itself - that would change a control this task must leave untouched
    -- - so it stops instead.
    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c
     WHERE c.oid = 'public.strategies'::regclass;

    IF NOT rls_on THEN
        RAISE EXCEPTION
            '015 refuses to run: row level security is DISABLED on '
            'public.strategies. This migration adds last_signal_at, which '
            'records when a user''s strategy last signalled - their live '
            'trading tempo. Adding it to a table without row-level isolation '
            'would let any authenticated caller read, and write, that fact '
            'about strategies they do not own. Apply rls_migration.sql (which '
            'enables RLS on public.strategies and creates the owner-scoped '
            '"strategies_authenticated_owner" policy) first. This file '
            'deliberately does not enable RLS itself.';
    END IF;

    SELECT count(*) INTO policy_count
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'strategies';

    IF policy_count = 0 THEN
        RAISE EXCEPTION
            '015 refuses to run: row level security is enabled on '
            'public.strategies but the table has NO policies, so it is '
            'unreachable to every non-superuser role and a last_signal_at '
            'written here could not be read back. Apply rls_migration.sql '
            'first.';
    END IF;

    -- The "nothing else changed" baselines, re-checked in section 4.
    -- indexname is of type "name"; cast to text explicitly rather than relying
    -- on an implicit name -> text coercion inside string_agg.
    SELECT coalesce(string_agg(indexname::TEXT, ',' ORDER BY indexname), '')
      INTO index_names
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'strategies';

    -- Every foreign key touching public.strategies in EITHER direction, each
    -- with its ON DELETE action (confdeltype: a = NO ACTION, r = RESTRICT,
    -- c = CASCADE, n = SET NULL, d = SET DEFAULT). If no signature moves, this
    -- file introduced no cascade and removed none.
    SELECT coalesce(string_agg(sig, ',' ORDER BY sig), '')
      INTO fk_signatures
      FROM (
        SELECT format('%s.%s:%s',
                      conrelid::regclass::text, conname, confdeltype) AS sig
          FROM pg_constraint
         WHERE contype = 'f'
           AND (conrelid  = 'public.strategies'::regclass
             OR confrelid = 'public.strategies'::regclass)
      ) fks;

    SELECT count(*) INTO trigger_count
      FROM pg_trigger
     WHERE tgrelid = 'public.strategies'::regclass
       AND NOT tgisinternal;

    PERFORM set_config('aerora.ls_policies_before', policy_count::TEXT,  true);
    PERFORM set_config('aerora.ls_indexes_before',  index_names,         true);
    PERFORM set_config('aerora.ls_fks_before',      fk_signatures,       true);
    PERFORM set_config('aerora.ls_triggers_before', trigger_count::TEXT, true);

    RAISE NOTICE '015 preflight: public.strategies has RLS enabled with % '
                 'policies, % indexes, % non-internal trigger(s) and % '
                 'foreign key(s) touching it. This file adds one nullable '
                 'column and one comment, changes none of them, and section 4 '
                 'verifies that.',
                 policy_count,
                 coalesce(array_length(string_to_array(nullif(index_names, ''), ','), 1), 0),
                 trigger_count,
                 coalesce(array_length(string_to_array(nullif(fk_signatures, ''), ','), 1), 0);
END $$;

-- 1) strategies.last_signal_at -------------------------------------------
-- design.md §7.2's "Last signal time" row, given a source.
--
--   last_signal_at  NULL means this strategy has never signalled; a timestamp
--                   means it has, and records when. Mirrors the
--                   public.signals.generated_at of that strategy's most
--                   recent signal row, written by
--                   backend_app/backend/strategy_last_signal.py after that
--                   row is confirmed persisted. Read by
--                   routers/strategies.py::list_strategies (BC-3) and by
--                   dashboard_aggregation_service.get_strategies, which has
--                   read this key as last_signal_time all along - so this file
--                   gives an EXISTING reader its first producer rather than
--                   introducing a second definition of "last signal".
--
--                   TIMESTAMPTZ, matching signals.generated_at, because the
--                   value is rendered to callers in other time zones and a
--                   naive timestamp would make the instant ambiguous.
--                   NULLABLE and with NO DEFAULT, both load-bearing - see
--                   section 2.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, so a re-run skips it. No DEFAULT and
-- no NOT NULL, so PostgreSQL rewrites no rows and no existing strategy starts
-- claiming it has signalled because this migration was applied.
ALTER TABLE public.strategies
    ADD COLUMN IF NOT EXISTS last_signal_at TIMESTAMPTZ NULL;

-- 2) Column shape assertion ----------------------------------------------
-- ADD COLUMN IF NOT EXISTS is SILENT when a column of that name already
-- exists with a DIFFERENT TYPE, NULLABILITY or DEFAULT - for instance one
-- added by hand during an investigation. With no CHECK constraint to fall
-- back on, the column's SHAPE is the entire two-state vocabulary, so that
-- silence would be severe rather than cosmetic:
--
--   * NOT NULL is unsatisfiable on a table of existing strategies, and would
--     mean this column can never say "never signalled" - the one thing
--     Requirement 19.2 requires it to be able to say.
--   * A DEFAULT of now() would make every strategy created from then on claim
--     it had signalled the moment it was saved. A trader would see a
--     brand-new draft reporting a live signal.
--   * A plain TIMESTAMP (without time zone) would make the recorded instant
--     ambiguous, and would disagree with signals.generated_at, which is
--     TIMESTAMPTZ.
--
-- None of the three is something this file can fix without an ALTER COLUMN,
-- which would be a destructive change to a pre-existing column of unknown
-- provenance. So this block reports the problem and refuses, rather than
-- leaving a timestamp whose meaning is automatic.
--
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    col_type    TEXT;
    col_null    TEXT;
    col_default TEXT;
BEGIN
    SELECT data_type, is_nullable, column_default
      INTO col_type, col_null, col_default
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name   = 'strategies'
       AND column_name  = 'last_signal_at';

    IF col_type IS NULL THEN
        RAISE EXCEPTION
            '015 failed: public.strategies.last_signal_at does not exist '
            'after ALTER TABLE ... ADD COLUMN IF NOT EXISTS. This should be '
            'unreachable; check for a same-named table in another schema '
            'earlier on search_path.';
    END IF;

    IF col_type <> 'timestamp with time zone' THEN
        RAISE EXCEPTION
            '015 refuses to proceed: public.strategies.last_signal_at has '
            'type "%" but must be "timestamp with time zone". A pre-existing '
            'column of that name was left exactly as it was, because ADD '
            'COLUMN IF NOT EXISTS does not alter one. A naive timestamp makes '
            'the recorded signal instant ambiguous and makes it disagree with '
            'public.signals.generated_at, the value it mirrors. Reconcile the '
            'column by hand, then re-run this migration.', col_type;
    END IF;

    IF col_null <> 'YES' THEN
        RAISE EXCEPTION
            '015 refuses to proceed: public.strategies.last_signal_at is NOT '
            'NULL. "Has never signalled" IS the NULL state (Requirement '
            '19.2), so a NOT NULL column could not express it, and satisfying '
            'the constraint would require asserting a signal instant for '
            'every strategy that has never produced one. A pre-existing '
            'column was left as it was; reconcile it by hand before '
            're-running this migration.';
    END IF;

    IF col_default IS NOT NULL THEN
        RAISE EXCEPTION
            '015 refuses to proceed: public.strategies.last_signal_at carries '
            'the DEFAULT "%", but it must have none. Any non-NULL default '
            'would make EVERY STRATEGY CREATED FROM NOW ON report a signal it '
            'never produced - a brand-new draft rendering as though it had '
            'just fired (Requirement 19.2 forbids exactly that fabrication). '
            'A pre-existing column was left as it was; drop the default by '
            'hand before re-running this migration.', col_default;
    END IF;
END $$;

-- 3) Column comment ------------------------------------------------------
-- Intent the schema cannot express, recorded where \d+ and every schema
-- browser will show it. COMMENT is naturally idempotent - it replaces.
--
-- No COMMENT is placed on the table itself or on any pre-existing column:
-- replacing one would be a change to something this file must leave alone.
COMMENT ON COLUMN public.strategies.last_signal_at IS
    'When this strategy last signalled, mirroring public.signals.generated_at of its most '
    'recent signal row. NULL means it has never signalled - the honest answer, never an '
    'epoch and never the strategy''s creation time (Requirement 19.2). Published by '
    'GET /api/strategies as last_signal_at (BC-3, vyomquant-ui-redesign task 12.3) and by '
    'the dashboard strategy projection as last_signal_time; both read this one key, so '
    'there is a single definition of "last signal". Written by '
    'backend_app/backend/strategy_last_signal.py AFTER the signal row is confirmed '
    'persisted and OFF the awaited signal path, so a failure to update this column can '
    'never fail or delay the signal that occasioned it - a signal recorded without its '
    'denormalised timestamp is acceptable, the reverse is not. Deliberately has NO CHECK '
    'constraint (a nullable timestamp is its own two-state vocabulary) and therefore MUST '
    'stay NULLABLE with NO DEFAULT: NOT NULL cannot express "never signalled", and a '
    'DEFAULT of now() would make every newly created strategy claim it had just fired. '
    'Denormalised rather than derived because public.signals is reachable only through '
    'PostgREST (no SQLAlchemy model), PostgREST''s grouped aggregates are disabled by '
    'default, and a view over signals would bypass that table''s RLS on this PostgreSQL '
    'baseline. Not to be confused with strategies.updated_at, which this file does not '
    'touch and which moves for any edit.';

-- 4) Nothing else changed ------------------------------------------------
-- Requirement 19.1's additive-only constraint, VERIFIED rather than promised:
-- the RLS state, the policy count, the non-internal trigger count, the index
-- inventory and every foreign-key signature recorded in section 0 must be
-- UNCHANGED. Unlike 005a, this file creates no index, so the index inventory
-- must match EXACTLY - there is no permitted addition to it.
--
-- The foreign-key check carries each constraint's confdeltype, so a changed
-- ON DELETE action on ANY key touching public.strategies - in either
-- direction - fails this block.
--
-- No object name is hard-coded, so a renamed policy, trigger or constraint in
-- some environment cannot false-alarm it; every baseline was taken inside this
-- one transaction.
--
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    policies_before INTEGER := current_setting('aerora.ls_policies_before')::INTEGER;
    triggers_before INTEGER := current_setting('aerora.ls_triggers_before')::INTEGER;
    indexes_before  TEXT    := current_setting('aerora.ls_indexes_before');
    fks_before      TEXT    := current_setting('aerora.ls_fks_before');
    policies_now    INTEGER;
    triggers_now    INTEGER;
    indexes_now     TEXT;
    fks_now         TEXT;
    rls_on          BOOLEAN;
    signalled_rows  BIGINT;
BEGIN
    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c
     WHERE c.oid = 'public.strategies'::regclass;

    IF NOT rls_on THEN
        RAISE EXCEPTION
            '015 postflight failed: row level security is no longer enabled '
            'on public.strategies. This file issues no ENABLE/DISABLE ROW '
            'LEVEL SECURITY, so this should be unreachable.';
    END IF;

    SELECT count(*) INTO policies_now
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'strategies';

    IF policies_now <> policies_before THEN
        RAISE EXCEPTION
            '015 postflight failed: public.strategies had % policies before '
            'this migration and has % now. This file issues no '
            'CREATE/ALTER/DROP POLICY.', policies_before, policies_now;
    END IF;

    SELECT count(*) INTO triggers_now
      FROM pg_trigger
     WHERE tgrelid = 'public.strategies'::regclass
       AND NOT tgisinternal;

    IF triggers_now <> triggers_before THEN
        RAISE EXCEPTION
            '015 postflight failed: public.strategies had % non-internal '
            'trigger(s) before this migration and has % now. This file '
            'creates no trigger - the last_signal_at write is application '
            'code, for the reasons in this file''s header.',
            triggers_before, triggers_now;
    END IF;

    SELECT coalesce(string_agg(indexname::TEXT, ',' ORDER BY indexname), '')
      INTO indexes_now
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'strategies';

    IF indexes_now <> indexes_before THEN
        RAISE EXCEPTION
            '015 postflight failed: the index inventory on public.strategies '
            'moved from [%] to [%]. This file creates and drops no index - '
            'nothing filters or orders by last_signal_at, so an index on it '
            'would add a write to the signal path for no read.',
            indexes_before, indexes_now;
    END IF;

    SELECT coalesce(string_agg(sig, ',' ORDER BY sig), '')
      INTO fks_now
      FROM (
        SELECT format('%s.%s:%s',
                      conrelid::regclass::text, conname, confdeltype) AS sig
          FROM pg_constraint
         WHERE contype = 'f'
           AND (conrelid  = 'public.strategies'::regclass
             OR confrelid = 'public.strategies'::regclass)
      ) fks;

    IF fks_now <> fks_before THEN
        RAISE EXCEPTION
            '015 postflight failed: the foreign keys touching '
            'public.strategies moved from [%] to [%], which includes their ON '
            'DELETE actions. This file adds and drops no constraint.',
            fks_before, fks_now;
    END IF;

    -- Applying this migration must report no signal for any strategy. On a
    -- first run every row is NULL; on a re-run this counts whatever the
    -- APPLICATION has written since, which this file neither adds to nor
    -- removes from (it contains no UPDATE).
    SELECT count(*) INTO signalled_rows
      FROM public.strategies
     WHERE last_signal_at IS NOT NULL;

    RAISE NOTICE '015 complete: public.strategies.last_signal_at exists, is '
                 'nullable, has no default and no index, and % row(s) carry a '
                 'non-NULL value (0 on a first run - this file back-fills '
                 'nothing). Policies, triggers, indexes and foreign keys are '
                 'unchanged. GET /api/strategies will now report a real '
                 'instant for a strategy that signals from here on, and null '
                 'for one that has not.', signalled_rows;
END $$;

COMMIT;

-- ==========================================================================
-- VERIFICATION - run these AFTER applying this file
-- ==========================================================================
--
-- 1. The column exists with exactly the required shape. Expect one row:
--    last_signal_at | timestamp with time zone | YES | (null default)
--
--   SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'public' AND table_name = 'strategies'
--     AND column_name = 'last_signal_at';
--
-- 2. Nothing claims a signal it did not produce. Expect 0 on a first run,
--    and thereafter only strategies that have actually signalled.
--
--   SELECT count(*) FROM public.strategies WHERE last_signal_at IS NOT NULL;
--
-- 3. No index was created on it, and no CHECK constraint mentions it.
--    Expect the index inventory that existed before this migration, and no
--    constraint definition containing "last_signal_at".
--
--   SELECT indexname, indexdef FROM pg_indexes
--   WHERE schemaname = 'public' AND tablename = 'strategies'
--   ORDER BY indexname;
--
--   SELECT conname, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--   WHERE conrelid = 'public.strategies'::regclass AND contype = 'c'
--   ORDER BY conname;
--
-- 4. Existing RLS UNTOUCHED. Expect the same policies that existed before,
--    still scoped to the owning user, and rowsecurity = true. RLS is
--    row-scoped, so those policies already cover last_signal_at: a user can
--    read and set it on their own strategies and no others.
--
--   SELECT policyname, cmd, roles, qual, with_check FROM pg_policies
--   WHERE schemaname = 'public' AND tablename = 'strategies'
--   ORDER BY policyname;
--
--   SELECT relrowsecurity, relforcerowsecurity
--   FROM pg_class WHERE oid = 'public.strategies'::regclass;
--
-- 5. The value the application writes agrees with the signal history it
--    mirrors. For any strategy that has signalled since this file was
--    applied, last_signal_at should equal the maximum generated_at among its
--    own signals. Expect zero rows back - each one that comes back is a
--    strategy whose denormalised timestamp is behind its signal history,
--    which is the acceptable failure mode this design chose (a signal is
--    never lost to keep this column current) and which query 6 repairs.
--
--   SELECT s.id, s.last_signal_at, m.max_generated_at
--   FROM public.strategies s
--   JOIN (SELECT strategy_id, max(generated_at) AS max_generated_at
--           FROM public.signals GROUP BY strategy_id) m
--     ON m.strategy_id = s.id
--   WHERE s.last_signal_at IS NULL
--      OR s.last_signal_at < m.max_generated_at;
--
-- 6. OPTIONAL, NOT PART OF THIS MIGRATION - seed the history, or repair a
--    drift found by query 5. Deliberately left to an operator running it by
--    hand with the cost in front of them: it writes one row per strategy that
--    has ever signalled, across every tenant, and this file's job is to add a
--    column rather than to rewrite tenant data (see "WHY NULLABLE" in the
--    header). It fabricates nothing - every value comes from a signals row
--    that exists - and it touches no strategy that has never signalled, so
--    "never signalled" stays NULL.
--
--   BEGIN;
--     UPDATE public.strategies s
--        SET last_signal_at = m.max_generated_at
--       FROM (SELECT strategy_id, max(generated_at) AS max_generated_at
--               FROM public.signals GROUP BY strategy_id) m
--      WHERE m.strategy_id = s.id
--        AND (s.last_signal_at IS NULL OR s.last_signal_at < m.max_generated_at);
--     -- inspect the count, then COMMIT or ROLLBACK
--   ROLLBACK;
--
-- 7. Idempotency. Re-running this whole file must report no error and leave
--    the results of 1, 3 and 4 unchanged, and must not change which rows have
--    a non-NULL last_signal_at (2).
