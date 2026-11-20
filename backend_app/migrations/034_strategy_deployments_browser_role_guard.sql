-- backend_app/migrations/034_strategy_deployments_browser_role_guard.sql
--
-- SEC-16 (P1). `strategy_deployments` carried an owner UPDATE policy with no WITH CHECK
-- and no column restriction, and `authenticated` holds table-wide UPDATE. A user holding
-- only the public anon key and their own JWT could PATCH their deployment row's `mode`
-- (paper -> live), `exchange_account_id`, `risk_config_id`, `execution_config` or
-- `dag_hash` directly through PostgREST, bypassing the API's entitlement and
-- deployment-binding gates (`deployment_binding`, LIVE_TRADING entitlement).
--
-- FIX (fail closed, for the browser-facing roles `anon` / `authenticated` only):
--   * UPDATE may change only lifecycle columns the backend itself writes through the
--     user-scoped client: status, started_at, stopped_at, error_message, updated_at.
--     (Allowlist, so columns added later are protected by default.)
--   * INSERT must be a PAPER deployment. A LIVE deployment row can only be created by a
--     trusted server-side role (service_role / owner), never directly by a browser role.
--   * DELETE is not permitted for these roles.
-- `service_role` and the owning database role are unaffected.
--
-- FOLLOW-UP (tracked with RT-02 / RT-15): the API's own LIVE deployment insert currently
-- runs under the user's JWT and will now be refused by this guard. That is intended
-- (live trading is disabled for launch); enabling live deploys requires moving that one
-- insert to a service-role client AFTER the server-side entitlement and live-gate checks.

CREATE OR REPLACE FUNCTION public.guard_strategy_deployments_browser_roles()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_user_updatable CONSTANT text[] := ARRAY[
        'status', 'started_at', 'stopped_at', 'error_message', 'updated_at'
    ];
BEGIN
    IF current_user NOT IN ('anon', 'authenticated') THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF (to_jsonb(NEW) - v_user_updatable) IS DISTINCT FROM (to_jsonb(OLD) - v_user_updatable) THEN
            RAISE EXCEPTION
                'public.strategy_deployments: only lifecycle columns may be updated by role %',
                current_user
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF COALESCE(NEW.mode, 'paper') <> 'paper' THEN
            RAISE EXCEPTION
                'public.strategy_deployments: role % may only create paper deployments',
                current_user
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'public.strategy_deployments: % is not permitted for role %', TG_OP, current_user
        USING ERRCODE = '42501';
END;
$$;

DROP TRIGGER IF EXISTS trg_guard_strategy_deployments_browser_roles ON public.strategy_deployments;
CREATE TRIGGER trg_guard_strategy_deployments_browser_roles
    BEFORE INSERT OR UPDATE OR DELETE ON public.strategy_deployments
    FOR EACH ROW EXECUTE FUNCTION public.guard_strategy_deployments_browser_roles();
