-- 008_marketplace_settlement.sql  (backend_app/migrations, migration 008)
--
-- PURPOSE
--   Create the Settlement and Subscription-lifecycle side of the
--   Marketplace: the append-only payment ledger, the append-only
--   Subscription_State history, the twelve-pair transition seed that makes
--   Requirement 11.3's "THE Persistence_Layer SHALL reject the write" true
--   against a direct SQL UPDATE rather than only against a service module,
--   and the additive library_subscriptions columns that carry the
--   Subscription_Period, the split and the provider handshake.
--
--   Requirements 1.2, 9.7, 10.5, 10.8, 11.2, 11.3, 11.11, 11.12, 11.13,
--   11.14, 21.2, 24.1, 24.2, 24.3, 24.4, 24.5, 24.7.
--   Source of the DDL: design.md -> "Data Models" (marketplace_settlements,
--   library_subscription_transitions and the library_subscriptions additive
--   row of "Additive columns on existing tables") and design.md ->
--   "marketplace/subscription_state.py" and
--   "marketplace/settlement_service.py" (the guard, the widened CHECK,
--   chk_ls_active_has_period and idx_lib_subs_expiry), statement for
--   statement, schema-qualified with public. and with every ADD CONSTRAINT
--   put behind a pg_constraint guard.
--
-- WHAT THIS FILE ADDS
--     1  marketplace_subscription_allowed_transitions  the twelve-pair seed
--     2  marketplace_settlements                       the payment ledger
--     3  library_subscription_transitions              append-only history
--     4  library_subscriptions                         twelve additive
--                                                      columns, the widened
--                                                      valid_subscription_
--                                                      status, two checks,
--                                                      one partial index
--     5  three functions and seven triggers
--     6  row level security, policies, grants
--     7  comments, postflight
--
--   Nothing about the paper_* tables or signals.environment is here. Those
--   are migrations 009 and 010, which do not depend on this file.
--
-- WHY THE SEED TABLE IS A TABLE AND NOT AN INLINE CASE
--   marketplace_subscription_allowed_transitions holds the same twelve pairs
--   as SUBSCRIPTION_TRANSITIONS in
--   backend_app/backend/marketplace/subscription_state.py, in the lowercase
--   spelling SUBSCRIPTION_TRANSITION_TEXT_PAIRS produces. It is a table
--   because task 14.6's state-agreement test reads the INSERT statement of
--   section 1b and that Python constant and asserts they are the SAME SET -
--   the technique tests/test_version_consumer_agreement.py already uses for
--   the three spellings of the canonical version column, and the technique
--   007_marketplace_submissions.sql section 1 uses for the eleven Submission
--   edges. An inline CASE inside the trigger function would be a second
--   definition of the state machine that no test could compare against the
--   first, and the two would drift the first time a state was added.
--
--   The twelve pairs, and no thirteenth (Requirement 11.2):
--     pending        -> active, payment_failed, cancelled
--     active         -> expired, cancelled, refunded, suspended
--     expired        -> active
--     cancelled      -> active
--     suspended      -> active, expired
--     payment_failed -> pending
--     refunded       -> (terminal, no row)
--   No state lists itself, so a same-value write is not a transition at all
--   and is short-circuited by the guard's first branch rather than by a
--   separate self-transition rule. refunded contributes no row, which is
--   exactly what makes it terminal: a reversal ends that subscription's
--   life and re-subscribing is a new row.
--
-- WHY THE SEED IS LOWERCASE
--   Requirement 11.1 spells Subscription_State uppercase and
--   SubscriptionState's members are uppercase, but the PERSISTED column
--   library_subscriptions.status is lowercase today - 'pending', 'active',
--   'cancelled', 'expired' - and billing.py's .eq("status", "pending") and
--   library.py's .eq("status", "active") read it that way. The seed, the
--   widened CHECK and the guard therefore all speak the column's lowercase
--   spelling, which is exactly STATUS_TEXT_FOR_STATE in subscription_state.py
--   and nothing else. Storing the uppercase enum spelling would have been a
--   silent rename of four existing values, which Requirement 24.7 forbids
--   and which would have broken both of those existing predicates.
--
-- WHY valid_subscription_status IS THE ONE DROP IN THIS FILE
--   archived_migrations/root_migrations/007_create_library_subscriptions.sql
--   carries
--     CONSTRAINT valid_subscription_status
--       CHECK (status IN ('active','expired','cancelled','pending'))
--   and Requirement 11.1 needs seven values. PostgreSQL has no
--   ALTER CONSTRAINT for a CHECK: widening one is a DROP and an ADD, and
--   there is no third form. Leaving the four-value constraint in place and
--   adding a seven-value one alongside would not widen anything - the old
--   constraint would still refuse 'refunded' - so "additive" is not
--   achievable here in the literal sense.
--
--   This is the documented exception, and it is made as narrow as it can be:
--
--     * The DROP and the ADD are ONE ALTER TABLE STATEMENT with two actions,
--       so there is no instant - not even inside this transaction - at which
--       library_subscriptions.status is unconstrained.
--     * The new value set is a strict SUPERSET of the old one. No existing
--       value is renamed, removed or reinterpreted, so no row that was legal
--       before is illegal after, and every existing predicate keeps its
--       meaning (Requirement 25).
--     * The widening runs only after this file has PROVED it is a widening:
--       it reads the distinct status values actually stored and, if it finds
--       one outside the seven, adds the new constraint NOT VALID and names
--       the offending values in a NOTICE instead of aborting - the same
--       best-effort shape 007 section 6c uses for
--       chk_ls_featured_requires_published, and what Requirement 24.8's
--       "applies ... from a database at the current production schema
--       revision" requires. On a database whose statuses are all inside the
--       seven - which is every database this specification has met - the
--       constraint ends up ordinary and fully validated.
--     * If no valid_subscription_status exists at all (the shape
--       migrations/006_reconcile_production_database.sql creates, which
--       carries no such constraint), nothing is dropped: the seven-value
--       constraint is simply ADDED, which is purely additive.
--     * A re-run drops nothing, because the first branch recognises a
--       constraint that already admits all seven values and leaves it alone.
--
--   No other DROP of any kind appears in this file.
--
-- WHY THE ACTIVATION CHECK IS IN THE DATABASE
--   Requirement 11.14: "IF a request would transition a Subscription into
--   ACTIVE without a payment confirmed through the Billing_Integration for
--   that Subscription_Period, THEN THE Marketplace SHALL reject the
--   request". design.md's root-cause note on the existing renew_subscription
--   handler is the reason this cannot be left to the application: that
--   handler set status = 'active', cleared the expiry and granted deployment
--   permission WITHOUT any payment, and Requirement 11.16 removes it. A
--   removed handler is not a control - the next one can be written the same
--   way, and a psql session with the service key never went through a
--   handler at all.
--
--   trg_subscription_transition_guard therefore refuses any transition into
--   'active' for which no non-reversal marketplace_settlements row matches
--   the subscription with settled_at at or after the row's CURRENT
--   period_expiry. The "current" is OLD.period_expiry, read before this
--   UPDATE changes it, and that is what makes the check exact rather than
--   circular: the payment that bought the period now ending was settled at
--   the START of it, so it cannot fund the next one. A direct
--     UPDATE library_subscriptions SET status='active',
--            period_expiry = period_expiry + interval '1 month'
--   grants no free month, because the row's own settlement history does not
--   contain a payment newer than the expiry it is trying to extend.
--
--   A NULL OLD.period_expiry - a Subscription that has never been active -
--   admits any non-reversal settlement for that subscription, because there
--   is no prior period for a payment to be suspected of re-using. That is
--   the first-activation path of Requirement 11.4.
--
--   ONE CONSEQUENCE OF THIS RULE, RECORDED RATHER THAN SOFTENED. Requirement
--   11.5 computes a renewal expiry from "the later of the current expiry and
--   the confirmation instant", so it contemplates a renewal CONFIRMED BEFORE
--   the current expiry - a purchaser who cancelled and then renews while
--   still inside the paid period (Requirement 11.9 keeps them entitled until
--   the unchanged expiry). Under the rule design.md specifies, that
--   mid-period renewal payment has settled_at < OLD.period_expiry and the
--   guard refuses the 'cancelled' -> 'active' transition. The rule is
--   implemented AS SPECIFIED and not weakened here, because with this schema
--   there is no exact expression of "this settlement has not already been
--   consumed by an earlier activation" - period_start is not advanced on
--   renewal, so no timestamp on the row distinguishes a fresh payment from
--   the previous renewal's. Softening the predicate to admit any newer
--   settlement would re-open exactly the free-month path the requirement
--   exists to close. Resolving the mid-period case needs a consumed-marker
--   column (a settlement_id on the subscription, or a period_sequence), and
--   that is a schema decision this migration must not make on its own. It is
--   flagged here so the settlement_service task and Requirement 11.5 meet it
--   deliberately rather than discovering it as a 23514 from a webhook.
--
-- WHY THE APPEND-ONLY GUARD EXEMPTS A CASCADE
--   Requirement 10.8 forbids deleting or modifying a Settlement_Record and
--   Requirement 11.12 requires each transition entry to be retained.
--   Requirement 24.1 wants an explicit delete rule per relationship. On
--   library_subscription_transitions those pull in opposite directions: the
--   history cascades from library_subscriptions, and an unconditional
--   BEFORE DELETE ... RAISE would make deleting a Subscription impossible
--   even in the one case where it is legitimate.
--
--   marketplace_settlement_append_only_guard() therefore refuses every
--   UPDATE unconditionally, and refuses a DELETE only WHILE THE PARENT
--   SUBSCRIPTION ROW STILL EXISTS. PostgreSQL performs a referential CASCADE
--   as an AFTER trigger on the parent, so by the time the child's BEFORE
--   DELETE trigger runs during a cascade the parent row is already gone; a
--   direct "DELETE FROM library_subscription_transitions WHERE ..." always
--   runs with the parent present. The test is exact, not heuristic - the
--   same reasoning 007 section 7d records for the evidence guard.
--
--   On marketplace_settlements the exemption is unreachable by construction,
--   because subscription_id is ON DELETE RESTRICT: a Subscription carrying a
--   Settlement_Record cannot be deleted at all, which is Requirements 10.8
--   and 11.11 read together. The branch is shared anyway, so there is one
--   function rather than two that could drift.
--
-- WHY BOTH REFERENCES ARE "ON DELETE RESTRICT"
--   design.md § Data Models states it for marketplace_settlements and gives
--   the reason: Requirement 10.8 forbids deleting a Settlement_Record and
--   Requirement 11.11 forbids deleting a Subscription row, so a CASCADE from
--   either parent would be a path to violating both - a DELETE the database
--   would happily perform, that no trigger on the settlement row could
--   refuse without also making the parent undeletable in every case.
--   RESTRICT states the intent where it belongs: the parent is the thing
--   that cannot go.
--
--   This is a deliberate, visible cost. A Listing with settled subscriptions
--   cannot be deleted, and neither can a paid Subscription. That is what
--   "retain every Subscription row and every Settlement_Record permanently"
--   means when the database is asked to enforce it rather than to hope.
--
-- SIX DELIBERATE ADDITIONS BEYOND THE DESIGN'S DDL, AND WHY
--   The design snippets are a schema sketch. These make the file safe to
--   apply by hand, twice, to a database whose history nobody recorded. None
--   weakens a control and none changes a column, constraint, index or policy
--   the design specifies.
--
--   1. Preflight assertions (section 0). A readable RAISE naming the missing
--      dependency instead of a bare 42P01 from inside a REFERENCES clause,
--      plus the library_subscriptions policy/trigger/index inventory that
--      section 9 re-checks so this file can prove it changed no existing
--      control on that pre-existing table.
--
--   2. Shape assertions after each CREATE TABLE IF NOT EXISTS and after the
--      ADD COLUMNs (sections 1a, 2a, 3a, 4a), copied from 007 sections 1a-6a
--      and 004d section 1a. CREATE TABLE IF NOT EXISTS and ADD COLUMN IF NOT
--      EXISTS are SILENT when an object of that name already exists with a
--      different shape, so "the table is there" would not mean "the table is
--      usable" and the failure would surface much later as an amount stored
--      as text that sorts lexically, or a timestamp without time zone that
--      shifts every settlement by the server's UTC offset.
--
--   3. Guarded ADD CONSTRAINT blocks. A bare ADD CONSTRAINT is NOT
--      idempotent: a second run raises 42710 duplicate_object and aborts the
--      whole file. PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS.
--
--   4. pg_trigger-guarded CREATE TRIGGER blocks. PostgreSQL has no CREATE
--      TRIGGER IF NOT EXISTS and this repository's PostgreSQL baseline
--      predates CREATE OR REPLACE TRIGGER (PG14).
--
--   5. pg_policies-guarded CREATE POLICY blocks (section 6), the pattern 007
--      section 8, 004d section 3 and 004b section 4 already use, rather than
--      the DROP POLICY IF EXISTS ... CREATE POLICY of
--      migrations/006_reconcile_production_database.sql: a DROP would open a
--      window in which the table is policy-less, and on the pre-existing
--      library_subscriptions it would be a change to an existing control.
--
--   6. Table and column comments (section 8) and a postflight (section 9)
--      that proves the three tables, every named constraint, every named
--      index, every trigger and every policy exist, that the seed holds
--      exactly twelve rows, and that this file added no policy and no index
--      to library_subscriptions beyond idx_lib_subs_expiry and its two
--      triggers.
--
-- WHY THIS FILE REFUSES TO RUN WITHOUT library_subscriptions,
-- library_strategies AND MIGRATION 007
--   marketplace_settlements.subscription_id REFERENCES
--   library_subscriptions(id) and listing_id REFERENCES
--   library_strategies(id); the guard and the period mirror are triggers ON
--   library_subscriptions; the three new tables reuse
--   public.marketplace_touch_updated_at(), which
--   007_marketplace_submissions.sql defines. Creating these tables without
--   those present would either fail with a bare undefined_table from inside
--   a REFERENCES clause or - worse, if the FK were dropped to get past it -
--   produce a financial ledger whose rows point at nothing, which is
--   precisely the 42703/PGRST204 class of failure Requirement 24.9 exists to
--   prevent a repeat of. Section 0 stops with a message naming the file to
--   apply first.
--
--   DEPENDS ON backend_app/migrations/007_marketplace_submissions.sql, for
--   public.marketplace_touch_updated_at() (Requirement 24.5's update
--   timestamp on the three tables this file introduces). Section 0 asserts
--   007 has landed rather than re-defining the function here, because a
--   second definition of it is the drift this specification keeps refusing
--   to create.
--
-- APPLICATION
--   NOT applied automatically. There is no migration table in this
--   repository and .github/workflows/03-deploy.yml has no migration step:
--   migrations here are applied BY HAND, per file, and nothing records which
--   files an environment has run. Apply this file explicitly against the
--   target database, then run the verification queries at the bottom, the
--   way scripts/forensics/apply_migration_007.py applied the root
--   migrations/007.
--
--   Until it is applied, these tables and columns do not exist. Every code
--   path that reads or writes them must degrade the way
--   backend_app/backend/backtest_service.py already does: a WARNING NAMING
--   THIS FILE ("008_marketplace_settlement.sql"), not a 500, and never a
--   write lost to a table a hand-applied migration has not created yet. A
--   missing table surfaces from PostgREST as PGRST205 and as 42P01
--   undefined_table on a select; a missing column as PGRST204 and 42703.
--
-- SAFETY
--   * Additive only (Requirement 24.7), with the single documented exception
--     of the valid_subscription_status WIDENING described above - which
--     drops and re-adds one CHECK in one statement, to a strict superset of
--     its own value set, and touches no column and no row. No DROP TABLE, no
--     DROP COLUMN, no ALTER ... RENAME, no DELETE FROM, no TRUNCATE, no
--     UPDATE of an existing row. No existing column's type, nullability or
--     default is altered. No existing policy, trigger, index or grant is
--     created, altered or removed - the only pre-existing table touched is
--     library_subscriptions, and only by ADD COLUMN IF NOT EXISTS, three
--     guarded constraint blocks, one new index and two new triggers of its
--     own.
--   * Fully idempotent (Requirements 24.7, 24.8). CREATE TABLE IF NOT
--     EXISTS; ADD COLUMN IF NOT EXISTS; every ADD CONSTRAINT behind a
--     pg_constraint guard; CREATE INDEX IF NOT EXISTS; CREATE OR REPLACE
--     FUNCTION; every CREATE TRIGGER behind a pg_trigger guard; every CREATE
--     POLICY behind a pg_policies guard; the seed as INSERT ... ON CONFLICT
--     DO NOTHING; COMMENT replaces; GRANT and REVOKE are idempotent by
--     definition. A second application adds nothing and raises nothing.
--   * One transaction. Either all three tables, the twelve seed rows, the
--     twelve library_subscriptions columns, every constraint, index,
--     function, trigger, policy and grant exist, or none of them do. A
--     library_subscriptions carrying period columns but no
--     trg_subscription_transition_guard - a Subscription table on which a
--     free month is one UPDATE away - is never visible to a session.
--   * The seed is the only INSERT. It writes twelve rows of platform-global
--     reference data and no user data, and ON CONFLICT DO NOTHING makes a
--     re-run write nothing at all.

-- ==========================================================================
-- task 11.3: the Settlement and Subscription-lifecycle side of the Marketplace
-- ==========================================================================

BEGIN;

-- 0) Preflight ---------------------------------------------------------
-- Read-only assertions plus the library_subscriptions inventory. Fails with
-- a readable message instead of a bare undefined_table from inside a
-- REFERENCES clause, and records the policy/index/trigger counts of the one
-- pre-existing table this file touches so section 9 can prove it added
-- nothing there beyond its own one index and two triggers, and no policy at
-- all. No NAME is hard-coded, so an environment that renamed a policy cannot
-- false-alarm; every count is taken inside this one transaction, so they are
-- comparable by construction.
-- Idempotent: reads catalogues, writes nothing but three settings local to
-- this transaction.
DO $$
DECLARE
    ls_policies  INTEGER;
    ls_indexes   INTEGER;
    ls_triggers  INTEGER;
    ls_rls       BOOLEAN;
    missing      TEXT;
BEGIN
    IF to_regclass('public.library_subscriptions') IS NULL THEN
        RAISE EXCEPTION
            '008 precondition failed: table public.library_subscriptions '
            'does not exist. It is the single authoritative Subscription '
            'table (Requirement 1.1); marketplace_settlements and '
            'library_subscription_transitions reference it, and both of this '
            'file''s new triggers are ON it. Apply the '
            'library_subscriptions definition first '
            '(archived_migrations/root_migrations/'
            '007_create_library_subscriptions.sql, or '
            'migrations/006_reconcile_production_database.sql part 3). This '
            'file does not create it, because doing so would add a third '
            'divergent definition of the Subscription table - the exact '
            'failure mode backend_app/migrations/'
            '006_backtest_evidence_columns.sql exists to repair on '
            'strategy_backtests.';
    END IF;

    IF to_regclass('public.library_strategies') IS NULL THEN
        RAISE EXCEPTION
            '008 precondition failed: table public.library_strategies does '
            'not exist. marketplace_settlements.listing_id references it '
            'ON DELETE RESTRICT (Requirement 24.1). Apply '
            'archived_migrations/root_migrations/'
            '001_create_library_strategies.sql and then '
            'backend_app/migrations/007_marketplace_submissions.sql first.';
    END IF;

    IF to_regclass('auth.users') IS NULL THEN
        RAISE EXCEPTION
            '008 precondition failed: table auth.users does not exist. '
            'Requirement 21.2''s owner-scoped row level security resolves '
            'auth.uid() against it. This database is not a Supabase '
            'database, or the auth schema has not been provisioned.';
    END IF;

    -- Migration 007 must have landed: the three tables this file introduces
    -- reuse its public.marketplace_touch_updated_at() for Requirement 24.5's
    -- update timestamp. Defining a second copy here would be exactly the
    -- duplicate-definition drift this specification keeps refusing.
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.proname = 'marketplace_touch_updated_at'
    ) THEN
        RAISE EXCEPTION
            '008 precondition failed: function '
            'public.marketplace_touch_updated_at() does not exist. It is '
            'Requirement 24.5''s update-timestamp trigger function and it is '
            'defined by backend_app/migrations/'
            '007_marketplace_submissions.sql section 7a, which is this '
            'file''s declared dependency. Apply 007 first. This file does '
            'not re-define the function, because two definitions of one '
            'trigger function is the drift this specification exists to '
            'stop.';
    END IF;

    -- The library_subscriptions columns this file's constraints, guard and
    -- period mirror read or write. All are present in both existing
    -- definitions of the table, so an absence means it is in a shape this
    -- file was not written against and section 4 would fail with a message
    -- that does not name the cause.
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('id'), ('library_id'), ('user_id'), ('status'),
                   ('started_at'), ('expires_at'), ('cancelled_at'),
                   ('price_paid'), ('currency'))
             AS expected(column_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns c
                WHERE c.table_schema::TEXT = 'public'
                  AND c.table_name::TEXT   = 'library_subscriptions'
                  AND c.column_name::TEXT  = expected.column_name);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            '008 precondition failed: public.library_subscriptions is '
            'missing column(s) %. status is what the widened '
            'valid_subscription_status and trg_subscription_transition_guard '
            'act on; started_at and expires_at are the retained columns the '
            'period mirror writes (Requirement 25). Reconcile the table '
            'before applying this file.', missing;
    END IF;

    SELECT count(*) INTO ls_policies
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'library_subscriptions';

    SELECT count(*) INTO ls_indexes
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'library_subscriptions';

    SELECT count(*) INTO ls_triggers
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.library_subscriptions'::regclass
       AND NOT t.tgisinternal;

    SELECT c.relrowsecurity INTO ls_rls
      FROM pg_class c
     WHERE c.oid = 'public.library_subscriptions'::regclass;

    PERFORM set_config('aerora.lsub_policies_before', ls_policies::TEXT, true);
    PERFORM set_config('aerora.lsub_indexes_before',  ls_indexes::TEXT,  true);
    PERFORM set_config('aerora.lsub_triggers_before', ls_triggers::TEXT, true);

    RAISE NOTICE '008 preflight: public.library_subscriptions has RLS %, % '
                 'policies, % indexes and % user triggers. This file adds '
                 'no policy, 1 index (idx_lib_subs_expiry) and 2 triggers '
                 'there, and section 9 verifies all three.',
                 CASE WHEN ls_rls THEN 'enabled' ELSE 'DISABLED' END,
                 ls_policies, ls_indexes, ls_triggers;
END $$;

-- ==========================================================================
-- SECTION 1 - marketplace_subscription_allowed_transitions and its seed
-- ==========================================================================
-- Created FIRST because marketplace_subscription_guard() in section 5 selects
-- from it: the guard must never be able to exist while the table it consults
-- does not, or an illegal transition would raise 42P01 instead of 23514 and
-- the error the API translates would be the wrong one.
--
-- The primary key is COMPOSITE - (from_state, to_state) - and there is no id
-- column, which departs from the "every new table carries id UUID PRIMARY
-- KEY" rule design.md § Data Models states for the OWNED tables. Two
-- reasons, both load-bearing: the pair IS the identity of a transition, so a
-- surrogate key would permit the same edge to be seeded twice under two ids;
-- and INSERT ... ON CONFLICT DO NOTHING needs a unique index over exactly
-- those two columns to be idempotent at all. Same shape as 007 section 1.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS.
CREATE TABLE IF NOT EXISTS public.marketplace_subscription_allowed_transitions (
    from_state  TEXT NOT NULL,
    to_state    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_subscription_allowed_transitions PRIMARY KEY (from_state, to_state)
);

-- 1a) Shape assertion --------------------------------------------------
-- CREATE TABLE IF NOT EXISTS is silent about a pre-existing table of the
-- same name and a different shape. A from_state or to_state of a non-text
-- type would make the guard's equality comparison against
-- library_subscriptions.status a cast rather than a comparison; a missing
-- column would make the seed below fail with a message that does not name
-- the cause.
-- ::TEXT on every information_schema identifier: those columns are the
-- sql_identifier / character_data domains, not text, and an explicit cast
-- keeps the comparison a plain text compare on every server version.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    problems TEXT;
BEGIN
    SELECT string_agg(
               format('%s (expected %s, found %s)',
                      expected.column_name,
                      array_to_string(expected.accepted, ' or '),
                      coalesce(actual.data_type::TEXT, 'no such column')),
               '; ' ORDER BY expected.column_name)
      INTO problems
      FROM (VALUES
                ('from_state', ARRAY['text', 'character varying']),
                ('to_state',   ARRAY['text', 'character varying']),
                ('created_at', ARRAY['timestamp with time zone']),
                ('updated_at', ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'marketplace_subscription_allowed_transitions'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.marketplace_subscription_allowed_transitions has '
            'column(s) of the wrong shape: %. A pre-existing table of that '
            'name was left as it was, because CREATE TABLE IF NOT EXISTS '
            'does not alter one. Reconcile it by hand before re-running this '
            'migration.', problems;
    END IF;
END $$;

-- 1b) The twelve-pair seed ---------------------------------------------
-- Requirement 11.2's twelve permitted transitions, and no thirteenth. This
-- set MUST equal SUBSCRIPTION_TRANSITIONS - specifically its lowercase
-- projection SUBSCRIPTION_TRANSITION_TEXT_PAIRS - in
-- backend_app/backend/marketplace/subscription_state.py; task 14.6's
-- state-agreement test parses the VALUES list below and that Python constant
-- and fails if either holds a pair the other does not. Keep the two in step
-- or the test says so.
--
-- Written as one VALUES list rather than twelve statements so the parser has
-- one thing to read, and grouped by from_state in SUBSCRIPTION_TRANSITIONS'
-- own declaration order.
--
-- LOWERCASE, because these are library_subscriptions.status values and that
-- column is lowercase today - see "WHY THE SEED IS LOWERCASE" in the header.
--
-- 'refunded' appears only as a to_state, never as a from_state: it is the
-- one terminal state. No row has from_state = to_state, so a same-value
-- write is refused by exactly the rule that refuses any other illegal edge.
--
-- Idempotent: ON CONFLICT DO NOTHING against
-- pk_subscription_allowed_transitions. A re-run inserts nothing. NOTHING IS
-- EVER DELETED FROM HERE by this file - Requirement 24.7 forbids a DELETE,
-- so removing an edge in future is a new migration's job, not this one's
-- re-run.
INSERT INTO public.marketplace_subscription_allowed_transitions (from_state, to_state)
VALUES
    ('pending',        'active'),
    ('pending',        'payment_failed'),
    ('pending',        'cancelled'),
    ('active',         'expired'),
    ('active',         'cancelled'),
    ('active',         'refunded'),
    ('active',         'suspended'),
    ('expired',        'active'),
    ('cancelled',      'active'),
    ('suspended',      'active'),
    ('suspended',      'expired'),
    ('payment_failed', 'pending')
ON CONFLICT (from_state, to_state) DO NOTHING;

-- ==========================================================================
-- SECTION 2 - marketplace_settlements
-- ==========================================================================
-- design.md § Data Models -> "marketplace_settlements", column for column.
--
--   subscription_id     the Subscription this payment settles. ON DELETE
--                       RESTRICT, not CASCADE: Requirement 10.8 forbids
--                       deleting a Settlement_Record and Requirement 11.11
--                       forbids deleting a Subscription row, so a cascade
--                       would be a path to violating both (Requirement
--                       24.1's explicit per-relationship delete rule - this
--                       is the explicit choice, and its cost is that a paid
--                       Subscription cannot be deleted at all).
--   listing_id          the Listing the payment was for. ON DELETE RESTRICT
--                       for the same reason: a Listing with settled
--                       Subscriptions is a Listing whose financial history
--                       must survive it.
--   owner_id            the creator who receives owner_share_minor, and the
--                       RLS owner column of the earnings read (Requirement
--                       10.6). No foreign key: the authoritative link to the
--                       Listing owner is listing_id -> library_strategies,
--                       which already carries one; owner_id is the
--                       denormalised copy the earnings query filters on, and
--                       a second FK to auth.users would make closing a
--                       creator's account either impossible (RESTRICT) or a
--                       way to delete financial records (CASCADE).
--   purchaser_id        who paid. The second RLS policy's column, so a
--                       purchaser can read their own payments without being
--                       able to read the owner's earnings of anyone else.
--   amount_minor        the charged amount in Minor_Units (Requirements 9.2,
--                       10.1). BIGINT and never NUMERIC or a float: the
--                       domain of Requirement 10.1 reaches 99,999,999,999,
--                       which needs 11 digits and fits BIGINT exactly, and
--                       an exact integer is the only representation in which
--                       Requirement 10.2's conservation is checkable at all.
--   owner_share_minor   (amount * 90) / 100, integer division truncating
--   platform_fee_minor  toward zero, and amount - owner_share (Requirement
--                       10.1). Both stored, not derived, because Requirement
--                       10.7's per-currency total is a sum over the STORED
--                       values and a derived column would let the split rule
--                       change under an already-reported total.
--   currency            chk_settlement_currency, the two currencies money.py
--                       supports. Requirement 10.7 forbids combining
--                       currencies into one total, so the currency is on the
--                       row and on the earnings index.
--   provider            chk_settlement_provider, the two the
--                       Billing_Integration has. Requirement 9.1 forbids a
--                       third, and this constraint is that requirement
--                       written where a future handler cannot talk its way
--                       past it.
--   provider_reference  the provider transaction reference. Half of
--                       uq_settlement_reference_reversal, which is
--                       Requirement 9.7's "at most one Settlement_Record per
--                       provider transaction reference with a unique
--                       constraint" and the correctness path behind the
--                       webhook's Redis lock.
--   is_reversal         FALSE for a payment, TRUE for a refund (Requirement
--                       10.8). NOT NULL DEFAULT FALSE, so a row that forgot
--                       to say is a payment rather than a NULL that neither
--                       half of the earnings sum would count.
--   reverses_reference  the original provider_reference a reversal reverses,
--                       bound to is_reversal by
--                       chk_settlement_reversal_reference.
--   settled_at          the confirmation timestamp (Requirement 10.4), and
--                       the column trg_subscription_transition_guard
--                       compares against a Subscription's current
--                       period_expiry. TIMESTAMPTZ, so that comparison is
--                       between two instants and not between two local
--                       readings of a clock.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. The inline CONSTRAINT clauses are
-- the design's; section 2b re-asserts them under guards for the case where
-- this table already existed without them.
CREATE TABLE IF NOT EXISTS public.marketplace_settlements (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subscription_id     UUID NOT NULL REFERENCES public.library_subscriptions(id) ON DELETE RESTRICT,
    listing_id          UUID NOT NULL REFERENCES public.library_strategies(id) ON DELETE RESTRICT,
    owner_id            UUID NOT NULL,
    purchaser_id        UUID NOT NULL,

    amount_minor        BIGINT NOT NULL,
    owner_share_minor   BIGINT NOT NULL,
    platform_fee_minor  BIGINT NOT NULL,
    currency            TEXT NOT NULL,

    provider            TEXT NOT NULL,
    provider_reference  TEXT NOT NULL,
    is_reversal         BOOLEAN NOT NULL DEFAULT FALSE,
    reverses_reference  TEXT,

    settled_at          TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_settlement_amounts CHECK (
        amount_minor >= 0
        AND owner_share_minor >= 0
        AND platform_fee_minor >= 0),
    CONSTRAINT chk_settlement_conserved CHECK (
        owner_share_minor + platform_fee_minor = amount_minor),
    CONSTRAINT chk_settlement_currency CHECK (currency IN ('USD', 'INR')),
    CONSTRAINT chk_settlement_provider CHECK (provider IN ('stripe', 'razorpay')),
    CONSTRAINT chk_settlement_reversal_reference CHECK (
        (is_reversal = FALSE AND reverses_reference IS NULL)
        OR (is_reversal = TRUE AND reverses_reference IS NOT NULL)),
    CONSTRAINT uq_settlement_reference_reversal UNIQUE (provider_reference, is_reversal)
);

-- 2a) Shape assertion --------------------------------------------------
-- Each silence CREATE TABLE IF NOT EXISTS would otherwise keep matters, and
-- on a financial ledger each one is a wrong number paid to somebody:
--   * a NUMERIC or double precision amount_minor, owner_share_minor or
--     platform_fee_minor lets a fractional Minor_Unit be stored - half a
--     cent - which Requirements 10.1 and 10.3 forbid, and makes
--     chk_settlement_conserved a floating-point equality, i.e. not an
--     equality at all;
--   * a TEXT amount_minor sorts lexically ('9' > '137'), so the earnings sum
--     of Requirement 10.7 would be a string concatenation or an error;
--   * a timestamp WITHOUT time zone settled_at silently reinterprets every
--     confirmation instant as local time, which shifts the guard's
--     "settled_at at or after period_expiry" comparison by the server's UTC
--     offset - up to fourteen hours of free subscription. That type is
--     deliberately NOT accepted: it is not equivalent, it is a silent shift;
--   * a non-boolean is_reversal breaks both halves of
--     uq_settlement_reference_reversal and lets a refund be counted as a
--     payment;
--   * a non-uuid subscription_id makes the guard's
--     "s.subscription_id = NEW.id" an equality over truncated text.
-- 'character varying' is accepted wherever 'text' is expected - a VARCHAR(n)
-- is what a hand-added column most likely is. 'integer' is accepted
-- alongside 'bigint' only where the domain fits it; it does not for
-- amount_minor (Requirement 10.1 reaches 99,999,999,999), so integer is
-- refused there. 'double precision' and 'numeric' are refused for every
-- amount column.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    problems TEXT;
BEGIN
    SELECT string_agg(
               format('%s (expected %s, found %s)',
                      expected.column_name,
                      array_to_string(expected.accepted, ' or '),
                      coalesce(actual.data_type::TEXT, 'no such column')),
               '; ' ORDER BY expected.column_name)
      INTO problems
      FROM (VALUES
                ('id',                 ARRAY['uuid']),
                ('subscription_id',    ARRAY['uuid']),
                ('listing_id',         ARRAY['uuid']),
                ('owner_id',           ARRAY['uuid']),
                ('purchaser_id',       ARRAY['uuid']),
                ('amount_minor',       ARRAY['bigint']),
                ('owner_share_minor',  ARRAY['bigint']),
                ('platform_fee_minor', ARRAY['bigint']),
                ('currency',           ARRAY['text', 'character varying']),
                ('provider',           ARRAY['text', 'character varying']),
                ('provider_reference', ARRAY['text', 'character varying']),
                ('is_reversal',        ARRAY['boolean']),
                ('reverses_reference', ARRAY['text', 'character varying']),
                ('settled_at',         ARRAY['timestamp with time zone']),
                ('created_at',         ARRAY['timestamp with time zone']),
                ('updated_at',         ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'marketplace_settlements'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.marketplace_settlements has column(s) of the wrong '
            'shape: %. This table is the Settlement_Ledger: every earnings '
            'figure an owner is shown is a sum over these columns '
            '(Requirements 10.6, 10.7), and a wrong type here is a wrong '
            'amount paid to a real person. A pre-existing table of that name '
            'was left as it was, because CREATE TABLE IF NOT EXISTS does not '
            'alter one. Reconcile it by hand before re-running this '
            'migration.', problems;
    END IF;
END $$;

-- 2b) Constraint guards ------------------------------------------------
-- No-ops on a fresh run: all six were declared inline above. They exist so
-- that a marketplace_settlements created earlier WITHOUT them gains them
-- rather than silently keeping the invariants unenforced.
--
-- Idempotent: each ADD CONSTRAINT runs only when pg_constraint holds no
-- constraint of that name on this table, so a re-run adds nothing and cannot
-- raise 42710 duplicate_object.
DO $$
BEGIN
    -- Requirement 10.5's "SHALL enforce amount >= 0, owner_share >= 0,
    -- platform_fee >= 0". Written as one constraint rather than three so a
    -- violation names the non-negativity rule rather than one of its three
    -- clauses. Zero is admitted, not one: Requirement 10.1's domain starts
    -- at 0, and a zero-amount confirmation is a real provider event that
    -- must be recordable rather than silently dropped.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.marketplace_settlements'::regclass
           AND conname  = 'chk_settlement_amounts'
    ) THEN
        ALTER TABLE public.marketplace_settlements
            ADD CONSTRAINT chk_settlement_amounts CHECK (
                amount_minor >= 0
                AND owner_share_minor >= 0
                AND platform_fee_minor >= 0);
        RAISE NOTICE 'Added chk_settlement_amounts to '
                     'public.marketplace_settlements (Requirement 10.5).';
    END IF;

    -- Requirement 10.2's "owner_share + platform_fee = amount exactly, for
    -- every payment amount, with no rounding remainder unaccounted for",
    -- enforced BY THE DATABASE (Requirement 10.5) and not only by
    -- money.split_ninety_ten. This is the constraint that makes the ledger
    -- balance to the minor unit provable rather than asserted: a row that
    -- loses a cent to rounding cannot be stored at all. It holds for a
    -- reversal too, which is why Requirement 10.8 states the same identity
    -- for the reversal magnitudes.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.marketplace_settlements'::regclass
           AND conname  = 'chk_settlement_conserved'
    ) THEN
        ALTER TABLE public.marketplace_settlements
            ADD CONSTRAINT chk_settlement_conserved CHECK (
                owner_share_minor + platform_fee_minor = amount_minor);
        RAISE NOTICE 'Added chk_settlement_conserved to '
                     'public.marketplace_settlements (Requirements 10.2, '
                     '10.5 - the split is conserved to the minor unit).';
    END IF;

    -- The two currencies money.py supports, both with ISO 4217 minor-unit
    -- exponent 2. Requirement 10.7 forbids combining currencies into one
    -- total, so an unrecognised currency here would be an amount no earnings
    -- figure could report; refusing it at write time is the honest place.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.marketplace_settlements'::regclass
           AND conname  = 'chk_settlement_currency'
    ) THEN
        ALTER TABLE public.marketplace_settlements
            ADD CONSTRAINT chk_settlement_currency
                CHECK (currency IN ('USD', 'INR'));
        RAISE NOTICE 'Added chk_settlement_currency to '
                     'public.marketplace_settlements (USD, INR).';
    END IF;

    -- Requirement 9.1's "SHALL NOT introduce a second payment provider
    -- integration", read from the persistence side: a settlement can only
    -- have come from one of the two providers the Billing_Integration
    -- already has. A third provider is then a visible, deliberate edit here
    -- rather than a value that appears in the ledger one day.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.marketplace_settlements'::regclass
           AND conname  = 'chk_settlement_provider'
    ) THEN
        ALTER TABLE public.marketplace_settlements
            ADD CONSTRAINT chk_settlement_provider
                CHECK (provider IN ('stripe', 'razorpay'));
        RAISE NOTICE 'Added chk_settlement_provider to '
                     'public.marketplace_settlements (stripe, razorpay; '
                     'Requirement 9.1).';
    END IF;

    -- Requirement 10.8's "WHERE a payment is refunded, THE Marketplace SHALL
    -- record the reversal as an additional Settlement_Record that references
    -- the original provider transaction reference". Both directions are
    -- enforced: a reversal without the original reference cannot be stored,
    -- and neither can a non-reversal that carries one - which would be a row
    -- claiming to reverse something while counting as a payment in the
    -- earnings sum of Requirement 10.7.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.marketplace_settlements'::regclass
           AND conname  = 'chk_settlement_reversal_reference'
    ) THEN
        ALTER TABLE public.marketplace_settlements
            ADD CONSTRAINT chk_settlement_reversal_reference CHECK (
                (is_reversal = FALSE AND reverses_reference IS NULL)
                OR (is_reversal = TRUE AND reverses_reference IS NOT NULL));
        RAISE NOTICE 'Added chk_settlement_reversal_reference to '
                     'public.marketplace_settlements (Requirement 10.8).';
    END IF;

    -- Requirements 9.7 and 10.5, and Requirement 24.3's list of uniqueness
    -- constraints this specification must apply. THIS IS THE CORRECTNESS
    -- PATH FOR WEBHOOK IDEMPOTENCY: N duplicate deliveries of one payment
    -- confirmation end with one row and N-1 unique violations, which
    -- settlement_service translates into an audit entry recording a
    -- duplicate (Requirement 10.10) while the period is extended exactly
    -- once (Requirements 9.6, P-6). The Redis lock in
    -- stripe_webhook/razorpay_webhook stays as the fast path; this
    -- constraint is what remains true when that lock expires mid-flight -
    -- the same division of responsibility
    -- 005b_signal_lifecycle_and_idempotency.sql documents for
    -- uq_signals_idempotency_key.
    --
    -- The key includes is_reversal because a refund legitimately shares the
    -- original payment's provider reference in the sense that it points at
    -- it; keying on provider_reference alone would make recording the
    -- reversal of Requirement 10.8 impossible.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.marketplace_settlements'::regclass
           AND conname  = 'uq_settlement_reference_reversal'
    ) THEN
        ALTER TABLE public.marketplace_settlements
            ADD CONSTRAINT uq_settlement_reference_reversal
                UNIQUE (provider_reference, is_reversal);
        RAISE NOTICE 'Added uq_settlement_reference_reversal (Requirements '
                     '9.7, 10.5, 24.3).';
    END IF;
END $$;

-- 2c) Indexes ----------------------------------------------------------
-- design.md § Data Models: idx_settlements_owner_currency ON (owner_id,
-- currency, settled_at DESC). This is Requirement 24.4's "Settlement_Record
-- lookup by owner" and the design's creator-earnings read verbatim -
-- "SELECT currency, is_reversal, owner_share_minor, platform_fee_minor,
-- amount_minor FROM marketplace_settlements WHERE owner_id = :owner_id" in
-- ONE round trip (design.md § round-trip budget: Creator earnings = 1) -
-- with currency second because Requirement 10.7 forbids combining currencies
-- into one total, so the per-currency grouping is part of the access
-- pattern and not a post-filter. The leading column is also the one the RLS
-- owner policy filters on.
CREATE INDEX IF NOT EXISTS idx_settlements_owner_currency
    ON public.marketplace_settlements (owner_id, currency, settled_at DESC);

-- TWO INDEXES BEYOND THE DESIGN'S NAMED ONE, both on a hot path the design
-- describes but does not index:
--
-- (a) trg_subscription_transition_guard's activation check is
--     "EXISTS (SELECT 1 FROM marketplace_settlements WHERE subscription_id =
--     NEW.id AND NOT is_reversal AND settled_at >= OLD.period_expiry)",
--     which runs on EVERY status change of EVERY subscription. Without this
--     index that is a sequential scan of the whole ledger per transition,
--     and the ledger only grows. settled_at DESC is part of the index
--     because the guard wants the NEWEST qualifying settlement, so the
--     answer is the first row of the scan. It also serves the ON DELETE
--     RESTRICT check on subscription_id, which PostgreSQL performs as a
--     lookup on the referencing column.
CREATE INDEX IF NOT EXISTS idx_settlements_subscription
    ON public.marketplace_settlements (subscription_id, settled_at DESC);

-- (b) the purchaser RLS policy filters on purchaser_id, and Requirement
--     21.5 requires a list query to be scoped by the authenticated identity
--     IN THE QUERY. Without this index every policy-scoped purchaser read is
--     a sequential scan.
CREATE INDEX IF NOT EXISTS idx_settlements_purchaser
    ON public.marketplace_settlements (purchaser_id, settled_at DESC);

-- The ON DELETE RESTRICT check on listing_id is a lookup on the referencing
-- column too, and it runs on every attempt to delete a Listing.
CREATE INDEX IF NOT EXISTS idx_settlements_listing
    ON public.marketplace_settlements (listing_id);

-- ==========================================================================
-- SECTION 3 - library_subscription_transitions
-- ==========================================================================
-- design.md § Data Models -> "library_subscription_transitions".
-- Requirement 11.12's record of every Subscription_State transition, every
-- period extension and every entitlement change: the prior value, the new
-- value, the cause, the acting identity where one applies, and the UTC
-- timestamp.
--
-- prior_period_expiry and new_period_expiry are what make it a record of
-- every PERIOD EXTENSION and not only of every state change. A renewal that
-- moves 'active' -> 'active' is not a transition at all (the guard's first
-- branch returns early, and the seed holds no self-edge), so without these
-- two columns an extension would leave no trace anywhere - and Requirement
-- 11.12 asks for all three kinds of event in one place. Both are NULLABLE:
-- a transition that changes no period - a suspension, say - records NULL in
-- both rather than repeating the unchanged value twice, so "the period moved"
-- and "the period did not" are distinguishable by reading the row.
--
-- user_id is DENORMALISED onto this table rather than resolved through
-- subscription_id. It is the RLS owner column, and an owner policy that had
-- to reach library_subscriptions to find the purchaser would either need a
-- subquery in the policy predicate - evaluated per row, on every read - or a
-- SECURITY DEFINER helper, which is a privilege-escalation surface. Copying
-- the purchaser is the cheaper and smaller-blast-radius answer, and it cannot
-- drift because this table is append-only. It is named user_id and not
-- purchaser_id because library_subscriptions calls that column user_id and
-- the two must read the same.
--
-- cause is TEXT NOT NULL with NO CHECK. The vocabulary of causes is the
-- application's - 'payment_confirmed', 'refund', 'expiry_sweep',
-- 'purchaser_cancelled', 'admin_suspend' - and it will grow with the
-- lifecycle; enumerating it here would make adding an audited cause a schema
-- change, and a cause is a description of why, not a value any invariant is
-- computed from. NOT NULL, though: a transition with no recorded cause is
-- exactly the entry Requirement 11.12 would not accept.
--
-- from_state and to_state carry NO CHECK over the seven values, deliberately.
-- This is a HISTORY table: if a future migration retires a state, the rows
-- recording transitions into it must remain readable, and a CHECK
-- enumerating today's vocabulary would make that migration a choice between
-- dropping a constraint and losing history. The vocabulary is enforced where
-- it is written - valid_subscription_status on the live row - and Requirement
-- 24.2 asks for the check on Subscription_State, which is that column, not on
-- its audit trail. Same reading as 007 section 3.
--
-- ON DELETE CASCADE from library_subscriptions, and RESTRICT would be wrong
-- here even though it is right on marketplace_settlements: Requirement 11.11
-- forbids deleting a Subscription row, and marketplace_settlements' RESTRICT
-- already makes deleting a PAID one impossible, so the only Subscription that
-- can still be deleted is one that was never settled - a PENDING row whose
-- checkout never completed, which Requirement 9.4 says must not be deleted
-- either but which no financial record depends on. Its transition history has
-- no meaning without it. The append-only guard of section 5b still refuses
-- every direct DELETE; only the declared cascade passes.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS.
CREATE TABLE IF NOT EXISTS public.library_subscription_transitions (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subscription_id      UUID NOT NULL REFERENCES public.library_subscriptions(id) ON DELETE CASCADE,
    user_id              UUID NOT NULL,
    from_state           TEXT NOT NULL,
    to_state             TEXT NOT NULL,
    cause                TEXT NOT NULL,
    actor_id             UUID,
    prior_period_expiry  TIMESTAMPTZ,
    new_period_expiry    TIMESTAMPTZ,
    transitioned_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3a) Shape assertion --------------------------------------------------
-- The silences that matter here: a timestamp WITHOUT time zone
-- transitioned_at, prior_period_expiry or new_period_expiry reinterprets
-- every instant as local time, and Requirement 11.12's "the UTC timestamp"
-- is then wrong by the server's offset; a non-uuid subscription_id makes the
-- foreign key and the history read an equality over truncated text.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    problems TEXT;
BEGIN
    SELECT string_agg(
               format('%s (expected %s, found %s)',
                      expected.column_name,
                      array_to_string(expected.accepted, ' or '),
                      coalesce(actual.data_type::TEXT, 'no such column')),
               '; ' ORDER BY expected.column_name)
      INTO problems
      FROM (VALUES
                ('id',                  ARRAY['uuid']),
                ('subscription_id',     ARRAY['uuid']),
                ('user_id',             ARRAY['uuid']),
                ('from_state',          ARRAY['text', 'character varying']),
                ('to_state',            ARRAY['text', 'character varying']),
                ('cause',               ARRAY['text', 'character varying']),
                ('actor_id',            ARRAY['uuid']),
                ('prior_period_expiry', ARRAY['timestamp with time zone']),
                ('new_period_expiry',   ARRAY['timestamp with time zone']),
                ('transitioned_at',     ARRAY['timestamp with time zone']),
                ('created_at',          ARRAY['timestamp with time zone']),
                ('updated_at',          ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'library_subscription_transitions'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.library_subscription_transitions has column(s) of the '
            'wrong shape: %. Requirement 11.12''s history must be readable '
            'in timestamp order with a real timestamptz; reconcile the table '
            'by hand before re-running this migration.', problems;
    END IF;
END $$;

-- 3b) Indexes ----------------------------------------------------------
-- The per-subscription history read, in the ascending order an audit trail
-- is read in. ASC is written explicitly rather than left to the default so
-- that the index and the intent read the same way. The leading column also
-- serves the ON DELETE CASCADE from library_subscriptions, which without an
-- index on the referencing column is a sequential scan per deleted row.
CREATE INDEX IF NOT EXISTS idx_lib_sub_transitions
    ON public.library_subscription_transitions (subscription_id, transitioned_at ASC);

-- The column the RLS owner policy filters on, and the purchaser's own
-- history read (Requirement 21.5's "scope every list query by the
-- authenticated identity in the query itself").
CREATE INDEX IF NOT EXISTS idx_lib_sub_transitions_user
    ON public.library_subscription_transitions (user_id, transitioned_at DESC);

-- ==========================================================================
-- SECTION 4 - library_subscriptions: twelve additive columns, the widened
--             status CHECK, two checks and one partial index
-- ==========================================================================
-- design.md § Data Models -> "Additive columns on existing tables", the
-- library_subscriptions row.
--
--   owner_id            the Listing owner who receives owner_share_minor.
--                       Denormalised from library_strategies.author_id so
--                       that a purchase records WHO WAS THE OWNER AT THE
--                       INSTANT OF PURCHASE (Requirement 9.3 lists "the
--                       owner" among the values recorded then), which a join
--                       through library_id would silently re-answer if the
--                       Listing changed hands. NULLABLE: every pre-existing
--                       row predates the concept, and back-filling an owner
--                       onto historical rows would be this migration
--                       inventing a fact.
--   price_minor         the price in Minor_Units charged for this
--                       Subscription, frozen at creation (Requirement 9.2's
--                       "from the Listing price persisted at the instant the
--                       Subscription is created"). BIGINT, so
--                       settlement_service compares the provider's amount
--                       against a stored integer and no float appears in the
--                       comparison Requirement 9.14 makes a rejection turn
--                       on. NULLABLE for the same reason as owner_id; the
--                       retained price_paid NUMERIC(10,2) is unchanged and
--                       is not authoritative.
--   owner_share_minor   the computed split, recorded at creation
--   platform_fee_minor  (Requirement 9.3), and bound to price_minor by
--                       chk_ls_split_conserved.
--   period_start        the Subscription_Period (Requirements 11.4, 11.5).
--   period_expiry       THE AUTHORITY ON ENTITLEMENT (Requirement 11.7):
--                       entitlement_resolver compares now with period_expiry
--                       directly, so a dead expiry sweep cannot extend
--                       access and the stored status label is a lagging
--                       projection rather than the truth. TIMESTAMPTZ, so
--                       that comparison is between instants.
--   provider            which provider the checkout was created with, and
--   provider_reference  the provider transaction reference recorded against
--                       the PENDING row before the checkout response is
--   provider_session_at returned (Requirement 9.12), with the session
--                       creation timestamp.
--   failure_cause       Requirement 9.4's failure record: when provider
--   failed_at           session creation fails after the PENDING row is
--                       written, the row is UPDATED to 'payment_failed' with
--                       these two set - it is NOT DELETED, which is what the
--                       existing handler's
--                       ".delete().eq("id", sub_id)" inside a bare
--                       "except: pass" does today and what Requirement 9.4
--                       forbids.
--   renewal_enabled     Requirement 11.9's "SHALL perform no further renewal
--                       for that Subscription". NOT NULL DEFAULT TRUE and
--                       existing rows back-filled to TRUE, because the
--                       existing default behaviour is that a subscription
--                       renews and the migration must not silently cancel
--                       renewal for every current subscriber. On PostgreSQL
--                       11+ a NOT NULL column with a constant default
--                       back-fills without rewriting the table.
--
-- started_at, expires_at, price_paid, currency and status are RETAINED,
-- untouched. started_at and expires_at are MIRRORED FROM the period columns
-- by the trigger in section 5d. Requirement 25 and 24.7: library.py's
-- check_deployment_permission reads status and expires_at today, and
-- billing.py's _apply_marketplace_entitlement writes status; both keep
-- working unchanged in MEANING.
--
-- NOTE ON auto_renew: migrations/006_reconcile_production_database.sql's
-- shape of this table carries "auto_renew BOOLEAN DEFAULT TRUE" while the
-- root-migration shape does not. renewal_enabled is added regardless and is
-- the column this specification reads and writes, because it must exist in
-- BOTH shapes and because a nullable auto_renew makes Requirement 11.9's
-- "no further renewal" depend on every reader's coalesce. auto_renew is left
-- exactly as it is wherever it exists - not dropped, not renamed, not read.
-- Two columns saying similar things is the lesser defect; rewriting a column
-- half the environments do not have is the greater one.
--
-- Every column is added with ADD COLUMN IF NOT EXISTS, so a re-run skips each
-- one already present and nothing existing is altered.
ALTER TABLE public.library_subscriptions
    ADD COLUMN IF NOT EXISTS owner_id             UUID,
    ADD COLUMN IF NOT EXISTS price_minor          BIGINT,
    ADD COLUMN IF NOT EXISTS owner_share_minor    BIGINT,
    ADD COLUMN IF NOT EXISTS platform_fee_minor   BIGINT,
    ADD COLUMN IF NOT EXISTS period_start         TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS period_expiry        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS provider             TEXT,
    ADD COLUMN IF NOT EXISTS provider_reference   TEXT,
    ADD COLUMN IF NOT EXISTS provider_session_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS failure_cause        TEXT,
    ADD COLUMN IF NOT EXISTS failed_at            TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS renewal_enabled      BOOLEAN NOT NULL DEFAULT TRUE;

-- 4a) Column shape assertion -------------------------------------------
-- ADD COLUMN IF NOT EXISTS is SILENT when a column of that name already
-- exists with a different type - one added by hand during an investigation,
-- say. Each silence here is a defect:
--   * a NUMERIC or double precision price_minor, owner_share_minor or
--     platform_fee_minor lets a fractional Minor_Unit be stored, so
--     Requirement 9.14's amount comparison and chk_ls_split_conserved become
--     floating-point equalities;
--   * a timestamp WITHOUT time zone period_expiry shifts THE AUTHORITY ON
--     ENTITLEMENT by the server's UTC offset - up to fourteen hours of free
--     or lost access - and shifts the guard's comparison against
--     marketplace_settlements.settled_at by the same amount;
--   * a NULLABLE renewal_enabled makes Requirement 11.9's "no further
--     renewal" depend on every reader's coalesce.
-- The NOT NULL and the DEFAULT of renewal_enabled are asserted too, not just
-- its type, because those two ARE the requirement in that case.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    problems     TEXT;
    renewal_null TEXT;
    renewal_dflt TEXT;
BEGIN
    SELECT string_agg(
               format('%s (expected %s, found %s)',
                      expected.column_name,
                      array_to_string(expected.accepted, ' or '),
                      coalesce(actual.data_type::TEXT, 'no such column')),
               '; ' ORDER BY expected.column_name)
      INTO problems
      FROM (VALUES
                ('owner_id',            ARRAY['uuid']),
                ('price_minor',         ARRAY['bigint']),
                ('owner_share_minor',   ARRAY['bigint']),
                ('platform_fee_minor',  ARRAY['bigint']),
                ('period_start',        ARRAY['timestamp with time zone']),
                ('period_expiry',       ARRAY['timestamp with time zone']),
                ('provider',            ARRAY['text', 'character varying']),
                ('provider_reference',  ARRAY['text', 'character varying']),
                ('provider_session_at', ARRAY['timestamp with time zone']),
                ('failure_cause',       ARRAY['text', 'character varying']),
                ('failed_at',           ARRAY['timestamp with time zone']),
                ('renewal_enabled',     ARRAY['boolean']),
                -- the retained columns the period mirror and the guard read
                ('status',              ARRAY['text', 'character varying']),
                ('started_at',          ARRAY['timestamp with time zone']),
                ('expires_at',          ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'library_subscriptions'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.library_subscriptions has subscription column(s) of the '
            'wrong shape: %. A pre-existing column of that name was left as '
            'it was, because ADD COLUMN IF NOT EXISTS does not alter one, so '
            '"the column exists" would not have meant "the column is usable" '
            '(Requirements 11.7, 11.13, 24.9). Reconcile it by hand before '
            're-running this migration.', problems;
    END IF;

    SELECT c.is_nullable::TEXT, coalesce(c.column_default::TEXT, '(none)')
      INTO renewal_null, renewal_dflt
      FROM information_schema.columns c
     WHERE c.table_schema::TEXT = 'public'
       AND c.table_name::TEXT   = 'library_subscriptions'
       AND c.column_name::TEXT  = 'renewal_enabled';

    IF renewal_null <> 'NO' OR renewal_dflt NOT ILIKE '%true%' THEN
        RAISE EXCEPTION
            'public.library_subscriptions.renewal_enabled is nullable=% with '
            'default %, but it must be NOT NULL DEFAULT TRUE: the existing '
            'behaviour of every current subscription is that it renews, and '
            'a nullable or false-defaulted column of that name would make '
            'this migration silently cancel renewal for every current '
            'subscriber, or leave Requirement 11.9''s "no further renewal" '
            'to each reader''s coalesce. A pre-existing column was left as '
            'it was; reconcile it by hand.', renewal_null, renewal_dflt;
    END IF;
END $$;

-- 4b) valid_subscription_status, widened to the seven lowercase spellings --
-- Requirements 11.1, 11.13, 24.2. The seven values are
-- SUBSCRIPTION_STATUS_VALUES in
-- backend_app/backend/marketplace/subscription_state.py, in Requirement
-- 11.1's own order: the four already stored today, reproduced EXACTLY, plus
-- 'refunded', 'payment_failed' and 'suspended'.
--
-- THIS IS THE ONE DROP IN THIS FILE, and the header section "WHY
-- valid_subscription_status IS THE ONE DROP IN THIS FILE" is the full
-- argument. In short: PostgreSQL has no ALTER CONSTRAINT for a CHECK, so
-- widening one is a DROP and an ADD and there is no third form; keeping the
-- four-value constraint alongside a seven-value one would widen nothing,
-- because the old one would still refuse 'refunded'.
--
-- The four narrowing safeguards, all present below:
--   1. ONE ALTER TABLE with two actions, so status is never unconstrained -
--      not even for an instant inside this transaction.
--   2. The new set is a strict SUPERSET, so no row legal before is illegal
--      after and no existing predicate changes meaning (Requirement 25).
--   3. The file PROVES it is a widening first, by reading the distinct
--      stored statuses. If one lies outside the seven it adds the constraint
--      NOT VALID and NAMES the offending values in a NOTICE rather than
--      aborting - Requirement 24.8's "applies from a database at the current
--      production schema revision". NOT VALID is not weaker for new data:
--      PostgreSQL enforces a NOT VALID CHECK on every subsequent INSERT and
--      UPDATE; it only means the rows already there were not examined.
--   4. If no valid_subscription_status exists at all - the shape
--      migrations/006_reconcile_production_database.sql creates, which
--      carries no such constraint - NOTHING IS DROPPED and the ADD is purely
--      additive.
--
-- Idempotent twice over: the widening branch recognises a constraint that
-- already admits all seven values and leaves it entirely alone, so a re-run
-- drops nothing; and the best-effort VALIDATE is reached on every run, so an
-- operator who has just reconciled the offending rows gets the constraint
-- validated by re-applying this file.
DO $$
DECLARE
    seven_values CONSTANT TEXT :=
        '''pending'', ''active'', ''expired'', ''cancelled'', ''refunded'', ''payment_failed'', ''suspended''';
    existing_def TEXT;
    add_action   TEXT;
    outside      TEXT;
BEGIN
    SELECT pg_get_constraintdef(c.oid) INTO existing_def
      FROM pg_constraint c
     WHERE c.conrelid = 'public.library_subscriptions'::regclass
       AND c.conname  = 'valid_subscription_status';

    IF existing_def IS NOT NULL
       AND existing_def LIKE '%''refunded''%'
       AND existing_def LIKE '%''payment_failed''%'
       AND existing_def LIKE '%''suspended''%' THEN
        -- Already widened, by a previous run of this very file. NOTHING IS
        -- DROPPED on a re-run.
        RAISE NOTICE 'valid_subscription_status already admits ''refunded'', '
                     '''payment_failed'' and ''suspended''; left entirely '
                     'unchanged. Definition: %', existing_def;
    ELSE
        -- Prove the widening is a widening before performing it. A status
        -- outside the seven is a value the Subscription_State machine cannot
        -- name at all, so it is reported rather than either aborted over
        -- (which Requirement 24.8 forbids) or rewritten (which Requirement
        -- 24.7 forbids).
        SELECT string_agg(DISTINCT quote_literal(s.status), ', ')
          INTO outside
          FROM public.library_subscriptions s
         WHERE s.status IS NOT NULL
           AND s.status <> ALL (ARRAY['pending', 'active', 'expired',
                                      'cancelled', 'refunded',
                                      'payment_failed', 'suspended']);

        add_action := 'ADD CONSTRAINT valid_subscription_status '
                      || 'CHECK (status IN (' || seven_values || '))';

        IF outside IS NOT NULL THEN
            add_action := add_action || ' NOT VALID';
            RAISE NOTICE
                'public.library_subscriptions holds status value(s) % outside '
                'the 7 of Requirement 11.1, so valid_subscription_status is '
                'being added NOT VALID: it IS enforced for every new INSERT '
                'and UPDATE from this commit onward, and those existing rows '
                'were left untouched because Requirement 24.7 forbids this '
                'file from rewriting one. Reconcile them by hand (map each to '
                'one of the 7 lowercase spellings STATUS_TEXT_FOR_STATE in '
                'backend_app/backend/marketplace/subscription_state.py '
                'defines) and re-run this file to validate.', outside;
        END IF;

        IF existing_def IS NULL THEN
            -- Nothing to drop. Purely additive.
            EXECUTE 'ALTER TABLE public.library_subscriptions ' || add_action;
            RAISE NOTICE 'Added valid_subscription_status to '
                         'public.library_subscriptions over the 7 lowercase '
                         'spellings of Requirement 11.1. No constraint of '
                         'that name existed (the '
                         'migrations/006_reconcile_production_database.sql '
                         'shape), so nothing was dropped.';
        ELSE
            -- ONE statement, two actions. There is no instant at which status
            -- is unconstrained.
            EXECUTE 'ALTER TABLE public.library_subscriptions '
                    || 'DROP CONSTRAINT valid_subscription_status, '
                    || add_action;
            RAISE NOTICE 'Widened valid_subscription_status on '
                         'public.library_subscriptions from % to the 7 '
                         'lowercase spellings of Requirement 11.1, in ONE '
                         'ALTER TABLE statement, to a strict superset of its '
                         'own value set. No existing value was renamed, '
                         'removed or reinterpreted.', existing_def;
        END IF;
    END IF;

    -- Best-effort validation for the NOT VALID case, and a no-op otherwise.
    -- Inside a nested BEGIN ... EXCEPTION block, which is a savepoint: a
    -- failure rolls back only the VALIDATE, leaves the constraint in place
    -- and lets this migration still commit. Same shape as 007 section 6c.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid    = 'public.library_subscriptions'::regclass
           AND conname     = 'valid_subscription_status'
           AND convalidated
    ) THEN
        BEGIN
            ALTER TABLE public.library_subscriptions
                VALIDATE CONSTRAINT valid_subscription_status;
            RAISE NOTICE 'valid_subscription_status validated: every stored '
                         'status is one of the 7.';
        EXCEPTION
            WHEN check_violation THEN
                RAISE NOTICE 'valid_subscription_status could NOT be '
                             'validated; it stays in place and IS enforced '
                             'for every new write. See the NOTICE above for '
                             'the offending values.';
        END;
    END IF;
END $$;

-- 4c) chk_ls_active_has_period -----------------------------------------
-- design.md § "marketplace/subscription_state.py":
--   chk_ls_active_has_period CHECK (status <> 'active' OR (period_start IS
--     NOT NULL AND period_expiry IS NOT NULL AND period_expiry >
--     period_start))
--
-- Requirement 11.13's second half - "SHALL enforce that an ACTIVE
-- Subscription has a non-null period start and a non-null expiry strictly
-- greater than that start" - and Property P-10. An 'active' row with no
-- expiry is an unbounded entitlement: entitlement_resolver compares now with
-- period_expiry (Requirement 11.7), and NULL compares false, so such a row
-- would either entitle forever or not at all depending on how each reader
-- spelled the comparison. This constraint makes it unrepresentable.
--
-- The name keeps the design's "chk_ls_" prefix even though on this table
-- "ls_" would more naturally read as library_strategies. It is the design's
-- name and the name task 11.3 and the postflight both look for; renaming it
-- to be tidier would put a third spelling of one constraint into circulation.
--
-- ADDED "NOT VALID" FIRST, THEN VALIDATED BEST-EFFORT, for the reason 007
-- section 6c records for chk_ls_featured_requires_published: period_start and
-- period_expiry were added NULLABLE in section 4 above, so EVERY pre-existing
-- row holds NULL in both - and archived_migrations/root_migrations/
-- 007_create_library_subscriptions.sql defaults status to 'active'. A
-- production library_subscriptions therefore almost certainly holds 'active'
-- rows this constraint refuses, and a plain validated ADD CONSTRAINT would
-- abort this whole transaction, which Requirement 24.8 forbids.
--
-- NOT VALID is not weaker for new data: every subsequent INSERT and UPDATE is
-- checked. On an empty database the VALIDATE succeeds and the constraint ends
-- up ordinary.
--
-- This file does NOT back-fill a period onto those rows. Inventing a start
-- and an expiry for a subscription nobody paid for through this system would
-- be a migration granting entitlement, and Requirement 24.7's additive-only
-- rule is the same rule read from the other side. The NOTICE names the count
-- so an operator can reconcile them.
--
-- Idempotent twice over: the ADD is behind a pg_constraint name guard, and
-- the VALIDATE is behind a convalidated guard, so a re-run on a database
-- where it is already valid does nothing and a re-run where it is still NOT
-- VALID re-attempts the validation - which is what an operator who has just
-- reconciled the legacy rows wants.
DO $$
DECLARE
    offenders BIGINT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.library_subscriptions'::regclass
           AND conname  = 'chk_ls_active_has_period'
    ) THEN
        ALTER TABLE public.library_subscriptions
            ADD CONSTRAINT chk_ls_active_has_period CHECK (
                status <> 'active'
                OR (period_start IS NOT NULL
                    AND period_expiry IS NOT NULL
                    AND period_expiry > period_start))
            NOT VALID;
        RAISE NOTICE 'Added chk_ls_active_has_period to '
                     'public.library_subscriptions NOT VALID; every '
                     'subsequent INSERT and UPDATE is checked from now on '
                     '(Requirement 11.13, P-10).';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid    = 'public.library_subscriptions'::regclass
           AND conname     = 'chk_ls_active_has_period'
           AND convalidated
    ) THEN
        BEGIN
            ALTER TABLE public.library_subscriptions
                VALIDATE CONSTRAINT chk_ls_active_has_period;
            RAISE NOTICE 'chk_ls_active_has_period validated: every '
                         '''active'' library_subscriptions row has a period '
                         'start and a strictly later expiry.';
        EXCEPTION
            WHEN check_violation THEN
                SELECT count(*) INTO offenders
                  FROM public.library_subscriptions
                 WHERE status = 'active'
                   AND (period_start IS NULL
                        OR period_expiry IS NULL
                        OR period_expiry <= period_start);

                RAISE NOTICE
                    'chk_ls_active_has_period could NOT be validated: % '
                    'existing library_subscriptions row(s) are ''active'' '
                    'without a period start and a strictly later expiry - '
                    'almost certainly rows that predate the period columns '
                    'this file adds, since '
                    'archived_migrations/root_migrations/'
                    '007_create_library_subscriptions.sql defaults status to '
                    '''active''. The constraint stays in place and IS '
                    'enforced for every new INSERT and UPDATE; those rows '
                    'were left untouched, because inventing a period for a '
                    'subscription would be this migration granting '
                    'entitlement and Requirement 24.7 forbids it from '
                    'rewriting an existing row. Reconcile them by hand (set '
                    'the period from started_at/expires_at where those are '
                    'meaningful, or move the row to ''expired'') and re-run '
                    'this file to validate.', offenders;
        END;
    ELSE
        RAISE NOTICE 'chk_ls_active_has_period already validated; left '
                     'unchanged.';
    END IF;
END $$;

-- 4d) chk_ls_split_conserved -------------------------------------------
-- design.md § Data Models:
--   chk_ls_split_conserved CHECK (price_minor IS NULL OR owner_share_minor +
--     platform_fee_minor = price_minor)
--
-- Requirement 9.3's "recording ... the price in Minor_Units, the currency,
-- the computed platform_fee, the computed owner_share" together with
-- Requirement 10.2's conservation, applied to the SUBSCRIPTION row and not
-- only to the Settlement_Record - so a Subscription whose recorded split does
-- not add up cannot exist to be settled against.
--
-- Validated on the first run, which cannot fail: all three columns were added
-- nullable with no default in section 4 above, so every pre-existing row
-- holds NULL in price_minor the moment this commits and the first disjunct is
-- true for all of them. That is why this one is added plainly while 4c is
-- added NOT VALID.
--
-- ONE HOLE IN THIS CONSTRAINT, STATED RATHER THAN QUIETLY WIDENED. When
-- price_minor IS NOT NULL and either share IS NULL, the sum is NULL, the
-- equality evaluates to NULL, and a CHECK admits NULL - so a row with a price
-- and no split passes. The design specifies this expression, and tightening
-- it to "... AND owner_share_minor IS NOT NULL AND platform_fee_minor IS NOT
-- NULL" would be this migration writing a constraint the design does not
-- state. The gap is closed on the path that matters instead:
-- marketplace_settlements.chk_settlement_conserved is NOT NULL on all three
-- of its amount columns, so the money that is actually paid and reported is
-- conserved unconditionally, and Requirement 10.7's earnings total is summed
-- from THAT table and never from this one. Recorded here so a reader does not
-- mistake this constraint for the ledger's.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.library_subscriptions'::regclass
           AND conname  = 'chk_ls_split_conserved'
    ) THEN
        ALTER TABLE public.library_subscriptions
            ADD CONSTRAINT chk_ls_split_conserved CHECK (
                price_minor IS NULL
                OR owner_share_minor + platform_fee_minor = price_minor);
        RAISE NOTICE 'Added chk_ls_split_conserved to '
                     'public.library_subscriptions (Requirements 9.3, 10.2).';
    ELSE
        RAISE NOTICE 'chk_ls_split_conserved already present; left unchanged.';
    END IF;
END $$;

-- 4e) idx_lib_subs_expiry ----------------------------------------------
-- design.md § "marketplace/expiry_sweep.py" and Requirement 24.4's
-- "Subscription expiry sweep by expiry timestamp". The sweep is one
-- statement -
--   UPDATE library_subscriptions SET status='expired'
--    WHERE period_expiry IS NOT NULL AND period_expiry <= now
--      AND status IN ('active','suspended')
-- - run every 30 seconds so Requirement 11.8's 60-second bound holds with
-- margin. PARTIAL over exactly the sweep's own status predicate, so the index
-- holds only the rows that can still expire: an 'expired', 'cancelled',
-- 'refunded', 'pending' or 'payment_failed' row is not in it at all, and the
-- index therefore stays roughly the size of the live subscriber base rather
-- than of all history. A full index on (period_expiry) would grow forever and
-- would still have to filter on status.
--
-- The predicate names 'suspended', which only became a legal status in
-- section 4b above - so the widening genuinely precedes the index that
-- depends on it. A partial index predicate does not require the CHECK to
-- admit the value, but writing them in this order keeps the file readable as
-- a sequence rather than as a set.
--
-- Idempotent: CREATE INDEX IF NOT EXISTS.
CREATE INDEX IF NOT EXISTS idx_lib_subs_expiry
    ON public.library_subscriptions (period_expiry)
    WHERE status IN ('active', 'suspended');

-- ==========================================================================
-- SECTION 5 - functions and triggers
-- ==========================================================================
-- CREATE OR REPLACE FUNCTION is naturally idempotent. Every CREATE TRIGGER is
-- behind a pg_trigger existence guard, because PostgreSQL has no CREATE
-- TRIGGER IF NOT EXISTS and this repository's PostgreSQL baseline predates
-- CREATE OR REPLACE TRIGGER (PG14) - the same pattern 007 section 7, 004c
-- section 2 and 003_signal_trace_restoration.sql section 7 use.
--
-- BEFORE triggers on one table fire in NAME order. On
-- public.library_subscriptions that gives, after this file:
--     trg_lib_subs_period_mirror            (section 5d, this file)
--     trg_library_subscriptions_updated_at  (pre-existing, untouched)
--     trg_subscription_transition_guard     (section 5c, this file)
-- The mirror runs first, so started_at and expires_at are already aligned with
-- the period columns by the time anything else looks; the guard runs last, so
-- a transition it refuses aborts the statement after no other trigger has
-- done anything that would need undoing. The order is a consequence of the
-- names, so it is stated here rather than assumed.

-- 5a) updated_at ------------------------------------------------------
-- Requirement 24.5: creation and update timestamps on every table introduced
-- by this specification. created_at is DEFAULT NOW() on each of the three
-- tables above; this is the update half.
--
-- REUSES public.marketplace_touch_updated_at() from
-- 007_marketplace_submissions.sql section 7a rather than defining a second
-- copy. Section 0 asserted it exists. A second identical function would be
-- exactly the duplicate definition this specification keeps removing, and
-- CREATE OR REPLACE on it here would be a change to a function 007's five
-- triggers already call.
--
-- The trigger is created on all three tables including the two append-only
-- ones, where an UPDATE is refused before it can reach the timestamp. That is
-- deliberate and matches 007, which does the same for
-- marketplace_submission_transitions and marketplace_backtest_evidence: the
-- timestamp trigger is a property of "a table this specification introduced"
-- and not a judgement about whether the table is writable, so a later
-- migration that relaxed the append-only rule would not also have to remember
-- to add the timestamp.
--
-- Idempotent: each CREATE TRIGGER is behind a pg_trigger guard keyed on the
-- trigger name AND the table, so a re-run creates nothing.
DO $$
DECLARE
    target TEXT;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'marketplace_settlements',
        'library_subscription_transitions',
        'marketplace_subscription_allowed_transitions'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger t
              JOIN pg_class c     ON c.oid = t.tgrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE t.tgname   = 'trg_' || target || '_updated_at'
               AND n.nspname  = 'public'
               AND c.relname  = target
               AND NOT t.tgisinternal
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE UPDATE ON public.%I '
                'FOR EACH ROW EXECUTE FUNCTION '
                'public.marketplace_touch_updated_at()',
                'trg_' || target || '_updated_at', target);
        END IF;
    END LOOP;
END $$;

-- 5b) The append-only guard --------------------------------------------
-- Requirement 10.8's "THE Marketplace SHALL NOT delete or modify a persisted
-- Settlement_Record" and Requirement 11.12's record of every transition,
-- which is worthless if it can be rewritten.
--
-- ONE function, two triggers. TG_TABLE_NAME and TG_OP put the table and the
-- operation in the message, so a single definition covers both tables and
-- there is no second copy to keep in step. ERRCODE 23514 (check_violation)
-- for the same reason 007's guards use it: settlement_service can distinguish
-- this from a connection error by SQLSTATE and translate it without parsing
-- English.
--
-- A SEPARATE FUNCTION FROM 007's public.marketplace_append_only_guard(), not
-- a replacement of it. That one resolves its parent through
-- OLD.submission_id against marketplace_submissions; these two tables resolve
-- theirs through OLD.subscription_id against library_subscriptions.
-- Generalising 007's function to handle both would mean CREATE OR REPLACE on
-- a function two of its triggers already depend on - a change to an existing
-- control - so this file adds its own, which is additive by construction.
--
-- THE DELETE BRANCH DELIBERATELY EXEMPTS A REFERENTIAL CASCADE. See "WHY THE
-- APPEND-ONLY GUARD EXEMPTS A CASCADE" in the header. PostgreSQL runs a
-- referential CASCADE as an AFTER trigger on the parent, so during a cascade
-- the parent library_subscriptions row is ALREADY GONE when this BEFORE
-- DELETE trigger fires, while a direct "DELETE FROM
-- library_subscription_transitions WHERE ..." always runs with the parent
-- present. The parent-existence test is therefore exact rather than
-- heuristic. On marketplace_settlements the exemption is unreachable anyway,
-- because subscription_id is ON DELETE RESTRICT: a Subscription carrying a
-- Settlement_Record cannot be deleted at all.
--
-- The REVOKE UPDATE, DELETE grants of section 7 are the second layer. They
-- are not sufficient alone - a table owner and, in some Supabase projects,
-- service_role bypass table privileges - which is why the trigger is the
-- primary control and the REVOKE the defence in depth.
--
-- CREATE OR REPLACE FUNCTION is naturally idempotent.
CREATE OR REPLACE FUNCTION public.marketplace_settlement_append_only_guard()
RETURNS TRIGGER AS $$
DECLARE
    parent_exists BOOLEAN;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            '% is append-only: UPDATE is not permitted (row %)',
            TG_TABLE_NAME, OLD.id
            USING ERRCODE = '23514';
    END IF;

    -- TG_OP = 'DELETE'
    SELECT EXISTS (
        SELECT 1 FROM public.library_subscriptions s
         WHERE s.id = OLD.subscription_id
    ) INTO parent_exists;

    IF parent_exists THEN
        RAISE EXCEPTION
            '% is append-only: DELETE is not permitted while subscription % '
            'exists (row %)',
            TG_TABLE_NAME, OLD.subscription_id, OLD.id
            USING ERRCODE = '23514';
    END IF;

    -- The parent Subscription is already gone, so this DELETE is the declared
    -- ON DELETE CASCADE of Requirement 24.1 and not a request to rewrite a
    -- financial record or an audit trail. Let it through.
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
          JOIN pg_class c     ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname  = 'trg_settlements_append_only'
           AND n.nspname = 'public'
           AND c.relname = 'marketplace_settlements'
           AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_settlements_append_only
            BEFORE UPDATE OR DELETE ON public.marketplace_settlements
            FOR EACH ROW EXECUTE FUNCTION public.marketplace_settlement_append_only_guard();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
          JOIN pg_class c     ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname  = 'trg_lib_sub_transitions_append_only'
           AND n.nspname = 'public'
           AND c.relname = 'library_subscription_transitions'
           AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_lib_sub_transitions_append_only
            BEFORE UPDATE OR DELETE ON public.library_subscription_transitions
            FOR EACH ROW EXECUTE FUNCTION public.marketplace_settlement_append_only_guard();
    END IF;
END $$;

-- 5c) marketplace_subscription_guard() + trg_subscription_transition_guard --
-- design.md -> "marketplace/subscription_state.py":
--   "trg_subscription_transition_guard mirrors the submission guard: it
--    rejects a transition absent from
--    marketplace_subscription_allowed_transitions, and additionally rejects
--    any transition into 'active' for which no qualifying
--    marketplace_settlements row exists (Requirements 11.3, 11.14)."
--
-- THIS IS WHAT MAKES REQUIREMENTS 11.3 AND 11.14 TRUE RATHER THAN
-- ASPIRATIONAL. Not the service layer. The database. A psql session with the
-- service key cannot move a Subscription along an edge the seed table does not
-- hold, and cannot make one 'active' without a payment.
--
-- Three branches, in this order and for these reasons:
--
--   1. A same-value write RETURNS NEW immediately. It is not a transition -
--      no state changed - so it is not the transition table's business, and
--      the seed holds no self-edge so a "transition" from a value to itself
--      changes nothing and records nothing (Requirement 11.2 lists no such
--      pair). IS NOT DISTINCT FROM, not =, so a NULL status on both sides is
--      also recognised as "unchanged" rather than evaluating to NULL and
--      falling through to branch 2 with a NULL to look up. Note this branch
--      also lets an UPDATE that touches only period_expiry,
--      provider_reference, failure_cause, renewal_enabled or cancelled_at
--      through, which is what the checkout, cancel and renewal paths need.
--
--      IT ALSO MEANS A RENEWAL - 'active' -> 'active' with a later expiry -
--      IS NOT CHECKED HERE. That is correct and deliberate: it is not a
--      transition, so Requirement 11.2 has nothing to say about it, and
--      Requirement 11.6's payment gate is written as a rule about
--      transitions INTO 'active'. The renewal path's own payment gate is
--      uq_settlement_reference_reversal plus settlement_service, which will
--      not extend a period it has no confirmed settlement for. A future task
--      that wants the database to police extensions too would add a separate
--      branch comparing NEW.period_expiry with OLD.period_expiry; this file
--      does not invent one, because the requirement does not ask for it.
--
--   2. An edge absent from marketplace_subscription_allowed_transitions
--      raises with ERRCODE 23514 (check_violation) and a message naming BOTH
--      states, which is Requirement 11.3's "SHALL return an error indicating
--      a disallowed Subscription_State transition". The statement aborts, so
--      "SHALL leave the stored Subscription_State and the stored period start
--      and expiry unchanged" is structural: nothing in this UPDATE lands.
--
--   3. A transition INTO 'active' with no qualifying Settlement_Record raises
--      the same 23514. Requirements 11.6 and 11.14. "Qualifying" is:
--
--        a non-reversal marketplace_settlements row for THIS subscription
--        whose settled_at is at or after the row's CURRENT period_expiry
--        (OLD.period_expiry, read before this UPDATE changes it),
--        or any non-reversal row at all when OLD.period_expiry IS NULL.
--
--      The OLD.period_expiry comparison is what makes the check non-circular.
--      The payment that bought the period now ending was settled at the START
--      of that period, so it is strictly earlier than the expiry and cannot
--      fund the next one. A direct
--        UPDATE library_subscriptions
--           SET status='active', period_expiry = period_expiry + '1 month'
--      therefore fails: the row's own settlement history holds nothing newer
--      than the expiry it is trying to extend. The NULL case is the first
--      activation of Requirement 11.4, where there is no prior period for a
--      payment to be suspected of re-using.
--
--      is_reversal = FALSE, not "IS NOT TRUE": the column is NOT NULL, so the
--      two are identical here, and the explicit FALSE reads as the intent -
--      a refund is not a payment and must never fund an activation.
--
--      THE KNOWN CONSEQUENCE, recorded in the header at length and repeated
--      here because this is where it bites: a renewal CONFIRMED BEFORE the
--      current expiry - the 'cancelled' -> 'active' path a purchaser takes
--      while still inside a paid period (Requirement 11.9) - has settled_at
--      < OLD.period_expiry and is refused by this branch, even though
--      Requirement 11.5's "the later of the current expiry and the
--      confirmation instant" contemplates it. The rule is implemented as
--      design.md specifies and NOT weakened, because no expression over this
--      schema distinguishes a fresh mid-period payment from a re-used older
--      one, and admitting the former by relaxing the comparison would admit
--      the latter too. Closing the case needs a consumed-marker column, which
--      is a schema decision beyond this task.
--
--   The RLS note that matters: the two SELECTs below run as the INVOKING
--   role, under row level security, because this function is deliberately NOT
--   SECURITY DEFINER - making the state-machine guard run with the definer's
--   privileges would be a privilege-escalation surface. If RLS hides a
--   settlement from the invoking role, branch 3 raises and the transition is
--   REFUSED. That is the safe direction: the guard fails closed. It is also
--   why section 6 gives marketplace_settlements a purchaser SELECT policy and
--   section 7 grants authenticated SELECT on both consulted tables.
--
-- NEW.updated_at is deliberately NOT assigned here, unlike 007's submission
-- guard. library_subscriptions already carries the pre-existing
-- trg_library_subscriptions_updated_at, which fires on every UPDATE and does
-- exactly that; assigning it again would be this file duplicating an existing
-- control, and the root-migration shape of the table does not even declare the
-- column that trigger writes.
--
-- No transition-history row is written here. Requirement 11.3 requires the
-- rejected write to leave the stored values unchanged, and history is inserted
-- by the application AFTER the UPDATE returns, in the same transaction - so a
-- raise here means the INSERT never runs. A trigger that wrote history itself
-- would also write it for a transition the application then failed to audit,
-- which Requirement 11.12 forbids in the other direction.
--
-- CREATE OR REPLACE FUNCTION is naturally idempotent.
CREATE OR REPLACE FUNCTION public.marketplace_subscription_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NEW;                      -- a no-op write is not a transition
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.marketplace_subscription_allowed_transitions t
         WHERE t.from_state = OLD.status
           AND t.to_state   = NEW.status
    ) THEN
        RAISE EXCEPTION
            'disallowed Subscription_State transition % -> %',
            OLD.status, NEW.status
            USING ERRCODE = '23514';
    END IF;

    IF NEW.status = 'active' THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.marketplace_settlements s
             WHERE s.subscription_id = NEW.id
               AND s.is_reversal = FALSE
               AND (OLD.period_expiry IS NULL
                    OR s.settled_at >= OLD.period_expiry)
        ) THEN
            RAISE EXCEPTION
                'transition % -> active requires a confirmed payment: no '
                'non-reversal marketplace_settlements row for subscription % '
                'has settled_at at or after the current period expiry %',
                OLD.status, NEW.id, OLD.period_expiry
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
          JOIN pg_class c     ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname  = 'trg_subscription_transition_guard'
           AND n.nspname = 'public'
           AND c.relname = 'library_subscriptions'
           AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_subscription_transition_guard
            BEFORE UPDATE ON public.library_subscriptions
            FOR EACH ROW EXECUTE FUNCTION public.marketplace_subscription_guard();
    END IF;
END $$;

-- 5d) trg_lib_subs_period_mirror ---------------------------------------
-- design.md § Data Models: "started_at and expires_at mirrored from the
-- period columns by trigger", and Requirement 25's "existing behaviour
-- retained": library.py's check_deployment_permission and the Strategies-page
-- reads consult expires_at today, and billing.py writes started_at.
--
-- ONE DIRECTION ONLY: period_start and period_expiry are authoritative and
-- started_at/expires_at are derived. Mirroring the other way as well would
-- make two pairs of columns each other's source of truth, and the first write
-- that set only expires_at would produce a Subscription whose displayed
-- expiry and whose ENTITLEMENT disagree - and entitlement is period_expiry
-- (Requirement 11.7). A caller that writes only expires_at therefore does NOT
-- change when access ends, and that is the intended reading: entitlement is
-- period_expiry.
--
-- A NULL period column leaves its mirror ALONE rather than writing NULL over
-- a value an existing row already carries. Three reasons, all of them the same
-- reason: started_at is NOT NULL DEFAULT NOW() in both existing definitions,
-- so writing NULL into it would raise on every insert that had not set a
-- period; a pre-existing row carries a real started_at and no period, and
-- clearing it would be this trigger deleting data (Requirement 24.7); and an
-- UPDATE that touches neither period column would otherwise blank both
-- mirrors.
--
-- BEFORE INSERT OR UPDATE, so the mirror is aligned within the same row write
-- rather than by a second statement that a caller could omit or a direct SQL
-- write could skip.
--
-- CREATE OR REPLACE FUNCTION is naturally idempotent.
CREATE OR REPLACE FUNCTION public.library_subscriptions_mirror_period()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.period_start IS NOT NULL THEN
        NEW.started_at := NEW.period_start;
    END IF;

    IF NEW.period_expiry IS NOT NULL THEN
        NEW.expires_at := NEW.period_expiry;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
          JOIN pg_class c     ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname  = 'trg_lib_subs_period_mirror'
           AND n.nspname = 'public'
           AND c.relname = 'library_subscriptions'
           AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_lib_subs_period_mirror
            BEFORE INSERT OR UPDATE ON public.library_subscriptions
            FOR EACH ROW EXECUTE FUNCTION public.library_subscriptions_mirror_period();
    END IF;
END $$;

-- ==========================================================================
-- SECTION 6 - row level security
-- ==========================================================================
-- Requirements 21.2 and 21.3. Enable FIRST, then add the policies: enabling
-- RLS is DEFAULT-DENY, so between these statements the tables are unreachable
-- rather than open - and every statement is inside this one transaction, so
-- no session ever observes the intermediate state. That ordering is
-- Requirement 21.3's "SHALL default to denying access, such that a query
-- executed against any table introduced by this specification without a
-- resolved authenticated identity or the service role returns zero rows and
-- performs zero writes".
--
-- Idempotent: ENABLE ROW LEVEL SECURITY is a no-op when RLS is already on
-- (the same unguarded form 007 section 8, 004d section 3 and
-- 003_signal_trace_restoration.sql section 8 use).
--
-- public.library_subscriptions is DELIBERATELY ABSENT from this list. It is a
-- pre-existing table and migrations/006_reconcile_production_database.sql
-- already enabled RLS on it with lib_subs_owner_access and
-- lib_subs_service_role; enabling or disabling RLS on it, or touching one of
-- its policies, would be a change to an existing control that no task in this
-- plan may make (Requirement 25.1). Section 9 proves its policy count did not
-- move.
ALTER TABLE public.marketplace_settlements                      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.library_subscription_transitions             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.marketplace_subscription_allowed_transitions ENABLE ROW LEVEL SECURITY;

-- Guarded on pg_policies by schemaname + tablename + policyname, the pattern
-- 007 section 8, 004d section 3 and 004b section 4 use, rather than the DROP
-- POLICY IF EXISTS ... CREATE POLICY of
-- migrations/006_reconcile_production_database.sql: PostgreSQL has no CREATE
-- POLICY IF NOT EXISTS, and a DROP would open a window inside this
-- transaction in which the table has no policy at all.
--
-- WITH CHECK repeats the USING predicate wherever both apply rather than
-- being omitted. Omitting it makes PostgreSQL reuse USING for the new row,
-- which is equivalent here, but stating it means a later reader cannot
-- mistake the omission for "any new row is allowed" and cannot "simplify" the
-- policy into that.
--
-- MARKETPLACE_SETTLEMENTS GETS THREE POLICIES, AND TWO OF THEM ARE
-- "FOR SELECT" RATHER THAN THE "FOR ALL" SHAPE Requirement 21.2 DESCRIBES:
--
--   settlements_owner_read      auth.uid() = owner_id      - Requirement
--                               10.6's creator earnings read.
--   settlements_purchaser_read  auth.uid() = purchaser_id  - design.md §
--                               Data Models: "second RLS policy so the
--                               purchaser can read their own payments". A
--                               purchaser must see what they paid; they must
--                               not see the owner's other earnings, which is
--                               why this is a second predicate and not a
--                               widening of the first.
--   settlements_service_role    FOR ALL, USING (true)      - the backend
--                               writes settlements from the webhook.
--
-- The deviation from "FOR ALL TO authenticated" is deliberate and is a
-- TIGHTENING, not a relaxation. This table is the Settlement_Ledger: no
-- client may ever write a row in it (Requirement 10.4 makes settlement a
-- webhook-side action) and no one at all may update or delete one
-- (Requirement 10.8). A FOR ALL owner policy would express the opposite
-- intent, and would mean that if the INSERT grant of section 7 were ever
-- widened by accident the policy would already permit the write. Two
-- read-only policies plus a service-role policy say exactly what is true.
-- Requirement 21.2's "scoping each row to its owning user, with a
-- service-role policy for backend-initiated writes" is satisfied in full -
-- the scoping is there twice over, and the backend-initiated writes are the
-- service role's.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'marketplace_settlements'
           AND policyname = 'settlements_owner_read'
    ) THEN
        CREATE POLICY settlements_owner_read
            ON public.marketplace_settlements
            FOR SELECT TO authenticated
            USING (auth.uid() = owner_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'marketplace_settlements'
           AND policyname = 'settlements_purchaser_read'
    ) THEN
        CREATE POLICY settlements_purchaser_read
            ON public.marketplace_settlements
            FOR SELECT TO authenticated
            USING (auth.uid() = purchaser_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'marketplace_settlements'
           AND policyname = 'settlements_service_role'
    ) THEN
        CREATE POLICY settlements_service_role
            ON public.marketplace_settlements
            FOR ALL TO service_role
            USING (true) WITH CHECK (true);
    END IF;

    -- library_subscription_transitions: the owner column is user_id, the
    -- purchaser whose subscription it is - the same column and the same
    -- predicate library_subscriptions' own lib_subs_owner_access uses, so the
    -- history is visible to exactly the identity the subscription is. FOR ALL
    -- here, following the design's stated pattern: the append-only trigger and
    -- the revoked UPDATE/DELETE of section 7 are what make it read-and-append
    -- rather than read-write, and unlike the settlement ledger there is no
    -- second identity to keep out of the INSERT path.
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'library_subscription_transitions'
           AND policyname = 'lib_sub_transitions_owner_access'
    ) THEN
        CREATE POLICY lib_sub_transitions_owner_access
            ON public.library_subscription_transitions
            FOR ALL TO authenticated
            USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'library_subscription_transitions'
           AND policyname = 'lib_sub_transitions_service_role'
    ) THEN
        CREATE POLICY lib_sub_transitions_service_role
            ON public.library_subscription_transitions
            FOR ALL TO service_role
            USING (true) WITH CHECK (true);
    END IF;
END $$;

-- marketplace_subscription_allowed_transitions is the one new table with NO
-- OWNER, because it holds no user data: twelve rows of platform-global
-- reference data naming the edges of a state machine that is published in
-- requirements.md. There is nothing to scope to a tenant, so the owner-policy
-- shape of Requirement 21.2 has no owner column to name.
--
-- What it gets instead is READ TO EVERYONE, WRITE TO NOBODY BUT service_role:
--
--   * a permissive FOR SELECT USING (true) policy, applying to PUBLIC. It has
--     to be readable by every role that can UPDATE a Subscription, because
--     marketplace_subscription_guard() SELECTs from it inside the trigger and
--     that SELECT runs as the INVOKING role, under RLS. The function is
--     deliberately NOT SECURITY DEFINER - making the state-machine guard run
--     with the definer's privileges would be a privilege-escalation surface
--     for the sake of hiding a table of twelve public constants.
--   * NO insert, update or delete policy for authenticated or anon. With RLS
--     on, default-deny means a caller cannot add an edge to the state machine
--     and so cannot authorise a transition Requirement 11.2 forbids. That is
--     the whole security property of this table, and it is achieved by the
--     ABSENCE of a policy rather than by one.
--   * the service_role FOR ALL policy, so a future migration or a backend job
--     can seed a new edge.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'marketplace_subscription_allowed_transitions'
           AND policyname = 'subscription_transitions_seed_read'
    ) THEN
        CREATE POLICY subscription_transitions_seed_read
            ON public.marketplace_subscription_allowed_transitions
            FOR SELECT USING (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'marketplace_subscription_allowed_transitions'
           AND policyname = 'subscription_transitions_seed_service_role'
    ) THEN
        CREATE POLICY subscription_transitions_seed_service_role
            ON public.marketplace_subscription_allowed_transitions
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

-- ==========================================================================
-- SECTION 7 - grants
-- ==========================================================================
-- Table privileges are the layer BELOW row level security: RLS decides which
-- rows a role may touch, a GRANT decides whether it may touch the table at
-- all. Both are needed - a policy on a table the role has no privilege on
-- grants nothing, and a privilege without a policy reaches no rows.
--
-- Following 007 section 9 and 004d section 5: revoke everything from anon and
-- authenticated first so the grant list below is the WHOLE list rather than an
-- addition to whatever a previous run or a Supabase default left behind, then
-- grant exactly what each role needs.
--
-- Idempotent: REVOKE and GRANT are both idempotent by definition.
REVOKE ALL ON public.marketplace_settlements                      FROM anon;
REVOKE ALL ON public.marketplace_settlements                      FROM authenticated;
REVOKE ALL ON public.library_subscription_transitions             FROM anon;
REVOKE ALL ON public.library_subscription_transitions             FROM authenticated;
REVOKE ALL ON public.marketplace_subscription_allowed_transitions FROM anon;
REVOKE ALL ON public.marketplace_subscription_allowed_transitions FROM authenticated;

-- An owner reads their earnings and a purchaser reads their own payments
-- (Requirement 10.6, design.md's purchaser policy). SELECT ONLY, and no
-- INSERT: a Settlement_Record is written from the webhook by the backend
-- (Requirement 10.4), never by a client, and a client that could insert one
-- could invent an earning. authenticated MUST have this SELECT, because
-- marketplace_subscription_guard() reads this table as the invoking role - a
-- missing privilege there would turn every Subscription UPDATE into a
-- permission error instead of a transition verdict.
GRANT SELECT ON public.marketplace_settlements TO authenticated;

-- Append-only history: SELECT and INSERT, never UPDATE or DELETE
-- (Requirement 11.12). The trigger of section 5b is the primary control; this
-- is the layer below it.
GRANT SELECT, INSERT ON public.library_subscription_transitions TO authenticated;

-- The state machine is readable and not writable. The guard's SELECT runs as
-- the invoking role, so authenticated MUST be able to read it or every
-- Subscription UPDATE - including the purchaser's own cancellation - would
-- fail with a permission error instead of a transition verdict.
GRANT SELECT ON public.marketplace_subscription_allowed_transitions TO authenticated;

-- service_role: everything the backend does, and no more. Notably no UPDATE
-- and no DELETE anywhere in this file's three tables - the design's "REVOKE
-- UPDATE, DELETE ... FROM authenticated, service_role" for the settlement
-- ledger, extended to the history table for the same reason.
GRANT SELECT, INSERT ON public.marketplace_settlements                      TO service_role;
GRANT SELECT, INSERT ON public.library_subscription_transitions             TO service_role;
GRANT SELECT, INSERT ON public.marketplace_subscription_allowed_transitions TO service_role;

-- The explicit REVOKE the design names, stated after the GRANTs so that it is
-- the last word on these tables even if a GRANT list above is ever widened by
-- accident. Requirements 10.8 and 11.12.
REVOKE UPDATE, DELETE ON public.marketplace_settlements                      FROM anon, authenticated, service_role;
REVOKE UPDATE, DELETE ON public.library_subscription_transitions             FROM anon, authenticated, service_role;
REVOKE UPDATE, DELETE ON public.marketplace_subscription_allowed_transitions FROM anon, authenticated;
REVOKE INSERT         ON public.marketplace_settlements                      FROM anon, authenticated;

-- ==========================================================================
-- SECTION 8 - comments
-- ==========================================================================
-- Intent the schema cannot express, recorded where \d+ and every schema
-- browser will show it, and in several cases a warning a reader genuinely
-- needs. COMMENT replaces, so this is idempotent.

COMMENT ON TABLE public.marketplace_settlements IS
    'The Settlement_Ledger: one row per confirmed marketplace payment and one '
    'per refund (Requirement 10). APPEND-ONLY - trg_settlements_append_only '
    'refuses every UPDATE and every DELETE, and UPDATE/DELETE are revoked '
    'from anon, authenticated and service_role (Requirement 10.8). A refund '
    'is an ADDITIONAL row with is_reversal = TRUE carrying the original '
    'provider reference in reverses_reference, never an edit of the original. '
    'Every earnings figure reported to an owner is a sum over these rows '
    '(Requirement 10.6) - never a counter multiplied by a current price - '
    'computed per currency, non-reversals minus reversals, in integer '
    'Minor_Units (Requirement 10.7). Both foreign keys are ON DELETE '
    'RESTRICT, so neither a paid Subscription nor a Listing with settled '
    'Subscriptions can be deleted: that is Requirements 10.8 and 11.11 '
    'enforced rather than hoped for. This table stores no Subscription '
    'state - it records payments, and carries a foreign key to the '
    'subscription rather than a copy of its status (Requirement 1.1).';

COMMENT ON COLUMN public.marketplace_settlements.amount_minor IS
    'The charged amount in integer Minor_Units (Requirements 9.2, 10.1). '
    'BIGINT and never NUMERIC or a float: Requirement 10.1''s domain reaches '
    '99,999,999,999, and an exact integer is the only representation in which '
    'chk_settlement_conserved is an equality at all.';

COMMENT ON COLUMN public.marketplace_settlements.owner_share_minor IS
    '(amount_minor * 90) / 100 by integer division truncating toward zero '
    '(Requirement 10.1). STORED, not derived: Requirement 10.7''s total is a '
    'sum over the stored values, and a derived column would let the split '
    'rule change under an already-reported total. '
    'chk_settlement_conserved makes owner_share_minor + platform_fee_minor = '
    'amount_minor a database fact (Requirements 10.2, 10.5).';

COMMENT ON COLUMN public.marketplace_settlements.provider_reference IS
    'The provider transaction reference. With is_reversal it forms '
    'uq_settlement_reference_reversal, which is Requirement 9.7''s unique '
    'constraint and THE CORRECTNESS PATH for webhook idempotency: N '
    'duplicate deliveries produce one row and N-1 unique violations, which '
    'settlement_service records as a duplicate (Requirement 10.10) while the '
    'period is extended exactly once (Requirement 9.6, P-6). The Redis lock '
    'in stripe_webhook/razorpay_webhook is the fast path; this constraint is '
    'what stays true when that lock expires mid-flight.';

COMMENT ON COLUMN public.marketplace_settlements.is_reversal IS
    'FALSE for a payment, TRUE for a refund (Requirement 10.8). NOT NULL '
    'DEFAULT FALSE, so a row that forgot to say is a payment rather than a '
    'NULL that neither half of the earnings sum would count. '
    'trg_subscription_transition_guard admits only is_reversal = FALSE rows '
    'as evidence of payment: a refund must never fund an activation.';

COMMENT ON COLUMN public.marketplace_settlements.settled_at IS
    'The payment confirmation instant in UTC (Requirement 10.4), and the '
    'column trg_subscription_transition_guard compares against a '
    'Subscription''s CURRENT period_expiry to decide whether a transition '
    'into ''active'' is paid for (Requirements 11.6, 11.14). TIMESTAMPTZ, so '
    'that comparison is between two instants - a timestamp WITHOUT time zone '
    'here would shift it by the server''s UTC offset, which is up to '
    'fourteen hours of free subscription.';

COMMENT ON TABLE public.marketplace_subscription_allowed_transitions IS
    'The twelve permitted Subscription_State edges of Requirement 11.2, as '
    'data, in the LOWERCASE spelling library_subscriptions.status uses. MUST '
    'equal SUBSCRIPTION_TRANSITIONS - specifically '
    'SUBSCRIPTION_TRANSITION_TEXT_PAIRS - in '
    'backend_app/backend/marketplace/subscription_state.py; task 14.6''s '
    'state-agreement test asserts the two are the same set. Platform-global '
    'reference data, no owner, no user data: RLS is on with a '
    'read-to-everyone SELECT policy (the transition guard SELECTs from here '
    'as the invoking role) and NO write policy for authenticated, so a caller '
    'cannot add an edge and thereby authorise a transition the requirement '
    'forbids. ''refunded'' appears only as a to_state - it is the one '
    'terminal state, because a reversal ends that subscription''s life and '
    're-subscribing is a new row - and no row has from_state = to_state, so a '
    'same-value write is not a transition.';

COMMENT ON TABLE public.library_subscription_transitions IS
    'Requirement 11.12''s record of every Subscription_State transition, '
    'every period extension and every entitlement change: prior value, new '
    'value, cause, acting identity where one applies, timestamp in UTC. '
    'prior_period_expiry and new_period_expiry are what make it a record of '
    'EXTENSIONS and not only of state changes - a renewal does not change '
    'status, so without them it would leave no trace. APPEND-ONLY - '
    'trg_lib_sub_transitions_append_only refuses every UPDATE and every '
    'DELETE whose parent Subscription still exists, and UPDATE/DELETE are '
    'revoked from anon, authenticated and service_role. A DELETE is permitted '
    'only as the declared ON DELETE CASCADE of a deleted Subscription, which '
    'by then has already gone - and which marketplace_settlements'' RESTRICT '
    'makes impossible for any Subscription that was ever paid. from_state and '
    'to_state carry no CHECK on purpose: a history row naming a state a later '
    'migration retires must stay readable.';

COMMENT ON COLUMN public.library_subscription_transitions.cause IS
    'Why the transition happened - ''payment_confirmed'', ''refund'', '
    '''expiry_sweep'', ''purchaser_cancelled'' and so on. NOT NULL, because a '
    'transition with no recorded cause is exactly the entry Requirement 11.12 '
    'would not accept. No CHECK: the vocabulary of causes is the '
    'application''s and will grow with the lifecycle, and a cause is a '
    'description rather than a value any invariant is computed from.';

COMMENT ON COLUMN public.library_subscriptions.owner_id IS
    'The Listing owner who receives owner_share_minor, denormalised from '
    'library_strategies.author_id at the instant of purchase (Requirement '
    '9.3). Recorded rather than joined so that a Listing changing hands does '
    'not silently re-answer who was owed for a payment already made. '
    'NULLABLE: rows that predate this specification have no recorded owner, '
    'and back-filling one would be a migration inventing a fact.';

COMMENT ON COLUMN public.library_subscriptions.price_minor IS
    'The price in Minor_Units charged for this Subscription, frozen at '
    'creation from the Listing price persisted at that instant (Requirement '
    '9.2). AUTHORITATIVE for the amount comparison of Requirement 9.14: '
    'settlement_service refuses a confirmation whose amount or currency '
    'differs from this column and the currency beside it. The retained '
    'price_paid NUMERIC(10,2) is unchanged and is NOT authoritative. NULL '
    'means unrecorded - never free.';

COMMENT ON COLUMN public.library_subscriptions.period_expiry IS
    'THE AUTHORITY ON ENTITLEMENT (Requirement 11.7). entitlement_resolver '
    'compares now with THIS column directly, not with status, so a dead '
    'expiry sweep cannot extend access - only the stored label and the '
    'session-stopping action lag (P-11). chk_ls_active_has_period makes an '
    '''active'' row without a period unrepresentable (Requirement 11.13, '
    'P-10), and idx_lib_subs_expiry supports the sweep of Requirement 11.8 in '
    'one index scan. The retained expires_at is MIRRORED FROM this column by '
    'trg_lib_subs_period_mirror and writing expires_at alone does NOT change '
    'when access ends.';

COMMENT ON COLUMN public.library_subscriptions.failure_cause IS
    'Why checkout or payment failed, recorded on the row together with '
    'failed_at when it moves to ''payment_failed'' (Requirements 9.4, 9.8). '
    'The row is UPDATED, never DELETED - the existing '
    'create_marketplace_checkout deletes it inside a bare "except: pass", '
    'which Requirement 9.4 forbids.';

COMMENT ON COLUMN public.library_subscriptions.renewal_enabled IS
    'Whether this Subscription may be renewed (Requirement 11.9''s "SHALL '
    'perform no further renewal"). NOT NULL DEFAULT TRUE and existing rows '
    'back-filled to TRUE, because the existing behaviour of every current '
    'subscription is that it renews and a migration must not silently cancel '
    'renewal for every current subscriber. Distinct from the auto_renew '
    'column the migrations/006_reconcile_production_database.sql shape of this '
    'table carries: that column exists in only one of the two shapes and is '
    'nullable, and it is left exactly as it is - not dropped, not renamed, not '
    'read.';

COMMENT ON FUNCTION public.marketplace_subscription_guard() IS
    'Requirements 11.3 and 11.14''s enforcement: rejects any '
    'Subscription_State transition absent from '
    'marketplace_subscription_allowed_transitions, and any transition into '
    '''active'' for which no non-reversal marketplace_settlements row for that '
    'subscription has settled_at at or after the row''s CURRENT period_expiry '
    '- so a direct SQL UPDATE with the service key cannot grant a free month. '
    'A same-value write returns early: it is not a transition, and a renewal '
    '(''active'' -> ''active'' with a later expiry) is therefore not checked '
    'here, which is deliberate - Requirement 11.6 is a rule about transitions '
    'INTO ''active''. NOT SECURITY DEFINER, so both consulted reads run under '
    'the invoking role''s RLS and the guard fails CLOSED if a settlement is '
    'hidden from it. Writes no history row: the application inserts '
    'library_subscription_transitions after the UPDATE returns, in the same '
    'transaction, so a raise here means that INSERT never runs. KNOWN GAP, '
    'documented in 008''s header: a renewal confirmed BEFORE the current '
    'expiry (the ''cancelled'' -> ''active'' path of Requirement 11.9 with '
    'Requirement 11.5''s "later of" arithmetic) is refused by the payment '
    'branch, because no expression over this schema distinguishes a fresh '
    'mid-period payment from a re-used older one.';

COMMENT ON FUNCTION public.marketplace_settlement_append_only_guard() IS
    'Refuses every UPDATE, and every DELETE whose parent Subscription still '
    'exists, on marketplace_settlements and library_subscription_transitions '
    '(Requirements 10.8, 11.12). The parent-existence test is what lets '
    'Requirement 24.1''s declared ON DELETE CASCADE from library_subscriptions '
    'still complete on the history table: PostgreSQL runs a cascade as an '
    'AFTER trigger on the parent, so during one the parent row is already '
    'gone, while a direct DELETE always runs with it present. On '
    'marketplace_settlements the exemption is unreachable, because '
    'subscription_id is ON DELETE RESTRICT. A separate function from 007''s '
    'public.marketplace_append_only_guard(), which resolves its parent through '
    'submission_id - generalising that one would have been a change to an '
    'existing control.';

COMMENT ON FUNCTION public.library_subscriptions_mirror_period() IS
    'Mirrors the retained library_subscriptions.started_at and expires_at from '
    'the authoritative period_start and period_expiry, ONE DIRECTION ONLY, so '
    'the existing check_deployment_permission and Strategies-page readers keep '
    'working unchanged (Requirement 25). Leaves a mirror untouched when its '
    'period column is NULL, rather than writing NULL over a value a '
    'pre-existing row already carries (Requirement 24.7) or over the NOT NULL '
    'started_at. Writing expires_at alone does NOT change entitlement: '
    'entitlement is period_expiry (Requirement 11.7).';

-- ==========================================================================
-- SECTION 9 - postflight
-- ==========================================================================
-- Turns the header's promises into facts about the database: the three tables,
-- the twelve library_subscriptions columns, every named constraint, every
-- named index, every function, every trigger, every policy, the twelve seed
-- rows, and - on the one pre-existing table this file touches - that its
-- policy count did not move and that it gained at most the one index and the
-- two triggers of sections 4e, 5c and 5d.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    lsub_policies_before INTEGER := current_setting('aerora.lsub_policies_before')::INTEGER;
    lsub_indexes_before  INTEGER := current_setting('aerora.lsub_indexes_before')::INTEGER;
    lsub_triggers_before INTEGER := current_setting('aerora.lsub_triggers_before')::INTEGER;
    lsub_policies_now    INTEGER;
    lsub_indexes_now     INTEGER;
    lsub_triggers_now    INTEGER;
    missing              TEXT;
    seed_rows            BIGINT;
    rls_off              TEXT;
    status_def           TEXT;
BEGIN
    -- (a) The three tables exist.
    SELECT string_agg(expected.table_name, ', ' ORDER BY expected.table_name)
      INTO missing
      FROM (VALUES ('marketplace_settlements'),
                   ('library_subscription_transitions'),
                   ('marketplace_subscription_allowed_transitions'))
             AS expected(table_name)
     WHERE to_regclass('public.' || expected.table_name) IS NULL;

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '008 postflight failed: table(s) % were not created.',
                        missing;
    END IF;

    -- (b) The twelve library_subscriptions columns exist.
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('owner_id'), ('price_minor'), ('owner_share_minor'),
                   ('platform_fee_minor'), ('period_start'),
                   ('period_expiry'), ('provider'), ('provider_reference'),
                   ('provider_session_at'), ('failure_cause'),
                   ('failed_at'), ('renewal_enabled'))
             AS expected(column_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns c
                WHERE c.table_schema::TEXT = 'public'
                  AND c.table_name::TEXT   = 'library_subscriptions'
                  AND c.column_name::TEXT  = expected.column_name);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '008 postflight failed: public.library_subscriptions '
                        'is missing column(s) % (Requirements 9.3, 9.4, 9.12, '
                        '11.4, 11.9).', missing;
    END IF;

    -- (c) Every named constraint exists, on the right table.
    SELECT string_agg(format('%s on %s', expected.conname, expected.table_name),
                      ', ' ORDER BY expected.conname)
      INTO missing
      FROM (VALUES
                ('chk_settlement_amounts',            'marketplace_settlements'),
                ('chk_settlement_conserved',          'marketplace_settlements'),
                ('chk_settlement_currency',           'marketplace_settlements'),
                ('chk_settlement_provider',           'marketplace_settlements'),
                ('chk_settlement_reversal_reference', 'marketplace_settlements'),
                ('uq_settlement_reference_reversal',  'marketplace_settlements'),
                ('valid_subscription_status',         'library_subscriptions'),
                ('chk_ls_active_has_period',          'library_subscriptions'),
                ('chk_ls_split_conserved',            'library_subscriptions'),
                ('pk_subscription_allowed_transitions',
                                                      'marketplace_subscription_allowed_transitions')
            ) AS expected(conname, table_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_constraint pc
                WHERE pc.conname  = expected.conname
                  AND pc.conrelid = ('public.' || expected.table_name)::regclass);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '008 postflight failed: constraint(s) % are absent '
                        '(Requirements 10.5, 10.8, 11.13, 24.2, 24.3).',
                        missing;
    END IF;

    -- (c2) valid_subscription_status really admits all seven values. The
    --      existence check above would pass on the four-value constraint, and
    --      four is exactly the shape this file exists to widen - so the
    --      definition itself is read.
    SELECT pg_get_constraintdef(pc.oid) INTO status_def
      FROM pg_constraint pc
     WHERE pc.conrelid = 'public.library_subscriptions'::regclass
       AND pc.conname  = 'valid_subscription_status';

    IF status_def IS NULL
       OR status_def NOT LIKE '%''pending''%'
       OR status_def NOT LIKE '%''active''%'
       OR status_def NOT LIKE '%''expired''%'
       OR status_def NOT LIKE '%''cancelled''%'
       OR status_def NOT LIKE '%''refunded''%'
       OR status_def NOT LIKE '%''payment_failed''%'
       OR status_def NOT LIKE '%''suspended''%' THEN
        RAISE EXCEPTION
            '008 postflight failed: valid_subscription_status on '
            'public.library_subscriptions does not admit all 7 '
            'Subscription_State spellings of Requirement 11.1. Definition: '
            '%. Without the widening, every transition into ''refunded'', '
            '''payment_failed'' or ''suspended'' is refused by the CHECK '
            'rather than by the state machine, and the twelve-pair seed '
            'describes edges the column cannot hold.',
            coalesce(status_def, '(no such constraint)');
    END IF;

    -- (d) Every named index exists. idx_lib_subs_expiry is the one whose
    --     PARTIAL predicate is the load-bearing part, so it is re-checked for
    --     partiality below.
    SELECT string_agg(format('%s on %s', expected.indexname, expected.table_name),
                      ', ' ORDER BY expected.indexname)
      INTO missing
      FROM (VALUES
                ('idx_settlements_owner_currency',  'marketplace_settlements'),
                ('idx_settlements_subscription',    'marketplace_settlements'),
                ('idx_settlements_purchaser',       'marketplace_settlements'),
                ('idx_settlements_listing',         'marketplace_settlements'),
                ('idx_lib_sub_transitions',         'library_subscription_transitions'),
                ('idx_lib_sub_transitions_user',    'library_subscription_transitions'),
                ('idx_lib_subs_expiry',             'library_subscriptions')
            ) AS expected(indexname, table_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_indexes pi
                WHERE pi.schemaname      = 'public'
                  AND pi.tablename       = expected.table_name
                  AND pi.indexname::TEXT = expected.indexname);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '008 postflight failed: index(es) % were not created '
                        '(Requirement 24.4).', missing;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public'
           AND tablename  = 'library_subscriptions'
           AND indexname  = 'idx_lib_subs_expiry'
           AND indexdef ILIKE '%WHERE%'
           AND indexdef ILIKE '%active%'
           AND indexdef ILIKE '%suspended%'
    ) THEN
        RAISE EXCEPTION
            '008 postflight failed: idx_lib_subs_expiry exists but is not '
            'PARTIAL over status IN (''active'',''suspended''). The predicate '
            'is load-bearing: it is exactly the expiry sweep''s own filter '
            '(Requirement 11.8), so without it the index holds every '
            'historical subscription forever and the sweep still has to '
            'filter on status.';
    END IF;

    -- (e) The three functions and the seven triggers exist.
    SELECT string_agg(expected.proname, ', ' ORDER BY expected.proname)
      INTO missing
      FROM (VALUES ('marketplace_subscription_guard'),
                   ('marketplace_settlement_append_only_guard'),
                   ('library_subscriptions_mirror_period'),
                   ('marketplace_touch_updated_at'))
             AS expected(proname)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_proc p
                 JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public'
                  AND p.proname = expected.proname);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '008 postflight failed: function(s) % are absent.',
                        missing;
    END IF;

    SELECT string_agg(format('%s on %s', expected.tgname, expected.table_name),
                      ', ' ORDER BY expected.tgname)
      INTO missing
      FROM (VALUES
                ('trg_subscription_transition_guard',   'library_subscriptions'),
                ('trg_lib_subs_period_mirror',          'library_subscriptions'),
                ('trg_settlements_append_only',         'marketplace_settlements'),
                ('trg_lib_sub_transitions_append_only', 'library_subscription_transitions'),
                ('trg_marketplace_settlements_updated_at',
                                                        'marketplace_settlements'),
                ('trg_library_subscription_transitions_updated_at',
                                                        'library_subscription_transitions'),
                ('trg_marketplace_subscription_allowed_transitions_updated_at',
                                                        'marketplace_subscription_allowed_transitions')
            ) AS expected(tgname, table_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_trigger t
                WHERE t.tgname   = expected.tgname
                  AND t.tgrelid  = ('public.' || expected.table_name)::regclass
                  AND NOT t.tgisinternal);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '008 postflight failed: trigger(s) % are absent. '
                        'Requirements 11.3 and 11.14''s "THE '
                        'Persistence_Layer SHALL reject the write" and '
                        'Requirements 10.8 and 11.12''s immutability depend '
                        'on them.', missing;
    END IF;

    -- (f) The seed holds exactly the twelve pairs of Requirement 11.2, no
    --     thirteenth, no self-edge, and no edge out of the terminal state.
    SELECT count(*) INTO seed_rows
      FROM public.marketplace_subscription_allowed_transitions;

    IF seed_rows <> 12 THEN
        RAISE EXCEPTION
            '008 postflight failed: '
            'marketplace_subscription_allowed_transitions holds % row(s), not '
            'the 12 permitted transitions of Requirement 11.2. It must equal '
            'SUBSCRIPTION_TRANSITION_TEXT_PAIRS in '
            'backend_app/backend/marketplace/subscription_state.py, which '
            'task 14.6''s state-agreement test asserts.', seed_rows;
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.marketplace_subscription_allowed_transitions
         WHERE from_state = to_state
    ) THEN
        RAISE EXCEPTION
            '008 postflight failed: '
            'marketplace_subscription_allowed_transitions holds a self-edge. '
            'Requirement 11.2 lists no pair from a value to that same value, '
            'and the guard relies on the absence of such a row.';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.marketplace_subscription_allowed_transitions
         WHERE from_state = 'refunded'
    ) THEN
        RAISE EXCEPTION
            '008 postflight failed: '
            'marketplace_subscription_allowed_transitions holds an edge OUT '
            'of ''refunded''. Requirement 11.2 makes it the one terminal '
            'state: a reversal ends that subscription''s life and '
            're-subscribing is a new row.';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.marketplace_subscription_allowed_transitions
         WHERE from_state <> lower(from_state)
            OR to_state   <> lower(to_state)
    ) THEN
        RAISE EXCEPTION
            '008 postflight failed: '
            'marketplace_subscription_allowed_transitions holds a non-lowercase '
            'state spelling. These are library_subscriptions.status values, '
            'that column is lowercase, and the guard compares them to it '
            'directly - an uppercase row would be an edge no transition can '
            'ever match.';
    END IF;

    -- (g) RLS is on, and each new table has at least its two policies.
    SELECT string_agg(expected.table_name, ', ' ORDER BY expected.table_name)
      INTO rls_off
      FROM (VALUES ('marketplace_settlements'),
                   ('library_subscription_transitions'),
                   ('marketplace_subscription_allowed_transitions'))
             AS expected(table_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_class c
                WHERE c.oid = ('public.' || expected.table_name)::regclass
                  AND c.relrowsecurity);

    IF rls_off IS NOT NULL THEN
        RAISE EXCEPTION '008 postflight failed: row level security is '
                        'DISABLED on %. Requirement 21.2 requires it on every '
                        'table this specification introduces.', rls_off;
    END IF;

    SELECT string_agg(expected.table_name, ', ' ORDER BY expected.table_name)
      INTO missing
      FROM (VALUES ('marketplace_settlements'),
                   ('library_subscription_transitions'),
                   ('marketplace_subscription_allowed_transitions'))
             AS expected(table_name)
     WHERE (SELECT count(*) FROM pg_policies p
             WHERE p.schemaname = 'public'
               AND p.tablename  = expected.table_name) < 2;

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '008 postflight failed: table(s) % have fewer than '
                        'the 2 policies this file creates, so with RLS '
                        'enabled they are unreachable to every non-superuser '
                        'role (Requirements 21.2, 21.3).', missing;
    END IF;

    IF (SELECT count(*) FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'marketplace_settlements') < 3 THEN
        RAISE EXCEPTION
            '008 postflight failed: public.marketplace_settlements has fewer '
            'than 3 policies. design.md § Data Models requires a SECOND '
            'owner-scoped policy on this table so the PURCHASER can read '
            'their own payments without seeing the owner''s other earnings.';
    END IF;

    -- (h) Nothing else moved on the one pre-existing table this file touches.
    SELECT count(*) INTO lsub_policies_now
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'library_subscriptions';
    SELECT count(*) INTO lsub_indexes_now
      FROM pg_indexes
     WHERE schemaname = 'public' AND tablename = 'library_subscriptions';
    SELECT count(*) INTO lsub_triggers_now
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.library_subscriptions'::regclass
       AND NOT t.tgisinternal;

    IF lsub_policies_now <> lsub_policies_before THEN
        RAISE EXCEPTION '008 postflight failed: the row-level security policy '
                        'count on public.library_subscriptions moved from % '
                        'to %. This file must not add, alter or remove a '
                        'policy on a pre-existing table - '
                        'migrations/006_reconcile_production_database.sql '
                        'owns lib_subs_owner_access and lib_subs_service_role '
                        '(Requirement 25.1).',
                        lsub_policies_before, lsub_policies_now;
    END IF;

    IF lsub_indexes_now > lsub_indexes_before + 1 THEN
        RAISE EXCEPTION '008 postflight failed: the index count on '
                        'public.library_subscriptions moved from % to %, more '
                        'than the 1 index (idx_lib_subs_expiry) this file '
                        'creates there.',
                        lsub_indexes_before, lsub_indexes_now;
    END IF;

    IF lsub_triggers_now > lsub_triggers_before + 2 THEN
        RAISE EXCEPTION '008 postflight failed: the user trigger count on '
                        'public.library_subscriptions moved from % to %, more '
                        'than the 2 triggers '
                        '(trg_subscription_transition_guard, '
                        'trg_lib_subs_period_mirror) this file creates there.',
                        lsub_triggers_before, lsub_triggers_now;
    END IF;

    RAISE NOTICE '008 complete: marketplace_settlements, '
                 'library_subscription_transitions and '
                 'marketplace_subscription_allowed_transitions exist with % '
                 'seed rows, every named constraint and index, 3 new '
                 'functions and 7 triggers, RLS enabled with the owner, '
                 'purchaser and service-role policies. '
                 'library_subscriptions gained owner_id, price_minor, '
                 'owner_share_minor, platform_fee_minor, period_start, '
                 'period_expiry, provider, provider_reference, '
                 'provider_session_at, failure_cause, failed_at and '
                 'renewal_enabled, the widened valid_subscription_status, '
                 'chk_ls_active_has_period, chk_ls_split_conserved, '
                 'idx_lib_subs_expiry, trg_subscription_transition_guard and '
                 'trg_lib_subs_period_mirror; its policies % (unchanged), '
                 'indexes % -> %, triggers % -> %. No existing row was '
                 'rewritten.',
                 seed_rows, lsub_policies_now,
                 lsub_indexes_before, lsub_indexes_now,
                 lsub_triggers_before, lsub_triggers_now;
END $$;

COMMIT;

-- ==========================================================================
-- VERIFICATION QUERIES - run these after applying, the way 005a/005b/006/007 do
-- ==========================================================================
--
-- 1) The three tables and their column counts:
--
--    SELECT table_name, count(*) AS columns
--      FROM information_schema.columns
--     WHERE table_schema = 'public'
--       AND table_name IN ('marketplace_settlements',
--                          'library_subscription_transitions',
--                          'marketplace_subscription_allowed_transitions')
--     GROUP BY table_name ORDER BY table_name;
--
--    Expect 3 rows: library_subscription_transitions 12,
--    marketplace_settlements 16,
--    marketplace_subscription_allowed_transitions 4.
--
-- 2) The twelve transition pairs, which MUST equal
--    SUBSCRIPTION_TRANSITION_TEXT_PAIRS in
--    backend_app/backend/marketplace/subscription_state.py:
--
--    SELECT from_state, to_state
--      FROM public.marketplace_subscription_allowed_transitions
--     ORDER BY from_state, to_state;
--
--    Expect exactly 12 rows:
--      active         -> cancelled
--      active         -> expired
--      active         -> refunded
--      active         -> suspended
--      cancelled      -> active
--      expired        -> active
--      payment_failed -> pending
--      pending        -> active
--      pending        -> cancelled
--      pending        -> payment_failed
--      suspended      -> active
--      suspended      -> expired
--    All lowercase. No row with from_state = to_state, and no row with
--    from_state = 'refunded'.
--
-- 3) The CHECK and UNIQUE constraints:
--
--    SELECT conrelid::regclass AS on_table, conname, contype, convalidated,
--           pg_get_constraintdef(oid)
--      FROM pg_constraint
--     WHERE conname IN ('chk_settlement_amounts', 'chk_settlement_conserved',
--                       'chk_settlement_currency', 'chk_settlement_provider',
--                       'chk_settlement_reversal_reference',
--                       'uq_settlement_reference_reversal',
--                       'valid_subscription_status',
--                       'chk_ls_active_has_period',
--                       'chk_ls_split_conserved')
--     ORDER BY on_table, conname;
--
--    Expect 9 rows. valid_subscription_status must list all SEVEN lowercase
--    spellings. chk_ls_active_has_period may show convalidated = false on a
--    database carrying legacy 'active' rows with no period - see query 10.
--
-- 4) The indexes, and above all the PARTIAL predicate of the sweep index:
--
--    SELECT tablename, indexname, indexdef
--      FROM pg_indexes
--     WHERE schemaname = 'public'
--       AND (tablename IN ('marketplace_settlements',
--                          'library_subscription_transitions')
--            OR indexname = 'idx_lib_subs_expiry')
--     ORDER BY tablename, indexname;
--
--    idx_lib_subs_expiry must read:
--      CREATE INDEX idx_lib_subs_expiry ON public.library_subscriptions
--      USING btree (period_expiry) WHERE (status = ANY (ARRAY['active'::text,
--      'suspended'::text]))
--
-- 5) The state machine is enforced against a DIRECT UPDATE (Requirement 11.3,
--    Property P-49). Run inside a transaction you ROLL BACK:
--
--    BEGIN;
--      SELECT id, status, period_start, period_expiry
--        FROM public.library_subscriptions LIMIT 1;
--      -- a 'pending' row: pending -> refunded is not one of the twelve edges
--      UPDATE public.library_subscriptions
--         SET status = 'refunded' WHERE id = '<the id>';
--      -- expect: ERROR 23514 disallowed Subscription_State transition
--      --         pending -> refunded
--    ROLLBACK;
--
-- 6) A free month is not obtainable by direct SQL (Requirements 11.6, 11.14):
--
--    BEGIN;
--      -- no marketplace_settlements row for this subscription at all
--      UPDATE public.library_subscriptions
--         SET status = 'active',
--             period_start = now(),
--             period_expiry = now() + interval '1 month'
--       WHERE id = '<a pending id>';
--      -- expect: ERROR 23514 transition pending -> active requires a
--      --         confirmed payment: no non-reversal marketplace_settlements
--      --         row for subscription <uuid> has settled_at at or after the
--      --         current period expiry <null>
--    ROLLBACK;
--
--    And the extension attempt on an already-active row, which the guard
--    refuses through the period-expiry comparison rather than the edge check:
--
--    BEGIN;
--      UPDATE public.library_subscriptions
--         SET status = 'expired' WHERE id = '<an active id>';   -- allowed
--      UPDATE public.library_subscriptions
--         SET status = 'active',
--             period_expiry = period_expiry + interval '1 month'
--       WHERE id = '<the same id>';
--      -- expect: ERROR 23514 ... requires a confirmed payment ... at or
--      --         after the current period expiry <the old expiry>
--    ROLLBACK;
--
-- 7) chk_ls_active_has_period (Requirement 11.13, P-10):
--
--    BEGIN;
--      UPDATE public.library_subscriptions
--         SET period_expiry = NULL WHERE id = '<an active id>';
--      -- expect: ERROR 23514 chk_ls_active_has_period
--    ROLLBACK;
--
-- 8) The append-only guards (Requirements 10.8, 11.12):
--
--    BEGIN;
--      UPDATE public.marketplace_settlements
--         SET amount_minor = 1 WHERE id = '<any id>';
--      -- expect: ERROR 23514 marketplace_settlements is append-only
--    ROLLBACK;
--    BEGIN;
--      DELETE FROM public.marketplace_settlements WHERE id = '<any id>';
--      -- expect: ERROR 23514 ... DELETE is not permitted while subscription
--      --         <uuid> exists
--    ROLLBACK;
--
--    And the RESTRICT that must hold (Requirements 10.8, 11.11):
--
--    BEGIN;
--      DELETE FROM public.library_subscriptions WHERE id = '<a settled id>';
--      -- expect: ERROR 23503 update or delete on table
--      --         "library_subscriptions" violates foreign key constraint on
--      --         table "marketplace_settlements"
--    ROLLBACK;
--
-- 9) The split is conserved and the ledger cannot hold an unbalanced row
--    (Requirements 10.2, 10.5):
--
--    BEGIN;
--      INSERT INTO public.marketplace_settlements
--        (subscription_id, listing_id, owner_id, purchaser_id, amount_minor,
--         owner_share_minor, platform_fee_minor, currency, provider,
--         provider_reference, settled_at)
--      VALUES ('<sub>', '<listing>', '<owner>', '<purchaser>', 4999,
--              4499, 499, 'USD', 'stripe', 'test_ref_1', now());
--      -- expect: ERROR 23514 chk_settlement_conserved  (4499 + 499 <> 4999)
--    ROLLBACK;
--
--    The correct split of 4999 is 4499 / 500.
--
--    And the idempotency key (Requirements 9.7, 10.10):
--
--    BEGIN;
--      -- insert one valid row, then the same provider_reference again
--      -- expect the second: ERROR 23505 duplicate key value violates unique
--      --         constraint "uq_settlement_reference_reversal"
--    ROLLBACK;
--
-- 10) Whether chk_ls_active_has_period could be validated, and what to
--     reconcile if not:
--
--    SELECT conname, convalidated FROM pg_constraint
--     WHERE conrelid = 'public.library_subscriptions'::regclass
--       AND conname IN ('chk_ls_active_has_period',
--                       'valid_subscription_status');
--
--    SELECT id, user_id, status, started_at, expires_at,
--           period_start, period_expiry
--      FROM public.library_subscriptions
--     WHERE status = 'active'
--       AND (period_start IS NULL OR period_expiry IS NULL
--            OR period_expiry <= period_start);
--
--    Any rows here predate the period columns. The constraint is enforced for
--    every new write regardless; set the period from started_at/expires_at
--    where those are meaningful, or move the row to 'expired', then re-run
--    this file to validate.
--
--    SELECT DISTINCT status FROM public.library_subscriptions ORDER BY status;
--
--    Every value must be one of the seven lowercase spellings.
--
-- 11) The period mirror (Requirement 25):
--
--    BEGIN;
--      UPDATE public.library_subscriptions
--         SET period_start = now(), period_expiry = now() + interval '1 month'
--       WHERE id = '<a non-active id>';
--      SELECT period_start, started_at, period_expiry, expires_at
--        FROM public.library_subscriptions WHERE id = '<the id>';
--      -- expect started_at = period_start and expires_at = period_expiry
--    ROLLBACK;
--
-- 12) RLS and the policies:
--
--    SELECT tablename, policyname, cmd, roles
--      FROM pg_policies
--     WHERE schemaname = 'public'
--       AND tablename IN ('marketplace_settlements',
--                         'library_subscription_transitions',
--                         'marketplace_subscription_allowed_transitions')
--     ORDER BY tablename, policyname;
--
--    Expect 7 rows: 3 on marketplace_settlements (owner read, purchaser read,
--    service role), 2 each on the other two. And library_subscriptions must be
--    UNCHANGED - compare against the count section 0 reported.
--
-- 13) Grants. Expect authenticated: SELECT only on marketplace_settlements and
--     on marketplace_subscription_allowed_transitions, SELECT+INSERT on
--     library_subscription_transitions. No UPDATE and no DELETE anywhere,
--     including for service_role.
--
--    SELECT table_name, grantee, string_agg(privilege_type, ',' ORDER BY
--           privilege_type) AS privileges
--      FROM information_schema.role_table_grants
--     WHERE table_schema = 'public'
--       AND table_name IN ('marketplace_settlements',
--                          'library_subscription_transitions',
--                          'marketplace_subscription_allowed_transitions')
--       AND grantee IN ('anon', 'authenticated', 'service_role')
--     GROUP BY table_name, grantee ORDER BY table_name, grantee;
--
-- 14) Idempotency (Requirements 24.7, 24.8, Property P-58). Re-running this
--     whole file must report no error, must leave the seed at 12 rows, must
--     DROP NOTHING (the widening branch recognises the already-widened
--     constraint and returns early), and must leave every count above
--     unchanged.
--
-- ==========================================================================
-- END OF MIGRATION 008. NEXT IN DEPENDENCY ORDER:
--   009_paper_trading.sql  (independent of this file; needs auth.users,
--                           library_strategies and strategy_versions)
-- ==========================================================================
