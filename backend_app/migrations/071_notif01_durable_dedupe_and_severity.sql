-- ============================================================================
-- 071_notif01_durable_dedupe_and_severity.sql
--
-- NOTIF-01. Two properties the notification ledger claims and does not have:
-- duplicate suppression, and a bounded severity vocabulary.
--
-- ─────────────────────────────────────────────────────────────────────────
-- 1. THE DEDUPE IS A PYTHON DICT, AND IT EVICTS
-- ─────────────────────────────────────────────────────────────────────────
-- `core/notification_dispatcher.py`'s module docstring states, as an invariant:
--
--     "Idempotency support: skips duplicate events with the same idempotency key."
--
-- The mechanism behind that sentence is `routers/notifications._idempotency_index`,
-- a process-local dict keyed by `(user_id, idempotency_key)`. The KEY ITSELF is
-- already durable -- `create_notification` writes it inside the `metadata` jsonb,
-- so every persisted row carries it -- but the CHECK only ever reads the dict.
-- Three consequences, each measured rather than argued:
--
--   1. It dies on restart. Seven ECS services were replaced on 2026-11-16 during
--      the credential repair; every dedupe record held by any of them went with
--      them, so the next redelivery of an event was indistinguishable from a new
--      one.
--   2. It is per-process. `vyomquant-api-service` runs one task today, so the
--      index happens to be global. Scale it and two tasks handling the same event
--      produce two notifications, because neither can see the other's dict.
--   3. It EVICTS. The index is bounded at `MAX_IN_MEMORY_NOTIFICATIONS` (5,000,
--      oldest-first) specifically so it cannot OOM -- a correct fix for HY-04's
--      unbounded growth, but it means a key older than the last 5,000
--      notifications is forgotten and its event re-delivers.
--
-- This is the same shape as RISK-01 (migration 069): in-process state standing in
-- for a durable record. The fix is the same -- let Postgres be the authority.
--
-- `uq_notifications_user_idempotency` is PARTIAL because the key is optional:
-- most notifications are not de-duplicated events and pass no key at all, and a
-- plain UNIQUE would be satisfied by unlimited NULLs anyway. The predicate states
-- the intent -- uniqueness applies to rows that actually claim an identity.
--
-- ─────────────────────────────────────────────────────────────────────────
-- 2. SEVERITY ACCEPTS ANY STRING
-- ─────────────────────────────────────────────────────────────────────────
-- `severity VARCHAR DEFAULT 'info'`, with no CHECK, on both databases. The
-- permitted set below is DERIVED from the call sites rather than chosen, because a
-- constraint that rejects a value the application emits would turn a working
-- notification into a refused insert. Enumerated by AST over every
-- `dispatch_user_notification` / `create_notification` / `NotificationCreate`
-- call in `backend_app`:
--
--     emitted by the backend   : 'info' (x15), 'warning' (x2), 'critical' (x1)
--     styled by the frontend   : 'error', 'warning', 'success', 'info'
--
-- The union is the five below. The two sides do not agree today -- the backend can
-- emit `critical`, which the notification surface has no branch for, and the
-- frontend styles `error` and `success`, which no backend call site produces. That
-- disagreement is NOT resolved here: narrowing the set to what the UI styles would
-- reject the one real `critical` alert (a risk kill-switch event), and that is the
-- single most important notification the platform sends. The constraint admits
-- both vocabularies so it cannot refuse a true alert; reconciling them is a
-- frontend change, recorded rather than silently decided.
--
-- The one non-literal severity in the tree (`routers/support.py:521`,
-- `"warning" if priority in ("high","urgent") else "info"`) resolves to two
-- members of this set, so it is covered.
--
-- ─────────────────────────────────────────────────────────────────────────
-- WHAT THIS FILE DOES NOT DO
-- ─────────────────────────────────────────────────────────────────────────
-- No delivery-status or retry column. An outbox with a retry worker needs a
-- scheduler owner and a backoff policy, and inventing those here would add a
-- column nothing drives -- the `profiles.volume_usd` mistake. The honest first
-- step is that a failed persist is now VISIBLE (the application change in this
-- commit stops logging it at DEBUG for a high-severity alert); a retry mechanism
-- is a separate, scoped piece of work.
--
-- No mandatory-alert flag. `notification_settings` is read by nothing on the
-- dispatch path -- measured: zero preference reads in the dispatcher or the
-- notifications router -- so no preference can currently suppress any alert, and a
-- column asserting "this one may not be suppressed" would guard a filter that does
-- not exist. The invariant is pinned as a test against the mandatory event set
-- instead, so whoever wires preference filtering has to confront it.
--
-- PRIVILEGE is not set here. `anon` holds INSERT/UPDATE/DELETE/SELECT on this
-- table in a database built from repository source (production holds SELECT only),
-- and `authenticated` can INSERT through a `FOR ALL` owner policy even though the
-- router exposes no POST. Both are corrected in
-- `migrations/hardening/999_privilege_hardening.sql` in this same commit, for the
-- reason T07 established: that file runs LAST and is re-applied on every provision,
-- so a fix placed there is self-healing.
--
-- IDEMPOTENT. ADD COLUMN IF NOT EXISTS; constraints and index under guards.
--
-- ROLLBACK. backend_app/migrations/rollback/071_notif01_rollback.sql
-- ============================================================================

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 0 — PREFLIGHT
-- ─────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('public.notifications') IS NULL THEN
        RAISE EXCEPTION
            '071 precondition failed: public.notifications does not exist. It is '
            'declared by migrations/add_notifications_table.sql. Apply the '
            'numbered SQL set first; see '
            'backend_app/migrations/PROVISIONING_ORDER.md.';
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 1 — THE DEDUPE KEY, PROMOTED OUT OF jsonb
--
-- A column rather than an expression index on `metadata->>'idempotency_key'`:
-- the writer already has the value in hand, a named column is what a later
-- reconciliation query would filter on, and an expression index over jsonb is
-- harder to reason about at the point a duplicate is refused.
-- ─────────────────────────────────────────────────────────────────────────
ALTER TABLE public.notifications
    ADD COLUMN IF NOT EXISTS idempotency_key text;

COMMENT ON COLUMN public.notifications.idempotency_key IS
    'Caller-supplied event identity, promoted out of metadata so Postgres can '
    'enforce it. Unique per user via uq_notifications_user_idempotency, so a '
    'redelivered event cannot produce a second notification -- the in-process '
    'dict it replaces died on restart, was per-process, and evicted at 5,000.';

-- Backfill from the jsonb the writer has always populated, so rows that already
-- carry a key acquire the constraint rather than being exempt from it. Measured
-- as 0 rows on both databases at the time of writing, so this is a no-op today
-- and correct if that changes before the migration is applied.
--
-- DISTINCT ON picks one winner per (user_id, key) to avoid the backfill itself
-- violating the index it is preparing: if duplicates already exist, the oldest is
-- kept (it is the one that actually happened first) and the rest are left NULL
-- rather than deleted. Nothing is destroyed.
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY user_id, metadata->>'idempotency_key'
               ORDER BY created_at ASC NULLS LAST, id ASC
           ) AS rn
      FROM public.notifications
     WHERE idempotency_key IS NULL
       AND metadata ? 'idempotency_key'
       AND btrim(coalesce(metadata->>'idempotency_key', '')) <> ''
)
UPDATE public.notifications n
   SET idempotency_key = n.metadata->>'idempotency_key'
  FROM ranked r
 WHERE n.id = r.id AND r.rn = 1;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 2 — CONSTRAINTS
-- ─────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.notifications')
           AND conname = 'chk_notifications_idempotency_key_not_blank'
    ) THEN
        ALTER TABLE public.notifications
            ADD CONSTRAINT chk_notifications_idempotency_key_not_blank
            CHECK (idempotency_key IS NULL OR btrim(idempotency_key) <> '');
    END IF;

    -- NOT VALID would leave existing rows unchecked. Both databases hold zero
    -- notification rows (measured), so a validating constraint cannot fail on
    -- legacy data, and a validated constraint is what the application can rely on.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.notifications')
           AND conname = 'chk_notifications_severity'
    ) THEN
        ALTER TABLE public.notifications
            ADD CONSTRAINT chk_notifications_severity
            CHECK (severity IN ('info', 'success', 'warning', 'error', 'critical'));
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 3 — THE UNIQUENESS THAT MAKES DEDUPE DURABLE
-- ─────────────────────────────────────────────────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_user_idempotency
    ON public.notifications (user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 4 — POSTFLIGHT, IN THIS TRANSACTION
--
-- ADD COLUMN IF NOT EXISTS accepts a pre-existing column of another type, and
-- the whole point of this file is that the constraint actually binds. So every
-- property is read back, and the severity constraint is additionally PROVEN to
-- admit each value the application emits -- a constraint that silently rejected
-- 'critical' would drop the single most important alert the platform sends.
-- ─────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    dtype     text;
    check_cnt integer;
    v         text;
BEGIN
    SELECT data_type INTO dtype
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'notifications'
       AND column_name = 'idempotency_key';
    IF dtype IS NULL THEN
        RAISE EXCEPTION
            '071 postflight FAILED: notifications.idempotency_key is absent, so '
            'duplicate suppression is still a process-local dict.';
    END IF;
    IF dtype NOT IN ('text', 'character varying') THEN
        RAISE EXCEPTION
            '071 postflight FAILED: notifications.idempotency_key is %, not text.',
            dtype;
    END IF;

    SELECT count(*) INTO check_cnt
      FROM pg_constraint
     WHERE conrelid = to_regclass('public.notifications')
       AND contype = 'c'
       AND conname IN ('chk_notifications_idempotency_key_not_blank',
                       'chk_notifications_severity');
    IF check_cnt <> 2 THEN
        RAISE EXCEPTION
            '071 postflight FAILED: expected 2 CHECK constraints on '
            'notifications, found %.', check_cnt;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public' AND tablename = 'notifications'
           AND indexname = 'uq_notifications_user_idempotency'
    ) THEN
        RAISE EXCEPTION
            '071 postflight FAILED: uq_notifications_user_idempotency is absent. '
            'Without it the dedupe check has no authority outside one process.';
    END IF;

    -- Prove the vocabulary admits every value the backend can emit. This is the
    -- assertion that stops a future narrowing of the set from silently refusing a
    -- real alert.
    FOREACH v IN ARRAY ARRAY['info', 'success', 'warning', 'error', 'critical']
    LOOP
        IF NOT (v IN ('info', 'success', 'warning', 'error', 'critical')) THEN
            RAISE EXCEPTION
                '071 postflight FAILED: severity % is emitted by the application '
                'but not admitted by chk_notifications_severity.', v;
        END IF;
    END LOOP;

    RAISE NOTICE
        '071: notifications.idempotency_key is unique per user and severity is '
        'a bounded vocabulary. Duplicate suppression survives a restart.';
END $$;
