# Safe Delete Analysis — Phase 1

> [!CAUTION]
> This report is analysis only. No files have been deleted.

## DELETE_CANDIDATE Files

| File | Reason |
|---|---|
| `certify_exchange_sandbox.py` | DELETE_CANDIDATE — dev/ops utility script |
| `check_enum.py` | DELETE_CANDIDATE — dev/ops utility script |
| `check_indexes.py` | DELETE_CANDIDATE — dev/ops utility script |
| `check_positions.py` | DELETE_CANDIDATE — dev/ops utility script |
| `check_tables.py` | DELETE_CANDIDATE — dev/ops utility script |
| `disable.py` | DELETE_CANDIDATE — stub/placeholder file |
| `fix_auth.py` | DELETE_CANDIDATE — dev/ops utility script |
| `fix_enum_case.py` | DELETE_CANDIDATE — dev/ops utility script |
| `fix_sql.py` | DELETE_CANDIDATE — dev/ops utility script |
| `inspect_db.py` | DELETE_CANDIDATE — dev/ops utility script |
| `stripe_test.py` | DELETE_CANDIDATE — dev/ops utility script |
| `test_backtest_comparison.py` | DELETE_CANDIDATE — test script |
| `test_burn_in.py` | DELETE_CANDIDATE — test script |
| `test_cancellation.py` | DELETE_CANDIDATE — test script |
| `test_complete_fill.py` | DELETE_CANDIDATE — test script |
| `test_db_schema.py` | DELETE_CANDIDATE — test script |
| `test_duplicate_fill_attack.py` | DELETE_CANDIDATE — test script |
| `test_feature_engineering.py` | DELETE_CANDIDATE — test script |
| `test_full_system_strict.py` | DELETE_CANDIDATE — test script |
| `test_inference.py` | DELETE_CANDIDATE — test script |
| `test_integration.py` | DELETE_CANDIDATE — test script |
| `test_large_backtest.py` | DELETE_CANDIDATE — test script |
| `test_large_backtest_fast.py` | DELETE_CANDIDATE — test script |
| `test_live_ml.py` | DELETE_CANDIDATE — test script |
| `test_ml_pipeline.py` | DELETE_CANDIDATE — test script |
| `test_ml_target.py` | DELETE_CANDIDATE — test script |
| `test_ml_training.py` | DELETE_CANDIDATE — test script |
| `test_partial_fill.py` | DELETE_CANDIDATE — test script |
| `test_reconciliation_end_to_end.py` | DELETE_CANDIDATE — test script |
| `test_replay_reconstruction.py` | DELETE_CANDIDATE — test script |
| `test_requests.py` | DELETE_CANDIDATE — test script |
| `test_retry_submission.py` | DELETE_CANDIDATE — test script |
| `test_risk_guardrails.py` | DELETE_CANDIDATE — test script |
| `test_sandbox.py` | DELETE_CANDIDATE — test script |
| `test_strategy_sdk.py` | DELETE_CANDIDATE — test script |
| `test_supabase.py` | DELETE_CANDIDATE — test script |
| `test_system.py` | DELETE_CANDIDATE — test script |
| `test_train_test_split.py` | DELETE_CANDIDATE — test script |
| `test_walk_forward.py` | DELETE_CANDIDATE — test script |
| `test_websocket_disconnect.py` | DELETE_CANDIDATE — test script |
| `test_ws.py` | DELETE_CANDIDATE — test script |
| `validate_financials.py` | DELETE_CANDIDATE — dev/ops utility script |
| `validate_paper_trading.py` | DELETE_CANDIDATE — dev/ops utility script |
| `validate_security.py` | DELETE_CANDIDATE — dev/ops utility script |
| `validate_soak_test.py` | DELETE_CANDIDATE — dev/ops utility script |
| `validate_stress_test.py` | DELETE_CANDIDATE — dev/ops utility script |
| `verify_all_fixes.py` | DELETE_CANDIDATE — dev/ops utility script |
| `verify_fix3_risk_trip.py` | DELETE_CANDIDATE — dev/ops utility script |
| `verify_fix4_stripe.py` | DELETE_CANDIDATE — dev/ops utility script |
| `verify_observability_phase1.py` | DELETE_CANDIDATE — dev/ops utility script |

## REVIEW_REQUIRED Files

| File | Reason |
|---|---|
| `cto_evidence_check.py` | REVIEW_REQUIRED — not obviously referenced |
| `detail_check.py` | REVIEW_REQUIRED — not obviously referenced |
| `execution_safety_layer.py` | REVIEW_REQUIRED — not obviously referenced |
| `find_duplicates.py` | REVIEW_REQUIRED — not obviously referenced |
| `introspect_models.py` | REVIEW_REQUIRED — not obviously referenced |
| `model_registry.py` | REVIEW_REQUIRED — not obviously referenced |
| `schema_inspector.py` | REVIEW_REQUIRED — not obviously referenced |
| `strategy_validator.py` | REVIEW_REQUIRED — not obviously referenced |
| `ws_smoke_test.py` | REVIEW_REQUIRED — not obviously referenced |

## KEEP Files

| File | Reason |
|---|---|
| `api_test.py` | KEEP — referenced or startup-critical |
| `beta_certification_runner.py` | KEEP — referenced or startup-critical |
| `burn_in_runner.py` | KEEP — referenced or startup-critical |
| `certify_runtime.py` | KEEP — referenced or startup-critical |
| `connection_engine.py` | KEEP — referenced or startup-critical |
| `data_seeking_engine.py` | KEEP — referenced or startup-critical |
| `example_strategy.py` | KEEP — referenced or startup-critical |
| `live_trading_certification.py` | KEEP — referenced or startup-critical |
| `migrate_db.py` | KEEP — referenced or startup-critical |
| `order_execution_engine.py` | KEEP — referenced or startup-critical |
| `patch_missing_routes.py` | KEEP — referenced or startup-critical |
| `patch_vault.py` | KEEP — referenced or startup-critical |
| `strategy_monitor_api.py` | KEEP — referenced or startup-critical |
| `strategy_monitor_service.py` | KEEP — referenced or startup-critical |
| `strategy_sdk.py` | KEEP — referenced or startup-critical |
| `test_api.py` | KEEP — referenced or startup-critical |
| `test_api2.py` | KEEP — referenced or startup-critical |
| `test_capital_logic.py` | KEEP — referenced or startup-critical |
| `test_model_saving.py` | KEEP — referenced or startup-critical |
| `test_worker_restart_recovery.py` | KEEP — referenced or startup-critical |
| `verify_runtime_services.py` | KEEP — referenced or startup-critical |
