-- ============================================================================
-- 073_notif02_rollback.sql — undo 073_notif02_mandatory_alert_floor.sql
--
-- READ THIS BEFORE RUNNING IT. Rolling back is a REGRESSION, not a neutral
-- undo. After this runs:
--
--   * a customer can again store `kill_switch_activated: false`,
--     `stop_loss_triggered: false` or `new_login_detected: false`, so the
--     emergency-halt, stop-loss and new-login alerts become switchable off;
--   * the sentence `components/NotificationPreferences.jsx` prints — "Mandatory
--     risk alerts (global kill switch triggers) cannot be disabled" — goes back
--     to being false;
--   * `routers/user.py`'s refusal still rejects such a payload, so the only
--     remaining enforcement is the one layer that can be bypassed by any other
--     client writing the table directly through PostgREST.
--
-- NO DATA IS LOST by this rollback: it drops a constraint and restores two
-- column defaults. Nothing is deleted and no stored preference changes.
--
-- The defaults are restored to the ORIGINAL declared values even though no row
-- has ever carried them, because a rollback's job is to restore the prior state
-- rather than to keep the half of the change it agrees with.
-- ============================================================================

BEGIN;

ALTER TABLE public.notification_settings
    DROP CONSTRAINT IF EXISTS chk_notification_settings_mandatory_alerts;

ALTER TABLE public.notification_settings
    ALTER COLUMN events SET DEFAULT
        '{"system": true, "stop_loss": true, "risk_alerts": true, "trade_fills": true}'::jsonb;

ALTER TABLE public.notification_settings
    ALTER COLUMN channels SET DEFAULT
        '{"push": true, "email": true, "in_app": true, "telegram": false}'::jsonb;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.notification_settings')
           AND conname  = 'chk_notification_settings_mandatory_alerts'
    ) THEN
        RAISE EXCEPTION
            '073 rollback FAILED: the constraint is still present.';
    END IF;
    RAISE NOTICE
        '073 rolled back: a mandatory safety alert can be switched off again at '
        'the database. routers/user.py still refuses it; nothing else does.';
END $$;

COMMIT;
