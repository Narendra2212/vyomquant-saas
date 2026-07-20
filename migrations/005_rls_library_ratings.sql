-- =============================================================================
-- Migration 005: Row-Level Security policies for library_ratings
-- Project:       VyomQuant / Algo22
-- Branch:        feature/strategy-marketplace-v1
-- Applied to:    Supabase PostgreSQL (public schema)
-- Depends on:    002_create_library_ratings.sql
--                004_rls_library_strategies.sql (library_strategies must have RLS enabled)
-- Rollback:      See §5 below
-- =============================================================================
--
-- RLS Design for library_ratings:
--
--   SELECT  — All authenticated users can see all ratings (reviews are social/public
--             content). Anon users cannot see ratings (avoids scraping user
--             review data without an account). The browse API endpoint is public
--             but the detail page's recent_ratings panel requires the API layer
--             to fetch ratings via service role for unauthenticated callers.
--
--   INSERT  — Authenticated users can create their own rating row.
--             The clone handler creates the row (is_verified_clone=TRUE, rating=NULL).
--             The rate handler upserts rating + review_text into the existing row.
--             UNIQUE(library_id, user_id) prevents duplicates at DB level.
--
--   UPDATE  — Users can update their own rating row (to change rating or review).
--
--   DELETE  — Users can delete their own rating row (withdraw review).
--             Note: The metrics worker will pick up the deletion on next run
--             and recompute avg_rating accordingly.
--
--   Service role (background worker, clone handler, admin):
--             Bypasses all RLS. clone_count / is_verified_clone are written
--             by the clone endpoint using service role client.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 0. Dependency guards
-- ---------------------------------------------------------------------------

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_name   = 'library_ratings'
  ) THEN
    RAISE EXCEPTION
      'Dependency missing: public.library_ratings does not exist. '
      'Apply migration 002 first.';
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_name   = 'library_strategies'
  ) THEN
    RAISE EXCEPTION
      'Dependency missing: public.library_strategies does not exist. '
      'Apply migration 001 first.';
  END IF;
END $$;

-- Confirm RLS is already enabled on library_strategies (migration 004)
DO $$
DECLARE
  v_rls_enabled BOOLEAN;
BEGIN
  SELECT relrowsecurity
    INTO v_rls_enabled
    FROM pg_class
   WHERE relname = 'library_strategies'
     AND relnamespace = 'public'::regnamespace;

  IF NOT v_rls_enabled THEN
    RAISE WARNING
      'RLS is not yet enabled on library_strategies. '
      'Apply migration 004 before this migration for full isolation. '
      'Proceeding anyway — this migration only affects library_ratings.';
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 1. Enable RLS on library_ratings
-- ---------------------------------------------------------------------------

ALTER TABLE public.library_ratings ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- 2. SELECT policy — Authenticated users see all ratings
-- ---------------------------------------------------------------------------

-- Why TO authenticated (not public)?
--   Ratings include user activity signals. Requiring auth to read ratings
--   limits scraping of review content by unauthenticated bots. The API detail
--   endpoint fetches recent_ratings via service role for unauthenticated callers,
--   so the public-facing detail page still shows reviews — but direct Supabase
--   PostgREST queries require auth.

CREATE POLICY "library_ratings_select_authenticated"
  ON public.library_ratings
  FOR SELECT
  TO authenticated
  USING (TRUE);

-- ---------------------------------------------------------------------------
-- 3. INSERT policy — Users can create their own rating row
-- ---------------------------------------------------------------------------

-- This policy covers two INSERT scenarios:
--
--   a) Clone handler (service role): creates row with is_verified_clone=TRUE,
--      rating=NULL. Service role bypasses RLS, so this policy is not evaluated
--      for that path. It is listed here for completeness.
--
--   b) Rate handler (user JWT): the clone handler should have already created
--      the row; the rate handler does an UPSERT. If the row does not exist
--      (edge case: rating without cloning, blocked by API layer), this INSERT
--      policy would apply — and the UNIQUE + check constraint would reject it.
--
-- WITH CHECK ensures user_id = auth.uid() (users cannot insert as another user).

CREATE POLICY "library_ratings_insert_own"
  ON public.library_ratings
  FOR INSERT
  TO authenticated
  WITH CHECK (user_id = auth.uid());

-- ---------------------------------------------------------------------------
-- 4. UPDATE policy — Users can update their own rating
-- ---------------------------------------------------------------------------

-- Covers: POST /api/library/{id}/rate → UPSERT into existing row.
-- The UNIQUE constraint on (library_id, user_id) means ON CONFLICT UPDATE
-- targets exactly the user's own row. USING + WITH CHECK enforce ownership.

CREATE POLICY "library_ratings_update_own"
  ON public.library_ratings
  FOR UPDATE
  TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- ---------------------------------------------------------------------------
-- 5. DELETE policy — Users can delete (withdraw) their own rating
-- ---------------------------------------------------------------------------

-- A user withdrawing their rating should be allowed.
-- The metrics worker will recompute avg_rating on next run (within 5 min).
-- Note: DELETE removes the entire row including is_verified_clone.
-- If the user wants to re-rate later, they must clone again (or the API layer
-- can guard against accidental deletion of is_verified_clone rows).

CREATE POLICY "library_ratings_delete_own"
  ON public.library_ratings
  FOR DELETE
  TO authenticated
  USING (user_id = auth.uid());

-- ---------------------------------------------------------------------------
-- 6. Helper function: has_user_cloned()
--    Inline RLS helper — can be called from API layer via Supabase RPC
--    to check if the current user has cloned a specific library strategy.
--    More efficient than a full table query from application code.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.fn_has_user_cloned(p_library_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.library_ratings
    WHERE library_id        = p_library_id
      AND user_id           = auth.uid()
      AND is_verified_clone = TRUE
  );
$$;

COMMENT ON FUNCTION public.fn_has_user_cloned(UUID) IS
  'Returns TRUE if auth.uid() has a verified clone of the given library strategy. '
  'Used by the rate endpoint to enforce the clone-before-rate gate. '
  'Callable via Supabase RPC: SELECT fn_has_user_cloned(''<library_id>'')';

-- Grant execute to authenticated role (used by API layer via user-scoped JWT)
GRANT EXECUTE ON FUNCTION public.fn_has_user_cloned(UUID)
  TO authenticated;

-- ---------------------------------------------------------------------------
-- 7. Helper function: get_user_library_status()
--    Returns the current user's clone and rating status for a library strategy.
--    Used by the browse and detail endpoints to enrich responses when auth is
--    present (user_has_cloned, user_rating fields in LibraryCardResponse).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.fn_get_user_library_status(p_library_id UUID)
RETURNS TABLE (
  has_cloned  BOOLEAN,
  user_rating SMALLINT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT
    COALESCE(is_verified_clone, FALSE) AS has_cloned,
    rating                             AS user_rating
  FROM public.library_ratings
  WHERE library_id = p_library_id
    AND user_id    = auth.uid()
  LIMIT 1;
$$;

COMMENT ON FUNCTION public.fn_get_user_library_status(UUID) IS
  'Returns has_cloned and user_rating for auth.uid() against a library strategy. '
  'Used to enrich browse/detail API responses when a valid JWT is present. '
  'Returns no rows (not NULL) if the user has no interaction with this strategy.';

GRANT EXECUTE ON FUNCTION public.fn_get_user_library_status(UUID)
  TO authenticated;

-- ---------------------------------------------------------------------------
-- 8. Helper function: get_library_recent_ratings()
--    Fetches the 5 most recent ratings with review text for the detail page.
--    Uses SECURITY DEFINER so unauthenticated API callers (service role) can
--    fetch ratings for the public detail page without requiring user auth on
--    the Supabase client.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.fn_get_library_recent_ratings(
  p_library_id UUID,
  p_limit      INT DEFAULT 5
)
RETURNS TABLE (
  rating      SMALLINT,
  review_text TEXT,
  created_at  TIMESTAMPTZ
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT
    r.rating,
    r.review_text,
    r.created_at
  FROM public.library_ratings r
  WHERE r.library_id  = p_library_id
    AND r.rating       IS NOT NULL
    AND r.review_text  IS NOT NULL
  ORDER BY r.created_at DESC
  LIMIT LEAST(p_limit, 20);  -- Cap at 20 regardless of caller input
$$;

COMMENT ON FUNCTION public.fn_get_library_recent_ratings(UUID, INT) IS
  'Returns the most recent rated reviews for a library strategy. '
  'SECURITY DEFINER allows unauthenticated callers (via service role in API) '
  'to fetch reviews for the public detail page. '
  'Callable as: SELECT * FROM fn_get_library_recent_ratings(''<id>'', 5)';

-- Grant to both authenticated and anon (needed for public detail page)
GRANT EXECUTE ON FUNCTION public.fn_get_library_recent_ratings(UUID, INT)
  TO authenticated, anon;

-- ---------------------------------------------------------------------------
-- 9. Verification queries
--    Run these manually after applying this migration.
-- ---------------------------------------------------------------------------

-- 9.1 List all policies on library_ratings:
-- SELECT policyname, cmd, roles, qual, with_check
-- FROM pg_policies
-- WHERE schemaname = 'public' AND tablename = 'library_ratings'
-- ORDER BY cmd, policyname;
--
-- Expected:
-- ┌────────────────────────────────────────┬────────┬─────────────────┐
-- │ policyname                             │ cmd    │ roles           │
-- ├────────────────────────────────────────┼────────┼─────────────────┤
-- │ library_ratings_select_authenticated   │ SELECT │ {authenticated} │
-- │ library_ratings_insert_own             │ INSERT │ {authenticated} │
-- │ library_ratings_update_own             │ UPDATE │ {authenticated} │
-- │ library_ratings_delete_own             │ DELETE │ {authenticated} │
-- └────────────────────────────────────────┴────────┴─────────────────┘

-- 9.2 Confirm anon cannot SELECT from library_ratings directly:
-- SET ROLE anon;
-- SELECT COUNT(*) FROM public.library_ratings;
-- -- Expected: permission denied (or 0 rows if anon has connect)
-- RESET ROLE;

-- 9.3 Confirm authenticated user cannot SELECT other users' clone rows
-- (they can via the current policy — all ratings are visible to auth users):
-- -- This is intentional: reviews are social content.
-- -- The user_id column is never returned in API responses (API layer strips it).

-- 9.4 Confirm fn_has_user_cloned works:
-- SELECT public.fn_has_user_cloned('<known_library_id>');
-- -- As a user who has cloned: TRUE
-- -- As a user who has not cloned: FALSE

-- ---------------------------------------------------------------------------
-- 10. Rollback instructions (do NOT execute here — for reference only)
-- ---------------------------------------------------------------------------
--
-- DROP POLICY IF EXISTS "library_ratings_select_authenticated" ON public.library_ratings;
-- DROP POLICY IF EXISTS "library_ratings_insert_own"           ON public.library_ratings;
-- DROP POLICY IF EXISTS "library_ratings_update_own"           ON public.library_ratings;
-- DROP POLICY IF EXISTS "library_ratings_delete_own"           ON public.library_ratings;
--
-- DROP FUNCTION IF EXISTS public.fn_has_user_cloned(UUID);
-- DROP FUNCTION IF EXISTS public.fn_get_user_library_status(UUID);
-- DROP FUNCTION IF EXISTS public.fn_get_library_recent_ratings(UUID, INT);
--
-- ALTER TABLE public.library_ratings DISABLE ROW LEVEL SECURITY;
--
-- Data impact: None. RLS policies and functions are metadata only.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- End of migration 005
-- ---------------------------------------------------------------------------
