-- ============================================================================
--  054 - PAPER-04: a terminal state for a session whose runner vanished
-- ============================================================================
--
--  THE GAP THIS CLOSES
--  -------------------
--  PAPER-04's remaining_action: "there is no terminal state to mark an orphaned
--  RUNNING session with (migration 009 chk_paper_session_state admits only
--  CREATED/RUNNING/PAUSED/STOPPED)."
--
--  The session loop is an asyncio.Task inside the web process. A redeploy or a
--  worker restart ends every in-flight loop, and nothing resumes it. The row stays
--  at RUNNING forever: it counts against Requirement 27.4's per-user concurrency
--  cap, it reads as live to every client and to the operator, and no figure it
--  reports will ever advance again. The platform had no way to say otherwise,
--  because all four of Requirement 17.7's states are wrong for it --
--
--     RUNNING   is false: nothing is running.
--     PAUSED    is false AND dangerous: PAUSED is a state a user chose and can
--               resume from, so it would invite a resume that cannot work and
--               would hide the defect behind a user-initiated label.
--     STOPPED   is false in the way that matters: STOPPED is what `stop_session`
--               writes after it has settled the session deliberately. Marking an
--               orphan STOPPED makes an unplanned death indistinguishable from an
--               orderly shutdown, which is exactly the confusion PAPER-04 is about.
--     CREATED   is false: it ran, and it holds orders and fills.
--
--  WHY A FIFTH STATE RATHER THAN REUSING ONE
--  -----------------------------------------
--  Requirement 17.7 names four states and 009's constraint comment says so. This
--  migration deliberately deviates, and the deviation IS the fix: the requirement's
--  four states cannot express "was RUNNING, and the process that ran it is gone",
--  which is the single fact PAPER-04 exists to make recordable. Requirement 17.7
--  should be updated to five; this file is the evidence for that edit rather than a
--  silent departure from it.
--
--  ABANDONED, not FAILED. PAPER-04's text says "a FAILED or similar terminal
--  state". FAILED reads as "the strategy failed" or "the session errored", which is
--  a claim about the trading, and this condition is not that: the session may have
--  been performing perfectly when its host disappeared. ABANDONED names what
--  actually happened -- its runner left -- and makes no claim about the strategy.
--
--  TERMINAL, AND DELIBERATELY NOT RESUMABLE
--  ----------------------------------------
--  ABANDONED has no outgoing transition except the same reset path STOPPED has.
--  Resuming is not offered because this migration cannot make it safe: the loop's
--  in-flight state -- which bar it had reached, which resting orders it was
--  watching -- lived in the dead process and was never persisted. A "resume" would
--  restart from a gap of unknown length and silently report continuity it does not
--  have. Reset-and-start is honest about discarding the gap; resume would not be.
--
--  IDEMPOTENT, and safe to run against a database that already has it.
-- ============================================================================

BEGIN;

DO $$
BEGIN
    -- The constraint is recreated rather than altered: PostgreSQL has no
    -- ALTER CONSTRAINT for a CHECK expression, and DROP + ADD inside one
    -- transaction is atomic, so no window exists where the column is unconstrained.
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_sessions'::regclass
           AND conname  = 'chk_paper_session_state'
           AND pg_get_constraintdef(oid) NOT LIKE '%ABANDONED%'
    ) THEN
        ALTER TABLE public.paper_sessions
            DROP CONSTRAINT chk_paper_session_state;

        ALTER TABLE public.paper_sessions
            ADD CONSTRAINT chk_paper_session_state CHECK (session_state IN (
                'CREATED', 'RUNNING', 'PAUSED', 'STOPPED', 'ABANDONED'));

        RAISE NOTICE 'PAPER-04: chk_paper_session_state now admits ABANDONED '
                     '(5 states; Requirement 17.7 named 4 and cannot express '
                     '"RUNNING with no runner").';

    ELSIF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.paper_sessions'::regclass
           AND conname  = 'chk_paper_session_state'
    ) THEN
        -- 009 never applied its own guarded ADD on this database.
        ALTER TABLE public.paper_sessions
            ADD CONSTRAINT chk_paper_session_state CHECK (session_state IN (
                'CREATED', 'RUNNING', 'PAUSED', 'STOPPED', 'ABANDONED'));

        RAISE NOTICE 'PAPER-04: added chk_paper_session_state with 5 states.';
    ELSE
        RAISE NOTICE 'PAPER-04: chk_paper_session_state already admits ABANDONED; '
                     'nothing to do.';
    END IF;
END $$;

-- ── The audit trail for an abandonment ──────────────────────────────────────
--
-- `stopped_at` is reused rather than a sixth timestamp column added: it means "when
-- this session stopped producing", which is true of an abandoned session too, and
-- every existing read that reports a session's end already selects it.
-- `abandoned_reason` is what distinguishes the two, so a row is never ambiguous
-- about whether a human stopped it or its process died.
ALTER TABLE public.paper_sessions
    ADD COLUMN IF NOT EXISTS abandoned_reason TEXT;

-- ── THE HEARTBEAT, AND WHY RECONCILIATION IS UNSAFE WITHOUT IT ──────────────
--
-- A boot-time pass that simply marked every RUNNING row ABANDONED would be a worse
-- defect than the one it fixes. `startup.sh` runs `gunicorn --workers ${WORKERS:-2}`,
-- so the web process is PLURAL: if worker B restarts while worker A is healthily
-- running sessions, B's boot pass would abandon A's LIVE sessions, stopping real
-- in-flight paper trading and reporting it as a crash. "Is this row's loop alive?"
-- cannot be answered from the row's state alone, and it cannot be answered by asking
-- the local process either, because the owner may be a different one.
--
-- So the loop declares liveness. `last_heartbeat_at` is written by the running loop
-- on each iteration, and reconciliation abandons only a RUNNING row whose heartbeat
-- is STALE -- which is a statement about the loop, not about which process is asking.
-- That also makes the pass safe to run from every worker concurrently and safe to run
-- repeatedly, since a live session is never eligible.
--
-- NULLABLE, deliberately: a session that was RUNNING before this column existed has
-- no heartbeat and never will. Reconciliation treats NULL as "stale" only when the
-- row is also older than the staleness window, so a session that started seconds ago
-- and has not yet written its first beat is not abandoned by a boot that races it.
ALTER TABLE public.paper_sessions
    ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;

COMMENT ON COLUMN public.paper_sessions.last_heartbeat_at IS
    'PAPER-04. Written by the running session loop on each iteration. Reconciliation '
    'abandons a RUNNING row only when this is stale, which is what makes the pass '
    'safe under gunicorn --workers 2: a worker restarting must not abandon sessions '
    'another worker is healthily running. NULL means no beat has been written yet.';

COMMENT ON COLUMN public.paper_sessions.abandoned_reason IS
    'PAPER-04. Why a session_state=ABANDONED row was abandoned - set by the '
    'boot-time reconciliation pass that finds a RUNNING row whose in-process loop '
    'no longer exists. NULL for every other state. Nullable deliberately: a '
    'backfilled NOT NULL would have to invent a reason for sessions abandoned '
    'before this column existed.';

-- Reconciliation reads exactly one predicate at boot: the RUNNING rows. Small and
-- selective, because RUNNING is a tiny fraction of the table and the cap on it is 20.
CREATE INDEX IF NOT EXISTS idx_paper_sessions_running_for_reconcile
    ON public.paper_sessions (session_state)
    WHERE session_state = 'RUNNING';

COMMIT;
