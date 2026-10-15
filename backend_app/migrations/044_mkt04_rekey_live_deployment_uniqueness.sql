-- ============================================================================
-- 044_mkt04_rekey_live_deployment_uniqueness.sql
--
-- MKT-04 -- re-key 021's partial unique index from (strategy_id) to
-- (user_id, strategy_id), so a second SUBSCRIBER can deploy a marketplace
-- Listing that is already running for a first subscriber.
--
-- THE DEFECT, AND IT WAS MEASURED, NOT INFERRED.
-- 021_strategy_deployment_live_uniqueness.sql built
-- `uq_strategy_deployments_one_live ON public.strategy_deployments (strategy_id)
-- WHERE status IN (<nine live spellings>)` to stop the OWNER's own deploy call
-- racing itself into two live rows for one strategy. That reasoning is correct
-- for a first-party deployment, where `strategy_id` already uniquely names "the
-- owner". It does not hold for a MARKETPLACE deployment:
-- `backend_app/routers/library.py::deploy_marketplace_strategy` writes a
-- `strategy_deployments` row with `strategy_id = owner_strategy_id` (the
-- Listing's OWNER strategy, shared by every subscriber) and `user_id` = the
-- SUBSCRIBER -- by design, so the subscriber executes the owner's immutable
-- version server-side rather than holding a copy of it (Requirements 7.1, 7.5).
-- So the first subscriber's live deployment occupies the only unique slot
-- `uq_strategy_deployments_one_live` grants for that `strategy_id`, and every
-- OTHER subscriber's `deploy_marketplace_strategy` INSERT fails with
-- `23505 unique_violation` for as long as that first row stays live -- which,
-- for an actively-running paper session, can be indefinitely. A marketplace
-- Listing with more than one concurrent subscriber was unusable by all but the
-- first.
--
-- THE FIX. Re-key the SAME partial index to (user_id, strategy_id), covering
-- the SAME nine live-status spellings. This is a WIDER key, not a different
-- invariant: "at most one live deployment of a given strategy, per owning
-- row's user_id" still refuses the exact race 021 built this index for (two
-- concurrent `deploy` calls from the SAME owner/user on the SAME strategy_id
-- still collide, because user_id is unchanged between them), while two
-- DIFFERENT users' rows for the same shared strategy_id -- a second
-- subscriber's own deployment -- no longer compete for one slot that only
-- ever had one owner in mind.
--
-- SAFE ON EXISTING DATA WITHOUT A PREFLIGHT DUPLICATE CHECK, UNLIKE 021's OWN
-- INDEX. A composite key (user_id, strategy_id) is STRICTLY WIDER than
-- (strategy_id) alone under the identical predicate: any pair of rows that
-- satisfied uniqueness under the narrower key necessarily still satisfies it
-- under the wider one (two rows equal on strategy_id are, a fortiori, not
-- equal on (user_id, strategy_id) unless they also share user_id, which the
-- narrower index already forbade). Widening a unique key can only ADMIT more
-- coexisting rows, never produce a duplicate that did not already exist under
-- the key being replaced. There is therefore nothing for `CREATE UNIQUE INDEX`
-- to choke on here that 021's own preflight did not already rule out.
--
-- PROVISIONING ORDER. Needs 021 (for `public.strategy_deployments` and the
-- index being replaced) and 011_marketplace_deployment_source.sql (for
-- `marketplace_listing_id`, read only in this file's own commentary, not by
-- its SQL). See `backend_app/migrations/PROVISIONING_ORDER.md`.
--
-- IDEMPOTENT. `DROP INDEX IF EXISTS` / `CREATE UNIQUE INDEX IF NOT EXISTS`.
-- Not CONCURRENTLY, for the same reason 021 gives: this needs to run inside
-- the transaction block `scripts/apply_migrations.py` wraps it in, and the
-- table this index covers is small enough (one row per live deployment) that
-- an ordinary `CREATE INDEX`'s brief lock costs nothing of consequence.
-- ============================================================================

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 1 — Preflight. Refuse clearly rather than failing obscurely.
-- ─────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('public.strategy_deployments') IS NULL THEN
        RAISE EXCEPTION
            '044 requires public.strategy_deployments, which 021_strategy_'
            'deployment_live_uniqueness.sql declares the index being replaced '
            'on. Apply 021 (and its own prerequisites) before this file.';
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 2 — Re-key: at most one LIVE deployment per (user_id, strategy_id).
-- ─────────────────────────────────────────────────────────────────────────
DROP INDEX IF EXISTS uq_strategy_deployments_one_live;

CREATE UNIQUE INDEX IF NOT EXISTS uq_strategy_deployments_one_live_per_user
    ON public.strategy_deployments (user_id, strategy_id)
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
          AND indexname = 'uq_strategy_deployments_one_live_per_user'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = 'strategy_deployments'
          AND indexname = 'uq_strategy_deployments_one_live'
    ) THEN
        RAISE NOTICE '044: uq_strategy_deployments_one_live_per_user present, old '
                     'strategy-only index gone — a second subscriber may now deploy '
                     'a marketplace Listing that is already live for a first '
                     'subscriber; the owner-double-deploy race 021 built this index '
                     'for is still refused with 23505 unique_violation.';
    ELSE
        RAISE EXCEPTION '044: re-key did not land as expected.';
    END IF;
END $$;
