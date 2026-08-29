# Implementation Plan: Trading Lifecycle Integration

## Overview

This is a **wiring and completion effort, not a greenfield build** — consistent with
`requirements.md`'s own framing and the sibling `strategy-builder` spec's precedent. The
engines this plan wires into (`BacktestRuntime`, `evaluate_binding`, `strategy_lifecycle`,
`signal_trace_engine`, `order_state_engine`, `ws_channels.OwnedChannelFamily`,
`DistributedIdempotencyLayer`, `api_key_vault`) already exist and are **reused as-is or
extended**; none of them is rebuilt. The tasks below follow the six gaps `design.md`'s
Overview identifies, in dependency order: the reconciliation module first (pure, no I/O,
nothing may wire into it before it exists), then the two schema migrations it backfills
into, then the backend endpoints that depend on that schema, then the frontend rewiring
that depends on those endpoints, then the integration suites that exercise the whole chain.

Implementation language: **Python** on the backend, **JavaScript/React** on the frontend,
**SQL** for migrations — matching the existing codebase and the sibling `strategy-builder`
spec's convention. `design.md`'s `pascal`-style `ALGORITHM`/`STRUCTURE` blocks are notation
for illustrating logic against named, existing Python/JS files; they are not a pseudocode
placeholder for an undecided language.

Rules that apply to every task:

- **Extend, do not duplicate.** `design.md § Component disposition` marks every reused
  engine REUSED AS-IS or EXTENDED. No task rebuilds `BacktestRuntime`, `evaluate_binding`'s
  existing gate functions, `strategy_lifecycle.py`'s state machines, `signal_trace_engine.py`,
  `order_state_engine.py`, or `DistributedIdempotencyLayer`'s locking algorithm.
- **No control is weakened.** No task removes or loosens authentication, authorization, RLS,
  tenant isolation, risk controls, execution guards, or idempotency (per the requirements
  document's non-functional constraints).
- **Out of scope.** Marketplace listing/subscription and Paper Trading as a distinct
  execution mode are explicitly out of scope (Requirement 27). No task in this plan
  implements either; Requirement 27's extension points (additive-only `mode` vocabulary,
  additive-only `Order_Lifecycle_State` vocabulary, nullable-only migration columns) are
  satisfied by construction in the migration and module tasks below, not by a separate task.
- **Strategy Builder is not touched.** No task modifies the canonical DAG schema, compiler,
  registry, or training pipeline (`.kiro/specs/strategy-builder/`); this plan consumes an
  immutable `Strategy_Version` as given.

## Tasks

- [x] 1. Implement the order lifecycle reconciliation module

  - [x] 1.1 Create `order_lifecycle_state.py`
    - Define the `OrderLifecycleState` enum (9 values), `TERMINAL_STATES`,
      `NON_TERMINAL_STATES`, and `ORDER_LIFECYCLE_TRANSITIONS` exactly as specified in
      `design.md § Canonical status vocabulary`
    - Implement `ORDER_STATE_MAP`, `SIGNALS_STATUS_MAP`, `TRACE_STATUS_MAP` as fixed,
      one-directional lookup tables covering every member of each source vocabulary
    - Implement `resolve_order_lifecycle_state(order_state, signals_status, trace_status)`
      applying priority order `OrderState > signals.status > TraceStatus`
    - Implement `assert_transition_legal(current, next)` against `ORDER_LIFECYCLE_TRANSITIONS`
    - No I/O, no database import, no FastAPI import — pure module, matching the
      `strategy_dag/schema.py` convention this codebase already establishes
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5_

  - [ ]* 1.2 Write property test for reconciliation mapping totality
    - `tests/test_order_lifecycle_state_property.py`
    - **Property 12: The Order_Lifecycle_State reconciliation mapping is total and
      single-valued**
    - Generator: every enum member of `OrderState`, `signals.status` (including legacy
      spellings), and `TraceStatus`, exhaustively
    - **Validates: Requirements 16.2, 16.3**

  - [ ]* 1.3 Write property test for transition reachability
    - `tests/test_order_lifecycle_transitions_property.py`
    - **Property 13: Only the diagram's transitions are reachable**
    - Generator: random pairs of canonical `Order_Lifecycle_State` values, checked against
      the fixed transition table including the `PARTIALLY_EXECUTED` self-loop
    - **Validates: Requirements 16.4**

  - [ ]* 1.4 Write property test for deterministic conflict resolution
    - `tests/test_order_lifecycle_resolution_property.py`
    - **Property 14: Conflicting source states resolve deterministically by priority**
    - Generator: random triples of `(OrderState, signals.status, TraceStatus)` readings,
      including all-agree and all-disagree cases
    - **Validates: Requirements 16.5**

  - [ ]* 1.5 Write unit tests for internal pre-submission sub-states
    - `tests/test_order_lifecycle_state_internal_substates.py`
    - Confirm `risk-check-pending` and `approved-pending-submission` — internal-only
      booleans on `RiskValidationTrace`, not enum members anywhere in the codebase — both
      map to `PENDING` per Requirement 16.3's explicit carve-out
    - _Requirements: 16.3_

- [x] 2. Land migration 005a — strategy archive

  - [x] 2.1 Create `005a_strategy_archive.sql`
    - `ALTER TABLE strategies ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL`
    - Add the partial index `idx_strategies_archived_at ON strategies(archived_at) WHERE
      archived_at IS NULL`
    - No `CHECK` constraint (a nullable timestamp is its own two-state vocabulary); leave
      existing RLS, indexes, triggers and foreign keys on `strategies` and its dependents
      untouched, satisfying Requirement 27.4's additive-only constraint
    - _Requirements: 3.2, 3.5, 21.4, 21.6_

  - [ ]* 2.2 Write the migration constraint and idempotency test
    - Assert `archived_at` is nullable, the partial index exists, and re-running the
      migration is a no-op against an already-migrated table
    - Assert no existing FK (`strategy_versions.strategy_id`, `strategy_backtests.strategy_id`,
      `strategy_deployments.strategy_id`, `signals.strategy_id`) is altered
    - _Requirements: 21.6_

- [x] 3. Land migration 005b — signal lifecycle, idempotency and transition history

  - [x] 3.1 Extend `signals` with idempotency key and canonical status
    - Add `idempotency_key TEXT` and `order_lifecycle_state TEXT` columns
    - Add `chk_signals_order_lifecycle_state` CHECK constraint against the 9-value
      vocabulary from Task 1.1
    - Add the partial unique index `uq_signals_idempotency_key ON signals(idempotency_key)
      WHERE idempotency_key IS NOT NULL`
    - Add `idx_signals_lifecycle_state` on `order_lifecycle_state`
    - _Requirements: 16.1, 16.6, 19.1, 21.2, 21.3, 21.7_

  - [x] 3.2 Create `order_lifecycle_transitions` table
    - Columns: `id, signal_id (FK, ON DELETE CASCADE), user_id (FK), from_state, to_state
      (CHECK against the 9-value vocabulary), reason, occurred_at`
    - Indexes on `(signal_id, occurred_at)` and `user_id`
    - Enable RLS with owner-scoped `SELECT`/`INSERT` policies only (no `UPDATE`, no
      `DELETE`), matching the append-only convention `signal_events` already establishes
    - _Requirements: 16.7, 21.1, 21.3, 21.4_

  - [x] 3.3 Backfill existing `signals.status` values
    - Application-code backfill (one `UPDATE` per legacy value, not a server-side function)
      mapping every existing legacy status through `SIGNALS_STATUS_MAP` from Task 1.1, so
      the mapping lives in exactly one place
    - Leave no row with a NULL `order_lifecycle_state` after the backfill completes
    - _Requirements: 16.2, 16.3_

  - [ ]* 3.4 Write the reconciliation backfill test
    - Confirm every existing `signals.status` value in a representative dataset maps to a
      value in the new 9-value vocabulary with no row left `NULL` after the backfill
    - _Requirements: 16.2, 16.3_

- [x] 4. Checkpoint - Ensure schema and reconciliation module tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement strategy archive (soft delete) and the rename endpoint

  - [x] 5.1 Implement `archive_strategy` and rewire `DELETE /api/strategies/{id}`
    - Implement `archive_strategy(sb, user, strategy_id)` per `design.md`'s algorithm:
      404 if not owned, idempotent `already_archived` response, refuse with 409 naming each
      blocking deployment in `STOPPABLE_BINDING_STATES` (imported unchanged from
      `strategy_lifecycle.py`), otherwise `UPDATE strategies SET archived_at = now()` and
      record the audit action
    - Rewire `DELETE /api/strategies/{id}` to call `archive_strategy` (soft) instead of the
      existing hard row delete
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 20.2_

  - [x] 5.2 Extend `GET /api/strategies` with `include_archived`
    - Default excludes archived strategies; `include_archived=true` includes them for
      history/audit views
    - _Requirements: 3.3_

  - [x] 5.3 Implement `PUT /api/strategies/{id}/rename`
    - New endpoint closing the confirmed-missing route the frontend already calls
    - Validate 1–100 characters after leading/trailing whitespace removal; touch only
      `strategies.name`; leave every version, backtest, deployment and signal record
      unchanged
    - Reject an archived strategy's identifier with the same disposition Requirement 3.3
      specifies for edit/backtest/deploy
    - _Requirements: 2.6, 3.3_

  - [ ]* 5.4 Write property test for archive gating
    - `tests/test_strategy_archive_property.py`
    - **Property 2: A strategy is archivable only when it has no active deployment**
    - Generator: random strategies with random deployment-state sets
    - **Validates: Requirements 3.1, 3.3**

  - [ ]* 5.5 Write unit tests for rename boundaries and archive responses
    - Rename length validation at exact boundaries (0, 1, 100, 101 characters, including
      whitespace-only input)
    - Archive: exact 409 body shape naming blocking deployments; idempotent re-archive
    - _Requirements: 2.6, 3.1, 3.6_

- [x] 6. Extend backtest execution to the canonical `BacktestRuntime` path

  - [x] 6.1 Extend `POST /strategy-operations/strategies/{id}/backtests/execute`
    - Accept `{version_id, start_date, end_date, initial_capital, ...}` and resolve the
      symbol/exchange from the version's own DATA node via the public feed
      (`ConnectionEngine` with no account), following the same pattern `preview_node`
      already uses for market previews
    - Remove the requirement that the caller reconstruct an `ExecutionGraph`; remove the
      hardcoded `ccxt.binance()` fallback
    - Run synchronously and return the persisted result directly, matching what
      `BacktestRuntime.run_backtest`'s docstring already describes as its own behavior
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 22.3_

  - [ ]* 6.2 Write property test for backtester entry-path independence
    - `tests/test_backtester_entry_path_property.py`
    - **Property 3: Backtester entry-path independence**
    - Generator: random strategy/version/configuration triples exercised via the
      Strategies_Page preselection path and the standalone navigation entry
    - **Validates: Requirements 4.7**

  - [ ]* 6.3 Write property test for configuration sanity bounds
    - `tests/test_backtest_configuration_sanity_property.py`
    - **Property 4: Universal backtest configuration sanity bounds**
    - Generator: random numeric/date configurations including boundary and negative values
    - **Validates: Requirements 6.4**

  - [ ]* 6.4 Write property test for backtest result immutability
    - `tests/test_backtest_result_immutability_property.py`
    - **Property 6: Backtest results are immutable once persisted**
    - Generator: random mutation attempts against random persisted result states
    - **Validates: Requirements 10.2**

  - [ ]* 6.5 Write the legacy-endpoint regression test
    - Confirm `POST /api/strategies/backtest` and `POST /api/strategies/{id}/deploy` remain
      reachable and unchanged, asserting their response shape is untouched by this task
    - _Requirements: 22.3_

- [x] 7. Checkpoint - Ensure all tests pass

- [x] 8. Implement collect-all deployment validation (preflight)

  - [x] 8.1 Implement `evaluate_binding_summary` in `deployment_binding.py`
    - Additive function reusing every existing `assert_*` gate function verbatim; calls
      each gate in turn, collects `PASSED`/`FAILED`/`PENDING` per condition, and marks a
      gate's dependents `PENDING` (not silently skipped) when an earlier gate it depends on
      failed
    - Keep the existing fail-fast `evaluate_binding` unchanged for the actual write path
    - _Requirements: 13.2_

  - [x] 8.2 Add `GET /strategy-operations/strategies/{id}/versions/{version}/deploy/preflight`
    - New, read-only, side-effect-free endpoint calling `evaluate_binding_summary`
    - Response shape per `design.md`: `{deployable, conditions: [{name, status, detail?,
      code?, message?, reason?}]}`
    - _Requirements: 13.3, 13.4, 13.5, 13.6_

  - [ ]* 8.3 Write property test for collect-all deployment validation
    - `tests/test_deploy_preflight_property.py`
    - **Property 9: The deployment validation gate reports every failed condition**
    - Generator: random pass/fail outcome vectors across the mandatory deployment
      conditions
    - **Validates: Requirements 13.2**

  - [ ]* 8.4 Write unit tests for preflight response shapes
    - Exact JSON shape for a fully-passed summary and a partially-failed one
    - _Requirements: 13.3_

- [x] 9. Extend the idempotency layer for the signal-to-order path

  - [x] 9.1 Add signal-scoped key derivation and configurable result TTL
    - Implement `idempotency_key_for(signal) -> str` as a deterministic, pure function of
      `signal.id` (`"signal:" + signal.id`), never a per-attempt value
    - Add an optional `result_ttl` keyword argument to
      `DistributedIdempotencyLayer.execute_with_idempotency`, defaulting to the existing
      `RESULT_TTL` so no other caller's behavior changes; introduce
      `SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS` (default 6 hours) for the trading path
    - Reuse the existing atomic Lua-script lock and owner-token compare-and-delete release
      unchanged
    - _Requirements: 19.1_

  - [ ]* 9.2 Write property test for idempotency key determinism
    - `tests/test_idempotency_key_property.py`
    - **Property 18: The idempotency key is a deterministic, collision-resistant function
      of one signal**
    - Generator: random signal identities, checked for derivation stability and
      cross-signal distinctness
    - **Validates: Requirements 19.1**

- [x] 10. Implement `signal_service.py` and wire the Live_Runtime signal path

  - [x] 10.1 Implement `generate_signal(deployment, node_output) -> Signal`
    - Mint the signal id (globally unique, immutable, never reused), persist a `GENERATED`
      row carrying every field Requirement 15.2 lists, including decision metadata and the
      idempotency key from Task 9.1
    - No exchange credential, API key, secret, passphrase, or (outside
      `exchange_account_id`) exchange identifier anywhere in the persisted record
    - If persistence fails, do not report the Signal to the frontend and do not route it to
      risk validation or order submission
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5_

  - [x] 10.2 Implement `submit_signal(signal) -> OrderLifecycleState`
    - Route through `execute_with_idempotency` (Task 9.1), `risk_engine`,
      `execution_engine`; write every transition through
      `order_lifecycle_state.assert_transition_legal` then persist (gate, then write, then
      audit)
    - On `DuplicateOrderError`, return the current `Order_Lifecycle_State` rather than
      treating it as a failure
    - On idempotency-store unavailability, hold the signal at `PENDING` and retry under the
      same key once the store recovers, never submitting unguarded
    - _Requirements: 11.2, 11.3, 16.7, 19.1, 19.5_

  - [x] 10.3 Wire the Live_Runtime's ACTION-node output into `signal_service`
    - Connect the DAG event loop's ACTION-node evaluation to `generate_signal` /
      `submit_signal`; a Signal is generated only if every upstream node in the emitting
      action's closure is `READY` and risk validation passes
    - Suspend signal generation for a deployment whose feed state is not `LIVE` beyond the
      platform's existing staleness threshold; resume automatically once the feed state
      returns to `LIVE`
    - A node evaluation error on one event aborts signal generation for that event only;
      subsequent events for that deployment continue processing
    - _Requirements: 14.3, 14.5, 14.6, 14.7, 14.8, 15.5_

  - [ ]* 10.4 Write property test for approved-signal-only order submission
    - `tests/test_signal_order_gate_property.py`
    - **Property 7: An order reaches the exchange only through an approved signal**
    - Generator: random (risk-verdict, execution-verdict) truth tables
    - **Validates: Requirements 11.2, 11.3**

  - [ ]* 10.5 Write property test for stale-feed signal suppression
    - `tests/test_signal_feed_staleness_property.py`
    - **Property 8: No signal is generated from a stale or gapped feed**
    - Generator: random deployment/feed-age combinations
    - **Validates: Requirements 14.6**

  - [ ]* 10.6 Write property test for credential containment in signal data
    - `tests/test_signal_credential_containment_property.py`
    - **Property 10: No credential or exchange identity appears in signal data**
    - Generator: random generated Signals, scanned across persisted metadata, API
      responses, and log lines
    - **Validates: Requirements 15.3, 15.4**

  - [ ]* 10.7 Write property test for signal identifier uniqueness
    - `tests/test_signal_identifier_uniqueness_property.py`
    - **Property 11: Every signal identifier is globally unique and never reused**
    - Generator: bulk-generated signal id sequences across strategies, deployments, and
      users, checked for collision
    - **Validates: Requirements 15.1**

- [x] 11. Checkpoint - Ensure all tests pass

- [x] 12. Write the crash-recovery regression suite

  - [x] 12.1 Write the worker-crash-mid-submission test
    - Assert at most one order is created at the exchange test double per Signal, the
      Signal's post-recovery `Order_Lifecycle_State` matches its true state, and no
      duplicate or orphaned record shares its idempotency key
    - _Requirements: 19.2, 19.4_

  - [x] 12.2 Write the API-process-restart test
    - Same three assertions as 12.1, exercised against a simulated process restart
    - _Requirements: 19.3, 19.4_

  - [x] 12.3 Write the WebSocket-disconnect test
    - Same three assertions, exercised against a simulated WebSocket disconnect
    - _Requirements: 19.3, 19.4_

  - [x] 12.4 Write the exchange-connection-disconnect test
    - Same three assertions, exercised against a simulated exchange connection disconnect
    - _Requirements: 19.3, 19.4_

  - [x] 12.5 Write the database/queue-restart test
    - Same three assertions, exercised against a simulated database/queue restart
    - _Requirements: 19.3, 19.4_

- [x] 13. Implement the signal-trace router

  - [x] 13.1 Create `signal_trace.py` and add `GET /api/signal-trace/signals`
    - New router (justified per Requirement 22.1: no existing router owns this resource)
    - Query params: `strategy_id, strategy_version, deployment_id, symbol, side,
      order_lifecycle_state[], date_from, date_to, limit(<=100), offset`
    - Multi-value filters combine AND-across-categories / OR-within-category
    - Enforce ownership scoping (RLS plus explicit `user_id` filter); non-owner requests
      receive the same non-existence response as a missing resource
    - _Requirements: 17.1, 17.2, 17.5, 17.7, 20.1, 21.3_

  - [x] 13.2 Add `GET /api/signal-trace/signals/{signal_id}`
    - Full trace detail: DAG node trace, ML inference (where applicable), risk validation,
      execution outcome, read from `signal_trace_engine` joined with the persisted
      `signals` row
    - _Requirements: 17.6_

  - [x] 13.3 Add `GET /api/signal-trace/signals/export`
    - CSV/JSON export matching the call `SignalTrace.jsx` already makes
    - _Requirements: 17.1_

  - [ ]* 13.4 Write property test for multi-category filter semantics
    - `tests/test_signal_filter_property.py`
    - **Property 15: Multi-category signal filters combine with AND-of-categories,
      OR-within-category semantics**
    - Generator: random signal sets and random filter-category combinations
    - **Validates: Requirements 17.2**

  - [ ]* 13.5 Write property test for signal-list pagination
    - `tests/test_signal_pagination_property.py`
    - **Property 5: Fixed-page-size pagination partitions any list without loss or
      reorder (signals, page size 100)**
    - Generator: random lists of signals of varying length, including 0, 1, and
      exact-multiple-of-page-size lengths
    - **Validates: Requirements 17.5**

- [x] 14. Implement the `SIGNAL_FAMILY` WebSocket channel

  - [x] 14.1 Add `SIGNAL_FAMILY` to `ws_channels.py`
    - `OwnedChannelFamily(namespace="signal", resource="deployment_id",
      events=SignalEvent)` following the exact pattern `EXECUTION_FAMILY` establishes
    - Reuse `core/websocket_auth.authorize_channel_subscription`'s existing `deployment_id`
      ownership lookup; add no new owner-resolution path
    - _Requirements: 18.2, 23.1, 23.2_

  - [x] 14.2 Implement per-deployment sequencing and publish signal frames
    - `ws_channels.next_signal_sequence(deployment_id)`: an in-process counter seeded from
      the deployment's own row on subscribe, assigning a monotonically increasing `seq`
      scoped to that channel
    - Every published frame carries `seq` plus a content-level key
      (`f"{signal_id}:{order_lifecycle_state}"`) for reconnect-safe dedup
    - Wire `signal_service.submit_signal`'s transitions to publish `GENERATED`,
      `STATUS_CHANGED`, `SNAPSHOT` events on `signal.{deployment_id}`
    - _Requirements: 18.1, 18.4, 23.3, 23.6_

  - [x] 14.3 Stop publishing to the legacy `ChannelType.SIGNAL_TRACE` broadcast for this
        traffic
    - `Live_Runtime` and `signal_trace_engine` stop publishing signal/order status updates
      for the Signal_Trace_Page's traffic through the legacy, unauthorized broadcast
      channel; leave that channel in place for any other consumer, unchanged
    - _Requirements: 23.7_

  - [ ]* 14.4 Write unit tests for channel naming and ownership refusal
    - `SIGNAL_FAMILY` channel name format; ownership refusal shape identical to the HTTP
      404 shape's field set; no field diverges from the existing four families' response
      conventions
    - _Requirements: 18.2, 23.2_

- [x] 15. Checkpoint - Ensure all tests pass

- [x] 16. Frontend: rewire `Strategies.jsx`

  - [x] 16.1 Re-point Deploy Live to the versioned endpoint
    - `handleOpenDeployModal` / `handleConfirmDeploy` re-pointed from
      `endpoints.strategies.deploy(id, {...})` to
      `POST /api/strategy-operations/strategies/{id}/versions/{version}/deploy` with the
      `DeploymentBindingRequest` body shape
    - Deploy button's enabled state driven by polling `GET .../deploy/preflight` (Task 8.2)
      while the modal is open
    - _Requirements: 2.2, 11.5, 13.4, 13.5, 13.6_

  - [x] 16.2 Re-point Rename to the real endpoint
    - Re-point from the raw `fetch` against the non-existent route to
      `PUT /api/strategies/{id}/rename` (Task 5.3), now real; no other change to the
      handler
    - _Requirements: 2.6_

  - [x] 16.3 Re-point Delete to the archive endpoint
    - `DELETE /api/strategies/{id}` call site unchanged; confirmation dialog text changes  
      from "delete" to "archive" language; render a 409 response as the specific error
      naming each blocking deployment
    - _Requirements: 2.9, 2.10, 3.1_

  - [x] 16.4 Compute the health indicator client-side
    - Health computed purely from `most_recent_deployment.status` and
      `most_recent_backtest.outcome` fields the list endpoint already returns;
      `undetermined` when both are absent, never a default or fabricated state
    - _Requirements: 1.8, 1.9_

  - [ ]* 16.5 Write property test for health indicator purity
    - `algo22-terminal/tests/unit/strategiesHealth.property.test.js`
    - **Property 1: Health indicator is a pure function of deployment and backtest state**
    - Generator: random `(deployment_status | None, backtest_outcome | None)` pairs
    - **Validates: Requirements 1.8, 1.9**

  - [ ]* 16.6 Write frontend tests for deploy/rename/delete
    - Deploy modal submits to the versioned endpoint (assert on request URL and body
      shape); rename succeeds against the new endpoint; delete confirmation renders archive
      language and handles the 409 blocking case
    - _Requirements: 2.2, 2.6, 2.9, 2.10_

- [x] 17. Frontend: rewire `Backtester.jsx`

  - [x] 17.1 Re-point Run to the extended execute endpoint
    - Re-pointed from `endpoints.strategies.backtest(payload)` to
      `POST /api/strategy-operations/strategies/{id}/backtests/execute` with
      `{version_id, start_date, end_date, initial_capital, ...}`; remove the
      `toCanonical()` serialization step from `runBacktest`'s call chain; consume the
      synchronous response directly rather than polling by job id
    - _Requirements: 5.1, 4.7_

  - [x] 17.2 Collapse the save-result flow
    - Remove the two-step `create_backtest` + `update_backtest_results` client-side glue;
      remove the "Save Backtest Run" button; the result appears in "Saved Backtest History"
      on completion, matching what the extended endpoint now persists server-side
    - _Requirements: 10.1_

  - [ ]* 17.3 Write property test for trade-table pagination
    - `algo22-terminal/tests/unit/tradeTablePagination.property.test.js`
    - **Property 5: Fixed-page-size pagination partitions any list without loss or
      reorder (trades, page size 25)**
    - Generator: random lists of trades of varying length, including 0, 1, and
      exact-multiple-of-page-size lengths
    - **Validates: Requirements 9.3**

  - [ ]* 17.4 Write frontend tests for run/save
    - Run button submits to `.../backtests/execute` with a `version_id`, not a
      reconstructed `dag` payload; result renders directly without a polling loop
    - _Requirements: 5.1, 4.7_

- [x] 18. Frontend: deployment configuration workflow preflight panel

  - [x] 18.1 Add the preflight conditions panel to the deploy modal
    - Small panel rendering the preflight conditions list (Task 8.2) with per-condition
      pass/fail/pending status, backed by a 2-second poll of `GET .../deploy/preflight`
      while the modal is open
    - _Requirements: 13.3, 13.4, 13.5, 13.6_

  - [ ]* 18.2 Write unit test for preflight panel rendering
    - Exact JSON-to-UI mapping for a fully-passed summary and a partially-failed one
    - _Requirements: 13.3_

- [x] 19. Frontend: `SignalTrace.jsx` realtime wiring

  - [x] 19.1 Subscribe per-deployment on the `SIGNAL_FAMILY` channel
    - Move realtime wiring from the optional embedded `SignalTraceVisualization` panel to
      the primary list/detail view; a Signal_Trace_Page filtered by strategy subscribes to
      the channel of every deployment that strategy currently has, holding one channel per
      active deployment via `websocketClient.js`'s existing ref-counted `acquire`/`release`
      and `subscribeChannel`/`unsubscribeChannel`
    - _Requirements: 17.4, 18.1, 18.2, 18.6, 18.7, 18.8_

  - [x] 19.2 Implement sequence-gap and content-key dedup in the reducer
    - Ride the existing `expectedSequence`/`messageBuffer`/`requestMessageReplay`
      gap-detection mechanism unchanged; additionally discard a frame whose content key
      (`signal_id:order_lifecycle_state`) the reducer has already applied, independent of
      `seq`
    - On reconnect, request a snapshot via `GET /api/signal-trace/signals?deployment_id=...
      &since=<last_known_seq>` rather than assuming no updates were missed
    - _Requirements: 18.4, 18.5, 23.6_

  - [x] 19.3 Add the connection-status indicator
    - Distinguish "connected", "reconnecting", and "disconnected" states visibly while the
      WebSocket connection is disconnected or attempting reconnection
    - _Requirements: 18.9_

  - [ ]* 19.4 Write property test for duplicate-delivery suppression
    - `algo22-terminal/tests/unit/signalTraceDedup.property.test.js`
    - **Property 16: Duplicate delivery never re-applies a state change**
    - Generator: random event sequences with injected exact duplicates and reordering,
      exercised against a mocked `websocketClient` emitting deliberately duplicated and
      reordered frames
    - **Validates: Requirements 18.4, 23.6**

  - [ ]* 19.5 Write property test for persisted-order display
    - `algo22-terminal/tests/unit/signalTraceOrdering.property.test.js`
    - **Property 17: Client-visible signal event order matches backend persistence order**
    - Generator: random sequences of status-change events for one signal, arriving out of
      network order
    - **Validates: Requirements 18.5**

- [x] 20. Checkpoint - Ensure all tests pass

- [x] 21. Integration: end-to-end deterministic sandbox test

  - [x] 21.1 Build and exercise the deterministic sandbox fixture
    - A single fixture strategy, built once through the existing Strategy Builder with a
      fixed seeded synthetic data source, exercised through: save → appears on
      Strategies_Page listing → `POST .../backtests/execute` completes and persists a
      `strategy_backtests` row → `POST .../versions/{v}/deploy` with `mode=paper` succeeds
      → at least one Signal is generated and reaches a terminal `Order_Lifecycle_State`
      distinguishable from a real fill → that signal appears with a full trace on
      `GET /api/signal-trace/signals`
    - Assert, by inspecting the mocked/sandboxed exchange client, that no real exchange
      order-placement call was made (call count == 0 on the real order-placement seam)
    - _Requirements: 26.1, 26.2, 26.3, 26.4_

  - [ ]* 21.2 Write property test for backtest determinism
    - `tests/test_backtest_determinism_property.py`
    - **Property 20: Repeated deterministic backtests are identical**
    - Generator: the fixed deterministic test strategy and a fixed Backtest_Configuration,
      run N >= 3 times
    - **Validates: Requirements 26.5**

- [x] 22. Integration: cross-tenant ownership suite

  - [x] 22.1 Write the cross-tenant ownership matrix test
    - A generated matrix over {strategy: list, get, versions, update, archive, duplicate} x
      {backtests: list, create, get, archive} x {deployments: list, create, get, start,
      pause, stop} x {signal trace: list, get} against every endpoint this specification
      adds or extends, asserting a non-owning authenticated user receives the identical
      non-existence response on every one
    - Enumerate the endpoint surface explicitly rather than generating requests, per
      Requirement 20.4's own wording that this is a mandated suite
    - _Requirements: 20.4_

  - [ ]* 22.2 Write property test for ownership/non-existence indistinguishability
    - `tests/test_ownership_indistinguishability_property.py`
    - **Property 19: Ownership-mismatch and non-existence are indistinguishable, on every
      transport**
    - Generator: random resource ids (strategy, version, backtest, deployment, signal,
      exchange account) matched against random non-owning users vs. random non-existent
      ids, checked over both HTTP and WebSocket subscription refusal
    - **Validates: Requirements 20.2, 23.2**

- [x] 23. Final checkpoint - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP. The three
  mandated suites (crash-recovery regression, cross-tenant ownership matrix, and the
  end-to-end deterministic sandbox exercise — Tasks 12, 21.1, 22.1) are **not** optional:
  Requirements 19.4, 20.4, and 26.4 each explicitly mandate an automated test suite of that
  shape, so they are required deliverables, not test sugar.
- Every property test corresponds to exactly one numbered Correctness Property from
  `design.md`; Property 5 appears twice (Tasks 13.5 and 17.3) because the design states it
  once but applies it to two distinct paginated lists (signals at 100/page, trades at
  25/page) under two distinct requirement clauses.
- `evaluate_binding` (fail-fast) and `evaluate_binding_summary` (collect-all, Task 8.1) are
  two calling conventions over the same unchanged gate functions — the write path keeps the
  former; only the read-only preflight surface (Task 8.2) and the frontend that polls it
  (Task 18) use the latter.
- No task in this plan implements Marketplace listing/subscription or Paper Trading as a
  distinct execution mode (Requirement 27); the extension points those features would need
  are satisfied by the additive-only migration and vocabulary design in Tasks 1–3 and
  require no separate task.
- Checkpoints ensure incremental validation; property tests validate universal correctness
  properties; unit tests validate specific examples, boundaries, and error shapes.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "1.5", "2.2", "3.1"] },
    { "id": 2, "tasks": ["3.2"] },
    { "id": 3, "tasks": ["3.3", "5.1", "6.1", "8.1", "9.1"] },
    { "id": 4, "tasks": ["3.4", "5.2", "5.3", "6.2", "6.3", "6.4", "8.2", "9.2"] },
    { "id": 5, "tasks": ["5.4", "5.5", "6.5", "8.3", "8.4", "10.1"] },
    { "id": 6, "tasks": ["10.2", "13.1", "14.1"] },
    { "id": 7, "tasks": ["10.3", "13.2", "14.2"] },
    { "id": 8, "tasks": ["10.4", "10.5", "10.6", "10.7", "13.3", "14.3"] },
    { "id": 9, "tasks": ["12.1", "12.2", "12.3", "12.4", "12.5", "13.4", "13.5", "14.4"] },
    { "id": 10, "tasks": ["16.4", "17.3"] },
    { "id": 11, "tasks": ["16.2"] },
    { "id": 12, "tasks": ["16.3"] },
    { "id": 13, "tasks": ["16.1", "17.1"] },
    { "id": 14, "tasks": ["18.1", "17.2"] },
    { "id": 15, "tasks": ["16.5", "16.6", "17.4", "18.2"] },
    { "id": 16, "tasks": ["19.1"] },
    { "id": 17, "tasks": ["19.2"] },
    { "id": 18, "tasks": ["19.3"] },
    { "id": 19, "tasks": ["19.4", "19.5"] },
    { "id": 20, "tasks": ["21.1", "22.1"] },
    { "id": 21, "tasks": ["21.2", "22.2"] }
  ]
}
```
