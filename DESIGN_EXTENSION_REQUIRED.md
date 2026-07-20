# DESIGN_EXTENSION_REQUIRED

## Overview
During the execution of **Sprint 2A.1 — Strategy Marketplace Foundation Implementation**, it was identified that several required production objects are missing from the approved architecture (`STRATEGY_LIBRARY_DESIGN.md`). 

Per the implementation rules, I have paused execution and generated this extension request instead of inventing undocumented schemas.

## Missing Objects

### 1. `strategy_images`
*   **Reason Required**: The certification audit (MARKETPLACE_ARCHITECTURE_AUDIT.md) listed this as a missing requirement, and it is essential for rendering the Marketplace UI (strategy cover images and equity curve snapshots).
*   **Recommended Schema**: 
    ```sql
    CREATE TABLE public.strategy_images (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        strategy_id UUID NOT NULL REFERENCES public.library_strategies(id) ON DELETE CASCADE,
        image_url TEXT NOT NULL,
        image_type TEXT NOT NULL CHECK (image_type IN ('cover', 'equity_curve')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    ```
*   **Implementation Impact**: Requires a new migration script and an RLS policy ensuring public read access and owner-only write access.

### 2. `strategy_favorites`
*   **Reason Required**: Listed in the audit as missing. Essential for the "Favorites" feature on the frontend, allowing users to bookmark strategies from the library.
*   **Recommended Schema**: 
    ```sql
    CREATE TABLE public.strategy_favorites (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
        library_id UUID NOT NULL REFERENCES public.library_strategies(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(user_id, library_id)
    );
    ```
*   **Implementation Impact**: Requires a migration script and an RLS policy for users to manage their own favorites.

### 3. `strategy_versions`
*   **Reason Required**: Listed in the audit as missing. Required if we ever need to persist older versions of strategies before they are updated or if a clone targets a specific historical point.
*   **Recommended Schema**: 
    ```sql
    CREATE TABLE public.strategy_versions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        strategy_id UUID NOT NULL REFERENCES public.strategies(id) ON DELETE CASCADE,
        version_number INTEGER NOT NULL,
        buy_logic JSONB NOT NULL,
        sell_logic JSONB NOT NULL,
        risk JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(strategy_id, version_number)
    );
    ```
*   **Implementation Impact**: Requires tracking logic inside the update endpoints of `strategies.py` and a new migration script.

### 4. `audit_log`
*   **Reason Required**: Listed as missing. Critical for compliance, tracking admin moderation actions (e.g. approve/reject/feature), and significant user actions (publish/clone).
*   **Recommended Schema**: 
    ```sql
    CREATE TABLE public.audit_log (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID REFERENCES auth.users(id),
        action TEXT NOT NULL,
        entity_id UUID,
        entity_type TEXT NOT NULL,
        metadata JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    ```
*   **Implementation Impact**: Requires a helper function to write to this log on sensitive endpoints and a new migration script.

### 5. `strategy_analytics`
*   **Reason Required**: Listed as missing. Needed for live P&L aggregation and more advanced metric tracking beyond `clone_count` and `avg_rating`.
*   **Recommended Schema**: 
    ```sql
    CREATE TABLE public.strategy_analytics (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        strategy_id UUID NOT NULL REFERENCES public.library_strategies(id) ON DELETE CASCADE,
        metric_name TEXT NOT NULL,
        metric_value NUMERIC,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    ```
*   **Implementation Impact**: Requires background workers to populate it and a new migration script.

## Request for Approval
I have already fixed the Critical Security flaws (BUG-M01, BUG-M02) in `backend_app/routers/strategies.py` by securing the stub endpoints with `Depends(get_current_user)` and returning `HTTP 501 Not Implemented`.

Please review the recommended schemas above. 

**Do you approve these schemas, or should I skip them and proceed only with `library_strategies` and `library_ratings` explicitly defined in `STRATEGY_LIBRARY_DESIGN.md`?**
