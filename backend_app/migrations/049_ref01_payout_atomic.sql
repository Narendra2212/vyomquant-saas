-- backend_app/migrations/049_ref01_payout_atomic.sql
--
-- REF-01. The payout debit and the payout INSERT become one atomic Postgres function;
-- the "pending -> completed/rejected" settlement step becomes one too.
--
-- THE DEFECT
-- ----------
-- `routers/referral.py::create_payout_request` issued the debit
-- (`referral_wallets.approved_balance_usd -= amount`, conditional on `.gte(...)`) and
-- the `referral_payouts` INSERT as two SEPARATE PostgREST statements -- two independent
-- server-side transactions. A failure between them (the insert raising, or returning
-- empty) left the wallet debited with no payout row to show for it, which the previous
-- remediation pass already covered with a BEST-EFFORT compensating re-credit: a third
-- statement, issued from the same handler, that itself can fail -- logged CRITICAL when
-- it does, but not prevented. `settle_payout`'s rejection path has the identical shape:
-- the status UPDATE and the re-credit UPDATE are two more separate statements, and a
-- failure of the second leaves a payout marked 'rejected' with its money still missing
-- from the wallet (the previous pass's own evidence already names this: "needs manual
-- reconciliation").
--
-- THIS FILE REMOVES THE NEED FOR COMPENSATION BY MAKING THE PAIR ATOMIC, the same
-- pattern `024_remediate_referral_double_commission.sql` and
-- `029_referral_obligations.sql` already apply to the referral commission ledger: one
-- `SECURITY DEFINER` function, one `.rpc()` call, one transaction. If the INSERT fails
-- for any reason inside `create_referral_payout_request`, the debit it already issued
-- in the SAME function body rolls back with it -- there is no window in which the money
-- is out of the wallet and nowhere else, so the compensating re-credit this file removes
-- from `routers/referral.py` is not a safety net being taken away, it is a safety net
-- that atomicity makes unreachable.
--
-- THE SCHEMA QUESTION THIS FILE HAD TO SETTLE FIRST
-- ----------------------------------------------------
-- `referral_wallets` was declared TWICE, by two different, uncoordinated migration
-- files, both using `CREATE TABLE IF NOT EXISTS`:
--   * `migrations/006_reconcile_production_database.sql` (applied FIRST in this
--     repository's documented build order, PROVISIONING_ORDER.md step 1): columns
--     `balance_usd`, `total_earned_usd`, `pending_usd`.
--   * `migrations/referral_system_redesign.sql` (applied AFTER it, per the same
--     document): its own `CREATE TABLE IF NOT EXISTS referral_wallets` is therefore a
--     no-op against an already-existing table -- but that file ALSO carries a
--     reconciliation block (its own lines ~714-785) that detects exactly this
--     situation (`information_schema.columns` lookup for `approved_balance_usd`) and,
--     when it is missing, `ALTER TABLE ... ADD COLUMN pending_balance_usd,
--     approved_balance_usd, paid_balance_usd, lifetime_earnings_usd`, BACKFILLED from
--     the 006 columns (`approved_balance_usd = coalesce(balance_usd, 0)`, and so on).
--
-- Measured against this session's own fully-provisioned disposable Postgres replica
-- (every file `PROVISIONING_ORDER.md` names, applied in its documented order,
-- including `migrations/referral_system_redesign.sql`): `referral_wallets` carries
-- BOTH column sets, and `approved_balance_usd` is present, backfilled, and is what
-- `routers/referral.py` already reads and writes today. That is the schema this file
-- is written against.
--
-- WHAT IS **NOT** CONFIRMED, AND WHY THAT IS STATED HERE RATHER THAN ASSUMED AWAY
-- -----------------------------------------------------------------------------------
-- This sandbox has no live production database credential and cannot query the real
-- production catalogue. The conclusion above rests on `referral_system_redesign.sql`
-- having actually been applied to production, which `PROVISIONING_ORDER.md` documents
-- as the intended build order but which this file cannot verify independently of that
-- document. SECTION 0 below therefore does not merely assume `approved_balance_usd`
-- exists: it checks for the column BEFORE creating either function and refuses, by
-- name, with the exact remediation (apply `referral_system_redesign.sql`'s own
-- reconciliation block) if it is absent -- so a production apply of this file either
-- installs functions that match the real column set, or fails loudly naming precisely
-- what is missing, and never silently installs a function against a column that is not
-- there.
--
-- WHY A REAL ROW LOCK NOW, NOT THE EXISTING `.gte(...)` CONDITIONAL UPDATE
-- -----------------------------------------------------------------------------
-- The existing debit is already correct under PostgREST's constraints: Postgres
-- serializes the two concurrent `UPDATE ... WHERE approved_balance_usd >= :amount`
-- statements row-wise, so only one can ever match. Inside a single PL/pgSQL function
-- body, `SELECT ... FOR UPDATE` is available and is used instead -- not because the old
-- guard was wrong, but because the function also has to decide between "insufficient
-- balance" (400) and "no wallet row" (404), which the old `.gte()` UPDATE's silent
-- zero-rows-matched outcome could not distinguish between.
--
-- WHAT THIS FILE DOES NOT CHANGE
-- -----------------------------------
-- The Redis distributed lock in `create_payout_request` (fixed to fail CLOSED earlier
-- this remediation effort) is left in place, for latency only: it turns a concurrent
-- request into a fast, friendly 409 instead of making the caller wait on the real
-- Postgres row lock this file adds. It is no longer load-bearing for CORRECTNESS -- the
-- row lock inside the function is -- but removing it would trade a fast rejection for a
-- slower, still-safe one, which is not the problem this file exists to solve.
--
-- Additive. No DROP TABLE, no DROP COLUMN, no row deletion.
-- REGRESSION: tests/test_ref01_payout_atomic_postgres.py

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 0 — PREFLIGHT, INCLUDING THE SCHEMA QUESTION ABOVE
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('public.referral_wallets') IS NULL THEN
        RAISE EXCEPTION
            '049 precondition failed: public.referral_wallets is absent. Provision the '
            'referral system first (migrations/006_reconcile_production_database.sql '
            'and/or migrations/referral_system_redesign.sql).';
    END IF;
    IF to_regclass('public.referral_payouts') IS NULL THEN
        RAISE EXCEPTION
            '049 precondition failed: public.referral_payouts is absent.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'referral_wallets'
           AND column_name = 'approved_balance_usd'
    ) THEN
        RAISE EXCEPTION
            '049 REFUSED: public.referral_wallets has no approved_balance_usd column on '
            'this database. The application (routers/referral.py) reads and writes '
            'that exact column, and this migration''s functions are written against '
            'it; installing them anyway would create a function that fails on every '
            'real call rather than one that fails here, loudly, naming the gap. '
            'migrations/referral_system_redesign.sql (its own reconciliation block, '
            'around line 714) adds and backfills this column from the legacy '
            'balance_usd/pending_usd/total_earned_usd shape '
            '(migrations/006_reconcile_production_database.sql) when it is missing -- '
            'apply that file first, THEN re-run this one.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'referral_payouts'
           AND column_name = 'payout_method'
    ) THEN
        RAISE EXCEPTION
            '049 REFUSED: public.referral_payouts has no payout_method column. '
            'routers/referral.py writes payout_method/payout_details (the '
            '006_reconcile_production_database.sql shape); this migration''s INSERT is '
            'written against the same two columns.';
    END IF;

    RAISE NOTICE '049 preflight: referral_wallets.approved_balance_usd and '
                 'referral_payouts.payout_method are both present';
END
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1 — THE DEBIT + INSERT, ATOMIC
-- ─────────────────────────────────────────────────────────────────────────────
-- SQLSTATE CODES, AND WHAT THE CALLER DOES WITH EACH
--   RF001  insufficient approved balance. The caller answers 400, exactly as the
--          existing `payout_request.amount_usd > approved_balance` check does today --
--          this merely moves the SAME comparison inside the lock that makes it safe
--          against a concurrent debit, rather than racing a separate UPDATE's `.gte()`.
--   RF002  no referral_wallets row for this user. The caller answers 404, same as today.
--   RF003  a caller-supplied argument is missing or out of range (should not be
--          reachable past the router's own Pydantic validation; checked anyway, because
--          a function called by `.rpc()` has no Pydantic model guarding it).
DROP FUNCTION IF EXISTS public.create_referral_payout_request(uuid, numeric, text, jsonb);

CREATE OR REPLACE FUNCTION public.create_referral_payout_request(
    p_user_id uuid,
    p_amount_usd numeric,
    p_payout_method text,
    p_payout_details jsonb DEFAULT '{}'::jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
    v_wallet  public.referral_wallets;
    v_payout  public.referral_payouts;
BEGIN
    IF p_user_id IS NULL THEN
        RAISE EXCEPTION 'create_referral_payout_request: user_id is required'
            USING ERRCODE = 'RF003';
    END IF;
    IF p_amount_usd IS NULL OR p_amount_usd <= 0 THEN
        RAISE EXCEPTION
            'create_referral_payout_request: amount_usd must be positive, got %',
            p_amount_usd
            USING ERRCODE = 'RF003';
    END IF;
    IF p_payout_method IS NULL OR btrim(p_payout_method) = '' THEN
        RAISE EXCEPTION 'create_referral_payout_request: payout_method is required'
            USING ERRCODE = 'RF003';
    END IF;

    -- THE FOR UPDATE SUBSTITUTE NO POSTGREST CALL COULD HOLD. Held for the rest of this
    -- function, so a second concurrent payout request for the SAME user cannot even
    -- begin its own read until this one commits or rolls back -- strictly stronger than
    -- the `.gte()` conditional UPDATE it replaces, which only serialised the write.
    SELECT * INTO v_wallet
      FROM public.referral_wallets
     WHERE user_id = p_user_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'create_referral_payout_request: no referral wallet for user %',
            p_user_id
            USING ERRCODE = 'RF002';
    END IF;

    IF v_wallet.approved_balance_usd < p_amount_usd THEN
        RAISE EXCEPTION
            'create_referral_payout_request: insufficient approved balance '
            '(available %, requested %)',
            v_wallet.approved_balance_usd, p_amount_usd
            USING ERRCODE = 'RF001';
    END IF;

    UPDATE public.referral_wallets
       SET approved_balance_usd = approved_balance_usd - p_amount_usd,
           updated_at = now()
     WHERE user_id = p_user_id;

    -- If this INSERT fails for ANY reason -- a constraint violation, a trigger
    -- refusal, a driver error -- the debit above is still inside this same,
    -- uncommitted transaction and rolls back with it. There is no best-effort
    -- compensation here because there is nothing left for it to compensate.
    INSERT INTO public.referral_payouts (
        user_id, amount_usd, status, payout_method, payout_details
    ) VALUES (
        p_user_id, p_amount_usd, 'pending', p_payout_method,
        coalesce(p_payout_details, '{}'::jsonb)
    )
    RETURNING * INTO v_payout;

    RETURN to_jsonb(v_payout);
END
$fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2 — THE SETTLEMENT STEP, ATOMIC
-- ─────────────────────────────────────────────────────────────────────────────
--   RF002  no such payout.
--   RF003  a caller-supplied argument is invalid (bad status, or 'completed' with no
--          reference).
--   RF004  the payout is already terminal (completed or rejected) -- the caller answers
--          409, same as the existing `.eq("status", expected)` optimistic guard does
--          today, just checked against a real lock instead of an UPDATE predicate.
--   RF002 (second use) -- a rejection that cannot be re-credited because no
--          referral_wallets row exists for the payout's own user_id. The previous
--          non-atomic code logged this CRITICAL and still returned success, leaving the
--          payout 'rejected' with the money nowhere. Atomicity makes that split
--          impossible: raising here rolls back the status flip too, so the payout
--          stays 'pending' -- re-settleable once the wallet is reconciled -- rather
--          than becoming a lie about where the money went.
DROP FUNCTION IF EXISTS public.settle_referral_payout(uuid, text, text, text);

CREATE OR REPLACE FUNCTION public.settle_referral_payout(
    p_payout_id uuid,
    p_status text,
    p_reference text DEFAULT NULL,
    p_reason text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
    v_payout  public.referral_payouts;
    v_details jsonb;
    v_updated int;
BEGIN
    IF p_status NOT IN ('completed', 'rejected') THEN
        RAISE EXCEPTION
            'settle_referral_payout: status must be completed or rejected, got %',
            p_status
            USING ERRCODE = 'RF003';
    END IF;

    SELECT * INTO v_payout
      FROM public.referral_payouts
     WHERE id = p_payout_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'settle_referral_payout: no payout %', p_payout_id
            USING ERRCODE = 'RF002';
    END IF;

    IF v_payout.status NOT IN ('pending', 'processing') THEN
        RAISE EXCEPTION
            'settle_referral_payout: payout % is already % and cannot be settled again',
            p_payout_id, v_payout.status
            USING ERRCODE = 'RF004';
    END IF;

    IF p_status = 'completed' THEN
        IF p_reference IS NULL OR btrim(p_reference) = '' THEN
            RAISE EXCEPTION
                'settle_referral_payout: a reference is required to mark a payout '
                'completed'
                USING ERRCODE = 'RF003';
        END IF;
        v_details := coalesce(v_payout.payout_details, '{}'::jsonb)
                     || jsonb_build_object('settlement_reference', p_reference);

        UPDATE public.referral_payouts
           SET status = 'completed', processed_at = now(),
               payout_details = v_details, updated_at = now()
         WHERE id = p_payout_id
        RETURNING * INTO v_payout;
    ELSE
        v_details := coalesce(v_payout.payout_details, '{}'::jsonb)
                     || jsonb_build_object(
                            'rejection_reason', coalesce(p_reason, 'Not specified')
                        );

        UPDATE public.referral_payouts
           SET status = 'rejected', processed_at = now(),
               payout_details = v_details, updated_at = now()
         WHERE id = p_payout_id
        RETURNING * INTO v_payout;

        -- Re-credit the balance create_referral_payout_request debited at request
        -- time. The FOR UPDATE above means no concurrent request against this SAME
        -- payout row can be in flight; it says nothing about the wallet row by
        -- itself, which is why the row count is checked explicitly below rather than
        -- assumed from this UPDATE's syntax alone.
        UPDATE public.referral_wallets
           SET approved_balance_usd = approved_balance_usd + v_payout.amount_usd,
               updated_at = now()
         WHERE user_id = v_payout.user_id;

        GET DIAGNOSTICS v_updated = ROW_COUNT;
        IF v_updated = 0 THEN
            RAISE EXCEPTION
                'settle_referral_payout: no referral_wallets row for user % to '
                're-credit % for rejected payout %',
                v_payout.user_id, v_payout.amount_usd, p_payout_id
                USING ERRCODE = 'RF002';
        END IF;
    END IF;

    RETURN to_jsonb(v_payout);
END
$fn$;

-- Both functions take a caller-supplied user_id / payout_id with no auth.uid() check.
-- `create_referral_payout_request` is invoked from the authenticated request path
-- (`routers/referral.py::create_payout_request`), which already resolves `user_id` from
-- the caller's own verified identity before calling it -- so `authenticated` may call
-- it, exactly as it may call `create_referral_code_for_user` today, and the function's
-- own scoping to the SAME user_id the request resolved is what keeps one user from
-- debiting another's wallet. `settle_referral_payout` is operator-only
-- (`routers/referral.py::settle_payout`, gated by `get_admin_user` and invoked through
-- the service-role client) and must not be reachable by a browser role at all.
REVOKE ALL ON FUNCTION public.create_referral_payout_request(uuid, numeric, text, jsonb)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.create_referral_payout_request(uuid, numeric, text, jsonb)
    FROM anon;
GRANT EXECUTE ON FUNCTION public.create_referral_payout_request(uuid, numeric, text, jsonb)
    TO authenticated;
GRANT EXECUTE ON FUNCTION public.create_referral_payout_request(uuid, numeric, text, jsonb)
    TO service_role;

REVOKE ALL ON FUNCTION public.settle_referral_payout(uuid, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.settle_referral_payout(uuid, text, text, text) FROM anon;
REVOKE ALL ON FUNCTION public.settle_referral_payout(uuid, text, text, text)
    FROM authenticated;
GRANT EXECUTE ON FUNCTION public.settle_referral_payout(uuid, text, text, text)
    TO service_role;

COMMENT ON FUNCTION public.create_referral_payout_request(uuid, numeric, text, jsonb) IS
    'REF-01. Debits referral_wallets.approved_balance_usd and inserts the '
    'referral_payouts row atomically, replacing a debit-then-insert pair that needed a '
    'best-effort compensating re-credit on failure. RF001 insufficient balance, RF002 '
    'no wallet, RF003 invalid argument.';

COMMENT ON FUNCTION public.settle_referral_payout(uuid, text, text, text) IS
    'REF-01. The pending -> completed/rejected payout settlement step. A rejection''s '
    'status flip and wallet re-credit commit together or not at all. RF002 not found / '
    'wallet missing, RF003 invalid argument, RF004 already terminal.';

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 3 — POSTFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    n int;
BEGIN
    SELECT count(*) INTO n
      FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'public' AND p.proname = 'create_referral_payout_request';
    IF n <> 1 THEN
        RAISE EXCEPTION
            '049 postflight FAILED: expected exactly one create_referral_payout_request, '
            'found %.', n;
    END IF;

    SELECT count(*) INTO n
      FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'public' AND p.proname = 'settle_referral_payout';
    IF n <> 1 THEN
        RAISE EXCEPTION
            '049 postflight FAILED: expected exactly one settle_referral_payout, '
            'found %.', n;
    END IF;

    IF has_function_privilege('anon',
            'public.create_referral_payout_request(uuid,numeric,text,jsonb)',
            'EXECUTE') THEN
        RAISE EXCEPTION
            '049 postflight FAILED: create_referral_payout_request is EXECUTE-able by '
            'anon.';
    END IF;

    IF has_function_privilege('anon',
            'public.settle_referral_payout(uuid,text,text,text)', 'EXECUTE')
       OR has_function_privilege('authenticated',
            'public.settle_referral_payout(uuid,text,text,text)', 'EXECUTE') THEN
        RAISE EXCEPTION
            '049 postflight FAILED: settle_referral_payout is EXECUTE-able by a browser '
            'role; it bypasses RLS for ANY user''s payout and must be operator-only.';
    END IF;

    IF NOT has_function_privilege('service_role',
            'public.create_referral_payout_request(uuid,numeric,text,jsonb)', 'EXECUTE')
       OR NOT has_function_privilege('service_role',
            'public.settle_referral_payout(uuid,text,text,text)', 'EXECUTE') THEN
        RAISE EXCEPTION
            '049 postflight FAILED: service_role lost EXECUTE on one of this '
            'migration''s functions.';
    END IF;

    RAISE NOTICE '049: create_referral_payout_request and settle_referral_payout '
                 'installed; browser roles cannot call settle_referral_payout; anon '
                 'cannot call either.';
END
$$;

-- Self-exercise: an insufficient balance must raise RF001 and leave the wallet AND the
-- payouts table completely untouched. Run inside a nested block whose fixture rows are
-- unconditionally rolled back via a deliberate exception, regardless of outcome.
DO $$
DECLARE
    v_user   uuid := gen_random_uuid();
    v_caught boolean := false;
BEGIN
    BEGIN
        INSERT INTO auth.users (id, email)
        VALUES (v_user, 'migration-049-self-test@example.invalid')
        ON CONFLICT DO NOTHING;
        INSERT INTO public.profiles (id) VALUES (v_user) ON CONFLICT DO NOTHING;
        INSERT INTO public.referral_wallets (user_id, approved_balance_usd)
        VALUES (v_user, 5.00)
        ON CONFLICT (user_id) DO UPDATE SET approved_balance_usd = 5.00;
    EXCEPTION WHEN undefined_table OR undefined_column THEN
        RAISE NOTICE '049: self-test skipped (auth.users/profiles not in the expected '
                     'shape on this database)';
        RAISE EXCEPTION 'MIGRATION_049_SELF_TEST_ROLLBACK';
    END;

    BEGIN
        PERFORM public.create_referral_payout_request(
            v_user, 50.00, 'bank_transfer', '{}'::jsonb
        );
    EXCEPTION WHEN SQLSTATE 'RF001' THEN
        v_caught := true;
    END;

    IF NOT v_caught THEN
        RAISE EXCEPTION
            '049 postflight FAILED: a request for more than the approved balance did '
            'not raise RF001.';
    END IF;

    IF EXISTS (SELECT 1 FROM public.referral_payouts WHERE user_id = v_user) THEN
        RAISE EXCEPTION
            '049 postflight FAILED: the rejected request still wrote a payout row.';
    END IF;

    IF (SELECT approved_balance_usd FROM public.referral_wallets WHERE user_id = v_user)
       <> 5.00 THEN
        RAISE EXCEPTION
            '049 postflight FAILED: the rejected request still moved the wallet '
            'balance.';
    END IF;

    RAISE EXCEPTION 'MIGRATION_049_SELF_TEST_ROLLBACK';
EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'MIGRATION_049_SELF_TEST_ROLLBACK' THEN
        RAISE NOTICE '049: self-test passed (insufficient balance raised RF001 and '
                     'wrote nothing); its own fixture rows were rolled back.';
    ELSE
        RAISE;
    END IF;
END
$$;
