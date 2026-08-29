-- 004c_immutable_versions.sql  (migration 004, PART 3 of 5)
--
-- PURPOSE
--   Make defect SB-02 unrepresentable at the database layer and enforce
--   version immutability procedurally as well as by convention.
--
--   Part 1 (004_strategy_builder_canonical.sql) added dag_hash,
--   compiled_plan, validation_state, lifecycle_state and schema_version to
--   strategy_versions but DELIBERATELY deferred two pieces because the
--   writer that populates dag_hash and compiled_plan did not exist yet
--   (tasks 2.3 / 4.2) and adding either piece before that writer existed
--   would either be vacuous or would reject rows the code of the day still
--   wrote:
--
--     1. chk_valid_requires_hash
--        CHECK (validation_state <> 'VALID'
--               OR (dag_hash IS NOT NULL AND compiled_plan IS NOT NULL))
--        This is the constraint that makes SB-02 - a clone (or any save)
--        that reports VALID while carrying no identity hash or compiled
--        plan - a state PostgreSQL itself refuses to store, rather than a
--        state the application layer merely promises not to create.
--
--     2. reject_immutable_version_update() + trg_sv_immutable
--        A BEFORE UPDATE trigger that rejects changes to graph_json,
--        compiled_plan, dag_hash and schema_version once a version is
--        read-only, so a later edit cannot retroactively change what a
--        running deployment is executing (design.md "Versioning and
--        immutability"; Requirements 9.2, 9.3).
--
--   This file lands both pieces now that tasks 2.3 (task "Persist the
--   canonical graph and plan through the service layer") and 2.1/2.4 exist
--   and every canonical write already goes through
--   ``CompiledVersion.canonical_columns()``
--   (backend_app/backend/strategy_builder.py), which sets dag_hash and
--   compiled_plan together with validation_state on every save, and never
--   independently of one another.
--
-- SCOPE - THIS FILE IS PART 3 ONLY
--   Migration 004 is landed incrementally, matching the phase that needs
--   each piece (tasks.md "Migration split"):
--     part 1  Phase 2 (task 2.2)  strategy_versions columns
--                                 -> 004_strategy_builder_canonical.sql
--     part 2  Phase 3 (task 3.2)  block_registry_snapshots
--                                 -> 004b_block_registry_snapshots.sql
--     part 3  Phase 4 (task 4.1)  chk_valid_requires_hash
--                                 + reject_immutable_version_update()
--                                 + trg_sv_immutable                <- THIS FILE
--     part 4  Phase 6 (task 6.1)  training_jobs, model_versions
--                                 -> 004d_training_and_models.sql
--     part 5  Phase 8 (task 8.1)  strategy_deployments columns
--                                 -> 004e_deployment_bindings.sql
--
-- WHY A SEPARATE FILE RATHER THAN AN EDIT TO PART 1
--   Same reasoning 004b's header already established for part 2, restated
--   for part 3:
--     1. Migrations here are applied BY HAND, per file, and nothing records
--        which files an environment has run. Editing a file an operator may
--        already have applied leaves no signal that the file changed; a new
--        filename is the signal.
--     2. Part 1's own header enumerates the parts as separate landings and
--        states explicitly that chk_valid_requires_hash and the
--        immutability trigger are "DELIBERATELY ABSENT here" and land in
--        Phase 4 - i.e. in this file.
--     3. 004b already set the "004<letter>" naming precedent for this
--        migration number, itself following 003's precedent
--        (003_signal_trace_preflight.sql beside
--        003_signal_trace_restoration.sql).
--   Applying part 1, part 2 and part 3 in any order works AS LONG AS part 1
--   has already run: part 3 depends on the columns part 1 creates (the
--   preflight below checks for them explicitly) and does not depend on
--   part 2's table at all.
--
-- SOURCE OF THE DEFINITION, AND ONE DELIBERATE DEVIATION
--   design.md "Migration 004_strategy_builder_canonical.sql", section 1,
--   gives chk_valid_requires_hash verbatim - reproduced unchanged below.
--
--   The same section's immutability trigger reads, in the design doc:
--
--     CREATE OR REPLACE FUNCTION reject_immutable_version_update()
--     RETURNS TRIGGER AS $$
--     BEGIN
--       IF OLD.is_read_only THEN
--         IF NEW.graph_json     IS DISTINCT FROM OLD.graph_json
--         OR NEW.compiled_plan  IS DISTINCT FROM OLD.compiled_plan
--         OR NEW.dag_hash       IS DISTINCT FROM OLD.dag_hash
--         OR NEW.schema_version IS DISTINCT FROM OLD.schema_version THEN
--           RAISE EXCEPTION '...';
--         END IF;
--       END IF;
--       RETURN NEW;
--     END;
--     $$ LANGUAGE plpgsql;
--
--   ``is_read_only`` is a real column (migrations/001_strategy_architecture.sql
--   line 36; re-declared identically by 003_signal_trace_restoration.sql
--   line 55), but no code path in this repository ever sets it to TRUE.
--   design.md's own "Lifecycle" state-machine section and Requirements 9.4
--   and 9.9 instead name lifecycle_state - specifically the values
--   DEPLOYED, RUNNING and PAUSED - as what makes a version read-only:
--
--     "WHILE a version's lifecycle state is DEPLOYED, RUNNING or PAUSED,
--      THE Strategy_Builder SHALL present that version's canvas as
--      read-only." (Requirement 9.9)
--     "WHEN an author edits a version whose lifecycle state is DEPLOYED,
--      RUNNING or PAUSED, THE Strategy_Builder_API SHALL create a new
--      draft ... and SHALL leave the existing version record unchanged."
--      (Requirement 9.4)
--
--   lifecycle_state is the column migration 004 part 1 actually added and
--   the one the service layer actually writes and reads
--   (backend_app/backend/strategy_builder.py LIFECYCLE_STATES;
--   backend_app/backend/strategy_service.py). Gating the trigger on
--   ``OLD.is_read_only`` alone would therefore be a trigger that can never
--   fire against any row this codebase produces - a silent no-op dressed
--   as an enforced invariant. This file gates on lifecycle_state instead,
--   per this task's explicit instruction, and keeps the ``is_read_only``
--   check ADDITIONALLY (an OR, never a replacement) so that column is not
--   made deader than it already is and any future code that does set it
--   gets the same protection for free. This is strictly MORE restrictive
--   than the design snippet, never less: every update the design's literal
--   trigger would reject is still rejected here, and some it would have
--   silently admitted (any DEPLOYED/RUNNING/PAUSED row, since is_read_only
--   is never TRUE for one today) are now correctly rejected too.
--
-- PRECONDITIONS
--   public.strategy_versions must exist AND must already carry the
--   graph_json, compiled_plan, dag_hash, schema_version, validation_state
--   and lifecycle_state columns from migration 004 part 1
--   (004_strategy_builder_canonical.sql). The preflight block below checks
--   both and fails loudly, naming the missing piece, rather than emitting a
--   bare 42P01/42703 partway through.
--
-- SAFETY
--   * Additive only in the schema sense - no DROP, no TRUNCATE, no DELETE.
--     One UPDATE-shaped statement exists (see "RECONCILIATION" below) and it
--     is read-only unless a genuinely SB-02-shaped row is found, in which
--     case this file refuses to proceed rather than silently repairing
--     production data on an operator's behalf.
--   * Fully idempotent. The CHECK constraint is added only when
--     pg_constraint holds none of that name (PostgreSQL has no
--     ADD CONSTRAINT IF NOT EXISTS, and DROP-then-ADD would both violate
--     the no-DROP rule and leave a window with no constraint enforced).
--     The function uses CREATE OR REPLACE, which is naturally idempotent.
--     The trigger is created only when pg_trigger holds none of that name
--     on this table, matching the guard pattern
--     003_signal_trace_restoration.sql already established, because
--     PostgreSQL has no CREATE TRIGGER IF NOT EXISTS or (before PG14)
--     CREATE OR REPLACE TRIGGER.
--   * Existing tables, columns, indexes and RLS policies are untouched.
--     This file adds one constraint, one function and one trigger and
--     creates no policy, alters no policy, and does not run
--     ALTER TABLE ... ENABLE/DISABLE ROW LEVEL SECURITY. Tenant isolation
--     on strategy_versions is exactly as strong after this migration as
--     before it.
--   * The trigger only ever REJECTS a write (RAISE EXCEPTION) or passes
--     ``NEW`` through unchanged. It never mutates a row itself, so it
--     cannot be the source of a silent data change.
--
-- RECONCILIATION
--   Part 1's header warned: "Existing rows must be reconciled first." A
--   plain ``ALTER TABLE ... ADD CONSTRAINT`` validates every existing row
--   and fails with a bare 23514 check_violation naming no row if any
--   pre-existing version claims VALID with a null hash or plan - the exact
--   silently-degraded state chk_valid_requires_hash exists to forbid. The
--   preflight below runs that same check first, as a plain SELECT COUNT,
--   and RAISEs a readable exception identifying the offending version ids
--   before the ALTER TABLE is attempted, so the failure (if any) is
--   diagnosable rather than a bare constraint-violation code. It writes
--   nothing and changes no row.
--
-- APPLICATION
--   NOT applied automatically. .github/workflows/03-deploy.yml has no
--   migration step (its jobs are pre-deployment validation, ECR verify,
--   ECS deploy, reports). Migrations in this repo are applied by hand, per
--   file. Apply this file explicitly against the target database, AFTER
--   004_strategy_builder_canonical.sql (part 1), and then run the
--   verification queries at the bottom.

BEGIN;

-- 0) Preflight -----------------------------------------------------------
-- Read-only assertions. Fail with a readable message instead of a bare
-- undefined_table/undefined_column error if part 1 has not been applied.
-- Idempotent: reads catalogues and writes nothing.
DO $$
BEGIN
    IF to_regclass('public.strategy_versions') IS NULL THEN
        RAISE EXCEPTION
            '004 part 3 precondition failed: table public.strategy_versions '
            'does not exist. Apply backend_app/migrations/'
            '001_strategy_architecture.sql (or 003_signal_trace_restoration.sql) '
            'first.';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM (VALUES ('graph_json'), ('compiled_plan'), ('dag_hash'),
                       ('schema_version'), ('validation_state'),
                       ('lifecycle_state')) AS expected(column_name)
         WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns c
             WHERE c.table_schema = 'public'
               AND c.table_name   = 'strategy_versions'
               AND c.column_name  = expected.column_name
         )
    ) THEN
        RAISE EXCEPTION
            '004 part 3 precondition failed: public.strategy_versions is '
            'missing one or more of graph_json, compiled_plan, dag_hash, '
            'schema_version, validation_state, lifecycle_state. Apply '
            'backend_app/migrations/004_strategy_builder_canonical.sql '
            '(part 1) first.';
    END IF;
END $$;

-- 0b) Reconciliation check -------------------------------------------------
-- Fail loudly and specifically, before ALTER TABLE turns the same problem
-- into a bare 23514. Reads only; changes nothing.
DO $$
DECLARE
    offending_ids TEXT;
BEGIN
    SELECT string_agg(id::text, ', ' ORDER BY id)
      INTO offending_ids
      FROM public.strategy_versions
     WHERE validation_state = 'VALID'
       AND (dag_hash IS NULL OR compiled_plan IS NULL);

    IF offending_ids IS NOT NULL THEN
        RAISE EXCEPTION
            '004 part 3 precondition failed: % existing strategy_versions '
            'row(s) report validation_state = ''VALID'' with a null '
            'dag_hash or compiled_plan - exactly the SB-02 state '
            'chk_valid_requires_hash exists to forbid. Offending id(s): %. '
            'Reconcile these rows (recompile and re-save, or set '
            'validation_state back to ''UNVALIDATED''/''INVALID'') before '
            're-running this migration.',
            (SELECT count(*) FROM public.strategy_versions
              WHERE validation_state = 'VALID'
                AND (dag_hash IS NULL OR compiled_plan IS NULL)),
            offending_ids;
    END IF;
END $$;

-- 1) chk_valid_requires_hash ----------------------------------------------
-- design.md "Migration 004_strategy_builder_canonical.sql", section 1,
-- verbatim. Makes SB-02 unrepresentable: a row cannot claim VALID while
-- carrying no identity hash or no compiled plan.
--
-- Idempotent: added only when pg_constraint holds no constraint of that
-- name on this table, so a re-run adds nothing and cannot raise 42710
-- duplicate_object.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.strategy_versions'::regclass
          AND conname  = 'chk_valid_requires_hash'
    ) THEN
        ALTER TABLE public.strategy_versions
            ADD CONSTRAINT chk_valid_requires_hash
                CHECK (validation_state <> 'VALID'
                       OR (dag_hash IS NOT NULL AND compiled_plan IS NOT NULL));
    END IF;
END $$;

-- 2) Immutability of a read-only version ----------------------------------
-- design.md "Versioning and immutability" + Requirements 9.2, 9.3, 9.4, 9.9.
-- See the header's "SOURCE OF THE DEFINITION, AND ONE DELIBERATE DEVIATION"
-- note for why this gates on lifecycle_state IN ('DEPLOYED','RUNNING',
-- 'PAUSED') in addition to the design snippet's OLD.is_read_only, rather
-- than on is_read_only alone.
--
-- CREATE OR REPLACE FUNCTION is naturally idempotent.
CREATE OR REPLACE FUNCTION public.reject_immutable_version_update()
RETURNS TRIGGER AS $$
BEGIN
  IF OLD.is_read_only
     OR OLD.lifecycle_state IN ('DEPLOYED', 'RUNNING', 'PAUSED') THEN
    IF NEW.graph_json     IS DISTINCT FROM OLD.graph_json
    OR NEW.compiled_plan  IS DISTINCT FROM OLD.compiled_plan
    OR NEW.dag_hash       IS DISTINCT FROM OLD.dag_hash
    OR NEW.schema_version IS DISTINCT FROM OLD.schema_version THEN
      RAISE EXCEPTION
        'strategy_versions %: immutable version (lifecycle_state=%, '
        'is_read_only=%) cannot have graph_json, compiled_plan, dag_hash '
        'or schema_version modified', OLD.id, OLD.lifecycle_state, OLD.is_read_only;
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Idempotent: created only when pg_trigger holds no trigger of that name on
-- this table, matching the guard pattern already used by
-- 003_signal_trace_restoration.sql section 7 (PostgreSQL has no
-- CREATE TRIGGER IF NOT EXISTS, and this repository's PostgreSQL baseline
-- predates CREATE OR REPLACE TRIGGER / PG14).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_sv_immutable'
          AND n.nspname = 'public'
          AND c.relname = 'strategy_versions'
    ) THEN
        CREATE TRIGGER trg_sv_immutable
            BEFORE UPDATE ON public.strategy_versions
            FOR EACH ROW EXECUTE FUNCTION public.reject_immutable_version_update();
    END IF;
END $$;

COMMIT;

-- VERIFICATION (run after applying) ------------------------------------
--
-- 1. Constraint present. Expect exactly 1 row.
--
--   SELECT conname, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--   WHERE conrelid = 'public.strategy_versions'::regclass
--     AND conname = 'chk_valid_requires_hash';
--
-- 2. All four part-1 constraints now present. Expect exactly 4 rows:
--    chk_graph_shape, chk_lifecycle_state, chk_valid_requires_hash,
--    chk_validation_state.
--
--   SELECT conname
--   FROM pg_constraint
--   WHERE conrelid = 'public.strategy_versions'::regclass
--     AND conname IN ('chk_validation_state','chk_lifecycle_state',
--                     'chk_graph_shape','chk_valid_requires_hash')
--   ORDER BY conname;
--
-- 3. The bad state is rejected directly by the database (Property 21):
--
--   BEGIN;
--     -- expect this INSERT to fail with a check_violation naming
--     -- chk_valid_requires_hash
--     INSERT INTO public.strategy_versions
--       (strategy_id, version, blueprint, validation_state, dag_hash, compiled_plan)
--     VALUES
--       ('00000000-0000-0000-0000-000000000000', 'v0.0-verify', '{}'::jsonb,
--        'VALID', NULL, NULL);
--   ROLLBACK;
--
-- 4. Trigger present. Expect exactly 1 row.
--
--   SELECT tgname, tgenabled
--   FROM pg_trigger
--   WHERE tgrelid = 'public.strategy_versions'::regclass
--     AND tgname = 'trg_sv_immutable';
--
-- 5. A read-only version cannot be updated (Property 21). Run against a
--    row whose lifecycle_state is DEPLOYED, RUNNING or PAUSED:
--
--   BEGIN;
--     -- expect this UPDATE to fail with 'immutable version ... cannot
--     -- have ... modified'
--     UPDATE public.strategy_versions
--        SET dag_hash = 'deadbeefdeadbeef'
--      WHERE lifecycle_state IN ('DEPLOYED','RUNNING','PAUSED')
--      LIMIT 1;
--   ROLLBACK;
--
--    A column NOT named in the trigger (for example validation_report or
--    updated_at) must still be updatable on the same row - the trigger
--    rejects only graph_json, compiled_plan, dag_hash and schema_version.
--
-- 6. RLS unchanged. Expect the same policies that existed before this
--    migration (SELECT / INSERT / UPDATE, each scoped through
--    strategies.user_id = auth.uid()), and rowsecurity = true.
--
--   SELECT policyname, cmd, qual, with_check
--   FROM pg_policies
--   WHERE schemaname = 'public' AND tablename = 'strategy_versions'
--   ORDER BY policyname;
--
--   SELECT relrowsecurity, relforcerowsecurity
--   FROM pg_class WHERE oid = 'public.strategy_versions'::regclass;
--
-- 7. Idempotency. Re-running this whole file must report no error (past
--    the preflight, assuming no new offending rows were written in the
--    interim) and leave the counts in 1, 2 and 4 unchanged.
