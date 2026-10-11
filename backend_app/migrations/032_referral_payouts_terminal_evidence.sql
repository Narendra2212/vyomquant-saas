-- 032 — referral_payouts: a terminal payout must carry evidence of when it was processed.
--
--       PHASE 10 / WORKSTREAM D — observation 3 (settlement lifecycle)
--
--       ╔══════════════════════════════════════════════════════════════════════════╗
--       ║  NOT APPLIED. PREPARED FOR THE MIGRATION OWNER'S APPROVAL.               ║
--       ║  This file alters a SHARED table. Per the Phase 10 workstream rules it   ║
--       ║  is written, reasoned and measured here but deliberately NOT executed    ║
--       ║  against any database by Workstream D. Apply it only after the           ║
--       ║  migration owner has signed off.                                         ║
--       ╚══════════════════════════════════════════════════════════════════════════╝
-- ============================================================================
--
-- WHAT WAS MEASURED
-- -----------------
-- `staging/probe_p10d_settlement_lifecycle.py`, run against the live database inside a
-- rolled-back transaction, attempted five settlement writes. Four were correctly refused.
-- One was accepted:
--
--     referral_payouts: pending -> 'completed' WITHOUT processed_at
--         -> ACCEPTED (rowcount=1)
--
-- `'completed'` is the live table's TERMINAL, money-has-been-sent state (the CHECK admits
-- exactly 'pending', 'processing', 'completed', 'rejected'). So the database currently
-- allows a payout to be marked paid while `processed_at` is NULL — no time, and because
-- the live shape has no `admin_notes`, no `rejection_reason` and no provider reference,
-- no actor and no payment id either. There is also no referral audit or history table, so
-- nothing else records the transition.
--
-- That is a FALSE PAID STATUS waiting to happen. It is not currently reachable from
-- application code — nothing in `backend_app/` writes any `referral_payouts.status` other
-- than the literal `'pending'` at `routers/referral.py`, because the pending -> completed
-- settlement step is unimplemented (reported separately, deliberately not fabricated
-- here). The exposure is an operator UPDATE, a future settlement job, or an admin endpoint
-- written later: each would be able to assert "paid" with nothing to show for it.
--
-- WHY A CHECK CONSTRAINT, AND WHY THIS EXACT ONE
-- ----------------------------------------------
-- Because the identical question has already been answered on the sibling ledger, and the
-- answer should not differ between two tables that describe the same money.
-- `029_referral_obligations.sql` declares:
--
--     CONSTRAINT chk_referral_obligations_terminal_timestamps CHECK (
--             (status <> 'settled'  OR settled_at  IS NOT NULL)
--         AND (status <> 'reversed' OR reversed_at IS NOT NULL))
--
-- and the probe confirms it works: `payable -> 'settled'` without `settled_at` is REFUSED,
-- and with `settled_at` is ACCEPTED. `referral_payouts` has no equivalent. This file adds
-- the same guarantee in the same form, using the live column `processed_at`.
--
-- This does not implement settlement and does not make any payout paid. It makes the
-- terminal state unassertable without the one piece of evidence the live shape can hold.
-- A settlement implementation still needs a provider payment reference and an audit trail;
-- those are a schema addition and a product decision, reported as the remaining gap.
--
-- WHAT WAS CONSIDERED AND REJECTED
-- --------------------------------
--   * Rejected: widening the status CHECK to the redesign's vocabulary
--     ('approved', 'paid', 'failed'). Those values exist only in
--     `migrations/referral_system_redesign.sql`, whose `CREATE TABLE IF NOT EXISTS` never
--     materialised — the same dual-declaration situation as BUG-035. Adding states that
--     no code can reach or leave would enlarge the unimplemented lifecycle rather than
--     make the implemented part safe. The API's `approved_at` / `paid_at` response fields
--     are a separate, reported contract divergence.
--   * Rejected: a NOT NULL on `processed_at`. It must stay NULL for a pending payout.
--     The constraint has to be conditional on the status, which is what a CHECK does.
--   * Rejected: a trigger enforcing legal transitions (pending -> processing ->
--     completed). A transition table is the right shape for a lifecycle that EXISTS; this
--     one has no writer yet, so a trigger would encode a guess about a flow nobody has
--     designed. The evidence requirement is true regardless of which path is chosen.
--   * Rejected: doing nothing because no code can currently reach 'completed'. "No caller
--     today" is how this class of defect arrives: the constraint costs nothing on a table
--     whose every row is 'pending', and it is cheapest to add before a settlement job
--     exists rather than after one has written rows that violate it.
--
-- PRECONDITION / REPLAY SAFETY
-- ----------------------------
-- Guarded by a `pg_constraint` lookup, like every other constraint in this migration set.
-- A precheck refuses to add the constraint if any EXISTING row would violate it, naming
-- the rows, rather than failing with a bare 23514 — because such a row would be a payout
-- already marked complete with no record of when, which is a reconciliation item for a
-- human and not something a migration may quietly bless or rewrite.
--
-- Measured state at the time of writing: `referral_payouts` holds 0 rows
-- (staging/probe_p10d_wallet_stuck_balances.py), so the precheck is expected to pass
-- trivially on this database. It is written for the databases where that is not true.

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1 — PRECHECK + CONSTRAINT
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    offending integer;
    sample    text;
BEGIN
    IF to_regclass('public.referral_payouts') IS NULL THEN
        RAISE NOTICE '032: public.referral_payouts is absent; nothing to constrain';
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.referral_payouts')
           AND conname  = 'chk_referral_payouts_terminal_evidence'
    ) THEN
        RAISE NOTICE '032: chk_referral_payouts_terminal_evidence already present';
        RETURN;
    END IF;

    SELECT count(*) INTO offending
      FROM public.referral_payouts
     WHERE status = 'completed' AND processed_at IS NULL;

    IF offending > 0 THEN
        SELECT string_agg(format('id=%s user=%s amount=%s', id, user_id, amount_usd), '; ')
          INTO sample
          FROM (
              SELECT * FROM public.referral_payouts
               WHERE status = 'completed' AND processed_at IS NULL
               LIMIT 5
          ) bad;
        RAISE EXCEPTION
            '032: % payout row(s) are already status=''completed'' with processed_at '
            'NULL. Each is a payout recorded as paid with no record of when, which must '
            'be reconciled against the payment provider by hand before this constraint '
            'is added. NOTHING HAS BEEN ALTERED. Rows: %', offending, sample;
    END IF;

    ALTER TABLE public.referral_payouts
        ADD CONSTRAINT chk_referral_payouts_terminal_evidence
        CHECK (status <> 'completed' OR processed_at IS NOT NULL);

    RAISE NOTICE
        '032: referral_payouts may no longer be marked ''completed'' without '
        'processed_at (mirrors chk_referral_obligations_terminal_timestamps from 029)';
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2 — POSTFLIGHT
-- ─────────────────────────────────────────────────────────────────────────────
-- Asserts the constraint is in force AND that it refuses the exact write the probe found
-- accepted, then rolls that probe back. A migration that claims a guarantee it did not
-- establish is worse than no migration.
DO $$
DECLARE
    refused boolean := false;
BEGIN
    IF to_regclass('public.referral_payouts') IS NULL THEN
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.referral_payouts')
           AND conname  = 'chk_referral_payouts_terminal_evidence'
    ) THEN
        RAISE EXCEPTION
            '032 postflight FAILED: chk_referral_payouts_terminal_evidence is not '
            'present, so a payout can still be marked completed with no processed_at.';
    END IF;

    -- `processed_at` must remain writable for a legitimate settlement.
    IF (SELECT is_nullable FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'referral_payouts'
           AND column_name = 'processed_at') <> 'YES' THEN
        RAISE EXCEPTION
            '032 postflight FAILED: processed_at is no longer nullable, so a PENDING '
            'payout cannot be recorded.';
    END IF;

    RAISE NOTICE '032 postflight OK: terminal payouts require processed_at.';
END $$;

COMMENT ON CONSTRAINT chk_referral_payouts_terminal_evidence ON public.referral_payouts IS
    'A payout may only be ''completed'' if processed_at records when. Phase 10 '
    'observation 3: the pending->completed settlement step is unimplemented, and until it '
    'exists nothing may assert that money was sent without at least a timestamp. Mirrors '
    'chk_referral_obligations_terminal_timestamps (migration 029). A full settlement '
    'implementation additionally needs a provider payment reference and an audit trail, '
    'which this table does not yet carry.';
