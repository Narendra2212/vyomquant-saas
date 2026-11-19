-- ============================================================================
-- rollback/077_mkt11_rollback.sql
--
-- Reverses 077_mkt11_listing_disclosures.sql.
--
-- DESTRUCTIVE, and the only rollback in this directory that is. 077 creates a
-- table, so undoing it drops seller-authored disclosures -- including published
-- ones a subscriber may have purchased on, which 077's own trigger refuses to let
-- even the service role edit. `DROP TABLE` bypasses that trigger by removing it.
--
-- So this file REFUSES to run while any published row exists, and says what to do
-- instead. Dropping a table whose rows are the basis of a completed sale is not a
-- rollback, it is destruction of evidence, and a migration rollback should not be
-- the tool that does it silently.
--
-- To force it anyway -- knowing what that means -- set the escape hatch first:
--
--     SET vyomquant.allow_disclosure_loss = 'yes';
--
-- Order is the reverse of 077: trigger, function, policies, table. The index and
-- the constraints go with the table.
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $$
DECLARE
    v_exists    boolean;
    v_published int := 0;
    v_total     int := 0;
    v_override  text;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'marketplace_listing_disclosures'
    ) INTO v_exists;

    IF NOT v_exists THEN
        RAISE NOTICE 'ROLLBACK 077: table is already absent, nothing to do';
        RETURN;
    END IF;

    EXECUTE 'SELECT count(*) FROM public.marketplace_listing_disclosures'
        INTO v_total;
    EXECUTE 'SELECT count(*) FROM public.marketplace_listing_disclosures '
            'WHERE published_at IS NOT NULL'
        INTO v_published;

    v_override := current_setting('vyomquant.allow_disclosure_loss', true);

    IF v_published > 0 AND coalesce(v_override, '') <> 'yes' THEN
        RAISE EXCEPTION
            'REFUSING: % of % disclosure rows are PUBLISHED. Dropping this table '
            'destroys the figures those buyers purchased on, and 077''s immutability '
            'trigger exists to prevent exactly that. If the table must go, export '
            'those rows first, then re-run with '
            'SET vyomquant.allow_disclosure_loss = ''yes'';',
            v_published, v_total;
    END IF;

    RAISE NOTICE 'ROLLBACK 077: dropping % rows (% published, override=%)',
        v_total, v_published, coalesce(v_override, 'unset');
END $$;

DROP TRIGGER IF EXISTS trg_listing_disclosures_immutable
    ON public.marketplace_listing_disclosures;

DROP FUNCTION IF EXISTS public.refuse_published_disclosure_change();

DROP POLICY IF EXISTS listing_disclosures_owner_read
    ON public.marketplace_listing_disclosures;
DROP POLICY IF EXISTS listing_disclosures_service_role
    ON public.marketplace_listing_disclosures;

DROP TABLE IF EXISTS public.marketplace_listing_disclosures;

-- After the table, because its CHECK constraints depend on this function.
DROP FUNCTION IF EXISTS public.disclosure_tags_are_valid(text[], integer, integer);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'marketplace_listing_disclosures'
    ) THEN
        RAISE EXCEPTION 'VERIFY: the table still exists after the drop';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_proc
        WHERE proname = 'refuse_published_disclosure_change'
          AND pronamespace = 'public'::regnamespace
    ) THEN
        RAISE EXCEPTION 'VERIFY: refuse_published_disclosure_change() still exists';
    END IF;
    RAISE NOTICE 'OK rollback 077: table, trigger, function and policies are gone';
END $$;

COMMIT;
