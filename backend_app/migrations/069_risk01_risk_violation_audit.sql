-- ============================================================================
-- 069_risk01_risk_violation_audit.sql
--
-- RISK-01. Every risk refusal was recorded in a Python dictionary and a log
-- line, and nowhere else:
--
--     _risk_violations_log.setdefault(uid, []).append(entry)   risk.py:100
--     logger.warning(f"[RISK_VIOLATION] ...")                  risk.py:102
--
-- `GET /api/risk/violations` (risk.py:872) reads that dictionary. Three
-- consequences, all of them measured rather than argued:
--
--   1. The trail dies on restart. Seven ECS services were replaced on
--      2026-11-16 at 19:08-19:13 during the credential repair; every refusal
--      any of them had recorded was lost at that moment.
--   2. The trail is per-process. `vyomquant-api-service` runs one task today,
--      but the endpoint can only ever answer for the task that happened to
--      serve the refusal. Scale the service and the endpoint starts returning
--      a different answer per request.
--   3. It is unbounded. `setdefault(uid, []).append(...)` has no cap, so a
--      strategy refused on every bar grows the dictionary until the task is
--      OOM-killed.
--
-- The callers are real. `backend/paper_trading_service.py` records eight
-- distinct rules through this function -- KILL_SWITCH_ACTIVE,
-- MAX_DRAWDOWN_EXCEEDED, MAX_DAILY_LOSS_EXCEEDED, MAX_POSITIONS_EXCEEDED,
-- CONSECUTIVE_LOSS_LIMIT_EXCEEDED, CAPITAL_UTILIZATION_LIMIT_EXCEEDED,
-- SYMBOL_RESTRICTED, MAX_POSITION_SIZE_EXCEEDED -- plus
-- KILL_SWITCH_MANUAL_ACTIVATION from risk.py:726. These are the records that
-- answer "why did my bot not trade", and they were never durable.
--
-- WHY NOT public.safety_control_audit (migration 028).
-- That was the obvious candidate and it is the wrong one. 028's own header
-- records the reasoning that excludes this use:
--
--     "A platform-wide operator halt has no owning customer, so there is no
--      honest value for `user_id`"
--
-- Accordingly 028's table has no `user_id` column at all -- only
-- `operator_id` -- and its RLS deliberately carries NO owner policy, because
-- "there is no predicate that could express 'your own rows'". A risk refusal
-- is the opposite: it belongs to exactly one customer, and `/violations` is a
-- customer-facing endpoint. Writing these rows there would store
-- customer-scoped data in a table the customer can never read, and would
-- make a platform-operator trail answerable for per-user events. 028 stays
-- for what it is for.
--
-- WHY NOT public.risk_settings_audit (create_risk_settings_tables.sql:72-97).
-- Closer -- it is user-keyed with read-own RLS -- but two problems. It is
-- scoped to *settings changes* (old value, new value), which a refusal is
-- not; and it declares `user_id TEXT NOT NULL` while `auth.users.id`,
-- `public.strategies.user_id` and the rest of this schema are `uuid`. That is
-- the identity-type defect `migrations/hardening/998_bug049_identity_types.sql`
-- exists to correct. Adding a new writer to a mistyped column would widen a
-- defect the repository is already retiring.
--
-- SHAPE NOTES.
--   user_id     uuid, FK to auth.users with ON DELETE CASCADE. uuid because
--               every other identity column in this schema is uuid after
--               BUG-049; the FK because an orphaned refusal names nobody.
--   rule        the machine-readable rule key (the eight above). text rather
--               than an enum so a new rule does not require a migration, with
--               a not-blank CHECK so it cannot be empty.
--   reason      human-readable, not blank. Same reasoning as 028's
--               chk_safety_control_audit_reason_not_blank: a blank reason is
--               the same defect as a missing one.
--   detail      jsonb, not text. Audit detail that cannot be queried is not
--               much better than a log line -- 028's phrasing, and it applies
--               identically here. The existing callers pass dicts.
--   recorded_at timestamptz default now().
--
-- APPEND-ONLY, like 028. No UPDATE and no DELETE are granted to any role. The
-- REVOKE before the GRANT is not redundant: the substrate's
-- ALTER DEFAULT PRIVILEGES materialises a full grant to service_role on every
-- newly created table in public, so a bare GRANT SELECT, INSERT would be a
-- no-op against that inherited ALL and would silently leave UPDATE and DELETE
-- in place. 028's postflight proved this; the same subtraction is applied.
--
-- RETENTION IS NOT SOLVED HERE, stated so a reader is not misled. The
-- unbounded-growth problem moves from process memory to a table, which is a
-- strict improvement (bounded by disk, survives restart, queryable) but is
-- not a retention policy. A refusal-per-bar strategy still accumulates rows.
-- The partial index below makes a time-bounded delete cheap when a policy is
-- chosen, and the owner-facing read is already capped by the endpoint's
-- `limit`.
--
-- PROVISIONING ORDER. Needs `auth.users`, which real Supabase provides and
-- which `scripts/provision_database.py --create-substrate` synthesises
-- locally. See backend_app/migrations/PROVISIONING_ORDER.md.
--
-- IDEMPOTENT. IF NOT EXISTS throughout; constraints added under guards so a
-- database where the table already exists still acquires them.
--
-- ROLLBACK. backend_app/migrations/rollback/069_risk01_rollback.sql
-- ============================================================================

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 0 — PREFLIGHT
-- ─────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('auth.users') IS NULL THEN
        RAISE EXCEPTION
            '069 precondition failed: auth.users does not exist. This file '
            'keys the risk-violation trail to it. Create the substrate '
            'first: scripts/provision_database.py --create-substrate.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        RAISE EXCEPTION
            '069 precondition failed: role "service_role" does not exist. '
            'This file grants the backend its write privilege on the '
            'risk-violation trail. Create the substrate first: '
            'scripts/provision_database.py --create-substrate.';
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 1 — THE TABLE
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.risk_violation_audit (
    violation_id uuid        NOT NULL DEFAULT gen_random_uuid(),
    user_id      uuid        NOT NULL,
    rule         text        NOT NULL,
    reason       text        NOT NULL,
    detail       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT risk_violation_audit_pkey PRIMARY KEY (violation_id)
);

-- Constraints added separately and guarded, so a database where the table
-- already exists (earlier run, or create_all) still acquires them.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.risk_violation_audit')
           AND conname = 'risk_violation_audit_user_id_fkey'
    ) THEN
        ALTER TABLE public.risk_violation_audit
            ADD CONSTRAINT risk_violation_audit_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.risk_violation_audit')
           AND conname = 'chk_risk_violation_audit_rule_not_blank'
    ) THEN
        ALTER TABLE public.risk_violation_audit
            ADD CONSTRAINT chk_risk_violation_audit_rule_not_blank
            CHECK (btrim(rule) <> '');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.risk_violation_audit')
           AND conname = 'chk_risk_violation_audit_reason_not_blank'
    ) THEN
        ALTER TABLE public.risk_violation_audit
            ADD CONSTRAINT chk_risk_violation_audit_reason_not_blank
            CHECK (btrim(reason) <> '');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.risk_violation_audit')
           AND conname = 'chk_risk_violation_audit_detail_object'
    ) THEN
        ALTER TABLE public.risk_violation_audit
            ADD CONSTRAINT chk_risk_violation_audit_detail_object
            CHECK (jsonb_typeof(detail) = 'object');
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 2 — INDEXES
--
-- The endpoint's only read is "my violations, most recent first, limited",
-- so the composite index matches it exactly and serves it as one index scan
-- rather than a per-user table scan. The rule index answers "how often is
-- this limit hit", which is the question that tells an operator whether a
-- default is wrong.
-- ─────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_risk_violation_audit_user_time
    ON public.risk_violation_audit (user_id, recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_risk_violation_audit_rule_time
    ON public.risk_violation_audit (rule, recorded_at DESC);

-- Kill-switch events are the ones an incident review starts from, so finding
-- them must not require scanning every drawdown refusal.
CREATE INDEX IF NOT EXISTS idx_risk_violation_audit_kill_switch
    ON public.risk_violation_audit (user_id, recorded_at DESC)
    WHERE rule LIKE 'KILL_SWITCH%';

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 3 — RLS
--
-- Unlike 028, an owner policy IS correct here: every row has exactly one
-- owning customer and the customer is entitled to read it. SELECT only --
-- there is deliberately no owner INSERT, UPDATE or DELETE policy, because a
-- user who can write their own risk trail can also launder it.
-- ─────────────────────────────────────────────────────────────────────────
ALTER TABLE public.risk_violation_audit ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public' AND tablename = 'risk_violation_audit'
           AND policyname = 'risk_violation_audit_owner_select'
    ) THEN
        CREATE POLICY risk_violation_audit_owner_select
            ON public.risk_violation_audit
            FOR SELECT TO authenticated
            USING (user_id = auth.uid());
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public' AND tablename = 'risk_violation_audit'
           AND policyname = 'risk_violation_audit_service_all'
    ) THEN
        CREATE POLICY risk_violation_audit_service_all
            ON public.risk_violation_audit
            FOR ALL TO service_role
            USING (true) WITH CHECK (true);
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 4 — PRIVILEGE
--
-- Privilege is what RLS filters; with no privilege there is nothing to
-- filter, so both layers are set. The REVOKE before each GRANT is what
-- retracts the substrate's ALTER DEFAULT PRIVILEGES grant -- see the header.
-- ─────────────────────────────────────────────────────────────────────────
REVOKE ALL ON public.risk_violation_audit FROM anon;
REVOKE ALL ON public.risk_violation_audit FROM authenticated;
REVOKE ALL ON public.risk_violation_audit FROM service_role;

-- The owner reads their own rows (RLS narrows this to user_id = auth.uid()).
GRANT SELECT ON public.risk_violation_audit TO authenticated;

-- The backend appends. No UPDATE, no DELETE, not even for service_role: an
-- audit trail its own writer can rewrite is not evidence.
GRANT SELECT, INSERT ON public.risk_violation_audit TO service_role;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 5 — POSTFLIGHT, IN THIS TRANSACTION
--
-- CREATE TABLE IF NOT EXISTS silently accepts a pre-existing table of a
-- DIFFERENT shape, so every property this file claims is read back.
-- ─────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    missing    text;
    check_cnt  integer;
    writable   integer;
    anon_cnt   integer;
    uid_type   text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
      FROM unnest(ARRAY['violation_id', 'user_id', 'rule', 'reason',
                        'detail', 'recorded_at']) AS c
     WHERE NOT EXISTS (
         SELECT 1 FROM information_schema.columns
          WHERE table_schema = 'public' AND table_name = 'risk_violation_audit'
            AND column_name = c
     );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            '069 postflight FAILED: public.risk_violation_audit is missing '
            'column(s): %. A pre-existing table of another shape was left in '
            'place by CREATE TABLE IF NOT EXISTS.', missing;
    END IF;

    -- The uuid typing is the whole reason this table exists rather than
    -- reusing risk_settings_audit, so it is asserted, not assumed.
    SELECT data_type INTO uid_type
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'risk_violation_audit'
       AND column_name = 'user_id';
    IF uid_type <> 'uuid' THEN
        RAISE EXCEPTION
            '069 postflight FAILED: risk_violation_audit.user_id is %, not '
            'uuid. This table exists specifically to avoid the TEXT identity '
            'typing that 998_bug049_identity_types.sql is retiring.', uid_type;
    END IF;

    SELECT count(*) INTO check_cnt
      FROM pg_constraint
     WHERE conrelid = to_regclass('public.risk_violation_audit')
       AND contype = 'c'
       AND conname IN ('chk_risk_violation_audit_rule_not_blank',
                       'chk_risk_violation_audit_reason_not_blank',
                       'chk_risk_violation_audit_detail_object');
    IF check_cnt <> 3 THEN
        RAISE EXCEPTION
            '069 postflight FAILED: expected 3 CHECK constraints on '
            'risk_violation_audit, found %. Without them a blank rule or a '
            'non-object detail can be recorded and the trail stops being '
            'queryable.', check_cnt;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.risk_violation_audit')
           AND contype = 'f'
    ) THEN
        RAISE EXCEPTION
            '069 postflight FAILED: risk_violation_audit has no foreign key '
            'to auth.users. An orphaned refusal names nobody.';
    END IF;

    SELECT count(*) INTO anon_cnt
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND table_name = 'risk_violation_audit'
       AND grantee = 'anon';
    IF anon_cnt <> 0 THEN
        RAISE EXCEPTION
            '069 postflight FAILED: anon holds % privilege(s) on '
            'risk_violation_audit. The REVOKEs in section 4 are what retract '
            'the substrate default privileges; if they did not take effect, '
            'every customer''s risk refusals are readable with the anon key '
            'that ships in the browser.', anon_cnt;
    END IF;

    SELECT count(*) INTO writable
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND table_name = 'risk_violation_audit'
       AND grantee IN ('service_role', 'authenticated')
       AND privilege_type IN ('UPDATE', 'DELETE');
    IF writable <> 0 THEN
        RAISE EXCEPTION
            '069 postflight FAILED: % UPDATE/DELETE privilege(s) remain on '
            'risk_violation_audit. An audit trail its own writer can rewrite '
            'is not evidence. Section 4''s REVOKE ALL before GRANT is what '
            'subtracts the substrate''s inherited ALL.', writable;
    END IF;

    RAISE NOTICE '069: public.risk_violation_audit present, uuid-keyed, '
                 'append-only, owner-readable. Risk refusals are durable.';
END $$;
