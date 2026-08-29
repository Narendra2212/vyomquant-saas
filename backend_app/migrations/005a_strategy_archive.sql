-- 005a_strategy_archive.sql  (migration 005, PART A)
--
-- PURPOSE
--   Give public.strategies somewhere to record THAT IT WAS ARCHIVED, AND WHEN,
--   so that "delete a strategy" can stop meaning "destroy the record of what
--   that strategy actually did".
--
--   Today DELETE /api/strategies/{id} performs a REAL row deletion
--   (design.md "Existing gaps", item 3), and every dependent table declares
--   its parent reference as
--
--       strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE
--
--   (001_strategy_architecture.sql lines 24, 73, 120; equivalently
--   003_signal_trace_restoration.sql). So one accepted delete today takes
--   the strategy's versions, its backtests, its deployments, its signals and
--   their signal_events with it - financial and audit history that
--   Requirement 3 exists to preserve.
--
--   This file lands the column and the index that let the delete path become
--   an UPDATE instead:
--
--     Requirement 3.2  a delete request that is accepted SHALL perform a
--                      soft delete (archival) rather than a hard row
--                      deletion, preserving the strategy's identifier, its
--                      versions, its backtests, its deployments and its
--                      signals.
--     Requirement 3.5  no strategy_backtests, strategy_versions,
--                      strategy_deployments or signals row SHALL be
--                      physically deleted as a side effect of archiving its
--                      parent strategy.
--     Requirement 21.6 WHEN a strategy is archived, the Persistence_Layer
--                      SHALL NOT cascade-delete any dependent row.
--     Requirement 21.4 row-level security, scoped to the owning user, on
--                      every table this specification extends - INCLUDING
--                      strategies as extended with archival state. See "WHY
--                      THIS FILE REFUSES TO RUN WITHOUT RLS" below.
--
--   3.5 and 21.6 are satisfied BY CONSTRUCTION rather than by a new
--   constraint: archiving is an UPDATE, and an UPDATE cascades nothing.
--   There is no ON DELETE clause to change, and this file changes none. The
--   existing CASCADE behaviour stays exactly as it is, for the case it was
--   written for - a genuine hard DELETE, which after task 5.1 the API no
--   longer issues.
--
-- SCOPE - THIS FILE IS PART A OF MIGRATION 005, AND ONLY THE strategies PART
--   Migration 005 is landed as two files, matching the two independent
--   schema changes this specification needs (tasks.md phases 2 and 3):
--
--     part A  task 2.1  strategies.archived_at + its partial index
--                                                            <- THIS FILE
--                       -> 005a_strategy_archive.sql
--     part B  task 3.1  public.signals: idempotency_key,
--                       order_lifecycle_state, their CHECK and their two
--                       indexes
--             task 3.2  order_lifecycle_transitions table
--             task 3.3  backfill of order_lifecycle_state
--                       -> 005b_signal_lifecycle_and_idempotency.sql
--
--   The two parts are independent: this file touches only public.strategies
--   and part B touches only public.signals and a new table, so they may be
--   applied in either order and neither depends on the other. The
--   "005<letter>" naming follows the precedent 004b through 004e already set
--   for migration 004, itself following 003's
--   (003_signal_trace_preflight.sql beside 003_signal_trace_restoration.sql).
--
--   Nothing is ever APPENDED to this file. Part A is one task's worth of DDL
--   and it is complete here, so unlike 005b - which reserves section markers
--   for tasks 3.2 and 3.3 - this file has no reserved sections. An operator
--   who has applied it once has applied all of part A.
--
-- SOURCE OF THE DEFINITION
--   design.md, section "`strategies` archival (Requirement 3)", gives both
--   statements verbatim:
--
--     ALTER TABLE strategies
--       ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL;
--
--     CREATE INDEX IF NOT EXISTS idx_strategies_archived_at ON strategies(archived_at)
--       WHERE archived_at IS NULL;
--
--   Both are reproduced below unchanged apart from schema-qualification
--   (public.strategies), which follows 004c/004e's convention rather than
--   001's unqualified form, so the file cannot land the column on a
--   same-named table in another schema on someone's search_path.
--
--   NOTHING ELSE the design specifies for this table is omitted, and nothing
--   the design does not specify is added to it.
--
-- WHY THERE IS NO CHECK CONSTRAINT
--   A nullable timestamp IS its own two-state vocabulary:
--
--     archived_at IS NULL       -> active
--     archived_at IS NOT NULL   -> archived, and the value says WHEN
--
--   There is no third state to forbid and no value to validate beyond "is it
--   set", so a CHECK here would be a constraint with an empty rejection set.
--   Contrast 004e's chk_sd_mode, which exists because ``mode`` is a TEXT
--   column whose vocabulary would otherwise be every string. This is also
--   why the column is deliberately NOT modelled as a boolean plus a separate
--   timestamp: that shape has two columns to keep in sync and a
--   representable contradiction (archived = false with a timestamp set),
--   which is exactly the class of defect this specification's design avoids
--   elsewhere ("no independent column to keep in sync", design.md "Existing
--   gaps", item 2).
--
--   The consequence is that the column's SHAPE is the whole control, which
--   is why section 2 asserts it rather than trusting it - see below.
--
-- archived_at IS NOT strategies.status, AND IT IS NOT is_read_only
--   public.strategies already carries, from 001_strategy_architecture.sql
--   section 6:
--
--     status         VARCHAR(20) DEFAULT 'draft'   (no CHECK, no fixed
--                                                   vocabulary in SQL)
--     is_published   BOOLEAN DEFAULT FALSE
--     is_subscribed  BOOLEAN DEFAULT FALSE
--     is_read_only   BOOLEAN DEFAULT FALSE
--     environment    VARCHAR(20) DEFAULT 'paper'
--
--   None of them is touched, constrained, backfilled or read by this file,
--   and archival is deliberately NOT expressed by adding an 'archived'
--   spelling to ``status``:
--
--     1. ``status`` has existing rows, an unfixed vocabulary and existing
--        writers. Overloading it would make "is this strategy archived" a
--        question whose answer depends on a string every one of those
--        writers can already set, and it would record no timestamp -
--        Requirement 3.2 wants the strategy's history preserved, and "when
--        was it archived" is part of that history.
--     2. It would be a DESTRUCTIVE reinterpretation of an existing column
--        rather than an additive change, which Requirement 27.4 forbids: the
--        schema changes made under this specification SHALL NOT drop, rename
--        or destructively alter any existing column. A new nullable column
--        is additive by construction; redefining ``status`` is not.
--     3. ``is_read_only`` is a cautionary precedent in this repository:
--        004c's header records that it is a real column no code path ever
--        sets, so a trigger gated on it alone would have been a silent
--        no-op. archived_at gets a writer in the same specification that
--        adds it (task 5.1's archive_strategy) and a reader in task 5.2's
--        ``include_archived`` listing, so it does not join that category.
--
-- WHAT THE PARTIAL INDEX IS FOR, AND WHY THE PREDICATE IS "IS NULL"
--   The default strategy list excludes archived strategies (Requirement 3.3,
--   task 5.2), so the hot query after task 5.1 ships is
--
--     SELECT ... FROM strategies WHERE user_id = ? AND archived_at IS NULL
--
--   A partial index whose predicate is ``archived_at IS NULL`` indexes only
--   the ACTIVE rows, which is both the smaller set and the one the default
--   listing reads. Indexing the archived rows instead would optimise the
--   audit view (rare) at the cost of the default view (every page load), and
--   a full index on the column would carry every archived row forever for no
--   read that wants them. This matches the shape 001 already uses on this
--   same table (idx_strategies_is_published ... WHERE is_published = TRUE).
--
--   This index does not replace idx_strategies_user_id (rls_migration.sql) -
--   it complements it, and neither is dropped or altered here.
--
-- WHY THIS FILE REFUSES TO RUN WITHOUT RLS ON strategies
--   archived_at is not merely descriptive: after task 5.1 it is the flag that
--   removes a strategy from its owner's active list, and Requirement 21.4
--   names strategies "as extended with archival state" as a table that SHALL
--   carry owner-scoped row-level security. A caller able to write this column
--   on a row they do not own can hide another user's strategy from that
--   user's Strategies_Page; a caller able to read it learns the archival
--   state of strategies that are not theirs.
--
--   So section 0 raises if relrowsecurity is false on public.strategies, or
--   if it is true with no policies at all (a state in which the table is
--   unreachable to every non-superuser role, so an archive written here
--   could not be read back). The remedy is to apply rls_migration.sql, which
--   enables RLS on this table and creates the owner-scoped
--   "strategies_authenticated_owner" policy, BEFORE this file.
--
--   THIS FILE DELIBERATELY DOES NOT ENABLE RLS ITSELF. Doing so would be a
--   change to the very control task 2.1 is told to leave untouched, and it
--   would silently paper over a misconfiguration an operator needs to see.
--   The refusal is a refusal to make an existing hole worse; it is not a
--   claim to fix one.
--
--   Row-level security is ROW-scoped, not column-scoped, so whatever
--   owner-scoped policies public.strategies already has keep applying
--   unchanged to archived_at: a user can read and set the archival state of
--   their own strategy rows and no others. Tenant isolation on this table is
--   exactly as strong after this migration as before it.
--
-- THE HARD CONSTRAINT: EXISTING RLS, INDEXES, TRIGGERS AND FOREIGN KEYS ARE
-- UNTOUCHED
--   Task 2.1's instruction, and Requirement 27.4's additive-only constraint.
--   This file contains NO DROP, NO TRUNCATE, NO DELETE, NO UPDATE, NO
--   ALTER COLUMN, NO CREATE/ALTER/DROP POLICY, NO ENABLE/DISABLE ROW LEVEL
--   SECURITY, NO DROP INDEX, NO CREATE/ALTER/DROP TRIGGER, NO
--   ADD/DROP CONSTRAINT, NO GRANT and NO REVOKE. It adds exactly one column,
--   one index and one column comment.
--
--   That is a fact about the text of the file. Section 5 turns it into a fact
--   about the DATABASE as well, without hard-coding a single object name:
--   section 0 records, in transaction-local settings, the policy count, the
--   full index-name inventory, the non-internal trigger count and the
--   signature of every foreign key that touches public.strategies - IN
--   EITHER DIRECTION, so both the FKs on strategies and the dependents'
--   ON DELETE CASCADE references TO strategies are covered, each with its
--   confdeltype (the ON DELETE action itself). Section 5 recomputes all four
--   and raises if any moved. The only permitted difference in the whole set
--   is the appearance of idx_strategies_archived_at.
--
--   That is the direct, checkable form of Requirements 3.5 and 21.6 at
--   migration time: if no FK's ON DELETE action changed, this migration
--   introduced no new cascade and removed none.
--
-- LOCKING AND COST
--   ALTER TABLE ... ADD COLUMN of a nullable column WITH NO DEFAULT is a
--   catalogue-only change - PostgreSQL rewrites no rows - so it holds
--   ACCESS EXCLUSIVE on public.strategies only momentarily.
--
--   CREATE INDEX (not CONCURRENTLY) holds a SHARE lock for the duration of
--   the build, blocking writes to strategies but not reads. CONCURRENTLY is
--   NOT used because it cannot run inside a transaction block, and this file
--   is deliberately one transaction (see SAFETY). For a strategies table
--   large enough for that build to matter, apply this file with section 3
--   commented out and then build the index separately:
--
--     CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_strategies_archived_at
--       ON public.strategies(archived_at) WHERE archived_at IS NULL;
--
--   then re-run this file, whose section 3 is a no-op once the index exists.
--
-- SAFETY
--   * Additive only. No statement in this file modifies a row: there is no
--     UPDATE, no DELETE and no DEFAULT, so applying it archives nothing and
--     un-archives nothing. Every existing strategy has archived_at NULL -
--     active - immediately after it runs.
--   * Fully idempotent. ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT
--     EXISTS, and COMMENT (which replaces). A re-run adds nothing and raises
--     nothing, and sections 0, 2 and 5 read catalogues without writing.
--   * One transaction. Either the column, the index and the comment all
--     exist, or none of them do.
--   * No DEFAULT on archived_at, and section 2 refuses to continue if one is
--     found. A DEFAULT of now() would archive every strategy created after
--     this migration - the single worst outcome reachable from this file -
--     and NOT NULL would mean every existing row is archived. Neither is
--     something ADD COLUMN IF NOT EXISTS would tell you about if the column
--     already existed in that shape, which is why the assertion exists.
--
-- APPLICATION
--   NOT applied automatically. .github/workflows/03-deploy.yml has no
--   migration step (its jobs are pre-deployment validation, ECR verify, ECS
--   deploy, reports), so migrations in this repository are applied BY HAND,
--   per file. Apply this file explicitly against the target database and then
--   run the verification queries at the bottom.
--
--   Until it is applied, archived_at does not exist. Every code path that
--   reads or writes it - task 5.1's archive_strategy above all - must
--   degrade the way the deployment-binding and registry-snapshot paths
--   already do: a WARNING NAMING THIS FILE ("005a_strategy_archive.sql"),
--   not a 500, and NEVER a strategy reported as archived when the archival
--   write did not happen. A missing column surfaces from PostgREST as an
--   undefined-column error on the UPDATE. Reporting success there would be
--   the worst possible degradation: the caller believes the strategy is gone
--   from their list while it is still live, and - since task 5.1 replaces the
--   hard delete - nothing else has removed it either. Task 5.2's
--   ``include_archived`` filter must likewise treat "column absent" as "no
--   strategy is archived" (its default list is then simply the full list)
--   rather than failing the listing outright.

BEGIN;

-- 0) Preflight -----------------------------------------------------------
-- Read-only assertions, plus four transaction-local baselines section 5
-- re-checks. Fails with a readable message naming the missing or
-- misconfigured object instead of a bare 42P01 from inside the ALTER.
--
-- No policy, index, trigger or constraint NAME is hard-coded here, so an
-- environment that renamed one cannot false-alarm section 5; every baseline
-- is taken inside this one transaction, which makes it comparable by
-- construction.
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
            '005 part A precondition failed: table public.strategies does '
            'not exist. This table is not created by any migration in this '
            'repository - it is part of the pre-existing Supabase schema '
            '(design.md: "given, not created by any migration in this '
            'repo") - so there is nothing to reconcile here: point this '
            'migration at the database that actually holds the strategies '
            'table.';
    END IF;

    -- archived_at is the flag that hides a strategy from its owner's active
    -- list (Requirement 3.3) and Requirement 21.4 names this table as one
    -- that SHALL carry owner-scoped row level security. This file will not
    -- enable RLS itself - that would change the control task 2.1 must leave
    -- untouched - so it stops instead.
    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c
     WHERE c.oid = 'public.strategies'::regclass;

    IF NOT rls_on THEN
        RAISE EXCEPTION
            '005 part A refuses to run: row level security is DISABLED on '
            'public.strategies. This migration adds the archival flag that '
            'removes a strategy from its owner''s active list, and '
            'Requirement 21.4 requires owner-scoped row level security on '
            'this table. Applying it to a table without row-level isolation '
            'would let any authenticated caller archive - hide - another '
            'user''s strategy. Apply rls_migration.sql (which enables RLS '
            'on public.strategies and creates the owner-scoped '
            '"strategies_authenticated_owner" policy) first. This file '
            'deliberately does not enable RLS itself.';
    END IF;

    SELECT count(*) INTO policy_count
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'strategies';

    IF policy_count = 0 THEN
        RAISE EXCEPTION
            '005 part A refuses to run: row level security is enabled on '
            'public.strategies but the table has NO policies, so it is '
            'unreachable to every non-superuser role and an archival write '
            'made here could not be read back. Apply rls_migration.sql '
            'first.';
    END IF;

    -- The "nothing else changed" baselines, re-checked in section 5.
    -- indexname is of type "name"; cast to text explicitly rather than
    -- relying on an implicit name -> text coercion inside string_agg.
    SELECT coalesce(string_agg(indexname::TEXT, ',' ORDER BY indexname), '')
      INTO index_names
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'strategies';

    -- Every foreign key touching public.strategies in EITHER direction,
    -- each with its ON DELETE action (confdeltype: a = NO ACTION,
    -- r = RESTRICT, c = CASCADE, n = SET NULL, d = SET DEFAULT). This is the
    -- checkable form of Requirements 3.5 and 21.6 at migration time: if no
    -- signature moves, this file introduced no cascade and removed none.
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

    PERFORM set_config('aerora.s_policies_before', policy_count::TEXT,  true);
    PERFORM set_config('aerora.s_indexes_before',  index_names,         true);
    PERFORM set_config('aerora.s_fks_before',      fk_signatures,       true);
    PERFORM set_config('aerora.s_triggers_before', trigger_count::TEXT, true);

    RAISE NOTICE '005 part A preflight: public.strategies has RLS enabled '
                 'with % policies, % indexes, % non-internal trigger(s) and '
                 '% foreign key(s) touching it. This file changes none of '
                 'them and section 5 verifies that.',
                 policy_count,
                 coalesce(array_length(string_to_array(nullif(index_names, ''), ','), 1), 0),
                 trigger_count,
                 coalesce(array_length(string_to_array(nullif(fk_signatures, ''), ','), 1), 0);
END $$;

-- 1) strategies.archived_at ----------------------------------------------
-- design.md "`strategies` archival (Requirement 3)", verbatim.
--
--   archived_at  NULL means active; a timestamp means archived and records
--                WHEN. Written only by task 5.1's archive_strategy
--                (UPDATE strategies SET archived_at = now() WHERE id = ?
--                AND user_id = ?), read by task 5.2's listing filter and by
--                every handler that must reject an archived strategy's
--                identifier (Requirement 3.3).
--
--                TIMESTAMPTZ, not TIMESTAMP: the value is compared against
--                now() and rendered to callers in other time zones, and a
--                naive timestamp would make "when was this archived"
--                ambiguous. NULLABLE and with NO DEFAULT, both load-bearing
--                - see section 2.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, so a re-run skips it. No DEFAULT and
-- no NOT NULL, so PostgreSQL rewrites no rows and no existing strategy
-- becomes archived by the act of applying this migration.
ALTER TABLE public.strategies
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL;

-- 2) Column shape assertion ----------------------------------------------
-- ADD COLUMN IF NOT EXISTS is SILENT when a column of that name already
-- exists with a DIFFERENT TYPE, NULLABILITY or DEFAULT - for instance one
-- added by hand during an investigation. Here that silence would be severe
-- rather than cosmetic, because with no CHECK constraint to fall back on the
-- column's SHAPE is the entire two-state vocabulary:
--
--   * NOT NULL would mean EVERY EXISTING STRATEGY IS ARCHIVED, since
--     "archived" is defined as "archived_at IS NOT NULL". The default list
--     (task 5.2) would come back empty for every user.
--   * A DEFAULT of now() (or of any non-NULL value) would ARCHIVE EVERY
--     STRATEGY CREATED FROM THEN ON - a strategy that vanishes from its
--     owner's list the moment it is saved.
--   * A plain TIMESTAMP (without time zone) would make the recorded archival
--     instant ambiguous and its comparison against now() dependent on the
--     session TimeZone.
--
-- None of the three is something this file can fix without an ALTER COLUMN,
-- which task 2.1 forbids (and which on a pre-existing column of unknown
-- provenance would be a destructive change under Requirement 27.4). So this
-- block reports the problem and refuses, rather than leaving a soft-delete
-- flag whose meaning is inverted or automatic.
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
       AND column_name  = 'archived_at';

    IF col_type IS NULL THEN
        RAISE EXCEPTION
            '005 part A failed: public.strategies.archived_at does not '
            'exist after ALTER TABLE ... ADD COLUMN IF NOT EXISTS. This '
            'should be unreachable; check for a same-named table in another '
            'schema earlier on search_path.';
    END IF;

    IF col_type <> 'timestamp with time zone' THEN
        RAISE EXCEPTION
            '005 part A refuses to proceed: public.strategies.archived_at '
            'has type "%" but must be "timestamp with time zone". A '
            'pre-existing column of that name was left exactly as it was, '
            'because ADD COLUMN IF NOT EXISTS does not alter one. A naive '
            'timestamp makes the recorded archival instant ambiguous and '
            'its comparison against now() dependent on the session '
            'TimeZone. Reconcile the column by hand, then re-run this '
            'migration.', col_type;
    END IF;

    IF col_null <> 'YES' THEN
        RAISE EXCEPTION
            '005 part A refuses to proceed: public.strategies.archived_at '
            'is NOT NULL, but "archived" is defined as archived_at IS NOT '
            'NULL, so a NOT NULL column would mean EVERY EXISTING STRATEGY '
            'IS ARCHIVED and the default (non-archived) list would be empty '
            'for every user. A pre-existing column was left as it was; '
            'reconcile it by hand before re-running this migration.';
    END IF;

    IF col_default IS NOT NULL THEN
        RAISE EXCEPTION
            '005 part A refuses to proceed: public.strategies.archived_at '
            'carries the DEFAULT "%", but it must have none. Any non-NULL '
            'default would ARCHIVE EVERY STRATEGY CREATED FROM NOW ON - the '
            'strategy would disappear from its owner''s list the moment it '
            'was saved. A pre-existing column was left as it was; drop the '
            'default by hand before re-running this migration.', col_default;
    END IF;
END $$;

-- 3) idx_strategies_archived_at -------------------------------------------
-- design.md "`strategies` archival (Requirement 3)", verbatim: a PARTIAL
-- index on the active rows, which is what the default listing reads
-- (Requirement 3.3, task 5.2:
--   WHERE user_id = ? AND archived_at IS NULL).
--
-- Same shape 001_strategy_architecture.sql already uses on this table
-- (idx_strategies_is_published ... WHERE is_published = TRUE). It
-- complements idx_strategies_user_id (rls_migration.sql) and replaces
-- nothing: no index is dropped or altered anywhere in this file.
--
-- Idempotent: CREATE INDEX IF NOT EXISTS. See "LOCKING AND COST" in the
-- header for the CONCURRENTLY escape hatch on a large table.
CREATE INDEX IF NOT EXISTS idx_strategies_archived_at
    ON public.strategies(archived_at)
    WHERE archived_at IS NULL;

-- 4) Column comment ------------------------------------------------------
-- Intent the schema cannot express, recorded where \d+ and every schema
-- browser will show it. COMMENT is naturally idempotent - it replaces.
--
-- No COMMENT is placed on the table itself or on any pre-existing column:
-- replacing one would be a change to something task 2.1 leaves alone.
COMMENT ON COLUMN public.strategies.archived_at IS
    'Soft-delete (archival) marker. NULL means active; a timestamp means archived and '
    'records when. Set by archive_strategy on DELETE /api/strategies/{id}, which performs '
    'an UPDATE and never a row deletion (Requirement 3.2), so no strategy_versions, '
    'strategy_backtests, strategy_deployments, signals or signal_events row is ever '
    'cascade-deleted by archiving (Requirements 3.5, 21.6). Deliberately has NO CHECK '
    'constraint - a nullable timestamp is its own two-state vocabulary - and therefore MUST '
    'stay nullable with no DEFAULT: NOT NULL would mean every strategy is archived, and a '
    'DEFAULT of now() would archive every strategy created from then on. Not to be confused '
    'with strategies.status, which this specification does not touch.';

-- 5) Nothing else changed ------------------------------------------------
-- Task 2.1's hard constraint and Requirement 27.4's additive-only
-- constraint, VERIFIED rather than promised: the RLS state, the policy
-- count, the non-internal trigger count and every foreign-key signature
-- recorded in section 0 must be unchanged, and the index inventory may
-- differ by exactly one new name - idx_strategies_archived_at.
--
-- The foreign-key check is the one that matters most: each signature carries
-- the constraint's confdeltype, so a changed ON DELETE action on ANY key
-- touching public.strategies - in either direction - fails this block. That
-- is Requirements 3.5 and 21.6 checked at migration time rather than
-- asserted in a comment.
--
-- No object name is hard-coded except the index this file creates, so a
-- renamed policy, trigger or constraint in some environment cannot
-- false-alarm it; every baseline was taken inside this one transaction.
--
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    policies_before INTEGER := current_setting('aerora.s_policies_before')::INTEGER;
    triggers_before INTEGER := current_setting('aerora.s_triggers_before')::INTEGER;
    indexes_before  TEXT[]  := string_to_array(
                                   nullif(current_setting('aerora.s_indexes_before'), ''), ',');
    fks_before      TEXT[]  := string_to_array(
                                   nullif(current_setting('aerora.s_fks_before'), ''), ',');
    policies_now    INTEGER;
    triggers_now    INTEGER;
    indexes_now     TEXT[];
    fks_now         TEXT[];
    lost            TEXT[];
    gained          TEXT[];
    rls_on          BOOLEAN;
    index_def       TEXT;
    archived_rows   BIGINT;
BEGIN
    indexes_before := coalesce(indexes_before, ARRAY[]::TEXT[]);
    fks_before     := coalesce(fks_before,     ARRAY[]::TEXT[]);

    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c
     WHERE c.oid = 'public.strategies'::regclass;

    SELECT count(*) INTO policies_now
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'strategies';

    SELECT count(*) INTO triggers_now
      FROM pg_trigger
     WHERE tgrelid = 'public.strategies'::regclass
       AND NOT tgisinternal;

    SELECT coalesce(array_agg(indexname::TEXT ORDER BY indexname), ARRAY[]::TEXT[])
      INTO indexes_now
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'strategies';

    SELECT coalesce(array_agg(sig ORDER BY sig), ARRAY[]::TEXT[])
      INTO fks_now
      FROM (
        SELECT format('%s.%s:%s',
                      conrelid::regclass::text, conname, confdeltype) AS sig
          FROM pg_constraint
         WHERE contype = 'f'
           AND (conrelid  = 'public.strategies'::regclass
             OR confrelid = 'public.strategies'::regclass)
      ) fks;

    IF NOT rls_on THEN
        RAISE EXCEPTION
            '005 part A postcondition failed: row level security on '
            'public.strategies is no longer enabled.';
    END IF;

    IF policies_now <> policies_before THEN
        RAISE EXCEPTION
            '005 part A postcondition failed: the policy count on '
            'public.strategies moved from % to %. This file must leave the '
            'existing RLS untouched (task 2.1).',
            policies_before, policies_now;
    END IF;

    IF triggers_now <> triggers_before THEN
        RAISE EXCEPTION
            '005 part A postcondition failed: the non-internal trigger '
            'count on public.strategies moved from % to %. This file must '
            'leave the existing triggers untouched (task 2.1).',
            triggers_before, triggers_now;
    END IF;

    -- Foreign keys: Requirements 3.5 and 21.6. Both directions, including
    -- each key's ON DELETE action.
    SELECT coalesce(array_agg(s), ARRAY[]::TEXT[]) INTO lost
      FROM unnest(fks_before) AS s WHERE NOT (s = ANY (fks_now));
    SELECT coalesce(array_agg(s), ARRAY[]::TEXT[]) INTO gained
      FROM unnest(fks_now) AS s WHERE NOT (s = ANY (fks_before));

    IF array_length(lost, 1) IS NOT NULL OR array_length(gained, 1) IS NOT NULL THEN
        RAISE EXCEPTION
            '005 part A postcondition failed: the foreign keys touching '
            'public.strategies changed. Removed or altered: %. Added: %. '
            'Each entry is table.constraint:on_delete_action, so a moved '
            'entry means a cascade rule changed - which this file must not '
            'do (Requirements 3.5, 21.6; task 2.1).',
            lost, gained;
    END IF;

    -- Indexes: everything that existed must still exist, and the only
    -- addition allowed is this file's own partial index.
    SELECT coalesce(array_agg(s), ARRAY[]::TEXT[]) INTO lost
      FROM unnest(indexes_before) AS s WHERE NOT (s = ANY (indexes_now));
    SELECT coalesce(array_agg(s), ARRAY[]::TEXT[]) INTO gained
      FROM unnest(indexes_now) AS s WHERE NOT (s = ANY (indexes_before));

    IF array_length(lost, 1) IS NOT NULL THEN
        RAISE EXCEPTION
            '005 part A postcondition failed: index(es) % disappeared from '
            'public.strategies. This file must leave the existing indexes '
            'untouched (task 2.1).', lost;
    END IF;

    IF array_length(gained, 1) IS NOT NULL
       AND gained <> ARRAY['idx_strategies_archived_at'] THEN
        RAISE EXCEPTION
            '005 part A postcondition failed: unexpected new index(es) % on '
            'public.strategies. This file creates exactly one, '
            'idx_strategies_archived_at.', gained;
    END IF;

    SELECT indexdef INTO index_def
      FROM pg_indexes
     WHERE schemaname = 'public'
       AND tablename  = 'strategies'
       AND indexname  = 'idx_strategies_archived_at';

    IF index_def IS NULL THEN
        RAISE EXCEPTION
            '005 part A postcondition failed: idx_strategies_archived_at '
            'does not exist after CREATE INDEX IF NOT EXISTS.';
    END IF;

    -- A pre-existing index of the same name that is NOT partial is reported,
    -- not rejected: an index is a performance aid here, not a control, and
    -- dropping and rebuilding someone else's index would be exactly the
    -- destructive change task 2.1 forbids. The migration is still correct -
    -- only the default listing is less selective than intended.
    IF position('WHERE (archived_at IS NULL)' IN index_def) = 0 THEN
        RAISE NOTICE '005 part A: idx_strategies_archived_at already existed '
                     'in a different form and was NOT altered. Its '
                     'definition is: %. design.md specifies the partial form '
                     '"... ON strategies(archived_at) WHERE archived_at IS '
                     'NULL"; consider reconciling it by hand.', index_def;
    END IF;

    -- This file contains no UPDATE and the new column has no DEFAULT, so
    -- nothing here can archive a strategy. On a first run this must be 0; on
    -- a re-run it is however many strategies task 5.1 has archived since.
    SELECT count(*) INTO archived_rows
      FROM public.strategies WHERE archived_at IS NOT NULL;

    RAISE NOTICE '005 part A complete: archived_at (nullable TIMESTAMPTZ, no '
                 'default) and idx_strategies_archived_at added; RLS still '
                 'enabled with % policies, % trigger(s) and % foreign '
                 'key(s), all unchanged. % strategy row(s) currently '
                 'archived (0 on a first run - this file archives nothing).',
                 policies_now, triggers_now,
                 coalesce(array_length(fks_now, 1), 0), archived_rows;
END $$;

COMMIT;

-- VERIFICATION (run after applying) --------------------------------------
--
-- 1. The column. Expect exactly 1 row, with
--    data_type = 'timestamp with time zone', is_nullable = 'YES' and
--    column_default IS NULL.
--
--    None of the three is cosmetic. There is no CHECK constraint on this
--    column by design, so its shape IS the two-state vocabulary: NOT NULL
--    would mean every strategy is archived, and any non-NULL default would
--    archive every strategy created from then on.
--
--   SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'public'
--     AND table_name   = 'strategies'
--     AND column_name  = 'archived_at';
--
-- 2. The partial index. Expect exactly 1 row whose indexdef ends with
--    "WHERE (archived_at IS NULL)".
--
--   SELECT indexname, indexdef
--   FROM pg_indexes
--   WHERE schemaname = 'public' AND tablename = 'strategies'
--     AND indexname  = 'idx_strategies_archived_at';
--
--    And it should actually be used by the default listing shape:
--
--   EXPLAIN SELECT id FROM public.strategies
--    WHERE user_id = '00000000-0000-0000-0000-000000000000'
--      AND archived_at IS NULL;
--
-- 3. No CHECK constraint was added to this table by this migration - the
--    two-state vocabulary needs none. Expect the same CHECK constraints
--    that existed before (a before/after comparison, not an expected list),
--    and in particular NOTHING mentioning archived_at.
--
--   SELECT conname, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--   WHERE conrelid = 'public.strategies'::regclass AND contype = 'c'
--   ORDER BY conname;
--
-- 4. Nothing was archived by applying this migration. Expect 0 on a first
--    run.
--
--   SELECT count(*) FROM public.strategies WHERE archived_at IS NOT NULL;
--
-- 5. Requirements 3.5 and 21.6: EVERY foreign key touching this table is
--    UNCHANGED, including its ON DELETE action. Expect the dependents'
--    references to still read ON DELETE CASCADE exactly as
--    001_strategy_architecture.sql declared them - archiving does not
--    cascade because archiving is an UPDATE, NOT because this migration
--    weakened a cascade rule.
--
--   SELECT conrelid::regclass AS on_table, conname,
--          pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--   WHERE contype = 'f'
--     AND (conrelid  = 'public.strategies'::regclass
--       OR confrelid = 'public.strategies'::regclass)
--   ORDER BY on_table::text, conname;
--
--    Exercised directly - an archive must leave every dependent row in
--    place:
--
--   BEGIN;
--     -- pick any strategy that has dependents
--     UPDATE public.strategies SET archived_at = now()
--      WHERE id = (SELECT strategy_id FROM public.strategy_versions LIMIT 1);
--     -- expect the same counts as before the UPDATE
--     SELECT (SELECT count(*) FROM public.strategy_versions)     AS versions,
--            (SELECT count(*) FROM public.strategy_backtests)    AS backtests,
--            (SELECT count(*) FROM public.strategy_deployments)  AS deployments,
--            (SELECT count(*) FROM public.signals)               AS signals;
--   ROLLBACK;
--
-- 6. Existing RLS UNTOUCHED (task 2.1's hard constraint). Expect the same
--    policies that existed before this migration, still scoped to the
--    owning user, and rowsecurity = true. A policy whose qual is no longer
--    owner-scoped would mean a control was widened.
--
--   SELECT policyname, cmd, roles, qual, with_check
--   FROM pg_policies
--   WHERE schemaname = 'public' AND tablename = 'strategies'
--   ORDER BY policyname;
--
--   SELECT relrowsecurity, relforcerowsecurity
--   FROM pg_class WHERE oid = 'public.strategies'::regclass;
--
--    RLS is row-scoped, so those policies already cover archived_at: a user
--    can set the archival state of their own strategies and no others.
--
-- 7. Existing indexes and triggers UNTOUCHED. Expect every index that
--    existed before plus idx_strategies_archived_at, and the same
--    non-internal triggers.
--
--   SELECT indexname, indexdef
--   FROM pg_indexes
--   WHERE schemaname = 'public' AND tablename = 'strategies'
--   ORDER BY indexname;
--
--   SELECT tgname FROM pg_trigger
--   WHERE tgrelid = 'public.strategies'::regclass AND NOT tgisinternal
--   ORDER BY tgname;
--
-- 8. Grants UNTOUCHED. This file issues no GRANT and no REVOKE, so this is
--    a before/after comparison rather than an expected list.
--
--   SELECT grantee, privilege_type
--   FROM information_schema.role_table_grants
--   WHERE table_schema = 'public' AND table_name = 'strategies'
--   ORDER BY grantee, privilege_type;
--
-- 9. The pre-existing status/flag columns are untouched: same types, same
--    defaults, same values. Archival is NOT expressed in status.
--
--   SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'public' AND table_name = 'strategies'
--     AND column_name IN ('status','is_published','is_subscribed',
--                         'is_read_only','environment')
--   ORDER BY column_name;
--
-- 10. Idempotency. Re-running this whole file must report no error and
--     leave the results of 1, 2, 3, 5, 6, 7, 8 and 9 unchanged, and must
--     not change which rows have a non-NULL archived_at (4).
