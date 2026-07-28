-- 004_rls_library_strategies.sql
ALTER TABLE public.library_strategies ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can view active and approved strategies" ON public.library_strategies;
CREATE POLICY "Authenticated users can view active and approved strategies"
  ON public.library_strategies
  FOR SELECT
  TO authenticated
  USING (
    (is_active = TRUE AND moderation_status IN ('approved', 'featured')) OR
    (author_id = auth.uid())
  );

DROP POLICY IF EXISTS "Users can insert their own strategies" ON public.library_strategies;
CREATE POLICY "Users can insert their own strategies"
  ON public.library_strategies
  FOR INSERT
  TO authenticated
  WITH CHECK (author_id = auth.uid());

DROP POLICY IF EXISTS "Users can update their own strategies" ON public.library_strategies;
CREATE POLICY "Users can update their own strategies"
  ON public.library_strategies
  FOR UPDATE
  TO authenticated
  USING (author_id = auth.uid());

DROP POLICY IF EXISTS "Users can delete their own strategies" ON public.library_strategies;
CREATE POLICY "Users can delete their own strategies"
  ON public.library_strategies
  FOR DELETE
  TO authenticated
  USING (author_id = auth.uid());
