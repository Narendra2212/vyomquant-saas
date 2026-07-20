# MARKETPLACE_DATABASE_FOUNDATION_REPORT

## Tables Created
*   **`library_strategies`**: Primary catalogue snapshot table for published strategies.
*   **`library_ratings`**: Ratings and reviews per user for published strategies.

## Columns Added
*   **`strategies.source_library_id`**: Added to track clone lineage from the marketplace.
*   **`strategies.backtest_result`**: Added to store the actual backtest results gating publishing eligibility.

## Indexes Created
*   `library_strategies`: `author_id`, `category`, `backtest_sharpe_ratio`, `backtest_total_return_pct`, `clone_count`, `avg_rating`, `published_at`, `is_active`, `is_featured`, and `tags` (using GIN).
*   `library_ratings`: `UNIQUE(library_id, user_id)` constraint automatically acts as an index.

## Row-Level Security (RLS)
*   **`library_strategies`**: Enabled.
    *   `SELECT`: Authenticated users can view active and approved strategies, plus their own authored strategies regardless of state.
    *   `INSERT`, `UPDATE`, `DELETE`: Scoped to the author (`auth.uid() = author_id`).
*   **`library_ratings`**: Enabled via `005_rls_library_ratings.sql`.
    *   `SELECT`: Authenticated users can view all ratings.
    *   `INSERT`, `UPDATE`, `DELETE`: Scoped to the reviewer (`auth.uid() = user_id`).

## Migration Status
*   `001_create_library_strategies.sql`: **APPLIED**
*   `002_create_library_ratings.sql`: **APPLIED**
*   `003_add_library_columns_to_strategies.sql`: **APPLIED**
*   `004_rls_library_strategies.sql`: **APPLIED**
*   `005_rls_library_ratings.sql`: **APPLIED**

## Rollback Plan
If an emergency rollback is required, execute the following SQL:
```sql
-- 1. Drop constraints & columns from existing tables
ALTER TABLE public.strategies DROP COLUMN IF EXISTS source_library_id;
ALTER TABLE public.strategies DROP COLUMN IF EXISTS backtest_result;

-- 2. Drop triggers and functions
DROP TRIGGER IF EXISTS trg_library_ratings_updated_at ON public.library_ratings;
DROP TRIGGER IF EXISTS trg_library_strategies_updated_at ON public.library_strategies;
DROP FUNCTION IF EXISTS update_library_ratings_updated_at();
DROP FUNCTION IF EXISTS update_library_strategies_updated_at();

-- 3. Drop tables (CASCADE will remove dependent policies and indexes)
DROP TABLE IF EXISTS public.library_ratings CASCADE;
DROP TABLE IF EXISTS public.library_strategies CASCADE;
```

## Verification Evidence
Execution using Python `psycopg2` verified the applied schema on the cloud instance:
```
Connected to database.
Applying migrations/001_create_library_strategies.sql... Success
Applying migrations/002_create_library_ratings.sql... Success
Applying migrations/003_add_library_columns_to_strategies.sql... Success
Applying migrations/004_rls_library_strategies.sql... Success
Applying migrations/005_rls_library_ratings.sql... Success

Verified Tables: ['library_ratings', 'library_strategies']
Verified Columns in strategies: ['backtest_result', 'source_library_id']
Verified RLS: [('library_ratings', True), ('library_strategies', True)]
```

Storage Bucket Verification:
```
Bucket not found, creating...
Bucket created: 200
(Verified via Supabase Storage REST API using Service Role Key)
```

## Remaining Work
*   Marketplace backend logic (`routers/library.py`).
*   Frontend integration (`StrategyMarketplace.jsx`).
*   Telemetry events.

**FOUNDATION COMPLETE**
