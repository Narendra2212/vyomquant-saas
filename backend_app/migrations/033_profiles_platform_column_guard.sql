-- backend_app/migrations/033_profiles_platform_column_guard.sql
--
-- SEC-07 (P0). Any signed-in user could grant themselves a paid plan, raise limits
-- and unfreeze their own account straight through the Supabase REST API.
--
-- ROOT CAUSE
-- `public.profiles` mixes user-editable columns (display name, avatar, ...) with
-- platform-controlled ones (subscription_tier, plan_limit_overrides, max_api_slots,
-- is_frozen, balance, ...). The row policies (`Update own profile`,
-- `profiles_owner_access`) only check *which row* the caller owns, and the
-- `authenticated` role holds table-wide UPDATE, so nothing constrained *which columns*.
-- Entitlement checks read exactly the platform columns.
--
-- FIX
-- A BEFORE INSERT/UPDATE/DELETE trigger that, for the browser-facing roles `anon` and
-- `authenticated` only, rejects any change outside an explicit ALLOWLIST of
-- user-editable columns. It is an allowlist on purpose: a column added to `profiles`
-- later is platform-controlled until someone deliberately lists it here.
-- Backend code that legitimately changes platform columns runs as `service_role` (or the
-- owning DB role) and is untouched.
--
-- Comparison is done on `to_jsonb(NEW) - allowed` vs `to_jsonb(OLD) - allowed`, so it
-- covers every column, including ones this file has never heard of.
--
-- Idempotent. Additive. Does not alter or remove any existing data or policy.

CREATE OR REPLACE FUNCTION public.guard_profiles_platform_columns()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    -- Columns a signed-in user may change on their OWN profile row.
    v_user_editable CONSTANT text[] := ARRAY[
        'username', 'display_name', 'avatar_url', 'bio', 'telegram_id',
        'full_name', 'preferred_currency', 'updated_at'
    ];
BEGIN
    -- SECURITY INVOKER: current_user is the role PostgREST switched to for the request.
    IF current_user NOT IN ('anon', 'authenticated') THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF (to_jsonb(NEW) - v_user_editable) IS DISTINCT FROM (to_jsonb(OLD) - v_user_editable) THEN
            RAISE EXCEPTION
                'public.profiles: platform-controlled columns cannot be modified by role %',
                current_user
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END IF;

    -- INSERT and DELETE of profile rows belong to the signup trigger / backend only.
    RAISE EXCEPTION
        'public.profiles: % is not permitted for role %', TG_OP, current_user
        USING ERRCODE = '42501';
END;
$$;

DROP TRIGGER IF EXISTS trg_guard_profiles_platform_columns ON public.profiles;
CREATE TRIGGER trg_guard_profiles_platform_columns
    BEFORE INSERT OR UPDATE OR DELETE ON public.profiles
    FOR EACH ROW EXECUTE FUNCTION public.guard_profiles_platform_columns();

-- Defence in depth: the browser roles never need to create or delete profile rows.
REVOKE INSERT, DELETE ON public.profiles FROM anon, authenticated;
