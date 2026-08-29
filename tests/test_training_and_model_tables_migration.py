"""
tests/test_training_and_model_tables_migration.py

Static verification of migration 004 part 4,
``backend_app/migrations/004d_training_and_models.sql`` - the ``training_jobs`` and
``model_versions`` tables.

Spec: strategy-builder task 6.1. Requirements 15.13, 17.4, 21.3.

What these tests hold in place
------------------------------
* **The DDL is what design.md specifies, not an improvisation.** The design's own
  ``-- 2. Training jobs`` and ``-- 3. Model versions`` blocks are PARSED OUT OF
  ``design.md`` at test time and every statement and column line in them is required to be
  present in the migration. The column sets are compared BOTH WAYS, so a silently added or
  silently dropped column fails the test rather than passing unnoticed.
* **Requirement 15.13** - at most one ``QUEUED``/``RUNNING`` job per (version, node) - rests
  on ``uq_tj_active_per_node`` being a PARTIAL unique index over
  ``(version_id, node_id) WHERE status IN ('QUEUED','RUNNING')``. The predicate is asserted
  literally, because losing it is a functional change and not a cosmetic one: without the
  predicate the index would forbid retraining a node at all.
* **Requirement 17.4** - at most one active model per (version, node) - rests on
  ``uq_mv_active_per_node`` being partial over ``(version_id, node_id) WHERE is_active``.
  Same reasoning: without the predicate a node could never have a second model version.
* **Requirement 21.3** - row-level ownership on training job and Model_Version records.
  RLS is enabled on both tables and exactly the five design policies exist, each keyed on
  ``user_id = auth.uid()``. The tests also assert what must NOT be there: no ``DELETE``
  policy, no ``FOR ALL`` policy, no ``UPDATE`` policy on ``model_versions``, and no
  ``UPDATE``/``DELETE`` grant that would let a client flip ``is_active``.
* **Requirement 17.4's data-minimisation half.** ``training_jobs.metrics_history`` carries a
  ``COMMENT ON COLUMN`` stating that it holds per-epoch scalars only and never predictions
  or feature values. SQL cannot enforce that, so the test asserts the rule is at least
  RECORDED IN THE SCHEMA where the next author of the progress payload will see it.
* **Idempotency by construction.** Every statement that creates something is either
  ``IF NOT EXISTS``, wrapped in a ``pg_constraint``/``pg_policies`` existence guard, or
  naturally idempotent (``COMMENT``, ``GRANT``, ``REVOKE``, ``ENABLE ROW LEVEL SECURITY``).
  The test walks the statements and fails on any unguarded ``CREATE``/``ADD CONSTRAINT``.
* **Nothing pre-existing is touched.** Every ``CREATE TABLE``, ``ALTER TABLE``, ``GRANT``,
  ``REVOKE`` and ``COMMENT`` names ``training_jobs`` or ``model_versions``. No existing
  table's RLS, columns or indexes are named in a DDL statement, so no control on another
  table can be weakened by this file.
* **The names later tasks reference all exist.** Task 6.3 inserts jobs, 6.5 writes model
  versions and 8.7's isolation matrix targets both tables; each constraint, index and policy
  name those tasks depend on is asserted present here so a rename is caught at this task
  rather than three phases later.

What is NOT covered here
------------------------
There is no local PostgreSQL in this environment, so **nothing below proves live
enforcement**. These tests read SQL text. They cannot show that PostgreSQL accepts the
file, that the partial unique indexes actually reject a second live job or a second active
model, that the RLS policies actually deny a non-owner, or that a re-run is a no-op against
a real catalogue. Those are the migration's own VERIFICATION queries, to be run by the
operator who applies it, and - for the isolation half - task 8.7's suite. Stated plainly so
the coverage here is not mistaken for the coverage that matters.

This is deliberately the same posture task 4.1 took for
``004c_immutable_versions.sql``: verify the SQL statically against the design, and say what
could not be verified.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

MIGRATION_REL = "backend_app/migrations/004d_training_and_models.sql"
MIGRATION_PATH = REPO_ROOT / MIGRATION_REL
DESIGN_PATH = REPO_ROOT / ".kiro" / "specs" / "strategy-builder" / "design.md"

TRAINING_TABLE = "training_jobs"
MODEL_TABLE = "model_versions"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _strip_comments(sql: str) -> str:
    """Drop ``--`` comments line by line.

    Safe for this file: it contains no ``--`` inside a string literal. The tests that care
    about the distinction between code and prose all run through here, so a rule described
    in a comment can never satisfy an assertion about the code.
    """
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _normalise(text: str) -> str:
    """Collapse whitespace and drop schema qualification.

    Comparison against design.md must survive two harmless differences: the migration
    schema-qualifies with ``public.`` and it wraps some statements over more lines than the
    design does. Normalising both sides removes exactly those two degrees of freedom and
    nothing else - a changed identifier, keyword or predicate still fails.
    """
    return re.sub(r"\s+", " ", text.replace("public.", "")).strip()


def _code() -> str:
    return _strip_comments(_sql())


def _normalised_code() -> str:
    return _normalise(_code())


def _statements() -> list[str]:
    """Top-level statements, with ``DO $$ ... $$;`` blocks kept whole."""
    code = _code()
    statements: list[str] = []
    buffer: list[str] = []
    in_dollar = False
    for line in code.splitlines():
        if "$$" in line:
            # Every line in this file has at most one $$ marker.
            in_dollar = not in_dollar or line.count("$$") % 2 == 0 and in_dollar
            if line.count("$$") % 2 == 1:
                in_dollar = not in_dollar
        buffer.append(line)
        if not in_dollar and line.rstrip().endswith(";"):
            statement = "\n".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
    leftover = "\n".join(buffer).strip()
    assert not leftover, f"trailing text outside any statement: {leftover[:200]!r}"
    return statements


def _design_section(start_marker: str, end_marker: str) -> str:
    """The text of one numbered section of the design's migration 004 SQL block."""
    design = DESIGN_PATH.read_text(encoding="utf-8")
    start = design.index(start_marker)
    end = design.index(end_marker, start)
    return design[start:end]


def _design_training_and_model_ddl() -> str:
    return _design_section("2. Training jobs", "4. Deployment binding completeness")


def _design_rls_lines() -> list[str]:
    """The training/model half of the design's ``-- 6. RLS`` section."""
    section = _design_section("6. RLS", "```")
    return [
        line.strip()
        for line in section.splitlines()
        if line.strip()
        and not line.strip().startswith("--")
        and (TRAINING_TABLE in line or MODEL_TABLE in line)
    ]


def _create_table_body(table: str, sql: str) -> str:
    """The parenthesised body of ``CREATE TABLE ... <table> ( ... );``."""
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS public\.{table}\s*\(", sql, re.IGNORECASE
    )
    assert match, f"no CREATE TABLE for {table}"
    index = match.end() - 1
    depth = 0
    for offset in range(index, len(sql)):
        if sql[offset] == "(":
            depth += 1
        elif sql[offset] == ")":
            depth -= 1
            if depth == 0:
                return sql[index + 1 : offset]
    raise AssertionError(f"unbalanced parentheses in CREATE TABLE {table}")


def _literal_text(sql_fragment: str) -> str:
    """Concatenate the single-quoted chunks of a SQL string expression.

    ``COMMENT ON ... IS 'a ' 'b';`` is one value to PostgreSQL (adjacent string constants
    separated by a newline are concatenated), so a test asserting on its wording has to
    join the chunks first or a phrase that straddles a line break would read as absent.
    """
    chunks = re.findall(r"'((?:[^']|'')*)'", sql_fragment, re.DOTALL)
    return re.sub(r"\s+", " ", "".join(chunks).replace("''", "'")).strip()


_COLUMN_LINE = re.compile(r"^([a-z_][a-z0-9_]*)\s+[A-Z]")


def _column_names(body: str) -> set[str]:
    names = set()
    for raw in body.splitlines():
        line = raw.split("--", 1)[0].strip()
        if not line or line.upper().startswith("CONSTRAINT"):
            continue
        match = _COLUMN_LINE.match(line)
        if match:
            names.add(match.group(1))
    return names


# ---------------------------------------------------------------------------
# The file itself
# ---------------------------------------------------------------------------


def test_the_migration_file_exists_where_the_split_says_it_does():
    assert MIGRATION_PATH.is_file(), (
        f"{MIGRATION_REL} is named as migration 004 part 4 but does not exist"
    )


def test_the_migration_is_ascii_and_carries_no_bom():
    """Matching parts 1-3: a BOM or a stray em-dash breaks tooling that reads these files
    without an explicit encoding."""
    raw = MIGRATION_PATH.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "the migration carries a UTF-8 BOM"
    raw.decode("ascii")  # raises if any byte is non-ASCII


def test_every_sibling_part_points_at_this_file():
    """The migration split is only navigable if each part names the next. Parts 1, 2 and 3
    all carry the same enumeration; part 4 must appear in it by filename."""
    for sibling in (
        "004_strategy_builder_canonical.sql",
        "004b_block_registry_snapshots.sql",
        "004c_immutable_versions.sql",
    ):
        text = (REPO_ROOT / "backend_app" / "migrations" / sibling).read_text(
            encoding="utf-8"
        )
        assert "004d_training_and_models.sql" in text, (
            f"{sibling} does not name part 4's file, so an operator reading it cannot find "
            "the training and model DDL"
        )


def test_the_migration_is_one_balanced_transaction():
    code = _code()
    assert code.count("BEGIN;") == 1
    assert code.count("COMMIT;") == 1
    assert code.index("BEGIN;") < code.index("COMMIT;")
    assert code.count("DO $$") == code.count("END $$;"), (
        "every DO block must open and close"
    )
    assert code.count("$$") % 2 == 0
    assert "ROLLBACK;" not in code

    # Parentheses and string literals balance across the whole file. This is not a parser -
    # there is no PostgreSQL here and no PostgreSQL grammar available to this suite - but an
    # unbalanced paren or an unterminated literal is the failure mode a hand-written DDL file
    # of this size actually hits, and it is cheap to rule out.
    depth = 0
    for character in code:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            assert depth >= 0, "a closing parenthesis with no opener"
    assert depth == 0, "unbalanced parentheses"
    assert code.count("'") % 2 == 0, "an unterminated string literal"


def test_the_migration_has_no_destructive_statement():
    """Additive only. ``ON DELETE CASCADE`` is a referential action in a column definition,
    not a DELETE statement, so it is the one allowed occurrence."""
    forbidden = ("DROP ", "TRUNCATE", "ALTER COLUMN")
    offenders = []
    for lineno, line in enumerate(_sql().splitlines(), start=1):
        code = line.split("--", 1)[0]
        upper = code.upper()
        for keyword in forbidden:
            if keyword in upper:
                offenders.append((lineno, keyword, line.strip()))
        if "DELETE" in upper and "ON DELETE CASCADE" not in upper:
            offenders.append((lineno, "DELETE", line.strip()))
    assert not offenders, f"destructive statement outside a comment: {offenders}"

    # No UPDATE statement either. "FOR UPDATE" (a policy command) and the UPDATE privilege
    # in a GRANT are not statements that modify a row.
    for lineno, line in enumerate(_sql().splitlines(), start=1):
        code = line.split("--", 1)[0].strip()
        assert not code.upper().startswith("UPDATE "), (
            f"line {lineno} modifies rows: {code}"
        )


def test_the_migration_touches_no_pre_existing_table():
    """The only relations named in a DDL, GRANT, REVOKE or COMMENT statement are the two
    this file creates. This is what makes 'no existing RLS policy is altered' a fact about
    the text rather than a promise in a comment."""
    ours = (TRAINING_TABLE, MODEL_TABLE)
    for statement in _statements():
        head = statement.split("\n", 1)[0].upper()
        if head.startswith(("DO ", "DO$", "BEGIN", "COMMIT")):
            continue
        if head.startswith(
            ("ALTER TABLE", "CREATE TABLE", "GRANT", "REVOKE", "COMMENT ON")
        ):
            assert any(name in statement for name in ours), (
                f"statement names a relation other than {ours}: {statement[:160]}"
            )


# ---------------------------------------------------------------------------
# Faithfulness to design.md
# ---------------------------------------------------------------------------


def test_every_design_ddl_line_for_the_two_tables_is_present():
    """The strongest form of 'exactly as specified': the design's own DDL text for sections
    2 and 3, line by line, must appear in the migration. Parsed from design.md at test time,
    so the two cannot drift apart silently."""
    haystack = _normalised_code()
    missing = []
    for raw in _design_training_and_model_ddl().splitlines():
        line = raw.split("--", 1)[0].strip()
        # Skip blanks, bare punctuation, and the design's box-drawing section banners
        # (the only non-ASCII text in the block).
        if not line or line in ("(", ")", ");") or not line.isascii():
            continue
        needle = _normalise(line)
        if needle and needle not in haystack:
            missing.append(line)
    assert not missing, (
        "design.md DDL lines absent from the migration:\n  " + "\n  ".join(missing)
    )


def test_every_design_rls_statement_for_the_two_tables_is_present():
    haystack = _normalised_code()
    missing = [
        line for line in _design_rls_lines() if _normalise(line) not in haystack
    ]
    assert not missing, (
        "design.md RLS statements absent from the migration:\n  " + "\n  ".join(missing)
    )


@pytest.mark.parametrize("table", [TRAINING_TABLE, MODEL_TABLE])
def test_the_column_set_matches_the_design_exactly_in_both_directions(table):
    """Not just 'the design's columns are present' but 'and no others'. A column added here
    without a design change is as much a drift as a column dropped."""
    design_body = _create_table_body(
        table, _design_training_and_model_ddl().replace(table, f"public.{table}")
    )
    migration_body = _create_table_body(table, _code())

    design_columns = _column_names(design_body)
    migration_columns = _column_names(migration_body)

    assert design_columns, f"failed to parse any column for {table} out of design.md"
    assert migration_columns - design_columns == set(), (
        f"{table} has columns the design does not specify: "
        f"{sorted(migration_columns - design_columns)}"
    )
    assert design_columns - migration_columns == set(), (
        f"{table} is missing columns the design specifies: "
        f"{sorted(design_columns - migration_columns)}"
    )


# ---------------------------------------------------------------------------
# Requirement 15.13 - one live job per (version, node)
# ---------------------------------------------------------------------------


def test_uq_tj_active_per_node_is_partial_over_the_live_statuses():
    """The predicate IS the requirement. ``WHERE status IN ('QUEUED','RUNNING')`` scopes
    uniqueness to live jobs, so a node keeps its COMPLETED/FAILED/CANCELLED history and can
    be retrained, while two live jobs are unrepresentable. Dropping the predicate would
    forbid retraining outright; widening it to all statuses would do the same."""
    normalised = _normalised_code()
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tj_active_per_node "
        f"ON {TRAINING_TABLE}(version_id, node_id) "
        "WHERE status IN ('QUEUED','RUNNING');" in normalised
    ), "uq_tj_active_per_node is missing, not unique, not partial, or over the wrong columns"


def test_training_jobs_check_constraints_are_all_present():
    normalised = _normalised_code()
    for name, definition in (
        (
            "chk_tj_status",
            "CHECK (status IN ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED'))",
        ),
        ("chk_tj_progress", "CHECK (progress >= 0 AND progress <= 1)"),
        (
            "chk_tj_failed_has_reason",
            "CHECK (status <> 'FAILED' OR failure_reason IS NOT NULL)",
        ),
    ):
        assert f"CONSTRAINT {name} {definition}" in normalised, (
            f"{name} is missing or its predicate changed"
        )


def test_the_supporting_training_indexes_are_present():
    normalised = _normalised_code()
    for index in (
        f"CREATE INDEX IF NOT EXISTS idx_tj_user_status ON {TRAINING_TABLE}(user_id, status);",
        f"CREATE INDEX IF NOT EXISTS idx_tj_version ON {TRAINING_TABLE}(version_id);",
        f"CREATE INDEX IF NOT EXISTS idx_tj_created ON {TRAINING_TABLE}(created_at DESC);",
    ):
        assert index in normalised, f"missing index: {index}"


# ---------------------------------------------------------------------------
# Requirement 17.4 - one active model per (version, node)
# ---------------------------------------------------------------------------


def test_uq_mv_active_per_node_is_partial_over_is_active():
    """``WHERE is_active`` indexes exactly the rows where is_active is TRUE, so the retrain
    trail survives as is_active = FALSE rows while a second active model is
    unrepresentable. This is the ML-3 retrain-overwrite class made impossible rather than
    unlikely."""
    normalised = _normalised_code()
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_active_per_node "
        f"ON {MODEL_TABLE}(version_id, node_id) WHERE is_active;" in normalised
    ), "uq_mv_active_per_node is missing, not unique, not partial, or over the wrong columns"


def test_uq_mv_version_node_is_unique_over_the_three_columns():
    normalised = _normalised_code()
    assert (
        "CONSTRAINT uq_mv_version_node UNIQUE (version_id, node_id, model_version)"
        in normalised
    ), "uq_mv_version_node is missing or covers the wrong columns"


def test_the_supporting_model_indexes_are_present():
    normalised = _normalised_code()
    for index in (
        f"CREATE INDEX IF NOT EXISTS idx_mv_strategy ON {MODEL_TABLE}(strategy_id);",
        f"CREATE INDEX IF NOT EXISTS idx_mv_user ON {MODEL_TABLE}(user_id);",
    ):
        assert index in normalised, f"missing index: {index}"


def test_the_artifact_is_a_reference_not_a_blob():
    """Requirement 17.9. ``artifact_uri`` is TEXT and there is no BYTEA anywhere: a model
    artifact is never stored in the row."""
    body = _create_table_body(MODEL_TABLE, _code())
    assert "artifact_uri      TEXT NOT NULL" in body
    assert "BYTEA" not in _code().upper(), "an artifact blob column has appeared"


# ---------------------------------------------------------------------------
# Requirement 21.3 - row level security
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", [TRAINING_TABLE, MODEL_TABLE])
def test_rls_is_enabled_on_both_new_tables(table):
    assert f"ENABLE ROW LEVEL SECURITY" in _code()
    assert _normalise(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;") in _normalised_code()


def test_exactly_the_five_design_policies_exist_and_no_others():
    code = _code()
    created = set(re.findall(r"CREATE POLICY\s+([a-z_][a-z0-9_]*)", code))
    assert created == {
        "tj_owner_select",
        "tj_owner_insert",
        "tj_owner_update",
        "mv_owner_select",
        "mv_owner_insert",
    }, f"policy set drifted from the design: {sorted(created)}"


def test_every_policy_is_scoped_to_the_owner():
    """Each of the five policies keys on ``user_id = auth.uid()``. A policy with a different
    predicate - or none - would be the tenant-isolation control weakened."""
    normalised = _normalised_code()
    for statement in (
        f"CREATE POLICY tj_owner_select ON {TRAINING_TABLE} FOR SELECT USING (user_id = auth.uid());",
        f"CREATE POLICY tj_owner_insert ON {TRAINING_TABLE} FOR INSERT WITH CHECK (user_id = auth.uid());",
        f"CREATE POLICY tj_owner_update ON {TRAINING_TABLE} FOR UPDATE USING (user_id = auth.uid());",
        f"CREATE POLICY mv_owner_select ON {MODEL_TABLE} FOR SELECT USING (user_id = auth.uid());",
        f"CREATE POLICY mv_owner_insert ON {MODEL_TABLE} FOR INSERT WITH CHECK (user_id = auth.uid());",
    ):
        assert statement in normalised, f"missing or altered policy: {statement}"

    assert normalised.count("auth.uid()") == 5, (
        "there should be exactly one owner predicate per policy"
    )


def test_no_policy_permits_delete_or_everything():
    """With RLS on and no DELETE policy, a job's history and a model's provenance cannot be
    erased through the API. ``FOR ALL`` would quietly grant both UPDATE and DELETE."""
    code = _code().upper()
    assert "FOR DELETE" not in code, "a DELETE policy would let training history be erased"
    assert "FOR ALL" not in code, "FOR ALL would grant more than the design specifies"


def test_model_versions_has_no_update_policy():
    """Deliberate, and load-bearing for task 6.5: without an UPDATE policy a client cannot
    flip ``is_active``, so it cannot change which artifact a running deployment resolves.
    Deactivating a superseded model version is a service-role operation."""
    code = _code()
    model_update = re.search(
        rf"CREATE POLICY\s+\w+\s+ON\s+public\.{MODEL_TABLE}\s+FOR UPDATE", code
    )
    assert model_update is None, (
        "an UPDATE policy on model_versions would let a client change which artifact a "
        "running deployment resolves"
    )


def test_grants_are_narrowed_and_never_include_delete():
    normalised = _normalised_code()
    for revoke in (
        f"REVOKE ALL ON {TRAINING_TABLE} FROM anon;",
        f"REVOKE ALL ON {TRAINING_TABLE} FROM authenticated;",
        f"REVOKE ALL ON {MODEL_TABLE} FROM anon;",
        f"REVOKE ALL ON {MODEL_TABLE} FROM authenticated;",
    ):
        assert revoke in normalised, f"missing: {revoke}"

    assert (
        f"GRANT SELECT, INSERT, UPDATE ON {TRAINING_TABLE} TO authenticated;" in normalised
    )
    assert f"GRANT SELECT, INSERT ON {MODEL_TABLE} TO authenticated;" in normalised
    assert f"GRANT SELECT, INSERT, UPDATE ON {MODEL_TABLE} TO service_role;" in normalised

    # No client-side UPDATE on model_versions, and no DELETE for anyone.
    assert (
        f"GRANT SELECT, INSERT, UPDATE ON {MODEL_TABLE} TO authenticated;"
        not in normalised
    )
    for grant in re.findall(r"GRANT ([^;]*?) ON", _code()):
        assert "DELETE" not in grant.upper(), f"DELETE granted: {grant}"
    assert "TO anon" not in _code(), "anon must be granted nothing"


# ---------------------------------------------------------------------------
# Requirement 17.4 / design "Storage architecture" - data minimisation
# ---------------------------------------------------------------------------


def test_metrics_history_records_the_data_minimisation_rule_in_the_schema():
    """SQL cannot tell a loss curve from a prediction vector, so the rule lives in a
    ``COMMENT ON COLUMN`` that travels with the schema and shows up in ``\\d+``. The test
    asserts the rule is stated where the next author of the training progress payload will
    read it - not that the database enforces it, because it cannot."""
    code = _code()
    match = re.search(
        r"COMMENT ON COLUMN public\.training_jobs\.metrics_history IS(.*?);",
        code,
        re.DOTALL,
    )
    assert match, "metrics_history carries no column comment"
    comment = _literal_text(match.group(1)).upper()
    assert "PER-EPOCH SCALARS ONLY" in comment
    assert "NEVER MODEL PREDICTIONS" in comment
    assert "NEVER FEATURE VALUES" in comment


def test_the_config_column_records_the_no_credential_rule():
    """Requirement 21.7 / design Property 23. ``config`` is the third place a leaked
    exchange identifier could hide, after ``graph_json`` and ``compiled_plan``."""
    code = _code()
    match = re.search(
        r"COMMENT ON COLUMN public\.training_jobs\.config IS(.*?);", code, re.DOTALL
    )
    assert match, "config carries no column comment"
    comment = _literal_text(match.group(1)).upper()
    assert "NO EXCHANGE IDENTIFIER" in comment
    assert "NO CREDENTIAL" in comment


def test_artifact_uri_is_marked_server_side_only():
    """Requirement 17.8: the reference is never returned raw to a client."""
    match = re.search(
        r"COMMENT ON COLUMN public\.model_versions\.artifact_uri IS(.*?);",
        _code(),
        re.DOTALL,
    )
    assert match, "artifact_uri carries no column comment"
    assert "SERVER-SIDE ONLY" in _literal_text(match.group(1)).upper()


# ---------------------------------------------------------------------------
# Idempotency by construction
# ---------------------------------------------------------------------------


def test_every_create_statement_is_guarded():
    """A re-run must add nothing and raise nothing. Tables and indexes use
    ``IF NOT EXISTS``; constraints and policies - which have no such form in PostgreSQL, and
    this baseline predates ``CREATE OR REPLACE TRIGGER`` - sit behind catalogue existence
    guards inside ``DO`` blocks."""
    for statement in _statements():
        collapsed = _normalise(statement)
        head = collapsed.upper()

        if head.startswith("CREATE TABLE"):
            assert "CREATE TABLE IF NOT EXISTS" in head, (
                f"unguarded CREATE TABLE: {collapsed[:120]}"
            )
        elif head.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX")):
            assert "IF NOT EXISTS" in head, f"unguarded CREATE INDEX: {collapsed[:120]}"
        elif head.startswith("CREATE POLICY"):
            raise AssertionError(
                f"CREATE POLICY outside a pg_policies guard: {collapsed[:120]}"
            )
        elif head.startswith("ALTER TABLE") and "ADD CONSTRAINT" in head:
            raise AssertionError(
                f"ADD CONSTRAINT outside a pg_constraint guard: {collapsed[:120]}"
            )


def test_every_constraint_and_policy_creation_sits_behind_a_catalogue_guard():
    """The guards are counted, not just assumed: three CHECK constraints on training_jobs,
    one UNIQUE constraint on model_versions, five policies."""
    code = _code()
    do_blocks = re.findall(r"DO \$\$(.*?)END \$\$;", code, re.DOTALL)
    assert do_blocks, "no DO blocks found"

    for block in do_blocks:
        for guarded, catalogue in (
            ("ADD CONSTRAINT", "pg_constraint"),
            ("CREATE POLICY", "pg_policies"),
        ):
            if guarded in block:
                assert f"NOT EXISTS" in block and catalogue in block, (
                    f"{guarded} inside a DO block with no {catalogue} guard"
                )

    guarded_constraints = set(
        re.findall(r"ADD CONSTRAINT\s+([a-z_][a-z0-9_]*)", code)
    )
    assert guarded_constraints == {
        "chk_tj_status",
        "chk_tj_progress",
        "chk_tj_failed_has_reason",
        "uq_mv_version_node",
    }, f"guarded constraint set drifted: {sorted(guarded_constraints)}"


def test_the_inline_and_guarded_constraint_definitions_agree():
    """The four constraints are declared twice - inline in ``CREATE TABLE`` (faithful to the
    design) and again behind a guard (so a pre-existing table gains them). Two spellings of
    one invariant is a drift hazard, so the definitions are required to match."""
    code = _code()
    for name in (
        "chk_tj_status",
        "chk_tj_progress",
        "chk_tj_failed_has_reason",
        "uq_mv_version_node",
    ):
        occurrences = re.findall(
            rf"CONSTRAINT\s+{name}\s+((?:CHECK|UNIQUE)\s*\([^;]*?\))\s*[,;\n]",
            code,
            re.DOTALL,
        )
        assert len(occurrences) == 2, (
            f"{name} should appear once inline and once guarded, found {len(occurrences)}"
        )
        first, second = (_normalise(text) for text in occurrences)
        assert first == second, (
            f"{name} is spelled two different ways:\n  {first}\n  {second}"
        )


def test_the_preflight_checks_every_object_the_file_names():
    """A missing prerequisite should be a readable message, not a bare 42P01 from the middle
    of a CREATE TABLE."""
    code = _code()
    preflight = code[: code.index("CREATE TABLE")]
    for required in (
        "public.strategies",
        "public.strategy_versions",
        "auth.users",
        "anon",
        "authenticated",
        "service_role",
    ):
        assert required in preflight, f"preflight does not check for {required}"
    assert preflight.count("RAISE EXCEPTION") >= 4


@pytest.mark.parametrize("table", [TRAINING_TABLE, MODEL_TABLE])
def test_a_pre_existing_table_of_the_wrong_shape_fails_readably(table):
    """``CREATE TABLE IF NOT EXISTS`` is silent about a same-named table of a different
    shape, which would move the failure to the first insert in production."""
    code = _code()
    assert f"'{table}'" in code and "information_schema.columns" in code
    assert f"public.{table} exists but is missing column(s)" in code


@pytest.mark.parametrize("table", [TRAINING_TABLE, MODEL_TABLE])
def test_the_shape_assertion_lists_exactly_the_columns_the_table_declares(table):
    """The shape assertion is only worth having if its column list cannot drift from the
    ``CREATE TABLE`` above it. A column added to the table but not to the list would leave a
    pre-existing table missing that column undetected - the exact silence the assertion
    exists to remove."""
    code = _code()
    marker = f"c.table_name   = '{table}'"
    blocks = [
        block
        for block in re.findall(r"DO \$\$(.*?)END \$\$;", code, re.DOTALL)
        if marker in block
    ]
    assert len(blocks) == 1, f"expected exactly one shape assertion for {table}"

    values = re.search(r"FROM \(VALUES(.*?)\)\s*AS expected", blocks[0], re.DOTALL)
    assert values, f"no VALUES list in the shape assertion for {table}"
    asserted = set(re.findall(r"'([a-z_]+)'", values.group(1)))
    declared = _column_names(_create_table_body(table, code))

    assert asserted == declared, (
        f"{table} shape assertion drifted from the table definition; "
        f"missing from assertion: {sorted(declared - asserted)}, "
        f"not a column: {sorted(asserted - declared)}"
    )


# ---------------------------------------------------------------------------
# Names later tasks depend on
# ---------------------------------------------------------------------------


def test_every_name_a_later_task_references_exists():
    """Task 6.3 inserts jobs, 6.5 writes model versions and binds them, 6.6 reads status,
    8.7's isolation matrix targets both tables. A rename here would surface three phases
    later; this catches it now."""
    code = _code()
    for name in (
        # tables
        "public.training_jobs",
        "public.model_versions",
        # constraints
        "chk_tj_status",
        "chk_tj_progress",
        "chk_tj_failed_has_reason",
        "uq_mv_version_node",
        # indexes
        "uq_tj_active_per_node",
        "uq_mv_active_per_node",
        "idx_tj_user_status",
        "idx_tj_version",
        "idx_tj_created",
        "idx_mv_strategy",
        "idx_mv_user",
        # policies
        "tj_owner_select",
        "tj_owner_insert",
        "tj_owner_update",
        "mv_owner_select",
        "mv_owner_insert",
    ):
        assert name in code, f"{name} is absent from the migration"


def test_the_verification_section_exercises_both_partial_predicates():
    """The operator-facing half of this task. Since live enforcement cannot be tested here,
    the file must at least hand the operator the queries that do test it."""
    sql = _sql()
    verification = sql[sql.index("-- VERIFICATION") :]
    assert "uq_tj_active_per_node" in verification
    assert "uq_mv_active_per_node" in verification
    assert "chk_tj_failed_has_reason" in verification
    assert "pg_policies" in verification
    assert "role_table_grants" in verification
    assert "Idempotency" in verification
