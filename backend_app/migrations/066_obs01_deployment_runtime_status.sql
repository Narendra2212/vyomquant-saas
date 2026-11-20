-- backend_app/migrations/066_obs01_deployment_runtime_status.sql
--
-- OBS-01 (P1). A running bot recorded nothing about its own progress. Its deployment row
-- said `running` and its WORK-03 claim heartbeat said the PROCESS was alive, but nothing
-- said whether market data was arriving, whether candles were closing, what the last
-- evaluation decided, or why the stream was failing. Found during the live testnet run:
-- a live bot whose condition held on every bar for eleven minutes produced no signal, no
-- refusal and no order, and the only evidence of why was a WARNING-level log line in a
-- container nobody could read from the product.
--
-- `runtime_status` is the bot's own self-report, written by the runtime under the SERVICE
-- role (`deployment_state.DeploymentStateStore.write_runtime_status`), throttled to about
-- one write per 30 seconds per bot plus one on every phase change. Shape (all optional):
--   {phase, updated_at, booted_at, last_tick_at, ticks, last_bar_closed_at, bars_closed,
--    last_evaluation_at, last_evaluation, refusals, last_error, last_error_at}
--
-- Additive and idempotent. Deliberately NOT added to migration 034/038's browser-role
-- allow-list: a user's JWT has no reason to write the runtime's self-report.

ALTER TABLE public.strategy_deployments
    ADD COLUMN IF NOT EXISTS runtime_status jsonb;

COMMENT ON COLUMN public.strategy_deployments.runtime_status IS
    'OBS-01: the running bot''s own progress report (phase, last tick, last closed bar, '
    'last evaluation, refusals, last error). Written by the runtime only.';
