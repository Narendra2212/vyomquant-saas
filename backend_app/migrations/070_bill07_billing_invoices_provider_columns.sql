-- ============================================================================
-- 070_bill07_billing_invoices_provider_columns.sql
--
-- BILL-07. `public.billing_invoices` is the billing-history ledger. Its one
-- writer names five columns that this schema has never declared, so the write
-- has never once succeeded:
--
--     sb_inv.table("billing_invoices").insert({            billing.py:2716
--         "provider": "razorpay",              <-- undeclared
--         "provider_payment_id": payment_id,   <-- undeclared
--         "amount_usd": 0,                     <-- undeclared
--         "amount_inr": str(payment_amount),   <-- undeclared
--         "plan": item_key,                    <-- undeclared
--         ... user_id, currency, status, created_at   (declared)
--     }).execute()
--
-- PostgREST rejects the whole statement on an undefined column, and the call
-- sits inside `try/except ... logger.warning`, so the failure has always been
-- a log line. Measured read-only against production and against a cluster
-- provisioned from repository source -- the two agree exactly:
--
--     billing_invoices columns, both databases : 11
--     of the 9 the insert sends, missing       : 5
--     existing invoice rows in production      : 0
--
-- The repository already knew. `tests/test_schema_table_reference_drift.py`
-- carries all five in KNOWN_UNDECLARED_COLUMN_DEFECTS, and
-- `.kiro/specs/production-launch-hardening/tasks.md` states the consequence
-- outright -- "no invoice row has ever been written". Both deferred it for one
-- stated reason: `routers/billing.py` carried a parallel pricing workstream's
-- uncommitted changes and editing it would collide. That workstream landed in
-- PR #285, so the reason no longer holds. This file is the deferred half.
--
-- WHAT WAS BROKEN BY IT, beyond an empty billing page:
--
--   1. `GET /api/billing/invoices` can only ever answer `[]`. The endpoint
--      itself is correct -- it selects `*` and projects with `.get(...)`
--      defaults, so it degrades rather than erroring -- but its source is a
--      table nothing can write to.
--   2. A FULL REFUND NEVER REVOKES THE PLAN.
--      `_revoke_plan_for_refunded_razorpay_payment` (billing.py:1608) finds the
--      purchase with `.select("user_id,plan").eq("provider", ...)
--      .eq("provider_payment_id", ...)`. Those are three of the five undeclared
--      names, and unlike the insert this read is NOT wrapped, so the refund
--      webhook raises, Razorpay redelivers into the same failure, and a
--      refunded customer keeps the plan they were refunded for.
--
-- WHY NULLABLE, AND WHY NO `DEFAULT 0` ON THE AMOUNTS.
-- `DATABASE_SCHEMA_MIGRATION.md:45` sketches `amount_inr DECIMAL(10,2) NOT NULL
-- DEFAULT 0`. That default is declined deliberately: a `0` on a row whose
-- amount is unknown asserts the payment was zero, which is the fabricated-zero
-- shape this repository forbids elsewhere and which the reading endpoint
-- already avoids ("a null is honest where a fabricated 'razorpay' would not
-- be", billing.py:2128). Nullable also leaves any pre-existing row untouched
-- rather than back-dating a denomination onto it. numeric(18,2) rather than
-- DECIMAL(10,2) because (10,2) caps at 99,999,999.99 and an INR annual
-- enterprise figure has no business sitting that close to a ceiling.
--
-- WHY A PARTIAL UNIQUE INDEX, NOT A PLAIN ONE.
-- `provider_payment_id` is the provider's own idempotency key, and the pair is
-- exactly what the refund path filters on, so one index serves both duties.
-- It is partial because the legacy columns are nullable: a plain UNIQUE would
-- admit unlimited NULL/NULL pairs (NULLs are distinct in a unique index) which
-- is harmless, but the predicate states the intent -- uniqueness applies to
-- rows that actually identify a provider payment. With it in place a webhook
-- redelivery collides instead of filing a second invoice for one charge, which
-- is what makes the handler's retry safe rather than duplicating.
--
-- PRIVILEGE IS NOT SET HERE. `anon` holds SELECT/INSERT/UPDATE/DELETE on this
-- table and `authenticated` holds all four through a `FOR ALL` owner policy --
-- so a customer could file or delete their own invoice. That correction belongs
-- in `migrations/hardening/999_privilege_hardening.sql`, beside the identical
-- blocks it already applies to `invoices`, `subscriptions` and
-- `payment_methods`, because that file runs LAST (STEP 4) and is re-applied on
-- every provision, so a fix placed there is self-healing. It is applied there
-- in the same commit as this file. Why the existing preflight did not catch it:
-- that guard fires on tables with RLS *disabled* that grant anon SELECT, and
-- this table has RLS enabled, so it was exempt. RLS does block the rows -- no
-- anon policy exists -- which is why this is defence-in-depth rather than an
-- open door, and why it is stated at that size.
--
-- IDEMPOTENT. ADD COLUMN IF NOT EXISTS throughout; constraints and the index
-- added under guards, so a database that already has some of this acquires the
-- rest.
--
-- ROLLBACK. backend_app/migrations/rollback/070_bill07_rollback.sql
-- ============================================================================

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 0 — PREFLIGHT
-- ─────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('public.billing_invoices') IS NULL THEN
        RAISE EXCEPTION
            '070 precondition failed: public.billing_invoices does not exist. '
            'It is declared by migrations/006_reconcile_production_database.sql '
            '(PART 11). Apply the numbered SQL set first; see '
            'backend_app/migrations/PROVISIONING_ORDER.md.';
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 1 — THE FIVE COLUMNS THE WRITER HAS ALWAYS NAMED
-- ─────────────────────────────────────────────────────────────────────────
ALTER TABLE public.billing_invoices
    ADD COLUMN IF NOT EXISTS provider            text,
    ADD COLUMN IF NOT EXISTS provider_payment_id text,
    ADD COLUMN IF NOT EXISTS amount_usd          numeric(18, 2),
    ADD COLUMN IF NOT EXISTS amount_inr          numeric(18, 2),
    ADD COLUMN IF NOT EXISTS plan                text;

COMMENT ON COLUMN public.billing_invoices.provider IS
    'Payment provider that captured this charge (''razorpay''). NULL on rows '
    'that predate provider metadata -- honest, rather than back-dated.';
COMMENT ON COLUMN public.billing_invoices.provider_payment_id IS
    'The provider''s own payment id. Carries the idempotency guarantee: unique '
    'per provider via uq_billing_invoices_provider_payment, so a webhook '
    'redelivery cannot file a second invoice for one charge.';
COMMENT ON COLUMN public.billing_invoices.amount_inr IS
    'Exact major-unit INR amount. NULL when unknown -- never 0, which would '
    'assert a zero-value payment.';
COMMENT ON COLUMN public.billing_invoices.plan IS
    'The item_key the payment bought. Read by the refund path to decide '
    'whether a full refund should revoke the plan.';

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 2 — CONSTRAINTS
--
-- Blank is the same defect as missing, and a blank provider_payment_id would
-- additionally defeat the partial unique index below by being a real value
-- that identifies nothing. The amount guards are the ordinary money guard:
-- a negative capture is not a refund, it is a bad write.
-- ─────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.billing_invoices')
           AND conname = 'chk_billing_invoices_provider_not_blank'
    ) THEN
        ALTER TABLE public.billing_invoices
            ADD CONSTRAINT chk_billing_invoices_provider_not_blank
            CHECK (provider IS NULL OR btrim(provider) <> '');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.billing_invoices')
           AND conname = 'chk_billing_invoices_payment_id_not_blank'
    ) THEN
        ALTER TABLE public.billing_invoices
            ADD CONSTRAINT chk_billing_invoices_payment_id_not_blank
            CHECK (provider_payment_id IS NULL
                   OR btrim(provider_payment_id) <> '');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.billing_invoices')
           AND conname = 'chk_billing_invoices_plan_not_blank'
    ) THEN
        ALTER TABLE public.billing_invoices
            ADD CONSTRAINT chk_billing_invoices_plan_not_blank
            CHECK (plan IS NULL OR btrim(plan) <> '');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.billing_invoices')
           AND conname = 'chk_billing_invoices_amounts_nonnegative'
    ) THEN
        ALTER TABLE public.billing_invoices
            ADD CONSTRAINT chk_billing_invoices_amounts_nonnegative
            CHECK ((amount_usd IS NULL OR amount_usd >= 0)
               AND (amount_inr IS NULL OR amount_inr >= 0));
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 3 — THE IDEMPOTENCY / REFUND-LOOKUP INDEX
--
-- One index, two duties: it makes a redelivered capture collide rather than
-- duplicate, and it is exactly the filter
-- `_revoke_plan_for_refunded_razorpay_payment` issues
-- (provider = ... AND provider_payment_id = ...).
-- ─────────────────────────────────────────────────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_invoices_provider_payment
    ON public.billing_invoices (provider, provider_payment_id)
    WHERE provider IS NOT NULL AND provider_payment_id IS NOT NULL;

-- The billing page reads "my invoices, newest first". idx_billing_invoices_user
-- (migration 006) covers user_id alone; adding created_at DESC lets that read
-- be served in index order instead of sorting the user's rows every call.
CREATE INDEX IF NOT EXISTS idx_billing_invoices_user_created
    ON public.billing_invoices (user_id, created_at DESC);

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 4 — POSTFLIGHT, IN THIS TRANSACTION
--
-- ADD COLUMN IF NOT EXISTS silently accepts a column of a DIFFERENT type, and
-- the whole point of this file is that the writer's names must resolve. So
-- every property is read back rather than assumed.
-- ─────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    missing   text;
    wrong     text;
    check_cnt integer;
BEGIN
    SELECT string_agg(c, ', ' ORDER BY c) INTO missing
      FROM unnest(ARRAY['provider', 'provider_payment_id', 'amount_usd',
                        'amount_inr', 'plan']) AS c
     WHERE NOT EXISTS (
         SELECT 1 FROM information_schema.columns
          WHERE table_schema = 'public' AND table_name = 'billing_invoices'
            AND column_name = c
     );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            '070 postflight FAILED: billing_invoices is still missing '
            'column(s): %. The webhook writer names them, so the invoice '
            'write would continue to fail silently.', missing;
    END IF;

    -- The amounts must be exact. A float money column would reintroduce the
    -- defect MONEY-02 removed from the arithmetic.
    SELECT string_agg(column_name || ' is ' || data_type, ', ' ORDER BY column_name)
      INTO wrong
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'billing_invoices'
       AND column_name IN ('amount_usd', 'amount_inr')
       AND data_type <> 'numeric';
    IF wrong IS NOT NULL THEN
        RAISE EXCEPTION
            '070 postflight FAILED: money column(s) not numeric: %. Exact '
            'decimal is required; binary float on a money amount is the '
            'defect MONEY-02 removed from this module''s arithmetic.', wrong;
    END IF;

    SELECT count(*) INTO check_cnt
      FROM pg_constraint
     WHERE conrelid = to_regclass('public.billing_invoices')
       AND contype = 'c'
       AND conname IN ('chk_billing_invoices_provider_not_blank',
                       'chk_billing_invoices_payment_id_not_blank',
                       'chk_billing_invoices_plan_not_blank',
                       'chk_billing_invoices_amounts_nonnegative');
    IF check_cnt <> 4 THEN
        RAISE EXCEPTION
            '070 postflight FAILED: expected 4 CHECK constraints on '
            'billing_invoices, found %.', check_cnt;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public' AND tablename = 'billing_invoices'
           AND indexname = 'uq_billing_invoices_provider_payment'
    ) THEN
        RAISE EXCEPTION
            '070 postflight FAILED: uq_billing_invoices_provider_payment is '
            'absent. Without it a redelivered webhook files a second invoice '
            'for one charge, and the refund lookup has no index.';
    END IF;

    RAISE NOTICE
        '070: billing_invoices now declares provider, provider_payment_id, '
        'amount_usd, amount_inr, plan -- unique per provider payment. The '
        'invoice write and the refund revocation can both resolve.';
END $$;
