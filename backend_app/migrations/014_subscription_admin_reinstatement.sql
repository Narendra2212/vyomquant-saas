-- 014_subscription_admin_reinstatement.sql
--
-- PURPOSE
--   Admit ONE transition that the database refused until now: the
--   administrative reinstatement of a SUSPENDED Subscription -
--   'suspended' -> 'active' with the Subscription_Period UNCHANGED and no NEW
--   payment - and admit it ONLY when the period being resumed was already paid
--   for.
--
--   marketplace-subscriptions-paper-trading, task 22 remediation.
--   Requirements 11.2, 11.6, 11.9, 11.12, 11.13, 11.14, 11.17, 24.7, 24.8.
--
-- THE DEFECT THIS FILE CLOSES
--   A SUSPENDED Subscription had no settlement-free route back to ACTIVE, so a
--   purchaser suspended by an administrator had to BUY A FRESH MONTH to
--   recover access they had already paid for.
--
--     Requirement 11.2 PERMITS SUSPENDED -> ACTIVE, and
--     008_marketplace_settlement.sql seeds that edge into
--     public.marketplace_subscription_allowed_transitions. The edge was never
--     the problem.
--
--     Requirement 11.6 requires a payment confirmed through the
--     Billing_Integration and recorded as a Settlement_Record "before every
--     transition into ACTIVE, from any of PENDING, EXPIRED, CANCELLED and
--     PAYMENT_FAILED" - a list that does NOT name SUSPENDED. The two clauses
--     contradict each other: 11.2 offers the edge, 11.6 states no payment rule
--     for it.
--
--     Branch 3 of public.marketplace_subscription_guard() (008 section 5c)
--     took the stricter reading and keyed itself on NEW.status = 'active'
--     ALONE - unconditional on the source - so every route into 'active',
--     SUSPENDED included, demanded a NEW non-reversal marketplace_settlements
--     row whose settled_at was at or after the CURRENT period expiry. A
--     purchaser mid-way through a paid month, suspended for an investigation
--     that then cleared them, could only be restored by a checkout: they paid
--     twice for one month, and the month they had already bought was lost.
--
-- THE DECISION (Requirement 11.17, added by this task to resolve 11.2 vs 11.6)
--   A SUSPENSION IS A HOLD, NOT A REFUND. The period the purchaser paid for is
--   still theirs, so lifting the hold restores access WITHOUT a charge and
--   WITHOUT moving the period. Both boundaries stay exactly where the paid
--   month put them, so the purchaser gets back the REMAINING period and not a
--   fresh one - and if that period has already elapsed the reinstatement hands
--   back nothing, because entitlement_resolver.resolve compares now with
--   period_expiry on every call (Requirement 11.7) and the expiry sweep moves
--   the row to EXPIRED on its next pass (Requirement 11.8).
--
--   The decision is recorded in exactly four places so it cannot become
--   invisible drift:
--
--     1. here, in this file's own comment;
--     2. requirements.md Requirement 11.17, the clause that resolves the
--        contradiction in EARS form;
--     3. design.md -> "marketplace/subscription_state.py" and
--        "marketplace/subscription_reinstatement.py", the design half;
--     4. backend_app/backend/marketplace/subscription_reinstatement.py, the
--        application writer, and
--        tests/test_subscription_reinstatement_regression.py, which reads this
--        file off disk and asserts the relaxation is scoped.
--
-- WHAT THIS FILE DOES NOT DO - THE INVARIANT IS NOT WEAKENED
--   "No transition into 'active' without a payment behind it" (Requirements
--   11.6, 11.14) remains TRUE FOR EVERY SOURCE.
--
--     * pending -> active, expired -> active, cancelled -> active and
--       payment_failed -> active still meet 008's probe, unchanged, character
--       for character: a non-reversal marketplace_settlements row for THIS
--       subscription whose settled_at is at or after the row's CURRENT
--       period_expiry (or any non-reversal row when that expiry is NULL).
--       Section 3 PROVES it by CALLING the admission predicate with each of
--       those four sources and asserting FALSE - so each of them reaches the
--       unchanged branch - and by asserting 008's probe is still in the guard
--       body.
--
--     * suspended -> active is admitted WITHOUT A NEW settlement only when
--       BOTH of these hold:
--         (a) the period columns are NOT MOVED - period_start and
--             period_expiry are written identically to their stored values (or
--             not written at all), so the relaxation can never fund an
--             extension; and
--         (b) a non-reversal marketplace_settlements row for THIS subscription
--             was settled at or before the stored period_expiry - i.e. the
--             period being resumed was BOUGHT.
--       A Subscription that never paid has no such row and therefore cannot be
--       "reinstated" into access it never bought. A refund cannot fund it
--       either: is_reversal = FALSE, and a refunded Subscription is REFUNDED,
--       which Requirement 11.2 makes terminal.
--
--   Why no lower bound on the settlement's settled_at: settled_at is written by
--   the database clock and period_start by the application instant, so a
--   legitimate first payment can precede its own period_start by milliseconds;
--   and a renewal keeps period_start fixed while extending period_expiry, so a
--   payment for the current month can be far from the stored start. Requiring
--   settled_at >= period_start would refuse legitimate reinstatements for a
--   guarantee condition (a) already gives: the period cannot move, so no
--   payment can be re-used to buy anything.
--
--   * It does not change the permitted-edge set. ('suspended','active') was
--     already seeded by 008 from Requirement 11.2's twelve pairs, and 012
--     added Requirement 11.6's one addendum. This file inserts NO row into
--     public.marketplace_subscription_allowed_transitions; section 3 asserts
--     the pair count is EXACTLY what it was before this file ran, so
--     tests/test_submission_state_agreement.subscription_permitted_pairs_in_db()
--     and every consumer of it are unaffected (still thirteen pairs).
--   * It does not touch row level security, any policy, any GRANT or REVOKE,
--     any table shape, any trigger, any other function or any row of user
--     data. It replaces one function body in place and adds one pure
--     predicate.
--   * It removes nothing. There is no DROP, no DELETE, no TRUNCATE and no
--     rename in this file (Requirement 24.7). 006 through 013 are unedited: a
--     new filename is the only signal an operator who has already applied a
--     file will see, which is the reasoning 012 and 013 each state in their own
--     headers.
--   * It does not mention public.marketplace_listings or
--     public.strategy_subscriptions. Requirement 1.2 keeps both dormant.
--   * It moves no money and touches no money column. This path has no amount,
--     no currency arithmetic and no ledger write; Minor_Units stay integer
--     because nothing here computes one.
--
-- WHY A SEPARATE PREDICATE INSTEAD OF AN INLINE CONDITION
--   public.marketplace_subscription_reinstatement_shape(...) is a pure,
--   IMMUTABLE, seven-argument SQL function that answers one question: "is this
--   UPDATE shaped like the administrative reinstatement Requirement 11.17
--   authorises?" It reads no table, so it is safe to call from the guard and -
--   the point - it is CALLABLE FROM THE POSTFLIGHT. That turns "the relaxation
--   admits suspended and nothing else" from a claim about SQL text into an
--   executable assertion this transaction refuses to commit without. An inline
--   boolean expression inside the trigger body could only ever be checked by
--   reading the text.
--
-- CASE
--   'suspended' and 'active' are LOWERCASE because these are
--   public.library_subscriptions.status values and that column is lowercase -
--   the same casing 008's seed and 012's addendum use, and the casing
--   subscription_state.STATUS_TEXT_FOR_STATE produces.
--
-- IDEMPOTENCY (Requirements 24.8, 24.9)
--   Two CREATE OR REPLACE FUNCTION statements and nothing else that writes. A
--   second application replaces both definitions with identical text and
--   raises nothing. The trigger 008 created is NOT recreated: it already points
--   at public.marketplace_subscription_guard() by name, so replacing the
--   function body is the whole change. Section 3 asserts the trigger is still
--   attached, because a relaxed guard nobody fires would be worse than no
--   change at all.
--
-- ORDERING
--   Depends on 008_marketplace_settlement.sql ONLY: it needs the guard
--   function, its trigger, the allowed-transitions seed and
--   public.marketplace_settlements to exist. It has no relationship to 009,
--   010, 011, 012 or 013 and may be applied before or after any of them. If
--   008 has not been applied, section 0 refuses with a readable message rather
--   than authoring a guard 008 owns.

BEGIN;

-- ==========================================================================
-- SECTION 0 - preflight
-- ==========================================================================
-- Read-only. Establishes that 008 is applied - the guard function, its
-- trigger, the seed table with the ('suspended','active') edge this file makes
-- reachable, and the settlement columns both probes read - and records the
-- permitted-pair count so section 3 can assert this file changed it by zero.
DO $$
DECLARE
    pairs_before INTEGER;
BEGIN
    IF to_regprocedure('public.marketplace_subscription_guard()') IS NULL THEN
        RAISE EXCEPTION
            '014 precondition failed: '
            'public.marketplace_subscription_guard() does not exist. Apply '
            'backend_app/migrations/008_marketplace_settlement.sql '
            '(section 5c) first. This file REPLACES that function to admit one '
            'further source; authoring it here would be a second definition of '
            'a function 008 owns, and an environment that later applied 008 '
            'would silently overwrite this relaxation.';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_trigger t
          JOIN pg_class c     ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname  = 'trg_subscription_transition_guard'
           AND n.nspname = 'public'
           AND c.relname = 'library_subscriptions'
           AND NOT t.tgisinternal
    ) THEN
        RAISE EXCEPTION
            '014 precondition failed: '
            'trg_subscription_transition_guard is not attached to '
            'public.library_subscriptions, so the function this file replaces '
            'is fired by nothing. Apply '
            'backend_app/migrations/008_marketplace_settlement.sql '
            '(section 5c) first.';
    END IF;

    IF to_regclass('public.marketplace_subscription_allowed_transitions') IS NULL THEN
        RAISE EXCEPTION
            '014 precondition failed: table '
            'public.marketplace_subscription_allowed_transitions does not '
            'exist, so the guard''s permitted-edge branch has nothing to read. '
            'Apply backend_app/migrations/008_marketplace_settlement.sql '
            '(section 1) first.';
    END IF;

    -- The edge this file makes REACHABLE. It is Requirement 11.2's, seeded by
    -- 008; this file inserts nothing. Its absence would mean the twelve-pair
    -- seed has not run, and relaxing the payment gate for an edge the guard's
    -- SECOND branch still refuses would be a relaxation with no effect and a
    -- misleading record.
    IF NOT EXISTS (
        SELECT 1 FROM public.marketplace_subscription_allowed_transitions t
         WHERE t.from_state = 'suspended'
           AND t.to_state   = 'active'
    ) THEN
        RAISE EXCEPTION
            '014 precondition failed: '
            'public.marketplace_subscription_allowed_transitions does not hold '
            '(''suspended'',''active''), so Requirement 11.2''s twelve-pair seed '
            'from backend_app/migrations/008_marketplace_settlement.sql has not '
            'been applied. This file relaxes the PAYMENT gate for an edge that '
            'seed already permits; it does not seed the edge.';
    END IF;

    IF to_regclass('public.marketplace_settlements') IS NULL THEN
        RAISE EXCEPTION
            '014 precondition failed: table public.marketplace_settlements '
            'does not exist, so neither the unchanged probe nor the '
            'reinstatement probe could establish that anything was ever paid. '
            'Apply backend_app/migrations/008_marketplace_settlement.sql first.';
    END IF;

    -- The three columns both probes read. A missing column would turn every
    -- Subscription UPDATE into a 42703 instead of a transition verdict.
    IF NOT EXISTS (
        SELECT 1
          FROM pg_attribute a
         WHERE a.attrelid = 'public.marketplace_settlements'::regclass
           AND a.attname IN ('subscription_id', 'is_reversal', 'settled_at')
           AND a.attnum > 0
           AND NOT a.attisdropped
        GROUP BY a.attrelid
        HAVING count(*) = 3
    ) THEN
        RAISE EXCEPTION
            '014 precondition failed: public.marketplace_settlements is '
            'missing one of subscription_id, is_reversal, settled_at - the '
            'columns the payment gate reads. Reconcile it with '
            'backend_app/migrations/008_marketplace_settlement.sql (section 3) '
            'before re-running this file.';
    END IF;

    SELECT count(*) INTO pairs_before
      FROM public.marketplace_subscription_allowed_transitions;

    -- Transaction-local (the third argument to set_config), so the count is
    -- comparable in section 3 and leaves nothing behind after COMMIT. The same
    -- carrier 011's and 012's preflight/postflight pairs use.
    PERFORM set_config('aerora.m014_pairs_before', pairs_before::TEXT, true);

    RAISE NOTICE '014 preflight: 008 is applied; % permitted subscription '
                 'transition pair(s) seeded, and this file will seed none.',
                 pairs_before;
END $$;


-- ==========================================================================
-- SECTION 1 - the admission predicate (Requirement 11.17)
-- ==========================================================================
-- "Is this UPDATE shaped like the administrative reinstatement Requirement
-- 11.17 authorises?" Pure: no table is read, so it can be called from the
-- BEFORE UPDATE guard and from the postflight below with equal safety.
--
-- IMMUTABLE and not STABLE: the answer is a function of the seven arguments
-- alone, which lets the planner fold it and lets section 3 call it in a plain
-- SELECT. STRICT is deliberately NOT declared - a NULL period_start or
-- period_expiry is a MEANINGFUL input here (a Subscription that never had a
-- period must not be reinstatable), and a STRICT function would return NULL
-- for it, which the guard would then treat as "not the reinstatement shape"
-- only by accident of three-valued logic. The conditions below decide it
-- explicitly instead.
--
-- The five conjuncts, each carrying its own reason:
--
--   new_status = 'active'          the only target Requirement 11.6 gates.
--   old_status = 'suspended'       THE ONE RELAXED SOURCE. A single equality,
--                                  not a set: pending, expired, cancelled and
--                                  payment_failed cannot satisfy it, which is
--                                  what section 3 proves by calling this
--                                  function with each of them.
--   old_period_expiry IS NOT NULL  a Subscription with no stored period has no
--                                  paid period to resume; it needs a checkout.
--                                  (chk_ls_active_has_period would refuse the
--                                  resulting row anyway - Requirement 11.13 -
--                                  but refusing here names the real reason.)
--   new_period_expiry IS NOT
--     DISTINCT FROM
--     old_period_expiry            THE PERIOD DOES NOT MOVE. IS NOT DISTINCT
--   new_period_start  IS NOT       FROM, not =, so an UPDATE that does not
--     DISTINCT FROM                mention the column (NEW carries the stored
--     old_period_start             value) and one that writes the same value
--                                  are both recognised, and a NULL on both
--                                  sides compares equal rather than yielding
--                                  NULL. This is the conjunct that makes the
--                                  relaxation unable to fund an extension: a
--                                  caller that tried to reinstate AND move the
--                                  expiry fails this predicate and falls
--                                  through to 008's unchanged probe, which
--                                  demands a fresh payment.
--
-- CREATE OR REPLACE FUNCTION is naturally idempotent.
CREATE OR REPLACE FUNCTION public.marketplace_subscription_reinstatement_shape(
    old_status        TEXT,
    new_status        TEXT,
    old_period_start  TIMESTAMPTZ,
    new_period_start  TIMESTAMPTZ,
    old_period_expiry TIMESTAMPTZ,
    new_period_expiry TIMESTAMPTZ
)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT new_status = 'active'
       AND old_status = 'suspended'
       AND old_period_expiry IS NOT NULL
       AND new_period_expiry IS NOT DISTINCT FROM old_period_expiry
       AND new_period_start  IS NOT DISTINCT FROM old_period_start;
$$;

COMMENT ON FUNCTION public.marketplace_subscription_reinstatement_shape(
    TEXT, TEXT, TIMESTAMPTZ, TIMESTAMPTZ, TIMESTAMPTZ, TIMESTAMPTZ
) IS
    'Requirement 11.17''s admission shape: TRUE only for '
    'suspended -> active with both Subscription_Period boundaries unchanged '
    'and a stored expiry present. Pure and IMMUTABLE so '
    'marketplace_subscription_guard() can call it and '
    'backend_app/migrations/014_subscription_admin_reinstatement.sql section 3 '
    'can PROVE, by calling it, that pending, expired, cancelled and '
    'payment_failed are not admitted by it and therefore still require a '
    'confirmed payment (Requirements 11.6, 11.14).';


-- ==========================================================================
-- SECTION 2 - the guard, replaced in place (Requirements 11.3, 11.14, 11.17)
-- ==========================================================================
-- 008 section 5c's function with ONE change: branch 3 now has two arms.
-- Branches 1 and 2 are character-for-character 008's, deliberately, because
-- they are what tests/property/test_db_transition_guards.py's P-49 reads as
-- "the refusal is generic table membership naming both states".
--
--   1. A same-value write RETURNS NEW immediately. Not a transition; no state
--      changed. IS NOT DISTINCT FROM so a NULL on both sides is "unchanged".
--      This is also what lets the checkout, cancel and renewal paths move the
--      period columns without the transition table's involvement.
--
--   2. An edge absent from marketplace_subscription_allowed_transitions raises
--      23514 naming BOTH states (Requirement 11.3). Unchanged.
--
--   3. A transition INTO 'active'. TWO ARMS, and every source reaches one of
--      them:
--
--      3a. THE ADMINISTRATIVE REINSTATEMENT (Requirement 11.17). Reached only
--          when marketplace_subscription_reinstatement_shape() says this is
--          suspended -> active with an unmoved period. It still requires a
--          payment - just not a NEW one: a non-reversal marketplace_settlements
--          row for THIS subscription settled AT OR BEFORE the stored
--          period_expiry, which is the payment that BOUGHT the period being
--          resumed. A Subscription that never paid has none, so it cannot be
--          reinstated into access it never bought, and the refusal is the same
--          translatable 23514.
--
--      3b. EVERYTHING ELSE - pending, expired, cancelled, payment_failed, and
--          suspended whenever the period is being moved. 008's probe, verbatim:
--          a non-reversal row settled AT OR AFTER the CURRENT period_expiry, or
--          any non-reversal row when that expiry is NULL (the first activation
--          of Requirement 11.4). The >= comparison is what makes the check
--          non-circular: the payment that bought the period now ending was
--          settled at the START of it, so it cannot fund the next one, and a
--          direct UPDATE ... SET status='active', period_expiry = period_expiry
--          + '1 month' still fails.
--
--      is_reversal = FALSE in both arms: a refund is not a payment and must
--      never fund an activation.
--
--      THE KNOWN CONSEQUENCE 008 recorded still stands and is still NOT
--      weakened here: a renewal CONFIRMED BEFORE the current expiry (the
--      cancelled -> active path of Requirement 11.9) has settled_at <
--      OLD.period_expiry and is refused by arm 3b. Closing that case needs a
--      consumed-marker column, which is a schema decision beyond this task,
--      and arm 3a does not close it either - arm 3a requires the source to be
--      'suspended' and the period NOT to move, so it can never admit a
--      period extension.
--
--   The RLS note that still matters: both SELECTs run as the INVOKING role,
--   under row level security, because this function is deliberately NOT
--   SECURITY DEFINER - a state-machine guard running with the definer's
--   privileges would be a privilege-escalation surface. If RLS hides a
--   settlement from the invoking role the transition is REFUSED: the guard
--   fails closed, which is the safe direction.
--
--   NEW.updated_at is deliberately NOT assigned, exactly as in 008: the
--   pre-existing trg_library_subscriptions_updated_at already does that on
--   every UPDATE.
--
--   No transition-history row is written here. Requirement 11.3 requires a
--   rejected write to leave the stored values unchanged, and the history is
--   inserted by the application AFTER the UPDATE returns - so a raise here
--   means that INSERT never runs.
--
-- CREATE OR REPLACE FUNCTION is naturally idempotent. The trigger 008 created
-- resolves this function by name and needs no change.
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
        IF public.marketplace_subscription_reinstatement_shape(
               OLD.status,
               NEW.status,
               OLD.period_start,
               NEW.period_start,
               OLD.period_expiry,
               NEW.period_expiry
           ) THEN
            -- Arm 3a: Requirement 11.17's administrative reinstatement. The
            -- period being resumed must have been PAID for.
            IF NOT EXISTS (
                SELECT 1 FROM public.marketplace_settlements s
                 WHERE s.subscription_id = NEW.id
                   AND s.is_reversal = FALSE
                   AND s.settled_at <= OLD.period_expiry
            ) THEN
                RAISE EXCEPTION
                    'reinstatement % -> active requires the period being '
                    'resumed to have been paid for: no non-reversal '
                    'marketplace_settlements row for subscription % was '
                    'settled at or before the current period expiry %',
                    OLD.status, NEW.id, OLD.period_expiry
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NOT EXISTS (
            -- Arm 3b: 008's probe, unchanged, for every other source.
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

COMMENT ON FUNCTION public.marketplace_subscription_guard() IS
    'Requirements 11.3, 11.14 and 11.17''s enforcement: rejects any '
    'Subscription_State transition absent from '
    'public.marketplace_subscription_allowed_transitions, and rejects any '
    'transition into ''active'' with no qualifying non-reversal '
    'public.marketplace_settlements row. The one relaxation, authored by '
    'backend_app/migrations/014_subscription_admin_reinstatement.sql, is '
    'Requirement 11.17''s administrative reinstatement: suspended -> active '
    'with the Subscription_Period unchanged is admitted on the payment that '
    'bought the period being resumed, instead of a new one. Every other '
    'source still requires a payment settled at or after the current expiry. '
    'Deliberately NOT SECURITY DEFINER: the probes run as the invoking role '
    'under RLS, so the guard fails closed.';


-- ==========================================================================
-- SECTION 3 - postflight
-- ==========================================================================
-- Three things, and the transaction does not commit without all three:
--
--   (a) the relaxation LANDED - the guard calls the predicate, and the
--       predicate admits suspended -> active with an unmoved period;
--   (b) the relaxation is SCOPED - the predicate REFUSES pending, expired,
--       cancelled and payment_failed, and refuses a suspended row whose period
--       is being moved or which never had one. These are executable calls, not
--       readings of the SQL text;
--   (c) the payment gate SURVIVED - 008's probe is still in the guard body for
--       those four sources, reversals are still excluded, the refusal still
--       carries 23514, the trigger is still attached, and this file seeded no
--       transition pair.
DO $$
DECLARE
    pairs_before INTEGER := current_setting('aerora.m014_pairs_before', true)::INTEGER;
    pairs_now    INTEGER;
    guard_body   TEXT;
    an_expiry    TIMESTAMPTZ := '2025-06-01T00:00:00Z';
    a_start      TIMESTAMPTZ := '2025-05-01T00:00:00Z';
    source       TEXT;
BEGIN
    -- ---- (a) the relaxation landed --------------------------------------
    IF to_regprocedure(
           'public.marketplace_subscription_reinstatement_shape('
           'text,text,timestamptz,timestamptz,timestamptz,timestamptz)'
       ) IS NULL THEN
        RAISE EXCEPTION
            '014 postcondition failed: '
            'public.marketplace_subscription_reinstatement_shape(...) does not '
            'exist at the end of the transaction that creates it.';
    END IF;

    IF NOT public.marketplace_subscription_reinstatement_shape(
               'suspended', 'active', a_start, a_start, an_expiry, an_expiry) THEN
        RAISE EXCEPTION
            '014 postcondition failed: the admission predicate refuses '
            'suspended -> active with an unchanged period, so Requirement '
            '11.17''s administrative reinstatement is still impossible and a '
            'suspended purchaser must still buy a fresh month.';
    END IF;

    guard_body := pg_get_functiondef(
        to_regprocedure('public.marketplace_subscription_guard()')::oid
    );

    IF position('marketplace_subscription_reinstatement_shape' IN guard_body) = 0 THEN
        RAISE EXCEPTION
            '014 postcondition failed: '
            'public.marketplace_subscription_guard() does not call '
            'public.marketplace_subscription_reinstatement_shape(...), so the '
            'predicate this file proves things about is not the one the '
            'database consults. Re-apply this file.';
    END IF;

    -- ---- (b) the relaxation is scoped ----------------------------------
    -- The four sources Requirement 11.6 names. Each must be REFUSED by the
    -- predicate, which is what sends it to arm 3b - 008's unchanged probe.
    FOREACH source IN ARRAY ARRAY['pending', 'expired', 'cancelled', 'payment_failed']
    LOOP
        IF public.marketplace_subscription_reinstatement_shape(
               source, 'active', a_start, a_start, an_expiry, an_expiry) THEN
            RAISE EXCEPTION
                '014 postcondition failed: the admission predicate admits '
                '% -> active without a new settlement. Requirement 11.6 '
                'requires a confirmed payment before that transition, and this '
                'file relaxes the gate for ''suspended'' ONLY.', source;
        END IF;
    END LOOP;

    -- A suspended row whose period is being MOVED is not a reinstatement: it
    -- is an extension, and an extension needs a payment.
    IF public.marketplace_subscription_reinstatement_shape(
           'suspended', 'active', a_start, a_start,
           an_expiry, an_expiry + INTERVAL '1 month') THEN
        RAISE EXCEPTION
            '014 postcondition failed: the admission predicate admits a '
            'suspended -> active write that MOVES period_expiry, so an unpaid '
            'period extension would be admitted (Requirements 11.5, 11.14).';
    END IF;

    IF public.marketplace_subscription_reinstatement_shape(
           'suspended', 'active', a_start, an_expiry, an_expiry, an_expiry) THEN
        RAISE EXCEPTION
            '014 postcondition failed: the admission predicate admits a '
            'suspended -> active write that MOVES period_start, so the '
            'purchaser''s paid period could be re-issued rather than resumed '
            '(Requirement 11.17).';
    END IF;

    -- A Subscription with no stored period never bought one.
    IF public.marketplace_subscription_reinstatement_shape(
           'suspended', 'active', NULL, NULL, NULL, NULL) THEN
        RAISE EXCEPTION
            '014 postcondition failed: the admission predicate admits a '
            'suspended -> active write on a Subscription with no stored '
            'period, which has no paid period to resume (Requirements 11.13, '
            '11.17).';
    END IF;

    -- And no other target is affected at all.
    IF public.marketplace_subscription_reinstatement_shape(
           'suspended', 'expired', a_start, a_start, an_expiry, an_expiry) THEN
        RAISE EXCEPTION
            '014 postcondition failed: the admission predicate answers TRUE '
            'for a target other than ''active''; it gates one transition only.';
    END IF;

    -- ---- (c) the payment gate survived ---------------------------------
    IF position('public.marketplace_settlements' IN guard_body) = 0 THEN
        RAISE EXCEPTION
            '014 postcondition failed: '
            'public.marketplace_subscription_guard() no longer consults '
            'public.marketplace_settlements, so a transition into ''active'' '
            'would be admitted with no confirmed payment behind it '
            '(Requirements 11.6, 11.14).';
    END IF;

    -- 008's probe for pending / expired / cancelled / payment_failed: the
    -- non-circular anchor. Its absence would mean this file replaced the gate
    -- rather than adding one narrow arm beside it.
    IF position('s.settled_at >= OLD.period_expiry' IN guard_body) = 0 THEN
        RAISE EXCEPTION
            '014 postcondition failed: '
            'public.marketplace_subscription_guard() no longer requires a '
            'settlement at or after the current period expiry for the sources '
            'Requirement 11.6 names (pending, expired, cancelled, '
            'payment_failed), so the payment that bought the period now ending '
            'could fund the next one. Restore the guard from this file.';
    END IF;

    -- Arm 3a's own payment requirement.
    IF position('s.settled_at <= OLD.period_expiry' IN guard_body) = 0 THEN
        RAISE EXCEPTION
            '014 postcondition failed: the reinstatement arm does not require '
            'a settlement for the period being resumed, so a Subscription that '
            'never paid could be reinstated into access it never bought '
            '(Requirement 11.14).';
    END IF;

    IF position('s.is_reversal = FALSE' IN guard_body) = 0 THEN
        RAISE EXCEPTION
            '014 postcondition failed: '
            'public.marketplace_subscription_guard() no longer excludes '
            'reversal rows from the settlement checks, so a refund could fund '
            'an activation (Requirement 11.14).';
    END IF;

    IF position('23514' IN guard_body) = 0 THEN
        RAISE EXCEPTION
            '014 postcondition failed: the guard''s refusals no longer carry '
            'the 23514 check_violation SQLSTATE the application translates by, '
            'so a refused transition would surface as an unclassified error '
            '(Requirement 11.3).';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_trigger t
          JOIN pg_class c     ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname  = 'trg_subscription_transition_guard'
           AND n.nspname = 'public'
           AND c.relname = 'library_subscriptions'
           AND NOT t.tgisinternal
    ) THEN
        RAISE EXCEPTION
            '014 postcondition failed: '
            'trg_subscription_transition_guard is no longer attached to '
            'public.library_subscriptions, so nothing fires the guard at all.';
    END IF;

    SELECT count(*) INTO pairs_now
      FROM public.marketplace_subscription_allowed_transitions;

    IF pairs_before IS NOT NULL AND pairs_now <> pairs_before THEN
        RAISE EXCEPTION
            '014 postcondition failed: the permitted-pair count changed from % '
            'to %. This file seeds no transition pair and removes none - '
            '(''suspended'',''active'') was already permitted by Requirement '
            '11.2''s seed in 008.',
            pairs_before, pairs_now;
    END IF;

    RAISE NOTICE '014 complete: suspended -> active is admitted on the payment '
                 'that bought the period being resumed, with the period '
                 'unchanged (Requirement 11.17); pending, expired, cancelled '
                 'and payment_failed each still require a settlement at or '
                 'after the current expiry (Requirements 11.6, 11.14); % '
                 'permitted pair(s), unchanged by this file.', pairs_now;
END $$;

COMMIT;
