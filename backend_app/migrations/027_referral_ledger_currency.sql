--
-- PHASE 8, STEP 8B: the referral ledger declares its currency.
--
-- MONEY-04 — WHAT WAS ACTUALLY FOUND, AND WHAT THIS FILE DOES AND DOES NOT CLOSE
-- ==============================================================================
-- MONEY-04 was recorded as an open P0: a Razorpay payment captured in paise being
-- credited as though it were USD, overstating the referral commission by roughly the
-- USD/INR rate (~83x). That defect DOES NOT EXIST IN THE CODE as it now stands, and this
-- file is not what fixes it. `backend_app/routers/billing.py:2562-2608` is an explicit
-- refusal block: the Razorpay webhook branch does not call `process_referral_commission`
-- at all. The only surviving commission credit is the Stripe one at
-- `billing.py:2216-2223`, whose amount comes from `_provider_major_amount(..., "USD")`.
-- The open status was stale documentation, reconciled in `staging/MONEY_FLOW_AUDIT.md`
-- and `staging/REMEDIATION_REPORT.md` with that code and test evidence rather than on the
-- strength of an earlier report.
--
-- What the re-examination DID find still open is narrower and is what this file addresses:
--
--     referral_commissions, referral_wallets and referral_payouts carry no currency
--     column at all. The unit is encoded only in COLUMN NAMES — payment_amount_usd,
--     commission_amount_usd, pending_balance_usd, approved_balance_usd,
--     paid_balance_usd, lifetime_earnings_usd, amount_usd.
--
-- A naming convention is enforced by nothing. Measured on the live catalogue before
-- writing this file (staging/probe_referral_ledger_currency.py):
--
--     referral_commissions   15 columns   HAS_CURRENCY=False   0 rows
--     referral_wallets       13 columns   HAS_CURRENCY=False  20 rows
--     referral_payouts        9 columns   HAS_CURRENCY=False   0 rows
--
-- Every other money ledger in this repository already does the opposite. 008's
-- `marketplace_settlements`, 009's `paper_sessions` and `paper_accounts`, and 007's
-- pricing table all declare `currency TEXT NOT NULL` beside their minor-unit amounts.
-- The referral ledger is the single money table set that does not, and the invariant
-- "every referral credit is USD" is currently asserted only by
-- `tests/test_money02_billing_amount_precision.py`, which parses the AST of ONE module.
-- A credit site added from any other module would satisfy every existing test.
--
-- WHAT THIS FILE GUARANTEES
--   An explicit non-USD credit is refused BY THE DATABASE, for every writer, from every
--   module, including a future one. `currency` becomes a declared, NOT NULL part of each
--   row, so the unit of every stored amount is recorded rather than implied.
--
-- WHAT THIS FILE DOES NOT GUARANTEE — STATED PLAINLY
--   It cannot detect an amount that is in the wrong unit but declared as USD. A caller
--   that writes an INR figure into `commission_amount_usd` without naming `currency`
--   still gets the 'USD' default and still succeeds. No constraint can distinguish
--   "20.00 USD" from "20.00 rupees labelled USD"; nothing at the database level can.
--   That residual is precisely what the refusal block in `billing.py` covers for the one
--   provider that can produce it, and it is recorded as MONEY-06 (Razorpay referral
--   commissions are never credited, so settlement is manual). This file narrows the
--   surface from "any module may credit any unit silently" to "any module may credit USD,
--   and must say so to credit anything else" — it does not eliminate it.
--
-- WHY `NOT NULL DEFAULT 'USD'` AND NOT A BARE `NOT NULL`
--   A bare NOT NULL would force every writer to name the column, which is stronger. It
--   was rejected on measurement, not preference:
--     * `backend_app/routers/referral.py:770-779` inserts into `referral_payouts` from
--       APPLICATION code via PostgREST without naming `currency`. A bare NOT NULL breaks
--       the payout path.
--     * Three separate migrations define functions that insert into this ledger (024,
--       migrations/referral_system_redesign.sql, migrations/006_reconcile_production_-
--       database.sql). Only two are installed and neither names `currency` (measured:
--       create_referral_code_for_user(uuid), process_referral_commission(uuid,varchar,
--       varchar,numeric) — names_currency=False for both). A bare NOT NULL is therefore
--       also a replay hazard for any of those files applied later.
--   With a default, both installed functions keep working with no code change and the
--   CHECK still refuses an explicit non-USD value.
--
-- WHY A CHECK AND NOT AN ENUM OR AN FX TABLE
--   `CHECK (currency = 'USD')` states the truth about this ledger today: it is
--   single-currency. It is deliberately NOT `CHECK (currency IN ('USD','INR'))`, because
--   permitting INR without a defined conversion is how MONEY-04 was reported in the first
--   place. No conversion rate is invented here and no implicit FX is performed. Widening
--   this constraint is the one place a multi-currency referral ledger would begin, and
--   doing so forces an explicit decision about how the balances are denominated.
--
-- Additive. No DROP TABLE, no DROP COLUMN, no row deletion, no data rewrite beyond
-- filling the new column with its default. Idempotent: every statement is guarded, and
-- re-applying the file reaches the same state.
--
-- REGRESSION: tests/test_money04_referral_ledger_is_single_currency_postgres.py
--

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1 — PREFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
-- Two things are checked before anything is altered:
--   (a) at least one of the three tables exists, otherwise this file is being applied to
--       a database with no referral system and should say so rather than succeed silently;
--   (b) if a `currency` column already exists on any of them, no row holds a value other
--       than 'USD'. Adding the CHECK over such data would fail with an opaque message,
--       and those rows would be a MONEY problem a migration must not resolve by itself.
DO $$
DECLARE
    present    int := 0;
    tbl        text;
    offending  int;
    sample     text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['referral_commissions', 'referral_wallets', 'referral_payouts']
    LOOP
        IF to_regclass('public.' || tbl) IS NULL THEN
            RAISE NOTICE '027 preflight: public.% is absent; it will be skipped', tbl;
            CONTINUE;
        END IF;
        present := present + 1;

        IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = tbl AND column_name = 'currency'
        ) THEN
            EXECUTE format(
                'SELECT count(*), string_agg(DISTINCT currency, '', '') '
                '  FROM public.%I WHERE currency IS DISTINCT FROM ''USD''', tbl
            ) INTO offending, sample;

            IF coalesce(offending, 0) > 0 THEN
                RAISE EXCEPTION
                    '027 PREFLIGHT: public.% already has a currency column and % row(s) '
                    'hold a value other than ''USD'' (%). Those are stored amounts whose '
                    'denomination this file cannot reconcile — it will not pick a rate or '
                    'relabel them. Settle them explicitly, then re-run.',
                    tbl, offending, sample;
            END IF;
        END IF;
    END LOOP;

    IF present = 0 THEN
        RAISE EXCEPTION
            '027 refused: none of referral_commissions, referral_wallets or '
            'referral_payouts exists on this database. This file declares the currency of '
            'the referral ledger; applying it where that ledger has not been provisioned '
            'would record a migration that guarantees nothing. Provision the referral '
            'system first (migrations/referral_system_redesign.sql).';
    END IF;

    RAISE NOTICE '027 preflight: % referral ledger table(s) present, no non-USD rows', present;
END
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2 — THE COLUMN
-- ─────────────────────────────────────────────────────────────────────────────
-- `ADD COLUMN IF NOT EXISTS` so replay is a no-op. On PostgreSQL 11+ a NOT NULL column
-- with a constant default is added without rewriting the table, so the 20 existing
-- `referral_wallets` rows are filled with 'USD' in place.
--
-- Guarded per table: a database where the referral system was never provisioned keeps
-- working, and Section 4 only asserts about tables that are actually present.
DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['referral_commissions', 'referral_wallets', 'referral_payouts']
    LOOP
        IF to_regclass('public.' || tbl) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format(
            'ALTER TABLE public.%I ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL '
            'DEFAULT ''USD''', tbl
        );
    END LOOP;
END
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 3 — THE CONSTRAINT THAT MAKES IT A GUARANTEE
-- ─────────────────────────────────────────────────────────────────────────────
-- Without this, `currency` is documentation that any writer can contradict. With it, a
-- credit that declares a currency this ledger cannot denominate is refused by the server.
--
-- Guarded on `pg_constraint.conname` rather than added with `DROP CONSTRAINT IF EXISTS`
-- first: dropping would open a window inside this transaction in which the column exists
-- and nothing constrains it.
DO $$
DECLARE
    tbl  text;
    cons text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['referral_commissions', 'referral_wallets', 'referral_payouts']
    LOOP
        IF to_regclass('public.' || tbl) IS NULL THEN
            CONTINUE;
        END IF;
        cons := 'chk_' || tbl || '_currency_usd';

        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = to_regclass('public.' || tbl) AND conname = cons
        ) THEN
            EXECUTE format(
                'ALTER TABLE public.%I ADD CONSTRAINT %I CHECK (currency = ''USD'')',
                tbl, cons
            );
            RAISE NOTICE '027: added % on public.%', cons, tbl;
        END IF;
    END LOOP;
END
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 4 — POSTFLIGHT. FAIL SAFELY IF THE SCHEMA IS NOT WHAT THIS FILE ASSUMED.
-- ─────────────────────────────────────────────────────────────────────────────
-- `ADD COLUMN IF NOT EXISTS` silently accepts a pre-existing column of a DIFFERENT shape
-- — nullable, or with no default, or typed something else. A migration that can quietly
-- leave a weaker invariant than it claims is worse than one that fails. Every property
-- this file is supposed to establish is read back from the catalogue and asserted, inside
-- the same transaction, so a mismatch rolls the whole thing back.
DO $$
DECLARE
    tbl       text;
    checked   int := 0;
    col_type  text;
    nullable  text;
    coldflt   text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['referral_commissions', 'referral_wallets', 'referral_payouts']
    LOOP
        IF to_regclass('public.' || tbl) IS NULL THEN
            CONTINUE;
        END IF;

        SELECT data_type, is_nullable, column_default
          INTO col_type, nullable, coldflt
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = tbl AND column_name = 'currency';

        IF col_type IS NULL THEN
            RAISE EXCEPTION
                '027 postflight FAILED: public.% has no currency column after section 2.', tbl;
        END IF;

        IF col_type <> 'text' THEN
            RAISE EXCEPTION
                '027 postflight FAILED: public.%.currency is %, not text. A pre-existing '
                'column of another type was left in place by ADD COLUMN IF NOT EXISTS.',
                tbl, col_type;
        END IF;

        IF nullable <> 'NO' THEN
            RAISE EXCEPTION
                '027 postflight FAILED: public.%.currency is nullable, so an amount can '
                'still be stored with no declared unit — which is the whole finding.', tbl;
        END IF;

        IF coldflt IS NULL OR coldflt NOT LIKE '%USD%' THEN
            RAISE EXCEPTION
                '027 postflight FAILED: public.%.currency has default %, not ''USD''. The '
                'two installed ledger functions insert without naming the column and would '
                'fail.', tbl, coalesce(coldflt, 'NULL');
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = to_regclass('public.' || tbl)
               AND contype = 'c'
               AND pg_get_constraintdef(oid) ILIKE '%currency%'
        ) THEN
            RAISE EXCEPTION
                '027 postflight FAILED: public.% has a currency column but no CHECK on it. '
                'Unconstrained, the column is documentation a writer can contradict.', tbl;
        END IF;

        checked := checked + 1;
    END LOOP;

    RAISE NOTICE
        '027: % referral ledger table(s) now declare currency TEXT NOT NULL DEFAULT ''USD'' '
        'with a CHECK that refuses any other value.', checked;
END
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 5 — WHAT THE COLUMN MEANS, ON THE COLUMN
-- ─────────────────────────────────────────────────────────────────────────────
-- Guarded like every other section: a bare `COMMENT ON COLUMN` would fail on a database
-- where one of the three tables was never provisioned, contradicting Section 1's decision
-- to skip an absent table rather than refuse. Each comment body is carried in its own
-- dollar-quoted tag (the `EXECUTE $tag$ … $tag$` form used by
-- migrations/referral_system_redesign.sql:889) so the prose needs no quote doubling.
DO $$
BEGIN
    IF to_regclass('public.referral_commissions') IS NOT NULL THEN
        EXECUTE $c_comm$
COMMENT ON COLUMN public.referral_commissions.currency IS
    'Denomination of payment_amount_usd and commission_amount_usd. CHECK-constrained to
     USD: this ledger is single-currency and performs no FX. A non-USD payment must not be
     credited here. See the refusal block in backend_app/routers/billing.py: the Razorpay
     branch records no commission row at all, so settlement is manual (MONEY-06). LIMIT:
     this column records the DECLARED unit; it cannot detect an amount stored in the wrong
     one.'
        $c_comm$;
    END IF;

    IF to_regclass('public.referral_wallets') IS NOT NULL THEN
        EXECUTE $c_wal$
COMMENT ON COLUMN public.referral_wallets.currency IS
    'Denomination of every balance column on this row: balance_usd, total_earned_usd,
     pending_usd, pending_balance_usd, approved_balance_usd, paid_balance_usd,
     lifetime_earnings_usd. CHECK-constrained to USD. Widening it requires deciding how a
     mixed-currency balance is denominated and summed, which is why it is not widened
     here.'
        $c_wal$;
    END IF;

    IF to_regclass('public.referral_payouts') IS NOT NULL THEN
        EXECUTE $c_pay$
COMMENT ON COLUMN public.referral_payouts.currency IS
    'Denomination of amount_usd. CHECK-constrained to USD. This table is written from
     application code by backend_app/routers/referral.py through PostgREST, which does not
     name this column, so the DEFAULT is what applies on that path. That is the measured
     reason the column has a default rather than being a bare NOT NULL.'
        $c_pay$;
    END IF;
END
$$;

COMMIT;
