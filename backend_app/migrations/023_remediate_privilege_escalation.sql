-- backend_app/migrations/023_remediate_privilege_escalation.sql
--
-- REMEDIATION PHASE, STEP 1: authorization / privilege escalation.
--
-- Closes three confirmed defects, each verified against the live catalogue before this
-- file was written (staging/rem_inspect.log, staging/rem_inspect2.txt):
--
--   BUG-057  a creator can self-approve / self-activate / self-feature their own listing
--   RLS-01   increment_ml_addon is SECURITY DEFINER, takes a caller-supplied user id,
--            and PUBLIC holds EXECUTE on it  (ACL observed: {=X/postgres, anon=X/..., ...})
--   RLS-03   anon holds INSERT/UPDATE/DELETE on public.library_strategies
--
-- Additive and idempotent. No DROP TABLE, no DROP COLUMN, no data mutation.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1 — BUG-057. THE INTENDED RULE, DERIVED NOT INVENTED.
-- ─────────────────────────────────────────────────────────────────────────────
-- `admin_moderate_strategy` (backend_app/routers/library.py:2670) is
-- `Depends(get_admin_user)`, writes through a SERVICE-ROLE client, and its own comment
-- states that the Submission state machine and its six admin actions are "the only path
-- that moves the effective lifecycle". `submission_state.py` holds the 8-state machine,
-- and `MODERATION_STATUS_FOR_STATE` / `IS_ACTIVE_FOR_STATE` are its single shared
-- mappings onto `moderation_status` / `is_active`. The product rule therefore already
-- exists and is unambiguous:
--
--     creator  -> may create and edit their own listing CONTENT
--     creator  -> may NOT move the moderation lifecycle
--     admin    -> approves / rejects / features / hides, through the state machine
--
-- CREATOR-CONTROLLED, deliberately left writable: the listing's descriptive and
-- commercial content, INCLUDING `price_minor` and `price`. Pricing is a creator decision
-- in this product — `update_library_settings` lets the author set it and checkout reads
-- it server-side, so a creator pricing their own listing is intended. The harm in
-- BUG-057 is not that a creator set a price; it is that a creator made an UNREVIEWED
-- listing visible and promoted.
--
-- PLATFORM-CONTROLLED, revoked below:
--   moderation_status  is_active  is_featured            the lifecycle itself
--   moderated_by  moderated_at  moderation_notes         the moderation record
--   published_at                                          publication instant
--   verification_status  evaluation_score                 platform assessment
--   avg_rating  rating_count  clone_count  subscriber_count   derived social proof
--
-- The last four are included because a creator who can write `avg_rating = 5` and
-- `subscriber_count = 10000` is committing the same category of fraud as self-approval,
-- and they were writable for exactly the same reason — a blanket table grant.
--
-- ─── WHY COLUMN PRIVILEGES, AND WHY `WITH CHECK` ALONE CANNOT WORK ───
-- `WITH CHECK (author_id = auth.uid())` is necessary but CANNOT fix this by itself: the
-- creator legitimately IS the author, so the predicate passes and the write proceeds.
-- The only predicate that could help would compare the new row to the old row, which
-- row-level security does not expose. Column privileges are PostgreSQL's mechanism for
-- "this role may update these columns and not those", so that is what is used.
--
-- ─── WHY A REVOKE-THEN-REGRANT, AND NOT A COLUMN-LEVEL REVOKE ───
-- This file's first version tried `REVOKE UPDATE (col) ... FROM authenticated` and its
-- own postflight caught that it had changed nothing. Column privileges in PostgreSQL are
-- ADDITIVE to table privileges: a table-wide `GRANT UPDATE ON t` already authorises
-- every column, and revoking a column privilege does not subtract from it. The only way
-- to restrict is to remove the table-wide grant and then grant back exactly the columns
-- that should be writable. That is done below, computed from the catalogue rather than
-- from a hardcoded list of ~50 column names.
--
-- CONSEQUENCE, STATED BECAUSE IT IS A REAL TRADE-OFF: a column added to this table in
-- future will NOT be writable by `authenticated` until it is granted. That is
-- deny-by-default, which is the correct direction for a table that carries moderation
-- state, but it means a future migration adding a creator-editable column must grant it.
-- Section 4's postflight prints the writable set so the decision is visible, and
-- `tests/test_bug057_listing_moderation_authz_postgres.py` pins the protected set.

DO $$
DECLARE
    protected_cols CONSTANT text[] := ARRAY[
        'moderation_status', 'is_active', 'is_featured',
        'moderated_by', 'moderated_at', 'moderation_notes',
        'published_at', 'verification_status', 'evaluation_score',
        'avg_rating', 'rating_count', 'clone_count', 'subscriber_count'
    ];
    writable_cols text;
    n_writable int;
BEGIN
    IF to_regclass('public.library_strategies') IS NULL THEN
        RAISE NOTICE '023: public.library_strategies absent; section 1 skipped';
        RETURN;
    END IF;

    SELECT string_agg(quote_ident(column_name), ', ' ORDER BY column_name), count(*)
      INTO writable_cols, n_writable
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name   = 'library_strategies'
       AND NOT (column_name = ANY (protected_cols));

    IF writable_cols IS NULL THEN
        RAISE EXCEPTION '023: refusing to revoke — could not compute the writable column '
                        'set for library_strategies.';
    END IF;

    -- Step 1: remove the blanket table-wide write grants. This is what actually makes
    -- the protected columns unwritable.
    REVOKE INSERT, UPDATE ON public.library_strategies FROM authenticated;

    -- Step 2: grant back only the creator-editable columns.
    EXECUTE format(
        'GRANT INSERT (%s), UPDATE (%s) ON public.library_strategies TO authenticated',
        writable_cols, writable_cols
    );

    RAISE NOTICE '023: library_strategies — authenticated may now write % of % columns; '
                 'the % moderation/derived columns are platform-only',
                 n_writable,
                 (SELECT count(*) FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='library_strategies'),
                 array_length(protected_cols, 1);
END
$$;

-- The UPDATE policy gains the WITH CHECK it never had, so an author cannot re-assign
-- their row to a different author. Independent of, and additional to, the column
-- privileges above — the two cover different attacks.
DROP POLICY IF EXISTS "Users can update their own strategies" ON public.library_strategies;
CREATE POLICY "Users can update their own strategies"
    ON public.library_strategies
    FOR UPDATE
    TO authenticated
    USING (author_id = auth.uid())
    WITH CHECK (author_id = auth.uid());

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2 — RLS-03. anon has no business writing to a listing table.
-- ─────────────────────────────────────────────────────────────────────────────
-- Observed: `anon` holds DELETE, INSERT, SELECT and UPDATE on library_strategies. It is
-- currently INERT, because every policy on the table is `TO authenticated` and a role
-- with no applicable policy is denied by default. Removed anyway: the grant is one
-- `CREATE POLICY ... TO anon` away from being live, and "safe because no policy happens
-- to match" is not a control. SELECT is retained — the catalogue is browsable
-- unauthenticated by design.
REVOKE INSERT, UPDATE, DELETE ON public.library_strategies FROM anon;

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 3 — RLS-01. increment_ml_addon is callable by anon and authenticated.
-- ─────────────────────────────────────────────────────────────────────────────
-- Observed ACL: {=X/postgres, postgres=X/postgres, anon=X/postgres,
--                authenticated=X/postgres, service_role=X/postgres}
-- `=X` is PUBLIC holding EXECUTE. The function is SECURITY DEFINER, takes
-- `target_user_id UUID` from the caller, and runs
--     UPDATE profiles SET ml_addons_purchased = ... WHERE id = target_user_id
-- with no check that the caller is that user. Any holder of a valid anon or
-- authenticated JWT could grant a paid add-on to any account via
--     POST /rest/v1/rpc/increment_ml_addon {"target_user_id": "<any-uuid>"}
--
-- WHY A REVOKE AND NOT AN `auth.uid()` GUARD INSIDE THE FUNCTION.
-- An internal `IF target_user_id <> auth.uid() THEN RAISE` would BREAK the only
-- legitimate caller: `routers/billing.py:1053` invokes this through a service-role
-- client from a signature-verified webhook, where `auth.uid()` is NULL. This is a
-- server-side billing primitive, not a user-facing RPC, so the fix is to stop exposing
-- it to browser roles. Strictly narrowing.
DO $$
DECLARE
    sig text;
BEGIN
    SELECT pg_get_function_identity_arguments(p.oid) INTO sig
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'increment_ml_addon'
     LIMIT 1;

    IF sig IS NULL THEN
        RAISE NOTICE '023: increment_ml_addon not present; section 3 skipped';
        RETURN;
    END IF;

    EXECUTE format('REVOKE ALL ON FUNCTION public.increment_ml_addon(%s) FROM PUBLIC', sig);
    EXECUTE format('REVOKE ALL ON FUNCTION public.increment_ml_addon(%s) FROM anon', sig);
    EXECUTE format('REVOKE ALL ON FUNCTION public.increment_ml_addon(%s) FROM authenticated', sig);
    EXECUTE format('GRANT EXECUTE ON FUNCTION public.increment_ml_addon(%s) TO service_role', sig);

    RAISE NOTICE '023: increment_ml_addon(%) EXECUTE revoked from PUBLIC/anon/authenticated, '
                 'granted to service_role', sig;
END
$$;

-- The three `fn_*` library helpers are also SECURITY DEFINER and PUBLIC-executable, but
-- each scopes internally by `auth.uid()` in its own WHERE clause
-- (archived_migrations/root_migrations/005_rls_library_ratings.sql:29-81), so a caller
-- cannot pass an arbitrary identity. Deliberately left alone — revoking them would break
-- the library read paths that legitimately use them.

-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 4 — POSTFLIGHT. Refuse to finish unless the fixes actually took.
-- ─────────────────────────────────────────────────────────────────────────────
-- A migration that silently did nothing is worse than one that failed, because the
-- report then says it ran. This section already earned its place: it caught the
-- additive-column-privilege mistake described in section 1.
DO $$
DECLARE
    leftover text;
    n int;
BEGIN
    IF to_regclass('public.library_strategies') IS NULL THEN
        RETURN;
    END IF;

    -- 4a. no browser role may write a protected listing column
    SELECT string_agg(DISTINCT grantee || '.' || column_name, ', ')
      INTO leftover
      FROM information_schema.column_privileges
     WHERE table_schema='public' AND table_name='library_strategies'
       AND grantee IN ('anon','authenticated')
       AND privilege_type IN ('UPDATE','INSERT')
       AND column_name IN ('moderation_status','is_active','is_featured',
                           'moderated_by','moderated_at','moderation_notes',
                           'published_at','verification_status','evaluation_score',
                           'avg_rating','rating_count','clone_count','subscriber_count');
    IF leftover IS NOT NULL AND leftover <> '' THEN
        RAISE EXCEPTION '023 postflight FAILED (BUG-057): a browser role can still write '
                        'protected moderation columns: %', leftover;
    END IF;

    -- 4b. a creator can still edit the content they are supposed to edit. Asserted
    -- positively: a fix that locked the whole table would also pass 4a.
    IF NOT has_column_privilege('authenticated', 'public.library_strategies', 'name', 'UPDATE')
       OR NOT has_column_privilege('authenticated', 'public.library_strategies', 'description', 'UPDATE')
       OR NOT has_column_privilege('authenticated', 'public.library_strategies', 'price_minor', 'UPDATE') THEN
        RAISE EXCEPTION '023 postflight FAILED: the revoke was too broad — a creator can no '
                        'longer edit their own listing name/description/price.';
    END IF;

    -- 4c. the UPDATE policy now carries a WITH CHECK
    SELECT count(*) INTO n
      FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
     WHERE c.relname='library_strategies' AND p.polcmd='w'
       AND p.polwithcheck IS NOT NULL;
    IF n = 0 THEN
        RAISE EXCEPTION '023 postflight FAILED (BUG-057): the library_strategies UPDATE '
                        'policy still has no WITH CHECK.';
    END IF;

    -- 4d. anon holds no write privilege on the listing table
    SELECT string_agg(privilege_type, ', ') INTO leftover
      FROM information_schema.role_table_grants
     WHERE table_schema='public' AND table_name='library_strategies'
       AND grantee='anon' AND privilege_type IN ('INSERT','UPDATE','DELETE');
    IF leftover IS NOT NULL AND leftover <> '' THEN
        RAISE EXCEPTION '023 postflight FAILED (RLS-03): anon still holds % on '
                        'library_strategies.', leftover;
    END IF;

    -- 4e. increment_ml_addon is not reachable by a browser role, and IS still reachable
    -- by the service role that legitimately needs it
    IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n2 ON n2.oid=p.pronamespace
                WHERE n2.nspname='public' AND p.proname='increment_ml_addon') THEN
        IF has_function_privilege('authenticated', 'public.increment_ml_addon(uuid)', 'EXECUTE')
           OR has_function_privilege('anon', 'public.increment_ml_addon(uuid)', 'EXECUTE') THEN
            RAISE EXCEPTION '023 postflight FAILED (RLS-01): increment_ml_addon is still '
                            'EXECUTE-able by a browser role.';
        END IF;
        IF NOT has_function_privilege('service_role', 'public.increment_ml_addon(uuid)', 'EXECUTE') THEN
            RAISE EXCEPTION '023 postflight FAILED (RLS-01): service_role LOST EXECUTE on '
                            'increment_ml_addon — the billing webhook would break.';
        END IF;
    END IF;

    RAISE NOTICE '023: postflight passed — BUG-057, RLS-01 and RLS-03 enforced at the '
                 'database layer, and creator content editing still works.';
END
$$;
