# Duplicate Module Analysis

Generated: 2026-06-18

## Detection Summary
A full AST and path-based analysis was performed to detect architectural duplicates in the monolith prior to extraction.

## Findings by Category

### 1. Duplicate Routes
- **Status:** Detected
- **Details:** `/api/execution` and `/api/orders` have overlapping responsibilities. (Addressed by Phase 1 safety lockdown prioritizing `/api/orders`).
- **Action:** Microservice separation will map `orders` to the Backend API and isolate raw `execution` strictly to the connection layer / GPU workers if necessary.

### 2. Duplicate WebSocket Paths
- **Status:** Clear
- **Details:** `api_ws/` efficiently manages connections without path collision.
- **Action:** Map directly to Connection Layer service.

### 3. Duplicate Execution Engines
- **Status:** Detected
- **Details:** `backend/execution_engine.py`, `core/execution_engine.py`, `backend/unified_execution_engine.py`, `backend/order_execution_engine.py`.
- **Action:** The system maintains multiple execution contexts (Paper, Live, Simulator). The Institutional Architecture will isolate these by service boundaries, mapping the `unified` and `live` engines exclusively to the Connection Layer/Backend.

### 4. Duplicate Auth Middleware
- **Status:** Clear
- **Details:** `core/auth_middleware.py` acts as the single source of truth for JWT validation.

### 5. Duplicate Strategy Classes
- **Status:** Detected
- **Details:** Legacy RSI strategies vs new ML Strategy Engine definitions.
- **Action:** Isolate to `gpu_workers/strategies/` to keep the backend API lightweight.

### 6. Duplicate Modules
- **Status:** Detected
- **Details:** `backend/metrics.py` vs `core/metrics.py` vs `routers/metrics.py`.
- **Action:** During package separation, unify under `shared/config/metrics.py`.
