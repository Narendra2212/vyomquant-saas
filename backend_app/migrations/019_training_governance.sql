-- 019_training_governance.sql
--
-- PURPOSE
--   Make a training governance DECISION durable and auditable, for both of the
--   answers it can give.
--
--   Before this file, a decision was recorded in exactly one place and only on
--   one path: an ADMITTED job's `training_jobs.config` carried the approved
--   epoch ceiling, the wall clock and the early-stopping terms, which is what
--   the worker executes. That is enough to ENFORCE a decision and not enough to
--   ANSWER a question about one:
--
--     * A REFUSED request wrote nothing at all. `prepare_training` raises
--       `TrainingBlocked` before any row exists - deliberately, so a blocked
--       path cannot leave a job behind - so the only trace was a log line and a
--       Prometheus counter. "Why was this account refused last Tuesday" had no
--       answer, and neither did "how often does the single-class-target gate
--       fire".
--     * An ADMITTED job recorded what was approved but never what HAPPENED. The
--       epoch ceiling came partly from a throughput ESTIMATE
--       (`estimate_seconds_per_epoch`), and with no measured actual to compare
--       it against that estimate could be wrong by an order of magnitude
--       indefinitely with nothing to reveal it.
--
--   So this file adds two things:
--
--     1. The `requested -> approved -> actual` triple on `training_jobs`, as
--        columns rather than only inside `config`, so it is queryable and
--        indexable rather than reachable only by walking a JSONB document.
--     2. `training_governance_decisions`, an append-only record of every
--        admission decision INCLUDING refusals - the one thing no existing table
--        can hold, because a refusal creates no job.
--
-- SCOPE
--   Additive only. No DROP, no TRUNCATE, no DELETE, no UPDATE of existing rows,
--   no column type change. Every new column on `training_jobs` is NULLABLE with
--   no default, so applying this file rewrites no existing row and every job
--   queued before it reads as "not recorded" rather than as zero.
--
--   This file does NOT change `training_jobs.config`, `chk_tj_status`,
--   `chk_tj_progress`, `chk_tj_failed_has_reason`, `uq_tj_active_per_node`, any
--   existing index, or any existing RLS policy. 004d's invariants are untouched.
--
-- SOURCE OF THE DEFINITION
--   `ml_training_policy.TrainingBudget.to_dict()` and `GateVerdict.to_dict()`
--   for the shape; `strategy_service.GOVERNANCE_JOB_COLUMNS` for the exact
--   column list, which that module uses to recognise a missing-column error and
--   degrade; `training_worker._actuals_row` for the measured half.
--   `strategy_service.GOVERNANCE_MIGRATION` names this file, so every
--   degradation warning in the application points here.
--
-- WHY THE APPLICATION DEGRADES RATHER THAN REQUIRING THIS FILE
--   `insert_training_job` merges the governance columns into its INSERT and
--   DROPS them, with a warning naming this file, when PostgREST reports an
--   unknown column; `training_worker._transition` does the same for the actuals.
--   The reason is a deployment-order one and it matters: the enforcement lives
--   in `config`, which 004d already created. An environment that has this code
--   but not this migration therefore queues jobs that are fully GOVERNED and
--   whose audit trail is thinner - rather than refusing to queue at all, which
--   is what a hard requirement on an unapplied migration would produce.
--
--   Stated plainly so nobody reads the degradation as optional governance: the
--   caps, the budget and the early-stopping terms are enforced with or without
--   this file. What is lost without it is the ability to ask questions afterwards.
--
-- DATA MINIMISATION - `dataset_measurements` AND `dataset_warnings`
--   Both hold STATISTICS ONLY: counts, ratios, booleans, column NAMES and gate
--   codes. Never a label vector, never a feature value, never a prediction.
--
--   The distinction is exact and load-bearing: a class COUNT ("1,176 rows are
--   class 0") is a statistic about the dataset, while a class ASSIGNMENT per row
--   would be the model's target - which is to say the user's private strategy
--   output. `training_jobs.metrics_history` carries the same rule from 004d, and
--   for the same reason: these documents are read back over the API and pushed
--   over realtime frames.
--
--   STATED PLAINLY: SQL CANNOT ENFORCE THIS. A CHECK can see that the column is
--   JSONB; it cannot tell a class histogram from a label column. The rule is
--   recorded in COMMENT ON COLUMN below, where `\d+` and every schema browser
--   will show it, and the enforcement is the writer's -
--   `ml_dataset.DatasetMeasurements.to_dict()`, which emits a fixed set of
--   scalar and name-list keys and nothing else. Do not read the comment as a
--   guarantee.
--
-- ROW LEVEL SECURITY
--   `training_jobs` already has RLS enabled with `tj_owner_select`,
--   `tj_owner_insert` and `tj_owner_update` (004d section 3). New columns inherit
--   those policies; nothing is added or changed for that table.
--
--   `training_governance_decisions` enables RLS and gets owner SELECT and owner
--   INSERT, matching `tj_owner_*`. Deliberately NO update policy and NO delete
--   policy, and no UPDATE/DELETE grant to `authenticated`: a governance decision
--   is a historical fact and an append-only record that a client can edit is not
--   an audit trail. Requirement 25's "do not change historical decisions
--   retroactively" is held by the absence of those policies rather than by
--   convention.
--
-- PRECONDITIONS
--   `public.training_jobs` exists (apply
--   `backend_app/migrations/004d_training_and_models.sql` first).
--   `auth.users`, `public.strategies`, `public.strategy_versions` exist.
--   The Supabase roles `anon`, `authenticated`, `service_role` exist.
--   Asserted in section 0, so a missing prerequisite is a readable message
--   rather than a bare 42P01 from inside an ALTER.
--
-- SAFETY
--   Idempotent statement by statement: `ADD COLUMN IF NOT EXISTS`,
--   `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, and every
--   constraint and policy guarded on `pg_constraint` / `pg_policies` by name
--   (PostgreSQL has neither `ADD CONSTRAINT IF NOT EXISTS` nor
--   `CREATE POLICY IF NOT EXISTS`). Re-running this file adds nothing and
--   raises nothing.
--
--   Everything is inside one transaction, so no session observes a table with
--   RLS enabled and no policies, nor a column that exists without its CHECK.
--
-- APPLICATION
--   Applied BY HAND, per file. Nothing in this repository records which
--   migrations an environment has run - `.github/workflows/03-deploy.yml` has no
--   migration step - which is why this is a NEW FILE rather than an edit to an
--   existing one: a new filename is the only signal that there is something to
--   apply.
--
--   Verification queries are at the end of this file.

BEGIN;

-- 0) Preflight ---------------------------------------------------------
-- Read-only assertions. Writes nothing; safe to re-run.
DO $$
DECLARE
    missing_roles TEXT;
BEGIN
    IF to_regclass('public.training_jobs') IS NULL THEN
        RAISE EXCEPTION
            '019 precondition failed: table public.training_jobs does not exist. '
            'Apply backend_app/migrations/004d_training_and_models.sql first.';
    END IF;

    IF to_regclass('public.strategies') IS NULL
       OR to_regclass('public.strategy_versions') IS NULL THEN
        RAISE EXCEPTION
            '019 precondition failed: public.strategies and/or '
            'public.strategy_versions do not exist. Apply the base schema first.';
    END IF;

    IF to_regclass('auth.users') IS NULL THEN
        RAISE EXCEPTION
            '019 precondition failed: table auth.users does not exist. This '
            'migration targets a Supabase database, where the auth schema is '
            'created by the platform.';
    END IF;

    SELECT string_agg(expected.rolname, ', ' ORDER BY expected.rolname)
      INTO missing_roles
      FROM (VALUES ('anon'), ('authenticated'), ('service_role'))
             AS expected(rolname)
     WHERE NOT EXISTS (
        SELECT 1 FROM pg_roles r WHERE r.rolname = expected.rolname
     );

    IF missing_roles IS NOT NULL THEN
        RAISE EXCEPTION
            '019 precondition failed: role(s) % do not exist. This migration '
            'targets a Supabase database, where these roles are created by the '
            'platform.', missing_roles;
    END IF;
END $$;

-- 1) The governance decision, on the job row ---------------------------
--
-- All NULLABLE with no default, on purpose. A job queued before this file ran
-- genuinely has no recorded decision, and a DEFAULT would backfill every one of
-- them with a number nobody decided - which is worse than an honest NULL,
-- because a reader cannot tell a default from a measurement.
--
--   policy_version            which policy admitted this job. Requirement 25:
--                             historical decisions are never re-evaluated, so a
--                             row has to say which rules it was judged by.
--   task                      classification | regression | reconstruction. The
--                             APPROVED task. The worker reads this rather than
--                             re-deriving it from a label mode, so a later edit
--                             to the configuration cannot change what a running
--                             job is fitting.
--   gate_outcome              VALID | WARNING | BLOCKED from the
--                             data-sufficiency engine. A row is only ever
--                             created for VALID or WARNING - BLOCKED never
--                             produces a job - so a BLOCKED value here would be
--                             a defect, and the column admits it only because
--                             the vocabulary belongs to one enum.
--   requested_epochs          what the client asked for.
--   recommended_epochs        what the model spec recommends, bounded by plan.
--   approved_max_epochs       the ceiling the policy engine approved.
--                             `epochs_total` (004d) is what will actually run;
--                             these three are Requirement 21's triple, kept so a
--                             refusal and an admission can be compared.
--   min_meaningful_epochs     the UNDERFITTING floor: no quality control may
--                             stop a run before this. Recorded because it is a
--                             promise to the author, not an implementation
--                             detail.
--   max_wall_clock_seconds    the duration cap this job was admitted under.
--   estimated_seconds_per_epoch   AN ESTIMATE, and named one. The third term of
--                             the triple (`actual_duration_seconds`) is what
--                             makes it correctable.
--   estimated_memory_mb       likewise an estimate; `actual_peak_memory_mb` is
--                             the measurement.
--   training_budget           the whole `TrainingBudget` document, including the
--                             early-stopping terms. The scalars above are the
--                             queryable projection of it; this is the complete
--                             record.
--   dataset_measurements      measured label / index / feature statistics. See
--                             "DATA MINIMISATION" in the header.
--   dataset_warnings          the structured concerns this job was ADMITTED
--                             with. The outcome most worth tracking: a block is
--                             visible because the author asks, a clean pass needs
--                             no attention, and a fleet of warned-but-admitted
--                             runs is a fleet shipping models that will not
--                             generalise.
--   actual_epochs             epochs that COMPLETED. Not `epochs_total`: an
--                             early-stopped run completed fewer and that is a
--                             success, but it has to be recorded as fewer or the
--                             epochs-used distribution is flat at 1.0 forever
--                             and a too-tight budget is invisible.
--   actual_duration_seconds   measured wall clock.
--   actual_peak_memory_mb     measured peak, from `ml_safety.MemoryMonitor`.
--   actual_artifact_bytes     measured artifact size, for the storage budget.
--   stopped_reason            EPOCH_CEILING | NO_IMPROVEMENT | DIVERGED. Why the
--                             run ended. A COMPLETED status cannot distinguish
--                             "converged" from "ran out of ceiling", and those
--                             are very different facts about a model.
--   best_epoch / best_metric  the epoch whose monitored metric was best, and its
--                             value - so a reader can tell whether the stored
--                             artifact is the best weights or the last ones.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS throughout.
ALTER TABLE public.training_jobs
    ADD COLUMN IF NOT EXISTS policy_version              TEXT,
    ADD COLUMN IF NOT EXISTS task                        TEXT,
    ADD COLUMN IF NOT EXISTS gate_outcome                TEXT,
    ADD COLUMN IF NOT EXISTS requested_epochs            INTEGER,
    ADD COLUMN IF NOT EXISTS recommended_epochs          INTEGER,
    ADD COLUMN IF NOT EXISTS approved_max_epochs         INTEGER,
    ADD COLUMN IF NOT EXISTS min_meaningful_epochs       INTEGER,
    ADD COLUMN IF NOT EXISTS max_wall_clock_seconds      INTEGER,
    ADD COLUMN IF NOT EXISTS estimated_seconds_per_epoch DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS estimated_memory_mb         INTEGER,
    ADD COLUMN IF NOT EXISTS training_budget             JSONB,
    ADD COLUMN IF NOT EXISTS dataset_measurements        JSONB,
    ADD COLUMN IF NOT EXISTS dataset_warnings            JSONB,
    ADD COLUMN IF NOT EXISTS actual_epochs               INTEGER,
    ADD COLUMN IF NOT EXISTS actual_duration_seconds     INTEGER,
    ADD COLUMN IF NOT EXISTS actual_peak_memory_mb       INTEGER,
    ADD COLUMN IF NOT EXISTS actual_artifact_bytes       BIGINT,
    ADD COLUMN IF NOT EXISTS stopped_reason              TEXT,
    ADD COLUMN IF NOT EXISTS best_epoch                  INTEGER,
    ADD COLUMN IF NOT EXISTS best_metric                 DOUBLE PRECISION;

-- 1a) Shape assertion --------------------------------------------------
-- ADD COLUMN IF NOT EXISTS is silent about a column that already exists with a
-- different TYPE. Assert the columns `strategy_service.GOVERNANCE_JOB_COLUMNS`
-- names, so a mismatch fails here with a readable message instead of at the
-- first insert.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    missing TEXT;
BEGIN
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('policy_version'), ('task'), ('gate_outcome'),
                   ('requested_epochs'), ('recommended_epochs'),
                   ('approved_max_epochs'), ('min_meaningful_epochs'),
                   ('max_wall_clock_seconds'), ('estimated_seconds_per_epoch'),
                   ('estimated_memory_mb'), ('training_budget'),
                   ('dataset_measurements'), ('dataset_warnings'),
                   ('actual_epochs'), ('actual_duration_seconds'),
                   ('actual_peak_memory_mb'), ('actual_artifact_bytes'),
                   ('stopped_reason'), ('best_epoch'), ('best_metric'))
             AS expected(column_name)
     WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns c
         WHERE c.table_schema = 'public'
           AND c.table_name   = 'training_jobs'
           AND c.column_name  = expected.column_name
     );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            'public.training_jobs is missing governance column(s) after this '
            'migration ran: %. A pre-existing column of that name may have a '
            'different shape; reconcile it by hand.', missing;
    END IF;
END $$;

-- 1b) Constraint guards ------------------------------------------------
-- Closed vocabularies and non-negative counts, as CHECKs rather than as writer
-- conventions. Each admits NULL, because a job queued before this file has no
-- recorded value and must stay valid.
--
-- Idempotent: each runs only when pg_constraint holds no constraint of that name
-- on this table, so a re-run adds nothing and cannot raise 42710.
DO $$
BEGIN
    -- `ml_training_policy.GateOutcome`, verbatim.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.training_jobs'::regclass
           AND conname  = 'chk_tj_gate_outcome'
    ) THEN
        ALTER TABLE public.training_jobs
            ADD CONSTRAINT chk_tj_gate_outcome
                CHECK (gate_outcome IS NULL
                       OR gate_outcome IN ('VALID','WARNING','BLOCKED'));
    END IF;

    -- `ml_models.ModelTask`, verbatim. Lowercase, matching
    -- `ml_dataset.LabelMode` so the task and the label mode are one spelling.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.training_jobs'::regclass
           AND conname  = 'chk_tj_task'
    ) THEN
        ALTER TABLE public.training_jobs
            ADD CONSTRAINT chk_tj_task
                CHECK (task IS NULL
                       OR task IN ('classification','regression','reconstruction'));
    END IF;

    -- `training_worker.STOP_REASONS`, verbatim. An empty string is neither a
    -- reason nor an absence, so it is not admitted - the writer omits the key.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.training_jobs'::regclass
           AND conname  = 'chk_tj_stopped_reason'
    ) THEN
        ALTER TABLE public.training_jobs
            ADD CONSTRAINT chk_tj_stopped_reason
                CHECK (stopped_reason IS NULL
                       OR stopped_reason IN ('EPOCH_CEILING','NO_IMPROVEMENT','DIVERGED'));
    END IF;

    -- Counts and durations cannot be negative. A negative actual would mean the
    -- measurement is wrong, and storing it would poison every aggregate built on
    -- this column.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.training_jobs'::regclass
           AND conname  = 'chk_tj_governance_counts_nonneg'
    ) THEN
        ALTER TABLE public.training_jobs
            ADD CONSTRAINT chk_tj_governance_counts_nonneg
                CHECK (
                    (requested_epochs            IS NULL OR requested_epochs            >= 0)
                AND (recommended_epochs          IS NULL OR recommended_epochs          >= 0)
                AND (approved_max_epochs         IS NULL OR approved_max_epochs         >= 0)
                AND (min_meaningful_epochs       IS NULL OR min_meaningful_epochs       >= 0)
                AND (max_wall_clock_seconds      IS NULL OR max_wall_clock_seconds      >= 0)
                AND (estimated_seconds_per_epoch IS NULL OR estimated_seconds_per_epoch >= 0)
                AND (estimated_memory_mb         IS NULL OR estimated_memory_mb         >= 0)
                AND (actual_epochs               IS NULL OR actual_epochs               >= 0)
                AND (actual_duration_seconds     IS NULL OR actual_duration_seconds     >= 0)
                AND (actual_peak_memory_mb       IS NULL OR actual_peak_memory_mb       >= 0)
                AND (actual_artifact_bytes       IS NULL OR actual_artifact_bytes       >= 0)
                AND (best_epoch                  IS NULL OR best_epoch                  >= 0)
                );
    END IF;

    -- The underfitting floor cannot exceed the ceiling it sits under. If it
    -- did, no run could satisfy both and every job would be unsatisfiable - so
    -- this is the one relational invariant of the budget worth asserting in SQL.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.training_jobs'::regclass
           AND conname  = 'chk_tj_budget_floor_under_ceiling'
    ) THEN
        ALTER TABLE public.training_jobs
            ADD CONSTRAINT chk_tj_budget_floor_under_ceiling
                CHECK (
                    min_meaningful_epochs IS NULL
                 OR approved_max_epochs   IS NULL
                 OR min_meaningful_epochs <= approved_max_epochs
                );
    END IF;
END $$;

-- 1c) Indexes ----------------------------------------------------------
--
-- Both PARTIAL, and for the same reason 004d's are: the interesting rows are a
-- small minority and a full index would be mostly dead weight on a table whose
-- hot path is `idx_tj_user_status`.
--
-- `idx_tj_warned` serves the question this governance layer most needs answered:
-- which admitted runs carried a data-quality concern. `idx_tj_stopped_reason`
-- serves the other: how runs are ending, and specifically how many are hitting
-- their ceiling rather than converging - which is the signal that epoch budgets
-- are too tight.
CREATE INDEX IF NOT EXISTS idx_tj_warned
    ON public.training_jobs(user_id, created_at DESC)
    WHERE gate_outcome = 'WARNING';

CREATE INDEX IF NOT EXISTS idx_tj_stopped_reason
    ON public.training_jobs(stopped_reason, created_at DESC)
    WHERE stopped_reason IS NOT NULL;

-- 2) Every decision, including the refusals ----------------------------
--
-- WHY A TABLE AND NOT MORE COLUMNS
--   A refusal creates no `training_jobs` row, by design: `prepare_training`
--   raises before the insert so that "no job on a blocked path" is structural
--   rather than promised. There is therefore no row to add a column to, and this
--   is the only shape that can record a refusal.
--
--   It is also the only table here that is written on BOTH paths, which makes it
--   the one place the ratio of admissions to refusals - and the distribution of
--   refusal reasons - can be read from stored fact rather than from a counter
--   that resets with the process.
--
--   `training_job_id` is nullable precisely because a refusal has none. It is
--   NOT a foreign key with ON DELETE CASCADE by accident: a decision outlives
--   the job it admitted, so deleting a strategy must not erase the record that
--   its training was authorised. The user FK keeps ON DELETE CASCADE, because a
--   deleted account's audit trail is personal data that should go with it.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS; the inline CHECK is re-asserted under
-- a guard in section 2b.
CREATE TABLE IF NOT EXISTS public.training_governance_decisions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    -- What was asked about. All nullable: a refusal can happen before a version
    -- or a node is resolved, and recording a decision is more important than
    -- recording a complete one.
    strategy_id       UUID REFERENCES public.strategies(id) ON DELETE SET NULL,
    version_id        UUID REFERENCES public.strategy_versions(id) ON DELETE SET NULL,
    node_id           TEXT,
    block_id          TEXT,
    -- Set only on an admission, and deliberately NOT cascading: see above.
    training_job_id   UUID REFERENCES public.training_jobs(id) ON DELETE SET NULL,

    -- The decision.
    decision          TEXT NOT NULL,           -- ADMITTED | DEFERRED | REFUSED
    reason            TEXT,                    -- the classified reason, never free text
    gate_outcome      TEXT,                    -- VALID | WARNING | BLOCKED
    task              TEXT,
    policy_version    TEXT NOT NULL,

    -- What it was decided from and to. Statistics only - see "DATA
    -- MINIMISATION" in the header.
    plan              TEXT,                    -- the plan id the caps came from
    requested_epochs  INTEGER,
    approved_epochs   INTEGER,
    training_budget   JSONB,
    detail            JSONB,                   -- the structured verdict payload

    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_tgd_decision
        CHECK (decision IN ('ADMITTED','DEFERRED','REFUSED')),
    CONSTRAINT chk_tgd_gate_outcome
        CHECK (gate_outcome IS NULL OR gate_outcome IN ('VALID','WARNING','BLOCKED')),
    CONSTRAINT chk_tgd_epochs_nonneg
        CHECK ((requested_epochs IS NULL OR requested_epochs >= 0)
           AND (approved_epochs  IS NULL OR approved_epochs  >= 0))
);

-- 2a) Shape assertion --------------------------------------------------
-- Same reasoning as 1a and as 004d's: CREATE TABLE IF NOT EXISTS is silent
-- about a pre-existing table of the same name and a different shape.
DO $$
DECLARE
    missing TEXT;
BEGIN
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('id'), ('user_id'), ('strategy_id'), ('version_id'),
                   ('node_id'), ('block_id'), ('training_job_id'), ('decision'),
                   ('reason'), ('gate_outcome'), ('task'), ('policy_version'),
                   ('plan'), ('requested_epochs'), ('approved_epochs'),
                   ('training_budget'), ('detail'), ('created_at'))
             AS expected(column_name)
     WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns c
         WHERE c.table_schema = 'public'
           AND c.table_name   = 'training_governance_decisions'
           AND c.column_name  = expected.column_name
     );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            'public.training_governance_decisions exists but is missing '
            'column(s): %. A pre-existing table of that name has a different '
            'shape; reconcile it by hand before re-running this migration.',
            missing;
    END IF;
END $$;

-- 2b) Constraint guards ------------------------------------------------
-- No-ops on a fresh run; present for the pre-existing-table case, so a table
-- created earlier without them gains them rather than silently keeping the
-- vocabularies unenforced.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.training_governance_decisions'::regclass
           AND conname  = 'chk_tgd_decision'
    ) THEN
        ALTER TABLE public.training_governance_decisions
            ADD CONSTRAINT chk_tgd_decision
                CHECK (decision IN ('ADMITTED','DEFERRED','REFUSED'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.training_governance_decisions'::regclass
           AND conname  = 'chk_tgd_gate_outcome'
    ) THEN
        ALTER TABLE public.training_governance_decisions
            ADD CONSTRAINT chk_tgd_gate_outcome
                CHECK (gate_outcome IS NULL
                       OR gate_outcome IN ('VALID','WARNING','BLOCKED'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.training_governance_decisions'::regclass
           AND conname  = 'chk_tgd_epochs_nonneg'
    ) THEN
        ALTER TABLE public.training_governance_decisions
            ADD CONSTRAINT chk_tgd_epochs_nonneg
                CHECK ((requested_epochs IS NULL OR requested_epochs >= 0)
                   AND (approved_epochs  IS NULL OR approved_epochs  >= 0));
    END IF;
END $$;

-- 2c) Indexes ----------------------------------------------------------
-- An owner's own decisions, newest first - the read a "why was I refused"
-- surface makes. And the refusal reasons, partially indexed, for the operational
-- question "which gate is firing".
CREATE INDEX IF NOT EXISTS idx_tgd_user_created
    ON public.training_governance_decisions(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_tgd_version
    ON public.training_governance_decisions(version_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_tgd_refusals
    ON public.training_governance_decisions(reason, created_at DESC)
    WHERE decision = 'REFUSED';

-- 3) Row level security ------------------------------------------------
-- Enable first (default-deny), then add policies. Both statements are inside the
-- transaction, so no session observes the intermediate state.
ALTER TABLE public.training_governance_decisions ENABLE ROW LEVEL SECURITY;

-- Owner SELECT and owner INSERT, matching 004d's `tj_owner_select` /
-- `tj_owner_insert`. NO update policy and NO delete policy, deliberately: see
-- "ROW LEVEL SECURITY" in the header.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'training_governance_decisions'
           AND policyname = 'tgd_owner_select'
    ) THEN
        CREATE POLICY tgd_owner_select ON public.training_governance_decisions
            FOR SELECT USING (user_id = auth.uid());
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'training_governance_decisions'
           AND policyname = 'tgd_owner_insert'
    ) THEN
        CREATE POLICY tgd_owner_insert ON public.training_governance_decisions
            FOR INSERT WITH CHECK (user_id = auth.uid());
    END IF;
END $$;

-- 4) Column comments ---------------------------------------------------
-- Intent the schema cannot express, recorded where `\d+` shows it.
COMMENT ON COLUMN public.training_jobs.policy_version IS
    'Which ml_training_policy version admitted this job. Historical decisions are '
    'never re-evaluated (Requirement 25), so a row must say which rules judged it.';

COMMENT ON COLUMN public.training_jobs.approved_max_epochs IS
    'The ceiling the policy engine approved. epochs_total is what will run. Both '
    'are kept because an over-ceiling request is REFUSED naming both values, never '
    'silently clamped.';

COMMENT ON COLUMN public.training_jobs.min_meaningful_epochs IS
    'The underfitting floor: no training-quality control may stop a run before '
    'this epoch. A promise to the author, not an implementation detail.';

COMMENT ON COLUMN public.training_jobs.estimated_seconds_per_epoch IS
    'AN ESTIMATE from a throughput model, not a measurement. Compare against '
    'actual_duration_seconds / actual_epochs to correct it.';

COMMENT ON COLUMN public.training_jobs.actual_epochs IS
    'Epochs that COMPLETED, which for an early-stopped run is fewer than '
    'epochs_total. Recording the ceiling here instead would make every run look '
    'maximal and hide a too-tight epoch budget.';

COMMENT ON COLUMN public.training_jobs.stopped_reason IS
    'EPOCH_CEILING | NO_IMPROVEMENT | DIVERGED. A COMPLETED status cannot tell '
    '"converged" from "ran out of ceiling"; this can.';

COMMENT ON COLUMN public.training_jobs.dataset_measurements IS
    'MEASURED STATISTICS ONLY - counts, ratios, booleans and column names. NEVER a '
    'label vector, NEVER a feature value, NEVER a prediction. A class COUNT is a '
    'statistic; a per-row class ASSIGNMENT would be the model target. SQL cannot '
    'enforce this: the writer (ml_dataset.DatasetMeasurements.to_dict) does.';

COMMENT ON COLUMN public.training_jobs.dataset_warnings IS
    'Structured data-quality concerns this job was ADMITTED with. Gate codes and '
    'measured quantities only, same minimisation rule as dataset_measurements.';

COMMENT ON TABLE public.training_governance_decisions IS
    'Append-only record of every training admission decision, INCLUDING refusals - '
    'which create no training_jobs row and so can be recorded nowhere else. No '
    'UPDATE or DELETE policy exists: a governance decision is a historical fact.';

COMMENT ON COLUMN public.training_governance_decisions.training_job_id IS
    'Set only on an admission. ON DELETE SET NULL rather than CASCADE: a decision '
    'outlives the job it authorised, so deleting the strategy must not erase the '
    'record that its training was approved.';

-- 5) Grants ------------------------------------------------------------
-- Narrow the default Supabase grants so RLS is not the only thing between an
-- anon key and a user's governance history. Note what is NOT granted on
-- training_governance_decisions: no UPDATE and no DELETE to anybody, including
-- service_role. An append-only table whose rows can be edited is not an audit
-- trail, and the backend has no legitimate reason to rewrite one.
REVOKE ALL ON public.training_governance_decisions FROM anon;

GRANT SELECT, INSERT ON public.training_governance_decisions TO authenticated;
GRANT SELECT, INSERT ON public.training_governance_decisions TO service_role;

-- training_jobs grants are 004d's and are unchanged by this file: the new
-- columns are covered by the existing table-level privileges.

COMMIT;

-- VERIFICATION (run after applying) ------------------------------------
--
-- 1. The twenty governance columns on training_jobs. Expect 20 rows, every one
--    is_nullable = YES and column_default = NULL. A NO or a non-null default
--    means a column was added with a backfill, which would make a default
--    indistinguishable from a decision.
--
--   SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'public' AND table_name = 'training_jobs'
--     AND column_name IN (
--       'policy_version','task','gate_outcome','requested_epochs',
--       'recommended_epochs','approved_max_epochs','min_meaningful_epochs',
--       'max_wall_clock_seconds','estimated_seconds_per_epoch',
--       'estimated_memory_mb','training_budget','dataset_measurements',
--       'dataset_warnings','actual_epochs','actual_duration_seconds',
--       'actual_peak_memory_mb','actual_artifact_bytes','stopped_reason',
--       'best_epoch','best_metric')
--   ORDER BY column_name;
--
-- 2. CHECK constraints on training_jobs. Expect the three from 004d
--    (chk_tj_failed_has_reason, chk_tj_progress, chk_tj_status) PLUS the five
--    this file adds: chk_tj_budget_floor_under_ceiling,
--    chk_tj_gate_outcome, chk_tj_governance_counts_nonneg,
--    chk_tj_stopped_reason, chk_tj_task. Eight rows. Fewer means this file did
--    not fully apply; 004d's three missing means something dropped them.
--
--   SELECT conname, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--   WHERE conrelid = 'public.training_jobs'::regclass AND contype = 'c'
--   ORDER BY conname;
--
-- 3. The new table, its three CHECKs and its two policies.
--
--   SELECT conname, pg_get_constraintdef(oid)
--   FROM pg_constraint
--   WHERE conrelid = 'public.training_governance_decisions'::regclass
--     AND contype = 'c'
--   ORDER BY conname;
--
--   SELECT policyname, cmd, qual, with_check
--   FROM pg_policies
--   WHERE schemaname = 'public'
--     AND tablename = 'training_governance_decisions'
--   ORDER BY policyname;
--
--   -- Expect EXACTLY tgd_owner_select (SELECT) and tgd_owner_insert (INSERT).
--   -- An UPDATE or DELETE policy here would break the append-only guarantee.
--
-- 4. Indexes, and the three PARTIAL predicates. Expect idx_tj_warned
--    (WHERE gate_outcome = 'WARNING'), idx_tj_stopped_reason
--    (WHERE stopped_reason IS NOT NULL) and idx_tgd_refusals
--    (WHERE decision = 'REFUSED') to SHOW their WHERE clauses. A missing
--    predicate is a silent functional change, not a cosmetic one: the index
--    would cover the whole table.
--
--   SELECT tablename, indexname, indexdef
--   FROM pg_indexes
--   WHERE schemaname = 'public'
--     AND tablename IN ('training_jobs','training_governance_decisions')
--   ORDER BY tablename, indexname;
--
-- 5. RLS is on, and the grants are narrow.
--
--   SELECT relname, relrowsecurity
--   FROM pg_class
--   WHERE oid IN ('public.training_jobs'::regclass,
--                 'public.training_governance_decisions'::regclass);
--
--   SELECT grantee, privilege_type
--   FROM information_schema.role_table_grants
--   WHERE table_schema = 'public'
--     AND table_name = 'training_governance_decisions'
--   ORDER BY grantee, privilege_type;
--
--   -- Expect SELECT + INSERT for authenticated and service_role, and NOTHING
--   -- for anon. No UPDATE and no DELETE for anybody.
