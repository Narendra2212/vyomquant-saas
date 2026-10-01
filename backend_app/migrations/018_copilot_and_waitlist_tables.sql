-- ==========================================================================
-- 018_copilot_and_waitlist_tables.sql — three tables the application already
--                                       talks to, and that production does
--                                       not have
-- ==========================================================================
--
-- WHAT THIS MIGRATION RECONCILES
-- ------------------------------
-- public.copilot_sessions, public.copilot_messages and public.waitlist are
-- referenced by shipped, mounted code. None of the three exists in the
-- production database. This file creates them, and nothing else.
--
-- They were never missed because they were declared in the WRONG PLACE. The
-- only declaration of all three in this repository is the Alembic revision
-- backend_app/alembic/versions/e88f9911b5a2_consolidate_full_schema.py, and
-- Alembic is vestigial here:
--
--   * production's alembic_version holds exactly one row, `d97ffff9c3bb`,
--     so e88f9911b5a2 and the three revisions after it were never applied;
--   * no workflow under .github/workflows/, no Dockerfile, and no script in
--     scripts/ or *.sh runs `alembic upgrade` at all — the authoritative
--     schema is the numbered SQL set in this directory (001..018) plus
--     migrations/, applied by hand via scripts/apply_migrations.py;
--   * and e88f9911b5a2 CANNOT be applied now even by hand: its first
--     statements include op.create_table('library_strategies'), and
--     public.library_strategies already exists in production, so the
--     revision would abort on a duplicate_table before reaching the copilot
--     or waitlist tables.
--
-- So the fix is not "run Alembic". The fix is to declare these three tables
-- where the other 68 production tables are declared. This file is that
-- declaration, and it is additive only: three CREATE TABLE IF NOT EXISTS
-- against relations confirmed absent, their indexes, their RLS. No existing
-- table is altered, no row is rewritten, nothing is dropped.
--
-- WHAT WAS BREAKING, AND HOW LOUDLY
-- ---------------------------------
-- 1. `waitlist` — a LIVE break, visible to an operator today.
--    algo22-terminal/src/lib/waitlistApi.js queries supabase.from('waitlist')
--    straight from the browser over PostgREST.
--    components/admin/AdminDashboard.jsx imports waitlistAdminApi and calls
--    getAll() / updateStatus() / delete(); App.jsx line 634 routes
--    /admin/waitlist to that dashboard behind AdminGuard. That surface is
--    MOUNTED, so every visit errors on a relation that does not exist.
--
-- 2. `copilot_sessions` / `copilot_messages` — a SILENT break, which is why
--    it survived. backend_app/main.py line 660 mounts the copilot router at
--    /api/v1/copilot, and backend_app/routers/copilot.py touches the two
--    tables nine times. EVERY write sits inside a try/except that only logs
--    a warning, and list_copilot_sessions returns [] on error. The chat
--    therefore streams normally, no 500 is ever raised, and NOTHING IS EVER
--    PERSISTED: session history is permanently empty by construction. There
--    are no frontend callers yet, so no user has reported it.
--
-- THE RLS MODEL, AND WHY THE POLICIES ARE NOT WHAT KEEPS COPILOT WORKING
-- ----------------------------------------------------------------------
-- The backend connects with SUPABASE_SERVICE_ROLE_KEY
-- (backend_app/core/supabase_connection.py line 33) and the service role
-- BYPASSES row level security. So the copilot router works the moment these
-- tables exist, with or without a permissive policy — the owner-scoped
-- policies below are defence in depth for any future browser-side read, not
-- the mechanism the router depends on.
--
-- THE ADMIN CLAIM PATH IS A FIX, NOT A COPY
-- -----------------------------------------
-- The archived original of this table,
-- archived_migrations/terminal_supabase_migrations/20260622000001_create_waitlist.sql,
-- carries three defects that are deliberately NOT reproduced here:
--
--   * `USING (auth.jwt() ->> 'role' = 'admin')` for the admin read. That
--     predicate can never be true. Supabase's top-level `role` JWT claim is
--     `anon`, `authenticated` or `service_role` — never `admin`. The admin
--     role lives at app_metadata.role, which is where the frontend reads it
--     from (App.jsx lines 329-331: `user.app_metadata?.role === 'admin'`).
--     Had that archived file ever been applied, /admin/waitlist would have
--     authenticated correctly and then read ZERO ROWS. The policies below
--     use (auth.jwt() -> 'app_metadata' ->> 'role') = 'admin', which is the
--     claim that actually carries the fact.
--   * `USING (auth.uid()::text = id::text)` for a "read own entry" policy,
--     comparing the caller's user id to the waitlist row's own primary key.
--     Two unrelated uuids are never equal; the policy granted nothing.
--     Dropped.
--   * no `trader_type` and no `monthly_volume` columns, both of which
--     waitlistApi.submit() actually sends. The column set below is
--     e88f9911b5a2's, which has them.
--
-- WHY THERE IS NO PUBLIC INSERT POLICY — read this before adding one
-- ------------------------------------------------------------------
-- The archived file granted `FOR INSERT TO anon, authenticated WITH CHECK
-- (true)`, i.e. a world-writable production table. That is deliberately
-- omitted, because the public signup form is currently UNMOUNTED:
-- algo22-terminal/src/components/waitlist/WaitlistForm.jsx is imported by
-- nothing outside tests, and LandingPage.jsx lines 18-22 record its removal.
-- An open insert path with no form behind it is pure spam surface with no
-- consumer. RLS denies by default and the service role is unaffected, so
-- omitting the policy costs nothing today.
--
-- THE POLICY MUST BE ADDED IN THE SAME CHANGE THAT RE-MOUNTS THE FORM.
-- tests/test_schema_table_reference_drift.py enforces exactly that: the
-- moment WaitlistForm.jsx is imported by a non-test module under
-- algo22-terminal/src/, that test demands an anon INSERT policy here and
-- fails until it is added — so re-mounting the form cannot silently drop
-- every lead into a denied insert.
--
-- IDEMPOTENCY
-- -----------
-- Re-running this file changes nothing. Tables and indexes use IF NOT
-- EXISTS; PostgreSQL has no CREATE POLICY IF NOT EXISTS, so every policy is
-- guarded on pg_policies by schemaname + tablename + policyname — the
-- pattern 017 section 3d, 008 section 6, 007 section 8 and 004b section 4
-- already use, rather than DROP POLICY IF EXISTS ... CREATE POLICY, which
-- would open a window in which the table is policy-less.
-- ==========================================================================

BEGIN;

-- ==========================================================================
-- SECTION 0 — Preflight
-- ==========================================================================
-- The policies below are granted to the Supabase `authenticated` role. On a
-- database where that role does not exist, CREATE POLICY fails with a bare
-- undefined_object and no context. Fail with a readable message instead —
-- the same read-only assertion 004b section 0 makes.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        RAISE EXCEPTION
            '018 precondition failed: role "authenticated" does not exist. '
            'This migration targets a Supabase database, where that role, '
            'anon and service_role are created by the platform.';
    END IF;
END $$;

-- ==========================================================================
-- SECTION 1 — public.copilot_sessions
-- ==========================================================================
-- Column set, defaults and index are e88f9911b5a2's, which is the newer and
-- more complete of the two definitions in the tree.
--
-- user_id carries NO foreign key, matching e88f9911b5a2. The referent would
-- be auth.users(id), which lives in a schema this migration set does not
-- own; every other user-scoped table here (strategy_deployments,
-- strategy_backtests, signals) declares the column the same way.
CREATE TABLE IF NOT EXISTS public.copilot_sessions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL,
    title       TEXT        NOT NULL DEFAULT 'New Chat',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_copilot_sessions_user_id
    ON public.copilot_sessions (user_id);

-- ==========================================================================
-- SECTION 2 — public.copilot_messages
-- ==========================================================================
-- ON DELETE CASCADE on the session FK is e88f9911b5a2's, and it is what makes
-- routers/copilot.py's delete_copilot_session correct: that handler deletes
-- the session row only and relies on the database to take the transcript
-- with it.
--
-- context_snapshot is JSONB where e88f9911b5a2 says sa.JSON() (which renders
-- as PostgreSQL `json`). JSONB is the deliberate choice: every other JSON
-- column in this schema is JSONB (strategy_versions.blueprint and
-- execution_graph in 001, plan_usage_ledger.context in 017), the router
-- writes and reads a plain dict either way, and `json` would store the
-- snapshot as reparsed text with no indexable structure.
CREATE TABLE IF NOT EXISTS public.copilot_messages (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id        UUID        NOT NULL
                                  REFERENCES public.copilot_sessions (id)
                                  ON DELETE CASCADE,
    role              TEXT        NOT NULL,
    content           TEXT        NOT NULL,
    context_snapshot  JSONB,
    token_count       INTEGER     NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_copilot_message_role
        CHECK (role IN ('user', 'assistant', 'system'))
);

CREATE INDEX IF NOT EXISTS idx_copilot_messages_session_id
    ON public.copilot_messages (session_id);

-- ==========================================================================
-- SECTION 3 — public.waitlist
-- ==========================================================================
-- All four CHECK constraints from e88f9911b5a2 are kept by name, so the
-- enumerations the form offers are enforced by the database and not only by
-- the browser.
--
-- ip_address is TEXT, not INET (the archived file) and not VARCHAR(45)
-- (e88f9911b5a2). Nothing in the application does subnet arithmetic on it;
-- PostgREST hands it over as a string; and an INET column REJECTS the
-- values a proxy chain actually produces, such as a comma-separated
-- X-Forwarded-For list, turning a best-effort audit field into a failed
-- insert.
CREATE TABLE IF NOT EXISTS public.waitlist (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT        NOT NULL,
    email             TEXT        NOT NULL UNIQUE,
    telegram          TEXT,
    experience_level  TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status            TEXT        NOT NULL DEFAULT 'pending',
    notes             TEXT,
    utm_source        TEXT,
    utm_medium        TEXT,
    ip_address        TEXT,
    trader_type       TEXT,
    monthly_volume    TEXT,
    CONSTRAINT chk_waitlist_exp
        CHECK (experience_level IN ('beginner', 'intermediate', 'advanced', 'professional')),
    CONSTRAINT chk_waitlist_status
        CHECK (status IN ('pending', 'approved', 'invited', 'converted')),
    CONSTRAINT chk_waitlist_trader_type
        CHECK (trader_type IS NULL OR trader_type IN ('retail', 'discretionary', 'prop', 'institutional')),
    CONSTRAINT chk_waitlist_volume
        CHECK (monthly_volume IS NULL OR monthly_volume IN ('<100k', '100k-1M', '1M-10M', '>10M'))
);

-- idx_waitlist_created_at backs the admin dashboard's default ordering
-- (waitlistAdminApi.getAll orders by created_at descending).
CREATE INDEX IF NOT EXISTS idx_waitlist_created_at
    ON public.waitlist (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_waitlist_status
    ON public.waitlist (status);
CREATE INDEX IF NOT EXISTS idx_waitlist_experience
    ON public.waitlist (experience_level);
-- idx_waitlist_email duplicates the index the UNIQUE constraint on email
-- creates implicitly. It is kept because e88f9911b5a2 declares it: two
-- declarations of one table that differ by an index are how the
-- strategy_backtests.version divergence started (see 016's header), and a
-- redundant btree on a low-volume table costs nothing.
CREATE INDEX IF NOT EXISTS idx_waitlist_email
    ON public.waitlist (email);

-- ==========================================================================
-- SECTION 4 — Row level security
-- ==========================================================================
-- Enabled first on all three tables. ENABLE ROW LEVEL SECURITY is
-- default-deny, so each table is unreachable rather than open between these
-- statements — and everything here is inside the one transaction, so no
-- session observes an intermediate state.
ALTER TABLE public.copilot_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.copilot_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.waitlist         ENABLE ROW LEVEL SECURITY;

-- 4a) copilot_sessions — owner scoped ---------------------------------------
-- Defence in depth only: the router reaches this table as service_role,
-- which bypasses RLS entirely.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'copilot_sessions'
           AND policyname = 'copilot_sessions_owner_access'
    ) THEN
        CREATE POLICY copilot_sessions_owner_access ON public.copilot_sessions
            FOR ALL TO authenticated
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END $$;

-- 4b) copilot_messages — scoped by the owning session ----------------------
-- A message has no user_id of its own, so ownership is the session's. The
-- EXISTS reads copilot_sessions, which is itself RLS-enabled and owner-scoped
-- by 4a, so under `authenticated` the subquery can only ever see the caller's
-- own sessions. The two policies therefore compose to the same answer whether
-- or not the inner table's RLS is applied, which is deliberate — nothing here
-- depends on a subtlety of how RLS nests.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'copilot_messages'
           AND policyname = 'copilot_messages_owner_access'
    ) THEN
        CREATE POLICY copilot_messages_owner_access ON public.copilot_messages
            FOR ALL TO authenticated
            USING (
                EXISTS (
                    SELECT 1 FROM public.copilot_sessions s
                     WHERE s.id = copilot_messages.session_id
                       AND s.user_id = auth.uid()
                )
            )
            WITH CHECK (
                EXISTS (
                    SELECT 1 FROM public.copilot_sessions s
                     WHERE s.id = copilot_messages.session_id
                       AND s.user_id = auth.uid()
                )
            );
    END IF;
END $$;

-- 4c) waitlist — admin read and administration ----------------------------
-- (auth.jwt() -> 'app_metadata' ->> 'role') = 'admin' is the whole point of
-- this section: it is the claim path AdminGuard already trusts
-- (App.jsx lines 329-331), and NOT the bare auth.jwt() ->> 'role' = 'admin'
-- of the archived file, which can never be true because the top-level role
-- claim is anon / authenticated / service_role. With the bare form, an admin
-- would pass the route guard and then read an empty table.
--
-- Three separate policies rather than one FOR ALL, because the capabilities
-- are genuinely different: the dashboard reads, approves (UPDATE) and
-- removes (DELETE) entries, and it never inserts.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'waitlist'
           AND policyname = 'waitlist_admin_select'
    ) THEN
        CREATE POLICY waitlist_admin_select ON public.waitlist
            FOR SELECT TO authenticated
            USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'waitlist'
           AND policyname = 'waitlist_admin_update'
    ) THEN
        CREATE POLICY waitlist_admin_update ON public.waitlist
            FOR UPDATE TO authenticated
            USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin')
            WITH CHECK ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'waitlist'
           AND policyname = 'waitlist_admin_delete'
    ) THEN
        CREATE POLICY waitlist_admin_delete ON public.waitlist
            FOR DELETE TO authenticated
            USING ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin');
    END IF;
END $$;

-- 4d) waitlist — the INSERT policy that is deliberately ABSENT -------------
-- There is intentionally NO anon/authenticated INSERT policy here. See the
-- header: the public form (components/waitlist/WaitlistForm.jsx) is
-- unmounted, so `FOR INSERT TO anon WITH CHECK (true)` would make a
-- production table world-writable with no consumer behind it. RLS denies by
-- default, so the absence is a closed door rather than an oversight, and the
-- service role is unaffected.
--
-- WHEN THE FORM IS RE-MOUNTED, ADD IT HERE, IN THAT SAME CHANGE:
--
--     CREATE POLICY waitlist_public_insert ON public.waitlist
--         FOR INSERT TO anon, authenticated
--         WITH CHECK (true);
--     GRANT INSERT ON public.waitlist TO anon, authenticated;
--
-- and expect rate limiting in front of it.
-- tests/test_schema_table_reference_drift.py fails the build if the form
-- becomes imported while this policy is still missing.

-- ==========================================================================
-- SECTION 5 — Privileges
-- ==========================================================================
-- RLS narrows what a role can see; a GRANT is what lets it reach the table
-- at all. Both are stated explicitly rather than left to Supabase's default
-- privileges, so the reachable surface of these three tables is readable
-- from this file alone. Same shape as 017 section 3d.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.copilot_sessions TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.copilot_messages TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.copilot_sessions TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.copilot_messages TO service_role;
REVOKE ALL ON public.copilot_sessions FROM anon;
REVOKE ALL ON public.copilot_messages FROM anon;

-- The admin dashboard reaches waitlist as `authenticated` over PostgREST, so
-- it needs the three verbs its policies scope. INSERT is revoked from both
-- browser roles to match the absent insert policy: one closed door, stated
-- twice.
GRANT SELECT, UPDATE, DELETE ON public.waitlist TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.waitlist TO service_role;
REVOKE INSERT ON public.waitlist FROM anon, authenticated;
REVOKE ALL ON public.waitlist FROM anon;

-- ==========================================================================
-- SECTION 6 — Documentation on the objects themselves
-- ==========================================================================
COMMENT ON TABLE public.copilot_sessions IS
    'One AI copilot conversation. Written by backend_app/routers/copilot.py as '
    'service_role. Before migration 018 this table did not exist in production and '
    'every write was swallowed by the router''s try/except, so session history was '
    'permanently empty without raising a single error.';

COMMENT ON TABLE public.copilot_messages IS
    'Transcript rows for a copilot session, cascade-deleted with the session. '
    'context_snapshot holds the strategy/market context the message was answered '
    'against, which is what makes a past answer auditable.';

COMMENT ON TABLE public.waitlist IS
    'Pre-launch signup entries. Read, approved and removed by /admin/waitlist via '
    'PostgREST under the app_metadata.role = admin policies. There is deliberately '
    'no public INSERT policy while components/waitlist/WaitlistForm.jsx is '
    'unmounted — add one in the same change that re-mounts the form.';

COMMENT ON COLUMN public.waitlist.ip_address IS
    'Best-effort origin of the signup, as text. Deliberately not INET: a proxy '
    'chain can hand over a comma-separated X-Forwarded-For list, which INET '
    'rejects outright and would turn an audit field into a failed insert.';

COMMIT;
