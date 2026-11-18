-- ============================================================================
-- 075_risk06_risk_settings_circuit_breaker_armed.sql
--
-- RISK-06. `PUT /api/risk/settings` has never persisted anything in production.
--
-- The handler assembles one record and upserts it whole:
--
--     updated_record = {                                  routers/risk.py:421
--         "user_id": uid,
--         "max_daily_loss": ...,                  (declared)
--         "max_positions": ...,                   (declared)
--         "max_leverage": ...,                    (declared)
--         "circuit_breaker_armed": ...,           <-- UNDECLARED
--         "kill_switches": ks_dict,               (declared)
--         "updated_at": ...,                      (declared)
--     }
--     sb.table("risk_settings").upsert(updated_record, on_conflict="user_id")
--
-- PostgREST rejects the whole statement on an undefined column, so the upsert
-- fails with 42703 every time -- and because ONE column is undeclared, the
-- other five never land either. The upsert is atomic; there is no partial save.
--
-- Measured read-only against production, 2026-11-18:
--
--     insert into public.risk_settings (user_id, max_daily_loss, max_positions,
--         max_leverage, circuit_breaker_armed, kill_switches, updated_at) ...
--     -> 42703  column "circuit_breaker_armed" of relation
--               "risk_settings" does not exist
--
--     public.risk_settings columns in production : 14
--     circuit_breaker_armed among them           : no
--     rows in public.risk_settings               : 1   (not written by this path)
--
-- And it is undeclared in SOURCE, not merely absent from production. A grep
-- across `migrations/`, `backend_app/migrations/`, `archived_migrations/` and
-- `staging/provisioning_snapshot.json` finds the name nowhere. A database
-- rebuilt from this repository would reproduce the defect exactly, which is
-- why this is a new column and not a drift repair.
--
-- WHAT WAS BROKEN BY IT
--
--   1. No risk limit a trader sets is durable. The handler writes the record
--      to a PROCESS-LOCAL dict (`_user_risk_settings`) before attempting the
--      upsert, so the value survives in the worker that served the request and
--      nowhere else: it is invisible to the other gunicorn workers, invisible
--      to the 5 worker ECS services, and gone on restart or redeploy.
--   2. The failure was reported as success. The upsert sat inside
--      `try/except ... logger.warning("Database persist ... skipped")` and the
--      handler then returned `{"status": "ok", "message": "Risk settings saved
--      successfully"}` unconditionally. Fixed in the same change as this file
--      -- a persistence failure now refuses with 503 rather than claiming a
--      save that did not happen.
--   3. The circuit-breaker armed state the Dashboard shows is always the
--      DEFAULT. `routers/risk.py:286` reads it back as
--      `row.get("circuit_breaker_armed", True)` and
--      `dashboard_aggregation_service.py:1921` surfaces it. With the column
--      absent, every read answers True regardless of what the trader chose.
--      Note the direction: enforcement does not depend on this column
--      (`core/risk_manager.py` clamps independently on the order path), so the
--      breaker was not silently disarmed. The defect is that the trader could
--      not change it and was told they had.
--
-- WHY `NOT NULL DEFAULT true` IS THE SAFE DECLARATION
--
-- `true` = armed. Backfilling the existing row to armed matches both the
-- read-path default every caller already assumes and the fail-safe direction:
-- if this column is ever read by something that does gate behaviour, the value
-- it finds on a pre-existing row should be the protective one. NOT NULL keeps
-- "unset" from becoming a third state that each reader interprets for itself.
--
-- WHAT THIS FILE DOES NOT DO
--
-- It does not touch `kill_switches`, whose stored vocabulary was measured
-- before 073 constrained the notification settings and is NOT re-measured
-- here. It adds no index: `risk_settings` is keyed by `user_id` UNIQUE and is
-- read one row at a time.
--
-- ROLLBACK: backend_app/migrations/rollback/075_risk06_rollback.sql
-- ============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- PRECONDITION
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('public.risk_settings') IS NULL THEN
        RAISE EXCEPTION
            '075 precondition failed: public.risk_settings does not exist. It is '
            'declared by migrations/create_risk_settings_tables.sql and '
            'migrations/006_reconcile_production_database.sql. Apply the numbered '
            'SQL set first; see backend_app/migrations/PROVISIONING_ORDER.md.';
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1 — THE COLUMN THE WRITER HAS ALWAYS NAMED
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE public.risk_settings
    ADD COLUMN IF NOT EXISTS circuit_breaker_armed boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN public.risk_settings.circuit_breaker_armed IS
    'Whether the institutional circuit breaker is armed for this account. '
    'true = armed, and true is the default so a pre-existing row and a row '
    'written before this column existed both read as protected. Enforcement '
    'does not read this column -- core/risk_manager.py clamps on the order '
    'path regardless -- so this is the trader''s declared intent and the value '
    'the Dashboard displays, not itself a safety gate.';

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2 — POSTCONDITIONS
--
-- Asserts the outcome the file exists for, and additionally asserts that the
-- WHOLE record the handler sends is now satisfiable. Checking only the one
-- column would leave the next undeclared name to be found in production again.
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    missing text;
    handler_columns text[] := ARRAY[
        'user_id', 'max_daily_loss', 'max_positions', 'max_leverage',
        'circuit_breaker_armed', 'kill_switches', 'updated_at'
    ];
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'risk_settings'
           AND column_name = 'circuit_breaker_armed'
    ) THEN
        RAISE EXCEPTION '075 postcondition failed: circuit_breaker_armed absent.';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'risk_settings'
           AND column_name = 'circuit_breaker_armed'
           AND is_nullable <> 'NO'
    ) THEN
        RAISE EXCEPTION
            '075 postcondition failed: circuit_breaker_armed is nullable, so '
            '"unset" became a third state.';
    END IF;

    SELECT string_agg(c, ', ') INTO missing
      FROM unnest(handler_columns) AS c
     WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'risk_settings'
           AND column_name = c
     );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            '075 postcondition failed: routers/risk.py still names column(s) '
            'public.risk_settings does not declare: %. The upsert is atomic, so '
            'any one of these keeps the whole save failing with 42703.', missing;
    END IF;

    RAISE NOTICE
        '075: risk_settings declares every column PUT /api/risk/settings sends. '
        'The save can now persist, and the handler refuses rather than reporting '
        'a success it did not achieve.';
END $$;

COMMIT;
