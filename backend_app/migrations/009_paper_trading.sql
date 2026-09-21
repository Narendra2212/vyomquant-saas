-- 009_paper_trading.sql  (backend_app/migrations, migration 009)
--
-- PURPOSE
--   Give the Paper_Session its durable storage: the eleven paper_* tables of
--   design.md § Data Models, plus the two two-column transition seed tables
--   that make the Paper_Order_State and Paper_Session state machines the
--   database's business rather than only the service layer's. Until this file
--   is applied every /api/paper/* endpoint answers 503
--   PAPER_PERSISTENCE_UNAVAILABLE naming this file (design.md § "Migration
--   refusal, not degradation"), because paper_repository probes for the
--   paper_accounts relation once per process and caches the verdict.
--
--   Requirements 1.2, 16.1, 16.4, 16.11, 17.1, 17.11, 18.4, 18.5, 18.10,
--   19.3, 21.2, 21.3, 24.1, 24.2, 24.3, 24.4, 24.5, 24.7.
--   Source of the DDL: design.md § Data Models -> paper_sessions,
--   paper_accounts, paper_orders, paper_fills, paper_positions,
--   paper_balance_events, paper_trades, paper_equity_snapshots, paper_metrics,
--   paper_events, paper_market_events, statement for statement,
--   schema-qualified with public., with every ADD CONSTRAINT behind a
--   pg_constraint guard, every CREATE TRIGGER behind a pg_trigger guard and
--   every CREATE POLICY behind a pg_policies guard - the same shape 007 and
--   008 use.
--
-- WHAT THIS FILE ADDS
--      1  paper_order_allowed_transitions    the nine-pair seed
--      2  paper_session_allowed_transitions  the six-pair seed
--      3  paper_sessions                     the session row (RLS owner root)
--      4  paper_accounts                     simulated balances
--      5  paper_orders                       the order book
--      6  paper_fills                        append-only fill log
--      7  paper_positions                    open/closed positions
--      8  paper_balance_events               append-only balance ledger
--      9  paper_trades                       append-only closed round-trips
--     10  paper_equity_snapshots             append-only equity curve
--     11  paper_metrics                      computed session metrics
--     12  paper_events                       append-only Paper_Channel log
--     13  paper_market_events                append-only market-data log
--     14  five functions and the triggers (updated_at, the two transition
--         guards, the config-immutability guard, the append-only guard)
--     15  row level security, policies, grants
--     16  comments, postflight, verification queries
--
--   Nothing about signals.environment or signals.paper_session_id is here:
--   that is migration 010, which depends on this file for the FK target.
--
-- WHY THE TWO SEED TABLES ARE TABLES AND NOT INLINE CASES
--   paper_order_allowed_transitions holds the same nine pairs as
--   PAPER_ORDER_TRANSITIONS in backend_app/backend/paper/paper_order_state.py,
--   and paper_session_allowed_transitions the six pairs of Requirement 17.7.
--   They are tables because task 14.6's parity test reads the INSERT
--   statements below and the Python constant and asserts they are the SAME
--   SET - the technique 007's marketplace_submission_allowed_transitions and
--   tests/test_submission_state_agreement.py already use. An inline CASE
--   inside the trigger would be a second definition of the state machine that
--   no test could compare against the first, and the two would drift the
--   first time a state was added.
--
--   The nine Paper_Order_State pairs (Requirement 16.2), and no tenth. This
--   is the one machine of the four with a SELF-transition -
--   PARTIALLY_FILLED -> PARTIALLY_FILLED - so that successive partial fills of
--   one order are representable. Every other state lists no self-edge:
--     CREATED          -> ACCEPTED, REJECTED
--     ACCEPTED         -> PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED
--     PARTIALLY_FILLED -> PARTIALLY_FILLED, FILLED, CANCELLED
--     FILLED, CANCELLED, REJECTED -> (terminal, no row; Requirement 16.3)
--
--   The six Paper_Session pairs (Requirement 17.7), and no seventh:
--     CREATED -> RUNNING          (start)
--     RUNNING -> PAUSED           (pause)
--     PAUSED  -> RUNNING          (resume)
--     RUNNING -> STOPPED          (stop)
--     PAUSED  -> STOPPED          (stop)
--     STOPPED -> CREATED          (reset - back to the fresh, restartable state)
--   reset returns a STOPPED session to CREATED, the one state from which a
--   start is permitted, so "reset from STOPPED" of Requirement 17.7 leads
--   somewhere rather than nowhere. No row has from_state = to_state for the
--   session machine, so a same-value write is short-circuited by the guard's
--   first branch rather than by a self-edge.
--
-- WHY EVERYTHING IS NUMERIC(28,10) AND NEVER double precision
--   design.md § Data Models -> paper_accounts: NUMERIC(28,10) rather than
--   NUMERIC(20,8) because quantity precision on crypto pairs reaches 8
--   decimals and a notional in a 2-decimal currency needs 18 integral digits
--   of headroom. Every quantity, price, balance, pnl and equity column is
--   NUMERIC(28,10); Decimal values are read and written as strings so no
--   driver-side float conversion occurs (design.md § "Everything financial").
--   The three *_minor columns (fee, slippage) are BIGINT Minor_Units, exact
--   integers, never fractional. A double precision anywhere here would
--   reintroduce the binary floating-point error Requirement 18.14's equity
--   identity forbids, so the shape assertions below refuse it for every
--   NUMERIC column.
--
-- WHY EVERY CHILD CASCADES FROM paper_sessions
--   design.md § Data Models -> "Delete rules": every paper_* child cascades
--   from paper_sessions, which cascades from auth.users (Requirement 24.1).
--   paper_accounts.session_id is NULLABLE (NULL is the default account the
--   existing endpoints serve) and ON DELETE CASCADE; paper_orders and every
--   other child carry a NOT NULL session_id ON DELETE CASCADE. Deleting a
--   user removes their sessions, and each session removes its accounts,
--   orders, fills, positions, balance events, trades, equity snapshots,
--   metrics, events and market events - one declared path, no orphan.
--
-- WHY THE APPEND-ONLY GUARD EXEMPTS A CASCADE
--   Requirements 3.10-style immutability applies to paper_fills, paper_trades,
--   paper_equity_snapshots, paper_events, paper_market_events and
--   paper_balance_events: each is append-only (design.md § Data Models). An
--   unconditional BEFORE DELETE ... RAISE would make deleting a session, or a
--   user account, impossible - the declared cascade would reach these child
--   rows and abort. paper_append_only_guard() therefore refuses every UPDATE
--   unconditionally, and refuses a DELETE only WHILE THE PARENT SESSION ROW
--   STILL EXISTS. PostgreSQL runs a referential CASCADE as an AFTER trigger on
--   the parent, so by the time a child's BEFORE DELETE trigger fires during a
--   cascade the parent paper_sessions row is already gone, while a direct
--   "DELETE FROM paper_fills WHERE ..." always runs with the parent present.
--   This is a SEPARATE function from 007's public.marketplace_append_only_guard()
--   (which resolves its parent through submission_id) and 008's
--   public.marketplace_settlement_append_only_guard() (subscription_id):
--   generalising either would be a change to an existing control. It resolves
--   its parent through OLD.session_id against paper_sessions.
--
-- DELIBERATE ADDITIONS BEYOND THE DESIGN'S DDL, AND WHY
--   The same seven the 007 header enumerates, applied here: a preflight that
--   names a missing auth.users or library_strategies instead of a bare
--   undefined_table; a shape assertion after each CREATE TABLE IF NOT EXISTS
--   (CREATE TABLE IF NOT EXISTS is silent about a pre-existing table of a
--   different shape); pg_constraint-guarded ADD CONSTRAINT (no ADD CONSTRAINT
--   IF NOT EXISTS exists and a bare re-run raises 42710); pg_trigger-guarded
--   CREATE TRIGGER (no CREATE TRIGGER IF NOT EXISTS and the baseline predates
--   CREATE OR REPLACE TRIGGER); pg_policies-guarded CREATE POLICY (no CREATE
--   POLICY IF NOT EXISTS, and a DROP would open a policy-less window); reuse
--   of public.marketplace_touch_updated_at() from 007 rather than redefining
--   it; and comments plus a postflight that proves every object exists.
--
-- APPLICATION
--   NOT applied automatically. Applied BY HAND, per file, the way
--   007 and 008 were. DEPENDS ON auth.users (owner cascade) and
--   library_strategies (paper_sessions.listing_id references it). No FK to
--   strategy_versions or public.strategies: those tables' id types differ
--   between environments (see 006's header on the same divergence), and an
--   ALTER ... ADD FOREIGN KEY validated against existing rows could abort this
--   transaction. version_id and source_strategy_id are resolved server-side
--   and are NOT NULL without a foreign key, the same choice 007 makes for
--   source_strategy_id.
--
-- SAFETY
--   * Additive only (Requirement 24.7). No DROP TABLE, no DROP COLUMN, no DROP
--     CONSTRAINT, no ALTER ... RENAME, no DELETE FROM, no TRUNCATE, no UPDATE
--     of an existing row. The two seeds are INSERT ... ON CONFLICT DO NOTHING.
--     No pre-existing table is touched at all - every table here is new.
--   * Fully idempotent (Requirements 24.7, 24.8). CREATE TABLE IF NOT EXISTS;
--     every ADD CONSTRAINT behind a pg_constraint guard; CREATE INDEX IF NOT
--     EXISTS; CREATE OR REPLACE FUNCTION; every CREATE TRIGGER behind a
--     pg_trigger guard; every CREATE POLICY behind a pg_policies guard; the
--     seeds ON CONFLICT DO NOTHING; COMMENT replaces; GRANT/REVOKE idempotent
--     by definition. A second application adds nothing and raises nothing.
--   * One transaction. Either all thirteen tables, both seeds, every
--     constraint, index, function, trigger, policy and grant exist, or none
--     do. A paper_orders without trg_paper_order_transition_guard - an order
--     table whose state machine is unenforced - is never visible to a session.

-- ==========================================================================
-- task 11.4: the Paper_Session side of the platform
-- ==========================================================================

BEGIN;

-- 0) Preflight ---------------------------------------------------------
-- Read-only assertions. Fails with a readable message instead of a bare
-- undefined_table from inside a REFERENCES clause. No pre-existing paper_*
-- table is touched by this file, so unlike 007 there is no inventory to
-- record here.
-- Idempotent: reads catalogues, writes nothing.
DO $$
BEGIN
    IF to_regclass('auth.users') IS NULL THEN
        RAISE EXCEPTION
            '009 precondition failed: table auth.users does not exist. '
            'paper_sessions.user_id references it, and Requirement 24.1 '
            'requires a foreign key from every row introduced by this '
            'specification to the user it belongs to. This database is not a '
            'Supabase database, or the auth schema has not been provisioned.';
    END IF;

    IF to_regclass('public.library_strategies') IS NULL THEN
        RAISE EXCEPTION
            '009 precondition failed: table public.library_strategies does '
            'not exist. paper_sessions.listing_id references it (a subscribed '
            'Listing being paper-traded), ON DELETE SET NULL. Apply the '
            'library_strategies definition first '
            '(archived_migrations/root_migrations/'
            '001_create_library_strategies.sql). This file does not create '
            'it - doing so would add a second divergent definition of the '
            'Listing table.';
    END IF;

    -- gen_random_uuid() is the default of every id column below. It lives in
    -- pgcrypto on the baseline this repository targets; 001 and 007 already
    -- rely on it, so this is a readable failure rather than a bare
    -- undefined_function from inside a column default.
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE p.proname = 'gen_random_uuid'
    ) THEN
        RAISE EXCEPTION
            '009 precondition failed: gen_random_uuid() is not available. '
            'Every paper_* id defaults to it. Enable it with '
            'CREATE EXTENSION IF NOT EXISTS pgcrypto; (001 and 007 already '
            'depend on it).';
    END IF;

    RAISE NOTICE '009 preflight: auth.users and public.library_strategies '
                 'present, gen_random_uuid() available. Creating 13 new '
                 'paper_* tables; no pre-existing table is touched.';
END $$;

-- ==========================================================================
-- SECTION 1 - paper_order_allowed_transitions and its seed
-- ==========================================================================
-- Created FIRST because paper_order_transition_guard() in section 14 selects
-- from it: the guard must never be able to exist while the table it consults
-- does not, or an illegal transition would raise 42P01 instead of 23514 and
-- the error paper_simulator translates would be the wrong one.
--
-- Composite primary key (from_state, to_state), no id column - the pair IS
-- the identity of a transition, and ON CONFLICT DO NOTHING needs a unique
-- index over exactly those two columns to be idempotent. Same departure from
-- the "every new table carries id UUID" rule that 007's
-- marketplace_submission_allowed_transitions makes, for the same reasons.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS.
CREATE TABLE IF NOT EXISTS public.paper_order_allowed_transitions (
    from_state  TEXT NOT NULL,
    to_state    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_paper_order_allowed_transitions PRIMARY KEY (from_state, to_state)
);

-- 1a) Shape assertion --------------------------------------------------
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
            AND actual.table_name::TEXT   = 'paper_order_allowed_transitions'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.paper_order_allowed_transitions has column(s) of the '
            'wrong shape: %. A pre-existing table of that name was left as it '
            'was, because CREATE TABLE IF NOT EXISTS does not alter one. '
            'Reconcile it by hand before re-running this migration.', problems;
    END IF;
END $$;

-- 1b) The nine-pair seed -----------------------------------------------
-- Requirement 16.2's nine permitted transitions, and no tenth. This set MUST
-- equal PAPER_ORDER_TRANSITIONS in
-- backend_app/backend/paper/paper_order_state.py; task 14.6's parity test
-- parses the VALUES list below and that Python constant and fails if either
-- holds a pair the other does not. Keep the two in step or the test says so.
--
-- The single self-transition PARTIALLY_FILLED -> PARTIALLY_FILLED is present:
-- it is what makes successive partial fills of one order representable
-- (Requirement 16.2). No other state lists itself, and the three terminal
-- states (FILLED, CANCELLED, REJECTED) appear only as a to_state - nothing
-- leaves them (Requirement 16.3).
--
-- Idempotent: ON CONFLICT DO NOTHING against pk_paper_order_allowed_
-- transitions. NOTHING IS EVER DELETED FROM HERE by this file.
INSERT INTO public.paper_order_allowed_transitions (from_state, to_state)
VALUES
    ('CREATED',          'ACCEPTED'),
    ('CREATED',          'REJECTED'),
    ('ACCEPTED',         'PARTIALLY_FILLED'),
    ('ACCEPTED',         'FILLED'),
    ('ACCEPTED',         'CANCELLED'),
    ('ACCEPTED',         'REJECTED'),
    ('PARTIALLY_FILLED', 'PARTIALLY_FILLED'),
    ('PARTIALLY_FILLED', 'FILLED'),
    ('PARTIALLY_FILLED', 'CANCELLED')
ON CONFLICT (from_state, to_state) DO NOTHING;

-- ==========================================================================
-- SECTION 2 - paper_session_allowed_transitions and its seed
-- ==========================================================================
-- Created before paper_session_guard() selects from it, for the same reason
-- as section 1. Composite primary key, no id column.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS.
CREATE TABLE IF NOT EXISTS public.paper_session_allowed_transitions (
    from_state  TEXT NOT NULL,
    to_state    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_paper_session_allowed_transitions PRIMARY KEY (from_state, to_state)
);

-- 2a) Shape assertion --------------------------------------------------
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
            AND actual.table_name::TEXT   = 'paper_session_allowed_transitions'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.paper_session_allowed_transitions has column(s) of the '
            'wrong shape: %. A pre-existing table of that name was left as it '
            'was, because CREATE TABLE IF NOT EXISTS does not alter one. '
            'Reconcile it by hand before re-running this migration.', problems;
    END IF;
END $$;

-- 2b) The six-pair seed ------------------------------------------------
-- Requirement 17.7's operations as edges: start CREATED -> RUNNING; pause
-- RUNNING -> PAUSED; resume PAUSED -> RUNNING; stop RUNNING -> STOPPED and
-- PAUSED -> STOPPED; reset STOPPED -> CREATED. Six pairs, no seventh, and no
-- self-edge. Task 27.3 defines the matching Python constant; keep the two in
-- step.
--
-- Idempotent: ON CONFLICT DO NOTHING.
INSERT INTO public.paper_session_allowed_transitions (from_state, to_state)
VALUES
    ('CREATED', 'RUNNING'),
    ('RUNNING', 'PAUSED'),
    ('PAUSED',  'RUNNING'),
    ('RUNNING', 'STOPPED'),
    ('PAUSED',  'STOPPED'),
    ('STOPPED', 'CREATED')
ON CONFLICT (from_state, to_state) DO NOTHING;

-- ==========================================================================
-- SECTION 3 - paper_sessions
-- ==========================================================================
-- design.md § Data Models -> "paper_sessions", column for column. This is the
-- RLS-owner root: every other paper_* table cascades from it, and it cascades
-- from auth.users. It is the resource the Paper_Channel (paper.{session_id})
-- and websocket_auth._PAPER_SESSIONS_RELATION resolve.
--
--   listing_id       the subscribed Listing being paper-traded, ON DELETE SET
--                    NULL - NULL for an owned strategy. A deleted Listing does
--                    not delete the session's history (design.md § "Delete
--                    rules").
--   environment      DEFAULT 'PAPER' and chk_paper_session_environment forces
--                    it to stay 'PAPER': a paper session cannot be relabelled
--                    into a live one (Requirement 13, the Execution_Environment
--                    boundary).
--   session_state    the four-value machine of Requirement 17.7, enforced by
--                    chk_paper_session_state and, for transitions, by
--                    trg_paper_session_guard against
--                    paper_session_allowed_transitions.
--   config           JSONB frozen at start; trg_paper_session_config_immutable
--                    refuses any later change (Requirement 16.12).
--   event_sequence   the per-session monotonic counter paper_events keys on.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. The inline CONSTRAINT clauses are
-- the design's; section 3b re-asserts them under guards.
CREATE TABLE IF NOT EXISTS public.paper_sessions (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    listing_id             UUID REFERENCES public.library_strategies(id) ON DELETE SET NULL,
    source_strategy_id     UUID NOT NULL,
    version_id             UUID NOT NULL,

    environment            TEXT NOT NULL DEFAULT 'PAPER',
    session_state          TEXT NOT NULL DEFAULT 'CREATED',
    exchange_id            TEXT NOT NULL,
    symbol                 TEXT NOT NULL,
    timeframe              TEXT NOT NULL,
    initial_capital_minor  BIGINT NOT NULL,
    currency               TEXT NOT NULL,
    config                 JSONB NOT NULL,
    market_data_source     TEXT NOT NULL,
    feed_state             TEXT NOT NULL DEFAULT 'PENDING',
    feed_transport         TEXT,
    event_sequence         BIGINT NOT NULL DEFAULT 0,

    started_at             TIMESTAMPTZ,
    paused_at              TIMESTAMPTZ,
    stopped_at             TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_paper_session_environment CHECK (environment = 'PAPER'),
    CONSTRAINT chk_paper_session_state CHECK (session_state IN (
        'CREATED', 'RUNNING', 'PAUSED', 'STOPPED')),
    CONSTRAINT chk_paper_capital CHECK (initial_capital_minor > 0)
);

-- 3a) Shape assertion --------------------------------------------------
-- The silences that matter: a non-uuid id, user_id or listing_id makes the
-- child cascades and the RLS predicate equality over truncated text; a text
-- initial_capital_minor makes chk_paper_capital a cast and sorts lexically; a
-- JSON (not JSONB) config cannot be compared by trg_paper_session_config_
-- immutable and preserves duplicate keys; a timestamp WITHOUT time zone
-- silently reinterprets every UTC instant as local time, which Requirement
-- 24.5 reads as evidence - that type is deliberately NOT accepted.
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
                ('id',                    ARRAY['uuid']),
                ('user_id',               ARRAY['uuid']),
                ('listing_id',            ARRAY['uuid']),
                ('source_strategy_id',    ARRAY['uuid']),
                ('version_id',            ARRAY['uuid']),
                ('environment',           ARRAY['text', 'character varying']),
                ('session_state',         ARRAY['text', 'character varying']),
                ('exchange_id',           ARRAY['text', 'character varying']),
                ('symbol',                ARRAY['text', 'character varying']),
                ('timeframe',             ARRAY['text', 'character varying']),
                ('initial_capital_minor', ARRAY['bigint', 'integer']),
                ('currency',              ARRAY['text', 'character varying']),
                ('config',                ARRAY['jsonb']),
                ('market_data_source',    ARRAY['text', 'character varying']),
                ('feed_state',            ARRAY['text', 'character varying']),
                ('feed_transport',        ARRAY['text', 'character varying']),
                ('event_sequence',        ARRAY['bigint', 'integer']),
                ('started_at',            ARRAY['timestamp with time zone']),
                ('paused_at',             ARRAY['timestamp with time zone']),
                ('stopped_at',            ARRAY['timestamp with time zone']),
                ('created_at',            ARRAY['timestamp with time zone']),
                ('updated_at',            ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'paper_sessions'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.paper_sessions has column(s) of the wrong shape: %. A '
            'pre-existing table of that name was left as it was, because '
            'CREATE TABLE IF NOT EXISTS does not alter one, so "the table '
            'exists" would not have meant "the table is usable" (Requirement '
            '17.1). Reconcile it by hand before re-running this migration.',
            problems;
    END IF;
END $$;

-- 3b) Constraint guards ------------------------------------------------
-- No-ops on a fresh run: all three were declared inline above. They exist so
-- a paper_sessions created earlier WITHOUT them gains them.
-- Idempotent: each ADD CONSTRAINT runs only when pg_constraint holds no
-- constraint of that name on this table.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_sessions'::regclass
           AND conname  = 'chk_paper_session_environment'
    ) THEN
        ALTER TABLE public.paper_sessions
            ADD CONSTRAINT chk_paper_session_environment CHECK (environment = 'PAPER');
        RAISE NOTICE 'Added chk_paper_session_environment to '
                     'public.paper_sessions (a paper session cannot be '
                     'relabelled).';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_sessions'::regclass
           AND conname  = 'chk_paper_session_state'
    ) THEN
        ALTER TABLE public.paper_sessions
            ADD CONSTRAINT chk_paper_session_state CHECK (session_state IN (
                'CREATED', 'RUNNING', 'PAUSED', 'STOPPED'));
        RAISE NOTICE 'Added chk_paper_session_state to public.paper_sessions '
                     '(the 4 values of Requirement 17.7).';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_sessions'::regclass
           AND conname  = 'chk_paper_capital'
    ) THEN
        ALTER TABLE public.paper_sessions
            ADD CONSTRAINT chk_paper_capital CHECK (initial_capital_minor > 0);
        RAISE NOTICE 'Added chk_paper_capital to public.paper_sessions '
                     '(initial_capital_minor > 0).';
    END IF;
END $$;

-- 3c) Indexes ----------------------------------------------------------
-- design.md § Data Models -> "paper_sessions", "Indexes:".
-- idx_paper_sessions_user is the owner's own list, newest first, and the
-- column the RLS owner policy filters on.
CREATE INDEX IF NOT EXISTS idx_paper_sessions_user
    ON public.paper_sessions (user_id, created_at DESC);

-- idx_paper_sessions_running supports the per-user concurrency cap in one
-- index scan (Requirements 24.4, 27.4): "how many RUNNING sessions does this
-- user have" is answered without scanning their whole session history. PARTIAL
-- on session_state = 'RUNNING' so the index holds only the rows the cap counts.
CREATE INDEX IF NOT EXISTS idx_paper_sessions_running
    ON public.paper_sessions (user_id)
    WHERE session_state = 'RUNNING';

-- ==========================================================================
-- SECTION 4 - paper_accounts
-- ==========================================================================
-- design.md § Data Models -> "paper_accounts". The simulated balances.
--
--   session_id  NULLABLE and ON DELETE CASCADE. NULL is the DEFAULT ACCOUNT -
--               the one GET /api/paper/account, /positions, /orders, /trades,
--               /summary serve, so risk.py, Portfolio.jsx and TradeHistory.jsx
--               see the same fields at the same paths (design.md § "The
--               default account"). A per-session account carries the session's
--               id and is removed when the session is deleted.
--   version     the optimistic-concurrency counter: place_order and apply_fill
--               SELECT ... FOR UPDATE and bump it, so a lost update surfaces as
--               a conflict rather than a silently overwritten balance
--               (Requirement 16.10).
--
-- Every balance, capital, pnl and equity column is NUMERIC(28,10), never
-- double precision - see the header. Read and written as strings.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. Section 4b re-asserts the check
-- under a guard; the two partial uniques are indexes, created in 4c.
CREATE TABLE IF NOT EXISTS public.paper_accounts (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID NOT NULL,
    session_id         UUID REFERENCES public.paper_sessions(id) ON DELETE CASCADE,
    currency           TEXT NOT NULL,
    initial_capital    NUMERIC(28, 10) NOT NULL,
    available_balance  NUMERIC(28, 10) NOT NULL,
    locked_balance     NUMERIC(28, 10) NOT NULL,
    realized_pnl       NUMERIC(28, 10) NOT NULL DEFAULT 0,
    total_equity       NUMERIC(28, 10) NOT NULL,
    version            BIGINT NOT NULL DEFAULT 1,
    last_price_at      TIMESTAMPTZ,
    stale              BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_paper_balances_non_negative CHECK (
        available_balance >= 0 AND locked_balance >= 0)
);

-- 4a) Shape assertion --------------------------------------------------
-- A double precision balance reintroduces binary floating-point error into
-- the equity identity total_equity = available + locked + position_market_value
-- (Requirement 18.14), so 'double precision' is deliberately refused for every
-- NUMERIC column here. A non-uuid session_id makes the ON DELETE CASCADE and
-- uq_paper_account_session equality over truncated text.
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
                ('id',                ARRAY['uuid']),
                ('user_id',           ARRAY['uuid']),
                ('session_id',        ARRAY['uuid']),
                ('currency',          ARRAY['text', 'character varying']),
                ('initial_capital',   ARRAY['numeric']),
                ('available_balance', ARRAY['numeric']),
                ('locked_balance',    ARRAY['numeric']),
                ('realized_pnl',      ARRAY['numeric']),
                ('total_equity',      ARRAY['numeric']),
                ('version',           ARRAY['bigint', 'integer']),
                ('last_price_at',     ARRAY['timestamp with time zone']),
                ('stale',             ARRAY['boolean']),
                ('created_at',        ARRAY['timestamp with time zone']),
                ('updated_at',        ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'paper_accounts'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.paper_accounts has column(s) of the wrong shape: %. Every '
            'money column must be NUMERIC (never double precision) so the '
            'equity identity of Requirement 18.14 holds exactly. Reconcile it '
            'by hand before re-running this migration.', problems;
    END IF;
END $$;

-- 4b) Constraint guard -------------------------------------------------
-- No-op on a fresh run: declared inline above. Requirement 18.4 / Property
-- P-26: a simulated balance can never go negative.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_accounts'::regclass
           AND conname  = 'chk_paper_balances_non_negative'
    ) THEN
        ALTER TABLE public.paper_accounts
            ADD CONSTRAINT chk_paper_balances_non_negative CHECK (
                available_balance >= 0 AND locked_balance >= 0);
        RAISE NOTICE 'Added chk_paper_balances_non_negative to '
                     'public.paper_accounts (Requirement 18.4, P-26).';
    END IF;
END $$;

-- 4c) Indexes / partial uniques ----------------------------------------
-- design.md § Data Models -> "paper_accounts". Two PARTIAL unique indexes:
--
-- uq_paper_account_default enforces one default account per (user, currency).
-- PARTIAL on session_id IS NULL because the uniqueness applies ONLY to the
-- default account - a user may hold many per-session accounts in the same
-- currency, one per session, but exactly one default. A plain UNIQUE
-- (user_id, currency) would forbid the per-session accounts.
CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_account_default
    ON public.paper_accounts (user_id, currency)
    WHERE session_id IS NULL;

-- uq_paper_account_session enforces one account per (session, currency), so a
-- session's isolated balance set (Requirement 17.6) has a single row per
-- currency. Not partial: every per-session account has a non-null session_id,
-- and the default account (session_id NULL) is excluded by the UNIQUE's own
-- NULL semantics from colliding here.
CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_account_session
    ON public.paper_accounts (session_id, currency);

-- The RLS owner column, for the policy-scoped read and the default-account
-- lookup by user.
CREATE INDEX IF NOT EXISTS idx_paper_accounts_user
    ON public.paper_accounts (user_id);

-- ==========================================================================
-- SECTION 5 - paper_orders
-- ==========================================================================
-- design.md § Data Models -> "paper_orders". The order book.
--
--   order_state    the six Paper_Order_State values (Requirement 16.1),
--                  enforced by chk_paper_order_state and, for transitions, by
--                  trg_paper_order_transition_guard against
--                  paper_order_allowed_transitions.
--   legacy_status  NOT NULL: the retained PaperOrderStatus spelling from
--                  LEGACY_STATUS_FOR_STATE in paper_order_state.py, so
--                  GET /api/paper/orders?status=OPEN keeps its exact meaning
--                  (Requirement 17.12).
--   idempotency_key + uq_paper_order_idem: Requirement 16.11's dedupe,
--                  enforced by the database as well as by the in-memory cache
--                  it replaces.
--   filled_quantity bounded by chk_paper_order_fill_bound to [0, quantity]
--                  (Requirement 16.7, P-20): an order can never be over-filled.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. The inline CONSTRAINT clauses are
-- the design's; section 5b re-asserts the named ones under guards.
CREATE TABLE IF NOT EXISTS public.paper_orders (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id       UUID NOT NULL REFERENCES public.paper_sessions(id) ON DELETE CASCADE,
    account_id       UUID NOT NULL REFERENCES public.paper_accounts(id) ON DELETE CASCADE,
    user_id          UUID NOT NULL,
    symbol           TEXT NOT NULL,
    side             TEXT NOT NULL,
    order_type       TEXT NOT NULL,
    quantity         NUMERIC(28, 10) NOT NULL,
    limit_price      NUMERIC(28, 10),
    reference_price  NUMERIC(28, 10),
    filled_quantity  NUMERIC(28, 10) NOT NULL DEFAULT 0,
    avg_fill_price   NUMERIC(28, 10),
    fee_minor        BIGINT NOT NULL DEFAULT 0,
    slippage_minor   BIGINT NOT NULL DEFAULT 0,
    order_state      TEXT NOT NULL DEFAULT 'CREATED',
    legacy_status    TEXT NOT NULL,
    rejection_reason TEXT,
    idempotency_key  TEXT,
    signal_id        UUID,
    fingerprint      TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_paper_order_side CHECK (side IN ('buy', 'sell')),
    CONSTRAINT chk_paper_order_type CHECK (order_type IN ('market', 'limit')),
    CONSTRAINT chk_paper_order_quantity CHECK (quantity > 0),
    CONSTRAINT chk_paper_order_limit_price CHECK (limit_price IS NULL OR limit_price > 0),
    CONSTRAINT chk_paper_order_state CHECK (order_state IN (
        'CREATED', 'ACCEPTED', 'PARTIALLY_FILLED', 'FILLED', 'CANCELLED', 'REJECTED')),
    CONSTRAINT chk_paper_order_fill_bound CHECK (
        filled_quantity >= 0 AND filled_quantity <= quantity),
    CONSTRAINT chk_paper_order_idem_len CHECK (
        idempotency_key IS NULL OR (length(idempotency_key) BETWEEN 1 AND 128)),
    CONSTRAINT uq_paper_order_idem UNIQUE (session_id, idempotency_key)
);

-- 5a) Shape assertion --------------------------------------------------
-- A double precision quantity or price is refused (the fill bound and the
-- accounting arithmetic must be exact). A text filled_quantity would make
-- chk_paper_order_fill_bound a cast. A non-uuid session_id/account_id makes
-- the cascades and uq_paper_order_idem equality over truncated text.
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
                ('id',               ARRAY['uuid']),
                ('session_id',       ARRAY['uuid']),
                ('account_id',       ARRAY['uuid']),
                ('user_id',          ARRAY['uuid']),
                ('symbol',           ARRAY['text', 'character varying']),
                ('side',             ARRAY['text', 'character varying']),
                ('order_type',       ARRAY['text', 'character varying']),
                ('quantity',         ARRAY['numeric']),
                ('limit_price',      ARRAY['numeric']),
                ('reference_price',  ARRAY['numeric']),
                ('filled_quantity',  ARRAY['numeric']),
                ('avg_fill_price',   ARRAY['numeric']),
                ('fee_minor',        ARRAY['bigint', 'integer']),
                ('slippage_minor',   ARRAY['bigint', 'integer']),
                ('order_state',      ARRAY['text', 'character varying']),
                ('legacy_status',    ARRAY['text', 'character varying']),
                ('rejection_reason', ARRAY['text', 'character varying']),
                ('idempotency_key',  ARRAY['text', 'character varying']),
                ('signal_id',        ARRAY['uuid']),
                ('fingerprint',      ARRAY['text', 'character varying']),
                ('created_at',       ARRAY['timestamp with time zone']),
                ('updated_at',       ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'paper_orders'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.paper_orders has column(s) of the wrong shape: %. '
            'Reconcile it by hand before re-running this migration.', problems;
    END IF;
END $$;

-- 5b) Constraint guards ------------------------------------------------
-- No-ops on a fresh run: all declared inline above.
-- Idempotent: each ADD CONSTRAINT / partial guard is behind a pg_constraint
-- name check.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_orders'::regclass
           AND conname  = 'chk_paper_order_side') THEN
        ALTER TABLE public.paper_orders
            ADD CONSTRAINT chk_paper_order_side CHECK (side IN ('buy', 'sell'));
        RAISE NOTICE 'Added chk_paper_order_side.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_orders'::regclass
           AND conname  = 'chk_paper_order_type') THEN
        ALTER TABLE public.paper_orders
            ADD CONSTRAINT chk_paper_order_type CHECK (order_type IN ('market', 'limit'));
        RAISE NOTICE 'Added chk_paper_order_type.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_orders'::regclass
           AND conname  = 'chk_paper_order_quantity') THEN
        ALTER TABLE public.paper_orders
            ADD CONSTRAINT chk_paper_order_quantity CHECK (quantity > 0);
        RAISE NOTICE 'Added chk_paper_order_quantity.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_orders'::regclass
           AND conname  = 'chk_paper_order_limit_price') THEN
        ALTER TABLE public.paper_orders
            ADD CONSTRAINT chk_paper_order_limit_price CHECK (
                limit_price IS NULL OR limit_price > 0);
        RAISE NOTICE 'Added chk_paper_order_limit_price.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_orders'::regclass
           AND conname  = 'chk_paper_order_state') THEN
        ALTER TABLE public.paper_orders
            ADD CONSTRAINT chk_paper_order_state CHECK (order_state IN (
                'CREATED', 'ACCEPTED', 'PARTIALLY_FILLED', 'FILLED',
                'CANCELLED', 'REJECTED'));
        RAISE NOTICE 'Added chk_paper_order_state (the 6 values of Requirement '
                     '16.1).';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_orders'::regclass
           AND conname  = 'chk_paper_order_fill_bound') THEN
        ALTER TABLE public.paper_orders
            ADD CONSTRAINT chk_paper_order_fill_bound CHECK (
                filled_quantity >= 0 AND filled_quantity <= quantity);
        RAISE NOTICE 'Added chk_paper_order_fill_bound (Requirement 16.7, '
                     'P-20).';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_orders'::regclass
           AND conname  = 'chk_paper_order_idem_len') THEN
        ALTER TABLE public.paper_orders
            ADD CONSTRAINT chk_paper_order_idem_len CHECK (
                idempotency_key IS NULL OR (length(idempotency_key) BETWEEN 1 AND 128));
        RAISE NOTICE 'Added chk_paper_order_idem_len (Requirement 16.8).';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_orders'::regclass
           AND conname  = 'uq_paper_order_idem') THEN
        ALTER TABLE public.paper_orders
            ADD CONSTRAINT uq_paper_order_idem UNIQUE (session_id, idempotency_key);
        RAISE NOTICE 'Added uq_paper_order_idem (Requirement 16.11).';
    END IF;
END $$;

-- 5c) Indexes ----------------------------------------------------------
-- design.md § Data Models: idx_paper_orders_session_state ON (session_id,
-- order_state, created_at DESC) - GET /api/paper/orders filtered by state,
-- newest first, in one index scan (Requirement 24.4). Also serves the ON
-- DELETE CASCADE from paper_sessions.
CREATE INDEX IF NOT EXISTS idx_paper_orders_session_state
    ON public.paper_orders (session_id, order_state, created_at DESC);

-- The RLS owner column, and the account cascade's referencing column.
CREATE INDEX IF NOT EXISTS idx_paper_orders_user
    ON public.paper_orders (user_id);
CREATE INDEX IF NOT EXISTS idx_paper_orders_account
    ON public.paper_orders (account_id);

-- ==========================================================================
-- SECTION 6 - paper_fills
-- ==========================================================================
-- design.md § Data Models -> "paper_fills". Every fill, append-only.
-- uq_paper_fill_event UNIQUE (order_id, fill_event_id) is Requirement 16.11 /
-- P-21's dedupe: a duplicate fill event is a no-op, enforced by the database.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. The inline uq/checks are the
-- design's; section 6b re-asserts the named unique under a guard.
CREATE TABLE IF NOT EXISTS public.paper_fills (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID NOT NULL REFERENCES public.paper_orders(id) ON DELETE CASCADE,
    session_id      UUID NOT NULL REFERENCES public.paper_sessions(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL,
    fill_event_id   TEXT NOT NULL,
    quantity        NUMERIC(28, 10) NOT NULL,
    price           NUMERIC(28, 10) NOT NULL,
    fee_minor       BIGINT NOT NULL,
    slippage_minor  BIGINT NOT NULL,
    market_event_id TEXT,
    filled_at       TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_paper_fill_quantity CHECK (quantity > 0),
    CONSTRAINT chk_paper_fill_price CHECK (price > 0),
    CONSTRAINT uq_paper_fill_event UNIQUE (order_id, fill_event_id)
);

-- 6a) Shape assertion --------------------------------------------------
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
                ('id',              ARRAY['uuid']),
                ('order_id',        ARRAY['uuid']),
                ('session_id',      ARRAY['uuid']),
                ('user_id',         ARRAY['uuid']),
                ('fill_event_id',   ARRAY['text', 'character varying']),
                ('quantity',        ARRAY['numeric']),
                ('price',           ARRAY['numeric']),
                ('fee_minor',       ARRAY['bigint', 'integer']),
                ('slippage_minor',  ARRAY['bigint', 'integer']),
                ('market_event_id', ARRAY['text', 'character varying']),
                ('filled_at',       ARRAY['timestamp with time zone']),
                ('created_at',      ARRAY['timestamp with time zone']),
                ('updated_at',      ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'paper_fills'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.paper_fills has column(s) of the wrong shape: %. '
            'Reconcile it by hand before re-running this migration.', problems;
    END IF;
END $$;

-- 6b) Constraint guards ------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_fills'::regclass
           AND conname  = 'chk_paper_fill_quantity') THEN
        ALTER TABLE public.paper_fills
            ADD CONSTRAINT chk_paper_fill_quantity CHECK (quantity > 0);
        RAISE NOTICE 'Added chk_paper_fill_quantity.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_fills'::regclass
           AND conname  = 'chk_paper_fill_price') THEN
        ALTER TABLE public.paper_fills
            ADD CONSTRAINT chk_paper_fill_price CHECK (price > 0);
        RAISE NOTICE 'Added chk_paper_fill_price.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_fills'::regclass
           AND conname  = 'uq_paper_fill_event') THEN
        ALTER TABLE public.paper_fills
            ADD CONSTRAINT uq_paper_fill_event UNIQUE (order_id, fill_event_id);
        RAISE NOTICE 'Added uq_paper_fill_event (Requirement 16.11, P-21).';
    END IF;
END $$;

-- 6c) Indexes ----------------------------------------------------------
-- The order's own fills, and the two cascade referencing columns.
CREATE INDEX IF NOT EXISTS idx_paper_fills_order
    ON public.paper_fills (order_id, filled_at ASC);
CREATE INDEX IF NOT EXISTS idx_paper_fills_session
    ON public.paper_fills (session_id);
CREATE INDEX IF NOT EXISTS idx_paper_fills_user
    ON public.paper_fills (user_id);

-- ==========================================================================
-- SECTION 7 - paper_positions
-- ==========================================================================
-- design.md § Data Models -> "paper_positions". Open and closed positions.
-- uq_paper_position_open UNIQUE (account_id, symbol) WHERE closed_at IS NULL:
-- an account holds at most one OPEN position per symbol at a time. PARTIAL so
-- a symbol can be opened, closed and reopened - the closed rows are history
-- and do not block a new open. size >= 0 is Requirement 18.5 / P-26.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. Section 7b re-asserts the check.
CREATE TABLE IF NOT EXISTS public.paper_positions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES public.paper_sessions(id) ON DELETE CASCADE,
    account_id      UUID NOT NULL REFERENCES public.paper_accounts(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    size            NUMERIC(28, 10) NOT NULL,
    entry_price     NUMERIC(28, 10) NOT NULL,
    current_price   NUMERIC(28, 10),
    unrealized_pnl  NUMERIC(28, 10),
    price_at        TIMESTAMPTZ,
    opened_at       TIMESTAMPTZ NOT NULL,
    closed_at       TIMESTAMPTZ,
    version         BIGINT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_paper_position_side CHECK (side IN ('LONG', 'SHORT')),
    CONSTRAINT chk_paper_position_size CHECK (size >= 0)
);

-- 7a) Shape assertion --------------------------------------------------
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
                ('id',             ARRAY['uuid']),
                ('session_id',     ARRAY['uuid']),
                ('account_id',     ARRAY['uuid']),
                ('user_id',        ARRAY['uuid']),
                ('symbol',         ARRAY['text', 'character varying']),
                ('side',           ARRAY['text', 'character varying']),
                ('size',           ARRAY['numeric']),
                ('entry_price',    ARRAY['numeric']),
                ('current_price',  ARRAY['numeric']),
                ('unrealized_pnl', ARRAY['numeric']),
                ('price_at',       ARRAY['timestamp with time zone']),
                ('opened_at',      ARRAY['timestamp with time zone']),
                ('closed_at',      ARRAY['timestamp with time zone']),
                ('version',        ARRAY['bigint', 'integer']),
                ('created_at',     ARRAY['timestamp with time zone']),
                ('updated_at',     ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'paper_positions'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.paper_positions has column(s) of the wrong shape: %. '
            'Reconcile it by hand before re-running this migration.', problems;
    END IF;
END $$;

-- 7b) Constraint guards ------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_positions'::regclass
           AND conname  = 'chk_paper_position_side') THEN
        ALTER TABLE public.paper_positions
            ADD CONSTRAINT chk_paper_position_side CHECK (side IN ('LONG', 'SHORT'));
        RAISE NOTICE 'Added chk_paper_position_side.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_positions'::regclass
           AND conname  = 'chk_paper_position_size') THEN
        ALTER TABLE public.paper_positions
            ADD CONSTRAINT chk_paper_position_size CHECK (size >= 0);
        RAISE NOTICE 'Added chk_paper_position_size (Requirement 18.5, P-26).';
    END IF;
END $$;

-- 7c) Indexes / partial unique -----------------------------------------
-- design.md § Data Models: uq_paper_position_open UNIQUE (account_id, symbol)
-- WHERE closed_at IS NULL. One open position per account per symbol.
CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_position_open
    ON public.paper_positions (account_id, symbol)
    WHERE closed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_paper_positions_session
    ON public.paper_positions (session_id);
CREATE INDEX IF NOT EXISTS idx_paper_positions_user
    ON public.paper_positions (user_id);

-- ==========================================================================
-- SECTION 8 - paper_balance_events
-- ==========================================================================
-- design.md § Data Models -> "paper_balance_events". Requirement 26.3's
-- per-balance-change record, append-only. cause is one of ORDER_LOCK,
-- ORDER_UNLOCK, FILL, FEE, RESET; the *_delta columns are the change and the
-- *_after columns the resulting balances, so the ledger can be replayed.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. Section 8b re-asserts the check.
CREATE TABLE IF NOT EXISTS public.paper_balance_events (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id       UUID NOT NULL REFERENCES public.paper_sessions(id) ON DELETE CASCADE,
    account_id       UUID NOT NULL REFERENCES public.paper_accounts(id) ON DELETE CASCADE,
    user_id          UUID NOT NULL,
    cause            TEXT NOT NULL,
    available_delta  NUMERIC(28, 10) NOT NULL,
    locked_delta     NUMERIC(28, 10) NOT NULL,
    realized_delta   NUMERIC(28, 10) NOT NULL,
    available_after  NUMERIC(28, 10) NOT NULL,
    locked_after     NUMERIC(28, 10) NOT NULL,
    realized_after   NUMERIC(28, 10) NOT NULL,
    fill_id          UUID,
    occurred_at      TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_paper_balance_event_cause CHECK (cause IN (
        'ORDER_LOCK', 'ORDER_UNLOCK', 'FILL', 'FEE', 'RESET'))
);

-- 8a) Shape assertion --------------------------------------------------
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
                ('id',              ARRAY['uuid']),
                ('session_id',      ARRAY['uuid']),
                ('account_id',      ARRAY['uuid']),
                ('user_id',         ARRAY['uuid']),
                ('cause',           ARRAY['text', 'character varying']),
                ('available_delta', ARRAY['numeric']),
                ('locked_delta',    ARRAY['numeric']),
                ('realized_delta',  ARRAY['numeric']),
                ('available_after', ARRAY['numeric']),
                ('locked_after',    ARRAY['numeric']),
                ('realized_after',  ARRAY['numeric']),
                ('fill_id',         ARRAY['uuid']),
                ('occurred_at',     ARRAY['timestamp with time zone']),
                ('created_at',      ARRAY['timestamp with time zone']),
                ('updated_at',      ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'paper_balance_events'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.paper_balance_events has column(s) of the wrong shape: %. '
            'Reconcile it by hand before re-running this migration.', problems;
    END IF;
END $$;

-- 8b) Constraint guard -------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_balance_events'::regclass
           AND conname  = 'chk_paper_balance_event_cause') THEN
        ALTER TABLE public.paper_balance_events
            ADD CONSTRAINT chk_paper_balance_event_cause CHECK (cause IN (
                'ORDER_LOCK', 'ORDER_UNLOCK', 'FILL', 'FEE', 'RESET'));
        RAISE NOTICE 'Added chk_paper_balance_event_cause.';
    END IF;
END $$;

-- 8c) Index ------------------------------------------------------------
-- design.md § Data Models: idx_paper_balance_events ON (session_id,
-- occurred_at ASC) - the ledger in occurrence order, and the cascade column.
CREATE INDEX IF NOT EXISTS idx_paper_balance_events
    ON public.paper_balance_events (session_id, occurred_at ASC);
CREATE INDEX IF NOT EXISTS idx_paper_balance_events_user
    ON public.paper_balance_events (user_id);

-- ==========================================================================
-- SECTION 9 - paper_trades
-- ==========================================================================
-- design.md § Data Models -> "paper_trades". A closed round-trip, append-only:
-- a row exists only when a position reached size zero (Requirement 18.10).
-- realized_pnl NUMERIC(28,10) NOT NULL - the trade's profit or loss.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS.
CREATE TABLE IF NOT EXISTS public.paper_trades (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    UUID NOT NULL REFERENCES public.paper_sessions(id) ON DELETE CASCADE,
    account_id    UUID NOT NULL REFERENCES public.paper_accounts(id) ON DELETE CASCADE,
    user_id       UUID NOT NULL,
    symbol        TEXT NOT NULL,
    side          TEXT NOT NULL,
    quantity      NUMERIC(28, 10) NOT NULL,
    entry_price   NUMERIC(28, 10) NOT NULL,
    exit_price    NUMERIC(28, 10) NOT NULL,
    realized_pnl  NUMERIC(28, 10) NOT NULL,
    fee_minor     BIGINT NOT NULL,
    opened_at     TIMESTAMPTZ NOT NULL,
    closed_at     TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 9a) Shape assertion --------------------------------------------------
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
                ('id',           ARRAY['uuid']),
                ('session_id',   ARRAY['uuid']),
                ('account_id',   ARRAY['uuid']),
                ('user_id',      ARRAY['uuid']),
                ('symbol',       ARRAY['text', 'character varying']),
                ('side',         ARRAY['text', 'character varying']),
                ('quantity',     ARRAY['numeric']),
                ('entry_price',  ARRAY['numeric']),
                ('exit_price',   ARRAY['numeric']),
                ('realized_pnl', ARRAY['numeric']),
                ('fee_minor',    ARRAY['bigint', 'integer']),
                ('opened_at',    ARRAY['timestamp with time zone']),
                ('closed_at',    ARRAY['timestamp with time zone']),
                ('created_at',   ARRAY['timestamp with time zone']),
                ('updated_at',   ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'paper_trades'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.paper_trades has column(s) of the wrong shape: %. '
            'Reconcile it by hand before re-running this migration.', problems;
    END IF;
END $$;

-- 9b) Index ------------------------------------------------------------
-- design.md § Data Models: idx_paper_trades ON (session_id, closed_at DESC) -
-- the trade history newest first, and the cascade column.
CREATE INDEX IF NOT EXISTS idx_paper_trades
    ON public.paper_trades (session_id, closed_at DESC);
CREATE INDEX IF NOT EXISTS idx_paper_trades_user
    ON public.paper_trades (user_id);

-- ==========================================================================
-- SECTION 10 - paper_equity_snapshots
-- ==========================================================================
-- design.md § Data Models -> "paper_equity_snapshots". The equity curve,
-- append-only. cause is one of SESSION_START, FILL, FEE, REVALUATION,
-- SESSION_STOP. series_index + taken_at is the non-decreasing order
-- Requirement 18.9 computes drawdown from.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. Section 10b re-asserts the check.
CREATE TABLE IF NOT EXISTS public.paper_equity_snapshots (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id            UUID NOT NULL REFERENCES public.paper_sessions(id) ON DELETE CASCADE,
    user_id               UUID NOT NULL,
    series_index          INTEGER NOT NULL DEFAULT 0,
    total_equity          NUMERIC(28, 10) NOT NULL,
    available_balance     NUMERIC(28, 10) NOT NULL,
    locked_balance        NUMERIC(28, 10) NOT NULL,
    position_market_value NUMERIC(28, 10) NOT NULL,
    stale                 BOOLEAN NOT NULL DEFAULT FALSE,
    cause                 TEXT NOT NULL,
    taken_at              TIMESTAMPTZ NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_paper_equity_cause CHECK (cause IN (
        'SESSION_START', 'FILL', 'FEE', 'REVALUATION', 'SESSION_STOP'))
);

-- 10a) Shape assertion -------------------------------------------------
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
                ('id',                    ARRAY['uuid']),
                ('session_id',            ARRAY['uuid']),
                ('user_id',               ARRAY['uuid']),
                ('series_index',          ARRAY['integer', 'bigint', 'smallint']),
                ('total_equity',          ARRAY['numeric']),
                ('available_balance',     ARRAY['numeric']),
                ('locked_balance',        ARRAY['numeric']),
                ('position_market_value', ARRAY['numeric']),
                ('stale',                 ARRAY['boolean']),
                ('cause',                 ARRAY['text', 'character varying']),
                ('taken_at',              ARRAY['timestamp with time zone']),
                ('created_at',            ARRAY['timestamp with time zone']),
                ('updated_at',            ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'paper_equity_snapshots'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.paper_equity_snapshots has column(s) of the wrong shape: '
            '%. Reconcile it by hand before re-running this migration.',
            problems;
    END IF;
END $$;

-- 10b) Constraint guard ------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_equity_snapshots'::regclass
           AND conname  = 'chk_paper_equity_cause') THEN
        ALTER TABLE public.paper_equity_snapshots
            ADD CONSTRAINT chk_paper_equity_cause CHECK (cause IN (
                'SESSION_START', 'FILL', 'FEE', 'REVALUATION', 'SESSION_STOP'));
        RAISE NOTICE 'Added chk_paper_equity_cause.';
    END IF;
END $$;

-- 10c) Index -----------------------------------------------------------
-- design.md § Data Models: idx_paper_equity ON (session_id, series_index,
-- taken_at ASC) - the non-decreasing order drawdown is computed from.
CREATE INDEX IF NOT EXISTS idx_paper_equity
    ON public.paper_equity_snapshots (session_id, series_index, taken_at ASC);
CREATE INDEX IF NOT EXISTS idx_paper_equity_user
    ON public.paper_equity_snapshots (user_id);

-- ==========================================================================
-- SECTION 11 - paper_metrics
-- ==========================================================================
-- design.md § Data Models -> "paper_metrics". The computed session metrics.
--
--   win_rate NUMERIC(6,5) is NULLABLE, and chk_paper_win_rate admits NULL:
--     an empty closed-trade set has NO win rate, which is ABSENT, not zero
--     (Requirement 18.10, P-30). A zero would be a losing session's rate, not
--     an unmeasured one.
--   max_drawdown_fraction is NULLABLE with chk_paper_drawdown_fraction in
--     [0, 1] (P-29): absent before any equity is recorded, a fraction after.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. Section 11b re-asserts both checks.
CREATE TABLE IF NOT EXISTS public.paper_metrics (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id             UUID NOT NULL REFERENCES public.paper_sessions(id) ON DELETE CASCADE,
    user_id                UUID NOT NULL,
    total_return_pct       NUMERIC(28, 10),
    realized_pnl           NUMERIC(28, 10),
    unrealized_pnl         NUMERIC(28, 10),
    max_drawdown_amount    NUMERIC(28, 10),
    max_drawdown_fraction  NUMERIC(28, 10),
    win_rate               NUMERIC(6, 5),
    closed_trade_count     INTEGER,
    order_count            INTEGER,
    fill_count             INTEGER,
    computed_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_paper_win_rate CHECK (
        win_rate IS NULL OR (win_rate >= 0 AND win_rate <= 1)),
    CONSTRAINT chk_paper_drawdown_fraction CHECK (
        max_drawdown_fraction IS NULL
        OR (max_drawdown_fraction >= 0 AND max_drawdown_fraction <= 1))
);

-- 11a) Shape assertion -------------------------------------------------
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
                ('id',                    ARRAY['uuid']),
                ('session_id',            ARRAY['uuid']),
                ('user_id',               ARRAY['uuid']),
                ('total_return_pct',      ARRAY['numeric']),
                ('realized_pnl',          ARRAY['numeric']),
                ('unrealized_pnl',        ARRAY['numeric']),
                ('max_drawdown_amount',   ARRAY['numeric']),
                ('max_drawdown_fraction', ARRAY['numeric']),
                ('win_rate',              ARRAY['numeric']),
                ('closed_trade_count',    ARRAY['integer', 'bigint', 'smallint']),
                ('order_count',           ARRAY['integer', 'bigint', 'smallint']),
                ('fill_count',            ARRAY['integer', 'bigint', 'smallint']),
                ('computed_at',           ARRAY['timestamp with time zone']),
                ('created_at',            ARRAY['timestamp with time zone']),
                ('updated_at',            ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'paper_metrics'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.paper_metrics has column(s) of the wrong shape: %. '
            'win_rate and max_drawdown_fraction must be NUMERIC and NULLABLE '
            'so an empty closed-trade set is ABSENT, not zero (Requirement '
            '18.10, P-30). Reconcile it by hand before re-running.', problems;
    END IF;
END $$;

-- 11b) Constraint guards -----------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_metrics'::regclass
           AND conname  = 'chk_paper_win_rate') THEN
        ALTER TABLE public.paper_metrics
            ADD CONSTRAINT chk_paper_win_rate CHECK (
                win_rate IS NULL OR (win_rate >= 0 AND win_rate <= 1));
        RAISE NOTICE 'Added chk_paper_win_rate (nullable; Requirement 18.10, '
                     'P-30).';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_metrics'::regclass
           AND conname  = 'chk_paper_drawdown_fraction') THEN
        ALTER TABLE public.paper_metrics
            ADD CONSTRAINT chk_paper_drawdown_fraction CHECK (
                max_drawdown_fraction IS NULL
                OR (max_drawdown_fraction >= 0 AND max_drawdown_fraction <= 1));
        RAISE NOTICE 'Added chk_paper_drawdown_fraction (nullable, [0,1]; '
                     'P-29).';
    END IF;
END $$;

-- 11c) Index -----------------------------------------------------------
-- The session's metrics, and the cascade / RLS columns.
CREATE INDEX IF NOT EXISTS idx_paper_metrics_session
    ON public.paper_metrics (session_id, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_paper_metrics_user
    ON public.paper_metrics (user_id);

-- ==========================================================================
-- SECTION 12 - paper_events
-- ==========================================================================
-- design.md § Data Models -> "paper_events". The append-only Paper_Channel
-- log, one row per emitted event.
--
--   chk_paper_event_type enumerates the SIXTEEN event types of task 26.1 /
--     design.md, and no seventeenth. These MUST equal the type set
--     backend_app/backend/paper/paper_events.py (task 26.1) emits.
--   sequence + uq_paper_event_seq: the per-session contiguous-from-1 counter
--     of Requirement 19.3 / P-53. chk_paper_event_sequence forbids a sequence
--     below 1. uq_paper_event_seq UNIQUE (session_id, sequence) makes a
--     duplicate sequence unrepresentable.
--   event_id + uq_paper_event_id: each event's stable id, unique per session
--     (Requirement 19.8, P-53).
--   idx_paper_events_replay ON (session_id, sequence ASC): the replay read of
--     Requirements 19.3, 19.8, 24.4 in one index scan.
--   emitted_at is TIMESTAMPTZ(6) - microsecond precision, so two events
--     emitted in the same millisecond still order by sequence rather than
--     colliding on the timestamp.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. Section 12b re-asserts the named
-- checks and uniques under guards.
CREATE TABLE IF NOT EXISTS public.paper_events (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     UUID NOT NULL REFERENCES public.paper_sessions(id) ON DELETE CASCADE,
    user_id        UUID NOT NULL,
    sequence       BIGINT NOT NULL,
    event_id       TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    payload        JSONB NOT NULL,
    emitted_at     TIMESTAMPTZ(6) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_paper_event_type CHECK (event_type IN (
        'paper_session_started', 'paper_session_paused',
        'paper_session_resumed', 'paper_session_stopped',
        'market_tick', 'signal_generated',
        'paper_order_created', 'paper_order_accepted',
        'paper_order_partially_filled', 'paper_order_filled',
        'paper_order_rejected', 'paper_position_updated',
        'paper_balance_updated', 'paper_pnl_updated',
        'paper_drawdown_updated', 'paper_error')),
    CONSTRAINT chk_paper_event_sequence CHECK (sequence >= 1),
    CONSTRAINT uq_paper_event_seq UNIQUE (session_id, sequence),
    CONSTRAINT uq_paper_event_id UNIQUE (session_id, event_id)
);

-- 12a) Shape assertion -------------------------------------------------
-- A timestamp WITHOUT time zone emitted_at, or one at second precision, would
-- lose the ordering guarantee. A JSON (not JSONB) payload preserves duplicate
-- keys and cannot be indexed. Idempotent: reads catalogues, writes nothing.
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
                ('id',             ARRAY['uuid']),
                ('session_id',     ARRAY['uuid']),
                ('user_id',        ARRAY['uuid']),
                ('sequence',       ARRAY['bigint', 'integer']),
                ('event_id',       ARRAY['text', 'character varying']),
                ('event_type',     ARRAY['text', 'character varying']),
                ('schema_version', ARRAY['text', 'character varying']),
                ('payload',        ARRAY['jsonb']),
                ('emitted_at',     ARRAY['timestamp with time zone']),
                ('created_at',     ARRAY['timestamp with time zone']),
                ('updated_at',     ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'paper_events'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.paper_events has column(s) of the wrong shape: %. The '
            'Paper_Channel replay of Requirements 19.3/19.8 depends on a '
            'bigint sequence and a JSONB payload. Reconcile it by hand before '
            're-running this migration.', problems;
    END IF;
END $$;

-- 12b) Constraint guards -----------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_events'::regclass
           AND conname  = 'chk_paper_event_type') THEN
        ALTER TABLE public.paper_events
            ADD CONSTRAINT chk_paper_event_type CHECK (event_type IN (
                'paper_session_started', 'paper_session_paused',
                'paper_session_resumed', 'paper_session_stopped',
                'market_tick', 'signal_generated',
                'paper_order_created', 'paper_order_accepted',
                'paper_order_partially_filled', 'paper_order_filled',
                'paper_order_rejected', 'paper_position_updated',
                'paper_balance_updated', 'paper_pnl_updated',
                'paper_drawdown_updated', 'paper_error'));
        RAISE NOTICE 'Added chk_paper_event_type (the 16 types of task 26.1).';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_events'::regclass
           AND conname  = 'chk_paper_event_sequence') THEN
        ALTER TABLE public.paper_events
            ADD CONSTRAINT chk_paper_event_sequence CHECK (sequence >= 1);
        RAISE NOTICE 'Added chk_paper_event_sequence (>= 1; Requirement 19.3, '
                     'P-53).';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_events'::regclass
           AND conname  = 'uq_paper_event_seq') THEN
        ALTER TABLE public.paper_events
            ADD CONSTRAINT uq_paper_event_seq UNIQUE (session_id, sequence);
        RAISE NOTICE 'Added uq_paper_event_seq (Requirement 19.3, P-53).';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_events'::regclass
           AND conname  = 'uq_paper_event_id') THEN
        ALTER TABLE public.paper_events
            ADD CONSTRAINT uq_paper_event_id UNIQUE (session_id, event_id);
        RAISE NOTICE 'Added uq_paper_event_id (Requirement 19.8, P-53).';
    END IF;
END $$;

-- 12c) Index -----------------------------------------------------------
-- design.md § Data Models: idx_paper_events_replay ON (session_id, sequence
-- ASC) - the replay read (Requirements 19.3, 19.8, 24.4).
CREATE INDEX IF NOT EXISTS idx_paper_events_replay
    ON public.paper_events (session_id, sequence ASC);
CREATE INDEX IF NOT EXISTS idx_paper_events_user
    ON public.paper_events (user_id);

-- ==========================================================================
-- SECTION 13 - paper_market_events
-- ==========================================================================
-- design.md § Data Models -> "paper_market_events". The append-only
-- market-data log, the replay input of Requirements 15.4, 15.5.
-- uq_paper_market_event UNIQUE (session_id, source_event_id) is Requirement
-- 14.7's dedupe, enforced by the database as well as by the in-memory LRU, so
-- a duplicate source event is a no-op (P-54). event_timestamp is the market
-- instant; the per-symbol non-decreasing order is validated at write time by
-- the feed layer.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. Section 13b re-asserts the unique.
CREATE TABLE IF NOT EXISTS public.paper_market_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES public.paper_sessions(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL,
    sequence        BIGINT NOT NULL,
    source_event_id TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    event_timestamp TIMESTAMPTZ NOT NULL,
    payload         JSONB NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    latency_ms      NUMERIC(10, 3),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_paper_market_event_sequence CHECK (sequence >= 1),
    CONSTRAINT uq_paper_market_event UNIQUE (session_id, source_event_id)
);

-- 13a) Shape assertion -------------------------------------------------
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
                ('id',              ARRAY['uuid']),
                ('session_id',      ARRAY['uuid']),
                ('user_id',         ARRAY['uuid']),
                ('sequence',        ARRAY['bigint', 'integer']),
                ('source_event_id', ARRAY['text', 'character varying']),
                ('symbol',          ARRAY['text', 'character varying']),
                ('timeframe',       ARRAY['text', 'character varying']),
                ('event_timestamp', ARRAY['timestamp with time zone']),
                ('payload',         ARRAY['jsonb']),
                ('received_at',     ARRAY['timestamp with time zone']),
                ('latency_ms',      ARRAY['numeric']),
                ('created_at',      ARRAY['timestamp with time zone']),
                ('updated_at',      ARRAY['timestamp with time zone'])
            ) AS expected(column_name, accepted)
      LEFT JOIN information_schema.columns actual
             ON actual.table_schema::TEXT = 'public'
            AND actual.table_name::TEXT   = 'paper_market_events'
            AND actual.column_name::TEXT  = expected.column_name
     WHERE actual.column_name IS NULL
        OR actual.data_type::TEXT <> ALL (expected.accepted);

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            'public.paper_market_events has column(s) of the wrong shape: %. '
            'The replay input of Requirements 15.4/15.5 depends on a JSONB '
            'payload and a timestamptz event_timestamp. Reconcile it by hand '
            'before re-running this migration.', problems;
    END IF;
END $$;

-- 13b) Constraint guards -----------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_market_events'::regclass
           AND conname  = 'chk_paper_market_event_sequence') THEN
        ALTER TABLE public.paper_market_events
            ADD CONSTRAINT chk_paper_market_event_sequence CHECK (sequence >= 1);
        RAISE NOTICE 'Added chk_paper_market_event_sequence (>= 1).';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_market_events'::regclass
           AND conname  = 'uq_paper_market_event') THEN
        ALTER TABLE public.paper_market_events
            ADD CONSTRAINT uq_paper_market_event UNIQUE (session_id, source_event_id);
        RAISE NOTICE 'Added uq_paper_market_event (Requirement 14.7''s dedupe, '
                     'P-54).';
    END IF;
END $$;

-- 13c) Index -----------------------------------------------------------
-- design.md § Data Models: idx_paper_market_events ON (session_id, sequence
-- ASC) - the replay read (Requirements 15.4, 15.5).
CREATE INDEX IF NOT EXISTS idx_paper_market_events
    ON public.paper_market_events (session_id, sequence ASC);
CREATE INDEX IF NOT EXISTS idx_paper_market_events_user
    ON public.paper_market_events (user_id);

-- ==========================================================================
-- SECTION 14 - functions and triggers
-- ==========================================================================
-- CREATE OR REPLACE FUNCTION is naturally idempotent. Every CREATE TRIGGER is
-- behind a pg_trigger existence guard, the same pattern 007 and 008 use.

-- 14a) updated_at ------------------------------------------------------
-- Requirement 24.5. public.marketplace_touch_updated_at() is REUSED from
-- 007, not redefined: it sets NEW.updated_at := NOW() and nothing else, which
-- is exactly what every paper_* table needs. Redefining it here would be a
-- second definition of a function 007 owns; a CREATE OR REPLACE with the same
-- body would be harmless but pointless, and one with a different body would
-- silently change 007's five triggers. So this file asserts it exists (007
-- must have run) and attaches a BEFORE UPDATE trigger to each of the thirteen
-- new tables.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.proname = 'marketplace_touch_updated_at'
    ) THEN
        RAISE EXCEPTION
            '009 precondition failed: public.marketplace_touch_updated_at() '
            'does not exist. It is defined by '
            'backend_app/migrations/007_marketplace_submissions.sql (section '
            '7a) and reused here for Requirement 24.5''s update timestamp on '
            'every paper_* table. Apply 007 first.';
    END IF;
END $$;

DO $$
DECLARE
    target TEXT;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'paper_order_allowed_transitions',
        'paper_session_allowed_transitions',
        'paper_sessions',
        'paper_accounts',
        'paper_orders',
        'paper_fills',
        'paper_positions',
        'paper_balance_events',
        'paper_trades',
        'paper_equity_snapshots',
        'paper_metrics',
        'paper_events',
        'paper_market_events'
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

-- 14b) paper_order_transition_guard() + trg_paper_order_transition_guard
-- design.md § "paper order lifecycle": "same shape as the submission guard,
-- reading paper_order_allowed_transitions". THIS IS WHAT MAKES REQUIREMENT
-- 16.4 TRUE RATHER THAN ASPIRATIONAL: a write that would move a paper order
-- along an edge the seed table does not hold is rejected by the database, not
-- only by paper_simulator. A psql session with the service key cannot
-- over-fill or resurrect a terminal order.
--
-- Two branches, in this order:
--   1. A same-value write RETURNS NEW immediately EXCEPT for the one
--      self-edge the seed holds - PARTIALLY_FILLED -> PARTIALLY_FILLED. For
--      every other state a same-value write is not a transition (no state
--      moved) and is let through so an UPDATE touching only filled_quantity,
--      avg_fill_price, fee_minor or rejection_reason is not blocked. But
--      PARTIALLY_FILLED -> PARTIALLY_FILLED IS a real transition (a further
--      partial fill), so it must be validated against the table rather than
--      short-circuited - and because the seed holds it, it passes. The check
--      below therefore skips the early return only when old = new =
--      'PARTIALLY_FILLED', so that legitimate self-fill is validated and every
--      other no-op write is cheap.
--   2. An edge absent from paper_order_allowed_transitions raises with ERRCODE
--      23514 and a message naming BOTH states (Requirement 16.4), so
--      paper_simulator can translate by SQLSTATE to
--      PAPER_ORDER_* without parsing English.
--
-- CREATE OR REPLACE FUNCTION is naturally idempotent.
CREATE OR REPLACE FUNCTION public.paper_order_transition_guard()
RETURNS TRIGGER AS $$
BEGIN
    -- A no-op write is not a transition, and is let through - UNLESS it is the
    -- one permitted self-edge, which is a real transition and must be checked
    -- against the seed (it is present, so it passes).
    IF NEW.order_state = OLD.order_state
       AND NEW.order_state <> 'PARTIALLY_FILLED' THEN
        RETURN NEW;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.paper_order_allowed_transitions t
         WHERE t.from_state = OLD.order_state
           AND t.to_state   = NEW.order_state
    ) THEN
        RAISE EXCEPTION
            'disallowed Paper_Order_State transition % -> %',
            OLD.order_state, NEW.order_state
            USING ERRCODE = '23514';
    END IF;

    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
          JOIN pg_class c     ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname  = 'trg_paper_order_transition_guard'
           AND n.nspname = 'public'
           AND c.relname = 'paper_orders'
           AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_paper_order_transition_guard
            BEFORE UPDATE ON public.paper_orders
            FOR EACH ROW EXECUTE FUNCTION public.paper_order_transition_guard();
    END IF;
END $$;

-- 14c) paper_session_guard() + trg_paper_session_guard -----------------
-- design.md § "Session state machine": enforced by
-- paper_session_allowed_transitions + trg_paper_session_guard. Requirement
-- 17.14: an operation from a state that does not permit it returns 409 naming
-- the current state and the rejected operation, changing nothing. The session
-- machine has NO self-edge, so a same-value write is not a transition and is
-- let through (this lets an UPDATE touching feed_state, event_sequence,
-- started_at etc. proceed without a state change).
--
-- CREATE OR REPLACE FUNCTION is naturally idempotent.
CREATE OR REPLACE FUNCTION public.paper_session_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.session_state = OLD.session_state THEN
        RETURN NEW;                      -- a no-op write is not a transition
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.paper_session_allowed_transitions t
         WHERE t.from_state = OLD.session_state
           AND t.to_state   = NEW.session_state
    ) THEN
        RAISE EXCEPTION
            'disallowed Paper_Session state transition % -> %',
            OLD.session_state, NEW.session_state
            USING ERRCODE = '23514';
    END IF;

    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
          JOIN pg_class c     ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname  = 'trg_paper_session_guard'
           AND n.nspname = 'public'
           AND c.relname = 'paper_sessions'
           AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_paper_session_guard
            BEFORE UPDATE ON public.paper_sessions
            FOR EACH ROW EXECUTE FUNCTION public.paper_session_guard();
    END IF;
END $$;

-- 14d) paper_session_config_immutable() + trg_paper_session_config_immutable
-- Requirement 16.12: the session configuration is captured at start and
-- frozen. config JSONB is written once at session creation and never updated.
-- A CHECK cannot express "this column may not change after INSERT" - a CHECK
-- sees only the new row - so this is a BEFORE UPDATE trigger comparing OLD and
-- NEW. IS DISTINCT FROM handles the JSONB comparison including the NULL cases
-- (config is NOT NULL, so NEW is never NULL, but the operator is the correct
-- one regardless).
--
-- CREATE OR REPLACE FUNCTION is naturally idempotent.
CREATE OR REPLACE FUNCTION public.paper_session_config_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.config IS DISTINCT FROM OLD.config THEN
        RAISE EXCEPTION
            'paper_sessions.config is frozen at start and cannot be changed '
            '(session %; Requirement 16.12)', OLD.id
            USING ERRCODE = '23514';
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
         WHERE t.tgname  = 'trg_paper_session_config_immutable'
           AND n.nspname = 'public'
           AND c.relname = 'paper_sessions'
           AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_paper_session_config_immutable
            BEFORE UPDATE ON public.paper_sessions
            FOR EACH ROW EXECUTE FUNCTION public.paper_session_config_immutable();
    END IF;
END $$;

-- 14e) The append-only guard -------------------------------------------
-- paper_fills, paper_trades, paper_equity_snapshots, paper_events,
-- paper_market_events and paper_balance_events are append-only (design.md §
-- Data Models). ONE function, six triggers. TG_TABLE_NAME and TG_OP put the
-- table and operation in the message, so a single definition covers all six.
-- 23514 (check_violation) so the service layer translates by SQLSTATE.
--
-- THE DELETE BRANCH EXEMPTS A REFERENTIAL CASCADE, exactly as 007's guard
-- does but resolving the parent through OLD.session_id against paper_sessions.
-- An unconditional raise would make deleting a session (design.md's declared
-- ON DELETE CASCADE from paper_sessions) or closing a user account (the
-- cascade from auth.users) impossible. During a cascade the parent
-- paper_sessions row is already gone when this BEFORE DELETE trigger fires,
-- while a direct DELETE always runs with the parent present. A SEPARATE
-- function from 007's marketplace_append_only_guard() and 008's
-- marketplace_settlement_append_only_guard(): generalising either would be a
-- change to an existing control.
--
-- CREATE OR REPLACE FUNCTION is naturally idempotent.
CREATE OR REPLACE FUNCTION public.paper_append_only_guard()
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
        SELECT 1 FROM public.paper_sessions s
         WHERE s.id = OLD.session_id
    ) INTO parent_exists;

    IF parent_exists THEN
        RAISE EXCEPTION
            '% is append-only: DELETE is not permitted while session % '
            'exists (row %)',
            TG_TABLE_NAME, OLD.session_id, OLD.id
            USING ERRCODE = '23514';
    END IF;

    -- The parent session is already gone, so this DELETE is the declared ON
    -- DELETE CASCADE and not a request to mutate history. Let it through.
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    target TEXT;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'paper_fills',
        'paper_trades',
        'paper_equity_snapshots',
        'paper_events',
        'paper_market_events',
        'paper_balance_events'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger t
              JOIN pg_class c     ON c.oid = t.tgrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE t.tgname   = 'trg_' || target || '_append_only'
               AND n.nspname  = 'public'
               AND c.relname  = target
               AND NOT t.tgisinternal
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON public.%I '
                'FOR EACH ROW EXECUTE FUNCTION public.paper_append_only_guard()',
                'trg_' || target || '_append_only', target);
        END IF;
    END LOOP;
END $$;

-- ==========================================================================
-- SECTION 15 - row level security
-- ==========================================================================
-- Requirements 21.2 and 21.3. Enable FIRST (default-deny), then add the
-- policies, all inside this one transaction so no session observes the
-- intermediate state. Every paper_* owned table carries user_id on the row
-- itself - the children denormalise it rather than reaching paper_sessions
-- through a subquery, because a policy predicate is evaluated PER ROW and a
-- subquery there would be a join on every read.
ALTER TABLE public.paper_sessions           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_accounts           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_orders             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_fills              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_positions          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_balance_events     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_trades             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_equity_snapshots   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_metrics            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_events             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_market_events      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_order_allowed_transitions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.paper_session_allowed_transitions ENABLE ROW LEVEL SECURITY;

-- The owner policy and the service-role policy on every owned table, the
-- pattern 007 and 006's library_subscriptions use: FOR ALL TO authenticated
-- USING (auth.uid() = user_id) WITH CHECK (the same), and FOR ALL TO
-- service_role USING (true) WITH CHECK (true). WITH CHECK repeats the USING
-- predicate rather than being omitted, so a later reader cannot mistake the
-- omission for "any new row is allowed". Guarded on pg_policies.
DO $$
DECLARE
    spec RECORD;
BEGIN
    FOR spec IN
        SELECT * FROM (VALUES
            ('paper_sessions',         'sessions'),
            ('paper_accounts',         'accounts'),
            ('paper_orders',           'orders'),
            ('paper_fills',            'fills'),
            ('paper_positions',        'positions'),
            ('paper_balance_events',   'balance_events'),
            ('paper_trades',           'trades'),
            ('paper_equity_snapshots', 'equity_snapshots'),
            ('paper_metrics',          'metrics'),
            ('paper_events',           'events'),
            ('paper_market_events',    'market_events')
        ) AS t(table_name, short_name)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
             WHERE schemaname = 'public'
               AND tablename  = spec.table_name
               AND policyname = 'paper_' || spec.short_name || '_owner_access'
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I FOR ALL TO authenticated '
                'USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id)',
                'paper_' || spec.short_name || '_owner_access', spec.table_name);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
             WHERE schemaname = 'public'
               AND tablename  = spec.table_name
               AND policyname = 'paper_' || spec.short_name || '_service_role'
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I FOR ALL TO service_role '
                'USING (true) WITH CHECK (true)',
                'paper_' || spec.short_name || '_service_role', spec.table_name);
        END IF;
    END LOOP;
END $$;

-- The two seed tables hold no user data - they name the edges of two state
-- machines published in requirements.md. READ TO EVERYONE (the guards SELECT
-- from them as the invoking role, under RLS), WRITE TO NOBODY BUT
-- service_role. Their security property is the ABSENCE of a write policy for
-- authenticated: a caller cannot add an edge and thereby authorise a
-- transition the requirement forbids.
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'paper_order_allowed_transitions',
        'paper_session_allowed_transitions'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
             WHERE schemaname = 'public'
               AND tablename  = tbl
               AND policyname = tbl || '_read'
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I FOR SELECT USING (true)',
                tbl || '_read', tbl);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
             WHERE schemaname = 'public'
               AND tablename  = tbl
               AND policyname = tbl || '_service_role'
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I FOR ALL TO service_role '
                'USING (true) WITH CHECK (true)',
                tbl || '_service_role', tbl);
        END IF;
    END LOOP;
END $$;

-- ==========================================================================
-- SECTION 16 - grants
-- ==========================================================================
-- Table privileges are the layer below RLS. Following 007: revoke everything
-- from anon and authenticated first, then grant exactly what each role needs.
-- Idempotent: REVOKE and GRANT are idempotent by definition.
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'paper_sessions', 'paper_accounts', 'paper_orders', 'paper_fills',
        'paper_positions', 'paper_balance_events', 'paper_trades',
        'paper_equity_snapshots', 'paper_metrics', 'paper_events',
        'paper_market_events', 'paper_order_allowed_transitions',
        'paper_session_allowed_transitions'
    ] LOOP
        EXECUTE format('REVOKE ALL ON public.%I FROM anon', tbl);
        EXECUTE format('REVOKE ALL ON public.%I FROM authenticated', tbl);
    END LOOP;
END $$;

-- An owner reads and writes their own sessions and accounts, reads their
-- orders/positions/metrics, and reads (never rewrites) the append-only logs.
-- The append-only tables get SELECT + INSERT and no UPDATE/DELETE; the six
-- append-only guards of section 14e are the primary control, these grants the
-- layer below. A session's own row can be updated by its owner (start/pause/
-- resume/stop drive session_state, guarded by trg_paper_session_guard); the
-- config-immutability trigger and the transition guard bound what that UPDATE
-- may do. No DELETE anywhere for authenticated: a session is deleted only by
-- the account cascade, not by a client.
GRANT SELECT, INSERT, UPDATE ON public.paper_sessions    TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.paper_accounts    TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.paper_orders      TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.paper_positions   TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.paper_metrics     TO authenticated;
GRANT SELECT, INSERT ON public.paper_fills               TO authenticated;
GRANT SELECT, INSERT ON public.paper_balance_events      TO authenticated;
GRANT SELECT, INSERT ON public.paper_trades              TO authenticated;
GRANT SELECT, INSERT ON public.paper_equity_snapshots    TO authenticated;
GRANT SELECT, INSERT ON public.paper_events              TO authenticated;
GRANT SELECT, INSERT ON public.paper_market_events       TO authenticated;
GRANT SELECT ON public.paper_order_allowed_transitions   TO authenticated;
GRANT SELECT ON public.paper_session_allowed_transitions TO authenticated;

-- service_role: everything the backend does. UPDATE on paper_sessions,
-- paper_accounts, paper_orders, paper_positions, paper_metrics (the live
-- mutable state); SELECT + INSERT on the append-only logs; SELECT + INSERT on
-- the seeds (a future migration or job can add an edge).
GRANT SELECT, INSERT, UPDATE ON public.paper_sessions    TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.paper_accounts    TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.paper_orders      TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.paper_positions   TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.paper_metrics     TO service_role;
GRANT SELECT, INSERT ON public.paper_fills               TO service_role;
GRANT SELECT, INSERT ON public.paper_balance_events      TO service_role;
GRANT SELECT, INSERT ON public.paper_trades              TO service_role;
GRANT SELECT, INSERT ON public.paper_equity_snapshots    TO service_role;
GRANT SELECT, INSERT ON public.paper_events              TO service_role;
GRANT SELECT, INSERT ON public.paper_market_events       TO service_role;
GRANT SELECT, INSERT ON public.paper_order_allowed_transitions   TO service_role;
GRANT SELECT, INSERT ON public.paper_session_allowed_transitions TO service_role;

-- The explicit last word: the six append-only tables and the two seeds refuse
-- UPDATE and DELETE from every role below superuser. Stated after the GRANTs
-- so a widened GRANT list above cannot re-open them by accident.
REVOKE UPDATE, DELETE ON public.paper_fills            FROM anon, authenticated, service_role;
REVOKE UPDATE, DELETE ON public.paper_balance_events   FROM anon, authenticated, service_role;
REVOKE UPDATE, DELETE ON public.paper_trades           FROM anon, authenticated, service_role;
REVOKE UPDATE, DELETE ON public.paper_equity_snapshots FROM anon, authenticated, service_role;
REVOKE UPDATE, DELETE ON public.paper_events           FROM anon, authenticated, service_role;
REVOKE UPDATE, DELETE ON public.paper_market_events    FROM anon, authenticated, service_role;
REVOKE UPDATE, DELETE ON public.paper_order_allowed_transitions   FROM anon, authenticated;
REVOKE UPDATE, DELETE ON public.paper_session_allowed_transitions FROM anon, authenticated;
-- No DELETE of a live session/account/order/position/metric by a client: the
-- cascade from paper_sessions is the only path that removes them.
REVOKE DELETE ON public.paper_sessions  FROM anon, authenticated;
REVOKE DELETE ON public.paper_accounts  FROM anon, authenticated;
REVOKE DELETE ON public.paper_orders    FROM anon, authenticated;
REVOKE DELETE ON public.paper_positions FROM anon, authenticated;
REVOKE DELETE ON public.paper_metrics   FROM anon, authenticated;

-- ==========================================================================
-- SECTION 17 - comments
-- ==========================================================================
-- COMMENT replaces, so this is idempotent.

COMMENT ON TABLE public.paper_sessions IS
    'A Paper_Session (Requirement 17): the RLS-owner root every other paper_* '
    'table cascades from, and the resource the Paper_Channel '
    '(paper.{session_id}) and websocket_auth._PAPER_SESSIONS_RELATION resolve. '
    'session_state is the authoritative four-value machine of Requirement '
    '17.7, enforced by chk_paper_session_state and, for transitions, by '
    'trg_paper_session_guard against paper_session_allowed_transitions - an '
    'illegal operation is refused even by a direct SQL UPDATE (Requirement '
    '17.14). environment is pinned to PAPER by chk_paper_session_environment. '
    'config is frozen at start by trg_paper_session_config_immutable '
    '(Requirement 16.12).';

COMMENT ON COLUMN public.paper_sessions.listing_id IS
    'The subscribed Listing being paper-traded, ON DELETE SET NULL (NULL for '
    'an owned strategy). A deleted Listing never deletes the session or its '
    'history (design.md Delete rules).';

COMMENT ON TABLE public.paper_order_allowed_transitions IS
    'The nine permitted Paper_Order_State edges of Requirement 16.2, as data. '
    'MUST equal PAPER_ORDER_TRANSITIONS in '
    'backend_app/backend/paper/paper_order_state.py; task 14.6 asserts the two '
    'are the same set. Includes the single self-edge PARTIALLY_FILLED -> '
    'PARTIALLY_FILLED (successive partial fills); the three terminal states '
    'appear only as a to_state. Platform-global reference data, no owner: RLS '
    'on with a read-to-everyone SELECT policy (the order guard SELECTs from '
    'here as the invoking role) and no write policy for authenticated.';

COMMENT ON TABLE public.paper_session_allowed_transitions IS
    'The six permitted Paper_Session state edges of Requirement 17.7, as data: '
    'start CREATED->RUNNING, pause RUNNING->PAUSED, resume PAUSED->RUNNING, '
    'stop RUNNING->STOPPED and PAUSED->STOPPED, reset STOPPED->CREATED. No '
    'self-edge. Same RLS shape as paper_order_allowed_transitions.';

COMMENT ON TABLE public.paper_accounts IS
    'Simulated balances (design.md § "The default account"). session_id NULL '
    'is the DEFAULT account the existing /api/paper/account, /positions, '
    '/orders, /trades, /summary endpoints serve; a non-null session_id is a '
    'per-session account. chk_paper_balances_non_negative keeps balances >= 0 '
    '(Requirement 18.4, P-26). version is the optimistic-concurrency counter '
    '(Requirement 16.10). Every money column is NUMERIC(28,10), never a '
    'float.';

COMMENT ON TABLE public.paper_orders IS
    'The order book. order_state is the six Paper_Order_State values '
    '(Requirement 16.1), transitions enforced by '
    'trg_paper_order_transition_guard against paper_order_allowed_transitions '
    '(Requirement 16.4). legacy_status carries the retained PaperOrderStatus '
    'spelling so GET /api/paper/orders?status=OPEN keeps its meaning '
    '(Requirement 17.12). uq_paper_order_idem is Requirement 16.11''s dedupe; '
    'chk_paper_order_fill_bound keeps filled_quantity in [0, quantity] '
    '(Requirement 16.7, P-20).';

COMMENT ON TABLE public.paper_fills IS
    'Every fill, APPEND-ONLY (trg_paper_fills_append_only plus revoked '
    'UPDATE/DELETE). uq_paper_fill_event makes a duplicate fill a no-op '
    '(Requirement 16.11, P-21).';

COMMENT ON TABLE public.paper_positions IS
    'Open and closed positions. uq_paper_position_open (partial, WHERE '
    'closed_at IS NULL) allows at most one open position per account per '
    'symbol; size >= 0 (Requirement 18.5, P-26).';

COMMENT ON TABLE public.paper_balance_events IS
    'Requirement 26.3''s per-balance-change record, APPEND-ONLY. cause is one '
    'of ORDER_LOCK, ORDER_UNLOCK, FILL, FEE, RESET; the *_delta and *_after '
    'columns let the ledger be replayed.';

COMMENT ON TABLE public.paper_trades IS
    'A closed round-trip, APPEND-ONLY: a row exists only when a position '
    'reached size zero (Requirement 18.10). realized_pnl is the trade''s '
    'profit or loss in NUMERIC(28,10).';

COMMENT ON TABLE public.paper_equity_snapshots IS
    'The equity curve, APPEND-ONLY. cause is one of SESSION_START, FILL, FEE, '
    'REVALUATION, SESSION_STOP. (session_id, series_index, taken_at ASC) is '
    'the non-decreasing order Requirement 18.9 computes drawdown from.';

COMMENT ON TABLE public.paper_metrics IS
    'Computed session metrics. win_rate NUMERIC(6,5) and max_drawdown_fraction '
    'are NULLABLE and their checks admit NULL: an empty closed-trade set has '
    'no win rate - ABSENT, not zero (Requirement 18.10, P-30, P-29).';

COMMENT ON TABLE public.paper_events IS
    'The Paper_Channel emission log, APPEND-ONLY. event_type is one of the 16 '
    'types of task 26.1 (chk_paper_event_type). (session_id, sequence) is '
    'unique and contiguous-from-1 per session (Requirement 19.3, P-53); '
    '(session_id, event_id) is unique (Requirement 19.8). '
    'idx_paper_events_replay serves the replay read.';

COMMENT ON TABLE public.paper_market_events IS
    'The market-data log, APPEND-ONLY, the replay input of Requirements 15.4, '
    '15.5. uq_paper_market_event (session_id, source_event_id) is Requirement '
    '14.7''s dedupe enforced by the database as well as the in-memory LRU '
    '(P-54).';

COMMENT ON FUNCTION public.paper_order_transition_guard() IS
    'Requirement 16.4''s enforcement: rejects any Paper_Order_State transition '
    'absent from paper_order_allowed_transitions with ERRCODE 23514 naming '
    'both states. A same-value write returns early except for the permitted '
    'PARTIALLY_FILLED self-edge, which is a real transition and is validated '
    'against (and passes) the seed.';

COMMENT ON FUNCTION public.paper_session_guard() IS
    'Requirement 17.14''s enforcement: rejects any Paper_Session state '
    'transition absent from paper_session_allowed_transitions with ERRCODE '
    '23514 naming both states. A same-value write is not a transition and is '
    'let through.';

COMMENT ON FUNCTION public.paper_session_config_immutable() IS
    'Requirement 16.12: paper_sessions.config is frozen at start. Refuses any '
    'UPDATE that changes config, with ERRCODE 23514.';

COMMENT ON FUNCTION public.paper_append_only_guard() IS
    'Refuses every UPDATE, and every DELETE whose parent session still exists, '
    'on paper_fills, paper_trades, paper_equity_snapshots, paper_events, '
    'paper_market_events and paper_balance_events. The parent-existence test '
    'lets the declared ON DELETE CASCADE from paper_sessions (and from '
    'auth.users) still complete. A separate function from 007''s '
    'marketplace_append_only_guard() and 008''s '
    'marketplace_settlement_append_only_guard(), resolving its parent through '
    'session_id.';

-- ==========================================================================
-- SECTION 18 - postflight
-- ==========================================================================
-- Turns the header's promises into facts: the thirteen tables, every named
-- constraint, every named index, every function, every trigger, both seeds
-- with their exact row counts, and RLS on with at least two policies per
-- table. Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    missing          TEXT;
    order_seed_rows  BIGINT;
    session_seed_rows BIGINT;
    rls_off          TEXT;
BEGIN
    -- (a) The thirteen tables exist.
    SELECT string_agg(expected.table_name, ', ' ORDER BY expected.table_name)
      INTO missing
      FROM (VALUES ('paper_sessions'), ('paper_accounts'), ('paper_orders'),
                   ('paper_fills'), ('paper_positions'),
                   ('paper_balance_events'), ('paper_trades'),
                   ('paper_equity_snapshots'), ('paper_metrics'),
                   ('paper_events'), ('paper_market_events'),
                   ('paper_order_allowed_transitions'),
                   ('paper_session_allowed_transitions'))
             AS expected(table_name)
     WHERE to_regclass('public.' || expected.table_name) IS NULL;

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '009 postflight failed: table(s) % were not created.',
                        missing;
    END IF;

    -- (b) Every named constraint exists, on the right table.
    SELECT string_agg(format('%s on %s', expected.conname, expected.table_name),
                      ', ' ORDER BY expected.conname)
      INTO missing
      FROM (VALUES
                ('chk_paper_session_environment',    'paper_sessions'),
                ('chk_paper_session_state',          'paper_sessions'),
                ('chk_paper_capital',                'paper_sessions'),
                ('chk_paper_balances_non_negative',  'paper_accounts'),
                ('chk_paper_order_state',            'paper_orders'),
                ('chk_paper_order_fill_bound',       'paper_orders'),
                ('chk_paper_order_idem_len',         'paper_orders'),
                ('uq_paper_order_idem',              'paper_orders'),
                ('uq_paper_fill_event',              'paper_fills'),
                ('chk_paper_win_rate',               'paper_metrics'),
                ('chk_paper_drawdown_fraction',      'paper_metrics'),
                ('chk_paper_event_type',             'paper_events'),
                ('chk_paper_event_sequence',         'paper_events'),
                ('uq_paper_event_seq',               'paper_events'),
                ('uq_paper_event_id',                'paper_events'),
                ('uq_paper_market_event',            'paper_market_events'),
                ('pk_paper_order_allowed_transitions',   'paper_order_allowed_transitions'),
                ('pk_paper_session_allowed_transitions', 'paper_session_allowed_transitions')
            ) AS expected(conname, table_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_constraint pc
                WHERE pc.conname  = expected.conname
                  AND pc.conrelid = ('public.' || expected.table_name)::regclass);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '009 postflight failed: constraint(s) % are absent '
                        '(Requirements 16.4, 16.11, 18.4, 24.2, 24.3).',
                        missing;
    END IF;

    -- (c) uq_paper_account_default and uq_paper_position_open are PARTIAL
    --     unique INDEXES, not constraints - they are checked as indexes.
    -- (d) Every named index exists.
    SELECT string_agg(format('%s on %s', expected.indexname, expected.table_name),
                      ', ' ORDER BY expected.indexname)
      INTO missing
      FROM (VALUES
                ('idx_paper_sessions_user',        'paper_sessions'),
                ('idx_paper_sessions_running',     'paper_sessions'),
                ('uq_paper_account_default',       'paper_accounts'),
                ('uq_paper_account_session',       'paper_accounts'),
                ('idx_paper_orders_session_state', 'paper_orders'),
                ('uq_paper_position_open',         'paper_positions'),
                ('idx_paper_balance_events',       'paper_balance_events'),
                ('idx_paper_trades',               'paper_trades'),
                ('idx_paper_equity',               'paper_equity_snapshots'),
                ('idx_paper_events_replay',        'paper_events'),
                ('idx_paper_market_events',        'paper_market_events')
            ) AS expected(indexname, table_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_indexes pi
                WHERE pi.schemaname      = 'public'
                  AND pi.tablename       = expected.table_name
                  AND pi.indexname::TEXT = expected.indexname);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '009 postflight failed: index(es) % were not created '
                        '(Requirement 24.4).', missing;
    END IF;

    -- The two account uniques and the position-open unique must be PARTIAL.
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public' AND tablename = 'paper_accounts'
           AND indexname  = 'uq_paper_account_default'
           AND indexdef ILIKE '%UNIQUE%' AND indexdef ILIKE '%WHERE%') THEN
        RAISE EXCEPTION '009 postflight failed: uq_paper_account_default is not '
                        'a PARTIAL UNIQUE index (one default account per user '
                        'and currency, session_id IS NULL).';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public' AND tablename = 'paper_positions'
           AND indexname  = 'uq_paper_position_open'
           AND indexdef ILIKE '%UNIQUE%' AND indexdef ILIKE '%WHERE%') THEN
        RAISE EXCEPTION '009 postflight failed: uq_paper_position_open is not a '
                        'PARTIAL UNIQUE index (one open position per account '
                        'per symbol, closed_at IS NULL).';
    END IF;

    -- (e) The four functions and their triggers exist.
    SELECT string_agg(expected.proname, ', ' ORDER BY expected.proname)
      INTO missing
      FROM (VALUES ('paper_order_transition_guard'),
                   ('paper_session_guard'),
                   ('paper_session_config_immutable'),
                   ('paper_append_only_guard'))
             AS expected(proname)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_proc p
                 JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public'
                  AND p.proname = expected.proname);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '009 postflight failed: function(s) % are absent.',
                        missing;
    END IF;

    SELECT string_agg(format('%s on %s', expected.tgname, expected.table_name),
                      ', ' ORDER BY expected.tgname)
      INTO missing
      FROM (VALUES
                ('trg_paper_order_transition_guard',    'paper_orders'),
                ('trg_paper_session_guard',             'paper_sessions'),
                ('trg_paper_session_config_immutable',  'paper_sessions'),
                ('trg_paper_fills_append_only',         'paper_fills'),
                ('trg_paper_trades_append_only',        'paper_trades'),
                ('trg_paper_equity_snapshots_append_only', 'paper_equity_snapshots'),
                ('trg_paper_events_append_only',        'paper_events'),
                ('trg_paper_market_events_append_only', 'paper_market_events'),
                ('trg_paper_balance_events_append_only','paper_balance_events'),
                ('trg_paper_sessions_updated_at',       'paper_sessions')
            ) AS expected(tgname, table_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_trigger t
                WHERE t.tgname   = expected.tgname
                  AND t.tgrelid  = ('public.' || expected.table_name)::regclass
                  AND NOT t.tgisinternal);

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '009 postflight failed: trigger(s) % are absent. '
                        'Requirement 16.4/17.14''s enforcement and the '
                        'append-only immutability depend on them.', missing;
    END IF;

    -- (f) The seeds hold exactly their pairs, and no self-edge on the session
    --     machine.
    SELECT count(*) INTO order_seed_rows
      FROM public.paper_order_allowed_transitions;
    IF order_seed_rows <> 9 THEN
        RAISE EXCEPTION
            '009 postflight failed: paper_order_allowed_transitions holds % '
            'row(s), not the 9 of Requirement 16.2. It must equal '
            'PAPER_ORDER_TRANSITIONS in '
            'backend_app/backend/paper/paper_order_state.py.', order_seed_rows;
    END IF;

    SELECT count(*) INTO session_seed_rows
      FROM public.paper_session_allowed_transitions;
    IF session_seed_rows <> 6 THEN
        RAISE EXCEPTION
            '009 postflight failed: paper_session_allowed_transitions holds % '
            'row(s), not the 6 of Requirement 17.7.', session_seed_rows;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.paper_order_allowed_transitions
         WHERE from_state = 'PARTIALLY_FILLED' AND to_state = 'PARTIALLY_FILLED'
    ) THEN
        RAISE EXCEPTION
            '009 postflight failed: paper_order_allowed_transitions is missing '
            'the single permitted self-edge PARTIALLY_FILLED -> '
            'PARTIALLY_FILLED (Requirement 16.2).';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.paper_session_allowed_transitions
         WHERE from_state = to_state
    ) THEN
        RAISE EXCEPTION
            '009 postflight failed: paper_session_allowed_transitions holds a '
            'self-edge; the session machine has none (Requirement 17.7).';
    END IF;

    -- (g) RLS on, and at least two policies per table.
    SELECT string_agg(expected.table_name, ', ' ORDER BY expected.table_name)
      INTO rls_off
      FROM (VALUES ('paper_sessions'), ('paper_accounts'), ('paper_orders'),
                   ('paper_fills'), ('paper_positions'),
                   ('paper_balance_events'), ('paper_trades'),
                   ('paper_equity_snapshots'), ('paper_metrics'),
                   ('paper_events'), ('paper_market_events'),
                   ('paper_order_allowed_transitions'),
                   ('paper_session_allowed_transitions'))
             AS expected(table_name)
     WHERE NOT EXISTS (
               SELECT 1 FROM pg_class c
                WHERE c.oid = ('public.' || expected.table_name)::regclass
                  AND c.relrowsecurity);

    IF rls_off IS NOT NULL THEN
        RAISE EXCEPTION '009 postflight failed: row level security is DISABLED '
                        'on %. Requirement 21.2 requires it on every table '
                        'this specification introduces.', rls_off;
    END IF;

    SELECT string_agg(expected.table_name, ', ' ORDER BY expected.table_name)
      INTO missing
      FROM (VALUES ('paper_sessions'), ('paper_accounts'), ('paper_orders'),
                   ('paper_fills'), ('paper_positions'),
                   ('paper_balance_events'), ('paper_trades'),
                   ('paper_equity_snapshots'), ('paper_metrics'),
                   ('paper_events'), ('paper_market_events'),
                   ('paper_order_allowed_transitions'),
                   ('paper_session_allowed_transitions'))
             AS expected(table_name)
     WHERE (SELECT count(*) FROM pg_policies p
             WHERE p.schemaname = 'public'
               AND p.tablename  = expected.table_name) < 2;

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '009 postflight failed: table(s) % have fewer than the '
                        '2 policies this file creates, so with RLS enabled '
                        'they are unreachable (Requirements 21.2, 21.3).',
                        missing;
    END IF;

    RAISE NOTICE '009 complete: 13 paper tables (11 paper_* + 2 transition '
                 'seeds) exist with % order-transition rows and % '
                 'session-transition rows, every named constraint and index, '
                 '4 functions and their guard/immutability/append-only/'
                 'updated_at triggers, RLS enabled with an owner and a '
                 'service-role policy each. No pre-existing table was touched.',
                 order_seed_rows, session_seed_rows;
END $$;

COMMIT;

-- ==========================================================================
-- VERIFICATION QUERIES - run these after applying, the way 007/008 do
-- ==========================================================================
--
-- 1) The thirteen tables and their column counts:
--
--    SELECT table_name, count(*) AS columns
--      FROM information_schema.columns
--     WHERE table_schema = 'public' AND table_name LIKE 'paper_%'
--     GROUP BY table_name ORDER BY table_name;
--
-- 2) The two transition seeds MUST equal their Python constants:
--
--    SELECT from_state, to_state FROM public.paper_order_allowed_transitions
--     ORDER BY from_state, to_state;
--    -- Expect exactly 9 rows including PARTIALLY_FILLED -> PARTIALLY_FILLED.
--    -- Must equal PAPER_ORDER_TRANSITIONS in
--    -- backend_app/backend/paper/paper_order_state.py (task 14.6).
--
--    SELECT from_state, to_state FROM public.paper_session_allowed_transitions
--     ORDER BY from_state, to_state;
--    -- Expect exactly 6 rows: CREATED->RUNNING, PAUSED->RUNNING,
--    -- PAUSED->STOPPED, RUNNING->PAUSED, RUNNING->STOPPED, STOPPED->CREATED.
--
-- 3) The order state machine is enforced against a DIRECT UPDATE (Requirement
--    16.4, P-49). Inside a transaction you ROLL BACK, take a CREATED order and
--    try CREATED -> FILLED (not an edge):
--
--    BEGIN;
--      UPDATE public.paper_orders SET order_state = 'FILLED'
--       WHERE id = '<a CREATED id>';
--      -- expect: ERROR 23514 disallowed Paper_Order_State transition
--      --         CREATED -> FILLED
--    ROLLBACK;
--
-- 4) The session state machine (Requirement 17.14):
--
--    BEGIN;
--      UPDATE public.paper_sessions SET session_state = 'STOPPED'
--       WHERE id = '<a CREATED id>';
--      -- expect: ERROR 23514 disallowed Paper_Session state transition
--      --         CREATED -> STOPPED
--    ROLLBACK;
--
-- 5) config immutability (Requirement 16.12):
--
--    BEGIN;
--      UPDATE public.paper_sessions SET config = '{}'::jsonb
--       WHERE id = '<any id>';
--      -- expect: ERROR 23514 paper_sessions.config is frozen at start
--    ROLLBACK;
--
-- 6) The append-only guards (Requirements 18.10, 19.3, and the immutability of
--    fills/trades/equity/events/market_events/balance_events):
--
--    BEGIN;
--      UPDATE public.paper_fills SET quantity = 999 WHERE id = '<any id>';
--      -- expect: ERROR 23514 paper_fills is append-only: UPDATE ...
--    ROLLBACK;
--    BEGIN;
--      DELETE FROM public.paper_events WHERE id = '<any id>';
--      -- expect: ERROR 23514 ... DELETE is not permitted while session ...
--    ROLLBACK;
--
--    And the cascade that MUST still work:
--
--    BEGIN;
--      DELETE FROM public.paper_sessions WHERE id = '<any id>';
--      -- expect: success. Its accounts, orders, fills, positions, balance
--      -- events, trades, equity snapshots, metrics, events and market events
--      -- go with it.
--    ROLLBACK;
--
-- 7) RLS and the policies (2 per table, 26 in all):
--
--    SELECT tablename, policyname, cmd, roles
--      FROM pg_policies
--     WHERE schemaname = 'public' AND tablename LIKE 'paper_%'
--     ORDER BY tablename, policyname;
--
-- 8) Grants. Expect authenticated: no DELETE anywhere; SELECT+INSERT (no
--    UPDATE) on the six append-only tables and on the two seeds (SELECT only);
--    SELECT+INSERT+UPDATE on paper_sessions, paper_accounts, paper_orders,
--    paper_positions, paper_metrics.
--
--    SELECT table_name, grantee, string_agg(privilege_type, ',' ORDER BY
--           privilege_type) AS privileges
--      FROM information_schema.role_table_grants
--     WHERE table_schema = 'public' AND table_name LIKE 'paper_%'
--       AND grantee IN ('anon', 'authenticated', 'service_role')
--     GROUP BY table_name, grantee ORDER BY table_name, grantee;
--
-- 9) Idempotency (Requirements 24.7, 24.8, Property P-58). Re-running this
--    whole file must report no error, must leave the seeds at 9 and 6 rows,
--    and must leave every count above unchanged.
--
-- ==========================================================================
-- END OF MIGRATION 009. NEXT IN DEPENDENCY ORDER:
--   010_signal_environment.sql  (needs paper_sessions above for the FK)
-- ==========================================================================
