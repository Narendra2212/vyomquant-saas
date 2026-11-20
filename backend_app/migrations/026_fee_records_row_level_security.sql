-- ==========================================================================
-- 026 — fee_records: declare the table, enable row level security, revoke the
--       browser roles                                     (TENANT-02, Phase 8A)
-- ==========================================================================
--
-- WHAT WAS WRONG
-- --------------
-- `fee_records` carries per-customer trading fees keyed on `tenant_id`, and on the
-- provisioned database it had ROW LEVEL SECURITY DISABLED with `anon` and
-- `authenticated` each holding SELECT, INSERT, UPDATE and DELETE. Measured directly
-- (staging/probe_phase8_db_state.py):
--
--     rls_enabled=False forced=False owner=postgres
--     policies=0
--     grant anon           DELETE,INSERT,SELECT,UPDATE
--     grant authenticated  DELETE,INSERT,SELECT,UPDATE
--
-- Three independent tests in the Phase 7 PostgreSQL lane agreed, and they were precise
-- rather than sweeping — 43 and 69 other tables blocked correctly:
--
--     test_cross_tenant_read_sweep_postgres   fee_records  anon saw 1 of 1 row(s)
--     test_cross_tenant_read_sweep_postgres   fee_records  authenticated saw 1 of 1 row(s)
--     test_production_security_probe_...      fee_records  (b_select=ALLOWED;
--                                                           b_update=ALLOWED;
--                                                           b_delete=ALLOWED)
--
-- WHY IT HAPPENED, AND WHY A MIGRATION IS THE FIX RATHER THAN A GRANT
-- -------------------------------------------------------------------
-- `fee_records` existed ONLY as a SQLAlchemy model — `backend_app/backend/fee_engine.py`
-- `FeeRecordModel` — and was created by `main.py`'s `Base.metadata.create_all()`. Zero
-- `.sql` files in this repository mentioned it. So:
--
--   * the `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` and `CREATE POLICY` statements that
--     every migration-created table receives did not exist for it ANYWHERE; and
--   * `scripts/provision_database.py`'s substrate installs
--     `ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES TO
--     anon, authenticated, service_role`, so the table inherited browser privileges at
--     creation time from a statement that never named it. Re-running the hardening file
--     cannot retract a default privilege already materialised on a table; only a
--     statement naming the table can.
--
-- The existing backstop would have caught this and could not. `999_privilege_hardening.sql`
-- section 4 already lists `tenant_id` among its per-customer columns and RAISEs an
-- EXCEPTION for exactly this shape — but it enumerates `pg_class` during provisioning
-- STEP 4, and `fee_records` does not exist yet at that moment: the application creates it
-- later, at startup. Declaring the table HERE, in step 1, is what puts it in front of that
-- backstop.
--
-- This is the ORM-versus-migration divergence family that
-- `tests/test_orm_models_match_provisioned_schema.py` exists for, in the direction nobody
-- had connected to security. That file describes the usual case as "the ORM silently binds
-- to a table it cannot read". This is the mirror image: the ORM created a table the
-- security model never saw.
--
-- SHOULD `fee_records` BE REACHABLE THROUGH PostgREST AT ALL? NO.
-- ---------------------------------------------------------------
-- Measured, not assumed. Every one of `FeeEngine`'s six methods — `record_fee`,
-- `record_funding_fee`, `get_fee_summary`, `get_fees_for_execution`,
-- `get_fees_for_position` and the two tenant lookups — goes through a single SQLAlchemy
-- `Session`. There is no `.table("fee_records")`, no Supabase client and no psycopg2 row
-- access anywhere in the repository, and no router or worker imports `fee_engine`. So no
-- API endpoint and no browser path reaches this table, and the `anon` / `authenticated`
-- privileges were never load-bearing for anything.
--
-- Both browser roles are therefore revoked outright. That follows the decision already
-- recorded for `payment_methods` and `creator_analytics` in
-- `migrations/hardening/999_privilege_hardening.sql:161-168`: "Served exclusively by the
-- API over SQLAlchemy, so neither browser role needs any privilege; the owner policy is
-- defence in depth for a future path."
--
-- ENABLE, NOT FORCE — AND THIS IS WHAT KEEPS THE APPLICATION WORKING
-- ------------------------------------------------------------------
-- `fee_records` is owned by `postgres` (measured: `owner=postgres`), and every DSN in this
-- repository connects as `postgres`. A table owner is EXEMPT from its own policies unless
-- `FORCE ROW LEVEL SECURITY` is set. So `ENABLE` closes the PostgREST surface — where the
-- request runs as `anon` or `authenticated` — and leaves the SQLAlchemy path untouched.
-- `FORCE` would break all six `FeeEngine` methods and is deliberately not used. Same
-- reasoning, same words, as 999 section 1.
--
-- THE OWNERSHIP KEY IS `tenant_id`, AND IN THIS CODEBASE THAT IS THE USER ID
-- --------------------------------------------------------------------------
-- Not assumed from the column name. `fee_records.tenant_id` is populated exclusively by
-- `FeeEngine._get_tenant_id_from_execution` / `_get_tenant_id_from_position`, which return
-- `ExecutionRecordModel.tenant_id` / `PositionModel.tenant_id`.
-- `backend_app/alembic/versions/add_foreign_keys_20260817.py:87-91` constrains
-- `execution_records.tenant_id` to `profiles(id)`, which is the Supabase mirror of
-- `auth.users(id)`; and `migrations/006_reconcile_production_database.sql:49-50` copies it
-- verbatim into a new `user_id` column (`UPDATE execution_records SET user_id = tenant_id`).
-- 006's own policy then treats the two as interchangeable:
-- `USING (auth.uid() = user_id OR auth.uid() = tenant_id)`, uncast uuid equality.
--
-- `fee_records.tenant_id` is already `uuid` (measured), so the predicate below is a plain
-- uuid comparison with no cast — matching 006 and 009 rather than the `::text` form 999
-- uses for columns that are not uuid.
--
-- ONE HONEST CONSEQUENCE, STATED RATHER THAN HIDDEN
-- -------------------------------------------------
-- `_get_tenant_id_from_execution` and `_get_tenant_id_from_position` fall back to
-- `UUID(int=0)` when the parent row is missing, so a fee row can be written with
-- `tenant_id = '00000000-...-0000'` — an id belonging to no user. Under the owner policy
-- such a row is invisible to every authenticated caller and visible only to
-- `service_role`. That is the fail-closed direction, and it is the correct one for an
-- unattributable row, but it means an orphaned fee is silently unreadable rather than
-- loudly wrong. Reported as FEE-01; not fixed here, because changing the fallback changes
-- what `record_fee` does on an error path and belongs with whoever owns fee accounting.
-- Measured on this database: 0 such rows (`SENTINEL_TENANT_ROWS=0`).
--
-- IDEMPOTENCY
-- -----------
-- Every statement is `IF NOT EXISTS`, `pg_policies`-guarded, or a REVOKE/GRANT/ENABLE,
-- which are idempotent by definition. Nothing drops, renames, deletes or truncates, so
-- `test_no_destructive_statements` holds. Applying this file twice reaches the same state.
--
-- Regression: tests/test_tenant02_fee_records_rls_postgres.py
-- ==========================================================================

BEGIN;

-- ──────────────────────────────────────────────────────────────────────────
-- SECTION 0 — preflight: the substrate this file's policies need
-- ──────────────────────────────────────────────────────────────────────────
-- Same precondition 009 and 020 assert, and for the same reason: a policy naming
-- `authenticated` / `service_role` and calling `auth.uid()` cannot be expressed without
-- them. Refuse with the remedy named rather than creating a table and leaving it
-- unguarded, because an unguarded table is the defect this file exists to close.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        RAISE EXCEPTION
            '026 precondition failed: role "authenticated" does not exist. This file '
            'declares row level security policies that name it. Create the Supabase-like '
            'substrate first: scripts/provision_database.py --create-substrate.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        RAISE EXCEPTION
            '026 precondition failed: role "service_role" does not exist. See '
            'scripts/provision_database.py --create-substrate.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'auth' AND p.proname = 'uid'
    ) THEN
        RAISE EXCEPTION
            '026 precondition failed: auth.uid() does not exist. The owner policy below '
            'is expressed in terms of it. See scripts/provision_database.py '
            '--create-substrate.';
    END IF;
END $$;

-- ──────────────────────────────────────────────────────────────────────────
-- SECTION 1 — the two enum types the model declares
-- ──────────────────────────────────────────────────────────────────────────
-- `SQLEnum(FeeType)` / `SQLEnum(FeeStatus)` emit PostgreSQL enums named `feetype` and
-- `feestatus` whose labels are the Python enum MEMBER NAMES, not their values — so
-- `MAKER`, not `maker`. Measured on the provisioned database:
--
--     feetype=MAKER,TAKER,FUNDING,LIQUIDATION,WITHDRAWAL,DEPOSIT
--     feestatus=PENDING,DEDUCTED,REBATED
--
-- Reproduced here in that exact order and casing. Getting the casing wrong would produce a
-- table the ORM cannot write to, which is the divergence this file is supposed to remove.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'feetype') THEN
        CREATE TYPE public.feetype AS ENUM (
            'MAKER', 'TAKER', 'FUNDING', 'LIQUIDATION', 'WITHDRAWAL', 'DEPOSIT'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'feestatus') THEN
        CREATE TYPE public.feestatus AS ENUM ('PENDING', 'DEDUCTED', 'REBATED');
    END IF;
END $$;

-- ──────────────────────────────────────────────────────────────────────────
-- SECTION 2 — the table, as the model declares it
-- ──────────────────────────────────────────────────────────────────────────
-- Column names, types and nullability are copied from the LIVE schema rather than inferred
-- from the Python types, because the two can disagree and only one of them is what the ORM
-- will bind to. In particular:
--
--   * `fee_id` is `varchar`, NOT `uuid`. `FeeEngine` builds it as
--     `f"fee_{execution_id}_{int(utcnow().timestamp())}"` and
--     `f"fee_funding_{position_id}_{funding_period}"`. A `uuid` column here would reject
--     every write.
--   * `created_at` is `timestamp without time zone` with NO server default — SQLAlchemy
--     supplies it from `default=datetime.utcnow`. Left without a default so this
--     declaration reproduces exactly what `create_all` produces; adding one would make
--     the migration and the ORM disagree about the schema, which is the class of defect
--     `tests/test_orm_models_match_provisioned_schema.py` exists to catch.
--
-- `IF NOT EXISTS`, so on a database where the application already created the table this
-- is a no-op and SECTION 3 verifies the existing shape instead.
CREATE TABLE IF NOT EXISTS public.fee_records (
    fee_id         varchar                     NOT NULL,
    tenant_id      uuid                        NOT NULL,
    execution_id   varchar                     NULL,
    order_id       varchar                     NOT NULL,
    position_id    varchar                     NULL,
    symbol         varchar                     NOT NULL,
    side           varchar                     NOT NULL,
    fee_type       public.feetype              NOT NULL,
    fee_amount     varchar                     NOT NULL,
    fee_asset      varchar                     NOT NULL,
    fee_pct        varchar                     NOT NULL,
    trade_volume   varchar                     NOT NULL,
    gross_pnl      varchar                     NULL,
    net_pnl        varchar                     NULL,
    status         public.feestatus            NOT NULL,
    exchange_id    varchar                     NOT NULL,
    created_at     timestamp without time zone NOT NULL,
    CONSTRAINT fee_records_pkey PRIMARY KEY (fee_id)
);

-- The two foreign keys the model declares. Added separately and guarded, because on a
-- database where `create_all` already made the table they are already present, and because
-- `execution_records` / `positions` are declared by Alembic rather than by this set — if
-- either is absent the constraint is skipped with a notice rather than aborting the file,
-- since the row security below does not depend on it.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fee_records_execution_id_fkey'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'execution_records'
    ) THEN
        ALTER TABLE public.fee_records
            ADD CONSTRAINT fee_records_execution_id_fkey
            FOREIGN KEY (execution_id) REFERENCES public.execution_records(execution_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fee_records_position_id_fkey'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'positions'
    ) THEN
        ALTER TABLE public.fee_records
            ADD CONSTRAINT fee_records_position_id_fkey
            FOREIGN KEY (position_id) REFERENCES public.positions(position_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_fee_records_tenant_symbol
    ON public.fee_records (tenant_id, symbol);
CREATE INDEX IF NOT EXISTS idx_fee_records_execution
    ON public.fee_records (execution_id);
CREATE INDEX IF NOT EXISTS idx_fee_records_position
    ON public.fee_records (position_id);
CREATE INDEX IF NOT EXISTS idx_fee_records_type
    ON public.fee_records (fee_type);
CREATE INDEX IF NOT EXISTS ix_fee_records_tenant_id
    ON public.fee_records (tenant_id);
CREATE INDEX IF NOT EXISTS ix_fee_records_order_id
    ON public.fee_records (order_id);

-- ──────────────────────────────────────────────────────────────────────────
-- SECTION 3 — FAIL SAFELY IF THE SCHEMA IS NOT WHAT THIS FILE ASSUMES
-- ──────────────────────────────────────────────────────────────────────────
-- Everything below is expressed in terms of `tenant_id` being a `uuid` that identifies the
-- owning customer. If the table on this database has a different ownership column, or
-- `tenant_id` is some other type, then the policy would compile and filter the wrong thing
-- — or filter nothing — and the table would LOOK guarded in a catalogue listing while
-- remaining open. That is strictly worse than an unguarded table, and 999's third backstop
-- exists because it has happened here before (`library_ratings`).
--
-- So the assumption is asserted, not assumed. Refusing the migration is the safe outcome:
-- it leaves the finding open and visible instead of closing it falsely.
DO $$
DECLARE
    tenant_type text;
BEGIN
    SELECT c.udt_name INTO tenant_type
      FROM information_schema.columns c
     WHERE c.table_schema = 'public'
       AND c.table_name = 'fee_records'
       AND c.column_name = 'tenant_id';

    IF tenant_type IS NULL THEN
        RAISE EXCEPTION
            '026 refused: public.fee_records has no tenant_id column on this database, so '
            'the owner policy this file declares would filter nothing. The ownership key '
            'must be established before row security can express it. Inspect the table and '
            'update this migration rather than leaving a policy that looks protective.';
    END IF;

    IF tenant_type <> 'uuid' THEN
        RAISE EXCEPTION
            '026 refused: public.fee_records.tenant_id is %, not uuid. The policy below '
            'compares it to auth.uid() as a plain uuid; against another type the '
            'comparison would either fail to compile or silently coerce. Reconcile the '
            'column type first (see migrations/hardening/998_bug049_identity_types.sql '
            'for the established pattern).', tenant_type;
    END IF;
END $$;

-- ──────────────────────────────────────────────────────────────────────────
-- SECTION 4 — row level security
-- ──────────────────────────────────────────────────────────────────────────
-- ENABLE first (default-deny), then the policies, inside this one transaction so no
-- session observes a table with RLS on and no policy, or policies with RLS off.
ALTER TABLE public.fee_records ENABLE ROW LEVEL SECURITY;

-- Guarded on `pg_policies` rather than `DROP POLICY IF EXISTS` + `CREATE POLICY`, following
-- 009: a DROP would open a policy-less window inside the transaction, and there is no
-- `CREATE POLICY IF NOT EXISTS`.
--
-- The owner policy is `FOR SELECT`, not `FOR ALL`. A fee record is written by the platform
-- from an exchange fill; a customer reads their own fees and never authors one. Granting
-- INSERT/UPDATE/DELETE to the owner would describe a client surface that does not exist.
-- Same shape as `payment_methods_owner_access` and `creator_analytics_owner_access`.
--
-- `WITH CHECK` is omitted BECAUSE the policy is SELECT-only — PostgreSQL rejects
-- `WITH CHECK` on a SELECT policy. Where a policy here is `FOR ALL` (service_role) the
-- `WITH CHECK` repeats the `USING` predicate, so a reader cannot mistake its absence for
-- "any new row is allowed".
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'fee_records'
           AND policyname = 'fee_records_owner_access'
    ) THEN
        CREATE POLICY "fee_records_owner_access" ON public.fee_records
            FOR SELECT TO authenticated
            USING (auth.uid() = tenant_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'fee_records'
           AND policyname = 'fee_records_service_role'
    ) THEN
        CREATE POLICY "fee_records_service_role" ON public.fee_records
            FOR ALL TO service_role
            USING (true) WITH CHECK (true);
    END IF;
END $$;

-- ──────────────────────────────────────────────────────────────────────────
-- SECTION 5 — privileges: the layer below row security
-- ──────────────────────────────────────────────────────────────────────────
-- Table privilege is what RLS filters; with no privilege there is nothing to filter, which
-- is why both layers are set rather than one. These REVOKEs are the only thing that can
-- retract the `ALTER DEFAULT PRIVILEGES` grant the substrate materialised on this table at
-- creation time — re-running the hardening file cannot, because that file names tables and
-- never named this one.
--
-- NOTHING IS GRANTED BACK. No code path in this repository reaches `fee_records` other
-- than through SQLAlchemy as `postgres`, so a browser role needs no privilege at all. The
-- owner policy above stays as defence in depth for a future PostgREST read, and the day
-- somebody adds one they must add the matching `GRANT SELECT` deliberately — which is the
-- right amount of friction for exposing a customer's fee history.
REVOKE ALL ON public.fee_records FROM anon;
REVOKE ALL ON public.fee_records FROM authenticated;

-- `service_role` keeps what the backend needs. Stated explicitly rather than inherited, so
-- a future blanket revoke cannot silently break fee recording.
GRANT SELECT, INSERT, UPDATE ON public.fee_records TO service_role;

-- ──────────────────────────────────────────────────────────────────────────
-- SECTION 6 — assert the end state, in this transaction
-- ──────────────────────────────────────────────────────────────────────────
-- The statements above are the intent; this is the verification. It runs inside the same
-- transaction, so a failure rolls the whole file back rather than reporting success on a
-- half-applied state. Deliberately an EXCEPTION, following 999 section 4: "a warning in a
-- provisioning log is a warning nobody reads".
DO $$
DECLARE
    rls_on      boolean;
    policy_cnt  integer;
    browser_cnt integer;
BEGIN
    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relname = 'fee_records';

    IF NOT rls_on THEN
        RAISE EXCEPTION '026 postflight FAILED: row level security is not enabled on '
                        'public.fee_records.';
    END IF;

    SELECT count(*) INTO policy_cnt
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'fee_records'
       AND policyname IN ('fee_records_owner_access', 'fee_records_service_role');

    IF policy_cnt <> 2 THEN
        RAISE EXCEPTION '026 postflight FAILED: expected both fee_records policies, found '
                        '% of 2. RLS with no owner policy denies the owner too, and RLS '
                        'with no service_role policy would break fee recording for any '
                        'caller that is not the table owner.', policy_cnt;
    END IF;

    SELECT count(*) INTO browser_cnt
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND table_name = 'fee_records'
       AND grantee IN ('anon', 'authenticated');

    IF browser_cnt <> 0 THEN
        RAISE EXCEPTION '026 postflight FAILED: a browser role still holds % privilege(s) '
                        'on public.fee_records. The REVOKEs in section 5 are what retract '
                        'the substrate default privileges; if they did not take effect, '
                        'the table is still reachable with the anon key that ships in the '
                        'browser.', browser_cnt;
    END IF;

    RAISE NOTICE '026: fee_records has row level security, an owner policy keyed on '
                 'tenant_id, a service_role policy, and no browser-role privileges.';
END $$;

-- ──────────────────────────────────────────────────────────────────────────
-- SECTION 7 — comments
-- ──────────────────────────────────────────────────────────────────────────
-- COMMENT replaces, so this is idempotent.
COMMENT ON TABLE public.fee_records IS
    'Per-customer trading fees, owned by tenant_id (which in this codebase is the auth '
    'user id -- see 006:49-50 and add_foreign_keys_20260817.py:87-91). Written only by '
    'backend_app/backend/fee_engine.py::FeeEngine through SQLAlchemy as the table owner, '
    'which is why row level security is ENABLED rather than FORCED and why neither '
    'browser role holds any privilege. Declared here rather than only as a SQLAlchemy '
    'model so that it exists before provisioning step 4, where '
    'migrations/hardening/999_privilege_hardening.sql section 4 checks every table '
    'carrying a per-customer column for exactly the exposure this file closes (TENANT-02).';

COMMENT ON COLUMN public.fee_records.tenant_id IS
    'The owning customer. NOT NULL and the subject of fee_records_owner_access. '
    'FeeEngine._get_tenant_id_from_execution / _get_tenant_id_from_position fall back to '
    'the all-zero uuid when the parent execution or position is missing, so an '
    'unattributable fee row is visible only to service_role rather than to a wrong owner '
    '-- fail-closed, and reported as FEE-01.';

COMMIT;
