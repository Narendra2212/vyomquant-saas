"""
tests/test_deployment_binding_columns_migration.py

Static verification of migration 004 part 5,
``backend_app/migrations/004e_deployment_bindings.sql`` - the
``strategy_deployments`` deployment-binding columns and their two CHECK constraints.

Spec: strategy-builder task 8.1. Requirements 13.1, 13.6.

What these tests hold in place
-----------------------------
* **The DDL is what design.md specifies, not an improvisation.** The design's own
  ``-- 4. Deployment binding completeness`` block is PARSED OUT OF ``design.md`` at test
  time and every line in it is required to be present in the migration. The added-column
  set is compared BOTH WAYS, so a silently added or silently dropped column fails the test
  rather than passing unnoticed.
* **Requirement 13.6** - a ``live`` binding requires an exchange account - rests on
  ``chk_sd_live_needs_account`` being ``CHECK (mode <> 'live' OR exchange_account_id IS NOT
  NULL)`` AND on ``mode`` being ``NOT NULL``. Both halves are asserted, because a CHECK
  constraint that evaluates to NULL PASSES: a nullable ``mode`` would leave both
  constraints listed in ``pg_constraint`` while admitting every row with ``mode IS NULL``.
* **Requirement 13.1's mode vocabulary is the platform's, not a third one.** The literals
  in ``chk_sd_mode`` are compared against ``market_data_contract.MODE_PAPER`` and
  ``MODE_LIVE`` (task 7.5), and ``MODE_BACKTEST`` is required to be absent - a bounded
  historical read is not a deployment.
* **Task 8.1's hard constraint: the existing deployment RLS and indexes are untouched.**
  That is asserted as a fact about the TEXT of the file - no ``CREATE``/``ALTER``/``DROP
  POLICY``, no ``ENABLE``/``DISABLE ROW LEVEL SECURITY``, no ``CREATE``/``DROP INDEX``, no
  ``CREATE TRIGGER``, no ``GRANT``, no ``REVOKE``, and no DDL statement naming any relation
  other than ``strategy_deployments`` - and the migration's own section 6 is required to
  re-count policies and indexes at apply time so it is a fact about the database too.
* **The predicates are exercised, not only matched.** Because there is no PostgreSQL here,
  the two CHECK predicates are evaluated by a small double implementing SQL's three-valued
  logic, over the EXHAUSTIVE cross product of the mode and account values that matter. The
  double is PINNED to the migration: a test requires the predicate text extracted from the
  SQL to be exactly the expression the double implements, so drift in either side fails.
* **Idempotency by construction.** Every column is ``ADD COLUMN IF NOT EXISTS`` and every
  constraint sits behind a ``pg_constraint`` existence guard. The design's own bare chained
  ``ADD CONSTRAINT`` would abort on a second run with 42710; the test fails on any
  unguarded ``ADD CONSTRAINT``.

What is NOT covered here
------------------------
There is no local PostgreSQL in this environment, so **nothing below proves live
enforcement**. These tests read SQL text and evaluate a Python model of two predicates.
They cannot show that PostgreSQL accepts the file, that ``chk_sd_live_needs_account``
actually refuses a live row with no account, that the shape assertion actually fires, that
section 6's counts actually hold, or that a re-run is a no-op against a real catalogue.
Those are the migration's own VERIFICATION queries (10 groups, including direct exercises
of both constraints), to be run by the operator who applies it, plus task 8.7's isolation
matrix for the RLS half.

The split is deliberate and is the same posture tasks 4.1 and 6.1 took for
``004c_immutable_versions.sql`` and ``004d_training_and_models.sql``: verify the SQL
statically against the design, exercise the invariant against a double shaped like it, and
say plainly what could not be verified.
"""

import re
from pathlib import Path
from typing import NamedTuple, Optional

import pytest

from backend_app.backend.market_data_contract import (
    MODE_BACKTEST,
    MODE_LIVE,
    MODE_PAPER,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

MIGRATION_REL = "backend_app/migrations/004e_deployment_bindings.sql"
MIGRATION_PATH = REPO_ROOT / MIGRATION_REL
DESIGN_PATH = REPO_ROOT / ".kiro" / "specs" / "strategy-builder" / "design.md"

TABLE = "strategy_deployments"

#: The five columns design.md section 4 adds.
BINDING_COLUMNS = frozenset(
    {
        "exchange_account_id",
        "risk_config_id",
        "execution_config",
        "mode",
        "dag_hash",
    }
)

#: The two constraints task 8.1 names.
CONSTRAINTS = ("chk_sd_mode", "chk_sd_live_needs_account")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _strip_comments(sql: str) -> str:
    """Drop ``--`` comments line by line.

    Safe for this file: it contains no ``--`` inside a string literal. Every test that
    distinguishes code from prose runs through here, so a rule stated in a comment can never
    satisfy an assertion about the code.
    """
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _normalise(text: str) -> str:
    """Collapse whitespace and drop schema qualification.

    Comparison against design.md must survive two harmless differences: the migration
    schema-qualifies with ``public.`` and wraps some statements over more lines. Normalising
    removes exactly those two degrees of freedom - a changed identifier, keyword or
    predicate still fails.
    """
    return re.sub(r"\s+", " ", text.replace("public.", "")).strip()


def _code() -> str:
    return _strip_comments(_sql())


def _normalised_code() -> str:
    return _normalise(_code())


def _executable_code() -> str:
    """Code with comments AND string literals removed.

    Needed by the "this token appears nowhere in the file" tests: the migration's own
    ``RAISE EXCEPTION`` messages talk ABOUT row level security and about
    ``ADD COLUMN IF NOT EXISTS``, and a message is data, not a statement. Scanning the raw
    code would confuse an explanation of a refusal with the thing being refused.
    """
    return re.sub(r"'(?:[^']|'')*'", "''", _code())


def _statements() -> list[str]:
    """Top-level statements, with ``DO $$ ... $$;`` blocks kept whole."""
    code = _code()
    statements: list[str] = []
    buffer: list[str] = []
    in_dollar = False
    for line in code.splitlines():
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


def _design_section() -> str:
    """The text of the design's ``-- 4. Deployment binding completeness`` block."""
    design = DESIGN_PATH.read_text(encoding="utf-8")
    start = design.index("4. Deployment binding completeness")
    end = design.index("5. Registry snapshots", start)
    return design[start:end]


def _constraint_predicate(name: str) -> str:
    """The normalised CHECK expression the migration attaches under ``name``."""
    match = re.search(
        rf"ADD CONSTRAINT\s+{name}\s+CHECK\s*\((.*?)\)\s*;",
        _code(),
        re.DOTALL,
    )
    assert match, f"no guarded ADD CONSTRAINT ... CHECK for {name}"
    return _normalise(match.group(1))


def _column_comment(column: str) -> str:
    """The concatenated text of ``COMMENT ON COLUMN ...`` for one column.

    ``COMMENT ON ... IS 'a ' 'b';`` is one value to PostgreSQL (adjacent string constants
    separated by a newline are concatenated), so a test asserting on the wording has to join
    the chunks first or a phrase straddling a line break would read as absent.
    """
    match = re.search(
        rf"COMMENT ON COLUMN public\.{TABLE}\.{column} IS\s*((?:'(?:[^']|'')*'\s*)+);",
        _code(),
        re.DOTALL,
    )
    assert match, f"{column} carries no column comment"
    chunks = re.findall(r"'((?:[^']|'')*)'", match.group(1), re.DOTALL)
    return re.sub(r"\s+", " ", "".join(chunks).replace("''", "'")).strip()


# ---------------------------------------------------------------------------
# The double: SQL three-valued CHECK semantics
#
# There is no PostgreSQL in this environment, so the two predicates are evaluated here
# instead. The double models the one rule that makes CHECK constraints subtle and that a
# reader is most likely to get wrong:
#
#     A CHECK CONSTRAINT ADMITS A ROW UNLESS IT EVALUATES TO FALSE.
#     NULL is not FALSE, so a predicate that evaluates to NULL PASSES.
#
# It is pinned to the migration by
# ``test_the_double_is_pinned_to_the_migrations_own_predicates`` below, so it cannot drift
# into testing some other expression.
# ---------------------------------------------------------------------------


class Row(NamedTuple):
    """A candidate ``strategy_deployments`` row, reduced to the two columns the two
    constraints read. ``None`` is SQL NULL."""

    mode: Optional[str]
    exchange_account_id: Optional[str]


def _sql_or(left: Optional[bool], right: Optional[bool]) -> Optional[bool]:
    """SQL's three-valued OR: TRUE dominates, otherwise NULL dominates."""
    if left is True or right is True:
        return True
    if left is None or right is None:
        return None
    return False


def chk_sd_mode(row: Row) -> Optional[bool]:
    """``mode IN ('paper','live')``. NULL IN (...) is NULL, not FALSE."""
    if row.mode is None:
        return None
    return row.mode in (MODE_PAPER, MODE_LIVE)


def chk_sd_live_needs_account(row: Row) -> Optional[bool]:
    """``mode <> 'live' OR exchange_account_id IS NOT NULL``.

    ``NULL <> 'live'`` is NULL; ``IS NOT NULL`` is never NULL.
    """
    left = None if row.mode is None else (row.mode != MODE_LIVE)
    right = row.exchange_account_id is not None
    return _sql_or(left, right)


def admitted_by_checks(row: Row) -> bool:
    """Would the table's two CHECK constraints admit this row?

    Deliberately ignores ``mode``'s NOT NULL, so that the NULL-mode rows the constraints
    alone would admit stay visible in the tests below - they are the reason the migration
    asserts NOT NULL.
    """
    return (
        chk_sd_mode(row) is not False
        and chk_sd_live_needs_account(row) is not False
    )


def admitted_by_the_table(row: Row) -> bool:
    """Would the table accept this row: the two CHECKs AND ``mode``'s NOT NULL."""
    return row.mode is not None and admitted_by_checks(row)


ACCOUNT = "3f6c1a9e-0000-4000-8000-000000000001"

#: Every mode value worth trying: the two admitted, the one the platform defines but does
#: not admit as a deployment, a value from the legacy ``environment`` vocabulary, a casing
#: variant, the empty string, and SQL NULL.
MODE_VALUES = (
    MODE_PAPER,
    MODE_LIVE,
    MODE_BACKTEST,
    "cloud",
    "LIVE",
    "",
    None,
)

ACCOUNT_VALUES = (ACCOUNT, None)


# ---------------------------------------------------------------------------
# The file itself
# ---------------------------------------------------------------------------


def test_the_migration_file_exists_where_the_split_says_it_does():
    assert MIGRATION_PATH.is_file(), (
        f"{MIGRATION_REL} is named as migration 004 part 5 but does not exist"
    )


def test_the_migration_is_ascii_and_carries_no_bom():
    """Matching parts 1-4: a BOM or a stray em-dash breaks tooling that reads these files
    without an explicit encoding."""
    raw = MIGRATION_PATH.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "the migration carries a UTF-8 BOM"
    raw.decode("ascii")  # raises if any byte is non-ASCII


def test_every_sibling_part_points_at_this_file():
    """The migration split is only navigable if each part names the others by filename. An
    operator reading part 1 must be able to find the deployment DDL."""
    for sibling in (
        "004_strategy_builder_canonical.sql",
        "004b_block_registry_snapshots.sql",
        "004c_immutable_versions.sql",
        "004d_training_and_models.sql",
    ):
        text = (REPO_ROOT / "backend_app" / "migrations" / sibling).read_text(
            encoding="utf-8"
        )
        assert "004e_deployment_bindings.sql" in text, (
            f"{sibling} does not name part 5's file, so an operator reading it cannot find "
            "the deployment binding DDL"
        )


def test_part_one_was_not_edited_in_place():
    """The task text says "extend 004_strategy_builder_canonical.sql". It is extended by a
    sibling file instead, because nothing records which migrations an environment has
    applied, so appending to an already-shipped file leaves no signal that it changed.

    Part 1 must therefore still contain only its own DDL: the deployment binding columns and
    their constraints must NOT have been added to it.
    """
    part_one = (
        REPO_ROOT / "backend_app" / "migrations" / "004_strategy_builder_canonical.sql"
    ).read_text(encoding="utf-8")
    code = _strip_comments(part_one)
    for name in CONSTRAINTS:
        assert name not in code, (
            f"{name} was added to part 1 in place; an operator who already applied part 1 "
            "would never run it"
        )
    assert TABLE not in code, "part 1 must name no deployment DDL"


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
    """Additive only. This file alters a PRE-EXISTING table, so the no-DROP rule matters
    more here than in a file that only creates its own relations."""
    offenders = []
    for lineno, line in enumerate(_sql().splitlines(), start=1):
        code = line.split("--", 1)[0]
        upper = code.upper()
        for keyword in ("DROP", "TRUNCATE", "ALTER COLUMN", "DELETE", "UPDATE "):
            if keyword in upper:
                offenders.append((lineno, keyword, line.strip()))
    assert not offenders, f"destructive statement outside a comment: {offenders}"


def test_the_migration_names_no_relation_other_than_the_deployment_table():
    """Every DDL, COMMENT, GRANT or REVOKE statement must name ``strategy_deployments`` and
    nothing else. This is what makes "no other table's controls can be weakened by this
    file" a fact about the text rather than a promise in a comment."""
    for statement in _statements():
        head = _normalise(statement).upper()
        if head.startswith(("DO ", "DO$", "BEGIN", "COMMIT")):
            continue
        if head.startswith(("ALTER TABLE", "CREATE", "GRANT", "REVOKE", "COMMENT ON")):
            assert TABLE in statement, (
                f"statement names a relation other than {TABLE}: {statement[:160]}"
            )


# ---------------------------------------------------------------------------
# Task 8.1's hard constraint: existing RLS and indexes untouched
# ---------------------------------------------------------------------------


def test_the_file_contains_no_rls_statement_of_any_kind():
    """``strategy_deployments`` already has RLS enabled and three owner policies from 003
    sections 8 and 9. This file must not redefine, drop, recreate or re-enable any of it."""
    code = _executable_code().upper()
    for forbidden in (
        "CREATE POLICY",
        "ALTER POLICY",
        "DROP POLICY",
        "ROW LEVEL SECURITY",
        "FOR SELECT",
        "FOR INSERT",
        "FOR UPDATE",
        "FOR DELETE",
        "FOR ALL",
    ):
        assert forbidden not in code, (
            f"{forbidden} appears in the migration's code; task 8.1 requires the existing "
            "deployment RLS to be left untouched"
        )


def test_the_file_creates_no_index_and_drops_none():
    """The five ``idx_strategy_deployments_*`` indexes from 001/003 are left exactly as they
    are, and no index is added on a binding column - the design specifies none and every
    deployment query already filters on user_id or strategy_id."""
    code = _executable_code().upper()
    assert "CREATE INDEX" not in code
    assert "CREATE UNIQUE INDEX" not in code
    assert "DROP INDEX" not in code
    assert "idx_strategy_deployments" not in _executable_code(), (
        "no existing deployment index may be named in a statement of this file"
    )


def test_the_file_changes_no_privilege_and_no_trigger():
    """003 already attaches ``trigger_update_strategy_deployments_updated_at``, which keeps
    maintaining ``updated_at`` for writes to the new columns; a second trigger would
    double-fire. Grants are likewise left as they are - this file narrows nothing and widens
    nothing."""
    code = _executable_code().upper()
    for forbidden in ("GRANT ", "REVOKE ", "CREATE TRIGGER", "CREATE OR REPLACE FUNCTION"):
        assert forbidden not in code, f"{forbidden} appears in the migration's code"


def test_the_file_verifies_at_apply_time_that_it_changed_neither():
    """Text assertions prove the file contains no policy or index statement. Section 6 goes
    further and proves it about the DATABASE: the policy and index counts recorded in the
    preflight are re-counted after every statement has run, and a move in either raises.

    No policy or index NAME is hard-coded, so an environment that renamed one cannot
    false-alarm it, and both counts are taken inside the one transaction.
    """
    code = _code()
    assert "aerora.sd_policies_before" in code
    assert "aerora.sd_indexes_before" in code
    assert code.count("set_config(") == 2, "both baselines must be recorded"
    assert code.count("current_setting(") == 2, "both baselines must be read back"
    assert code.count("FROM pg_policies") == 2, "counted before and after"
    assert code.count("FROM pg_indexes") == 2, "counted before and after"
    assert "the policy count on" in code
    assert "the index count on" in code

    # The comparison itself, literally, and unconditionally. A neutered guard - an extra
    # conjunct, a commented-out branch, a `<=` instead of a `<>` - would leave every token
    # above in place while proving nothing.
    postflight = [
        block
        for block in re.findall(r"DO \$\$(.*?)END \$\$;", code, re.DOTALL)
        if "current_setting(" in block
    ]
    assert len(postflight) == 1, "expected exactly one postflight block"
    block = postflight[0]
    assert "IF policies_now <> policies_before THEN" in block
    assert "IF indexes_now <> indexes_before THEN" in block
    assert "IF NOT rls_on THEN" in block
    assert block.count("RAISE EXCEPTION") == 3, (
        "each of the three postconditions must abort the transaction, not warn"
    )


def test_the_file_refuses_to_run_if_rls_is_off_on_the_deployment_table():
    """``exchange_account_id`` resolves real exchange credentials and ``mode = 'live'`` makes
    fills real. Adding both to a table whose row-level isolation is off would let any
    authenticated caller read or rewrite which account another user's live deployment trades
    through. The file stops instead - and deliberately does not enable RLS itself, because
    that would be a change to the control task 8.1 must leave alone."""
    code = _code()
    assert "relrowsecurity" in code
    assert code.count("relrowsecurity") >= 2, "checked in the preflight and the postflight"
    assert "refuses to run" in code
    assert "003_signal_trace_restoration.sql" in code, (
        "the refusal must name the file that supplies the missing prerequisite"
    )


# ---------------------------------------------------------------------------
# Faithfulness to design.md
# ---------------------------------------------------------------------------


def test_every_design_ddl_line_for_the_binding_columns_is_present():
    """The strongest form of "exactly as specified": the design's own section 4 text, line by
    line, must appear in the migration. Parsed from design.md at test time, so the two cannot
    drift apart silently.

    Trailing ``,`` and ``;`` are stripped from each needle: the design chains its two
    ``ADD CONSTRAINT`` clauses into one statement, while the migration puts each behind its
    own ``pg_constraint`` guard (the chained form is not idempotent - a second run raises
    42710). That is the only difference the comparison tolerates.
    """
    haystack = _normalised_code()
    missing = []
    for raw in _design_section().splitlines():
        line = raw.split("--", 1)[0].strip()
        # Skip blanks, bare punctuation and the design's box-drawing section banner (the
        # only non-ASCII text in the block).
        if not line or line in ("(", ")", ");") or not line.isascii():
            continue
        needle = _normalise(line).rstrip(",;")
        if needle and needle not in haystack:
            missing.append(line)
    assert not missing, (
        "design.md DDL lines absent from the migration:\n  " + "\n  ".join(missing)
    )


def test_the_added_column_set_matches_the_design_exactly_in_both_directions():
    """Not just "the design's columns are present" but "and no others". A column added here
    without a design change is as much a drift as a column dropped, and on a pre-existing
    production table an undesigned column is worse."""
    design_columns = set(
        re.findall(
            r"ADD COLUMN IF NOT EXISTS\s+([a-z_][a-z0-9_]*)", _design_section()
        )
    )
    migration_columns = set(
        re.findall(r"ADD COLUMN IF NOT EXISTS\s+([a-z_][a-z0-9_]*)", _executable_code())
    )

    assert design_columns == BINDING_COLUMNS, (
        f"design.md section 4 no longer adds the five expected columns: "
        f"{sorted(design_columns)}"
    )
    assert migration_columns - design_columns == set(), (
        f"the migration adds columns the design does not specify: "
        f"{sorted(migration_columns - design_columns)}"
    )
    assert design_columns - migration_columns == set(), (
        f"the migration is missing columns the design specifies: "
        f"{sorted(design_columns - migration_columns)}"
    )


def test_the_two_not_null_columns_carry_a_default():
    """NOT NULL without a DEFAULT would fail on a table that already has rows, and would
    make this migration unapplicable in production rather than merely wrong."""
    normalised = _normalised_code()
    assert (
        "ADD COLUMN IF NOT EXISTS execution_config JSONB NOT NULL DEFAULT '{}'::JSONB"
        in normalised
    )
    assert "ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'paper'" in normalised


def test_the_default_mode_is_the_safe_one():
    """``paper`` is the value that reaches no real order router and the one
    ``chk_sd_live_needs_account`` does not constrain, so every pre-existing row is
    backfilled into a state both constraints admit. A default of ``live`` would both
    misdescribe existing rows and make the migration unapplicable, since those rows have no
    ``exchange_account_id``."""
    match = re.search(
        r"ADD COLUMN IF NOT EXISTS\s+mode\s+TEXT NOT NULL DEFAULT\s+'([a-z]+)'", _code()
    )
    assert match, "mode has no literal default"
    assert match.group(1) == MODE_PAPER


def test_neither_uuid_column_is_not_null():
    """A paper deployment needs no exchange account and no risk config, so making either
    column NOT NULL would refuse a legal binding. ``chk_sd_live_needs_account`` is what makes
    the account mandatory, and only for ``live`` (Requirement 13.6)."""
    normalised = _normalised_code()
    assert "ADD COLUMN IF NOT EXISTS exchange_account_id UUID," in normalised
    assert "ADD COLUMN IF NOT EXISTS risk_config_id UUID," in normalised
    assert "exchange_account_id UUID NOT NULL" not in normalised
    assert "risk_config_id UUID NOT NULL" not in normalised


def test_dag_hash_is_text_and_nullable():
    """TEXT matches ``strategy_versions.dag_hash`` (part 1) and ``strategies.dag_hash``.
    Nullable because rows written before this migration have no plan identity to record, and
    inventing one would be a fabricated fact."""
    assert "ADD COLUMN IF NOT EXISTS dag_hash TEXT;" in _normalised_code()


# ---------------------------------------------------------------------------
# The two constraints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CONSTRAINTS)
def test_each_constraint_is_added_under_a_pg_constraint_guard(name):
    """The design writes the two as one bare chained ``ALTER TABLE ... ADD CONSTRAINT a ...,
    ADD CONSTRAINT b ...``, which raises 42710 on a second run. PostgreSQL has no
    ``ADD CONSTRAINT IF NOT EXISTS``, and DROP-then-ADD would both break this file's no-DROP
    rule and leave a window inside the transaction with the invariant unenforced."""
    code = _code()
    blocks = [
        block
        for block in re.findall(r"DO \$\$(.*?)END \$\$;", code, re.DOTALL)
        if f"conname  = '{name}'" in block or f"conname = '{name}'" in block
    ]
    assert len(blocks) == 1, f"expected exactly one guarded block for {name}"
    block = blocks[0]
    assert "NOT EXISTS" in block and "pg_constraint" in block
    assert f"ADD CONSTRAINT {name}" in block
    assert "'public.strategy_deployments'::regclass" in block, (
        "the guard must be scoped to this table, or a same-named constraint on another "
        "table would suppress it"
    )


def test_chk_sd_mode_admits_exactly_paper_and_live():
    assert _constraint_predicate("chk_sd_mode") == "mode IN ('paper','live')"


def test_chk_sd_live_needs_account_has_the_designs_predicate():
    assert (
        _constraint_predicate("chk_sd_live_needs_account")
        == "mode <> 'live' OR exchange_account_id IS NOT NULL"
    )


def test_the_mode_vocabulary_agrees_with_the_code_and_is_not_a_third_one():
    """``market_data_contract`` (task 7.5) is where the platform's mode spellings live, and
    its own comment on ``MODE_PAPER`` says "chk_sd_mode (design.md) admits this as a
    deployment mode". The constraint must therefore use those exact spellings.

    ``MODE_BACKTEST`` must be ABSENT, and that is not a disagreement with the code: a
    backtest is "a bounded historical read ... no order router is downstream of it", which is
    not a deployment. Admitting it here would let a historical read masquerade as a running
    deployment and - since ``chk_sd_live_needs_account`` only constrains ``live`` - do so
    with no account binding at all.
    """
    literals = set(
        re.findall(r"'([a-z]+)'", _constraint_predicate("chk_sd_mode"))
    )
    assert literals == {MODE_PAPER, MODE_LIVE}, (
        f"chk_sd_mode's vocabulary drifted from market_data_contract: {sorted(literals)}"
    )
    assert MODE_BACKTEST not in literals


def test_the_shape_assertion_covers_all_five_columns_and_both_not_nulls():
    """``ADD COLUMN IF NOT EXISTS`` is SILENT about a pre-existing column of the same name and
    a different type or nullability. For ``mode`` that silence is severe rather than
    cosmetic: a nullable ``mode`` would leave both constraints listed in ``pg_constraint``
    while admitting every row with ``mode IS NULL``. The assertion must therefore cover the
    type of all five columns AND the NOT NULL of the two that have one."""
    code = _code()
    blocks = [
        block
        for block in re.findall(r"DO \$\$(.*?)END \$\$;", code, re.DOTALL)
        if "information_schema.columns" in block
    ]
    assert len(blocks) == 1, "expected exactly one column shape assertion"
    block = blocks[0]

    values = re.search(r"FROM \(VALUES(.*?)\)\s*AS expected", block, re.DOTALL)
    assert values, "no VALUES list in the shape assertion"

    asserted = {
        name: (data_type, not_null)
        for name, data_type, not_null in re.findall(
            r"\('([a-z_]+)',\s*'([a-z ]+)',\s*(TRUE|FALSE)\)", values.group(1)
        )
    }

    assert set(asserted) == BINDING_COLUMNS, (
        f"the shape assertion drifted from the added column set: {sorted(asserted)}"
    )
    assert asserted["mode"] == ("text", "TRUE"), "mode must be asserted text NOT NULL"
    assert asserted["execution_config"] == ("jsonb", "TRUE")
    assert asserted["exchange_account_id"] == ("uuid", "FALSE")
    assert asserted["risk_config_id"] == ("uuid", "FALSE")
    assert asserted["dag_hash"] == ("text", "FALSE")

    assert "is_nullable" in block, "nullability must actually be compared"
    assert "RAISE EXCEPTION" in block, "a wrong shape must abort, not warn"


def test_the_reason_mode_must_be_not_null_is_written_down_where_it_is_enforced():
    """The rule is counter-intuitive enough to be worth stating in the file: a CHECK that
    evaluates to NULL PASSES. If the next author relaxes ``mode`` to nullable, this is the
    sentence that tells them what they broke."""
    sql = _sql().upper()
    assert "A CHECK CONSTRAINT THAT EVALUATES TO NULL PASSES" in sql


# ---------------------------------------------------------------------------
# Requirements 13.1 and 13.6, exercised against the double
# ---------------------------------------------------------------------------


def test_the_double_is_pinned_to_the_migrations_own_predicates():
    """The double below is only evidence about the migration if it evaluates the migration's
    expressions. This test is the pin: the predicate text extracted from the SQL must be
    exactly what ``chk_sd_mode`` and ``chk_sd_live_needs_account`` implement in Python.
    Change either side and this fails."""
    assert _constraint_predicate("chk_sd_mode") == (
        f"mode IN ('{MODE_PAPER}','{MODE_LIVE}')"
    )
    assert _constraint_predicate("chk_sd_live_needs_account") == (
        f"mode <> '{MODE_LIVE}' OR exchange_account_id IS NOT NULL"
    )


def test_a_check_admits_a_row_unless_it_evaluates_to_false():
    """The rule the whole double rests on, stated as a test so it is not folded into the
    others by accident."""
    null_mode = Row(mode=None, exchange_account_id=None)
    assert chk_sd_mode(null_mode) is None
    assert chk_sd_live_needs_account(null_mode) is None
    # NULL is not FALSE, so both constraints admit the row.
    assert admitted_by_checks(null_mode) is True
    # ... and it is the column's NOT NULL, added by this migration, that refuses it.
    assert admitted_by_the_table(null_mode) is False


def test_a_live_binding_without_an_exchange_account_is_refused():
    """Requirement 13.6, the execution-safety control of this migration. Task 8.2 depends on
    it: this is the row that must be unrepresentable, because a live deployment with no
    account is one that reaches a real order router with no credentials chosen."""
    row = Row(mode=MODE_LIVE, exchange_account_id=None)
    assert chk_sd_live_needs_account(row) is False
    assert admitted_by_the_table(row) is False


def test_a_live_binding_with_an_exchange_account_is_admitted():
    """The other half, and the half a too-strong predicate would break: the constraint must
    not refuse live deployments outright."""
    row = Row(mode=MODE_LIVE, exchange_account_id=ACCOUNT)
    assert chk_sd_live_needs_account(row) is True
    assert admitted_by_the_table(row) is True


@pytest.mark.parametrize("account", ACCOUNT_VALUES)
def test_a_paper_binding_is_admitted_with_or_without_an_account(account):
    """The constraint constrains exactly one direction, which is what 13.6 asks for. Binding
    an account to a paper deployment is legal and useful - it is how an author paper-trades
    against the venue they intend to go live on."""
    row = Row(mode=MODE_PAPER, exchange_account_id=account)
    assert chk_sd_live_needs_account(row) is True
    assert admitted_by_the_table(row) is True


@pytest.mark.parametrize("account", ACCOUNT_VALUES)
def test_backtest_is_not_a_deployment_mode(account):
    """``chk_sd_mode`` refuses it whether or not an account is bound. Note what would happen
    if it were admitted: ``chk_sd_live_needs_account`` only constrains ``live``, so a
    'backtest' deployment would need no account either."""
    row = Row(mode=MODE_BACKTEST, exchange_account_id=account)
    assert chk_sd_mode(row) is False
    assert admitted_by_the_table(row) is False


@pytest.mark.parametrize("mode", ["cloud", "local", "LIVE", "Live", "", " live"])
def test_no_other_mode_spelling_is_admitted(mode):
    """The legacy ``environment`` vocabulary (cloud, local), casing variants and whitespace
    are all outside the binding vocabulary. ``'LIVE'`` matters most: it must not sneak past
    ``chk_sd_mode`` as an unrecognised mode AND past ``chk_sd_live_needs_account`` as
    "not live"."""
    row = Row(mode=mode, exchange_account_id=None)
    assert admitted_by_the_table(row) is False


@pytest.mark.parametrize("mode", MODE_VALUES)
@pytest.mark.parametrize("account", ACCOUNT_VALUES)
def test_the_table_admits_a_row_exactly_when_it_is_a_well_formed_binding(mode, account):
    """The whole input space, exhaustively: 7 modes x 2 account values. The table must admit
    a row if and only if the mode is one of the two deployment modes and, when it is
    ``live``, an account is bound. Exhaustive enumeration rather than sampling, because the
    space is small enough to cover completely."""
    row = Row(mode=mode, exchange_account_id=account)
    expected = mode in (MODE_PAPER, MODE_LIVE) and (
        mode != MODE_LIVE or account is not None
    )
    assert admitted_by_the_table(row) is expected


def test_the_only_rejected_live_row_is_the_one_without_an_account():
    """Stated as a set, so a predicate that happens to reject an unrelated row - which would
    be worse than no constraint, since it would refuse legal deployments - fails here."""
    rejected = {
        Row(mode=mode, exchange_account_id=account)
        for mode in (MODE_PAPER, MODE_LIVE)
        for account in ACCOUNT_VALUES
        if not admitted_by_the_table(Row(mode, account))
    }
    assert rejected == {Row(mode=MODE_LIVE, exchange_account_id=None)}


# ---------------------------------------------------------------------------
# Intent the schema cannot express
# ---------------------------------------------------------------------------


def test_exchange_account_id_is_marked_a_reference_and_never_a_credential():
    """Requirement 13.9 / 21.7. The api key, secret and passphrase stay in the vault and are
    resolved inside the execution process by account id. A column comment is the only place
    the schema can carry that."""
    comment = _column_comment("exchange_account_id").upper()
    assert "NEVER A CREDENTIAL" in comment
    assert "CREDENTIAL_VAULT" in comment or "LOAD_DECRYPTED_KEYS" in comment


def test_both_reference_columns_record_that_ownership_is_not_enforced_here():
    """There is no ``risk_configs`` table at all and the candidate exchange-account tables
    key on TEXT, so a foreign key is not expressible - which means the DATABASE does not
    check that a referenced account or risk config belongs to the deploying user.
    Requirement 13.2 is application-enforced, by task 8.2, and the schema says so rather than
    letting a reader infer a guard that is not there."""
    for column in ("exchange_account_id", "risk_config_id"):
        comment = _column_comment(column).upper()
        assert "NO FOREIGN KEY" in comment, f"{column} does not record the absent FK"
        assert "13.2" in comment, f"{column} does not name the requirement that covers it"


def test_mode_records_that_it_is_not_the_legacy_environment_column():
    """``environment`` already exists with the vocabulary paper/live/cloud/local. Conflating
    the two would be the defect: ``chk_sd_live_needs_account`` guards ``mode``, so a reader
    who believes it guards ``environment`` would believe a control exists that does not."""
    comment = _column_comment("mode").upper()
    assert "NOT THE LEGACY ENVIRONMENT COLUMN" in comment
    assert "NOT NULL" in comment


def test_dag_hash_records_that_it_is_a_field_and_what_reads_it():
    """SB-02: the clone path once called ``compiled.get("dag_hash")`` on an object that only
    had ``compute_hash()``, the AttributeError was swallowed, and every clone was persisted
    with no hash. Requirement 22.5 is what reads the column."""
    comment = _column_comment("dag_hash").upper()
    assert "SB-02" in comment
    assert "FIELD" in comment and "NEVER A" in comment
    assert "22.5" in comment


def test_the_header_states_what_task_8_2_must_still_do():
    """The database-level 13.6 guarantee only covers writers that SET ``mode``. The current
    writer sets ``environment`` and not ``mode``, so until 8.2 populates it a deployment
    created as ``environment = 'live'`` is stored with ``mode = 'paper'`` and the constraint
    correctly does not fire. Leaving that implicit would be the reader-misleading failure
    this file is otherwise careful about."""
    sql = _sql().upper()
    assert "TASK 8.2 MUST SET MODE ON EVERY DEPLOYMENT IT CREATES" in sql


def test_the_degradation_contract_names_this_file():
    """004d's convention, and the reason it exists: the migration is applied by hand, so the
    code reaches production first. Every path that touches these columns must degrade with a
    warning NAMING this file rather than 500-ing."""
    sql = _sql()
    assert "004e_deployment_bindings.sql" in sql
    assert "not a 500" in sql


# ---------------------------------------------------------------------------
# Idempotency by construction
# ---------------------------------------------------------------------------


def test_every_column_addition_is_guarded():
    """A re-run must add nothing and raise nothing."""
    additions = re.findall(
        r"ADD COLUMN(\s+IF NOT EXISTS)?\s+([a-z_]+)", _executable_code()
    )
    assert additions, "no ADD COLUMN found"
    for guard, column in additions:
        assert guard.strip() == "IF NOT EXISTS", f"unguarded ADD COLUMN: {column}"


def test_no_add_constraint_sits_outside_a_do_block():
    code = _code()
    for statement in _statements():
        collapsed = _normalise(statement)
        head = collapsed.upper()
        if head.startswith("ALTER TABLE") and "ADD CONSTRAINT" in head:
            raise AssertionError(
                f"ADD CONSTRAINT outside a pg_constraint guard: {collapsed[:120]}"
            )
    guarded = set(
        re.findall(r"ADD CONSTRAINT\s+([a-z_][a-z0-9_]*)", _executable_code())
    )
    assert guarded == set(CONSTRAINTS), f"guarded constraint set drifted: {sorted(guarded)}"


def test_the_preflight_checks_what_the_file_needs_before_altering_anything():
    """A missing prerequisite should be a readable message naming the file that supplies it,
    not a bare 42P01 from inside an ALTER."""
    code = _code()
    preflight = code[: code.index("ALTER TABLE")]
    assert "to_regclass('public.strategy_deployments')" in preflight
    assert "001_strategy_architecture.sql" in preflight
    assert "relrowsecurity" in preflight
    assert preflight.count("RAISE EXCEPTION") >= 3


# ---------------------------------------------------------------------------
# Names later tasks depend on, and the operator-facing half
# ---------------------------------------------------------------------------


def test_every_name_a_later_task_references_exists():
    """Task 8.2 writes the binding, 8.3 drives the lifecycle, 8.7's isolation matrix targets
    this table. A rename here would surface a phase later; this catches it now."""
    code = _code()
    for name in (
        "public.strategy_deployments",
        "exchange_account_id",
        "risk_config_id",
        "execution_config",
        "mode",
        "dag_hash",
        "chk_sd_mode",
        "chk_sd_live_needs_account",
    ):
        assert name in code, f"{name} is absent from the migration"


def test_the_verification_section_exercises_both_constraints_in_both_directions():
    """The operator-facing half of this task. Live enforcement cannot be tested here, so the
    file must at least hand the operator the queries that do test it - including the
    ACCEPTING cases, since a constraint that refuses every live deployment would pass a
    rejection-only check."""
    sql = _sql()
    verification = sql[sql.index("-- VERIFICATION") :]
    for expected in (
        "chk_sd_live_needs_account",
        "chk_sd_mode",
        "23514",  # check_violation, the code the operator must see
        "23502",  # not_null_violation on mode
        "expect success",
        "pg_policies",
        "pg_indexes",
        "pg_trigger",
        "role_table_grants",
        "Idempotency",
    ):
        assert expected in verification, f"the verification section omits {expected}"
