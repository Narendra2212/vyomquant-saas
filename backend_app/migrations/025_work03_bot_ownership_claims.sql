-- ===========================================================================
-- 025_work03_bot_ownership_claims.sql
--
-- WORK-03: at most one active execution owner per logical bot, ACROSS PROCESSES.
--
-- THE DEFECT
-- ----------
-- `FleetManager.start_bot` refuses a duplicate with
--
--     if bot_key in self._active_fleet: return False, "Bot already running"
--
-- where `_active_fleet` is a per-process Python dict. Two pods therefore cannot see
-- each other's bots, and each will happily start its own BotRunner for the same
-- (user, symbol). The WORK-02 XAUTOCLAIM reaper made this reachable rather than
-- theoretical: a reclaimed `start_bot` command is delivered to a DIFFERENT consumer than
-- the one that first took it.
--
-- `uq_strategy_deployments_one_live` already exists and is a good control, but it is a
-- UNIQUE INDEX on `strategy_id` for live statuses -- it constrains the DEPLOYMENT ROW,
-- and `FleetManager` neither reads nor writes that table. Its key is also the wrong
-- shape: the fleet's logical owner key is (user_id, symbol), because that is what
-- `_bot_key` builds and what decides whether two runners would trade the same pair on
-- the same account.
--
-- WHY A CLAIM TABLE AND NOT A REDIS LOCK
-- --------------------------------------
-- A Redis `SET NX EX` is the obvious reach, and it is not sufficient on its own here:
-- `core/cache/redis_manager.py` is deliberately FAIL-SOFT (every method swallows its
-- exception and returns a falsey default), so a Redis outage turns `SET NX` into "lock
-- not acquired" for everyone -- or, worse, a future non-fail-soft refactor turns it into
-- "acquired" for everyone. An availability dependency must not be able to flip a
-- correctness invariant in the unsafe direction. PostgreSQL is already a hard dependency
-- of the deployment path, and a unique constraint cannot fail open.
--
-- THE MECHANISM
-- -------------
-- One row per logical bot, PRIMARY KEY on (user_id, symbol). The claim is taken with a
-- single statement:
--
--     INSERT INTO bot_ownership_claims (...) VALUES (...)
--     ON CONFLICT (user_id, symbol) DO UPDATE
--        SET owner_token = EXCLUDED.owner_token, ...
--      WHERE bot_ownership_claims.heartbeat_at < now() - <stale interval>
--     RETURNING owner_token;
--
-- PostgreSQL takes a row lock for the ON CONFLICT path and re-evaluates the WHERE after
-- acquiring it, so of two concurrent claimants exactly one can match. The loser gets
-- ZERO ROWS -- a deterministic no-op, not an error.
--
--   * fresh row        -> INSERT wins, caller owns the bot
--   * live row         -> DO UPDATE's WHERE is false, zero rows, caller stands down
--   * stale row        -> DO UPDATE's WHERE is true, ownership transfers, caller owns it
--
-- NO PERMANENT LOCK. The stale predicate is the whole reason `heartbeat_at` exists: a
-- worker that dies holding a claim stops heartbeating, and after the threshold any other
-- worker can take the claim over. There is no state in which a bot is permanently
-- unstartable because a pod died -- which is the failure mode a naive
-- "INSERT ... ON CONFLICT DO NOTHING" lock would have.
--
-- OWNERSHIP IS A TOKEN, NOT A BOOLEAN. `owner_token` is regenerated on every successful
-- claim, and release/heartbeat are scoped to it:
--
--     DELETE ... WHERE user_id=$1 AND symbol=$2 AND owner_token=$3
--
-- So a worker that was declared stale and had its claim taken over CANNOT release or
-- heartbeat the new owner's claim when it wakes up. Without the token, a resurrected
-- process would delete a live claim and let a third worker start a second bot.
--
-- TENANT INDEPENDENCE is structural: the key is (user_id, symbol), so two users starting
-- the same symbol are two different rows and never contend.
--
-- Regression: tests/test_work03_bot_ownership_claims_postgres.py
-- ===========================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS public.bot_ownership_claims (
    user_id      uuid        NOT NULL,
    symbol       text        NOT NULL,
    owner_token  uuid        NOT NULL DEFAULT gen_random_uuid(),
    worker_name  text        NOT NULL,
    state        text        NOT NULL DEFAULT 'STARTING',
    claimed_at   timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    released_at  timestamptz,
    CONSTRAINT pk_bot_ownership_claims PRIMARY KEY (user_id, symbol),
    CONSTRAINT chk_bot_claim_state
        CHECK (state IN ('STARTING', 'ACTIVE', 'STOPPING')),
    CONSTRAINT chk_bot_claim_symbol_nonempty
        CHECK (length(btrim(symbol)) > 0)
);

-- The reaper's access path: "which claims have gone stale?"
CREATE INDEX IF NOT EXISTS idx_bot_claims_heartbeat
    ON public.bot_ownership_claims (heartbeat_at);

-- Operational: "what is this worker holding?", for draining a pod.
CREATE INDEX IF NOT EXISTS idx_bot_claims_worker
    ON public.bot_ownership_claims (worker_name);

COMMENT ON TABLE public.bot_ownership_claims IS
    'WORK-03: one row per logical bot (user_id, symbol). The PRIMARY KEY is the '
    'cross-process single-owner invariant that FleetManager._active_fleet (a '
    'per-process dict) could not provide. Claimed with INSERT ... ON CONFLICT DO '
    'UPDATE ... WHERE heartbeat_at < now() - threshold, so exactly one of two '
    'concurrent claimants matches and the loser gets zero rows. owner_token is '
    'regenerated per claim and scopes release/heartbeat, so a worker whose claim was '
    'reaped cannot release its successor''s claim.';

COMMENT ON COLUMN public.bot_ownership_claims.owner_token IS
    'Regenerated on every successful claim. Release and heartbeat are scoped to it so a '
    'resurrected worker cannot touch a claim that was taken over from it.';

COMMENT ON COLUMN public.bot_ownership_claims.heartbeat_at IS
    'Liveness. A claim whose heartbeat is older than the stale threshold is reclaimable, '
    'which is what stops a dead pod stranding a bot permanently.';

-- RLS: these rows are per-user, and the backend writes them as the service role /
-- owner. The owner policy mirrors the pattern 009_paper_trading.sql established, so a
-- future request-scoped read is isolated by auth.uid() like everything else.
ALTER TABLE public.bot_ownership_claims ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname='public' AND tablename='bot_ownership_claims'
           AND policyname='bot_claims_owner_access'
    ) THEN
        CREATE POLICY bot_claims_owner_access ON public.bot_ownership_claims
            FOR ALL TO authenticated
            USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname='public' AND tablename='bot_ownership_claims'
           AND policyname='bot_claims_service_role'
    ) THEN
        CREATE POLICY bot_claims_service_role ON public.bot_ownership_claims
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

GRANT SELECT ON public.bot_ownership_claims TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.bot_ownership_claims TO service_role;

-- A client may READ its own claims (the UI shows which bots are running) but must never
-- write one: ownership is decided by the backend, and a caller who could INSERT a claim
-- could deny themselves -- or, with a forged user_id, somebody else -- the ability to
-- start a bot.
REVOKE INSERT, UPDATE, DELETE ON public.bot_ownership_claims FROM authenticated, anon;
REVOKE ALL ON public.bot_ownership_claims FROM anon;

COMMIT;

-- ── POSTFLIGHT ────────────────────────────────────────────────────────────
-- Fails loudly rather than reporting success on a partial apply.
DO $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM information_schema.tables
     WHERE table_schema='public' AND table_name='bot_ownership_claims';
    IF n <> 1 THEN
        RAISE EXCEPTION 'WORK-03 postflight: bot_ownership_claims was not created';
    END IF;

    SELECT count(*) INTO n FROM pg_constraint
     WHERE conname = 'pk_bot_ownership_claims';
    IF n <> 1 THEN
        RAISE EXCEPTION 'WORK-03 postflight: the (user_id, symbol) primary key is '
                        'missing - without it there is NO uniqueness invariant';
    END IF;

    SELECT count(*) INTO n FROM information_schema.role_table_grants
     WHERE table_name='bot_ownership_claims' AND grantee='authenticated'
       AND privilege_type IN ('INSERT','UPDATE','DELETE');
    IF n <> 0 THEN
        RAISE EXCEPTION 'WORK-03 postflight: authenticated still holds write grants on '
                        'bot_ownership_claims (% found)', n;
    END IF;

    RAISE NOTICE 'WORK-03 postflight OK: claim table, primary key and grants verified';
END $$;
