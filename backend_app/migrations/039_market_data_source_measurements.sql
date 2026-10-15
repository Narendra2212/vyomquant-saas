-- backend_app/migrations/039_market_data_source_measurements.sql
--
-- RT-04 (P0). `_recorded_measurements()` (routers/paper_trading.py) has always returned
-- `None`: Requirement 14.4's correctness floor is real and running, but the measurement it
-- is applied to was never produced or stored anywhere a request could read it. Every
-- `POST /api/paper/sessions` call therefore resolves `select_market_data_source(None)` to
-- `FLOOR_ADMITTED_NOTHING` and refuses before creating anything -- a Paper Session can
-- never start, for any user, regardless of what else on the platform works.
--
-- THIS MIGRATION DOES NOT CLOSE RT-04. It cannot: the floor requires >= 99.99% completeness
-- over >= 10,000 bars (`CorrectnessFloor.min_completeness_sample_bars`) measured against a
-- REAL market feed over real wall-clock time -- `backend/market_data_measurement_store.py`'s
-- own module docstring and `scripts/record_market_data_measurement.py` explain why that
-- cannot be produced from a chat session. What this gives the operator who DOES run that
-- measurement is somewhere real to put the result, so `_recorded_measurements()` has
-- something to read once they have.
--
-- Append-only, service-role write only (this is not a user-facing table and no browser role
-- has ever had a reason to write a measurement of the platform's own infrastructure).
-- `payload` is `SourceMeasurement.to_dict()` verbatim, so `SourceMeasurement.from_dict` is
-- the exact inverse of whatever is read back -- no second schema to keep in sync with the
-- dataclass this exists to persist.

CREATE TABLE IF NOT EXISTS public.market_data_source_measurements (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source          TEXT        NOT NULL,
    measured        BOOLEAN     NOT NULL,
    window_seconds  DOUBLE PRECISION,
    recorded_by     TEXT        NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload         JSONB       NOT NULL
);

-- The read this exists for ("the latest measurement per source") is exactly this index.
CREATE INDEX IF NOT EXISTS idx_market_data_source_measurements_latest
    ON public.market_data_source_measurements (source, recorded_at DESC);

ALTER TABLE public.market_data_source_measurements ENABLE ROW LEVEL SECURITY;

-- No policy is created for anon/authenticated: with RLS enabled and no permissive policy,
-- PostgREST denies every row to both, which is the intended shape for a table only an
-- operator's service-role process ever writes to or reads from directly (the API reads it
-- through that same service-role path, in backend/market_data_measurement_store.py).
REVOKE ALL ON public.market_data_source_measurements FROM anon, authenticated;
GRANT SELECT, INSERT ON public.market_data_source_measurements TO service_role;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'market_data_source_measurements'
    ) THEN
        RAISE EXCEPTION '039 postflight FAILED: market_data_source_measurements was not created.';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.role_table_grants
         WHERE table_schema = 'public' AND table_name = 'market_data_source_measurements'
           AND grantee IN ('anon', 'authenticated')
    ) THEN
        RAISE EXCEPTION '039 postflight FAILED: a browser role holds a grant on '
                        'market_data_source_measurements.';
    END IF;
END
$$;
