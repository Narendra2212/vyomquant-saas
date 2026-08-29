# Pre-existing backend test failures

Baseline run (local dev machine, sqlite fallback, no Redis, no Supabase reachability):

```
.venv\Scripts\python.exe -m pytest tests/ --ignore=tests/full_system_test.py --ignore=tests/e2e \
  --ignore=tests/load_test_500_users.py --ignore=tests/chaos_test.py -q -rf
==== 40 failed, 1431 passed, 4 skipped, 1217 warnings in 119.28s (0:01:59) ====
```

## Headline

The strategy-builder baseline note describes all 40 failures as infrastructure-dependent. That is wrong for 27 of them.

| Category | Count |
|---|---|
| A — missing local infrastructure (would pass in CI) | 13 |
| B — test/code drift (inert: testing nothing) | 23 |
| C — assertion failure on real logic (genuine defect) | 3 |
| D — blocked by a live guardrail | 1 |

Two findings matter beyond the test suite:

1. **The distributed-execution HA subsystem does not import.** All 7 tests in `tests/test_distributed_execution_safety.py` fail at patch/collection time, so leader election, split-brain prevention, lease exclusivity, orphan replay safety, heartbeat recovery, deterministic reassignment and failover double-leader prevention are **not being tested at all**. The cause is production code, not the tests:
   - `lease_manager.py` — `TypeError: non-default argument 'resource_id' follows default argument` (`LeaseRequest` declares `lease_id: Optional[str] = None` before `resource_id: str`). Module is unimportable.
   - `orchestration_safety_guarantees` imports fine but has no `OperationIsolationGuarantee`, which breaks `heartbeat_manager` and, transitively, `leader_election_manager`, `orphan_recovery_manager`, `failover_coordinator`, `standby_coordinator`.
   - `worker_registry` — `ModuleNotFoundError: No module named 'backend_app.backend.distributed_execution.deterministic_reassignment_model'`.
   - `orchestration_safety_guarantees` has no `redis_manager` attribute (patch target gone).
2. **Reconciliation is fail-open on local positions.** `ReconciliationWorker._fetch_local_positions` returns `{}` on any exception (`reconciliation_worker.py:549`) while `_fetch_local_orders` correctly re-raises. Unavailable local state therefore reads as "no open positions", which can drive spurious corrective actions.

## Failure inventory

### A — Missing local infrastructure (13)

Expected locally; these exercise PostgreSQL-only SQL, Supabase, or Redis. Not code defects.

| Node id | Observed error |
|---|---|
| `test_atomic_order_cancellation_fix.py::test_atomic_order_cancellation` | `(sqlite3.OperationalError) no such table: orders` |
| `test_atomic_order_cancellation_fix.py::test_concurrent_order_cancellation` | `(sqlite3.OperationalError) no such table: orders` |
| `test_atomic_order_cancellation_fix.py::test_transaction_isolation_for_cancellation` | `(sqlite3.OperationalError) near "SHOW": syntax error` |
| `test_atomic_order_cancellation_fix.py::test_order_cancellation_idempotency` | `(sqlite3.OperationalError) no such table: orders` |
| `test_end_to_end_api_suite.py::test_user_profile_update` | `httpx.ConnectError: [Errno 11001] getaddrinfo failed` |
| `test_end_to_end_api_suite.py::test_market_symbols` | `assert 401 in (200, 404)`, preceded by `getaddrinfo failed` and `DEV_MODE: profile query failed ... returning fallback profile` |
| `test_end_to_end_api_suite.py::test_admin_users_accessible_for_admin` | `httpx.ConnectError: [Errno 11001] getaddrinfo failed` |
| `test_exchange_vault_singleton.py::test_list_exchanges_response_shape_and_no_per_request_vault_init` | `AssertionError: Expected 'get_user_tier' to be called once. Called 0 times.` with `Failed to connect: Error 22 connecting to localhost:6379` and `Failed to fetch exchange keys: [Errno 11001] getaddrinfo failed` |
| `test_exchange_vault_singleton.py::test_concurrent_tenant_isolation_subscription_tiers` | `AssertionError: get_user_tier('tenant_a_111') call not found` with 10x `Failed to fetch exchange keys: [Errno 11001] getaddrinfo failed` |
| `test_position_delta_race_condition_fix.py::test_position_update_row_level_locking` | `(sqlite3.OperationalError) near "FOR": syntax error` on `SELECT * FROM positions WHERE tenant_id = ? AND symbol = ? FOR UPDATE` |
| `test_transaction_isolation_serializable.py::test_transaction_isolation_level_serializable` | `(sqlite3.OperationalError) near "SHOW": syntax error` |
| `test_transaction_isolation_serializable.py::test_pool_transaction_isolation_level` | `(sqlite3.OperationalError) near "SHOW": syntax error` |
| `test_transaction_isolation_serializable.py::test_concurrent_transaction_safety` | `Concurrent transactions should not fail: ... [SQL: SHOW transaction_isolation] ... assert 5 == 0` |

Confidence caveat: the two `test_exchange_vault_singleton` cases are attributed from the captured log (Supabase/Redis unreachable, handler bails before reaching `get_user_tier`) rather than from a direct infra error at the assertion site. If the endpoint no longer calls `get_user_tier` at all, these are actually category B. Re-check against PostgreSQL/Supabase before trusting them.

### B — Test/code drift: currently inert (23)

| Node id | Observed error | What is consequently untested |
|---|---|---|
| `test_distributed_execution_safety.py::test_leader_election_prevents_split_brain` | `AttributeError: module 'backend_app.backend.distributed_execution' has no attribute 'leader_election_manager'` | Split-brain prevention / fencing tokens |
| `test_distributed_execution_safety.py::test_orphan_detection_preserves_replay_safety` | `AttributeError: ... has no attribute 'orphan_recovery_manager'` | Orphan detection and replay safety |
| `test_distributed_execution_safety.py::test_lease_prevents_concurrent_execution` | `TypeError: non-default argument 'resource_id' follows default argument` | Lease exclusivity / expiry preventing concurrent execution |
| `test_distributed_execution_safety.py::test_heartbeat_detection_triggers_recovery` | `AttributeError: ... has no attribute 'heartbeat_manager'` | Heartbeat-triggered recovery |
| `test_distributed_execution_safety.py::test_deterministic_reassignment_prevents_races` | `AttributeError: ... has no attribute 'worker_registry'` | Deterministic task reassignment |
| `test_distributed_execution_safety.py::test_transaction_atomicity_guarantee` | `AttributeError: <module '...orchestration_safety_guarantees'> does not have the attribute 'redis_manager'` | Distributed transaction atomicity |
| `test_distributed_execution_safety.py::test_failover_coordinator_prevents_double_leader` | `AttributeError: ... has no attribute 'failover_coordinator'` | Failover double-leader prevention |
| `test_event_pipeline.py::test_producer_initialization` | `Failed: async def functions are not natively supported.` | Event producer construction |
| `test_event_pipeline.py::test_consumer_initialization` | `Failed: async def functions are not natively supported.` | Event consumer construction |
| `test_event_pipeline.py::test_pipeline_initialization` | `Failed: async def functions are not natively supported.` | Event pipeline wiring |
| `test_exchange_safety_fix.py::test_retry_with_exponential_backoff` | `TypeError: OrderWatchdog.__init__() got an unexpected keyword argument 'max_retries'` (actual signature: `(db_session, config: Optional[WatchdogConfig] = None)`) | Exponential backoff on exchange retries; retry-storm protection |
| `test_false_success_report_fix.py::TestExchangeStatus::test_exchange_status_is_available_not_active` | `assert 401 == 200` — `GET /api/exchanges/supported` now requires auth (`user: dict = Depends(get_current_user)`), test sends no token | That exchange status reports `available` rather than a hardcoded `active` |
| `test_get_db_dependency.py::TestGetDbDependency::test_user_billing_plan_with_real_db_dependency` | `TypeError: argument of type 'int' is not iterable`; `--showlocals` shows `entitlements_data = 0`. The test patches `subscription_engine.redis_manager.get` to return `"0"`, and that is the same singleton as `core.cache.redis_manager.redis_manager`, so the entitlements handler treats `"0"` as a cache hit and returns `json.loads("0")` | Real-`get_db` billing entitlements shape (plan/features/quotas/usage) |
| `test_marketplace_pipeline.py::TestGetAdminUserP01Regression::test_admin_via_app_metadata_allowed` | `RuntimeError: There is no current event loop in thread 'MainThread'` — helper at line 378 uses `asyncio.get_event_loop().run_until_complete(...)` | Admin recognised via `app_metadata` |
| `test_marketplace_pipeline.py::TestGetAdminUserP01Regression::test_admin_via_user_metadata_allowed` | same | Admin recognised via `user_metadata` |
| `test_marketplace_pipeline.py::TestGetAdminUserP01Regression::test_non_admin_rejected_with_403` | same | Non-admin rejection (privilege escalation guard) |
| `test_marketplace_pipeline.py::TestGetAdminUserP01Regression::test_missing_metadata_rejected_gracefully` | same | Graceful rejection when metadata absent |
| `test_redis_debounce.py::test_thundering_herd_one_attempt[trio]` | `RuntimeError: There is no current event loop in thread 'MainThread'` | Nothing extra — the `[asyncio]` variant passes; only the trio backend is unsupported |
| `test_redis_debounce.py::test_get_redis_manager_debounced[trio]` | same | Nothing extra — `[asyncio]` variant passes |
| `test_strategy_analysis_endpoint_accuracy.py::TestSignalReplayEndpointAccuracy::test_signal_replay_audit_retrieval_functionality` | `TypeError: object MagicMock can't be used in 'await' expression` -> `HTTPException 500 SIGNAL_RETRIEVAL_FAILED` (needs `AsyncMock`) | Signal replay audit retrieval |
| `test_strategy_analysis_endpoint_accuracy.py::TestSignalReplayEndpointAccuracy::test_signal_replay_execution_records_fallback` | same | Execution-records fallback path |
| `test_strategy_analysis_endpoint_accuracy.py::TestSignalReplayEndpointAccuracy::test_signal_replay_honest_limitation_documentation` | same | That the endpoint declares its own limitations |
| `test_tenant_isolation_fixes.py::TestStrategyOperationsTenantIsolation::test_delete_strategy_owner_can_delete` | `TypeError: '<' not supported between instances of 'AsyncMock' and 'int'` at `subscription_engine.py:333`, via `strategies.py:1091 delete_strategy` | Owner-can-delete positive path. The negative isolation paths in the same file and all of `test_tenant_isolation_strategy_clone.py` pass |

Note on ordering: the four `TestGetAdminUserP01Regression` cases pass when `test_marketplace_pipeline.py` runs alone (a different test, `test_13_unpublish_removes_from_pool`, fails instead). They only fail in a full-suite run, so the file has an inter-test event-loop dependency in both directions.

### C — Assertion failure on real logic (3)

| Node id | Observed error | What is consequently untested / broken |
|---|---|---|
| `test_pricing_tier_reconciliation.py::TestPricingTierReconciliation::test_entitlement_engine_plan_mapping_reconciliation` | `AssertionError: assert 'pro' == 'pro_999'` at line 69 (`PlanMapper.tenant_to_billing(TenantPlan.PROFESSIONAL)`) | Billing-key round-trip. `billing_to_tenant("pro_999")` works, so the mapping is asymmetric: anything persisting `tenant_to_billing` output writes a key the billing side does not recognise |
| `test_pricing_tier_reconciliation.py::TestPricingTierReconciliation::test_feature_entitlement_matrix_reconciliation` | `AssertionError: assert not True` at line 108 — `FeatureEntitlements.is_feature_available(FeatureFlag.ML_TRAINING, TenantPlan.PROFESSIONAL)` returns `True` | The entitlement matrix grants ML training to Professional where the test expects Enterprise-only. Either a revenue leak or a stale test; needs a product decision |
| `test_reconciliation_engine_fail_closed.py::test_reconciliation_worker_rejects_unavailable_local_state` | `Failed: DID NOT RAISE RuntimeError` at line 75, with `Failed to fetch local orders: Redis connection is required but not connected` | Fail-closed on local **positions**. `_fetch_local_positions` swallows and returns `{}` (`reconciliation_worker.py:547-549`); `_fetch_local_orders` re-raises correctly. Unavailable local state is indistinguishable from "no positions" |

### D — Blocked by a live guardrail (1)

| Node id | Observed error | What is consequently untested |
|---|---|---|
| `test_fee_precision_fix.py::test_execution_engine_fee_precision` | `Failed: Failed to open position: RISK GUARDRAIL BLOCKED: MAX POSITION SIZE EXCEEDED \| Position: $50022.57 \| Max allowed: $10000.00 (10% of $100000.00)` | Fee rounding / decimal precision in the execution engine. The guardrail is working; the test's fixture sizes a position at ~50% of equity against a 10% cap, so the assertion is never reached |

## Safety-relevant summary

| Group | Category | Genuinely inert, or infra-blocked? |
|---|---|---|
| Distributed execution (7 tests) | B | **Genuinely inert.** Would also fail in CI — five production modules are unimportable |
| Reconciliation fail-closed | C | **Real defect**, reproducible anywhere |
| Tenant isolation — `test_delete_strategy_owner_can_delete` | B | **Genuinely inert**, but low exposure: it is the positive (owner-allowed) path; the deny paths pass |
| Marketplace admin gate (4 tests, incl. non-admin 403) | B | **Genuinely inert in full-suite order**; passes in isolation, so coverage is order-dependent and unreliable |
| Atomic order cancellation (4 tests) | A | Infra-blocked. Would run against PostgreSQL in CI |
| Position-delta race condition | A | Infra-blocked (`FOR UPDATE` needs PostgreSQL). Would run in CI |
| Transaction isolation SERIALIZABLE (3 tests) | A | Infra-blocked (`SHOW transaction_isolation`). Would run in CI |
| Tenant isolation — `test_concurrent_tenant_isolation_subscription_tiers` | A (see caveat) | Probably infra-blocked; verify against Supabase |
| Fee precision | D | Not inert, but never reaches its assertion. Fee rounding is unverified |

## Recommended priority

1. Make `backend_app/backend/distributed_execution` importable. Fix the `LeaseRequest` field order, restore or rename `OperationIsolationGuarantee`, and resolve the missing `deterministic_reassignment_model`. Until then the HA safety story is unverified.
2. Fix `_fetch_local_positions` to re-raise instead of returning `{}`, matching `_fetch_local_orders`.
3. Decide whether `ML_TRAINING` on Professional is intended, then fix either the matrix or the test. Fix the `tenant_to_billing` / `billing_to_tenant` asymmetry.
4. Resize the `test_fee_precision_fix` fixture below the 10% position cap, or raise equity in the fixture, so fee precision is actually asserted.
5. Replace `asyncio.get_event_loop().run_until_complete` in `test_marketplace_pipeline.py:378` with `asyncio.run`, and find the test that leaves the loop policy dirty.
6. Low-effort one-liners: add the async marker to `test_event_pipeline.py`; swap `MagicMock` for `AsyncMock` in `test_strategy_analysis_endpoint_accuracy.py` and `test_tenant_isolation_fixes.py`; update the `OrderWatchdog(...)` call to the `WatchdogConfig` signature; add an auth override to `test_false_success_report_fix.py`; narrow the `redis_manager.get` patch in `test_get_db_dependency.py`; restrict the `redis_debounce` anyio backend to asyncio.
7. Category A stays as-is locally. Confirm it goes green in a CI job with PostgreSQL, Redis and Supabase before treating those 13 as clean.

No test or source file was modified while producing this record.
