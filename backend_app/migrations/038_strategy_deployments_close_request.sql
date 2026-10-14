-- backend_app/migrations/038_strategy_deployments_close_request.sql
--
-- RT-21 (P0). Cancel and cancel-all (commit 2644850) reach a resting order directly at
-- the venue -- no deployment process needs to be running. Flattening an OPEN POSITION is
-- different: a BotRunner-deployed bot tracks its own exposure in process memory
-- (`BotRunner.recovered_exposure_coins`, settled only from a real fill -- see
-- `settle_exposure` in `backend/master_executor.py`), with no second, externally-readable
-- copy. The only process that can safely know whether a close would be correct, and by how
-- much, is the one already running that bot -- the same reason `request_stop` /
-- `_honour_stop_request` (migration-free, `deployment_state.py`) hands a STOP to the owning
-- process rather than guessing from outside it.
--
-- `close_requested_at` is that same durable signal for CLOSE. `strategy_deployments` has no
-- CHECK constraint on `status` (free text, see `deployment_state.py`'s own note), so this is
-- a column next to it, read and cleared by the owning bot on its own heartbeat tick
-- (`fleet_manager.FleetManager._close_requested` / `_honour_close_request`), identically to
-- how `status='stopping'` already is. It is NOT a status value itself: closing a position
-- does not stop the deployment, so overloading `status` would conflate two independent
-- lifecycle axes (running/stopped vs flat/holding).
--
-- Idempotent and additive. Migration 034's trigger allow-lists the columns a browser role
-- (anon/authenticated) may write on this table; `close_requested_at` is added to it here so
-- a user's own JWT can request a close the same way it already requests a stop, without that
-- guard needing to be relaxed for anything else.

ALTER TABLE public.strategy_deployments
    ADD COLUMN IF NOT EXISTS close_requested_at timestamptz;

CREATE OR REPLACE FUNCTION public.guard_strategy_deployments_browser_roles()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_user_updatable CONSTANT text[] := ARRAY[
        'status', 'started_at', 'stopped_at', 'error_message', 'updated_at',
        'close_requested_at'
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

-- Postflight: the allow-list actually grew, and the column exists, on any environment this
-- migration runs against -- not just the fresh-provision path.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'strategy_deployments'
           AND column_name = 'close_requested_at'
    ) THEN
        RAISE EXCEPTION '038 postflight FAILED: strategy_deployments.close_requested_at is missing.';
    END IF;

    IF (SELECT prosrc FROM pg_proc WHERE proname = 'guard_strategy_deployments_browser_roles')
       NOT LIKE '%close_requested_at%' THEN
        RAISE EXCEPTION '038 postflight FAILED: the browser-role guard was not updated to '
                        'allow close_requested_at.';
    END IF;
END
$$;
