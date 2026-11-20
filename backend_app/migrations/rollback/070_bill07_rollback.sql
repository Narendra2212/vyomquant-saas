-- ============================================================================
-- rollback/070_bill07_rollback.sql
--
-- Reverses 070_bill07_billing_invoices_provider_columns.sql.
--
-- WHAT THIS DESTROYS. Dropping these five columns drops the DATA IN THEM, and
-- that data is the provider's side of the billing ledger: which gateway took
-- the money, the gateway's own payment id, the amount, and the plan it bought.
-- Unlike the 069 rollback there is no in-process mirror to fall back on, and
-- `amount` (migration 006's column) is not a substitute -- the writer has
-- never populated it.
--
-- The provider payment id is the only handle that ties a row in this table to a
-- charge on a Razorpay statement. Losing it makes the invoice unreconcilable
-- rather than merely sparse. Take a copy first:
--
--     \copy (SELECT id, user_id, provider, provider_payment_id, amount_usd,
--                   amount_inr, currency, status, plan, created_at
--              FROM public.billing_invoices ORDER BY created_at)
--       TO 'billing_invoices_backup.csv' WITH CSV HEADER
--
-- AFTER THIS RUNS the system returns to the state 070 corrected, so be clear
-- that it is a regression, not a neutral undo:
--
--   * the invoice write at billing.py:2716 fails on every captured payment
--     again, silently, because it is wrapped in `except ... logger.critical`;
--   * `GET /api/billing/invoices` answers with rows that have no provider,
--     amount or plan -- and no new rows arrive;
--   * `_revoke_plan_for_refunded_razorpay_payment` raises again, so a FULL
--     REFUND STOPS REVOKING THE PLAN and the refund webhook redelivers
--     indefinitely.
--
-- Only roll this back if 070 itself is implicated in an incident. If the
-- concern is the privilege change, that lives in
-- migrations/hardening/999_privilege_hardening.sql and is reverted there,
-- independently of these columns.
-- ============================================================================

-- Index and constraints first: dropping the columns would take them anyway,
-- but naming them makes the reversal auditable rather than implicit.
DROP INDEX IF EXISTS public.uq_billing_invoices_provider_payment;
DROP INDEX IF EXISTS public.idx_billing_invoices_user_created;

ALTER TABLE public.billing_invoices
    DROP CONSTRAINT IF EXISTS chk_billing_invoices_provider_not_blank,
    DROP CONSTRAINT IF EXISTS chk_billing_invoices_payment_id_not_blank,
    DROP CONSTRAINT IF EXISTS chk_billing_invoices_plan_not_blank,
    DROP CONSTRAINT IF EXISTS chk_billing_invoices_amounts_nonnegative;

ALTER TABLE public.billing_invoices
    DROP COLUMN IF EXISTS provider,
    DROP COLUMN IF EXISTS provider_payment_id,
    DROP COLUMN IF EXISTS amount_usd,
    DROP COLUMN IF EXISTS amount_inr,
    DROP COLUMN IF EXISTS plan;

DO $$
DECLARE
    remaining text;
BEGIN
    SELECT string_agg(column_name, ', ' ORDER BY column_name) INTO remaining
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'billing_invoices'
       AND column_name IN ('provider', 'provider_payment_id', 'amount_usd',
                           'amount_inr', 'plan');
    IF remaining IS NOT NULL THEN
        RAISE EXCEPTION
            '070 rollback: column(s) still present: %.', remaining;
    END IF;

    RAISE NOTICE '070 rollback: the five provider columns are gone. The '
                 'invoice write and the refund revocation are both broken '
                 'again -- this is the pre-070 state, not a neutral undo.';
END $$;
