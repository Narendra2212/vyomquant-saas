-- ═══════════════════════════════════════════════════════════════════════════
-- PRIVILEGE HARDENING -- RUNS LAST, DELIBERATELY
-- ═══════════════════════════════════════════════════════════════════════════
-- Applied by `scripts/provision_database.py` as STEP 4, after every other file.
--
-- WHY THIS IS A SEPARATE FILE AND NOT PART OF 006
-- -----------------------------------------------
-- It was part of `006_reconcile_production_database.sql` first, and that did not work.
-- 006 runs in the middle of the sequence, and its `IF EXISTS` guards silently skipped
-- every table that did not exist yet. `migrations/create_plan_tables.sql` runs AFTER
-- 006, so `plans`, `plan_prices`, `plan_features`, `plan_limits` and
-- `plan_migration_mapping` were created later and inherited Supabase's default
-- `GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role`.
-- Verified by execution: after the 006-based attempt, `anon` still held
-- DELETE, INSERT, TRUNCATE and UPDATE on all five.
--
-- A privilege posture can only be asserted once the objects it applies to all exist. So
-- this is the last thing that runs, it is idempotent, and it is the single auditable
-- place that answers "what can the browser's credentials do?".
--
-- THE THREAT MODEL, IN ONE PARAGRAPH
-- ----------------------------------
-- Supabase resolves a PostgREST request bearing only the anon key to the `anon` role,
-- and one bearing a user's JWT to `authenticated`. The anon key is published in the
-- browser bundle; it is not a secret and must not be treated as one. Supabase's
-- bootstrap grants both roles full privileges on `public` by default. Therefore any
-- table in `public` is reachable by anyone on the internet unless either row level
-- security filters it or the privilege is revoked. This file sets the privileges; RLS
-- policies live with the tables they protect.
--
-- WHAT WAS FOUND BEFORE THIS FILE EXISTED
-- ---------------------------------------
-- `staging/billing_exposure_probe.py` executed the access matrix as each role and found
-- 22 exposures, including `anon` reading every customer's invoices, subscription records
-- and stored card metadata, and `authenticated` reading, updating, deleting and
-- forge-inserting other customers' billing rows. `staging/exposure_check.py` then found
-- `creator_analytics` (per-creator `total_earnings`) in the same state. A separate
-- question -- which tables can `anon` WRITE? -- found the plan catalogue and the
-- migration bookkeeping tables, where the risk is integrity rather than disclosure:
-- rewriting `plan_prices.price_cents` changes what customers are charged, and setting
-- `alembic_version.version_num` makes every later deployment reason from a false
-- migration state.

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 0: remove the privileges ROW LEVEL SECURITY CANNOT FILTER
-- ───────────────────────────────────────────────────────────────────────────
-- Supabase's model is "grant the browser roles DML, let RLS filter the rows". That model
-- works for SELECT, INSERT, UPDATE and DELETE, which operate per row. It does not work
-- for TRUNCATE, REFERENCES or TRIGGER, which are table-level: PostgreSQL never consults
-- a row policy for any of them. So those three privileges are pure surface and are
-- removed from both browser roles everywhere.
--
-- MEASURED. `staging/truncate_probe.py`, run after the per-table hardening below was
-- already in place:
--
--     profiles           rls=True pol=4  anon  TRUNCATE CASCADE  *** SUCCEEDED ***
--     orders             rls=True pol=3  anon  TRUNCATE CASCADE  *** SUCCEEDED ***
--     execution_records  rls=True pol=3  anon  TRUNCATE          *** SUCCEEDED ***
--     referral_wallets   rls=True pol=3  anon  TRUNCATE          *** SUCCEEDED ***
--
-- `profiles` is the identity table every other table references, and CASCADE truncates
-- the referencing tables with it, so that first line is the whole database in one
-- statement. Note what it is NOT stopped by: RLS is enabled on all four and they carry
-- correct owner policies. Note also that the plain TRUNCATE refusals on `profiles` and
-- `orders` were not a control -- they failed only because a foreign key referenced them,
-- and CASCADE asks for the referencing tables too, which the same role could also
-- truncate. Incidental protection, measured and then measured around.
--
-- HOW REACHABLE IS IT, HONESTLY. Not reachable through the documented client API today,
-- and this is stated plainly rather than inflated: `anon` and `authenticated` are both
-- NOLOGIN, so neither can open a PostgreSQL connection; PostgREST exposes no TRUNCATE
-- verb; and no function in `public` performs a TRUNCATE, so there is no RPC path either
-- (checked: `pg_get_functiondef` ILIKE '%truncate%' returns nothing). It is a latent
-- least-privilege defect, not a confirmed remote exploit.
--
-- It is still worth closing, for two reasons. It sets the blast radius of anything that
-- goes wrong later: with these privileges held, one SQL injection or one careless
-- SECURITY INVOKER function turns a row-scoped bug into total data destruction. And
-- TRIGGER lets a role attach a trigger to a table, which is the ability to run chosen
-- code on someone else's write.
--
-- The ALTER DEFAULT PRIVILEGES lines matter as much as the REVOKEs: without them the
-- next table anyone creates arrives holding all three again.
DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format(
                'REVOKE TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public '
                'FROM %I', r);
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public '
                'REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLES FROM %I', r);
            RAISE NOTICE
                'hardening: TRUNCATE/REFERENCES/TRIGGER revoked from % (now and by default)',
                r;
        END IF;
    END LOOP;
END $$;

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 1: per-customer financial and commercial tables
-- ───────────────────────────────────────────────────────────────────────────
-- RLS enabled so the owner-access policies 006 PART 11 writes actually bind -- PART 11
-- created correct policies on `invoices` and `subscriptions` and never enabled RLS, so
-- PostgreSQL never consulted them.
--
-- ENABLE, not FORCE. `routers/billing.py` reads `payment_methods` through SQLAlchemy at
-- lines 1598 and 2576 via `get_db`, and every DSN in this repository connects as
-- `postgres`, which owns these tables. An owner is exempt from its own policies unless
-- FORCE is set, so ENABLE closes the PostgREST surface and leaves the API untouched.
-- FORCE would break those two endpoints and is not used.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'invoices') THEN
        ALTER TABLE public.invoices ENABLE ROW LEVEL SECURITY;
        REVOKE ALL ON public.invoices FROM anon;
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
            ON public.invoices FROM authenticated;
        GRANT SELECT ON public.invoices TO authenticated;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'subscriptions') THEN
        ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;
        REVOKE ALL ON public.subscriptions FROM anon;
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
            ON public.subscriptions FROM authenticated;
        GRANT SELECT ON public.subscriptions TO authenticated;
    END IF;

    -- Stored-instrument metadata: brand, last4, expiry, and the provider's
    -- payment_method_id. 006 PART 11 declared no policy for this table at all, so it
    -- needs both. Served exclusively by the API over SQLAlchemy, so neither browser role
    -- needs any privilege; the owner policy is defence in depth for a future path.
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'payment_methods') THEN
        ALTER TABLE public.payment_methods ENABLE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS "payment_methods_owner_access" ON public.payment_methods;
        DROP POLICY IF EXISTS "payment_methods_service_role" ON public.payment_methods;
        CREATE POLICY "payment_methods_owner_access" ON public.payment_methods
            FOR SELECT TO authenticated USING (auth.uid()::text = user_id::text);
        CREATE POLICY "payment_methods_service_role" ON public.payment_methods
            FOR ALL TO service_role USING (true) WITH CHECK (true);
        REVOKE ALL ON public.payment_methods FROM anon;
        REVOKE ALL ON public.payment_methods FROM authenticated;
    END IF;

    -- Per-creator commercial figures: creator_id, total_earnings, subscriber and clone
    -- counts. Its declaring migration
    -- (archived_migrations/root_migrations/008_create_creator_analytics.sql) sets no RLS,
    -- no policy and no grant. `routers/library.py::creator_analytics` computes a
    -- creator's figures from `library_strategies` and `marketplace_settlements` through
    -- the service-role client and never reads this table, so no browser privilege is
    -- granted; the policy is there for a future path.
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'creator_analytics') THEN
        ALTER TABLE public.creator_analytics ENABLE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS "creator_analytics_owner_access" ON public.creator_analytics;
        DROP POLICY IF EXISTS "creator_analytics_service_role" ON public.creator_analytics;
        CREATE POLICY "creator_analytics_owner_access" ON public.creator_analytics
            FOR SELECT TO authenticated USING (auth.uid()::text = creator_id::text);
        CREATE POLICY "creator_analytics_service_role" ON public.creator_analytics
            FOR ALL TO service_role USING (true) WITH CHECK (true);
        REVOKE ALL ON public.creator_analytics FROM anon;
        REVOKE ALL ON public.creator_analytics FROM authenticated;
    END IF;

    -- Platform-wide rather than per-customer: total_revenue, total_subscribers,
    -- top_strategy_id, top_creator_id. Commercially sensitive, with no owning customer
    -- to compare a row against -- so no owner policy applies and both browser roles are
    -- revoked outright rather than given a policy that would have to read `USING (true)`.
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'marketplace_analytics') THEN
        ALTER TABLE public.marketplace_analytics ENABLE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS "marketplace_analytics_service_role" ON public.marketplace_analytics;
        CREATE POLICY "marketplace_analytics_service_role" ON public.marketplace_analytics
            FOR ALL TO service_role USING (true) WITH CHECK (true);
        REVOKE ALL ON public.marketplace_analytics FROM anon;
        REVOKE ALL ON public.marketplace_analytics FROM authenticated;
    END IF;

    -- Internal transaction plumbing keyed by tenant_id. No supported client path exists,
    -- so no policy is invented: with no privilege there is nothing for a policy to
    -- filter, and adding one would imply a client surface that does not exist.
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'transaction_rollbacks') THEN
        ALTER TABLE public.transaction_rollbacks ENABLE ROW LEVEL SECURITY;
        REVOKE ALL ON public.transaction_rollbacks FROM anon;
        REVOKE ALL ON public.transaction_rollbacks FROM authenticated;
    END IF;
END $$;

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 2: the plan catalogue is readable, not writable
-- ───────────────────────────────────────────────────────────────────────────
-- Plan and price rows are public product information, so SELECT stays for both browser
-- roles -- a pricing page is entitled to read them. Writes do not: `anon` held
-- INSERT/UPDATE/DELETE/TRUNCATE on `plan_prices`, i.e. on what customers are charged.
--
-- RLS is deliberately NOT enabled here. There is no owning customer, so a policy would
-- have to be `USING (true)`, which filters nothing and only creates the appearance of a
-- control. The privilege revoke IS the control, and saying so plainly is better than
-- dressing it up as row security.
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'plans', 'plan_features', 'plan_limits', 'plan_prices', 'plan_migration_mapping'
    ]
    LOOP
        IF EXISTS (SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = t) THEN
            EXECUTE format(
                'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
                'ON public.%I FROM anon, authenticated', t);
            EXECUTE format('GRANT SELECT ON public.%I TO anon, authenticated', t);
        END IF;
    END LOOP;
END $$;

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 3: migration bookkeeping is not a client surface
-- ───────────────────────────────────────────────────────────────────────────
-- `alembic_version` decides which revisions a deployment believes are applied;
-- `schema_migrations` is the provisioner's own record of applied files and checksums.
-- Neither is readable or writable by a browser role.
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['alembic_version', 'schema_migrations']
    LOOP
        IF EXISTS (SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = t) THEN
            EXECUTE format('REVOKE ALL ON public.%I FROM anon, authenticated', t);
        END IF;
    END LOOP;
END $$;

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 3b: a policy predicate of TRUE is not row security
-- ───────────────────────────────────────────────────────────────────────────
-- `library_ratings` had row level security ENABLED and a SELECT policy of `USING (true)`.
-- Enabled-with-a-true-predicate filters nothing, so the table read as if RLS were off
-- while every inventory query that looks only at `relrowsecurity` reported it as secured.
--
-- The table is dual purpose, which is what makes the blanket policy wrong:
--   * `rating IS NOT NULL` rows are marketplace reviews. Public by design -- a listing
--     page shows its reviews to everyone.
--   * `rating IS NULL` rows are the caller's FAVOURITES, written by
--     `POST /api/library/{id}/favorite` and read back by `GET /api/library/favorites`,
--     which `tests/property/test_tenant_isolation_matrix.py` classifies OWNER_SCOPED_LIST.
-- So the blanket policy published one customer's private favourites list to every other
-- logged-in customer, through the browser anon key in `algo22-terminal/src/supabase.js`.
--
-- The replacement keeps reviews public and makes everything else owner-only. Server-side
-- reads in `backend_app/routers/library.py` all go through the service client, which holds
-- BYPASSRLS, so none of them change behaviour.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'library_ratings') THEN
        DROP POLICY IF EXISTS library_ratings_select_authenticated ON public.library_ratings;
        DROP POLICY IF EXISTS library_ratings_select_reviews_public ON public.library_ratings;
        CREATE POLICY library_ratings_select_reviews_public
            ON public.library_ratings
            FOR SELECT
            TO authenticated
            USING (rating IS NOT NULL OR user_id = auth.uid());
    END IF;
END $$;

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 4: refuse to finish while a per-customer table is still exposed
-- ───────────────────────────────────────────────────────────────────────────
-- The sections above name tables explicitly, which means a table added later is not
-- covered and nobody finds out. This is the backstop: any table in `public` carrying a
-- column that identifies a customer, with row level security disabled and `anon` still
-- holding SELECT, aborts provisioning with the table named.
--
-- Deliberately an EXCEPTION and not a NOTICE. A warning in a provisioning log is a
-- warning nobody reads; this is the class of defect that produced 22 live exposures, and
-- the correct behaviour on finding another one is to stop.
DO $$
DECLARE
    offending text;
BEGIN
    SELECT string_agg(c.relname, ', ' ORDER BY c.relname) INTO offending
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'r'
      AND NOT c.relrowsecurity
      AND EXISTS (
          SELECT 1 FROM information_schema.columns col
          WHERE col.table_schema = 'public'
            AND col.table_name = c.relname
            AND col.column_name IN (
                'user_id', 'tenant_id', 'profile_id', 'owner_id', 'author_id',
                'customer_id', 'subscriber_id', 'referrer_id', 'created_by',
                'creator_id', 'seller_id', 'account_id'
            )
      )
      AND EXISTS (
          SELECT 1 FROM information_schema.role_table_grants g
          WHERE g.table_schema = 'public'
            AND g.table_name = c.relname
            AND g.grantee = 'anon'
            AND g.privilege_type = 'SELECT'
      );

    IF offending IS NOT NULL THEN
        RAISE EXCEPTION
            'privilege hardening preflight FAILED: these tables carry a per-customer '
            'column, have row level security DISABLED, and still grant SELECT to anon '
            '-- so any holder of the browser anon key can read every customer''s rows: '
            '%. Either enable RLS with an owner policy, or revoke anon. See '
            'migrations/hardening/999_privilege_hardening.sql section 4.', offending;
    END IF;

    RAISE NOTICE 'privilege hardening: no per-customer table is readable by anon';
END $$;

-- Second backstop, for the privileges section 0 removes. Same reasoning: the blanket
-- REVOKE covers the tables that exist when this file runs, and ALTER DEFAULT PRIVILEGES
-- covers tables created afterwards BY `postgres`. A table created by some other role
-- would miss both, and the failure would be silent. So the condition is asserted rather
-- than assumed.
DO $$
DECLARE
    offending text;
BEGIN
    SELECT string_agg(DISTINCT g.table_name || ' (' || g.grantee || ':' ||
                      g.privilege_type || ')', ', ' ORDER BY g.table_name || ' (' ||
                      g.grantee || ':' || g.privilege_type || ')')
      INTO offending
    FROM information_schema.role_table_grants g
    WHERE g.table_schema = 'public'
      AND g.grantee IN ('anon', 'authenticated')
      AND g.privilege_type IN ('TRUNCATE', 'REFERENCES', 'TRIGGER');

    IF offending IS NOT NULL THEN
        RAISE EXCEPTION
            'privilege hardening preflight FAILED: a browser role still holds a '
            'privilege row level security cannot filter. TRUNCATE empties a table '
            'regardless of any policy, and TRIGGER allows attaching code to another '
            'user''s writes: %. See section 0.', offending;
    END IF;

    RAISE NOTICE 'privilege hardening: no browser role holds TRUNCATE, REFERENCES or TRIGGER';
END $$;
-- Third backstop. The first one looks for `relrowsecurity = false`, which is how the 22
-- billing exposures were found -- but it is blind to the `library_ratings` shape, where
-- RLS is ENABLED and the policy predicate is the constant TRUE. Those two together are
-- indistinguishable from RLS being off, while every `relrowsecurity` check reports the
-- table as secured. That is worse than an unguarded table, because it looks guarded.
--
-- `service_role` is excluded: it holds BYPASSRLS, so a `USING (true)` policy on it grants
-- nothing it does not already have, and the 51 such policies in this schema are intended.
DO $$
DECLARE
    offending text;
BEGIN
    SELECT string_agg(DISTINCT p.tablename || '.' || p.policyname || ' (' || p.cmd || ')',
                      ', ' ORDER BY p.tablename || '.' || p.policyname || ' (' || p.cmd || ')')
      INTO offending
    FROM pg_policies p
    WHERE p.schemaname = 'public'
      AND p.permissive = 'PERMISSIVE'
      AND coalesce(p.qual, p.with_check) = 'true'
      AND ('anon' = ANY(p.roles) OR 'authenticated' = ANY(p.roles) OR 'public' = ANY(p.roles))
      AND EXISTS (
          SELECT 1 FROM information_schema.columns col
          WHERE col.table_schema = 'public'
            AND col.table_name = p.tablename
            AND col.column_name IN (
                'user_id', 'tenant_id', 'profile_id', 'owner_id', 'author_id',
                'customer_id', 'subscriber_id', 'referrer_id', 'created_by',
                'creator_id', 'seller_id', 'account_id'
            )
      );

    IF offending IS NOT NULL THEN
        RAISE EXCEPTION
            'privilege hardening preflight FAILED: a table carrying a per-customer column '
            'has row level security ENABLED but a policy predicate of TRUE, which applies '
            'no row filter -- the table is readable or writable across customers and still '
            'reports relrowsecurity = true: %. Give the policy an ownership predicate. See '
            'migrations/hardening/999_privilege_hardening.sql section 3b.', offending;
    END IF;

    RAISE NOTICE 'privilege hardening: no per-customer table has an unconstrained policy';
END $$;
