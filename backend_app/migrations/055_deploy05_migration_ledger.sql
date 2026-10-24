-- ============================================================================
--  055 - DEPLOY-05: the migration ledger, and it records EVIDENCE not intent
-- ============================================================================
--
--  THE GAP
--  -------
--  DEPLOY-05: "THE MIGRATION LEDGER ITSELF -- still a DESIGN TASK, not a
--  reconciliation. Nothing declares recorded_migrations anywhere in the repository;
--  public.alembic_version holds a single revision pointer (d97ffff9c3bb) and
--  supabase_migrations.schema_migrations holds ONE row against 50+ migration files.
--  Decide whether to adopt the Supabase table, adopt Alembic properly, or build
--  something -- and then backfill from EVIDENCE by probing for each migration's
--  declared objects."
--
--  THE DECISION: BUILD recorded_migrations. Here is why the other two were rejected,
--  because the reason is the whole design.
--
--  NOT ALEMBIC. `alembic_version` holds ONE revision pointer, and that shape carries a
--  specific claim: "every revision up to this one has been applied", i.e. the applied
--  set is a PREFIX of the revision sequence. Production disproves that claim directly --
--  DEPLOY-05 records that 037 was applied while 017 was not. A single pointer is
--  structurally incapable of expressing a non-prefix reality, so adopting it would mean
--  either recording something false or abandoning the prefix semantics that are the only
--  thing giving the pointer meaning. `alembic_version` is therefore left exactly as it
--  is, pointing at d97ffff9c3bb, and is NOT treated as the ledger.
--
--  NOT supabase_migrations.schema_migrations. That is the Supabase CLI's own bookkeeping
--  table, and it holds one row against 50+ files precisely because the CLI is not what
--  applies these migrations. Writing rows the CLI did not write would make its future
--  behaviour unpredictable -- a later `supabase db push` would reason from entries it has
--  no basis for -- and would couple this platform's deploy record to another tool's
--  internal schema. Left untouched.
--
--  SO: a table with ONE ROW PER MIGRATION, which is the only shape that can say "037 yes,
--  017 no". The name is not invented either: DEPLOY-04 already claimed to write to
--  `recorded_migrations`, so it is already this platform's vocabulary for the thing -- it
--  just never existed, which is how DEPLOY-04's close-out claim came to be unverifiable.
--
--  WHAT MAKES THIS DIFFERENT FROM THE FALSE CONFIDENCE IT REPLACES
--  ---------------------------------------------------------------
--  DEPLOY-05 warns about exactly the failure mode a ledger invites: "a half-working SQL
--  parser that silently missed a declaration would restore exactly the false confidence
--  this replaces." Three properties answer that, and they are enforced by the schema
--  rather than by whoever writes to it:
--
--   1. A ROW RECORDS EVIDENCE, NOT INTENT. `verification` names HOW the row was
--      established and `evidence` says WHAT was checked. A row cannot be written without
--      both, so "we ran the file" and "we confirmed the objects exist" are never the same
--      statement.
--   2. ABSENCE MEANS UNVERIFIED, NEVER "NOT APPLIED". There is no row saying a migration
--      is absent, deliberately: this ledger is backfilled from probing a hand-maintained
--      inventory, and that inventory does not cover all 50+ files. A missing row means
--      nobody has checked, which is the honest reading and the one that keeps the next
--      "migrations N through M are closed out" claim falsifiable.
--   3. THE EVIDENCE IS NAMED OBJECTS. `objects_verified` counts what was probed, so a row
--      backed by zero probed objects is visibly worthless rather than quietly reassuring.
--
--  Read-only to every client. This is operator/deploy bookkeeping, no tenant owns a row,
--  and nothing in the request path reads it.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS public.recorded_migrations (
    -- The migration's filename as it appears in the repository, which is the only stable
    -- identifier these files have: they are raw .sql applied out of band, with no
    -- revision id and no embedded version.
    filename            TEXT        NOT NULL,

    -- HOW this row was established. Not free text: the two values mean different things
    -- and conflating them is how DEPLOY-04's claim became unverifiable.
    --   EVIDENCE_PROBED - the objects this migration declares were queried in THIS
    --                     database and found present. Says nothing about who ran it or
    --                     when, because that information does not exist for a file
    --                     applied out of band.
    --   APPLIED_BY_TOOL - a deploy process ran this file and recorded doing so. A
    --                     stronger provenance claim, and only writable by something that
    --                     actually performed the apply.
    verification        TEXT        NOT NULL,

    -- WHAT was checked, in words. A row whose evidence does not describe a check is the
    -- thing this table exists to prevent, so it is NOT NULL and non-empty.
    evidence            TEXT        NOT NULL,

    -- How many declared objects were confirmed. Zero is permitted and is meant to be
    -- read as "this row proves nothing" rather than rejected, because a migration that
    -- declares no inspectable object (a pure GRANT, a COMMENT) is a real case.
    objects_verified    INTEGER     NOT NULL DEFAULT 0,

    -- Who or what wrote the row. An operator identity or a tool name, for audit.
    recorded_by         TEXT        NOT NULL,

    -- When the row was written. NOT when the migration was applied -- that is unknown
    -- for every backfilled row, and a column that invited a guess would be filled with
    -- one.
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_recorded_migrations PRIMARY KEY (filename),
    CONSTRAINT chk_recorded_migrations_verification
        CHECK (verification IN ('EVIDENCE_PROBED', 'APPLIED_BY_TOOL')),
    CONSTRAINT chk_recorded_migrations_evidence_not_blank
        CHECK (length(btrim(evidence)) > 0),
    CONSTRAINT chk_recorded_migrations_objects_not_negative
        CHECK (objects_verified >= 0)
);

COMMENT ON TABLE public.recorded_migrations IS
    'DEPLOY-05. One row per migration whose application has been ESTABLISHED, with how '
    'it was established. The absence of a row means NOBODY HAS CHECKED -- never that the '
    'migration is unapplied. Backfilled by scripts/record_migration_evidence.py from the '
    'hand-maintained inventory in scripts/check_production_schema_drift.py, which does '
    'not cover every file; see that script for why the inventory is deliberately not '
    'parsed out of the SQL. public.alembic_version is NOT the ledger: its single-pointer '
    'shape asserts the applied set is a prefix of the revision sequence, which production '
    'disproves (037 applied, 017 not).';

COMMENT ON COLUMN public.recorded_migrations.verification IS
    'EVIDENCE_PROBED (the declared objects were found in this database) or '
    'APPLIED_BY_TOOL (a deploy process ran the file and said so). Kept distinct because '
    '"we ran it" and "we confirmed it" are different claims, and DEPLOY-04 conflated them.';

COMMENT ON COLUMN public.recorded_migrations.recorded_at IS
    'When this ROW was written, not when the migration was applied. The latter is unknown '
    'for every file applied out of band, and a column for it would be filled with a guess.';

-- Queried by filename (the PK) and, for reporting, by how a row was established.
CREATE INDEX IF NOT EXISTS idx_recorded_migrations_verification
    ON public.recorded_migrations (verification);

-- ── RLS: nobody reads this from a request ───────────────────────────────────
--
-- Enabled with NO permissive policy, which denies every non-superuser, non-owner read.
-- That is the intended posture rather than an omission: this is deploy bookkeeping, no
-- tenant owns a row, and the only readers are the service-role scripts that write it.
-- Stated explicitly because an RLS-enabled table with no policy is easy to mistake for
-- an unfinished one.
ALTER TABLE public.recorded_migrations ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.recorded_migrations FROM anon, authenticated;

COMMIT;
