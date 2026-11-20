-- ═══════════════════════════════════════════════════════════════════════════
-- BUG-049: customer-identity columns are uuid and declare their owner
--
-- WHY THIS FILE EXISTS ALONGSIDE ALEMBIC d4e5f6a7b8c9
-- ───────────────────────────────────────────────────────────────────────────
-- The alembic revision does exactly this work and is the right mechanism for an
-- EXISTING database, where every table is already present. It cannot do the job on the
-- from-empty provisioning path, and that was measured rather than assumed:
--
--   `scripts/provision_database.py` runs `alembic upgrade head` at STEP 0c, BEFORE the
--   numbered SQL set, because `006_reconcile_production_database.sql` ALTERs
--   `execution_records` and only alembic declares it. At STEP 0c, 9 of the 12 target
--   tables -- billing_invoices, exchange_connections, exchange_keys,
--   notification_settings, referral_profiles, risk_settings, risk_settings_audit,
--   security_logs, strategy_limits -- DO NOT EXIST YET. The revision's `_table_exists`
--   guard correctly skips them, and a freshly provisioned database came out with 9
--   columns still `text` and 9 foreign keys missing.
--
--   Caught by `scripts/verify_production_schema.py`, which reported
--       drift/columns      9 missing, 9 unexpected
--       drift/foreign_keys 0 missing, 9 unexpected
--   comparing a freshly provisioned baseline against the migrated database.
--
-- Ordering the conversion by filename inside `migrations/` was rejected for the same
-- reason the privilege hardening was: within that directory the files sort
-- numbered-then-alphabetical and `referral_system_redesign.sql` currently sorts last, so
-- a new `zz_*.sql` would displace it. This is an explicit provisioner step instead.
--
-- THE DUPLICATION IS REAL AND IS BOUNDED DELIBERATELY
-- ───────────────────────────────────────────────────────────────────────────
-- Two implementations of one decision is a liability. It is accepted here because the
-- two run in different situations that cannot be collapsed -- alembic against a
-- database that exists, this against one being built -- and it is constrained three
-- ways:
--
--   1. both are idempotent, so whichever runs second does nothing;
--   2. `tests/test_bug049_identity_types_postgres.py` asserts the END STATE, not the
--      mechanism, so it holds whichever path produced it;
--   3. `tests/test_bug049_sql_and_alembic_agree.py` parses both files and fails if their
--      target lists or ON DELETE actions diverge.
--
-- IT REFUSES RATHER THAN GUESSES
-- ───────────────────────────────────────────────────────────────────────────
-- Every column is inventoried for values that are not UUID-shaped before anything is
-- altered. If any are found this RAISES with the table, column, count and a sample,
-- having changed nothing. Silently casting a customer identity reassigns the data it
-- owns; silently dropping one orphans it. Neither is acceptable on a table holding
-- invoices or encrypted API keys.
--
-- ON DELETE IS CHOSEN PER TABLE
-- ───────────────────────────────────────────────────────────────────────────
--   CASCADE   exchange_connections, exchange_keys, notification_settings,
--             referral_profiles, risk_settings, strategy_limits
--             `exchange_keys` and `exchange_connections` are the security cases: an
--             orphaned row is an encrypted exchange API key with no owner, which cannot
--             be audited, rotated or revoked through the product.
--
--   RESTRICT  billing_invoices, invoices, payment_methods, subscriptions,
--             risk_settings_audit, security_logs
--             Financial and audit records that must SURVIVE customer deletion. CASCADE
--             would destroy them. Every one of these columns is NOT NULL so SET NULL is
--             impossible; RESTRICT forces an account-deletion path to handle them
--             explicitly. Verified before choosing it: there is no account-deletion path
--             in `backend_app/` today, so this constrains a flow that does not exist yet.
-- ═══════════════════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 1: refuse if any value cannot become a uuid
-- ───────────────────────────────────────────────────────────────────────────
-- A separate pass over every column BEFORE the first ALTER. If the eleventh table holds
-- an unconvertible value, that must be found before the first ten have been rewritten --
-- a half-applied conversion on a money table is the worst outcome available.
DO $bug049_precheck$
DECLARE
    t      text;
    c      text := 'user_id';
    bad    bigint;
    total  bigint;
    sample text;
    targets text[] := ARRAY[
        'exchange_connections', 'exchange_keys', 'notification_settings',
        'referral_profiles', 'risk_settings', 'strategy_limits',
        'billing_invoices', 'invoices', 'payment_methods', 'subscriptions',
        'risk_settings_audit', 'security_logs'
    ];
BEGIN
    FOREACH t IN ARRAY targets
    LOOP
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
                'BUG-049 refuses to convert public.%.%: % of % non-null value(s) are '
                'not UUID-shaped. Sample: %. NOTHING HAS BEEN ALTERED. Decide what '
                'those rows are -- a legacy identifier, a service account, a test '
                'fixture, or a defect -- and reconcile them first. This will not guess, '
                'because silently casting a customer identity reassigns the data it '
                'owns.', t, c, bad, total, sample;
        END IF;
    END LOOP;

    RAISE NOTICE 'bug049: precheck clean -- every identity value is convertible';
END
$bug049_precheck$;

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 2: convert the type, preserving the RLS policies
-- ───────────────────────────────────────────────────────────────────────────
-- PostgreSQL refuses ALTER COLUMN TYPE on a column referenced by a policy, so each
-- table's policies are captured, dropped, and recreated.
--
-- THE PREDICATES CANNOT ALWAYS BE RESTORED VERBATIM. `billing_invoices` carries
--     USING ((auth.uid())::text = user_id)
-- -- left side cast, column bare. Valid while the column is text; once it is uuid,
-- restoring that verbatim fails with `operator does not exist: text = uuid`. That is not
-- hypothetical: it is what the first attempt at the alembic revision did, and alembic's
-- transactional DDL rolled the whole thing back.
--
-- So a bare reference to the column is rewritten as `(user_id)::text`, which restores
-- the ORIGINAL text comparison exactly and is the same double-cast shape `invoices`,
-- `payment_methods` and `subscriptions` already use. Rewriting to a native
-- `auth.uid() = user_id` comparison would arguably be better -- uuid equality
-- normalises case -- but that is a behaviour CHANGE, and a type conversion is not the
-- place to silently alter who can see a row.
DO $bug049_convert$
DECLARE
    t        text;
    c        text := 'user_id';
    pol      record;
    captured int := 0;
    restored int := 0;
    newqual  text;
    newcheck text;
    stmt     text;
    targets text[] := ARRAY[
        'exchange_connections', 'exchange_keys', 'notification_settings',
        'referral_profiles', 'risk_settings', 'strategy_limits',
        'billing_invoices', 'invoices', 'payment_methods', 'subscriptions',
        'risk_settings_audit', 'security_logs'
    ];
BEGIN
    CREATE TEMP TABLE IF NOT EXISTS bug049_saved_policies (
        tablename  text,
        policyname text,
        permissive text,
        roles      text,
        cmd        text,
        qual       text,
        with_check text
    ) ON COMMIT DROP;
    DELETE FROM bug049_saved_policies;

    FOREACH t IN ARRAY targets
    LOOP
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_schema='public' AND table_name=t
                          AND column_name=c) THEN
            CONTINUE;
        END IF;
        IF (SELECT data_type FROM information_schema.columns
             WHERE table_schema='public' AND table_name=t AND column_name=c) = 'uuid' THEN
            CONTINUE;
        END IF;

        -- Capture, then drop.
        FOR pol IN
            SELECT p.tablename, p.policyname, p.permissive,
                   array_to_string(p.roles, ',') AS roles, p.cmd, p.qual, p.with_check
            FROM pg_policies p
            WHERE p.schemaname = 'public' AND p.tablename = t
        LOOP
            INSERT INTO bug049_saved_policies VALUES (
                pol.tablename, pol.policyname, pol.permissive, pol.roles,
                pol.cmd, pol.qual, pol.with_check
            );
            captured := captured + 1;
            EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I',
                           pol.policyname, pol.tablename);
        END LOOP;

        EXECUTE format(
            'ALTER TABLE public.%I ALTER COLUMN %I TYPE uuid USING %I::uuid', t, c, c
        );
        RAISE NOTICE 'bug049: public.%.% -> uuid', t, c;
    END LOOP;

    -- Recreate. The column reference is cast to text so the predicate type-checks
    -- against a uuid column; an already-cast `(user_id)::text` is left alone by
    -- matching on the bare word with a boundary.
    FOR pol IN SELECT * FROM bug049_saved_policies ORDER BY tablename, policyname
    LOOP
        newqual := regexp_replace(
            coalesce(pol.qual, ''),
            '(^|[^[:alnum:]_.(])' || c || '([^[:alnum:]_]|$)',
            '\1(' || c || ')::text\2', 'g'
        );
        newcheck := regexp_replace(
            coalesce(pol.with_check, ''),
            '(^|[^[:alnum:]_.(])' || c || '([^[:alnum:]_]|$)',
            '\1(' || c || ')::text\2', 'g'
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
            'bug049: captured % policies and restored %. A retyped column with its '
            'policy missing is a table whose row filtering silently disappeared.',
            captured, restored;
    END IF;

    IF captured > 0 THEN
        RAISE NOTICE 'bug049: captured and restored % policy/policies', captured;
    END IF;
END
$bug049_convert$;

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 3: declare the foreign keys, with the chosen ON DELETE
-- ───────────────────────────────────────────────────────────────────────────
-- Added only where absent, so a re-run is a no-op. `profiles(id)` rather than
-- `auth.users(id)`: `profiles` is this schema's own customer table, it is what every
-- other customer foreign key references, and it already cascades from `auth.users`.
--
-- An orphan would make the constraint un-addable. It RAISES with a count and a sample
-- rather than deleting the rows -- orphans are exactly the defect this exists to
-- prevent, and removing them automatically would destroy customer records.
DO $bug049_fks$
DECLARE
    spec      text[];
    t         text;
    c         text := 'user_id';
    action    text;
    fkname    text;
    orphans   bigint;
    sample    text;
    specs text[][] := ARRAY[
        ARRAY['exchange_connections',  'CASCADE'],
        ARRAY['exchange_keys',         'CASCADE'],
        ARRAY['notification_settings', 'CASCADE'],
        ARRAY['referral_profiles',     'CASCADE'],
        ARRAY['risk_settings',         'CASCADE'],
        ARRAY['strategy_limits',       'CASCADE'],
        ARRAY['billing_invoices',      'RESTRICT'],
        ARRAY['invoices',              'RESTRICT'],
        ARRAY['payment_methods',       'RESTRICT'],
        ARRAY['subscriptions',         'RESTRICT'],
        ARRAY['risk_settings_audit',   'RESTRICT'],
        ARRAY['security_logs',         'RESTRICT']
    ];
BEGIN
    FOREACH spec SLICE 1 IN ARRAY specs
    LOOP
        t := spec[1];
        action := spec[2];
        fkname := 'fk_bug049_' || t || '_' || c;

        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_schema='public' AND table_name=t
                          AND column_name=c) THEN
            CONTINUE;
        END IF;

        IF EXISTS (
            SELECT 1 FROM pg_constraint con
            JOIN pg_class cl ON cl.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = cl.relnamespace AND n.nspname='public'
            CROSS JOIN LATERAL unnest(con.conkey) AS k(attnum)
            JOIN pg_attribute att ON att.attrelid = con.conrelid
                                 AND att.attnum = k.attnum
            WHERE con.contype='f' AND cl.relname = t AND att.attname = c
        ) THEN
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
                'bug049: cannot add a foreign key on public.%.%: % row(s) name a '
                'customer that does not exist in public.profiles. Sample: %. These '
                'orphans are exactly the defect this migration exists to prevent, and '
                'deleting them automatically would destroy customer records. Reconcile '
                'them, then re-run.', t, c, orphans, sample;
        END IF;

        EXECUTE format(
            'ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (%I) '
            'REFERENCES public.profiles(id) ON DELETE %s', t, fkname, c, action
        );
        RAISE NOTICE 'bug049: % -> profiles(id) ON DELETE %', fkname, action;
    END LOOP;
END
$bug049_fks$;

-- ───────────────────────────────────────────────────────────────────────────
-- SECTION 4: refuse to finish while any identity column is still divergent
-- ───────────────────────────────────────────────────────────────────────────
-- The sections above name tables explicitly, so a table added later is not covered and
-- nobody finds out. This is the backstop, and it sweeps the WHOLE schema rather than the
-- list: any `user_id` or `tenant_id` that is not `uuid` aborts provisioning with the
-- column named.
--
-- An EXCEPTION and not a NOTICE. This is the defect class that let encrypted exchange
-- credentials outlive the accounts that owned them.
DO $bug049_backstop$
DECLARE
    offending text;
BEGIN
    SELECT string_agg(table_name || '.' || column_name || ' (' || data_type || ')',
                      ', ' ORDER BY table_name, column_name)
      INTO offending
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND column_name IN ('user_id', 'tenant_id')
      AND data_type <> 'uuid';

    IF offending IS NOT NULL THEN
        RAISE EXCEPTION
            'bug049 backstop FAILED: these customer/tenant identity columns are not '
            'uuid, so they cannot reference public.profiles(id) and nothing stops a row '
            'naming a customer who does not exist -- or keeps a credential from '
            'outliving its owner: %. See migrations/hardening/998_bug049_identity_'
            'types.sql.', offending;
    END IF;

    RAISE NOTICE 'bug049: every customer/tenant identity column is uuid';
END
$bug049_backstop$;
