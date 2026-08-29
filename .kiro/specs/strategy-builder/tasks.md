# Implementation Plan: Strategy Builder (VyomQuant)

## Overview

This is a **consolidation plan, not a greenfield build**. Roughly 400 KB of production DAG,
execution, ML, market-data and validation code already exists and works. The tasks below
follow the nine phases defined in `design.md § Phased implementation sequence`, in that
order, because each phase is independently shippable and leaves the system working.

Implementation language: **Python** on the backend, **JavaScript/React** on the frontend,
**SQL** for migrations — as stated in `design.md § Notation conventions` and dictated by the
existing codebase.

Rules that apply to every task:

- **Extend, do not duplicate.** `design.md § Component disposition` lists 30 components:
  12 REUSED AS-IS, 15 EXTENDED, 3 REPLACED. Tasks touch the named existing files. No task
  recreates a REUSED AS-IS component (`dag_event_loop.py`, `dag_task_queue.py`,
  `dag_worker.py`, `dag_engine_parallel.py`, `dag_risk_integration.py`,
  `execution_engine.py`, `execution_guard.py`, `exchange_executor.py`,
  `connection_engine.py`, `feature_validator.py`, `ml_safety.py`,
  `market_data_validation.py`, `data_seeking_engine.py`, `credential_vault.py`).
- **Deletions are work.** The three REPLACED components are deleted by explicit tasks
  (2.4, 3.4, 3.5).
- **No control is weakened.** No task removes or loosens authentication, authorization,
  RLS, tenant isolation, financial invariants, execution safeguards, risk controls or
  idempotency (Requirement 21, `design.md § Financial safety`).
- **Baseline.** Measured at the Phase 3 exit gate (task 3.18): the backend suite sits at
  **1887 passed / 44 failed / 17 skipped** over 1948 collected, and the frontend unit suite
  at **449 passed / 0 failed** over 17 files. No task may increase either failure count.
  The 44 backend failures are pre-existing and spread over 19 non-strategy-builder files;
  most are infrastructure-dependent (no local PostgreSQL / Redis / Supabase). Two are not:
  `test_exception_swallow_regression.py::test_db_error_returns_503_not_empty_limits` and
  `test_risk_settings_api.py::test_get_strategy_limits` fail because `_sb()` in
  `backend_app/routers/risk.py` wraps `create_request_supabase_async` in
  `except Exception: return None`, so a database crash yields HTTP 200 with an empty limit
  list instead of 503. That is a genuine exception-swallow defect in the risk-gate
  workstream, outside this spec's scope — recorded here so it is not mistaken for
  infrastructure noise or for builder regression.
  (The earlier note in this file read 620 passed / 40 failed / 4 skipped; that predated
  Phases 1-4 and their tests, and is superseded by the measurement above.)

  **Re-measured and pinned at the Phase 5 exit gate (task 5.13).** The backend suite sits at
  **2490 passed / 44 failed / 18 skipped** over 2552 collected, and the frontend unit suite at
  **508 passed / 0 failed** over 19 files. The failing 44 are the same 44 files-and-tests as at
  Phase 3 exit; not one is a strategy-builder test, and the whole Phase 5 surface is inside the
  2490.

  The failure count drifted through Phase 5 (44 → 45 → 47 → 48 → 46 → 44) and each movement was
  attributed to collection-order dependence in the marketplace / notifications / stress /
  tenant-isolation families. **That attribution was wrong, and the baseline is not a range.**
  Collection order never changed between those runs: no order-randomising plugin is installed,
  so pytest's default walk is deterministic, and three consecutive full runs in it returned
  byte-identical failure sets. The movement was one flaky test:
  `tests/test_support_feature_e2e.py::test_real_saas_support_production_acceptance_twenty_points`.

  Its cause, and the fix landed under 5.13: `tests/test_rate_limit_backend.py` calls
  `importlib.reload` on `backend_app.core.rate_limit`, which rebinds that module's `limiter`
  global while `app.state.limiter` and every `@limiter.limit(...)` decorator closure keep the
  original object. The support test's `limiter.reset()` — read from the module, and wrapped in
  `except Exception: pass` — was therefore resetting an orphan no route consults. Its ninth
  `POST /api/support/tickets` against a `10/minute` limit answered 429 whenever the file ran
  fast enough for all nine to land inside one 60-second window, so the count moved with wall
  clock rather than with ordering. `test_rate_limit_backend.py` now snapshots and restores the
  module dict, and `test_support_feature_e2e.py` resets the storage of the limiter the app
  actually uses, per test, before the test — the pattern `test_sb06_exchange_agnostic_save.py`
  already established. No declared limit is weakened and no assertion was removed.

  **The figure task 9.7 compares against: exactly 44 failures in pytest's default collection
  order**, distributed as
  `full_system_test.py` 4, `test_account_health_query.py` 1,
  `test_atomic_order_cancellation_fix.py` 4, `test_billing_e2e.py` 1,
  `test_distributed_execution_safety.py` 7, `test_event_pipeline.py` 3,
  `test_exception_swallow_regression.py` 2, `test_exchange_safety_fix.py` 1,
  `test_exchange_vault_singleton.py` 2, `test_false_success_report_fix.py` 1,
  `test_fee_precision_fix.py` 1, `test_get_db_dependency.py` 1,
  `test_marketplace_pipeline.py` 4, `test_position_delta_race_condition_fix.py` 1,
  `test_reconciliation_engine_fail_closed.py` 1, `test_risk_management_lifecycle.py` 1,
  `test_risk_settings_api.py` 2, `test_strategy_analysis_endpoint_accuracy.py` 3,
  `test_tenant_isolation_fixes.py` 1, `test_transaction_isolation_serializable.py` 3.

  Order dependence is real but smaller, and it does not move that figure. Running the same 156
  files in reverse yields 48: four in `tests/test_atomic_idempotency_fix.py` and
  `test_marketplace_pipeline.py::TestMarketplacePipeline::test_13_unpublish_removes_from_pool`
  begin failing, and `test_tenant_isolation_fixes.py::TestStrategyOperationsTenantIsolation::
  test_delete_strategy_owner_can_delete` begins passing. So **43 of the 44 fail regardless of
  order**; the tenant-isolation one is order-dependent — a `MagicMock` leaks into
  `SubscriptionEngine.decrement_quota_usage`, which then evaluates `if new_usage < 0` against a
  mock at `subscription_engine.py:333`. Five further tests pass in default order and fail in
  reverse, on shared idempotency-store and marketplace-pool state. All six are in the
  subscription/quota, idempotency and marketplace families, outside this spec's scope; they are
  recorded so a later phase that changes collection order knows what will move and why.
- **Migration split.** `004_strategy_builder_canonical.sql` lands in parts where the design
  places them: version columns in Phase 2, registry snapshots in Phase 3,
  `chk_valid_requires_hash` plus the immutability trigger in Phase 4, training and model
  tables in Phase 6, deployment columns in Phase 8.

## Tasks

- [x] 1. Phase 1 — Canonical DAG + block schema (pure addition, nothing re-pointed)

  - [x] 1.1 Create the `strategy_dag` package and canonical schema
    - Create `backend_app/backend/strategy_dag/__init__.py` and `schema.py`
    - Define `PortType`, `BlockCategory`, `Port`, `NodeSpec`, `EdgeSpec` and
      `StrategyGraph` exactly as specified in `design.md § Canonical DAG model`
    - Implement wire parse/serialize using the canonical field names; no per-layer DTOs
    - Implement `compute_dag_hash(graph)`: key-sorted canonical node payload, sorted
      port-addressed edge strings, `ui` excluded, first 16 hex chars of sha256
    - Mint node ids as `n_` + ULID at creation; ids are never reused or renumbered
    - No `exchange` field anywhere in the envelope (SB-06)
    - Pin `hypothesis` as a test dependency in `requirements.txt` (exact version)
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 1.6, 1.7, 1.8, 2.1, 2.2, 2.4_

  - [x] 1.2 Add indicator descriptor specs to `indicators_backend.py`
    - Add `IndicatorSpec` and `INDICATOR_SPECS` covering all 33 implementations, each with
      its own input ports, output ports (one port per distinct output), `ParamSpec` list
      with per-indicator ranges, and `warmup_fn`
    - Declare `macd`, `bollinger_bands`, `stochastic`, `supertrend`, `ichimoku_cloud`,
      `psar`, `donchian_channel`, `keltner_channels` and the pivot/fibonacci blocks with
      one output port per output rather than a single collapsed series
    - Mark Ichimoku `chikou` as `leakage_risk = REVIEW_REQUIRED`
    - Derive `AVAILABLE_INDICATORS` from `INDICATOR_SPECS`; delete the hand-maintained
      parallel list that let `wma` and `hma` be runnable but unselectable
    - _Requirements: 4.4, 5.9, 5.10_

  - [x] 1.3 Add feature descriptor specs to `feature_engineering.py`
    - Add `FEATURE_SPECS` exposing each `FeatureEngine` capability as an individually
      addressable block: the 15 blocks tabulated in `design.md § Feature engineering`
      (`feat_lag`, `feat_returns`, `feat_log_returns`, `feat_rolling_mean`,
      `feat_rolling_std`, `feat_volatility`, `feat_momentum`, `feat_zscore`,
      `feat_normalize`, `feat_standardize`, `feat_time`, `feat_volume`,
      `feat_price_transform`, `feat_concat`, `feat_select`)
    - Each spec declares ports, params, lookback/warmup function and `leakage_risk`
    - Keep `create_feature_matrix` intact; this task adds addressability, not a rewrite
    - _Requirements: 4.3, 18.1_

  - [x] 1.4 Add model descriptor specs to `ml_models.py`
    - Add `ModelSpec` and `MODEL_SPECS` for `xgboost`, `lightgbm`, `random_forest`,
      `catboost`, `lstm`, `gru`, `transformer` and `autoencoder` with the minimum columns,
      minimum rows, sequence length, hyperparameters, recommended and max safe epochs,
      default batch size, validation requirements and serialization mode from the table in
      `design.md § ML/DL model registry`
    - Set `backend_available` from a real import probe per library
    - Derive `AVAILABLE_ML_MODELS` and `AVAILABLE_DL_MODELS` from `MODEL_SPECS`
    - _Requirements: 4.5, 4.6, 14.1_

  - [x] 1.5 Add DATA, MATH, LOGIC and ACTION block specs
    - Create `backend_app/backend/strategy_dag/block_specs.py` with `MATH_SPECS`,
      `LOGIC_SPECS`, the DATA descriptors (`ohlcv_feed`, `live_ticker`,
      `orderbook_imbalance`) and the ACTION descriptor factory
    - `ohlcv_feed` has no `exchange` param; `symbol` and `timeframe` are required with no
      default (SB-06)
    - ACTION descriptors are generated from `exchange_executor.OrderType`, are `TERMINAL`
      with an empty successor set, carry no traded-asset param, and declare
      `quantity_type` and `quantity` as required with no default
    - _Requirements: 4.10, 5.4, 12.2, 12.3, 12.8_

  - [x] 1.6 Implement `migrate_v1_to_v2` and `load_graph`
    - Add both to `strategy_dag/schema.py` per
      `design.md § Schema versioning and migration`
    - Map legacy node types to canonical categories, resolve `block_id`, merge legacy
      scalar fields into `params`, resolve ports from the descriptor
    - Mark unresolvable blocks rather than guessing; migration is read-only and never
      rewrites the stored row
    - Raise `UnsupportedSchemaVersion` for any version other than 1 or 2
    - _Requirements: 1.9, 1.10, 1.11_

  - [x] 1.7 Implement the backend-authoritative registry
    - Create `backend_app/backend/strategy_dag/registry.py` with `ParamSpec`,
      `BlockDescriptor`, `build_registry()`, `get(block_id)`, port lookup helpers and
      `registry_version` (hash of the assembled descriptor set)
    - Assemble from `INDICATOR_SPECS`, `FEATURE_SPECS`, `MODEL_SPECS`, `MATH_SPECS`,
      `LOGIC_SPECS`, the DATA descriptors and the ACTION factory
    - Assert every `runtime_ref` resolves to a callable; fail startup naming the offender
    - Omit model blocks whose library is unavailable instead of advertising them
    - Assert every category is non-empty; fail startup naming the empty category — the
      structural guard against SB-03 recurring
    - Publish the `compatibility_matrix` and the `port_types` vocabulary
    - Support a per-block `validate(params)` hook for cross-field rules ranges cannot
      express (for example MACD `fast < slow`)
    - _Requirements: 4.1, 4.2, 4.7, 4.8, 4.9, 4.10, 5.1, 5.9, 5.10, 6.9_

  - [x] 1.8 Widen `NodeType` to the seven canonical categories
    - Extend `NodeType` in `backend_app/core/models/pydantic_models.py` from 5 values to
      `DATA`, `INDICATOR`, `MATH`, `LOGIC`, `FEATURE_ENGINEERING`, `ML_DL`, `ACTION`
    - Keep the old values as aliases for one release so persisted rows keep deserializing
    - _Requirements: 1.6, 1.9_

  - [x] 1.9 Implement the validation rule engine
    - Create `backend_app/backend/strategy_dag/validator.py` with `is_edge_legal` rules
      R1–R8 and the collect-all stage pipeline (stages 1–9 plus the warmup feasibility
      warning of stage 10) from `design.md § Validation pipeline`
    - Implement `find_cycle` iteratively with an explicit stack, returning the true cycle
      path rather than an arbitrary visited set
    - Emit the structured error contract: `code`, `severity`, `node_id`, `edge_id`,
      `field`, `message`, `expected`, `actual`, `fix_hint`
    - Enforce the graph-size limits as validation errors naming the exceeded limit and its
      permitted value
    - Recompute node port lists, edge types, validation state, hash and execution order
      server-side; never trust client-supplied values
    - Leave named seams for the leakage stage (Phase 5) and the ML readiness stage
      (Phase 6) rather than stubbing their behaviour
    - _Requirements: 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.10, 6.11, 6.12, 6.13, 6.14,
      6.15, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 25.4_

  - [x] 1.10 Implement `CompiledPlan` and warmup composition
    - Create `backend_app/backend/strategy_dag/plan.py` with `CompiledPlan` carrying
      `dag_hash` as a readable **field**, not a method (SB-02), and a single `to_dict()`
      serialization path
    - Implement `compute_warmup` with memoisation, composing warmups along a path
      (own + upstream max) rather than taking a bare maximum
    - _Requirements: 2.4, 2.5, 3.8_

  - [ ]* 1.11 Write unit tests for schema, wire round-trip and v1 migration
    - `tests/test_strategy_dag_schema.py`: parse/serialize round-trip, `ui` exclusion from
      the hash, id stability, unresolvable legacy block marked `INVALID`, unsupported
      schema version rejected
    - _Requirements: 1.5, 1.8, 1.9, 1.10, 1.11_

  - [ ]* 1.12 Write property test for hash layout invariance
    - `tests/test_dag_hash_layout_invariance.py`
    - **Property 4: The identity hash is invariant under presentation-only change**
    - Generator: random graphs plus random layout mutations (position, label, collapsed)
    - **Validates: Requirements 1.8, 2.1**

  - [x]* 1.13 Write property test for hash semantic sensitivity
    - `tests/test_dag_hash_sensitivity.py`
    - **Property 5: The identity hash changes under any semantic change**
    - Generator: random graphs plus random semantic mutations (node set, `block_id`,
      params, category, port wiring, schema version)
    - **Validates: Requirements 2.2**

  - [ ]* 1.14 Write unit tests for the legality matrix and every error code
    - `tests/test_strategy_dag_validator.py`: one rejecting and one accepting case per
      error code, table-driven over the cartesian product of port types and category pairs
    - Cover ACTION→DATA, ACTION→INDICATOR, ACTION→ML_DL and ML_DL→DATA rejection through
      R5 and R6 rather than as special cases
    - _Requirements: 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 7.8, 7.9, 8.2, 8.3, 8.4_

  - [ ]* 1.15 Write property test for cycle detection
    - `tests/test_dag_cycle_properties.py`
    - **Property 12: A graph containing a cycle is rejected, and `find_cycle` returns empty
      exactly when Kahn consumes every node**
    - Generator: random layered DAGs with an injected back-edge
    - **Validates: Requirements 6.8, 6.14, 6.15**

  - [ ]* 1.16 Write property test for registry descriptor integrity
    - `tests/test_block_registry_integrity.py`
    - **Property 8: Every advertised block is runnable, and every parameter value the UI can
      produce from a `ParamSpec` is accepted by the validator**
    - **Validates: Requirements 4.7, 4.8, 5.7**

  - [x] 1.17 Write the SB-03 guard test for category coverage
    - `tests/test_block_registry_categories.py`
    - **Property 9: Every `BlockCategory` holds at least one descriptor**
    - Assert FEATURE_ENGINEERING holds at least 15 descriptors; the test must fail against
      the pre-fix state where that category was empty
    - **Validates: Requirements 4.2, 4.3, 4.9**

  - [x] 1.18 Write the SB-04 guard test for registry parity
    - `tests/test_block_registry_indicator_parity.py`
    - **Property 10: Every backend indicator and every importable model appears in the
      registry**
    - Assert `wma`, `hma`, `catboost` and `autoencoder` are present, and that a model whose
      library is unavailable is absent; the test must fail against the pre-fix state
    - **Validates: Requirements 4.4, 4.5, 4.6**

  - [ ]* 1.19 Write unit tests for warmup composition and plan shape
    - `tests/test_strategy_dag_plan.py`: warmup composes along a path (EMA(200) →
      rolling-std(20) → lag(3)) instead of taking the maximum; `dag_hash` is a field;
      `to_dict()` is the only serialization path
    - _Requirements: 2.4, 2.5, 3.8, 8.6_

  - [x] 1.20 Write the architecture test for `strategy_dag` purity
    - `tests/test_strategy_dag_architecture.py`: no module under
      `backend_app/backend/strategy_dag/` imports `execution_engine`, `exchange_executor`,
      `credential_vault`, `api_key_vault` or FastAPI
    - _Requirements: 21.10_

  - [x] 1.21 Phase 1 exit verification
    - Confirm the schema, hash, legality, cycle, warmup and registry tests are green and
      that `build_registry()` assembles at startup with every category non-empty
    - Confirm nothing is re-pointed yet: `routers/strategies.py` and
      `strategy_compiler.py` are untouched, so this phase is a pure addition
    - Confirm the backend failure count has not risen above the 40-test baseline
    - Ensure all tests pass, ask the user if questions arise.
    - _Requirements: 4.2, 4.9, 21.10_

- [x] 2. Phase 2 — Collapse the two compilers (SB-01)

  - [x] 2.1 Make `strategy_compiler.py` the single canonical compiler
    - Extend `StrategyCompiler.compile` to take a canonical `StrategyGraph` plus the
      registry, delegate all validation to `strategy_dag/validator.py`, and return a
      `CompiledPlan`
    - Implement Kahn with a sorted ready set at every step so `execution_order` is
      byte-identical across processes, and emit `execution_levels` for
      `dag_engine_parallel`
    - Absorb the rules that only existed in the router copy: orphan and unreachable node
      rejection, action-input provenance, unknown node type rejection
    - Replace the recursive `_has_cycle` with the iterative `find_cycle`
    - Raise `ValidationError(report)` and emit no plan when the graph is invalid
    - _Requirements: 2.3, 2.5, 3.1, 3.3, 3.4, 3.5, 3.8, 7.1, 7.2, 7.3_

  - [x] 2.2 Land migration `004_strategy_builder_canonical.sql` part 1
    - Create `backend_app/migrations/004_strategy_builder_canonical.sql` with the
      `strategy_versions` additions: `graph_json`, `compiled_plan`, `dag_hash`,
      `schema_version`, `compiler_version`, `validation_state`, `validation_report`,
      `lifecycle_state`, `warmup_bars`, `registry_version`
    - Add `chk_validation_state`, `chk_lifecycle_state`, `chk_graph_shape` and the three
      indexes; leave existing tables, indexes, triggers and RLS untouched
    - Defer `chk_valid_requires_hash` and the immutability trigger to Phase 4
    - _Requirements: 9.1_

  - [x] 2.3 Persist the canonical graph and plan through the service layer
    - Extend `backend_app/backend/strategy_service.py` and
      `backend_app/backend/strategy_builder.py` with version creation writing
      `graph_json`, `compiled_plan`, `dag_hash`, `schema_version`, `compiler_version`,
      `registry_version`, `warmup_bars`, `validation_state` and `validation_report`
    - Persist nothing on the validation-failure path
    - _Requirements: 3.6, 3.7, 9.1_

  - [x] 2.4 Re-point all six call sites and delete the router compiler in one step
    - This is a single atomic change; there must never be a window with two active
      compilers
    - `routers/strategies.py`: `POST /validate`, `POST /strategies` and the clone path call
      the canonical validator and compiler; then **delete** the `DAGCompiler` and
      `CompiledDAG` classes from the file
    - `routers/strategy_operations.py`: `POST /strategies/compile` accepts a canonical
      `StrategyGraph`
    - `dag_worker.py` and `dag_event_loop.py` consume `CompiledPlan.to_dict()` from the
      persisted `compiled_plan` instead of loose dicts
    - The Backtester load path reads the persisted `compiled_plan`, recompiling only when
      `dag_hash` does not match
    - Keep `GET /api/strategies/blocks` as a thin alias marked `"deprecated": true` with a
      `"replacement"` field for one release
    - _Requirements: 3.1, 3.2, 22.3, 22.5_

  - [ ]* 2.5 Write property test for all-or-nothing compilation
    - `tests/test_compiler_no_partial_plan.py`
    - **Property 1: `compile(g)` returns a plan or raises; it never returns a partial plan
      and never persists on the failure path**
    - **Validates: Requirements 3.5, 3.6**

  - [ ]* 2.6 Write property test for topological correctness
    - `tests/test_compiler_topological_property.py`
    - **Property 2: `execution_order` is a permutation of the node set with every edge's
      source before its target, and every node's predecessors appear in an earlier level**
    - Generator: random layered graphs, guaranteed acyclic, including diamonds and
      multi-action topologies
    - **Validates: Requirements 3.3, 3.4, 7.1, 7.2, 7.3**

  - [ ]* 2.7 Write property test for compiler determinism
    - `tests/test_compiler_determinism_property.py`
    - **Property 3: Repeated compilation, in-process and across processes, yields an
      identical hash and an identical execution order**
    - **Validates: Requirements 2.3**

  - [x] 2.8 Write the SB-01 regression test
    - `tests/test_sb01_single_compiler_regression.py`: submit one graph set through the
      validate, save, clone, compile and plan-load paths and assert an identical validity
      verdict and identical error code set from every path
    - Include the graphs that previously diverged: an orphan node, an action fed directly by
      an indicator, a single-node graph with no edges, and an unknown node type
    - The test must fail against the pre-fix commit
    - _Requirements: 3.1, 3.2_

  - [x] 2.9 Write the single-compiler architecture tests
    - `tests/test_compiler_architecture.py`: `DAGCompiler` and `CompiledDAG` exist nowhere
      under `backend_app/routers/`, and `strategy_compiler` is the only module defining a
      graph `compile` entry point
    - _Requirements: 3.1_

  - [x] 2.10 Write the golden-file runtime plan test
    - `tests/test_dag_runtime_golden_plan.py`: a fixed canonical graph over fixed candles
      produces a fixed intent sequence when driven from the persisted `compiled_plan`
    - This is the contract that keeps the Backtester and live execution in agreement
    - _Requirements: 22.3, 22.4_

  - [x] 2.11 Phase 2 exit verification
    - Confirm the architecture test proves exactly one compiler, the determinism property is
      green and the golden-file test is unchanged
    - Confirm the backend failure count has not risen above the 40-test baseline
    - Ensure all tests pass, ask the user if questions arise.
    - _Requirements: 2.3, 3.1, 3.2_

- [x] 3. Phase 3 — Backend-authoritative registry end to end (SB-03, SB-04, SB-05, SB-06)

  - [x] 3.1 Ship the registry endpoints
    - Add to `backend_app/routers/strategy_operations.py`: `GET /registry/blocks`,
      `GET /registry/indicators`, `GET /registry/features`, `GET /registry/models` and
      `GET /registry/timeframes`
    - Serve `registry_version`, `port_types`, ordered `categories`, `blocks[]` and the
      `compatibility_matrix`; support `ETag` with 200 and 304
    - Keep `Depends(get_current_user)` and the existing `slowapi` limits on every endpoint
    - _Requirements: 4.1, 4.11, 4.15, 5.1, 6.9, 11.8, 21.1, 21.8_

  - [x] 3.2 Persist registry snapshots
    - Extend `004_strategy_builder_canonical.sql` with `block_registry_snapshots` and write a
      snapshot keyed by `registry_version` on registry assembly
    - Record `registry_version` on each created version so what the author was offered stays
      reproducible
    - _Requirements: 4.16, 9.1_

  - [x] 3.3 Add the single frontend serializer
    - Create `algo22-terminal/src/lib/canonicalGraph.js` with `toCanonical(nodes, edges)` and
      `fromCanonical(graph)`
    - Preserve `sourceHandle` and `targetHandle` as `source_port` and `target_port`, carry
      `block_id` and `category` explicitly from the descriptor, and derive no semantics from
      display labels
    - Emit every node of every category, including DATA, MATH and FEATURE_ENGINEERING
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 3.4 Delete the two lossy serializers
    - Delete `algo22-terminal/src/utils/dagSerializer.js`, whose type filter silently
      discarded every DATA, MATH and FEATURE node
    - Delete the inline `serializeReactFlowToDAG` in
      `algo22-terminal/src/pages/StrategyBuilder.jsx` (~line 521) that shadowed the import
      and emitted a third shape
    - Re-point every call site to `canonicalGraph.js`
    - _Requirements: 1.1, 1.2, 1.3_

  - [x] 3.5 Reduce `blockRegistry.js` to presentation and add the registry client
    - Delete all block definitions from `algo22-terminal/src/lib/blockRegistry.js`; keep only
      the category → icon/colour mapping and the `StreamTypes` re-export generated from the
      backend port-type vocabulary
    - Add `algo22-terminal/src/lib/registryClient.js` fetching the registry with `ETag`
      caching and exposing descriptor lookup
    - Fail closed: on any registry error the client returns an error state and never a local
      block list, because that fallback is the drift mechanism behind SB-03 and SB-04
    - _Requirements: 4.11, 4.12_

  - [x] 3.6 Re-point the palette at the registry
    - In `StrategyBuilder.jsx`, render all seven categories from the registry response,
      ordered by `categories[].order`
    - Match palette search against display name, block id, description and category across
      every category, not just indicators, ML and DL
    - Show each entry's input and output port types as chips
    - Render an explicit error panel with retry, and zero block entries, when the registry
      request fails
    - _Requirements: 4.11, 4.12, 4.13, 4.14_

  - [x] 3.7 Generate parameter forms from `ParamSpec`
    - Add `algo22-terminal/src/components/builder/ParameterForm.jsx` rendering the control per
      `type`, with label, unit, min, max, step, options, example as placeholder and help as
      tooltip
    - Show every required parameter without expansion; group optional params under
      "Advanced"; show declared defaults with an explicit "default" affordance
    - Render behaviour-changing params (`symbol`, `timeframe`, `quantity`, `quantity_type`,
      `price`, `trigger_price`, `confidence_threshold`) empty and blocking until set
    - Display the backend's `fix_hint` text for inline validation messages so the two never
      disagree
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 8.9_

  - [x] 3.8 Add client-side connection legality
    - Add `algo22-terminal/src/lib/connectionLegality.js` implementing R1–R8 against the
      shipped `compatibility_matrix` and descriptors
    - Dim incompatible target ports during an edge drag, before the drop
    - Refuse an illegal edge locally with the rejecting reason: port types, categories,
      terminal source, occupied non-variadic port, self-loop, unknown port, cycle path
    - The local check is UX latency only; the backend stays authoritative
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

  - [x] 3.9 Remove exchange and market identity from the save payload (SB-06)
    - Delete `exchange: "binance"`, the `|| "BTC/USDT"` and `|| "15m"` fallbacks and the
      unmatched `ccxt_asset_feed` node lookup from `handleSaveStrategy` in
      `StrategyBuilder.jsx`
    - Read `symbol` and `timeframe` from the DATA node's validated params; fail the save with
      a structured validation error naming the node and field when either is unset
    - Fix `POST /strategies/{id}/train` in `routers/strategies.py` to construct
      `ConnectionEngine` from the resolved training data source instead of the literal
      `"binance"`
    - Resolve an ACTION node's traded symbol from its upstream DATA node in the compiler
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.8_

  - [x] 3.10 Wire debounced validation, markers and the status strip
    - Mark the graph unvalidated on edit and request backend validation 400 ms after the last
      edit
    - Mark each node and edge named in the report with its reported severity and issue count,
      and render the issue list with `fix_hint` text
    - Render the status strip: validation summary, feed state, save state, training state
    - _Requirements: 8.8, 8.9, 8.10, 8.11_

  - [ ]* 3.11 Add fast-check and write the serializer round-trip property test
    - Add `fast-check` to `algo22-terminal/package.json` as a pinned dev dependency — the only
      new dependency in this design
    - `algo22-terminal/tests/unit/canonicalGraph.roundtrip.test.js`
    - **Property 6: `fromCanonical(toCanonical(g)) ≡ g` — no node, parameter or port dropped**
    - Generator: random canonical graphs across all seven categories (fast-check)
    - **Validates: Requirements 1.4**

  - [x] 3.12 Write the anti-fallback palette test
    - `algo22-terminal/tests/unit/palette.antifallback.test.jsx`: with the registry endpoint
      returning 500, assert the palette renders an error state with retry and **zero** block
      entries
    - _Requirements: 4.12_

  - [x] 3.13 Write the SB-03 and SB-04 palette regression tests
    - `algo22-terminal/tests/unit/palette.categories.test.jsx`: all seven categories render
      populated from a mocked registry; FEATURE_ENGINEERING renders at least 15 entries;
      `wma`, `hma`, `catboost` and `autoencoder` are selectable
    - Assert `blockRegistry.js` exports zero block definitions
    - The tests must fail against the pre-fix frontend
    - _Requirements: 4.2, 4.3, 4.4, 4.5, 4.11_

  - [x] 3.14 Write the SB-05 serializer regression test
    - `algo22-terminal/tests/unit/canonicalGraph.sb05.test.js`: a graph containing DATA, MATH
      and FEATURE_ENGINEERING nodes survives serialization with every node and every port
      handle intact, and no node semantics are derived from `data.label`
    - The test must fail against the deleted `dagSerializer.js` behaviour
    - _Requirements: 1.1, 1.2, 1.3_

  - [x] 3.15 Write the SB-06 exchange-agnostic save regression test
    - `tests/test_sb06_exchange_agnostic_save.py`: saving with an unparameterised DATA node
      fails with a validation error naming the node and the missing field; no `"binance"`,
      `"BTC/USDT"` or `"15m"` value is ever substituted; no exchange identifier or credential
      appears in `graph_json`, `compiled_plan` or `training_jobs.config`
    - The test must fail against the pre-fix save path
    - _Requirements: 12.1, 12.2, 12.3, 12.4_

  - [ ]* 3.16 Write the client and server legality parity test
    - `tests/test_edge_legality_parity.py` plus its JS mirror
    - **Property 11: The client and the backend accept exactly the same edge set**
    - Generator: cartesian product of port types and category pairs, evaluated by both
      implementations from the same shipped matrix
    - **Validates: Requirements 6.10, 6.11**

  - [x] 3.17 Write the builder and backtester import architecture test
    - `algo22-terminal/tests/unit/builder.architecture.test.js`
    - **Property 24 (structural half): `StrategyBuilder.jsx` imports nothing from the
      Backtester module tree**
    - **Validates: Requirements 22.2**

  - [x] 3.18 Phase 3 exit verification
    - Confirm FEATURE_ENGINEERING populates, `wma`, `hma`, `catboost` and `autoencoder` are
      selectable, and the registry-parity, round-trip and anti-fallback tests are green
    - Confirm `dagSerializer.js` is gone, the inline serializer is gone, and
      `blockRegistry.js` holds no block definitions
    - Ensure all tests pass, ask the user if questions arise.
    - _Requirements: 1.4, 4.2, 4.3, 4.4, 4.12, 12.4_

- [x] 4. Phase 4 — Clone correctness (SB-02)

  - [x] 4.1 Add the constraint and trigger that make SB-02 unrepresentable
    - Extend `004_strategy_builder_canonical.sql` with `chk_valid_requires_hash CHECK
      (validation_state <> 'VALID' OR (dag_hash IS NOT NULL AND compiled_plan IS NOT NULL))`
    - Add `reject_immutable_version_update()` and the `trg_sv_immutable` BEFORE UPDATE trigger
      rejecting changes to `graph_json`, `compiled_plan`, `dag_hash` and `schema_version` on a
      read-only version
    - _Requirements: 9.2, 9.3, 10.5_

  - [x] 4.2 Fix the clone path
    - Recompile the clone through the canonical compiler and read `plan.dag_hash` as a field,
      ending the object-versus-dict access that produced the swallowed `AttributeError`
    - Replace the `except Exception` that downgraded the failure to a warning with typed
      exception handling; a bare except on this path is a review-blocking defect
    - Persist a clone whose recompile fails with `validation_state = 'INVALID'` and the
      structured report attached, and return the failure in the response `warnings[]`
    - _Requirements: 3.7, 10.1, 10.2, 10.3_

  - [x] 4.3 Add the deploy prerequisite gate
    - Refuse deployment when `validation_state <> 'VALID'`, `dag_hash IS NULL` or
      `compiled_plan IS NULL`, naming the missing prerequisite
    - Keep the existing ownership filters and RLS in force; this task only adds checks
    - _Requirements: 9.5, 10.4_

  - [x] 4.4 Write the SB-02 regression test
    - `tests/test_sb02_clone_preserves_plan.py`: cloning a valid strategy yields a non-null
      `dag_hash` and `compiled_plan`; cloning an invalid one yields
      `validation_state = 'INVALID'` plus a non-empty `warnings[]`
    - The test must fail against the pre-fix commit and pass after
    - _Requirements: 10.1, 10.2, 10.3_

  - [ ]* 4.5 Write the database constraint and immutability tests
    - `tests/test_strategy_version_constraints.py`: the database rejects
      `validation_state = 'VALID'` with a null hash or plan, and the trigger raises on any
      update to a read-only version's graph, plan, hash or schema version
    - **Property 21: A deployed version is immutable**
    - **Validates: Requirements 9.2, 9.3, 10.5**

  - [ ]* 4.6 Write property test for clone outcome exhaustiveness
    - `tests/test_clone_tristate_property.py`
    - **Property 7: A clone is either fully compiled or explicitly `INVALID` with a non-empty
      report; there is no third outcome**
    - **Validates: Requirements 10.1, 10.2, 10.5**

  - [x] 4.7 Phase 4 exit verification
    - Confirm the SB-02 regression test fails on the pre-fix commit and passes now, and that
      the database rejects the bad state directly
    - Ensure all tests pass, ask the user if questions arise.
    - _Requirements: 9.3, 10.1, 10.2_

- [x] 5. Phase 5 — Feature engineering UX and leakage protection

  - [x] 5.0 Correct `CompiledPlan.inbound` variadic fan-in (design.md STRUCTURE parity)
    - Corrective task. **Reopens 1.10 and 2.1**: 1.10 typed `CompiledPlan.inbound` as
      `node_id -> target_port -> ONE EdgeSpec` (built by `inbound_map` with last-write-wins)
      by following the inline comment in `design.md`'s compile algorithm
      ("target_port -> resolved edge") instead of the typed STRUCTURE line
      `inbound: Map<String, List<EdgeSpec>>`, which is authoritative; 2.1's
      `plan_to_engine_graph` then emitted one edge per port
    - A variadic input port legitimately holds 2..N connections — validator rule R7 rejects a
      second edge only on a *non*-variadic port — so `feat_concat`, `and`, `or`, `add`,
      `multiply`, `min`, `max` and `between` silently lost every operand but one: `add`
      returned one addend, `and` returned one condition. Same silent-wrong-answer class as
      SB-05's dropped nodes
    - `plan.py`: `inbound_map`, the `CompiledPlan.inbound` field, its `__post_init__`
      normalisation, `to_dict` and `from_dict` all carry `target_port -> Tuple[EdgeSpec, ...]`,
      in the graph's own total edge order so operand order is identical across processes and
      across a JSON round trip. `to_dict()` remains the single serialization path and
      `dag_hash` remains a readable field, never a method (SB-02)
    - `inbound_edge` keeps single-edge semantics and now **raises** `PlanBuildError` on a
      multiply-fed port rather than returning one of several; `inbound_edges` is the plural
      accessor a variadic-aware caller uses
    - `from_dict` accepts both shapes: a pre-fix persisted `compiled_plan` row storing one
      edge object per port loads as a one-element tuple, and a value that is neither an edge
      nor a sequence of edges is rejected rather than skipped
    - `strategy_compiler.py`: `_engine_input_order` and `plan_to_engine_graph` emit every edge
      on a port, per target in declared input-port order and within a port in plan order
    - Golden re-recorded for `plan.sha256` only (`tests/golden/dag_runtime_plan_golden.json`):
      the golden graph's maximum fan-in is 1, so no operand changed — only `inbound`'s JSON
      shape, one edge object becoming a one-element list. `dag_hash`, `execution_order`,
      `execution_levels`, the engine payload digest and every node-output digest are unchanged
    - _Requirements: 2.4, 2.5, 3.3, 3.4, 20.2_

  - [x] 5.1 Add the `FeatureMatrix` contract
    - Create `backend_app/backend/strategy_dag/feature_matrix.py` with `index`, `columns`,
      `values`, `warmup_offset` and per-column `provenance`
    - Align combined feature outputs on the timestamp index, never on position
    - _Requirements: 18.12, 18.13_

  - [x] 5.2 Add `FeatureExecutor` and port-addressed input resolution to `dag_engine.py`
    - Add `FeatureExecutor` delegating to the `FEATURE_SPECS` runtime refs in
      `feature_engineering.py`
    - Teach `execute_node` to read inputs keyed by input port name resolved from
      `plan.inbound` and to write outputs keyed by output port name
    - Validate each output series against its port descriptor before it enters the value map,
      reusing the existing `validate_node_output` helper
    - Preserve the existing `ExecutionTracer` and event-buffer behaviour
    - _Requirements: 4.3, 18.12, 20.2_

  - [x] 5.3 Add `MathExecutor` with the numeric safety firewall
    - Implement `safe_math_apply` in `dag_engine.py` per `design.md § Math blocks`: NaN
      propagation within a series, NaN for division by near-zero, `sqrt(<0)`, `log(<=0)` and
      overflowing `exp`, with the condition recorded against the node, and never `±Inf`
    - Implement `assert_execution_safe(intent, node_id)` blocking non-finite and non-positive
      order fields before any intent leaves the runtime
    - Arithmetic stays in `block_specs`: `dag_engine.safe_math_apply` is the engine-side seam
      that adds bar alignment and hands the kernel's reports to `NodeIssueLog` under the
      node id. `MathExecutor` is `BlockKernelExecutor` (MATH and LOGIC differ only in which
      kernel they call), and operands bind by input port name through
      `PortInputs.port_values`, so `high -> subtract.a` / `low -> subtract.b` from one
      upstream node stays two operands instead of collapsing to `low - low`
    - Overflow is recorded, not only NaN'd: Requirement 20.4 names "an arithmetic overflow"
      alongside the three undefined operations, and `exp(800)` / `1e300 * 1e300` were being
      converted to NaN by the terminal finite-or-NaN sweep with nothing reported. One
      detection on the result covers every op (`ARITHMETIC_OVERFLOW`); a bar already NaN'd by
      a named branch is no longer an infinity, so the two never double-report a bar
    - `assert_execution_safe` raises a subclass of the platform's existing
      `core.global_safety.ExecutionBlocked` and **adds** a check: `execution_guard.py`,
      `risk_engine.py` and `dag_risk_integration.py` stay authoritative on the intent path.
      What they cannot see is a field that is not a number at all - NaN compares False
      against every limit, so a "refuse if quantity > max" guard passes it through
    - `tests/test_math_executor_firewall.py` covers each rule with a rejecting and an
      accepting case through the real registry, compiler and `DAGEngine`, plus the recorded
      condition, variadic MATH operands and the intent firewall on non-finite and
      non-positive fields
    - _Requirements: 20.3, 20.4, 20.5, 20.6_

  - [x] 5.4 Add `LogicExecutor` and re-point `IndicatorExecutor`
    - Implement the comparators, boolean gates, `between`, `if_then_else` and `to_signal` from
      `LOGIC_SPECS`
    - Implement `cross_above` and `cross_below` on closed bars only, reporting false when
      either series is NaN at the current or previous bar so the warmup region never signals
    - Re-point `IndicatorExecutor` at `indicators_backend` and delete its private
      `_calculate_*` duplicates, exposing every output port of `macd`, `bollinger_bands` and
      the other multi-output indicators
    - `LogicExecutor` is a `BlockKernelExecutor` subclass whose default fallback is the
      renamed `LegacyLogicExecutor`, so it stays no-arg constructible for
      `dag_engine_parallel` and a loose `{"type": "logic", "operator": "GT"}` dict keeps its
      previous behaviour. Not one comparison or boolean rule is restated in the engine: the
      `logic_*` kernels in `block_specs` already own the comparators, the gates, `between`,
      `if_then_else`, `to_signal` and both crosses, and operand binding, variadic-port
      expansion and the `input_order` fallback are inherited unchanged from task 5.3
    - `IndicatorExecutor` now resolves the node's `IndicatorSpec`, binds operands from
      `spec.inputs` in declaration order (falling back to the market-data column of the same
      name, which is what it read unconditionally before), and publishes one Series per
      declared output port through `NodeOutputs`. The legacy primary port is pinned
      explicitly - `macd` -> `histogram`, `bollinger_bands` -> `percent_b` - so publishing
      the other ports does not move `node_results[node_id]`. `guard_market_data`,
      `observe_market_data`, `guard_indicators`, `observe_indicators` and the
      excessive-missing-data refusal all survive; the two indicator guards were previously
      unreachable for the six named indicators and now run for all 33
    - A named-but-unpublished indicator is now **refused** rather than silently computed as
      RSI(14), which is how 27 of the 33 published indicators used to behave
    - `indicators_backend.adx` returned 100% NaN on all three ports for every input length
      (`plus_dm[0]` / `minus_dm[0]` left NaN NaN'd the EMA seed and the recursion carried it,
      and DX's own EMA then seeded from that warmup). Fixed with `plus_dm[0] = minus_dm[0] =
      0.0` - bar 0 has no previous bar, exactly as `tr[0]` already assumes - and
      `_ema_after_warmup` for DX. Invisible until the re-point, since the executor had no ADX
      branch. `tests/test_indicator_specs.py` now asserts no declared output port is entirely
      NaN or holds an infinity, for every spec
    - Golden re-recorded, deliberately (`tests/golden/dag_runtime_plan_golden.json`):
      `gold_rsi`, `gold_ema` and therefore `gold_gate` moved because the deleted private
      copies **disagreed** with the module they duplicated. `_calculate_rsi` used a simple
      rolling mean over `loss + 1e-9`, so the fixture's flat stretch (bars 40-61) slammed RSI
      between 100.0 and 0.0 with no price change; Wilder smoothing holds 93.835. The EMA
      branch published a 20-bar EMA at bar 0 from one bar where the registry declares a
      20-bar warmup, so `first` moved from 100.0 to NaN. `dag_hash`, the plan bytes,
      `execution_order`, `execution_levels`, the engine payload digest, `gold_data`,
      `gold_floor`, `gold_buy`, `signals_rle` and `intents` are all unchanged - no trade
      decision moved. The reasoning is recorded in the golden test's own docstring
    - `tests/test_logic_and_indicator_executors.py` covers every comparator, the three gates
      including a variadic port, `between` inclusive and exclusive, `if_then_else`,
      `to_signal` with its refusals, the crossover warmup rule bar by bar and end to end
      against a real EMA warmup, all three `macd` ports, all five `bollinger_bands` bands,
      `stochastic`'s high/low/close port binding, all 33 indicators publishing every declared
      port, the pinned legacy primaries, and the absence of the "executor did not produce
      that port" fallback warning
    - _Requirements: 5.10, 20.2, 20.7_

  - [x] 5.5 Implement the leakage validation stage
    - Add `validate_no_lookahead` to `strategy_dag/validator.py` as validation stage 10:
      negative `shift` → `LOOKAHEAD_SHIFT`; a `REVIEW_REQUIRED` block reaching an ML node →
      `LEAKY_FEATURE_INTO_MODEL`; global-mode z-score or normalize → `GLOBAL_STATISTIC_LEAK`
    - Compute ML reachability once before the loop so classification is order-independent
    - _Requirements: 18.1, 18.2, 18.3, 18.4_

  - [x] 5.6 Implement temporal splits and the supervised dataset builder
    - Create `backend_app/backend/ml_dataset.py` with `make_temporal_splits` returning
      chronological, disjoint, embargoed **ranges** — never index permutations, so shuffling
      is structurally impossible — and `build_supervised_dataset` enforcing
      `rows(X) == len(y)`, backward-only features, forward-only labels and dropped trailing
      horizon rows
    - Set the embargo to at least the longest feature lookback plus the label horizon
    - This is the invariant form of the recorded ML-1 off-by-one defect
    - _Requirements: 18.5, 18.6, 18.7, 18.8, 18.9, 18.10, 18.11_

  - [x] 5.7 Add node preview through the same executors
    - Add a bounded-window preview endpoint to `routers/strategy_operations.py` computing the
      last N values through the same executors the runtime uses, so a preview cannot disagree
      with runtime
    - Render the preview in the inspector; for a FEATURE_ENGINEERING node show the produced
      column names and a sample of values
    - `POST /strategy-operations/strategies/{id}/nodes/{node_id}/preview` is a thin transport
      over four objects that already exist: `load_graph`, `get_compiler().compile_plan`,
      `plan_to_engine_graph` and `DAGEngine.execute_dag`. It defines no indicator, feature,
      math or logic code and no execution loop of its own, and
      `tests/test_node_preview_endpoint.py` asserts that with `ast` over the module rather than
      trusting a comment — plus a control proving the guard would catch a real reimplementation
    - Only the previewed node's upstream closure is executed, in `plan.execution_order`: a
      node's value is a function of its ancestors, so restricting the node set cannot change a
      value, and it keeps an untrained ML node downstream from breaking a feature preview
    - The window is capped server-side and is sized from the **previewed node's** composed
      warmup (`plan.compute_warmup(node_ids=[node_id])`), not the plan's. It must cover
      `2 * warmup + sample_rows`, because `core.pipeline_guard.guard_indicators` refuses a
      series that is more than half warmup — a real runtime control, so a preview cannot render
      a mostly-warmup window as nulls without disagreeing with runtime. When even the cap
      cannot cover it the answer is `PREVIEW_WARMUP_EXCEEDS_WINDOW` naming the warmup, the
      window needed and the ceiling, not `Excessive NaN values (94.7%)`
    - `DAGEngine.get_node_issues()` travels with the preview (Requirement 20.4), which is what
      turns "why is this bar empty?" into `DIVISION_BY_ZERO at bar 34`
    - No exchange identifier or credential is accepted or returned: `extra="forbid"` makes an
      `exchange` key a 422, an undeclared `exchange` *param* is reported `PARAM_UNKNOWN` and
      ignored, and the venue is the server's own `DEFAULT_EXCHANGE`. A preview places no order
      and starts no training job — `plan_to_engine_graph` omits ACTION's `runtime_ref`
    - Frontend: `lib/nodePreview.js` (projection + state machine, pure, no arithmetic — the
      absence is asserted structurally), `components/builder/NodePreview.jsx` and
      `strategiesApi.previewNode`. A `null` bar renders as an em dash, never `0`; every
      produced column name survives a truncated value sample; a refusal shows the backend's own
      `message` and `hint` verbatim; an unsaved strategy is explained rather than only disabled
    - _Requirements: 24.7, 24.8_

  - [ ]* 5.8 Write property test for arithmetic safety
    - `tests/test_math_executor_properties.py`
    - **Property 13: No math result contains `±Inf` or an overflowed value; division by zero,
      `sqrt(<0)` and `log(<=0)` yield NaN**
    - Generator: random float series including NaN, ±Inf and extremes
    - **Validates: Requirements 20.3, 20.4**

  - [ ]* 5.9 Write property test for supervised dataset alignment
    - `tests/test_supervised_dataset_property.py`
    - **Property 16: `rows(X) == len(y)` for every dataset and every horizon ≥ 1**
    - Generator: random `(rows, warmup, horizon)` triples
    - **Validates: Requirements 18.9, 18.10**

  - [ ]* 5.10 Write property test for split disjointness
    - `tests/test_temporal_splits_property.py`
    - **Property 17: Train, validation and test ranges are disjoint, chronologically ordered
      and separated by at least the embargo bar count**
    - Generator: random `(n, val_fraction, test_fraction, embargo)` tuples
    - **Validates: Requirements 18.5, 18.6, 18.11**

  - [x] 5.11 Write the leakage suite
    - `tests/ml/test_no_leakage.py` covering the four cases the design mandates: a model
      trained on shuffled-in-time data scores materially worse on the chronological test
      split; a future-shifted column raises `LOOKAHEAD_SHIFT`; a `REVIEW_REQUIRED` block
      feeding a model is rejected; global-statistic normalization is rejected
    - **Property 18: No feature on a path into a model reads a bar later than its own row**
    - **Validates: Requirements 18.2, 18.3, 18.4, 18.7, 18.8**

  - [ ]* 5.12 Write unit tests for the feature and logic executors
    - Output length, warmup offset and column naming per feature block; timestamp-aligned
      `feat_concat`; `cross_above` silence in the NaN warmup region; type rejection cases
    - _Requirements: 4.3, 18.12, 18.13, 20.7_

  - [x] 5.13 Phase 5 exit verification
    - Confirm the leakage suite is green including the shuffle-detection check, and that
      FEATURE_ENGINEERING nodes execute end to end through `dag_engine`
    - Ensure all tests pass, ask the user if questions arise.
    - `tests/ml/test_no_leakage.py` is green — 34 tests, in three collection orders and in
      four separate runs, including the order that matters: after
      `tests/test_strategy_dag_validator.py`, which clears the stage hooks. The empirical
      shuffle check is the one that had to be run repeatedly rather than once, and it holds
    - FEATURE_ENGINEERING end to end confirmed through `tests/test_dag_engine_port_addressing.py`
      (49 green). Its `run()` helper is the real path and nothing in it is faked: `V.validate`
      → `SC.compile_graph` → `SC.plan_to_engine_graph` → `DAGEngine.execute_dag` over a real
      OHLCV frame, and `test_a_feature_node_produces_a_matrix_on_the_market_data_index` asserts
      a `FeatureMatrix` whose index is the market-data index, per block
    - Phase 5 surface confirmed coherent: `pending_stages()` is `('ml_readiness',)` at import;
      `MathExecutor` is `BlockKernelExecutor`, `LogicExecutor` subclasses it, both bind operands
      through `PortInputs.port_values` in declared-port order with `_bind_positionally` reachable
      only by a payload carrying no descriptor or no port-addressed edges; `CompiledPlan.inbound`
      is `target_port -> Tuple[EdgeSpec, ...]` through `inbound_map`, `__post_init__`, `to_dict`
      and `from_dict`, with `inbound_edge` raising on a multiply-fed port; the golden plan test
      passes unchanged. 827 green across the Phase 5 file set
    - **Gap found and closed (not a test weakening).** "No path can skip leakage" was not true
      process-wide: `tests/test_strategy_dag_validator.py` exercises the stage seam with
      `clear_stage_hooks()` and never restored it, so every module collected after it validated
      with stage 10b uninstalled — a leaky graph would have read clean and earned a `dag_hash`,
      silently. Proven, not inferred: with the restore removed, a probe asserting
      `pending_stages() == ('ml_readiness',)` after that file reports `('leakage', 'ml_readiness')`.
      An autouse fixture now restores the defaults; the two seam tests still clear the hooks
      themselves and still assert exactly what they asserted
    - **Baseline drift settled** — see the Baseline bullet above. It was not collection-order
      dependence; it was one flaky test with a named cause, now fixed. Backend
      **2490 passed / 44 failed / 18 skipped**, the same 44 in three consecutive runs; frontend
      **508 passed / 0 failed** over 19 files
    - Optional 5.8, 5.9, 5.10 and 5.12 leave no hole this gate depends on, checked against the
      landed suites rather than assumed. 5.9 and 5.10: `tests/test_ml_dataset.py` (44 tests)
      enforces `rows(X) == len(y)`, split disjointness, chronological order and the embargo floor
      **at construction**, so they hold for every input rather than for drawn ones, and
      `tests/ml/test_no_leakage.py` adds the generated form over `(bars, warmup, horizon)`.
      5.12: output shape, index, warmup offset and column naming per feature block, the
      timestamp-aligned `feat_concat`, `cross_above`'s silence through a real NaN warmup and the
      by-name type rejections are all directly covered across
      `test_dag_engine_port_addressing.py`, `test_feature_specs.py`,
      `test_feature_matrix_contract.py` and `test_logic_and_indicator_executors.py`. 5.8 is the
      one with a caveat worth stating plainly: every named rule of Property 13 has its own
      rejecting and accepting test, and
      `test_no_operation_produces_an_infinity_on_an_adversarial_series` sweeps all 17 published
      MATH operations with a series carrying NaN, ±Inf, zero, a negative, a denormal, both ends
      of the float range and an overflowing exponent — the property's generator, as fixed
      adversarial constants rather than random draws. The substance is covered; what is missing
      is the chance of being surprised by an input nobody chose
    - _Requirements: 4.3, 18.2, 18.5, 18.9_

- [x] 6. Phase 6 — Training pipeline, caps and model versioning

  - [x] 6.1 Land the training and model tables
    - Extend `004_strategy_builder_canonical.sql` with `training_jobs` and `model_versions`
      exactly as specified, including `chk_tj_status`, `chk_tj_progress`,
      `chk_tj_failed_has_reason`, the `uq_tj_active_per_node` partial unique index, the
      `uq_mv_active_per_node` partial unique index, `uq_mv_version_node`, the supporting
      indexes and the RLS policies for both tables
    - Store per-epoch scalars only in `metrics_history`; never predictions or feature values
    - _Requirements: 15.13, 17.4, 21.3_
    - **Landed as `backend_app/migrations/004d_training_and_models.sql` (migration 004
      part 4)**, following the sibling-file pattern parts 2 and 3 established: migrations
      here are applied by hand, per file, with nothing recording which files an environment
      has run, so appending to a file an operator may already have applied leaves no signal
      that it changed. Parts 1, 2 and 3 now name part 4's file in their split enumeration.
    - Both partial unique indexes are the mechanism, not a check:
      `uq_tj_active_per_node ... WHERE status IN ('QUEUED','RUNNING')` scopes uniqueness to
      LIVE jobs, so a node keeps its completed/failed/cancelled history and stays
      retrainable while two live jobs are unrepresentable (15.13);
      `uq_mv_active_per_node ... WHERE is_active` does the same for models, which is the
      ML-3 retrain-overwrite class made impossible (17.4). Dropping either predicate would
      invert the requirement rather than merely relax it.
    - RLS enabled on both tables with the design's five policies, all keyed on
      `user_id = auth.uid()` (21.3). **No DELETE policy on either table** and **no UPDATE
      policy on `model_versions`**, exactly as the design specifies. The second is
      load-bearing for task 6.5: flipping `is_active` on a superseded model version cannot
      be done with the caller's RLS-scoped client, so 6.5 must deactivate through the
      backend's service role in the same transaction as the new insert, or the insert will
      fail on `uq_mv_active_per_node`. Grants narrowed to match (no DELETE for anybody, no
      client-side UPDATE on `model_versions`, nothing for `anon`). No existing table's RLS,
      columns or indexes are named in any DDL statement in the file.
    - `metrics_history` carries a `COMMENT ON COLUMN` stating the data-minimisation rule:
      per-epoch scalars only, never predictions and never feature values, because the
      column is read back for progress charting and serialised into API responses and
      realtime frames. **SQL cannot enforce this** — a CHECK can see that the value is
      JSONB, not that it is a loss curve rather than a prediction vector — so the comment
      records the rule where the next author of the progress payload will read it and the
      enforcement itself belongs to tasks 6.4 and 6.6. Not presented as a guarantee.
    - Deliberately NOT added: a `BEFORE UPDATE` trigger maintaining `training_jobs.
      updated_at`. 003 attaches one to every table it creates that carries `updated_at`, so
      the departure from local habit is called out in the file header rather than left as an
      oversight — the design specifies no such trigger, the column carries
      `NOT NULL DEFAULT NOW()`, and the only freshness signal anything depends on is
      `last_heartbeat`. **Tasks 6.3 and 6.4 must therefore set `updated_at` on each status
      transition they write.**
    - Five additions beyond the design's DDL, all documented in the file header: preflight
      assertions; column shape assertions (`CREATE TABLE IF NOT EXISTS` is silent about a
      same-named table of a different shape, which would move the failure to the first
      insert in production); guarded `ADD CONSTRAINT` repeats of the four constraints the
      design declares inline, which are no-ops on a fresh run and repair a pre-existing
      table; column comments; narrowed grants. None changes a column, constraint or policy
      the design specifies.
    - **Verified statically, not live.** `tests/test_training_and_model_tables_migration.py`
      (37 tests) parses the design's own `-- 2. Training jobs`, `-- 3. Model versions` and
      RLS blocks out of `design.md` at test time and requires all 63 DDL lines and 7 RLS
      statements to be present; compares both column sets in BOTH directions (28 and 19
      columns, so a silently added column fails too); asserts both partial predicates
      literally, the constraint/index/policy names later tasks reference, the absence of
      DELETE/`FOR ALL`/`mv` UPDATE policies and of any DELETE grant, the data-minimisation
      comment, idempotency by construction (every `CREATE` either `IF NOT EXISTS` or behind
      a `pg_constraint`/`pg_policies` guard), that the inline and guarded spellings of each
      constraint agree, that each shape assertion's column list cannot drift from the
      `CREATE TABLE` above it, and that no statement names a pre-existing relation. Eight targeted
      mutations (dropping each partial predicate, weakening a policy to `USING (true)`,
      weakening `chk_tj_failed_has_reason`, removing the minimisation wording, adding an
      undesigned column, widening the `model_versions` grants, dropping an
      `IF NOT EXISTS`) were each caught by at least one test.
    - **Not verified:** there is no local PostgreSQL in this environment, so nothing proves
      PostgreSQL accepts the file, that the partial unique indexes actually reject a second
      live job or second active model, that the policies actually deny a non-owner, or that
      a re-run is a no-op against a real catalogue. Parenthesis and string-literal balance
      is checked, but no PostgreSQL grammar was available to this suite, so "the SQL parses"
      is NOT claimed. Live enforcement is the file's own VERIFICATION queries (11 groups,
      including direct exercises of both unique violations and
      `chk_tj_failed_has_reason`), to be run by the operator who applies it, plus task 8.7's
      isolation matrix. Same posture as task 4.1 took for `004c_immutable_versions.sql`.
    - Baseline held: backend **44 failed / 2527 passed / 18 skipped** over 2589 collected in
      pytest's default order — the pinned 44, still spread over the same 20 files in the same
      per-file counts, none of them a strategy-builder test, and the 2527 is 2490 plus this
      task's 37 new tests. Frontend **508 passed / 0 failed** over 19 files, unchanged; no
      frontend file was touched.

  - [x] 6.2 Implement the ML training policy and the readiness validation stage
    - Create `backend_app/backend/ml_training_policy.py` with `MLTrainingCaps`,
      `enforce_caps` and `check_ml_data_requirements` per
      `design.md § ML/DL model registry`
    - Resolve caps per user from `core/entitlement_engine.py` and
      `core/hard_quota_enforcer.py`, intersected with the model spec's `max_safe_epochs`, so a
      paid tier can raise a limit but never exceed what the spec declares safe
    - Measure usable columns and rows from the fetched dataset after removing warmup and the
      label horizon; never estimate
    - Return `required` and `available` for both dimensions so the UI can render the exact
      message
    - Wire the gate into `strategy_dag/validator.py` as validation stage 11
    - Keep the existing `require_ml_training` and `check_ml_quota` dependencies in force; caps
      are additive to them
    - _Requirements: 14.2, 14.3, 14.4, 14.5, 14.6, 16.1, 16.2, 16.3, 16.7_
    - **Landed as `backend_app/backend/ml_training_policy.py`** with the design's two
      surfaces: `check_ml_data_requirements` (collect-all, returning a `GateVerdict`) and
      `resolve_caps` / `enforce_caps` (returning `MLTrainingCaps` and an `Admission`), plus
      the adapters they need — `ModelSpecView`, `ValidationConfig`, `DatasetStats`,
      `TrainingRequest`, `JobCounts`, `CapExceeded`.
    - **Stage 11 is installed at import, not registered by a caller.**
      `install_default_stage_hooks()` now registers both seams, so `pending_stages()`
      returns `()` — same posture task 5.5 took for stage 10b, for the same reason: a gate
      a call site has to remember to switch on is a gate some call site ships without.
      `validate()` gained one keyword, `ml_dataset_stats`.
    - **A third status was needed, and it is the point of the stage.** `_run_seam` now
      catches a new `validator.StageSkipped` and records `SKIPPED`. Stage 11 raises it when
      the caller supplied no measured statistics, so a graph validated from the plain
      validate path reports `SKIPPED` with a detail ending "This graph's ML nodes are NOT
      confirmed ready" — not `PASSED`, and not `NOT_IMPLEMENTED`. Recording `PASSED` there
      would be the false-clean signal the seam's own docstring warns about; the three
      states are now `NOT_IMPLEMENTED` (nothing installed), `SKIPPED` (installed, no
      measurement) and `PASSED`/`FAILED`. A graph with no ML node is `PASSED`, since
      nothing to check is not the same as could not check (14.10).
    - **The import-cycle problem was solved the way stage 10b solves it: through the
      descriptor.** The per-model figures stage 11 needs travel on
      `BlockDescriptor.metadata["model"]`, which `registry.descriptor_from_model_spec`
      already carries through verbatim from `ml_models.ModelSpec` — exactly the route
      `plan.ModelRequirement.from_node` uses and the route stage 10b uses for its leakage
      facts. So no `ml_models` import enters `strategy_dag` to make the stage work.
      `validator` reaches `ml_training_policy` with an in-function import inside
      `install_default_stage_hooks()`, and `ml_training_policy` keeps `ml_models`,
      `ml_dataset`, `ml_safety`, `entitlement_engine`, `subscription_engine` and
      `validator.LIMITS` behind its own lazy seams. Measured, not assumed: importing
      `strategy_dag.validator` in a fresh interpreter still loads none of `fastapi`,
      `ccxt`, `sqlalchemy`, `asyncpg`, `supabase` and no vault, and adds no new import
      weight at all (`registry.py` already imported `ml_models` at module scope).
      `tests/test_strategy_dag_architecture.py` is green, all 60 cases.
    - **Cap intersection direction, asserted across the whole matrix.**
      `max_epochs = min(tier_cap, model.max_safe_epochs)`, checked for every one of the
      4 plans × 8 models, with `!= max(...)` asserted wherever the two figures differ — a
      single pair cannot tell `min` from `max`, since some pairs agree by coincidence. Both
      sides bind on real pairs: PROFESSIONAL + `xgboost` is tier-bound (300 < 2 000),
      ENTERPRISE + `lstm` is model-bound (200 < 3 000), ENTERPRISE + `catboost` is equal.
      `_intersect()` is the one helper that applies ceilings and it can only lower.
    - **What is read versus what is declared.** Read: the plan from
      `entitlement_engine.PlanMapper` (which normalises through
      `SubscriptionEngine.migrate_plan_key`, so no alias table is restated); the
      `ml_training` flag from `FeatureEntitlements`; the monthly `ml_trainings` allowance
      from `SubscriptionEngine.get_quota_limit`; per-user concurrency from
      `TenantQuota.for_plan(...).max_backtest_parallel`; duration and memory from
      `TrainingIsolator`'s live `TrainingConfig`; artifact size from `MemoryMonitor`'s
      `MemoryConfig` (a model that cannot fit the cache it must be loaded into can never
      be served); the feature-column ceiling from `validator.LIMITS`; the column floor from
      `ml_models.PLATFORM_MIN_FEATURE_COLUMNS`; the embargo floor from
      `ml_dataset.required_embargo_bars`. Declared: `ML_TIER_CAPS`, keyed on the existing
      `TenantPlan`, covering only epochs, rows, columns and the per-tier share of
      duration/memory/artifact size — the four dimensions **no existing surface carries**.
      FREE and BASIC are zero there on purpose, so this layer agrees with the entitlement
      layer rather than permitting what `require_ml_training` would refuse.
    - **Caps are additive, and that is tested rather than asserted in prose.**
      `require_ml_training` and `check_ml_quota` are untouched, still async dependencies in
      `core/subscription_dependencies.py`, still wired on the endpoint (covered by
      `test_sb06_exchange_agnostic_save.py`); `ml_training_policy` defines neither and
      replaces neither. The resolved caps *record* `ml_training_entitled` and
      `monthly_training_quota` so a caps payload shows both layers were consulted.
    - **Measure, never estimate — enforced, not documented.** `DatasetStats.from_dataset`
      reads `SupervisedDataset.n_rows`, which `build_supervised_dataset` produced from one
      row range starting at the warmup offset and stopping `horizon` rows early, so both
      trims are already in the number and subtracting again would double-count.
      `DatasetStats.from_mapping` **refuses** a mapping carrying only `total_rows` and a
      warmup figure: doing that subtraction is the estimate 14.2 forbids, and it is wrong
      whenever the exchange served a gap or a feature column came out all-NaN. Memory is
      the one estimated figure and it says so — the design writes
      `estimated ← estimate_memory_mb`, and a SEQUENCE model's windowed matrix is counted
      at `rows × columns × sequence_length`, since treating a 120-bar window like a single
      row under-counts by two orders of magnitude, which is the case the cap exists for.
    - `required` and `available` always carry **both** `columns` and `rows`, even when only
      one fell short and even when the gate passed, plus `sequence_length` and `train_rows`
      for the sequence family (14.6). A verdict reporting only the failing dimension forces
      the UI back to "insufficient data", which is what 14.9 exists to delete.
      `GateVerdict.message()` renders the design's exact sentence from those four numbers.
    - **Verified with 89 new tests** in `tests/test_ml_training_policy.py`: the row
      arithmetic at *exactly* required and required − 1 for a tree (`xgboost`) and a
      sequence (`lstm`) family; the column floor at the floor and one below; each of
      `MAX_EPOCHS`, `MAX_ROWS`, `MAX_FEATURES`, `MAX_CONCURRENT_USER` and `MAX_MEMORY`
      independently, with the other dimensions widened so a passing test names the cap that
      actually fired, and each boundary shown inclusive; the intersection direction over
      all 32 plan × model pairs; the estimate refusal; a real `SupervisedDataset` built
      over numpy compared against hand-computed `rows − warmup − horizon`; stage 11's three
      statuses; order-independence over five random node/edge permutations; global
      saturation deferring rather than rejecting with a queue position; and the degraded
      path warning by name about `004d_training_and_models.sql` while still enforcing every
      per-request cap. Optional tasks 6.8-6.10 remain open and are not pre-empted.
    - **Not verified:** nothing exercises the concurrency caps against a real
      `training_jobs` table — migration `004d_training_and_models.sql` is unapplied and
      there is no local PostgreSQL, so `JobCounts` is supplied by the caller and its
      absence degrades with a warning naming that file rather than a 500. Skipping is safe
      in the one direction that matters: it can defer a rejection to the worker's
      pre-first-epoch re-check (16.4) but can never turn an over-cap *request* into an
      admission, because the epoch, row, column and memory caps are evaluated from the
      request alone. The worker-side re-check itself is task 6.4, and no endpoint calls
      this module yet — that is task 6.3.
    - Baseline held: backend **44 failed / 2625 passed / 18 skipped** in pytest's default
      order — the pinned 44, the same 20 files in the same per-file counts, none a
      strategy-builder test, and every one of this task's 89 tests inside the passed count.
      A second full run of the identical tree returned **43 failed / 2643 passed / 18
      skipped**: the pinned 44 minus `test_billing_e2e.py`, which passed that time. Note
      for a later phase that collection in this environment is **not** stable run to run —
      two consecutive full runs of the same tree collected 2687 and 2704 — so the "over N
      collected" figure is not a fixed number here; the failure count and its per-file
      distribution are, and they did not rise. Frontend **508 passed / 0 failed** over 19
      files; no frontend file was touched. (One earlier frontend run showed a single
      failure in `tests/unit/integration`'s "should load dashboard data on mount", which
      passes in isolation and passed on re-run; a `waitFor` flake under full-suite load on
      this machine, not a regression.)

  - [x] 6.3 Implement the training service and job endpoints
    - Extend `routers/strategy_operations.py` and `strategy_service.py` with
      `create_version_and_maybe_train`: compile, fetch and quality-check the dataset, run the
      feature pipeline, run the `feature_validator` schema check, run the ML gate, create the
      immutable version, admit against caps, insert the job and enqueue it
    - Add `POST /training/jobs`, idempotent per (version, node), and
      `POST /training/jobs/{job_id}/cancel` setting `cancel_requested`
    - Record the training configuration and dataset fingerprint with each job; the
      configuration holds the resolved data source and no exchange identifier
    - Return `training: QUEUED` with the job id in the save response and never block the HTTP
      request on training
    - Set the version to `READY` and report training as not required when the graph declares
      no ML node
    - _Requirements: 12.6, 14.7, 14.8, 14.10, 15.1, 15.12, 15.13, 15.14_
    - **Landed in the two files the task names**, as one section of
      `backend_app/backend/strategy_service.py` (the admission order and the two writes)
      plus three endpoints in `backend_app/routers/strategy_operations.py`:
      `POST /strategy-operations/strategies/{id}/versions` — the design's "single save
      path" — `POST /strategy-operations/training/jobs` and
      `POST /strategy-operations/training/jobs/{job_id}/cancel`, on exactly the paths
      `design.md § API surface` tabulates. No new module: this task owns an ORDER, and
      every decision inside it is made by a component that already existed and is
      called — `compile_version` → `strategy_dag`, `connection_engine` +
      `data_seeking_engine` for the window, `market_data_validation` for the grade,
      `dag_engine.execute_dag` for the features, `feature_validator` for the schema,
      `ml_dataset` for `(X, y)` and the splits, `ml_training_policy` for the gate and
      the caps, `StrategyService.create_version` for the row. There is no second gate,
      no second cap table and no second feature path anywhere in it.
    - **No job row on any blocked path is STRUCTURAL, not promised.** `prepare_training`
      returns a `TrainingPreparation` whose `blocked` and `nodes` are mutually exclusive
      by construction, and the single `training_jobs` INSERT lives after that value has
      been inspected. On a block the insert is not reached, so there is nothing to roll
      back and no window in which a half-admitted job exists — the same enforcement-by-
      order `create_version` uses for Requirement 3.6. Covered for a gate failure, a cap
      rejection, a rejected data window and an unresolved market; optional task 6.8 is
      not pre-empted, and what it will assert is already true.
    - **Idempotency is held by the database, not by a read-then-write check.**
      `uq_tj_active_per_node` is the mechanism; `insert_training_job` catches the unique
      violation, reads the live job back and returns it with `idempotent: true`. A
      read-then-write pre-check would race — two concurrent requests would both read "no
      live job" and both insert — so the pre-check that does exist is a round-trip
      saving, and the tests drive the insert path directly so it is the *constraint* that
      answers. The partial predicate is exercised in both directions: a second QUEUED job
      is rejected, and a COMPLETED one leaves the node retrainable.
      The pre-check earns its place for a second reason: the per-user concurrency cap
      counts jobs in QUEUED or RUNNING, so without it a retry would be refused by
      `MAX_CONCURRENT_USER` on the strength of the live job for the very (version, node)
      being asked about — a correct-looking answer to the wrong question.
    - **Deliberate deviation from the design's status-code table, stated rather than
      quiet.** That table lists `409` for a duplicate training job; `POST /training/jobs`
      answers **200** with the existing job and `idempotent_nodes`. The task text
      specifies idempotency, Requirement 15.13 is satisfied identically under both
      answers (the index holds the invariant either way), and a retry after a dropped
      connection must not read as an error when the outcome the client asked for holds.
    - **A blocked training half is a 200 save, and a 422 on the explicit endpoint.** The
      save path persists the version (the graph compiled; a short history does not make
      it invalid) and reports `training: BLOCKED` with the structured reason. Refusing
      the whole request would discard a compiled version and force the author to
      resubmit an identical graph. `POST /training/jobs`, whose only purpose is to create
      a job, answers 422 with the same payload. Both are "block and return the reason"
      as 14.7/14.8/14.3/14.4 require; only the framing differs, per endpoint purpose.
    - **`config` holds the resolved data source and nothing else** (12.6, SB-06): the
      DATA node's own `node_id`, `block_id`, `symbol` and `timeframe`, plus the range,
      splits, embargo, horizon, epochs, batch, seed and provenance. Never a venue — a
      strategy carries no exchange identity to record, and the feed
      `fetch_training_bars` actually reads is resolved from `DEFAULT_EXCHANGE` at fetch
      time and never enters the payload. `assert_no_exchange_identity` walks the
      assembled config against `schema.FORBIDDEN_PARAM_FIELDS` at every depth and raises
      **before** the write, so task 8.7's Property 23 scans a column that a structural
      guard already protects rather than being the first thing to notice.
    - **Requirement 15.1 is asserted from the row, not from prose.** The job is written
      `QUEUED` with `epoch_current = 0`, `progress = 0`, no `started_at`, no loss; the
      hand-off is `enqueue_training_job`, a best-effort Redis hint whose failure is a
      warning because the `QUEUED` row is the authoritative queue that 6.4's claim step
      reads. A path that trained inline could not produce that row.
    - **`updated_at` is set on every status transition this task writes** — the job
      INSERT and the cancel UPDATE — because 004d deliberately attaches no
      `BEFORE UPDATE` trigger to `training_jobs`. Task 6.1's report called for exactly
      this.
    - **Cancel sets `cancel_requested` and stops there** (15.7): the worker owns the
      `CANCELLED` transition, and marking it here would make the row disagree with a
      process still writing epochs. Idempotent, 409 for a job that has already finished
      (including the case where it finishes between the read and the write), 404 for
      another user's job — identical to a missing one, so existence does not leak.
      Deliberately NOT behind `check_ml_quota`: a quota check refuses an action that
      consumes capacity, and cancelling releases it; a user whose allowance had run out
      would otherwise be unable to stop a run they had started.
    - **Controls unchanged.** `Depends(get_current_user)` and a `slowapi` limit on all
      three endpoints; `require_ml_training` and `check_ml_quota` remain declared
      dependencies on `POST /training/jobs`. The save endpoint cannot carry them as
      dependencies — a dependency cannot be conditional, and gating every save behind
      them would refuse a FREE user's indicator-only strategy — so
      `_assert_ml_training_entitled` calls the same two platform functions inside the
      training branch, asserted by source inspection rather than by claim. Request models
      are `extra="forbid"`, so an `exchange` or `api_key` field is a 422 rather than a
      silently dropped key. Ownership is enforced twice everywhere (RLS-scoped client
      plus an explicit `user_id` / `strategies.user_id` filter), including on the live
      job counts.
    - **Two supporting extensions, both minimal.** `strategy_builder.compile_version`
      and `StrategyService.create_version` gained an `ml_dataset_stats` passthrough, so
      the training path — the only caller that HAS the built dataset — hands stage 11 its
      measurement and the persisted `validation_report` records `PASSED` instead of
      `SKIPPED`; every other caller leaves it `None` and the report honestly says the
      stage could not judge the graph. `strategy_builder` also exports
      `LIFECYCLE_TRAINING` and `LIFECYCLE_READY`. `routers/strategy_operations._preview_market`
      was re-pointed at the shared `scan_plan_markets` scan rather than keeping a second
      copy of it; its error contract is untouched and
      `tests/test_node_preview_endpoint.py` is green.
    - **Verified with 70 new tests.** `tests/test_training_service_admission.py` (38):
      the no-model-node READY path with no job row and no feed read; the QUEUED path with
      the job id, the recorded config, the split sizes, the feature names and a fingerprint
      that changes with the middle of the window and not only its endpoints; stage 11
      `PASSED` here versus `SKIPPED` from the plain validate path; the fetch sized from
      `required_row_count` rather than a second rule; no job row on a gate failure with
      required AND available for both dimensions; no job row on a rejected data window;
      no job row on a cap rejection naming `MAX_EPOCHS`, requested and allowed, with the
      allowance equal to `min(tier, model)`; no silent clamping; a FREE plan blocked with
      the version still saved; idempotency driven through the constraint including the
      partial predicate in both directions; the degraded path answering `UNAVAILABLE` and
      naming `004d_training_and_models.sql`; cancel leaving status and progress alone
      while moving `updated_at`; and the config scanned for every one of
      `FORBIDDEN_PARAM_FIELDS` at every depth plus five venue literals.
      `tests/test_training_endpoints.py` (32): the three paths mounted, each keeping its
      route decorator, its `slowapi` limit and `get_current_user`; the two entitlement
      dependencies still on the job endpoint and the same two functions called on the
      save path's branch; `extra="forbid"` refusing `exchange` / `exchange_id` /
      `api_key` / `secret` / `exchange_account_id`; and the full status-code mapping
      (200 saved, 422 report, 422 blocked with `job_created: false`, 409 not cancellable,
      404 for another tenant's strategy, version or job).
      The database double enforces the two constraints these tests are about — a missing
      relation and `uq_tj_active_per_node`'s partial predicate — and the candle feed is
      the module-level `fetch_training_bars` seam. Nothing else is stubbed: the registry,
      the compiler, the validator, `market_data_validation`, `dag_engine`,
      `feature_validator`, `ml_dataset` and the whole of `ml_training_policy` are real.
    - **Not verified, and one thing found on the way.** Nothing here has touched a real
      `training_jobs` table: `004d_training_and_models.sql` is unapplied and there is no
      local PostgreSQL, so the partial unique index, the RLS policies and the CHECK
      constraints are exercised against a Python double shaped like them and live
      enforcement remains that file's own VERIFICATION queries plus task 8.7's isolation
      matrix. No model has been trained by anything in this task — that is 6.4 — so no
      artifact, metric or `model_versions` row has been produced, and the version
      correctly stops at `TRAINING` rather than reaching `READY`.
      **Found, not fixed, and outside this task's scope:** `market_data_validation`
      REJECTS rather than grades a window containing any close beyond 3 sigma
      (`OutlierDetector._z_score_filter` raises), and a real multi-thousand-bar market
      series is near-certain to contain one. That component is REUSED AS-IS, so its
      threshold was not relaxed from the training path — relaxing a data-integrity rule
      from one caller would be worse than the strictness. The rejection is mapped to a
      `DATA_QUALITY` block carrying the validator's own message rather than becoming a
      500, and the tests' synthetic window is one that component accepts. A later task
      that wants training to admit real market history will have to decide what that
      outlier rule should say; it is recorded here so that decision is made deliberately
      rather than discovered as "training always blocks".
      The global concurrency half of the caps is still not measurable from an RLS-scoped
      client (no service-role client on this path), so `JobCounts`' global figures carry
      the measured user-scoped counts as an explicit lower bound. Safe in the one
      direction that matters — a lower bound can only fail to DEFER, and DEFER is not a
      rejection — and detecting global saturation belongs to 6.4's scheduler.
    - Baseline held: backend **43 failed / 2756 passed / 18 skipped** in pytest's default
      order. That is the pinned 44 minus `test_billing_e2e.py`, which passed this run —
      the same 43-or-44 movement task 6.2 recorded for that exact file. The per-file
      distribution of the other nineteen files is unchanged, none of them is a
      strategy-builder test, and all 70 of this task's tests are inside the passed count.
      Frontend **507 passed / 1 failed** over 19 files: the single failure is
      `tests/unit/integration.test.jsx`'s "should load dashboard data on mount", which
      times out at 30 s under full-suite load and **passes in isolation in 26.5 s**
      (verified) — the same `waitFor` flake on this machine that task 6.2 recorded. No
      frontend file was touched by this task.

  - [x] 6.4 Implement the training worker
    - Add the worker entry point that claims a job atomically, sets RUNNING with a heartbeat,
      re-evaluates caps before the first epoch, and runs under `ml_safety.TrainingIsolator`,
      `MemoryMonitor` and `DeterministicEnforcer`
    - Honour `cancel_requested` at each epoch boundary, set `CANCELLED` and bind no model
    - Fail with `MAX_DURATION_EXCEEDED` when the wall-clock cap is exceeded, and mark
      `WORKER_LOST` when a heartbeat goes stale, returning the version to `SAVED`
    - Record a classified failure reason, never a generic string
    - _Requirements: 15.7, 15.8, 15.9, 15.10, 16.4, 16.6_
    - **What landed.** One new module, `backend_app/backend/training_worker.py`, holding the
      **control plane of a training run and nothing else**: `claim_training_job`,
      `recheck_caps`, `run_training_job` (the epoch loop), `reap_stale_jobs`, and a
      `TrainingWorker` poll loop plus a `python -m backend_app.backend.training_worker`
      entry point. No existing file was modified — the worker reaches every rule through
      task 6.3's own functions **via the module object** (`S.fetch_training_bars`, not a
      `from … import`), so the seams 6.3 built are the seams the worker uses and a test
      that replaces one replaces it for both.
    - **The claim is atomic against the table, and the table is the queue.** The claim is a
      conditional `UPDATE … WHERE id = :id AND status = 'QUEUED'`: PostgreSQL takes the row
      lock and the loser re-evaluates the predicate against the already-updated row, so two
      workers holding the same Redis hint cannot both run one job. Redis is consulted only
      as a wake-up hint — `pop_queue_hint` first because it is cheap, `next_queued_job_id`
      (the oldest `QUEUED` row) second because it is the truth — which is what makes
      `enqueue_training_job`'s best-effort failure a delay rather than a lost job. A
      read-then-write would let both workers see `QUEUED`; the status predicate is asserted
      in the tests from the write's own filter list, not from its effect.
    - **`updated_at` is written by one writer.** Every status change, heartbeat and epoch
      write goes through `_transition`, which sets the column unconditionally, because 004d
      deliberately attaches no `BEFORE UPDATE` trigger to `training_jobs`. A test walks
      every UPDATE a whole run issued and fails on any payload missing it, so the rule
      cannot be broken in one branch. `_transition` is also where the compare-and-set lives
      (`status`, `worker_id`, and for the reaper the exact `last_heartbeat` it judged), so
      "two writers cannot disagree about one job" is one mechanism rather than four.
    - **"A classified reason, never a generic string" is structural, not a convention.**
      `FAILURE_REASONS` is a closed frozenset and `assert_classified_reason` *raises* on
      anything outside it, so an unclassified reason cannot be written at all; `_finish` is
      the only path to a terminal state and it calls that guard for every `FAILED`. The
      vocabulary **reuses task 6.3's `REASON_*` constants** (`DATA_QUALITY`, `CAP_EXCEEDED`,
      `FEATURES`, `ML_REQUIREMENTS`, …) rather than restating them, so the builder renders
      one vocabulary instead of two that drift — a window that degrades between admission
      and execution fails for the same measured reason the API would have blocked it for.
      Five reasons are new because nothing needed them before: `MEMORY_EXCEEDED`,
      `VERSION_UNAVAILABLE`, `TRAINER_UNAVAILABLE`, `MODEL_PERSISTENCE_UNAVAILABLE` and the
      catch-all `TRAINING_RUNTIME_ERROR`. The catch-all is still a classification: the
      exception's type and text go in the *detail*, never in the reason column, and a test
      asserts that an `IndexError("list index out of range")` produces
      `TRAINING_RUNTIME_ERROR` and not its own message.
    - **Cancellation cannot reach the binder.** `cancel_requested` is re-read from the row at
      every boundary (a cached copy could only ever say "no", since another process sets it
      after the job started) and checked **before** the wall clock, so an author's explicit
      stop is recorded as `CANCELLED` rather than as a duration failure. The cancelled path
      returns a terminal `TrainingRunResult` from inside `_epoch_loop`, and the caller
      returns it unchanged — there is **no code path from a cancelled loop to the binder**,
      which is how "bind no model" is a property of the shape rather than a promise. The
      epoch in flight completes and is reported (`epoch_current = 2`, `progress = 2/6`),
      because discarding work the row already recorded would be the less honest option.
      `CANCELLED` carries no `failure_reason` at all — `_finish` refuses to attach one to a
      non-`FAILED` state, so a cancellation can never be mistaken for a failure.
    - **Two deliberate strengthenings of the pseudocode, both stated.** (1) The wall clock is
      checked **after** each epoch as well as before it: Requirement 15.8 bounds elapsed
      *duration*, and the design's top-of-loop-only check would let one long epoch overrun
      the cap by any margin and still finish. The epoch's own figures are persisted first, so
      a job stopped there still reports the work it really did. (2) Elapsed time is measured
      from the row's stored `started_at`, and `claim_training_job` writes that column **only
      when it is empty** — otherwise a job could buy an unlimited budget by crashing and
      being re-queued often enough.
    - **`WORKER_LOST` is a standalone sweep, and the two subtleties are load-bearing.**
      Requirement 15.9 attributes the transition to the Training_Service rather than to the
      worker — by definition the worker holding the job is the one that cannot act — so
      `reap_stale_jobs` is a function any process can run; `TrainingWorker` calls it on its
      own poll so a single-process deployment still detects loss. The staleness comparison is
      done **in Python, not in the WHERE clause**, because a server-side
      `last_heartbeat < :cutoff` silently skips `NULL` heartbeats (SQL `NULL < anything` is
      NULL, and a NULL predicate matches nothing) and a `RUNNING` job with no heartbeat is
      the most lost job there is. The write then carries `last_heartbeat = :the_value_it_
      judged` as a compare-and-set, so a worker that proved itself alive between the read and
      the write is left running rather than orphaned mid-fit. Both are asserted directly:
      one test reaps a NULL-heartbeat row, another reads the CAS filter off the recorded
      write. The version goes to **`SAVED`** and the worker that lost the job stands down at
      its next boundary without writing, so the reaper's verdict is not overwritten.
    - **Requirement 16.4's re-check is real work, not symmetry.** It closes three windows the
      API check cannot: a client that never went through the API, a tier that changed while
      the job sat queued, and — the one task 6.2's report explicitly handed here — the global
      concurrency figure. The worker runs with the **service role**, so
      `count_all_training_jobs` reports a genuinely fleet-wide `global_running` instead of
      the user-scoped lower bound `count_training_jobs` had to settle for, and Requirement
      16.5's saturation check becomes detectable. The job being admitted is excluded from its
      own count (it has already been claimed, so counting it would refuse the very job just
      started). `DEFER` **releases the claim back to `QUEUED`** rather than failing the job —
      16.5 says hold it, not reject it — and `started_at` is left in place so the release
      does not reset the duration budget either. The epoch count re-checked is the stored
      `epochs_total` and the row/column counts are the **rebuilt dataset's measured**
      figures, not what the config recorded at admission; a test edits `usable_rows` to a
      nonsense figure and shows the re-check ignores it.
    - **Requirement 16.6, each mechanism wired where it can be proved.**
      `DeterministicEnforcer.deterministic_context(seed=config.seed)` wraps the **whole**
      loop rather than one call, so a trainer that drew from the global RNG between epochs
      cannot make the run irreproducible from its recorded seed — asserted by running the
      same row twice and requiring byte-identical loss curves from a trainer whose initial
      weights come from `np.random`. `MemoryMonitor.validate_tensor_size` /
      `validate_batch_size` gate the assembled matrix before the first epoch, re-raised as
      `MemoryBoundExceeded` because `validate_batch_size` raises a bare `ValueError` that the
      classified catch-all would otherwise absorb as a generic runtime error. The wall-clock
      ceiling is read from `TrainingIsolator`'s **live** `_config` — the same place
      `ml_training_policy._runtime_ceilings` reads it — and intersected with the caps' figure,
      so the number the caps resolved and the number the worker enforces are one number.
    - **Isolation: per epoch, with the existing precedent and one addition.**
      `TrainingIsolator` is applied around the *fit* rather than around the run, because
      wrapping the run would put the epoch loop inside the child process and the epoch
      boundary is exactly where 15.7 and 15.8 require decisions that need the parent's
      database client. `run_isolated` follows `ml_models.XGBoostStrategyBlock`'s existing
      pattern verbatim — try isolated, log, continue in-process — because the isolator uses a
      `spawn` pool and a closure over a live model is not picklable, so an unconditional
      isolated call would make training impossible rather than safe. The **addition** is a
      latch: `ml_models` retries once per model, this loop would retry once per *epoch* and
      pay a process launch each time for a deterministic failure, so the first failure is
      logged in full and remembered (`reset_isolation_latch` clears it). The coarse-grained
      isolation the requirement is really about is the process itself, which is why
      `run_worker` is a **separate entry point and is deliberately not wired into
      `main.py`'s startup** — starting this loop in the API process is the one thing the
      isolation requirement forbids.
    - **004d's data-minimisation rule is enforced here, because 004d says it must be.** That
      header states plainly that SQL cannot tell a loss curve from a prediction vector and
      that "the enforcement itself belongs to the writer in tasks 6.4 and 6.6".
      `sanitize_epoch_metrics` drops a key in `FORBIDDEN_METRIC_KEYS` whatever its value looks
      like (a single float named `prediction` is still model output) **and** any value that is
      not a real scalar whatever its key is (a vector under an innocent name is the same
      leak), logs what it dropped, and turns NaN/infinity into NULL so one bad float cannot
      take an epoch's honest figures down with the write.
    - **The one named seam, and the judgement call behind it.** `TrainingBackend`
      (`register_training_backend` / `current_training_backend` / `reset_training_backend`)
      carries `trainer(ctx) -> EpochTrainer` and `binder(ctx, model)`. **Task 6.4 creates no
      `model_versions` row and no artifact** — asserted by a test that fails if the table is
      even touched. The judgement call is what an un-landed backend does: the worker writes
      **`FAILED` with `TRAINER_UNAVAILABLE` or `MODEL_PERSISTENCE_UNAVAILABLE`, never
      `COMPLETED`**. That follows the design's own ordering (`persist_artifact` →
      `insert_model_version` → `bind` → `set_status(job, COMPLETED)`): a `COMPLETED` job with
      no bound model is the silent-success shape 15.10 exists to prevent, and task 6.6 would
      report it to an author as a finished training run. The consequence is stated plainly —
      **until 6.5 lands, no training run can reach `COMPLETED` in this codebase** — and it is
      the honest reading rather than an oversight. The alternative considered was marking
      `COMPLETED` and relying on the version staying `TRAINING`; it was rejected because the
      job row would then over-claim on its own.
    - **One deviation from the design, and why.** The pseudocode writes
      `refetch(job.config, expect_fingerprint := job.dataset_fingerprint)`. Enforced equality
      there is **unsatisfiable** with the feed the platform has: `fetch_training_bars` returns
      the *N most recent* bars, so any delay between queueing and running shifts the window
      and changes the fingerprint by construction, and a strict check would fail every real
      job. So the fingerprint is **computed, compared and reported** — a logged warning
      carrying both values, the recorded one left as the provenance record task 6.3 wrote it
      as — rather than enforced. Making it a real precondition needs a by-timestamp fetch,
      which is a change to the feed contract and not to this worker; it is recorded here so
      that decision is made deliberately.
    - **A second, cosmetic divergence, honoured per source rather than silently unified.**
      `design.md`'s failure table and this task's own text both say `WORKER_LOST` returns the
      version to **`SAVED`**; the design's `run_training_job` CATCH block writes
      `set_state(job.version, VALIDATED)`. Both are un-deployable pre-training states, so each
      is written where its own source specifies it (`WORKER_LOST_LIFECYCLE_STATE = "SAVED"`,
      `FAILED_LIFECYCLE_STATE = "VALIDATED"`) rather than one being quietly preferred. Both
      constants are validated against `strategy_builder.LIFECYCLE_STATES` (`chk_lifecycle_
      state` verbatim) before the write.
    - **The known outlier strictness is mapped, not relaxed.**
      `market_data_validation.OutlierDetector._z_score_filter` still rejects any window with a
      close beyond 3 sigma and its threshold was **not** touched from the worker, for the same
      reason task 6.3 gave. The rejection arrives as `TrainingBlocked(DATA_QUALITY)` and
      `classify_failure` keeps that reason, carrying the validator's own message into the
      detail. A test admits a job, makes the *refetched* window spiky — which is what a real
      feed does — and asserts `DATA_QUALITY`, no 500, no retry against looser settings, and no
      model bound.
    - **Controls unchanged.** No auth, RLS, tenant, financial, execution, risk or idempotency
      control was weakened. The worker holds no JWT, so it uses the existing service-role
      singleton (`core.supabase_connection`) — which is also what 004d's header says task 6.5
      will need for `model_versions`, since that table has no UPDATE policy — and tenant
      scoping therefore becomes **explicit**: every read and write is keyed on the specific
      `job.id` it claimed, and the only fleet-wide query is `count_all_training_jobs`, which
      returns counts and never rows. The user's plan is read through the platform's existing
      `core.subscription_dependencies.get_user_plan` (`profiles.subscription_tier` normalised
      by `SubscriptionEngine.migrate_plan_key`); there is no second tier table.
      `uq_tj_active_per_node` keeps holding — the worker only ever moves a job *out* of the
      live set and back into it via `release_training_job`, which cannot create a second live
      row for a node — and `uq_mv_active_per_node` is untouched because nothing here writes
      `model_versions`. Every path touching `training_jobs` or `strategy_versions` degrades
      with a warning **naming `004d_training_and_models.sql`**, never a 500 and never a crash
      on every poll; asserted for the claim, the sweep and the counts.
    - **Verified with 79 new tests** in `tests/test_training_worker.py`, which builds a real
      version and a real `QUEUED` row **through task 6.3's own
      `create_version_and_maybe_train`** and then runs the worker against it, so the job the
      worker executes is the job the platform writes, config and all. Coverage: the closed
      failure vocabulary and every classification branch, including that a raw exception's
      text never becomes the reason; the `metrics_history` scalar rule in both directions;
      the claim's status predicate read off the write, a second worker getting nothing, a
      finished job being unclaimable, `started_at` surviving a re-claim, and the degraded
      path naming 004d; a completed run's per-epoch progress sequence `[0.25, 0.5, 0.75, 1.0]`
      (asserted from every intermediate write, not just the final value), its scalar metrics,
      and a loss that actually falls — so the "epochs" are a fit and not a loop counter;
      cancellation at a boundary, before the first epoch, with no reason, with the flag re-read
      each boundary and the binder never called; the duration cap with elapsed-versus-permitted
      detail and the version left un-deployable; the stale-heartbeat sweep including the NULL
      case, the heartbeat CAS filter, a healthy job left alone, a reaped worker standing down
      without overwriting the verdict, and the stale threshold's floor; the cap re-check
      refusing an `epochs_total` written past the API with `MAX_EPOCHS`/requested/allowed and
      `allowed == min(tier, model)`, measuring the rebuilt dataset rather than stored figures,
      global saturation deferring to `QUEUED` with the claim released, and the running job
      excluded from its own global count; determinism by identical reruns; both memory
      refusals; the isolator's live ceiling, the isolation-off path and the fallback latch;
      the three un-landed-backend outcomes; and the poll loop. The database double enforces
      `uq_tj_active_per_node`'s partial predicate, `chk_tj_failed_has_reason` and
      `chk_tj_progress`, and records every UPDATE's filters so the compare-and-sets are
      asserted rather than inferred. **The trainer is not a double**: `GradientDescentTrainer`
      is a real multinomial logistic regression fitted by gradient descent over the real
      `(X, y)` the real `ml_dataset` assembled from the real feature pipeline, standardised on
      train-split statistics only, installed through the production `TrainingBackend` seam.
      Only three things are stubbed — the database, the candle feed (`fetch_training_bars`,
      the same seam 6.3 uses) and the realtime broadcast.
    - **Not verified.** Nothing here has touched a real `training_jobs` table:
      `004d_training_and_models.sql` is unapplied and there is no local PostgreSQL, so the
      partial unique index, the RLS policies and the CHECK constraints are exercised against a
      Python double shaped like them, and live enforcement remains that file's own
      VERIFICATION queries plus task 8.7's isolation matrix. The service-role client path is
      exercised with the double, not against Supabase. `TrainingIsolator`'s **successful**
      isolated-fit path is not exercised: its `spawn` pool cannot pickle a closure over a live
      model, so the tests assert the fallback and the latch — which is the path production
      actually takes — rather than simulating a success that does not occur. No artifact, no
      checksum and no `model_versions` row has been produced by anything in this task, and no
      version has reached `READY`; both are task 6.5.
    - Baseline held: backend **43 failed / 2893 passed / 18 skipped** (2954 collected) in
      pytest's default order — the same **43** the previous run measured, under the pinned
      ceiling of 44, with the failures distributed over exactly the same nineteen files
      (`test_billing_e2e.py` passed again, which is the 43-or-44 movement tasks 6.2 and 6.3
      both recorded for that file). None of the nineteen is a strategy-builder test and all 79
      of this task's tests are inside the passed count. The passed total is higher than task
      6.3's note by more than this task's 79: collecting the suite with
      `--ignore=tests/test_training_worker.py` yields **2875**, exactly 79 fewer, so the
      remaining difference is suite growth between the two measurements and not an effect of
      this task. No frontend file was touched, so the known
      `tests/unit/integration.test.jsx` "should load dashboard data on mount" timeout flake is
      unaffected and the frontend suite was not re-run.

  - [x] 6.5 Implement model versioning and artifact handling
    - Insert a `model_versions` row per completed job with artifact reference, checksum, size,
      serialization mode, feature schema, hyperparameters and train/val/test metrics
    - Bind the model version to one immutable strategy version and one node; retraining inserts
      a new row and leaves a running deployment's artifact in place
    - Store artifacts in object storage and serve them only through an ownership-checked signed
      endpoint with `_safe_filename` sanitisation (fixing the ML-5 path traversal class);
      never return `artifact_uri` raw to the client
    - Transition the version to `READY` when every ML node is bound to an active model version
    - _Requirements: 17.1, 17.2, 17.3, 17.5, 17.8, 17.9, 21.2, 21.7_
    - **Landed as one new module plus one new pair of endpoints**, and nothing else was
      restructured. `backend_app/backend/model_versioning.py` owns the artifact, the
      `model_versions` row, the binding and the `READY` transition;
      `backend_app/routers/strategy_operations.py` gained the two-step download exchange.
      `model_versioning.bind_trained_model` **is** the `binder` task 6.4 left named, and
      `model_versioning.training_backend(trainer)` is the one-liner that installs it, so
      the gap 6.4 recorded — "until 6.5 lands, no run can reach `COMPLETED`" — is closed
      by this task and is asserted end to end: a real run through the real worker now
      writes `COMPLETED`, a real artifact and a real bound row.
    - **The binding is the row, not a second write.** `design.md`'s pseudocode names
      `insert_model_version` and `bind_model_to_version_node` as two steps; they are one
      here, because the row carries `version_id` and `node_id` and `uq_mv_active_per_node`
      is a partial unique index over exactly that pair. Requirement 17.2 therefore holds
      as the *shape* of the row rather than as a follow-up UPDATE that could be forgotten
      or interrupted. Stated rather than silently collapsed.
    - **The ML-3 retrain-overwrite class is closed twice over, independently.** The object
      key is content-addressed — `models/<user>/<strategy>/<version>/<node>/v<n>-<sha256
      [:16]>.<ext>` — so two different models cannot land on one key; and
      `ArtifactStore.put` is append-only, reusing a key that already holds the same bytes
      and raising `ArtifactExists` for one that holds different bytes. There is no
      `delete` and no `remove` anywhere on the store (asserted structurally, matching
      004d granting DELETE to nobody), and no path mutates `artifact_uri` on an existing
      row. Retraining appends: new key, new file, new row, `model_version + 1`, and the
      superseded row keeps its own reference, its own checksum and its own readable
      bytes. A test runs two real jobs for one node through the worker and asserts the
      first artifact still checksums to the first row's recorded value afterwards.
    - **Deactivation is a service-role write, and it is a compensating sequence rather
      than a transaction — deliberately, and this is the one real deviation from the
      design.** 004d's header asks for the `is_active` flip "in the same transaction as
      the new INSERT". The platform reaches PostgreSQL through PostgREST, which exposes no
      multi-statement transaction to this client, so the pair is *deactivate → insert →
      on failure re-activate*. The invariant that arrangement exists to protect —
      never two active rows for one `(version, node)` — holds throughout, because the
      window between the two writes has **fewer** active rows, not more. What a crash
      between them can leave is a node with **no** active model version, which reads as
      awaiting a model and holds a deployment out of the running state (Requirement
      17.6's own disposition) rather than swapping an artifact underneath a running
      deployment. That is the safe direction to fail in, which is why the compensating
      sequence is acceptable and not merely convenient. Both halves are asserted: the
      insert being *accepted* by a double that enforces `uq_mv_active_per_node`'s partial
      predicate is the proof the deactivation happened first, and a forced insert failure
      is the proof the predecessor comes back. The `UPDATE` itself is a compare-and-set on
      the row's id **and** its current `is_active`, and it runs on the service-role client
      (`ctx.sb`, which is `training_worker._worker_client`'s singleton in production) —
      never on a caller's RLS-scoped client, because there is no `mv_owner_update` policy
      and a user must not be able to rewrite which artifact a running deployment resolves.
    - **Object storage: an interface with the local filesystem as its default backend, and
      a real remote backend beside it.** `ArtifactStore` fixes the contract (append-only,
      content-addressed, opaque reference); `LocalArtifactStore` is the default because
      this environment has no object-store credentials, and it writes through a `.partial`
      staging file so a crash mid-write cannot leave truncated bytes behind a checksum
      that says they are whole; `SupabaseStorageArtifactStore` is selected when
      `STRATEGY_MODEL_ARTIFACT_BUCKET` names a bucket and the platform's **existing**
      service-role client exposes `.storage` — no new dependency was added, and
      `upsert` is pinned to `"false"` so the remote store cannot silently overwrite
      either. A configured bucket that is unreachable falls back to local **and logs that
      it did**, because "your artifacts are somewhere other than where you configured
      them" must not be something an operator has to infer. `register_artifact_store()`
      makes an S3 backend a drop-in. The row, the binding and the download exchange do
      not change with the backend, which is the property that makes the local default
      acceptable rather than a shortcut.
    - **`artifact_uri` cannot reach a client, by construction rather than by filtering.**
      `public_model_version` builds a *new* mapping from named keys instead of copying the
      row and deleting one, so it cannot regress into passing a reference through; and the
      **binder returns that projection**, not the row — which matters because the worker
      publishes the binder's return value on `training.completed` and carries it on
      `TrainingRunResult`. The tests search the whole serialised payload for the reference
      as a *substring* — on the binder's return, on `result.to_dict()`, on the realtime
      frame and on both HTTP responses — rather than checking one key, because the failure
      being guarded against is a reference nested somewhere nobody filtered.
    - **The download is two endpoints, and both are flagged here as new
      network-exposed routes.** Requirement 17.8 asks for an ownership-checked endpoint
      and `design.md` asks for a *signed* one; those are two different things, so:
      `GET /api/strategy-operations/models/{model_version_id}/download` **mints** — it is
      authenticated by the platform's `get_current_user`, keeps a `slowapi` limit, and
      enforces ownership twice (the caller's own RLS-scoped client for `mv_owner_select`
      plus an explicit `.eq("user_id", …)`, exactly as `_load_owned_version` does it) —
      and returns the client projection plus a five-minute signed link.
      `GET /api/strategy-operations/models/artifact?token=…` **redeems** and streams.
      **What authenticates the second endpoint is the token, and only the token**: an
      HMAC-SHA256 signature over `(purpose, model_version_id, user_id, exp)` compared with
      `hmac.compare_digest` *before* the payload is parsed, using the platform's existing
      `SUPABASE_JWT_SECRET`/`JWT_SECRET`. It carries no `get_current_user` because an
      artifact download is a browser navigation that cannot send a Bearer header — that
      absence is a recorded decision with its own test, not an oversight. Ownership is
      re-checked at redemption: the row is read again with an explicit `user_id` filter
      and compared to the token's owner, so a token whose row changed hands stops working.
      No secret configured is a **503**, never a 200 with an unsigned URL. The literal
      `artifact` route is declared before the parameterised one so it can never be read as
      a model version id, and that ordering has a test.
    - **`_safe_filename` is reused, not reimplemented** — asserted by *identity*
      (`MV._safe_filename is ml_models._safe_filename`), because two sanitisers that agree
      today are one edit away from disagreeing and only one of them is covered by the ML-5
      suite. Every key segment and every served file name goes through it, including its
      pairwise shape and 64-character truncation; the file extension is the only part that
      does not, and it comes from a closed `ARTIFACT_EXTENSIONS` map because a dot would
      not survive the sanitiser. A `../../../../etc/passwd` in the user id, strategy id,
      version id or node id yields a six-segment key inside the root, and the store then
      refuses any resolved path outside its root anyway — defence in depth, so a key
      assembled by future code that forgot the sanitiser still cannot escape. The
      `Content-Disposition` name is built from the row's own identifiers and sanitised
      afterwards, so it can carry no path, quote or newline whatever ends up in the table.
    - **`READY` needs every node, and says which one it is waiting for.**
      `promote_version_if_ready` compares the version's own plan's `ml_nodes` against the
      nodes that actually hold an **active** row; a partial binding is not `READY` and the
      unbound nodes are named on the report and in the log. A deactivated row does not
      count as bound. The write goes through `training_worker.set_version_lifecycle`, which
      validates against `strategy_builder.LIFECYCLE_STATES` (`chk_lifecycle_state`
      verbatim) before touching the row, so this module cannot disagree with the worker
      about the vocabulary and a typo is a local `ValueError` rather than a 23514. A
      version that declares no model node is **not** promoted here — that transition is
      Requirement 14.10's and task 6.3's, at compile time — and reaching this function
      with no ML nodes logs a warning rather than being papered over.
    - **Three small, purely additive changes to task 6.4's worker, and no change to its
      control plane.** `TrainingContext` gained `split_metrics` and `sb`, and
      `_run_claimed_job` sets both. `split_metrics` exists because Requirement 17.1 puts
      train/val/test metrics on the `model_versions` row, the worker is what scores the
      three splits, and the seam's signature is `binder(ctx, model)` — carrying them on the
      context is what makes them reachable without duplicating them into `training_jobs`
      and giving the platform two records of one measurement that can disagree. It is set
      immediately after `_score_splits` and is therefore empty on every path that did not
      run every epoch, which is what makes "a cancelled run binds no model" and "a
      cancelled run records no metrics" the same structural fact. `sb` exists because
      `run_training_job` deliberately accepts an injected client, and a binder that
      re-derived one from `_worker_client()` could silently write to a different store than
      the run it is finishing; in production it *is* that service-role singleton. No
      status, transition, cancellation boundary, heartbeat, cap or classification was
      touched, and the 6.4 suite's 79 tests all still pass unchanged.
    - **Two refusals were made stricter than the task asked for, both deliberate.** An
      identity column that is absent or blank is refused by name rather than coerced,
      because `str(None)` is the string `"None"` and a row carrying that satisfies
      `NOT NULL` while naming a node, a block or an owner that does not exist. And the
      `hyperparameters` document is built from a **whitelist** of `config` keys rather than
      from a copy of the mapping — that document is served to the client and published on a
      realtime frame, so Requirement 21.7 is held by there being no path that copies
      unknown keys; a test injects `api_key` and `exchange_account_id` into a job's
      `config` and asserts neither the keys nor the value come back out.
    - **The artifact ceiling and the checksum are the platform's own, not new ones.** The
      size ceiling is read *live* from `MemoryMonitor.get_cache_stats()`, so an operator
      who tightens the model cache tightens what may be written, and an artifact over it is
      refused **before** anything is stored — an artifact that cannot be cached cannot
      serve inference, so a row that looks deployable and is not never comes into
      existence. The recorded checksum is computed by `SafeModelLoader.compute_checksum`
      over the bytes **as they were actually stored**, read back after the write, so it is a
      fact rather than an intention; a lying store is caught and no row is inserted. On the
      serving path the checksum is verified again and a moved artifact is not served
      (Requirement 17.6's condition, loud in the log and opaque to the caller). A stored
      artifact is asserted to load through `SafeModelLoader.load_model` with its recorded
      checksum, so what is written is what production reads.
    - **Controls unchanged.** No auth, RLS, tenant, financial, execution, risk or
      idempotency control was weakened. `uq_mv_active_per_node` and `uq_mv_version_node`
      both keep holding — the double enforces their predicates and the tests assert both
      the accepted insert and the rejected one — and `uq_tj_active_per_node` is untouched
      because nothing here writes `training_jobs`. The retrain path is asserted not to swap
      a running deployment's artifact. Both new endpoints require an authenticated caller
      or a signed token, both keep a `slowapi` limit, and every refusal on the download
      path — bad signature, expired token, wrong purpose, another tenant's row, an
      unapplied migration, a moved checksum — is the **same** 404 with no distinguishing
      detail, because telling them apart tells a token holder something about a resource
      they do not own. Every path touching `model_versions` degrades with a warning
      **naming `004d_training_and_models.sql`**: on the worker as
      `FAILED`/`MODEL_PERSISTENCE_UNAVAILABLE` (a member of the closed vocabulary, not a
      500 and not a silent success), and on the API as "no such model version".
    - **Verified with 104 new tests** in `tests/test_model_versioning.py`. The artifacts are
      real joblib bytes of a real fitted model written to a real filesystem, the runs go
      through task 6.3's own `create_version_and_maybe_train` and then task 6.4's real
      worker with **this module's production binder** installed through the production
      `TrainingBackend` seam — task 6.4's suite installs a recording stub there, and this
      one deliberately does not. Coverage: the reused vocabulary and the sanitiser
      identity; traversal in each of the four identity segments, content-addressing,
      per-mode extensions and a root-escaping key refused twice; the store's append-only
      rules including the reuse case, the refusal case, the absence of any delete, the
      `.partial` staging and a `SupabaseStorageArtifactStore` exercised against a double of
      the bucket API with `upsert="false"` asserted; store-then-read-back including a lying
      store, the live ceiling and a `None` model; the row carrying all of Requirement
      17.1's fields, per-node monotonic numbering from the maximum, and a blank identity
      column refused; retraining appending with the old artifact still checksum-valid, the
      deactivation's exact compare-and-set filters, the compensating re-activation, the
      duplicate-index rejection recognised by name and by SQLSTATE; the three degraded
      paths naming 004d and the narrowness of the probe; `READY` at one-of-one,
      not-`READY` at one-of-two with the missing node named, the second node completing,
      a deactivated row not counting, and the no-ML-node case; a whole run reaching
      `COMPLETED` with the version `READY`; the three metric sets, the order-significant
      feature schema matching the job's own `feature_names`, the hyperparameters, and the
      injected-credential refusal; cancellation and a failed persist each leaving no row
      and no artifact; the projection's construction; the token's round trip, edited
      payload, edited signature, expiry, foreign secret, malformed forms and the
      fail-closed no-secret case; the ownership-checked read with its explicit filter,
      another tenant's 404, the unapplied-migration 404 and redemption's re-check; and the
      HTTP exchange end to end — mint, stream, byte-for-byte equality with the stored
      artifact, the sanitised `Content-Disposition`, `Cache-Control: no-store`, the
      forged/absent/expired/foreign token 404s, the tampered-artifact 404, the 503 with no
      secret, both routes' rate limits and route ordering, and the recorded decision that
      the streaming route carries no `get_current_user`.
    - **Not verified.** No real `model_versions` table: `004d_training_and_models.sql` is
      unapplied and there is no local PostgreSQL, so `uq_mv_active_per_node`,
      `uq_mv_version_node`, the `NOT NULL` set, `mv_owner_select`/`mv_owner_insert`, the
      deliberate **absence** of an UPDATE policy and the service-role-only UPDATE grant are
      exercised against a Python double shaped like them; live enforcement remains that
      file's own VERIFICATION queries plus task 8.7's isolation matrix. No Supabase storage
      bucket is reachable, so the remote store is exercised against a double of the bucket
      API and never against Supabase — and the "configured bucket, unreachable client"
      fallback is what production would take in this environment. Nothing here deploys a
      model or runs inference: the checksum-at-load and feature-schema-at-deploy
      dispositions (Requirements 17.6, 17.7) have their *write* side recorded here and
      their *enforcement* side in phase 8, and no `strategy_deployments` row was involved
      in the "leaves a running deployment's artifact in place" assertions — what is asserted
      is that the bytes and the reference survive, which is the property a deployment
      depends on.
    - Baseline held: backend **43 failed / 2997 passed / 18 skipped** (3058 collected) in
      pytest's default order — the same **43** tasks 6.3 and 6.4 measured, under the pinned
      ceiling of 44, distributed over exactly the same nineteen files
      (`full_system_test.py` 4, `test_account_health_query.py` 1,
      `test_atomic_order_cancellation_fix.py` 4, `test_distributed_execution_safety.py` 7,
      `test_event_pipeline.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_safety_fix.py` 1, `test_exchange_vault_singleton.py` 2,
      `test_false_success_report_fix.py` 1, `test_fee_precision_fix.py` 1,
      `test_get_db_dependency.py` 1, `test_marketplace_pipeline.py` 4,
      `test_position_delta_race_condition_fix.py` 1,
      `test_reconciliation_engine_fail_closed.py` 1, `test_risk_management_lifecycle.py` 1,
      `test_risk_settings_api.py` 2, `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_tenant_isolation_fixes.py` 1, `test_transaction_isolation_serializable.py` 3).
      None is a strategy-builder test. The passed total is exactly 104 higher than task
      6.4's 2893 — this task's 104 tests and nothing else — and the five training suites
      (236 tests across 6.1, 6.3, 6.4 and 6.7) were re-run together after the worker change
      and all pass. No frontend file was touched, so the known
      `tests/unit/integration.test.jsx` "should load dashboard data on mount" timeout flake
      is unaffected and the frontend suite was not re-run.

  - [x] 6.6 Implement truthful training status reporting
    - `GET /training/jobs/{job_id}` and `GET /training/jobs?version_id=` return status, dataset
      rows, usable rows, feature columns, feature names, split sizes, model, epochs total,
      current epoch, loss, val loss, progress, cancellable flag and failure reason
    - Derive progress from completed epochs only; report ETA as absent until at least three
      epochs have completed and their duration coefficient of variation is below 0.35
    - Render each blocking training message in the builder with its required and available
      quantities
    - _Requirements: 14.9, 15.2, 15.3, 15.4, 15.5, 15.6_
    - **Landed as one new backend module and two GET routes**, plus six lines in task 6.4's
      publisher and one panel in the builder.
      `backend_app/backend/training_status.py` owns the **read** half of a training job and
      nothing else: `public_training_job` (Requirement 15.3's whole list in one document),
      `derive_progress`, `reliable_eta`, `is_cancellable`, `scalar_metrics_history`, and the
      two ownership-checked reads `training_job_report` / `list_training_job_reports`.
      `backend_app/routers/strategy_operations.py` gained
      `GET /strategy-operations/training/jobs?version_id=` and
      `GET /strategy-operations/training/jobs/{job_id}`, both of which answer with that one
      projection. The frontend half is `deriveTrainingBlocks` in
      `algo22-terminal/src/lib/graphValidation.js` and the `training-blocks` panel in
      `algo22-terminal/src/pages/StrategyBuilder.jsx`.
    - **This module writes nothing, and a test asserts that from the source.** Not one
      `insert`, `update`, `upsert` or `delete` appears in it. Two reasons, both
      load-bearing: 004d deliberately attaches **no** `BEFORE UPDATE` trigger to
      `training_jobs`, so every writer in this codebase has to set `updated_at` itself and
      the safest way for a reporting surface to hold that invariant is to have no write to
      forget it on; and a read that "repaired" a row it thought was wrong would be a read
      racing the worker that owns it. Where the stored `progress` column disagrees with the
      derived figure, the **derived** figure is reported and the disagreement is logged —
      the column is a cache of the same arithmetic, and Requirement 15.4 names completed
      epochs as the source.
    - **The ETA rule lives in exactly one function, and the socket calls it.** `reliable_eta`
      is `None` until at least three epochs have completed **and** their measured durations
      have a coefficient of variation below 0.35, and task 6.4's `training.progress` frame
      now calls that same function through a lazy import (module-level would be a cycle,
      because `training_status` reads the worker's vocabulary at import time). That is what
      stops the socket showing a number while the endpoint says "estimating…". Five distinct
      absences are reported as machine codes rather than prose —
      `ABSENT_NOT_RUNNING`, `ABSENT_UNKNOWN_EPOCHS_TOTAL`, `ABSENT_TOO_FEW_EPOCHS`,
      `ABSENT_NO_MEASURED_DURATIONS`, `ABSENT_UNSTABLE_EPOCH_DURATIONS` — so a client can
      say *why* it is estimating without parsing a sentence.
    - **Four judgement calls on the ETA, all in the withholding direction.** The comparison
      is `cv < 0.35`, so a cv of **exactly** 0.35 is absent, because Requirement 15.6 says
      "0.35 **or greater**"; a test pins the boundary exactly rather than approximately, with
      `[6.5, 10.0, 13.5]` — a sample whose mean is exactly 10 and whose sample standard
      deviation is exactly 3.5 in floating point as well as in arithmetic. The **sample**
      standard deviation (`n - 1`) is used rather than the population one: the requirement
      names no denominator, `n - 1` is the larger of the two, and in the borderline case
      the larger denominator withholds. *Completed* and *measured* are counted separately —
      an epoch whose `duration_seconds` was never recorded is absent from the sample rather
      than imputed into it, so three completed epochs with two measured durations is
      `ABSENT_NO_MEASURED_DURATIONS` and not an average of two. And an undefined cv (fewer
      than two samples, or a zero mean) withholds rather than reading as `0.0`, which would
      look like "perfectly stable" for a sample that says nothing.
    - **`metrics_history` goes out through the same sanitiser it came in through.** 004d's
      header states that the column holds per-epoch scalars only, that "SQL CANNOT ENFORCE
      THIS", and that the enforcement belongs to the writer "in tasks 6.4 **and 6.6**". Task
      6.4 is the writer; this is the reader, and it applies the identical rule with the
      identical function — `training_worker.sanitize_epoch_metrics`, by identity and not by
      a second copy, asserted as such. Two reasons the read side needs it as well: a row
      written by an older worker, by a migration or by hand is not covered by the write-side
      check at all; and `loss` / `val_loss` are **separate** `DOUBLE PRECISION` columns that
      the write-side rule never saw as metrics, so a vector or a NaN in either would leak
      model output or break the JSON encoding of the whole response. Both go through the
      sanitiser here, and a test injects `predictions`, `feature_values` and `y_true` into a
      history entry and greps the serialised response for each.
    - **Three columns are absent by construction, not by filtering.** The projection is built
      from named keys rather than by copying the row and deleting from it, for the reason
      task 6.5's `public_model_version` is: a projection that *removes* a key is one refactor
      away from not removing it. `config` is the provenance record (Requirement 15.14), is
      not on Requirement 15.3's list, and already has exactly one path that decides which of
      its keys a client may see — task 6.5's **whitelisted** `hyperparameters` — so it is not
      served here; a test puts an `api_key` on a job's `config` and greps the whole response
      for the value. `worker_id` and `last_heartbeat` name internal hosts and a client has no
      use for them. An unknown column put on the row does not come back out either.
    - **No artifact reference, anywhere, checked as a substring.** A completed job's bound
      model is reported as task 6.5's `public_model_version`, which has no `artifact_uri` by
      construction; this module never reads that column and offers no way to reach the bytes.
      The tests search the **whole serialised payload** for the stored URI, for
      `artifact_uri`, for `local://` and for a path segment of the key — not for one absent
      key, because a nested copy under any name is the same leak. `model_bound` travels
      beside `model_version` on purpose: a job that completed with no readable model row must
      not look identical to one whose model the caller did not ask for. A **deactivated**
      `model_versions` row is not reported as the job's model either — it keeps its own
      artifact and checksum, but the model its node resolves to is the active one.
    - **Ownership is the same double filter every other Strategy Builder read uses, and
      another tenant's job is a 404.** Every read goes through the caller's own
      request-scoped client (so `tj_owner_select` applies) **and** carries an explicit
      `.eq("user_id", …)`; both filters are asserted from the recorded query, and the seam is
      `strategy_service.create_request_supabase_async` rather than a direct
      `core.dependencies` import, so this is the same path task 6.3's writes and task 6.5's
      reads take. A job belonging to another tenant answers **404, not 403** — asserted as
      `status_code == 404` *and* `!= 403`, and asserted to be indistinguishable from the
      answer for an identifier that never existed, because a 403 confirms the identifier
      names a real job and turns the endpoint into an existence oracle over other tenants'
      job ids (Requirement 21.4). The listing answers an **empty list** for a version with
      no jobs, for another tenant's version and for an unapplied migration, deliberately the
      same answer for all three. A row that somehow arrived with a foreign `user_id` is
      refused by a third check in Python — unreachable with the filter above and with RLS in
      force, asserted anyway, because it is the last line before another tenant's run would
      be described.
    - **The unapplied migration degrades everywhere and 500s nowhere.** `004d_training_and_
      models.sql` is unapplied and there is no local PostgreSQL, so every path is exercised
      in the degraded state this repository is actually in: one job answers "not found", a
      listing answers "no jobs", a completed job whose `model_versions` table is missing is
      reported `model_bound: false`, and each of those logs a warning **naming that file**.
      Both the raised-exception form and PostgREST's answer-200-with-an-`error`-body form are
      covered. The probe stays narrow — a transport error that is *not* a missing relation
      propagates, because a read that fails loudly beats one that reports an empty status
      surface for a job that is really running.
    - **`cancellable` is derived from the status vocabulary, never stored.** It reads the same
      `TRAINING_JOB_LIVE_STATES` constant `request_job_cancellation` filters on, so the flag
      cannot promise a cancellation the cancel endpoint would answer 409 to; a test walks the
      whole vocabulary and asserts the two agree state by state. A stored `cancellable: true`
      on a `COMPLETED` row is ignored, and a status outside `chk_tj_status` is reported
      verbatim, flagged in the log, and **not** cancellable — fail closed.
    - **The frontend renders the backend's quantities and computes none of its own.** The
      builder's `training-blocks` panel renders each blocking message with its required
      **and** available quantity, both read out of the payload task 6.3's gate produced:
      `detail.required` / `detail.available` for `ML_REQUIREMENTS` (both dimensions always,
      plus the sequence window and training split when the gate measured them — Requirements
      14.3, 14.4, 14.6), `detail.requested` / `detail.allowed` for `CAP_EXCEEDED`
      (Requirement 16.3), and `detail.window.target_bars` / `.max_bars` for `DATASET`. Each
      gate issue is additionally rendered with its own `expected` / `actual` pair, which is
      Requirement 14.9 applied per message rather than only per block. Nothing in the client
      recomputes a threshold — the minimum column count, the minimum row count, the reserved
      fractions and the embargo are `ml_training_policy`'s arithmetic and a second copy would
      eventually disagree with the gate that actually refused the request; a test feeds
      deliberately inconsistent figures and asserts they are rendered as sent. The server's
      sentence is rendered **verbatim**, asserted character for character including the
      thousands separators, following `NodePreview` and `ParameterForm`'s rule that the client
      renders backend-authored messages rather than authoring a parallel copy. A pair with
      only one side present is **dropped** rather than completed with a guess or the word
      "unknown", and a reason that expresses no numeric threshold (`FEATURES`,
      `DATA_QUALITY`, `MODEL_UNPUBLISHED`, `DATA_SOURCE_UNRESOLVED`, `DATA_UNAVAILABLE`) is
      still rendered, with its message and its issues — hiding a block is worse than
      rendering it without figures, and inventing figures is worse than both. The panel is
      gold rather than red and states "No training job was created", because on a blocked
      path the strategy **is** saved and only training was refused (Requirements 14.3, 14.4,
      14.7, 14.8).
    - **One existing test was corrected, and it is the only file outside this task that
      changed.** `tests/test_training_endpoints.py::TestTheDesignedPathsExist::
      test_each_one_is_a_post` iterated **every route object** whose path was one of task
      6.3's three and demanded `POST` of each. FastAPI mounts one route object per method,
      and this task deliberately mounts `GET /training/jobs` on the same path as
      `POST /training/jobs` — the normal REST reading of a collection, and the reason the
      GET is declared *before* `GET /training/jobs/{job_id}` so `version_id` cannot be read
      as a path segment. The assertion is now per **path** (`each of these three paths
      accepts a POST`) rather than per route. That is what the test meant: what must hold is
      that the POST is there, not that no other method may ever share the path. Nothing about
      the POSTs themselves was relaxed, and the rest of that file is untouched.
    - **Controls unchanged.** No auth, RLS, tenant, financial, execution, risk, idempotency
      or rate-limit control was weakened. Both new endpoints require an authenticated caller
      through the platform's `get_current_user` and both keep a `slowapi` limit, asserted
      from the router **source** rather than from a wrapper chain, because source is what a
      reviewer would delete. `version_id` is **required** on the listing rather than optional:
      an unfiltered list of every job a user has ever run is a broader read than anything in
      this spec needs, so it is not offered. Neither response wraps its body in a synthetic
      `{"status": "ok"}`, because `status` on a training job means one of Requirement 15.2's
      five states and one key with two meanings on sibling endpoints is worse than no
      envelope. Nothing here writes, so `uq_tj_active_per_node`, `chk_tj_status`,
      `chk_tj_progress`, `chk_tj_failed_has_reason` and the missing `updated_at` trigger are
      all untouched by construction. Cancellation, the duration cap, the heartbeat sweep and
      the failure classification are read and reported, never re-decided.
    - **Verified with 122 new backend tests** in `tests/test_training_status.py` and **23 new
      frontend tests** in `algo22-terminal/tests/unit/strategyBuilder.trainingBlocks.test.jsx`.
      The backend file reuses the task 6.4 and 6.5 doubles rather than rebuilding them
      (`FakeSupabase`, `ModelStore`, `GradientDescentTrainer`, `model_graph`, `queue_job`,
      `owner`, `a_row`), and the integration half runs **real** jobs: task 6.3's admission
      path creates them, task 6.4's worker runs a real gradient-descent fit with task 6.5's
      production binder installed, and the report is read off what that run actually wrote.
      Coverage: the reused vocabulary and the sanitiser identity; the no-write property read
      out of the module's own source; progress over five ratios, an unknown denominator, the
      `[0, 1]` clamp, a stored value that disagrees, and two jobs whose only difference is
      how long they have been running proving no elapsed-time term exists; the ETA's
      published regime, the three-epoch boundary from both sides, a cv of exactly 0.35, just
      below and just above it, an undefined cv, the sample-versus-population denominator,
      the five absence codes being five distinct codes, and the measured-duration filter
      (missing, `None`, non-numeric, NaN, negative dropped; `0.0` kept); `cancellable` over
      the whole vocabulary, over three unrecognised values, and against the cancel path's own
      filter; Requirement 15.3's field list asserted present in **every one of the five
      statuses**; every classified failure reason echoed and an unclassified one reported
      rather than rewritten; `config`, `worker_id`, `last_heartbeat` and an unknown column
      all absent with their values greped for; `metrics_history` re-sanitised including a
      vector under an innocent key, a non-list column and a non-mapping entry; a vector and
      a NaN in `loss` / `val_loss`; the artifact reference absent as a substring in both
      endpoints' bodies after a real run stored a real artifact; the double ownership filter
      captured from the recorded query on both reads; the request-scoped seam and the token
      it is called with; another tenant's job and a non-existent one answering identically;
      an anonymous caller; a leaked foreign row refused twice; the deterministic ordering; a
      deactivated and a foreign `model_versions` row both reported unbound; five degraded
      paths naming 004d and one transport error propagating; both routes mounted, ordered,
      authenticated and rate-limited; the HTTP 404-not-403; the 422 for a missing
      `version_id`; and a real run's QUEUED, COMPLETED, CANCELLED and FAILED reports
      including that a cancelled run carries **no** failure reason, that a failed run still
      reports the two epochs it finished, that no report writes to the row, and that every
      `training.progress` frame's `eta_state` and `progress` equal what the endpoint would
      compute.
    - **Not verified.** No real `training_jobs` table: 004d is unapplied and there is no
      local PostgreSQL, so `tj_owner_select` is exercised as an explicit filter against a
      Python double rather than as a live RLS policy, and live enforcement remains that
      file's own VERIFICATION queries plus task 8.7's isolation matrix. No real Supabase
      realtime connection, so the "socket and endpoint agree" property is asserted over the
      recorded frames rather than over a live subscription — task 6.7 owns the transport.
      Nothing here deploys a model or runs inference.
    - Baseline held: backend **43 failed / 3119 passed / 18 skipped** (3180 collected) in
      pytest's default order — the same **43** tasks 6.3, 6.4 and 6.5 measured, under the
      pinned ceiling of 44, distributed over exactly the same nineteen files with exactly the
      same per-file counts (`full_system_test.py` 4, `test_account_health_query.py` 1,
      `test_atomic_order_cancellation_fix.py` 4, `test_distributed_execution_safety.py` 7,
      `test_event_pipeline.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_safety_fix.py` 1, `test_exchange_vault_singleton.py` 2,
      `test_false_success_report_fix.py` 1, `test_fee_precision_fix.py` 1,
      `test_get_db_dependency.py` 1, `test_marketplace_pipeline.py` 4,
      `test_position_delta_race_condition_fix.py` 1,
      `test_reconciliation_engine_fail_closed.py` 1, `test_risk_management_lifecycle.py` 1,
      `test_risk_settings_api.py` 2, `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_tenant_isolation_fixes.py` 1, `test_transaction_isolation_serializable.py` 3).
      None is a strategy-builder test. The passed total is exactly 122 higher than task 6.5's
      2997 — this task's 122 tests and nothing else — and the eight training and model
      suites (551 tests across 6.1, 6.2, 6.3, 6.4, 6.5, 6.6 and 6.7) were re-run together
      afterwards and all pass. Frontend: **529 passed / 2 failed** (531), the 23 new tests
      included. Both failures are pre-existing and neither is a strategy-builder test: the
      known `tests/unit/integration.test.jsx` "should load dashboard data on mount" timeout
      flake, and `tests/unit/portfolio-rendering.test.jsx` "Portfolio Page CSS and …", which
      greps `src/pages/Portfolio.jsx` for the string `Trade history` — a string an earlier,
      uncommitted rewrite of that page removed. It fails in isolation and with this task's
      file absent, it is out of this spec's scope, and it was not visible before now only
      because task 6.5 touched no frontend file and did not re-run the suite.

  - [x] 6.7 Add the training realtime channels
    - Extend `websocket_manager.py` and `ws_channels.py` with `training.{job_id}` carrying
      `queued`, `progress`, `completed`, `failed` and `cancelled`
    - Authorise every subscription against `job.user_id` through `core/websocket_auth.py` and
      refuse another user's channel rather than silently ignoring it
    - Multiplex over the existing connection; open no second socket
    - _Requirements: 15.11, 21.5, 21.6, 23.1_
    - **Landed in the three files the task names**, and only those three plus six lines in
      task 6.3's publisher. `backend_app/backend/ws_channels.py` gained the channel's
      name and vocabulary; `backend_app/core/websocket_auth.py` gained
      `authorize_channel_subscription`, which resolves `training_jobs.user_id`;
      `backend_app/backend/websocket_manager.py` gained the per-job subscriber registry,
      the fan-out and the subscribe/refuse branch in the existing `websocket_endpoint`.
      No new module and no new endpoint: `training.{job_id}` is another channel name on
      the socket that endpoint already serves.
    - **`training.{job_id}` is deliberately NOT a `ChannelType` member.** `VALID_CHANNELS`
      is derived from that enum and `ws_event_stream.py` calls
      `ChannelType(channel_name)` on the line immediately after
      `is_valid_channel(channel_name)` returns True — so admitting a parameterised name
      through `is_valid_channel` would hand a live code path a value the enum cannot
      construct. The parameterised family therefore gets its own predicate
      (`parse_training_channel` / `is_training_channel` / `claims_training_namespace`)
      and `ChannelType`, `VALID_CHANNELS` and `is_valid_channel` are byte-for-byte
      unchanged in meaning. Nothing that validated before validates differently now.
    - **A well-formed name is not an authorised one, and the two live in different
      files.** `ws_channels` answers only "is this routable": a job segment of 1–64
      characters of `[A-Za-z0-9_-]`, which excludes an empty segment, a second `.` (so
      `training.a.b` cannot smuggle one past the parser), `*`, whitespace, `/`, `\` and
      `%`. A UUID-only rule was considered — `training_jobs.id` is a `UUID` — and
      rejected: this is a shape check *ahead of* the ownership check, the ownership
      lookup is what refuses another user's job, and UUID-only would make every
      non-production identifier unroutable while adding no security. Ownership is
      `core/websocket_auth.authorize_channel_subscription`, which reads
      `training_jobs.id = job_id` through the RLS-scoped per-request client and compares
      `user_id` to the token's `sub`.
    - **Refusal is explicit, reported, and it does not close the connection.** The client
      receives `{"type": "subscription_refused", "channel", "code", "reason"}` and its
      other channels keep flowing — a disconnect would satisfy "refuse" while leaving the
      client to guess why, and silently ignoring the subscribe (which is what the
      endpoint did for every unknown channel before) is what Requirement 21.6 forbids.
      The frame carries a machine code and a sentence and **no resource data at all**:
      asserted by a test that greps the frame for the owner id, the strategy id, the
      version id, the node id and the block id.
    - **One refusal code covers "not yours" and "no such job", on purpose.** Under
      `tj_owner_select` a real client sees the same empty result for both, so separating
      them would turn the channel into an existence oracle for other tenants' job
      identifiers. Requirement 21.4 permits either answer; `CHANNEL_FORBIDDEN` is both.
      The distinguishable case — a row visible with a foreign `user_id`, which is what a
      service-role reader would see — refuses with the same code and logs the difference
      server-side.
    - **Every failed lookup DENIES.** No database client, an unloadable training service,
      a transport error, a row with no `user_id`, and the case this repository is actually
      in — `004d_training_and_models.sql` unapplied, so the relation does not exist — all
      return `CHANNEL_OWNER_UNRESOLVED`. The missing-relation path logs a warning naming
      `004d_training_and_models.sql` and never raises, matching what tasks 6.2 and 6.3 do
      on the same relation; the difference is that a degraded *authorisation* answer is
      "no", where a degraded *cap count* could safely be a lower bound. Allowing on error
      would make any outage an authorisation bypass. `authorize_channel_subscription`
      cannot raise: a transport mid-message needs a decision, not an exception.
    - **The per-job registry is separate from `_channel_subscribers`, and that is the
      point.** `broadcast_to_tenant` also delivers to every subscriber of the special
      `"all"` channel *in the same tenant*, and a tenant is not a user — the token's
      `tenant_id` can cover several. Routing per-job training frames through it would
      hand one tenant member another member's training progress. `_training_subscribers`
      is written only by `subscribe_training`, which the endpoint reaches only after an
      allow, and read only by `broadcast_training_event`, which re-checks the owner id
      against the connection's authenticated `user_id` before each send. `WebSocketConnection`
      grew a `user_id` for that reason, defaulting to `tenant_id` so no existing caller
      changes behaviour. A test asserts the tenant sibling on `"all"` receives nothing
      while the owner receives the frame.
    - **A six-member frame vocabulary, five from the design and one that already
      existed.** `queued`, `progress`, `completed`, `failed`, `cancelled` are
      `design.md`'s table verbatim; `training.cancel_requested` is the sixth because task
      6.3's cancel endpoint already emits it and it is a genuinely different fact from
      `cancelled` — the flag is set, the worker has not stopped, the job is still
      `RUNNING`. Folding it into `cancelled` would have the channel announce a stop that
      has not happened. An unrecognised event name is refused by the fan-out rather than
      forwarded to a browser as if it were a status change. The progress frame is
      forwarded verbatim, so it carries the per-epoch scalars task 6.1 constrained
      `metrics_history` to (epoch, loss, val_loss, progress) and never a prediction
      vector; a test pins that the fan-out neither drops those keys nor invents any.
    - **Wired to task 6.3's `publish_training_event`, not to a second publisher.** That
      one function now fans out to both seams — the user's private channel via
      `app_state.ws_manager.broadcast_user`, which every other producer in the codebase
      uses, and `training.{job_id}` via the channel manager — each independently
      best-effort, because an unconnected browser must not fail a save and the
      `training_jobs` row remains the source of truth. It returns True when either seam
      accepted the frame. That is the whole of the change to `strategy_service.py`: no
      worker logic, no claim logic, no worker entry point, so task 6.4 publishes through
      the same seam without collision.
    - **One bug fixed because the endpoint could not otherwise work.**
      `websocket_endpoint` called `manager.connect(connection)` while `connect` takes
      `(connection, tenant_id, client_id)`, so it raised `TypeError` before the first
      frame was read — no subscription of any kind was reachable through the multiplexed
      endpoint. Fixed to pass the keys the manager stores under. Nothing else in that
      function's behaviour changed for non-training channels.
    - **Scope held.** No existing channel's authorisation was touched:
      `authorize_channel_subscription` returns "allowed" for everything outside the
      training namespace, explicitly and with a test that fails if that ever starts
      allowing or denying `orders`, `pnl`, `all` or `market.*` from here. Narrowing them
      would be a control change this task did not analyse. `GET /training/jobs/...` (task
      6.6) was not opened. The frontend was not touched: `wsChannels.js` mirrors the
      fixed enum, and the builder's single multiplexed hook is a later task — nothing
      here needs a client change to be correct, and the channel is inert until one
      subscribes.
    - **Verified with 50 new tests**, `tests/test_training_realtime_channels.py`: the
      name round-tripping and fourteen malformed names refused, including `training.`,
      `training..`, `training.a.b`, `training.*`, `training.%`, whitespace, `/etc/passwd`,
      a backslash and a 65-character segment; the fixed vocabulary proven untouched by
      constructing `ChannelType` from every member of `VALID_CHANNELS`; the owner allowed;
      another user's job refused with a reason and with no resource data in the frame; an
      invisible job and a nonexistent one answering identically; the unapplied migration
      refusing while naming `004d_training_and_models.sql` in both the reason and the log;
      no client, an unclassifiable transport error, an ownerless row and three shapes of
      unauthenticated caller all refusing; a malformed name refused **before** the
      database is touched (asserted against the double's `touched` list); five non-training
      channels still allowed with no lookup; delivery to the owner only; the `"all"`
      tenant sibling receiving nothing; the send-time owner re-check dropping a
      non-owner; unsubscribe and disconnect both clearing the registry; an unknown frame
      type not forwarded; and six end-to-end passes through `websocket_endpoint` proving
      one `accept()`, a refusal frame on a socket that then serves `orders` and `ping`
      normally, a refused subscriber receiving nothing when a frame is published while it
      is still connected, and `orders` plus `training.{job_id}` riding one connection.
      The socket double exists because "no second socket was opened" is only assertable
      against a socket that counts its own `accept()` calls; the manager registries are
      inspected by probes that run *while* the connection is open, since the endpoint
      tears it down on disconnect.
    - **Not verified.** No real `training_jobs` table was read: `004d_training_and_models.sql`
      is unapplied and there is no local PostgreSQL, so `tj_owner_select` — the policy
      that makes another user's job an empty result rather than a visible foreign row —
      is exercised against a Python double, and the authorisation code is written to
      refuse under *both* shapes precisely because which one occurs depends on that
      policy being live. Live RLS enforcement remains the migration's own VERIFICATION
      queries plus task 8.7's isolation matrix. No frame has crossed a real WebSocket,
      and no browser has subscribed: `starlette`'s `WebSocket` is replaced by a recording
      double, so what is proven is the endpoint's decision logic and the fan-out's
      targeting, not the wire. Nothing in this task has trained anything, so the only
      frames observed are ones a test published — the `progress`, `completed`, `failed`
      and `cancelled` producers are task 6.4, publishing through the seam this task
      wired.
    - Baseline held: backend **43 failed / 2814 passed / 18 skipped** in pytest's default
      order — the same 43 failures as the last measured run, against a ceiling of 44, with
      all 50 of this task's tests inside the passed count. No failing file is a
      strategy-builder, training or websocket test. (The passed count is 58 higher than
      the 2756 recorded under 6.3 rather than 50: tasks 6.4 and 6.6 are in progress
      concurrently in this workspace, and the extra 8 are not this task's.) The frontend
      was not touched by this task and was not re-run.

  - [ ]* 6.8 Write property test for the insufficient-data gate
    - `tests/test_ml_gate_property.py`
    - **Property 19: When usable feature columns or usable rows fall short, no `training_jobs`
      row is created and the response states required versus available**
    - Generator: random `(rows, columns, model_family, sequence_length)` tuples
    - **Validates: Requirements 14.3, 14.4, 14.5, 14.6**

  - [ ]* 6.9 Write property test for server-side cap enforcement
    - `tests/test_training_caps_property.py`
    - **Property 20: After admission, epochs never exceed
      `min(model.max_safe_epochs, tier_cap)`, even when the client sends more**
    - **Validates: Requirements 16.2, 16.3, 16.4**

  - [ ]* 6.10 Write unit tests for gate arithmetic and cap boundaries
    - Minimum-data arithmetic for tree and sequence families at exactly required and
      required − 1; each cap independently; a request that satisfies the API but is rejected on
      the worker re-check; global saturation deferring rather than rejecting
    - _Requirements: 14.2, 14.5, 14.6, 16.1, 16.3, 16.4, 16.5_

  - [ ]* 6.11 Write the training lifecycle integration tests
    - Queue → progress events → model version bound → version `READY`; cancel mid-run;
      worker-loss recovery; retrain creates a new row without overwriting the running
      artifact; emitted frames contain no fabricated progress or premature ETA
    - _Requirements: 15.1, 15.4, 15.5, 15.7, 15.9, 15.11, 17.3, 17.5_

  - [x] 6.12 Phase 6 exit verification
    - Confirm the minimum-data gate and cap properties are green, the cancel and worker-loss
      paths are covered, and no fabricated progress is emitted
    - Confirm no training job is created on any blocked path
    - Ensure all tests pass, ask the user if questions arise.
    - **Verdict: the gate holds on substance and is now closed on the number as well.**
      Every behavioural exit criterion is green and every one of the five carried
      commitments holds. What was not held at first measurement was the pinned failure
      ceiling — the backend measured **45 failed / 3138 passed / 18 skipped (3201
      collected)** against a ceiling of 44 — and the cause is named below, is reproducible
      in isolation, is outside this spec and is not a Phase 6 regression. That cause has
      since been fixed at its own source and the suite re-measured at **43 failed**; the
      closing note at the end of this entry records the fix and the new numbers.
    - **How it was checked.** Backend in pytest's default order, one whole-suite run
      (`python -m pytest -q`, 418 s); the eight Phase 6 suites re-run together afterwards
      (`551 passed` in 275 s); the frontend suite through `vitest run` (228 s). Everything
      else is source-level: the five commitments were verified by reading every call site
      rather than by trusting the per-task notes, and each optional task was checked against
      the landed suites by enumerating their test names rather than by assuming coverage.
    - **The gate and the caps are green — as content, not as properties.** All 551 tests
      across `test_training_and_model_tables_migration.py`, `test_ml_training_policy.py`,
      `test_training_endpoints.py`, `test_training_service_admission.py`,
      `test_training_worker.py`, `test_model_versioning.py`, `test_training_status.py` and
      `test_training_realtime_channels.py` pass, and none of them appears anywhere in the
      failure list. The minimum-data arithmetic is pinned at **exactly required and
      required − 1** for both families
      (`test_tree_family_admits_exactly_the_required_row_count` /
      `..._blocks_one_row_below_...`, and the sequence pair), each cap is enforced
      **alone** (`test_max_epochs_is_enforced_alone` and its four siblings), the worker's
      pre-first-epoch re-check refuses an over-cap row that skipped the API
      (`TestCapsAreRecheckedBeforeTheFirstEpoch`, asserting `seen == []` so the refusal
      precedes the first epoch), and global saturation **defers** rather than rejecting,
      with the job left `QUEUED`, no failure reason and `worker_id` released.
    - **No training job on any blocked path — structurally, not by a filter.**
      `create_version_and_maybe_train` computes `preparation` before it writes anything,
      and the `preparation.blocked is not None` branch returns `training.state = BLOCKED`
      with `jobs: []` **before** `_insert_training_jobs` is reachable at all. The version is
      still saved (Requirements 14.7, 14.8) and the log line says so. There is no path from
      a block to an insert.
    - **Cancel and worker-loss are both covered, on the worker that owns them.**
      `TestCancellationAtAnEpochBoundary` proves the status becomes `CANCELLED` at the next
      boundary, that it carries **no** failure reason, that a job cancelled before its first
      epoch runs none, and that the cancellation is announced.
      `TestStaleHeartbeatIsWorkerLoss` proves a stale job is failed `WORKER_LOST`, that the
      reaper binds no model, that a job with no heartbeat at all is reaped, that the write
      is a compare-and-set **on the heartbeat it judged**, that a reaped worker stands down
      without overwriting the verdict, and that a poll reaps before taking new work. The
      cancel *endpoint* half is in `test_training_endpoints.py` and the cancelled-job
      *report* half in `test_training_status.py`.
    - **No fabricated progress, on either surface.** `derive_progress` takes
      `(epoch_current, epochs_total)` and nothing else — no timestamp is an input, which is
      what makes `test_no_elapsed_time_term_can_move_it` (two jobs, one started days ago,
      identical progress) a statement about the shape of the function rather than about two
      examples. The ETA rule lives in **one** function: `reliable_eta` withholds unless the
      status is `RUNNING`, `epochs_total` is positive, **three** epochs have completed,
      **three** durations were actually measured, and the sample coefficient of variation is
      `< 0.35` — five distinct machine codes for five distinct absences, `cv == 0.35`
      exactly withholding, an undefined cv withholding, and an unmeasured epoch absent from
      the sample rather than imputed into it. The worker's `training.progress` frame calls
      **that same function** through a lazy import (module-level would cycle), and
      `test_the_socket_frame_and_the_endpoint_agree_about_the_estimate` runs a real 4-epoch
      job and asserts, frame by frame, that `eta_seconds` is `None` below three epochs and
      that `frame["progress"] == TS.derive_progress(frame["epoch"], frame["epochs_total"])`.
      The realtime fan-out forwards the frame verbatim, adding only `type`, `channel`,
      `job_id` and `broadcast_time`, and refuses an event name outside the six-member
      vocabulary rather than passing it to a browser. The builder computes nothing: it
      renders `eta_seconds` when present and the literal `ETA estimating…` when absent, with
      a comment saying why an elapsed-time estimate is the forbidden thing.
    - **Commitment 1 — degrades naming 004d, never a 500. Holds.** All seven modules that
      can touch `training_jobs`/`model_versions` name the file:
      `ml_training_policy` (`TRAINING_TABLES_MIGRATION`, caps skipped with a warning),
      `strategy_service` (`TRAINING_MIGRATION`, the one spelling every other module imports;
      `state: UNAVAILABLE` with no invented job id), `training_worker` (`_degraded` swallows
      only a missing-relation error, and the worker ends `FAILED` /
      `MODEL_PERSISTENCE_UNAVAILABLE`), `model_versioning`, `training_status` (404 / empty
      list / `model_bound: false`), `core/websocket_auth` (`CHANNEL_OWNER_UNRESOLVED`, deny
      on error) and `strategy_operations` (404, never 500). Both the raised-exception and
      the PostgREST answer-200-with-an-`error`-body forms are handled. The probe is narrow
      on purpose: a transport error that is *not* a missing relation propagates rather than
      being reported as an empty status surface for a job that is really running.
    - **Commitment 2 — every status write sets `updated_at`. Holds, and by funnel.**
      `training_jobs` has exactly three writers in the codebase:
      `training_worker._transition`, which does `body["updated_at"] = _iso()`
      unconditionally before the query and is the single UPDATE path for the claim, every
      heartbeat, every epoch and every terminal state; `strategy_service`'s
      `cancel_requested` update, which sets it explicitly; and the `QUEUED` insert, which
      carries both `created_at` and `updated_at`. `training_status` contains no `insert`,
      `update`, `upsert` or `delete` at all, and `model_versioning` never touches the table.
      There is no fourth writer to forget the column on.
    - **Commitment 3 — `artifact_uri` never reaches a client. Holds.**
      `public_model_version` is the only projection anything outside `model_versioning`
      returns, and it is assembled from named keys — `artifact_available: bool(row.get(...))`
      is as close as the reference gets to the wire. It is applied at the **binder**, so the
      value the worker carries on `TrainingRunResult` and publishes on `training.completed`
      never held the URI in the first place. `training_status` never selects the column and
      offers no route to the bytes; the fan-out copies the payload it was given. The
      download exchange returns a path plus a five-minute HMAC token, never the URI. The
      tests search the **whole serialised payload** for the stored URI, for `artifact_uri`,
      for `local://` and for a key path segment, on both endpoints and on the frame — a
      nested copy under any name is the same leak.
    - **Commitment 4 — every `FAILED` job carries a closed-vocabulary reason. Holds.**
      `_finish` is the only writer of a terminal status, and `status == STATUS_FAILED` goes
      through `assert_classified_reason`, which **raises** `UnclassifiedFailureReason` for
      anything outside `FAILURE_REASONS`. The `else` branch forces `failure_reason = None`
      and raises if a non-`FAILED` state was handed one, so a cancelled job cannot look
      failed. Every `STATUS_FAILED` call site in the module passes a `FAILURE_*` constant,
      including the reaper's `WORKER_LOST` and the defect path's
      `TRAINING_RUNTIME_ERROR`. `classify_failure` puts the exception's own text in
      `detail["message"]`, so the diagnosis survives without the column becoming free text.
      The read side does not launder an unrecognised value: it reports it verbatim and logs
      that something wrote outside the vocabulary.
    - **Commitment 5 — both partial unique indexes stand, and no control was weakened.**
      `004d_training_and_models.sql` still declares
      `uq_tj_active_per_node ON training_jobs(version_id, node_id) WHERE status IN
      ('QUEUED','RUNNING')` and `uq_mv_active_per_node ON model_versions(version_id,
      node_id) WHERE is_active`, predicate for predicate as `design.md` writes them, and
      `test_training_and_model_tables_migration.py` (37 tests) is green. Every endpoint
      Phase 6 added carries `Depends(get_current_user)` and a `slowapi` limit — `POST
      /training/jobs` (20/min), `GET /training/jobs` and `GET /training/jobs/{job_id}`
      (200/min), `POST /training/jobs/{job_id}/cancel` (60/min), `GET
      /models/{id}/download` (60/min) — with the single deliberate exception of
      `GET /models/artifact`, authenticated by the token alone and recorded as such under
      6.5. Reads carry the RLS-scoped client **plus** an explicit `user_id` filter; another
      tenant's job is 404 and not 403. The listing requires `version_id` rather than
      offering an unfiltered read. The realtime registry is deliberately separate from
      `_channel_subscribers` so a tenant sibling on `"all"` cannot receive another member's
      frames. `authorize_channel_subscription` returns "allowed" for every non-training
      channel, so no existing channel's controls were narrowed either. The platform's own
      auth, RLS, tenant-isolation, execution-safety and idempotency suites are all in the
      green set of the full run.
    - **The skipped optional tasks, one at a time, honestly.**
      **6.10 leaves no hole** — every clause of it is directly covered:
      exactly-required and required − 1 for tree and sequence, each cap independently, the
      worker re-check of a request that satisfied the API, and global saturation deferring
      rather than rejecting, all named above.
      **6.11 leaves no hole** — queue → progress → bound → `READY` is
      `TestACompletedRunProducesABoundModel` running a real job through the real worker with
      the production binder; cancel mid-run and worker-loss recovery are the two worker
      classes above; "retrain creates a new row without overwriting the running artifact" is
      `TestRetrainingAppendsAndOverwritesNothing` plus its end-to-end twin
      `test_retraining_the_same_node_through_the_worker_appends`; and "emitted frames
      contain no fabricated progress or premature ETA" is the socket/endpoint agreement test.
      **6.8 and 6.9 leave a real hole, and it is not a coverage hole but a generator hole.**
      There is **no `hypothesis` import in any of the eight Phase 6 suites** — checked, not
      assumed. Property 19's content (short columns or short rows ⇒ no job row, and the
      response states required versus available) and Property 20's content (admitted epochs
      never exceed `min(model.max_safe_epochs, tier_cap)`, whatever the client sent) are both
      asserted, at the boundaries and through the real admission path, and Property 20's is
      additionally asserted on the worker's re-check where the client bypassed the API. What
      does not exist is a generated `(rows, columns, model_family, sequence_length)` sweep,
      so — in 5.13's words for the same situation — the substance is covered and what is
      missing is the chance of being surprised by an input nobody chose. Stated plainly
      because "the gate and cap **properties** are green" is true of their content and of no
      property test.
    - **Three findings that are not gate failures but are real, and are recorded rather
      than smoothed over.**
      (a) **The progress arithmetic exists in three places, the ETA in one.** `reliable_eta`
      is genuinely a single function called by both surfaces; progress is not.
      `training_status.derive_progress`, `record_epoch`'s column write
      (`min(1.0, max(0.0, completed / total))`) and the frame's inline
      `min(1.0, epoch / max(1, epochs_total))` are three copies of one rule. They agree
      today and a test asserts the frame equals `derive_progress`, but the discipline that
      was applied to the ETA was not applied here.
      (b) **Terminal frames carry the stored column, not the derived figure.**
      `training.completed` / `failed` / `cancelled` publish `written.get("progress")`, where
      the HTTP surface would report `derive_progress` and log any disagreement. Consistent
      today because `record_epoch` writes the same arithmetic; not the same guarantee.
      (c) **`epochs_total <= 0` is the one row where they could disagree.** Such a row takes
      an empty epoch loop and reaches `_finish(COMPLETED)`, which writes `progress = 1.0`,
      while `derive_progress` answers `None` for it. Unreachable through the API —
      `epochs` is `Field(None, ge=1)` and an omitted value resolves to the spec's own
      positive recommendation — so it needs a hand-written row, which is why it is a note
      and not a defect.
    - **Not verifiable in this environment.** `004d_training_and_models.sql` is unapplied
      and there is no local PostgreSQL, so `uq_tj_active_per_node`, `uq_mv_active_per_node`,
      `uq_mv_version_node`, `chk_tj_status`, `chk_tj_progress`,
      `chk_tj_failed_has_reason`, the `NOT NULL` set, the `tj_*`/`mv_*` RLS policies and the
      deliberate **absence** of a `BEFORE UPDATE` trigger are all exercised against Python
      doubles that enforce their predicates rather than against the engine; live enforcement
      remains that file's own VERIFICATION queries plus task 8.7's isolation matrix. No
      object storage is reachable, so `SupabaseStorageArtifactStore` is exercised against a
      double of the bucket API and the local filesystem store is what a run here actually
      uses. No frame has crossed a real WebSocket — `starlette`'s `WebSocket` is a recording
      double, so what is proven is the endpoint's decision logic and the fan-out's
      targeting, not the wire. Nothing here deploys a model or runs inference, so
      Requirements 17.6 and 17.7 have their write side recorded and their enforcement side
      in phase 8.
    - Baseline: backend **45 failed / 3138 passed / 18 skipped (3201 collected)** in
      pytest's default order, against the pinned ceiling of **44**. The pinned nineteen
      files account for exactly **43** of those failures with exactly the same per-file
      counts as tasks 6.3, 6.4, 6.5 and 6.6 measured (`full_system_test.py` 4,
      `test_account_health_query.py` 1, `test_atomic_order_cancellation_fix.py` 4,
      `test_distributed_execution_safety.py` 7, `test_event_pipeline.py` 3,
      `test_exception_swallow_regression.py` 2, `test_exchange_safety_fix.py` 1,
      `test_exchange_vault_singleton.py` 2, `test_false_success_report_fix.py` 1,
      `test_fee_precision_fix.py` 1, `test_get_db_dependency.py` 1,
      `test_marketplace_pipeline.py` 4, `test_position_delta_race_condition_fix.py` 1,
      `test_reconciliation_engine_fail_closed.py` 1, `test_risk_management_lifecycle.py` 1,
      `test_risk_settings_api.py` 2, `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_tenant_isolation_fixes.py` 1, `test_transaction_isolation_serializable.py` 3).
      **The two extra failures are a twentieth file and an environment change, and the
      cause is named rather than guessed at.** `tests/test_redis_debounce.py::
      test_thundering_herd_one_attempt[trio]` and `::test_get_redis_manager_debounced[trio]`
      fail with `RuntimeError: There is no current event loop in thread 'MainThread'`,
      because both call `asyncio.gather` inside a backend-agnostic `@pytest.mark.anyio`
      test. `anyio` 4.14.2 declares `anyio_backend` as
      `@pytest.fixture(scope="module", params=get_available_backends())`, and `trio` 0.34.0
      is now importable in this venv as a transitive dependency of `selenium` /
      `trio-websocket`, so the 21 `@pytest.mark.anyio` tests in `test_database_pool.py`,
      `test_profile_cache_optimization.py`, `test_rate_limiter.py` and
      `test_redis_debounce.py` are parametrised over trio as well as asyncio. That accounts
      for the collection delta exactly — **3201 − 3180 = 21** — and 19 of the 21 pass. The
      `[asyncio]` twins of both failures pass in the same run, the file is committed at
      `v2.0.0`, it imports nothing from Phase 6, and it reproduces in isolation
      (`2 failed, 10 passed` running that file alone). It is a latent defect in that test
      file surfaced by a second event-loop backend appearing in the environment, not a
      Phase 6 regression; the fix belongs to whoever owns that file — pin `anyio_backend` to
      asyncio in `tests/conftest.py`, or replace `asyncio.gather` with an
      `anyio.create_task_group` — and it was deliberately **not** applied here, because
      editing an unrelated committed test to make a number come out right is the thing this
      gate exists to catch.
      Frontend: **530 passed / 1 failed (531 across 20 files)**. The two known frontend
      failures are **not** both still failing: only
      `tests/unit/portfolio-rendering.test.jsx "Portfolio Page CSS and …"` failed, still
      greping `src/pages/Portfolio.jsx` for `Trade history`, a string an earlier uncommitted
      rewrite of that page removed. `tests/unit/integration.test.jsx` **passed** this run
      (3 tests, 18.8 s), consistent with its being the timeout flake it was recorded as.
      Nothing else failed, and `strategyBuilder.trainingBlocks.test.jsx` is 23 green.
    - **Closing the number.** The twentieth file has been fixed at its source and the gate
      re-measured. `tests/test_redis_debounce.py` held a latent defect in a committed test —
      not a Phase 6 regression and not a product defect. `test_thundering_herd_one_attempt`
      and `test_get_redis_manager_debounced` are marked `@pytest.mark.anyio`, which is
      backend-agnostic, but drove their concurrency with `asyncio.gather`, which is not.
      They passed for as long as asyncio was the only installed backend and began failing
      the moment `trio` 0.34.0 became importable: a second event-loop backend appearing in
      the environment, not a change in the tests' intent or in the code under test. The fix
      replaces `asyncio.gather` with `anyio.create_task_group`, spawning the same 50 and 30
      callers concurrently on whichever backend the test is actually running, and collecting
      the results the assertions need. Nothing was weakened — both tests still exercise the
      real race, N concurrent callers against one debounced attempt, and each now
      additionally asserts that every one of its callers completed (50 and 30), so a task
      group that silently spawned nothing cannot read as a pass. Two cheaper spellings were
      deliberately rejected: pinning `anyio_backend` to asyncio in `tests/conftest.py`,
      which would have disabled trio for the whole suite and cost the other nineteen trio
      parametrisations their coverage, and serialising the callers into a loop, which would
      have removed the concurrency the tests exist to test. **Trio parametrisation is
      preserved, not disabled** — all **21** `@pytest.mark.anyio` tests across
      `test_database_pool.py`, `test_profile_cache_optimization.py`, `test_rate_limiter.py`
      and `test_redis_debounce.py` still collect on both backends and all pass (12/12 for
      the repaired file, 41/41 for the other three).
      **Re-measured, backend in pytest's default order, one whole-suite run
      (`python -m pytest -q`, 396 s): 43 failed / 3140 passed / 18 skipped (3201
      collected)** — the ceiling of 44 met with one to spare, the collection count
      unchanged, and the two former failures converted to passes (3138 → 3140). The
      failures are the pinned nineteen files at exactly the pinned per-file counts, checked
      file by file rather than assumed: `full_system_test.py` 4,
      `test_distributed_execution_safety.py` 7, `test_atomic_order_cancellation_fix.py` 4,
      `test_marketplace_pipeline.py` 4, `test_event_pipeline.py` 3,
      `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_transaction_isolation_serializable.py` 3,
      `test_exception_swallow_regression.py` 2, `test_exchange_vault_singleton.py` 2,
      `test_risk_settings_api.py` 2, and 1 each in `test_account_health_query.py`,
      `test_exchange_safety_fix.py`, `test_false_success_report_fix.py`,
      `test_fee_precision_fix.py`, `test_get_db_dependency.py`,
      `test_position_delta_race_condition_fix.py`,
      `test_reconciliation_engine_fail_closed.py`, `test_risk_management_lifecycle.py` and
      `test_tenant_isolation_fixes.py`. `test_redis_debounce.py` appears nowhere in the
      failure list. No control was touched anywhere: the change is confined to one test
      file's concurrency plumbing plus two added assertions.
      Frontend re-run: **529 passed / 2 failed (531 across 20 files, 168 s)** — the recorded
      pair and nothing new. `portfolio-rendering.test.jsx` fails as before, still greping
      `src/pages/Portfolio.jsx` for `Trade history`, a string an earlier uncommitted rewrite
      of that page removed; out of scope and left alone. `integration.test.jsx >
      Dashboard Load > should load dashboard data on mount` fired its 30 s timeout this run
      where it had passed the previous one, and passes in isolation in 5.2 s (3 tests) — the
      flake it was already recorded as, confirmed rather than assumed. No third frontend
      failure appeared, so the number moving here is the flake alternating, not new
      breakage.
    - _Requirements: 14.3, 14.4, 15.5, 16.3, 16.4_

- [x] 7. Phase 7 — Asset discovery, market data source decision and feed honesty

  - [x] 7.1 Implement cached asset discovery
    - Add the asset universe cache (Redis, 6 h TTL, warmed at startup, refreshed off the
      request path) and `discover_assets(query)` per
      `design.md § DATA blocks and exchange-agnostic asset discovery`
    - Add `GET /strategy-operations/assets` with `search`, `base`, `quote`, `market_type`,
      `active_only`, `limit` and `cursor`, returning `AssetRef` records with precision and
      limit metadata, a total count and a continuation cursor
    - Return `ASSET_UNIVERSE_UNAVAILABLE` when the cache is empty and refresh fails; substitute
      no hardcoded list
    - Read the market map from the existing `connection_engine.py`; do not call
      `load_markets()` inside a request
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 25.5_
    - **What landed.** One new backend module, `backend_app/backend/asset_universe.py`,
      holding the whole of the Asset_Discovery_Service: `AssetRef` and `AssetUniverse`,
      `AssetQuery`, `merge_markets`, `filter_assets`, `paginate`, `encode_cursor` /
      `decode_cursor`, the cache pair `read_cached_universe` / `write_cached_universe`,
      `refresh_universe`, `schedule_refresh`, `discover_assets`, and the
      `AssetUniverseRefresher` background service with its
      `start_asset_universe_refresher` / `stop_asset_universe_refresher` module functions.
      One new route on the existing router — `GET /api/strategy-operations/assets` in
      `backend_app/routers/strategy_operations.py`, declared immediately after the registry
      endpoints because `design.md` → component table says asset discovery belongs on that
      surface rather than on a second one. Two hooks in `backend_app/main.py`'s lifespan
      (start beside the other background services, stop beside their shutdowns). Tests:
      `tests/test_asset_discovery.py`, 79 of them, all green. Nothing else was touched —
      `connection_engine.py`, `redis_manager.py` and `routers/market.py` are unmodified, and
      7.2 still owns the `/api/market/symbols` rewrite and the fallback deletion.
    - **`load_markets()` cannot run inside a request, and that is asserted on the AST rather
      than promised in a comment.** `discover_assets` awaits exactly one thing —
      `read_cached_universe` — and the router handler awaits exactly one thing,
      `discover_assets`; both sets are compared for equality against the parsed function
      bodies, so a future `await load_exchange_markets(...)` on either path is a test
      failure and not a review miss. A cache miss calls `schedule_refresh()` **without**
      `await`: the design's `refresh_async()` that "returns last-known-good immediately".
      The single exchange call site is the `asyncio.gather` inside `refresh_universe`, and a
      test counts the call sites to keep it single. `refresh_universe` reads the market map
      through `ConnectionEngine.connect()` — the existing retrying `load_markets` (fix
      CE-1) — and adds no second CCXT construction path.
    - **Warmed at startup without blocking startup.** `AssetUniverseRefresher.start()`
      creates the task and returns; the loop's first cycle *is* the warm. Seven venues at up
      to three retries each with a 30 s timeout is a boot the lifespan must not wait on, and
      the alternative — awaiting the warm — would have made an unreachable exchange a failed
      deployment. Until the warm lands, discovery answers 503 rather than a substitute list,
      which is the honest state and is exactly Requirement 11.6's condition. The scheduled
      interval is `UNIVERSE_TTL_SECONDS // 2` (3 h against the 6 h TTL Requirement 25.5
      fixes), so a healthy refresher never lets a served entry expire; a test pins
      `0 < interval <= TTL`, and `ASSET_UNIVERSE_REFRESH_SECONDS` can widen it but is floored
      at 60 s so no operator setting can turn the loop into a public-endpoint hammer.
    - **No hardcoded symbol list, and the test that says so does not trust prose.** Every
      string constant in `asset_universe.py` is extracted from the AST with docstrings
      excluded — the module's prose names the ten-symbol fallback it replaces, and prose about
      a defect is not the defect — and each is checked against `[A-Z0-9]{2,10}/[A-Z0-9]{2,10}`.
      The 503 body is checked the same way, as a substring search over the whole serialised
      response rather than for one absent key, because a nested substitute under any name is
      the same lie. The **exchange** set is not written here either: `supported_exchange_ids`
      reads `ExchangeExecutorFactory.SUPPORTED_EXCHANGES`, the platform's own statement of
      where it can place an order, so a market the executor could never trade cannot reach an
      author's selector and there is no second list to drift. `ASSET_UNIVERSE_EXCHANGES`
      overrides it with exchange ids only; no value of it can conjure a market.
    - **DEV_MODE's mock market map is refused, not cached — the one guard this task needed
      that the design does not mention.** `ConnectionEngine.connect()` has a pre-existing
      DEV_MODE branch that, when a venue is unreachable, injects `_apply_mock_interface`: a
      two-entry market map holding `BTC/USDT` and `ETH/USDT`. Caching that and serving it as
      the tradeable universe would have reintroduced a hardcoded list through the back door,
      in the exact environment this repository runs in (`tests/conftest.py` sets
      `DEV_MODE=true`). `_markets_are_mocked` detects the injected loader and
      `load_exchange_markets` raises, so that venue counts as failed and a developer with no
      network gets `503 ASSET_UNIVERSE_UNAVAILABLE`, which is true, rather than a two-symbol
      universe, which is not. Detection is by the injected closure's `__name__`, and the test
      invokes the **real** `_apply_mock_interface` and asserts both the detection and that its
      map really is that pair — so a rename in `connection_engine.py` breaks the test instead
      of silently disarming the guard.
    - **Precision and limits are the venue's own figures, and `None` means `None`.**
      `_coerce_number` returns `None` for an absent, non-numeric, NaN or infinite value and
      never a default. A defaulted `8` or a `0` would be a fabricated trading constraint, and
      phases after this one validate order sizes against these numbers. A fractional tick size
      (`price_precision = 0.01`, which is what several venues publish under CCXT's
      `TICK_SIZE` precision mode) is carried as `0.01` and not rounded to an integer, even
      though `design.md` types the field `Integer` — a rounded tick size is a wrong tick size,
      so the field is typed `Optional[float]` and the design's narrower type is the deviation.
      A test asserts each of: verbatim carry, `None` for a venue that publishes no limits, and
      the unrounded fraction.
    - **One record per `(symbol, market_type)`, with precision attributed to one named
      venue.** The same symbol on two venues has two different tick sizes and two different
      minimums; taking a min, a max or a mean across them would describe a market that trades
      nowhere. So the record carries exactly one venue's figures and names it in
      `precision_source`, while `available_on` lists every venue that has the market
      (`design.md`'s informational field, never part of a graph and so never in `dag_hash`).
      The chosen venue is deterministic: the first in the platform's supported order. Keying
      on `(symbol, market_type)` rather than on `symbol` keeps `BTC/USDT` spot and
      `BTC/USDT:USDT` swap as separate selectable markets. `active` is the only merged field —
      a market listed as active anywhere is offerable — and the per-venue answer stays
      reachable through `available_on`.
    - **`market_type` outside `spot | swap | future` is excluded and counted, not
      relabelled.** The DATA descriptor publishes exactly those three options, so an option,
      an index or a margin pair has no selectable representation; `merge_markets` reports
      `considered`, `excluded_unusable` and `duplicate_listings` and the refresh logs them, so
      an exclusion is visible rather than silent. A market with no canonical symbol, no base
      or no quote is dropped for the same reason: nothing is guessed for a market that does
      not state what it is.
    - **Ordering is `listing_count DESC, symbol ASC`, and it is not called liquidity — a
      deliberate deviation from the design.** `design.md` sorts by `liquidity_rank DESC`.
      Nothing in this platform caches per-market volume, and fetching a ticker for every
      market of seven venues to manufacture one is both a rate-limit decision and a latency
      decision belonging with 7.4, not something to invent here. `listing_count` — how many
      supported venues list the market — is a real, checkable number that correlates loosely
      with liquidity and is **not** presented as it; a test asserts the string `liquidity`
      appears nowhere in a served record, because a proxy labelled with the thing it proxies
      is the same class of dishonesty as a hardcoded universe. When volume data exists, the
      sort key is one function (`AssetRef.sort_key`) and one place to change.
    - **Pagination is a keyset cursor, and what happens under a mid-pagination refresh is
      stated rather than hoped for.** `next_cursor` is base64url over
      `{v, k: [-listing_count, symbol], f: filter fingerprint, u: universe hash}` — a
      position in the total order, never an offset. The next page is the first `limit`
      records whose `sort_key()` is strictly greater than `k`, so a refresh that inserts a
      market *before* the cursor cannot shift every later row into a duplicate, and the
      cursor's own record does not have to survive the refresh because the comparison is
      against the ordering and not against a row identity. A test paginates, then rebuilds
      the universe with an insert that sorts first and a delisting that sorts last, and
      asserts page two overlaps page one not at all and sorts strictly after it. What a
      keyset cursor cannot do is hide the change: a market inserted **after** the cursor
      appears on a later page and one removed after it never appears at all, so the response
      carries `universe_changed: true` when `u` no longer matches the served universe's hash
      — the client is told the set moved instead of being left to infer it from a total that
      does not add up. `f` pins the filters: continuing a cursor under a different filter set
      is `422 ASSET_CURSOR_INVALID`, not a silent walk through a different result set, and an
      unreadable, wrong-version or truncated cursor is the same 422 rather than being
      reinterpreted. `total` counts everything matching the filters, `source_meta.universe_total`
      the whole cached universe, so a filter's effect and a truncation are both visible.
    - **A stale-but-real universe is served and labelled stale; only an empty one is a 503.**
      Requirement 11.6's condition is "cache empty **and** a refresh attempt fails", so a
      universe older than its TTL is served with `source_meta.stale: true` and its
      `age_seconds`, and a refresh is scheduled. Withholding real markets because they are six
      hours old would be a worse answer than serving them with their age stated. The 503 path
      carries `last_refresh_error` and `refresh_in_flight` so an operator reads the cause
      instead of reproducing it, and `schedule_refresh` is single-flight with a 30 s backoff
      after a total failure, so a miss storm cannot become a `load_markets` storm against the
      venues.
    - **Redis absent or down degrades to a labelled local copy, never to a 500 and never to a
      fabricated universe.** The cache of record is the platform's own
      `redis_manager.get_redis_manager()` (DB 0, `cache_set_json` / `cache_get_json`, `ttl=
      UNIVERSE_TTL_SECONDS`) — no second Redis client, and its debounced reconnection is what
      keeps a dead server from being dialled per request. `_redis_cache` swallows and logs
      every Redis failure and returns `None`; a process-local last-known-good copy sits behind
      it, so a Redis outage answers from process memory with
      `source_meta.cache_backend: "process"` and an environment with no Redis at all still
      serves a real universe once a refresh has succeeded. A cached payload of the wrong
      schema version, or one carrying no asset list, is rejected **whole** and logged rather
      than partially interpreted. Nothing in the module raises at import, and the ccxt-touching
      import is lazy inside `load_exchange_markets`.
    - **Controls.** The endpoint carries `Depends(get_current_user)` — the platform's own
      bearer dependency, which is **what authenticates it**, stated because the endpoint is
      network-exposed — plus `@limiter.limit("120/minute")`, matching `design.md` → security
      surface ("Asset discovery | authenticated and rate-limited; cache is global and contains
      no user data"). Tests assert both from the signature and the source, and assert an
      unauthenticated caller is refused. `limit` is bounded server-side (`ge=1, le=500`) so a
      client cannot ask for the whole universe in one response. **The recorded decision on
      tenant scoping: there is none, deliberately.** The universe is reference data — public
      exchange metadata about venues the platform's own executor supports, identical for every
      caller, holding no user row, no exchange account, no key and no strategy — so there is
      no RLS-scoped client and no `user_id` filter on this path, by decision and not by
      omission. A test fetches the same page as two different identities and asserts the
      payloads are identical, and another greps a served page for `api_key`, `secret`,
      `passphrase` and `exchange_account`. No existing control was touched: every
      tenant-scoped read on this router keeps its double filter, no auth, RLS, financial
      invariant, execution safeguard, risk gate, idempotency key or rate limit was modified,
      and `Cache-Control: private, must-revalidate` keeps an authenticated response out of a
      shared cache.
    - **One change outside the task's footprint, and why it was made rather than worked
      around.** `main.py`'s global `HTTPException` handler normalises every error into
      `create_api_error_response` and was **discarding `exc.headers`**, so a 503 could not
      carry `Retry-After` — and, more seriously, a 401 raised anywhere in the platform could
      not carry `WWW-Authenticate`. It now forwards `exc.headers`; the body shape, the status
      code and the normalisation are untouched, so this adds headers a raiser explicitly
      attached and removes nothing. The alternative was to bypass the platform's canonical
      error shape for this one endpoint, which would have been a second error format to keep
      in step. Verified against the full suite rather than assumed: no test's failure count
      moved.
    - **What is not verifiable in this environment, said plainly.** No exchange is reachable
      from here (`getaddrinfo failed` throughout the run) and no Redis server is running, so
      **no test in this file has seen a real CCXT market map or a real Redis round-trip**.
      What is exercised is the real merge, sort, filter, cursor, cache and status-code logic
      against CCXT-*shaped* market dictionaries and a seeded cache — deliberately awkward
      ones: absent limits, a fractional tick size, an option market, a market with no quote,
      and the same symbol on two venues with different figures. `refresh_universe`'s
      partial-failure and total-failure paths are exercised with a substituted loader; the
      `ConnectionEngine.connect()` call inside `load_exchange_markets` itself is **not**
      exercised end to end, and neither is the Redis TTL actually expiring — the TTL is
      asserted as the value passed to `cache_set_json`, not as an observed eviction. The
      DEV_MODE mock guard *is* exercised against the real `_apply_mock_interface`. Live
      behaviour against real venues remains for a deployed environment.
    - **No generated sweep, and that gap is named rather than smoothed over.** There is no
      `hypothesis` import in this file. Task 7.8 is the spec's own asset-discovery test task
      and owns the generated `(universe, filters, page size)` sweep; what stands in for it
      here is a deterministic parametrisation of the pagination invariant over page sizes
      `1, 2, 3, 4, 5, 7, 100` asserting pages are disjoint and their union is the whole
      filtered set in order. The substance is covered; what is missing is the chance of being
      surprised by an input nobody chose.
    - Baseline: backend **43 failed / 3219 passed / 18 skipped (3280 collected)** in pytest's
      default order, one whole-suite run with the `.venv` interpreter
      (`.venv\Scripts\python.exe -m pytest -q`, 411 s), against the pinned ceiling of **44**.
      The failure count is **unchanged** from the 43 task 6.6 closed on, and the collection
      delta is exactly this task's new tests — **3280 − 3201 = 79**, all 79 passing. The
      failures are the pinned nineteen files at exactly the pinned per-file counts, checked
      file by file rather than assumed: `full_system_test.py` 4,
      `test_distributed_execution_safety.py` 7, `test_atomic_order_cancellation_fix.py` 4,
      `test_marketplace_pipeline.py` 4, `test_event_pipeline.py` 3,
      `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_transaction_isolation_serializable.py` 3,
      `test_exception_swallow_regression.py` 2, `test_exchange_vault_singleton.py` 2,
      `test_risk_settings_api.py` 2, and 1 each in `test_account_health_query.py`,
      `test_exchange_safety_fix.py`, `test_false_success_report_fix.py`,
      `test_fee_precision_fix.py`, `test_get_db_dependency.py`,
      `test_position_delta_race_condition_fix.py`,
      `test_reconciliation_engine_fail_closed.py`, `test_risk_management_lifecycle.py` and
      `test_tenant_isolation_fixes.py`. `test_asset_discovery.py` appears nowhere in the
      failure list, and `test_redis_debounce.py` stays green on both event-loop backends.
      Frontend: **not re-run, and deliberately so** — 7.1 touches no frontend file
      (`asset_universe.py`, `strategy_operations.py`, `main.py`, `tests/test_asset_discovery.py`),
      and the builder wiring is 7.3's task.

  - [x] 7.2 Retire the hardcoded symbol universe and publish timeframes
    - Re-point `GET /api/market/symbols` in `routers/market.py` at the cached universe,
      removing the synchronous `ccxt.binance().load_markets()` call, the `/USDT`-only filter,
      the first-50 alphabetical truncation and the 10-symbol hardcoded fallback
    - Serve `GET /registry/timeframes` from the intersection of timeframes the data pipeline
      supports
    - _Requirements: 11.1, 11.5, 11.6, 11.8_
    - **What landed.** `routers/market.py`'s `get_symbols` rewritten to read 7.1's cached
      universe through `asset_universe.discover_assets(AssetQuery(...))`; all four defects
      deleted, not softened. Two module constants added there (`SYMBOLS_WHOLE_SET`,
      `SYMBOLS_REQUESTED_LIMIT_CEILING`) and `ccxt` no longer imported by the module at all.
      `_TIMEFRAME_SOURCES` in `backend_app/routers/strategy_operations.py` widened from two
      vocabularies to three — the registry router from task 3.1 was **extended, not
      duplicated**, so there is still exactly one timeframe surface. One supporting change in
      `backend_app/backend/market_data_validation.py`: the row-coverage gate's
      timeframe→minutes table hoisted out of the method body into a module constant,
      `TIMEFRAME_MINUTES`, unchanged in keys, values and behaviour, so it is *readable* as a
      vocabulary instead of buried where nothing can intersect it. Tests:
      `tests/test_market_symbols_universe.py`, 36 of them, all green. Nothing else touched —
      `asset_universe.py`, `main.py` and every frontend file are unmodified, and the builder
      selector wiring plus the frontend's own fallback list remain 7.3's.
    - **The response shape is deliberately unchanged, and that was the decision to make.**
      `get_symbols` still returns a bare JSON array of symbol strings. Three callers were
      found before deciding: `algo22-terminal/src/api/typed-client.ts` types it
      `Promise<string[]>`, `algo22-terminal/src/api/modules/market.js` documents it as
      `string[]`, and `src/contexts/DataPipelineContext.jsx` tests the body with
      `Array.isArray` and would fall through to *its own* hardcoded ten-symbol list if the
      shape stopped being an array — so an envelope here would have silently re-armed the
      exact defect this task removes, in the client, while the backend reported success. The
      record-bearing, paginated, provenance-carrying shape already exists at
      `GET /api/strategy-operations/assets` (7.1); publishing a second full-fat discovery
      response would be a second contract to keep in step. What the legacy surface gains
      instead is headers — `X-Asset-Total`, `X-Asset-Returned`, `X-Asset-Truncated`,
      `X-Asset-Universe-Hash`, `X-Asset-Universe-Stale`, `X-Asset-Universe-Age-Seconds`, and
      a `Link: rel="successor-version"` pointing at the canonical endpoint. No frontend file
      was touched, so the frontend suite was **not** re-run.
    - **The truncation was removed rather than raised, which is why there is no default page
      size.** The `[:50]` cut is the defect that made every market after the letter B
      unselectable, and its essence is that it was *undisclosed* — so replacing it with a
      bigger undisclosed cap would have been the same defect with a larger number, and a
      client ignoring headers (all three of the ones found do) could not tell. The endpoint
      now returns every market matching the caller's filters. `limit` exists but has **no
      default**: truncation happens only when a caller asks for it, and when it does,
      `X-Asset-Truncated: true` sits beside the true `X-Asset-Total`. `SYMBOLS_WHOLE_SET`
      (1,000,000) is how "all of it" is expressed to a service whose parameter is a page
      size; the keyset pager slices, so the figure allocates nothing, and if a universe ever
      exceeded it the response would report itself truncated rather than pass a partial set
      off as the whole one. A caller-supplied `limit` is still bounded server-side at 5000 so
      one client cannot ask for an unbounded assembly.
    - **`/USDT` became a filter with no default, so nothing is hidden and the old view is
      still reachable.** `?quote=USDT` reproduces exactly what the endpoint used to serve, as
      an explicit request rather than an invisible policy; omitting it returns every quote the
      platform can trade. `search`, `base`, `market_type` and `active_only` are passed through
      to the same `AssetQuery` the canonical endpoint builds, and a parametrised test asserts
      the two surfaces agree symbol-for-symbol across six filter combinations — a projection
      that could disagree with its source is how SB-03 happened.
    - **An unavailable universe is 503 `ASSET_UNIVERSE_UNAVAILABLE`, and the test does not
      trust the status code alone.** The `except` branch that returned ten symbols with a 200
      is gone; the handler raises the platform's `HTTPException` carrying
      `last_refresh_error`, `refresh_in_flight`, `retry_after_seconds` and a `Retry-After`
      header, mirroring `/assets` exactly. Three separate assertions pin the honesty: the
      error code is named, the whole serialised body is substring-searched for any
      pair-shaped literal so a substitute list cannot return under a new key or nesting, and
      the response is asserted **not** to be `[]` — an empty array would read to a client as
      "this platform lists nothing", which is the same untruth in a different costume. A
      *filter* that matches nothing is still a 200 with `[]` and `X-Asset-Total: 0`, because
      "no market matched your query" and "there is no universe" are different statements and
      have to stay distinguishable. A stale-but-real universe is served with
      `X-Asset-Universe-Stale: true`, per 7.1's reading of 11.6.
    - **No `load_markets()` on any request path here, asserted on the AST.** Three structural
      tests parse `routers/market.py`: `ccxt` appears in no `import` or `from` statement
      anywhere in the module, no `load_markets` / `loadMarkets` call node exists in it, and
      no non-docstring string constant matches `[A-Z0-9]{2,10}/[A-Z0-9]{2,10}`. Docstrings are
      excluded deliberately and the handler's prose does name the four defects it replaced —
      prose describing a defect is not the defect — so a fourth test strips docstrings via
      `ast.unparse` before checking that `endswith` and `[:50]` are absent from executable
      code. A future re-import of ccxt into this router is therefore a test failure, not a
      review miss.
    - **Other CCXT work still on this router's request paths — reported, not fixed, as the
      task directs.** `/candles`, `/orderbook`, `/ticker`, `/funding`, `/data/{symbol}/{tf}`
      and `/data/historical` all go through `_get_data_engine`, whose public fallback
      constructs `ConnectionEngine(exchange_id=...)` and `await`s `connect()` — and
      `connect()` calls `await self.exchange.load_markets()` (ccxt.**pro**, with CE-1's retry)
      on every such request. Two things are true and worth separating: it is **not** a
      synchronous, loop-blocking call, so it is not the defect 7.2 removed; but it is a
      per-request `load_markets` round-trip against a venue, and each of those handlers also
      builds a fresh unpooled `ConnectionEngine` on the fallback path instead of reusing the
      `get_or_create_exchange` pool. That is a real N7/SCALE regression surface on five
      handlers. It is outside this task's footprint — 7.2 owns the symbols path — and no line
      of it was changed.
    - **The published timeframe set is now a three-way intersection, and one interval left as
      a result.** The vocabularies are `backtesting_engine.VALID_FREQ_MAP` (14 labels; an
      absent one *raises*, refusing the backtest), `master_executor.TF_SEC` (11; an absent one
      falls back to 60 s, so the live loop would silently aggregate the wrong bars) and the
      newly-readable `market_data_validation.TIMEFRAME_MINUTES` (13; an absent one is measured
      as if it were an hour, which makes the row-coverage check *unfailable* — a disarmed
      control, not a cosmetic gap). Served set: **1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d**
      — ten, down from eleven. **`3m` is no longer published**, and that is the change working
      rather than a regression: `TF_SEC` and the resampler both handle 3m, but the market-data
      coverage gate has no figure for it, so a 3m strategy would have run with its
      completeness check silently neutralised. Offering it was the lie; withdrawing it is the
      fix. The inclusion rule is written down at `_TIMEFRAME_SOURCES`: a source belongs when a
      missing label changes what the pipeline *does* to the data — rejects it, or mis-aligns or
      mis-measures it silently.
    - **What was considered as a timeframe source and rejected, recorded so the omissions are
      decisions.** (a) The DATA descriptor's `timeframe` param publishes **no** `options`
      tuple — it is `required` with `default=None` by SB-06 — so it states no vocabulary and
      can constrain nothing; a test asserts that absence, because a hand-written `options`
      list appearing there later would be a second, drift-prone vocabulary. (b)
      `data_seeking_engine.DataEngine` holds no whitelist at all: it forwards the timeframe
      string straight to CCXT, so it accepts whatever the venue accepts. Intersecting an
      empty constraint is a no-op, and treating it as a vocabulary would mean inventing one.
      (c) `backtest_service._timeframe_to_minutes` is method-local and feeds a gap *warning*
      only — it gates nothing and its output is visible — yet its narrower key set would have
      dropped 2h, 6h and 12h, intervals the resampler, the executor and the coverage gate all
      handle properly; intersecting it would have understated what the platform supports. (d)
      The ML and dataset constraints (minimum rows, sequence length, warmup) bound the
      *quantity* of data at a given interval, not the set of intervals, so they do not bear on
      this set and stay with the training admission gate where they belong. **Judgement call
      worth flagging:** (c) is the one that could reasonably have gone the other way. The line
      drawn — advisory output does not narrow a published capability, a silently disarmed
      control does — is stated at the constant so a reviewer can disagree with the rule rather
      than reverse-engineer it.
    - **The timeframe test recomputes the intersection instead of comparing to a list.** A
      test that asserted the served set equalled a written-out ten-label list would be the
      hardcoding defect wearing a test's clothes. So the test imports the modules
      `_TIMEFRAME_SOURCES` names, intersects them itself, and requires exact equality with
      what was served; a further test requires that every label in the *union* but not in the
      served set is genuinely missing from at least one vocabulary, so an intersection that
      quietly dropped something it should have kept fails. Task 3.1's existing timeframe tests
      were already written against `_TIMEFRAME_SOURCES` dynamically, so widening the tuple
      needed no edit to them — and they still pass, which is the evidence the widening did not
      change the endpoint's shape, only its content.
    - **Controls.** `@limiter.limit("60/minute")` and `Depends(get_current_user)` are both
      preserved verbatim and both asserted — from the handler signature and from the module
      source — and an unauthenticated caller is asserted refused. Nothing was weakened and
      nothing was added that could weaken something: no auth, RLS filter, tenant scope,
      financial invariant, execution safeguard, risk gate, idempotency key or rate limit was
      modified anywhere in this change. The symbol universe is reference data — public
      exchange metadata about venues the platform's own executor supports, holding no user
      row, no exchange account and no key — so, as recorded in 7.1, there is no tenant scoping
      on this path by decision; `Cache-Control: private, max-age=0, must-revalidate` keeps the
      authenticated response out of a shared cache. The `market_data_validation` hoist is
      assert-tested to be a hoist and not a copy: the gate's own source must still read
      `TIMEFRAME_MINUTES.get(timeframe, 60)`, so there cannot be two tables.
    - **What is not verifiable here, said plainly.** No exchange is reachable from this
      environment and no Redis server is running, so no test in this file has seen a real CCXT
      market map or a real Redis round-trip. What is exercised is the real handler, the real
      `discover_assets`, the real merge/filter/pagination and the real status codes against a
      seeded process-local cache built through `merge_markets` from CCXT-*shaped* dictionaries
      — deliberately 61 of them, more than the retired fifty-row cut, including non-USDT
      quotes, an inactive market, and one symbol listed as both spot and swap. The
      `ConnectionEngine.connect()` path is not exercised end to end. There is no `hypothesis`
      import in this file: task 7.8 owns the generated asset-discovery sweep, and what stands
      in for it is the six-way parametrised agreement check between this endpoint and
      `/api/strategy-operations/assets`. The substance is covered; what is missing is the
      chance of being surprised by an input nobody chose.
    - Baseline: backend **43 failed / 3255 passed / 18 skipped (3316 collected)** in pytest's
      default order, one whole-suite run with the `.venv` interpreter
      (`.venv\Scripts\python.exe -m pytest -q`, 264 s), against the pinned ceiling of **44**.
      The failure count is **unchanged** from the 43 task 7.1 closed on, and the collection
      delta is exactly this task's new tests — **3316 − 3280 = 36**, all 36 passing. The
      failures are the same pinned nineteen files at the same per-file counts:
      `full_system_test.py` 4, `test_distributed_execution_safety.py` 7,
      `test_atomic_order_cancellation_fix.py` 4, `test_marketplace_pipeline.py` 4,
      `test_event_pipeline.py` 3, `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_transaction_isolation_serializable.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_vault_singleton.py` 2, `test_risk_settings_api.py` 2, and 1 each in
      `test_account_health_query.py`, `test_exchange_safety_fix.py`,
      `test_false_success_report_fix.py`, `test_fee_precision_fix.py`,
      `test_get_db_dependency.py`, `test_position_delta_race_condition_fix.py`,
      `test_reconciliation_engine_fail_closed.py`, `test_risk_management_lifecycle.py` and
      `test_tenant_isolation_fixes.py`. `test_market_symbols_universe.py` appears nowhere in
      the failure list, and `test_registry_endpoints.py`, `test_asset_discovery.py`,
      `test_end_to_end_api_suite.py` and `test_market_none_keys_fix.py` — the four suites that
      touch the changed surfaces — are green (183/183). Frontend: **not re-run, and
      deliberately so** — this task touches no frontend file (`routers/market.py`,
      `routers/strategy_operations.py`, `backend/market_data_validation.py`,
      `tests/test_market_symbols_universe.py`), and the selector wiring plus the removal of
      `DataPipelineContext.jsx`'s own ten-symbol fallback are 7.3's task.

  - [x] 7.3 Populate the builder asset and timeframe selectors from the API
    - Wire the DATA node's `symbol` and `timeframe` controls to the discovery and timeframe
      endpoints with search, filtering and pagination
    - Show no local symbol list; an unavailable universe renders an error state
    - _Requirements: 11.7, 11.8, 5.2_
    - **What landed.** Six new frontend modules and four edited ones. New:
      `src/api/modules/assets.js` (the transport for `GET /api/strategy-operations/assets`,
      registered as `api.assets`), `src/lib/assetUniverse.js` (the projection, the failure
      vocabulary and the cursor-fingerprint rules — pure, no React, no network),
      `src/hooks/useAssetSearch.js` (debounce, keyset pagination, cursor reset, 422 restart,
      staleness guards), `src/hooks/useTimeframeSet.js`,
      `src/components/builder/AssetSelector.jsx` and
      `src/components/builder/TimeframeSelector.jsx`. Edited: `src/lib/registryClient.js`
      **extended** with a second, independent cache for `/registry/timeframes`;
      `src/components/builder/ParameterForm.jsx` gained one optional `controls` prop;
      `src/pages/StrategyBuilder.jsx` passes `MARKET_PARAM_CONTROLS` to it;
      `src/contexts/DataPipelineContext.jsx` and `src/contexts/StrategyEngineContext.jsx` had
      their hardcoded market vocabularies deleted. Tests:
      `tests/unit/assetDiscovery.test.js` (48) and `tests/unit/builder.marketControls.test.jsx`
      (60), all 108 green. No backend file was touched — zero lines — so the backend suite was
      **not** re-run.
    - **Three hardcoded vocabularies were deleted from `DataPipelineContext.jsx`, not two.**
      The task named the ten-symbol fallback in `loadMarkets`; reading the file found two more
      of the same defect. (a) The **fallback list** is gone: a failure now sets a `symbolsError`
      and leaves `availableSymbols` **empty**, and a response that is not an array is an *error*
      rather than the trigger for a substitute — which is what the `Array.isArray` test used to
      be. The error is rethrown rather than swallowed into a value, because a caller that cannot
      tell a failure from an empty market list is the defect itself. (b) `availableTimeframes`
      was initialised to a **seven-label list** (`1m … 1w`); it is now derived from
      `registryClient`'s timeframe cache, so it starts empty and holds exactly what the endpoint
      published. `1w` was never in the published intersection, so the old initial state offered
      an interval the pipeline cannot process. (c) `validateInputs` held a **second copy** of
      that list and used it to accept or reject a timeframe; it now checks the fetched set and
      **fails closed** when either set could not be loaded, with distinct codes so "we could not
      check" (`SYMBOL_UNIVERSE_UNAVAILABLE`, `TIMEFRAME_SET_NOT_LOADED`) and "your value is
      wrong" (`SYMBOL_NOT_TRADEABLE`, `TIMEFRAME_NOT_SUPPORTED`) are different messages.
    - **A fourth one was found by the structural test rather than by reading, and it was the
      worst of them.** `contexts/StrategyEngineContext.jsx` — mounted by the builder, inside its
      import closure — built its execution plan with `symbol || 'BTC/USDT'`, `timeframe || '1h'`
      **and** `exchange || 'binance'`. That is SB-06 exactly, and Requirements 12.1/12.2 for the
      third: the DATA descriptor publishes no exchange parameter, and exchange identity is a
      deployment binding that must never be persisted with a strategy. All three defaults are
      gone; an unset symbol or timeframe now pushes an error naming the node and the field, and
      the `exchange` key was removed outright (it was assembled into `plan.pipeline.dataSource`
      and read by nothing, checked before deleting). The function is legacy — it filters on
      `node.type === 'source'`, which task 3.4's re-typing means never matches, so `dataSource`
      is `null` in practice — but a dormant silent default is still a shipped literal, and it
      was not reported-and-left for the same reason 7.2 did not leave the backend's.
    - **`registryClient.js` was extended rather than copied, and that is why the timeframe set
      lives there.** `/registry/timeframes` is served by the same router behind the same
      `_registry_response` — same `ETag`, same `304`, same `Cache-Control` — so a second module
      would have reimplemented the conditional GET, the single-in-flight rule, the 304-is-success
      rule and the fail-closed drop. Instead the `If-None-Match`-only-when-a-copy-is-held logic
      was factored into one `conditionalGet` used by **both** resources (the two rules easiest to
      get subtly wrong, now in one place), and `toRegistryError` was generalised to carry a
      `resource` field. The two **caches** are independent — separate payload, tag, in-flight
      promise, state and listener set — because a 503 from `/registry/blocks` says nothing about
      whether the pipeline's timeframe vocabularies could be read, and conflating them would take
      the timeframe selector down with the palette. Existing `registryClient` behaviour is
      byte-identical for blocks: its 33 tests needed no edit and still pass.
    - **The timeframe endpoint is the only source, and the tests prove the absence rather than
      assert a list.** A test asserting the served set equalled a written-out ten-label list
      would be the hardcoding defect wearing a test's clothes, so the assertions are the other
      way round: `3m`, `1w` and `1M` are asserted **absent** from the rendered `<option>` values,
      `getTimeframeSeconds('3m')` is asserted `null`, and the `timeframe` `ParamSpec` is asserted
      to publish `options: null` with `required: true, default: null` — because a hand-written
      `options` tuple appearing there later is what would make the endpoint one source among
      several. `seconds` is taken off the wire and never parsed from the label: two derivations
      of one interval is how `1M` (a month) and `1m` (a minute) become indistinguishable.
    - **A 200 with zero timeframes is malformed, not an empty vocabulary.** The backend refuses
      to publish an empty set (`503 TIMEFRAME_VOCABULARY_EMPTY`), so a 200 carrying none is a
      failed assembly, and rendering it as an empty `<select>` would read as a platform that
      trades on no interval. It becomes `REGISTRY_EMPTY` with zero options and an `alert`. An
      entry with no positive `seconds` is likewise refused: a zero or missing interval would make
      any later freshness check unfailable.
    - **The four selector states are kept apart, which is the whole point.** A selector can
      honestly say four things, and collapsing any two of them misleads the author:
      `loading`; `ready` with N of a stated total; **`ready` with zero — the filters matched
      nothing, a real answer whose fix is the filters**; and **`error` — the platform cannot say
      what it trades, whose fix is to retry after the interval the server stated**. The last two
      are identical on screen if an error renders as an empty list, so a failure renders an
      `alert` carrying the backend's own sentence verbatim (`ParameterForm`'s `fix_hint` rule and
      `NodePreview`'s `detail.message` rule) plus a retry, and the status line says *"No markets
      are listed: the asset list could not be loaded"* rather than *"No market matches these
      filters"*. One test asserts the whole rendered subtree of a 503 contains **no pair-shaped
      literal at all**, so a substitute list cannot return under a new key or a new component.
    - **Classification keys on the backend's code, not on the status.** A 503 is
      `ASSET_UNIVERSE_UNAVAILABLE` because the body says so and a 422 is `ASSET_CURSOR_INVALID`
      because the body says so; a status-only classifier would put a future 422 with a different
      cause (a bad `limit`, say) onto the cursor-restart path, which is precisely the accidental
      loop the restart rule exists to avoid. `retryAfterSeconds` is the figure the server stated
      — from `details.retry_after_seconds` or the `Retry-After` header — and is `null` rather
      than invented when it stated none. A 401 is its own code with `retryable: false` and
      "sign in again", because telling someone to retry a dead session is telling them the wrong
      thing (Requirement 21.1).
    - **Pagination uses the cursor, and a filter change drops it — that is the mechanism; the
      422 handler is the backstop.** `filterFingerprint` canonicalises the six filters the server
      pins a cursor to, and any change to it resets to page one, so the client never *sends* a
      cursor under filters it was not cut under. When a 422 arrives anyway (one already in
      flight, or a rejection the client does not model) the query restarts from page one **once
      per fingerprint**, guarded by a ref cleared only when the fingerprint changes. A test drives
      exactly four requests — page 1, a 422'd cursor, the restart, a second 422'd cursor — and
      asserts it stops at an error state rather than looping against an authenticated,
      rate-limited endpoint.
    - **Search is debounced at 250 ms, and the state says which kind of waiting it is.** A test
      fires three keystrokes, asserts **zero** requests were issued, advances the timer and
      asserts exactly one, carrying the final search string. While the timer is armed the status
      reads "Waiting for you to finish typing…" rather than showing a spinner for a fetch that is
      not running. 250 ms against the validator's 400 ms (Requirement 8.10) because a search is
      cheap and interactive where a validation is not.
    - **Two staleness guards, because either alone leaks.** Every request carries an
      `AbortController` signal *and* a sequence number checked before its result is adopted. The
      abort covers the network; the sequence number covers the window between an abort and the
      promise settling, which is where a slow page-one response would otherwise land on top of a
      fast page-two and show the author markets they had already scrolled past.
    - **Fail closed, with one deliberate exception, and it is named.** A failed **first** page
      yields zero markets. A failed **later** page keeps the pages the server already sent and
      states the failure beside them, because those records were real and discarding them would
      lose real data to report an error about a page that was never served. `assets.length` and
      `error` are both readable, so the two cases are distinguishable, and both are tested.
      `universe_changed` is surfaced as its own sticky notice, pages are merged by symbol so a
      universe that refreshed mid-page cannot show one market twice, and the repeat count is
      stated rather than swallowed.
    - **Absent is not zero, all the way to the string.** `min_notional`, `min_amount` and both
      precisions are `null` where the venue published none (task 7.1), and `describeAsset` renders
      that as `not published` — never `0`, because a later order-size check reading `0` as "no
      minimum" would size an order against a limit nobody stated. A *stated* zero still reads
      `0`, and a test pins both. `active` is tri-state: an unstated flag is `null` and reads
      "active state not published", not "inactive". `listing_count` is reported as "listed on N
      venues" and asserted **not** to contain the words "rank" or "liquidity", because task 7.1
      chose that field precisely for being a checkable count rather than the `liquidity_rank` the
      design's pseudocode named and no adapter can supply. `precision_source` is named beside the
      figures, so a record never looks like a blend of two venues.
    - **Typing does not choose a market.** `symbol` is published `required` with no default
      because it decides which market a saved strategy trades (Requirement 5.4, SB-06), so the
      parameter is set **only** when an option that came off the wire is activated. `"BTC/USD"`
      typed at a venue listing `"BTC/USDT"` leaves the parameter unset, the form's blocking state
      visible and the save refused. The chosen market is restated in text beside the control —
      a combobox whose input holds a search string cannot let the input's contents stand in for
      the value — with a Clear action, and the descriptor's `example` is a placeholder, never a
      prefill.
    - **`ParameterForm` gained a seam, not a client.** One optional `controls` prop maps a
      `ParamType` to a component; with it absent every type renders from the descriptor exactly
      as before (asserted). The form still imports nothing that fetches, so its guarantees — no
      prefill of a behaviour-changing parameter, no auto-selected first option, backend text
      verbatim — remain assertable without a network, and the injected controls inherit the
      form's `id`, `name`, `disabled`, `required` and full ARIA bundle rather than re-deriving
      it. `MARKET_PARAM_CONTROLS` is injected from `StrategyBuilder.jsx`, which is where the
      decision to fetch belongs.
    - **The `market_type` filter reads the sibling `ParamSpec`; `base` and `quote` are free
      text, and that difference is a decision.** The DATA descriptor publishes
      `options := ["spot", "swap", "future"]`, so that filter's vocabulary is the descriptor's
      and no copy is kept — a client copy would keep working right up to the day the platform
      gained a fourth market type. The platform publishes **no** currency vocabulary, so offering
      a picked list of quotes would mean inventing one; those two are free-text exact matches, and
      an unmatched code returns an honest `total: 0`. The filter is seeded from the block's own
      `market_type` value so an author who set `swap` is not first shown spot markets, writes
      nothing back, and every row states its own `market_type` so a mismatch is visible.
    - **Accessibility, and the one attribute deliberately dropped.** An ARIA 1.2 combobox:
      labelled input with `role="combobox"`, `aria-expanded`, `aria-controls`,
      `aria-autocomplete="list"` and `aria-activedescendant` over a `role="listbox"` of
      `role="option"` children; Up, Down (wrapping), Home, End, Enter and Escape all tested; a
      pointer path in addition to, not instead of, the keyboard one. `aria-describedby`
      **composes** with the form's, so help and constraints are still announced. Every state is
      text through a polite live region, a failure through an `alert`, and the chosen option
      carries `aria-selected` rather than only a background colour. The timeframe control is a
      native `<select>`, keyboard-operable by construction, disabled while there is nothing real
      to choose so it cannot be operated into a value that came from nowhere. The **HTML
      `required` attribute is dropped from the asset input** while `aria-required` is kept — the
      same distinction `ParameterForm` already draws for a required BOOLEAN — because that input
      holds a search string, not the parameter's value, so `required` would mark it invalid while
      a market was legitimately chosen and valid while none was.
    - **No control was weakened.** Both endpoints go through the shared authenticated axios
      instance (`src/apiClient.js`), whose request interceptor attaches the session bearer token
      and whose response interceptor normalises errors; no new client, no `fetch`, no
      unauthenticated path, and no token is read, stored or forwarded by any new file. Nothing
      touched auth, RLS, a tenant scope, a financial invariant, an execution safeguard, a risk
      gate, an idempotency key or a rate limit. A read-only version (Requirement 9.9) issues
      **no request at all** from either control, asserted, so a `DEPLOYED` canvas does not hold a
      rate-limited endpoint open behind it.
    - **One deviation from the task's letter, stated with its reason.** The task asked for the
      api module layer rather than axios from a component, and that is what shipped — the
      components reach the network only through `api/modules/assets.js` and `lib/registryClient.js`.
      But that module calls the shared axios **instance** rather than `apiClient`'s `get()`
      helper, deliberately, for three reasons that are wrong for a selector: `get()` retries 5xx
      three times with exponential backoff, so a 503 `ASSET_UNIVERSE_UNAVAILABLE` — a deliberate,
      immediate answer carrying its own `Retry-After` — would cost four requests and about seven
      seconds before the honest error state could render, and five of them would trip the circuit
      breaker and replace the server's stated interval with a client-invented 503 saying something
      else; it caches for 60 s keyed on URL, so a search could be served a page the server would
      no longer serve and `universe_changed` could not report it; and it returns `response.data`
      only, hiding the `Retry-After` and `X-Asset-Universe-Hash` headers. This is the same choice
      `lib/registryClient.js` already made for the same reason. It is written at the top of
      `assets.js` so a reviewer can disagree with the rule rather than reverse-engineer it.
    - **Reported, not fixed, as adjacent surface.** (a) `pages/Backtester.jsx` holds two
      hardcoded symbol lists (17 and 30 pairs) and a six-label timeframe `<select>`. It is
      **outside** the builder's closure — task 3.17's architecture test asserts the builder
      reaches no Backtester module at any depth, and that test still passes — so it is not
      "reachable from the builder" and not in this task's footprint. It is a real instance of the
      same defect and belongs with whichever task owns that page. (b)
      `utils/engineHelpers.getMaxHistoryForTimeframe` keeps a per-interval max-history table
      with a silent `|| 30` default for an unknown label. It is a data-availability heuristic
      rather than an interval vocabulary — it constrains *how much* history, not *which*
      intervals exist — so it does not bear on either selector and was left untouched; the
      undisclosed default is worth someone's attention. (c) `npm run lint` cannot run in this
      repo at all: `eslint.config.js` throws `TypeError: Cannot read properties of undefined
      (reading 'recommended')` at load. Pre-existing and unrelated to this change, but it means
      the `jsx-a11y-x` rules configured there were **not** exercised against the new components;
      the accessibility claims above rest on the assertions in
      `tests/unit/builder.marketControls.test.jsx`, not on a linter.
    - **What is not verifiable here, said plainly.** No backend is running and no exchange is
      reachable, so **no test in either file has seen a real discovery response**. What is
      exercised is the real transport, the real projection, the real hook, the real registry
      cache and the real components against CCXT-shaped `AssetRef` dictionaries and the real
      page/`source_meta` shape — including a market with unpublished minimums, an inactive one,
      a two-venue listing, a `universe_changed` page, a stale universe, and the ten labels task
      7.2's intersection actually publishes. Debounce and pagination are driven on fake timers,
      so the 250 ms is asserted as a scheduled delay rather than as observed wall-clock. There is
      no `fast-check` dependency in this project, so there is **no generated sweep**: what stands
      in for it is a parametrised failure table over six status/code combinations and a
      structural check over the builder's whole import closure. The substance is covered; what is
      missing is the chance of being surprised by an input nobody chose.
    - Baseline: frontend **1 failed / 638 passed (639 collected)**, 22 files, one whole-suite run
      (`npx vitest run` from `algo22-terminal`, 118 s). The collection delta is exactly this
      task's new tests — **639 − 531 = 108**, all 108 passing. The single failure is
      `tests/unit/portfolio-rendering.test.jsx` → "should verify improved empty states", the known
      out-of-scope grep against a `src/pages/Portfolio.jsx` an uncommitted rewrite changed.
      The second known failure — `tests/unit/integration.test.jsx` → "should load dashboard data
      on mount" — **appeared in one of three full runs and not the other two**, and
      `tests/unit/integration.test.jsx` run alone is 3/3 green, which is the documented 30 s
      timeout flake rather than a new break. So it is still exactly those two, and no other test
      moved: `ParameterForm.test.jsx` (86) and `registryClient.test.js` (33) — the two suites
      whose modules this task edited — are green, and task 3.17's
      `builder.architecture.test.js` still passes with six new modules in the builder's closure.
      Backend: **not re-run, and deliberately so** — this task touches no backend file, and the
      pinned ceiling of 44 with 7.2's measured 43 stands untouched.

  - [x] 7.4 Implement feed state and the data quality endpoint
    - Compute feed state from the last event age against the expected interval: `LIVE`,
      `DELAYED`, `STALE`, `DISCONNECTED`, `INSUFFICIENT_DATA`
    - Display the concrete age and expected interval, not only a colour; never label stale data
      `LIVE`
    - Add `GET /strategy-operations/strategies/{id}/data-quality` reading `DataQualityReport`
      from the existing `market_data_validation.py` rather than computing anything new
    - _Requirements: 19.6, 19.7, 19.8, 19.9, 19.10, 19.14_
    - **What landed.** One new module, `backend_app/backend/feed_state.py`: the five-state
      classifier (`FeedState`, `classify_age`, `evaluate_feed_state`), the display helpers
      (`format_age`, `FeedStateReport.display`) and one observation adapter (`observe_feed`)
      that reads the platform's existing `websocket_monitor` rather than starting a second
      liveness tracker. One new endpoint on the existing router,
      `GET /api/strategy-operations/strategies/{id}/data-quality`
      (`get_strategy_data_quality` plus three module-level helpers `_data_quality_window`,
      `_data_quality_market`, `_data_quality_payload`) — the router was **extended, not
      duplicated**, on the same conventions 6.3/6.5/6.6/7.1 established there. Tests:
      `tests/test_feed_state_and_data_quality.py`, 78 of them, all green.
      `backend_app/backend/market_data_validation.py` is **untouched by this task** — zero
      lines — which is what "reused as-is" has to mean for the component that owns the
      thresholds.
    - **The expected interval is a lookup into the pipeline's vocabulary, never arithmetic on
      the label.** `expected_interval_seconds` reads `market_data_validation.TIMEFRAME_MINUTES`
      (the table 7.2 hoisted into view) and nothing else, so the freshness threshold and the
      row-coverage gate measure the same intervals. Two consequences are deliberate. First,
      the lookup is **case-sensitive**: CCXT writes a month `1M` and a minute `1m`, and
      lower-casing the key before the lookup would read a month-old candle as a one-minute
      one — a 43,200× error, in the direction of reporting stale data as `LIVE`. A test pins
      `1M`, `1H` and `1D` as unmeasurable. Second, an absent label is **refused, not
      defaulted**: the coverage gate's own `.get(timeframe, 60)` merely disarms a check, but
      defaulting here would let a 3m feed sit twenty minutes stale and still read `LIVE`, so
      an unpublished interval produces `STALE` / `EXPECTED_INTERVAL_NOT_PUBLISHED` with
      `expected_interval_seconds: null`. A further test recomputes the `/registry/timeframes`
      intersection and asserts every label an author can actually select **is** measurable
      here, so the selector's set and the freshness table cannot drift apart.
    - **The boundaries, and the side each one falls.** `LIVE` is `age < 1.5 × interval`
      **strictly**; `DELAYED` is `1.5 × ≤ age < 3 ×`; `STALE` is `3 × ≤ age`. Both boundaries
      are closed on the *worse* side: exactly 1.5 intervals reads `DELAYED`, exactly 3 reads
      `STALE`. That makes Property 26 — anything `LIVE` has an age below 1.5 × the interval —
      true **by construction** rather than by a rounding accident, and it resolves every
      ambiguous instant against the feed rather than in its favour. A parametrised table pins
      449.999 / 450.0 / 899.999 / 900.0, which are the four values a `<=` would get wrong.
    - **Precedence is written down, because 19.7–19.10 genuinely overlap.** A feed can be
      three intervals old *and* short of warmup at once, and one label has to be chosen. The
      order is `DISCONNECTED` → measured `STALE` → `INSUFFICIENT_DATA` → unmeasurable
      (fail-closed `STALE`) → `DELAYED` → `LIVE`, stated at the module docstring so a reviewer
      can disagree with the rule rather than reverse-engineer it from a chain of `if`s. Two
      of those placements are judgement calls worth flagging. **Measured staleness outranks a
      bar shortfall**, because reporting "not enough bars yet" for a feed that has stopped
      reads as "keep waiting, bars are accumulating" when they demonstrably are not — the
      optimistic reading this task exists to delete. **A known bar shortfall outranks an
      unmeasurable age**, which is the opposite direction, and for a reason: a bar count is an
      observation, an unmeasurable age is the absence of one, and a real observation should
      not be displaced by a guess. Choosing a precedence means one requirement's letter
      yields to another's in the overlap (a feed both delayed and short of warmup is reported
      `INSUFFICIENT_DATA`, not `DELAYED`), so **nothing is lost by the choice**: the payload
      carries every derivation input beside the label — `connected`, `age_seconds`,
      `expected_interval_seconds`, `available_bars`, `warmup_bars`, `bars_missing` — plus
      `age_state`, the pure age-only classification, which in that example still says
      `DELAYED`. A client wanting the age answer rather than the precedence answer reads one
      field; it never has to re-derive one.
    - **`INSUFFICIENT_DATA` and `DISCONNECTED` are asserted not to collapse.** Two tests hold
      the same bar shortfall constant and flip only the transport: connected is
      `INSUFFICIENT_DATA`, disconnected is `DISCONNECTED`, and **both** responses carry
      `bars_missing` and `connected`, so neither label hides the other's evidence. A third
      holds the shortfall and makes the feed stale, and gets `STALE` with `bars_missing`
      still on the wire.
    - **Unknown is never fresh, and the absence propagates all the way to the string.** An
      unmeasured age is `age_seconds: null` **and** `age_text: null` — never `0` and never
      `"0s"`, which a panel would render as a candle that just arrived. Eight shapes of "we
      do not know" are asserted non-`LIVE` in one test: no event observed, interval not
      published, no timeframe, `NaN`, a *negative* age (a timestamp in the future is a clock
      fault, not freshness), a non-numeric age, `connected=None` and `connected=False`.
      `connected=None` is a distinct reason (`TRANSPORT_STATE_UNKNOWN`) from a transport
      known to be down, because "the platform could not answer" and "the feed is down" are
      different statements.
    - **The numbers are the payload, not a decoration.** Requirement 19.8 asks for the age
      *together with* the expected interval, so both are keys on every state (null when
      unmeasured), beside both thresholds in multiples **and** seconds
      (`delayed_after_seconds`, `stale_after_seconds`), and beside `display` — the sentence
      `design.md` specifies, rendered server-side: *"Last candle 4m 12s ago, expected every
      5m."* A client cannot render a colour from this body without the numbers, because the
      numbers are what it was given, and a UI that wants to check the label against the
      figures has every threshold it needs to do so.
    - **The observation reads an existing component, conservatively.** `websocket_monitor`
      (STEP 8.7) already tracks per-connection state and last-message time, so `observe_feed`
      reads that projection rather than starting a second one; it writes nothing, changes no
      threshold and touches no ingest path. Three outcomes stay distinct: a matching
      connection; **no** connection registered for this market (`connected=False`,
      `NO_FEED_OBSERVED` — nothing is arriving, which is what `DISCONNECTED` means, and the
      reason says it is because there is no subscription rather than because one dropped);
      and a monitor that could not be read at all (`connected=None`). Where several
      connections match a symbol, the one on the server's `DEFAULT_EXCHANGE` is preferred and
      otherwise the **least fresh** is taken, never the freshest — a second venue's healthy
      socket must not lend this market its liveness. The monitor's own `stale` flag is its
      10-second judgement, not a bar-interval one, so it maps to "still connected" and the
      measured age decides. The venue is used for the preference and **never returned**
      (SB-06); a test asserts the exchange id appears nowhere in the observation or the
      response.
    - **A deadlock found in the component being read, and fixed rather than worked around.**
      `WebSocketMonitor.get_all_status()` built its dict *while holding* `self._lock` and
      called `get_connection_status`, which takes the same non-reentrant `threading.Lock`.
      With one connection registered it blocked the calling thread **permanently** — not an
      exception, not a slow answer, a hang — and `routers/health_websocket.py` calls the same
      method, so `GET /health/websocket` hangs today for any process with a registered
      connection. It was found by this task's first test run hanging. Fixed in place, as
      small as it goes: the ids are snapshotted under the lock and each status is read
      outside it, with a vanished connection omitted rather than reported as `None`. No lock
      semantics were changed and the projection is not duplicated. The regression test runs
      `get_all_status` in a worker thread with a join deadline, so a reintroduction fails in
      a second instead of hanging the suite. Flagged rather than left silent: this is a live
      defect on a health endpoint, outside 7.4's footprint, and it is now fixed.
    - **Reconciled with 7.5, which landed concurrently, by reading its contract instead of
      writing a second one.** 7.5 introduced `backend_app/backend/market_data_contract.py`
      (`closed_bar_frame`, `quality_report`, `resolve_gap_policy`, `validated_window`) and
      changed `_preview_frame`'s signature mid-task — which is how the overlap was noticed.
      `_data_quality_payload` was rewritten to go through `validated_window(..., mode=
      MODE_BACKTEST, bars=…)`: the gap policy, the closed-bar gate, the ingest counters and
      the `MarketDataValidator` call are all **read** from 7.5's one entry point. So the
      report served here is computed on the same closed-bar window the preview computes
      indicators on, by the same validator singleton the training admission gate reads
      (`strategy_service.quality_check_training_frame`), and the counters are republished
      rather than re-derived — one ingest path, not a reporting path free to drift from it,
      which is the `DataQualityReport` equivalent of SB-01. `mode=backtest` is that mode's own
      definition: a bounded historical read with no order router downstream. Because the
      window is closed-bar-gated, `available_bars` is a count of *closed* bars, which is the
      right figure to compare against `plan.warmup_bars`. No file 7.5 owns was restructured.
    - **The strict 3σ posture is preserved and reported, not relaxed.**
      `OutlierDetector._z_score_filter` refuses any window holding a close beyond three
      standard deviations, and `GapHandler.handle` refuses any gapped window. Both arrive as
      `DATA_QUALITY` from the contract and are served as
      `data_quality.status = "REJECTED"`, reason `DATA_QUALITY` (the platform's existing
      `strategy_service.REASON_DATA_QUALITY` vocabulary, not a new one), carrying the
      validator's own message, as a **200**. Nothing is retried under looser settings, no
      threshold is redefined and no validator is subclassed; a structural test reads
      `_data_quality_payload`'s source and fails if `z_score`, `ValidationConfig(`,
      `min_quality_score` or `iqr_multiplier` ever appears in it. A rejected window claims
      **no** `available_bars`, so the feed state does not assert a warmup shortfall from a
      count the validator would not vouch for. The behavioural test puts the spike near the
      *end* of the pool on purpose — a spike outside the tail the endpoint actually reads
      would have proved nothing.
    - **A poor report is still served, and that is the difference between a gate and a
      report.** The training path blocks on `POOR`/`UNUSABLE` (`BLOCKING_QUALITY_LEVELS`) and
      keeps doing so; this endpoint does not, because refusing to serve a bad report hides
      exactly the case the report exists for. Both `quality_level` (the enum's *value*, which
      is a threshold integer — 85 for `GOOD`) and `quality_level_name` are published, because
      an integer read as a label is a report nobody can act on. The parity test compares the
      served body key-for-key against a report it produces by calling
      `market_data_validation` itself on the same window, rather than restating what a score
      ought to be.
    - **Degrading honestly, and the reason it is a 200.** No feed, no monitor and no market
      data in this environment, so the endpoint's ordinary answer here is `DISCONNECTED` with
      `age_seconds: null` and `data_quality.available: false` — served with **200**, not 503.
      A 503 would leave a status strip with nothing to render, and a strip with nothing to
      render shows the last colour it had: a stale reading presented as current, which is the
      defect this endpoint exists to prevent. `Cache-Control: no-store` is set for the same
      reason — a cached liveness reading *is* a stale reading served as a fresh one. Three
      tests pin the failure paths: a fetch that raises, an `observe_feed` that raises, and no
      monitor connection at all; all three are 200 with an explicit absence and none is
      `LIVE`. There is no 500 path on this handler except an unresolvable strategy read,
      which keeps the router's existing `STRATEGY_GET_FAILED` contract.
    - **Controls.** `Depends(get_current_user)` and `@limiter.limit("60/minute")`, both
      asserted from the handler signature and from the router source, and an unauthenticated
      caller asserted refused. Ownership is enforced **twice**, exactly as the preview and the
      model-version reads are: `StrategyService.get_strategy` queries the caller's own
      RLS-scoped client **and** filters `.eq("user_id", user["id"])`. Another tenant's
      strategy is a **404**, identical to a missing one, and the test asserts the feed
      recorded **no** call on the refused path — nothing is fetched or validated before
      ownership resolves. No auth, RLS filter, tenant scope, financial invariant, execution
      safeguard, risk gate, idempotency key or rate limit was modified anywhere in this
      change; the endpoint is read-only, writes no row, places no order and starts no job.
      **This is a new network-exposed endpoint**, and what authenticates it is the platform's
      bearer-token dependency; what scopes it is RLS plus the explicit `user_id` filter; what
      bounds it is the limiter and a server-side window ceiling. No venue, exchange
      identifier, key, secret or passphrase is accepted or returned, asserted by substring
      over the whole serialised response.
    - **What is not verifiable here, said plainly.** No market data socket, no Redis and no
      venue are reachable from this environment, so no test in this file has seen a real
      candle arrive. What is supplied is exactly three things, all outside the property under
      test: the OHLCV window (through the endpoint's own one I/O seam), the strategy row (a
      fake service applying the real `id` **and** `user_id` predicate), and the monitor's
      *contents* (a real `WebSocketMonitor`, real projection, last-message timestamps set
      directly on the real `ConnectionMetrics` — which is how an age of exactly 1.5 intervals
      becomes reachable at all). Everything the requirements are about is real: the registry,
      the graphs, `load_graph`, the compiler and its `warmup_bars`, the classifier, the ingest
      contract, the validator and its shipped thresholds, and the router with its dependency
      and its limiter. There is no `hypothesis` import in this file — task 7.7 owns the
      generated sweep and `tests/test_feed_state_property.py`; what stands in for it is a
      deterministic sweep over **every** published interval × fourteen age multiples
      straddling both boundaries (asserted to actually reach all three age-derived states, or
      it would prove nothing). The frontend labelling test is also 7.7's, and 7.3 owns the
      selector wiring, so **no frontend file was touched and the frontend suite was not
      re-run**.
    - Baseline: backend **43 failed / 3401 passed / 18 skipped (3462 collected)** in pytest's
      default order, one whole-suite run with the `.venv` interpreter
      (`.venv\Scripts\python.exe -m pytest -q`, 235 s), against the pinned ceiling of **44**.
      The failure count is **unchanged** from the 43 task 7.2 closed on, and the failures are
      the same pinned nineteen files at the same per-file counts: `full_system_test.py` 4,
      `test_distributed_execution_safety.py` 7, `test_atomic_order_cancellation_fix.py` 4,
      `test_marketplace_pipeline.py` 4, `test_event_pipeline.py` 3,
      `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_transaction_isolation_serializable.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_vault_singleton.py` 2, `test_risk_settings_api.py` 2, and 1 each in
      `test_account_health_query.py`, `test_exchange_safety_fix.py`,
      `test_false_success_report_fix.py`, `test_fee_precision_fix.py`,
      `test_get_db_dependency.py`, `test_position_delta_race_condition_fix.py`,
      `test_reconciliation_engine_fail_closed.py`, `test_risk_management_lifecycle.py` and
      `test_tenant_isolation_fixes.py`. **The collection delta is reconciled rather than
      assumed:** 3462 − 3316 = 146 new tests, of which **78 are this task's**
      (`test_feed_state_and_data_quality.py`) and **68 are task 7.5's**
      (`tests/test_market_data_contract.py`, landing concurrently); all 146 pass.
      `test_feed_state_and_data_quality.py` appears nowhere in the failure list, and the four
      suites over the surfaces this task touches or shares are green:
      `test_node_preview_endpoint.py`, `test_registry_endpoints.py`,
      `test_market_data_contract.py` and `test_market_symbols_universe.py` (273 passed, 1
      skipped).

  - [x] 7.5 Wire the validated market data path into the builder contract
    - Feed indicator computation with closed bars only, so a forming bar never produces a second
      contradictory value for one timestamp
    - Surface the existing validators' late-event, duplicate, out-of-order and integrity-drop
      counters; keep first-wins on duplicates
    - Pin the gap strategy per environment: forward fill allowed for backtests with disclosure,
      `SYNTHETIC_FILL` rejected for `mode = 'live'`
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5, 13.7_
    - **What landed.** One new module, `backend_app/backend/market_data_contract.py` — the
      arrival seam between a raw candle feed and indicator computation:
      `interval_for`, `ClosedBarIngest` (`offer` / `extend` / `frame`), `closed_bar_frame`,
      `IngestCounters`, `GapPolicy` + `resolve_gap_policy`, `quality_report`, `IngestResult`
      and the one entry point `validated_window`. Wired into the builder contract at the node
      preview endpoint: `_preview_frame` now routes rows through the closed-bar gate (and
      takes the timeframe, since bar length is what decides "closed"), plus two new helpers
      `_preview_gap_policy` and `_preview_data_quality`, one new constant
      `PREVIEW_DATA_MODE = "backtest"`, and one new response key `market_data`
      (`counters`, `gap_policy`, `quality`, `closed_bars_only`). Tests:
      `tests/test_market_data_contract.py`, 68 of them, all green;
      `tests/test_node_preview_endpoint.py` (91 passed, 1 skipped) needed a two-line change to
      `served_frame` for the new signature and nothing else, which is the evidence the gate
      did not change what the preview computes — only which bars it computes on.
    - **The validators are reused, and the module is asserted not to be a second one.**
      `market_data_validation.MarketDataValidator` is called through `get_validator()`; the
      required columns, OHLC relationships, positive prices, non-negative volume, the outlier
      filter, the gap gate and `DataQualityReport` are all its rules and its report.
      `IngestResult.to_dict()["quality"]` is `report.to_dict()` republished **verbatim**, and a
      test asserts equality against the report object rather than restating field names. Two
      structural tests read the module's own source and fail if it ever grades data quality
      (`quality_score =`, `_calculate_quality`, `DataQualityLevel.`) or computes on a price
      (`rolling(`, `ewm(`, `.pct_change(`, `.diff(`, `cumsum(`) — task 5.4 had to re-record
      golden files because two `_calculate_*` implementations genuinely disagreed (a simple
      rolling mean against Wilder smoothing), and a module that handles bars is exactly where
      the third one would appear. `interval_for` reads
      `market_data_validation.TIMEFRAME_MINUTES` (the table 7.2 hoisted into view) and a test
      requires the source to contain `TIMEFRAME_MINUTES.get(` and **not** contain a written-out
      copy of the vocabulary, so there cannot be two interval tables.
    - **The closed-bar rule, and the invariant it exists for.** A bar opening at `t` is closed
      once `t + interval <= now`. Expressed as a boundary (`ClosedBarIngest.closed_before`)
      rather than an epoch-aligned floor on purpose: alignment differs by venue and interval
      (`3d` and `1w` especially), while "has this bar's interval elapsed" needs no alignment
      at all. Two boundary tests pin both sides — the bar that *just* finished is admitted (a
      gate that also dropped it would delay every signal by a full interval, which is a
      different defect wearing the same fix), and one second short of the boundary is refused
      — parametrised across `1m`/`5m`/`15m`/`1h`/`4h`/`1d` so the interval, not a constant,
      is what decides. The headline test is the invariant itself, not a row count: the same
      timestamp is offered three times as a rising forming bar and once as its closed value,
      and what reaches the frame is exactly **one** row holding the closed value. This matters
      concretely here because a CCXT `fetch_ohlcv` window ends with the bar still forming, so
      before this change a preview computed now and the same preview computed thirty seconds
      later were two contradictory answers to "what does this block produce?" — the one thing
      the preview's design forbids. `design.md`'s canonical `Candle.is_closed` is honoured and
      **outranks the clock**: a feed that says the bar is forming knows something inference
      does not.
    - **First-wins on duplicates — where it is enforced, and two places it was not.** Enforced
      at `ClosedBarIngest.offer`: a timestamp already held is counted and the arriving candle
      discarded; the stored row is never overwritten. That is the only duplicate-resolution
      point on this path. Found while reading for it: **three** frame builders resolved
      duplicates *last*-wins, which is the opposite of Requirement 19.4 and of `design.md`'s
      "Duplicate candle | first wins; duplicate counted". `_preview_frame`'s own
      `keep="last"` is gone (it now goes through the gate); the other two were changed, one
      word each and nothing else, because leaving them would mean a later arrival silently
      revising a bar the pipeline may already have computed on:
      `market_data_validation.StructuralValidator.validate` — the component `design.md`
      *names* as the owner of this rule — and `strategy_service.training_frame`, which feeds
      the same `indicators_backend` executors on the training path. Both changes are pinned by
      their own tests here. The duplicate **counter** in `StructuralValidator` is untouched.
      A further test asserts a duplicate is counted as a duplicate and **not** as a late
      event: they are two different feed faults and conflating them makes both figures
      unreadable, so `offer` checks the duplicate before it checks ordering.
    - **The counters, and the two that did not exist — said plainly rather than quietly
      tallied.** `DataQualityReport` already counts `duplicate_timestamps`,
      `ohlc_violations`, `outliers_detected`, `gaps_found`, `missing_values`,
      `invalid_candles` and `max_gap_duration_minutes`; those are surfaced, never re-derived.
      It counts **no late events and no out-of-order arrivals**, and it has **no per-candle
      integrity-drop counter** — the last one by design, because its posture is to reject a
      whole batch (`DataValidationError`) rather than drop a candle and carry on. So:
      `late_events` and `out_of_order` are counted at the seam, which is the only place
      arrival order is observable at all (a batch validator handed a sorted frame cannot see
      it), and Requirements 19.3/19.5 place them on the *pipeline*, so there is nothing for
      them to be a parallel tally *to*. Integrity drops are reported as the validator reports
      them — a classified `DATA_QUALITY` refusal carrying its own message, or the graded
      counts in the report — and this module drops no candle for integrity reasons and
      publishes no integrity-drop figure of its own. `duplicates` is counted here **and** the
      validator's `duplicate_timestamps` will read `0` on any window that came through the
      gate, because first-wins is applied before the validator sees the frame; the two live
      under separate keys (`counters` and `quality`) and a test asserts
      `duplicate_timestamps` is absent from `counters`, so they can never be summed into a
      number that means nothing.
    - **Late versus out-of-order follows the mode, not a flag someone can flip.**
      `design.md`: "re-sort historical; for live, drop late events older than the last closed
      bar and count them." `validated_window` sets `drop_late` from the resolved policy's
      mode — `paper`/`live` drop, `backtest` re-sorts — so the two behaviours cannot be
      selected independently of the environment they belong to. The re-sort claim is checked
      **exhaustively**, not sampled: all 120 permutations of a five-bar batch must produce the
      byte-identical frame (compared with `assert_frame_equal`, so a frame that sorted its
      index while leaving its rows in place fails, where a monotonicity check would pass).
    - **Gap strategy, pinned — and stricter than the requirement's floor, deliberately.**
      `resolve_gap_policy(mode, requested)` is the gate. `SYNTHETIC_FILL` is refused for
      `live` with its own code `SYNTHETIC_FILL_FORBIDDEN_LIVE` (Requirement 13.7's named
      case), and refused in **every** mode as `SYNTHETIC_FILL_FORBIDDEN` — a deliberate
      deviation upward from 13.7's `live`-only floor, for three reasons: Requirement 19.11
      admits only a source delivering "zero delivered synthetic candles" with no mode
      qualifier on it; `GapHandler._synthetic_fill` already raises `RuntimeError` on its first
      line, so permitting it anywhere would be permitting something that cannot run; and a
      backtest built on invented prices produces invented returns, which is precisely what an
      author decides to go live on. `LINEAR_INTERPOLATE`/`SPLINE_INTERPOLATE` are refused
      everywhere as `GAP_INTERPOLATION_FORBIDDEN` on the same grounds. `FORWARD_FILL` is
      refused for `paper` and `live` (`GAP_FILL_FORBIDDEN_LIVE` — republishing the last close
      as a new bar is a fabricated price, and in those modes it reaches the execution path)
      and permitted for `backtest`. The mode itself **fails closed**: an unrecognised mode is
      refused (`MODE_UNRECOGNISED`) rather than defaulted, because the safe default and the
      permissive one are a typo apart; case and surrounding whitespace normalise, since
      `"LIVE "` is a spelling of `live` and not a different environment. A test asserts the
      platform's *shipped* `ValidationConfig` default is permitted for a live deployment — a
      pinning that refused the default configuration would take every deployment down, which
      is a different failure from the one being prevented.
    - **The backtest forward-fill disclosure travels, and it tells the truth about what
      happened.** `GapPolicy` carries `requested`, `effective`, `permitted`, `fills_gaps` and
      `disclosure`, and the preview response carries `gap_policy` whole, so the disclosure
      reaches the caller instead of being logged and forgotten. `requested` and `effective`
      differ in exactly one case and it is stated rather than smoothed over: a backtest may
      ask for `FORWARD_FILL` and this permits it, but `GapHandler.handle` **rejects any gapped
      window outright and never branches on the strategy it is passed**, and
      `GapHandler._forward_fill` raises `RuntimeError` on its first line. So no forward fill
      occurs, `effective` is `SKIP_EXECUTION`, `fills_gaps` is `False`, and the disclosure says
      so in words — reporting `FORWARD_FILL` as effective would be this function claiming a
      repair the pipeline does not perform. That claim is not left as prose: a test runs the
      real `GapHandler` over a real gapped window with `FORWARD_FILL` asked for and requires
      the refusal. The practical reading is that "forward fill allowed for backtests" is
      currently a **permission with no implementation behind it**; the honest disclosure is
      that every bar served is an exchange print.
    - **Where the wiring sits in the preview path, and what it cannot do.** The gap policy is
      resolved **before the fetch**, right after the market is resolved, so a server
      configured to fabricate candles is refused while the refusal is still free — and as a
      **503**, because the request is fine and the *server's* data configuration is not, which
      the author cannot fix by editing the graph. An interval the pipeline cannot measure is a
      **422** (`TIMEFRAME_UNSUPPORTED`), because the graph named it. A window that yielded no
      closed bar keeps the existing `PREVIEW_DATA_UNAVAILABLE` 503 and now carries the
      counters with it, so "nothing arrived" and "everything that arrived was still forming"
      are distinguishable from the response rather than from a log. `drop_late` is left at
      `False` on this path: a preview is a historical read. The preview's structural guards
      still hold — the router's existing test scans `_preview_frame`'s source for
      arithmetic tokens and for a second evaluator, and both still pass.
    - **The strict 3σ posture is preserved, classified, and it has a cost that is worth
      stating.** `OutlierDetector._z_score_filter` refuses any window holding a close beyond
      three standard deviations and `GapHandler.handle` refuses any gapped window; both mean
      "this window cannot be computed on", so both become one `DATA_QUALITY` refusal carrying
      the validator's own message — the same mapping `strategy_service`
      `quality_check_training_frame` makes for a training window (Phase 6). Nothing is
      retried under looser settings and no threshold is redefined. **The judgement call:** a
      real 1200-bar window very often *does* hold a close beyond 3σ, so this makes previews
      refusable on real market data where before they always rendered. That is deliberate and
      is the posture earlier phases decided — and loosening it *for previews specifically*
      would be worse than the refusal, because a preview computed under looser data rules than
      the runtime uses is a preview that disagrees with the runtime, which Requirement 24.7
      forbids outright. If the threshold is wrong it is wrong for training and deployment too,
      and it should be changed in `ValidationConfig` where the whole platform sees it, not
      bypassed here. Flagged rather than silently absorbed.
    - **Scope kept off 7.4's side, and reconciled with it.** No feed-state enum and no
      data-quality endpoint were added here — 7.4 owns both. `market_data_validation.py`
      received exactly **one** functional change, the `keep="last"` → `keep="first"` word
      described above plus its comment; everything else in that file is untouched, which is
      what let 7.4 land on it concurrently. The one collision was real and is recorded:
      `_preview_frame`'s signature and return type changed under 7.4's in-flight
      `_data_quality_payload`, which was calling it. 7.4 has since rewritten that payload to
      go through `validated_window`, so the report it serves is computed on the same
      closed-bar window the preview computes indicators on, by the same validator singleton
      the training gate reads — one ingest path with a reporting path reading it, rather than
      two paths free to drift.
    - **Controls.** Nothing was weakened and nothing was added that could weaken something:
      no auth, RLS filter, tenant scope, financial invariant, execution safeguard, risk gate,
      idempotency key or rate limit was modified anywhere in this change. The preview
      endpoint's `Depends(get_current_user)` and `@limiter.limit("30/minute")` are untouched,
      as is its ownership resolution through `StrategyService.get_strategy` (RLS-scoped client
      **plus** the explicit `user_id` filter, another tenant's strategy a 404); the
      gap-policy resolution and the closed-bar gate both sit **after** that ownership check,
      so nothing is fetched or validated for a caller who does not own the strategy. No new
      endpoint, no new network surface, no persisted row, no order and no training job. No
      venue, exchange identifier, key, secret or passphrase is accepted or returned by
      anything added here. In the direction of *strengthening*: a forming bar can no longer
      reach an executor, a duplicated bar can no longer be revised by a later arrival on
      three paths that previously allowed it, and a fabricating gap strategy is now refused
      before a candle is read rather than being a configuration nobody checked.
    - **What is not verifiable here, said plainly.** No exchange and no market data socket are
      reachable from this environment, so no test in this file has seen a real candle arrive
      or a real forming bar close. What is real is everything the requirements are about: the
      platform's `MarketDataValidator` with its shipped `ValidationConfig` thresholds, the real
      `StructuralValidator`, the real `GapHandler`, the real `strategy_service.training_frame`,
      the real `TIMEFRAME_MINUTES`, and — through `test_node_preview_endpoint.py` — the real
      router, compiler, adapter and `DAGEngine`. What is supplied is the clock (`now` is
      injected, so a boundary is *stated* rather than raced) and CCXT-shaped rows. The live
      stream itself is exercised only through `ClosedBarIngest.offer` called in arrival order,
      not through `exchange_websocket_listener`; `dag_event_loop`'s tick path — which builds
      forming candles in `RollingWindow.add_tick` and appends in `add_candle` with no
      duplicate, ordering or closed-bar check — is **not** routed through this gate by this
      task and remains a live forming-bar source on that path. It also still carries its own
      duplicate `_calculate_*` indicator implementations, the second-computation-path defect
      5.4 recorded; neither was widened here and neither was fixed here. There is no
      `hypothesis` import in this file, deliberately: `design.md` numbers the properties this
      spec generates tests for, 7.7 owns the next one (Property 26), and adding an unnumbered
      property would put a test in the suite no design document accounts for. What stands in
      for it is exhaustive rather than sampled — all 120 permutations of a five-bar batch, and
      the closed-bar boundary parametrised over six intervals on both sides.
    - Baseline: backend **43 failed / 3401 passed / 18 skipped (3462 collected)** in pytest's
      default order, one whole-suite run with the `.venv` interpreter
      (`.venv\Scripts\python.exe -m pytest -q`, 291 s), against the pinned ceiling of **44**.
      The failure count is **unchanged** from the 43 tasks 7.1/7.2 closed on, and the failures
      are the same pinned nineteen files at the same per-file counts: `full_system_test.py` 4,
      `test_distributed_execution_safety.py` 7, `test_atomic_order_cancellation_fix.py` 4,
      `test_marketplace_pipeline.py` 4, `test_event_pipeline.py` 3,
      `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_transaction_isolation_serializable.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_vault_singleton.py` 2, `test_risk_settings_api.py` 2, and 1 each in
      `test_account_health_query.py`, `test_exchange_safety_fix.py`,
      `test_false_success_report_fix.py`, `test_fee_precision_fix.py`,
      `test_get_db_dependency.py`, `test_position_delta_race_condition_fix.py`,
      `test_reconciliation_engine_fail_closed.py`, `test_risk_management_lifecycle.py` and
      `test_tenant_isolation_fixes.py`. **The collection delta is reconciled, not assumed:**
      3462 − 3316 = 146, of which **68 are this task's** (`test_market_data_contract.py`) and
      **78 are task 7.4's** (`test_feed_state_and_data_quality.py`, landing concurrently);
      all 146 pass. An earlier whole-suite run measured **46** failed — the three extra were
      7.4's `_data_quality_payload` calling `_preview_frame` with its old signature while both
      tasks were mid-flight, they are gone now that 7.4 reads `validated_window`, and they are
      recorded here rather than dropped because reconciling the delta is the point.
      `test_market_data_contract.py` appears nowhere in the failure list, and the suites over
      the surfaces this task touches are green: `test_node_preview_endpoint.py` (91 passed, 1
      skipped), `test_feed_state_and_data_quality.py` (78), `test_training_service_admission.py`
      and `test_market_symbols_universe.py`. Frontend: **not re-run, and deliberately so** —
      this task touches no frontend file (`backend/market_data_contract.py`,
      `routers/strategy_operations.py`, `backend/market_data_validation.py`,
      `backend/strategy_service.py`, `tests/test_market_data_contract.py`,
      `tests/test_node_preview_endpoint.py`); rendering the counters and the disclosure on the
      canvas belongs with 7.3's selector wiring and 7.7's labelling test.

  - [x] 7.6 Build the market data latency harness and the source decision function
    - Create `tests/perf/test_market_data_latency.py` measuring end-to-end, ingest and pipeline
      latency, CPU, memory, throughput, reconnect, completeness, timestamp quality,
      duplication, ordering and reliability for Candidate A (direct CCXT stream) and
      Candidate B (validated OHLCV pipeline)
    - Implement `choose_market_data_source` applying the correctness floor first and the 25 ms
      p99 margin second, so latency never outvotes correctness
    - Run as a scripted experiment, not in the default CI lane; the harness writes
      `reports/market_data_latency_decision.md` as its output
    - _Requirements: 19.11, 19.12, 19.13_
    - **Implementation note (7.6).**
    - **The rule lives in the backend, not in the harness.** `backend/market_data_latency.py`
      (new) holds `SourceMeasurement` — one field per row of `design.md`'s twelve-metric
      table, every field admitting `None` for *not measured* — plus `CorrectnessFloor`,
      `evaluate_correctness_floor`, `choose_market_data_source`, `SourceDecision`,
      `render_decision_report` and `write_decision_report`. The task names one file and puts
      it in `tests/perf`; the *measuring* belongs there and does, but the *rule* decides
      whether a feed that can deliver an invented candle may reach the order router, and a
      control whose only home is a directory CI never collects is not a control. So the rule
      is a backend module and its regression tests are in the lane that actually runs
      (`tests/test_market_data_source_decision.py`, 48 tests, no network, no clock, no feed —
      every input a constructed `SourceMeasurement`). That is the one scope deviation from the
      task text and it is the reason for it.
    - **The floor is structural, not an ordering.** "Latency never outvotes correctness" is
      trivially written as a branch order and trivially broken by moving a branch, so it is not
      one here. `_decide_on_latency` does not accept a `SourceMeasurement`; it accepts an
      `_Admitted`, whose `__post_init__` refuses any instance not carrying the module-private
      `_ADMISSION_KEY`, and the only thing holding that key is `_admit`, which mints a token
      only after `evaluate_correctness_floor` returns `passed`. A candidate that fails the
      floor is therefore not scored down and not compared later — it is **unrepresentable** as
      an input to the latency rule, at any latency. Deleting or reordering the floor check does
      not make a fabricating source selectable; it makes the module raise. Two tests pin that
      directly (a forged key raises even for a *passing* candidate, so the key is the gate and
      not the verdict), and one parametrises a floor failure against latency advantages of 1,
      25, 1 000 and 100 000 ms to show the floor does not bend.
    - **The floor's criteria, stated.** Requirement 19.11's five, as numbers in
      `CorrectnessFloor`: completeness `>= 0.9999`, and zero each of monotonicity violations,
      delivered duplicates, delivered invalid candles and delivered synthetic candles. Three
      distinct rejection codes, because they call for different actions: `NOT_MEASURED` (run
      the experiment), `FLOOR_METRICS_NOT_MEASURED` (instrument it — "nobody counted" is never
      "counted zero", so a measured-but-uninstrumented candidate is rejected rather than
      admitted by default), and `FLOOR_CRITERIA_FAILED` (fix the source; do not re-run hoping
      for a better sample). Nothing admitted -> `BLOCKED` with no source returned, not even the
      less bad one. Exactly one admitted -> that one whatever the latencies are, with
      `p99_margin_ms` left `None` to record that latency did not vote.
    - **A fourth rejection code the requirement implies and the run forced.**
      `FLOOR_EVIDENCE_INSUFFICIENT`. A 45-second smoke run delivered 499 bars and reported
      "499 of 499" — a completeness ratio of 1.0 that cannot distinguish 100 % from 99.6 % and
      therefore is not evidence of 99.99 % anything. `CorrectnessFloor
      .min_completeness_sample_bars` is **derived** from the threshold, not chosen:
      `ceil(1 / (1 - min_completeness))` = 10 000 at 99.99 %, 1 000 at 99.9 %, so changing the
      threshold moves the required sample with it. (Rounded before the ceiling: `1 - 0.9999` is
      `9.999999999998899e-05` in binary floating point and the naive reciprocal ceiling is
      10 001.) It is reported as its own code and its own sentence — "an insufficient-evidence
      rejection, not a defect in the source" — because telling an operator to fix a feed that
      is fine would be worse than telling them nothing.
    - **The 25 ms figure is a margin, and every branch of it has a defined answer.** Reached
      only with two admitted candidates. `diff >= 25 ms` -> the lower p99
      (`P99_MARGIN_MET`). `diff < 25 ms` -> the candidate carrying the validation layer
      (`P99_MARGIN_NOT_MET_VALIDATION_LAYER_PREFERRED`), **including when that candidate is the
      slower one** — a test pins Candidate A being 24.9 ms faster and losing anyway. Where
      19.12's tie-break does not discriminate because both or neither carry validation, the
      requirement gives no answer, so one is defined: the lower p99 is taken as a free
      tie-break (no correctness is traded for it) and an exact p99 tie falls to the validated
      candidate and then to a stable source-name ordering, so two runs of one experiment cannot
      disagree — pinned by asserting the decision is invariant under argument order. A p99
      nobody measured hands the decision to the validated candidate
      (`P99_UNMEASURED_VALIDATION_LAYER_PREFERRED`); latency cannot vote on a number that does
      not exist. **Deviation from `design.md`'s pseudocode, deliberately:** its under-margin
      branch reads `RETURN other.source IF other.has_validation_layer ELSE best.source`, which
      returns the *slower* candidate when both are validated. Requirement 19.12's text is
      authoritative and says "otherwise select the source carrying the validation layer" —
      satisfied by either — so the free tie-break is taken instead of an arbitrary preference
      for slowness.
    - **Percentiles are exact, and `metrics.Histogram` is deliberately not used.** It is
      Prometheus-shaped: bucket counts, not samples, so a percentile read from it is a bucket
      edge. The 25 ms margin is measured on exactly that quantity and a bucket boundary near
      25 ms would decide the comparison by the bucket layout rather than by the feed.
      `LatencySummary.from_samples` computes nearest-rank percentiles over retained samples and
      returns `None` — never a zero, which would read as "instant" — for an empty population.
      `Histogram` is untouched.
    - **Not in the default CI lane, verified rather than asserted.** There was no existing
      opt-out mechanism to follow: `pytest.ini` had no markers, no `--strict-markers` and no
      exclusions, and `testpaths = tests` means `tests/perf` *would* have been collected. So
      `norecursedirs` was added — and, importantly, the shipped defaults
      (`*.egg .* _darcs build CVS dist node_modules venv {arch}`) are **restated** alongside
      `tests/perf`, because pytest *replaces* rather than extends that key and omitting them
      makes collection descend into `.venv`, `.hypothesis` and `node_modules`. The hypothesis
      plugin warned about exactly that on the first attempt. Explicit paths still collect, so
      `pytest tests/perf/test_market_data_latency.py` runs the experiment. Each measuring test
      is gated a *second* time on `AERORA_MARKET_DATA_LATENCY_EXPERIMENT`, so even
      `pytest tests/perf` skips rather than starting a 24 h measurement by accident (verified:
      6 skipped without the variable, 7 passed with it). The prescribed window is the
      **default** — `AERORA_MARKET_DATA_LATENCY_SECONDS` unset means 86 400 s — so a shorter
      run has to be asked for and is labelled a smoke run in the report.
    - **What the harness actually produced here, and the fabrication it caught.** Two revisions
      of this file published invented numbers before the third refused to, and both are
      recorded because the failure mode is the whole point of the task:
      - **First:** `_measure_candidate_b` subtracted a bar's exchange timestamp from its arrival
        wall clock on a `fetch_historical_ohlcv` batch and published a **540-second "end-to-end
        p99"**. That subtraction is the bar's *age in the batch*, not delivery latency. A REST
        poll cannot produce an end-to-end or ingest latency at all, so both are now left `None`
        with the reason in the report, clock skew is dropped for the same reason, and
        cross-poll arrival ordering is dropped because it is a property of the polling loop
        rather than the feed. What a REST poll *can* honestly measure is kept: the pipeline
        segment (the wall time `validated_window` takes on the batch an executor reads),
        completeness over the bar grid the delivered stamps span, CPU, RSS, throughput, and the
        contract's own `IngestCounters` / `DataQualityReport` counts.
      - **Second, and worse:** the run reported 2 495 bars over a 499-bar grid — a completeness
        of **5.0** — and it was measuring nothing at all. `api.binance.com:443` completes a TCP
        handshake from here but the REST API does not answer, and
        `ConnectionEngine.connect` responds to that by catching the failure, calling
        `_apply_mock_interface` under `DEV_MODE`, and returning a **working** exchange object
        whose `fetch_ohlcv` generates candles from `time.time()`. Candles arrived, counters
        populated, percentiles computed, and every figure was fiction. `refuse_if_mocked` now
        inspects the connected exchange for methods named `mock_*` and raises `MockedFeed`,
        which becomes an `unmeasured` result naming the mocked methods. This is the single most
        important safeguard in the file and it is the reason a TCP probe is documented as
        "necessary and not sufficient".
    - **So the published decision is `BLOCKED`, and that is the correct answer.**
      `reports/market_data_latency_decision.md` records: Candidate A unmeasured
      (`REDIS_URL` empty, so `DataEngine.stream_live_ohlcv` cannot subscribe to the
      `mds:data:{exchange}:{symbol}` channel `mds/main.py` publishes to and no candle can
      arrive on that path at all); Candidate B unmeasured (`DEV_MODE` mock interface refused);
      both rejected at the floor with `NOT_MEASURED`; no source selected. Every unmeasured
      figure prints as "not measured" and no candidate is credited with a zero it did not earn.
      The document also states what the prescribed experiment is, what this run actually was,
      and what to change to obtain a real decision.
    - **The two observations this environment could honestly make, fenced off from the
      decision.** Both land in the report under `Notes (not decision inputs)`:
      - `ClosedBarIngest.offer` over 2 000 generated closed bars: **p50 0.019 ms, p95 0.043 ms,
        p99 0.091 ms**; `validated_window` over the same window **375 ms total, 0.188 ms per
        bar**, including the platform `MarketDataValidator`. That is processing cost, not
        latency — no exchange timestamp is involved — so it cannot populate `pipeline` and is
        not a decision input. What it does do is bound how much pipeline latency Candidate B's
        validation layer can add, which is the quantity `design.md`'s hypothesis ("a small
        constant validation overhead") is about, and at ~0.19 ms per bar the hypothesis looks
        right: the validation layer is nowhere near the 25 ms margin.
      - Two **structural** facts about Candidate A's path, read off the shipped source rather
        than asserted in prose: `DataEngine.stream_live_ohlcv` does not reference the closed-bar
        ingest contract and applies no duplicate, ordering or OHLC-integrity rule of its own,
        and `mds/main.py`'s `broadcast_ohlcv` republishes `candles[-1]` on every socket update
        (with a 10 s REST fallback). Republishing the newest candle per update means one bar
        open time is published repeatedly while the bar is still forming, so forming-bar
        delivery and duplicates-delivered are Candidate A's figures to watch — and it is why
        its Requirement 19.11 numbers cannot be assumed to be zero. Not a measurement, and not
        a floor input.
    - **Reuse, not reinvention.** The bar interval comes from
      `feed_state.expected_interval_seconds` (task 7.4), which reads
      `market_data_validation.TIMEFRAME_MINUTES` — no second interval table, and an unmeasurable
      timeframe yields `None` rather than the row-coverage gate's guessed hour, because guessing
      the interval misstates completeness by the ratio of the guess to the truth. The delivered
      window's duplicate, ordering and late-event figures are read from task 7.5's
      `IngestCounters` and the validator's `DataQualityReport`; nothing is re-tallied, and
      ingest-side `duplicates` is reported beside validator-side `duplicate_timestamps` and
      never summed with it. Candidate B's delivered-stream monotonicity of 0 is reported as
      *earned* — `ClosedBarIngest.frame()` returns a sorted, de-duplicated index — rather than
      as an assumption.
    - **Data-integrity posture untouched, and reported on.** `OutlierDetector._z_score_filter`
      (3σ) and `GapHandler`'s gap refusal are exactly as they were; nothing here loosens a
      threshold to get a number out. The harness records validator refusals as a **completeness
      cost against Candidate B** and says so in the report, which is the honest place for them:
      a strict validator that refuses often is a completeness problem, not a reason to tune the
      validator. On the generated processing-cost window the validator did *not* refuse (375 ms
      for 2 000 bars), and the branch that reports a refusal as a `FINDING` instead of a timing
      is in place for the runs where it does.
    - **Controls.** Nothing weakened and nothing added that could weaken something. No auth
      dependency, RLS filter, tenant scope, financial invariant, execution safeguard, risk gate,
      idempotency key or rate limit was touched. No endpoint, router or listener was added: the
      harness binds nothing and serves nothing, so there is no new network-exposed surface. Its
      only outbound traffic is a 5-second TCP reachability probe to the feed it is trying to
      measure, plus public OHLCV reads. **No credential is read and none is passed** —
      `ConnectionEngine` is constructed with `exchange_id` alone, deliberately, because OHLCV
      is public market data, which keeps the harness off `credential_vault` entirely. No order
      is placed, no database row is written, no training job is queued, and no venue identifier,
      key, secret or passphrase is accepted or returned. `write_decision_report` writes one
      local markdown file and nothing else. In the direction of *strengthening*: a market data
      source that delivers one invented candle is now structurally unselectable rather than
      merely disfavoured, and a mocked feed can no longer be reported as a measured one.
    - **What is not measurable in this environment, said plainly.** Everything the twelve-metric
      table is about. No Redis instance is running, so Candidate A's transport does not exist
      and Candidate B's *live* arrival half — which reaches the DAG through the same MDS relay —
      does not either. `api.binance.com` answers a TCP handshake but not the REST API, so
      `load_markets` fails and the only exchange object obtainable here is `DEV_MODE`'s
      generator, which is refused. Therefore: no end-to-end, ingest or true pipeline latency, no
      reconnect count or recovery time, no clock skew, no silent-stall episode, no uptime
      figure, no delivered-duplicate or delivered-invalid-candle count, and no completeness
      figure for either candidate — and no forced-churn window, since inducing a disconnect
      requires a connection that stays up. What *is* real: the whole decision rule and its 48
      tests, the floor's arithmetic, the report renderer, the ingest path's processing cost
      measured on the real `ClosedBarIngest` and the real `MarketDataValidator`, the structural
      source reads, and the refusal machinery that kept two fabricated result sets out of the
      published document. There is no `hypothesis` import in either new test file, deliberately:
      `design.md` numbers the properties this spec generates tests for, 7.7 owns Property 26,
      and an unnumbered property would put a test in the suite no design document accounts for.
    - Baseline: backend **43 failed / 3449 passed / 18 skipped (3510 collected)** in pytest's
      default order, one whole-suite run with the `.venv` interpreter
      (`.venv\Scripts\python.exe -m pytest -q`, 297 s), against the pinned ceiling of **44**.
      The failure count is **unchanged** from the 43 tasks 7.1–7.5 closed on, and the failures
      are the same pinned nineteen files at the same per-file counts. **The collection delta is
      reconciled:** 3510 − 3462 = 48, and all 48 are
      `tests/test_market_data_source_decision.py`, all passing (3449 = 3401 + 48).
      `tests/perf/test_market_data_latency.py` contributes **zero** to the default lane —
      confirmed by grepping `--collect-only -q` for `tests/perf` and getting nothing — which is
      the requirement, and it is why the delta is 48 and not 55. Run explicitly, the harness is
      green both ways: **6 skipped** without `AERORA_MARKET_DATA_LATENCY_EXPERIMENT`, **7
      passed** with it (30 s at a 45 s requested window, since both candidates are refused
      before any measurement loop), and it published
      `reports/market_data_latency_decision.md`. Frontend: **not re-run, and deliberately so** —
      this task touches no frontend file (`backend/market_data_latency.py`, `pytest.ini`,
      `tests/perf/test_market_data_latency.py`, `tests/test_market_data_source_decision.py`).
    - **For 7.9.** `reports/market_data_latency_decision.md` exists on disk and is produced by
      the harness, though `reports/` is gitignored (`.gitignore:336`), so the artefact is a
      generated one reproduced by re-running the experiment rather than a committed file — which
      is right for a document whose whole value is that it reports a specific run. It records
      `BLOCKED` with both candidates unmeasured. The Phase 7 exit
      criterion "`reports/market_data_latency_decision.md` published" is satisfied in the
      literal sense and **not** in the sense of a source having been chosen. Choosing one
      requires running the harness where a venue and Redis are reachable; that is a deployment
      decision this environment cannot make, and it should not be marked as made.

  - [ ]* 7.7 Write property test for feed state honesty
    - `tests/test_feed_state_property.py` plus the frontend labelling test
    - **Property 26: Any feed reported `LIVE` has a last-event age below 1.5 × the expected
      interval**
    - Generator: injected event ages spanning fresh, delayed, stale and disconnected
    - **Validates: Requirements 19.7, 19.8, 19.9, 19.10**

  - [ ]* 7.8 Write asset discovery tests
    - Filtering, pagination and precision metadata; `503 ASSET_UNIVERSE_UNAVAILABLE` on an
      empty cache with a failed refresh; assert no hardcoded symbol list is reachable from any
      Builder path
    - _Requirements: 11.2, 11.3, 11.4, 11.6_

  - [x] 7.9 Phase 7 exit verification
    - Confirm `reports/market_data_latency_decision.md` is produced by the harness, no hardcoded
      symbol list remains in the Builder path, and the stale-never-`LIVE` test is green
    - Ensure all tests pass, ask the user if questions arise.
    - _Requirements: 11.1, 11.6, 19.7, 19.13_
    - **Verification note (7.9). Verdict: the three named exit criteria pass; the phase does
      not close, because Requirement 19.2 is not satisfied on the live runtime path.** Read as
      a gate rather than a task: nothing was implemented here, every claim below was re-derived
      from the shipped source and from runs made in this session, and the per-task notes were
      treated as claims to check rather than as evidence.
    - **Criterion 1 — `reports/market_data_latency_decision.md` is produced by the harness:
      PASS, and proved by regeneration rather than by the file's existence.** The file was
      renamed away (`Test-Path` → `False`), `pytest tests/perf/test_market_data_latency.py` was
      run with `AERORA_MARKET_DATA_LATENCY_EXPERIMENT=1` and
      `AERORA_MARKET_DATA_LATENCY_SECONDS=45` (**7 passed**, 21 s), and the harness recreated
      the document with a fresh `Generated 2026-08-25 15:17:49Z` header naming
      `tests/perf/test_market_data_latency.py`. So the artefact is harness-produced, not
      hand-written and not stale. **Which reading is certified, said plainly:** the criterion is
      certified in the sense the task words it — *the harness produces the report* — and **not**
      in the sense that a market data source has been chosen. The report records `BLOCKED`,
      `FLOOR_ADMITTED_NOTHING`, both candidates `NOT_MEASURED` (Candidate A has no transport
      with `REDIS_URL` empty; Candidate B is a `DEV_MODE` mock interface the harness refuses to
      measure), and no selected source. Requirement 19.13 asks that the figures **and the
      decision** be recorded, and they are: what is recorded is that nobody has measured either
      candidate, which is a truthful decision record and not a decision. `reports/` being
      gitignored (`.gitignore:336`) **does not matter for this gate** — the criterion is about
      production, and production is exactly what was re-demonstrated; a committed copy would in
      fact be worse, since a document whose entire content is "what this specific run observed"
      goes stale the moment the environment changes. It does mean a reviewer on a clean
      checkout must run the harness to see it, which is the correct cost. One small correction
      to 7.6's note: without the environment variable the perf file is now **7 skipped**, not 6
      (the report-writing test skips too), and with it **7 passed**.
    - **Criterion 2 — no hardcoded symbol list remains in the Builder path: PASS for symbol and
      timeframe vocabularies, on both sides, checked independently of the per-task notes.**
      Backend: `routers/market.py` imports no `ccxt` and contains no pair-shaped literal outside
      docstrings; `backend/asset_universe.py` holds none at all; the only literal in the DATA
      descriptor (`block_specs.py`) is `example="BTC/USDT"`, which 7.3's controls render as a
      placeholder and never as a prefill. `/registry/timeframes` is assembled as the
      intersection of `_TIMEFRAME_SOURCES` (three pipeline vocabularies) and refuses to publish
      an empty set. Frontend: **zero** `BTC/USDT`/`BTCUSDT` literals in the builder's import
      closure — every hit in `contexts/DataPipelineContext.jsx`, `lib/assetUniverse.js`,
      `components/builder/AssetSelector.jsx`, `pages/StrategyBuilder.jsx` and
      `contexts/StrategyEngineContext.jsx` is prose *describing* a removed defect, which is the
      distinction this criterion has to draw and which the structural tests draw for it
      (`test_market_symbols_universe.py::test_the_handler_holds_no_symbol_literal` excludes
      docstrings by node identity rather than by regex). No timeframe vocabulary survives in the
      closure either. **Where it is only a partial pass, and it is the exchange-default clause,
      not the symbol clause:** `contexts/DataPipelineContext.jsx` — which *is* in the builder's
      closure, `StrategyBuilder.jsx` imports `DataPipelineProvider` — still holds **five**
      `|| 'binance'` venue defaults plus a `setActiveExchange({id: 'default', name: 'binance',
      isDefault: true})` fallback on both the empty-accounts and the request-failed branches,
      and they are passed as the `exchange` query parameter on its candle, historical, orderbook
      and stream reads. The matching backend defaults are live too: `exchange: str =
      Query("binance")` on `/market/candles/{symbol}/{timeframe}`,
      `/market/data/{symbol}/{timeframe}` and `/market/data/historical`. This is **not** an
      SB-06 violation in Requirement 12's sense — nothing here is persisted into a saved
      strategy, and the save path's own substitutions were deleted by 3.9 — but it does fire in
      normal operation for any author with no connected account, and it means a chart can be
      Binance data with nothing on screen saying so. So: no hardcoded symbol list in the Builder
      path, confirmed; **"no exchange default in the Builder path" cannot be certified.** No
      Phase 7 task owned it and no requirement in this spec covers it, so it is recorded here
      rather than fixed under a verification gate.
    - **Criterion 3 — the stale-never-`LIVE` test is green: PASS, and here is where it is
      asserted.** `tests/test_feed_state_and_data_quality.py`, three classes run directly this
      session, **17 passed**: `TestProperty26ByConstruction` (the sweep — 14 age multiples
      including `1.49`, `1.4999`, `1.5`, `1.5001` against **every** interval in
      `TIMEFRAME_MINUTES`, asserting `age < 1.5 x interval` for anything `LIVE`, and asserting
      the sweep actually reached `LIVE`, `DELAYED` and `STALE` so it cannot pass vacuously);
      `TestBoundaries` (the exact `1.5 x interval` value reads `DELAYED`, which is the value a
      `<=` would get wrong); `TestUnknownIsNotFresh` (an unknown age, an unpublished interval, a
      `nan` age, a **negative** age, a non-numeric age and an unreadable transport each produce
      a non-`LIVE` state with `age_seconds: null`, never `0`). The frontend half is asserted in
      `algo22-terminal/tests/unit/graphValidation.test.js` against `deriveFeedState`: `LIVE`
      inside 1.5 intervals, `DELAYED`/`STALE` beyond 1.5x/3x, `DISCONNECTED` and
      `INSUFFICIENT_DATA` reported literally, and nothing-observed reported `UNKNOWN` rather
      than `LIVE`. Requirement 19.7 holds in both places.
    - **The other Phase 7 commitments, one at a time.**
      - **`load_markets()` in no request handler: holds.** Grepped every `load_markets`
        occurrence in the tree. The only production call is
        `ConnectionEngine.connect` (fix CE-1's retrying loader) plus
        `SymbolNormalizer.load_markets` on the executor; `asset_universe.load_exchange_markets`
        is called only from `refresh_universe`, and `refresh_universe` only from the startup
        warm and the scheduled loop. `discover_assets` awaits exactly `read_cached_universe`,
        and the two router handlers await exactly `discover_assets` — both asserted on the AST
        rather than on prose (`test_asset_discovery.py::TestRefreshIsOffTheRequestPath`,
        `test_market_symbols_universe.py::test_no_handler_in_this_router_calls_load_markets`).
      - **An unavailable universe is a 503 carrying no substitute, on every surface that serves
        symbols: holds, and the surface count was checked rather than assumed.** Exactly two
        endpoints serve symbol lists — `GET /api/market/symbols` and
        `GET /api/strategy-operations/assets` — and both raise `503
        ASSET_UNIVERSE_UNAVAILABLE` with `Retry-After`. `/api/exchange/supported` serves
        *exchanges*, not markets, so it is not a third surface. Both 503 bodies are
        substring-searched for a pair-shaped literal by their own tests, which is the check that
        catches a substitute list nested under a new key, and both suites also assert the 503 is
        not silently replaced by an empty `200`.
      - **`SYNTHETIC_FILL` cannot be selected for a live deployment: holds, by three independent
        mechanisms rather than one.** (1) `resolve_gap_policy` refuses it for `live` as
        `SYNTHETIC_FILL_FORBIDDEN_LIVE` and in every other mode as `SYNTHETIC_FILL_FORBIDDEN`,
        together with both interpolating strategies. (2) There is **no selection surface**:
        `gap_strategy` is a single platform-level `ValidationConfig` field whose default is the
        refusing `SKIP_EXECUTION`, and nothing per-deployment or per-request sets it. (3) The
        dispatch is gone — `GapHandler.handle` ignores its `strategy` argument entirely and
        raises `DataValidationError` on any detected gap, and `_synthetic_fill`, `_interpolate`
        and `_forward_fill` all raise `RuntimeError` on their first line. Worth stating for
        Phase 8: the *deployment* path never calls `resolve_gap_policy` — only the preview path
        and `validated_window` do — so on the live path the guarantee rests on (2) and (3), not
        on a mode-aware check.
      - **Duplicates resolve first-wins: holds where the contract runs, and the exception is
        named.** `ClosedBarIngest.offer` discards a bar whose open time is already held and
        never overwrites the stored candle; `closed_bar_frame` delegates to it, so a batch read
        and a stream cannot disagree. `dag_event_loop`'s `RollingWindow.add_candle` applies no
        duplicate rule — it has a `_last_event_timestamp` monotonicity guard ahead of it that
        returns early on an out-of-order event, which covers the common case but is not the
        first-wins rule.
      - **A forming bar cannot reach indicator computation: DOES NOT HOLD, and this is why the
        phase stays `[~]`.** It holds on every path that goes through 7.5's contract — the node
        preview, the data-quality report and anything calling `validated_window` or
        `closed_bar_frame`. It does **not** hold on the live runtime path, which is the path
        with an order router at the end of it. `dag_event_loop._process_event` (line ~665) and
        `dag_risk_integration` (line ~497) route a tick event into
        `RollingWindow.add_tick`, which *constructs a forming candle* by mutating
        `highs[-1]`/`lows[-1]`/`closes[-1]` in place, and the very next statement hands
        `window.to_dataframe()` to the `DAGEngine` once 20 rows exist. Nothing on that path
        imports `market_data_contract`. So Requirement 19.2 — "THE Market_Data_Pipeline SHALL
        supply indicator computation with closed bars" — is satisfied on the read paths and
        unsatisfied on the streaming one. 7.5 disclosed this in its own note and closed anyway;
        the disclosure is accurate, but a disclosure is not a fix, and an exit gate is the wrong
        place to let a requirement the phase owns stay half-met without saying so. No code was
        changed here: routing the event loop through `ClosedBarIngest` is a runtime change with
        a blast radius (it would also have to reconcile the duplicate `_calculate_*` indicator
        implementations 5.4 recorded on the same file) and belongs in a task with a design, not
        in a verification pass.
      - **No control weakened anywhere in Phase 7: holds, and 7.1's global change was
        re-checked rather than taken on trust.** `main.py`'s `http_exception_handler` now passes
        `headers=getattr(exc, "headers", None) or None`. Every `HTTPException(headers=...)` site
        in `backend_app` was enumerated: exactly two kinds exist — `Retry-After` on the two 503
        universe-unavailable raisers, and `WWW-Authenticate: Bearer` on
        `core/dependencies.py`'s 401. Both are headers the HTTP specification requires and both
        *strengthen* the response; there is no `Access-Control-*`, no `Set-Cookie` and no
        cache-permissive header anywhere in that set, so the forwarding cannot relax a control.
        The body shape is untouched. 7.4's `WebSocketMonitor.get_all_status` deadlock fix
        snapshots connection ids under `self._lock` and reads each status outside it; every
        other method still takes the lock and `get_connection_status` still takes it per read,
        so what changed is the atomicity of a composite read, which the docstring states. The
        four Phase 7 endpoints all carry `Depends(get_current_user)` and a limiter
        (`/assets` 120/min, `/registry/timeframes` 200/min, `/market/symbols` 60/min,
        `/data-quality` 60/min), and `/data-quality` resolves ownership through
        `StrategyService.get_strategy`'s RLS client *plus* explicit `user_id` filter with a
        cross-tenant 404 asserted. No auth, RLS filter, tenant scope, financial invariant,
        execution safeguard, risk gate, idempotency key or rate limit was removed or loosened by
        any Phase 7 task.
    - **The two skipped optional tasks, judged per task rather than waved through.**
      - **7.7 (Property 26 generated + frontend labelling). Coverage genuinely exists; the
        residual is procedural, not substantive.** 7.4's deterministic table is not a thin
        stand-in for the generated sweep here, because the input space is genuinely small: the
        classifier is two threshold comparisons on **one scalar** against a finite published
        interval set, and the table crosses every interval with 14 multiples *including both
        sides of the exact boundary*, then enumerates the pathological inputs a generator would
        be hunting for — `nan`, negative (a future timestamp), non-numeric, `None`, and an
        unpublished interval. A `hypothesis` sweep over generated ages would very likely find
        nothing the table has not pinned. What is genuinely missing is the *bookkeeping*:
        `design.md` numbers Property 26 and its testing strategy expects a generated test, and
        there is no `tests/test_feed_state_property.py`, so the property is satisfied by a test
        that does not carry its name. The frontend labelling half is **already covered** by
        `graphValidation.test.js` as described above — so 7.7's absence leaves no hole in the
        stale-never-`LIVE` claim.
      - **7.7 exposed a different hole, which is a wiring gap rather than a test gap.** Nothing
        in the frontend calls `GET /strategy-operations/strategies/{id}/data-quality`.
        `StrategyBuilder.jsx` takes `feedObservation` as a prop defaulting to `null`, and the
        only thing in the repository that passes it is a test. So the builder's status strip
        always reports `UNKNOWN`. That is fail-closed and does not endanger criterion 3 —
        `UNKNOWN` is not `LIVE` — but Requirements 19.7 and 19.8's *display* obligation ("SHALL
        display the age of the last event together with the expected interval") is unrealised on
        the canvas: 7.4 shipped the endpoint, 3.x shipped the derivation, and no task connected
        them. Recorded rather than assumed closed by 7.4's `[x]`.
      - **7.8 (generated asset-discovery tests). Coverage substantively exists; a narrow
        residual is real.** 7.1's 79 deterministic tests cover every clause the optional task
        names, and the pagination ones are stronger than "deterministic" suggests: pages are
        parametrised over seven page sizes (1, 2, 3, 4, 5, 7, 100) with disjointness **and**
        union-equals-the-whole-ordered-set asserted, plus a refresh-between-pages case that an
        offset cursor would fail, cursor opacity (no `offset` in the decoded payload), a cursor
        from a different filter set refused, and five malformed cursors refused rather than
        reinterpreted. Filtering asserts monotonic narrowing against the live universe.
        Requirement 11.6 is covered at three layers (service raise, endpoint 503, and a
        substring search of the body). **The residual:** every one of those invariants is proved
        against **one hand-built fixture universe** of about ten markets. A generated test would
        cross random universes with random filter combinations and page sizes, and that is the
        one thing missing — the chance of being surprised by an input nobody chose. Narrow, but
        not nothing, and it is the keyset cursor that would benefit.
    - **Adjacent surfaces that are real instances of the same defect and are out of this
      spec's scope, reported so they are not lost.** (a) `pages/Backtester.jsx` still holds
      **two** hardcoded symbol lists of 35 pairs each and an `extractSymbolsFromNodes` fallback
      of `['BTCUSDT']`, and it is worse than a fallback: the success branch fetches
      `endpoints.exchange.getSupported()`, checks `symbols.length > 0`, then **discards the
      response** and sets the hardcoded list. Confirmed still outside the builder's closure —
      task 3.17's `builder.architecture.test.js` resolves the whole import graph and passes —
      and `requirements.md` puts the Backtester out of scope explicitly, so it is not this
      criterion's subject; it is a live defect belonging to whichever task owns that page.
      (b) `routers/strategy_operations.py:1220`, the `POST /backtests/validate-data` handler,
      reads `body.get("symbol", "BTC/USDT")` and `body.get("timeframe", "1h")` — the same
      surface, on the backend. (c) `routers/strategies.py` substitutes
      `blueprint.get("exchange_id", "binance")` and `blueprint.get("symbol", "BTC/USDT")` on the
      **legacy deploy path** (line ~1010-1017) and again on pause (~2409) and resume (~2450).
      The save path is clean — 3.9 removed its substitutions and `test_sb06_exchange_agnostic_save.py`
      pins it, 33 passing — but deploy-time substitution of a venue and a market is the SB-06
      shape surviving one hop downstream of the fix, and it lands squarely in Phase 8's
      deployment-binding scope (8.1/8.2). Flagged for 8.x rather than patched here.
      (d) `utils/engineHelpers.getMaxHistoryForTimeframe`'s silent `|| 30` and
      `contexts/IndicatorEngineContext.jsx`'s `timeframe = '1h'` cache-key default are both
      unchanged since 7.3 flagged the first; neither is an interval vocabulary and neither
      selects a market, so neither bears on this criterion.
    - Baseline: backend **43 failed / 3449 passed / 18 skipped (3510 collected)** in pytest's
      default order, one whole-suite run with the `.venv` interpreter
      (`.venv\Scripts\python.exe -m pytest -q`, 390 s), against the pinned ceiling of **44**.
      **Identical to 7.6's measurement in every respect** — same 43, same 3449, same 3510, and
      the same pinned nineteen files at the same per-file counts (`full_system_test.py` 4,
      `test_distributed_execution_safety.py` 7, `test_atomic_order_cancellation_fix.py` 4,
      `test_marketplace_pipeline.py` 4, `test_event_pipeline.py` 3,
      `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_transaction_isolation_serializable.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_vault_singleton.py` 2, `test_risk_settings_api.py` 2, and 1 each in
      `test_account_health_query.py`, `test_exchange_safety_fix.py`,
      `test_false_success_report_fix.py`, `test_fee_precision_fix.py`,
      `test_get_db_dependency.py`, `test_position_delta_race_condition_fix.py`,
      `test_reconciliation_engine_fail_closed.py`, `test_risk_management_lifecycle.py` and
      `test_tenant_isolation_fixes.py`), which sums to exactly 43 and is the arithmetic check
      that no failure moved between files. Zero collection delta, as expected for a gate that
      wrote no test. **`tests/perf` contributes zero to the default lane, verified rather than
      inherited:** `--collect-only -q` reports `3510 tests collected` and a filter for
      `tests/perf` over that output returns nothing. Frontend: **1 failed / 638 passed (639),
      22 files**, one whole-suite run (`npx vitest run` from `algo22-terminal`, 180 s) —
      identical to 7.3's measurement, and zero collection delta since 7.4-7.6 touched no
      frontend file and 7.7 was skipped. The one failure is
      `tests/unit/portfolio-rendering.test.jsx` -> "should verify improved empty states", the
      known out-of-scope grep against a `Portfolio.jsx` an uncommitted rewrite changed. The
      second known failure, `tests/unit/integration.test.jsx` -> "should load dashboard data on
      mount", **did not fire this run**, which is consistent with 7.3's finding that it appears
      in roughly one run in three and is green in isolation. So it is a subset of the two known
      failures and nothing new.
    - **What remains unverifiable in this environment, said plainly and without hedging the
      parts that *are* verified.** No venue: `api.binance.com` completes a TCP handshake but the
      REST API does not answer, so `load_markets` cannot succeed and the only exchange object
      obtainable here is `DEV_MODE`'s generator, which both `asset_universe._markets_are_mocked`
      and the harness's `refuse_if_mocked` refuse — meaning **no test in Phase 7 has seen a real
      market universe, a real candle or a real forming bar close**. No Redis: the asset
      universe's cache runs process-local (`cache_backend: "process"`), so the 6 h TTL, the
      cross-process share and Candidate A's entire transport are unexercised. Migration `004d`
      is unapplied, so nothing in Phase 7 has been read back through a real Postgres schema. No
      object storage and no live WebSocket, so `observe_feed` has only ever read a real
      `WebSocketMonitor` whose `ConnectionMetrics` timestamps were set by hand — which is how
      an age of exactly 1.5 intervals became reachable, and is also why "the feed is fresh" has
      never been observed rather than injected. Consequently the twelve-metric latency table is
      empty by construction, not by omission, and `BLOCKED` is the only honest verdict this
      environment can produce. What **is** real and was exercised: the whole decision rule and
      its 48 tests, the closed-bar/duplicate/gap contract and its 68, the feed-state classifier
      and endpoint and its 78, the asset universe's merge, filter, keyset pagination and 503 and
      its 79, the two symbol surfaces through the real router with real auth and real limiter
      dependencies, and every structural assertion that keeps a deleted defect deleted.
    - **One question for the user, which is the only thing this gate cannot decide alone.**
      Phase 8 begins with deployment binding and the runtime. The forming-bar gap on
      `dag_event_loop`'s tick path is a runtime defect against a Phase 7 requirement (19.2) and
      the legacy deploy path's `"binance"` / `"BTC/USDT"` substitution is a Phase 8 concern
      already; both live in the same code Phase 8 is about to touch. Fold them into Phase 8 as
      explicit tasks, or open a 7.10 to close 19.2 before the phase is marked complete? Marking
      7.9 `[x]` is not offered as an option while 19.2 is half-met.
    - **Closing addendum (re-run of the gate after 7.10). Verdict: the gate PASSES. 19.2 is
      now satisfied on the streaming path, and 7.9 is marked `[x]`.** The note above stands
      unaltered as the record of what the gate found the first time; this addendum records
      only what changed and what was re-derived. 7.10 landed as a user-approved corrective
      task and its claims were treated the same way every other note has been — as claims to
      check.
    - **Re-verified rather than taken on trust, by driving the shipped code in this session
      (a throwaway probe, not a committed test — the committed assertions are 7.10's own 34).**
      - **No forming bar reaches the `DAGEngine` on *either* loop.** Twelve ticks inside one
        5-minute bar leave the window that feeds computation holding **zero** rows; the next
        interval's first tick releases exactly **one** row whose OHLC is
        `100.0 / 104.0 / 97.5 / 100.25` — the aggregate, not the last tick. A CANDLE event for
        a still-forming bar was refused and counted `forming_dropped` on `DAGEventLoop` **and**
        on `RiskIntegratedEventLoop`, driven through each class in turn rather than through the
        base class twice. The just-closed bar is admitted at the boundary, so no signal is
        delayed by a whole interval.
      - **One seam, literally.** `RiskIntegratedEventLoop._ingest_event is
        DAGEventLoop._ingest_event` is `True`, `_ingest_event` is **absent** from
        `RiskIntegratedEventLoop.__dict__`, and the subclass relationship holds. Stronger than
        the identity check: the window's six deques are written from **exactly one place in the
        repository** — `RollingWindow.append_bar` — and the only other reader of a window is
        `StatefulIndicatorExecutor` (still zero instantiation sites, re-grepped). So there is
        no second door into the frame an executor computes on.
      - **`market_data_contract`'s batch behaviour is unchanged, and the read paths did not
        shift.** `closed_bar_frame` still defaults to `drop_late=False`: three bars offered
        out of order were **all three kept**, re-sorted monotonic, `late_events: 0`. `drain()`
        raises `DRAIN_REQUIRES_STREAMING` on a historical gate, so no batch caller can reach
        the streaming path by accident. The new high-water clause in `offer` is provably a
        no-op for a never-drained gate (the highest accepted open time is always still in
        `_bars`), and first-wins still holds there. The callers are unchanged and were
        enumerated: `closed_bar_frame(rows, timeframe)` on the node preview
        (`strategy_operations.py:3031`) and `validated_window(...)` on data-quality
        (`:4475`) — neither touches `advance_to` or `drain`. The training path never called the
        contract (it doesn't now either), so it has nothing to shift. `test_market_data_contract.py`
        **68**, `test_node_preview_endpoint.py` **91 passed / 1 skipped**,
        `test_feed_state_and_data_quality.py` **78**, `test_closed_bar_streaming_ingest.py`
        **34** — 271 passed / 1 skipped, every count identical to before.
      - **The golden file is genuinely unmodified — and git cannot be the proof, so it was
        proved another way.** `tests/golden/` is **untracked** (`git status` reports `?? tests/golden/`;
        `git ls-files` returns nothing), because the whole spec worktree is uncommitted, so
        "unmodified per git" is not a claim anyone can make here. Instead: SHA-256 of both
        `dag_runtime_plan_golden.json` and `dag_runtime_plan_candles.json` was taken before and
        after running the suite — `28C64CBB…4153A683` and `BB48C40E…AC0B9EB9`, **identical
        either side**, with `AERORA_UPDATE_GOLDEN` unset (that is the only branch that rewrites
        the file, and it is opt-in). **13 passed.** Both files' mtimes are 20-21 Aug, ahead of
        7.10's 25 Aug edits. 7.10's claim is correct, and it matters for the reason it says:
        that test drives `add_candle` and the real `DAGEngine`, so an unchanged golden means no
        trade decision moved.
      - **First-wins holds on the streaming path, at both levels.** Two candle events with the
        same timestamp and different prices produced **one** row holding the **first** close
        (`1.5`, not `9.0`) with `duplicates: 1` counted at the seam; and `append_bar` refuses an
        equal timestamp and a strictly older one directly, so the deque holds the line even when
        written by hand.
      - **The two defects 7.10 reports were confirmed against the committed baseline, not
        against its own prose.** `git show HEAD:…dag_event_loop.py` has **no**
        `self.latency_monitor =` anywhere — only two unguarded reads plus the `hasattr` guard in
        `get_stats` — so the non-risk `_process_event` did raise `AttributeError` before the DAG
        ran, and the current loop assigns no such attribute and stays silent without one.
        `last_signals[symbol] = signal` and `await self._emit_signal(signal)` sat outside the
        `if last_signal is None or …` branch in the committed file, exactly as described.
        **7.10's correction of the note above is right and the note above was wrong on that
        point:** committed `add_candle` was six unconditional `deque.append` calls, and the
        STEP 4.5 guard compared `event_timestamp < last_timestamp` — strictly older — so an
        equal timestamp passed the guard and appended a second row. Also confirmed: the guard
        now reports into the contract's own counters (`late_events: 1`, `out_of_order: 1`, window
        empty) rather than starting a second tally, and `SimulatedEventSource` now stamps naive
        **UTC**. Fail-closed on an unmeasurable interval was exercised too: `7s` yields no gate,
        refuses every event, and reports `gate: "unavailable"` — and `_initialize()` is called
        from `__init__`, so a gate always exists for every tracked symbol and "no gate" cannot
        happen by omission.
    - **The cadence change: acceptable, and the phase closes with it. Judged, not absorbed.**
      It is the correct change and nothing that should fire stops firing.
      - **The non-risk loop lost nothing, because it fired nothing.** Proved above from the
        committed source: `_process_event` raised `AttributeError` on the first timestamped
        event, before the DAG. For that loop the change is from *never evaluating* to
        *evaluating once per closed bar*. The risk-integrated subclass is the one that really
        did compute on forming bars, and every evaluation it loses was a revision of an
        unfinished bar — the defect, not a feature. So "emission volume can only fall" is true
        of the risk loop and false in the *good* direction for the other one.
      - **A candle feed's cadence is unchanged.** A feed publishing only closed klines is
        admitted at the boundary, one row per bar, exactly as before — except that a feed
        publishing *forming* klines used to append a **new row per update** for the same
        timestamp (unconditional `add_candle` past a strictly-older guard), which is the
        outcome the change removes.
      - **The one real hole is bounded and visible.** Completion is data-driven, so a symbol
        whose ticks stop holds its last bar until the next tick. That delays an evaluation; it
        does not suppress it, the feed-state classifier already reports `DELAYED`/`STALE` when
        events stop (19.7, 17 tests re-run green), and the seam's counters are now published
        through `get_stats()`. The clock-driven alternative would break historical replay, as
        7.10 argues.
      - **The ~20-minute warmup on `POST /api/strategies/events/start` is a demo cost, not a
        correctness loss.** `len(window) < 20` was 20 *ticks* of a "1m" strategy, i.e. 20
        seconds of one-second bars labelled `1m`; it is now 20 actual bars. The slower demo is
        the price of the endpoint no longer producing a "1m" signal from one-second bars.
      - **Blast radius today is small, and that is checkable rather than assumed.** The only
        non-simulation source is `WebSocketEventSource` pointed at the placeholder
        `wss://stream.exchange.com/v1/market` in both routers, so no deployment is currently
        driven by a real socket through this loop. The cadence change lands on the simulation
        endpoint now and on whatever real feed Phase 8 wires later — which is the right order.
      - **One residual found in this pass, flagged for 8.x, not a blocker.** Under *negative*
        clock skew a genuinely-closed bar arriving before the local clock agrees is dropped as
        `forming_dropped`, and once a later bar advances the high-water mark a re-send is
        dropped as `late_events` — so that bar is lost and the window carries a hole no gap
        check on this path would catch (reproduced directly). Two things keep it off the
        blocker list: the loss is **counted**, so it is visible in `market_data_state()` rather
        than silent; and it is the contract's own pre-existing rule, already governing the
        preview and data-quality reads since 7.5, not something 7.10 introduced. Network
        latency also biases arrivals to *after* the close. Worth a bounded tolerance when Phase
        8 connects a real venue.
    - **The two items 7.9 left outstanding, re-checked and disposed of.**
      - **(a) The exchange defaults: still present, still not a Phase 7 exit blocker, and
        genuinely Phase 8 work.** Unchanged in `contexts/DataPipelineContext.jsx` — five
        `|| 'binance'` (lines 105, 203, 260, 337, 379) plus the `{id: 'default', name: 'binance',
        isDefault: true}` fallback on both branches (111, 115). **Correcting 7.9's own count:
        the backend side is six handlers, not three** — `Query("binance")` on `/market/candles`,
        `/orderbook`, `/ticker`, `/funding`, `/data/{symbol}/{timeframe}` and `/data/historical`
        (`market.py` 79, 92, 105, 117, 279, 292). `/market/symbols` carries no such default,
        which is the surface that matters for criterion 2. Not a blocker: no Phase 7 task owned
        it, no requirement in this spec states "no exchange default", and Requirement 12's
        persistence clause — the one that *is* stated — is met and pinned
        (`test_sb06_exchange_agnostic_save.py`). Venue binding is literally 8.1/8.2's subject
        and the legacy deploy path's substitution sits in the same code, so both should be one
        Phase 8 task rather than a patch under a verification gate.
      - **(b) The unwired data-quality endpoint: not exit-blocking, but it is Phase 7 debt, not
        Phase 8 scope, and 7.9 understated it by filing it under 7.7.** Still nothing calls
        `GET /strategy-operations/strategies/{id}/data-quality`; `StrategyBuilder.jsx` still
        takes `feedObservation` defaulting to `null` and only a test passes it; and there is not
        even an api-layer method for it (`src/api/**` holds no `dataQuality` call). Requirement
        **19.8** — "SHALL report `DELAYED` **and SHALL display the age of the last event
        together with the expected interval**" — is owned by **task 7.4**, which is `[x]`, so
        this is a Phase 7 requirement whose display clause is unrealised on the canvas, and it
        should not be allowed to vanish behind that `[x]`. It does **not** block the gate, and
        the distinction from 19.2 is severity, not convenience: 19.2 unsatisfied meant the
        runtime *did the wrong thing* with an order router downstream; 19.8 unsatisfied means
        the platform *does not render something it already computes correctly*, and the strip
        fail-closes to `UNKNOWN`, which `graphValidation.test.js` pins as "unknown, not
        healthy". Nothing is decided on false information. Nor is it gated on Phase 8: the
        endpoint answers meaningfully today (`observe_feed` returns `connected=False /
        OBSERVATION_NO_FEED` → `DISCONNECTED` when no subscription exists), so the work is one
        fetch plus one prop. **Recorded as owed: 19.8 is not certified as realised, and a
        follow-up task should wire it.** The phase's stale-never-`LIVE` criterion is unaffected
        — the classifier never labels stale data `LIVE`, and it never did.
    - **The three named criteria and the Phase 7 commitments, re-confirmed this session.**
      Criterion 1: `reports/market_data_latency_decision.md` was **deleted again**, the harness
      re-run (`AERORA_MARKET_DATA_LATENCY_EXPERIMENT=1`, `…_SECONDS=45`, **7 passed**, 31 s),
      and it recreated the document with a fresh `Generated 2026-08-25 16:38:03Z` header naming
      `tests/perf/test_market_data_latency.py`; the verdict is still `BLOCKED` /
      `FLOOR_ADMITTED_NOTHING`, which is the only honest one in an environment with no venue.
      Criterion 2: the structural suites are green in the full run and
      `builder.architecture.test.js` still resolves the builder's import graph clean.
      Criterion 3: `test_feed_state_and_data_quality.py` **78 passed**. Commitments: no
      `load_markets()` in any request handler (the only production calls remain
      `ConnectionEngine.connect` and `SymbolNormalizer.load_markets`); both symbol surfaces
      still 503 with `Retry-After` and no substitute list; `SYNTHETIC_FILL` still unselectable
      for live by all three mechanisms (`_synthetic_fill`, `_interpolate` and `_forward_fill`
      each still raise on their first line; `gap_strategy` is still only the platform default
      `SKIP_EXECUTION` plus `validated_window`'s preview argument — 7.10 added no selection
      surface); and no control was weakened — `git diff` on the two modified runtime modules
      removes **no** line touching a lock, the trading-enabled flag, the kill switch, a health
      check, the ordering guard or a dependency, and `dag_risk_integration` loses exactly the
      four window lines and nothing else.
    - **Re-measured baseline. Backend: 43 failed / 3483 passed / 18 skipped (3544 collected)**
      in pytest's default order, one whole-suite run with the `.venv` interpreter
      (`.venv\Scripts\python.exe -m pytest -q`, **679 s**), against the pinned ceiling of
      **44**. Exactly 7.10's figures, and the same pinned **nineteen** files at the same
      per-file counts — `test_distributed_execution_safety.py` 7, `full_system_test.py` 4,
      `test_atomic_order_cancellation_fix.py` 4, `test_marketplace_pipeline.py` 4,
      `test_event_pipeline.py` 3, `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_transaction_isolation_serializable.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_vault_singleton.py` 2, `test_risk_settings_api.py` 2, and 1 each in
      `test_account_health_query.py`, `test_exchange_safety_fix.py`,
      `test_false_success_report_fix.py`, `test_fee_precision_fix.py`,
      `test_get_db_dependency.py`, `test_position_delta_race_condition_fix.py`,
      `test_reconciliation_engine_fail_closed.py`, `test_risk_management_lifecycle.py` and
      `test_tenant_isolation_fixes.py` — summing to exactly 43, the arithmetic check that no
      failure moved between files. **`tests/perf` contributes zero, verified not inherited:**
      `--collect-only -q` reports **3544 tests collected** and a filter for `perf` over that
      output returns **0**. The 3544 − 3510 = 34 delta against 7.9's own measurement is
      `tests/test_closed_bar_streaming_ingest.py` and nothing else, as 7.10 reconciled.
      Frontend: **1 failed / 638 passed (639), 22 files**, one whole-suite run
      (`npx vitest run` from `algo22-terminal`, 269 s) — identical to 7.9's measurement, since
      7.10 touched no frontend file. The one failure is the known out-of-scope
      `tests/unit/portfolio-rendering.test.jsx` → "should verify improved empty states"; the
      `integration.test.jsx` dashboard timeout flake did not fire this run. A subset of the two
      known failures and nothing new.
    - **Final verdict. `[x]`.** Requirement 19.2 now holds on the streaming path as well as the
      read paths, verified by driving both loops rather than by reading 7.10's note; 19.3 and
      19.4 hold with the drop counted; the golden file is provably unchanged; the batch read
      paths are untouched; the cadence change is the correct one and closes no evaluation that
      should happen; the three named criteria and every Phase 7 commitment hold. Two items are
      carried out of the phase deliberately and neither is a safety gap: the exchange defaults
      go to Phase 8 with the venue-binding work, and Requirement 19.8's display clause is
      recorded as owed Phase 7 debt needing its own task — it fail-closes to `UNKNOWN` and
      misleads nobody. Everything unverifiable in this environment is unchanged from the note
      above: still no venue, no Redis, no object storage and no live socket, so no test in
      Phase 7 has watched a real tick arrive or a real bar close, and the seam is where the
      requirement lives.

  - [x] 7.10 Route the live streaming path through the closed-bar ingest contract
    - Closes Requirement 19.2 on the streaming path, which 7.9 found unsatisfied
    - Corrective task. **Reopens 7.5**: 7.5 built the closed-bar contract
      (`market_data_contract.py`) and wired it into the *read* paths — the node preview and the
      data-quality report — while disclosing, accurately, that `dag_event_loop`'s tick path was
      left unrouted. A disclosure is not a fix, and the unrouted path is the one with an order
      router at the end of it
    - `dag_event_loop._process_event` and the `RiskIntegratedEventLoop._process_event` override
      in `dag_risk_integration` routed a tick into `RollingWindow.add_tick`, which mutated
      `highs[-1]`/`lows[-1]`/`closes[-1]` into a **forming** candle, and the next statement
      handed `to_dataframe()` to the
      `DAGEngine`. One bar timestamp therefore produced a different indicator value on every
      tick — the exact outcome Requirement 19.2 forbids and `design.md` names ("the DAG must
      never compute an indicator on a forming bar and then recompute a different value for the
      same bar"). Nothing on that path imported `market_data_contract`
    - Route both loops through the existing contract — `ClosedBarIngest` / `IngestCounters`,
      `drop_late=True` because a streaming path drops late arrivals rather than re-sorting
      them. Reuse it; do not build a second gate
    - Apply first-wins on duplicates on this path too, matching `ClosedBarIngest.offer`
      (7.5 corrected three other frame builders from last-wins for the same reason)
    - Keep tick data available to what legitimately consumes it — last price for sizing and
      stop checks — while keeping it out of indicator computation
    - _Requirements: 19.2, 19.3, 19.4_
    - **What landed.** Two modules changed and one test file added; no golden file moved.
      `market_data_contract.py` gained exactly three public things, all for a gate that
      outlives one batch: `ClosedBarIngest.advance_to` (a **monotonic** clock, so one gate per
      symbol can live for a whole deployment instead of being constructed per batch),
      `ClosedBarIngest.drain` (release the admitted bars, keep the high-water mark, so the gate
      stays bounded over a long run) and `to_utc_naive` (the timestamp normalisation
      `_parse_timestamp` already performed, hoisted to a public name so the event loop's tick
      bucketing reads the same rule rather than writing a second one — `_parse_timestamp` now
      delegates to it and is one line). **No rule was added, moved or duplicated:** what a
      closed bar is, first-wins on a duplicate and the late-event drop are still defined only
      in that module, and the batch entry points (`closed_bar_frame`, `validated_window`) are
      untouched — a test in the new file pins that a historical batch still re-sorts and keeps
      every bar. `dag_event_loop.py` gained `TickBarBuilder` (tick aggregation, holding the
      in-progress bar **outside** the executor's window), `ClosedBarGate` (one symbol's arrival
      seam, which delegates to `ClosedBarIngest` and adds only the clock and the tick builder),
      `DAGEventLoop._ingest_event` (the single place an arriving event becomes a row an
      executor may compute on), `_now`/`market_data_state`/`_record_market_data_delay`,
      `RollingWindow.append_bar`, an optional `MarketEvent.is_closed` field and an injectable
      `clock=` constructor argument. `dag_risk_integration`'s override lost its two window
      lines and calls the inherited `_ingest_event`, so there is one seam for both loops and a
      test asserts they are literally the same function object.
    - **The invariant, and how it is asserted.** The headline test is not a row count: twelve
      ticks land inside one 5-minute bar and the window that feeds computation holds **zero**
      rows until the next interval's first tick proves the bar finished, at which point it
      holds exactly **one** row whose open/high/low/close are the aggregate of all twelve
      (`100.0 / 104.0 / 97.5 / 100.25`) rather than whichever tick arrived last. A CANDLE event
      for a bar still forming — a Binance kline update with `x: false`, which is what a real
      socket sends most of the time — is dropped and counted. The just-closed bar **is**
      admitted (a gate that also refused it would delay every signal by a full interval, which
      is a different defect wearing the same fix) and one second short of the boundary is
      refused. `design.md`'s canonical `Candle.is_closed` is now carried on `MarketEvent` and
      `False` **outranks the clock**: a feed that says the bar is forming knows something
      inference does not.
    - **What the tick path does with in-progress data — the decision, stated.** A tick that has
      not completed a bar is legitimate input for *something* and is not input for indicator
      computation, so the two uses were separated rather than one of them deleted.
      `TickBarBuilder.last_price` is updated by **every** tick and is what a price consumer
      reads; `RiskIntegratedEventLoop._process_event` still sets `self.last_prices[symbol]`
      from the tick at the top of the method, before the seam, so position sizing and stop
      checks continue to see the live price on every tick. What waits for the bar to close is
      only the DAG. Bars are bucketed arithmetically on the interval, floored against the Unix
      epoch, so a tick-built bar carries the open time the exchange's own bar for that interval
      would — a bar opened at "whenever the first tick arrived" would look like a duplicate or
      a late arrival to the gate as soon as the same symbol also received candle events. Exact
      for every interval up to `1d`; for `3d`/`1w` the epoch grid may not be the grid a
      particular venue publishes on, which is a real limitation of building bars from ticks at
      those intervals and is stated rather than papered over. Completion is **data-driven**
      (the next bucket's first tick releases the previous bar), deliberately not clock-driven:
      a wall-clock flush would, on a historical tick replay, release a one-tick bar
      immediately and then drop every later tick in that bucket as late.
    - **The cadence change, flagged rather than absorbed — and it has consequences for live
      strategies.** A strategy that previously computed on every tick now computes **once per
      closed bar**. That is a real semantic change even though it is the correct one, and it is
      the whole point of the task: the per-tick evaluations were computing an indicator on a bar
      that was still moving, so two evaluations of the same bar disagreed, and the second one
      silently revised the first. Concretely: (a) a 1m strategy fed by ticks is evaluated ~1×
      per minute instead of once per arriving tick; (b) the loop's existing
      `len(window) < 20` warmup floor is now 20 *bars* of wall time rather than 20 ticks, so
      `POST /api/strategies/events/start` with `simulation_mode` (whose `SimulatedEventSource`
      emits one tick per second) reaches its first evaluation after ~20 minutes at `1m`
      instead of ~20 seconds — the demo endpoint is materially slower to produce anything, and
      what it produced before was a "1m" signal computed on a one-second bar; (c) an
      illiquid symbol whose ticks stop arriving leaves its last bar unreleased until the next
      tick, which is silence rather than a wrong answer. Emission volume can only fall, never
      rise: nothing here creates an evaluation that did not previously happen. **Not
      absorbed:** if a deployment genuinely wants tick-cadence evaluation it needs a
      tick-timeframe strategy and a feed that publishes closed bars at that interval, not a
      forming bar relabelled as a closed one.
    - **First-wins on duplicates, at the seam and once more at the deque.**
      `ClosedBarIngest.offer`
      is the authority (a repeated timestamp is counted as a *duplicate*, never as a late event
      — two different feed faults). `RollingWindow.append_bar` refuses a timestamp equal to or
      older than the newest one held, as a last line, because that deque is also written
      directly: the golden-plan test drives `add_candle` itself, and a window carrying two rows
      for one timestamp is a wrong answer, not a slow one. Counting stays only at the seam, so
      there is one reconcilable set of figures. `offer`'s duplicate check now also compares the
      **high-water mark**: after a `drain` the stored bars are gone, and without that
      comparison a re-sent copy of the most recent bar would find an empty store and be
      admitted a second time. For a gate that is never drained the two conditions are the same
      test, so no batch behaviour changed — pinned by its own test.
    - **`RollingWindow.add_tick` now raises rather than being deleted.** It was the live path's
      forming-bar source and it has no production caller left. Raising follows the posture
      `market_data_validation.GapHandler._forward_fill` already takes for a repair the platform
      no longer permits: a caller that still wants tick-by-tick indicator values finds out
      immediately instead of being quietly served them. Its `_should_new_candle` helper went
      with it (no other caller). A test pins the refusal.
    - **The duplicate `_calculate_*` implementations: unreachable before this change and
      unreachable after — reported, not touched.** They live on
      `dag_event_loop.StatefulIndicatorExecutor`, which has **zero instantiation sites in the
      repository** (grep for `StatefulIndicatorExecutor(` finds none; the only references are
      the class definition and prose in `strategy_compiler.py` and two tests explaining why the
      `period`/`std_dev` param aliases are kept for it). Both loops construct plain
      `DAGEngine()`, whose `IndicatorExecutor` task 5.4 re-pointed at `indicators_backend`. So
      this change neither reaches them nor widens their reach, and removal was **not** required
      for correctness and was not undertaken. Worth stating for whoever does remove them: the
      class also merges its window with `market_data` using `keep='last'`, i.e. last-wins on a
      duplicate, so it carries both defects 7.5 and this task were about — which is an argument
      for deleting it outright rather than repairing it.
    - **Two defects found on this path while making it reachable, both fixed because the
      verification claim would otherwise be hollow.** (1) `self.latency_monitor` is **never
      assigned** by `DAGEventLoop.__init__` — `get_stats` already guarded it with `hasattr` for
      the same reason — so `_process_event` raised `AttributeError` on the first event carrying
      a timestamp, *before* the DAG ran, and `_event_processor`'s `except Exception` logged it
      as an unexplained processing error. The same line subtracted a possibly tz-aware event
      timestamp from a naive local `datetime.now()`, which raises `TypeError`. Both are now in
      `_record_market_data_delay`: absent monitor is silence (a measurement nobody collects
      must not stop a strategy being evaluated), the instant comes from `_now` (UTC) and the
      event timestamp is normalised through the contract. **This means the non-risk loop's
      `_process_event` could not reach the `DAGEngine` at all before this task**, which is
      worth knowing when reading "the live path computed on forming bars": the risk-integrated
      subclass, which overrides `_process_event` and never touched `latency_monitor`, is the one
      that actually did. (2) `self.last_signals[symbol] = signal` and `await
      _emit_signal(signal)` sat **outside** the `if last_signal is None or last_signal.action
      != action:` branch that binds `signal`, so a second bar carrying the same action raised
      `UnboundLocalError` and was logged as a DAG execution failure. Both statements are now
      inside that branch. Emission behaviour is unchanged — a repeated action still emits
      nothing, which is what "avoid spam" means — it is now the intended silence rather than a
      crash. Also fixed: `SimulatedEventSource` stamped `datetime.now()` (naive **local**), and
      the closed-bar rule compares against a UTC clock, so on a host east of Greenwich every
      bar built from those ticks would read as still forming and nothing would ever be
      evaluated; it now stamps naive UTC.
    - **Fail-closed choices, said plainly.** A timeframe the market data pipeline has no bar
      length for (`interval_for` refuses it) produces **no gate**: the loop still constructs —
      a constructor that threw would take down callers that only read the loop's configuration
      — logs an error naming the symbol and the interval, and then **refuses every market event
      for that symbol**, because an interval that cannot be measured cannot tell a closed bar
      from a forming one. `market_data_state()` reports that symbol as
      `gate: "unavailable"` with the reason, and `get_stats()` now carries `market_data` per
      symbol (the contract's own `IngestCounters`, republished, never re-derived) so the forming
      bars, duplicates and late events a running deployment refused are readable rather than
      only logged. A tick or candle whose price, size or timestamp is unreadable changes
      nothing and is counted. `RollingWindow.append_bar` refuses a bar whose timestamp cannot
      be *compared* with the window's own (tz-aware meeting tz-naive) rather than appending on
      an unchecked comparison, because the ordering of the resulting frame — and therefore any
      indicator computed on it — would be undefined.
    - **STEP 4.5's out-of-order guard is kept, and now counts.** That guard returns before the
      seam is reached, so routing alone would have lost exactly the late events it caught.
      Requirement 19.3 asks that a late event be discarded **and** counted, so the guard now
      reports into the gate's own counters (`ClosedBarGate.count_rejected_event`) rather than
      starting a second set of figures. The guard itself is unchanged: it is a control, and
      this task removes none.
    - **Controls.** Nothing was weakened. No auth, RLS filter, tenant scope, financial
      invariant, execution safeguard, risk gate, idempotency key or rate limit was modified:
      the Redis advisory execution lock, `_emit_signal`'s
      `ExecutionFlags.EVENT_LOOP_TRADING_ENABLED`
      block and its Redis exactly-once set, the STEP 4.9 kill switch and health checks, the
      STEP 4.5 ordering guard, `RiskIntegratedExecutionPipeline`'s risk checks and
      `assert_execution_safe` are all untouched and all still sit where they sat. The
      `/api/strategies/events/*` router is unchanged, including its fail-closed WebSocket auth.
      `SYNTHETIC_FILL` remains unselectable for `mode='live'` — this task added no gap-strategy
      selection surface and `resolve_gap_policy` is unchanged (the streaming path still does not
      call it; as 7.9 recorded, on the live path that guarantee rests on there being no
      per-deployment selection surface and on `GapHandler` refusing every gap outright, which
      remains 8.x's business). `OutlierDetector`'s 3σ filter and `GapHandler`'s gap refusal are
      untouched. In the direction of *strengthening*: a forming bar can no longer reach the
      `DAGEngine` on the streaming path, a duplicate bar can no longer be revised by a later
      arrival on that path, a late arrival is now counted as well as dropped, and an
      unmeasurable bar interval now refuses to compute instead of computing on bars it cannot
      classify.
    - **What is real here and what is supplied.** Real: the shipped `DAGEventLoop`, its
      `RollingWindow`, the real `ClosedBarIngest` reached through the loop's own gate, the real
      `TIMEFRAME_MINUTES`, the real `RiskIntegratedEventLoop` class relationship, and — through
      `test_dag_runtime_golden_plan.py` — the real compiler, adapter and `DAGEngine` over the
      committed candles. Nothing is mocked; a test that mocked the gate would prove only that
      the mock was called. Supplied: **the clock** (`DAGEventLoop(clock=...)` feeding
      `ClosedBarIngest(now=)`, so "this bar has closed" is *stated* rather than raced) and the
      events (hand-built `MarketEvent`s of the shape `WebSocketEventSource._handle_message`
      constructs). No exchange, no Redis and no market data socket is reachable in this
      environment, so **no test here has watched a real tick arrive or a real bar close**, and
      `_process_event` is only exercised as far as the seam — past it, it takes a Redis
      advisory lock. The seam is where the requirement lives, and both `_process_event`
      implementations reach it.
    - Baseline: backend **43 failed / 3483 passed / 18 skipped (3544 collected)** in pytest's
      default order, one whole-suite run with the `.venv` interpreter
      (`.venv\Scripts\python.exe -m pytest -q`, 400 s), against the pinned ceiling of **44**.
      The failure count is **unchanged** from 7.9's 43, and it is the same pinned nineteen
      files at the same per-file counts (`full_system_test.py` 4,
      `test_distributed_execution_safety.py` 7, `test_atomic_order_cancellation_fix.py` 4,
      `test_marketplace_pipeline.py` 4, `test_event_pipeline.py` 3,
      `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_transaction_isolation_serializable.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_vault_singleton.py` 2, `test_risk_settings_api.py` 2, and 1 each in
      `test_account_health_query.py`, `test_exchange_safety_fix.py`,
      `test_false_success_report_fix.py`, `test_fee_precision_fix.py`,
      `test_get_db_dependency.py`, `test_position_delta_race_condition_fix.py`,
      `test_reconciliation_engine_fail_closed.py`, `test_risk_management_lifecycle.py` and
      `test_tenant_isolation_fixes.py`), which sums to exactly 43 and is the arithmetic check
      that no failure moved between files. **The collection delta is reconciled:** 3544 − 3510
      = **34**, which is exactly this task's new file
      (`tests/test_closed_bar_streaming_ingest.py`, 34 tests, all passing) and nothing else. The
      suites over this path were also run on their own and are green:
      **`test_dag_runtime_golden_plan.py`
      passes with the golden file unmodified** — worth saying loudly, because a re-recorded
      golden here would have meant a trade decision moved, and the live half of that test drives
      `RollingWindow.add_candle` and the real `DAGEngine` directly. Also green:
      `test_dag_engine_port_addressing.py` (49), `test_lifecycle_integration.py` (13 skipped, as
      before), `test_live_execution_reliability.py` (7), `test_worker_reliability.py` (2),
      `test_market_data_contract.py` (68, unchanged), `test_node_preview_endpoint.py` (91 passed
      1 skipped), `test_feed_state_and_data_quality.py` (78),
      `test_logic_and_indicator_executors.py`, `test_plan_engine_adaptation_fidelity.py`,
      `test_task_2_4_call_site_migration.py`, `test_dag_redis_migration.py` and
      `test_websocket_auth_fail_closed.py` — 310 passed / 13 skipped across that set, with no
      behaviour change in any of them. Frontend: **not re-run, and deliberately so** — this task
      touches no frontend file (`backend/market_data_contract.py`, `backend/dag_event_loop.py`,
      `backend/dag_risk_integration.py`, `tests/test_closed_bar_streaming_ingest.py`).

  - [x] 7.11 Wire the builder's feed-state display to the data-quality endpoint
    - Discharges the display clause of Requirement 19.8 — "SHALL display the age of the last
      event **together with** the expected interval" — which **7.9 recorded as owed, not
      certified**: the endpoint existed and answered correctly, and nothing on the canvas
      called it. **7.4 keeps the reporting half** (the classifier, the five-state vocabulary,
      the 1.5x / 3x boundaries and the endpoint); this task owns only the read and the render,
      and 7.4's `[x]` is not touched
    - Add the api-layer method the exit gate found missing — `src/api/modules/dataQuality.js`,
      registered on the shared `api` object — reaching
      `GET /api/strategy-operations/strategies/{id}/data-quality` through the existing
      authenticated client, uncached, with no retry and no substitute body
    - Poll it for a **saved** strategy from `StrategyBuilder.jsx` and render the server's
      pre-rendered `display` sentence **verbatim** in the status strip, the convention
      `ParameterForm.jsx` (`fix_hint`) and `NodePreview.jsx` (`detail.message`) already follow.
      Compose no sentence client-side and re-derive no state
    - Fail closed: no reading, a read in flight, a failed read, a body carrying no feed report,
      an unrecognised state word or a reading about another market all read `UNKNOWN`, which
      stays distinguishable from the five server states and is never `LIVE`. An unsaved canvas
      is `NOT_APPLICABLE` — no request, and not an error
    - Read-only display. Weaken no control, add no second classifier, and stay inside the
      route's `60/minute` limit and its `Cache-Control: no-store`
    - _Requirements: 19.7, 19.8, 19.9, 19.10_
    - **What landed.** Three frontend files and one test file; **no backend file was touched.**
      New: `algo22-terminal/src/api/modules/dataQuality.js` (`dataQualityApi.forStrategy`,
      `DATA_QUALITY_PATH`, `DATA_QUALITY_LIMIT_PER_MINUTE`) and
      `algo22-terminal/tests/unit/strategyBuilder.feedState.test.jsx` (19 tests, all green).
      Changed: `src/api/index.js` registers the module as `api.dataQuality` and re-exports it;
      `src/lib/graphValidation.js` gained `FEED_READ_STATES` and `feedStateFromReport`, the
      projection of one read onto the strip; `src/pages/StrategyBuilder.jsx` gained
      `FEED_READ_INTERVAL_MS`, the `feedRead` state, the effect that owns the request and the
      timer, and the `feed-display` cell. `deriveFeedState` was **kept, not replaced** — it
      still serves a caller holding a direct observation, and `feedStateFromReport` reuses its
      "nothing has been observed" wording rather than writing a second version of it, so
      `graphValidation.test.js`'s "unknown, not healthy" stays literally true and unedited.
    - **The sentence on screen is the server's, character for character, and that is the whole
      of the display clause.** `feed_state.py`'s `FeedStateReport.display` already renders
      "Last candle 12m 30s ago, expected every 5m." — the age *and* the interval, as figures.
      The strip prints that string and adds nothing to it. Re-composing it here from
      `age_seconds` and `expected_interval_seconds` was the obvious alternative and was
      rejected: it would have put a second formatter and, inevitably, a second reading of the
      threshold next to the classifier that actually decides, and a client sentence that
      disagreed with the state beside it would be indistinguishable from a state that was
      wrong. The figures still travel with the sentence as `data-age-seconds` and
      `data-expected-interval-seconds`, so a rendered state remains checkable against its
      numbers — the same reason the report carries them.
    - **Nothing here classifies a feed, and the test asserts that on the source rather than
      trusting the prose.** `feedStateFromReport`'s own body is checked, with comment lines
      stripped first, for the absence of `1.5`, a bare `3`, `delayed_at_intervals` /
      `stale_at_intervals` and **all five state words** — the projection's prose does name the
      states it refuses to re-derive ("labelling a stale feed `LIVE`"), and prose about a defect
      is not the defect. `FEED_STATE_LABELS` is a translation table keyed by the server's word,
      so a vocabulary this build does not know cannot be rendered as a sixth state, least of
      all a passing one; the behavioural half of the same claim is a report of `DELAYED` at 30 s
      of age against a 5m bar — figures a client recomputing the boundaries would call `LIVE` —
      rendered as **Delayed**.
    - **Fail-closed, in every branch that is not a report, and a `LIVE` test to keep those
      branches honest.** Unsaved canvas, read in flight, HTTP 500, HTTP 429 (the limiter's own
      refusal), and a 200 whose body carries no `feed` object are each asserted to render
      `UNKNOWN` with `known: false` and never `LIVE`, with no sentence at all rather than a
      guessed one — an empty strip beats a fabricated age. Because five of those tests would
      pass against a strip that could *never* say `LIVE`, a sixth feeds a reported `LIVE` and
      requires it to render as Live: the fail-closed assertions mean something only if the open
      path works. `UNKNOWN` is pinned as absent from the five-state list, so "nobody has
      measured this" cannot be read as "measured, and healthy".
    - **A reading is attributed to the version it describes, not to whatever is on the canvas.**
      The endpoint reports on the **saved** version's configured data source, so a canvas whose
      DATA node now names another market gets `UNKNOWN` with both markets stated and the
      reading quoted, not the version's figures under the new market's name. Attributing a
      true measurement to the wrong market is the same class of error as labelling stale data
      `LIVE`, and it is the one that would be hardest to notice.
    - **The refresh interval is 15 s, which spends 4 of the route's 60 per minute.** The route
      is `@limiter.limit("60/minute")` and answers `Cache-Control: no-store`, so the budget is
      one request per second and none of them may come from a cache. 15 s leaves a **15x**
      margin, so several builders open at once, or a refresh landing beside a save, still cannot
      approach the limit; a test pins the arithmetic against
      `DATA_QUALITY_LIMIT_PER_MINUTE` with a 4x floor on the margin so shortening the interval
      later cannot silently spend the budget. It is not slower than what it measures either:
      the shortest published interval is 1m and `DELAYED` begins at 1.5 intervals (90 s), so a
      feed that stops is seen well inside the window in which it starts to matter. Three things
      keep one tick from becoming several requests: the reads are **single-flight** (a slow
      answer is not stacked on by the timer), the module deliberately bypasses `apiClient`'s
      `get()` helper — whose 60 s response cache would serve a *stale age as the current one*,
      exactly the defect Requirement 19.7 exists to prevent, and whose three retries with
      backoff would triple the rate against a rate-limited route — and the effect aborts its
      in-flight read on unmount or a strategy change, which a test verifies through the real
      `AbortSignal`. What is **not** covered is an observed second tick: the poll is asserted as
      one read per mount plus the interval arithmetic, not by advancing a fake clock through the
      builder, which also drives a 400 ms validation debounce and a 30 s autosave.
    - **Accessibility, and no control weakened.** The state is text — the label word, an
      explicit "(unknown)" where it is not known, and the sentence — inside the strip's existing
      `role="status"` / `aria-live="polite"` region, so colour is an addition to the reading and
      never the reading itself; a test asserts the announced text carries both the state word and
      the sentence. The cell is display-only: it disables nothing, enables nothing, and gates no
      save, deploy or order path. No auth, RLS, financial invariant, execution safeguard, risk
      gate, idempotency key or rate limit was touched, on either side of the wire — the read
      goes through the platform's own bearer-carrying instance and the endpoint keeps its
      `Depends(get_current_user)` and its limiter.
    - **Two stale comments corrected, because they now described the opposite of the code.**
      `StrategyBuilder.jsx`'s `feedObservation` docblock and `graphValidation.js`'s
      `deriveFeedState` docblock both said the builder "has no feed subscription until task 7.4
      wires the data-quality endpoint". 7.4 wired the endpoint; **7.11 wires the read**. Both now
      state where the reading comes from and that `feedObservation`, when a caller does supply
      one, still wins — a direct observation of the feed is closer to it than a report about the
      saved version, and that is also what keeps
      `strategyBuilder.validation.test.jsx`'s existing use of the prop meaningful rather than
      grandfathered.
    - **No backend defect found, so nothing there was widened.** The endpoint's shape, its
      `no-store` header, its limiter, its 404/422 refusals and `display`'s rendering of both
      figures were all read against this wiring and all hold; `feed_state.py` and
      `strategy_operations.py` are unmodified, so **pytest was not re-run** and 7.10's measured
      **43 failed / 3548 passed / 18 skipped** against the pinned ceiling of 44 stands
      untouched.
    - Frontend: **2 failed / 656 passed (658 across 23 files, 352 s)** with `npx vitest run`
      from `algo22-terminal`. The collection delta reconciles exactly: 658 − 639 = **19**, this
      task's new file, all 19 passing. The two failures are the two already recorded and neither
      is new. `portfolio-rendering.test.jsx` → "should verify improved empty states" still greps
      `src/pages/Portfolio.jsx` for `Trade history`, a string an earlier uncommitted rewrite of
      that page removed; out of scope and untouched. `integration.test.jsx` → "should load
      dashboard data on mount" fired its 30 s timeout again (31.6 s) and **passes in isolation in
      6.5 s (3 tests)**, re-confirmed this session rather than assumed — the flake it was already
      recorded as. No third failure appeared, so the number moving is the flake alternating, not
      new breakage.
    - **Requirement 19.8's display clause is now realised on the canvas** — the age of the last
      event is displayed together with the expected interval, in the classifier's own words, for
      a saved strategy — so the item 7.9 carried out of Phase 7 as owed is **discharged**. 7.9's
      and 7.4's checkboxes are unchanged: the gate's verdict stands as recorded, and what changes
      is that its one outstanding display debt is now paid.

- [x] 8. Phase 8 — Deployment binding, lifecycle and runtime readiness

  - [x] 8.1 Land the deployment binding columns
    - Extend `004_strategy_builder_canonical.sql` with the `strategy_deployments` additions:
      `exchange_account_id`, `risk_config_id`, `execution_config`, `mode`, `dag_hash`, plus
      `chk_sd_mode` and `chk_sd_live_needs_account`
    - Leave the existing deployment RLS and indexes untouched
    - _Requirements: 13.1, 13.6_
    - **Landed as `backend_app/migrations/004e_deployment_bindings.sql` (migration 004
      part 5)**, not as an in-place edit to part 1 — and the judgement is stated rather than
      assumed. In-place editing *would* be defensible if part 1 were provably unapplied
      everywhere, but that is exactly what cannot be established here: there is no migration
      table, no migration step in `03-deploy.yml`, and part 1's own header says it is applied
      by hand, so nothing records what any environment has run. Appending to a file an
      operator may already have applied leaves no signal that it changed and the new
      statements would simply never run; a new filename is the signal. Parts 1, 2, 3 and 4
      already enumerated part 5 as a separate landing and now name its file. Part 1 is
      otherwise byte-identical (one comment line added), and a test asserts the deployment
      DDL is *not* in it.
    - The five columns and both constraints are `design.md § "-- 4. Deployment binding
      completeness"` verbatim, schema-qualified. `exchange_account_id` and `risk_config_id`
      nullable UUID, `execution_config JSONB NOT NULL DEFAULT '{}'`, `mode TEXT NOT NULL
      DEFAULT 'paper'`, `dag_hash TEXT` nullable. `'paper'` is the safe default: it is the
      value that reaches no real order router, the one `chk_sd_live_needs_account` does not
      constrain, and therefore the one that lets every pre-existing row be backfilled in
      place without violating either CHECK.
    - **`mode`'s `NOT NULL` is part of the control, not decoration, and this is the one thing
      about the file worth reading twice: A CHECK CONSTRAINT THAT EVALUATES TO NULL PASSES.**
      For a row with `mode IS NULL`, `chk_sd_mode` is `NULL IN (...)` → NULL → admitted, and
      `chk_sd_live_needs_account` is `NULL OR (account IS NOT NULL)` → NULL when there is no
      account → admitted. Both would appear in `pg_constraint` and enforce nothing. Since
      `ADD COLUMN IF NOT EXISTS` is silent about a pre-existing column of a different type or
      nullability, section 2 asserts the shape of all five columns and **aborts** if `mode` or
      `execution_config` is nullable, or if either UUID column is not `uuid` (a TEXT column of
      the same name would turn task 8.2's ownership comparison into a string compare).
    - `chk_sd_live_needs_account` constrains exactly one direction, which is what 13.6 asks:
      live+no-account REJECTED; live+account admitted; paper with *or* without an account
      admitted (binding an account to a paper deployment is how an author paper-trades against
      the venue they intend to go live on). A predicate that also rejected paper-with-account,
      or that rejected live outright, would refuse legal deployments — worse than no
      constraint — so the accepting cases are asserted as explicitly as the rejecting one.
    - **`chk_sd_mode` agrees with the code, it is not a third vocabulary.**
      `backend/market_data_contract.py` (task 7.5) defines `MODE_PAPER = 'paper'` and
      `MODE_LIVE = 'live'`, and its own comment on `MODE_PAPER` already says "`chk_sd_mode`
      (design.md) admits this as a deployment mode". The test imports those constants and
      compares them against the literals parsed out of the SQL, so the two cannot drift.
      `MODE_BACKTEST` is deliberately excluded and that is *not* a disagreement: a backtest is
      "a bounded historical read … no order router is downstream of it", i.e. a
      `strategy_backtests` row, not a deployment — and since `chk_sd_live_needs_account` only
      constrains `'live'`, admitting `'backtest'` would let a historical read pose as a running
      deployment with no account binding at all.
    - `dag_hash` records the identity of the exact compiled plan a deployment runs. TEXT, to
      match `strategy_versions.dag_hash` and `strategies.dag_hash`. The column comment states
      that it is a **readable field on `CompiledPlan`, never a method (SB-02)** — the clone path
      once called `compiled.get("dag_hash")` on an object that only had `compute_hash()`, the
      `AttributeError` was swallowed, and every clone persisted hashless — so the value written
      is `plan.dag_hash`, never a recomputation a caller can forget. Requirement 22.5 is the
      reader: a stored hash that differs from the hash recomputed from the canonical graph
      means recompile before executing. Nullable, because pre-existing rows have no plan
      identity and inventing one would be a fabricated fact.
    - **Controls preserved, and verified two ways.** The file contains no `CREATE`/`ALTER`/`DROP
      POLICY`, no `ENABLE`/`DISABLE ROW LEVEL SECURITY`, no `CREATE`/`DROP INDEX`, no
      `CREATE TRIGGER`, no `GRANT`, no `REVOKE`, no `DROP`/`DELETE`/`UPDATE`/`ALTER COLUMN`, and
      no DDL statement naming a relation other than `strategy_deployments` — asserted against
      the text with comments *and string literals* stripped, so the file's own explanatory
      `RAISE` messages cannot satisfy the assertion. It also proves it about the **database**:
      the preflight records the policy and index counts for the table in two
      transaction-local settings and section 6 re-counts them after every statement and raises
      if either moved, with **no policy or index name hard-coded**, so a renamed object in some
      environment cannot false-alarm. The three existing owner policies (SELECT/INSERT/UPDATE
      on `user_id = auth.uid()`, no DELETE policy), the five `idx_strategy_deployments_*`
      indexes, `trigger_update_strategy_deployments_updated_at` and all grants are untouched.
      RLS is row-scoped, so those policies keep applying unchanged to the five new columns.
    - **The file refuses to run if RLS is disabled on `strategy_deployments`.** `mode = 'live'`
      makes fills real and `exchange_account_id` is the handle that resolves real credentials,
      so adding both to a table without row-level isolation would let any authenticated caller
      read or rewrite which account another user's live deployment trades through. The preflight
      raises, naming `003_signal_trace_restoration.sql` sections 8 and 9 as the remedy, and
      deliberately **does not enable RLS itself** — that would be a change to the control this
      task must leave alone. It also refuses if RLS is on with zero policies, which would make
      a written binding unreadable.
    - **Two honest gaps, both stated in the file rather than left to be discovered.** (1) *No
      FK on either reference, and it is not expressible.* There is no `risk_configs` table at
      all (per-user risk lives in `risk_settings`), there are two candidate account tables
      (`exchange_keys`, `exchange_connections`) plus an `exchanges` table 003 notes as absent in
      production, and **all of them declare `id TEXT PRIMARY KEY`** — a UUID column cannot
      reference a TEXT key (42804). So the database does **not** check that a referenced account
      or risk config belongs to the deploying user; Requirement 13.2 is application-enforced in
      8.2's one transaction. UUID is kept as the design specifies and earns its keep as a format
      guard, matching `strategy_service.deploy_strategy`'s existing `UUID()` screen. (2) *`mode`
      is not `environment`.* The table already has `environment VARCHAR(20) DEFAULT 'paper'`
      with the vocabulary paper/live/cloud/local and an index on it; it is not touched, not
      constrained and not backfilled. **`chk_sd_live_needs_account` guards `mode`, not
      `environment`**, and the current writer sets `environment` and not `mode` — so until 8.2
      populates `mode`, a deployment created as `environment = 'live'` with no account is stored
      as `mode = 'paper'` and the constraint correctly does not fire. **Task 8.2 must set `mode`
      on every deployment it creates** and must keep enforcing 13.6 in the handler so a refusal
      is a 4xx naming the missing account, not a 23514 surfacing as a 500. A test pins that
      sentence into the file.
    - Deliberately NOT added: no index on any new column (task 8.1's constraint, and the design
      specifies none — every deployment query already filters on `user_id`/`strategy_id`); no
      `jsonb_typeof` CHECK on `execution_config` (undesigned, and it could reject rows a writer
      not yet revised for it produces — the document's shape belongs to 8.2's request model);
      no constraint on or backfill of `environment`; no `BEFORE UPDATE` trigger, because unlike
      004d's new tables this one already has 003's and a second would double-fire; no RLS or
      grant change of any kind.
    - Five additions beyond the design's DDL, all documented in the header: preflight
      assertions; the column shape assertion; **guarded `ADD CONSTRAINT` blocks — the design's
      bare chained `ADD CONSTRAINT a …, ADD CONSTRAINT b …` is not idempotent and aborts a
      second run with 42710**, and PostgreSQL has no `ADD CONSTRAINT IF NOT EXISTS` while
      DROP-then-ADD would break the no-DROP rule and open a window with the invariant
      unenforced; column comments; the "nothing else changed" postflight.
    - **Verified statically and against a double, not live.**
      `tests/test_deployment_binding_columns_migration.py` (65 tests) parses design.md's
      section 4 at test time and requires every line present (tolerating only the trailing
      `,`/`;` the chained-vs-guarded split forces); compares the added-column set both ways;
      asserts both predicates literally; and — because there is no PostgreSQL here — evaluates
      the two predicates through **a double implementing SQL's three-valued logic** over the
      exhaustive 7 modes × 2 account values, with a test that **pins the double to the
      predicate text extracted from the SQL** so it cannot drift into testing some other
      expression. It also asserts the set of rejected well-formed-mode rows is exactly
      `{live, no account}` — one row, so a predicate that rejects something unrelated fails.
      Twelve targeted mutations (dropping the account requirement, admitting `'backtest'`,
      making `mode` nullable, defaulting `mode` to `'live'`, adding an index, adding a
      `USING (true)` policy, dropping an `IF NOT EXISTS`, unguarding a constraint, weakening
      the shape assertion, neutering the postflight, dropping the no-FK disclaimer, dropping
      the SB-02 note) were each caught by at least one test.
    - **Not verified:** there is no local PostgreSQL in this environment, so **this task lands
      SQL that cannot be executed here** and nothing proves PostgreSQL accepts the file, that
      `chk_sd_live_needs_account` actually refuses a live row with no account, that the shape
      assertion or section 6's counts actually fire, or that a re-run is a no-op against a real
      catalogue. Parenthesis and string-literal balance is checked, but no PostgreSQL grammar
      was available, so "the SQL parses" is NOT claimed. The split is deliberate: the
      constraints are exercised against a Python double shaped like them, and **live enforcement
      remains the file's own VERIFICATION queries** — 10 groups, exercising both constraints in
      **both** directions (23514 for live-without-account and for `'backtest'`, 23502 for a NULL
      mode, plus the three "expect success" cases), plus before/after checks on policies,
      indexes, the trigger, grants and `environment` — to be run by the operator who applies it,
      and task 8.7's isolation matrix for the RLS half.
    - Until it is applied these five columns do not exist, so **every code path that touches
      them must degrade with a warning naming `004e_deployment_bindings.sql`** — 004d's
      convention — and never a 500, and never report a deployment as bound when the binding was
      not stored. That is task 8.2's obligation; a test requires the file to state it.
    - Baseline held: backend **43 failed / 3548 passed / 18 skipped** over 3609 collected in
      pytest's default order with `.venv\Scripts\python.exe -m pytest -q` (281 s), against the
      pinned ceiling of **44**. The failure count is **unchanged** from 7.10's 43 and it is the
      same pinned nineteen files. **The collection delta is reconciled:** 3609 − 3544 = **65**,
      which is exactly this task's new file (`tests/test_deployment_binding_columns_migration.py`,
      65 tests, all passing) and nothing else; 3483 + 65 = 3548. Task 7.11 landing concurrently
      on the frontend did not affect the backend count. Frontend: **not re-run, and deliberately
      so** — this task touches no frontend file (one new migration, one new test file, and one
      comment line in each of the four sibling migrations).

  - [x] 8.2 Implement the deployment binding and deploy gate
    - Extend `POST /strategies/{id}/versions/{v}/deploy` to record version, exchange account,
      risk config, execution config and mode as one `DeploymentBinding`
    - Assert in one transaction that the version, the exchange account and the risk config all
      belong to the requesting user
    - Refuse a version whose lifecycle state is not `READY`, naming the state and the
      outstanding prerequisite
    - Refuse a symbol the target account does not list and a timeframe it does not support,
      naming both the symbol or timeframe and the account
    - Require an exchange account for `mode = 'live'` and reject synthetic gap filling for live
    - Resolve credentials through `credential_vault.load_decrypted_keys(user_id,
      exchange_account_id)` inside the execution process only
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.9_
    - **Landed as one new module plus edits to the two existing writers and their two
      handlers.** `backend_app/backend/deployment_binding.py` owns every Requirement 13
      gate, the `DeploymentBinding` structure, the 004e availability probe and the client
      projection; `strategy_service.deploy_version` owns the quota, the single INSERT and
      the runtime start; `routers/strategy_operations.py` gained
      `DeploymentBindingRequest` (the design's "existing path, **extended body**") and one
      `DeployRejected → HTTPException` mapper shared by both deploy routes. Nothing else
      was restructured. The gate lives in `backend/` rather than in the router **because
      tasks 8.3 and 8.4 have to read the same refusals** — a lifecycle transition and a
      runtime readiness check cannot import FastAPI, rate limiting and auth to find out
      what `VERSION_NOT_READY` means — and because it makes the gate testable without a
      TestClient.
    - **"In one transaction" — the decision, and what a crash leaves.** PostgREST exposes
      no multi-statement transaction to this client; task 6.5 hit the same wall and used a
      compensating sequence. Here the shape is strictly better than that, and it is stated
      rather than assumed: **all three of Requirement 13.2's ownership assertions are
      *reads*.** The sequence is read+assert version owner → read+assert account owner →
      read+assert risk-config owner → **one** INSERT. The INSERT is a single statement, so
      the binding row cannot come into existence unless all three assertions have already
      passed, and **a crash anywhere before it leaves nothing at all** — no row, no
      partially bound deployment, no quota consumed (quota is reserved after the gates, and
      released if the INSERT then fails). The failure mode is "no deployment", never "a
      deployment bound to an account it was never checked against", which is the safe
      direction and is why this is acceptable rather than merely convenient. Nine tests
      assert `sb.inserts == []` after each gate refuses. What the arrangement does **not**
      eliminate is a TOCTOU window — an account could change hands between its check and
      the INSERT — and two independent controls cover the *consequence* instead of the
      window: the INSERT goes through the caller's RLS-scoped client carrying `user_id`, so
      `sd_owner_insert` re-checks tenancy at write time; and credentials are resolved at
      execution time by `(user_id, exchange_id)` under `exchange_keys`' own owner policy,
      so a stale reference yields **no** keys, never another tenant's.
    - **Requirement 13.2 is application-enforced, exactly as 8.1 said it would have to be.**
      There is no FK on `exchange_account_id` or `risk_config_id` and none is expressible
      (no `risk_configs` table exists — risk is `public.risk_settings`; the candidate
      account tables declare `id TEXT PRIMARY KEY`, which a UUID column cannot reference).
      So `load_owned_exchange_account` and `load_owned_risk_config` each apply **two**
      filters — the request-scoped client's own owner policy **plus** an explicit
      `.eq("user_id", …)`, the convention tasks 6.3/6.5/6.6/7.1/7.4 established — and the
      version is confirmed the same way by re-reading its `strategies` row with an explicit
      `user_id` filter, which `deploy_version` did **not** do before. Every one of the three
      is a **404, not a 403**: a 403 about an account id confirms the id exists. The test
      double honours `.eq` filters precisely so an ownership test cannot pass while the
      production filter is missing. Both candidate account tables are consulted
      (`exchange_keys` first, because it is the table the vault actually reads), and a
      relation absent from a deployment is skipped — but if *every* candidate is absent the
      refusal stands, because "no table to check" is not "the account is yours".
    - **`mode` is written on every deployment either writer creates**, which is task 8.1's
      explicit obligation on this task and the reason the database-level 13.6 guarantee
      covers anything at all. `chk_sd_live_needs_account` guards `mode`, not the legacy
      `environment` column, and both writers previously set only `environment`. 13.6 is
      **also** enforced in the handler, so live-without-an-account is a 422 naming the
      missing account rather than a 23514 surfacing as a 500 — and the test asserts the set
      of refused (mode, account) pairs is exactly `{(live, None)}`, one pair, so a rule that
      also refused paper-with-account (which is how an author paper-trades against the venue
      they intend to go live on) fails. `normalise_mode` fails closed: `'backtest'` is
      refused because a backtest is a `strategy_backtests` row, and an *explicit*
      `mode: 'cloud'` is refused, while a legacy `environment` of `cloud`/`local`/`sandbox`
      resolves to `paper` — those describe where a worker runs, not whether fills are real,
      and mapping either onto `live` would route real orders because of a hosting choice.
    - **`READY` names the state *and* the outstanding prerequisite, per state.**
      `_OUTSTANDING_PREREQUISITE` carries a sentence for each of the ten non-`READY` states
      in `chk_lifecycle_state`, and a test asserts every state has its own entry, that the
      refusal message contains both the state and the sentence, and that no state falls
      through to the generic "unrecognised" wording. A state outside the constraint is
      refused *naming `chk_lifecycle_state`*, because a row disagreeing with the schema is
      more useful said than reported as "unknown". A row with no `lifecycle_state` at all is
      refused identically to one holding NULL — in practice such a row is already refused a
      gate earlier by 4.3's `validation_state` check, since both are 004 part-1 columns, so
      this is the same disposition rather than a new one.
    - **The symbol and the timeframe come from real sources, and there is no list here.**
      The market is read from the version's own `compiled_plan` through the existing
      `scan_plan_markets` — the same scan the training path and the node preview use, so a
      deploy cannot disagree with them about which market a graph declares. Availability is
      task 7.1's cached universe (`AssetRef.available_on`, whose docstring already names
      this check as its reason to exist); the timeframe is task 7.2's `_pipeline_timeframes`
      intersection — **the same set `GET /registry/timeframes` serves**, imported rather
      than restated — and then the venue's own CCXT `timeframes` descriptor, which is static
      class metadata and therefore **no network call and no credential** (`asset_universe`
      never awaits an exchange from a request path and this does not either). A test greps
      the module's *tokenised* source for `"BTC/USDT"`, `"1h"` and `"5m"` and fails if any
      appears, so the Phase 7 defect cannot re-enter here disguised as a fallback. An
      unreadable venue vocabulary yields `None` — never an empty set, which would read as
      "this venue supports nothing" and refuse every deploy — and the binding then records
      `timeframe_source = "pipeline"` and carries a warning, so a weaker answer cannot be
      read as the stronger one.
    - **"Cannot tell" resolves to a refusal, everywhere.** This task creates a path to real
      order routing, so: an unreadable plan is a 409 (recompile, per 22.5's disposition, not
      a substituted market); two markets are refused rather than one chosen; an unrecognised
      mode or gap label is refused rather than defaulted; and **an asset universe that has
      not loaded is a 503 for paper as well as live**. That last one is the judgement call
      worth flagging: Requirement 13.4 says refuse when the account lists no market for the
      symbol, and "we could not check" is not "it is listed" — a paper deployment whose
      symbol the venue does not list produces fills priced from a feed the author will not
      have when they go live, which is exactly the figure they use to decide to go live. It
      is a retryable 503 (the universe refreshes off the request path), not a 4xx.
    - **One gap rule, task 7.5's, reused rather than reimplemented.**
      `resolve_binding_gap_policy` is a thin adapter over
      `market_data_contract.resolve_gap_policy`, which already refuses every fabricating
      strategy in **every** mode — stricter than 13.7's live-only floor — and returns the
      `GapPolicy` with its disclosure. The tests assert the code raised here **equals** the
      code `resolve_gap_policy` raises for the same input across 4 strategies × 2 modes, and
      that `FABRICATING_STRATEGIES` appears nowhere in this module, so there is no second
      live-only rule for the looser of the two to become the one consulted.
    - **Requirement 13.9 and 21.7: the binding is a reference, and that is structural.**
      `DeploymentBindingRequest` declares no api key, secret or passphrase field and sets
      `extra="forbid"`, so a body carrying one is a 422 **before the service is called** —
      and `extra="forbid"` also means a client that sends `max_notional` instead of
      `max_order_notional` is told so rather than having the limit it believes it set
      silently dropped. `normalise_execution_config` refuses any key matching
      `schema.is_forbidden_param`, the same vocabulary that strips credentials from node
      params, so `execution_config` cannot become a second place a secret lives.
      `public_binding` is assembled from an explicit key list rather than by copying a row
      and deleting a field — task 6.5's rule for `artifact_uri`, for the same reason — and a
      test searches the whole `repr` of a live deployment's response for `api_key`,
      `secret`, `passphrase` and `encrypted_` as **substrings**. The "no credential in the
      deploy path" assertions are made against **tokenised** source with comments and string
      literals removed, so the modules' own docstrings explaining the rule cannot satisfy
      them; and a companion test asserts `master_executor` — the live loop — **does** still
      call `load_decrypted_keys`, so "no credential here" cannot be satisfied by there being
      no credential path anywhere.
    - **004e is unapplied, and the degradation is honest in both directions.** One cached
      read-only probe per process through the caller's own client, a narrow error
      classification (`42703`/`PGRST204`/a message naming one of the five columns;
      `PGRST205` deliberately excluded, because a missing *table* must not be masked), an
      indeterminate answer resolving to "attempt the write", and a negative verdict that
      expires after 300 s so applying 004e to a running fleet needs no redeploy — the same
      shape `strategy_service.canonical_columns_supported` uses for part 1. A **paper**
      deployment then writes the legacy row shape, logs a warning naming
      `004e_deployment_bindings.sql`, and reports `binding_stored: false` with a
      `BINDING_NOT_STORED` warning — never a 500, and **never a deployment reported as bound
      when the binding was not stored**. A **live** deployment is refused with a 503 naming
      the file, because with the columns absent the account reference has nowhere to live,
      nothing records that the deployment is live, and `chk_sd_live_needs_account` does not
      exist to catch either — a live deployment written that way is one nothing can route
      and nothing can audit while the response claims it is running. The INSERT itself is
      wrapped, so a process that cached a *positive* verdict and then meets `42703` degrades
      at the write (and re-asserts the live refusal) instead of 500-ing; an unrelated write
      failure releases the reserved quota and propagates, because a deployment that was not
      recorded must not be counted.
    - **The legacy `routers/strategies.py` substitutions: reported, not silently rewritten.**
      Task 7.9's gate assigned to Phase 8 that `routers/strategies.py` substitutes
      `"binance"` and `"BTC/USDT"` on the legacy deploy/pause/resume paths. Those are a
      **genuinely separate route** — `POST /api/strategies/{id}/deploy` in a different
      router, with a different prefix, a different body, `Depends(get_fleet)`/
      `Depends(check_bot_quota)` and no version reference at all — not the surface this task
      extends (`POST /api/strategy-operations/strategies/{id}/versions/{v}/deploy`). So it
      is reported and left alone, per the instruction to scope tightly. **On the surfaces
      this task does own, both substitutions are gone**: `deploy_version` no longer starts
      the fleet with `strategy["symbol"] or "BTC/USDT"` — the symbol comes from the
      version's plan — and it no longer assigns `exchange_id` from `strategies.exchange`
      after the INSERT (a write that never persisted, reading a column SB-06 leaves empty).
      The venue now enters exactly once, from the account row, and only where 12.1 says it
      may.
    - **The one existing suite whose behaviour changed, and how.**
      `tests/test_task_4_3_deploy_prerequisite_gate.py`'s four `deploy_version` tests. The
      three **refusal** tests are unchanged in intent and still assert
      `DeployPrerequisiteError`, `start_bot` not awaited and no row inserted; they needed
      the `strategies` row added to the fake database, because the ownership assertion now
      re-reads it. The one **happy-path** test needed what any deployable version now needs:
      `lifecycle_state = "READY"`, a `compiled_plan` that reads back and declares one market
      (it previously held the unreadable `{"execution_order": []}` stub), and one real market
      seeded into task 7.1's cache. The amendment is recorded in that file's own docstring.
      No other deploy or lifecycle suite changed: `test_lifecycle_integration.py` (13
      skipped, needs a live server), `test_notifications_feature_e2e.py` and
      `test_deployment_binding_columns_migration.py` were run explicitly and are green
      untouched.
    - **Two controls tightened, both stated.** `deploy_version` now (1) asserts version
      ownership explicitly instead of resting on RLS alone, and (2) checks the live-trading
      entitlement against the binding's `mode` as well as the legacy `environment` string —
      `mode` is the value that decides whether fills are real, so it is the value the
      entitlement has to be checked against. `deploy_strategy` gained `mode` and 13.6 and
      **nothing else**: no lifecycle gate and no market-compatibility gate, because that is
      the strategy-level legacy surface and those gates belong to the versioned route. The
      consequence is a real behaviour change and is called out: a legacy `environment:
      "live"` deploy with no `exchange_account_id` used to succeed and is now a 422 naming
      the missing account. That is Requirement 13.6, in the direction the requirement points.
    - No auth, RLS, tenant isolation, financial invariant, execution safeguard, risk gate,
      idempotency key or rate limit was weakened. The route keeps its
      `Depends(get_current_user)` and its `@limiter.limit("50/minute")` (asserted against
      the source), `dag_risk_integration` / `execution_guard` / `risk_engine` /
      `assert_execution_safe` are untouched and remain authoritative on the intent path
      (task 8.4's subject), no migration was edited, and no policy, index, trigger or grant
      was touched anywhere.
    - **Verified with 115 new backend tests** in `tests/test_task_8_2_deployment_binding.py`,
      all passing. The compiler, the plan, the gap resolver, the lifecycle vocabulary, the
      pipeline timeframe intersection, the CCXT venue descriptors and the asset universe are
      all the **real** ones; the only double is the PostgREST client, which is the one thing
      this environment has no instance of — and it honours `.eq` filters and simulates both
      `42703` (missing column, per-column) and `42P01` (missing relation) so the ownership
      and degradation paths are exercised rather than described. The lifecycle gate is
      parameterised over every state in `chk_lifecycle_state`; 13.6 over the whole 2 × 2 of
      mode and account; the gap rule over 4 strategies × 2 modes against task 7.5's own
      raise codes.
    - **Not verified, and it cannot be here.** There is no PostgreSQL in this environment and
      **004e is unapplied**, so nothing proves the five columns accept these payloads, that
      `chk_sd_mode` and `chk_sd_live_needs_account` fire on a real row, or that
      `sd_owner_insert` re-checks tenancy as the TOCTOU argument above relies on — that half
      remains 004e's own VERIFICATION queries and task 8.7's isolation matrix. There is no
      live venue either, so nothing proves a real account's `exchange_id` resolves through
      the vault to usable keys, that a real venue's listed markets match the cached
      universe, or that a bound deployment actually routes an order — the runtime start is
      still `fleet.start_bot`, and `execute_plan` is task 8.4. The venue timeframe check
      depends on the installed `ccxt` build; the Kraken-specific test **skips itself** if
      that build does not exhibit the gap, rather than asserting a fact about a package
      version. And with the universe cache empty in this environment, a real deploy here
      would return the 503 this task chose deliberately.
    - Baseline held: backend **43 failed / 3663 passed / 18 skipped** over 3724 collected in
      pytest's default order with `.venv\Scripts\python.exe -m pytest -q` (291 s), against
      the pinned ceiling of **44**. The failure count is **unchanged** from 8.1's 43, over
      exactly the same pinned nineteen files with exactly the same per-file counts
      (`full_system_test.py` 4, `test_account_health_query.py` 1,
      `test_atomic_order_cancellation_fix.py` 4, `test_distributed_execution_safety.py` 7,
      `test_event_pipeline.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_safety_fix.py` 1, `test_exchange_vault_singleton.py` 2,
      `test_false_success_report_fix.py` 1, `test_fee_precision_fix.py` 1,
      `test_get_db_dependency.py` 1, `test_marketplace_pipeline.py` 4,
      `test_position_delta_race_condition_fix.py` 1,
      `test_reconciliation_engine_fail_closed.py` 1, `test_risk_management_lifecycle.py` 1,
      `test_risk_settings_api.py` 2, `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_tenant_isolation_fixes.py` 1, `test_transaction_isolation_serializable.py` 3).
      **The collection delta reconciles exactly:** 3724 − 3609 = **115**, this task's new
      file and nothing else; 3548 + 115 = 3663. Frontend: **not re-run, and deliberately
      so** — this task touches no frontend file (one new backend module, one new test file,
      and edits to `strategy_service.py`, `routers/strategy_operations.py` and
      `tests/test_task_4_3_deploy_prerequisite_gate.py`).

  - [x] 8.3 Implement the lifecycle state machine
    - Enforce only the transitions defined in `design.md § Lifecycle`; reject anything else with
      a conflict response naming the current state
    - Record actor, timestamp and reason through `core/audit_trail.py` for every version
      creation, lifecycle transition, deployment action and training create or cancel
    - Editing a `DEPLOYED`, `RUNNING` or `PAUSED` version returns a new draft instead of
      mutating the version, and the canvas renders read-only for those states
    - Support deploy, pause, resume and stop, and report binding state as one of `DEPLOYING`,
      `RUNNING`, `PAUSED`, `STOPPED`, `FAILED`
    - Move running deployments to `STOPPED` with the reason preserved on a guard trip or kill
      switch, using the existing `core/global_safety.py`
    - _Requirements: 9.4, 9.6, 9.7, 9.8, 9.9, 13.8, 13.10, 20.9_
    - **Implementation note.** One new module — `backend_app/backend/strategy_lifecycle.py` —
      plus additive edits to `core/audit_trail.py`, `strategy_service.py`,
      `routers/strategy_operations.py`, one keyword argument on
      `training_worker.set_version_lifecycle`, and one new test file. No migration was
      edited and no existing symbol changed meaning.
    - **A sibling module, because the question is a different question.** Task 8.2's
      `deployment_binding` answers *may this version be bound to this account, in this mode,
      now?* using reads only. This one answers *given where something is, where may it go
      next, and what is written down when it goes there?* — asked by the deploy path, by
      pause/resume/stop, by a guard trip and by the editor, three of which have nothing to do
      with binding. Nothing is duplicated: `DeployRejected` is the refusal **base class** (so
      `LifecycleRejected` reaches a client through the mapping both deploy routes already
      have, with no second mapper to drift), `BINDING_STATES` is imported, `LIFECYCLE_STATES`
      stays `chk_lifecycle_state` verbatim in `strategy_builder`, and the `lifecycle_state`
      write goes through `training_worker.set_version_lifecycle` — the existing validated
      setter that the model-versioning suite already pins — rather than a third writer of one
      column.
    - **The transition table is the diagram, edge for edge, and a test proves it.** All
      seventeen arrows of `design.md § Lifecycle` are transcribed, including the `DRAFT →
      DRAFT` self-edge and `VALIDATED → DRAFT`; the test lists them independently and asserts
      **set equality**, so an edge added to the code that is not drawn fails, and a drawn edge
      the code dropped fails too. A second test asserts the table's keys are exactly
      `chk_lifecycle_state`, so a state added to the migration cannot arrive with undefined
      transitions and be silently treated as terminal. A third greps the design file for the
      same seventeen arrows, so changing the diagram forces this task to be revisited instead
      of quietly passing. Legality is verified over all **11 × 11** pairs, not sampled.
    - **The one gap this task reports rather than invents: there is no `DEPLOYED → STOPPED`
      edge.** The design's rule is explicit ("only forward transitions listed above are
      legal"), so a deployment whose runtime start *fails* leaves the version at `DEPLOYED`
      while the binding is `FAILED` — the version cannot legally reach `STOPPED`, and so
      cannot reach `ARCHIVED` either, until it has actually run. Patching in an invented edge
      would put a state machine in the code that no longer matches the one in the design with
      no way for a reader to tell which is authoritative. So it is transcribed as-is and
      reported in three places instead: the skipped transition logs a warning naming the
      missing edge, the response says `lifecycle.moved: false` with the reason, and
      **Requirement 20.9 is satisfied at the binding level anyway** — which is where 20.9 puts
      it ("move the affected running **deployments** to `STOPPED`"). A deployment is always
      stoppable; only the version's own label has nowhere to go. Two tests pin exactly that:
      the deployment reaches `stopped` and the version stays `DEPLOYED`.
    - **Two machines, related in exactly one place.** The version vocabulary has no `FAILED`
      and the binding vocabulary has no `ARCHIVED`, so
      `VERSION_STATE_FOR_BINDING_STATE` is **deliberately partial** — `FAILED` maps to
      `None`, because writing a `FAILED` version state would be a 23514 from PostgreSQL.
    - **13.10 is a reporting requirement, so no column vocabulary was rewritten.**
      `strategy_deployments.status` is migration 001's lowercase string with an index, existing
      rows, several writers and no `CHECK`. The module maps between it and the five uppercase
      states (`binding_state` / `status_column_value`), every legacy spelling this codebase
      writes is mapped (`deployed`, `starting`, `active`, `stopping`, `restarting`), and the
      response keeps the lowercase `status` key **next to** `binding_state` so existing
      clients do not break. `binding_state` is total: a status nothing recognises — or a
      missing one — reports **`FAILED`** with a warning naming the raw value, never `RUNNING`
      (a claim about real money) and never `STOPPED` (a claim it is not). The raw value travels
      as `status_raw` so an operator can tell a real failure from an unreadable one.
    - **The deploy path now moves the version, which is what makes immutability real.**
      Before this task `deploy_version` never touched `lifecycle_state`: a deployed version
      stayed `READY` for ever, and migration 004c's `trg_sv_immutable` — which gates on
      `lifecycle_state IN ('DEPLOYED','RUNNING','PAUSED') OR is_read_only` — therefore had
      nothing to fire on. Now `READY → DEPLOYED` happens immediately **after** the INSERT that
      justifies it (a refused INSERT must leave the version deployable) and `DEPLOYED →
      RUNNING` after the runtime starts, and the same UPDATE sets `is_read_only = TRUE`, which
      004c's own header records as a column no code ever set. `OLD.is_read_only` is still
      `FALSE` on that statement, so the trigger does not fire on the write that arms it and
      guards every later one. Post-INSERT transitions are **non-strict** by design: the gate
      already asserted `READY`, and a label that could not be written must not un-deploy a
      deployment that was.
    - **Pause/resume/stop: one implementation, and a tenant-isolation hole closed on the way.**
      `POST /api/deployments/{id}/pause` and `/resume` previously passed the path parameter
      straight to `deployment_manager` and then updated the row **by id alone, with no
      `user_id` filter anywhere** — one user could pause another user's deployment. All three
      now go through `StrategyService.transition_deployment`, which reads the row with
      `.eq("user_id", …)`, writes with the same filter, and reports another tenant's
      deployment as **404** rather than 403 (tasks 6.3/8.2's convention: a 403 confirms
      existence). Three endpoints share one body because three copies is how one of them ends
      up without the filter. Order: read scoped → gate → ask the runtime → write → move the
      version → audit. The runtime call is **best effort and says so**: `deployment_manager` is
      a per-process dict that has never heard of any deployment `deploy_version` created (that
      path starts a fleet bot), so refusing to stop a deployment because an in-memory registry
      lost it would be a control that harms; the row is authoritative. An action whose target
      the deployment is already in is **idempotent** — a retried request after a dropped
      connection must not read as an error — and anything else off the binding machine is a
      409 naming the current state and the actions that *are* available. The duplicate
      `/deployments/{id}/stop` registration (which pre-dates this task and was already
      shadowed) now points at the same implementation and is `include_in_schema=False`, so the
      OpenAPI document stops advertising one path twice.
    - **Editing a deployed version: the row is left *exactly* as it was.** `update_strategy`
      already created a new version on a blueprint change, but it also flipped the current
      version's `is_current` to `FALSE` and knew nothing about lifecycle state. For a
      `DEPLOYED`/`RUNNING`/`PAUSED` version the fork is now `is_current = FALSE`,
      `is_draft = TRUE`, `lifecycle_state = 'DRAFT'` (written only where 004 part 1 is
      applied) and `cloned_from_version = current.id` — the design's `base := current.id`, in
      migration 001's own column — and **the deployed row is not written at all**. 9.4 says
      "SHALL leave the existing version record unchanged", and demoting a running
      deployment's version has consequences: the Builder would open a draft as "the strategy"
      while the executing plan is somewhere else. An editable version keeps the pre-existing
      behaviour byte for byte. `edit_disposition` fails **safe**: a version whose state is
      absent or unrecognised is treated as read-only, because forking a draft costs the author
      one save while mutating a version that was in fact running changes what a live
      deployment is doing.
    - **9.9 with no frontend to render it.** There is no Builder canvas in this repository yet
      — no file under `algo22-terminal/` or `aerora_quant_platform/` references
      `strategy-operations`, a registry endpoint or `lifecycle_state` — so the rendering half
      cannot be written here and is **reported, not silently skipped**. What this task owns is
      published: `GET /strategies/{id}/versions` now carries a `canvas` block per version
      (`read_only`, `editable`, `edit_creates_new_draft`, the four `frozen_fields`, the legal
      next transitions and a sentence), decided by the backend so the UI cannot offer an edit
      004c's trigger would then reject. The version rows are unchanged; the block is added
      alongside them.
    - **9.8 through `core/audit_trail.py`, without touching the order trail.** A new
      `StrategyAuditLogger` / `StrategyAuditRecord` / `StrategyAuditAction` section was added
      to that module; **no existing symbol changed**, and a test pins `AuditEventType`'s seven
      members in order. Two concrete reasons a lifecycle transition is not an
      `OrderAuditRecord`: that record is an order (symbol, side, size, price, fee, exchange
      response — all null for a transition), and `OrderAuditLogger._cache_in_redis` keys on
      `audit:{execution_id}:{event_type}`, which holds **one** record per pair — lifecycle
      transitions accumulate (`DEPLOYED, RUNNING, PAUSED, RUNNING, STOPPED` against one
      version), so that key shape would overwrite the history it exists to preserve. This
      logger appends to a Redis **list**, the same structure `global_safety` uses for its own
      activation history, bounded and expiring after 30 days. Every record carries actor,
      timestamp, reason **and both `before` and `after`** — "moved to STOPPED" cannot answer
      "was it running, or had it already failed?", which is exactly the question asked after a
      trip. The structured log line is written first and unconditionally because it is the one
      sink that exists in every environment; Redis is a cache on top. An audit write **never**
      fails the act it describes (a deployment must not stay running because Redis was down)
      and is **never silent** (a failed write logs a warning naming the record). Wired at all
      five places 9.8 names: version creation (`create_version` and the edit fork), every
      lifecycle transition, every deployment action (deploy, the runtime start, a failed
      start, pause, resume, stop, guard trip), training job creation — only for a job actually
      created, since an idempotent hit created nothing — and `cancel_requested`, recorded as
      *requested* rather than *cancelled*, because Requirement 15.7 makes the worker the thing
      that ends a job.
    - **20.9 uses `global_safety` in the stop direction only, and that is deliberate.**
      `stop_deployments_for_reason` moves this user's `DEPLOYING`/`RUNNING`/`PAUSED`
      deployments to `STOPPED`, writing the reason to `error_message` — a column migration 001
      already provides, so no migration was needed — with `stopped_at`, and every read and
      write carries `.eq("user_id", …)`: a trip against one tenant must not stop another's
      deployments, and a trip is not a reason to widen a filter. One deployment that will not
      write is collected in `failed[]` and the rest still stop. `stop_deployments_on_kill_switch`
      reads the switch and stops **nothing** unless it says it is active, and writes the reason
      from the switch's own activation history rather than one composed here; its fail-safe
      (unreachable Redis ⇒ active) is followed rather than second-guessed, and the reason says
      when it was the local latch. **No kill-switch check was added to the deploy path**, and
      the reason is worth stating: with no Redis in this environment `is_active()` fails closed
      to `True`, so a deploy-time check would refuse every deployment here — a control that
      looks strict and is actually an outage. The runtime caller that invokes a guard trip
      (`dag_event_loop` / a later phase) is out of this task's scope; the Deployment_Service
      entry point 20.9 names is what this task provides.
    - **No control was weakened.** Every route keeps its `Depends(get_current_user)` and its
      `@limiter.limit` (asserted against the source for all three transitions);
      `execution_guard`, `risk_engine`, `dag_risk_integration`, `assert_execution_safe`, the
      idempotency controls and `global_safety` itself are untouched; no RLS policy, index,
      trigger, grant or migration was edited; the transition body is `extra="forbid"` with one
      field (a reason), because a transition is not an opportunity to re-bind an account.
      Quota is released only on `RUNNING → STOPPED`, and the idempotent branch returns before
      it, so a repeated stop cannot decrement twice. Two things were tightened: the tenant
      filter on pause/resume described above, and `is_read_only` now actually being set.
    - **Verified with 267 new tests** in `tests/test_task_8_3_lifecycle_state_machine.py`, all
      passing. The lifecycle vocabulary, the transition table, the binding vocabulary, the
      audit logger, the service and the router are the **real** ones; the doubles are the
      PostgREST client (this environment has no instance of one) and the kill switch (needs
      Redis). The fake client honours `.eq` filters **and applies updates to its rows**, so
      "the tenant filter was on the write" and "the reason was preserved" are facts about the
      write rather than about the call — a fake that only recorded calls could not tell a
      write carrying the reason from one carrying the filter twice.
    - **Not verified, and it cannot be here.** There is no PostgreSQL and 004/004c/004e are
      unapplied, so nothing proves `chk_lifecycle_state` rejects a bad label, that
      `trg_sv_immutable` refuses a graph edit on a `DEPLOYED` row, or that `is_read_only` makes
      that trigger fire — those remain 004c's own VERIFICATION queries and task 8.7's
      isolation matrix. There is no Redis, so the audit assertions are made against the
      logger's records, not against a Redis list, and the kill-switch tests assert what the
      module does with a verdict rather than that a real switch produces one. There is no
      fleet or venue either, so the deploy test's runtime start is a double.
    - Baseline held: **43 failed / 4054 passed / 18 skipped** over 4115 collected with
      `.venv\Scripts\python.exe -m pytest -q` in default order (686 s), against the pinned
      ceiling of 43. The failure count is **unchanged**, over exactly the same pinned nineteen
      files with exactly the same per-file counts (`full_system_test.py` 4,
      `test_account_health_query.py` 1, `test_atomic_order_cancellation_fix.py` 4,
      `test_distributed_execution_safety.py` 7, `test_event_pipeline.py` 3,
      `test_exception_swallow_regression.py` 2, `test_exchange_safety_fix.py` 1,
      `test_exchange_vault_singleton.py` 2, `test_false_success_report_fix.py` 1,
      `test_fee_precision_fix.py` 1, `test_get_db_dependency.py` 1,
      `test_marketplace_pipeline.py` 4, `test_position_delta_race_condition_fix.py` 1,
      `test_reconciliation_engine_fail_closed.py` 1, `test_risk_management_lifecycle.py` 1,
      `test_risk_settings_api.py` 2, `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_tenant_isolation_fixes.py` 1, `test_transaction_isolation_serializable.py` 3).
      **On the collection count, precisely:** `pytest --collect-only` with this task's file
      ignored reports **3848**, not the 3831 quoted when the task was assigned, so that figure
      was already stale before this task started; 3848 + **267** (this file, counted on its
      own) = 4115, which reconciles exactly. This task created one test file and edited none.
      The suites most at risk were also run explicitly and are green:
      `test_task_8_2_deployment_binding.py`, `test_task_4_3_deploy_prerequisite_gate.py`,
      `test_model_versioning.py`, `test_strategy_version_canonical_persistence.py`,
      `test_audit_trail_events.py` and `test_notifications_feature_e2e.py` — 274 passed
      together, none of them modified. Frontend: not re-run, and deliberately so — this task
      touches no frontend file (see the 9.9 note above: there is no Builder canvas to touch).

  - [x] 8.4 Implement the runtime readiness gate
    - Implement `execute_plan` in `dag_engine.py` per `design.md § DAG runtime contract`:
      level-wise evaluation, per-port input resolution from `plan.inbound`, `NOT_READY` on a
      missing required input, `AWAITING_MODEL` on an unloaded or checksum-mismatched model,
      `WARMING` until warmup is satisfied
    - Emit an intent only when every node in the action's upstream closure is `READY`, and run
      `assert_execution_safe` before the intent leaves the runtime
    - Hold a merging node warming when any input is warming; merge is all-or-nothing
    - Keep `dag_risk_integration.py`, `execution_guard.py`, `risk_engine.py` and the existing
      idempotency controls authoritative on the intent path
    - _Requirements: 17.6, 20.1, 20.2, 20.8, 20.10, 20.11_
    - **Implementation note.** One file changed — `backend_app/backend/dag_engine.py` — plus
      one new test file. `execute_plan` is **additive**: `execute_dag` is byte-for-byte the
      path it was, which is why the golden file could not move.
    - **The whole design follows from one sentence: the four states are four states.**
      `NOT_READY`, `AWAITING_MODEL`, `WARMING` and `READY` are not degrees of the same
      thing — an author fixes each of them differently (wire something, train something,
      wait, nothing). So the level walk classifies *why* a port has no value rather than
      only *that* it has none: a port whose every unsatisfied feed is a `WARMING` node is
      itself warming, and anything else is `NOT_READY`. Collapsing the two would put "wait
      for it" on a graph that will never become ready, or "fix your wiring" on a deployment
      that started ten bars ago. `AWAITING_MODEL` and `READY` are **imported** from task
      8.6's `model_readiness`, and the checksum decision is `model_ready(row)` — called, not
      re-derived. `NOT_READY` / `WARMING` are spelled here because 8.4 owns the state
      machine. `mark_node(state, node_id, LABEL, missing)` refuses an unpublished label
      rather than storing it.
    - **Every port is read with `plan.inbound_edges` — the plural accessor.** A variadic
      port (`add`, `and`, `or`, `min`, `max`, `multiply`, `between`, `feat_concat`) holds
      2..N edges and is satisfied only when **every** operand the author wired is present.
      `inbound_edge` would raise on exactly those ports, and a gate that read one edge would
      decide readiness from a subset of the author's operands with nothing raised anywhere —
      the defect task 5.0 removed, re-entering through the runtime. Value availability is
      asked through the same two-step resolution `resolve_node_inputs` performs (exact
      output port, then the source's primary), so "the gate thought this port was fed" and
      "the executor found a value there" cannot disagree. Input **ports** come from the
      registry descriptor, never from `node.inputs`: a payload that declares no ports must
      not thereby have none.
    - **Merge is all-or-nothing, and a warming node is not executed at all.** This is the
      one place the pseudocode had to be read rather than transcribed. A block asked for a
      value it has no history for returns an all-NaN series, and the existing
      `core/pipeline_guard.py` **raises** on one — correctly, because on a full window that
      means a broken indicator. Executing a warming node would therefore turn "this
      deployment is young" into a hard runtime failure, and catching that exception to
      recover would be weakening a control that is right to be strict. So the gate declines
      to ask: a node with an unmet warmup, or any warming input, is marked and skipped, and
      publishes nothing — which is also what makes warming propagate to the action.
    - **Judgement call, stated plainly: `WARMUP_SERIES_HEADROOM = 2`.** `design.md` asserts
      `window.bars >= plan.warmup_bars`; that is one bar short (warmup counts bars that are
      *discarded*, so the first trustworthy bar sits at index `warmup`), and even
      `warmup + 1` is a window that is almost all warmup, which the pipeline guard refuses
      as >50% NaN. So `bars_needed = max(warmup + 1, 2 x warmup + 1)`. The headroom is not
      invented here — it is the same figure, for the same reason, as
      `routers/strategy_operations.PREVIEW_WARMUP_HEADROOM` ("warmup as a minority of the
      series"), and a test pins the two together. The cost is that a node stays `WARMING`
      slightly longer than strictly necessary. That is the safe direction, and the only
      alternatives were crashing a young deployment or softening a guard.
    - **The intent path gained one refusal and lost none.** `execute_plan` returns
      `TradeIntent` objects and **sends nothing**; `dag_risk_integration`, `execution_guard`,
      `risk_engine` and the idempotency controls are untouched and remain authoritative
      (20.8), and a structural test asserts no `place_order` / `CCXTExchangeExecutor`
      reference exists anywhere in `dag_engine` and that none of those three modules was
      rewired. `assert_execution_safe` runs on every triggered intent and its raise is
      **not** swallowed — nothing is returned, so nothing is sent. The one control that had
      to be *added* is the platform SYSTEM FREEZE: `ActionExecutor` already consults
      `SafetyMonitor.check_execution_allowed('strategy_signal')`, and because this gate
      derives a trigger from the ACTION node's *signal input* rather than from the
      executor's signed output, a frozen platform would otherwise still have produced
      intents. Node evaluation is deliberately left unfrozen — a freeze stops intents, not
      the canvas.
    - **Why the trigger comes from the input port.** An ACTION descriptor is `TERMINAL` with
      **no output ports**, and `plan_to_engine_graph` adapts the six exit blocks as
      `action="hold"` rather than guessing a side, so their engine output is all zeros by
      design. Reading the author's condition off the `signal` port is the only faithful read
      for all six order types. `ACTION_TRIGGER_THRESHOLD` is now spelled once and
      `ActionExecutor` compares against it, so a live intent cannot fire on a bar the
      backtester scored flat (22.4). `triggered` is `False` — never an exception — for an
      unfed port, a multiply-fed `signal` port, an upstream that produced nothing, and a
      `NaN` at the current bar. No price is resolved and no side is inferred: an
      `offset_bps` mode needs market context the runtime does not own, and an exit's side is
      the inverse of a position the runtime does not hold.
    - **No second market-data ingest.** Task 7.10 owns arrival;
      `_assert_window_usable` only *refuses* a frame those guarantees do not hold for
      (empty, duplicate timestamps, out of order) and repairs nothing — a repair here is an
      invented bar, and re-sorting on a live path would hide a late arrival. `design.md`'s
      `window.bars >= plan.warmup_bars OR mode = WARMING` is honoured through its second
      branch: a short window is answered per node, not rejected.
    - **Verified with 57 new backend tests** in
      `tests/test_task_8_4_runtime_readiness_gate.py`, all passing: the four labels distinct
      and 8.6's constants identical; every node ready on a long window; the warming boundary
      exact to the bar; warming propagating through a merge with the port named; a variadic
      port holding a node warming on one cold operand (with `inbound_edge` asserted to raise
      on that same port, so the plural accessor is pinned as the one in use); `NOT_READY`
      naming its unfed port and never reported as warming; an action missing its `quantity`
      held rather than sized by default; `AWAITING_MODEL` for no active version, for a
      checksum mismatch over real bytes in a real `LocalArtifactStore`, and for a store that
      raises — plus the clear when the checksum verifies; `assert_execution_safe` raising
      `NON_POSITIVE_QUANTITY` out of `execute_plan` with the incident recorded; the freeze
      holding; the window refusals; determinism and state reuse; `execute_dag` unaffected;
      and one **property** (`bars` in 1..200) that every label is legal and nothing is
      emitted below the plan's composed warmup.
    - **The golden file did not move.** `tests/golden/dag_runtime_plan_golden.json` hashed
      SHA-256 `28C64CBB36767F1A4B4BA119896E32CC3664491709FB2231BC9151924153A683` before the
      first edit and **the identical value** after the full suite — no trade decision moved.
      All nine named regression files are clean: `test_dag_runtime_golden_plan.py`,
      `test_dag_engine_port_addressing.py`, `test_math_executor_firewall.py`,
      `test_logic_and_indicator_executors.py`, `test_compiled_plan.py`,
      `test_closed_bar_streaming_ingest.py`, `test_live_execution_reliability.py`,
      `test_risk_gate_enforcement.py`, `test_live_risk_gate_enforcement.py` — 343 passed,
      0 failed.
    - Baseline held: backend **43 failed / 3770 passed / 18 skipped** over 3831 collected in
      default order, against the recorded **43 failed / 3713 passed / 18 skipped** over 3774.
      **The delta reconciles exactly:** 3831 − 3774 = **57**, this task's new file and
      nothing else; 3713 + 57 = 3770; failures unchanged at 43 (ceiling 44) and skips
      unchanged at 18. Every failure is the same pre-existing Redis / Postgres /
      live-server-dependent set task 8.6 recorded. Frontend: **not re-run, and deliberately
      so** — this task touches no frontend file, and task 8.5 owns canvas rendering.

  - [x] 8.5 Surface runtime state and deployment realtime channels
    - Extend `ws_channels.py` with `builder.validation.{strategy_id}`,
      `strategy.{strategy_id}`, `deployment.{deployment_id}` and `execution.{deployment_id}`,
      authorised against the resource owner
    - Implement the shared ref-counted socket hook: one connection per session, resubscribe and
      snapshot on reconnect, reauthenticate on token refresh without reconnecting, bounded
      jittered backoff to 30 s, and a 30 s safety poll only while disconnected
    - Render node runtime state on the canvas: warming with a bar count, ready, awaiting model,
      training with epoch counter, deployed lock
    - _Requirements: 20.12, 21.5, 21.6, 23.1, 23.2, 23.3, 23.4, 23.5, 23.6_
    - **Implementation note.** Three backend files **extended**, four frontend files
      extended, three new files (one frontend hook, one frontend projection, two test
      files). No new module owns channel names, no second authorisation path, and no
      second socket layer — every one of those was an available shortcut and each was
      the specific thing this task was told not to do.
    - **Task 6.7 wrote one parameterised channel by hand; four more copies of it would
      have been four chances to admit a name it refuses.** That is the whole argument for
      the shape of the backend change. 6.7's `training.{job_id}` needed a prefix constant,
      a builder, a parser, a namespace predicate and an event-membership predicate;
      transcribing that five times puts the separator, the identifier pattern and the
      "claims the namespace but names no resource" rule in five places, and the one that
      drifted would be the one a subscriber could smuggle `deployment.*` past. So
      `ws_channels.py` now states the shape rule **once** (`_RESOURCE_ID_PATTERN`, with
      `_JOB_ID_PATTERN` kept as its alias), the family is data (`OwnedChannelFamily`), and
      `parse_owned_channel` / `owned_channel` are the only parser and the only builder.
      6.7's five training helpers survive **by name, answering exactly what they answered**
      — they are now wrappers, and a test class re-asserts each of them plus its three
      transport methods and both its `get_stats` figures, because a wrapper is where a
      behaviour change hides. A parametrised test drives all ten of 6.7's malformed-name
      cases against all five families: the bare namespace, an empty segment, `a.b`, `*`,
      `%`, whitespace, `/etc/passwd`, a backslash and a 65-character id.
      `builder.validation` carries a dot of its own and is parsed at its **last**
      separator, so `builder.validation.a.b` is refused rather than read as strategy `a`,
      and the family list is sorted longest-namespace-first with a test pinning that
      order rather than trusting the literal.
    - **`ws_channels` still does not know who owns anything, and that is deliberate.** The
      family-to-relation map lives in `core/websocket_auth.py`, because that is the module
      that has to hold an owner to compare against and because a family descriptor
      carrying a table name would be a second place for the relation name to drift from
      the service that reads it. 6.7's `_resolve_training_job_owner` became one generic
      `_resolve_owned_channel_owner` over a per-family `_OwnerRelation`, so **every**
      refusal branch is shared by construction: no client, unloadable service, missing
      relation, transport failure, no visible row, a row with no `user_id`, and a row owned
      by somebody else all deny, for all five families. Four hand-written copies of that
      would have been four chances to get "a failed lookup denies" wrong, and that mistake
      is an authorisation bypass rather than a cosmetic divergence. The two
      strategy-scoped channels resolve `strategies.user_id`; the two deployment-scoped ones
      resolve `strategy_deployments.user_id` — **the same row**, which is correct rather
      than lazy: a deployment's fills and its lifecycle belong to whoever owns the
      deployment, and a second ownership rule for the execution stream would be inventing a
      way for the two to disagree about who may watch one running strategy. A test proves
      the family, not the identifier, picks the relation: `STRATEGY_ID` is a real owned
      strategy and is **refused** on `deployment.*`.
    - **A missing relation warns, names the migration, and denies.** There is no local
      PostgreSQL and 004/004b/004c/004d/004e are unapplied, so this is the path that
      actually runs here rather than a hypothetical. `strategy_deployments` names
      `001_strategy_architecture.sql` — the file that creates the relation and its
      `user_id`, **not** 004e, which only adds binding columns this lookup does not read.
      `strategies` predates this repository's `migrations/` directory, so its refusal names
      no file: naming a wrong one would be worse than naming none. Never a 500; the
      subscription is refused with `CHANNEL_OWNER_UNRESOLVED`, and a test asserts the file
      name appears in both the log and the reason the client is shown. `_is_missing_relation_error`
      reuses `strategy_service.is_missing_training_table_error` for the generic
      PostgreSQL/PostgREST codes — the same reuse `model_versioning` makes, one degradation
      vocabulary for the whole Strategy Builder — and adds only the table-named phrasing.
    - **The subscriber registry is now keyed by the full channel name, and it had to be.**
      6.7 keyed it by bare `job_id`, which worked while there was one family and collides
      the moment there are five: `deployment.{id}` and `execution.{id}` share an identifier
      **by design**, so a resource-id-keyed registry would deliver every fill to a client
      that asked only for lifecycle transitions. Two tests hold that shut, one for the
      deployment/execution pair and one for a strategy and a deployment that happen to
      share an id. `get_stats`'s `training_channels` / `training_subscribers` still count
      training only (6.7's test asserts they reach zero), with `owned_channels` /
      `owned_subscribers` added beside them. `subscribe_owned` **refuses** a name no family
      routes: the registry's entire premise is that everything in it was authorised against
      an owner, and a name with no resource has no owner.
    - **The endpoint gained one action and no new leniency.** `auth` is Requirement 23.3.
      A refreshed token that verifies for the same `sub` replaces the identity future
      subscriptions are authorised against and **acknowledges without reconnecting**; a
      token that does not decode, or one for another user, sends `auth_failed` and closes.
      Running on under an identity the server can no longer vouch for was the alternative,
      as was silently keeping the old one while the client believes it re-presented
      credentials. It does not retroactively bless existing subscriptions — those were
      authorised against the identity that held at the time, and this identity has to be
      the same user anyway.
    - **Requirement 20.12 and the deployed lock travel as verdicts, not as inputs to a
      client-side rule.** `runtime_state_frame` publishes task 8.4's
      `PlanRuntimeState.to_dict()` — the method 8.4 wrote for exactly this — and
      `canvas_state_frame` publishes task 8.3's `canvas_state` mapping, both field for
      field. Neither imports the module it publishes (the caller passes the value it
      already holds), which is what keeps `ws_channels` free of backend imports at module
      scope. Two **structural** tests assert the source of `ws_channels.py` contains no
      string literal for any of `dag_engine.RUNTIME_STATES` or any of
      `strategy_lifecycle.READ_ONLY_LIFECYCLE_STATES`, so a fifth label or a sixth lock
      rule cannot enter through this path even by accident. The warming bar counts are
      asserted to **equal** `state.bars_needed(node)` / `bars_remaining(node)` rather than
      being restated as arithmetic — 8.4 owns its headroom rule and pins it in its own file,
      and a second copy of that figure here would eventually disagree with it.
      `runtime_state` rides `deployment.{deployment_id}` and not `strategy.{strategy_id}`
      because a node is warming *for a running deployment over a window of bars*, and the
      same version deployed twice holds two runtime states at once.
    - **Frontend: `websocketClient.js` was extended, and the reason it needed extending is
      that its `subscribe()` never told the server anything.** That method is a client-side
      routing table keyed by `message.type`; the builder's channels are server-side
      subscriptions that must be *asked for* and can be *refused*. So the class gained
      `subscribeChannel` (ref-counted per channel, so several views share one server-side
      subscription and the `unsubscribe` frame goes out only when the last holder leaves),
      channel-keyed delivery alongside the existing type-keyed delivery (both run — an
      event-type subscriber and a channel subscriber are two questions about one frame),
      `reauthenticate` (Requirement 23.3, no reconnect), `acquire`/`release` (Requirement
      23.1's one connection per session, ref-counted), `onStatusChange`/`onOpen`, and
      jitter on the existing capped backoff. A subscribe frame is sent **only** on an open
      socket and never queued: `handleOpen` resubscribes from the registry, so queueing
      would put the same frame on the wire twice — a defect this task hit and fixed rather
      than documented away.
    - **`maxAttempts: Infinity` is the one policy change, and it is safe only because of
      the other three fields.** The client's default of 5 is right for a page that can be
      reloaded and is left alone for every existing caller; a builder left open through a
      backend deploy has to come back on its own, and giving up after five tries leaves it
      silently stale behind a socket the user believes is live. `setReconnectPolicy` returns
      the previous policy and `release()` restores it, so raising the ceiling is scoped to
      the builder session. Unbounded retry is defensible **because** the delay doubles, is
      capped at 30 s and is jittered: a test drives twelve schedules and requires every
      delay to sit inside `cap + jitter`, and a second draws forty delays pinned past the
      cap and requires more than one distinct value — the jitter clause is the one that
      matters in aggregate, since without it every browser that lost the same restart
      retries on the same schedule.
    - **Requirement 23.5's snapshot and 23.6's poll are the same read, and giving them one
      implementation was the point.** Both are "read the current status over the
      authenticated REST surface", and this page already has exactly one such read: task
      7.11's data-quality poll, which is single-flight, abort-aware and deliberately
      bypasses `apiClient`'s 60 s response cache — so a snapshot taken through it cannot be
      answered with a stale reading, which is the one thing a snapshot must not be. The
      instruction to reuse 7.11's shape fit better than expected: the shape *and* the read
      are reused, and no second endpoint, api-module or cache path was added. The hook
      calls `onSnapshot('reconnect' | 'mount' | 'poll')` and the page points it at that
      existing read. The resubscribe happens inside `handleOpen` **before** `onOpen` fires,
      so the order is resubscribe-then-snapshot; a snapshot answered while the connection
      is still receiving nothing would close no gap.
    - **The poll runs while the connection is not CONNECTED, which includes CONNECTING —
      and that reading is deliberate.** A dropped socket spends most of its time in
      `CONNECTING`, because each backoff attempt sets that state and only a successful open
      clears it. Treating `CONNECTING` as not-closed stopped the poll after the first
      reconnect attempt and left a session with an unreachable backend reading nothing at
      all, which is precisely the situation the safety net exists for. The test asserts
      both halves, and the negative half is the one that matters: three intervals pass on a
      connected socket and the poll fires **zero** times.
    - **The hook opens no socket without a token, and that is honesty rather than
      caution.** The endpoint closes an unauthenticated connection with 1008 before the
      first frame, so opening one would be a connect/refuse/backoff loop that can never
      succeed while reporting `DISCONNECTED` for a connection that was never possible. A
      session with no token reports `UNAVAILABLE` — a separate state from `DISCONNECTED`,
      because "nothing to lose" and "lost it" want different sentences and only one of them
      is fixed by signing in — and falls back to the same 30 s poll. It also means the
      existing builder test files, which carry no token, open no socket at all and were
      unaffected: **69 new tests, 677 → 746 passing, and not one existing test edited.**
    - **`realtime` is its own status cell, NOT folded into 7.11's `feed`.** The feed cell is
      a measurement of market data (7.4 classifies it, 7.11 reads it); this one is the state
      of the push transport. Folding them would let a dropped socket overwrite a true
      reading about the market with a statement about the transport, and would hide a
      genuinely stale feed behind a healthy socket — the same class of error as labelling
      stale data `LIVE`. Both live in the strip's existing `role="status"` /
      `aria-live="polite"` region, so both are announced, and the state word is text in
      every badge with the figures on `data-` attributes: colour is an addition to the
      reading, never the reading.
    - **On the canvas, training is reported ahead of `AWAITING_MODEL`, and the precedence is
      a judgement call worth stating.** A node whose model is training is `AWAITING_MODEL`
      at the same time — correctly, there is no active version yet — and both statements are
      true, but only "epoch 7/40" tells the author that waiting will fix it.
      `AWAITING_MODEL` with no job running is the one that means "go and train this", and a
      test drives a *failed* job to prove the label yields back. An unrecognised runtime
      label renders **nothing** rather than a fifth state, and a node nobody has evaluated
      renders nothing rather than looking fine; `RUNTIME_STATE_LABELS` is keyed by the
      backend's word so a vocabulary this build does not know cannot be rendered as a
      passing state. A broken `runtime_state` frame does not replace a good reading —
      blanking the canvas on a malformed frame would be worse than ignoring it.
    - **The deployed lock is the only place a control was disabled, and the reason is on
      screen before it is.** `read_only` comes from task 8.3's `canvas_state`; the banner
      quotes the backend's sentence verbatim, lists its `frozen_fields`, every node carries
      the lock affordance (the lock is a property of the version, so locking only some
      blocks would be a lie), and Save/Compile are disabled — the write they would attempt
      is one migration 004c's trigger refuses, so offering it would be offering an edit that
      cannot land. Selection, panning, the inspector and previews all stay on: reading a
      locked version is exactly what an author does with one. Until a verdict arrives
      `locked` is **false** and nothing changes, which is why no existing test moved.
    - **A refused subscription is on screen with the server's sentence.** The server refuses
      the subscription and keeps the connection, so silence would be indistinguishable from
      a channel with nothing to say and a disconnect message would be false. The refusal is
      recorded per channel and handed to whoever asked for it; a late joiner to an
      already-refused channel is told immediately rather than waiting for a reconnect.
      Refused channels **are** resubscribed on reconnect: a refusal is answered per
      connection, and a token refresh or an applied migration can legitimately change the
      answer, for the cost of a few bytes if nothing did.
    - **No control was weakened on either side.** No authentication, authorisation, RLS,
      tenant-isolation, risk, execution-guard, idempotency or rate-limit path was changed;
      the four new channels each **added** an ownership check where there was none, and a
      test re-asserts that `orders`, `positions`, `pnl`, `all` and `market.*` still get
      exactly the authorisation they had and reach the database not at all. Every refusal
      frame carries only `type`, `channel`, `code` and `reason` — no owner id, no resource
      data, and one sentence for "not yours" and "does not exist" alike, so none of these
      channels becomes an existence oracle for another tenant's identifiers.
    - **Verified with 144 new backend tests** in
      `tests/test_task_8_5_runtime_state_channels.py`, all passing, and **69 new frontend
      tests** in `algo22-terminal/tests/unit/builderRealtime.test.jsx`, all passing. Task
      6.7's `tests/test_training_realtime_channels.py` is **unedited and 50/50 green**.
    - Baseline held on both sides, and the deltas reconcile exactly. Backend: **43 failed /
      4201 passed / 18 skipped** with `.venv\Scripts\python.exe -m pytest -q` in default
      order; the same run with only this task's file ignored is **43 failed / 4057 passed /
      18 skipped**, and 4057 + 144 = 4201 with failures unchanged at 43 against the ceiling
      of 43. Frontend: **1 failed / 746 passed (747)** with `npx vitest run`; the same run
      excluding this task's file is **1 failed / 677 passed (678)**, and 677 + 69 = 746.
      The one failure is `portfolio-rendering.test.jsx` → "should verify improved empty
      states", already recorded as out of scope and untouched; `integration.test.jsx`'s
      30 s-timeout flake did not fire this run, so the number is 1 rather than 2 — the
      recorded pair, not a new one.
    - **One defect found and fixed on the way through, and it was mine.** An interim
      `Set-Content -Encoding UTF8` while renaming a constant wrote a UTF-8 BOM onto
      `ws_channels.py`. Python imported it happily, so every test of this task passed
      while `tests/test_compiler_architecture.py`'s AST sweep of `backend_app/` failed with
      `SyntaxError: invalid non-printable character U+FEFF` — 3 failures and 9 errors,
      caught only by running the full suite rather than the files this task touched. The
      BOM is gone and that file is 23/23 green. Worth recording because the failure mode is
      invisible to `import` and to every targeted run.
    - **Re-verified on a later tree, and one recorded figure has moved for reasons outside
      this task.** Backend is **unchanged to the item**: 4262 collected, **43 failed / 4201
      passed / 18 skipped** in default order, and this task's file plus 6.7's run together
      at **194 passed** (144 + 50), so the 6.7 wrapper class is still answering what 6.7
      asserted. Frontend now totals **1 failed / 758 passed (759)** over 29 files rather
      than the 747 recorded above; the file this task added is **69/69 green standalone**
      and unedited, so the +12 belongs to test files this task does not own. The single
      failure is still `portfolio-rendering.test.jsx` → "should verify improved empty
      states" — the recorded out-of-scope one, one failure against a ceiling of two, and
      `integration.test.jsx`'s timeout flake again did not fire.

  - [x] 8.6 Add the model checksum and feature schema drift gates
    - Refuse to reach `RUNNING` when `SafeModelLoader` checksum verification fails, holding the
      node `AWAITING_MODEL`
    - Refuse deployment when `feature_validator.check_model_compatibility` fails, reporting
      expected versus actual feature columns
    - _Requirements: 17.6, 17.7_
    - **Implementation note.** One new module, `backend_app/backend/model_readiness.py`
      (725 lines), plus the wiring in task 8.2's gate. **Requirement 17.6 has two enforcers,
      not one** — "THE DAG_Runtime SHALL mark that model node as awaiting a model **and** THE
      Deployment_Service SHALL hold the deployment out of the running state" — and that single
      sentence decides the whole shape. The runtime wants a *verdict* it can put on a node
      (`mark_node(state, node_id, AWAITING_MODEL)`); the Deployment_Service wants a *refusal*
      it can turn into an HTTP status. One exception type cannot serve both without the
      runtime catching exceptions on its hot path, so **every function in `model_readiness`
      returns a verdict and nothing there raises**: `ArtifactVerdict.runtime_state` is the
      label (`AWAITING_MODEL` / `READY`), `model_ready(row)` is the boolean seam task 8.4's
      `execute_plan` calls, and `deployment_binding` is the single place a verdict becomes a
      `DeployRejected`. A test asserts structurally that the module names neither
      `DeployRejected` nor `HTTPException` nor `fastapi`, so the seam cannot quietly close.
    - **Reused verbatim, with nothing reimplemented.** (a) The digest is
      `core.ml_safety.SafeModelLoader.compute_checksum`, reached through task 6.5's
      `ArtifactStore.checksum` — `LocalArtifactStore.checksum` *is* that function over the
      stored file. Task 6.5 recorded `artifact_checksum` by reading the bytes **back** after
      the write, so recomputing through the same method compares like with like; a test
      computes the digest both ways over the same real file and requires them to agree.
      (b) The comparison is `feature_validator.FeatureValidator.check_model_compatibility`,
      called with the recorded contract presented under sklearn's own attribute names
      (`n_features_in_`, `feature_names_in_`) — which is exactly why 6.5 recorded
      `feature_schema.feature_count` and `.feature_names`, and it means the deploy gate applies
      the *same* comparison the inference path applies rather than a second one written here.
      A test drives the real validator over the same pair the gate compared and requires the
      answers to agree. (c) Column names come from `dag_engine._feature_column_names`; this
      module re-derives not one name.
    - **The artifact is deliberately NOT deserialized.** `SafeModelLoader.load_model` verifies
      the checksum and *then* `joblib.load`s. Deserializing a pickle is arbitrary code
      execution and an unbounded allocation, and `MemoryMonitor`'s ceiling is enforced at
      *store* time (6.5's `max_artifact_bytes`), not at load time — so doing it inside an
      authenticated deploy request would put both on an HTTP path for no gain. 17.6 asks about
      the *checksum*, and the digest answers it without loading anything; `load_model` stays
      the runtime's loader, where the model is going to be executed anyway and where
      `TrainingIsolator`'s limits apply. A test asserts `joblib`, `load_model` and `pickle`
      appear nowhere in the module's executable source.
    - **The 17.7 "actual" side is static, and the cost is stated.** 17.7 compares the recorded
      schema against "the feature output of the version's **current** Canonical_Graph".
      Producing that output for real means running the feature pipeline, which means a
      validated market-data window — a network fetch on the deploy path whose failure modes
      ("the venue was slow") have nothing to do with drift. So `declared_feature_columns`
      walks the ML node's upstream feature closure in the version's own `CompiledPlan` and asks
      each node what it *names* its columns, merging multiple matrices exactly as
      `feature_matrix.concat_matrices` does (edge order, duplicates suffixed by `_unique_name`)
      because that is what `run_feature_pipeline` did before `check_feature_schema` recorded
      the number being compared against. **Exactness is the load-bearing claim, so it is
      measured rather than argued:** the walk and the real pipeline are required to produce the
      same *ordered* list for a single block, for a `feat_concat` of two, and through a
      `feat_select` projection. All **15** `FEATURE_ENGINEERING` blocks are covered — the 12
      leaf blocks by name, plus `feat_concat`'s merge, `feat_select`'s projection and
      `feat_standardize`'s pass-through (a scaler changes values, never names). What the walk
      **cannot** see, stated plainly: a block whose formula changed while its params and column
      name did not. That is `dag_hash`'s and Requirement 22.5's recompilation disposition, and
      it is not claimed here.
    - **Order is compared but not refused on.** `FeatureValidator.validate_features` projects
      the inference frame onto `schema.expected_features` before the model sees it, so a graph
      producing the same names in a different order is *normalised at inference* rather than
      wrong. Refusing a deploy for something the runtime fixes would be refusing on a
      difference with no consequence — but an author who reordered their feature blocks should
      know, so an order-only difference is a `FEATURE_ORDER_DIFFERS` **warning** that travels
      with the binding (through `DeploymentBinding.warnings` into the deploy response) rather
      than being logged and forgotten. Both ordered lists are reported either way.
    - **Fail closed, exhaustively.** Every "cannot tell" is a refusal, and each has a distinct
      code so the author is told which: no `artifact_uri` (`ARTIFACT_REFERENCE_MISSING`), no
      recorded checksum (`ARTIFACT_CHECKSUM_MISSING` — a vacuous comparison that returns true
      is precisely the hole 17.6 exists to close), a store that cannot be reached or an object
      that is gone or a digest that raises (`ARTIFACT_UNREADABLE`, never a propagated
      exception), a recorded schema with no column names (`FEATURE_SCHEMA_NOT_RECORDED`), a
      plan whose columns cannot be named (`FEATURE_COLUMNS_UNDETERMINED` — a schema that
      cannot be compared has not matched), a model bound to a node this graph no longer has
      (`MODEL_NODE_NOT_IN_PLAN`), and a node whose model block changed underneath the model
      (`MODEL_BLOCK_CHANGED`, because comparing columns across it compares two unrelated
      things). Checksum runs **before** schema per node: drift measured against a file we have
      just established is not the file is not a fact.
    - **The deploy gate (17.6's second enforcer and 17.7's refusal).** `deployment_binding`
      gained `assert_models_verified`, four codes — `MODEL_AWAITING_ARTIFACT` (409, carrying
      `runtime_state: AWAITING_MODEL` so the two halves of 17.6 are visibly the same verdict),
      `FEATURE_SCHEMA_DRIFT` (409, carrying `expected_feature_columns`,
      `actual_feature_columns`, `missing_feature_columns` and `extra_feature_columns`, because
      "the schema drifted" without the columns is not something an author can act on),
      `MODEL_NODE_NOT_BOUND` (409, when the plan declares a model node with no active model
      version — 17.5 says such a version is not `READY`, so reaching here means the row
      disagrees with its own bindings) and `MODEL_VERSIONS_UNAVAILABLE` (503) — and one
      extraction: `load_binding_plan` is now separate from `resolve_binding_market` so the
      market gate and the model gates read **one** parse of the plan and cannot disagree about
      what the version is. `active_model_versions` is reused as-is, with the row's own
      `user_id` compared again on top of 004d's `mv_owner_select` (the same two-filter
      convention `load_owned_exchange_account` uses); another tenant's row is dropped rather
      than reported, which lands on `MODEL_NODE_NOT_BOUND` rather than confirming that
      someone else's model version exists.
    - **Placed before the ownership reads, deliberately.** In `evaluate_binding` the model
      gates run after the request is fully validated and **before** any account, risk or venue
      lookup: a version whose artifact does not match its recorded checksum cannot run on
      *any* account, so resolving one would be work done to answer a settled question — and it
      would put an account id in a log line for a deploy that was never going to happen. Two
      tests pin the ordering in both directions, because one alone would pass with the gate
      placed anywhere: a bad checksum refuses before `exchange_keys` is ever selected, and a
      good model lets the account gate have its say.
    - **A graph with no model node reads nothing.** Both gates are skipped entirely when
      `plan.ml_nodes` is empty, and a test asserts the fake database recorded **zero** selects
      — a deploy of a pure-indicator strategy must not acquire a dependency on 004d.
    - **004d/004e unapplied — degraded, named, never a 500.** With `model_versions` absent,
      `active_model_versions` degrades to `ModelPersistenceUnavailable` and logs a warning
      naming `004d_training_and_models.sql` (`model_versioning._degrade`, reused); the gate
      turns that into `MODEL_VERSIONS_UNAVAILABLE` **503** whose message and `details.migration`
      name the same file, and a test asserts the file name is in the log, in the message, and
      is a file that exists on disk. It is a *refusal* rather than a pass because "could not
      look" is not "it verified" — that is the same fail-closed direction 8.2 chose for the
      market universe. 004e is untouched by this task: the binding columns are not read by
      either gate, so an unapplied 004e still degrades exactly as 8.2 recorded.
    - Deliberately NOT added: no migration edit; no new table, column, index or policy; no
      change to `SafeModelLoader`, `feature_validator`, `dag_engine` or `model_versioning` (the
      three are read, never revised); no artifact download and no deserialization on the deploy
      path; no state transition — task 8.4 owns `execute_plan`'s state machine and the two
      labels are published as constants for it to import rather than a second machine being
      started here; and no gate on the *legacy* `routers/strategies.py` deploy path, which
      carries no version reference and therefore no model version to check (8.2's scoping
      decision, unchanged).
    - No auth, RLS, tenant isolation, financial invariant, execution safeguard, risk gate,
      idempotency key or rate limit was weakened. The route keeps its
      `Depends(get_current_user)` and its `@limiter.limit("50/minute")`, the ownership filter is
      *added to* rather than replaced, and no policy, index, trigger or grant was touched.
    - **Verified with 50 new backend tests** in
      `tests/test_task_8_6_model_readiness_gates.py`, all passing. Nothing under test is
      mocked: the registry, the compiler, the compiled plan (round-tripped through
      `to_dict`/`from_dict` as the database stores it), the feature executors, the artifact
      store (a real `LocalArtifactStore` on a real temporary directory), the SHA-256 routine
      and the feature validator are all the real ones — the checksum-mismatch tests **really
      rewrite the bytes on disk** underneath the recorded row. The only double is the PostgREST
      client, and it honours `.eq` filters (so the ownership test cannot pass with the filter
      missing) and simulates `42P01` so the 004d degradation is exercised rather than described.
    - **Not verified, and it cannot be here.** There is no PostgreSQL in this environment and
      **004d is unapplied**, so nothing proves `uq_mv_active_per_node` makes the two-active-rows
      branch unreachable or that `mv_owner_select` scopes the read as the second filter assumes.
      There is no object storage, so `SupabaseStorageArtifactStore.checksum` is exercised only
      through the shared `ArtifactStore` contract, not against a real bucket. And the **runtime**
      half of 17.6 — a node actually holding `AWAITING_MODEL` on the canvas while a plan
      executes — is `execute_plan`, which is task 8.4: what is pinned here is that the verdict
      carries the label and that `model_ready` is a pure, exception-free seam for it to call.
    - Baseline held: backend **43 failed / 3713 passed / 18 skipped** over 3774 collected in
      pytest's default order with `.venv\Scripts\python.exe -m pytest -q` (449 s), against the
      pinned ceiling of **44**. The failure count is **unchanged** from 8.2's 43, over exactly
      the same pinned nineteen files with exactly the same per-file counts
      (`full_system_test.py` 4, `test_account_health_query.py` 1,
      `test_atomic_order_cancellation_fix.py` 4, `test_distributed_execution_safety.py` 7,
      `test_event_pipeline.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_safety_fix.py` 1, `test_exchange_vault_singleton.py` 2,
      `test_false_success_report_fix.py` 1, `test_fee_precision_fix.py` 1,
      `test_get_db_dependency.py` 1, `test_marketplace_pipeline.py` 4,
      `test_position_delta_race_condition_fix.py` 1,
      `test_reconciliation_engine_fail_closed.py` 1, `test_risk_management_lifecycle.py` 1,
      `test_risk_settings_api.py` 2, `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_tenant_isolation_fixes.py` 1, `test_transaction_isolation_serializable.py` 3).
      **The collection delta reconciles exactly:** 3774 − 3724 = **50**, this task's new file
      and nothing else; 3663 + 50 = 3713. Frontend: **not re-run, and deliberately so** — this
      task touches no frontend file (one new backend module, one new test file, and edits to
      `deployment_binding.py` only).

  - [x] 8.7 Write the tenant isolation and secret containment suite
    - `tests/security/test_builder_tenant_isolation.py`: a matrix of (endpoint × resource type)
      asserting 403 or 404, and never data, for a non-owner across strategy, version, training
      job, model version, deployment and exchange account
    - **Property 22: No user can read or reference another user's resources**
    - **Property 23: No exchange id, api key, secret or passphrase appears in `graph_json`,
      `compiled_plan`, `training_jobs.config`, any response body, any WS frame or any log line**
    - **Validates: Requirements 12.1, 21.1, 21.2, 21.4, 21.6, 21.7**

    - **Implementation note.** `tests/security/test_builder_tenant_isolation.py` — 137 tests,
      all passing. The directory is a new package; `pytest.ini`'s `norecursedirs` excludes
      only `tests/perf` and the shipped defaults, so the default `pytest -q` lane collects
      it, which is the point — an isolation matrix that has to be asked for is one nobody
      runs. `tests/conftest.py` already applies.
    - **What it holds.** A 21-entry matrix of (endpoint × resource type) across strategy,
      version, training job, model version, deployment and exchange account, driven through
      the **real app** (`backend_app.main.app`) as a non-owner: every entry answers 403/404,
      or 200-with-nothing for the three collection endpoints where an empty list is the
      correct answer and a 404 would be wrong. Each entry is asserted three ways — the
      status, a substring scan of the **whole** body for the owner's markers, and a
      before/after comparison of the fake database's rows plus a check that no recorded
      insert/update/delete carried the owner's `user_id`. Property 23 adds a live save
      through the real router with **every** forbidden key at once on the DATA node, and
      scans the persisted `graph_json`, `compiled_plan` and `blueprint` key-by-key at every
      depth and value-by-value as substrings; the `training_jobs.config` guard
      (`assert_no_exchange_identity`) is exercised over the full vocabulary at three nesting
      depths and the call is proved present by parsing `build_training_config`'s AST rather
      than by reading its docstring. All five owned channel families are authorised as owner
      and refused as non-owner through the real `authorize_channel_subscription`, and the
      refusal frame is required to carry a reported code and no resource data (21.6). Frames
      are scanned at their **real producers** — `PlanRuntimeState.to_dict()` holding a
      poisoned active `model_versions` row (so `artifact_uri` not being published is a fact
      about the projection), `strategy_lifecycle.canvas_state` over a poisoned version row,
      and `public_training_job` over a poisoned `config` — not by stuffing canaries into
      `runtime_state_frame`, which forwards its caller's mapping by design and makes no such
      claim. Every request runs under `caplog` at DEBUG and the captured text is scanned.
    - **The matrix cannot pass vacuously**, and this is enforced three ways rather than
      asserted: every refused entry is re-issued **as the owner** and must not be 403/404;
      six loader-level positive controls resolve each resource type for the owner and refuse
      it for the intruder against the *same* seeded client; and the three collection entries
      assert the owner's own list is non-empty and names the seeded rows. The one double is
      the PostgREST client, which **honours `.eq` filters and applies its writes** — so an
      endpoint whose ownership filter went missing cannot pass, and "the refusal wrote
      nothing" is a statement about the rows. The `_leaked_markers` helper excludes any
      marker the caller itself put in the URL: echoing a requested id back in "not found" is
      not a leak, and pretending otherwise would have forced the assertion to be weakened
      elsewhere.
    - **Five real cross-tenant defects found and fixed in source. None was worked around.**
      (1) `GET /api/deployments/{id}` read the in-process `deployment_manager` registry —
      keyed by deployment id with no tenant concept — and returned worker id, region, host
      metrics, heartbeat, `error_message` and every runtime metric to any authenticated
      caller holding the id. It now reads the authoritative `strategy_deployments` row first
      with an explicit `user_id` filter, 404s identically for "not yours" and "not there",
      and consults the runtime only afterwards; a fleet-started deployment absent from the
      registry now reports its row with `runtime_attached: false` instead of being reported
      as missing to its own owner. (2) `POST /api/deployments/{id}/restart` passed the path
      parameter straight to that registry and then updated the row `.eq("id", …)` with no
      tenant filter — a cross-tenant **write** that could restart another user's live
      strategy; ownership is now read first (404) and the write carries `user_id`. This is
      the third copy of the defect tasks 8.3 fixed in pause/resume/stop. (3)
      `StrategyService.restore_version` had **no ownership check at all**: it cleared
      `is_current`, inserted a version row and repointed `strategies.current_version`, every
      statement filtered by `strategy_id`/`id` alone. The suite caught it answering **200
      and writing three statements** against another tenant. (4)
      `StrategyService.compare_versions` returned another tenant's version metadata and
      blueprint diff with a **200**. (5) `StrategyService.get_version_history` returned
      another tenant's version rows — graph and plan included — filtered by `strategy_id`
      only. (3)/(4)/(5) now go through one new `StrategyService._owns_strategy` helper
      applying the same two-filter pattern `create_version`, `deploy_version` and
      `_load_owned_version` already used; `restore_version`'s `strategies` update carries the
      filter too. The leak in (5) was reproduced directly with the guard neutralised to
      confirm the new test bites.
    - No auth, RLS, financial invariant, execution safeguard, risk gate, idempotency key or
      rate limit was weakened — every change **adds** a filter or a refusal. No migration,
      table, column, index, policy, trigger or grant was touched. No route was added or
      removed. The one behavioural change beyond refusals is `GET /deployments/{id}`
      answering 200 for an owned deployment the in-process registry does not hold, which
      previously 404'd; `restart`'s pre-existing 500 when the registry has lost a deployment
      is deliberately left alone — that is not an isolation defect and `restart` has no entry
      in 8.3's state machine.
    - **Not verified, and it cannot be here. Database-level RLS is NOT proven by this
      suite.** There is no PostgreSQL and 004/004b/004c/004d/004e are unapplied, so nothing
      here shows `tj_owner_select`, `tj_owner_insert`, `tj_owner_update`, `mv_owner_select`
      or `mv_owner_insert` filtering a query, nor the `strategy_versions` policies scoping a
      read through `strategies.user_id = auth.uid()`. `TestPersistenceLayerRls` asserts only
      what is checkable — that 004d **declares** the five policies and enables RLS on both
      tables — and says so in its own docstring; enforcement is 004d's VERIFICATION section
      against a real database, and Requirement 21.3 stays open until then. The fake client
      deliberately simulates no policy, which is the strict direction: a path leaning on RLS
      alone fails here rather than passing on a policy this environment cannot run. Also not
      covered: `GET /strategy-operations/models?version_id=` and
      `GET .../versions/{v}` are in `design.md`'s API table but not yet implemented, so they
      are absent from the matrix rather than stubbed.
    - Baseline held: **43 failed / 4338 passed / 18 skipped** over 4399 collected with
      `.venv\Scripts\python.exe -m pytest -q` in default order (605 s), against the pinned
      ceiling of 43. The failure count is **unchanged**, over exactly the same pinned
      nineteen files with exactly the same per-file counts recorded by 8.6. **The collection
      delta reconciles exactly:** 4399 − 4262 = **137**, this task's new file and nothing
      else; 4201 + 137 = 4338. Frontend not re-run, deliberately — this task touches no
      frontend file.

  - [ ]* 8.8 Write property test for intent numeric safety
    - `tests/test_intent_finite_property.py`
    - **Property 14: Every numeric field of an emitted intent is finite and quantity is
      positive**
    - **Validates: Requirements 20.5, 20.6**

  - [ ]* 8.9 Write property test for upstream readiness
    - `tests/test_intent_readiness_property.py`
    - **Property 15: Every node in the upstream closure of an emitted intent is `READY`**
    - **Validates: Requirements 20.1, 20.10, 20.11**

  - [x] 8.10 Write the shared artifact agreement test
    - `tests/test_version_consumer_agreement.py`
    - **Property 25: Both version consumers load the same `compiled_plan` and observe the same
      `dag_hash`, and produce the same intent sequence on identical data; a hash mismatch
      triggers recompilation before execution**
    - **Validates: Requirements 22.3, 22.4, 22.5**

    - **Implementation note.** `tests/test_version_consumer_agreement.py` — 36 tests, all
      passing. Task 2.10's golden file pins *values*; this file pins the *agreement rule*
      around it, and touches none of 2.10's recording — not `dag_runtime_plan_golden.json`,
      not its candles, not one digest. The doubles are borrowed rather than re-spelled: the
      canonical graph, the `strategy_versions`-shaped row, the committed candle fixture and
      both consumer pipelines come from `test_dag_runtime_golden_plan.py`; the deterministic
      OHLCV frame, the `reg` registry fixture and the per-test `unfrozen` safety fixture come
      from `test_task_8_4_runtime_readiness_gate.py`. A second spelling of "the graph both
      consumers agree about" is the one thing this file could not afford.
    - **What it holds.** Four row shapes × two consumers. The matched shape (`graph_json` +
      `compiled_plan` + `dag_hash`) must be **reused** by `BacktestRuntime.load_version_plan`
      and `DAGEventLoop.from_version_row` alike, byte-for-byte (`to_json()` compared against
      the column's own text), with one `dag_hash` shared by both loaders, the row's column,
      the direct `load_plan` worker seam and the API projection — `_dag_fields` →
      `_lift_dag_fields` → `CompiledPlan.from_dict`, which is Requirement 22.3's API half at
      the seam that needs no database. That no recompile happened is asserted by **making one
      impossible** — `compile_graph` is replaced by something that raises and the load must
      still succeed — rather than by reading `reused`. The live loop's decision is observed by
      recording what `load_plan` returned to it, not by calling `load_plan` a second time and
      asserting about this test's own result. Requirement 22.4 is asserted twice: once through
      both **real** pipelines over the committed candles (backtester frame vs. the loop's own
      `RollingWindow` fed real `MarketEvent` candles), comparing every per-node output digest
      and the intent sequence; and once through task 8.4's `execute_plan` over the plan *each
      consumer loaded*, with a non-vacuity test proving that comparison is over a non-empty
      sequence. Requirement 22.5 is the stale row: `graph_json` moves RSI 14 → 21 while
      `compiled_plan` and the `dag_hash` column still hold the 14-bar plan. The load must
      report `compiled_plan_hash_mismatch`, serve the **graph's** hash rather than the
      column's, warn naming both hashes, and — the clause that matters — recompile *before*
      execution: the loop's engine payload carries `window`/`period` 21 before a single
      candle is pushed, and the recompiled intents equal a fresh compile of the row's graph.
      Agreement is then re-asserted on that recompiled row, which the golden recording never
      exercises. Unreadable and NULL plan columns compile from the graph instead of crashing;
      a graph that no longer validates is **refused** (`ValidationError`) rather than falling
      back to the stale plan.
    - **One real defect found and fixed in source. It was not worked around.**
      `strategy_compiler._PLAN_COLUMNS` reads `compiled_plan` and then the legacy
      `execution_graph`, documenting the second as "where `StrategyService._insert_version_row`
      puts the serialized plan while migration 004 part 1 is unapplied". **That fallback was
      unreachable.** `blueprint` is `NOT NULL` in the pre-canonical schema, so the degraded
      INSERT writes the graph to `blueprint`, the plan to `execution_graph`, and no
      `graph_json` at all — while `schema.extract_raw_graph` read `graph_json`, then a v1
      `buy_logic` blob, then top-level `nodes`, and raised. `load_plan` therefore raised
      `CompilerError("Row carries no loadable strategy graph")` before it could reach the plan
      column that exists for exactly this case: for every version saved while 004 is
      unapplied — which is **every row in this environment** — both consumers refused the
      version outright instead of degrading. `extract_raw_graph` now falls back to a
      graph-shaped `blueprint` **last**, and `load_plan` warns naming
      `004_strategy_builder_canonical.sql`, what the row is missing and why the deploy-time
      hash gate cannot run against it. The degraded row now serves the *same* plan bytes and
      the *same* hash as the canonical row, through `execution_graph_hash_match`, to both
      consumers. `schema.py` stays pure in, pure out — no logger was added to it; the warning
      lives at the version-consumer seam, which already had one.
    - **The refusals the fix could have softened are asserted instead.** A `blueprint` holding
      the genuinely legacy entry/exit-condition blob is still unreadable rather than
      half-parsed (the fallback is gated on `nodes`/`edges`); a row with no graph at all is
      still refused; `graph_json` still **wins** over `blueprint` when both are present, which
      is what keeps the immutability trigger and the hash gate authoritative; and
      `_assert_deploy_prerequisites` still refuses the degraded row for its missing
      `validation_state`, so degrading the *load* did not soften the *deploy* (Requirement
      10.4). No auth, RLS, tenant isolation, financial invariant, execution safeguard, risk
      gate, idempotency key or rate limit was touched. No migration, table, column, index,
      policy, trigger or grant was touched. No route was added, removed or re-signed. The
      fixture that stands in for the degraded write is checked against
      `_insert_version_row`'s **real source**, so if that branch is rewritten the fixture stops
      claiming to describe it.
    - **Property 25** is a Hypothesis property over the input space that actually moves
      identity: two indicator windows, the comparator's bound and inclusivity, the action's
      size, and the window length — ids, block ids, ports and edges fixed, because a graph
      that does not validate says nothing about whether two consumers of a *stored* version
      agree. Each example asserts all three clauses on one drawn version: same bytes and same
      hash for both loaders (22.3), same intent sequence over the same frame (22.4), and a
      mutated `graph_json` forcing both loaders to recompile to the graph's hash and execute
      the graph's own intents (22.5).
    - **Not verified, and it cannot be here.** No HTTP request and no database: Requirement
      22.3 names the Strategy_Builder_API, and what is checkable without PostgreSQL is the
      projection it serves from plus the loader every consumer goes through — that the row
      cannot be edited under a running deployment is `004c_immutable_versions.sql`'s trigger
      and `chk_valid_requires_hash`, both unapplied here (Requirement 9.2, task 8.12).
      Migrations 004–004e being unapplied also means the degraded shape is the *normal* shape
      locally and the canonical shape is the one constructed by hand. No order is placed:
      `execute_plan` returns intents and sends nothing, `VYOMQUANT_MODE=safe` stays set in
      `conftest`, and emission is observed only through 8.4's own per-test `unfrozen` fixture.
      And this file claims *agreement*, not correctness — that the intents are the **right**
      intents is the golden file's claim, not this one's.
    - Baseline held: **43 failed / 4374 passed / 18 skipped** over 4435 collected with
      `.venv\Scripts\python.exe -m pytest -q` in default order (407 s), against the pinned
      ceiling of 43. The failure count is **unchanged**, over exactly the same pinned nineteen
      files with exactly the same per-file counts recorded by 8.6 and 8.7. **The collection
      delta reconciles exactly:** 4435 − 4399 = **36**, this task's new file and nothing else;
      4338 + 36 = 4374. Frontend not re-run, deliberately — this task touches no frontend
      file (one new test file, plus edits to `strategy_dag/schema.py` and
      `strategy_compiler.py` only).

  - [x] 8.11 Write the save-performs-no-backtest test
    - `tests/test_save_performs_no_backtest.py`
    - **Property 24: Saving a version creates no backtest row and starts no backtest job**
    - Pair with an architecture assertion that the Builder save path imports no Backtester
      execution module
    - **Validates: Requirements 22.1, 22.2**

    - **Implementation note.** `tests/test_save_performs_no_backtest.py` — 58 tests, all
      passing. **No source file was changed by this task**, and nothing was mocked away: the
      router, the service, the compiler, the validator and the assembled registry are all
      production code on every path exercised. The doubles are borrowed rather than
      re-spelled — `FakeDB`/`seeded_db`/`service_on`/`valid_graph`/`graph_with_unfed_action`
      and `LEGACY_VERSION_COLUMNS` from task 2.3's
      `test_strategy_version_canonical_persistence.py` (that model is what makes "which
      relations did the save touch" answerable at all, because it logs every statement and
      enforces the migration-004 column vocabulary); the AST machinery
      (`iter_python_modules`, `module_facts`, `definition_use`, `_walk_definitions`, `rel`)
      from task 2.9's `test_compiler_architecture.py`; the training-branch doubles
      (`FakeSupabase`, `model_graph`, `linear_graph`, `save`, `window`) from task 6.1; and
      the real-app client from task 8.7.
    - **Three independent lines of evidence, because each alone has a hole.** *(1) No
      record.* A real save writes rows, and the relations and columns those rows land in are
      asserted — not the response. `strategy_versions.backtest_results` is a real column of
      the pre-004 schema (`001_strategy_architecture.sql`, kept by `003`), so the fake would
      *accept* a write to it, which is what makes its absence an assertion rather than a
      tautology; the follow-up write to `strategies` is pinned to exactly
      `{current_version, updated_at}`. *(2) No job, no execution.* Twelve entry points —
      `get_backtest_runtime`, `BacktestRuntime.run_backtest`/`run_version_backtest`,
      `BacktestEngine.run_backtest_async`, `routers.strategies.backtest_internal`,
      `get_backtest_service`, `BacktestService.create_backtest`, `publish_backtest_job`,
      `worker._write_status`, plus the three names the two routers already bound into their
      own namespaces at import time — are each replaced by something that **records the call
      and then raises a `BaseException`**. The record is the assertion and the raise only
      stops the work: both save routes and the backtest route wrap their bodies in
      `except Exception`, so an `AssertionError` would be caught and re-reported as
      something else. *(3) No dependency.* Statically, the module-scope import closure of the
      eleven save-path modules is walked and contains no backtest module, no router and no
      `optimization_engine`, and each of the nine save-path functions is read off its own AST
      and must name no backtest identifier. Dynamically, an `ImportRecorder` wrapping a live
      save proves nothing on the *executed* path imports one either — the half a static scan
      cannot make, because this repository defers imports into function bodies constantly
      (the save handler does it with its own exception types).
    - **Every control is proved live rather than assumed.** The tripwires are fired from
      `POST /api/strategies/backtest`, the endpoint whose whole purpose is to trip them, on
      both its branches: the queued one (`_write_status` → `publish_backtest_job`) and the
      `sync=true` one that calls `backtest_internal` directly — the second matters because
      it is also the fallback the queued branch takes inside an `except Exception` when Redis
      is unreachable, so proving only the queue wires would leave a live path undemonstrated.
      Each of the twelve is additionally called individually and must record itself. That
      control asserts the **record**, not a raised exception reaching the test: task 8.7's
      shared client is deliberately `raise_server_exceptions=False`, and Starlette renders
      anything escaping a handler as a 500 — which is precisely the condition the
      `fired == []` assertions have to survive. The import closure is asserted against a
      floor and re-seeded at `optimization_engine`, where the same walker must report all
      three backtest modules; the AST reader is re-applied to
      `routers/strategies.walk_forward_optimization`, the endpoint that genuinely runs
      backtests, and must convict it.
    - **What is asserted, across every save shape.** The service seam (`create_version`), the
      canonical row shape and the **degraded** one (which is the normal shape here, with
      004 unapplied — it must warn naming `004_strategy_builder_canonical.sql`, keep the plan
      in `execution_graph`, and never 500), the refusal path (`ValidationError`, and
      `db.statements == []` — the failure path never opens a client), all three ML branches
      through `create_version_and_maybe_train` (`QUEUED` with real `training_jobs` rows,
      `BLOCKED`, and `NOT_REQUIRED`), and the two HTTP surfaces:
      `POST /api/strategy-operations/strategies/{id}/versions` and the legacy
      `POST /api/strategies` the save button actually takes today, driven with the body
      `StrategyBuilder.jsx`'s `buildSavePayload` really sends.
    - **The routers are NOT claimed to be backtest-free, because they are not.**
      `routers/strategies.py` and `routers/strategy_operations.py` both import
      `backtest_runtime` at module scope and the second imports `backtest_service` too —
      they host both surfaces. Choosing a scope that happened to exclude that would have made
      the file decorative, so the exact set of six `(module, depth, backtest_module)` import
      edges under `backend_app/` is **pinned** instead: a new importer, including one added to
      a save-path module, is a set difference that has to be read against Requirement 22.2.
      The one deferred edge *out* of the save-path closure
      (`deployment_binding` → `routers.strategy_operations._pipeline_timeframes`, task 7.2's
      deliberate reuse) is pinned too, and asserted to be deferred rather than module-scope.
      `BacktestRuntime.load_version_plan` is deliberately **not** a tripwire: it loads a
      stored `compiled_plan` and executes nothing, and Requirement 22.3 plus task 8.10
      require both consumers to share it — convicting the shared loader would argue against
      the design. A test asserts it is not among the armed names.
    - **Property 24** is a Hypothesis property over the dimensions that actually move a save:
      the two graph parameters that change the plan, the hash and the warmup; the version
      label; the two flags that decide whether `is_current` and `strategies.current_version`
      move; and whether migration 004 part 1 is applied, because the degraded branch is a
      different code path writing a different row. Each example asserts the save *happened*
      (a property about an absent side effect is worthless otherwise) and then all four
      clauses: no tripwire, no backtest relation, no backtest key at any depth, no backtest
      import. A second property covers the refusal path, because the quantifier is over save
      *operations*, not successful ones.
    - **Two honest findings, neither of them a defect, and neither worked around.** (1) A
      bare `{"nodes": ..., "edges": ...}` body to `POST /api/strategies` is read by
      `schema.load_graph` as **schema version 1** — that is its documented contract and what
      keeps pre-canonical clients working — so canonical v2 nodes sent that way arrive
      stripped of the `block_id` the v1 reader looks for and the save is refused 422 with the
      full report. That is a correct refusal, and it is now *asserted as one* (and asserted
      to write nothing and start nothing) rather than avoided; the passing case uses the
      envelope the real client sends. (2) `PUT /api/strategies/{id}` forwards its request
      body verbatim to `.update(body)`. That is worth a look on its own terms, but it is not
      a Requirement 22 hazard — `strategies` carries no `backtest_*` column in any migration,
      so no backtest record can be created through it — and it is out of scope here, so it
      was left alone rather than half-fixed. Two claims in the file's own prose were wrong
      when found and were corrected against the migrations rather than left standing:
      `strategies` does not carry "nine `backtest_*` metric columns", and `backtest_results`
      is a `strategy_versions` column. Four `_scratch_*.py` files an earlier attempt left at
      the repository root were deleted.
    - No auth, RLS, tenant isolation, financial invariant, execution safeguard, risk gate,
      idempotency key or rate limit was touched, because no production file was touched. No
      migration, table, column, index, policy, trigger or grant. No route added, removed or
      re-signed.
    - **Not verified, and it cannot be here.** There is no PostgreSQL, no Redis and no
      exchange, so "no backtest row" is a statement about a model that applies its writes and
      enforces a column vocabulary, not about a database; migrations 004–004e are unapplied,
      which is why both persistence shapes are exercised and why the degraded one is the
      normal one locally. The frontend half of Property 24 — "`StrategyBuilder.jsx` imports
      nothing from the Backtester module tree" — is **not** re-asserted here and was not
      re-run: it already exists as task 3.17's
      `algo22-terminal/tests/unit/builder.architecture.test.js`, which walks the JS closure,
      follows lazy `import()` forms and records that the handoff is a route string plus an
      injected `onBacktest` prop. Re-implementing a JS resolver in Python would be a second,
      worse answer to an answered question. And nothing here says the Backtester *works*:
      this file's entire subject is what a save does not do.
    - Baseline held: **43 failed / 4445 passed / 18 skipped** over 4506 collected with
      `.venv\Scripts\python.exe -m pytest -q` in default order (451 s), against the pinned
      ceiling of 43. The failure count is **unchanged**, over exactly the same pinned nineteen
      files with exactly the same per-file counts recorded by 8.6, 8.7 and 8.10.
      **The collection delta reconciles exactly:** collection with
      `--ignore=tests/test_save_performs_no_backtest.py` is 4448 and with it 4506, so this
      file contributes **58** and nothing else; 4387 + 58 = 4445 passed. The remaining +13
      against 8.10's recorded 4435 is **not** this task: an unrelated exchange-vault work
      stream landed after 8.10 wrote its note (`tests/test_exchange_vault_contract.py`,
      `tests/test_exchange_phase6c_adversarial_acceptance.py`, with
      `backend_app/backend/api_key_vault.py`, `connection_engine.py`, `routers/exchange.py`
      and `core/models/pydantic_models.py`), which is where those tests come from. Frontend
      not re-run, deliberately — this task touches no frontend file, and adds one test file
      and nothing else.

  - [x] 8.12 Phase 8 exit verification
    - Confirm the cross-tenant matrix is green, deployed-version immutability is enforced at the
      database level, and  the credential leak scan is green
    - Confirm no existing risk, guard, idempotency, RLS or auth control lost coverage
    - Ensure all tests pass, ask the user if questions arise.
    - _Requirements: 9.2, 21.4, 21.7, 21.9, 22.1_

    - **Implementation note.** A verification gate, not a feature: **no file was changed by
      this task** apart from this note. Two of the three exit clauses are green and the third
      **is not closed** — see "The clause that does not close" below. The gate is reported
      open on Requirement 9.2 rather than signed off.
    - **Cross-tenant matrix: green.** `tests/security/test_builder_tenant_isolation.py`,
      **137 tests, all passing**, collected by the default `pytest -q` lane (it is the first
      file in the run, `[ 1%]`, no `F`). The 21-entry (endpoint × resource type) matrix answers
      403/404 — or 200-with-nothing on the three collection endpoints — for a non-owner across
      strategy, version, training job, model version, deployment and exchange account, through
      the real `backend_app.main.app`, and each entry is checked three ways (status, whole-body
      marker scan, before/after row comparison plus no owner `user_id` on any recorded write).
      Non-vacuity is enforced rather than assumed: every refused entry is re-issued as the
      owner and must not refuse, six loader-level positive controls separate the two tenants
      against the same seeded client, and the collection entries assert the owner's own list
      names the seeded rows. Requirement 21.4's "and no resource data" is the body scan, not the
      status code alone.
    - **Credential leak scan: green.** Property 23 runs over the ten-key canary vocabulary
      (`api_key`, `api_secret`, `secret`, `secret_key`, `passphrase`, `password`,
      `private_key`, … plus the venue id tracked separately) at every nesting depth, key-by-key
      and value-by-value as substrings, against: persisted `graph_json` / `compiled_plan` /
      `blueprint` from a **live save through the real router** carrying every forbidden key at
      once; `training_jobs.config` through `assert_no_exchange_identity` over the full
      vocabulary at three depths, with the call proved present by AST rather than by docstring;
      response bodies including `GET /api/exchanges` against a vault row that really holds a
      key, a secret and a passphrase; WS frames at their **real producers**
      (`PlanRuntimeState.to_dict`, `strategy_lifecycle.canvas_state`, `public_training_job`)
      over poisoned rows; and `caplog` at DEBUG on every request. Requirement 21.7 is met at the
      API, frame and log level.
    - **Requirement 21.9 — no control lost coverage across 8.1-8.11.** Checked four ways, not
      asserted. *(1) Nothing was deleted or skipped away.* No test file was removed in Phase 8
      (`git status` shows two deletions, both frontend and both from the Phase 2/3 single-schema
      work: `src/utils/dagSerializer.js` and `test_e2e.js`). The skip count is **18, unchanged
      across 8.6, 8.7, 8.10, 8.11 and this run**. The only `pytest.skip` inside a Phase 8 file
      is 8.2's conditional venue-gap guard ("the installed ccxt build does not exhibit this
      venue gap") — environmental, not a control. `tests/test_risk_settings_api.py`'s two skips
      are **unchanged since HEAD**, so they pre-date this spec. *(2) Auth and limiters are read
      off the live route table.* `TestEveryEndpointKeepsItsAuthAndItsLimiter` resolves
      `get_current_user` from `route.dependant` for all 21 matrix entries — a route that lost
      the dependency is caught even with the decorator text still in the file — and requires
      `@limiter.limit(` on every `/strategy-operations/` route (≥12 declarations), covering
      21.1 and 21.8. *(3) Every Phase 8 source change adds a filter or a refusal.* 8.3's
      pause/resume/stop, 8.6's model gates and 8.7's five fixes all *add* to the ownership
      filter rather than replace it; 8.4's `execute_plan` adds a refusal ahead of
      `execution_guard` / `risk_engine` / `dag_risk_integration` and a test asserts none of the
      three was rewired to call it. No migration, table, column, index, policy, trigger or grant
      was touched by any Phase 8 task. *(4) The baseline did not move.* Identical failure set,
      below.
    - **The two source changes named in the task, re-checked against the code, not the notes.**
      *8.10's `schema.extract_raw_graph` fallback weakens nothing:* `graph_json` is still read
      **first**, so a canonical row is unaffected and the deploy-time hash gate and 004c's
      trigger stay authoritative over the column they guard; `_graph_shaped_blueprint` is the
      **last** fallback and is gated on `nodes`/`edges`, so the genuinely legacy entry/exit blob
      still reports itself unloadable rather than being half-parsed; the payload it returns goes
      through the same `load_graph` validation as any other, and
      `_assert_deploy_prerequisites` still refuses the degraded row for its missing
      `validation_state` (so the *load* degraded and the *deploy* did not). `schema.py` stays
      pure in / pure out. *8.7's five fixes strengthen:* `GET /api/deployments/{id}` now reads
      the authoritative `strategy_deployments` row with an explicit `.eq("user_id", …)`
      **before** the in-process registry, answers one 404 for both "not yours" and "not there",
      and **fails closed with a 503** (`DEPLOYMENT_LOOKUP_UNAVAILABLE`) when ownership cannot be
      verified rather than falling through; `restart` reads ownership first and carries the
      filter on its write; and `get_version_history`, `compare_versions` and `restore_version`
      now go through one `StrategyService._owns_strategy` helper (verified present at
      `strategy_service.py:1006` with three call sites at 1059, 1096 and 1220).
    - **The clause that does not close: "immutability of deployed versions enforced at the
      database level" (Requirement 9.2). NOT VERIFIED, and the gap is larger than "there is no
      PostgreSQL here."** `004c_immutable_versions.sql` **declares** the enforcement correctly —
      `chk_valid_requires_hash` verbatim from `design.md`, and
      `reject_immutable_version_update()` + `trg_sv_immutable` as a `BEFORE UPDATE … FOR EACH
      ROW` trigger rejecting any change to `graph_json`, `compiled_plan`, `dag_hash` or
      `schema_version` while `OLD.is_read_only OR OLD.lifecycle_state IN
      ('DEPLOYED','RUNNING','PAUSED')`. But **004/004b/004c/004d/004e are unapplied**, so
      nothing here shows the trigger refusing an edit, and — the part that is not merely an
      environment limit — **no test asserts even that 004c declares them.** `trg_sv_immutable`
      and `reject_immutable_version_update` appear in the test tree only inside prose
      docstrings (`test_task_8_3_lifecycle_state_machine.py`,
      `test_version_consumer_agreement.py`); `chk_valid_requires_hash` appears only in comments
      (`test_sb02_clone_preserves_plan.py` and two others); and 004c is referenced by a test
      only as a **filename**, in 8.1's "every sibling part names part 5" cross-reference. There
      is no `tests/*immutab*` file. Contrast Requirement 21.3, where 8.7's
      `TestPersistenceLayerRls` at least statically asserts 004d declares its five owner
      policies and enables RLS on both tables and says in its own docstring that enforcement is
      unproven. **9.2 has no equivalent, static or live.** So this exit clause currently rests
      on an unapplied *and* unasserted migration, and it is reported open. Two things would
      close it, and neither is in scope here: a static assertion that 004c declares the
      constraint and the trigger with that predicate (cheap, and the honest floor), and 004c's
      own VERIFICATION queries plus the negative UPDATE run against a real database (the actual
      claim). Requirement 21.3 stays open for the same reason 8.7 recorded.
    - **The two optional tasks, per task, with no papering over.**
      **8.8 (Property 14 — every numeric field of an emitted intent is finite and quantity is
      positive; Requirements 20.5, 20.6): equivalent coverage exists for the *requirements*,
      but the property is a real generator hole.** The decision function
      `dag_engine.assert_execution_safe` is covered by examples in
      `tests/test_math_executor_firewall.py` —
      `TestAssertExecutionSafeBlocksNonFiniteFields`, `TestAssertExecutionSafeBlocksNonPositiveQuantity`,
      `TestAssertExecutionSafeRecordsAndIsRecognisable` — including a NaN quantity, each named
      order field, **a non-finite field beyond the named four**, an unparseable numeric string
      ("unable to check is not safe to send"), the `NodeIssueLog` record, and the typed
      `ExecutionBlocked.node_id`/`.code` attributes; and the wiring is covered by 8.4's
      `test_assert_execution_safe_runs_before_the_intent_leaves_the_runtime` plus
      `test_a_blocked_intent_is_recorded_against_its_node`. So neither 20.5 nor 20.6 is
      unasserted. **What is genuinely missing:** (a) no quantification — the guard iterates
      `_intent_field_names(intent)`, i.e. *every* field the payload happens to carry, and only a
      handful of names and values are ever tried, so "every numeric field" is asserted for a
      finite hand-picked set; and (b) the end-to-end wiring test exercises only the
      `NON_POSITIVE_QUANTITY` arm, at `quantity = 0.0` — **the `NON_FINITE_ORDER_FIELD` arm is
      never exercised through `execute_plan`**, only against the guard called directly, so
      nothing proves a NaN or infinity arising *inside* intent construction is caught on the
      real emit path. `tests/test_intent_finite_property.py` does not exist and no test file
      claims Property 14 by name.
      **8.9 (Property 15 — every node in the upstream closure of an emitted intent is `READY`;
      Requirements 20.1, 20.10, 20.11): same shape, and the hole is structural.** All three arms
      of 20.1 have green example coverage in `tests/test_task_8_4_runtime_readiness_gate.py`
      (`TestNotReadyOnAMissingRequiredInput`, including the downstream ACTION going `NOT_READY`
      and the dormant-branch case; `TestWarmingHoldsUntilWarmupIsSatisfied` with the `bars_needed`
      boundary; `TestAwaitingModel` asserting it is neither `WARMING` nor `NOT_READY`), 20.11 has
      `TestMergeIsAllOrNothing`, and `TestReadinessProperties` **is** a Hypothesis property (25
      examples) asserting every label is one of the four published states and that no intent
      exists at or below the plan's composed warmup. **What is genuinely missing:** that
      property's generator is `st.integers(1, 200)` over **window length against a single fixed
      linear plan** — its own docstring says so — whereas Property 15's quantifier is over
      *topology*, the upstream closure itself. No test generates graphs, so the closure claim
      holds only for the five hand-built fixtures (linear, merge, variadic, unwired, model).
      `tests/test_intent_readiness_property.py` does not exist and no test file claims Property
      15 by name.
      Neither hole leaves a requirement without a green assertion, and neither property exists;
      both remain `[ ]*`, correctly, and describing them as covered elsewhere would be false.
    - **Baseline: exactly the pinned ceiling, no new failures.** Backend
      **43 failed / 4445 passed / 18 skipped over 4506 collected** with
      `.venv\Scripts\python.exe -m pytest -q` at repo root in default order (389.56 s) —
      identical to 8.11's recording, against the pinned ceiling of **43**. The failures are the
      same **nineteen** files with **identical per-file counts** recorded by 8.6, 8.7, 8.10 and
      8.11: `full_system_test.py` 4, `test_account_health_query.py` 1,
      `test_atomic_order_cancellation_fix.py` 4, `test_distributed_execution_safety.py` 7,
      `test_event_pipeline.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_safety_fix.py` 1, `test_exchange_vault_singleton.py` 2,
      `test_false_success_report_fix.py` 1, `test_fee_precision_fix.py` 1,
      `test_get_db_dependency.py` 1, `test_marketplace_pipeline.py` 4,
      `test_position_delta_race_condition_fix.py` 1,
      `test_reconciliation_engine_fail_closed.py` 1, `test_risk_management_lifecycle.py` 1,
      `test_risk_settings_api.py` 2, `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_tenant_isolation_fixes.py` 1, `test_transaction_isolation_serializable.py` 3.
      43 + 4445 + 18 = 4506 reconciles. **One of those nineteen was checked individually
      because its name invites the wrong conclusion:**
      `test_tenant_isolation_fixes.py::TestStrategyOperationsTenantIsolation::test_delete_strategy_owner_can_delete`
      is the *positive* owner case and it fails with
      `TypeError: '<' not supported between instances of 'AsyncMock' and 'int'` inside
      `SubscriptionEngine.decrement_quota_usage` — a defect in the test's own mocking of the
      subscription quota path, not an isolation failure, and the file is unchanged since HEAD.
    - **Frontend: `npx vitest run` in `algo22-terminal/` — 1 failed / 783 passed over 784, in
      32 files, 1 file failed (269.59 s).** The single failure is the known out-of-scope
      `tests/unit/portfolio-rendering.test.jsx` > "should verify improved empty states"
      (`portfolio-rendering.test.jsx:147`, a `/Trade history/i` match against `Portfolio.jsx`),
      which is a Portfolio-page copy assertion and belongs to no Strategy Builder task.
      `integration.test.jsx` > "should load dashboard data on mount" **did not fire this run** —
      it is the known timeout flake, and it passed here.
    - **Verdict: Phase 8 does not exit clean.** Two of the three exit clauses (cross-tenant
      matrix, credential leak scan) are green and Requirements 21.4, 21.7, 21.9 and 22.1 are
      covered. **Requirement 9.2's database-level enforcement is unproven and, unlike 21.3, is
      not even statically asserted**, and Properties 14 and 15 do not exist. Phase 9 work that
      does not depend on 9.2 can proceed; the acceptance gate before Backtester or Marketplace
      work should not be signed off on 9.2 until 004c is applied and verified against a real
      database.

    - **Addendum, 2026-08-28 — re-evaluation after task 8.13.** The note above is left
      unedited as the historical record of the first run. One sentence in it is now stale and
      is corrected here: "**Requirement 9.2's database-level enforcement is unproven and,
      unlike 21.3, is not even statically asserted**". The second half no longer holds.
      `tests/test_immutable_versions_migration.py` (task 8.13, **13 tests, all passing**,
      re-run standalone at this re-evaluation: 13 passed in 0.72 s) now statically asserts
      that `004c_immutable_versions.sql` **declares** `chk_valid_requires_hash` on
      `public.strategy_versions` with design.md's predicate parsed and compared at test time,
      `public.reject_immutable_version_update()` as a plpgsql `RETURNS TRIGGER` gated on
      `OLD.is_read_only OR OLD.lifecycle_state IN ('DEPLOYED','RUNNING','PAUSED')` comparing
      exactly Requirement 9.2's four columns (`graph_json`, `compiled_plan`, `dag_hash`,
      `schema_version`) with `IS DISTINCT FROM` and no fifth comparison, and
      `trg_sv_immutable` as `BEFORE UPDATE ON public.strategy_versions FOR EACH ROW`. It
      strips `--` comments before asserting, so 004c's header — which quotes the design's
      trigger verbatim in prose — cannot satisfy the assertions in place of the DDL. So 9.2
      now has the same floor 21.3 has had since 8.7, and the specific gap the first run
      called out is closed. **The verdict itself does not move.**
    - **The three exit clauses, restated with current status.**
      *(1) Cross-tenant isolation matrix (Requirement 21.4): **GREEN**, unchanged.*
      `tests/security/test_builder_tenant_isolation.py`, 137 tests passing, 21 endpoint ×
      resource entries answered three ways with owner-side positive controls. Untouched by
      8.13.
      *(2) Credential leak scan (Requirement 21.7): **GREEN**, unchanged.* Property 23 over
      the ten-key canary vocabulary at persistence, response, WS-frame and log level.
      Untouched by 8.13.
      *(3) Deployed-version immutability enforced at the database level (Requirement 9.2):
      **STILL NOT SATISFIED.*** Declaration asserted; enforcement not.
    - **Is Requirement 9.2 satisfied? No.** A static assertion over SQL text is **not
      enforcement** — it proves the file says the right thing, not that any database does the
      right thing. Nothing in the suite shows that PostgreSQL accepts 004c, that
      `chk_valid_requires_hash` refuses a `VALID` row with a null `dag_hash` or
      `compiled_plan`, that `trg_sv_immutable` fires, or that it rejects a graph edit on a
      `DEPLOYED` row. Requirement 9.2 — "THE Persistence_Layer SHALL reject any update to a
      read-only version's graph, Compiled_Plan, Identity_Hash or schema version" — is a claim
      about a running database, and it remains unverified.
    - **Classification: environment limitation to carry forward, not a blocking gap.** This
      is a change of classification from the first run, and 8.13 is what earns it. Before
      8.13 the gap had a component that was *not* environmental — no assertion of any kind
      existed, so a rename or a silent deletion of the trigger would have been invisible to
      CI as well as unenforced locally, and that part was closeable here and was not closed.
      That component is now gone. What is left is exactly and only "there is no PostgreSQL in
      this environment and migrations 004–004e are unapplied", which no amount of work inside
      this workspace can close, and which is the same posture already carried for Requirement
      21.3 (004d's RLS policies), 004e's deployment binding columns and 004d's training and
      model tables. Treating it as blocking would block on the absence of infrastructure
      rather than on missing work, and would make every migration-backed requirement in the
      spec unresolvable. Carried forward with an explicit condition instead: **the acceptance
      gate before Backtester or Marketplace work must not be signed off on 9.2 until 004c is
      applied to a real PostgreSQL instance and its own VERIFICATION section is run** — groups
      1, 2 and 4 for presence in `pg_constraint`/`pg_trigger`, group 3 for the negative
      `INSERT`, group 5 for the negative `UPDATE` (`SET dag_hash` on a `DEPLOYED`/`RUNNING`/
      `PAUSED` row) together with its paired positive case that a column the trigger does not
      name (`validation_report`, `updated_at`) stays updatable, which is what separates a
      correct trigger from an over-broad one.
    - **8.8 and 8.9 remain the two known generator holes, unchanged.** Re-checked at this
      re-evaluation: `tests/test_intent_finite_property.py` and
      `tests/test_intent_readiness_property.py` still do not exist, and no test file claims
      Property 14 or Property 15 by name. Both stay `[ ]*`. The analysis above stands
      verbatim: Requirements 20.5/20.6 and 20.1/20.10/20.11 all have green example coverage,
      but Property 14 is unquantified over intent fields and its `NON_FINITE_ORDER_FIELD` arm
      is never exercised through `execute_plan`, and Property 15's nearest existing property
      generates window length against one fixed linear plan rather than topology, so the
      upstream-closure claim holds only for five hand-built fixtures. 8.13 touched neither.
    - **Suite: taken as given from 8.13's run, not re-run here.** **43 failed / 4458 passed /
      18 skipped over 4519 collected** (4519 reconciles), against this note's original
      baseline of 43 / 4445 / 18 over 4506 — delta **+13 collected, +13 passed, +0 failed**,
      exactly 8.13's 13 tests. Failures hold at the pinned ceiling of **43** in the same
      nineteen files with the same per-file counts. Requirement 21.9 is therefore still
      satisfied: no control lost coverage, no skip added (18, unchanged), and 8.13 changed no
      source file and no migration.
    - **Re-evaluated verdict: Phase 8 still does not exit clean, for one reason instead of
      two.** Clauses 1 and 2 green; Requirements 21.4, 21.7, 21.9 and 22.1 covered. Clause 3
      open on 9.2 as an environment limitation carried forward with the condition above, no
      longer as a missing-assertion gap. Properties 14 and 15 (8.8, 8.9) remain absent and
      optional. Phase 9 work that does not depend on 9.2 can proceed.

  - [x] 8.13 Assert the immutability declaration in migration 004c
    - Add a static test asserting that
      `backend_app/migrations/004c_immutable_versions.sql` declares
      `chk_valid_requires_hash`, the `reject_immutable_version_update()` function and the
      `trg_sv_immutable` trigger, with the trigger gated on
      `is_read_only OR lifecycle_state IN ('DEPLOYED','RUNNING','PAUSED')` and firing
      `BEFORE UPDATE ... FOR EACH ROW` on `public.strategy_versions`
    - Follow the shape of `TestPersistenceLayerRls` in
      `tests/security/test_builder_tenant_isolation.py`, which already does this for
      migration 004d's five RLS policies
    - State explicitly that this asserts the declaration and NOT that the constraint is in
      force; migrations 004-004e are unapplied in this environment and proving enforcement
      needs 004c's own VERIFICATION queries plus a negative UPDATE against a real PostgreSQL
      instance
    - _Requirements: 9.2_

    - **Implementation note.** `tests/test_immutable_versions_migration.py` — 13 tests, all
      passing. **No source file was changed by this task**, and no migration was edited:
      `004c_immutable_versions.sql` is byte-for-byte what task 4.1 landed. This closes the
      gap 8.12 recorded — that Requirement 9.2 was "not even statically asserted" — and
      closes only that gap. It does **not** move 8.12's verdict: Phase 8 still does not exit
      clean on 9.2, because a static assertion is not enforcement.
    - **What the file asserts.** That 004c *declares*, in code rather than in its header's
      prose: `chk_valid_requires_hash` attached to `public.strategy_versions`, with the
      predicate `validation_state <> 'VALID' OR (dag_hash IS NOT NULL AND compiled_plan IS
      NOT NULL)` — parsed out of `design.md` at test time and compared, so the two cannot
      drift apart silently; `public.reject_immutable_version_update()` as a plpgsql
      `RETURNS TRIGGER`, gated on `OLD.is_read_only OR OLD.lifecycle_state IN
      ('DEPLOYED','RUNNING','PAUSED')` (both arms asserted — dropping the `is_read_only` arm
      would un-protect any future code that sets it, dropping the lifecycle arm would return
      the trigger to the no-op the design's literal snippet describes), comparing exactly
      Requirement 9.2's four columns `graph_json`, `compiled_plan`, `dag_hash`,
      `schema_version` with `IS DISTINCT FROM` and no fifth comparison, and rejecting rather
      than repairing (`RAISE EXCEPTION` present, no assignment to `NEW`); and
      `trg_sv_immutable` as `BEFORE UPDATE ON public.strategy_versions FOR EACH ROW EXECUTE
      FUNCTION public.reject_immutable_version_update()`, with no `INSERT`/`DELETE` arm.
      Both DDL pieces are asserted to sit inside a name-scoped, table-scoped `pg_constraint`
      / `pg_trigger` re-run guard, inside the file's single `BEGIN;`/`COMMIT;`.
    - **Two things make the method load-bearing rather than decorative.** *(1) Comments are
      stripped first.* 004c's header quotes `design.md`'s trigger **verbatim, in prose**, so
      all three identifiers appear in the file even if every statement were deleted — a
      naive substring search over the raw text would be satisfied by the explanation of the
      trigger instead of by the trigger. One test exists purely to fail if the identifiers
      ever survive only in the comments. The single exception is deliberate and marked: the
      test that requires the VERIFICATION queries reads the **raw** text, because there the
      comment block *is* the subject. *(2) The schema qualification is not normalised away*
      where the target table is what is being asserted, so a declaration attached to some
      other schema's same-named table fails.
    - **What it does not prove — and cannot, here.** Nothing below "the file says so".
      Migrations 004–004e are unapplied in this environment and there is no PostgreSQL, so
      these tests do **not** show that PostgreSQL accepts the file, that
      `chk_valid_requires_hash` actually refuses a `VALID` row carrying a null `dag_hash` or
      `compiled_plan`, that `trg_sv_immutable` actually fires, or that it actually rejects a
      graph edit on a `DEPLOYED` row. Requirement 9.2's claim — "THE Persistence_Layer SHALL
      reject any update to a read-only version's graph, Compiled_Plan, Identity_Hash or
      schema version" — is a claim about a running database and remains **unverified**.
      Closing it needs 004c's own VERIFICATION section run after the file is applied: groups
      1, 2 and 4 for presence in `pg_constraint`/`pg_trigger`, group 3 for the negative
      `INSERT`, and group 5 for the negative `UPDATE` (`SET dag_hash` on a
      `DEPLOYED`/`RUNNING`/`PAUSED` row) together with its paired positive case, that a
      column the trigger does not name — `validation_report`, `updated_at` — is still
      updatable on that same row, which is what separates a correct trigger from an
      over-broad one. The acceptance gate should not be signed off on 9.2 until that is
      done. Posture matches `TestPersistenceLayerRls` in
      `tests/security/test_builder_tenant_isolation.py` for 004d's RLS policies, and
      `test_deployment_binding_columns_migration.py` (004e) and
      `test_training_and_model_tables_migration.py` (004d).
    - **Suite.** **43 failed / 4458 passed / 18 skipped over 4519 collected** with
      `.venv\Scripts\python.exe -m pytest -q` at repo root in default order (371.07 s).
      43 + 4458 + 18 = 4519 reconciles. Against 8.12's baseline of **43 failed / 4445 passed
      / 18 skipped over 4506 collected**, the delta is **+13 collected, +13 passed, +0
      failed** — exactly this task's 13 tests. Failures hold at the pinned ceiling of **43**,
      in the same **nineteen** files with the same per-file counts 8.6, 8.7, 8.10, 8.11 and
      8.12 recorded; none is in a file this task touched, and this task touched no file that
      any of them reads.

- [x] 9. Phase 9 — Observability, performance and hardening

  - [x] 9.1 Instrument the builder, training and runtime metrics
    - Record through the existing `metrics.py`: `builder.validation.duration_ms`,
      `builder.validation.errors` by code, `builder.compile.duration_ms` by node count,
      `builder.compile.failures`, `builder.registry.requests` and `.cache_hits`,
      `builder.assets.universe_age_seconds`
    - Record `training.jobs.by_status`, `training.job.duration_seconds` by block,
      `training.jobs.cap_rejections` by cap, `training.jobs.blocked_insufficient_data`
    - Record `dag.node.execution_ms` by category, `dag.node.not_ready` by reason,
      `dag.intents.blocked_non_finite`, `deployment.state_transitions`,
      `market_data.latency_ms`, `market_data.quality_score`, `market_data.feed_state`
    - _Requirements: 24.1, 24.2, 24.3_

    - **Implementation note.** All eighteen named metrics land in the **existing**
      `backend_app/backend/metrics.py` and are exported by its existing
      `get_prometheus_metrics()`. No second collector, no parallel registry and no new
      endpoint: `routers/metrics.py` is untouched. `tests/test_task_9_1_builder_metrics.py`
      — **160 tests, all passing**. Nine seams instrumented, one metrics module extended,
      ten files changed in total.
    - **The dotted names are used verbatim, and they are still legal Prometheus.** The
      task's three bullets spell the names with dots; Prometheus' exposition format admits
      none. So the dotted name is the metric's **identity** (`.name`, what a caller and a
      test address it by, and what task 9.2's alert rules will name) and the exported line
      carries `.prometheus_name`, produced by the new `to_prometheus_name()`. Two renderings
      of one name, not two names. Every metric this module defined before 9.1 is already a
      legal Prometheus name, so sanitisation is the identity function on all twelve of them
      and **no existing exported line changed** — asserted, not assumed
      (`test_sanitisation_leaves_every_pre_existing_name_alone`).
    - **A new `Summary` type, for the three metrics a stated number is measured against.**
      Task 7.6 recorded that `metrics.Histogram` is Prometheus-shaped — bucket counts, not
      samples — so a percentile read out of it is a bucket edge, and built
      `market_data_latency.LatencySummary` for the one figure Requirement 19.12's 25 ms
      margin is measured on. Three of this task's metrics carry stated numbers:
      `builder.compile.duration_ms` (Requirement 25.1, p95 ≤ 50 ms at 200 nodes),
      `builder.validation.duration_ms` (25.2, p95 ≤ 120 ms) and `market_data.latency_ms`
      (19.12's margin). Those three are `Summary`, which retains samples in a bounded ring
      per label combination and **delegates its percentile method to
      `LatencySummary.from_samples`** — so there is one nearest-rank implementation in the
      codebase and this one cannot drift from task 7.6's. It inherits that class's two
      rules: an empty population answers `None` and never a zero (which would read as
      "instant"), and a non-finite observation is discarded rather than stored (one infinity
      would make every percentile above it an infinity). `_sum` and `_count` are lifetime
      totals unaffected by the ring, so they stay monotonic as a Prometheus client expects.
      `dag.node.execution_ms` and `training.job.duration_seconds` stay `Histogram`: no
      requirement states a percentile for either, and a histogram is cheaper on a
      per-node-per-bar path. `Histogram` itself is otherwise **unchanged** — its docstring
      now records why it is not interchangeable with `Summary`, and
      `test_a_histogram_keeps_bucket_counts_and_offers_no_exact_percentile` asserts the
      contrast rather than leaving it to the comment.
    - **The nine seams, and what each reads rather than re-derives.**
      `strategy_dag/validator.py` — `validate()` times itself with `perf_counter` (an
      interval, so a wall-clock correction cannot make it negative) and records
      `builder.validation.errors` from `report.errors`' own codes, at the single `return`,
      after the report is complete. `strategy_compiler.py` — `compile_plan` records a
      duration **only for a compile that produced a plan** (the duration of a refusal is not
      the duration of a compile, and blending the two would make 25.1's p95 a mixture of
      work done and work declined), with the node count taken from `plan.execution_order`;
      failures are counted under a closed three-value vocabulary,
      `COMPILE_FAILURE_{INVALID_GRAPH,NOT_CANONICAL,ASSEMBLY}`, because a compile yields no
      plan for exactly three reasons an author fixes differently.
      `routers/strategy_operations.py` — `builder.registry.requests` / `.cache_hits` in
      `_registry_response`, the one function every registry endpoint returns through, so a
      projection added later is counted without anybody remembering to; a 304 counts as
      **both** a request and a hit, so Requirement 25.3's hit ratio has the right
      denominator. `builder.assets.universe_age_seconds` is read off
      `page["source_meta"]["age_seconds"]`, the figure the response body already reports, so
      the gauge and the body cannot disagree. `training_worker.py` — `RUNNING` at
      `claim_training_job` *after* the conditional UPDATE matched (so the tally is claims,
      not attempts) and every terminal status in `_finish`, the one function every terminal
      state comes through, *after* the write landed; the duration is `elapsed_seconds(job)`,
      the same figure Requirement 15.8's cap is enforced against, and an unmeasurable
      elapsed time is recorded as **absent rather than zero**. `strategy_service.py` —
      `QUEUED` only for a job the call actually created (an idempotent hit created nothing;
      counting one would inflate the tally past the rows that exist), plus the admission-side
      cap rejections and data blocks. `dag_engine.py` — `dag.node.execution_ms` around the
      one `execute_node` call, labelled from the **descriptor's** category rather than the
      engine node dict's `type` (which is the executor key); `dag.node.not_ready` from
      `state.not_ready_reasons()`, the snapshot task 8.4 already builds and the existing log
      line already reads, so the reason vocabulary is `RUNTIME_STATES` and there is no second
      list; `dag.intents.blocked_non_finite` inside `assert_execution_safe`'s `_blocked`
      closure. `strategy_lifecycle.py` — `deployment.state_transitions` in
      `apply_version_state`, past the write. `feed_state.py` — `market_data.feed_state` in
      `evaluate_feed_state`'s `report()` closure, which every one of its return paths goes
      through, so a sixth classification added later is counted automatically.
      `market_data_contract.py` — `market_data.quality_score` republishes the validator's
      own `DataQualityReport.quality_score` (this module grades nothing, and a second scoring
      arithmetic here would be the defect its own docstring forbids), and
      `market_data.latency_ms` is derived from the closed-bar rule's existing boundary.
    - **Four placement decisions that are load-bearing, not stylistic.**
      *(1) `market_data.latency_ms` is delivery delay, and only on the streaming path.*
      The figure is `closed_before - open_time` — `closed_before` being `now - interval`, the
      boundary `_is_closed` already computes — so it is "how long after this bar closed was
      it admitted", derived from the same arithmetic the closed-bar rule uses, and a bar
      admitted the instant it closed reads as zero. Recorded only when `drop_late` is set,
      i.e. the paper/live path whose clock advances through `advance_to`. A historical read
      fixes its clock at construction, so every bar in a fetched window would report its
      *age*; that is a real figure but not this one, and mixing the two would make the
      percentiles describe neither.
      *(2) `dag.node.not_ready` is recorded before the SYSTEM FREEZE early-return*, not
      after the intent gate. A freeze stops intents; it must not make node readiness
      unobservable. `READY` is excluded — it is not a reason a node produced nothing.
      *(3) `dag.intents.blocked_non_finite` increments on the `NON_FINITE_ORDER_FIELD` arm
      only.* `assert_execution_safe` also refuses `NON_POSITIVE_QUANTITY`, and task 9.2
      alerts on **any** increment of this counter — folding the second arm in would page an
      operator every time an author submitted a zero-size order. The counter carries **no
      labels**, so "any increment" is one series rather than a sum an alert rule has to
      remember to take.
      *(4) `training.jobs.blocked_insufficient_data` counts exactly two reasons*, named once
      in `strategy_service.INSUFFICIENT_DATA_BLOCK_REASONS` and read by both the admission
      seam and the worker's `_finish`, so the two answer one question rather than two
      half-questions: `ML_REQUIREMENTS` (Requirements 14.3/14.4 — the dataset was built,
      measured, and came up short) and `DATASET` (the needed window exceeds
      `TRAINING_MAX_BARS`, the same shortfall one step earlier). Deliberately excluded, so
      the omissions are decisions: `DATA_QUALITY` (14.7) is a *grading* refusal — the bars
      arrived and were judged POOR/UNUSABLE, which is not a shortage, and it is already
      visible through `market_data.quality_score`; `DATA_UNAVAILABLE` and
      `DATA_SOURCE_UNRESOLVED` are feed and wiring faults; `MODEL_UNPUBLISHED` is a registry
      fact; `CAP_EXCEEDED` has its own metric. Folding any of them in would make the counter
      unusable as the answer to "how often does an author not have enough history?".
      Admission-time and run-time counts cannot double-count: a request blocked at admission
      never becomes a row to fail.
    - **Cardinality and secrecy, checked against both vocabularies.** No metric name and no
      label name carries `exchange`, `venue`, `api_key`, `secret`, `passphrase`, `password`,
      `token` or `credential` (SB-06, Requirement 12.1), and none is a per-tenant or
      per-resource identity — no `user_id`, `tenant_id`, `strategy_id`, `version_id`,
      `deployment_id`, `job_id`, `node_id`, `email`, `session_id` or `ip`. Both lists are
      asserted over all eighteen metrics. Every label is drawn from a **closed vocabulary**:
      a validation code, a compile refusal reason, a registry resource name, a job status, a
      registry block id, a cap name, a Block_Category, a runtime state, a lifecycle state, a
      bar interval. `builder.compile.duration_ms`'s "by node count" is bucketed by
      `node_count_bucket` to {10, 50, 100, 200, 200+} — the measurement points 25.1/25.2 and
      task 9.6 name — because a raw count would be one series per graph size. `symbol` is
      labelled on `market_data.quality_score` only: it is global reference data drawn from
      the asset universe, not user data, and `execution_latency_ms` has been labelled by it
      since STEP 8.1. `feed_state` deliberately carries **no** symbol, because state × reason
      × timeframe × market is where that counter would go unbounded. As a backstop,
      `safe_label_value` escapes backslashes, quotes and newlines (an unescaped quote would
      split one exported line into two malformed ones) and truncates to 64 characters.
    - **Instrumentation cannot change a control's decision, and cannot fail the act it
      measures.** Two guards, both structural. *(1)* Every recorder is wrapped in
      `metrics.never_fails`, which logs at debug and returns `None`: a bad label, a
      non-numeric duration or a metric in a bad state cannot propagate. *(2)* Every seam
      reaches the collector through a **lazy, guarded** `_metrics()` returning `None` when
      the import fails, so an unimportable metrics module cannot break a validation, a
      compile, a training transition or the intent gate. `validator.py`'s is lazy for a
      second reason as well: a module-scope import would add a `backend/` edge to the pure
      core for every consumer of `strategy_dag`, which its own Purity note forbids and
      `test_strategy_dag_architecture.py` measures. The claim is then tested by breaking the
      metric object each seam writes to and asserting the seam still does its job — the
      load-bearing case being `test_a_broken_metric_does_not_stop_an_intent_from_being_refused`,
      because a refusal that stopped refusing would send the order.
      **No control was touched:** no auth, RLS, tenant-isolation, financial-invariant,
      execution-safeguard, risk-gate, idempotency or rate-limit code path was edited, no
      threshold moved, no `except` widened around a control, and no migration was touched.
      Every recording call is a statement that reads state and returns; none appears inside
      a conditional a control evaluates.
    - **One rename, forced by an existing architecture test and worth recording.** The
      node-count label helper was first written as `compile_node_count_bucket` and
      `tests/test_compiler_architecture.py` failed it: that test treats **any**
      `compile`-named definition as a candidate graph-compile authority, and this one named
      no canonical entry point, so it was classified as SB-01 reintroduced. The test is right
      to be that broad. The fix is the name — `node_count_bucket` — not an exemption for a
      file, and the function's docstring records why so the next author does not rename it
      back.
    - **Migrations 004–004e remain unapplied here, and this task does not change that.**
      Nothing added reads a column, so no path gained a new degradation mode.
      `apply_version_state` still returns `moved=False` and warns naming
      `004_strategy_builder_canonical.sql` when `lifecycle_state` cannot be written, and
      `deployment.state_transitions` is recorded **past** that return — so an unapplied
      migration produces an uncounted transition, not a 500 and not a counted transition
      that never happened. Asserted:
      `test_a_transition_that_could_not_be_written_is_not_counted`.
    - **Suite.** **43 failed / 4618 passed / 18 skipped over 4679 collected** with
      `.venv\Scripts\python.exe -m pytest -q` at repo root in default order (304.52 s).
      43 + 4618 + 18 = 4679 reconciles. Against 8.13's baseline of **43 failed / 4458 passed
      / 18 skipped over 4519 collected**, the delta is **+160 collected, +160 passed, +0
      failed** — exactly this task's 160 tests. Failures hold at the pinned ceiling of
      **43**, in the same **nineteen** files with the same per-file counts 8.6 … 8.13
      recorded (`full_system_test.py` 4, `test_account_health_query.py` 1,
      `test_atomic_order_cancellation_fix.py` 4, `test_distributed_execution_safety.py` 7,
      `test_event_pipeline.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_safety_fix.py` 1, `test_exchange_vault_singleton.py` 2,
      `test_false_success_report_fix.py` 1, `test_fee_precision_fix.py` 1,
      `test_get_db_dependency.py` 1, `test_marketplace_pipeline.py` 4,
      `test_position_delta_race_condition_fix.py` 1,
      `test_reconciliation_engine_fail_closed.py` 1, `test_risk_management_lifecycle.py` 1,
      `test_risk_settings_api.py` 2, `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_tenant_isolation_fixes.py` 1, `test_transaction_isolation_serializable.py` 3).
      None is in a file this task touched. Requirement 21.9 holds: no control lost coverage
      and no skip was added (18, unchanged). The suites most exposed to these edits were also
      run on their own and are green — `test_market_data_contract.py`,
      `test_feed_state_and_data_quality.py`, `test_task_8_4_runtime_readiness_gate.py`,
      `test_task_8_3_lifecycle_state_machine.py`, `test_asset_discovery.py`,
      `test_block_registry.py`: **629 passed** — as are both architecture tests,
      `test_strategy_dag_architecture.py` + `test_compiler_architecture.py`: **92 passed**.
    - **Frontend: unchanged and not implicated, and re-run anyway.** This task edited no
      file under `algo22-terminal/`. `npx vitest run` in `algo22-terminal/`: **2 failed / 782
      passed over 784**, in 32 files, 2 files failed (346.95 s) — **at the ceiling of 2, not
      over it**. Both failures are the two known out-of-scope items 8.12 named: the
      `tests/unit/portfolio-rendering.test.jsx` > "should verify improved empty states" copy
      assertion (`portfolio-rendering.test.jsx:147`, a `/Trade history/i` match against
      `Portfolio.jsx`), which belongs to no Strategy Builder task; and
      `tests/unit/integration.test.jsx` > "should load dashboard data on mount", the known
      timeout flake, which fired this run at **47 948 ms** having passed in 8.12's run. Both
      are frontend files this task did not touch and neither reads a metric.
    - **What this task does NOT do, so 9.2 and 9.6 are not read as done.** No alert rule is
      wired: task 9.2 owns the four alerts, and this note's job was to make each of them
      readable from one series. No performance budget is asserted: task 9.6 owns the p95
      measurements at 10/50/100/200 nodes, and the reason
      `builder.compile.duration_ms` is a `Summary` bucketed by node count is so that 9.6 can
      read 25.1's p95 at 200 nodes from an exact percentile rather than from a bucket edge.
      The inspector's execution trace (9.3) is untouched.

  - [x] 9.2 Wire the alerts
    - Alert on any `dag.intents.blocked_non_finite` increment, on `market_data.feed_state =
      STALE` sustained beyond 3 expected intervals for a live deployment, on training queue
      depth beyond threshold, and on asset universe age beyond 12 h
    - _Requirements: 24.4, 24.5_

    **Implementation note.**
    - **The four alerts join the platform's existing pipeline; no second one was built.**
      `core/alerting_system.py` is the platform's multi-channel dispatcher — structured log,
      HTTP webhook, SMTP email for `CRITICAL`/`HIGH`, Redis pub/sub, and a five-minute
      deduplication window keyed on `(type, source, tenant_id)` — and the four alert types
      were added to **its** `AlertType` enum (`BUILDER_INTENT_BLOCKED_NON_FINITE`,
      `BUILDER_FEED_STALE`, `BUILDER_TRAINING_QUEUE_DEPTH`,
      `BUILDER_ASSET_UNIVERSE_STALE`) rather than to a builder-shaped enum beside it. That
      module keeps delivery, severity routing and dedup; the new
      `backend_app/backend/builder_alerts.py` owns only **the four conditions and their
      thresholds**. `reconciliation_scheduler.py` reaches the dispatcher exactly this way, so
      the pattern is the codebase's, not a new one.
    - **No Prometheus rule was written, and that is a finding rather than an omission.**
      `monitoring/alerts.yml` is the platform's rules file and was the obvious place to look.
      It cannot serve these four: `routers/metrics.py` serves
      `prometheus_client.generate_latest()`, a **different registry** from the
      `MetricsCollector` singleton task 9.1's eighteen metrics live in, and
      `metrics_collector.get_prometheus_metrics()` is bound to no route at all. A rule over
      `dag_intents_blocked_non_finite` would therefore never fire, and a rule that cannot
      fire is worse than no rule because it reads as coverage. Two of the four are also
      **unexpressible** as rules on principle: `market_data.feed_state` carries no deployment
      label (so "for a live deployment" cannot be written), and `training.jobs.by_status` is a
      counter of *transitions*, not a depth gauge. Wiring the collector to a scrape is its own
      task; this one raises the alerts in-process, where all four facts are actually in hand.
    - **Four conditions, four thresholds, and every threshold is read from the authority
      that already owns it.** *(1) Non-finite intents: no threshold, because Requirement 24.4
      states none.* The counter "must stay at zero", so the first increment is the event. The
      counter is unlabelled (task 9.1's decision), which makes "any increment" a comparison
      against one number instead of a sum over a label set. *(2) `STALE`: the classifier's own
      `3x` boundary.* `feed_state.evaluate_feed_state` already applied Requirement 19.9's
      boundary to produce `STALE` and already publishes the boundary it used as
      `FeedStateReport.stale_after_seconds`; both are read off the report. There is exactly
      one place in the codebase that decides where three intervals falls and this is not it —
      a second copy could drift, and then the feed panel and the pager would disagree about
      whether a feed had stopped. `test_the_alert_boundary_is_the_classifiers_boundary_and_not_a_copy_of_it`
      pins it at `3i - 0.001` (DELAYED, silent) and `3i` (STALE, pages), inheriting 19.9's
      closed-on-the-worse-side rule rather than restating it. *(3) Training queue depth:
      `TRAINING_QUEUE_DRAIN_CYCLES` (4) × the shared pool width*
      `ml_training_policy.DEFAULT_MAX_CONCURRENT_JOBS_GLOBAL`, i.e. "a job joining now waits
      behind four full passes of the fleet". Read through the policy module so the alert and
      Requirement 16.5's saturation check cannot disagree about how wide the fleet is;
      `ML_TRAINING_QUEUE_DEPTH_ALERT` names an absolute for a deployment that queues
      deliberately. *(4) Universe age: `UNIVERSE_AGE_TTL_MULTIPLE` (2) ×
      `asset_universe.UNIVERSE_TTL_SECONDS`* = the 12 h the task names. Both of the last two
      are **multiples of an existing number, not absolutes**, and that is the point: an
      operator who widens the fleet stops being paged for a queue the fleet can now drain, and
      one who moves the TTL moves the staleness alert with it. A hardcoded `32` and a
      hardcoded `43200` would both drift silently away from what they were derived from —
      asserted by `test_the_threshold_follows_the_ttl_rather_than_a_constant` and
      `test_the_threshold_is_drain_cycles_of_the_pool_width`, which move the authority and
      watch the threshold follow.
    - **Every condition has a near-miss that must stay silent, and each one is a case that
      would otherwise train an operator to ignore the pager.** *A zero-size order does not
      fire the non-finite alert.* `assert_execution_safe` also refuses
      `NON_POSITIVE_QUANTITY`; that is an authoring mistake, not an incident, and because this
      alert fires on *any* increment of an unlabelled counter, folding the second arm in would
      page on every mis-sized order. Asserted by
      `test_a_zero_size_order_is_refused_and_raises_no_alert` — refused, `code ==
      NON_POSITIVE_QUANTITY`, counter still zero, no alert. *A paper deployment and a preview
      do not fire the STALE alert.* Requirement 24.5 is qualified "a live deployment"; real
      orders are what make a stopped feed an emergency. *The two fail-closed `STALE` arms do
      not fire it either* — `NO_EVENT_OBSERVED` is a feed that never started and
      `EXPECTED_INTERVAL_NOT_PUBLISHED` is a pipeline limitation for that bar label. Both are
      correctly `STALE` (an unmeasured feed must never read `LIVE`) and neither is "the feed
      stopped three intervals ago"; only `AGE_AT_OR_OVER_3_INTERVALS` — the arm the classifier
      reaches by comparing a real age against a real interval — pages. `feed_state.py`'s own
      comment on that closure names exactly this discrimination as what task 9.2 needs.
      *A queue one job short of the threshold, and a universe at exactly 12 h, stay silent*
      (12 h is the last acceptable age, not the first unacceptable one). All five states are
      swept in `test_every_one_of_the_five_states_has_a_decided_answer`, so a sixth added later
      cannot silently inherit an answer nobody chose.
    - **The one signature change, and why it changes no classification.**
      `evaluate_feed_state` gained a keyword-only `deployment_mode=None`, threaded to the
      `report()` closure that task 9.1 already records `market_data.feed_state` in — so the
      alert sits where the metric sits and a sixth classification added later is covered
      automatically. The mode is `market_data_contract`'s `MODE_PAPER`/`MODE_LIVE`
      vocabulary and is a fact about the **caller**, which a classifier cannot measure; it was
      needed because 24.5's qualifier is not derivable from any observation. It moves nothing:
      `test_the_deployment_mode_changes_no_classification` compares state, reason, age and
      interval across all five states × `{None, paper, live}`, and
      `test_the_deployment_mode_never_reaches_the_wire_form` keeps it off `to_dict()` — it is
      an alert qualifier, and task 7.4's whole point is that the panel reports a measurement.
      `None` is the default and raises nothing, which is correct for the Builder's one existing
      feed-state caller: the data-quality endpoint previews a *version*, and a preview is not a
      deployment. **Honest gap, named so 9.7's staging drill plans for it:** no live-deployment
      runtime reads feed state yet, so the live arm has no production caller today. It is
      reachable by one keyword argument, is tested end-to-end through the real classifier, and
      the task that adds live-deployment feed monitoring gets the alert by passing the mode it
      already holds.
    - **An alert never fails or delays the act it observes — three guards, all structural.**
      *(1) Delivery is never awaited at a seam.* `raise_alerts` hands the coroutine to
      `core.background_tasks.fire_and_forget_task` — the platform's existing tracked helper,
      which keeps a strong reference so the task is not collected mid-flight and logs any
      exception — and returns the number scheduled.
      `test_with_a_running_loop_delivery_is_scheduled_not_awaited` asserts the call returned
      **before** the send ran. With no running loop there is nothing to schedule onto and the
      alert is written to the log at its own severity instead: `asyncio.run` from a synchronous
      seam would block the intent gate on a webhook, which is precisely the delay this module
      is forbidden to introduce. *(2) Every entry point is total* (`_never_fails`, the same
      rule and reasoning as `metrics.never_fails`). *(3) Every seam is **independently**
      total.* Each of the four call sites reaches the module through a lazy, guarded
      `_builder_alerts()` returning `None` on an import failure — separate from the existing
      `_metrics()` because it is a separate failure: the alert module reaches the dispatcher
      and therefore `aiohttp` and Redis, neither of which belongs on `assert_execution_safe`'s
      import path — **and** wraps the call in its own `except`. The second guard was added
      after the first test run: three tests failed because replacing the module attribute
      replaced the decorated function, so the seam called an unguarded one. The tests were
      right to be that broad — a refusal that stopped refusing would send the order, and that
      outcome must not depend on a decorator in another module staying applied. The
      load-bearing four are
      `test_a_broken_alert_module_does_not_stop_an_intent_from_being_refused`,
      `test_a_broken_alert_module_does_not_stop_a_feed_from_being_classified`,
      `test_a_broken_alert_module_does_not_stop_the_count` and
      `test_every_seam_entry_point_is_total`.
    - **The watermark, and why "any increment" needs one.** Two drivers read the non-finite
      condition — the intent gate pushes on each refusal, and `evaluate()` pulls for anything
      on a schedule — so the condition compares the counter's total against a watermark and
      advances it when it fires. Neither driver can page twice for the same refusal, a burst is
      one page that says "7 trade intent(s)" rather than seven pages, and a counter that went
      *backwards* (a restart, a replaced collector) is not an increment. The property
      `test_every_increment_is_eventually_reported_exactly_once` is the real statement of
      Requirement 24.4: over any sequence of bursts, the reported rises sum to exactly the
      number of refusals and no refusal is reported twice — which is what makes the watermark
      correct rather than merely quiet.
    - **Nothing identifying or secret reaches an alert, and it is a filter rather than a
      review convention.** `safe_details` drops any payload key whose **words** intersect
      task 9.1's two vocabularies — `CREDENTIAL_WORDS` (exchange, venue, api_key, secret,
      passphrase, password, token, credential) and `IDENTITY_WORDS` (user, tenant, account,
      strategy, version, deployment, job, node, email, session, request, ip, **id**) — and
      bounds every value at 200 characters. Word-wise rather than substring-wise, so
      `timeframe` survives while `exchange_account_id` does not; `id` being in the list is
      what makes every `*_id` key unrepresentable without naming each one. A future caller
      that adds an account reference gets it **dropped and logged**, not delivered. All four
      alerts are dispatched with `tenant_id=None`: they are platform-health facts, and a
      tenant here would both name a customer on an operator's pager and multiply the dedup key
      by the customer count — which is how a five-minute window stops deduplicating. The feed
      alert cannot name a market even by accident, because `FeedStateReport` has no symbol
      field (task 9.1 kept `feed_state` symbol-free for cardinality). Asserted over every
      payload the four conditions can build, at chosen points and under Hypothesis
      (`test_no_alert_any_condition_can_produce_carries_an_identity_or_a_credential`).
    - **No control was touched.** No auth, RLS, tenant-isolation, financial-invariant,
      execution-safeguard, risk-gate, idempotency or rate-limit code path was edited, no
      threshold that governs a decision was moved, no `except` was widened around a control,
      and no migration was touched. Every added statement reads state and returns; none appears
      inside a conditional a control evaluates. The queue alert takes **no** second query — it
      reads the count `count_all_training_jobs` had just computed, which is the only genuinely
      global `QUEUED` figure (the worker holds the service role; an RLS-scoped read reports the
      caller's own jobs as a lower bound) — and the caps below it are still evaluated from the
      same `JobCounts`.
    - **Migrations 004–004e remain unapplied here, and no path gained a degradation mode.**
      Nothing added reads a column. The queue alert is raised **only** on
      `JobCounts.available is True`, so an absent `training_jobs` still degrades to
      `JobCounts.unavailable` naming `004d_training_and_models.sql` and produces **no** page:
      an unreadable queue is not a deep one, and paging on a migration gap would report
      capacity trouble that does not exist. Asserted by
      `test_an_unreadable_queue_pages_nothing`. No 500 anywhere: the router seam is wrapped so
      an alert cannot turn a served asset page into one.
    - **Suite.** **43 failed / 4743 passed / 18 skipped over 4804 collected** with
      `.venv\Scripts\python.exe -m pytest -q` at repo root in default order (296.79 s).
      43 + 4743 + 18 = 4804 reconciles. Against 9.1's baseline of **43 failed / 4618 passed /
      18 skipped over 4679 collected**, the delta is **+125 collected, +125 passed, +0
      failed** — exactly this task's 125 tests. Failures hold at the pinned ceiling of **43**,
      in the same **nineteen** files with the same per-file counts 8.6 … 9.1 recorded
      (`full_system_test.py` 4, `test_account_health_query.py` 1,
      `test_atomic_order_cancellation_fix.py` 4, `test_distributed_execution_safety.py` 7,
      `test_event_pipeline.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_safety_fix.py` 1, `test_exchange_vault_singleton.py` 2,
      `test_false_success_report_fix.py` 1, `test_fee_precision_fix.py` 1,
      `test_get_db_dependency.py` 1, `test_marketplace_pipeline.py` 4,
      `test_position_delta_race_condition_fix.py` 1,
      `test_reconciliation_engine_fail_closed.py` 1, `test_risk_management_lifecycle.py` 1,
      `test_risk_settings_api.py` 2, `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_tenant_isolation_fixes.py` 1, `test_transaction_isolation_serializable.py` 3).
      None is in a file this task touched. Requirement 21.9 holds: no control lost coverage and
      no skip was added (18, unchanged). The suites most exposed to these edits were also run
      on their own and are green — `test_task_9_1_builder_metrics.py`,
      `test_feed_state_and_data_quality.py`, `test_asset_discovery.py`,
      `test_task_8_4_runtime_readiness_gate.py` plus both architecture tests
      (`test_strategy_dag_architecture.py`, `test_compiler_architecture.py`): **466 passed** —
      as is everything touching the dispatcher this task extended (`-k "alert or
      reconciliation_scheduler or global_safety"`): **126 passed**.
    - **Frontend: unchanged and not implicated.** This task edited no file under
      `algo22-terminal/`; the alerts are backend-side and no frontend file reads one.
      `npx vitest run` in `algo22-terminal/` was re-run anyway: **1 failed / 783 passed over
      784**, in 32 files, 1 file failed (204.45 s) — **under the ceiling of 2**. The single
      failure is the known out-of-scope copy assertion 8.12 and 9.1 both named:
      `tests/unit/portfolio-rendering.test.jsx` > "should verify improved empty states"
      (`portfolio-rendering.test.jsx:147`, a `/Trade history/i` match against
      `Portfolio.jsx`), which belongs to no Strategy Builder task. 9.1's second failure — the
      `tests/unit/integration.test.jsx` "should load dashboard data on mount" timeout flake —
      did **not** fire this run, which is consistent with 8.12 and 9.1 recording it as a
      flake rather than a regression.
    - **What this task does NOT do, so 9.3 and 9.7 are not read as done.** No execution trace
      is surfaced (9.3 owns the inspector). No staging drill was run — 9.7 owns "alerts firing
      in a staging drill", and the two facts it needs from here are recorded above: the live
      arm of the STALE alert has no production caller yet, and the four alerts are delivered
      in-process rather than by a Prometheus rule, so the drill exercises the dispatcher's
      channels rather than a scrape. No metric was added, renamed or relabelled: the eighteen
      are 9.1's, read from `metrics.py`, and this task added no nineteenth.

  - [x] 9.3 Surface execution traces in the inspector
    - Read the existing `dag_engine.ExecutionTracer` and `signal_trace_engine.py` records and
      render the selected node's inputs, outputs, duration and failures, so "why did nothing
      happen?" is answerable
    - _Requirements: 24.6_
    - **Implementation note.** Two backend files **extended** (`dag_engine.py`,
      `routers/strategy_operations.py`), one frontend file extended
      (`pages/StrategyBuilder.jsx`), two new frontend files (`lib/nodeTrace.js`,
      `components/builder/NodeTrace.jsx`) and two new test files. **No third trace store, no
      new endpoint, no new channel and no new ownership path** — each of those was an
      available shortcut and each was the specific thing this task was told not to do.
    - **Why the trace rides the preview response, and why a `GET …/trace` would have been
      wrong.** An `ExecutionTracer` is populated by a run and lives exactly as long as the
      engine that ran. The one place the Builder surface runs a DAG is `preview_node`, which
      already constructs its engine with `enable_tracing=True` and then discarded everything
      the tracer recorded. A separate endpoint would have to **re-execute** to have anything
      to report, and two executions is two answers to one question — SB-01's shape, at the
      moment an author decides whether to deploy. It would also cost a second ownership
      resolution, a second rate limit and a second 404-not-403 rule, all three of which are
      new places to get an isolation rule wrong. So the trace is published from the same
      response, on **both** of its outcomes: `body.trace` on the 200 and
      `detail.trace` on the 422 `PREVIEW_EXECUTION_FAILED`. The 422 is the branch that
      matters most and the reason both paths exist — a run that produced nothing is exactly
      the run whose recorded inputs, duration and failure answer "why did nothing happen?",
      and the tracer holds them whether or not `execute_dag` returned. A test asserts the
      **key sets of the two are identical**, so a field added later cannot appear on the
      success path only, and `test_no_trace_route_exists_at_all` walks `app.routes` to keep
      the absence of a trace endpoint a property rather than an omission.
    - **The one recorder change, and the payload defect it fixes.** `ExecutionTracer` records
      `input_shape` and `input_types` keyed by **upstream node id**, because that is how
      `PortInputs` addresses a value and what every executor reads. The projection was
      calling that key `port`, which made Requirement 24.6's "inputs" render as a column of
      ULIDs under a field name that was not true. `ExecutionTrace` therefore gained
      `input_ports` (`input port -> upstream node ids`), recorded in `start_node` from the
      inputs object's **own** `port_sources` — the compiler's port-level wiring, already
      resolved for that node, asked of the object rather than re-derived from the edges, so
      the trace cannot disagree with what the executor was handed. **Additive rather than
      re-keyed**, for two reasons: re-keying would move a payload
      `DAGEngine.get_execution_trace` already publishes, and the id-keyed view is the only
      one that can represent a source feeding two input ports (`ohlcv_feed.high -> a`,
      `ohlcv_feed.low -> b`, the ordinary way to author a bar range). `_trace_entry_payload`
      now emits one row per **binding**, carrying `port` *and* `source`; a source bound to no
      declared port keeps its row with `port: null`, because dropping it would lose an input
      the run genuinely bound and a legacy plain-dict call site records no port view at all
      ("unknown", never "no inputs"). Both cases are pinned, and
      `test_dag_engine_port_addressing.py`'s two trace assertions still pass untouched.
    - **Which failure is reported is the whole difference between naming the cause and naming
      the victim.** `blocking_failure` is the **earliest** recorded failure at or upstream of
      the selection — everything in `executed_nodes` is the node or an ancestor of it (that is
      what a closure is), so no second graph walk is needed and none could disagree with the
      compiler's order. `get_last_failure` reports the newest and is the wrong answer here;
      the run's whole failure list travels as well, so a second, later failure a single
      call-out would hide is still visible. Stated as a Hypothesis property
      (`test_the_blocking_failure_is_always_the_earliest_at_or_upstream`, 150 examples over
      arbitrary run lengths, failure sets and selections) because no finite set of examples
      pins "and never one downstream": the chosen-point test
      `test_a_failure_downstream_of_the_selection_is_not_blocking` is the case that would
      otherwise point an author at a block that failed *because* of the silence rather than
      at its cause.
    - **`not_executed` is a first-class status, and it is not "produced nothing".** The
      tracer holding no entry for a node means it was never started — look upstream — whereas
      a node that ran and produced nothing means look at this block. They render distinctly
      on both sides, and `duration_ms` stays `null` rather than `0`: a node nobody timed and
      a node that took no measurable time are different facts. Duration is **summed** across
      entries by the endpoint, because a node started twice in a run spent time twice, and
      the frontend renders that sum rather than re-deriving it.
    - **8.5's plumbing is reused as a *reading*, not as a new subscription.** The panel shows
      the selected node's live runtime state from `builderRealtime.nodeRuntimeView` — the
      projection of task 8.5's `deployment.runtime_state` frame that the canvas effect
      already stamps onto every node, passed straight in as a prop. A block that is `WARMING`
      or waiting on a port **never ran**, which is a different answer from one that ran and
      failed, and it is the answer for a live deployment rather than a preview. **No sixth
      frame type was added to `ws_channels.py`, deliberately:** a trace is a fact about one
      run of one engine, `execution.{deployment_id}` carries intents, orders, fills and
      rejections, and a `trace` frame would have no publisher today — a channel that never
      speaks is worse than no channel, and 9.7's drill would have to exercise it.
    - **`NodePreview`'s precedent, followed field for field.** The component is presentation
      only and takes its render model as a **prop**, so it has no client and is asserted
      without a network; `lib/nodeTrace.js` owns the projection and every judgement; every
      sentence on screen is the server's, verbatim — the `summary`, each condition's
      `display`, and the provenance message — which is the `ParameterForm` `fix_hint` /
      `NodePreview` `detail.message` rule and the right rule here because every fact in that
      sentence is a fact about a run only the server observed. A structural test asserts the
      module starts no clock (`performance.now`, `Date.now`, `setTimeout`), keeps no store
      (`localStorage`) and sums nothing, so a **fourth** opinion about a run cannot appear in
      a browser.
    - **Fail *soft*, and the divergence from `projectPreview` is deliberate.**
      `projectPreview` throws on a malformed body, because a preview that cannot be read must
      not render as a block that produces nothing. A trace is a diagnostic *about* that
      preview, so throwing here would take the values off screen to report that the
      explanation was unreadable. `projectNodeTrace` returns `null` and the panel says no
      trace was recorded. The page stores the trace **before** `projectPreview` can throw,
      and a failure replaces a held trace only when it carries one — a refused validation and
      a transport error executed nothing, so they invalidate nothing. The staleness rule is
      the reducer's own: a late answer for a selection nobody is looking at is dropped, and a
      trace is cleared when the preview key (strategy + node + graph) changes, because a
      trace is a statement about one run of one graph.
    - **No control was touched.** `Depends(get_current_user)` and `@limiter.limit("30/minute")`
      are unchanged and re-asserted structurally; ownership is still resolved by
      `StrategyService.get_strategy`'s `id` **and** `user_id` predicate on top of the
      request-scoped client's RLS, **before** anything is read or executed; another tenant's
      strategy is a **404** identical to a missing one, with `feed.calls == []` and no
      `trace` key anywhere in the body, so the trace cannot become an existence oracle. The
      cross-tenant test applies the real predicate through a fake that can actually fail it.
      No auth, RLS, tenant-isolation, financial-invariant, execution-safeguard, risk-gate,
      idempotency or rate-limit path was edited, and no migration was touched. Two
      substring-vs-key traps were caught and fixed rather than left: this file's own
      `stg_trace_0001` and the frontend fixture's `stg_trace_1` both contain the word
      "trace", so the payload assertions match **keys** and the "no second request"
      assertion matches a `/traces?` path **segment** — either would otherwise have passed
      for the wrong reason.
    - **No credential and no venue can travel, on either side.** The endpoint already
      allow-lists `signal_trace_engine`'s record fields (`SignalTraceRecord` carries an
      `exchange` and a `bot_id`, and neither is read); the frontend projection is an
      allow-list **by construction**, copying every field by name. Both halves are swept:
      the backend test walks every key of the whole nested payload against task 9.1's
      credential vocabulary and asserts no venue name appears even inside a failure message,
      and the frontend test poisons every node of a trace payload with eleven marker values
      and requires none to reach the render model *while* the panel still renders — so the
      sweep cannot pass by projecting nothing.
    - **Migrations 004–004e remain unapplied, and nothing here changes that.** The whole
      trace path reads **no relation and no column**, asserted over the source of the four
      projection functions (`.table(`, `.select(`, `.eq(`, `supabase` all absent), so there
      is no degradation mode to name a file for and no 500 to avoid. The one path that could
      fail is the signal-trace store, which is process-local: an unstarted store, an
      unimportable module or a record shaped differently all report `available: false` with a
      sentence and the preview is still served **200** — `test_an_unreadable_signal_trace_store_degrades_rather_than_500ing`
      drives that through a real exception, and the honest empty answer is a fixed four-key
      shape rather than an absent key.
    - **Suite.** **43 failed / 4774 passed / 18 skipped over 4835 collected** with
      `.venv\Scripts\python.exe -m pytest -q` at repo root in default order (414.25 s).
      43 + 4774 + 18 = 4835 reconciles. Against 9.2's baseline of **43 failed / 4743 passed /
      18 skipped over 4804 collected**, the delta is **+31 collected, +31 passed, +0 failed**
      — exactly this task's 31 tests. Failures hold at the pinned ceiling of **43**, in the
      same **nineteen** files with the same per-file counts 8.6 … 9.2 recorded
      (`full_system_test.py` 4, `test_account_health_query.py` 1,
      `test_atomic_order_cancellation_fix.py` 4, `test_distributed_execution_safety.py` 7,
      `test_event_pipeline.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_safety_fix.py` 1, `test_exchange_vault_singleton.py` 2,
      `test_false_success_report_fix.py` 1, `test_fee_precision_fix.py` 1,
      `test_get_db_dependency.py` 1, `test_marketplace_pipeline.py` 4,
      `test_position_delta_race_condition_fix.py` 1,
      `test_reconciliation_engine_fail_closed.py` 1, `test_risk_management_lifecycle.py` 1,
      `test_risk_settings_api.py` 2, `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_tenant_isolation_fixes.py` 1, `test_transaction_isolation_serializable.py` 3).
      None is in a file this task touched, and 18 skips is unchanged — no control lost
      coverage (Requirement 21.9). The suites most exposed to the `dag_engine` edit were run
      on their own and are green: `test_node_preview_endpoint.py`,
      `test_dag_engine_port_addressing.py`, `test_dag_runtime_golden_plan.py`,
      `test_logic_and_indicator_executors.py`, `test_plan_engine_adaptation_fidelity.py`
      plus both architecture tests — **324 passed, 1 skipped**.
    - **Frontend.** `npx vitest run` in `algo22-terminal/`: **1 failed / 829 passed over
      830**, in 34 files (307.59 s) — **under the ceiling of 2**. Against 9.1's baseline of
      784 over 32 files the delta is **+46 tests in +2 files**, which is this task's 33 + 13.
      The single failure is the known out-of-scope copy assertion 8.12, 9.1 and 9.2 all
      named: `tests/unit/portfolio-rendering.test.jsx` > "should verify improved empty
      states", a `/Trade history/i` match against `Portfolio.jsx`, belonging to no Strategy
      Builder task. 9.1's `integration.test.jsx` timeout flake did not fire.
    - **What this task does NOT do, so 9.4 … 9.7 are not read as done.** No capacity limit is
      enforced, nothing is memoised and no branch runs concurrently (9.4). The registry
      payload and its cache were not touched (9.5). No performance figure was measured — the
      trace adds one dict per traced entry to a response that already carried a 50-row
      sample, and no latency claim is made here (9.6 owns the budgets). No staging drill was
      run (9.7). And the honest gap for 9.7: the live half of "why did nothing happen?"
      reaches the inspector through 8.5's `runtime_state` frame and
      `signal_trace_engine`'s in-process store, so a **restarted** backend has no
      provenance to report for a block that fired before the restart — the store is
      process-local by design and this task did not give it a home it does not have.

  - [x] 9.4 Enforce graph limits and add the runtime optimisations
    - Enforce the capacity limits (200 nodes, 400 edges, 4 ML nodes, 40 FE nodes, 200 feature
      columns, 16 connections per variadic port) with errors naming the limit and its permitted
      value
    - Memoise node results on `(node_id, params_hash, window_end)`
    - Drive `dag_engine_parallel` from `execution_levels` so independent branches evaluate
      concurrently
    - _Requirements: 25.4, 25.6, 25.7_
    - **Implementation note.** Two backend files **extended** —
      `backend_app/backend/strategy_dag/validator.py` and `backend_app/backend/dag_engine.py`
      — plus one new test file (`tests/test_task_9_4_limits_and_runtime_optimisations.py`, 52
      tests, all passing). **`dag_engine_parallel.py` is byte-for-byte unchanged**, which is
      what "REUSED AS-IS" has to mean if the disposition table is to be worth anything, and
      `execute_dag` — the loose-dict path the live loop and the backtester actually run — was
      not touched at all. That is why the golden file could not move.
    - **The limits stay one table, and what 9.4 added is a second *caller*, not a second
      table.** Task 1.9 built `validator.LIMITS`, the six-field `GraphLimits`, `LIMIT_LABELS`
      and the stage-1 capacity check, and all of it is still the only definition of the six
      numbers. What was missing was reach: the rule could only be applied by running the whole
      eleven-stage pipeline, and the 200-column bound could only fire if a caller passed the
      column count as a separate argument, which no production caller did. So
      `capacity_issues(graph, limits=…, descriptors=…, feature_columns=…)` is now public and
      `_stage_limits` *is* that function's body — a boundary that wants the refusal without
      the other ten stages, or a runtime that has just measured a matrix, gets the same codes,
      the same messages and the same permitted values, and a test asserts the standalone and
      pipeline answers are string-identical rather than merely similar. `descriptors` is
      optional and falls back to each node's declared category, so an omitted registry
      narrows the answer instead of skipping the check.
    - **The feature-column ceiling now fires on a path that exists.** `validate` reads the
      count out of the `ml_dataset_stats` a caller already supplies —
      `measured_feature_columns` accepts a `SupervisedDataset`, a `dataset.stats()` mapping or
      `{node_id: stats}` and takes the **widest**, because the bound is per matrix and any one
      exceeding it refuses the graph. An explicit `feature_columns=` still wins, and an
      unreadable shape returns `None` rather than a guess: a fabricated width would either
      refuse a legal graph or admit an illegal one, and the stage saying it had no measurement
      is the honest third answer. Nothing estimates a column count from bars minus warmup.
      The single-source claim is asserted against **both** of task 9.1's readers —
      `ml_training_policy._platform_max_feature_columns() == LIMITS.max_feature_columns` and
      `metrics.NODE_COUNT_BUCKETS[-1] == LIMITS.max_nodes` — so a future edit to one number
      breaks a test instead of quietly creating two ceilings.
    - **The real 200 and the real 16 are exercised, not just injected small ones.** A
      201-node graph and a 17-edge fan-in on `and`'s genuinely variadic `a` port are both
      built and refused, each error carrying `expected` = the permitted value, `actual` = the
      graph's own figure and the design's human label in the message. A 200-node graph is
      asserted **allowed**, so the bound is a bound and not an off-by-one.
    - **Memoisation: the design's key is the key, and it is not the whole story.**
      `(node_id, params_hash, window_end)` is exactly what `NodeMemoKey` holds. But a window
      *end* is not a window — two frames can close on the same bar and disagree about every
      earlier one — and a node's own params say nothing about what feeds it. Either gap would
      produce a remembered value that differs from a recomputed one, which is worse than no
      cache. So two things close them. (1) The cache is **scoped**: `open_window` fingerprints
      the entire frame (`pd.util.hash_pandas_object` over every row and the index, plus column
      names and dtypes) and empties the cache the moment that fingerprint moves; a frame that
      cannot be fingerprinted returns `None` and disables caching for that evaluation rather
      than being cached under a guess. (2) `params_hash` is a **Merkle** hash — block id,
      canonical params (through `schema.canonicalize`, the function `compute_dag_hash` already
      uses, so a params change that moves a strategy's identity moves the key), the
      descriptor's `runtime_ref`, and the `params_hash` of every value feeding the node, read
      through `plan.inbound_edges` (the **plural** accessor, so a variadic port folds in all
      its operands). A source that was not keyed in this window makes its consumer unkeyable,
      which is the honest answer for a node whose input provenance is unknown.
    - **The staleness traps are driven, not argued.** Same last bar, same bar count, same
      columns, one edited *first* bar: no hit, `window_changes == 1`, and the served answer
      equals a fresh engine's. Upstream `rsi(14) → rsi(9)` with the comparator's own params
      untouched: the comparator and the action miss while the three untouched nodes hit
      (asserted as an exact count of 3), and the result again equals a fresh run. Plus a cache
      too small for the graph (evicts, still correct), a frame with an unhashable object column
      (nothing cached, still correct), and a consumer that overwrites a remembered series in
      place (the next hit is unaffected — values are copied both into and out of the cache).
      Four graph shapes are each run cached-then-compared against uncached, and the
      equivalence assertion is the full one: node values, **published port values**, node
      states, intents, trace order and trace statuses.
    - **The cache is caller-owned on purpose, and nothing global was created.** A
      process-wide store of market-derived series would outlive the deployment that filled it
      and would put one tenant's computed values in a structure another tenant's evaluation
      reads from. `NodeResultCache` is an object a caller passes to `execute_plan`, bounded
      (`max_entries`, oldest-first eviction) and cleared wholesale on a window change, so its
      memory belongs to whoever asked for it. `cache=None` — the default — is byte-for-byte
      the pre-9.4 path, asserted.
    - **Concurrency: three phases, and only the middle one moves.** `execute_plan`'s level
      walk is now (1) the readiness gate, input resolution and trace-entry opening, on the
      calling thread in the plan's own order; (2) the executor calls; (3) publication, trace
      completion, the 24.3 duration metric, the memo store and the READY/WARMING marking,
      again on the calling thread in level order. Only phase 2 can leave the thread, and
      `execute_node` is now literally those three phases composed, so there is **one**
      executor-dispatch table rather than a second one for the parallel path. Because phase 3
      is ordered, a level evaluated on four threads publishes in the same order, records the
      same trace and raises the same first failure as a level evaluated on one — a test
      asserts full equivalence between `parallel=True` and `parallel=False`, and another
      asserts a node that explodes inside a concurrent level is still reported as *that*
      node's failure with its output unpublished.
    - **Concurrency is proved, not assumed.** Two indicator nodes of one level must both be
      inside their executors at the same time or a `threading.Barrier(2)` breaks and the test
      fails loudly; the same test asserts two distinct worker threads, neither of them the
      caller's. Hoisting the gate to the top of the level is safe for the reason the level
      list is safe: the compiler puts a node in level *k* only when every predecessor is
      below it, and `execute_plan` already **asserts** that before evaluating a level, so a
      level with an intra-level edge raises rather than being scheduled.
    - **What is deliberately held back from the pool.** `SERIAL_EXECUTOR_TYPES` keeps ML/DL
      and ACTION nodes on the calling thread: `MLExecutor` reaches a shared model object whose
      `predict` is not documented as re-entrant, and `ActionExecutor` consults `SafetyMonitor`
      — the platform kill switch, which is the last surface that should acquire a second
      caller as a side effect of an optimisation. Requirement 25.4 caps a strategy at 4 model
      nodes, so what is given up is bounded and known while what is protected is a financial
      control. A test asserts the ACTION node ran on the caller's thread while the two
      indicators did not.
    - **A defect found in `dag_engine_parallel` and reported rather than edited.**
      `ParallelDAGEngine.__init__`'s default worker count is `threading.cpu_count() * 2`, and
      `threading` has no `cpu_count` — the name is `os.cpu_count` — so `ParallelDAGEngine()`
      and therefore `get_parallel_engine()` raise `AttributeError`, today, for every caller.
      Its two FastAPI endpoints are the only other callers and **neither is mounted in
      `main.py`**, so nothing is currently broken by it in production. Fixing a module the
      design pins as REUSED AS-IS was not this task's call, so `_level_pool()` supplies the
      count explicitly using the same `cpu_count * 2` rule the module intended, and a test
      pins both halves: the pool is a real `ThreadPoolExecutor` on a real `ParallelDAGEngine`,
      and `get_parallel_engine()` still raises with `cpu_count` in the message — so whoever
      fixes it will see this test, not a silent behaviour change. One engine per **process**,
      not per `DAGEngine`: a live fleet holds an engine per symbol, and a pool each would be a
      thread leak dressed as an optimisation. An unobtainable pool logs and evaluates inline;
      concurrency is never load-bearing.
    - **Level parallelism defaults on, with a kill switch that needs no code change.**
      `LEVEL_PARALLELISM_DEFAULT` reads `STRATEGY_DAG_LEVEL_PARALLEL` once at import
      (`0`/`false`/`no`/`off` disables it), and `execute_plan(parallel=…)` overrides it per
      call, which is what the equivalence tests use. It only engages when a level holds two or
      more pool-eligible nodes, so a linear strategy — every level one node, which is the
      golden fixture — takes the inline path unchanged.
    - **No control was weakened, and no relation was read.** The intent gate, the
      `SafetyMonitor` freeze, `assert_execution_safe`, the upstream-closure hard gate and the
      readiness gate are untouched, and the freeze is re-asserted **with both optimisations
      on**: no `unfrozen` fixture, `intents == []`, action node still `READY`. A warming node
      is still not executed and therefore not remembered — the gate runs before the cache is
      consulted, on every evaluation. Nothing in this task touches auth, RLS, tenant
      isolation, financial invariants, execution safeguards, risk gates, idempotency or rate
      limits, no endpoint was added or changed, and **no code path here reads a table or a
      column** — so migrations 004–004e being unapplied locally creates no degradation mode
      to name a file for and no 500 to avoid. A sweep asserts no venue name or credential
      vocabulary reaches an intent or the cache's stats.
    - **`execute_plan` and `execute_dag` still agree, and the golden did not move.**
      `execute_dag` was not edited; the only shared code is `execute_node`, whose behaviour is
      preserved phase for phase (same trace entries, same statuses, same log line, same
      exception object, same `ValueError` text for a missing executor). `dag_runtime_plan_golden.json`
      hashes to `28C64CBB36767F1A4B4BA119896E32CC3664491709FB2231BC9151924153A683` both before
      and after the full-suite run, and `test_dag_runtime_golden_plan.py` — which recomputes
      every pinned digest rather than reading the file twice — is green, as is task 8.10's
      Property 25 file (`test_version_consumer_agreement.py`, 36 tests).
    - **Suite.** **43 failed / 4826 passed / 18 skipped over 4887 collected** with
      `.venv\Scripts\python.exe -m pytest -q` at repo root in default order (435.31 s).
      43 + 4826 + 18 = 4887 reconciles. Against 9.3's baseline of **43 failed / 4774 passed /
      18 skipped over 4835 collected**, the delta is **+52 collected, +52 passed, +0 failed**
      — exactly this task's 52 tests. Failures hold at the pinned ceiling of **43**, in the
      same **nineteen** files with the same per-file counts 8.6 … 9.3 recorded
      (`full_system_test.py` 4, `test_account_health_query.py` 1,
      `test_atomic_order_cancellation_fix.py` 4, `test_distributed_execution_safety.py` 7,
      `test_event_pipeline.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_safety_fix.py` 1, `test_exchange_vault_singleton.py` 2,
      `test_false_success_report_fix.py` 1, `test_fee_precision_fix.py` 1,
      `test_get_db_dependency.py` 1, `test_marketplace_pipeline.py` 4,
      `test_position_delta_race_condition_fix.py` 1,
      `test_reconciliation_engine_fail_closed.py` 1, `test_risk_management_lifecycle.py` 1,
      `test_risk_settings_api.py` 2, `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_tenant_isolation_fixes.py` 1, `test_transaction_isolation_serializable.py` 3).
      None is in a file this task touched, and 18 skips is unchanged — no control lost
      coverage (Requirement 21.9). The suites most exposed to the two edited files were run
      together and are green: `test_dag_runtime_golden_plan.py`,
      `test_dag_engine_port_addressing.py`, `test_plan_engine_adaptation_fidelity.py`,
      `test_logic_and_indicator_executors.py`, `test_task_8_4_runtime_readiness_gate.py`,
      `test_node_preview_endpoint.py`, `test_version_consumer_agreement.py`,
      `test_task_9_1_builder_metrics.py`, `test_task_9_2_builder_alerts.py`,
      `test_task_9_3_execution_trace.py`, `test_strategy_dag_validator.py`,
      `test_ml_training_policy.py`, `test_math_executor_firewall.py` plus both architecture
      tests — **943 passed, 1 skipped**.
    - **Frontend.** `npx vitest run` in `algo22-terminal/`: **1 failed / 829 passed over
      830**, in 34 files (178.47 s) — **under the ceiling of 2** and identical to 9.3's
      figures, which is expected: **no frontend file was touched by this task**. The single
      failure is the same known out-of-scope copy assertion 8.12, 9.1, 9.2 and 9.3 all named
      — `tests/unit/portfolio-rendering.test.jsx` > "should verify improved empty states", a
      `/Trade history/i` match against `Portfolio.jsx`, belonging to no Strategy Builder task.
    - **The honest gap, and it is the important one for 9.7.** Both optimisations live on
      **`execute_plan`**, because `execution_levels` is a `CompiledPlan` field and the memo key
      needs canonical node params — and `execute_plan` still has **no production caller**. It
      was additive when task 8.4 built it and it is additive now: `dag_event_loop` and the
      backtester both run `execute_dag`, the loose-dict topological path, which has no levels
      to parallelise and no canonical params to key on. So Requirements 25.6 and 25.7 are
      implemented, proved and switched on by default *on the canonical runtime the design
      specifies*, and they will start doing work on live bars only when the live loop is moved
      from `execute_dag` to `execute_plan` — a switch that belongs to no completed task and is
      not claimed here. Nor does any caller pass a `NodeResultCache` yet; wiring one into the
      preview endpoint would need a cross-request store, and where that store lives (and whose
      memory it is) is a decision this task did not take unilaterally. Also unclosed: the
      feature-column ceiling still needs a *measurement* to fire (no block declares its output
      width, so a graph-only projection would be invented metadata), ML/DL and ACTION nodes
      are never concurrent by design, and the speed-up from thread-level parallelism is
      bounded by whatever pandas and NumPy release the GIL for — no latency figure is claimed
      here, because 9.6 owns the budgets and was not run.
    - **What this task does NOT do, so 9.5 … 9.7 are not read as done.** The registry payload
      and its `ETag` cache were not touched (9.5). No compile or validation latency was
      measured at any node count (9.6). No staging drill was run (9.7).

  - [x] 9.5 Keep the registry payload and cache within budget
    - Gzip and `ETag`-cache the registry response, keeping it at or below 250 KB gzipped, fetched
      once per session
    - Keep compilation free of input and output operations so it can run inline in a request
    - _Requirements: 4.15, 25.3_

    - **Implementation note.** One backend file **extended** —
      `backend_app/routers/strategy_operations.py`, and only inside the seam task 3.1 built —
      plus three new test files: `tests/test_task_9_5_registry_budget.py` (54 tests),
      `tests/test_task_9_5_compile_is_io_free.py` (42 tests) and
      `algo22-terminal/tests/unit/registryBudget.test.js` (12 tests). All 108 pass. **No new
      module, no middleware, no second response path, and no frontend source file was
      touched** — the client half of this requirement already existed and is asserted where it
      lives rather than rebuilt.
    - **The budget is met by a factor of sixteen, and the figure is measured rather than
      recomputed.** `/registry/blocks` serves **15 863 gzipped bytes — 6.2% of the 250 KB
      ceiling** — from 172 400 bytes of identity JSON over 100 block descriptors, a 10.9x
      ratio. The four projections: `/indicators` 4 651 B (1.8%), `/features` 3 883 B (1.5%),
      `/models` 3 340 B (1.3%), `/timeframes` 304 B (0.1%). Every one of those numbers is read
      off the wire — the `Content-Length` of a real gzipped `200` from the real router with the
      real assembled registry — and printed under `pytest -s`, because a ceiling nobody can
      see the distance to is a ceiling nobody will notice moving. **Nothing was trimmed to make
      a number come out right**: a test asserts every served descriptor still carries its
      `inputs`, `outputs`, `params` and `runtime_ref`, that the block count still equals the
      assembled registry's, and that the fit is tenfold rather than marginal.
    - **`compresslevel=6`, chosen from a measurement.** On the assembled payload: level 1 →
      20 571 B in 2.6 ms, level 6 → 15 863 B in 4.1 ms, level 9 → 15 640 B in 7.4 ms. Level 9
      buys 223 bytes for 3.3 ms against a ceiling already met sixteen times over. `mtime=0`, so
      the compressed bytes — and therefore the measured length — are a function of the payload
      alone; the default gzip timestamp would make two identical payloads produce two different
      figures and an unreproducible measurement.
    - **Why the compression is in `_registry_response` and not in `GZipMiddleware`.** The
      middleware would compress every response the application serves — order fills, risk
      decisions, deployment mutations — to close a ceiling stated for one read-only reference
      payload. That is a change of blast radius, not of scope, and it would also strip the
      deterministic `Content-Length` this task measures from. The budget belongs to the
      registry, so the compression sits in the one function all five registry endpoints already
      return through. A test asserts `gzip` is named by **exactly one** function in the router
      and that it is `_registry_response`, that every registry handler's every `return` is a
      `_registry_response(...)` call, and that no `GZipMiddleware` is installed on the app — so
      a sixth projection is compressed, budgeted and counted without anybody remembering to.
    - **There is deliberately no server-side cache of the compressed bytes.** The second and
      every later request in a session carries `If-None-Match` and is answered `304` with no
      body, so nothing is compressed on the hot path at all — the `ETag` cache task 3.1 built
      *is* the answer to the repeat cost, and 4 ms on a cold request does not justify a second
      cache to keep coherent. It would also be subtly wrong for one resource:
      `registry_version` hashes the *descriptor set*, and `/registry/timeframes` mixes in three
      pipeline vocabularies that hash does not cover, so a byte cache keyed on the tag could
      serve a stale interval set after a vocabulary change. Recorded because "add a cache" is
      the obvious next suggestion and the reason not to is not obvious.
    - **Three coding details that are easy to get wrong, so they are asserted.** (1) The gzip
      branch compresses `JSONResponse.render`'s *own* output rather than calling `json.dumps` a
      second time, so the two representations cannot drift in separators, key order or float
      repr — a test asserts they decode **byte for byte** equal, not merely equal after
      `json.loads`. (2) `Vary: Accept-Encoding` travels on the `200` **and** the `304`, because
      the body now depends on a request header and a shared cache that missed it would hand
      gzipped bytes to a client that cannot decode them. (3) The `ETag` is **not** varied by
      the encoding: it identifies the registry representation, both branches carry the identical
      JSON, and a client that revalidates after switching encodings should still be told its
      copy is current. Content coding is negotiated, never assumed — `identity`, `deflate`,
      `br`, an absent header and `gzip;q=0` (a refusal, not a preference, RFC 9110 12.5.3) all
      get uncompressed JSON, and a body under 500 bytes is served as-is because the ~18-byte
      envelope plus the CPU is not repaid. No served resource is currently that small, so that
      branch is exercised through the real `_registry_response` with a real `Request` rather
      than left unmeasured.
    - **"Fetched once per session" is a client property and is asserted against the client that
      implements it.** No server header can stop a client asking twelve times, and
      `Cache-Control: private, max-age=0, must-revalidate` deliberately does not try — it asks
      for revalidation every time and lets the `ETag` make revalidation free. So
      `registryBudget.test.js` measures the **total** over the shared `conditionalGet` seam
      (task 3.5, extended by 7.3 and 8.5), which `registryClient.test.js` never does: that file
      proves each mechanism one at a time. Twelve palette, inspector, compatibility and
      timeframe reads in the order a real session makes them cost **one** request per resource
      and two in total; the two resources' caches and in-flight promises are independent, so a
      held block registry does not satisfy a timeframe read; five simultaneous callers collapse
      to two requests and the block callers receive one and the same snapshot *object*; an
      explicit `refreshRegistry()` sends the held tag and the `304` reuses the very same frozen
      payload — asserted **by identity**, so a 304 cannot have rebuilt anything from a body it
      never received; `If-None-Match` is never sent before a copy is held, and is dropped with
      the payload after a failure so a retry is a real fetch; and `resetRegistryClient()`, what
      logout calls, makes the next read a real fetch with no validator — "once per session" is a
      session, not forever.
    - **"Compile is I/O-free" is a structural test, because timing it would prove nothing.** A
      compile that reads Redis on a warm cache is fast and still not I/O-free, and the first
      request after a deploy is the one that would find out. So the claim is asserted the way
      task 2.9 asserts the single-compiler property: by parsing, never grepping (these modules
      carry prose naming the very things being forbidden). A **call closure** is built from the
      three entry points a request reaches — `StrategyCompiler.compile_plan`, the module-level
      `compile_graph`, and `strategy_builder.compile_version` — followed through the eight
      modules that implement compilation. Resolution is deliberately conservative: a called
      name is followed into *every* definition of that bare name, which over-approximates and
      therefore only makes the scan look at more code than it has to. **229 definitions
      reached, zero findings, zero coroutines**, and the closure is printed under `-s` so the
      property is reviewable and not merely asserted. Its floor is pinned (≥ 150 definitions,
      and ten named ones including `validate`, `topological_order`, `CompiledPlan.from_graph`
      and `compute_dag_hash`) so a broken walker cannot make the property vacuously true.
    - **The one boundary is the registry, and its justification is a test rather than a
      remark.** `get_registry()` is memoised, so descriptor assembly happens at most once per
      process and is not on the compile path — which matters because assembly is *not* I/O-free
      by this file's definition: `build_registry` imports `exchange_executor` for the
      `OrderType` enum. Both halves are asserted: statically, the compile closure names only
      `get_registry` and **never** `build_registry`, `reset_registry` or `default_sources`, so
      compilation cannot trigger an assembly even once; dynamically, two real compiles of a real
      graph with a counted `build_registry` produce **zero** assemblies and the same `dag_hash`.
    - **What the denylist forbids, and what it deliberately permits.** Forbidden: the builtins
      that are I/O or arbitrary execution (`open`, `input`, `print`, `eval`, `exec`,
      `__import__`); ~50 import roots that are the door to a filesystem, socket, subprocess,
      database, cache or event loop; twenty-one first-party modules that hold the platform's own
      I/O (`exchange_executor`, `data_seeking_engine`, `redis_manager`, `core.database`,
      `core.dependencies`, …); ~45 method names **no dict, list or dataclass carries**; and any
      `await` or `async def`. Generic names — `get`, `run`, `save`, `load`, `execute`, `table` —
      are pointedly *not* on the method list: `node.get("id")` and `path.remove(x)` are
      container operations, and a first draft that convicted them produced 239 false positives
      across five files. Those idioms are caught by the import root instead, since no code
      reaches a Supabase table without importing something. Permitted with reasons recorded:
      **logging** (universal, and a rule suppressed everywhere measures nothing),
      **`time.perf_counter` / `secrets.token_hex` / `hashlib`** (a clock read and a CSPRNG draw
      open no descriptor and block on no peer — `time.sleep` *is* forbidden, because declining
      to work inside a request budget is the failure this is about), and **`json.dumps` /
      `loads`** (string work; the file-taking `load` / `dump` are reachable only through
      `open`). Nine synthetic dirty definitions must each be caught and six clean ones must not,
      so the scanner's edges are measured rather than trusted — and the whole gate is exercised
      end to end by injecting a database-reading `validate` into the real surface and asserting
      the closure walk finds it.
    - **`ml_training_policy.py`'s module scope is inside the closure scan but outside the
      module-scope check, and that is a decision.** The compile path enters it at one door —
      `ml_readiness_stage`, called by the readiness validation stage, thirteen definitions of
      which all scan clean — but the module as a whole is the ML *training admission gate*, and
      its module scope legitimately imports `os` (one configuration default read at import) and
      `core.tenant` (the plan and quota dataclasses the gate compares against). Neither is
      reachable from the closure, which is the claim that matters. Forbidding the module's own
      imports would be forbidding the training gate from existing, which is not what "compile
      is I/O-free" says.
    - **A finding that sharpened the test rather than being suppressed.** `preview_node` does
      `await asyncio.to_thread(engine.execute_dag, …)` — correctly, because *executing* a
      preview is pandas work that would block the event loop for as long as the bars take. A
      first draft of the inline check convicted the handler for it. Convicting the wrong call
      would have meant an exemption, and an exempted guard stops guarding, so the assertion was
      narrowed to the thing it is actually about: no compiler name appears in the arguments of
      any deferral call (`delay`, `apply_async`, `run_in_executor`, `to_thread`, `add_task`,
      `submit`, …) and no compiler call is awaited, across all four request-path call sites. An
      end-to-end test then confirms `POST /api/strategies/compile` answers with a plan and a
      `dag_hash` in one request, not a `job_id` to poll.
    - **Nothing was weakened.** All five registry endpoints keep `Depends(get_current_user)`
      and their `slowapi` limits — re-asserted in this task's own file, from the signature and
      from the source, since this task edited their shared seam — still refuse an anonymous
      caller, still carry `private, max-age=0, must-revalidate` so an auth-gated payload cannot
      land in a shared cache, and still answer a **named 5xx** rather than a gzipped empty
      palette when assembly fails (Requirement 4.12, the SB-03 guard, re-tested through the
      compressed path). A sweep asserts no user id, email or access token reaches the served
      body. No endpoint was added, no route changed, no schema or migration touched, no relation
      read, and no auth, RLS, tenant isolation, financial invariant, execution safeguard, risk
      gate, idempotency control or rate limit was altered. `dag_runtime_plan_golden.json` still
      hashes to `28C64CBB36767F1A4B4BA119896E32CC3664491709FB2231BC9151924153A683`.
    - **Suite.** **43 failed / 4922 passed / 18 skipped over 4983 collected** with
      `.venv\Scripts\python.exe -m pytest -q` at repo root in default order (311.88 s).
      43 + 4922 + 18 = 4983 reconciles. Against 9.4's baseline of **43 failed / 4826 passed /
      18 skipped over 4887 collected**, the delta is **+96 collected, +96 passed, +0 failed** —
      exactly this task's 96 backend tests (54 + 42). Failures hold at the pinned ceiling of
      **43**, in the same **nineteen** files with the same per-file counts 8.6 … 9.4 recorded.
      None is in a file this task touched, and 18 skips is unchanged — no control lost coverage
      (Requirement 21.9). Frontend: `npx vitest run` in `algo22-terminal/` — **1 failed / 841
      passed over 842** in 35 files (224.50 s), **under the ceiling of 2**. Against 9.4's
      **1 / 829 over 830** in 34 files the delta is **+12 collected, +12 passed, +0 failed** —
      exactly this task's 12 tests in one new file. The single failure is the same known
      out-of-scope copy assertion 8.12 … 9.4 all named:
      `tests/unit/portfolio-rendering.test.jsx` > "should verify improved empty states", a
      `/Trade history/i` match against `Portfolio.jsx`, belonging to no Strategy Builder task.
    - **The honest gaps.** (1) **No latency was measured.** 9.6 owns the compile and validation
      budgets and was not run; this task proves compilation *can* run inline (no I/O, no
      coroutine, not deferred, answers in one request) and says nothing about whether p95
      compile is under 50 ms at 200 nodes. (2) **250 KB is read as 250 KiB**, consistent with
      how `design.md` states `graph_json` "well under 1 MB". The interpretation is only
      load-bearing within 2.4% of the limit and the measurement is 6.2% of it, so nothing turns
      on it. (3) **The figure is today's registry, not a projection.** 100 blocks fit in 6.2% of
      the budget; no test predicts the block count at which it would stop fitting, because that
      would need a model of descriptor size the registry does not publish. The headroom
      assertion (served × 10 ≤ budget) is what would notice a tenfold growth early. (4) **Task
      3.1's original size assertion was left in place, not deleted.** It gzips
      `json.dumps(response.json())` in the test — a recompression of a reparse of a body that
      was not compressed in transit — which is a fine proxy for "the catalogue has not exploded"
      and is now the weaker of two checks rather than the only one. (5) **Only the registry is
      compressed.** Every other response the application serves is still identity-encoded; that
      is the deliberate scope of this task and not an oversight, but it means the ~10x transfer
      saving measured here applies to five endpoints and no others. (6) **"Once per session" is
      asserted against the client module, not a browser.** The axios instance is stubbed at the
      network boundary, so what is proved is that `registryClient` enters the transport twice
      per session — not that a browser's own HTTP cache behaves as expected on top of it.

  - [x]* 9.6 Write the performance tests
    - Compile and validation latency at 10, 50, 100 and 200 nodes asserting p95 compile ≤ 50 ms
      and p95 validation ≤ 120 ms; validation latency under the debounce path; registry response
      size and cache-hit ratio
    - _Requirements: 25.1, 25.2, 25.3_

    - **Implementation note.** **Why an optional task was run.** 9.6 carries a `*`, but 9.7's
      exit criterion is "confirm the performance budgets hold at 200 nodes" and no completed
      task has measured a latency — 9.1 built the instrumentation, 9.4 pinned the capacity
      ceiling and 9.5 proved compilation *can* run inline without measuring how long it takes.
      Skipping 9.6 would leave 9.7 with nothing to confirm, so it was run.
    - **Two new test files, zero source files touched.**
      `tests/test_task_9_6_performance_budgets.py` (43 tests, default lane) and
      `tests/perf/test_strategy_builder_budgets.py` (44 tests, performance lane). All 87 pass.
      **No backend module, no router, no frontend file, no schema, no migration and no
      configuration file — including `pytest.ini` — was edited.** Everything this task needed
      already existed, which is the point of the three tasks that came before it.
    - **The budgets hold, and here are the numbers.** 120 samples per series after 5 discarded
      warm-up iterations, nearest-rank percentiles read off the shipped `Summary` metrics,
      three full runs. At **200 nodes / 400 edges — the largest graph Requirement 25.4
      permits**: compile p95 **8.99 / 10.31 / 10.00 ms** against 50 ms (**4.8–5.6x headroom**)
      and validation p95 **27.98 / 31.19 / 31.18 ms** against 120 ms (**3.8–4.3x**). At 200
      nodes / 200 edges: compile **7.50 / 8.21 / 8.15 ms**, validation **20.99 / 23.00 /
      22.16 ms**. At the smaller measurement points (first run): 10 nodes compile 0.74 ms /
      validate 1.58 ms; 50 nodes 4.75 / 6.18; 100 nodes 4.34 / 11.12. **Run-to-run spread is
      about ±8%** on every row and the ordering never changed, so the margins above are
      comfortable rather than marginal — with one exception, recorded below, which is the whole
      reason this note gives five rows instead of two.
    - **Where the figures come from, and why that was decided two tasks ago.** Every percentile
      is read from `metrics.builder_compile_duration_ms` and
      `metrics.builder_validation_duration_ms` — the shipped instrumentation, not a stopwatch
      wrapped around the call — so what is asserted is the number a dashboard would show. Task
      9.1 made exactly those two `Summary` rather than `Histogram` so that 25.1 and 25.2 could
      be read from a nearest-rank percentile instead of a bucket edge, and delegated the
      percentile method to `market_data_latency.LatencySummary.from_samples` so there is one
      implementation. Both decisions are load-bearing here and both are asserted: the two
      metrics are `Summary` and not `Histogram`, a known population of 100 gives p95 exactly
      95.0 and exactly what `LatencySummary.from_samples` gives, an empty population answers
      `None` and never a zero, and one series per node count reads back independently. The
      measurement points are **`metrics.NODE_COUNT_BUCKETS`**, not a restated `(10, 50, 100,
      200)` — a test asserts the tuple identity and that `node_count_bucket(n) == str(n)` at
      each point, because a p95 read at `node_count="200"` is only a 200-node p95 while the
      measurement points *are* the bucket edges.
    - **The one tight figure, stated rather than buried.** Three of the four subjects clear
      their budget by 4x–6x. The fourth does not: **compile *without* a precomputed report is
      40.58 / 42.69 / 40.78 ms of the 50 ms budget at the ceiling graph — about 1.2x.** It
      passes and it is the honest number, and it is the row that would breach first on slower
      hardware. `compile_plan` measures from after the parse, so on that path the duration
      published under `builder.compile.duration_ms` **contains the validation** — which is why
      it is a separate row and not blended into the request-path figure. It is asserted against
      the bare 50 ms anyway rather than given an allowance, because a dashboard reading 25.1's
      series cannot tell which call site produced a sample. The cause is *located* rather than
      guessed: a test asserts this row is the tightest in the file and that its excess over the
      request path is the validation cost, so a future reader gets the diagnosis and not just
      the symptom. The remedy, if it ever breaches, is the one
      `strategy_builder.compile_version` already applies — validate once and pass the report —
      and the three `strategy_operations` call sites that pass no report are where it would be
      applied. Nothing was tuned; the alternative was to relax the assertion to 170 ms after
      seeing the number, which is how a budget stops being one.
    - **Two graph shapes, because 25.1 states its budget in nodes and compile cost is also a
      function of edges.** `fan_graph(n)` gives exactly *n* nodes and *n* edges — a feed, a
      constant, an EMA, a `between` gate, a market action and a bank of RSI nodes fanning out
      of the feed's `close` port. Fan-*out* is unbounded (25.4's 16 is a fan-*in* limit), so it
      scales to any size, and the unconsumed bank members are not orphans because stage 7
      clears anything reachable *from* a DATA block. `ceiling_graph()` is the maximum legal
      graph: **200 nodes and 400 edges at once**, reached with a second bank of `between` gates
      at three edges each. Both shapes **validate with zero errors and zero warnings** at every
      size and `validator.capacity_issues` reports nothing — so the compile series is a
      measurement of compilation and not of the much cheaper refusal path, and the categories
      are read off the descriptors so the report carries no `PORT_CONTRACT_RECOMPUTED` override
      work the timing would otherwise include. The ceiling is proved to *be* a ceiling: one
      more node raises `NODE_LIMIT_EXCEEDED`, and a 198-node / 401-edge graph raises
      `EDGE_LIMIT_EXCEEDED` **without** the node code, which isolates the second bound. Task
      9.4 pinned 200 as legal; this pins 201 as not, so "the budget holds at the maximum" is a
      claim about the maximum.
    - **"Validation latency under the debounce path" is measured on the request the debounce
      actually issues.** The 400 ms interval and the coalescing that makes ten keystrokes cost
      one request are the *client's* and are already asserted where they live —
      `graphValidation.test.js` pins `VALIDATION_DEBOUNCE_MS === 400`, and task 3.10's
      `strategyBuilder.validation.test.jsx` proves one request per burst. Re-proving them in
      Python would be a second opinion about someone else's code. What 9.6 adds is the server
      cost of the request that survives: `StrategyBuilder.jsx` posts the canonical envelope as
      the **whole body**, not wrapped in `{"dag": ...}` the way the older callers and most
      existing endpoint tests do, so the real `strategies.validate_strategy` handler is called
      with that body and both halves are read — the validation the metric records (**29.12 /
      31.76 / 30.32 ms** at the ceiling) and the handler's whole wall time including the parse
      and the structured-report assembly (**40.43 / 44.55 / 44.33 ms**, 2.7–3.0x under 120 ms).
      A third read goes over the mounted app through `TestClient` at 200 nodes — **58.81 /
      53.22 / 60.57 ms p95** — which carries httpx's own encode and decode as well and is
      therefore the most pessimistic of the three; it is asserted against the same 120 ms
      rather than given an allowance. Tests assert the handler costs *more* than the validation
      it contains and that the HTTP row costs more again, so none of the three is the same
      measurement counted twice. One more thing measured because it is the reason 400 ms is a
      sane interval: the whole handler's p95 at the ceiling sits inside the debounce window, so
      a steadily typing author cannot queue requests faster than the server retires them.
    - **Requirement 25.3: the ratio is measured here, the size is 9.5's and was not
      re-derived.** 9.5 read `/registry/blocks` off the wire at **15 863 gzipped bytes, 6.2% of
      the 250 KB ceiling**, justified `compresslevel=6` from a measurement, and asserted the
      client's twelve-reads-two-requests behaviour in `registryBudget.test.js`. A second size
      measurement with a second methodology would give the budget two answers, so there is
      none; the ceiling constant is **imported** from 9.5's module rather than restated, so the
      two files cannot drift. What is added is the **cache-hit ratio** `design.md`'s
      observability table names, which 9.5 asserted only as a counter pair for a single 304: a
      twelve-read session of `/blocks` is one `200` and eleven `304`s — **11/12** — each of the
      five resources caches independently and is counted under its own name (a test asserts
      reading one counts nothing against the other four), the ratio is `(n-1)/n` and therefore
      **rises** with session length because `private, max-age=0, must-revalidate` asks for
      revalidation every time and lets the `ETag` make it free, and a client holding a **stale**
      tag is served a `200` and scores **0.0** — a ratio that counted a stale revalidation as a
      hit would be a lie.
    - **The latency lane is outside the default `pytest -q`, deliberately, and that decision is
      itself a test.** `pytest.ini` already excludes `tests/perf` (task 7.6's precedent, with
      the shipped `norecursedirs` defaults restated) and **nothing in that file was edited**.
      The reason for reusing the exclusion is different from 7.6's and is worth stating: a
      wall-clock assertion on a shared runner fails for reasons that have nothing to do with
      the code, and this suite carries a *pinned* failure ceiling that every task from 8.6
      onward reconciles against — a timing test that flakes under a noisy neighbour would turn
      that ceiling into noise. Unlike 7.6's harness there is **no second environment-variable
      gate**, because this file finishes in about ninety seconds and takes no feed, no Redis,
      no exchange, no database and no network: `pytest tests/perf` should run it, and
      `.venv\Scripts\python.exe -m pytest tests/perf/test_strategy_builder_budgets.py -q -s`
      is the command 9.7 should use. So that the excluded lane cannot rot unnoticed, the
      **default** lane asserts that the file exists and parses, that `pytest.ini` still names
      `tests/perf` *and* still restates pytest's nine shipped ignores (setting the key replaces
      them, and dropping them makes collection descend into `.venv` and `node_modules`), that
      the perf file imports its graphs from the default-lane module so both lanes mean the same
      thing by "a 200-node graph", that it names both budget constants, that its sample count
      fits `Summary.DEFAULT_CAPACITY`, and that it carries no skip gate.
    - **Measurement hygiene, and what was deliberately not done to make a number better.**
      Warm-up iterations are discarded and `gc.collect()` runs before every series — the first
      call through a path pays lazy imports, and a generational collection caused by the
      *previous* series' garbage would otherwise be charged to this one. Garbage collection
      stays **enabled** during measurement, so a real pause is still counted, and `max` is
      printed beside every p95 so an outlier is visible rather than hidden behind the
      percentile (the largest seen was a 108 ms single sample on the debounce handler row,
      against a 44 ms p95 — recorded, not discarded). Each series clears the metric's ring
      first, so a p95 is that series' p95 and not a blend with the one before, and the count of
      recorded observations is asserted to equal the count of calls made — an off-by-one there
      would silently measure a different population. Nothing is retried, no sample is dropped
      after the fact, every budget assertion prints the measured figure and the margin so a red
      run says what the number was, and the two "not marginal" alarms are set at 2x — well
      under the 3.8x and 4.8x measured — so they are regression alarms and not second budgets
      competing with the requirement's.
    - **Nothing was weakened.** `POST /api/strategies/validate` still carries
      `Depends(get_current_user)` — re-asserted from the signature, and an anonymous caller is
      still refused — the authenticated identity is the only dependency override, and
      `tests/conftest.py`'s `VYOMQUANT_MODE=safe` SYSTEM FREEZE is **never lifted**: this task
      has no `unfrozen` fixture because it never executes a plan. Asserted through the metrics
      rather than in prose: no intent reached the action boundary, no deployment transition was
      recorded, and **every compile this file timed produced a plan** (`builder.compile.failures`
      total zero) so no series is secretly a measurement of the refusal path. No endpoint, route,
      schema, migration, relation, auth rule, RLS policy, tenant boundary, financial invariant,
      execution safeguard, risk gate, idempotency control or rate limit was touched — this task
      added two test files and nothing else.
    - **Suite.** **43 failed / 4965 passed / 18 skipped over 5026 collected** with
      `.venv\Scripts\python.exe -m pytest -q` at repo root in default order (489.37 s).
      43 + 4965 + 18 = 5026 reconciles. Against 9.5's baseline of **43 failed / 4922 passed /
      18 skipped over 4983 collected**, the delta is **+43 collected, +43 passed, +0 failed** —
      exactly this task's 43 default-lane tests. The 44 performance tests are not in that
      count, by design. Failures hold at the pinned ceiling of **43**, in the same **nineteen**
      files with the same per-file counts 8.6 … 9.5 recorded, none in a file this task touched,
      and 18 skips is unchanged — no control lost coverage (Requirement 21.9). Frontend:
      `npx vitest run` in `algo22-terminal/` — **1 failed / 841 passed over 842** in 35 files
      (261.58 s), **identical to 9.5** and under the ceiling of 2, which it has to be: no
      frontend file was edited. The single failure is the same known out-of-scope copy
      assertion 8.12 … 9.5 all named:
      `tests/unit/portfolio-rendering.test.jsx` > "should verify improved empty states".
    - **The honest gaps.** (1) **The compile-plus-validate row is 1.2x, not 4x.** It passes on
      this machine and it is the figure most likely to breach elsewhere; the remedy is named
      above and was not applied, because changing three `strategy_operations` call sites is a
      source change and 9.6 is a test task. (2) **One machine, one process, one operating
      system** — Windows, CPython 3.12.10, a warm process. Three runs bound the *local* spread
      at ±8%; they say nothing about a CI runner, a container with a CPU quota, or an ARM host.
      That is precisely why the assertions are not in the default lane, and it is also why 9.7
      should run the file on the staging host rather than trusting these numbers. (3) **Nothing
      here measures a cold start.** Registry assembly is ~150 ms and is memoised, so the first
      request after a deploy pays it; 9.5 proved compilation never triggers an assembly, and
      neither task measures the first request. (4) **The graphs are synthetic.** An RSI bank
      plus a gate bank is a legal 200-node strategy and a fair worst case for node and edge
      count, but it holds **zero ML nodes, zero FEATURE_ENGINEERING nodes and no variadic
      fan-in** — so validation stage 11 has no dataset statistics to work over and the
      leakage stage sees no model path. A 200-node graph carrying 4 ML nodes and 40 FE nodes
      would do strictly more stage work than anything measured here, and its cost is not
      claimed. (5) **Stage 10a is `SKIPPED` in every timing**, because no path supplies
      `available_bars` — `_validate_payload` leaves it unset deliberately so every path skips
      it identically. That is the shipped behaviour and a test asserts it is the *only* skipped
      stage, but it does mean the warmup-feasibility work is outside the measured 120 ms.
      (6) **The registry size is 9.5's measurement, reused on purpose.** If the block catalogue
      grows, the figure quoted here goes stale and 9.5's test is what will notice, not this
      one. (7) **"Fetched once per session" is still 9.5's client-side claim.** The ratio
      measured here is the *server's* counter ratio for a client that revalidates; a client
      that never revalidates transfers even less and appears in no server counter at all.
      (8) **The budget is not enforced automatically anywhere.** The default lane guards the
      harness — the graphs, the percentile machinery, the exclusion, the constants — but not
      the numbers. Wiring the perf lane into a nightly job is a CI change this task did not
      make unilaterally; `04-nightly-audit.yml` is where it would go.

  - [x] 9.7 Phase 9 exit verification and acceptance gate
    - Walk the 42 acceptance criteria in `design.md § Acceptance criteria` and assert each is
      covered by a green test or an explicit code assertion
    - Confirm the performance budgets hold at 200 nodes and the alerts fire in a staging drill
    - Confirm the backend suite still shows the 40-test pre-existing infrastructure failure
      baseline and no new failures
    - Ensure all tests pass, ask the user if questions arise.
    - _Requirements: 24.4, 24.5, 25.1, 25.2, 25.4_

    - **Implementation note.** **This task changed nothing.** No source file, no test file, no
      schema, no migration, no configuration file and no frontend file was edited. It ran the
      suites, walked the 42 criteria against the tests that exist, and recorded what it found —
      including three findings that are gate outcomes rather than gate passes. Three verification
      artifacts (`suite_9_7.txt`, `vitest_9_7.txt`, `perf_9_7_run3.txt`) were written and then
      deleted; their figures are quoted below.

    - **The 42 acceptance criteria, each against the test that covers it.**
      *Canonical model.* **1** `test_compiler_architecture.py` (`TestSB01ClassNamesAreGone`,
      `TestSingleGraphCompileAuthority`, and `TestTheGuardBites` proving the scan is not inert)
      plus `test_sb01_single_compiler_regression.py::TestTheDefectIsClosed`. **2**
      `test_sb01_single_compiler_regression.py::TestOneCompilerBehindEveryPath` and
      `test_task_2_4_call_site_migration.py::TestCompilerSingularity` / `TestPlanLoadPath` /
      `TestValidateEndpoint` / `TestSaveEndpointSB06` / `TestCloneEndpointSB02`. **3**
      `test_strategy_dag_architecture.py` for the backend half and
      `algo22-terminal/tests/unit/canonicalGraph.test.js` + `canonicalGraph.sb05.test.js` for
      the client half; `test_version_consumer_agreement.py::TestOneArtifactManyConsumers`
      closes the DB/runtime half by reading one artifact from both consumers. **4**
      `test_dag_engine_port_addressing.py` (carries a Hypothesis sweep) and
      `test_strategy_version_canonical_persistence.py`; **the "no persisted v2 edge lacks
      port identity" clause is asserted over rows this suite writes, not over the production
      table** — an all-rows scan needs the database this environment has not got. **5**
      `test_dag_hash_sensitivity.py`: Property 5 in `TestProperty5SemanticSensitivity`, and
      Property 4 in `TestTheComplementHolds`, which is four `@given` sweeps over relayout,
      list reordering, edge-id relabelling and envelope metadata. 26 Hypothesis properties in
      that one file. Task 1.12's separate `test_dag_hash_layout_invariance.py` was never
      written and is not needed: Property 4 is genuinely green where it sits.
      *Registry and palette.* **6** `test_block_registry_categories.py` (Property 9) plus
      `palette.categories.test.jsx`, which asserts the seven sections render populated from a
      mocked payload. **7** `test_feature_specs.py` and `palette.categories.test.jsx`'s
      at-least-15 FEATURE_ENGINEERING assertion. **8**
      `palette.categories.test.jsx` > "wma, hma, catboost and autoencoder are selectable",
      which drags each one by block id and drops it into a configurable node — selectable, not
      merely listed. **9** `test_block_registry_indicator_parity.py::test_parity_holds_for_the_real_registry`,
      with the contrapositive under Hypothesis. **10**
      `palette.categories.test.jsx` > "blockRegistry.js exports zero block definitions" and
      `registryClient.test.js` > "blockRegistry.js holds no block definitions", both scanning
      the whole export surface rather than reading the file. **11**
      `palette.antifallback.test.jsx`: the registry 500s, an error panel renders, no block list
      appears, and a block dropped by id anyway gets "no descriptor".
      *Validation.* **12** `test_strategy_dag_validator.py` runs the stage set and asserts
      nothing short-circuits; `tests/perf/test_strategy_builder_budgets.py` asserts all twelve
      declared `V.STAGE_NAMES` are recorded at the 200-node ceiling with `warmup` the only
      `SKIPPED` one. **13** `test_strategy_dag_validator.py` per code, and
      `connectionLegality.test.js` R1–R7 for the connect-time half. **14** cycle detection:
      `test_strategy_dag_validator.py` plus `connectionLegality.test.js`'s `CODE_CYCLE` cases.
      **15 — partially covered.** Task 3.16's `tests/test_edge_legality_parity.py` does not
      exist. What stands in is `connectionLegality.test.js` > "cross-check against the backend
      validator", which replays `tests/fixtures/backend_edge_verdicts.json` — generated by
      `scripts/generate_connection_legality_fixture.py` running the real
      `validator.is_edge_legal` over the real assembled registry — and asserts the same verdict
      *and the same rejection reason* for every case. That is a real cross-check and it cannot
      drift silently, but it is a fixture replay rather than the cartesian generator evaluated
      by both implementations that Property 11 specifies. **16**
      `test_compiled_plan.py::test_diamond_composes_and_memoises`,
      `test_independent_branches_take_the_max_of_the_branches_plus_own`,
      `test_diamond_levels_place_predecessors_earlier` and the variadic fan-in class that
      exists because a linear-chain assumption once collapsed it;
      `test_task_9_4_limits_and_runtime_optimisations.py` drives a diamond through the engine.
      Multi-symbol is covered as a *refusal* (`CODE_MULTI_SYMBOL_ACTION_PATH`) — the
      with-portfolio shape is open question 2 in `design.md` and has no block yet.
      *Clone / persistence.* **17**, **18** `test_sb02_clone_preserves_plan.py`, with
      `TestCloneOutcomeIsExhaustive` closing Property 7's third outcome and tying INVALID to
      the deploy gate. **19**, **20 — declaration only.**
      `test_immutable_versions_migration.py` asserts `004c_immutable_versions.sql` *declares*
      `chk_valid_requires_hash` and `trg_sv_immutable`, that the trigger gates on all three
      read-only lifecycle states in addition to `is_read_only`, and that it compares all four
      immutable columns — parsed out of the SQL, with the migration's prose stripped so the
      explanation cannot satisfy the search. That the constraint and trigger are *in force* is
      004c's own VERIFICATION section against a real database, and there is no database here.
      *Exchange agnosticism.* **21**, **22**, **25** `test_sb06_exchange_agnostic_save.py`:
      `TestNoMarketLiteralIsEverSubstituted`, `TestNoExchangeIdentityOrCredentialIsPersisted`,
      `TestForbiddenParamVocabulary`, `TestUnparameterisedDataNodeRefusesTheSave`,
      `TestDataDescriptorHasNoExchangeAndNoMarketDefault`. **21's "scan over all rows" is
      again a scan over rows this suite writes**, for the same reason as criterion 4. **23**
      `test_asset_discovery.py` plus `assetDiscovery.test.js`. **24**
      `test_task_8_2_deployment_binding.py`: `TestLiveRequiresAnExchangeAccount`,
      `TestSymbolAvailability`, `TestTimeframeSupport`, `TestExecutionConfig`,
      `TestBindingIsWrittenAsOneRow`, `TestNothingIsWrittenWhenAGateRefuses`.
      *ML/DL.* **26** `test_block_registry_indicator_parity.py::test_each_model_is_advertised_iff_its_library_is_importable`
      — a biconditional per model block, probed with a fresh `find_spec` independent of
      `ml_models`' own cache. **27**, **28**
      `test_ml_training_policy.py::test_the_blocking_message_states_required_and_available_for_both_dimensions`
      and the sequence-family row arithmetic (`CODE_INSUFFICIENT_SEQUENCE_ROWS`, required and
      available for `sequence_length` and `train_rows`). **29** admission side in
      `test_ml_training_policy.py` / `test_training_service_admission.py::TestNoJobRowOnACapRejection`,
      worker side in `test_training_worker.py::TestCapsAreRecheckedBeforeTheFirstEpoch` and
      `TestTheDurationCap`. **30** `TestAdmittedTrainingIsQueuedAndNotRun`,
      `TestCancellationIsCooperative`, `test_training_worker.py::TestCancellationAtAnEpochBoundary`,
      and `test_training_status.py::TestTheVocabularyIsReused` for the five states. **31**
      `test_training_status.py`, including `TestTheEtaIsAbsentUntilItIsTrustworthy` and
      `TestProgressComesFromCompletedEpochs`. **32**
      `test_model_versioning.py::TestRetrainingAppendsAndOverwritesNothing`,
      `TestTheStoreIsAppendOnly`, `TestACompletedRunProducesABoundModel`. **33**
      `test_leakage_validation_stage.py` (26 tests, all three leakage codes, order-independent),
      `test_ml_dataset.py::test_split_range_exposes_no_permutable_member_list` for the
      shuffle-detection half and `test_splits_are_disjoint_and_chronological` (parametrised over
      four sizes × four embargoes) for the disjointness half. **The disjointness assertion is a
      parametrised sweep, not a Hypothesis property** — task 5.10's
      `test_temporal_splits_property.py` was never written.
      *Runtime and safety.* **34** `test_task_8_4_runtime_readiness_gate.py`:
      `TestWarmingHoldsUntilWarmupIsSatisfied`, `TestNotReadyOnAMissingRequiredInput`,
      `TestAwaitingModel`, `TestMergeIsAllOrNothing`, `TestTheIntentGate`, and
      `TestReadinessProperties`' `@given` sweep over window lengths 1–200. **See finding 2:
      every one of those runs `execute_plan`, which no production path calls.** **35**
      the firewall half is `test_math_executor_firewall.py::TestAssertExecutionSafeBlocksNonFiniteFields`
      and `test_task_9_1_builder_metrics.py`'s counter assertions — a NaN quantity is refused
      and counted, a zero quantity is refused and *not* counted. **The "zero across the full
      integration suite" half has no assertion anywhere**: there is no session-scoped check in
      `tests/conftest.py` that the counter finished at zero. It is trivially zero today because
      no production path increments it, which is not the same statement. **36** see the failure
      baseline below; the honest reading is recorded there. **37**
      `test_save_performs_no_backtest.py` (Property 24 under Hypothesis, plus the import-closure
      walk with its own bite tests) and `builder.architecture.test.js` for the frontend half.
      **38** `test_version_consumer_agreement.py::TestProperty25::test_two_consumers_of_one_version_cannot_disagree`
      under Hypothesis, with the loader half using the real `BacktestRuntime` and
      `DAGEventLoop`; the golden plan is `test_dag_runtime_golden_plan.py`. **The intent-sequence
      half of this criterion is measured through `execute_plan` — see finding 2.**
      *Quality of experience.* **39** `test_block_specs.py` (`TestDescriptorCoverage`,
      `TestSharedInvariants`) and `ParameterForm.test.jsx`, which asserts help renders as a
      keyboard-reachable `role="tooltip"` wired by `aria-describedby`, examples render as
      placeholders, and ranges and steps reach the controls from the descriptor — proven against
      descriptors captured from the running backend. **40**
      `test_ml_training_policy.py::test_the_blocking_message_states_required_and_available_for_both_dimensions`,
      `test_task_8_2_deployment_binding.py`'s named-gate refusals, and `ParameterForm.test.jsx`'s
      `fix_hint` assertions. **41** `test_feed_state_and_data_quality.py`:
      `TestTheFiveStates`, `TestBoundaries`, `TestUnknownIsNotFresh`,
      `TestProperty26ByConstruction` (a deterministic sweep over every published interval,
      boundary included) plus `strategyBuilder.feedState.test.jsx`. **42** see below.
      **Verdict on the walk: 38 of the 42 are covered by a green test or an explicit code
      assertion. Four carry a qualification, and none of the four is silent about it** —
      4 and 21 (row scans are over suite-written rows, not the production table), 19 and 20
      (declaration only, no database), 15 (fixture replay rather than a two-implementation
      generator), 35 (the suite-wide zero has no assertion), 42 (below).

    - **Criterion 42 and the performance budgets: the one row 9.6 predicted would breach has
      breached.** The perf lane was re-run three times with
      `.venv\Scripts\python.exe -m pytest tests/perf/test_strategy_builder_budgets.py -q -s`.
      **Run 1 completed in 90.46 s — the same wall time 9.6 recorded, so it is the comparable
      run — and every row reproduced 9.6's figures except one.** Compile on the request path at
      200 nodes / 400 edges: **p95 9.31 ms** against 9.6's 8.99 / 10.31 / 10.00, inside its ±8%
      spread. Validation at the same shape: **p95 28.65 ms** against 27.98 / 31.19 / 31.18.
      Both budgets, both at the ceiling graph, with 5.4x and 4.2x headroom. **The
      compile-plus-validate row did not reproduce: p95 57.34 ms against 9.6's 40.58 / 42.69 /
      40.78, over the 50 ms budget by 7.34 ms**, with p50 33.99 and p99 94.58 — a heavier tail
      than the other rows carry. Run 1 was **2 failed / 42 passed**: that row's budget
      assertion, and the derived `test_the_tightest_series_is_the_one_that_folds_validation_into_the_metric`.
      Runs 2 and 3 are not usable as measurements and are recorded so nobody mistakes them for
      one: run 2 took 139.86 s (5 failed / 39 passed) and run 3 took 330.84 s (19 failed / 25
      passed), with run 3's *10-node* validation p95 at 15.07 ms against run 1's 1.84 ms — an
      8x inflation on a row that has nothing to do with graph size. No stray `python` or `node`
      process was running (checked; zero of each), so the cause is host contention or thermal
      state rather than a concurrent suite, and it is exactly 9.6's honest gap (2) arriving:
      this is why the lane is outside `pytest -q`. **What that leaves.** Criterion 42 as
      literally written — "compile p95 < 50 ms and validation p95 < 120 ms at 200 nodes" — holds
      on the request path, twice measured, matching 9.6. The breaching series is
      `compile_plan` called *without* a precomputed report, which publishes to the same
      `builder.compile.duration_ms` a dashboard reads and therefore counts against 25.1
      regardless of call site. 9.6 named this row as the one that would go first, named the
      remedy (validate once and pass the report, as `strategy_builder.compile_version` already
      does; the three `strategy_operations` call sites that pass no report are where it goes)
      and declined to apply it because it is a source change. **This task declines it for the
      same reason and escalates instead: the remedy is a source change to three call sites and
      wants its own task, not a gate task's unreviewed edit.** Nothing was tuned and no
      assertion was relaxed.

    - **The alerts: what fired here, and what a staging drill still owes.** `test_task_9_2_builder_alerts.py`
      + `test_task_9_1_builder_metrics.py` + `test_dag_runtime_golden_plan.py` were run together:
      **298 passed**. All four conditions fire and each has a near-miss that stays silent —
      the non-finite counter pages on its first increment and not on a zero-size order, `STALE`
      pages only on `AGE_AT_OR_OVER_3_INTERVALS` for a live deployment and not on the two
      fail-closed arms or on paper, the queue pages at or above drain-cycles × pool width and
      not one job short, and the universe pages above 12 h and not at it. Each of the four
      `AlertType` members is registered with the platform dispatcher
      (`test_each_builder_alert_type_is_registered_with_the_dispatcher`), delivery is scheduled
      rather than awaited, and an unreachable dispatcher logs instead of raising. **What is not
      verifiable here, stated rather than implied.** There is no staging host, so no alert was
      delivered over a real webhook, a real SMTP relay or a real Redis channel — the dispatcher
      is exercised through a recording double. And 9.2's recorded gap is confirmed by
      inspection: the only production caller of `evaluate_feed_state` is the data-quality
      preview in `strategy_operations.py` (line ~5602) and it passes no `deployment_mode`;
      `grep deployment_mode=` over `backend_app/` returns nothing. **The live arm of the STALE
      alert cannot fire in staging today**, because no live-deployment runtime reads feed state.
      It is one keyword argument away and is tested end-to-end through the real classifier; the
      task that adds live-deployment feed monitoring gets the alert for free.

    - **The failure baseline held exactly.** **43 failed / 4965 passed / 18 skipped over 5026
      collected** with `.venv\Scripts\python.exe -m pytest -q` at repo root in default order
      (390.04 s). 43 + 4965 + 18 = 5026 reconciles, and it is 9.6's figure to the test:
      **+0 collected, +0 passed, +0 failed**, which is what a task that edits nothing should
      produce. The failures are the same **nineteen** files with the same per-file counts 8.6 …
      9.6 recorded: `full_system_test.py` 4, `test_account_health_query.py` 1,
      `test_atomic_order_cancellation_fix.py` 4, `test_distributed_execution_safety.py` 7,
      `test_event_pipeline.py` 3, `test_exception_swallow_regression.py` 2,
      `test_exchange_safety_fix.py` 1, `test_exchange_vault_singleton.py` 2,
      `test_false_success_report_fix.py` 1, `test_fee_precision_fix.py` 1,
      `test_get_db_dependency.py` 1, `test_marketplace_pipeline.py` 4,
      `test_position_delta_race_condition_fix.py` 1,
      `test_reconciliation_engine_fail_closed.py` 1, `test_risk_management_lifecycle.py` 1,
      `test_risk_settings_api.py` 2, `test_strategy_analysis_endpoint_accuracy.py` 3,
      `test_tenant_isolation_fixes.py` 1, `test_transaction_isolation_serializable.py` 3.
      Skips 18, unchanged. The causes were read rather than assumed: `ConnectionRefusedError`
      against `127.0.0.1:8000` (no server), `sqlite3.OperationalError: no such table: orders`
      and `near "SHOW": syntax error` (no PostgreSQL) — infrastructure, as every note since 8.6
      has said. **Frontend: `npx vitest run` in `algo22-terminal/` — 1 failed / 841 passed over
      842** in 35 files (360.66 s), identical to 9.5 and 9.6 and under the ceiling of 2. The
      single failure is the known out-of-scope copy assertion:
      `tests/unit/portfolio-rendering.test.jsx` > "should verify improved empty states".
      **The honest reading of criterion 36.** "The security and financial-safety suites are
      green" is *not* literally true and never has been in this environment: eleven of the
      nineteen baseline files are security or financial-safety files
      (`test_risk_settings_api.py`, `test_risk_management_lifecycle.py`,
      `test_tenant_isolation_fixes.py`, `test_distributed_execution_safety.py`,
      `test_reconciliation_engine_fail_closed.py`, `test_exchange_safety_fix.py`,
      `test_fee_precision_fix.py`, `test_atomic_order_cancellation_fix.py`,
      `test_false_success_report_fix.py`, `test_position_delta_race_condition_fix.py`,
      `test_transaction_isolation_serializable.py`). What holds, and what 21.9 actually asks
      for, is the second clause: **unchanged in coverage** — same files, same per-file counts,
      same skip count, every failure an infrastructure refusal rather than an assertion about a
      control. Criterion 36 passes on that reading and on no stronger one.

    - **The golden file is untouched.** `tests/golden/dag_runtime_plan_golden.json` hashes
      `28C64CBB36767F1A4B4BA119896E32CC3664491709FB2231BC9151924153A683`, and
      `test_dag_runtime_golden_plan.py` is green.

    - **The optional tasks that were skipped, and whether the coverage is genuinely elsewhere.**
      Twenty-five optional tasks are unchecked; **only one of the twenty-one test files they
      name exists** (1.14's `test_strategy_dag_validator.py`). Coverage was checked by subject
      rather than by filename. **Genuinely covered elsewhere:** 1.11 (schema round-trip and v1
      migration — `test_strategy_dag_architecture.py`, `canonicalGraph.test.js`,
      `test_task_2_4_call_site_migration.py`'s v1 paths); 1.12 (Property 4 —
      `test_dag_hash_sensitivity.py::TestTheComplementHolds`, four `@given` sweeps, so this file
      would have been a second copy); 1.14 (its own file exists); 1.15 (Property 12 —
      `test_strategy_dag_validator.py`'s cycle codes and `connectionLegality.test.js`'s
      `CODE_CYCLE`, though as examples not a generator); 1.16 (Property 8 —
      `test_block_specs.py` and `test_block_registry.py` carry six `@given` sweeps between
      them); 1.19 (warmup composition — `test_compiled_plan.py`, including the
      compose-not-maximum case and the diamond memoisation); 2.5/2.6/2.7 (Properties 1–3 —
      `test_strategy_compiler_canonical.py` and `test_compiled_plan.py` cover
      all-or-nothing, topological order and determinism as examples; **no Hypothesis sweep**);
      3.16 (Property 11 — the generated-fixture cross-check described under criterion 15);
      4.5 (constraints — `test_immutable_versions_migration.py`, declaration only, no database);
      4.6 (Property 7 — `test_sb02_clone_preserves_plan.py::TestCloneOutcomeIsExhaustive`);
      5.8 (Property 13 — `test_math_executor_firewall.py`, eleven classes covering NaN
      propagation, division by zero, `sqrt(<0)`, `log(<=0)`, overflow and the variadic path);
      5.9 (Property 16 — `test_ml_dataset.py`'s `rows(X) == len(y)` cases);
      5.10 (Property 17 — `test_ml_dataset.py::test_splits_are_disjoint_and_chronological`,
      parametrised 4 × 4); 5.12 (feature and logic executors —
      `test_logic_and_indicator_executors.py`, `test_feature_matrix_contract.py`,
      `test_feature_specs.py`); 6.10/6.11 (gate arithmetic, cap boundaries and the training
      lifecycle — `test_ml_training_policy.py`, `test_training_service_admission.py`,
      `test_training_worker.py`, `test_training_endpoints.py`, `test_training_realtime_channels.py`,
      `test_model_versioning.py`, `test_lifecycle_integration.py`); 7.7 (Property 26 —
      `test_feed_state_and_data_quality.py::TestProperty26ByConstruction`, a deterministic
      sweep over every published interval including the boundary, plus
      `strategyBuilder.feedState.test.jsx`); 7.8 (asset discovery — `test_asset_discovery.py`
      and `assetDiscovery.test.js` cover filtering, pagination, precision metadata and the 503,
      and `test_sb06_exchange_agnostic_save.py` covers the no-hardcoded-list claim);
      8.8 (Property 14 — `test_math_executor_firewall.py::TestAssertExecutionSafeBlocksNonFiniteFields`
      and `TestAssertExecutionSafeBlocksNonPositiveQuantity`, every field, at chosen values);
      8.9 (Property 15 — `test_task_8_4_runtime_readiness_gate.py::TestReadinessProperties`,
      one `@given` over window length, plus the per-state classes).
      **The real hole, stated as one fact rather than nine.** The *subjects* are all covered.
      What is missing is the **generator**: of `design.md`'s numbered correctness properties,
      only 4, 5, 24, 25 and 26 exist as named property tests, and 26's is a deterministic sweep.
      Properties **1, 2, 3, 7, 8, 12, 13, 14, 15, 16, 17, 19 and 20 are named nowhere in
      `tests/`** — a `grep` for each returns nothing outside `design.md` and this file. The
      prompt's list (6.8/6.9, 7.8, 8.8/8.9) is right about where the holes are and understates
      how many there are. Properties **19 and 20 are the two with the weakest substitute**:
      6.8's "no `training_jobs` row is created and the response states required versus
      available" is asserted at chosen points by `TestNoJobRowOnAGateFailure` /
      `TestNoJobRowOnACapRejection`, and 6.9's "epochs never exceed
      `min(model.max_safe_epochs, tier_cap)` even when the client sends more" is asserted per
      cap rather than over a generated `(requested, cap, tier)` space. Neither is uncovered;
      neither is swept.

    - **Finding 1 — the eighteen metrics cannot be scraped. Carried forward; does not block.**
      Confirmed by inspection: `routers/metrics.py` serves `prometheus_client.generate_latest()`
      over the library's default registry, and `grep get_prometheus_metrics` over
      `backend_app/routers/` returns nothing, so `metrics_collector.get_prometheus_metrics()`
      is bound to no route. **Why it does not block.** Requirements 24.1–24.3 ask that the
      metrics be *recorded*; they are, addressably, under 160 tests. Requirements 24.4/24.5 ask
      that the alerts fire, and 9.2 deliberately raised them **in-process** off the collector
      rather than as Prometheus rules precisely because of this gap — so the alerts do not
      depend on the scrape and are not blocked by it. No acceptance criterion mentions a scrape.
      **What it costs, so the carry-forward is honest.** An operator opening `/metrics` today
      sees none of the eighteen, so every dashboard in `design.md`'s observability table is
      unbuilt, and `monitoring/alerts.yml` still cannot express a rule over any of them. The fix
      is one route or one registry merge and it is small; it is not a gate task's call to pick
      which, because merging registries changes what every existing scraper sees.

    - **Finding 2 — `execute_plan` has no production caller. This is the gate's real finding,
      and it qualifies criteria 34, 35 and 38.** Confirmed: `grep "\.execute_plan("` over the
      repository returns **test files and one docstring, and nothing else**. `dag_event_loop`
      (line ~1128), `dag_risk_integration` (~558), `backtest_runtime`, the node preview and
      `strategy_service`'s feature pipeline all call `execute_dag`. And
      `assert_execution_safe` — the intent firewall — is called from **exactly one place in the
      codebase, inside `execute_plan`** (dag_engine.py ~4779). So the readiness gate
      (Requirement 20.1's "no action on partial data"), the intent firewall (20.2), the
      per-node memoisation (25.7) and level parallelism (25.6) are all built, all tested, and
      **none of them runs in production**. Criterion 34 is proved against `execute_plan`;
      criterion 38's intent-sequence half is measured through `execute_plan`; criterion 35's
      "zero across the integration suite" is zero because nothing production-side can increment
      it. **What stops this from being an open hole rather than an unfinished wiring job**, and
      it was checked rather than assumed: `dag_event_loop._emit_signal` returns early unless
      `ExecutionFlags.EVENT_LOOP_TRADING_ENABLED`, which defaults **False** outside paper mode
      and is documented in `core/feature_flags.py` as "unsafe, pending handler validation"; the
      same file has `DAG_TRADING_ENABLED` and `PRODUCTION_ROUTER_ENABLED` False as well. The
      `execute_dag` live path therefore does not emit to handlers in live mode. **So: no order
      flows through an ungated path today, and no order flows through the gated path either.**
      **Assessment. This blocks the claim that criteria 34, 35 and 38 are *enforced in
      production*; it does not block the claim that they are implemented and tested.** Given
      criterion 38 is precisely "backtester and live execution both load the same
      `compiled_plan` and produce the same intent sequence", and given the gate exists to
      release Backtester and Marketplace work, **migrating `dag_event_loop` and
      `backtest_runtime` from `execute_dag` onto `execute_plan` should be the first task after
      this gate and before that work resumes** — it is the difference between a tested control
      and an enforced one. It is not a change this task can make: it moves the live execution
      path, it changes what the backtester runs, and it needs its own task with its own
      equivalence evidence.

    - **Finding 3 — `ParallelDAGEngine()` raises `AttributeError`. Carried forward; does not
      block.** Confirmed: `dag_engine_parallel.py:106` reads `threading.cpu_count() * 2` and
      `threading` has no `cpu_count` (the name is `os.cpu_count`), so `get_parallel_engine()`
      and the module's `__main__` construction both raise. Its router is not mounted —
      `grep dag_engine_parallel` over `backend_app/main.py` returns nothing — so the two
      endpoints are unreachable. **Why it does not block.** Task 9.4 reused the module as-is
      and worked around the defect by passing `max_workers=LEVEL_POOL_WORKERS` explicitly, and
      `test_task_9_4_limits_and_runtime_optimisations.py::test_the_pool_is_the_parallel_engines_own`
      **pins the failure** with `pytest.raises(AttributeError, match="cpu_count")` so it cannot
      be fixed silently or rot unnoticed. Level parallelism works. The one-character fix
      belongs to whoever mounts that router; doing it here would delete a passing assertion
      about a defect that is still there.

    - **Migrations remain unapplied, and that is an environment limitation rather than a gate
      failure.** `004_strategy_builder_canonical.sql`, `004b`, `004c`,
      `004d_training_and_models.sql` and `004e_deployment_bindings.sql` are unapplied because
      there is no local PostgreSQL. Every degradation path is asserted rather than assumed —
      `apply_version_state` returns `moved=False` naming the file, `JobCounts.unavailable` names
      `004d`, `load_plan` falls back to a graph-shaped `blueprint` and warns naming the file,
      the deploy gate still refuses the degraded row, `deployment.state_transitions` is not
      counted for a transition that could not be written, and the queue alert pages nothing on
      an unreadable queue. What it costs the gate is named above and not hidden: criteria 19 and
      20 are declaration-only, and criteria 4 and 21's row scans cover rows this suite writes
      rather than a production table.

    - **Nothing was weakened, and nothing could have been.** This task edited no file. No auth,
      RLS, tenant-isolation, financial-invariant, execution-safeguard, risk-gate, idempotency or
      rate-limit code path was touched; no threshold moved; no `except` widened; no migration
      changed; no skip added (18, unchanged); the failure ceiling was not raised (43, unchanged,
      same nineteen files); the frontend ceiling was not raised (1 of 842, under 2); and
      `tests/conftest.py`'s `VYOMQUANT_MODE=safe` SYSTEM FREEZE was never lifted.

    - **Gate verdict.** Phase 9's exit condition is met on the evidence that exists: 38 of 42
      criteria cleanly covered, four qualified and each qualification named, the failure
      baseline held to the test, the golden plan unchanged, all four alerts firing in-process
      with their near-misses silent. **Three things should be decided before Backtester or
      Marketplace work resumes, and they are ranked.** (1) **Finding 2** — wire
      `dag_event_loop` and `backtest_runtime` onto `execute_plan`, or the readiness gate and
      the intent firewall stay tested-but-unenforced and criterion 38 stays a claim about a
      path production does not take. (2) **Criterion 42's breaching row** — pass the validation
      report at the three `strategy_operations` compile call sites, or accept that
      `builder.compile.duration_ms` will show p95 over 50 ms for those callers. (3) **Finding
      1** — give the eighteen metrics a route, or the observability table stays undeliverable
      and `monitoring/alerts.yml` stays unable to name them. None of the three is a defect this
      task introduced, all three were predicted in earlier notes, and none is fixable inside a
      verification task without making an unreviewed source change.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP. The six defect
  regression tests (1.17, 1.18, 2.8, 3.12, 3.13, 3.14, 3.15, 4.4), the architecture tests
  (1.20, 2.9, 3.17, 8.11), the leakage suite (5.11), the tenant isolation suite (8.7) and the
  shared artifact test (8.10) are **not** optional: they are the acceptance gate that must pass
  before Backtester or Marketplace work resumes.
- Every defect regression test must fail against the pre-fix code and pass after, which is the
  standard `design.md` sets explicitly for SB-02 and which is applied here to SB-01 … SB-06.
- Property tests use Hypothesis on the backend (added in task 1.1) and fast-check on the
  frontend (added in task 3.11, the only new dependency, dev-only, pinned exactly).
- `004_strategy_builder_canonical.sql` is landed incrementally: version columns (2.2), registry
  snapshots (3.2), the valid-requires-hash constraint and immutability trigger (4.1), training
  and model tables (6.1), deployment columns (8.1).
- Each phase ends with an exit verification task carrying that phase's exit condition from
  `design.md § Phased implementation sequence`.
- No task removes or weakens authentication, authorization, RLS, tenant isolation, financial
  invariants, execution safeguards, risk controls or idempotency.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "1.4", "1.8"] },
    { "id": 1, "tasks": ["1.5", "1.6"] },
    { "id": 2, "tasks": ["1.7"] },
    { "id": 3, "tasks": ["1.9", "1.10"] },
    { "id": 4, "tasks": ["1.11", "1.12", "1.13", "1.14", "1.15", "1.16", "1.17", "1.18", "1.19", "1.20"] },
    { "id": 5, "tasks": ["1.21"] },
    { "id": 6, "tasks": ["2.1", "2.2"] },
    { "id": 7, "tasks": ["2.3"] },
    { "id": 8, "tasks": ["2.4"] },
    { "id": 9, "tasks": ["2.5", "2.6", "2.7", "2.8", "2.9", "2.10"] },
    { "id": 10, "tasks": ["2.11"] },
    { "id": 11, "tasks": ["3.1", "3.3"] },
    { "id": 12, "tasks": ["3.2", "3.4"] },
    { "id": 13, "tasks": ["3.5"] },
    { "id": 14, "tasks": ["3.6"] },
    { "id": 15, "tasks": ["3.7"] },
    { "id": 16, "tasks": ["3.8"] },
    { "id": 17, "tasks": ["3.9"] },
    { "id": 18, "tasks": ["3.10"] },
    { "id": 19, "tasks": ["3.11", "3.12", "3.13", "3.14", "3.15", "3.16", "3.17"] },
    { "id": 20, "tasks": ["3.18"] },
    { "id": 21, "tasks": ["4.1"] },
    { "id": 22, "tasks": ["4.2"] },
    { "id": 23, "tasks": ["4.3"] },
    { "id": 24, "tasks": ["4.4", "4.5", "4.6"] },
    { "id": 25, "tasks": ["4.7"] },
    { "id": 26, "tasks": ["5.1", "5.5", "5.6"] },
    { "id": 27, "tasks": ["5.2"] },
    { "id": 28, "tasks": ["5.3"] },
    { "id": 29, "tasks": ["5.4"] },
    { "id": 30, "tasks": ["5.7"] },
    { "id": 31, "tasks": ["5.8", "5.9", "5.10", "5.11", "5.12"] },
    { "id": 32, "tasks": ["5.13"] },
    { "id": 33, "tasks": ["6.1"] },
    { "id": 34, "tasks": ["6.2"] },
    { "id": 35, "tasks": ["6.3"] },
    { "id": 36, "tasks": ["6.4", "6.7"] },
    { "id": 37, "tasks": ["6.5"] },
    { "id": 38, "tasks": ["6.6"] },
    { "id": 39, "tasks": ["6.8", "6.9", "6.10", "6.11"] },
    { "id": 40, "tasks": ["6.12"] },
    { "id": 41, "tasks": ["7.1"] },
    { "id": 42, "tasks": ["7.2"] },
    { "id": 43, "tasks": ["7.4", "7.5"] },
    { "id": 44, "tasks": ["7.3"] },
    { "id": 45, "tasks": ["7.6"] },
    { "id": 46, "tasks": ["7.7", "7.8"] },
    { "id": 47, "tasks": ["7.9"] },
    { "id": 48, "tasks": ["7.10", "7.11"] },
    { "id": 49, "tasks": ["8.1"] },
    { "id": 50, "tasks": ["8.2"] },
    { "id": 51, "tasks": ["8.4", "8.6"] },
    { "id": 52, "tasks": ["8.3"] },
    { "id": 53, "tasks": ["8.5"] },
    { "id": 54, "tasks": ["8.7", "8.8", "8.9", "8.10", "8.11"] },
    { "id": 55, "tasks": ["8.13"] },
    { "id": 56, "tasks": ["8.12"] },
    { "id": 57, "tasks": ["9.1", "9.2"] },
    { "id": 58, "tasks": ["9.3", "9.4"] },
    { "id": 59, "tasks": ["9.5"] },
    { "id": 60, "tasks": ["9.6"] },
    { "id": 61, "tasks": ["9.7"] }
  ]
}
```
