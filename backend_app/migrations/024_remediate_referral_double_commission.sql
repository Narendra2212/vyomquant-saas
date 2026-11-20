-- backend_app/migrations/024_remediate_referral_double_commission.sql
--
-- CORRECTION, applied against real production (see ledger DEPLOY-04 / BILL-06): this
-- file's own "installed signature" and "installed body" claims below were themselves
-- stale by the time it was actually applied. The real production function was
-- (p_payment_id varchar, p_referred_user_id uuid, p_subscription_tier varchar,
-- p_payment_amount_usd numeric) -- note the SECOND parameter's name, not the first --
-- and its wallet upsert used balance_usd/total_earned_usd, not the
-- pending_balance_usd/lifetime_earnings_usd this file assumes throughout. The actual
-- production fix: kept the real signature AND parameter names (CREATE OR REPLACE
-- FUNCTION cannot rename a parameter without DROP FUNCTION first, which this file's
-- own tooling could not run), added this file's ON CONFLICT (payment_id) guard and
-- the uq_referral_commission_payment index exactly as declared below, used the real
-- wallet column names, and fixed backend_app/routers/billing.py's RPC call (which was
-- passing "p_referred_id", resolving to nothing) to use "p_referred_user_id" instead.
-- See BILL-07's ledger entry for the full account. The SQL below is this file's
-- ORIGINAL, as-designed version; it does not reflect what actually landed.
--
-- REMEDIATION PHASE, STEP 3: money / referral accounting.
--
-- MONEY-01. CONFIRMED, NOT SUSPECTED.
-- The audit could not determine which of the two `process_referral_commission` bodies
-- was installed. It has now been read off the live catalogue:
--
--     PROCESS_REF_COMM_HAS_DUP_GUARD| NO_EXISTS_GUARD      (staging/rem_inspect2.txt)
--
-- The installed function is the `migrations/referral_system_redesign.sql:434-496`
-- version, which has NO `payment_id` duplicate check. It computes
-- `v_commission_amount := p_payment_amount_usd * 0.20`, unconditionally INSERTs into
-- `referral_commissions`, and then upserts `referral_wallets` with
-- `pending_balance_usd = pending_balance_usd + v_commission_amount`.
--
-- And the table has no uniqueness to stop it:
--
--     REFCOMM_CONSTRAINTS| referral_commissions_pkey ~ PRIMARY KEY (id)
--     REFCOMM_INDEXES|     idx_referral_commissions_payment        <- plain, NOT unique
--
-- So a redelivered or replayed payment webhook credits the referrer twice. The Redis
-- two-phase lock in `routers/billing.py` is the only thing in the way, and it is a
-- 60-second window on a store that can be unavailable — the same single-point-of-failure
-- shape that the marketplace path deliberately does NOT rely on, because there
-- `uq_settlement_reference_reversal` makes the guarantee in the database.
--
-- WHY A CONSTRAINT AND NOT A PYTHON CHECK, AND NOT EVEN A `SELECT EXISTS` IN PLPGSQL.
-- The audit brief is explicit that `if already_processed: return` is not acceptable, and
-- it is right for a reason worth stating: the `006` version of this function DOES carry
-- an `IF EXISTS (SELECT 1 ... WHERE payment_id = p_payment_id) THEN RETURN NULL`, and
-- that is STILL not safe. Two concurrent webhook deliveries can both execute the SELECT
-- before either executes the INSERT, both see no row, and both insert. Only a unique
-- index makes the check and the write one atomic decision. That is what this file adds.
--
-- THE INVARIANT CHOSEN, AND WHY THIS SHAPE.
--     at most one non-reversed commission per payment
-- Expressed as a PARTIAL unique index on `payment_id`:
--   * partial on `payment_id IS NOT NULL`, because a NULL payment_id carries no economic
--     identity to deduplicate on and SQL would treat every NULL as distinct anyway —
--     being explicit documents the intent instead of relying on that;
--   * `payment_id` alone rather than `(payment_id, referrer_id)`, because the installed
--     function credits exactly ONE referrer per payment (single-level referral:
--     `referrer_id`, `referred_id`, `referral_relationship_id`). If multi-level referral
--     is introduced later, this index will fail the second insert and force an explicit
--     decision about the new economic model — which is the correct forcing function
--     rather than silently permitting N credits per payment.
--
-- Additive. No DROP TABLE, no DROP COLUMN, no row deletion. Idempotent.

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1 — PREFLIGHT. Refuse clearly if existing duplicates would block the index.
-- ─────────────────────────────────────────────────────────────────────────────
-- A `CREATE UNIQUE INDEX` over data that already violates the invariant fails with an
-- opaque message. If duplicates exist they are a MONEY problem that a migration must not
-- silently resolve by picking a winner, so this refuses and names them.
DO $$
DECLARE
    dup_count int;
    sample text;
BEGIN
    IF to_regclass('public.referral_commissions') IS NULL THEN
        RAISE NOTICE '024: public.referral_commissions absent; nothing to do';
        RETURN;
    END IF;

    SELECT count(*), string_agg(DISTINCT payment_id, ', ')
      INTO dup_count, sample
      FROM (
        SELECT payment_id
          FROM public.referral_commissions
         WHERE payment_id IS NOT NULL
         GROUP BY payment_id
        HAVING count(*) > 1
      ) d;

    IF coalesce(dup_count, 0) > 0 THEN
        RAISE EXCEPTION
            '024 PREFLIGHT: % payment_id value(s) already carry more than one '
            'referral commission row: %. These are duplicate economic credits that '
            'already happened. A migration will not choose which to keep — reconcile '
            'them (reverse the extras, or confirm they are legitimate multi-level '
            'credits and change this invariant deliberately), then re-run.',
            dup_count, sample;
    END IF;

    RAISE NOTICE '024 preflight: no existing duplicate commissions; safe to add the index';
END
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2 — THE INVARIANT.
-- ─────────────────────────────────────────────────────────────────────────────
-- Not CONCURRENTLY: this must be usable inside a transaction block by the hand-applied
-- migration runner, and the table is small. The brief lock costs nothing here.
CREATE UNIQUE INDEX IF NOT EXISTS uq_referral_commission_payment
    ON public.referral_commissions (payment_id)
    WHERE payment_id IS NOT NULL;

COMMENT ON INDEX public.uq_referral_commission_payment IS
    'MONEY-01. At most one referral commission per payment. The installed '
    'process_referral_commission had no duplicate guard, so a replayed payment webhook '
    'double-credited the referrer. A SELECT EXISTS check is not sufficient because two '
    'concurrent deliveries can both pass it before either inserts; only a unique index '
    'makes the check and the write one atomic decision.';

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 3 — MAKE A REPLAY A NO-OP RATHER THAN AN ERROR.
-- ─────────────────────────────────────────────────────────────────────────────
-- With the index in place, a replayed webhook would now raise 23505 inside the function.
-- `routers/billing.py` wraps the RPC in a bare `try/except` that logs and continues, so
-- the webhook would still answer 200 — but the log would fill with unique-violation
-- noise on every retry, and an operator cannot distinguish "harmless replay" from "real
-- failure". So the function is replaced with a version whose INSERT carries
-- `ON CONFLICT DO NOTHING` and which only moves the wallet when it actually inserted.
--
-- THE WALLET UPDATE IS NOW CONDITIONAL ON THE INSERT HAVING HAPPENED. That is the part
-- that matters: in the installed version the wallet upsert ran unconditionally, so even
-- if the commission row had been deduplicated the BALANCE would still have moved twice.
-- Deduplicating the ledger row without deduplicating the balance would have fixed the
-- symptom and left the defect.
--
-- Everything else about the function's contract is preserved: same name, same signature,
-- same 20% rate, same `pending` status, same relationship lookup, same return type. This
-- is deliberately the smallest change that makes the economics atomic.
-- ─── THE SIGNATURE MATTERS, AND THIS FILE'S FIRST VERSION GOT IT WRONG ───
-- The installed signature was read off the catalogue:
--
--   (p_referred_id uuid, p_payment_id character varying,
--    p_subscription_tier character varying, p_payment_amount_usd numeric)
--
-- and it matches what the application actually calls — `routers/billing.py:2114-2119`
-- and `:2437-2442` pass the named arguments `p_referred_id`, `p_payment_id`,
-- `p_subscription_tier`, `p_payment_amount_usd`.
--
-- This file's first version declared `(p_referred_user_id uuid, p_payment_id text,
-- p_payment_amount_usd numeric, p_subscription_tier text)`. Because PostgreSQL
-- identifies a function by its argument TYPES, that did not replace anything — it
-- created a SECOND OVERLOAD and left the vulnerable original in place, still PUBLIC-
-- executable and still the one PostgREST would resolve for the application's call. The
-- postflight then passed, because it checked the overload this file had just created.
--
-- That is a near-miss worth recording: a migration that "applied OK" while changing
-- nothing the application uses. Section 3 now (a) drops the accidental overload if it
-- exists, and (b) replaces the function at its real signature. Section 4 asserts there
-- is EXACTLY ONE `process_referral_commission` afterwards, which is what makes the
-- mistake impossible to repeat silently.
-- Drop the accidental overload from this file's first version, if it landed.
DROP FUNCTION IF EXISTS public.process_referral_commission(UUID, TEXT, NUMERIC, TEXT);

-- And drop the real one at its exact installed signature. A plain `CREATE OR REPLACE`
-- is not enough: the installed function's RETURN type differs from the one declared
-- below, and PostgreSQL answers that with `42P13 cannot change return type of existing
-- function`. (That error is how this was discovered — the previous attempt failed here
-- rather than silently.) No CASCADE: nothing may be dropped as a side effect of this.
DROP FUNCTION IF EXISTS
    public.process_referral_commission(UUID, CHARACTER VARYING, CHARACTER VARYING, NUMERIC);

CREATE OR REPLACE FUNCTION public.process_referral_commission(
    p_referred_id UUID,
    p_payment_id CHARACTER VARYING,
    p_subscription_tier CHARACTER VARYING,
    p_payment_amount_usd NUMERIC
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
    v_relationship   RECORD;
    v_commission_id  UUID;
    v_rate           NUMERIC := 0.20;
    v_amount         NUMERIC;
BEGIN
    -- A payment with no identity cannot be deduplicated, so it is refused rather than
    -- credited. Previously a NULL payment_id would have inserted an unbounded number of
    -- commission rows across retries, and the partial unique index cannot stop that
    -- because SQL treats every NULL as distinct.
    IF p_payment_id IS NULL OR btrim(p_payment_id) = '' THEN
        RAISE WARNING 'process_referral_commission: refused a commission with no '
                      'payment_id (referred_id=%)', p_referred_id;
        RETURN NULL;
    END IF;

    IF p_payment_amount_usd IS NULL OR p_payment_amount_usd <= 0 THEN
        RAISE WARNING 'process_referral_commission: refused a commission for a '
                      'non-positive payment amount % (payment_id=%)',
                      p_payment_amount_usd, p_payment_id;
        RETURN NULL;
    END IF;

    SELECT * INTO v_relationship
      FROM public.referral_relationships
     WHERE referred_id = p_referred_id
     LIMIT 1;

    IF NOT FOUND THEN
        RETURN NULL;              -- not referred; nothing to pay. Not an error.
    END IF;

    -- Self-referral is already refused by chk_no_self_referral at insert time on
    -- referral_relationships, so a self-referring relationship cannot exist to be found
    -- here. Re-checked anyway: one comparison, and it closes the case where that
    -- constraint is ever dropped.
    IF v_relationship.referrer_id = p_referred_id THEN
        RAISE WARNING 'process_referral_commission: refused self-referral commission '
                      '(user=%)', p_referred_id;
        RETURN NULL;
    END IF;

    v_amount := round(p_payment_amount_usd * v_rate, 2);

    -- THE ATOMIC DECISION. If this payment already produced a commission, the partial
    -- unique index refuses the row, RETURNING yields nothing, v_commission_id stays
    -- NULL, and the wallet is NOT touched.
    INSERT INTO public.referral_commissions (
        referrer_id, referred_id, referral_relationship_id,
        payment_id, subscription_tier,
        payment_amount_usd, commission_rate, commission_amount_usd,
        status
    ) VALUES (
        v_relationship.referrer_id, p_referred_id, v_relationship.id,
        p_payment_id, p_subscription_tier,
        p_payment_amount_usd, v_rate, v_amount,
        'pending'
    )
    ON CONFLICT (payment_id) WHERE payment_id IS NOT NULL DO NOTHING
    RETURNING id INTO v_commission_id;

    IF v_commission_id IS NULL THEN
        -- Replay. Idempotent no-op, at a level an operator can filter, rather than a
        -- 23505 that would fill the log on every provider retry.
        RAISE NOTICE 'process_referral_commission: payment_id % already carries a '
                     'commission; no second credit issued', p_payment_id;
        RETURN NULL;
    END IF;

    -- Reached ONLY when a commission row was genuinely created. In the version this
    -- replaces, the wallet upsert ran unconditionally — so deduplicating the ledger row
    -- without moving this inside the guard would have fixed the symptom and left the
    -- defect: the BALANCE would still have moved twice.
    INSERT INTO public.referral_wallets (user_id, pending_balance_usd, lifetime_earnings_usd)
    VALUES (v_relationship.referrer_id, v_amount, v_amount)
    ON CONFLICT (user_id) DO UPDATE
       SET pending_balance_usd   = public.referral_wallets.pending_balance_usd + v_amount,
           lifetime_earnings_usd = public.referral_wallets.lifetime_earnings_usd + v_amount,
           updated_at            = now();

    RETURN v_commission_id;
END
$fn$;

-- Same exposure reasoning as 023 section 3: a server-side billing primitive invoked from
-- a signature-verified webhook through a service-role client. It takes a caller-supplied
-- `p_referred_id` and has no `auth.uid()` check, so it must not be reachable by a
-- browser role.
REVOKE ALL ON FUNCTION
    public.process_referral_commission(UUID, CHARACTER VARYING, CHARACTER VARYING, NUMERIC)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION
    public.process_referral_commission(UUID, CHARACTER VARYING, CHARACTER VARYING, NUMERIC)
    FROM anon;
REVOKE ALL ON FUNCTION
    public.process_referral_commission(UUID, CHARACTER VARYING, CHARACTER VARYING, NUMERIC)
    FROM authenticated;
GRANT EXECUTE ON FUNCTION
    public.process_referral_commission(UUID, CHARACTER VARYING, CHARACTER VARYING, NUMERIC)
    TO service_role;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 4 — POSTFLIGHT.
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    leftover text;
    n int;
BEGIN
    IF to_regclass('public.referral_commissions') IS NULL THEN
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='public' AND tablename='referral_commissions'
           AND indexname='uq_referral_commission_payment'
    ) THEN
        RAISE EXCEPTION '024 postflight FAILED: uq_referral_commission_payment was not '
                        'created, so a replayed payment webhook can still double-credit.';
    END IF;

    -- EXACTLY ONE overload must exist. This is the assertion that makes the
    -- wrong-signature mistake described in section 3 impossible to repeat silently: if a
    -- future edit declares a different argument list, this fails instead of quietly
    -- leaving the real function untouched.
    SELECT count(*) INTO n
      FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'public' AND p.proname = 'process_referral_commission';
    IF n <> 1 THEN
        SELECT string_agg(pg_get_function_identity_arguments(p.oid), ' | ')
          INTO leftover
          FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
         WHERE ns.nspname = 'public' AND p.proname = 'process_referral_commission';
        RAISE EXCEPTION '024 postflight FAILED: expected exactly one '
                        'process_referral_commission, found % — overloads: %. '
                        'PostgREST would resolve the application''s call to one of them '
                        'non-obviously, and the unguarded body may still be live.',
                        n, leftover;
    END IF;

    -- The surviving function must be the one the application actually calls.
    SELECT pg_get_function_identity_arguments(p.oid) INTO leftover
      FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'public' AND p.proname = 'process_referral_commission';
    IF leftover NOT LIKE '%p_referred_id%'
       OR leftover NOT LIKE '%p_payment_id%'
       OR leftover NOT LIKE '%p_subscription_tier%'
       OR leftover NOT LIKE '%p_payment_amount_usd%' THEN
        RAISE EXCEPTION '024 postflight FAILED: the surviving process_referral_commission '
                        'signature is (%), which does not match the argument names '
                        'routers/billing.py passes (p_referred_id, p_payment_id, '
                        'p_subscription_tier, p_payment_amount_usd).', leftover;
    END IF;

    -- The replacement body must actually carry the atomic guard.
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
         WHERE ns.nspname='public' AND p.proname='process_referral_commission'
           AND pg_get_functiondef(p.oid) ILIKE '%ON CONFLICT (payment_id)%'
    ) THEN
        RAISE EXCEPTION '024 postflight FAILED: the installed '
                        'process_referral_commission body has no ON CONFLICT '
                        '(payment_id) guard.';
    END IF;

    IF has_function_privilege('authenticated',
            'public.process_referral_commission(uuid,character varying,character varying,numeric)',
            'EXECUTE')
       OR has_function_privilege('anon',
            'public.process_referral_commission(uuid,character varying,character varying,numeric)',
            'EXECUTE') THEN
        RAISE EXCEPTION '024 postflight FAILED: process_referral_commission is still '
                        'EXECUTE-able by a browser role.';
    END IF;

    IF NOT has_function_privilege('service_role',
            'public.process_referral_commission(uuid,character varying,character varying,numeric)',
            'EXECUTE') THEN
        RAISE EXCEPTION '024 postflight FAILED: service_role LOST EXECUTE on '
                        'process_referral_commission — the billing webhook would break.';
    END IF;

    RAISE NOTICE '024: postflight passed — one commission per payment is now a database '
                 'invariant, exactly one function overload exists, it matches the '
                 'application''s call, and the wallet moves only when a commission row '
                 'was created.';
END
$$;
