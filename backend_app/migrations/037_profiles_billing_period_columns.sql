-- backend_app/migrations/037_profiles_billing_period_columns.sql
--
-- BILL-01 / BILL-05 (P1). Application code reads and writes five billing-lifecycle columns on
-- public.profiles that NO migration declared: subscription_status, next_billing_date,
-- cancel_at_period_end, payment_provider, trial_end_date. A database provisioned from this
-- repository therefore had no place to record when a purchased plan ends, so (a) a Razorpay
-- plan purchase granted the tier with no end date and never expired, and (b) cancel/resume
-- failed with an undefined-column error.
--
-- Idempotent and additive: ADD COLUMN IF NOT EXISTS is a no-op on an environment that already
-- has them (e.g. a production database that was altered by hand), whatever their exact type.
-- No data is touched. These are PLATFORM columns: migration 033's trigger already rejects any
-- change to them from the browser roles (it allow-lists the user-editable columns instead of
-- deny-listing platform ones), so no new guard is required.

ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS subscription_status    text;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS next_billing_date      timestamptz;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS cancel_at_period_end   boolean NOT NULL DEFAULT false;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS payment_provider       text;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS trial_end_date         timestamptz;

-- The expiry sweep selects by (provider, period end); keep that scan cheap.
CREATE INDEX IF NOT EXISTS idx_profiles_plan_period_end
    ON public.profiles (next_billing_date)
    WHERE payment_provider = 'razorpay' AND subscription_tier <> 'free';
