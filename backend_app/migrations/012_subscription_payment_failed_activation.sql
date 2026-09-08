-- 012_subscription_payment_failed_activation.sql
--
-- PURPOSE
--   Seed the ONE transition pair ('payment_failed', 'active') into
--   public.marketplace_subscription_allowed_transitions, so that a retried
--   marketplace payment which later succeeds can actually activate the
--   Subscription it paid for.
--
--   marketplace-subscriptions-paper-trading, task 19.15 (remediation of task
--   19.2). Requirements 11.6, 11.2, 11.3, 11.14, 24.7, 24.8.
--
-- THE DEFECT THIS FILE CLOSES
--   Task 19.2 recorded a requirements decision resolving a contradiction
--   between two clauses of the same requirement set:
--
--     Requirement 11.2 enumerates twelve permitted Subscription_State
--     transitions, and gives PAYMENT_FAILED exactly one successor: PENDING.
--
--     Requirement 11.6 requires "a payment confirmed through the
--     Billing_Integration and recorded as a Settlement_Record before every
--     transition into ACTIVE, from any of PENDING, EXPIRED, CANCELLED and
--     PAYMENT_FAILED" - which names PAYMENT_FAILED as a SOURCE of a
--     transition into ACTIVE.
--
--   The resolution is 11.6, because a retried payment that later confirms
--   must activate the Subscription it paid for; refusing it leaves a
--   purchaser charged with no access.
--   backend_app/backend/marketplace/settlement_service.py implements that
--   decision - ELIGIBLE_FOR_ACTIVATION carries five statuses, the four
--   derived from Requirement 11.2's table plus 'payment_failed'.
--
--   The seed table was NOT widened with it, and the seed table is what
--   trg_subscription_transition_guard consults. The result, in production, was
--   strictly worse than refusing the retry outright: settlement_service writes
--   the marketplace_settlements row FIRST (Requirement 9.5 - the money is
--   recorded before any entitlement), and only then attempts the transition.
--   So a retried payment recorded the ledger row, credited the owner's share,
--   and then the database refused the activation with 23514 from branch 2 of
--   the guard (the missing edge - NOT the settlement branch, which passes
--   because the ledger row already exists). The subscriber paid, got nothing,
--   and the ledger and the Subscription_State disagreed permanently.
--
--   One row closes it. This file inserts exactly that row and nothing else.
--
-- A DELIBERATE, DOCUMENTED DIVERGENCE FROM REQUIREMENT 11.2's TWELVE PAIRS
--   After this file, public.marketplace_subscription_allowed_transitions holds
--   THIRTEEN pairs: Requirement 11.2's twelve, seeded by
--   008_marketplace_settlement.sql, plus this one addendum from Requirement
--   11.6. That divergence is intentional and is named in exactly three places
--   so it can never become invisible drift:
--
--     1. here, in this file's own comment;
--     2. settlement_service.ELIGIBLE_FOR_ACTIVATION's comment, the
--        application half of the same decision;
--     3. tests/test_submission_state_agreement.py, whose subscription
--        agreement test compares the DB set against
--        SUBSCRIPTION_TRANSITIONS UNION a NAMED addendum constant
--        (MIGRATION_012_SUBSCRIPTION_ADDENDUM) - so 008's seed is still
--        pinned to the twelve pairs, this pair is still visible as an
--        addition, and any FOURTEENTH pair fails the test.
--
--   008's seed is NOT edited. Requirement 24.7 forbids a destructive
--   statement, migrations here are applied BY HAND per file with nothing
--   recording which files an environment has run, and editing a file an
--   operator may already have applied leaves no signal that it changed - the
--   new statement would simply never run. A new filename is the signal. Same
--   reasoning as 006 through 011 each state in their own headers.
--
-- WHAT THIS FILE DOES NOT DO
--   * It does not weaken the settlement precondition. Branch 3 of
--     public.marketplace_subscription_guard() - "no transition into 'active'
--     without a qualifying non-reversal marketplace_settlements row" -
--     (Requirements 11.6, 11.14) is untouched, and section 2 below ASSERTS it
--     is still present in the guard body before this transaction commits. A
--     payment_failed -> active write with no matching Settlement_Record is
--     still refused, now by branch 3 instead of branch 2.
--   * It does not touch row-level security, any policy, any GRANT or REVOKE,
--     any trigger, any function, any table shape or any other table. It adds
--     one reference row to a table of platform-global reference data that
--     holds no user data.
--   * It removes nothing. There is no DELETE, no DROP, no TRUNCATE and no
--     rename in this file (Requirement 24.7). The twelve pairs 008 seeded are
--     still there afterwards, and section 2 asserts the row count only ever
--     went up.
--   * It does not mention public.marketplace_listings or
--     public.strategy_subscriptions. Requirement 1.2 keeps both dormant.
--
-- CASE
--   'payment_failed' and 'active' are LOWERCASE because these are
--   public.library_subscriptions.status values and that column is lowercase -
--   the same casing 008's seed uses, and the casing
--   subscription_state.STATUS_TEXT_FOR_STATE produces from the UPPERCASE
--   SubscriptionState members Requirement 11.1 spells.
--
-- IDEMPOTENCY (Requirements 24.8, 24.9)
--   INSERT ... ON CONFLICT (from_state, to_state) DO NOTHING against
--   pk_subscription_allowed_transitions. A second application inserts nothing
--   and raises nothing. Every other statement in the file reads catalogues or
--   raises; none of them writes.
--
-- ORDERING
--   Depends on 008_marketplace_settlement.sql ONLY - it needs the table and
--   its composite primary key to exist. It has no relationship to 009, 010 or
--   011 and may be applied before or after any of them. If 008 has not been
--   applied, section 0 refuses with a readable message rather than letting the
--   INSERT fail with a bare undefined_table.

BEGIN;

-- ==========================================================================
-- SECTION 0 - preflight
-- ==========================================================================
-- Read-only. Establishes that the table this file seeds exists, that it is
-- the table 008 created (composite key over the two state columns, which is
-- what makes ON CONFLICT idempotent at all), that 008's seed actually ran
-- (so this file is an ADDENDUM to the twelve pairs and not the whole machine
-- arriving out of order), and records the row count for section 2 to compare
-- against.
DO $$
DECLARE
    pairs_before INTEGER;
BEGIN
    IF to_regclass('public.marketplace_subscription_allowed_transitions') IS NULL THEN
        RAISE EXCEPTION
            '012 precondition failed: table '
            'public.marketplace_subscription_allowed_transitions does not '
            'exist. Apply backend_app/migrations/'
            '008_marketplace_settlement.sql (section 1) first. This file '
            'seeds ONE row into that table and deliberately does not create '
            'it: creating it here would author a second definition of a '
            'table 008 owns, and the guard function that reads it lives in '
            '008 too.';
    END IF;

    -- The composite primary key over (from_state, to_state). Without it the
    -- ON CONFLICT target below has no unique index to arbitrate on, the
    -- INSERT raises 42P10 instead of being idempotent, and the same edge
    -- could be seeded twice.
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
         WHERE c.conrelid = 'public.marketplace_subscription_allowed_transitions'::regclass
           AND c.contype  = 'p'
           AND (
               SELECT array_agg(a.attname::TEXT ORDER BY a.attname)
                 FROM unnest(c.conkey) AS k(attnum)
                 JOIN pg_attribute a
                   ON a.attrelid = c.conrelid
                  AND a.attnum   = k.attnum
           ) = ARRAY['from_state', 'to_state']
    ) THEN
        RAISE EXCEPTION
            '012 precondition failed: '
            'public.marketplace_subscription_allowed_transitions has no '
            'PRIMARY KEY over exactly (from_state, to_state), so the '
            'ON CONFLICT below cannot be idempotent and the same edge could '
            'be seeded twice. Reconcile the table with '
            'backend_app/migrations/008_marketplace_settlement.sql section 1 '
            'before re-running this file.';
    END IF;

    -- 008's seed must already be in place. ('pending','active') is the pair
    -- every marketplace payment path depends on, so its absence means the
    -- twelve-pair seed has not run and this addendum would be seeding a
    -- one-edge state machine.
    IF NOT EXISTS (
        SELECT 1 FROM public.marketplace_subscription_allowed_transitions t
         WHERE t.from_state = 'pending'
           AND t.to_state   = 'active'
    ) THEN
        RAISE EXCEPTION
            '012 precondition failed: '
            'public.marketplace_subscription_allowed_transitions does not '
            'hold (''pending'',''active''), so Requirement 11.2''s twelve-pair '
            'seed from backend_app/migrations/008_marketplace_settlement.sql '
            'has not been applied. This file is an ADDENDUM of one pair to '
            'that seed, not a replacement for it.';
    END IF;

    SELECT count(*) INTO pairs_before
      FROM public.marketplace_subscription_allowed_transitions;

    -- Transaction-local (the third argument to set_config), so the count is
    -- comparable in section 2 and leaves nothing behind after COMMIT. Same
    -- carrier 011's preflight/postflight pair uses.
    PERFORM set_config('aerora.m012_pairs_before', pairs_before::TEXT, true);

    RAISE NOTICE '012 preflight: % permitted subscription transition pair(s) '
                 'seeded before this file runs.', pairs_before;
END $$;


-- ==========================================================================
-- SECTION 1 - the one addendum pair (Requirement 11.6)
-- ==========================================================================
-- ('payment_failed', 'active'): a Subscription whose payment failed, whose
-- purchaser retried, and whose retry the Billing_Integration confirmed and
-- settlement_service recorded as a Settlement_Record.
--
-- This is the THIRTEENTH pair. Requirement 11.2's twelve are 008's, and this
-- one is Requirement 11.6's - see "A DELIBERATE, DOCUMENTED DIVERGENCE" in
-- the header. It is the only pair this file seeds, now or ever.
--
-- Idempotent: ON CONFLICT (from_state, to_state) DO NOTHING. Nothing is ever
-- removed from this table by this file.
INSERT INTO public.marketplace_subscription_allowed_transitions (from_state, to_state)
VALUES
    ('payment_failed', 'active')
ON CONFLICT (from_state, to_state) DO NOTHING;


-- ==========================================================================
-- SECTION 2 - postflight
-- ==========================================================================
-- The pair is present; the count only went up; and - the assertion that
-- matters most - the guard still refuses an activation with no confirmed
-- payment behind it. Widening the edge set must not open the payment gate.
DO $$
DECLARE
    pairs_before  INTEGER := current_setting('aerora.m012_pairs_before', true)::INTEGER;
    pairs_now     INTEGER;
    guard_body    TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.marketplace_subscription_allowed_transitions t
         WHERE t.from_state = 'payment_failed'
           AND t.to_state   = 'active'
    ) THEN
        RAISE EXCEPTION
            '012 postcondition failed: (''payment_failed'',''active'') is '
            'absent from public.marketplace_subscription_allowed_transitions '
            'at the end of the transaction that inserts it.';
    END IF;

    SELECT count(*) INTO pairs_now
      FROM public.marketplace_subscription_allowed_transitions;

    IF pairs_before IS NOT NULL AND pairs_now < pairs_before THEN
        RAISE EXCEPTION
            '012 postcondition failed: the permitted-pair count fell from % '
            'to %. This file only ever inserts; a fall means an edge was '
            'removed, which Requirement 24.7 forbids.',
            pairs_before, pairs_now;
    END IF;

    IF pairs_before IS NOT NULL AND pairs_now > pairs_before + 1 THEN
        RAISE EXCEPTION
            '012 postcondition failed: the permitted-pair count rose from % '
            'to %. This file seeds exactly ONE pair.',
            pairs_before, pairs_now;
    END IF;

    -- Requirement 11.14 / 11.6's payment gate, still in place. The guard is
    -- 008's; this file must not have replaced, dropped or bypassed it, and
    -- the settlement branch is the reason a widened edge set is safe: a
    -- payment_failed -> active write with no qualifying non-reversal
    -- marketplace_settlements row is now refused by THAT branch instead of by
    -- the missing-edge branch. If the branch were gone, this file would have
    -- turned a refusal into an unpaid activation.
    IF to_regprocedure('public.marketplace_subscription_guard()') IS NULL THEN
        RAISE EXCEPTION
            '012 postcondition failed: '
            'public.marketplace_subscription_guard() does not exist, so '
            'nothing enforces Requirement 11.3''s permitted-edge check or '
            'Requirement 11.14''s "no activation without a Settlement_Record" '
            'rule. Apply backend_app/migrations/'
            '008_marketplace_settlement.sql (section 5c) before this file: '
            'seeding a permitted edge into a table no trigger reads would '
            'record a decision nothing enforces.';
    END IF;

    -- ::oid explicitly: pg_get_functiondef takes an oid, and regprocedure is
    -- only binary-coercible to it - the cast keeps this a plain lookup on
    -- every server version rather than relying on an implicit coercion.
    guard_body := pg_get_functiondef(
        to_regprocedure('public.marketplace_subscription_guard()')::oid
    );

    IF position('marketplace_settlements' IN guard_body) = 0 THEN
        RAISE EXCEPTION
            '012 postcondition failed: '
            'public.marketplace_subscription_guard() no longer consults '
            'public.marketplace_settlements, so a transition into ''active'' '
            'would be admitted with no confirmed payment behind it '
            '(Requirements 11.6, 11.14). This file widens the PERMITTED EDGE '
            'set only; the payment gate is not negotiable. Restore the guard '
            'from backend_app/migrations/008_marketplace_settlement.sql '
            'section 5c.';
    END IF;

    IF position('is_reversal' IN guard_body) = 0 THEN
        RAISE EXCEPTION
            '012 postcondition failed: '
            'public.marketplace_subscription_guard() no longer excludes '
            'reversal rows from the settlement check, so a refund could fund '
            'an activation (Requirement 11.14). Restore the guard from '
            'backend_app/migrations/008_marketplace_settlement.sql '
            'section 5c.';
    END IF;

    RAISE NOTICE '012 complete: (''payment_failed'',''active'') is permitted '
                 '(% pair(s) now seeded - Requirement 11.2''s twelve plus '
                 'Requirement 11.6''s one addendum), and '
                 'marketplace_subscription_guard() still requires a '
                 'non-reversal marketplace_settlements row for every '
                 'transition into ''active''.', pairs_now;
END $$;

COMMIT;
