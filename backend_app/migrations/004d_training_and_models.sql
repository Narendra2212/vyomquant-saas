-- 004d_training_and_models.sql  (migration 004, PART 4 of 5)
--
-- PURPOSE
--   Give training runs and trained models a home, and make two invariants
--   unrepresentable rather than merely checked:
--
--     * AT MOST ONE LIVE TRAINING JOB PER (version, node). Requirement
--       15.13. Enforced by the partial unique index uq_tj_active_per_node,
--       so a double-clicked "train" button, a retried request or two racing
--       workers cannot produce two QUEUED/RUNNING jobs for the same model
--       node - the second INSERT fails on a unique violation instead of
--       quietly consuming a second slot of shared training capacity.
--
--     * AT MOST ONE ACTIVE MODEL VERSION PER (version, node). Requirement
--       17.4. Enforced by the partial unique index uq_mv_active_per_node.
--       This is the ML-3 retrain-overwrite class: retraining inserts a NEW
--       model_versions row and can never silently become the second active
--       model for a node a running deployment is already executing.
--
--   Requirement 21.3 ("THE Persistence_Layer SHALL enforce row-level
--   ownership policies on strategy, version, training job and Model_Version
--   records") is the third reason this file exists. Both tables hold
--   per-user work - a training configuration, a dataset fingerprint, an
--   artifact reference - so tenant isolation is enforced in the database,
--   not only in the handlers. Task 8.7's (endpoint x resource) isolation
--   matrix targets these two tables by name.
--
-- SCOPE - THIS FILE IS PART 4 ONLY
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
--     part 4  Phase 6 (task 6.1)  training_jobs, model_versions   <- THIS FILE
--     part 5  Phase 8 (task 8.1)  strategy_deployments columns
--                                 -> 004e_deployment_bindings.sql
--
-- WHY A SEPARATE FILE RATHER THAN AN EDIT TO PART 1
--   Same three reasons 004b established for part 2 and 004c restated for
--   part 3:
--     1. Migrations here are applied BY HAND, per file, and nothing records
--        which files an environment has run. Appending to a file an
--        operator may already have applied leaves no signal that the file
--        changed; a new filename is the signal.
--     2. Part 1's header states "THIS FILE IS PART 1 ONLY" and enumerates
--        the parts as separate landings, naming this one as part 4.
--     3. 004b and 004c already set the "004<letter>" naming precedent for
--        this migration number, itself following 003's precedent
--        (003_signal_trace_preflight.sql beside
--        003_signal_trace_restoration.sql).
--   Order: this file needs part 1 only insofar as it needs
--   public.strategy_versions to exist (which 001/003 create); it does not
--   read the columns part 1 adds, and it does not depend on part 2 or
--   part 3 at all. The preflight below checks what it actually needs.
--
-- SOURCE OF THE DEFINITION
--   design.md -> "Migration 004_strategy_builder_canonical.sql", sections
--   "-- 2. Training jobs", "-- 3. Model versions" and the training/model
--   half of "-- 6. RLS". Every column, every CHECK, every index and every
--   policy below is that text, schema-qualified with public. and reordered
--   only so that RLS is enabled in the same statement group as the table it
--   protects. Nothing in the design's DDL for these two tables is omitted.
--
-- FIVE DELIBERATE ADDITIONS BEYOND THE DESIGN'S DDL, AND WHY
--   The design snippet is a schema sketch; these five make it safe to apply
--   by hand, twice, to a database whose history nobody recorded. None
--   weakens a control and none changes a column, constraint or policy the
--   design specifies.
--
--   1. Preflight assertions (section 0). A readable RAISE naming the
--      missing object, instead of a bare 42P01/42704 from the middle of a
--      CREATE TABLE.
--
--   2. Shape assertions (sections 1a, 2a). CREATE TABLE IF NOT EXISTS is
--      SILENT when a table of that name already exists in a different
--      shape - for instance one created by hand during an investigation.
--      That silence would move the failure to the first training insert, at
--      runtime, in production. The column lists are asserted instead, so a
--      mismatch is a migration-time error naming the missing columns. This
--      is 004b section 2's pattern.
--
--   3. Guarded ADD CONSTRAINT blocks (sections 1b, 2b) repeating the four
--      constraints that the design declares INLINE in CREATE TABLE:
--      chk_tj_status, chk_tj_progress, chk_tj_failed_has_reason and
--      uq_mv_version_node. On a fresh run every one of them already exists
--      from the inline declaration, so each block is a no-op. They exist
--      for the pre-existing-table case: without them, a table created
--      earlier without those constraints would keep the invariants
--      unenforced forever and this migration would report success. The
--      guard is pg_constraint existence, which is also what makes a re-run
--      add nothing (PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS, and
--      DROP-then-ADD would both break this file's no-DROP rule and leave a
--      window inside the transaction with the invariant unenforced).
--
--   4. Column comments (section 4) recording intent the schema cannot
--      express - above all the metrics_history data-minimisation rule. See
--      "DATA MINIMISATION" below.
--
--   5. Grants (section 6), narrowing the default Supabase grants on both
--      new tables so RLS is not the only thing between an anon API key and
--      a user's training work. 004b established this. Note what is NOT
--      granted: no DELETE to anybody, and no UPDATE on model_versions to
--      authenticated.
--
-- DATA MINIMISATION - training_jobs.metrics_history (Requirement 17.4 area,
-- design.md "Storage architecture")
--   metrics_history holds PER-EPOCH SCALARS ONLY - epoch index, loss,
--   val_loss and the like, bounded by the effective max_epochs cap - and
--   NEVER predictions and NEVER feature values. This is a
--   data-minimisation constraint, not a size note. The table is read for
--   charting a job's progress; a prediction vector or a feature matrix
--   stored here would put the model OUTPUT for a user's private strategy
--   into a document that is fetched, serialised into API responses and
--   pushed over realtime frames. Model artifacts live in object storage
--   behind artifact_uri + artifact_checksum; feature matrices and OHLCV
--   history are never persisted here at all.
--
--   STATED PLAINLY: SQL CANNOT ENFORCE THIS. A CHECK constraint can see
--   that metrics_history is JSONB; it cannot tell a loss curve from a
--   prediction vector. So this file does the only two things it can - it
--   records the rule in a COMMENT ON COLUMN that travels with the schema
--   (section 4), where \d+ and every schema browser will show it to the
--   next person - and the enforcement itself belongs to the writer in
--   tasks 6.4 and 6.6. Do not read the comment as a guarantee.
--
-- ROW LEVEL SECURITY (Requirement 21.3)
--   RLS is enabled on both new tables and the design's five policies are
--   created, all keyed on the table's own user_id column:
--
--     training_jobs   tj_owner_select  SELECT  USING (user_id = auth.uid())
--                     tj_owner_insert  INSERT  WITH CHECK (user_id = auth.uid())
--                     tj_owner_update  UPDATE  USING (user_id = auth.uid())
--     model_versions  mv_owner_select  SELECT  USING (user_id = auth.uid())
--                     mv_owner_insert  INSERT  WITH CHECK (user_id = auth.uid())
--
--   Three properties of that list are worth stating rather than leaving to
--   be inferred:
--
--   * NO DELETE POLICY on either table. With RLS enabled and no DELETE
--     policy, DELETE is denied to every non-superuser, non-owner role,
--     including service_role's grants at the privilege layer below. A
--     training job's history and a model version's provenance therefore
--     cannot be erased through the API. Cascades still work: a delete of
--     the parent strategy, strategy version or auth user propagates
--     through the ON DELETE CASCADE foreign keys, which run as the system
--     and are not subject to RLS. This matches 003's stated "No DELETE
--     policies" rule.
--
--   * NO UPDATE POLICY ON model_versions, exactly as the design specifies.
--     The consequence is load-bearing and task 6.5 needs to know it:
--     flipping is_active on a superseded model version is an UPDATE, so it
--     CANNOT be done with the caller's RLS-scoped client. Task 6.5 must
--     deactivate the previous model version through the backend's own
--     service role (or a SECURITY DEFINER function), in the same
--     transaction as the new INSERT, or the insert will fail on
--     uq_mv_active_per_node. That is the stricter arrangement and it is
--     the design's: a user's own client cannot rewrite which artifact a
--     running deployment resolves.
--
--   * tj_owner_update carries USING and no WITH CHECK, as the design
--     writes it. PostgreSQL then applies the USING expression to the NEW
--     row as well, so a user cannot reassign a job to another user_id.
--     Adding a WITH CHECK clause would be redundant, not stronger.
--
--   The policies deliberately carry NO "TO <role>" clause, matching the
--   design. They therefore apply to every role, and anon - for whom
--   auth.uid() is NULL - matches nothing, because user_id = NULL evaluates
--   to NULL and a NULL policy result denies. The grants in section 6 close
--   the same door a second time at the privilege layer.
--
--   No existing table's RLS is touched. This file creates five policies, on
--   the two tables it creates, and runs no ENABLE/DISABLE ROW LEVEL
--   SECURITY against any pre-existing relation. Tenant isolation on
--   strategies, strategy_versions, strategy_deployments and signals is
--   exactly as strong after this migration as before it.
--
-- ONE THING DELIBERATELY NOT ADDED
--   No BEFORE UPDATE trigger maintaining training_jobs.updated_at. 003
--   attaches update_updated_at_column() to every table it creates that
--   carries updated_at, so the omission is a departure from local habit
--   and is called out here rather than left as an oversight: the design
--   specifies no such trigger for this table, the column already carries
--   NOT NULL DEFAULT NOW() so no row is ever without a value, and the only
--   freshness signal anything actually depends on is last_heartbeat (which
--   the worker-loss detector in task 6.4 reads and the worker writes
--   explicitly). Tasks 6.3 and 6.4 must therefore set updated_at on each
--   status transition they write. Adding the trigger later is a one-block
--   change if that turns out to be the wrong call.
--
-- PRECONDITIONS
--   public.strategies, public.strategy_versions and auth.users must exist
--   (001_strategy_architecture.sql / 003_signal_trace_restoration.sql, plus
--   the Supabase auth schema), and the anon, authenticated and service_role
--   roles must exist because section 6 names them. The preflight checks all
--   of it and fails with a message naming the missing piece.
--
-- SAFETY
--   * Additive only. No DROP, no TRUNCATE, no DELETE, no UPDATE, no
--     ALTER COLUMN TYPE. There is no data-loss path in this file, and no
--     statement in it can modify a row of any pre-existing table.
--   * Fully idempotent, statement by statement. CREATE TABLE IF NOT
--     EXISTS; CREATE UNIQUE INDEX / CREATE INDEX IF NOT EXISTS; every
--     constraint behind a pg_constraint existence guard; every policy
--     behind a pg_policies existence guard (PostgreSQL has no
--     CREATE POLICY IF NOT EXISTS, and this repository's PostgreSQL
--     baseline predates CREATE OR REPLACE TRIGGER, so the guard pattern is
--     what 003 and 004c already use); ENABLE ROW LEVEL SECURITY is a no-op
--     when RLS is already on; COMMENT, GRANT and REVOKE converge on the
--     same state however many times they run. A re-run adds nothing and
--     raises nothing.
--   * REVOKE here only ever narrows privileges on the two tables this file
--     creates. It cannot affect another relation and cannot remove a row.
--   * NOT NULL is safe on tables that start empty, and every NOT NULL
--     column that the application does not always supply carries a DEFAULT.
--   * One transaction. Either both tables, their constraints, their
--     indexes, their RLS, their policies and their grants all exist, or
--     none of them do. A half-secured table is never visible to a session.
--
-- APPLICATION
--   NOT applied automatically. .github/workflows/03-deploy.yml has no
--   migration step (its jobs are pre-deployment validation, ECR verify,
--   ECS deploy, reports), so migrations in this repo are applied by hand,
--   per file, the way scripts/forensics/apply_migration_007.py applied 007.
--   Apply this file explicitly against the target database and then run the
--   verification queries at the bottom.
--
--   Until it is applied, the training surface of Phase 6 has no storage:
--   tasks 6.3 to 6.6 must degrade the way registry_snapshot_service already
--   does - a warning naming this file, not a 500 - rather than assume the
--   tables are present.

BEGIN;

-- 0) Preflight ---------------------------------------------------------
-- Read-only assertions. Every object this file references by name is
-- checked here, so a missing prerequisite is a readable message rather
-- than a bare undefined_table/undefined_object from inside a DDL
-- statement.
-- Idempotent: it reads catalogues and writes nothing.
DO $$
DECLARE
    missing_roles TEXT;
BEGIN
    IF to_regclass('public.strategies') IS NULL THEN
        RAISE EXCEPTION
            '004 part 4 precondition failed: table public.strategies does '
            'not exist. Apply the base schema first.';
    END IF;

    IF to_regclass('public.strategy_versions') IS NULL THEN
        RAISE EXCEPTION
            '004 part 4 precondition failed: table public.strategy_versions '
            'does not exist. Apply backend_app/migrations/'
            '001_strategy_architecture.sql (or 003_signal_trace_restoration.sql) '
            'first.';
    END IF;

    -- Both tables carry a user_id FK to auth.users, matching
    -- 003_signal_trace_restoration.sql's strategy_deployments and signals.
    IF to_regclass('auth.users') IS NULL THEN
        RAISE EXCEPTION
            '004 part 4 precondition failed: table auth.users does not '
            'exist. This migration targets a Supabase database, where the '
            'auth schema is created by the platform.';
    END IF;

    -- Named by the GRANT/REVOKE statements in section 6.
    SELECT string_agg(expected.rolname, ', ' ORDER BY expected.rolname)
      INTO missing_roles
      FROM (VALUES ('anon'), ('authenticated'), ('service_role'))
             AS expected(rolname)
     WHERE NOT EXISTS (
        SELECT 1 FROM pg_roles r WHERE r.rolname = expected.rolname
     );

    IF missing_roles IS NOT NULL THEN
        RAISE EXCEPTION
            '004 part 4 precondition failed: role(s) % do not exist. This '
            'migration targets a Supabase database, where these roles are '
            'created by the platform.', missing_roles;
    END IF;
END $$;

-- 1) Training jobs -----------------------------------------------------
-- design.md "Migration 004_strategy_builder_canonical.sql", section 2,
-- column for column, schema-qualified.
--
--   node_id / block_id   the canonical ML node id (n_ + ULID, minted by
--                        strategy_dag.schema and never reused) and the
--                        block that node instantiates (xgboost, lstm, ...).
--                        TEXT, not a FK: the node lives inside the
--                        version's graph_json document, so there is no
--                        relation to reference.
--   status               vocabulary fixed by chk_tj_status below;
--                        Requirement 15.2's five values exactly.
--   cancel_requested     the cancel flag task 6.3's cancel endpoint sets
--                        and task 6.4's worker reads at each epoch
--                        boundary. A flag rather than a status, so a
--                        cancelled-but-still-running job is representable
--                        and honest.
--   config               symbol, timeframe, range, splits, epochs, batch,
--                        seed, horizon, embargo. NEVER an exchange
--                        identifier, api key, secret or passphrase
--                        (Requirement 21.7, design Property 23; enforced
--                        by the writer and by the scan in task 8.8, not by
--                        this schema).
--   dataset_fingerprint  reproducibility (Requirement 15.14).
--   dataset_rows /       what the ML gate actually measured, so the
--   usable_rows /        "required vs available" message in task 6.2 is
--   feature_columns /    reported from stored fact rather than recomputed
--   feature_names /      from a guess.
--   split_sizes
--   epochs_total /       progress is DERIVED FROM COMPLETED EPOCHS
--   epoch_current /      (Requirement 15.4) and bounded to [0,1] by
--   progress             chk_tj_progress. Nothing here is fabricated.
--   loss / val_loss      last observed scalars, for cheap status reads.
--   metrics_history      PER-EPOCH SCALARS ONLY - never predictions, never
--                        feature values. See "DATA MINIMISATION" in the
--                        header and the COMMENT in section 4.
--   failure_reason       a CLASSIFIED reason (MAX_DURATION_EXCEEDED,
--                        WORKER_LOST, ...) never a generic string
--                        (Requirement 15.10); chk_tj_failed_has_reason
--                        makes a FAILED job with no reason unrepresentable.
--   worker_id /          worker identity and liveness; the stale-heartbeat
--   last_heartbeat       detector in task 6.4 reads last_heartbeat.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. The three inline CONSTRAINT
-- clauses are the design's; section 1b re-asserts them under guards for
-- the case where this table already existed without them.
CREATE TABLE IF NOT EXISTS public.training_jobs (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    strategy_id          UUID NOT NULL REFERENCES public.strategies(id) ON DELETE CASCADE,
    version_id           UUID NOT NULL REFERENCES public.strategy_versions(id) ON DELETE CASCADE,
    node_id              TEXT NOT NULL,                 -- canonical ML node id
    block_id             TEXT NOT NULL,                 -- xgboost, lstm, ...

    status               TEXT NOT NULL DEFAULT 'QUEUED',
    cancel_requested     BOOLEAN NOT NULL DEFAULT FALSE,

    config               JSONB NOT NULL,                -- symbol, timeframe, range, splits,
                                                        -- epochs, batch, seed, horizon, embargo
    dataset_fingerprint  TEXT,                          -- reproducibility
    dataset_rows         INTEGER,
    usable_rows          INTEGER,
    feature_columns      INTEGER,
    feature_names        JSONB,
    split_sizes          JSONB,

    epochs_total         INTEGER,
    epoch_current        INTEGER NOT NULL DEFAULT 0,
    loss                 DOUBLE PRECISION,
    val_loss             DOUBLE PRECISION,
    metrics_history      JSONB,
    progress             DOUBLE PRECISION NOT NULL DEFAULT 0,
    failure_reason       TEXT,

    worker_id            TEXT,
    last_heartbeat       TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at           TIMESTAMPTZ,
    completed_at         TIMESTAMPTZ,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_tj_status CHECK (status IN ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED')),
    CONSTRAINT chk_tj_progress CHECK (progress >= 0 AND progress <= 1),
    CONSTRAINT chk_tj_failed_has_reason CHECK (status <> 'FAILED' OR failure_reason IS NOT NULL)
);

-- 1a) Shape assertion --------------------------------------------------
-- CREATE TABLE IF NOT EXISTS is silent about a pre-existing table of the
-- same name and a different shape. Assert the columns the rest of this
-- file and tasks 6.3-6.6 depend on, so a mismatch fails here with a
-- readable message instead of at the first insert in production.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    missing TEXT;
BEGIN
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('id'), ('user_id'), ('strategy_id'), ('version_id'),
                   ('node_id'), ('block_id'), ('status'), ('cancel_requested'),
                   ('config'), ('dataset_fingerprint'), ('dataset_rows'),
                   ('usable_rows'), ('feature_columns'), ('feature_names'),
                   ('split_sizes'), ('epochs_total'), ('epoch_current'),
                   ('loss'), ('val_loss'), ('metrics_history'), ('progress'),
                   ('failure_reason'), ('worker_id'), ('last_heartbeat'),
                   ('created_at'), ('started_at'), ('completed_at'),
                   ('updated_at')) AS expected(column_name)
     WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns c
         WHERE c.table_schema = 'public'
           AND c.table_name   = 'training_jobs'
           AND c.column_name  = expected.column_name
     );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            'public.training_jobs exists but is missing column(s): %. A '
            'pre-existing table of that name has a different shape; '
            'reconcile it by hand before re-running this migration.', missing;
    END IF;
END $$;

-- 1b) Constraint guards ------------------------------------------------
-- No-ops on a fresh run: the three constraints were declared inline above.
-- They exist so that a training_jobs table created earlier WITHOUT them
-- gains them rather than silently keeping the invariants unenforced.
--
-- Idempotent: each ADD CONSTRAINT runs only when pg_constraint holds no
-- constraint of that name on this table, so a re-run adds nothing and
-- cannot raise 42710 duplicate_object.
DO $$
BEGIN
    -- Requirement 15.2's status vocabulary, and nothing outside it.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.training_jobs'::regclass
           AND conname  = 'chk_tj_status'
    ) THEN
        ALTER TABLE public.training_jobs
            ADD CONSTRAINT chk_tj_status
                CHECK (status IN ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED'));
    END IF;

    -- A reported progress fraction outside [0,1] is not a progress
    -- fraction. Requirement 15.4.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.training_jobs'::regclass
           AND conname  = 'chk_tj_progress'
    ) THEN
        ALTER TABLE public.training_jobs
            ADD CONSTRAINT chk_tj_progress
                CHECK (progress >= 0 AND progress <= 1);
    END IF;

    -- A FAILED job with no recorded reason is the silent-failure shape
    -- Requirement 15.10 forbids. The database refuses to store it.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.training_jobs'::regclass
           AND conname  = 'chk_tj_failed_has_reason'
    ) THEN
        ALTER TABLE public.training_jobs
            ADD CONSTRAINT chk_tj_failed_has_reason
                CHECK (status <> 'FAILED' OR failure_reason IS NOT NULL);
    END IF;
END $$;

-- 1c) Indexes ----------------------------------------------------------
-- design.md section 2, as written there.
--
-- uq_tj_active_per_node is the mechanism behind Requirement 15.13, and it
-- is a PARTIAL unique index for a reason: the uniqueness must hold over
-- LIVE jobs only. status IN ('QUEUED','RUNNING') is exactly the live set,
-- so a node may accumulate any number of COMPLETED, FAILED and CANCELLED
-- jobs (its history, and the retrain trail Requirement 17.3 needs) while
-- never having two live ones. A plain UNIQUE (version_id, node_id) would
-- instead make retraining impossible; no index at all would make the
-- double-click race representable.
CREATE UNIQUE INDEX IF NOT EXISTS uq_tj_active_per_node
    ON public.training_jobs(version_id, node_id)
    WHERE status IN ('QUEUED','RUNNING');

-- Supporting indexes: the owner's job list filtered by status, every job
-- of one version (the readiness re-check in task 6.5), and the newest
-- jobs first.
CREATE INDEX IF NOT EXISTS idx_tj_user_status ON public.training_jobs(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tj_version     ON public.training_jobs(version_id);
CREATE INDEX IF NOT EXISTS idx_tj_created     ON public.training_jobs(created_at DESC);

-- 2) Model versions ----------------------------------------------------
-- design.md "Migration 004_strategy_builder_canonical.sql", section 3,
-- column for column, schema-qualified.
--
--   training_job_id    the run that produced this artifact. NOT NULL: a
--                      model version with no provenance is not a model
--                      version.
--   version_id/node_id the binding Requirement 17.2 requires - ONE
--                      immutable strategy version, ONE model node.
--   model_version      monotonic per (version_id, node_id). uq_mv_version_node
--                      makes it UNIQUE within that pair; monotonicity
--                      itself is the writer's job in task 6.5 (SQL can
--                      enforce "no duplicate", not "always the next one").
--   artifact_uri       an OBJECT STORE REFERENCE, never the blob
--                      (Requirement 17.9). Server-side only: never
--                      returned raw to a client (Requirement 17.8; see the
--                      COMMENT in section 4).
--   artifact_checksum  what SafeModelLoader verifies before loading; a
--   artifact_bytes     mismatch parks the node as awaiting-a-model rather
--   serialization      than loading an unverified artifact
--                      (Requirement 17.6).
--   feature_schema     names + order + scaler params, so the deployment
--                      gate in Requirement 17.7 can compare the schema the
--                      model was trained on against the graph's current
--                      feature output and report expected vs actual.
--   hyperparameters    what was actually used, post-cap-clamping.
--   train_/val_/test_  Requirement 17.1's three metric sets.
--   metrics
--   is_active          exactly one TRUE per (version_id, node_id), by
--                      uq_mv_active_per_node below. Retraining inserts a
--                      new row; see the header's note on why deactivating
--                      the old one needs the service role.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. The inline uq_mv_version_node is
-- the design's; section 2b re-asserts it under a guard.
CREATE TABLE IF NOT EXISTS public.model_versions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    training_job_id   UUID NOT NULL REFERENCES public.training_jobs(id) ON DELETE CASCADE,
    strategy_id       UUID NOT NULL REFERENCES public.strategies(id) ON DELETE CASCADE,
    version_id        UUID NOT NULL REFERENCES public.strategy_versions(id) ON DELETE CASCADE,
    node_id           TEXT NOT NULL,

    block_id          TEXT NOT NULL,
    model_version     INTEGER NOT NULL,                 -- monotonic per (version_id, node_id)
    artifact_uri      TEXT NOT NULL,                    -- object store reference, NOT the blob
    artifact_checksum TEXT NOT NULL,                    -- verified by SafeModelLoader
    artifact_bytes    BIGINT NOT NULL,
    serialization     TEXT NOT NULL,

    feature_schema    JSONB NOT NULL,                   -- names + order + scaler params
    hyperparameters   JSONB NOT NULL,
    train_metrics     JSONB,
    val_metrics       JSONB,
    test_metrics      JSONB,

    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_mv_version_node UNIQUE (version_id, node_id, model_version)
);

-- 2a) Shape assertion --------------------------------------------------
-- Same reasoning as 1a.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    missing TEXT;
BEGIN
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('id'), ('user_id'), ('training_job_id'), ('strategy_id'),
                   ('version_id'), ('node_id'), ('block_id'),
                   ('model_version'), ('artifact_uri'), ('artifact_checksum'),
                   ('artifact_bytes'), ('serialization'), ('feature_schema'),
                   ('hyperparameters'), ('train_metrics'), ('val_metrics'),
                   ('test_metrics'), ('is_active'), ('created_at'))
             AS expected(column_name)
     WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns c
         WHERE c.table_schema = 'public'
           AND c.table_name   = 'model_versions'
           AND c.column_name  = expected.column_name
     );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            'public.model_versions exists but is missing column(s): %. A '
            'pre-existing table of that name has a different shape; '
            'reconcile it by hand before re-running this migration.', missing;
    END IF;
END $$;

-- 2b) Constraint guard -------------------------------------------------
-- No-op on a fresh run: uq_mv_version_node was declared inline above.
-- Present for the pre-existing-table case, so a second model_versions row
-- can never reuse a (version_id, node_id, model_version) triple and make
-- the version history ambiguous.
--
-- Idempotent: guarded on pg_constraint by name.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.model_versions'::regclass
           AND conname  = 'uq_mv_version_node'
    ) THEN
        ALTER TABLE public.model_versions
            ADD CONSTRAINT uq_mv_version_node UNIQUE (version_id, node_id, model_version);
    END IF;
END $$;

-- 2c) Indexes ----------------------------------------------------------
-- design.md section 3, as written there.
--
-- uq_mv_active_per_node is the mechanism behind Requirement 17.4, and it
-- is PARTIAL for the same class of reason as uq_tj_active_per_node: the
-- uniqueness must hold over ACTIVE rows only. "WHERE is_active" indexes
-- exactly the rows where is_active is TRUE (a boolean predicate admits
-- neither FALSE nor NULL, and the column is NOT NULL anyway), so a node
-- keeps its full retrain history as is_active = FALSE rows while never
-- having two active models. This is what makes the ML-3 retrain-overwrite
-- defect unrepresentable instead of merely unlikely: a second concurrent
-- "activate" fails on a unique violation rather than leaving a running
-- deployment resolving an artifact nobody chose.
CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_active_per_node
    ON public.model_versions(version_id, node_id) WHERE is_active;

-- Supporting indexes: a strategy's models, and an owner's models.
-- (version_id) lookups are served by uq_mv_version_node's leading column,
-- so the design specifies no separate index for them and none is added.
CREATE INDEX IF NOT EXISTS idx_mv_strategy ON public.model_versions(strategy_id);
CREATE INDEX IF NOT EXISTS idx_mv_user     ON public.model_versions(user_id);

-- 3) Row level security ------------------------------------------------
-- Requirement 21.3. Enable first, then add the policies: enabling RLS is
-- default-deny, so between these statements the tables are unreachable
-- rather than open - and every statement is inside the transaction, so no
-- session ever observes the intermediate state.
--
-- Idempotent: ENABLE ROW LEVEL SECURITY is a no-op when RLS is already
-- on (the same unguarded form 003_signal_trace_restoration.sql section 8
-- uses).
ALTER TABLE public.training_jobs  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_versions ENABLE ROW LEVEL SECURITY;

-- The design's five policies, verbatim in predicate and name. No DELETE
-- policy on either table and no UPDATE policy on model_versions - both
-- deliberate; see "ROW LEVEL SECURITY" in the header for the consequences
-- task 6.5 must design around.
--
-- Idempotent: each is guarded on pg_policies by schemaname + tablename +
-- policyname, the pattern 003_signal_trace_restoration.sql section 9 and
-- 004b section 4 already use. PostgreSQL has no CREATE POLICY IF NOT
-- EXISTS.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'training_jobs'
           AND policyname = 'tj_owner_select'
    ) THEN
        CREATE POLICY tj_owner_select ON public.training_jobs
            FOR SELECT USING (user_id = auth.uid());
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'training_jobs'
           AND policyname = 'tj_owner_insert'
    ) THEN
        CREATE POLICY tj_owner_insert ON public.training_jobs
            FOR INSERT WITH CHECK (user_id = auth.uid());
    END IF;

    -- USING with no WITH CHECK: PostgreSQL reuses the USING expression for
    -- the NEW row, so the owner can update their own job (for instance to
    -- set cancel_requested) but cannot reassign it to another user_id.
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'training_jobs'
           AND policyname = 'tj_owner_update'
    ) THEN
        CREATE POLICY tj_owner_update ON public.training_jobs
            FOR UPDATE USING (user_id = auth.uid());
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'model_versions'
           AND policyname = 'mv_owner_select'
    ) THEN
        CREATE POLICY mv_owner_select ON public.model_versions
            FOR SELECT USING (user_id = auth.uid());
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'model_versions'
           AND policyname = 'mv_owner_insert'
    ) THEN
        CREATE POLICY mv_owner_insert ON public.model_versions
            FOR INSERT WITH CHECK (user_id = auth.uid());
    END IF;
END $$;

-- 4) Column comments ---------------------------------------------------
-- Intent the schema cannot express, recorded where \d+ and every schema
-- browser will show it. COMMENT is naturally idempotent - it replaces.
--
-- The metrics_history comment is the data-minimisation rule from
-- design.md "Storage architecture", stated in the schema itself so the
-- next person to add a field to the training worker's progress payload
-- reads it before, not after. It is a RULE FOR THE WRITER, not an
-- enforced constraint: see "DATA MINIMISATION" in the header.
COMMENT ON TABLE public.training_jobs IS
    'One asynchronous training run per (strategy version, ML node). At most one row '
    'per (version_id, node_id) may be QUEUED or RUNNING - enforced by the partial '
    'unique index uq_tj_active_per_node (Requirement 15.13). Row-level ownership by '
    'user_id (Requirement 21.3).';

COMMENT ON COLUMN public.training_jobs.metrics_history IS
    'PER-EPOCH SCALARS ONLY - epoch index, loss, val_loss and comparable scalar '
    'metrics, bounded by the effective max_epochs cap. NEVER model predictions and '
    'NEVER feature values. This is data minimisation, not a size guideline: this '
    'column is read back for progress charting and is serialised into API responses '
    'and realtime frames, so a prediction vector or feature matrix stored here would '
    'expose the model output of a private strategy through a charting path. Model '
    'artifacts belong in object storage behind model_versions.artifact_uri; feature '
    'matrices and OHLCV history are not persisted here at all.';

COMMENT ON COLUMN public.training_jobs.config IS
    'Resolved training configuration: symbol, timeframe, range, splits, epochs, '
    'batch, seed, horizon, embargo. Contains NO exchange identifier and NO '
    'credential of any kind (Requirement 21.7).';

COMMENT ON COLUMN public.training_jobs.failure_reason IS
    'Classified failure reason (for example MAX_DURATION_EXCEEDED, WORKER_LOST), '
    'never a generic message (Requirement 15.10). A FAILED row without one is '
    'rejected by chk_tj_failed_has_reason.';

COMMENT ON TABLE public.model_versions IS
    'One trained model artifact bound to exactly one immutable strategy version and '
    'one ML node (Requirement 17.2). At most one row per (version_id, node_id) may '
    'have is_active - enforced by the partial unique index uq_mv_active_per_node '
    '(Requirement 17.4), which is what stops a retrain from overwriting the model a '
    'running deployment resolves. Row-level ownership by user_id (Requirement 21.3).';

COMMENT ON COLUMN public.model_versions.artifact_uri IS
    'Object-store reference to the artifact, never the artifact itself. '
    'SERVER-SIDE ONLY: never returned raw to a client. Downloads go through an '
    'ownership-checked signed endpoint with sanitised filenames (Requirement 17.8).';

COMMENT ON COLUMN public.model_versions.artifact_checksum IS
    'Checksum verified before the artifact is loaded. On mismatch the node is marked '
    'as awaiting a model and the deployment is held out of running, rather than '
    'loading an unverified artifact (Requirement 17.6).';

-- 5) Grants ------------------------------------------------------------
-- Defence in depth behind RLS, following 004b section 5. Supabase grants
-- the public schema's tables to anon and authenticated by default, which
-- would hand anon full DML on both tables the moment anyone ran
-- ALTER TABLE ... DISABLE ROW LEVEL SECURITY. Narrowing the grants means
-- RLS is not the only thing standing between an anon API key and a user's
-- training work, and it makes the policy set's shape true at the privilege
-- layer too.
--
-- REVOKE here only narrows privileges on the two tables created above. It
-- removes no row and touches no other relation.
--
-- Idempotent: GRANT and REVOKE converge on the same privilege set however
-- many times they run.
REVOKE ALL ON public.training_jobs  FROM anon;
REVOKE ALL ON public.training_jobs  FROM authenticated;
REVOKE ALL ON public.model_versions FROM anon;
REVOKE ALL ON public.model_versions FROM authenticated;

-- training_jobs: the owner reads their jobs, creates them through the save
-- and training endpoints, and updates them (cancel_requested). Note what
-- is NOT granted: no DELETE, so job history cannot be erased by a client.
GRANT SELECT, INSERT, UPDATE ON public.training_jobs TO authenticated;

-- model_versions: read and append only. No UPDATE, matching the absence of
-- an mv_owner_update policy - a client cannot flip is_active and so cannot
-- change which artifact a running deployment resolves. No DELETE, so a
-- model's provenance cannot be erased.
GRANT SELECT, INSERT ON public.model_versions TO authenticated;

-- The backend's own role. service_role bypasses RLS but still needs the
-- table privilege, so state it rather than relying on default privileges.
-- UPDATE on model_versions is granted HERE AND ONLY HERE: deactivating a
-- superseded model version (task 6.5) is a backend operation, never a
-- client one. Still no DELETE for anybody.
GRANT SELECT, INSERT, UPDATE ON public.training_jobs  TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.model_versions TO service_role;

COMMIT;

-- VERIFICATION (run after applying) ------------------------------------
--
-- 1. Columns. Expect 28 rows for training_jobs and 19 for model_versions.
--    user_id, strategy_id, version_id, node_id, block_id, status,
--    cancel_requested, config, epoch_current, progress, created_at and
--    updated_at must show is_nullable = NO on training_jobs; every
--    artifact_* column, serialization, feature_schema, hyperparameters,
--    is_active and created_at must show is_nullable = NO on
--    model_versions.
--
--   SELECT table_name, column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'public'
--     AND table_name IN ('training_jobs','model_versions')
--   ORDER BY table_name, ordinal_position;
--
-- 2. CHECK constraints on training_jobs. Expect exactly 3 rows:
--    chk_tj_failed_has_reason, chk_tj_progress, chk_tj_status.
--
--   SELECT conname, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--   WHERE conrelid = 'public.training_jobs'::regclass AND contype = 'c'
--   ORDER BY conname;
--
-- 3. uq_mv_version_node present as a UNIQUE constraint over exactly
--    (version_id, node_id, model_version).
--
--   SELECT conname, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--   WHERE conrelid = 'public.model_versions'::regclass
--     AND conname = 'uq_mv_version_node';
--
-- 4. Indexes, and above all the two PARTIAL predicates. Expect 4 rows for
--    training_jobs (uq_tj_active_per_node, idx_tj_user_status,
--    idx_tj_version, idx_tj_created) and 3 for model_versions
--    (uq_mv_active_per_node, idx_mv_strategy, idx_mv_user), plus the
--    primary-key and unique-constraint indexes.
--
--    uq_tj_active_per_node MUST show
--      UNIQUE ... (version_id, node_id) WHERE (status = ANY (ARRAY['QUEUED','RUNNING']))
--    uq_mv_active_per_node MUST show
--      UNIQUE ... (version_id, node_id) WHERE is_active
--
--    A missing WHERE clause on either is a silent functional change, not a
--    cosmetic one: uq_tj_active_per_node without its predicate would make
--    retraining a node impossible, and uq_mv_active_per_node without its
--    predicate would make a second model version for a node impossible.
--
--   SELECT tablename, indexname, indexdef
--   FROM pg_indexes
--   WHERE schemaname = 'public'
--     AND tablename IN ('training_jobs','model_versions')
--   ORDER BY tablename, indexname;
--
-- 5. The two invariants, exercised directly (Requirements 15.13, 17.4).
--    Both INSERTs must fail with a unique_violation naming the index.
--
--   BEGIN;
--     -- second live job for the same (version, node): expect
--     -- 23505 duplicate key value violates unique constraint
--     -- "uq_tj_active_per_node"
--     INSERT INTO public.training_jobs (user_id, strategy_id, version_id, node_id,
--                                       block_id, status, config)
--     SELECT user_id, strategy_id, version_id, node_id, block_id, 'QUEUED', config
--     FROM public.training_jobs
--     WHERE status IN ('QUEUED','RUNNING') LIMIT 1;
--   ROLLBACK;
--
--   BEGIN;
--     -- second active model for the same (version, node): expect
--     -- 23505 ... "uq_mv_active_per_node"
--     INSERT INTO public.model_versions (user_id, training_job_id, strategy_id,
--                                        version_id, node_id, block_id, model_version,
--                                        artifact_uri, artifact_checksum, artifact_bytes,
--                                        serialization, feature_schema, hyperparameters)
--     SELECT user_id, training_job_id, strategy_id, version_id, node_id, block_id,
--            model_version + 1, artifact_uri, artifact_checksum, artifact_bytes,
--            serialization, feature_schema, hyperparameters
--     FROM public.model_versions WHERE is_active LIMIT 1;
--   ROLLBACK;
--
--    And the same pair must SUCCEED once the earlier row is no longer live
--    or no longer active - that is the whole point of the partial
--    predicates. Retraining a node whose jobs are all COMPLETED/FAILED/
--    CANCELLED, and inserting a model version while the previous one has
--    is_active = FALSE, are both legal.
--
-- 6. chk_tj_failed_has_reason (Requirement 15.10): a FAILED job with no
--    reason must be refused by the database.
--
--   BEGIN;
--     -- expect 23514 check_violation naming chk_tj_failed_has_reason
--     UPDATE public.training_jobs
--        SET status = 'FAILED', failure_reason = NULL
--      WHERE id = (SELECT id FROM public.training_jobs LIMIT 1);
--   ROLLBACK;
--
-- 7. RLS on, and exactly the five expected policies:
--      tj_owner_select  SELECT  qual (user_id = auth.uid())
--      tj_owner_insert  INSERT  with_check (user_id = auth.uid())
--      tj_owner_update  UPDATE  qual (user_id = auth.uid())
--      mv_owner_select  SELECT  qual (user_id = auth.uid())
--      mv_owner_insert  INSERT  with_check (user_id = auth.uid())
--    A row with cmd DELETE or ALL appearing here, or an UPDATE policy on
--    model_versions, would mean a control has been widened.
--
--   SELECT relname, relrowsecurity, relforcerowsecurity
--   FROM pg_class
--   WHERE oid IN ('public.training_jobs'::regclass,
--                 'public.model_versions'::regclass);
--
--   SELECT tablename, policyname, cmd, roles, qual, with_check
--   FROM pg_policies
--   WHERE schemaname = 'public'
--     AND tablename IN ('training_jobs','model_versions')
--   ORDER BY tablename, policyname;
--
-- 8. Grants. Expect authenticated: SELECT+INSERT+UPDATE on training_jobs,
--    SELECT+INSERT on model_versions; service_role: SELECT+INSERT+UPDATE
--    on both; anon: nothing. No DELETE for any grantee.
--
--   SELECT table_name, grantee, privilege_type
--   FROM information_schema.role_table_grants
--   WHERE table_schema = 'public'
--     AND table_name IN ('training_jobs','model_versions')
--   ORDER BY table_name, grantee, privilege_type;
--
-- 9. The metrics_history rule is visible in the schema itself:
--
--   SELECT col_description('public.training_jobs'::regclass, attnum)
--   FROM pg_attribute
--   WHERE attrelid = 'public.training_jobs'::regclass
--     AND attname = 'metrics_history';
--
-- 10. Existing RLS untouched. strategy_versions must still show the same
--     3 policies it had before (SELECT / INSERT / UPDATE, each scoped
--     through strategies.user_id = auth.uid()), and strategy_deployments
--     and signals their own 3 each, all with rowsecurity = true.
--
--   SELECT tablename, policyname, cmd FROM pg_policies
--   WHERE schemaname = 'public'
--     AND tablename IN ('strategy_versions','strategy_deployments','signals',
--                       'block_registry_snapshots')
--   ORDER BY tablename, policyname;
--
-- 11. Idempotency. Re-running this whole file must report no error and
--     leave the counts in 1, 2, 3, 4, 7 and 8 unchanged.
