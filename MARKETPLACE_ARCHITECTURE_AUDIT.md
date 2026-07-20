# MARKETPLACE ARCHITECTURE AUDIT
## Sprint 2A — Phase 1 Certification
**Project:** Aerora Quant Platform
**Audit Date:** 2026-07-18
**Auditor:** Sprint 2A Automated Architecture Certification Suite
**Scope:** Strategy Marketplace — all architecture layers
**Design Reference:** `STRATEGY_LIBRARY_DESIGN.md` (60,952 bytes, 2026-06-22)

---

## 1. Executive Summary

The Strategy Marketplace (referred to as the "Strategy Library" in the design document) has **a comprehensive design** but is **predominantly unimplemented**. The design document specifies a complete architecture — database schema, RLS policies, API routes, Redis caching, frontend pages, background workers, and admin moderation. Against this design, the current codebase is **predominantly unimplemented**.

The production codebase contains:
- One 174-line frontend component (`StrategyMarketplace.jsx`) displaying 100% hardcoded mock data
- Zero backend API routes for any marketplace operation (browse, publish, clone, rate, moderate)
- Zero marketplace database tables in Supabase (migrations written but not applied)
- One stub clone endpoint returning a hardcoded string with **no authentication**, no DB write, no lineage tracking
- No background metrics worker
- No storage buckets for strategy images
- No telemetry events for marketplace operations
- No audit logging for marketplace events
- No search, filter, or sort implementation

**Final Verdict: FAILED**

> The Strategy Marketplace is a design prototype. No marketplace operation (browse, publish, clone, rate, search, filter, sort, favorite, review, moderate) is production-functional. The frontend renders four hardcoded strategies with no API connection. The backend has no `/api/library` router.

---

## 2. Architecture Overview

### Designed Architecture (from `STRATEGY_LIBRARY_DESIGN.md`)
```
StrategyMarketplace.jsx
  └─► GET /api/library          (browse, paginated, filtered, sorted)
  └─► GET /api/library/{id}     (detail)
  └─► POST /api/library         (publish)
  └─► DELETE /api/library/{id}  (unpublish)
  └─► POST /api/library/{id}/clone  (fork to user's strategies)
  └─► POST /api/library/{id}/rate   (submit 1-5 star + review)
  └─► GET  /api/library/me      (my published strategies)
  └─► PATCH /api/admin/library/{id}  (admin moderation)
        │
        ▼
  FastAPI Backend (backend_app/main.py)
        │
        ├─► Supabase PostgreSQL
        │     ├── library_strategies  (NOT CREATED)
        │     ├── library_ratings     (NOT CREATED)
        │     ├── strategies          (EXISTS — missing source_library_id, backtest_result)
        │     └── profiles            (EXISTS)
        │
        ├─► Redis cache
        │     ├── library:browse_page:*  (NOT IMPLEMENTED)
        │     └── library:detail:{id}   (NOT IMPLEMENTED)
        │
        └─► background worker: library_metrics_worker.py  (NOT IMPLEMENTED)
```

### Actual Architecture (production runtime)
```
StrategyMarketplace.jsx
  └─► const MOCK_MARKETPLACE = [...] (4 hardcoded strategies, NO API call)
        │
        └─► handleClone(strat) → go('builder') with mock node  (NO API call)

NAV (sidebar): no 'marketplace' entry
  └─► StrategyMarketplace.jsx is NOT registered in App.jsx PAGES{}
  └─► StrategyMarketplace.jsx is NOT in NAV[]
```

---

## 3. Frontend Architecture

### 3.1 Component Inventory

| Component | File | Status | Evidence |
|---|---|---|---|
| `StrategyMarketplace` | `algo22-terminal/src/pages/StrategyMarketplace.jsx` | MOCK ONLY | Code verified — `MOCK_MARKETPLACE` array, no fetch/api call |
| Sidebar nav entry | `algo22-terminal/src/App.jsx:L1491-L1510` | MISSING | `NAV` array has no `{ id: "marketplace", ... }` entry |
| App page routing | `algo22-terminal/src/App.jsx:L7994-L8016` | MISSING | `PAGES` object has no `marketplace` key |
| `LibraryDetailPage` | (design: `src/pages/LibraryDetailPage.jsx`) | NOT CREATED | File not found in repo |
| `MyLibraryPage` | (design: `src/pages/MyLibraryPage.jsx`) | NOT CREATED | File not found in repo |
| `PublishModal` | (design: `src/components/marketplace/PublishModal.jsx`) | NOT CREATED | File not found |
| `RatingWidget` | (design: `src/components/marketplace/RatingWidget.jsx`) | NOT CREATED | File not found |
| `LibraryCard` | (design: `src/components/marketplace/LibraryCard.jsx`) | NOT CREATED | File not found |
| `CloneConfirmModal` | (design: component) | NOT CREATED | File not found |

### 3.2 Frontend Data Flow

**Actual:**
```
StrategyMarketplace renders → MOCK_MARKETPLACE (JS constant, 4 entries)
handleClone(strat) → go('builder') with mock nodes/edges
Search input → NO handler → no state update, no filter
Filter buttons → setFilter(f) → no data effect (mock data not filtered)
```

**Finding: CRITICAL — The search input does not filter any data. The filter buttons update React state but no filtering logic applies to `MOCK_MARKETPLACE`. The filter state is cosmetically decorative.**

### 3.3 AppState Integration

`AppState.jsx` exports:
```javascript
const [demoMode, setDemoMode] = useState(false);
const [uiMode, setUiMode] = useState('pro');
```

No marketplace state (selected strategy, user ratings, clone status, favorites, published strategies) exists in AppState. **Code Verified.**

### 3.4 Frontend Routing

`StrategyMarketplace.jsx` is **not reachable via any navigation path** in the production application:
- Not in `NAV` array (`App.jsx:L1491`)
- Not in `PAGES` object (`App.jsx:L7994`)
- Not imported in `App.jsx`

The component exists as an **orphan module** — it exists in the file system but is unreachable from the running application. **Code Verified.**

---

## 4. Backend Architecture

### 4.1 Router Registration (`backend_app/main.py`)

All registered API routers (code verified — `main.py:L464-L519`):

| Router | Prefix | Marketplace Relevant |
|---|---|---|
| `auth.router` | `/api/auth` | Auth flow only |
| `exchange.router` | `/api/exchanges` | No |
| `market.router` | `/api/market` | No |
| `orders.router` | `/api/orders` | No |
| `strategies.router` | `/api/strategies` | Partial (strategy CRUD) |
| `portfolio.router` | `/api/portfolio` | No |
| `user.router` | `/api` | No |
| `admin.router` | `/api/admin` | No library moderation |
| `risk.router` | `/api/risk` | No |
| `billing.router` | `/api/billing` | No |
| `analytics.router` | `/api/analytics` | No library analytics |

**No `/api/library` router exists. Code Verified.**

### 4.2 Strategy Router — Marketplace-Adjacent Endpoints

All routes under `/api/strategies` (code verified — `strategies.py`):

| Method | Path | Auth | Implementation | Status |
|---|---|---|---|---|
| GET | `/api/strategies/` | JWT | Full implementation | OPERATIONAL |
| POST | `/api/strategies/` | JWT | Full implementation | OPERATIONAL |
| GET | `/api/strategies/{id}` | JWT | Full implementation | OPERATIONAL |
| PUT | `/api/strategies/{id}` | JWT | Full implementation | OPERATIONAL |
| DELETE | `/api/strategies/{id}` | JWT | Full implementation | OPERATIONAL |
| POST | `/api/strategies/{id}/deploy` | JWT | Full implementation | OPERATIONAL |
| POST | `/api/strategies/{id}/stop` | JWT | Full implementation | OPERATIONAL |
| POST | `/api/strategies/train-ml` | JWT | Full implementation | OPERATIONAL |
| POST | `/api/strategies/validate` | JWT | Full implementation | OPERATIONAL |
| POST | `/api/strategies/backtest` | JWT | Full implementation | OPERATIONAL |
| POST | `/api/strategies/{id}/clone` | **NO AUTH** | **STUB** — hardcoded response | FAIL |
| POST | `/api/strategies/optimize` | NO AUTH | STUB — hardcoded response | FAIL |
| POST | `/api/strategies/monte-carlo` | NO AUTH | STUB — hardcoded response | FAIL |
| POST | `/api/strategies/walk-forward` | NO AUTH | STUB — hardcoded response | FAIL |
| POST | `/api/strategies/{id}/pause` | NO AUTH | STUB — hardcoded response | FAIL |
| POST | `/api/strategies/{id}/resume` | NO AUTH | STUB — hardcoded response | FAIL |

**BUG-M01 (CRITICAL SECURITY): `POST /api/strategies/{id}/clone` has no authentication.** Any unauthenticated caller can hit this endpoint. The stub returns `{"strategy_id": "new_clone_id_123"}` — a hardcoded static string. No database write occurs, no lineage is tracked, no clone_count is incremented. **Code Verified.**

### 4.3 Designed vs Implemented API Endpoints

| Designed Endpoint | Designed in | Implemented | Notes |
|---|---|---|---|
| `GET /api/library` | `STRATEGY_LIBRARY_DESIGN.md:L100` | NO | Not registered |
| `GET /api/library/{id}` | Design | NO | Not registered |
| `POST /api/library` | Design | NO | Publish — not registered |
| `DELETE /api/library/{id}` | Design | NO | Unpublish — not registered |
| `POST /api/library/{id}/clone` | Design | NO | Proper clone — not registered |
| `POST /api/library/{id}/rate` | Design | NO | Ratings — not registered |
| `GET /api/library/me` | Design | NO | My library — not registered |
| `PATCH /api/admin/library/{id}` | Design | NO | Moderation — not registered |

**All 8 designed marketplace API endpoints are missing from production. Code Verified.**

### 4.4 Backend Services Inventory

The backend has an extensive service layer (`backend_app/backend/`) but **zero marketplace-specific services**:

| Designed Service | Status |
|---|---|
| `library_metrics_worker.py` (clone_count/avg_rating recompute) | NOT CREATED |
| Library browse service (paginated browse with Redis cache) | NOT CREATED |
| Library publish service (snapshot strategy to library_strategies) | NOT CREATED |
| Library clone service (fork strategy + lineage) | NOT CREATED |
| Library rating service (UPSERT + fn_has_user_cloned check) | NOT CREATED |
| Library moderation service (admin approve/reject/feature) | NOT CREATED |

The only worker in `backend_app/workers/` is `command_worker.py` (2,078 bytes). **Code Verified.**

---

## 5. API Architecture

### 5.1 Authentication Flow

**Production auth flow (for all operational `/api/strategies/*` endpoints):**
```
Request → JWT in Authorization header
  └─► get_current_user() dependency (backend_app/core/dependencies.py)
        └─► Supabase JWT verify (RS256)
              └─► return user dict {id, email, ...}
```

**Marketplace auth flow: N/A — no marketplace endpoints exist.**

**Bug at clone stub (`strategies.py:L1543-L1545`):**
```python
@router.post("/{strategy_id}/clone")
async def clone_strategy_stub(strategy_id: str):  # No auth dependency
    return {"status": "ok", "message": "Strategy cloned", "strategy_id": "new_clone_id_123"}
```

### 5.2 API Response Schemas

No Pydantic models exist for:
- `LibraryBrowseResponse`, `LibraryDetailResponse`, `LibraryCardResponse`
- `PublishRequest`, `RateRequest`, `CloneResponse`, `ModerationRequest`

**Code Verified — no models found matching these names in `backend_app/`.**

---

## 6. Database Architecture

### 6.1 Supabase Table Existence (Runtime Verified)

```
Verification method: Direct Supabase service-role query
Timestamp: 2026-07-18T16:09:54Z
```

| Table | Design Requires | Status | Runtime Evidence |
|---|---|---|---|
| `library_strategies` | PRIMARY CATALOGUE | NOT IN SUPABASE | Could not find the table 'public.library_strategies' in the schema |
| `library_ratings` | RATINGS/REVIEWS | NOT IN SUPABASE | Could not find the table 'public.library_ratings' in the schema |
| `strategy_images` | IMAGE METADATA | NOT IN SUPABASE | Could not find the table 'public.strategy_images' in the schema |
| `strategies` | EXISTING — clone target | EXISTS | 1 row confirmed, 14 columns verified |
| `profiles` | EXISTING — author alias | EXISTS | Columns: username, display_name, avatar_url, bio |
| `audit_log` | AUDIT TRAIL | NOT IN SUPABASE | Missing |
| `strategy_analytics` | ANALYTICS | NOT IN SUPABASE | Missing |
| `strategy_versions` | VERSIONING | NOT IN SUPABASE | Missing |
| `strategy_favorites` | FAVORITES | NOT IN SUPABASE | Missing |

### 6.2 `strategies` Table Schema (Runtime Verified — 2026-07-18T16:09:54Z)

```
Actual columns:
  id, user_id, name, symbol, timeframe,
  buy_logic (JSONB), sell_logic (JSONB), risk (JSONB),
  indicators (TEXT[]), ml_model_path (nullable),
  exchange_id, status, created_at, updated_at
```

**Missing marketplace columns (design requires, not present):**
- `source_library_id UUID` — clone lineage tracking
- `backtest_result JSONB` — required for publish eligibility check

Specified in `STRATEGY_LIBRARY_DESIGN.md:L274-L287` and in the Alembic migration at `L299-L329`. Neither has been applied to Supabase. **Code Verified.**

### 6.3 Migration Status

| Migration File | Purpose | Applied |
|---|---|---|
| `migrations/001_create_dag_tasks_table.sql` | DAG tasks | YES (dag_tasks exists) |
| `migrations/002_create_execution_records.sql` | Execution records | YES |
| `migrations/005_rls_library_ratings.sql` | RLS for library_ratings | NO — depends on library_ratings which doesn't exist |

Migration `005` has a dependency guard on line 44 that will `RAISE EXCEPTION` if `library_ratings` doesn't exist. Migrations 001-004 for library tables were never created or applied. **Code Verified.**

### 6.4 RLS Policies

The design specifies RLS policies for `library_strategies` and `library_ratings`. Since neither table exists, no RLS policies apply. **Runtime Verified.**

### 6.5 Database Entity Relationships

**Designed:**
```
auth.users
  └──< library_strategies (author_id → auth.users.id)
         └──< library_ratings (library_id → library_strategies.id)
                └── user_id → auth.users.id
strategies
  └── source_library_id → library_strategies.id (clone lineage)
profiles
  └── id → auth.users.id (alias display)
```

**Actual:**
```
auth.users
  └──< strategies (user_id → auth.users.id)
profiles
  └── id → auth.users.id
```

No marketplace relationships exist. **Runtime Verified.**

---

## 7. Storage Architecture

### 7.1 Supabase Storage Buckets (Runtime Verified)

```
Verification: sb.storage.list_buckets(), 2026-07-18T16:09:05Z
Result: No buckets returned (empty list)
```

**No Supabase storage buckets exist.**

| Designed Bucket | Purpose | Status |
|---|---|---|
| `strategy-images` | Strategy cover images, equity curve PNGs | NOT CREATED |

### 7.2 Image Upload Flow

The design specifies: on publish, generate equity curve PNG → upload to `strategy-images/{library_id}/equity_curve.png` → store URL in `library_strategies.equity_curve_snapshot`.

**This entire flow is non-existent.** No upload service exists. **Code Verified.**

---

## 8. Authentication Architecture

### 8.1 Auth Mechanism

The platform uses Supabase JWT (RS256). All operational strategy endpoints correctly use `Depends(get_current_user)`. **Code Verified.**

### 8.2 Marketplace Auth Gaps

| Endpoint | Auth Required | Auth Applied | Risk |
|---|---|---|---|
| `POST /api/strategies/{id}/clone` (STUB) | YES | NO | CRITICAL — unauthenticated access |
| `POST /api/strategies/optimize` (STUB) | YES | NO | HIGH — unauthenticated |
| `POST /api/strategies/pause` (STUB) | YES | NO | HIGH — unauthenticated |
| `POST /api/strategies/resume` (STUB) | YES | NO | HIGH — unauthenticated |

All stub endpoints lack `Depends(get_current_user)`. **Code Verified.**

### 8.3 JWT Token Storage (Frontend)

JWT stored in `sessionStorage` — cleared on tab close, safer than `localStorage` against XSS persistence. **Code Verified — `App.jsx:L7918`.**

---

## 9. Authorization Architecture

### 9.1 Tenant Isolation

**Operational strategies endpoints:** Tenant isolation enforced via Supabase RLS (user_id = auth.uid()). **Code Verified.**

**Marketplace endpoints:** No marketplace endpoints exist. No tenant isolation assessment possible.

### 9.2 RLS Policy Status

| Table | RLS Enabled | Status |
|---|---|---|
| `strategies` | YES | ACTIVE |
| `profiles` | Presumed YES | ACTIVE |
| `library_strategies` | N/A — table not created | NOT APPLICABLE |
| `library_ratings` | N/A — table not created | NOT APPLICABLE |

### 9.3 Admin Authorization

The design specifies `PATCH /api/admin/library/{id}` for admin moderation. The admin router (`admin.py`) has no marketplace moderation endpoints. **Code Verified.**

---

## 10. Strategy Lifecycle

### 10.1 Designed Lifecycle
```
DRAFT (in builder)
  └─► BACKTESTED (has backtest_result)
        └─► PUBLISHED (library_strategies, moderation_status=pending)
              └─► APPROVED (admin)
              └─► FEATURED (admin)
              └─► CLONED (other users → new strategies row)
              └─► RATED (library_ratings upserted)
              └─► UNPUBLISHED (is_active=FALSE)
```

### 10.2 Actual Lifecycle
```
DRAFT (in builder)
  └─► BACKTESTED (result returned to client only — NOT persisted to DB)
```

No lifecycle beyond user's own strategy CRUD exists. **Code Verified.**

**Critical gap:** `backtest_result JSONB` column does not exist in Supabase. Even if library endpoints were built, strategies cannot be published because the backtest persistence mechanism is missing.

### 10.3 Strategy Versioning

`CompiledDAG` class in `strategies.py:L80-L144` implements DAG schema versioning:
```python
SCHEMA_VERSION = 1  # strategies.py:L88
```
DAG version stored in `buy_logic`/`sell_logic` JSONB blobs. This tracks DAG schema compatibility, not marketplace publishing history. No dedicated version history table exists. **Code Verified.**

---

## 11. Marketplace Workflow

### 11.1 Designed Workflow
```
Publish: backtest → POST /api/library → admin approval → public catalogue
Clone:   POST /api/library/{id}/clone → fork DAG → lineage tracking → clone_count++
Rate:    POST /api/library/{id}/rate → fn_has_user_cloned() gate → UPSERT ratings
```

### 11.2 Actual Workflow
```
User opens app → sidebar → "Marketplace" nav item: NOT PRESENT
If user navigates to go('marketplace'): PAGES['marketplace'] is undefined
App renders: "Page not found: marketplace"
```

**No marketplace workflow is executable in production. Code Verified.**

---

## 12. Data Flow Diagram

### Designed
```
[Frontend] → GET /api/library → [FastAPI] → Redis check → [Supabase library_strategies] → Response
```

### Actual
```
[Frontend StrategyMarketplace.jsx] → const MOCK_MARKETPLACE = [...4 hardcoded...]
                                    → renders 4 mock cards, zero network requests
```

---

## 13. Dependency Graph

### Frontend Dependencies
```
StrategyMarketplace.jsx
  ├── React (useState)
  ├── lucide-react (icons)
  └── ../AppState (uiMode only)
  
  NOT connected to: api.js, Supabase client, auth context
```

### Migration Chain (Design → Missing)
```
migrations/001_create_library_strategies.sql  [MISSING]
  └─► migrations/002_create_library_ratings.sql  [MISSING]
        └─► migrations/004_rls_library_strategies.sql  [MISSING]
              └─► migrations/005_rls_library_ratings.sql  [EXISTS but unapplicable]
```

---

## 14. Security Boundaries

### Current Boundaries
```
Internet → FastAPI (CORS, CSP headers) → JWT auth → User-scoped Supabase client (RLS)
```

### Security Gaps (Marketplace)

| Gap | Severity | Description |
|---|---|---|
| Unauthenticated clone stub | CRITICAL | `POST /api/strategies/{id}/clone` — no auth |
| Unauthenticated optimize/pause/resume stubs | HIGH | Same pattern — 5 endpoints |
| No clone gate for ratings | HIGH | Rate endpoint must verify fn_has_user_cloned() — not implemented |
| No moderation gate | HIGH | Published strategies must require admin approval |

### Security Headers (Code Verified — `main.py`)
CSP, X-Frame-Options: DENY, X-Content-Type-Options, X-XSS-Protection, HSTS — all active.

---

## 15. Telemetry Integration

### Active Channels (Sprint 1D3)
`execution_events`, `risk_events`, `signal_trace`, `bot_status` — all ACTIVE.

### Marketplace Telemetry

| Designed Event | Status |
|---|---|
| `strategy_published` | NOT EMITTED — channel doesn't exist |
| `strategy_cloned` | NOT EMITTED |
| `strategy_rated` | NOT EMITTED |
| `strategy_featured` | NOT EMITTED |

**No marketplace telemetry exists.** `event_bus.py` is available but no marketplace events are wired. **Code Verified.**

---

## 16. Dead Code Audit

### Orphan Files

| File | Reason Orphaned |
|---|---|
| `algo22-terminal/src/pages/StrategyMarketplace.jsx` | Not imported in App.jsx; not in NAV; not in PAGES |
| `migrations/005_rls_library_ratings.sql` | Unapplicable — base tables missing |

### Stub Endpoints (All Unauthenticated, All No-Op)

| Endpoint | Location | Issue |
|---|---|---|
| `POST /api/strategies/{id}/clone` | `strategies.py:L1543-L1545` | No auth, returns hardcoded clone ID |
| `POST /api/strategies/optimize` | `strategies.py:L1547-L1549` | No auth, returns empty results |
| `POST /api/strategies/monte-carlo` | `strategies.py:L1551-L1553` | No auth, returns empty results |
| `POST /api/strategies/walk-forward` | `strategies.py:L1555-L1557` | No auth, returns empty results |
| `POST /api/strategies/{id}/pause` | `strategies.py:L1559-L1561` | No auth, returns "paused" |
| `POST /api/strategies/{id}/resume` | `strategies.py:L1563-L1565` | No auth, returns "running" |

---

## 17. Architecture Risks

| Risk ID | Risk | Severity |
|---|---|---|
| RISK-M01 | Stub clone endpoint has no auth — exploitable today | CRITICAL |
| RISK-M02 | `backtest_result` not persisted — publish prerequisite impossible | HIGH |
| RISK-M03 | `library_strategies` table missing — no catalogue can exist | HIGH |
| RISK-M04 | Migration chain incomplete — `005` unapplicable without `001-004` | HIGH |
| RISK-M05 | `StrategyMarketplace.jsx` renders mock data — if nav item added, users see fake 12,450 copiers | HIGH |
| RISK-M06 | No tenant isolation implemented for browse — RLS must be verified when tables created | CRITICAL |
| RISK-M07 | Redis not deployed locally — metrics worker would fail if started | MEDIUM |
| RISK-M08 | No image upload infrastructure — equity curves can't be displayed | MEDIUM |
| RISK-M09 | No DAG serialization format locked for clone — version drift risk | MEDIUM |
| RISK-M10 | No moderation gate — strategies could bypass review before appearing in browse | HIGH |

---

## 18. Production Risks

| Risk | Status |
|---|---|
| Mock data in `StrategyMarketplace.jsx` | ACTIVE — orphaned so not user-facing yet |
| `POST /api/strategies/{id}/clone` returns fake clone ID to any caller | ACTIVE — live endpoint, callable now |
| No backtest persistence — publish eligibility unenforceable | ACTIVE |
| Frontend hardcodes "12,450 copiers", "+22.1% 30D P&L" — misleading | ACTIVE in component (orphaned) |

---

## 19. Bug List

| Bug ID | Severity | Category | Description | Location | Fix |
|---|---|---|---|---|---|
| BUG-M01 | CRITICAL | Security | `POST /api/strategies/{id}/clone` — no auth, returns hardcoded `new_clone_id_123` | `strategies.py:L1543-L1545` | Add `Depends(get_current_user)` or return HTTP 501 |
| BUG-M02 | CRITICAL | Security | `POST /api/strategies/pause`, `resume`, `optimize`, `monte-carlo`, `walk-forward` — no authentication | `strategies.py:L1547-L1565` | Add `Depends(get_current_user)` to all stubs |
| BUG-M03 | HIGH | Correctness | Search input in `StrategyMarketplace.jsx` has no onChange handler | `StrategyMarketplace.jsx:L96-L101` | Block nav entry until real API built |
| BUG-M04 | HIGH | Correctness | Filter buttons update `filter` state but `MOCK_MARKETPLACE.map()` ignores filter value | `StrategyMarketplace.jsx:L104-L166` | Block or implement real filtering |
| BUG-M05 | HIGH | Data | `backtest_result` column missing from `strategies` table | Supabase DB | Apply Alembic migration to add column |
| BUG-M06 | HIGH | Data | `source_library_id` column missing from `strategies` table | Supabase DB | Same migration |
| BUG-M07 | MEDIUM | Orphan | `StrategyMarketplace.jsx` not reachable from any navigation path | `App.jsx:L1491, L7994` | Add to NAV and PAGES only after real API exists |
| BUG-M08 | MEDIUM | Data | `migrations/005_rls_library_ratings.sql` references missing tables — will fail if applied | `migrations/005:L44` | Create migrations 001-004 first |

---

## 20. Recommendations

### Immediate (Security — Before Any Other Work)

1. **Fix BUG-M01/M02 now**: Add `Depends(get_current_user)` to all 6 stub endpoints or return HTTP 501. The unauthenticated clone endpoint is a live security vulnerability.

2. **Do not add marketplace nav entry** until real API exists. The orphan status currently protects users from fake data.

### Phase 1 — Database Foundation

3. Create and apply: `001_create_library_strategies.sql`, `002_create_library_ratings.sql`, `003_add_library_columns_to_strategies.sql`, `004_rls_library_strategies.sql`
4. Apply existing `005_rls_library_ratings.sql` after base tables created
5. Create `strategy-images` Supabase Storage bucket

### Phase 2 — Backend API

6. Create `backend_app/routers/library.py` with all 8 designed endpoints
7. Create `backend_app/backend/library_metrics_worker.py`
8. Register library router in `backend_app/main.py`
9. Persist `backtest_result` to DB in `POST /api/strategies/backtest`

### Phase 3 — Frontend

10. Create: `LibraryCard`, `LibraryDetailPage`, `PublishModal`, `RatingWidget`, `CloneConfirmModal`
11. Wire `StrategyMarketplace.jsx` to `GET /api/library`
12. Add marketplace to NAV and PAGES

### Phase 4 — Telemetry & Analytics

13. Add `marketplace_events` WebSocket channel
14. Emit `strategy_published`, `strategy_cloned`, `strategy_rated` events
15. Add marketplace metrics to analytics dashboard

---

## 21. Certification Matrix

| Component | Designed | Implemented | Test Method | Result |
|---|---|---|---|---|
| Frontend component file | YES | YES (174 lines, mock) | Code Verified | PARTIAL — MOCK ONLY |
| Frontend nav entry | YES | NO | Code Verified — App.jsx:L1491 | FAIL |
| Frontend routing | YES | NO | Code Verified — App.jsx:L7994 | FAIL |
| Search functionality | YES | NO (input, no handler) | Code Verified | FAIL |
| Filter functionality | YES | NO (state, no filter logic) | Code Verified | FAIL |
| Sort functionality | YES | NO | Code Verified | FAIL |
| `GET /api/library` | YES | NO | Code Verified | FAIL |
| `GET /api/library/{id}` | YES | NO | Code Verified | FAIL |
| `POST /api/library` (publish) | YES | NO | Code Verified | FAIL |
| `DELETE /api/library/{id}` (unpublish) | YES | NO | Code Verified | FAIL |
| `POST /api/library/{id}/clone` | YES | STUB — no auth, hardcoded | Code Verified — L1543 | CRITICAL FAIL |
| `POST /api/library/{id}/rate` | YES | NO | Code Verified | FAIL |
| `GET /api/library/me` | YES | NO | Code Verified | FAIL |
| Admin moderation API | YES | NO | Code Verified | FAIL |
| `library_strategies` table | YES | NO | Runtime Verified | FAIL |
| `library_ratings` table | YES | NO | Runtime Verified | FAIL |
| `source_library_id` on strategies | YES | NO | Runtime Verified | FAIL |
| `backtest_result` on strategies | YES | NO | Runtime Verified | FAIL |
| `strategy_images` table | YES | NO | Runtime Verified | FAIL |
| Supabase Storage bucket | YES | NO | Runtime Verified — empty | FAIL |
| RLS on `library_strategies` | YES | N/A (table missing) | Runtime Verified | FAIL |
| RLS on `library_ratings` | YES | N/A (table missing) | Runtime Verified | FAIL |
| Authentication on clone | YES | NO AUTH | Code Verified | CRITICAL FAIL |
| Tenant isolation (marketplace) | YES | N/A | N/A | FAIL |
| `library_metrics_worker.py` | YES | NOT CREATED | Code Verified | FAIL |
| Clone count aggregation | YES | NO | Code Verified | FAIL |
| Average rating aggregation | YES | NO | Code Verified | FAIL |
| Redis browse caching | YES | NO | Code Verified | FAIL |
| Strategy versioning (DAG schema) | PARTIAL | YES (schema_version in JSONB) | Code Verified — strategies.py:L88 | PASS |
| Security headers | YES | YES | Code Verified — main.py | PASS |
| Marketplace telemetry | YES | NO | Code Verified | FAIL |
| Audit logging | YES | NO | Runtime Verified — no audit_log | FAIL |
| Favorites | Out of scope V1 | NO | N/A | N/A |
| Strategy images | YES | NO | Runtime Verified | FAIL |
| Admin moderation UI | YES | NO | Code Verified | FAIL |
| fn_has_user_cloned() RPC | YES | NO (in unapplied migration) | Code Verified | FAIL |

**Summary:**
- PASS: 2 / 36 (DAG schema versioning, security headers)
- PARTIAL: 1 / 36 (Frontend component exists but mock only)
- FAIL: 33 / 36
- N/A: 1 / 36

---

## 22. Final Verdict

```
╔═══════════════════════════════════════════════════════════════════════════╗
║                                                                           ║
║   FAILED                                                                  ║
║                                                                           ║
║   Sprint 2A Phase 1 — Strategy Marketplace Architecture Certification    ║
║   Certification Date: 2026-07-18                                         ║
║                                                                           ║
║   REASON:                                                                 ║
║   The Strategy Marketplace is not implemented.                            ║
║                                                                           ║
║   - 0 of 8 designed API endpoints implemented                            ║
║   - 0 of 4 required database tables exist in Supabase                    ║
║   - 0 Supabase Storage buckets provisioned                               ║
║   - 0 marketplace telemetry events emitted                               ║
║   - 0 marketplace background workers implemented                         ║
║   - Frontend: 100% hardcoded mock data (MOCK_MARKETPLACE constant)       ║
║   - Frontend: component is unreachable (not in NAV or PAGES)             ║
║   - Clone endpoint: UNAUTHENTICATED — CRITICAL security defect           ║
║   - 5 additional stubs: no authentication                                ║
║                                                                           ║
║   WHAT EXISTS:                                                            ║
║   - Comprehensive design document (STRATEGY_LIBRARY_DESIGN.md)           ║
║   - RLS migration script (005 — unapplicable without base tables)        ║
║   - Strategy CRUD backend (operational — not marketplace)                ║
║   - DAG schema versioning (CompiledDAG class — operational)              ║
║   - Security headers on all routes (operational)                         ║
║                                                                           ║
║   CRITICAL BUG — FIX IMMEDIATELY:                                        ║
║   POST /api/strategies/{id}/clone                                        ║
║   No authentication. Returns hardcoded new_clone_id_123.                 ║
║   Callable by any unauthenticated client right now.                      ║
║                                                                           ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

---

*This document was generated by the Sprint 2A Phase 1 Architecture Certification Suite.*
*All evidence is from direct code inspection, runtime Supabase queries, and live DB verification.*
*No assumptions. No mocks. Every finding sourced to file, line number, or runtime query timestamp.*
