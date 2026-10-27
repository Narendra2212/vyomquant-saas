-- ═══════════════════════════════════════════════════════════════════════════
-- 060_exch06_credential_sandbox_flag.sql
--
-- EXCH-06. Give `exchange_keys` somewhere to put the answer the credential UI
-- has been asking for and then throwing away.
--
-- THE DEFECT, MEASURED AGAINST THE LIVE DATABASE RATHER THAN READ OFF A FILE
-- ───────────────────────────────────────────────────────────────────────────
-- `backend_app/core/exchange_connection_schema.py` declares, for binance and
-- four other venues:
--
--     ExchangeCredentialField(
--         field_id="sandbox",
--         label="Testnet / Sandbox Mode",
--         type=FieldType.BOOLEAN,
--         description="Connect to Binance Testnet instead of Production.")
--
-- and the chain from that toggle to a connection was broken at every link:
--
--   * `ExchangeKeysRequest` (core/models/pydantic_models.py) has no `sandbox`
--     field, and no `extra="forbid"`, so a UI that posts one gets HTTP 200 and
--     pydantic discards it silently;
--   * `APIKeyVault.store_exchange_keys` builds an upsert dict of exactly five
--     keys and none of them is a sandbox flag;
--   * THIS TABLE HAS NO COLUMN FOR IT. Queried against the production database
--     on 2026-10-27, `public.exchange_keys` carries: user_id, exchange_id,
--     encrypted_api_key, encrypted_secret_key, encrypted_password, created_at,
--     updated_at, is_active, validation_status, last_validated_at, can_trade,
--     id. No `sandbox`, no `testnet`;
--   * `APIKeyVault.load_decrypted_keys` SELECTs `*` but then hand-builds a
--     return dict of exactly three keys -- api_key, secret_key, password -- so a
--     column arriving in the row is dropped before any caller sees it.
--
-- And the consumer at the far end was already written and waiting:
-- `backend_app/backend/order_control.py::_executor_for` does
--
--     bool(keys.get("sandbox") or keys.get("testnet"))
--
-- which against a three-key dict is False UNCONDITIONALLY. That is the single
-- expression in the codebase that still said what was intended, and it is the
-- fan-in point for POST /api/orders/cancel/{id}, /cancel-all, OrderWatchdog,
-- ExchangeReconciliationService and PositionConsistencyChecker.
--
-- So a trader who ticked "Testnet / Sandbox Mode" got a PRODUCTION connection,
-- with no error and no warning, on a venue where their real balance lives. Not
-- an active incident -- those paths cancel and read rather than open positions,
-- and live trading is hard-gated off (RT-15) -- but a false promise about money.
--
-- WHY A COLUMN AND NOT A TABLE
-- ───────────────────────────────────────────────────────────────────────────
-- The flag is a property OF a credential, one-to-one, read on the same lookup
-- that decrypts it (`load_decrypted_keys(user_id, exchange_id)`). A side table
-- would mean a second round trip on the connect path to answer a boolean, and a
-- row that could go missing -- and a MISSING row would have to default to
-- something. Defaulting to live is what the bug already does; defaulting to
-- sandbox would silently redirect real credentials. A NOT NULL column with a
-- DEFAULT cannot be absent.
--
-- `false` IS THE RIGHT DEFAULT, and it is worth saying why given the above. It
-- preserves EXACTLY today's behaviour for every existing row and every caller
-- that does not pass the flag, so this migration changes no connection that
-- works now. The two rows currently stored are quarantined
-- `validation_status='unreadable'` (VAULT-01) and will be re-entered rather than
-- interpreted, so there is no historical intent to recover: nobody's stored
-- preference is being guessed at, because none was ever stored.
--
-- THE PRIMARY KEY IS NOT TOUCHED, and nor is any constraint. `(user_id,
-- exchange_id)` remains the primary key -- it is what the vault resolves by, and
-- `tests/test_mkt05_subscriber_live_deployment.py` asserts no migration drops or
-- replaces it on this table. This file only adds a column.
--
-- IDEMPOTENT. `ADD COLUMN IF NOT EXISTS`, a backfill that touches only NULLs,
-- and `SET NOT NULL` behind an `is_nullable` check. The provisioning job runs the
-- whole numbered set TWICE to prove idempotency, so a re-run must do nothing.
-- ═══════════════════════════════════════════════════════════════════════════

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 1 — Preflight.
-- ─────────────────────────────────────────────────────────────────────────
DO $exch06_preflight$
BEGIN
    IF to_regclass('public.exchange_keys') IS NULL THEN
        RAISE NOTICE
            '060: public.exchange_keys does not exist on this database; nothing '
            'to do. It is declared by migrations/003_create_exchange_keys_table'
            '.sql and 006_reconcile_production_database.sql.';
        RETURN;
    END IF;
    RAISE NOTICE '060 preflight: public.exchange_keys is present';
END
$exch06_preflight$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 2 — The flag.
-- ─────────────────────────────────────────────────────────────────────────
DO $exch06_column$
BEGIN
    IF to_regclass('public.exchange_keys') IS NULL THEN
        RETURN;
    END IF;

    -- Added nullable first, then backfilled, then constrained. `ADD COLUMN ...
    -- NOT NULL DEFAULT false` in one statement would also work on modern
    -- PostgreSQL, but the three-step form is what the rest of this numbered set
    -- uses (see 058) and it is safe on a column that a previous hand-run may
    -- already have added WITHOUT the default.
    ALTER TABLE public.exchange_keys
        ADD COLUMN IF NOT EXISTS sandbox BOOLEAN DEFAULT false;

    -- A NULL here is indistinguishable from "live" to
    -- `bool(keys.get("sandbox"))`, so this is not strictly load-bearing for
    -- behaviour -- it is load-bearing for HONESTY. A nullable flag has three
    -- states and the product has two, and "we do not know whether this
    -- credential is sandbox" is not an answer any caller on the connect path can
    -- act on.
    UPDATE public.exchange_keys SET sandbox = false WHERE sandbox IS NULL;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'exchange_keys'
           AND column_name = 'sandbox' AND is_nullable = 'YES'
    ) THEN
        ALTER TABLE public.exchange_keys ALTER COLUMN sandbox SET NOT NULL;
    END IF;

    RAISE NOTICE
        '060: exchange_keys.sandbox present, backfilled to false and NOT NULL';
END
$exch06_column$;

COMMENT ON COLUMN public.exchange_keys.sandbox IS
    'EXCH-06. Whether this credential addresses the venue''s SANDBOX/testnet
     environment rather than production -- the answer to the credential UI''s
     "Testnet / Sandbox Mode" toggle, which had nowhere to be stored and so had
     no effect on any connection. Read by
     api_key_vault.load_decrypted_keys and consumed by
     order_control._executor_for, which passes it to get_exchange_executor and
     therefore to CCXTExchangeExecutor.connect''s sandbox verification. It is a
     REQUEST, not evidence: EXCH-01 requires the routing to be PROVEN from the
     client''s endpoints at connect time (verify_sandbox_endpoint), and a true
     value here that cannot be proven is refused rather than trusted. NOT NULL
     DEFAULT false, so an unanswered flag means production -- which is the
     behaviour that existed before this column and therefore changes nothing that
     works today.';

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 3 — Postflight: the thing that was broken must now be possible.
-- ─────────────────────────────────────────────────────────────────────────
DO $exch06_postflight$
DECLARE
    v_total    bigint;
    v_flagged  bigint;
    v_nullable text;
    v_default  text;
BEGIN
    IF to_regclass('public.exchange_keys') IS NULL THEN
        RETURN;
    END IF;

    SELECT is_nullable, column_default
      INTO v_nullable, v_default
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'exchange_keys'
       AND column_name = 'sandbox';

    IF v_nullable IS NULL THEN
        RAISE EXCEPTION
            '060 postflight FAILED: exchange_keys has no `sandbox` column, so the '
            'credential UI''s "Testnet / Sandbox Mode" toggle still has nowhere '
            'to be stored and still has no effect.';
    END IF;

    IF v_nullable <> 'NO' THEN
        RAISE EXCEPTION
            '060 postflight FAILED: exchange_keys.sandbox is nullable, so a '
            'credential can be in a third state the connect path cannot act on.';
    END IF;

    IF v_default IS NULL OR v_default NOT LIKE '%false%' THEN
        RAISE EXCEPTION
            '060 postflight FAILED: exchange_keys.sandbox has no `false` default '
            '(found %). A caller that omits the flag must get PRODUCTION, which '
            'is the pre-existing behaviour; any other default silently changes '
            'where an existing credential would send.', coalesce(v_default, 'NULL');
    END IF;

    -- The data itself: every row answerable, and (recorded, not enforced) how
    -- many currently say sandbox. Expected to be 0 immediately after this runs.
    SELECT count(*), count(*) FILTER (WHERE sandbox)
      INTO v_total, v_flagged
      FROM public.exchange_keys;

    RAISE NOTICE
        '060: % credential row(s), % flagged sandbox. The column is NOT NULL '
        'with a false default, so order_control._executor_for''s '
        'bool(keys.get("sandbox")) can now be something other than False.',
        v_total, v_flagged;

    RAISE NOTICE
        '060: NOTE -- storing true here does not GRANT a sandbox connection. '
        'EXCH-01''s verify_sandbox_endpoint still has to prove the client left '
        'production at connect time, and refuses if it cannot; coinbase, kraken '
        'and kucoin publish no sandbox endpoint at all on the installed ccxt and '
        'are refused before a client is built.';
END
$exch06_postflight$;
