"""
tests/test_signal_lifecycle_idempotency_migration.py

Static verification of migration 005 part B,
``backend_app/migrations/005b_signal_lifecycle_and_idempotency.sql``:

* **section 1** (task 3.1) adds ``public.signals.idempotency_key`` and
  ``public.signals.order_lifecycle_state`` with their CHECK constraint and their indexes
  (Requirements 16.1, 16.6, 19.1, 21.2, 21.3, 21.7);
* **section 2** (task 3.2) creates ``public.order_lifecycle_transitions`` - the append-only
  transition history, with both cascading foreign keys, its own copy of the 9-value CHECK,
  ``idx_olt_signal_time``, ``idx_olt_user``, and RLS with owner-scoped SELECT and INSERT
  policies ONLY (Requirements 16.7, 21.1, 21.3, 21.4);
* **section 3** (task 3.3) is the backfill of ``order_lifecycle_state`` from the legacy
  ``signals.status`` column - and is asserted here to be COMMENT ONLY: a pointer to
  ``scripts/forensics/backfill_signal_order_lifecycle_state.py`` plus the queries proving
  no row is left ``NULL``, with no ``UPDATE``, no legacy spelling and no transaction of its
  own, because ``design.md`` puts the mapping in application code so that
  ``SIGNALS_STATUS_MAP`` lives in exactly one place (Requirements 16.2, 16.3).

The two sections are asserted separately wherever "this must not appear anywhere" is the
assertion, because what section 1 must not contain and what section 2 must not contain are
different lists: section 1 extends a pre-existing table and so may not touch RLS, a policy
or a trigger at all, while section 2 creates its own table and MUST enable RLS and create
exactly two policies on it.

WHAT THESE TESTS CAN AND CANNOT ESTABLISH
-----------------------------------------
There is no PostgreSQL in this environment, so nothing here proves the migration APPLIES.
What it proves is what a static reader of the file would otherwise have to check by eye,
and what silently rots the moment someone edits one side of a duplicated fact:

* **The CHECK's vocabulary IS the module's vocabulary.** The nine values are extracted
  from the SQL and compared against ``ORDER_LIFECYCLE_STATE_VALUES`` in
  ``backend_app/backend/order_lifecycle_state.py`` - same values, same order. That tuple's
  own docstring names this migration as its consumer, so this test is the thing that keeps
  the two transcriptions from disagreeing (Requirement 16.1).
* **The DDL is what design.md specifies.** The design's own "Signals table extensions"
  block is parsed out of ``design.md`` at test time and every DDL line in it must be
  present, normalised for the schema qualification and line wrapping the migration adds.
* **Additive and re-runnable by construction.** Every column is ``ADD COLUMN IF NOT
  EXISTS``, every index is ``CREATE INDEX IF NOT EXISTS``, the constraint sits behind a
  ``pg_constraint`` guard, and the file contains no ``DROP``, ``UPDATE``, ``DELETE``,
  ``TRUNCATE``, ``ALTER COLUMN``, policy, RLS, trigger or privilege statement.
* **Both columns stay nullable and defaultless.** That is load-bearing rather than
  cosmetic: a ``CHECK`` evaluating to NULL PASSES, so this is what lets the migration
  apply to a table with existing history, and it is why the vocabulary is total over
  ``public.signals`` only once task 3.3's backfill has run (Requirement 27.4).
* **The idempotency index is UNIQUE and partial.** A non-unique index of the same name
  would satisfy a name check while enforcing nothing (Requirements 19.1, 21.2).
* **Section 1 touches only public.signals; section 2 touches only its own new table.** No
  statement in either section names a relation outside that section's remit, so no other
  table's controls can be weakened by this file (Requirement 21.4).
* **The transition log is append-only, and that is asserted rather than assumed.** Section 2
  creates a SELECT policy and an INSERT policy and nothing else, and its postflight refuses
  a table carrying any ``UPDATE``, ``DELETE`` or ``ALL`` policy - so a table this file finds
  rather than creates cannot silently be writable (Requirement 16.7).
* **The two CHECK vocabularies cannot disagree.** Section 2's ``to_state`` IN-list is
  compared byte for byte against section 1.3's, and both against the module's tuple.

What is NOT asserted here: that the file applies cleanly, that the preflight actually
refuses a table without RLS, or that a re-run is a no-op against a real catalogue. Those
are the migration's own VERIFICATION queries, to be run by the operator who applies it.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend_app.backend.order_lifecycle_state import (
    ORDER_LIFECYCLE_STATE_VALUES,
    SIGNALS_STATUS_MAP,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

MIGRATION_REL = "backend_app/migrations/005b_signal_lifecycle_and_idempotency.sql"
MIGRATION_PATH = REPO_ROOT / MIGRATION_REL

DESIGN_PATH = (
    REPO_ROOT / ".kiro" / "specs" / "trading-lifecycle-integration" / "design.md"
)

NEW_COLUMNS = ("idempotency_key", "order_lifecycle_state")
NEW_INDEXES = (
    "uq_signals_idempotency_key",
    "idx_signals_lifecycle_state",
    "idx_signals_strategy_version",
)

OLT_TABLE = "order_lifecycle_transitions"
OLT_COLUMNS = (
    "id",
    "signal_id",
    "user_id",
    "from_state",
    "to_state",
    "reason",
    "occurred_at",
)
OLT_INDEXES = ("idx_olt_signal_time", "idx_olt_user")
OLT_POLICIES = ("olt_owner_select", "olt_owner_insert")

# design.md names this constraint ``chk_olt_to_state``; the migration creates it as
# ``chk_order_lifecycle_transitions_to_state``, which is the name task 3.2 uses and the
# ``chk_<table>_<column>`` convention section 1.3's own constraint follows. The rename is
# documented in the migration's section 2 header ("ONE DELIBERATE NAMING DEVIATION") and is
# safe because no code names either spelling - the table is created there for the first
# time. It is mapped here so the design-fidelity comparison below still checks every other
# token of that line.
OLT_CHECK = "chk_order_lifecycle_transitions_to_state"
DESIGN_OLT_CHECK = "chk_olt_to_state"


def _sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _no_comments(text: str) -> str:
    return re.sub(r"--[^\n]*", "", text)


def _no_strings(text: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", text)


def _code() -> str:
    """The file with comments removed."""
    return _no_comments(_sql())


def _executable_code() -> str:
    """Comments AND string literals removed.

    A ``RAISE EXCEPTION`` message that talks ABOUT row level security is data, not a
    statement, so the "this token appears nowhere" tests must not see it.
    """
    return _no_strings(_code())


def _sections() -> tuple[str, str, str]:
    """The raw text of sections 1, 2 and 3, split on their own banners.

    Everything between section 1's ``COMMIT;`` and section 2's banner - the append-point
    block - is comment, so it lands in section 1's slice harmlessly. That block MENTIONS
    "SECTION 2 - task 3.2" and "SECTION 3 - task 3.3" in indented comment lines, which is
    why the banners are matched anchored at the start of a line with exactly one space.
    """
    sql = _sql()
    one = re.search(r"^-- SECTION 1 - task 3\.1:", sql, re.M)
    two = re.search(r"^-- SECTION 2 - task 3\.2:", sql, re.M)
    three = re.search(r"^-- SECTION 3 - task 3\.3:", sql, re.M)
    verification = re.search(r"^-- VERIFICATION \(", sql, re.M)
    assert one and two and three and verification, (
        "the section banners or the VERIFICATION banner are not where the file says"
    )
    assert one.start() < two.start() < three.start() < verification.start(), (
        "the three sections must sit in order, above the VERIFICATION block"
    )
    return (
        sql[one.start() : two.start()],
        sql[two.start() : three.start()],
        sql[three.start() : verification.start()],
    )


def _section_one() -> str:
    return _sections()[0]


def _section_two() -> str:
    return _sections()[1]


def _section_three() -> str:
    return _sections()[2]


def _normalise(text: str) -> str:
    """Collapse whitespace, drop ``public.`` qualification, close up ``foo (bar)``.

    Comparison against design.md must survive exactly three harmless differences, and no
    others: the migration schema-qualifies with ``public.``, wraps statements over more
    lines, and puts a space between a relation name and its column list. A changed
    identifier, keyword or predicate still fails.
    """
    collapsed = re.sub(r"\s+", " ", text.replace("public.", "")).strip()
    return re.sub(r"\s+\(", "(", collapsed)


def _design_section() -> str:
    """The SQL inside the design's "Signals table extensions" fenced block.

    Only the fenced SQL is returned: the surrounding prose is the design's explanation, not
    DDL, and comparing it against a migration would be comparing English to SQL.
    """
    design = DESIGN_PATH.read_text(encoding="utf-8")
    start = design.index("### Signals table extensions")
    end = design.index("### Idempotency key scheme", start)
    section = design[start:end]
    fence_open = section.index("```sql") + len("```sql")
    fence_close = section.index("```", fence_open)
    return section[fence_open:fence_close]


# ---------------------------------------------------------------------------
# The file itself
# ---------------------------------------------------------------------------


def test_the_migration_file_exists_where_the_split_says_it_does():
    assert MIGRATION_PATH.is_file(), (
        f"{MIGRATION_REL} is named as migration 005 part B but does not exist"
    )


def test_the_migration_is_ascii_and_carries_no_bom():
    """Matching the 004 parts: a BOM or a stray box-drawing character breaks tooling that
    reads these files without an explicit encoding."""
    raw = MIGRATION_PATH.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "the migration carries a UTF-8 BOM"
    raw.decode("ascii")  # raises if any byte is non-ASCII


def test_section_one_is_one_balanced_transaction():
    """Section 1 opens and closes its own transaction, so that tasks 3.2 and 3.3 can be
    appended below it without widening its commit boundary. A signals table carrying
    order_lifecycle_state without its CHECK must never be visible to a session."""
    code = _no_comments(_section_one())
    assert code.count("BEGIN;") == 1
    assert code.count("COMMIT;") == 1
    assert code.index("BEGIN;") < code.index("COMMIT;")
    assert code.count("DO $$") == code.count("END $$;") == 4


def test_section_two_is_its_own_balanced_transaction():
    """Task 3.2 is appended as a SECOND transaction rather than inside section 1's. Either
    the transition table, its CHECK, its indexes, RLS and both policies exist, or none of
    them do - a table holding trading history with RLS enabled but no policy (unreachable)
    or with rows but no RLS (cross-tenant readable) must never be observable."""
    code = _no_comments(_section_two())
    assert code.count("BEGIN;") == 1
    assert code.count("COMMIT;") == 1
    assert code.index("BEGIN;") < code.index("COMMIT;")
    assert code.count("DO $$") == code.count("END $$;")
    assert code.count("DO $$") >= 4, (
        "section 2's preflight, shape assertion, guards and postflight are all DO blocks"
    )


def test_the_file_is_exactly_two_transactions_in_order():
    """Three landed sections, but only TWO transactions - and that is the point.

    Sections 1 and 2 each execute DDL and so each own a transaction, section 1's closing
    before section 2's opens; nothing may reopen section 1's. Section 3 (task 3.3) adds a
    THIRD section and NO third transaction, because it executes no statement at all: the
    backfill is application code (design.md), so section 3 is a pointer to the script plus
    its verification queries, and a section that writes nothing has no commit boundary to
    open. A BEGIN appearing here would mean the mapping had been transcribed into SQL."""
    code = _no_comments(_sql())
    assert code.count("BEGIN;") == 2, (
        "expected exactly two transactions: sections 1 and 2. Section 3 is comment-only"
    )
    assert code.count("COMMIT;") == 2
    first_commit = code.index("COMMIT;")
    second_begin = code.index("BEGIN;", code.index("BEGIN;") + 1)
    assert first_commit < second_begin, (
        "section 2's BEGIN must come after section 1's COMMIT"
    )


def test_the_section_markers_are_intact_and_all_three_sections_have_landed():
    """The orchestrating plan appended the transition table (task 3.2) and the backfill
    pointer (task 3.3) to THIS file. The markers are what made that appendable without
    re-deriving where, and they are what a later reader navigates by."""
    sql = _sql()
    assert "END OF SECTION 1 (task 3.1)" in sql
    assert "SECTION 2 - task 3.2" in sql
    assert "END OF SECTION 2 (task 3.2)" in sql
    assert "SECTION 3 - task 3.3" in sql
    assert "END OF SECTION 3 (task 3.3)" in sql
    assert "MIGRATION 005 PART B IS COMPLETE" in sql, (
        "the append-point block promised sections 2 and 3; the file must say so once "
        "both have landed, so an operator knows nothing further is pending"
    )


# ---------------------------------------------------------------------------
# The vocabulary is not duplicated, it is transcribed
# ---------------------------------------------------------------------------


def test_the_check_vocabulary_is_exactly_the_modules_vocabulary():
    """Requirement 16.1. ``order_lifecycle_state.py`` says this constraint is written from
    ORDER_LIFECYCLE_STATE_VALUES; this is the test that keeps that true, in the same order,
    so a tenth value added to the enum cannot silently be missing from the database's own
    vocabulary."""
    match = re.search(
        r"ADD CONSTRAINT\s+chk_signals_order_lifecycle_state\s+CHECK\s*"
        r"\(order_lifecycle_state IN \((.*?)\)\s*\)\s*;",
        _sql(),
        re.S,
    )
    assert match, "chk_signals_order_lifecycle_state is absent or its shape changed"
    values = tuple(re.findall(r"'([A-Z_]+)'", match.group(1)))
    assert values == ORDER_LIFECYCLE_STATE_VALUES, (
        "the CHECK's IN-list has drifted from ORDER_LIFECYCLE_STATE_VALUES:\n"
        f"  sql:    {values}\n  module: {ORDER_LIFECYCLE_STATE_VALUES}"
    )


def test_the_constraint_is_added_behind_an_existence_guard():
    """PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS, so a bare second run would raise
    42710 and abort the whole file."""
    code = _code()
    assert "chk_signals_order_lifecycle_state" in code
    guard = re.search(
        r"IF NOT EXISTS \(\s*SELECT 1\s*FROM pg_constraint\s*"
        r"WHERE conname\s*=\s*'chk_signals_order_lifecycle_state'",
        code,
        re.S,
    )
    assert guard, "the CHECK is not behind a pg_constraint existence guard"


# ---------------------------------------------------------------------------
# The DDL is design.md's DDL
# ---------------------------------------------------------------------------


def test_every_design_ddl_line_for_the_signals_extension_is_present():
    """The design's own "Signals table extensions" block, line by line, parsed from
    design.md at test time so the two cannot drift apart silently. Only the lines belonging
    to this task are checked: the transition table below them is task 3.2."""
    section = _design_section()
    # Cut at the line that starts the transition table: that is task 3.2, appended later.
    lines = []
    for raw_line in section.splitlines():
        if "order_lifecycle_transitions" in raw_line:
            break
        lines.append(raw_line)

    migration = _normalise(_sql())

    missing = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("--"):
            continue
        needle = _normalise(line).rstrip(",;")
        if needle and needle not in migration:
            missing.append(line)

    assert not missing, "design.md DDL lines absent from the migration:\n  " + "\n  ".join(
        missing
    )


def test_both_columns_are_added_idempotently():
    code = _code()
    for column in NEW_COLUMNS:
        assert f"ADD COLUMN IF NOT EXISTS {column}" in re.sub(r" +", " ", code), column


def test_both_columns_are_nullable_and_have_no_default():
    """Requirement 27.4's additive-only, nullable-only constraint - and the reason the CHECK
    can be added to a table with existing history at all: every pre-existing row is NULL,
    and a CHECK evaluating to NULL passes. A DEFAULT here would fabricate a lifecycle state
    onto financial history; a NOT NULL would fail the ALTER outright."""
    statement = re.search(
        r"ALTER TABLE public\.signals\s+(.*?);", _executable_code(), re.S
    )
    assert statement, "the ADD COLUMN statement was not found"
    body = statement.group(1).upper()
    assert "NOT NULL" not in body, body
    assert "DEFAULT" not in body, body


def test_the_idempotency_index_is_unique_and_partial():
    """Requirements 19.1, 21.2. This index is the durable backstop behind the Redis lock: a
    non-unique or non-partial index of the same name would satisfy a name check while
    enforcing nothing."""
    match = re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS uq_signals_idempotency_key\s+"
        r"ON public\.signals \(idempotency_key\)\s+"
        r"WHERE idempotency_key IS NOT NULL;",
        _code(),
        re.S,
    )
    assert match, "uq_signals_idempotency_key is absent, not UNIQUE, or not partial"


def test_the_lifecycle_state_index_is_not_partial():
    """Requirement 21.3 plus the task 3.3 backfill's own query: "which rows are not yet
    reconciled" is a search for NULL, which a partial index excluding NULL would not
    serve."""
    match = re.search(
        r"CREATE INDEX IF NOT EXISTS idx_signals_lifecycle_state\s+"
        r"ON public\.signals \(order_lifecycle_state\);",
        _code(),
        re.S,
    )
    assert match, "idx_signals_lifecycle_state is absent or its definition changed"


def test_every_index_is_created_idempotently():
    code = _code()
    for index in NEW_INDEXES:
        assert f"INDEX IF NOT EXISTS {index}" in code, index


# ---------------------------------------------------------------------------
# Nothing is weakened
# ---------------------------------------------------------------------------


def test_the_migration_has_no_destructive_statement():
    """Additive only, across the whole file. Section 1 alters a PRE-EXISTING table holding
    financial history and section 2 creates a new one; neither may remove or rewrite
    anything. ``DELETE`` is checked as ``DELETE FROM`` here because section 2's foreign keys
    legitimately carry ``ON DELETE CASCADE``, which is a clause of a new constraint, not a
    deletion - section 2 has its own test pinning that down."""
    code = _executable_code().upper()
    for forbidden in (
        "DROP ",
        "TRUNCATE",
        "DELETE FROM",
        "UPDATE ",
        "ALTER COLUMN",
        "GRANT ",
        "REVOKE ",
    ):
        assert forbidden not in code, f"{forbidden} appears in the migration's code"


def test_section_one_contains_no_rls_policy_trigger_or_privilege_statement():
    """public.signals already has RLS enabled and three owner policies from 002/003, and
    trigger_update_signals_updated_at already maintains updated_at for writes to the new
    columns (a second trigger would double-fire). Requirement 21.4's control is reused, not
    rewritten.

    Scoped to section 1 deliberately: section 2 creates its OWN table and must enable RLS
    and create exactly two policies on it."""
    code = _no_strings(_no_comments(_section_one())).upper()
    for forbidden in (
        "CREATE POLICY",
        "ALTER POLICY",
        "DROP POLICY",
        "ROW LEVEL SECURITY",
        "CREATE TRIGGER",
        "CREATE OR REPLACE FUNCTION",
        "GRANT ",
        "REVOKE ",
    ):
        assert forbidden not in code, (
            f"{forbidden} appears in section 1's code; the existing signals RLS, "
            "trigger and privileges must be left untouched"
        )


def test_section_one_names_no_relation_other_than_public_signals():
    """What makes "no other table's controls can be weakened by section 1" checkable rather
    than promised."""
    named = set(
        re.findall(
            r"(?:ALTER TABLE|CREATE TABLE|CREATE INDEX|CREATE UNIQUE INDEX|"
            r"COMMENT ON COLUMN)\s+(?:IF NOT EXISTS\s+)?([a-z_.]+)",
            _no_strings(_no_comments(_section_one())),
        )
    )
    offenders = {
        name
        for name in named
        if not name.startswith("public.signals") and name not in NEW_INDEXES
    }
    assert not offenders, f"section 1 names relation(s) it must not touch: {offenders}"


def test_the_legacy_status_column_is_left_alone():
    """order_lifecycle_state is the canonical column Requirement 16.2 asks for; the legacy
    signals.status column has live writers, two indexes and existing rows, and is the
    backfill's own source. It must not be constrained, renamed or dropped here."""
    code = _executable_code()
    assert not re.search(r"\bstatus\b", code), (
        "the migration's executable code references the legacy status column"
    )


def test_the_file_refuses_to_run_without_row_level_security_on_signals():
    """Both new columns sit on rows recording real trading decisions, and a 23505 on the
    idempotency index would be a cross-tenant oracle on a table without row-level
    isolation. The preflight stops rather than proceeding - and does not enable RLS itself,
    which would be a change to an existing control."""
    sql = _sql()
    assert "relrowsecurity" in sql
    assert "refuses to run" in sql
    assert "003_signal_trace_restoration.sql" in sql, (
        "the refusal must name the migration that establishes the missing RLS"
    )


def test_the_backfill_mapping_is_not_restated_in_sql():
    """design.md is explicit that the backfill is application code so SIGNALS_STATUS_MAP
    lives in exactly one place. A legacy status spelling appearing in this file would mean
    the mapping had been duplicated into SQL."""
    code = _code()
    for legacy_spelling in ("'accepted'", "'expired'", "'pending'"):
        assert legacy_spelling not in code, (
            f"{legacy_spelling} appears in the migration; the legacy-status mapping "
            "belongs to order_lifecycle_state.SIGNALS_STATUS_MAP, not to SQL"
        )


# ---------------------------------------------------------------------------
# SECTION 2 (task 3.2) - public.order_lifecycle_transitions
#
# Requirement 16.7 asks for the transition history to be persisted with "the prior value,
# the new value, and the transition timestamp" and to be "retrievable in chronological order
# by timestamp"; Requirements 21.1, 21.3 and 21.4 ask for its foreign keys, its indexes and
# its row-level security. These tests pin each of those to the SQL, plus the one guarantee
# the design states in a comment and the migration turns into an assertion: the log is
# append-only.
# ---------------------------------------------------------------------------


def _design_olt_lines() -> list[str]:
    """The design's transition-table DDL: the fenced block from its CREATE TABLE onward."""
    lines: list[str] = []
    for raw_line in _design_section().splitlines():
        if not lines and f"CREATE TABLE IF NOT EXISTS {OLT_TABLE}" not in raw_line:
            continue
        lines.append(raw_line)
    assert lines, (
        "design.md no longer contains the order_lifecycle_transitions DDL this task "
        "transcribes"
    )
    return lines


def test_every_design_ddl_line_for_the_transition_table_is_present():
    """The design's own transition-table block, line by line, parsed from design.md at test
    time so the two cannot drift apart silently.

    Compared against section 2's CODE rather than its text, so a line that appears only in a
    comment cannot satisfy this. The one mapped identifier is the constraint name (see
    OLT_CHECK above); every other token, including both ``ON DELETE CASCADE`` clauses, the
    ``NOT NULL``s, the ``DEFAULT NOW()`` and both policy predicates, must match."""
    migration = _normalise(_no_comments(_section_two()))

    missing = []
    for raw_line in _design_olt_lines():
        line = raw_line.split("--")[0].strip()
        if not line:
            continue
        needle = _normalise(line.replace(DESIGN_OLT_CHECK, OLT_CHECK)).rstrip(",;")
        if needle and needle not in migration:
            missing.append(raw_line.strip())

    assert not missing, (
        "design.md transition-table DDL absent from section 2:\n  " + "\n  ".join(missing)
    )


def test_the_transition_table_is_created_idempotently_with_every_column():
    """CREATE TABLE IF NOT EXISTS, and all seven columns design.md lists. A column missing
    here is a column the trace surfaces (Requirement 17.6) would read as NULL."""
    code = _no_comments(_section_two())
    assert f"CREATE TABLE IF NOT EXISTS public.{OLT_TABLE}" in code, (
        "the transition table is not created, or not created idempotently"
    )
    body = re.search(
        rf"CREATE TABLE IF NOT EXISTS public\.{OLT_TABLE} \((.*?)\n\);", code, re.S
    )
    assert body, "the CREATE TABLE statement's shape changed"
    for column in OLT_COLUMNS:
        assert re.search(rf"^\s+{column}\s", body.group(1), re.M), column


def test_both_foreign_keys_are_not_null_and_cascade_from_their_owner():
    """Requirement 21.1: a transition row referencing no signal or no user is
    unrepresentable, and history for a deleted signal is not history but an orphan. The
    cascade is asserted in the DDL AND in the postflight, which checks confdeltype so a
    pre-existing table with a NO ACTION foreign key is caught rather than accepted."""
    code = _no_comments(_section_two())
    assert re.search(
        r"signal_id\s+UUID NOT NULL REFERENCES public\.signals\(id\) ON DELETE CASCADE",
        code,
    ), "signal_id is not a NOT NULL cascading foreign key to public.signals"
    assert re.search(
        r"user_id\s+UUID NOT NULL REFERENCES auth\.users\(id\) ON DELETE CASCADE",
        code,
    ), "user_id is not a NOT NULL cascading foreign key to auth.users"
    assert code.count("confdeltype = 'c'") == 2, (
        "the postflight must assert ON DELETE CASCADE on BOTH foreign keys"
    )


def test_to_state_is_not_null_and_from_state_is_nullable():
    """design.md's own annotation: from_state is "NULL for the first transition", because
    there is no prior value and inventing one would be a fabricated fact. to_state is the
    row's reason for existing and is NOT NULL, which is also what makes its CHECK total
    rather than NULL-permissive the way section 1.3's is."""
    code = _no_comments(_section_two())
    assert re.search(r"from_state\s+TEXT,", code), "from_state must be nullable TEXT"
    assert re.search(r"to_state\s+TEXT NOT NULL,", code), "to_state must be TEXT NOT NULL"
    assert re.search(r"occurred_at\s+TIMESTAMPTZ NOT NULL DEFAULT NOW\(\)", code), (
        "occurred_at must be NOT NULL DEFAULT NOW() - it is this table's creation "
        "timestamp (Requirement 21.5) and Requirement 16.7's ordering column"
    )


def test_the_transition_check_vocabulary_is_exactly_the_modules_vocabulary():
    """Requirement 16.1/16.7. Same assertion as for chk_signals_order_lifecycle_state, for
    the second transcription: a tenth value added to ORDER_LIFECYCLE_STATE_VALUES under
    Requirement 27.2 must not be able to land in one table's CHECK only."""
    match = re.search(
        rf"CONSTRAINT\s+{OLT_CHECK}\s+CHECK\s*\(to_state IN \((.*?)\)\s*\)",
        _no_comments(_section_two()),
        re.S,
    )
    assert match, f"{OLT_CHECK} is absent from the CREATE TABLE or its shape changed"
    values = tuple(re.findall(r"'([A-Z_]+)'", match.group(1)))
    assert values == ORDER_LIFECYCLE_STATE_VALUES, (
        "the transition CHECK's IN-list has drifted from ORDER_LIFECYCLE_STATE_VALUES:\n"
        f"  sql:    {values}\n  module: {ORDER_LIFECYCLE_STATE_VALUES}"
    )


def test_the_three_check_in_lists_are_identical():
    """The history and the current value must be drawn from the same vocabulary. There are
    three transcriptions of it in this file - section 1.3's guarded ADD CONSTRAINT, section
    2.1's inline CREATE TABLE constraint and section 2.3's guarded ADD CONSTRAINT - and
    every one is compared as TEXT, not as a parsed set, so a value added to, removed from or
    respelled in one of them fails here even though each list on its own would still be
    valid SQL."""
    bodies = re.findall(
        r"(?:order_lifecycle_state|to_state) IN \((.*?)\)",
        _no_comments(_sql()),
        re.S,
    )
    assert len(bodies) == 3, (
        "expected the 9-value IN-list exactly three times (section 1.3, section 2.1's "
        f"CREATE TABLE, section 2.3's ADD CONSTRAINT); found {len(bodies)}"
    )
    normalised = {re.sub(r"\s+", " ", body).strip() for body in bodies}
    assert len(normalised) == 1, (
        f"the IN-lists have drifted apart: {sorted(normalised)}"
    )
    values = tuple(re.findall(r"'([A-Z_]+)'", normalised.pop()))
    assert values == ORDER_LIFECYCLE_STATE_VALUES


def test_the_transition_check_is_also_added_behind_an_existence_guard():
    """The constraint is declared inline in the CREATE TABLE, which is a no-op on a
    pre-existing table. The guarded ADD CONSTRAINT is what gives that table the constraint
    instead of leaving the vocabulary unenforced - and being guarded is what keeps a second
    run from raising 42710."""
    code = _no_comments(_section_two())
    guard = re.search(
        rf"IF NOT EXISTS \(\s*SELECT 1\s*FROM pg_constraint\s*"
        rf"WHERE conname\s*=\s*'{OLT_CHECK}'",
        code,
        re.S,
    )
    assert guard, f"{OLT_CHECK} is not re-asserted behind a pg_constraint guard"
    assert f"ADD CONSTRAINT {OLT_CHECK}" in code


def test_both_transition_indexes_are_created_idempotently():
    """Requirement 21.3. idx_olt_signal_time's column ORDER is load-bearing: (signal_id,
    occurred_at) makes Requirement 16.7's "chronological order by timestamp" for one signal
    a single ordered index scan, which (occurred_at, signal_id) would not."""
    code = _no_comments(_section_two())
    for index in OLT_INDEXES:
        assert f"INDEX IF NOT EXISTS {index}" in code, index
    assert re.search(
        rf"CREATE INDEX IF NOT EXISTS idx_olt_signal_time\s+ON public\.{OLT_TABLE}"
        r"\(signal_id, occurred_at\);",
        code,
    ), "idx_olt_signal_time is absent or its column order changed"
    assert re.search(
        rf"CREATE INDEX IF NOT EXISTS idx_olt_user\s+ON public\.{OLT_TABLE}"
        r"\(user_id\);",
        code,
    ), "idx_olt_user is absent or its definition changed"


def test_section_two_enables_row_level_security_on_its_own_table():
    """Requirement 21.4 names this table explicitly ("where the transition-history table
    described in Requirement 16 Criterion 7 is implemented, that table"). Without RLS the
    two policies below are inert and one user's transition history is readable by another -
    which the postflight also asserts, so a table this file finds rather than creates cannot
    pass with RLS off."""
    code = _no_comments(_section_two())
    assert f"ALTER TABLE public.{OLT_TABLE} ENABLE ROW LEVEL SECURITY;" in code
    assert "relrowsecurity" in code, (
        "the postflight must verify RLS is actually on, not just that it was requested"
    )


def test_section_two_creates_owner_scoped_select_and_insert_policies_only():
    """Requirement 16.7's append-only log, matching signal_events in 002_signal_trace.sql.
    Two policies, both on auth.uid(), and no UPDATE, DELETE or ALL policy anywhere: a client
    role can read its own transitions and append to them, and can neither rewrite nor erase
    one."""
    code = _no_comments(_section_two())
    created = re.findall(r"CREATE POLICY (\w+) ON public\.(\w+)", code)
    assert [name for name, _ in created] == list(OLT_POLICIES), (
        f"expected exactly {OLT_POLICIES}, found {created}"
    )
    assert {table for _, table in created} == {OLT_TABLE}, (
        f"a policy is created on a table other than {OLT_TABLE}: {created}"
    )
    assert "FOR SELECT USING (user_id = auth.uid());" in code
    assert "FOR INSERT WITH CHECK (user_id = auth.uid());" in code

    upper = _no_strings(code).upper()
    for forbidden in ("FOR UPDATE", "FOR DELETE", "FOR ALL"):
        assert forbidden not in upper, (
            f"section 2 creates a {forbidden} policy; the transition log is append-only "
            "(Requirement 16.7)"
        )


def test_both_policies_are_created_behind_existence_guards():
    """PostgreSQL has no CREATE POLICY IF NOT EXISTS, so a bare second run would raise
    42710 and abort section 2. Same guard pattern as 003 section 9, 004b section 4 and
    004d section 3."""
    code = _no_comments(_section_two())
    for policy in OLT_POLICIES:
        guard = re.search(
            r"IF NOT EXISTS \(\s*SELECT 1 FROM pg_policies\s*"
            r"WHERE schemaname = 'public'\s*"
            rf"AND tablename\s*=\s*'{OLT_TABLE}'\s*"
            rf"AND policyname\s*=\s*'{policy}'",
            code,
            re.S,
        )
        assert guard, f"{policy} is not behind a pg_policies existence guard"


def test_the_postflight_refuses_a_writable_transition_log():
    """Not creating an UPDATE or DELETE policy does not prove none exists - this section is
    re-runnable and may find a table it did not create. The postflight asserts the absence,
    naming FOR ALL too, since one such policy covers both."""
    code = _no_comments(_section_two())
    assert "cmd IN ('UPDATE', 'DELETE', 'ALL')" in code, (
        "the postflight must query pg_policies for any policy permitting a rewrite or an "
        "erase, including a FOR ALL policy"
    )
    assert "append-only" in _section_two()


def test_the_postflight_asserts_both_owner_predicates():
    """A policy of the right name and command that scoped rows to something other than the
    caller would satisfy a name check while isolating nothing."""
    code = _no_comments(_section_two())
    assert code.count("position('auth.uid()' in") == 2, (
        "the postflight must check the owner predicate of BOTH policies"
    )
    assert "with_check" in code and "qual" in code


def test_section_two_names_no_relation_outside_its_table_and_its_fk_targets():
    """The counterpart of section 1's containment test. Section 2 may name its own table,
    and public.signals / auth.users as foreign-key targets and regclass casts - nothing
    else, so no pre-existing table's controls can be weakened here."""
    named = set(re.findall(r"\b(?:public|auth)\.[a-z_]+", _no_comments(_section_two())))
    allowed = {
        f"public.{OLT_TABLE}",
        "public.signals",
        "auth.users",
        "auth.uid",
    }
    offenders = named - allowed
    assert not offenders, f"section 2 names relation(s) it must not touch: {offenders}"


def test_section_two_does_not_alter_public_signals():
    """Section 2 adds history ALONGSIDE public.signals. It must not touch the table section
    1 extended: no column, no constraint, no index, no policy and no trigger on it."""
    code = _no_strings(_no_comments(_section_two())).upper()
    assert "ALTER TABLE PUBLIC.SIGNALS" not in code
    assert "CREATE TRIGGER" not in code
    assert "CREATE OR REPLACE FUNCTION" not in code


def test_delete_appears_in_section_two_only_as_on_delete_cascade():
    """The whole-file destructive-statement test allows ``DELETE`` because of the two
    cascading foreign keys. This is the test that keeps that allowance honest: every
    occurrence of the token in section 2's code is part of an ON DELETE CASCADE clause."""
    code = _no_strings(_no_comments(_section_two())).upper()
    assert code.count("DELETE") == code.count("ON DELETE CASCADE"), (
        "section 2's code mentions DELETE outside an ON DELETE CASCADE clause"
    )
    assert code.count("ON DELETE CASCADE") == 2, (
        "expected exactly the two cascading foreign keys"
    )


def test_section_two_preflight_asserts_both_foreign_key_targets():
    """A bare 42P01 from inside a CREATE TABLE names neither missing target. public.signals
    comes from 002/003; auth.users is Supabase's, asserted the same way 004d does."""
    section = _section_two()
    assert "to_regclass('public.signals')" in section
    assert "to_regclass('auth.users')" in section
    assert "002_signal_trace.sql" in section, (
        "the refusal must name the migration that creates the missing signals table"
    )


# ---------------------------------------------------------------------------
# SECTION 3 (task 3.3) - the backfill, which is DELIBERATELY NOT SQL
#
# Requirements 16.2 and 16.3 ask for every legacy ``signals.status`` value to be mapped into
# the 9-value vocabulary with no row left NULL. design.md places that work in application
# code - "one UPDATE per legacy value, not a server-side function ... so the mapping lives in
# exactly one place (order_lifecycle_state.py)" - so what these tests pin down is the
# ABSENCE of SQL here, the presence of a pointer to the script that does the work, and the
# presence of the query that proves the work finished. A future edit that "helpfully" writes
# the UPDATEs into the migration fails here, which is the point.
# ---------------------------------------------------------------------------

BACKFILL_SCRIPT_REL = "scripts/forensics/backfill_signal_order_lifecycle_state.py"

# The seven legacy spellings SIGNALS_STATUS_MAP holds, read from the map itself rather than
# listed here: listing them in this test would create the second copy the whole design is
# arranged to avoid.
LEGACY_STATUS_SPELLINGS = tuple(sorted(SIGNALS_STATUS_MAP))


def test_section_three_executes_nothing_at_all():
    """Every non-blank line of section 3 is a comment.

    Not a stylistic assertion. A single executable statement here would either be an UPDATE
    (the duplication design.md forbids) or DDL outside a transaction (section 1 and 2's
    guarantee is that each section's objects appear atomically). Comment-only is the design.
    """
    offenders = [
        line
        for line in _section_three().splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    ]
    assert not offenders, (
        "section 3 must be comment-only; found executable line(s):\n  "
        + "\n  ".join(offenders)
    )


def test_section_three_opens_no_transaction():
    """The counterpart of the two-transaction test, stated locally: a section that executes
    nothing has no commit boundary, so a BEGIN or COMMIT here would mean section 3 had grown
    statements it must not have."""
    code = _no_comments(_section_three())
    assert "BEGIN" not in code.upper()
    assert "COMMIT" not in code.upper()
    assert not code.strip(), "section 3 has non-comment content"


def test_section_three_names_the_script_that_performs_the_backfill():
    """A pointer is only a pointer if it names the thing. Requirement 16.2's work has to be
    findable from the migration an operator is holding, including how to invoke it."""
    section = _section_three()
    assert BACKFILL_SCRIPT_REL in section, (
        f"section 3 must name {BACKFILL_SCRIPT_REL}, the script that performs the backfill"
    )
    assert "--check" in section and "--apply" in section, (
        "section 3 must tell the operator how to invoke the script, the way the file's "
        "APPLICATION header does for the migration itself"
    )
    assert "order_lifecycle_state.py" in section, (
        "section 3 must name the module the mapping lives in, so a reader knows where to "
        "add a legacy spelling rather than editing SQL"
    )
    assert "SIGNALS_STATUS_MAP" in section


def test_the_backfill_script_exists_where_section_three_says_it_does():
    """A pointer to a file that is not there is worse than no pointer."""
    assert (REPO_ROOT / BACKFILL_SCRIPT_REL).is_file(), (
        f"section 3 names {BACKFILL_SCRIPT_REL} but that file does not exist"
    )


def test_section_three_restates_neither_the_legacy_vocabulary_nor_the_canonical_one():
    """The whole reason the backfill is application code.

    Checked against section 3's RAW TEXT, comments included - stricter than the whole-file
    test, which strips comments. A legacy spelling written even in a comment here is a
    second copy of SIGNALS_STATUS_MAP that nothing keeps in sync, and a canonical state
    named here would be the same duplication from the other end.
    """
    section = _section_three()
    for spelling in LEGACY_STATUS_SPELLINGS:
        assert f"'{spelling}'" not in section, (
            f"the legacy spelling '{spelling}' appears in section 3; the mapping belongs "
            "to order_lifecycle_state.SIGNALS_STATUS_MAP alone"
        )
    for state in ORDER_LIFECYCLE_STATE_VALUES:
        assert state not in section, (
            f"the canonical state {state} is named in section 3; section 3 must point at "
            "the script and prove completeness, not transcribe the vocabulary"
        )


def test_section_three_verifies_that_no_row_is_left_null():
    """Task 3.3's completion condition, as a query an operator can run.

    "Leave no row with a NULL order_lifecycle_state" is not provable by reading the script;
    it is provable by counting. Section 3 has to carry that count, and has to carry the
    follow-up that shows WHICH rows are left when it is not zero.
    """
    section = _section_three()
    assert re.search(
        r"count\(\*\) FILTER \(WHERE order_lifecycle_state IS NULL\)", section
    ), "section 3 must carry the count of unreconciled rows"
    assert "not_yet_reconciled" in section, (
        "the completion query must name what it is counting, the way section 1's "
        "VERIFICATION block already does"
    )
    assert re.search(
        r"WHERE order_lifecycle_state IS NULL\s*\n--\s*GROUP BY status", section
    ), (
        "section 3 must also show WHICH rows are left and what legacy value they carry - "
        "a bare zero/non-zero count does not tell an operator what to fix"
    )


def test_section_three_records_that_the_null_permissive_caveat_ends_with_the_backfill():
    """Section 1's header says NULL means "not yet reconciled" and that Requirement 16.1's
    "exactly 9 values" is total over public.signals only once this backfill has run. Section
    3 is where that caveat is discharged, and it must say so - otherwise the file's only
    statement about NULL is the one that admits it."""
    section = _section_three()
    assert "not yet reconciled" in section
    assert "NOT NULL" in section, (
        "section 3 must record that a NOT NULL constraint is still deliberately not added, "
        "and belongs in a NEW migration file rather than an edit to this one"
    )
