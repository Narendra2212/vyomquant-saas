-- backend_app/migrations/040_trade_history_durable.sql
--
-- IMPORT-03 (P2). `backend/position_engine.py::PositionEngine._record_trade` has always
-- imported `backend_app.core.models.trade_history.{TradeCreate,TradeHistoryModel}`, a
-- module that did not exist -- every call raised ModuleNotFoundError, caught by the
-- method's own except-and-log, so no trade has ever been persisted through this path.
--
-- This is the durable store the restored model writes to via plain SQLAlchemy (the same
-- engine/session `PositionModel`/`ExecutionRecordModel` use -- not PostgREST), following
-- the same append-only, service-only shape as migration 036's order_audit_log:
--   * trade_id is the primary key, so a retried write is idempotent at the DB level.
--   * A BEFORE UPDATE/DELETE trigger refuses every role (including the owner) - a
--     correction is a new row, never an edit. TRUNCATE is guarded by a statement trigger.
--   * RLS is enabled with no policy and every privilege is revoked from anon/authenticated:
--     this is backend-process-only data (PositionEngine has no end-user-facing route),
--     never read or written by a browser role.
--
-- Note (see trade_history.py's own module docstring): PositionEngine has no caller
-- anywhere in the application today (the same gap RT-16's ledger entry records for its
-- reconciliation side), so this table will stay empty until that is wired up. This
-- migration and the model it backs are the correctness fix for THAT future wiring, not a
-- claim that trade history is being recorded for any trade today.
--
-- Idempotent and additive; touches no existing table.

CREATE TABLE IF NOT EXISTS public.trade_history (
    trade_id       text PRIMARY KEY,
    tenant_id      uuid NOT NULL,
    strategy_id    text NOT NULL,
    symbol         text NOT NULL,
    side           text NOT NULL,
    entry_price    numeric NOT NULL,
    exit_price     numeric NOT NULL,
    size           numeric NOT NULL,
    realized_pnl   numeric NOT NULL,
    close_reason   text NOT NULL,
    closed_at      timestamptz NOT NULL,
    recorded_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trade_history_tenant_symbol ON public.trade_history (tenant_id, symbol);
CREATE INDEX IF NOT EXISTS idx_trade_history_tenant_time ON public.trade_history (tenant_id, closed_at DESC);
CREATE INDEX IF NOT EXISTS idx_trade_history_strategy ON public.trade_history (tenant_id, strategy_id);

CREATE OR REPLACE FUNCTION public.trade_history_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'public.trade_history is append-only (% refused)', TG_OP
        USING ERRCODE = '42501';
END;
$$;

DROP TRIGGER IF EXISTS trg_trade_history_no_update_delete ON public.trade_history;
CREATE TRIGGER trg_trade_history_no_update_delete
    BEFORE UPDATE OR DELETE ON public.trade_history
    FOR EACH ROW EXECUTE FUNCTION public.trade_history_append_only();

DROP TRIGGER IF EXISTS trg_trade_history_no_truncate ON public.trade_history;
CREATE TRIGGER trg_trade_history_no_truncate
    BEFORE TRUNCATE ON public.trade_history
    FOR EACH STATEMENT EXECUTE FUNCTION public.trade_history_append_only();

ALTER TABLE public.trade_history ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON public.trade_history FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON public.trade_history FROM authenticated';
    END IF;
END
$$;

-- Postflight: table exists, append-only trigger present, RLS enabled.
DO $$
BEGIN
    IF to_regclass('public.trade_history') IS NULL THEN
        RAISE EXCEPTION 'postflight failed: public.trade_history was not created';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_trade_history_no_update_delete'
    ) THEN
        RAISE EXCEPTION 'postflight failed: trade_history append-only trigger missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_class WHERE relname = 'trade_history' AND relrowsecurity
    ) THEN
        RAISE EXCEPTION 'postflight failed: trade_history RLS not enabled';
    END IF;
END
$$;
