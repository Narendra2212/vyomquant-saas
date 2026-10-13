"""scripts/provision_database.py — provision this application's schema from source.

WHY THIS EXISTS
---------------
Before this script there was **no single command that could stand up this schema**, and
``backend_app/migrations/020_declare_pre_existing_tables.sql`` says so in its own header:
*"THIS REPOSITORY CANNOT PROVISION ITS OWN DATABASE. There is no disaster-recovery
rebuild and no way to stand up staging from source."*

The pieces existed but nothing joined them:

* ``backend_app/migrations/PROVISIONING_ORDER.md`` documents the order that works,
  including the fact that ``020`` is numbered last and must run **first**.
* ``scripts/apply_migrations.py`` applies only ``006``–``014`` of one directory.
* Nothing ran ``alembic upgrade`` — not the Dockerfile, not ``startup.sh``, not
  ``Procfile``, not any GitHub workflow, not any script. Verified by search.
* Nothing recorded which files an environment had already applied, so the set was
  tracked by hand.

This script implements ``PROVISIONING_ORDER.md`` end to end and records what it applied.

THE ORDER, as that document prescribes it
-----------------------------------------
    Step 0   020_declare_pre_existing_tables.sql        (first, despite its number)
    Step 1   backend_app/migrations/001..019, then migrations/*
    Step 2   alembic upgrade d97ffff9c3bb               (the branchpoint, NOT head)
    Step 3   020 again, so strategies_source_library_id_fkey can land

THE ORDER THIS SCRIPT ACTUALLY USES, and the two places it differs
------------------------------------------------------------------
    Step 0   020_declare_pre_existing_tables.sql
    Step 0c  alembic upgrade head                       (BEFORE the SQL set, and head)
    Step 0b  archived_migrations/root_migrations/*.sql   (less the two alembic owns)
    Step 1   backend_app/migrations/001..019, then migrations/*
    Step 3   020 again

1. Alembic moves ahead of the SQL set. The document's step 2 placement is circular:
   ``migrations/006_reconcile_production_database.sql`` ALTERs ``execution_records``,
   which only alembic declares, so the SQL set cannot run first. Proven both ways.

2. Alembic goes to ``head``, not the branchpoint. The document's obstacle 1 and the CI
   ``database-tests`` job both say head is unreachable. It was. The stated cause --
   ``add_foreign_keys`` needing a ``profiles`` table no migration declares -- does not
   apply here, because step 0 declares it first. The real cause was a
   ``DatatypeMismatch`` inside ``add_foreign_keys`` that aborted the transaction and
   took alembic's version bump down with it; see ALEMBIC_TARGET below. Reaching head is
   what installs the nine tenant-isolation RLS policies on the execution and financial
   tables, which no database built by migration previously had.

``4ef23035a692_baseline.py``'s ``upgrade()`` containing only DROPs -- its ``CREATE
TABLE``s are in ``downgrade()`` -- is still true and still means the baseline
provisions nothing on its own. It is not, on its own, a reason to stop short of head.

SAFETY MODEL
------------
  * ``--dry-run`` is the DEFAULT. Nothing executes without ``--apply``.
  * DSN comes from ``--dsn`` or ``PROVISION_DATABASE_URL``. No hardcoded default, and
    the DSN is never printed or logged.
  * **Refuses any DSN that is not localhost unless ``--i-understand-this-is-remote`` is
    passed.** This tool creates roles and applies DDL; pointing it at production by
    accident is the failure mode worth the most friction.
  * Refuses port 6543 (Supabase's transaction-mode pooler): these files use
    multi-statement ``DO $$ ... $$`` blocks and explicit ``BEGIN``/``COMMIT`` and need a
    direct session.
  * One file per transaction, with the file's own ``BEGIN``/``COMMIT`` left exactly as
    written.
  * Records every applied file in ``public.schema_migrations`` with a SHA-256 of its
    contents, so a re-run skips what is already applied and **refuses if a file has
    changed since it was applied**.
  * Aborts on the first failure, naming the file and the driver's error.

USAGE
-----
    # Dry run against a local staging database
    python scripts/provision_database.py --dsn postgresql://...@127.0.0.1:5432/postgres

    # Apply, creating the Supabase-like substrate first (non-Supabase targets only)
    python scripts/provision_database.py --dsn ... --apply --create-substrate

EXIT CODES
----------
    0   completed (dry run, or apply with every step succeeding)
    2   refused before executing anything
    4   a step failed while applying
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import subprocess
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The Alembic revision to upgrade to.
#:
#: This was ``d97ffff9c3bb`` -- the branchpoint -- on the grounds PROVISIONING_ORDER.md
#: obstacle 1 and the CI ``database-tests`` job both give: ``upgrade head`` cannot
#: provision a fresh database. That was true, and is no longer, so stopping short now
#: costs real coverage.
#:
#: WHAT WAS ACTUALLY BLOCKING HEAD. Not the stated cause. The cited reason is that
#: ``add_foreign_keys`` references ``profiles(id)`` and no migration declares
#: ``profiles`` -- but step 0 below applies ``020_declare_pre_existing_tables.sql``,
#: which does declare it, before alembic runs, so that cause is absent here. Running it
#: anyway surfaced the real one: ``execution_records.task_id`` is ``uuid`` while
#: ``dag_tasks.task_id`` is ``varchar(36)``, both from ``d97ffff9c3bb`` itself, so the
#: first FK fails with ``DatatypeMismatch``, PostgreSQL aborts the transaction, and
#: alembic's own ``UPDATE alembic_version`` dies with ``InFailedSqlTransaction``. The
#: revision's per-statement ``try/except`` could not help: catching the error does not
#: un-abort the transaction. ``add_foreign_keys_20260817.py`` now gives each constraint
#: its own SAVEPOINT, which is what its author's ``try/except`` was reaching for, so the
#: five constraints the schema cannot support warn and are skipped while the revision
#: commits.
#:
#: WHY THIS MATTERS MORE THAN THE FKs. ``implement_rls_policies`` sits immediately
#: downstream of ``add_foreign_keys`` on the only path to head. While head was
#: unreachable, the nine tenant-isolation policies it declares -- orders, positions,
#: fills, dag_tasks, execution_records, idempotency_keys, transaction_records,
#: transaction_checkpoints, reconciliation_mismatches, i.e. the execution and financial
#: tables -- were installed on no database built by migration, and those tables carried
#: no row filtering at all. Verified absent by ``staging/rls_inventory.py`` before this
#: change and present after.
ALEMBIC_TARGET = "head"

#: Applied first despite its number — the only declaration of ``public.profiles``,
#: ``public.strategies`` and ``public.processed_orders``, which nine later files ALTER.
STEP0_FILE = ROOT / "backend_app" / "migrations" / "020_declare_pre_existing_tables.sql"

#: Applied LAST. Sets what the browser's ``anon`` and ``authenticated`` roles may do, and
#: aborts if any per-customer table is still readable by ``anon``. See STEP 4 for why
#: this cannot live in the numbered set.
HARDENING_FILE = ROOT / "migrations" / "hardening" / "999_privilege_hardening.sql"

#: Applied as STEP 3b, between the numbered SQL set and the privilege hardening.
#:
#: WHY IT CANNOT BE LEFT TO ALEMBIC. Alembic `d4e5f6a7b8c9` performs the same BUG-049
#: conversion and is the right mechanism for a database that already exists -- but on
#: this path alembic runs at STEP 0c, BEFORE the numbered SQL set, and 9 of its 12 target
#: tables do not exist yet. Its `_table_exists` guard correctly skips them, and a freshly
#: provisioned database came out with 9 columns still `text` and 9 foreign keys missing.
#: Measured, not inferred: `scripts/verify_production_schema.py` reported
#:     drift/columns      9 missing, 9 unexpected
#:     drift/foreign_keys 0 missing, 9 unexpected
#: against a migrated database. See the header of the SQL file for the full reasoning.
BUG049_FILE = ROOT / "migrations" / "hardening" / "998_bug049_identity_types.sql"

TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    filename     text        PRIMARY KEY,
    sha256       text        NOT NULL,
    applied_at   timestamptz NOT NULL DEFAULT now(),
    step         text        NOT NULL
);
COMMENT ON TABLE public.schema_migrations IS
  'Which migration files this environment has applied, and the SHA-256 of each as '
  'applied. Written by scripts/provision_database.py. Before it existed the set was '
  'tracked by hand, which PROVISIONING_ORDER.md records as the reason a renamed file '
  'is invisible to an operator who already ran it.';
"""

#: The substrate Supabase provides and a bare PostgreSQL does not. ``020``'s preflight
#: refuses to run without the ``authenticated`` and ``service_role`` roles and
#: ``auth.uid()``, because the row level security it reproduces cannot be expressed
#: without them (PROVISIONING_ORDER.md obstacle 5).
#:
#: This is the PLATFORM, not the application schema. Creating it is what makes a
#: non-Supabase target a faithful stand-in; it is not a fabricated application schema.
SUBSTRATE_DDL = """
-- pgcrypto. Supabase enables it by default, which is why nothing in this repository
-- declared it and why the dependency was invisible until a plain PostgreSQL was used.
--
-- `migrations/referral_system_redesign.sql` defines generate_unique_referral_code()
-- around `gen_random_bytes(4)`, and `trigger_create_referral_code` on `public.profiles`
-- calls it, so without pgcrypto every INSERT INTO public.profiles fails -- every user
-- signup. That file now issues CREATE EXTENSION itself, but it runs late in the numbered
-- set and `009_paper_trading.sql`'s preflight (correctly) refuses earlier if the
-- extension is missing. The platform is the right layer for it: the substrate exists to
-- supply what Supabase supplies.
--
-- THE FALLBACK IS A STAGING STAND-IN AND IS NOT CRYPTOGRAPHIC. Some PostgreSQL
-- distributions ship no pgcrypto at all -- the `pgserver` wheel used for local
-- verification carries only `plpgsql` and `vector` -- so CREATE EXTENSION is attempted
-- and, if the control file is absent, a `random()`-based `gen_random_bytes` is defined
-- instead. `random()` is a PRNG, not a CSPRNG: referral codes derived from it are
-- guessable. That is acceptable for a disposable verification database and is NOT
-- acceptable anywhere else, which is why this whole block lives behind
-- `--create-substrate` and is documented as emulating the platform rather than being it.
-- Supabase and any ordinary PostgreSQL package both take the real branch.
DO $pgcrypto$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS pgcrypto;
        RAISE NOTICE 'substrate: pgcrypto extension present';
    EXCEPTION WHEN feature_not_supported OR undefined_file THEN
        RAISE WARNING 'substrate: pgcrypto is not available on this server (%)',
                      SQLERRM;
    END;

    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p WHERE p.proname = 'gen_random_bytes'
    ) THEN
        RAISE WARNING 'substrate: defining a NON-CRYPTOGRAPHIC gen_random_bytes() '
                      'stand-in so provisioning and tests can run. Never use this '
                      'database for anything but verification.';
        EXECUTE $shim$
            CREATE OR REPLACE FUNCTION public.gen_random_bytes(n integer)
            RETURNS bytea LANGUAGE sql VOLATILE AS $body$
                SELECT decode(
                    string_agg(lpad(to_hex((random() * 255)::int), 2, '0'), ''),
                    'hex'
                )
                FROM generate_series(1, n)
            $body$;
        $shim$;
    END IF;
END $pgcrypto$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        CREATE ROLE service_role NOLOGIN BYPASSRLS;
    END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS auth;

-- auth.users is Supabase's own identity table. The application's migrations reference
-- it with foreign keys (`REFERENCES auth.users(id)`), so a non-Supabase target needs it
-- to exist before any of them will apply. Only the columns the application's FKs and
-- policies actually depend on are declared: this stands in for the platform, it does not
-- reimplement it.
CREATE TABLE IF NOT EXISTS auth.users (
    id         uuid PRIMARY KEY,
    email      text,
    created_at timestamptz DEFAULT now()
);

-- Supabase derives this from the request JWT. In staging it reads a session GUC so a
-- test can adopt an identity with `SET LOCAL request.jwt.claim.sub = '<uuid>'`, which
-- is how the real one behaves from a policy's point of view.
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION auth.role() RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(NULLIF(current_setting('request.jwt.claim.role', true), ''),
                    'authenticated')
$$;

-- Supabase exposes the whole verified claim set as jsonb. Policies in the numbered set
-- read it (e.g. `auth.jwt() -> 'app_metadata' ->> 'role'`), so a non-Supabase target
-- needs it or those CREATE POLICY statements fail.
CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        NULLIF(current_setting('request.jwt.claims', true), '')::jsonb,
        jsonb_build_object(
            'sub',  NULLIF(current_setting('request.jwt.claim.sub',  true), ''),
            'role', COALESCE(NULLIF(current_setting('request.jwt.claim.role', true), ''),
                             'authenticated')
        )
    )
$$;

GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA auth   TO anon, authenticated, service_role;

-- DEFAULT PRIVILEGES, AND WHY OMITTING THEM MADE RLS UNTESTABLE.
--
-- Supabase's bootstrap grants `anon`, `authenticated` and `service_role` privileges on
-- everything in `public`, and installs ALTER DEFAULT PRIVILEGES so tables created later
-- inherit them. That is why most of this repository's tables carry no GRANT statement
-- and the application still works: on the PostgREST path the privilege is already
-- there, and ROW LEVEL SECURITY -- not table privilege -- is what separates one
-- customer's rows from another's.
--
-- Without these lines the substrate was not a faithful stand-in. `authenticated` held
-- no privilege on `strategies`, `signals`, `strategy_backtests` or `orders`, so a
-- cross-tenant SELECT failed with `permission denied for table ...` rather than
-- returning zero rows. That looks like isolation and is not: it is a missing GRANT, and
-- reporting it as working tenant isolation would have been wrong. Tests have to be able
-- to reach a table before a policy on it can be said to hold.
--
-- Stating it the other way round is the uncomfortable half: in production these
-- privileges ARE present, so any `public` table whose RLS is off is readable in full by
-- any holder of the anon key, which ships in the browser. `staging/exposure_check.py`
-- is what that observation turned into.
--
-- FOR ROLE postgres because `postgres` is what runs this script and therefore creates
-- every table that follows; default privileges are recorded per granting role.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT ALL ON TABLES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT ALL ON SEQUENCES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT ALL ON FUNCTIONS TO anon, authenticated, service_role;

-- THERE IS DELIBERATELY NO `GRANT ALL ON ALL TABLES IN SCHEMA public` HERE.
--
-- There was, to "cover anything already created when the substrate is applied to a
-- non-empty target", and it made provisioning non-idempotent in the dangerous direction.
-- `scripts/verify_provisioning.py` provisions an empty database twice and compares the
-- two schemas; with that statement present, run 2 differed from run 1 by 787 privilege
-- rows -- all of them ADDED. Among them `anon` holding DELETE, INSERT, SELECT and
-- REFERENCES on `alembic_version`.
--
-- The mechanism: on a second run every table already exists, so the blanket GRANT
-- re-opened all 85 of them, while every file that had revoked something -- the hardening
-- file, and migrations like `004b_block_registry_snapshots.sql` with its
-- `REVOKE ALL ON public.block_registry_snapshots FROM anon` -- was skipped as
-- already-applied. A re-provision therefore left the database MORE permissive than a
-- first-time provision, which is the worst possible direction for an operation an
-- operator would reasonably repeat, and exactly the kind of thing that is never noticed
-- because nobody provisions twice and then diffs the privileges.
--
-- Removing it costs nothing. `ALTER DEFAULT PRIVILEGES` above already grants the
-- Supabase defaults to every table created AFTER the substrate runs, which is every
-- table on a fresh provision -- the case the substrate exists for. On a re-provision the
-- existing tables keep the posture the previous run established, which is the definition
-- of idempotent.
--
-- Sequences are left alone for the same reason; the default-privileges line covers new
-- ones.
"""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _archived_root_migrations() -> list[pathlib.Path]:
    """Step 0b — the marketplace/library tables the numbered set depends on.

    ``archived_migrations/root_migrations/`` is NOT actually archived: it holds the only
    declaration of ``library_strategies``, ``library_ratings``,
    ``library_subscriptions``, ``deployment_permissions`` and the creator/marketplace
    analytics tables, and the numbered set requires them.

    THIS STEP IS MISSING FROM PROVISIONING_ORDER.md, and its absence makes that
    document's order circular. The document places ``library_strategies`` in step 2
    (Alembic ``e88f9911b5a2``), but step 1's
    ``backend_app/migrations/007_marketplace_submissions.sql`` has an explicit
    precondition that aborts without it:

        007 precondition failed: table public.library_strategies does not exist. It is
        the single authoritative Listing table (Requirement 1.1) ... Apply the
        library_strategies definition first
        (archived_migrations/root_migrations/001_create_library_strategies.sql, then
        migrations/007_add_marketplace_pricing_columns.sql).

    So step 1 cannot finish before step 2, and step 2 is documented as following step 1.
    Found by running the order: provisioning aborted at 007 with exactly that message.
    Applying this directory between 020 and the numbered set resolves it, and 007's own
    message is what prescribes the fix.

    Numeric order, with the duplicate numbers (two ``001``s, two ``002``s) broken by
    filename so the order is deterministic.

    TWO FILES IN THIS DIRECTORY ARE EXCLUDED, because Alembic owns their tables:
    ``001_create_dag_tasks_table.sql`` and ``002_create_execution_records.sql``.
    ``d97ffff9c3bb_execution_schema_rebuild`` declares ``dag_tasks`` and
    ``execution_records`` (PROVISIONING_ORDER.md's step-2 table), and applying the SQL
    versions first creates ``TYPE task_status`` and then step 2 aborts on
    ``42710 DuplicateObject`` / ``CREATE TYPE task_status AS ENUM (...)``.

    Measured, not assumed: including them took provisioning all the way to step 2 and
    failed there. The split is clean -- Alembic is authoritative for the execution
    schema (that is what its revision is named), and this directory is authoritative for
    the marketplace/library tables the numbered SQL set requires.
    """
    directory = ROOT / "archived_migrations" / "root_migrations"
    if not directory.is_dir():
        return []

    #: Owned by `d97ffff9c3bb_execution_schema_rebuild`, applied in step 2.
    ALEMBIC_OWNED = {
        "001_create_dag_tasks_table.sql",
        "002_create_execution_records.sql",
    }

    def key(p: pathlib.Path):
        m = re.match(r"^(\d+)", p.name)
        return (0, int(m.group(1)), p.name) if m else (1, 0, p.name)

    return sorted(
        (p for p in directory.glob("*.sql") if p.name not in ALEMBIC_OWNED), key=key
    )


def _numbered_sql_files() -> list[pathlib.Path]:
    """Step 1's files, in the order PROVISIONING_ORDER.md prescribes.

    ``backend_app/migrations/001``-``019`` in numeric order, then the files in
    ``migrations/``, then ``backend_app/migrations/021`` and up. ``020`` is
    excluded because it is step 0 and step 3.

    DB-01: the ``021``+ files were previously sorted straight after ``019``, i.e.
    before ``migrations/``. ``024`` ALTERs ``referral_commissions``, which is
    declared only by ``migrations/006_reconcile_production_database.sql`` /
    ``migrations/referral_system_redesign.sql``, so a fresh build failed with
    ``42P01 relation "public.referral_commissions" does not exist``. Every file
    in ``migrations/`` depends only on tables declared at or below ``019`` (plus
    step 0), so the later ``backend_app`` files go after them.
    """
    primary = sorted(
        p for p in (ROOT / "backend_app" / "migrations").glob("*.sql")
        if p.name != STEP0_FILE.name
    )
    secondary = sorted((ROOT / "migrations").glob("*.sql"))

    def key(p: pathlib.Path):
        m = re.match(r"^(\d+)", p.name)
        # Unnumbered files sort last within their directory, as the document says.
        return (0, int(m.group(1)), p.name) if m else (1, 0, p.name)

    primary = sorted(primary, key=key)
    early = [p for p in primary if int(re.match(r"^(\d+)", p.name).group(1)) < 20]
    late = [p for p in primary if int(re.match(r"^(\d+)", p.name).group(1)) > 20]
    return early + sorted(secondary, key=key) + late


def _refuse(message: str) -> None:
    print(f"REFUSED: {message}", file=sys.stderr)
    sys.exit(2)


def _validate_dsn(dsn: str, allow_remote: bool) -> None:
    parsed = urllib.parse.urlparse(dsn)
    host = parsed.hostname or ""
    port = parsed.port or 5432

    if port == 6543:
        _refuse(
            "DSN port is 6543, Supabase's transaction-mode pooler. These files use "
            "multi-statement DO blocks and explicit BEGIN/COMMIT and need a direct "
            "session on 5432."
        )

    local = host in ("127.0.0.1", "localhost", "::1", "")
    if not local and not allow_remote:
        _refuse(
            f"DSN host is not local. This tool creates roles and applies DDL; pass "
            f"--i-understand-this-is-remote to target a non-local database. (Host is "
            f"withheld from this message deliberately.)"
        )


def _applied(cur) -> dict[str, str]:
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name='schema_migrations'"
    )
    if not cur.fetchone():
        return {}
    cur.execute("SELECT filename, sha256 FROM public.schema_migrations")
    return dict(cur.fetchall())


def _apply_sql(conn, path: pathlib.Path, step: str, dry_run: bool, already: dict) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    digest = _sha256(text)
    name = f"{path.parent.name}/{path.name}"

    if name in already:
        if already[name] != digest:
            print(f"  REFUSED  {name}: applied previously with a different checksum.")
            print("           A migration file changed after being applied. Resolve by "
                  "hand; this tool will not re-apply a mutated file.")
            sys.exit(4)
        return "skipped (already applied)"

    if dry_run:
        return "would apply"

    with conn.cursor() as cur:
        try:
            cur.execute(text)
            cur.execute(
                "INSERT INTO public.schema_migrations (filename, sha256, step) "
                "VALUES (%s, %s, %s) ON CONFLICT (filename) DO NOTHING",
                (name, digest, step),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"  FAILED   {name}: {type(exc).__name__}: {exc}")
            sys.exit(4)
    return "applied"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--apply", action="store_true", help="execute (default is dry run)")
    ap.add_argument("--create-substrate", action="store_true",
                    help="create the Supabase-like roles and auth.uid(); non-Supabase targets only")
    ap.add_argument("--skip-alembic", action="store_true")
    ap.add_argument("--i-understand-this-is-remote", action="store_true")
    args = ap.parse_args()

    import os
    dsn = args.dsn or os.environ.get("PROVISION_DATABASE_URL")
    if not dsn:
        _refuse("no DSN. Pass --dsn or set PROVISION_DATABASE_URL.")
    _validate_dsn(dsn, args.i_understand_this_is_remote)

    mode = "APPLY" if args.apply else "DRY RUN"
    print("=" * 78)
    print(f"PROVISION DATABASE — {mode}")
    print("=" * 78)
    print(f"order source : backend_app/migrations/PROVISIONING_ORDER.md")
    print(f"alembic target: {ALEMBIC_TARGET}")
    print()

    import psycopg2

    try:
        conn = psycopg2.connect(dsn)
    except Exception as exc:
        _refuse(f"could not connect: {type(exc).__name__}: {exc}")

    conn.autocommit = False

    if args.apply:
        with conn.cursor() as cur:
            cur.execute(TRACKING_DDL)
            conn.commit()

    if args.create_substrate:
        print("SUBSTRATE (Supabase-like roles, auth schema, auth.uid())")
        if args.apply:
            with conn.cursor() as cur:
                cur.execute(SUBSTRATE_DDL)
                conn.commit()
            print("  applied")
        else:
            print("  would apply")
        print()

    with conn.cursor() as cur:
        already = _applied(cur)

    # ── Step 0 ───────────────────────────────────────────────────────────────
    print("STEP 0 — 020_declare_pre_existing_tables.sql (first, despite its number)")
    print(f"  {STEP0_FILE.name}: {_apply_sql(conn, STEP0_FILE, 'step0', not args.apply, already)}")
    print()

    # ── Step 0c — ALEMBIC, BEFORE the SQL set, not after ─────────────────────
    #
    # PROVISIONING_ORDER.md places Alembic in step 2, AFTER the numbered SQL set.
    # That order cannot work, and running it proved so twice:
    #
    #   * with the archived execution-schema files included in step 0b, step 2 aborted
    #     on `CREATE TYPE task_status` (42710 DuplicateObject) because the SQL set had
    #     already created it;
    #   * with them excluded, step 1 aborted on
    #     `migrations/006_reconcile_production_database.sql` with
    #     `42P01 UndefinedTable: relation "public.execution_records" does not exist`,
    #     because that file ALTERs a table only Alembic declares.
    #
    # So the numbered set depends on Alembic's tables and Alembic's revision does not
    # depend on the numbered set's. The dependency runs one way, and the documented order
    # has it backwards. Alembic therefore runs here: after `020` (which declares
    # `profiles`, the table `add_foreign_keys` needs) and before anything that
    # references `execution_records`, `orders`, `fills`, `positions` or `dag_tasks`.
    #
    # Goes all the way to `head` now, not the branchpoint. See ALEMBIC_TARGET for what
    # was really blocking head and why reaching it is what installs the nine
    # tenant-isolation RLS policies on the execution and financial tables.
    #
    # Running here rather than after the SQL set is also what makes head reachable at
    # all in a second way: `e88f9911b5a2` ("consolidate full schema") issues bare
    # `CREATE TABLE`s for tables the numbered SQL set also declares -- `copilot_sessions`
    # among them -- so with alembic second it aborts on `DuplicateTable`. With alembic
    # first, alembic creates them and the SQL files' `CREATE TABLE IF NOT EXISTS` form
    # tolerates what is already there. The asymmetry is the whole reason this ordering
    # is not interchangeable, and it was measured in both directions.
    print(f"STEP 0c — alembic upgrade {ALEMBIC_TARGET}")
    print("          (runs BEFORE the SQL set — see the comment in provision_database.py;")
    print("           PROVISIONING_ORDER.md's step-2 placement is circular)")
    if args.skip_alembic:
        print("  skipped (--skip-alembic)")
    elif not args.apply:
        print(f"  would run: alembic upgrade {ALEMBIC_TARGET}")
    else:
        env = dict(os.environ, DATABASE_URL=dsn)
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", ALEMBIC_TARGET],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
        )
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-10:])
        tail = re.sub(r"postgresql[^\s]*", "<dsn redacted>", tail)
        print(f"  exit={proc.returncode}")
        for line in tail.splitlines():
            print(f"    {line}")
        if proc.returncode != 0:
            print("  FAILED — see above")
            return 4
    print()

    # ── Step 0b ──────────────────────────────────────────────────────────────
    archived = _archived_root_migrations()
    print(f"STEP 0b — archived_migrations/root_migrations ({len(archived)} files)")
    print("          (marketplace/library tables the numbered set requires;")
    print("           missing from PROVISIONING_ORDER.md — see _archived_root_migrations)")
    for path in archived:
        status = _apply_sql(conn, path, "step0b", not args.apply, already)
        print(f"  {path.parent.name}/{path.name}: {status}")
    print()

    # ── Step 1 ───────────────────────────────────────────────────────────────
    files = _numbered_sql_files()
    print(f"STEP 1 — the numbered SQL set ({len(files)} files)")
    for path in files:
        status = _apply_sql(conn, path, "step1", not args.apply, already)
        print(f"  {path.parent.name}/{path.name}: {status}")
    print()

    # ── Step 3 ───────────────────────────────────────────────────────────────
    print("STEP 3 — re-run 020 so strategies_source_library_id_fkey can land")
    if args.apply:
        with conn.cursor() as cur:
            try:
                cur.execute(STEP0_FILE.read_text(encoding="utf-8", errors="replace"))
                conn.commit()
                print("  re-applied (idempotent)")
            except Exception as exc:
                conn.rollback()
                print(f"  FAILED: {type(exc).__name__}: {exc}")
                return 4
    else:
        print("  would re-apply")
    print()

    # ── Step 4 — privilege hardening, LAST ───────────────────────────────────
    #
    # WHY LAST, AND WHY A STEP RATHER THAN A FILE IN THE NUMBERED SET.
    #
    # Supabase's bootstrap grants `anon` and `authenticated` full privileges on `public`
    # and installs ALTER DEFAULT PRIVILEGES so later tables inherit them. The substrate
    # above reproduces that faithfully, which is what makes RLS testable at all -- but it
    # also means every table lands reachable by the browser's anon key unless something
    # narrows it afterwards.
    #
    # "Afterwards" is load bearing. This hardening started life inside
    # `006_reconcile_production_database.sql` and did not work there: 006 runs mid-set,
    # `migrations/create_plan_tables.sql` runs after it, so the `IF EXISTS` guards skipped
    # tables that did not exist yet and the ones created later inherited the defaults
    # anyway. Measured after that attempt -- `anon` still held DELETE, INSERT, TRUNCATE
    # and UPDATE on all five plan tables.
    #
    # Ordering it by filename would have been fragile too: within `migrations/` the files
    # sort numbered-then-alphabetical, and `referral_system_redesign.sql` currently sorts
    # last. An explicit step cannot be displaced by someone adding `zz_*.sql`.
    #
    # The file also ends with a preflight that RAISEs if any table carrying a
    # per-customer column still grants SELECT to `anon`, so a table added later that
    # misses the named lists fails provisioning instead of shipping exposed.
    # ── Step 3b — BUG-049 identity types, after the SQL set, before hardening ─
    #
    # Separate from STEP 4 because it is a different kind of thing: a type conversion
    # with a data precheck, not a declarative privilege end state. It runs BEFORE the
    # hardening so that hardening's policy work sees the final column types.
    #
    # Always re-applied rather than skipped as already-applied: every section is
    # idempotent (it checks the current type and the existing constraint before acting),
    # and its final backstop is an assertion about the whole schema that is worth
    # re-evaluating on every run.
    print("STEP 3b — BUG-049 identity column types and foreign keys")
    if not BUG049_FILE.exists():
        print(f"  MISSING: {BUG049_FILE}")
        print("  REFUSING to report success: the identity-type backstop did not run.")
        return 5
    print(f"  {BUG049_FILE.name}: "
          f"{_apply_sql(conn, BUG049_FILE, 'step3b', not args.apply, {})}")
    print()

    print("STEP 4 — privilege hardening (runs last; see the comment in provision_database.py)")
    if not HARDENING_FILE.exists():
        print(f"  MISSING: {HARDENING_FILE}")
        print("  REFUSING to report success: the anon-exposure backstop did not run.")
        return 5

    # ALWAYS RE-APPLIED, never skipped as already-applied. This one is a declarative end
    # state, not a one-time migration, and it has to run after the substrate because the
    # substrate re-grants everything on every invocation.
    #
    # The empty `already` set passed here is the whole point. With the normal tracking
    # set, run 2 skipped this file -- its checksum matched -- while the substrate's
    # `GRANT ALL ON ALL TABLES` had just re-opened all 85 tables. Measured by
    # `scripts/verify_provisioning.py`: 787 privilege rows reappeared, including `anon`
    # with DELETE/INSERT/SELECT/REFERENCES on `alembic_version`. A re-provision therefore
    # left the database MORE permissive than a fresh one, which is the worst possible
    # direction for an operation an operator would reasonably repeat.
    #
    # Safe to re-run by construction: every statement in the file is REVOKE, GRANT,
    # ENABLE ROW LEVEL SECURITY, or CREATE POLICY preceded by DROP POLICY IF EXISTS.
    status = _apply_sql(conn, HARDENING_FILE, "step4", not args.apply, already=set())
    print(f"  hardening/{HARDENING_FILE.name}: {status} (always re-applied)")
    if args.apply and status.startswith("FAILED"):
        print("  FAILED — the database may still expose customer data. See above.")
        return 5

    print()
    print("=" * 78)
    print(f"{mode} COMPLETE")
    print("=" * 78)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
