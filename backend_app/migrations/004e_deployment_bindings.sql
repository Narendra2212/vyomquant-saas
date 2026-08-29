-- 004e_deployment_bindings.sql  (migration 004, PART 5 of 5)
--
-- PURPOSE
--   Give a deployment somewhere to record WHAT IT IS BOUND TO. Today
--   public.strategy_deployments records a version, an environment string and
--   an exchange id, and nothing else: there is no risk configuration
--   reference, no execution configuration, no deployment mode distinct from
--   the legacy environment column, and no record of which compiled plan the
--   deployment actually runs (design.md "Database": "the
--   strategy_deployments table binds an exchange_id but not a risk config or
--   execution config").
--
--   This part adds the five columns and the two CHECK constraints specified
--   in design.md "Migration 004_strategy_builder_canonical.sql", section
--   "-- 4. Deployment binding completeness".
--
--     Requirement 13.1  record the version identifier, the exchange account
--                       identifier, the risk configuration identifier, the
--                       execution configuration and the mode as ONE
--                       Deployment_Binding. version_id is already there;
--                       these four columns are the rest of the binding.
--     Requirement 13.6  WHERE a Deployment_Binding mode is 'live', an
--                       exchange account identifier is REQUIRED. That is
--                       chk_sd_live_needs_account, and it is the one
--                       execution-safety control in this file.
--
-- SCOPE - THIS FILE IS PART 5 ONLY
--   Migration 004 is landed incrementally, matching the phase that needs
--   each piece (tasks.md "Migration split"):
--     part 1  Phase 2 (task 2.2)  strategy_versions columns
--                                 -> 004_strategy_builder_canonical.sql
--     part 2  Phase 3 (task 3.2)  block_registry_snapshots
--                                 -> 004b_block_registry_snapshots.sql
--     part 3  Phase 4 (task 4.1)  chk_valid_requires_hash
--                                 + reject_immutable_version_update()
--                                 + trg_sv_immutable
--                                 -> 004c_immutable_versions.sql
--     part 4  Phase 6 (task 6.1)  training_jobs, model_versions
--                                 -> 004d_training_and_models.sql
--     part 5  Phase 8 (task 8.1)  strategy_deployments columns  <- THIS FILE
--                                 -> 004e_deployment_bindings.sql
--
-- WHY A SEPARATE FILE RATHER THAN AN EDIT TO PART 1
--   Task 8.1 says "extend 004_strategy_builder_canonical.sql". It is landed
--   as a sibling file instead, for the three reasons 004b established for
--   part 2 and 004c and 004d restated for parts 3 and 4:
--     1. Migrations here are applied BY HAND, per file, and NOTHING RECORDS
--        WHICH FILES AN ENVIRONMENT HAS RUN - there is no migration table
--        and no migration step in .github/workflows/03-deploy.yml.
--        Appending to a file an operator may already have applied leaves no
--        signal that the file changed, so the new statements would simply
--        never run. A new filename is the signal.
--     2. Part 1's header states "THIS FILE IS PART 1 ONLY" and enumerates
--        the parts as separate landings, naming this one as part 5.
--     3. 004b, 004c and 004d already set the "004<letter>" naming precedent
--        for this migration number, itself following 003's precedent
--        (003_signal_trace_preflight.sql beside
--        003_signal_trace_restoration.sql).
--   The judgement is stated rather than assumed: editing part 1 in place
--   WOULD have been defensible if part 1 were provably unapplied
--   everywhere, but that is exactly what cannot be established here -
--   nothing in this repository records what any environment has run, and
--   part 1's own header says it is applied by hand. An unverifiable
--   assumption about an already-shipped migration is not a safe basis for
--   an in-place edit, so the in-place edit is not made. Part 1 is left
--   byte-identical apart from one comment line naming this file in its
--   split enumeration.
--
--   Order: this file needs only public.strategy_deployments, which
--   001_strategy_architecture.sql and 003_signal_trace_restoration.sql both
--   create. It does not depend on parts 1 to 4 and they do not depend on
--   it.
--
-- SOURCE OF THE DEFINITION
--   design.md "Migration 004_strategy_builder_canonical.sql", section 4,
--   column for column and predicate for predicate, schema-qualified with
--   public. and split from the design's two chained ALTER statements into
--   one ALTER for the columns plus one guarded ALTER per constraint (see
--   deviation 3). Nothing the design specifies for this table is omitted
--   and nothing it does not specify is added to the table.
--
-- FOUR DELIBERATE ADDITIONS BEYOND THE DESIGN'S DDL, AND WHY
--   The design snippet is a schema sketch. These four make it safe to
--   apply by hand, twice, to a database whose history nobody recorded.
--   None weakens a control and none changes a column, constraint, index or
--   policy the design specifies.
--
--   1. Preflight assertions (section 0). A readable RAISE naming the
--      missing object instead of a bare 42P01 from inside an ALTER, plus
--      the RLS check described under "WHY THIS FILE REFUSES TO RUN WITHOUT
--      RLS" below.
--
--   2. A column shape assertion (section 2). ADD COLUMN IF NOT EXISTS is
--      SILENT when a column of that name already exists WITH A DIFFERENT
--      TYPE OR NULLABILITY - for instance one added by hand during an
--      investigation. That silence matters more here than anywhere else in
--      migration 004, because of this:
--
--        A CHECK CONSTRAINT THAT EVALUATES TO NULL PASSES.
--
--      If mode were pre-existing and NULLABLE, then for a row with
--      mode IS NULL both of this file's constraints evaluate to NULL and
--      therefore ADMIT the row: chk_sd_mode would not fix the vocabulary
--      and chk_sd_live_needs_account would not require an account. The
--      constraints would look present in pg_constraint and enforce
--      nothing. So mode's NOT NULL is not decoration, it is what makes the
--      vocabulary total, and section 2 refuses to continue without it
--      rather than leaving a constraint that reports success and admits
--      everything. Same reasoning for execution_config's NOT NULL, and the
--      two UUID columns are type-checked because a TEXT column of the same
--      name would make task 8.2's ownership comparison a string compare.
--
--   3. Guarded ADD CONSTRAINT blocks (sections 3 and 4). The design writes
--      the two constraints as a bare chained
--      "ALTER TABLE ... ADD CONSTRAINT a ..., ADD CONSTRAINT b ...", which
--      is NOT idempotent: a second run raises 42710 duplicate_object and
--      aborts. Each constraint is therefore added only when pg_constraint
--      holds no constraint of that name on this table. PostgreSQL has no
--      ADD CONSTRAINT IF NOT EXISTS, and DROP-then-ADD would both violate
--      this file's no-DROP rule and leave a window inside the transaction
--      where the invariant is not enforced. This is part 1 section 2's
--      pattern and 004d section 1b's pattern.
--
--   4. Column comments (section 5) recording intent the schema cannot
--      express: that exchange_account_id is a REFERENCE and never a
--      credential, that mode is not the legacy environment column, and
--      what dag_hash is for.
--
--   5. A "nothing else changed" postflight (section 6). See below.
--
-- THE HARD CONSTRAINT: EXISTING DEPLOYMENT RLS AND INDEXES ARE UNTOUCHED
--   public.strategy_deployments already carries, from
--   003_signal_trace_restoration.sql sections 8 and 9 (equivalently
--   001_strategy_architecture.sql):
--
--     RLS      enabled, with three policies, all keyed on
--              user_id = auth.uid()
--                "Users can view their own deployments"    SELECT
--                "Users can insert their own deployments"  INSERT
--                "Users can update their own deployments"  UPDATE
--              and NO DELETE policy, so a deployment record cannot be
--              erased through the API.
--     indexes  idx_strategy_deployments_strategy_id
--              idx_strategy_deployments_user_id
--              idx_strategy_deployments_status
--              idx_strategy_deployments_environment
--              idx_strategy_deployments_created_at
--     trigger  trigger_update_strategy_deployments_updated_at
--              (BEFORE UPDATE -> update_updated_at_column())
--
--   THIS FILE CHANGES NONE OF IT. It contains no CREATE POLICY, no
--   ALTER POLICY, no DROP POLICY, no ENABLE/DISABLE ROW LEVEL SECURITY, no
--   CREATE INDEX, no DROP INDEX, no CREATE TRIGGER, no GRANT and no
--   REVOKE. That is a fact about the text of the file and it is asserted by
--   tests/test_deployment_binding_columns_migration.py, not merely promised
--   here.
--
--   Section 6 turns it into a fact about the DATABASE as well, without
--   hard-coding a single policy or index name: section 0 records how many
--   policies and how many indexes public.strategy_deployments has, in two
--   transaction-local settings, and section 6 recounts them and raises if
--   either number moved. A rename in some environment cannot false-alarm
--   it, because both counts are taken inside this one transaction.
--
--   Row level security is ROW-scoped, not column-scoped, so the three
--   existing policies keep applying unchanged to the five new columns: a
--   user can read, insert and update the binding fields of their own
--   deployment rows and no others. Tenant isolation on this table is
--   exactly as strong after this migration as before it. What the DATABASE
--   still does not do is check that a referenced exchange account or risk
--   config belongs to the same user - see "NO FOREIGN KEYS" below.
--
-- WHY THIS FILE REFUSES TO RUN WITHOUT RLS ON strategy_deployments
--   exchange_account_id is the handle by which a running deployment
--   resolves REAL EXCHANGE CREDENTIALS
--   (credential_vault.load_decrypted_keys(user_id, exchange_account_id),
--   design.md "Deployment binding"), and mode = 'live' is the flag that
--   makes fills real. Adding those two columns to a table whose row-level
--   isolation is off would mean any authenticated caller could read or
--   rewrite which account another user's live deployment trades through.
--   So section 0 raises if relrowsecurity is false on this table, rather
--   than proceeding. It is a refusal to make an existing hole worse, not a
--   claim to fix it: the remedy is to apply
--   003_signal_trace_restoration.sql sections 8 and 9 first. Nothing in
--   this file enables RLS itself, because enabling it here would be a
--   change to the very control task 8.1 is told to leave alone.
--
-- NO FOREIGN KEYS ON exchange_account_id OR risk_config_id, AND WHAT THAT
-- COSTS
--   The design declares both as bare UUID with no REFERENCES clause, and
--   that is what is landed. The reason is not stylistic and it is worth
--   recording, because the next reader will ask:
--
--     * There is no risk_configs table in this repository at all. Per-user
--       risk configuration lives in public.risk_settings
--       (migrations/create_risk_settings_tables.sql,
--       migrations/006_reconcile_production_database.sql).
--     * There are two candidate exchange-account tables -
--       public.exchange_keys (migrations/003_create_exchange_keys_table.sql)
--       and public.exchange_connections
--       (migrations/004_create_exchange_connections_table.sql) - and the
--       public.exchanges table that 001 references is noted by 003 as not
--       existing in production.
--     * All three of those tables declare "id TEXT PRIMARY KEY DEFAULT
--       gen_random_uuid()". A UUID column cannot carry a foreign key to a
--       TEXT primary key: the ALTER would fail with 42804 datatype
--       mismatch. So a FK here is not merely omitted, it is not
--       expressible today.
--
--   UUID is kept anyway, as the design specifies, and it earns its keep as
--   a format guard: a non-UUID account identifier cannot be silently
--   stored (strategy_service.deploy_strategy already screens
--   exchange_account_id through UUID() before writing exchange_id, so this
--   matches the code's existing expectation).
--
--   STATED PLAINLY: THE DATABASE DOES NOT ENFORCE THAT A REFERENCED
--   EXCHANGE ACCOUNT OR RISK CONFIG BELONGS TO THE DEPLOYING USER.
--   Requirement 13.2 is therefore application-enforced, in the one
--   transaction that creates the binding - that is task 8.2's job and
--   design.md's own pseudocode
--   ("ASSERT account.user_id = user.id", "ASSERT
--   risk_config_owned_by(...)"). Do not read the presence of these two
--   columns as a cross-tenant guard.
--
-- mode IS NOT environment, AND WHAT THAT LEAVES OPEN UNTIL TASK 8.2
--   public.strategy_deployments already has
--   "environment VARCHAR(20) NOT NULL DEFAULT 'paper'", documented by 001
--   as "paper, live, cloud, local" - it mixes the paper/live distinction
--   with a worker-placement notion. mode is the Deployment_Binding field
--   of Requirement 13.1 and its vocabulary is exactly paper|live.
--   environment is NOT touched, NOT constrained and NOT backfilled by this
--   file: it has existing rows, an index
--   (idx_strategy_deployments_environment) and a writer, and rewriting or
--   constraining it is neither in task 8.1 nor in the design.
--
--   THE CONSEQUENCE IS LOAD-BEARING AND MUST NOT BE MISREAD:
--   chk_sd_live_needs_account guards mode, NOT environment. The current
--   writer (strategy_service.deploy_strategy) sets environment and does
--   NOT set mode, so until task 8.2 populates mode, a deployment created
--   with environment = 'live' and no exchange account will be stored with
--   mode = 'paper' by default and the constraint will correctly not fire -
--   the row is not claiming to be a live binding. The database-level
--   Requirement 13.6 guarantee therefore only covers writers that set
--   mode. TASK 8.2 MUST SET mode ON EVERY DEPLOYMENT IT CREATES, and must
--   keep enforcing 13.6 in the handler as well, so a refusal is a 4xx
--   naming the missing account rather than a 23514 check_violation
--   surfacing as a 500.
--
-- APPLICATION
--   NOT applied automatically. .github/workflows/03-deploy.yml has no
--   migration step (its jobs are pre-deployment validation, ECR verify,
--   ECS deploy, reports), so migrations in this repo are applied by hand,
--   per file, the way scripts/forensics/apply_migration_007.py applied 007.
--   Apply this file explicitly against the target database and then run the
--   verification queries at the bottom.
--
--   Until it is applied, these five columns do not exist. Every code path
--   that reads or writes them - task 8.2's binding insert above all - must
--   degrade the way registry_snapshot_service and the training surface
--   already do: a WARNING NAMING THIS FILE
--   ("004e_deployment_bindings.sql"), not a 500, and never a deployment
--   reported as bound when the binding was not stored. A missing column
--   surfaces from PostgREST as an undefined-column error on the insert,
--   which is the same class the training paths classify today.
--
-- SAFETY
--   * Additive only. No DROP, no TRUNCATE, no DELETE, no UPDATE, no
--     ALTER COLUMN TYPE, no CREATE/ALTER/DROP POLICY, no
--     ENABLE/DISABLE ROW LEVEL SECURITY, no CREATE/DROP INDEX, no
--     CREATE TRIGGER, no GRANT, no REVOKE. There is no data-loss path in
--     this file and no statement in it modifies a row.
--   * Fully idempotent. ADD COLUMN IF NOT EXISTS for every column; every
--     constraint behind a pg_constraint existence guard; COMMENT replaces.
--     A re-run adds nothing and raises nothing.
--   * NOT NULL is only ever paired with a DEFAULT, so PostgreSQL 11+
--     backfills existing rows in place and neither CHECK can be violated
--     by pre-existing data: every existing row becomes
--     mode = 'paper', execution_config = '{}', which chk_sd_mode admits
--     and which chk_sd_live_needs_account does not constrain.
--   * One transaction. Either all five columns, both constraints and both
--     comments exist, or none of them do. A table with mode present but
--     chk_sd_mode absent is never visible to a session.
--
-- WHAT IS DELIBERATELY NOT ADDED
--   * No index on any new column. Task 8.1 says leave the deployment
--     indexes untouched and the design specifies none for this section.
--     mode is low-cardinality and every deployment query in
--     strategy_operations.py already filters by user_id or strategy_id,
--     which the existing indexes serve.
--   * No CHECK that execution_config is a JSON object. It would be a
--     constraint the design does not specify, on a column whose body is
--     validated by task 8.2's request model, and it could reject rows a
--     writer not yet revised for it produces. The shape of that document
--     (max_order_notional, max_open_positions, slippage_tolerance_bps,
--     ...) is design.md's "Deployment binding" and belongs to 8.2.
--   * No constraint on the legacy environment column, and no backfill of
--     it from mode or of mode from it. Reasons above.
--   * No BEFORE UPDATE trigger. Unlike 004d's new tables, this one already
--     has trigger_update_strategy_deployments_updated_at from 003, which
--     keeps maintaining updated_at for writes to the new columns too.
--     Attaching a second trigger would double-fire.
--   * No RLS or grant change of any kind. That is the hard constraint of
--     task 8.1.

BEGIN;

-- 0) Preflight ---------------------------------------------------------
-- Read-only assertions plus two transaction-local counters. Fails with a
-- readable message instead of a bare undefined_table error, refuses to add
-- a live-trading binding to a table without row-level security, and records
-- the policy and index inventory so section 6 can prove this file left both
-- alone.
-- Idempotent: it reads catalogues and writes nothing but two settings that
-- are local to this transaction.
DO $$
DECLARE
    rls_on     BOOLEAN;
    policies   INTEGER;
    indexes    INTEGER;
BEGIN
    IF to_regclass('public.strategy_deployments') IS NULL THEN
        RAISE EXCEPTION
            '004 part 5 precondition failed: table '
            'public.strategy_deployments does not exist. Apply '
            'backend_app/migrations/001_strategy_architecture.sql (or '
            '003_signal_trace_restoration.sql) first.';
    END IF;

    -- exchange_account_id resolves real exchange credentials and
    -- mode = 'live' makes fills real. Neither column belongs on a table
    -- whose row-level isolation is off. This file will not enable RLS
    -- itself - that would be a change to the control task 8.1 must leave
    -- untouched - so it stops instead.
    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c
     WHERE c.oid = 'public.strategy_deployments'::regclass;

    IF NOT rls_on THEN
        RAISE EXCEPTION
            '004 part 5 refuses to run: row level security is DISABLED on '
            'public.strategy_deployments. This migration adds the exchange '
            'account binding and the live-mode flag, so applying it to a '
            'table without row-level isolation would expose which account '
            'another user''s live deployment trades through. Apply '
            'backend_app/migrations/003_signal_trace_restoration.sql '
            'sections 8 and 9 (RLS + the three owner policies) first. This '
            'file deliberately does not enable RLS itself.';
    END IF;

    -- The "nothing else changed" baseline, checked again in section 6.
    -- No policy or index NAME is hard-coded, so an environment that
    -- renamed one cannot false-alarm; both counts are taken inside this
    -- transaction, so they are comparable by construction.
    SELECT count(*) INTO policies
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'strategy_deployments';

    SELECT count(*) INTO indexes
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'strategy_deployments';

    IF policies = 0 THEN
        RAISE EXCEPTION
            '004 part 5 refuses to run: row level security is enabled on '
            'public.strategy_deployments but it has NO policies, so the '
            'table is unreachable to every non-superuser role and a '
            'deployment binding written here could not be read back. '
            'Apply backend_app/migrations/003_signal_trace_restoration.sql '
            'section 9 first.';
    END IF;

    PERFORM set_config('aerora.sd_policies_before', policies::TEXT, true);
    PERFORM set_config('aerora.sd_indexes_before',  indexes::TEXT,  true);

    RAISE NOTICE '004 part 5 preflight: public.strategy_deployments has RLS '
                 'enabled, % policies and % indexes; this file adds neither '
                 'and section 6 verifies both counts are unchanged.',
                 policies, indexes;
END $$;

-- 1) Deployment binding completeness -----------------------------------
-- design.md "Migration 004_strategy_builder_canonical.sql", section 4,
-- "-- 4. Deployment binding completeness", column for column.
--
--   exchange_account_id  A REFERENCE INTO THE CREDENTIAL VAULT, NEVER A
--                        CREDENTIAL (design.md "Deployment binding":
--                        "reference into credential vault - NOT a
--                        credential"). The api key, secret and passphrase
--                        stay in credential_vault / api_key_vault and are
--                        resolved by
--                        load_decrypted_keys(user_id, exchange_account_id)
--                        inside the execution process only (Requirement
--                        13.9). Nullable, because a paper deployment needs
--                        no account; chk_sd_live_needs_account is what
--                        makes it mandatory for live (Requirement 13.6).
--                        No FK - see "NO FOREIGN KEYS" in the header.
--   risk_config_id       Which risk configuration this deployment runs
--                        under, so one validated version can run under
--                        different limits without being edited
--                        (Requirement 13's user story). No FK, same
--                        reason. Ownership is asserted by the handler
--                        (Requirement 13.2), not by this schema.
--   execution_config     max_order_notional, max_open_positions,
--                        slippage_tolerance_bps and the rest of
--                        design.md's execution block. NOT NULL DEFAULT
--                        '{}' so no deployment row is ever without one and
--                        existing rows are backfilled in place. Holds NO
--                        credential of any kind (Requirement 21.7).
--   mode                 paper | live, fixed by chk_sd_mode below. This is
--                        the Deployment_Binding mode of Requirement 13.1
--                        and it is NOT the legacy environment column - see
--                        "mode IS NOT environment" in the header. Defaults
--                        to 'paper', which is the safe default: the value
--                        that does not reach a real order router and the
--                        one chk_sd_live_needs_account does not constrain.
--   dag_hash             The identity hash of the exact compiled plan this
--                        deployment runs. TEXT, matching
--                        strategy_versions.dag_hash (part 1) and
--                        strategies.dag_hash
--                        (migrations/add_billing_currency_and_dag_hash.sql);
--                        16 hex characters from
--                        strategy_dag.schema.compute_dag_hash. It is a
--                        READABLE FIELD on CompiledPlan and never a method
--                        (SB-02: the clone path once called
--                        compiled.get("dag_hash") on an object that only
--                        had compute_hash(), the AttributeError was
--                        swallowed, and every clone persisted with no hash
--                        at all) - so the value written here is
--                        plan.dag_hash, never a recomputation a caller
--                        could forget. Requirement 22.5 reads it: if a
--                        stored plan's hash differs from the hash
--                        recomputed from the version's canonical graph,
--                        the consumer recompiles BEFORE executing.
--                        Nullable, because rows written before this
--                        migration have no plan identity to record and
--                        inventing one would be a fabricated fact.
--
-- Idempotent: every clause is ADD COLUMN IF NOT EXISTS, so a re-run skips
-- each column already present. The two NOT NULL columns carry a DEFAULT,
-- so the first run backfills existing rows and no row is left in a state
-- either constraint below rejects.
ALTER TABLE public.strategy_deployments
    ADD COLUMN IF NOT EXISTS exchange_account_id UUID,
    ADD COLUMN IF NOT EXISTS risk_config_id      UUID,
    ADD COLUMN IF NOT EXISTS execution_config    JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN IF NOT EXISTS mode                TEXT NOT NULL DEFAULT 'paper',
    ADD COLUMN IF NOT EXISTS dag_hash            TEXT;

-- 2) Column shape assertion --------------------------------------------
-- ADD COLUMN IF NOT EXISTS is SILENT about a pre-existing column of the
-- same name and a different type or nullability. For mode that silence
-- would be severe rather than cosmetic: A CHECK CONSTRAINT THAT EVALUATES
-- TO NULL PASSES, so a nullable mode makes both constraints below admit
-- any row with mode IS NULL - chk_sd_mode would not fix the vocabulary and
-- chk_sd_live_needs_account would not require an account, while both would
-- still be listed in pg_constraint. mode's NOT NULL is therefore part of
-- the control, not decoration, and this block refuses to continue without
-- it.
--
-- The two UUID columns are type-checked for a related reason: a TEXT
-- column of the same name would turn task 8.2's ownership comparison into
-- a string compare and would accept an identifier that is not a UUID at
-- all.
--
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    problems TEXT;
BEGIN
    SELECT string_agg(
               format('%s (expected %s%s, found %s%s)',
                      expected.column_name,
                      expected.data_type,
                      CASE WHEN expected.not_null THEN ' NOT NULL' ELSE '' END,
                      coalesce(actual.data_type, 'no such column'),
                      CASE
                          WHEN actual.column_name IS NULL THEN ''
                          WHEN actual.is_nullable = 'NO' THEN ' NOT NULL'
                          ELSE ' NULL'
                      END),
               '; ' ORDER BY expected.column_name)
      INTO problems
      FROM (VALUES ('exchange_account_id', 'uuid',  FALSE),
                   ('risk_config_id',      'uuid',  FALSE),
                   ('execution_config',    'jsonb', TRUE),
                   ('mode',                'text',  TRUE),
                   ('dag_hash',            'text',  FALSE))
             AS expected(column_name, data_type, not_null)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema = 'public'
            AND actual.table_name   = 'strategy_deployments'
            AND actual.column_name  = expected.column_name
     WHERE actual.column_name IS NULL
        -- 'character varying' is accepted wherever 'text' is expected: a
        -- VARCHAR(n) mode or dag_hash is functionally equivalent for these
        -- two columns and is what a hand-added column most likely is.
        OR NOT (actual.data_type = expected.data_type
                OR (expected.data_type = 'text'
                    AND actual.data_type = 'character varying'))
        OR (expected.not_null AND actual.is_nullable <> 'NO');

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.strategy_deployments has binding column(s) of the wrong '
            'shape: %. A pre-existing column of that name was left as it '
            'was, because ADD COLUMN IF NOT EXISTS does not alter one. '
            'Reconcile it by hand before re-running this migration. NOTE '
            'why mode NOT NULL is required: a CHECK that evaluates to NULL '
            'PASSES, so a nullable mode would leave chk_sd_mode and '
            'chk_sd_live_needs_account listed in pg_constraint while '
            'enforcing nothing for rows with mode IS NULL (Requirement '
            '13.6).', problems;
    END IF;
END $$;

-- 3) chk_sd_mode -------------------------------------------------------
-- design.md section 4, verbatim: CHECK (mode IN ('paper','live')).
--
-- THE VOCABULARY AGREES WITH THE CODE, IT IS NOT A THIRD ONE.
-- backend_app/backend/market_data_contract.py (task 7.5) defines
-- MODE_BACKTEST = 'backtest', MODE_PAPER = 'paper', MODE_LIVE = 'live',
-- and its own comment on MODE_PAPER says "chk_sd_mode (design.md) admits
-- this as a deployment mode". The two spellings that reach an order router
-- - paper and live - are exactly the two admitted here.
--
-- 'backtest' is deliberately NOT admitted, and that is not a disagreement
-- with the code: MODE_BACKTEST describes "a bounded historical read: node
-- preview, backtest, training window. No order router is downstream of
-- it", which is not a deployment at all. A backtest run is a
-- strategy_backtests row, not a strategy_deployments row, so admitting
-- 'backtest' here would let a bounded historical read masquerade as a
-- running deployment - and, since chk_sd_live_needs_account only
-- constrains 'live', do so without any account binding. Requirement 13.10
-- likewise enumerates deployment STATES with no backtest among them.
--
-- Idempotent: the ADD CONSTRAINT runs only when pg_constraint holds no
-- constraint of that name on this table, so a re-run adds nothing and
-- cannot raise 42710 duplicate_object. The design's bare chained
-- ADD CONSTRAINT would abort on a second run.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.strategy_deployments'::regclass
           AND conname  = 'chk_sd_mode'
    ) THEN
        ALTER TABLE public.strategy_deployments
            ADD CONSTRAINT chk_sd_mode
                CHECK (mode IN ('paper','live'));
    END IF;
END $$;

-- 4) chk_sd_live_needs_account -----------------------------------------
-- design.md section 4, verbatim:
--   CHECK (mode <> 'live' OR exchange_account_id IS NOT NULL)
--
-- Requirement 13.6: WHERE a Deployment_Binding mode is 'live', an exchange
-- account identifier is REQUIRED. This is the execution-safety control of
-- this file and task 8.2 depends on it, so the predicate's behaviour is
-- spelled out row by row rather than left to be re-derived:
--
--   mode = 'live', account NULL      -> FALSE OR FALSE = FALSE  -> REJECTED
--   mode = 'live', account present   -> FALSE OR TRUE  = TRUE   -> admitted
--   mode = 'paper', account NULL     -> TRUE  OR FALSE = TRUE   -> admitted
--   mode = 'paper', account present  -> TRUE  OR TRUE  = TRUE   -> admitted
--   mode IS NULL, any account        -> NULL  OR ...            -> see below
--
-- The fourth row is deliberate, not an oversight: binding an account to a
-- paper deployment is legal and useful (it is how an author paper-trades
-- against the venue they intend to go live on), so the constraint requires
-- an account for live and says nothing about paper. It constrains exactly
-- one direction, which is exactly what 13.6 asks for.
--
-- The fifth row is why section 2 exists. A CHECK that evaluates to NULL
-- PASSES. With mode IS NULL the predicate is NULL OR (account IS NOT NULL),
-- which is TRUE when an account is present and NULL - therefore admitted -
-- when it is not. mode's NOT NULL is what removes that row from the input
-- space altogether, and section 2 refuses to apply this file if some
-- pre-existing nullable mode column would leave it reachable.
--
-- Note also what this constraint does NOT cover: the legacy environment
-- column. A row with environment = 'live' and mode = 'paper' is admitted,
-- because it is not claiming to be a live BINDING. See "mode IS NOT
-- environment" in the header - task 8.2 must set mode.
--
-- Idempotent: guarded on pg_constraint by name, as in section 3.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.strategy_deployments'::regclass
           AND conname  = 'chk_sd_live_needs_account'
    ) THEN
        ALTER TABLE public.strategy_deployments
            ADD CONSTRAINT chk_sd_live_needs_account
                CHECK (mode <> 'live' OR exchange_account_id IS NOT NULL);
    END IF;
END $$;

-- 5) Column comments ---------------------------------------------------
-- Intent the schema cannot express, recorded where \d+ and every schema
-- browser will show it. COMMENT is naturally idempotent - it replaces.
-- No COMMENT is placed on the table itself or on any pre-existing column:
-- 001 already comments the table and environment, and replacing either
-- would be a change to something task 8.1 leaves alone.
COMMENT ON COLUMN public.strategy_deployments.exchange_account_id IS
    'Reference into the credential vault, NEVER a credential. The api key, secret and '
    'passphrase stay in credential_vault / api_key_vault and are resolved by '
    'load_decrypted_keys(user_id, exchange_account_id) inside the execution process only '
    '(Requirement 13.9, 21.7). Required when mode is live, by '
    'chk_sd_live_needs_account (Requirement 13.6). No foreign key: the candidate account '
    'tables key on TEXT, so ownership of the referenced account is asserted by the '
    'deployment handler in one transaction with the insert (Requirement 13.2), NOT by '
    'this schema.';

COMMENT ON COLUMN public.strategy_deployments.risk_config_id IS
    'Which risk configuration this deployment runs under, so one validated version can run '
    'under different limits without being edited (Requirement 13.1). No foreign key: '
    'ownership of the referenced config is asserted by the deployment handler, not by this '
    'schema (Requirement 13.2).';

COMMENT ON COLUMN public.strategy_deployments.execution_config IS
    'Execution settings for this binding: max_order_notional, max_open_positions, '
    'slippage_tolerance_bps and the rest of the design''s execution block. Contains NO '
    'exchange identifier and NO credential of any kind (Requirement 21.7).';

COMMENT ON COLUMN public.strategy_deployments.mode IS
    'Deployment_Binding mode, paper or live, fixed by chk_sd_mode (Requirement 13.1). '
    'NOT the legacy environment column, which also carries cloud and local. Defaults to '
    'paper, the value that reaches no real order router. mode is NOT NULL by design: a '
    'CHECK that evaluates to NULL passes, so a nullable mode would leave both chk_sd_mode '
    'and chk_sd_live_needs_account enforcing nothing.';

COMMENT ON COLUMN public.strategy_deployments.dag_hash IS
    'Identity hash of the exact compiled plan this deployment runs, from '
    'strategy_dag.schema.compute_dag_hash. A readable FIELD on CompiledPlan, never a '
    'method (SB-02). Requirement 22.5: if a stored plan''s hash differs from the hash '
    'recomputed from the version''s canonical graph, the consumer recompiles before '
    'executing. NULL on rows written before this migration - no plan identity is invented '
    'for them.';

-- 6) Nothing else changed ----------------------------------------------
-- Task 8.1's hard constraint, verified rather than promised: the policy and
-- index counts recorded in section 0 must be unchanged. No name is
-- hard-coded, so a renamed policy or index in some environment cannot
-- false-alarm this; both counts are taken inside this one transaction.
--
-- This block cannot pass by accident: it reads pg_policies and pg_indexes
-- again, after every statement above has run.
--
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    policies_before INTEGER := current_setting('aerora.sd_policies_before')::INTEGER;
    indexes_before  INTEGER := current_setting('aerora.sd_indexes_before')::INTEGER;
    policies_now    INTEGER;
    indexes_now     INTEGER;
    rls_on          BOOLEAN;
BEGIN
    SELECT count(*) INTO policies_now
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'strategy_deployments';

    SELECT count(*) INTO indexes_now
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'strategy_deployments';

    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c
     WHERE c.oid = 'public.strategy_deployments'::regclass;

    IF policies_now <> policies_before THEN
        RAISE EXCEPTION
            '004 part 5 postcondition failed: the policy count on '
            'public.strategy_deployments moved from % to %. This file must '
            'leave the existing deployment RLS untouched (task 8.1).',
            policies_before, policies_now;
    END IF;

    IF indexes_now <> indexes_before THEN
        RAISE EXCEPTION
            '004 part 5 postcondition failed: the index count on '
            'public.strategy_deployments moved from % to %. This file must '
            'leave the existing deployment indexes untouched (task 8.1).',
            indexes_before, indexes_now;
    END IF;

    IF NOT rls_on THEN
        RAISE EXCEPTION
            '004 part 5 postcondition failed: row level security on '
            'public.strategy_deployments is no longer enabled.';
    END IF;

    RAISE NOTICE '004 part 5 complete: 5 binding columns and 2 constraints '
                 'added; RLS still enabled with % policies and % indexes, '
                 'both unchanged.', policies_now, indexes_now;
END $$;

COMMIT;

-- VERIFICATION (run after applying) ------------------------------------
--
-- 1. Columns. Expect 5 rows. execution_config and mode must show
--    is_nullable = NO with their defaults ('{}'::jsonb and 'paper'::text);
--    exchange_account_id and risk_config_id must show data_type = uuid and
--    is_nullable = YES; dag_hash text, YES.
--
--    mode's is_nullable = NO is not cosmetic. A CHECK that evaluates to
--    NULL passes, so if mode were nullable both constraints below would
--    admit every row with mode IS NULL while still appearing in
--    pg_constraint.
--
--   SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'public'
--     AND table_name   = 'strategy_deployments'
--     AND column_name IN ('exchange_account_id','risk_config_id',
--                         'execution_config','mode','dag_hash')
--   ORDER BY column_name;
--
-- 2. Constraints. Expect exactly 2 rows, with these definitions:
--      chk_sd_live_needs_account
--        CHECK (((mode <> 'live'::text) OR (exchange_account_id IS NOT NULL)))
--      chk_sd_mode
--        CHECK ((mode = ANY (ARRAY['paper'::text, 'live'::text])))
--
--   SELECT conname, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--   WHERE conrelid = 'public.strategy_deployments'::regclass
--     AND conname IN ('chk_sd_mode','chk_sd_live_needs_account')
--   ORDER BY conname;
--
-- 3. Requirement 13.6, exercised directly. THIS IS THE CHECK THAT MATTERS:
--    a live binding with no exchange account must be REFUSED by the
--    database, with 23514 check_violation naming
--    chk_sd_live_needs_account.
--
--   BEGIN;
--     -- expect 23514 check_violation "chk_sd_live_needs_account"
--     UPDATE public.strategy_deployments
--        SET mode = 'live', exchange_account_id = NULL
--      WHERE id = (SELECT id FROM public.strategy_deployments LIMIT 1);
--   ROLLBACK;
--
--    And the same row must be ACCEPTED once an account is present - the
--    constraint must not be refusing live deployments outright:
--
--   BEGIN;
--     -- expect success
--     UPDATE public.strategy_deployments
--        SET mode = 'live', exchange_account_id = gen_random_uuid()
--      WHERE id = (SELECT id FROM public.strategy_deployments LIMIT 1);
--   ROLLBACK;
--
--    And a paper binding with no account must be accepted, since the
--    constraint constrains one direction only:
--
--   BEGIN;
--     -- expect success
--     UPDATE public.strategy_deployments
--        SET mode = 'paper', exchange_account_id = NULL
--      WHERE id = (SELECT id FROM public.strategy_deployments LIMIT 1);
--   ROLLBACK;
--
-- 4. chk_sd_mode: anything outside paper|live is refused, including
--    'backtest' (which is a bounded historical read, not a deployment) and
--    including NULL, which is refused by the column's NOT NULL rather than
--    by the CHECK.
--
--   BEGIN;
--     -- expect 23514 check_violation "chk_sd_mode"
--     UPDATE public.strategy_deployments SET mode = 'backtest'
--      WHERE id = (SELECT id FROM public.strategy_deployments LIMIT 1);
--   ROLLBACK;
--
--   BEGIN;
--     -- expect 23502 not_null_violation on column "mode"
--     UPDATE public.strategy_deployments SET mode = NULL
--      WHERE id = (SELECT id FROM public.strategy_deployments LIMIT 1);
--   ROLLBACK;
--
-- 5. Existing RLS UNTOUCHED (task 8.1's hard constraint). Expect the same
--    3 policies that existed before this migration - SELECT / INSERT /
--    UPDATE, each USING or WITH CHECK (user_id = auth.uid()) - and
--    rowsecurity = true. A row with cmd DELETE or ALL appearing here, or a
--    policy whose qual is no longer scoped to auth.uid(), would mean a
--    control was widened.
--
--   SELECT policyname, cmd, roles, qual, with_check
--   FROM pg_policies
--   WHERE schemaname = 'public' AND tablename = 'strategy_deployments'
--   ORDER BY policyname;
--
--   SELECT relrowsecurity, relforcerowsecurity
--   FROM pg_class WHERE oid = 'public.strategy_deployments'::regclass;
--
-- 6. Existing indexes UNTOUCHED. Expect the same 5 idx_strategy_deployments_*
--    indexes plus the primary key, and NO new index on any binding column.
--
--   SELECT indexname, indexdef
--   FROM pg_indexes
--   WHERE schemaname = 'public' AND tablename = 'strategy_deployments'
--   ORDER BY indexname;
--
-- 7. Existing trigger UNTOUCHED, and still the only one:
--    trigger_update_strategy_deployments_updated_at from 003.
--
--   SELECT tgname FROM pg_trigger
--   WHERE tgrelid = 'public.strategy_deployments'::regclass
--     AND NOT tgisinternal
--   ORDER BY tgname;
--
-- 8. Grants UNTOUCHED. This file issues no GRANT and no REVOKE, so this is
--    a before/after comparison rather than an expected list.
--
--   SELECT grantee, privilege_type
--   FROM information_schema.role_table_grants
--   WHERE table_schema = 'public' AND table_name = 'strategy_deployments'
--   ORDER BY grantee, privilege_type;
--
-- 9. The legacy environment column is untouched: same type, same default,
--    same values, and still no CHECK on it. A row with
--    environment = 'live' and mode = 'paper' is expected and legal until
--    task 8.2 sets mode on the write path.
--
--   SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'public' AND table_name = 'strategy_deployments'
--     AND column_name = 'environment';
--
--   SELECT mode, environment, count(*)
--   FROM public.strategy_deployments
--   GROUP BY mode, environment ORDER BY mode, environment;
--
-- 10. Idempotency. Re-running this whole file must report no error and
--     leave the results of 1, 2, 5, 6, 7 and 8 unchanged.
