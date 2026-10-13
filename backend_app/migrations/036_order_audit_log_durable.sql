-- backend_app/migrations/036_order_audit_log_durable.sql
--
-- AUD-02 (P1). The order audit trail (signal -> decision -> submit -> confirm/fail -> fill)
-- was written only to a structured log line and a 24h Redis key per (execution, event);
-- `_store_to_db` was a `pass` and the read path called a `from_dict` that did not exist, so
-- it always returned []. After a Redis flush or a log rotation there was no record of what
-- the platform did with a customer's money.
--
-- This is the durable store: append-only, service-role only.
--   * audit_id is the primary key, so a retried write is idempotent (ON CONFLICT DO NOTHING).
--   * A BEFORE UPDATE/DELETE trigger refuses every role (including the owner) - corrections
--     are new rows, never edits. TRUNCATE is revoked from the browser roles and guarded by
--     a statement trigger.
--   * RLS is enabled with no policy and every privilege is revoked from anon/authenticated:
--     only the backend (service_role / owner) reads or writes it.
-- Idempotent and additive; touches no existing table.

CREATE TABLE IF NOT EXISTS public.order_audit_log (
    audit_id           text PRIMARY KEY,
    occurred_at        timestamptz NOT NULL,
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    event_type         text NOT NULL,
    user_id            text NOT NULL,
    strategy_id        text,
    execution_id       text,
    symbol             text,
    side               text,
    size               numeric,
    price              numeric,
    order_type         text,
    exchange_id        text,
    client_order_id    text,
    exchange_order_id  text,
    status             text,
    filled_amount      numeric,
    remaining_amount   numeric,
    average_price      numeric,
    fee                numeric,
    decision_reason    text,
    error_message      text,
    signal             jsonb,
    exchange_response  jsonb,
    metadata           jsonb
);

CREATE INDEX IF NOT EXISTS idx_order_audit_log_execution ON public.order_audit_log (execution_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_order_audit_log_user_time ON public.order_audit_log (user_id, occurred_at DESC);

CREATE OR REPLACE FUNCTION public.order_audit_log_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'public.order_audit_log is append-only (% refused)', TG_OP
        USING ERRCODE = '42501';
END;
$$;

DROP TRIGGER IF EXISTS trg_order_audit_log_no_update_delete ON public.order_audit_log;
CREATE TRIGGER trg_order_audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON public.order_audit_log
    FOR EACH ROW EXECUTE FUNCTION public.order_audit_log_append_only();

DROP TRIGGER IF EXISTS trg_order_audit_log_no_truncate ON public.order_audit_log;
CREATE TRIGGER trg_order_audit_log_no_truncate
    BEFORE TRUNCATE ON public.order_audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION public.order_audit_log_append_only();

ALTER TABLE public.order_audit_log ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON public.order_audit_log FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON public.order_audit_log FROM authenticated';
    END IF;
END
$$;
