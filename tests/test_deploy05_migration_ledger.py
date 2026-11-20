"""DEPLOY-05 -- the migration ledger records EVIDENCE, and absence means UNVERIFIED.

THE FINDING
    "THE MIGRATION LEDGER ITSELF -- still a DESIGN TASK, not a reconciliation. Nothing
    declares recorded_migrations anywhere in the repository; public.alembic_version holds
    a single revision pointer (d97ffff9c3bb) and supabase_migrations.schema_migrations
    holds ONE row against 50+ migration files. Decide whether to adopt the Supabase table,
    adopt Alembic properly, or build something -- and then backfill from EVIDENCE by
    probing for each migration's declared objects."

THE DECISION, AND WHY THE OTHER TWO WERE REJECTED
    NOT Alembic. `alembic_version` holds ONE pointer, and that shape asserts "every
    revision up to this one is applied" -- the applied set is a PREFIX. Production
    disproves it: 037 was applied while 017 was not. A single pointer cannot express a
    non-prefix reality, so adopting it would mean recording something false.
    NOT supabase_migrations.schema_migrations. That is the Supabase CLI's own table, and
    it holds 1 row against 50+ files because the CLI is not what applies these. Writing
    rows it did not write would make its future behaviour unpredictable.
    SO: one row per migration, which is the only shape that can say "037 yes, 017 no".

WHAT KEEPS THIS FROM BECOMING THE FALSE CONFIDENCE IT REPLACES
    DEPLOY-05 warns about exactly that: "a half-working SQL parser that silently missed a
    declaration would restore exactly the false confidence this replaces." The three
    properties that answer it are asserted below, and two of them are enforced by the
    schema rather than by whoever writes to it:

      1. a row records EVIDENCE (what was probed), not intent (that a file was run);
      2. ABSENCE of a row means UNVERIFIED, never "not applied";
      3. the recorder never infers from the FILE LIST, only from probing.

SCOPE
    Reads the migration and the scripts off disk. No database: the live apply was
    performed and verified separately, and is recorded in the ledger entry.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = REPO / "backend_app" / "migrations" / "055_deploy05_migration_ledger.sql"
RECORDER = REPO / "scripts" / "record_migration_evidence.py"
DRIFT = REPO / "scripts" / "check_production_schema_drift.py"


@pytest.fixture(scope="module")
def sql() -> str:
    assert MIGRATION.exists(), f"{MIGRATION.name} is missing"
    return MIGRATION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def recorder() -> str:
    assert RECORDER.exists(), f"{RECORDER.name} is missing"
    return RECORDER.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
#  1. The table exists and is shaped to record evidence
# ══════════════════════════════════════════════════════════════════════════════


def test_the_ledger_table_is_declared(sql):
    assert "CREATE TABLE IF NOT EXISTS public.recorded_migrations" in sql


def test_a_row_cannot_omit_how_it_was_established(sql):
    """`verification` and `evidence` are both NOT NULL, so "we ran it" and "we confirmed
    it" can never be recorded as the same claim -- the conflation that made DEPLOY-04's
    close-out unverifiable."""
    assert re.search(r"verification\s+TEXT\s+NOT NULL", sql)
    assert re.search(r"evidence\s+TEXT\s+NOT NULL", sql)


def test_the_verification_vocabulary_is_closed(sql):
    """Free text would let a future writer invent a value that means neither thing."""
    assert "chk_recorded_migrations_verification" in sql
    assert "'EVIDENCE_PROBED'" in sql
    assert "'APPLIED_BY_TOOL'" in sql


def test_evidence_cannot_be_blank(sql):
    """A row citing nothing is the record this table exists to prevent, so the database
    refuses it rather than relying on the writer."""
    assert "chk_recorded_migrations_evidence_not_blank" in sql
    assert "length(btrim(evidence)) > 0" in sql


def test_one_row_per_migration(sql):
    """The PK is the filename, which is the only stable identifier these files have: they
    are raw .sql applied out of band, with no revision id."""
    assert "PRIMARY KEY (filename)" in sql


def test_recorded_at_is_not_an_applied_at(sql):
    """A column for when the migration was APPLIED would be filled with a guess, because
    that is unknown for every file applied out of band."""
    assert "applied_at" not in sql.lower(), (
        "an applied_at column invites a fabricated timestamp; recorded_at is when the ROW "
        "was written and the migration's own comment says so"
    )
    assert "recorded_at" in sql


def test_nothing_in_the_request_path_can_read_it(sql):
    """Deploy bookkeeping; no tenant owns a row. RLS on with no policy denies every
    non-owner read, and that is the intended posture rather than an unfinished one."""
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON public.recorded_migrations FROM anon, authenticated" in sql
    assert "CREATE POLICY" not in sql, (
        "a permissive policy would expose deploy bookkeeping to a request"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  2. The rejected alternatives are rejected IN WRITING, with the reason
# ══════════════════════════════════════════════════════════════════════════════


def test_alembic_is_rejected_for_a_stated_reason(sql):
    """Pinned because the reason is the design: a single pointer asserts a prefix, and
    production is not a prefix. If someone later 'simplifies' this onto alembic_version,
    this test is where they meet the counterexample."""
    assert "alembic_version" in sql
    assert "prefix" in sql.lower()
    assert "037" in sql and "017" in sql, (
        "the concrete counterexample (037 applied, 017 not) must stay in the file; it is "
        "what makes the rejection checkable rather than an opinion"
    )


def test_the_supabase_cli_table_is_left_alone(sql):
    assert "schema_migrations" in sql
    assert "Left untouched" in sql or "left untouched" in sql


def test_the_migration_does_not_write_to_either_other_ledger(sql):
    body = sql.split("BEGIN;", 1)[1]
    for foreign in ("alembic_version", "schema_migrations"):
        assert not re.search(rf"(INSERT INTO|UPDATE)\s+\S*{foreign}", body), (
            f"055 writes to {foreign}; it must only create its own table"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  3. The recorder infers from probing, never from the file list
# ══════════════════════════════════════════════════════════════════════════════


def test_the_recorder_reads_the_hand_maintained_inventory(recorder):
    """Not a SQL parser. DEPLOY-05: "a half-working SQL parser that silently missed a
    declaration would restore exactly the false confidence this replaces"."""
    assert "DECLARED_OBJECTS" in recorder
    assert "check_production_schema_drift" in recorder


def test_the_recorder_never_lists_the_migrations_directory(recorder):
    """THE LOAD-BEARING ONE. "the files up to N exist, therefore N is applied" is the
    inference that made DEPLOY-04's claim unverifiable, and the deployed schema is not a
    prefix of the migration sequence."""
    for forbidden in ("glob(", "iterdir(", "listdir(", "rglob("):
        assert forbidden not in recorder, (
            f"the recorder uses {forbidden} -- if it enumerates migration FILES it can "
            f"record a migration it never probed"
        )


def test_a_partially_applied_migration_is_never_recorded(recorder):
    """The most dangerous state to mark as applied."""
    assert "partial" in recorder.lower()
    assert "NOT RECORDABLE" in recorder


def test_the_recorder_only_claims_what_it_did(recorder):
    """It probed; it did not apply. Only something that ran a file may claim the stronger
    provenance."""
    assert "VERIFICATION_PROBED = \"EVIDENCE_PROBED\"" in recorder
    assert "APPLIED_BY_TOOL" not in recorder.split('"""', 2)[-1], (
        "the recorder writes APPLIED_BY_TOOL somewhere in its code; it only ever looked"
    )


def test_writing_requires_an_explicit_flag_and_an_identity(recorder):
    assert "--apply" in recorder
    assert "recorded-by" in recorder
    assert "--recorded-by is required with --apply" in recorder, (
        "a ledger row with blank provenance is the kind of record DEPLOY-05 exists to stop"
    )


def test_the_evidence_string_states_its_own_limit(recorder):
    """A row must not read as attesting to the whole file when it attests to an inventory
    subset."""
    assert "hand-maintained" in recorder
    assert "not to" in recorder or "ONLY" in recorder


# ══════════════════════════════════════════════════════════════════════════════
#  4. download_analytics: the build-or-remove decision, taken as REMOVE
# ══════════════════════════════════════════════════════════════════════════════


def test_the_dead_api_module_is_gone():
    assert not (
        REPO / "algo22-terminal" / "src" / "lib" / "downloadAnalyticsApi.js"
    ).exists(), "downloadAnalyticsApi.js wrote to a table declared in no migration"


def test_no_component_reads_the_table_any_more():
    offenders = []
    root = REPO / "algo22-terminal" / "src"
    for path in list(root.rglob("*.js")) + list(root.rglob("*.jsx")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")):
                continue  # a comment explaining the removal is not a read
            if "download_analytics" in line or "downloadAnalyticsApi" in line:
                offenders.append(f"{path.name}: {stripped[:90]}")
    assert not offenders, (
        "download_analytics is still referenced in executable code:\n"
        + "\n".join(offenders)
    )


def test_the_drift_inventory_records_why_production_is_right_not_to_have_it():
    text = DRIFT.read_text(encoding="utf-8")
    assert "download_analytics" in text
    assert "RESOLVED by DEPLOY-05" in text, (
        "the entry must say the decision was taken, so a reader who finds the name in git "
        "history does not re-open it"
    )


def test_the_ledger_itself_is_in_the_drift_inventory():
    """The thing that records evidence is subject to the same evidence check."""
    text = DRIFT.read_text(encoding="utf-8")
    assert "055_deploy05_migration_ledger.sql" in text
    assert "recorded_migrations" in text
    assert "Declared nowhere in the repository" not in text, (
        "the stale note saying recorded_migrations is declared nowhere must go; 055 "
        "declares it"
    )
