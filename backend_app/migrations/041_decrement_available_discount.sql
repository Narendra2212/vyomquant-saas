-- backend_app/migrations/041_decrement_available_discount.sql
--
-- BILL-06 (P2). `routers/billing.py::create_checkout_session` reads
-- `profiles.available_discounts` and, if it is greater than zero, applies a 10%
-- discount to the checkout amount -- but nothing anywhere in the codebase ever
-- decremented that column. Once a referral (or whatever else grants a discount)
-- sets `available_discounts` to a positive value, EVERY future checkout for that
-- account applies the discount forever: a one-time benefit that never expires.
--
-- This migration adds the one atomic primitive the fix needs: a SECURITY DEFINER
-- function that decrements `available_discounts` by one, guarded by
-- `available_discounts > 0` in the same statement so two concurrent webhook
-- deliveries for the same user cannot both decrement past zero (a classic
-- read-then-write race a plain PostgREST `UPDATE` through the table builder cannot
-- avoid, since it has no server-side arithmetic expression). It raises when there
-- was nothing to decrement, so the caller (`_apply_billing_entitlement`) can tell
-- "the discount was consumed" from "there was none to begin with" rather than
-- silently succeeding either way.
--
-- Modelled on `increment_ml_addon` (migration 023's own remediation target): a
-- server-side billing primitive called ONLY from the webhook path through a
-- service-role client, where `auth.uid()` is NULL -- so, unlike `increment_ml_addon`
-- before 023, this one is created with EXECUTE revoked from anon/authenticated from
-- day one rather than fixed retroactively.
--
-- Idempotent and additive; touches no existing table structure.

CREATE OR REPLACE FUNCTION public.decrement_available_discount(target_user_id uuid)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    remaining integer;
BEGIN
    UPDATE public.profiles
       SET available_discounts = available_discounts - 1
     WHERE id = target_user_id
       AND available_discounts > 0
    RETURNING available_discounts INTO remaining;

    IF remaining IS NULL THEN
        RAISE EXCEPTION 'no available discount to decrement for user %', target_user_id
            USING ERRCODE = 'P0001';
    END IF;

    RETURN remaining;
END;
$$;

REVOKE ALL ON FUNCTION public.decrement_available_discount(uuid) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON FUNCTION public.decrement_available_discount(uuid) FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON FUNCTION public.decrement_available_discount(uuid) FROM authenticated';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.decrement_available_discount(uuid) TO service_role';
    END IF;
END
$$;

-- Postflight: function exists and is not reachable by anon/authenticated.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname = 'decrement_available_discount'
    ) THEN
        RAISE EXCEPTION 'postflight failed: decrement_available_discount was not created';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
       AND has_function_privilege('anon', 'public.decrement_available_discount(uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION 'postflight failed: decrement_available_discount is callable by anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated')
       AND has_function_privilege('authenticated', 'public.decrement_available_discount(uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION 'postflight failed: decrement_available_discount is callable by authenticated';
    END IF;
END
$$;
