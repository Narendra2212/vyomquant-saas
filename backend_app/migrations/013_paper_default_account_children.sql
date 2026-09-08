-- 013_paper_default_account_children.sql
--
-- PURPOSE
--   Let the DEFAULT PAPER ACCOUNT own something. 009 makes
--   public.paper_accounts.session_id NULLABLE, and that nullable row IS the
--   default account - the one the six existing /api/paper/* endpoints serve
--   (design.md § "The default account"), the one uq_paper_account_default
--   (partial unique on (user_id, currency) WHERE session_id IS NULL) keeps
--   single, and the one design.md § "Response-shape compatibility" describes
--   as answering session_id: null.
--
--   But seven of its child tables declare
--
--       session_id UUID NOT NULL REFERENCES public.paper_sessions(id)
--
--   so the default account had nowhere to persist an order, a fill, a
--   position, a balance movement, a closed trade, an equity point or a
--   computed metric. This file relaxes exactly those seven columns to NULLABLE
--   and then RECONCILES every guarantee that had been resting on the column
--   being non-null, so the relaxation costs nothing.
--
--   marketplace-subscriptions-paper-trading, remediation arising from task
--   23.1, which unblocks task 23.2. Requirements 17.1, 17.2, 17.6, 17.12,
--   18.4, 18.5, 18.13, 16.8, 16.11, 21.2, 21.5, 24.3, 24.7, 24.8, 28.3.
--
-- THE DEFECT THIS FILE CLOSES, EXACTLY
--   public.paper_accounts.session_id  ->  NULLABLE   (009 section 4)
--   public.paper_orders.session_id            NOT NULL   (009 section 5)
--   public.paper_fills.session_id             NOT NULL   (009 section 6)
--   public.paper_positions.session_id         NOT NULL   (009 section 7)
--   public.paper_balance_events.session_id    NOT NULL   (009 section 8)
--   public.paper_trades.session_id            NOT NULL   (009 section 9)
--   public.paper_equity_snapshots.session_id  NOT NULL   (009 section 10)
--   public.paper_metrics.session_id           NOT NULL   (009 section 11)
--
--   A default-account order therefore needed a public.paper_sessions row to
--   point at, and public.paper_sessions requires source_strategy_id,
--   version_id, exchange_id, symbol, timeframe, initial_capital_minor,
--   currency, config and market_data_source - nine NOT NULL columns - none of
--   which a default account has an honest value for. So the write was
--   impossible, and every /api/paper/orders POST against the default account
--   would have failed with 23502 not_null_violation once task 23.2 repointed
--   the service at the repository.
--
--   After this file a row belonging to the default account carries
--   session_id IS NULL exactly as its account does, and a row belonging to a
--   Paper_Session carries that session's id exactly as before. The two kinds
--   stay distinguishable by the same predicate that distinguishes their
--   accounts.
--
-- THE ALTERNATIVE THAT WAS REJECTED, AND WHY
--   A synthetic "default session" row per user - one public.paper_sessions row
--   with placeholder values, existing only so the child foreign keys have a
--   target. It is rejected for two reasons:
--
--     1. Its nine NOT NULL columns have no honest value for an account that is
--        not running a strategy. source_strategy_id and version_id would name
--        a strategy that is not being traded; exchange_id, symbol and timeframe
--        would name a market the account is not scoped to; initial_capital_minor
--        would restate the account's own capital in a second place, where it
--        could drift; config and market_data_source would describe a feed that
--        was never connected. Every one of those is a fabricated figure, which
--        Requirement 28.3 forbids on a production path, and Requirement 28.5
--        says an unavailable value is omitted rather than defaulted.
--
--     2. public.paper_sessions would then mean two different things - "a
--        Paper_Session someone started" and "a placeholder that exists so a
--        foreign key resolves". Every count, every RUNNING-cap query
--        (idx_paper_sessions_running), every session list read and every
--        replay of paper_events would have to learn to exclude the second
--        kind, and the first query that forgot would report a session the user
--        never started.
--
--   NULL is the honest representation of "this row belongs to no
--   Paper_Session", and it is already the representation paper_accounts uses
--   for the same fact.
--
-- A DELIBERATE, DOCUMENTED DIVERGENCE FROM REQUIREMENT 17.11 READ LITERALLY
--   Requirement 17.11 reads: "THE Persistence_Layer SHALL enforce that every
--   paper order, fill, position, balance, trade, equity snapshot, metric and
--   event row references a Paper_Session and a user, with foreign keys". Read
--   as an unconditional NOT NULL on session_id it contradicts Requirement
--   17.12, which retains the six existing /api/paper/* endpoints and their
--   response shapes - endpoints that serve an account which is not a
--   Paper_Session and never was, and which design.md § "Response-shape
--   compatibility" says answers session_id: null.
--
--   The resolution is 17.12, because 17.11's purpose is tenant isolation and
--   an owner cascade, and BOTH survive: the user_id side of 17.11 is untouched
--   and still NOT NULL on all seven tables, still carries the row-level
--   security predicate auth.uid() = user_id, and is still the predicate every
--   read and write in paper_repository issues. What is relaxed is only the
--   session side, and only for rows whose account has no session either. A
--   row with session_id IS NULL is still reachable from, and still cascades
--   with, its account (account_id on paper_orders, paper_positions,
--   paper_balance_events and paper_trades; order_id on paper_fills).
--
--   Named here, in section 5's postflight assertions, and in
--   backend_app/backend/paper/paper_repository.py's module docstring, so the
--   divergence cannot become invisible drift.
--
-- WHAT THIS FILE RECONCILES, AND WHY EACH RECONCILIATION IS NECESSARY
--
--   1. uq_paper_order_idem UNIQUE (session_id, idempotency_key)   -> WIDENED
--      This is the one that would have silently broken. SQL treats NULLs as
--      DISTINCT in a unique index, so with session_id IS NULL the index stops
--      de-duplicating altogether: (NULL, 'k') never conflicts with
--      (NULL, 'k'), and every retry of a default-account order intent would
--      have inserted another order. That is precisely the failure Requirement
--      16.8's idempotency exists to prevent, and it would have been a SILENT
--      one - no error, just two orders. Section 2 adds a companion PARTIAL
--      unique index for the NULL case. See section 2 for why the key is
--      (account_id, idempotency_key) and not something else.
--
--   2. public.paper_append_only_guard()                           -> REPLACED
--      The second one that would have silently broken, and it would have
--      broken immutability rather than idempotency. 009's guard exempts a
--      referential cascade by asking whether the parent session row still
--      exists:
--
--          SELECT EXISTS (SELECT 1 FROM public.paper_sessions s
--                          WHERE s.id = OLD.session_id)
--
--      With OLD.session_id IS NULL that EXISTS is FALSE - no row equals NULL -
--      so the guard would have concluded "the parent is already gone, this is
--      a cascade" and LET THE ROW REMOVAL THROUGH. Every default-account fill,
--      balance event, closed trade and equity point would have been mutable
--      history that any holder of the service key could erase, one row at a
--      time, with no error. Section 3 replaces the function so the NULL case
--      resolves the parent the row ACTUALLY has instead of assuming
--      paper_sessions. This is the only object 009 owns that this file
--      touches, it is touched with CREATE OR REPLACE (nothing is removed), the
--      change is strictly a tightening, and section 5 asserts both the old
--      behaviour and the new branch are in force afterwards.
--
--   3. Everything else was checked and needed nothing. The verdicts, so a
--      later reader does not have to re-derive them:
--
--      uq_paper_account_default   (user_id, currency) WHERE session_id IS NULL
--        UNAFFECTED. It is the index that DEFINES the default account, on a
--        column that was already nullable. This file does not touch
--        paper_accounts at all. Section 5 asserts it is still unique and still
--        partial, because it is what keeps the default account single and the
--        new index in section 2 relies on that singleness.
--
--      uq_paper_account_session   (session_id, currency)
--        UNAFFECTED, and already reconciled by 009: paper_accounts.session_id
--        was always nullable, and the default account is excluded from this
--        index by the same NULL-distinctness that made problem 1 above - which
--        is correct there, because uq_paper_account_default covers exactly the
--        rows this index lets through.
--
--      uq_paper_fill_event        (order_id, fill_event_id)
--        UNAFFECTED. Keyed on order_id, which is NOT NULL on paper_fills and
--        stays NOT NULL. A repeated fill event on a default-account order is
--        still a no-op (Requirements 16.11, 18.13, P-21), because the pair
--        that identifies it never mentions session_id.
--
--      uq_paper_position_open     (account_id, symbol) WHERE closed_at IS NULL
--        UNAFFECTED. Keyed on account_id, NOT NULL on paper_positions and
--        staying NOT NULL. One open position per symbol per account holds for
--        the default account exactly as for a session account - and this is
--        the index design.md already cites as the reason the child reads are
--        scoped by account_id rather than by session_id.
--
--      uq_paper_event_seq / uq_paper_event_id / uq_paper_market_event
--        UNAFFECTED. paper_events and paper_market_events are NOT relaxed by
--        this file: they are the Paper_Channel and market-data logs of a
--        Paper_Session, they have no meaning for an account that is not
--        running one, and their session_id stays NOT NULL. Section 5 asserts
--        that, so a later reader cannot mistake this file for a general
--        relaxation.
--
--      chk_paper_order_fill_bound CHECK (filled_quantity >= 0
--                                        AND filled_quantity <= quantity)
--        UNAFFECTED. Both operands are columns of the same row and neither is
--        session_id, so the bound holds identically whether session_id is null
--        or not. An order can still never be over-filled (Requirement 16.7,
--        P-20). Asserted present in section 5.
--
--      chk_paper_balances_non_negative CHECK (available_balance >= 0
--                                             AND locked_balance >= 0)
--        UNAFFECTED. It lives on paper_accounts, which this file does not
--        alter, and reads two money columns. Requirement 18.4 / P-26 is
--        untouched. Asserted present in section 5.
--
--      chk_paper_order_idem_len, chk_paper_order_side/type/quantity/
--      limit_price/state, chk_paper_fill_quantity, chk_paper_fill_price,
--      chk_paper_position_side, chk_paper_position_size,
--      chk_paper_balance_event_cause, chk_paper_equity_cause,
--      chk_paper_win_rate, chk_paper_drawdown_fraction
--        ALL UNAFFECTED. Every one reads only columns of its own row other
--        than session_id.
--
--      idx_paper_orders_session_state (session_id, order_state, created_at DESC)
--      idx_paper_fills_session (session_id)
--      idx_paper_positions_session (session_id)
--      idx_paper_balance_events (session_id, occurred_at ASC)
--      idx_paper_trades (session_id, closed_at DESC)
--      idx_paper_equity (session_id, series_index, taken_at ASC)
--      idx_paper_metrics_session (session_id, computed_at DESC)
--        ALL UNAFFECTED AND STILL USABLE. A PostgreSQL B-tree index stores
--        NULL keys, and IS NULL is an indexable condition on a B-tree, so a
--        default-account read that filters session_id IS NULL can still be
--        served by these rather than falling back to a sequential scan. No
--        companion partial index is added for them, because adding one would
--        be an index nothing needs: the default-account reads
--        paper_repository issues are scoped by user_id and account_id, which
--        idx_paper_*_user and idx_paper_orders_account already serve, and
--        design.md § "WHY THE CHILD READS ARE SCOPED BY account_id" is
--        explicit that the account, not the session, is the identity a
--        position hangs from.
--
--      idx_paper_sessions_user, idx_paper_sessions_running,
--      idx_paper_accounts_user
--        UNAFFECTED. On tables this file does not alter.
--
--      Row-level security, every policy, every GRANT and every REVOKE
--        UNTOUCHED. This file contains no ALTER ... ENABLE ROW LEVEL
--        SECURITY, no CREATE POLICY, no GRANT and no REVOKE. Section 5
--        asserts that RLS is still enabled on all seven relaxed tables, that
--        each still carries its owner policy and its service-role policy, and
--        that the owner policy still reads auth.uid() = user_id - because
--        "relaxing session_id must not relax the tenant scope" is the one
--        thing this file must not have done.
--
-- WHAT THIS FILE DOES NOT DO
--   * It does not create a table, a column, a policy or a grant.
--   * It does not write, move or remove a single row of data. There is no
--     INSERT, no UPDATE of an existing row and no row removal anywhere in it.
--   * It does not back-fill session_id on any existing row. Rows written
--     before this file all carry a non-null session_id (they had to), and they
--     keep it.
--   * It does not touch public.paper_events or public.paper_market_events.
--   * It does not touch public.paper_accounts, public.paper_sessions or the
--     two transition seed tables.
--   * It touches no marketplace, subscription, listing or billing relation at
--     all. Every object it reads or alters is a paper_* one.
--   * It does not edit 006 through 012. A new file is the signal, because
--     these migrations are applied BY HAND per file with nothing recording
--     which files an environment has run - editing a file an operator may
--     already have applied leaves no signal that it changed, and the new
--     statement would simply never run. Same reasoning as 006 through 012
--     each state in their own headers.
--
-- SAFETY (Requirement 24.7)
--   Additive only. ALTER COLUMN ... DROP NOT NULL removes a restriction, not
--   data: every row that satisfied the column before still satisfies it after,
--   which is why it is non-destructive and why it needs no table scan.
--   CREATE UNIQUE INDEX IF NOT EXISTS adds. CREATE OR REPLACE FUNCTION
--   replaces a body in place. There is no table removal, no column removal, no
--   constraint removal, no row removal, no emptying of a table and no rename
--   in this file.
--
-- IDEMPOTENCY (Requirements 24.7, 24.8, 24.9)
--   Every relaxation in section 1 is behind a pg_attribute.attnotnull check,
--   so a second application performs no ALTER at all. The index in section 2
--   is CREATE UNIQUE INDEX IF NOT EXISTS. The function in section 3 is CREATE
--   OR REPLACE with a fixed body. Sections 0, 4 and 5 read catalogues and
--   raise; none of them writes. A second application changes nothing and
--   raises nothing.
--
-- ORDERING
--   Depends on 009_paper_trading.sql and on nothing else. It has no
--   relationship to 010, 011 or 012 and may be applied before or after any of
--   them. If 009 has not been applied, section 0 refuses with a readable
--   message naming it rather than letting an ALTER fail with a bare
--   undefined_table.
--
-- ONE TRANSACTION
--   Either all seven columns are nullable, the companion unique index exists
--   and the append-only guard has been tightened, or none of it happened. A
--   database in which session_id is nullable but uq_paper_order_idem has no
--   companion - an order table that silently stops de-duplicating - is never
--   visible to a session.

BEGIN;

-- ==========================================================================
-- SECTION 0 - preflight
-- ==========================================================================
-- Read-only. Establishes that 009 is applied and that the premise of this
-- file - a nullable paper_accounts.session_id, i.e. an actual default account
-- - holds, then records the "before" facts section 5 compares against.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    target          TEXT;
    missing         TEXT := NULL;
    notnull_before  INTEGER := 0;
BEGIN
    -- 0a) Every relation this file reads or alters must exist.
    FOREACH target IN ARRAY ARRAY[
        'public.paper_sessions',
        'public.paper_accounts',
        'public.paper_orders',
        'public.paper_fills',
        'public.paper_positions',
        'public.paper_balance_events',
        'public.paper_trades',
        'public.paper_equity_snapshots',
        'public.paper_metrics',
        'public.paper_events',
        'public.paper_market_events'
    ] LOOP
        IF to_regclass(target) IS NULL THEN
            missing := coalesce(missing || ', ', '') || target;
        END IF;
    END LOOP;

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            '013 precondition failed: relation(s) % do not exist, so '
            'backend_app/migrations/009_paper_trading.sql has not been '
            'applied. This file only relaxes a NOT NULL on seven columns 009 '
            'creates and adds one index; it deliberately creates no table, '
            'because creating one here would author a second definition of a '
            'table 009 owns.', missing;
    END IF;

    -- 0b) THE PREMISE. paper_accounts.session_id must be NULLABLE, because
    -- that nullable column IS the default account, and the default account is
    -- the entire reason the seven child columns are being relaxed. If it were
    -- NOT NULL there would be no default account, this file's reasoning would
    -- not apply, and relaxing the children would be a change with no purpose.
    IF EXISTS (
        SELECT 1
          FROM pg_attribute a
         WHERE a.attrelid = 'public.paper_accounts'::regclass
           AND a.attname  = 'session_id'
           AND a.attnum   > 0
           AND NOT a.attisdropped
           AND a.attnotnull
    ) THEN
        RAISE EXCEPTION
            '013 precondition failed: public.paper_accounts.session_id is NOT '
            'NULL in this database, so there is no default account (the '
            'account 009 section 4 and design.md define as the row where '
            'session_id IS NULL). This file exists only to let that account '
            'own child rows. Reconcile public.paper_accounts with '
            'backend_app/migrations/009_paper_trading.sql section 4 before '
            're-running this file.';
    END IF;

    -- 0c) uq_paper_account_default must exist, be UNIQUE and be PARTIAL. It is
    -- what makes at most one default account per (user_id, currency), and
    -- section 2's companion index keys on account_id precisely BECAUSE that
    -- singleness holds. Without it, account_id would not be a stable scope for
    -- an idempotency key.
    IF NOT EXISTS (
        SELECT 1
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
         WHERE i.indrelid    = 'public.paper_accounts'::regclass
           AND c.relname     = 'uq_paper_account_default'
           AND i.indisunique
           AND i.indpred IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            '013 precondition failed: uq_paper_account_default is absent, not '
            'unique, or not partial. It is the PARTIAL unique index on '
            '(user_id, currency) WHERE session_id IS NULL that keeps the '
            'default account single, and section 2 of this file keys its '
            'companion idempotency index on account_id because that '
            'singleness holds. Restore it from '
            'backend_app/migrations/009_paper_trading.sql section 4c.';
    END IF;

    -- 0d) uq_paper_order_idem must exist. It is the guarantee section 2
    -- reconciles; if it is already gone, this file would be adding a companion
    -- to nothing and Requirement 16.11 would already be unenforced.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_orders'::regclass
           AND conname  = 'uq_paper_order_idem'
    ) THEN
        RAISE EXCEPTION
            '013 precondition failed: constraint uq_paper_order_idem is absent '
            'from public.paper_orders, so Requirement 16.11''s per-session '
            'idempotency-key uniqueness is already unenforced. Restore it from '
            'backend_app/migrations/009_paper_trading.sql section 5 before '
            'relaxing session_id: this file adds the companion index for the '
            'session_id IS NULL case and is not a replacement for the '
            'constraint itself.';
    END IF;

    -- 0e) The append-only guard must exist, because section 3 tightens it. If
    -- it is absent, the six append-only tables are already mutable and that is
    -- a larger problem than this file addresses.
    IF to_regprocedure('public.paper_append_only_guard()') IS NULL THEN
        RAISE EXCEPTION
            '013 precondition failed: public.paper_append_only_guard() does '
            'not exist, so nothing enforces append-only immutability on '
            'paper_fills, paper_trades, paper_equity_snapshots, '
            'paper_balance_events, paper_events and paper_market_events. '
            'Apply backend_app/migrations/009_paper_trading.sql section 14e '
            'first: section 3 of this file REPLACES that function''s body to '
            'close the hole a nullable session_id would open in it, and '
            'replacing a function that is not there would install a guard 009 '
            'never attached to any table.';
    END IF;

    -- 0f) Record how many of the seven columns are NOT NULL right now, for
    -- section 5 to compare against. Transaction-local (the third argument to
    -- set_config), the same carrier 011's and 012's preflight/postflight pairs
    -- use, so nothing is left behind after COMMIT.
    SELECT count(*) INTO notnull_before
      FROM pg_attribute a
     WHERE a.attrelid IN (
               'public.paper_orders'::regclass,
               'public.paper_fills'::regclass,
               'public.paper_positions'::regclass,
               'public.paper_balance_events'::regclass,
               'public.paper_trades'::regclass,
               'public.paper_equity_snapshots'::regclass,
               'public.paper_metrics'::regclass)
       AND a.attname = 'session_id'
       AND a.attnum  > 0
       AND NOT a.attisdropped
       AND a.attnotnull;

    PERFORM set_config('aerora.m013_notnull_before', notnull_before::TEXT, true);

    RAISE NOTICE '013 preflight: 009 is applied, '
                 'paper_accounts.session_id is nullable (the default account '
                 'exists), uq_paper_account_default and uq_paper_order_idem '
                 'are in force, paper_append_only_guard() exists. % of the 7 '
                 'child session_id column(s) are NOT NULL and will be '
                 'relaxed.', notnull_before;
END $$;


-- ==========================================================================
-- SECTION 1 - the relaxation
-- ==========================================================================
-- Seven columns, one restriction removed from each. Nothing else about any of
-- them changes: the type stays UUID, the REFERENCES public.paper_sessions(id)
-- ON DELETE CASCADE stays exactly as 009 declared it (section 5 asserts that),
-- and no existing row is read, written or moved.
--
-- Each ALTER is behind a pg_attribute.attnotnull check so a second application
-- performs no ALTER and takes no ACCESS EXCLUSIVE lock. ALTER COLUMN ... DROP
-- NOT NULL is a catalogue-only change - it needs no table scan and no rewrite,
-- because removing a restriction cannot invalidate a row that already
-- satisfied it.
--
-- Idempotent: guarded on attnotnull; a no-op on a second run.
DO $$
DECLARE
    target   TEXT;
    relaxed  INTEGER := 0;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'paper_orders',
        'paper_fills',
        'paper_positions',
        'paper_balance_events',
        'paper_trades',
        'paper_equity_snapshots',
        'paper_metrics'
    ] LOOP
        IF EXISTS (
            SELECT 1
              FROM pg_attribute a
             WHERE a.attrelid = ('public.' || target)::regclass
               AND a.attname  = 'session_id'
               AND a.attnum   > 0
               AND NOT a.attisdropped
               AND a.attnotnull
        ) THEN
            EXECUTE format(
                'ALTER TABLE public.%I ALTER COLUMN session_id DROP NOT NULL',
                target);
            relaxed := relaxed + 1;
            RAISE NOTICE '013: public.%.session_id is now nullable, so a row '
                         'belonging to the default account can carry NULL '
                         'exactly as its account does.', target;
        END IF;
    END LOOP;

    IF relaxed = 0 THEN
        RAISE NOTICE '013 section 1: all 7 child session_id columns were '
                     'already nullable; nothing to relax (this file has been '
                     'applied before).';
    END IF;
END $$;


-- ==========================================================================
-- SECTION 2 - reconciling uq_paper_order_idem for the NULL-session case
-- ==========================================================================
-- THE PROBLEM SECTION 1 WOULD OTHERWISE HAVE CREATED
--   uq_paper_order_idem is UNIQUE (session_id, idempotency_key). In SQL two
--   NULLs are DISTINCT for uniqueness purposes, so once session_id may be
--   NULL the index stops de-duplicating those rows entirely: (NULL,'k') never
--   conflicts with (NULL,'k'). A retried default-account order intent would
--   insert a SECOND order, silently, with no error for the caller to notice -
--   which is exactly the double-submission Requirement 16.8 exists to prevent
--   and Property P-22 states ("exactly one order exists, and every response
--   returns that order").
--
-- WHY (account_id, idempotency_key), AND WHY THAT IS THE RIGHT SCOPE
--   Requirement 16.11 scopes an idempotency key to a Paper_Session, and
--   uq_paper_order_idem implements that. A default-account order has no
--   Paper_Session, so the question is what plays the session's role as the
--   book the key is unique within. The account does:
--
--     * paper_orders.account_id is NOT NULL (009 section 5) and stays NOT
--       NULL, so this index has no NULL-distinctness hole of its own - which
--       is the whole reason the base index failed here.
--     * uq_paper_account_default makes at most ONE default account per
--       (user_id, currency) - asserted in section 0c - so account_id resolves
--       to exactly one book, and it is the book the caller was writing to.
--     * account_id already carries the tenant: it references a paper_accounts
--       row whose user_id is NOT NULL under a row-level-security policy of
--       auth.uid() = user_id, so two users cannot share an account_id and this
--       index cannot collide across tenants.
--     * design.md § "WHY THE CHILD READS ARE SCOPED BY account_id AND NOT BY
--       session_id" already establishes the account as the identity a paper
--       child row hangs from, and uq_paper_position_open (account_id, symbol)
--       already keys on it for the same reason.
--
--   (user_id, idempotency_key) was considered and rejected: it would make one
--   key collide across a user's USD and EUR default accounts, which are two
--   distinct books with two distinct balances, so a EUR order would be
--   refused because a USD order reused its key.
--
--   (session_id, account_id, idempotency_key) as a single widened index was
--   considered and rejected: it would require altering 009's constraint, and
--   for session rows it would be strictly WEAKER than what 009 has - a key
--   would become reusable across two accounts of the same session, which
--   Requirement 16.11 does not permit.
--
-- WHY THE PREDICATE INCLUDES idempotency_key IS NOT NULL
--   Because idempotency_key is nullable (009: chk_paper_order_idem_len admits
--   NULL - an order placed without a key requests no de-duplication), and the
--   base uq_paper_order_idem lets any number of NULL keys coexist for the same
--   session by the same NULL-distinctness. Excluding them here keeps the two
--   indexes semantically identical rather than accidentally stricter, and
--   keeps this index holding only the rows it can actually arbitrate.
--
-- WHAT THIS INDEX ALSO SERVES
--   paper_repository.probe_idempotency_key's default-account path, which
--   filters user_id, account_id, session_id IS NULL and idempotency_key. This
--   index's own key and predicate answer that read directly.
--
-- Idempotent: CREATE UNIQUE INDEX IF NOT EXISTS. Not created CONCURRENTLY,
-- because CREATE INDEX CONCURRENTLY cannot run inside a transaction block and
-- this file is one transaction; paper_orders is a new table in every
-- environment that has 009, so the brief lock is on a table with few or no
-- rows.
CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_order_idem_default
    ON public.paper_orders (account_id, idempotency_key)
    WHERE session_id IS NULL AND idempotency_key IS NOT NULL;

COMMENT ON INDEX public.uq_paper_order_idem_default IS
    'Requirements 16.8, 16.11, P-22 for the DEFAULT ACCOUNT. The companion to '
    'uq_paper_order_idem UNIQUE (session_id, idempotency_key), which cannot '
    'de-duplicate a default-account order because SQL treats two NULL '
    'session_ids as distinct. PARTIAL on session_id IS NULL so it arbitrates '
    'exactly the rows the base index lets through, and keyed on account_id '
    'because the account is the book a default-account key is unique within: '
    'account_id is NOT NULL, uq_paper_account_default makes it resolve to one '
    'default account per (user_id, currency), and it carries the tenant. Added '
    'by backend_app/migrations/013_paper_default_account_children.sql.';


-- ==========================================================================
-- SECTION 3 - reconciling public.paper_append_only_guard()
-- ==========================================================================
-- THE PROBLEM SECTION 1 WOULD OTHERWISE HAVE CREATED
--   009 section 14e's guard refuses every UPDATE on the six append-only
--   tables, and refuses a row removal only WHILE THE PARENT SESSION ROW STILL
--   EXISTS - the exemption that lets the declared ON DELETE CASCADE from
--   paper_sessions actually run, since PostgreSQL performs a referential
--   cascade as an AFTER trigger on the parent and the parent row is therefore
--   already gone by the time the child's BEFORE trigger fires.
--
--   It resolves that parent with
--
--       SELECT EXISTS (SELECT 1 FROM public.paper_sessions s
--                       WHERE s.id = OLD.session_id)
--
--   and nothing equals NULL. So for a default-account row that EXISTS is
--   FALSE, the guard concludes "the parent is already gone, this must be a
--   cascade", and lets the row go. Requirement 18.13's replayability and the
--   append-only property design.md gives paper_fills, paper_trades,
--   paper_equity_snapshots and paper_balance_events would have held for
--   session rows and silently not held for default-account rows.
--
-- THE FIX, AND WHY IT IS SHAPED THIS WAY
--   The exemption's real question is not "does the session still exist" but
--   "is this removal the tail of a declared cascade, or a request to rewrite
--   history". For a session row the session IS the parent, so the existing
--   test is right and is kept unchanged. For a row whose session_id IS NULL
--   the parent is whatever else the row references, and 009 gives a different
--   answer per table:
--
--     paper_fills            order_id NOT NULL -> public.paper_orders
--                            ON DELETE CASCADE. A default-account fill is
--                            reached by removing its order (itself reachable
--                            by removing its account), so the parent to test
--                            is the order.
--     paper_trades           account_id NOT NULL -> public.paper_accounts
--     paper_balance_events   account_id NOT NULL -> public.paper_accounts
--                            ON DELETE CASCADE. A default-account trade or
--                            balance event is reached by removing its
--                            account, so the parent to test is the account.
--     paper_equity_snapshots session_id is its ONLY foreign key - 009 gives it
--                            no account_id and no order_id. With session_id
--                            NULL it has no parent at all, so NO cascade can
--                            ever produce this removal and it is refused
--                            unconditionally. That is the strictest possible
--                            answer and it is the correct one.
--     paper_events           session_id stays NOT NULL (this file does not
--     paper_market_events    relax them), so neither can reach the NULL
--                            branch. The branch below still answers safely
--                            for them - refuse - rather than relying on that.
--
--   The result: a direct removal of a default-account fill, trade, balance
--   event or equity snapshot is REFUSED with 23514 exactly as a session row's
--   would be, and a removal that is genuinely the tail of a declared cascade
--   still passes.
--
-- THIS IS THE ONLY OBJECT 009 OWNS THAT THIS FILE TOUCHES
--   It is touched because section 1 would otherwise have broken it, and it is
--   touched with CREATE OR REPLACE: no trigger is detached, no function is
--   removed, and the six triggers 009 attached continue to point at this same
--   name. Section 5 asserts the UPDATE refusal is still unconditional, that
--   the session-parent branch is still present, that the new NULL branch is
--   present, and that all six triggers are still attached - so this
--   replacement cannot have quietly become a relaxation.
--
-- 23514 (check_violation) as 009 chose, so the service layer translates by
-- SQLSTATE rather than by parsing English.
--
-- Idempotent: CREATE OR REPLACE FUNCTION with a fixed body.
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

    -- TG_OP is a row removal from here on.
    IF OLD.session_id IS NOT NULL THEN
        -- A Paper_Session row: the session is the parent, unchanged from 009.
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

        -- The parent session is already gone, so this is the declared
        -- ON DELETE CASCADE and not a request to mutate history.
        RETURN OLD;
    END IF;

    -- session_id IS NULL: a DEFAULT ACCOUNT row. Resolve the parent it
    -- actually has, per table, instead of asking paper_sessions about NULL -
    -- which would answer "no parent" for every such row and exempt every
    -- removal.
    --
    -- The branch is on TG_TABLE_NAME FIRST, and that ordering is load-bearing:
    -- OLD.order_id does not exist on paper_trades and OLD.account_id does not
    -- exist on paper_fills. PL/pgSQL prepares an expression's plan lazily, on
    -- the first execution of that expression, so a branch never taken for a
    -- given table never resolves its column reference. One function over six
    -- tables with three different parent columns is only expressible this way
    -- short of writing three functions, and three functions would be three
    -- places for the UPDATE refusal above to drift apart.
    IF TG_TABLE_NAME = 'paper_fills' THEN
        SELECT EXISTS (
            SELECT 1 FROM public.paper_orders o
             WHERE o.id = OLD.order_id
        ) INTO parent_exists;
    ELSIF TG_TABLE_NAME IN ('paper_trades', 'paper_balance_events') THEN
        SELECT EXISTS (
            SELECT 1 FROM public.paper_accounts a
             WHERE a.id = OLD.account_id
        ) INTO parent_exists;
    ELSE
        -- paper_equity_snapshots has no foreign key other than session_id, so
        -- with session_id NULL no cascade can reach it and there is no
        -- exemption to grant. paper_events and paper_market_events keep a NOT
        -- NULL session_id and cannot arrive here at all; refusing is the safe
        -- answer for any table added to the guard later without its own case.
        parent_exists := TRUE;
    END IF;

    IF parent_exists THEN
        RAISE EXCEPTION
            '% is append-only: DELETE is not permitted for a default-account '
            'row while its owning account or order still exists (row %)',
            TG_TABLE_NAME, OLD.id
            USING ERRCODE = '23514';
    END IF;

    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION public.paper_append_only_guard() IS
    'Append-only enforcement for paper_fills, paper_trades, '
    'paper_equity_snapshots, paper_events, paper_market_events and '
    'paper_balance_events (design.md Data Models). Refuses every UPDATE '
    'unconditionally. Refuses a row removal while the row''s PARENT still '
    'exists, exempting only the tail of a declared ON DELETE CASCADE - the '
    'parent being paper_sessions for a session row, and, since '
    'backend_app/migrations/013_paper_default_account_children.sql, '
    'paper_orders (paper_fills) or paper_accounts (paper_trades, '
    'paper_balance_events) for a default-account row whose session_id IS '
    'NULL, with paper_equity_snapshots refused unconditionally because '
    'session_id is its only foreign key. Before 013 the NULL case resolved '
    'no parent and every default-account row was silently removable. '
    'ERRCODE 23514 so the service layer translates by SQLSTATE.';


-- ==========================================================================
-- SECTION 4 - column comments recording what NULL now means
-- ==========================================================================
-- COMMENT replaces, so this is idempotent. These exist so the next person to
-- read the schema learns what a NULL session_id means from the schema itself
-- rather than from this file.
DO $$
DECLARE
    target TEXT;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'paper_orders',
        'paper_fills',
        'paper_positions',
        'paper_balance_events',
        'paper_trades',
        'paper_equity_snapshots',
        'paper_metrics'
    ] LOOP
        EXECUTE format(
            'COMMENT ON COLUMN public.%I.session_id IS %L',
            target,
            'The Paper_Session this row belongs to, ON DELETE CASCADE - or '
            'NULL when it belongs to the DEFAULT ACCOUNT (paper_accounts '
            'where session_id IS NULL, the account the six existing '
            '/api/paper/* endpoints serve). Relaxed from NOT NULL by '
            'backend_app/migrations/013_paper_default_account_children.sql: a '
            'default account has no Paper_Session, and a synthetic one would '
            'have needed nine fabricated NOT NULL values (Requirement 28.3). '
            'user_id is NOT NULL on this table regardless and remains the '
            'row-level-security predicate and the predicate of every read and '
            'write - relaxing session_id does not relax the tenant scope.');
    END LOOP;
END $$;


-- ==========================================================================
-- SECTION 5 - postflight
-- ==========================================================================
-- Every relaxation landed, and every guarantee that was resting on the
-- relaxed column is still in force. This section is the reason the file is
-- safe to apply: it fails the transaction rather than leaving a database in
-- which session_id is nullable and something that assumed otherwise has
-- quietly stopped holding.
-- Idempotent: reads catalogues, writes nothing.
DO $$
DECLARE
    notnull_before  INTEGER := current_setting('aerora.m013_notnull_before', true)::INTEGER;
    target          TEXT;
    problems        TEXT := NULL;
    guard_body      TEXT;
    trigger_count   INTEGER;
    policy_count    INTEGER;
BEGIN
    -- 5a) All seven are nullable now.
    FOREACH target IN ARRAY ARRAY[
        'paper_orders',
        'paper_fills',
        'paper_positions',
        'paper_balance_events',
        'paper_trades',
        'paper_equity_snapshots',
        'paper_metrics'
    ] LOOP
        IF EXISTS (
            SELECT 1 FROM pg_attribute a
             WHERE a.attrelid = ('public.' || target)::regclass
               AND a.attname  = 'session_id'
               AND a.attnum   > 0
               AND NOT a.attisdropped
               AND a.attnotnull
        ) THEN
            problems := coalesce(problems || ', ', '') || target || '.session_id';
        END IF;
    END LOOP;

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            '013 postcondition failed: % still NOT NULL at the end of the '
            'transaction that relaxes it, so the default account still cannot '
            'own that kind of row.', problems;
    END IF;

    -- 5b) The foreign key to paper_sessions survived the relaxation on all
    -- seven. Dropping a NOT NULL must not have taken the reference with it: a
    -- non-null session_id must still be a session that exists, and must still
    -- cascade (Requirements 17.11, 24.1).
    problems := NULL;
    FOREACH target IN ARRAY ARRAY[
        'paper_orders',
        'paper_fills',
        'paper_positions',
        'paper_balance_events',
        'paper_trades',
        'paper_equity_snapshots',
        'paper_metrics'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint c
              JOIN pg_attribute a
                ON a.attrelid = c.conrelid
               AND a.attnum   = c.conkey[1]
             WHERE c.conrelid   = ('public.' || target)::regclass
               AND c.contype    = 'f'
               AND c.confrelid  = 'public.paper_sessions'::regclass
               AND a.attname    = 'session_id'
               AND array_length(c.conkey, 1) = 1
               AND c.confdeltype = 'c'
        ) THEN
            problems := coalesce(problems || ', ', '') || target;
        END IF;
    END LOOP;

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            '013 postcondition failed: % no longer carries a single-column '
            'foreign key from session_id to public.paper_sessions(id) with ON '
            'DELETE CASCADE. A nullable session_id must still be either NULL '
            'or a session that exists; without the reference it could name a '
            'session that never did (Requirements 17.11, 24.1).', problems;
    END IF;

    -- 5c) THE TENANT SCOPE IS UNCHANGED. user_id is still NOT NULL on all
    -- seven, and the two identity columns that carry the cascade for a
    -- default-account row - paper_orders/positions/balance_events/trades
    -- account_id, and paper_fills order_id - are still NOT NULL. This is the
    -- assertion that says relaxing session_id did not relax anything else.
    problems := NULL;
    FOREACH target IN ARRAY ARRAY[
        'paper_orders.user_id',
        'paper_fills.user_id',
        'paper_positions.user_id',
        'paper_balance_events.user_id',
        'paper_trades.user_id',
        'paper_equity_snapshots.user_id',
        'paper_metrics.user_id',
        'paper_orders.account_id',
        'paper_positions.account_id',
        'paper_balance_events.account_id',
        'paper_trades.account_id',
        'paper_fills.order_id'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
             WHERE a.attrelid = ('public.' || split_part(target, '.', 1))::regclass
               AND a.attname  = split_part(target, '.', 2)
               AND a.attnum   > 0
               AND NOT a.attisdropped
               AND a.attnotnull
        ) THEN
            problems := coalesce(problems || ', ', '') || target;
        END IF;
    END LOOP;

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            '013 postcondition failed: % is not NOT NULL. This file relaxes '
            'session_id ONLY. user_id is the row-level-security predicate and '
            'the predicate of every read and write (Requirements 21.2, 21.5), '
            'and account_id / order_id are what a default-account row cascades '
            'from now that session_id may be NULL - none of them may be '
            'nullable.', problems;
    END IF;

    -- 5d) paper_events and paper_market_events were NOT relaxed. A general
    -- relaxation would have been a different change from the one this file
    -- documents.
    problems := NULL;
    FOREACH target IN ARRAY ARRAY['paper_events', 'paper_market_events'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
             WHERE a.attrelid = ('public.' || target)::regclass
               AND a.attname  = 'session_id'
               AND a.attnum   > 0
               AND NOT a.attisdropped
               AND a.attnotnull
        ) THEN
            problems := coalesce(problems || ', ', '') || target;
        END IF;
    END LOOP;

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            '013 postcondition failed: %.session_id is nullable. This file '
            'relaxes the SEVEN accounting child tables only. paper_events and '
            'paper_market_events are the Paper_Channel and market-data logs of '
            'a Paper_Session and have no meaning without one, so their '
            'session_id stays NOT NULL.', problems;
    END IF;

    -- 5e) The count of NOT NULL child session_id columns only ever went DOWN,
    -- and it went to zero. A rise would mean this file added a restriction,
    -- which it does not do.
    IF notnull_before IS NOT NULL AND notnull_before > 7 THEN
        RAISE EXCEPTION
            '013 postcondition failed: the preflight counted % NOT NULL child '
            'session_id column(s) among 7 tables, which is impossible.',
            notnull_before;
    END IF;

    -- 5f) uq_paper_order_idem is still there AND the companion index exists,
    -- is UNIQUE, and is PARTIAL. Requirement 16.11 for a session, Requirement
    -- 16.8 / P-22 for the default account.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_orders'::regclass
           AND conname  = 'uq_paper_order_idem'
    ) THEN
        RAISE EXCEPTION
            '013 postcondition failed: uq_paper_order_idem is gone from '
            'public.paper_orders. This file adds a companion index for the '
            'session_id IS NULL case and removes nothing; without the base '
            'constraint, a session''s idempotency keys are no longer unique '
            '(Requirement 16.11).';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
         WHERE i.indrelid = 'public.paper_orders'::regclass
           AND c.relname  = 'uq_paper_order_idem_default'
           AND i.indisunique
           AND i.indpred IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            '013 postcondition failed: uq_paper_order_idem_default is absent, '
            'not unique, or not partial. Without it, a default-account order '
            'intent retried with the same idempotency key would insert a '
            'SECOND order - SQL treats two NULL session_ids as distinct, so '
            'uq_paper_order_idem cannot arbitrate those rows. That is the '
            'double submission Requirement 16.8 and Property P-22 forbid, and '
            'it would be silent.';
    END IF;

    -- The companion must key on account_id, in first position: that is what
    -- makes it a per-book uniqueness rather than a per-tenant or per-anything
    -- one. A companion keyed on something else would be a different rule from
    -- the one this file documents and justifies.
    IF NOT EXISTS (
        SELECT 1
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
          JOIN pg_attribute a
            ON a.attrelid = i.indrelid
           AND a.attnum   = i.indkey[0]
         WHERE i.indrelid = 'public.paper_orders'::regclass
           AND c.relname  = 'uq_paper_order_idem_default'
           AND a.attname  = 'account_id'
    ) THEN
        RAISE EXCEPTION
            '013 postcondition failed: uq_paper_order_idem_default does not '
            'lead on account_id. The account is the book a default-account '
            'idempotency key is unique within (see section 2); keyed on '
            'anything else it is not the guarantee this file claims.';
    END IF;

    -- 5g) uq_paper_account_default is still unique and still partial - the
    -- singleness section 2's choice of account_id depends on.
    IF NOT EXISTS (
        SELECT 1
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
         WHERE i.indrelid = 'public.paper_accounts'::regclass
           AND c.relname  = 'uq_paper_account_default'
           AND i.indisunique
           AND i.indpred IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            '013 postcondition failed: uq_paper_account_default is no longer a '
            'partial unique index, so the default account is no longer single '
            'and account_id no longer resolves to one book.';
    END IF;

    -- 5h) The two other uniqueness guarantees that key on a still-NOT NULL
    -- column, asserted present rather than assumed: a repeated fill event is
    -- still a no-op, and an account still holds one open position per symbol.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_fills'::regclass
           AND conname  = 'uq_paper_fill_event'
    ) THEN
        RAISE EXCEPTION
            '013 postcondition failed: uq_paper_fill_event is gone from '
            'public.paper_fills, so a repeated fill event would move money '
            'twice (Requirements 16.11, 18.13, P-21). It keys on order_id, '
            'which this file leaves NOT NULL, so nothing here should have '
            'affected it.';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
         WHERE i.indrelid = 'public.paper_positions'::regclass
           AND c.relname  = 'uq_paper_position_open'
           AND i.indisunique
           AND i.indpred IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            '013 postcondition failed: uq_paper_position_open is no longer a '
            'partial unique index, so an account could hold two open '
            'positions in one symbol. It keys on account_id, which this file '
            'leaves NOT NULL.';
    END IF;

    -- 5i) The two check constraints the remediation brief names, asserted
    -- present. Both read only columns of their own row, so neither could have
    -- been affected - which is exactly why asserting them is cheap and worth
    -- doing.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_orders'::regclass
           AND conname  = 'chk_paper_order_fill_bound'
    ) THEN
        RAISE EXCEPTION
            '013 postcondition failed: chk_paper_order_fill_bound is gone from '
            'public.paper_orders, so an order could be over-filled '
            '(Requirement 16.7, P-20).';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_accounts'::regclass
           AND conname  = 'chk_paper_balances_non_negative'
    ) THEN
        RAISE EXCEPTION
            '013 postcondition failed: chk_paper_balances_non_negative is gone '
            'from public.paper_accounts, so a simulated balance could go '
            'negative (Requirement 18.4, P-26).';
    END IF;

    -- 5j) The append-only guard: the UPDATE refusal is still unconditional,
    -- the session-parent branch is still there, and the NULL-session branch
    -- section 3 added is there too. Without the third, every default-account
    -- append-only row is silently removable; without the second, a session
    -- cascade would abort.
    guard_body := pg_get_functiondef(
        to_regprocedure('public.paper_append_only_guard()')::oid
    );

    IF position('append-only' IN guard_body) = 0
       OR position('UPDATE' IN guard_body) = 0 THEN
        RAISE EXCEPTION
            '013 postcondition failed: public.paper_append_only_guard() no '
            'longer refuses UPDATE, so the six append-only tables are '
            'rewritable.';
    END IF;

    IF position('paper_sessions' IN guard_body) = 0 THEN
        RAISE EXCEPTION
            '013 postcondition failed: public.paper_append_only_guard() no '
            'longer consults public.paper_sessions, so the cascade exemption '
            'for a session row is gone and removing a Paper_Session would '
            'abort.';
    END IF;

    IF position('session_id IS NULL' IN guard_body) = 0
       AND position('session_id IS NOT NULL' IN guard_body) = 0 THEN
        RAISE EXCEPTION
            '013 postcondition failed: public.paper_append_only_guard() does '
            'not distinguish a NULL session_id, so for a default-account row '
            'it resolves no parent, concludes the removal is a cascade, and '
            'lets history be erased. That is the hole section 1''s relaxation '
            'opens and section 3 exists to close.';
    END IF;

    IF position('paper_accounts' IN guard_body) = 0
       OR position('paper_orders' IN guard_body) = 0 THEN
        RAISE EXCEPTION
            '013 postcondition failed: public.paper_append_only_guard() no '
            'longer resolves a default-account row''s parent through '
            'public.paper_accounts (paper_trades, paper_balance_events) or '
            'public.paper_orders (paper_fills), so it cannot tell a declared '
            'cascade from a request to rewrite history for those rows.';
    END IF;

    -- 5k) All six append-only triggers are still attached to the guard. A
    -- correct function nothing calls enforces nothing.
    SELECT count(*) INTO trigger_count
      FROM pg_trigger t
      JOIN pg_class c     ON c.oid = t.tgrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND NOT t.tgisinternal
       AND t.tgfoid = to_regprocedure('public.paper_append_only_guard()')::oid
       AND c.relname IN (
               'paper_fills', 'paper_trades', 'paper_equity_snapshots',
               'paper_events', 'paper_market_events', 'paper_balance_events');

    IF trigger_count <> 6 THEN
        RAISE EXCEPTION
            '013 postcondition failed: % of 6 append-only trigger(s) point at '
            'public.paper_append_only_guard(). Replacing the function must not '
            'have detached any of them; without a trigger the guard enforces '
            'nothing.', trigger_count;
    END IF;

    -- 5l) Row-level security and both policies are still in force on all
    -- seven relaxed tables, and the owner policy still reads
    -- auth.uid() = user_id. Relaxing session_id must not have relaxed the
    -- tenant scope (Requirements 21.2, 21.5).
    problems := NULL;
    FOREACH target IN ARRAY ARRAY[
        'paper_orders',
        'paper_fills',
        'paper_positions',
        'paper_balance_events',
        'paper_trades',
        'paper_equity_snapshots',
        'paper_metrics'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public'
               AND c.relname = target
               AND c.relrowsecurity
        ) THEN
            problems := coalesce(problems || ', ', '') || target || ' (no RLS)';
            CONTINUE;
        END IF;

        SELECT count(*) INTO policy_count
          FROM pg_policies p
         WHERE p.schemaname = 'public'
           AND p.tablename  = target
           AND p.qual IS NOT NULL
           AND position('user_id' IN p.qual) > 0
           AND position('uid()' IN p.qual) > 0;

        IF policy_count < 1 THEN
            problems := coalesce(problems || ', ', '') || target
                        || ' (no owner policy on auth.uid() = user_id)';
        END IF;

        SELECT count(*) INTO policy_count
          FROM pg_policies p
         WHERE p.schemaname = 'public'
           AND p.tablename  = target
           AND 'service_role' = ANY (coalesce(p.roles, ARRAY[]::name[]));

        IF policy_count < 1 THEN
            problems := coalesce(problems || ', ', '') || target
                        || ' (no service-role policy)';
        END IF;
    END LOOP;

    IF problems IS NOT NULL THEN
        RAISE EXCEPTION
            '013 postcondition failed: %. Relaxing session_id must not relax '
            'the tenant scope: every relaxed table must still have row-level '
            'security enabled, an owner policy predicated on '
            'auth.uid() = user_id and a service-role policy (Requirements '
            '21.2, 21.5). This file creates and alters no policy, so a failure '
            'here means the policies were already missing.', problems;
    END IF;

    RAISE NOTICE '013 complete: all 7 accounting child session_id columns are '
                 'nullable, each still references public.paper_sessions(id) ON '
                 'DELETE CASCADE, user_id / account_id / order_id are still '
                 'NOT NULL, paper_events and paper_market_events are still NOT '
                 'NULL, uq_paper_order_idem_default de-duplicates the default '
                 'account, uq_paper_account_default / uq_paper_fill_event / '
                 'uq_paper_position_open / chk_paper_order_fill_bound / '
                 'chk_paper_balances_non_negative are in force, '
                 'paper_append_only_guard() refuses a default-account row '
                 'removal through 6 attached trigger(s), and row-level '
                 'security with both policies is untouched.';
END $$;

COMMIT;


-- ==========================================================================
-- VERIFICATION QUERIES (run by hand after applying; not part of the migration)
-- ==========================================================================
--
-- 1) The seven columns are nullable, paper_events / paper_market_events are not:
--
--    SELECT c.relname, a.attnotnull
--      FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
--     WHERE a.attname = 'session_id' AND c.relname LIKE 'paper_%'
--     ORDER BY c.relname;
--
-- 2) The companion index, with its predicate:
--
--    SELECT indexdef FROM pg_indexes
--     WHERE schemaname = 'public' AND indexname = 'uq_paper_order_idem_default';
--
-- 3) The guard distinguishes a NULL session_id:
--
--    SELECT pg_get_functiondef(to_regprocedure('public.paper_append_only_guard()')::oid);
--
-- 4) The tenant predicate is untouched:
--
--    SELECT tablename, policyname, roles, qual FROM pg_policies
--     WHERE schemaname = 'public' AND tablename LIKE 'paper_%' ORDER BY 1, 2;
