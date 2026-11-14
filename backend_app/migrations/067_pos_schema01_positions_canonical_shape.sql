-- backend_app/migrations/067_pos_schema01_positions_canonical_shape.sql
--
-- POS-SCHEMA-01 (P0). Production's `public.positions` is still the BLOB shape
-- (id, tenant_id, data json, created_at, updated_at) from alembic `d97ffff9c3bb`, but
-- every reader and writer of positions (`core/position_model.py::PositionModel`, used by
-- `backend/position_engine.py`, the order watchdog and the consistency checker) is built on
-- the RICH shape. The reshape lives in alembic revision `b2c3d4e5f6a7`, and production's
-- `alembic_version` is still `d97ffff9c3bb`: nothing ever ran it there.
--
-- Found when the first live testnet fills arrived: the order watchdog's position update
-- failed with
--     UndefinedColumn: column positions.position_id does not exist
-- and, because the failure left the transaction aborted, every later statement on that
-- session raised PendingRollbackError (about 2,000 Sentry events in a few hours).
--
-- This file reproduces `b2c3d4e5f6a7.upgrade()` statement for statement, so a database
-- reshaped by either path ends up identical. It is a NO-OP where `position_id` already
-- exists, which includes every database provisioned by `scripts/provision_database.py`
-- (alembic head runs before the numbered SQL set there).
--
-- Like the alembic revision, it REFUSES rather than converts when the table holds blob
-- rows with a JSON payload, or rows with a NULL tenant_id: mapping free-form JSON onto
-- symbol/side/size would be a guess about money. Production had 0 rows when this was
-- written.

DO $$
DECLARE
    blob_rows bigint;
    null_tenants bigint;
    pk_name text;
BEGIN
    IF to_regclass('public.positions') IS NULL THEN
        RAISE NOTICE 'public.positions does not exist (alembic declares it); nothing to do';
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'positions'
           AND column_name = 'position_id'
    ) THEN
        RAISE NOTICE 'positions already has position_id; nothing to do';
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'positions' AND column_name = 'data'
    ) THEN
        SELECT count(*) INTO blob_rows FROM public.positions
         WHERE data IS NOT NULL AND data::text NOT IN ('null', '{}', '');
        IF blob_rows > 0 THEN
            RAISE EXCEPTION
                'public.positions holds % row(s) with a JSON data payload; refusing to guess '
                'how they map onto symbol/side/size. Nothing has been altered.', blob_rows;
        END IF;
    END IF;

    SELECT count(*) INTO null_tenants FROM public.positions WHERE tenant_id IS NULL;
    IF null_tenants > 0 THEN
        RAISE EXCEPTION
            'public.positions holds % row(s) with a NULL tenant_id; assign an owner or '
            'remove them first. Nothing has been altered.', null_tenants;
    END IF;

    -- The two enum types PositionModel declares with create_type=False.
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'positionside') THEN
        CREATE TYPE positionside AS ENUM ('LONG', 'SHORT');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'positionstatus') THEN
        CREATE TYPE positionstatus AS ENUM ('OPEN', 'PARTIAL', 'CLOSED', 'LIQUIDATED');
    END IF;

    -- position_id, backfilled from the existing identifier, becomes the primary key.
    ALTER TABLE public.positions ADD COLUMN position_id varchar;
    UPDATE public.positions SET position_id = id WHERE position_id IS NULL;
    ALTER TABLE public.positions ALTER COLUMN position_id SET NOT NULL;

    SELECT c.conname INTO pk_name FROM pg_constraint c
      JOIN pg_class t ON t.oid = c.conrelid
      JOIN pg_namespace n ON n.oid = t.relnamespace
     WHERE n.nspname = 'public' AND t.relname = 'positions' AND c.contype = 'p';
    IF pk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE public.positions DROP CONSTRAINT %I', pk_name);
    END IF;
    ALTER TABLE public.positions ADD CONSTRAINT positions_pkey PRIMARY KEY (position_id);

    -- `id` stays, nullable, as the legacy name (same as the alembic revision).
    ALTER TABLE public.positions ALTER COLUMN id DROP NOT NULL;

    ALTER TABLE public.positions ADD COLUMN IF NOT EXISTS strategy_id varchar;
    UPDATE public.positions SET strategy_id = '' WHERE strategy_id IS NULL;
    ALTER TABLE public.positions ALTER COLUMN strategy_id SET NOT NULL;

    ALTER TABLE public.positions ADD COLUMN IF NOT EXISTS exchange_id varchar;

    ALTER TABLE public.positions ADD COLUMN IF NOT EXISTS symbol varchar;
    UPDATE public.positions SET symbol = '' WHERE symbol IS NULL;
    ALTER TABLE public.positions ALTER COLUMN symbol SET NOT NULL;

    ALTER TABLE public.positions ADD COLUMN IF NOT EXISTS size varchar;
    UPDATE public.positions SET size = '0' WHERE size IS NULL;
    ALTER TABLE public.positions ALTER COLUMN size SET NOT NULL;

    ALTER TABLE public.positions ADD COLUMN IF NOT EXISTS avg_entry_price varchar;
    UPDATE public.positions SET avg_entry_price = '0' WHERE avg_entry_price IS NULL;
    ALTER TABLE public.positions ALTER COLUMN avg_entry_price SET NOT NULL;

    ALTER TABLE public.positions ADD COLUMN IF NOT EXISTS unrealized_pnl varchar;
    UPDATE public.positions SET unrealized_pnl = '0.0' WHERE unrealized_pnl IS NULL;
    ALTER TABLE public.positions ALTER COLUMN unrealized_pnl SET NOT NULL;

    ALTER TABLE public.positions ADD COLUMN IF NOT EXISTS realized_pnl varchar;
    UPDATE public.positions SET realized_pnl = '0.0' WHERE realized_pnl IS NULL;
    ALTER TABLE public.positions ALTER COLUMN realized_pnl SET NOT NULL;

    ALTER TABLE public.positions ADD COLUMN IF NOT EXISTS closed_at timestamptz;

    ALTER TABLE public.positions ADD COLUMN IF NOT EXISTS side positionside;
    UPDATE public.positions SET side = 'LONG' WHERE side IS NULL;
    ALTER TABLE public.positions ALTER COLUMN side SET NOT NULL;

    ALTER TABLE public.positions ADD COLUMN IF NOT EXISTS status positionstatus;
    UPDATE public.positions SET status = 'OPEN' WHERE status IS NULL;
    ALTER TABLE public.positions ALTER COLUMN status SET NOT NULL;

    -- Timezone-aware timestamps, defaulted, NOT NULL.
    IF (SELECT data_type FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'positions'
           AND column_name = 'created_at') = 'timestamp without time zone' THEN
        ALTER TABLE public.positions
            ALTER COLUMN created_at TYPE timestamptz USING created_at AT TIME ZONE 'UTC';
    END IF;
    ALTER TABLE public.positions ALTER COLUMN created_at SET DEFAULT now();
    UPDATE public.positions SET created_at = now() WHERE created_at IS NULL;
    ALTER TABLE public.positions ALTER COLUMN created_at SET NOT NULL;

    IF (SELECT data_type FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'positions'
           AND column_name = 'updated_at') = 'timestamp without time zone' THEN
        ALTER TABLE public.positions
            ALTER COLUMN updated_at TYPE timestamptz USING updated_at AT TIME ZONE 'UTC';
    END IF;
    ALTER TABLE public.positions ALTER COLUMN updated_at SET DEFAULT now();
    UPDATE public.positions SET updated_at = now() WHERE updated_at IS NULL;
    ALTER TABLE public.positions ALTER COLUMN updated_at SET NOT NULL;

    ALTER TABLE public.positions ALTER COLUMN tenant_id SET NOT NULL;

    CREATE INDEX IF NOT EXISTS ix_positions_tenant_id ON public.positions (tenant_id);
    CREATE INDEX IF NOT EXISTS ix_positions_strategy_id ON public.positions (strategy_id);
    CREATE INDEX IF NOT EXISTS ix_positions_exchange_id ON public.positions (exchange_id);
END $$;
