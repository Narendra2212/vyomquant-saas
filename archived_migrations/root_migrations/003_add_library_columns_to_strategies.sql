-- 003_add_library_columns_to_strategies.sql
ALTER TABLE public.strategies
ADD COLUMN IF NOT EXISTS source_library_id UUID REFERENCES public.library_strategies(id) ON DELETE SET NULL,
ADD COLUMN IF NOT EXISTS backtest_result JSONB;
