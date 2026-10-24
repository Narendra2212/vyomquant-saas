-- ═══════════════════════════════════════════════════════════════════════════
-- 058_exch03_exchange_keys_stable_handle.sql
--
-- EXCH-03. Give `exchange_keys` the single-column handle a deployment can store,
-- so that a live deployment can resolve the exchange account it was bound to.
--
-- THE DEFECT, MEASURED RATHER THAN REASONED ABOUT
-- ───────────────────────────────────────────────────────────────────────────
-- `deployment_binding.load_owned_exchange_account` resolves a deployment's
-- `exchange_account_id` with `.eq("id", ...)` against each of
-- `EXCHANGE_ACCOUNT_TABLES = ("exchange_keys", "exchange_connections")`, and
-- raises `EXCHANGE_ACCOUNT_NOT_FOUND` (404) if neither answers.
--
-- Against the real production database (2026-10-24):
--
--   * `exchange_keys` HAS NO `id` COLUMN. Eleven columns, and its PRIMARY KEY is
--     the COMPOSITE `(user_id, exchange_id)`. So `.eq("id", ...)` raises
--     `42703 column exchange_keys.id does not exist`, which that function's own
--     `except/continue` -- written to tolerate a relation absent from a given
--     deployment -- swallows as "table unavailable". It holds 2 rows, i.e. real
--     stored credentials.
--   * `exchange_connections` DOES carry an `id`, and holds ZERO rows.
--
-- So the table with the credentials cannot answer the query and the table that
-- can has nothing in it: EVERY live deployment 404s, first-party as well as
-- marketplace. Not a live incident, because live trading is hard-off -- a launch
-- blocker rather than an active fault.
--
-- WHY A SURROGATE KEY, AND NOT ONE OF THE OTHER TWO OPTIONS
-- ───────────────────────────────────────────────────────────────────────────
-- EXCH-03 recorded three candidate fixes. This is the first, and the reason it is
-- the right one is a fact about how credentials are ACTUALLY resolved at runtime:
--
--     `api_key_vault.load_decrypted_keys(user_id, exchange_id)`
--
-- The execution process resolves credentials by the COMPOSITE, never by an
-- account id. `deployment_binding.account_exchange_id` exists precisely to read
-- `exchange_id` back off the account row, and the module docstring states the
-- vault call takes `(user_id, exchange_id)`.
--
-- So `exchange_account_id` carries exactly one responsibility: a stable,
-- single-column, FK-able record of WHICH of a user's accounts a deployment was
-- bound to. It is a handle, not an identity. That is what
-- `strategy_deployments.exchange_account_id uuid` (004e) is shaped for, and a
-- composite natural key cannot be stored in one uuid column.
--
-- Option (b) -- resolving by `(user_id, exchange_id)` -- would mean redesigning
-- that column, and would make a deployment's binding ambiguous the moment a user
-- holds two credentials for one venue.
-- Option (c) -- making `exchange_connections` authoritative -- is the larger,
-- better-shaped end state and is still worth doing, because the two tables
-- genuinely disagree today (2 rows against 0). It is not what unblocks live
-- deployment, and conflating "stop the tables disagreeing" with "let a deployment
-- name an account" would hold the second hostage to the first. EXCH-03's entry
-- keeps (c) open.
--
-- THE PRIMARY KEY IS NOT TOUCHED, DELIBERATELY
-- ───────────────────────────────────────────────────────────────────────────
-- `(user_id, exchange_id)` stays the PRIMARY KEY. It is semantically correct --
-- one credential per user per venue is the real constraint, and it is what the
-- vault looks up by -- and replacing the primary key of a table holding encrypted
-- customer credentials is a far larger risk than adding a column beside it. `id`
-- gets a UNIQUE constraint instead, which is all a lookup handle needs.
--
-- NO FOREIGN KEY FROM strategy_deployments, for the reason 004e already gives for
-- this exact column: it is a REFERENCE and never a credential, 004e declares no
-- FK on it, and adding one now would make a credential's deletion unable to
-- proceed while any historical deployment still names it -- which would be a new
-- behaviour, not a fix to this one.
--
-- IDEMPOTENT. `ADD COLUMN IF NOT EXISTS`, the backfill only touches NULLs, and
-- both constraints sit behind existence guards. A re-run does nothing.
-- ═══════════════════════════════════════════════════════════════════════════

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 1 — Preflight.
-- ─────────────────────────────────────────────────────────────────────────
DO $exch03_preflight$
BEGIN
    IF to_regclass('public.exchange_keys') IS NULL THEN
        RAISE NOTICE
            '058: public.exchange_keys does not exist on this database; nothing '
            'to do. It is declared by 006_reconcile_production_database.sql.';
        RETURN;
    END IF;
    RAISE NOTICE '058 preflight: public.exchange_keys is present';
END
$exch03_preflight$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 2 — The handle.
-- ─────────────────────────────────────────────────────────────────────────
-- `uuid`, to match `strategy_deployments.exchange_account_id` exactly. A text id
-- would make the two sides of the same reference different types, which is the
-- mismatch `exchange_connections.id` (text) already has against that column and
-- which EXCH-03 records as its third problem -- repeating it here would be
-- choosing to keep that problem.
DO $exch03_column$
BEGIN
    IF to_regclass('public.exchange_keys') IS NULL THEN
        RETURN;
    END IF;

    -- TWO SHAPES EXIST AND BOTH ARE ACCEPTABLE, which is why this does not force a
    -- type. `migrations/003_create_exchange_keys_table.sql` declares
    -- `id TEXT PRIMARY KEY DEFAULT gen_random_uuid()`, so a database provisioned
    -- from repository source ALREADY has a handle -- as text, and as the primary
    -- key. Production's table was never created by that migration (its own
    -- `tests/test_exchange_connections_read_regression.py` header records that the
    -- repo's SQL does not describe the live table) and had no handle at all.
    --
    -- `ADD COLUMN IF NOT EXISTS` is therefore a no-op on the from-source shape and
    -- the real change on the production shape. It must NOT try to retype an
    -- existing text id to uuid: that column is the PRIMARY KEY there, and
    -- rewriting the primary key of a table holding encrypted customer credentials
    -- to fix a type that already works as a handle would be a serious risk taken
    -- for tidiness. A text column holding a uuid string answers
    -- `.eq("id", ...)` exactly as well, which is the only thing the resolver asks
    -- of it.
    ALTER TABLE public.exchange_keys
        ADD COLUMN IF NOT EXISTS id UUID DEFAULT gen_random_uuid();

    -- Backfill. The DEFAULT covers every row inserted from here on and every row
    -- the ALTER itself rewrote, but a column added by hand earlier could carry
    -- NULLs, and a NULL handle is a row no deployment can name.
    UPDATE public.exchange_keys SET id = gen_random_uuid() WHERE id IS NULL;

    -- NOT NULL only after the backfill, so the statement cannot fail on existing
    -- data. Checked first because ALTER ... SET NOT NULL is not idempotent-safe
    -- to re-issue in every PostgreSQL version.
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'exchange_keys'
           AND column_name = 'id' AND is_nullable = 'YES'
    ) THEN
        ALTER TABLE public.exchange_keys ALTER COLUMN id SET NOT NULL;
    END IF;

    RAISE NOTICE '058: exchange_keys.id present, backfilled and NOT NULL';
END
$exch03_column$;

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 3 — UNIQUE, so the handle actually identifies one account.
-- ─────────────────────────────────────────────────────────────────────────
DO $exch03_unique$
BEGIN
    IF to_regclass('public.exchange_keys') IS NULL THEN
        RETURN;
    END IF;

    -- Skipped when `id` is already the PRIMARY KEY, which is the from-source shape
    -- (003 declares `id TEXT PRIMARY KEY`). A primary key is already unique, so a
    -- second constraint would add an index that enforces nothing new.
    IF EXISTS (
        SELECT 1 FROM pg_constraint con
          CROSS JOIN LATERAL unnest(con.conkey) k(attnum)
          JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
         WHERE con.contype = 'p'
           AND con.conrelid = 'public.exchange_keys'::regclass
           AND a.attname = 'id'
    ) THEN
        RAISE NOTICE
            '058: exchange_keys.id is already the primary key on this database '
            '(the shape migrations/003_create_exchange_keys_table.sql declares), so '
            'it is already unique and no extra constraint is added';
    ELSIF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'uq_exchange_keys_id'
           AND conrelid = 'public.exchange_keys'::regclass
    ) THEN
        -- Safe without a duplicate preflight: every value was either generated by
        -- `gen_random_uuid()` above or already distinct, and a collision would
        -- fail this statement and roll the whole migration back rather than
        -- admitting two accounts under one handle.
        ALTER TABLE public.exchange_keys
            ADD CONSTRAINT uq_exchange_keys_id UNIQUE (id);
        RAISE NOTICE
            '058: uq_exchange_keys_id added -- exchange_keys.id now identifies '
            'exactly one account, which is what a deployment''s '
            'exchange_account_id needs it to do';
    ELSE
        RAISE NOTICE '058: uq_exchange_keys_id already present';
    END IF;
END
$exch03_unique$;

COMMENT ON COLUMN public.exchange_keys.id IS
    'EXCH-03. A stable single-column handle for this credential, so a deployment
     can record WHICH account it was bound to in
     strategy_deployments.exchange_account_id (uuid, migration 004e). It is a
     HANDLE, NOT AN IDENTITY: the primary key remains the composite
     (user_id, exchange_id), and the execution process resolves credentials by
     that composite via api_key_vault.load_decrypted_keys(user_id, exchange_id),
     never by this column. Added because deployment_binding.
     load_owned_exchange_account resolves exchange_account_id with .eq("id", ...)
     and this table had no such column, so every live deployment answered 404.';

-- ─────────────────────────────────────────────────────────────────────────
-- SECTION 4 — Postflight: the lookup the resolver performs must now work.
-- ─────────────────────────────────────────────────────────────────────────
DO $exch03_postflight$
DECLARE
    v_total   bigint;
    v_handled bigint;
    v_probe   uuid;
    v_found   bigint;
BEGIN
    IF to_regclass('public.exchange_keys') IS NULL THEN
        RETURN;
    END IF;

    -- `uuid` OR `text`, deliberately. The from-source shape is text and is the
    -- primary key; production's is uuid, added by this file. Both answer the
    -- resolver's `.eq("id", ...)`, which is the only property that was broken.
    -- Demanding uuid here would fail this migration on every database built from
    -- repository source -- which is every CI run.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='exchange_keys'
           AND column_name='id' AND data_type IN ('uuid', 'text', 'character varying')
    ) THEN
        RAISE EXCEPTION
            '058 postflight FAILED: exchange_keys has no usable `id` handle, so '
            'strategy_deployments.exchange_account_id cannot name a credential and '
            'every live deployment would keep answering 404.';
    END IF;

    -- Unique by EITHER route: this file's constraint, or the primary key the
    -- from-source shape already puts on the column.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'uq_exchange_keys_id'
           AND conrelid = 'public.exchange_keys'::regclass
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint con
          CROSS JOIN LATERAL unnest(con.conkey) k(attnum)
          JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
         WHERE con.contype = 'p'
           AND con.conrelid = 'public.exchange_keys'::regclass
           AND a.attname = 'id'
    ) THEN
        RAISE EXCEPTION
            '058 postflight FAILED: exchange_keys.id is neither UNIQUE nor the '
            'primary key, so the handle does not identify one account.';
    END IF;

    SELECT count(*), count(id) INTO v_total, v_handled FROM public.exchange_keys;
    IF v_total <> v_handled THEN
        RAISE EXCEPTION
            '058 postflight FAILED: % of % exchange_keys row(s) carry no handle, '
            'so those accounts cannot be bound to a deployment.',
            v_total - v_handled, v_total;
    END IF;

    -- Exercise the resolver's own lookup shape against a real row, if there is
    -- one. This is the actual thing that was broken -- `.eq("id", ...)` raising
    -- 42703 -- so proving the query now returns is worth more than proving the
    -- column exists.
    SELECT id INTO v_probe FROM public.exchange_keys LIMIT 1;
    IF v_probe IS NOT NULL THEN
        SELECT count(*) INTO v_found
          FROM public.exchange_keys WHERE id = v_probe;
        IF v_found <> 1 THEN
            RAISE EXCEPTION
                '058 postflight FAILED: a lookup by handle returned % rows, not 1.',
                v_found;
        END IF;
        RAISE NOTICE
            '058: a lookup by handle returns exactly one account -- the query '
            'deployment_binding.load_owned_exchange_account issues now works';
    ELSE
        RAISE NOTICE
            '058: exchange_keys holds no rows here, so the handle lookup was not '
            'exercised against real data (the column and constraint are verified)';
    END IF;

    RAISE NOTICE
        '058: % exchange_keys row(s), all carrying a unique uuid handle', v_total;
END
$exch03_postflight$;
