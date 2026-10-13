-- backend_app/migrations/035_marketplace_entitlement_tables_service_only.sql
--
-- MKT-01 (P1). Any signed-in user could INSERT an `active`, free, 365-day row into
-- public.library_subscriptions for another author's paid listing straight through the
-- Supabase REST API, after which entitlement_resolver.resolve() answered SUBSCRIBED and
-- admitted paper sessions and deployments of that strategy.
--
-- ROOT CAUSE: the browser roles held table-wide write privileges (and RLS is either
-- disabled on the repo-provisioned schema or only checks row ownership), while the
-- transition guard only runs on UPDATE.
--
-- FIX: entitlement rows are written ONLY by backend code running as service_role / the
-- owning role (checkout, settlement, expiry sweep, admin reinstatement - all of which use
-- the service client). The browser roles keep SELECT. Two layers:
--   1. REVOKE INSERT/UPDATE/DELETE from anon, authenticated;
--   2. a BEFORE INSERT/UPDATE/DELETE trigger that refuses those roles even if a later
--      grant, default privilege or policy re-opens the table.
-- Idempotent; additive; touches no data.

CREATE OR REPLACE FUNCTION public.guard_entitlement_tables_service_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF current_user IN ('anon', 'authenticated') THEN
        RAISE EXCEPTION
            'public.%: % is reserved for the backend (role %)', TG_TABLE_NAME, TG_OP, current_user
            USING ERRCODE = '42501';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['library_subscriptions', 'deployment_permissions'] LOOP
        IF to_regclass('public.' || t) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format('REVOKE INSERT, UPDATE, DELETE ON public.%I FROM anon, authenticated', t);
        EXECUTE format('DROP TRIGGER IF EXISTS trg_guard_%I_service_only ON public.%I', t, t);
        EXECUTE format(
            'CREATE TRIGGER trg_guard_%I_service_only BEFORE INSERT OR UPDATE OR DELETE ON public.%I '
            'FOR EACH ROW EXECUTE FUNCTION public.guard_entitlement_tables_service_only()', t, t);
    END LOOP;
END
$$;
