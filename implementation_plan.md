# Sprint 2A Phase 1 Remediation — Strategy Marketplace Implementation Plan

This plan outlines the complete implementation of the Strategy Marketplace, remediating all findings from the Sprint 2A Architecture Audit.

> [!IMPORTANT]  
> The `STRATEGY_LIBRARY_DESIGN.md` explicitly defined `library_strategies` and `library_ratings`, but did not specify detailed schemas for `strategy_images`, `strategy_favorites`, `strategy_versions`, `audit_log`, or `strategy_analytics`. I have proposed standard schemas for these tables in **Section 2 (Open Questions)** below. Please review them carefully before approving.

## User Review Required

> [!WARNING]  
> **Security Remediation (BUG-M01 & BUG-M02)**
> The stub endpoints for `clone`, `optimize`, `monte-carlo`, `walk-forward`, `pause`, and `resume` in `backend_app/routers/strategies.py` currently have NO authentication. I will immediately secure these with `Depends(get_current_user)` as Priority 0.

> [!NOTE]  
> **Supabase Management**
> I will run a Python script via `run_command` using the `supabase` Python client (or HTTP requests) to apply the missing SQL migrations directly to your active Supabase instance (`https://YOUR_PROJECT_REF.supabase.co`). I will also create the `strategy-images` bucket.

## Open Questions

### 1. Undefined Schemas
Since `strategy_images`, `strategy_favorites`, `strategy_versions`, `audit_log`, and `strategy_analytics` are not defined in the design doc but are required by your instructions, I propose the following structures:

*   **`strategy_favorites`**: `id`, `user_id`, `strategy_id` (refs `library_strategies`), `created_at`.
*   **`strategy_images`**: `id`, `strategy_id`, `image_url`, `image_type` ('cover', 'equity_curve'), `created_at`.
*   **`strategy_versions`**: `id`, `strategy_id`, `version_number`, `buy_logic` (JSONB), `sell_logic` (JSONB), `created_at`.
*   **`audit_log`**: `id`, `user_id`, `action` (e.g., 'publish', 'clone', 'rate'), `entity_id`, `metadata` (JSONB), `created_at`.
*   **`strategy_analytics`**: `id`, `strategy_id`, `metric_name`, `metric_value`, `recorded_at`.

Does this align with your expectations?

## Proposed Changes

---

### Priority 0: Critical Security Remediation
#### [MODIFY] [backend_app/routers/strategies.py](file:///c:/aerora_quant_backend_updated_final1/backend_app/routers/strategies.py)
*   Add `user: dict = Depends(get_current_user)` to `clone_strategy_stub`, `optimize_strategy_stub`, `monte_carlo_stub`, `walk_forward_stub`, `pause_strategy_stub`, and `resume_strategy_stub`.
*   Until fully implemented, these stubs will return HTTP 501 (Not Implemented) for anything other than `clone`. For `clone`, we will implement the real logic later in Priority 3.

---

### Priority 1: Database Foundation
#### [NEW] migrations/001_create_library_strategies.sql
*   Create `library_strategies` table per `STRATEGY_LIBRARY_DESIGN.md`.
#### [NEW] migrations/002_create_library_ratings.sql
*   Create `library_ratings` table per design.
#### [NEW] migrations/003_add_library_columns_to_strategies.sql
*   Add `source_library_id` (UUID) and `backtest_result` (JSONB) to `strategies`.
#### [NEW] migrations/004_rls_library_strategies.sql
*   Implement RLS for `library_strategies`.
#### [NEW] migrations/006_create_extended_marketplace_tables.sql
*   Create `strategy_images`, `strategy_favorites`, `strategy_versions`, `audit_log`, and `strategy_analytics`.
#### [MODIFY] Database Instance (Supabase)
*   Apply migrations 001, 002, 003, 004, 005 (existing), and 006 directly to Supabase via script.

---

### Priority 2: Supabase Storage
#### [MODIFY] Storage Instance (Supabase)
*   Create `strategy-images` bucket. Set to public access for easy CDN rendering on the frontend.
#### [NEW] [backend_app/backend/storage_service.py](file:///c:/aerora_quant_backend_updated_final1/backend_app/backend/storage_service.py)
*   Implement image upload utility (base64 PNG to Supabase bucket).

---

### Priority 3 & 4: API Router & Marketplace Services
#### [NEW] [backend_app/routers/library.py](file:///c:/aerora_quant_backend_updated_final1/backend_app/routers/library.py)
*   `GET /api/library`: Paginated browse endpoint. Uses Supabase queries on `library_strategies`.
*   `GET /api/library/{id}`: Detailed fetch.
*   `POST /api/library`: Publish strategy (validates `backtest_result` exists).
*   `DELETE /api/library/{id}`: Unpublish strategy.
*   `POST /api/library/{id}/clone`: Proper clone endpoint copying from `strategies` table, incrementing `clone_count`, and tracking `source_library_id`.
*   `POST /api/library/{id}/rate`: Upsert into `library_ratings`.
*   `GET /api/library/me`: Fetch current user's published libraries.
*   `PATCH /api/admin/library/{id}`: Admin moderation gate.
#### [MODIFY] [backend_app/main.py](file:///c:/aerora_quant_backend_updated_final1/backend_app/main.py)
*   Register `/api/library` router.
#### [NEW] [backend_app/backend/library_metrics_worker.py](file:///c:/aerora_quant_backend_updated_final1/backend_app/backend/library_metrics_worker.py)
*   Background process caching `clone_count` and `avg_rating` aggregation to Redis and writing back to `library_strategies`.

---

### Priority 5: Frontend Connection
#### [MODIFY] [algo22-terminal/src/App.jsx](file:///c:/aerora_quant_backend_updated_final1/algo22-terminal/src/App.jsx)
*   Register `<StrategyMarketplace />` in `PAGES` map.
*   Add `marketplace` entry to sidebar `NAV`.
#### [MODIFY] [algo22-terminal/src/pages/StrategyMarketplace.jsx](file:///c:/aerora_quant_backend_updated_final1/algo22-terminal/src/pages/StrategyMarketplace.jsx)
*   Remove `MOCK_MARKETPLACE`.
*   Integrate API calls to `/api/library`.
*   Implement Search, Filter, Sort state connected to backend queries.
*   Implement `handleClone` properly with success toasts.

---

### Priority 6: Telemetry Integration
#### [MODIFY] [backend_app/routers/library.py](file:///c:/aerora_quant_backend_updated_final1/backend_app/routers/library.py)
*   Emit `strategy_published`, `strategy_cloned`, `strategy_rated`, `strategy_featured`, `strategy_deleted` events via `event_bus.publish('marketplace_events', payload)`.

## Verification Plan

### Automated Tests
*   Run FastAPI test client against the new `/api/library` routes (if pytest is configured).
*   Validate unauthenticated requests to `/api/strategies/{id}/clone` are rejected (403/401).

### Manual Verification
*   Execute a DB script to verify all 7 missing tables exist.
*   Execute a DB script to verify the `strategy-images` bucket exists.
*   Hit the `/api/library` endpoint using python script.
*   Check that the frontend renders the real data and mock data is gone.
