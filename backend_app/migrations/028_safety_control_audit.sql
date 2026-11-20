-- backend_app/migrations/028_safety_control_audit.sql
--
-- PHASE 9, STEP 1: a durable record of who halted the platform, and who cleared it.
--
-- KILL-02 / KILL-03. WHAT THE AUDIT TRAIL ACTUALLY WAS, MEASURED.
-- ----------------------------------------------------------------
-- Two mechanisms, neither of them a record:
--
--   1. `routers/admin.py::_log_admin_audit_event` (lines 34-63) builds an audit dict and
--      emits `logger.info(..., extra={"audit": ...})`, then best-effort
--      `RPUSH "audit:admin_actions"` inside a `try/except` that only `logger.warning`s.
--      No database row. Unbounded list. Lost entirely if Redis is down at that moment --
--      which, for a kill switch, is a plausible reason somebody is halting the platform.
--
--   2. `core/global_safety.py` LPUSHes to the Redis LIST `system:kill_switch_history` and
--      applies `EXPIRE ... 604800` AT LINE 368 -- inside `activate()` ONLY. So the 7-day
--      clock is anchored to the last ACTIVATION, and a `deactivate()` entry inherits
--      whatever remains of it. A reset recorded six days and 23 hours after the
--      activation survives for one hour. Worse, `_redis_lpush` and `_redis_expire`
--      (global_safety.py:197-224) swallow EVERY exception and return False, so
--      `activate()` can engage the switch and record nothing at all, silently.
--
-- THE RETENTION QUESTION, AND WHY THIS TABLE HAS NO EXPIRY.
-- ---------------------------------------------------------
-- The brief for this phase is explicit: establish the retention requirement from existing
-- product/security requirements, and do NOT invent a duration and call it compliant.
--
-- Searched: `.kiro/**` (which contains only `specs`; there is no steering directory),
-- `docs/**`, and every root `*.md`. Every retention statement found is RELATIVE and
-- therefore circular:
--
--   .kiro/specs/marketplace-subscriptions-paper-trading/requirements.md:186
--     "...retrievable by an Admin_Reviewer for at least the retention period applied to
--      existing Audit_Log records"      <- names no duration; `Audit_Log` has no policy
--   .kiro/specs/marketplace-subscriptions-paper-trading/design.md:759
--     "...so retention matches existing records"            <- same circularity
--
-- The only ABSOLUTE statements the repository makes about records of this kind are:
--
--   requirements.md:387  "THE Marketplace SHALL retain every Subscription row and every
--                         Settlement_Record permanently"
--   alembic d4e5f6a7b8c9:138 / migrations/hardening/998_bug049_identity_types.sql:286
--                        "Financial and audit records that must SURVIVE customer deletion"
--                        (expressed as ON DELETE RESTRICT)
--
-- So: PERMANENT. That is not an invented duration -- it is the absence of one, and it is
-- the only choice consistent with the two absolute statements above. The single numeric
-- precedent in the codebase (604800s) is the generic Redis EVENTS tier
-- (`tests/test_redis_cluster.py:38` pins `DATABASE_TTL[RedisDatabase.EVENTS] == 604800`),
-- which is a class-of-data TTL for ephemeral events and carries no audit authority.
--
-- The Redis list keeps its existing 604800 unchanged. This file does not touch it. What
-- changes is that the list is no longer the record -- it is a fast operational view, and
-- this table is the system of record. That is why the absence of a documented duration
-- stops mattering.
--
-- WHY A NEW TABLE RATHER THAN AN EXISTING ONE.
-- --------------------------------------------
-- There is no `audit_logs` table on this database (measured: the only match for that
-- identifier anywhere is the READER function `execution_guard.get_audit_logs`).
--
-- `risk_settings_audit` exists (migrations/create_risk_settings_tables.sql:72-97) and was
-- rejected: it is keyed `user_id TEXT NOT NULL` with RLS granting read-own to
-- `authenticated`. A platform-wide operator halt has no owning customer, so there is no
-- honest value for `user_id`; and writing "the platform was halted because of X" into a
-- customer-readable per-user table is both a modelling error and an information leak.
--
-- WHAT THIS TABLE IS NOT.
-- -----------------------
-- It is not a replacement for `_log_admin_audit_event`'s Redis list or for the structured
-- log line. Both remain. This is the durable leg of the same record, added because the
-- other two can both be absent at exactly the moment the record matters most.
--
-- Additive. No DROP, no DELETE, no TRUNCATE, no column removal. Idempotent.
-- Regression: tests/test_kill03_authoritative_global_halt.py
-- ==========================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 0 — PREFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
-- `service_role` is the only role this file grants to, so its absence must be a clear
-- refusal rather than a table nobody can write.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        RAISE EXCEPTION
            '028 precondition failed: role "service_role" does not exist. This file '
            'grants the backend its write privilege on the safety audit trail. Create '
            'the substrate first: scripts/provision_database.py --create-substrate.';
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1 — THE TABLE
-- ─────────────────────────────────────────────────────────────────────────────
-- `gen_random_uuid()` is core PostgreSQL from 13 onward, so this does NOT depend on
-- pgcrypto -- which matters, because this substrate logs
-- "pgcrypto is not available on this server" at startup and installs a
-- NON-CRYPTOGRAPHIC `gen_random_bytes()` stand-in. Nothing here uses that stand-in.
--
-- Column notes where the choice is load-bearing:
--
--   `operator_id` is NULLABLE and has NO foreign key. Nullable because the kill switch's
--     four auto-triggers (health monitor, reconciliation engine, idempotency layer,
--     portfolio validator) have no operator and must still be recorded. No FK because an
--     audit row must survive the deletion of the account it names -- the same reasoning
--     the identity migrations state as "audit records that must SURVIVE customer
--     deletion", there expressed as ON DELETE RESTRICT. RESTRICT would be wrong here: it
--     would make an operator account undeletable, which is a different policy than
--     "the record outlives the account".
--
--   `reason` is `text NOT NULL` with a CHECK that it is not blank. The endpoint already
--     refuses an empty reason (admin.py:193-195) and
--     `tests/test_role_granularity_and_audit.py` pins that; the constraint makes it true
--     of every writer rather than of one caller.
--
--   `outcome` distinguishes APPLIED from REFUSED. A REFUSED row is the point: an
--     unauthorized or unverifiable reset attempt is exactly the event an operator needs
--     to see, and the previous design recorded nothing at all for it.
--
--   `detail` is `jsonb`, not a Python `str(dict)` repr. `global_safety.activate()` writes
--     its metadata as `str(activation_metadata)` (global_safety.py:382), which is not
--     machine-readable without `ast.literal_eval`. Audit detail that cannot be queried is
--     not much better than a log line.
CREATE TABLE IF NOT EXISTS public.safety_control_audit (
    audit_id             uuid        NOT NULL DEFAULT gen_random_uuid(),
    control              text        NOT NULL,
    action               text        NOT NULL,
    outcome              text        NOT NULL,
    reason               text        NOT NULL,
    operator_id          uuid        NULL,
    operator_email       text        NULL,
    triggered_by         text        NOT NULL,
    authorization_source text        NULL,
    detail               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    recorded_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT safety_control_audit_pkey PRIMARY KEY (audit_id)
);

-- Constraints added separately and guarded, so a database where the table already exists
-- (created by an earlier run or by `create_all`) still acquires them.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.safety_control_audit')
           AND conname = 'chk_safety_control_audit_action'
    ) THEN
        ALTER TABLE public.safety_control_audit
            ADD CONSTRAINT chk_safety_control_audit_action
            CHECK (action IN ('ACTIVATE', 'DEACTIVATE'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.safety_control_audit')
           AND conname = 'chk_safety_control_audit_outcome'
    ) THEN
        ALTER TABLE public.safety_control_audit
            ADD CONSTRAINT chk_safety_control_audit_outcome
            CHECK (outcome IN ('APPLIED', 'REFUSED'));
    END IF;

    -- A blank reason is the same defect as a missing one.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.safety_control_audit')
           AND conname = 'chk_safety_control_audit_reason_not_blank'
    ) THEN
        ALTER TABLE public.safety_control_audit
            ADD CONSTRAINT chk_safety_control_audit_reason_not_blank
            CHECK (btrim(reason) <> '');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.safety_control_audit')
           AND conname = 'chk_safety_control_audit_triggered_by_not_blank'
    ) THEN
        ALTER TABLE public.safety_control_audit
            ADD CONSTRAINT chk_safety_control_audit_triggered_by_not_blank
            CHECK (btrim(triggered_by) <> '');
    END IF;
END $$;

-- Read patterns this trail is for: "what happened to this control, most recent first"
-- and "what did this operator do".
CREATE INDEX IF NOT EXISTS idx_safety_control_audit_control_time
    ON public.safety_control_audit (control, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_safety_control_audit_recorded_at
    ON public.safety_control_audit (recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_safety_control_audit_operator
    ON public.safety_control_audit (operator_id);
-- Finding a refused attempt must not require a sequential scan of the whole trail.
CREATE INDEX IF NOT EXISTS idx_safety_control_audit_refused
    ON public.safety_control_audit (control, recorded_at DESC)
    WHERE outcome = 'REFUSED';

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2 — ROW LEVEL SECURITY
-- ─────────────────────────────────────────────────────────────────────────────
-- ENABLE, then one policy, in this transaction, so no session observes RLS-on-with-no-
-- policy or policies-with-RLS-off. Same ordering 026 uses and for the same reason.
--
-- ENABLE rather than FORCE, matching 026's measured reasoning: the application connects as
-- the table owner `postgres`, and an owner is exempt from its own policies unless FORCE is
-- set. FORCE here would break the audit write itself -- i.e. halting the platform would
-- fail because its audit row was refused, which is the worst possible direction for this
-- particular control.
ALTER TABLE public.safety_control_audit ENABLE ROW LEVEL SECURITY;

-- SERVICE ROLE ONLY, AND NO OWNER POLICY AT ALL.
--
-- Deliberately unlike `fee_records` (which has an owner policy keyed on tenant_id) and
-- unlike `risk_settings_audit` (which grants read-own to `authenticated`). This trail
-- records platform-wide operator actions and their stated reasons. There is no owning
-- customer, so there is no predicate that could express "your own rows"; and the reason
-- text for a halt routinely names the incident that caused it, which is not customer-
-- facing information. A browser role gets nothing.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'safety_control_audit'
           AND policyname = 'safety_control_audit_service_role'
    ) THEN
        CREATE POLICY "safety_control_audit_service_role"
            ON public.safety_control_audit
            FOR ALL TO service_role
            USING (true) WITH CHECK (true);
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 3 — PRIVILEGES
-- ─────────────────────────────────────────────────────────────────────────────
-- Privilege is what RLS filters; with no privilege there is nothing to filter, so both
-- layers are set. These REVOKEs are also the only thing that can retract the
-- `ALTER DEFAULT PRIVILEGES` grant the substrate materialises on a newly created table.
REVOKE ALL ON public.safety_control_audit FROM anon;
REVOKE ALL ON public.safety_control_audit FROM authenticated;

-- AND FROM service_role, BEFORE granting it back the two privileges it needs.
--
-- This REVOKE is not redundant and the postflight below proved it: without it, section 4
-- failed with
--
--     028 postflight FAILED: service_role holds UPDATE or DELETE on
--     safety_control_audit. An audit trail its own writer can rewrite is not evidence.
--
-- because the substrate's `ALTER DEFAULT PRIVILEGES` materialises a full grant to
-- `service_role` on every newly created table in `public`. A bare
-- `GRANT SELECT, INSERT` is therefore a no-op against that inherited ALL -- it adds two
-- privileges that are already held and silently leaves UPDATE and DELETE in place. The
-- append-only property has to be established by subtraction first.
REVOKE ALL ON public.safety_control_audit FROM service_role;

-- NO UPDATE AND NO DELETE, NOT EVEN FOR service_role.
--
-- An audit trail that its own writer can rewrite is not evidence. The backend needs to
-- append rows and read them back; it never needs to change one. Withholding UPDATE/DELETE
-- at the privilege layer is a stronger statement than a comment asking nobody to do it,
-- and it costs nothing because no code path wants to.
--
-- Note this does not bind the table OWNER (`postgres`), which is how the application
-- connects today -- an owner's privileges cannot be revoked this way. The grant is
-- therefore a statement of intent for any non-owner connection plus a real constraint on
-- PostgREST/service_role access. The owner-side guarantee is the absence of any UPDATE or
-- DELETE statement against this table in the codebase, which
-- tests/test_kill03_authoritative_global_halt.py asserts by scanning the source.
GRANT SELECT, INSERT ON public.safety_control_audit TO service_role;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 4 — POSTFLIGHT, IN THIS TRANSACTION
-- ─────────────────────────────────────────────────────────────────────────────
-- `CREATE TABLE IF NOT EXISTS` silently accepts a pre-existing table of a DIFFERENT
-- shape. A migration that can quietly leave a weaker invariant than it claims is worse
-- than one that fails, so every property this file is supposed to establish is read back
-- from the catalogue and asserted. A mismatch rolls the whole file back.
DO $$
DECLARE
    rls_on      boolean;
    policy_cnt  integer;
    browser_cnt integer;
    writable    integer;
    missing     text;
BEGIN
    -- Columns, with their types and nullability.
    SELECT string_agg(expected, ', ') INTO missing
      FROM (
        SELECT e.col AS expected
          FROM (VALUES
                ('audit_id'),       ('control'),     ('action'),
                ('outcome'),        ('reason'),      ('operator_id'),
                ('operator_email'), ('triggered_by'),('authorization_source'),
                ('detail'),         ('recorded_at')
               ) AS e(col)
         WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns c
             WHERE c.table_schema = 'public'
               AND c.table_name   = 'safety_control_audit'
               AND c.column_name  = e.col
         )
      ) q;

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            '028 postflight FAILED: public.safety_control_audit is missing column(s): %. '
            'A pre-existing table of another shape was left in place by '
            'CREATE TABLE IF NOT EXISTS.', missing;
    END IF;

    -- `recorded_at` must default to now(), or a writer that forgets it produces an
    -- undated audit row.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'safety_control_audit'
           AND column_name = 'recorded_at'
           AND is_nullable = 'NO'
           AND column_default LIKE '%now()%'
    ) THEN
        RAISE EXCEPTION
            '028 postflight FAILED: safety_control_audit.recorded_at is nullable or has '
            'no now() default, so an audit row could carry no timestamp.';
    END IF;

    -- The four CHECKs.
    IF (SELECT count(*) FROM pg_constraint
         WHERE conrelid = to_regclass('public.safety_control_audit')
           AND contype = 'c'
           AND conname IN ('chk_safety_control_audit_action',
                           'chk_safety_control_audit_outcome',
                           'chk_safety_control_audit_reason_not_blank',
                           'chk_safety_control_audit_triggered_by_not_blank')) <> 4 THEN
        RAISE EXCEPTION
            '028 postflight FAILED: expected all four CHECK constraints on '
            'safety_control_audit. Without them a blank reason or an unknown action can '
            'be recorded, and the trail stops being queryable by outcome.';
    END IF;

    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relname = 'safety_control_audit';
    IF NOT rls_on THEN
        RAISE EXCEPTION
            '028 postflight FAILED: row level security is not enabled on '
            'public.safety_control_audit.';
    END IF;

    SELECT count(*) INTO policy_cnt
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'safety_control_audit';
    IF policy_cnt <> 1 THEN
        RAISE EXCEPTION
            '028 postflight FAILED: expected exactly one policy on '
            'safety_control_audit (service_role), found %. An extra policy on this table '
            'would be a read path for the halt reasons that nothing asked for.',
            policy_cnt;
    END IF;

    SELECT count(*) INTO browser_cnt
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND table_name = 'safety_control_audit'
       AND grantee IN ('anon', 'authenticated');
    IF browser_cnt <> 0 THEN
        RAISE EXCEPTION
            '028 postflight FAILED: a browser role holds % privilege(s) on '
            'safety_control_audit. The REVOKEs in section 3 are what retract the '
            'substrate default privileges; if they did not take effect, the halt reasons '
            'are readable with the anon key that ships in the browser.', browser_cnt;
    END IF;

    -- The append-only intent, asserted rather than commented.
    SELECT count(*) INTO writable
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND table_name = 'safety_control_audit'
       AND grantee = 'service_role'
       AND privilege_type IN ('UPDATE', 'DELETE');
    IF writable <> 0 THEN
        RAISE EXCEPTION
            '028 postflight FAILED: service_role holds UPDATE or DELETE on '
            'safety_control_audit. An audit trail its own writer can rewrite is not '
            'evidence.';
    END IF;

    RAISE NOTICE
        '028: safety_control_audit exists, append-only for service_role, row security '
        'enabled with one service_role policy, no browser privileges, and no expiry '
        '(retention is permanent -- see this file''s header for why no duration is '
        'declared).';
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 5 — COMMENTS
-- ─────────────────────────────────────────────────────────────────────────────
-- COMMENT replaces, so this is idempotent.
COMMENT ON TABLE public.safety_control_audit IS
    'Durable, append-only record of every activation and reset of a platform safety '
    'control, and of every attempt that was refused. KILL-02/KILL-03: before this table '
    'the only records were a structured log line and two best-effort Redis LISTs, one of '
    'them with an EXPIRE applied only on activation (global_safety.py:368) and all of '
    'them written through helpers that swallow every exception -- so the platform could '
    'be halted with no record at all. RETENTION IS PERMANENT and no duration is '
    'declared: the repository contains no authoritative retention policy for safety '
    'audit records (every statement found is relative), and permanence is the only '
    'choice consistent with its two absolute statements about financial and audit rows. '
    'Not customer-facing: reason text names the incident, so no browser role holds any '
    'privilege and there is deliberately no owner policy.';

COMMENT ON COLUMN public.safety_control_audit.outcome IS
    'APPLIED or REFUSED. A REFUSED row is the point of this column: an unauthorized or '
    'unverifiable reset attempt on an emergency control is exactly the event an operator '
    'needs to see, and nothing recorded it before.';

COMMENT ON COLUMN public.safety_control_audit.operator_id IS
    'The authenticated operator, or NULL for the kill switch''s automatic triggers '
    '(health monitor, reconciliation engine, idempotency layer, portfolio validator), '
    'which have no operator. No foreign key, deliberately: this row must outlive the '
    'account it names, and ON DELETE RESTRICT would instead make that account '
    'undeletable -- a different policy.';

COMMENT ON COLUMN public.safety_control_audit.authorization_source IS
    'How the actor''s authority was established for this transition -- e.g. the FastAPI '
    'dependency that resolved the operator role. Recorded because KILL-02 was that '
    'GlobalKillSwitch.deactivate() accepted a free-text triggered_by that nothing '
    'verified, so the trail could not distinguish an authorized reset from a claimed one.';

COMMIT;
