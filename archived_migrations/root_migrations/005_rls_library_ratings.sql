-- Migration 005: Row-Level Security policies for library_ratings

ALTER TABLE public.library_ratings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "library_ratings_select_authenticated"
  ON public.library_ratings
  FOR SELECT
  TO authenticated
  USING (TRUE);

CREATE POLICY "library_ratings_insert_own"
  ON public.library_ratings
  FOR INSERT
  TO authenticated
  WITH CHECK (user_id = auth.uid());

CREATE POLICY "library_ratings_update_own"
  ON public.library_ratings
  FOR UPDATE
  TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

CREATE POLICY "library_ratings_delete_own"
  ON public.library_ratings
  FOR DELETE
  TO authenticated
  USING (user_id = auth.uid());

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
  LIMIT LEAST(p_limit, 20);
$$;
