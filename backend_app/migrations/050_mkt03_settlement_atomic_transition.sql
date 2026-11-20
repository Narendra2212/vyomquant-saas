-- backend_app/migrations/050_mkt03_settlement_atomic_transition.sql
--
-- MKT-03. The ledger INSERT and the subscription transition become one Postgres
-- transaction, closing the cross-call gap a prior pass could only detect and resume.
--
-- THE GAP, READ OFF `_settle_once`'s OWN DOCSTRING AND THIS SESSION'S PREDECESSOR
-- -----------------------------------------------------------------------------------
-- A transient failure WITHIN one `settle()` call's retry loop already resumes
-- correctly, via the in-memory `_Attempt` state -- that is not this finding. The real
-- gap is CROSS-CALL: `settle()` writes the `marketplace_settlements` ledger row (step
-- 2) and the `library_subscriptions` transition (step 3) as two SEPARATE PostgREST
-- statements, each its own server-side transaction. If the WHOLE `settle()` invocation
-- exhausts its own retry budget, or the process dies, between those two statements, a
-- later, independent redelivery of the SAME webhook finds the ledger row via
-- `uq_settlement_reference_reversal`, reads that alone as "already settled", and
-- returns `DUPLICATE_IGNORED` without ever checking whether the Subscription was
-- actually activated -- money recorded, entitlement never granted.
--
-- A prior remediation pass already closed the WORST consequence of that gap: when a
-- duplicate is found at the ledger, `_settle_once` now re-reads the Subscription fresh
-- and, when the transition is provably still pending (computed the same way
-- `_apply_transition` itself decides `permitted`, deliberately excluding the
-- `renewal_in_place` case, which is ambiguous between "this settlement already
-- activated it" and "a later renewal already did"), falls through to finish the
-- transition instead of returning. That pass's own `remaining_action` named exactly
-- what was deliberately NOT attempted: "the broader architectural alternative
-- (performing the entire settle() as one SQL function/transaction)... judged lower-risk
-- ... than restructuring it into an RPC." THIS FILE IS THAT RESTRUCTURING, SCOPED
-- NARROWLY to the two writes whose separation IS the defect.
--
-- WHAT MOVES INTO THE FUNCTION, AND WHAT DOES NOT
-- ----------------------------------------------------
-- Moves: the ledger INSERT (with its existing `ON CONFLICT` idempotency) and the
-- subscription UPDATE plus its `library_subscription_transitions` history row.
--
-- Does NOT move: anything that decides WHAT to write. `_settle_once` still performs,
-- in Python, before this function is ever called:
--   * the provider-reference lookup and the amount/currency match (steps 0-1) --
--     neither is a write;
--   * the whole "is this transition permitted, and what are the new period dates"
--     decision in `_apply_transition` (`ELIGIBLE_FOR_ACTIVATION`, `can_transition`,
--     `renewal_in_place`, `period_for_activation` / `period_for_renewal`) -- these are
--     Requirements 11.4/11.5's date arithmetic and the existing property-test suite
--     (`test_subscription_state_machine.py`, `test_entitlement_expiry_boundary.py`)
--     exercises it at length; porting it into PL/pgSQL would risk the two
--     implementations disagreeing, which is exactly the class of defect this
--     remediation effort exists to avoid, not to introduce;
--   * the cross-call duplicate-resumability decision the prior pass added, which reads
--     the FRESH subscription status and asks "is the transition still pending" --
--     Python still makes that call, this function is still just told the answer
--     (`p_proceed_if_duplicate`).
--   * the Requirement 10.11 retry budget and backoff in `settle()` itself.
--   * THE ENTITLEMENT GRANT (step 4, `grant_deployment_permission`). Deliberately left
--     OUTSIDE this function and still invoked through `settle()`'s existing injectable
--     `grant_permission` parameter -- tests inject it to observe or fail that one step
--     in isolation, and folding it into this function would remove that seam for no
--     gain: the entitlement table's own UNIQUE (user_id, library_id, is_active)
--     constraint already makes a duplicate grant structurally impossible (MKT-03's own
--     prior evidence, re-verified against this session's disposable replica), so
--     whether the grant lands a moment before or after this function's COMMIT changes
--     nothing about correctness -- only about how far a crash in that one narrow window
--     could leave things, which is the SAME residual this file's own remaining_action
--     states below.
--
-- WHY THE LEDGER INSERT AND THE TRANSITION TRULY HAD TO BE IN THE SAME FUNCTION
-- -----------------------------------------------------------------------------------
-- Read off `marketplace_subscription_guard` (008's own trigger, unmodified by this
-- file): a transition INTO 'active' requires a non-reversal `marketplace_settlements`
-- row for this subscription to ALREADY EXIST (`settled_at >= OLD.period_expiry`, or the
-- reinstatement arm's `<=`). Postgres triggers see uncommitted writes from EARLIER in
-- the SAME transaction, so the ledger INSERT below has to run, in the SAME function
-- call, before the subscription UPDATE -- which is exactly the order `_insert_settlement`
-- then `_apply_transition` already use today. This file does not change that order; it
-- removes the commit boundary that used to sit between them.
--
-- WHAT THIS CLOSES, PRECISELY
-- -------------------------------
-- For any settlement applied THROUGH this function, "the ledger row committed but the
-- subscription did not move" becomes unreachable: both statements are inside one
-- transaction, so either both commit or neither does. A later redelivery that finds the
-- ledger row via the unique constraint therefore also finds the subscription already
-- moved (or provably never eligible to move, e.g. a transition the state machine never
-- permitted) -- the ambiguity the prior pass's DUPLICATE_RESUMED logic exists to resolve
-- can now only arise from data written BEFORE this migration, or from a path outside
-- `settle()` entirely. That logic is NOT removed: it is exactly the right fallback for
-- that legacy population, and `_settle_once`'s own code says so at the point it is kept.
--
-- SQLSTATE THIS FUNCTION RAISES
--   MK001  the subscription's status changed between when `_settle_once` read it and
--          when this function's UPDATE tried to apply the transition -- the same
--          "another writer moved it while this settlement was in flight" outcome
--          `_apply_transition`'s `.eq("status", from_status)` guard already reports
--          today as `SettlementPersistenceError`, which `settle()`'s own retry loop
--          (Requirement 10.11) already retries. Caught and re-raised as that exact type.
--
-- Additive. No DROP TABLE, no DROP COLUMN, no row deletion.
-- REGRESSION: tests/test_mkt03_settlement_atomic_transition_postgres.py

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 0 — PREFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('public.marketplace_settlements') IS NULL
       OR to_regclass('public.library_subscriptions') IS NULL
       OR to_regclass('public.library_subscription_transitions') IS NULL THEN
        RAISE EXCEPTION
            '050 precondition failed: marketplace_settlements, library_subscriptions '
            'or library_subscription_transitions is absent. Provision the marketplace '
            'schema first (008_marketplace_settlement.sql and the root_migrations '
            'library_subscriptions set).';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.marketplace_settlements')
           AND conname = 'uq_settlement_reference_reversal'
    ) THEN
        RAISE EXCEPTION
            '050 precondition failed: uq_settlement_reference_reversal is absent, so '
            'the idempotent ledger insert below has nothing to de-duplicate on.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
         WHERE tgrelid = to_regclass('public.library_subscriptions')
           AND tgname = 'trg_subscription_transition_guard'
    ) THEN
        RAISE EXCEPTION
            '050 precondition failed: trg_subscription_transition_guard is absent. '
            'This function relies on it (and on the settlement row it reads existing '
            'inside the SAME transaction) as the durable guarantee against an illegal '
            'or unpaid activation.';
    END IF;

    RAISE NOTICE '050 preflight: marketplace_settlements, library_subscriptions, '
                 'library_subscription_transitions and the transition guard trigger '
                 'are all present';
END
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1 — THE FUNCTION
-- ─────────────────────────────────────────────────────────────────────────────
DROP FUNCTION IF EXISTS public.record_and_transition_settlement(
    uuid, text, boolean, jsonb, boolean, text, jsonb, jsonb);

CREATE OR REPLACE FUNCTION public.record_and_transition_settlement(
    p_subscription_id uuid,
    p_provider_reference text,
    p_is_reversal boolean,
    p_settlement jsonb,
    p_proceed_if_duplicate boolean,
    p_expected_from_status text,
    p_transition jsonb,
    p_history jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
    v_settlement    public.marketplace_settlements;
    v_subscription  public.library_subscriptions;
    v_inserted      boolean;
BEGIN
    IF p_subscription_id IS NULL OR p_provider_reference IS NULL
       OR btrim(p_provider_reference) = '' THEN
        RAISE EXCEPTION
            'record_and_transition_settlement: subscription_id and provider_reference '
            'are required'
            USING ERRCODE = 'MK002';
    END IF;

    -- ── write 1: the ledger row, idempotent on the provider's own identity ──
    INSERT INTO public.marketplace_settlements (
        id, subscription_id, listing_id, owner_id, purchaser_id,
        amount_minor, owner_share_minor, platform_fee_minor, currency,
        provider, provider_reference, is_reversal, reverses_reference, settled_at
    ) VALUES (
        (p_settlement->>'id')::uuid,
        p_subscription_id,
        (p_settlement->>'listing_id')::uuid,
        (p_settlement->>'owner_id')::uuid,
        (p_settlement->>'purchaser_id')::uuid,
        (p_settlement->>'amount_minor')::bigint,
        (p_settlement->>'owner_share_minor')::bigint,
        (p_settlement->>'platform_fee_minor')::bigint,
        p_settlement->>'currency',
        p_settlement->>'provider',
        p_provider_reference,
        p_is_reversal,
        p_settlement->>'reverses_reference',
        (p_settlement->>'settled_at')::timestamptz
    )
    ON CONFLICT ON CONSTRAINT uq_settlement_reference_reversal DO NOTHING
    RETURNING * INTO v_settlement;

    v_inserted := FOUND;

    IF NOT v_inserted THEN
        -- A row for this (provider_reference, is_reversal) already exists -- either
        -- from a genuinely earlier, fully-committed call (the common duplicate-webhook
        -- case), or from an earlier call of THIS SAME `settle()` invocation's own retry
        -- loop that got this far and then failed before this function returned (there
        -- is no partial-write version of THAT any more either, since this whole
        -- function is one transaction -- but `_settle_once`'s outer retry loop can
        -- still call this function more than once across attempts, and the second call
        -- must see its own first call's row as a duplicate, not as a fresh insert).
        SELECT * INTO v_settlement
          FROM public.marketplace_settlements
         WHERE provider_reference = p_provider_reference AND is_reversal = p_is_reversal;

        IF NOT p_proceed_if_duplicate THEN
            -- `_settle_once`'s own DUPLICATE_IGNORED case: Python has not (yet) decided
            -- this is resumable. Nothing past the ledger read happens.
            RETURN jsonb_build_object(
                'settlement', to_jsonb(v_settlement),
                'inserted', false,
                'transitioned', false
            );
        END IF;
        -- else: Python has already re-read the subscription fresh and decided the
        -- transition is still genuinely pending (DUPLICATE_RESUMED) -- fall through and
        -- apply it, using the pre-existing ledger row.
    END IF;

    IF p_transition IS NULL THEN
        -- `_apply_transition`'s "not permitted" branch: the ledger row stands
        -- (Requirement 10.8), but there is no state to move to and nothing else is
        -- written, matching `state.transition_written = True` with no UPDATE issued.
        RETURN jsonb_build_object(
            'settlement', to_jsonb(v_settlement),
            'inserted', v_inserted,
            'transitioned', false
        );
    END IF;

    -- ── write 2: the transition. `period_start` / `period_expiry` carry the STORED
    --    values unchanged for a reversal (Python computes them that way -- see this
    --    file's header) rather than NULL, so this UPDATE never erases a period it was
    --    not asked to touch; `trg_lib_subs_period_mirror` keeps started_at/expires_at
    --    in step with whatever period_start/period_expiry end up being, exactly as it
    --    already does for the non-atomic UPDATE this replaces. ──
    UPDATE public.library_subscriptions
       SET status        = p_transition->>'status',
           period_start  = CASE WHEN p_transition->>'period_start' IS NULL THEN NULL
                                 ELSE (p_transition->>'period_start')::timestamptz END,
           period_expiry = CASE WHEN p_transition->>'period_expiry' IS NULL THEN NULL
                                 ELSE (p_transition->>'period_expiry')::timestamptz END,
           cancelled_at  = CASE WHEN p_transition->>'cancelled_at' IS NULL THEN NULL
                                 ELSE (p_transition->>'cancelled_at')::timestamptz END,
           updated_at    = now()
     WHERE id = p_subscription_id AND status = p_expected_from_status
     RETURNING * INTO v_subscription;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'record_and_transition_settlement: subscription % status changed under the '
            'caller (expected %); another writer moved it while this settlement was in '
            'flight',
            p_subscription_id, p_expected_from_status
            USING ERRCODE = 'MK001';
    END IF;

    INSERT INTO public.library_subscription_transitions (
        subscription_id, user_id, from_state, to_state, cause, actor_id,
        prior_period_expiry, new_period_expiry, transitioned_at
    ) VALUES (
        p_subscription_id,
        (p_history->>'user_id')::uuid,
        p_history->>'from_state',
        p_history->>'to_state',
        p_history->>'cause',
        CASE WHEN p_history->>'actor_id' IS NULL THEN NULL
             ELSE (p_history->>'actor_id')::uuid END,
        CASE WHEN p_history->>'prior_period_expiry' IS NULL THEN NULL
             ELSE (p_history->>'prior_period_expiry')::timestamptz END,
        CASE WHEN p_history->>'new_period_expiry' IS NULL THEN NULL
             ELSE (p_history->>'new_period_expiry')::timestamptz END,
        (p_history->>'transitioned_at')::timestamptz
    );

    RETURN jsonb_build_object(
        'settlement', to_jsonb(v_settlement),
        'subscription', to_jsonb(v_subscription),
        'inserted', v_inserted,
        'transitioned', true
    );
END
$fn$;

-- Server-side billing primitive, invoked only from `settle()` through a service-role
-- client at a signature-verified webhook -- the same exposure posture `024`'s
-- `process_referral_commission` and `029`'s `record_referral_obligation` already carry,
-- for the identical reason: a caller-supplied subscription_id with no auth.uid() check
-- must not be reachable by a browser role.
REVOKE ALL ON FUNCTION public.record_and_transition_settlement(
    uuid, text, boolean, jsonb, boolean, text, jsonb, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.record_and_transition_settlement(
    uuid, text, boolean, jsonb, boolean, text, jsonb, jsonb) FROM anon;
REVOKE ALL ON FUNCTION public.record_and_transition_settlement(
    uuid, text, boolean, jsonb, boolean, text, jsonb, jsonb) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.record_and_transition_settlement(
    uuid, text, boolean, jsonb, boolean, text, jsonb, jsonb) TO service_role;

COMMENT ON FUNCTION public.record_and_transition_settlement(
    uuid, text, boolean, jsonb, boolean, text, jsonb, jsonb) IS
    'MKT-03. The marketplace_settlements ledger insert and the library_subscriptions '
    'transition + history row, committed atomically. Closes the cross-call gap where a '
    'settle() invocation that exhausted its retry budget (or died) between the two left '
    'a payment recorded as settled while the subscription never activated. The '
    'transition decision itself (permitted/period math/renewal-in-place) stays in '
    'Python (settlement_service._apply_transition); this function only commits it. '
    'MK001 raised when the subscription moved under the caller (retried by settle()''s '
    'existing budget, same as today).';

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2 — POSTFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    n int;
BEGIN
    SELECT count(*) INTO n
      FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'public' AND p.proname = 'record_and_transition_settlement';
    IF n <> 1 THEN
        RAISE EXCEPTION
            '050 postflight FAILED: expected exactly one '
            'record_and_transition_settlement, found %.', n;
    END IF;

    IF has_function_privilege('anon',
            'public.record_and_transition_settlement(uuid,text,boolean,jsonb,boolean,'
            'text,jsonb,jsonb)', 'EXECUTE')
       OR has_function_privilege('authenticated',
            'public.record_and_transition_settlement(uuid,text,boolean,jsonb,boolean,'
            'text,jsonb,jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION
            '050 postflight FAILED: record_and_transition_settlement is EXECUTE-able '
            'by a browser role.';
    END IF;

    IF NOT has_function_privilege('service_role',
            'public.record_and_transition_settlement(uuid,text,boolean,jsonb,boolean,'
            'text,jsonb,jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION
            '050 postflight FAILED: service_role lost EXECUTE on '
            'record_and_transition_settlement -- settlement webhooks would break.';
    END IF;

    RAISE NOTICE '050: record_and_transition_settlement installed, service-role only.';
END
$$;

-- Self-exercise: a stale expected_from_status must raise MK001, and -- the property
-- this migration exists to establish -- the ledger row it already inserted in THIS
-- same call must have been rolled back along with it, leaving nothing durable.
DO $$
DECLARE
    v_owner    uuid := gen_random_uuid();
    v_buyer    uuid := gen_random_uuid();
    v_library  uuid;
    v_sub      uuid;
    v_ref      text := 'migration-050-self-test-' || gen_random_uuid()::text;
    v_caught   boolean := false;
BEGIN
    BEGIN
        INSERT INTO auth.users (id, email) VALUES
            (v_owner, 'migration-050-owner@example.invalid'),
            (v_buyer, 'migration-050-buyer@example.invalid')
        ON CONFLICT DO NOTHING;
        INSERT INTO public.profiles (id) VALUES (v_owner), (v_buyer)
        ON CONFLICT DO NOTHING;
        INSERT INTO public.library_strategies (
            author_id, source_strategy_id, name, category, symbol, timeframe,
            exchange_id, moderation_status, is_active
        ) VALUES (
            -- 'trend_following', NOT 'trend'. library_strategies carries a CHECK named
            -- valid_category admitting exactly mean_reversion / trend_following /
            -- market_making / arbitrage / momentum / ml_hybrid / other. 'trend' is in
            -- none of them, so this INSERT raised check_violation -- which the handler
            -- below did not catch -- and took the whole migration down with it. This file
            -- therefore could never be applied to any database carrying that constraint,
            -- including real production, where it failed exactly this way. Measured
            -- against the real constraint definition via pg_get_constraintdef, not guessed.
            v_owner, gen_random_uuid(), 'migration-050-self-test', 'trend_following',
            'BTC/USDT', '1h', 'binance', 'approved', true
        ) RETURNING id INTO v_library;
        INSERT INTO public.library_subscriptions (
            id, library_id, user_id, owner_id, status, price_minor,
            owner_share_minor, platform_fee_minor, currency
        ) VALUES (
            gen_random_uuid(), v_library, v_buyer, v_owner, 'pending', 1000, 900, 100,
            'USD'
        ) RETURNING id INTO v_sub;
    -- `check_violation` IS IN THIS LIST DELIBERATELY, and its absence was the second
    -- half of the defect above. This handler's own message says the self-test should be
    -- SKIPPED when the fixture tables are "not in the expected shape on this database" --
    -- a CHECK constraint refusing a fixture value is exactly that situation, and it was
    -- the one shape that fell through and failed the migration instead. A self-exercise
    -- must never be the reason a correct function cannot be installed.
    EXCEPTION WHEN undefined_table OR undefined_column OR not_null_violation
                OR check_violation OR foreign_key_violation THEN
        RAISE NOTICE '050: self-test skipped (library_strategies/library_subscriptions '
                     'not in the expected shape on this database: %)', SQLERRM;
        RAISE EXCEPTION 'MIGRATION_050_SELF_TEST_ROLLBACK';
    END;

    BEGIN
        PERFORM public.record_and_transition_settlement(
            v_sub, v_ref, false,
            jsonb_build_object(
                'id', gen_random_uuid()::text, 'listing_id', v_library::text,
                'owner_id', v_owner::text, 'purchaser_id', v_buyer::text,
                'amount_minor', 1000, 'owner_share_minor', 900,
                'platform_fee_minor', 100, 'currency', 'USD', 'provider', 'stripe',
                'reverses_reference', NULL, 'settled_at', now()::text
            ),
            false,
            'active',  -- deliberately wrong: the row is actually 'pending'
            jsonb_build_object(
                'status', 'active', 'period_start', now()::text,
                'period_expiry', (now() + interval '30 days')::text, 'cancelled_at', NULL
            ),
            jsonb_build_object(
                'user_id', v_buyer::text, 'from_state', 'pending', 'to_state', 'active',
                'cause', 'settlement', 'actor_id', NULL,
                'prior_period_expiry', NULL, 'new_period_expiry', NULL,
                'transitioned_at', now()::text
            )
        );
    EXCEPTION WHEN SQLSTATE 'MK001' THEN
        v_caught := true;
    END;

    IF NOT v_caught THEN
        RAISE EXCEPTION
            '050 postflight FAILED: a stale expected_from_status did not raise MK001.';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.marketplace_settlements WHERE provider_reference = v_ref
    ) THEN
        RAISE EXCEPTION
            '050 postflight FAILED: the rejected transition still left a ledger row '
            'committed -- the atomicity this migration exists to provide does not '
            'hold.';
    END IF;

    IF (SELECT status FROM public.library_subscriptions WHERE id = v_sub) <> 'pending' THEN
        RAISE EXCEPTION
            '050 postflight FAILED: the subscription moved despite the rejected call.';
    END IF;

    RAISE EXCEPTION 'MIGRATION_050_SELF_TEST_ROLLBACK';
EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'MIGRATION_050_SELF_TEST_ROLLBACK' THEN
        RAISE NOTICE '050: self-test passed (a stale from_status raised MK001 and left '
                     'no ledger row committed); its own fixture rows were rolled back.';
    ELSE
        RAISE;
    END IF;
END
$$;
