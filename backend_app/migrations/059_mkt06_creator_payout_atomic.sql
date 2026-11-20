-- backend_app/migrations/059_mkt06_creator_payout_atomic.sql
--
-- MKT-06. There is currently NO way for a marketplace strategy creator to ever
-- actually receive the revenue share `routers/library.py::creator_analytics`
-- (via `settlement_service.creator_earnings`) already computes correctly. That
-- function is READ-ONLY: it sums `owner_share_minor` over every non-reversal
-- `marketplace_settlements` row for an owner, per currency, but nothing marks any
-- of that as paid out and nothing ever creates a payout. A prior pass in this same
-- codebase deleted a dashboard label that falsely claimed "Monthly auto-transfer
-- (Stripe Connect)" specifically because -- its own comment, 008's section
-- preserved above this file in the same tree -- "no payout mechanism exists in
-- this system." This file is that mechanism, built on the REF-01 template
-- (`049_ref01_payout_atomic.sql`) for the structurally identical referral-payout
-- problem.
--
-- THE PRODUCT SHAPE: WEEKLY, PLATFORM-INITIATED, MANUALLY SETTLED
-- -----------------------------------------------------------------------------
-- Unlike a referral payout (the creator asks whenever they want), a creator
-- payout must be CREATED automatically, on a weekly schedule, by the platform --
-- `backend_app/core/creator_payout_scheduler.py` is the caller of this file's
-- creation function, one call per creator per sweep. Settlement -- the admin
-- marking a payout `completed` or `rejected` after actually transferring money
-- OUTSIDE this system (bank/UPI/PayPal) -- stays exactly analogous to
-- `settle_referral_payout`: manual and admin-attested. THIS FILE DOES NOT MOVE
-- MONEY VIA ANY PAYMENT RAIL. There is no RazorpayX Payouts or Stripe Connect
-- integration anywhere in this codebase, for creator payouts any more than for
-- referral payouts, and this migration does not add one -- building a different
-- answer here than REF-01 built for the same underlying problem would need a
-- product reason this task does not have, and this sandbox has no live
-- Razorpay/Stripe credentials to verify an automated transfer against even if it
-- wanted to.
--
-- WHY A SEPARATE CLAIMS TABLE, NOT A COLUMN ON marketplace_settlements ITSELF
-- -----------------------------------------------------------------------------
-- The first version of this file added a nullable `claimed_by_payout_id` column
-- directly to `marketplace_settlements` and UPDATEd it when a payout claimed a
-- row. That is unreachable: `marketplace_settlement_append_only_guard`
-- (`008_marketplace_settlement.sql`'s own trigger, confirmed by `pg_get_
-- functiondef` against this session's disposable Postgres replica rather than
-- assumed from the migration file alone) raises `23514` on EVERY UPDATE to this
-- table, unconditionally -- Requirement 10.8's "never modify a persisted
-- Settlement_Record", enforced at the database, not merely documented. A column
-- on that table that this file's own function tried to SET would therefore fail
-- on its very first real call. So the claim boundary lives in a NEW table
-- instead, `marketplace_creator_payout_claims` -- one row per (settlement,
-- payout) pair, INSERTed when a payout claims a settlement and DELETEd (not
-- UPDATEd) when a rejection releases it. This table carries no append-only
-- trigger of its own: it is bookkeeping about settlements, not the settlement
-- ledger itself, and REF-01's own template already establishes that a claims/
-- obligations side table is this codebase's accepted pattern for tracking
-- ledger-row state without mutating the ledger (see `029_referral_obligations.sql`
-- for the referral commission ledger's own version of the same idea).
--
-- THE IDEMPOTENT "NOT YET PAID OUT" BOUNDARY
-- -----------------------------------------------------------------------------
-- A settlement row is "unclaimed" (payable) exactly when no row in
-- `marketplace_creator_payout_claims` references its id. "This owner's unpaid
-- balance" is therefore always exactly `creator_earnings`'s own formula (sum of
-- `owner_share_minor` over non-reversal rows minus the sum over reversal rows,
-- per currency) restricted to settlements with no claim row -- the same ledger,
-- the same sign convention, a narrower WHERE clause (a `NOT EXISTS` against the
-- claims table rather than a predicate on the ledger table itself). A settlement
-- can have at most one claim at a time (`UNIQUE (settlement_id)` on the claims
-- table), so the same settlement can never be counted into two different weekly
-- payouts: the moment it is claimed by payout A it drops out of every later
-- sweep's unclaimed sum for this owner, until and unless payout A is rejected,
-- which deletes its claim rows atomically with the rejection itself -- see
-- SECTION 3's own comment for why that is the single most important correctness
-- property this file has to get right.
--
-- WHY A SINGLE SECURITY DEFINER FUNCTION, NOT N POSTGREST STATEMENTS
-- -----------------------------------------------------------------------------
-- Same discipline REF-01 already applies: "compute this creator's unpaid
-- balance, create one payout row for it, claim exactly the settlements that
-- produced it" is one `create_creator_payout` call, one transaction. If the
-- claiming INSERT failed for any reason, a payout row would exist with no
-- claimed settlements behind it -- double-counted money the instant the next
-- sweep ran -- so the payout INSERT and the claim INSERTs either all happen or
-- none do.
--
-- WHY `pg_advisory_xact_lock`, NOT `SELECT ... FOR UPDATE` ON A SINGLE ROW
-- -----------------------------------------------------------------------------
-- REF-01's functions lock a single `referral_wallets` row -- one row per user,
-- trivial to `SELECT ... FOR UPDATE`. A creator's unpaid balance here is a SUM
-- over however many `marketplace_settlements` rows happen to be unclaimed, one
-- query that can race with a concurrent call computing the SAME sum before
-- either one's claiming INSERTs have run. `pg_advisory_xact_lock(hashtextextended
-- (p_owner_id::text, 0))` serialises the whole function body per owner_id: a
-- second concurrent call for the SAME creator blocks until the first commits (the
-- lock is released automatically at transaction end), by which point the first
-- call's claim rows already exist for every settlement it summed -- so the
-- second call's own `NOT EXISTS` sees nothing left to claim and creates nothing.
-- This is the exact leader-election-adjacent guarantee the scheduler's own Redis
-- lease (see `creator_payout_scheduler.py`) provides at the PROCESS level; this
-- lock provides the matching guarantee at the DATABASE level, so the property
-- holds even if the Redis lease were ever bypassed or two different database
-- clients called this function directly. The exact settlement ids a call is
-- about to claim are collected in the SAME query that computes the sum
-- (`array_agg(s.id)` alongside the `sum(...)`), so there is no separate
-- "recompute which rows to claim" query that could see a different, newer set of
-- unclaimed rows than the one the sum was based on.
--
-- CURRENCIES ARE NEVER SUMMED TOGETHER
-- -----------------------------------------------------------------------------
-- `settlement_service.creator_earnings` groups by `currency` and returns one
-- `EarningsTotals` per currency specifically because Requirement 10.7 (its own
-- docstring) forbids combining Settlement_Records of different currencies into
-- one total. This file's payout creation groups by currency the same way: the
-- loop below iterates once per currency the owner has an unclaimed balance in and
-- inserts (at most) one `marketplace_creator_payouts` row PER CURRENCY, never one
-- row covering two currencies' money.
--
-- Additive to `marketplace_settlements` (no column, no constraint, no trigger
-- change on that table at all -- it stays exactly as 008/050 left it). The two
-- new tables are append-ONLY in spirit for payouts (no UPDATE path exists for a
-- terminal payout) but the claims table permits DELETE, which is its entire
-- purpose: releasing a claim on rejection, not rewriting a financial record.
-- REGRESSION: tests/test_mkt06_creator_payout_atomic_postgres.py

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 0 — PREFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('public.marketplace_settlements') IS NULL THEN
        RAISE EXCEPTION
            '051 precondition failed: public.marketplace_settlements is absent. '
            'Provision the marketplace settlement ledger first '
            '(backend_app/migrations/008_marketplace_settlement.sql).';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'marketplace_settlements'
           AND column_name = 'owner_share_minor'
    ) THEN
        RAISE EXCEPTION
            '051 REFUSED: public.marketplace_settlements has no owner_share_minor '
            'column on this database. settlement_service.creator_earnings reads '
            'exactly that column, and this migration''s claiming function sums the '
            'same one -- installing it anyway would create a function that fails '
            'on every real call rather than one that fails here, loudly, naming '
            'the gap.';
    END IF;

    RAISE NOTICE '051 preflight: marketplace_settlements.owner_share_minor is present';
END
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1 — THE PAYOUT TABLE
-- ─────────────────────────────────────────────────────────────────────────────
-- Column set mirrors `referral_payouts` (006_reconcile_production_database.sql)
-- as closely as this ledger's own money representation allows: `amount_minor` is
-- an exact integer number of Minor_Units (matching `marketplace_settlements`'s
-- own money columns, not `referral_payouts`'s `DECIMAL(10,2)` -- Requirement
-- 10.3 forbids a fractional-Minor_Unit, binary-floating-point money column on
-- this ledger, and that requirement does not stop applying just because this is
-- a new table), `currency` travels with the amount rather than being implied by
-- a field name, `status` reuses the exact pending/completed/rejected vocabulary
-- `referral_payouts` already uses, and `period_start`/`period_end` name the week
-- this payout covers for display/audit, NOT as the row-selection predicate (see
-- the file header: selection is by the claims table, not by timestamp range, so
-- a settlement that could not be claimed in one week because its running total
-- was still negative is still claimable in a later week once it turns positive,
-- rather than being permanently excluded by a date window it missed).
CREATE TABLE IF NOT EXISTS public.marketplace_creator_payouts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id      UUID NOT NULL,
    amount_minor  BIGINT NOT NULL,
    currency      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    period_start  TIMESTAMPTZ NOT NULL,
    period_end    TIMESTAMPTZ NOT NULL,
    reference     TEXT,
    reason        TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at    TIMESTAMPTZ,

    CONSTRAINT chk_creator_payout_amount_positive CHECK (amount_minor > 0),
    CONSTRAINT chk_creator_payout_currency CHECK (currency IN ('USD', 'INR')),
    CONSTRAINT chk_creator_payout_status CHECK (
        status IN ('pending', 'completed', 'rejected')),
    CONSTRAINT chk_creator_payout_period CHECK (period_end >= period_start)
);

CREATE INDEX IF NOT EXISTS idx_marketplace_creator_payouts_owner
    ON public.marketplace_creator_payouts(owner_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_creator_payouts_status
    ON public.marketplace_creator_payouts(status);

COMMENT ON TABLE public.marketplace_creator_payouts IS
    'MKT-06. One row per creator per currency per weekly sweep that found a '
    'positive unpaid balance. Created only by public.create_creator_payout '
    '(SECURITY DEFINER, called from creator_payout_scheduler.py); settled only by '
    'public.settle_creator_payout (admin-only, mirrors settle_referral_payout). '
    'NEVER itself moves money -- completed records a human operator''s manual '
    'transfer attestation only, exactly like referral_payouts.';

ALTER TABLE public.marketplace_creator_payouts ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "creator_payouts_owner_read" ON public.marketplace_creator_payouts;
    DROP POLICY IF EXISTS "creator_payouts_service" ON public.marketplace_creator_payouts;

    -- Owner-read-only, deliberately narrower than referral_payouts' own
    -- "FOR ALL ... USING (user_id = auth.uid())" policy: nothing in this design
    -- lets a creator insert or update their own payout row directly (creation is
    -- platform-initiated via the scheduler, settlement is admin-only), so there
    -- is no legitimate direct write for `authenticated` to have at all.
    CREATE POLICY "creator_payouts_owner_read" ON public.marketplace_creator_payouts
        FOR SELECT
        TO authenticated
        USING (owner_id = auth.uid());

    CREATE POLICY "creator_payouts_service" ON public.marketplace_creator_payouts
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2 — THE CLAIMS TABLE (the idempotent "not yet paid out" boundary)
-- ─────────────────────────────────────────────────────────────────────────────
-- `UNIQUE (settlement_id)`: a settlement may be claimed by AT MOST ONE payout at
-- a time. `ON DELETE CASCADE` from `marketplace_creator_payouts`: deleting a
-- payout (nothing in this file does that, but an operator could by hand) frees
-- its claims rather than leaving a dangling reference. `ON DELETE RESTRICT` to
-- `marketplace_settlements`: the append-only ledger row this claim references
-- must still exist for the claim to mean anything (mirrors
-- `marketplace_settlements.subscription_id ... ON DELETE RESTRICT`'s own
-- reasoning in 008).
CREATE TABLE IF NOT EXISTS public.marketplace_creator_payout_claims (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payout_id      UUID NOT NULL REFERENCES public.marketplace_creator_payouts(id)
                       ON DELETE CASCADE,
    settlement_id  UUID NOT NULL REFERENCES public.marketplace_settlements(id)
                       ON DELETE RESTRICT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_creator_payout_claim_settlement UNIQUE (settlement_id)
);

CREATE INDEX IF NOT EXISTS idx_marketplace_creator_payout_claims_payout
    ON public.marketplace_creator_payout_claims(payout_id);

COMMENT ON TABLE public.marketplace_creator_payout_claims IS
    'MKT-06. Which marketplace_settlements row is accounted for in which payout. '
    'A row here means "claimed, not payable again"; its absence means "payable". '
    'INSERTed by create_creator_payout, DELETEd (never UPDATEd) by '
    'settle_creator_payout on a REJECTED settlement -- the inverse of the claim '
    'step, making the referenced settlement payable again in the next weekly '
    'sweep. This table exists, rather than a column on marketplace_settlements '
    'itself, because that table''s own append-only trigger '
    '(marketplace_settlement_append_only_guard) raises on every UPDATE -- see '
    'this file''s own header.';

ALTER TABLE public.marketplace_creator_payout_claims ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "creator_payout_claims_service" ON public.marketplace_creator_payout_claims;

    -- Service-role only: this is internal bookkeeping about the ledger, not a
    -- figure any creator-facing response reads directly (the creator-facing
    -- history reads marketplace_creator_payouts only).
    CREATE POLICY "creator_payout_claims_service" ON public.marketplace_creator_payout_claims
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2b — THE SWEEP LOG (durable "when did the weekly sweep last run")
-- ─────────────────────────────────────────────────────────────────────────────
-- `creator_payout_scheduler.py` needs to know, ACROSS PROCESS RESTARTS, whether
-- seven days have passed since the weekly sweep last completed -- an in-memory
-- timestamp (the pattern `expiry_sweep.py`'s own `_last_run_at` module global
-- uses) is lost on every restart, which would either re-run the sweep
-- immediately after every deploy (harmless here, since `create_creator_payout`
-- is itself idempotent -- see SECTION 3 -- but wasteful) or, worse, never
-- recover a genuinely missed cycle if the in-memory clock were ever read the
-- other way. One row per completed sweep, in real Postgres, answers "when did
-- this last succeed" durably. `finished_at IS NOT NULL` is what the scheduler
-- reads -- a sweep that started and crashed leaves a row with `finished_at`
-- still NULL, which does not count as a completion and does not push the next
-- scheduled sweep further away.
CREATE TABLE IF NOT EXISTS public.marketplace_creator_payout_sweeps (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    owners_processed  INTEGER NOT NULL DEFAULT 0,
    payouts_created   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_marketplace_creator_payout_sweeps_finished
    ON public.marketplace_creator_payout_sweeps(finished_at);

COMMENT ON TABLE public.marketplace_creator_payout_sweeps IS
    'MKT-06. One row per weekly creator-payout sweep attempt. '
    'MAX(finished_at) is how creator_payout_scheduler.py recovers "when did the '
    'sweep last successfully run" across a process restart, durably, instead of '
    'an in-memory timestamp that resets on every deploy.';

ALTER TABLE public.marketplace_creator_payout_sweeps ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS "creator_payout_sweeps_service" ON public.marketplace_creator_payout_sweeps;

    CREATE POLICY "creator_payout_sweeps_service" ON public.marketplace_creator_payout_sweeps
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
END
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 3 — create_creator_payout: COMPUTE + CLAIM, ATOMIC, PER CURRENCY
-- ─────────────────────────────────────────────────────────────────────────────
--   PY001  a caller-supplied argument is missing or out of range.
-- Returns SETOF marketplace_creator_payouts -- zero rows if the owner has no
-- currency with a positive unclaimed balance (a creator with zero unpaid
-- earnings gets no payout row, by construction: the loop below only INSERTs
-- when v_total > 0), one row per currency that did.
DROP FUNCTION IF EXISTS public.create_creator_payout(uuid, timestamptz, timestamptz);

CREATE OR REPLACE FUNCTION public.create_creator_payout(
    p_owner_id uuid,
    p_period_start timestamptz,
    p_period_end timestamptz
)
RETURNS SETOF public.marketplace_creator_payouts
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
    v_currency text;
    v_total    bigint;
    v_ids      uuid[];
    v_payout   public.marketplace_creator_payouts;
BEGIN
    IF p_owner_id IS NULL THEN
        RAISE EXCEPTION 'create_creator_payout: owner_id is required'
            USING ERRCODE = 'PY001';
    END IF;
    IF p_period_start IS NULL OR p_period_end IS NULL
       OR p_period_end < p_period_start THEN
        RAISE EXCEPTION
            'create_creator_payout: period_start/period_end must be set and '
            'period_end >= period_start'
            USING ERRCODE = 'PY001';
    END IF;

    -- Serialises the WHOLE function body per owner: a second concurrent call for
    -- the SAME owner_id (two scheduler instances racing in the same cycle, or a
    -- manual re-run immediately after an automatic one) blocks here until the
    -- first commits or rolls back. See the file header for why a single-row
    -- `FOR UPDATE` -- REF-01's own mechanism -- does not apply when the thing
    -- being locked is a SUM over an unbounded set of rows rather than one row's
    -- balance column.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_owner_id::text, 0));

    -- The sum AND the exact set of settlement ids it is made of are read in the
    -- SAME query (`array_agg` alongside `sum`), so there is no separate
    -- "recompute which rows to claim" step that could see a different -- newer --
    -- unclaimed set than the one the total was based on.
    FOR v_currency, v_total, v_ids IN
        SELECT s.currency,
               sum(CASE WHEN s.is_reversal
                        THEN -s.owner_share_minor
                        ELSE s.owner_share_minor END),
               array_agg(s.id)
          FROM public.marketplace_settlements s
         WHERE s.owner_id = p_owner_id
           AND NOT EXISTS (
               SELECT 1 FROM public.marketplace_creator_payout_claims c
                WHERE c.settlement_id = s.id
           )
         GROUP BY s.currency
    LOOP
        -- A currency whose unclaimed net is zero or negative (reversals currently
        -- outweighing payments not yet claimed) gets no payout and its rows stay
        -- unclaimed, exactly like `creator_earnings` reports a negative total
        -- rather than clamping it (settlement_service.py's own documented
        -- reasoning) -- there is nothing to pay out, and claiming these rows into
        -- a payout nobody is owed would only make a future positive balance
        -- harder to reconcile.
        CONTINUE WHEN v_total IS NULL OR v_total <= 0;

        INSERT INTO public.marketplace_creator_payouts (
            owner_id, amount_minor, currency, status, period_start, period_end
        ) VALUES (
            p_owner_id, v_total, v_currency, 'pending', p_period_start, p_period_end
        )
        RETURNING * INTO v_payout;

        -- Claim exactly the ids already captured above -- not a re-query -- so
        -- nothing newly inserted between the SELECT and this INSERT can be
        -- claimed by mistake, and nothing this SELECT already saw can be missed.
        INSERT INTO public.marketplace_creator_payout_claims (payout_id, settlement_id)
        SELECT v_payout.id, unnest(v_ids);

        RETURN NEXT v_payout;
    END LOOP;

    RETURN;
END
$fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 4 — settle_creator_payout: pending -> completed/rejected, ATOMIC
-- ─────────────────────────────────────────────────────────────────────────────
--   PY002  no such payout.
--   PY003  a caller-supplied argument is invalid (bad status, or 'completed' with
--          no reference).
--   PY004  the payout is already terminal (completed or rejected) -- 409.
-- A rejection DELETEs every claim row this payout holds -- inside the SAME
-- transaction as the status flip -- so those settlements become payable again
-- the next time `create_creator_payout` runs for this owner. This is the inverse
-- of SECTION 3's claim step and the single most important correctness property
-- this file provides: without it, a rejected payout would leave its creator's
-- real, unpaid earnings permanently invisible to every future sweep, which is a
-- worse outcome for the creator than the missing payout mechanism this migration
-- exists to fix.
DROP FUNCTION IF EXISTS public.settle_creator_payout(uuid, text, text, text);

CREATE OR REPLACE FUNCTION public.settle_creator_payout(
    p_payout_id uuid,
    p_status text,
    p_reference text DEFAULT NULL,
    p_reason text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
    v_payout public.marketplace_creator_payouts;
BEGIN
    IF p_status NOT IN ('completed', 'rejected') THEN
        RAISE EXCEPTION
            'settle_creator_payout: status must be completed or rejected, got %',
            p_status
            USING ERRCODE = 'PY003';
    END IF;

    SELECT * INTO v_payout
      FROM public.marketplace_creator_payouts
     WHERE id = p_payout_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'settle_creator_payout: no payout %', p_payout_id
            USING ERRCODE = 'PY002';
    END IF;

    IF v_payout.status <> 'pending' THEN
        RAISE EXCEPTION
            'settle_creator_payout: payout % is already % and cannot be settled '
            'again', p_payout_id, v_payout.status
            USING ERRCODE = 'PY004';
    END IF;

    IF p_status = 'completed' THEN
        IF p_reference IS NULL OR btrim(p_reference) = '' THEN
            RAISE EXCEPTION
                'settle_creator_payout: a reference is required to mark a payout '
                'completed'
                USING ERRCODE = 'PY003';
        END IF;

        UPDATE public.marketplace_creator_payouts
           SET status = 'completed', reference = p_reference,
               settled_at = now(), updated_at = now()
         WHERE id = p_payout_id
        RETURNING * INTO v_payout;
    ELSE
        UPDATE public.marketplace_creator_payouts
           SET status = 'rejected',
               reason = coalesce(p_reason, 'Not specified'),
               settled_at = now(), updated_at = now()
         WHERE id = p_payout_id
        RETURNING * INTO v_payout;

        -- THE INVERSE OF THE CLAIM. Deleting the claim rows (not the settlements
        -- themselves, and not the payout row, which stays as rejected for audit)
        -- frees every settlement this payout claimed, so the next
        -- create_creator_payout call for this owner sums it back in.
        DELETE FROM public.marketplace_creator_payout_claims
         WHERE payout_id = p_payout_id;
    END IF;

    RETURN to_jsonb(v_payout);
END
$fn$;

-- Both functions take a caller-supplied owner_id/payout_id with no auth.uid()
-- check of their own, same as REF-01's pair: `create_creator_payout` is called
-- ONLY from `creator_payout_scheduler.py`, itself a trusted backend process, not
-- from any authenticated request path, so it is service_role-only -- a browser
-- role has no legitimate reason to create a payout for ANY owner_id, including
-- its own, since creation is platform-initiated, not creator-initiated.
-- `settle_creator_payout` is operator-only, identical to `settle_referral_payout`.
REVOKE ALL ON FUNCTION public.create_creator_payout(uuid, timestamptz, timestamptz)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.create_creator_payout(uuid, timestamptz, timestamptz)
    FROM anon;
REVOKE ALL ON FUNCTION public.create_creator_payout(uuid, timestamptz, timestamptz)
    FROM authenticated;
GRANT EXECUTE ON FUNCTION public.create_creator_payout(uuid, timestamptz, timestamptz)
    TO service_role;

REVOKE ALL ON FUNCTION public.settle_creator_payout(uuid, text, text, text)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.settle_creator_payout(uuid, text, text, text)
    FROM anon;
REVOKE ALL ON FUNCTION public.settle_creator_payout(uuid, text, text, text)
    FROM authenticated;
GRANT EXECUTE ON FUNCTION public.settle_creator_payout(uuid, text, text, text)
    TO service_role;

COMMENT ON FUNCTION public.create_creator_payout(uuid, timestamptz, timestamptz) IS
    'MKT-06. Computes an owner''s unclaimed (not-yet-paid-out) balance per '
    'currency from marketplace_settlements and atomically creates one '
    'marketplace_creator_payouts row per currency with a positive balance, '
    'claiming (in marketplace_creator_payout_claims) the settlements that '
    'produced it. Serialised per owner_id via pg_advisory_xact_lock. PY001 '
    'invalid argument.';

COMMENT ON FUNCTION public.settle_creator_payout(uuid, text, text, text) IS
    'MKT-06. The pending -> completed/rejected creator payout settlement step, '
    'mirroring settle_referral_payout. A rejection deletes every claim row this '
    'payout holds, atomically with the status flip, so those settlements are '
    'summed again by the next create_creator_payout call. PY002 not found, PY003 '
    'invalid argument, PY004 already terminal. NEVER itself moves money.';

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 5 — POSTFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    n int;
BEGIN
    SELECT count(*) INTO n
      FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'public' AND p.proname = 'create_creator_payout';
    IF n <> 1 THEN
        RAISE EXCEPTION
            '051 postflight FAILED: expected exactly one create_creator_payout, '
            'found %.', n;
    END IF;

    SELECT count(*) INTO n
      FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'public' AND p.proname = 'settle_creator_payout';
    IF n <> 1 THEN
        RAISE EXCEPTION
            '051 postflight FAILED: expected exactly one settle_creator_payout, '
            'found %.', n;
    END IF;

    IF has_function_privilege('anon',
            'public.create_creator_payout(uuid,timestamptz,timestamptz)', 'EXECUTE')
       OR has_function_privilege('authenticated',
            'public.create_creator_payout(uuid,timestamptz,timestamptz)', 'EXECUTE')
    THEN
        RAISE EXCEPTION
            '051 postflight FAILED: create_creator_payout is EXECUTE-able by a '
            'browser role; creation is platform-initiated only.';
    END IF;

    IF has_function_privilege('anon',
            'public.settle_creator_payout(uuid,text,text,text)', 'EXECUTE')
       OR has_function_privilege('authenticated',
            'public.settle_creator_payout(uuid,text,text,text)', 'EXECUTE')
    THEN
        RAISE EXCEPTION
            '051 postflight FAILED: settle_creator_payout is EXECUTE-able by a '
            'browser role; it bypasses RLS for ANY owner''s payout and must be '
            'operator-only.';
    END IF;

    IF NOT has_function_privilege('service_role',
            'public.create_creator_payout(uuid,timestamptz,timestamptz)', 'EXECUTE')
       OR NOT has_function_privilege('service_role',
            'public.settle_creator_payout(uuid,text,text,text)', 'EXECUTE')
    THEN
        RAISE EXCEPTION
            '051 postflight FAILED: service_role lost EXECUTE on one of this '
            'migration''s functions.';
    END IF;

    RAISE NOTICE '051: create_creator_payout and settle_creator_payout installed; '
                 'neither is EXECUTE-able by anon or authenticated.';
END
$$;

-- Self-exercise: a creator with zero marketplace_settlements rows gets no payout
-- (empty SETOF), and the function leaves no trace of having run against a
-- nonexistent owner. Run inside a nested block whose exception is unconditionally
-- raised at the end so nothing it does persists, regardless of outcome.
DO $$
DECLARE
    v_owner uuid := gen_random_uuid();
    v_count int;
BEGIN
    SELECT count(*) INTO v_count
      FROM public.create_creator_payout(v_owner, now() - interval '7 days', now());

    IF v_count <> 0 THEN
        RAISE EXCEPTION
            '051 postflight FAILED: a creator with no settlement rows at all got % '
            'payout row(s) instead of zero.', v_count;
    END IF;

    RAISE EXCEPTION 'MIGRATION_051_SELF_TEST_ROLLBACK';
EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'MIGRATION_051_SELF_TEST_ROLLBACK' THEN
        RAISE NOTICE '051: self-test passed (an owner with no settlements gets no '
                     'payout row).';
    ELSE
        RAISE;
    END IF;
END
$$;
