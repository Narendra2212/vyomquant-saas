-- ═══════════════════════════════════════════════════════════════════════════
-- 065_version_ready01_promote_current_validated_drafts.sql
--
-- VERSION-READY-01. The builder's save wrote every version as a VALIDATED *draft*, and nothing ever
-- moved one on, while deploy (lifecycle READY), a Paper_Session (READY/DEPLOYED/RUNNING/PAUSED, never a
-- draft) and the Marketplace (non-draft versions only) all require the opposite. Production held six
-- versions, none READY. The save path is fixed in code (routers/strategies._persist_current_version);
-- this brings the rows it already wrote to the state it now writes.
--
-- NARROW ON PURPOSE. Only a version that is
--   * the strategy's CURRENT version,
--   * VALIDATED with validation_state VALID (so dag_hash and compiled_plan exist: chk_valid_requires_hash),
--   * still a draft, not read-only,
--   * and declares NO model block (compiled_plan.ml_nodes empty or absent -- a version with a model
--     block is not deployable until it has been trained, and that transition is the training worker's)
-- is promoted. graph_json, compiled_plan, dag_hash and schema_version are NOT touched, so migration
-- 004c's immutability trigger has nothing to reject. Idempotent: a second run matches nothing.
-- ═══════════════════════════════════════════════════════════════════════════

UPDATE public.strategy_versions
   SET lifecycle_state = 'READY',
       is_draft        = false
 WHERE is_current      = true
   AND is_draft        = true
   AND COALESCE(is_read_only, false) = false
   AND lifecycle_state = 'VALIDATED'
   AND validation_state = 'VALID'
   AND compiled_plan IS NOT NULL
   AND COALESCE(jsonb_array_length(compiled_plan -> 'ml_nodes'), 0) = 0;
