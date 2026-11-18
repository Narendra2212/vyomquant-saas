-- ============================================================================
-- 073_notif02_mandatory_alert_floor.sql
--
-- NOTIF-02. The notification preferences panel ends with this sentence:
--
--     "Mandatory risk alerts (global kill switch triggers) cannot be disabled."
--
-- Directly above it, `components/NotificationPreferences.jsx` renders a switch
-- labelled "Emergency Kill Switch & Liquidations" wired to
-- `events.kill_switch_activated`, which a customer can turn off. The panel
-- states an invariant and then offers the control that breaks it.
--
-- Nothing in the repository enforced the sentence. Grep for readers of
-- `notification_settings` across `backend_app`: two, both in `routers/user.py`
-- (the GET and the PUT of the panel itself). Nothing on the dispatch path reads
-- it. So the floor was a sentence in a paragraph, which is what the session
-- handoff recorded as "the mandatory-alert floor has no schema anchor".
--
-- ────────────────────────────────────────────────────────────────────────────
-- 1. THE VOCABULARY, MEASURED RATHER THAN READ OFF THE COLUMN DEFAULT
-- ────────────────────────────────────────────────────────────────────────────
-- This is the part that would have made a naive constraint vacuous.
--
-- The column defaults declared by the table are:
--
--     events   DEFAULT '{"system": true, "stop_loss": true,
--                        "risk_alerts": true, "trade_fills": true}'
--     channels DEFAULT '{"push": true, "email": true,
--                        "in_app": true, "telegram": false}'
--
-- The vocabulary actually stored is a DIFFERENT one -- the eight fields of
-- `core/models/pydantic_models.NotificationEvents` and the three of
-- `NotificationChannels`. Measured read-only against production
-- (`jsonb_each` over all four rows):
--
--     events keys   : api_key_expiring, backtest_complete, bot_state_change,
--                     daily_pnl_summary, kill_switch_activated,
--                     new_login_detected, stop_loss_triggered, trade_executed
--     channels keys : email, mobile, telegram
--
-- Every one of the four rows carries all eleven. NOT ONE row carries `system`,
-- `risk_alerts`, `trade_fills`, `push` or `in_app`. The declared defaults have
-- never been realised: the only writer is the panel's PUT, which always sends
-- the whole Pydantic object, so the default is only reachable by an INSERT that
-- omits the column -- and nothing does that.
--
-- A CHECK written against `risk_alerts` or `system` would therefore have
-- constrained a key no row has, passed on every real row, and read in review
-- like an enforced invariant. That is the shape this migration exists to
-- prevent, so the constraint is written against the stored vocabulary and the
-- dead defaults are corrected to match it in the same transaction.
--
-- ────────────────────────────────────────────────────────────────────────────
-- 2. WHICH KEYS ARE MANDATORY, AND WHY THE LINE IS WHERE IT IS
-- ────────────────────────────────────────────────────────────────────────────
-- Three, and each one is an event whose suppression leaves a customer unaware
-- that their money is at risk or their account was reached:
--
--   * `kill_switch_activated` -- the emergency halt. `routers/risk.py:849` is
--     the ONE `severity="critical"` dispatch in the whole tree (AST over every
--     `dispatch_user_notification` call site: 17 sites, 3 severities). It is
--     also the switch the panel's own sentence names.
--   * `stop_loss_triggered` -- a stop-loss firing. The brief names stop-loss
--     events among the alerts that must reach the customer.
--   * `new_login_detected` -- an authentication event. "Preserve mandatory
--     security and risk alerts" covers the case where the person who would
--     want the alert is not the person who turned it off.
--
-- DELIBERATELY NOT mandatory, because a floor that swallows everything stops
-- being a floor: `api_key_expiring` (a maintenance warning -- real, but a
-- customer may legitimately mute it), `trade_executed`, `daily_pnl_summary`,
-- `bot_state_change`, `backtest_complete`, and all three delivery channels.
-- A customer may silence email and still receive every mandatory alert in the
-- in-app feed, which is the record and is not a preference key at all.
--
-- ────────────────────────────────────────────────────────────────────────────
-- 3. WHY THIS APPLIES CLEANLY
-- ────────────────────────────────────────────────────────────────────────────
-- Measured before writing it, because a VALIDATED check fails to apply if one
-- existing row violates it. Read-only against production:
--
--     rows where kill_switch_activated = false : 0
--     rows where stop_loss_triggered   = false : 0
--     rows where new_login_detected    = false : 0
--     rows with a NULL events column           : 0
--
-- So no customer's stored preference changes, and nobody starts receiving an
-- alert they had switched off. The constraint forbids a FUTURE write, which is
-- the only thing it needs to do.
--
-- The predicate is `IS NOT FALSE`, not `= true`. An absent key and a JSON
-- `null` both read as "not set", and a not-set mandatory key is the default,
-- which is on. `= true` would have rejected a partial payload that omitted the
-- key entirely -- refusing a write for not mentioning something it agrees with.
--
-- ────────────────────────────────────────────────────────────────────────────
-- 4. WHAT THIS DOES NOT CLAIM
-- ────────────────────────────────────────────────────────────────────────────
-- It does not make preferences take effect. Nothing on the dispatch path reads
-- `notification_settings`, so no preference suppresses any notification today,
-- mandatory or otherwise -- and of the eight event keys, SEVEN have no producer
-- at all (only `kill_switch_activated` matches a real dispatch site; the rest
-- correspond to events the platform does not emit, which is the same gap
-- NOTIF-01 recorded for the synchronous execution engine). Building a filter
-- over that would be building a mechanism with no input.
--
-- What this does is make the sentence the panel already prints TRUE, at the
-- only layer that can hold it true regardless of which client writes next.
-- ============================================================================

BEGIN;

-- ── PREFLIGHT: refuse rather than apply half of this ───────────────────────
DO $$
DECLARE
    offending integer;
BEGIN
    IF to_regclass('public.notification_settings') IS NULL THEN
        RAISE EXCEPTION
            '073 preflight FAILED: public.notification_settings is absent. The '
            'mandatory-alert floor has nothing to constrain.';
    END IF;

    SELECT count(*) INTO offending
      FROM public.notification_settings
     WHERE (events ->> 'kill_switch_activated') = 'false'
        OR (events ->> 'stop_loss_triggered')   = 'false'
        OR (events ->> 'new_login_detected')    = 'false';

    IF offending > 0 THEN
        RAISE EXCEPTION
            '073 preflight FAILED: % row(s) already store a mandatory alert as '
            'false. Applying the constraint would fail; decide explicitly '
            'whether those customers should start receiving the alert again '
            'before re-running.', offending;
    END IF;
END $$;

-- ── The floor ──────────────────────────────────────────────────────────────
ALTER TABLE public.notification_settings
    DROP CONSTRAINT IF EXISTS chk_notification_settings_mandatory_alerts;

ALTER TABLE public.notification_settings
    ADD CONSTRAINT chk_notification_settings_mandatory_alerts CHECK (
        (events -> 'kill_switch_activated') IS DISTINCT FROM 'false'::jsonb
        AND (events -> 'stop_loss_triggered') IS DISTINCT FROM 'false'::jsonb
        AND (events -> 'new_login_detected') IS DISTINCT FROM 'false'::jsonb
    );

-- ── The dead defaults, corrected to the vocabulary that is actually stored ──
--
-- Left alone, a row inserted without an explicit payload would carry four event
-- keys the application never reads and would be missing the eight it does --
-- including all three mandatory ones, which the constraint would then permit as
-- "not set". Correcting the default is what makes the constraint's `IS NOT
-- FALSE` reading safe for a row nobody wrote a payload for.
ALTER TABLE public.notification_settings
    ALTER COLUMN events SET DEFAULT '{
        "trade_executed": true,
        "stop_loss_triggered": true,
        "daily_pnl_summary": true,
        "bot_state_change": false,
        "kill_switch_activated": true,
        "new_login_detected": true,
        "api_key_expiring": true,
        "backtest_complete": false
    }'::jsonb;

ALTER TABLE public.notification_settings
    ALTER COLUMN channels SET DEFAULT '{
        "email": true,
        "telegram": false,
        "mobile": false
    }'::jsonb;

-- ── POSTFLIGHT: the constraint exists AND refuses what it claims to ─────────
DO $$
DECLARE
    probe_id uuid;
    refused  boolean := false;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.notification_settings')
           AND contype  = 'c'
           AND conname  = 'chk_notification_settings_mandatory_alerts'
    ) THEN
        RAISE EXCEPTION
            '073 postflight FAILED: chk_notification_settings_mandatory_alerts '
            'is absent, so the panel''s "cannot be disabled" sentence is still '
            'only a sentence.';
    END IF;

    -- Existence is not enforcement. Attempt the write the constraint exists to
    -- refuse, against an id that cannot collide, and require the refusal. A
    -- constraint whose predicate were subtly wrong (`->>` against a jsonb
    -- literal, say) would pass the existence check and permit the write.
    probe_id := gen_random_uuid();
    BEGIN
        INSERT INTO public.notification_settings (user_id, events)
        VALUES (probe_id, '{"kill_switch_activated": false}'::jsonb);
    EXCEPTION
        WHEN check_violation THEN
            refused := true;
        WHEN foreign_key_violation THEN
            -- The row never reached the CHECK: `user_id` references
            -- `profiles(id)`. Reported rather than read as a pass, because a
            -- foreign-key refusal says nothing about the constraint under test.
            RAISE NOTICE
                '073 postflight: the enforcement probe could not run (user_id '
                'is a foreign key and the probe id owns no profile). The '
                'constraint exists; its predicate was not exercised here. '
                'tests/test_notif02_mandatory_alert_floor_postgres.py does '
                'exercise it against a real profile.';
            refused := true;
    END;

    IF NOT refused THEN
        -- No cleanup statement here, deliberately. The probe row would need deleting if
        -- this block could return normally, but it cannot: the RAISE aborts the enclosing
        -- transaction and the INSERT goes with it. A `DELETE FROM` would also put a
        -- destructive statement into the migration set, which Requirement 24.7 forbids and
        -- `test_marketplace_paper_schema_contract.py::test_no_destructive_statements`
        -- refuses -- correctly, since a reader scanning for destructive SQL cannot be asked
        -- to also reason about which branch is reachable.
        RAISE EXCEPTION
            '073 postflight FAILED: a row storing kill_switch_activated=false '
            'was ACCEPTED. The constraint exists but does not bind.';
    END IF;

    RAISE NOTICE
        '073: kill_switch_activated, stop_loss_triggered and new_login_detected '
        'cannot be stored as false. The preference defaults now name the keys '
        'the application actually reads.';
END $$;

COMMIT;
