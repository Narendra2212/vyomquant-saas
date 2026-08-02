# ALGO22 / Aerora Quant Platform — Project Architecture

> **Version:** 2.4.0  
> **Branch:** `feature/strategy-marketplace-v1`  
> **Generated:** 2026-06-22  
> **Audience:** Onboarding engineers, senior reviewers, CTO audit

---

## Table of Contents

1. [Repository Overview](#1-repository-overview)
2. [Full Directory Tree](#2-full-directory-tree)
3. [Backend Services & Engine Inventory](#3-backend-services--engine-inventory)
4. [FastAPI Routers](#4-fastapi-routers)
5. [Database Models (SQLAlchemy)](#5-database-models-sqlalchemy)
6. [Redis Integration](#6-redis-integration)
7. [WebSocket Architecture](#7-websocket-architecture)
8. [Authentication Flow](#8-authentication-flow)
9. [Strategy Builder Architecture](#9-strategy-builder-architecture)
10. [Backtesting Architecture](#10-backtesting-architecture)
11. [Frontend Page Map](#11-frontend-page-map)
12. [Desktop / Tauri Architecture](#12-desktop--tauri-architecture)
13. [Complete API Endpoint Catalogue](#13-complete-api-endpoint-catalogue)
14. [Database Tables](#14-database-tables)
15. [Strategy-Related Schemas](#15-strategy-related-schemas)
16. [Marketplace Functionality](#16-marketplace-functionality)
17. [Infrastructure & Deployment](#17-infrastructure--deployment)
18. [Safety & Kill-Switch System](#18-safety--kill-switch-system)
19. [Cross-Cutting Concerns](#19-cross-cutting-concerns)
20. [Known Gaps & Onboarding Notes](#20-known-gaps--onboarding-notes)

---

## 1. Repository Overview

**Aerora Dynamics** builds the **Algo22** quantitative trading platform — an end-to-end system that lets retail and institutional traders build, backtest, paper-trade, and live-trade algorithmic strategies.

| Property | Value |
|---|---|
| **Product name** | Algo22 |
| **API version** | 2.4.0 |
| **Primary language** | Python 3.11+ (backend), JavaScript/JSX (frontend) |
| **Framework** | FastAPI + Uvicorn (backend), React + Vite (frontend), Tauri 2 (desktop) |
| **Primary database** | Supabase (PostgreSQL with RLS) |
| **Local ORM** | SQLAlchemy + Alembic (for local-fallback tables) |
| **Time-series store** | QuestDB (tick/OHLCV data) |
| **Cache / pubsub** | Redis (session data, rate limits, profile cache, bot state) |
| **Exchange connectivity** | CCXT / CCXT.pro (multi-exchange HTTP + WebSocket) |
| **ML runtime** | XGBoost + scikit-learn + VectorBT |
| **Observability** | Sentry, Prometheus, structured JSON logs |
| **Container** | Docker Compose (multi-service), Railway (cloud deploy) |
| **Desktop shell** | Tauri 2 (Rust), wrapping the React frontend as a native app |

### Mono-Repo Layout (Top Level)

The repository root contains **two full-stack copies** at different stages of maturity, plus extensive operational artefacts:

| Path | Purpose |
|---|---|
| `backend_app/` | **Primary canonical backend** — all active development here |
| `aerora_quant_platform/` | Older monorepo scaffold (backend_api, frontend_app, shared, gpu_workers) |
| `algo22-terminal/` | **Desktop Tauri app** — React frontend + Rust shell |
| `connection_layer/` | Standalone exchange connection micro-service |
| `routers/` | Root-level router stubs (archived / bridge) |
| `api_ws/` | Root-level WS stubs |
| `migrations/` | Root-level Alembic migrations |
| `k8s/` | Kubernetes manifests |
| `terraform/` | IaC definitions |
| `monitoring/` | Prometheus / Grafana configs |
| `nginx/` | Nginx reverse proxy config |
| `scripts/` | Utility & maintenance scripts |
| `docs/` | Additional documentation |

> ⚠️ **Onboarding note**: The `aerora_quant_platform/` tree is a parallel codebase. Most new development should target `backend_app/`. The `aerora_quant_platform/backend_api/` directory mirrors the same structure — verify which tree a ticket targets before starting.

---

## 2. Full Directory Tree

```
d:\aerora_quant_backend_updated_final1\
│
├── backend_app/                        ← CANONICAL BACKEND
│   ├── main.py                         ← FastAPI app entry point (v2.4.0)
│   ├── requirements.txt
│   ├── .env
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   │       ├── 4ef23035a692_baseline.py
│   │       └── d97ffff9c3bb_execution_schema_rebuild.py
│   ├── api/
│   │   └── main.py                     ← Minimal API bootstrap shim
│   ├── api_ws/
│   │   ├── __init__.py
│   │   ├── ws_manager.py               ← WebSocket connection manager
│   │   └── ws_routes.py                ← All WS endpoint handlers
│   ├── core/
│   │   ├── alerting_system.py
│   │   ├── audit_trail.py
│   │   ├── auth_middleware.py          ← JWT decode (local HS256)
│   │   ├── backpressure.py / _v2.py
│   │   ├── cache/
│   │   ├── cache.py                    ← Redis manager singleton
│   │   ├── cancellation_idempotency_manager.py
│   │   ├── circuit_breaker.py
│   │   ├── config.py                   ← Settings (env vars)
│   │   ├── consistency_checker.py      ← Live position consistency
│   │   ├── credential_vault.py
│   │   ├── dag_task_queue.py
│   │   ├── database.py                 ← SQLAlchemy engine + Base
│   │   ├── database_pool.py
│   │   ├── database_scaling.py
│   │   ├── decimal_utils.py
│   │   ├── dependencies.py             ← FastAPI DI (auth, Supabase, Redis)
│   │   ├── deployment_config.py
│   │   ├── distributed_idempotency.py
│   │   ├── distributed_rate_limiter.py
│   │   ├── event_bus.py
│   │   ├── event_pipeline.py
│   │   ├── exchange_rate_limit_engine.py
│   │   ├── exchange_rate_limiter.py
│   │   ├── execution_engine.py
│   │   ├── feature_flags.py            ← Execution mode gates
│   │   ├── fill_deduplication_manager.py
│   │   ├── global_safety.py
│   │   ├── hard_quota_enforcer.py
│   │   ├── live_engine.py
│   │   ├── load_protection.py
│   │   ├── metrics.py                  ← Prometheus counters
│   │   ├── metrics_exporter.py
│   │   ├── ml_safety.py
│   │   ├── models/
│   │   │   ├── __init__.py             ← Model registry / re-exports
│   │   │   ├── billing.py              ← SubscriptionModel, InvoiceModel, PaymentMethodModel
│   │   │   ├── dag_task.py             ← DAGTaskModel (SQLAlchemy)
│   │   │   ├── execution_record.py     ← ExecutionRecordModel (SQLAlchemy)
│   │   │   ├── execution_tables.py
│   │   │   ├── pydantic_models.py      ← All Pydantic request/response schemas
│   │   │   └── reconciliation.py       ← ReconciliationMismatchModel (SQLAlchemy)
│   │   ├── order_state_engine.py
│   │   ├── order_state_machine.py
│   │   ├── performance_engine.py
│   │   ├── pipeline_guard.py
│   │   ├── portfolio_engine.py
│   │   ├── position_model.py           ← PositionModel (SQLAlchemy)
│   │   ├── quota_errors.py
│   │   ├── rate_limit_middleware.py
│   │   ├── rate_limiter.py
│   │   ├── reconciliation_engine.py
│   │   ├── reconciliation_scheduler.py ← Background 30s reconciliation loop
│   │   ├── redis_cluster.py
│   │   ├── replay_auth.py
│   │   ├── replay_reconstruction_engine.py
│   │   ├── risk_engine.py
│   │   ├── risk_manager.py
│   │   ├── safety_config.py            ← ExecutionFlags (LIVE/PAPER/SAFE gates)
│   │   ├── safety_monitor.py
│   │   ├── scaling_metrics.py
│   │   ├── schemas.py
│   │   ├── security_vault.py
│   │   ├── slo_monitor.py
│   │   ├── state.py                    ← AppState singleton (all engine refs)
│   │   ├── telemetry_engine.py
│   │   ├── tenant.py
│   │   ├── tenant_middleware.py
│   │   ├── unified_execution_engine.py ← ~100KB master execution orchestrator
│   │   ├── websocket_auth.py           ← WS JWT validation (_decode_hs256_token)
│   │   └── worker_restart_recovery_manager.py
│   ├── backend/
│   │   ├── __init__.py
│   │   ├── alert_engine.py
│   │   ├── alert_system.py
│   │   ├── atomic_persistence_coordinator.py
│   │   ├── backtesting_engine.py       ← VectorBT + ML backtest runner
│   │   ├── bot_telemetry.py
│   │   ├── circuit_breaker.py
│   │   ├── connection_engine.py        ← CCXT.pro exchange connector
│   │   ├── connection_manager.py
│   │   ├── dag_engine.py               ← DAG execution engine
│   │   ├── dag_engine_parallel.py      ← Parallel DAG (router exposed)
│   │   ├── dag_event_loop.py           ← Event-driven DAG (router exposed)
│   │   ├── dag_risk_integration.py     ← Risk-integrated DAG (router exposed)
│   │   ├── dag_worker.py
│   │   ├── data_observability.py
│   │   ├── data_pipeline_validator.py
│   │   ├── data_processing_engine.py
│   │   ├── data_seeking_engine.py      ← CCXT market data streams
│   │   ├── distributed_execution/
│   │   ├── event_listener.py
│   │   ├── event_publisher.py
│   │   ├── event_router.py
│   │   ├── exchange_executor.py        ← ~50KB exchange order execution
│   │   ├── exchange_reconciliation.py
│   │   ├── exchange_simulator.py       ← Paper trading simulator
│   │   ├── exchange_telemetry.py
│   │   ├── exchange_validation/
│   │   ├── exchange_websocket_listener.py
│   │   ├── execution_engine.py         ← ~92KB full execution engine
│   │   ├── execution_guard.py          ← ~73KB safety gate wrapper
│   │   ├── execution_router.py         ← Safety-gated production router
│   │   ├── execution_worker.py
│   │   ├── feature_engineering.py
│   │   ├── feature_validator.py
│   │   ├── fee_engine.py
│   │   ├── fleet_manager.py            ← Multi-user bot lifecycle
│   │   ├── indicators_backend.py
│   │   ├── logging_config.py
│   │   ├── market_data_validation.py   ← Router for validation endpoints
│   │   ├── master_executor.py
│   │   ├── metrics.py
│   │   ├── metrics_system.py
│   │   ├── ml_models.py                ← XGBoost model training
│   │   ├── observability/
│   │   │   └── sentry_config.py
│   │   ├── order_execution_engine.py
│   │   ├── order_watchdog.py           ← Stale order detector (30s interval)
│   │   ├── pnl_engine.py               ← P&L, Sharpe, drawdown calculations
│   │   ├── portfolio_cache_updater.py
│   │   ├── portfolio_engine.py
│   │   ├── portfolio_management.py     ← ~113KB portfolio router + logic
│   │   ├── portfolio_worker.py
│   │   ├── position_engine.py
│   │   ├── reconciliation_worker.py
│   │   ├── redis_manager.py
│   │   ├── replay_safe_transaction_guard.py
│   │   ├── risk_manager.py
│   │   ├── security_vault.py
│   │   ├── services/
│   │   ├── signal_trace_engine.py
│   │   ├── signal_validator.py
│   │   ├── soak_runtime/
│   │   ├── startup_recovery.py         ← Crash recovery on boot
│   │   ├── state_persistence.py        ← Router for state persistence
│   │   ├── state_service.py
│   │   ├── strategy_builder.py
│   │   ├── task_recovery.py
│   │   ├── telemetry_engine.py
│   │   ├── tenant_rls_validator.py
│   │   ├── transactional_execution_manager.py
│   │   ├── validation/
│   │   ├── validation_runtime/
│   │   ├── websocket_cluster.py
│   │   ├── websocket_manager.py
│   │   ├── websocket_monitor.py
│   │   ├── ws_channels.py
│   │   ├── ws_event_stream.py
│   │   └── ws_server.py
│   ├── routers/
│   │   ├── admin.py
│   │   ├── analytics.py
│   │   ├── auth.py
│   │   ├── billing.py
│   │   ├── dag_tasks.py
│   │   ├── distributed_execution.py
│   │   ├── exchange.py
│   │   ├── health.py
│   │   ├── health_websocket.py
│   │   ├── market.py
│   │   ├── metrics.py
│   │   ├── orders.py                   ← 45KB — primary order execution router
│   │   ├── portfolio.py
│   │   ├── risk.py
│   │   ├── security.py
│   │   ├── strategies.py               ← 60KB — strategy CRUD + backtest + deploy
│   │   ├── support.py
│   │   └── user.py
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── aggregator.py
│   │   ├── base.py
│   │   ├── registry.py
│   │   └── rsi_strategy.py             ← Built-in RSI strategy
│   └── workers/
│       ├── __init__.py
│       └── command_worker.py
│
├── algo22-terminal/                    ← DESKTOP APP (Tauri 2 + React)
│   ├── src/
│   │   ├── main.jsx                    ← React entry point
│   │   ├── App.jsx                     ← ~350KB monolithic SPA app
│   │   ├── AppState.jsx                ← Global app state context
│   │   ├── SupportPage.jsx
│   │   ├── apiClient.js                ← 45KB typed API client
│   │   ├── websocketClient.js          ← WS connection management
│   │   ├── websocketSafety.js          ← WS circuit breaker
│   │   ├── supabase.js                 ← Supabase JS client
│   │   ├── config.js
│   │   ├── api/
│   │   ├── assets/
│   │   ├── components/
│   │   │   ├── StrategyBuilder.jsx
│   │   │   ├── StrategyDashboard.jsx
│   │   │   ├── StrategyControlPanel.jsx
│   │   │   ├── StrategyRiskIndicator.jsx
│   │   │   ├── StrategyTemplatesOverlay.jsx
│   │   │   ├── BotMonitoringConsole.jsx
│   │   │   ├── EventDagRunner.jsx
│   │   │   ├── EventLogPanel.jsx
│   │   │   ├── KillSwitchBanner.jsx
│   │   │   ├── LiveRiskAlerts.jsx
│   │   │   ├── DrawdownMonitor.jsx
│   │   │   ├── SignalTracePanel.jsx
│   │   │   ├── SignalTraceVisualization.jsx
│   │   │   ├── LatencyMonitor.jsx
│   │   │   ├── RiskCommandCenter.jsx
│   │   │   ├── InfrastructureOperations.jsx
│   │   │   ├── DashboardUpgrades.jsx
│   │   │   ├── NotificationsPage.jsx
│   │   │   ├── ExecutionBlotter.jsx
│   │   │   ├── MarketRibbon.jsx
│   │   │   ├── CommandPalette.jsx
│   │   │   ├── Algo22Copilot.jsx
│   │   │   ├── WebSocketReconnectManager.jsx
│   │   │   ├── MonitoringTerminal.jsx
│   │   │   ├── FirstTradeWizard.jsx
│   │   │   └── OnboardingWidget.jsx
│   │   ├── constants/
│   │   ├── hooks/
│   │   ├── layouts/
│   │   ├── pages/
│   │   │   ├── PremiumDashboard.jsx
│   │   │   └── StrategyMarketplace.jsx
│   │   ├── store/
│   │   ├── types/
│   │   └── utils/
│   └── src-tauri/
│       ├── tauri.conf.json
│       ├── Cargo.toml
│       ├── build.rs
│       ├── src/                        ← Rust Tauri commands
│       └── capabilities/
│
├── aerora_quant_platform/              ← OLDER MONOREPO SCAFFOLD
│   ├── backend_api/                    ← Mirrors backend_app structure
│   ├── frontend_app/                   ← Mirrors algo22-terminal
│   ├── connection_layer/
│   ├── gpu_workers/
│   ├── shared/
│   ├── deployment/
│   └── infrastructure/
│
├── docker-compose.yml
├── docker-compose.production.yml
├── docker-compose.staging.yml
├── docker-compose.workers.yml
├── docker-compose.websocket.yml
├── docker-compose.redis-architecture.yml
├── docker-compose.backend-scaling.yml
├── Dockerfile
├── Dockerfile.backend
├── Dockerfile.simulator
├── Dockerfile.websocket
├── requirements.txt
├── railway.json
├── redis.conf
├── nginx/
├── k8s/
├── terraform/
└── monitoring/
```

---

## 3. Backend Services & Engine Inventory

The application is a **single FastAPI process** that bootstraps a set of named engines at startup via the `AppState` singleton (`core/state.py`). Each engine is wired once and injected via FastAPI `Depends()`.

| Engine | File | Class | Role |
|---|---|---|---|
| **A — SecurityVault** | `core/security_vault.py` | `SecurityVault` | AES-256 key vault; stores/retrieves encrypted exchange API keys |
| **B — TelemetryEngine** | `backend/telemetry_engine.py` | `TelemetryEngine` | QuestDB connection pool; writes OHLCV/tick time-series |
| **C — RiskManager** | `core/risk_manager.py` | `RiskManager` | Real-time position risk; circuit-breaker kill switch |
| **D — ConnectionEngine** | `backend/connection_engine.py` | `ConnectionEngine` | CCXT.pro exchange connector; shared pool; handles reconnects |
| **E — DataEngine** | `backend/data_seeking_engine.py` | `DataEngine` | WebSocket market data + private data streams via CCXT |
| **F — OrderEngine** | `backend/order_execution_engine.py` | `OrderExecutionEngine` | CCXT order placement + exponential-backoff retry |
| **G — DataProcessor** | `backend/data_processing_engine.py` | `DataProcessingEngine` | Numba tick→candle builder |
| **H — StrategyEngine** | `backend/strategy_builder.py` + `strategies/` | registry | Blueprint evaluator using safe-eval DAG runner |
| **I — BacktestEngine** | `backend/backtesting_engine.py` | `BacktestEngine` | VectorBT + ML model backtesting in thread pool |
| **J — XGBoostBlock** | `backend/ml_models.py` | `MLModels` | XGBoost model training; joblib persistence |
| **K — BotRunner** | `backend/execution_engine.py` | `ExecutionEngine` | Per-bot event loop; runs strategy signal→order pipeline |
| **L — FleetManager** | `backend/fleet_manager.py` | `FleetManager` | Multi-user bot lifecycle; start/stop/monitor all bots |
| **M — AlertEngine** | `backend/alert_engine.py` | `AlertEngine` | Discord/Telegram webhook dispatcher |

### Runtime Services (Started at lifespan)

These are long-lived background tasks started inside the FastAPI `@asynccontextmanager lifespan`:

| Service | File | Interval | Purpose |
|---|---|---|---|
| `ConsistencyChecker` | `core/consistency_checker.py` | 10 s | Detects position drift; arms kill-switch on mismatch |
| `OrderWatchdog` | `backend/order_watchdog.py` | 30 s | Flags stale orders (>60s unconfirmed); alerts at >300s |
| `PnLEngine` | `backend/pnl_engine.py` | on-demand | Decimal-precision Sharpe/drawdown calculation |
| `ReconciliationScheduler` | `core/reconciliation_scheduler.py` | 30 s | Exchange ↔ local state reconciliation; triggers kill switch |

---

## 4. FastAPI Routers

All routers are registered in `backend_app/main.py`.

### Standard REST Routers

| Router File | Prefix | Tag | Key Routes |
|---|---|---|---|
| `routers/auth.py` | `/api/auth` | Auth | POST /login, /register, /magic-link, /verify-2fa, /refresh |
| `routers/exchange.py` | `/api/exchanges` | Exchange Vault | GET/POST/DELETE exchange keys; POST /test |
| `routers/market.py` | `/api/market` | Market Data | GET /ticker, /orderbook, /candles, /symbols |
| `routers/orders.py` | `/api/orders` | Order Execution | POST /execute, /stop-loss, /take-profit; DELETE /cancel; GET /open, /history |
| `routers/strategies.py` | `/api/strategies` | Strategies | CRUD; POST /backtest, /train, /deploy, /stop; GET /bots |
| `routers/portfolio.py` | `/api/portfolio` | Portfolio | GET /summary, /positions, /pnl; POST /close-all |
| `routers/user.py` | `/api` | User | GET/PATCH /profile; POST /notifications, /upgrade |
| `routers/admin.py` | `/api/admin` | Admin / God Mode | GET /users; POST /freeze, /unfreeze, /global-kill |
| `routers/risk.py` | `/api/risk` | Risk Management | GET/PATCH /settings; POST /kill-switch; GET /status |
| `routers/billing.py` | `/api/billing` | Billing | GET /plans, /subscription, /invoices; POST /payment-method; Stripe webhooks |
| `routers/security.py` | `/api/security` | Security | Security audit endpoints |
| `routers/analytics.py` | `/api/analytics` | Analytics | GET /performance, /trade-stats |
| `routers/support.py` | `/api/support` | Support | POST /tickets; GET /tickets; POST /tickets/{id}/comment |
| `routers/metrics.py` | *(root)* | Metrics | GET /metrics (Prometheus exposition) |
| `routers/dag_tasks.py` | *(root)* | DAG Tasks | CRUD for DAG task queue |
| `routers/health.py` | (imported inline) | System | GET /health, /health/services, /health/live |

### Engine-Level Routers (mounted directly)

| Source Module | Router Variable | Notes |
|---|---|---|
| `backend/dag_event_loop.py` | `event_dag_router` | Event-driven DAG endpoints |
| `backend/dag_engine_parallel.py` | `parallel_dag_router` | Parallel DAG execution |
| `backend/dag_risk_integration.py` | `risk_dag_router` | Risk-checked DAG flow |
| `backend/execution_router.py` | `execution_router` | **SAFETY-GATED** — only mounted when `ExecutionFlags.PRODUCTION_ROUTER_ENABLED=True` |
| `backend/portfolio_management.py` | `portfolio_mgmt_router` | Portfolio management engine |
| `backend/market_data_validation.py` | `validation_router` | Data pipeline validation |
| `backend/state_persistence.py` | `persistence_router` | Bot state checkpoint/restore |
| `api_ws/ws_routes.py` | `ws_router` | All WebSocket endpoints |

---

## 5. Database Models (SQLAlchemy)

SQLAlchemy ORM is used for **local-fallback tables** (SQLite in dev, PostgreSQL in prod). The primary user data lives in **Supabase** (accessed via the PostgREST/supabase-py client). SQLAlchemy is used when Supabase is unavailable or for data requiring strong transactional guarantees outside Supabase's RLS scope.

All models are registered on `Base.metadata` and created via `Base.metadata.create_all(bind=engine)` at startup.

### Table: `execution_records`

File: `core/models/execution_record.py`  
Class: `ExecutionRecordModel`

| Column | Type | Notes |
|---|---|---|
| `execution_id` | `String` PK | Deterministic SHA-256 prefix: `exec_{hash[:16]}` |
| `tenant_id` | `UUID` NOT NULL | Row-level tenant isolation |
| `task_id` | `UUID` nullable | Links to `dag_tasks.task_id` |
| `strategy_id` | `String` NOT NULL | Strategy identifier |
| `symbol` | `String` NOT NULL | e.g. `BTCUSDT` |
| `side` | `String` NOT NULL | `buy` or `sell` |
| `size` | `String` NOT NULL | Order size (string for Decimal precision) |
| `price` | `String` nullable | Order price (null for market orders) |
| `status` | `Enum(ExecutionStatus)` | `pending` / `executing` / `completed` / `failed` / `unknown` |
| `order_id` | `String` nullable idx | Exchange order ID |
| `filled_size` | `String` nullable | Fill progress |
| `avg_price` | `String` nullable | Average fill price |
| `remaining_size` | `String` nullable | Remaining to fill |
| `exchange_id` | `String` nullable | Exchange identifier |
| `last_exchange_sync` | `DateTime` nullable | Last reconciliation timestamp |
| `exchange_status` | `String` nullable | Exchange-reported status |
| `result` | `JSON` nullable | Full execution result blob |
| `created_at` | `DateTime` NOT NULL | |
| `updated_at` | `DateTime` NOT NULL | Auto-updates on change |
| `submitted_at` | `DateTime` nullable | When sent to exchange |
| `filled_at` | `DateTime` nullable | When completely filled |

**Indexes:** `(tenant_id, symbol)`, `(task_id)`, `(status)`, `(tenant_id, status)`, `(created_at DESC)`, `(order_id)`, `(last_exchange_sync)`, partial index on active records

**Idempotency pattern:** `check_idempotent_execution()` generates a deterministic ID from `(tenant_id, strategy_id, symbol, time_bucket, side, qty, price)` and returns one of: `execute`, `skip_return_result`, `skip_already_running`, `allow_retry`, `skip_failed_no_retry`.

---

### Table: `dag_tasks`

File: `core/models/dag_task.py`  
Class: `DAGTaskModel`

| Column | Type | Notes |
|---|---|---|
| `task_id` | `String(36)` PK UUID | |
| `tenant_id` | `String(36)` NOT NULL idx | |
| `status` | `Enum(TaskStatus)` | `PENDING` / `ASSIGNED` / `RUNNING` / `COMPLETED` / `FAILED` / `CANCELLED` |
| `priority` | `Integer` NOT NULL | 1 (CRITICAL) – 10 (BACKGROUND) |
| `dag_config` | `JSON` NOT NULL | Full DAG node/edge config |
| `progress` | `Float` NOT NULL | 0.0–100.0 |
| `result` | `JSON` nullable | |
| `error` | `Text` nullable | |
| `retry_count` | `Integer` | |
| `max_retries` | `Integer` | |
| `created_at` | `DateTime` | |
| `started_at` | `DateTime` nullable | |
| `assigned_at` | `DateTime` nullable | |
| `completed_at` | `DateTime` nullable | |
| `last_heartbeat` | `DateTime` nullable | Worker liveness |
| `scheduled_for` | `DateTime` nullable | Deferred execution |
| `worker_id` | `String(64)` nullable idx | Assigned worker |

**Indexes:** `(tenant_id, status)`, `(created_at)`, `(status)`, `(tenant_id, created_at)`, `(tenant_id, priority, created_at)`

---

### Table: `reconciliation_mismatches`

File: `core/models/reconciliation.py`  
Class: `ReconciliationMismatchModel`

| Column | Type | Notes |
|---|---|---|
| `id` | `Integer` PK autoincrement | |
| `mismatch_id` | `String(32)` UNIQUE | Deterministic SHA-256: `rmm_{digest[:24]}` |
| `tenant_id` | `UUID` NOT NULL idx | |
| `execution_id` | `String` NOT NULL idx | |
| `order_id` | `String` nullable idx | |
| `symbol` | `String` NOT NULL | |
| `side` | `String` NOT NULL | |
| `field` | `String(64)` NOT NULL | Which field diverged: `size`, `status`, `side`, `symbol` |
| `local_value` | `Text` NOT NULL | Our DB value |
| `exchange_value` | `Text` NOT NULL | Exchange-reported value |
| `severity` | `Enum(MismatchSeverity)` | `critical` / `high` / `medium` |
| `status` | `Enum(MismatchStatus)` | `new` / `escalated` / `resolved` / `suppressed` |
| `resolved_at` | `DateTime` nullable | |
| `resolved_by` | `String(128)` nullable | |
| `resolution_notes` | `Text` nullable | |
| `escalation_count` | `Integer` | |
| `last_escalated_at` | `DateTime` nullable | |
| `kill_switch_triggered` | `Boolean` | |
| `raw_local_state` | `JSON` nullable | Full snapshot at detection |
| `raw_exchange_state` | `JSON` nullable | |
| `detected_at` | `DateTime` NOT NULL | |
| `created_at` / `updated_at` | `DateTime` | |

**Severity rules:**
- `critical` → size or symbol mismatch → immediate kill switch
- `high` → side mismatch
- `medium` → status or fill-price divergence

---

### Table: `subscriptions`

File: `core/models/billing.py`  
Class: `SubscriptionModel`

| Column | Type | Notes |
|---|---|---|
| `id` | `String(36)` PK UUID | |
| `user_id` | `String(36)` UNIQUE idx | |
| `plan_id` | `String(32)` | `free` / `starter` / `pro` / `enterprise` |
| `name` | `String(64)` | Display name |
| `priceUSD` | `Float` | |
| `priceINR` | `Float` | |
| `features` | `JSON` nullable | Feature list |
| `nextBillingDate` | `String(64)` nullable | |
| `autoRenew` | `Boolean` | |
| `created_at` / `updated_at` | `DateTime` | |

---

### Table: `invoices`

Class: `InvoiceModel` | Columns: `id`, `user_id`, `date`, `amtUSD`, `amtINR`, `status` (paid/pending/failed), `created_at`

---

### Table: `payment_methods`

Class: `PaymentMethodModel` | Columns: `id`, `user_id`, `payment_method_id` (Stripe PM ID), `brand`, `last4`, `expiry_month`, `expiry_year`, `is_default`, `created_at`

> **Security note:** No hardcoded defaults for card metadata — all values must originate from Stripe PaymentMethod objects.

---

### Supabase Tables (PostgREST — not SQLAlchemy)

These tables exist in Supabase PostgreSQL and are accessed exclusively through the `supabase-py` client with RLS:

| Table | Primary use |
|---|---|
| `profiles` | User profile, subscription tier, `deployed_bots` count, `is_frozen` flag, `ml_strategies_built`, `ml_addons_purchased` |
| `strategies` | Strategy blueprints (name, symbol, timeframe, buy_logic, sell_logic, risk, indicators, ml_model_path, status, dag_hash, execution_order) |
| `exchange_keys` | Encrypted exchange API credentials per user |
| `orders` | Order history |
| `positions` | Current open positions |
| `support_tickets` | Help desk tickets and comments |
| `notifications` | User notification settings |

---

## 6. Redis Integration

Redis is used as:

1. **Profile/session cache** — Supabase `profiles` rows cached for 300 s under key `profile_limits:{user_id}`
2. **Idempotency store** — Distributed idempotency keys for order execution
3. **Rate limiting** — Per-user and per-tenant request rate enforcement
4. **Bot state** — Live bot config and metrics
5. **Pub/Sub** — Internal event bus for WebSocket fan-out

### Client Hierarchy

```
core/cache.py       → RedisManager (primary; used by DI layer)
core/redis_client.py → RedisClient (secondary; simpler get/set wrapper)
core/redis_cluster.py → RedisCluster (future clustering support)
```

**Both clients support a `MockRedisClient` fallback** activated when `DEV_MODE=true` or Redis is unreachable. The mock is in-memory only (no persistence across restarts).

### Key Patterns

| Key Pattern | TTL | Purpose |
|---|---|---|
| `profile_limits:{user_id}` | 300 s | Cached Supabase profile |
| `idempotency:{execution_id}` | varies | Execution idempotency |
| `rate_limit:{user_id}:{endpoint}` | 60 s | Rate limit counter |
| `bot_state:{bot_id}` | - | Live bot configuration |
| `ws_session:{user_id}` | - | WebSocket session metadata |

### Cache Invalidation

Profile cache is explicitly invalidated via `invalidate_profile_cache(user_id)` after billing tier upgrades, preventing stale tier limits from blocking newly-upgraded users.

---

## 7. WebSocket Architecture

### Endpoints (all in `api_ws/ws_routes.py`)

| Path | Auth | Direction | Description |
|---|---|---|---|
| `WS /ws/ticker/{symbol}` | None | Server → Client | Real-time ticker for any trading pair |
| `WS /ws/orderbook/{symbol}` | None | Server → Client | Level 2 order book (configurable depth) |
| `WS /ws/candles/{symbol}/{timeframe}` | None | Server → Client | Live OHLCV candle stream |
| `WS /ws/user/{user_id}` | Bearer JWT (query `?token=`) | Bidirectional | Private channel: fills, order updates, balance changes |
| `WS /ws/pnl/{user_id}` | Bearer JWT (query `?token=`) | Server → Client | Streaming P&L updates |

### Connection Manager (`api_ws/ws_manager.py`)

A singleton `WSManager` (accessed via `app_state.ws`) maintains subscription maps:

```
"ticker"    → { symbol → set[WebSocket] }
"orderbook" → { symbol → set[WebSocket] }
"candles"   → { symbol_timeframe → set[WebSocket] }
"user"      → { user_id → set[WebSocket] }
"pnl"       → { user_id → set[WebSocket] }
```

### Heartbeat Protocol

- Server → Client: `{"type": "ping", "timestamp": <epoch>}` every 10 s
- Client → Server: `{"type": "pong"}` expected within 30 s
- On timeout: connection closed with code `1001`

### Stale Data Detection

- Data age > 30 s → **DROP** (not broadcast) + mark connection unhealthy
- No data for > 60 s → **FORCE RECONNECT** of the public exchange connection

### Authentication (WebSocket)

WS token validation uses **local HS256 JWT verification** (`core/websocket_auth.py::_decode_hs256_token`), eliminating the previous blocking `supabase.auth.get_user()` call that caused rate limit exhaustion.

### State Recovery on Reconnect

When a private `/ws/user/{id}` connection is established, `_sync_initial_state()` is called to immediately push:
- Open orders snapshot
- Current positions snapshot
- Account balance snapshot

This prevents stale data in the UI after connection drops.

---

## 8. Authentication Flow

### REST API Auth

```
Client Request
    └─► HTTP Bearer: Authorization: Bearer <supabase_jwt>
            └─► FastAPI bearer_scheme (HTTPBearer)
                    └─► get_current_user() [core/dependencies.py]
                            └─► auth_middleware.decode_token_local() [local HS256 verify]
                                    │   Uses SUPABASE_JWT_SECRET
                                    │   Extracts: sub (user_id), email, tenant_id, role
                                    └─► _get_cached_profile() → Redis → Supabase profiles
                                            └─► Check is_frozen → 403 if frozen
                                                    └─► Return user dict to handler
```

### WebSocket Auth

```
WS Connect: ws://host/ws/user/{user_id}?token=<jwt>&exchange_id=<id>
    └─► _validate_ws_token(token, user_id)
            └─► _decode_hs256_token(token) [core/websocket_auth.py]
                    └─► Validate signature + sub == user_id
                            └─► Accept or Close(4001)
```

### Admin Auth

`get_admin_user()` requires `app_metadata.role == "admin"` in the JWT claims. The `service_role` Supabase credential is explicitly **rejected** from admin endpoints to prevent key leakage from giving admin access.

### Per-Request Supabase Client (RLS)

For data operations that must respect Row-Level Security, `create_request_supabase(access_token)` creates a per-request Supabase client that injects the user's JWT into PostgREST, ensuring `auth.uid()` resolves correctly in RLS policies. **The anon key is used as the API key** (not the service role key) to preserve RLS enforcement.

### Tier / Limit System

| Tier | Max Bots | ML Training |
|---|---|---|
| `free` | 1 | 0 |
| `pro_999` | 5 | 0 |
| `elite_1999` | ∞ | 2 + purchased add-ons |

Live bot count is read from **FleetManager** (ground truth), not the stale `deployed_bots` Supabase counter column.

---

## 9. Strategy Builder Architecture

### Frontend Builder (`components/StrategyBuilder.jsx`)

A visual node-graph editor where users drag and drop nodes to build a strategy DAG. Node types available:

| Node Type | Role |
|---|---|
| `market_data` | Source node — subscribes to an exchange feed |
| `indicator` | Technical indicator (RSI, EMA, MACD, Bollinger, etc.) |
| `feature` | Engineered feature computation |
| `ml` | XGBoost/ML model inference node |
| `logic` | Boolean logic gate (AND, OR, NOT, threshold) |
| `signal` | Signal aggregator |
| `action` | Terminal node — emits `BUY` / `SELL` / `HOLD` |

### DAG Compilation Pipeline (`routers/strategies.py`)

Before a strategy is saved to Supabase, it passes through a 10-step DAG compiler:

1. **Structure validation** — unique node IDs, edge fields present
2. **Type validation** — only known `NodeType` values accepted
3. **Dependency validation** — all edge references point to existing nodes
4. **Type compatibility** — enforces the DAG type system (e.g., INDICATOR → FEATURE is valid; INDICATOR → ACTION is not)
5. **Adjacency build** — constructs in-memory adjacency list
6. **Cycle detection** — DFS graph traversal (DFS with grey/white/black coloring)
7. **Connectivity check** — BFS from input nodes; all nodes must be reachable
8. **Orphan detection** — nodes not reachable from any input are rejected
9. **Execution path** — at least one `action` node must be reachable
10. **Action input validation** — ACTION nodes may only receive input from SIGNAL or LOGIC nodes

If compilation succeeds, a `CompiledDAG` object is produced with:
- `execution_order` — topological sort (Kahn's algorithm)
- `action_nodes` — list of terminal nodes
- `dag_hash` — SHA-256 of node/edge set for integrity
- `schema_version` — for backward compatibility on upgrades

DAG fields are stored **inside `buy_logic` JSON** as `_nodes`, `_edges`, `_dag_version`, etc. (workaround for missing DB columns — a schema migration is pending).

### DAG Execution at Runtime (`backend/dag_engine.py`)

At bot execution time, the compiled DAG is:
1. Loaded from Supabase `strategies` table
2. Deserialized into DAG nodes
3. Executed in topological order by `DAGEngine`
4. Parallel branches run concurrently via `dag_engine_parallel.py`
5. Risk checks are injected at each action node via `dag_risk_integration.py`

### Built-in Strategies (`backend_app/strategies/`)

| File | Strategy |
|---|---|
| `rsi_strategy.py` | RSI-based mean reversion |
| `base.py` | Abstract base class `BaseStrategy` |
| `registry.py` | `get_strategy(name)` lookup |
| `aggregator.py` | Multi-strategy signal aggregation |

---

## 10. Backtesting Architecture

File: `backend/backtesting_engine.py` — Class: `BacktestEngine`

### Flow

```
POST /api/strategies/{id}/backtest
    └─► BacktestEngine.run_backtest_async()
            │   Converts CCXT OHLCV list → pandas DatetimeIndex Series
            │   Validates timeframe whitelist (1m–1w)
            └─► asyncio.to_thread(_compute_vectorbt_sync)
                    │   (runs in thread pool — never blocks event loop)
                    ├── FIX BT-4: Min 50 bars guard
                    ├── Position-aware entry/exit signals
                    │   (entries only when flat; exits only when open)
                    ├── ML predictions via joblib model (if model_path set)
                    ├── vbt.Portfolio.from_signals()
                    │   ├── fees=0.001 (0.1%)
                    │   ├── slippage=0.0005
                    │   ├── sl_stop, tp_stop
                    │   ├── size_type="percent"
                    │   └── freq from validated whitelist
                    └─► Returns (stats_dict, equity_curve_list)
```

### Output Metrics

| Metric | Source |
|---|---|
| `total_return_pct` | VectorBT stats |
| `final_equity` | Last equity curve value |
| `win_rate_pct` | `portfolio.trades.win_rate()` |
| `max_drawdown_pct` | VectorBT `Max Drawdown [%]` |
| `total_trades` | Trade count |
| `profit_factor` | gross_profit / gross_loss |
| `sharpe_ratio` | `(mean/std) * √252` (requires ≥20 trades) |
| `sortino_ratio` | VectorBT (None if unavailable — not forced to 0) |
| `calmar_ratio` | VectorBT |
| `total_fees_paid` | VectorBT stats |
| `expectancy` | `(win_rate × avg_win) − (loss_rate × avg_loss)` |

> **FIX BT-1 Note:** `_safe_stat()` handles `NaN` correctly — returns `None` instead of `0` for genuinely unavailable metrics. This prevents misleading UI display of 0 when data is missing.

---

## 11. Frontend Page Map

The primary frontend lives in `algo22-terminal/src/App.jsx` (358 KB monolithic file) with a client-side routing system using a `go(page)` navigation function.

### Page / View Routing

| Route Key | Component | Description |
|---|---|---|
| `dashboard` | `PremiumDashboard.jsx` | Main trading dashboard |
| `builder` | `StrategyBuilder.jsx` | Node-graph strategy editor |
| `marketplace` | `StrategyMarketplace.jsx` | Strategy discovery and copy-trading |
| `monitoring` | `BotMonitoringConsole.jsx` | Live bot monitoring console |
| `risk` | `RiskCommandCenter.jsx` | Risk settings and kill-switch |
| `portfolio` | Inline in `App.jsx` | Portfolio summary and positions |
| `blotter` | `ExecutionBlotter.jsx` | Trade execution history |
| `signals` | `SignalTracePanel.jsx` + `SignalTraceVisualization.jsx` | Signal trace and visualization |
| `notifications` | `NotificationsPage.jsx` | Notification history and settings |
| `infrastructure` | `InfrastructureOperations.jsx` | System operations panel |
| `dag` | `EventDagRunner.jsx` | DAG task runner UI |
| `support` | `SupportPage.jsx` | Help and ticket system |
| `settings` | Inline in `App.jsx` | User and app settings |
| `onboarding` | `FirstTradeWizard.jsx` + `OnboardingWidget.jsx` | New user onboarding flow |

### Key Shared Components

| Component | Purpose |
|---|---|
| `KillSwitchBanner.jsx` | Always-visible emergency stop banner |
| `LiveRiskAlerts.jsx` | Real-time risk alert panel |
| `DrawdownMonitor.jsx` | Live drawdown gauge |
| `LatencyMonitor.jsx` | API/WS latency metrics |
| `MarketRibbon.jsx` | Top-bar ticker ribbon |
| `CommandPalette.jsx` | Keyboard-driven command palette |
| `Algo22Copilot.jsx` | AI assistant integration |
| `WebSocketReconnectManager.jsx` | WS reconnect logic with backoff |
| `DashboardUpgrades.jsx` | Premium feature promotion |

---

## 12. Desktop / Tauri Architecture

**Tauri 2** wraps the React frontend as a cross-platform native desktop application.

### Tauri Config (`algo22-terminal/src-tauri/tauri.conf.json`)

| Property | Value |
|---|---|
| Product name | `Algo22` |
| Identifier | `com.algo22.terminal` |
| Dev URL | `http://localhost:1420` |
| Min window | 1024 × 680 px |
| Default window | 1280 × 800 px |
| CSP | Disabled (set to `null`) |
| Bundle targets | All (Windows `.exe`, macOS `.dmg`, Linux `.AppImage`) |
| Build command | `npm run build` |

### Build Flow

```
npm run dev              → Vite dev server on :1420
cargo tauri dev          → Rust shell + Vite in dev mode

npm run build            → Vite production build → dist/
cargo tauri build        → Rust binary + bundle dist/ into installer
```

### Rust Backend (`src-tauri/src/`)

The Tauri Rust layer is minimal — it primarily:
- Hosts the system window
- Forwards all API calls to the FastAPI backend over HTTP
- Handles native OS integration (notifications, file system access)

No heavy business logic lives in Rust; the frontend communicates with `backend_app/` via standard HTTP/WS.

---

## 13. Complete API Endpoint Catalogue

### System

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | None | Full health check (Redis, QuestDB, Supabase, Fleet) |
| GET | `/health/services` | None | Runtime service status (ConsistencyChecker, Watchdog, PnL, Recon) |
| GET | `/health/live` | None | Kubernetes liveness probe |
| GET | `/api/stats` | None | Global trading stats (currently returns empty structure) |
| GET | `/metrics` | None | Prometheus metrics exposition |

### Auth (`/api/auth`)

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/login` | Email+password sign-in → JWT |
| POST | `/api/auth/register` | User registration |
| POST | `/api/auth/magic-link` | Magic link email |
| POST | `/api/auth/verify-2fa` | 2FA verification |
| POST | `/api/auth/refresh` | Token refresh |

### Exchange Vault (`/api/exchanges`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/exchanges` | List connected exchanges |
| POST | `/api/exchanges` | Add exchange API keys (encrypted) |
| DELETE | `/api/exchanges/{exchange_id}` | Remove exchange |
| POST | `/api/exchanges/test` | Test connection to exchange |

### Market Data (`/api/market`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/market/ticker/{symbol}` | Current ticker |
| GET | `/api/market/orderbook/{symbol}` | Order book snapshot |
| GET | `/api/market/candles/{symbol}/{timeframe}` | Historical OHLCV |
| GET | `/api/market/symbols` | Available trading pairs |

### Orders (`/api/orders`)

| Method | Path | Description |
|---|---|---|
| POST | `/api/orders/execute` | Place a new order |
| POST | `/api/orders/stop-loss` | Set stop-loss |
| POST | `/api/orders/take-profit` | Set take-profit |
| DELETE | `/api/orders/cancel` | Cancel a specific order |
| DELETE | `/api/orders/cancel-all` | Cancel all open orders |
| GET | `/api/orders/open` | Open orders |
| GET | `/api/orders/history` | Order history |

### Strategies (`/api/strategies`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/strategies/` | List user's strategies |
| POST | `/api/strategies/` | Create strategy (with DAG validation) |
| GET | `/api/strategies/{id}` | Get strategy by ID |
| PUT | `/api/strategies/{id}` | Update strategy |
| DELETE | `/api/strategies/{id}` | Delete strategy |
| POST | `/api/strategies/{id}/backtest` | Run VectorBT backtest |
| POST | `/api/strategies/{id}/train` | Train ML model |
| POST | `/api/strategies/{id}/deploy` | Deploy strategy as live/paper bot |
| POST | `/api/strategies/{id}/stop` | Stop running bot |
| GET | `/api/strategies/bots` | List active bots |
| GET | `/api/strategies/{id}/signal-trace` | Signal trace for debugging |

### Portfolio (`/api/portfolio`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/portfolio/summary` | Portfolio summary |
| GET | `/api/portfolio/positions` | Open positions |
| GET | `/api/portfolio/pnl` | P&L breakdown |
| POST | `/api/portfolio/close-all` | Emergency close all positions |

### Risk Management (`/api/risk`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/risk/settings` | Current risk parameters |
| PATCH | `/api/risk/settings` | Update risk parameters |
| POST | `/api/risk/kill-switch` | Activate kill switch for user |
| GET | `/api/risk/status` | Risk engine status |

### Admin (`/api/admin`) — requires `role=admin`

| Method | Path | Description |
|---|---|---|
| GET | `/api/admin/users` | All users |
| POST | `/api/admin/freeze/{user_id}` | Freeze user account |
| POST | `/api/admin/unfreeze/{user_id}` | Unfreeze user account |
| POST | `/api/admin/global-kill` | Global kill switch (all bots) |

### Billing (`/api/billing`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/billing/plans` | Available subscription plans |
| GET | `/api/billing/subscription` | Current user subscription |
| GET | `/api/billing/invoices` | Invoice history |
| POST | `/api/billing/payment-method` | Add payment method (Stripe) |
| POST | `/api/billing/webhook` | Stripe webhook handler |

### User (`/api`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/profile` | User profile |
| PATCH | `/api/profile` | Update profile |
| POST | `/api/notifications` | Update notification settings |
| POST | `/api/upgrade` | Trigger plan upgrade |

### Support (`/api/support`)

| Method | Path | Description |
|---|---|---|
| POST | `/api/support/tickets` | Create support ticket |
| GET | `/api/support/tickets` | List tickets |
| GET | `/api/support/tickets/{id}` | Get ticket |
| POST | `/api/support/tickets/{id}/comment` | Add comment |

### Analytics (`/api/analytics`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/analytics/performance` | Strategy performance metrics |
| GET | `/api/analytics/trade-stats` | Trade statistics |

### WebSocket Endpoints

| Path | Description |
|---|---|
| `WS /ws/ticker/{symbol}` | Real-time ticker |
| `WS /ws/orderbook/{symbol}` | Level 2 order book |
| `WS /ws/candles/{symbol}/{timeframe}` | Live OHLCV |
| `WS /ws/user/{user_id}?token=&exchange_id=` | Private user channel |
| `WS /ws/pnl/{user_id}?token=` | P&L push channel |

---

## 14. Database Tables

### SQLAlchemy (Local / PostgreSQL)

| Table | Class | Primary Key | Notes |
|---|---|---|---|
| `execution_records` | `ExecutionRecordModel` | `execution_id` (SHA-256 deterministic) | Trade execution idempotency log |
| `dag_tasks` | `DAGTaskModel` | `task_id` (UUID) | DAG execution queue |
| `reconciliation_mismatches` | `ReconciliationMismatchModel` | `id` (autoincrement) + `mismatch_id` (UNIQUE) | Exchange ↔ local mismatch audit trail |
| `subscriptions` | `SubscriptionModel` | `id` (UUID) | Local fallback subscription data |
| `invoices` | `InvoiceModel` | `id` (UUID) | Local fallback invoice data |
| `payment_methods` | `PaymentMethodModel` | `id` (UUID) | Stripe payment methods |
| `positions` | `PositionModel` | (see `core/position_model.py`) | Live position tracking |

### Supabase PostgreSQL (via PostgREST)

| Table | Description |
|---|---|
| `profiles` | User profile, tier, bot count, freeze status |
| `strategies` | Strategy blueprints with DAG serialized in `buy_logic` JSON |
| `exchange_keys` | AES-encrypted exchange credentials |
| `orders` | Order history |
| `positions` | Position state |
| `support_tickets` | Help desk |
| `notifications` | User notification settings |

---

## 15. Strategy-Related Schemas

### Pydantic Schemas (from `core/models/pydantic_models.py`)

#### StrategyBlueprint
```python
class StrategyBlueprint(BaseModel):
    name: str
    symbol: str              # e.g. "BTC/USDT"
    timeframe: str           # e.g. "5m"
    exchange_id: str         # e.g. "binance"
    buy_logic: dict          # Embedded DAG + conditions
    sell_logic: dict
    risk: RiskParameters
    indicators: list
    ml_model_path: Optional[str]
```

#### RiskParameters
```python
class RiskParameters(BaseModel):
    max_position_size: float     # % of portfolio
    stop_loss_pct: float         # e.g. 0.05 = 5%
    take_profit_pct: float       # e.g. 0.15 = 15%
    max_daily_loss_pct: float
    max_drawdown_pct: float
```

#### DAG Schemas

```python
class NodeType(Enum):
    MARKET_DATA = "market_data"
    INDICATOR   = "indicator"
    FEATURE     = "feature"
    ML          = "ml"
    LOGIC       = "logic"
    SIGNAL      = "signal"
    ACTION      = "action"

class DAGNode(BaseModel):
    id: str
    type: NodeType
    data: dict              # Node-specific params

class DAGEdge(BaseModel):
    source: str
    target: str

class DAGConfig(BaseModel):
    nodes: List[DAGNode]
    edges: List[DAGEdge]
    symbols: List[str]
    timeframe: Optional[str]
```

#### BacktestRequest
```python
class BacktestRequest(BaseModel):
    strategy_id: str
    start_date: str          # ISO8601
    end_date: str
    initial_capital: float   # default 10000
    timeframe: str           # validated against VALID_FREQ_MAP
    trade_size_pct: float    # 0–1.0
    stop_loss_pct: float
    take_profit_pct: float
    ml_threshold: float      # 0.5–0.99
```

#### DeployRequest
```python
class DeployRequest(BaseModel):
    strategy_id: str
    mode: Literal["live", "paper"]
    exchange_id: str
```

#### DAG Storage Format (in Supabase `strategies.buy_logic`)

```json
{
  "_nodes": [...],
  "_edges": [...],
  "_dag_version": 1,
  "_dag_schema_version": 1,
  "_dag_created_at": "ISO8601",
  "_dag_updated_at": "ISO8601",
  "_dag_hash": "sha256_prefix_16"
}
```

---

## 16. Marketplace Functionality

### Current State: Frontend Only (Mock Data)

The Strategy Marketplace is implemented as a **read-only frontend prototype** with hardcoded mock data. There is no backend API for marketplace operations yet.

**File:** `algo22-terminal/src/pages/StrategyMarketplace.jsx`

### What Exists

| Feature | Status |
|---|---|
| Marketplace UI grid with cards | ✅ Implemented |
| Filter tabs (Trending, Most Copied, Highest Sharpe, Beginner) | ✅ UI only — no backend filter |
| Search bar | ✅ UI only — not connected |
| Strategy cards with metrics (copiers, 30D P&L, Sharpe) | ✅ Mock data only |
| Difficulty badge (Beginner / Advanced / Pro) | ✅ Mock |
| "Clone to Builder" button | ✅ Loads mock nodes into StrategyBuilder |
| `uiMode` adaptation (beginner vs expert terminology) | ✅ Connected to AppState |

### Mock Strategies

| ID | Name | Category | Copiers | Sharpe | 30D P&L |
|---|---|---|---|---|---|
| `strat-1` | RSI Mean Reversion Alpha | Mean Reversion | 12,450 | 2.1 | +14.5% |
| `strat-2` | High-Frequency Grid Array | Market Making | 8,320 | 3.4 | +22.1% |
| `strat-3` | EMA Golden Cross Trend | Trend Following | 4,500 | 1.5 | +8.2% |
| `strat-4` | Arbitrage Triangle Scalp | Arbitrage | 2,100 | 4.1 | +4.5% |

### What Needs to Be Built (for `feature/strategy-marketplace-v1`)

| Feature | Backend Work | Frontend Work |
|---|---|---|
| Marketplace Supabase table (`marketplace_strategies`) | Schema + RLS + migration | — |
| `GET /api/marketplace` — paginated strategy listing | New router | Connect search/filter |
| `POST /api/marketplace/{id}/clone` — fork strategy | New endpoint; clones `strategies` row | Wire Clone button |
| Strategy publish flow | New endpoint; visibility flag | Publish UI |
| Metrics aggregation (copier count, live P&L) | Background job | — |
| Search + filter API | Supabase PostgREST query | Filter bar |
| Rating / star system | Supabase table + RLS | Star component |

---

## 17. Infrastructure & Deployment

### Docker Compose Services

| Service | Dockerfile | Port | Description |
|---|---|---|---|
| `backend` | `Dockerfile.backend` | 8000 | FastAPI backend |
| `websocket` | `Dockerfile.websocket` | 8001 | Dedicated WS process |
| `simulator` | `Dockerfile.simulator` | — | Paper trading simulator |
| `redis` | `redis:alpine` | 6379 | Cache / pubsub |
| `nginx` | `nginx/` | 80/443 | Reverse proxy / SSL terminator |

### Cloud Deploy (Railway)

`railway.json` configures Railway deployment with auto-scaling. `Procfile` specifies:
```
web: uvicorn backend_app.main:app --host 0.0.0.0 --port $PORT
```

### Environment Variables (Required)

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_URL` | ✅ | Supabase project URL |
| `SUPABASE_ANON_KEY` | ✅ | Supabase anon key (for RLS user requests) |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | Service key (admin background jobs only) |
| `SUPABASE_JWT_SECRET` | ✅ | JWT signing secret (local verification) |
| `REDIS_URL` | ✅ | Redis connection URL |
| `AERORA_MODE` | ✅ | `safe` / `paper` / `live` (execution safety gate) |
| `DEFAULT_EXCHANGE` | ✅ | Default public exchange for market data |
| `SENTRY_DSN` | recommended | Error reporting |
| `DISCORD_WEBHOOK_URL` | optional | Alert notifications |
| `STRIPE_SECRET_KEY` | optional | Billing |
| `QUESTDB_HOST` | optional | Time-series store (defaults to `127.0.0.1`) |

---

## 18. Safety & Kill-Switch System

The platform has a layered safety architecture designed to prevent accidental live trading.

### Execution Mode Gates

File: `core/safety_config.py` — Class: `ExecutionFlags`

| Mode | `AERORA_MODE` | Live Trading | Paper Trading | Notes |
|---|---|---|---|---|
| Safe | `safe` (default) | ❌ | ❌ | All execution blocked |
| Paper | `paper` | ❌ | ✅ | Simulated only |
| Live | `live` | ✅ | ✅ | Full execution |

The production execution router (`/api/execution`) is additionally gated by `ExecutionFlags.PRODUCTION_ROUTER_ENABLED` and **not mounted by default**, requiring explicit operator enablement.

### Kill-Switch Triggers

Kill switch can be activated by:
1. **Manual** — `POST /api/risk/kill-switch` (user) or `POST /api/admin/global-kill` (admin)
2. **Automatic — ConsistencyChecker** — position drift detected
3. **Automatic — ReconciliationScheduler** — critical mismatch (size/symbol divergence)
4. **Automatic — OrderWatchdog** — orders stale > threshold

### Account Freeze

Admin can freeze user accounts via `POST /api/admin/freeze/{user_id}`. A frozen user:
- Gets `403 Forbidden` on all authenticated requests
- Cannot deploy bots
- Cannot execute orders

---

## 19. Cross-Cutting Concerns

### Idempotency

All trade executions use deterministic execution IDs computed as:
```
SHA-256(tenant_id + strategy_id + symbol + time_bucket + side + qty + price)
→ "exec_{hash[:16]}"
```

This guarantees that retries, network failures, and duplicate requests never result in duplicate trades.

### Tenant Isolation

- Every SQLAlchemy table has a `tenant_id` column.
- Supabase RLS policies enforce user isolation at the DB level.
- All queries are filtered by `tenant_id` before returning data.
- RLS validation is tested by `backend/tenant_rls_validator.py`.

### Observability

| System | Implementation |
|---|---|
| Error tracking | Sentry (initialized in `lifespan` via `backend/observability/sentry_config.py`) |
| Metrics | Prometheus counters in `core/metrics.py`; exposed at `GET /metrics` |
| Request tracing | `asgi_correlation_id` middleware (correlation ID injected into all requests) |
| Request metrics | `PrometheusMiddleware` tracks HTTP method / endpoint / status code / duration |
| Security headers | `SecurityHeadersMiddleware` injects CSP, X-Frame-Options, HSTS, etc. |
| Structured logs | JSON-structured logging via `backend/logging_config.py` |

### Rate Limiting

- `core/rate_limiter.py` — per-user rate limiting
- `core/distributed_rate_limiter.py` — Redis-backed distributed rate limiting
- `core/exchange_rate_limiter.py` — CCXT exchange-specific rate limiting
- `rate_limit_middleware.py` — FastAPI middleware integration

### Circuit Breaker

`core/circuit_breaker.py` and `backend/circuit_breaker.py` implement circuit breakers for:
- Exchange connectivity
- Supabase API calls
- QuestDB connections

---

## 20. Known Gaps & Onboarding Notes

### Architecture Debt

| Issue | Impact | Status |
|---|---|---|
| DAG fields stored inside `buy_logic` JSON blob | Schema coupling; hard to query | Pending migration to proper columns |
| Two parallel codebases (`backend_app/` and `aerora_quant_platform/`) | Confusion about which is canonical | `backend_app/` is canonical |
| Large monolithic `App.jsx` (358 KB) | Hard to maintain/test | Refactor to page-level lazy loading |
| `aerora_quant_platform/frontend_app/` still active | Frontend split across two trees | Consolidate into `algo22-terminal/` |
| `execution_router` safety-gated and not mounted | `/api/execution` endpoints inaccessible | Intentional — pending safety review |
| Marketplace is mock-only | No real backend data | Planned for `feature/strategy-marketplace-v1` |

### Onboarding Quick-Start

1. **Backend**: Set required env vars from `.env.example` → `uvicorn backend_app.main:app --reload`
2. **Frontend**: `cd algo22-terminal && npm install && npm run dev` (starts Vite on `:1420`)
3. **Desktop**: `cd algo22-terminal && cargo tauri dev` (requires Rust + `cargo-tauri`)
4. **Database**: Supabase project required. Local SQLite fallback active when `DEV_MODE=true`
5. **Redis**: Local Redis or `DEV_MODE=true` for in-memory mock
6. **Mode**: Start with `AERORA_MODE=paper` for safe testing

### File Size Reference (Key Files)

| File | Size | Notes |
|---|---|---|
| `algo22-terminal/src/App.jsx` | 358 KB | Entire SPA in one file |
| `backend_app/core/unified_execution_engine.py` | 103 KB | Master execution orchestrator |
| `backend_app/backend/portfolio_management.py` | 113 KB | Portfolio engine + router |
| `backend_app/backend/execution_engine.py` | 92 KB | Bot execution engine |
| `backend_app/backend/execution_guard.py` | 73 KB | Safety gate wrapper |
| `backend_app/routers/strategies.py` | 60 KB | Strategy CRUD + backtest + deploy |
| `backend_app/routers/orders.py` | 45 KB | Order execution router |
| `algo22-terminal/src/apiClient.js` | 45 KB | Typed API client |

---

*Document generated by automated repository audit — 2026-06-22.*  
*Branch: `feature/strategy-marketplace-v1`*  
*Do not modify application code when updating this document.*
