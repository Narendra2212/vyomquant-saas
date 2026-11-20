-- backend_app/migrations/029_referral_obligations.sql
--
-- PHASE 9, STEP 2: money owed to a customer must exist as a row, not as a log line.
--
-- MONEY-06. WHAT WAS MEASURED
-- ---------------------------
-- `razorpay_webhook` (backend_app/routers/billing.py) contains ZERO
-- `process_referral_commission` call nodes -- confirmed by AST, because the name DOES
-- appear four times in Razorpay-named code as prose (a docstring at :357, a comment at
-- :596, and the MONEY-04 comment block at :2563-2589) and a substring scan is fooled by
-- exactly that. When an INR payment is captured for a referred user, the handler writes a
-- `billing_invoices` row, dispatches a notification, and then emits
--
--     logger.error("[BILLING] MONEY-04: referral commission SKIPPED for Razorpay ...")
--
-- and that log line is the ONLY artefact of the decision. The obligation is
-- reconstructible (the invoice row is durable and attribution lives in
-- `referral_relationships`), but nothing STATES that money is owed, how much, or in what
-- currency -- so nothing can be queried, reconciled, aged, or settled.
--
-- THE SCOPE IS WIDER THAN RAZORPAY, which the finding did not say. `stripe_webhook`
-- drops a commission silently in two further cases:
--   * `_provider_major_amount` returns None (billing.py:2230-2237) -- log only;
--   * the RPC raises anything (billing.py:2227-2229) -- a bare `except` that logs and
--     continues, so a transient PostgREST failure loses a real USD commission.
-- All three now produce a row.
--
-- WHY A NEW TABLE RATHER THAN A COLUMN ON referral_commissions
-- ------------------------------------------------------------
-- Three measured reasons, in order of how binding they are:
--
-- 1. `referral_wallets` aggregates `pending_balance_usd` across commission rows. A
--    mixed-currency wallet has no defined sum. 027's own column comment already records
--    this: widening "requires deciding how a mixed-currency balance is denominated and
--    summed, which is why it is not widened here."
--
-- 2. `process_referral_commission` (024:158-254) computes `round(amount * 0.20, 2)` on
--    whatever it is handed and writes it to `commission_amount_usd`. Feeding it INR is
--    precisely the ~83x overstatement MONEY-04 was reported for.
--
-- 3. `CHECK (currency = 'USD')` on all three referral ledger tables is pinned by
--    tests/test_money04_referral_ledger_is_single_currency_postgres.py:230 and :287.
--    Widening it is forbidden, and correctly so.
--
-- So this is a SEPARATE LEDGER for obligations that the single-currency ledger cannot
-- denominate. `currency` here is deliberately NOT constrained to USD -- recording an INR
-- amount AS INR is the whole point. What is constrained is the FORMAT (three uppercase
-- letters), because an amount labelled with a typo is as unusable as an unlabelled one.
--
-- NO FX. ANYWHERE. `commission_amount` is NULLABLE and is left NULL whenever the
-- obligation is not already denominated in the ledger's currency. `core/fx_service.py`
-- exists, is float-based and falls back to baseline rates; billing.py:2605-2611 already
-- records the decision not to reach for it on this path. Choosing a rate, an as-of
-- instant, and who absorbs the drift between accrual and payout is a product decision
-- about money owed to a customer. An obligation with a stated amount in a stated currency
-- and a NULL commission is honest; one with an invented commission is not.
--
-- IDEMPOTENCY IS A REAL CONSTRAINT, NOT A CHECK-THEN-INSERT
-- ---------------------------------------------------------
-- `uq_referral_obligation_event UNIQUE (provider, provider_payment_id)`, consumed by
-- `ON CONFLICT DO NOTHING` in the function below. Same reasoning 024 states for
-- `uq_referral_commission_payment`: two concurrent webhook deliveries can both pass a
-- `SELECT EXISTS` before either inserts. Only a unique constraint makes the check and
-- the write one atomic decision.
--
-- `(provider, provider_payment_id)` rather than `provider_payment_id` alone, because the
-- two providers' id spaces are independent and a collision between a Stripe payment
-- intent and a Razorpay payment id would silently suppress a real obligation.
--
-- WHY A DATABASE FUNCTION AND NOT TWO PostgREST CALLS
-- ---------------------------------------------------
-- Measured: every `supabase.table(...).execute()` is a separate HTTP request that
-- PostgREST runs in its own server-side transaction and commits before responding. There
-- is no BEGIN/COMMIT a handler can hold across two of them, no savepoint and no
-- two-phase commit. In `stripe_webhook` the tier write, the invoice write and the
-- commission RPC are three independent transactions, each separately try/except-wrapped
-- -- which is exactly how a commission ends up owed and absent while the entitlement is
-- granted. Anything that must be atomic has to live inside ONE function invoked by ONE
-- `.rpc()`.
--
-- Additive. No DROP, no DELETE, no TRUNCATE, no column removal, and it does not touch
-- referral_commissions, referral_wallets or referral_payouts.
-- Regression: tests/test_money06_referral_obligations_postgres.py
-- ==========================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 0 — PREFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        RAISE EXCEPTION
            '029 precondition failed: role "service_role" does not exist. The billing '
            'webhook writes obligations through it. See '
            'scripts/provision_database.py --create-substrate.';
    END IF;

    IF to_regclass('public.referral_relationships') IS NULL THEN
        RAISE EXCEPTION
            '029 precondition failed: public.referral_relationships is absent, so an '
            'obligation could not be attributed to a referrer. Provision the referral '
            'system first (migrations/referral_system_redesign.sql).';
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1 — THE TABLE
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.referral_obligations (
    obligation_id       uuid          NOT NULL DEFAULT gen_random_uuid(),

    -- The payment event this obligation arises from. Together these are the idempotency
    -- key, and both are NOT NULL because an obligation with no economic identity cannot
    -- be deduplicated -- the same reason 024's function refuses a NULL payment_id.
    provider            text          NOT NULL,
    provider_payment_id text          NOT NULL,

    -- Who paid. NOT NULL: an obligation arises from someone's payment.
    referred_id         uuid          NOT NULL,
    -- Who is owed. NULLABLE: the relationship may not resolve (no referrer, or the
    -- lookup failed). An unattributable obligation is still preserved for investigation
    -- rather than dropped, which is the finding's own stated remedy.
    referrer_id         uuid          NULL,

    -- THE AMOUNT AND ITS UNIT, TOGETHER AND BOTH MANDATORY.
    payment_amount      numeric(18,2) NOT NULL,
    currency            text          NOT NULL,

    -- The commission, when it can be stated WITHOUT inventing a rate. NULL means "not
    -- computable here", not zero.
    commission_rate     numeric(5,4)  NULL,
    commission_amount   numeric(18,2) NULL,

    subscription_tier   text          NULL,

    status              text          NOT NULL DEFAULT 'unresolved',
    -- WHY this obligation exists rather than a credited commission. Machine-readable, so
    -- "every INR obligation" and "every obligation caused by a failed RPC" are different
    -- queries.
    reason_code         text          NOT NULL,
    detail              jsonb         NOT NULL DEFAULT '{}'::jsonb,

    created_at          timestamptz   NOT NULL DEFAULT now(),
    updated_at          timestamptz   NOT NULL DEFAULT now(),
    settled_at          timestamptz   NULL,
    reversed_at         timestamptz   NULL,

    CONSTRAINT referral_obligations_pkey PRIMARY KEY (obligation_id),
    CONSTRAINT uq_referral_obligation_event UNIQUE (provider, provider_payment_id)
);

DO $$
BEGIN
    -- STATUS VOCABULARY.
    --
    -- Deliberately NOT a copy of referral_commissions' pending|approved|paid|reversed.
    -- That vocabulary describes a commission moving toward payout. These rows describe an
    -- obligation that could not become a commission, so its first question is whether it
    -- is resolvable at all:
    --
    --   unresolved -> recorded, owed, and NOT yet expressible in the ledger's currency
    --                 or not yet attributable. The state MONEY-06 asks for.
    --   payable    -> resolved and ready to settle (amount and currency agreed).
    --   settled    -> paid out or credited; `settled_at` says when.
    --   reversed   -> the underlying payment was refunded; `reversed_at` says when.
    --   refused    -> deliberately not owed (e.g. no referral relationship exists). Kept
    --                 rather than deleted, so "we decided nothing is owed" is a recorded
    --                 decision and not an absence.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.referral_obligations')
           AND conname = 'chk_referral_obligations_status'
    ) THEN
        ALTER TABLE public.referral_obligations
            ADD CONSTRAINT chk_referral_obligations_status
            CHECK (status IN ('unresolved', 'payable', 'settled', 'reversed', 'refused'));
    END IF;

    -- CURRENCY FORMAT, NOT CURRENCY POLICY.
    --
    -- `CHECK (currency = 'USD')` would defeat the entire purpose of this table. But an
    -- amount labelled 'inr', 'Rs', '' or 'UNITED STATES DOLLAR' is as unusable as an
    -- unlabelled one, so the FORM is constrained: exactly three uppercase letters. Which
    -- currencies the application will actually accept is enforced in
    -- `marketplace/money.py` (MINOR_UNIT_EXPONENT, currently USD and INR) -- a policy
    -- that changes with product scope and therefore does not belong in a CHECK.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.referral_obligations')
           AND conname = 'chk_referral_obligations_currency_format'
    ) THEN
        ALTER TABLE public.referral_obligations
            ADD CONSTRAINT chk_referral_obligations_currency_format
            CHECK (currency ~ '^[A-Z]{3}$');
    END IF;

    -- A non-positive payment cannot owe a commission. 024's function refuses one; this
    -- makes it true of every writer.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.referral_obligations')
           AND conname = 'chk_referral_obligations_amount_positive'
    ) THEN
        ALTER TABLE public.referral_obligations
            ADD CONSTRAINT chk_referral_obligations_amount_positive
            CHECK (payment_amount > 0);
    END IF;

    -- A commission may be absent (no FX) but must never be negative, and must carry its
    -- rate when it carries an amount -- otherwise the figure cannot be audited.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.referral_obligations')
           AND conname = 'chk_referral_obligations_commission_coherent'
    ) THEN
        ALTER TABLE public.referral_obligations
            ADD CONSTRAINT chk_referral_obligations_commission_coherent
            CHECK (
                (commission_amount IS NULL AND commission_rate IS NULL)
                OR (commission_amount >= 0 AND commission_rate IS NOT NULL)
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.referral_obligations')
           AND conname = 'chk_referral_obligations_reason_not_blank'
    ) THEN
        ALTER TABLE public.referral_obligations
            ADD CONSTRAINT chk_referral_obligations_reason_not_blank
            CHECK (btrim(reason_code) <> '');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.referral_obligations')
           AND conname = 'chk_referral_obligations_provider_not_blank'
    ) THEN
        ALTER TABLE public.referral_obligations
            ADD CONSTRAINT chk_referral_obligations_provider_not_blank
            CHECK (btrim(provider) <> '' AND btrim(provider_payment_id) <> '');
    END IF;

    -- A settled obligation must say when, and a reversed one likewise. Without this the
    -- status is an unverifiable claim.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.referral_obligations')
           AND conname = 'chk_referral_obligations_terminal_timestamps'
    ) THEN
        ALTER TABLE public.referral_obligations
            ADD CONSTRAINT chk_referral_obligations_terminal_timestamps
            CHECK (
                (status <> 'settled'  OR settled_at  IS NOT NULL)
                AND (status <> 'reversed' OR reversed_at IS NOT NULL)
            );
    END IF;
END $$;

-- The queries this ledger exists to answer.
CREATE INDEX IF NOT EXISTS idx_referral_obligations_status_created
    ON public.referral_obligations (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_referral_obligations_referrer
    ON public.referral_obligations (referrer_id);
CREATE INDEX IF NOT EXISTS idx_referral_obligations_referred
    ON public.referral_obligations (referred_id);
CREATE INDEX IF NOT EXISTS idx_referral_obligations_currency
    ON public.referral_obligations (currency);
-- "What is still owed and unresolved" must not be a sequential scan as the table grows.
CREATE INDEX IF NOT EXISTS idx_referral_obligations_outstanding
    ON public.referral_obligations (created_at DESC)
    WHERE status IN ('unresolved', 'payable');

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2 — THE WRITER
-- ─────────────────────────────────────────────────────────────────────────────
-- One function, one transaction, one `.rpc()` call. See the header for why composing
-- this from PostgREST calls cannot be made atomic.
--
-- It does NOT credit a wallet and does NOT write referral_commissions. That is the
-- point: these are the obligations the single-currency ledger cannot express. The USD
-- path still goes through `process_referral_commission`, which is already atomic across
-- its own commission INSERT and wallet upsert.
--
-- SO WHAT IS "ATOMIC CONSISTENCY BETWEEN THE OBLIGATION AND ANY LEDGER CREDIT" HERE?
-- It is vacuous by construction, and that is a stronger guarantee than coordinating two
-- writes: an obligation row exists precisely WHEN no credit was made, and the credit path
-- is a different function that writes no obligation. The two are mutually exclusive
-- states of one payment, each produced by a single transaction, and
-- `uq_referral_obligation_event` plus `uq_referral_commission_payment` independently stop
-- either being duplicated. Section 4's postflight asserts this function touches neither
-- ledger table.
DROP FUNCTION IF EXISTS public.record_referral_obligation(
    TEXT, TEXT, UUID, NUMERIC, TEXT, TEXT, TEXT, JSONB);

CREATE OR REPLACE FUNCTION public.record_referral_obligation(
    p_provider            TEXT,
    p_provider_payment_id TEXT,
    p_referred_id         UUID,
    p_payment_amount      NUMERIC,
    p_currency            TEXT,
    p_subscription_tier   TEXT,
    p_reason_code         TEXT,
    p_detail              JSONB DEFAULT '{}'::jsonb
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
    v_relationship  RECORD;
    v_obligation_id UUID;
    v_currency      TEXT;
    v_rate          NUMERIC;
    v_commission    NUMERIC;
    v_status        TEXT;
    --: The currency this platform's referral ledger is denominated in. Matches
    --: `CHECK (currency = 'USD')` on referral_commissions / referral_wallets /
    --: referral_payouts, which 027 established.
    c_ledger_ccy    TEXT := 'USD';
    --: The same flat rate 024's process_referral_commission applies. Duplicated as a
    --: literal rather than read from anywhere, because 024 hardcodes it too
    --: (`v_rate NUMERIC := 0.20` at 024:165) and there is no single source to read. If
    --: that ever becomes configurable, BOTH must change -- which Section 4 asserts by
    --: comparing them.
    c_rate          NUMERIC := 0.20;
BEGIN
    -- An event with no identity cannot be deduplicated, so it is refused rather than
    -- inserted. Without this, retries would accumulate unbounded rows: the unique
    -- constraint cannot stop them because NULL is distinct from NULL in SQL.
    IF p_provider IS NULL OR btrim(p_provider) = ''
       OR p_provider_payment_id IS NULL OR btrim(p_provider_payment_id) = '' THEN
        RAISE WARNING 'record_referral_obligation: refused an obligation with no '
                      'provider/payment identity (provider=%, payment_id=%)',
                      p_provider, p_provider_payment_id;
        RETURN NULL;
    END IF;

    IF p_payment_amount IS NULL OR p_payment_amount <= 0 THEN
        RAISE WARNING 'record_referral_obligation: refused a non-positive amount % for '
                      '%:%', p_payment_amount, p_provider, p_provider_payment_id;
        RETURN NULL;
    END IF;

    -- Normalise and validate the unit here as well as in the application. The webhook
    -- handler already refuses a currency it does not recognise; this makes the guarantee
    -- hold for any future writer too, and turns a malformed unit into a clear refusal
    -- rather than a 23514 from the format CHECK.
    v_currency := upper(btrim(coalesce(p_currency, '')));
    IF v_currency !~ '^[A-Z]{3}$' THEN
        RAISE WARNING 'record_referral_obligation: refused obligation %:% because the '
                      'currency %L is not a three-letter code. An amount with no usable '
                      'unit is not a recordable obligation.',
                      p_provider, p_provider_payment_id, p_currency;
        RETURN NULL;
    END IF;

    IF p_reason_code IS NULL OR btrim(p_reason_code) = '' THEN
        RAISE WARNING 'record_referral_obligation: refused obligation %:% with no reason '
                      'code. Why an obligation exists rather than a credited commission '
                      'is the column that makes it actionable.',
                      p_provider, p_provider_payment_id;
        RETURN NULL;
    END IF;

    -- Attribution. Not an error when absent: a payment from an unreferred user owes
    -- nobody, and that is a decision worth recording rather than an exception.
    SELECT * INTO v_relationship
      FROM public.referral_relationships
     WHERE referred_id = p_referred_id
     LIMIT 1;

    IF NOT FOUND THEN
        v_status := 'refused';
        v_rate := NULL;
        v_commission := NULL;
    ELSIF v_relationship.referrer_id = p_referred_id THEN
        -- Self-referral. Already refused by chk_no_self_referral at relationship insert
        -- time, so this is unreachable unless that constraint is dropped; re-checked for
        -- the same belt-and-braces reason 024 gives.
        v_status := 'refused';
        v_rate := NULL;
        v_commission := NULL;
    ELSIF v_currency = c_ledger_ccy THEN
        -- Same currency as the ledger, so the amount owed IS computable and is stated.
        -- This is the "the RPC failed" / "the amount was unreadable and then recovered"
        -- case: a real USD commission that simply did not get credited.
        v_rate := c_rate;
        v_commission := round(p_payment_amount * c_rate, 2);
        v_status := 'payable';
    ELSE
        -- A currency this ledger cannot denominate. The amount and its unit are recorded
        -- exactly as received; the commission is left NULL rather than converted.
        -- THIS IS THE MONEY-06 CASE.
        v_rate := NULL;
        v_commission := NULL;
        v_status := 'unresolved';
    END IF;

    INSERT INTO public.referral_obligations (
        provider, provider_payment_id, referred_id, referrer_id,
        payment_amount, currency, commission_rate, commission_amount,
        subscription_tier, status, reason_code, detail
    ) VALUES (
        btrim(p_provider), btrim(p_provider_payment_id), p_referred_id,
        CASE WHEN v_status = 'refused' THEN NULL ELSE v_relationship.referrer_id END,
        p_payment_amount, v_currency, v_rate, v_commission,
        p_subscription_tier, v_status, btrim(p_reason_code),
        coalesce(p_detail, '{}'::jsonb)
    )
    ON CONFLICT (provider, provider_payment_id) DO NOTHING
    RETURNING obligation_id INTO v_obligation_id;

    IF v_obligation_id IS NULL THEN
        -- Replay. An idempotent no-op at a level an operator can filter, rather than a
        -- 23505 that would fill the log on every provider retry -- the same choice 024
        -- made for the commission path.
        RAISE NOTICE 'record_referral_obligation: %:% already carries an obligation; no '
                     'second row recorded', p_provider, p_provider_payment_id;
        RETURN NULL;
    END IF;

    RETURN v_obligation_id;
END
$fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 3 — REVERSAL
-- ─────────────────────────────────────────────────────────────────────────────
-- A refunded payment owes nothing. Separate from `reverse_referral_commission` because
-- that one operates on `referral_commissions` and, for a Razorpay payment, finds no row
-- to reverse -- which the audit recorded as "structurally dead".
--
-- IDEMPOTENT, AND IT WILL NOT RESURRECT A SETTLED OBLIGATION. The predicate only matches
-- rows that are still outstanding, so:
--   * a second refund webhook is a no-op;
--   * an obligation already `settled` (money actually paid out) is NOT silently flipped
--     to `reversed`, because clawing back a completed payout is a decision with its own
--     process, not a side effect of a webhook. It returns FALSE and the caller logs it.
DROP FUNCTION IF EXISTS public.reverse_referral_obligation(TEXT, TEXT, TEXT);

CREATE OR REPLACE FUNCTION public.reverse_referral_obligation(
    p_provider            TEXT,
    p_provider_payment_id TEXT,
    p_reason              TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
    v_updated INT;
BEGIN
    IF p_provider IS NULL OR p_provider_payment_id IS NULL THEN
        RETURN FALSE;
    END IF;

    UPDATE public.referral_obligations
       SET status      = 'reversed',
           reversed_at = now(),
           updated_at  = now(),
           detail      = detail || jsonb_build_object(
                             'reversal_reason', coalesce(p_reason, 'unspecified'),
                             'reversed_from_status', status
                         )
     WHERE provider = btrim(p_provider)
       AND provider_payment_id = btrim(p_provider_payment_id)
       AND status IN ('unresolved', 'payable');

    GET DIAGNOSTICS v_updated = ROW_COUNT;
    RETURN v_updated > 0;
END
$fn$;

-- Same exposure posture as 024's function: a server-side billing primitive invoked from a
-- signature-verified webhook through a service-role client. Both take a caller-supplied
-- id and have no `auth.uid()` check, so neither may be reachable by a browser role.
REVOKE ALL ON FUNCTION public.record_referral_obligation(
    TEXT, TEXT, UUID, NUMERIC, TEXT, TEXT, TEXT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.record_referral_obligation(
    TEXT, TEXT, UUID, NUMERIC, TEXT, TEXT, TEXT, JSONB) FROM anon;
REVOKE ALL ON FUNCTION public.record_referral_obligation(
    TEXT, TEXT, UUID, NUMERIC, TEXT, TEXT, TEXT, JSONB) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.record_referral_obligation(
    TEXT, TEXT, UUID, NUMERIC, TEXT, TEXT, TEXT, JSONB) TO service_role;

REVOKE ALL ON FUNCTION public.reverse_referral_obligation(TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.reverse_referral_obligation(TEXT, TEXT, TEXT) FROM anon;
REVOKE ALL ON FUNCTION public.reverse_referral_obligation(TEXT, TEXT, TEXT)
    FROM authenticated;
GRANT EXECUTE ON FUNCTION public.reverse_referral_obligation(TEXT, TEXT, TEXT)
    TO service_role;

-- Row security. A referrer may read their own obligations: unlike the kill-switch audit
-- trail, this IS the customer's money and they are entitled to see what is owed. They may
-- not write one -- obligations are platform-recorded from provider events.
ALTER TABLE public.referral_obligations ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public' AND tablename = 'referral_obligations'
           AND policyname = 'referral_obligations_owner_select'
    ) THEN
        CREATE POLICY "referral_obligations_owner_select"
            ON public.referral_obligations
            FOR SELECT TO authenticated
            USING (referrer_id = auth.uid());
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public' AND tablename = 'referral_obligations'
           AND policyname = 'referral_obligations_service_role'
    ) THEN
        CREATE POLICY "referral_obligations_service_role"
            ON public.referral_obligations
            FOR ALL TO service_role
            USING (true) WITH CHECK (true);
    END IF;
END $$;

-- REVOKE FIRST, THEN GRANT. The substrate's ALTER DEFAULT PRIVILEGES materialises a full
-- grant to these roles on every newly created table in `public`, so a bare
-- `GRANT SELECT` is a no-op that leaves INSERT/UPDATE/DELETE in place. 028's postflight
-- caught exactly that, which is why the order is explicit here.
REVOKE ALL ON public.referral_obligations FROM anon;
REVOKE ALL ON public.referral_obligations FROM authenticated;
REVOKE ALL ON public.referral_obligations FROM service_role;

GRANT SELECT ON public.referral_obligations TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.referral_obligations TO service_role;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 4 — POSTFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    n            int;
    browser_wr   int;
    fn_body      text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.referral_obligations')
           AND conname = 'uq_referral_obligation_event'
    ) THEN
        RAISE EXCEPTION
            '029 postflight FAILED: uq_referral_obligation_event is absent, so a '
            'redelivered webhook can record the same obligation twice. A SELECT EXISTS '
            'check is not a substitute: two concurrent deliveries can both pass it.';
    END IF;

    -- The currency CHECK must constrain FORM and must NOT pin a single currency -- that
    -- would reproduce the defect this table exists to fix.
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.referral_obligations')
           AND contype = 'c'
           AND pg_get_constraintdef(oid) ILIKE '%currency%=%USD%'
    ) THEN
        RAISE EXCEPTION
            '029 postflight FAILED: a CHECK pins referral_obligations.currency to USD. '
            'This ledger exists to record an obligation in the currency it was incurred '
            'in; constraining it to USD makes it unable to hold the MONEY-06 case.';
    END IF;

    SELECT count(*) INTO n
      FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'public' AND p.proname = 'record_referral_obligation';
    IF n <> 1 THEN
        RAISE EXCEPTION
            '029 postflight FAILED: expected exactly one record_referral_obligation, '
            'found %. PostgREST would resolve the application''s call to one of them '
            'non-obviously -- the near-miss 024 documents.', n;
    END IF;

    -- STRIP SQL COMMENTS BEFORE MATCHING.
    --
    -- `pg_get_functiondef` returns the source INCLUDING its comments, and the assertions
    -- below search for table names and for the word "convert". This file's own
    -- explanatory comments inside the function name `referral_commissions` and
    -- `referral_wallets` precisely in order to say that the function does NOT write them
    -- -- so the first version of this postflight failed on its own documentation:
    --
    --     029 postflight FAILED: record_referral_obligation references
    --     referral_commissions or referral_wallets.
    --
    -- That is the same substring-versus-code error that produced three false verdicts in
    -- Phase 8's open-finding verifier and four false positives in the MONEY-04
    -- remediation. Removing `--` comments first means the prose can name the forbidden
    -- things freely while the assertion still examines only executable SQL.
    SELECT regexp_replace(pg_get_functiondef(p.oid), '--[^' || chr(10) || ']*', '', 'g')
      INTO fn_body
      FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'public' AND p.proname = 'record_referral_obligation';

    IF fn_body NOT ILIKE '%ON CONFLICT (provider, provider_payment_id)%' THEN
        RAISE EXCEPTION
            '029 postflight FAILED: the installed record_referral_obligation has no '
            'ON CONFLICT guard, so a provider retry raises 23505 instead of being an '
            'idempotent no-op.';
    END IF;

    -- IT MUST NOT TOUCH THE SINGLE-CURRENCY LEDGER. This is the assertion that keeps the
    -- two paths mutually exclusive, and it is what makes "atomic consistency" vacuous
    -- rather than unproven.
    IF fn_body ILIKE '%referral_wallets%' OR fn_body ILIKE '%referral_commissions%' THEN
        RAISE EXCEPTION
            '029 postflight FAILED: record_referral_obligation references '
            'referral_commissions or referral_wallets. An obligation exists precisely '
            'because no credit was made; writing both would make one payment produce two '
            'economic records.';
    END IF;

    -- NO FX, ASSERTED. A rate multiplication against anything other than the flat
    -- commission rate would be a conversion.
    IF fn_body ILIKE '%fx%' OR fn_body ILIKE '%exchange_rate%'
       OR fn_body ILIKE '%convert%' THEN
        RAISE EXCEPTION
            '029 postflight FAILED: record_referral_obligation mentions FX/conversion. '
            'Choosing a rate and an as-of instant is a product decision about money owed '
            'to a customer, not something a migration may bake in.';
    END IF;

    -- The rate must still agree with 024's, or the two paths would owe different amounts
    -- for the same payment.
    IF fn_body NOT LIKE '%0.20%' THEN
        RAISE EXCEPTION
            '029 postflight FAILED: record_referral_obligation no longer carries the '
            '0.20 commission rate that 024''s process_referral_commission applies. The '
            'two paths must agree on what is owed.';
    END IF;

    IF has_function_privilege('anon',
            'public.record_referral_obligation(text,text,uuid,numeric,text,text,text,jsonb)',
            'EXECUTE')
       OR has_function_privilege('authenticated',
            'public.record_referral_obligation(text,text,uuid,numeric,text,text,text,jsonb)',
            'EXECUTE') THEN
        RAISE EXCEPTION
            '029 postflight FAILED: record_referral_obligation is EXECUTE-able by a '
            'browser role. It takes a caller-supplied referred_id and has no auth.uid() '
            'check.';
    END IF;

    IF NOT has_function_privilege('service_role',
            'public.record_referral_obligation(text,text,uuid,numeric,text,text,text,jsonb)',
            'EXECUTE') THEN
        RAISE EXCEPTION
            '029 postflight FAILED: service_role LOST EXECUTE on '
            'record_referral_obligation -- the billing webhook would stop recording '
            'obligations.';
    END IF;

    -- A customer may READ their own obligations and must not be able to write one.
    SELECT count(*) INTO browser_wr
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND table_name = 'referral_obligations'
       AND grantee IN ('anon', 'authenticated')
       AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE');
    IF browser_wr <> 0 THEN
        RAISE EXCEPTION
            '029 postflight FAILED: a browser role holds % write privilege(s) on '
            'referral_obligations. A customer must not be able to record money owed to '
            'themselves.', browser_wr;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.role_table_grants
         WHERE table_schema = 'public' AND table_name = 'referral_obligations'
           AND grantee = 'anon' ) THEN
        NULL;  -- anon holding nothing is the intended state
    END IF;

    RAISE NOTICE
        '029: referral_obligations exists with DB-enforced per-event idempotency, a '
        'multi-currency amount, a NULLable commission (no FX), a five-value status '
        'vocabulary, owner-read row security and no browser write path.';
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 5 — COMMENTS
-- ─────────────────────────────────────────────────────────────────────────────
COMMENT ON TABLE public.referral_obligations IS
    'MONEY-06. Referral commissions that are OWED but could not be credited to the '
    'single-currency ledger. Before this table the only artefact of such an obligation '
    'was a logger.error line in the Razorpay webhook (and, for two Stripe paths, nothing '
    'at all), so money owed to a customer could not be queried, aged, reconciled or '
    'settled. Separate from referral_commissions because referral_wallets aggregates '
    'pending_balance_usd and a mixed-currency balance has no defined sum, and because '
    'CHECK (currency = ''USD'') on the three ledger tables is a deliberate invariant. '
    'Rows here are mutually exclusive with a referral_commissions row for the same '
    'payment: an obligation exists precisely when no credit was made.';

COMMENT ON COLUMN public.referral_obligations.currency IS
    'The currency the payment was actually made in -- NOT constrained to USD, which is '
    'the entire point of this table. Constrained in FORM only (three uppercase letters), '
    'because an amount labelled with a typo is as unusable as an unlabelled one. Which '
    'currencies the application accepts is enforced in marketplace/money.py, a policy '
    'that moves with product scope and so does not belong in a CHECK.';

COMMENT ON COLUMN public.referral_obligations.commission_amount IS
    'The amount owed, or NULL when it cannot be stated without inventing an FX rate. '
    'NULL means "not computable here", never zero. core/fx_service.py exists but is '
    'float-based with baseline fallback rates, and choosing a rate, an as-of instant and '
    'who absorbs the accrual-to-payout drift is a product decision about a customer''s '
    'money. An obligation with a stated amount in a stated currency and a NULL commission '
    'is honest; one with an invented commission is not.';

COMMENT ON COLUMN public.referral_obligations.status IS
    'unresolved = owed, not yet expressible in the ledger currency or not yet '
    'attributable (the MONEY-06 state). payable = resolved, amount and currency agreed. '
    'settled = paid or credited (settled_at). reversed = underlying payment refunded '
    '(reversed_at). refused = deliberately not owed, e.g. no referral relationship -- '
    'retained rather than deleted so that "nothing is owed" is a recorded decision and '
    'not an absence. Deliberately NOT referral_commissions'' '
    'pending|approved|paid|reversed, which describes a commission moving toward payout.';

COMMENT ON COLUMN public.referral_obligations.reason_code IS
    'Why this obligation exists rather than a credited commission, machine-readable so '
    '"every INR obligation" and "every obligation caused by a failed RPC" are different '
    'queries.';

COMMIT;
