-- ==========================================================================
-- 017_plan_entitlements.sql — the VyomQuant plan ladder's persistence
-- ==========================================================================
--
-- WHAT THIS MIGRATION ADDS, AND WHAT IT DELIBERATELY DOES NOT TOUCH
-- ----------------------------------------------------------------
-- The five-tier ladder (Free / Trader / Pro Quant / Business / Enterprise) needs three things
-- the schema does not yet hold:
--
--   1. profiles.billing_interval      — which cycle a subscriber is on. There was no annual
--                                       billing before this change, so there was nothing to
--                                       record; Stripe's interval was hardcoded "month".
--   2. profiles.plan_limit_overrides  — the contracted capacity of a custom Enterprise account.
--                                       Without it, the custom tier would have to mean
--                                       "unlimited", which is a promise the platform does not
--                                       implement. With it, an Enterprise agreement is a set of
--                                       NUMBERS an operator records, and an unprovisioned
--                                       account falls back to Business capacity rather than to
--                                       no ceiling at all.
--   3. plan_usage_ledger              — the durable record behind the monthly meters for
--                                       backtests, optimization runs and ML training runs.
--
-- NOTHING EXISTING IS RENAMED, DROPPED OR REWRITTEN. In particular:
--
--   * profiles.subscription_tier keeps every value it holds. The Business tier reuses the
--     historic `enterprise` id precisely so that no row has to be rewritten and no Razorpay or
--     Stripe subscription has to be re-pointed — see core/subscription_engine.py's module
--     docstring for why renaming it was rejected. There is no UPDATE against profiles here.
--   * billing_invoices, library_subscriptions and marketplace_settlements are untouched, so
--     payment history and the 90/10 settlement ledger carry over exactly as they are.
--   * Both new profiles columns are NULLABLE with no default, so every existing row stays valid
--     and no backfill runs. NULL billing_interval means "cycle not recorded", which is the
--     truth for every subscription taken before annual billing existed — it is NOT silently
--     read as monthly.
--
-- WHY THE LEDGER EXISTS WHEN REDIS ALREADY COUNTS
-- -----------------------------------------------
-- The hot counter for a monthly meter is a Redis key scoped by period
-- (`quota:{user}:{resource}:{YYYY-MM}`), because a reservation has to be atomic and must not
-- cost a database round trip on every backtest. But Redis is a cache: flush it, evict it or
-- replace the node and every account silently receives a fresh month. So each successful
-- reservation also appends a row here, and core/usage_ledger.py REBUILDS the Redis counter from
-- this table whenever the key is missing. The table is the record; Redis is the fast path.
--
-- `idempotency_key` is what makes a retry safe. A client that retries, or a worker that
-- redelivers, presents the same key and the unique index refuses the second insert — so one
-- piece of work cannot be billed to the account twice.
--
-- Idempotent throughout: every statement is guarded, so re-running this file changes nothing.
-- ==========================================================================

BEGIN;

-- ==========================================================================
-- SECTION 0 — Preflight
-- ==========================================================================
-- `profiles` is created in Supabase rather than in this repository's migrations, so its
-- existence is asserted rather than assumed. A RAISE naming the missing relation is a readable
-- failure; an ALTER against a non-existent table is a 42P01 with no context.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'profiles'
    ) THEN
        RAISE EXCEPTION
            'public.profiles does not exist. It is the authoritative store for '
            'subscription_tier and is provisioned in Supabase, not by this migration set. '
            'Create it before running 017_plan_entitlements.sql.';
    END IF;
END $$;

-- ==========================================================================
-- SECTION 1 — profiles.billing_interval
-- ==========================================================================
-- NULLABLE with no default. A subscription taken before annual billing existed has no recorded
-- cycle, and defaulting it to 'month' would assert a fact about thousands of rows that this
-- migration cannot know. `_apply_billing_entitlement` writes the column only when the gateway
-- metadata actually carries an interval, so historic rows stay NULL until their next renewal.
ALTER TABLE public.profiles
    ADD COLUMN IF NOT EXISTS billing_interval TEXT;

-- The vocabulary, guarded on pg_constraint by name so a re-run cannot raise 42710. NULL passes a
-- CHECK in PostgreSQL, which is what keeps every existing row valid.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.profiles'::regclass
           AND conname  = 'chk_profiles_billing_interval'
    ) THEN
        ALTER TABLE public.profiles
            ADD CONSTRAINT chk_profiles_billing_interval
            CHECK (billing_interval IS NULL OR billing_interval IN ('month', 'year'));
        RAISE NOTICE 'Added chk_profiles_billing_interval to public.profiles.';
    END IF;
END $$;

COMMENT ON COLUMN public.profiles.billing_interval IS
    'The billing cycle of the current subscription: ''month'', ''year'', or NULL when the cycle '
    'was never recorded (every subscription predating annual billing). NULL is NOT monthly — a '
    'reader that needs a cycle must treat NULL as unknown. Written only by '
    'routers/billing.py::_apply_billing_entitlement, from the gateway metadata.';

-- ==========================================================================
-- SECTION 2 — profiles.plan_limit_overrides
-- ==========================================================================
-- The contracted capacity of a custom Enterprise account, as
-- {"strategies": 60, "bots": 60, "ml_models": 40}. Read by
-- SubscriptionEngine.get_effective_quotas, which honours an override only UPWARD: a value below
-- the plan's own limit is logged and ignored, so this column cannot silently downgrade a paying
-- account. That rule lives in application code rather than in a CHECK because the floor it
-- compares against is the plan catalogue, which the database does not hold.
ALTER TABLE public.profiles
    ADD COLUMN IF NOT EXISTS plan_limit_overrides JSONB;

-- An object, or nothing. A scalar or an array here would be silently ignored by the reader
-- (which requires a Mapping), and silently ignored is the wrong outcome for a column that
-- encodes a commercial agreement — so the shape is refused at the door instead.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.profiles'::regclass
           AND conname  = 'chk_profiles_plan_limit_overrides_object'
    ) THEN
        ALTER TABLE public.profiles
            ADD CONSTRAINT chk_profiles_plan_limit_overrides_object
            CHECK (plan_limit_overrides IS NULL OR jsonb_typeof(plan_limit_overrides) = 'object');
        RAISE NOTICE 'Added chk_profiles_plan_limit_overrides_object to public.profiles.';
    END IF;
END $$;

COMMENT ON COLUMN public.profiles.plan_limit_overrides IS
    'Per-account contractual capacity for the custom Enterprise tier, as resource -> integer '
    'limit. Honoured only UPWARD by SubscriptionEngine.get_effective_quotas: a value below the '
    'plan''s own limit is ignored, so this column can raise a contracted account''s capacity but '
    'can never downgrade a paying one. NULL means the account runs on its plan''s catalogue '
    'limits; an Enterprise account with no override falls back to Business capacity rather than '
    'to an uncapped one.';

-- ==========================================================================
-- SECTION 3 — plan_usage_ledger
-- ==========================================================================
-- One row per reservation of a METERED resource. Append-mostly: the only deletion is the narrow
-- release path in core/usage_ledger.py::release, for a reservation whose work never started.
--
--   period            the YYYY-MM usage period, computed once by
--                     subscription_engine.usage_period() in a configurable timezone
--                     (PLAN_USAGE_TIMEZONE, UTC by default) so every writer and reader agrees
--                     where the month boundary is.
--   amount            units reserved, >= 1. A row recording zero consumption is not a record of
--                     anything.
--   idempotency_key   globally unique. A retry presents the same key and is refused by the
--                     index, which is what stops one backtest counting twice.
--   context           what the reservation was for (strategy id, job id). Diagnostic; nothing
--                     branches on it.
CREATE TABLE IF NOT EXISTS public.plan_usage_ledger (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    resource        TEXT NOT NULL,
    period          TEXT NOT NULL,
    amount          INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT NOT NULL,
    context         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_pul_amount_positive CHECK (amount >= 1),
    CONSTRAINT chk_pul_period_shape CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    CONSTRAINT uq_pul_idempotency UNIQUE (idempotency_key)
);

-- 3a) Shape assertion ------------------------------------------------------
-- CREATE TABLE IF NOT EXISTS is silent about a pre-existing table of the same name and a
-- different shape, so the columns the reader depends on are asserted here. A mismatch fails with
-- a readable message now instead of at the first reservation in production.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    missing TEXT;
BEGIN
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('id'), ('user_id'), ('resource'), ('period'), ('amount'),
                   ('idempotency_key'), ('context'), ('created_at')) AS expected(column_name)
     WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns c
         WHERE c.table_schema = 'public'
           AND c.table_name   = 'plan_usage_ledger'
           AND c.column_name  = expected.column_name
     );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            'public.plan_usage_ledger exists but is missing column(s): %. A pre-existing table '
            'of that name has a different shape; reconcile it by hand before re-running this '
            'migration.', missing;
    END IF;
END $$;

-- 3b) Constraint guards ----------------------------------------------------
-- No-ops on a fresh run: all three were declared inline above. Present so a table created
-- earlier WITHOUT them gains them, rather than silently keeping the invariants unenforced.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.plan_usage_ledger'::regclass
           AND conname  = 'chk_pul_amount_positive'
    ) THEN
        ALTER TABLE public.plan_usage_ledger
            ADD CONSTRAINT chk_pul_amount_positive CHECK (amount >= 1);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.plan_usage_ledger'::regclass
           AND conname  = 'chk_pul_period_shape'
    ) THEN
        ALTER TABLE public.plan_usage_ledger
            ADD CONSTRAINT chk_pul_period_shape
            CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.plan_usage_ledger'::regclass
           AND conname  = 'uq_pul_idempotency'
    ) THEN
        ALTER TABLE public.plan_usage_ledger
            ADD CONSTRAINT uq_pul_idempotency UNIQUE (idempotency_key);
    END IF;
END $$;

-- 3c) Indexes --------------------------------------------------------------
-- The one query the reader makes is "sum amount for this user, this resource, this period", so
-- the composite index matches it exactly and the rebuild is a single index scan rather than a
-- per-user table scan. `created_at DESC` serves support and audit reads.
CREATE INDEX IF NOT EXISTS idx_pul_user_resource_period
    ON public.plan_usage_ledger (user_id, resource, period);
CREATE INDEX IF NOT EXISTS idx_pul_created
    ON public.plan_usage_ledger (created_at DESC);

-- 3d) Row level security ---------------------------------------------------
-- Enabled first, which is default-deny, so the table is unreachable rather than open between
-- these statements — and every statement is inside the transaction, so no session observes the
-- intermediate state.
ALTER TABLE public.plan_usage_ledger ENABLE ROW LEVEL SECURITY;

-- SELECT only for the owner. There is deliberately no owner INSERT, UPDATE or DELETE policy: a
-- usage record is written by the backend's service role as part of a reservation it controls. If
-- a user could insert here they could not inflate their own usage to any advantage, but if they
-- could DELETE they could erase their month — so the capability simply does not exist for them.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'plan_usage_ledger'
           AND policyname = 'pul_owner_select'
    ) THEN
        CREATE POLICY pul_owner_select ON public.plan_usage_ledger
            FOR SELECT USING (user_id = auth.uid());
    END IF;
END $$;

GRANT SELECT ON public.plan_usage_ledger TO authenticated;
GRANT SELECT, INSERT, DELETE ON public.plan_usage_ledger TO service_role;
REVOKE INSERT, UPDATE, DELETE ON public.plan_usage_ledger FROM anon, authenticated;

COMMENT ON TABLE public.plan_usage_ledger IS
    'Durable record of monthly metered consumption (backtests, optimization runs, ML training '
    'runs). The Redis counter at quota:{user}:{resource}:{period} is the fast path over this '
    'table; core/usage_ledger.py rebuilds that counter from here whenever the key is absent, so '
    'a cache flush cannot hand every account a fresh month. One row per reservation, keyed by '
    'idempotency_key so a retried request cannot be counted twice.';

COMMENT ON COLUMN public.plan_usage_ledger.period IS
    'The YYYY-MM usage period, from subscription_engine.usage_period(). The period is part of the '
    'key rather than a column that gets reset, so a new month is a new key holding zero and no '
    'scheduled reset job is required.';

-- ==========================================================================
-- SECTION 4 — Postconditions
-- ==========================================================================
-- Everything this file promised, asserted inside the same transaction. A missing object aborts
-- the migration instead of leaving a half-applied schema that the application would discover at
-- its first reservation.
DO $$
DECLARE
    problems TEXT;
BEGIN
    SELECT string_agg(expected.what, ', ' ORDER BY expected.what)
      INTO problems
      FROM (VALUES
                ('column profiles.billing_interval'),
                ('column profiles.plan_limit_overrides')
           ) AS expected(what)
     WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns c
         WHERE c.table_schema = 'public'
           AND c.table_name   = 'profiles'
           AND ('column profiles.' || c.column_name) = expected.what
     );

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION '017 postcondition failed — missing: %', problems;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'plan_usage_ledger'
    ) THEN
        RAISE EXCEPTION '017 postcondition failed — public.plan_usage_ledger was not created.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public'
           AND tablename  = 'plan_usage_ledger'
           AND indexname  = 'idx_pul_user_resource_period'
    ) THEN
        RAISE EXCEPTION
            '017 postcondition failed — idx_pul_user_resource_period is absent, so the meter '
            'rebuild would table-scan.';
    END IF;

    RAISE NOTICE '017_plan_entitlements.sql applied: billing_interval, plan_limit_overrides, '
                 'plan_usage_ledger (+2 indexes, RLS).';
END $$;

COMMIT;
