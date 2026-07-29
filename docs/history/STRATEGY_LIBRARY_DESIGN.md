# Strategy Library — Version 1 Design Document

> **Branch target:** `feature/strategy-marketplace-v1`  
> **Status:** Design only — no code modified  
> **Author:** Architecture Review  
> **Date:** 2026-06-22  
> **Depends on:** [PROJECT_ARCHITECTURE.md](./PROJECT_ARCHITECTURE.md)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Scope & V1 Feature Set](#2-scope--v1-feature-set)
3. [System Context Diagram](#3-system-context-diagram)
4. [Database Schema — Supabase](#4-database-schema--supabase)
5. [Database Schema — Alembic / SQLAlchemy](#5-database-schema--alembic--sqlalchemy)
6. [Row-Level Security (RLS) Policies](#6-row-level-security-rls-policies)
7. [API Endpoint Design](#7-api-endpoint-design)
8. [Pydantic Request / Response Schemas](#8-pydantic-request--response-schemas)
9. [Frontend Pages & Components](#9-frontend-pages--components)
10. [Redis Impact & New Key Patterns](#10-redis-impact--new-key-patterns)
11. [Supabase Impact Summary](#11-supabase-impact-summary)
12. [WebSocket Impact & New Channels](#12-websocket-impact--new-channels)
13. [Background Jobs & Aggregation Workers](#13-background-jobs--aggregation-workers)
14. [Access Control & Tier Gating](#14-access-control--tier-gating)
15. [Migration Plan](#15-migration-plan)
16. [Risk & Open Questions](#16-risk--open-questions)
17. [File Map — New Files to Create](#17-file-map--new-files-to-create)

---

## 1. Executive Summary

The **Strategy Library** is the social and discovery layer of the Algo22 platform. It allows users to:

- **Publish** their own strategies to a shared, browsable catalogue
- **Browse** published strategies with live performance metrics, ratings, and filters
- **Clone** any published strategy into their personal builder (one-click fork to editor)
- **Rate** strategies they have run or cloned (1–5 star score with optional review text)

The current `StrategyMarketplace.jsx` is a hardcoded frontend prototype with mock data. This document specifies the **complete V1 backend and frontend work** required to make it fully operational.

### Design Principles

| Principle | Application |
|---|---|
| **RLS-first** | All Strategy Library tables in Supabase carry RLS policies. No data leaks across tenants. |
| **Read-optimised** | Browse is the hot path. Aggregated metrics (copiers, avg_rating) are pre-computed by a background job and cached in Redis, not computed on every request. |
| **Clone is a write on `strategies`** | Cloning creates a new row in the existing `strategies` table owned by the cloning user. The Library just tracks lineage. |
| **No DAG exposure on browse** | The browse API returns metadata only (name, metrics, author alias). Full DAG nodes/edges are returned only after a clone request, preventing strategy theft before fork. |
| **Moderation-ready** | A `moderation_status` column allows admin review before public listing without blocking the V1 release. |
| **Backwards-compatible** | The new `library_strategies` table is a sibling to `strategies`, not a replacement. Existing strategy CRUD is unaffected. |

---

## 2. Scope & V1 Feature Set

### In Scope (V1)

| Feature | Description |
|---|---|
| ✅ **Publish** | User publishes a strategy from their personal `strategies` table to the Library. Creates a snapshot row in `library_strategies`. Strategy must have been backtested (has `backtest_result`). |
| ✅ **Unpublish** | Author can retract a published strategy. Existing clones are unaffected. |
| ✅ **Browse** | Paginated, filterable, searchable catalogue. Filters: category, difficulty, sort by Sharpe / return / clones / rating. |
| ✅ **Strategy Detail** | Full detail page: description, author alias, performance chart (equity curve snapshot), backtest stats, node count, risk params. No DAG exposed here. |
| ✅ **Clone** | Fork a published strategy into the user's own `strategies` table, ready to edit in the Builder. Sets `source_library_id` on the new strategy row for lineage tracking. Increments `clone_count`. |
| ✅ **Rate** | After cloning, user can leave a 1–5 star rating with optional review text. One rating per user per library strategy. |
| ✅ **My Library** | User sees all strategies they have published, their clone counts, avg ratings. |
| ✅ **Admin moderation** | Admin can approve/reject/feature strategies. `moderation_status` field. |

### Out of Scope (V1)

| Feature | Notes |
|---|---|
| ❌ **Copy-trading (live mirroring)** | V2. Strategy Library is discovery only in V1. |
| ❌ **Revenue share / monetisation** | V2. |
| ❌ **Comments / discussion threads** | V2. Use support tickets if needed. |
| ❌ **Follow author / feed** | V2. |
| ❌ **Live P&L aggregation** | V2. V1 shows backtest stats only. |
| ❌ **Strategy versioning** | V2. Clone captures a point-in-time snapshot. |

---

## 3. System Context Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Frontend (algo22-terminal / React)                                              │
│                                                                                 │
│  StrategyMarketplace.jsx (replace mock)   MyLibraryPage.jsx (new)               │
│  StrategyLibraryDetail.jsx (new)          LibraryRatingModal.jsx (new)           │
└──────────────────────────────┬──────────────────────┬───────────────────────────┘
                               │ HTTP                  │ WebSocket
                               ▼                       ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│  FastAPI Backend (backend_app)                                                   │
│                                                                                 │
│  routers/library.py (new)                                                       │
│  ├── GET  /api/library                — browse (paginated)                      │
│  ├── GET  /api/library/{id}           — detail                                  │
│  ├── POST /api/library                — publish                                 │
│  ├── DELETE /api/library/{id}         — unpublish                               │
│  ├── POST /api/library/{id}/clone     — clone → new strategies row              │
│  ├── POST /api/library/{id}/rate      — submit rating                           │
│  ├── GET  /api/library/me             — my published strategies                  │
│  └── PATCH /api/admin/library/{id}   — admin moderation                        │
│                                                                                 │
│  backend/library_metrics_worker.py (new background job)                         │
│  └── Runs every 5 min: recompute clone_count, avg_rating → update Redis cache  │
└───────┬──────────────────────┬────────────────────────────────────────────────┘
        │                      │
        ▼                      ▼
┌───────────────┐   ┌──────────────────────────────────────────────────────────┐
│     Redis     │   │  Supabase PostgreSQL                                      │
│               │   │                                                           │
│ library:      │   │  library_strategies  (new — primary catalogue)           │
│ browse_page:* │   │  library_ratings     (new — one row per user+strategy)   │
│ detail:{id}   │   │  strategies          (existing — clone target)           │
│ author:{id}   │   │  profiles            (existing — author alias)           │
└───────────────┘   └──────────────────────────────────────────────────────────┘
```

---

## 4. Database Schema — Supabase

Both new tables live in Supabase PostgreSQL. They are accessed via the `supabase-py` client with user-scoped JWT (RLS enforced). Migrations are SQL scripts applied through the Supabase dashboard or CLI.

---

### 4.1 Table: `library_strategies`

**Purpose:** The public catalogue. One row per published strategy. This is a **snapshot** of the source strategy at publish time; it does not stay in sync with the original. The author's source strategy may evolve independently.

```sql
CREATE TABLE public.library_strategies (
  -- Identity
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  author_id           UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  source_strategy_id  UUID NOT NULL,               -- The strategies.id it was published from

  -- Metadata (snapshot at publish time)
  name                TEXT NOT NULL,
  description         TEXT,
  category            TEXT NOT NULL,               -- 'mean_reversion' | 'trend_following' | 'market_making' | 'arbitrage' | 'momentum' | 'ml_hybrid' | 'other'
  difficulty          TEXT NOT NULL DEFAULT 'beginner',  -- 'beginner' | 'intermediate' | 'advanced' | 'pro'
  tags                TEXT[] DEFAULT '{}',          -- e.g. ['crypto', 'low_risk', 'ml']
  symbol              TEXT NOT NULL,               -- Primary symbol, e.g. 'BTC/USDT'
  timeframe           TEXT NOT NULL,               -- e.g. '5m'
  exchange_id         TEXT NOT NULL,               -- e.g. 'binance'
  node_count          INTEGER NOT NULL DEFAULT 0,  -- DAG node count (metadata only)
  has_ml_model        BOOLEAN NOT NULL DEFAULT FALSE,

  -- Performance snapshot (from backtest at publish time)
  backtest_total_return_pct   NUMERIC(10, 4),
  backtest_sharpe_ratio       NUMERIC(10, 4),
  backtest_max_drawdown_pct   NUMERIC(10, 4),
  backtest_win_rate_pct       NUMERIC(10, 4),
  backtest_profit_factor      NUMERIC(10, 4),
  backtest_total_trades       INTEGER,
  backtest_start_date         DATE,
  backtest_end_date           DATE,
  backtest_initial_capital    NUMERIC(16, 2),
  equity_curve_snapshot       JSONB,               -- Sampled equity curve [{ts, equity}] for chart display (max 500 points)

  -- Risk params snapshot
  risk_stop_loss_pct          NUMERIC(8, 4),
  risk_take_profit_pct        NUMERIC(8, 4),
  risk_max_position_size      NUMERIC(8, 4),
  risk_max_drawdown_pct       NUMERIC(8, 4),

  -- Aggregated metrics (updated by background worker)
  clone_count         INTEGER NOT NULL DEFAULT 0,
  avg_rating          NUMERIC(3, 2),               -- NULL until first rating
  rating_count        INTEGER NOT NULL DEFAULT 0,

  -- Moderation
  moderation_status   TEXT NOT NULL DEFAULT 'pending',   -- 'pending' | 'approved' | 'rejected' | 'featured'
  moderation_notes    TEXT,
  moderated_by        UUID REFERENCES auth.users(id),
  moderated_at        TIMESTAMPTZ,

  -- Visibility & lifecycle
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,   -- FALSE = unpublished
  is_featured         BOOLEAN NOT NULL DEFAULT FALSE,  -- Admin-promoted
  published_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Constraints
  CONSTRAINT valid_category CHECK (category IN (
    'mean_reversion', 'trend_following', 'market_making',
    'arbitrage', 'momentum', 'ml_hybrid', 'other'
  )),
  CONSTRAINT valid_difficulty CHECK (difficulty IN (
    'beginner', 'intermediate', 'advanced', 'pro'
  )),
  CONSTRAINT valid_moderation_status CHECK (moderation_status IN (
    'pending', 'approved', 'rejected', 'featured'
  )),
  CONSTRAINT valid_avg_rating CHECK (avg_rating IS NULL OR (avg_rating >= 1.0 AND avg_rating <= 5.0))
);

-- Indexes for browse queries
CREATE INDEX idx_library_strategies_author         ON public.library_strategies(author_id);
CREATE INDEX idx_library_strategies_active_approved ON public.library_strategies(is_active, moderation_status)
  WHERE is_active = TRUE AND moderation_status IN ('approved', 'featured');
CREATE INDEX idx_library_strategies_category       ON public.library_strategies(category) WHERE is_active = TRUE;
CREATE INDEX idx_library_strategies_sharpe         ON public.library_strategies(backtest_sharpe_ratio DESC NULLS LAST) WHERE is_active = TRUE;
CREATE INDEX idx_library_strategies_return         ON public.library_strategies(backtest_total_return_pct DESC NULLS LAST) WHERE is_active = TRUE;
CREATE INDEX idx_library_strategies_clones         ON public.library_strategies(clone_count DESC) WHERE is_active = TRUE;
CREATE INDEX idx_library_strategies_rating         ON public.library_strategies(avg_rating DESC NULLS LAST) WHERE is_active = TRUE;
CREATE INDEX idx_library_strategies_featured       ON public.library_strategies(is_featured, published_at DESC) WHERE is_featured = TRUE;
CREATE INDEX idx_library_strategies_published_at   ON public.library_strategies(published_at DESC);
CREATE INDEX idx_library_strategies_tags           ON public.library_strategies USING GIN(tags);

-- Full-text search index
CREATE INDEX idx_library_strategies_fts ON public.library_strategies
  USING GIN (to_tsvector('english', name || ' ' || COALESCE(description, '') || ' ' || COALESCE(array_to_string(tags, ' '), '')));

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_library_strategies_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_library_strategies_updated_at
  BEFORE UPDATE ON public.library_strategies
  FOR EACH ROW EXECUTE FUNCTION update_library_strategies_updated_at();
```

---

### 4.2 Table: `library_ratings`

**Purpose:** One rating row per user per library strategy. Prevents duplicate ratings and enables per-user "have I rated this?" lookups.

```sql
CREATE TABLE public.library_ratings (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  library_id      UUID NOT NULL REFERENCES public.library_strategies(id) ON DELETE CASCADE,
  user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

  -- Rating data
  rating          SMALLINT NOT NULL,               -- 1–5 inclusive
  review_text     TEXT,                            -- Optional, max 500 chars
  is_verified_clone BOOLEAN NOT NULL DEFAULT FALSE, -- TRUE if user actually cloned first

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- One rating per user per strategy
  CONSTRAINT uq_library_ratings_user_library UNIQUE (library_id, user_id),
  CONSTRAINT valid_rating CHECK (rating >= 1 AND rating <= 5),
  CONSTRAINT valid_review_length CHECK (review_text IS NULL OR length(review_text) <= 500)
);

CREATE INDEX idx_library_ratings_library ON public.library_ratings(library_id);
CREATE INDEX idx_library_ratings_user    ON public.library_ratings(user_id);
CREATE INDEX idx_library_ratings_created ON public.library_ratings(library_id, created_at DESC);

CREATE OR REPLACE FUNCTION update_library_ratings_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_library_ratings_updated_at
  BEFORE UPDATE ON public.library_ratings
  FOR EACH ROW EXECUTE FUNCTION update_library_ratings_updated_at();
```

---

### 4.3 Changes to Existing Table: `strategies`

Two new columns are added to the existing `strategies` table to track Library lineage:

```sql
-- Add clone lineage tracking to existing strategies table
ALTER TABLE public.strategies
  ADD COLUMN IF NOT EXISTS source_library_id UUID REFERENCES public.library_strategies(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS backtest_result    JSONB;  -- Store last backtest output for publish eligibility check

-- Index for lineage queries ("who has cloned this library entry?")
CREATE INDEX IF NOT EXISTS idx_strategies_source_library ON public.strategies(source_library_id)
  WHERE source_library_id IS NOT NULL;
```

> **Note:** `backtest_result` resolves the current architecture debt where backtest output is only returned to the client, not persisted. Publishing requires a backtest to have been run, so the result must be stored.

---

## 5. Database Schema — Alembic / SQLAlchemy

The Strategy Library is **Supabase-only** for its primary tables. No new SQLAlchemy models are required for V1.

However, one Alembic migration is needed to add `source_library_id` and `backtest_result` to the local PostgreSQL mirror used in non-Supabase environments (CI, on-prem deployments):

```python
# File: backend_app/alembic/versions/XXXX_add_strategy_library_columns.py
"""Add source_library_id and backtest_result to strategies

Revision ID: XXXX
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

def upgrade():
    op.add_column('strategies', sa.Column(
        'source_library_id', UUID(as_uuid=True), nullable=True,
        comment='FK to library_strategies.id if strategy was cloned from the Library'
    ))
    op.add_column('strategies', sa.Column(
        'backtest_result', JSONB, nullable=True,
        comment='Last backtest output blob, required for Library publishing'
    ))
    op.create_index(
        'idx_strategies_source_library',
        'strategies',
        ['source_library_id'],
        postgresql_where=sa.text('source_library_id IS NOT NULL')
    )

def downgrade():
    op.drop_index('idx_strategies_source_library', table_name='strategies')
    op.drop_column('strategies', 'backtest_result')
    op.drop_column('strategies', 'source_library_id')
```

---

## 6. Row-Level Security (RLS) Policies

All new tables have RLS **enabled by default**. Service role key bypasses are restricted to admin moderation and background metric workers only.

### `library_strategies` RLS

```sql
ALTER TABLE public.library_strategies ENABLE ROW LEVEL SECURITY;

-- SELECT: Anyone can read approved/featured active strategies (public catalogue)
CREATE POLICY "library_strategies_select_public"
  ON public.library_strategies FOR SELECT
  USING (
    is_active = TRUE
    AND moderation_status IN ('approved', 'featured')
  );

-- SELECT (author): Author can see all their own strategies regardless of status
CREATE POLICY "library_strategies_select_own"
  ON public.library_strategies FOR SELECT
  USING (author_id = auth.uid());

-- INSERT: Authenticated users can publish (further validated in API layer)
CREATE POLICY "library_strategies_insert_own"
  ON public.library_strategies FOR INSERT
  WITH CHECK (author_id = auth.uid());

-- UPDATE: Author can update description/tags/metadata only (not metrics or moderation)
-- Metrics and moderation fields updated only via service role (background worker)
CREATE POLICY "library_strategies_update_own"
  ON public.library_strategies FOR UPDATE
  USING (author_id = auth.uid())
  WITH CHECK (author_id = auth.uid());

-- DELETE: Author can delete (sets is_active = FALSE in API; hard delete not recommended)
-- No direct DELETE policy — soft delete via UPDATE is_active=FALSE in API layer
```

### `library_ratings` RLS

```sql
ALTER TABLE public.library_ratings ENABLE ROW LEVEL SECURITY;

-- SELECT: All ratings visible to all authenticated users
CREATE POLICY "library_ratings_select_all"
  ON public.library_ratings FOR SELECT
  TO authenticated
  USING (TRUE);

-- INSERT: Authenticated users can rate, one per strategy (enforced by UNIQUE constraint)
CREATE POLICY "library_ratings_insert_own"
  ON public.library_ratings FOR INSERT
  TO authenticated
  WITH CHECK (user_id = auth.uid());

-- UPDATE: Users can update their own rating
CREATE POLICY "library_ratings_update_own"
  ON public.library_ratings FOR UPDATE
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- DELETE: Users can delete their own rating
CREATE POLICY "library_ratings_delete_own"
  ON public.library_ratings FOR DELETE
  USING (user_id = auth.uid());
```

---

## 7. API Endpoint Design

### New Router: `backend_app/routers/library.py`

Mounted in `main.py` as:
```python
app.include_router(library_router, prefix="/api/library", tags=["Strategy Library"])
```

---

### 7.1 Browse Catalogue

```
GET /api/library
```

**Auth:** None required (public endpoint)  
**Rate limit:** 30 req/min (unauthenticated), 120 req/min (authenticated)

**Query Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | Page number (1-indexed) |
| `limit` | int | 20 | Items per page (max 50) |
| `sort` | str | `clones` | `clones` / `rating` / `sharpe` / `return` / `newest` / `featured` |
| `category` | str | — | Filter by category slug |
| `difficulty` | str | — | Filter by difficulty |
| `has_ml` | bool | — | Filter ML strategies only |
| `min_sharpe` | float | — | Minimum Sharpe ratio filter |
| `min_return` | float | — | Minimum total return % filter |
| `tags` | str | — | Comma-separated tag filter (OR logic) |
| `q` | str | — | Full-text search (name, description, tags) |

**Response body:**
```json
{
  "items": [
    {
      "id": "uuid",
      "name": "RSI Mean Reversion Alpha",
      "author_alias": "QuantLabs",
      "category": "mean_reversion",
      "difficulty": "beginner",
      "tags": ["crypto", "low_risk"],
      "symbol": "BTC/USDT",
      "timeframe": "5m",
      "node_count": 6,
      "has_ml_model": false,
      "backtest_sharpe_ratio": 2.1,
      "backtest_total_return_pct": 14.5,
      "backtest_max_drawdown_pct": -8.2,
      "backtest_win_rate_pct": 61.3,
      "backtest_total_trades": 142,
      "clone_count": 12450,
      "avg_rating": 4.3,
      "rating_count": 891,
      "is_featured": false,
      "published_at": "2026-01-15T10:00:00Z",
      "user_has_cloned": false,       // present only when authenticated
      "user_rating": null             // present only when authenticated
    }
  ],
  "total": 847,
  "page": 1,
  "limit": 20,
  "pages": 43
}
```

**Caching:** Response cached in Redis at `library:browse:{hash(params)}` for **60 seconds**.

---

### 7.2 Strategy Detail

```
GET /api/library/{library_id}
```

**Auth:** None required (public); user context enriches `user_has_cloned` / `user_rating` if authenticated  
**Rate limit:** 60 req/min

**Response body:**
```json
{
  "id": "uuid",
  "name": "RSI Mean Reversion Alpha",
  "description": "Uses RSI divergence with EMA confirmation...",
  "author_alias": "QuantLabs",
  "category": "mean_reversion",
  "difficulty": "beginner",
  "tags": ["crypto", "low_risk"],
  "symbol": "BTC/USDT",
  "timeframe": "5m",
  "exchange_id": "binance",
  "node_count": 6,
  "has_ml_model": false,
  "backtest_total_return_pct": 14.5,
  "backtest_sharpe_ratio": 2.1,
  "backtest_max_drawdown_pct": -8.2,
  "backtest_win_rate_pct": 61.3,
  "backtest_profit_factor": 1.87,
  "backtest_total_trades": 142,
  "backtest_start_date": "2024-01-01",
  "backtest_end_date": "2025-12-31",
  "backtest_initial_capital": 10000,
  "equity_curve_snapshot": [{"ts": "2024-01-01T00:00:00Z", "equity": 10000.0}, ...],
  "risk_stop_loss_pct": 0.05,
  "risk_take_profit_pct": 0.15,
  "risk_max_position_size": 0.10,
  "risk_max_drawdown_pct": 0.20,
  "clone_count": 12450,
  "avg_rating": 4.3,
  "rating_count": 891,
  "recent_ratings": [
    {
      "rating": 5,
      "review_text": "Clean logic, works great on 4h too.",
      "created_at": "2026-06-10T08:00:00Z"
    }
  ],
  "is_featured": false,
  "published_at": "2026-01-15T10:00:00Z",
  "user_has_cloned": false,
  "user_rating": null
}
```

**Note:** DAG nodes and edges are **NOT** returned in browse or detail. They are transferred only during the clone operation.

**Caching:** Cached at `library:detail:{library_id}` for **120 seconds**. Invalidated on unpublish, admin status change, or metric update.

---

### 7.3 Publish Strategy

```
POST /api/library
```

**Auth:** Required (`get_current_user`)  
**Rate limit:** 5 publishes per day per user (enforced via Redis counter)

**Request body:**
```json
{
  "strategy_id": "uuid",
  "description": "A clean RSI mean reversion system...",
  "category": "mean_reversion",
  "difficulty": "beginner",
  "tags": ["crypto", "low_risk"]
}
```

**Validation rules (API layer, not just DB):**

| Rule | Rejection message |
|---|---|
| `strategy_id` belongs to calling user | 403 — Not your strategy |
| Strategy has `backtest_result` stored | 400 — Run a backtest before publishing |
| Strategy has ≥1 action node (via `dag_hash` + stored nodes) | 400 — Strategy DAG has no action nodes |
| Strategy not already published (check existing `library_strategies` where `source_strategy_id = strategy_id AND is_active = TRUE`) | 409 — Already published |
| User tier is `free` → limited to 1 published strategy | 403 — Upgrade to Pro to publish more |
| User tier is `pro_999` → up to 5 published strategies | (same) |
| User tier is `elite_1999` → unlimited | — |

**Processing flow:**
1. Validate all rules above
2. Load strategy from `strategies` table (verify ownership)
3. Extract equity curve snapshot (sample to max 500 points)
4. Insert row into `library_strategies` with `moderation_status = 'pending'`
5. Return library entry ID and `pending` status
6. Enqueue admin notification (webhook or internal flag)

**Response:**
```json
{
  "library_id": "uuid",
  "moderation_status": "pending",
  "message": "Strategy submitted for review. It will appear publicly once approved."
}
```

> **V1 simplification:** All publishes go to `pending` first. Admin approves. An auto-approve flag can be set per-environment for dev/staging.

---

### 7.4 Unpublish Strategy

```
DELETE /api/library/{library_id}
```

**Auth:** Required — must be the author  
**Effect:** Sets `is_active = FALSE`, does not hard-delete. Existing clones are unaffected.

**Response:**
```json
{"message": "Strategy unpublished. Existing clones are unaffected."}
```

**Side-effects:** Invalidates `library:detail:{library_id}` and `library:browse:*` in Redis.

---

### 7.5 Clone Strategy

```
POST /api/library/{library_id}/clone
```

**Auth:** Required (`get_current_user`)  
**Rate limit:** 20 clones per hour per user

**Request body:** Empty `{}`

**Validation:**
- Library strategy must be `is_active = TRUE` and `moderation_status IN ('approved', 'featured')`
- User cannot clone their own published strategy
- User cannot clone the same library strategy twice (idempotent: return existing clone ID)

**Processing flow:**
1. Fetch `library_strategies` row (public read — no RLS issue)
2. Fetch full strategy DAG from source user's `strategies` table using `source_strategy_id`
   - This read uses **service role** since the DAG belongs to another user
   - The service role read is scoped only to the `source_strategy_id` column set, not all strategies
3. Create new row in `strategies` table owned by `auth.uid()`:
   - Copies: `name`, `symbol`, `timeframe`, `exchange_id`, `buy_logic` (with DAG), `sell_logic`, `risk`, `indicators`, `ml_model_path`
   - Sets: `user_id = auth.uid()`, `status = 'stopped'`, `source_library_id = library_id`
   - Name prefix: `"[Clone] {original_name}"`
4. Atomically increment `clone_count` in `library_strategies` (via `UPDATE ... SET clone_count = clone_count + 1`)
5. Record clone in `library_ratings` table as `is_verified_clone = TRUE` (rating row created but rating=NULL; allows the user to later fill in the rating)
6. Publish `library:clone:{library_id}` event to Redis Pub/Sub (for WS real-time counter)

**Response:**
```json
{
  "new_strategy_id": "uuid",
  "message": "Strategy cloned into your builder. Ready to customise."
}
```

---

### 7.6 Rate a Strategy

```
POST /api/library/{library_id}/rate
```

**Auth:** Required  
**Constraint:** Only users who have cloned the strategy may rate it (`is_verified_clone = TRUE`)

**Request body:**
```json
{
  "rating": 4,
  "review_text": "Solid Sharpe, slightly aggressive stop-loss but good overall."
}
```

**Validation:**
- `rating` must be 1–5
- `review_text` max 500 characters
- User must have cloned (check `library_ratings` for `user_id` + `library_id` where `is_verified_clone = TRUE`)
- If a rating row already exists (from update), do upsert

**Processing flow:**
1. Validate user cloned the strategy
2. Upsert `library_ratings` row
3. Enqueue metric recomputation job (async — does not block response)
4. Invalidate `library:detail:{library_id}` in Redis

**Response:**
```json
{
  "library_id": "uuid",
  "rating": 4,
  "review_text": "Solid Sharpe...",
  "message": "Rating submitted."
}
```

---

### 7.7 My Published Strategies

```
GET /api/library/me
```

**Auth:** Required  
**Returns:** All library entries where `author_id = user_id`, regardless of moderation status.

**Response:**
```json
{
  "items": [
    {
      "id": "uuid",
      "name": "RSI Mean Reversion Alpha",
      "moderation_status": "approved",
      "clone_count": 12450,
      "avg_rating": 4.3,
      "rating_count": 891,
      "is_active": true,
      "published_at": "2026-01-15T10:00:00Z"
    }
  ],
  "total": 2
}
```

---

### 7.8 Admin: Moderate Strategy

```
PATCH /api/admin/library/{library_id}
```

**Auth:** Required — `get_admin_user()` (app_metadata.role == "admin")

**Request body:**
```json
{
  "moderation_status": "approved",
  "is_featured": false,
  "moderation_notes": "Verified backtest; approved."
}
```

**Side-effects:** Invalidates `library:detail:{library_id}` and `library:browse:*` in Redis. Publishes WS event if status becomes `approved` or `featured`.

---

### 7.9 Admin: Get All Pending Strategies

```
GET /api/admin/library/pending
```

**Auth:** Required — `get_admin_user()`  
**Returns:** All strategies with `moderation_status = 'pending'`, ordered by `published_at ASC`.

---

### Full Endpoint Summary Table

| Method | Path | Auth | Cache | Description |
|---|---|---|---|---|
| GET | `/api/library` | Optional | Redis 60s | Browse catalogue (paginated, filterable) |
| GET | `/api/library/{id}` | Optional | Redis 120s | Strategy detail (metrics + equity curve) |
| POST | `/api/library` | Required | Invalidates browse | Publish a strategy |
| DELETE | `/api/library/{id}` | Required (author) | Invalidates detail+browse | Unpublish |
| POST | `/api/library/{id}/clone` | Required | — | Clone into user's builder |
| POST | `/api/library/{id}/rate` | Required | Invalidates detail | Submit/update rating |
| GET | `/api/library/me` | Required | — | My published strategies |
| PATCH | `/api/admin/library/{id}` | Admin | Invalidates detail+browse | Moderate |
| GET | `/api/admin/library/pending` | Admin | — | Pending moderation queue |

---

## 8. Pydantic Request / Response Schemas

### New file: `backend_app/core/models/library_schemas.py`

```python
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Literal
from datetime import date, datetime
from uuid import UUID


# ─── Enums ──────────────────────────────────────────────────────────────────

LibraryCategory = Literal[
    "mean_reversion", "trend_following", "market_making",
    "arbitrage", "momentum", "ml_hybrid", "other"
]

LibraryDifficulty = Literal["beginner", "intermediate", "advanced", "pro"]

LibrarySort = Literal["clones", "rating", "sharpe", "return", "newest", "featured"]

ModerationStatus = Literal["pending", "approved", "rejected", "featured"]


# ─── Browse ─────────────────────────────────────────────────────────────────

class LibraryBrowseQuery(BaseModel):
    page: int = Field(1, ge=1)
    limit: int = Field(20, ge=1, le=50)
    sort: LibrarySort = "clones"
    category: Optional[LibraryCategory] = None
    difficulty: Optional[LibraryDifficulty] = None
    has_ml: Optional[bool] = None
    min_sharpe: Optional[float] = None
    min_return: Optional[float] = None
    tags: Optional[str] = None       # comma-separated
    q: Optional[str] = None          # full-text search


class LibraryCardResponse(BaseModel):
    id: UUID
    name: str
    author_alias: str
    category: str
    difficulty: str
    tags: List[str]
    symbol: str
    timeframe: str
    node_count: int
    has_ml_model: bool
    backtest_sharpe_ratio: Optional[float]
    backtest_total_return_pct: Optional[float]
    backtest_max_drawdown_pct: Optional[float]
    backtest_win_rate_pct: Optional[float]
    backtest_total_trades: Optional[int]
    clone_count: int
    avg_rating: Optional[float]
    rating_count: int
    is_featured: bool
    published_at: datetime
    user_has_cloned: Optional[bool] = None   # enriched when authenticated
    user_rating: Optional[int] = None        # enriched when authenticated


class LibraryBrowseResponse(BaseModel):
    items: List[LibraryCardResponse]
    total: int
    page: int
    limit: int
    pages: int


# ─── Detail ─────────────────────────────────────────────────────────────────

class EquityCurvePoint(BaseModel):
    ts: str
    equity: float


class RecentRating(BaseModel):
    rating: int
    review_text: Optional[str]
    created_at: datetime


class LibraryDetailResponse(LibraryCardResponse):
    description: Optional[str]
    exchange_id: str
    backtest_profit_factor: Optional[float]
    backtest_start_date: Optional[date]
    backtest_end_date: Optional[date]
    backtest_initial_capital: Optional[float]
    equity_curve_snapshot: Optional[List[EquityCurvePoint]]
    risk_stop_loss_pct: Optional[float]
    risk_take_profit_pct: Optional[float]
    risk_max_position_size: Optional[float]
    risk_max_drawdown_pct: Optional[float]
    recent_ratings: List[RecentRating] = []


# ─── Publish ─────────────────────────────────────────────────────────────────

class PublishStrategyRequest(BaseModel):
    strategy_id: UUID
    description: Optional[str] = Field(None, max_length=2000)
    category: LibraryCategory
    difficulty: LibraryDifficulty
    tags: List[str] = Field(default_factory=list, max_items=8)

    @validator("tags", each_item=True)
    def validate_tag(cls, v):
        v = v.lower().strip()
        if len(v) > 30 or not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"Invalid tag: '{v}'. Use alphanumeric, hyphens, underscores only.")
        return v


class PublishStrategyResponse(BaseModel):
    library_id: UUID
    moderation_status: ModerationStatus
    message: str


# ─── Clone ──────────────────────────────────────────────────────────────────

class CloneStrategyResponse(BaseModel):
    new_strategy_id: UUID
    message: str


# ─── Rating ─────────────────────────────────────────────────────────────────

class SubmitRatingRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    review_text: Optional[str] = Field(None, max_length=500)


class SubmitRatingResponse(BaseModel):
    library_id: UUID
    rating: int
    review_text: Optional[str]
    message: str


# ─── My Library ─────────────────────────────────────────────────────────────

class MyLibraryEntry(BaseModel):
    id: UUID
    name: str
    moderation_status: ModerationStatus
    clone_count: int
    avg_rating: Optional[float]
    rating_count: int
    is_active: bool
    published_at: datetime


class MyLibraryResponse(BaseModel):
    items: List[MyLibraryEntry]
    total: int


# ─── Admin ──────────────────────────────────────────────────────────────────

class AdminModerateRequest(BaseModel):
    moderation_status: ModerationStatus
    is_featured: Optional[bool] = None
    moderation_notes: Optional[str] = Field(None, max_length=1000)
```

---

## 9. Frontend Pages & Components

### 9.1 Replaces / Updates

| Current file | Action | Reason |
|---|---|---|
| `algo22-terminal/src/pages/StrategyMarketplace.jsx` | **Rewrite** | Replace mock data with real API calls |

### 9.2 New Files

| File | Purpose |
|---|---|
| `algo22-terminal/src/pages/StrategyLibraryDetail.jsx` | Full detail page for a single library strategy |
| `algo22-terminal/src/pages/MyLibraryPage.jsx` | Author's own published strategies management |
| `algo22-terminal/src/components/LibraryPublishModal.jsx` | Publish wizard (description, category, tags) |
| `algo22-terminal/src/components/LibraryRatingModal.jsx` | Star rating + review text submission |
| `algo22-terminal/src/components/LibraryStrategyCard.jsx` | Reusable strategy card component |
| `algo22-terminal/src/components/LibraryEquityChart.jsx` | Equity curve sparkline/chart |
| `algo22-terminal/src/components/LibraryFilterBar.jsx` | Search + filter controls |
| `algo22-terminal/src/components/LibraryModerationBadge.jsx` | Pending / Approved / Rejected badge |
| `algo22-terminal/src/hooks/useLibrary.js` | React hook for library API calls |
| `algo22-terminal/src/hooks/useLibraryDetail.js` | React hook for detail + rating state |

---

### 9.3 Updated Route Table

| Route Key | Component | Description |
|---|---|---|
| `marketplace` | `StrategyMarketplace.jsx` (rewritten) | Live Library browse — replaces mock |
| `library_detail` | `StrategyLibraryDetail.jsx` (new) | Detail page for one library entry |
| `my_library` | `MyLibraryPage.jsx` (new) | Author view: manage published strategies |

---

### 9.4 StrategyMarketplace.jsx — Rewrite Specification

**What stays:**
- Overall layout, dark theme, filter tabs visual design
- `uiMode` beginner/expert adaptation
- Navigation via `go()` function

**What changes:**
- Remove `MOCK_MARKETPLACE` constant entirely
- Add `useLibrary` hook call on mount: `GET /api/library`
- Add real search input (debounced 300ms → API call with `?q=`)
- Filter tabs become real query param mutations: `?sort=clones`, `?sort=rating`, etc.
- Category filter row added below tabs
- Clone button calls `POST /api/library/{id}/clone` → on success, call existing `setResumeBuilderStrategy` with real nodes from clone response + `go('builder')`
- Clicking a card → `go('library_detail', { id })` 
- Add `LibraryStrategyCard` as a shared component
- Add loading skeleton states
- Add empty state for no results
- Pagination controls (prev/next) at bottom

---

### 9.5 StrategyLibraryDetail.jsx — Specification

**Sections:**

1. **Header** — Name, author alias, category badge, difficulty badge, tags, published date
2. **Performance Panel** — 4 stat boxes: Total Return, Sharpe Ratio, Max Drawdown, Win Rate
3. **Equity Curve** — `LibraryEquityChart` renders the `equity_curve_snapshot` (sampled line chart)
4. **Trade Stats** — Total trades, Profit Factor, Backtest period, Initial capital
5. **Risk Parameters** — Stop-loss %, Take-profit %, Max position size, Max drawdown
6. **Node Graph Summary** — Node count, Has ML model badge, Symbol, Timeframe, Exchange
7. **Ratings Section** — Avg stars (visual), rating count, recent 5 reviews
8. **Action Bar** — "Clone to Builder" button (primary), "Rate this Strategy" button (if user has cloned)

---

### 9.6 MyLibraryPage.jsx — Specification

**Sections:**

1. **Stats row** — Total published, Total clones across all, Avg rating
2. **Published strategies table** — Name, Moderation status badge, Clone count, Avg rating, Published date, Unpublish button
3. **Publish CTA** — "Publish a Strategy" button → opens `LibraryPublishModal`
4. **Publish eligibility notice** — Shows tier limits (free=1, pro=5, elite=∞)

---

### 9.7 LibraryPublishModal.jsx — Specification

**Step 1 — Select Strategy:**
- Dropdown of user's existing strategies (from `GET /api/strategies/`)
- Shows backtest eligibility: ✅ if `backtest_result` exists, ❌ if not (with link to run backtest first)

**Step 2 — Describe:**
- Name (pre-filled from strategy name, editable)
- Description (rich text or plain text, 2000 chars max)
- Category select
- Difficulty select
- Tags (chip input, max 8)

**Step 3 — Review & Submit:**
- Preview card showing how it will appear in browse
- Submit button → `POST /api/library`
- On success: toast "Submitted for review" + redirect to `my_library`

---

### 9.8 LibraryRatingModal.jsx — Specification

- Triggered from detail page Action Bar
- 5-star interactive selector (click to select)
- Optional textarea (max 500 chars)
- Submit → `POST /api/library/{id}/rate`
- On success: toast + update detail page stats (re-fetch detail)

---

### 9.9 apiClient.js — New Methods

Add the following functions to the existing `apiClient.js`:

```javascript
// Browse the library
async browseLibrary(params = {}) { ... }           // GET /api/library

// Get strategy detail
async getLibraryDetail(libraryId) { ... }          // GET /api/library/{id}

// Publish a strategy
async publishStrategy(payload) { ... }             // POST /api/library

// Unpublish a strategy
async unpublishStrategy(libraryId) { ... }         // DELETE /api/library/{id}

// Clone a library strategy
async cloneLibraryStrategy(libraryId) { ... }      // POST /api/library/{id}/clone

// Rate a strategy
async rateLibraryStrategy(libraryId, payload) { ...} // POST /api/library/{id}/rate

// Get my published strategies
async getMyLibrary() { ... }                       // GET /api/library/me
```

---

## 10. Redis Impact & New Key Patterns

### New Keys

| Key Pattern | TTL | Populated by | Invalidated by |
|---|---|---|---|
| `library:browse:{param_hash}` | 60 s | API handler on cache miss | Unpublish, admin moderate, metric worker |
| `library:detail:{library_id}` | 120 s | API handler on cache miss | Unpublish, rate submit, admin moderate, metric worker |
| `library:author_alias:{user_id}` | 3600 s | First publish / profile lookup | Profile update |
| `library:clone_count:{library_id}` | 300 s | Metric worker | Clone event, metric worker |
| `library:rating:{library_id}` | 300 s | Metric worker | Rating submit, metric worker |
| `library:publish_count:{user_id}` | 86400 s | Publish endpoint | Unpublish event |
| `library:publish_rate:{user_id}` | 86400 s | Publish endpoint | Rolling window counter (rate limiting) |
| `library:clone_rate:{user_id}` | 3600 s | Clone endpoint | Rolling window counter (rate limiting) |

### Author Alias Resolution

The browse API returns `author_alias` but must not expose `author_id` or real user emails. The alias is a display name fetched from `profiles.display_name` (or first part of email as fallback). This is cached at `library:author_alias:{user_id}` for 1 hour.

### Cache Invalidation Strategy

| Event | Keys to Invalidate |
|---|---|
| Strategy published | `library:browse:*` (SCAN + DEL pattern) |
| Strategy unpublished | `library:detail:{id}`, `library:browse:*` |
| Clone completed | `library:clone_count:{id}`, `library:detail:{id}` |
| Rating submitted | `library:rating:{id}`, `library:detail:{id}` |
| Admin moderation | `library:detail:{id}`, `library:browse:*` |
| Metric worker run | `library:browse:*` (bulk invalidation) |

> **Implementation note:** Use `SCAN 0 MATCH library:browse:* COUNT 100` + `DEL` for pattern invalidation. Do NOT use `KEYS *` (blocks Redis). The `RedisManager` pattern in `core/cache.py` already supports this via `scan_iter`.

### Clone Count Real-Time Update

The clone operation publishes to Redis Pub/Sub channel `library_events`:
```json
{
  "event": "clone",
  "library_id": "uuid",
  "new_clone_count": 12451,
  "ts": 1750000000
}
```
This feeds the WebSocket broadcast (see §12).

---

## 11. Supabase Impact Summary

### New Tables

| Table | Row size estimate | Notes |
|---|---|---|
| `library_strategies` | ~2–5 KB per row (equity curve is sampled) | Expected: ~10K rows at launch |
| `library_ratings` | ~200 bytes per row | Expected: ~100K rows at maturity |

### Modified Tables

| Table | Change | Migration type |
|---|---|---|
| `strategies` | +`source_library_id UUID`, +`backtest_result JSONB` | Non-breaking `ALTER TABLE ADD COLUMN` |

### New Indexes

12 new indexes across `library_strategies` and `library_ratings` (listed in §4). These are standard B-tree and GIN indexes — no extension requirements beyond `pg_trgm` for full-text search (already enabled by Supabase by default).

### Supabase Edge Function Candidates (Optional V1)

These can be Supabase Edge Functions in V2 but are API handlers in V1:
- Auto-approve moderation for trusted users (by follower count or account age)
- Notify author when their strategy gets its 100th clone

### Supabase Realtime (Not Used in V1)

Supabase Realtime (PostgreSQL logical replication → WebSocket) is not used for library events. Instead, the FastAPI WebSocket layer handles real-time clone count updates (§12), giving more control over message format and auth.

---

## 12. WebSocket Impact & New Channels

### New WebSocket Subscription Type: `library`

Added to `api_ws/ws_manager.py` subscription map:
```
"library" → { library_id → set[WebSocket] }
```

### New WebSocket Endpoint

```
WS /ws/library/{library_id}
```

**Auth:** None (public — clone count is public data)  
**Direction:** Server → Client only  
**Purpose:** Real-time clone count and rating updates for the detail page

#### Message Format

```json
// Sent when clone count changes
{
  "type": "library_clone",
  "library_id": "uuid",
  "clone_count": 12451,
  "ts": 1750000000
}

// Sent when a new rating is submitted
{
  "type": "library_rating",
  "library_id": "uuid",
  "avg_rating": 4.31,
  "rating_count": 892,
  "ts": 1750000000
}
```

#### Server Flow

```
POST /api/library/{id}/clone
    └─► DB: increment clone_count
    └─► Redis PUBLISH library_events: {event: "clone", library_id, new_clone_count}
            └─► Library WS subscriber task reads channel
                    └─► ws_manager.broadcast("library", library_id, {type: "library_clone", ...})
                            └─► All connected detail page clients get real-time update
```

#### Frontend Integration

`StrategyLibraryDetail.jsx` connects to `WS /ws/library/{id}` on mount and disconnects on unmount. Received messages update the local clone count / rating display without requiring a full page reload.

### Changes to Existing WS Infrastructure

- `ws_manager.py`: Add `"library"` key to subscription map
- `ws_routes.py`: Add `ws_library()` handler
- Background Redis subscription task for `library_events` channel (runs as a lifespan task alongside existing ones)

### Heartbeat on Library WS

Inherits the existing 10s ping / 30s pong timeout from `ws_routes.py`. No special handling needed.

---

## 13. Background Jobs & Aggregation Workers

### New File: `backend_app/backend/library_metrics_worker.py`

**Purpose:** Recompute `clone_count`, `avg_rating`, `rating_count` in `library_strategies` and refresh Redis caches. Runs as a background asyncio task in the FastAPI lifespan.

**Interval:** Every 5 minutes (300 seconds)

**Logic per run:**

```
1. Query Supabase (service role):
   SELECT library_id, COUNT(*) as clone_count
   FROM strategies
   WHERE source_library_id IS NOT NULL
   GROUP BY library_id

2. Query Supabase (service role):
   SELECT library_id,
          AVG(rating)::numeric(3,2) as avg_rating,
          COUNT(*) as rating_count
   FROM library_ratings
   GROUP BY library_id

3. For each library_id with changed metrics:
   a. UPDATE library_strategies SET clone_count=X, avg_rating=Y, rating_count=Z
   b. Invalidate Redis: DEL library:detail:{id}
   c. Publish WS event if delta > 0

4. Invalidate Redis: SCAN + DEL library:browse:*
```

**Error handling:** Worker failures are logged to Sentry but do not crash the API. Stale aggregates up to 5 minutes old are acceptable for V1.

**Registration in `main.py` lifespan:**
```python
asyncio.create_task(library_metrics_worker.run_forever(interval_seconds=300))
```

---

## 14. Access Control & Tier Gating

### Publish Limits by Tier

| Tier | Max Published Strategies | Notes |
|---|---|---|
| `free` | 1 | One strategy to drive upgrade conversion |
| `pro_999` | 5 | |
| `elite_1999` | Unlimited | |

**Enforcement:** `check_library_publish_limit()` dependency — mirrors `check_deployment_limit()` pattern from `core/dependencies.py`. Reads cached Supabase profile, counts active library entries via `GET library_strategies WHERE author_id = X AND is_active = TRUE`.

### Clone Limits

No tier restriction on cloning. All authenticated users can clone unlimited strategies in V1.

### Browse Access

Browse is **fully public** — no authentication required. This maximises discoverability and SEO.

### Rating Access

Rating requires:
1. Authentication
2. Verified clone (`is_verified_clone = TRUE` in `library_ratings`)

### Admin Moderation Access

`get_admin_user()` — requires `app_metadata.role == "admin"` in JWT (existing pattern, no change).

### `is_frozen` Check

The `is_frozen` check in `get_current_user()` applies to Publish, Clone, and Rate. Frozen users cannot interact with the Library.

---

## 15. Migration Plan

### Phase 0 — Prerequisites (Before Any Code)

| Task | Owner | Notes |
|---|---|---|
| Decide on auto-approve vs. manual review policy | Product | Affects `moderation_status` default |
| Set `display_name` field in `profiles` table (or confirm fallback) | Backend | Needed for `author_alias` |
| Confirm `backtest_result` JSON schema with backend team | Backend | Defines what gets stored in strategies.backtest_result |
| Set `STRATEGY_LIBRARY_ENABLED` feature flag to `false` in prod | DevOps | Gate the feature until fully tested |

---

### Phase 1 — Database (Supabase — No Code Changes)

Apply in order through Supabase SQL editor or migration CLI:

```
Step 1.1  Create library_strategies table (§4.1 SQL)
Step 1.2  Create library_ratings table (§4.2 SQL)
Step 1.3  ALTER TABLE strategies ADD COLUMN source_library_id, backtest_result (§4.3 SQL)
Step 1.4  Apply all RLS policies (§6 SQL)
Step 1.5  Verify RLS in Supabase table editor — confirm public cannot see pending/rejected rows
Step 1.6  Apply Alembic migration for local dev environments (§5)
```

**Rollback:** All `ALTER TABLE ADD COLUMN` statements are non-breaking and rollback is a `DROP COLUMN`. Table creation rollback is `DROP TABLE`. No data loss possible at this phase.

---

### Phase 2 — Backend: Core Router & Schemas

Files to create/modify (no code change directive in effect — this is the spec for when implementation begins):

```
[CREATE] backend_app/routers/library.py
[CREATE] backend_app/core/models/library_schemas.py
[MODIFY] backend_app/main.py              ← include_router(library_router)
[MODIFY] backend_app/core/dependencies.py ← add check_library_publish_limit()
[MODIFY] backend_app/routers/strategies.py ← persist backtest_result after backtest
```

**Testing gate:** All library endpoints must return correct data and respect RLS before Phase 3.

---

### Phase 3 — Backend: Background Worker & WebSocket

```
[CREATE] backend_app/backend/library_metrics_worker.py
[MODIFY] backend_app/api_ws/ws_routes.py      ← add ws_library() handler
[MODIFY] backend_app/api_ws/ws_manager.py     ← add "library" subscription type
[MODIFY] backend_app/main.py                  ← register metrics worker in lifespan
```

---

### Phase 4 — Frontend: Rewrite Marketplace + New Pages

```
[MODIFY] algo22-terminal/src/pages/StrategyMarketplace.jsx     ← replace mock data
[CREATE] algo22-terminal/src/pages/StrategyLibraryDetail.jsx
[CREATE] algo22-terminal/src/pages/MyLibraryPage.jsx
[CREATE] algo22-terminal/src/components/LibraryPublishModal.jsx
[CREATE] algo22-terminal/src/components/LibraryRatingModal.jsx
[CREATE] algo22-terminal/src/components/LibraryStrategyCard.jsx
[CREATE] algo22-terminal/src/components/LibraryEquityChart.jsx
[CREATE] algo22-terminal/src/components/LibraryFilterBar.jsx
[CREATE] algo22-terminal/src/components/LibraryModerationBadge.jsx
[CREATE] algo22-terminal/src/hooks/useLibrary.js
[CREATE] algo22-terminal/src/hooks/useLibraryDetail.js
[MODIFY] algo22-terminal/src/apiClient.js     ← add library API methods
[MODIFY] algo22-terminal/src/App.jsx          ← add library_detail and my_library routes
```

---

### Phase 5 — Admin UI (Minimal V1)

A temporary admin moderation queue accessible via:
- `GET /api/admin/library/pending` → displayed in an admin-only view (can be a simple table inside `settings` page guarded by admin role check)
- `PATCH /api/admin/library/{id}` → approve / reject button

A full admin moderation UI is V2. V1 admin can use the API directly or a minimal inline table.

---

### Phase 6 — Feature Flag Enable & Staged Rollout

```
Day 0:  Enable STRATEGY_LIBRARY_ENABLED=true in staging
Day 1:  QA testing: publish, clone, rate, admin moderation
Day 2:  Load test browse endpoint (target: 200 req/s with Redis cache hit)
Day 3:  Enable in production — internal users only (feature flag: whitelist)
Day 7:  Open to all users — remove whitelist
```

---

### Phase 7 — Seed Data

Before public launch, seed the library with 10–20 curated strategies published by the `aerora_admin` account:
1. Build and backtest them in the Builder (stores `backtest_result`)
2. Publish via `POST /api/library`
3. Admin-approve via `PATCH /api/admin/library/{id}` with `is_featured: true`

This ensures the browse page is not empty on day one.

---

### Rollback Plan

| Phase | Rollback procedure | Data loss? |
|---|---|---|
| Phase 1 (DB) | `DROP TABLE library_strategies, library_ratings; ALTER TABLE strategies DROP COLUMN source_library_id, backtest_result;` | None (new tables) |
| Phase 2–3 (Backend) | Remove `include_router(library_router)` from `main.py`; revert worker registration | None |
| Phase 4 (Frontend) | Revert `StrategyMarketplace.jsx` to mock version from git | None |
| Phase 5 (Admin) | Remove admin routes | None |
| Phase 6 (Flag) | Set `STRATEGY_LIBRARY_ENABLED=false` | None |

---

## 16. Risk & Open Questions

### Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| DAG theft: clone endpoint reads source user's DAG via service role | Medium | High | Scope service role read to exact `strategy_id` from `library_strategies.source_strategy_id`; audit log the read |
| Browse endpoint becomes hot: 1000s of req/s on Redis miss | Medium | Medium | 60s Redis TTL covers normal traffic; add ETag / 304 Not Modified for repeat requests |
| `backtest_result` not populated on existing strategies | High | High | Publishing requires `backtest_result`; users must re-run backtest before publishing |
| `equity_curve_snapshot` too large (full curve = 10K+ points) | High | Medium | Sample to max 500 points in the publish handler before storing |
| Metric worker lag: clone_count stale by 5 min | Low | Low | Real-time WS updates for clone count smooth over the lag on the detail page |
| Author identity exposed via `author_id` in API response | Medium | High | **Never expose `author_id`** in any browse/detail response; use `author_alias` only |
| Rating manipulation (clone + immediately rate) | Low | Low | `is_verified_clone` check; rate limiting (one rating update per day per strategy) |

### Open Questions

| # | Question | Decision needed by | Notes |
|---|---|---|---|
| Q1 | Should moderation default to `auto-approve` for trusted tiers (pro/elite)? | Product | V1 safer with all `pending`; V2 can add trust scoring |
| Q2 | Should `author_alias` be the user's `display_name` or a pseudonym? | Product | Pseudonym is safer for privacy; display_name creates accountability |
| Q3 | Should the full DAG be accessible post-clone, or is the clone enough? | Engineering | Clone gives full access already; the original is never exposed |
| Q4 | Should clone count be the truth from `strategies.source_library_id` or the DB `clone_count` column? | Engineering | Use column (pre-aggregated) for reads; worker reconciles truth every 5 min |
| Q5 | Should unpublishing tombstone the strategy or hard-delete? | Product | Recommend soft-delete (`is_active=FALSE`); existing clones need lineage intact |
| Q6 | What happens if the source strategy is deleted by the author after publishing? | Engineering | `source_strategy_id` is not a FK to `strategies` (user could delete it); the library entry is a snapshot and remains valid |
| Q7 | Free tier: 1 publish limit — does unpublishing free the slot? | Product | Recommend yes (count only `is_active = TRUE`) |
| Q8 | Should `equity_curve_snapshot` use a CDN-backed object store for large curves? | Engineering | S3/Supabase Storage is V2; sampled JSONB column is fine for V1 at 500 points |

---

## 17. File Map — New Files to Create

This is the complete list of new files the implementation phase will create, for ticket tracking purposes. **No files have been modified.**

### Backend (`backend_app/`)

| File | Type | Description |
|---|---|---|
| `routers/library.py` | New | Strategy Library REST router (all 9 endpoints) |
| `core/models/library_schemas.py` | New | All Pydantic request/response models for Library |
| `backend/library_metrics_worker.py` | New | Background aggregation worker (5-min interval) |

### Backend Modifications

| File | Change |
|---|---|
| `main.py` | Add `include_router(library_router)` + register metrics worker in lifespan |
| `core/dependencies.py` | Add `check_library_publish_limit()` dependency |
| `core/models/__init__.py` | Re-export library schemas |
| `api_ws/ws_routes.py` | Add `ws_library()` WebSocket handler |
| `api_ws/ws_manager.py` | Add `"library"` subscription type |
| `routers/strategies.py` | Persist `backtest_result` to `strategies` table after successful backtest |

### Database Migrations

| File | Type | Description |
|---|---|---|
| `alembic/versions/XXXX_add_strategy_library_columns.py` | New Alembic version | Adds `source_library_id`, `backtest_result` to strategies |
| `supabase/migrations/001_create_library_strategies.sql` | New Supabase SQL | Creates `library_strategies` table + indexes + triggers + RLS |
| `supabase/migrations/002_create_library_ratings.sql` | New Supabase SQL | Creates `library_ratings` table + indexes + triggers + RLS |
| `supabase/migrations/003_alter_strategies_library_columns.sql` | New Supabase SQL | Adds `source_library_id`, `backtest_result` to Supabase strategies |

### Frontend (`algo22-terminal/src/`)

| File | Type | Description |
|---|---|---|
| `pages/StrategyLibraryDetail.jsx` | New | Full detail view for one library entry |
| `pages/MyLibraryPage.jsx` | New | Author's published strategy management |
| `components/LibraryPublishModal.jsx` | New | 3-step publish wizard |
| `components/LibraryRatingModal.jsx` | New | Star rating + review submission |
| `components/LibraryStrategyCard.jsx` | New | Reusable browse card |
| `components/LibraryEquityChart.jsx` | New | Equity curve sparkline |
| `components/LibraryFilterBar.jsx` | New | Search + filter controls |
| `components/LibraryModerationBadge.jsx` | New | Status badge |
| `hooks/useLibrary.js` | New | Browse state + API integration |
| `hooks/useLibraryDetail.js` | New | Detail + WS integration |

### Frontend Modifications

| File | Change |
|---|---|
| `pages/StrategyMarketplace.jsx` | Full rewrite — replace mock with API |
| `apiClient.js` | Add 7 library API methods |
| `App.jsx` | Add `library_detail` and `my_library` to routing switch |

---

*Design document — no code has been modified.*  
*Branch: `feature/strategy-marketplace-v1`*  
*Reference: [PROJECT_ARCHITECTURE.md](./PROJECT_ARCHITECTURE.md)*
