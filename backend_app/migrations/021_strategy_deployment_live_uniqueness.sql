-- ============================================================================
-- 021_strategy_deployment_live_uniqueness.sql
--
-- production-launch-hardening task 13.23 — Requirement 1.16 / 2.16, the
-- "no double deploy" half, fixed at the database level.
--
-- WHAT THIS FIXES, AND IT WAS MEASURED, NOT INFERRED.
-- Two concurrent `deploy` calls on one strategy both land a live
-- `strategy_deployments` row. Proved twice, in two different ways:
--
--   * Application level: `tests/test_strategy_lifecycle_concurrency.py::
--     test_deploy_can_race_itself_into_a_double_deploy` reproduces two
--     simultaneously-`running` deployments for one strategy in **12 of 12**
--     seeded interleavings. `StrategyService.deploy_version` reads the
--     strategy's archival state once, early, and INSERTs several `await`
--     points later with nothing re-read in between.
--   * Database level: two REAL PostgreSQL sessions released together off a
--     `threading.Barrier`, each running deploy's own read-then-INSERT, both
--     COMMIT and leave two live rows. Recorded in
--     `tests/test_strategy_lifecycle_concurrency_postgres.py`.
--
-- SERIALIZABLE DOES NOT CATCH THIS ONE, and that is why an index is the fix
-- rather than the isolation control task 13.21 built. Re-run at
-- SERIALIZABLE, the same two sessions still **both commit** — two INSERTs of
-- two different rows create no read/write dependency cycle for SSI to find,
-- because neither transaction's write falsifies a predicate the other read.
-- (The *other* half of 2.16, running-but-deleted, is the opposite case: SSI
-- does catch it, with `40001`. See task 13.23 for why that is not taken.)
--
-- WHY A PARTIAL UNIQUE INDEX, AND WHERE THE PATTERN COMES FROM.
-- This is `uq_paper_account_default`'s shape, already in this schema —
-- `CREATE UNIQUE INDEX uq_paper_account_default ON public.paper_accounts
-- (user_id, currency) WHERE (session_id IS NULL)`, from
-- `009_paper_trading.sql` / `013_paper_default_account_children.sql`, which
-- keeps at most one default account per user. `tests/
-- test_strategy_lifecycle_concurrency.py`'s own module docstring names that
-- index as the contrast `strategy_deployments` lacked. It no longer lacks it.
-- The losing INSERT fails with `23505 unique_violation` — a refusal the
-- caller sees, not a silently-accepted second deployment.
--
-- SAFE TO CREATE ON PRODUCTION, AND THAT WAS CHECKED BEFORE IT WAS WRITTEN.
-- `public.strategy_deployments` holds **0 rows** (counted against the live
-- server, 2026-10-03, PostgreSQL 17.6, alongside `public.strategies`' 187).
-- There is therefore no existing duplicate for `CREATE UNIQUE INDEX` to
-- choke on and no table scan of consequence. Had a duplicate existed this
-- file would not have been written: a unique index that cannot be built is
-- not a migration, and reconciling live duplicate deployments is an
-- operational decision, not a schema one.
--
-- THE PREDICATE COVERS ALL NINE LIVE SPELLINGS, NOT THREE.
-- `backend_app/backend/strategy_lifecycle.py`'s `_STATUS_TO_BINDING_STATE`
-- maps nine distinct `status` spellings onto the three live binding states
-- in `STOPPABLE_BINDING_STATES` (DEPLOYING, RUNNING, PAUSED):
--
--   DEPLOYING  <- deploying, deployed, pending, queued, starting, restarting
--   RUNNING    <- running, active
--   PAUSED     <- paused
--
-- A predicate naming only ('deploying','running','paused') would leave six
-- legacy spellings outside the index, so a writer using any of them could
-- still land a second live row. The nine are enumerated here and pinned
-- against that dict by
-- `tests/test_strategy_lifecycle_concurrency_postgres.py::
-- test_the_index_predicate_covers_every_live_status_spelling`, so the two
-- cannot drift apart.
--
-- `stopped`, `stopping`, `cancelled`, `canceled`, `completed`, `failed`,
-- `error` and `crashed` are deliberately OUTSIDE the predicate: a strategy
-- may accumulate any number of finished deployments, and STOPPED is terminal
-- by design (a stop is never restarted — a new deployment row is created,
-- because execution references an immutable `version_id`). Constraining
-- those would break the normal lifecycle.
--
-- PROVISIONING ORDER. This file needs `public.strategy_deployments`, which
-- `001_strategy_architecture.sql` declares and which in turn needs
-- `public.strategies` from `020_declare_pre_existing_tables.sql`. So the
-- order is 020, then 001, then this. See
-- `backend_app/migrations/PROVISIONING_ORDER.md`.
--
-- IDEMPOTENT. `IF NOT EXISTS` on the index; the second run is a notice.
-- No `COMMENT ON` — production carries no comment on this table and adding
-- one would be a write against the live catalogue for no behavioural gain
-- (020's header states the same reasoning).
--
-- WHAT THIS FILE DOES NOT FIX, STATED HERE SO A READER IS NOT MISLED.
-- The running-but-deleted half of 2.16 — a strategy archived while one of
-- its deployments is still live — is **NOT** addressed. It is an invariant
-- spanning two tables (`strategies.archived_at` and
-- `strategy_deployments.status`), and a unique index cannot span tables.
-- Task 13.23 records it as a proven P0 defect with its counterexample and
-- the three candidate mechanisms, none of which is bounded enough to land
-- alongside this one.
-- ============================================================================

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 1 — Preflight. Refuse clearly rather than failing obscurely.
-- ─────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('public.strategy_deployments') IS NULL THEN
        RAISE EXCEPTION
            '021 requires public.strategy_deployments, which is declared by '
            '001_strategy_architecture.sql (itself requiring public.strategies '
            'from 020_declare_pre_existing_tables.sql). Apply 020, then 001, '
            'then this file. See backend_app/migrations/PROVISIONING_ORDER.md.';
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 2 — At most one LIVE deployment per strategy.
--
-- Not CONCURRENTLY: the table is empty in production, so the brief lock an
-- ordinary CREATE INDEX takes costs nothing, and CONCURRENTLY cannot run
-- inside a transaction block, which would make this file unusable through
-- `scripts/apply_migrations.py`.
-- ─────────────────────────────────────────────────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS uq_strategy_deployments_one_live
    ON public.strategy_deployments (strategy_id)
    WHERE status IN (
        -- DEPLOYING
        'deploying', 'deployed', 'pending', 'queued', 'starting', 'restarting',
        -- RUNNING
        'running', 'active',
        -- PAUSED
        'paused'
    );

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 3 — Say what landed, so a hand-applied run leaves a trace.
-- ─────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = 'strategy_deployments'
          AND indexname = 'uq_strategy_deployments_one_live'
    ) THEN
        RAISE NOTICE '021: uq_strategy_deployments_one_live present — at most one '
                     'live deployment per strategy is now enforced by the database. '
                     'A racing second deploy fails with 23505 unique_violation.';
    ELSE
        RAISE EXCEPTION '021: uq_strategy_deployments_one_live was not created.';
    END IF;
END $$;
