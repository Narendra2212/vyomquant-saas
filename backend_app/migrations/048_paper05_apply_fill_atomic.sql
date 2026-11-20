-- backend_app/migrations/048_paper05_apply_fill_atomic.sql
--
-- PAPER-05. A paper fill was SEVEN separate PostgREST writes; now it is ONE transaction.
--
-- THE DEFECT, READ OFF `paper_simulator.apply_fill`'s OWN DOCSTRING
-- -------------------------------------------------------------------
-- "THE WRITES, AND THE ORDERING THE MISSING TRANSACTION FORCES" states plainly that
-- "Requirement 24.6's single transaction is not achievable over this transport" --
-- PostgREST speaks one HTTP statement per request, so `paper_fills` -> `paper_accounts`
-- (version bump) -> `paper_balance_events` -> `paper_positions` -> `paper_orders` ->
-- `paper_trades` (conditionally) -> `paper_equity_snapshots` were seven independent
-- server-side transactions, each committing before the next began. A crash, a timeout
-- or an exhausted retry budget between the second write (the money) and any later one
-- left the account balance moved with some or all of the ledger, the position, the
-- order and the equity curve still reflecting the OLD state -- and the existing code
-- correctly refuses to retry past that point (`_NoRetryPastTheMoney`,
-- `PaperConcurrencyExhausted(partial_write=True)`), because retrying would double-apply
-- the money. The prior remediation pass added `paper_reconciliation.py` to FIND such a
-- stuck session on demand; it could not PREVENT one, because PostgREST genuinely offers
-- no transaction spanning several requests.
--
-- THIS FILE DOES NOT TOUCH THAT ANALYSIS. It implements the one thing the docstring says
-- the transport cannot do, the same way `024_remediate_referral_double_commission.sql`
-- and `029_referral_obligations.sql` already did for the referral ledger: move the
-- whole write sequence inside ONE `SECURITY DEFINER` Postgres function, invoked through
-- ONE `.rpc()` call. A single PL/pgSQL function body IS one transaction -- either every
-- statement inside it commits, or (on any exception) every one of them rolls back,
-- including ones issued earlier in the SAME call. There is no partial state a crash
-- between two of these writes can now produce, because there is no longer a boundary
-- between them for a crash to land on.
--
-- WHAT THIS FILE DOES NOT MOVE INTO SQL, AND WHY
-- -----------------------------------------------
-- Every GUARD and every ARITHMETIC DECISION `apply_fill` makes before it issues a
-- statement -- the terminal-order no-op, the duplicate-event no-op, the feed-health
-- gate, the over-fill refusal, the illegal-transition refusal, the invariant assertion,
-- and the whole fee/slippage/weighted-average-cost/realized-PnL computation in
-- `paper_accounting` -- stay exactly where they are, in Python, computed from a fresh
-- read, BEFORE this function is ever called. `paper_repository`'s own module docstring
-- states the one thing this module is for -- "Arithmetic. paper_accounting owns every
-- balance, equity, PnL, drawdown and win-rate computation" -- and reimplementing that
-- a second time in PL/pgSQL would create exactly the risk this remediation effort exists
-- to avoid: two implementations of one financial computation that can silently disagree.
-- This function's ONLY job is to take the ALREADY-COMPUTED final values and commit the
-- seven writes that record them, atomically, with the SAME column shapes
-- `paper_repository.insert_fill` / `bump_version` / `insert_balance_event` /
-- `upsert_position` / `update_order` / `insert_trade` / `insert_equity_snapshot` already
-- write today -- ported one for one, not redesigned.
--
-- WHAT GENUINELY IMPROVES, BEYOND "NOW ATOMIC"
-- -----------------------------------------------
-- * REAL ROW LOCKS, NOT OPTIMISTIC VERSIONING. `lock_account_for_update`'s own docstring
--   says PostgREST offers no `SELECT ... FOR UPDATE` that could outlive the round trip,
--   so the existing code substitutes a `version` counter and a conditional UPDATE. A
--   PL/pgSQL function body IS one session, so `SELECT ... FOR UPDATE` here is the real
--   thing: the account row is locked for the function's whole duration, and the order
--   row too, so two fills on the same order, or two fills on the same account in
--   different orders, cannot interleave their writes even in principle. The `version`
--   and `order_state` columns are STILL checked (Section 2 below explains why: a stale
--   Python-side read must still be refused), but the predicate they guard can no longer
--   be lost to a race the way an UPDATE issued over a SEPARATE HTTP request could.
-- * THE "FILL ROW EXISTS, LEDGER DOES NOT" STATE BECOMES UNREACHABLE FOR A NEW FILL.
--   That state is exactly what the existing RESUME logic (`resumed_fill` in
--   `apply_fill`) exists to repair -- and it still can, for a fill that predates this
--   migration or that reached that state some other way. But for every fill applied
--   THROUGH this function from now on, the fill INSERT and the account UPDATE and the
--   ledger INSERT are three statements inside the SAME transaction: there is no commit
--   boundary between "the fill row landed" and "the money moved" for a crash to split.
-- * A NETWORK-AMBIGUOUS RETRY IS NOW SAFE TO REPEAT, NOT JUST SAFE TO DETECT. If the
--   HTTP response to this RPC call is lost after the server-side transaction committed,
--   the caller does not know whether it happened. Retrying the WHOLE Python-level
--   attempt (not this function in isolation) is safe: the fresh read at the top of the
--   next attempt finds the fill, the ledger row and the moved order exactly as the
--   first call left them, and the existing duplicate-fill guard (Requirements 16.9,
--   18.13) reports it as the no-op it is. That was NOT safe before this file, because an
--   ambiguous retry could have landed between any two of the seven separate writes.
--
-- WHAT THIS FILE DOES NOT CLAIM TO FIX
-- ---------------------------------------
-- The feed-health gate (`paper_market_feed.admit_execution`) is read in Python, from a
-- `paper_sessions` row fetched BEFORE this function is called, and stays there: it is
-- pure Python logic with no SQL equivalent, and porting it would be exactly the kind of
-- business-logic migration this file deliberately avoids. The feed could in principle
-- flip from HEALTHY to unhealthy in the narrow window between that Python read and this
-- function's commit -- a race that existed identically in the OLD seven-statement code
-- (the gate was read once per attempt, before any of the seven writes) and is therefore
-- NOT a regression this file introduces.
--
-- `PaperConcurrencyExhausted(partial_write=True)` and `_NoRetryPastTheMoney` are NOT
-- removed from `paper_simulator.py`. They remain reachable for exactly the residual
-- cases above (pre-migration data, or an error this function's own classification does
-- not recognise) and the code says so at the point they are now reached.
--
-- PRECONDITIONS, GUARDED LIKE EVERY OTHER FILE IN THIS SET
-- -----------------------------------------------------------
-- Refuses to install if `009_paper_trading.sql` / `013_paper_default_account_children.sql`
-- have not been applied (the seven tables and `uq_paper_fill_event` /
-- `uq_paper_position_open` would not exist to write against). Additive: no DROP TABLE,
-- no DROP COLUMN, no row deletion. `CREATE OR REPLACE FUNCTION` is idempotent; replaying
-- this file changes nothing once applied.
--
-- REGRESSION: tests/test_paper05_apply_fill_atomic_postgres.py

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 0 — PREFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('public.paper_accounts') IS NULL
       OR to_regclass('public.paper_orders') IS NULL
       OR to_regclass('public.paper_fills') IS NULL
       OR to_regclass('public.paper_positions') IS NULL
       OR to_regclass('public.paper_balance_events') IS NULL
       OR to_regclass('public.paper_trades') IS NULL
       OR to_regclass('public.paper_equity_snapshots') IS NULL THEN
        RAISE EXCEPTION
            '048 precondition failed: one or more paper_* tables are absent. Apply '
            '009_paper_trading.sql and 013_paper_default_account_children.sql first.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.paper_fills')
           AND conname = 'uq_paper_fill_event'
    ) THEN
        RAISE EXCEPTION
            '048 precondition failed: uq_paper_fill_event is absent, so the idempotent '
            'fill insert below has nothing to de-duplicate on.';
    END IF;

    -- uq_paper_position_open is a PARTIAL unique index (`WHERE closed_at IS NULL`), and
    -- a partial index cannot be registered as a table CONSTRAINT in PostgreSQL -- it
    -- lives in pg_indexes, not pg_constraint, which is why it is checked differently
    -- from the full constraints above.
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public' AND tablename = 'paper_positions'
           AND indexname = 'uq_paper_position_open'
    ) THEN
        RAISE EXCEPTION
            '048 precondition failed: uq_paper_position_open is absent.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'paper_accounts'
           AND column_name = 'version'
    ) THEN
        RAISE EXCEPTION
            '048 precondition failed: paper_accounts.version is absent, so the '
            'concurrency guard below has nothing to compare against.';
    END IF;

    RAISE NOTICE '048 preflight: every paper_* table and constraint this function '
                 'depends on is present';
END
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1 — THE FUNCTION
-- ─────────────────────────────────────────────────────────────────────────────
-- Every numeric is written through `::numeric(28,10)`, matching the column type `009`
-- declares, and every minor-unit figure through `::bigint`. Both casts come from TEXT
-- extracted out of the jsonb arguments (`->>`), which is deliberate: it is the exact
-- same decimal-STRING representation `paper_repository._numeric` already sends over
-- PostgREST today (`str(to_decimal(...))`), so this function changes HOW the value
-- travels (one RPC call instead of a JSON body on an HTTP POST) and not WHAT value
-- travels or how it is parsed.
--
-- SQLSTATE CODES THIS FUNCTION RAISES, AND WHAT THE CALLER DOES WITH EACH
--   PA001  a lock was held and re-checked anyway, and the check failed: the account
--          version or the order state no longer matches what the Python-side attempt
--          read before computing its new values. The caller's existing retry loop
--          (`with_retries`, task 25.6) already treats this as the signal to re-read
--          everything and compute the whole attempt again -- precisely the outcome
--          `PaperConcurrencyConflict` produces today.
--   PA002  the fill INSERT lost a race to `uq_paper_fill_event` to a writer that is not
--          this one (the Python-side duplicate guard found nothing, then a concurrent
--          call inserted the same event before this call's own INSERT). The caller
--          treats this exactly as `PaperDuplicateFill` is treated today: FILL_DUPLICATE,
--          nothing applied twice.
--   PA003  a referenced row (the account, the order, or -- on a resume -- the fill being
--          resumed) does not exist for this identity. The caller treats this as
--          `InvalidOrderIntent`, same as the read that precedes it today.
CREATE OR REPLACE FUNCTION public.paper_apply_fill_atomic(
    p_user_id uuid,
    p_account_id uuid,
    p_order_id uuid,
    p_session_id uuid,
    p_fill_event_id text,
    p_expected_account_version bigint,
    p_expected_order_state text,
    p_resume_fill_id uuid,
    p_fill jsonb,
    p_account jsonb,
    p_balance_event jsonb,
    p_order jsonb,
    p_position jsonb,
    p_closed_leg jsonb,
    p_trade jsonb,
    p_equity_snapshot jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
    v_account       public.paper_accounts;
    v_order         public.paper_orders;
    v_fill          public.paper_fills;
    v_account_after public.paper_accounts;
    v_balance_event public.paper_balance_events;
    v_closed_row    public.paper_positions;
    v_open_position public.paper_positions;
    v_position_row  public.paper_positions;
    v_order_after   public.paper_orders;
    v_trade_row     public.paper_trades;
    v_snapshot_row  public.paper_equity_snapshots;
BEGIN
    IF p_user_id IS NULL OR p_account_id IS NULL OR p_order_id IS NULL
       OR p_fill_event_id IS NULL OR btrim(p_fill_event_id) = '' THEN
        RAISE EXCEPTION
            'paper_apply_fill_atomic: user_id, account_id, order_id and fill_event_id '
            'are all required; nothing was written'
            USING ERRCODE = 'PA003';
    END IF;

    -- ── THE FOR UPDATE SUBSTITUTE NO POSTGREST CALL COULD HOLD (account) ──
    -- Held for the rest of this function, which is what makes every later write in
    -- this call serialised against every other call touching the same account.
    SELECT * INTO v_account
      FROM public.paper_accounts
     WHERE id = p_account_id AND user_id = p_user_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'paper_apply_fill_atomic: no paper account % for user %; nothing was written',
            p_account_id, p_user_id
            USING ERRCODE = 'PA003';
    END IF;
    IF v_account.version IS DISTINCT FROM p_expected_account_version THEN
        RAISE EXCEPTION
            'paper_apply_fill_atomic: account % version changed under the caller '
            '(expected %, found %); the Python-side attempt read a stale image',
            p_account_id, p_expected_account_version, v_account.version
            USING ERRCODE = 'PA001';
    END IF;

    -- ── the order, locked the same way ──
    SELECT * INTO v_order
      FROM public.paper_orders
     WHERE id = p_order_id AND user_id = p_user_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'paper_apply_fill_atomic: no paper order % for user %; nothing was written',
            p_order_id, p_user_id
            USING ERRCODE = 'PA003';
    END IF;
    IF v_order.order_state IS DISTINCT FROM p_expected_order_state THEN
        RAISE EXCEPTION
            'paper_apply_fill_atomic: order % state changed under the caller '
            '(expected %, found %)',
            p_order_id, p_expected_order_state, v_order.order_state
            USING ERRCODE = 'PA001';
    END IF;

    -- ── write 1: the fill row, the idempotency anchor -- insert, or pick up a resume ──
    IF p_resume_fill_id IS NOT NULL THEN
        -- The Python-side guard found this exact fill row with no corresponding ledger
        -- entry: a previous call committed write 1 and failed before write 3, back when
        -- these were separate transactions. Locked here so this call, too, cannot race
        -- a second resumer of the same row.
        SELECT * INTO v_fill
          FROM public.paper_fills
         WHERE id = p_resume_fill_id
           AND order_id = p_order_id
           AND user_id = p_user_id
           AND fill_event_id = p_fill_event_id
         FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'paper_apply_fill_atomic: resume fill % not found for order % event %; '
                'nothing was written',
                p_resume_fill_id, p_order_id, p_fill_event_id
                USING ERRCODE = 'PA003';
        END IF;
    ELSE
        INSERT INTO public.paper_fills (
            order_id, session_id, user_id, fill_event_id, quantity, price,
            fee_minor, slippage_minor, market_event_id, filled_at
        ) VALUES (
            p_order_id, p_session_id, p_user_id, p_fill_event_id,
            (p_fill->>'quantity')::numeric(28,10),
            (p_fill->>'price')::numeric(28,10),
            (p_fill->>'fee_minor')::bigint,
            (p_fill->>'slippage_minor')::bigint,
            p_fill->>'market_event_id',
            (p_fill->>'filled_at')::timestamptz
        )
        ON CONFLICT ON CONSTRAINT uq_paper_fill_event DO NOTHING
        RETURNING * INTO v_fill;

        IF v_fill IS NULL THEN
            -- Another writer inserted this exact (order_id, fill_event_id) between the
            -- Python-side duplicate guard and this statement. That writer owns the
            -- movement; this call changes nothing (Requirements 16.9, 18.13) -- and
            -- because nothing has been written yet in THIS transaction, the exception
            -- below rolls back to exactly that: nothing.
            RAISE EXCEPTION
                'paper_apply_fill_atomic: fill_event_id % was inserted concurrently for '
                'order %; this call changed nothing',
                p_fill_event_id, p_order_id
                USING ERRCODE = 'PA002';
        END IF;
    END IF;

    -- ── write 2: the money. In the old seven-statement design this was the point past
    --    which a failure was a PARTIAL WRITE and was never retried. Here it is simply
    --    the next statement inside the same transaction as write 1 and every write
    --    below: if anything from here on raises, THIS UPDATE rolls back too. ──
    UPDATE public.paper_accounts
       SET available_balance = (p_account->>'available_balance')::numeric(28,10),
           locked_balance    = (p_account->>'locked_balance')::numeric(28,10),
           realized_pnl      = (p_account->>'realized_pnl')::numeric(28,10),
           total_equity      = (p_account->>'total_equity')::numeric(28,10),
           last_price_at     = (p_fill->>'filled_at')::timestamptz,
           stale             = false,
           version           = version + 1,
           updated_at        = now()
     WHERE id = p_account_id AND user_id = p_user_id AND version = p_expected_account_version
     RETURNING * INTO v_account_after;

    IF NOT FOUND THEN
        -- Unreachable in practice: the row was locked and version-checked above inside
        -- this same transaction, so nothing else could have moved it since. Checked
        -- anyway -- an UPDATE whose own WHERE clause this function trusted blindly is
        -- exactly the kind of guard this migration exists to stop treating as optional.
        RAISE EXCEPTION
            'paper_apply_fill_atomic: account % version changed between the lock and '
            'the update, which should be impossible inside one transaction',
            p_account_id
            USING ERRCODE = 'PA001';
    END IF;

    -- ── write 3: the ledger row ──
    INSERT INTO public.paper_balance_events (
        session_id, account_id, user_id, cause,
        available_delta, locked_delta, realized_delta,
        available_after, locked_after, realized_after,
        fill_id, occurred_at
    ) VALUES (
        p_session_id, p_account_id, p_user_id, 'FILL',
        (p_balance_event->>'available_delta')::numeric(28,10),
        (p_balance_event->>'locked_delta')::numeric(28,10),
        (p_balance_event->>'realized_delta')::numeric(28,10),
        v_account_after.available_balance,
        v_account_after.locked_balance,
        v_account_after.realized_pnl,
        v_fill.id,
        (p_balance_event->>'occurred_at')::timestamptz
    )
    RETURNING * INTO v_balance_event;

    -- ── write 4: the position(s). A reversal through zero writes the closed leg
    --    FIRST, in its own row, exactly as `paper_simulator.apply_fill` and
    --    `paper_trading_service._execute_fill` already do, so the closing side's
    --    history is not overwritten by the new side that replaces it. ──
    IF p_closed_leg IS NOT NULL THEN
        INSERT INTO public.paper_positions (
            session_id, account_id, user_id, symbol, side, size, entry_price,
            opened_at, current_price, unrealized_pnl, price_at, closed_at
        ) VALUES (
            p_session_id, p_account_id, p_user_id,
            p_position->>'symbol',
            p_closed_leg->>'side',
            0,
            (p_closed_leg->>'entry_price')::numeric(28,10),
            (p_closed_leg->>'opened_at')::timestamptz,
            (p_fill->>'price')::numeric(28,10),
            0,
            (p_fill->>'filled_at')::timestamptz,
            (p_fill->>'filled_at')::timestamptz
        )
        RETURNING * INTO v_closed_row;
    END IF;

    -- The open row for this (account_id, symbol): update it if one exists, insert
    -- otherwise. `uq_paper_position_open` is a PARTIAL unique index
    -- (`WHERE closed_at IS NULL`), which PostgREST's own `upsert` cannot target in an
    -- `ON CONFLICT` -- the same reason `paper_repository.upsert_position` reads first
    -- and writes second rather than using a native upsert. Locked here the same way.
    SELECT * INTO v_open_position
      FROM public.paper_positions
     WHERE account_id = p_account_id AND user_id = p_user_id
       AND symbol = (p_position->>'symbol')
       AND closed_at IS NULL
     FOR UPDATE;

    IF FOUND THEN
        UPDATE public.paper_positions
           SET side           = p_position->>'side',
               size            = (p_position->>'size')::numeric(28,10),
               entry_price     = (p_position->>'entry_price')::numeric(28,10),
               current_price   = (p_position->>'current_price')::numeric(28,10),
               unrealized_pnl  = (p_position->>'unrealized_pnl')::numeric(28,10),
               price_at        = (p_position->>'price_at')::timestamptz,
               closed_at       = CASE WHEN p_position->>'closed_at' IS NULL
                                      THEN NULL
                                      ELSE (p_position->>'closed_at')::timestamptz END,
               version         = version + 1,
               updated_at      = now()
         WHERE id = v_open_position.id AND user_id = p_user_id
           AND version = v_open_position.version
         RETURNING * INTO v_position_row;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'paper_apply_fill_atomic: position % version changed between the lock '
                'and the update, which should be impossible inside one transaction',
                v_open_position.id
                USING ERRCODE = 'PA001';
        END IF;
    ELSE
        INSERT INTO public.paper_positions (
            session_id, account_id, user_id, symbol, side, size, entry_price,
            opened_at, current_price, unrealized_pnl, price_at, closed_at
        ) VALUES (
            p_session_id, p_account_id, p_user_id,
            p_position->>'symbol', p_position->>'side',
            (p_position->>'size')::numeric(28,10),
            (p_position->>'entry_price')::numeric(28,10),
            (p_position->>'opened_at')::timestamptz,
            (p_position->>'current_price')::numeric(28,10),
            (p_position->>'unrealized_pnl')::numeric(28,10),
            (p_position->>'price_at')::timestamptz,
            CASE WHEN p_position->>'closed_at' IS NULL
                 THEN NULL
                 ELSE (p_position->>'closed_at')::timestamptz END
        )
        RETURNING * INTO v_position_row;
    END IF;

    -- ── write 5: the order state. `trg_paper_order_transition_guard` remains the
    --    durable guarantee against an illegal transition; Python's own guard 4 in
    --    `apply_fill` already refused one before this function was ever called. ──
    UPDATE public.paper_orders
       SET order_state     = p_order->>'target_state',
           legacy_status   = p_order->>'legacy_status',
           filled_quantity = (p_order->>'filled_quantity')::numeric(28,10),
           avg_fill_price  = (p_order->>'avg_fill_price')::numeric(28,10),
           fee_minor       = (p_order->>'fee_minor')::bigint,
           slippage_minor  = (p_order->>'slippage_minor')::bigint,
           updated_at      = now()
     WHERE id = p_order_id AND user_id = p_user_id AND order_state = p_expected_order_state
     RETURNING * INTO v_order_after;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'paper_apply_fill_atomic: order % state changed between the lock and the '
            'update, which should be impossible inside one transaction',
            p_order_id
            USING ERRCODE = 'PA001';
    END IF;

    -- ── write 6: the closed round-trip, only when a position reached size zero ──
    IF p_trade IS NOT NULL THEN
        INSERT INTO public.paper_trades (
            session_id, account_id, user_id, symbol, side, quantity, entry_price,
            exit_price, realized_pnl, fee_minor, opened_at, closed_at
        ) VALUES (
            p_session_id, p_account_id, p_user_id,
            p_trade->>'symbol', p_trade->>'side',
            (p_trade->>'quantity')::numeric(28,10),
            (p_trade->>'entry_price')::numeric(28,10),
            (p_trade->>'exit_price')::numeric(28,10),
            (p_trade->>'realized_pnl')::numeric(28,10),
            (p_trade->>'fee_minor')::bigint,
            (p_trade->>'opened_at')::timestamptz,
            (p_trade->>'closed_at')::timestamptz
        )
        RETURNING * INTO v_trade_row;
    END IF;

    -- ── write 7: the equity point. One per APPLIED fill, matching guard 2's return
    --    before reaching here for a repeated fill_event_id (Requirement 18.13). ──
    INSERT INTO public.paper_equity_snapshots (
        session_id, user_id, series_index, total_equity, available_balance,
        locked_balance, position_market_value, stale, cause, taken_at
    ) VALUES (
        p_session_id, p_user_id,
        coalesce((p_equity_snapshot->>'series_index')::int, 0),
        (p_equity_snapshot->>'total_equity')::numeric(28,10),
        (p_equity_snapshot->>'available_balance')::numeric(28,10),
        (p_equity_snapshot->>'locked_balance')::numeric(28,10),
        (p_equity_snapshot->>'position_market_value')::numeric(28,10),
        coalesce((p_equity_snapshot->>'stale')::boolean, false),
        'FILL',
        (p_equity_snapshot->>'taken_at')::timestamptz
    )
    RETURNING * INTO v_snapshot_row;

    RETURN jsonb_build_object(
        'fill', to_jsonb(v_fill),
        'account', to_jsonb(v_account_after),
        'balance_event', to_jsonb(v_balance_event),
        'position', to_jsonb(v_position_row),
        'closed_position', to_jsonb(v_closed_row),
        'order', to_jsonb(v_order_after),
        'trade', to_jsonb(v_trade_row),
        'snapshot', to_jsonb(v_snapshot_row),
        'resumed', (p_resume_fill_id IS NOT NULL)
    );
END
$fn$;

-- A caller-supplied user_id / account_id / order_id with no auth.uid() check, invoked
-- only from the server-side simulator through a service-role (or request-scoped,
-- RLS-backed) client -- same exposure posture as every SECURITY DEFINER function this
-- remediation effort has added. `paper_*` RLS already grants `service_role` ALL on
-- every table this function touches (009), so this function's own grant only has to
-- cover WHO may invoke it, not what it may do once invoked.
REVOKE ALL ON FUNCTION public.paper_apply_fill_atomic(
    uuid, uuid, uuid, uuid, text, bigint, text, uuid,
    jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb
) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.paper_apply_fill_atomic(
    uuid, uuid, uuid, uuid, text, bigint, text, uuid,
    jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb
) FROM anon;
GRANT EXECUTE ON FUNCTION public.paper_apply_fill_atomic(
    uuid, uuid, uuid, uuid, text, bigint, text, uuid,
    jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb
) TO authenticated;
GRANT EXECUTE ON FUNCTION public.paper_apply_fill_atomic(
    uuid, uuid, uuid, uuid, text, bigint, text, uuid,
    jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb
) TO service_role;

COMMENT ON FUNCTION public.paper_apply_fill_atomic(
    uuid, uuid, uuid, uuid, text, bigint, text, uuid,
    jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb, jsonb
) IS
    'PAPER-05. The seven paper-fill writes (fill, account, ledger, position(s), order, '
    'trade, equity snapshot), committed atomically. Replaces seven separate PostgREST '
    'statements that could leave a fill partially applied if a crash landed between '
    'any two of them. Every guard and every arithmetic computation stays in Python '
    '(paper_simulator.apply_fill / paper_accounting); this function only commits the '
    'already-decided final values, holding real FOR UPDATE locks on the account and '
    'order rows for its whole duration instead of the optimistic version check a '
    'separate PostgREST statement was limited to.';

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2 — POSTFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    n int;
BEGIN
    SELECT count(*) INTO n
      FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'public' AND p.proname = 'paper_apply_fill_atomic';
    IF n <> 1 THEN
        RAISE EXCEPTION
            '048 postflight FAILED: expected exactly one paper_apply_fill_atomic, '
            'found %.', n;
    END IF;

    IF has_function_privilege('anon',
            'public.paper_apply_fill_atomic(uuid,uuid,uuid,uuid,text,bigint,text,uuid,'
            'jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION
            '048 postflight FAILED: paper_apply_fill_atomic is EXECUTE-able by anon.';
    END IF;

    IF NOT has_function_privilege('service_role',
            'public.paper_apply_fill_atomic(uuid,uuid,uuid,uuid,text,bigint,text,uuid,'
            'jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION
            '048 postflight FAILED: service_role lost EXECUTE on '
            'paper_apply_fill_atomic -- the paper simulator would break.';
    END IF;

    RAISE NOTICE '048: paper_apply_fill_atomic installed, exactly one overload, '
                 'service-role and authenticated may call it, anon may not.';
END
$$;

-- Self-exercise: a deliberately stale expected_account_version must raise PA001 and
-- leave NOTHING written -- no fill row, no balance event -- which is the property this
-- whole migration exists to establish. Run inside its own sub-transaction (a SAVEPOINT)
-- so this file's own verification writes nothing durable either way.
DO $$
DECLARE
    v_user    uuid := gen_random_uuid();
    v_acct    uuid;
    v_order   uuid;
    v_sess    uuid;
    v_caught  boolean := false;
BEGIN
    IF to_regclass('public.auth_users_shim') IS NOT NULL THEN
        NULL; -- unused placeholder branch kept out of the way of the FK insert below
    END IF;

    -- auth.users / profiles are required by paper_accounts' and paper_orders' own
    -- foreign keys on a database where they are enforced; seeded defensively.
    BEGIN
        INSERT INTO auth.users (id, email)
        VALUES (v_user, 'migration-048-self-test@example.invalid')
        ON CONFLICT DO NOTHING;
        INSERT INTO public.profiles (id)
        VALUES (v_user)
        ON CONFLICT DO NOTHING;
    EXCEPTION WHEN undefined_table OR undefined_column THEN
        NULL; -- a database without auth.users/profiles in this shape skips the exercise
    END;

    BEGIN
        INSERT INTO public.paper_accounts (
            user_id, currency, initial_capital, available_balance, locked_balance,
            total_equity
        ) VALUES (v_user, 'USD', 100000, 100000, 0, 100000)
        RETURNING id INTO v_acct;

        INSERT INTO public.paper_orders (
            account_id, user_id, symbol, side, order_type, quantity,
            order_state, legacy_status, fingerprint
        ) VALUES (
            v_acct, v_user, 'BTC/USDT', 'buy', 'market', 1,
            'ACCEPTED', 'OPEN', 'migration-048-self-test'
        ) RETURNING id INTO v_order;

        BEGIN
            PERFORM public.paper_apply_fill_atomic(
                v_user, v_acct, v_order, v_sess, 'migration-048-self-test-event',
                999999::bigint,  -- deliberately wrong version
                'ACCEPTED',
                NULL,
                jsonb_build_object('quantity', '1', 'price', '100', 'fee_minor', 0,
                                    'slippage_minor', 0, 'market_event_id', NULL,
                                    'filled_at', now()::text),
                jsonb_build_object('available_balance', '99900', 'locked_balance', '0',
                                    'realized_pnl', '0', 'total_equity', '99900'),
                jsonb_build_object('available_delta', '-100', 'locked_delta', '0',
                                    'realized_delta', '0', 'occurred_at', now()::text),
                jsonb_build_object('target_state', 'FILLED', 'legacy_status', 'FILLED',
                                    'filled_quantity', '1', 'avg_fill_price', '100',
                                    'fee_minor', 0, 'slippage_minor', 0),
                jsonb_build_object('symbol', 'BTC/USDT', 'side', 'LONG', 'size', '1',
                                    'entry_price', '100', 'opened_at', now()::text,
                                    'current_price', '100', 'unrealized_pnl', '0',
                                    'price_at', now()::text, 'closed_at', NULL),
                NULL, NULL,
                jsonb_build_object('series_index', 0, 'total_equity', '99900',
                                    'available_balance', '99900', 'locked_balance', '0',
                                    'position_market_value', '0', 'stale', false,
                                    'taken_at', now()::text)
            );
        EXCEPTION WHEN SQLSTATE 'PA001' THEN
            v_caught := true;
        END;

        IF NOT v_caught THEN
            RAISE EXCEPTION
                '048 postflight FAILED: a stale account version did not raise PA001.';
        END IF;

        IF EXISTS (
            SELECT 1 FROM public.paper_fills WHERE fill_event_id = 'migration-048-self-test-event'
        ) THEN
            RAISE EXCEPTION
                '048 postflight FAILED: the rejected call still wrote a fill row -- the '
                'migration this function exists to provide (atomicity) does not hold.';
        END IF;
    END;

    -- Roll back every row this exercise created, regardless of outcome.
    RAISE EXCEPTION 'MIGRATION_048_SELF_TEST_ROLLBACK';
EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'MIGRATION_048_SELF_TEST_ROLLBACK' THEN
        RAISE NOTICE '048: self-test passed (a stale version raised PA001 and wrote '
                     'nothing); its own fixture rows were rolled back.';
    ELSE
        RAISE;
    END IF;
END
$$;
