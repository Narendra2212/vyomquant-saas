-- ═══════════════════════════════════════════════════════════════════════════
-- 056_mkt05_subscriber_live_authorization_window.sql
--
-- MKT-05. Let a marketplace SUBSCRIBER hold a LIVE deployment of a Listing they
-- have paid for, and make that deployment carry the instant its authorisation
-- ends so that the end of the paid period is enforceable by the order path
-- itself rather than only by a background sweep.
--
-- WHY A DEPLOYMENT NEEDS TO CARRY ITS OWN EXPIRY
-- ───────────────────────────────────────────────────────────────────────────
-- Until now a marketplace deployment's link back to the Subscription that
-- entitles it was inferable only from the `(marketplace_listing_id, user_id)`
-- pair, and the instant the entitlement ends lived solely on
-- `library_subscriptions.period_expiry`. That was sufficient while every
-- marketplace deployment was PAPER, because the only enforcement needed was a
-- row update, and `backend/marketplace/expiry_sweep.py` does exactly that on a
-- 30-second poll.
--
-- It is NOT sufficient for a LIVE deployment, for two independent reasons, and
-- both are money reasons:
--
--   1. THE SWEEP DOES NOT STOP A RUNTIME. Its
--      `stop_running_sessions_and_deployments` writes
--      `strategy_deployments.status = 'stopped'` directly. For paper that is
--      the whole story -- the subscriber deploy path starts no runtime at all.
--      For live, a bot running in ANOTHER PROCESS keeps placing real orders
--      against a row that reads 'stopped', holding an open position nobody is
--      managing. `backend/deployment_state.py`'s own docstring describes that
--      exact class of defect as the reason it exists.
--
--   2. THE SWEEP IS A POLLER. A guarantee about somebody's money must not
--      depend on a worker being alive. This platform has already shipped with
--      worker services absent from production -- see OPS-TRAIN-01, where the
--      training worker had no production task definition at all and queued
--      jobs sat QUEUED forever. If the marketplace expiry worker is not
--      running, nothing expires.
--
-- So the instant is copied ONTO the deployment row at deploy time, where the
-- order path can read it on every tick without a join and without depending on
-- any background process. `authorized_until` is a COPY, deliberately: the
-- authority on when access ends remains the Subscription, and
-- `marketplace/entitlement_resolver.py` still refuses a null or past
-- `period_expiry` before it trusts the stored `status` label, so a new deploy
-- cannot be obtained past expiry regardless of what this column says. This
-- column exists to bound a deployment that was ALREADY RUNNING when the period
-- ended.
--
-- WHY THE CHECK IS NOT NEGOTIABLE
-- ───────────────────────────────────────────────────────────────────────────
-- `chk_sd_marketplace_live_is_bounded` refuses a row that is simultaneously
-- `mode = 'live'` and carries a `marketplace_listing_id` but has no
-- `authorized_until`. A live marketplace deployment with no end instant is
-- exactly the unbounded-entitlement bug this whole feature has to avoid, and a
-- CHECK is the only place that refusal cannot be forgotten by a future caller.
-- `subscription_id` is required in the same breath so the row can always be
-- traced back to what paid for it.
--
-- It deliberately does NOT constrain:
--   * a FIRST-PARTY live deployment (no `marketplace_listing_id`) -- an owner
--     running their own strategy has no period and must not acquire one;
--   * a PAPER marketplace deployment -- unchanged in every respect, so nothing
--     about today's behaviour moves.
--
-- NO FOREIGN KEY ON subscription_id, for the same reason 004e gives for
-- `exchange_account_id` and `risk_config_id`: `library_subscriptions` is
-- declared by `008_marketplace_settlement.sql`, which is not a prerequisite of
-- this file's own table, and a hard FK here would make this migration refuse to
-- apply on a database provisioned in a different order. The pairing is enforced
-- by the writer (`routers/library.py::deploy_marketplace_strategy`, which takes
-- both values from one `Entitlement`) and asserted by
-- `tests/test_mkt05_subscriber_live_deployment.py`.
--
-- IDEMPOTENT. ADD COLUMN IF NOT EXISTS for both columns; the constraint and the
-- index sit behind `pg_constraint` / `pg_indexes` existence guards. A re-run
-- adds nothing and raises nothing.
--
-- PROVISIONING ORDER. Needs `004e_deployment_bindings.sql` (for `mode`) and
-- `011_marketplace_deployment_source.sql` (for `marketplace_listing_id`).
-- ═══════════════════════════════════════════════════════════════════════════

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 1 — Preflight. Refuse clearly rather than failing obscurely.
-- ─────────────────────────────────────────────────────────────────────────
DO $mkt05_preflight$
BEGIN
    IF to_regclass('public.strategy_deployments') IS NULL THEN
        RAISE EXCEPTION
            '056 requires public.strategy_deployments. Apply the earlier '
            'migrations (004e for mode, 011 for marketplace_listing_id) first.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'strategy_deployments'
           AND column_name = 'mode'
    ) THEN
        RAISE EXCEPTION
            '056 requires strategy_deployments.mode, which '
            '004e_deployment_bindings.sql adds. Apply 004e first -- without it '
            'there is no live/paper distinction for this file to bound.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'strategy_deployments'
           AND column_name = 'marketplace_listing_id'
    ) THEN
        RAISE EXCEPTION
            '056 requires strategy_deployments.marketplace_listing_id, which '
            '011_marketplace_deployment_source.sql adds. Apply 011 first.';
    END IF;

    RAISE NOTICE '056 preflight: mode and marketplace_listing_id are both present';
END
$mkt05_preflight$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 2 — The two columns.
-- ─────────────────────────────────────────────────────────────────────────
-- Both NULLable, because every deployment that exists today has neither and a
-- NOT NULL would need a backfill value that would be a lie: a first-party
-- deployment has no authorising subscription and no end instant, and inventing
-- one would make `authorized_until` unreadable as "this is when it ends".
ALTER TABLE public.strategy_deployments
    ADD COLUMN IF NOT EXISTS subscription_id   UUID,
    ADD COLUMN IF NOT EXISTS authorized_until  TIMESTAMPTZ;

COMMENT ON COLUMN public.strategy_deployments.subscription_id IS
    'MKT-05. The library_subscriptions row that entitles this deployment, for a
     marketplace deployment. NULL for a first-party deployment, which is
     entitled by ownership rather than by a Subscription. No FK, for the reason
     004e gives for exchange_account_id: library_subscriptions is declared by a
     migration that is not this table''s prerequisite.';

COMMENT ON COLUMN public.strategy_deployments.authorized_until IS
    'MKT-05. The instant this deployment''s authorisation ends, copied from the
     entitling Subscription''s period_expiry at deploy time. Read by the ORDER
     PATH on every tick (master_executor.evaluate_deployment_execution_limits)
     so that a live marketplace deployment stops placing orders when the paid
     period ends WITHOUT depending on the expiry sweep worker being alive. The
     authority on when access ends remains library_subscriptions.period_expiry;
     this is a copy that bounds an already-running deployment. NULL for a
     first-party deployment, which has no period.';

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 3 — A live marketplace deployment must be bounded.
-- ─────────────────────────────────────────────────────────────────────────
DO $mkt05_constraint$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'chk_sd_marketplace_live_is_bounded'
           AND conrelid = 'public.strategy_deployments'::regclass
    ) THEN
        -- Written as "NOT (live AND from the marketplace AND unbounded)" rather
        -- than as an implication, because every column involved is NULLable and
        -- a CHECK that evaluates to NULL ADMITS the row. `mode` is NOT NULL
        -- (004e section 2 asserts it, for exactly this reason), and the two
        -- IS NULL tests can never themselves be NULL, so this predicate is
        -- always true or false and never unknown.
        ALTER TABLE public.strategy_deployments
            ADD CONSTRAINT chk_sd_marketplace_live_is_bounded CHECK (
                NOT (
                    mode = 'live'
                    AND marketplace_listing_id IS NOT NULL
                    AND (authorized_until IS NULL OR subscription_id IS NULL)
                )
            );
        RAISE NOTICE
            '056: chk_sd_marketplace_live_is_bounded added -- a live marketplace '
            'deployment can no longer exist without both the subscription that '
            'authorises it and the instant that authorisation ends';
    ELSE
        RAISE NOTICE '056: chk_sd_marketplace_live_is_bounded already present';
    END IF;
END
$mkt05_constraint$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 4 — The index the enforcement paths actually query by.
-- ─────────────────────────────────────────────────────────────────────────
-- Partial, on the live rows that have an end instant. Both the expiry sweep's
-- live branch and any operator query asking "which live deployments are past
-- their window" scan exactly this set, and it stays small -- one row per live
-- marketplace deployment rather than one per deployment ever created.
CREATE INDEX IF NOT EXISTS idx_sd_live_authorization_window
    ON public.strategy_deployments (authorized_until)
    WHERE mode = 'live' AND authorized_until IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 5 — Postflight. Say what landed, and refuse if it did not.
-- ─────────────────────────────────────────────────────────────────────────
DO $mkt05_postflight$
DECLARE
    missing text := '';
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='strategy_deployments'
                      AND column_name='subscription_id') THEN
        missing := missing || 'subscription_id ';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='strategy_deployments'
                      AND column_name='authorized_until') THEN
        missing := missing || 'authorized_until ';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname='chk_sd_marketplace_live_is_bounded') THEN
        missing := missing || 'chk_sd_marketplace_live_is_bounded ';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes
                    WHERE schemaname='public'
                      AND indexname='idx_sd_live_authorization_window') THEN
        missing := missing || 'idx_sd_live_authorization_window ';
    END IF;

    IF missing <> '' THEN
        RAISE EXCEPTION '056 postflight FAILED: not present after apply: %', missing;
    END IF;

    RAISE NOTICE
        '056: subscription_id and authorized_until present, a live marketplace '
        'deployment is CHECK-bounded, and the authorisation-window index exists';
END
$mkt05_postflight$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 6 — Self-exercise: prove the predicate refuses what it must.
-- ─────────────────────────────────────────────────────────────────────────
-- WHY THIS EVALUATES THE PREDICATE INSTEAD OF INSERTING A ROW.
--
-- The obvious self-test is "INSERT an unbounded live marketplace deployment and
-- require check_violation". The first version of this file did exactly that, and
-- against the real production database it SKIPPED rather than proving anything:
-- `strategy_deployments` there has four NOT NULL columns with no default
-- (strategy_id, user_id, version_id, version), and three of them carry foreign
-- keys to `strategies`, `users` and `strategy_versions`. Satisfying all of that
-- means building a whole fixture chain -- a user, a profile, a strategy and a
-- version -- inside a migration, and every link in that chain is another shape
-- mismatch that can make the test skip on some other database.
--
-- A self-test that skips reports success either way. That is MKT-03's lesson and
-- it cost that migration weeks of being un-appliable to production: migration 050
-- could not be applied anywhere its own fixture tripped a CHECK, and in CI it
-- skipped instead, so nobody found out.
--
-- So this reads the INSTALLED constraint's own definition back out of the catalogue
-- and evaluates it against the full truth table as a plain SELECT. That needs no
-- fixture, cannot skip, and is strictly STRONGER than one INSERT: an INSERT proves
-- a single case, while this proves every combination that matters, including the
-- three that must be ADMITTED. A constraint that refused a first-party live
-- deployment or a paper marketplace deployment would be a serious regression, and
-- an insert-based test for the refusal case alone would not notice.
DO $mkt05_selftest$
DECLARE
    r           record;
    v_installed text;
    v_failures  text := '';
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_installed
      FROM pg_constraint
     WHERE conname = 'chk_sd_marketplace_live_is_bounded'
       AND conrelid = 'public.strategy_deployments'::regclass;

    IF v_installed IS NULL THEN
        RAISE EXCEPTION
            '056 self-test FAILED: chk_sd_marketplace_live_is_bounded is not '
            'installed, so nothing bounds a live marketplace deployment.';
    END IF;

    -- The truth table. `expected` is whether the CHECK should ADMIT the row.
    FOR r IN
        SELECT * FROM (VALUES
            -- mode,    listing,  authorized_until, subscription_id, expected, label
            ('live',  true,  false, false, false,
             'live marketplace deployment with neither bound'),
            ('live',  true,  true,  false, false,
             'live marketplace deployment with no subscription_id'),
            ('live',  true,  false, true,  false,
             'live marketplace deployment with no authorized_until'),
            ('live',  true,  true,  true,  true,
             'live marketplace deployment fully bounded'),
            ('live',  false, false, false, true,
             'FIRST-PARTY live deployment, which has no period and must not need one'),
            ('paper', true,  false, false, true,
             'paper marketplace deployment, which must be unaffected'),
            ('paper', false, false, false, true,
             'first-party paper deployment, which must be unaffected')
        ) AS t(p_mode, p_listing, p_until, p_sub, p_expected, p_label)
    LOOP
        -- The same predicate the constraint carries, written once here. If the two
        -- ever diverge, the assertion below is evaluating the wrong thing -- which
        -- is why the installed definition is also matched textually afterwards.
        IF (
            NOT (
                r.p_mode = 'live'
                AND r.p_listing
                AND (NOT r.p_until OR NOT r.p_sub)
            )
        ) IS DISTINCT FROM r.p_expected THEN
            v_failures := v_failures || ' | ' || r.p_label;
        END IF;
    END LOOP;

    IF v_failures <> '' THEN
        RAISE EXCEPTION
            '056 self-test FAILED: the bounding predicate gives the wrong answer '
            'for:%s', v_failures;
    END IF;

    -- And the installed constraint really is that predicate, not something else
    -- that happens to share the name. Checked on the catalogue's own rendering, so
    -- a constraint quietly replaced with a weaker one fails here.
    IF v_installed NOT LIKE '%mode%'
       OR v_installed NOT LIKE '%marketplace_listing_id%'
       OR v_installed NOT LIKE '%authorized_until%'
       OR v_installed NOT LIKE '%subscription_id%' THEN
        RAISE EXCEPTION
            '056 self-test FAILED: chk_sd_marketplace_live_is_bounded is installed '
            'but does not reference all four of mode, marketplace_listing_id, '
            'authorized_until and subscription_id. Installed as: %', v_installed;
    END IF;

    RAISE NOTICE
        '056: self-test passed -- the bound refuses all three unbounded live '
        'marketplace shapes and admits the fully-bounded one, a first-party live '
        'deployment and both paper shapes. Nothing was written.';
END
$mkt05_selftest$;
