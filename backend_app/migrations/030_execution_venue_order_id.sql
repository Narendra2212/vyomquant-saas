-- backend_app/migrations/030_execution_venue_order_id.sql
--
-- PHASE 9, STEP 3: separate THE VENUE'S order id from THIS PLATFORM'S order id.
--
-- TRADE-12 (NEW, discovered while building TRADE-07 recovery; not in the Phase 9 brief).
-- =====================================================================================
-- `execution_records.order_id` carries a foreign key:
--
--     fk_execution_records_order_id
--         FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL
--         deferrable = false
--
-- and `public.orders.id` is this platform's OWN order identifier. But the only thing the
-- application ever writes into that column is the EXCHANGE's identifier, taken straight
-- out of the venue's response:
--
--     exchange_executor.place_order:   order_id_str = str(response.get("id"))
--     execution_engine (on success):   repo.update_status(..., status=COMPLETED,
--                                           order_id=trade_result.get('order_id'), ...)
--
-- A venue-assigned id has no reason to exist as a row in `public.orders`, so the write is
-- refused. Measured directly (staging/probe_phase9_order_id_fk_blocks_completion.py, run
-- inside a rolled-back transaction):
--
--     VENUE_NUMERIC_ID   = REJECTED  ForeignKeyViolation ... fk_execution_records_order_id
--     VENUE_STRING_ID    = REJECTED  ForeignKeyViolation ... fk_execution_records_order_id
--     EXISTING_ORDERS_ID = ACCEPTED  ('t_id_9d45c7')
--     ORDERS_ROWS                     = 24
--     EXECUTION_RECORDS_WITH_ORDER_ID = 0
--
-- WHAT THAT COSTS, AND WHY IT COMPOUNDS TRADE-07
-- ----------------------------------------------
-- `update_status` commits, and there is no `try` around it at the success call site. So on
-- a real live fill:
--
--   1. `create_order` succeeds -- money has moved;
--   2. the COMPLETED write raises 23503 at commit;
--   3. the session's transaction is now ABORTED, so the enclosing handler's own
--      `update_status(..., FAILED, ...)` also fails;
--   4. the exception leaves `execute_with_idempotency` entirely;
--   5. the row is left `EXECUTING` with `order_id IS NULL` -- which is exactly the stuck
--      state TRADE-07 is about, and `OrderWatchdog` filters on `order_id IS NOT NULL` so
--      it can never see it.
--
-- In other words TRADE-07's "stuck EXECUTING row" population has a GUARANTEED producer,
-- not merely a crash-induced one. Live trading has never been enabled, which is why no
-- such row exists yet; `EXECUTION_RECORDS_WITH_ORDER_ID = 0` is consistent with the write
-- never having succeeded, and this file does not claim more than that.
--
-- THE FIX IS A MODELLING CORRECTION, NOT A CONSTRAINT REMOVAL
-- -----------------------------------------------------------
-- Two different identifiers were conflated into one column:
--
--   * `order_id`        -> an order THIS platform created, in `public.orders`. The
--                          foreign key is correct for that and is KEPT.
--   * `venue_order_id`  -> the identifier the EXCHANGE assigned. Externally generated,
--                          so no foreign key can or should constrain it.
--
-- REJECTED: dropping `fk_execution_records_order_id`. It is a real referential guarantee
-- for the id it was declared on, `add_foreign_keys_20260817.py` added it deliberately,
-- and dropping a constraint to make a wrong write succeed fixes the symptom and keeps the
-- modelling error.
--
-- REJECTED: inserting a `public.orders` row for every venue id. That fabricates platform
-- orders out of exchange responses and would make `orders` mean two things.
--
-- REJECTED: storing the venue id in `exchange_status` (free text). It is a human-readable
-- status field, not an identifier, and nothing could index or join on it.
--
-- Additive and nullable, so this file changes no existing row and breaks no existing
-- reader. `order_id` keeps its meaning, its foreign key and its index.
-- Regression: tests/test_trade07_stale_execution_recovery_postgres.py
-- ==========================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 0 — PREFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('public.execution_records') IS NULL THEN
        RAISE EXCEPTION
            '030 precondition failed: public.execution_records does not exist. Run the '
            'alembic schema first (d97ffff9c3bb_execution_schema_rebuild).';
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1 — THE COLUMN
-- ─────────────────────────────────────────────────────────────────────────────
-- `varchar` to match `order_id`, and to match what the executor produces:
-- `str(response.get("id"))`. Nullable, because it is unknown until the venue answers --
-- and the whole point of TRADE-07 is that there is a real window in which it is unknown.
--
-- NO FOREIGN KEY, deliberately and necessarily: the value is generated by a third party.
ALTER TABLE public.execution_records
    ADD COLUMN IF NOT EXISTS venue_order_id varchar NULL;

-- Recovery looks a row up by this id after reconciling with the venue, and the ordinary
-- reconciliation path will want it too. Partial, because the overwhelming majority of
-- rows never have one.
CREATE INDEX IF NOT EXISTS idx_execution_records_venue_order_id
    ON public.execution_records (venue_order_id)
    WHERE venue_order_id IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2 — POSTFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
-- `ADD COLUMN IF NOT EXISTS` silently accepts a pre-existing column of a different
-- shape, so every property is read back and asserted inside this transaction.
DO $$
DECLARE
    col_type text;
    nullable text;
    fk_count int;
    order_id_fk_still_there boolean;
BEGIN
    SELECT data_type, is_nullable INTO col_type, nullable
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name = 'execution_records'
       AND column_name = 'venue_order_id';

    IF col_type IS NULL THEN
        RAISE EXCEPTION
            '030 postflight FAILED: execution_records.venue_order_id was not created.';
    END IF;

    IF col_type <> 'character varying' THEN
        RAISE EXCEPTION
            '030 postflight FAILED: execution_records.venue_order_id is %, not '
            'character varying. A pre-existing column of another type was left in place '
            'by ADD COLUMN IF NOT EXISTS.', col_type;
    END IF;

    IF nullable <> 'YES' THEN
        RAISE EXCEPTION
            '030 postflight FAILED: execution_records.venue_order_id is NOT NULL. The '
            'venue id is unknown between submission and acknowledgement -- which is the '
            'exact window TRADE-07 is about -- so the column must be nullable.';
    END IF;

    -- IT MUST NOT ACQUIRE A FOREIGN KEY. The value is externally generated; constraining
    -- it would reproduce TRADE-12 on the new column.
    SELECT count(*) INTO fk_count
      FROM pg_constraint
     WHERE conrelid = to_regclass('public.execution_records')
       AND contype = 'f'
       AND 'venue_order_id' = ANY (
            SELECT a.attname FROM pg_attribute a
             WHERE a.attrelid = conrelid AND a.attnum = ANY (conkey)
       );
    IF fk_count <> 0 THEN
        RAISE EXCEPTION
            '030 postflight FAILED: venue_order_id has % foreign key(s). The exchange '
            'assigns this id, so no local table can be its parent -- that is exactly '
            'the defect this column exists to avoid.', fk_count;
    END IF;

    -- AND THE ORIGINAL CONSTRAINT MUST SURVIVE. This file is a modelling correction, not
    -- a constraint removal; if `order_id`'s foreign key disappeared, the referential
    -- guarantee it provides for PLATFORM orders would have been silently traded away.
    SELECT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.execution_records')
           AND conname = 'fk_execution_records_order_id'
    ) INTO order_id_fk_still_there;

    IF NOT order_id_fk_still_there THEN
        RAISE EXCEPTION
            '030 postflight FAILED: fk_execution_records_order_id is gone. This file '
            'adds a separate column for the venue id precisely so that constraint can '
            'be KEPT; dropping it would fix the symptom and preserve the modelling '
            'error.';
    END IF;

    RAISE NOTICE
        '030: execution_records.venue_order_id added (varchar, nullable, no foreign '
        'key), order_id''s foreign key retained.';
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 3 — COMMENTS
-- ─────────────────────────────────────────────────────────────────────────────
COMMENT ON COLUMN public.execution_records.venue_order_id IS
    'The identifier the EXCHANGE assigned to this order, i.e. str(create_order '
    'response["id"]). Deliberately has NO foreign key: it is generated by a third party. '
    'TRADE-12 -- the application previously wrote this value into order_id, which has '
    'FOREIGN KEY (order_id) REFERENCES orders(id), so the write was refused with 23503 '
    'and (because update_status commits, with no try around the success call site) left '
    'the row stuck EXECUTING with order_id NULL -- invisible to OrderWatchdog, which '
    'filters on order_id IS NOT NULL. Measured in '
    'staging/probe_phase9_order_id_fk_blocks_completion.py.';

COMMENT ON COLUMN public.execution_records.order_id IS
    'An order THIS platform created, referencing public.orders(id). Keeps its foreign '
    'key. The EXCHANGE''s identifier belongs in venue_order_id -- conflating the two is '
    'TRADE-12.';

COMMIT;
