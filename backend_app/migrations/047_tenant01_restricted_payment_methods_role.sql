-- ==========================================================================
-- 047 — a new, deliberately-restricted role for the two owner-path routes
--       TENANT-01 identified as the actual blocker on FORCE RLS
-- ==========================================================================
--
-- THE PROBLEM THIS FILE CLOSES, STATED PRECISELY
-- -------------------------------------------------------------------------
-- TENANT-01's own remaining_action worked out exactly which two real,
-- per-request SQLAlchemy call sites touch `payment_methods` --
-- `routers/billing.py::get_payment_methods` / `delete_payment_method` -- and
-- then found the fix everyone had assumed would close them
-- ("wire a tenant-context setter, then FORCE ROW LEVEL SECURITY") would NOT
-- close anything: FORCE only changes behaviour for the TABLE OWNER, and
-- every DSN in this repository connects as `postgres`, a superuser.
-- `rolsuper`/`rolbypassrls` both unconditionally skip RLS, FORCE or not --
-- confirmed directly against a real replica (`SELECT rolsuper, rolbypassrls
-- FROM pg_roles WHERE rolname = current_user` returned true/true). A
-- superuser cannot be made subject to RLS by any policy or FORCE clause;
-- the only way row security protects THIS connection shape is a DIFFERENT
-- connection, as a role that is neither.
--
-- So this file does not touch the owner connection at all. It creates one.
--
-- WHY A NEW ROLE RATHER THAN REUSING `authenticated`
-- -------------------------------------------------------------------------
-- `authenticated` already has an owner-only SELECT policy on this table
-- (migrations/hardening/999_privilege_hardening.sql) and is the identity
-- PostgREST assumes for the browser. The backend is not the browser: it
-- needs DELETE too (`delete_payment_method`), and giving the backend the
-- SAME role the browser's anon key could theoretically present (if `anon`
-- were ever compromised into `authenticated`) would conflate two different
-- trust boundaries for no reason. A role scoped to exactly these two
-- queries, and nothing else, is the smaller surface.
--
-- WHY `auth.uid()`, THE SAME FUNCTION EVERY OTHER POLICY IN THIS REPOSITORY
-- USES, RATHER THAN A BESPOKE PREDICATE
-- -------------------------------------------------------------------------
-- `scripts/provision_database.py`'s `auth.uid()` reads a plain session GUC
-- (`current_setting('request.jwt.claim.sub', true)`) that ANY role can set
-- with `SET LOCAL` -- it is not PostgREST-specific or `authenticated`-
-- specific machinery, just a convention this schema already standardises
-- on. The new role sets the same GUC the real Supabase JWT path sets, so
-- the policy below is identical in shape to `payment_methods_owner_access`,
-- `fee_records_owner_access` and every other owner policy in this
-- migration set -- not a second, parallel tenant-context mechanism to keep
-- in sync with the first.
--
-- PASSWORD: DELIBERATELY NOT IN THIS FILE
-- -------------------------------------------------------------------------
-- This migration creates the role WITHOUT a password (LOGIN with no
-- PASSWORD clause cannot authenticate at all until one is set). Setting the
-- real password is a separate, one-off statement run directly against the
-- real database by whoever applies this file --
--   ALTER ROLE payment_methods_app WITH PASSWORD '<a strong random value>';
-- -- specifically so a real credential never enters this tracked, committed
-- file or this repository's git history. See the companion note this
-- session also produced for the exact command and the app-side env var it
-- feeds (PAYMENT_METHODS_DB_URL).
--
-- IDEMPOTENCY
-- -------------------------------------------------------------------------
-- `CREATE ROLE` has no `IF NOT EXISTS` in PostgreSQL, so role creation is
-- guarded on `pg_roles` the same way 026 guards its preflight checks.
-- Everything else is `IF NOT EXISTS`-guarded, `pg_policies`-guarded, or a
-- GRANT/REVOKE, which are idempotent by definition. Nothing drops, renames
-- or truncates `payment_methods` itself -- only the new role's own objects
-- and grants, so `test_no_destructive_statements` holds.
--
-- Regression: tests/test_tenant01_restricted_payment_methods_role_postgres.py
-- ==========================================================================

BEGIN;

-- ──────────────────────────────────────────────────────────────────────────
-- SECTION 0 — preflight: the substrate this file's policy needs
-- ──────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'auth' AND p.proname = 'uid'
    ) THEN
        RAISE EXCEPTION
            '047 precondition failed: auth.uid() does not exist. The policy below is '
            'expressed in terms of it. See scripts/provision_database.py '
            '--create-substrate.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'payment_methods'
    ) THEN
        RAISE EXCEPTION
            '047 precondition failed: public.payment_methods does not exist on this '
            'database. This file grants a new role privileges on it and would silently '
            'grant nothing.';
    END IF;
END $$;

-- ──────────────────────────────────────────────────────────────────────────
-- SECTION 1 — the role, created WITHOUT a password (see header)
-- ──────────────────────────────────────────────────────────────────────────
-- NOSUPERUSER and NOBYPASSRLS are PostgreSQL's own defaults for CREATE ROLE,
-- stated explicitly here rather than relied on implicitly: this role's
-- entire purpose is to be a role RLS actually applies to, and a future
-- reader should not have to know the defaults to see that this is true.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'payment_methods_app') THEN
        CREATE ROLE payment_methods_app WITH
            LOGIN
            NOSUPERUSER
            NOBYPASSRLS
            NOCREATEDB
            NOCREATEROLE
            NOREPLICATION
            CONNECTION LIMIT 10;
    END IF;
END $$;

COMMENT ON ROLE payment_methods_app IS
    'TENANT-01: the one role in this schema that genuinely has RLS applied to it on '
    'public.payment_methods, because it is neither the table owner nor a superuser nor '
    'BYPASSRLS. Used by exactly two routes (routers/billing.py get_payment_methods / '
    'delete_payment_method) through a second, minimal SQLAlchemy engine -- see '
    'backend_app/core/restricted_db.py. Sets request.jwt.claim.sub per request the same '
    'way the real Supabase JWT path does, so payment_methods_app_owner_access below is '
    'the same shape as every other owner policy in this schema.';

-- Baseline connectivity grants every role needs before any table grant can matter.
GRANT CONNECT ON DATABASE postgres TO payment_methods_app;
GRANT USAGE ON SCHEMA public TO payment_methods_app;
GRANT USAGE ON SCHEMA auth TO payment_methods_app;

-- ──────────────────────────────────────────────────────────────────────────
-- SECTION 2 — exactly the two privileges the two real call sites use
-- ──────────────────────────────────────────────────────────────────────────
-- get_payment_methods: SELECT. delete_payment_method: SELECT (to find the row) then
-- DELETE. No INSERT/UPDATE -- add_payment_method is already an explicit refusal
-- (routers/billing.py's own comment: "unreachable from any client before that, so the
-- table this reads has no writer") and this role should not be able to write even if
-- that changes without this migration being revisited.
GRANT SELECT, DELETE ON public.payment_methods TO payment_methods_app;

-- Nothing granted on any other table. A freshly created role gets no privilege from
-- the substrate's `ALTER DEFAULT PRIVILEGES ... TO anon, authenticated, service_role`
-- (named roles only, verified this session against a real replica), so this section is
-- the complete grant for payment_methods_app on this database.

-- ──────────────────────────────────────────────────────────────────────────
-- SECTION 3 — the owner policy for payment_methods_app
-- ──────────────────────────────────────────────────────────────────────────
-- RLS is already ENABLED on public.payment_methods (999_privilege_hardening.sql).
-- ENABLE alone already applies to payment_methods_app, since it is neither the owner
-- nor a superuser -- FORCE is not needed and not added, consistent with the existing
-- ENABLE-not-FORCE reasoning for this table (999's own comment: FORCE would break the
-- owner-connection routes, which still exist and are unaffected by this file).
--
-- Two policies, not one `FOR ALL`, because `FOR ALL` would also silently permit INSERT
-- and UPDATE the moment anyone granted them later -- the policy should not be more
-- permissive than the two commands this role is actually granted.
--
-- THE PREDICATE IS BUILT DYNAMICALLY, NOT HARDCODED, BECAUSE THIS COLUMN'S REAL TYPE
-- GENUINELY DIFFERS BETWEEN ENVIRONMENTS (DB-ALM-02's own subject matter, hit for real
-- here): the real production database's payment_methods.user_id is `character varying`
-- (alembic's BUG-049/d4e5f6a7b8c9 has never actually run there -- alembic_version is
-- stuck at d97ffff9c3bb, long before it). scripts/provision_database.py's own
-- `--create-substrate` path (what CI's fresh-schema job runs) explicitly runs `alembic
-- upgrade head`, which DOES apply BUG-049, converting the same column to `uuid` on that
-- fresh build. A single hardcoded cast direction is therefore wrong in one of the two
-- real environments this migration must run in -- confirmed by this exact failure on
-- this exact file's first real CI run (`operator does not exist: text = uuid`,
-- 01-pr-check.yml's Provisioning job). Checking the live type and casting the uuid side
-- (auth.uid()) when the column is already uuid, or the column side to text otherwise,
-- makes this migration correct in both shapes rather than correct in only the one this
-- session happened to test against first.
DO $$
DECLARE
    user_id_type text;
    select_qual  text;
    delete_qual  text;
BEGIN
    SELECT data_type INTO user_id_type
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'payment_methods' AND column_name = 'user_id';

    IF user_id_type = 'uuid' THEN
        select_qual := 'auth.uid() = user_id';
        delete_qual := 'auth.uid() = user_id';
    ELSE
        select_qual := '(auth.uid())::text = user_id';
        delete_qual := '(auth.uid())::text = user_id';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'payment_methods'
           AND policyname = 'payment_methods_app_select'
    ) THEN
        EXECUTE format(
            'CREATE POLICY "payment_methods_app_select" ON public.payment_methods '
            'FOR SELECT TO payment_methods_app USING (%s)', select_qual
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'payment_methods'
           AND policyname = 'payment_methods_app_delete'
    ) THEN
        EXECUTE format(
            'CREATE POLICY "payment_methods_app_delete" ON public.payment_methods '
            'FOR DELETE TO payment_methods_app USING (%s)', delete_qual
        );
    END IF;
END $$;

-- ──────────────────────────────────────────────────────────────────────────
-- SECTION 4 — assert the end state, in this transaction
-- ──────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    role_exists   boolean;
    role_super    boolean;
    role_bypass   boolean;
    policy_cnt    integer;
    grant_cnt     integer;
BEGIN
    SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'payment_methods_app')
      INTO role_exists;
    IF NOT role_exists THEN
        RAISE EXCEPTION '047 postflight FAILED: role payment_methods_app was not created.';
    END IF;

    SELECT rolsuper, rolbypassrls INTO role_super, role_bypass
      FROM pg_roles WHERE rolname = 'payment_methods_app';
    IF role_super OR role_bypass THEN
        RAISE EXCEPTION '047 postflight FAILED: payment_methods_app has rolsuper=% '
                        'rolbypassrls=% -- this role exists specifically to be subject '
                        'to RLS, and a superuser/BYPASSRLS role is exempt from it '
                        'regardless of any policy below.', role_super, role_bypass;
    END IF;

    SELECT count(*) INTO policy_cnt
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'payment_methods'
       AND policyname IN ('payment_methods_app_select', 'payment_methods_app_delete');
    IF policy_cnt <> 2 THEN
        RAISE EXCEPTION '047 postflight FAILED: expected both payment_methods_app '
                        'policies, found % of 2.', policy_cnt;
    END IF;

    SELECT count(*) INTO grant_cnt
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND table_name = 'payment_methods'
       AND grantee = 'payment_methods_app'
       AND privilege_type IN ('SELECT', 'DELETE');
    IF grant_cnt <> 2 THEN
        RAISE EXCEPTION '047 postflight FAILED: expected SELECT and DELETE grants for '
                        'payment_methods_app, found %.', grant_cnt;
    END IF;

    RAISE NOTICE '047: payment_methods_app exists, is neither superuser nor BYPASSRLS, '
                 'holds exactly SELECT+DELETE on public.payment_methods, and is subject '
                 'to two owner-keyed RLS policies.';
END $$;

COMMIT;
