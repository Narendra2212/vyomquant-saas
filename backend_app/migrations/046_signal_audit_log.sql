-- backend_app/migrations/046_signal_audit_log.sql
--
-- AUD-01 (P2), the still-open half: a durable, append-only history of a signal's
-- lifecycle (created -> risk decision -> order -> execution), alongside -- not instead
-- of -- the existing `public.signals` table.
--
-- WHY A NEW TABLE, NOT MAKING `signals` ITSELF APPEND-ONLY.
-- `signal_service.py`'s own update_risk_decision / update_order / update_execution
-- UPDATE the same `signals` row in place as a signal progresses, and `update_execution`'s
-- own docstring is explicit that a repeat call OVERWRITES rather than appends ("a REPEAT
-- call is safe by construction: it overwrites the same `executed_at`... so the timeline
-- still carries exactly one POSITION_UPDATED"). `signals` holds CURRENT state by design;
-- forcing it append-only (the 036/040 treatment) would break that live, working lifecycle
-- model. This table is the separate, durable history those three writers now also append
-- to, following the same append-only shape 036 (order_audit_log) and 040 (trade_history)
-- already established for exactly this reason.
--
-- NOT ATTEMPTED HERE: hash chaining. Neither 036 nor 040 -- the two existing precedents
-- for an append-only audit table on this platform -- implement it either; it does not
-- exist anywhere in this codebase to reuse, and designing a new tamper-evidence primitive
-- from scratch (chain-break detection under concurrent inserts, hash scope, alerting) is a
-- platform-wide design decision, not something to improvise for one table in this pass.
-- Append-only-by-trigger already closes the specific risk this entry's own evidence
-- named (a historical signal row silently edited after the fact); hash chaining would add
-- tamper EVIDENCE on top of tamper RESISTANCE, which is a real but separate improvement.
--
-- Same append-only shape as 036/040:
--   * audit_id is the primary key, so a retried write is idempotent (ON CONFLICT DO NOTHING).
--   * A BEFORE UPDATE/DELETE trigger refuses every role (including the owner) - corrections
--     are new rows, never edits. TRUNCATE is revoked from the browser roles and guarded by
--     a statement trigger.
--   * RLS is enabled with no policy and every privilege is revoked from anon/authenticated:
--     only the backend (service_role / owner) reads or writes it.
-- Idempotent and additive; touches no existing table.

CREATE TABLE IF NOT EXISTS public.signal_audit_log (
    audit_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id       uuid NOT NULL,
    event_type      text NOT NULL CHECK (event_type IN (
                        'created', 'risk_decision', 'order', 'execution'
                    )),
    -- uuid, NULLable, NO foreign key: BUG-049 (hardening/998) requires every user_id column
    -- to be uuid, and an audit row must outlive the customer it describes.
    user_id         uuid,
    strategy_id     text,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    payload         jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signal_audit_log_signal ON public.signal_audit_log (signal_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_signal_audit_log_user_time ON public.signal_audit_log (user_id, recorded_at DESC);

CREATE OR REPLACE FUNCTION public.signal_audit_log_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'public.signal_audit_log is append-only (% refused)', TG_OP
        USING ERRCODE = '42501';
END;
$$;

DROP TRIGGER IF EXISTS trg_signal_audit_log_no_update_delete ON public.signal_audit_log;
CREATE TRIGGER trg_signal_audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON public.signal_audit_log
    FOR EACH ROW EXECUTE FUNCTION public.signal_audit_log_append_only();

DROP TRIGGER IF EXISTS trg_signal_audit_log_no_truncate ON public.signal_audit_log;
CREATE TRIGGER trg_signal_audit_log_no_truncate
    BEFORE TRUNCATE ON public.signal_audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION public.signal_audit_log_append_only();

ALTER TABLE public.signal_audit_log ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON public.signal_audit_log FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON public.signal_audit_log FROM authenticated';
    END IF;
END
$$;
