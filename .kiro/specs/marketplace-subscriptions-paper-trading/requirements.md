# Requirements Document

## Introduction

This specification covers three connected capabilities: a **Strategy Marketplace** that
publishes a saved strategy without disclosing its logic, **paid one-month subscriptions**
to published strategies with a 90/10 owner/platform split, and **real-time Paper Trading**
that executes a strategy against real market data with simulated capital.

It sits after `.kiro/specs/strategy-builder/` (which defines how a strategy and its
immutable versions are produced) and after
`.kiro/specs/trading-lifecycle-integration/` (which defines backtesting, live deployment,
signal generation and Signal Trace). That second specification explicitly deferred both
Marketplace workflows and Paper Trading as a distinct execution mode, and recorded in its
Requirement 27 the extension points this specification is expected to use: an additive
`mode` value for paper execution, an additive marketplace-sourced deployment source, and
additive-only schema change. **This document is the deferred work. It does not redefine the
Order_Lifecycle_State vocabulary of that specification's Requirement 16, does not rebuild
the Backtest_Engine, the Deployment_Gate, the DAG runtime, the credential vault, or the
Signal Trace data model, and does not introduce a second payment system, a second API
layer, a second WebSocket layer, or a second market-data pipeline.**

### What the repository audit found

A marketplace, a subscription path and a paper-trading service **already exist**. None of
the three is complete, and several parts are actively defective. Every classification below
was read from the files named.

| Component | File(s) read | Status |
|---|---|---|
| Marketplace API (browse, featured, trending, categories, detail, publish, unpublish, clone, rate, favorites, compare, recommendations, admin moderate, admin pending) | `backend_app/routers/library.py` (2497 lines) | **EXISTS+PARTIAL** |
| Marketplace listing table | `archived_migrations/root_migrations/001_create_library_strategies.sql`, `migrations/007_add_marketplace_pricing_columns.sql` | **EXISTS+PARTIAL** — `library_strategies` with backtest metric columns, `moderation_status` in (`pending`,`approved`,`rejected`,`featured`), plus `price`, `currency`, `subscription_tier`, `evaluation_score`, `verification_status` added by migration 007, whose own header records that these columns were missing in production and that `/api/library/trending` and `/featured` were silently returning empty results with PostgreSQL error 42703 |
| A **second, parallel** marketplace schema, unused by any application code | `backend_app/migrations/001_strategy_architecture.sql` (`marketplace_listings`, `strategy_subscriptions`) — referenced only by `tests/test_schema_as_code_completeness.py` and `.kiro/specs/strategy-builder/design.md` | **EXISTS+PARTIAL (dormant)** |
| Publication eligibility gate | `library.py` `publish_strategy` | **EXISTS+PARTIAL** — checks ownership, presence of one `strategies.backtest_result` JSON blob, presence of an action node, no duplicate active listing, and a heuristic `evaluation_score` threshold. **No multi-condition backtest requirement, no per-condition evidence, no distinctness check, no execution-success check** |
| Submission lifecycle | `library.py`, `001_create_library_strategies.sql` | **MISSING** — a single `moderation_status` column with four values; no `DRAFT`/`SUBMITTED`/`UNDER_REVIEW`/`PUBLISHED` distinction, no transition validation, no rejection-reason column separate from `moderation_notes`, no suspend action |
| Admin review authorization | `backend_app/core/dependencies.py` `get_admin_user` (role read from `app_metadata` only), `library.py` `admin_moderate_strategy`, `admin_pending_strategies` | **EXISTS+WORKING** for authorization; **EXISTS+PARTIAL** for the review surface (no backtest evidence view, no risk-metric view, no suspend, no audit write) |
| Public listing projection | `library.py` `get_library_detail` | **EXISTS+BROKEN** — returns `select("*")` of the listing row, strips only `author_id`; every internal column (including `source_strategy_id`, `moderation_notes`, `moderated_by`, `evaluation_score`, `deployment_requirements`, `version_history`) reaches the client |
| Clone path (full strategy logic transfer) | `library.py` `clone_strategy` | **EXISTS+WORKING as built, CONFLICTS with this specification** — copies `buy_logic`, `sell_logic`, `risk`, `indicators`, `ml_model_path` from the author's `strategies` row into a new row owned by the caller, gated only on `require_marketplace_access` and approval status, not on any owner consent or subscription |
| Pricing guidance | `library.py` `publish_strategy` | **EXISTS+PARTIAL** — four hardcoded price points (`29.99`/`49.99`/`99.99`/`199.99`) selected by a hand-weighted `evaluation_score`; no minimum, no maximum, no server-side range enforcement on the owner's submitted `price` |
| Marketplace checkout | `library.py` `create_marketplace_checkout` | **EXISTS+BROKEN** — the query uses `.single()` (which yields a dict) and the next statement indexes it as `resp.data[0]`, outside any `try`; the amount is computed as `int(float(price) * 100)` (floating point); `expires_at` is inserted as `NULL`; no platform-fee or owner-share value is recorded |
| Subscription activation | `backend_app/routers/billing.py` `_apply_marketplace_entitlement`, webhook `item_key` prefix `marketplace_` | **EXISTS+PARTIAL** — flips `library_subscriptions.status` from `pending` to `active` idempotently; sets no period end; records no settlement |
| Subscription renewal | `library.py` `renew_subscription` | **EXISTS+BROKEN** — sets `status='active'`, `cancelled_at=NULL`, `expires_at=NULL` **with no payment of any kind**; grants deployment permission and increments `subscriber_count` |
| Subscription cancellation | `library.py` `cancel_subscription` | **EXISTS+PARTIAL** — cancels immediately and revokes deployment permission at once rather than at period end |
| Subscription expiry | searched `library.py`, `billing.py`, `backend_app/workers/` | **MISSING** — no code sets or evaluates `library_subscriptions.expires_at` |
| Revenue split | `library.py` `creator_analytics` | **EXISTS+BROKEN** — the only 90/10 arithmetic in the codebase multiplies `clone_count` by `monthly_price` in floats, and selects the columns `monthly_price` and `rating_average`, **neither of which exists** on `library_strategies` (the real columns are `price` and `avg_rating`); the handler's bare `except` then returns all-zero figures, so the number a creator sees is a fabricated zero |
| Creator payout ledger for marketplace revenue | searched all `*.sql` and `backend_app/**` | **MISSING** — `referral_payouts`/`referral_wallets` exist for the referral programme only |
| Subscription tables | `migrations/006_reconcile_production_database.sql` | **EXISTS+PARTIAL** — `library_subscriptions` (with RLS, unique `(library_id, user_id)`) and `deployment_permissions`; no fee/share columns, no event history table, no refund state |
| Deprecated subscription service | `backend_app/backend/subscription_service.py` | **EXISTS+PARTIAL** — every method raises `NotImplementedError` pointing at `/api/library/*` |
| Billing architecture (plans, entitlements, currency USD/INR, invoices, payment methods, Stripe + Razorpay checkout, signed/IP-allowlisted/timestamp-validated webhooks, portal, cancel, resume) | `backend_app/routers/billing.py`, `backend_app/core/entitlement_engine.py`, `backend_app/core/subscription_dependencies.py` | **EXISTS+WORKING** — and is the only payment architecture this specification may use |
| Marketplace entitlement flags | `core/entitlement_engine.py` (`MARKETPLACE_ACCESS`, `STRATEGY_SHARING`, `STRATEGY_SUBSCRIPTION`) | **EXISTS+WORKING** |
| Backtest persistence with per-run parameters | `backend_app/migrations/001_strategy_architecture.sql` (`strategy_backtests`: `version_id`, `blueprint`, `dataset`, `start_date`, `end_date`, `initial_capital`, `commission`, `slippage`, `status`, metrics, `equity_curve`), `backend_app/backend/backtest_service.py` (`dag_hash`, `dataset_checksum`, `engine_version`, `schema_version`, owner-scoped writes) | **EXISTS+WORKING** — this is the only legitimate source of marketplace backtest evidence |
| Single-blob backtest field used by the current publish gate | `archived_migrations/root_migrations/003_add_library_columns_to_strategies.sql` (`strategies.backtest_result` JSONB) | **EXISTS+PARTIAL** — one blob per strategy, overwritten, with no parameters, no version reference and no reproducibility metadata |
| VectorBT engine | `backend_app/backend/backtesting_engine.py` (`vbt.Portfolio.from_signals`, 50-bar minimum guard, whitelisted frequency map, `records_readable` trade extraction, 20-trade significance warning, pandas fallback when `vectorbt` is not importable) | **EXISTS+WORKING** |
| Canonical backtest runtime | `backend_app/backend/backtest_runtime.py` (DAG engine + VectorBT + risk engine + `BacktestService`) | **EXISTS+WORKING** |
| Paper trading service | `backend_app/backend/paper_trading_service.py` | **EXISTS+PARTIAL** — Decimal accounting, per-user asyncio locks, idempotency cache, market/limit orders, long/short/reversal, the invariant `total_equity = available + locked + position_market_value`; **all state is in process memory** (`self._accounts`, `self._positions`, `self._orders`, `self._trades`), so every balance, position and trade is lost on restart or on a second container |
| Paper trading API | `backend_app/routers/paper_trading.py` (`/api/paper/account`, `/account/reset`, `/positions`, `/orders`, `/trades`, `/summary`) | **EXISTS+PARTIAL** — no session concept, no strategy execution, no marketplace/subscription linkage, no WebSocket events |
| Paper trading persistence | searched all `*.sql` | **MISSING** — no `paper_*` table exists |
| Paper trading dependants | `backend_app/routers/risk.py` (risk utilisation read from the in-memory paper account), `algo22-terminal/src/pages/Portfolio.jsx`, `pages/TradeHistory.jsx` (both read `api.paper.*`) | **EXISTS+PARTIAL** — three surfaces already present in-memory paper figures to users |
| Synthetic exchange simulator | `backend_app/backend/exchange_simulator.py` | **EXISTS, MUST NOT BE USED FOR USER-FACING PAPER TRADING** — prices are produced by `random.gauss` around a base and fills by `random.random() > fill_probability`; it is a staging/self-test harness, not a real-market simulator |
| CCXT sandbox/testnet availability | `backend_app/core/exchange_certification.py`, `core/exchange_connection_schema.py`, `routers/exchange.py` (`sandbox_support` from CCXT `has`) | **EXISTS+PARTIAL** — sandbox is a per-connection credential option, unavailable for Kraken ("No public CCXT sandbox endpoint") and Bitfinex, and requires the user to hold exchange credentials |
| Market-data source selection rule | `backend_app/backend/market_data_latency.py` (`choose_market_data_source`, correctness floor before latency, 25 ms p99 margin, `NOT_MEASURED` fails the floor), `backend/market_data_contract.py`, `backend/market_data_validation.py` | **EXISTS+WORKING** |
| Live market-data paths | `backend_app/mds/main.py` (`watch_ohlcv` + REST fallback `fetch_ohlcv`), `backend/data_seeking_engine.py` (`watch_ticker`, `stream_live_ohlcv`), `api_ws/ws_routes.py` (`broadcast_ticker`) | **EXISTS+WORKING** |
| DEV_MODE mock exchange interface | `backend_app/backend/connection_engine.py` `_apply_mock_interface` (fabricated OHLCV, ticker, balance and order responses, installed when a real connection fails **and** `DEV_MODE` is set) | **EXISTS+WORKING as a dev aid, hazardous for paper trading** |
| WebSocket infrastructure | `backend_app/api_ws/ws_manager.py` (channels `ticker`, `orderbook`, `candles`, `user`, `pnl`, `marketplace`, `dashboard`, `strategy`, Redis bridge, per-user connection tracking, `broadcast_marketplace` already called on publish and rating), `backend/ws_channels.py` (`ChannelType`, `OwnedChannelFamily` for `training.{job_id}`, `strategy.{id}`, `deployment.{id}`, `execution.{id}`) | **EXISTS+WORKING** — **no paper-trading channel family exists** |
| Frontend marketplace page | `algo22-terminal/src/pages/StrategyMarketplace.jsx`, route `/marketplace` (public) and `/app/marketplace` in `src/App.jsx` | **EXISTS+PARTIAL** — calls `/api/library/*` directly through `client`/`publicGet` rather than through `src/api/modules/*`, and its `handleClone`/`handleSubscribe` use `alert()` |
| Frontend paper API module | `algo22-terminal/src/api/modules/paper.js`, exported as `api.paper` | **EXISTS+WORKING** |
| Frontend paper trading page | searched `src/pages`, `src/App.jsx` | **MISSING** — no paper trading route or page; `api.paper` is consumed only by `Portfolio.jsx` and `TradeHistory.jsx` |
| Frontend library API module | searched `src/api/modules` | **MISSING** |
| Signal Trace | `backend_app/migrations/002_signal_trace.sql` (`signals`, RLS, indices), `backend/signal_trace_engine.py`, `routers/signal_trace.py`, `pages/SignalTrace.jsx` | **EXISTS+PARTIAL** — the `signals` table has no column distinguishing live from paper from backtest execution (grep for `environment`/`PAPER` in `signal_trace_engine.py` returns nothing) |
| Tenant isolation machinery | `core/tenant.py`, `core/tenant_middleware.py`, `backend/tenant_rls_validator.py`, RLS policies in `006_reconcile_production_database.sql` and `001_strategy_architecture.sql` | **EXISTS+WORKING** |
| Audit trail | `core/audit_trail.py` (`OrderAuditLogger`, `StrategyAuditLogger`) | **EXISTS+WORKING** — not called by any marketplace or subscription handler |
| Existing test coverage | `tests/test_marketplace_pipeline.py` (publish→approve→browse→clone→rate over a `FakeDB`), `tests/test_marketplace_concurrency.py`, `tests/test_library_schema_contract.py`, `tests/test_paper_trading_lifecycle.py` (10 batteries incl. the equity invariant, concurrency and idempotency), `tests/test_billing_e2e.py`, `tests/test_tenant_isolation_*`, `tests/sandbox_lifecycle/` | **EXISTS+PARTIAL** |
| Property-based testing capability | `requirements-dev.txt` pins `hypothesis==6.165.10`; `.hypothesis/` database present | **EXISTS+WORKING** |
| CI gates | `.github/workflows/01-pr-check.yml` (import/AST audit, dependency audit, flake8 E9/F63/F7/F82, black, isort, `pytest tests/ -k "not chaos and not load"`, Docker validation), `02-build.yml`, `03-deploy.yml`, `04-nightly-audit.yml`, `05-security.yml`, `06-frontend-deploy.yml` (frontend build + a check that the service-role key is absent from the bundle) | **EXISTS+PARTIAL** — no frontend unit-test job runs on a pull request |

### What this specification therefore governs

1. **Correcting the existing marketplace path** rather than adding a parallel one: one
   canonical listing/subscription schema, a real submission lifecycle, a safe public
   projection, and the four defects classified EXISTS+BROKEN above fixed at their root.
2. **Adding what is genuinely absent**: multi-condition backtest evidence and its
   distinctness validation, a defensible pricing range, a subscription period with a real
   expiry, a settlement ledger for the 90/10 split, durable paper-trading persistence,
   paper sessions driven by strategy execution over a real market feed, a paper WebSocket
   channel family, and an environment discriminator on Signal Trace.
3. **Protecting proprietary strategy logic** as a first-class, server-enforced property of
   both the listing projection and the subscriber execution path.
4. **Keeping BACKTEST, PAPER and LIVE strictly separated** in data, in credentials, in
   accounting and in presentation.

### Priority order

Where two requirements in this document cannot both be satisfied by a single
implementation choice, the earlier item in this list wins: (1) security, tenant isolation
and financial correctness; (2) marketplace eligibility and proprietary-logic protection;
(3) subscription billing and 90/10 settlement correctness; (4) paper trading correctness
and real-time data integrity; (5) strategy execution and Signal Trace; (6) API and
WebSocket reliability; (7) frontend experience; (8) performance.

---

## Glossary

- **Marketplace**: The public catalogue of published strategies, served by the existing `/api/library/*` surface (`backend_app/routers/library.py`) over the existing `library_strategies` table. The term "Strategy Library" in existing code and this term denote the same thing.
- **Listing**: One `library_strategies` row representing one owner's strategy offered to other users.
- **Listing_Projection**: The server-side function that maps a Listing to the field set returned to a non-owner client. The only permitted source of public Listing data.
- **Protected_Logic**: Everything that expresses how a strategy decides: the canonical graph and DAG structure, node/block configuration, indicator parameters, feature-engineering configuration, ML/DL model artifacts, model hyperparameters, feature schemas, thresholds, entry/exit conditions, compiled plan, `strategies.buy_logic`, `strategies.sell_logic`, `strategies.indicators`, `strategies.risk`, `strategies.ml_model_path`, `strategy_versions` graph documents, `strategy_backtests.blueprint`, and any serialisation of the foregoing.
- **Eligibility_Gate**: The server-side validation that decides whether a strategy may be submitted to the Marketplace.
- **Backtest_Evidence**: A set of references to completed `strategy_backtests` rows owned by the strategy owner and produced by the Backtest_Engine, recorded against one Submission, together with the exact parameters and result metrics of each referenced run.
- **Backtest_Condition**: One member of a Backtest_Evidence set: one completed `strategy_backtests` row and its parameters.
- **Evidence_Validator**: The server-side component that decides whether a Backtest_Evidence set satisfies the distinctness and quality rules of Requirement 3.
- **Submission**: One owner request to publish one strategy, carrying its own state, its Backtest_Evidence, its validation outcome, and its review history.
- **Submission_State**: One of `DRAFT`, `SUBMITTED`, `UNDER_REVIEW`, `APPROVED`, `PUBLISHED`, `REJECTED`, `SUSPENDED`, `UNPUBLISHED`.
- **Admin_Reviewer**: A caller authorised by the existing `get_admin_user` dependency (`app_metadata.role` in `admin`, `support`, `operator`).
- **Pricing_Evaluator**: The server-side component that computes `minimum_price`, `recommended_price` and `maximum_price` for a Listing from persisted Backtest_Evidence.
- **Price_Range**: The triple (`minimum_price`, `recommended_price`, `maximum_price`) in minor currency units for one Listing in one currency.
- **Subscription**: One purchaser's paid, time-bounded right to execute one Listing's strategy, persisted as a `library_subscriptions` row.
- **Subscription_State**: One of `PENDING`, `ACTIVE`, `EXPIRED`, `CANCELLED`, `REFUNDED`, `PAYMENT_FAILED`, `SUSPENDED`.
- **Subscription_Period**: One calendar month of access, computed as stated in Requirement 11.
- **Billing_Integration**: The existing payment architecture — `backend_app/routers/billing.py`, its Stripe and Razorpay checkout construction, its signature/IP/timestamp-validated webhooks, and `_apply_marketplace_entitlement`.
- **Minor_Units**: An exact integer count of the smallest indivisible unit of a currency (cents for USD, paise for INR).
- **Settlement_Record**: The persisted, immutable per-payment split of one Subscription payment into `owner_share` and `platform_fee` in Minor_Units, plus the provider transaction reference.
- **Settlement_Ledger**: The complete set of Settlement_Records.
- **Entitlement_Resolver**: The server-side component that answers "which strategy artifact, if any, may this authenticated caller execute right now" from server-side identity alone.
- **Execution_Environment**: One of `BACKTEST`, `PAPER`, `LIVE`. The discriminator that keeps the three regimes separate in data and in presentation.
- **Paper_Account**: A purchaser's or owner's durable simulated-capital account for one currency, isolated from every real balance.
- **Paper_Session**: One bounded run of one strategy in the `PAPER` Execution_Environment against one symbol and timeframe, with its own lifecycle, its own orders, fills, positions, balances, metrics and events.
- **Paper_Simulator**: The server-side component that accepts a Paper_Session's order intents and produces acceptance, rejection, fills, fees and slippage against Paper_Market_Data. It never contacts an exchange trading endpoint.
- **Paper_Market_Data**: Real market data delivered to a Paper_Session through the existing market-data pipeline, selected by the existing `choose_market_data_source` rule.
- **Paper_Order_State**: One of `CREATED`, `ACCEPTED`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `REJECTED`.
- **Paper_Accounting_Engine**: The component that maintains Paper_Account cash, locked funds, positions, realized and unrealized profit and loss, equity and drawdown in exact decimal arithmetic.
- **Paper_Channel**: The parameterised, ownership-authorised WebSocket channel family for Paper_Session events, following the existing `OwnedChannelFamily` pattern in `backend/ws_channels.py`.
- **Signal_Trace_Recorder**: The existing `SignalTraceEngine` and the `signals`/`signal_events` tables it writes.
- **Persistence_Layer**: The database and its constraints, indexes, transaction boundaries and row-level security policies.
- **Marketplace_API**: The `/api/library/*` HTTP surface.
- **Paper_Trading_API**: The `/api/paper/*` HTTP surface.
- **Marketplace_UI**: `algo22-terminal/src/pages/StrategyMarketplace.jsx` and the Listing detail surface.
- **Strategies_Page**: `algo22-terminal/src/pages/Strategies.jsx`.
- **Paper_Trading_UI**: The frontend surface for Paper_Sessions introduced by this specification.
- **Audit_Log**: The existing audit-trail facility in `backend_app/core/audit_trail.py`.
- **Implementation**: The delivered code, schema and configuration produced under this specification, in both the backend and the frontend.
- **Operator**: The person or automated process performing a deployment or a production verification of this platform.
- **Verification_Suite**: The automated test and review apparatus this specification obliges: the backend and frontend test suites, the property-based test suite, the schema-contract tests, the regression tests recorded against pre-change behaviour, the end-to-end journey, and the recorded per-endpoint security review.
- **Order_Lifecycle_State**: The nine-value canonical signal/order vocabulary defined by `.kiro/specs/trading-lifecycle-integration/` Requirement 16. Consumed here, not redefined.

---

## Requirements

### Requirement 1: One canonical Marketplace and Subscription data model

**User Story:** As an engineer maintaining this platform, I want exactly one set of tables
to answer "what is listed" and "who is subscribed", so that no query can read a stale
parallel schema and no handler can select a column that does not exist.

#### Acceptance Criteria

1. THE Marketplace SHALL use `library_strategies` as the single authoritative Listing table and `library_subscriptions` as the single authoritative Subscription table, SHALL resolve every Listing read and write and every Subscription read and write to exactly one of those two tables, and SHALL introduce no third table that stores Listing state or Subscription state.
2. THE Marketplace SHALL NOT read from or write to `marketplace_listings` or `strategy_subscriptions`, and THE Persistence_Layer SHALL retain both tables and their constraints unchanged so that existing schema-completeness tests (`tests/test_schema_as_code_completeness.py`) continue to pass.
3. WHEN a Marketplace handler issues a query to the Persistence_Layer, THE Marketplace SHALL reference only columns that exist in the applied migration set for each referenced table.
4. THE Marketplace SHALL compute every `creator_analytics` figure from the `library_strategies` columns `price` and `avg_rating`, and SHALL reference neither `monthly_price` nor `rating_average` in any query.
5. IF a Marketplace read from the Persistence_Layer fails for any reason — connection failure, query timeout, undefined column, or permission denial — THEN THE Marketplace_API SHALL return a structured error response carrying a stable machine-readable error code and an HTTP status of 500 or 503, and SHALL NOT return a numerically populated success response derived from default values.
6. THE Marketplace SHALL retain `strategies.backtest_result` unchanged for backward compatibility, AND THE Eligibility_Gate SHALL derive no eligibility decision from `strategies.backtest_result`.
7. IF a Marketplace handler catches an exception while assembling a response, THEN THE Marketplace_API SHALL return an error response, and SHALL NOT return a success response in which any numeric field holds a zero, a null-coerced value, or any value that was not read from the Persistence_Layer.
8. THE Verification_Suite SHALL include a check that fails when any application module reachable from the Marketplace_API references `marketplace_listings` or `strategy_subscriptions`, excluding migration files and the schema-completeness tests.
9. THE Verification_Suite SHALL include a check that fails when any column referenced by a Marketplace query is absent from the applied migration set for that column's table.

---

### Requirement 2: Publication eligibility is decided entirely on the server

**User Story:** As a platform operator, I want every publication precondition enforced by the backend, so that a caller with a valid token and a crafted request body cannot place an unvalidated strategy in front of paying users.

#### Acceptance Criteria

1. WHEN an authenticated owner requests publication of a strategy, THE Eligibility_Gate SHALL evaluate every criterion in this requirement server-side, using values it reads from the Persistence_Layer, and SHALL ignore any eligibility, validation, score, price-range or status value present in the request body.
2. THE Eligibility_Gate SHALL admit a strategy only when the strategy row exists, its `user_id` equals the authenticated caller's identity, and its tenant matches the caller's tenant context.
3. THE Eligibility_Gate SHALL admit a strategy only when the strategy has at least one saved immutable Strategy_Version and the Strategy_Version passes the Strategy_Builder's existing structural validation for its canonical graph.
4. THE Eligibility_Gate SHALL admit a strategy only when the Strategy_Version referenced by the Backtest_Evidence has at least one execution recorded with status `completed` and when no execution of that Strategy_Version referenced by the Backtest_Evidence carries a non-null, non-empty `error_message` or a status other than `completed`.
5. THE Eligibility_Gate SHALL admit a strategy only when its Backtest_Evidence satisfies Requirement 3 in full.
6. THE Eligibility_Gate SHALL admit a strategy only when, for every Backtest_Condition, the persisted row carries a non-null, finite numeric value for each of: `total_return_pct`, `sharpe_ratio`, `max_drawdown`, `win_rate`, `profit_factor`, `total_trades`, `final_capital`.
7. THE Eligibility_Gate SHALL admit a strategy only when no other Submission for the same strategy is in state `SUBMITTED`, `UNDER_REVIEW` or `APPROVED`, and no `library_strategies` row for the same `source_strategy_id` is currently in state `PUBLISHED`.
8. WHILE a Submission operation for one strategy is in progress, THE Marketplace SHALL serialise any concurrent Submission operation for the same strategy such that at most one Submission row is created, using a uniqueness constraint in the Persistence_Layer as the final arbiter, AND SHALL reject every losing concurrent operation with an error indicating that a Submission for that strategy already exists, creating no additional Submission row and changing no existing Submission_State.
9. IF a caller lacks the `STRATEGY_SHARING` or marketplace-publish entitlement resolved by the existing `subscription_dependencies`, THEN THE Marketplace_API SHALL reject the publication request with HTTP 403 and SHALL create no Submission row.
10. IF the Eligibility_Gate rejects a strategy, THEN THE Marketplace_API SHALL return, in one response, every criterion of this requirement that failed rather than only the first failure, as a list of stable machine-readable codes with owner-actionable messages, SHALL create no Submission row, SHALL leave the strategy and any existing Submission unchanged, and SHALL exclude from that response any internal validation threshold, internal identifier, database column name, query text, stack trace and security-control detail.
11. THE Eligibility_Gate SHALL record every evaluation outcome — admitted or rejected — in the Audit_Log with the strategy identifier, the evaluated Strategy_Version identifier, the acting identity, the list of evaluated criteria, the pass or fail outcome per criterion, the evaluating server version, and a timestamp, retrievable by an Admin_Reviewer for at least the retention period applied to existing Audit_Log records.
12. WHEN the Eligibility_Gate admits a strategy, THE Marketplace SHALL create exactly one Submission row for that strategy in state `SUBMITTED`, SHALL record on it the admitted Strategy_Version identifier and the Backtest_Evidence references that were evaluated, and SHALL return the created Submission identifier to the owner.
13. IF the Eligibility_Gate cannot read a value required by Criteria 2 through 7 because a Persistence_Layer read fails or does not complete, THEN THE Eligibility_Gate SHALL treat the evaluation as not admitted, THE Marketplace_API SHALL create no Submission row, and THE Marketplace_API SHALL return an error indicating that eligibility could not be evaluated, distinguishable by its stable code from a criteria-failure response.

---

### Requirement 3: Backtest evidence is real, persisted, and genuinely distinct across three conditions

**User Story:** As a subscriber, I want the performance shown on a Listing to come from at least three meaningfully different tests of the same strategy, so that a single lucky window cannot be sold to me as a track record.

#### Acceptance Criteria

1. THE Evidence_Validator SHALL require at least 3 and at most 10 Backtest_Conditions per Submission, each referencing a distinct `strategy_backtests` row whose `user_id` equals the strategy owner and whose `strategy_id` equals the submitted strategy.
2. THE Evidence_Validator SHALL require every referenced `strategy_backtests` row to have `status = 'completed'`, a non-null `completed_at`, and a null or empty `error_message`.
3. THE Evidence_Validator SHALL require every referenced `strategy_backtests` row to reference the same `version_id`, so that all three conditions test one immutable Strategy_Version.
4. THE Evidence_Validator SHALL require every referenced `strategy_backtests` row to record `dataset`, `start_date`, `end_date`, `initial_capital`, `commission`, `slippage`, `dataset_checksum` and `dag_hash` as non-null values.
5. THE Evidence_Validator SHALL treat two Backtest_Conditions as distinct only when at least one of the following holds: (a) their `dataset` values differ; (b) the number of calendar days in the intersection of their `[start_date, end_date]` windows is at most 25 percent of the number of calendar days in the shorter of the two windows, where each window length is counted as inclusive calendar days, the intersection length is 0 when the windows do not overlap, and the comparison is evaluated without rounding as `intersection_days × 4 ≤ shorter_window_days`.
6. THE Evidence_Validator SHALL require every pair of Backtest_Conditions in the Submission to be distinct under Criterion 5, and SHALL require all `dataset_checksum` values in the set to be pairwise different.
7. THE Evidence_Validator SHALL require each Backtest_Condition to span at least 90 calendar days between `start_date` and `end_date` inclusive, and to record `total_trades` of at least 20, matching the statistical-significance threshold the Backtest_Engine already warns on.
8. THE Evidence_Validator SHALL require each Backtest_Condition to record a non-null executed bar count of at least the Backtest_Engine's existing 50-bar minimum, and SHALL reject a Backtest_Condition whose recorded bar count is null, absent, or below 50.
9. WHEN a Submission is created, THE Marketplace SHALL persist, per Backtest_Condition, in the same Persistence_Layer transaction that records the Submission, an immutable copy of the exact parameters (`dataset`, `start_date`, `end_date`, `initial_capital`, `commission`, `slippage`, `dataset_checksum`, `dag_hash`, `engine_version`) and the exact result metrics read from `strategy_backtests`, together with the source row identifier.
10. IF any request attempts to modify or delete a persisted Backtest_Evidence field for an existing Submission, THEN THE Marketplace SHALL reject the request with an error indicating that persisted evidence is immutable, and SHALL leave every persisted Backtest_Evidence value unchanged.
11. THE Marketplace SHALL derive every backtest figure it displays or stores for a Listing from the persisted Backtest_Evidence, AND SHALL NOT compute, estimate, extrapolate, default or substitute any backtest figure that the persisted evidence does not contain.
12. WHEN the same Submission's Backtest_Evidence is re-validated, THE Evidence_Validator SHALL produce the same admit/reject outcome and the same per-criterion outcomes for unchanged persisted rows.
13. IF a referenced `strategy_backtests` row is absent, is owned by another user, or is not in status `completed`, THEN THE Evidence_Validator SHALL reject the Submission with a code identifying which condition failed, SHALL NOT disclose whether the row exists under another owner, SHALL persist no Backtest_Evidence for that Submission, and SHALL leave the Submission_State unchanged.
14. IF a Submission fails any of Criteria 1 and 3 through 8, THEN THE Evidence_Validator SHALL reject the Submission with a code identifying each failed criterion and the identifiers of the affected Backtest_Conditions, SHALL persist no Backtest_Evidence for that Submission, and SHALL leave the Submission_State unchanged.
15. IF persisting the Backtest_Evidence copy for any Backtest_Condition fails, THEN THE Marketplace SHALL reject the Submission with an error indicating evidence persistence failure, SHALL retain no partial Backtest_Evidence for that Submission, and SHALL leave the Submission_State unchanged.

---

### Requirement 4: The Submission lifecycle is an explicit, validated state machine

**User Story:** As an owner, I want to see exactly where my submission stands and what
happens next, and as an operator I want illegal status changes to be impossible.

#### Acceptance Criteria

1. THE Submission_State SHALL be one of exactly 8 values: `DRAFT`, `SUBMITTED`, `UNDER_REVIEW`, `APPROVED`, `PUBLISHED`, `REJECTED`, `SUSPENDED`, `UNPUBLISHED`.
2. THE Marketplace SHALL permit only the following Submission_State transitions: `DRAFT → SUBMITTED`; `SUBMITTED → UNDER_REVIEW`; `SUBMITTED → REJECTED`; `UNDER_REVIEW → APPROVED`; `UNDER_REVIEW → REJECTED`; `APPROVED → PUBLISHED`; `PUBLISHED → SUSPENDED`; `PUBLISHED → UNPUBLISHED`; `SUSPENDED → PUBLISHED`; `SUSPENDED → UNPUBLISHED`; `REJECTED → DRAFT`; AND SHALL permit no other transition, including any transition from a value to that same value and any transition out of `UNPUBLISHED`.
3. WHEN THE Marketplace creates a Submission, THE Marketplace SHALL set its Submission_State to `DRAFT` and SHALL record the creation timestamp in UTC.
4. IF a write would set a Submission_State to a value not reachable from its current value under Criterion 2, THEN THE Persistence_Layer SHALL reject the write, SHALL leave the stored Submission_State unchanged, SHALL leave the Listing's visibility to the Listing_Projection unchanged, SHALL record no transition history entry for the rejected write, and SHALL return an error naming the current Submission_State and the rejected transition.
5. THE Persistence_Layer SHALL enforce the permitted Submission_State value set with a check constraint, and SHALL enforce, with a partial unique index, at most one Submission per strategy whose Submission_State is one of `SUBMITTED`, `UNDER_REVIEW`, `APPROVED` or `PUBLISHED`.
6. WHEN a Submission enters `PUBLISHED`, THE Marketplace SHALL, in the same database transaction as the transition, mark the Listing available to the Listing_Projection and record the publication timestamp in UTC, such that every public catalogue response and public detail response served after that transaction commits includes the Listing.
7. WHILE a Submission is in `DRAFT`, `SUBMITTED`, `UNDER_REVIEW`, `APPROVED`, `REJECTED`, `SUSPENDED` or `UNPUBLISHED`, THE Listing_Projection SHALL exclude the Listing from every public catalogue response and every public detail response.
8. WHEN a Submission enters `REJECTED`, THE Marketplace SHALL persist a rejection reason supplied by the Admin_Reviewer of at least 1 and at most 2000 characters as required by Requirement 5 Criterion 5, SHALL make that reason readable by the Submission's owner and by every Admin_Reviewer, and SHALL exclude from the owner-visible reason any internal validation threshold, internal identifier, database column name, query text, stack trace, security-control detail and other users' data.
9. IF an Admin_Reviewer requests a transition to `REJECTED` without a reason, with an empty or whitespace-only reason, or with a reason longer than 2000 characters, THEN THE Marketplace_API SHALL reject the request, SHALL leave the stored Submission_State unchanged, SHALL persist no rejection reason, and SHALL return an error indicating that a rejection reason within the permitted length is required.
10. WHEN a Submission enters `SUSPENDED`, THE Marketplace SHALL leave every existing `ACTIVE` Subscription to that Listing in Subscription_State `ACTIVE` and readable by its purchaser, SHALL leave every affected Subscription_Period expiry and every Settlement_Record unchanged, and SHALL reject the creation of any new Subscription to that Listing with an error indicating the Listing is unavailable for new Subscriptions.
11. WHILE a Submission is in `SUSPENDED` or `UNPUBLISHED`, THE Entitlement_Resolver SHALL continue to resolve execution entitlement for each Subscription to that Listing that was `ACTIVE` at the moment of the transition, until that Subscription's Subscription_Period expiry, and SHALL resolve no entitlement for any other caller.
12. THE Marketplace SHALL map each Submission_State to exactly one existing `library_strategies.moderation_status` value for backward compatibility with the current handlers, SHALL define that mapping in exactly one shared definition, and SHALL derive every handler's `moderation_status` value from that definition rather than from a mapping written at the call site.
13. THE Marketplace SHALL record every Submission_State transition with the prior value, the new value, the acting identity, the reason where one applies, and the transition timestamp in UTC, SHALL retain each recorded entry unmodified thereafter, and SHALL make the entries for one Submission retrievable by its owner and by an Admin_Reviewer ordered by transition timestamp ascending.

---

### Requirement 5: Admin verification is server-authorised, evidence-based, and audited

**User Story:** As an Admin_Reviewer, I want to see the real evidence behind a submission
and act on it, and as an operator I want every one of those actions attributable.

#### Acceptance Criteria

1. THE Marketplace_API SHALL authorise every review operation — the Submission list of Criterion 2, the Submission detail of Criterion 3, and each action of Criterion 4 — with the existing `get_admin_user` dependency on the server, AND SHALL NOT rely on any role, flag or capability supplied by the client.
2. THE Marketplace_API SHALL provide an Admin_Reviewer with a list of Submissions filterable by one or more Submission_State values, returning all Submission_State values when no filter is supplied, ordered by submission time descending with the Submission identifier as tie-break, paginated with a caller-supplied page size of 1 to 100 items defaulting to 25 items and clamped to 100 items when a larger value is requested, and SHALL return the total count of matching Submissions with each page.
3. THE Marketplace_API SHALL provide an Admin_Reviewer, for one Submission, with: the Listing metadata, every Backtest_Condition's persisted parameters and metrics, the aggregate performance summary, the risk metrics, the Eligibility_Gate evaluation outcome per criterion, and the Submission_State transition history in chronological order carrying the prior value, the new value, the acting identity, the reason where one applies, and the transition timestamp.
4. THE Marketplace_API SHALL provide the Admin_Reviewer with the actions approve, reject with reason, publish, suspend and unpublish, and SHALL execute each only as a transition permitted by Requirement 4 Criterion 2, evaluated against the Submission_State persisted at the moment of execution.
5. WHEN an Admin_Reviewer rejects a Submission, THE Marketplace_API SHALL require a reason containing at least 1 non-whitespace character and at most 2000 characters after trimming leading and trailing whitespace, and SHALL persist the trimmed reason against the Submission.
6. WHEN an Admin_Reviewer performs any action in Criterion 4, THE Marketplace SHALL write an Audit_Log entry through `backend_app/core/audit_trail.py` recording the acting identity, the action, the Submission identifier, the Listing identifier, the prior and new Submission_State, the reason where one applies, and the timestamp, AND SHALL commit that entry in the same transaction as the Submission_State change so that neither is persisted without the other.
7. IF a caller without an Admin_Reviewer role invokes any review operation, THEN THE Marketplace_API SHALL respond with HTTP 403, SHALL make no state change, and SHALL return a response that does not reveal whether the named Submission exists.
8. THE Marketplace_API SHALL NOT expose Protected_Logic to an Admin_Reviewer through any review endpoint.
9. IF an Admin_Reviewer invokes an action in Criterion 4 for a Submission identifier that does not exist, or for a Submission whose persisted Submission_State does not permit the action's implied transition under Requirement 4 Criterion 2, THEN THE Marketplace_API SHALL refuse the action, SHALL leave the stored Submission_State and rejection reason unchanged, and SHALL return an error whose code distinguishes an unknown Submission from a rejected transition and names the rejected transition where one applies.
10. IF an Admin_Reviewer invokes the reject action with a reason that is absent, contains no non-whitespace character, or exceeds 2000 characters after trimming, THEN THE Marketplace_API SHALL refuse the action, SHALL leave the stored Submission_State unchanged, and SHALL return a validation error identifying the reason field and the violated bound.
11. IF the Audit_Log write required by Criterion 6 fails, THEN THE Marketplace SHALL roll back the Submission_State change, SHALL leave the stored Submission_State unchanged, and SHALL return an error indicating that the action was not recorded.

---

### Requirement 6: A public Listing exposes safe metadata only

**User Story:** As a strategy owner, I want to sell access to my strategy without giving away how it works, and as a subscriber I want enough information to judge it.

#### Acceptance Criteria

1. THE Listing_Projection SHALL return a Listing to a non-owner using an explicit allow-list of fields, SHALL omit every field not named in that allow-list irrespective of its presence in the persisted record, AND SHALL NOT return a Listing by serialising the whole database row.
2. THE Listing_Projection's allow-list SHALL be limited to: listing identifier, name, description, category, difficulty, tags, supported timeframes, asset and market information, aggregate performance summary, per-condition backtest summary, risk metrics, maximum drawdown, number of Backtest_Conditions, validation status, subscription price, currency, Subscription_Period length, creator display representation, publication date, subscriber count, and — where the existing rating infrastructure holds values for the Listing — average rating and rating count.
3. THE Listing_Projection SHALL exclude Protected_Logic in every response, without exception, including from the fields named in Criterion 2, such that the aggregate performance summary, per-condition backtest summary, risk metrics and maximum drawdown carry outcome values and Backtest_Condition labels only, and carry no DAG node identifier, node type, connection, indicator name, indicator parameter, threshold, entry or exit rule, or ML model identifier, feature list or weight.
4. THE Listing_Projection SHALL exclude `author_id`, `source_strategy_id`, `moderated_by`, `moderation_notes`, `deployment_requirements`, `version_history`, internal evaluation scores, internal database identifiers other than the Listing identifier, credentials, secrets, API keys and payment provider references.
5. THE Listing_Projection SHALL represent the creator by a display alias resolved server-side, AND SHALL NOT return the creator's user identifier, email address or authentication identity.
6. WHERE a Listing exposes performance figures, THE Marketplace_UI SHALL label each figure with its Execution_Environment (`BACKTEST`, `PAPER` or `LIVE`), SHALL present the three groups in visually separated sections, SHALL omit the section for an Execution_Environment for which the Listing carries no figures rather than rendering a zero or blank figure, AND SHALL display a statement that historical results do not guarantee future results within the section presenting `BACKTEST` figures.
7. THE Marketplace_API SHALL apply the same Listing_Projection to authenticated and unauthenticated catalogue, search and detail responses, and SHALL rate-limit every catalogue, search and detail endpoint to at most 120 requests per 60-second window per authenticated caller and at most 60 requests per 60-second window per source address for an unauthenticated caller.
8. THE Verification_Suite SHALL assert, for a Listing whose underlying strategy contains DAG nodes, indicator parameters and a bound ML model, that no field of any Marketplace_API catalogue, search or detail response reachable by a non-owner, at any nesting depth including free-text and collection fields, contains any part of that Protected_Logic, and SHALL run that assertion for both an authenticated non-owner caller and an unauthenticated caller.
9. IF the existing rating infrastructure holds no values for a Listing, THEN THE Listing_Projection SHALL omit average rating and rating count from the response rather than returning a substitute value.
10. IF a non-owner requests a Listing that is not in a published Submission_State, THEN THE Marketplace_API SHALL respond as though the Listing does not exist, SHALL return no field from the Listing_Projection, and SHALL NOT disclose the Listing's existence, owner or Submission_State.
11. IF a caller exceeds a rate limit defined in Criterion 7, THEN THE Marketplace_API SHALL reject the request with an error indicating that the request rate limit was exceeded, SHALL return no Listing data, and SHALL leave the Listing unchanged.

---

### Requirement 7: Subscribers execute a published strategy without ever receiving its logic

**User Story:** As a strategy owner, I want a subscriber to be able to run my strategy and see its results without being able to read, copy, edit or export it.

#### Acceptance Criteria

1. IF the authenticated identity of a caller is not the owner of a Listing, THEN THE Marketplace_API SHALL refuse every operation whose response body would contain that Listing's Protected_Logic with HTTP 403, AND SHALL omit Protected_Logic — including strategy graph, node definitions, node parameters, compiled plan and source text — from every field of every response returned to that caller, including error and diagnostic responses.
2. WHERE the Listing's owner has explicitly enabled source cloning for that Listing, THE Marketplace SHALL permit the existing `clone_strategy` operation for a subscriber with an `ACTIVE` Subscription to that Listing.
3. IF a caller invokes `clone_strategy` for a Listing whose owner has not enabled source cloning, THEN THE Marketplace_API SHALL refuse the operation with HTTP 403 and SHALL create no copy of the strategy.
4. THE Persistence_Layer SHALL store the owner's source-cloning choice per Listing, SHALL default that choice to disabled, and SHALL back-fill existing rows to disabled.
5. WHEN a subscriber requests execution of a subscribed strategy, THE Entitlement_Resolver SHALL resolve the executable artifact server-side from the Listing and its `source_strategy_id`, AND THE Marketplace_API SHALL accept from the subscriber only the execution parameters symbol, timeframe, capital and session options, and never a strategy definition, graph, compiled plan, version identifier, owner identifier, tenant identifier or subscription identifier used for authorisation.
6. IF a subscriber's execution request contains any field other than the execution parameters listed in Criterion 5, THEN THE Marketplace_API SHALL refuse the request with HTTP 422, SHALL start no Paper_Session, and SHALL return an error indicating that an unaccepted field was supplied without echoing the supplied value.
7. THE Marketplace_API SHALL derive the authorising identity for every subscriber operation from the authenticated server-side session, AND SHALL read the current Subscription_State from the Persistence_Layer within the same request that performs each execution-affecting operation, ignoring any subscription identifier, entitlement flag or state value supplied by the client.
8. THE Marketplace_API SHALL reject a subscriber request to edit, rename, re-version, recompile, export or download a subscribed strategy's definition with HTTP 403.
9. THE Signal_Trace_Recorder SHALL expose to a subscriber, for a signal produced by a subscribed strategy, only the fields listed in Requirement 23 Criterion 3, AND SHALL exclude node-level trace detail, indicator values, feature values and ML inference detail that reveal Protected_Logic.
10. IF a subscriber's Subscription is not in state `ACTIVE` at the moment of an execution-affecting operation, THEN THE Marketplace_API SHALL refuse the operation with HTTP 403 and a code distinguishing "expired" from "not subscribed".
11. IF the Entitlement_Resolver cannot resolve an executable artifact for a Listing held under an `ACTIVE` Subscription — because the Listing is no longer published or its `source_strategy_id` no longer resolves — THEN THE Marketplace_API SHALL refuse the execution-affecting operation with HTTP 409 and an error indicating the strategy is unavailable, SHALL leave the Subscription_State and Subscription_Period unchanged, and SHALL disclose no Protected_Logic and no owner identity in the response.
12. WHEN THE Marketplace_API refuses an operation under Criterion 1, Criterion 3, Criterion 6, Criterion 8 or Criterion 10, THE Audit_Log SHALL record, within 5 seconds of the refusal, the authenticated caller identity, the Listing identifier, the attempted operation, the refusal reason code and a UTC timestamp, and SHALL record no Protected_Logic in that entry.

---

### Requirement 8: Pricing guidance is derived from real evidence and enforced server-side

**User Story:** As an owner, I want a defensible price range for my strategy, and as a
platform operator I want the accepted price bounded by the server, not by the form.

#### Acceptance Criteria

1. THE Pricing_Evaluator SHALL compute a Price_Range for a Submission from the persisted Backtest_Evidence alone, in Minor_Units, for the Listing's currency.
2. THE Pricing_Evaluator SHALL compute the Price_Range from a documented, deterministic function of the following inputs, each read from persisted Backtest_Evidence: total return, return volatility across conditions, Sharpe ratio, Sortino ratio, maximum drawdown, win rate, profit factor, trade count, number of distinct Backtest_Conditions, cross-condition consistency of returns, and the tested duration in calendar days.
3. WHEN the Pricing_Evaluator runs twice on the same persisted Backtest_Evidence and the same evaluator version, THE Pricing_Evaluator SHALL produce an identical Price_Range.
4. THE Pricing_Evaluator SHALL produce a Price_Range satisfying `minimum_price <= recommended_price <= maximum_price`, with all three values being positive integers in Minor_Units, `minimum_price >= 1` and `maximum_price <= 100000000`.
5. WHERE the platform's existing ML infrastructure (`backend/ml_models.py`, `backend/model_versioning.py`, `backend/ml_training_policy.py`, `backend/training_worker.py`, `model_versions`) is used for pricing, THE Pricing_Evaluator SHALL persist its model through `model_versioning` with the artifact checksum, feature schema, hyperparameters and split metrics that module already records, SHALL version the model, and SHALL produce identical output for identical inputs under a fixed model version.
6. WHILE no labelled pricing dataset exists that satisfies the minimum-data gate the existing `ml_training_policy` defines for the chosen model family, THE Pricing_Evaluator SHALL use a statistical model rather than a trained ML model.
7. IF an accuracy, confidence, precision or error figure for the Price_Range was not measured on a held-out split of real data and persisted with the evaluator version that produced it, THEN THE Pricing_Evaluator SHALL NOT report that figure.
8. WHEN an owner submits a price, THE Marketplace_API SHALL evaluate the price server-side against the persisted Price_Range for that Submission when that Price_Range's inputs digest equals the digest of the Submission's current persisted Backtest_Evidence, SHALL otherwise recompute the Price_Range before evaluating, and SHALL accept the price only when `minimum_price <= submitted_price <= maximum_price` for the Listing's currency.
9. IF a submitted price falls outside the Price_Range, THEN THE Marketplace_API SHALL reject the request with HTTP 400, SHALL return the permitted range, and SHALL create no Listing and no Submission state change.
10. THE Marketplace_API SHALL rate-limit Price_Range evaluation requests to at most 30 requests per authenticated caller per rolling 60-second window, and SHALL persist each evaluation with its inputs digest, its outputs, the evaluator version and a timestamp.
11. THE Marketplace SHALL remove the hardcoded price points `29.99`, `49.99`, `99.99` and `199.99` from the publication path and SHALL replace them with the Pricing_Evaluator's output.
12. THE Marketplace SHALL express and store every price in Minor_Units together with an ISO 4217 currency code.
13. THE Marketplace SHALL NOT perform any pricing arithmetic in binary floating point.
14. IF the persisted Backtest_Evidence for a Submission is absent or lacks any input listed in Criterion 2, THEN THE Pricing_Evaluator SHALL produce no Price_Range and THE Marketplace_API SHALL reject the price submission with an error indicating which required Backtest_Evidence inputs are missing, SHALL create no Listing, and SHALL leave the Submission_State unchanged.
15. IF an authenticated caller exceeds the rate limit defined in Criterion 10, THEN THE Marketplace_API SHALL reject the request with an error indicating the rate limit was exceeded, SHALL compute no Price_Range, and SHALL persist no evaluation record for the rejected request.

---

### Requirement 9: Subscription purchase reuses the existing billing architecture with exact arithmetic

**User Story:** As a purchaser, I want to pay for a strategy subscription through the same
checkout the platform already uses, and as an operator I want the amount charged to be
exactly the amount recorded.

#### Acceptance Criteria

1. THE Marketplace SHALL create every Subscription payment through the Billing_Integration, AND SHALL NOT introduce a second payment provider integration, a second webhook handler or a second checkout construction path.
2. THE Marketplace SHALL compute the charged amount as an exact integer of at least 1 Minor_Unit, in the Listing's currency, from the Listing price persisted at the instant the Subscription is created, SHALL charge the provider exactly that amount, AND SHALL NOT compute a charged amount by multiplying a binary floating-point value.
3. WHEN a purchaser initiates a Subscription, THE Marketplace SHALL, in one database transaction that is committed before any provider session is requested, create the Subscription in state `PENDING` recording the Listing, the purchaser, the owner, the price in Minor_Units, the currency, the computed `platform_fee`, the computed `owner_share`, and the creation timestamp.
4. IF provider session creation fails after a `PENDING` Subscription row is written, THEN THE Marketplace SHALL transition that row to `PAYMENT_FAILED`, SHALL record the failure cause and the failure timestamp on that row, SHALL grant no entitlement, SHALL write no Settlement_Record, AND SHALL NOT delete the row.
5. WHEN the Billing_Integration confirms payment for a marketplace item, THE Marketplace SHALL transition the Subscription identified by the confirmed provider transaction reference from `PENDING` to `ACTIVE`, SHALL set the Subscription_Period start and expiry per Requirement 11, and SHALL write the Settlement_Record, inside one database transaction that commits all of those writes together or none of them.
6. WHEN the Billing_Integration delivers the same payment confirmation more than once, THE Marketplace SHALL produce the same end state as a single delivery, SHALL write at most one Settlement_Record per provider transaction reference, and SHALL extend the Subscription_Period at most once per provider transaction reference.
7. THE Persistence_Layer SHALL enforce at most one Settlement_Record per provider transaction reference with a unique constraint.
8. IF a payment for a `PENDING` Subscription fails or is declined, THEN THE Marketplace SHALL transition that Subscription to `PAYMENT_FAILED`, SHALL grant no entitlement, and SHALL leave the Subscription_State and Subscription_Period of any other Subscription held by the same purchaser for the same Listing unchanged.
9. THE Marketplace SHALL retain the existing webhook signature validation, IP allow-listing and timestamp validation of the Billing_Integration for every marketplace payment event, and SHALL NOT weaken any of them.
10. IF a purchase request targets a Listing that is not in Submission_State `PUBLISHED`, a Listing owned by the requesting purchaser, or a Listing to which the purchaser already holds an `ACTIVE` Subscription, THEN THE Marketplace_API SHALL reject the request with HTTP 409 for the not-`PUBLISHED` case and the already-`ACTIVE` case and with HTTP 400 for the own-Listing case, SHALL return an error indicating which condition applied, SHALL create no provider session, and SHALL create no Subscription row.
11. THE Marketplace SHALL fix the existing `create_marketplace_checkout` defect in which a `.single()` result is indexed as a list, and SHALL cover the corrected path in the Verification_Suite with a regression test that fails against the current code and passes against the corrected code.
12. WHEN provider session creation succeeds, THE Marketplace SHALL record the provider transaction reference and the session creation timestamp against that `PENDING` Subscription before returning the checkout response to the purchaser, and SHALL leave that Subscription in `PENDING` until a payment confirmation or failure is received.
13. IF provider session creation does not return a usable session within 30 seconds of the request, THEN THE Marketplace SHALL abandon the attempt, SHALL apply Criterion 4 to the `PENDING` Subscription, and SHALL return an error indicating that checkout could not be started.
14. IF a payment confirmation carries a provider transaction reference that matches no Subscription, or carries an amount or currency that differs from the amount and currency recorded on the matched Subscription, THEN THE Marketplace SHALL make no Subscription_State transition, SHALL write no Settlement_Record, SHALL grant no entitlement, and SHALL write an Audit_Log entry recording the unmatched or mismatched confirmation.

---

### Requirement 10: The 90/10 split is exact, conserved and auditable

**User Story:** As a strategy owner, I want to receive exactly 90 percent of every payment for my strategy, and as an operator I want the ledger to balance to the minor unit.

#### Acceptance Criteria

1. THE Marketplace SHALL compute, for a payment of `amount` Minor_Units where `amount` is an integer in the inclusive range 0 to 99,999,999,999, `owner_share = (amount * 90) / 100` using integer division that truncates toward zero, and `platform_fee = amount - owner_share`.
2. THE Marketplace SHALL satisfy `owner_share + platform_fee = amount` exactly, for every payment amount, with no rounding remainder unaccounted for.
3. THE Marketplace SHALL perform every split computation on the server in integer Minor_Units or exact decimal arithmetic, AND SHALL NOT perform any split computation in binary floating point.
4. WHEN Billing_Integration confirms a payment, THE Marketplace SHALL persist, within 5 seconds of receiving the confirmation, a Settlement_Record recording the Subscription, the Listing, the owner, the purchaser, `amount`, `owner_share`, `platform_fee`, the currency, the provider transaction reference, and the confirmation timestamp.
5. THE Persistence_Layer SHALL enforce `amount >= 0`, `owner_share >= 0`, `platform_fee >= 0` and `owner_share + platform_fee = amount` with check constraints on the Settlement_Record table, SHALL enforce uniqueness of the combination of provider transaction reference and reversal indicator, AND SHALL reject any write that violates a constraint, leaving the Settlement_Ledger unchanged.
6. THE Marketplace SHALL derive every earnings figure it reports to an owner by summing persisted Settlement_Records, AND SHALL NOT derive an earnings figure from `clone_count`, `subscriber_count`, or any other counter multiplied by a current price.
7. THE Marketplace SHALL report the sum of `owner_share` over a set of Settlement_Records sharing one currency as an exact integer total in Minor_Units, computed as the sum of `owner_share` over non-reversal Settlement_Records minus the sum of `owner_share` over reversal Settlement_Records, before any presentation formatting, AND SHALL NOT combine Settlement_Records of different currencies into a single total.
8. THE Marketplace SHALL NOT delete or modify a persisted Settlement_Record, AND WHERE a payment is refunded, THE Marketplace SHALL record the reversal as an additional Settlement_Record that references the original provider transaction reference, carries a reversal indicator, and carries `amount`, `owner_share` and `platform_fee` magnitudes equal to the refunded portion and satisfying `owner_share + platform_fee = amount`.
9. THE Marketplace SHALL write an Audit_Log entry for every Settlement_Record creation, including reversal Settlement_Records, containing no payment credential, provider secret or card data.
10. IF a payment confirmation is received whose provider transaction reference and reversal indicator already exist in the Settlement_Ledger, THEN THE Marketplace SHALL NOT create an additional Settlement_Record, SHALL leave all reported earnings totals unchanged, AND SHALL write an Audit_Log entry indicating a duplicate confirmation was ignored.
11. IF persisting a Settlement_Record fails, THEN THE Marketplace SHALL retry up to 5 times within 60 seconds, AND IF all attempts fail, THEN THE Marketplace SHALL leave the Settlement_Ledger unchanged, SHALL NOT include the payment in any earnings figure reported to the owner, AND SHALL write an Audit_Log entry indicating settlement persistence failure together with the provider transaction reference for operator reconciliation.

---

### Requirement 11: Subscription lifecycle, expiry and renewal are correct and paid

**User Story:** As a purchaser, I want one month of access for my payment, a clear expiry, and the ability to renew; as an operator I want access to end when the period ends.

#### Acceptance Criteria

1. THE Subscription_State SHALL be one of exactly 7 values: `PENDING`, `ACTIVE`, `EXPIRED`, `CANCELLED`, `REFUNDED`, `PAYMENT_FAILED`, `SUSPENDED`.
2. THE Subscription_State_Machine SHALL permit only the following transitions: `PENDING → ACTIVE`; `PENDING → PAYMENT_FAILED`; `PENDING → CANCELLED`; `ACTIVE → EXPIRED`; `ACTIVE → CANCELLED`; `ACTIVE → REFUNDED`; `ACTIVE → SUSPENDED`; `EXPIRED → ACTIVE`; `CANCELLED → ACTIVE`; `SUSPENDED → ACTIVE`; `SUSPENDED → EXPIRED`; `PAYMENT_FAILED → PENDING`.
3. IF a write would set a Subscription_State to a value not reachable from its current value under Criterion 2, THEN THE Persistence_Layer SHALL reject the write, SHALL leave the stored Subscription_State and the stored period start and expiry unchanged, and SHALL return an error indicating a disallowed Subscription_State transition.
4. WHEN a Subscription becomes `ACTIVE`, THE Marketplace SHALL set the period start to the payment confirmation instant in UTC and the period expiry to the same clock time one calendar month later in UTC, clamping the day of month to the last valid day of the target month where the start day does not exist in that month.
5. WHEN a Subscription is renewed by a confirmed payment, THE Marketplace SHALL set the new expiry to one calendar month after the later of the current expiry and the confirmation instant, computed by the same rule as Criterion 4.
6. THE Marketplace SHALL require a payment confirmed through the Billing_Integration and recorded as a Settlement_Record before every transition into `ACTIVE`, from any of `PENDING`, `EXPIRED`, `CANCELLED` and `PAYMENT_FAILED`.
7. WHILE the current UTC time is at or after a Subscription's expiry and no renewal payment has been confirmed, THE Entitlement_Resolver SHALL treat that Subscription as not entitling, irrespective of the stored Subscription_State value and irrespective of whether the expiry sweep of Criterion 8 has yet run for that Subscription.
8. THE Marketplace SHALL run an expiry sweep at an interval no greater than 60 seconds that transitions every Subscription whose expiry is at or before the current UTC time and whose Subscription_State is `ACTIVE` or `SUSPENDED` to `EXPIRED`.
9. WHEN a purchaser cancels a Subscription, THE Marketplace SHALL perform no further renewal for that Subscription, SHALL retain entitlement until the unchanged current expiry, and SHALL transition the Subscription to `CANCELLED` with the cancellation instant recorded in UTC.
10. WHEN a Subscription ceases to entitle, THE Marketplace SHALL refuse every new deployment request and every new Paper_Session request for that Listing's strategy by that purchaser, returning an error indicating that no entitling Subscription exists.
11. THE Marketplace SHALL retain every Subscription row and every Settlement_Record permanently, AND SHALL NOT delete a Subscription row on cancellation, expiry or refund.
12. THE Marketplace SHALL write to the Audit_Log every Subscription_State transition, every period extension and every entitlement change, each entry carrying the prior value, the new value, the cause, the acting identity where one applies, and the UTC timestamp.
13. THE Persistence_Layer SHALL enforce the permitted Subscription_State value set with a check constraint, and SHALL enforce that an `ACTIVE` Subscription has a non-null period start and a non-null expiry strictly greater than that start.
14. IF a request would transition a Subscription into `ACTIVE` without a payment confirmed through the Billing_Integration for that Subscription_Period, THEN THE Marketplace SHALL reject the request, SHALL leave the stored Subscription_State, period start and expiry unchanged, and SHALL return an error indicating that a confirmed payment is required.
15. WHEN a Subscription ceases to entitle, THE Marketplace SHALL stop every running deployment and every running Paper_Session for that Listing's strategy owned by that purchaser within 60 seconds of the instant entitlement ceased.
16. THE Marketplace SHALL remove the existing `renew_subscription` path that transitions a Subscription to `ACTIVE`, clears its expiry and grants deployment permission without a payment confirmed through the Billing_Integration.

---

### Requirement 12: Owned and subscribed strategies coexist on the Strategies page with correct capabilities

**User Story:** As a user with both my own and subscribed strategies, I want one list that
tells me clearly which is which and offers only the actions each one permits.

#### Acceptance Criteria

1. THE Strategies_Page SHALL display the authenticated user's owned strategies and the strategies of every Listing to which that user holds an entitling Subscription in one list.
2. THE Strategies_Page SHALL label each entry as `OWNED` or `SUBSCRIBED`, AND SHALL derive the label from the backend response rather than from a client-side inference.
3. THE Strategies_Page SHALL offer, for a `SUBSCRIBED` entry, the actions: view listing detail, run backtest where the Listing permits it, deploy live, start paper trading, view performance, view subscription status, renew, and cancel renewal.
4. THE Strategies_Page SHALL NOT offer, for a `SUBSCRIBED` entry, the actions: edit, open in Strategy Builder, view graph, edit blocks, view or edit indicator parameters, view or edit risk configuration, download or export definition, view model parameters, re-version, or delete the owner's strategy.
5. WHERE a `SUBSCRIBED` entry's Subscription does not entitle, THE Strategies_Page SHALL render the entry in an explicit expired state, SHALL disable every execution action, and SHALL offer renewal.
6. THE Strategies_Page SHALL display, per `SUBSCRIBED` entry, the Subscription_State, the period expiry, and the renewal state.
7. THE Marketplace_API SHALL enforce every restriction in Criterion 4 on the server, AND SHALL return HTTP 403 for a restricted operation even when the client offers no such affordance.
8. THE Strategies_Page SHALL render explicit loading, empty, error-with-retry, unauthorised, expired-subscription and unavailable-strategy states, AND SHALL NOT render a stale or fabricated list in place of an error state.

---

### Requirement 13: Paper Trading is a distinct, isolated Execution_Environment

**User Story:** As a trader, I want to test a strategy with real prices and fake money, with
absolute certainty that no real order can leave the platform.

#### Acceptance Criteria

1. THE Execution_Environment SHALL be one of exactly 3 values: `BACKTEST`, `PAPER`, `LIVE`, added as an additive value set so that no existing `mode` or `environment` value is renamed or removed.
2. THE Paper_Simulator SHALL NOT invoke any exchange order-creation, order-cancellation, order-amendment, withdrawal or transfer endpoint, AND SHALL NOT read, request, decrypt or receive any exchange credential from the credential vault.
3. WHEN a Paper_Session emits an order intent, THE Paper_Session SHALL route that order intent to the Paper_Simulator, AND SHALL NOT enqueue, forward or copy that order intent onto the live order path.
4. THE Paper_Accounting_Engine SHALL maintain Paper_Account balances in storage separate from live exchange balances, real portfolio balances, billing balances, owner earnings and platform funds, AND SHALL NOT read or write any of those.
5. THE Marketplace and the Paper_Trading_API SHALL NOT transfer any value between a Paper_Account and any real-money balance in either direction.
6. THE Paper_Trading_API SHALL identify the Execution_Environment of every figure it returns for a Paper_Account or Paper_Session as `PAPER`, AND every surface that reads `api.paper.*`, including the existing Portfolio and Trade History surfaces, SHALL render a simulated indicator within the same displayed region as those figures AND SHALL NOT combine any `PAPER` figure into a `LIVE` balance, position, profit-and-loss or performance total.
7. THE Verification_Suite SHALL assert that a complete Paper_Session performs no call to any exchange trading endpoint and no read of the credential vault, by asserting on the collaborators invoked rather than on log text.
8. THE Paper_Simulator SHALL NOT be the synthetic `exchange_simulator.PaperTradingExchange`, whose prices and fills are drawn from `random`, AND THE platform SHALL reach that module from no Paper_Trading_API request path and no Paper_Session execution path, confining it to internal staging self-tests.
9. IF the live order path receives an order whose originating Execution_Environment is `PAPER`, THEN THE live order path SHALL reject the order before issuing any exchange call, SHALL return an error indicating an Execution_Environment mismatch, SHALL leave every Paper_Account balance and every real-money balance unchanged, and SHALL record the rejection in the Audit_Log.
10. IF an order carries no Execution_Environment value or a value outside the 3 defined values, THEN THE live order path SHALL reject the order before issuing any exchange call, SHALL NOT default that order to `LIVE`, and SHALL return an error indicating an unresolved Execution_Environment.
11. IF a Paper_Session start resolves its Paper_Simulator to `exchange_simulator.PaperTradingExchange`, THEN THE Paper_Session SHALL refuse to start, SHALL report a simulator-misconfiguration condition, and SHALL record the refusal reason in the Audit_Log.

---

### Requirement 14: Paper Trading consumes real market data through the existing pipeline

**User Story:** As a trader, I want my paper session to react to the same prices the live
market shows, delivered by the same feed the platform already trusts.

#### Acceptance Criteria

1. THE Paper_Market_Data source SHALL be selected by the existing `choose_market_data_source` rule in `backend/market_data_latency.py`, honouring its correctness floor before its latency comparison.
2. THE Paper_Session SHALL consume market data through the existing market-data pipeline (`market_data_contract`, `market_data_validation`, the `mds` WebSocket path and its REST fallback), AND SHALL NOT open an independent exchange feed, poller or normalisation path.
3. THE Paper_Session SHALL record, per session, the identity of the selected market-data source and the exchange, symbol and timeframe it is subscribed to.
4. IF the selected market-data source is unmeasured or fails the correctness floor, THEN THE Paper_Session SHALL refuse to start and SHALL report a market-data-unavailable condition, rather than starting on an unvalidated feed.
5. IF the market-data connection for a running Paper_Session drops, THEN THE Paper_Session SHALL attempt reconnection with bounded backoff, SHALL mark the session's feed state as degraded while disconnected, SHALL NOT fill any order at a price observed before the disconnection as though it were current, and SHALL resume from the first validated event after reconnection.
6. WHERE the WebSocket market-data path is unavailable and the REST path is available, THE Paper_Session SHALL fall back to the REST path and SHALL record the fallback in the session's feed state.
7. THE Paper_Session SHALL discard a market-data event whose event identity has already been processed for that session, and SHALL process events for one symbol in non-decreasing timestamp order.
8. THE Paper_Session SHALL refuse to start against a market-data connection served by the `DEV_MODE` mock interface in `connection_engine._apply_mock_interface`, AND SHALL record the refusal reason.
9. THE Paper_Session SHALL NOT synthesise, interpolate, extrapolate or randomise a price, a bid, an ask or a volume, AND SHALL use bid and ask values only where the selected source supplies them.
10. THE Paper_Session SHALL measure and expose market-data delivery latency and feed health for the session using the existing latency measurement facilities.

---

### Requirement 15: The simulator choice is an explicit, justified constraint

**User Story:** As a platform architect, I want the paper-execution mechanism chosen for
stated reasons that hold for every exchange we support, not for whichever exchange was
tested first.

#### Acceptance Criteria

1. THE Paper_Simulator SHALL be an internal, platform-owned simulator executing against Paper_Market_Data, AND SHALL NOT depend on any exchange sandbox or testnet endpoint for order execution.
2. THE Paper_Simulator SHALL be usable for every exchange the platform's existing exchange registry lists, including those the registry records as having no public sandbox endpoint (`kraken`, `bitfinex`).
3. THE Paper_Simulator SHALL be usable by a user who holds no exchange credentials, AND THE Paper_Session SHALL require no exchange account connection to start.
4. THE Paper_Simulator SHALL produce the same sequence of order states, fills, fees and balances when replayed against the same recorded sequence of market-data events, the same session configuration and the same order intents.
5. THE Paper_Session SHALL record the market-data event sequence sufficient to reproduce its order and accounting history for audit.
6. THE specification's rationale for Criteria 1 through 4 — safety of never touching an exchange trading endpoint, latency independence from a third-party sandbox, determinism, testability, exchange independence, and per-tenant suitability in a multi-tenant service — SHALL be recorded in the design document and SHALL NOT be re-litigated per exchange during implementation.

---

### Requirement 16: The paper order lifecycle is a validated state machine

**User Story:** As a trader, I want every simulated order to move through states that make
sense, and as an operator I want a duplicate event or a race never to corrupt a position.

#### Acceptance Criteria

1. THE Paper_Order_State SHALL be one of exactly 6 values: `CREATED`, `ACCEPTED`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `REJECTED`.
2. THE Paper_Order_State_Machine SHALL permit only the following transitions: `CREATED → ACCEPTED`; `CREATED → REJECTED`; `ACCEPTED → PARTIALLY_FILLED`; `ACCEPTED → FILLED`; `ACCEPTED → CANCELLED`; `ACCEPTED → REJECTED`; `PARTIALLY_FILLED → PARTIALLY_FILLED`; `PARTIALLY_FILLED → FILLED`; `PARTIALLY_FILLED → CANCELLED`.
3. THE Paper_Order_State values `FILLED`, `CANCELLED` and `REJECTED` SHALL be terminal, AND THE Paper_Simulator SHALL reject any transition out of a terminal state.
4. IF a write would set a Paper_Order_State to a value not reachable from its current value under Criterion 2, THEN THE Persistence_Layer SHALL reject the write, SHALL leave the stored state unchanged, and SHALL return an error naming the rejected transition.
5. THE Paper_Simulator SHALL validate every order intent against an explicitly enumerated supported order type set and supported side set recorded for the Paper_Session; IF the order's quantity is less than or equal to zero, exceeds the session's configured maximum order quantity, or carries more decimal places than the symbol's recorded quantity precision, or the symbol is not in the session's validated symbol set, or a carried limit price is less than or equal to zero or carries more decimal places than the symbol's recorded price precision, or the order type is outside the supported order type set, or the side is outside the supported side set, THEN THE Paper_Simulator SHALL set the order to `REJECTED` from `CREATED`, SHALL record the rejection reason naming the failed check, and SHALL make no change to the Paper_Account's balances or positions.
6. IF an order's required funds exceed the Paper_Account's available balance, THEN THE Paper_Simulator SHALL set the order to `REJECTED`, SHALL record an insufficient-funds rejection reason, and SHALL lock no funds; WHERE the order carries a limit price, required funds SHALL be computed from that limit price, and WHERE it carries none, from the latest validated Paper_Market_Data price for the symbol, in both cases plus the fees and slippage of the session's recorded configuration, and evaluated on the balance read inside the transaction of Criterion 10.
7. THE Paper_Simulator SHALL apply the sum of all fills for one order to at most the order's quantity; IF a fill event would raise the cumulative filled quantity above the order quantity, THEN THE Paper_Simulator SHALL reject that fill event, SHALL leave the cumulative filled quantity, the order's state, the position, the balance and the realized profit and loss unchanged, and SHALL return an error indicating an over-fill.
8. WHEN the Paper_Simulator receives an order intent carrying an idempotency key of 1 to 128 characters already recorded for the session with identical order parameters, THE Paper_Simulator SHALL return the previously created order with its current Paper_Order_State, SHALL create no second order, and SHALL make no change to the Paper_Account's balances or positions.
9. WHEN the Paper_Simulator receives a fill event carrying an event identifier already applied to an order, THE Paper_Simulator SHALL make no change to the order, the position, the balance or the realized profit and loss.
10. THE Paper_Simulator SHALL apply each order intent and each fill event to one Paper_Account under a single database transaction with row-level locking or an optimistic-concurrency version check on the affected Paper_Account and position rows; IF the transaction fails or the version check conflicts, THEN THE Paper_Simulator SHALL roll back the whole transaction leaving no partial write, SHALL retry the application at most 3 times, and SHALL return an error indicating a concurrency conflict once the retries are exhausted.
11. THE Persistence_Layer SHALL enforce uniqueness of the session idempotency key per Paper_Session and uniqueness of the fill event identifier per order.
12. THE Paper_Simulator SHALL apply fees and slippage from the session's recorded configuration, which SHALL be captured at session start and SHALL remain unchanged for the session's lifetime, SHALL record on each fill the fee amount and the slippage amount applied at the currency's Minor_Units precision, AND SHALL NOT apply a randomised fill probability, a randomised fee or a randomised slippage.
13. WHEN a fill event is applied to an order and the resulting cumulative filled quantity is less than the order quantity, THE Paper_Simulator SHALL set the order's Paper_Order_State to `PARTIALLY_FILLED`.
14. WHEN a fill event is applied to an order and the resulting cumulative filled quantity equals the order quantity, THE Paper_Simulator SHALL set the order's Paper_Order_State to `FILLED`.
15. IF an order intent carries an idempotency key already recorded for the session with order parameters differing from the recorded intent, THEN THE Paper_Simulator SHALL create no order, SHALL leave the previously created order unchanged, and SHALL return an error indicating a conflicting reuse of the idempotency key.

---

### Requirement 17: Paper sessions and accounts are durable, validated and isolated

**User Story:** As a trader, I want my paper account and session history to survive a
restart and to be mine alone.

#### Acceptance Criteria

1. THE Persistence_Layer SHALL store Paper_Accounts, Paper_Sessions, paper orders, paper fills, paper positions, paper balances, paper trades, paper equity snapshots, paper metrics and paper events in database tables, AND THE Paper_Trading_API SHALL serve them from that storage rather than from process memory.
2. WHEN the backend process restarts, THE Paper_Trading_API SHALL return the same Paper_Account balances, positions, orders and trade history it returned before the restart.
3. WHILE two or more backend instances serve requests concurrently, THE Paper_Trading_API SHALL return the same Paper_Account state from every instance.
4. WHEN a user requests the start of a Paper_Session, THE Paper_Session_Service SHALL complete all of the following validations before creating any Paper_Session, Paper_Account or market-data subscription: ownership of, or an entitling Subscription to, the selected strategy as resolved by the Entitlement_Resolver from the authenticated server-side identity; the strategy's publication or lifecycle status; the initial simulated capital's currency, amount and precision per Criterion 5; the symbol against the session's exchange market metadata; the timeframe against the supported timeframe set; and the strategy's compatibility with the selected symbol and timeframe.
5. IF a requested initial simulated capital is not greater than zero, or exceeds the configured per-session maximum, or carries more decimal places than the currency's Minor_Units precision, THEN THE Paper_Session_Service SHALL reject the start request. THE configured per-session maximum SHALL default to 1,000,000 major currency units expressed in Minor_Units and SHALL be configurable within the range 1 to 1,000,000,000 major currency units.
6. THE Paper_Session_Service SHALL create each Paper_Session with its own isolated order book state, position set and balance set, AND SHALL NOT share any of them with another Paper_Session, such that no create, update or delete applied to one Paper_Session's orders, positions or balances changes any row belonging to another Paper_Session, including two concurrent Paper_Sessions of the same user on the same strategy, symbol and timeframe.
7. THE Paper_Session_Service SHALL maintain each Paper_Session in exactly one of the states `CREATED`, `RUNNING`, `PAUSED`, `STOPPED`, SHALL permit the operations only as follows — start from `CREATED`; pause from `RUNNING`; resume from `PAUSED`; stop from `RUNNING` or `PAUSED`; reset from `STOPPED` — and SHALL record each accepted operation with the requesting user, the resulting state and a timestamp.
8. WHEN a Paper_Session is stopped, THE Paper_Session_Service SHALL persist its final orders, positions, balances, trades, metrics and equity curve, SHALL release its market-data subscription, SHALL close its WebSocket broadcast registration, SHALL report the stop as complete only after those steps have committed, and SHALL keep the persisted history readable through the Paper_Trading_API after the stop and after a subsequent process restart.
9. THE Paper_Session_Service SHALL execute the session pipeline in the order: validate entitlement, validate strategy status, validate configuration, create the isolated session, connect the authorised feed, deliver market data to strategy execution, generate signals, convert signals to simulated order intents, execute through the Paper_Simulator, update positions, update balances, compute profit and loss, compute drawdown, persist events, broadcast events.
10. THE Paper_Session_Service SHALL reuse the existing DAG execution runtime and signal-generation path to produce signals, AND SHALL NOT implement a second strategy evaluation path.
11. THE Persistence_Layer SHALL enforce that every paper order, fill, position, balance, trade, equity snapshot, metric and event row references a Paper_Session and a user, with foreign keys, and SHALL apply row-level security scoping every such row to its owning user.
12. THE Paper_Trading_API SHALL retain the existing `/api/paper/*` endpoint paths and response shapes that `api.paper` already consumes, AND SHALL extend them only additively, meaning no existing path is removed or renamed, no existing response field is removed, renamed, retyped or given a changed meaning, and every newly added response field is optional for existing consumers.
13. IF any validation listed in Criterion 4 or Criterion 5 fails, THEN THE Paper_Session_Service SHALL reject the start request, SHALL create no Paper_Session, no paper order, no paper balance and no market-data subscription, SHALL leave every existing Paper_Account unchanged, and SHALL return an error indicating which validation failed.
14. IF an operation named in Criterion 7 is requested from a state that does not permit it, THEN THE Paper_Session_Service SHALL reject the operation, SHALL leave the Paper_Session's state, orders, positions, balances and persisted history unchanged, and SHALL return an error indicating the current state and the rejected operation.
15. WHEN a Paper_Session is reset, THE Paper_Session_Service SHALL set its balances to the recorded initial simulated capital, SHALL leave no open paper order and no open paper position, SHALL begin a new equity series for the session, AND SHALL keep the pre-reset orders, fills, trades, metrics and equity snapshots readable through the Paper_Trading_API.

---

### Requirement 18: Paper accounting invariants hold at every observable point

**User Story:** As a trader, I want the numbers in my paper session to add up, so that the performance I read is the performance the simulator produced.

#### Acceptance Criteria

1. THE Paper_Accounting_Engine SHALL perform every monetary and quantity computation in exact decimal arithmetic, AND SHALL NOT perform any such computation in binary floating point.
2. THE Paper_Accounting_Engine SHALL quantize every persisted and reported monetary value to the Minor_Units precision of the Paper_Account's currency, and every persisted quantity to the symbol's quantity precision from the session's exchange market metadata, using one rounding mode recorded in the session configuration and applied to every such computation.
3. THE Paper_Accounting_Engine SHALL maintain `total_equity = available_balance + locked_balance + position_market_value` as exact decimal equality with zero tolerance at every point at which any of the four values is readable through the Paper_Trading_API, the Paper_Channel or the Persistence_Layer, where `position_market_value` is the sum over the session's open positions of position size multiplied by the latest validated Paper_Market_Data price for that position's symbol, preserving the invariant the existing paper trading service already asserts.
4. THE Paper_Accounting_Engine SHALL keep `available_balance >= 0` and `locked_balance >= 0` at every point at which either value is readable through the Paper_Trading_API, the Paper_Channel or the Persistence_Layer.
5. THE Paper_Accounting_Engine SHALL keep each position's size at or above zero, SHALL represent direction by an explicit side value of exactly one of `LONG` or `SHORT` rather than by a negative size, and SHALL set a fully closed position's size to exactly zero.
6. WHEN an order is fully filled with no fees and no slippage, THE Paper_Accounting_Engine SHALL change `total_equity` by exactly zero, evaluated with each affected position valued at that fill's price at the moment the fill is applied, so that cash and position value are conserved across the fill.
7. WHEN fees are applied, THE Paper_Accounting_Engine SHALL reduce `total_equity` by exactly the sum of the fees recorded on the fills.
8. THE Paper_Accounting_Engine SHALL compute realized profit and loss only from closed quantity at recorded fill prices, applying one cost-basis convention recorded in the session configuration and applied identically to every position in the session, and SHALL compute unrealized profit and loss only from open quantity at the latest validated Paper_Market_Data price for the position's symbol, recording the timestamp of the price used.
9. THE Paper_Accounting_Engine SHALL compute maximum drawdown from the session's persisted equity snapshots taken in non-decreasing timestamp order as the largest decline from a running peak equity to a subsequent trough, SHALL report it both as an exact decimal amount at or above zero and as a fraction of that peak between 0 and 1 inclusive, and SHALL report zero while the session holds fewer than two persisted equity snapshots.
10. THE Paper_Accounting_Engine SHALL treat a trade as closed when its position quantity reaches zero, SHALL compute win rate as the count of closed trades whose realized profit is strictly greater than zero divided by the total count of closed trades, expressed as a value between 0 and 1 inclusive, and SHALL report the win rate as absent rather than as zero while the closed-trade count is zero.
11. THE Paper_Accounting_Engine SHALL persist an equity snapshot recording `total_equity` and the snapshot timestamp on each of: session start, each applied fill, each applied fee, each position revaluation from a validated Paper_Market_Data price, and session stop; AND THE Paper_Trading_UI's equity curve SHALL be drawn from those persisted snapshots rather than from live event state.
12. WHERE margin is supported for a session's market type, THE Paper_Accounting_Engine SHALL compute and record margin usage; WHERE it is not supported, THE Paper_Accounting_Engine SHALL report no margin figure rather than a zero presented as a measurement.
13. THE Paper_Accounting_Engine SHALL produce the same final balances, positions, realized profit and loss and equity series, compared as exact decimal equality, when the same fill sequence is applied twice with the same event identifiers, and SHALL persist no additional equity snapshot for a repeated event identifier.
14. IF applying an order intent, a fill, a fee or a revaluation would violate Criterion 3, Criterion 4 or Criterion 5, THEN THE Paper_Accounting_Engine SHALL reject the operation within the same database transaction, SHALL roll back so that the Paper_Account's stored balances, positions, realized profit and loss and equity series remain unchanged, and SHALL return an error naming the violated invariant.
15. IF no validated Paper_Market_Data price exists for an open position's symbol, or the session's feed state is degraded, THEN THE Paper_Accounting_Engine SHALL mark `position_market_value`, unrealized profit and loss and `total_equity` as stale, SHALL record the timestamp of the last validated price used, AND SHALL NOT substitute a synthesised, interpolated or zero price.

---

### Requirement 19: Paper Trading WebSocket events are typed, versioned, authorised and tenant-isolated

**User Story:** As a trader watching a paper session, I want live updates that are complete, ordered and mine alone.

#### Acceptance Criteria

1. THE Paper_Channel SHALL be implemented as a parameterised, ownership-authorised channel family following the existing `OwnedChannelFamily` pattern in `backend/ws_channels.py`, keyed by Paper_Session identifier, AND SHALL be registered in the existing WebSocket infrastructure rather than in a new server.
2. THE Paper_Channel SHALL emit exactly the following event types: `paper_session_started`, `paper_session_paused`, `paper_session_resumed`, `paper_session_stopped`, `market_tick`, `signal_generated`, `paper_order_created`, `paper_order_accepted`, `paper_order_partially_filled`, `paper_order_filled`, `paper_order_rejected`, `paper_position_updated`, `paper_balance_updated`, `paper_pnl_updated`, `paper_drawdown_updated`, `paper_error`.
3. THE Paper_Channel SHALL include in every event a schema version, the Paper_Session identifier, a per-session sequence number that is 1 for the first event of that Paper_Session and increases by exactly 1 for each subsequent event of that Paper_Session, an event identifier unique within the session, and an emission timestamp in UTC with millisecond or finer resolution.
4. WHEN a client subscribes to a Paper_Channel for a Paper_Session that it does not own or that does not exist, THE Paper_Channel SHALL refuse the subscription within 1 second, SHALL return an identical refusal indication in both cases so that the response does not reveal whether that Paper_Session exists, and SHALL NOT register the connection in any Paper_Channel registry.
5. THE Paper_Channel SHALL authenticate every subscription against the existing WebSocket authentication path, AND SHALL re-verify session ownership at subscription time rather than trusting an identifier supplied in the subscribe message.
6. THE Paper_Channel SHALL deliver a Paper_Session's events only to connections owned by that session's owner.
7. THE Paper_Channel SHALL include no Protected_Logic in any event payload, and SHALL include no credential, token or payment reference.
8. WHEN a client reconnects to a Paper_Channel and supplies a last-received sequence number, THE Paper_Channel SHALL replay in ascending sequence order every retained event of that Paper_Session whose sequence number is greater than the supplied value, from a per-session buffer that retains at least the most recent 1000 events for at least 5 minutes after emission, AND THE Paper_Trading_UI SHALL discard an event whose event identifier it has already applied.
9. IF a client supplies a last-received sequence number that is lower than the lowest sequence number still retained for that Paper_Session, THEN THE Paper_Channel SHALL NOT replay a partial history, SHALL emit a `paper_error` event indicating that event history is incomplete and that the client must reload session state from the Paper_Trading_API, and SHALL continue delivering newly emitted events from the current sequence number.
10. THE Paper_Channel SHALL send heartbeats on the existing heartbeat mechanism, SHALL classify a connection as stale when the client has failed to respond to 2 consecutive heartbeats, SHALL close a stale connection within 5 seconds of that classification, and SHALL remove a closed connection's subscriptions from every registry it was added to.
11. IF the pending outbound event queue for a subscribed connection exceeds 1000 events, THEN THE Paper_Channel SHALL close that connection with an indication that the client fell behind and must reconnect and resume, SHALL remove its subscriptions from every registry, and SHALL continue delivering events to all other connections without dropping or reordering their events.
12. WHEN a Paper_Session stops or is deleted, THE Paper_Channel SHALL release every subscription for that session.
13. IF an event handler raises, THEN THE Paper_Channel SHALL log the failure with the session identifier and the event type, SHALL continue serving other events and other connections, AND SHALL NOT leave the connection registered without a handler.

---

### Requirement 20: The Paper Trading interface reuses the existing frontend architecture

**User Story:** As a trader, I want a paper trading screen that looks and behaves like the
rest of the product and tells me the truth about its own state.

#### Acceptance Criteria

1. THE Paper_Trading_UI SHALL be reachable from the existing authenticated route structure and the existing sidebar navigation, and SHALL use the existing design system, notification mechanism, authentication hooks, API client modules and state management.
2. THE Paper_Trading_UI SHALL call the backend through a module in `algo22-terminal/src/api/modules/`, AND SHALL NOT construct its own HTTP client, its own base URL, or a hard-coded host.
3. THE Paper_Trading_UI SHALL present controls for strategy selection, initial simulated capital, symbol, timeframe, and the session operations start, pause, resume, stop and reset.
4. THE Paper_Trading_UI SHALL display: market-data status, session status, current price, equity, cash, unrealized profit and loss, realized profit and loss, total return, maximum drawdown, win rate, trade count, open positions, open orders, completed trades, the equity curve, the profit-and-loss chart, the drawdown chart, trade markers on the price series, the signal stream, execution events, and feed latency and health.
5. THE Paper_Trading_UI SHALL render explicit loading, empty, error-with-retry, disabled, unauthorised, expired-subscription, unavailable-strategy and feed-disconnected states, and SHALL indicate reconnection while it is in progress.
6. THE Paper_Trading_UI SHALL label every figure it displays as simulated.
7. THE Paper_Trading_UI SHALL remain usable at viewport widths from 360 pixels to 1920 pixels without horizontal overflow of its primary content.
8. THE Paper_Trading_UI SHALL remove every WebSocket subscription, timer and event listener it created when its route unmounts.
9. THE Marketplace_UI SHALL provide a catalogue landing view, strategy cards, a Listing detail view, a backtest performance section, a risk metrics section, a pricing section, a subscribe action, a subscription-status indication, an eligibility-state indication for an owner's own Listing, and the administrative status where the caller is an Admin_Reviewer.
10. THE Marketplace_UI SHALL call the backend through a module in `algo22-terminal/src/api/modules/` rather than through direct client calls scattered across the page, and SHALL report outcomes through the existing notification mechanism rather than through `alert()`.

---

### Requirement 21: Tenant isolation is enforced and adversarially tested

**User Story:** As a user of a multi-tenant platform, I want another tenant's identifiers to be useless to me.

#### Acceptance Criteria

1. THE Marketplace_API and THE Paper_Trading_API SHALL derive the acting identity and tenant from the authenticated server-side session for every operation, AND SHALL ignore any user, tenant, owner, subscriber or session identity supplied in a request body, query parameter, path parameter or WebSocket message for authorisation purposes.
2. THE Persistence_Layer SHALL apply row-level security to every table introduced by this specification, scoping each row to its owning user, with a service-role policy for backend-initiated writes, following the pattern already applied to `library_subscriptions` and `deployment_permissions`.
3. THE Persistence_Layer SHALL default to denying access, such that a query executed against any table introduced by this specification without a resolved authenticated identity or the service role returns zero rows and performs zero writes.
4. IF a caller references a Submission, Listing-private record, Subscription, Settlement_Record, Paper_Account, Paper_Session, paper order, paper position or paper event whose owning user is not the authenticated identity, THEN THE responding component SHALL refuse the operation, SHALL return a response whose status and body are byte-identical in shape and content to the response returned for an identifier that exists in no tenant, SHALL leave the referenced record and all of the caller's own records unchanged, AND SHALL record the attempt in the Audit_Log with the acting identity, the referenced record type and the referenced identifier.
5. THE Marketplace_API and THE Paper_Trading_API SHALL scope every list query by the authenticated identity in the query itself, AND SHALL NOT filter another tenant's rows out after retrieval.
6. IF a request to THE Marketplace_API or THE Paper_Trading_API, or a WebSocket subscription attempt on THE Paper_Channel, carries no valid authenticated server-side session, THEN THE receiving component SHALL refuse the operation before any data access, SHALL perform zero writes, AND SHALL return or emit a response indicating that authentication is required.
7. WHILE a Paper_Channel subscription is open, THE Paper_Trading_API SHALL re-derive the owning user of the subscribed Paper_Session from the Persistence_Layer before emitting each event, AND IF that owning user is not the authenticated identity of the subscription, THEN THE Paper_Trading_API SHALL emit no further events on that subscription and SHALL close it.
8. THE Verification_Suite SHALL attempt, for every endpoint and every WebSocket channel introduced or modified by this specification, an access using a second account's identifiers for each of: strategy, submission, listing-private record, subscription, settlement record, paper account, paper session, paper order, paper position and user, SHALL assert for each attempt that the response matches the non-existent-record response defined in Criterion 4, that no row in any table introduced by this specification changed, and that no field value belonging to the second account appears in the response, AND SHALL report an overall failure if any single attempt fails any of those three assertions or if any endpoint or channel introduced or modified by this specification is not covered by at least one attempt.
9. THE Marketplace_API SHALL refuse, using the response defined in Criterion 4, any attempt by an owner to read, modify, withdraw, resubmit or delete another owner's Submission, and any attempt by a purchaser to read, renew, cancel or refund another purchaser's Subscription, AND SHALL leave the targeted Submission, Subscription and any associated Settlement_Record in their prior state.

---

### Requirement 22: Every new API and WebSocket operation passes an explicit security review

**User Story:** As a platform operator, I want each new surface to have been checked against
the specific attack classes that matter for a paid, multi-tenant trading product.

#### Acceptance Criteria

1. THE Marketplace_API and THE Paper_Trading_API SHALL require authentication on every non-catalogue endpoint, and SHALL apply the platform's existing authorisation dependencies rather than inline role checks.
2. THE Marketplace_API and THE Paper_Trading_API SHALL validate every request body and parameter against a declared schema, including type, range, length, enumeration membership and identifier format, before any database access.
3. THE Marketplace_API and THE Paper_Trading_API SHALL parameterise every database query and SHALL NOT interpolate a caller-supplied value into query text.
4. THE Marketplace_API and THE Paper_Trading_API SHALL apply rate limiting to every endpoint, with a stricter limit on publication, price evaluation, checkout creation, renewal, session start and order placement than on read endpoints.
5. THE Marketplace SHALL treat a repeated payment webhook delivery, a repeated checkout confirmation and a repeated order submission as idempotent, keyed on the provider transaction reference or the caller-supplied idempotency key.
6. THE Marketplace SHALL reject a webhook whose signature, source address or timestamp fails the Billing_Integration's existing validation, and SHALL NOT process a webhook whose timestamp lies outside the accepted window.
7. THE Marketplace_API SHALL NOT fetch a URL supplied by a caller, and WHERE a Listing carries a cover image reference, THE Marketplace_API SHALL validate that reference against an allow-list of permitted hosts or storage prefixes.
8. THE Marketplace_UI and THE Paper_Trading_UI SHALL render owner-supplied text (Listing name, description, tags, review text, rejection reason) as text and SHALL NOT interpret it as markup or script.
9. THE Marketplace_API and THE Paper_Trading_API SHALL return structured error responses carrying a stable code and a safe message, AND SHALL NOT return a stack trace, a database error string, a query, an internal path or an internal identifier to a client.
10. THE Marketplace_API and THE Paper_Trading_API SHALL NOT permit a non-Admin_Reviewer to reach any review operation, and SHALL NOT accept a privilege claim from client-controlled metadata.
11. THE Verification_Suite SHALL record a security review for every endpoint and WebSocket event introduced or modified by this specification, covering authentication, authorisation, insecure direct object reference, broken object-level authorisation, injection, cross-site scripting, cross-site request forgery, server-side request forgery, rate limiting, replay, duplicate payment callback, duplicate order execution, race conditions, privilege escalation, administrative authorisation, secret exposure, Protected_Logic leakage, tenant leakage, WebSocket authorisation and sensitive logging.

---

### Requirement 23: Signal Trace distinguishes the three environments and stays safe for subscribers

**User Story:** As a trader, I want one place that shows what my strategies decided, in
which environment, and what happened next.

#### Acceptance Criteria

1. THE Signal_Trace_Recorder SHALL record an Execution_Environment value on every signal it records, added as an additive column with a check constraint over `BACKTEST`, `PAPER`, `LIVE`.
2. THE Signal_Trace_Recorder SHALL record, per signal: the strategy, the strategy version, the Paper_Session or deployment identifier as applicable, the generation timestamp, the decision, the asset, the side, the quantity, the price, the execution status expressed in the Order_Lifecycle_State vocabulary, the order identifier once assigned, the signal source, the Execution_Environment, and a safe reason where the signal was not executed.
3. WHERE the viewing user is a subscriber rather than the strategy owner, THE Signal_Trace_Recorder's read path SHALL return only the fields listed in Criterion 2 and SHALL exclude node-level trace detail, indicator values, feature values, ML inference detail and risk-rule internals.
4. THE Signal Trace read path SHALL support filtering by Execution_Environment, AND SHALL NOT mix `PAPER` and `LIVE` signals in a total, an aggregate or a chart series without an explicit environment label.
5. THE Signal_Trace_Recorder SHALL record `PAPER` signals through the same recording path as `LIVE` signals, AND SHALL NOT introduce a second signal store.
6. THE Signal Trace read path SHALL default to the caller's own signals across all environments, scoped by the authenticated identity.
7. THE Persistence_Layer SHALL back-fill the Execution_Environment of existing signal rows to `LIVE` and SHALL NOT delete or rewrite any existing signal row's other columns.

---

### Requirement 24: Database schema integrity and safe migrations

**User Story:** As a platform operator, I want the database to refuse to hold an incoherent
marketplace, subscription or paper-trading state, and I want to apply the change without
risking production data.

#### Acceptance Criteria

1. THE Persistence_Layer SHALL define a foreign key from every row introduced by this specification to the user, Listing, Subscription, Paper_Session or strategy it belongs to, with an explicit delete rule per relationship.
2. THE Persistence_Layer SHALL apply a check constraint enumerating the permitted values of Submission_State, Subscription_State, Paper_Order_State and Execution_Environment.
3. THE Persistence_Layer SHALL apply the uniqueness constraints named in Requirements 2.8, 4.4, 9.7, 16.11 and 21.2.
4. THE Persistence_Layer SHALL apply an index supporting each of: catalogue browse ordering and filtering, Submission lookup by state, Subscription lookup by purchaser, Subscription lookup by Listing, Subscription expiry sweep by expiry timestamp, Settlement_Record lookup by owner, Paper_Session lookup by user, paper order lookup by session and state, and paper event lookup by session and sequence number.
5. THE Persistence_Layer SHALL record creation and update timestamps on every table introduced by this specification.
6. THE Marketplace and THE Paper_Session_Service SHALL commit each multi-row state change described in Requirements 9.3, 9.5, 11.4, 16.10 and 17.8 within a single database transaction, such that a failure leaves no partially applied change.
7. THE migration set SHALL be additive: it SHALL NOT drop or rename an existing column or table, SHALL NOT delete existing rows, and SHALL be expressed so that repeated application produces the same schema.
8. THE migration set SHALL apply successfully from an empty database and from a database at the current production schema revision.
9. THE migration set SHALL create every column that a handler introduced or modified by this specification reads or writes, preventing a repeat of the PostgreSQL 42703 condition recorded in migration 007.
10. THE Verification_Suite SHALL assert Criteria 7, 8 and 9, including a schema-contract assertion of every column name read or written by every handler introduced or modified by this specification against the migration-defined schema.

---

### Requirement 25: Live trading, billing and authentication continue to work unchanged

**User Story:** As an existing user, I want none of this new functionality to disturb what I
already rely on.

#### Acceptance Criteria

1. THE live order path, the exchange credential vault, the live strategy execution runtime, the signal-generation path and the Signal Trace live recording SHALL retain their existing behaviour.
2. THE Billing_Integration's plan checkout, entitlement resolution, invoice retrieval, payment-method management, currency selection, portal, cancel and resume paths SHALL retain their existing behaviour.
3. THE authentication and authorisation paths — registration, sign-in, token handling, two-factor enrolment, administrative role resolution — SHALL retain their existing behaviour.
4. THE existing surfaces authentication, sign-up, sign-in, dashboard, strategy builder, strategies, backtester, signal trace, exchange connection, risk settings, portfolio, trade history, billing, notifications, profile, security logs and support SHALL remain functional after the change, verified against behaviour recorded before the change.
5. THE risk-utilisation computation in `routers/risk.py` that currently reads the in-memory paper account SHALL be repointed at the persisted Paper_Account without changing its reported semantics.
6. THE Portfolio and Trade History surfaces that currently read `api.paper.*` SHALL continue to render, sourcing the same fields from the persisted Paper_Account.
7. THE Verification_Suite SHALL assert Criteria 1 through 6 against behaviour recorded from the unmodified system before the change, for each named path and surface individually.
8. THE existing test suites `tests/test_marketplace_pipeline.py`, `tests/test_marketplace_concurrency.py`, `tests/test_library_schema_contract.py`, `tests/test_paper_trading_lifecycle.py`, `tests/test_billing_e2e.py`, `tests/test_tenant_isolation_*` and `tests/sandbox_lifecycle/` SHALL pass after the change, updated only where this specification deliberately changes the behaviour they assert, with each such update recorded together with the requirement that mandates it.

---

### Requirement 26: Observability without sensitive disclosure

**User Story:** As an operator diagnosing a marketplace, billing or paper-trading incident,
I want enough structured evidence to find the cause, and no secret in the logs.

#### Acceptance Criteria

1. THE Marketplace_API and THE Paper_Trading_API SHALL attach a request identifier to every request and SHALL include it in every log record and every error response produced for that request.
2. THE Marketplace SHALL emit a structured audit record for: Submission creation, each Submission_State transition, each Admin_Reviewer action, Price_Range evaluation, checkout creation, payment confirmation, Settlement_Record creation, Subscription_State transition, period extension, entitlement grant and entitlement revocation.
3. THE Paper_Session_Service SHALL emit a structured record for each Paper_Session lifecycle operation, each order state transition, each fill, each balance change and each session error, keyed by session identifier and sequence number.
4. THE Marketplace and THE Paper_Session_Service SHALL exclude from every log record: API keys, exchange secrets, decrypted credentials, authentication tokens, payment provider secrets, card data, Protected_Logic, and any other user's identifiers.
5. THE Marketplace and THE Paper_Session_Service SHALL log an error for every failure on a correctness-critical path, AND SHALL NOT swallow an exception on such a path without a log record and a defined outcome.
6. THE Marketplace and THE Paper_Session_Service SHALL emit latency and error-rate metrics for each API endpoint, WebSocket event type, market-data delivery, signal generation, paper order execution and database operation introduced by this specification.

---

### Requirement 27: Performance and resource behaviour

**User Story:** As a trader, I want the catalogue and my paper session to respond quickly
enough to be usable, and as an operator I want the load to be bounded.

#### Acceptance Criteria

1. THE Marketplace_API SHALL serve a catalogue page of at most 50 Listings with a fixed number of database round trips independent of the number of Listings returned.
2. THE Marketplace_API SHALL serve the Strategies_Page's combined owned-and-subscribed list with a fixed number of database round trips independent of the number of entries returned.
3. THE Paper_Session_Service SHALL execute market-data handling, signal generation, order execution and event broadcast without blocking the HTTP event loop, using the platform's existing asynchronous and thread-offload facilities.
4. THE Paper_Session_Service SHALL bound the number of concurrent Paper_Sessions per user by a configured maximum and SHALL refuse a session beyond that maximum with a clear reason.
5. THE Paper_Trading_UI SHALL bound the number of retained in-memory events, ticks and chart points per session, and SHALL discard the oldest beyond that bound rather than growing without limit.
6. THE Implementation SHALL measure and record the observed latency of each API endpoint, WebSocket event type, market-data delivery path, signal generation step, paper order execution step and database operation introduced by this specification, and SHALL record those measurements rather than asserting them without measurement.

---

### Requirement 28: No mock, fabricated or placeholder data in production paths

**User Story:** As a user, I want every number on the screen to be a real measurement.

#### Acceptance Criteria

1. THE Marketplace and THE Paper_Session_Service SHALL contain no mock, stub, fake or sample data on any production code path; such constructs SHALL exist only in test code.
2. THE Marketplace SHALL NOT present a Listing performance figure, subscriber count, rating, earnings figure or validation status that was not read from persisted data.
3. THE Paper_Session_Service SHALL NOT present a price, fill, position, balance, profit-and-loss figure or metric that the Paper_Simulator did not produce from validated market data.
4. THE Marketplace and THE Paper_Session_Service SHALL NOT present a simulated execution as a live execution, and SHALL NOT present a `PAPER` figure without its simulated label.
5. IF a value required by a response is unavailable, THEN THE responding component SHALL omit the value or report it as unavailable, AND SHALL NOT substitute a zero, a default or a previous value presented as current.
6. THE Implementation SHALL contain no commented-out implementation, no unreferenced dead code path, no hard-coded user or tenant identifier, no hard-coded credential, and no `localhost` endpoint on a production code path.
7. THE Implementation SHALL NOT mark a requirement satisfied by a code comment or a task marker in place of working code.

---

### Requirement 29: Verification, testing and end-to-end validation

**User Story:** As a platform operator, I want evidence that this works end to end before it
reaches users.

#### Acceptance Criteria

1. THE Verification_Suite SHALL cover, for the backend: unit behaviour, API contracts, database constraints, migration application, authorisation, tenant isolation, concurrency, financial arithmetic, subscription lifecycle, marketplace eligibility and evidence validation, paper trading execution and accounting, WebSocket behaviour, and strategy execution.
2. THE Verification_Suite SHALL cover, for the frontend: component rendering and state transitions, route behaviour, API interaction, WebSocket interaction, and the loading, empty, error, disabled, unauthorised, expired and disconnected states named in Requirements 12.8 and 20.5.
3. THE Verification_Suite SHALL include property-based tests, using the pinned `hypothesis` dependency already present, for each correctness property listed in the "Correctness properties" section of this document.
4. THE Verification_Suite SHALL include one end-to-end journey covering, in order: register and sign in; create and save a strategy; run the backtests required by Requirement 3; verify three distinct Backtest_Conditions; submit a Listing; administrative review; approval and publication; public catalogue visibility; a second account viewing the Listing; subscribing; verifying the 90/10 split in the Settlement_Ledger; the Subscription becoming `ACTIVE`; the strategy appearing on the subscriber's Strategies_Page as `SUBSCRIBED`; confirming Protected_Logic is unreachable by the subscriber; starting a Paper_Session; receiving real market data; generating signals; producing simulated orders and fills; updating positions, profit and loss and drawdown; rendering the charts; stopping the session; confirming persistence; confirming the Signal Trace records with Execution_Environment `PAPER`; expiring the Subscription; confirming access becomes non-entitling; and confirming a renewal payment restores access.
5. THE Verification_Suite SHALL, in a browser against the deployed application, record zero uncaught exceptions, zero unhandled promise rejections, zero application error dialogs, zero unexpected console warnings, zero failed API requests, zero unexpected 4xx or 5xx responses, zero mixed-content requests, zero cross-origin errors, zero WebSocket errors and zero requests to a `localhost` address, on the Marketplace, Listing detail, Strategies, Paper Trading, Signal Trace and Billing surfaces.
6. THE Verification_Suite SHALL cover, before the change is accepted in production: local validation, the full test suite, static analysis, security scanning, frontend build, backend build, container image build and validation, backend deployment, container rollout with target health and load-balancer health checks, health endpoint responses, frontend deployment, CDN invalidation and propagation, authenticated production API checks, the end-to-end browser journey, console, network and WebSocket inspection, backend log inspection, database error inspection, and the regression checks in Requirement 25.
7. IF a deployment fails, THEN THE Operator SHALL diagnose the cause before redeploying, examining the container service events, the task stopped reason, the exit code, the application logs, the task definition, the environment variables, the execution and task roles, the security groups, the target group and health checks, the startup command, the migration status, the database connectivity, the secret resolution, the image reference, the architecture compatibility, the resource limits and the dependency startup order, including for a rollback.
8. THE continuous integration configuration SHALL verify backend tests, linting, formatting, import and dependency audits, security scanning, container build, migration validity and backend startup, AND SHALL additionally verify the frontend build and frontend unit tests for changes under `algo22-terminal/`.
9. THE continuous integration configuration SHALL NOT be weakened to accommodate this change: no existing check SHALL be removed, no existing check SHALL be made non-blocking, and no test SHALL be skipped, marked expected-to-fail or excluded by selector in order to make the pipeline pass.
10. THE Implementation SHALL remove every temporary artifact it creates during verification.

---

### Requirement 30: Code quality constraints

**User Story:** As an engineer who will maintain this after it ships, I want the result to be
readable, typed and testable.

#### Acceptance Criteria

1. THE Implementation SHALL declare types for every function parameter, return value and persisted schema it introduces, in both the Python and the JavaScript or TypeScript surfaces, consistent with the conventions already used in the files it modifies.
2. THE Implementation SHALL place each responsibility in one module, AND SHALL NOT duplicate an existing service, model, validation rule or API client.
3. THE Implementation SHALL make every state-changing operation it introduces idempotent under retry, keyed on a stable identifier.
4. THE Implementation SHALL pass the repository's existing formatting, import-ordering and lint gates as configured in `.github/workflows/01-pr-check.yml`.
5. THE Implementation SHALL NOT catch a broad exception on a correctness-critical path without re-raising or returning a defined error outcome.
6. THE Implementation SHALL keep each function it introduces to a single responsibility expressible in its name, and SHALL extract a helper rather than extending a function past the point where its purpose requires a conjunction to state.

---

## Correctness properties for property-based testing

Each property below is stated as a universally quantified invariant over generated inputs,
with the requirement it verifies and the property class it belongs to (invariant,
round-trip, idempotence, metamorphic, model-based, confluence, error-condition). These are
the properties Requirement 29.3 mandates. Every one of them exercises platform logic over
in-process or in-transaction state, so none of them requires an external service call.

### Revenue split and ledger consistency (Requirement 10)

- **P-1 (invariant, conservation).** For all integer payment amounts `a` in Minor_Units with `a >= 0`: `owner_share(a) + platform_fee(a) == a`.
- **P-2 (invariant, bounds).** For all `a >= 0`: `0 <= owner_share(a) <= a` and `0 <= platform_fee(a) <= a`.
- **P-3 (invariant, ratio bound).** For all `a >= 0`: `owner_share(a) * 100 <= a * 90` and `(owner_share(a) + 1) * 100 > a * 90`, so the owner receives the largest whole Minor_Unit amount not exceeding 90 percent and the rounding remainder never exceeds one Minor_Unit.
- **P-4 (metamorphic, monotonicity).** For all `a1 <= a2`: `owner_share(a1) <= owner_share(a2)` and `platform_fee(a1) <= platform_fee(a2)`.
- **P-5 (invariant, ledger sum).** For all generated sequences of confirmed payments for one owner: the reported owner earnings total equals the exact integer sum of `owner_share` over the persisted Settlement_Records, and the reported platform total equals the exact integer sum of `platform_fee`.
- **P-6 (idempotence, duplicate webhook).** For all generated payment confirmations and all repetition counts `n >= 1`: applying the same provider transaction reference `n` times yields exactly one Settlement_Record and the same Subscription period expiry as applying it once.
- **P-7 (error-condition).** For all negative amounts, non-integer amounts and amounts exceeding the configured maximum: the split computation refuses the input and writes no Settlement_Record.

### Subscription state machine and expiry boundaries (Requirement 11)

- **P-8 (invariant, reachability).** For all generated sequences of Subscription operations: every persisted Subscription_State is reachable from `PENDING` by the transitions of Requirement 11.2, and no persisted transition is outside that set.
- **P-9 (invariant, illegal transition rejection).** For all pairs of Subscription_States `(s1, s2)` not present in the permitted transition set: attempting `s1 → s2` leaves the stored state at `s1` and returns an error.
- **P-10 (invariant, active period well-formedness).** For all Subscriptions in state `ACTIVE`: the period start is non-null, the expiry is non-null, and `expiry > start`.
- **P-11 (metamorphic, expiry boundary).** For all Subscriptions with expiry `e` and all evaluation instants `t`: the Entitlement_Resolver returns entitling for `t < e` and non-entitling for `t >= e`, independent of whether the expiry sweep has run.
- **P-12 (round-trip, calendar month).** For all UTC instants `t` and the period function `m`: `m(t)` falls in the calendar month following the month of `t`, has the same clock time as `t`, and has day-of-month equal to `min(day_of_month(t), last_day(target_month))`.
- **P-13 (idempotence, sweep).** For all generated sets of Subscriptions and all repetition counts `n >= 1`: running the expiry sweep `n` times produces the same set of Subscription_States as running it once.
- **P-14 (metamorphic, renewal monotonicity).** For all Subscriptions and all confirmed renewal payments: the new expiry is strictly greater than the previous expiry, and is one calendar month after the later of the previous expiry and the confirmation instant.
- **P-15 (invariant, history preservation).** For all generated sequences of cancel, expire, refund and renew operations: the count of persisted Subscription rows never decreases, and no Settlement_Record is removed.
- **P-16 (invariant, access agreement).** For all Subscriptions: the deployment and Paper_Session admission decision equals the Entitlement_Resolver's decision for the same Subscription at the same instant.

### Paper order lifecycle (Requirement 16)

- **P-17 (invariant, reachability).** For all generated sequences of order intents, market events and cancellations: every persisted Paper_Order_State is reachable from `CREATED` by the transitions of Requirement 16.2.
- **P-18 (invariant, terminality).** For all orders reaching `FILLED`, `CANCELLED` or `REJECTED`: no subsequent event changes the order's state, its filled quantity or its recorded fees.
- **P-19 (invariant, illegal transition rejection).** For all pairs of Paper_Order_States not present in the permitted transition set: the attempted transition is rejected and the stored state is unchanged.
- **P-20 (invariant, fill accumulation).** For all orders and all generated fill sequences: the sum of fill quantities is at most the order quantity, and the state is `FILLED` if and only if that sum equals the order quantity.
- **P-21 (idempotence, duplicate fill).** For all orders and all fill events `f` and repetition counts `n >= 1`: applying `f` with the same event identifier `n` times produces the same order state, filled quantity, position and balance as applying it once.
- **P-22 (idempotence, duplicate order intent).** For all order intents carrying the same idempotency key within one Paper_Session: exactly one order exists, and every response returns that order.
- **P-23 (confluence, concurrent submission).** For all sets of concurrently submitted distinct order intents against one Paper_Account: the resulting balances, positions and equity are identical to those produced by applying the same intents in some sequential order, and no intent is applied twice or lost.
- **P-24 (error-condition).** For all order intents with quantity at or below zero, a symbol outside the session's validated set, a limit price at or below zero, an unsupported order type, an unsupported side, or required funds exceeding the available balance: the order is `REJECTED` with a recorded reason and no balance, position or equity value changes.

### Paper accounting invariants (Requirement 18)

- **P-25 (invariant, equity identity).** For all generated event sequences applied to a Paper_Account: `total_equity == available_balance + locked_balance + position_market_value` after every event.
- **P-26 (invariant, non-negativity).** For all generated event sequences: `available_balance >= 0`, `locked_balance >= 0`, and every position size `>= 0` after every event.
- **P-27 (invariant, cash conservation on zero-cost fills).** For all fills with zero fee and zero slippage: `total_equity` is unchanged across the fill.
- **P-28 (invariant, fee accounting).** For all generated fill sequences: the decrease in `total_equity` attributable to costs equals the exact sum of the recorded fees, with no residual.
- **P-29 (metamorphic, drawdown bounds).** For all generated equity series: reported maximum drawdown is at or above zero, is at most the series maximum minus the series minimum, and is unchanged by appending an equity value at or above the running peak.
- **P-30 (invariant, win rate range).** For all generated closed-trade sets with at least one closed trade: the reported win rate lies in the closed interval from 0 to 1 and equals the winning count divided by the closed count; for an empty closed-trade set, no win rate is reported.
- **P-31 (model-based, replay agreement).** For all generated market-event and order-intent sequences: the Paper_Simulator's final balances, positions, realized profit and loss, equity series and order states equal those produced by a straightforward reference implementation of the same rules applied to the same inputs.
- **P-32 (round-trip, persistence).** For all generated Paper_Session states: persisting the session state and reading it back yields balances, positions, orders, fills, trades and equity snapshots equal to the values persisted, with no precision loss.

### Backtest condition distinctness (Requirement 3)

- **P-33 (invariant, symmetry).** For all pairs of Backtest_Conditions `(c1, c2)`: `distinct(c1, c2) == distinct(c2, c1)`.
- **P-34 (invariant, irreflexivity).** For all Backtest_Conditions `c`: `distinct(c, c)` is false.
- **P-35 (metamorphic, overlap threshold).** For all pairs with equal `dataset` values: `distinct` is true if and only if the calendar-day overlap of the two windows is at most 25 percent of the shorter window's calendar-day length; and shrinking the overlap of a distinct pair keeps it distinct.
- **P-36 (invariant, dataset difference sufficiency).** For all pairs with different `dataset` values: `distinct` is true regardless of window overlap.
- **P-37 (invariant, set admission).** For all generated Backtest_Evidence sets: the set is admitted if and only if it contains at least 3 conditions that are pairwise distinct, share one `version_id`, have pairwise different `dataset_checksum` values, and each satisfy the completeness, duration, trade-count and bar-count criteria of Requirement 3.
- **P-38 (idempotence, revalidation).** For all admitted and rejected Backtest_Evidence sets and all repetition counts `n >= 1`: revalidating `n` times produces the same outcome and the same per-criterion outcomes.
- **P-39 (metamorphic, no substitution).** For all Backtest_Evidence sets with a missing metric on any condition: the set is rejected, and no displayed or stored Listing metric takes a value absent from the evidence.
- **P-40 (error-condition, ownership).** For all Backtest_Condition references owned by a user other than the strategy owner: the set is rejected, and the rejection is indistinguishable from a reference to a non-existent row.

### Tenant isolation (Requirement 21)

- **P-41 (invariant, cross-tenant read).** For all pairs of distinct users `(u1, u2)`, all endpoints introduced or modified by this specification, and all resource identifiers owned by `u2`: a request authenticated as `u1` returns no field of `u2`'s resource.
- **P-42 (invariant, cross-tenant write).** For the same quantification: a request authenticated as `u1` leaves every row owned by `u2` byte-identical.
- **P-43 (invariant, indistinguishability).** For all resource identifiers: the response to a request for another user's existing resource is indistinguishable, in status code and body, from the response to a request for a non-existent resource of the same kind.
- **P-44 (invariant, WebSocket isolation).** For all Paper_Sessions owned by `u2` and all subscription attempts authenticated as `u1`: the subscription is refused and no event for that session is delivered to `u1`.
- **P-45 (invariant, identity source).** For all requests carrying a user, tenant, owner, subscriber or session identity in the body, query, path or WebSocket message: the authorisation decision is identical to the decision for the same request with that field absent.
- **P-46 (invariant, list scoping).** For all generated multi-tenant datasets and all list endpoints: every returned row's owner equals the authenticated identity.

### Protected logic containment (Requirements 6, 7)

- **P-47 (invariant, projection allow-list).** For all generated Listings backed by strategies containing DAG nodes, indicator parameters, risk configuration and bound ML models: no field of any Marketplace_API response reachable by a non-owner, and no field of any Paper_Channel event, contains any substring of the strategy's Protected_Logic serialisation.
- **P-48 (invariant, clone gating).** For all Listings with source cloning disabled and all non-owner callers: the clone operation is refused and creates no strategy row.

---

## Non-functional constraints (apply to every requirement above)

- No requirement in this document weakens existing authentication, authorisation, row-level
  security, tenant isolation, exchange-credential security, risk-management controls,
  execution guards, webhook validation, entitlement enforcement or idempotency controls.
- Every existing production component named in this document — `routers/library.py`,
  `routers/billing.py`, `routers/paper_trading.py`, `routers/risk.py`,
  `core/entitlement_engine.py`, `core/subscription_dependencies.py`,
  `core/dependencies.get_admin_user`, `core/audit_trail.py`, `core/tenant*`,
  `backend/paper_trading_service.py`, `backend/backtest_service.py`,
  `backend/backtest_runtime.py`, `backend/backtesting_engine.py`,
  `backend/market_data_contract.py`, `backend/market_data_validation.py`,
  `backend/market_data_latency.py`, `backend/model_versioning.py`,
  `backend/signal_trace_engine.py`, `backend/ws_channels.py`, `api_ws/ws_manager.py`,
  `mds/main.py`, `algo22-terminal/src/api/*`, `src/websocketClient.js` — is reused or
  extended in place. None is duplicated or replaced by an independently constructed
  equivalent.
- No production functionality specified here is backed by mock or fabricated data. No
  Listing metric, subscription state, settlement figure, paper price, paper fill or paper
  metric is presented as something it is not.
- Backend validation is authoritative for every workflow in this document. Database
  constraints enforce the invariants stated in Requirement 24.
- Every defect discovered while implementing this specification is fixed at its root cause
  and covered by a regression test that fails against the unfixed code.
- `BACKTEST`, `PAPER` and `LIVE` never share a balance, an order, a fill, a session, a
  credential or a profit-and-loss figure.

---

## Traceability

| User specification area | Primary requirement(s) |
|---|---|
| Marketplace publication request and eligibility | 2, 3 |
| Never expose strategy logic in a listing | 6, 7 |
| Three distinct backtest conditions, persisted and validated | 3 |
| Submission lifecycle DRAFT → … → PUBLISHED, REJECTED with reason | 4 |
| Admin verification workflow, server-side authorisation, audit | 5, 22, 26 |
| Public listing safe-metadata projection | 6 |
| Pricing: minimum, recommended, maximum; ML reuse conditions; server enforcement | 8 |
| One-month subscription, existing billing architecture, no second payment system | 9, 11 |
| Exact money arithmetic in minor units | 8.12, 9.2, 10.3, 18.1 |
| 90/10 split, settlement records, ledger consistency | 10 |
| Subscription lifecycle, expiry, renewal, cancellation, history retention | 11 |
| Subscribed strategies on the Strategies page, OWNED vs SUBSCRIBED, permitted actions | 12 |
| Server-side subscribed-strategy execution, no client-supplied identity | 7, 21 |
| Paper trading is real simulation, never a real order | 13 |
| CCXT sandbox vs internal simulator decision and rationale | 15 |
| Real market data through the existing pipeline, WebSocket with REST fallback | 14 |
| Deterministic paper order lifecycle and corruption prevention | 16 |
| Paper session flow, capital selection, isolation, persistence | 17 |
| Positions, balances, equity, profit and loss, drawdown invariants | 18 |
| Paper trading WebSocket event contract | 19 |
| Paper trading and marketplace user interface | 20 |
| Database entities, constraints, indexes, transactions, migrations | 24 |
| Tenant isolation, IDOR and BOLA testing | 21 |
| Security review of every API and WebSocket operation | 22 |
| API conventions, structured errors, rate limiting | 22 |
| Error handling without silent failure | 22.9, 26.5, 28.5 |
| Observability and safe logging | 26 |
| Frontend reuse, states, no duplicate clients, no memory leaks | 20 |
| Marketplace UI, backtest vs live vs paper separation | 6.6, 20.9 |
| Real persisted backtest results, VectorBT verification, reproducibility | 3 |
| Strict BACKTEST / PAPER / LIVE separation | 13, 23 |
| Live trading safety and regression protection | 25 |
| Signal Trace environment discrimination and subscriber-safe projection | 23 |
| Performance measurement, N+1 avoidance, indexes, non-blocking execution | 27 |
| Testing: backend, frontend, property-based, end-to-end journey | 29 |
| Browser verification with a clean console | 29.5 |
| Production verification and deployment-failure diagnosis | 29.6, 29.7 |
| Continuous integration gates, never weakened | 29.8, 29.9 |
| No mocks or fabricated metrics in production | 28 |
| Code quality, typing, modularity, idempotency, concurrency safety | 30 |
| Priority ordering across the eight emphasis areas | Introduction, "Priority order" |
