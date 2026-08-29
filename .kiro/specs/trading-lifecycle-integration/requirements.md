# Requirements Document

## Introduction

The Strategy Builder (`.kiro/specs/strategy-builder/`) is an existing, separately-specified
production component. It is **not** rebuilt here. This document specifies the next lifecycle
stage: taking a strategy that the Strategy Builder has already saved as an immutable version
and carrying it through the rest of its production life — listing, backtesting, live
deployment, live execution, signal generation and signal trace — using the engines that
already exist in this codebase.

**This is a wiring and completion effort, not a greenfield build.** Investigation of the
repository found that most of the individual pieces already exist:

| Piece | Status found in repo |
|---|---|
| Strategies list page (`algo22-terminal/src/pages/Strategies.jsx`) | Exists. Lists real strategies from `GET /api/strategies`. Has Backtest/Deploy/Clone/Rename/Delete actions wired to *some* backend endpoint. |
| Backtester page (`algo22-terminal/src/pages/Backtester.jsx`) | Exists. Already supports both entry paths (preselected from Strategies page via route state or `?strategy_id=`, and manual selection from the sidebar). |
| Backtester sidebar entry | Exists (`algo22-terminal/src/components/Sidebar.jsx`, route `/app/backtester`). |
| VectorBT integration (`backend_app/backend/backtesting_engine.py`) | Exists. Calls the real `vectorbt` package (`vbt.Portfolio.from_signals`) inside `asyncio.to_thread`, with a pandas-only fallback path when the package is not importable. |
| Canonical backtest runtime (`backend_app/backend/backtest_runtime.py`) | Exists. `BacktestRuntime` already wires `DAGEngine` + `BacktestEngine` (VectorBT) + `RiskEngine` + `BacktestService`, and `BacktestRuntime.load_version_plan` already reads the *same* persisted `compiled_plan` the live runtime reads (per strategy-builder Requirement 22.3/22.5), recompiling only on a hash mismatch. |
| Legacy backtest path (`routers/strategies.py` `POST /backtest`, `routers/strategy_operations.py` `POST /strategies/{id}/backtests/execute`) | Exists, and is **not** the same code path as `BacktestRuntime`. The frontend currently calls the legacy job-queue endpoint. |
| Deploy gate (`backend_app/backend/deployment_binding.py`) | Exists. `evaluate_binding` already enforces version readiness, exchange-account ownership, symbol/timeframe availability against the account's venue, mode (`paper`/`live`) rules, gap-fill policy and model-artifact/feature-schema readiness before a binding is written. |
| Legacy deploy path (`routers/strategies.py` `POST /{strategy_id}/deploy`, called `deploy_bot`) | Exists, and does **not** call `evaluate_binding`. This is what `Strategies.jsx`'s deploy modal currently calls. |
| Lifecycle state machine (`backend_app/backend/strategy_lifecycle.py`) | Exists. Defines the version lifecycle (`DRAFT`…`ARCHIVED`) and the binding lifecycle (`DEPLOYING`, `RUNNING`, `PAUSED`, `STOPPED`, `FAILED`) with legal-transition tables and an audit hook. |
| Signal trace data model (`backend_app/migrations/002_signal_trace.sql`, `003_signal_trace_restoration.sql`) | Exists. `signals` and `signal_events` tables with RLS, indexed by strategy/deployment/symbol/status. |
| Signal trace engine (`backend_app/backend/signal_trace_engine.py`) | Exists. `SignalTraceEngine` records DAG node traces, ML inference, risk validation and execution traces per signal, buffered and flushed. |
| Signal Trace frontend (`algo22-terminal/src/pages/SignalTrace.jsx`) | Exists. Filters by strategy/date, fetches list and detail, imports `websocketClient` but the realtime wiring is incomplete. |
| Order state machine (`backend_app/core/order_state_engine.py`) | Exists (`OrderState` enum: `CREATED, SUBMITTED, OPEN, PARTIAL, FILLED, CANCELLING, CANCELLED, FAILED, TIMED_OUT`). |
| A **second** status vocabulary on the `signals` table (`pending, accepted, rejected, executed, failed, cancelled, expired`) and a **third** on `SignalTraceRecord`/`TraceStatus` (`pending, running, completed, failed, blocked, timeout`) | All three exist today and do not agree with each other or with the vocabulary the user's spec names (`GENERATED, PENDING, SUBMITTED, PARTIALLY_EXECUTED, EXECUTED, CLOSED, FAILED, CANCELLED, REJECTED`). |
| Owned WebSocket channel families (`backend_app/backend/ws_channels.py`) | Exists for `training.{job_id}`, `strategy.{strategy_id}`, `deployment.{deployment_id}`, `execution.{deployment_id}` via a shared `OwnedChannelFamily` mechanism. **No such family exists yet for signal trace.** |
| Idempotency layer (`backend_app/core/distributed_idempotency.py`) | Exists (`DistributedIdempotencyLayer`). |
| Strategy delete (`routers/strategies.py` `DELETE /{strategy_id}`) | Exists, and performs a **hard delete** of the `strategies` row after stopping a running bot. No soft-delete/archive column exists on `strategies`. |
| Exchange credential vault (`backend_app/backend/api_key_vault.py`) | Exists (`store_exchange_keys`, `load_decrypted_keys`, `delete_exchange_keys`). |

**Therefore the work this specification governs is, in the main, closing four kinds of gap:**

1. **Wiring gaps** — a frontend action calls a legacy endpoint that bypasses a canonical
   engine that already exists and is more correct (deploy modal → legacy `deploy_bot`
   instead of the version-aware `evaluate_binding` gate; Backtester page → legacy
   job-queue endpoint instead of `BacktestRuntime`).
2. **Consolidation gaps** — multiple status vocabularies exist for the same concept
   (order/signal state) and must be reconciled into one reported vocabulary rather than
   having a fourth one invented on top.
3. **Missing pieces** — no soft-delete/archive on `strategies`; no owned WebSocket channel
   family for signal trace; no reproducibility snapshot captured per saved backtest;
   no deterministic idempotency key scheme documented for the signal → order path.
4. **UX completion gaps** — action buttons that do not yet reflect real deployment state,
   confirmation dialogs, pre-deployment validation summaries, and empty/loading/error
   states across the four pages in this lifecycle.

**Scope boundary.** This document covers: the Strategies page and its actions; the
Backtester's integration with the canonical version and the real VectorBT engine; backtest
persistence and reproducibility; live deployment binding and validation; live market data
delivery to the strategy runtime; signal generation and the canonical signal/order status
model; the Signal Trace page and its realtime channel; idempotency and crash recovery for
the signal → order path; and the security, database and API conventions that tie these
together. It reuses, and does not duplicate: the DAG compiler/runtime, `execution_engine`,
`risk_engine`, `execution_guard`, CCXT integration, `market_data_validation`, and
`credential_vault`/`api_key_vault`. It does not rebuild the Strategy Builder, its canonical
graph schema, its registry, or its training pipeline — all defined in
`.kiro/specs/strategy-builder/`.

**Explicitly out of scope for this specification:** Marketplace listing and subscription
workflows, and Paper Trading as a distinct execution mode. Both already have partial
existing surface (`StrategyMarketplace.jsx`, `library.py`, `paper.js`) that is untouched by
this specification. Requirement 27 records the extension points this specification must
preserve so that both can be added later without redesigning the pieces this specification
does touch.

---

## Glossary

- **Strategy**: The owned, named entity a user creates in the Strategy Builder (`strategies` table row): identity, ownership, current version pointer, lifecycle status, timestamps.
- **Strategy_Version**: An immutable, versioned snapshot of a strategy's canonical graph and compiled plan (`strategy_versions` table row), as defined and produced by the Strategy Builder. This specification consumes versions; it does not define how they are produced.
- **Version_Consumer**: A component that loads an immutable Strategy_Version to execute it: the Backtest_Engine or the Live_Runtime. Both must load the same artifact (strategy-builder Requirement 22.3/22.5).
- **Strategies_Page**: The frontend surface (`Strategies.jsx`) listing a user's strategies and exposing per-strategy actions.
- **Backtest_Engine**: The existing `BacktestRuntime` + `BacktestEngine` (VectorBT) + `DAGEngine` + `RiskEngine` pipeline that executes an immutable Strategy_Version against historical data and produces a Backtest_Result.
- **Backtest_Configuration**: The set of parameters a user supplies to run a backtest: strategy version, symbol, market type, data feed, timeframe, date range, capital, sizing, fees, slippage, and any other parameter the Backtest_Engine actually accepts.
- **Backtest_Result**: The persisted, immutable outcome of one backtest run: configuration, metrics, trade list, and reproducibility metadata (`strategy_backtests` table row plus its trade detail).
- **Deployment_Gate**: The existing `evaluate_binding` validation in `deployment_binding.py` that must pass before a Strategy_Version may be bound to a running deployment.
- **Deployment**: A running or previously-running binding of one immutable Strategy_Version to one exchange account, under one risk and execution configuration (`strategy_deployments` table row).
- **Exchange_Account**: A user's previously connected, credentialed exchange connection, referenced by identifier only. Credentials never travel through the strategy definition or the deployment request body.
- **Live_Runtime**: The DAG event loop and DAG engine executing a Deployment's compiled plan against live market data.
- **Signal**: One decision emitted by a Live_Runtime's ACTION node: a proposed trade intent, given a unique identifier, persisted, and carried through risk validation and (if permitted) order execution.
- **Signal_Trace**: The complete, ordered record of one Signal's life: DAG node trace, ML inference (if applicable), risk validation, execution outcome, keyed by a stable identifier and consumed by the Signal_Trace_Page.
- **Signal_Trace_Page**: The frontend surface (`SignalTrace.jsx`) listing and filtering signals and displaying their trace detail, updated in realtime.
- **Order_Lifecycle_State**: The canonical, single reported state of one signal/order pair, reconciling the three vocabularies found in the codebase (`OrderState`, `signals.status`, `TraceStatus`) into the one vocabulary this specification's Requirement 16 defines.
- **Idempotency_Key**: A deterministic identifier that makes a repeated request, retry or replay of the same signal/order intent produce at most one order, reusing `DistributedIdempotencyLayer`.
- **Owned_Channel**: A parameterised WebSocket channel authorised against the identity of the resource it names, following the existing `OwnedChannelFamily` pattern in `ws_channels.py`.
- **Persistence_Layer**: The database-level enforcement layer underlying the Strategy_Builder_API — foreign-key constraints, uniqueness constraints, indexes, and row-level security policies applied directly at the database — referenced by Requirements 16 and 21.

---

## Requirements

### Requirement 1: Strategies Page reflects only real, current backend state

**User Story:** As a strategy owner, I want the Strategies page to show me the true current
state of every strategy I own, so that I never act on stale or fabricated information.

#### Acceptance Criteria

1. THE Strategies_Page SHALL populate its list exclusively from a backend query scoped to the authenticated user's own strategies; it SHALL NOT render any hardcoded, sample or placeholder strategy row.
2. THE Strategies_Page SHALL display, for each strategy, its name, its lifecycle status, its current version identifier, its creation timestamp, its last-updated timestamp, its deployment state (if any active or most recent Deployment exists), and the outcome summary of its most recent Backtest_Result (if any exists); each of these fields SHALL reflect the corresponding backend record's value without transformation, default substitution, or fabrication.
3. IF a strategy has never been backtested, THEN THE Strategies_Page SHALL display an explicit "not yet backtested" state rather than a zero-valued or blank metric.
4. IF a strategy has never been deployed, THEN THE Strategies_Page SHALL display an explicit "not deployed" state rather than a fabricated running/stopped label.
5. THE Strategies_Page SHALL NOT render an action control for a capability the backend does not currently support for that strategy in its current state (for example, a Deploy action on a strategy with no version at `READY` lifecycle state, or a Pause action on a strategy with no active Deployment).
6. WHEN the strategies list request fails, THE Strategies_Page SHALL render an explicit error state with a retry action, and SHALL NOT render a stale or fabricated list in its place.
7. WHEN a strategy list is empty, THE Strategies_Page SHALL render an explicit empty state distinguishing "no strategies exist yet" from "no strategies match the current filter".
8. THE Strategies_Page SHALL compute each strategy's health indicator exclusively from that strategy's most recent Deployment status and/or most recent Backtest_Result outcome, using no other data source and no fabricated or default value.
9. IF a strategy has neither a Deployment record nor a Backtest_Result record, THEN THE Strategies_Page SHALL display that strategy's health indicator as "undetermined" rather than a default, zero-valued, or fabricated state.
10. WHEN the user activates the manual refresh control on the Strategies_Page, THE Strategies_Page SHALL re-query the backend for the current strategies list and re-render the displayed list, deployment states, backtest outcomes, and health indicators using the returned data.

---

### Requirement 2: Strategy actions perform the correct backend operation

**User Story:** As a strategy owner, I want each action button on a strategy to do exactly
what it says and nothing else, so that I do not lose data or trigger an unintended operation.

#### Acceptance Criteria

1. WHEN a user selects Backtest for a strategy, THE Strategies_Page SHALL navigate to the Backtester route with that strategy's identifier carried as a query parameter (`/app/backtester?strategy_id=<id>`), and the Backtester SHALL preselect that strategy and its current version without requiring the user to search for it again.
2. WHEN a user selects Deploy Live for a strategy, THE Strategies_Page SHALL open the deployment configuration workflow (Requirement 13) for that strategy's current READY version; it SHALL NOT deploy without that workflow's explicit confirmation. IF the strategy has no version in the READY lifecycle state, THEN THE Strategies_Page SHALL prevent the Deploy Live action from opening the deployment configuration workflow and SHALL indicate that a READY version is required.
3. WHEN a user selects View Signal Trace for a strategy, THE Strategies_Page SHALL navigate to the Signal_Trace_Page filtered to that strategy's identifier.
4. WHEN a user selects Edit for a strategy, THE Strategies_Page SHALL load that strategy's current version's canonical graph into the existing Strategy Builder for editing, following the Strategy Builder's own edit-versus-fork rule for a read-only (deployed/running/paused) version.
5. WHEN a user selects Duplicate for a strategy, THE Strategies_Page SHALL create a new, independently owned strategy carrying a copy of the source strategy's current version, with the new strategy's display name derived from the source strategy's name plus a distinguishing suffix (e.g., "(Copy)") that the user may change afterward via Rename, and SHALL NOT create a reference or alias that shares mutable state with the source.
6. WHEN a user submits a Rename for a strategy with a name between 1 and 100 characters after leading/trailing whitespace is removed, THE Strategies_Page SHALL update only the strategy's display name and SHALL leave every version, backtest, deployment and signal record for that strategy unchanged. IF the submitted name is empty after whitespace removal or exceeds 100 characters, THEN THE Strategies_Page SHALL reject the rename, SHALL retain the previous display name, and SHALL indicate the reason for rejection.
7. WHEN a user selects View Versions/History for a strategy, THE Strategies_Page SHALL display every persisted version of that strategy in creation order, each labeled with its version number, creation timestamp and lifecycle state.
8. WHEN a user selects Delete, Deploy Live, Stop, or Pause for a strategy, THE Strategies_Page SHALL require the user to explicitly confirm the action, with the confirmation step naming the selected action and its consequence, before THE Strategies_Page issues the corresponding backend request.
9. WHEN a user selects Delete for a strategy and confirms the action, THE Strategies_Page SHALL remove that strategy, together with all of its versions, backtests, and signal records, from the strategy list.
10. IF a strategy selected for Delete has a deployment in the running or paused lifecycle state, THEN THE Strategies_Page SHALL prevent the deletion, SHALL retain the strategy and its records unchanged, and SHALL indicate that active deployments must be stopped before the strategy can be deleted.

---

### Requirement 3: Strategy deletion is safe and preserves financial/audit history

**User Story:** As a strategy owner and as a compliance-conscious operator, I want deleting a
strategy to never destroy the record of what it actually did, so that historical backtests,
deployments and signals remain inspectable even after the strategy itself is removed from my
active list.

#### Acceptance Criteria

1. IF a strategy has one or more active Deployments in a running or paused state, THEN THE Strategies_Page and Strategy_Builder_API SHALL refuse a delete request for that strategy, identifying each blocking Deployment by its identifier and current state, and SHALL leave the strategy and all its associated records (versions, backtests, deployments, signals) unchanged until every blocking Deployment has been stopped.
2. WHEN a strategy delete request is accepted, THE Strategy_Builder_API SHALL perform a soft delete (archival) of the strategy record rather than a hard row deletion, preserving the strategy's identifier, its versions, its backtests, its deployments and its signals.
3. WHEN a strategy is archived, THE Strategies_Page SHALL exclude it from the default strategy list and SHALL NOT navigate to, backtest, or deploy an archived strategy; THE Strategy_Builder_API SHALL reject any edit, rename, backtest, or deploy request that targets an archived strategy's identifier.
4. IF a user attempts to reference an archived strategy's identifier through a direct API call, THEN THE Strategy_Builder_API SHALL respond distinguishing "archived" from "not found" only where the requirement to avoid leaking existence to a non-owner (Requirement 20) does not apply; for the strategy's own owner, an archived strategy remains readable for historical/audit purposes.
5. THE Strategy_Builder_API SHALL NOT physically delete any `strategy_backtests`, `strategy_versions`, `strategy_deployments` or `signals` row as a side effect of archiving its parent strategy.
6. IF a delete/archive request targets a strategy that is already archived, THEN THE Strategy_Builder_API SHALL respond indicating the strategy is already archived, SHALL NOT perform any further archival action or state change, and SHALL leave all existing archived data unchanged.

---

### Requirement 4: Backtester is reachable from both a preselected and a manual entry path

**User Story:** As a strategy owner, I want to reach the Backtester either directly from a
strategy I am looking at or independently from the main navigation, so that both my common
workflows are supported without duplicated logic.

#### Acceptance Criteria

1. WHERE the user is authorized to run backtests, THE application navigation SHALL present a dedicated Backtester entry with its own icon, label, active-route highlighting when the current route is `/app/backtester`, and a route of `/app/backtester`.
2. IF the user is not authorized to run backtests, THEN THE application navigation SHALL disable the Backtester entry and SHALL display an explanatory message indicating the user lacks permission to run backtests when the user interacts with the disabled entry.
3. WHEN the Backtester is reached from the Strategies_Page with a strategy preselected, and that strategy and its current version are still persisted, THE Backtester SHALL load that strategy's current version and SHALL allow the user to change the selected version to any other persisted version of that strategy.
4. WHEN the Backtester is reached from the navigation entry with no strategy preselected, THE Backtester SHALL present a strategy selector populated from the same backend listing the Strategies_Page uses.
5. IF the preselected strategy referenced when entering from the Strategies_Page no longer exists or has no persisted current version, THEN THE Backtester SHALL display an error message indicating the strategy or version could not be loaded and SHALL present the strategy selector described in Criterion 4 as a fallback.
6. IF the backend listing used to populate the strategy selector returns zero strategies, THEN THE Backtester SHALL display a message indicating no strategies are available for backtesting.
7. WHEN the Backtester is configured, run, or queried for results, THE Backtester SHALL produce identical behavior and results for the same strategy, version and configuration regardless of whether it was reached via the Strategies_Page preselection path or the navigation entry path.

---

### Requirement 5: Backtesting executes the same canonical artifact and the same engine as live execution

**User Story:** As a strategy owner, I want my backtest to run the actual strategy I built,
using the platform's real simulation engine, so that a backtest result is a trustworthy
predictor of what live execution would do with the same strategy.

#### Acceptance Criteria

1. THE Backtester SHALL execute a backtest through the existing `BacktestRuntime` pipeline (`Strategy_Version → Strategy_Compiler → DAG_Engine → BacktestEngine (VectorBT) → RiskEngine → BacktestService`), and SHALL NOT execute through a separate, independently constructed simulation path.
2. THE Backtester SHALL load the immutable Strategy_Version's persisted compiled plan through the same version-load path the Live_Runtime uses (`BacktestRuntime.load_version_plan`).
3. IF the persisted compiled plan's identity hash does not match the version's canonical graph, THEN THE Backtester SHALL recompile the plan for that backtest run only, and SHALL NOT overwrite or replace the Strategy_Version's persisted immutable compiled plan with the recompiled result.
4. IF the persisted compiled plan cannot be loaded because the referenced Strategy_Version is missing or the persisted plan data is corrupted, THEN THE Backtester SHALL refuse to run the backtest and SHALL return an explicit error identifying the Strategy_Version as unavailable for backtesting.
5. THE Backtest_Engine SHALL simulate trade execution using the platform's real VectorBT integration (`backend_app/backend/backtesting_engine.py`) rather than a hand-rolled or approximated portfolio simulation.
6. IF the VectorBT package is not importable in the running environment, THEN THE Backtest_Engine SHALL either refuse to run the backtest with an explicit "simulation engine unavailable" error, or SHALL run a reduced-fidelity fallback calculation and mark that result with a fallback status that is distinguishable from a VectorBT-produced result; this fallback status SHALL be recorded in the persisted backtest result's metadata and SHALL be shown on every user-facing display of that result; THE Backtest_Engine SHALL NOT present a fallback-calculated result as an ordinary VectorBT result.
7. THE Backtester SHALL NOT construct or persist an execution graph representation for backtesting that differs from the canonical graph and compiled plan the Live_Runtime consumes.

---

### Requirement 6: Backtest configuration exposes only backend-supported parameters

**User Story:** As a strategy owner configuring a backtest, I want every option I am shown to
actually work, so that I never discover after running the backtest that a parameter I set was
silently ignored.

#### Acceptance Criteria

1. THE Backtester SHALL present configuration controls for: strategy, strategy version, symbol, market type, data feed source, timeframe, start date, end date, initial capital, position sizing, trading fees, slippage, and — only where the Backtest_Engine actually implements them — commission, leverage, stop loss, take profit, and benchmark comparison, where "implements" means that varying the parameter's submitted value produces a corresponding, observable change in the backtest's simulated trades or results.
2. THE Backtester SHALL NOT present a configuration control for a parameter that the Backtest_Engine does not read from the submitted configuration or does not apply (per the definition in Criterion 1) when running the simulation.
3. IF a configuration control corresponds to a parameter that the Backtest_Engine accepts but does not fully apply to the simulation (for example, accepted but not yet used in calculations), THEN THE Backtester SHALL display a visible limitation notice adjacent to that control, without requiring additional user interaction (such as a hover or click) to reveal it, before the user submits the backtest request.
4. THE Backtester SHALL validate every submitted configuration value against the Backtest_Engine's own accepted ranges (for example, the existing whitelisted VectorBT frequency map) where such a range is defined, and, for any field without an engine-defined range, SHALL apply the following universal sanity bounds: initial capital SHALL be greater than 0, any percentage-based parameter (for example, trading fees, slippage, commission) SHALL be within 0 to 100 inclusive, and the start date SHALL be strictly before the end date.
5. IF a submitted configuration value fails the validation in Criterion 4, THEN THE Backtester SHALL reject that value locally before sending the backtest request to the Backtest_Engine and SHALL prevent submission of the backtest until the value is corrected.
6. IF the Backtester rejects a submitted configuration value, THEN THE Backtester SHALL retain all previously entered configuration values, SHALL display the rejection reason adjacent to the specific field that failed validation, and SHALL NOT clear or reset any other field.
7. IF a required configuration field (strategy, strategy version, symbol, market type, data feed source, timeframe, start date, end date, or initial capital) is left empty at the time of submission, THEN THE Backtester SHALL reject the submission, SHALL identify each empty required field, and SHALL prevent the backtest request from being sent to the Backtest_Engine.

---

### Requirement 7: Historical data feed selection is real, disclosed and validated before running

**User Story:** As a strategy owner, I want to see and validate exactly what historical data
my backtest will run against before I run it, so that I do not discover a silent data
substitution or a data quality problem only after burning compute time.

#### Acceptance Criteria

1. THE Backtester SHALL populate its symbol, timeframe and data-source selectors from real historical data availability (CCXT and/or the platform's OHLCV storage), and SHALL NOT offer a symbol/timeframe/date-range combination for which no real data source exists.
2. IF the configured historical data source(s) are unreachable while populating selectors or the available date range, THEN THE Backtester SHALL NOT offer stale, cached, or fabricated selectors or ranges as if current, and SHALL display an explicit error identifying which data source is unreachable.
3. THE Backtester SHALL display, for the selected symbol and timeframe, the actual available date range.
4. IF a requested date range extends beyond the actually available data range, THEN THE Backtester SHALL refuse to silently narrow or substitute the range, and SHALL display an explicit error naming the requested range and the available range.
5. BEFORE executing a backtest, THE Backtest_Engine SHALL validate the candidate dataset for: correct symbol and timeframe matching the request, candle count exactly matching the number of expected candles for the requested date range and timeframe, absence of duplicate timestamps, correct timestamp ordering, and sufficient leading bars to satisfy the strategy version's computed warmup requirement.
6. IF the dataset validation in Criterion 5 finds a blocking defect, THEN THE Backtest_Engine SHALL refuse to run the backtest and SHALL report the specific defect (naming incorrect symbol, incorrect timeframe, missing candles, timestamps with conflicting OHLCV values, out-of-order timestamps, or insufficient warmup, as applicable) rather than running on defective data.
7. IF the dataset validation finds duplicate timestamps whose OHLCV values are identical, THEN THE Backtest_Engine SHALL treat this as non-blocking, SHALL automatically remove the duplicate entries, and SHALL surface a warning identifying the number of duplicates removed.
8. IF the dataset validation finds a non-blocking issue other than identical-value duplicate timestamps, THEN THE Backtester SHALL surface it as a warning that the user may explicitly choose to proceed past before the backtest runs.

---

### Requirement 8: Backtest results and metrics are computed, never fabricated

**User Story:** As a strategy owner, I want every number in my backtest report to be a real
computed result, so that I can make a deployment decision on trustworthy evidence.

#### Acceptance Criteria

1. WHEN a backtest run completes, THE Backtest_Engine SHALL compute and report: initial capital, final equity, total return, CAGR, Sharpe ratio, Sortino ratio, maximum drawdown, win rate, profit factor, total trade count, winning trade count, losing trade count, average trade result, best trade result, worst trade result, total fees paid, market exposure, and portfolio turnover.
2. WHERE the Backtest_Engine supports a buy-and-hold benchmark comparison, THE Backtest_Engine SHALL compute the benchmark using the same historical dataset, the same time period, and the same initial capital as the strategy simulation being compared against.
3. IF a metric cannot be validly computed for a given result (for example, Sortino ratio with zero downside deviation, or win rate with zero trades), THEN THE Backtest_Engine SHALL report that metric as null or not-applicable together with a stated reason identifying the specific undefined condition that prevented computation, and SHALL NOT substitute a fabricated, defaulted, or arbitrarily clamped numeric value.
4. THE Backtest_Engine SHALL compute every reported metric from the actual simulated trade and equity series produced by the VectorBT execution for that specific run; no metric SHALL be estimated, interpolated, or copied from an unrelated run.
5. IF the VectorBT execution for a run fails or terminates before producing a complete trade and equity series, THEN THE Backtest_Engine SHALL report the run as failed, SHALL NOT report any partial or placeholder metric values for that run, and SHALL preserve any partial simulation output generated up to the point of failure for diagnostic purposes.

---

### Requirement 9: Backtest visualization and trade detail are drawn from the real result

**User Story:** As a strategy owner, I want to visually inspect exactly what my strategy did
during the backtest, trade by trade, so that I can judge its behavior, not just its summary
statistics.

#### Acceptance Criteria

1. WHEN a persisted Backtest_Result is loaded for display, THE Backtester SHALL render an equity curve, a drawdown chart, a price chart annotated with entry and exit markers that are visually distinguishable by side (long/short) and by outcome (win/loss), a trade outcome distribution, and a per-period performance breakdown using monthly periods, each drawn exclusively from the loaded Backtest_Result's own data series.
2. THE Backtester SHALL render a trade table listing, per trade: trade identifier, entry time and price, exit time and price, side, quantity, gross profit/loss, net profit/loss, fees, return, duration, and status, where status is one of OPEN or CLOSED, and duration is expressed in whole minutes measured from entry time to exit time (or to the current time if status is OPEN).
3. THE Backtester SHALL paginate the trade table at a fixed page size of 25 trades per page, ordered by entry time descending by default.
4. THE Backtester SHALL NOT render a chart series or trade row that was not present in the persisted Backtest_Result.
5. IF the loaded Backtest_Result contains zero trades, THEN THE Backtester SHALL render the equity curve, drawdown chart, and price chart using the Backtest_Result's available data series and SHALL display an empty-state indication in place of the trade table and trade outcome distribution, without treating this as a load failure.
6. IF the Backtest_Result fails to load or is missing for the requested backtest, THEN THE Backtester SHALL display an error indication in place of the charts and trade table, SHALL NOT render any partial or cached chart or trade data, and SHALL preserve the user's current view context so the user can retry or navigate away.

---

### Requirement 10: Backtest results are persisted immutably and reproducibly

**User Story:** As a strategy owner, I want to save a backtest result and trust that reopening
it later shows me exactly what happened when I ran it, so that I can compare strategies and
configurations reliably over time.

#### Acceptance Criteria

1. WHEN a backtest completes, THE Backtest_Engine SHALL persist a Backtest_Result carrying: a unique backtest identifier, the owning user identifier, the strategy identifier, the strategy version identifier, the full submitted configuration, the data source and symbol and timeframe and date range used, the computed metrics, the trade detail, and the completion timestamp.
2. THE Backtest_Engine SHALL treat a persisted Backtest_Result's stored configuration, metrics, and trade detail as immutable. IF any subsequent action attempts to modify a persisted Backtest_Result's stored configuration, metrics, or trade detail, THEN THE Backtest_Engine SHALL reject the modification, preserve the previously stored values unchanged, and return an indication to the caller that the record is immutable.
3. THE Backtest_Engine SHALL persist, as part of each Backtest_Result, the strategy version's identity hash, the market-data source identifier, the fee and slippage assumptions applied, and the Backtest_Engine's own version identifier, so that the run is fully reproducible from the record alone.
4. THE Strategies_Page and Backtester SHALL allow a user to list and open their own saved Backtest_Results.
5. IF a user attempts to list, open, delete, archive, or select for comparison a Backtest_Result they do not own, THEN THE Strategies_Page and Backtester SHALL deny the action and SHALL NOT expose that Backtest_Result's configuration, metrics, or trade detail to the user.
6. THE Strategies_Page and Backtester SHALL allow a user to archive or permanently delete their own saved Backtest_Results, where archiving hides the Backtest_Result from the default list view while retaining its stored record, and permanent deletion irreversibly removes the stored record such that it can no longer be opened, inspected, or included in comparisons.
7. WHERE the Backtester's comparison view is implemented, THE Backtester SHALL allow a user to select between 2 and 5 of their own saved Backtest_Results for side-by-side comparison.
8. WHEN a user opens a previously saved Backtest_Result, THE Backtester SHALL render the stored result as saved; it SHALL NOT silently re-run the backtest against current data and present that as the saved result.
9. THE Backtester SHALL allow a user to inspect a saved Backtest_Result's full configuration and the strategy version it was run against, distinct from re-running it.

---

### Requirement 11: Live deployment reuses the existing execution architecture

**User Story:** As a platform operator, I want live deployment to route every strategy
through the one execution stack this platform already trusts, so that there is never a second,
divergent path an order can travel by.

#### Acceptance Criteria

1. THE Deployment_Service SHALL bind a Deployment to exactly one immutable Strategy_Version, one Exchange_Account, one risk configuration and one execution configuration, and SHALL start execution through the existing Live_Runtime (DAG event loop and DAG engine) rather than a separate execution stack.
2. THE Deployment_Service SHALL route every generated Signal through the platform's existing risk validation (`risk_engine`, `dag_risk_integration`) and order validation/execution (`execution_engine`, `execution_guard`) components before any order may be submitted; it SHALL NOT introduce a second order-placement path that bypasses these components.
3. IF the risk validation or the order validation/execution components reject a generated Signal, THEN THE Deployment_Service SHALL persist that Signal's rejected outcome and SHALL NOT submit an order for it.
4. IF the Live_Runtime fails to start for a bound Deployment, THEN THE Deployment_Service SHALL set that Deployment's binding state to the existing binding lifecycle's FAILED state, SHALL NOT report the Deployment as RUNNING, and SHALL surface the specific startup failure reason on the Strategies_Page.
5. WHEN a user's Deploy Live action submits a deployment request, THE Strategies_Page SHALL invoke the same Deployment_Gate (`evaluate_binding`) that already enforces version readiness, exchange-account ownership, symbol/timeframe availability, mode rules and model-readiness checks; it SHALL NOT invoke the legacy deploy path or any other endpoint that bypasses that gate.

---

### Requirement 12: A strategy cannot be deployed live without a valid, connected exchange account, and remains exchange-independent otherwise

**User Story:** As a strategy owner, I want to build and save a strategy without ever being
asked for exchange credentials, and to bind it to a specific exchange account only when I
choose to deploy it live, so that one strategy definition can be reused across accounts and
never carries a secret.

#### Acceptance Criteria

1. THE Strategy_Builder and the strategy save path SHALL NOT request, accept, or persist an exchange identifier, API key, secret or account reference as part of a strategy's canonical graph or version.
2. THE Deployment configuration workflow SHALL present, for selection, only Exchange_Accounts that are already connected and owned by the authenticated user, and SHALL require the user to select one such account, referenced by identifier only, before deployment may proceed.
3. IF the user has no connected Exchange_Account for the mode being deployed (live, as opposed to any sandbox/test mode covered by Requirement 26), THEN THE Deployment configuration workflow SHALL refuse to proceed and SHALL direct the user to the exchange connection management surface.
4. THE Deployment configuration workflow SHALL present: exchange selection, connected account selection, market/symbol confirmation, quantity/sizing configuration, risk configuration selection, and execution configuration — all as deployment-time inputs distinct from the strategy definition itself.
5. THE Deployment_Service SHALL resolve exchange credentials only inside the execution process, by Exchange_Account identifier, through the existing `credential_vault`/`api_key_vault`; credentials SHALL NOT appear in the deployment request body, the strategy graph, any API response, or any log output written by the Deployment_Service.
6. IF a strategy save, update, or version-creation request submitted to the Strategy_Builder or the strategy save path contains an exchange identifier, API key, secret, or account reference, THEN THE Strategy_Builder and the strategy save path SHALL reject the request, SHALL report an error identifying the disallowed field, and SHALL NOT persist any part of that request.

---

### Requirement 13: Deployment activation requires every mandatory validation to pass, with an actionable reason for each failure

**User Story:** As a strategy owner, I want to know exactly why a deployment cannot start
before I click deploy, so that I can fix the problem instead of guessing.

#### Acceptance Criteria

1. WHEN a Deployment activation is requested, THE Deployment_Gate SHALL validate, for that Deployment: the Exchange_Account's connection status and credential validity; the account's trading permissions required for the order type and market being deployed; the target market/symbol's availability on that account's exchange; account balance sufficiency, defined as the account's available balance being greater than or equal to the requested order's notional value plus estimated fees and any applicable margin requirement; the requested quantity against the exchange's minimum order size and precision/tick-size constraints; the risk limits configured in the Deployment's selected risk configuration (including maximum position size, maximum leverage and maximum daily loss, where configured); the Strategy_Version's lifecycle readiness state (READY) together with the presence of a valid, hash-verified compiled plan; market data availability for the required symbol/timeframe; and the availability of the required live market-data connection.
2. IF one or more mandatory validations in Criterion 1 fail, THEN THE Deployment_Gate SHALL refuse activation and SHALL report every failed condition — naming the specific validation that failed and the corrective action required to resolve it — rather than reporting only the first encountered failure; it SHALL NOT activate a deployment while any mandatory condition in Criterion 1 remains unmet.
3. THE Deployment configuration workflow SHALL present a pre-deployment summary listing: the strategy and version, the exchange and account, the asset and quantity, the order type, the risk settings, the data feed to be used, the account balance, the required permissions, and the current validation status — passed, failed, or pending — of each mandatory condition in Criterion 1, together with the specific reason for any condition currently in a failed state.
4. WHILE any mandatory validation in Criterion 1 has not yet passed, THE Deploy button in the deployment configuration workflow SHALL remain disabled.
5. WHEN every mandatory validation in Criterion 1 has passed, THE Deploy button in the deployment configuration workflow SHALL become enabled automatically, without requiring a page reload or any other manual user action.
6. IF a mandatory validation that had previously passed subsequently fails while the deployment configuration workflow remains open (for example, the account balance drops below the required amount or the exchange connection is lost), THEN THE Deploy button SHALL become disabled again automatically, and THE pre-deployment summary in Criterion 3 SHALL be updated to reflect the newly failed condition.

---

### Requirement 14: Live strategy execution connects to real market data and produces auditable server-side decisions

**User Story:** As a strategy owner running a live deployment, I want my strategy to react to
real market data and make its decisions entirely on the server, so that I never have to trust
my own browser tab to place my orders correctly.

#### Acceptance Criteria

1. WHEN a Deployment transitions to an active running state, THE Live_Runtime SHALL subscribe to live market data through the platform's existing production market-data source, subscribing only to the stream types (ticker, trade, OHLCV, order book) the deployed strategy's compiled plan actually requires.
2. THE Live_Runtime SHALL deliver normalized market events to the strategy's DAG execution exactly as validated by the existing `market_data_validation` pipeline; it SHALL NOT bypass that validation for the live path.
3. WHEN the Live_Runtime receives a live market event that has passed `market_data_validation` and matches a stream type and symbol the deployed strategy's compiled plan subscribes to, THE Live_Runtime SHALL update node state and indicators, evaluate any ML/DL nodes, evaluate logic and action nodes, and validate the resulting candidate against risk limits; THE Live_Runtime SHALL generate a Signal only if every upstream node in the emitting action's closure is ready and the risk validation passes.
4. ALL strategy execution decisions and order submissions SHALL occur exclusively in server-side processes; the frontend SHALL NOT construct or submit an order directly to an exchange or to the execution engine.
5. THE Live_Runtime SHALL persist the generated Signal, and SHALL persist the resulting order/execution state as it becomes available, before reporting the corresponding outcome to the frontend; a Signal or an order/execution state update SHALL NOT be reported to the frontend before it has been persisted.
6. IF the live market-data subscription required by Criterion 1 disconnects or stops delivering qualifying events for the deployed strategy's required stream types, THEN THE Live_Runtime SHALL suspend Signal generation for that Deployment, SHALL mark the Deployment's live-data health state accordingly, and SHALL resume normal evaluation only after the subscription is re-established and confirmed current; it SHALL NOT generate a Signal from stale or gapped market data.
7. IF node-state update, indicator evaluation, ML/DL node evaluation, or logic/action node evaluation raises an error for a given market event, THEN THE Live_Runtime SHALL abort Signal generation for that event, SHALL record the failure against the Deployment without generating a Signal, and SHALL continue processing subsequent market events; a node evaluation error on one event SHALL NOT halt the Live_Runtime's processing of later events for that Deployment.
8. IF persisting a generated Signal or its resulting order/execution state fails, THEN THE Live_Runtime SHALL retry persistence according to the platform's existing retry conventions, SHALL NOT report the unpersisted Signal or state to the frontend, and SHALL NOT submit or continue submitting the corresponding order until persistence succeeds.

---

### Requirement 15: Every signal is uniquely identified and fully attributed

**User Story:** As a strategy owner, I want every trading decision my deployed strategy makes
to be individually traceable back to exactly which strategy, version, deployment and market
context produced it, so that I can audit and debug my live trading.

#### Acceptance Criteria

1. THE Live_Runtime SHALL assign to every generated Signal a unique identifier that is globally unique across the platform, immutable once assigned, and never reused for any other Signal, regardless of strategy, deployment or user.
2. THE Live_Runtime SHALL persist, for every Signal, at the time of generation: signal identifier, owning user identifier, strategy identifier, strategy version identifier, deployment identifier, symbol, generation timestamp, signal type, side, requested quantity or sizing intention, the identifier(s) of the source node(s) that produced the decision, decision metadata comprising at minimum the evaluated state of every upstream node in the emitting action node's closure, the outcome of the risk validation applied to the candidate decision, and — where an ML/DL node contributed to the decision — that node's inference output, the Signal's current Order_Lifecycle_State (as defined in Requirement 16), and, once available, a reference to the resulting order/execution record.
3. THE decision metadata persisted in Criterion 2 SHALL contain no exchange credential, API key, secret or passphrase.
4. THE Live_Runtime and every logging path it writes through SHALL NOT record an exchange credential, API key, secret or passphrase in any log line.
5. IF the Live_Runtime fails to persist a generated Signal's record, THEN THE Live_Runtime SHALL NOT report that Signal to the frontend and SHALL NOT route it to risk validation or order submission, and SHALL record the persistence failure through the platform's existing error-handling path so the missed Signal can be investigated.

---

### Requirement 16: One canonical order/signal status vocabulary, reconciling the vocabularies that already exist

**User Story:** As a strategy owner and as an engineer maintaining this system, I want exactly
one authoritative answer to "what state is this signal/order in", so that the Signal Trace
page, the order engine and the database never disagree about the same event.

#### Acceptance Criteria

1. THE Order_Lifecycle_State reported to the frontend SHALL be one of exactly 9 values: `GENERATED`, `PENDING`, `SUBMITTED`, `PARTIALLY_EXECUTED`, `EXECUTED`, `CLOSED`, `FAILED`, `CANCELLED`, `REJECTED`, where `CLOSED`, `FAILED`, `CANCELLED`, and `REJECTED` are terminal states (no further transitions permitted) and `GENERATED`, `PENDING`, `SUBMITTED`, `PARTIALLY_EXECUTED`, and `EXECUTED` are non-terminal states.
2. THE Order_Lifecycle_State reporting layer SHALL derive its reported value from the existing `OrderState` machine (`backend_app/core/order_state_engine.py`), the `signals.status` column, and `SignalTraceRecord`/`TraceStatus`, reconciling all three into the single vocabulary in Criterion 1, rather than introducing a fourth independent status column that must additionally be kept in sync.
3. THE reconciliation layer SHALL maintain a fixed, one-directional mapping table from every possible value of `OrderState`, every possible value of `signals.status`, and every possible value of `TraceStatus` to exactly one of the 9 values in Criterion 1, including any internal-only pre-submission sub-states (e.g. risk-check-pending, approved-pending-submission) which SHALL map to the reported value `PENDING`.
4. THE backend state machine underlying Order_Lifecycle_State SHALL permit only the following transitions among the 9 values in Criterion 1: `GENERATED → PENDING → SUBMITTED → PARTIALLY_EXECUTED → EXECUTED → CLOSED`, plus a self-transition `PARTIALLY_EXECUTED → PARTIALLY_EXECUTED` to represent repeated partial fills, plus failure branches to `REJECTED`, `FAILED`, or `CANCELLED` reachable from any non-terminal state (`GENERATED`, `PENDING`, `SUBMITTED`, `PARTIALLY_EXECUTED`, `EXECUTED`) that the underlying `OrderState` machine's own transition rules permit.
5. IF the `OrderState` machine, `signals.status`, and `SignalTraceRecord`/`TraceStatus` map (per Criterion 3) to two or more different values in Criterion 1's vocabulary for the same signal/order pair at the same point in time, THEN THE reconciliation layer SHALL resolve the conflict by selecting the value corresponding to the source's priority order `OrderState` > `signals.status` > `TraceStatus`, and SHALL report only that single resolved value.
6. IF a write to the Persistence_Layer would set an Order_Lifecycle_State to a value not reachable from its current state under Criterion 4's transition rules, THEN THE Persistence_Layer SHALL reject the write, SHALL retain the Order_Lifecycle_State's prior value unchanged, and SHALL return an error indication identifying the rejected transition to the caller.
7. WHERE the platform's existing audit-trail storage is available (i.e. reachable and accepting writes) at the time of a transition, THE Order_Lifecycle_State transition history for a signal/order pair SHALL be persisted with, at minimum, the prior value, the new value, and the transition timestamp, and SHALL be retrievable in chronological order by timestamp.

---

### Requirement 17: Signal Trace displays every live signal with full context and supports filtering

**User Story:** As a strategy owner, I want to see every signal my live strategies have
generated, filter to the ones I care about, and see the full lifecycle detail of any one of
them, so that I can verify my strategy is doing what I expect.

#### Acceptance Criteria

1. THE Signal_Trace_Page SHALL display every Signal generated by the authenticated user's own live deployments, sorted by generation time in descending order (most recent first) by default; it SHALL NOT omit a signal that was actually generated.
2. THE Signal_Trace_Page SHALL support filtering the signal list by strategy, strategy version, deployment, symbol, side, Order_Lifecycle_State, and date range; it SHALL allow multiple values to be selected within any one filter category, and SHALL combine all currently active filter categories using AND logic, such that a signal is included only if it matches at least one selected value in every active category.
3. THE Signal_Trace_Page SHALL display, per signal: generation time, strategy name, symbol, side, quantity, Order_Lifecycle_State, order identifier (once assigned), execution identifier (once assigned), execution price (once available), filled quantity, remaining quantity, fees, and — for a failed/rejected/cancelled signal — the failure reason.
4. WHEN a user navigates to the Signal_Trace_Page filtered by strategy identifier (from the Strategies_Page's View Signal Trace action), THE Signal_Trace_Page SHALL apply that filter on load without further user action.
5. THE Signal_Trace_Page SHALL paginate the signal list at a maximum of 100 signals per page, and SHALL provide pagination controls (or an equivalent "load more" mechanism) that allow the user to reach every signal beyond the first page without that signal being otherwise inaccessible.
6. WHEN a user selects a signal from the list, THE Signal_Trace_Page SHALL open that signal's full Signal_Trace detail, displaying its DAG node trace, its ML inference detail (where the signal's strategy version includes an ML node), its risk validation detail, and its execution outcome.
7. WHEN the signal list request fails, THE Signal_Trace_Page SHALL render an explicit error state with a retry action, and SHALL NOT render a stale or fabricated list in its place. WHEN the signal list is empty, THE Signal_Trace_Page SHALL render an explicit empty state distinguishing "no signals exist yet" from "no signals match the current filter".

---

### Requirement 18: Signal Trace updates in realtime without manual refresh

**User Story:** As a strategy owner watching my live deployment, I want to see a signal's
status change on screen the moment it happens, so that I do not have to keep refreshing the
page to know if my order filled.

#### Acceptance Criteria

1. THE Signal_Trace_Page SHALL receive signal and order-status updates over a WebSocket connection; it SHALL NOT rely on manual page refresh or a general-purpose polling loop as its primary update mechanism.
2. THE Signal_Trace_Page's WebSocket subscription SHALL be an Owned_Channel authorised against the identity of the strategy, deployment, or user it is scoped to, following the existing `OwnedChannelFamily` pattern already used for `training`, `strategy`, `deployment` and `execution` channels.
3. IF a client attempts to subscribe to a Signal_Trace_Page channel scoped to a strategy, deployment, or user other than its own authorised identity, THEN THE Signal_Trace_Page's backend SHALL refuse the subscription, and THE Signal_Trace_Page SHALL surface a visible error state to the user rather than silently displaying no data.
4. THE Signal_Trace_Page SHALL NOT display a duplicate event for the same underlying status change received more than once (for example, after a reconnect that replays a snapshot); duplicate detection SHALL be based on each event's own stable identifier (e.g., signal id combined with status/version), not on stream position or arrival order.
5. THE Signal_Trace_Page SHALL display status updates for one signal in the order in which they were actually persisted/occurred on the backend, buffering and reordering any frames that arrive out of sequence over the network before rendering them, rather than displaying them in raw network arrival order.
6. WHEN the WebSocket connection drops, THE Signal_Trace_Page SHALL attempt reconnection using the same bounded, jittered backoff policy defined by the platform's existing `useBuilderRealtime` reconnect contract, and, upon reconnection, SHALL request a snapshot of current state rather than assuming no updates were missed.
7. WHEN the authenticated user's session token is refreshed, THE Signal_Trace_Page's WebSocket connection SHALL re-authenticate without a full reconnect, consistent with the platform's existing `useBuilderRealtime` reconnect/reauthenticate contract.
8. WHEN the Signal_Trace_Page is unmounted or the user navigates away, its WebSocket subscription SHALL be explicitly torn down.
9. WHILE the Signal_Trace_Page's WebSocket connection is disconnected or attempting reconnection, THE Signal_Trace_Page SHALL display a visible connection-status indicator distinguishing "connected", "reconnecting", and "disconnected" states to the user.

---

### Requirement 19: Idempotency and crash recovery prevent duplicate orders under every observed failure mode

**User Story:** As a platform operator, I want a network retry, a worker crash, or a
WebSocket reconnect to never cause the same trading decision to be executed twice, so that a
user's account is never charged for a duplicate order because our infrastructure hiccupped.

#### Acceptance Criteria

1. THE Live_Runtime SHALL derive a deterministic Idempotency_Key for every order submission attempt, reusing the existing `DistributedIdempotencyLayer`, such that the same underlying Signal submitted more than once (through network retry, exchange-side retry, WebSocket reconnect, or worker/process restart) produces at most one order at the exchange; THE Idempotency_Key and its recorded submission outcome SHALL remain valid and enforced for the entirety of that Signal's lifecycle, from its `GENERATED` state until it reaches any terminal Order_Lifecycle_State (`CLOSED`, `FAILED`, `CANCELLED`, or `REJECTED`), and SHALL NOT expire or be evicted before that Signal reaches a terminal state.
2. IF a strategy worker process crashes and is restarted while a Signal's order submission was in flight, THEN THE Live_Runtime SHALL, upon restart and before taking any further action on that Signal, query the exchange for the order matching that Signal's Idempotency_Key wherever the exchange supports lookup by client order identifier, and SHALL treat the persisted Order_Lifecycle_State as authoritative when the exchange is unreachable or does not support such a lookup; THE Live_Runtime SHALL bound this exchange-state check to a maximum of 3 attempts within 30 seconds total, and SHALL NOT resubmit an order for a Signal whose Idempotency_Key was already used. IF the exchange-state check exhausts its attempts without a definitive answer, THEN THE Live_Runtime SHALL mark that Signal's Order_Lifecycle_State as requiring manual reconciliation rather than resubmitting the order.
3. IF the API process restarts, the WebSocket connection disconnects, the exchange connection disconnects, or the database/queue restarts, THEN no in-flight Signal's persisted Order_Lifecycle_State record SHALL be overwritten with an earlier state, split into two or more signal/order records sharing the same Idempotency_Key, or left referencing an order identifier that does not match the exchange's own record of that order; THE Signal SHALL resume from its last persisted Order_Lifecycle_State without re-deriving it from an earlier, superseded state.
4. THE crash-recovery behaviors in Criteria 2 and 3 SHALL be exercised by an automated regression test that, for each named failure mode (worker crash mid-submission, API process restart, WebSocket disconnect, exchange connection disconnect, database/queue restart), asserts: (a) at most one order is created at the exchange or its test double per Signal, (b) the Signal's persisted Order_Lifecycle_State after recovery matches its true state at the exchange, and (c) no duplicate or orphaned signal/order record exists for that Signal's Idempotency_Key; THE regression test SHALL fail if any of these three assertions does not hold for any simulated failure mode.
5. IF the `DistributedIdempotencyLayer`'s backing store is unavailable or unreachable at the moment of order submission, THEN THE Live_Runtime SHALL refuse to submit that order rather than submitting it without an idempotency guarantee, SHALL mark the Signal's Order_Lifecycle_State to reflect that submission was withheld pending the idempotency store's availability, and SHALL retry submission — under the same Idempotency_Key — once the backing store becomes available again.

---

### Requirement 20: Strict ownership and credential containment across the full lifecycle

**User Story:** As a strategy owner, I want absolute certainty that no other user can read,
reference, or affect my strategies, backtests, deployments or signals, and that my exchange
credentials are never exposed anywhere outside the credential vault.

#### Acceptance Criteria

1. THE Strategy_Builder_API SHALL enforce, on every read, write, and creation request for a strategy, strategy version, backtest, backtest result, deployment, signal, or exchange account reference, both an application-layer ownership check against the authenticated user and the row-level security policies required by Requirement 21; no endpoint SHALL permit a user to read, modify, delete, or reference — including by naming it as a related entity in a request body — another user's resource of any of these kinds.
2. WHEN any request, whether read or write, references a resource covered by Criterion 1 by identifier and the authenticated user does not own that resource, THE Strategy_Builder_API SHALL respond identically to how it responds when that identifier does not exist at all, such that no aspect of the response reveals whether the resource exists.
3. THE Strategy_Builder_API SHALL NOT include any exchange-issued account identifier, API key, secret, passphrase, or access token in a strategy's canonical graph or version record, a backtest configuration or result, a deployment request or response body, a signal record, a WebSocket frame, a URL, or a log line, at any point in this lifecycle; this prohibition covers all persisted records, API responses, and log output, and does not restrict the credential vault's own transient, internal use of decrypted credentials to authenticate an outbound request to the exchange. The platform's internal Exchange_Account reference identifier — used solely to name which connected account a strategy, deployment, or signal belongs to — is exempt from this prohibition.
4. AN automated cross-tenant test suite SHALL assert, for every read and write endpoint introduced or extended by this specification, that a non-owning authenticated user receives the identical non-existence response defined in Criterion 2 — never the requested data or any indication the resource exists — across strategy, version, backtest, backtest result, deployment, signal, and exchange account resource types.

---

### Requirement 21: Database schema integrity for the full lifecycle

**User Story:** As a platform operator, I want the database itself to refuse to represent an
inconsistent or ownerless trading record, so that application bugs cannot silently corrupt
financial history.

#### Acceptance Criteria

1. THE Persistence_Layer (the database-level enforcement layer underlying the Strategy_Builder_API) SHALL enforce, via foreign-key constraints, that every `strategy_versions`, `strategy_backtests`, `strategy_deployments`, `signals` and `signal_events` row references an existing owning strategy and an existing owning user.
2. THE Persistence_Layer SHALL enforce the following uniqueness constraints: exactly one current version per strategy in `strategy_versions`, and a unique Idempotency_Key per order submission recorded against `signals`; no two rows SHALL share the same current-version-per-strategy pairing or the same Idempotency_Key.
3. THE Persistence_Layer SHALL index, at minimum: the owning-user and strategy identifier columns on `strategy_versions`, `strategy_backtests`, `strategy_deployments`, `signals` and `signal_events` (supporting Requirement 1's ownership-scoped listing); the strategy-version identifier and completion-timestamp columns on `strategy_backtests` (supporting Requirement 10's list, open and comparison operations); and the strategy, strategy-version, deployment, symbol, side, Order_Lifecycle_State and generation-timestamp columns on `signals` (supporting Requirement 17's filtering).
4. THE Persistence_Layer SHALL enforce row-level security, scoped to the owning user, on every table introduced or extended by this specification — namely `strategies` (as extended with archival state per Requirement 3), `strategy_versions`, `strategy_backtests`, `strategy_deployments`, `signals`, `signal_events`, and, where the transition-history table described in Requirement 16 Criterion 7 is implemented, that table — consistent with the row-level security already applied to `strategy_versions`, `strategy_deployments`, `strategy_backtests`, `signals` and `signal_events`.
5. THE Persistence_Layer SHALL record creation and last-updated timestamps on every table listed in Criterion 4.
6. WHEN a strategy is archived per Requirement 3, THE Persistence_Layer SHALL NOT cascade-delete any `strategy_versions`, `strategy_backtests`, `strategy_deployments`, `signals` or `signal_events` row that references it.
7. IF a write would violate a foreign-key constraint under Criterion 1, a uniqueness constraint under Criterion 2, or the Order_Lifecycle_State transition-reachability rule established in Requirement 16 Criterion 4, THEN THE Persistence_Layer SHALL reject the write in its entirety, leaving all previously committed rows unchanged, and THE Strategy_Builder_API SHALL surface to the caller an error indicating which constraint was violated.

---

### Requirement 22: APIs follow existing conventions and require authorization

**User Story:** As a frontend engineer integrating with this lifecycle, I want the API surface
to follow the same conventions the rest of this platform already uses, so that I do not have
to special-case this feature area.

#### Acceptance Criteria

1. THE API endpoints covering strategies (list, get, versions, update, delete/archive, duplicate), backtests (list, create, get, delete/archive), deployments (list, create, get, start, pause, stop) and signal trace (list, get) SHALL either extend the existing `strategy_operations.py`/`strategies.py` router conventions or be documented, in this specification's design documentation, as an explicit, deliberate deviation with a stated reason; no new, parallel router SHALL be introduced without such documented justification.
2. EVERY endpoint covered by Criterion 1 SHALL require an authenticated caller and SHALL apply the existing rate-limiting convention (`slowapi`) already applied to the comparable existing endpoint — defined as the existing endpoint that performs the same action (list, get, create, update, delete/archive, duplicate, start, pause, or stop) on the closest equivalent existing resource (strategies, backtests, or deployments).
3. THE frontend and any new backend code SHALL call an existing endpoint or component that already implements a capability this specification requires (for example, the canonical `evaluate_binding` deploy gate, or `BacktestRuntime`) rather than re-implementing equivalent logic.
4. IF a caller for any endpoint covered by Criterion 1 is not authenticated, THEN THE API SHALL reject the request without performing the requested action and SHALL return an observable authorization-failure response that discloses no strategy, backtest, deployment, or signal trace data.
5. IF a caller exceeds the rate limit applied under Criterion 2 for any endpoint covered by Criterion 1, THEN THE API SHALL reject the request without performing the requested action, SHALL leave any prior state unchanged, and SHALL return an observable rate-limit-exceeded response.

---

### Requirement 23: WebSocket channels are authenticated, tenant-isolated, and resource-scoped

**User Story:** As a platform operator, I want every realtime channel in this lifecycle to be
provably scoped to its owner, so that a browser tab can never receive another tenant's live
trading data.

#### Acceptance Criteria

1. EVERY WebSocket channel covering deployment state, live strategy runtime state, signals, and order/execution status SHALL require an authenticated connection and SHALL authorise each subscription request against the identity of the resource it names, following the existing `OwnedChannelFamily` mechanism.
2. IF a subscription request names a resource the authenticated caller does not own, THEN THE WebSocket layer SHALL refuse that subscription and SHALL NOT deliver any event for that resource to the requesting connection; the refusal SHALL NOT distinguish whether the named resource exists, so that a non-owner cannot use it to confirm another tenant's resource identifier.
3. THE WebSocket layer SHALL send a heartbeat over each active channel connection at an interval of no more than 30 seconds. IF no heartbeat response is received within 10 seconds of a heartbeat being sent, THEN THE WebSocket layer SHALL treat the connection as stale and close it. WHEN a channel connection closes unintentionally, THE WebSocket layer SHALL attempt reconnection using an exponential backoff delay bounded by a minimum of no more than 2 seconds and a maximum of no more than 30 seconds, with random jitter of up to 1 second applied to each attempt so that multiple clients reconnecting after the same disconnection event do not retry in lockstep, and SHALL continue attempting reconnection until it succeeds or the subscribing page explicitly tears down the subscription.
4. WHEN a client unsubscribes from a channel, or its connection disconnects for any reason, THE WebSocket layer SHALL remove that client's subscription state for the affected channel(s) as part of that same operation, such that no further event on that channel is delivered to that client unless and until it resubscribes.
5. IF the WebSocket layer cannot immediately deliver an event to a client because of backpressure (for example, a slow consumer or a full send buffer), THEN THE WebSocket layer SHALL either buffer the event for delivery once capacity allows, or close the connection and require the client to reconnect and resynchronize its state; THE WebSocket layer SHALL NOT discard a status-transition event without the client either receiving it or being made aware, upon reconnection, that it must resynchronize.
6. THE WebSocket layer SHALL assign each event a stable, monotonically increasing sequence identifier per channel, and SHALL NOT deliver an event to a client more than once for the same channel and sequence identifier, including when a retransmission, a reconnect-triggered replay, or a network-level duplicate occurs; a client that receives an event whose sequence identifier it has already processed for that channel SHALL discard it without re-applying its state change.
7. THE platform SHALL provide a resource-scoped, signal-trace-specific Owned_Channel, authorised per the existing `OwnedChannelFamily` mechanism, for delivering signal and order/execution status updates to the Signal_Trace_Page; THE Live_Runtime and Signal_Trace_Engine SHALL NOT deliver a signal or order/execution status update to a client through the legacy, non-resource-scoped `ChannelType.SIGNAL_TRACE` broadcast channel.

---

### Requirement 24: Frontend state stays consistent across the four pages in this lifecycle

**User Story:** As a strategy owner moving between the Strategies page, Backtester, deployment
dialog and Signal Trace, I want each page to reflect the latest state immediately after an
action, so that I never see stale data after I have just changed something.

#### Acceptance Criteria

1. THE Strategies_Page, Backtester, deployment configuration workflow, and Signal_Trace_Page SHALL obtain strategy, version, backtest, deployment and signal data through the application's existing shared data-fetching layer (the same API client and, where applicable, the same realtime mechanism already used by `useBuilderRealtime`/`websocketClient`) rather than each page independently caching its own copy of the same entity; at no point in a single session SHALL two of these pages display two different values for the same field of the same strategy, version, Backtest_Result, Deployment, or Signal.
2. WHEN a strategy is saved, edited, deleted/archived, deployed, paused, stopped, backtested, or has a Backtest_Result saved, and that action's backend request succeeds, THE Strategies_Page, Backtester, deployment configuration workflow, and Signal_Trace_Page SHALL each, if currently mounted and displaying the affected strategy or its data, invalidate and refetch that data (or apply the update delivered over the applicable realtime channel) within 5 seconds of the action succeeding, without requiring a manual page reload; a page among these four that is not currently mounted at that time SHALL fetch current, non-cached data the next time it is mounted or navigated to, rather than serving a copy older than the action.
3. IF a strategy save, edit, delete/archive, deploy, pause, stop, backtest, or Backtest_Result save request fails, THEN THE originating page SHALL revert any optimistic UI change to the last confirmed server state and SHALL NOT propagate the failed change to the Strategies_Page, Backtester, deployment configuration workflow, or Signal_Trace_Page.
4. WHEN a user deploys a strategy from the Strategies_Page and then navigates to the Signal_Trace_Page, THE Signal_Trace_Page SHALL, using the Owned_Channel and reconnection contract defined in Requirement 18, display each of that deployment's signals within 5 seconds of the signal being persisted, without the user having to manually refresh; IF no signal has yet been generated for that deployment, THEN THE Signal_Trace_Page SHALL display an explicit "no signals yet" state rather than an error or a blank page.

---

### Requirement 25: UI quality — states, confirmations, and non-overloaded presentation

**User Story:** As a strategy owner, I want each page in this lifecycle to clearly tell me what
is happening, ask before doing anything destructive or live-affecting, and not overwhelm me
with everything at once.

#### Acceptance Criteria

1. THE Strategies_Page, Backtester, deployment configuration workflow, and Signal_Trace_Page SHALL each present its empty, loading, error, and success states using a distinct combination of icon, label text, or layout per state, such that no two of the four states on the same page render identically.
2. EVERY destructive action (delete/archive) and every live-affecting action on a strategy's deployment (deploy, pause, resume, stop) or on an in-progress backtest run (cancel) SHALL require an explicit confirmation step, naming the action and its consequence, before the corresponding backend request is issued.
3. WHILE no backtest run is in progress and no saved result is loaded, THE Backtester SHALL present only the strategy selector, configuration panel, and run control as immediately visible, and SHALL keep the progress indicator, performance summary, charts, trade table, and saved-backtests list collapsed or hidden until a run starts, a run completes, or a saved result is opened.
4. THE Strategies_Page SHALL present, per strategy, exactly the following primary actions directly visible: Backtest, Deploy Live, Open (navigate to the strategy's detail view), Edit, and Signal Trace; and SHALL present Duplicate, Rename, View Versions/History, and Delete/Archive (per Requirement 2 and Requirement 3) behind a secondary "more actions" affordance that is hidden until the user explicitly triggers it.
5. THE Strategies_Page SHALL visibly display each strategy's deployment state as either an explicit "not deployed" indicator (per Requirement 1, Criterion 4, when no Deployment exists) or one of the binding lifecycle states already defined in `strategy_lifecycle.py`: `DEPLOYING`, `RUNNING`, `PAUSED`, `STOPPED`, `FAILED`.

---

### Requirement 26: A deterministic test strategy can validate the full lifecycle without real-money execution

**User Story:** As an engineer validating this lifecycle end to end, I want a safe, repeatable
way to exercise save → list → backtest → save backtest → deploy → signal → signal trace, so
that automated tests can prove the whole chain works without ever risking real capital.

#### Acceptance Criteria

1. THE existing Strategy Builder SHALL be usable, without modification to the Strategy Builder itself, to construct a strategy whose behavior is deterministic — producing identical Signals given identical input data across repeated runs — and whose execution does not depend on live market data or real-time randomness (for example, one driven by a fixed, seeded historical or synthetic data source rather than a live market feed).
2. THE lifecycle covered by this specification SHALL support running such a test strategy through each of the following stages, each reaching its own persisted, verifiable completion state as defined elsewhere in this specification: save, appearance on the Strategies_Page, backtest execution, backtest result persistence, deployment to a non-production/sandbox execution mode (any Deployment_Binding mode other than `live`), signal generation, and appearance on the Signal_Trace_Page.
3. WHEN such a test strategy is deployed to a non-production/sandbox execution mode, THE Deployment_Service SHALL NOT route any resulting Signal to a real exchange order-placement call, and SHALL record that Signal's Order_Lifecycle_State reaching a terminal state that is distinguishable, on the Signal_Trace_Page, from a Signal that resulted in a real exchange fill.
4. THE automated test suite for this specification SHALL include at least one end-to-end test that exercises the full chain in Criterion 2 using such a test strategy and asserts: successful persistence of the strategy and its version, a completed and persisted Backtest_Result, a successful deployment binding in a non-production/sandbox mode, generation of at least one Signal, that Signal's appearance with a trace record on the Signal_Trace_Page, and the absence of any real exchange order-placement call for that Signal.
5. THE automated test suite for this specification SHALL include a test that runs the same test strategy's Backtest_Configuration more than once and asserts that the computed metrics and trade list of each run are identical, verifying the determinism required by Criterion 1.

---

### Requirement 27: Extension points for Marketplace and Paper Trading are preserved but not implemented

**User Story:** As a platform architect, I want this specification's design to leave room for
"List in Marketplace" and "Paper Trading" to be added later, without requiring anything this
specification builds to be redesigned.

#### Acceptance Criteria

1. THE Deployment_Service and Strategies_Page SHALL NOT provide Marketplace listing/subscription workflows or Paper Trading as a distinct execution mode as part of this specification's implementation; both remain explicitly out of scope.
2. WHERE a "List in Marketplace" and/or "Paper Trading" action affordance is displayed on the Strategies_Page, THE Strategies_Page SHALL render it in a disabled, non-interactive state (not clickable, and issuing no backend request when interacted with) or as a placeholder label only, and SHALL NOT invoke any backend endpoint that performs a real marketplace listing or a real paper-trading execution.
3. THE Deployment_Binding's `mode` vocabulary and the Order_Lifecycle_State vocabulary defined by Requirement 16 SHALL be defined such that a future `paper` execution mode value or a future marketplace-sourced deployment source can be added as an additional accepted value without renaming, removing, or redefining any value or transition already defined by this specification.
4. THE database schema changes made under this specification SHALL NOT drop, rename, or destructively alter any existing column or table in a way that would require deleting or irreversibly transforming existing production rows in order to later add support for Marketplace listings or Paper Trading; any such future extension SHALL be addable through additive schema changes (new columns, tables, or enum values) alone.

---

## Non-functional constraints (apply to every requirement above)

- No requirement in this document weakens existing authentication, authorization, row-level
  security, tenant isolation, exchange-credential security, risk-management controls,
  execution guards, or idempotency controls.
- No production functionality specified here SHALL be backed by mock data; no backtest result,
  live signal, or exchange execution outcome SHALL be fabricated.
- No exception on a correctness-critical path in this lifecycle SHALL be silently swallowed;
  every defect discovered while implementing this specification SHALL be fixed at its root
  cause and covered by a regression test.
- Backend validation is authoritative for every workflow in this document; database
  constraints enforce the invariants stated in Requirement 21.
- Every existing production engine reused by this specification (DAG compiler/runtime,
  `execution_engine`, `risk_engine`, `execution_guard`, CCXT integration,
  `market_data_validation`, `credential_vault`/`api_key_vault`, `BacktestRuntime`,
  `BacktestEngine`, `deployment_binding`, `strategy_lifecycle`, `signal_trace_engine`,
  `order_state_engine`, `DistributedIdempotencyLayer`, `ws_channels.OwnedChannelFamily`)
  SHALL be reused as-is or extended; none SHALL be duplicated or replaced by an
  independently constructed equivalent.

---

## Traceability

| User spec functional area | Primary requirement(s) |
|---|---|
| 1. Strategy data model & versioning | Requirement 1 (consumes strategy-builder's existing model), Requirement 21 |
| 2. Strategies Page listing & actions | Requirements 1, 2, 25 |
| 3. Strategy action semantics (nav, edit, delete safety) | Requirements 2, 3 |
| 4. Backtester sidebar entry | Requirement 4 |
| 5. Backtester engine (VectorBT, canonical artifact) | Requirement 5 |
| 6. Backtester page (two entry paths) | Requirement 4 |
| 7. Backtest configuration | Requirement 6 |
| 8. Data feed selection | Requirement 7 |
| 9. Historical data validation | Requirement 7 |
| 10. VectorBT execution pipeline | Requirement 5, 8 |
| 11. Backtest results/metrics | Requirement 8 |
| 12. Backtest visualization | Requirement 9 |
| 13. Backtest trade table | Requirement 9 |
| 14. Save backtest result | Requirement 10 |
| 15. Saved backtests (view/open/compare/delete) | Requirement 10 |
| 16. Backtest reproducibility | Requirement 10 |
| 17. Live deployment (reuse execution architecture) | Requirement 11 |
| 18. Exchange connection prerequisite | Requirement 12 |
| 19. Deployment validation | Requirement 13 |
| 20. Live market data | Requirement 14 |
| 21. Live strategy execution | Requirement 14 |
| 22. Signal generation | Requirement 15 |
| 23. Signal Trace status model | Requirement 16 |
| 24. Signal Trace frontend | Requirement 17 |
| 25. Realtime Signal Trace | Requirement 18, 23 |
| 26. Order/Signal state machine | Requirement 16 |
| 27. Idempotency | Requirement 19 |
| 28. Crash recovery | Requirement 19 |
| 29. Security/ownership | Requirement 20 |
| 30. Database schema | Requirement 21 |
| 31. APIs | Requirement 22 |
| 32. WebSocket | Requirement 23 |
| 33. Frontend state management | Requirement 24 |
| 34. UI quality | Requirement 25 |
| 35. Strategy card/page UX | Requirement 25 |
| 36. Deployment UX | Requirement 13 |
| 37. Backtester UX | Requirement 9, 25 |
| 38. Test strategy for lifecycle validation | Requirement 26 |
| 39. Extension points (Marketplace/Paper Trading) | Requirement 27 |
