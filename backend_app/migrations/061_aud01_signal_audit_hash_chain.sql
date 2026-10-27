-- 061 — AUD-01: hash-chain public.signal_audit_log, so tampering is DETECTABLE and not
--               merely forbidden.
--
-- ============================================================================
--
-- WHAT 046 ALREADY GUARANTEES, AND WHAT IT CANNOT
-- -----------------------------------------------
-- `046_signal_audit_log.sql` made this table append-only in the shape 036/040 established:
-- a `BEFORE UPDATE OR DELETE` trigger and a `BEFORE TRUNCATE` trigger, both raising for
-- every role including the table owner, plus RLS with no write grant to anon/authenticated.
-- Measured in production: both triggers present and enabled ('O').
--
-- That makes tampering FORBIDDEN through the database's own enforcement. It does not make
-- tampering DETECTABLE, and the two are different properties:
--
--   * A trigger is enforcement by a mechanism that can itself be disabled. `ALTER TABLE
--     ... DISABLE TRIGGER` is available to the table owner, and a superuser can do as it
--     likes. After a `DELETE` with the trigger disabled and re-enabled, the table is
--     internally consistent and says nothing about what it used to contain.
--   * A restore from a doctored backup, or a logical replication stream replayed with rows
--     dropped, never passes through the trigger at all.
--   * Nothing in 046 orders the rows. `recorded_at` has a `now()` default and is not
--     unique, and `audit_id` is random, so "the row that came after this one" was not a
--     question the table could answer -- which means a reordering was not even definable,
--     let alone detectable.
--
-- A hash chain closes the gap the trigger cannot: each row commits to its predecessor, so
-- removing, altering or reordering ANY row breaks the chain at that point and every
-- verification afterwards reports it. The trigger stops the easy case; the chain means the
-- hard case leaves evidence.
--
-- AUD-01's own entry called this "unprecedented anywhere in this codebase and a
-- platform-wide design decision", which is why it was deferred rather than attempted
-- alongside the append-only work. It is attempted here, deliberately narrow: ONE table, the
-- one AUD-01 is about. If the pattern is wanted on `order_audit_log` and
-- `risk_settings_audit` it transfers unchanged, but widening it is a separate decision and
-- this file does not take it.
--
-- WHY THIS IS SAFE TO APPLY NOW: THE TABLE IS EMPTY
-- ------------------------------------------------
-- Measured in production before writing this: `SELECT count(*) FROM signal_audit_log` = 0.
-- That matters because a chain over EXISTING rows would need a backfill, and a backfill
-- needs `UPDATE` — which this table's own append-only trigger refuses, to the owner
-- included. Backfilling would have meant disabling the very guarantee being strengthened.
-- Starting the chain on an empty table avoids the question entirely.
--
-- A FILE APPLIED TO A NON-EMPTY TABLE IS REFUSED, in the preflight below, rather than
-- silently leaving older rows unchained. An unchained prefix is worse than no chain: it
-- looks verified and is not.
--
-- WHY BUILT-IN `sha256()` AND NOT pgcrypto's `digest()`
-- ----------------------------------------------------
-- PostgreSQL 11+ ships `sha256(bytea)` in core. Production is 17.6 and was checked:
-- `encode(sha256('abc'::bytea),'hex')` returns the known
-- ba7816bf...f20015ad, so the primitive is there.
--
-- pgcrypto's `digest()` would also work in production and in CI, and would NOT work in the
-- local provisioning lane: `pgserver` ships no contrib, which is why
-- `provision_database.py --create-substrate` installs a `gen_random_bytes` shim and why the
-- drift baseline carries pgcrypto's 36 functions as a known local gap. Depending on the
-- extension would make this migration unappliable in the one environment a developer can
-- run, for no benefit over a core function.
--
-- WHY THE HASH IS OVER A `jsonb` RENDERING AND NOT A CONCATENATION
-- ---------------------------------------------------------------
-- `strategy_id` is free text. A naive `a || '|' || b` lets content containing the separator
-- produce the same digest as a different field split -- so two materially different rows
-- could hash identically and a substitution would go undetected. `jsonb`'s text output
-- escapes string content and emits keys in a canonical order, so the rendering is
-- unambiguous and stable. `recorded_at` is formatted explicitly in UTC to microseconds so
-- the digest does not depend on the session's TimeZone setting.
--
-- ============================================================================
-- WHY `seq` IS ASSIGNED BY THE TRIGGER AND NOT BY AN IDENTITY COLUMN
-- ============================================================================
--
-- THIS IS THE ONE NON-OBVIOUS THING IN THIS FILE, AND IT WAS A REAL BUG FOUND BY TEST
-- RATHER THAN BY REASONING. The first draft declared
--
--     seq bigint GENERATED ALWAYS AS IDENTITY
--
-- which looks strictly better: the database assigns the order, a client cannot choose its
-- own position, and `UPDATE`ing seq is refused outright. Eight concurrent inserts then
-- forked the chain -- two rows claiming the same predecessor -- and the instrumented
-- reproduction showed exactly why.
--
-- A column DEFAULT, identity included, is evaluated while the tuple is being FORMED, which
-- is BEFORE any `BEFORE INSERT` trigger runs. So `nextval` is called outside the advisory
-- lock, and the seq a session ends up with bears no relation to the order in which
-- sessions acquire the lock. Measured, with the lock and visibility both working
-- perfectly (each session saw the row count climb 1,2,3,...,8 in lock-grant order):
--
--     session acquiring the lock 4th  ->  seq = 161
--     session acquiring the lock 5th  ->  seq = 158
--
-- The trigger read the head with `ORDER BY seq DESC LIMIT 1`. When the 5th session
-- committed its row at seq=158, the 6th session's read still returned seq=161's row --
-- because 158 < 161. Two rows then pointed at the same predecessor. The advisory lock was
-- not at fault and no key would have fixed it: `seq` simply did not order the chain, so
-- "the current head" was not what the query returned.
--
-- The fix is to decide the position in the same critical section that reads the head:
--
--     SELECT seq, row_hash INTO v_seq, v_prev ... ORDER BY seq DESC LIMIT 1;
--     NEW.seq := coalesce(v_seq, 0) + 1;
--
-- Now link order and seq order are the same by construction, because one read establishes
-- both. `UNIQUE (seq)` is the backstop if that ever stops being true.
--
-- WHAT IS GIVEN UP, STATED HONESTLY: with no identity, `UPDATE ... SET seq` is no longer
-- refused by the column itself, so reordering goes from PREVENTED to DETECTED. 046's
-- append-only trigger still refuses the UPDATE to every role, and an attacker who disables
-- that trigger and swaps two seq values is reported by verification (exercised in
-- tests/test_aud01_signal_audit_log.py). A column-level guarantee that only held while the
-- chain itself was wrong is not a trade worth making.
--
-- A client-supplied `seq` is discarded exactly like a client-supplied hash: the trigger
-- overwrites it unconditionally.
--
-- WHY AN ADVISORY LOCK
-- --------------------
-- The chain is GLOBAL (one chain for the table), so each insert must read the current head.
-- Two concurrent inserts would otherwise both read the same `prev_hash` and produce a fork:
-- two rows claiming the same predecessor, which verification would report as a break caused
-- by nothing but concurrency. `pg_advisory_xact_lock` serialises inserts on this table for
-- the duration of the transaction.
--
-- It is also what makes the head read correct, and that needed checking rather than
-- assuming: in READ COMMITTED a statement's snapshot is taken when the statement starts, so
-- a session that waited for the lock could in principle still read a pre-wait snapshot and
-- miss the row the previous holder just committed. Measured on a real server: a VOLATILE
-- plpgsql function (which is what an unmarked plpgsql trigger function is) takes a FRESH
-- snapshot for each command it runs, so the `SELECT` after the lock does see the latest
-- committed head. The same probe with the function marked `STABLE` read a stale snapshot
-- and saw 0 rows -- so this trigger function must NOT be marked STABLE or IMMUTABLE. That
-- is not a style preference; it is the correctness condition.
--
-- THE COST IS REAL AND IS ACCEPTED: this makes audit inserts serial. It is the right trade
-- for an audit log on a pre-launch platform (`signals` activity is low and the table is
-- empty), and the alternative -- a per-signal or per-user chain -- buys concurrency by
-- making the deletion of one signal's ENTIRE history undetectable, which is the attack a
-- chain exists to catch. A global chain that is slow is a safety control; a partitioned
-- chain that misses whole-history deletion is a performance feature wearing its clothes.
-- If throughput ever requires it, the honest change is a per-user chain plus a periodic
-- cross-chain checkpoint row, not dropping the lock.
--
-- WHAT THIS CHAIN STILL CANNOT DETECT, STATED PLAINLY
-- ---------------------------------------------------
-- A chain proves the INTERNAL consistency of the rows that are present. Two attacks leave
-- a perfectly consistent table and are therefore NOT caught by verify_signal_audit_chain()
-- alone:
--
--   1. TAIL TRUNCATION. Delete the newest k rows and what remains is a valid chain that
--      simply ends earlier. Nothing inside the table knows how long it was supposed to be.
--      Measured, not supposed: with the triggers off, deleting the newest 2 of 5 rows
--      leaves verify_signal_audit_chain() reporting ZERO breaks.
--      (Deleting from the HEAD or the MIDDLE *is* caught: the first surviving row then
--      carries a prev_hash with nothing before it, or its successor's prev_hash no longer
--      matches. Both are reported, and both were exercised.)
--   2. DELETING EVERY ROW. An empty table is a vacuously intact chain -- also measured:
--      zero breaks reported after a full DELETE.
--
-- This is not a flaw in the construction; it is the known limit of any self-contained
-- chain, and it is why real tamper-evident logs publish checkpoints somewhere the attacker
-- does not control. Closing it requires an EXTERNAL ANCHOR: the head hash recorded outside
-- this database, so "the chain is intact AND it still ends where we last saw it end" can
-- be asserted. A second table would not do -- whoever can disable a trigger on one table
-- can disable it on two.
--
-- `signal_audit_chain_head()` below exists for exactly that: it returns the row count, the
-- highest seq and the head hash, which is the triple an operator or a scheduled job can
-- ship to an append-only store outside Postgres (CloudWatch Logs, an object lock bucket)
-- and later compare. THAT JOB IS NOT CREATED HERE -- it needs a destination, a schedule and
-- an alerting path, which are deployment decisions and not DDL. Until it exists, the honest
-- statement of what this migration buys is: removal, alteration and reordering ANYWHERE
-- before the final row are detectable; truncation of the tail is not yet.
--
-- SAFETY
--   Additive. One ALTER TABLE adding three columns, three functions, one trigger. No
--   existing column changes type, no data is written, nothing is dropped. Re-running is a
--   no-op: every statement is guarded.
-- ============================================================================

-- ── Preflight: the table exists, and it is empty ─────────────────────────────
DO $$
BEGIN
    IF to_regclass('public.signal_audit_log') IS NULL THEN
        RAISE EXCEPTION
            '061 preflight FAILED: public.signal_audit_log does not exist. Apply '
            '046_signal_audit_log.sql first.';
    END IF;

    -- Only on the FIRST application: once the chain columns exist, rows are expected.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'signal_audit_log'
           AND column_name = 'row_hash'
    ) AND EXISTS (SELECT 1 FROM public.signal_audit_log) THEN
        RAISE EXCEPTION
            '061 preflight FAILED: signal_audit_log already holds rows, so a chain started '
            'here would leave an UNCHAINED PREFIX that looks verified and is not. Those '
            'rows cannot be backfilled because this table''s own append-only trigger '
            'refuses UPDATE to every role. Resolve deliberately: either archive the '
            'existing rows to a side table and apply this to the emptied table, or accept '
            'a chain that begins mid-history and record the first chained audit_id so a '
            'verifier knows where its guarantee starts.';
    END IF;
END $$;

-- ── The chain columns ────────────────────────────────────────────────────────
--
-- `seq` is what makes "the next row" a well-defined question: before this, nothing in the
-- table ordered the rows at all. It is a plain bigint, assigned by the insert trigger
-- inside the advisory lock -- see the long block above for the measured reason an identity
-- column cannot do this job.
ALTER TABLE public.signal_audit_log
    ADD COLUMN IF NOT EXISTS seq bigint;

-- An earlier draft of this file declared `seq` as GENERATED ALWAYS AS IDENTITY, which
-- forked the chain under concurrency. This clears that leftover so the file is re-appliable
-- over a database that saw the draft; it is a no-op everywhere else.
ALTER TABLE public.signal_audit_log
    ALTER COLUMN seq DROP IDENTITY IF EXISTS;

-- `prev_hash` is NULL for exactly one row, the genesis -- enforced structurally by the
-- UNIQUE NULLS NOT DISTINCT constraint below, not merely checked by verification.
ALTER TABLE public.signal_audit_log
    ADD COLUMN IF NOT EXISTS prev_hash text;

ALTER TABLE public.signal_audit_log
    ADD COLUMN IF NOT EXISTS row_hash text;

DO $$
BEGIN
    -- Safe unconditionally: the trigger always assigns seq, and the preflight above
    -- guarantees there are no pre-chain rows that could hold NULL.
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='signal_audit_log'
           AND column_name='seq' AND is_nullable='YES'
    ) AND NOT EXISTS (SELECT 1 FROM public.signal_audit_log WHERE seq IS NULL) THEN
        ALTER TABLE public.signal_audit_log ALTER COLUMN seq SET NOT NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.signal_audit_log')
           AND conname = 'uq_signal_audit_log_seq'
    ) THEN
        ALTER TABLE public.signal_audit_log
            ADD CONSTRAINT uq_signal_audit_log_seq UNIQUE (seq);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.signal_audit_log')
           AND conname = 'uq_signal_audit_log_row_hash'
    ) THEN
        -- Two rows with the same digest would make the chain ambiguous to walk. With
        -- `prev_hash` and `seq` in the preimage this can only happen on a genuine SHA-256
        -- collision or a deliberate replay, and both are things to refuse rather than
        -- tolerate.
        ALTER TABLE public.signal_audit_log
            ADD CONSTRAINT uq_signal_audit_log_row_hash UNIQUE (row_hash);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.signal_audit_log')
           AND conname = 'uq_signal_audit_log_prev_hash'
    ) THEN
        -- THE STRUCTURAL BACKSTOP AGAINST A FORK.
        --
        -- Each row may be the predecessor of AT MOST ONE other row -- which is precisely
        -- what being a chain means. The concurrency bug described at the top of this file
        -- stored a fork silently and left verification to report it afterwards as
        -- "tampering"; with this constraint the second claimant cannot be written at all.
        --
        -- NULLS NOT DISTINCT (PostgreSQL 15+; production is 17.6, CI is 17, the local
        -- pgserver lane is 16.2) also makes "exactly one genesis row" a constraint rather
        -- than an observation: a plain UNIQUE treats every NULL as distinct and would
        -- happily accept a second chain start.
        --
        -- It is a BACKSTOP, not the mechanism. If it ever fires, the serialisation above
        -- is broken and that is the bug to fix -- a refused insert costs one audit row
        -- (SignalService._record_signal_audit deliberately never lets an audit failure
        -- break the write it is recording), whereas a stored fork makes the verifier
        -- report tampering that never happened, which is the way a control like this dies.
        ALTER TABLE public.signal_audit_log
            ADD CONSTRAINT uq_signal_audit_log_prev_hash
            UNIQUE NULLS NOT DISTINCT (prev_hash);
    END IF;
END $$;

-- ── The digest, as one function so the trigger and the verifier cannot disagree ──
--
-- THIS IS THE WHOLE CORRECTNESS ARGUMENT: if the writer and the checker computed the
-- preimage separately, a verification pass could report a break that is really a
-- disagreement between two copies of the same rule. One definition, two callers.
--
-- The 8-argument version from the first draft (without p_seq) is dropped rather than left
-- behind: CREATE OR REPLACE with a changed signature creates an OVERLOAD, and two digest
-- functions is exactly the drift this single definition exists to prevent.
DROP FUNCTION IF EXISTS public.signal_audit_row_digest(
    text, uuid, uuid, text, uuid, text, timestamptz, jsonb);

CREATE OR REPLACE FUNCTION public.signal_audit_row_digest(
    p_prev_hash  text,
    p_seq        bigint,
    p_audit_id   uuid,
    p_signal_id  uuid,
    p_event_type text,
    p_user_id    uuid,
    p_strategy_id text,
    p_recorded_at timestamptz,
    p_payload    jsonb
) RETURNS text
LANGUAGE sql
-- STABLE, NOT IMMUTABLE, AND THE DIFFERENCE WAS MEASURED RATHER THAN ASSUMED.
--
-- This was drafted IMMUTABLE. pg_proc in production (17.6) says three of the four things
-- this body calls are only STABLE: `to_char(timestamptz, text)`, `convert_to(text, name)`
-- and `jsonb_build_object(VARIADIC "any")`. (`sha256`, `encode` and `timezone(text,
-- timestamptz)` are IMMUTABLE.) PostgreSQL does not check a declared volatility against
-- the body, so IMMUTABLE here would have been a claim the planner believes and acts on --
-- constant-folding, and eligibility for index expressions where a later change of meaning
-- corrupts the index silently. STABLE is the strongest guarantee this body can honestly
-- make, and it is sufficient for both callers (an insert trigger and a STABLE verifier);
-- nothing here needs the digest in an index or a CHECK.
--
-- SEPARATELY MEASURED: the OUTPUT is in fact invariant, which is the property the chain
-- actually depends on. The same inputs were digested in production across TimeZone=UTC /
-- Asia/Kolkata / America/New_York / Pacific/Kiritimati / Pacific/Niue, DateStyle=ISO,MDY /
-- ISO,YMD / German,DMY / SQL,DMY / Postgres,MDY, IntervalStyle, extra_float_digits, lc_time,
-- lc_numeric and standard_conforming_strings: ONE digest, every time. The numeric-only
-- format string below is why DateStyle and lc_time cannot reach it.
--
-- client_encoding was also varied (UTF8/LATIN1/WIN1252/SQL_ASCII) over a value built
-- server-side from codepoints: again one digest. An earlier run that passed the same text
-- as a CLIENT PARAMETER did produce two digests -- but the server was measured to have
-- RECEIVED a different string in that case (47 chars vs 52, the UTF-8 bytes of the
-- non-ASCII characters reinterpreted single-byte), so a different digest was correct. That
-- is the insert path's transport, not this function; verification reads its inputs from the
-- table, where the value is already server-encoded.
STABLE
AS $fn$
    SELECT encode(
        sha256(
            convert_to(
                coalesce(p_prev_hash, '') ||
                jsonb_build_object(
                    -- `seq` is in the preimage so a row commits to its own POSITION as
                    -- well as its contents and its predecessor. Rewriting seq then shows
                    -- up directly as "this row was altered" rather than only indirectly,
                    -- as a link that no longer matches the ordering.
                    'seq',         p_seq,
                    'audit_id',    p_audit_id,
                    'signal_id',   p_signal_id,
                    'event_type',  p_event_type,
                    'user_id',     p_user_id,
                    'strategy_id', p_strategy_id,
                    -- Explicit UTC to microseconds: the digest must not depend on the
                    -- session's TimeZone, or the same row verifies differently for two
                    -- connections.
                    'recorded_at', to_char(
                        p_recorded_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                    ),
                    'payload',     p_payload
                )::text,
                'UTF8'
            )
        ),
        'hex'
    )
$fn$;

COMMENT ON FUNCTION public.signal_audit_row_digest(
    text, bigint, uuid, uuid, text, uuid, text, timestamptz, jsonb
) IS
    'AUD-01: the one definition of a signal_audit_log row digest. Called by the insert '
    'trigger that writes row_hash and by verify_signal_audit_chain() that checks it, so '
    'the writer and the checker cannot drift. Commits to the row''s position (seq), its '
    'contents and its predecessor. Hashes a canonical jsonb rendering rather than a '
    'concatenation, because strategy_id is free text and a separator in its content would '
    'otherwise let two different rows produce one digest.';

-- ── The insert trigger: assigns the chain, and refuses to let a client choose it ──
--
-- DELIBERATELY UNMARKED, WHICH MEANS VOLATILE. Measured: a VOLATILE plpgsql function takes
-- a fresh snapshot for each command it runs, so the head read below sees the row the
-- previous lock holder just committed. The same probe with the function marked STABLE read
-- a pre-wait snapshot and saw zero rows -- which would reintroduce the fork this file
-- exists to avoid. Do not add a volatility marker here.
CREATE OR REPLACE FUNCTION public.signal_audit_log_chain()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_prev text;
    v_seq  bigint;
BEGIN
    -- Serialise inserts on this table. Two concurrent inserts would otherwise read the same
    -- head and fork the chain, which verification would report as tampering caused by
    -- nothing but concurrency. Transaction-scoped, so it is released on commit or abort
    -- without a cleanup path.
    --
    -- `hashtextextended(..., 0)` and not `hashtext(...)`: advisory keys share ONE global
    -- namespace per cluster, so the key has to be chosen against the others in use, and
    -- 059's `marketplace_creator_payout` is the existing precedent -- it takes
    -- `pg_advisory_xact_lock(hashtextextended(owner_id::text, 0))`. Matching it keeps the
    -- keys in one 64-bit space instead of mixing a sign-extended int32 in. Checked in
    -- production: this key is 3591519021121751626 and equals none of the 183 keys 059 can
    -- currently produce (one per profile, all of them enumerated, not sampled), and no
    -- other function in `public` takes an advisory lock at all. A collision here would not
    -- corrupt anything -- advisory locks are mutexes -- but it would make an audit insert
    -- and a creator payout block each other for a reason invisible in both files.
    PERFORM pg_advisory_xact_lock(hashtextextended('public.signal_audit_log', 0));

    -- ONE read establishes both the position and the predecessor. Splitting them -- a
    -- sequence for the position, this query for the predecessor -- is what forked the
    -- chain in the first draft; see the block comment at the top of this file.
    SELECT s.seq, s.row_hash
      INTO v_seq, v_prev
      FROM public.signal_audit_log s
     ORDER BY s.seq DESC
     LIMIT 1;

    -- Whatever the caller supplied is DISCARDED. A client that could set its own seq,
    -- prev_hash or row_hash could forge a consistent chain around a row it altered, which
    -- would make the whole structure decorative.
    NEW.seq := coalesce(v_seq, 0) + 1;
    NEW.prev_hash := v_prev;
    NEW.row_hash := public.signal_audit_row_digest(
        v_prev, NEW.seq, NEW.audit_id, NEW.signal_id, NEW.event_type,
        NEW.user_id, NEW.strategy_id, NEW.recorded_at, NEW.payload
    );

    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS trg_signal_audit_log_chain ON public.signal_audit_log;
CREATE TRIGGER trg_signal_audit_log_chain
    BEFORE INSERT ON public.signal_audit_log
    FOR EACH ROW
    EXECUTE FUNCTION public.signal_audit_log_chain();

-- ── Verification ─────────────────────────────────────────────────────────────
--
-- Returns one row per BREAK, and nothing at all for an intact chain -- so a caller tests
-- for emptiness rather than parsing a report, and "no rows" cannot be confused with "the
-- check did not run" by anything that also checks the row count separately.
CREATE OR REPLACE FUNCTION public.verify_signal_audit_chain()
RETURNS TABLE (
    seq           bigint,
    audit_id      uuid,
    problem       text,
    expected_hash text,
    stored_hash   text
)
LANGUAGE sql
STABLE
AS $fn$
    WITH ordered AS (
        SELECT s.*,
               lag(s.row_hash) OVER (ORDER BY s.seq) AS expected_prev,
               row_number()    OVER (ORDER BY s.seq) AS rn
          FROM public.signal_audit_log s
    )
    SELECT o.seq,
           o.audit_id,
           CASE
               -- Exactly one genesis, and it must be the first row.
               WHEN o.rn = 1 AND o.prev_hash IS NOT NULL
                   THEN 'first row carries a prev_hash, so rows before it were removed'
               WHEN o.rn > 1 AND o.prev_hash IS NULL
                   THEN 'a second genesis row: the chain was restarted mid-history'
               WHEN o.rn > 1 AND o.prev_hash <> o.expected_prev
                   THEN 'prev_hash does not match the preceding row: a row was removed, '
                        'altered or reordered here'
               ELSE 'row_hash does not match its own contents: this row was altered'
           END AS problem,
           public.signal_audit_row_digest(
               o.prev_hash, o.seq, o.audit_id, o.signal_id, o.event_type,
               o.user_id, o.strategy_id, o.recorded_at, o.payload
           ) AS expected_hash,
           o.row_hash AS stored_hash
      FROM ordered o
     WHERE (o.rn = 1 AND o.prev_hash IS NOT NULL)
        OR (o.rn > 1 AND o.prev_hash IS NULL)
        OR (o.rn > 1 AND o.prev_hash IS DISTINCT FROM o.expected_prev)
        OR o.row_hash IS DISTINCT FROM public.signal_audit_row_digest(
               o.prev_hash, o.seq, o.audit_id, o.signal_id, o.event_type,
               o.user_id, o.strategy_id, o.recorded_at, o.payload
           )
     ORDER BY o.seq
$fn$;

COMMENT ON FUNCTION public.verify_signal_audit_chain() IS
    'AUD-01: returns one row per hash-chain break in signal_audit_log, and NOTHING for an '
    'intact chain. Detects a removed row, an altered row, a reordered row and a restarted '
    'chain, each with its own message. This is what the append-only trigger cannot do: a '
    'trigger forbids tampering through the database, a chain makes tampering that went '
    'around the database (a disabled trigger, a doctored restore, a filtered replication '
    'stream) leave evidence. It CANNOT see truncation of the chain''s tail -- that needs '
    'signal_audit_chain_head() compared against an externally recorded copy.';

-- ── The head, for an external anchor ────────────────────────────────────────
--
-- See "WHAT THIS CHAIN STILL CANNOT DETECT" above. verify_signal_audit_chain() cannot see
-- tail truncation, because a truncated chain is still an intact one. This returns the three
-- values that make truncation detectable ONCE THEY ARE RECORDED SOMEWHERE ELSE: if a
-- previously-observed (row_count, max_seq, head_hash) is ahead of what the table now
-- reports, rows were removed from the end.
--
-- Returning it as a function rather than leaving callers to write the query keeps "what the
-- head is" in one place, so an anchoring job and a verifier cannot disagree about it.
CREATE OR REPLACE FUNCTION public.signal_audit_chain_head()
RETURNS TABLE (
    row_count bigint,
    max_seq   bigint,
    head_hash text
)
LANGUAGE sql
STABLE
AS $fn$
    SELECT (SELECT count(*) FROM public.signal_audit_log),
           (SELECT max(s.seq) FROM public.signal_audit_log s),
           (SELECT s.row_hash FROM public.signal_audit_log s
             ORDER BY s.seq DESC LIMIT 1)
$fn$;

COMMENT ON FUNCTION public.signal_audit_chain_head() IS
    'AUD-01: the (row_count, max_seq, head_hash) triple to record OUTSIDE this database so '
    'tail truncation becomes detectable. verify_signal_audit_chain() cannot see it: '
    'deleting the newest k rows leaves a chain that is internally valid and merely shorter. '
    'Comparing this triple against a previously anchored copy is what closes that gap. No '
    'such anchoring job exists yet -- it needs an external destination and a schedule, '
    'which are deployment decisions rather than DDL.';

-- Verification is an operator/service concern, not a client one. `anon` and `authenticated`
-- get no EXECUTE: the function reads every row's digest, which is a whole-table read that
-- RLS on the table itself would otherwise mediate.
REVOKE ALL ON FUNCTION public.verify_signal_audit_chain() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.verify_signal_audit_chain() FROM anon;
REVOKE ALL ON FUNCTION public.verify_signal_audit_chain() FROM authenticated;
GRANT EXECUTE ON FUNCTION public.verify_signal_audit_chain() TO service_role;

REVOKE ALL ON FUNCTION public.signal_audit_row_digest(
    text, bigint, uuid, uuid, text, uuid, text, timestamptz, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.signal_audit_row_digest(
    text, bigint, uuid, uuid, text, uuid, text, timestamptz, jsonb) FROM anon;
REVOKE ALL ON FUNCTION public.signal_audit_row_digest(
    text, bigint, uuid, uuid, text, uuid, text, timestamptz, jsonb) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.signal_audit_row_digest(
    text, bigint, uuid, uuid, text, uuid, text, timestamptz, jsonb) TO service_role;

REVOKE ALL ON FUNCTION public.signal_audit_chain_head() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.signal_audit_chain_head() FROM anon;
REVOKE ALL ON FUNCTION public.signal_audit_chain_head() FROM authenticated;
GRANT EXECUTE ON FUNCTION public.signal_audit_chain_head() TO service_role;

-- ── Postflight: assert the structure, and that it actually chains ────────────
DO $$
DECLARE
    v_breaks int;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='signal_audit_log'
           AND column_name IN ('seq','prev_hash','row_hash')
         GROUP BY table_name HAVING count(*) = 3
    ) THEN
        RAISE EXCEPTION '061 postflight FAILED: the three chain columns are not all present.';
    END IF;

    -- The identity leftover from the first draft must be gone, or `nextval` runs outside
    -- the advisory lock again and the chain forks under concurrency.
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='signal_audit_log'
           AND column_name='seq' AND is_identity='YES'
    ) THEN
        RAISE EXCEPTION
            '061 postflight FAILED: signal_audit_log.seq is still an identity column. A '
            'column default is evaluated BEFORE the insert trigger and therefore outside '
            'the advisory lock, so seq order would not match chain order and concurrent '
            'inserts would fork the chain.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = to_regclass('public.signal_audit_log')
           AND conname = 'uq_signal_audit_log_prev_hash'
    ) THEN
        RAISE EXCEPTION
            '061 postflight FAILED: uq_signal_audit_log_prev_hash is absent, so a forked '
            'chain could be stored rather than refused.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
         WHERE tgrelid = to_regclass('public.signal_audit_log')
           AND tgname = 'trg_signal_audit_log_chain'
           AND NOT tgisinternal
    ) THEN
        RAISE EXCEPTION
            '061 postflight FAILED: trg_signal_audit_log_chain is absent, so inserts would '
            'write NULL hashes and the chain would be decorative.';
    END IF;

    -- The append-only guarantee 046 established must still be in force. A chain on a table
    -- that now accepts UPDATE would be strictly weaker than what was there before.
    IF (SELECT count(*) FROM pg_trigger
         WHERE tgrelid = to_regclass('public.signal_audit_log')
           AND NOT tgisinternal
           AND tgname IN ('trg_signal_audit_log_no_update_delete',
                          'trg_signal_audit_log_no_truncate')) <> 2 THEN
        RAISE EXCEPTION
            '061 postflight FAILED: 046''s append-only triggers are not both present. The '
            'chain detects tampering; those triggers prevent it, and this migration must '
            'not have replaced one guarantee with the other.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname = 'signal_audit_chain_head'
    ) THEN
        RAISE EXCEPTION
            '061 postflight FAILED: signal_audit_chain_head() is absent, so there is no '
            'way to anchor the head externally and tail truncation stays undetectable.';
    END IF;

    -- Exactly one digest function: the 8-argument draft must not survive alongside the
    -- 9-argument one, or the trigger and the verifier could resolve to different rules.
    IF (SELECT count(*) FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname='public' AND p.proname='signal_audit_row_digest') <> 1 THEN
        RAISE EXCEPTION
            '061 postflight FAILED: there is not exactly one signal_audit_row_digest. Two '
            'overloads mean the writer and the checker can compute different preimages, '
            'which is the single thing this design depends on not happening.';
    END IF;

    SELECT count(*) INTO v_breaks FROM public.verify_signal_audit_chain();
    IF v_breaks <> 0 THEN
        RAISE EXCEPTION
            '061 postflight FAILED: the chain reports % break(s) immediately after being '
            'created. On an empty table this means the verifier and the digest function '
            'disagree.', v_breaks;
    END IF;

    RAISE NOTICE
        '061 postflight OK: signal_audit_log is hash-chained, append-only is intact, and '
        'verify_signal_audit_chain() reports no breaks.';
END $$;
