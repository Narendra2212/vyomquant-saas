-- 031 — fee_records.fee_pct: drop NOT NULL so a third-asset fee can be recorded.
--       (Phase 10, observation A — financial integrity)
-- ==========================================================================
--
-- WHAT WAS WRONG
-- --------------
-- `fee_records.fee_pct` is the trading fee expressed as a FRACTION OF TRADE VOLUME. That
-- ratio only has meaning when the fee and the volume are in the SAME currency. For a fee
-- denominated in a THIRD asset — e.g. a BNB fee on a BTC/USDT trade, which is the Binance
-- DEFAULT (fees in BNB carry a discount) and therefore common, not exotic —
-- `FeeEngine.record_fee` has no conversion rate available, so it CANNOT compute a
-- percentage and correctly writes `fee_pct = None`:
--
--     backend_app/backend/fee_engine.py::record_fee
--         else:                       # a third asset
--             fee_cost_in_quote = None
--         ...
--         fee_pct = (fee_cost_in_quote / trade_volume)  if computable  else  None
--         fee_record = FeeRecordModel(..., fee_pct=str(fee_pct) if fee_pct is not None
--                                                  else None, ...)
--
-- But the provisioned column was `character varying NOT NULL`. Measured directly
-- (staging/probe_fee_pct_notnull.py, inside a rolled-back transaction):
--
--     fee_pct is_nullable (before): NO
--     THIRD-ASSET FEE INSERT: REFUSED -> NotNullViolation
--         null value in column "fee_pct" of relation "fee_records" violates not-null
--         constraint
--
-- So committing a real third-asset fee raised a NOT NULL violation and LOST THE FEE
-- RECORD ENTIRELY — the fee was charged by the venue, was known exactly (amount + asset),
-- and could not be persisted. This has never been hit in production because live trading
-- has never been enabled; it is a latent financial-integrity defect, recorded and fixed
-- rather than left for the first live BNB fee to drop.
--
-- WHY NULL IS THE CORRECT REPRESENTATION, AND NOT ZERO OR A FABRICATED RATE
-- ------------------------------------------------------------------------
-- Three candidate representations of "the percentage is not computable":
--
--   * NULL        — "no percentage; look at fee_amount + fee_asset for the real fee".
--                   Honest and unambiguous.
--   * '0'         — WRONG. A zero percentage states the trade paid no fee, which is false;
--                   it would understate costs and overstate net PnL.
--   * a conversion — FORBIDDEN. `record_fee` has no market-data handle, and fabricating a
--     rate            BNB->USDT price to manufacture a percentage is exactly the
--                     "substitute a plausible number for one nobody measured" anti-pattern
--                     the fee engine's own comments refuse.
--
-- NULL it is. The fee AMOUNT and the fee ASSET remain NOT NULL, so the money actually
-- charged is never lost — only the derived percentage is absent, which is the truth.
--
-- WHY A MIGRATION, GIVEN fee_records IS create_all()-MANAGED
-- ---------------------------------------------------------
-- `fee_records` is created by `main.py`'s `Base.metadata.create_all()` from
-- `FeeRecordModel` (see 026's own analysis of the same table). The model's
-- `fee_pct = Column(String, nullable=True)` is updated in the same change, which is the
-- authoritative source for a FRESH database. But `create_all()` does NOT alter an
-- EXISTING table's column, so a database already provisioned with the NOT NULL column
-- keeps it until an explicit `ALTER`. This file is that ALTER, for the already-provisioned
-- (local verification) database and any environment built before the model change.
--
-- Additive-in-spirit: it only RELAXES a constraint (NOT NULL -> NULL). It writes no rows,
-- drops no column, and narrows nothing — every value that was valid before is valid
-- after, and one more (NULL) becomes valid. No existing row changes.
-- Regression: tests/test_fee01_tenant_is_validated_not_sentinel_postgres.py and
--             tests/test_fee_third_asset_pct_is_nullable_postgres.py
-- ==========================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 0 — PREFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('public.fee_records') IS NULL THEN
        RAISE EXCEPTION
            '031 precondition failed: public.fee_records does not exist. It is created by '
            'main.py Base.metadata.create_all() from FeeRecordModel at application '
            'startup; run the app (or create_all) against this database first.';
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1 — RELAX THE CONSTRAINT
-- ─────────────────────────────────────────────────────────────────────────────
-- fee_amount and fee_asset are deliberately NOT touched: the fee charged must always be
-- recorded. Only the DERIVED percentage may be absent.
ALTER TABLE public.fee_records
    ALTER COLUMN fee_pct DROP NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2 — POSTFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    pct_nullable   text;
    amount_nullable text;
    asset_nullable  text;
BEGIN
    SELECT is_nullable INTO pct_nullable
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'fee_records'
       AND column_name = 'fee_pct';

    IF pct_nullable <> 'YES' THEN
        RAISE EXCEPTION
            '031 postflight FAILED: fee_records.fee_pct is still NOT NULL, so a '
            'third-asset fee still cannot be recorded.';
    END IF;

    -- The money columns MUST stay mandatory. Relaxing the percentage must not have
    -- loosened the fee amount or asset — those are the record of what was actually
    -- charged, and a NULL there would be a lost fee, which is the opposite of this fix.
    SELECT is_nullable INTO amount_nullable
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'fee_records'
       AND column_name = 'fee_amount';
    SELECT is_nullable INTO asset_nullable
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'fee_records'
       AND column_name = 'fee_asset';

    IF amount_nullable <> 'NO' OR asset_nullable <> 'NO' THEN
        RAISE EXCEPTION
            '031 postflight FAILED: fee_amount (%) or fee_asset (%) became nullable. The '
            'fee actually charged must always be recorded; only the derived fee_pct may '
            'be absent.', amount_nullable, asset_nullable;
    END IF;

    RAISE NOTICE
        '031: fee_records.fee_pct is now NULLABLE (third-asset fees record their amount '
        'and asset with fee_pct NULL); fee_amount and fee_asset remain NOT NULL.';
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 3 — COMMENT
-- ─────────────────────────────────────────────────────────────────────────────
COMMENT ON COLUMN public.fee_records.fee_pct IS
    'The fee as a fraction of trade volume, or NULL when it is not computable. It is '
    'computable only when the fee is denominated in the trade''s base or quote asset; a '
    'THIRD-asset fee (e.g. BNB on BTC/USDT) has no conversion rate available in '
    'record_fee, so fee_pct is NULL and the real fee is read from fee_amount + fee_asset. '
    'NULL rather than 0 (which would falsely state a free trade) or a fabricated rate '
    '(forbidden). Phase 10 observation A.';

COMMIT;
