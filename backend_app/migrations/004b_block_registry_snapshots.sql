-- 004b_block_registry_snapshots.sql  (migration 004, PART 2 of 5)
--
-- PURPOSE
--   Make "what the author was actually offered" reconstructable. A strategy
--   version records registry_version (a deterministic content hash of the
--   assembled descriptor set, e.g. 'r_3d173598'), but that hash is only a
--   label: without the descriptor set it names, a version saved against an
--   older palette cannot be explained. A param range widens, a block is
--   removed, a model library disappears from the image - and the row that
--   says 'r_3d173598' becomes unreadable provenance.
--
--   This part adds the table that stores the descriptor set itself, keyed by
--   that hash. Requirement 4.16 ("THE Persistence_Layer SHALL retain a
--   Block_Registry snapshot for each registry version") is what it exists to
--   satisfy; Requirement 9.1 is the other half, and it is already met by the
--   strategy_versions.registry_version column from part 1.
--
-- SCOPE - THIS FILE IS PART 2 ONLY
--   Migration 004 is landed incrementally, matching the phase that needs
--   each piece (tasks.md "Migration split"):
--     part 1  Phase 2 (task 2.2)  strategy_versions columns
--                                 -> 004_strategy_builder_canonical.sql
--     part 2  Phase 3 (task 3.2)  block_registry_snapshots   <- THIS FILE
--     part 3  Phase 4 (task 4.1)  chk_valid_requires_hash
--                                 + reject_immutable_version_update()
--                                 + trg_sv_immutable
--                                 -> 004c_immutable_versions.sql
--     part 4  Phase 6 (task 6.1)  training_jobs, model_versions
--                                 -> 004d_training_and_models.sql
--     part 5  Phase 8 (task 8.1)  strategy_deployments columns
--                                 -> 004e_deployment_bindings.sql
--
-- WHY A SEPARATE FILE RATHER THAN AN EDIT TO PART 1
--   tasks.md says "extend 004_strategy_builder_canonical.sql". The DDL below
--   IS that extension - same migration number, same design.md section - but
--   it lands as a sibling file for three reasons:
--     1. Migrations here are applied BY HAND, per file, and nothing records
--        which files an environment has run. Appending to a file an operator
--        may already have applied leaves no signal that the file changed;
--        the operator would have to diff it to notice. A new filename is the
--        signal.
--     2. Part 1's header states "THIS FILE IS PART 1 ONLY" and enumerates
--        the parts as separate landings. strategy_service.py names part 1
--        explicitly in its degradation warning
--        (CANONICAL_COLUMN_MIGRATION + "(part 1)"); this file is named
--        separately in the snapshot writer's warning
--        (registry_snapshot_service.SNAPSHOT_TABLE_MIGRATION), so each
--        warning points an operator at exactly one file to apply.
--     3. 003 already set the precedent for a suffixed companion under one
--        number (003_signal_trace_preflight.sql beside
--        003_signal_trace_restoration.sql).
--   Applying part 1 and part 2 in either order works: they touch different
--   objects and neither references the other.
--
-- SOURCE OF THE DEFINITION
--   design.md -> "Migration 004_strategy_builder_canonical.sql", section
--   "-- 5. Registry snapshots (reproducibility of what the user was
--   offered)", reproduced column for column:
--     registry_version TEXT PRIMARY KEY    the deterministic hash; the
--                                          natural key, and the PRIMARY KEY
--                                          is what makes a repeat write a
--                                          no-op rather than a duplicate
--     descriptors      JSONB NOT NULL      BlockRegistry.to_dict(): the full
--                                          served payload - registry_version,
--                                          registry_schema_version,
--                                          port_types, categories, blocks[],
--                                          compatibility_matrix
--     created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
--                                          when this descriptor set was
--                                          first seen
--   No column is added beyond those three and no index beyond the implicit
--   primary-key one, because design.md specifies none and a snapshot is read
--   by exact key, never scanned. One CHECK constraint IS added beyond the
--   design - see section 1a and the RLS note below for why.
--
-- ROW LEVEL SECURITY - THE DECISION AND WHY
--   A registry snapshot is PLATFORM-GLOBAL, not per-tenant. There is no
--   user_id, no strategy_id and no tenant-derived value anywhere in the
--   table, and nothing tenant-specific in the payload: descriptors are
--   block ids, port types, parameter specifications and the compatibility
--   matrix - the same bytes for every user, and already served in full to
--   every authenticated caller by GET /api/strategy-operations/registry/
--   blocks. So there is nothing here for a per-tenant USING clause to
--   scope, and a policy modelled on the strategy_versions ones would be
--   theatre: it would filter on a column that does not exist.
--
--   design.md's own RLS section (section 6 of the migration) enables RLS on
--   training_jobs and model_versions and DELIBERATELY omits this table,
--   which is consistent with "platform-global". Taken literally that would
--   leave the table with RLS off - and a Supabase table in the public
--   schema with RLS off is readable AND WRITABLE by anyone holding the anon
--   key. This file is therefore STRICTER than the design: RLS is enabled
--   and the access list below is the whole of it.
--
--     * SELECT to the authenticated role, USING (true). Stated plainly:
--       ANY LOGGED-IN USER CAN READ EVERY SNAPSHOT. That is deliberate and
--       exposes nothing new - it is the same descriptor set the registry
--       endpoint already returns to them, and there is no tenant data in
--       the table to leak.
--     * INSERT to the authenticated role, WITH CHECK (true), so the
--       existing request-scoped client can record a snapshot on the save
--       path. No new elevated credential is introduced anywhere: the
--       writer uses the caller's own RLS-scoped client, exactly as
--       strategy_service does for strategy_versions.
--     * NO UPDATE policy and NO DELETE policy. The table is APPEND-ONLY to
--       every user. Combined with the PRIMARY KEY this makes it
--       first-writer-wins: an existing snapshot can never be altered or
--       removed through the API, so the provenance of a version that names
--       an already-recorded registry_version cannot be rewritten.
--     * anon gets nothing at all: no policy, and its grants are revoked.
--
--   The residual risk, stated rather than hidden: registry_version is a
--   CONTENT HASH, so a row is the claim "this descriptor set hashes to this
--   value", and SQL cannot recompute the hash to verify it. An
--   authenticated user can therefore insert a row under a key the backend
--   has not recorded yet. Three things bound that:
--       - chk_brs_descriptors_shape below requires the payload to be an
--         object carrying a blocks array and a registry_version that
--         MATCHES the row key, so mismatched junk is rejected outright;
--       - append-only means an already-recorded snapshot is immutable, so
--         the only reachable target is a registry_version the backend has
--         never written, which no existing version row references;
--       - the backend records the snapshot on the same assembly that serves
--         the hash to the client, so the window on a brand-new
--         registry_version is the first save after a deploy.
--     The alternative - denying INSERT and writing through the service role
--     - closes that window but puts an RLS-bypassing credential on the
--     strategy save path, which is a larger and permanently open surface
--     than the one it removes. Append-only-for-everyone is the smaller
--     exposure, and it is the one taken.
--
--   No existing table's RLS is touched: this file creates two policies, on
--   the new table only, and runs no ENABLE/DISABLE ROW LEVEL SECURITY
--   against any pre-existing relation.
--
-- SAFETY
--   * Additive only. No DROP, no TRUNCATE, no DELETE, no UPDATE, no
--     ALTER COLUMN TYPE. There is no data-loss path in this file.
--   * Fully idempotent. CREATE TABLE IF NOT EXISTS; the CHECK constraint is
--     added only when pg_constraint holds none of that name; each policy is
--     created only when pg_policies holds none of that name; ENABLE ROW
--     LEVEL SECURITY is a no-op when already enabled; GRANT and REVOKE are
--     naturally idempotent. A second run changes nothing.
--   * REVOKE here only ever narrows privileges on the table this file
--     creates. It cannot affect another relation and cannot remove a row.
--   * NOT NULL on descriptors and created_at is safe on a table that starts
--     empty, and created_at carries a DEFAULT.
--   * One transaction: either the table, its CHECK, its RLS, its policies
--     and its grants all exist, or none of them do. A half-secured table is
--     never visible.
--
-- APPLICATION
--   NOT applied automatically. .github/workflows/03-deploy.yml has no
--   migration step, so migrations are applied by hand, per file, the way
--   scripts/forensics/apply_migration_007.py applied 007. Apply this file
--   explicitly against the target database and then run the verification
--   queries at the bottom.
--
--   Until it is applied the backend does NOT fail: the snapshot writer
--   probes for the table once, degrades to a warning naming this file, and
--   the registry endpoints and the save path keep working. Requirement 4.16
--   is simply unmet until the DDL lands - which is exactly what the warning
--   says.

BEGIN;

-- 0) Preflight ---------------------------------------------------------
-- Read-only assertion. The policy below is granted to the Supabase
-- 'authenticated' role; on a database where that role does not exist,
-- CREATE POLICY would fail with a bare undefined_object. Fail with a
-- readable message instead. (003_signal_trace_restoration.sql assumes the
-- same Supabase roles by using auth.uid() throughout.)
-- Idempotent: it reads catalogues and writes nothing.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        RAISE EXCEPTION
            '004 part 2 precondition failed: role "authenticated" does not '
            'exist. This migration targets a Supabase database, where that '
            'role and the anon role are created by the platform.';
    END IF;
END $$;

-- 1) The snapshot table ------------------------------------------------
-- design.md "Migration 004_strategy_builder_canonical.sql", section 5,
-- verbatim, schema-qualified.
--
--   registry_version  deterministic hash of the assembled descriptor set,
--                     e.g. 'r_3d173598'. TEXT, matching
--                     strategy_versions.registry_version from part 1 so the
--                     two join without a cast. PRIMARY KEY: one row per
--                     descriptor set, which is what lets the writer use
--                     ON CONFLICT DO NOTHING and stay idempotent across
--                     processes, restarts and concurrent requests.
--   descriptors       BlockRegistry.to_dict() - the whole served payload,
--                     so a snapshot answers "what was this author offered"
--                     without needing the code that produced it.
--   created_at        first time this descriptor set was recorded.
--
-- Deliberately NOT here: a foreign key from strategy_versions.
-- registry_version to this table. A version must be savable before its
-- snapshot has been recorded (the snapshot write is best-effort and this
-- migration may not be applied yet), so a FK would turn a provenance gap
-- into a failed save - the exact inversion of priorities this design
-- avoids.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS.
CREATE TABLE IF NOT EXISTS public.block_registry_snapshots (
    registry_version TEXT PRIMARY KEY,
    descriptors      JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2) Shape assertion ---------------------------------------------------
-- CREATE TABLE IF NOT EXISTS is silent when a table of that name already
-- exists in a DIFFERENT shape - for instance one created by hand during an
-- investigation. That silence would move the failure to the first snapshot
-- write, at runtime, in production. Assert the three columns instead, so a
-- mismatch is a readable migration-time error.
-- Idempotent: it reads catalogues and writes nothing.
DO $$
DECLARE
    missing TEXT;
BEGIN
    SELECT string_agg(expected.column_name, ', ' ORDER BY expected.column_name)
      INTO missing
      FROM (VALUES ('registry_version'), ('descriptors'), ('created_at'))
             AS expected(column_name)
     WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns c
         WHERE c.table_schema = 'public'
           AND c.table_name   = 'block_registry_snapshots'
           AND c.column_name  = expected.column_name
     );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION
            'public.block_registry_snapshots exists but is missing column(s): %. '
            'A pre-existing table of that name has a different shape; reconcile '
            'it by hand before re-running this migration.', missing;
    END IF;
END $$;

-- 3) Payload shape -----------------------------------------------------
-- The one constraint added beyond design.md's three columns, and the reason
-- is the RLS decision in the header: authenticated users may INSERT, so the
-- table needs a floor on what a row can claim. It runs after the column
-- assertion above, so a pre-existing table of the wrong shape has already
-- failed with a readable message rather than a confusing 42703 from inside
-- ADD CONSTRAINT.
--
--   jsonb_typeof(descriptors) = 'object'                it is a payload
--   jsonb_typeof(descriptors->'blocks') = 'array'       it has descriptors
--   descriptors->>'registry_version' = registry_version the key matches the
--                                                       payload's own idea
--                                                       of its key
--
-- All three hold for BlockRegistry.to_dict(), which is the only thing the
-- writer sends. Same spirit as part 1's chk_graph_shape: never store a
-- document we cannot read back.
--
-- Idempotent: added only when pg_constraint holds no constraint of that
-- name on this table. PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS, and
-- a DROP-then-ADD would break this file's no-DROP rule.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.block_registry_snapshots'::regclass
           AND conname  = 'chk_brs_descriptors_shape'
    ) THEN
        ALTER TABLE public.block_registry_snapshots
            ADD CONSTRAINT chk_brs_descriptors_shape
                CHECK (jsonb_typeof(descriptors) = 'object'
                       AND jsonb_typeof(descriptors->'blocks') = 'array'
                       AND descriptors->>'registry_version' = registry_version);
    END IF;
END $$;

-- 4) Row level security ------------------------------------------------
-- Enable first, then add the two policies. Order matters: enabling RLS is
-- default-deny, so between these statements the table is unreachable rather
-- than open - and all of them are inside the transaction, so no session
-- ever observes the intermediate state.
--
-- Idempotent: ENABLE ROW LEVEL SECURITY is a no-op when RLS is already on
-- (this is the same unguarded form 003_signal_trace_restoration.sql uses).
ALTER TABLE public.block_registry_snapshots ENABLE ROW LEVEL SECURITY;

-- Exactly two policies: global read and append. No UPDATE policy and no
-- DELETE policy, which is what makes the table append-only to every user.
-- See "ROW LEVEL SECURITY" in the header for why there is no tenant
-- predicate.
--
-- Idempotent: each is guarded on pg_policies by schemaname + tablename +
-- policyname, matching the pattern in 003_signal_trace_restoration.sql
-- section 9. PostgreSQL has no CREATE POLICY IF NOT EXISTS.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'block_registry_snapshots'
           AND policyname = 'brs_authenticated_read'
    ) THEN
        CREATE POLICY "brs_authenticated_read"
            ON public.block_registry_snapshots
            FOR SELECT
            TO authenticated
            USING (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename  = 'block_registry_snapshots'
           AND policyname = 'brs_authenticated_append'
    ) THEN
        CREATE POLICY "brs_authenticated_append"
            ON public.block_registry_snapshots
            FOR INSERT
            TO authenticated
            WITH CHECK (true);
    END IF;
END $$;

-- 5) Grants ------------------------------------------------------------
-- Defence in depth behind RLS. Supabase grants the public schema's tables
-- to anon and authenticated by default, which would give anon full DML on
-- this table the moment anyone ran
-- ALTER TABLE ... DISABLE ROW LEVEL SECURITY. Narrowing the grants means
-- RLS is not the only thing standing between an anon API key and this
-- table, and it makes the append-only intent true at the privilege layer
-- as well as the policy layer.
--
-- REVOKE here only narrows privileges on the table created above. It
-- removes no row and touches no other relation.
--
-- Idempotent: GRANT and REVOKE converge on the same privilege set however
-- many times they run.
REVOKE ALL ON public.block_registry_snapshots FROM anon;
REVOKE ALL ON public.block_registry_snapshots FROM authenticated;

-- Read and append for logged-in users. Note what is NOT granted: no
-- UPDATE, no DELETE, no TRIGGER, no REFERENCES.
GRANT SELECT, INSERT ON public.block_registry_snapshots TO authenticated;

-- The backend's own role, for maintenance reads and any future backfill.
-- service_role bypasses RLS, but it still needs the table privilege, so
-- state it rather than relying on default privileges.
GRANT SELECT, INSERT ON public.block_registry_snapshots TO service_role;

COMMIT;

-- VERIFICATION (run after applying) ------------------------------------
--
-- 1. Columns. Expect exactly 3 rows: created_at (timestamp with time zone,
--    NO, now()), descriptors (jsonb, NO), registry_version (text, NO).
--
--   SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'public'
--     AND table_name   = 'block_registry_snapshots'
--   ORDER BY column_name;
--
-- 2. Constraints. Expect 2 rows: the PRIMARY KEY on registry_version and
--    chk_brs_descriptors_shape. The primary key is what the writer's
--    ON CONFLICT DO NOTHING relies on; without it the write would not be
--    idempotent.
--
--   SELECT conname, contype, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--   WHERE conrelid = 'public.block_registry_snapshots'::regclass
--   ORDER BY conname;
--
-- 3. RLS on, and exactly two policies:
--      brs_authenticated_read    cmd SELECT, roles {authenticated},
--                                qual true,  with_check NULL
--      brs_authenticated_append  cmd INSERT, roles {authenticated},
--                                qual NULL,  with_check true
--    A row with cmd UPDATE, DELETE or ALL appearing here would mean the
--    table is no longer append-only, which is the property that keeps a
--    recorded snapshot immutable. A policy naming the public role would
--    mean anon can reach it.
--
--   SELECT relrowsecurity, relforcerowsecurity
--   FROM pg_class WHERE oid = 'public.block_registry_snapshots'::regclass;
--
--   SELECT policyname, cmd, roles, qual, with_check
--   FROM pg_policies
--   WHERE schemaname = 'public' AND tablename = 'block_registry_snapshots'
--   ORDER BY policyname;
--
-- 4. Grants. Expect SELECT + INSERT for authenticated, SELECT + INSERT for
--    service_role, and NOTHING for anon. No UPDATE and no DELETE for any
--    grantee.
--
--   SELECT grantee, privilege_type
--   FROM information_schema.role_table_grants
--   WHERE table_schema = 'public'
--     AND table_name   = 'block_registry_snapshots'
--   ORDER BY grantee, privilege_type;
--
-- 5. Existing RLS untouched. strategy_versions must still show the same 3
--    policies it had before (SELECT / INSERT / UPDATE, each scoped through
--    strategies.user_id = auth.uid()) and rowsecurity = true.
--
--   SELECT policyname, cmd FROM pg_policies
--   WHERE schemaname = 'public' AND tablename = 'strategy_versions'
--   ORDER BY policyname;
--
-- 6. Idempotency. Re-running this whole file must report no error and leave
--    the counts in 1, 2, 3 and 4 unchanged.
--
-- 7. Provenance join, once the writer has run. Every registry_version a
--    version row names should eventually appear here. Rows listed by this
--    query are versions whose snapshot was never recorded - expected while
--    this migration is unapplied, and worth investigating afterwards.
--
--   SELECT DISTINCT sv.registry_version
--   FROM public.strategy_versions sv
--   LEFT JOIN public.block_registry_snapshots brs
--          ON brs.registry_version = sv.registry_version
--   WHERE sv.registry_version IS NOT NULL
--     AND brs.registry_version IS NULL;
