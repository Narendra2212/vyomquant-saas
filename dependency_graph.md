# Repository Module & Service Dependency Graph

This document details the architectural module dependency graph of the `vyomquant` backend repository.

---

## 1. Top-Level Entrypoint Hierarchy

```mermaid
graph TD
    A[gunicorn / uvicorn] --> B[backend_app/main.py]
    B --> C[backend_app/routers/*]
    B --> D[backend_app/core/middleware/*]
    B --> E[backend_app/core/cache/redis_manager.py]
    B --> F[backend_app/connection_layer/*]
    
    C --> G[backend_app/backend/execution_guard.py]
    C --> H[backend_app/backend/unified_execution_engine.py]
    C --> I[backend_app/core/rate_limiter.py]
    C --> J[backend_app/core/security.py]

    H --> K[backend_app/backend/order_router.py]
    H --> L[backend_app/backend/portfolio_manager.py]
    H --> M[backend_app/backend/risk_engine.py]

    K --> N[backend_app/backend/connection_engine.py]
    N --> O[CCXT / Exchange Sockets]
```

---

## 2. Inbound & Outbound Dependency Mapping

### A. FastAPI Application Core (`backend_app/main.py`)
- **Inbound Dependents** (Invoked by):
  - `gunicorn` / `uvicorn` startup command (`CMD ["./startup.sh"]`)
  - `scripts/pre_deployment_validation.py` (`test_fastapi_startup()`)
  - `tests/test_full_system.py`
- **Outbound Dependencies** (Imports):
  - `backend_app.routers` (15 modules: `health`, `auth`, `orders`, `strategies`, `library`, `market_data`, `risk`, `backtest`, `copilot`, `telemetry`, `tenant`, `webhooks`, `analytics`, `portfolio`, `dag_tasks`)
  - `backend_app.core.cache.redis_manager`
  - `backend_app.core.rate_limit`
  - `backend_app.core.middleware`

---

### B. Execution Engine (`backend_app/backend/unified_execution_engine.py`)
- **Inbound Dependents**:
  - `backend_app/routers/orders.py`
  - `backend_app/routers/strategies.py`
  - `backend_app/routers/dag_tasks.py`
  - `tests/test_execution_correctness.py`
- **Outbound Dependencies**:
  - `backend_app.backend.execution_guard`
  - `backend_app.backend.order_router`
  - `backend_app.backend.risk_engine`
  - `backend_app.core.cache.redis_manager`
  - `backend_app.connection_layer.database`

---

### C. Automated Deployment & Diagnostics Engine (`scripts/`)

```mermaid
graph LR
    Sub1[.github/workflows/03-deploy.yml] --> Sub2[scripts/pre_deployment_validation.py]
    Sub1 --> Sub3[scripts/ecs_deploy_and_diagnose.py]
    Sub1 --> Sub4[scripts/post_deployment_validation.py]

    Sub2 --> Sub5[scripts/import_audit.py]
    Sub2 --> Sub6[scripts/dependency_audit.py]
    Sub2 --> Sub7[scripts/docker_validation.py]
    Sub2 --> Sub8[scripts/aws_validation.py]

    Sub3 --> Sub9[AWS ECS API / CloudWatch Logs]
    Sub4 --> Sub10[HTTP Endpoints /health, /metrics]
```

---

## 3. Storage & Cache Dependency Reachability

- **PostgreSQL / Supabase Database**:
  - Reached via `backend_app/connection_layer/database.py` and SQLAlchemy ORM.
  - Required secrets: `/vyomquant/production/DATABASE_URL`, `/vyomquant/production/SUPABASE_SERVICE_ROLE_KEY`.
- **Redis Cache & Cluster**:
  - Reached via `backend_app/core/cache/redis_manager.py` shared connection pool.
  - Used for rate limiting, portfolio cache, order idempotency keys, and pub/sub streaming.
