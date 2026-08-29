"""
tests/test_immutable_versions_migration.py

Static verification of migration 004 part 3,
``backend_app/migrations/004c_immutable_versions.sql`` - the ``chk_valid_requires_hash``
CHECK constraint, the ``reject_immutable_version_update()`` function and the
``trg_sv_immutable`` trigger.

Spec: strategy-builder task 8.13. Requirement 9.2.

WHAT THIS FILE ASSERTS, AND WHAT IT DOES NOT
--------------------------------------------
**These tests assert that migration 004c DECLARES its immutability controls. They do NOT
assert - and cannot assert - that any of those controls is in force.** Migrations
004-004e are unapplied in this environment and there is no PostgreSQL here, so every
statement below is a statement about SQL *text*. Nothing here shows that PostgreSQL
accepts the file, that ``chk_valid_requires_hash`` actually refuses a ``VALID`` row with a
null ``dag_hash`` or ``compiled_plan``, that ``trg_sv_immutable`` actually fires, or that
it actually rejects a graph edit on a ``DEPLOYED`` row.

Proving enforcement - which is what Requirement 9.2 ("THE Persistence_Layer SHALL reject
any update to a read-only version's graph, Compiled_Plan, Identity_Hash or schema
version") actually claims - takes two things this suite does not have:

1. 004c's own VERIFICATION queries (its numbered groups 1, 2 and 4: the constraint is
   present in ``pg_constraint``, all four part-1 constraints are present, the trigger is
   present in ``pg_trigger``), run against a real database after the file is applied; and
2. a negative ``UPDATE`` against that database - VERIFICATION group 5 - setting
   ``dag_hash`` on a row whose ``lifecycle_state`` is ``DEPLOYED``, ``RUNNING`` or
   ``PAUSED`` and observing the exception, plus the paired positive case that a column the
   trigger does not name (``validation_report``, ``updated_at``) is still updatable on the
   same row. Group 3 is the constraint's equivalent negative case.

That gap is the whole reason this file exists: before task 8.13 there was no assertion of
any kind about 004c, so a rename or a silent deletion of the trigger would have been
invisible to the suite as well as unenforced locally. This is the posture
:class:`tests.security.test_builder_tenant_isolation.TestPersistenceLayerRls` already
takes for 004d's five RLS policies, and the same posture as
``tests/test_deployment_binding_columns_migration.py`` (004e) and
``tests/test_training_and_model_tables_migration.py`` (004d).

Every assertion below runs against the migration with ``--`` comments stripped. That is
load-bearing rather than tidy: 004c's header quotes the design document's version of the
trigger verbatim, in prose, so a naive substring search over the raw file would be
satisfied by the explanation of the trigger rather than by the trigger.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

MIGRATION_REL = "backend_app/migrations/004c_immutable_versions.sql"
MIGRATION_PATH = REPO_ROOT / MIGRATION_REL
DESIGN_PATH = REPO_ROOT / ".kiro" / "specs" / "strategy-builder" / "design.md"

TABLE = "strategy_versions"
CONSTRAINT = "chk_valid_requires_hash"
FUNCTION = "reject_immutable_version_update"
TRIGGER = "trg_sv_immutable"

#: The four columns Requirement 9.2 names: graph, Compiled_Plan, Identity_Hash, schema
#: version. The trigger must compare all four and nothing less.
IMMUTABLE_COLUMNS = ("graph_json", "compiled_plan", "dag_hash", "schema_version")

#: The lifecycle states design.md's own state machine and Requirements 9.4 and 9.9 call
#: read-only. 004c gates on these IN ADDITION to ``is_read_only``; see its header note
#: "SOURCE OF THE DEFINITION, AND ONE DELIBERATE DEVIATION".
READ_ONLY_STATES = ("DEPLOYED", "RUNNING", "PAUSED")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _strip_comments(sql: str) -> str:
    """Drop ``--`` comments line by line.

    Safe for this file: it contains no ``--`` inside a string literal. This is not a
    convenience - 004c's header reproduces design.md's trigger definition verbatim as
    prose, so any assertion that does not run through here would be satisfied by the
    header's description of the trigger instead of by the trigger itself.
    """
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _code() -> str:
    return _strip_comments(_sql())


def _collapse(text: str) -> str:
    """Collapse whitespace only. Schema qualification is preserved.

    Used wherever the schema matters - task 8.13 names ``public.strategy_versions``
    specifically, and a declaration attached to some other schema's same-named table is a
    different declaration.
    """
    return re.sub(r"\s+", " ", text).strip()


def _normalise(text: str) -> str:
    """Collapse whitespace and drop schema qualification.

    Comparison against design.md must survive two harmless differences: the migration
    schema-qualifies with ``public.`` and wraps statements over more lines. Nothing else -
    a changed identifier, keyword or predicate still fails.

    Prefer :func:`_collapse` when the assertion is about *where* something is attached;
    this one deliberately cannot tell ``public.strategy_versions`` from
    ``other.strategy_versions``.
    """
    return _collapse(text.replace("public.", ""))


def _balanced_group(text: str, open_index: int) -> str:
    """The contents of the parenthesised group opening at ``text[open_index]``."""
    assert text[open_index] == "(", "not a parenthesised group"
    depth = 0
    for offset in range(open_index, len(text)):
        if text[offset] == "(":
            depth += 1
        elif text[offset] == ")":
            depth -= 1
            if depth == 0:
                return text[open_index + 1 : offset]
    raise AssertionError("unbalanced parentheses")


def _check_predicate(name: str, sql: str) -> str:
    """The normalised CHECK expression attached under constraint ``name`` in ``sql``.

    Parenthesis-aware, because the predicate this file cares about contains a nested
    group: a non-greedy regex would stop at the inner ``)``.
    """
    match = re.search(rf"ADD CONSTRAINT\s+{name}\s+CHECK\s*\(", sql)
    assert match, f"no ADD CONSTRAINT ... CHECK for {name}"
    return _normalise(_balanced_group(sql, match.end() - 1))


def _function_body() -> str:
    """The ``$$ ... $$`` body of ``reject_immutable_version_update()``, comments stripped."""
    code = _code()
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION\s+public\.{FUNCTION}\s*\(\s*\)(.*?)\$\$\s*LANGUAGE",
        code,
        re.DOTALL,
    )
    assert match, f"no CREATE OR REPLACE FUNCTION public.{FUNCTION}() ... LANGUAGE"
    return match.group(1)


def _create_trigger_statement() -> str:
    """The ``CREATE TRIGGER trg_sv_immutable ...;`` statement, whitespace collapsed.

    Schema-qualification is kept, not normalised away: the target table is part of what
    task 8.13 asks to be asserted.
    """
    code = _code()
    match = re.search(rf"CREATE TRIGGER\s+{TRIGGER}\b(.*?);", code, re.DOTALL)
    assert match, f"no CREATE TRIGGER {TRIGGER} statement in the migration's code"
    return _collapse(match.group(0))


def _design_migration_section() -> str:
    """design.md's ``### Migration 004_strategy_builder_canonical.sql`` section 1 text."""
    design = DESIGN_PATH.read_text(encoding="utf-8")
    start = design.index("1. Canonical graph + plan on the immutable version")
    end = design.index("2. Training jobs", start)
    return design[start:end]


# ---------------------------------------------------------------------------
# The file exists and is readable
# ---------------------------------------------------------------------------


def test_the_migration_file_exists_where_the_split_says_it_does():
    assert MIGRATION_PATH.is_file(), (
        f"{MIGRATION_REL} is named as migration 004 part 3 but does not exist"
    )


def test_the_migration_is_ascii_and_carries_no_bom():
    """Matching parts 1, 2, 4 and 5: a BOM or a stray em-dash breaks tooling that reads
    these files without an explicit encoding."""
    raw = MIGRATION_PATH.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "the migration carries a UTF-8 BOM"
    raw.decode("ascii")  # raises if any byte is non-ASCII


class TestImmutabilityDeclaration:
    """Requirement 9.2, as far as this environment permits - and no further.

    **These tests do not prove that anything rejects anything.** There is no PostgreSQL
    here and ``004c_immutable_versions.sql`` is unapplied, so the only verifiable
    statement is that the migration *declares* ``chk_valid_requires_hash``, the
    ``reject_immutable_version_update()`` function and the ``trg_sv_immutable`` trigger,
    with the gate and the timing the design and Requirement 9.2 call for. Whether the
    constraint and the trigger are IN FORCE is 004c's own VERIFICATION section - groups 1,
    2 and 4 for presence, groups 3 and 5 for the negative INSERT and the negative UPDATE -
    run against a real database.

    Read every assertion below as "the file says so", never as "the database does so".
    """

    # -- chk_valid_requires_hash ------------------------------------------------

    def test_004c_declares_chk_valid_requires_hash(self):
        code = _code()
        assert f"ADD CONSTRAINT {CONSTRAINT}" in code, (
            f"{CONSTRAINT} is not declared in 004c's code (a mention in the header's "
            "prose does not count)"
        )
        assert (
            f"ALTER TABLE public.{TABLE} ADD CONSTRAINT {CONSTRAINT}" in _collapse(code)
        ), f"{CONSTRAINT} is not attached to public.{TABLE}"

    def test_the_constraint_predicate_is_the_designs_verbatim(self):
        """Requirement 9.3's half of SB-02: a row cannot claim ``VALID`` while carrying no
        identity hash or no compiled plan. Parsed out of design.md at test time so the two
        cannot drift apart silently."""
        migration_predicate = _check_predicate(CONSTRAINT, _code())
        design_predicate = _check_predicate(CONSTRAINT, _design_migration_section())
        assert migration_predicate == design_predicate, (
            f"004c's {CONSTRAINT} predicate drifted from design.md:\n"
            f"  migration: {migration_predicate}\n"
            f"  design:    {design_predicate}"
        )
        assert migration_predicate == (
            "validation_state <> 'VALID' "
            "OR (dag_hash IS NOT NULL AND compiled_plan IS NOT NULL)"
        )

    def test_the_constraint_is_added_under_a_pg_constraint_guard(self):
        """PostgreSQL has no ``ADD CONSTRAINT IF NOT EXISTS``, and DROP-then-ADD would both
        break this file's no-DROP rule and leave a window inside the transaction with the
        invariant unenforced. Migrations here are applied by hand and nothing records which
        files an environment has run, so a second run has to be a no-op."""
        blocks = [
            block
            for block in re.findall(r"DO \$\$(.*?)END \$\$;", _code(), re.DOTALL)
            if CONSTRAINT in block and "pg_constraint" in block
        ]
        assert len(blocks) == 1, f"expected exactly one guarded block for {CONSTRAINT}"
        block = blocks[0]
        assert "NOT EXISTS" in block
        assert f"ADD CONSTRAINT {CONSTRAINT}" in block
        assert re.search(rf"conname\s*=\s*'{CONSTRAINT}'", block), (
            "the guard queries a name other than the constraint it adds, so it is always "
            "true or always false and a re-run raises 42710"
        )
        assert f"'public.{TABLE}'::regclass" in block, (
            "the guard must be scoped to this table, or a same-named constraint on another "
            "table would suppress it"
        )

    # -- reject_immutable_version_update() -------------------------------------

    def test_004c_declares_the_rejection_function(self):
        code = _code()
        assert re.search(
            rf"CREATE OR REPLACE FUNCTION\s+public\.{FUNCTION}\s*\(\s*\)", code
        ), f"public.{FUNCTION}() is not declared in 004c's code"
        assert re.search(r"RETURNS TRIGGER", code), "the function is not a trigger function"
        assert re.search(r"\$\$\s*LANGUAGE plpgsql", code), (
            "the function body is not closed as plpgsql"
        )

    def test_the_function_is_gated_on_read_only_or_the_three_read_only_states(self):
        """The gate 004c deviates from the design on, deliberately.

        design.md's snippet gates on ``OLD.is_read_only`` alone. No code path in this
        repository ever sets that column TRUE, so that gate alone is a trigger that can
        never fire - an invariant in name only. 004c ORs in the lifecycle states
        Requirements 9.4 and 9.9 actually call read-only, which is strictly more
        restrictive, never less. Both halves are asserted: dropping ``is_read_only`` would
        silently un-protect any future code that does set it, and dropping the lifecycle
        arm would return the trigger to being a no-op.
        """
        body = _normalise(_function_body())
        assert "IF OLD.is_read_only" in body, (
            "the design's own gate was dropped rather than widened"
        )
        match = re.search(r"OR OLD\.lifecycle_state IN \(([^)]*)\)", body)
        assert match, (
            "the trigger does not gate on lifecycle_state, so it can never fire against "
            "any row this codebase produces"
        )
        states = tuple(re.findall(r"'([A-Z]+)'", match.group(1)))
        assert states == READ_ONLY_STATES, (
            f"the read-only lifecycle states drifted from Requirements 9.4/9.9: {states}"
        )

    def test_the_function_compares_all_four_immutable_columns(self):
        """Requirement 9.2 names four things: the graph, the Compiled_Plan, the
        Identity_Hash and the schema version. ``IS DISTINCT FROM`` rather than ``<>``
        matters - ``NULL <> NULL`` is NULL, which would admit an edit that nulls a column
        or fills one that was null."""
        body = _normalise(_function_body())
        for column in IMMUTABLE_COLUMNS:
            assert f"NEW.{column} IS DISTINCT FROM OLD.{column}" in body, (
                f"{column} is not compared, so an immutable version's {column} could be "
                "rewritten under a running deployment"
            )
        assert body.count("IS DISTINCT FROM") == len(IMMUTABLE_COLUMNS), (
            "the comparison set drifted from Requirement 9.2's four columns"
        )

    def test_the_function_rejects_rather_than_repairs(self):
        """A BEFORE trigger can rewrite ``NEW``. This one must not: silently discarding an
        edit would leave the caller believing it succeeded. It raises, or it returns ``NEW``
        untouched."""
        body = _function_body()
        assert "RAISE EXCEPTION" in body, "the trigger must abort, not warn"
        assert "RETURN NEW;" in body
        assert not re.search(r"NEW\.\w+\s*:=", body), (
            "the trigger assigns to NEW, so it can silently change a row instead of "
            "refusing the write"
        )

    # -- trg_sv_immutable ------------------------------------------------------

    def test_004c_declares_the_trigger_before_update_for_each_row(self):
        """Timing and level are the whole point. ``AFTER`` would let the write land before
        the exception rolls it back (correct but wasteful, and wrong if the trigger is ever
        made to warn), and ``FOR EACH STATEMENT`` has no ``OLD``/``NEW`` at all, so a
        statement-level version of this trigger would not compile - or worse, would compile
        and inspect nothing."""
        statement = _create_trigger_statement()
        assert f"BEFORE UPDATE ON public.{TABLE}" in statement, (
            f"{TRIGGER} is not BEFORE UPDATE on public.{TABLE}: {statement}"
        )
        assert "FOR EACH ROW" in statement, f"{TRIGGER} is not row-level: {statement}"
        assert f"EXECUTE FUNCTION public.{FUNCTION}()" in statement, (
            f"{TRIGGER} does not execute public.{FUNCTION}(): {statement}"
        )
        # Scoped to the versions table and to UPDATE only - an INSERT-time or DELETE-time
        # arm would reject legal writes.
        assert "BEFORE INSERT" not in statement
        assert "BEFORE DELETE" not in statement
        assert "OR UPDATE" not in statement

    def test_the_trigger_is_created_under_a_pg_trigger_guard(self):
        """PostgreSQL has no ``CREATE TRIGGER IF NOT EXISTS``, and this repository's
        baseline predates ``CREATE OR REPLACE TRIGGER`` (PG14), so a re-run of a hand-applied
        file would raise 42710 without the guard."""
        blocks = [
            block
            for block in re.findall(r"DO \$\$(.*?)END \$\$;", _code(), re.DOTALL)
            if TRIGGER in block and "pg_trigger" in block
        ]
        assert len(blocks) == 1, f"expected exactly one guarded block for {TRIGGER}"
        block = blocks[0]
        assert "NOT EXISTS" in block
        assert f"CREATE TRIGGER {TRIGGER}" in block
        assert re.search(rf"tgname\s*=\s*'{TRIGGER}'", block), (
            "the guard queries a name other than the trigger it creates, so it is always "
            "true or always false and a re-run raises 42710"
        )
        assert f"c.relname = '{TABLE}'" in block, (
            "the guard must be scoped to this table, or a same-named trigger elsewhere "
            "would suppress it"
        )
        assert "n.nspname = 'public'" in block

    # -- what this file cannot prove, kept honest ------------------------------

    def test_the_migration_carries_the_verification_queries_enforcement_needs(self):
        """The declaration is all this suite can check, so the file must carry the queries
        that check the rest.

        Asserted against the RAW text on purpose - unlike every other test here, the
        subject IS the comment block, because these queries are for an operator to run by
        hand after applying the file. Requiring the negative ``UPDATE`` specifically is the
        point: presence in ``pg_trigger`` shows the trigger exists, not that it refuses
        anything.
        """
        sql = _sql()
        assert "VERIFICATION (run after applying)" in sql
        assert "FROM pg_constraint" in sql, "no query checks the constraint is present"
        assert "FROM pg_trigger" in sql, "no query checks the trigger is present"

        verification = sql[sql.index("VERIFICATION (run after applying)") :]
        assert f"UPDATE public.{TABLE}" in verification, (
            "no negative UPDATE for an operator to run against a real database"
        )
        assert "SET dag_hash" in verification, (
            "the negative UPDATE must target a column the trigger names"
        )
        assert "expect this UPDATE to fail" in verification
        assert "expect this INSERT to fail" in verification, (
            "the constraint's own negative case is missing"
        )
        assert "must still be updatable" in verification, (
            "the paired positive case is missing: a column the trigger does not name has "
            "to remain writable, or the trigger is over-broad rather than correct"
        )

    def test_the_declaration_is_code_and_not_only_prose(self):
        """The guard on this whole file's method.

        004c's header quotes design.md's trigger verbatim, so all three names appear in the
        file even if every statement were deleted. This test fails if that ever becomes the
        only place they appear.
        """
        code = _code()
        for fragment in (
            f"ADD CONSTRAINT {CONSTRAINT}",
            f"FUNCTION public.{FUNCTION}",
            f"CREATE TRIGGER {TRIGGER}",
        ):
            assert fragment in code, (
                f"{fragment!r} survives only in 004c's comments, so the control is "
                "described but not declared"
            )
        assert code.count("BEGIN;") == 1 and code.count("COMMIT;") == 1, (
            "the declarations must land in one transaction"
        )
        assert code.index("BEGIN;") < code.index(f"ADD CONSTRAINT {CONSTRAINT}")
        assert code.index(f"CREATE TRIGGER {TRIGGER}") < code.index("COMMIT;")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
