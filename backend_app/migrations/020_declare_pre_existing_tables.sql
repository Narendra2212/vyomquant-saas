-- ==========================================================================
-- 020_declare_pre_existing_tables.sql — the three tables that NO migration
--                                      and NO Alembic revision in this
--                                      repository declares
-- ==========================================================================
--
-- WHAT THIS MIGRATION RECONCILES, AND WHY IT IS THE LAUNCH BLOCKER
-- ----------------------------------------------------------------
-- public.profiles, public.strategies and public.processed_orders exist in the
-- production database and are declared by NOTHING in this checkout. Parsing
-- CREATE TABLE out of backend_app/migrations/*.sql + migrations/*.sql yields
-- 64 tables, and op.create_table(...) out of backend_app/alembic/versions/*.py
-- yields 19; differencing that union against production's pg_tables leaves
-- four names, of which `alembic_version` is Alembic's own bookkeeping table
-- and the other three are these.
--
-- The consequence, recorded by task 13.21 and only partly fixable here: THIS
-- REPOSITORY CANNOT PROVISION ITS OWN DATABASE. There is no disaster-recovery
-- rebuild and no way to stand up staging from source. Production's 71 `public`
-- tables exist only because they were applied out-of-band. `profiles` is also
-- the specific name that makes the Alembic chain unrunnable:
-- alembic/versions/add_foreign_keys_20260817.py adds
-- `FOREIGN KEY (tenant_id) REFERENCES profiles(id)`, and with no `profiles`
-- that statement fails, aborts the transaction, and takes Alembic's own
-- `UPDATE alembic_version` down with it (`InFailedSqlTransaction`). Declaring
-- `profiles` here is what unblocks that revision.
--
-- See backend_app/migrations/PROVISIONING_ORDER.md for the order in which the
-- numbered SQL set and the Alembic chain have to be applied, and for the two
-- obstacles that remain after this file.
--
-- HOW THE DDL BELOW WAS OBTAINED — CAPTURED, NOT WRITTEN FROM KNOWLEDGE
-- ---------------------------------------------------------------------
-- Every column name, ordinal position, type, length/precision, nullability
-- and default below was READ OFF THE PRODUCTION CATALOGUE on a read-only
-- session (PostgreSQL 17.6, 71 base tables in `public`), not reconstructed
-- from application code and not taken from the audit documents. The queries
-- were:
--
--   columns      information_schema.columns  (column_name, ordinal_position,
--                data_type, udt_name, character_maximum_length,
--                numeric_precision, numeric_scale, datetime_precision,
--                is_nullable, column_default, is_identity)
--                WHERE table_schema = 'public' AND table_name = <t>
--                ORDER BY ordinal_position
--   constraints  pg_constraint JOIN pg_class JOIN pg_namespace, projecting
--                conname, contype and pg_get_constraintdef(oid) — so every
--                PRIMARY KEY / UNIQUE / FOREIGN KEY / CHECK below is
--                PostgreSQL's own rendering of the live constraint
--   indexes      pg_indexes.indexdef WHERE schemaname = 'public'
--   policies     pg_policies (policyname, cmd, permissive, roles, qual,
--                with_check)
--   triggers     pg_trigger WHERE NOT tgisinternal — none of the three has
--                any, so this file declares none
--
-- Observed shape at capture time, stated so a reader can re-check it:
-- profiles 20 columns / 5 constraints / 4 indexes / 4 policies / 181 rows;
-- strategies 26 / 3 / 6 / 1 / 187 rows;
-- processed_orders 3 / 2 / 1 / 1 / 25 rows. RLS is ENABLED on all three and
-- FORCED on none.
--
-- NOTE ON processed_orders, because the audit documents are not a source.
-- PHASE_7A_AUTH_FORENSIC_AUDIT.md and TENANT_ISOLATION_AUDIT_REPORT.md both
-- record it as RLS-enabled, and both imply a user_id column. The catalogue
-- confirms a user_id — but the table has only THREE columns and its primary
-- key is `order_id`, not a surrogate `id`. The shape below is the catalogue's.
--
-- THIS FILE IS A NO-OP AGAINST PRODUCTION, BY CONSTRUCTION
-- --------------------------------------------------------
-- Production already has all three tables, all their indexes and all their
-- policies, so every statement here is guarded and every guard is satisfied:
-- CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, and — because
-- PostgreSQL has no CREATE POLICY IF NOT EXISTS — a pg_policies existence
-- check per policy, the same pattern 018 section 4, 017 section 3d and 008
-- section 6 use. DROP POLICY IF EXISTS ... CREATE POLICY is deliberately NOT
-- used: it would open a window in which a live, populated table is
-- policy-less.
--
-- Nothing here ALTERs, DROPs or rewrites an existing object. In particular
-- there is deliberately no COMMENT ON: none of the three tables carries a
-- comment in production, so a COMMENT ON TABLE would be a WRITE against the
-- live catalogue, which this file must not perform. The prose lives in this
-- header instead.
--
-- WHY THE TWO FOREIGN KEYS ARE ADDED SEPARATELY AND GUARDED
-- ---------------------------------------------------------
-- Production's constraint definitions reference two relations the numbered
-- SQL set does not own:
--
--   profiles.id          -> auth.users(id) ON DELETE CASCADE
--   strategies.user_id   -> auth.users(id)
--   processed_orders.user_id -> auth.users(id)
--   strategies.source_library_id -> library_strategies(id) ON DELETE SET NULL
--
-- `auth.users` belongs to the Supabase platform. `library_strategies` is
-- declared ONLY by Alembic (e88f9911b5a2), so on a from-source rebuild it does
-- not exist until the Alembic chain has run. Writing these inline in CREATE
-- TABLE would make the whole file abort on a database where a referent is
-- absent. They are therefore added by guarded ALTER TABLE ... ADD CONSTRAINT:
-- the guard checks to_regclass on the referent and the absence of the
-- constraint, and RAISEs a NOTICE naming the skipped constraint when a
-- referent is missing — loudly, so a rebuild that produced a weaker schema
-- says so in its own log rather than looking clean.
--
-- GRANTS ARE DELIBERATELY NOT RESTATED HERE — read this before adding them
-- ------------------------------------------------------------------------
-- All three tables carry the identical privilege shape in production
-- (anon, authenticated, postgres and service_role each hold
-- SELECT/INSERT/UPDATE/DELETE/REFERENCES/TRIGGER/TRUNCATE), and so do 44 of
-- the 71 `public` tables. That is Supabase's schema-level default privilege
-- set, not a per-table decision, which is why this file does not re-declare
-- it: emitting GRANTs would turn a platform default into a claim this
-- repository owns, and emitting the REVOKEs that would narrow it (anon holds
-- INSERT/UPDATE/DELETE on `profiles` today) would CHANGE production, which
-- this migration must not do. RLS is what constrains those grants, and the
-- RLS is reproduced below exactly as measured. Narrowing anon's privileges on
-- these three tables is a real and separate decision; it does not belong in a
-- file whose entire purpose is to be a no-op.
--
-- WHAT THIS FILE DOES NOT DECLARE, AND WHY NOT
-- --------------------------------------------
-- Sixteen further tables are declared in backend_app/alembic/versions/ and by
-- no SQL migration: dag_tasks, execution_records, fills, idempotency_keys,
-- invoices, library_ratings, library_strategies, orders, payment_methods,
-- positions, reconciliation_mismatches, referrals, subscriptions,
-- transaction_checkpoints, transaction_records, transaction_rollbacks. They
-- are NOT re-declared here on purpose. Two declarations of one table is the
-- divergence 016's header warns about and the one task 13.15 found between
-- 006_reconcile_production_database.sql and referral_system_redesign.sql over
-- `referrals` — which, note, is declared in Alembic and is ABSENT from
-- production, a different defect from the three tables in this file. Those
-- sixteen stay Alembic's; PROVISIONING_ORDER.md says so and says in what
-- order.
--
-- A DISAGREEMENT WORTH RECORDING: rls_migration.sql WAS NEVER APPLIED
-- -------------------------------------------------------------------
-- The repo-root rls_migration.sql is the only other file in this tree that
-- claims RLS for these tables, and the catalogue contradicts it. It creates
-- `profiles_authenticated_owner`, `strategies_authenticated_owner` and
-- `processed_orders_authenticated_owner`; production has NONE of those three
-- names. What production actually has is reproduced below:
-- `Select own profile`, `Update own profile`, `profiles_owner_access`,
-- `profiles_service_role`, `Manage own strategies` and
-- `Manage own processed orders`. It also creates
-- `idx_strategies_user_id` and `idx_processed_orders_user_id`, and production
-- has NEITHER (`strategies` has a composite `idx_strategies_user_status`
-- instead, and `processed_orders` has only its primary key index). Of
-- everything that file would create, only `idx_profiles_id` is present. So
-- rls_migration.sql records an intent that was never applied, and this file
-- reproduces the measured state rather than that intent.
--
-- IDEMPOTENCY
-- -----------
-- Re-running this file changes nothing, on production or on a fresh database.
-- Proven by applying it twice inside one transaction against a throwaway
-- schema on the production server (search_path scoped to that schema,
-- `public` never addressed) and comparing the resulting catalogue column by
-- column against `public`'s.
-- ==========================================================================

BEGIN;

-- ==========================================================================
-- SECTION 0 — Preflight
-- ==========================================================================
-- The policies below name the Supabase roles `authenticated` and
-- `service_role` and call auth.uid(). On a database without them, CREATE
-- POLICY fails with a bare undefined_object / undefined_function and no
-- context. Fail with a readable message instead — the same read-only
-- assertion 018 section 0 and 004b section 0 make.
DO $$
DECLARE
    missing TEXT := '';
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        missing := missing || ' role "authenticated";';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        missing := missing || ' role "service_role";';
    END IF;
    IF to_regprocedure('auth.uid()') IS NULL THEN
        missing := missing || ' function auth.uid();';
    END IF;

    IF missing <> '' THEN
        RAISE EXCEPTION
            '020 precondition failed, missing:%. This migration targets a '
            'Supabase database, where anon, authenticated, service_role and '
            'the auth schema are created by the platform. The row level '
            'security reproduced here is the production policy set and '
            'cannot be expressed without them.', missing;
    END IF;
END $$;

-- ==========================================================================
-- SECTION 1 — public.profiles
-- ==========================================================================
-- 20 columns, in production's ordinal order. The ordering is not cosmetic:
-- `email`, `full_name` and `updated_at` sit at positions 15-17, AFTER
-- `is_frozen` and `available_discounts`, which is the fingerprint of a table
-- grown by ALTER rather than declared once. Reproducing the order keeps a
-- rebuilt database's catalogue byte-comparable with production's.
--
-- `balance` is unconstrained NUMERIC — the catalogue reports no precision and
-- no scale — with DEFAULT 0.00. It is NOT numeric(18,2); do not "tidy" it, a
-- precision would start rejecting values this column accepts today.
--
-- `available_discounts` and `max_api_slots` are the only two NOT NULL columns
-- besides the primary key, and both carry defaults (0 and 1). Every other
-- column is nullable, including `email` — so nothing here may assume a
-- profile has an address.
--
-- The FOREIGN KEY on `id` is added in section 4; see the header.
CREATE TABLE IF NOT EXISTS public.profiles (
    id                   UUID        NOT NULL,
    username             TEXT,
    display_name         TEXT,
    avatar_url           TEXT,
    bio                  TEXT,
    telegram_id          TEXT,
    created_at           TIMESTAMPTZ          DEFAULT now(),
    balance              NUMERIC              DEFAULT 0.00,
    subscription_tier    TEXT                 DEFAULT 'free'::text,
    deployed_bots        INTEGER              DEFAULT 0,
    ml_strategies_built  INTEGER              DEFAULT 0,
    ml_addons_purchased  INTEGER              DEFAULT 0,
    is_frozen            BOOLEAN              DEFAULT false,
    available_discounts  INTEGER     NOT NULL DEFAULT 0,
    email                TEXT,
    full_name            TEXT,
    updated_at           TIMESTAMPTZ          DEFAULT now(),
    max_api_slots        INTEGER     NOT NULL DEFAULT 1,
    preferred_currency   TEXT                 DEFAULT 'USD'::text,
    billing_currency     TEXT                 DEFAULT 'USD'::text,
    CONSTRAINT profiles_pkey PRIMARY KEY (id),
    CONSTRAINT profiles_username_key UNIQUE (username),
    CONSTRAINT profiles_preferred_currency_check
        CHECK ((preferred_currency = ANY (ARRAY['USD'::text, 'INR'::text]))),
    CONSTRAINT profiles_billing_currency_check
        CHECK ((billing_currency = ANY (ARRAY['USD'::text, 'INR'::text])))
);

-- idx_profiles_id duplicates the unique index profiles_pkey creates
-- implicitly. It is reproduced because production HAS it (created by
-- rls_migration.sql phase 4, the one statement from that file that did land),
-- and this migration's job is to match the catalogue, not to improve it.
CREATE INDEX IF NOT EXISTS idx_profiles_id
    ON public.profiles (id);
CREATE INDEX IF NOT EXISTS idx_profiles_preferred_currency
    ON public.profiles (preferred_currency);

-- ==========================================================================
-- SECTION 2 — public.strategies
-- ==========================================================================
-- 26 columns. `current_version` and `environment` are varchar(20), not TEXT —
-- the only two length-limited columns across all three tables, and the limit
-- is real: 'paper' and 'live' fit, a longer environment name would not.
--
-- The six JSONB columns split into two groups by default, which is load
-- bearing: buy_logic / sell_logic / risk default to '{}' and indicators to
-- '[]', so a row always has a container to read; dag_config,
-- backtest_result and execution_order have NO default and are NULL until
-- written. Code that reads the first group may assume a shape; code that
-- reads the second may not.
--
-- This is the table tests/test_strategy_lifecycle_concurrency.py needs and
-- could not have, which is why task 12.6 and clause 1.16's
-- database-level-serialisation half were carried BLOCKED: no migration
-- declared it. That gap closes here.
CREATE TABLE IF NOT EXISTS public.strategies (
    id                  UUID        NOT NULL DEFAULT gen_random_uuid(),
    user_id             UUID        NOT NULL,
    name                TEXT        NOT NULL,
    symbol              TEXT        NOT NULL,
    timeframe           TEXT                 DEFAULT '5m'::text,
    buy_logic           JSONB                DEFAULT '{}'::jsonb,
    sell_logic          JSONB                DEFAULT '{}'::jsonb,
    risk                JSONB                DEFAULT '{}'::jsonb,
    indicators          JSONB                DEFAULT '[]'::jsonb,
    ml_model_path       TEXT,
    exchange_id         TEXT                 DEFAULT 'binance'::text,
    status              TEXT                 DEFAULT 'stopped'::text,
    created_at          TIMESTAMPTZ          DEFAULT now(),
    updated_at          TIMESTAMPTZ          DEFAULT now(),
    source_library_id   UUID,
    backtest_result     JSONB,
    current_version     VARCHAR(20)          DEFAULT 'v1.0'::character varying,
    environment         VARCHAR(20)          DEFAULT 'paper'::character varying,
    is_active           BOOLEAN     NOT NULL DEFAULT true,
    dag_config          JSONB,
    dag_hash            TEXT,
    dag_version         INTEGER              DEFAULT 1,
    dag_schema_version  TEXT,
    execution_order     JSONB,
    last_signal_at      TIMESTAMPTZ,
    archived_at         TIMESTAMPTZ,
    CONSTRAINT strategies_pkey PRIMARY KEY (id)
);

-- All five secondary indexes are pg_indexes' own indexdef, including the two
-- PARTIAL ones. The predicates are the point: idx_strategies_archived_at
-- indexes only live rows (archived_at IS NULL), which is the filter
-- 005a_strategy_archive.sql's reads use, and idx_strategies_dag_hash skips
-- the strategies that have no DAG. A full index on either column would be
-- larger and would not serve those queries as well.
CREATE INDEX IF NOT EXISTS idx_strategies_archived_at
    ON public.strategies (archived_at) WHERE (archived_at IS NULL);
CREATE INDEX IF NOT EXISTS idx_strategies_current_version
    ON public.strategies (current_version);
CREATE INDEX IF NOT EXISTS idx_strategies_dag_hash
    ON public.strategies (dag_hash) WHERE (dag_hash IS NOT NULL);
CREATE INDEX IF NOT EXISTS idx_strategies_environment
    ON public.strategies (environment);
CREATE INDEX IF NOT EXISTS idx_strategies_user_status
    ON public.strategies (user_id, status);

-- ==========================================================================
-- SECTION 3 — public.processed_orders
-- ==========================================================================
-- THREE columns, and the primary key is `order_id` — there is no surrogate
-- `id`. This is an idempotency ledger: one row per order that has already
-- been handled, so the PK on order_id IS the duplicate-suppression mechanism.
-- 25 rows in production.
--
-- It is declared by nothing and READ by almost nothing: there is no
-- `.table("processed_orders")` call site anywhere under backend_app/. It is
-- referenced by rls_migration.sql (which, per the header, never landed) and
-- counted by backend_app/validate_paper_trading.py. It is nonetheless LIVE
-- DATA under RLS, so it is declared here rather than left for a rebuild to
-- discover it is missing.
--
-- `user_id` is NULLABLE. That is the catalogue's answer, not a choice made
-- here, and it has a consequence for the policy in section 5: a row with a
-- NULL user_id satisfies `auth.uid() = user_id` for nobody, so it is
-- invisible to every browser-side caller and reachable only by service_role.
-- Flagged rather than fixed — adding NOT NULL would be an ALTER against a
-- populated production table, which this file may not do.
--
-- The only index is the primary key's. In particular there is NO
-- idx_processed_orders_user_id, despite rls_migration.sql creating one.
CREATE TABLE IF NOT EXISTS public.processed_orders (
    order_id      UUID        NOT NULL,
    user_id       UUID,
    processed_at  TIMESTAMPTZ          DEFAULT now(),
    CONSTRAINT processed_orders_pkey PRIMARY KEY (order_id)
);

-- ==========================================================================
-- SECTION 4 — Foreign keys, guarded on their referents
-- ==========================================================================
-- Four FOREIGN KEY constraints, each exactly pg_get_constraintdef's rendering
-- of the live one, added only if the referent exists and the constraint does
-- not. See the header for why they are not inline in the CREATE TABLEs.
--
-- The conrelid lookup is by regclass, not by bare conname: constraint names
-- are unique per table, not per database, so keying on the name alone would
-- answer for the wrong table on a schema that holds a same-named relation.
DO $$
BEGIN
    -- 4a) profiles.id -> auth.users(id) ON DELETE CASCADE -------------------
    -- ON DELETE CASCADE is production's, and it is what makes `profiles` a
    -- true extension row of the auth user rather than an independent record:
    -- deleting the user removes the profile.
    IF to_regclass('auth.users') IS NULL THEN
        RAISE NOTICE '020: auth.users absent - SKIPPING profiles_id_fkey, '
                     'strategies_user_id_fkey and '
                     'processed_orders_user_id_fkey. The resulting schema is '
                     'WEAKER than production.';
    ELSE
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = 'public.profiles'::regclass
               AND conname  = 'profiles_id_fkey'
        ) THEN
            ALTER TABLE public.profiles
                ADD CONSTRAINT profiles_id_fkey
                FOREIGN KEY (id) REFERENCES auth.users (id) ON DELETE CASCADE;
        END IF;

        -- 4b) strategies.user_id -> auth.users(id) -------------------------
        -- NO referential action, which is production's shape: deleting an
        -- auth user is REFUSED while they still own a strategy. Do not add
        -- ON DELETE CASCADE here to make 4a symmetric; the asymmetry is the
        -- live behaviour.
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = 'public.strategies'::regclass
               AND conname  = 'strategies_user_id_fkey'
        ) THEN
            ALTER TABLE public.strategies
                ADD CONSTRAINT strategies_user_id_fkey
                FOREIGN KEY (user_id) REFERENCES auth.users (id);
        END IF;

        -- 4c) processed_orders.user_id -> auth.users(id) --------------------
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = 'public.processed_orders'::regclass
               AND conname  = 'processed_orders_user_id_fkey'
        ) THEN
            ALTER TABLE public.processed_orders
                ADD CONSTRAINT processed_orders_user_id_fkey
                FOREIGN KEY (user_id) REFERENCES auth.users (id);
        END IF;
    END IF;

    -- 4d) strategies.source_library_id -> library_strategies(id) -----------
    -- ON DELETE SET NULL: unpublishing a marketplace strategy must not delete
    -- the clones made from it, only forget the provenance.
    --
    -- library_strategies is declared ONLY by Alembic e88f9911b5a2, so on a
    -- from-source rebuild this guard is the one that will skip. That is the
    -- provisioning order made visible: run the Alembic chain before 020 and
    -- the constraint lands; run 020 alone and it does not.
    IF to_regclass('public.library_strategies') IS NULL THEN
        RAISE NOTICE '020: public.library_strategies absent - SKIPPING '
                     'strategies_source_library_id_fkey. It is declared only '
                     'by alembic revision e88f9911b5a2; see '
                     'backend_app/migrations/PROVISIONING_ORDER.md.';
    ELSIF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.strategies'::regclass
           AND conname  = 'strategies_source_library_id_fkey'
    ) THEN
        ALTER TABLE public.strategies
            ADD CONSTRAINT strategies_source_library_id_fkey
            FOREIGN KEY (source_library_id)
            REFERENCES public.library_strategies (id) ON DELETE SET NULL;
    END IF;
END $$;

-- ==========================================================================
-- SECTION 5 — Row level security, as measured
-- ==========================================================================
-- ENABLE ROW LEVEL SECURITY is idempotent and is a no-op where it is already
-- on, which it is on all three tables in production (relrowsecurity = true,
-- relforcerowsecurity = false on all three — so the table OWNER still
-- bypasses, which is how the backend's postgres/service_role connection
-- reaches these rows).
ALTER TABLE public.profiles         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.strategies       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.processed_orders ENABLE ROW LEVEL SECURITY;

-- Six policies, reproduced from pg_policies. FOUR of the six are named with
-- spaces and initial capitals — `Select own profile`, `Update own profile`,
-- `Manage own strategies`, `Manage own processed orders`. Those names were
-- typed into the Supabase dashboard, which is the out-of-band provisioning
-- this whole migration exists to replace. They are kept VERBATIM, quoted,
-- because the guard matches on policyname: renaming them to the repo's
-- snake_case convention would make this file create a second, duplicate
-- policy on a live table instead of being a no-op.
--
-- Four of the six are granted to PUBLIC rather than to `authenticated`. That
-- is production's shape. A policy granted to PUBLIC applies to every role
-- that is not bypassing RLS, so it is WIDER in reach but the predicate is the
-- same owner check; for `anon`, auth.uid() is NULL and `NULL = id` is NULL,
-- so an anonymous caller still matches no row. Reproduced, not narrowed —
-- narrowing would be a change to production's security posture, and that is a
-- decision, not a capture.
DO $$
BEGIN
    -- 5a) profiles ---------------------------------------------------------
    -- Four policies, and they overlap: `profiles_owner_access` (ALL, to
    -- authenticated) already subsumes the two PUBLIC single-verb policies.
    -- RLS combines PERMISSIVE policies with OR, so the overlap grants nothing
    -- extra; all four are reproduced because all four are there.
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'profiles'
           AND policyname = 'Select own profile'
    ) THEN
        CREATE POLICY "Select own profile" ON public.profiles
            FOR SELECT TO PUBLIC
            USING ((auth.uid() = id));
    END IF;

    -- No WITH CHECK on this one, which is pg_policies' answer
    -- (with_check IS NULL). PostgreSQL then applies the USING expression to
    -- the new row, so the effect is the same as restating it — but it is left
    -- unstated so the catalogue keeps matching.
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'profiles'
           AND policyname = 'Update own profile'
    ) THEN
        CREATE POLICY "Update own profile" ON public.profiles
            FOR UPDATE TO PUBLIC
            USING ((auth.uid() = id));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'profiles'
           AND policyname = 'profiles_owner_access'
    ) THEN
        CREATE POLICY profiles_owner_access ON public.profiles
            FOR ALL TO authenticated
            USING ((auth.uid() = id))
            WITH CHECK ((auth.uid() = id));
    END IF;

    -- USING (true) for service_role is belt and braces: service_role is a
    -- BYPASSRLS-equivalent in Supabase's model, so it would reach these rows
    -- with or without this policy. Reproduced because it exists.
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'profiles'
           AND policyname = 'profiles_service_role'
    ) THEN
        CREATE POLICY profiles_service_role ON public.profiles
            FOR ALL TO service_role
            USING (true)
            WITH CHECK (true);
    END IF;

    -- 5b) strategies -------------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'strategies'
           AND policyname = 'Manage own strategies'
    ) THEN
        CREATE POLICY "Manage own strategies" ON public.strategies
            FOR ALL TO PUBLIC
            USING ((auth.uid() = user_id))
            WITH CHECK ((auth.uid() = user_id));
    END IF;

    -- 5c) processed_orders -------------------------------------------------
    -- See section 3: user_id is nullable, so rows with a NULL user_id match
    -- this predicate for nobody and are service_role-only by accident of the
    -- column being nullable rather than by design.
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'processed_orders'
           AND policyname = 'Manage own processed orders'
    ) THEN
        CREATE POLICY "Manage own processed orders" ON public.processed_orders
            FOR ALL TO PUBLIC
            USING ((auth.uid() = user_id))
            WITH CHECK ((auth.uid() = user_id));
    END IF;
END $$;

COMMIT;
