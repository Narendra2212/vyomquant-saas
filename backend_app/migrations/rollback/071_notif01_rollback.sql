-- ============================================================================
-- rollback/071_notif01_rollback.sql
--
-- Reverses 071_notif01_durable_dedupe_and_severity.sql.
--
-- WHAT THIS DESTROYS. Dropping `idempotency_key` drops the column's data, but
-- NOT the underlying facts: `create_notification` writes the same value into the
-- `metadata` jsonb as well, and this rollback does not touch `metadata`. So the
-- keys survive and the column can be rebuilt by re-applying 071, whose backfill
-- reads exactly that jsonb field. That is deliberate -- the duplicated write is
-- what makes this migration cheaply reversible.
--
-- Take a copy anyway if any row carries a key:
--
--     \copy (SELECT id, user_id, idempotency_key, severity, created_at
--              FROM public.notifications
--             WHERE idempotency_key IS NOT NULL ORDER BY created_at)
--       TO 'notifications_idempotency_backup.csv' WITH CSV HEADER
--
-- AFTER THIS RUNS, state it plainly rather than as a neutral undo:
--
--   * duplicate suppression falls back to `_idempotency_index`, the process-local
--     dict -- so it dies on restart, is invisible to a second task, and evicts
--     the oldest key once 5,000 notifications have passed through the process.
--     That is the NOTIF-01 defect, restored.
--   * `severity` accepts any string again, including values the notification
--     surface has no branch to render.
--
-- The application change that ships with 071 is SAFE ACROSS THIS ROLLBACK and is
-- deliberately written that way: `create_notification` treats a unique violation
-- as "already delivered", and with the index gone no violation is raised, so it
-- simply inserts -- the pre-071 behaviour. It also still writes the key into
-- `metadata`, so nothing in the writer depends on the column existing. No code
-- revert is required to roll this back.
--
-- The privilege change in this same commit lives in
-- migrations/hardening/999_privilege_hardening.sql and is reverted there,
-- independently of this file.
-- ============================================================================

DROP INDEX IF EXISTS public.uq_notifications_user_idempotency;

ALTER TABLE public.notifications
    DROP CONSTRAINT IF EXISTS chk_notifications_idempotency_key_not_blank,
    DROP CONSTRAINT IF EXISTS chk_notifications_severity;

ALTER TABLE public.notifications
    DROP COLUMN IF EXISTS idempotency_key;

DO $$
DECLARE
    leftover integer;
BEGIN
    SELECT count(*) INTO leftover
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'notifications'
       AND column_name = 'idempotency_key';
    IF leftover <> 0 THEN
        RAISE EXCEPTION '071 rollback: notifications.idempotency_key is still present.';
    END IF;

    SELECT count(*) INTO leftover
      FROM pg_constraint
     WHERE conrelid = to_regclass('public.notifications')
       AND conname IN ('chk_notifications_idempotency_key_not_blank',
                       'chk_notifications_severity');
    IF leftover <> 0 THEN
        RAISE EXCEPTION '071 rollback: % constraint(s) still present.', leftover;
    END IF;

    RAISE NOTICE '071 rollback: dedupe is a process-local dict again and severity '
                 'is unbounded. The keys themselves survive in metadata, so '
                 're-applying 071 restores the column from them.';
END $$;
