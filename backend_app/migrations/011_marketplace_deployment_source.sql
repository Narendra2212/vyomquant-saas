--
-- 011_marketplace_deployment_source.sql
--
-- PURPOSE
--   Give a deployment somewhere to record THAT IT CAME FROM A MARKETPLACE
--   LISTING, so a subscriber can execute the owner's immutable
--   Strategy_Version without ever holding a copy of it.
--
--   marketplace-subscriptions-paper-trading, task 17.3. Requirements 7.1,
--   7.5, 7.8. design.md -> "marketplace/entitlement_resolver.py and
--   subscriber execution":
--
--     "[deploy_marketplace_strategy] creates a strategy_deployments row
--      bound to the OWNER's version_id with marketplace_listing_id recorded
--      and user_id = the subscriber - the additive marketplace-sourced
--      deployment source that
--      .kiro/specs/trading-lifecycle-integration/design.md Requirement 27
--      reserved."
--
--   This file is that additive extension point being taken up. It adds ONE
--   nullable column and nothing else.
--
-- WHY THE COLUMN IS NEEDED AT ALL
--   Before task 17.3, POST /api/library/{id}/deploy answered a subscriber by
--   INSERTing a public.strategies row carrying the owner's buy_logic,
--   sell_logic, risk, indicators and ml_model_path into a row the SUBSCRIBER
--   owned. That is a full Protected_Logic transfer, and Requirement 7.1
--   forbids it: a non-owner may never receive the Listing's logic, in any
--   field of any response or any row they own.
--
--   The subscriber-safe shape is the one public.strategy_deployments already
--   has: (user_id, strategy_id, version_id) with user_id = the SUBSCRIBER and
--   strategy_id / version_id = the OWNER's strategy and its current immutable
--   version. The server resolves the executable artifact from version_id at
--   run time; the subscriber's row references it and never contains it.
--
--   What that shape cannot express today is WHY a row points at a strategy
--   its user_id does not own. Without this column, a marketplace-sourced
--   deployment is indistinguishable from a cross-user row written in error,
--   there is nothing to join a deployment back to the Listing (and so to the
--   Subscription that entitled it) for revocation, creator analytics or
--   audit, and nothing records that the referenced version is deliberately
--   another user's.
--
-- WHY A NEW FILE RATHER THAN AN EDIT TO 001 / 003 / 004e
--   The same reason 004b/004c/004d/004e, 005a/005b and 006 through 010 each
--   state: migrations here are applied BY HAND, per file, and NOTHING RECORDS
--   WHICH FILES AN ENVIRONMENT HAS RUN - there is no migration table, no
--   alembic/django runner, and .github/workflows/03-deploy.yml has no
--   migration step. Editing a file an operator may already have applied
--   leaves no signal that it changed, so the new statement would simply never
--   run. A new filename is the signal.
--
--   004e_deployment_bindings.sql is the file that most looks like it should
--   own this column, and deliberately does not: it is
--   trading-lifecycle-integration task 8.1, whose Requirement 27 places
--   Marketplace explicitly OUT OF SCOPE and only RESERVES the extension
--   point. 004e also asserts, in its own section 6, that the policy and index
--   counts on public.strategy_deployments are unchanged by its run; adding a
--   marketplace concern inside it would put a second spec's column behind
--   that spec's assertions.
--
-- WHY 011 AND NOT A LOWER NUMBER
--   006 -> 007 -> 008 -> 009 -> 010 is this spec's declared dependency chain
--   (design.md -> "Migration ordering"), and 010 is already applied wherever
--   the chain has been. This file depends on NONE of them: its only
--   prerequisite is public.strategy_deployments, created by 001 (or,
--   equivalently, by 003). It can therefore be applied at any point after
--   001/003, in either direction relative to 006-010, without changing the
--   resulting schema.
--
-- NO FOREIGN KEY, AND WHY
--   The column holds a public.library_strategies id, and there is no
--   REFERENCES clause on it. Two reasons, both already precedents in this
--   directory:
--
--     * 003_signal_trace_restoration.sql redefines
--       strategy_deployments.exchange_id as VARCHAR(50) with NO foreign key
--       precisely because the referenced table does not exist in every
--       environment, and a dangling FK aborts the whole file. The marketplace
--       catalogue table is created outside both migration sequences
--       (migrations/007_add_marketplace_pricing_columns.sql documents the
--       42703 that followed from an environment that lacked its columns), so
--       a hard FK here would make this file's applicability depend on a table
--       it has no business creating.
--
--     * 004e section "NO FOREIGN KEYS" makes the same choice for
--       exchange_account_id and risk_config_id on this very table: the
--       handler asserts the reference, not the schema, because the check the
--       reference needs is a per-user one (does this Listing entitle THIS
--       caller) that a foreign key cannot express anyway. Requirement 7.7
--       puts that check in the Entitlement_Resolver, inside the same request
--       as the write.
--
--   A nullable UUID with no FK is therefore the honest shape: it records the
--   Listing when there is one and says nothing when there is not.
--
-- NULLABLE, AND WHY NOT NOT NULL DEFAULT
--   Every strategy_deployments row that exists today was written by an owner
--   deploying their OWN strategy - there is no Listing behind it. NULL is the
--   correct value for those rows and for every future first-party deployment,
--   and it is the value the column already has after ADD COLUMN. A NOT NULL
--   DEFAULT would have to invent a Listing id for a deployment that never had
--   one, which is a fabricated fact of exactly the kind Requirement 28.5
--   forbids. No backfill is performed for the same reason.
--
-- SCOPE - ONE COLUMN, ONE COMMENT, ONE TRANSACTION
--     0  preflight assertions and the policy/index/trigger inventory
--     1  the column, nullable
--     2  column shape assertion
--     3  column comment
--     4  postflight - the column exists, and nothing else moved
--
--   Nothing about library_strategies, library_subscriptions,
--   marketplace_submissions, paper trading or the signal environment is here.
--   Those are migrations 007 through 010.
--
-- WHY THIS FILE REFUSES TO RUN WITHOUT RLS ON public.strategy_deployments
--   Same reading as 004e's, and it applies more sharply here. This column is
--   the handle by which one user's deployment points at ANOTHER user's
--   strategy version. Row-level security is what keeps the pointing
--   one-directional: the subscriber may read their own deployment row (and so
--   its results), and cannot read the owner's strategy, version or blueprint
--   rows, because those are scoped to the owner. Adding the column to a table
--   whose row-level isolation is off would let any authenticated caller
--   enumerate which users subscribe to which Listings, and pair a foreign
--   version_id with their own session.
--
--   That is a refusal to make an existing hole worse, not a claim to fix one.
--   This file does NOT enable row-level security itself and does NOT create,
--   alter or remove a policy. Row-level security is ROW-scoped, not
--   column-scoped, so the existing owner and service-role policies apply
--   unchanged to the new column; tenant isolation is exactly as strong after
--   this migration as before it.
--
-- APPLICATION
--   NOT applied automatically. Apply this file explicitly against the target
--   database, then run the verification queries at the bottom, the way
--   scripts/forensics/apply_migration_007.py applied 007.
--
--   Until it is applied, the column does not exist, and
--   deploy_marketplace_strategy's INSERT surfaces PostgREST PGRST204 - which
--   the handler answers as a 500 naming this file rather than by silently
--   omitting the Listing linkage. A deployment recorded without the Listing
--   it came from would read as a first-party deployment of another user's
--   version, which is worse than a refusal.
--
-- SAFETY
--   * Additive only (Requirement 24.7). One ADD COLUMN. Nothing is removed or
--     re-labelled, no existing column's type, nullability or default is
--     altered, no row is inserted, updated or removed, no policy is created,
--     altered or removed, row-level security is neither enabled nor disabled,
--     no trigger, no GRANT, no REVOKE, no index.
--   * Fully idempotent (Requirements 24.7, 24.8, 24.9). ADD COLUMN IF NOT
--     EXISTS, and COMMENT replaces. A second application adds nothing and
--     raises nothing.
--   * One transaction. Either the column and its comment exist, or neither
--     does.
--   * Applies both from an empty database (after 001 or 003) and from the
--     current production revision (Requirement 24.8).
--

-- ==========================================================================
-- SECTION 1 - task 17.3: the marketplace-sourced deployment source column
-- ==========================================================================

BEGIN;

-- 0) Preflight ---------------------------------------------------------
-- Read-only assertions plus three transaction-local counters. Fails with a
-- readable message instead of a bare undefined_table error from inside the
-- ALTER, refuses to widen a deployment table without row-level security, and
-- records the policy/index/trigger inventory so section 4 can prove this file
-- left all three alone. No NAME is hard-coded, so an environment whose policy
-- or index names differ cannot false-alarm; every count is taken inside this
-- one transaction, so they are comparable by construction.
DO $$
DECLARE
    rls_on    BOOLEAN;
    policies  INTEGER;
    indexes   INTEGER;
    triggers  INTEGER;
    missing   TEXT;
BEGIN
    IF to_regclass('public.strategy_deployments') IS NULL THEN
        RAISE EXCEPTION
            '011 precondition failed: table public.strategy_deployments does '
            'not exist. Apply backend_app/migrations/'
            '001_strategy_architecture.sql (section 2) first, or '
            'backend_app/migrations/003_signal_trace_restoration.sql '
            '(section 3). This file does not create the table, because doing '
            'so would add a THIRD divergent shape to the two that already '
            'declare it with CREATE TABLE IF NOT EXISTS.';
    END IF;

    -- The three columns that make the subscriber-safe deployment shape work:
    -- user_id is the SUBSCRIBER, strategy_id and version_id are the OWNER's.
    -- All three are present under BOTH base definitions of this table, so an
    -- absence here means the table is in a shape neither file produced, and
    -- the column added below would have nothing to qualify.
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('user_id'), ('strategy_id'), ('version_id'))
             AS expected(column_name)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema = 'public'
            AND actual.table_name   = 'strategy_deployments'
            AND actual.column_name  = expected.column_name
     WHERE actual.column_name IS NULL;

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            '011 precondition failed: public.strategy_deployments is missing '
            'column(s): %. The marketplace-sourced deployment source '
            'qualifies the (user_id, strategy_id, version_id) triple - '
            'subscriber, owner strategy, owner version - so a table without '
            'them is in a shape neither 001 nor 003 produced.', missing;
    END IF;

    SELECT c.relrowsecurity INTO rls_on
      FROM pg_class c
     WHERE c.oid = 'public.strategy_deployments'::regclass;

    IF NOT rls_on THEN
        RAISE EXCEPTION
            '011 refuses to run: row level security is DISABLED on '
            'public.strategy_deployments. The column added here is the handle '
            'by which ONE user''s deployment references ANOTHER user''s '
            'strategy version, and row-level isolation is what keeps that '
            'reference one-directional. Applying it to a table without RLS '
            'would let any authenticated caller enumerate which users '
            'subscribe to which Listings. Enable row level security and '
            'restore the owner and service-role policies from '
            'backend_app/migrations/001_strategy_architecture.sql (or 003), '
            'then re-run this file. This migration deliberately does not do '
            'it for you: creating a policy here would be this file quietly '
            'authoring an access control it is not the owner of.';
    END IF;

    SELECT count(*) INTO policies
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'strategy_deployments';

    SELECT count(*) INTO indexes
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'strategy_deployments';

    SELECT count(*) INTO triggers
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.strategy_deployments'::regclass
       AND NOT t.tgisinternal;

    IF policies = 0 THEN
        RAISE EXCEPTION
            '011 refuses to run: row level security is enabled on '
            'public.strategy_deployments but it has NO policies, so the table '
            'is unreachable to every non-superuser role and a '
            'marketplace-sourced deployment written here could not be read '
            'back by the subscriber who owns it. Restore the policies from '
            'backend_app/migrations/001_strategy_architecture.sql (or 003) '
            'first.';
    END IF;

    PERFORM set_config('aerora.md_policies_before', policies::TEXT, true);
    PERFORM set_config('aerora.md_indexes_before',  indexes::TEXT,  true);
    PERFORM set_config('aerora.md_triggers_before', triggers::TEXT, true);

    RAISE NOTICE '011 preflight: public.strategy_deployments has RLS '
                 'enabled, % policies, % indexes and % non-internal '
                 'trigger(s); this file adds none of them and section 4 '
                 'verifies all three counts are unchanged.',
                 policies, indexes, triggers;
END $$;


-- 1) The column --------------------------------------------------------
-- marketplace_listing_id  The public.library_strategies Listing this
--                         deployment was created from, or NULL for a
--                         first-party deployment of the caller's own
--                         strategy. When it is set, user_id is the
--                         SUBSCRIBER and (strategy_id, version_id) are the
--                         OWNER's - the whole point of the column being that
--                         the subscriber EXECUTES the owner's immutable
--                         version and never holds a copy of it
--                         (Requirements 7.1, 7.5).
--
--                         UUID, matching library_strategies.id. Nullable and
--                         un-defaulted: see "NULLABLE, AND WHY NOT NOT NULL
--                         DEFAULT" in the header. No REFERENCES: see "NO
--                         FOREIGN KEY, AND WHY".
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, so a second run skips it.
ALTER TABLE public.strategy_deployments
    ADD COLUMN IF NOT EXISTS marketplace_listing_id UUID;


-- 2) Column shape assertion --------------------------------------------
-- ADD COLUMN IF NOT EXISTS is SILENT about a pre-existing column of the same
-- name and a different type - one added by hand during an investigation, say,
-- or carried over from a shape nobody recorded. "The column is there" then
-- does not mean "the column is usable": a TEXT marketplace_listing_id would
-- accept a value that is not a Listing id at all, and would compare unequal
-- to a UUID library_strategies.id under every join and every PostgREST
-- filter, so a revocation sweep or a creator-analytics read would silently
-- match nothing. Same pattern as 004e section 2 and 006 section 1.2.
DO $$
DECLARE
    actual_type TEXT;
BEGIN
    SELECT data_type INTO actual_type
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name   = 'strategy_deployments'
       AND column_name  = 'marketplace_listing_id';

    IF actual_type IS NULL THEN
        RAISE EXCEPTION
            '011 postcondition failed: '
            'public.strategy_deployments.marketplace_listing_id does not '
            'exist immediately after ADD COLUMN IF NOT EXISTS. Section 1 did '
            'not take effect.';
    END IF;

    IF actual_type <> 'uuid' THEN
        RAISE EXCEPTION
            '011 refuses to continue: '
            'public.strategy_deployments.marketplace_listing_id already '
            'exists with type % instead of uuid. A pre-existing column of '
            'that name was left exactly as it was, because ADD COLUMN IF NOT '
            'EXISTS does not change one. It must hold the same type as '
            'public.library_strategies.id (uuid) or every join and every '
            'PostgREST filter against it matches nothing. Reconcile the '
            'column by hand and re-run; this file will not cast it for you, '
            'because a cast on an existing column is not an additive '
            'change.', actual_type;
    END IF;
END $$;


-- 3) Column comment ----------------------------------------------------
-- COMMENT replaces rather than accumulates, so this is idempotent.
COMMENT ON COLUMN public.strategy_deployments.marketplace_listing_id IS
    'The public.library_strategies Listing this deployment was created from, or NULL for a '
    'first-party deployment of the caller''s own strategy. Set by '
    'routers/library.deploy_marketplace_strategy (marketplace-subscriptions-paper-trading task '
    '17.3) after backend/marketplace/entitlement_resolver.resolve returns an entitling '
    'decision. When it is set, user_id is the SUBSCRIBER and (strategy_id, version_id) are the '
    'OWNER''s: the subscriber executes the owner''s immutable Strategy_Version server-side and '
    'never holds a copy of its buy_logic, sell_logic, risk, indicators or ml_model_path '
    '(Requirements 7.1, 7.5). This is the additive marketplace-sourced deployment source that '
    'trading-lifecycle-integration Requirement 27.3 reserved and 27.4 required be addable '
    'without disturbing any existing column. NO foreign key by design - the reference that '
    'matters is the per-caller one (does this Listing entitle THIS caller right now), which is '
    'checked by the Entitlement_Resolver inside the same request as the write (Requirement '
    '7.7), and which no foreign key can express.';


-- 4) Postflight --------------------------------------------------------
-- Prove the column exists and that this file added no policy, no index and no
-- trigger. The counts come from section 0's transaction-local settings, so
-- they are comparable by construction and no object NAME is hard-coded.
DO $$
DECLARE
    policies_before  INTEGER := current_setting('aerora.md_policies_before', true)::INTEGER;
    indexes_before   INTEGER := current_setting('aerora.md_indexes_before',  true)::INTEGER;
    triggers_before  INTEGER := current_setting('aerora.md_triggers_before', true)::INTEGER;
    policies_now     INTEGER;
    indexes_now      INTEGER;
    triggers_now     INTEGER;
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'strategy_deployments'
           AND column_name  = 'marketplace_listing_id'
    ) THEN
        RAISE EXCEPTION
            '011 postcondition failed: '
            'public.strategy_deployments.marketplace_listing_id is absent at '
            'the end of the transaction that adds it.';
    END IF;

    SELECT count(*) INTO policies_now
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'strategy_deployments';

    SELECT count(*) INTO indexes_now
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'strategy_deployments';

    SELECT count(*) INTO triggers_now
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.strategy_deployments'::regclass
       AND NOT t.tgisinternal;

    IF policies_before IS NOT NULL AND policies_now <> policies_before THEN
        RAISE EXCEPTION
            '011 postcondition failed: the policy count on '
            'public.strategy_deployments moved from % to %. This file must '
            'not create, alter or remove a policy.',
            policies_before, policies_now;
    END IF;

    IF indexes_before IS NOT NULL AND indexes_now <> indexes_before THEN
        RAISE EXCEPTION
            '011 postcondition failed: the index count on '
            'public.strategy_deployments moved from % to %. This file adds '
            'no index.',
            indexes_before, indexes_now;
    END IF;

    IF triggers_before IS NOT NULL AND triggers_now <> triggers_before THEN
        RAISE EXCEPTION
            '011 postcondition failed: the trigger count on '
            'public.strategy_deployments moved from % to %. This file adds '
            'no trigger; the existing updated_at trigger from 001/003 keeps '
            'maintaining updated_at for writes that touch the new column '
            'too, and attaching a second one would double-fire.',
            triggers_before, triggers_now;
    END IF;

    RAISE NOTICE '011 complete: '
                 'public.strategy_deployments.marketplace_listing_id exists '
                 '(uuid, nullable); policies, indexes and triggers all '
                 'unchanged at %, % and %.',
                 policies_now, indexes_now, triggers_now;
END $$;

COMMIT;


-- ==========================================================================
-- VERIFICATION (run after applying; each is read-only)
-- ==========================================================================
--
--   -- 1. The column exists with the right type and is nullable.
--   SELECT column_name, data_type, is_nullable, column_default
--     FROM information_schema.columns
--    WHERE table_schema = 'public'
--      AND table_name   = 'strategy_deployments'
--      AND column_name  = 'marketplace_listing_id';
--   -- expected: marketplace_listing_id | uuid | YES | (null)
--
--   -- 2. No existing row was given a value.
--   SELECT count(*) AS with_listing
--     FROM public.strategy_deployments
--    WHERE marketplace_listing_id IS NOT NULL;
--   -- expected before the first marketplace deployment: 0
--
--   -- 3. The comment landed.
--   SELECT col_description(
--            'public.strategy_deployments'::regclass,
--            (SELECT ordinal_position
--               FROM information_schema.columns
--              WHERE table_schema = 'public'
--                AND table_name   = 'strategy_deployments'
--                AND column_name  = 'marketplace_listing_id')::INT);
--
--   -- 4. Row-level security and the policy set are as they were.
--   SELECT relrowsecurity FROM pg_class
--    WHERE oid = 'public.strategy_deployments'::regclass;
--   SELECT policyname, cmd, roles FROM pg_policies
--    WHERE schemaname = 'public' AND tablename = 'strategy_deployments'
--    ORDER BY policyname;
--
--   -- 5. Every marketplace-sourced deployment references a live Listing and
--   --    is owned by someone other than that Listing's author. Reports rows,
--   --    changes nothing - the invariant is asserted by the handler
--   --    (Requirement 7.7), not by a constraint.
--   SELECT d.id, d.user_id, d.marketplace_listing_id, l.author_id
--     FROM public.strategy_deployments d
--     LEFT JOIN public.library_strategies l
--            ON l.id = d.marketplace_listing_id
--    WHERE d.marketplace_listing_id IS NOT NULL
--      AND (l.id IS NULL OR l.author_id = d.user_id);
--   -- expected: 0 rows
