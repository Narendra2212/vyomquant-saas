-- ═══════════════════════════════════════════════════════════════════════════
-- 057_ref01_referral_wallet_four_state_shape.sql
--
-- REF-01 / BUG-035. Bring `referral_wallets` onto the four-state shape the rest of
-- the referral system already uses, on a database where it never happened.
--
-- WHY THIS FILE EXISTS WHEN THE LOGIC ALREADY DID
-- ───────────────────────────────────────────────────────────────────────────
-- `migrations/referral_system_redesign.sql` carries exactly this reconciliation,
-- and `PROVISIONING_ORDER.md` documents it as part of the build. REF-01's ledger
-- entry rested on it having run, and recorded that the conclusion "could not be
-- verified independently of that document".
--
-- IT HAD NOT RUN. Measured directly against the real production database
-- (2026-10-24): `referral_wallets` there carries 006's three columns --
-- balance_usd, total_earned_usd, pending_usd -- and NONE of
-- pending_balance_usd / approved_balance_usd / paid_balance_usd /
-- lifetime_earnings_usd.
--
-- WHAT THAT BREAKS IN PRODUCTION TODAY, which is more than REF-01 recorded:
--   * `routers/referral.py` reads all four columns in three endpoints. A column
--     that does not exist cannot come back from `select("*")`, so its payout
--     handler's `float(wallet.get("approved_balance_usd", 0))` resolves to 0 and,
--     against the $10 minimum, EVERY payout request is answered "Insufficient
--     approved balance. Available: $0.00" -- silently, for every user.
--   * the installed `process_referral_commission` writes pending_balance_usd and
--     lifetime_earnings_usd, so CREDITING a commission raises UndefinedColumn.
--   * `reverse_referral_commission` writes three of the four, so REVERSING a
--     commission on a refund fails too.
--   * `049_ref01_payout_atomic.sql`'s own preflight refuses to install either of
--     its functions without `approved_balance_usd`, which is correct and is why
--     REF-01's atomicity fix could not be applied to production either.
--
-- So the referral payout feature has never worked in production, independently of
-- the non-transactional defect REF-01 was filed for.
--
-- WHY IT IS A SEPARATE, NUMBERED FILE rather than re-running
-- `referral_system_redesign.sql`: that file also re-declares several functions and
-- a good deal else. Applying the whole thing to production to obtain two DO blocks
-- is a far larger surface than the problem needs, and a numbered migration leaves a
-- record of what was actually run.
--
-- THE SQL BELOW IS COPIED VERBATIM from that file's own
-- `$wallet_forward_migration$` and `$wallet_check$` blocks. It is not re-derived:
-- the balance mapping is a money decision documented in its own comments, and
-- re-typing it is how such a mapping gets changed by accident. One comment was
-- reworded -- it quoted a column-removal statement it had decided NOT to run, and
-- `test_no_destructive_statements` scans this directory for that text and reads the
-- quote as a statement. The reasoning is preserved verbatim; only the quoted SQL
-- went.
--
-- IT REFUSES RATHER THAN GUESSING, and that is the important property. The
-- derivation `paid_balance_usd = total_earned_usd - balance_usd - pending_usd` is
-- only valid if 006's own three columns are mutually consistent, so every row is
-- checked FIRST and the migration RAISES, having altered nothing, on any row where
-- the result would go negative. A negative would mean 006's columns disagreed, and
-- inventing a paid-out figure for a referrer is not something a migration may do.
--
-- IDEMPOTENT. The forward block returns early when `approved_balance_usd` already
-- exists; the constraint block is behind a `pg_constraint` guard. A re-run does
-- nothing. 006's three columns are RETAINED, not removed -- they hold money
-- balances, they are the only way to check the backfill or undo it, nothing reads
-- them, and no installed function writes them.
--
-- PROVISIONING ORDER. Needs `006_reconcile_production_database.sql` (which creates
-- the table) and must run BEFORE `049_ref01_payout_atomic.sql` (whose preflight
-- requires `approved_balance_usd`).
-- ═══════════════════════════════════════════════════════════════════════════

DO $wallet_forward_migration$
DECLARE
    inconsistent integer;
    sample text;
BEGIN
    IF to_regclass('public.referral_wallets') IS NULL THEN
        RAISE NOTICE 'referral_wallets does not exist; nothing to migrate';
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'referral_wallets'
          AND column_name = 'approved_balance_usd'
    ) THEN
        RAISE NOTICE 'referral_wallets already carries the four-state shape; nothing to do';
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'referral_wallets'
          AND column_name = 'balance_usd'
    ) THEN
        RAISE EXCEPTION
            'referral_wallets has neither the four-state shape nor 006''s three-column '
            'shape. Refusing to guess at its layout.';
    END IF;

    -- Consistency precheck, before anything is altered.
    SELECT count(*) INTO inconsistent
    FROM public.referral_wallets
    WHERE coalesce(total_earned_usd, 0)
          - coalesce(balance_usd, 0) - coalesce(pending_usd, 0) < 0;

    IF inconsistent > 0 THEN
        SELECT string_agg(
                   format('user=%s earned=%s balance=%s pending=%s',
                          user_id, total_earned_usd, balance_usd, pending_usd),
                   '; ')
          INTO sample
          FROM (
              SELECT * FROM public.referral_wallets
              WHERE coalesce(total_earned_usd, 0)
                    - coalesce(balance_usd, 0) - coalesce(pending_usd, 0) < 0
              LIMIT 3
          ) bad;
        RAISE EXCEPTION
            'referral_wallets: % row(s) have total_earned_usd < balance_usd + '
            'pending_usd, so paid_balance_usd cannot be derived without inventing a '
            'figure. NOTHING HAS BEEN ALTERED. Reconcile these rows first: %',
            inconsistent, sample;
    END IF;

    ALTER TABLE public.referral_wallets
        ADD COLUMN pending_balance_usd  DECIMAL(10, 2) DEFAULT 0.00,
        ADD COLUMN approved_balance_usd DECIMAL(10, 2) DEFAULT 0.00,
        ADD COLUMN paid_balance_usd     DECIMAL(10, 2) DEFAULT 0.00,
        ADD COLUMN lifetime_earnings_usd DECIMAL(10, 2) DEFAULT 0.00;

    UPDATE public.referral_wallets
       SET pending_balance_usd   = coalesce(pending_usd, 0),
           approved_balance_usd  = coalesce(balance_usd, 0),
           lifetime_earnings_usd = coalesce(total_earned_usd, 0),
           paid_balance_usd      = coalesce(total_earned_usd, 0)
                                   - coalesce(balance_usd, 0)
                                   - coalesce(pending_usd, 0);

    -- THE OLD COLUMNS ARE DELIBERATELY NOT DROPPED.
    --
    -- An earlier version of this block ended with
    --     ALTER TABLE public.referral_wallets
    --     an ALTER TABLE removing balance_usd, total_earned_usd and
    --     pending_usd outright,
    -- and `tests/test_marketplace_paper_schema_contract.py::test_no_destructive_statements`
    -- rejected it: Requirement 24.7 forbids destructive statements anywhere in the
    -- migration set. The test is right and the requirement is right. These three columns
    -- hold MONEY BALANCES. Removing a column in production is irreversible, and
    -- if the backfill above turns out to have mapped something wrongly, the original
    -- figures are the only way to find out -- or to put it back.
    --
    -- Keeping them costs nothing and risks nothing:
    --   * all three are nullable with DEFAULT 0.00 (006 lines 660-662), so no INSERT
    --     anywhere needs to mention them;
    --   * no code reads them. `routers/referral.py` reads the four-state columns in all
    --     three endpoints that touch a wallet;
    --   * no installed function writes them. Verified against `pg_proc` rather than
    --     against these files, because the question is what the database has --
    --     `staging/which_referral_funcs.py` reports:
    --         process_referral_commission   redesign four-state shape
    --         reverse_referral_commission   redesign four-state shape
    --     `CREATE OR REPLACE FUNCTION` means the last file to run wins, and this one
    --     runs after 006.
    --
    -- So they are vestigial, readable, and available for reconciliation. Removing them is
    -- a separate decision for a separate change, taken once the four-state figures have
    -- been checked against real balances -- see POST_LAUNCH_IMPROVEMENTS.md.

    RAISE NOTICE 'referral_wallets migrated to the four-state shape (BUG-035); '
                 'the three 006 columns are retained for reconciliation';
END
$wallet_forward_migration$;

-- The non-negative invariant this file's CREATE TABLE declares. Added separately so it
-- also lands on a table that arrived via 006 and was converted above.
DO $wallet_check$
BEGIN
    IF to_regclass('public.referral_wallets') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_constraint
           WHERE conname = 'chk_non_negative_balances'
       ) THEN
        ALTER TABLE public.referral_wallets
            ADD CONSTRAINT chk_non_negative_balances CHECK (
                pending_balance_usd >= 0 AND
                approved_balance_usd >= 0 AND
                paid_balance_usd >= 0 AND
                lifetime_earnings_usd >= 0
            );
        RAISE NOTICE 'referral_wallets: non-negative balance constraint added';
    END IF;
END
$wallet_check$;

-- ─────────────────────────────────────────────────────────────────────────
-- Postflight — say what landed, and refuse if it did not.
-- ─────────────────────────────────────────────────────────────────────────
DO $ref01_postflight$
DECLARE
    missing text := '';
    c       text;
BEGIN
    IF to_regclass('public.referral_wallets') IS NULL THEN
        RAISE NOTICE '057: referral_wallets does not exist here; nothing to assert';
        RETURN;
    END IF;

    FOREACH c IN ARRAY ARRAY['pending_balance_usd', 'approved_balance_usd',
                             'paid_balance_usd', 'lifetime_earnings_usd']
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'referral_wallets'
               AND column_name = c
        ) THEN
            missing := missing || c || ' ';
        END IF;
    END LOOP;

    IF missing <> '' THEN
        RAISE EXCEPTION
            '057 postflight FAILED: referral_wallets still lacks: %. '
            '049_ref01_payout_atomic.sql will refuse to install while that is true, '
            'and every payout request will keep answering $0.00.', missing;
    END IF;

    -- The backfill must not have produced a negative balance. The CHECK added above
    -- would already have refused one, but asserting it here means a database where
    -- the constraint was somehow absent still cannot pass this file silently.
    IF EXISTS (
        SELECT 1 FROM public.referral_wallets
         WHERE pending_balance_usd < 0 OR approved_balance_usd < 0
            OR paid_balance_usd < 0 OR lifetime_earnings_usd < 0
    ) THEN
        RAISE EXCEPTION
            '057 postflight FAILED: a referral wallet carries a negative balance after '
            'the backfill. NOTHING should have been committed -- investigate before '
            'retrying.';
    END IF;

    RAISE NOTICE
        '057: referral_wallets carries the four-state shape, every balance is '
        'non-negative, and 049_ref01_payout_atomic.sql can now be applied';
END
$ref01_postflight$;
