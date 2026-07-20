-- 002_create_library_ratings.sql
CREATE TABLE IF NOT EXISTS public.library_ratings (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  library_id      UUID NOT NULL REFERENCES public.library_strategies(id) ON DELETE CASCADE,
  user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

  rating          SMALLINT NOT NULL,
  review_text     TEXT,
  is_verified_clone BOOLEAN NOT NULL DEFAULT FALSE,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT valid_rating CHECK (rating >= 1 AND rating <= 5),
  CONSTRAINT library_ratings_user_library_idx UNIQUE (library_id, user_id)
);

CREATE OR REPLACE FUNCTION update_library_ratings_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_library_ratings_updated_at ON public.library_ratings;
CREATE TRIGGER trg_library_ratings_updated_at
  BEFORE UPDATE ON public.library_ratings
  FOR EACH ROW EXECUTE FUNCTION update_library_ratings_updated_at();
