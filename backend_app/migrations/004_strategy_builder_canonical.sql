-- 004_strategy_builder_canonical.sql  (PART 1 of 5)
--
-- PURPOSE
--   Give the canonical Strategy Builder DAG a home on the immutable version
--   row. Today the canonical graph has nowhere to live except a JSONB blob
--   under strategies.buy_logic._nodes, and there is no validation-state
--   column at all (design.md "Database" > "Gaps to close").
--
--   This part adds the ten strategy_versions columns, three CHECK
--   constraints and three indexes specified in
--   design.md "Migration 004_strategy_builder_canonical.sql", section 1.
--   Requirement 9.1 (persist graph, plan, hash, schema version, compiler
--   version, registry version, warmup bars, validation state and report as
--   one version record) is what these columns exist to satisfy.
--
-- SCOPE - THIS FILE IS PART 1 ONLY
--   Migration 004 is landed incrementally, matching the phase that needs
--   each piece (tasks.md "Migration split"):
--     part 1  Phase 2 (task 2.2)  strategy_versions columns   <- THIS FILE
--     part 2  Phase 3 (task 3.2)  block_registry_snapshots
--                                 -> 004b_block_registry_snapshots.sql
--     part 3  Phase 4 (task 4.1)  chk_valid_requires_hash
--                                 + reject_immutable_version_update()
--                                 + trg_sv_immutable
--                                 -> 004c_immutable_versions.sql
--     part 4  Phase 6 (task 6.1)  training_jobs, model_versions
--                                 -> 004d_training_and_models.sql
--     part 5  Phase 8 (task 8.1)  strategy_deployments columns
--                                 -> 004e_deployment_bindings.sql
--
--   chk_valid_requires_hash and the immutability trigger are DELIBERATELY
--   ABSENT here. The check is
--     CHECK (validation_state <> 'VALID'
--            OR (dag_hash IS NOT NULL AND compiled_plan IS NOT NULL))
--   and it is the constraint that makes defect SB-02 (a clone that looks
--   saved but carries no hash or plan) unrepresentable. It lands in Phase 4
--   (task 4.1, backend_app/migrations/004c_immutable_versions.sql) because
--   the writer that populates dag_hash and compiled_plan does not exist
--   until tasks 2.3 and 4.2, so adding it now would either be vacuous or
--   would reject rows the current code still writes. Existing rows must be
--   reconciled first.
--
-- PRECONDITIONS
--   public.strategy_versions must exist. It is created by
--   backend_app/migrations/001_strategy_architecture.sql (and equivalently
--   by 003_signal_trace_restoration.sql, which is what production ran).
--   The preflight block below fails loudly with a readable message rather
--   than emitting a bare 42P01 if the table is missing.
--
-- SAFETY
--   * Additive only. No DROP, no TRUNCATE, no DELETE, no UPDATE, no
--     ALTER COLUMN TYPE. There is no data-loss path in this file.
--   * Fully idempotent. Every statement is guarded, so a second run is a
--     no-op. Per-statement rationale is in the comment above each one.
--   * NOT NULL is only ever paired with a DEFAULT, so PostgreSQL 11+
--     backfills existing rows in place and the CHECK constraints below
--     cannot be violated by pre-existing data.
--   * Existing tables, columns, indexes, triggers and RLS policies are
--     untouched. This file creates no policy, alters no policy, and does
--     not run ALTER TABLE ... ENABLE/DISABLE ROW LEVEL SECURITY. Tenant
--     isolation on strategy_versions is exactly as strong after this
--     migration as before: the three policies from
--     003_signal_trace_restoration.sql (SELECT / INSERT / UPDATE, each
--     scoped through strategies.user_id = auth.uid()) remain the only
--     policies on the table and keep applying to the new columns, because
--     RLS is row-scoped, not column-scoped.
--   * Constraints are added under a pg_constraint existence guard rather
--     than 007's DROP-then-ADD pattern. PostgreSQL has no
--     ADD CONSTRAINT IF NOT EXISTS, and DROP-then-ADD would both violate
--     the no-DROP rule for this file and leave a window inside the
--     transaction where the invariant is not enforced.
--
-- APPLICATION
--   NOT applied automatically. .github/workflows/03-deploy.yml has no
--   migration step (its jobs are pre-deployment validation, ECR verify,
--   ECS deploy, reports). Migrations in this repo are applied by hand, per
--   file, the way scripts/forensics/apply_migration_007.py applied 007.
--   Apply this file explicitly against the target database and then run the
--   verification queries at the bottom.

BEGIN;

-- 0) Preflight ---------------------------------------------------------
-- Read-only assertion. Fails with a readable message instead of a bare
-- undefined_table error if 001/003 has not been applied.
-- Idempotent: it reads catalogues and writes nothing.
DO $$
BEGIN
    IF to_regclass('public.strategy_versions') IS NULL THEN
        RAISE EXCEPTION
            '004 part 1 precondition failed: table public.strategy_versions '
            'does not exist. Apply backend_app/migrations/'
            '001_strategy_architecture.sql (or 003_signal_trace_restoration.sql) '
            'first.';
    END IF;
END $$;

-- 1) Canonical graph + plan on the immutable version -------------------
-- design.md "Migration 004_strategy_builder_canonical.sql", section 1,
-- "-- 1. Canonical graph + plan on the immutable version".
--
--   graph_json        canonical v2 StrategyGraph.to_dict()
--   compiled_plan     CompiledPlan.to_dict()
--   dag_hash          16 hex chars from compute_dag_hash; TEXT, matching
--                     both the design and the existing strategies.dag_hash
--                     column (migrations/add_billing_currency_and_dag_hash.sql)
--   schema_version    canonical schema version; 2 today
--   compiler_version  e.g. '2.0.0'
--   validation_state  UNVALIDATED | VALID | INVALID (schema.ValidationState)
--   validation_report ValidationReport.to_dict()
--   lifecycle_state   version lifecycle; vocabulary fixed by
--                     chk_lifecycle_state below
--   warmup_bars       composed warmup bar count
--   registry_version  e.g. 'r_3d173598'; which descriptor set the author
--                     was offered
--
-- Idempotent: every clause is ADD COLUMN IF NOT EXISTS, so a re-run skips
-- each column that is already present. The two NOT NULL columns carry a
-- DEFAULT, so the first run backfills existing rows and no row is left in
-- a state the constraints below reject.
ALTER TABLE public.strategy_versions
    ADD COLUMN IF NOT EXISTS graph_json        JSONB,
    ADD COLUMN IF NOT EXISTS compiled_plan     JSONB,
    ADD COLUMN IF NOT EXISTS dag_hash          TEXT,
    ADD COLUMN IF NOT EXISTS schema_version    INTEGER NOT NULL DEFAULT 2,
    ADD COLUMN IF NOT EXISTS compiler_version  TEXT,
    ADD COLUMN IF NOT EXISTS validation_state  TEXT NOT NULL DEFAULT 'UNVALIDATED',
    ADD COLUMN IF NOT EXISTS validation_report JSONB,
    ADD COLUMN IF NOT EXISTS lifecycle_state   TEXT NOT NULL DEFAULT 'DRAFT',
    ADD COLUMN IF NOT EXISTS warmup_bars       INTEGER,
    ADD COLUMN IF NOT EXISTS registry_version  TEXT;

-- 2) Constraints (idempotent) ------------------------------------------
-- design.md, same section: chk_validation_state, chk_lifecycle_state and
-- chk_graph_shape, verbatim. chk_valid_requires_hash is deferred to
-- Phase 4 (task 4.1) and is intentionally not added here.
--
-- Idempotent: each ADD CONSTRAINT runs only when pg_constraint holds no
-- constraint of that name on this table, so a re-run adds nothing and
-- cannot raise 42710 duplicate_object.
--
-- NULL semantics: a CHECK that evaluates to NULL passes, so these
-- constraints do not reject a NULL state column. That matters only in the
-- partially-applied case where a column pre-existed without our DEFAULT;
-- on a clean run the DEFAULTs guarantee the columns are populated.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.strategy_versions'::regclass
          AND conname  = 'chk_validation_state'
    ) THEN
        ALTER TABLE public.strategy_versions
            ADD CONSTRAINT chk_validation_state
                CHECK (validation_state IN ('UNVALIDATED','VALID','INVALID'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.strategy_versions'::regclass
          AND conname  = 'chk_lifecycle_state'
    ) THEN
        ALTER TABLE public.strategy_versions
            ADD CONSTRAINT chk_lifecycle_state
                CHECK (lifecycle_state IN ('DRAFT','VALIDATED','SAVED','TRAINING','TRAINED',
                                           'READY','DEPLOYED','RUNNING','PAUSED','STOPPED','ARCHIVED'));
    END IF;

    -- never store a graph we cannot parse
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.strategy_versions'::regclass
          AND conname  = 'chk_graph_shape'
    ) THEN
        ALTER TABLE public.strategy_versions
            ADD CONSTRAINT chk_graph_shape
                CHECK (graph_json IS NULL
                       OR (jsonb_typeof(graph_json->'nodes') = 'array'
                           AND jsonb_typeof(graph_json->'edges') = 'array'));
    END IF;
END $$;

-- 3) Indexes -----------------------------------------------------------
-- design.md, same section: the three indexes as written there.
--   idx_sv_dag_hash    partial, for hash lookup and the recompile-on-
--                      mismatch path in the plan loader
--   idx_sv_lifecycle   lifecycle filtering (deploy gate, read-only canvas)
--   idx_sv_validation  validation-state filtering
--
-- Idempotent: CREATE INDEX IF NOT EXISTS. Note these run inside the
-- transaction (not CONCURRENTLY), which is deliberate: the whole file is
-- one atomic unit, and strategy_versions is small enough that the brief
-- write lock is acceptable.
CREATE INDEX IF NOT EXISTS idx_sv_dag_hash
    ON public.strategy_versions(dag_hash) WHERE dag_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sv_lifecycle
    ON public.strategy_versions(lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_sv_validation
    ON public.strategy_versions(validation_state);

COMMIT;

-- VERIFICATION (run after applying) ------------------------------------
--
-- 1. Columns. Expect 10 rows. schema_version, validation_state and
--    lifecycle_state must show is_nullable = NO with their defaults.
--
--   SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'public'
--     AND table_name   = 'strategy_versions'
--     AND column_name IN ('graph_json','compiled_plan','dag_hash',
--                         'schema_version','compiler_version',
--                         'validation_state','validation_report',
--                         'lifecycle_state','warmup_bars',
--                         'registry_version')
--   ORDER BY column_name;
--
-- 2. Constraints. Expect exactly 3 rows: chk_graph_shape,
--    chk_lifecycle_state, chk_validation_state. chk_valid_requires_hash
--    must NOT appear yet -- it is Phase 4, task 4.1
--    (004c_immutable_versions.sql).
--
--   SELECT conname, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--   WHERE conrelid = 'public.strategy_versions'::regclass
--     AND conname IN ('chk_validation_state','chk_lifecycle_state',
--                     'chk_graph_shape','chk_valid_requires_hash')
--   ORDER BY conname;
--
-- 3. Indexes. Expect 3 rows; idx_sv_dag_hash must carry the partial
--    WHERE (dag_hash IS NOT NULL) predicate.
--
--   SELECT indexname, indexdef
--   FROM pg_indexes
--   WHERE schemaname = 'public'
--     AND tablename  = 'strategy_versions'
--     AND indexname IN ('idx_sv_dag_hash','idx_sv_lifecycle','idx_sv_validation')
--   ORDER BY indexname;
--
-- 4. RLS unchanged. Expect the same 3 policies that existed before this
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
-- 5. Trigger absence. Expect 0 rows: trg_sv_immutable is Phase 4
--    (004c_immutable_versions.sql).
--
--   SELECT tgname FROM pg_trigger
--   WHERE tgrelid = 'public.strategy_versions'::regclass
--     AND tgname = 'trg_sv_immutable';
