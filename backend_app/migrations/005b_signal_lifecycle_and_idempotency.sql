-- 005b_signal_lifecycle_and_idempotency.sql  (migration 005, PART B)
--
-- PURPOSE
--   Give public.signals the two columns the canonical order lifecycle needs,
--   and give the database itself the two guarantees the application cannot
--   make alone:
--
--     1. A signal row can name its canonical Order_Lifecycle_State, drawn
--        from ONE vocabulary of exactly 9 values, enforced by a CHECK
--        rather than by convention. Requirements 16.1, 16.6, 21.7.
--     2. An Idempotency_Key recorded against a signal is UNIQUE, so a
--        second submission attempt for the same signal cannot become a
--        second order row even if Redis is flushed or a key expires
--        mid-flight. Requirements 19.1, 21.2, 21.7.
--
--   Source of the definition: design.md "Signals table extensions
--   (Requirements 16, 19, 21)", statement for statement, schema-qualified
--   with public. and with each ALTER ... ADD CONSTRAINT put behind a
--   pg_constraint guard (see DELIBERATE ADDITIONS below).
--
--   The 9-value vocabulary is NOT invented here. It is transcribed from
--   backend_app/backend/order_lifecycle_state.py's
--   ORDER_LIFECYCLE_STATE_VALUES (task 1.1), in that tuple's own order:
--
--     GENERATED, PENDING, SUBMITTED, PARTIALLY_EXECUTED, EXECUTED,
--     CLOSED, FAILED, CANCELLED, REJECTED
--
--   That module's docstring already names this file as the consumer of that
--   tuple ("This is the list the 005b migration's
--   chk_signals_order_lifecycle_state ... are written from, so the
--   constraint and the enum cannot disagree"). If a tenth value is ever
--   added there (Requirement 27.2 permits additive-only growth), this
--   constraint is what must be widened in a new migration file - never
--   edited in place here, for the reason under APPLICATION below.
--
-- SCOPE - THIS FILE IS PART B OF MIGRATION 005, AND ONLY THE signals PART
--   Migration 005 is landed incrementally, matching the task that needs
--   each piece (tasks.md "3. Land migration 005b"):
--
--     part A  task 2.1  strategies.archived_at + its partial index
--                       -> 005a_strategy_archive.sql
--     part B  task 3.1  public.signals: idempotency_key,
--                       order_lifecycle_state, their CHECK and their two
--                       indexes                              <- SECTION 1
--             task 3.2  order_lifecycle_transitions table     <- SECTION 2
--             task 3.3  backfill of order_lifecycle_state     <- SECTION 3
--
--   Sections 2 and 3 are appended to THIS file by tasks 3.2 and 3.3. The
--   section markers at the bottom say exactly where, and section 1 closes
--   its own transaction so an appended section cannot widen section 1's
--   commit boundary or be silently skipped by an operator who applied this
--   file before the append landed. Each section is independently
--   re-runnable, so applying the whole file again after an append is safe
--   and is the intended way to pick the appended part up.
--
--   Order: this file needs only public.signals, which
--   002_signal_trace.sql and 003_signal_trace_restoration.sql both create.
--   It does not depend on part A and part A does not depend on it.
--
-- WHY A SEPARATE FILE RATHER THAN AN EDIT TO 002/003
--   The same reason 004b/004c/004d/004e state for migration 004:
--   migrations here are applied BY HAND, per file, and NOTHING RECORDS
--   WHICH FILES AN ENVIRONMENT HAS RUN - there is no migration table and
--   .github/workflows/03-deploy.yml has no migration step. Appending to a
--   file an operator may already have applied leaves no signal that the
--   file changed, so the new statements would simply never run. A new
--   filename is the signal. 002 and 003 are left byte-identical.
--
-- FOUR DELIBERATE ADDITIONS BEYOND THE DESIGN'S DDL, AND WHY
--   The design snippet is a schema sketch. These make it safe to apply by
--   hand, twice, to a database whose history nobody recorded. None weakens
--   a control and none changes a column, constraint, index or policy the
--   design specifies.
--
--   1. Preflight assertions (section 1.0). A readable RAISE naming the
--      missing object instead of a bare 42P01 from inside an ALTER, plus a
--      refusal to run without row-level security on public.signals (see
--      below), plus the policy/index/trigger inventory section 1.6
--      re-checks.
--
--   2. A guarded ADD CONSTRAINT block (section 1.3). The design writes
--      "ALTER TABLE signals ADD CONSTRAINT chk_signals_order_lifecycle_state
--      CHECK (...)" bare, which is NOT idempotent: a second run raises
--      42710 duplicate_object and aborts the whole file. PostgreSQL has no
--      ADD CONSTRAINT IF NOT EXISTS, and DROP-then-ADD would both violate
--      this file's no-DROP rule and open a window inside the transaction
--      where the vocabulary is unenforced. Same pattern as
--      004e section 3 and 004d section 1b.
--
--   3. A column shape assertion (section 1.2). ADD COLUMN IF NOT EXISTS is
--      SILENT when a column of that name already exists with a different
--      type - for instance one added by hand during an investigation. That
--      matters here twice over: a non-text order_lifecycle_state would
--      make the CHECK's IN-list comparison a cast, and a non-text
--      idempotency_key added with data already in it could make section
--      1.4's UNIQUE index fail with 23505 instead of being created.
--
--   4. Column comments (section 1.5) and a postflight (section 1.6) that
--      proves the five objects exist and that this file added no policy,
--      no trigger and no index beyond its own two.
--
-- THE CHECK IS NULL-PERMISSIVE UNTIL TASK 3.3 RUNS, AND THAT IS DELIBERATE
--   READ THIS BEFORE ASSUMING order_lifecycle_state IS ALWAYS ONE OF THE 9:
--
--     A CHECK CONSTRAINT THAT EVALUATES TO NULL PASSES.
--
--   order_lifecycle_state is added NULLABLE and with NO DEFAULT, so every
--   pre-existing signal row has NULL in it the moment this file commits,
--   and "NULL IN (...)" is NULL, so chk_signals_order_lifecycle_state
--   ADMITS every one of those rows. That is what makes this migration
--   applicable to a table with existing history at all, and it is what
--   Requirement 27.4's "nullable-only migration columns" constraint asks
--   for. It also means:
--
--     * The constraint fixes the vocabulary for every NON-NULL value, and
--       fixes nothing for a NULL one. Requirement 16.1's "exactly 9 values"
--       becomes total over public.signals only once TASK 3.3's backfill has
--       mapped every legacy signals.status through SIGNALS_STATUS_MAP and
--       left no row NULL. Until then, a reader MUST treat NULL as
--       "not yet reconciled", never as a tenth state and never as a default.
--     * No NOT NULL is added here, and none may be added until that
--       backfill is verified complete in the target database - adding it
--       earlier would fail the ALTER against existing rows, or worse, force
--       a fabricated DEFAULT onto financial history.
--
--   The legacy signals.status column is NOT touched, NOT constrained and
--   NOT dropped by this file. It has existing rows, an index
--   (idx_signals_status), a composite index (idx_signals_user_status) and
--   live writers in signal_service.py. order_lifecycle_state is the
--   canonical column Requirement 16.2 asks for; status stays as the source
--   the backfill reads and as the column existing readers keep reading
--   until they are migrated.
--
-- WHY THE UNIQUE INDEX IS PARTIAL, AND WHAT IT IS FOR
--   uq_signals_idempotency_key is declared
--   "WHERE idempotency_key IS NOT NULL". In PostgreSQL a plain UNIQUE index
--   already treats NULLs as distinct, so the predicate is not what makes
--   many NULL keys legal - it is what keeps the index from carrying an
--   entry for every signal that never reached submission (a HOLD decision,
--   a risk rejection, a signal still at GENERATED). Those rows are the
--   majority and none of them has a key to enforce.
--
--   Its job is stated plainly in design.md "Idempotency key scheme": this
--   index is the DURABLE BACKSTOP behind DistributedIdempotencyLayer's
--   Redis lock. Redis holds the fast path; if a key expires mid-flight or
--   the store is flushed, a second INSERT carrying the same
--   idempotency_key fails HERE, at the database, with 23505 - which is
--   exactly Requirement 19.1's "at most one order at the exchange" and
--   Requirement 21.2's "a unique Idempotency_Key per order submission ...
--   no two rows SHALL share the same Idempotency_Key". Requirement 21.7
--   then requires the API to surface which constraint was violated rather
--   than a bare 500: THE CALLER OF THAT INSERT (task 10.1/10.2's
--   signal_service) MUST TRANSLATE 23505 ON uq_signals_idempotency_key
--   INTO DuplicateOrderError AND RETURN THE SIGNAL'S CURRENT
--   Order_Lifecycle_State, per design.md's submit_signal algorithm. This
--   index is not a hint the application may ignore.
--
--   The key's own shape is application-side and NOT constrained here:
--   idempotency_key_for(signal) = "signal:" + signal.id (task 9.1). No
--   CHECK on its format is added, because the design specifies none and a
--   format constraint would reject a key produced by a writer not yet
--   revised for it. It carries no credential and no exchange identity
--   (Requirement 20.3): it is derived from the signal's own id and nothing
--   else.
--
-- WHY THIS FILE REFUSES TO RUN WITHOUT RLS ON public.signals
--   Both new columns sit on rows that record a user's real trading
--   decisions, and idempotency_key is the handle by which a submission is
--   deduplicated. Adding them to a table whose row-level isolation is off
--   would let any authenticated caller read another user's signals - and
--   probe their idempotency keys through 23505 on this very index, which
--   would turn the durable backstop into a cross-tenant oracle. So
--   section 1.0 raises if relrowsecurity is false, rather than proceeding.
--   That is a refusal to make an existing hole worse, not a claim to fix
--   one: the remedy is to apply 003_signal_trace_restoration.sql sections
--   8 and 9 (or 002_signal_trace.sql) first. This file deliberately does
--   NOT enable RLS itself and does NOT create, alter or drop a policy -
--   Requirement 21.4's RLS on public.signals already exists and the one
--   rule this plan repeats everywhere is that no task weakens or rewrites
--   an existing control.
--
--   Row level security is ROW-scoped, not column-scoped, so the three
--   existing owner policies keep applying unchanged to both new columns.
--   Tenant isolation on public.signals is exactly as strong after this
--   migration as before it.
--
-- WHAT REQUIREMENT 21 ALREADY HAS, AND IS THEREFORE NOT REPEATED HERE
--   21.1 (FKs to the owning strategy and user), 21.4 (RLS) and 21.5
--   (generated_at / updated_at plus
--   trigger_update_signals_updated_at) are already on public.signals from
--   002/003. This file adds nothing to them and touches none of them; the
--   existing BEFORE UPDATE trigger keeps maintaining updated_at for writes
--   to the two new columns too, which is why no second trigger is attached
--   (a second one would double-fire).
--
--   21.3's index list for signals is served by 002/003 for strategy_id,
--   deployment_id, symbol, decision (side) and generated_at. This file adds
--   the two it names for the new column plus ONE index the design's snippet
--   omits - see section 1.4's note on idx_signals_strategy_version, which
--   is the only object in this file not transcribed from design.md, is
--   additive, and is justified there rather than assumed.
--
-- APPLICATION
--   NOT applied automatically. Apply this file explicitly against the
--   target database, then run the verification queries at the bottom, the
--   way scripts/forensics/apply_migration_007.py applied 007.
--
--   Until it is applied, these two columns do not exist. Every code path
--   that reads or writes them must degrade the way the training and
--   registry-snapshot surfaces already do: a WARNING NAMING THIS FILE
--   ("005b_signal_lifecycle_and_idempotency.sql"), not a 500, and NEVER a
--   signal reported as submitted-with-idempotency when the key was not
--   stored. A missing column surfaces from PostgREST as an
--   undefined-column error on the insert.
--
-- SAFETY
--   * Additive only. No DROP, no TRUNCATE, no DELETE, no UPDATE, no
--     ALTER COLUMN TYPE, no CREATE/ALTER/DROP POLICY, no
--     ENABLE/DISABLE ROW LEVEL SECURITY, no DROP INDEX, no CREATE TRIGGER,
--     no GRANT, no REVOKE. No statement in section 1 modifies a row.
--   * Fully idempotent. ADD COLUMN IF NOT EXISTS for both columns; the
--     constraint behind a pg_constraint existence guard; CREATE INDEX
--     IF NOT EXISTS for all three indexes; COMMENT replaces. A re-run adds
--     nothing and raises nothing.
--   * One transaction per section. Either both columns, the constraint,
--     the three indexes and the comments exist, or none of them do. A
--     signals table carrying order_lifecycle_state without
--     chk_signals_order_lifecycle_state is never visible to a session.
--   * No existing row is rewritten. Reconciling legacy status values is
--     task 3.3's job, is application code by design (so SIGNALS_STATUS_MAP
--     lives in exactly one place), and is NOT smuggled into this file as an
--     UPDATE.
--
-- WHAT IS DELIBERATELY NOT ADDED
--   * No NOT NULL and no DEFAULT on order_lifecycle_state. Reasons under
--     "THE CHECK IS NULL-PERMISSIVE" above.
--   * No CHECK, no rename and no drop on the legacy signals.status column.
--   * No trigger enforcing Requirement 16.4's transition reachability. The
--     gate is order_lifecycle_state.assert_transition_legal (task 1.1),
--     called before the write by task 10.2, which is what lets the caller
--     receive Requirement 16.6's "error indication identifying the
--     rejected transition" instead of a 23514 surfacing as a 500. A
--     database trigger cannot see the prior value in an INSERT and cannot
--     name the rejected transition to the API caller; design.md places the
--     gate in the application for exactly that reason.
--   * No format CHECK on idempotency_key. Reasons above.
--   * No order_lifecycle_transitions table and no backfill in section 1 -
--     those are tasks 3.2 and 3.3, appended as sections 2 and 3.

-- ==========================================================================
-- SECTION 1 - task 3.1: public.signals lifecycle state + idempotency key
-- ==========================================================================

BEGIN;

-- 1.0) Preflight -------------------------------------------------------
-- Read-only assertions plus three transaction-local counters. Fails with a
-- readable message instead of a bare undefined_table error, refuses to add
-- trading-record columns to a table without row-level security, and records
-- the policy/index/trigger inventory so section 1.6 can prove this file
-- left all three alone (no NAME is hard-coded, so an environment that
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
BEGIN
    IF to_regclass('public.signals') IS NULL THEN
        RAISE EXCEPTION
            '005 part B precondition failed: table public.signals does not '
            'exist. Apply backend_app/migrations/002_signal_trace.sql (or '
            '003_signal_trace_restoration.sql section 4) first.';
    END IF;

    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c
     WHERE c.oid = 'public.signals'::regclass;

    IF NOT rls_on THEN
        RAISE EXCEPTION
            '005 part B refuses to run: row level security is DISABLED on '
            'public.signals. This migration adds the canonical lifecycle '
            'state and the idempotency key, so applying it to a table '
            'without row-level isolation would expose another user''s '
            'trading records and would let a 23505 on '
            'uq_signals_idempotency_key be used to probe their keys. Apply '
            'backend_app/migrations/003_signal_trace_restoration.sql '
            'sections 8 and 9 (RLS + the three owner policies) first. This '
            'file deliberately does not enable RLS itself, because that '
            'would be a change to an existing control this task must leave '
            'untouched (Requirement 21.4).';
    END IF;

    SELECT count(*) INTO policies
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'signals';

    IF policies = 0 THEN
        RAISE EXCEPTION
            '005 part B refuses to run: row level security is enabled on '
            'public.signals but it has NO policies, so the table is '
            'unreachable to every non-superuser role and a signal written '
            'here could not be read back. Apply '
            'backend_app/migrations/003_signal_trace_restoration.sql '
            'section 9 first.';
    END IF;

    SELECT count(*) INTO indexes
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'signals';

    SELECT count(*) INTO triggers
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.signals'::regclass
       AND NOT t.tgisinternal;

    PERFORM set_config('aerora.signals_policies_before', policies::TEXT, true);
    PERFORM set_config('aerora.signals_indexes_before',  indexes::TEXT,  true);
    PERFORM set_config('aerora.signals_triggers_before', triggers::TEXT, true);

    RAISE NOTICE '005 part B preflight: public.signals has RLS enabled, % '
                 'policies, % indexes and % user triggers. This file adds no '
                 'policy and no trigger, adds exactly 3 indexes, and section '
                 '1.6 verifies each of those three facts.',
                 policies, indexes, triggers;
END $$;

-- 1.1) The two columns --------------------------------------------------
-- design.md "Signals table extensions", verbatim.
--
--   idempotency_key        The deterministic key an order submission was
--                          guarded by, "signal:" + signal.id (task 9.1).
--                          NULLABLE: a signal that never reached submission
--                          has none, and inventing one would be a
--                          fabricated fact. Uniqueness is enforced by
--                          uq_signals_idempotency_key (section 1.4), not by
--                          a column constraint, because the guarantee is
--                          needed only over non-NULL keys.
--                          Carries NO credential and NO exchange identity
--                          (Requirement 20.3).
--   order_lifecycle_state  The canonical Order_Lifecycle_State of
--                          Requirement 16.1, one of the 9 values fixed by
--                          chk_signals_order_lifecycle_state (section 1.3).
--                          NULLABLE and with NO DEFAULT until task 3.3's
--                          backfill has reconciled every legacy
--                          signals.status value - see "THE CHECK IS
--                          NULL-PERMISSIVE" in the header. NULL means
--                          "not yet reconciled", never a tenth state.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, so a re-run skips each column
-- already present. Neither column is NOT NULL, so no existing row is
-- rewritten and no DEFAULT is backfilled.
ALTER TABLE public.signals
    ADD COLUMN IF NOT EXISTS idempotency_key       TEXT,
    ADD COLUMN IF NOT EXISTS order_lifecycle_state TEXT;

-- 1.2) Column shape assertion ------------------------------------------
-- ADD COLUMN IF NOT EXISTS is SILENT about a pre-existing column of the
-- same name and a different type. Both silences would matter here:
--   * a non-text order_lifecycle_state turns section 1.3's IN-list into a
--     cast comparison, and an enum or integer column would not admit the
--     spellings order_lifecycle_state.py writes;
--   * a non-text idempotency_key that already holds data can make section
--     1.4's UNIQUE index fail with 23505 rather than be created, aborting
--     this transaction with a message that does not name the cause.
-- 'character varying' is accepted wherever 'text' is expected: a VARCHAR(n)
-- column of either name is functionally equivalent for these two and is
-- what a hand-added column most likely is.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    problems TEXT;
BEGIN
    SELECT string_agg(
               format('%s (expected text, found %s)',
                      expected.column_name,
                      coalesce(actual.data_type, 'no such column')),
               '; ' ORDER BY expected.column_name)
      INTO problems
      -- ::TEXT on every information_schema identifier: those columns are the
      -- sql_identifier / character_data domains, not text, and an explicit
      -- cast keeps the comparison a plain text compare on every server
      -- version rather than relying on an implicit operator.
      FROM (VALUES ('idempotency_key'),
                   ('order_lifecycle_state'))
             AS expected(column_name)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'signals'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT NOT IN ('text', 'character varying');

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.signals has lifecycle column(s) of the wrong shape: %. '
            'A pre-existing column of that name was left as it was, because '
            'ADD COLUMN IF NOT EXISTS does not alter one. Reconcile it by '
            'hand before re-running this migration.', problems;
    END IF;
END $$;

-- 1.3) chk_signals_order_lifecycle_state -------------------------------
-- Requirements 16.1, 16.6, 21.7. The 9 values, in the order
-- ORDER_LIFECYCLE_STATE_VALUES lists them
-- (backend_app/backend/order_lifecycle_state.py, task 1.1). This IN-list
-- and that tuple are the same vocabulary written twice, once for Python and
-- once for the database; nothing here may be edited without editing there,
-- and Requirement 27.2 permits only ADDING values, never removing or
-- respelling one.
--
-- Reminder, because it is load-bearing rather than pedantic: THIS
-- CONSTRAINT ADMITS NULL (a CHECK evaluating to NULL passes), so it fixes
-- the vocabulary for reconciled rows only until task 3.3's backfill
-- completes. See the header.
--
-- Guarded rather than bare: PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS
-- and a second bare run would raise 42710 and abort the file. No
-- DROP-then-ADD, which would open a window inside this transaction where
-- the vocabulary is unenforced.
-- Validated against existing rows on the first run: every pre-existing row
-- has order_lifecycle_state IS NULL, which the constraint admits, so the
-- ALTER cannot fail on existing data.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname  = 'chk_signals_order_lifecycle_state'
           AND conrelid = 'public.signals'::regclass
    ) THEN
        ALTER TABLE public.signals
            ADD CONSTRAINT chk_signals_order_lifecycle_state
            CHECK (order_lifecycle_state IN (
                'GENERATED','PENDING','SUBMITTED','PARTIALLY_EXECUTED','EXECUTED',
                'CLOSED','FAILED','CANCELLED','REJECTED'
            ));
        RAISE NOTICE 'Added chk_signals_order_lifecycle_state to '
                     'public.signals (9 canonical values, NULL admitted '
                     'until the task 3.3 backfill completes).';
    ELSE
        RAISE NOTICE 'chk_signals_order_lifecycle_state already present on '
                     'public.signals; left unchanged.';
    END IF;
END $$;

-- 1.4) Indexes ---------------------------------------------------------
-- uq_signals_idempotency_key: Requirements 19.1, 21.2, 21.7. The durable
-- backstop behind the Redis lock - see "WHY THE UNIQUE INDEX IS PARTIAL"
-- in the header, including the requirement that its 23505 be translated
-- into DuplicateOrderError by the caller rather than surfacing as a 500.
-- Partial, so the majority of signals - the ones that never reached
-- submission and have no key - carry no index entry at all.
CREATE UNIQUE INDEX IF NOT EXISTS uq_signals_idempotency_key
    ON public.signals (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- idx_signals_lifecycle_state: Requirement 21.3's Order_Lifecycle_State
-- column, serving Requirement 17.2's filter on the Signal_Trace_Page. Not
-- partial: NULL is a value the trace page and the task 3.3 backfill both
-- need to find ("which rows are not yet reconciled"), and a partial index
-- excluding NULL would leave exactly that query unserved.
CREATE INDEX IF NOT EXISTS idx_signals_lifecycle_state
    ON public.signals (order_lifecycle_state);

-- idx_signals_strategy_version: THE ONE OBJECT IN THIS FILE THAT design.md's
-- snippet DOES NOT SPECIFY, added deliberately and named as such rather
-- than slipped in. Requirement 21.3 requires public.signals to index "the
-- strategy, strategy-version, deployment, symbol, side,
-- Order_Lifecycle_State and generation-timestamp columns ... (supporting
-- Requirement 17's filtering)", and Requirement 17.2 lists strategy version
-- as a filter category of the Signal_Trace_Page. 002/003 index every column
-- on that list except strategy_version:
--     strategy_id     idx_signals_strategy_id, idx_signals_user_strategy
--     deployment_id   idx_signals_deployment_id
--     symbol          idx_signals_symbol, idx_signals_user_exchange_symbol
--     decision (side) idx_signals_decision
--     generated_at    idx_signals_generated_at
-- strategy_version is the single gap, and this file is the migration that
-- Requirement 21.3 is cited against (task 3.1). Adding it here is additive,
-- costs one small btree on an existing VARCHAR(20) column, weakens nothing,
-- and removes nothing; the alternative - leaving 21.3 knowingly unmet with
-- a comment saying so - would be worse. If the design's snippet is later
-- treated as exhaustive, this index is the one line to drop.
CREATE INDEX IF NOT EXISTS idx_signals_strategy_version
    ON public.signals (strategy_version);

-- 1.5) Column comments -------------------------------------------------
-- Intent the schema cannot express. COMMENT replaces, so this is
-- idempotent.
COMMENT ON COLUMN public.signals.idempotency_key IS
    'Deterministic Idempotency_Key the order submission for this signal was '
    'guarded by: "signal:" + signals.id (idempotency_key_for, task 9.1). '
    'NULL for a signal that never reached submission. Uniqueness over '
    'non-NULL values is enforced by the partial index '
    'uq_signals_idempotency_key, which is the DURABLE BACKSTOP behind '
    'DistributedIdempotencyLayer''s Redis lock (Requirements 19.1, 21.2): a '
    'second insert with the same key fails with 23505 even if Redis was '
    'flushed. Callers MUST translate that 23505 into DuplicateOrderError '
    'and return the signal''s current order_lifecycle_state (Requirement '
    '21.7). Contains no credential, secret or exchange identity '
    '(Requirement 20.3).';

COMMENT ON COLUMN public.signals.order_lifecycle_state IS
    'Canonical Order_Lifecycle_State (Requirement 16.1): one of GENERATED, '
    'PENDING, SUBMITTED, PARTIALLY_EXECUTED, EXECUTED, CLOSED, FAILED, '
    'CANCELLED, REJECTED, fixed by chk_signals_order_lifecycle_state and '
    'written from ORDER_LIFECYCLE_STATE_VALUES in '
    'backend_app/backend/order_lifecycle_state.py. NULL means NOT YET '
    'RECONCILED - never a tenth state and never a default - and is admitted '
    'by the CHECK only until the task 3.3 backfill maps every legacy '
    'signals.status value through SIGNALS_STATUS_MAP. Transition legality '
    '(Requirement 16.4) is gated in the application by '
    'assert_transition_legal before the write, not by a trigger, so a '
    'rejected transition reaches the caller as a named error rather than a '
    '23514. The legacy signals.status column is retained unchanged and is '
    'this column''s backfill source.';

-- 1.6) Postflight - the five objects exist, and nothing else moved -----
-- Turns the header's promises into facts about the database. Re-counts the
-- policy, trigger and index inventory recorded in section 1.0: policies and
-- triggers must be unchanged, indexes must have grown by exactly the three
-- this section creates (0 on a re-run, since all three are IF NOT EXISTS).
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    policies_before  INTEGER := current_setting('aerora.signals_policies_before')::INTEGER;
    indexes_before   INTEGER := current_setting('aerora.signals_indexes_before')::INTEGER;
    triggers_before  INTEGER := current_setting('aerora.signals_triggers_before')::INTEGER;
    policies_now     INTEGER;
    indexes_now      INTEGER;
    triggers_now     INTEGER;
    missing          TEXT;
BEGIN
    -- (a) Both columns present.
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('idempotency_key'), ('order_lifecycle_state'))
             AS expected(column_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns c
                WHERE c.table_schema::TEXT = 'public'
                  AND c.table_name::TEXT   = 'signals'
                  AND c.column_name::TEXT  = expected.column_name);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '005 part B postflight failed: public.signals is '
                        'missing column(s) %.', missing;
    END IF;

    -- (b) The CHECK is present.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname  = 'chk_signals_order_lifecycle_state'
           AND conrelid = 'public.signals'::regclass
           AND contype  = 'c'
    ) THEN
        RAISE EXCEPTION '005 part B postflight failed: '
                        'chk_signals_order_lifecycle_state is not present on '
                        'public.signals, so the 9-value vocabulary is '
                        'unenforced (Requirements 16.1, 16.6).';
    END IF;

    -- (c) The idempotency index is present AND is unique AND is partial.
    --     A non-unique or non-partial index of the same name would satisfy a
    --     name check while enforcing nothing, so all three are asserted.
    IF NOT EXISTS (
        SELECT 1
          FROM pg_index i
          JOIN pg_class ic ON ic.oid = i.indexrelid
         WHERE ic.relname = 'uq_signals_idempotency_key'
           AND i.indrelid = 'public.signals'::regclass
           AND i.indisunique
           AND i.indpred IS NOT NULL
    ) THEN
        RAISE EXCEPTION '005 part B postflight failed: '
                        'uq_signals_idempotency_key is absent, not UNIQUE, or '
                        'not partial. Requirement 21.2''s durable uniqueness '
                        'guarantee behind the Redis lock would not hold.';
    END IF;

    -- (d) The two plain indexes are present.
    SELECT string_agg(expected.index_name, ', ' ORDER BY expected.index_name)
      INTO missing
      FROM (VALUES ('idx_signals_lifecycle_state'),
                   ('idx_signals_strategy_version'))
             AS expected(index_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_indexes p
                WHERE p.schemaname = 'public'
                  AND p.tablename  = 'signals'
                  AND p.indexname::TEXT = expected.index_name);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '005 part B postflight failed: index(es) % were not '
                        'created (Requirement 21.3).', missing;
    END IF;

    -- (e) Nothing else moved.
    SELECT count(*) INTO policies_now
      FROM pg_policies WHERE schemaname = 'public' AND tablename = 'signals';
    SELECT count(*) INTO indexes_now
      FROM pg_indexes  WHERE schemaname = 'public' AND tablename = 'signals';
    SELECT count(*) INTO triggers_now
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.signals'::regclass AND NOT t.tgisinternal;

    IF policies_now <> policies_before THEN
        RAISE EXCEPTION '005 part B postflight failed: the row-level security '
                        'policy count on public.signals moved from % to %. '
                        'This file must not add, alter or remove a policy '
                        '(Requirement 21.4).', policies_before, policies_now;
    END IF;

    IF triggers_now <> triggers_before THEN
        RAISE EXCEPTION '005 part B postflight failed: the user trigger count '
                        'on public.signals moved from % to %. This file must '
                        'not add a trigger; '
                        'trigger_update_signals_updated_at already maintains '
                        'updated_at for the new columns.',
                        triggers_before, triggers_now;
    END IF;

    IF indexes_now > indexes_before + 3 THEN
        RAISE EXCEPTION '005 part B postflight failed: the index count on '
                        'public.signals moved from % to %, more than the 3 '
                        'indexes this file creates.',
                        indexes_before, indexes_now;
    END IF;

    RAISE NOTICE '005 part B section 1 complete: public.signals has '
                 'idempotency_key and order_lifecycle_state, '
                 'chk_signals_order_lifecycle_state, '
                 'uq_signals_idempotency_key (UNIQUE, partial), '
                 'idx_signals_lifecycle_state and '
                 'idx_signals_strategy_version. Policies % (unchanged), '
                 'triggers % (unchanged), indexes % -> %. '
                 'order_lifecycle_state is NULL on every pre-existing row '
                 'until the task 3.3 backfill runs.',
                 policies_now, triggers_now, indexes_before, indexes_now;
END $$;

COMMIT;

-- ==========================================================================
-- END OF SECTION 1 (task 3.1). APPEND POINTS FOR THE REST OF MIGRATION 005b
-- ==========================================================================
--
-- Sections 2 and 3 are appended BELOW THIS BLOCK. Section 2 opens its own
-- BEGIN and closes its own COMMIT, so that section 1's commit boundary is
-- never widened and a partially-applied file never leaves
-- order_lifecycle_state present with its CHECK missing. Section 3 opens NO
-- transaction, because it executes no statement at all - see its own header.
--
-- STATUS: BOTH SECTIONS HAVE LANDED. Section 2 begins immediately after this
-- block; section 3 follows section 2's COMMIT and sits above the
-- VERIFICATION block. The whole of migration 005 part B is therefore in this
-- file, and applying it is exactly: run this file, then run the script
-- section 3 names.
--
--   SECTION 2 - task 3.2: CREATE TABLE public.order_lifecycle_transitions
--       (id, signal_id FK ON DELETE CASCADE, user_id FK, from_state,
--        to_state + its CHECK against the SAME 9 values section 1.3
--        uses, reason, occurred_at), idx_olt_signal_time,
--        idx_olt_user, RLS enabled with owner-scoped SELECT and INSERT
--        policies ONLY - no UPDATE and no DELETE policy, matching the
--        append-only convention signal_events already establishes in
--        002_signal_trace.sql. Requirements 16.7, 21.1, 21.3, 21.4.
--       Its CHECK's IN-list is byte-identical to section 1.3's: both are
--       transcriptions of ORDER_LIFECYCLE_STATE_VALUES and they must not be
--       able to disagree. The constraint is named
--       chk_order_lifecycle_transitions_to_state rather than design.md's
--       abbreviated chk_olt_to_state - see "ONE DELIBERATE NAMING
--       DEVIATION" in section 2's own header for why.
--
--   SECTION 3 - task 3.3: the backfill of public.signals
--       .order_lifecycle_state from the legacy signals.status column.
--       design.md is explicit that this is APPLICATION CODE - "one UPDATE
--       per legacy value, not a server-side function ... so the mapping
--       lives in exactly one place (order_lifecycle_state.py)". So section
--       3 is expected to be a POINTER to the script that performs it plus
--       the verification query proving no row is left NULL, NOT a
--       transcription of SIGNALS_STATUS_MAP into SQL. Do not restate the
--       mapping here; that is the duplication task 1.1 exists to prevent.
--       It landed as exactly that: COMMENT ONLY, with no BEGIN and no
--       COMMIT, because a section that executes no statement has no
--       transaction to open. Requirements 16.2, 16.3.
--
-- ==========================================================================
-- SECTION 2 - task 3.2: public.order_lifecycle_transitions
-- ==========================================================================
--
-- PURPOSE
--   Requirement 16.7's transition history, as a table: for a signal/order
--   pair, "the prior value, the new value, and the transition timestamp
--   ... retrievable in chronological order by timestamp". Section 1 gave
--   public.signals a single CURRENT order_lifecycle_state; that column is
--   overwritten on every transition and so cannot answer "how did this
--   order get here", which is what Requirement 17.6's trace detail and
--   Requirement 16.7's audit trail both need.
--
--   Source of the definition: design.md "Signals table extensions
--   (Requirements 16, 19, 21)", the block commented
--   "Requirement 16.7's transition history", statement for statement,
--   schema-qualified with public. / auth. and with each CREATE POLICY put
--   behind a pg_policies guard (see DELIBERATE ADDITIONS below).
--
--   The 9-value IN-list is byte-identical to section 1.3's. It is the same
--   transcription of ORDER_LIFECYCLE_STATE_VALUES
--   (backend_app/backend/order_lifecycle_state.py, task 1.1) written a
--   second time, for a second table, and the two must not be able to
--   disagree: a to_state this table admits but public.signals rejects, or
--   the reverse, would mean the history and the current value are drawn
--   from different vocabularies. tests/
--   test_signal_lifecycle_idempotency_migration.py compares both lists
--   against that tuple, in order, so a tenth value added under
--   Requirement 27.2 cannot land in one place only.
--
-- APPEND-ONLY, AND WHY THAT IS THE WHOLE POINT
--   RLS is enabled with an owner-scoped SELECT policy and an owner-scoped
--   INSERT policy, and NOTHING ELSE. There is deliberately no UPDATE
--   policy and no DELETE policy, so under row-level security a client role
--   can read its own transitions and append to them, and can neither
--   rewrite nor erase one. That is the convention 002_signal_trace.sql
--   already establishes for signal_events (SELECT + INSERT only, while
--   signals itself also gets an UPDATE policy), and design.md cites that
--   precedent by name. A financial audit trail whose rows can be edited is
--   not an audit trail.
--
--   Consequences a caller must design around, stated here rather than
--   discovered later:
--     * A wrong reason string cannot be corrected in place. Record a
--       further transition; do not expect an UPDATE to work.
--     * Erasing history for a deleted signal happens ONLY by cascade from
--       public.signals (ON DELETE CASCADE on signal_id) or from
--       auth.users. Nothing else removes a row through a client role.
--     * service_role bypasses RLS, as it does everywhere in this schema.
--       The append-only guarantee is against client roles, which is where
--       the requirement places it.
--
-- WHY THERE IS NO TRANSITION-LEGALITY CONSTRAINT ON (from_state, to_state)
--   Requirement 16.4's reachability rule is gated in the application by
--   order_lifecycle_state.assert_transition_legal (task 1.1), called by
--   task 10.2 BEFORE the write. design.md "Persistence layer rejection
--   (Requirement 16.6)" is explicit that this table "has no unreachable
--   value to reject at the table level because it is an append-only log of
--   transitions that already passed assert_transition_legal before the
--   write". Restating the transition table in SQL would duplicate the map
--   task 1.1 exists to hold in one place, and a 23514 surfacing from here
--   could not name the rejected transition to the caller the way
--   Requirement 16.6 requires. So to_state is constrained to the
--   VOCABULARY (which is a fact about spelling, not about reachability)
--   and from_state is not constrained at all - it is NULL for a signal's
--   first recorded transition, which is design.md's own annotation.
--
-- ONE DELIBERATE NAMING DEVIATION FROM design.md
--   design.md names the CHECK chk_olt_to_state. It is created here as
--   chk_order_lifecycle_transitions_to_state, which is the name task 3.2
--   itself uses and which follows the chk_<table>_<column> convention
--   section 1.3's chk_signals_order_lifecycle_state and
--   005a/004b/004d/004e all follow. Nothing in the codebase references
--   either spelling - this table is created here for the first time and no
--   application code names its constraints - so the rename costs nothing
--   and cannot break a caller. The two INDEX names design.md gives
--   (idx_olt_signal_time, idx_olt_user) keep their abbreviated form
--   verbatim, because those ARE referenced by the design's own
--   Requirement 21.3 index inventory. If the design's snippet is later
--   treated as exhaustive down to constraint names, this one identifier is
--   the single line to change.
--
-- FOUR DELIBERATE ADDITIONS BEYOND THE DESIGN'S DDL, AND WHY
--   Same rationale as section 1: the design snippet is a schema sketch,
--   and this file is applied BY HAND, possibly twice, to a database whose
--   migration history nobody recorded. None of these weakens a control,
--   and none changes a column, constraint, index or policy the design
--   specifies.
--
--   1. Preflight assertions (section 2.0). Both foreign-key targets must
--      exist before a CREATE TABLE that references them, and a bare 42P01
--      from inside the CREATE names neither. public.signals comes from
--      002_signal_trace.sql / 003_signal_trace_restoration.sql; auth.users
--      is Supabase's, exactly as 004d section 0 asserts it.
--   2. A shape assertion (section 2.2). CREATE TABLE IF NOT EXISTS is
--      SILENT about a pre-existing table of the same name and a different
--      shape - for instance one created by hand during an investigation.
--      Every column this table's readers depend on is asserted present and
--      of the expected type, so a mismatch fails here with a readable
--      message instead of at the first insert in production.
--   3. Guards on the CHECK (section 2.3) and on both policies (section
--      2.5). PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS and no
--      CREATE POLICY IF NOT EXISTS; a bare second run raises 42710 and
--      aborts the file. Same pattern as 004d section 3 and 004b section 4.
--      The guards also repair a table that already existed WITHOUT them
--      rather than leaving the invariant unenforced.
--   4. Comments (section 2.6) and a postflight (section 2.7) that proves
--      the columns, the CHECK, BOTH cascading foreign keys, both indexes,
--      RLS itself and exactly the two intended policies are all in place -
--      and that no UPDATE, DELETE or ALL policy exists, which is the
--      append-only guarantee restated as an assertion rather than a
--      comment.
--
-- REQUIREMENT 21 COVERAGE FOR THIS TABLE
--   21.1  Both foreign keys are NOT NULL and ON DELETE CASCADE:
--         signal_id -> public.signals(id), user_id -> auth.users(id). An
--         ownerless or signal-less transition row is unrepresentable.
--   21.3  idx_olt_signal_time (signal_id, occurred_at) serves Requirement
--         16.7's "retrievable in chronological order by timestamp" for one
--         signal with a single index scan; idx_olt_user serves the
--         ownership-scoped listing every read path here performs.
--   21.4  RLS enabled, scoped to the owning user, on the transition-history
--         table 21.4 names explicitly ("where the transition-history table
--         described in Requirement 16 Criterion 7 is implemented, that
--         table").
--   21.5  occurred_at NOT NULL DEFAULT NOW() is this table's creation
--         timestamp. No updated_at column is added and none is needed: the
--         table is append-only by policy, so a row's last-updated time is
--         its creation time by construction. Adding a column that could
--         only ever equal occurred_at would invite a writer to believe
--         rows are updatable here.
--   21.7  A violated FK or the to_state CHECK aborts the whole INSERT; the
--         caller (task 10.2) surfaces which constraint was violated, the
--         same way it must translate 23505 on uq_signals_idempotency_key.
--
-- SAFETY
--   * Additive only. Creates ONE new table and its own objects. No DROP,
--     no TRUNCATE, no DELETE, no UPDATE, no ALTER COLUMN, no GRANT, no
--     REVOKE; no statement touches public.signals, auth.users or any other
--     pre-existing relation, and no statement modifies a row anywhere.
--     ON DELETE CASCADE is a clause of a new foreign key, not a deletion.
--   * Fully idempotent. CREATE TABLE IF NOT EXISTS, the CHECK behind a
--     pg_constraint guard, CREATE INDEX IF NOT EXISTS, ENABLE ROW LEVEL
--     SECURITY (a no-op when already on), each policy behind a pg_policies
--     guard, COMMENT replaces. A re-run adds nothing and raises nothing.
--   * Its own transaction. Either the table, its CHECK, its two indexes,
--     RLS and both policies exist, or none of them do. A table carrying
--     trading history with RLS enabled but no policy - unreachable - and a
--     table with rows but no RLS - cross-tenant readable - are both
--     impossible to observe from another session.
--   * No GRANT or REVOKE is issued, matching 002_signal_trace.sql's
--     treatment of signals and signal_events. The privilege narrowing
--     004b/004d apply to their own tables is deliberately NOT copied here,
--     because design.md specifies none for this table and Supabase's
--     default grants sit behind the RLS this section enables. If the
--     defence-in-depth posture of 004d is later adopted schema-wide, this
--     table's grants belong in that change, not in this task.

BEGIN;

-- 2.0) Preflight -------------------------------------------------------
-- Both foreign-key targets must exist before a CREATE TABLE that
-- references them. Also records whether the table already exists, so
-- section 2.7 can say whether this run created it or found it.
-- Idempotent: reads catalogues, writes nothing but one transaction-local
-- setting.
DO $$
DECLARE
    already_there BOOLEAN;
BEGIN
    IF to_regclass('public.signals') IS NULL THEN
        RAISE EXCEPTION
            '005 part B section 2 precondition failed: table public.signals '
            'does not exist, so order_lifecycle_transitions.signal_id has '
            'nothing to reference. Apply '
            'backend_app/migrations/002_signal_trace.sql (or '
            '003_signal_trace_restoration.sql section 4) first.';
    END IF;

    IF to_regclass('auth.users') IS NULL THEN
        RAISE EXCEPTION
            '005 part B section 2 precondition failed: table auth.users does '
            'not exist, so order_lifecycle_transitions.user_id has nothing '
            'to reference. This migration targets a Supabase database, where '
            'the auth schema is created by the platform. The same '
            'precondition is asserted by '
            'backend_app/migrations/004d_training_and_models.sql.';
    END IF;

    already_there := to_regclass('public.order_lifecycle_transitions') IS NOT NULL;
    PERFORM set_config('aerora.olt_existed_before',
                       CASE WHEN already_there THEN 'yes' ELSE 'no' END, true);

    RAISE NOTICE '005 part B section 2 preflight: both foreign-key targets '
                 'exist; public.order_lifecycle_transitions existed before '
                 'this run: %.',
                 CASE WHEN already_there THEN 'yes' ELSE 'no' END;
END $$;

-- 2.1) The table -------------------------------------------------------
-- design.md "Requirement 16.7's transition history", verbatim apart from
-- schema qualification and the constraint name (see the header).
--
--   id           Surrogate key. gen_random_uuid() is the default every
--                table in this schema uses, from 002 onward.
--   signal_id    The signal whose lifecycle this row is a step of.
--                NOT NULL and ON DELETE CASCADE: history for a signal that
--                no longer exists is not history, it is an orphan
--                (Requirement 21.1).
--   user_id      The owning user, for the RLS predicate in section 2.5 and
--                for idx_olt_user. Denormalised from signals.user_id
--                deliberately: an RLS policy that had to join back to
--                public.signals to find the owner would be a policy whose
--                cost and whose correctness both depend on another table's
--                policies.
--   from_state   The PRIOR Order_Lifecycle_State. NULL for the first
--                recorded transition of a signal - design.md's own
--                annotation - because there is no prior value to record
--                and inventing one (say GENERATED) would be a fabricated
--                fact. Unconstrained by design; see the header.
--   to_state     The NEW Order_Lifecycle_State, one of the 9 values fixed
--                by chk_order_lifecycle_transitions_to_state.
--   reason       Free-text explanation of the transition, NULL when there
--                is nothing to add beyond the two states. Must carry no
--                credential, secret, passphrase or exchange identity
--                (Requirement 20.3) - it is written into audit output and
--                read back by the trace surfaces.
--   occurred_at  When the transition happened. NOT NULL DEFAULT NOW(),
--                and the ORDER in Requirement 16.7's "chronological order
--                by timestamp".
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. Section 2.3 re-asserts the
-- inline CHECK under a guard for the case where this table already existed
-- without it.
CREATE TABLE IF NOT EXISTS public.order_lifecycle_transitions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id     UUID NOT NULL REFERENCES public.signals(id) ON DELETE CASCADE,
    user_id       UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    from_state    TEXT,
    to_state      TEXT NOT NULL,
    reason        TEXT,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_order_lifecycle_transitions_to_state
        CHECK (to_state IN (
                'GENERATED','PENDING','SUBMITTED','PARTIALLY_EXECUTED','EXECUTED',
                'CLOSED','FAILED','CANCELLED','REJECTED'
        ))
);

-- 2.2) Shape assertion -------------------------------------------------
-- CREATE TABLE IF NOT EXISTS is SILENT about a pre-existing table of the
-- same name and a different shape. Assert every column and its type, so a
-- mismatch fails here with a readable message rather than at the first
-- insert: a non-text to_state would make section 2.3's IN-list comparison
-- a cast, a non-uuid signal_id would not carry the foreign key, and a
-- non-timestamptz occurred_at would break Requirement 16.7's ordering.
-- 'character varying' is accepted wherever 'text' is expected - a
-- VARCHAR(n) column is functionally equivalent for these three and is what
-- a hand-added column most likely is.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    problems TEXT;
BEGIN
    SELECT string_agg(
               format('%s (expected %s, found %s)',
                      expected.column_name,
                      expected.data_type,
                      coalesce(actual.data_type, 'no such column')),
               '; ' ORDER BY expected.column_name)
      INTO problems
      -- ::TEXT on every information_schema identifier, for the reason
      -- section 1.2 gives: those columns are domains, not text.
      FROM (VALUES ('id',          'uuid'),
                   ('signal_id',   'uuid'),
                   ('user_id',     'uuid'),
                   ('from_state',  'text'),
                   ('to_state',    'text'),
                   ('reason',      'text'),
                   ('occurred_at', 'timestamp with time zone'))
             AS expected(column_name, data_type)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'order_lifecycle_transitions'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR NOT (actual.data_type::TEXT = expected.data_type
                OR (expected.data_type = 'text'
                    AND actual.data_type::TEXT = 'character varying'));

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.order_lifecycle_transitions has column(s) of the wrong '
            'shape: %. A pre-existing table of that name was left as it was, '
            'because CREATE TABLE IF NOT EXISTS does not alter one. '
            'Reconcile it by hand before re-running this migration.',
            problems;
    END IF;
END $$;

-- 2.3) chk_order_lifecycle_transitions_to_state ------------------------
-- A no-op on a fresh run: the constraint was declared inline in section
-- 2.1. It exists so that a table created earlier WITHOUT it gains it
-- rather than silently admitting a to_state outside the vocabulary.
--
-- The IN-list is byte-identical to section 1.3's, deliberately. Both are
-- transcriptions of ORDER_LIFECYCLE_STATE_VALUES in
-- backend_app/backend/order_lifecycle_state.py; Requirement 27.2 permits
-- only ADDING a value, never removing or respelling one, and a value added
-- to one list and not the other would let the history and the current
-- state disagree about what a legal spelling is.
--
-- Unlike section 1.3's CHECK, this one is NOT null-permissive in practice:
-- to_state is NOT NULL, so every row must name one of the 9. from_state
-- may be NULL and is not constrained at all.
--
-- Idempotent: guarded on pg_constraint, since PostgreSQL has no
-- ADD CONSTRAINT IF NOT EXISTS and a bare second run would raise 42710.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname  = 'chk_order_lifecycle_transitions_to_state'
           AND conrelid = 'public.order_lifecycle_transitions'::regclass
    ) THEN
        ALTER TABLE public.order_lifecycle_transitions
            ADD CONSTRAINT chk_order_lifecycle_transitions_to_state
            CHECK (to_state IN (
                'GENERATED','PENDING','SUBMITTED','PARTIALLY_EXECUTED','EXECUTED',
                'CLOSED','FAILED','CANCELLED','REJECTED'
            ));
        RAISE NOTICE 'Added chk_order_lifecycle_transitions_to_state to a '
                     'pre-existing public.order_lifecycle_transitions '
                     '(9 canonical values, identical to '
                     'chk_signals_order_lifecycle_state''s).';
    END IF;
END $$;

-- 2.4) Indexes ---------------------------------------------------------
-- Requirement 21.3, and design.md's two indexes verbatim.
--
-- idx_olt_signal_time: (signal_id, occurred_at) in that order, so
-- Requirement 16.7's "retrievable in chronological order by timestamp" for
-- one signal is a single index scan already in the right order - which is
-- exactly what the trace detail surface (Requirement 17.6) reads.
CREATE INDEX IF NOT EXISTS idx_olt_signal_time ON public.order_lifecycle_transitions(signal_id, occurred_at);

-- idx_olt_user: the ownership-scoped listing every read path here performs,
-- and the column the RLS predicate in section 2.5 filters on.
CREATE INDEX IF NOT EXISTS idx_olt_user ON public.order_lifecycle_transitions(user_id);

-- 2.5) Row level security ----------------------------------------------
-- Requirement 21.4. Enable first, then add the policies: enabling RLS is
-- default-deny, so between these statements the table is unreachable
-- rather than open - and every statement is inside this transaction, so no
-- session ever observes the intermediate state.
--
-- Idempotent: ENABLE ROW LEVEL SECURITY is a no-op when RLS is already on
-- (the same unguarded form 003_signal_trace_restoration.sql section 8 and
-- 004d section 3 use).
ALTER TABLE public.order_lifecycle_transitions ENABLE ROW LEVEL SECURITY;

-- The design's two policies, verbatim in name and predicate. SELECT and
-- INSERT only: no UPDATE policy and no DELETE policy, so a transition
-- cannot be rewritten or erased through a client role. That is the
-- append-only convention signal_events already establishes in
-- 002_signal_trace.sql, and it is why section 2.7 asserts the absence of
-- an UPDATE, DELETE or ALL policy instead of merely not creating one here.
--
-- Idempotent: each guarded on pg_policies by schemaname + tablename +
-- policyname, the pattern 003 section 9, 004b section 4 and 004d section 3
-- already use. PostgreSQL has no CREATE POLICY IF NOT EXISTS.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'order_lifecycle_transitions'
           AND policyname = 'olt_owner_select'
    ) THEN
        CREATE POLICY olt_owner_select ON public.order_lifecycle_transitions
            FOR SELECT USING (user_id = auth.uid());
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'order_lifecycle_transitions'
           AND policyname = 'olt_owner_insert'
    ) THEN
        CREATE POLICY olt_owner_insert ON public.order_lifecycle_transitions
            FOR INSERT WITH CHECK (user_id = auth.uid());
    END IF;
END $$;

-- 2.6) Comments --------------------------------------------------------
-- Intent the schema cannot express, recorded where every schema browser
-- shows it. COMMENT replaces, so this is idempotent.
COMMENT ON TABLE public.order_lifecycle_transitions IS
    'Append-only history of Order_Lifecycle_State transitions per signal '
    '(Requirement 16.7): prior value, new value and timestamp, retrievable '
    'in chronological order through idx_olt_signal_time. Row-level '
    'ownership by user_id (Requirement 21.4) with SELECT and INSERT '
    'policies ONLY - there is deliberately no UPDATE and no DELETE policy, '
    'matching signal_events in 002_signal_trace.sql, so a client role can '
    'append to this log and read its own rows but can neither rewrite nor '
    'erase one. Rows are removed only by cascade from public.signals or '
    'auth.users. Transition LEGALITY (Requirement 16.4) is gated in the '
    'application by order_lifecycle_state.assert_transition_legal before '
    'the write, not by a constraint here, so a rejected transition reaches '
    'the caller as a named error rather than a 23514.';

COMMENT ON COLUMN public.order_lifecycle_transitions.from_state IS
    'The PRIOR Order_Lifecycle_State. NULL for a signal''s first recorded '
    'transition - there is no prior value, and inventing one would be a '
    'fabricated fact. Deliberately unconstrained: reachability is the '
    'application''s gate (Requirement 16.4), not this table''s.';

COMMENT ON COLUMN public.order_lifecycle_transitions.to_state IS
    'The NEW Order_Lifecycle_State: one of GENERATED, PENDING, SUBMITTED, '
    'PARTIALLY_EXECUTED, EXECUTED, CLOSED, FAILED, CANCELLED, REJECTED, '
    'fixed by chk_order_lifecycle_transitions_to_state. That IN-list is '
    'byte-identical to chk_signals_order_lifecycle_state''s and both are '
    'written from ORDER_LIFECYCLE_STATE_VALUES in '
    'backend_app/backend/order_lifecycle_state.py, so the history and the '
    'current value on public.signals cannot be drawn from different '
    'vocabularies.';

COMMENT ON COLUMN public.order_lifecycle_transitions.reason IS
    'Why the transition happened, NULL when there is nothing to add beyond '
    'the two states. Contains no credential, secret, passphrase or '
    'exchange identity (Requirement 20.3): this column is read back by the '
    'signal-trace surfaces and written into audit output.';

COMMENT ON COLUMN public.order_lifecycle_transitions.occurred_at IS
    'When the transition happened. This table''s creation timestamp for '
    'Requirement 21.5 purposes, and the ordering column of Requirement '
    '16.7''s "chronological order by timestamp". No updated_at column '
    'exists because the table is append-only by policy: a row''s '
    'last-updated time is its creation time by construction.';

-- 2.7) Postflight - everything exists, and the log is append-only ------
-- Turns section 2's promises into facts about the database: the columns,
-- the CHECK, BOTH cascading foreign keys, both indexes, RLS itself, the
-- two intended policies with the right commands, and the ABSENCE of any
-- UPDATE, DELETE or ALL policy. That last assertion is the append-only
-- guarantee: not creating such a policy here would not prove one does not
-- exist on a table this file found rather than created.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    existed_before TEXT := current_setting('aerora.olt_existed_before');
    missing        TEXT;
    writable       TEXT;
    rls_on         BOOLEAN;
    select_qual    TEXT;
    insert_check   TEXT;
    policies_now   INTEGER;
BEGIN
    -- (a) The table itself.
    IF to_regclass('public.order_lifecycle_transitions') IS NULL THEN
        RAISE EXCEPTION '005 part B section 2 postflight failed: '
                        'public.order_lifecycle_transitions was not created.';
    END IF;

    -- (b) The CHECK is present.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname  = 'chk_order_lifecycle_transitions_to_state'
           AND conrelid = 'public.order_lifecycle_transitions'::regclass
           AND contype  = 'c'
    ) THEN
        RAISE EXCEPTION '005 part B section 2 postflight failed: '
                        'chk_order_lifecycle_transitions_to_state is not '
                        'present, so the 9-value vocabulary is unenforced on '
                        'the transition history (Requirements 16.1, 16.7).';
    END IF;

    -- (c) Both foreign keys, both cascading. confdeltype = 'c' is
    --     ON DELETE CASCADE; a FK of the right shape with the wrong
    --     delete action would leave orphan history behind a deleted
    --     signal (Requirement 21.1).
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid    = 'public.order_lifecycle_transitions'::regclass
           AND contype     = 'f'
           AND confrelid   = 'public.signals'::regclass
           AND confdeltype = 'c'
           AND pg_get_constraintdef(oid) LIKE 'FOREIGN KEY (signal_id)%'
    ) THEN
        RAISE EXCEPTION '005 part B section 2 postflight failed: signal_id '
                        'has no ON DELETE CASCADE foreign key to '
                        'public.signals (Requirement 21.1).';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid    = 'public.order_lifecycle_transitions'::regclass
           AND contype     = 'f'
           AND confrelid   = 'auth.users'::regclass
           AND confdeltype = 'c'
           AND pg_get_constraintdef(oid) LIKE 'FOREIGN KEY (user_id)%'
    ) THEN
        RAISE EXCEPTION '005 part B section 2 postflight failed: user_id has '
                        'no ON DELETE CASCADE foreign key to auth.users, so '
                        'an ownerless transition row would be representable '
                        '(Requirement 21.1).';
    END IF;

    -- (d) Both indexes.
    SELECT string_agg(expected.index_name, ', ' ORDER BY expected.index_name)
      INTO missing
      FROM (VALUES ('idx_olt_signal_time'), ('idx_olt_user'))
             AS expected(index_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_indexes p
                WHERE p.schemaname = 'public'
                  AND p.tablename  = 'order_lifecycle_transitions'
                  AND p.indexname::TEXT = expected.index_name);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '005 part B section 2 postflight failed: index(es) % '
                        'were not created (Requirement 21.3).', missing;
    END IF;

    -- (e) RLS is on. Without it the two policies below are inert and one
    --     user's transition history is readable by every other user.
    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c
     WHERE c.oid = 'public.order_lifecycle_transitions'::regclass;

    IF NOT rls_on THEN
        RAISE EXCEPTION '005 part B section 2 postflight failed: row level '
                        'security is not enabled on '
                        'public.order_lifecycle_transitions, so its policies '
                        'are inert and one user''s transition history is '
                        'readable by another (Requirement 21.4).';
    END IF;

    -- (f) The two owner policies exist and are owner-scoped.
    SELECT qual INTO select_qual
      FROM pg_policies
     WHERE schemaname = 'public'
       AND tablename  = 'order_lifecycle_transitions'
       AND policyname = 'olt_owner_select'
       AND cmd        = 'SELECT';

    IF select_qual IS NULL OR position('auth.uid()' in select_qual) = 0 THEN
        RAISE EXCEPTION '005 part B section 2 postflight failed: '
                        'olt_owner_select is absent, is not a SELECT policy, '
                        'or does not scope rows to auth.uid() '
                        '(Requirement 21.4).';
    END IF;

    SELECT with_check INTO insert_check
      FROM pg_policies
     WHERE schemaname = 'public'
       AND tablename  = 'order_lifecycle_transitions'
       AND policyname = 'olt_owner_insert'
       AND cmd        = 'INSERT';

    IF insert_check IS NULL OR position('auth.uid()' in insert_check) = 0 THEN
        RAISE EXCEPTION '005 part B section 2 postflight failed: '
                        'olt_owner_insert is absent, is not an INSERT '
                        'policy, or does not force user_id = auth.uid() on '
                        'the new row (Requirement 21.4).';
    END IF;

    -- (g) Append-only: nothing may let a client role rewrite or erase a
    --     transition. A FOR ALL policy counts, since it covers both.
    SELECT string_agg(policyname || ' (' || cmd || ')', ', ' ORDER BY policyname)
      INTO writable
      FROM pg_policies
     WHERE schemaname = 'public'
       AND tablename  = 'order_lifecycle_transitions'
       AND cmd IN ('UPDATE', 'DELETE', 'ALL');

    IF writable IS NOT NULL THEN
        RAISE EXCEPTION '005 part B section 2 postflight failed: '
                        'public.order_lifecycle_transitions has policy/'
                        'policies % that permit rewriting or erasing a '
                        'transition. This log is append-only (Requirement '
                        '16.7): SELECT and INSERT policies only, matching '
                        'signal_events.', writable;
    END IF;

    SELECT count(*) INTO policies_now
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'order_lifecycle_transitions';

    RAISE NOTICE '005 part B section 2 complete: '
                 'public.order_lifecycle_transitions (existed before this '
                 'run: %) has chk_order_lifecycle_transitions_to_state, both '
                 'cascading foreign keys, idx_olt_signal_time, idx_olt_user, '
                 'RLS enabled and % policy/policies - append-only, with no '
                 'UPDATE, DELETE or ALL policy.',
                 existed_before, policies_now;
END $$;

COMMIT;

-- ==========================================================================
-- END OF SECTION 2 (task 3.2). SECTION 3 (task 3.3) FOLLOWS, ABOVE THE
-- VERIFICATION BLOCK. IT IS COMMENT ONLY AND OPENS NO TRANSACTION.
-- ==========================================================================

-- ==========================================================================
-- SECTION 3 - task 3.3: the backfill of public.signals.order_lifecycle_state
-- ==========================================================================
--
-- THIS SECTION EXECUTES NOTHING. IT IS A POINTER PLUS THE QUERIES THAT PROVE
-- THE BACKFILL FINISHED. THAT IS THE WHOLE DESIGN, NOT AN OMISSION.
--
-- WHY THERE IS NO SQL HERE
--   design.md, "Signals table extensions": "Application code performs the
--   backfill (one UPDATE per legacy value, not a server-side function), so
--   the mapping lives in exactly one place (order_lifecycle_state.py)
--   rather than being restated in SQL."
--
--   The legacy signals.status vocabulary exists in this repository exactly
--   once, as SIGNALS_STATUS_MAP in
--   backend_app/backend/order_lifecycle_state.py (task 1.1). Writing the
--   UPDATEs out here would transcribe that dict into SQL and create the
--   second copy task 1.1 exists to prevent: a spelling added to the map
--   under Requirement 16.3 would then have to be remembered here too, in a
--   file operators apply by hand and nothing records having run. So the
--   statements are DERIVED from the map at run time by the script below,
--   and this section carries no legacy spelling, no state name and no
--   UPDATE. tests/test_signal_lifecycle_idempotency_migration.py asserts
--   that emptiness rather than trusting it.
--
--   Nothing is lost by the split. Section 1's CHECK still fixes the
--   vocabulary for every non-NULL value the script writes, so a state the
--   script could not produce is refused by the database with a 23514
--   whether the write comes from a migration or from Python.
--
-- THE SCRIPT THAT PERFORMS IT
--   scripts/forensics/backfill_signal_order_lifecycle_state.py
--
--     python scripts/forensics/backfill_signal_order_lifecycle_state.py --check
--     python scripts/forensics/backfill_signal_order_lifecycle_state.py --apply
--
--   Applied BY HAND after this file, the same way
--   scripts/forensics/apply_migration_007.py applied 007. --check is
--   read-only and rolls back; --apply writes in one transaction and commits
--   only after re-counting.
--
--   What it does, so a reader of this file need not open it:
--     * one UPDATE per key of SIGNALS_STATUS_MAP, sorted, each setting
--       order_lifecycle_state for the rows whose legacy status normalises to
--       that key, plus ONE further UPDATE for rows reporting no legacy
--       status at all (NULL or blank), whose state is read from
--       resolve_order_lifecycle_state() rather than written down anywhere;
--     * every statement is guarded by "order_lifecycle_state IS NULL", so
--       the run is re-runnable AND cannot drag a signal the live path has
--       already moved back to whatever its legacy column still says. This is
--       the query idx_signals_lifecycle_state (section 1.4) is deliberately
--       not partial for;
--     * the comparison is normalised the way
--       order_lifecycle_state.normalise_source_value normalises - trim,
--       lower-case, then - and space folded onto _ - because signals.status
--       is a DB-unenforced text column;
--     * it REFUSES to apply while any row carries a legacy spelling the map
--       does not know, naming each one with its row count. Requirement 16.3
--       makes the mapping total over the source vocabulary, so the fix is to
--       add the spelling to order_lifecycle_state.py and re-run - never to
--       guess a state, and never to leave the row NULL and call the backfill
--       complete;
--     * it rolls back if any row is still NULL after the UPDATEs, because
--       "leave no row NULL" is this task's completion condition and a
--       half-reconciled table is worse than an untouched one;
--     * it touches nothing but signals.order_lifecycle_state: no DDL, no
--       DELETE, no TRUNCATE, and no write to the legacy status column, which
--       stays as the source it reads and as the column existing readers keep
--       reading until they are migrated.
--
-- WHAT IS TRUE ONLY AFTER THAT SCRIPT HAS RUN
--   Section 1's header records that chk_signals_order_lifecycle_state ADMITS
--   NULL, because a CHECK evaluating to NULL passes, and that NULL therefore
--   means "not yet reconciled" rather than a tenth state. That caveat ends
--   here: once the verification below returns zero, Requirement 16.1's
--   "exactly 9 values" is total over public.signals.
--
--   A NOT NULL constraint on order_lifecycle_state is still NOT added, by
--   either this file or the script. It belongs in a NEW migration file, for
--   the reason this file's header gives: adding it here would be an edit to
--   a file operators may already have applied, and nothing records which
--   files an environment has run.
--
-- ==========================================================================
-- VERIFICATION FOR SECTION 3 (run after the script; read-only)
-- ==========================================================================
--
-- -- THE COMPLETION CONDITION. Expect not_yet_reconciled = 0. Anything else
-- -- means the backfill did not finish, and every reader of
-- -- order_lifecycle_state must keep treating NULL as "not yet reconciled":
-- SELECT count(*) AS total_signals,
--        count(*) FILTER (WHERE order_lifecycle_state IS NULL)
--            AS not_yet_reconciled
--   FROM public.signals;
--
-- -- Which rows are left, and what legacy value they carry. Expect zero
-- -- rows. A row here whose legacy value is not in SIGNALS_STATUS_MAP is
-- -- what the script refuses on: add the spelling to
-- -- backend_app/backend/order_lifecycle_state.py and re-run --apply:
-- SELECT status AS legacy_status, count(*) AS rows
--   FROM public.signals
--  WHERE order_lifecycle_state IS NULL
--  GROUP BY status
--  ORDER BY rows DESC;
--
-- -- The distribution the backfill produced, against the legacy column it
-- -- read. Every pair here must be one entry of SIGNALS_STATUS_MAP - compare
-- -- against that dict, which is the only place the mapping is written:
-- SELECT status AS legacy_status, order_lifecycle_state, count(*) AS rows
--   FROM public.signals
--  GROUP BY status, order_lifecycle_state
--  ORDER BY legacy_status, order_lifecycle_state;
--
-- -- Nothing outside the vocabulary was written. Expect zero rows: the CHECK
-- -- guarantees it, and this is the query that says so out loud:
-- SELECT order_lifecycle_state, count(*)
--   FROM public.signals
--  WHERE order_lifecycle_state IS NOT NULL
--    AND order_lifecycle_state NOT IN (
--        SELECT unnest(string_to_array(
--            btrim(substring(pg_get_constraintdef(oid)
--                            FROM '\((.*)\)$'), '()'), ','))
--          FROM pg_constraint
--         WHERE conname = 'chk_signals_order_lifecycle_state')
--  GROUP BY order_lifecycle_state;
--
-- -- The legacy column is UNCHANGED by the backfill. Take this count before
-- -- and after running the script; both numbers must match, because the
-- -- script writes to order_lifecycle_state and to nothing else:
-- SELECT count(*) AS rows_with_a_legacy_status
--   FROM public.signals
--  WHERE status IS NOT NULL AND btrim(status) <> '';

-- ==========================================================================
-- END OF SECTION 3 (task 3.3). MIGRATION 005 PART B IS COMPLETE.
-- ==========================================================================

-- ==========================================================================
-- VERIFICATION (run after applying; read-only, safe to run any time)
-- ==========================================================================
--
-- -- The two columns, both nullable, both text:
-- SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--  WHERE table_schema = 'public' AND table_name = 'signals'
--    AND column_name IN ('idempotency_key', 'order_lifecycle_state')
--  ORDER BY column_name;
--
-- -- The CHECK, with its full predicate (compare against
-- -- ORDER_LIFECYCLE_STATE_VALUES in order_lifecycle_state.py):
-- SELECT conname, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--  WHERE conrelid = 'public.signals'::regclass
--    AND conname = 'chk_signals_order_lifecycle_state';
--
-- -- The three indexes this file adds, with UNIQUE/partial visible:
-- SELECT indexname, indexdef
--   FROM pg_indexes
--  WHERE schemaname = 'public' AND tablename = 'signals'
--    AND indexname IN ('uq_signals_idempotency_key',
--                      'idx_signals_lifecycle_state',
--                      'idx_signals_strategy_version')
--  ORDER BY indexname;
--
-- -- RLS and its policies, unchanged by this file:
-- SELECT c.relrowsecurity AS rls_enabled,
--        (SELECT count(*) FROM pg_policies
--          WHERE schemaname = 'public' AND tablename = 'signals') AS policies
--   FROM pg_class c WHERE c.oid = 'public.signals'::regclass;
--
-- -- How much work the task 3.3 backfill still has. Expect
-- -- not_yet_reconciled = total_signals immediately after this migration,
-- -- and 0 once the backfill has run:
-- SELECT count(*) AS total_signals,
--        count(*) FILTER (WHERE order_lifecycle_state IS NULL)
--            AS not_yet_reconciled,
--        count(*) FILTER (WHERE idempotency_key IS NOT NULL)
--            AS with_idempotency_key
--   FROM public.signals;
--
-- -- Uniqueness actually holds over non-NULL keys (expect zero rows):
-- SELECT idempotency_key, count(*)
--   FROM public.signals
--  WHERE idempotency_key IS NOT NULL
--  GROUP BY idempotency_key HAVING count(*) > 1;

-- ---- SECTION 2 (task 3.2): public.order_lifecycle_transitions ----------
--
-- -- All 7 columns. Expect id/signal_id/user_id uuid NOT NULL, to_state
-- -- text NOT NULL, from_state and reason nullable text, occurred_at
-- -- timestamptz NOT NULL DEFAULT now():
-- SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--  WHERE table_schema = 'public'
--    AND table_name = 'order_lifecycle_transitions'
--  ORDER BY ordinal_position;
--
-- -- The CHECK and both foreign keys. Both FKs must show
-- -- ON DELETE CASCADE, and the CHECK's IN-list must be identical to
-- -- chk_signals_order_lifecycle_state's:
-- SELECT conname, contype, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--  WHERE conrelid = 'public.order_lifecycle_transitions'::regclass
--    AND contype IN ('c', 'f')
--  ORDER BY contype, conname;
--
-- -- The two indexes (Requirement 21.3). idx_olt_signal_time must be
-- -- (signal_id, occurred_at) in that order:
-- SELECT indexname, indexdef
--   FROM pg_indexes
--  WHERE schemaname = 'public'
--    AND tablename = 'order_lifecycle_transitions'
--  ORDER BY indexname;
--
-- -- RLS on, and EXACTLY two policies: olt_owner_select (SELECT) and
-- -- olt_owner_insert (INSERT), both on auth.uid(). Any row with
-- -- cmd IN ('UPDATE','DELETE','ALL') means the append-only guarantee of
-- -- Requirement 16.7 has been broken:
-- SELECT c.relrowsecurity AS rls_enabled FROM pg_class c
--  WHERE c.oid = 'public.order_lifecycle_transitions'::regclass;
--
-- SELECT policyname, cmd, roles, qual, with_check
--   FROM pg_policies
--  WHERE schemaname = 'public'
--    AND tablename = 'order_lifecycle_transitions'
--  ORDER BY policyname;
--
-- -- The vocabulary cannot disagree with public.signals'. Expect the two
-- -- definitions to differ only in the column and constraint names:
-- SELECT conrelid::regclass AS table_name, conname,
--        pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--  WHERE conname IN ('chk_signals_order_lifecycle_state',
--                    'chk_order_lifecycle_transitions_to_state')
--  ORDER BY conname;
--
-- -- An out-of-vocabulary to_state must be refused by the database
-- -- (expect 23514 check_violation naming
-- -- chk_order_lifecycle_transitions_to_state):
-- BEGIN;
--   INSERT INTO public.order_lifecycle_transitions
--          (signal_id, user_id, from_state, to_state)
--   SELECT id, user_id, NULL, 'NOT_A_STATE' FROM public.signals LIMIT 1;
-- ROLLBACK;
--
-- -- One signal's history, in the order Requirement 16.7 requires:
-- SELECT from_state, to_state, reason, occurred_at
--   FROM public.order_lifecycle_transitions
--  WHERE signal_id = '<signal-uuid>'
--  ORDER BY occurred_at;
