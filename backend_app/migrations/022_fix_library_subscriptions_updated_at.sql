-- backend_app/migrations/022_fix_library_subscriptions_updated_at.sql
--
-- BUG-056 [P0]: EVERY UPDATE to public.library_subscriptions fails.
--
-- THE DEFECT, FOUND BY EXECUTING IT
-- ----------------------------------
-- `trg_library_subscriptions_updated_at` (archived_migrations/root_migrations/
-- 007_create_library_subscriptions.sql:30-39) unconditionally assigns
-- `NEW.updated_at` on every UPDATE to this table. `library_subscriptions` has
-- never had an `updated_at` column — confirmed against
-- `information_schema.columns` on a database provisioned from this repository,
-- zero rows. PL/pgSQL does not silently ignore an assignment to a field `NEW`
-- does not have; it raises `42703 record "new" has no field "updated_at"` at
-- the first UPDATE statement.
--
-- "The first UPDATE statement" is every one that matters:
--
--   * `settlement_service.settle()` — the function that moves a subscription
--     from `pending` to `active` after a confirmed Stripe/Razorpay payment —
--     issues exactly this UPDATE (settlement_service.py:1549-1553). Reproduced
--     against a real database: seed a PENDING subscription, seed a REAL,
--     non-reversal `marketplace_settlements` row for it (what a successful
--     webhook produces), then issue the same UPDATE the service issues.
--     Result: `42703`, every time. A paying customer's subscription can never
--     reach `active` through this path.
--   * `cancel_subscription` (routers/library.py:3520) and `renew_subscription`
--     (checkout_service.py:723) UPDATE the same table and fail the same way.
--   * `expiry_sweep.py` explicitly documents writing "`status` (plus
--     `updated_at`)" as the ONLY two columns it touches — it was written
--     assuming the column exists, and it does not.
--
-- THIS WAS NOT A SILENT FAILURE. `settlement_service.settle()` re-raises as
-- `SettlementPersistenceError`; `routers/billing.py`'s webhook handler catches
-- that and answers the webhook with HTTP 500, logged as an error — never a
-- fabricated 200. Stripe/Razorpay would retry per their own schedule and fail
-- identically every time, because the defect is deterministic. The payment
-- itself still succeeds at the provider and `marketplace_settlements` still
-- records it (the ledger write happens first and is unaffected — see 008's own
-- ordering), so this is a complete, loud, reconcilable outage of entitlement
-- activation, not a silent loss of money. It is still a P0: no customer who
-- pays for a marketplace subscription receives access to it.
--
-- WHY NOBODY'S TEST CAUGHT IT
-- ----------------------------
-- `tests/test_settlement_service.py` and the property suite built on it
-- (`tests/property/test_settlement_idempotence.py`,
-- `tests/property/test_settlement_ledger.py`) exercise `settle()` against
-- `FakeSupabase`, a Python double with no trigger of any kind.
-- `tests/test_expiry_sweep.py::test_the_column_contract_entry_covers_every_
-- table_the_sweep_touches` asserts `"updated_at" in tables["library_
-- subscriptions"]` against `COLUMN_CONTRACT` — a hand-maintained Python
-- dict, not a live schema introspection — so the test and the bug agreed with
-- each other and both were wrong. This is recorded in
-- `staging/FINAL_FULL_SYSTEM_AUDIT.md`'s test-quality section as the clearest
-- example found of a mocked-too-heavily test: every layer that could have
-- caught this never touched a real trigger. The regression test added for
-- this fix (`tests/test_bug056_library_subscriptions_updated_at_postgres.py`)
-- is deliberately a `*_postgres.py` file: it runs the real trigger against a
-- real database, which no existing test did.
--
-- THE FIX
-- -------
-- Add the column the trigger has always assumed exists, rather than remove
-- the trigger: `expiry_sweep.py`'s own invariant
-- (`_FORBIDDEN_UPDATE_COLUMNS`) and its documentation already treat
-- `updated_at` as a real, maintained column, and removing the trigger instead
-- would mean every one of those UPDATEs stops updating it rather than
-- starting to. Additive only — no DROP, no data loss, no behaviour change to
-- any row that already exists; `library_subscriptions` has no rows on a
-- correctly-functioning deployment of this defect (every write failed), so
-- there is nothing to backfill.
--
-- Idempotent: `ADD COLUMN IF NOT EXISTS` and the backstop both tolerate a
-- re-run.
--
-- NUMBERING NOTE: this file was originally written and applied/tested as
-- `021_fix_library_subscriptions_updated_at.sql`, chosen without checking
-- for an existing `021` in this directory. `021_strategy_deployment_live_
-- uniqueness.sql` already occupied that number (production-launch-hardening
-- task 13.23, applied earlier in this audit). This file was recreated under
-- `022` to avoid the collision; its content and effect on a database are
-- otherwise unchanged from the version already applied and verified against
-- the local PostgreSQL instance used for this audit.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name = 'library_subscriptions'
    ) THEN
        RAISE NOTICE '022: public.library_subscriptions does not exist; nothing to do';
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'library_subscriptions'
           AND column_name = 'updated_at'
    ) THEN
        ALTER TABLE public.library_subscriptions
            ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        RAISE NOTICE '022: added public.library_subscriptions.updated_at';
    ELSE
        RAISE NOTICE '022: public.library_subscriptions.updated_at already exists';
    END IF;
END
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- BACKSTOP: refuse to finish while any UPDATE-triggered column write targets a
-- column that does not exist on its table. The defect this file closes is a
-- CLASS (a trigger function assuming a column its table never declared), and
-- the specific instance being fixed above must not be the only one checked —
-- a future trigger added the same way, on any table, should fail provisioning
-- rather than fail on the first production UPDATE.
--
-- Detected generically: for every BEFORE/INSTEAD OF trigger on a table in
-- `public`, parse its function body for `NEW.<identifier> = ` or
-- `NEW.<identifier> :=` assignments and confirm each named column exists on
-- that trigger's own table. A false negative is possible (the parse is a
-- regex over `pg_get_functiondef`, not a SQL parser), so this is a backstop
-- for the common shape, not a proof of absence — the specific case this file
-- exists for is confirmed fixed above regardless of what this backstop finds.
DO $$
DECLARE
    trig record;
    body text;
    assigned_col text;
    offending text := '';
BEGIN
    FOR trig IN
        SELECT c.relname AS table_name, t.tgname AS trigger_name, p.oid AS func_oid
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE NOT t.tgisinternal
    LOOP
        body := pg_get_functiondef(trig.func_oid);

        FOR assigned_col IN
            SELECT (regexp_matches(body, 'NEW\.([a-zA-Z_][a-zA-Z0-9_]*)\s*[:=]=', 'g'))[1]
        LOOP
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                 WHERE table_schema = 'public' AND table_name = trig.table_name
                   AND column_name = assigned_col
            ) THEN
                offending := offending || format('%I.%I (trigger %I assigns NEW.%I); ',
                                                  trig.table_name, assigned_col,
                                                  trig.trigger_name, assigned_col);
            END IF;
        END LOOP;
    END LOOP;

    IF offending <> '' THEN
        RAISE EXCEPTION
            '022 backstop FAILED: a trigger assigns NEW.<column> for a column its own '
            'table does not declare, which raises 42703 on the FIRST update that fires '
            'it: %. This is exactly BUG-056. Add the missing column or fix the trigger, '
            'then re-run.', offending;
    END IF;

    RAISE NOTICE '022: no trigger assigns NEW.<column> for a column its table lacks';
END
$$;
