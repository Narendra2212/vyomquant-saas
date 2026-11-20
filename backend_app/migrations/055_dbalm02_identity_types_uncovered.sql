-- ═══════════════════════════════════════════════════════════════════════════
-- 055_dbalm02_identity_types_uncovered.sql
--
-- DB-ALM-02. Convert the identity columns that `998_bug049_identity_types.sql`'s own
-- backstop DEMANDS be uuid but that nothing in the codebase ever converts, and declare
-- BUG-033's three tenant_id foreign keys.
--
-- HOW THIS GAP WAS FOUND, AND IT WAS MEASURED
-- ───────────────────────────────────────────────────────────────────────────
-- 998's SECTION 4 is a deliberate whole-schema backstop: it sweeps every `public` column
-- named `user_id` or `tenant_id` and RAISES if any is not uuid, precisely so that "a
-- table added later is not covered and nobody finds out" cannot happen. Its SECTIONS
-- 1-3, by contrast, iterate a hardcoded list of twelve tables and a hardcoded column
-- name of `user_id`.
--
-- Run against the real production database, 998 converted all eleven of its remaining
-- `user_id` targets, captured and restored 22 policies and added eleven `fk_bug049_*`
-- foreign keys -- and then its own backstop aborted the transaction, naming five columns
-- that are not uuid and that SECTIONS 1-3 do not touch:
--
--     fills.tenant_id                    character varying
--     orders.tenant_id                   character varying
--     positions.tenant_id                character varying
--     strategy_backtests.user_id         text
--     strategy_research_reports.user_id  text
--
-- So 998 could never complete on production. The backstop was not wrong; it was doing
-- exactly its job, and the gap it names had no owner. The first three are BUG-033's
-- targets (alembic `a1b2c3d4e5f6`), whose Python logic has never run against production
-- -- `alembic_version` there still reads `d97ffff9c3bb`, the earliest point in the whole
-- chain. The last two belong to neither BUG-033 nor BUG-049 and are the genuinely
-- un-owned case.
--
-- WHY THIS IS A SEPARATE FILE RATHER THAN A WIDER TARGET LIST IN 998
-- ───────────────────────────────────────────────────────────────────────────
-- `tests/test_bug049_sql_and_alembic_agree.py` parses 998 and alembic `d4e5f6a7b8c9`
-- and FAILS if their target lists or ON DELETE actions diverge. That guard is correct
-- and worth keeping: 998 is BUG-049's from-empty twin and must stay in lockstep with it.
-- Widening 998 to carry BUG-033's tenant_id columns would break that agreement and
-- conflate two findings in one file. This file owns the remainder instead.
--
-- ORDERING. Runs before 998 (STEP 3b of `scripts/provision_database.py`), so by the time
-- the backstop sweeps, these five are already uuid. On a database provisioned purely
-- from repository source every one of them is created uuid to begin with, so this file
-- is a no-op there -- which is why the gap was invisible in CI and only appeared against
-- production's older, drifted schema.
--
-- IT REFUSES RATHER THAN GUESSES, exactly as 998 does: every value is inventoried for
-- UUID shape before the first ALTER, and an unconvertible value aborts having changed
-- nothing. Measured against production before writing this: all 16 then-divergent
-- columns held zero non-UUID-shaped values, and the five here hold 0 rows apiece.
--
-- POLICIES ARE CAPTURED AND RESTORED, and the count is asserted equal. PostgreSQL
-- refuses ALTER COLUMN TYPE on a column any policy references, and a retyped column
-- whose policy silently vanished is a table with no row filtering -- a cross-tenant
-- hole. Each of these five tables carries exactly two policies (an owner-access policy
-- for `authenticated` and a blanket one for `service_role`).
--
-- ON DELETE CASCADE for the three tenant_id keys, copied verbatim from BUG-033's own
-- `_BLOCKED_CONSTRAINTS` in `a1b2c3d4e5f6` rather than chosen afresh, so the two
-- mechanisms cannot disagree about deletion semantics.
--
-- NO FOREIGN KEY IS ADDED FOR strategy_backtests.user_id OR
-- strategy_research_reports.user_id, DELIBERATELY. The backstop requires only that the
-- TYPE be uuid, which this delivers. Adding a key would also decide what happens to a
-- customer's backtests and research reports when their account is deleted -- CASCADE
-- destroys them, RESTRICT blocks the deletion -- and that is a data-retention decision
-- for the product, not a side effect of a type conversion. Recorded here so the next
-- reader sees an open question rather than an oversight.
-- ═══════════════════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 1: refuse if any value cannot become a uuid
-- ───────────────────────────────────────────────────────────────────────────
-- A separate pass over every column BEFORE the first ALTER, for the reason 998 gives:
-- if the last table holds an unconvertible value, that must be found before the earlier
-- ones have been rewritten.
DO $dbalm02_precheck$
DECLARE
    spec    text[];
    t       text;
    c       text;
    bad     bigint;
    total   bigint;
    sample  text;
    specs text[][] := ARRAY[
        ARRAY['orders',                    'tenant_id'],
        ARRAY['positions',                 'tenant_id'],
        ARRAY['fills',                     'tenant_id'],
        ARRAY['strategy_backtests',        'user_id'],
        ARRAY['strategy_research_reports', 'user_id']
    ];
BEGIN
    FOREACH spec SLICE 1 IN ARRAY specs
    LOOP
        t := spec[1];
        c := spec[2];

        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = t
                          AND column_name = c) THEN
            CONTINUE;
        END IF;
        IF (SELECT data_type FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = t
               AND column_name = c) = 'uuid' THEN
            CONTINUE;
        END IF;

        EXECUTE format(
            'SELECT count(*) FILTER (WHERE %I IS NOT NULL), '
            '       count(*) FILTER (WHERE %I IS NOT NULL AND %I::text !~* %L) '
            'FROM public.%I',
            c, c, c,
            '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
            t
        ) INTO total, bad;

        IF bad > 0 THEN
            EXECUTE format(
                'SELECT string_agg(DISTINCT %I::text, '', '') FROM '
                '(SELECT %I FROM public.%I WHERE %I IS NOT NULL AND %I::text !~* %L '
                ' LIMIT 5) s',
                c, c, t, c, c,
                '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            ) INTO sample;

            RAISE EXCEPTION
                'DB-ALM-02 refuses to convert public.%.%: % of % non-null value(s) are '
                'not UUID-shaped. Sample: %. NOTHING HAS BEEN ALTERED. Decide what those '
                'rows are -- a legacy identifier, a service account, a test fixture, or '
                'a defect -- and reconcile them first. This will not guess, because '
                'silently casting a tenant identity reassigns the data it owns.',
                t, c, bad, total, sample;
        END IF;
    END LOOP;

    RAISE NOTICE 'dbalm02: precheck clean -- every uncovered identity value is convertible';
END
$dbalm02_precheck$;

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 2: convert the type, preserving the RLS policies
-- ───────────────────────────────────────────────────────────────────────────
-- Same mechanism as 998's SECTION 2, generalised over (table, column) pairs because
-- these five span two different column names. The saved-policy table therefore also
-- records WHICH column each policy's predicate must be rewritten against -- 998 could
-- hold that in a scalar because its column was always `user_id`.
DO $dbalm02_convert$
DECLARE
    spec     text[];
    t        text;
    c        text;
    pol      record;
    captured int := 0;
    restored int := 0;
    newqual  text;
    newcheck text;
    stmt     text;
    specs text[][] := ARRAY[
        ARRAY['orders',                    'tenant_id'],
        ARRAY['positions',                 'tenant_id'],
        ARRAY['fills',                     'tenant_id'],
        ARRAY['strategy_backtests',        'user_id'],
        ARRAY['strategy_research_reports', 'user_id']
    ];
BEGIN
    CREATE TEMP TABLE IF NOT EXISTS dbalm02_saved_policies (
        tablename  text,
        colname    text,
        policyname text,
        permissive text,
        roles      text,
        cmd        text,
        qual       text,
        with_check text
    ) ON COMMIT DROP;
    -- NO `DELETE FROM` here, unlike 998's otherwise-identical block. `ON COMMIT DROP`
    -- means this table cannot outlive the transaction that created it, so it is always
    -- empty at this point and the delete was defensive copying rather than a real
    -- safeguard. It also tripped `test_no_destructive_statements`, which forbids
    -- DELETE/DROP/TRUNCATE anywhere in `backend_app/migrations/` (Requirement 24.7) and
    -- reads the statement without knowing the target is a temp table of this block's own
    -- making. 998 escapes that check only because it lives in `migrations/hardening/`,
    -- which the scanner does not cover -- not because the statement is any more correct
    -- there.

    FOREACH spec SLICE 1 IN ARRAY specs
    LOOP
        t := spec[1];
        c := spec[2];

        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_schema='public' AND table_name=t
                          AND column_name=c) THEN
            CONTINUE;
        END IF;
        IF (SELECT data_type FROM information_schema.columns
             WHERE table_schema='public' AND table_name=t
               AND column_name=c) = 'uuid' THEN
            CONTINUE;
        END IF;

        -- Capture, then drop.
        FOR pol IN
            SELECT p.tablename, p.policyname, p.permissive,
                   array_to_string(p.roles, ',') AS roles, p.cmd, p.qual, p.with_check
            FROM pg_policies p
            WHERE p.schemaname = 'public' AND p.tablename = t
        LOOP
            INSERT INTO dbalm02_saved_policies VALUES (
                pol.tablename, c, pol.policyname, pol.permissive, pol.roles,
                pol.cmd, pol.qual, pol.with_check
            );
            captured := captured + 1;
            EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I',
                           pol.policyname, pol.tablename);
        END LOOP;

        EXECUTE format(
            'ALTER TABLE public.%I ALTER COLUMN %I TYPE uuid USING %I::uuid', t, c, c
        );
        RAISE NOTICE 'dbalm02: public.%.% -> uuid', t, c;
    END LOOP;

    -- Recreate. A BARE reference to the column is rewritten as `(col)::text` so the
    -- predicate still type-checks against a uuid column and compares exactly what it
    -- compared before; an already-cast `(tenant_id)::text` is left alone, because the
    -- pattern matches only the bare word with a boundary on each side. Rewriting to a
    -- native uuid comparison would arguably be better -- uuid equality normalises case
    -- -- but that changes WHO CAN SEE A ROW, and a type conversion is not the place to
    -- alter row filtering silently. Same reasoning, verbatim, as 998's.
    FOR pol IN SELECT * FROM dbalm02_saved_policies ORDER BY tablename, policyname
    LOOP
        newqual := regexp_replace(
            coalesce(pol.qual, ''),
            '(^|[^[:alnum:]_.(])' || pol.colname || '([^[:alnum:]_]|$)',
            '\1(' || pol.colname || ')::text\2', 'g'
        );
        newcheck := regexp_replace(
            coalesce(pol.with_check, ''),
            '(^|[^[:alnum:]_.(])' || pol.colname || '([^[:alnum:]_]|$)',
            '\1(' || pol.colname || ')::text\2', 'g'
        );

        stmt := format('CREATE POLICY %I ON public.%I AS %s FOR %s TO %s',
                       pol.policyname, pol.tablename, pol.permissive, pol.cmd,
                       pol.roles);
        IF coalesce(newqual, '') <> '' THEN
            stmt := stmt || ' USING (' || newqual || ')';
        END IF;
        IF coalesce(newcheck, '') <> '' THEN
            stmt := stmt || ' WITH CHECK (' || newcheck || ')';
        END IF;
        EXECUTE stmt;
        restored := restored + 1;
    END LOOP;

    IF restored <> captured THEN
        RAISE EXCEPTION
            'dbalm02: captured % policies and restored %. A retyped column with its '
            'policy missing is a table whose row filtering silently disappeared.',
            captured, restored;
    END IF;

    IF captured > 0 THEN
        RAISE NOTICE 'dbalm02: captured and restored % policy/policies', captured;
    END IF;
END
$dbalm02_convert$;

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 3: BUG-033's tenant_id foreign keys
-- ───────────────────────────────────────────────────────────────────────────
-- Names and ON DELETE copied verbatim from `a1b2c3d4e5f6`'s own `_BLOCKED_CONSTRAINTS`,
-- so the alembic path and this one cannot disagree. Added only where absent, so a re-run
-- is a no-op. An orphan RAISES with a count and a sample rather than being deleted --
-- orphans are the defect this prevents, and removing them would destroy tenant records.
DO $dbalm02_fks$
DECLARE
    spec    text[];
    t       text;
    c       text;
    fkname  text;
    action  text;
    orphans bigint;
    sample  text;
    specs text[][] := ARRAY[
        ARRAY['fills',     'tenant_id', 'fk_fills_tenant_id',     'CASCADE'],
        ARRAY['orders',    'tenant_id', 'fk_orders_tenant_id',    'CASCADE'],
        ARRAY['positions', 'tenant_id', 'fk_positions_tenant_id', 'CASCADE']
    ];
BEGIN
    FOREACH spec SLICE 1 IN ARRAY specs
    LOOP
        t := spec[1]; c := spec[2]; fkname := spec[3]; action := spec[4];

        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_schema='public' AND table_name=t
                          AND column_name=c AND data_type='uuid') THEN
            RAISE NOTICE 'dbalm02: %.% is not uuid; skipping %', t, c, fkname;
            CONTINUE;
        END IF;

        IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = fkname) THEN
            CONTINUE;
        END IF;

        EXECUTE format(
            'SELECT count(*) FROM public.%I x WHERE x.%I IS NOT NULL '
            'AND NOT EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = x.%I)',
            t, c, c
        ) INTO orphans;

        IF orphans > 0 THEN
            EXECUTE format(
                'SELECT string_agg(DISTINCT v::text, '', '') FROM '
                '(SELECT x.%I AS v FROM public.%I x WHERE x.%I IS NOT NULL '
                ' AND NOT EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = x.%I) '
                ' LIMIT 5) s', c, t, c, c
            ) INTO sample;

            RAISE EXCEPTION
                'dbalm02: cannot add % on public.%.%: % row(s) name a tenant that does '
                'not exist in public.profiles. Sample: %. Reconcile them, then re-run.',
                fkname, t, c, orphans, sample;
        END IF;

        EXECUTE format(
            'ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (%I) '
            'REFERENCES public.profiles(id) ON DELETE %s', t, fkname, c, action
        );
        RAISE NOTICE 'dbalm02: % -> profiles(id) ON DELETE %', fkname, action;
    END LOOP;
END
$dbalm02_fks$;

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 4: postflight -- say what landed, and refuse if it did not
-- ───────────────────────────────────────────────────────────────────────────
DO $dbalm02_postflight$
DECLARE
    still_text text;
    missing_fk text;
BEGIN
    SELECT string_agg(table_name || '.' || column_name || ' (' || data_type || ')',
                      ', ' ORDER BY table_name)
      INTO still_text
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND (table_name, column_name) IN (
          ('orders','tenant_id'), ('positions','tenant_id'), ('fills','tenant_id'),
          ('strategy_backtests','user_id'), ('strategy_research_reports','user_id'))
      AND data_type <> 'uuid';

    IF still_text IS NOT NULL THEN
        RAISE EXCEPTION 'dbalm02 postflight FAILED: still not uuid: %', still_text;
    END IF;

    SELECT string_agg(n, ', ' ORDER BY n) INTO missing_fk
      FROM (VALUES ('fk_fills_tenant_id'), ('fk_orders_tenant_id'),
                   ('fk_positions_tenant_id')) v(n)
     WHERE NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = v.n);

    IF missing_fk IS NOT NULL THEN
        RAISE EXCEPTION 'dbalm02 postflight FAILED: foreign key(s) absent: %', missing_fk;
    END IF;

    RAISE NOTICE 'dbalm02: all five uncovered identity columns are uuid and BUG-033''s '
                 'three tenant_id foreign keys exist';
END
$dbalm02_postflight$;
